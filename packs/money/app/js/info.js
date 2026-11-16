// ⓘ：一句白話說這張卡在做什麼（看不懂的地方都要有一個可以點的說明）。
// 滑鼠停上去會看到標題；點下去打開說明（手機沒有「停上去」，所以一定要能點）。
import { h } from "./dom.js";

export function infoButton(app, title, lines) {
  return h("button", {
    type: "button", class: "info", title: `${title}是什麼？`, "aria-label": `${title}是什麼？`,
    onclick: () => app.openModal({ title: `${title}是什麼？`, body: lines.map((t) => h("p", null, t)) }),
  }, "ⓘ");
}
