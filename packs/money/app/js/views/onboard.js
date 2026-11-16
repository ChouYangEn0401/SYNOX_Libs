// 第一次使用的三步驟引導（W5 新寫）：選幣別與每期起算日 → 選起始類別與選配功能 → 每期大概收入與生活費（可跳過）。
// 總覽讀回 {"onboarding": true} 時由 home.js 叫 start(app)：回一個節點＝總覽整頁顯示它，直到引導做完（做完叫 app.afterWrite 重畫）。
// 只經 ../api.js（裡面是 SynoxApp.read／act）；不自己連線、不用瀏覽器儲存。
// 已經有帳的帳本（例如舊版轉進來的）不能選起始類別：跳過第 2 步，第 1 步按「下一步」就做完設定。
import { api, errorText } from "../api.js";
import { fill, h, setDecimals, toMinor } from "../dom.js";

const DAYS = Array.from({ length: 31 }, (_, i) => i + 1);

// 空白回 null；其他照 dom.js 的 toMinor（照幣別的小數位數換成最小單位；看不懂回 NaN）。
const minorOrNull = (text) => (String(text ?? "").trim() === "" ? null : toMinor(text));

export async function start(app) {
  let info;
  try {
    info = await api("/api/features");
  } catch (e) {
    return null;                                          // 讀不到就不擋總覽（下次打開再問）
  }
  if (!info || !info.currency) return null;               // 讀回來的東西不完整：同樣不擋總覽
  if (app.hideNav) app.hideNav();
  const box = h("section", { class: "card hero first-run onboard" });
  const state = { currency: info.currency.code, startDay: info.start_day, seed: "generic", features: new Set(),
    decimals: info.currency.decimals, step: 1 };
  const steps = info.can_seed ? 3 : 2;
  const head = (n, title) => [h("span", { class: "label" }, `第一次用：${n}／${steps}`), h("h2", null, title)];

  const fail = (e) => { if (e.code !== "cancelled") app.toast(errorText(e)); };

  const init = async () => {
    const body = { currency: state.currency, start_day: state.startDay, features: [...state.features] };
    if (info.can_seed) body.seed = state.seed;
    const r = await api("/api/init", { method: "POST", body });
    if (r && r.write_seq) app.seq = r.write_seq;          // 自己寫的：回到分頁時不要當成別人寫的
    state.decimals = r.currency ? r.currency.decimals : state.decimals;
    setDecimals(state.decimals);                          // 剛選的幣別：之後的金額都照它換算、顯示
    return r;
  };

  const step1 = () => {
    const cur = h("select", { "aria-label": "幣別" }, info.currencies.map((c) =>
      h("option", { value: c.code, selected: c.code === state.currency }, `${c.name}（${c.symbol}）`)));
    const day = h("select", { "aria-label": "每期從幾號開始" }, DAYS.map((d) =>
      h("option", { value: String(d), selected: d === state.startDay }, `${d} 號`)));
    const next = h("button", { type: "button", class: "btn primary", onclick: async () => {
      state.currency = cur.value;
      state.startDay = Number(day.value);
      if (info.can_seed) { show(step2); return; }
      next.disabled = true;
      try { await init(); show(step3); } catch (e) { next.disabled = false; fail(e); }
    } }, "下一步");
    return [...head(1, "用什麼錢、每期從幾號開始"),
      h("p", { class: "muted" }, "都有預設，不確定就直接按下一步，之後在「設定」都能改。"),
      h("label", { class: "field" }, "幣別", cur),
      h("label", { class: "field" }, "每期從幾號開始（通常是發薪日）", day),
      h("div", { class: "actions" }, next)];
  };

  const step2 = () => {
    const seeds = info.seeds.map((s) => {
      const radio = h("input", { type: "radio", name: "onboard-seed", value: s.id, checked: s.id === state.seed });
      radio.addEventListener("change", () => { state.seed = s.id; });
      return h("label", { class: "pick" }, radio, h("span", null, h("b", null, s.name), `：${s.summary}`));
    });
    const feats = info.features.filter((f) => !f.enabled).map((f) => {
      const box = h("input", { type: "checkbox", checked: state.features.has(f.name) });
      box.addEventListener("change", () => { if (box.checked) state.features.add(f.name); else state.features.delete(f.name); });
      return h("label", { class: "pick" }, box, h("span", null, f.summary || f.name));
    });
    const next = h("button", { type: "button", class: "btn primary", onclick: async () => {
      next.disabled = true;
      try { await init(); show(step3); } catch (e) { next.disabled = false; fail(e); }
    } }, "下一步");
    return [...head(2, "起始類別與選配功能"),
      h("p", { class: "muted" }, "起始類別只能現在選；之後在「設定」的「類別」自己加、改、停用。"),
      h("div", { class: "pick-group" }, seeds),
      feats.length ? h("p", { class: "muted small" }, "要不要多開這些（不確定就先不開，之後在「設定」一鍵打開）：") : null,
      feats.length ? h("div", { class: "pick-group" }, feats) : null,
      h("div", { class: "actions" },
        h("button", { type: "button", class: "btn", onclick: () => show(step1) }, "上一步"), next)];
  };

  const step3 = () => {
    const income = h("input", { inputmode: "decimal", placeholder: "例：40000", "aria-label": "每期大概收入多少" });
    const living = h("input", { inputmode: "decimal", placeholder: "例：15000", "aria-label": "生活費留多少" });
    const finish = async (r, text) => {
      app.onboardDone = true;
      await app.afterWrite(r, text);
      app.toast("好了！到「明細」最上面填金額、按「記」，就記下第一筆。");
    };
    const save = h("button", { type: "button", class: "btn primary", onclick: async () => {
      const inc = minorOrNull(income.value);
      const liv = minorOrNull(living.value);
      if (Number.isNaN(inc) || Number.isNaN(liv)) { app.toast(`金額要是數字（最多 ${state.decimals} 位小數）`); return; }
      if (inc === null && liv === null) { await finish({}, ""); return; }
      save.disabled = true;
      try {
        const body = {};
        if (inc !== null) body.income_expected = inc;
        if (liv !== null) body.buckets = { living: liv };
        const r = await api("/api/plan", { method: "PUT", body });
        await finish(r, "記下來了");
      } catch (e) { save.disabled = false; fail(e); }
    } }, "完成");
    return [...head(steps, "每期大概收入多少、生活費留多少"),
      h("p", { class: "muted" }, "填了，總覽最上面的大數字就是「還可以花多少」。可以跳過，之後在「規劃」填。"),
      h("label", { class: "field" }, "每期大概收入多少", income),
      h("label", { class: "field" }, "生活費留多少", living),
      h("div", { class: "actions" },
        h("button", { type: "button", class: "btn", onclick: () => finish({}, "") }, "跳過"), save)];
  };

  const show = (step) => { fill(box, step()); };
  show(step1);
  return box;
}
