// 轉接器（契約檔）：原本畫面的 api(path, {method, body, params}) 介面照舊，裡面只叫 SYNOX 的共用元件
// （SynoxApp.read／act／download），不自己連線。對照表在 routes.js。
// 錯誤一律丟 ApiError（message 是給人看的那一句；引擎的「是不是：…」與下一步已經接在 message 裡）。
import { match } from "./routes.js";

export class ApiError extends Error {
  constructor(err, status) {
    super(err.message || "發生錯誤");
    this.code = err.code;
    this.hint = err.hint || "";
    this.fields = err.fields || {};
    this.suggestions = err.suggestions || [];
    this.status = status;
  }
}

const CLEAR = "-";                       // 欄位給「-」＝清掉（PATCH 送 null 或空字串時）

// body 的一個值 → 欄位的字串；回 undefined＝不送這個欄位。
//   null／"" → PATCH 是「清掉」（-），其他方法不送；true／false → yes／no；數字 → 字串；
//   清單 → 逗號分開；物件 → JSON 字串（例：計畫的各格金額）。
export function toValue(v, method) {
  if (v === undefined) return undefined;
  if (v === null || v === "") return method === "PATCH" ? CLEAR : undefined;
  if (v === true) return "yes";
  if (v === false) return "no";
  if (Array.isArray(v)) return v.map((x) => String(x)).join(",");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function toValues(route, args, body, params, method) {
  const out = {};
  const rename = route.rename || {};
  const put = (k, v) => {
    const s = toValue(v, method);
    if (s !== undefined) out[rename[k] || k] = s;
  };
  for (const [k, v] of Object.entries(params || {})) put(k, v);
  for (const [k, v] of Object.entries(body || {})) put(k, v);
  for (const [k, v] of Object.entries(args || {})) out[k] = String(v);
  for (const [k, v] of Object.entries(route.fixed || {})) out[k] = String(v);
  return out;
}

// 讀的參數：照原本 api() 的規則（undefined、null、空字串、false 不送）。
export function toParams(params) {
  const out = {};
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    out[k] = String(v);
  }
  return out;
}

// 一個網址上多個動作時怎麼選
const PICKS = {
  txn(body) {
    const b = body || {};
    if (b.kind === "iou") return "iou";
    if (b.to || b.kind === "transfer") return "transfer";
    if (b.kind === "income") return "in";
    return "add";
  },
};

function kit() {
  const k = globalThis.SynoxApp;
  if (!k) throw new ApiError({ code: "network", message: "沒有從 SYNOX 打開這個畫面", hint: "回到 SYNOX 的工具頁再按一次「打開」" }, 0);
  return k;
}

// 共用元件丟出來的錯（{code, error, fields, status}）→ ApiError
function fromKit(e) {
  if (e instanceof ApiError) return e;
  const err = e || {};
  if (err.code === "network") return new ApiError({ code: "network", message: "連不到 SYNOX", hint: "SYNOX 可能關掉了，回到 SYNOX 再打開一次" }, 0);
  if (err.code === "login") return new ApiError({ code: "login", message: err.error || "請從 SYNOX 再打開一次" }, 401);
  if (err.code === "cancelled") return new ApiError({ code: "cancelled", message: "取消了" }, 0);
  return new ApiError({ code: err.code || "error", message: err.error || err.message || "發生錯誤", fields: err.fields || {} },
    err.status || 0);
}

export async function api(path, { method = "GET", body, params, raw } = {}) {
  const m = String(method).toUpperCase();
  const hit = match(m, path);
  if (!hit) throw new ApiError({ code: "not_found", message: "這個功能這一版還沒有" }, 404);
  const { route, args } = hit;
  if (route.none) throw new ApiError({ code: "unsupported", message: route.none }, 501);
  if (raw) throw new ApiError({ code: "unsupported", message: "這一版還不能從畫面上傳檔案" }, 501);
  try {
    if (route.read) return await kit().read(route.read, { ...toParams(params), ...args });
    if (route.download) {
      const ok = await kit().download(route.download, { ...toParams(params), ...args });
      return { ok: Boolean(ok) };
    }
    const name = route.pick ? PICKS[route.pick](body) : route.act;
    return await kit().act(name, toValues(route, args, body, params, m), { quiet: true });
  } catch (e) {
    throw fromKit(e);
  }
}

// 下載（匯出）：畫面上原本是 <a href="/api/book/export">，這一版要改成按鈕叫這個。
export function download(name, params) {
  return kit().download(name, params || {});
}

export function errorText(e) {
  const parts = [e.message];
  if (e.suggestions && e.suggestions.length) parts.push(`是不是：${e.suggestions.join("、")}`);
  if (e.hint) parts.push(e.hint);
  return parts.join("　");
}
