// 規劃頁與設定頁共用的表單小零件：欄位、選單、數字欄、存（不會連點送兩次）。
import { api, errorText } from "./api.js";
import { h, major, toMinor } from "./dom.js";
import { infoButton } from "./info.js";

export const field = (label, input) => h("label", { class: "field" }, label, input);
export const select = (pairs, value) => h("select", null, pairs.map(([v, t]) => h("option", { value: v, selected: v === value }, t)));
export const numInput = (value, placeholder = "") => h("input", { inputmode: "numeric", value: value ?? "", placeholder });
// 金額欄：value 是最小單位，框裡顯示照帳本小數位數的數字（dom.js 的 major）；讀回來用 toMoney／moneyOrNull
export const money = (value, label) => h("input", { inputmode: "decimal", value: value ? major(value) : "", placeholder: "0", "aria-label": label });
export const moneyInput = (value, placeholder = "") => h("input", { inputmode: "decimal", value: major(value ?? ""), placeholder });

// 卡片標題＋ⓘ（每一張卡一句白話說它在做什麼）
export const cardHead = (app, title, info) => h("div", { class: "row" }, h("h2", null, title), infoButton(app, info[0], info[1]));

export function intOrNull(input, label) {
  const t = String(input.value || "").replace(/[,\s元]/g, "");
  if (t === "") return null;
  if (!/^\d+$/.test(t)) throw new Error(`${label}要是整數`);
  return Number(t);
}

// 金額欄 → 最小單位的整數；空白回 null；看不懂丟錯（訊息帶欄位名）
export function moneyOrNull(input, label) {
  if (String(input.value || "").replace(/[,\s元]/g, "") === "") return null;
  const n = toMinor(input.value);
  if (Number.isNaN(n)) throw new Error(`${label}要是數字`);
  return n;
}

// 金額字 → 最小單位的整數；空白回 0；看不懂回 NaN
export function toMoney(text) {
  const t = String(text ?? "").replace(/[,\s元]/g, "");
  return t === "" ? 0 : toMinor(t);
}

export function toInt(text) {
  const t = String(text ?? "").replace(/[,\s元]/g, "");
  if (t === "") return 0;
  return /^\d+$/.test(t) ? Number(t) : NaN;
}

let saving = false;                                       // 存的時候再按一次（連點）不會送第二次：第二次會變成「重複」或多記一筆

// 送出一個寫入、關掉視窗、給「復原」。名字刻意叫 send：卡片裡常有自己的「存」，不要同名蓋掉它
// （以前卡片自己的 save 蓋掉過這個，「＋ 加一格」「✕ 停用」按了變成存計畫）。
// message 也可以是函式（拿到回應才知道要講什麼，例：記交易時的違規警告要跟「復原」放在同一則提示）。
export async function send(app, request, message) {
  if (saving) return;
  saving = true;
  try {
    const r = await request();
    app.closeModal();
    const text = typeof message === "function" ? message(r) : message;
    await app.afterWrite(r, r.batch_id ? text : "沒有改到東西", r.batch_id);
  } catch (e) { app.toast(errorText(e)); } finally { saving = false; }
}

// 一個開關（🔒、↻）：送 PATCH、給復原
export async function toggle(app, path, body, message) {
  try {
    const r = await api(path, { method: "PATCH", body });
    await app.afterWrite(r, message(r), r.batch_id);
  } catch (e) { app.toast(errorText(e)); }
}
