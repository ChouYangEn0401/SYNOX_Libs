// 原本畫面打的每一條 /api/… → SYNOX 工具服務的「讀」「動作」「匯出」（契約檔：W0／W2 以外不改，要改在回報寫清楚）。
//
// 每一列：
//   method, path   原本的呼叫；路徑裡的 :名字 會變成同名的欄位（多半是 :id）
//   read           GET 的讀；query 參數照原樣送（true → "true"；false、null、空字串不送）
//   act            動作的名字；body 的每個鍵就是欄位名（值怎麼轉成字串見 api.js 的 toValues）
//   pick           act 有好幾個時，照 body 選哪一個（api.js 的 PICKS）
//   fixed          另外固定帶的欄位
//   rename         body 的鍵要換名字時：{原本的鍵: 欄位名}
//   download       匯出的名字（SynoxApp.download，先提醒沒有加密）
//   none           這一版畫面做不到：直接回這一句
// 比對照順序：寫在前面的先比（/api/dedup/scan 要在 /api/dedup/:id 前面）。

export const ROUTES = [
  // 記帳主線、明細（W3）
  { method: "GET", path: "/api/meta", read: "meta" },
  { method: "GET", path: "/api/ref", read: "ref" },
  { method: "GET", path: "/api/guess", read: "guess" },
  { method: "GET", path: "/api/items", read: "items" },
  { method: "GET", path: "/api/passbook", read: "passbook" },
  { method: "GET", path: "/api/inbox", read: "inbox" },
  { method: "GET", path: "/api/batches", read: "batches" },
  { method: "GET", path: "/api/txn/:id", read: "txn" },
  { method: "POST", path: "/api/txn", act: ["add", "in", "transfer", "iou"], pick: "txn" },
  { method: "PATCH", path: "/api/txn/:id", act: "edit" },
  { method: "DELETE", path: "/api/txn/:id", act: "rm" },
  { method: "POST", path: "/api/batch/undo", act: "undo" },
  { method: "POST", path: "/api/batch/:id/revert", act: "revert" },
  { method: "POST", path: "/api/note", act: "note" },
  { method: "POST", path: "/api/reclassify", act: "reclassify" },
  { method: "POST", path: "/api/dedup/scan", act: "dedup" },
  { method: "POST", path: "/api/dedup/:id", act: "dedup" },

  // 總覽（W4）
  { method: "GET", path: "/api/home", read: "home" },
  { method: "GET", path: "/api/year", read: "year" },
  { method: "GET", path: "/api/allowance", read: "allowance" },
  { method: "GET", path: "/api/conflicts", read: "conflicts" },
  { method: "GET", path: "/api/moves", read: "moves" },
  { method: "POST", path: "/api/moves", act: "move" },
  { method: "DELETE", path: "/api/moves/:undo", act: "move" },
  { method: "POST", path: "/api/bucket/:id/skip", act: "bucket_skip" },
  { method: "POST", path: "/api/bucket/:id/setaside", act: "bucket_setaside" },
  { method: "POST", path: "/api/bucket/:id/fold", act: "bucket_fold" },
  { method: "POST", path: "/api/recurring/post", act: "fixed_post" },
  { method: "POST", path: "/api/envelope/:id/do", act: "envelope_do" },

  // 規劃、設定、第一次使用、帳本（W5）
  { method: "GET", path: "/api/setup", read: "setup" },
  { method: "GET", path: "/api/features", read: "features" },
  { method: "GET", path: "/api/book", read: "book_info" },
  { method: "PUT", path: "/api/plan", act: "plan" },
  { method: "PATCH", path: "/api/plan/groups", act: "plan_names" },
  { method: "POST", path: "/api/bucket", act: "bucket" },
  { method: "PATCH", path: "/api/bucket/:id", act: "bucket" },
  { method: "POST", path: "/api/recurring", act: "fixed" },
  { method: "PATCH", path: "/api/recurring/:id", act: "fixed" },
  { method: "POST", path: "/api/group", act: "group" },
  { method: "PATCH", path: "/api/group/:id", act: "group" },
  { method: "POST", path: "/api/category", act: "cat_add" },
  { method: "PATCH", path: "/api/category/:id", act: "cat_add" },
  { method: "POST", path: "/api/account", act: "account" },
  { method: "PATCH", path: "/api/account/:id", act: "account" },
  { method: "POST", path: "/api/setting", act: "setting" },
  { method: "POST", path: "/api/feature", act: "feature_on" },
  { method: "POST", path: "/api/init", act: "init" },
  { method: "POST", path: "/api/book/new", act: "book_new" },
  { method: "GET", path: "/api/book/export", download: "book" },
  { method: "POST", path: "/api/book/import", none: "這一版還不能從畫面匯入整本帳：把檔放進自己的檔案資料夾，再用指令匯入。" },
  { method: "GET", path: "/api/entries.csv", download: "entries_csv" },

  // 檢討（W7）
  { method: "GET", path: "/api/reviews", read: "reviews" },
  { method: "GET", path: "/api/review/:id", read: "review" },
  { method: "POST", path: "/api/review/:id/apply", act: "review_apply" },
  { method: "POST", path: "/api/review/:id/reply", act: "review_reply" },
  { method: "POST", path: "/api/review", act: "review_save" },

  // 撲滿、對明細（W8）
  { method: "GET", path: "/api/piggy", read: "piggy" },
  { method: "GET", path: "/api/piggy/trades", read: "piggy_trades" },
  { method: "GET", path: "/api/piggy/positions", read: "piggy_positions" },
  { method: "GET", path: "/api/piggy/book/:id", read: "piggy_book" },
  { method: "GET", path: "/api/piggy/compare", read: "piggy_compare" },
  { method: "GET", path: "/api/books", read: "books" },
  { method: "GET", path: "/api/quotes/needed", read: "quotes_needed" },
  { method: "GET", path: "/api/quotes", read: "quotes" },
  { method: "GET", path: "/api/reconcile", read: "reconcile" },
  { method: "POST", path: "/api/trade", act: "trade_add" },
  { method: "DELETE", path: "/api/trade/:id", act: "trade_rm" },
  { method: "POST", path: "/api/books", act: "pbook" },
  { method: "PATCH", path: "/api/books/:id", act: "pbook" },
  { method: "POST", path: "/api/books/:id/tithe", act: "pbook", rename: { rate: "tithe_rate" } },
  { method: "POST", path: "/api/books/:id/close", act: "pbook_close", fixed: { do: "close" } },
  { method: "POST", path: "/api/books/:id/reopen", act: "pbook_close", fixed: { do: "reopen" } },
  { method: "POST", path: "/api/books/:id/archive", act: "pbook_close", fixed: { do: "archive" } },
  { method: "POST", path: "/api/books/:id/unarchive", act: "pbook_close", fixed: { do: "unarchive" } },
  { method: "POST", path: "/api/piggy/hide", act: "piggy_hide" },
  { method: "PATCH", path: "/api/piggy/settings", act: "piggy_set" },
  { method: "POST", path: "/api/reconcile/count", act: "count" },
  { method: "POST", path: "/api/quotes", act: "price_push" },
];

