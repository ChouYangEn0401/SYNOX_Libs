// 記帳本的進入點（契約檔）：六個分頁（總覽、明細、撲滿、檢討、規劃、設定）、期別切換、提示與「復原」、對話框。
// 從原本的畫面原樣搬來，改了三件事：
//   1. 等 SYNOX 的共用元件進門（SynoxApp.ready）才開始；
//   2. 不輪詢：回到這個分頁（visibilitychange）時讀一次 meta，寫入序號變了才重畫；
//   3. app.hideTab(名字)／app.showTab(名字)：meta 回 piggy=false 時不顯示撲滿分頁。
// 可能重複的帳不再一打開就跳出來問：放在總覽「待解決」最上面一列（W4）。
import { api, errorText } from "./api.js";
import { fill, h, setDecimals } from "./dom.js";
import * as home from "./views/home.js";
import * as book from "./views/book.js";
import * as piggy from "./views/piggy.js";
import * as reviews from "./views/reviews.js";
import * as plan from "./views/plan.js";
import * as setup from "./views/setup.js";

const TABS = [["home", "🏠", "總覽"], ["book", "📒", "明細"], ["piggy", "🐷", "撲滿"],
  ["review", "💬", "檢討"], ["plan", "📋", "規劃"], ["setup", "⚙️", "設定"]];       // 規劃＝數字；設定＝東西長什麼樣
const VIEWS = { home, book, piggy, review: reviews, plan, setup };
const $ = (id) => document.getElementById(id);
const hidden = new Set();                                                          // 不顯示的分頁

export const app = { tab: "home", period: null, current: null, seq: 0, meta: null, nav: null, refCache: null,
  homeMode: "month", year: null };

// ---------------------------------------------------------------- 分頁與期別

function renderTabs() {
  fill($("tabs"), TABS.filter(([id]) => !hidden.has(id)).map(([id, ico, name]) => h("button", {
    type: "button", class: "tab", "aria-current": app.tab === id ? "page" : null, onclick: () => go(id),
  }, h("span", { class: "ico", "aria-hidden": "true" }, ico), name)));
}

app.hideTab = (name) => {
  if (!VIEWS[name] || name === "home" || hidden.has(name)) return;
  hidden.add(name);
  if (app.tab === name) { go("home"); return; }
  renderTabs();
};
app.showTab = (name) => {
  if (!hidden.delete(name)) return;
  renderTabs();
};

// 照 meta 決定要不要顯示撲滿（沒有證券帳戶、也沒開投資選配時 piggy 是 false）
function applyMeta(meta) {
  if (meta && meta.currency) setDecimals(meta.currency.decimals);       // 金額怎麼顯示、怎麼換成最小單位（dom.js）
  if (meta && meta.piggy === false) app.hideTab("piggy");
  else if (meta && meta.piggy === true) app.showTab("piggy");
}

function monthName(key) {
  if (!key) return "…";
  const [y, m] = key.split("-");
  const thisYear = (app.current || key).slice(0, 4);
  return (y === thisYear ? "" : `${y}年 `) + `${Number(m)}月`;
}

const mmdd = (iso) => `${iso.slice(5, 7)}/${iso.slice(8, 10)}`;

// 期別切換：一般是「哪一期」，總覽切到「今年」時改成「哪一年」。
let navHandlers = { prev: null, next: null };
app.setNav = (nav) => {
  app.nav = nav;
  $("pName").textContent = monthName(nav.period.key);
  $("pName").title = `${mmdd(nav.period.start)}～${mmdd(nav.period.end)}`;
  navHandlers = { prev: nav.prev && (() => app.setPeriod(nav.prev)), next: nav.next && (() => app.setPeriod(nav.next)) };
  $("prevP").disabled = !nav.prev;
  $("nextP").disabled = !nav.next;
  $("prevP").hidden = $("nextP").hidden = false;
};
app.setYearNav = (y) => {
  $("pName").textContent = `${y.year} 年`;
  $("pName").title = "";
  navHandlers = { prev: y.prev && (() => { app.year = y.prev; render(); }), next: y.next && (() => { app.year = y.next; render(); }) };
  $("prevP").disabled = !y.prev;
  $("nextP").disabled = !y.next;
  $("prevP").hidden = $("nextP").hidden = false;
};
app.hideNav = () => {
  $("pName").textContent = "";
  navHandlers = { prev: null, next: null };
  $("prevP").hidden = $("nextP").hidden = true;
};
app.setPeriod = (key) => {
  if (!key || key === app.period) return;
  app.period = key;
  render();
};

