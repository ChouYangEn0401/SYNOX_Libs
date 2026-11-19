// 表單：行程（新增／修改）、今日清單的卡片。送出只叫動作（add、edit、task、task_edit）；欄位檢查在伺服器那一側。
import { act, errorText } from "./api.js";
import { h } from "./dom.js";

const field = (label, input, hint) => h("label", { class: "field" }, label, input, hint ? h("span", { class: "muted" }, hint) : null);
const REPEAT = [["", "不重複"], ["一二三四五", "每個工作日"], ["二四", "每週二、四"], ["六日", "每週末"], ["custom", "自己寫…"]];

function saveButton(label, run, app) {
  let busy = false;
  const btn = h("button", { type: "button", class: "btn primary" }, label);
  btn.addEventListener("click", async () => {
    if (busy) return;                                   // 不會連點送兩次
    busy = true;
    btn.disabled = true;
    try { await run(); } catch (e) { if (e.code !== "cancelled") app.toast(errorText(e)); } finally { busy = false; btn.disabled = false; }
  });
  return btn;
}

// 開一個行程表單。init：{date}（新增）或 {event}（修改；event.id 是編號或 編號@日期）
export function eventForm(app, init) {
  const ev = init.event || null;
  const inputs = {
    title: h("input", { value: ev ? ev.title : "", placeholder: "例：看牙醫", maxlength: "120" }),
    date: h("input", { type: "date", value: ev ? ev.day : init.date }),
    allDay: h("input", { type: "checkbox", checked: ev ? ev.all_day : false }),
    start: h("input", { type: "time", value: ev && !ev.all_day ? ev.start.slice(11, 16) : "" }),
    end: h("input", { type: "time", value: ev && !ev.all_day ? ev.end.slice(11, 16) : "" }),
    endDate: h("input", { type: "date", value: ev && ev.end.slice(0, 10) !== ev.day ? ev.end.slice(0, 10) : "" }),
    location: h("input", { value: ev ? ev.location : "", maxlength: "120" }),
    note: h("textarea", { rows: "3", maxlength: "1000" }, ev ? ev.note : ""),
    repeat: h("select", null, REPEAT.map(([v, t]) => h("option", { value: v }, t))),
    repeatText: h("input", { placeholder: "例：二四、週末、一到五", hidden: true }),
    until: h("input", { type: "date", value: ev && ev.repeat_rule ? ev.repeat_rule.until : "" }),
  };
  const occurrence = Boolean(ev && ev.occurrence);       // 只改某一次：不能改重複規則
  inputs.repeat.addEventListener("change", () => { inputs.repeatText.hidden = inputs.repeat.value !== "custom"; });
  const timeBox = h("div", { class: "grid2" }, field("開始", inputs.start), field("結束", inputs.end));
  const syncAllDay = () => { timeBox.hidden = inputs.allDay.checked; };
  inputs.allDay.addEventListener("change", syncAllDay);
  syncAllDay();

  const submit = async () => {
    const v = { date: init.series ? "" : inputs.date.value, location: inputs.location.value.trim(), note: inputs.note.value.trim(),
      end_date: init.series ? "" : inputs.endDate.value };
    const title = inputs.title.value.trim();
    if (!title) throw new Error("行程要有名字");
    if (!inputs.allDay.checked && !inputs.start.value) throw new Error("要有開始時間，或勾「整天」");
    v.time = inputs.allDay.checked ? "-" : inputs.start.value + (inputs.end.value ? `-${inputs.end.value}` : "");
    if (!occurrence) {
      const rule = inputs.repeat.value === "custom" ? inputs.repeatText.value.trim() : inputs.repeat.value;
      if (rule) { v.repeat = rule; v.until = inputs.until.value || (ev ? "-" : ""); }   // 修改時清空＝「-」（一直重複），空字串會被 api.js 丟掉 else if (ev && ev.repeat) { v.repeat = "none"; }
    }
    if (ev) {                                            // 修改：空白 ＝ 清掉（-）
      await act("edit", { n: ev.id, title, ...v, location: v.location || "-", note: v.note || "-",
        end_date: init.series ? "" : v.end_date || "-" });
    } else {
      await act("add", { text: title, ...v, time: v.time === "-" ? "" : v.time });
    }
    app.closeModal();
    await app.afterWrite(ev ? "改好了" : "加好了");
  };

  const body = [
    field("名稱", inputs.title),
    init.series ? h("p", { class: "muted" }, "改的是整串重複行程（開始那天不變）。") : field("日期", inputs.date),
    h("label", { class: "check" }, inputs.allDay, "整天"),
    timeBox,
    init.series ? null : field("跨日到（選填）", inputs.endDate, "出差、連假這種好幾天的"),
    field("地點", inputs.location),
    field("備註", inputs.note),
    occurrence ? h("p", { class: "muted" }, "這是重複行程的其中一次：只會改這一次（原本那天會跳過）。")
      : [field("重複", inputs.repeat), inputs.repeatText, field("重複到（選填）", inputs.until)],
  ];
  app.openModal({ title: ev ? "修改行程" : "新增行程", body, foot: [saveButton("存起來", submit, app)] });
  inputs.title.focus();
}

// 今日清單的卡片。init：{date}（新增）或 {task}
export function taskForm(app, init) {
  const t = init.task || null;
  const inputs = {
    title: h("input", { value: t ? t.title : "", maxlength: "120", placeholder: "要做的事" }),
    date: h("input", { type: "date", value: t ? t.date : init.date }),
    priority: h("select", null, [["0", "沒標"], ["1", "低"], ["2", "中"], ["3", "高"]].map(([v, n]) =>
      h("option", { value: v, selected: String(t ? t.priority : 0) === v }, n))),
    detail: h("textarea", { rows: "3", maxlength: "1000" }, t ? t.detail : ""),
  };
  const submit = async () => {
    const title = inputs.title.value.trim();
    if (!title) throw new Error("卡片要有標題");
    const detail = inputs.detail.value.trim();
    if (t) await act("task_edit", { n: t.id, title, date: inputs.date.value, priority: inputs.priority.value, detail: detail || "-" });
    else await act("task", { text: title, date: inputs.date.value, priority: inputs.priority.value, detail });
    app.closeModal();
    await app.afterWrite(t ? "改好了" : "加進清單了");
  };
  app.openModal({ title: t ? "修改卡片" : "新增卡片",
    body: [field("標題", inputs.title), field("排在哪天", inputs.date), field("重要程度", inputs.priority), field("細節", inputs.detail)],
    foot: [saveButton("存起來", submit, app)] });
  inputs.title.focus();
}
