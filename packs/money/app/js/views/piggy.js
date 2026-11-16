// 撲滿 🐷：存下來的錢長怎樣（W8；從原本的撲滿分頁搬來，只改了通用版要改的地方）。
// 第一層＝四層帳戶水平：現金帳戶（分到最後剩下的）／投資商品（目前已有）／資金操盤（現值）／產。
// 點投資或操盤進去：［← 回撲滿］總覽｜部位狀況｜明細｜檢討｜設定。明細就是交易明細（不碰錢包），一頁 50 筆、按「更多」再拿。
// 以倉為中心：總覽只列每一倉的成績，收掉的倉摺起來；點一倉（piggyTab＝book）才看它的部位、明細、個股 vs 基準。
// 操盤的每一頁上面都有「操盤三條規矩」：停止了就整片變紅，提醒自己守紀律。
// 價格不自動抓：按「查詢當下價格」才去看有沒有人塞進來（動作 price_push）。
// 通用版改的地方：基準不寫死任何代號（名字從設定來，沒取就寫「基準」）；捐贈（倉有賺時的比例）只在開了「捐贈」選配時出現；
// 沒有任何券商或銀行的名字；沒有證券帳戶、也沒開投資時 main.js 根本不顯示這個分頁（meta.piggy）。
import { api, errorText } from "../api.js";
import { fill, h, md, num, s } from "../dom.js";
import { send } from "../forms.js";
import { reconcileCard } from "./reconcile.js";

const ICON = { cash: "💵", invest: "📈", trading: "🎯", property: "🏠" };
const HINT = {
  cash: "分完、要留的扣掉，剩下的",
  invest: "以年為期，跟基準比",
  trading: "停損額，打完就沒了",
  property: "動產、不動產、黃金",
};
const KIND = { cash: "現金", bank: "銀行", credit_card: "信用卡", securities: "證券", e_wallet: "電子錢包", receivable: "代墊", loan: "貸款", pension: "退休金" };
const price2 = (v) => (v === null || v === undefined ? "—" : String(Math.round(v * 100) / 100));
const BIG_LABEL = { cash: "分到最後剩下的", invest: "目前已有", trading: "現值", property: "合計" };
const TRADE_TABS = [["home", "總覽"], ["positions", "部位狀況"], ["trades", "明細"], ["review", "檢討"], ["setup", "設定"]];
const PLAIN_TABS = [["home", "總覽"], ["setup", "設定"]];
const ACTIONS = [["buy", "買進"], ["sell", "賣出"], ["dividend", "股利"], ["fee", "費用"], ["other", "其他"]];
const PAGE = 50;
const BENCH_WORD = "基準";

const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(v * 1000) / 10}%`);
const ymd = (iso) => (iso ? iso.slice(0, 10).replace(/-/g, "/") : "");      // 年/月/日（明細要看得到年份）
const held = (days) => (days >= 365 ? `${(days / 365).toFixed(1)}年` : `${num(days)}天`);   // 一年以上寫「4.8 年」，不然欄位放不下
const tagOf = (b) => h("i", { class: `tag k-${b.kind} st-${b.status}` }, `${b.kind_name}・${b.status_name}`);
const signed = (n) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${num(Math.abs(n))}`;
const f = (label, input, cls = "") => h("label", { class: `f ${cls}`.trim() }, label, input);
const benchOf = (b) => (b && b.symbol) || BENCH_WORD;                     // 基準叫什麼：設定裡取的名字，沒取就「基準」
const giving = (app) => app.piggyGiving !== false;                         // 開了「捐贈」選配沒有（讀 piggy 時記下）

export async function render(app) {
  const level = app.piggyLevel || null;
  const d = await api("/api/piggy", { params: { period: app.period, level } });
  app.piggyGiving = d.giving !== false;
  app.setNav(d);
  if (!d.level) { app.piggyLevel = null; return top(app, d); }
  let book = null;
  if (d.layer && app.piggyTab === "book" && app.piggyBook) {
    try { book = await api(`/api/piggy/book/${app.piggyBook}`); } catch { book = null; }
    if (!book || book.book.layer !== d.level.key) { book = null; app.piggyTab = "home"; }
  }
  return focused(app, d, book);
}

function openBook(app, b) {
  app.piggyBook = b.id;
  app.piggyTab = "book";
  app.render();
  window.scrollTo({ top: 0 });
}

// ---------------------------------------------------------------- 第一層：四層帳戶水平

function top(app, d) {
  return h("div", { class: "page-inner" },
    pendingCard(app, d.pending),
    h("section", { class: "card" },
      h("h2", null, "帳戶水平"),
      h("div", { class: "ledger" }, d.levels.map((lv) => levelRow(app, lv))),
      h("p", { class: "muted small" }, "點一層進去。帳戶歸哪一層在「設定」的帳戶改。")),
    d.legacy_invest ? legacyCard(d.legacy_invest) : null);
}

function levelRow(app, lv) {
  const go = () => { app.piggyLevel = lv.key; app.piggyTab = "home"; app.render(); };
  const empty = !lv.count;
  return h("button", { type: "button", class: "entry nodate", onclick: go },
    h("span", { class: "what" },
      h("b", null, `${ICON[lv.key] || "•"} ${lv.name}`),
      h("small", null, empty ? `還沒有帳戶・${HINT[lv.key]}` : HINT[lv.key])),
    h("span", { class: "money" },
      h("span", { class: `amt ${lv.big < 0 ? "bad" : ""}` }, empty ? "—" : num(lv.big)),
      h("span", { class: "bal" }, lv.unverified && lv.key !== "cash" ? `${lv.unverified} 個沒核對` : "")));
}

// 撲滿待處理：捐贈（改比例、✓；沒開捐贈選配時引擎不會列）、到期該收的倉。總覽的撲滿卡也用同一份（home.js 匯入 pendingList）。
export function pendingList(app, list) {
  if (!list || !list.length) return null;
  return h("div", { class: "ledger" }, list.map((p) => (p.kind === "tithe" ? givingRow(app, p) : closeRow(app, p))));
}

function pendingCard(app, list) {
  if (!list || !list.length) return null;
  return h("section", { class: "card" }, h("h2", null, "撲滿待處理"), pendingList(app, list));
}

function givingRow(app, p) {
  return h("div", { class: "entry nodate" },
    h("span", { class: "what" }, h("b", null, `捐贈：${p.book}`),
      h("small", null, `賺 ${num(p.result)} × ${pct(p.rate)}・收倉 ${md(p.closed_at)}`)),
    h("span", { class: "money row" },
      h("b", { class: "num" }, num(p.amount)),
      h("button", { type: "button", class: "btn small", onclick: () => titheRate(app, p.book_id, p.rate) }, "比例"),
      h("button", { type: "button", class: "btn small primary", onclick: () => titheDone(app, p.book_id, true) }, "✓ 做完了")));
}

function closeRow(app, p) {
  return h("div", { class: "entry nodate" },
    h("span", { class: "what" }, h("b", null, `該收倉了：${p.book}`),
      h("small", null, `${md(p.end_date)} 結束。收倉會把結果寫死${giving(app) ? "、算捐贈" : ""}。`)),
    h("span", { class: "money" },
      h("button", { type: "button", class: "btn small primary", onclick: () => bookAction(app, p.book_id, "close", p.book) }, "收倉")));
}

// 名字沿用（別的分頁 import）：titheDone／titheRate＝捐贈打勾、改比例。
export async function titheDone(app, bookId, done) {
  await send(app, () => api(`/api/books/${bookId}/tithe`, { method: "POST", body: { done } }), done ? "捐贈做完了 ✓" : "取消打勾了");
}

export function titheRate(app, bookId, rate) {
  const input = h("input", { inputmode: "decimal", value: String(Math.round((rate || 0.1) * 1000) / 10), "aria-label": "捐贈比例（%）" });
  app.openModal({
    title: "捐贈的比例",
    body: [h("p", { class: "muted small" }, "這一倉賺的錢要捐多少 %。改了金額會跟著算。"), f("比例（%）", input)],
    foot: [h("button", { type: "button", class: "btn primary", onclick: () => send(app,
      () => api(`/api/books/${bookId}/tithe`, { method: "POST", body: { rate: `${input.value}%` } }), "比例改好了") }, "存")],
  });
}

async function bookAction(app, bookId, action, name) {
  const words = { close: "收倉", reopen: "重新打開", archive: "封存", unarchive: "拿回來" };
  await send(app, () => api(`/api/books/${bookId}/${action}`, { method: "POST" }), `${name}：${words[action]}了`);
}

function legacyCard(v) {
  return h("section", { class: "card" }, h("h2", null, "舊的每月損益"),
    h("p", { class: "muted small" }, `${v.count} 個月累計 ${signed(v.total)}。`));
}

// ---------------------------------------------------------------- 點進一層

