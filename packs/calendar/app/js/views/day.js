// 這一天：行程（改、標狀態、刪）、今日清單（勾、挑最重要的一件）、撞時間。
import { act, errorText, read } from "../api.js";
import { h, md, WEEK } from "../dom.js";
import { eventForm, taskForm } from "../forms.js";

const STATE_BTNS = [["done", "✔ 完成"], ["not_done", "🙅 沒做"], ["cancelled", "🚫 取消"], ["planned", "↩ 還原"]];
const PRIORITY = ["", "·", "‼", "🔥"];

async function run(app, name, values, message) {
  try {
    await act(name, values);
    await app.afterWrite(message);
  } catch (e) { if (e.code !== "cancelled") app.toast(errorText(e)); }
}

function statusRow(app, c) {
  const btns = STATE_BTNS.filter(([s]) => (s === "planned") === (c.status !== "planned") && s !== c.status)
    .map(([s, label]) => h("button", { type: "button", class: "btn small", onclick: () => setStatus(app, c, s) }, label));
  return btns;
}

async function setStatus(app, c, state) {
  if (state === "not_done") {
    const input = h("input", { placeholder: "為什麼沒做（選填）", maxlength: "200" });
    const go = h("button", { type: "button", class: "btn primary" }, "記下來");
    go.addEventListener("click", () => { app.closeModal(); run(app, "status", { n: c.id, state, note: input.value.trim() }, "記下了"); });
    app.openModal({ title: "這次沒做", body: [h("label", { class: "field" }, c.title, input)], foot: [go] });
    input.focus();
    return;
  }
  await run(app, "status", { n: c.id, state }, state === "cancelled" ? "取消了" : "改好了");
}

async function remove(app, c, whole) {
  const target = whole ? c.series_id : c.id;
  const what = whole ? "整串重複行程" : c.series_id ? "這一次" : "這個行程";
  if (!(await app.confirm("刪掉？", `要刪掉「${c.title}」（${what}）嗎？`, "刪掉"))) return;
  await run(app, "rm", { n: target }, "刪掉了");
}

function eventCard(app, c, clashes) {
  const mine = clashes.filter((x) => x.a.id === c.id || x.b.id === c.id);
  return h("div", { class: `card event ${c.status}` },
    h("div", { class: "row" },
      h("strong", null, (c.all_day ? "整天" : c.range || c.when) + "　" + c.title),
      c.series_id || c.repeat ? h("span", { class: "badge" }, c.repeat ? `🔁 ${c.repeat}` : "🔁") : null,
      c.status !== "planned" ? h("span", { class: "badge" }, { done: "✔ 完成了", not_done: "🙅 這次沒做", cancelled: "🚫 取消了" }[c.status]) : null),
    c.location ? h("div", { class: "muted" }, `@${c.location}`) : null,
    c.note ? h("div", { class: "note" }, c.note) : null,
    c.status_note ? h("div", { class: "muted" }, `（${c.status_note}）`) : null,
    mine.length ? h("div", { class: "warn" }, "⚠ 撞時間：" + mine.map((x) => x.pair).join("；")) : null,
    h("div", { class: "actions" },
      statusRow(app, c),
      h("button", { type: "button", class: "btn small", onclick: () => eventForm(app, { event: c }) }, c.series_id ? "✎ 只改這次" : "✎ 修改"),
      c.series_id ? h("button", { type: "button", class: "btn small", onclick: () => editSeries(app, c) }, "✎ 改整串") : null,
      h("button", { type: "button", class: "btn small danger", onclick: () => remove(app, c, false) }, c.series_id ? "只跳過這次" : "🗑 刪掉"),
      c.series_id ? h("button", { type: "button", class: "btn small danger", onclick: () => remove(app, c, true) }, "🗑 刪整串") : null));
}

function editSeries(app, c) {
  eventForm(app, { event: { ...c, id: c.series_id, occurrence: "" }, series: true });   // 整串：不動開始那天
}

function taskRow(app, t, date) {
  return h("li", { class: `task ${t.done ? "done" : ""}` },
    h("input", { type: "checkbox", checked: t.done, "aria-label": `完成 ${t.title}`,
      onchange: (ev) => run(app, "done", { n: t.id, done: ev.target.checked }, ev.target.checked ? "做完了" : null) }),
    h("span", { class: "grow" }, (t.is_focus ? "⭐ " : "") + (PRIORITY[t.priority] || "") + " " + t.title,
      t.late ? h("span", { class: "muted" }, "（順延）") : null,
      t.detail ? h("div", { class: "muted" }, t.detail) : null),
    h("button", { type: "button", class: "btn small", title: "今天最重要的一件事",
      onclick: () => run(app, "focus", { n: t.is_focus ? "-" : t.id, date }, t.is_focus ? "不再是最重要的" : "挑好了") }, t.is_focus ? "取消⭐" : "⭐"),
    h("button", { type: "button", class: "btn small", onclick: () => taskForm(app, { task: t }) }, "✎"),
    h("button", { type: "button", class: "btn small danger", "aria-label": `刪掉 ${t.title}`,
      onclick: async () => { if (await app.confirm("刪掉卡片？", t.title, "刪掉")) run(app, "task_rm", { n: t.id }, "刪掉了"); } }, "🗑"));
}

export async function render(app) {
  const d = await read("day", { date: app.date });
  const weekday = WEEK[(new Date(`${d.date}T12:00:00`).getDay() + 6) % 7];
  const quick = h("input", { placeholder: "加一張卡片（要做的事）…", maxlength: "120", "aria-label": "新卡片" });
  const add = () => {
    const title = quick.value.trim();
    if (title) run(app, "task", { text: title, date: d.date }, "加進清單了");
  };
  quick.addEventListener("keydown", (ev) => { if (ev.key === "Enter") add(); });
  const view = d.tasks;
  return h("div", null,
    h("h2", null, `${md(d.date)}（${weekday}）${d.date === app.today ? "　今天" : ""}`),
    view.focus ? h("div", { class: "card focus-card" }, h("span", { class: "muted" }, "🎯 這天最重要的一件事"), h("strong", null, view.focus.title)) : null,
    d.clashes.length ? h("div", { class: "card warn" }, "⚠ 撞時間", d.clashes.map((c) => h("div", null, c.text,
      c.ok ? null : h("button", { type: "button", class: "btn small", onclick: () => run(app, "keep", { a: c.a.id, b: c.b.id }, "兩個都留了") }, "兩個都留")))) : null,
    h("h3", null, `行程（${d.events.length}）`),
    d.events.length ? d.events.map((c) => eventCard(app, c, d.clashes)) : h("p", { class: "muted" }, "這天沒有行程。按右上角「＋ 行程」加一個。"),
    h("h3", null, `清單（${view.done}/${view.total}）`),
    h("ul", { class: "tasks" }, view.tasks.map((t) => taskRow(app, t, d.date))),
    h("div", { class: "quick" }, quick, h("button", { type: "button", class: "btn", onclick: add }, "加")));
}