export function go(tab) {
  app.tab = VIEWS[tab] && !hidden.has(tab) ? tab : "home";
  try { history.replaceState(null, "", `#${app.tab}`); } catch { /* 不重要 */ }
  renderTabs();
  render();
  window.scrollTo({ top: 0 });
}
app.go = go;

let rendering = 0;
export async function render() {
  const mine = ++rendering;
  try {
    const node = await VIEWS[app.tab].render(app);
    if (mine === rendering) fill($("view"), node);
  } catch (e) {
    if (mine === rendering) fill($("view"), h("div", { class: "card" }, h("p", null, "讀不到資料："), h("p", { class: "muted" }, errorText(e))));
  }
}
app.render = render;

// ---------------------------------------------------------------- 提示、對話框、寫入之後

let toastTimer;
export function toast(message, action) {
  const t = $("toast");
  fill(t, h("span", null, message), action ? h("button", { type: "button", onclick: () => { t.hidden = true; action.run(); } }, action.label) : null);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, action ? 7000 : 3000);
}
app.toast = toast;

app.openModal = ({ title, body = [], foot = [] }) => {
  $("modalTitle").textContent = title;
  fill($("modalBody"), body);
  fill($("modalFoot"), foot, h("button", { type: "button", class: "btn", onclick: app.closeModal }, "關閉"));
  $("modal").hidden = false;
};
app.closeModal = () => { $("modal").hidden = true; fill($("modalBody")); };       // 內容也清掉，不然藏起來的表單還留在頁面上
$("modal").addEventListener("click", (ev) => { if (ev.target.id === "modal") app.closeModal(); });
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") app.closeModal(); });

// 寫入之後：記下序號（回到分頁時才不會以為是別人寫的）、重畫、給「復原」。
app.afterWrite = async (res, message, undoBatch) => {
  if (res && res.write_seq) app.seq = res.write_seq;
  app.refCache = null;
  await render();
  if (message) toast(message, undoBatch ? { label: "復原", run: () => undo(undoBatch) } : null);
};

async function undo(batchId) {
  try {
    const r = await api(`/api/batch/${batchId}/revert`, { method: "POST" });
    await app.afterWrite(r, "復原了");
  } catch (e) { if (e.code !== "cancelled") toast(errorText(e)); }
}

app.ref = async () => {
  if (!app.refCache) app.refCache = await api("/api/ref");
  return app.refCache;
};

// ---------------------------------------------------------------- 回到這個分頁：聊天裡記進來的就更新（不輪詢）

let checking = false;
async function refreshIfChanged() {
  if (checking || document.visibilityState !== "visible" || !app.meta) return;
  checking = true;
  try {
    const m = await api("/api/meta");
    app.meta = m;
    applyMeta(m);
    if (m.write_seq !== app.seq) {
      const seen = app.seq;
      app.seq = m.write_seq;
      if (seen) {
        app.refCache = null;
        await render();
        toast("有新的記帳進來了");
      }
    }
  } catch { /* 下次回來再試 */ } finally { checking = false; }
}

// ---------------------------------------------------------------- 開場

$("prevP").addEventListener("click", () => navHandlers.prev && navHandlers.prev());
$("nextP").addEventListener("click", () => navHandlers.next && navHandlers.next());

async function boot() {
  try {
    app.meta = await api("/api/meta");
  } catch (e) {
    fill($("view"), h("div", { class: "card" }, errorText(e)));
    return;
  }
  app.current = app.period = app.meta.current;
  app.seq = app.meta.write_seq;
  applyMeta(app.meta);
  const hash = (location.hash || "").slice(1);
  if (VIEWS[hash] && !hidden.has(hash)) app.tab = hash;
  if (hash === "year") app.homeMode = "year";            // #year：直接看今年
  renderTabs();
  await render();
  document.addEventListener("visibilitychange", refreshIfChanged);
}

if (globalThis.SynoxApp) globalThis.SynoxApp.ready(() => { boot(); });
