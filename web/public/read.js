// 已讀標記：只存在這台裝置的瀏覽器（localStorage），不會上傳到任何地方。
// 每則新聞的 key 是「刊別 ID:事件 ID」（事件 ID 只在同一刊內唯一，例如 evt_001）。
// 點卡片裡的來源連結會自動標為已讀；也可以按「標為已讀」手動切換。
(function () {
  var KEY = "read-v1";
  var HIDE_KEY = "hide-read";
  var KEEP_DAYS = 180; // 超過半年的已讀紀錄自動清掉，避免越存越多

  function today() {
    return Math.floor(Date.now() / 86400000);
  }
  function load() {
    try {
      var data = JSON.parse(localStorage.getItem(KEY) || "{}");
      return data && typeof data === "object" ? data : {};
    } catch (e) {
      return {};
    }
  }
  var store = load();
  function save() {
    try {
      localStorage.setItem(KEY, JSON.stringify(store));
    } catch (e) {}
  }
  (function prune() {
    var cutoff = today() - KEEP_DAYS,
      changed = false;
    for (var k in store) {
      if (store[k] < cutoff) {
        delete store[k];
        changed = true;
      }
    }
    if (changed) save();
  })();

  function readCount(editionId) {
    var prefix = editionId + ":",
      n = 0;
    for (var k in store) if (k.indexOf(prefix) === 0) n++;
    return n;
  }

  // ---- 存檔頁：每刊顯示已讀幾則 ----
  document.querySelectorAll(".archive-item[data-edition]").forEach(function (a) {
    var n = readCount(a.dataset.edition);
    var total = +a.dataset.total;
    var el = a.querySelector(".ai-read");
    if (!el || n === 0) return;
    el.hidden = false;
    if (n >= total) {
      el.textContent = "✓ 讀完";
      el.classList.add("done");
    } else {
      el.textContent = "已讀 " + n;
    }
  });

  // ---- 一刊的頁面 ----
  var mast = document.querySelector(".masthead[data-edition]");
  if (!mast) return;
  var edition = mast.dataset.edition;
  var cards = Array.prototype.slice.call(document.querySelectorAll(".card[data-eid]"));
  if (!cards.length) return;
  var ids = [];
  cards.forEach(function (c) {
    if (ids.indexOf(c.dataset.eid) < 0) ids.push(c.dataset.eid);
  });

  var bar = mast.querySelector(".read-bar");
  var progress = mast.querySelector(".read-progress");
  var hideBox = mast.querySelector("[data-hide-read]");
  var readAllBtn = mast.querySelector("[data-read-all]");
  var meter = mast.querySelector(".read-meter span");

  function key(eid) {
    return edition + ":" + eid;
  }
  function isRead(eid) {
    return Object.prototype.hasOwnProperty.call(store, key(eid));
  }
  // 「今日重點」和分類裡會出現同一則，兩張卡片要一起更新
  function paint(eid, fresh) {
    var read = isRead(eid);
    cards.forEach(function (c) {
      if (c.dataset.eid !== eid) return;
      c.classList.toggle("is-read", read);
      // 剛剛才讀的先不要藏起來，免得正在看的卡片突然消失；重新整理後才藏
      if (fresh) c.classList.toggle("just-read", read);
      var btn = c.querySelector(".read-btn");
      if (btn) {
        btn.setAttribute("aria-pressed", read ? "true" : "false");
        btn.title = read ? "改回未讀" : "標為已讀";
      }
    });
  }
  function updateProgress() {
    var n = ids.filter(isRead).length;
    var left = ids.length - n;
    progress.textContent = left === 0 ? "這一刊全部讀完了 ✓" : "已讀 " + n + "／" + ids.length;
    readAllBtn.hidden = left === 0;
    if (meter) meter.style.width = Math.round((n / ids.length) * 100) + "%";
  }
  function setRead(eid, read) {
    if (read) store[key(eid)] = today();
    else delete store[key(eid)];
    save();
    paint(eid, true);
    updateProgress();
  }

  ids.forEach(function (eid) {
    paint(eid, false);
  });
  bar.hidden = false;
  updateProgress();

  document.addEventListener("click", function (ev) {
    var card = ev.target.closest(".card[data-eid]");
    if (!card) return;
    var eid = card.dataset.eid;
    if (ev.target.closest(".read-btn")) {
      setRead(eid, !isRead(eid));
    } else if (ev.target.closest(".sources a[href]") && !isRead(eid)) {
      setRead(eid, true);
    }
  });
  // 用中鍵或「在新分頁開啟」點連結也算
  document.addEventListener("auxclick", function (ev) {
    var link = ev.target.closest(".card[data-eid] .sources a[href]");
    if (link) {
      var eid = link.closest(".card").dataset.eid;
      if (!isRead(eid)) setRead(eid, true);
    }
  });

  readAllBtn.addEventListener("click", function () {
    var t = today();
    ids.forEach(function (eid) {
      if (!isRead(eid)) store[key(eid)] = t;
    });
    save();
    ids.forEach(function (eid) {
      paint(eid, true);
    });
    updateProgress();
  });

  var hide = false;
  try {
    hide = localStorage.getItem(HIDE_KEY) === "1";
  } catch (e) {}
  function applyHide() {
    document.body.classList.toggle("hide-read", hide);
    // 切換時把「剛剛讀的」也一起藏
    cards.forEach(function (c) {
      c.classList.remove("just-read");
    });
  }
  hideBox.checked = hide;
  applyHide();
  hideBox.addEventListener("change", function () {
    hide = hideBox.checked;
    try {
      localStorage.setItem(HIDE_KEY, hide ? "1" : "0");
    } catch (e) {}
    applyHide();
  });
})();
