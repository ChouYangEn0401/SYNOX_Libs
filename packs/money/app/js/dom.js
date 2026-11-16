// 建 DOM 的小工具：一律用 textContent／setAttribute，絕不把資料拼成 HTML 字串（記帳內容可能是任何字）。
// style 一律給物件（走 el.style，SYNOX 畫面的 CSP 不准 style="…" 屬性）。
// SVGNS 是 SVG 的命名空間字串（只是名字，不會連線）。
const SVGNS = "http://www.w3.org/2000/svg";

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === "") continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

function setAttrs(el, attrs, isSvg) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (!isSvg && (k === "value" || k === "checked" || k === "selected")) el[k] = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
}

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  setAttrs(el, attrs, false);
  append(el, children);
  return el;
}

// replaceChildren(null) 會插入字串 "null"：一律經這裡，空值先濾掉。
export function fill(el, ...children) {
  el.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false && c !== ""));
  return el;
}

export function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVGNS, tag);
  setAttrs(el, attrs, true);
  append(el, children);
  return el;
}

// ---- 金額：帳本裡一律是最小單位的整數；小數位數照 meta.currency.decimals（main.js 開場與回到分頁時 setDecimals）。
// 顯示：num(最小單位) → 「1,234」或「12.34」；輸入：toMinor(人打的字) → 最小單位的整數（看不懂回 NaN）；
// 放回輸入框：major(最小單位) → 「12.34」。小數位數 0 時三個都跟以前完全一樣。
let places = 0;
export function setDecimals(d) {
  const n = Number(d);
  places = Number.isInteger(n) && n >= 0 && n <= 3 ? n : 0;
}
export const decimals = () => places;

// 不是金額的數字（筆數、天數、股數）：千分位、不換算
export const count = (n) => Math.round(n ?? 0).toLocaleString("en-US");

export const num = (n) => {
  if (!places) return Math.round(n ?? 0).toLocaleString("en-US");
  return ((n ?? 0) / 10 ** places).toLocaleString("en-US", { minimumFractionDigits: places, maximumFractionDigits: places });
};

export function toMinor(text) {
  const t = String(text ?? "").replace(/[,\s元]/g, "");
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(t);
  if (!m) return NaN;
  const frac = (m[3] || "").replace(/0+$/, "");
  if (frac.length > places) return NaN;
  const v = Number(m[2]) * 10 ** places + (places ? Number(frac.padEnd(places, "0")) : 0);
  return m[1] ? -v : v;
}

export function major(n) {
  if (n === null || n === undefined || n === "") return "";
  if (!places) return String(n);
  const v = Number(n);
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  const whole = Math.floor(a / 10 ** places);
  const frac = String(a % 10 ** places).padStart(places, "0").replace(/0+$/, "");
  return sign + String(whole) + (frac ? `.${frac}` : "");
}
export const md = (iso) => `${iso.slice(5, 7)}/${iso.slice(8, 10)}`;
