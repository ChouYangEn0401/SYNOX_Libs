// 總覽：一個中心——「生活費還可以花多少」；下面是這一期的日曆、錢包、燈號、錢怎麼分、分期、上次檢討。
// 日曆：每天一格、顏色＝那天花得兇不兇、點點＝早午晚餐有沒有記到；旁邊寫還有幾天發薪、
// 以及「剩下的日子每天可以花多少」——花超了這個數字就會變小，一眼看得出被吃掉。
// 從原本的畫面原樣搬來（記帳 0.4.0，W4）；改的只有：第一次使用的引導（onboard.js）、去掉個人用語。
import { api, errorText } from "../api.js";
import { h, major, num, s, toMinor } from "../dom.js";        // md 用這個檔自己的（10/5，不補零）
import { infoButton } from "../info.js";
import { recordForm } from "./book.js";
import { postRecurring } from "./plan.js";
import { pendingList } from "./piggy.js";
import { start as startOnboarding } from "./onboard.js";

const ICON = { good: "👍", watch: "⚠️", next: "👉" };

// ---------------------------------------------------------------- 每個狀態都有字
// 每個狀態都要清楚可見：不能只靠顏色或一個符號（✓ ↻ ≈），色盲也要分得出來。
// 這一段是純函式（不碰 DOM），tests/money/test_app_home.py（照原本的 state_text_check 規則）會逐一檢查每個狀態都有白話字。

// 燈號（健康檢查、待解決）：顏色＋形狀（✓ ! ✕，在 style.css）＋這個字。
export const LIGHT_WORD = { good: "沒問題", warn: "注意", bad: "要處理" };

// 日曆一天的顏色也配一個字（色盲分得出來；點一下那天會跳出完整的說明）。
export const DAY_MARK = { save: "省", warn: "超", crit: "爆" };

// 「錢怎麼分」每一格的狀態 → 一段白話（state 來自 services/envelopes.py）。沒有計畫也沒用（none）就不寫。
export function envStatus(e) {
  switch (e.state) {
    case "skipped": return { cls: "skip-mark", text: "⏭ 這期跳過", title: "這期決定不做（不用記帳、不提醒、不留錢）；下一期照常" };
    case "done": return e.near
      ? { cls: "ok", text: "≈ 差不多做完", title: `金額會浮動：跟計畫差 ${num(Math.abs(e.left))}，算做完了` }
      : { cls: "ok", text: "✓ 做完了", title: "這一格這期做到了" };
    case "over": return { cls: "over", text: `超過 ${num(-e.left)}`, title: `比能用的多 ${num(-e.left)}` };
    case "rolling": return { cls: "roll-mark", text: "↻ 留著等帳單", title: "滾存：這期的額度留著等帳單來；沒用完的留到下期" };
    case "todo": return { cls: "todo", text: `還差 ${num(e.left)}`, title: `計畫 ${num(e.plan)}，還差 ${num(e.left)} 才做到` };
    case "open": return e.left < 0
      ? { cls: "over", text: `超過 ${num(-e.left)}`, title: `比能用的多 ${num(-e.left)}` }
      : { cls: "todo", text: `還能花 ${num(e.left)}`, title: "生活費是拿來花的：用掉越多、剩越少" };
    default: return null;
  }
}

// 「標示是什麼意思」：總覽右上角一顆按鈕打開（預設收起來，不佔首屏高度）。每一個符號／狀態一行白話。
export const LEGEND = [
  ["錢怎麼分（每一格後面的字）", [
    ["✓ 做完了", "這一格這期做到了。"],
    ["≈ 差不多做完", "金額會浮動的格子（水電、訂閱），差一點點也算做完。"],
    ["還差 N", "還沒做完：再記 N 就做到了（「今天處理」可以一鍵記）。"],
    ["還能花 N", "生活費這期還剩多少可以花。"],
    ["超過 N", "比這期能用的多了 N。可以按「⇄ 挪用」從別格補。"],
    ["↻ 留著等帳單", "滾存的格子：這期的額度先留著，帳單來了再付；沒用完的留到下期。"],
    ["已撥 N → 去向", "這期的額度已經移出日常帳戶（轉到哪個帳戶，或預繳給對方）。"],
    ["⏭ 這期跳過", "這期決定不做：不提醒、不留錢，下一期照常。"],
    ["⇄ 挪用", "從一格拿額度去補另一格；格子上會寫「挪入 +N／挪出 −N」。"],
    ["🔒", "一定要做到：到期末還沒做到會亮燈。"],
    ["？（名字後面）", "還沒指定哪些類別算這一格：到「設定→類別」那一格按 🔗。"],
    ["點格子的名字", "跳出這一格的說明（手機也可以）。"],
  ]],
  ["燈號（健康檢查、待解決）", [
    ["✓ 綠", "沒問題。"],
    ["! 黃", "注意：還不急，但要留意。"],
    ["✕ 紅", "要處理：已經超過或到期了。"],
    ["待解決 N 件", "帳裡彼此對不上的地方；按「去處理」到對的地方決定。"],
  ]],
  ["這一期的日曆", [
    ["省（綠底）", "那天花得比每天額度的一半還少。"],
    ["白底", "正常。"],
    ["超（黃底）", "那天花超過每天的額度。"],
    ["爆（紅底）", "那天花超過每天額度的 1.5 倍。"],
    ["淡灰", "還沒到的日子；藍框是今天。"],
    ["早 午 晚", "那天記了哪一餐。"],
    ["？", "那天有還沒分類的花費，顏色可能不準。"],
    ["📦", "那天有「長期用」的花費：算在生活費總額，不算在那天。"],
    ["點一天", "跳出那天花了多少、最大一筆（手機也可以）。"],
  ]],
  ["其他", [
    ["本來每天可以花", "照預算算的（計畫）。"],
    ["依現在的錢估", "照你帳戶裡現在真的有的錢估的，只是參考。"],
    ["薪水：已入帳／還沒入帳", "這一期的薪水進來了沒。"],
    ["處理報帳", "別人欠我、我欠別人，還沒還清的；還了按「已還」。"],
    ["信用卡：繳好了／還沒繳", "這期帳單繳了沒；「估的」＝帳單還沒來，用刷的加起來估。"],
    ["ⓘ", "按一下看這張卡在做什麼。"],
  ]],
];

function openLegend(app) {
  app.openModal({
    title: "畫面上的標示是什麼意思",
    body: LEGEND.map(([section, rows]) => h("div", { class: "legend-sec" }, h("h3", null, section),
      h("dl", { class: "legend-list" }, rows.map(([mark, text]) => [h("dt", null, mark), h("dd", null, text)])))),
  });
}

// 停上去才看得到的說明（title），手機上沒有「停上去」：點一下用視窗打開。
function tipModal(app, title, text) {
  app.openModal({ title, body: String(text || "").split("\n").filter(Boolean).map((t) => h("p", null, t)) });
}

