// 接下來：往後 30 天一天一段；撞時間的放最上面，可以按「兩個都留」。
import { act, errorText, read } from "../api.js";
import { h, md } from "../dom.js";

export async function render(app) {
  const a = await read("agenda", { from: app.today, days: 30 });
  const keep = async (c) => {
    try {
      await act("keep", { a: c.a.id, b: c.b.id });
      await app.afterWrite("兩個都留了");
    } catch (e) { if (e.code !== "cancelled") app.toast(errorText(e)); }
  };
  const busy = a.days.filter((d) => d.items.length);
  return h("div", null,
    a.conflicts.length ? h("div", { class: "card warn" }, h("strong", null, `⚠ ${a.conflicts.length} 組撞時間`),
      a.conflicts.map((c) => h("div", { class: "row" }, h("span", { class: "grow" }, c.text),
        h("button", { type: "button", class: "btn small", onclick: () => keep(c) }, "兩個都留")))) : null,
    busy.length ? busy.map((d) => h("section", { class: "card" },
      h("h3", null, h("button", { type: "button", class: "link", onclick: () => app.go("day", { date: d.date }) },
        `${md(d.date)}（${d.weekday}）${d.date === app.today ? "　今天" : ""}`)),
      d.items.map((c) => h("div", { class: `agenda-item ${c.status}` },
        h("span", { class: "when" }, c.all_day ? "整天" : c.range || c.time),
        h("span", { class: "grow" }, (c.series_id ? "🔁 " : "") + c.title, c.location ? h("span", { class: "muted" }, `　@${c.location}`) : null),
        c.status !== "planned" ? h("span", { class: "badge" }, { done: "✔", not_done: "🙅", cancelled: "🚫" }[c.status]) : null))))
      : h("p", { class: "muted" }, "接下來 30 天沒有行程。按右上角「＋ 行程」加一個。"));
}
