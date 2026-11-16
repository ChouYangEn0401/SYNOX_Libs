// 明細：像存摺一樣一筆一行，右邊是那一筆之後的錢包餘額；最上面一行就能記一筆。點一筆可以改每一格或刪掉。
// 每一行左邊是一顆圓球：裡面是這筆的圖示、底色是「怎麼付的」（現金淡紅、信用卡藍、存錢綠、投資黃…），日期在球下面。
// 「記一筆」跟「點開一筆」用同一組欄位、同樣的標籤與順序：
// 種類／金額／細節／帳戶／類別／算哪一期／日期／時間。
// 一行的寫法：**大項目（類別）在上、細節在下**——標題是類別（午餐、捐贈、房租），
// 細節是店家、菜名、買了什麼（範例便當店、雞腿便當、耳機）。細節就是聊天裡打數字記帳時按鈕上的選項（quick items）。
// recordForm 也給總覽的「今天處理」用：預先填好、確認就記。
// 對明細卡（reconcile.js）由撲滿那一包做：還沒做好時 reconcileCard 回 null，這裡就不顯示那張卡。
import { api, errorText } from "../api.js";
import { decimals, fill, h, major, md, num, toMinor } from "../dom.js";
import { reconcileCard } from "./reconcile.js";

// 金額填錯時的提示：小數位數 0 的帳本跟以前一樣說「整數」
const amountHint = () => (decimals() ? `金額要填大於 0 的數字（最多 ${decimals()} 位小數，例：95.5）` : "金額要填大於 0 的整數（例：95）");

const PAYABLE = ["cash", "bank", "credit_card", "e_wallet"];
let openId = null;
let formSeq = 0;

export async function render(app) {
  const [d, ref] = await Promise.all([api("/api/passbook", { params: { period: app.period } }), app.ref()]);
  app.setNav(d);
  const list = h("div", { class: "ledger" });
  fill(list, d.items.length ? d.items.flatMap((it) => row(app, it, ref, d.period.key)) : h("p", { class: "empty" },
    "這個月還沒有記帳。上面輸入就能記，或在聊天裡跟助理說。"));
  return h("div", { class: "page-inner" },
    h("section", { class: "card add-card" }, recordForm(app, ref, { draft: app.draft || (app.draft = {}), persist: true })),
    reconcileCard(app, "ledger"),
    h("section", { class: "card" },
      h("div", { class: "row" }, h("h2", null, "存摺"),
        h("span", { class: "muted small" }, `月初 ${num(d.opening)} → 現在 `, h("b", { class: "num" }, num(d.closing)))),
      legend(d.items),
      list));
}

// ---------------------------------------------------------------- 圓球：圖示＋底色（怎麼付的）

// 底色的意思：花錢看用什麼付的；搬錢看搬去做什麼。顏色在 style.css 的 --pay-*。
const PAY = {
  cash: "現金", credit: "信用卡", bank: "銀行", wallet: "電子錢包", save: "存錢", invest: "投資", prepay: "預付",
  income: "收入", cardpay: "繳卡費", lend: "代墊／欠款", debt: "分期", move: "轉帳",
};
const ACCOUNT_PAY = { cash: "cash", credit_card: "credit", bank: "bank", e_wallet: "wallet", securities: "invest", loan: "lend" };
const PURPOSE_PAY = { saving: "save", investing: "invest", prepay: "prepay", card_payment: "cardpay", lend: "lend", collect: "lend", repay: "lend", debt: "debt", pension: "save" };
const FALLBACK_ICON = { income: "💰", save: "🐖", invest: "📈", prepay: "📦", cardpay: "💳", lend: "🤝", debt: "📅", move: "🔁", expense: "🧾" };

export function payKey(it) {
  if (it.kind === "income") return "income";
  if (it.kind === "debt") return "debt";
  if (it.kind === "transfer") return PURPOSE_PAY[it.purpose] || "move";
  return ACCOUNT_PAY[it.account_kind] || "bank";
}

function iconOf(it, ref, key) {
  const icons = ref.icons || {};
  return icons[it.category] || icons[it.group] || FALLBACK_ICON[key] || FALLBACK_ICON[it.kind] || FALLBACK_ICON.expense;
}