export async function render(app) {
  const seg = h("div", { class: "seg", role: "group", "aria-label": "看這個月或今年" },
    [["month", "這個月"], ["year", "今年"]].map(([id, name]) => h("button", {
      type: "button", "aria-pressed": app.homeMode === id ? "true" : "false",
      onclick: () => { app.homeMode = id; app.render(); },
    }, name)));
  if (app.homeMode === "year") {
    const y = await api("/api/year", { params: { year: app.year } });
    app.setYearNav(y);
    return h("div", { class: "page-inner" }, seg, yearView(y));
  }
  const d = await api("/api/home", { params: { period: app.period } });
  app.setNav(d);
  if (d.onboarding && !app.onboardDone) {                      // 還沒做第一次使用：三步驟引導（onboard.js）
    const guide = await startOnboarding(app);
    if (guide) return h("div", { class: "page-inner" }, guide);   // 引導自己畫一頁：做完前總覽就是它
    app.onboardDone = true;                                    // 引導沒畫東西（開了對話框，或還沒做）：這次不再叫
  }
  const fresh = !d.start.has_transactions;                     // 還沒有任何一筆帳：三步驟引導（記了第一筆就不出現）
  if (!d.start.has_plan && fresh) return h("div", { class: "page-inner" }, firstRun(app));
  const top = h("div", { class: "home-top" }, seg,
    h("button", { type: "button", class: "btn small legend-btn", onclick: () => openLegend(app) }, "？ 標示是什麼意思"));
  return h("div", { class: "page-inner" }, top,
    fresh ? firstRun(app) : null,
    d.start.has_plan ? null : startCard(app),
    hero(d), dupCard(app, d.duplicates), calendarCard(app, d), walletCard(app, d.wallet, d.living, d.month_end), lights(app, d.health, d.conflicts), iouCard(app, d.advance),
    piggyCard(app, d.piggy),
    cards(app, d.cards), envelopes(app, d), movesCard(app, d.moves), debts(d.debts), reviewCard(app, d.review));
}

function bar(value, total, cls = "") {
  const pct = total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0;
  return h("div", { class: `bar ${cls}`, role: "img", "aria-label": `${pct}%` }, h("i", { style: { width: `${pct}%` } }));
}

// 第一次用（帳本裡還沒有任何一筆帳）：三步驟的白話引導。記了第一筆之後就不出現。
export function firstRun(app) {
  return h("section", { class: "card hero first-run" },
    h("span", { class: "label" }, "第一次用？照這三步開始"),
    h("ol", { class: "steps" },
      h("li", null, h("b", null, "告訴我你的錢放在哪裡"),
        h("span", { class: "muted" }, "到「設定」的「帳戶」加上你的現金、銀行、信用卡。"),
        h("button", { type: "button", class: "btn primary", onclick: () => app.go("setup") }, "去設定加帳戶")),
      h("li", null, h("b", null, "記第一筆"),
        h("span", { class: "muted" }, "到「明細」最上面填金額，按「記」。也可以在聊天裡跟助理說「早餐 80」。"),
        h("button", { type: "button", class: "btn", onclick: () => app.go("book") }, "去明細記一筆")),
      h("li", null, h("b", null, "看今天還可以花多少"),
        h("span", { class: "muted" }, "到「規劃」填這個月的生活費；回到這裡，最上面的大數字就是還可以花的錢。"),
        h("button", { type: "button", class: "btn", onclick: () => app.go("plan") }, "去規劃填生活費"))),
    h("small", { class: "muted" }, "記了第一筆之後，這張就不會再出現。"));
}

function startCard(app) {
  return h("section", { class: "card" },
    h("p", null, "第一步：告訴我薪水怎麼分。"),
    h("button", { type: "button", class: "btn primary", onclick: () => app.go("plan") }, "去規劃"));
}

function hero(d) {
  const lv = d.living;
  if (!lv.budget) {
    return h("section", { class: "card hero" }, h("span", { class: "label" }, "這個月花了"),
      h("span", { class: "big" }, num(lv.spent)), h("span", { class: "sub" }, "還沒設生活費預算"));
  }
  const over = lv.left < 0;
  const tip = `這期生活費預算 ${num(lv.budget)}（${lv.follows ? "＝規劃裡「可以花的錢」各大類加起來" : "你在規劃自己填的"}），`
    + `已花 ${num(lv.spent)}，${over ? "超過" : "還剩"} ${num(Math.abs(lv.left))}。三餐、飲料、交通這類每天的花費都算在這裡；`
    + "房租、捐贈、存錢這些固定的不算。";
  return h("section", { class: "card hero", title: tip },
    h("span", { class: "label" }, over ? "生活費超支" : "生活費還可以花"),
    h("span", { class: `big ${over ? "bad" : ""}` }, num(Math.abs(lv.left))),
    bar(lv.spent, lv.budget, over ? "over" : ""),
    h("span", { class: "sub" }, over ? `預算 ${num(lv.budget)}，花了 ${num(lv.spent)}`
      : (lv.per_day_left !== null ? `每天 ${num(lv.per_day_left)}・還有 ${lv.days_left} 天` : `預算 ${num(lv.budget)}，花了 ${num(lv.spent)}`)));
}

// 可能記重複了：寫入當下打分、很像的就建一組。只在「剛寫進來」那一刻問一次，錯過就再也看不到，
// 所以直接放在總覽上，隨時可以處理。
function dupOne(t) {
  return h("div", { class: "dup-one" },
    h("small", { class: "muted" }, `${md(t.date)}${t.time ? " " + t.time : ""}${t.account ? "・" + t.account : ""}`),
    h("b", { class: "num" }, num(t.amount)),
    h("span", null, t.desc || "（沒寫）"),
    t.category ? h("small", { class: "muted" }, t.category) : null);
}

function dupPair(app, p) {
  const pick = async (action, msg) => {
    try {
      const r = await api(`/api/dedup/${p.pair_id}`, { method: "POST",
        body: { action, expect_a: p.a.updated_at, expect_b: p.b.updated_at } });
      await app.afterWrite(r, msg, r.batch_id);
    } catch (e) { app.toast(errorText(e)); }
  };
  return h("div", { class: "dup" },
    h("div", { class: "dup-pair" }, dupOne(p.a), h("span", { class: "muted small" }, "跟"), dupOne(p.b)),
    p.reason ? h("p", { class: "muted small" }, p.reason) : null,
    h("div", { class: "actions" },
      h("button", { type: "button", class: "btn primary", onclick: () => pick("delete_newer", "刪掉重複的那筆了") },
        "是同一筆，刪一筆"),
      h("button", { type: "button", class: "btn", onclick: () => pick("keep_both", "兩筆都留著") }, "不是，兩筆都留")));
}

function dupCard(app, list) {
  if (!list || !list.length) return null;
  return h("section", { class: "card dup-card" },
    h("div", { class: "row" }, h("h2", null, "可能記重複了"), h("span", { class: "muted small" }, `${list.length} 組`)),
    h("p", { class: "muted small" }, "同一天、金額一樣、內容很像的兩筆。選錯了在明細可以復原。"),
    list.map((p) => dupPair(app, p)));
}

// 這一期的日曆：一天一格。顏色＝那天花得兇不兇；小點＝早午晚餐有沒有記到；今天有框；還沒到的淡淡的。
const MEAL_MARK = { breakfast: "早", lunch: "午", dinner: "晚" };

function dayCell(app, day) {
  const n = Number(day.date.slice(8));
  const meals = day.meals.map((m) => h("i", { class: "m", title: `有記${MEAL_MARK[m]}餐` }, MEAL_MARK[m]));
  const tip = day.future ? `${md(day.date)} 還沒到`
    : `${md(day.date)}（星期${day.weekday}）花了 ${num(day.total)}・${day.status}`
      + (day.top ? `・最大一筆：${day.top.label} ${num(day.top.amount)}` : "")
      + (day.meals.length ? `・有記${day.meals.map((m) => MEAL_MARK[m]).join("、")}餐` : "")
      + (day.unsorted ? `\n其中 ${day.unsorted_count} 筆還沒分類（${num(day.unsorted)}）：分類後這天的顏色可能會變` : "")
      + (day.stock ? `\n另外有長期用 ${num(day.stock)}，不算在這天` : "");
  const mark = !day.future && DAY_MARK[day.level];          // 顏色之外再配一個字：色盲也分得出省／超／爆
  // 手機沒有「停上去」：點一下那天，用提示把那天的說明講出來
  return h("div", { class: `d ${day.level}${day.today ? " now" : ""}`, title: tip, role: "button", tabindex: "0",
    "aria-label": tip.replace(/\n/g, "，"), onclick: () => app.toast(tip.replace(/\n/g, "　")) },
    h("span", { class: "n" }, String(n), mark ? h("b", { class: `lv ${day.level}` }, mark) : null,
      day.unsorted ? h("b", { class: "q" }, "？") : null, day.stock ? h("b", { class: "q" }, "📦") : null),
    h("span", { class: "v" }, day.future ? "" : (day.total ? num(day.total) : "－")),
    meals.length ? h("span", { class: "meals" }, meals) : null);
}

