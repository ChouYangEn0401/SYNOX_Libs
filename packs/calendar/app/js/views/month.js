// 月曆總覽：整週對齊的格子（週一起算）。格子只是「看」；點一格去「這一天」改東西。
// 分類開關（行程、卡片、外部項目）只影響畫面，不改資料。
import { read } from "../api.js";
import { h, WEEK } from "../dom.js";

const KINDS = [["event", "行程"], ["task", "今日清單卡片"], ["link", "別的工具的項目"]];
const hiddenKinds = new Set();                     // 這次開著畫面期間記住，不存起來

function chip(item) {
  const cls = ["chip", item.kind, item.done ? "done" : "", item.clash && item.clash.length ? "clash" : "", item.is_focus ? "focus" : "",
    item.status === "cancelled" ? "cancelled" : ""].filter(Boolean).join(" ");
  const mark = item.clash && item.clash.length ? "⚠ " : item.is_focus ? "⭐ " : item.kind === "task" ? "☐ " : "";
  return h("div", { class: cls, title: item.text }, (item.at ? `${item.at} ` : "") + mark + item.text);
}

export async function render(app) {
  const m = await read("month", { month: app.month });
  const items = (d) => d.items.filter((x) => !hiddenKinds.has(x.kind));
  const toggles = KINDS.filter(([k]) => k === "event" || k === "task" || m.counts.links > 0).map(([k, name]) =>
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: !hiddenKinds.has(k),
      onchange: (ev) => { if (ev.target.checked) hiddenKinds.delete(k); else hiddenKinds.add(k); app.render(); } }), name));

  const head = WEEK.split("").slice(0, 7).map((w) => h("div", { class: "dow" }, w));
  const cells = m.days.map((d) => {
    const shown = items(d);
    const more = shown.length - m.max_per_day;
    return h("button", { type: "button", class: ["cell", d.in_month ? "" : "out", d.is_today ? "today" : "", d.is_past ? "past" : ""]
      .filter(Boolean).join(" "), "aria-label": `${d.date}，${shown.length} 件`, onclick: () => app.go("day", { date: d.date }) },
    h("span", { class: "num" }, String(d.day)),
    shown.slice(0, m.max_per_day).map(chip),
    more > 0 ? h("div", { class: "more" }, `還有 ${more} 件`) : null);
  });
  return h("div", null,
    h("div", { class: "card summary" },
      h("span", null, `本月 ${m.counts.events} 個行程`),
      h("span", null, `${m.counts.tasks} 張卡片`),
      m.counts.links ? h("span", null, `${m.counts.links} 個外部項目`) : null,
      h("span", { class: "grow" }), toggles),
    h("div", { class: "month" }, head, cells),
    h("p", { class: "muted" }, "這裡只是看；點一天進去改。"));
}