function focused(app, d, book = null) {
  const lv = d.level;
  const trade = Boolean(d.layer);
  const tabs = trade ? TRADE_TABS : PLAIN_TABS;
  let tab = app.piggyTab || "home";
  if (!tabs.some(([id]) => id === tab) && !(tab === "book" && book)) tab = "home";
  const nav = h("div", { class: "seg piggy-nav", role: "group", "aria-label": `${lv.name}裡面` },
    h("button", { type: "button", class: "back", onclick: () => { app.piggyLevel = null; app.render(); } }, "← 回撲滿"),
    tabs.map(([id, name]) => h("button", {
      type: "button", "aria-pressed": tab === id ? "true" : "false",
      onclick: () => { app.piggyTab = id; app.render(); },
    }, name)));
  const body = lv.key === "cash" ? cashBody(app, d, tab)
    : (trade && tab === "book" ? bookBody(app, d, book) : (trade ? tradeBody(app, d, tab) : plainBody(app, d, tab)));
  return h("div", { class: "page-inner" }, nav, ...body);
}

function hero(lv, big, sub) {
  return h("section", { class: "card hero" },
    h("span", { class: "label" }, `${ICON[lv.key] || "•"} ${lv.name}・${BIG_LABEL[lv.key]}`),
    h("span", { class: `big ${big < 0 ? "bad" : ""}` }, lv.count ? num(big) : "—"),
    sub ? h("span", { class: "sub" }, sub) : null);
}

function accountsCard(lv) {
  if (!lv.accounts.length) {
    return h("section", { class: "card" }, h("h2", null, "帳戶"),
      h("p", { class: "muted small" }, `還沒有帳戶歸在「${lv.name}」。到「設定」加一個帳戶，或把現有帳戶改到這一層。`));
  }
  return h("section", { class: "card" }, h("h2", null, "帳戶"),
    h("div", { class: "ledger" }, lv.accounts.map((a) => h("div", { class: "entry nodate" },
      h("span", { class: "what" }, h("b", null, a.name), a.purpose ? h("small", null, a.purpose) : null),
      h("span", { class: "money" }, h("span", { class: "amt" }, num(a.balance)),
        h("span", { class: "bal" }, a.verified_on ? `${md(a.verified_on)} 核對` : "用記帳推算的"))))));
}

