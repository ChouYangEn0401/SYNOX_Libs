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

export const $ = (id) => document.getElementById(id);
export const mmdd = (iso) => (iso ? `${iso.slice(5, 7)}/${iso.slice(8, 10)}` : "");
