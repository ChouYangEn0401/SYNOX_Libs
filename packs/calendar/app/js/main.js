// 行事曆的進入點：三個分頁（月曆、這一天、接下來）、前後切換、提示、對話框。
// 等 SYNOX 的共用元件進門（SynoxApp.ready）才開始；不輪詢（回到這個分頁時才看一次有沒有變）。
import { download, errorText, read } from "./api.js";
import { addDays, fill, h, pad } from "./dom.js";
import { eventForm } from "./forms.js";
import * as month from "./views/month.js";
import * as day from "./views/day.js";
import * as agenda from "./views/agenda.js";

const TABS = [["month", "🗓", "月曆"], ["day", "📌", "這一天"], ["agenda", "📋", "接下來"]];
const VIEWS = { month, day, agenda };
const $ = (id) => document.getElementById(id);

export const app = { tab: "month", today: null, month: null, date: null, stamp: "" };

function monthShift(key, n) {
  const [y, m] = key.split("-").map(Number);
  const d = new Date(y, m - 1 + n, 1);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
}

function renderTabs() {
  fill($("tabs"), TABS.map(([id, ico, name]) => h("button", {
    type: "button", class: "tab", "aria-current": app.tab === id ? "page" : null, onclick: () => go(id),
  }, h("span", { class: "ico", "aria-hidden": "true" }, ico), name)));
}

function renderNav() {
  const label = app.tab === "month" ? `${app.month.slice(0, 4)} 年 ${Number(app.month.slice(5))} 月`
    : app.tab === "day" ? app.date : "";
  $("navName").textContent = label;
  $("prev").hidden = $("next").hidden = $("todayBtn").hidden = app.tab === "agenda";
}

function step(n) {
  if (app.tab === "month") app.month = monthShift(app.month, n);
  else if (app.tab === "day") app.date = addDays(app.date, n);
  render();
}

export function go(tab, opts = {}) {
  app.tab = VIEWS[tab] ? tab : "month";
  if (opts.date) {
    app.date = opts.date;
    app.month = opts.date.slice(0, 7);
  }
  try { history.replaceState(null, "", `#${app.tab}`); } catch { /* 不重要 */ }
  renderTabs();
  render();
  window.scrollTo({ top: 0 });
}
app.go = go;

let rendering = 0;
export async function render() {
  const mine = ++rendering;
  renderNav();
  try {
    const node = await VIEWS[app.tab].render(app);
    if (mine === rendering) fill($("view"), node);
  } catch (e) {
    if (mine === rendering) fill($("view"), h("div", { class: "card" }, h("p", null, "讀不到資料："), h("p", { class: "muted" }, errorText(e))));
  }
}
app.render = render;

// ---------------------------------------------------------------- 提示、對話框

let toastTimer;
export function toast(message) {
  const t = $("toast");
  fill(t, h("span", null, message));
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3000);
}
app.toast = toast;

app.openModal = ({ title, body = [], foot = [] }) => {
  $("modalTitle").textContent = title;
  fill($("modalBody"), body);
  fill($("modalFoot"), foot, h("button", { type: "button", class: "btn", onclick: app.closeModal }, "關閉"));
  $("modal").hidden = false;
};
app.closeModal = () => { $("modal").hidden = true; fill($("modalBody")); };
$("modal").addEventListener("click", (ev) => { if (ev.target.id === "modal") app.closeModal(); });
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") app.closeModal(); });

// 問一句「確定嗎」→ true／false（按「關閉」或點外面都算 false）
app.confirm = (title, message, okLabel = "確定") => new Promise((resolve) => {
  const ok = h("button", { type: "button", class: "btn danger" }, okLabel);
  ok.addEventListener("click", () => { app.closeModal(); resolve(true); });
  app.openModal({ title, body: [h("p", null, message)], foot: [ok] });
  const cancel = () => resolve(false);                 // 已經回答過的 resolve 再呼叫不會有影響
  $("modal").addEventListener("click", cancel, { once: true });
  document.addEventListener("keydown", cancel, { once: true });
  $("modalFoot").lastChild.addEventListener("click", cancel, { once: true });
});

// 寫入之後：重畫並提示
app.afterWrite = async (message) => {
  await render();
  if (message) toast(message);
};

// ---------------------------------------------------------------- 回到這個分頁：聊天裡加的行程就更新（不輪詢）

let checking = false;
async function refreshIfChanged() {
  if (checking || document.visibilityState !== "visible" || !app.today) return;
  checking = true;
  try {
    const t = await read("today");
    const stamp = JSON.stringify([t.today, t.agenda.map((d) => d.items.map((c) => c.id + c.start + c.status)),
      t.tasks.tasks.map((x) => `${x.id}${x.done}`)]);
    if (app.stamp && stamp !== app.stamp) { await render(); toast("行事曆有新的變動"); }
    app.stamp = stamp;
  } catch { /* 下次回來再試 */ } finally { checking = false; }
}

// ---------------------------------------------------------------- 開場

$("prev").addEventListener("click", () => step(-1));
$("next").addEventListener("click", () => step(1));
$("todayBtn").addEventListener("click", () => { app.month = app.today.slice(0, 7); app.date = app.today; render(); });
// 匯出 .ics：檔案沒有加密，先問一次再交給 SYNOX 下載（SYNOX 自己也會再確認一次）
$("exportBtn").addEventListener("click", async () => {
  const yes = await app.confirm("匯出 .ics？",
    "匯出的 .ics 檔沒有加密：存到哪裡、誰拿到都看得到你所有的行程。要匯出嗎？", "匯出");
  if (!yes) return;
  try { await download("ics"); } catch (e) { toast(errorText(e)); }
});
$("addBtn").addEventListener("click", () => eventForm(app, { date: app.tab === "day" ? app.date : app.today }));

async function boot() {
  try {
    const t = await read("today");
    app.today = app.date = t.today;
    app.month = t.today.slice(0, 7);
  } catch (e) {
    fill($("view"), h("div", { class: "card" }, errorText(e)));
    return;
  }
  const hash = (location.hash || "").slice(1);
  if (VIEWS[hash]) app.tab = hash;
  renderTabs();
  await render();
  document.addEventListener("visibilitychange", refreshIfChanged);
  refreshIfChanged();
}

if (globalThis.SynoxApp) globalThis.SynoxApp.ready(() => { boot(); });