function layerSetupCard(app, lv) {
  return h("section", { class: "card" }, h("h2", null, "這一層的帳戶"),
    h("p", { class: "muted small" }, `哪些帳戶算「${lv.name}」，在設定頁的帳戶那裡選（每個帳戶一個「算撲滿哪一層」）。`),
    lv.accounts.length ? h("div", { class: "ledger" }, lv.accounts.map((a) => h("div", { class: "entry nodate" },
      h("span", { class: "what" }, h("b", null, a.name), h("small", null, a.purpose || "")),
      h("span", { class: "money" }, h("span", { class: "bal" }, KIND[a.kind] || a.kind))))) : null,
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn", onclick: () => app.go("setup") }, "去設定改帳戶")));
}

// ---------------------------------------------------------------- 現金帳戶：分到最後剩下的

function cashBody(app, d, tab) {
  const lv = d.level;
  const c = d.cash;
  const head = hero(lv, c.leftover, c.unverified ? `${c.unverified} 個帳戶還沒核對，結餘是用記帳推算的` : "結餘扣掉要留的錢之後");
  if (tab === "setup") return [head, layerSetupCard(app, lv)];
  const line = (label, value, cls = "") => h("div", { class: `entry nodate ${cls}` },
    h("span", { class: "what" }, h("b", null, label)), h("span", { class: "money" }, h("span", { class: "amt" }, value)));
  const items = (c.reserved_items || []).filter((i) => i.amount > 0);
  return [head,
    h("section", { class: "card" }, h("h2", null, "怎麼算出來的"),
      h("div", { class: "ledger" },
        line("帳戶結餘", num(c.balance)),
        line("− 還沒做完的用途要留", `−${num(c.reserved)}`),
        items.length ? h("p", { class: "muted small" }, items.map((i) => `${i.name} ${num(i.amount)}`).join("、")) : null,
        line("− 生活費還剩", `−${num(c.living_left)}`),
        // 卡費分兩行寫清楚，才不會跟上面那行「要留的」搞混
        (c.card_items || []).flatMap((k) => [
          k.bill ? line(`− ${k.account}本期信用卡帳單（${md(k.due)} 繳）`, signed(-k.bill)) : null,
          k.next ? line(`− ${k.account}下一期帳單，已刷的（${md(k.closing)} 結帳）`, signed(-k.next)) : null]),
        line("＝ 分到最後剩下的", num(c.leftover), "total"))),
    c.history.length ? h("section", { class: "card" }, h("h2", null, "每一期剩下多少"),
      h("div", { class: "ledger" }, c.history.map((r) => h("div", { class: "entry nodate" },
        h("span", { class: "what" }, h("b", null, r.period_key), h("small", null, `${md(r.recorded_on)} 記的`)),
        h("span", { class: "money" }, h("span", { class: `amt ${r.amount < 0 ? "bad" : ""}` }, num(r.amount)))))),
      h("p", { class: "muted small" }, "每天記一次；這一期過完，最後一次就是這一期的數字。")) : null,
    accountsCard(lv)];
}

function plainBody(app, d, tab) {
  const lv = d.level;
  const head = hero(lv, lv.total, lv.count ? `${lv.count} 個帳戶` : HINT[lv.key]);
  return tab === "setup" ? [head, layerSetupCard(app, lv)] : [head, accountsCard(lv)];
}

// ---------------------------------------------------------------- 投資／資金操盤

function tradeBody(app, d, tab) {
  const lv = d.level;
  const L = d.layer;
  const v = L.value;
  const sub = !lv.count ? HINT[lv.key]
    : v.cash_known ? `持股成本 ${num(v.holding_cost)}・證券戶現金 ${num(v.cash)}`
      : `持股成本 ${num(v.holding_cost)}（證券戶現金還沒核對：對一次證券戶的餘額，或記一筆轉進來的錢）`;
  const head = hero(lv, v.value, sub);
  const rules = lv.key === "trading" ? rulesCard(app, L) : null;
  if (tab === "positions") return [head, rules, reconcileCard(app, "stocks"), positionsCard(app, lv)];
  if (tab === "trades") return [head, rules, tradesCard(app, lv, L)];
  if (tab === "review") return [head, rules, reviewCards(app, lv, L)];
  if (tab === "setup") return [head, rules, booksSetupCard(app, lv, L), piggySettingsCard(app, lv, d), hiddenCard(app, lv, L), layerSetupCard(app, lv)];
  return [head, rules,
    h("section", { class: "card" },
      h("div", { class: "trio" },
        h("div", null, h("span", null, "本月存下"), h("b", null, num(L.month_saved)),
          h("small", { class: "muted" }, L.plan_monthly ? `規劃每月 ${num(L.plan_monthly)}` : "規劃頁還沒填")),
        h("div", null, h("span", null, "在場上"), h("b", null, num(v.holding_cost)), h("small", { class: "muted" }, `${v.open} 檔・用成本算`)),
        h("div", null, h("span", null, lv.key === "trading" ? "倉" : "交易"),
          h("b", null, lv.key === "trading" ? String(L.books.filter((b) => b.status === "open").length) : num(L.trades_count)),
          h("small", { class: "muted" }, lv.key === "trading" ? "開著的" : "筆明細")))),
    booksOverview(app, lv, L),
    benchCard(app, lv, L)];
}

// 每一倉的表現（點一倉進去看細節）；收掉的倉摺起來，只寫一行合計
function booksOverview(app, lv, L) {
  const open = L.books.filter((b) => b.status === "open");
  const closed = L.books.filter((b) => b.status !== "open");
  const total = closed.reduce((a, b) => a + (b.result || 0), 0);
  return h("section", { class: "card" },
    h("div", { class: "row" }, h("h2", null, "倉"), h("span", { class: "muted small" }, `點一倉看它的部位、明細、跟${benchOf(L.bench)}比`)),
    open.length ? h("div", { class: "ledger" }, open.map((b) => bookRow(app, b, { click: true, bench: L.bench })))
      : h("p", { class: "muted small" }, lv.key === "trading"
        ? "還沒有開著的倉。到「設定」開一倉：先寫好操作金額、可虧損、單次可虧損。"
        : "還沒有開著的倉；記一筆投資就會自動開「今年」的倉。"),
    closed.length ? h("details", { class: "closed-books" },
      h("summary", null, `已收掉的 ${closed.length} 倉・加起來 ${signed(total)}`),
      h("div", { class: "ledger" }, closed.map((b) => bookRow(app, b, { click: true, bench: L.bench })))) : null,
    L.archived ? h("p", { class: "muted small" }, `另外有 ${L.archived} 倉封存了（在「設定」）。`) : null);
}

// 點進一倉
function bookBody(app, d, bd) {
  const lv = d.level;
  const L = d.layer;
  const b = bd.book;
  const m = b.bench;
  const name = benchOf(bd.bench);
  const bits = [b.capital ? `操作金額 ${num(b.capital)}` : null, b.in_market ? `在場上 ${num(b.in_market)}（成本）` : null,
    b.max_loss ? `可虧損 ${num(b.max_loss)}，還剩 ${num(b.loss_left)}` : null, b.per_trade_loss ? `單次 ${num(b.per_trade_loss)}` : null,
    `${num(b.trades)} 筆`, b.first ? `${ymd(b.first)}～${ymd(b.last)}` : null].filter(Boolean);
  const head = h("section", { class: `card hero ${b.stopped ? "rules alarm" : ""}` },
    h("span", { class: "label" }, `${ICON[lv.key] || "•"} ${b.name} `, tagOf(b)),
    h("span", { class: `big ${b.result < 0 ? "bad" : ""}` }, b.trades ? signed(b.result) : "—"),
    h("span", { class: "sub" }, bits.join("・")),
    b.stopped ? h("p", { class: "note-line bad" }, `停止中（${ymd(b.stopped_on)} 起）：只能離場，再進場會記成違規。`) : null,
    b.rules_missing ? h("p", { class: "note-line" }, "還沒設可虧損——按下面的「改」寫好再下單。") : null,
    !m && b.derivatives ? h("p", { class: "note-line" }, `期貨、選擇權不跟${name}比：名目金額不是放進去的錢，比了沒有意義。看上面的結果（已經扣掉手續費和稅）就好。`) : null,
    costLine(b.costs),
    m ? h("p", { class: `note-line ${m.beat ? "good" : "bad"}` },
      `實際 ${signed(m.actual_gain)}；同樣的錢放在${name}（年 ${pct(bd.bench.rate)}，比到 ${ymd(m.until)}）是 ${signed(m.shadow_gain)}——`
      + `${m.beat ? "贏" : "輸"} ${num(Math.abs(m.diff))}。`
      + (b.in_market ? "還在場上的股票用成本算（還沒算漲跌），所以放越久越容易看起來輸；到「部位狀況」按「查詢當下價格」看現值。" : "")) : null);
  return [h("div", { class: "actions" }, h("button", { type: "button", class: "btn small", onclick: () => { app.piggyTab = "home"; app.render(); } },
    `← ${lv.name}的所有倉`)),
  head, bookPositionsCard(bd), b.derivatives && b.derivatives >= b.trades ? null : compareCard(app, lv, L.bench, b.id),
  tradesCard(app, lv, L, b), bookActionsCard(app, lv, b)];
}

// 這一倉付了多少手續費、交易稅（佔成交金額的比例）
function costLine(c) {
  if (!c || (!c.turnover && !c.derivatives)) return null;
  const bits = [];
  if (c.turnover) {
    bits.push(`手續費 ${num(c.fee)}（${(c.fee_rate * 100).toFixed(3)}%・約 ${(c.fee_discount * 10).toFixed(1)} 折）`);
    if (c.tax_rate !== null && c.tax_rate !== undefined) bits.push(`交易稅 ${num(c.tax)}（賣出的 ${(c.tax_rate * 100).toFixed(2)}%）`);
  }
  if (c.derivatives) bits.push(`期貨選擇權 ${num(c.derivatives.lots)} 口：手續費 ${num(c.derivatives.fee)}（每口 ${c.derivatives.fee_per_lot}）・期交稅 ${num(c.derivatives.tax)}`);
  return h("p", { class: "muted small" }, (c.turnover ? `成交 ${num(c.turnover)}・` : "") + bits.join("・"));
}

// 未平倉＝買了還沒賣完（做多）或賣了還沒買回（放空）。每一檔都用字講清楚是哪一種，不只靠顏色。
const OPEN_WORDS = {
  long: ["未平倉・做多", "買了還沒賣完，帳上還有這些股"],
  short: ["未平倉・放空", "賣出比買進多：帳上賣掉的比買進的多，還沒買回來補平"],
};

function bookPositionsCard(bd) {
  const items = (bd.positions.items || []).filter((i) => i.shares);
  const closed = bd.book.status !== "open";
  const shorts = items.filter((i) => i.side === "short").length;
  return h("section", { class: "card" }, h("h2", null, "這一倉還沒平倉的"),
    closed && items.length ? h("p", { class: "note-line bad" },
      `這一倉已經收倉（${ymd(bd.book.closed_at)}），但帳上還有 ${items.length} 檔沒平倉。收倉時結果已經寫死，不含這些的漲跌。`
      + (shorts ? "其中「放空」通常不是真的放空，而是賣出的股數比記下來的買進多——多半是漏記了買進、配股或零股；請對一下券商庫存。" : "")) : null,
    items.length ? h("div", { class: "ledger" }, items.map((i) => {
      const [word, why] = OPEN_WORDS[i.side === "short" ? "short" : "long"];
      return h("div", { class: "entry nodate" },
        h("span", { class: "what" }, h("b", null, `${i.symbol} ${i.name}`.trim() || i.key, h("i", { class: `tag ${i.side === "short" ? "bad" : ""}`, title: why }, word)),
          h("small", null, `${num(Math.abs(i.shares))} ${i.unit || "股"}・平均 ${price2(i.avg_price)}・最後動 ${ymd(i.last_date)}`),
          h("small", { class: "muted" }, why)),
        h("span", { class: "money" }, h("span", { class: "amt" }, num(i.cost)), h("span", { class: "bal" }, i.side === "short" ? "賣出拿到" : "成本")));
    }))
      : h("p", { class: "muted small" }, "這一倉現在沒有持股，全部都平倉了。"));
}

// 收倉有賺的真倉：捐贈多少、做了沒（只有開了「捐贈」選配才出現）
function givingLine(app, b, dateFmt) {
  if (!giving(app) || b.status === "open" || b.kind !== "real" || !((b.tithe_amount || 0) > 0)) return null;
  return h("div", { class: "row small" },
    h("span", null, `捐贈 ${num(b.tithe_amount)}（${pct(b.tithe_rate_used)}）${b.tithe_done_at ? `・${dateFmt(b.tithe_done_at)} 做完` : "・還沒做"}`),
    h("span", { class: "actions" },
      h("button", { type: "button", class: "btn small", onclick: () => titheRate(app, b.id, b.tithe_rate_used) }, "比例"),
      b.tithe_done_at ? h("button", { type: "button", class: "btn small", onclick: () => titheDone(app, b.id, false) }, "取消打勾")
        : h("button", { type: "button", class: "btn small primary", onclick: () => titheDone(app, b.id, true) }, "✓ 做完了")));
}

function bookActionsCard(app, lv, b) {
  return h("section", { class: "card" }, h("h2", null, "這一倉"),
    givingLine(app, b, ymd),
    b.note ? h("details", { class: "small" }, h("summary", null, "筆記"), h("p", { class: "pre" }, b.note)) : null,
    h("div", { class: "actions" },
      h("button", { type: "button", class: "btn small", onclick: () => bookForm(app, lv, b) }, "改"),
      b.status === "open" ? h("button", { type: "button", class: "btn small", onclick: () => confirmClose(app, b) }, "收倉")
        : h("button", { type: "button", class: "btn small", onclick: () => bookAction(app, b.id, "reopen", b.name) }, "重新打開"),
      h("button", { type: "button", class: "btn small danger", onclick: () => confirmArchive(app, b) }, "封存")));
}

// 操盤三條規矩：一直在上面；停止了整張變紅。
function rulesCard(app, L) {
  const m = L.month || {};
  const open = L.books.filter((b) => b.status === "open" && b.kind !== "history");
  const stopped = open.filter((b) => b.stopped);
  const missing = open.filter((b) => b.rules_missing);
  const alarm = m.stopped || stopped.length;
  const lines = [];
  if (m.stopped) lines.push(h("p", { class: "note-line bad" }, `本月可虧損 ${num(m.limit)} 打完了：這期剩下的日子停止操盤，進場會記成違規。`));
  stopped.forEach((b) => lines.push(h("p", { class: "note-line bad" }, `「${b.name}」可虧損打完了：只能離場，再進場會記成違規。`)));
  missing.forEach((b) => lines.push(h("p", { class: "note-line" }, `「${b.name}」還沒設可虧損——先到「設定」寫好再下單。`)));
  return h("section", { class: `card rules ${alarm ? "alarm" : ""}` },
    h("h2", null, "🎯 操盤三條規矩"),
    h("p", { class: "muted small" }, m.limit ? `本月可虧損 ${num(m.limit)}：已賠 ${num(m.loss)}，還剩 ${num(m.left)}` : "本月可虧損：還沒設（在「設定」）"),
    h("ol", { class: "rule-list" },
      h("li", null, "每一倉先寫好：操作金額、可虧損、單次可虧損。"),
      h("li", null, "可虧損打完就停——只能離場，不能再進場。"),
      h("li", null, "單筆不能賠超過單次可虧損；下單前用停損價算手數。")),
    lines);
}

// ---------------------------------------------------------------- 線圖：實際 vs 基準紅線、往後的期望

function benchCard(app, lv, L) {
  const b = L.bench;
  const c = b.compare;
  const name = benchOf(b);
  const verdict = c ? h("p", { class: `note-line ${c.beat ? "good" : "bad"}` },
    `同樣的錢放在${name}（年 ${pct(b.rate)}）會是 ${num(c.shadow)}；你現在 ${num(c.remaining)}，${c.beat ? "贏" : "輸"} ${num(Math.abs(c.diff))}。`)
    : h("p", { class: "muted small" }, lv.count ? `記帳裡轉錢進這一層的帳戶（例：銀行帳戶 → 證券帳戶）之後，就會畫出跟${name}比的線。` : "");
  const rows = b.blueprint.map((x) => h("tr", null, h("td", null, `${x.years} 年後`), h("td", { class: "num" }, num(x.bench)),
    h("td", { class: "num muted" }, num(x.low)), h("td", { class: "num muted" }, num(x.put_in))));
  if (lv.key === "trading") {                       // 停損額當成消失的錢：不畫藍圖，只看「有沒有比放著基準好」
    return h("section", { class: "card" },
      h("div", { class: "row" }, h("h2", null, `跟${name}比`), h("span", { class: "muted small" }, `尺度 年 ${pct(b.rate)}`)),
      chart(b, L.value.value, { future: false }), verdict,
      h("p", { class: "muted small" }, `每個月撥進來的停損額當成消失的錢：操盤要贏過「同樣的錢放在${name}」才值得。`));
  }
  return h("section", { class: "card" },
    h("div", { class: "row" }, h("h2", null, `跟${name}比`), h("span", { class: "muted small" }, `尺度 年 ${pct(b.rate)}（設定可改）`)),
    chart(b, L.value.value),
    verdict,
    h("table", { class: "mini" },
      h("thead", null, h("tr", null, h("th", null, "藍圖"), h("th", null, `年 ${pct(b.rate)}`), h("th", null, `保守 ${pct(b.low)}`), h("th", null, "放進去的"))),
      h("tbody", null, rows)),
    h("p", { class: "muted small" }, L.monthly_used ? `從目前 ${num(L.value.value)} 起，每月再放 ${num(L.monthly_used)}（${L.plan_monthly ? "照規劃" : "照這個月"}）。只算到 5 年：未來沒人知道，標著給自己期待。`
      : "規劃頁填了每月放多少，藍圖就會把每月存的也算進去。"));
}

function chart(b, now, { future: withFuture = true } = {}) {
  const past = b.series || [];
  const future = withFuture ? (b.future || []).slice(0, 37) : [];
  if (!past.length && (!now || !future.length)) return null;              // 沒東西畫：底下那一句會講怎麼開始
  const name = benchOf(b);
  // 畫布寬度跟著螢幕走（手機上字才不會被縮成芝麻）：viewBox 寬≈實際寬，字就是 11px
  const W = Math.round(Math.max(300, Math.min(660, (globalThis.innerWidth || 660) - 70))), H = 220, L = 46, R = 52, T = 14, B = 26;
  const t = (iso) => new Date(`${iso}T00:00:00`).getTime();
  const pts = [...past.map((p) => p.date), ...future.map((p) => p.date)];
  if (pts.length === 1) pts.push(new Date(t(pts[0]) + 30 * 864e5).toISOString().slice(0, 10));   // 只有一點：留一個月寬
  const x0 = t(pts[0]), x1 = t(pts[pts.length - 1]) || x0 + 1;
  const ymax = Math.max(1, ...past.flatMap((p) => [p.put_in, p.bench, p.actual]), ...future.map((p) => p.value)) * 1.08;
  const ymin = Math.min(0, ...past.flatMap((p) => [p.actual, p.bench, p.put_in]), ...future.map((p) => p.value));
  const X = (iso) => L + ((t(iso) - x0) / Math.max(1, x1 - x0)) * (W - L - R);
  const Y = (v) => T + (1 - (v - ymin) / (ymax - ymin)) * (H - T - B);
  const path = (list, key) => list.map((p, i) => `${i ? "L" : "M"}${X(p.date).toFixed(1)},${Y(p[key]).toFixed(1)}`).join(" ");
  const ticks = niceTicks(ymin, ymax);
  const kids = [];
  ticks.forEach((v) => kids.push(s("line", { class: "grid", x1: L, x2: W - R, y1: Y(v), y2: Y(v) }),
    s("text", { x: L - 6, y: Y(v) + 4, "text-anchor": "end" }, compact(v))));
  if (past.length) {
    const area = `${path(past, "put_in")} L${X(past[past.length - 1].date).toFixed(1)},${Y(Math.max(0, ymin)).toFixed(1)} L${X(past[0].date).toFixed(1)},${Y(Math.max(0, ymin)).toFixed(1)} Z`;
    kids.push(s("path", { class: "a-put", d: area }),
      s("path", { class: "l-bench", d: path(past, "bench") }),
      s("path", { class: "l-actual", d: path(past, "actual") }));
  }
  if (future.length) {
    kids.push(s("path", { class: "l-future", d: path(future, "value") }));
    const last = future[future.length - 1];
    kids.push(s("text", { x: X(last.date) + 6, y: Y(last.value) + 4 }, compact(last.value)));
  }
  const today = future.length ? future[0].date : past[past.length - 1].date;
  kids.push(s("line", { class: "today", x1: X(today), x2: X(today), y1: T, y2: H - B }),
    s("text", { x: X(today) + 4, y: T + 10, "text-anchor": "start" }, "今天"));   // 寫在上面，不跟底下的月份疊在一起
  const endActual = past.length ? past[past.length - 1] : null;
  if (past.length <= 3) {                           // 點太少連不成線：每一點都標出來
    past.forEach((p) => kids.push(s("circle", { class: "dot-bench", cx: X(p.date), cy: Y(p.bench), r: 4 })));
  }
  if (endActual) kids.push(s("circle", { class: "dot-actual", cx: X(endActual.date), cy: Y(endActual.actual), r: 4 }));
  const first = pts[0];
  kids.push(s("text", { x: L, y: H - 8, "text-anchor": "start" }, first.slice(0, 7)),
    s("text", { x: W - R, y: H - 8, "text-anchor": "end" }, pts[pts.length - 1].slice(0, 7)));
  const cross = s("line", { class: "cross", x1: 0, x2: 0, y1: T, y2: H - B, visibility: "hidden" });
  const tip = h("div", { class: "tip", hidden: true });
  const all = [...past.map((p) => ({ ...p, kind: "past" })), ...future.map((p) => ({ ...p, kind: "future" }))];
  // 指到哪一點：滑鼠移動、手指點一下（手機）、鍵盤 ← →（focus 先看最後一點）。讀屏念 aria-valuetext。
  const hit = s("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent", tabindex: "0",
    role: "slider", "aria-label": "每個月的數字（用 ← → 換）", "aria-valuemin": "0", "aria-valuemax": String(all.length - 1) });
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "group", "aria-label": `實際金額、${name}紅線、往後的期望` }, kids, cross, hit);
  let at = all.length - 1;
  const showAt = (i) => {
    at = Math.max(0, Math.min(all.length - 1, i));
    const best = all[at];
    const box = svg.getBoundingClientRect();
    const px = X(best.date);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
    const rowsTip = best.kind === "past"
      ? [["actual", "實際", best.actual], ["bench", `${name}紅線`, best.bench], ["put", "累計投入", best.put_in]]
      : [["future", "往後的期望", best.value]];
    fill(tip, h("div", { class: "muted" }, best.date.slice(0, 7)),
      rowsTip.map(([k, label, val]) => h("div", null, h("i", { class: `key-line ${k}` }), h("b", null, num(val)), ` ${label}`)));
    tip.hidden = false;
    const left = (px / W) * (box.width || W);
    tip.style.left = `${Math.min(Math.max(left + 10, 0), Math.max(0, (box.width || W) - 150))}px`;
    hit.setAttribute("aria-valuenow", String(at));
    hit.setAttribute("aria-valuetext", `${best.date.slice(0, 7)}：` + rowsTip.map(([, label, val]) => `${label} ${num(val)}`).join("、"));
  };
  const nearest = (clientX) => {
    const box = svg.getBoundingClientRect();
    const xv = ((clientX - box.left) / box.width) * W;
    let i = 0;
    all.forEach((p, k) => { if (Math.abs(X(p.date) - xv) < Math.abs(X(all[i].date) - xv)) i = k; });
    return i;
  };
  const hide = () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); };
  hit.addEventListener("pointermove", (e) => showAt(nearest(e.clientX)));
  hit.addEventListener("pointerdown", (e) => showAt(nearest(e.clientX)));            // 手機點一下就出來
  hit.addEventListener("pointerleave", (e) => { if (e.pointerType !== "touch") hide(); });   // 手指離開不要馬上消失
  hit.addEventListener("focus", () => showAt(at));
  hit.addEventListener("blur", hide);
  hit.addEventListener("keydown", (e) => {
    const step = { ArrowLeft: -1, ArrowRight: 1, Home: -all.length, End: all.length }[e.key];
    if (step === undefined) return;
    e.preventDefault();
    showAt(at + step);
  });
  const legend = h("div", { class: "legend small" },
    past.length ? h("span", { class: "key" }, h("i", { class: "key-line actual" }), "實際") : null,
    past.length ? h("span", { class: "key" }, h("i", { class: "key-line bench" }), `${name}紅線（同樣的錢）`) : null,
    future.length ? h("span", { class: "key" }, h("i", { class: "key-line future" }), `往後的期望（年 ${pct(b.rate)}）`) : null,
    past.length ? h("span", { class: "key" }, h("i", { class: "key-area" }), "累計投入") : null);
  const table = h("details", { class: "small" }, h("summary", null, "看數字"),
    h("table", { class: "mini" },
      h("thead", null, h("tr", null, h("th", null, "月"), h("th", null, "投入"), h("th", null, "實際"), h("th", null, name))),
      h("tbody", null, past.map((p) => h("tr", null, h("td", null, p.date.slice(0, 7)), h("td", { class: "num" }, num(p.put_in)),
        h("td", { class: "num" }, num(p.actual)), h("td", { class: "num" }, num(p.bench)))),
      future.filter((_, i) => i && i % 12 === 0).map((p) => h("tr", { class: "muted" }, h("td", null, p.date.slice(0, 7)),
        h("td", null, ""), h("td", { class: "num" }, `期望 ${num(p.value)}`), h("td", null, ""))))));
  return h("div", { class: "chart" }, legend, svg, tip, table);
}