function calendarCard(app, d) {
  const c = d.calendar;
  if (!c || !c.days.length) return null;
  const head = c.days_to_payday
    ? h("span", { class: "muted small" }, `還有 ${c.days_to_payday} 天發薪（${md(c.payday)}）`)
    : h("span", { class: "muted small" }, "這一期結束了");
  const sal = c.salary;
  const salary = !sal ? null : h("p", { class: "muted small", title: "每月照日期換期（起算日不看薪水）；這裡只記薪水這期到了沒" },
    sal.arrived ? `薪水週期：✓ 已入帳（${md(sal.date)}，${num(sal.amount)}）${sal.late_days > 0 ? `，比起算日 ${md(sal.start)} 晚 ${sal.late_days} 天，固定支出照日期算` : ""}`
      : `薪水週期：還沒入帳${sal.waiting_days > 0 ? `（起算日 ${md(sal.start)} 已過 ${sal.waiting_days} 天）` : `（起算日 ${md(sal.start)}）`}`);
  const pace = c.daily_budget
    ? (c.per_day_left === null ? null
      : h("p", { class: `note-line${c.per_day_left < c.daily_budget ? " warn" : ""}` },
        (c.stock ? `扣掉長期用的 ${num(c.stock)}，` : "") + `本來每天可以花 ${num(c.daily_budget)}，剩下的 ${c.days_left} 天每天 ${num(c.per_day_left)}`
        + (c.per_day_left < c.daily_budget ? "（前面花多了，之後每天就少了）" : "")))
    : h("p", { class: "muted small" }, "還沒設生活費預算，所以沒有每天的額度");
  const guess = estimateLine(app, c.estimate);
  const pattern = c.pattern
    ? h("p", { class: "muted small" }, `最近你星期${c.pattern.weekday}花最多：平均 ${num(c.pattern.avg)}，其他天 ${num(c.pattern.others)}`)
    : null;
  const pad = new Date(c.days[0].date + "T00:00:00").getDay();          // 0＝星期日
  const blanks = Array.from({ length: (pad + 6) % 7 }, () => h("div", { class: "d blank" }));
  // 這一期有哪些錢不是「當天用掉的」：沒分類的（顏色還沒定）、長期用的（算總額、不算每天）——寫出來，不要讓人猜為什麼某天紅
  const notes = [];
  if (c.unsorted && c.unsorted.count) {
    notes.push(h("p", { class: "note-line warn", title: "信用卡帳單匯進來還沒歸類的，先算在那天；分類之後（例如某一筆其實是訂閱），那天的顏色就會對。" },
      `有 ${c.unsorted.count} 筆還沒分類（${num(c.unsorted.amount)}），日曆上標 ？的那幾天顏色可能不準 `,
      h("button", { type: "button", class: "btn small", title: "用現在的規則再猜一次（訂閱、你教過的店、吃的依時間）；猜不出的不動，可以復原。",
        onclick: async () => {
          try {
            const r = await api("/api/reclassify", { method: "POST", body: {} });
            await app.afterWrite(r, r.count ? `分好了 ${r.count} 筆：${r.changed.slice(0, 3).map((x) => `${x.desc}→${x.category}`).join("、")}${r.count > 3 ? "…" : ""}`
              : "沒有能自動分的，請去明細一筆一筆選（改一次，系統就記住這家店）", r.batch_id);
          } catch (e) { app.toast(errorText(e)); }
        } }, "自動分類"),
      h("button", { type: "button", class: "btn small", onclick: () => app.go("book") }, "去明細")));
  }
  if (c.stock) {
    notes.push(h("p", { class: "muted small", title: "囤貨、3C、一年份的東西：錢花了、算在生活費總額，但不算在每天（標 📦 的那幾天）。" },
      `📦 長期用 ${num(c.stock)}：算在生活費總額，不算在每天`));
  }
  return h("section", { class: "card" },
    h("div", { class: "row" }, h("h2", null, "這一期"), head),
    h("div", { class: "cal-week" }, ["一", "二", "三", "四", "五", "六", "日"].map((w) => h("span", null, w))),
    h("div", { class: "cal" }, blanks, c.days.map((day) => dayCell(app, day))),
    salary, pace, guess, notes, pattern);
}

// 依現在的錢估：手上真的有的錢 − 要留的 − 卡費要留的，除以剩下的天數。不是預算、不會存起來；
// 比計畫少很多才用黃字。ⓘ 打開看扣掉了哪些（手機沒有「停上去」，所以用按鈕）。
function estimateLine(app, e) {
  if (!e || e.per_day === null) return null;
  const signed = (n) => `${n < 0 ? "−" : ""}${num(Math.abs(n))}`;
  const lines = [`${e.label}。`, ...e.reasons.map((r) => `${r.text}：${signed(r.amount)}`),
    "這是用你帳戶裡現在真的有的錢估的，跟預算算出來的不一樣；只是參考，不會存起來。"];
  const vs = !e.plan_per_day ? "" : (e.per_day < e.plan_per_day ? `，比計畫的 ${num(e.plan_per_day)} 少` : `（計畫是 ${num(e.plan_per_day)}）`);
  return h("p", { class: `note-line ${e.level === "warn" ? "warn" : "info"}` },
    (e.level === "warn" ? "注意・" : "") + `依現在的錢估：每天可以花 ${num(e.per_day)}` + vs,
    h("small", { class: "muted" }, `・${e.basis === "after_salary" ? e.label : "薪水還沒進來，用上期剩的錢估"}`), " ",
    infoButton(app, "依現在的錢估", lines));
}

// 錢包裡的錢分三堆：要留的（還沒做完的捐贈、固定、存錢、投資、分期）、生活費預算還剩的、剩下多出來的。
// 每一格下面一句白話、停上去（或點一下）看算式。
function walletCard(app, w, lv, me) {
  const living = lv.budget ? Math.max(lv.left, 0) : 0;
  const extra = w.wallet - w.reserved - living;
  const reservedTip = w.reserved_items.length
    ? "還沒做完、要先留著的錢：\n" + w.reserved_items.map((r) => `・${r.name} ${num(r.amount)}`).join("\n") + `\n合計 ${num(w.reserved)}`
    : "沒有要留的：計畫裡的固定項目都做完了。";
  const livingTip = lv.budget
    ? `這期生活費預算 ${num(lv.budget)}，已花 ${num(lv.spent)}，還剩 ${num(living)}。`
    : "還沒設生活費預算，所以這裡是 0。到「規劃」填。";
  const extraTip = extra < 0
    ? `錢包 ${num(w.wallet)} − 要留 ${num(w.reserved)} − 生活費還剩 ${num(living)} = −${num(-extra)}。\n`
      + "意思是：錢包裡的錢，不夠同時做完固定項目又花滿生活費。到「規劃→薪水怎麼分」看是不是有兩格其實是同一筆錢。"
    : `錢包 ${num(w.wallet)} − 要留 ${num(w.reserved)} − 生活費還剩 ${num(living)} = ${num(extra)}。\n沒有被分配、可以自由用（也可以當緩衝）。`;
  const cell = (label, value, sub, tip, bad) => h("div", { title: tip, onclick: () => tipModal(app, label, tip) },
    h("span", null, label), h("b", { class: bad ? "bad" : null }, num(value)), h("small", { class: "muted" }, sub));
  return h("section", { class: "card" },
    h("div", { class: "row" }, h("h2", null, "錢包"),
      h("span", null, h("b", { class: `num ${w.wallet < 0 ? "bad" : ""}`, title: "現在帳戶裡一共有多少（現金、銀行都算）" }, num(w.wallet)),
        infoButton(app, "錢包", [
          "上面的數字是你現在所有帳戶加起來的錢。",
          "下面三格是在回答：「這些錢裡面，有多少已經有安排了？」",
          "要留＝固定的項目（房租、存錢、捐贈…）計畫要做、但還沒做完的，先留著不能花。",
          "生活費＝這期生活費預算還剩多少可以花。",
          "多出來＝錢包 − 要留 − 生活費。正的是沒被分配、可以自由用；變成「不夠」就是計畫分得比手上的錢多。",
          "月底：多出來的就是結餘，可以轉到存錢的帳戶；銀行裡留下滾存（水電、手機費還留著的）和卡費（刷卡的錢，帳單來才扣）。",
          "點一下（或把滑鼠停在）每一格，會看到算式。",
        ]))),
    h("div", { class: "trio" },
      cell("要留", w.reserved, "固定的還沒做完", reservedTip),
      cell("生活費", living, "這期還能花", livingTip),
      cell(extra < 0 ? "不夠" : "多出來", Math.abs(extra), extra < 0 ? "計畫比錢多" : "沒分配、可自由用", extraTip, extra < 0)),
    monthEnd(app, me));
}

