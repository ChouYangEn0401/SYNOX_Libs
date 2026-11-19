/* 🔑 USB 鑰匙的畫面。全部經 SynoxApp（讀＝SynoxApp.read，做事＝SynoxApp.act；危險的動作 SYNOX 會先跳確認框）。
   密碼只在這個畫面輸入：送出之後馬上清掉欄位，不存在任何地方。解開的訊息只顯示在這裡，不送回聊天、不給 AI。 */
(function () {
  "use strict";
  var S = window.SynoxApp;
  var $ = function (id) { return document.getElementById(id); };
  var state = { keys: [], folders: [], folder: 0, sub: "", picked: "" };

  function el(tag, cls, text) { return S.el(tag, cls, text); }

  function button(text, cls, fn) {
    var b = el("button", "btn " + (cls || ""), text);
    b.type = "button";
    b.addEventListener("click", fn);
    return b;
  }

  function busy(btn, promise) {
    if (btn) { btn.disabled = true; }
    return promise.then(function (v) { if (btn) { btn.disabled = false; } return v; },
                        function (e) { if (btn) { btn.disabled = false; } throw e; });
  }

  function protectText(p) {
    return p === "remember" ? "💻 記住在這台" : p === "password" ? "🔒 要密碼" : p === "none" ? "⚠️ 沒保護" : "保護方式不明";
  }

  /* 鑰匙 ─────────────────────────────────────────────────────────── */

  function fillKeySelects() {
    ["msgKey", "fileKey"].forEach(function (id) {
      var sel = S.clear($(id));
      var none = el("option", "", id === "fileKey" ? "（解開時可以不選）" : "（選一把）");
      none.value = "";
      sel.appendChild(none);
      state.keys.forEach(function (k) {
        var o = el("option", "", (k.present ? "🟢 " : "⚪ ") + k.label + "（" + protectText(k.protect) + "）");
        o.value = k.label;
        sel.appendChild(o);
      });
    });
  }

  function drawKeys() {
    var box = S.clear($("keys"));
    if (!state.keys.length) {
      box.appendChild(el("p", "muted", "還沒有登記任何鑰匙。插上隨身碟，按「列出插著的隨身碟」來登記。"));
      return;
    }
    var list = el("ul", "list");
    state.keys.forEach(function (k) {
      var li = el("li", "item");
      li.appendChild(el("span", "dot", k.present ? "🟢" : "⚪"));
      var main = el("div", "grow");
      main.appendChild(el("b", "", k.label));
      main.appendChild(el("div", "small muted", k.product + " · " + protectText(k.protect) + " · 登記 " + k.registered +
                                               " · id " + k.short));
      li.appendChild(main);
      li.appendChild(button("撤銷…", "danger ghost", function () { revokeFlow(k); }));
      list.appendChild(li);
    });
    box.appendChild(list);
  }

  function loadKeys(btn) {
    return busy(btn, S.read("keys")).then(function (d) {
      var warn = $("notReady");
      warn.hidden = !!d.ready;
      warn.textContent = d.ready ? "" : d.message;
      state.keys = d.keys || [];
      drawKeys();
      fillKeySelects();
    });
  }

  function revokeFlow(k) {
    var box = el("div", "card warn");
    box.appendChild(el("p", "", "⛔ 撤銷「" + k.label + "」之後，用它鎖過的訊息和檔案永遠打不開，沒有辦法救回來。" +
                                 "隨身碟本身不會被清掉。"));
    var input = el("input");
    input.type = "text";
    input.setAttribute("aria-label", "再打一次鑰匙的名字");
    input.placeholder = "再打一次鑰匙的名字：" + k.label;
    box.appendChild(input);
    var row = el("div", "row");
    row.appendChild(button("撤銷", "danger", function (e) {
      busy(e.target, S.act("revoke", { key: k.label, confirm: input.value })).then(function () {
        S.toast("撤銷了「" + k.label + "」");
        loadKeys();
      }, function () {});
    }));
    row.appendChild(button("算了", "ghost", function () { loadKeys(); }));
    box.appendChild(row);
    var keys = S.clear($("keys"));
    keys.appendChild(box);
  }

  /* 登記 ─────────────────────────────────────────────────────────── */

  function loadDrives(btn) {
    var box = S.clear($("drives"));
    box.appendChild(el("p", "muted", "讀隨身碟中…"));
    return busy(btn, S.read("drives")).then(function (d) {
      S.clear(box);
      if (!d.ready) { box.appendChild(el("p", "warn", d.message)); return; }
      if (!d.drives.length) { box.appendChild(el("p", "muted", "沒有插著的隨身碟。")); return; }
      var list = el("ul", "list");
      d.drives.forEach(function (drv) {
        var li = el("li", "item");
        var main = el("div", "grow");
        main.appendChild(el("b", "", (drv.mount || "（沒有磁碟代號）") + " " + drv.product));
        var extra = [drv.volume ? "標籤 " + drv.volume : "", drv.size].filter(Boolean).join("、");
        main.appendChild(el("div", "small muted", (extra ? extra + " · " : "") + drv.state));
        li.appendChild(main);
        if (drv.can_register) {
          li.appendChild(button("登記…", "primary", function () { registerForm(drv, false); }));
        } else if (drv.mount && drv.has_key) {
          li.appendChild(button("重新登記…", "danger ghost", function () { registerForm(drv, true); }));
        }
        list.appendChild(li);
      });
      box.appendChild(list);
    }, function () { S.clear(box); });
  }

  function registerForm(drv, overwrite) {
    var box = S.clear($("regForm"));
    box.hidden = false;
    box.appendChild(el("h3", "", (overwrite ? "重新登記 " : "登記 ") + drv.mount));
    if (overwrite) {
      box.appendChild(el("p", "warn", "⚠️ 這支隨身碟上已經有鑰匙資料。重新登記會讓原本那把鑰匙永遠失效：" +
                                       "用它鎖過的訊息和檔案都打不開，沒有辦法救回來。"));
    }
    function field(label, input) {
      var l = el("label", "field", label);
      l.appendChild(input);
      box.appendChild(l);
      return input;
    }
    var name = field("鑰匙的名字（例如：書桌、備用）", el("input"));
    name.type = "text";
    name.maxLength = 40;
    var protect = field("保護方式", el("select"));
    [["記住", "💻 記住在這台（建議）"], ["密碼", "🔒 要密碼"], ["不保護", "⚠️ 不保護"]].forEach(function (p) {
      var o = el("option", "", p[1]);
      o.value = p[0];
      protect.appendChild(o);
    });
    var pw = field("密碼", el("input"));
    pw.type = "password";
    pw.autocomplete = "new-password";
    var pw2 = field("再打一次密碼", el("input"));
    pw2.type = "password";
    pw2.autocomplete = "new-password";
    var confirm = null;
    if (overwrite) {
      confirm = field("確定要覆蓋，就在這裡打「覆蓋」", el("input"));
      confirm.type = "text";
    }
    function sync() {
      var on = protect.value === "密碼";
      pw.parentNode.hidden = !on;
      pw2.parentNode.hidden = !on;
      if (!on) { pw.value = ""; pw2.value = ""; }
    }
    protect.addEventListener("change", sync);
    sync();
    var row = el("div", "row");
    function send(btn) {
      var values = { mount: drv.mount, label: name.value, protect: protect.value, password: pw.value };
      if (confirm) { values.confirm = confirm.value; }
      pw.value = "";
      pw2.value = "";
      busy(btn, S.act(overwrite ? "reregister" : "register", values)).then(function (out) {
        S.toast("登記好了：「" + out.label + "」");
        S.clear(box);
        box.hidden = true;
        loadKeys().catch(function () {});
        loadDrives().catch(function () {});
      }, function () {});
    }
    row.appendChild(button(overwrite ? "重新登記" : "登記", overwrite ? "danger" : "primary", function (e) {
      var btn = e.target;
      if (protect.value === "密碼" && pw.value !== pw2.value) {
        S.toast("兩次密碼不一樣", "bad");
        return;
      }
      if (protect.value !== "不保護") { send(btn); return; }
      S.confirmBox("⚠️ 不保護：撿到這支隨身碟的人也能用它解開你鎖的東西。確定不要保護？", "確定不保護").then(function (yes) {
        if (yes) { send(btn); }
      });
    }));
    row.appendChild(button("算了", "ghost", function () { S.clear(box); box.hidden = true; }));
    box.appendChild(row);
  }

  /* 訊息 ─────────────────────────────────────────────────────────── */

  function showOut(hint, text) {
    $("msgOut").hidden = false;
    $("msgOutHint").textContent = hint;
    $("msgOutText").textContent = text;
  }

  function msgLock(e) {
    var pw = $("msgPw").value;
    $("msgPw").value = "";
    busy(e.target, S.act("lock_pw", { key: $("msgKey").value, text: $("msgText").value, password: pw })).then(function (out) {
      showOut("🔒 用「" + out.label + "」鎖好了。整段複製起來；解開時這支隨身碟要插著。", out.message);
    }, function () {});
  }

  function msgCheck(e) {
    var text = $("msgText").value.trim().slice(0, 200);
    busy(e.target, S.read("check", { text: text })).then(function (out) { S.toast(out.text); }, function () {});
  }

  function msgUnlock(e) {
    var pw = $("msgPw").value;
    $("msgPw").value = "";
    busy(e.target, S.act("unlock", { text: $("msgText").value, key: $("msgKey").value, password: pw })).then(function (out) {
      showOut("🔓 解開了（只顯示在這個畫面；看完按下面的鈕清掉）：", out.text);
    }, function () {});
  }

  /* 檔案 ─────────────────────────────────────────────────────────── */

  function loadFolders() {
    return S.read("folders").then(function (d) {
      state.folders = d.folders || [];
      var box = S.clear($("folders"));
      if (!state.folders.length) {
        box.appendChild(el("p", "muted", "還沒有可以用的資料夾：在下面打一個完整的資料夾路徑加進來。"));
      } else {
        var list = el("ul", "list");
        state.folders.forEach(function (f) {
          var li = el("li", "item");
          li.appendChild(el("span", "grow", f.n + ". " + f.path));
          li.appendChild(button("打開", "ghost", function () { browse(f.n, ""); }));
          li.appendChild(button("拿掉", "ghost", function (e) {
            busy(e.target, S.act("folder_rm", { n: f.n })).then(function () { pick(""); loadFolders(); }, function () {});
          }));
          list.appendChild(li);
        });
        box.appendChild(list);
      }
      if (state.folders.length && !state.folder) { browse(1, ""); }
    });
  }

  function pick(path) {
    state.picked = path;
    $("picked").textContent = path || "（還沒選）";
  }

  function browse(n, sub) {
    return S.read("browse", { folder: n, sub: sub }).then(function (d) {
      state.folder = d.folder;
      state.sub = d.sub;
      var box = S.clear($("browser"));
      if (!d.folder) { return; }
      box.appendChild(el("p", "small muted", "第 " + d.folder + " 個資料夾" + (d.sub ? " / " + d.sub : "")));
      var list = el("ul", "list");
      if (d.sub) {
        var up = el("li", "item");
        up.appendChild(button("⬆ 上一層", "ghost", function () {
          browse(d.folder, d.sub.split("/").slice(0, -1).join("/"));
        }));
        list.appendChild(up);
      }
      d.dirs.forEach(function (name) {
        var li = el("li", "item");
        li.appendChild(button("📁 " + name, "ghost", function () { browse(d.folder, d.sub ? d.sub + "/" + name : name); }));
        list.appendChild(li);
      });
      d.files.forEach(function (f) {
        var li = el("li", "item");
        var rel = d.folder + "/" + (d.sub ? d.sub + "/" : "") + f.name;
        li.appendChild(button((f.locked ? "🔒 " : "📄 ") + f.name, "ghost", function () { pick(rel); }));
        list.appendChild(li);
      });
      if (!d.dirs.length && !d.files.length) { list.appendChild(el("li", "muted", "（空的）")); }
      box.appendChild(list);
    });
  }

  function needPick() {
    if (!state.picked) { S.toast("先在上面選一個檔", "bad"); return true; }
    return false;
  }

  function fileLock(e) {
    if (needPick()) { return; }
    var pw = $("filePw").value;
    $("filePw").value = "";
    busy(e.target, S.act("lock_pw", { key: $("fileKey").value, file: state.picked, password: pw })).then(function (out) {
      S.toast("🔒 鎖好了：" + out.name + " → " + out.out);
      browse(state.folder, state.sub);
    }, function () {});
  }

  function fileCheck(e) {
    if (needPick()) { return; }
    busy(e.target, S.read("check", { file: state.picked })).then(function (out) { S.toast(out.text); }, function () {});
  }

  function fileUnlock(e) {
    if (needPick()) { return; }
    var pw = $("filePw").value;
    $("filePw").value = "";
    busy(e.target, S.act("unlock", { key: $("fileKey").value, file: state.picked, password: pw })).then(function (out) {
      S.toast("🔓 解開了：" + out.name + " → " + out.out);
      browse(state.folder, state.sub);
    }, function () {});
  }

  function folderAdd(e) {
    var path = $("folderPath").value;
    busy(e.target, S.act("folder_add", { path: path })).then(function (out) {
      $("folderPath").value = "";
      S.toast("加好了：第 " + out.n + " 個資料夾");
      loadFolders();
    }, function () {});
  }

  S.ready(function () {
    $("reloadKeys").addEventListener("click", function (e) { loadKeys(e.target).catch(function () {}); });
    $("loadDrives").addEventListener("click", function (e) { loadDrives(e.target).catch(function () {}); });
    $("msgLock").addEventListener("click", msgLock);
    $("msgCheck").addEventListener("click", msgCheck);
    $("msgUnlock").addEventListener("click", msgUnlock);
    $("msgOutClear").addEventListener("click", function () { showOut("", ""); $("msgOut").hidden = true; });
    $("folderAdd").addEventListener("click", folderAdd);
    $("fileLock").addEventListener("click", fileLock);
    $("fileCheck").addEventListener("click", fileCheck);
    $("fileUnlock").addEventListener("click", fileUnlock);
    loadKeys().catch(function () {});
    loadFolders().catch(function () {});
  });
})();
