# -*- coding: utf-8 -*-
"""擁有者的規矩：每個公開頁（docs/site/*.html、衣櫃頁、README）的下載連結永遠指到最新版（docs/RELEASING.md）。

- 頁面上的版本、檔名、tag 都要等於 pack.json／VERSION 現在的值（scripts/links.py 的 render 改完不能再有差）；
- 頁面連到的每個發佈檔，scripts/build.py 都真的會產生；build 產生的檔＝links.expected_assets()；
- 從 main 打包出的 index.json 有每個工具（pack.json 的版本）＋26 套外觀（kind=theme）；tools-<VERSION>.zip 不過期。
"""
import hashlib
import json
import os
import re
import zipfile

import pytest

import build as build_script
import build_costumes as bc
import links
from conftest import PACK_NAMES, ROOT


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("dist"))
    result = build_script.build(out_dir=out)
    assert result["problems"] == [], result["problems"]
    return out, result["index"]


def test_link_mode_is_known():
    assert links.LINK_MODE in links.MODES


def test_every_public_page_links_the_current_versions():
    assert links.check() == []


def test_checker_catches_stale_links():
    versions = links.current_versions()
    tag = links.release_tag()
    stale = (f'<a href="{links.REPO_URL}/releases/download/{tag}/money-0.0.1.synoxpack">x</a>'
             f'<a href="{links.REPO_URL}/releases/download/v0.0.1/notes-{versions["notes"]}.synoxpack">x</a>'
             f'<a href="{links.REPO_URL}/releases/download/{tag}/nothing-1.0.0.synoxpack">x</a>'
             f'<a href="{links.REPO_URL}/raw/main/docs/site/packs/costume-a-bubble-1.0.0.synoxpack">x</a>'
             f'<code>tools-0.0.1.zip</code><code>money-0.0.1.synoxpack</code>')
    problems = "\n".join(links.problems_in("x", stale, mode="tag"))
    assert "money 0.0.1" in problems
    assert "v0.0.1" in problems
    assert "nothing-1.0.0.synoxpack" in problems
    assert "repo 分支" in problems
    assert "tools-0.0.1.zip" in problems
    assert "沒更新" in problems


def test_render_round_trips_between_modes():
    for path in links.public_pages():
        with open(path, encoding="utf-8") as f:
            page = f.read()
        latest = links.render(page, mode="latest")
        assert links.problems_in(path, latest, mode="latest") == []
        assert links.render(latest, mode=links.LINK_MODE) == page


def test_pages_link_only_assets_the_build_produces(dist):
    out, _ = dist
    produced = set(os.listdir(out))
    assert produced == links.expected_assets()
    for path in links.public_pages():
        with open(path, encoding="utf-8") as f:
            for m in links.ANY_ASSET_URL.finditer(f.read()):
                assert m.group(2) in produced, (path, m.group(0))


def test_index_json_has_every_pack_at_its_current_version_and_26_costumes(dist):
    out, index = dist
    with open(os.path.join(out, "index.json"), encoding="utf-8") as f:
        assert json.load(f) == index
    versions = links.current_versions()
    by_name = {e["name"]: e for e in index["packs"]}
    for name in PACK_NAMES:
        assert by_name[name]["version"] == versions[name]
        assert by_name[name]["file"] == links.pack_file(name, versions[name])
    themes = [e for e in index["packs"] if e.get("kind") == "theme"]
    assert len(themes) == 26 == len(bc.costume_folders())
    for e in themes:
        assert e["version"] == versions[e["name"]]
    assert set(by_name) == set(versions)
    assert index["library_version"] == links.library_version()


def test_aliases_tools_zip_and_sha256sums(dist):
    out, index = dist
    for e in index["packs"]:
        assert _sha(os.path.join(out, links.alias_file(e["name"]))) == e["sha256"] == _sha(os.path.join(out, e["file"]))
    zip_name = links.tools_zip()
    assert _sha(os.path.join(out, zip_name)) == _sha(os.path.join(out, links.TOOLS_ALIAS))
    with zipfile.ZipFile(os.path.join(out, zip_name)) as z:
        names = set(z.namelist())
        inner = json.loads(z.read("tools/index.json"))
    tools = [e for e in index["packs"] if e.get("kind") != "theme"]
    assert names == {"tools/" + e["file"] for e in tools} | {"tools/index.json"}
    assert {e["name"]: e["version"] for e in inner["packs"]} == {n: links.current_versions()[n] for n in PACK_NAMES}
    sums = {}
    with open(os.path.join(out, links.SUMS_FILE), encoding="utf-8") as f:
        for line in f:
            digest, name = re.match(r"([0-9a-f]{64})  (\S+)$", line.rstrip("\n")).groups()
            sums[name] = digest
    assert set(sums) == links.expected_assets() - {links.SUMS_FILE}
    for name, digest in sums.items():
        assert _sha(os.path.join(out, name)) == digest, name


def test_wardrobe_page_is_what_the_generator_makes_now():
    blank = re.compile(r"(<span data-stamp>)(.*?)(</span>)", re.S)
    with open(os.path.join(ROOT, "docs", "site", "wardrobe.html"), encoding="utf-8") as f:
        page = f.read()
    assert blank.sub(r"\1\3", bc.page_html()) == blank.sub(r"\1\3", page), "跑 python scripts/build_costumes.py page"