// 這一頁用到哪幾種顏色，就列哪幾個（字少：一排小圓點加名字）。
function legend(items) {
  const seen = [...new Set(items.map(payKey))];
  if (!seen.length) return null;
  return h("p", { class: "legend muted small", title: "圓球的底色＝這筆是怎麼付的／搬去做什麼" },
    seen.map((k) => h("span", { class: "key" }, h("i", { class: `pdot ${k}` }), PAY[k])));
}

// ---------------------------------------------------------------- 兩邊共用的零件

// 記什麼：種類決定欄位名字、帳戶可選什麼、類別選單給哪一組。help＝表單上那一句「這是做什麼的」（每一種都要有，測試會擋）。
export const KINDS = [
  { id: "expense", name: "花錢", label: "細節", desc: "店家、菜名、買了什麼（例：範例便當店）",
    help: "今天吃喝用掉的日常花費：算在今天的日曆。" },
  { id: "stock", name: "長期用", base: "expense", nature: "stock", label: "細節", desc: "買了什麼（例：耳機、囤的日用品）",
    help: "這個月（或更久）才會慢慢用掉的：囤貨、3C、一年份的東西。算在生活費總額，但不算在每天的日曆、不會讓那天變紅。" },
  { id: "lend", name: "代墊", purpose: "lend", fixedTo: "代墊款", label: "幫誰付的", desc: "只寫對象的名字，例：小明", payLabel: "從哪個帳戶付",
    help: "幫別人先付的錢（信用卡刷的也行）：不算你的花費，錢還在「代墊款」等對方還你；對方還了就記「收回」。" },
  { id: "collect", name: "收回", purpose: "collect", fixedFrom: "代墊款", label: "誰還的", desc: "例：小明", payLabel: "收進哪個帳戶",
    help: "之前代墊的錢，對方還你了。" },
  // 欠別人：別人先幫你付＝算你的花費，但錢包等你還的那天才扣；還了記「還別人」
  { id: "owe", name: "別人幫我墊", base: "expense", iou: "owe", noAccount: true, label: "誰幫你付的", desc: "只寫對方的名字，例：小華",
    help: "別人先幫你付的（例：小華幫你買了午餐）：算你的花費，錢包等你還他那天才扣；總覽會列「我欠別人」。" },
  { id: "repay", name: "還別人", purpose: "repay", iou: "repay", fixedTo: "欠別人", label: "還給誰", desc: "例：小華", payLabel: "從哪個帳戶付",
    help: "之前別人幫你墊的錢，你還他了：錢包這天才少。" },
  { id: "income", name: "收入", label: "來源", desc: "哪裡來的（例：薪水）", help: "進來的錢：薪水、兼職、退款。" },
  { id: "saving", name: "存錢", purpose: "saving", to: ["bank", "cash", "e_wallet"], label: "說明", desc: "例：存緊急預備金",
    help: "把錢搬去存起來：不是花掉，錢還是你的。" },
  { id: "investing", name: "投資", purpose: "investing", to: ["securities"], label: "說明", desc: "例：每月定期定額",
    help: "把錢搬去投資帳戶：不是花掉。" },
  { id: "prepay", name: "預付", purpose: "prepay", to: ["bank", "cash", "e_wallet", "securities"], label: "說明", desc: "例：旅費先放進預備帳戶",
    help: "以後要用的錢，先付掉或先放到別的地方。轉進「有指定算哪一格」的帳戶（設定 → 帳戶），就算做到那一格、可以花的錢跟著變少。" },
  { id: "card_payment", name: "繳卡費", purpose: "card_payment", to: ["credit_card"], label: "說明", desc: "例：9 月帳單",
    help: "還信用卡的錢：刷卡那天已經算花掉了，這裡只是搬錢，不會重複算。" },
  { id: "other", name: "轉帳", purpose: "other", to: null, label: "說明", desc: "例：領現金", help: "自己帳戶之間搬錢（例：領現金）。" },
];

// 一格：上面小字標籤、下面輸入。
const field = (label, input, cls) => h("label", { class: `f ${cls || ""}`.trim() }, h("span", null, label), input);

const accountOptions = (sel, accounts, chosen) => {
  const names = accounts.map((a) => a.name);
  if (chosen && !names.includes(chosen)) names.unshift(chosen);        // 這筆原本的帳戶就算不在清單裡也要看得到
  fill(sel, names.map((n) => h("option", { value: n, selected: n === chosen }, n)));
};

