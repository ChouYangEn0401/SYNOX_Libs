/* 記帳本（money 的完整畫面，ADR-0003）：只做「抓資料 → 畫出來 → 送動作」。
 * 讀、做動作、匯出一律經 SYNOX 的共用元件（SynoxApp.*）；加總與判斷都在伺服器，畫面不自己算錢。字一律 textContent，集中在 TEXT。 */
(function () {
  "use strict";
  var TEXT = {
    tabs: { overview: "總覽", entries: "帳目", categories: "類別", budget: "預算" },
    empty: "還沒有記帳。按「＋ 記一筆」，或在 Telegram 打 /money 120 午餐。",
    spent: "這個月花了", income: "收入", transfer: "轉帳（不算花費）", budget: "預算", left: "還剩", over: "超過",
    noBudget: "還沒設預算（到「預算」分頁設定）。",
    month: "月份", kind: "種類", category: "類別", keyword: "關鍵字", all: "全部",
    filter: "篩選", sums: "合計", more: "再多一些",
    edit: "改", rm: "刪", undo: "撤銷最後一筆", exportCsv: "匯出（照目前的篩選）",
    head: ["#", "日期", "種類", "類別", "金額", "備註", "用什麼付", ""],
    yuan: " 元",
    catEmpty: "你還沒有自己的類別（記帳時類別可以直接打字）。可以自己加，也可以載入範本。",
    catAdd: "新增類別（或幫已有的加關鍵字）", catRm: "刪類別", setCap: "設上限", cap: "上限",
    templates: "類別範本（不會自己載入）", load: "載入",
    budgetNow: "每月預算", budgetSet: "設定預算（0＝拿掉）",
    addTitle: "記一筆", editTitle: "改一筆", capTitle: "設上限（0＝拿掉）",
    kinds: { expense: "支出", income: "收入", transfer: "轉帳" },
    close: "關閉"
  };
  var ACTION_OF_KIND = { expense: "add", income: "in", transfer: "transfer" };
  var state = { tab: "overview", month: "", filters: {}, pane: null, cats: null };
  var el = SynoxApp.el;

  function yuan(n) { return SynoxApp.fmt.number(n) + TEXT.yuan; }

  function dialog(title, body) {
    var back = el("div", "synox-modal");
    var box = el("div", "card");
    box.appendChild(el("h3", "", title));
    box.appendChild(body);
    var close = el("button", "btn ghost", TEXT.close);
    close.type = "button";
    close.addEventListener("click", function () { back.parentNode.removeChild(back); });
    box.appendChild(close);
    back.appendChild(box);
    document.body.appendChild(back);
    return function () { if (back.parentNode) { back.parentNode.removeChild(back); } };
  }

  function formDialog(title, action, values) {
    var done = null;
    var f = SynoxApp.form(action, { values: values || {}, onDone: function () { if (done) { done(); } show(state.tab); } });
    done = dialog(title, f);
  }

  function addDialog() {
    var wrap = el("div");
    var pick = el("div", "tabs");
    var holder = el("div");
    Object.keys(ACTION_OF_KIND).forEach(function (kind) {
      var b = el("button", "btn", TEXT.kinds[kind]);
      b.type = "button";
      b.addEventListener("click", function () {
        SynoxApp.clear(holder);
        holder.appendChild(SynoxApp.form(ACTION_OF_KIND[kind], { onDone: function () { closeIt(); show(state.tab); } }));
      });
      pick.appendChild(b);
    });
    wrap.appendChild(pick);
    wrap.appendChild(holder);
    holder.appendChild(SynoxApp.form("add", { onDone: function () { closeIt(); show(state.tab); } }));
    var closeIt = dialog(TEXT.addTitle, wrap);
  }

  /* 總覽 ─────────────────────────────────────────────────────────── */
  function overview(pane) {
    SynoxApp.read("month", state.month ? { month: state.month } : {}).then(function (d) {
      SynoxApp.clear(pane);
      state.month = d.month;
      pane.appendChild(monthPicker(d.months, d.month, function () { show("overview"); }));
      var top = el("div", "card");
      var row = el("div", "money-row");
      row.appendChild(el("span", "", TEXT.spent));
      row.appendChild(el("span", "money-big", yuan(d.expense)));
      top.appendChild(row);
      if (d.budget) {
        var meter = el("div", "meter" + (d.left < 0 ? " over" : ""));
        var fill = el("span");
        fill.style.width = Math.min(100, Math.round(100 * d.expense / d.budget)) + "%";
        meter.appendChild(fill);
        top.appendChild(meter);
        top.appendChild(el("p", "money-note", TEXT.budget + " " + yuan(d.budget) + "，" +
                           (d.left >= 0 ? TEXT.left + " " + yuan(d.left) : TEXT.over + " " + yuan(-d.left))));
      } else {
        top.appendChild(el("p", "money-note", TEXT.noBudget));
      }
      top.appendChild(el("p", "", TEXT.income + " " + yuan(d.income) + "　" + TEXT.transfer + " " + yuan(d.transfer)));
      pane.appendChild(top);
      var bars = el("div", "card");
      var list = d.by_category.expense || [];
      if (!list.length) { bars.appendChild(el("p", "money-empty", TEXT.empty)); }
      var max = list.length ? list[0].amount : 1;
      list.forEach(function (c) {
        var line = el("div", "money-bar");
        line.appendChild(el("span", "", c.category));
        var meter = el("div", "meter");
        var fill = el("span");
        fill.style.width = Math.max(2, Math.round(100 * c.amount / max)) + "%";
        meter.appendChild(fill);
        line.appendChild(meter);
        line.appendChild(el("span", "num", yuan(c.amount)));
        bars.appendChild(line);
      });
      pane.appendChild(bars);
      (d.caps || []).forEach(function (c) {
        pane.appendChild(el("p", c.over ? "bad" : "money-note", c.category + " " + TEXT.cap + " " + yuan(c.cap) + "，" +
                            (c.over ? TEXT.over + " " + yuan(-c.left) : TEXT.left + " " + yuan(c.left))));
      });
    });
  }

  function monthPicker(months, current, onChange) {
    var label = el("label", "field");
    label.appendChild(el("span", "", TEXT.month));
    var sel = el("select");
    var list = (months || []).slice();
    if (current && list.indexOf(current) < 0) { list.unshift(current); }
    list.forEach(function (m) { var o = el("option", "", m); o.value = m; if (m === current) { o.selected = true; } sel.appendChild(o); });
    sel.addEventListener("change", function () { state.month = sel.value; onChange(); });
    label.appendChild(sel);
    return label;
  }

  /* 帳目 ─────────────────────────────────────────────────────────── */
  function entries(pane) {
    SynoxApp.clear(pane);
    var bar = el("div", "money-filters card");
    var inputs = {};
    [["month", TEXT.month, "2026-11"], ["kind", TEXT.kind, ""], ["category", TEXT.category, ""], ["keyword", TEXT.keyword, ""]]
      .forEach(function (spec) {
        var label = el("label", "field");
        label.appendChild(el("span", "", spec[1]));
        var input;
        if (spec[0] === "kind") {
          input = el("select");
          [["", TEXT.all]].concat(Object.keys(TEXT.kinds).map(function (k) { return [k, TEXT.kinds[k]]; })).forEach(function (o) {
            var opt = el("option", "", o[1]); opt.value = o[0]; input.appendChild(opt);
          });
        } else {
          input = el("input");
          input.placeholder = spec[2];
        }
        input.value = state.filters[spec[0]] || "";
        inputs[spec[0]] = input;
        label.appendChild(input);
        bar.appendChild(label);
      });
    var go = el("button", "btn", TEXT.filter);
    go.type = "button";
    go.addEventListener("click", function () {
      state.filters = {};
      Object.keys(inputs).forEach(function (k) { if (inputs[k].value) { state.filters[k] = inputs[k].value; } });
      entries(pane);
    });
    bar.appendChild(go);
    var undo = el("button", "btn ghost", TEXT.undo);
    undo.type = "button";
    undo.addEventListener("click", function () { SynoxApp.act("undo", {}).then(function () { entries(pane); }); });
    bar.appendChild(undo);
    var out = el("button", "btn ghost", TEXT.exportCsv);
    out.type = "button";
    out.addEventListener("click", function () { SynoxApp.download("entries_csv", state.filters); });
    bar.appendChild(out);
    pane.appendChild(bar);
    var box = el("div", "card tablewrap");
    pane.appendChild(box);
    SynoxApp.read("entries", state.filters).then(function (d) {
      if (!d.entries.length) { box.appendChild(el("p", "money-empty", TEXT.empty)); return; }
      var table = el("table", "table");
      var head = el("tr");
      TEXT.head.forEach(function (h) { head.appendChild(el("th", "", h)); });
      table.appendChild(head);
      d.entries.forEach(function (e) {
        var tr = el("tr");
        [e.id, e.date, TEXT.kinds[e.kind], e.category].forEach(function (v) { tr.appendChild(el("td", "", v)); });
        tr.appendChild(el("td", "num", SynoxApp.fmt.number(e.amount)));
        tr.appendChild(el("td", "", e.memo));
        tr.appendChild(el("td", "", e.account));
        var acts = el("td", "money-actions");
        var ed = el("button", "btn", TEXT.edit);
        ed.type = "button";
        ed.addEventListener("click", function () {
          formDialog(TEXT.editTitle, "edit", { n: String(e.id), amount: String(e.amount), category: e.category, memo: e.memo,
                                               date: e.date, account: e.account });
        });
        var rm = el("button", "btn danger", TEXT.rm);
        rm.type = "button";
        rm.addEventListener("click", function () { SynoxApp.act("rm", { n: String(e.id) }).then(function () { entries(pane); }); });
        acts.appendChild(ed);
        acts.appendChild(rm);
        tr.appendChild(acts);
        table.appendChild(tr);
      });
      box.appendChild(table);
      box.appendChild(el("p", "money-note", TEXT.sums + "：" + Object.keys(TEXT.kinds).map(function (k) {
        return TEXT.kinds[k] + " " + yuan(d.sums[k]);
      }).join("、")));
    });
  }

  /* 類別 ─────────────────────────────────────────────────────────── */
  function categories(pane) {
    SynoxApp.read("categories").then(function (d) {
      SynoxApp.clear(pane);
      var box = el("div", "card");
      if (!d.categories.length) { box.appendChild(el("p", "money-empty", TEXT.catEmpty)); }
      var groups = {};
      d.categories.forEach(function (c) { (groups[c.group] = groups[c.group] || []).push(c); });
      Object.keys(groups).forEach(function (g) {
        var sec = el("div", "money-group");
        sec.appendChild(el("h3", "", g));
        groups[g].forEach(function (c) {
          var row = el("div", "money-row");
          row.appendChild(el("span", "", c.name + "（" + d.kinds[c.kind] + "）"));
          if (c.keywords.length) { row.appendChild(el("span", "money-note", c.keywords.join("、"))); }
          if (c.cap) { row.appendChild(el("span", "badge write", TEXT.cap + " " + yuan(c.cap))); }
          var capBtn = el("button", "btn ghost", TEXT.setCap);
          capBtn.type = "button";
          capBtn.addEventListener("click", function () { formDialog(TEXT.capTitle, "cap", { category: c.name }); });
          var del = el("button", "btn ghost", TEXT.catRm);
          del.type = "button";
          del.addEventListener("click", function () {
            SynoxApp.act("cat_rm", { name: c.name }).then(function () { categories(pane); });
          });
          row.appendChild(capBtn);
          row.appendChild(del);
          sec.appendChild(row);
        });
        box.appendChild(sec);
      });
      pane.appendChild(box);
      var add = el("div", "card");
      add.appendChild(el("h3", "", TEXT.catAdd));
      add.appendChild(SynoxApp.form("cat_add", { onDone: function () { categories(pane); } }));
      pane.appendChild(add);
      var tpl = el("div", "card");
      tpl.appendChild(el("h3", "", TEXT.templates));
      d.templates.forEach(function (t) {
        var row = el("div", "money-row");
        row.appendChild(el("strong", "", t.title));
        row.appendChild(el("span", "money-note", t.about + "：" + t.items.join("、")));
        var b = el("button", "btn", TEXT.load);
        b.type = "button";
        b.addEventListener("click", function () {
          SynoxApp.act("template", { name: t.key }).then(function () { categories(pane); });
        });
        row.appendChild(b);
        tpl.appendChild(row);
      });
      pane.appendChild(tpl);
    });
  }

  /* 預算 ─────────────────────────────────────────────────────────── */
  function budget(pane) {
    SynoxApp.read("month").then(function (d) {
      SynoxApp.clear(pane);
      var box = el("div", "card");
      box.appendChild(el("p", "", TEXT.budgetNow + "：" + (d.budget ? yuan(d.budget) : TEXT.noBudget)));
      box.appendChild(el("p", "money-note", TEXT.spent + " " + yuan(d.expense)));
      box.appendChild(el("h3", "", TEXT.budgetSet));
      box.appendChild(SynoxApp.form("budget", { onDone: function () { budget(pane); } }));
      pane.appendChild(box);
    });
  }

  var VIEWS = { overview: overview, entries: entries, categories: categories, budget: budget };

  function show(tab) {
    state.tab = tab;
    Array.prototype.forEach.call(document.querySelectorAll("#tabs button"), function (b) {
      b.setAttribute("aria-selected", b.getAttribute("data-tab") === tab ? "true" : "false");
    });
    VIEWS[tab](state.pane);
  }

  SynoxApp.ready(function () {
    state.pane = document.getElementById("pane");
    var tabs = document.getElementById("tabs");
    Object.keys(TEXT.tabs).forEach(function (key) {
      var b = el("button", "", TEXT.tabs[key]);
      b.type = "button";
      b.setAttribute("data-tab", key);
      b.addEventListener("click", function () { show(key); });
      tabs.appendChild(b);
    });
    document.getElementById("add-one").addEventListener("click", addDialog);
    document.addEventListener("visibilitychange", function () { if (document.visibilityState === "visible") { show(state.tab); } });
    show("overview");
  });
})();
