(function () {
  "use strict";

  var RH_COIN_BASE_PRICE_CNY = 0.00374;
  var RH_COIN_MULTIPLIER = 0.44;
  var RH_COIN_PRICE_CNY = Math.round(RH_COIN_BASE_PRICE_CNY * RH_COIN_MULTIPLIER * 10000000) / 10000000;
  var USD_TO_CNY = 6.72245;

  function amount(record) {
    if (!record || record.cost == null) return null;
    var value = Number(String(record.cost).trim());
    return Number.isFinite(value) && value >= 0 ? value : null;
  }

  function moneySymbol(record) {
    return String(record && (record.key_site || record.site) || "").toLowerCase() === "cn" ? "¥" : "$";
  }

  function cnyValue(record) {
    var value = amount(record);
    if (value == null) return null;
    if (record.cost_type === "coins") return value * RH_COIN_PRICE_CNY;
    if (record.cost_type === "money") return moneySymbol(record) === "¥" ? value : value * USD_TO_CNY;
    return null;
  }

  function formatCny(value, digits) {
    var places = digits == null ? 6 : digits;
    var text = Number(value).toFixed(places).replace(/\.?0+$/, "");
    return "¥" + (text || "0");
  }

  function tooltip(record) {
    var value = amount(record);
    var cny = cnyValue(record);
    if (value == null || cny == null) return "";
    if (record.cost_type === "coins") {
      return "真实价格：" + formatCny(cny) + "（" + String(record.cost).trim() + " RH 币 × ¥" + RH_COIN_BASE_PRICE_CNY + " × " + RH_COIN_MULTIPLIER + " = ¥" + RH_COIN_PRICE_CNY + "/币）";
    }
    if (moneySymbol(record) === "¥") return "真实价格：" + formatCny(cny) + "（人民币余额）";
    return "真实价格：" + formatCny(cny) + "（$" + String(record.cost).trim() + " × ¥" + USD_TO_CNY + "/美元）";
  }

  function coinsTooltip(value) {
    return tooltip({ cost_type: "coins", cost: value });
  }

  function moneySummaryTooltip(items) {
    if (!Array.isArray(items)) return "";
    var parts = items.map(function (item) {
      var value = amount({ cost: item && item.value });
      var site = String(item && item.site || "").toLowerCase();
      var symbol = moneySymbol({ key_site: site });
      if (value == null) return "";
      var converted = cnyValue({ cost_type: "money", cost: value, key_site: site });
      if (converted == null) return "";
      return symbol + String(item && item.value).trim() + " → " + formatCny(converted);
    }).filter(Boolean);
    return parts.length ? "真实价格：" + parts.join("；") : "";
  }

  window.RHCost = {
    rhCoinBasePriceCny: RH_COIN_BASE_PRICE_CNY,
    rhCoinMultiplier: RH_COIN_MULTIPLIER,
    rhCoinPriceCny: RH_COIN_PRICE_CNY,
    usdToCny: USD_TO_CNY,
    cnyValue: cnyValue,
    coinsTooltip: coinsTooltip,
    formatCny: formatCny,
    moneySummaryTooltip: moneySummaryTooltip,
    moneySymbol: moneySymbol,
    tooltip: tooltip,
  };
}());