// 類別選單：照大類分組、有 icon 的帶上 icon；blank 是「沒有類別」那一格的字。
function categoryOptions(sel, ref, kind, chosen, blank) {
  const groups = (ref.categories_by_kind || {})[kind] || [];
  const icons = ref.icons || {};
  const label = (n) => (icons[n] ? `${icons[n]} ${n}` : n);
  fill(sel, h("option", { value: "" }, blank),
    groups.map((g) => h("optgroup", { label: label(g.group) }, g.items.map((n) => h("option", { value: n }, label(n))))));
  sel.value = groups.some((g) => g.items.includes(chosen)) ? chosen : "";
  return sel.value;
}

// 細節欄打字時的提示：選了類別就列那個類別以前記過的（跟聊天裡的按鈕同一份）。
async function suggest(datalist, category) {
  fill(datalist);
  if (!category) return;
  try {
    const r = await api("/api/items", { params: { category } });
    fill(datalist, r.items.map((n) => h("option", { value: n })));
  } catch (e) { /* 提示拿不到就算了，不影響記帳 */ }
}

// 算哪一期（預繳／補繳）：只有花出去的固定項目（房租、手機費、存錢…）才有；生活費是每天在花的，不能。
function periodField(ref) {
  const sel = h("select", { "aria-label": "算哪一期" });
  const f = field("算哪一期", sel);
  f.title = "房租、手機費這類可以先繳（預繳）或補繳：現金照付款日，但算給哪一期的計畫。預設＝付款那一期。";
  const fillOptions = (chosen) => {
    const by = Object.fromEntries((ref.periods || []).map((p) => [p.which, p]));
    const opts = [["", "這一期（照付款日）"]];
    if (by.next) opts.push([by.next.key, `下期（${by.next.month}月）`]);
    if (by.prev) opts.push([by.prev.key, `上期（${by.prev.month}月）`]);
    if (chosen && !opts.some(([v]) => v === chosen)) opts.push([chosen, `${chosen}`]);
    fill(sel, opts.map(([v, t]) => h("option", { value: v, selected: v === (chosen || "") }, t)));
  };
  fillOptions("");
  return { node: f, select: sel, setChosen: fillOptions };
}

// 這個類別能不能「算哪一期」：不是生活費的支出、或任何轉帳。
const prepayable = (ref, kind, category) => {
  if (kind === "income") return false;
  if (kind === "transfer") return true;
  const info = category ? (ref.category_bucket || {})[category] : null;
  return Boolean(info) && !info.living;
};

// ---------------------------------------------------------------- 記一筆（也給「今天處理」用）

