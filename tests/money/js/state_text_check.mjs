// 總覽（home.js）的每一種狀態都有白話字：格子狀態、燈號、日曆、圖例、第一次用的引導。
// node state_text_check.mjs <home.js 的路徑>　→ 印一行 JSON：[[檢查名, null 或沒過的原因], …]
import { readFileSync } from "node:fs";

import { allText, checker, count, fileUrl, printJson } from "./prelude.mjs";

const HOME = process.argv[2];
const SOURCE = readFileSync(HOME, "utf-8");
const home = await import(fileUrl(HOME));
const { check, out } = checker();
const CJK2 = /[一-鿿][^]*[一-鿿]/;
const BROKEN = /undefined|NaN|null|\[object/;
const INTERNAL = /\b(todo|rolling|skipped|state|level|step:\d|damaged_refs|HTTP|status)\b/i;

// 1. 「錢怎麼分」每一格的每一種狀態都有字
const SAMPLES = {
  "還沒做完 todo": { state: "todo", left: 500, plan: 1000 },
  "生活費還在花 open": { state: "open", left: 3200, plan: 15000 },
  "生活費超過 open<0": { state: "open", left: -300, plan: 15000 },
  "做完了 done": { state: "done", left: 0, near: false },
  "差不多做完 done+near": { state: "done", left: 40, near: true },
  "超過 over": { state: "over", left: -250 },
  "留著等帳單 rolling": { state: "rolling", left: 800 },
  "這期跳過 skipped": { state: "skipped", left: 0 },
};
for (const [name, e] of Object.entries(SAMPLES)) {
  check(`格子狀態有字：${name}`, () => {
    const st = home.envStatus(e);
    if (!st || !st.text) return "沒有字";
    if (!CJK2.test(st.text)) return `只有符號、沒有白話：「${st.text}」`;
    if (BROKEN.test(st.text + st.title)) return `字壞掉了：「${st.text}」「${st.title}」`;
    if (INTERNAL.test(st.text)) return `出現程式內部詞：「${st.text}」`;
    if (!st.title || !CJK2.test(st.title)) return "停上去的說明（title）沒有白話";
    return null;
  });
}
check("沒有計畫也沒用的格子（none）不寫狀態", () => (home.envStatus({ state: "none", left: 0 }) === null ? null : "應該是 null"));
check("超過的格子寫出超過多少", () => (home.envStatus({ state: "over", left: -250 }).text.includes("250") ? null : "沒寫金額"));
check("還沒做完的格子寫出還差多少", () => (home.envStatus({ state: "todo", left: 500, plan: 1000 }).text.includes("500") ? null : "沒寫金額"));

// 2. 燈號、日曆：顏色之外都有字
for (const lv of ["good", "warn", "bad"]) {
  check(`燈號 ${lv} 有字`, () => (CJK2.test(home.LIGHT_WORD[lv] || "") ? null : `LIGHT_WORD.${lv}＝${home.LIGHT_WORD[lv]}`));
}
for (const lv of ["save", "warn", "crit"]) {
  check(`日曆 ${lv} 有一個字`, () => (/[一-鿿]/.test(home.DAY_MARK[lv] || "") ? null : `DAY_MARK.${lv}＝${home.DAY_MARK[lv]}`));
}

// 3. 圖例：畫面上用到的每個符號都有一行白話
const rows = home.LEGEND.flatMap(([, list]) => list);
const legendText = rows.map(([mark, text]) => `${mark} ${text}`).join("\n");
check("圖例每一行都有標示和白話", () => {
  const bad = rows.find(([mark, text]) => !mark || !CJK2.test(text || ""));
  return bad ? `這一行不完整：${JSON.stringify(bad)}` : null;
});
const SYMBOLS = ["✓", "≈", "↻", "⏭", "⇄", "🔒", "？", "📦", "ⓘ", "已撥", "挪入", "待解決", "省", "超", "爆", "早", "午", "晚",
  "已入帳", "還沒入帳", "繳好了", "估的", "處理報帳"];
for (const sym of SYMBOLS) {
  if (!SOURCE.includes(sym)) continue;
  check(`圖例解釋了「${sym}」`, () => (legendText.includes(sym) ? null : "總覽有用到，但圖例沒寫它是什麼意思"));
}
for (const [lv, word] of Object.entries({ good: "綠", warn: "黃", bad: "紅" })) {
  check(`圖例講了燈號 ${lv}（${word}）`, () => (legendText.includes(word) ? null : "沒寫"));
}

// 4. 第一次用：三步驟的白話引導
check("第一次用的引導有三步（帳戶 → 記第一筆 → 還可以花多少）", () => {
  const node = home.firstRun({ go: () => {} });
  const text = allText(node);
  if (count(node, "li") !== 3) return `應該三步，畫出 ${count(node, "li")} 步`;
  for (const w of ["帳戶", "記第一筆", "還可以花"]) if (!text.includes(w)) return `少了「${w}」`;
  if (BROKEN.test(text)) return "畫面上有壞掉的字";
  return null;
});
printJson(out);
