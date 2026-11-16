// 對明細卡（W8）：記帳與股票各自「最後對到哪一天」，加一個檢查模式看有沒有記錯或漏算。
// kind＝"ledger"（明細頁，對的是帳戶餘額）或 "stocks"（撲滿的部位狀況，對的是券商庫存股數）。
// 檢查模式只讀；股票那邊可以直接把「券商實際是幾股」記下來（動作 count），記下之後對不上的會變紅。
// 明細頁（book.js）與撲滿（piggy.js）都 import reconcileCard；讀不到資料時卡片裡寫原因，不會讓整頁壞掉。
import { api, errorText } from "../api.js";
import { count, fill, h } from "../dom.js";
import { send } from "../forms.js";

const ymd = (iso) => (iso ? iso.slice(0, 10).replace(/-/g, "/") : "");
const ago = (days) => (days === null || days === undefined ? "" : days === 0 ? "（今天）" : `（${count(days)} 天前）`);
const LEVEL_WORD = { bad: "對不上", warn: "要看一下", info: "提醒" };

export function reconcileCard(app, kind) {
  const body = h("div", null, h("p", { class: "muted small" }, "讀取中…"));
  let open = false;
  let data = null;
  const mine = (i) => i.key.startsWith(kind === "ledger" ? "ledger:" : "stock:");

  const dateLine = () => {
    const st = data.status;
    if (kind === "ledger") {
      const l = st.ledger;
      return l.last_checked
        ? `最後核對餘額：${ymd(l.last_checked)}${ago(Math.round((new Date(st.today) - new Date(l.last_checked)) / 864e5))}・最久沒核對的帳戶 ${ymd(l.oldest_checked)}`
        : "還沒核對過任何帳戶的餘額。";
    }
    const k = st.stocks;
    return `最後對券商股數：${k.last_counted ? ymd(k.last_counted) : "還沒對過"}・最後一筆交易明細：${k.last_trade ? ymd(k.last_trade) : "沒有"}`;
  };

  const countRow = (i) => {
    const input = h("input", { class: "amt", inputmode: "numeric", placeholder: "股數", "aria-label": `${i.symbol} 券商實際股數` });
    const go = () => {
      const v = input.value.replace(/[,\s]/g, "");
      if (!/^\d+$/.test(v)) { app.toast("股數要填整數（0＝券商已經沒有這檔）"); return; }
      send(app, () => api("/api/reconcile/count", { method: "POST", body: { account: i.account, symbol: i.symbol, shares: Number(v) } }),
        (r) => (r.match ? `${i.symbol} 對上了：${count(r.counted)} 股` : `${i.symbol} 記下 ${count(r.counted)} 股，跟帳上差 ${count(Math.abs(r.diff))}（帳上 ${count(r.derived)}）`));
    };
    return h("div", { class: "entry nodate" },
      h("span", { class: "what" }, h("b", null, `${i.symbol} ${i.name || ""}`.trim()),
        h("small", null, `帳上算 ${count(i.derived)} 股${i.counted !== null ? `・${ymd(i.counted_on)} 對過 ${count(i.counted)} 股` : "・還沒對過"}`
          + (i.state === "ok" && i.moved_since ? "・對過之後又有交易" : ""))),
      h("span", { class: "money" }, input,
        h("button", { type: "button", class: "btn small", onclick: go }, "記下實際股數")));
  };

  const draw = () => {
    const items = data.items.filter(mine);
    const bad = items.filter((i) => i.level === "bad").length;
    const stocks = kind === "stocks" ? data.status.stocks.items.filter((i) => i.state !== "ok" || i.moved_since) : [];
    fill(body,
      h("p", { class: "muted small" }, dateLine()),
      h("p", { class: `note-line ${bad ? "bad" : items.length ? "" : "good"}` },
        items.length ? `檢查結果：有 ${items.length} 處要對${bad ? `（${bad} 處對不上）` : ""}` : "檢查結果：沒有發現要對的地方"),
      h("div", { class: "actions" },
        h("button", { type: "button", class: "btn small", onclick: () => { open = !open; draw(); } }, open ? "收起檢查模式" : "打開檢查模式")),
      open ? h("div", { class: "ledger" }, items.length ? items.map((i) => h("div", { class: "entry nodate" },
        h("span", { class: "what" },
          h("b", null, i.title, h("i", { class: `tag ${i.level === "bad" ? "bad" : ""}` }, LEVEL_WORD[i.level])),
          h("small", null, i.why), i.where.hint ? h("small", { class: "muted" }, `怎麼處理：${i.where.hint}`) : null)))
        : h("p", { class: "muted small" }, "每一項都對得上。")) : null,
      open && stocks.length ? h("div", null, h("h3", null, "把券商庫存的實際股數記下來"), h("div", { class: "ledger" }, stocks.map(countRow))) : null);
  };

  api("/api/reconcile").then((r) => { data = r; draw(); }).catch((e) => fill(body, h("p", { class: "muted small" }, errorText(e))));
  return h("section", { class: "card" }, h("h2", null, "對明細"),
    h("p", { class: "muted small" }, kind === "ledger" ? "對過網銀或存摺的餘額，才知道有沒有記錯、漏記。" : "對過券商的庫存股數，才知道交易明細有沒有漏記買賣、配股。"), body);
}
