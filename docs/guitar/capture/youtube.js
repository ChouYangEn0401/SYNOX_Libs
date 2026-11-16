/* 🎸 SYNOX 吉他：從 YouTube 影片頁抓「字幕（歌詞）時間軸」→ 下載 lyrics.json
 *
 * 用法：在 youtube.com/watch?v=… 頁面點書籤。會依序試：
 *   1. 播放器自己的字幕清單（人工字幕優先，其次自動字幕），用同一個網站的 timedtext（json3）取每一句的開始、長度
 *   2. 拿不到時：先在影片下方按「…更多 → 顯示轉錄稿」，再點一次書籤，改讀轉錄稿面板（只有開始時間，結束＝下一句開始）
 *   3. 都沒有：很多「歌詞 MV」的歌詞是燒在畫面上的，不是字幕——這時只下載影片資訊（長度、標題），歌詞要另外給
 * 只讀你正在看的頁面與它自己的字幕檔，不上傳任何東西。
 * ⚠ 尚未在真的 YouTube 上測過；YouTube 改版時最常壞的是第 1 步（timedtext 可能要額外參數而回空的），第 2 步是備案。
 */
(function () {
  "use strict";
  var PREFER = ["zh-Hant", "zh-TW", "zh-HK", "zh", "zh-Hans", "zh-CN", "ko", "ja", "en"];

  function playerResponse() {
    var p = document.querySelector("#movie_player");
    try { if (p && p.getPlayerResponse) { return p.getPlayerResponse(); } } catch (e) { /* 用下一個 */ }
    return window.ytInitialPlayerResponse || null;
  }

  function pickTrack(tracks) {
    function score(t) {
      var lang = t.languageCode || "", i = PREFER.indexOf(lang);
      if (i < 0) { i = PREFER.indexOf(lang.split("-")[0]); }
      return (t.kind === "asr" ? 100 : 0) + (i < 0 ? 50 : i);
    }
    return tracks.slice().sort(function (a, b) { return score(a) - score(b); })[0] || null;
  }

  function fromJson3(data) {
    return (data.events || []).filter(function (e) { return e.segs; }).map(function (e) {
      return { start: e.tStartMs, end: e.tStartMs + (e.dDurationMs || 0),
               text: e.segs.map(function (s) { return s.utf8 || ""; }).join("").replace(/\s+/g, " ").trim() };
    }).filter(function (l) { return l.text; });
  }

  function fromTranscriptPanel() {
    var rows = document.querySelectorAll("ytd-transcript-segment-renderer");
    var out = Array.prototype.map.call(rows, function (r) {
      var ts = (r.querySelector(".segment-timestamp") || {}).textContent || "";
      var parts = ts.trim().split(":").map(Number), ms = 0;
      parts.forEach(function (n) { ms = ms * 60 + n; });
      return { start: ms * 1000, end: null, text: ((r.querySelector(".segment-text") || {}).textContent || "").trim() };
    }).filter(function (l) { return l.text && isFinite(l.start); });
    out.forEach(function (l, i) { l.end = out[i + 1] ? out[i + 1].start : null; });
    return out;
  }

  function download(obj) {
    var name = (obj.metadata.title || "video").replace(/[\\/:*?"<>|]+/g, " ").slice(0, 60).trim();
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" }));
    a.download = "youtube-" + name + ".json";
    a.style.display = "none";
    document.body.appendChild(a); a.click();
    setTimeout(function () { a.remove(); URL.revokeObjectURL(a.href); }, 2000);
  }

  var pr = playerResponse(), vd = (pr && pr.videoDetails) || {};
  var tracks = (((pr || {}).captions || {}).playerCaptionsTracklistRenderer || {}).captionTracks || [];
  var track = pickTrack(tracks);
  var result = {
    schema: "synox.guitar.lyrics/1",
    metadata: { youtube_id: vd.videoId || new URLSearchParams(location.search).get("v"), title: vd.title || document.title,
                channel: vd.author || null, duration_ms: vd.lengthSeconds ? +vd.lengthSeconds * 1000 : null,
                page_url: location.href, source: "YouTube" },
    lyrics_source: null,
    lyrics: [],
    captured_at: new Date().toISOString()
  };

  function finish(note) {
    if (note) { result.note = note; }
    download(result);
    alert("🎸 SYNOX：" + (result.lyrics.length ? "抓到 " + result.lyrics.length + " 句（" + result.lyrics_source + "）" : "沒有抓到字幕。")
          + (note ? "\n" + note : "") + "\n已下載 JSON。");
  }

  var panel = fromTranscriptPanel();
  if (panel.length) { result.lyrics = panel; result.lyrics_source = "transcript-panel"; finish(); return; }
  if (!track) { finish("這支影片沒有字幕軌：歌詞可能是燒在畫面上的。可以只用影片資訊，歌詞另外貼。"); return; }
  var url = track.baseUrl + (track.baseUrl.indexOf("?") >= 0 ? "&" : "?") + "fmt=json3";
  fetch(url, { credentials: "same-origin" })
    .then(function (r) { return r.text(); })
    .then(function (t) {
      if (!t) { throw new Error("empty"); }
      result.lyrics = fromJson3(JSON.parse(t));
      result.lyrics_source = "captions:" + track.languageCode + (track.kind === "asr" ? "（自動字幕）" : "");
      finish(track.kind === "asr" ? "這是自動產生的字幕，歌詞可能有錯字。" : null);
    })
    .catch(function () {
      finish("字幕檔讀不到（YouTube 可能擋了）。請按影片下方「…更多 → 顯示轉錄稿」，再點一次這個書籤。");
    });
})();
