// 用真的讀回來的資料跑一次總覽（home.js）：月與年兩個模式，印出畫面上的字與讀了哪些東西。
// node render_home.mjs <home.js 的路徑> <資料 JSON 檔：{"home": …, "year": …}>
import { readFileSync } from "node:fs";

import { allText, fileUrl, printJson } from "./prelude.mjs";

const [HOME, DATA] = process.argv.slice(2);
const PAYLOADS = JSON.parse(readFileSync(DATA, "utf-8"));
const calls = [];
globalThis.SynoxApp = { read: async (name) => { calls.push(name); return PAYLOADS[name]; },
  act: async () => ({}), download: async () => true };
const home = await import(fileUrl(HOME));
const app = { period: null, homeMode: "month", year: null, setNav() {}, setYearNav() {}, go() {}, toast() {},
  openModal() {}, closeModal() {}, render() {}, afterWrite: async () => {}, ref: async () => ({ accounts: [] }) };
const text = allText(await home.render(app));
app.homeMode = "year";
const yearText = allText(await home.render(app));
printJson({ text, yearText, calls, onboardDone: Boolean(app.onboardDone) });
