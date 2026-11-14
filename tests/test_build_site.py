# -*- coding: utf-8 -*-
"""公開入口頁是產生的（scripts/build_site.py，docs/CONTRIBUTING-TOOLS.md）：

- docs/site/index.html 等於產生器現在的輸出；手寫的工具頁不會被動到；
- 加一個新工具資料夾（只有 pack.json，沒寫任何 HTML）→ 入口頁多一張卡片、多一頁簡單說明頁、index.json 也有它，
  下載連結照 scripts/links.py；登記了線上網址之後卡片改連線上頁；工具拿掉，產生的頁也跟著拿掉。
"""
import hashlib
import json
import os
import re
import shutil

import pytest

import build as build_script
import build_site
import links
import site_stamp
from conftest import PACK_NAMES, ROOT

STAMP = re.compile(r"(<span data-stamp>)(.*?)(</span>)", re.S)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _stamp_ok(html):
    found = STAMP.findall(html)
    digest = hashlib.sha256(STAMP.sub(r"\1\3", html).encode("utf-8")).hexdigest()[:10]
    return len(found) == 1 and found[0][1].startswith(digest + " · ")


# ── 這個 repo ─────────────────────────────────────────────────────────

def test_overview_is_what_the_generator_makes_now():
    assert build_site.check() == [], "跑 python scripts/build_site.py"


def test_every_tool_has_a_card_linking_its_online_page():
    page = _read(os.path.join(ROOT, "docs", "site", "index.html"))
    assert build_site.GENERATED in page
    urls = build_site.online_urls()
    versions = links.current_versions()
    for name in PACK_NAMES:
        assert f"{name} · v{versions[name]}" in page, name
        assert f'href="{urls[f"docs/site/{name}.html"]}"' in page, name
    assert f"所有工具（{len(PACK_NAMES)} 個）" in page


def test_hand_written_tool_pages_are_left_alone():
    for name in PACK_NAMES:
        assert build_site.is_hand_written(os.path.join(ROOT, "docs", "site", f"{name}.html")), name
    assert set(build_site.outputs()) == {build_site.INDEX_PAGE}


def test_template_typos_are_caught():
    with pytest.raises(KeyError):
        build_site.fill("<b>{{no_such_thing}}</b>", {"title": "x"})
    assert build_site.fill("{{a}}|{{{a}}}", {"a": "<i>"}) == "&lt;i&gt;|<i>"


def test_list_republish_names_every_page_with_its_url():
    rows = dict(build_site.republish_list())
    assert rows["docs/site/index.html"] == "https://claude.ai/artifact/M8zGBGy9kRtGTjNS24wBVL"
    assert set(rows) == {f"docs/site/{n}" for n in os.listdir(os.path.join(ROOT, "docs", "site")) if n.endswith(".html")}
    assert all(rows.values())


# ── 假的新工具 ────────────────────────────────────────────────────────

FAKE = "fake-tool"


