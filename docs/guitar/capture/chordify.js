/* 🎸 SYNOX 吉他：從 Chordify 歌曲頁抓「和弦時間軸」→ 下載 chords.json
 *
 * 用法：在 Chordify 的歌曲頁點書籤（安裝見 install.html）。右下角出現小面板：
 *   1. 按「開始校正」，然後在頁面上按播放，讓歌放 15 秒以上（不要拖進度條、播放速度 1x）
 *   2. 按「停止並下載」→ 得到 chordify-<歌名>.json
 *   不想校正也可以直接按「直接下載」：時間用 bpm 推算（每個格子＝一拍），會標 uncalibrated。
 *
 * 讀的東西（都只讀你自己正在看的頁面，不打任何 API、不上傳）：
 *   - 每個 [data-chord-i] 格子：格子編號（index）＋和弦（從和弦圖的網址解，比畫面上的 Eₘ⁷ 可靠）
 *   - 正在亮的格子（--diagram-opacity: 1）、進度條 input[type=range] 的百分比
 *   - 標題（h1）、Song Details（Key、bpm、Title…）、transpose 按鈕上的調
 * 校正怎麼算：播放時每 200ms 記一次（時間, 百分比, 亮的格子）。
 *   歌的長度＝經過的毫秒 ÷ 進度條走了幾 %；
 *   每換一個格子記下「歌曲時間」，用最小平方法算出「第 0 格在幾毫秒、每格幾毫秒」，再跟 bpm 比（1 格＝1 拍或半拍）。
 */
