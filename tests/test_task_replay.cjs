const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function replayRuntime(data) {
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  const startup = source.indexOf("  bindEvents();");
  assert.notEqual(startup, -1);
  const elements = new Map();
  const requests = [];
  const toasts = [];
  const context = vm.createContext({
    replayData: data,
    captureRequest(url, method, body) {
      requests.push({ url, method, body: JSON.parse(JSON.stringify(body)) });
      return Promise.resolve({ task: { id: "new-task" } });
    },
    captureToast(message, isError) { toasts.push({ message, isError }); },
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, {
          value: id === "instanceType" ? "default" : "",
          disabled: false,
          classList: { add() {}, remove() {} },
          setAttribute() {}, removeAttribute() {}, querySelector() { return null; },
        });
        return elements.get(id);
      },
    },
  });
  vm.runInContext(source.slice(0, startup) + `
    var restoredValues;
    request = function (url) {
      if (url !== "/api/tasks/" + replayData.task.id + "/load") throw new Error("Unexpected request: " + url);
      return Promise.resolve(replayData);
    };
    jsonRequest = captureRequest;
    showToast = captureToast;
    renderAnalysis = function () {};
    restoreInputValues = function (values) { restoredValues = values; };
    collectInputs = function () { return restoredValues; };
    queuePromptGroupSnapshot = function () {};
    applyPendingPrompt = function () {};
    setWorkflowLibraryActionsVisible = function () {};
    setAnalysisStatus = function () {};
    setRemoteWorkflowId = function (value) { $("remoteWorkflowId").value = value; };
    saveDraftNow = function () {};
    notifyPromptWorkbench = function () {};
    selectedTaskProject = function () { return {}; };
    refresh = function () { return Promise.resolve(); };
    jumpToProcessStep = function () {};
    animateTaskInsertion = function () {};
    globalThis.api = { loadTask, submitTask, appState };
  })();`, context);
  return { api: context.api, requests, toasts, elements };
}

function qwenReplay(withImage = true) {
  const workflow = {
    "485": { class_type: "TextEncodeQwenImage21", inputs: { prompt: "saved prompt" } },
    "7": { class_type: "RandomNoise", inputs: { noise_seed: 42, mode: "fixed" } },
  };
  const items = [{ id: "485:prompt", node_id: "485", field: "prompt", kind: "prompt" }];
  const files = {};
  if (withImage) {
    workflow["523"] = { class_type: "LoadImage", inputs: { image: "/tmp/reference.png" } };
    workflow["485"].inputs["images.image_1"] = ["523", 0];
    items.push({ id: "523:image", node_id: "523", field: "image", kind: "file", required: true });
    files["523:image"] = "/tmp/reference.png";
  }
  return {
    workflow_id: "wf_library",
    workflow,
    input_config: { mode: "manual", items },
    analysis: {},
    task: {
      id: "task_history", workflow_name: "qwen.json", remote_workflow_id: "123456",
      files, prompts: { "485:prompt": "saved prompt" },
      random_noise: { "7": { seed: "42", mode: "fixed" } }, bypassed_nodes: [],
    },
  };
}

async function submitReplay(data) {
  const runtime = replayRuntime(data);
  await runtime.api.loadTask(data.task);
  assert.equal(runtime.api.appState.workflowDirty, false);
  runtime.api.submitTask();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(runtime.toasts.filter(item => item.isError), []);
  assert.equal(runtime.requests.length, 1);
  assert.equal(runtime.requests[0].url, "/api/tasks");
  assert.equal(runtime.requests[0].method, "POST");
  assert.equal(runtime.elements.get("submitButton").disabled, false);
  return runtime.requests[0].body;
}

test("reloaded Qwen history submits added media and links without further edits", async () => {
  const data = qwenReplay();
  const original = JSON.stringify(data.workflow);
  const body = await submitReplay(data);
  assert.ok(body.workflow, "submission must include the loaded task snapshot");
  assert.deepEqual(body.workflow, data.workflow);
  assert.deepEqual(body.workflow["485"].inputs["images.image_1"], ["523", 0]);
  assert.deepEqual(body.files, data.task.files);
  assert.deepEqual(body.prompts, data.task.prompts);
  assert.deepEqual(body.workflow_input_config, data.input_config);
  assert.equal(JSON.stringify(data.workflow), original);
});

test("reloaded history keeps removed media absent instead of restoring library nodes", async () => {
  const data = qwenReplay(false);
  const body = await submitReplay(data);
  assert.ok(body.workflow, "submission must include the loaded task snapshot");
  assert.deepEqual(body.workflow, data.workflow);
  assert.equal(body.workflow["523"], undefined);
  assert.equal(body.workflow["485"].inputs["images.image_1"], undefined);
});
