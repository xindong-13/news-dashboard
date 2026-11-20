// 搜尋頁：依月份載入 search/YYYY-MM.json，在瀏覽器裡比對標題與摘要。
(function () {
  var cfg = document.getElementById("search-config");
  var months = JSON.parse(cfg.dataset.months);
  var catLabels = JSON.parse(cfg.dataset.cats);
  var cache = {};
  var input = document.getElementById("q");
  var results = document.getElementById("results");
  var statusEl = document.getElementById("status");
  var MAX = 100;
  var seq = 0;

  function load(month) {
    if (!cache[month]) {
      cache[month] = fetch("search/" + month + ".json")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return [];
        });
    }
    return cache[month];
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function highlight(text, terms) {
    var out = esc(text);
    terms.forEach(function (t) {
      var re = new RegExp(esc(t).replace(/[.*+?^$(){}|[\]\\]/g, "\\$&"), "gi");
      out = out.replace(re, function (m) {
        return "<mark>" + m + "</mark>";
      });
    });
    return out;
  }
  function fmt(iso) {
    return new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
  }
  function card(r, terms) {
    return (
      '<article class="card" data-imp="' + r.imp + '">' +
      "<h3>" + highlight(r.t, terms) + "</h3>" +
      (r.s ? '<p class="summary">' + highlight(r.s, terms) + "</p>" : "") +
      '<div class="meta"><span class="cat-label">' + esc(catLabels[r.cat] || r.cat) + "</span>" +
      "<span>" + fmt(r.at) + "</span>" +
      '<a href="' + esc(r.ed) + ".html#" + esc(r.cat) + '">看那一刊</a></div>' +
      (r.url ? '<div class="sources">來源：<a href="' + esc(r.url) + '" target="_blank" rel="noopener noreferrer">' + esc(r.src || "原文") + "</a>" + (r.n > 1 ? "（共 " + r.n + " 個來源）" : "") + "</div>" : "") +
      "</article>"
    );
  }
  async function run() {
    var my = ++seq;
    var q = input.value.trim().toLowerCase();
    if (!q) {
      results.innerHTML = "";
      statusEl.textContent = "";
      return;
    }
    var terms = q.split(/\s+/);
    var found = [];
    var seen = {};
    statusEl.textContent = "搜尋中…";
    for (var i = 0; i < months.length && found.length < MAX; i++) {
      var rows = await load(months[i]);
      if (my !== seq) return;
      for (var j = 0; j < rows.length && found.length < MAX; j++) {
        var r = rows[j];
        var hay = (r.t + " " + (r.o || "") + " " + (r.s || "")).toLowerCase();
        if (!terms.every(function (t) { return hay.indexOf(t) >= 0; })) continue;
        var key = r.url || r.t; // 同一則新聞可能出現在午報和晚報的不同事件裡，只列一次
        if (seen[key]) continue;
        seen[key] = 1;
        found.push(r);
      }
    }
    found.sort(function (a, b) { return b.at.localeCompare(a.at); });
    results.innerHTML = found.map(function (r) { return card(r, terms); }).join("");
    statusEl.textContent = found.length === 0 ? "找不到符合的新聞" : found.length >= MAX ? "顯示最新的 " + MAX + " 則，請加關鍵字縮小範圍" : "找到 " + found.length + " 則";
  }
  var timer;
  input.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(run, 200);
  });
  document.getElementById("search-form").addEventListener("submit", function (e) {
    e.preventDefault();
    run();
  });
  input.focus();
})();