function niceTicks(lo, hi) {
  const span = hi - lo || 1;
  const raw = span / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw) || mag * 10;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v);
  return out;
}

function compact(v) {
  const a = Math.abs(v);
  if (a >= 1e8) return `${Math.round(v / 1e7) / 10}億`;
  if (a >= 1e4) return `${Math.round(v / 1e3) / 10}萬`;
  return num(v);
}

// ---------------------------------------------------------------- 分頁清單（更多）

// 一次拿一頁：載入中「更多」不能再按（連點不會重複）；換篩選時 reset，還在路上的舊回應直接丟掉。
function pager(box, more, fetchPage, renderRow) {
  let offset = 0, gen = 0, busy = false;
  const rows = [];
  const load = async () => {
    if (busy) return null;
    busy = true;
    more.disabled = true;
    const mine = gen;
    try {
      const page = await fetchPage(offset);
      if (mine !== gen) return null;
      rows.push(...page.items.map(renderRow));
      fill(box, rows);
      offset += page.items.length;
      more.hidden = !page.more;
      return page;
    } finally {
      if (mine === gen) { busy = false; more.disabled = false; }
    }
  };
  more.onclick = () => load().catch(() => {});
  return {
    load,
    reset() { gen++; busy = false; more.disabled = false; rows.length = 0; offset = 0; fill(box); return load(); },
    removed() { offset = Math.max(0, offset - 1); },     // 收起來一列：伺服器那邊少一筆，下一頁要往前一格才不會漏
  };
}

