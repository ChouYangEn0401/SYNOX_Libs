// 轉接器：只叫 SYNOX 的共用元件（SynoxApp.read／act／download），不自己連線。
// 錯誤一律丟 ApiError（message 是給人看的那一句）。

export class ApiError extends Error {
  constructor(err) {
    super(err.message || "發生錯誤");
    this.code = err.code;
    this.fields = err.fields || {};
  }
}

function kit() {
  const k = globalThis.SynoxApp;
  if (!k) throw new ApiError({ code: "network", message: "沒有從 SYNOX 打開這個畫面。回到 SYNOX 的工具頁再按一次「打開」" });
  return k;
}

function fromKit(e) {
  if (e instanceof ApiError) return e;
  const err = e || {};
  if (err.code === "network") return new ApiError({ code: "network", message: "連不到 SYNOX。SYNOX 可能關掉了，回到 SYNOX 再打開一次" });
  if (err.code === "login") return new ApiError({ code: "login", message: err.error || "請從 SYNOX 再打開一次" });
  if (err.code === "cancelled") return new ApiError({ code: "cancelled", message: "取消了" });
  return new ApiError({ code: err.code || "error", message: err.error || err.message || "發生錯誤", fields: err.fields });
}

// 讀的參數：undefined、null、空字串不送
function clean(values) {
  const out = {};
  for (const [k, v] of Object.entries(values || {})) {
    if (v === undefined || v === null || v === "") continue;
    out[k] = v === true ? "yes" : v === false ? "no" : String(v);
  }
  return out;
}

export async function read(name, params) {
  try { return await kit().read(name, clean(params)); } catch (e) { throw fromKit(e); }
}

export async function act(name, values) {
  try { return await kit().act(name, clean(values), { quiet: true }); } catch (e) { throw fromKit(e); }
}

export async function download(name, params) {
  try { return await kit().download(name, clean(params)); } catch (e) { throw fromKit(e); }
}

export const errorText = (e) => (e && e.message) || "發生錯誤";
