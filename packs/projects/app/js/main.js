// 專案畫面：清單、篩選、一鍵暫停、編輯。只經 SYNOX 的共用元件（SynoxApp.read／act）說話，不自己連線、不輪詢。
import { h, fill, isoLocal } from "./dom.js";

const $ = (sel) => document.querySelector(sel);
const CLEAR = "-";                       // 欄位給「-」＝清掉

const state = { data: null, filter: "all", reason: "", q: "" };
let closeModal = () => {};
let toastTimer = 0;

function kit() {
  const k = globalThis.SynoxApp;
  if (!k) throw new Error("沒有從 SYNOX 打開這個畫面");
  return k;
}

function toast(text, undo) {
  const box = $("#toast");
  fill(box, h("span", null, text), undo ? h("button", { type: "button", class: "btn ghost", onclick: () => { box.hidden = true; undo(); } }, "↩ 復原") : null);
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, undo ? 12000 : 3500);
}

const errText = (e) => (e && (e.error || e.message)) || "發生錯誤";

// 做一個動作；成功回 data、失敗顯示原因回 null
async function act(name, values) {
  try {
    return await kit().act(name, values, { quiet: true });
  } catch (e) {
    toast(`${name === "add" ? "加不進去" : "存不起來"}：${errText(e)}`);
    return null;
  }
}

async function load() {
  try {
    state.data = await kit().read("projects", {});
  } catch (e) {
    fill($("#list"), h("p", { class: "muted" }, "讀不到專案：" + errText(e)));
    return;
  }
  render();
}

// ---- 詞彙查詢 ----
const meta = () => state.data || { states: [], reasons: [], legacy_reasons: [], resting_states: [], resume_presets: [], projects: [], counts: {} };
const stateOf = (id) => meta().states.find((s) => s.id === id) || { icon: "•", label: id, hint: "" };
// 一定要連退休的一起找，否則按過那些原因的卡片會看起來像原因不見了
const reasonOf = (id) => (id ? meta().reasons.concat(meta().legacy_reasons || []).find((r) => r.id === id) || null : null);
const refName = (id) => {
  const hit = id && meta().projects.find((x) => x.id === id);
  return hit ? hit.name : "";
};

// ---- 清單 ----
function visible() {
  const q = state.q.trim().toLowerCase();
  return meta().projects.filter((p) => {
    // 冷掉、說好回來看是「橫向」的：跨狀態撈，比對的是程式算出來的旗標，不是 p.state
    if (state.filter === "cold") { if (!p.is_cold) return false; }
    else if (state.filter === "due") { if (!p.resume_due) return false; }
    // 「全部」刻意不含放下的：放下了還天天出現在眼前，就不算放下了
    else if (state.filter === "all" ? p.state === "archived" : p.state !== state.filter) return false;
    if (state.reason === "(none)") { if (p.reason) return false; }
    else if (state.reason && p.reason !== state.reason) return false;
    if (!q) return true;
    return [p.name, p.path, p.status, p.next, p.reason_note].some((t) => (t || "").toLowerCase().includes(q))
      || (p.tags || []).some((t) => t.toLowerCase().includes(q));
  }).sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));
}

function render() {
  const all = meta().projects;
  $("#count").textContent = all.length ? String(all.length) : "";
  renderChips();
  const list = $("#list");
  if (!all.length) return fill(list, h("p", { class: "muted" }, "還沒有追蹤任何專案。按上面「＋ 專案」加一個。"));
  const shown = visible();
  if (!shown.length) {
    const why = state.q ? "沒有符合的專案。" : state.filter === "cold" ? "沒有專案冷掉——全部都還在動。"
      : state.filter === "due" ? "沒有到期的回訪日。" : "這個狀態下沒有專案。";
    return fill(list, h("p", { class: "muted" }, why));
  }
  fill(list, shown.map(card));
}

