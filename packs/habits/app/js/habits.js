// 習慣頁：跨習慣的打卡矩陣、每條習慣的卡片（打卡、加碼、記時間、暫停、這期夠了、釘選、編輯、刪除）、編輯器。
import { read, act } from "./api.js";
import { h, fill, mmdd } from "./dom.js";
import { go, guard, openModal, closeModal, ask, toast } from "./main.js";

let range = 7;
let showSide = false;
let showEnded = false;
let meta = null;

export async function render(root) {
  const [m, r] = await Promise.all([read("meta"), read("routines")]);
  meta = m;
  const rows = r.routines || [];
  const live = rows.filter((x) => !x.period_ended);
  const core = live.filter((x) => x.section !== "side");
  const side = live.filter((x) => x.section === "side");
  const ended = rows.filter((x) => x.period_ended);
  const sorted = (a) => [...a].sort((x, y) => (y.pinned ? 1 : 0) - (x.pinned ? 1 : 0));
  fill(root,
    h("div", { class: "bar" },
      h("h1", null, "今天 ", h("small", null, mmdd(m.today))),
      h("button", { type: "button", class: "btn primary", onclick: () => editor(null) }, "＋ 加一條")),
    dash(sorted(core)),
    core.length ? sorted(core).map(card) : h("p", { class: "muted" }, "還沒有習慣。按「加一條」開始，預設撐 30 天。"),
    group(`加分／提醒（${side.length}）`, side, showSide, (v) => { showSide = v; }, sorted),
    group(`已結束（${ended.length}）`, ended, showEnded, (v) => { showEnded = v; }, sorted),
    h("label", { class: "opt" },
      h("input", { type: "checkbox", checked: !!m.show_missed_red,
        onchange: (e) => run(() => act("display", { red: e.target.checked ? "yes" : "no" })) }),
      " 沒做的格子畫成紅色（預設不畫，只標「沒做」）"));
}

function group(title, rows, open, set, sorted) {
  if (!rows.length) return null;
  return h("details", { class: "grp", open: open ? "" : null, ontoggle: (e) => set(e.target.open) },
    h("summary", null, title), sorted(rows).map(card));
}

// 做一個動作，成功就重畫；失敗提示那句話
async function run(fn, done) {
  const out = await guard(fn);
  if (out === null) return null;
  if (done) done(out);
  await go("habits");
  return out;
}

// ---- 矩陣：橫軸日期、縱軸習慣；做了／沒做／待打卡／不用做
function dash(list0) {
  const list = list0.filter((r) => r.kind === "habit" && r.matrix && r.matrix.length);
  if (!list.length) return null;
  const red = !!meta.show_missed_red;
  const days = list[0].matrix.slice(-range);
  const cls = (c) => (c.status === "missed" || c.status === "skip" ? (red ? "missed" : "neutral") : c.status || "na");
  return h("section", { class: "card dash" },
    h("div", { class: "bar" }, h("b", null, "打卡矩陣"),
      h("span", { class: "seg" }, [7, 14, 31].map((n) => h("button", { type: "button", class: "btn small" + (n === range ? " primary" : ""),
        onclick: () => { range = n; go("habits"); } }, `${n} 天`)))),
    h("div", { class: "scroll" }, h("table", { class: "matrix" },
      h("thead", null, h("tr", null, h("th"), days.map((c) => h("th", null, mmdd(c.date))))),
      h("tbody", null, list.map((r) => h("tr", null, h("th", { class: "rowname" }, r.pinned ? "📌 " : "", r.name),
        r.matrix.slice(-range).map((c) => h("td", { class: "cell " + cls(c), title: `${c.date}　${c.status}` }))))))),
    h("p", { class: "legend muted" }, h("i", { class: "sw done" }), "做了　",
      h("i", { class: "sw " + (red ? "missed" : "neutral") }), red ? "沒做到　" : "沒做　",
      h("i", { class: "sw pending" }), "待打卡　", h("i", { class: "sw na" }), "不用做／還沒開始"));
}