// 月底：結餘轉去存錢的帳戶，銀行只留滾存＋卡費。數字在 engine/services/home._month_end。
function monthEnd(app, me) {
  if (!me || (!me.pots_total && !me.card_keep && me.surplus <= 0)) return null;
  const pots = me.pots.filter((p) => p.amount).map((p) => `${p.name} ${num(p.amount)}`).join("、");
  const keep = [me.pots_total ? `滾存 ${num(me.pots_total)}${pots ? `（${pots}）` : ""}` : null, me.card_keep ? `信用卡帳單 ${num(me.card_keep)}（本期＋下一期已刷）` : null]
    .filter(Boolean).join("＋") || "沒有要留的";
  const move = me.surplus > 0
    ? (me.to ? h("button", { type: "button", class: "btn small", title: `記一筆存錢：${me.from || "銀行"} → ${me.to}`, onclick: () => moveSurplus(app, me) }, "轉出結餘")
      : h("button", { type: "button", class: "btn small", title: "還沒有另外的存錢帳戶：到設定加一個銀行帳戶", onclick: () => app.go("setup") }, "先加存錢帳戶"))
    : null;
  return h("div", { class: "month-end", title: "多出來的＝錢包 − 要留 − 生活費還剩；滾存與卡費留在銀行，帳單來的時候用" },
    h("span", { class: "small" }, h("b", null, "月底　"),
      me.surplus > 0 ? `結餘 ${num(me.surplus)} 可以轉到${me.to ? `「${me.to}」` : "存錢的帳戶"}；` : "這期沒有多出來的；",
      `銀行留下${keep}`), move);
}

function setAsideWhere(s, long = false) {
  if (s.kind === "prepay") return long ? "預繳給對方，已記成本期支出" : "預繳";
  if (s.dest) return long ? `轉到「${s.dest}」` : s.dest;
  return long ? "舊紀錄：錢沒搬" : "沒搬";
}

async function moveSurplus(app, me) {
  const ref = await app.ref();
  app.openModal({
    title: "月底：結餘轉出去",
    body: [h("p", { class: "muted small" }, `多出來的 ${num(me.surplus)} 轉到「${me.to}」，銀行留下滾存與卡費。金額可以改，確認就記一筆存錢。`),
      recordForm(app, ref, { defaults: { kind: "saving", amount: me.surplus, account: me.from || undefined, to: me.to, item: "月底結餘" },
        submitLabel: "確認轉出", onDone: () => app.closeModal() })],
  });
}

// 撥額度：這期的額度先處理掉，**錢要離開日常帳戶**才算（轉去另一個帳戶，或預繳給對方）。
// 去向一定要選，金額可以改；寫清楚是為了不要有人按了以為錢有留好、其實還在可以亂花的戶頭裡。
const PREPAY = "預繳";
async function setAside(app, e) {
  const ref = await app.ref();
  const amount = h("input", { inputmode: "decimal", value: e.plan ? major(e.plan) : "", "aria-label": "撥多少" });
  const homes = ref.accounts.filter((a) => !["credit_card", "receivable", "loan"].includes(a.kind));
  const dest = h("select", { "aria-label": "錢放去哪" }, h("option", { value: "" }, "請選一個…"),
    homes.map((a) => h("option", { value: a.name }, `${a.name}（轉過去，還是你的錢）`)),
    h("option", { value: PREPAY }, "預繳給對方（直接付掉，記成本期支出）"));
  const go = h("button", { type: "button", class: "btn primary", onclick: async () => {
    const n = toMinor(amount.value);                   // 照帳本的小數位數換成最小單位（dom.js）
    if (!Number.isInteger(n) || n <= 0) return app.toast("「撥多少」要填一個大於 0 的數字");
    if (!dest.value) return app.toast("要選「錢放去哪」：撥額度的錢一定要離開日常帳戶");
    try {
      const r = await api(`/api/bucket/${e.id}/setaside`, { method: "POST", body: { amount: n, to: dest.value } });
      app.closeModal();
      await app.afterWrite(r, `${e.name}撥了 ${num(n)}：${dest.value === PREPAY ? "預繳給對方，記成本期支出" : `轉到「${dest.value}」`}`, r.batch_id);
    } catch (err) { app.toast(errorText(err)); }
  } }, "確認撥額度");
  app.openModal({
    title: `撥額度：${e.name}`,
    body: [h("p", { class: "muted small" }, `這期計畫 ${num(e.plan)}${e.carry ? `，前期留下 ${num(e.carry)}` : ""}。`),
      h("p", { class: "small" }, h("b", null, "撥額度＝把這筆錢真的移出日常帳戶。"),
        "留在日常帳戶的錢很容易不小心花掉，所以只在總覽打勾不算；錢離開了，這一期的額度才算處理完。"),
      h("ul", { class: "small muted" },
        h("li", null, "轉到自己的另一個帳戶（或現金）＝還是你的錢，只是先放著，帳單來了再從那裡付。"),
        h("li", null, "預繳給對方＝現在就付掉，記成本期的支出；帳單來了只補差額。")),
      h("label", { class: "field" }, "撥多少", amount), h("label", { class: "field" }, "錢放去哪", dest)],
    foot: [go],
  });
}

// 待解決：帳裡對不上的地方，只標出來、不自動解決。**沒有也要寫「沒有待解決的」**（每個狀態都要看得到）。
// 每件：紅黃點＋一句話＋為什麼＋「去處理」按鈕（跳到對的分頁；在總覽上的就捲過去）。
const CONFLICTS_SHOWN = 6;
function conflictsBlock(app, cf) {
  if (!cf) return null;
  const goTo = (w) => {
    const el = w.anchor && app.tab === w.tab ? document.querySelector(w.anchor) : null;
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    else app.go(w.tab);
  };
  const row = (i) => h("li", null, lightDot(i.level),
    h("span", { class: "what" }, h("b", null, i.title),
      h("small", { class: "muted" }, i.why + (i.where.hint ? `（${i.where.hint}）` : ""))),
    h("button", { type: "button", class: "btn small", onclick: () => goTo(i.where) }, i.where.label));
  const more = cf.items.slice(CONFLICTS_SHOWN);
  return h("div", { class: `conflicts ${cf.count ? (cf.bad ? "bad" : "warn") : "good"}` },
    h("div", { class: "row" }, h("b", null, cf.count ? `⚠️ ${cf.headline}` : `✅ ${cf.headline}`),
      infoButton(app, "待解決", [
        "帳裡彼此對不上的地方：可能記重複、同一天收了兩次一樣的錢、薪水記錯帳戶、信用卡帳單跟記的帳不一樣、"
          + "核對的餘額跟記的帳差、很多筆還沒分類。",
        "這裡只把它們標出來，不會自動幫你改；按「去處理」到對的地方決定要怎麼做。",
        "處理好了（或確定沒問題），下次打開就不會再列。",
      ])),
    cf.count ? h("ul", { class: "lights" }, cf.items.slice(0, CONFLICTS_SHOWN).map(row)) : null,
    more.length ? h("details", null, h("summary", null, `還有 ${more.length} 件`), h("ul", { class: "lights" }, more.map(row))) : null);
}

