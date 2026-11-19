// 進入點：兩個分頁（習慣、旅程）、提示、對話框。等 SYNOX 的共用元件進門（SynoxApp.ready）才開始。
import { fill, h, $ } from "./dom.js";
import * as habits from "./habits.js";
import * as journeys from "./journeys.js";

const TABS = [["habits", "🎯", "習慣", habits], ["journeys", "🗺️", "旅程", journeys]];
export const app = { tab: "habits" };

let toastTimer = null;
export function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

export function closeModal() { $("modal").hidden = true; }

// 對話框：buttons = [{label, cls, onclick}]；onclick 回 false＝不關
export function openModal(title, body, buttons) {
  $("modalTitle").textContent = title;
  fill($("modalBody"), body);
  fill($("modalFoot"), (buttons || []).map((b) => h("button", {
    type: "button", class: "btn " + (b.cls || ""),
    onclick: async () => { if (b.onclick && (await b.onclick()) === false) return; closeModal(); },
  }, b.label)));
  $("modal").hidden = false;
}

// 問一句，回 true／false
export function ask(title, text, yes = "確定") {
  return new Promise((resolve) => {
    openModal(title, h("p", null, text), [
      { label: "取消", cls: "ghost", onclick: () => resolve(false) },
      { label: yes, cls: "primary", onclick: () => resolve(true) },
    ]);
  });
}

// 動作失敗時的統一處理：提示那句話，回 null
export async function guard(fn) {
  try { return await fn(); } catch (e) { toast(e.message || "發生錯誤"); return null; }
}

function renderTabs() {
  fill($("tabs"), TABS.map(([id, ico, name]) => h("button", {
    type: "button", class: "tab", "aria-current": app.tab === id ? "page" : null,
    onclick: () => go(id),
  }, h("span", { "aria-hidden": "true" }, ico + " "), name)));
}

export async function go(tab, arg) {
  app.tab = tab || app.tab;
  renderTabs();
  const view = TABS.find(([id]) => id === app.tab)[3];
  fill($("view"), h("p", { class: "muted" }, "載入中…"));
  try { await view.render($("view"), arg); } catch (e) { fill($("view"), h("p", { class: "muted" }, e.message || "讀不到資料")); }
}

if (globalThis.SynoxApp) globalThis.SynoxApp.ready(() => go("habits"));
else go("habits");