// ---- 一條習慣
function badge(r) {
  if (r.paused) return h("span", { class: "badge" }, "⏸ 暫停中" + (r.paused_until ? `（到 ${mmdd(r.paused_until)}）` : ""));
  if (r.enough_now) return h("span", { class: "badge" }, "🙆 這期夠了");
  if (r.not_started) return h("span", { class: "badge" }, `⏳ ${r.days_to_start} 天後開始`);
  if (r.period_ended) return h("span", { class: "badge" }, "承諾期結束");
  if (r.locked) return h("span", { class: "badge" }, "🔒 承諾期");
  return null;
}

function progress(r) {
  const q = r.quota;
  if (!q) return null;
  const pct = q.times ? Math.min(100, Math.round((q.count / q.times) * 100)) : 0;
  return h("div", { class: "quota" },
    h("div", { class: "meter" }, h("i", { class: q.met ? "met" : "", style: { width: pct + "%" } })),
    h("div", { class: "muted" }, q.progress_text || `${q.label || ""} ${q.count}/${q.times}`, q.met ? " 🎉" : ""));
}

function card(r) {
  const checkable = r.can_check && !r.paused;
  const st = r.today_state;
  const chk = (state, label, cls) => h("button", { type: "button", class: "btn small " + (cls || ""),
    onclick: () => run(() => act("check", { ref: r.id, state })) }, label);
  const buttons = [];
  if (r.kind === "habit" && checkable) {
    if (r.unit === "minutes") {
      const inp = h("input", { type: "text", class: "mins", placeholder: "分鐘（45、1h、-15）", "aria-label": "做了多久" });
      buttons.push(inp, h("button", { type: "button", class: "btn small primary",
        onclick: () => inp.value.trim() && run(() => act("log", { ref: r.id, minutes: inp.value.trim() })) }, "記時間"));
    } else if (r.repeat === "quota" || r.mode === "reps") {
      buttons.push(chk("rep", "＋1 做了", "primary"), r.today_reps > 0 ? chk("unrep", "−1 按錯了") : null);
    } else {
      buttons.push(chk("done", st === "done" ? "✅ 做了" : "✓ 做了", st === "done" ? "ok" : "primary"));
      if (r.can_cancel) buttons.push(chk("cancel", st === "cancel" ? "⏭ 已跳過" : "✗ 今天跳過"));
      if (st === "done" || st === "cancel") buttons.push(chk("none", "清掉"));
    }
  } else if (r.kind === "reminder") {
    buttons.push(chk("done", st === "done" ? "✅ 好了" : "✓ 好了", "primary"));
  }
  const more = [];
  if (r.kind === "habit") {
    more.push(h("button", { type: "button", class: "btn small ghost", onclick: () => pauseFlow(r) }, r.paused ? "接著做" : "⏸ 暫停"),
      h("button", { type: "button", class: "btn small ghost",
        onclick: () => run(() => act("enough", { ref: r.id, off: r.enough_now ? "yes" : "" })) }, r.enough_now ? "收回「夠了」" : "🙆 這期夠了"));
  }
  more.push(
    h("button", { type: "button", class: "btn small ghost",
      onclick: () => run(() => act("pin", { ref: r.id, off: r.pinned ? "yes" : "" })) }, r.pinned ? "取消釘選" : "📌 釘選"),
    h("button", { type: "button", class: "btn small ghost", onclick: () => editor(r) }, "✎ 編輯"),
    h("button", { type: "button", class: "btn small danger", onclick: () => remove(r) }, "刪除"));
  const s = r.stats || {};
  return h("section", { class: "card habit" + (r.paused || r.enough_now ? " rest" : ""), id: "r-" + r.id },
    h("div", { class: "bar" }, h("b", null, r.pinned ? "📌 " : "", r.name), badge(r)),
    h("div", { class: "muted" }, r.desc, r.at ? `　${r.at} 提醒` : "", r.end_date ? `　到 ${mmdd(r.end_date)}` : ""),
    r.why ? h("div", { class: "why" }, "💭 " + r.why) : null,
    progress(r),
    r.repeat !== "quota" && r.mode === "reps" && r.today_reps ? h("div", { class: "muted" }, `今天做了 ${r.today_reps} 次`) : null,
    r.nudge ? h("div", { class: "nudge" }, r.nudge) : null,
    r.kind === "habit" && !r.quota && s.streak !== undefined ? h("div", { class: "muted" }, `連續 ${s.streak} 天`) : null,
    h("div", { class: "acts" }, buttons),
    h("div", { class: "acts more" }, more));
}