// 燈號的點：顏色＋形狀（✓ ! ✕，在 style.css）＋讀得出來的字（螢幕閱讀器、停上去都看得到）。
function lightDot(level) {
  const word = LIGHT_WORD[level] || "";
  return h("span", { class: `dot ${level}`, role: "img", "aria-label": word, title: word });
}

function lights(app, all, cf) {
  const items = all.filter((i) => i.also !== "conflicts");     // 待解決已經講的，這裡不重複
  const due = items.some((i) => i.key === "recurring");
  return h("section", { class: "card" }, h("h2", null, "健康檢查"),
    conflictsBlock(app, cf),
    h("ul", { class: "lights" }, items.map((i) => h("li", { title: i.tip || null }, lightDot(i.level), h("span", null, i.text),
      i.tip ? h("button", { type: "button", class: "info why", title: "為什麼？", "aria-label": `${i.text}：為什麼？`,
        onclick: () => tipModal(app, i.text, i.tip) }, "ⓘ") : null))),
    due ? h("div", { class: "actions" },
      h("button", { type: "button", class: "btn primary", onclick: () => postRecurring(app) }, "一鍵記下固定的")) : null);
}

// 撲滿：捐贈還沒做的、該收的倉（打勾就好）、這期違規與本月停止、投資與操盤目前 vs 基準紅線。
// 完整的線圖在撲滿裡；這裡只放一眼看得懂的那幾行（總覽的中心還是生活費）。
function piggyCard(app, p) {
  if (!p) return null;
  const lines = h("div", { class: "ledger" }, p.lines.map((l) => h("div", { class: "entry nodate" },
    h("span", { class: "what" }, h("b", null, l.name),
      h("small", null, l.bench === null ? (l.cash_known ? "目前" : "目前（證券戶現金還沒核對）") : `${p.symbol || "基準"} 紅線 ${num(l.bench)}`)),
    h("span", { class: "money" }, h("span", { class: "amt" }, num(l.value)),
      l.bench === null ? null : h("span", { class: `bal ${l.diff >= 0 ? "good" : "bad"}` }, `${l.diff >= 0 ? "贏" : "輸"} ${num(Math.abs(l.diff))}`)))));
  return h("section", { class: "card" },
    h("div", { class: "row" }, h("h2", null, "🐷 撲滿"),
      h("button", { type: "button", class: "btn small", onclick: () => app.go("piggy") }, "去撲滿")),
    p.month_stopped ? h("p", { class: "note-line bad" }, `本月可虧損 ${num(p.month_stopped.limit)} 打完了：這期停止操盤。`) : null,
    p.violations ? h("p", { class: "note-line bad" }, `這期操盤違規 ${p.violations} 次——到撲滿的「檢討」看是哪幾筆。`) : null,
    lines, pendingList(app, p.pending));
}

// 處理報帳：別人欠我／我欠別人，每個人一行淨額（同一個人兩邊都有已經抵掉），按「已還」預先填好那一筆。
// 兩邊都沒有就不出現。只還一部分就把金額改小，剩下的下次再按。
function iouCard(app, adv) {
  if (!adv || (!adv.total && !(adv.owe && adv.owe.total))) return null;
  // 每個人一行：誰、幾筆、從哪天開始還沒還（狀態寫成字，不只一個數字）
  const since = (data, w) => {
    const first = data.items.find((i) => i.counterparty === w.counterparty);
    return first ? `${md(first.date)} 起還沒還` : "還沒還";
  };
  const side = (title, data, kind) => (data && data.total ? h("div", { class: "iou-side" },
    h("h3", null, `${title} ${num(data.total)}`),
    h("div", { class: "ledger" }, data.who.map((w) => h("div", { class: "entry nodate" },
      h("span", { class: "what" }, h("b", null, w.named ? w.counterparty : "沒寫是誰"),
        h("small", null, (w.count > 1 ? `${w.count} 筆・` : "") + since(data, w))),
      h("span", { class: "money" }, h("span", { class: "amt" }, num(w.amount)),
        h("button", { type: "button", class: "btn small", onclick: () => settle(app, data, w, kind) },
          kind === "collect" ? "對方還了" : "我還了")))))) : null);
  return h("section", { class: "card iou" },
    h("div", { class: "row" }, h("h2", null, "處理報帳"),
      infoButton(app, "處理報帳", [
        "還沒還清的錢：「別人欠我」是你幫別人先付的（代墊），「我欠別人」是別人先幫你付的。",
        "一個人一行；同一個人兩邊都有，已經互相抵掉，只寫差多少。",
        "對方還了（或你還了）就按那一行的按鈕：會預先填好一筆，金額可以改小（只還一部分）。",
      ])),
    side("別人欠我", adv, "collect"), side("我欠別人", adv.owe, "repay"));
}

async function settle(app, data, w, kind) {
  const ref = await app.ref();
  const bank = ref.accounts.find((a) => a.kind === "bank");
  const list = data.items.filter((i) => i.counterparty === w.counterparty)
    .map((i) => `${md(i.date)} ${i.label || ""} ${num(i.amount)}`.replace(/\s+/g, " ")).join("、");
  const who = w.named ? w.counterparty : "";
  app.openModal({
    title: kind === "collect" ? `${who || "代墊"}還我` : `我還${who}`,
    body: [h("p", { class: "small" }, kind === "collect" ? "記下對方還你的錢：錢進你的帳戶，「別人欠我」跟著變少。"
      : "記下你還對方的錢：錢從你的帳戶出去，「我欠別人」跟著變少。"),
    h("p", { class: "muted small" }, `還沒還的：${list}。還了多少就填多少（只還一部分就改小，剩下的下次再按）。`),
      recordForm(app, ref, { defaults: { kind, amount: w.amount, item: who, account: bank ? bank.name : undefined },
        submitLabel: "確認已還", onDone: () => app.closeModal() })],
  });
}

// 信用卡：刷卡時錢包已經扣了，這裡只回答「銀行裡要留多少給卡費」——這期要繳的＋下期已經刷的。
const md = (iso) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;

