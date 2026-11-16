// 記帳畫面的 node 檢查共用的開頭：最小的假 DOM（只夠 dom.js 的 h()／s()／fill() 用）。
// 不開瀏覽器、不開埠、不連網；畫面的檔用 file:// 網址 import（由 Python 測試把路徑傳進來）。
// 用法：import { allText, count, fileUrl, printJson } from "./prelude.mjs";（import 這個檔就會裝好 document／Node）
import { pathToFileURL } from "node:url";

class MNode {}
export class El extends MNode {
  constructor(tag) {
    super(); this.tag = tag; this.children = []; this.attrs = {}; this.style = {}; this.dataset = {}; this._text = "";
    this.classList = { toggle() {}, add() {}, remove() {}, contains: () => false };
  }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener() {}
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = kids; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text; }
}
export class Txt extends MNode { constructor(t) { super(); this.text = String(t); } }

globalThis.Node = MNode;
globalThis.document = { createElement: (t) => new El(t), createElementNS: (ns, t) => new El(t),
  createTextNode: (t) => new Txt(t), querySelector: () => null };

export const allText = (n) => (n instanceof Txt ? n.text : n instanceof El
  ? n._text + Object.values(n.attrs).filter((v) => typeof v === "string").join(" ") + n.children.map(allText).join("") : "");
export const count = (n, tag) => (n instanceof El ? (n.tag === tag ? 1 : 0) + n.children.reduce((s, c) => s + count(c, tag), 0) : 0);
export const fileUrl = (path) => pathToFileURL(path).href;
export const printJson = (value) => console.log(JSON.stringify(value));

// 一組檢查：check(名字, fn)；fn 回 null／undefined＝過，回一句話＝沒過（丟錯也算沒過）。最後 results() 印出來。
export function checker() {
  const out = [];
  const check = (label, fn) => {
    let why;
    try { why = fn(); } catch (e) { why = String((e && e.message) || e); }
    out.push([label, why || null]);
  };
  return { check, out };
}
