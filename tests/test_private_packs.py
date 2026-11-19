# -*- coding: utf-8 -*-
"""私人工具（pack.json "publish": false；docs/CONTRIBUTING-TOOLS.md「私人工具」）：只在自己電腦打包，
永遠不出現在 index.json、tools.zip、SHA256SUMS、下載連結、公開頁、建議群組。全部在暫存資料夾、不連網、不開埠。"""
import json
import os
import shutil
import zipfile

import build as build_script
import build_site
import links
from conftest import PACKS, ROOT, _synox


def _library(tmp_path, publish=False):
    """暫存的工具庫：notes（公開）＋ privy（notes 的複本，改名、publish 設成給的值）。"""
    root = tmp_path / "libs"
    os.makedirs(root)
    shutil.copy(os.path.join(ROOT, "VERSION"), root / "VERSION")
    (root / "catalog.json").write_text('{"bundles": {}}', encoding="utf-8")
    shutil.copytree(os.path.join(PACKS, "notes"), root / "packs" / "notes")
    shutil.copytree(os.path.join(PACKS, "notes"), root / "packs" / "privy")
    path = root / "packs" / "privy" / "pack.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(name="privy", group="privy", title="🔒 私人", publish=publish)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return root


def test_is_published_reads_the_flag(tmp_path):
    root = _library(tmp_path)
    packs = str(root / "packs")
    assert _synox.is_published(os.path.join(packs, "notes"))
    assert not _synox.is_published(os.path.join(packs, "privy"))
    assert [os.path.basename(f) for f in _synox.pack_folders(packs)] == ["notes", "privy"]
    assert [os.path.basename(f) for f in _synox.pack_folders(packs, private=False)] == ["notes"]


def test_full_build_leaves_private_pack_out_of_index_zip_and_sums(tmp_path):
    root = _library(tmp_path)
    out = tmp_path / "out"
    result = build_script.build(out_dir=str(out), root=str(root))
    assert result["problems"] == [], result["problems"]
    index = result["index"]
    assert [e["name"] for e in index["packs"]] == ["notes"]
    assert not [n for n in os.listdir(out) if "privy" in n]
    written = build_script.release_extras(index, str(out), str(root))
    for name in written:
        assert "privy" not in name
    with zipfile.ZipFile(out / links.tools_zip(str(root))) as z:
        assert not [n for n in z.namelist() if "privy" in n]
        assert "privy" not in z.read("tools/index.json").decode("utf-8")
    assert "privy" not in (out / links.SUMS_FILE).read_text(encoding="utf-8")
    assert "privy" not in (out / links.INDEX_FILE).read_text(encoding="utf-8")


def test_named_build_still_makes_a_local_pack_in_dist_private(tmp_path):
    root = _library(tmp_path)
    assert build_script.default_out(["privy"], str(root)) == os.path.join(str(root), "dist", build_script.PRIVATE_DIST)
    assert build_script.default_out(["notes"], str(root)) == os.path.join(str(root), "dist")
    assert build_script.default_out(None, str(root)) == os.path.join(str(root), "dist")
    result = build_script.build(["privy"], root=str(root))           # 沒給 out_dir → dist/private/
    assert result["problems"] == [], result["problems"]
    entry = result["index"]["packs"][0]
    assert entry["name"] == "privy"
    assert os.path.isfile(os.path.join(str(root), "dist", "private", entry["file"]))
    assert not os.path.exists(os.path.join(str(root), "dist", entry["file"]))


def test_links_and_site_never_see_private_pack(tmp_path):
    root = str(_library(tmp_path))
    assert links.tool_names(root) == ["notes"]
    assert "privy" not in links.current_versions(root)
    assert not [a for a in links.expected_assets(root) if "privy" in a]
    assert [t["name"] for t in build_site.tools(root)] == ["notes"]


def test_catalog_cannot_list_private_pack(tmp_path):
    root = _library(tmp_path)
    (root / "catalog.json").write_text('{"bundles": {"x": {"title": "x", "packs": ["privy"]}}}', encoding="utf-8")
    result = build_script.build(check_only=True, root=str(root))
    assert any("私人工具 privy" in p for p in result["problems"]), result["problems"]


def test_publish_must_be_a_boolean_and_bad_values_count_as_private(tmp_path):
    root = _library(tmp_path, publish="no")
    assert not _synox.is_published(str(root / "packs" / "privy"))     # 寧可漏發
    result = build_script.build(check_only=True, root=str(root))
    assert any("publish 只能是 true 或 false" in p for p in result["problems"]), result["problems"]


def test_publish_true_is_an_ordinary_public_pack(tmp_path):
    root = _library(tmp_path, publish=True)
    result = build_script.build(out_dir=str(tmp_path / "out"), root=str(root))
    assert result["problems"] == [], result["problems"]
    assert [e["name"] for e in result["index"]["packs"]] == ["notes", "privy"]


def test_this_repo_private_packs_stay_out_of_public_outputs():
    """這個 repo 的私人工具（如果有）：沒有下載連結、沒有公開頁、不在建議群組、不在入口頁。"""
    private = build_script.private_names()
    assert not set(private) & set(links.tool_names())
    with open(os.path.join(ROOT, "catalog.json"), encoding="utf-8") as f:
        listed = {n for b in (json.load(f).get("bundles") or {}).values() for n in b.get("packs", [])}
    assert not set(private) & listed
    site = os.path.join(ROOT, "docs", "site")
    index_page = open(os.path.join(site, "index.html"), encoding="utf-8").read()
    for name in private:
        assert not os.path.exists(os.path.join(site, f"{name}.html")), name
        assert f"packs/{name}/" not in index_page and f"{name}.html" not in index_page, name
    assert not set(private) & {t["name"] for t in build_site.tools()}