function cards(app, list) {
  if (!list.length) return null;
  return h("section", { class: "card" }, h("h2", null, "信用卡"), list.map((c) => {
    if (!c.ready) {
      return h("div", { class: "row" }, h("span", null, c.text),
        h("button", { type: "button", class: "btn small", onclick: () => app.go("setup") }, "去設定"));
    }
    const b = c.bill;
    const cell = (label, value, sub, bad) => h("div", null, h("span", null, label), h("b", { class: bad ? "bad" : null }, value),
      h("small", { class: "muted" }, sub));
    // 本期帳單的狀態一定寫成字：繳好了／還沒繳（幾號前）／沒有帳單；「估的」＝帳單還沒來
    const paid = b.amount && !b.left;
    const billSub = !b.amount ? "這期沒有要繳的"
      : (paid ? `✓ 已繳（${md(b.due)} 前）` : `還沒繳・${md(b.due)} 前要繳`) + (b.source === "estimate" ? "・估的（帳單還沒來）" : "");
    return h("div", { class: "cc" },
      h("div", { class: "row" }, h("b", null, c.account),
        h("span", { class: "muted small", title: "本期還沒繳的帳單＋下一期已經刷的，要留在扣款帳戶" }, `${c.bank || "銀行"}要預留信用卡帳款 `, h("b", { class: `num ${c.level === "bad" ? "bad" : ""}` }, num(c.keep)))),
      c.why && c.level !== "good" ? h("p", { class: `note-line ${c.level === "bad" ? "bad" : "warn"}` }, `${LIGHT_WORD[c.level] || ""}・${c.why}`) : null,
      h("div", { class: "trio" },
        cell("本期信用卡帳單", paid ? "繳好了" : num(b.left), billSub, c.level === "bad" && b.left > 0),
        cell("下一期帳單（已刷）", num(c.next.amount), `還沒結帳・${md(c.next.closing)} 結帳`),
        c.bank_balance === null ? cell(c.bank || "銀行", "還不知道", "核對一次餘額")
          : cell(`${c.bank}有`, num(c.bank_balance), c.bank_balance >= c.keep ? "✓ 夠繳" : `不夠，差 ${num(c.keep - c.bank_balance)}`,
            c.bank_balance < c.keep)));
  }));
}

// 「今天處理」：預先填好這一格該記的那一筆（金額＝還差多少、帳戶、類別），確認就記進明細。
// 記完這一格就會自己變成做到了；在聊天裡直接說項目也一樣，程式會對上。
async function handle(app, e, link = { bucket: e.id, name: e.name }) {
  const ref = await app.ref();
  const a = e.action;
  const kind = a.kind === "transfer" ? (a.purpose === "saving" ? "saving" : (a.purpose === "investing" ? "investing" : "other")) : "expense";
  app.openModal({
    title: `今天處理：${e.name}`,
    body: [h("p", { class: "muted small" }, e.rollover ? `能用 ${num(e.available)}（這期撥 ${num(e.plan)}${e.carry ? `＋前期留下 ${num(e.carry)}` : ""}）・已用 ${num(e.used)}・還剩 ${num(e.left)}。帳單來了就照實際金額記。`
      : e.plan ? `計畫 ${num(e.plan)}・已做 ${num(e.used)}・還差 ${num(e.left)}。確認一下，按「確認記下」就記進明細。`
      : `${e.name}這期的金額不一定：填實際的金額，確認就記進明細。`),
      recordForm(app, ref, { defaults: { kind, amount: a.amount, item: a.item, account: a.account, to: a.to, category: a.category, date: a.date },
        link, submitLabel: "確認記下", onDone: () => app.closeModal() })],
    // 這個月決定不做（例：這個月不存旅遊基金）：不用記一筆假帳，標「這期跳過」
    foot: link.bucket && link.bucket !== "living" && Array.isArray(e.items) ? [h("button", { type: "button", class: "btn",
      title: "這一格這期不做了：不提醒、不留錢；下一期照常", onclick: () => skipBucket(app, { id: link.bucket, name: link.name }, false) }, "⏭ 這期跳過")] : [],
  });
}

async function skipBucket(app, e, undo) {
  try {
    const r = await api(`/api/bucket/${e.id}/skip`, { method: "POST", body: { undo } });
    app.closeModal();
    await app.afterWrite(r, undo ? `${e.name}取消跳過` : `${e.name}這期跳過了（不用記帳，下一期照常）`, r.batch_id);
  } catch (err) { app.toast(errorText(err)); }
}

// 額度挪用：這期從一格挪額度去補另一格。不記帳、不搬錢，只改這期兩格能用多少；每一次都留紀錄。
// 從超過的那一格打開＝預設「挪來補這一格」（金額＝超過多少）；其他格＝預設「從這一格挪出去」。
function moveQuota(app, d, e) {
  const all = d.envelopes;
  const pick = (label, list, value) => h("select", { "aria-label": label },
    h("option", { value: "" }, "請選一格…"),
    list.map((x) => h("option", { value: x.id, selected: x.id === value }, `${x.name}（剩 ${num(x.left)}）`)));
  const over = e.left < 0;
  const from = pick("從哪一格", all, over ? "" : e.id);
  const to = pick("挪到哪一格", all.filter((x) => !x.skipped), over ? e.id : "");
  const amount = h("input", { inputmode: "decimal", value: over ? major(-e.left) : "", "aria-label": "挪多少" });
  const note = h("input", { maxlength: "60", placeholder: "例：生活費超支", "aria-label": "為什麼" });
  const go = h("button", { type: "button", class: "btn primary", onclick: async () => {
    const n = toMinor(amount.value);                   // 照帳本的小數位數換成最小單位（dom.js）
    if (!from.value || !to.value) return app.toast("要選「從哪一格」和「挪到哪一格」");
    if (from.value === to.value) return app.toast("「從哪一格」和「挪到哪一格」要選不一樣的");
    if (!Number.isInteger(n) || n <= 0) return app.toast("「挪多少」要填一個大於 0 的數字");
    try {
      const r = await api("/api/moves", { method: "POST", body: { from_bucket: from.value, to_bucket: to.value, amount: n, note: note.value, period: d.period.key } });
      app.closeModal();
      const m = r.move;
      await app.afterWrite(r, `${m.from_name} → ${m.to_name} 挪了 ${num(m.amount)}` + (r.warnings.length ? `（${r.warnings[0]}）` : ""), r.batch_id);
    } catch (err) { app.toast(errorText(err)); }
  } }, "確認挪用");
  app.openModal({
    title: "挪用額度",
    body: [h("p", { class: "small" }, h("b", null, "挪用＝這期從一格拿額度去補另一格。"),
      "例：生活費超支，從存錢挪一點來補。不記帳、錢不用搬，只是兩格這期能用的數字跟著改；每一次都會留紀錄，可以復原。"),
      h("label", { class: "field" }, "從哪一格", from), h("label", { class: "field" }, "挪到哪一格", to),
      h("label", { class: "field" }, "挪多少", amount), h("label", { class: "field" }, "為什麼（可以不填）", note)],
    foot: [go],
  });
}

// 這期的挪用：清單（復原）＋一行統計。沒有挪用就不出現。
function movesCard(app, mv) {
  if (!mv || !mv.items.length) return null;
  const st = mv.stats;
  const undo = async (m) => {
    try {
      const r = await api(`/api/moves/${m.id}`, { method: "DELETE" });
      await app.afterWrite(r, `拿掉挪用：${m.from_name} → ${m.to_name} ${num(m.amount)}`, r.batch_id);
    } catch (err) { app.toast(errorText(err)); }
  };
  return h("section", { class: "card moves" },
    h("div", { class: "row" }, h("h2", null, "這期的挪用"),
      h("span", { class: "muted small" }, `${st.count} 次，共 ${num(st.total)}`)),
    h("p", { class: "muted small" }, "這期從一格拿額度去補另一格的紀錄（不記帳、錢不用搬）。按「復原」就拿掉那一次。"),
    h("div", { class: "ledger" }, mv.items.map((m) => h("div", { class: "entry nodate" },
      h("span", { class: "what" }, h("b", null, `${m.from_name} → ${m.to_name}`), h("small", null, `${md(m.date)}${m.note ? `・${m.note}` : ""}`)),
      h("span", { class: "money" }, h("span", { class: "amt" }, num(m.amount)),
        h("button", { type: "button", class: "btn small", title: "拿掉這一筆挪用（兩格回到挪之前）", onclick: () => undo(m) }, "復原"))))),
    st.most_taken ? h("p", { class: "note-line info" }, `被挪最多：${st.most_taken.name}（挪出 ${num(st.most_taken.out)}）`
      + (st.top_pair && st.top_pair.count > 1 ? `；最常：${st.top_pair.from_name} → ${st.top_pair.to_name} ${st.top_pair.count} 次` : "")) : null);
}