// 一條路徑樣式 → 正規式與參數名（:id → ([^/]+)）
function compile(path) {
  const names = [];
  const source = path.split("/").map((part) => {
    if (part.startsWith(":")) { names.push(part.slice(1)); return "([^/]+)"; }
    return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }).join("/");
  return { re: new RegExp(`^${source}$`), names };
}

const COMPILED = ROUTES.map((route) => ({ route, ...compile(route.path) }));

// (方法, 路徑) → { route, args: {參數名: 值} }；對不到回 null。路徑上的 ?query 不算。
export function match(method, path) {
  const bare = String(path).split("?")[0];
  const m = String(method || "GET").toUpperCase();
  for (const c of COMPILED) {
    if (c.route.method !== m) continue;
    const hit = c.re.exec(bare);
    if (!hit) continue;
    const args = {};
    c.names.forEach((name, i) => { args[name] = decodeURIComponent(hit[i + 1]); });
    return { route: c.route, args };
  }
  return null;
}

// 這張表會叫到的所有名字（測試用：每一個都要在 pack.json 宣告過）
export function names() {
  const out = { reads: [], actions: [], exports: [] };
  for (const r of ROUTES) {
    if (r.read) out.reads.push(r.read);
    for (const a of [].concat(r.act || [])) out.actions.push(a);
    if (r.download) out.exports.push(r.download);
  }
  return out;
}