function renderChips() {
  const host = $("#chips");
  const counts = meta().counts || {};
  const chip = (label, on, fn, title, cls) => h("button", { type: "button", class: `chip ${cls || ""} ${on ? "active" : ""}`, title: title || "", onclick: fn }, label);
  const pick = (id) => { state.filter = state.filter === id && id !== "all" ? "all" : id; if (state.filter !== "paused") state.reason = ""; render(); };
  const row = [chip("全部", state.filter === "all", () => pick("all"))];
  for (const s of meta().states) if (counts[s.id]) row.push(chip(`${s.icon} ${counts[s.id]}`, state.filter === s.id, () => pick(s.id), `${s.label}　${s.hint}`));
  // 🧊 程式自己量的、🔖 你自己約的：跟上面那排狀態平行的一格，不是第七個狀態；顏色分開讓人決定的與機器量的看得出來
  if (counts.cold) row.push(chip(`🧊 冷掉 ${counts.cold}`, state.filter === "cold", () => pick("cold"),
    `${meta().cold_days || 30} 天以上沒有紀錄／檔案變動，而且還沒完成或放下。這格不看你按的狀態，是程式掃資料夾自己算的。`, "cold"));
  if (counts.due_back) row.push(chip(`🔖 回來看 ${counts.due_back}`, state.filter === "due", () => pick("due"), "你自己說要回來看它們的日子到了（或過了）。", "due"));
  host.replaceChildren(...row);
  if (state.filter !== "paused") return;
  // 第二層：只有停在「暫停中」時才展開，問的是「為什麼停」
  const paused = meta().projects.filter((p) => p.state === "paused");
  const sub = [chip("全部", !state.reason, () => { state.reason = ""; render(); }, "", "sub")];
  for (const r of meta().reasons.concat(meta().legacy_reasons || [])) {
    const n = paused.filter((p) => p.reason === r.id).length;
    if (n) sub.push(chip(`${r.icon} ${n}`, state.reason === r.id, () => { state.reason = state.reason === r.id ? "" : r.id; render(); }, `${r.label}　${r.hint || ""}`, "sub"));
  }
  const none = paused.filter((p) => !p.reason).length;
  if (none) sub.push(chip(`— ${none}`, state.reason === "(none)", () => { state.reason = state.reason === "(none)" ? "" : "(none)"; render(); }, "沒填原因", "sub"));
  host.append(h("div", { class: "subchips" }, sub));
}