function envTip(e) {
  const lines = e.rollover
    ? [`${e.name}：這期能用 ${num(e.available)}（撥 ${num(e.plan)}，${e.carry >= 0 ? "前期留下" : "前期多用"} ${num(Math.abs(e.carry))}），用了 ${num(e.used)}`
      + (e.left > 0 ? `，還剩 ${num(e.left)}（留到下期）` : (e.left < 0 ? `，超過 ${num(-e.left)}（下期的額度先補）` : "，剛好用完"))]
    : [`${e.name}：計畫 ${num(e.plan)}，這期做了 ${num(e.used)}` + (e.left > 0 ? `，還差 ${num(e.left)}` : (e.left < 0 ? `，多了 ${num(-e.left)}` : "，剛好做到"))];
  if (e.rollover) lines.push("↻ 滾存：每期撥的額度沒用完就留到下期。兩個月才收一次的帳單，來的那期剛好用完。");
  if (e.id === "living") lines.push("生活費是拿來花的：用掉越多、剩越少。");
  if (e.locked) lines.push("🔒 固定住：這期一定要做到，沒做到會亮燈。");
  lines.push(e.categories.length ? `算進來的類別：${e.categories.join("、")}${e.categories.length >= 8 ? "…" : ""}`
    : (e.id === "living" || !e.plan ? "" : "還沒指定記哪些類別算這一格——按「今天處理」記一筆就會對上，或到「設定→類別」那一格的 🔗 勾選。"));
  if (e.moved) lines.push(`原本計畫 ${num(e.planned)}，這期${e.moved > 0 ? "挪入" : "挪出"} ${num(Math.abs(e.moved))}（看「這期的挪用」）`);
  if (e.prepaid_in) lines.push(`含預繳／補繳進來的 ${num(e.prepaid_in)}（不是這期付的，但算這一期）`);
  if (e.prepaid_out) lines.push(`這期另外付了 ${num(e.prepaid_out)}，是給別期的（不算在這裡）`);
  if ((e.items || []).length) lines.push(`底下有 ${e.items.length} 個固定項目（總額的明細），沒做的各有「今天處理」。`);
  if (e.action) lines.push("按「今天處理」確認就記一筆。");
  return lines.filter(Boolean).join("\n");
}

function envelopes(app, d) {
  const rows = d.envelopes.filter((e) => e.plan || e.used || e.moved || (e.rollover && e.carry)).map((e) => {
    const cls = e.state === "over" ? "over" : (e.state === "done" ? "done" : "");
    const roll = e.rollover ? (e.carry > 0 ? `含上期留下 ${num(e.carry)}` : (e.carry < 0 ? `上期多用 ${num(-e.carry)}，先補` : "")) : "";
    // 狀態一律寫成字（✓ 做完了、≈ 差不多做完、還差 N、超過 N、↻ 留著等帳單、⏭ 這期跳過），不是只有一個符號
    const st = envStatus(e);
    const tail = st ? h("span", { class: `st ${st.cls}`, title: st.title }, st.text) : null;
    const aside = e.set_aside ? h("span", { class: "st roll-mark", title: "這期撥額度的紀錄（做完之後也看得到）" },
      `已撥 ${num(e.set_aside.amount)} → ${setAsideWhere(e.set_aside)}`) : null;
    const loose = e.plan && e.state !== "skipped" && !e.categories.length && !(e.items || []).length && e.id !== "living" && e.id !== "debt";
    // 滾存的格子：撥額度（錢留在帳戶）或今天處理（帳單來了直接記）都可以
    const go = e.state === "skipped" ? [h("button", { type: "button", class: "btn small", title: "取消跳過：這一格這期照常要做",
      onclick: () => skipBucket(app, e, true) }, "取消跳過")] : [e.can_set_aside ? h("button", { type: "button", class: "btn small", title: `把這期的 ${num(e.plan)} 移出日常帳戶（轉到別的帳戶，或預繳給對方）`,
        onclick: () => setAside(app, e) }, "撥額度") : null,
      e.action ? h("button", { type: "button", class: "btn small", title: `確認後記一筆「${e.name}」${num(e.action.amount)}`,
        onclick: () => handle(app, e) }, "今天處理") : null];
    // 挪用：格子上寫這期挪入／挪出多少；⇄ 打開挪用的視窗（進行中的那一期才有）
    const moved = e.moved ? h("small", { class: "muted", title: `原本計畫 ${num(e.planned)}` },
      e.moved > 0 ? ` 挪入 +${num(e.moved)}` : ` 挪出 −${num(-e.moved)}`) : null;
    if (d.period.open && e.state !== "skipped") go.push(h("button", { type: "button", class: "btn small", "aria-label": `挪用：${e.name}`,
      title: e.left < 0 ? "從別格挪額度來補這一格" : "把這一格的額度挪去補別格", onclick: () => moveQuota(app, d, e) }, "⇄ 挪用"));
    // 固定項目：每一項的狀態也寫出來（已記／到期了，還沒記／幾號到期／等帳單進來／這期跳過）
    const itemsBlock = (e.items || []).length ? h("div", { class: "env-items" },
      e.items.map((it) => h("div", { class: "env-item", title: `${it.name}：${it.state}` },
        h("span", { class: "nm" }, it.name, h("small", { class: `muted${it.level === "warn" ? " warn" : ""}` },
          it.done ? `　每月 ${it.day} 號` : `　每月 ${it.day} 號・${it.state}`)),
        h("span", { class: "val" }, it.done ? [num(it.paid), h("span", { class: "ok" }, "✓ 已記")] : (it.amount === null ? "金額不一定" : num(it.amount)),
          it.action ? h("button", { type: "button", class: "btn small", title: `確認後記一筆「${it.name}」`,
            onclick: () => handle(app, { id: e.id, name: it.name, plan: it.amount || 0, used: 0, left: it.amount || 0, action: it.action }, { bucket: e.id, name: e.name }) }, "今天處理") : null))),
      h("div", { class: `env-item other${e.other < 0 ? " bad" : ""}`, title: "這一格的總額減掉上面項目加起來：金額不一定的（電費、雜支）都在這裡。" },
        h("span", null, e.other < 0 ? `項目加起來比總額多 ${num(-e.other)}` : "其他（金額不一定的）"),
        e.other < 0 ? null : h("span", { class: "val" }, `${num(e.other_used)} / ${num(e.other)}`))) : null;
    const quiet = (e.state === "done" || e.state === "skipped") && e.id !== "living" && !(e.items || []).length;    // 做完的：收進「做完的 N 格」
    // 名字是一顆按鈕：點一下跳出這一格的說明（手機沒有「停上去」）。數字＋狀態一段、按鈕一段（手機上按鈕換到下一行）。
    return { quiet, nodes: [h("div", { class: "env", title: envTip(e) },
      h("button", { type: "button", class: "name", "aria-label": `${e.name}的說明`, onclick: () => tipModal(app, e.name, envTip(e)) },
        e.name, e.locked ? h("span", { class: "lock-mark", title: "一定要做到", "aria-label": "一定要做到" }, "🔒") : null,
        loose ? h("span", { class: "loose", title: "還沒指定記哪些類別算這一格", "aria-label": "還沒指定類別" }, "？") : null),
      bar(e.used, e.rollover ? e.available : e.plan, cls),
      e.state === "skipped" ? h("span", { class: "val" }, tail)              // 跳過的：不寫數字，只寫跳過＋取消（不然擠到名字）
        : h("span", { class: "val" }, `${num(e.used)} / ${num(e.rollover ? e.available : e.plan)}`, moved, tail, aside),
      go.some(Boolean) ? h("span", { class: "go" }, go) : null),
    e.rollover ? h("div", { class: "note-line info", title: "滾存：每期撥的額度沒用完就留到下期" },
      `${e.name}：這期撥 ${num(e.plan)}${e.set_aside ? `（已撥 ${num(e.set_aside.amount)}，${setAsideWhere(e.set_aside, true)}）` : ""}${roll ? `，${roll}` : ""}；現在留著 ${num(e.keep)}`) : null, itemsBlock] };
  });
  // 做完的格子收起來（不用每次都拉到很下面）：只留還要處理的；點一下再展開
  const done = rows.filter((r) => r.quiet);
  const open = app.envDoneOpen;
  const doneToggle = done.length ? h("button", { type: "button", class: "btn small env-done-toggle", "aria-expanded": open ? "true" : "false",
    onclick: () => { app.envDoneOpen = !app.envDoneOpen; app.render(); } },
  open ? `收起做完的 ${done.length} 格` : `做完的 ${done.length} 格 ✓（點開）`) : null;
  const shown = rows.filter((r) => !r.quiet || open).map((r) => r.nodes);
  const p = d.plan;
  let note = null;
  if (p.income && p.unassigned > 0) note = h("div", { class: "note-line" }, `還有 ${num(p.unassigned)} 沒分配`);
  else if (p.income && p.unassigned < 0) note = h("div", { class: "note-line bad", title: "每一格的計畫加起來，比這個月收入還多。到規劃看是不是有兩格其實是同一筆錢。" }, `分配比收入多 ${num(-p.unassigned)}`);
  return h("section", { class: "card" }, h("div", { class: "row" }, h("h2", null, "錢怎麼分"),
    h("span", null, p.income ? h("span", { class: "muted small" }, `收入 ${num(p.income)}${p.income_actual ? "" : "（預計）"}`) : null,
      infoButton(app, "錢怎麼分", [
        "薪水先分成一格一格：每一格是這個月要做的事，右邊是「已做 / 計畫」。",
        "記帳時，記到屬於那一格的項目，那一格就自動往前（不用另外按）；做到了會寫「✓ 做完了」，還沒做到寫「還差 N」。",
        "還沒做完的格子後面有「今天處理」：預先填好金額，確認就記一筆。",
        "滾存的格子（「↻ 留著等帳單」，水電瓦斯、手機費）：按「撥額度」把這期的錢移出日常帳戶（轉到別的帳戶或預繳），格子上會寫「已撥 N → 去向」。",
        "滾存的格子也可以直接按「今天處理」：帳單來了就記，不一定要先撥額度。",
        "「≈ 差不多做完」＝金額會浮動的格子（水電、訂閱）差一點點，算做完（在「設定」的格子裡勾「金額會浮動」）。",
        "做完的格子會收起來（「做完的 N 格 ✓」），點一下再打開。",
        "「⇄ 挪用」：這期從一格拿額度去補另一格（例：生活費超支，拿存錢的補）。格子上會寫「挪入 +N／挪出 −N」，下面「這期的挪用」看每一筆、可以復原。",
        "名字後面的 ？＝還沒指定記哪些類別算這一格，到「設定→類別」那一格的 🔗 勾選。",
        "預繳（例：先繳 11 月房租）在明細那一筆選「算哪一期」，這期不會被算成超過，下期就是做到的。",
        "點一下格子的名字（或把滑鼠停在格子上），會看到這一格的說明。所有標示的意思在最上面「？ 標示是什麼意思」。",
      ]))),
  rows.length ? shown : h("p", { class: "empty" }, "還沒分。到「規劃」填薪水怎麼分。"), doneToggle, note);
}

