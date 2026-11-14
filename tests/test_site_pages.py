"""docs/site/ 的每一頁（線上 Artifact 的本機副本）頁尾都有線上網址，而且頁面版本對得上內容。
改了頁面：python scripts/site_stamp.py <檔>，再重新發佈同一個網址。"""
import hashlib
import os
import re

import pytest

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "site")
STAMP = re.compile(r"(<span data-stamp>)(.*?)(</span>)", re.S)
PAGES = sorted(n for n in os.listdir(SITE) if n.endswith(".html")) if os.path.isdir(SITE) else []


@pytest.mark.parametrize("name", PAGES)
def test_page_has_online_url_and_current_stamp(name):
    html = open(os.path.join(SITE, name), encoding="utf-8").read()
    assert "https://claude.ai/artifact/" in html.split("data-stamp")[0][-600:], "頁尾要寫線上網址"
    found = STAMP.findall(html)
    assert len(found) == 1
    m = re.match(r"\s*([0-9a-f]{10}) · \d{4}-\d{2}-\d{2}\s*$", found[0][1])
    assert m, found[0][1]
    assert m.group(1) == hashlib.sha256(STAMP.sub(r"\1\3", html).encode("utf-8")).hexdigest()[:10], "內容改了但頁面版本沒更新"