// opts: draft（要記住的草稿，persist＝重畫後還在）、defaults（預先填好：kind、amount、item、account、to、category、date）、
//       link（{bucket, name}＝「今天處理」的那一格：類別不屬於它時問要不要以後都算進它）、onDone（記好之後）、submitLabel
export function recordForm(app, ref, opts = {}) {
  const today = app.meta.today;
  const d = opts.defaults || {};
  const draft = opts.draft || {};
  if (!opts.persist || !draft.kind) {
    Object.assign(draft, { kind: "expense", date: today, time: "", account: null, to: null, category: "" }, d);
  }
  if (!KINDS.some((k) => k.id === draft.kind)) draft.kind = "expense";
  const kind = h("select", { "aria-label": "種類" },
    KINDS.map((k) => h("option", { value: k.id, selected: k.id === draft.kind }, k.name)));
  const amount = h("input", { class: "amt", inputmode: decimals() ? "decimal" : "numeric", placeholder: "例：95", "aria-label": "金額", value: d.amount ? major(d.amount) : "" });
  const listId = `item-suggest-${++formSeq}`;            // 總覽的「今天處理」會在視窗裡再開一份表單：datalist 的 id 不能重複
  const item = h("input", { list: listId, autocomplete: "off", "aria-label": "細節", value: d.item || "" });
  const suggestions = h("datalist", { id: listId });
  const account = h("select", { "aria-label": "帳戶" });
  const to = h("select", { "aria-label": "轉到" });
  const cat = h("select", { "aria-label": "類別" });
  const day = h("input", { type: "date", value: draft.date || today, max: today, "aria-label": "日期" });
  const time = h("input", { type: "time", value: draft.time || "", "aria-label": "時間" });
  const forP = periodField(ref);
  const linkBox = h("input", { type: "checkbox", checked: true });
  const linkField = h("label", { class: "f wide check", hidden: true }, linkBox, h("span", null, ""));
  const itemField = field("細節", item, "wide");
  const accountField = field("帳戶", account);
  const toField = field("轉到", to);
  // 選了哪一種，下面就寫一句「這是做什麼的」（只放在停上去的 title 裡的話，手機看不到）
  const kindHelp = h("p", { class: "f-help muted small", "aria-live": "polite" });
  const current = () => KINDS.find((x) => x.id === kind.value);
  const baseKind = (k) => (k.purpose ? "transfer" : (k.base || k.id));          // 長期用其實是花錢
  const syncTo = () => {
    const k = current();
    const wanted = ref.accounts.filter((a) => !k.to || k.to.includes(a.kind));
    accountOptions(to, (wanted.length ? wanted : ref.accounts).filter((a) => a.name !== account.value), draft.to);
  };
  const syncExtras = () => {
    const k = current();
    const show = !k.nature && prepayable(ref, baseKind(k), cat.value);          // 長期用是生活費，不能預繳
    forP.node.hidden = !show;
    if (!show) forP.select.value = "";
    const info = cat.value ? (ref.category_bucket || {})[cat.value] : null;
    const askLink = Boolean(opts.link) && baseKind(k) === "expense" && Boolean(info) && info.bucket !== opts.link.bucket;
    linkField.hidden = !askLink;
    if (askLink) linkField.lastChild.textContent = `以後「${cat.value}」都算進「${opts.link.name}」`;
  };
  // 類別：自己選（選了就照你的，不猜）。花錢給支出的細項、收入給收入的、轉帳給轉帳的，照大類分組。
  const syncCats = () => {
    const k = current();
    draft.category = categoryOptions(cat, ref, baseKind(k), draft.category,
      baseKind(k) === "expense" ? "自動猜類別" : "不指定類別");
    suggest(suggestions, baseKind(k) === "expense" ? draft.category : "");
    kind.title = k.help || "";
    kindHelp.textContent = `${k.name}：${k.help || ""}`;
    syncExtras();
  };
  const sync = () => {
    const k = current();
    const transfer = Boolean(k.purpose);
    item.placeholder = k.desc;
    item.setAttribute("aria-label", k.label);
    itemField.firstChild.textContent = k.label;
    accountField.firstChild.textContent = k.payLabel || (transfer ? "從" : "帳戶");
    accountField.hidden = Boolean(k.noAccount);                  // 別人幫我墊：錢不是從我的帳戶出去的
    const payers = ref.accounts.filter((a) => transfer ? (a.kind !== "credit_card" || k.id === "lend") : PAYABLE.includes(a.kind));   // 代墊常常是刷卡先付的，所以可以選信用卡；其他轉帳不行
    if (!payers.some((a) => a.name === draft.account)) draft.account = ref.default_account;       // 換種類後上一次選的帳戶不能用了（信用卡→收回）：退回預設，不要留著
    accountOptions(account, payers, draft.account);
    const fixed = Boolean(k.fixedTo || k.fixedFrom);
    if (transfer && !fixed) syncTo();
    toField.hidden = !transfer || fixed;
    syncCats();
  };
  let busy = false;                                       // 連點兩下不能記兩筆（代墊款會變負的）
  const submit = async (ev) => {
    ev.preventDefault();
    if (busy) return;
    const k = current();
    const who = item.value.trim();                      // 清空表單之前先拿起來（提示文字要用）
    const value = toMinor(amount.value);                // 照帳本的小數位數換成最小單位（dom.js）
    if (!Number.isInteger(value) || value <= 0) { app.toast(amountHint()); amount.focus(); return; }
    const fixed = Boolean(k.fixedTo || k.fixedFrom);
    if (k.purpose && !fixed && !to.value) { app.toast("先在設定加一個要轉進去的帳戶"); return; }
    const body = { amount: value, desc: item.value.trim() || (k.purpose ? k.name : ""), account: account.value,
      date: day.value || today, kind: baseKind(k) };
    if (k.nature) body.nature = k.nature;
    if (time.value) body.time = time.value;
    if (cat.value) body.category = cat.value;
    if (forP.select.value && !forP.node.hidden) body.for_period = forP.select.value;
    if (k.purpose) Object.assign(body, { to: to.value, purpose: k.purpose });
    if (k.fixedTo) Object.assign(body, { to: k.fixedTo, desc: k.name, counterparty: who });          // 代墊：對象寫在細節
    if (k.fixedFrom) Object.assign(body, { to: account.value, account: k.fixedFrom, desc: k.name, counterparty: who });
    if (k.iou) {                                                  // 欠別人、還別人：方向＋對象，帳戶程式自己建
      if (!who) { app.toast(`先填「${k.label}」（只寫名字，例：小華）`); item.focus(); return; }
      Object.assign(body, { kind: "iou", direction: k.iou, counterparty: who, desc: "" });
      delete body.to; delete body.purpose;
      if (k.noAccount) delete body.account;
    }
    busy = true;
    submitBtn.disabled = true;
    try {
      if (!linkField.hidden && linkBox.checked) {            // 先把類別對上這一格，再記（之後你自己打也會對上）
        await api(`/api/bucket/${opts.link.bucket}`, { method: "PATCH", body: { add_categories: [cat.value] } });
      }
      const r = await api("/api/txn", { method: "POST", body });
      const res = r.results[0] || {};
      amount.value = ""; item.value = "";
      if (opts.persist) { draft.category = ""; draft.time = ""; }          // 下一筆重新猜，日期和帳戶留著
      const when = body.date === today ? "今天 " : `${md(body.date)} `;
      const what = k.iou ? `${k.name} ${num(value)}（${who}）` : k.purpose ? `${k.name} ${num(value)}（${who ? who + "・" : ""}${body.account} → ${body.to}）`
        : `${res.category || "待分類"} ${num(res.amount || value)}${body.desc ? `（${body.desc}）` : ""}`;
      const notes = (r.warnings || []).filter((x) => !/重複/.test(x)).slice(0, 2);       // 額度提醒：做到了、超過了
      if (opts.onDone) opts.onDone(r);
      await app.afterWrite(r, `記好了：${when}${what}${notes.length ? `　${notes.join("；")}` : ""}`, r.batch_id);
    } catch (e) { app.toast(errorText(e)); } finally { busy = false; submitBtn.disabled = false; }
  };
  kind.addEventListener("change", () => { draft.kind = kind.value; sync(); });
  account.addEventListener("change", () => { draft.account = account.value; if (!toField.hidden) syncTo(); });
  to.addEventListener("change", () => { draft.to = to.value; });
  cat.addEventListener("change", () => {
    draft.category = cat.value;
    suggest(suggestions, baseKind(current()) === "expense" ? cat.value : "");
    syncExtras();
  });
  day.addEventListener("change", () => { draft.date = day.value || today; });
  time.addEventListener("change", () => { draft.time = time.value; });
  const submitBtn = h("button", { type: "submit", class: "btn primary" }, opts.submitLabel || "記");
  const form = h("form", { class: "add fgrid", onsubmit: submit },
    field("種類", kind), field("金額", amount), kindHelp, itemField, accountField, toField, field("類別", cat), forP.node,
    field("日期", day), field("時間", time), linkField,
    h("div", { class: "f go" }, h("span", null, " "), submitBtn),
    suggestions);
  sync();
  return form;
}

