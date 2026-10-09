(function () {
  "use strict";

  var catalogs = Object.create(null);
  var cacheMs = 300000;

  function typeForField(field) {
    return field === "lora_name" ? "lora" : (field === "unet_name" ? "diffusion" : "");
  }

  function escape(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function markup(field, value, attributes) {
    var attrs = Object.assign({}, attributes, { "class": ((attributes || {}).class || "") + " model-value" });
    var serialized = Object.keys(attrs).map(function (name) { return " " + name + '="' + escape(attrs[name]) + '"'; }).join("");
    return '<div class="model-select" data-model-type="' + typeForField(field) + '">' +
      '<select' + serialized + '><option value="' + escape(value) + '">' + escape(value || "请选择模型") + '</option></select>' +
      '<button class="secondary-button button-compact model-refresh" type="button" aria-label="刷新收藏模型" title="刷新此账号的收藏模型">刷新</button>' +
      '<button class="secondary-button button-compact model-manual" type="button" aria-label="手动输入模型名称">手动</button></div>';
  }

  function option(value, label, disabled) {
    var element = document.createElement("option");
    element.value = value;
    element.textContent = label;
    element.disabled = Boolean(disabled);
    return element;
  }

  function setValue(control, value) {
    value = String(value == null ? "" : value);
    if (control.tagName === "SELECT" && control.classList.contains("model-value") &&
        !Array.from(control.options).some(function (entry) { return entry.value === value; })) {
      control.appendChild(option(value, value || "请选择模型"));
    }
    control.value = value;
  }

  function populate(wrapper) {
    var control = wrapper.querySelector(".model-value");
    if (control.tagName !== "SELECT") return;
    var value = control.value;
    var choices = wrapper._modelChoices || [];
    var state = wrapper.dataset.modelState;
    var emptyLabel = state === "loading" ? "正在读取收藏模型…" :
      state === "error" ? "读取失败，可重试或手动输入" : choices.length ? "请选择模型" : "暂无收藏模型，可手动输入";
    control.textContent = "";
    control.appendChild(option("", emptyLabel, Boolean(value)));
    if (value && choices.indexOf(value) === -1) control.appendChild(option(value, value + "（当前值）"));
    choices.forEach(function (name) { control.appendChild(option(name, name)); });
    control.value = value;
  }

  function requestCatalog(accountId, refresh) {
    var existing = catalogs[accountId];
    if (!refresh && existing && (existing.pending || Date.now() - existing.at < cacheMs)) return existing.promise;
    var query = new URLSearchParams();
    if (accountId) query.set("account_id", accountId);
    if (refresh) query.set("refresh", "1");
    var entry = { at: 0, pending: true, promise: null };
    catalogs[accountId] = entry;
    entry.promise = fetch("/api/models/favorites?" + query.toString(), { headers: { "Accept": "application/json" } }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.message || "无法读取收藏模型");
        if (!data.models || !Array.isArray(data.models.lora) || !Array.isArray(data.models.diffusion)) throw new Error("模型列表格式无效");
        entry.pending = false;
        entry.at = Date.now();
        return data.models;
      });
    }).catch(function (error) {
      if (catalogs[accountId] === entry) delete catalogs[accountId];
      throw error;
    });
    return entry.promise;
  }

  function load(wrapper, refresh) {
    var accountId = String(wrapper._modelAccount() || "");
    var revision = (wrapper._modelRevision || 0) + 1;
    wrapper._modelRevision = revision;
    wrapper.dataset.modelAccount = accountId;
    wrapper.dataset.modelState = "loading";
    // A different account must never display the previous account's choices.
    wrapper._modelChoices = [];
    wrapper.setAttribute("aria-busy", "true");
    var button = wrapper.querySelector(".model-refresh");
    button.disabled = true;
    button.textContent = "读取中";
    populate(wrapper);
    requestCatalog(accountId, refresh).then(function (models) {
      if (!wrapper.isConnected || wrapper._modelRevision !== revision) return;
      wrapper._modelChoices = models[wrapper.dataset.modelType];
      wrapper.dataset.modelState = "ready";
      button.textContent = "刷新";
      button.title = "刷新此账号的收藏模型";
      populate(wrapper);
    }).catch(function (error) {
      if (!wrapper.isConnected || wrapper._modelRevision !== revision) return;
      wrapper.dataset.modelState = "error";
      button.textContent = "重试";
      button.title = error.message;
      populate(wrapper);
    }).finally(function () {
      if (wrapper._modelRevision !== revision) return;
      button.disabled = Boolean(wrapper.closest(".is-bypassed"));
      wrapper.setAttribute("aria-busy", "false");
    });
  }

  function toggleManual(wrapper) {
    if (wrapper.closest(".is-bypassed")) return;
    var previous = wrapper.querySelector(".model-value");
    var manual = previous.tagName === "SELECT";
    var replacement = document.createElement(manual ? "input" : "select");
    Array.from(previous.attributes).forEach(function (attr) { replacement.setAttribute(attr.name, attr.value); });
    if (manual) { replacement.type = "text"; replacement.autocomplete = "off"; }
    else { replacement.removeAttribute("type"); replacement.appendChild(option(previous.value, previous.value)); }
    replacement.value = previous.value;
    previous.replaceWith(replacement);
    var button = wrapper.querySelector(".model-manual");
    button.textContent = manual ? "列表" : "手动";
    button.setAttribute("aria-label", manual ? "返回收藏模型列表" : "手动输入模型名称");
    populate(wrapper);
    replacement.focus({ preventScroll: true });
  }

  function hydrate(root, accountResolver) {
    if (!root) return;
    root.querySelectorAll(".model-select").forEach(function (wrapper) {
      wrapper._modelAccount = accountResolver;
      if (!wrapper._modelBound) {
        wrapper._modelBound = true;
        wrapper.querySelector(".model-refresh").addEventListener("click", function () {
          if (!wrapper.closest(".is-bypassed")) load(wrapper, true);
        });
        wrapper.querySelector(".model-manual").addEventListener("click", function () { toggleManual(wrapper); });
      }
      if (!wrapper.dataset.modelState || wrapper.dataset.modelAccount !== String(accountResolver() || "")) load(wrapper, false);
    });
  }

  window.RHModelSelect = { typeForField: typeForField, markup: markup, hydrate: hydrate, setValue: setValue };
})();