@pytest.fixture
def lib(tmp_path):
    """一份小工具庫：notes（有手寫頁）＋假的新工具 fake-tool（只有 pack.json 等原始檔，沒寫任何 HTML）。"""
    root = tmp_path / "libs"
    (root / "packs").mkdir(parents=True)
    (root / "docs" / "site").mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "VERSION"), root / "VERSION")
    (root / "catalog.json").write_text(json.dumps({"bundles": {"starter": {"title": "入門：每天用得到", "summary": "x",
                                                                        "packs": ["notes", FAKE]}}}), encoding="utf-8")
    shutil.copy(os.path.join(ROOT, "docs", "artifacts.json"), root / "docs" / "artifacts.json")
    shutil.copy(os.path.join(ROOT, "docs", "site-tools.json"), root / "docs" / "site-tools.json")
    shutil.copy(os.path.join(ROOT, "docs", "site", "notes.html"), root / "docs" / "site" / "notes.html")
    shutil.copytree(os.path.join(ROOT, "packs", "notes"), root / "packs" / "notes",
                    ignore=shutil.ignore_patterns("__pycache__"))
    fake = root / "packs" / FAKE
    shutil.copytree(os.path.join(ROOT, "packs", "notes"), fake, ignore=shutil.ignore_patterns("__pycache__"))
    raw = json.loads((fake / "pack.json").read_text(encoding="utf-8"))
    raw.update({"name": FAKE, "title": "🧪 假工具", "summary": "測試用的新工具：產生器要自己把它列出來",
                "version": "0.0.1", "stage": "test", "group": "fake", "access": "owner",
                "help": {"featured": ["add"],
                         "commands": {"add": {"plain": "記一筆", "examples": [{"text": "買牛奶", "note": "記下來"}]}}},
                "site": {"line": "一句話的說明"}})
    (fake / "pack.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(root)


def test_new_pack_appears_in_overview_with_a_generated_page(lib):
    changed = build_site.write(lib)
    assert set(changed) == {"docs/site/index.html", f"docs/site/{FAKE}.html"}      # notes.html 是手寫的，不動
    page = _read(os.path.join(lib, "docs", "site", "index.html"))
    assert f"{FAKE} · v0.0.1" in page and "假工具" in page and "一句話的說明" in page
    assert "<code class=\"t-ex\">/fake 買牛奶</code>" in page                          # 從 help 區塊來的範例
    assert f'href="{build_site.readme_url(FAKE)}"' in page                              # 還沒有線上頁 → README
    assert build_site.NO_PAGE in page and "🔧 test" in page and "👤 單人用" in page
    assert "所有工具（2 個）" in page
    assert links.tools_zip(lib) in page and links.problems_in("index", page, root=lib) == []

    tool = _read(os.path.join(lib, "docs", "site", f"{FAKE}.html"))
    assert links.pack_url(FAKE, root=lib) in tool                                       # 下載連結＝links.py
    assert links.pack_file(FAKE, "0.0.1") in tool
    assert "/fake 買牛奶" in tool and "記一筆" in tool and build_site.NO_PAGE in tool
    assert 'data-online=""' in tool and build_site.GENERATED in tool
    assert links.problems_in("tool", tool, root=lib) == []
    assert _stamp_ok(page) and _stamp_ok(tool)
    assert build_site.check(lib) == []
    assert build_site.write(lib) == []                                                  # 第二次沒有變
    assert dict(build_site.republish_list(lib))[f"docs/site/{FAKE}.html"] == ""        # 還沒有網址


def test_new_pack_links_its_artifact_once_registered(lib):
    build_site.write(lib)
    reg_path = os.path.join(lib, "docs", "artifacts.json")
    reg = json.loads(_read(reg_path))
    url = "https://claude.ai/artifact/FakeToolPage123"
    reg["pages"].append({"title": "假工具", "url": url, "local": f"docs/site/{FAKE}.html", "tool": FAKE, "made_by": "產生"})
    with open(reg_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False)
    assert set(build_site.check(lib)) and set(build_site.write(lib)) == {"docs/site/index.html", f"docs/site/{FAKE}.html"}
    assert f'href="{url}"' in _read(os.path.join(lib, "docs", "site", "index.html"))
    tool = _read(os.path.join(lib, "docs", "site", f"{FAKE}.html"))
    assert f'data-online="{url}"' in tool and _stamp_ok(tool)
    assert dict(build_site.republish_list(lib))[f"docs/site/{FAKE}.html"] == url


def test_new_pack_is_in_index_json(lib, tmp_path):
    out = str(tmp_path / "dist")
    result = build_script.build(out_dir=out, root=lib)
    assert result["problems"] == [], result["problems"]
    by_name = {e["name"]: e for e in result["index"]["packs"]}
    assert set(by_name) == {"notes", FAKE}
    entry = by_name[FAKE]
    assert entry["version"] == "0.0.1" and entry["stage"] == "test" and entry["line"] == "一句話的說明"
    assert entry["file"] == links.pack_file(FAKE, "0.0.1") and os.path.isfile(os.path.join(out, entry["file"]))
    assert links.pack_url(FAKE, root=lib).endswith("/" + entry["file"])
    assert by_name["notes"]["line"] == "記一筆、找、刪"


def test_removed_pack_drops_its_generated_page(lib):
    build_site.write(lib)
    shutil.rmtree(os.path.join(lib, "packs", FAKE))
    assert any(FAKE in p for p in build_site.check(lib))
    build_site.write(lib)
    assert not os.path.exists(os.path.join(lib, "docs", "site", f"{FAKE}.html"))
    assert FAKE not in _read(os.path.join(lib, "docs", "site", "index.html"))


def test_bad_site_block_is_refused(lib):
    path = os.path.join(lib, "packs", FAKE, "pack.json")
    raw = json.loads(_read(path))
    raw["site"]["colour"] = "red"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)
    with pytest.raises(ValueError):
        build_site.tools(lib)
    assert any("colour" in p for p in build_script.build(check_only=True, root=lib)["problems"])


def test_stamp_module_is_the_one_the_generator_uses():
    assert build_site.site_stamp.STAMP.pattern == site_stamp.STAMP.pattern