function card(p) {
  const resting = (meta().resting_states || []).includes(p.state);
  const paused = p.state === "paused";
  const top = h("div", { class: "top-line" },
    h("span", { class: "ico" }, stateOf(p.state).icon),
    h("span", { class: "name" }, p.name),
    h("button", { type: "button", class: `mini ${p.pinned ? "on" : ""}`, title: p.pinned ? "取消標記" : "標記：常看的放前面",
      onclick: async () => { if (await act("update", { id: p.id, pinned: p.pinned ? "no" : "yes" })) load(); } }, p.pinned ? "📌" : "☆"),
    // 靜止的狀態（完成／放下／保管中）沒有「暫停」可言
    resting ? null : h("button", { type: "button", class: `mini ${paused ? "on" : ""}`,
      title: paused ? "回來做：改回進行中（回訪日清掉，原因留著）" : "先停一下：記為什麼停、讓位給誰、什麼時候回來看",
      onclick: () => (paused ? resume(p) : openPause(p)) }, paused ? "▶" : "⏸"),
    h("button", { type: "button", class: "mini", title: "編輯狀態／下一步", onclick: () => openEditor(p) }, "⋯"));

  const chips = [];
  if (p.idle_days > 0) {
    const c = h("span", { class: `tag ${p.is_cold ? "stale" : ""} ${p.idle_is_real ? "" : "guess"}`, title: p.idle_is_real
      ? `依${p.activity_source === "git" ? " git 最後一次紀錄" : "資料夾裡檔案的修改時間"}${p.commit_days === null ? "" : `　·　最後一次 commit：${p.commit_days} 天前`}`
      : "還沒掃描過——這是這張卡上次被編輯的時間，不是資料夾真的有沒有在動。按上面「⟳ 更新」" }, `${p.idle_days} 天沒動`);
    chips.push(c);
  }
  const r = reasonOf(p.reason);
  if (r) {
    const ref = refName(p.reason_ref);
    // 讓位給誰直接寫在原因旁邊：「☄️ 隕石插隊 → 某專案」一眼就是一筆機會成本。不在暫停中時變淡的「上次停」
    chips.push(h("span", { class: `tag clickable ${paused ? "" : "past"}`, title: [r.hint, p.reason_note].filter(Boolean).join("\n"),
      onclick: () => openPause(p) }, `${paused ? "" : "上次停："}${r.icon} ${r.label}${ref ? " → " + ref : ""}`));
  }
  if (p.paused_days !== null && p.paused_days !== undefined) chips.push(h("span", { class: "tag" }, p.paused_days === 0 ? "今天停的" : `已停 ${p.paused_days} 天`));
  if (p.resume_days !== null && p.resume_days !== undefined) {
    // 過期的轉紅——但它是你自己約的，不是機器發現你偷懶，所以文案是「說好今天回來看」，不是「拖了 N 天」
    chips.push(h("span", { class: `tag clickable ${p.resume_days <= 0 ? "due" : ""}`, title: `回訪日：${p.resume_at}　·　點一下可以改`, onclick: () => openPause(p) },
      p.resume_days > 0 ? `🔖 ${p.resume_days} 天後回來看` : p.resume_days === 0 ? "🔖 說好今天回來看" : `🔖 說好 ${-p.resume_days} 天前回來看`));
  }
  for (const t of p.tags || []) chips.push(h("span", { class: "tag tagname" }, "#" + t));

  const parts = [top, chips.length ? h("div", { class: "tags" }, chips) : null];
  if (p.next) parts.push(h("div", { class: "next" }, "▸ " + p.next));
  else if (p.status) parts.push(h("div", { class: "next dim" }, "· " + p.status));
  if (p.path) {
    parts.push(h("div", { class: "path small" }, p.path,
      p.path_exists ? null : h("button", { type: "button", class: "fix", title: p.resolved_path ? `找不到：${p.resolved_path}` : "要填完整路徑（不能是相對路徑）", onclick: () => openEditor(p, true) }, "路徑不存在 · 修一下")));
  }
  return h("article", { class: `card proj ${p.pinned ? "pinned" : ""} ${p.path && !p.path_exists ? "missing" : ""}` }, parts);
}

async function resume(p) {
  // 回來做：回訪日清掉（約定完成了），原因留著
  if (await act("resume", { id: p.id })) load();
}

