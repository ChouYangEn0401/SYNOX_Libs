// 規劃：只填數字和規則——薪水怎麼分（每一格的金額、🔒 一定要做到、↻ 滾存、固定項目）、可以花的錢怎麼分（大類共用上限、細項單項上限）。
// 東西長什麼樣（加格子、加類別、改名、換圖示、停用、哪些類別算進哪一格）在「設定」。
// 從原本的畫面原樣搬來（記帳 0.4.0，W5）；改的只有：收合的開關改記在這次打開的畫面裡（不用瀏覽器儲存）、去掉個人用語。
import { api, errorText } from "../api.js";
import { h, num } from "../dom.js";
import { cardHead, field, intOrNull, money, moneyInput, moneyOrNull, numInput, select, send, toMoney } from "../forms.js";

const FLOW = { expense: "花錢", income: "收入", transfer: "轉存" };

// ⓘ 的內容：每一段一句話，不要專有名詞。
const INFO = {
  plan: ["薪水怎麼分", [
    "薪水進來先分給這些「格子」：固定支出、存錢、投資、還債、捐贈……分完才知道剩多少可以花。",
    "🔒＝這一格「一定要做到」：到期末還沒做到，總覽會亮燈（最後一週變紅燈）。沒鎖的就只是計畫，少做不會唸你。",
    "預設鎖住的是固定支出——答應過的、跑不掉的；存錢和投資沒鎖（某個月少一點是正常的）；",
    "生活費不能鎖，它本來就是拿來花的。要鎖哪些由你決定，在「設定」的格子裡切換。",
    "「生活費」預設等於下面「可以花的錢怎麼分」各大類加起來，會跟著動；你也可以直接填一個數字，那就是你自己決定的。",
    "每一格底下可以列「固定項目」（房租、手機費、訂閱、電費…）：這一格的金額是總額，項目是它的明細，不是另外再加一份；",
    "「其他」＝總額減掉項目加起來，就是金額不一定的那些（電費、雜支）。項目會每個月出現，到總覽按「今天處理」就記好。",
    "↻＝滾存（自己加的格子才有）：每個月撥的額度，沒用完的留到下期，超過的下期補。給不是每個月都來的帳單用（水電瓦斯）。",
    "滾存的格子，總覽的「要留的錢」會把留下來的也算進去，你花不到。每期在總覽那一格按「撥額度」，把這期的錢移出日常帳戶（轉到別的帳戶，或預繳給對方），格子上會寫「已撥 N → 去向」。",
    "要加一格、改名字、停用、指定哪些記帳類別算進這一格（🔗），到「設定」的「類別」。",
  ]],
  caps: ["可以花的錢怎麼分", [
    "生活費底下分成幾個大類，每個大類是一個「共用上限」：例如娛樂 2,500，底下的電影、展覽、遊戲都從這 2,500 花。",
    "細項那欄是「大概多少」的軟上限，三種寫法：每月（娛樂一個月 2,000）、每天（午餐每天 150）、每次（每一次晚餐 500）。",
    "寫了也不會擋你、不會亮燈——超過的時候只在記下去那一刻輕輕說一句，",
    "它只是給你自己和月檢討當參考——真的花到誇張的時候，月檢討會講。不想寫就空著。",
    "這裡只填上限。要加大類或細項、改名、換圖示、停用，到「設定」的「類別」。",
  ]],
};

export async function render(app) {
  const [d, ref] = await Promise.all([api("/api/setup", { params: { period: app.period } }), app.ref()]);
  app.setNav(d);
  return h("div", { class: "page-inner" }, planCard(app, d, ref), capsCard(app, d));
}

// ---------------------------------------------------------------- 固定項目（放在「薪水怎麼分」每一格底下）