(function () {
  "use strict";
  if (window.__synoxChordify) { window.__synoxChordify.show(); return; }

  var QUALITY = { maj: "", min: "m", "7": "7", maj7: "maj7", min7: "m7", sus2: "sus2", sus4: "sus4", dim: "dim",
                  aug: "aug", dim7: "dim7", hdim7: "m7b5", min6: "m6", maj6: "6", "6": "6", add9: "add9", "9": "9",
                  min9: "m9", maj9: "maj9", minmaj7: "mM7", "5": "5", "1": "5" };

  function text(el) { return el ? el.textContent.replace(/\s+/g, " ").trim() : ""; }

  // "/api/v2/diagrams/instruments/guitar/chords/A%3Amaj%252FG?…" → { root:"A", quality:"maj", bass:"G", name:"A/G" }
  function chordFromHref(href) {
    var m = /\/chords\/([^?#]+)/.exec(href || "");
    if (!m) { return null; }
    var raw = m[1];
    for (var i = 0; i < 3 && /%[0-9A-Fa-f]{2}/.test(raw); i++) { raw = decodeURIComponent(raw); }
    var parts = raw.split(":"), root = parts[0], rest = parts[1] || "maj";
    var slash = rest.split("/"), q = slash[0], bass = slash[1] || null;
    var suffix = QUALITY.hasOwnProperty(q) ? QUALITY[q] : q;
    if (root === "N" || root === "") { return { raw: raw, name: "N.C.", root: null, quality: null, bass: null }; }
    return { raw: raw, name: root + suffix + (bass ? "/" + bass : ""), root: root, quality: q, bass: bass };
  }

  function readCells() {
    return Array.prototype.map.call(document.querySelectorAll("[data-chord-i]"), function (el) {
      var use = el.querySelector("use"), c = chordFromHref(use && (use.getAttribute("href") || use.getAttribute("xlink:href")));
      var label = text(el.querySelector("[id^='_r_'], .ct1wuqa"));
      return { index: +el.getAttribute("data-chord-i"), chord: c ? c.name : label, raw: c ? c.raw : null, label: label,
               active: (el.style.getPropertyValue("--diagram-opacity") || "").trim() === "1" };
    }).sort(function (a, b) { return a.index - b.index; });
  }

  function readMeta() {
    var details = {};
    document.querySelectorAll("dl dt").forEach(function (dt) {
      var dd = dt.nextElementSibling;
      if (dd && dd.tagName === "DD") { details[text(dt).toLowerCase()] = text(dd); }
    });
    var h1 = document.querySelector("h1"), spans = h1 ? h1.querySelectorAll(":scope > span") : [];
    var byLink = h1 ? h1.querySelector("a") : null;
    var bpm = parseFloat(details.bpm);
    return {
      title: details.title || text(spans[0]) || document.title,
      artist: null,                                    // Chordify 的 h1 常常是「頻道 by 影片標題」，不可靠：留給人或 AI 填
      h1: { first: text(spans[0]), by: text(byLink) },
      key: details.key || text(document.querySelector("#toolbar-transpose .ct1wuqa")) || null,
      bpm: isFinite(bpm) ? bpm : null,
      transcription_by: details["transcription by"] || null,
      last_modified: details["last modified"] || null,
      chords_summary: details.chords || null,
      page_url: location.href,
      source: "Chordify"
    };
  }

  function sliderPct() {
    var r = document.querySelector("input[type=range]");
    var v = r ? parseFloat(r.value) : NaN;
    return isFinite(v) ? v : null;
  }

  // 最小平方法 y = a + b x
  function fit(points) {
    var n = points.length;
    if (n < 2) { return null; }
    var sx = 0, sy = 0, sxx = 0, sxy = 0;
    points.forEach(function (p) { sx += p[0]; sy += p[1]; sxx += p[0] * p[0]; sxy += p[0] * p[1]; });
    var d = n * sxx - sx * sx;
    if (!d) { return null; }
    var b = (n * sxy - sx * sy) / d;
    return { a: (sy - b * sx) / n, b: b };
  }

  function calibrate(samples, bpm) {
    var moving = samples.filter(function (s, i) { return i === 0 || s.pct !== samples[i - 1].pct; });
    var f = fit(moving.map(function (s) { return [s.pct, s.t]; }));              // 牆上時間 vs 百分比
    if (!f || f.b <= 0) { return null; }
    var duration = f.b * 100;                                                   // 每 1% 幾毫秒 × 100
    var changes = [];
    samples.forEach(function (s, i) {
      if (i && s.active !== null && s.active !== samples[i - 1].active && s.pct !== null) {
        changes.push([s.active, s.pct / 100 * duration]);
      }
    });
    var g = fit(changes);
    var beat = bpm ? 60000 / bpm : null;
    var res = { duration_ms: Math.round(duration), samples: samples.length, changes: changes.length };
    if (g && g.b > 0) {
      res.ms_per_index = +g.b.toFixed(2);
      res.offset_ms = Math.round(g.a);
      if (beat) {
        var ratio = g.b / beat;
        res.index_unit = Math.abs(ratio - 1) < 0.15 ? "beat" : Math.abs(ratio - 0.5) < 0.08 ? "half-beat"
          : Math.abs(ratio - 2) < 0.3 ? "two-beats" : "unknown";
        res.beats_per_index = +ratio.toFixed(3);
      }
    }
    return res;
  }

  function build(cal) {
    var meta = readMeta(), cells = readCells(), beat = meta.bpm ? 60000 / meta.bpm : 500;
    var msPer = cal && cal.ms_per_index ? cal.ms_per_index : beat;
    var offset = cal && cal.offset_ms !== undefined ? cal.offset_ms : 0;
    var duration = cal ? cal.duration_ms : null;
    var chords = cells.map(function (c, i) {
      var start = Math.round(offset + c.index * msPer);
      var next = cells[i + 1];
      var end = next ? Math.round(offset + next.index * msPer) : (duration || Math.round(start + 4 * beat));
      return { index: c.index, chord: c.chord, start: start, end: end, raw: c.raw };
    });
    return {
      schema: "synox.guitar.chords/1",
      metadata: meta,
      timeline: {
        unit: "milliseconds",
        duration: duration,
        beat_ms: meta.bpm ? +beat.toFixed(2) : null,
        ms_per_index: +msPer.toFixed(2),
        offset_ms: offset,
        calibration: cal || { status: "uncalibrated", note: "沒有校正：假設每個格子＝一拍、第 0 格在 0 毫秒" }
      },
      chords: chords,
      captured_at: new Date().toISOString()
    };
  }

  function download(obj) {
    var name = (obj.metadata.title || "song").replace(/[\\/:*?"<>|]+/g, " ").slice(0, 60).trim();
    var blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "chordify-" + name + ".json";
    a.style.display = "none";
    document.body.appendChild(a); a.click();
    setTimeout(function () { a.remove(); URL.revokeObjectURL(a.href); }, 2000);
  }

  // ── 小面板 ──
  var box = document.createElement("div");
  box.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;background:#fff;color:#222;" +
    "border:1px solid #bbb;border-radius:10px;padding:10px 12px;font:14px/1.5 system-ui,sans-serif;" +
    "box-shadow:0 4px 16px rgba(0,0,0,.2);max-width:300px";
  var title = document.createElement("strong"); title.textContent = "🎸 SYNOX：抓和弦時間軸";
  var status = document.createElement("div"); status.style.margin = "6px 0";
  var row = document.createElement("div"); row.style.cssText = "display:flex;gap:6px;flex-wrap:wrap";
  function btn(label, fn) {
    var b = document.createElement("button"); b.textContent = label;
    b.style.cssText = "padding:4px 10px;border:1px solid #999;border-radius:6px;background:#f6f6f6;cursor:pointer";
    b.addEventListener("click", fn); row.appendChild(b); return b;
  }
  var samples = [], timer = null;
  function activeIndex() { var a = readCells().filter(function (c) { return c.active; })[0]; return a ? a.index : null; }
  function refresh() {
    var cells = readCells();
    status.textContent = "找到 " + cells.length + " 個和弦格子" + (timer ? "；校正中：已記 " + samples.length + " 筆、換格 " +
      samples.filter(function (s, i) { return i && s.active !== samples[i - 1].active; }).length + " 次" : "");
  }
  var startBtn = btn("開始校正", function () {
    samples = [];
    timer = setInterval(function () {
      samples.push({ t: performance.now(), pct: sliderPct(), active: activeIndex() });
      refresh();
    }, 200);
    startBtn.disabled = true;
    status.textContent = "請在頁面按播放，讓歌放 15 秒以上…";
  });
  btn("停止並下載", function () {
    if (timer) { clearInterval(timer); timer = null; }
    var cal = calibrate(samples, readMeta().bpm);
    if (!cal) { status.textContent = "沒有記到進度條在動（有按播放嗎？）。可以按「直接下載」用 bpm 推算。"; startBtn.disabled = false; return; }
    download(build(cal));
    status.textContent = "已下載。歌長約 " + Math.round(cal.duration_ms / 1000) + " 秒" +
      (cal.index_unit ? "，1 格＝" + ({ beat: "1 拍", "half-beat": "半拍", "two-beats": "2 拍" }[cal.index_unit] || "？") : "");
    startBtn.disabled = false;
  });
  btn("直接下載", function () { download(build(null)); });
  btn("關閉", function () { if (timer) { clearInterval(timer); } box.remove(); delete window.__synoxChordify; });
  box.appendChild(title); box.appendChild(status); box.appendChild(row);
  document.body.appendChild(box);
  refresh();
  window.__synoxChordify = { show: function () { box.style.display = ""; }, build: build, calibrate: calibrate,
                             chordFromHref: chordFromHref, readCells: readCells, readMeta: readMeta,
                             samples: function () { return samples; } };
})();