async function scanAll(btn) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "掃描中…";
  try {
    const d = await kit().act("scan", {}, { quiet: true });
    await load();
    toast(`⟳ 掃描了 ${d.scanned} 個專案，「幾天沒動」現在看的是資料夾裡真的有沒有動`);
  } catch (e) {
    toast("掃描失敗：" + errText(e));
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

// ---- 彈窗 ----
function modal(title, sub, body, actions) {
  const wrap = $("#modal");
  const close = () => { wrap.hidden = true; fill(wrap); };
  closeModal = close;
  fill(wrap, h("div", { class: "modal", role: "dialog", "aria-modal": "true" },
    h("h2", null, title), sub ? h("p", { class: "muted small" }, sub) : null, h("div", { class: "modal-body" }, body), h("div", { class: "modal-foot" }, actions)));
  wrap.hidden = false;
  return close;
}
const field = (label, node, hint) => h("div", { class: "field" }, h("label", null, label), node, hint ? h("div", { class: "muted small" }, hint) : null);
const text = (value, ph, max) => h("input", { type: "text", value: value || "", placeholder: ph || "", maxlength: max || 400 });
const area = (value, ph) => h("textarea", { rows: 2, placeholder: ph || "", maxlength: 400 }, value || "");

// 為什麼停：⏸ 快捷面板與編輯共用這一份（問法與讓位對象是會一直長的東西，兩份必然走鐘）。回 { node, get() }
function reasonField(p) {
  let reasonId = (p && p.reason) || "";
  let refId = (p && p.reason_ref) || "";
  const note = text(p && p.reason_note, "", 400);
  const box = h("div", { class: "choices" });
  const refSel = h("select", null);
  const refWrap = h("div", { class: "ref" }, refSel);
  const refHint = h("div", { class: "muted small" });

  const syncAsk = () => {
    const r = reasonOf(reasonId);
    note.placeholder = (r && r.ask) || "一句話就好：等下個月額度、忙專案 X…";
    const kind = (r && r.ref) || "";
    refHint.textContent = kind === "project" ? "選了之後，這就是一筆「為了它，我放下這個」的紀錄。" : "";
    refWrap.hidden = !kind;
    if (!kind) return;
    const opts = [h("option", { value: "" }, "（沒有特定對象／不想說）")];
    for (const x of meta().projects.filter((x) => !p || x.id !== p.id)) opts.push(h("option", { value: x.id }, `${stateOf(x.state).icon} ${x.name}`));
    fill(refSel, opts);
    refSel.value = refId;
    if (refSel.value !== refId) refId = refSel.value;
  };
  const newInp = text("", "例如：等對方回覆", 12);
  const newRow = h("div", { class: "newreason", hidden: true }, newInp, h("button", { type: "button", class: "btn primary", onclick: addReason }, "加進去"));
  async function addReason() {
    const label = newInp.value.trim();
    if (!label) return;
    const d = await act("reason_add", { label });
    if (!d) return;
    state.data = await kit().read("projects", {});      // 詞彙換新的，按鈕列才長得出來
    reasonId = d.reason.id;
    newInp.value = "";
    newRow.hidden = true;
    paint(); syncAsk();
  }
  newInp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addReason(); } });
  function paint() {
    const btn = (r) => h("button", { type: "button", class: `choice ${reasonId === r.id ? "active" : ""}`, title: r.hint || "",
      onclick: () => { reasonId = reasonId === r.id ? "" : r.id; paint(); syncAsk(); } }, `${r.icon} ${r.label}`);
    const shown = meta().reasons.map(btn);
    const legacy = (meta().legacy_reasons || []).find((r) => r.id === reasonId);
    if (legacy) shown.push(btn(legacy));      // 退休的原因還被按著時也畫出來，否則看起來像原因被吃掉
    // 自己加一個：內建分類裝不下真正的理由時；沒有這顆，那一格會被硬塞成不精確的原因
    shown.push(h("button", { type: "button", class: "choice add", title: "打一個你自己的原因，之後它就在這排按鈕裡了",
      onclick: (e) => { e.currentTarget.hidden = true; newRow.hidden = false; newInp.focus(); } }, "＋ 自己加一個"));
    fill(box, shown, newRow);
  }
  paint(); syncAsk();
  refSel.addEventListener("change", () => { refId = refSel.value; });
  return {
    node: h("div", null, box, note, refWrap, refHint),
    get: () => ({ reason: reasonId, reason_note: note.value, reason_ref: (reasonOf(reasonId) || {}).ref ? refId : "" }),
  };
}

// 什麼時候回來看：預設「先不設」——不逼你承諾一個日期；一旦設了，到那天會排到前面
function resumeField(current) {
  const inp = h("input", { type: "date", value: current || "" });
  const btns = [];
  const sync = () => btns.forEach((b) => b.classList.toggle("active", b.dataset.v === inp.value));
  const mk = (label, value, title) => {
    const b = h("button", { type: "button", class: "choice", title: title || "", onclick: () => { inp.value = value; sync(); } }, label);
    b.dataset.v = value;
    btns.push(b);
    return b;
  };
  const row = [mk("先不設", "", "不約時間也完全可以——那就是一個誠實的答案")];
  for (const pre of meta().resume_presets || []) {
    const d = new Date();
    d.setDate(d.getDate() + pre.days);
    row.push(mk(pre.label, isoLocal(d)));
  }
  inp.addEventListener("change", sync);
  sync();
  return { node: h("div", null, h("div", { class: "choices" }, row), inp), value: () => inp.value };
}

