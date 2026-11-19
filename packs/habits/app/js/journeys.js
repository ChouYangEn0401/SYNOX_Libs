// 旅程頁：旅程清單、新旅程、一條旅程的每一格（完成、心情與筆記、插入休息或臨時的事、刪掉插進來的）。
// 錯過不算失敗：沒做的那天插一格休息，後面的步驟自己順延。
import { read, act } from "./api.js";
import { h, fill, mmdd } from "./dom.js";
import { go, guard, openModal, closeModal, ask } from "./main.js";

let meta = null;
let current = null;           // 正在看的旅程 id；null＝清單
let openCell = null;          // 展開的格子位置

export async function render(root, arg) {
  if (arg !== undefined) { current = arg; openCell = null; }
  meta = meta || (await read("meta"));
  if (current) return detail(root);
  return list(root);
}

async function run(fn) {
  const out = await guard(fn);
  if (out !== null) await go("journeys");
  return out;
}

// ---- 清單
async function list(root) {
  const { milestones } = await read("milestones");
  fill(root,
    h("div", { class: "bar" }, h("h1", null, "旅程"),
      h("button", { type: "button", class: "btn primary", onclick: () => form(null) }, "＋ 開一個旅程")),
    milestones.length ? milestones.map(summary)
      : h("p", { class: "muted" }, "還沒有旅程。旅程是有期限、一步一步走的事；錯過一天只是插一格休息，後面順延。"));
}

function ring(pct) {
  return h("div", { class: "pct", title: `${pct}%` }, h("div", { class: "meter" }, h("i", { style: { width: pct + "%" } })), h("small", null, `${pct}%`));
}

function summary(m) {
  return h("section", { class: "card journey", onclick: () => go("journeys", m.id), tabindex: "0", role: "button",
    onkeydown: (e) => { if (e.key === "Enter") go("journeys", m.id); } },
    h("div", { class: "bar" }, h("b", null, `${m.emoji} ${m.name}`), h("span", { class: "badge" }, m.level_name)),
    m.goal ? h("div", { class: "muted" }, m.goal) : null,
    ring(m.pct),
    h("div", { class: "muted" }, `${m.done}/${m.total} 步`, m.streak ? `　連續 ${m.streak}` : "",
      m.pct < 100 ? `　剩 ${m.days_left} 天` : "　已走完 🎉", m.current ? `　下一步：${m.current}` : ""));
}

// ---- 新增／改旅程本身
function form(m) {
  const name = h("input", { type: "text", maxlength: "400", value: m ? m.name : "" });
  const goal = h("input", { type: "text", maxlength: "400", value: m ? m.goal : "" });
  const emoji = h("input", { type: "text", maxlength: "8", value: m ? m.emoji : "🗺️" });
  const start = h("input", { type: "date", value: m ? m.start_date : "" });
  const cadence = h("select", null, meta.cadences.map((c) => h("option", { value: c.id, title: c.hint, selected: m && m.cadence === c.id }, c.label)));
  const remind = h("input", { type: "time", value: m ? m.remind_time : "" });
  const plan = h("textarea", { rows: "6", placeholder: "每行一步，例如：\n畫角色\n寫關卡\n上架" });
  const field = (label, node) => h("label", { class: "field" }, label, node);
  openModal(m ? "改旅程" : "開一個旅程", [
    field("名字", name), field("目標（選填）", goal), field("圖示", emoji), field("從哪天開始（不填＝今天）", start),
    field("節奏", cadence), field("每天提醒時間（選填，不設就只在月曆顯示）", remind),
    m ? null : field("每一步（每行一步，之後也能補）", plan),
  ], [
    { label: "取消", cls: "ghost" },
    { label: "儲存", cls: "primary", onclick: async () => {
      if (!name.value.trim()) { name.focus(); return false; }
      const p = { name: name.value.trim(), goal: goal.value.trim(), emoji: emoji.value.trim() || "🗺️",
        cadence: cadence.value, remind_time: remind.value };
      if (start.value) p.start_date = start.value;
      if (m) p.id = m.id;
      else p.plan = plan.value.split("\n").map((s) => s.trim()).filter(Boolean);
      const out = await guard(() => act("ms_save", { json: JSON.stringify(p) }));
      if (out === null) return false;
      closeModal();
      go("journeys", (out.milestone && out.milestone.id) || (m && m.id) || null);
      return true;
    } },
  ]);
}

// ---- 一條旅程
async function detail(root) {
  const m = await read("milestone", { id: current });
  const s = m.stats;
  fill(root,
    h("div", { class: "bar" },
      h("button", { type: "button", class: "btn ghost", onclick: () => go("journeys", null) }, "← 全部旅程"),
      h("span", null,
        h("button", { type: "button", class: "btn small ghost", onclick: () => form(m) }, "✎ 改"),
        h("button", { type: "button", class: "btn small danger", onclick: () => removeJourney(m) }, "刪除"))),
    h("h1", null, `${m.emoji} ${m.name}`),
    m.goal ? h("p", { class: "muted" }, "🎯 " + m.goal) : null,
    ring(s.pct),
    h("p", { class: "muted" }, `${s.done}/${s.total} 步・${s.level_name}・連續 ${s.streak}・${s.xp} XP`
      + `・最晚 ${mmdd(s.hard_deadline)}（${s.days_left >= 0 ? `剩 ${s.days_left} 天` : `超過 ${-s.days_left} 天`}）`),
    h("div", { class: "acts" },
      h("button", { type: "button", class: "btn small", onclick: () => insertAtCurrent(m, "rest") }, "🍜 今天沒做（插休息）"),
      h("button", { type: "button", class: "btn small", onclick: () => insertAtCurrent(m, "extra") }, "⚡ 臨時的事"),
      h("button", { type: "button", class: "btn small", onclick: () => addStep(m) }, "＋ 補一步")),
    h("div", { class: "cells" }, m.cells.map((c, i) => cell(m, c, i))));
}

