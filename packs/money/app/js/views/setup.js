// 設定：東西長什麼樣——類別三層（用途 → 大類 → 細項：加、改名、換圖示、搬、停用、哪些類別算進哪一格）、帳戶、
// 帳本設定（幣別、起算日、時差、提醒）、選配功能、帳本。數字（每一格多少錢、上限、固定項目）在「規劃」。
// 每張卡右上角有 ⓘ：一句白話說這張卡在做什麼。
// 從原本的畫面原樣搬來（記帳 0.4.0，W5）；改的：系統用的類別也可以改名、帳本卡改成 SYNOX 的下載（從檔案匯入這一版沒有）、
// 多了「帳本設定」「選配功能」兩張卡、去掉個人用語。
import { api, download, errorText } from "../api.js";
import { count, h, num, setDecimals } from "../dom.js";
import { cardHead, field, intOrNull, moneyInput, moneyOrNull, numInput, select, send } from "../forms.js";

const KIND = { cash: "現金", bank: "銀行", credit_card: "信用卡", securities: "證券", e_wallet: "電子錢包", receivable: "代墊（別人欠我的）", loan: "貸款", pension: "退休金" };
const WEEKDAYS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"];

const INFO = {
  tree: ["類別", [
    "三層：最上面是「格子」（薪水分出去的用途：固定支出、存錢、生活費、你自己加的房租、手機費…），",
    "格子底下是「大類」（食、住、行…），大類底下是「細項」——就是記帳時選的類別（早餐、午餐、房租…）。",
    "每一層都能加、改名、換圖示、停用（停用不會刪掉以前的紀錄）。點細項：改名字、圖示、搬到別的大類、指定「算在哪一格」、關鍵字。",
    "🔗＝這一格算進哪些類別：例如「手機費」那格對應「手機網路」，記手機網路就會算進手機費那格，總覽就會打勾。",
    "↳＝把自己加的格子併進別的格子，變成它底下的固定項目。✕＝這一格以後不用了。",
    "有些類別是系統記帳會用到的（借出、房租、繳卡費、三餐…）：可以改名、換圖示、改關鍵字，但不能停用、不能換種類。",
    "圖示：在視窗裡點一個就好（或在圖示欄自己打一個 emoji）。名字就只是名字。",
    "每一格多少錢、上限、🔒、↻ 滾存、固定項目，在「規劃」。",
  ]],
  accounts: ["帳戶", [
    "錢放在哪裡：現金、銀行、信用卡、電子錢包。記一筆時選「用什麼付」，系統才知道哪裡的錢變少。",
    "銀行帳戶可以設「底線」（低於就亮燈）和「目標」。",
    "信用卡設結帳日、繳款日、從哪個帳戶扣——總覽才算得出銀行這個月要留多少錢繳卡費。",
  ]],
  settings: ["帳本設定", [
    "幣別：一本帳一種幣別。小數位數只有在還沒記帳的時候能改（改了每一筆金額的意思都會變）。",
    "每期從幾號開始：通常是發薪日。改了只影響之後的期，以前的期不動。",
    "時差：空白＝跟著這台電腦；人在別的時區時填幾小時（例：8、-5）。",
    "午餐提醒、每週報告：選星期幾。會浮動的格子（水電、訂閱）差多少以內算做完。",
  ]],
  features: ["選配功能", [
    "不是每個人都用得到的格子與類別：捐贈、投資、還債、薪資扣繳、資金操盤。",
    "按「打開」就加上那幾個大類與細項，並打開那一格；按兩次不會多。",
    "不想用了：到上面的「類別」把那一格或那些類別停用（以前記的都留著）。",
  ]],
  book: ["帳本", [
    "你的帳是一個檔案，放在你自己的資料夾，沒有加密。",
    "每天第一次記帳前會自動備份一份（也沒有加密），留最近 14 天。",
    "可以整本下載成一個試算表檔（CSV，每一列是一筆資料），或開一本全新的（開新的之前會先把現在這本封存起來，不會刪掉）。",
  ]],
};

export async function render(app) {
  const d = await api("/api/setup", { params: { period: app.period } });
  if (d.currency) setDecimals(d.currency.decimals);       // 剛改了幣別／小數位數：金額照新的換算、顯示
  app.hideNav();                                          // 結構不分期：不顯示月份
  return h("div", { class: "page-inner" }, treeCard(app, d), accountsCard(app, d), settingsCard(app, d),
    featuresCard(app, d), bookCard(app, d.book));
}

// ---------------------------------------------------------------- 類別三層：用途 → 大類 → 細項