// ---------------------------------------------------------------- 一行一筆

function row(app, it, ref, periodKey) {
  const cls = it.amount > 0 ? "plus" : (it.amount === 0 ? "zero" : "");
  const sign = it.amount > 0 ? "+" : (it.amount < 0 ? "−" : (it.kind === "transfer" ? "↔ " : ""));
  const shown = it.amount || (it.kind === "transfer" ? it.raw : 0);        // 繳卡費、互轉：餘額不變，但看得到搬了多少
  const key = payKey(it);
  const tip = [PAY[key], it.account && it.account !== PAY[key] ? it.account : null, it.category].filter(Boolean).join("・");
  const ahead = it.for_period ? (it.for_period > periodKey ? "預繳" : "補繳") : null;      // 期名可以直接比大小
  const tag = ahead
    ? h("em", { class: "tag", title: `現金這期付了，但算給 ${it.for_period} 那一期的計畫` }, `${ahead} ${Number(it.for_period.slice(5))}月`)
    : (it.nature === "stock" ? h("em", { class: "tag", title: "長期用：算在生活費總額，但不算在每天的日曆" }, "長期用") : null);
  const unsorted = it.kind === "expense" && (!it.category || it.category === "待分類");         // 還沒分類：標題用警示色，點開選類別
  const today = it.date === app.meta.today;                // 今天的帳換個底色、日期寫「今天」，跟之前的做區隔
  const line = h("button", { type: "button", class: `entry${unsorted ? " unsorted" : ""}${today ? " today" : ""}`, title: unsorted ? "還沒分類：點開選一個類別" : null, "aria-expanded": openId === it.id && it.id ? "true" : "false",
    onclick: () => { if (!it.id) return; openId = openId === it.id ? null : it.id; app.render(); } },
  h("span", { class: "when" }, h("span", { class: `ball ${key}`, title: tip }, iconOf(it, ref, key)),
    h("span", { class: "d" }, today ? "今天" : md(it.date))),
  h("span", { class: "what" }, h("b", null, it.title), it.detail || tag ? h("small", null, it.detail, tag) : null),
  h("span", { class: "money" }, h("span", { class: `amt ${cls}` }, sign + num(Math.abs(shown))),
    h("span", { class: "bal" }, num(it.balance))));
  return it.id && openId === it.id ? [line, editRow(app, it, ref)] : [line];
}

