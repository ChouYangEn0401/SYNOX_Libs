"""在 docs/site/*.html 最下面蓋「頁面版本」，給開發者比對線上版（Artifact）和本機版是不是同一份。

    python scripts/site_stamp.py            全部頁面
    python scripts/site_stamp.py notes.html 只蓋這幾頁

頁面版本＝把 <span data-stamp>…</span> 裡的字清空後，整頁的 sha256 前 10 碼＋今天日期。
線上版和本機版的頁面版本一樣＝內容一樣。只用標準函式庫，不連網。
"""
from __future__ import annotations

import datetime
import hashlib
import os
import re
import sys

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "site")
STAMP = re.compile(r"(<span data-stamp>)(.*?)(</span>)", re.S)


def stamp(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        html = f.read()
    if len(STAMP.findall(html)) != 1:
        raise SystemExit(f"{os.path.basename(path)}：要剛好有一個 <span data-stamp>…</span>")
    blank = STAMP.sub(r"\1\3", html)
    digest = hashlib.sha256(blank.encode("utf-8")).hexdigest()[:10]
    out = STAMP.sub(lambda m: f"{m.group(1)}{digest} · {datetime.date.today().isoformat()}{m.group(3)}", html)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    return digest


def main(argv: list[str]) -> int:
    names = argv or sorted(n for n in os.listdir(SITE) if n.endswith(".html"))
    for name in names:
        print(f"{name}: {stamp(os.path.join(SITE, name))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