const spots = (d) => d.buckets.filter((b) => b.active && !b.is_living);                // 能「算在」的格子（生活費是大類加起來的，不能直接認領）
const allGroups = (d) => d.catalog.flatMap((b) => b.groups.map((x) => ({ ...x, bucket: b.bucket, bucketName: b.name, living: b.is_living })));
const groupOf = (d, id, fallback) => allGroups(d).find((x) => x.id === id) || { id, name: fallback };

const chip = (app, d, c, g, extra) => h("button", { type: "button", class: `cat-chip${c.pinned ? " pinned" : ""}`,
  title: c.pinned ? `算在「${c.bucket_name}」（你指定的）。點一下修改。` : "點一下：改名字、圖示、算在哪一格",
  onclick: () => categoryModal(app, d, c, g) },
c.icon ? h("span", { class: "ico" }, c.icon) : null, c.name, extra ? h("small", null, extra) : null);

function treeCard(app, d) {
  const open = app.catalogOpen || (app.catalogOpen = {});
  const sections = d.catalog.map((b) => {
    const count = b.groups.reduce((n, g) => n + g.categories.length, 0) + b.linked.length;
    const shown = open[b.bucket] ?? !b.is_living;                                   // 固定的、自己加的先展開；生活費的項目多，收起來
    const tools = h("span", { class: "tree-tools" },
      h("button", { type: "button", class: "x", title: `「${b.name}」的設定（名字、一定要做到、滾存）`, "aria-label": `${b.name}的設定`, onclick: () => renameBucket(app, d, b) }, "✎"),
      b.is_living ? null : h("button", { type: "button", class: "x link", title: `選哪些記帳類別算進「${b.name}」`,
        "aria-label": `對應${b.name}`, onclick: () => linkCategories(app, d, b.bucket, b.name) }, "🔗"),
      b.core ? null : h("button", { type: "button", class: "x link", title: `把「${b.name}」併進別的格子，變成它的固定項目`,
        "aria-label": `併進${b.name}`, onclick: () => foldBucket(app, d, b.bucket, b.name) }, "↳"),
      b.core ? null : h("button", { type: "button", class: "x", title: `不用「${b.name}」了`, "aria-label": `停用${b.name}`,
        onclick: () => stopBucket(app, b) }, "✕"));
    const body = shown ? [
      b.groups.map((g) => h("div", { class: "cat-group" },
        h("div", { class: "cat-head" },
          h("b", null, g.icon ? h("span", { class: "ico" }, g.icon) : null, g.name),
          h("button", { type: "button", class: "x", title: `改「${g.name}」（名字、圖示、屬於哪一格）`, "aria-label": `改${g.name}`,
            onclick: () => groupModal(app, d, g, b) }, "✎")),
        h("div", { class: "cat-chips" },
          g.categories.map((c) => chip(app, d, c, g, c.pinned ? `→${c.bucket_name}` : null)),
          h("button", { type: "button", class: "cat-chip add", title: `在「${g.name}」加一個細項`, "aria-label": `加細項到${g.name}`,
            onclick: () => categoryModal(app, d, null, g) }, "＋")))),
      b.linked.length ? h("div", { class: "cat-group" },
        h("div", { class: "cat-head" }, h("span", { class: "muted small" }, "別的大類、算進這一格的")),
        h("div", { class: "cat-chips" }, b.linked.map((c) => chip(app, d, c, groupOf(d, c.group_id, c.group), `（${c.group}）`)))) : null,
      !b.groups.length && !b.linked.length ? h("p", { class: "muted small tree-empty" }, "還沒有類別算進這一格：按 🔗 選，或在下面加一個大類。") : null,
      h("div", { class: "cat-more" }, h("button", { type: "button", class: "btn small", onclick: () => groupModal(app, d, null, b) }, `＋ 在「${b.name}」加大類`)),
    ] : null;
    return h("div", { class: "cat-sec" },
      h("div", { class: "tree-head" },
        h("button", { type: "button", class: "cat-toggle", "aria-expanded": shown ? "true" : "false",
          onclick: () => { open[b.bucket] = !shown; app.render(); } },
        h("b", null, `${shown ? "▾" : "▸"} ${b.name}`), h("span", { class: "muted small" }, `${count} 個類別`)),
        tools),
      body);
  });
  return h("section", { class: "card" }, cardHead(app, "類別", INFO.tree),
    h("p", { class: "muted small" }, "格子 → 大類 → 細項（記帳時選的）。每一層都能加、改名、換圖示、停用；🔗 選哪些類別算進那一格。"),
    sections,
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn", onclick: () => addSpot(app) }, "＋ 加一格")));
}

// 自己加一個格子（例：教育、保險）。金額到「規劃」填。
function addSpot(app) {
  const name = h("input", { placeholder: "例：教育", "aria-label": "新格子的名字" });
  app.openModal({
    title: "加一格",
    body: [h("p", { class: "muted small" }, "薪水要分給它的新用途，例如教育、保險、孝親費。加好之後到「規劃」填金額。"),
      field("名字（1～8 個字）", name)],
    foot: [h("button", { type: "button", class: "btn primary", onclick: () => send(app,
      () => api("/api/bucket", { method: "POST", body: { label: name.value.trim() } }),
      `加了「${name.value.trim()}」，到規劃填金額`) }, "加進去")],
  });
  name.focus();
}

// 格子的設定：名字、🔒 一定要做到（規劃頁就是照這個分成「一定要做到／有餘力再做」兩組）、↻ 滾存、≈ 金額會浮動。
function renameBucket(app, d, b) {
  const info = (d.buckets || []).find((x) => x.id === b.bucket) || {};
  const name = h("input", { value: b.name, "aria-label": "格子的名字" });
  const locked = h("input", { type: "checkbox", checked: !!info.locked });
  const rollover = h("input", { type: "checkbox", checked: !!info.rollover });
  const rollBox = h("label", { class: "field check" }, rollover,
    h("span", null, "↻ 滾存：這期沒用完的留到下期，超過的下期補（水電瓦斯這種不是每個月都來的）"));
  if (info.core) rollBox.hidden = true;                   // 核心的六格不做滾存
  // ≈ 金額會浮動（水電、訂閱）：差不多就算做完
  const flex = h("input", { type: "checkbox", checked: !!info.flex });
  const flexBox = h("label", { class: "field check" }, flex,
    h("span", null, `≈ 金額會浮動（水電、訂閱）：跟計畫差 ${Math.round((d.flex_tolerance ?? 0.1) * 100)}% 以內就算做完，不亮燈`));
  const body = [field("名字（1～8 個字）", name),
    h("label", { class: "field check" }, locked,
      h("span", null, "🔒 一定要做到：到期末還沒做到，總覽會亮燈。沒勾的就只是計畫，少做不會唸你。")),
    rollBox, flexBox];
  if (b.bucket === "living") { body.splice(1, 3); }        // 生活費是拿來花的：不能鎖、不滾存、不用「差不多」
  app.openModal({
    title: `「${b.name}」的設定`,
    body,
    foot: [h("button", { type: "button", class: "btn primary", onclick: () => send(app,
      () => api(`/api/bucket/${b.bucket}`, { method: "PATCH", body: {
        label: name.value.trim(),
        ...(b.bucket === "living" ? {} : { locked: locked.checked }),
        ...(b.bucket === "living" || info.core ? {} : { rollover: rollover.checked }),
        ...(b.bucket === "living" ? {} : { flex: flex.checked }),
      } }), "改好了") }, "存")],
  });
  name.focus();
}

function stopBucket(app, b) {
  app.openModal({
    title: `不用「${b.name}」了？`,
    body: [h("p", null, "以後分薪水不會再列它，但以前記的、以前的分配都留著。"),
      h("p", { class: "muted small" }, "算進這一格的類別會回到原本的大類。底下自己的大類要先搬到別的格子（✎ 換「屬於哪一格」）。之後想用回來，再加一個同名的就好。")],
    foot: [h("button", { type: "button", class: "btn primary", onclick: () => send(app,
      () => api(`/api/bucket/${b.bucket}`, { method: "PATCH", body: { active: false } }), `${b.name}停用了`) }, "停用")],
  });
}

// 把自己加的用途併進別的用途底下，變成它的固定項目。金額預設「加到總額」（總額不變）；如果它本來就算在那個總額裡面，選「本來就在裡面」。
function foldBucket(app, d, bucket, name) {
  const parents = d.buckets.filter((b) => b.id !== bucket && b.id !== "living" && b.active !== false);
  const into = select(parents.map((b) => [b.id, b.name]), parents.some((b) => b.id === "fixed") ? "fixed" : parents[0]?.id);
  const accounts = d.accounts.filter((a) => a.kind !== "credit_card").map((a) => [a.name, a.name]);
  const to = select([["", "（選一個）"], ...accounts], "");
  const toField = field("定期轉到哪個帳戶", to);
  const addBox = h("input", { type: "radio", name: "fold-add", checked: true });
  const inBox = h("input", { type: "radio", name: "fold-add" });
  const sync = () => { toField.hidden = !["saving", "invest"].includes(into.value); };
  into.addEventListener("change", sync);
  sync();
  app.openModal({
    title: `把「${name}」併進…`,
    body: [h("p", { class: "muted small" }, `「${name}」會變成那一格底下的固定項目；它對應的記帳類別也一起搬過去，記帳照樣自動算進去。這一格會停用（以前的紀錄留著）。`),
      field("併進哪一格", into), toField,
      h("label", { class: "pick" }, addBox, h("span", null, "這筆錢是額外的：把它的金額加到那一格的總額（總額不變）")),
      h("label", { class: "pick" }, inBox, h("span", null, "它本來就算在那一格的總額裡面（那一格的總額不動）"))],
    foot: [h("button", { type: "button", class: "btn primary", onclick: () => send(app,
      () => api(`/api/bucket/${bucket}/fold`, { method: "POST", body: { into: into.value, add_to_parent: addBox.checked, to: to.value || null, period: d.period.key } }),
      `「${name}」併進${(parents.find((b) => b.id === into.value) || {}).name || ""}了`) }, "併進去")],
  });
}

// 選哪些記帳類別算進這一格：照大類分組勾選；已經算在別格的會寫「現在算在 X」（勾了就搬過來）；
// 大類本來就屬於這一格的類別不能取消（要搬走，改那個大類）。
function linkCategories(app, d, bucket, name) {
  const boxes = [];
  const groups = [...new Set(d.claimable.map((c) => c.group))];
  const body = [h("p", { class: "muted small" }, `記到勾選的類別，就算進「${name}」。沒勾的維持原本算在哪裡。`)];
  for (const g of groups) {
    const items = d.claimable.filter((c) => c.group === g && (!c.living || c.bucket === bucket));
    if (!items.length) continue;
    body.push(h("div", { class: "pick-group" }, h("b", { class: "small" }, g),
      items.map((c) => {
        const here = c.bucket === bucket;
        const box = h("input", { type: "checkbox", checked: here, disabled: here && !c.pinned });
        boxes.push([c.name, box, here && !c.pinned]);
        return h("label", { class: "pick" }, box, h("span", null, c.name),
          here && !c.pinned ? h("small", { class: "muted" }, "（這個大類本來就屬於這格）")
            : (!here && c.bucket_name ? h("small", { class: "muted" }, `（現在算在 ${c.bucket_name}）`) : null));
      })));
  }
  const names = () => boxes.filter(([, box, fixed]) => box.checked && !fixed).map(([n]) => n);
  const home = d.catalog.find((b) => b.bucket === bucket && b.groups.length)         // 這一格自己的大類；沒有就放固定支出的第一個大類
    || d.catalog.find((b) => b.bucket === "fixed" && b.groups.length) || d.catalog.find((b) => !b.is_living && b.groups.length);
  if (home) {
    body.push(h("div", { class: "actions" }, h("button", { type: "button", class: "btn small",
      title: "沒有你要的類別？新加一個，它會直接算進這一格（大類可以在視窗裡換）",
      onclick: () => categoryModal(app, d, null, home.groups[0], bucket) }, "＋ 沒有要的？新增一個類別")));
  }
  app.openModal({
    title: `「${name}」算進哪些類別`, body,
    foot: [h("button", { type: "button", class: "btn primary", onclick: () => send(app,
      () => api(`/api/bucket/${bucket}`, { method: "PATCH", body: { categories: names() } }), `「${name}」對應好了`) }, "存")],
  });
}

// 圖示是類別自己的一個設定：點一個就好，也可以在圖示欄自己打一個 emoji。名字就只是名字，不從名字裡猜。
const ICONS = ["🍱", "🍜", "🍔", "🍕", "🥗", "🍣", "🥟", "🍢", "🥪", "🍰", "🧋", "☕", "🍺", "🍎", "🧂",
  "🛒", "🛍️", "👕", "👟", "🎁", "💄", "🧴", "🧹", "💊", "🏥", "🦷", "💇", "🚌", "🚇", "🚕", "⛽", "🅿️",
  "🏠", "💡", "💧", "🔥", "📱", "💻", "📶", "🎮", "🎬", "🎵", "📚", "🎓", "💼", "❤️", "🌾", "🪴",
  "🐶", "✈️", "🏖️", "💍", "👶", "🎉", "💰", "🧾", "📦"];

function iconPicker(input) {
  const grid = h("div", { class: "icon-pick", role: "group", "aria-label": "點一個圖示" },
    ICONS.map((ic) => {
      const b = h("button", { type: "button", class: "ip", "aria-label": `用 ${ic} 當圖示`, title: ic }, ic);
      b.dataset.icon = ic;
      b.addEventListener("click", () => { input.value = input.value === ic ? "" : ic; mark(); });
      return b;
    }));
  const mark = () => { for (const b of grid.children) b.classList.toggle("on", b.dataset.icon === input.value); };
  input.addEventListener("input", mark);
  mark();
  return grid;
}

const iconField = (value, placeholder) => {
  const input = h("input", { value: value || "", placeholder, maxlength: "8", "aria-label": "圖示", class: "icon-input" });
  return [input, iconPicker(input)];
};

// 加或改一個細項。c＝null 是新增（g 是要放進哪個大類）；presetBucket＝新增時預先選好「算在哪一格」（對應視窗用）。
// 只有這一個視窗會建立或修改類別（規劃頁不再有自己的「加一個細項」）：上限在規劃填。
// 系統用的類別（三餐、繳卡費…）可以改名；不能停用（這裡不給「停用」鈕）、不能換種類。
function categoryModal(app, d, c, g, presetBucket = "") {
  const isNew = !c;
  const cur = c || { name: "", icon: "", keywords: [], pinned: null, system: false };
  const groups = allGroups(d);
  if (!groups.some((x) => x.name === g.name)) groups.push({ ...g, bucketName: "", living: false });    // 它的大類不在樹上：選單要有它，不然存的時候被換成第一個
  const name = h("input", { value: cur.name, placeholder: "例：手機費", "aria-label": "名字" });
  const [icon, picker] = iconField(cur.icon, "例：📱（可不填）");
  const group = select(groups.map((x) => [x.name, x.name]), g.name);
  const words = h("input", { value: (cur.keywords || []).join(","), placeholder: "例：電話費,網路費（打到這些字會自動歸到這一項）", "aria-label": "關鍵字" });
  const spot = select([["", ""], ...spots(d).map((b) => [b.id, b.name])], presetBucket || cur.pinned || "");
  const spotNote = h("small", { class: "muted" });
  const sync = () => {                                                              // 大類一換，「跟著大類」的字與能不能選都跟著換
    const here = groups.find((x) => x.name === group.value) || {};
    spot.options[0].textContent = `跟著大類（${here.bucketName || ""}）`;
    spot.disabled = Boolean(here.living);
    if (here.living) spot.value = "";
    spotNote.textContent = here.living ? "生活費底下的類別是每天的花費，不能指定到別的格子。" : "";
  };
  group.addEventListener("change", sync);
  sync();
  const payload = () => {
    const out = { icon: icon.value.trim(), keywords: words.value.trim(), bucket: spot.value, name: name.value.trim() };
    if (isNew) out.group = group.value;
    else if (group.value !== g.name) out.group = group.value;                       // 只有真的換了大類才送
    return out;
  };
  app.openModal({
    title: isNew ? (presetBucket ? `加一個類別，算進「${(spots(d).find((b) => b.id === presetBucket) || {}).name || ""}」` : `在「${g.name}」加一個細項`)
      : `改「${cur.name}」`,
    body: [
      !isNew && cur.system ? h("p", { class: "muted small" }, "這個類別是系統記帳會用到的：可以改名、換圖示、改關鍵字與算在哪一格，但不能停用。") : null,
      h("div", { class: "form-grid" }, field("名字", name), field("大類", group)),
      field("圖示（點下面一個，或自己打一個）", icon),
      picker,
      field("算在哪一格", spot), spotNote,
      field("關鍵字（可不填，逗號分開）", words),
      h("p", { class: "muted small" }, "上限（每月／每天／每次大概多少）在「規劃」填。")],
    foot: [
      isNew || cur.system ? null : h("button", { type: "button", class: "btn danger", onclick: () => send(app,
        () => api(`/api/category/${cur.id}`, { method: "PATCH", body: { active: false } }), `${cur.name}停用了`) }, "停用"),
      h("button", { type: "button", class: "btn primary", onclick: () => {
        const body = payload();
        if (!body.name) { app.toast("先打名字"); return; }
        send(app, () => (isNew ? api("/api/category", { method: "POST", body }) : api(`/api/category/${cur.id}`, { method: "PATCH", body })),
          isNew ? `加了「${body.name}」${spot.value ? `，算進「${(spots(d).find((b) => b.id === spot.value) || {}).name}」` : ""}` : `「${body.name}」改好了`);
      } }, isNew ? "加進去" : "存")],
  });
  name.focus();
}

// 加或改一個大類。g＝null 是新增（b 是預設放進哪個格子）。
function groupModal(app, d, g, b) {
  const isNew = !g;
  const name = h("input", { value: g ? g.name : "", placeholder: "例：寵物", "aria-label": "大類的名字" });
  const [icon, picker] = iconField(g ? g.icon : "", "例：🐶（可不填）");
  const bucket = select(d.buckets.filter((x) => x.active).map((x) => [x.id, x.is_living ? `${x.name}（每天的花費）` : x.name]),
    b ? b.bucket : (spots(d)[0] || {}).id);
  app.openModal({
    title: isNew ? (b ? `在「${b.name}」加一個大類` : "加一個大類") : `改「${g.name}」`,
    body: [h("p", { class: "muted small" }, "大類＝一群細項放在一起。屬於哪一格，底下的細項就算在哪一格（細項自己也能另外指定）。"),
      h("div", { class: "form-grid" }, field("名字", name), field("屬於哪一格", bucket)),
      field("圖示（點下面一個，或自己打一個）", icon),
      picker],
    foot: [
      isNew ? null : h("button", { type: "button", class: "btn danger", onclick: () => stopGroup(app, g) }, "停用"),
      h("button", { type: "button", class: "btn primary", onclick: () => {
        if (!name.value.trim()) { app.toast("先打名字"); return; }
        const body = { name: name.value.trim(), icon: icon.value.trim(), bucket: bucket.value };
        send(app, () => (isNew ? api("/api/group", { method: "POST", body }) : api(`/api/group/${g.id}`, { method: "PATCH", body })),
          isNew ? `加了大類「${body.name}」` : `「${body.name}」改好了`);
      } }, isNew ? "加進去" : "存")],
  });
  name.focus();
}

function stopGroup(app, g) {
  app.openModal({
    title: `不用「${g.name}」了？`,
    body: [h("p", null, "以後記帳不會再出現這個大類，但以前記的都留著。"),
      h("p", { class: "muted small" }, "底下的細項也會跟著不出現。要找回來：加一個同名的大類，就會重新打開。")],
    foot: [h("button", { type: "button", class: "btn primary", onclick: () => send(app,
      () => api(`/api/group/${g.id}`, { method: "PATCH", body: { active: false } }), `${g.name}停用了`) }, "停用")],
  });
}

// ---------------------------------------------------------------- 帳戶

function accountLine(a, d) {                              // 帳戶的細節（不含種類）：結帳日、繳款日、從哪扣…放在名字下面，名字才不會被擠成一字一行
  const bits = [];
  const bound = a.bucket && d ? d.buckets.find((x) => x.id === a.bucket) : null;     // 轉進來的錢算哪一格
  if (a.bucket) bits.push(`轉進來算「${bound ? bound.name : a.bucket}」`);
  const LEVEL_NAME = { cash: "💵 現金帳戶", invest: "📈 投資商品", trading: "🎯 資金操盤", property: "🏠 產" };
  if (a.asset_class && LEVEL_NAME[a.asset_class]) bits.push(LEVEL_NAME[a.asset_class]);
  if (a.floor_amount) bits.push(`底線 ${num(a.floor_amount)}`);
  if (a.target_amount) bits.push(`目標 ${num(a.target_amount)}`);
  if (a.card_closing_day) bits.push(`${a.card_closing_day} 號結帳`);
  if (a.card_due_day) bits.push(`${a.card_due_day} 號繳款`);
  if (a.pay_from) bits.push(`從${a.pay_from}扣`);
  return bits.join("・");
}

function accountsCard(app, d) {
  return h("section", { class: "card" }, cardHead(app, "帳戶", INFO.accounts),
    h("div", { class: "ledger" }, d.accounts.map((a) => h("button", { type: "button", class: "entry nodate", onclick: () => accountForm(app, d, a) },
      h("span", { class: "what" }, h("b", null, a.name),
        a.aliases.length ? h("small", null, `也叫 ${a.aliases.join("、")}`) : null,
        accountLine(a, d) ? h("small", null, accountLine(a, d)) : null),
      h("span", { class: "money" }, h("span", { class: "muted small" }, KIND[a.kind] || a.kind))))),
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn", onclick: () => accountForm(app, d, null) }, "＋ 加帳戶")));
}

function accountForm(app, d, a) {
  const isNew = !a;
  const cur = a || { kind: "bank", aliases: [] };
  const name = h("input", { value: cur.name || "", placeholder: "例：緊急預備金" });
  const kinds = d.account_kinds.includes(cur.kind) ? d.account_kinds : [cur.kind, ...d.account_kinds];
  const kind = select(kinds.map((k) => [k, KIND[k] || k]), cur.kind);
  kind.disabled = cur.kind === "receivable";                                       // 代墊款是報帳用的系統帳戶，種類不能換
  const aliases = h("input", { value: cur.aliases.join(", "), placeholder: "例：薪轉（跟助理說的時候用）" });
  const floor = moneyInput(cur.floor_amount, "例：30000");
  const target = moneyInput(cur.target_amount, "例：60000");
  const closing = numInput(cur.card_closing_day, "1–31");
  const due = numInput(cur.card_due_day, "1–31");
  const banks = d.accounts.filter((x) => x.kind === "bank");
  const payFrom = select([["", banks.length ? `（沒選＝${banks[0].name}）` : "（沒選）"], ...banks.map((x) => [x.name, x.name])], cur.pay_from || "");
  // 算撲滿的哪一層：撲滿頁就是照這個分四塊。信用卡是負債、代墊款是應收，所以可以不歸。
  const LEVELS = [["", "不算（信用卡、代墊款這種）"], ["cash", "💵 現金帳戶"], ["invest", "📈 投資商品"],
    ["trading", "🎯 資金操盤"], ["property", "🏠 產"]];
  const level = select(LEVELS, cur.asset_class || "");
  // 轉進這個帳戶的錢算哪一格：例 旅遊基金帳戶 → 旅遊。生活費、還債不能綁；信用卡不能綁。
  const spotsFor = d.buckets.filter((x) => x.active && !x.is_living && x.id !== "debt").map((x) => [x.id, x.name]);
  if (cur.bucket && !spotsFor.some(([id]) => id === cur.bucket)) spotsFor.push([cur.bucket, `${cur.bucket}（停用了，不會算）`]);
  const bucketSel = select([["", "不算哪一格（只是搬錢）"], ...spotsFor], cur.bucket || "");
  const bucketField = field("轉進這個帳戶的錢算哪一格", bucketSel);
  bucketField.title = "選了一格：轉進這個帳戶的錢就算做到那一格，可以自由花的錢跟著變少。例：旅遊基金帳戶 → 旅遊。";
  // 這是做什麼的：寫在欄位下面（title 手機看不到）
  const bucketHint = h("p", { class: "muted small" }, "選了一格：轉進這個帳戶的錢就算做到那一格（例：旅遊基金帳戶 → 旅遊）。不選＝只是搬錢。");
  const saveBox = h("div", { class: "form-grid" }, field("存錢底線（少於這個會變紅）", floor), field("存錢目標", target));
  const cardBox = h("div", { class: "form-grid" }, field("每月幾號結帳", closing), field("每月幾號繳款", due),
    field("卡費從哪個帳戶扣", payFrom));
  const sync = () => {
    cardBox.hidden = kind.value !== "credit_card"; saveBox.hidden = kind.value === "credit_card";
    bucketField.hidden = bucketHint.hidden = kind.value === "credit_card";
  };
  kind.addEventListener("change", sync);
  sync();
  const body = () => {
    const out = { name: name.value.trim(), aliases: aliases.value };
    if (isNew || kind.value !== cur.kind) out.kind = kind.value;
    if (kind.value === "credit_card") {
      Object.assign(out, { card_closing_day: intOrNull(closing, "結帳日"), card_due_day: intOrNull(due, "繳款日"), pay_from: payFrom.value });
    }
    else Object.assign(out, { floor_amount: moneyOrNull(floor, "底線"), target_amount: moneyOrNull(target, "目標") });
    if (kind.value !== "credit_card" && bucketSel.value !== (cur.bucket || "")) out.bucket = bucketSel.value;   // 沒改就不送（停用的格子送回去會被擋）
    out.asset_class = level.value;
    return out;
  };
  app.openModal({
    title: isNew ? "加帳戶" : cur.name,
    body: [field("名字", name), field("種類", kind), field("也叫做", aliases), field("算撲滿哪一層", level), bucketField, bucketHint, saveBox, cardBox],
    foot: [
      isNew ? null : h("button", { type: "button", class: "btn danger", onclick: () => send(app,
        () => api(`/api/account/${cur.id}`, { method: "PATCH", body: { active: false } }), `${cur.name} 不用了`) }, "不用了"),
      h("button", { type: "button", class: "btn primary", onclick: () => send(app,
        () => api(isNew ? "/api/account" : `/api/account/${cur.id}`, { method: isNew ? "POST" : "PATCH", body: body() }),
        isNew ? `加好了：${name.value.trim()}` : "改好了") }, "存")],
  });
}

// ---------------------------------------------------------------- 帳本設定（W5 新加：以前只有指令能改）

function settingInput(d, s) {
  const v = s.value === null || s.value === undefined ? "" : String(s.value);
  if (s.key === "notify.weekday") return select(WEEKDAYS.map((w, i) => [String(i), w]), v || "4");
  if (s.key === "default_account") return select(d.accounts.map((a) => [a.name, a.name]), v);
  if (s.key === "currency.decimals") return select([["0", "0（新台幣、日圓）"], ["1", "1"], ["2", "2（美元、歐元）"], ["3", "3"]], v || "0");
  if (s.key === "plan.flex_tolerance") return h("input", { value: s.value ? `${Math.round(s.value * 100)}%` : "", placeholder: "例：10%", "aria-label": s.label });
  if (s.key === "quick.lunch_weekdays") {
    return h("input", { value: v.split(",").filter((x) => x !== "").map((x) => WEEKDAYS[Number(x)] || x).join(","),
      placeholder: "例：週一,週二,週三,週四,週五（空白＝不問）", "aria-label": s.label });
  }
  const placeholder = { tz_offset: "空白＝跟著這台電腦；例：8", period_start_day: "1–31", "currency.code": "例：TWD", "currency.symbol": "例：NT$" }[s.key] || "";
  return h("input", { value: v, placeholder, "aria-label": s.label });
}

function settingsCard(app, d) {
  const rows = (d.settings || []).map((s) => {
    const input = settingInput(d, s);
    input.addEventListener("change", () => send(app,
      () => api("/api/setting", { method: "POST", body: { key: s.key, value: String(input.value).trim() || "-" } }),
      `${s.label}改好了`));
    return field(s.label, input);
  });
  const cur = d.currency || {};
  return h("section", { class: "card" }, cardHead(app, "帳本設定", INFO.settings),
    h("p", { class: "muted small" }, `這本帳用 ${cur.name || cur.code}（${cur.symbol}，${cur.decimals} 位小數）。改完一欄就存。`),
    h("div", { class: "setting-list" }, rows));
}

// ---------------------------------------------------------------- 選配功能（W5 新加）

function featuresCard(app, d) {
  const items = (d.features || []).map((f) => h("div", { class: "feature-row" },
    h("span", { class: "what" }, h("b", null, f.summary || f.name),
      f.groups && f.groups.length ? h("small", { class: "muted" }, `會加：${f.groups.join("、")}`) : null),
    f.enabled ? h("span", { class: "muted small" }, "✓ 開著")
      : h("button", { type: "button", class: "btn small", onclick: () => send(app,
        () => api("/api/feature", { method: "POST", body: { name: f.name } }), `${(f.summary || f.name).split("（")[0]}打開了`) }, "打開")));
  return h("section", { class: "card" }, cardHead(app, "選配功能", INFO.features), items);
}

// ---------------------------------------------------------------- 帳本

function bookCard(app, b) {
  const confirmBox = h("input", { placeholder: "打「開新帳本」", "aria-label": "確認" });
  const startNew = () => app.openModal({
    title: "開一本新的空帳本？",
    body: [h("p", { class: "muted small" }, "現在這本會先封存在你自己的資料夾，不會不見。確定的話打「開新帳本」。"), h("div", { class: "field" }, confirmBox)],
    foot: [h("button", { type: "button", class: "btn primary", onclick: async () => {
      try {
        const r = await api("/api/book/new", { method: "POST", body: { confirm: confirmBox.value.trim() } });
        app.closeModal();
        await app.afterWrite(r, "開好了，從空的開始");
      } catch (e) { if (e.code !== "cancelled") app.toast(errorText(e)); }
    } }, "開新的")],
  });
  const save = async () => {
    try { await download("book"); } catch (e) { app.toast(errorText(e)); }
  };
  const archives = (b.archives || []).length;
  return h("section", { class: "card" },
    cardHead(app, "帳本", INFO.book),
    h("p", null, b.transactions ? `這本帳有 ${count(b.transactions)} 筆（${b.first_date}～${b.last_date}）` : "這本帳是空的"),
    h("p", { class: "muted small" }, `帳本、每日備份（留最近 ${b.backups_kept || 14} 天）與封存都放在你自己的資料夾，沒有加密。`
      + (archives ? `封存了 ${archives} 本。` : "")),
    h("div", { class: "actions" },
      h("button", { type: "button", class: "btn", onclick: save }, "下載整本（CSV）"),
      h("button", { type: "button", class: "btn danger", onclick: startNew }, "開新帳本")),
    h("p", { class: "muted small" }, "這一版還不能從畫面匯入整本帳。"));
}
