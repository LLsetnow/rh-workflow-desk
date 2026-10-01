const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const context = vm.createContext({ window: {} });
vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/pricing.js"), "utf8"), context);
const pricing = context.window.RHCost;

test("RH coin cost uses the discounted RMB unit price", () => {
  assert.equal(pricing.rhCoinPriceCny, 0.0016456);
  assert.equal(pricing.formatCny(pricing.cnyValue({ cost_type: "coins", cost: "100" })), "¥0.16456");
  assert.match(pricing.tooltip({ cost_type: "coins", cost: "100" }), /¥0\.0016456\/币/);
});

test("dollar balance cost is converted to RMB on hover", () => {
  const tooltip = pricing.tooltip({ cost_type: "money", cost: "0.12", key_site: "ai" });
  assert.equal(pricing.cnyValue({ cost_type: "money", cost: "0.12", key_site: "ai" }), 0.12 * 6.72245);
  assert.match(tooltip, /真实价格：¥0\.806694/);
  assert.match(tooltip, /¥6\.72245\/美元/);
});

test("RMB balance is not converted a second time", () => {
  const record = { cost_type: "money", cost: "1.25", key_site: "cn" };
  assert.equal(pricing.moneySymbol(record), "¥");
  assert.equal(pricing.cnyValue(record), 1.25);
  assert.equal(pricing.tooltip(record), "真实价格：¥1.25（人民币余额）");
});

test("dashboard summaries expose RMB conversion details", () => {
  assert.equal(pricing.coinsTooltip("100"), "真实价格：¥0.16456（100 RH 币 × ¥0.00374 × 0.44 = ¥0.0016456/币）");
  assert.equal(pricing.moneySummaryTooltip([
    { site: "ai", symbol: "$", value: "0.12" },
    { site: "cn", symbol: "¥", value: "1.25" },
  ]), "真实价格：$0.12 → ¥0.806694；¥1.25 → ¥1.25");
});