// 總覽的「一鍵記下固定項目」也用它（home.js import）。
export async function postRecurring(app) {
  try {
    const r = await api("/api/recurring/post", { method: "POST", body: {} });
    await app.afterWrite(r, r.created.length ? `記好了：${r.created.map((c) => c.name).join("、")}` : "沒有要記的", r.batch_id);
  } catch (e) { if (e.code !== "cancelled") app.toast(errorText(e)); }
}

function recurringForm(app, d, ref, r, preset = {}) {
  const isNew = !r;
  const presetBucket = preset.bucket || "";
  const transferBucket = { saving: "saving", invest: "investing" }[presetBucket];
  const cur = r || { kind: preset.kind || (transferBucket ? "transfer" : "expense"), day_of_month: 5, amount: null,
    purpose: transferBucket, bucket_id: presetBucket };
  const name = h("input", { value: cur.name || "", placeholder: "例：房租" });
  const kind = select(Object.entries(FLOW), cur.kind);
  kind.disabled = !isNew;
  const amount = moneyInput(cur.amount, "空白＝每次不一樣（例：電費）");
  const day = numInput(cur.day_of_month, "1–31");
  const accounts = d.accounts.map((a) => [a.name, a.name]);
  const account = select([["", "（預設）"], ...accounts], cur.account || "");
  const to = select([["", "（選一個）"], ...accounts], cur.to_account || "");
  const category = h("select", null, h("option", { value: "" }, "（自己猜）"),
    ((ref && ref.categories) || []).map((g) => h("optgroup", { label: g.group }, g.items.map((n) => h("option", { value: n, selected: n === cur.category }, n)))));
  const purpose = select([["saving", "存錢"], ["investing", "投資"], ["prepay", "預付（以後要用的錢先放到別處）"], ["card_payment", "繳卡費"], ["other", "就只是搬錢"]],
    cur.purpose || "saving");
  const cadence = select([["monthly", "每個月"], ["bimonthly", "兩個月一次"]], cur.cadence || "monthly");
  const note = h("input", { value: cur.note || "", placeholder: "例：繳費代碼（可不填）" });
  const purposeField = field("這筆是在做什麼", purpose);
  const toField = field("存到哪個帳戶", to);
  const catField = field("類別", category);
  const accField = field("從哪個帳戶", account);
  const bucketSel = select([["", "（照類別自己對）"], ...d.buckets.filter((b) => b.id !== "living").map((b) => [b.id, b.name])],
    cur.bucket_id || "");
  const bucketField = field("屬於哪一格（薪水怎麼分）", bucketSel);
  bucketField.title = "固定項目是那一格的明細：房租放固定支出、定期存款放存錢。不選的話，照上面的類別自己對。";
  const sync = () => {
    toField.hidden = kind.value !== "transfer";
    purposeField.hidden = kind.value !== "transfer";
    catField.hidden = kind.value === "transfer";
    accField.firstChild.textContent = kind.value === "income" ? "進哪個帳戶" : "從哪個帳戶";
  };
  kind.addEventListener("change", sync);
  sync();
  const body = () => {
    const out = { name: name.value.trim(), amount: moneyOrNull(amount, "金額"), day_of_month: intOrNull(day, "幾號") ?? 5,
      account: account.value, category: kind.value === "transfer" ? "" : category.value,
      cadence: cadence.value, note: note.value.trim() };
    if (kind.value === "transfer") Object.assign(out, { to: to.value, purpose: purpose.value });
    if (kind.value !== "income") out.bucket = bucketSel.value;
    if (isNew) out.kind = kind.value;
    return out;
  };
  app.openModal({
    title: isNew ? (presetBucket ? `加一個固定項目：${(d.buckets.find((b) => b.id === presetBucket) || {}).name || ""}` : "加固定項目") : cur.name,
    body: [field("名字", name), h("div", { class: "form-grid" }, field("種類", kind), field("每月幾號", day)),
      h("div", { class: "form-grid" }, field("金額", amount), field("多久一次", cadence)),
      accField, toField, purposeField, catField, bucketField, field("備註", note)],
    foot: [
      isNew ? null : h("button", { type: "button", class: "btn danger", onclick: () => send(app,
        () => api(`/api/recurring/${cur.id}`, { method: "PATCH", body: { active: false } }), `${cur.name} 停掉了`) }, "停掉"),
      h("button", { type: "button", class: "btn primary", onclick: () => send(app,
        () => api(isNew ? "/api/recurring" : `/api/recurring/${cur.id}`, { method: isNew ? "POST" : "PATCH", body: body() }),
        isNew ? `加好了：${name.value.trim()}` : "改好了") }, "存")],
  });
}