// ---------------------------------------------------------------- 個股 vs 基準（不管輸贏一直列著）

// 對齊的表（名字有長有短也要整齊）：股票｜持有天數｜實際／基準｜輸贏
function compareCard(app, lv, bench, bookId = null) {
  const name = benchOf(bench);
  const box = h("div", { class: "cmp-body" });
  const head = h("p", { class: "muted small" }, "讀取中…");
  const more = h("button", { type: "button", class: "btn small", hidden: true }, "更多");
  const list = pager(box, more, async (offset) => {
    const params = { level: lv.key, offset, limit: 20 };
    if (bookId) params.book = bookId;
    const c = await api("/api/piggy/compare", { params });
    const cname = benchOf(c);
    head.textContent = !c.total ? `還沒有個股可以比${c.symbol ? `（${cname}自己不比）` : ""}。`
      : `${c.total} 檔裡贏 ${c.beat} 檔；加起來比${cname}${c.diff >= 0 ? "多" : "少"} ${num(Math.abs(c.diff))}。`
        + `尺度年 ${pct(c.rate)}＝每天 ${(c.daily * 100).toFixed(3)}%，持有幾天就滾幾天（出清的比到賣掉那天）；持股用成本算。`
        + (c.hidden ? ` 收起來的 ${c.hidden} 檔在「設定」。` : "");
    return { items: c.items, more: offset + c.items.length < c.total };
  }, (i) => compareRow(app, lv, i, () => list.removed()));
  list.load().catch((e) => { head.textContent = errorText(e); });
  return h("section", { class: "card" },
    h("div", { class: "row" }, h("h2", null, `個股 vs ${name}`), h("span", { class: "muted small" }, "不管輸贏一直列著")),
    head,
    h("div", { class: "cmp-table", role: "table", "aria-label": `個股跟${name}比` },
      h("div", { class: "cmp-row cmp-head", role: "row" },
        h("span", { role: "columnheader" }, "股票"), h("span", { role: "columnheader" }, "持有"),
        h("span", { role: "columnheader" }, `實際／${name}`), h("span", { role: "columnheader" }, "輸贏"), h("span")),
      box),
    h("div", { class: "actions" }, more),
    h("p", { class: "muted small" }, `同樣的錢、同一天進出，換成${name}的尺度會剩多少。輸的就是要想清楚的地方：這一檔、這種做法，值不值得。`));
}

function compareRow(app, lv, i, onHidden = () => {}) {
  const label = `${i.symbol} ${i.name}`.trim() || i.key;
  const row = h("div", { class: "cmp-row", role: "row", title: `${label}：${ymd(i.since)}～${ymd(i.until)}，${i.days} 天` },
    h("span", { class: "cmp-name", role: "cell" }, h("b", null, i.name || i.symbol || i.key),
      h("small", null, i.symbol && i.name ? i.symbol : "", i.open ? h("i", { class: "tag" }, "在場上") : null)),
    h("span", { class: "num", role: "cell", title: `${num(i.days)} 天` }, held(i.days)),
    h("span", { class: "num cmp-pair", role: "cell" }, h("span", null, signed(i.actual_gain)), h("small", { class: "muted" }, signed(i.shadow_gain))),
    h("span", { class: `num ${i.beat ? "good" : "bad"}`, role: "cell" }, `${i.beat ? "贏" : "輸"} ${num(Math.abs(i.diff))}`),
    h("span", { role: "cell" },
      h("button", { type: "button", class: "x", title: "收起來（之後在設定拿回來）", "aria-label": `收起來 ${label}`, onclick: async () => {
        try {
          const r = await api("/api/piggy/hide", { method: "POST", body: { layer: lv.key, key: i.key, hidden: true } });
          if (r.write_seq) app.seq = r.write_seq;          // 是我寫的：回到分頁時不要以為有新的記帳進來而整頁重畫
          row.hidden = true;
          onHidden();
          app.toast(`${label} 收起來了`, r.batch_id ? { label: "復原", run: async () => {
            try {
              const back = await api(`/api/batch/${r.batch_id}/revert`, { method: "POST" });
              await app.afterWrite(back, `${label} 拿回來了`);
            } catch (e) { if (e.code !== "cancelled") app.toast(errorText(e)); }
          } } : null);
        } catch (e) { app.toast(errorText(e)); }
      } }, "✕")));
  return row;
}

// ---------------------------------------------------------------- 倉

function bookRow(app, b, { click = false, bench = null } = {}) {
  const used = b.max_loss ? Math.min(100, Math.round((b.loss_used / b.max_loss) * 100)) : 0;
  const m = b.bench;
  const bits = [b.capital ? `操作金額 ${num(b.capital)}` : null,
    b.max_loss ? `可虧損 ${num(b.max_loss)}，還剩 ${num(b.loss_left)}` : (b.layer === "trading" && b.kind !== "history" ? "還沒設可虧損" : null),
    b.per_trade_loss ? `單次 ${num(b.per_trade_loss)}` : null,
    b.violations ? `違規 ${b.violations}` : null].filter(Boolean);
  const attrs = click ? { class: `book clickable ${b.stopped ? "stopped" : ""}`, role: "button", tabindex: "0",
    onclick: () => openBook(app, b), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openBook(app, b); } } }
    : { class: `book ${b.stopped ? "stopped" : ""}` };
  return h("div", attrs,
    h("div", { class: "row" },
      h("b", null, b.name, tagOf(b)),
      h("span", { class: `num ${b.result < 0 ? "bad" : (b.result > 0 ? "good" : "")}` }, b.trades ? signed(b.result) : "—")),
    m ? h("small", { class: m.beat ? "good" : "bad" }, `跟${benchOf(bench)}比 ${m.beat ? "贏" : "輸"} ${num(Math.abs(m.diff))}`
      + (b.in_market ? `・在場上 ${num(b.in_market)}（用成本算）` : "")) : null,
    b.max_loss ? h("div", { class: `bar ${used >= 100 ? "over" : ""}`, role: "img", "aria-label": `可虧損用了 ${used}%` },
      h("i", { style: { width: `${used}%` } })) : null,
    h("small", { class: "muted" }, bits.join("・") || `${b.trades} 筆`),
    b.stopped ? h("p", { class: "note-line bad" }, `停止中（${md(b.stopped_on)} 起）：只能離場。`) : null);
}