// ⏸ 一鍵暫停：只問該問的三件事
function openPause(p) {
  const rf = reasonField(p);
  const resumeAt = resumeField(p.resume_at);
  const next = area(p.next, "接前端看板的拖曳；先看清單那一頁的程式");
  const ok = h("button", { type: "button", class: "btn primary" }, "⏸ 暫停");
  const close = modal("⏸ 先停一下：" + p.name, "停下來也是一個決定。花十秒記三件事，三個月後回來的是你自己。", [
    field("為什麼停下來", rf.node),
    field("什麼時候回來看", resumeAt.node, "到那天，它會排在「🔖 回來看」裡。在那之前不會拿它煩你。"),
    field("回來先做什麼", next, "回來的時候先看這一行就好。")], [
    h("button", { type: "button", class: "btn ghost", onclick: () => { close(); openEditor(p); } }, "完整設定…"),
    h("button", { type: "button", class: "btn ghost", onclick: () => close() }, "取消"), ok]);
  ok.addEventListener("click", async () => {
    const r = rf.get();
    const d = await act("pause", { id: p.id, reason: r.reason || CLEAR, reason_note: r.reason_note || CLEAR, reason_ref: r.reason_ref || CLEAR,
      resume_at: resumeAt.value() || CLEAR, next: next.value || CLEAR });
    if (d) { close(); load(); }
  });
}