// ---------------------------------------------------------------- 薪水怎麼分

function planCard(app, d, ref) {
  const plan = d.plan;
  const names = Object.fromEntries(d.buckets.map((b) => [b.id, b.name]));
  const info = Object.fromEntries(d.buckets.map((b) => [b.id, b]));
  const ORDER = d.buckets.filter((b) => b.id !== "living").map((b) => b.id);   // 用途照資料庫，不寫死
  const income = money(plan.income_expected, "每個月收入");
  const inputs = Object.fromEntries(ORDER.map((b) => [b, money(plan.buckets[b], names[b])]));
  // 生活費可以自己填一個數字；下面的大類只是軟性分配，加起來比生活費少很正常（那就是彈性）。
  const livingInput = money(plan.buckets.living, "生活費");
  const assigned = d.plan.assigned_to_groups || 0;
  const livingNote = h("span", { class: "muted small" });
  const pct = Object.fromEntries([...ORDER, "living"].map((b) => [b, h("span", { class: "pct" })]));
  const status = h("div", { class: "note-line" });
  // 分兩組：鎖起來的（🔒）＝不能少，沒鎖的＝可以少。
  const mustDo = ORDER.filter((b) => info[b].locked);
  const canFlex = ORDER.filter((b) => !info[b].locked);
  const sums = { must: h("span", { class: "muted small" }), flex: h("span", { class: "muted small" }) };
  // 三組可以收合：收合時只看組名和合計；開關記在這次打開的畫面裡。組名自己改（✎）。
  const groups = d.group_names || { must: { name: "一定要做到", tip: "" }, flex: { name: "有餘力再做", tip: "" }, living: { name: "剩下可以花的", tip: "" } };
  const fold = loadFold(app);
  for (const k of ["must", "flex"]) if (typeof fold[k] !== "boolean") fold[k] = true;   // 預設收起來：只看組名和合計
  const members = { must: [], flex: [] };
  const groupRow = (key, sum) => {
    const g = groups[key];
    const arrow = h("span", { class: "fold-arrow", "aria-hidden": "true" }, key in members && fold[key] ? "▸" : "▾");
    const toggle = key in members ? h("button", { type: "button", class: "grp-toggle", "aria-expanded": fold[key] ? "false" : "true",
      onclick: () => {
        fold[key] = !fold[key];
        members[key].forEach((el) => { el.hidden = fold[key]; });
        arrow.textContent = fold[key] ? "▸" : "▾";
        toggle.setAttribute("aria-expanded", fold[key] ? "false" : "true");
      } }, arrow, h("b", null, g.name)) : h("b", null, g.name);
    return h("div", { class: "grp" },
      toggle,
      h("button", { type: "button", class: "x", title: `改「${g.name}」這一組的名字`, "aria-label": `改${g.name}的名字`,
        onclick: () => renameGroup(app, key, g) }, "✎"),
      h("span", { class: "muted small" }, g.tip), sum);
  };

  const recalc = () => {
    const inc = toMoney(income.value);
    const living = toMoney(livingInput.value) || 0;
    const left = living - assigned;
    const follows = d.plan.living_follows && living === d.plan.buckets.living;
    livingNote.textContent = follows ? "＝下面大類加起來（想自己決定就直接改數字）" : living
      ? `下面大類分了 ${num(assigned)}` + (left > 0 ? `，還有 ${num(left)} 沒分（彈性）` : (left < 0 ? `，比生活費多 ${num(-left)}` : "，剛好分完"))
      : "填一個數字，例如 15000";
    const amounts = { ...Object.fromEntries(ORDER.map((b) => [b, toMoney(inputs[b].value) || 0])), living };
    const sumOf = (list) => list.reduce((t, b) => t + (amounts[b] || 0), 0);
    sums.must.textContent = mustDo.length ? `合計 ${num(sumOf(mustDo))}` : "";
    sums.flex.textContent = canFlex.length ? `合計 ${num(sumOf(canFlex))}` : "";
    for (const [b, el] of Object.entries(pct)) el.textContent = inc ? `${Math.round((amounts[b] / inc) * 100)}%` : "";
    const total = Object.values(amounts).reduce((s, v) => s + v, 0);
    if (!inc) { status.className = "note-line"; status.textContent = `分出去 ${num(total)}（先填每個月收入）`; }
    else if (total === inc) { status.className = "note-line good"; status.textContent = "剛好分完 ✓"; }
    else if (total < inc) { status.className = "note-line"; status.textContent = `還有 ${num(inc - total)} 沒分`; }
    else { status.className = "note-line bad"; status.textContent = `分超過了：比收入多 ${num(total - inc)}`; }
  };
  for (const el of [income, livingInput, ...Object.values(inputs)]) el.addEventListener("input", recalc);
  recalc();

  const savePlan = async () => {
    const bad = [income, livingInput, ...Object.values(inputs)].find((el) => Number.isNaN(toMoney(el.value)));
    if (bad) { app.toast("金額要是整數"); bad.focus(); return; }
    try {
      const r = await api("/api/plan", { method: "PUT", body: {
        period: d.period.key, income_expected: toMoney(income.value) || null,
        buckets: { ...Object.fromEntries(ORDER.map((b) => [b, toMoney(inputs[b].value)])), living: toMoney(livingInput.value) },
      } });
      await app.afterWrite(r, r.batch_id ? "存好了" : "沒有改到東西", r.batch_id);
    } catch (e) { if (e.code !== "cancelled") app.toast(errorText(e)); }
  };

  // 固定項目：這一格的明細。「其他」＝總額減掉項目加起來（電費這種金額不一定的）；項目比總額多就標紅。
  const itemsOf = (b) => d.recurring.filter((r) => r.bucket === b && r.kind !== "income");
  const others = {};
  const itemRow = (r) => h("button", { type: "button", class: "fi", title: `${r.name}：${r.state}（點一下修改）`,
    onclick: () => recurringForm(app, d, ref, r) },
  h("span", { class: "nm" }, r.name),
  h("span", { class: "am num" }, r.amount === null ? "金額不一定" : num(r.amount)),
  h("span", { class: "dy muted" }, `${r.day_of_month} 號`),
  // 這一期記了沒：寫成字（✓ 已記／還沒記），不是只有一個勾
  h("span", { class: `st ${r.level}`, title: r.state }, r.level === "good" ? "✓ 已記" : (r.level === "warn" ? "還沒記" : "")));
  const itemsBlock = (b) => {
    const items = itemsOf(b);
    const plus = h("button", { type: "button", class: "fi add",
      title: `在「${names[b]}」底下加一個固定項目（例：房租、手機費）`,
      onclick: () => recurringForm(app, d, ref, null, { bucket: b, kind: "expense" }) }, "＋ 加固定項目");
    if (!items.length) return h("div", { class: "fixed-items" }, plus);
    others[b] = h("div", { class: "fi other", title: "這一格的總額減掉上面項目加起來：金額不一定的（電費、雜支）都在這裡。" });
    return h("div", { class: "fixed-items" }, items.map(itemRow), others[b], plus);
  };
  const refreshOthers = () => {
    for (const [b, el] of Object.entries(others)) {
      const planned = itemsOf(b).reduce((sum, r) => sum + (r.amount || 0), 0);
      const diff = (toMoney(inputs[b].value) || 0) - planned;
      el.className = `fi other${diff < 0 ? " bad" : ""}`;
      el.textContent = diff < 0 ? `項目加起來 ${num(planned)}，比這一格多 ${num(-diff)}` : `其他（金額不一定的）${num(diff)}`;
    }
  };
  for (const el of Object.values(inputs)) el.addEventListener("input", refreshOthers);
  const addItem = (b, kind) => h("button", { type: "button", class: "x plus", title: kind === "income" ? "加一個固定的收入（薪水）" : `在「${names[b]}」底下加一個固定項目（例：房租、手機費）`,
    "aria-label": `加固定項目到${names[b] || "收入"}`, onclick: () => recurringForm(app, d, ref, null, { bucket: kind === "income" ? "" : b, kind }) }, "＋");
  const incomeItems = d.recurring.filter((r) => r.kind === "income");
  const orphans = d.recurring.filter((r) => !r.bucket && r.kind !== "income");

  const line = (label, input, p) => [h("span", null, label), input, p];
  // 一列只有「名字－金額－%」。🔒 一定要做到、↻ 滾存、🔗 算哪些類別都在「設定」的格子裡改。
  const bucketRows = (b, key) => {
    const els = [...line(h("span", { class: "nm" }, names[b]), inputs[b], pct[b]), itemsBlock(b)];
    els.forEach((el) => { el.hidden = Boolean(fold[key]); members[key].push(el); });
    return els;
  };
  const card = h("section", { class: "card" },
    cardHead(app, "薪水怎麼分", INFO.plan),
    h("p", { class: "muted small" }, `從這個月開始，之後每個月都照這個分。點組名可以收起來／打開；✎ 改組名。哪一格在「${groups.must.name}」看 🔒（在「設定」的格子裡勾），開滾存、加格子也在那裡。`),
    h("div", { class: "plan-table" },
      line(h("span", { class: "with-lock" }, h("b", null, "每個月收入"), addItem("", "income")), income, h("span")),
      incomeItems.length ? h("div", { class: "fixed-items" }, incomeItems.map(itemRow)) : null,
      mustDo.length ? groupRow("must", sums.must) : null,
      mustDo.flatMap((b) => bucketRows(b, "must")),
      canFlex.length ? groupRow("flex", sums.flex) : null,
      canFlex.flatMap((b) => bucketRows(b, "flex")),
      groupRow("living", h("span")),
      line(h("span", null, names.living, livingNote), livingInput, pct.living)),
    status,
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn primary", onclick: savePlan }, "存起來")),
    orphans.length ? h("div", { class: "fixed-items orphans" }, h("p", { class: "muted small" }, "還沒歸到哪一格的固定項目（點開，選「屬於哪一格」）"),
      orphans.map(itemRow)) : null);
  refreshOthers();
  return card;
}