function debts(dd) {
  if (!dd.items.length) return null;
  return h("section", { class: "card" }, h("div", { class: "row" }, h("h2", null, "分期"),
    h("span", { class: "muted small" }, `每月還 ${num(dd.monthly)}` + (dd.share ? `（收入 ${Math.round(dd.share * 100)}%）` : ""))),
  dd.items.map((x) => h("div", { class: "debt" },
    h("div", { class: "row" }, h("b", null, x.name), h("span", { class: "num" }, `${x.paid}/${x.periods} 期`)),
    h("div", { class: "pips", "aria-hidden": "true" }, Array.from({ length: Math.min(x.periods, 36) }, (_, i) => h("i", { class: i < x.paid ? "paid" : null }))),
    h("span", { class: "muted small" }, `每月 ${num(x.per_period)}・還剩 ${num(x.remaining)}`))));
}

function reviewCard(app, rv) {
  if (!rv) return null;
  const icons = [ICON.good, ICON.watch, ICON.next];
  return h("section", { class: "card" },
    h("div", { class: "rv-head" }, h("span", { class: "grade" }, rv.grade || "—"),
      h("div", null, h("span", { class: "muted small" }, `上次檢討・${rv.label}`), h("h3", null, rv.title))),
    h("ul", { class: "points" }, rv.points.map((p, i) => h("li", null, h("span", { "aria-hidden": "true" }, icons[i] || "・"), h("span", null, p)))),
    h("div", { class: "actions" },
      h("button", { type: "button", class: "btn", onclick: () => app.go("review") }, "看檢討"),
      rv.can_apply ? h("button", { type: "button", class: "btn primary", onclick: () => apply(app, rv) }, `照建議分 ${rv.apply_period}`) : null));
}

async function apply(app, rv) {
  try {
    const r = await api(`/api/review/${rv.id}/apply`, { method: "POST", body: {} });
    await app.afterWrite(r, `${r.period} 期照建議分好了`, r.batch_id);
  } catch (e) { app.toast(errorText(e)); }
}

// ---------------------------------------------------------------- 今年

function yearView(y) {
  const stats = h("section", { class: "card" }, h("div", { class: "trio" },
    h("div", null, h("span", null, "收入"), h("b", null, num(y.income))),
    h("div", null, h("span", null, "花掉"), h("b", null, num(y.used))),
    h("div", null, h("span", null, "存下"), h("b", null, num(y.saved)))),
  y.over_months ? h("div", { class: "note-line bad" }, `生活費超支 ${y.over_months} 個月`) : null);
  return h("div", { class: "page-inner" }, stats,
    h("section", { class: "card years" }, h("h2", null, "每個月的生活費"), yearBars(y.months),
      h("p", { class: "muted small" }, "紅色、上面寫「超」＝那個月超過預算；虛線＝預算")));
}

function yearBars(months) {
  const W = 600, H = 180, pad = 22, gap = 8;
  const max = Math.max(1, ...months.filter((m) => !m.future).map((m) => Math.max(m.living, m.living_budget || 0)));
  const bw = (W - gap * 11) / 12;
  const parts = [];
  months.forEach((m, i) => {
    const x = i * (bw + gap);
    parts.push(s("text", { x: x + bw / 2, y: H - 4, "text-anchor": "middle" }, `${m.month}月`));
    if (m.future) return;
    const bh = Math.round(((H - pad - 14) * m.living) / max);
    const y = H - pad - bh;
    parts.push(s("rect", { x, y, width: bw, height: Math.max(bh, m.living ? 2 : 0), rx: 4,
      class: m.over ? "b-over" : "b-ok" }, s("title", null, `${m.month}月：生活費 ${num(m.living)}` +
      (m.living_budget ? ` / 預算 ${num(m.living_budget)}` : "") + `，存下 ${num(m.saved)}` + (m.over ? "（超過預算）" : ""))));
    if (m.over) parts.push(s("text", { x: x + bw / 2, y: Math.max(y - 4, 12), "text-anchor": "middle", class: "over-mark" }, "超"));   // 紅色之外再寫一個字
    if (m.living_budget) {
      const by = H - pad - Math.round(((H - pad - 14) * m.living_budget) / max);
      parts.push(s("line", { x1: x - 2, x2: x + bw + 2, y1: by, y2: by, class: "b-budget" }));
    }
  });
  return s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "每個月的生活費長條圖，虛線是預算" }, parts);
}
