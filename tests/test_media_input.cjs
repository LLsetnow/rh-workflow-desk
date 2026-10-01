const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadMediaInputRuntime() {
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  const startup = source.indexOf("  bindEvents();");
  assert.notEqual(startup, -1);
  const document = {
    querySelectorAll() { return []; },
    querySelector() { return null; },
  };
  const window = {
    setTimeout() {},
    clearTimeout() {},
    confirm() { return true; },
    localStorage: { getItem() { return null; }, setItem() {} },
  };
  const context = vm.createContext({
    CSS: { escape(value) { return String(value); } },
    document,
    window,
  });
  vm.runInContext(source.slice(0, startup) + `
    globalThis.api = { disconnectMediaInputConnections, removeWorkflowNodeLinks };
  })();`, context);
  return context.api;
}

test("media deletion disconnects every direct consumer, including non-H3 nodes", () => {
  const api = loadMediaInputRuntime();
  const workflow = {
    "1": { class_type: "LoadImage", inputs: { image: "" } },
    "2": { class_type: "ImageScale", inputs: { image: ["1", 0], width: 512 } },
    "3": { class_type: "ImageBatch", inputs: { images: [["1", 0], ["9", 0]] } },
    "4": { class_type: "MiniMaxH3AudioConditioningT8", inputs: { "ref_images.ref_image_0": ["1", 0] } },
    "__rh_meta__": { workflowId: "123" },
  };

  const disconnected = api.disconnectMediaInputConnections(workflow, "1");

  assert.equal(JSON.stringify(disconnected), JSON.stringify([
    { node_id: "2", field: "image" },
    { node_id: "3", field: "images" },
    { node_id: "4", field: "ref_images.ref_image_0" },
  ]));
  assert.equal(JSON.stringify(workflow["2"].inputs), JSON.stringify({ width: 512 }));
  assert.equal(JSON.stringify(workflow["3"].inputs), JSON.stringify({ images: [["9", 0]] }));
  assert.equal(JSON.stringify(workflow["4"].inputs), JSON.stringify({}));
  assert.equal(JSON.stringify(workflow.__rh_meta__), JSON.stringify({ workflowId: "123" }));
});

test("media link cleanup preserves unrelated nested links", () => {
  const api = loadMediaInputRuntime();
  const result = api.removeWorkflowNodeLinks([["1", 0], ["2", 0]], "1");

  assert.equal(JSON.stringify(result), JSON.stringify({ removed: true, value: [["2", 0]] }));
});
