// dom.js 的金額換算：num（最小單位 → 顯示）、toMinor（人打的字 → 最小單位）、major（最小單位 → 放回輸入框）。
// 小數位數 0 跟以前完全一樣；小數位數 2 時 12345 顯示「123.45」、打「99.5」送 9950。
// node money_format_check.mjs <dom.js 的路徑>　→ 印一行 JSON：[[檢查名, null 或沒過的原因], …]
import { checker, fileUrl, printJson } from "./prelude.mjs";

const dom = await import(fileUrl(process.argv[2]));
const { check, out } = checker();
const same = (got, want) => (Object.is(got, want) ? null : `得到 ${JSON.stringify(got)}，應該是 ${JSON.stringify(want)}`);

dom.setDecimals(0);
check("0 位：num 跟以前一樣（千分位、四捨五入）", () => same(dom.num(1234567), "1,234,567") || same(dom.num(12.6), "13")
  || same(dom.num(null), "0") || same(dom.num(-500), "-500"));
check("0 位：toMinor 收整數、千分位、「元」", () => same(dom.toMinor("1,200元"), 1200) || same(dom.toMinor(" 95 "), 95));
check("0 位：toMinor 不收小數與亂打", () => (Number.isNaN(dom.toMinor("99.5")) && Number.isNaN(dom.toMinor("abc"))
  && Number.isNaN(dom.toMinor("")) ? null : "應該是 NaN"));
check("0 位：toMinor 收「99.0」（小數都是 0）", () => same(dom.toMinor("99.0"), 99));
check("0 位：major 原樣", () => same(dom.major(1200), "1200") || same(dom.major(null), "") || same(dom.major(""), ""));
check("count 不換算", () => same(dom.count(1234), "1,234"));

dom.setDecimals(2);
check("2 位：decimals()", () => same(dom.decimals(), 2));
check("2 位：num 顯示兩位小數", () => same(dom.num(12345), "123.45") || same(dom.num(9950), "99.50")
  || same(dom.num(120000), "1,200.00") || same(dom.num(-5), "-0.05"));
check("2 位：toMinor 換成最小單位", () => same(dom.toMinor("99.5"), 9950) || same(dom.toMinor("120"), 12000)
  || same(dom.toMinor("1,234.56"), 123456) || same(dom.toMinor("0.07"), 7) || same(dom.toMinor("-3.5"), -350));
check("2 位：toMinor 小數太多是 NaN", () => (Number.isNaN(dom.toMinor("1.234")) ? null : "應該是 NaN"));
check("2 位：major 放回輸入框", () => same(dom.major(9950), "99.5") || same(dom.major(12000), "120")
  || same(dom.major(7), "0.07") || same(dom.major(-350), "-3.5"));
check("2 位：count 還是不換算", () => same(dom.count(1234), "1,234"));
check("2 位：來回一樣", () => {
  for (const n of [1, 7, 10, 99, 100, 9950, 123456, 100000000]) {
    if (dom.toMinor(dom.major(n)) !== n) return `${n} → ${dom.major(n)} → ${dom.toMinor(dom.major(n))}`;
  }
  return null;
});

dom.setDecimals("壞掉的值");
check("壞掉的小數位數當 0", () => same(dom.decimals(), 0) || same(dom.num(1234), "1,234"));
printJson(out);