function pauseFlow(r) {
  if (r.paused) { run(() => act("pause", { ref: r.id, off: "yes" })); return; }
  const until = h("input", { type: "date", "aria-label": "哪天回來" });
  openModal(`暫停「${r.name}」`,
    [h("p", { class: "muted" }, "暫停不算失敗。不選日期＝自己說回來才回來。"), h("label", { class: "field" }, "哪天回來（選填）", until)],
    [{ label: "取消", cls: "ghost" },
      { label: "暫停", cls: "primary", onclick: () => { run(() => act("pause", { ref: r.id, until: until.value })); } }]);
}

// 承諾期內要動它：meta 給三道「我堅持」的樣板，這裡照順序問
async function insist(r, verb) {
  const fmt = (t) => String(t).replaceAll("{name}", r.name).replaceAll("{verb}", verb);
  for (const step of meta.insist_steps || []) {
    if (!(await ask(fmt(step.title), fmt(step.body), fmt(step.yes)))) return false;
  }
  return true;
}

async function remove(r) {
  let force = "";
  if (r.locked) {
    if (!(await insist(r, "刪掉"))) return;
    force = "yes";
  } else if (!(await ask("刪掉這一條？", `刪掉「${r.name}」和它所有的打卡紀錄。`, "刪掉"))) return;
  run(() => act("rm", { ref: r.id, force }), () => toast("刪掉了"));
}

// ---- 編輯器
const opts = (list, cur) => list.map((o) => h("option", { value: String(o.id), selected: String(o.id) === String(cur) }, o.label));

