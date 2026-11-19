// 建 DOM 的小工具：一律用 textContent／setAttribute，絕不把資料拼成 HTML 字串（專案名稱、備註可能是任何字）。
// style 一律給物件（SYNOX 畫面的 CSP 不准 style="…" 屬性）。

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === "") continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value" || k === "checked") el[k] = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

// replaceChildren(null) 會插入字串 "null"：一律經這裡，空值先濾掉。
export function fill(el, ...children) {
  el.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false && c !== ""));
  return el;
}

// 本地時間的 YYYY-MM-DD（不能用 toISOString：它是 UTC，在 UTC+8 的凌晨會算成昨天，「兩週後」就變 13 天後）
export function isoLocal(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