function booksSetupCard(app, lv, L) {
  const list = L.books;
  const newBtn = h("button", { type: "button", class: "btn primary", onclick: () => bookForm(app, lv) },
    "＋ 開一倉");
  const sim = h("button", { type: "button", class: "btn", onclick: () => bookForm(app, lv, { kind: "sim" }) }, "＋ 模擬倉");
  return h("section", { class: "card" }, h("h2", null, "倉"),
    h("p", { class: "muted small" }, lv.key === "trading"
      ? "每一倉先寫好三個數字：操作金額、可虧損（整倉最多賠多少）、單次可虧損（算手數用）。模擬倉不算進真的數字。"
      : `投資一年一倉，年底收倉${giving(app) ? "算捐贈" : "把結果寫死"}。記交易沒指定倉就放進那一年的倉。`),
    h("div", { class: "ledger" }, list.map((b) => h("div", { class: "book" },
      bookRow(app, b, { bench: L.bench }),
      h("div", { class: "actions" },
        h("button", { type: "button", class: "btn small", onclick: () => bookForm(app, lv, b) }, "改"),
        b.status === "open" ? h("button", { type: "button", class: "btn small", onclick: () => confirmClose(app, b) }, "收倉")
          : h("button", { type: "button", class: "btn small", onclick: () => bookAction(app, b.id, "reopen", b.name) }, "重新打開"),
        h("button", { type: "button", class: "btn small danger", onclick: () => confirmArchive(app, b) }, "封存"))))),
    h("div", { class: "actions" }, newBtn, sim,
      L.archived ? h("button", { type: "button", class: "btn", onclick: () => archivedList(app, lv) }, `封存的 ${L.archived} 倉`) : null));
}

function confirmClose(app, b) {
  app.openModal({
    title: `收倉：${b.name}`,
    body: [h("p", null, `結果 ${signed(b.result)} 會寫死，之後不會再變。`),
      giving(app) && b.result > 0 && b.kind === "real" ? h("p", { class: "note-line good" }, `有賺：捐贈 ${num(Math.round(b.result * b.tithe_rate_used))}（${pct(b.tithe_rate_used)}），收倉之後在「撲滿待處理」打勾。`) : null,
      b.open ? h("p", { class: "note-line" }, `還有 ${b.open} 檔在場上（用成本算）。`) : null],
    foot: [h("button", { type: "button", class: "btn primary", onclick: () => bookAction(app, b.id, "close", b.name) }, "收倉")],
  });
}

function confirmArchive(app, b) {
  app.openModal({
    title: `封存：${b.name}`,
    body: [h("p", null, "從清單收起來，資料不會消失；之後可以拿回來。還開著的會先收倉。")],
    foot: [h("button", { type: "button", class: "btn danger", onclick: () => bookAction(app, b.id, "archive", b.name) }, "封存")],
  });
}

async function archivedList(app, lv) {
  try {
    const r = await api("/api/books", { params: { level: lv.key, archived: true } });
    app.openModal({
      title: "封存的倉",
      body: [h("div", { class: "ledger" }, r.books.map((b) => h("div", { class: "entry nodate" },
        h("span", { class: "what" }, h("b", null, b.name), h("small", null, `${b.kind_name}・結果 ${signed(b.result || 0)}`)),
        h("span", { class: "money" }, h("button", { type: "button", class: "btn small", onclick: () => bookAction(app, b.id, "unarchive", b.name) }, "拿回來")))))],
    });
  } catch (e) { app.toast(errorText(e)); }
}

function bookForm(app, lv, b = {}) {
  const editing = Boolean(b.id);
  const year = String((app.meta && app.meta.today) || "").slice(0, 4);
  const name = h("input", { value: b.name || "", placeholder: lv.key === "trading" ? "例：波段一號" : `例：${year} 投資` });
  const capital = h("input", { class: "amt", inputmode: "numeric", value: b.capital ? String(b.capital) : "", placeholder: "0" });
  const maxLoss = h("input", { class: "amt", inputmode: "numeric", value: b.max_loss ? String(b.max_loss) : "", placeholder: "整倉最多賠多少" });
  const per = h("input", { class: "amt", inputmode: "numeric", value: b.per_trade_loss ? String(b.per_trade_loss) : "", placeholder: "一筆最多賠多少" });
  const withGiving = giving(app);
  const rate = h("input", { inputmode: "decimal", value: b.tithe_rate !== null && b.tithe_rate !== undefined ? String(Math.round(b.tithe_rate * 1000) / 10) : "", placeholder: "不填＝預設" });
  const start = h("input", { type: "date", value: b.start_date || app.meta.today });
  const end = h("input", { type: "date", value: b.end_date || "" });
  const note = h("textarea", { rows: 3, placeholder: "這一倉的做法、心態" }, b.note || "");
  const submit = () => {
    const data = { name: name.value.trim() || null, capital: capital.value.replace(/,/g, "") || 0,
      max_loss: maxLoss.value.replace(/,/g, "") || (editing ? "" : null), per_trade_loss: per.value.replace(/,/g, "") || (editing ? "" : null),
      note: note.value };
    if (withGiving) data.tithe_rate = rate.value ? `${rate.value}%` : (editing ? "" : null);
    if (editing) {
      Object.assign(data, { start_date: start.value, end_date: end.value || "" });
      return send(app, () => api(`/api/books/${b.id}`, { method: "PATCH", body: data }), `${b.name} 改好了`);
    }
    Object.assign(data, { level: lv.key, kind: b.kind || "real", start: start.value, end: end.value || null });
    return send(app, () => api("/api/books", { method: "POST", body: data }), "開好一倉了");
  };
  app.openModal({
    title: editing ? `改倉：${b.name}` : (b.kind === "sim" ? "開一個模擬倉" : "開一倉"),
    body: [h("div", { class: "fgrid" },
      f("名字", name, "wide"),
      f("操作金額", capital), f("可虧損", maxLoss), f("單次可虧損", per), withGiving ? f("捐贈比例（%）", rate) : null,
      f("開始", start), f("結束（可以不填）", end),
      f("筆記", note, "wide")),
    lv.key === "trading" ? h("p", { class: "muted small" }, "可虧損打完這一倉就停止；單次可虧損用來算手數（股數 ≤ 單次可虧損 ÷（進場價 − 停損價））。") : null],
    foot: [h("button", { type: "button", class: "btn primary", onclick: submit }, editing ? "存" : "開倉")],
  });
}