// 收合的開關記在這次打開的畫面（app 物件）裡：重畫還在，關掉畫面就回到預設（SYNOX 的畫面不用瀏覽器儲存）。
function loadFold(app) {
  if (!app.planFold) app.planFold = {};
  return app.planFold;
}

function renameGroup(app, key, g) {
  const name = h("input", { value: g.name, "aria-label": "組的名字", placeholder: g.default });
  app.openModal({
    title: "這一組的名字",
    body: [field("名字（最多 10 個字；空白＝回到「" + g.default + "」）", name)],
    foot: [h("button", { type: "button", class: "btn primary", onclick: () => send(app,
      () => api("/api/plan/groups", { method: "PATCH", body: { [key]: name.value.trim() } }), "組名改好了") }, "存")],
  });
}

// ---------------------------------------------------------------- 可以花的錢怎麼分（共用上限＋單項上限）

// 預設只看得到「大類的共用上限」和「已經設了單項上限的細項」。其他細項收起來，要設才點開。
// 每天只要回答「還能不能花」一個數字；單項上限是用來擋會失控的那一兩項，不是每項都要管。
// 這裡只填數字：加大類、加細項、改名、停用都在設定頁。
function capsCard(app, d) {
  const open = app.capsOpen || (app.capsOpen = {});
  const rows = [];
  for (const g of d.caps) {
    const cap = money(g.budget, `${g.name}的共用上限`);
    cap.addEventListener("change", () => send(app, () => api("/api/plan", { method: "PUT", body: {
      period: d.period.key, group_budgets: { [g.name]: toMoney(cap.value) } } }), `${g.name}改成 ${num(toMoney(cap.value))}`));
    const used = g.budget
      ? h("span", { class: `muted small${g.over ? " bad" : ""}` }, `已花 ${num(g.spent)}／${num(g.budget)}`)
      : h("span", { class: "muted small" }, `已花 ${num(g.spent)}`);
    const capped = g.categories.filter((c) => c.cap);
    const rest = g.categories.filter((c) => !c.cap);
    const toggleBtn = rest.length ? h("button", { type: "button", class: "btn small",
      onclick: () => { open[g.name] = !open[g.name]; app.render(); } },
    open[g.name] ? "收起來" : `還有 ${rest.length} 個細項，要設上限再點開`) : null;
    rows.push(h("div", { class: "cap-group" },
      h("div", { class: "cap-head" },
        h("b", null, g.icon ? h("span", { class: "ico" }, g.icon) : null, g.name), used, cap),
      capped.length ? h("div", { class: "cap-items" }, capped.map((c) => capRow(app, c))) : null,
      open[g.name] && rest.length ? h("div", { class: "cap-items" }, rest.map((c) => capRow(app, c))) : null,
      toggleBtn ? h("div", { class: "cap-more" }, toggleBtn) : null));
  }
  return h("section", { class: "card" },
    cardHead(app, "可以花的錢怎麼分", INFO.caps),
    h("p", { class: "muted small" }, "平常只要顧大類這幾個數字就好。細項那欄是「大概多少」的參考值（軟上限），超過不會唸你，誇張的時候月檢討會講。"),
    rows.length ? rows : h("p", { class: "muted small" }, "還沒有大類。到「設定」的「類別」加一個，例如「娛樂」。"),
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn", onclick: () => app.go("setup") }, "加大類、加細項 → 設定")));
}

function capRow(app, c) {
  const cap = money(c.cap, `${c.name}的單項上限`);
  // 上限可以是「每月」「每天」或「每次」：三餐這種本來就是每天抓，這一期的上限＝每天 × 天數。
  const basis = select([["period", "每月"], ["day", "每天"], ["each", "每次"]], c.cap_basis || "period");
  const sendCap = () => send(app, () => api(`/api/category/${c.id}`, { method: "PATCH", body: {
    default_budget: toMoney(cap.value) || null, cap_basis: basis.value } }),
  toMoney(cap.value) ? `${c.name}：${{ day: "每天", each: "每次" }[basis.value] || "每月"} ${num(toMoney(cap.value))}` : `${c.name}不設上限`);
  cap.addEventListener("change", sendCap);
  basis.addEventListener("change", sendCap);
  // 軟上限：只寫「花了多少／大概多少」，不標紅。超過一點是常態，誇張的交給月檢討講。
  const spent = c.cap_basis === "each" && c.cap
    ? h("span", { class: "muted small" }, `每次 ${num(c.cap)}` + (c.over_each ? `・超過 ${c.over_each} 次` : ""))
    : (c.cap_period
      ? h("span", { class: "muted small" }, `${num(c.spent)}／大概 ${num(c.cap_period)}`)
      : h("span", { class: "muted small" }, num(c.spent)));
  return h("div", { class: "cap-item" },
    h("span", { class: "name" }, c.icon ? h("span", { class: "ico" }, c.icon) : null, c.name), spent, cap, basis);
}