function editor(r) {
  const v = r || { kind: "habit", repeat: "daily", at: "21:00", weekdays: [], nth: 1, nth_weekday: 0, every_days: 7, period: "week",
    times: 3, unit: "count", mode: "once", backfill_mode: "h12", importance: "core", habit_kind: "build", catch_up: true,
    extra_days: true, cap_per_day: false, why: "", note: "", end_date: "", floor_min: 0, target_min: 0, nudges: {} };
  const f = {};
  const input = (key, attrs) => (f[key] = h("input", attrs));
  const select = (key, list, cur) => (f[key] = h("select", null, opts(list, cur)));
  const check = (key, label, on) => {
    f[key] = h("input", { type: "checkbox", checked: !!on });
    return h("label", { class: "opt" }, f[key], " " + label);
  };
  const field = (label, node, hint) => h("label", { class: "field" }, label, node, hint ? h("small", { class: "muted" }, hint) : null);
  const days = meta.weekdays.map((w, i) => h("label", { class: "chip" },
    h("input", { type: "checkbox", value: String(i), checked: (v.weekdays || []).includes(i) }), w));
  const nudges = meta.nudges.map((n) => h("label", { class: "opt", title: n.hint },
    h("input", { type: "checkbox", dataset: { nudge: n.id }, checked: (v.nudges || {})[n.id] ?? n.default }), " " + n.label));
  const nths = Object.entries(meta.nths).map(([id, label]) => ({ id, label }));
  const wds = meta.weekdays.map((label, id) => ({ id, label }));
  openModal(r ? "編輯" : "加一條", [
    field("名字", input("name", { type: "text", maxlength: "40", value: v.name || "" })),
    field("種類", select("kind", meta.kinds, v.kind)),
    field("週期", select("repeat", meta.repeats, v.repeat)),
    h("div", { class: "field" }, "星期幾（每週用）", h("div", { class: "chips" }, days)),
    field("每幾天一次（每 N 天用）", input("every_days", { type: "number", min: "1", value: v.every_days })),
    field("每月第幾個", select("nth", nths, v.nth)),
    field("星期幾（每月用）", select("nth_weekday", wds, v.nth_weekday)),
    field("一期是（一期幾次用）", select("period", meta.periods, v.period)),
    field("單位", select("unit", meta.units, v.unit)),
    field("一期做幾次（次數）", input("times", { type: "number", min: "1", max: String(meta.max_times), value: v.times })),
    field("底線（時間，例：90、1.5h）", input("floor_min", { type: "text", value: v.floor_min ? String(v.floor_min) : "" }), "做到就收工"),
    field("目標（時間，選填）", input("target_min", { type: "text", value: v.target_min ? String(v.target_min) : "" }), "只是往上看的方向，不拿來催你"),
    check("cap_per_day", "一天最多算一次", v.cap_per_day),
    field("每天幾點提醒（留白＝不提醒）", input("at", { type: "time", value: v.at || "" })),
    field("為什麼要做（選填）", input("why", { type: "text", maxlength: "200", value: v.why || "" })),
    field("備註", input("note", { type: "text", maxlength: "200", value: v.note || "" })),
    field("打卡方式", select("mode", meta.habit_modes, v.mode)),
    field("補打卡", select("backfill_mode", meta.backfill_modes, v.backfill_mode)),
    field("重要度", select("importance", meta.importances, v.importance)),
    field("型態", select("habit_kind", meta.habit_kinds, v.habit_kind)),
    check("extra_days", "不用做的日子也能打卡", v.extra_days),
    check("catch_up", "過了時間還沒做就再叫一次", v.catch_up),
    h("div", { class: "field" }, "一期幾次的提醒時機", nudges),
    field("承諾到哪天（想養成才要）", input("end_date", { type: "date", value: v.end_date || "" }),
      v.locked ? "承諾期內要改，儲存時會再確認三次" : "不填＝30 天"),
  ], [
    { label: "取消", cls: "ghost" },
    { label: "儲存", cls: "primary", onclick: () => save(r, f, days, nudges) },
  ]);
}

async function save(r, f, days, nudges) {
  const val = (k) => (f[k].type === "checkbox" ? f[k].checked : f[k].value.trim());
  const kind = val("kind");
  const repeat = val("repeat");
  const item = { name: val("name"), kind, repeat, at: val("at"), why: val("why"), note: val("note"), mode: val("mode"),
    backfill_mode: val("backfill_mode"), importance: val("importance"), habit_kind: val("habit_kind"),
    extra_days: val("extra_days"), catch_up: val("catch_up"), cap_per_day: val("cap_per_day"),
    weekdays: days.filter((l) => l.firstChild.checked).map((l) => Number(l.firstChild.value)),
    every_days: Number(val("every_days")) || 7, nth: Number(val("nth")), nth_weekday: Number(val("nth_weekday")),
    period: val("period"), times: Number(val("times")) || 3,
    nudges: Object.fromEntries(nudges.map((l) => [l.firstChild.dataset.nudge, l.firstChild.checked])) };
  if (repeat === "quota" && val("unit") === "minutes") {
    Object.assign(item, { unit: "minutes", floor_min: val("floor_min"), target_min: val("target_min") });
  } else {
    item.unit = "count";
  }
  if (kind === "habit") {
    item.end_date = val("end_date");
    if (!item.end_date && !r) item.end_date = new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10);
    if (r && r.locked && item.end_date && item.end_date !== r.end_date) {
      if (!(await insist(r, "改承諾期"))) return false;
      item.force = true;
    }
  }
  if (r) item.id = r.id;
  const out = await guard(() => act("save", { json: JSON.stringify(item) }));
  if (out === null) return false;
  closeModal();
  go("habits");
  return true;
}
