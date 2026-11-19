/* 影片筆記的畫面：全部經 SynoxApp（不自己連線、不輪詢；進度要看就按「重新整理」）。 */
(function () {
  "use strict";
  var K = window.SynoxApp;
  var $ = function (id) { return document.getElementById(id); };
  var current = "";

  function showNote(title, markdown) {
    current = markdown || "";
    $("noteTitle").textContent = "筆記：" + (title || "");
    $("note").textContent = current;
    $("noteBox").hidden = false;
    $("noteBox").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function copy() {
    if (!current) { return; }
    var done = function () { K.toast("複製好了，可以貼進 Notion"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(current).then(done, selectNote);
    } else {
      selectNote();
    }
  }

  function selectNote() {
    var range = document.createRange();
    range.selectNodeContents($("note"));
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    K.toast("已經選好全文，按 Ctrl+C 複製");
  }

  function progressList(items) {
    var ul = K.el("ul", "progress");
    (items || []).forEach(function (it) {
      var now = it.state === "running" ? it.stage + "中" : (it.state === "failed" ? it.stage + "失敗" : (it.stage ? it.stage + "完成" : "準備中"));
      ul.appendChild(K.el("li", "", it.label + "：" + now + "（" + Math.min(it.done, 5) + "/5）"));
    });
    return ul;
  }

  function jobRow(job) {
    var card = K.el("div", "card job state-" + job.state);
    var head = K.el("div", "row");
    head.appendChild(K.el("strong", "", "#" + job.id));
    head.appendChild(K.el("span", "badge", job.state_text));
    head.appendChild(K.el("span", "label", job.label));
    card.appendChild(head);
    if (job.progress && job.progress.length) { card.appendChild(progressList(job.progress)); }
    if (job.error && job.error.reason) {
      card.appendChild(K.el("p", "bad", "原因：" + job.error.reason));
      if (job.error.action) { card.appendChild(K.el("p", "muted", "怎麼辦：" + job.error.action)); }
    }
    var buttons = K.el("div", "row");
    if (job.state === "done") {
      var see = K.el("button", "btn", "看筆記");
      see.type = "button";
      see.addEventListener("click", function () { openJob(job.id); });
      buttons.appendChild(see);
    }
    if (job.state === "running" || job.state === "queued") {
      var stop = K.el("button", "btn danger", "停掉");
      stop.type = "button";
      stop.addEventListener("click", function () {
        K.act("cancel", { n: job.id }).then(function (d) { K.toast(d.message); loadJobs(); }, function () {});
      });
      buttons.appendChild(stop);
    }
    card.appendChild(buttons);
    return card;
  }

  function loadJobs() {
    return K.read("jobs").then(function (d) {
      var hint = $("hint");
      hint.hidden = !!d.ready;
      hint.textContent = d.ready ? "" : d.hint;
      var box = K.clear($("jobs"));
      if (!d.jobs.length) { box.appendChild(K.el("p", "muted", "還沒有工作。")); return; }
      d.jobs.forEach(function (job) { box.appendChild(jobRow(job)); });
    }, function () {});
  }

  function openJob(id) {
    K.read("job", { id: id }).then(function (d) {
      if (!d.notes.length) { K.toast("這個工作沒有筆記"); return; }
      var all = d.notes.map(function (n) { return d.notes.length > 1 ? "# " + n.title + "\n\n" + n.markdown : n.markdown; });
      showNote(d.notes[0].title + (d.notes.length > 1 ? " 等 " + d.notes.length + " 份" : ""), all.join("\n\n---\n\n"));
    }, function () {});
  }

  function loadLibrary() {
    var box = K.clear($("library"));
    box.appendChild(K.el("p", "muted", "讀取中…"));
    K.read("library", { keyword: $("keyword").value || "" }).then(function (d) {
      K.clear(box);
      if (!d.items.length) { box.appendChild(K.el("p", "muted", "筆記庫是空的（或沒有符合的）。")); return; }
      var table = K.el("table", "table");
      d.items.slice(0, 100).forEach(function (it) {
        var tr = K.el("tr");
        tr.appendChild(K.el("td", "num", String(it.n)));
        tr.appendChild(K.el("td", "", "[" + it.type + "] " + it.title));
        tr.appendChild(K.el("td", "muted", (it.notes || []).join("、") || "還沒有筆記"));
        var td = K.el("td");
        if ((it.notes || []).length) {
          var see = K.el("button", "btn ghost", "看筆記");
          see.type = "button";
          see.addEventListener("click", function () {
            K.read("note", { item: it.n }).then(function (n) { showNote(n.title, n.markdown); }, function () {});
          });
          td.appendChild(see);
        }
        tr.appendChild(td);
        table.appendChild(tr);
      });
      box.appendChild(table);
    }, function () { K.clear(box); });
  }

  K.ready(function () {
    $("addForm").appendChild(K.form("add", { submitText: "開始做筆記", onDone: function (d) {
      K.toast("工作 #" + d.job + " 排進去了，做完會通知你");
      loadJobs();
    } }));
    $("combineForm").appendChild(K.form("combine", { submitText: "合併", onDone: function (d) {
      K.toast("合併排進去了（工作 #" + d.job + "）");
      loadJobs();
    } }));
    $("reload").addEventListener("click", loadJobs);
    $("listLib").addEventListener("click", loadLibrary);
    $("copy").addEventListener("click", copy);
    loadJobs();
  });
}());