// 一個編輯器管一個專案的全部。順序＝回來時回答的順序：狀態 → 為什麼停 → 做到哪 → 下一步
function openEditor(p, focusPath) {
  const isNew = !p;
  const name = text(p && p.name, "範例專案", 60);
  const path = text(p && p.path, "完整路徑，例如 ~/work/sample");
  const pathHint = h("div", { class: "muted small" }, "選填。填了才能掃描「真的多久沒動」；只讀，不會改動資料夾裡的任何東西。");
  let timer = 0;
  const checkPath = async () => {
    const raw = path.value.trim();
    if (!raw) { pathHint.className = "muted small"; pathHint.textContent = "選填。填了才能掃描「真的多久沒動」；只讀，不會改動資料夾裡的任何東西。"; return; }
    try {
      const d = await kit().read("path_check", { path: raw });
      pathHint.className = `small ${d.exists ? "ok" : "bad"}`;
      pathHint.textContent = d.exists ? (d.resolved === raw ? "✓ 這個資料夾在" : `✓ 這個資料夾在 → ${d.resolved}`)
        : d.relative ? "✗ 要填完整路徑（開頭是 / 、磁碟機代號或 ~）" : "✗ 找不到這個資料夾";
    } catch { /* 查不到就不顯示 */ }
  };
  path.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(checkPath, 400); });
  if (path.value) checkPath();

  let stateId = (p && p.state) || "active";
  const stateBtns = meta().states.map((s) => h("button", { type: "button", class: `choice ${stateId === s.id ? "active" : ""}`, title: s.hint,
    "data-s": s.id, onclick: () => { stateId = s.id; stateBtns.forEach((b) => b.classList.toggle("active", b.dataset.s === stateId)); syncState(); } }, `${s.icon} ${s.label}`));

  const rf = reasonField(isNew ? null : p);
  const reasonLab = h("label", null, "為什麼停下來");
  const fReason = h("div", { class: "field" }, reasonLab, rf.node);
  const resumeAt = resumeField(p && p.resume_at);
  const why = text(p && p.why_now, "", 400);
  const status = area(p && p.status, "後端做完了，前端只剩清單那一頁");
  const next = area(p && p.next, "接前端看板的拖曳；先看清單那一頁的程式");
  const tags = text(p && (p.tags || []).join(", "), "python, 工作, 練習", 200);
  const pinned = h("input", { type: "checkbox", checked: p ? !!p.pinned : true });
  // 為什麼那一格不再隨狀態消失：暫停中問「為什麼停下來」，其他狀態是「上次停下來的原因」（留著，三個月後會想知道）
  function syncState() {
    const paused = stateId === "paused";
    reasonLab.textContent = paused ? "為什麼停下來" : "上次停下來的原因";
    fReason.classList.toggle("dim", !paused);
    why.placeholder = paused ? "回來之後為什麼還要做它？" : "這輪只押這一個";
  }
  syncState();

  const del = isNew ? null : h("button", { type: "button", class: "btn danger" }, "刪除");
  const ok = h("button", { type: "button", class: "btn primary" }, isNew ? "開始追蹤" : "儲存");
  const close = modal(isNew ? "＋ 追蹤新專案" : "專案設定", isNew ? "記下位置與狀態；之後回來只要看「下一步」就能接上。" : p.name, [
    field("專案名稱", name), h("div", { class: "field" }, h("label", null, "位置"), path, pathHint),
    field("狀態", h("div", { class: "choices" }, stateBtns)), fReason,
    field("什麼時候回來看", resumeAt.node), field("為什麼現在做這個", why, "選填。這輪的力氣為什麼押在它身上。"),
    field("現在做到哪", status), field("下一步要做什麼", next, "回來的時候先看這一行就好。"),
    field("標籤", tags), h("label", { class: "pin" }, pinned, " 📌 標記：放在清單前面")], [
    del, h("button", { type: "button", class: "btn ghost", onclick: () => close() }, "取消"), ok]);

  ok.addEventListener("click", async () => {
    const r = rf.get();
    const clear = (v) => (v === "" || v === undefined ? CLEAR : v);
    let id = p && p.id;
    if (isNew) {
      const made = await act("add", { name: name.value, ...(path.value.trim() ? { path: path.value } : {}) });
      if (!made) return;
      id = made.project.id;
    }
    const body = { id, name: name.value, path: clear(path.value.trim()), state: stateId, reason: clear(r.reason), reason_note: clear(r.reason_note.trim()),
      reason_ref: clear(r.reason_ref), resume_at: clear(resumeAt.value()), why_now: clear(why.value.trim()), status: clear(status.value.trim()),
      next: clear(next.value.trim()), tags: clear(tags.value.trim()), pinned: pinned.checked ? "yes" : "no" };
    if (await act("update", body)) { close(); load(); }
  });
  if (del) {
    del.addEventListener("click", async () => {
      // rm 是危險動作：SYNOX 會先跳確認框；停止追蹤只刪這裡的紀錄，資料夾與檔案完全不會動
      let d;
      try { d = await kit().act("rm", { id: p.id }, { quiet: true }); } catch (e) { if (e && e.code !== "cancelled") toast("刪不掉：" + errText(e)); return; }
      close(); await load();
      toast(`停止追蹤「${p.name}」`, async () => { if (await act("restore", { id: d.id })) load(); });
    });
  }
  (focusPath ? path : name).focus();
}

kit_ready();
function kit_ready() {
  const k = globalThis.SynoxApp;
  if (!k) { fill($("#list"), h("p", { class: "muted" }, "沒有從 SYNOX 打開這個畫面。回到 SYNOX 的工具頁再按一次「打開」。")); return; }
  k.ready(() => {
    $("#addBtn").addEventListener("click", () => openEditor(null));
    $("#scanBtn").addEventListener("click", (e) => scanAll(e.currentTarget));
    $("#filter").addEventListener("input", (e) => { state.q = e.target.value; render(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
    load();
  });
}