function cell(m, c, i) {
  const open = openCell === i;
  const toggle = () => { openCell = open ? null : i; go("journeys"); };
  if (c.type === "rest") {
    return h("div", { class: "cell rest" }, h("span", null, `${c.sticker || "💤"} ${c.label || "休息"}`), h("small", { class: "muted" }, mmdd(c.date)),
      h("button", { type: "button", class: "btn small ghost", onclick: () => run(() => act("ms_remove", { id: m.id, idx: i })) }, "拿掉"));
  }
  const done = c.state === "done";
  const label = c.type === "task" ? `${c.n}. ${c.title}` : `＋ ${c.title || "臨時的事"}`;
  const row = h("div", { class: "cell " + (done ? "done" : c.is_current ? "current" : "") },
    h("button", { type: "button", class: "chk", "aria-label": done ? "取消完成" : "完成",
      onclick: () => run(() => act("ms_complete", { id: m.id, idx: i, undo: done ? "yes" : "" })) }, done ? "✅" : c.is_current ? "👉" : "⬜"),
    h("button", { type: "button", class: "ttl", onclick: toggle }, label, " ", h("small", { class: "muted" }, mmdd(c.date)),
      c.check ? " 🏁" : ""));
  if (!open) return row;
  const mood = h("input", { type: "text", value: c.mood || "", placeholder: "今天的心情" });
  const note = h("input", { type: "text", placeholder: "加一條筆記" });
  return h("div", { class: "open" }, row,
    c.desc ? h("p", { class: "muted" }, c.desc) : null,
    (c.notes || []).map((n) => h("p", { class: "note" }, "📝 " + n)),
    h("div", { class: "acts" }, mood, h("button", { type: "button", class: "btn small",
      onclick: () => run(() => act("ms_edit", { id: m.id, idx: i, mood: mood.value.trim() })) }, "存心情")),
    h("div", { class: "acts" }, note, h("button", { type: "button", class: "btn small",
      onclick: () => note.value.trim() && run(() => act("ms_edit", { id: m.id, idx: i, note: note.value.trim() })) }, "加筆記")),
    h("div", { class: "acts" },
      h("button", { type: "button", class: "btn small ghost", onclick: () => run(() => act("ms_insert", { id: m.id, idx: i, where: "before", kind: "rest" })) }, "前面插一格休息"),
      h("button", { type: "button", class: "btn small ghost", onclick: () => run(() => act("ms_insert", { id: m.id, idx: i, where: "after", kind: "rest" })) }, "後面插一格休息"),
      c.ins ? h("button", { type: "button", class: "btn small danger", onclick: () => run(() => act("ms_remove", { id: m.id, idx: i })) }, "刪掉這格") : null));
}

function currentIdx(m) {
  const i = m.cells.findIndex((c) => c.is_current);
  return i >= 0 ? i : m.cells.length;
}

function insertAtCurrent(m, kind) {
  const label = h("input", { type: "text", placeholder: kind === "rest" ? "吃飯、生病…" : "臨時的事" });
  let sticker = "💤";
  const stickers = h("div", { class: "chips" }, (meta.stickers || []).map(([e, t]) => h("button", { type: "button", class: "btn small ghost",
    title: t, onclick: (ev) => { sticker = e; label.value = label.value || t; ev.target.parentNode.querySelectorAll("button").forEach((b) => b.classList.remove("primary")); ev.target.classList.add("primary"); } }, e)));
  openModal(kind === "rest" ? "今天沒做：插一格休息，後面順延" : "插一件臨時的事", [
    h("label", { class: "field" }, "寫什麼", label), kind === "rest" ? stickers : null,
  ], [
    { label: "取消", cls: "ghost" },
    { label: "插入", cls: "primary", onclick: async () => {
      const v = { id: m.id, idx: currentIdx(m), where: "before", kind };
      if (kind === "rest") Object.assign(v, { label: label.value.trim() || "休息", sticker });
      else Object.assign(v, { title: label.value.trim() || "額外任務" });
      await run(() => act("ms_insert", v));
    } },
  ]);
}

function addStep(m) {
  const title = h("input", { type: "text", placeholder: "這一步要做什麼" });
  openModal("補一步（算進原本的計畫，不算插隊）", [h("label", { class: "field" }, "標題", title)], [
    { label: "取消", cls: "ghost" },
    { label: "加進去", cls: "primary", onclick: async () => {
      if (!title.value.trim()) return false;
      await run(() => act("ms_insert", { id: m.id, idx: m.cells.length - 1 < 0 ? 0 : m.cells.length - 1, where: m.cells.length ? "after" : "before",
        kind: "task", title: title.value.trim(), blueprint: "yes" }));
    } },
  ]);
}

async function removeJourney(m) {
  if (!(await ask("刪掉這個旅程？", `刪掉「${m.name}」和它所有的紀錄。`, "刪掉"))) return;
  const out = await guard(() => act("ms_rm", { ref: m.id }));
  if (out !== null) go("journeys", null);
}