// 點開一筆：欄位跟「記一筆」一樣，每改一格就存一次（都可以復原）。
function editRow(app, it, ref) {
  const save = async (body, msg) => {
    try {
      const r = await api(`/api/txn/${it.id}`, { method: "PATCH", body });
      await app.afterWrite(r, msg, r.batch_id);
    } catch (e) { app.toast(errorText(e)); }
  };
  const transfer = it.kind === "transfer";
  const lent = transfer && it.purpose === "lend";                 // 代墊
  const collect = transfer && it.purpose === "collect";           // 收回
  const owed = it.kind === "expense" && it.account_kind === "loan";   // 別人幫我墊
  const withWho = lent || collect || owed || (transfer && it.purpose === "repay");
  const meta = KINDS.find((k) => (transfer ? k.purpose === it.purpose : k.id === it.kind)) || KINDS.find((k) => k.id === (transfer ? "other" : "expense"));

  const day = h("input", { type: "date", value: it.date, max: app.meta.today, "aria-label": "日期" });
  day.addEventListener("change", () => { if (day.value && day.value !== it.date) save({ date: day.value }, `日期改成 ${md(day.value)}`); });

  const time = h("input", { type: "time", value: it.time || "", "aria-label": "時間" });
  time.addEventListener("change", () => save({ time: time.value }, time.value ? `時間改成 ${time.value}` : "拿掉時間"));

  const amount = h("input", { class: "amt", inputmode: decimals() ? "decimal" : "numeric", value: major(it.raw), "aria-label": "金額" });
  amount.addEventListener("change", () => {
    const v = toMinor(amount.value);                    // 框裡是照小數位數的數字；送出去的是最小單位
    if (!Number.isInteger(v) || v <= 0) { app.toast(amountHint()); return; }
    if (v !== it.raw) save({ amount: v }, `金額改成 ${num(v)}`);
  });

  const itemLabel = withWho ? "細節" : meta.label;        // 代墊／收回：「幫誰」另外一格，這格是店家、買了什麼
  const item = h("input", { value: it.item || "", list: "item-suggest-edit", autocomplete: "off", placeholder: it.detail || meta.desc, "aria-label": itemLabel });
  const suggestions = h("datalist", { id: "item-suggest-edit" });
  item.addEventListener("change", () => {
    const v = item.value.trim();
    if (v !== (it.item || "")) save({ desc: v }, v ? `${itemLabel}改成「${v}」` : `拿掉${itemLabel}`);
  });

  // 畫面上的細節＝品項＋店家＋備註湊起來的；每一段都要有自己的欄位能改（不然會「看得到、改不到」）
  const merchant = h("input", { value: it.merchant || "", autocomplete: "off", placeholder: "例：範例便當店", "aria-label": "店家" });
  merchant.addEventListener("change", () => {
    const v = merchant.value.trim();
    if (v !== (it.merchant || "")) save({ merchant: v }, v ? `店家改成「${v}」` : "拿掉店家");
  });
  const memo = h("input", { value: it.memo || "", autocomplete: "off", placeholder: "例：路口那一家", "aria-label": "備註" });
  memo.addEventListener("change", () => {
    const v = memo.value.trim();
    if (v !== (it.memo || "")) save({ note: v }, v ? `備註改成「${v}」` : "拿掉備註");
  });

  const who = h("input", { value: it.counterparty || "", autocomplete: "off", placeholder: meta.desc, "aria-label": meta.label });
  who.addEventListener("change", () => {
    const v = who.value.trim();
    if (v !== (it.counterparty || "")) save({ counterparty: v }, v ? `${meta.label}：${v}` : `拿掉${meta.label}`);
  });

  // 代墊的開關：這筆其實是幫別人先付（要報帳）。開了＝不算你的花費，錢記在「代墊款」等對方還；卡費照算。
  const advanceBox = h("input", { type: "checkbox", checked: lent });
  advanceBox.addEventListener("change", () => save({ advance: advanceBox.checked },
    advanceBox.checked ? "改成代墊：不算你的花費，錢記在「代墊款」等對方還你（總覽會提醒你去報帳）" : "改回花錢"));
  const advanceNode = h("label", { class: "f wide check", title: "幫別人先付的錢（例：公司的訂閱先用你的卡刷）。開了：不算你的花費、不進生活費，錢記在「代墊款」，總覽會提醒你去處理報帳；信用卡的卡費照算。" },
    advanceBox, h("span", null, "代墊：幫別人先付，要報帳"));
  advanceNode.hidden = !(lent || (it.kind === "expense" && !it.financed));
  const account = h("select", { "aria-label": transfer ? "從" : "帳戶" });
  accountOptions(account, ref.accounts.filter((a) => transfer ? (a.kind !== "credit_card" || lent) : PAYABLE.includes(a.kind)), it.account);
  account.addEventListener("change", () => { if (account.value !== it.account) save({ account: account.value }, `帳戶改成「${account.value}」`); });

  let to = null;
  if (transfer) {
    to = h("select", { "aria-label": "轉到" });
    accountOptions(to, ref.accounts.filter((a) => a.name !== it.account), it.to_account);
    to.addEventListener("change", () => { if (to.value !== it.to_account) save({ to: to.value }, `轉到「${to.value}」`); });
  }

  const cat = h("select", { "aria-label": "類別" });
  const shownCat = categoryOptions(cat, ref, transfer ? "transfer" : it.kind, it.category, transfer || it.kind === "income" ? "不指定類別" : "待分類");
  cat.addEventListener("change", () => {
    if (cat.value !== shownCat) save({ category: cat.value }, cat.value ? `改成「${cat.value}」` : "拿掉類別");
  });
  if (it.kind === "expense") suggest(suggestions, shownCat);

  const forP = periodField(ref);
  forP.setChosen(it.for_period || "");
  forP.node.hidden = !prepayable(ref, it.kind, shownCat);
  forP.select.addEventListener("change", () => {
    if (forP.select.value !== (it.for_period || "")) {
      save({ for_period: forP.select.value }, forP.select.value ? `算進 ${forP.select.value} 那一期` : "改回照付款日算");
    }
  });

  const natureField = h("select", { "aria-label": "性質" },
    h("option", { value: "", selected: it.nature !== "stock" }, "日常"), h("option", { value: "stock", selected: it.nature === "stock" }, "長期用"));
  const living = it.kind === "expense" && !it.financed && ((ref.category_bucket || {})[shownCat] ? (ref.category_bucket[shownCat].living) : true);
  const natureNode = field("性質", natureField);
  natureNode.hidden = !living;
  natureNode.title = "日常＝算在今天的日曆；長期用＝這個月（或更久）才慢慢用掉的（囤貨、3C），算在生活費總額但不算在每天。";
  natureField.addEventListener("change", () => {
    if (natureField.value !== (it.nature || "")) save({ nature: natureField.value }, natureField.value ? "標成長期用" : "改回日常");
  });

  const del = h("button", { type: "button", class: "btn danger", onclick: async () => {
    try {
      const r = await api(`/api/txn/${it.id}`, { method: "DELETE" });
      openId = null;
      await app.afterWrite(r, `刪掉了：${it.title}${it.detail ? `（${it.detail}）` : ""}`, r.batch_id);
    } catch (e) { app.toast(errorText(e)); }
  } }, "刪掉");

  return h("div", { class: "entry-edit fgrid" },
    field("金額", amount), withWho ? field(owed ? "誰幫你付的" : meta.label, who, "wide") : null, field(itemLabel, item, "wide"),
    it.kind === "expense" ? field("店家", merchant) : null, field("備註", memo, it.kind === "expense" ? "" : "wide"),
    field(transfer ? "從" : "帳戶", account), transfer ? field("轉到", to) : null, field("類別", cat), natureNode, forP.node,
    field("日期", day), field("時間", time), advanceNode,
    h("div", { class: "f go" }, h("span", null, " "), del), suggestions);
}
