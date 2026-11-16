// 檢討：每份一張卡（從原本的畫面原樣搬來）。
// 收合＝只看大事（等第、標題、一句話、標成「重要」的那幾點）；展開＝照脈絡分成三區（做得好／要注意／下一步），
// 每區有小標題，重要的那幾點標 ❗ 排在前面。
// 這一版只改了沒有檢討時的那句話：檢討由 SYNOX 的 AI 助理讀報表數字寫好再存回來（/money_report、/money_review_save）。
import { api, errorText } from "../api.js";
import { h, num } from "../dom.js";

const KINDS = [["month", "月報"], ["week", "週報"]];
const GROUPS = [["good", "✅", "做得好"], ["watch", "⚠️", "要注意"], ["strategy", "👉", "下一步"]];
let kind = "month";
let open = null;

export async function render(app) {
  const d = await api("/api/reviews", { params: { period: app.period, kind } });
  app.hideNav();                                          // 檢討不分月份：全部列出來
  const done = d.reviews.filter((r) => r.status === "done");
  const cards = await Promise.all(done.slice(0, 12).map((r) => api(`/api/review/${r.id}`)));
  const seg = h("div", { class: "seg", role: "group", "aria-label": "月報或週報" }, KINDS.map(([id, name]) => h("button", {
    type: "button", "aria-pressed": kind === id ? "true" : "false", onclick: () => { kind = id; open = null; app.render(); },
  }, name)));
  return h("div", { class: "page-inner" }, seg,
    cards.length ? cards.map((rv) => card(app, rv))
      : h("section", { class: "card" }, h("p", { class: "empty" },
        kind === "month" ? "還沒有月報。每期快結束時，請 AI 助理照記帳手冊幫你寫一份，寫好會出現在這裡。"
          : "還沒有週報。每週請 AI 助理照記帳手冊幫你看一次，寫好會出現在這裡。")));
}

// 一點：重要的前面加 ❗、字粗一點；其他的小圓點。
const point = (p, icon) => h("li", { class: p.level === "key" ? "key" : null },
  h("span", { "aria-hidden": "true" }, p.level === "key" ? "❗" : icon), h("span", null, p.text));

// 收合時的「大事」：每一組標成 key 的都算大事；一個 key 都沒有（舊的檢討）就退回每組第一點。
function headline(rv) {
  const keys = GROUPS.flatMap(([k, icon]) => (rv[k] || []).filter((p) => p.level === "key").map((p) => point(p, icon)));
  if (keys.length) return keys;
  return GROUPS.map(([k, icon]) => ((rv[k] || []).length ? point(rv[k][0], icon) : null)).filter(Boolean);
}

function section(rv, key, icon, name) {
  const list = rv[key] || [];
  if (!list.length) return null;
  const sorted = [...list].sort((a, b) => (a.level === "key" ? 0 : 1) - (b.level === "key" ? 0 : 1));
  return h("div", { class: "rv-sec" },
    h("h4", null, h("span", { "aria-hidden": "true" }, icon), name),
    h("ul", { class: "points" }, sorted.map((p) => point(p, icon))));
}

function card(app, rv) {
  const expanded = open === rv.id;
  return h("section", { class: "card" },
    h("div", { class: "rv-head" }, h("span", { class: "grade" }, rv.grade || "—"),
      h("div", null, h("span", { class: "muted small" }, rv.label), h("h3", null, rv.title || rv.summary))),
    expanded ? null : h("ul", { class: "points" }, headline(rv)),
    expanded ? h("div", { class: "rv-body" },
      rv.summary ? h("p", { class: "muted" }, rv.summary) : null,
      GROUPS.map(([key, icon, name]) => section(rv, key, icon, name)),
      suggestion(app, rv)) : null,
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn small", onclick: () => { open = expanded ? null : rv.id; app.render(); } },
      expanded ? "收起來" : "看全部")));
}

function suggestion(app, rv) {
  const s = rv.suggestion;
  if (!s) return null;
  const cur = rv.current_plan || { buckets: {} };
  const rows = Object.entries(s.buckets).filter(([, v]) => v).map(([b, v]) => h("div", { class: "env" },
    h("span", { class: "name" }, rv.bucket_names[b] || b), h("span", { class: "muted small" }, `現在 ${num(cur.buckets[b] || 0)}`),
    h("span", { class: "val" }, num(v))));
  let action;
  if (rv.applied_period) action = h("div", { class: "note-line good" }, `已經照這個分 ${rv.applied_period}`);
  else if (s.valid) action = h("button", { type: "button", class: "btn primary", onclick: () => apply(app, rv) }, `照這個分 ${s.period}`);
  else action = h("div", { class: "note-line" }, "這份建議的數字對不起來，沒辦法直接用");
  return h("div", { class: "rv-sec" }, h("h4", null, h("span", { "aria-hidden": "true" }, "📊"), `建議 ${s.period} 怎麼分`),
    rows, action);
}

async function apply(app, rv) {
  try {
    const r = await api(`/api/review/${rv.id}/apply`, { method: "POST", body: {} });
    await app.afterWrite(r, `${r.period} 照建議分好了`, r.batch_id);
  } catch (e) { app.toast(errorText(e)); }
}
