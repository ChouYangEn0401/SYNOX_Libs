"""docs/site/ 的每一頁（線上 Artifact 的本機副本）頁尾都有線上網址，而且頁面版本對得上內容（docs/ARTIFACTS.md）。
改了頁面：python scripts/site_stamp.py <檔>，再重新發佈同一個網址。
例外只有一種：scripts/build_site.py 幫新工具產生、還沒第一次發佈的頁（data-online=""，頁尾寫「還沒發佈」）。"""
import hashlib
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "docs", "site")
STAMP = re.compile(r"(<span data-stamp>)(.*?)(</span>)", re.S)
ONLINE = re.compile(r'data-online="([^"]*)"')
PAGES = sorted(n for n in os.listdir(SITE) if n.endswith(".html")) if os.path.isdir(SITE) else []
GENERATED = "generated: scripts/build_site.py"


def _registry():
    with open(os.path.join(ROOT, "docs", "artifacts.json"), encoding="utf-8") as f:
        return {p["local"]: p for p in json.load(f)["pages"]}


@pytest.mark.parametrize("name", PAGES)
def test_page_has_online_url_and_current_stamp(name):
    html = open(os.path.join(SITE, name), encoding="utf-8").read()
    pending = ONLINE.search(html) and ONLINE.search(html).group(1) == "" and GENERATED in html
    if not pending:
        assert "https://claude.ai/artifact/" in html.split("data-stamp")[0][-600:], "頁尾要寫線上網址"
    found = STAMP.findall(html)
    assert len(found) == 1
    m = re.match(r"\s*([0-9a-f]{10}) · \d{4}-\d{2}-\d{2}\s*$", found[0][1])
    assert m, found[0][1]
    assert m.group(1) == hashlib.sha256(STAMP.sub(r"\1\3", html).encode("utf-8")).hexdigest()[:10], "內容改了但頁面版本沒更新"


@pytest.mark.parametrize("name", PAGES)
def test_footer_url_matches_the_artifact_registry(name):
    """頁尾的線上網址＝docs/artifacts.json 寫的；沒登記的只能是產生的、還沒發佈的頁。"""
    html = open(os.path.join(SITE, name), encoding="utf-8").read()
    online = ONLINE.search(html)
    assert online, "頁尾要有 data-online"
    entry = _registry().get(f"docs/site/{name}")
    if entry is None:
        assert online.group(1) == "" and GENERATED in html, "這一頁不在 docs/artifacts.json：發佈後把網址登記進去"
    else:
        assert online.group(1) == entry["url"]


def test_registry_points_at_real_files_and_unique_urls():
    reg = _registry()
    for local, entry in reg.items():
        assert os.path.isfile(os.path.join(ROOT, local)), local
        assert re.match(r"^https://claude\.ai/artifact/[A-Za-z0-9]+$", entry["url"]), entry["url"]
    urls = [e["url"] for e in reg.values()]
    assert len(urls) == len(set(urls))