function piggySettingsCard(app, lv, d) {
  const b = d.bench;
  const withGiving = giving(app);
  const benchName = h("input", { value: b.symbol || "", placeholder: "例：大盤指數", "aria-label": "基準叫什麼" });
  const rate = h("input", { inputmode: "decimal", value: String(Math.round(b.rate * 1000) / 10) });
  const low = h("input", { inputmode: "decimal", value: String(Math.round(b.low * 1000) / 10) });
  const tithe = h("input", { inputmode: "decimal", value: String(Math.round(d.tithe_rate * 1000) / 10) });
  const month = h("input", { class: "amt", inputmode: "numeric", value: d.month_max_loss ? String(d.month_max_loss) : "", placeholder: "0＝不設",
    "aria-label": "本月可虧損（全部的倉加起來）" });
  const save = () => send(app, () => api("/api/piggy/settings", { method: "PATCH", body: {
    bench_name: benchName.value.trim(), bench_rate: `${rate.value}%`, bench_low: `${low.value}%`,
    ...(withGiving ? { tithe_rate: `${tithe.value}%` } : {}),
    ...(lv.key === "trading" ? { month_max_loss: month.value.replace(/,/g, "") || 0 } : {}) } }), "撲滿的設定存好了");
  return h("section", { class: "card" }, h("h2", null, "尺子與規矩"),
    h("div", { class: "fgrid" },
      f("基準叫什麼", benchName, "wide"),
      f(`${benchOf(b)}尺度 %`, rate), f("保守線 %", low), withGiving ? f("捐贈 %", tithe) : null,
      lv.key === "trading" ? f("本月可虧損", month) : null),
    h("p", { class: "muted small" }, "基準是拿來比的尺子：自己挑一個想比的對象（例如大盤型的指數），填它長期大概的年報酬率；保守線是比較低的假設，兩條線一起看。"
      + "基準的名字如果是一檔的代號，那一檔就不跟自己比。"
      + (withGiving ? "捐贈不會記進明細（那是另一份金流）。" : "")
      + (lv.key === "trading" ? "本月可虧損＝這一期全部的倉加起來最多賠多少。" : "")),
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn primary", onclick: save }, "存")));
}

function hiddenCard(app, lv, L) {
  const box = h("div", { class: "ledger" });
  const sec = h("section", { class: "card", hidden: true }, h("h2", null, `收起來的個股 vs ${benchOf(L.bench)}`), box);
  api("/api/piggy/compare", { params: { level: lv.key, hidden: true, limit: 100 } }).then((c) => {
    const hid = c.items.filter((i) => i.hidden);
    if (!hid.length) return;
    sec.hidden = false;
    fill(box, hid.map((i) => h("div", { class: "entry nodate" },
      h("span", { class: "what" }, h("b", null, `${i.symbol} ${i.name}`.trim() || i.key),
        h("small", null, `${i.beat ? "贏" : "輸"} ${num(Math.abs(i.diff))}`)),
      h("span", { class: "money" }, h("button", { type: "button", class: "btn small", onclick: () => send(app,
        () => api("/api/piggy/hide", { method: "POST", body: { layer: lv.key, key: i.key, hidden: false } }), "拿回來了") }, "拿回來")))));
  }).catch(() => {});
  return sec;
}

// ---------------------------------------------------------------- 部位狀況（按了才看價格）

function positionsCard(app, lv) {
  const box = h("div", { class: "ledger" });
  const head = h("p", { class: "muted small" }, "讀取中…");
  const quoteNote = h("p", { class: "muted small" });
  const btn = h("button", { type: "button", class: "btn", hidden: true }, "查詢當下價格");   // 部位讀到了才出現
  let data = null;
  let prices = {};
  const draw = () => {
    fill(box, data.items.map((i) => {
      const q = prices[(i.symbol || "").toUpperCase()];
      const value = q && i.shares > 0 ? Math.round(i.shares * q.price * (i.multiplier || 1)) : null;
      const diff = value === null ? null : value - i.cost;
      return h("div", { class: "entry nodate" },
        h("span", { class: "what" }, h("b", null, `${i.symbol} ${i.name}`.trim() || i.key, h("i", { class: "tag" }, i.side === "short" ? "未平倉・放空" : "未平倉・做多")),
          h("small", null, `${num(Math.abs(i.shares))} ${i.unit || "股"}・平均 ${price2(i.avg_price)}` + (i.book ? `・${i.book}` : "")
            + (q ? `・${q.price}（${q.as_of}）` : ""))),
        h("span", { class: "money" },
          h("span", { class: "amt" }, num(value ?? i.cost)),
          h("span", { class: `bal ${diff > 0 ? "good" : diff < 0 ? "bad" : ""}` }, diff === null ? "成本" : signed(diff))));
    }));
  };
  api("/api/piggy/positions", { params: { level: lv.key } }).then((p) => {
    data = p;
    head.textContent = p.items.length
      ? `在場上 ${p.items.length} 檔，成本 ${num(p.holding_cost)}。出清的 ${p.closed_count} 檔合計已實現 ${signed(p.closed_realized)}；股利 ${num(p.dividends)}。`
      : "現在沒有持股。";
    btn.hidden = !p.items.length;
    draw();
  }).catch((e) => { head.textContent = errorText(e); });
  btn.onclick = async () => {
    const symbols = data.items.map((i) => i.symbol).filter(Boolean).join(",");
    try {
      const r = await api("/api/quotes", { params: { symbols } });
      prices = r.prices;
      const got = Object.keys(r.prices).length;
      quoteNote.textContent = got ? `有 ${got} 檔有人塞了價格（時間寫在每一檔後面）` + (r.missing.length ? `；沒有資料：${r.missing.join("、")}` : "。")
        : "沒有價格資料（還沒有人用 /money_price_push 塞進來），照舊用成本。";
      draw();
    } catch (e) { app.toast(errorText(e)); }
  };
  return h("section", { class: "card" },
    h("div", { class: "row" }, h("h2", null, "部位狀況"), btn),
    head, quoteNote, box,
    h("p", { class: "muted small" }, "平常一律用成本算。價格不自動抓：有人用 /money_price_push 塞進來，按「查詢當下價格」才會顯示。"));
}

// ---------------------------------------------------------------- 明細（交易明細，一頁 50 筆）

function tradesCard(app, lv, L, fixed = null) {
  const list = h("div", { class: "ledger" });
  const more = h("button", { type: "button", class: "btn small", hidden: true }, "更多");
  const head = h("p", { class: "muted small" });
  // 先選倉再看明細；點進一倉時就固定是那一倉
  const pick = h("select", { "aria-label": "只看某一倉", hidden: Boolean(fixed) },
    h("option", { value: "" }, "全部的倉"),
    L.books.map((b) => h("option", { value: String(b.id), selected: String(fixed ? fixed.id : (app.piggyBook || "")) === String(b.id) },
      `${b.name}（${b.kind_name}・${b.status_name}）`)));
  const pages = pager(list, more, async (offset) => {
    const params = { level: lv.key, offset, limit: PAGE };
    const chosen = fixed ? String(fixed.id) : pick.value;   // 點進一倉：一律是那一倉（封存的倉不在選單裡）
    if (chosen) params.book = chosen;
    const r = await api("/api/piggy/trades", { params });
    head.textContent = r.total ? `${r.total} 筆，新的在上面。` : "還沒有交易明細。按「記一筆」，或在聊天裡用 /money_trade_add 記。";
    return r;
  }, (t) => tradeRow(app, t));
  const fail = (e) => { head.textContent = errorText(e); };
  pick.onchange = () => { app.piggyBook = pick.value; pages.reset().catch(fail); };
  pages.load().catch(fail);
  return h("section", { class: "card" },
    h("div", { class: "row" }, h("h2", null, fixed ? "明細" : "交易明細"),
      fixed && fixed.status !== "open" ? null
        : h("button", { type: "button", class: "btn primary", onclick: () => tradeForm(app, lv, L, fixed ? fixed.id : null) }, "＋ 記一筆")),
    pick, head, list, h("div", { class: "actions" }, more));
}

function tradeRow(app, t) {
  const label = `${t.action_name} ${t.symbol} ${t.name}`.trim();
  // 期貨：價金是名目金額（點數 × 口數 × 每點），不是真的付出去的錢——右邊改寫「名目」，不要看起來像花了一大筆
  const future = t.instrument === "future";
  const sub = [t.shares ? `${num(t.shares)} ${t.unit || "股"}${t.price ? ` @ ${t.price}` : ""}` : null, t.book,
    t.day_trade ? "當沖" : null, t.realized ? `實現 ${signed(t.realized)}` : null].filter(Boolean).join("・");
  return h("button", { type: "button", class: `entry ymd ${t.violation ? "violation" : ""}`, onclick: () => tradeDetail(app, t) },
    h("span", { class: "when" }, h("span", { class: "d" }, ymd(t.date)), t.time ? h("span", { class: "d" }, t.time) : null),
    h("span", { class: "what" }, h("b", null, label, t.violation ? h("i", { class: "tag bad" }, "違規") : null), h("small", null, sub)),
    h("span", { class: "money" }, future && t.gross
      ? [h("span", { class: "amt muted" }, num(t.gross)), h("span", { class: "bal" }, "名目")]
      : h("span", { class: `amt ${t.net > 0 ? "plus" : ""}` }, signed(t.net))));
}

function rateText(t) {
  if (t.fee_per_lot !== null && t.fee_per_lot !== undefined) return `（每口 ${t.fee_per_lot}）`;
  if (t.fee_rate === null || t.fee_rate === undefined) return "";
  return `（手續費 ${(t.fee_rate * 100).toFixed(3)}%・約 ${(t.fee_discount * 10).toFixed(1)} 折`
    + (t.tax_rate !== null && t.tax_rate !== undefined ? `；稅 ${(t.tax_rate * 100).toFixed(2)}%` : "") + "）";
}

function tradeDetail(app, t) {
  const lines = [
    ["日期", `${t.date}${t.time ? " " + t.time : ""}`], ["帳戶・倉", `${t.account}・${t.book || "（沒有倉）"}`],
    ...(t.instrument && t.instrument !== "stock"
      ? [["口數 × 點數 × 每點", t.shares ? `${num(t.shares)} × ${t.price ?? "—"} × ${t.multiplier}` : "—"],
        [t.instrument === "future" ? "名目金額" : "權利金", num(t.gross)]]
      : [["股數 × 價格", t.shares ? `${num(t.shares)} × ${t.price ?? "—"}` : "—"], ["價金", num(t.gross)]]),
    ["手續費・稅", `${num(t.fee)}・${num(t.tax)}` + rateText(t)], ["淨收付", signed(t.net)],
    t.realized !== undefined ? ["這一筆實現", signed(t.realized || 0)] : null,
    t.stop_price ? ["停損價", t.stop_price] : null];
  app.openModal({
    title: `${t.action_name} ${t.symbol} ${t.name}`.trim(),
    body: [t.violation ? h("p", { class: "note-line bad" }, `違規：${t.violation}`) : null,
      h("table", { class: "mini" }, h("tbody", null, lines.filter(Boolean).map(([k, v]) => h("tr", null, h("th", null, k), h("td", { class: "num" }, v))))),
      t.note ? h("p", { class: "pre small" }, t.note) : null],
    foot: [h("button", { type: "button", class: "btn danger", onclick: () => send(app,
      () => api(`/api/trade/${t.id}`, { method: "DELETE" }), "刪掉了（可以復原）") }, "刪掉這一筆")],
  });
}

function tradeForm(app, lv, L, preset = null) {
  const today = app.meta.today;
  const open = L.books.filter((b) => b.status === "open");
  const day = h("input", { type: "date", value: today, max: today });
  const time = h("input", { type: "time" });
  const action = h("select", null, ACTIONS.map(([v, n]) => h("option", { value: v }, n)));
  const symbol = h("input", { placeholder: "例：TEST1", autocomplete: "off" });
  const name = h("input", { placeholder: "例：範例公司" });
  const shares = h("input", { class: "amt", inputmode: "numeric", placeholder: "股數" });
  const price = h("input", { class: "amt", inputmode: "decimal", placeholder: "單價" });
  const fee = h("input", { class: "amt", inputmode: "numeric", placeholder: "0" });
  const tax = h("input", { class: "amt", inputmode: "numeric", placeholder: "0" });
  const stop = h("input", { class: "amt", inputmode: "decimal", placeholder: "停損價" });
  const book = h("select", null,
    h("option", { value: "" }, lv.key === "trading" ? "自動（最新開著的倉）" : "自動（那一年的倉）"),
    open.map((b) => h("option", { value: String(b.id), selected: preset !== null && String(preset) === String(b.id) }, `${b.name}（${b.kind_name}）`)));
  const dayTrade = h("input", { type: "checkbox" });
  const note = h("textarea", { rows: 2, placeholder: "為什麼進場、停損設哪裡、心態" });
  const banner = h("div");
  const formBox = h("div", { class: "trade-form" });                 // 停止時整片變紅
  const hint = h("p", { class: "muted small" });
  const m = L.month || {};
  // 沒選倉＝伺服器自動挑：這一層第一個帳戶、最新開著的真倉（L.books 已經照 開始日↓、編號↓ 排好）
  const first = (lv.accounts[0] || {}).name;
  const chosen = () => (book.value ? open.find((b) => String(b.id) === book.value)
    : (lv.key === "trading" ? open.find((b) => b.kind === "real" && b.account === first) : null));
  const sync = () => {
    const b = chosen();
    const entering = action.value === "buy" || action.value === "sell";
    const msgs = [];
    if (lv.key === "trading" && entering) {
      if (m.stopped) msgs.push(h("p", { class: "note-line bad" }, "本月可虧損打完了：現在進場會記成違規。離場（賣掉持股）可以。"));
      if (b && b.stopped) msgs.push(h("p", { class: "note-line bad" }, `「${b.name}」已經停止：現在進場會記成違規。離場可以。`));
      if (b && b.rules_missing) msgs.push(h("p", { class: "note-line" }, `「${b.name}」還沒設可虧損——先到「設定」寫好。`));
      if (!b && !open.length) msgs.push(h("p", { class: "note-line" }, "還沒有開著的倉：記下去會自動開一倉，但記得馬上去設可虧損。"));
    }
    fill(banner, msgs);
    formBox.classList.toggle("alarm", msgs.some((x) => x.getAttribute && x.getAttribute("class") === "note-line bad"));
    const p = Number(price.value), st = Number(stop.value);
    if (b && b.per_trade_loss && p && st && p !== st) {
      const gap = Math.round(Math.abs(p - st) * 10000) / 10000;   // 小數相減在電腦裡會多出尾巴（0.30000000000000071）
      const n = Math.floor(b.per_trade_loss / gap + 1e-9);
      hint.textContent = `照單次可虧損 ${num(b.per_trade_loss)}：停損 ${st}，最多 ${num(n)} 股。`;
      shares.classList.toggle("over", Number(shares.value) > n);
    } else {
      hint.textContent = lv.key === "trading" ? "填了價格和停損價，會算出照單次可虧損最多買幾股。" : "";
      shares.classList.remove("over");
    }
  };
  [action, book, price, stop, shares].forEach((x) => x.addEventListener("input", sync));
  sync();
  // 數字照打的讀：全形、千分位、「股」「元」都接受；看不懂就講清楚，不要悄悄變成 0
  const clean = (x) => x.value.normalize("NFKC").replace(/[,\s元股]/g, "");
  const intOf = (x, label) => {
    const t = clean(x);
    if (t === "") return null;
    if (!/^\d+$/.test(t)) throw new Error(`${label}要寫整數（例：1000）`);
    return Number(t);
  };
  const decOf = (x, label) => {
    const t = clean(x);
    if (t === "") return null;
    if (!/^\d+(\.\d+)?$/.test(t)) throw new Error(`${label}要寫數字（例：33.1）`);
    return t;
  };
  const submit = () => send(app, async () => {
    const body = { level: lv.key, date: day.value, time: time.value || null, action: action.value,
      symbol: symbol.value.trim(), name: name.value.trim(), shares: intOf(shares, "股數") ?? 0, price: decOf(price, "單價"),
      fee: intOf(fee, "手續費") ?? 0, tax: intOf(tax, "交易稅") ?? 0, stop_price: decOf(stop, "停損價"), note: note.value,
      day_trade: dayTrade.checked };
    if ((action.value === "buy" || action.value === "sell") && !body.shares) throw new Error("買賣要寫股數");
    if (book.value) body.book = Number(book.value);
    return api("/api/trade", { method: "POST", body });
  }, (r) => (r.warnings && r.warnings.length ? `記好了。⚠️ ${r.warnings.join("；")}` : "記好了"));
  app.openModal({
    title: `記一筆：${lv.name}`,
    body: [banner, fill(formBox, h("div", { class: "fgrid" },
      f("日期", day), f("時間", time), f("買或賣", action), f("倉", book, "wide"),
      f("代號", symbol), f("名稱", name), f("股數", shares), f("單價", price),
      f("手續費", fee), f("交易稅", tax), f("停損價", stop),
      h("label", { class: "f check" }, dayTrade, "當沖"),
      f("筆記", note, "wide")), hint),
    h("p", { class: "muted small" }, "淨收付會自己算（買＝−(價金＋費＋稅)）。對帳單上的數字照填就好。")],
    foot: [h("button", { type: "button", class: "btn primary", onclick: submit }, "記下")],
  });
}

// ---------------------------------------------------------------- 檢討

function reviewCards(app, lv, L) {
  const b = L.bench;
  const c = b.compare;
  const name = benchOf(b);
  const closed = L.books.filter((x) => x.status === "closed");
  const open = L.books.filter((x) => x.status === "open");
  const violations = L.books.reduce((a, x) => a + x.violations, 0);
  return h("div", { class: "page-inner" },
    h("section", { class: "card" }, h("h2", null, "誠實面對"),
      h("ul", { class: "plain" },
        c ? h("li", null, `整個「${lv.name}」：同樣的錢放在${name}會是 ${num(c.shadow)}，你現在 ${num(c.remaining)}——${c.beat ? "贏" : "輸"} ${num(Math.abs(c.diff))}。`) : null,
        h("li", null, `開著的倉 ${open.length} 個、收掉的 ${closed.length} 個；收掉的加起來 ${signed(closed.reduce((a, x) => a + (x.result || 0), 0))}。`),
        lv.key === "trading" ? h("li", null, `違規 ${violations} 次`
          + (violations > L.violations.length ? `（下面列最近 ${L.violations.length} 筆）。` : "（下面一筆一筆列著）。")) : null),
      h("p", { class: "muted small" }, `個股一檔一檔跟${name}比：點進一倉就看得到。`)),
    lv.key === "trading" ? violationsCard(L.violations) : null,
    h("section", { class: "card" }, h("h2", null, "每一倉"),
      h("div", { class: "ledger" }, [...open, ...closed].map((x) => h("div", { class: "book" },
        bookRow(app, x, { click: true, bench: b }),
        x.status === "closed" ? givingLine(app, x, md) : null,
        x.note ? h("details", { class: "small" }, h("summary", null, "筆記"), h("p", { class: "pre" }, x.note)) : null)))));
}

function violationsCard(list) {
  return h("section", { class: `card ${list.length ? "rules alarm" : ""}` }, h("h2", null, "違規紀錄"),
    list.length ? h("div", { class: "ledger" }, list.map((t) => h("div", { class: "entry" },
      h("span", { class: "d" }, md(t.date)),
      h("span", { class: "what" }, h("b", null, `${t.action_name} ${t.symbol} ${t.name}`.trim()), h("small", null, `${t.book}：${t.violation}`)),
      h("span", { class: "money" }, h("span", { class: "amt" }, signed(t.net))))))
      : h("p", { class: "muted small" }, "沒有違規。保持。"));
}
