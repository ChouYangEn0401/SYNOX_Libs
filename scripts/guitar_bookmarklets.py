"""產生 docs/guitar/capture/install.html：把 chordify.js、youtube.js 變成可以拖到書籤列的 javascript: 連結。

    python scripts/guitar_bookmarklets.py          # 重新產生
    python scripts/guitar_bookmarklets.py --check  # 只檢查是不是最新的
只用標準函式庫。書籤的內容＝原始檔拿掉開頭的說明註解、百分比編碼（換行保留成 %0A，所以 // 註解也安全）。
"""

import html
import os
import re
import sys
from urllib.parse import quote

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "guitar", "capture")
TOOLS = (("chordify.js", "🎸 Chordify→和弦", "在 Chordify 歌曲頁點：抓和弦格子、校正時間（按播放 15 秒）、下載 JSON"),
         ("youtube.js", "🎸 YouTube→歌詞", "在 YouTube 影片頁點：抓字幕（或轉錄稿）的每一句時間、下載 JSON"))


def bookmarklet(source: str) -> str:
    body = re.sub(r"^/\*.*?\*/\s*", "", source, count=1, flags=re.S)
    return "javascript:" + quote(body, safe="(){}[];,=+-*!?'.:_$&|<>/\"")


def render() -> str:
    rows = []
    for name, label, what in TOOLS:
        with open(os.path.join(HERE, name), "r", encoding="utf-8") as f:
            href = bookmarklet(f.read())
        rows.append(f'<li><a class="bm" href="{html.escape(href, quote=True)}">{label}</a>'
                    f'<div class="what">{html.escape(what)}</div></li>')
    return f"""<!doctype html>
<!-- generated: scripts/guitar_bookmarklets.py（不要手改；改 chordify.js／youtube.js 之後重跑） -->
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>吉他抓譜書籤</title>
<style>
:root{{--bg:#f7f4ec;--fg:#26221b;--muted:#736b5a;--accent:#8a4a22;--card:#fff;--line:#e3dbc8}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#1b1815;--fg:#f1ebde;--muted:#b7ac96;--accent:#e0925a;--card:#252119;--line:#3b3427}}}}
:root[data-theme="dark"]{{--bg:#1b1815;--fg:#f1ebde;--muted:#b7ac96;--accent:#e0925a;--card:#252119;--line:#3b3427}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"PingFang TC","Microsoft JhengHei",sans-serif}}
main{{max-width:720px;margin:0 auto}} ul{{list-style:none;padding:0}} li{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:10px 0}}
a.bm{{display:inline-block;padding:8px 14px;border-radius:999px;background:var(--accent);color:#fff;text-decoration:none;font-weight:700}}
.what,.muted{{color:var(--muted);font-size:14px}} ol li{{background:none;border:none;padding:2px 0;margin:0}}
</style></head><body><main>
<h1>🎸 吉他抓譜書籤</h1>
<p>把下面兩顆按鈕<strong>拖到瀏覽器的書籤列</strong>（手機：先在電腦加，再同步）。點書籤時只讀你正在看的那一頁，不上傳任何東西。</p>
<ul>{''.join(rows)}</ul>
<h2>流程</h2>
<ol>
<li>Chordify 歌曲頁 → 點「Chordify→和弦」→「開始校正」→ 按播放 15 秒 →「停止並下載」</li>
<li>同一首歌的 YouTube 頁 → 點「YouTube→歌詞」→ 下載（沒有字幕時，先打開「顯示轉錄稿」再點一次）</li>
<li>兩個 JSON 拿到 SYNOX 吉他面板的「匯入」合併成一份歌（設計見 docs/adr/0007）</li>
</ol>
<p class="muted">Chordify 的和弦是電腦聽出來的，常有錯；請只拿來自己練習，不要轉貼。</p>
</main></body></html>
"""


def main(argv):
    out = os.path.join(HERE, "install.html")
    text = render()
    if "--check" in argv:
        with open(out, "r", encoding="utf-8") as f:
            ok = f.read() == text
        print("✅ install.html 是最新的" if ok else "❌ install.html 過期了：python scripts/guitar_bookmarklets.py")
        return 0 if ok else 1
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("產生：docs/guitar/capture/install.html")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
