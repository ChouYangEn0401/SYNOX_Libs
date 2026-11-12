# -*- coding: utf-8 -*-
"""打包與安裝的整條路（ADR-0001），全部在暫存資料夾、不連網、不開埠：

工具庫 packs/ → scripts/build.py → dist/*.synoxpack ＋ index.json → 一個「空的」SYNOX（沒有任何工具）
→ 用 SYNOX 的安裝流程（第 1、2 關 → 確認 → packs.lock → 搬進 packs/）一個一個裝 → 重開 → 每個工具都能用。"""
import hashlib
import json
import os
import shutil
import zipfile

import pytest

from isdtelekit.testing import OWNER_ID, FakeChannel

import build as build_script
from conftest import PACK_NAMES, PACKS, ROOT

from synox.packs.manifest import STAGES
from synox.sdk import author

# 每個工具裝好之後試一個指令：(指令, 回覆裡要有的字)
SMOKE = {"money": ("/money 120 午餐", "120"), "notes": ("/note 買牛奶", "記下了"),
         "calendar": ("/cal 明天 14:00 看牙醫", "加好了"), "todo": ("/todo 買牛奶", "加好了"), "bullet": ("/bj • 買牛奶", "記到今天"),
         # 接外面服務的：還沒連接時回「要先設定」，不連網
         "notion": ("/notion 會議", "還沒設定 Notion"), "todoist": ("/todoist_list", "還沒接上 Todoist"),
         "google-calendar": ("/gcal_today", "還沒連上 Google 日曆"), "apple-reminders": ("/applerem_status", "還沒設定帳號")}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("dist"))
    result = build_script.build(out_dir=out)
    assert result["problems"] == [], result["problems"]
    return out, result["index"]


def _empty_host(tmp_path, packs_dir):
    from synox.app import build
    from synox.config import SynoxConfig
    from synox.llm.fake import FakeAdapter
    from synox.vault import Vault
    data = tmp_path / "data"
    config = SynoxConfig.from_dict({"owner_id": OWNER_ID, "data_dir": str(data), "vault": {"mode": "file"},
                                    "llm": {"kind": "fake"}}, require_token=False, root=str(tmp_path))
    api = FakeChannel()
    app = build(config, home=api, adapter=FakeAdapter(None), vault=Vault(b"k" * 32, str(data)), pack_dirs=[packs_dir])
    return app, api, str(data)


def _say(app, api, text):
    from isdtelekit.core.channel import Inbound
    app.dispatcher.handle(api, Inbound("test", OWNER_ID, OWNER_ID, text, username="owner"), "typed")
    return api.last(OWNER_ID)


def test_every_tool_is_in_smoke_list():
    assert set(SMOKE) == set(PACK_NAMES)


def test_index_lists_every_tool_with_hash_and_sdk(built):
    out, index = built
    assert index["library"] == "SYNOX_Libs" and index["library_version"] == build_script.library_version()
    assert index["built_with"]["sdk"] == author.SDK_VERSION
    assert {e["name"] for e in index["packs"]} == set(PACK_NAMES)
    with open(os.path.join(out, "index.json"), encoding="utf-8") as f:
        assert json.load(f) == index
    for e in index["packs"]:
        path = os.path.join(out, e["file"])
        assert e["file"] == f"{e['name']}-{e['version']}.synoxpack"
        with open(path, "rb") as f:
            blob = f.read()
        assert hashlib.sha256(blob).hexdigest() == e["sha256"] and len(blob) == e["size"]
        assert e["requires"]["sdk"] and e["requires"]["synox"]
        assert e["stage"] in STAGES, e                        # 穩定度：每個工具都明寫（README「穩定度」）
        assert e["bundles"], f"{e['name']} 不在任何建議群組"
        with zipfile.ZipFile(path) as z:
            raw = json.loads(z.read("pack.json"))
        assert raw["integrity"]["sha256"]                     # zip_pack 算好的雜湊（SYNOX 安裝時會對）
    for key, bundle in index["bundles"].items():
        assert set(bundle["packs"]) <= set(PACK_NAMES), key


def test_empty_host_then_install_every_synoxpack(built, tmp_path):
    """空的 SYNOX：沒有任何工具；把 dist/ 裡的每個 .synoxpack 走安裝流程裝進去，重開後每個都能用。"""
    from synox.packs import admin
    out, index = built
    packs_dir = str(tmp_path / "packs")
    os.makedirs(packs_dir)
    app, api, data = _empty_host(tmp_path, packs_dir)
    assert app.extras["synox_pack_report"].items == []                          # 一開始是空的
    assert "未知指令" in _say(app, api, "/money 120 午餐")
    for e in index["packs"]:
        with open(os.path.join(out, e["file"]), "rb") as f:
            token, summary = admin.stage(f.read(), data)
        assert summary["scan"]["ok"], (e["name"], summary["scan"]["problems"])
        assert admin.install_staged(token, data, packs_dir, approved_by=OWNER_ID, via="test") == e["name"]
    app, api, data = _empty_host(tmp_path, packs_dir)                           # 重開
    status = {i.name: (i.status, i.lock) for i in app.extras["synox_pack_report"].items}
    assert status == {n: ("loaded", "locked") for n in PACK_NAMES}, status
    for name, (command, needle) in SMOKE.items():
        assert needle in _say(app, api, command), name


def test_console_install_command(built, tmp_path):
    """`python -m synox install <檔> --yes`（終端機那條路）也裝得進去。"""
    from synox.__main__ import main
    out, index = built
    entry = next(e for e in index["packs"] if e["name"] == "notes")
    data = tmp_path / "data"
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"owner_id": OWNER_ID, "data_dir": str(data), "vault": {"mode": "file"}}), encoding="utf-8")
    packs_dir = tmp_path / "packs"
    code = main(["--config", str(cfg), "install", os.path.join(out, entry["file"]), "--yes",
                 "--packs-dir", str(packs_dir)])
    assert code == 0 and (packs_dir / "notes" / "pack.json").is_file()


def _copy_library(tmp_path, names=("notes",)):
    root = tmp_path / "libs"
    for rel in ("VERSION", "catalog.json"):
        os.makedirs(root, exist_ok=True)
        shutil.copy(os.path.join(ROOT, rel), root / rel)
    (root / "catalog.json").write_text('{"bundles": {}}', encoding="utf-8")
    for name in names:
        shutil.copytree(os.path.join(PACKS, name), root / "packs" / name)
    return root


def test_build_refuses_tool_without_sdk_range(tmp_path):
    root = _copy_library(tmp_path)
    path = root / "packs" / "notes" / "pack.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["requires"]["sdk"]
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    result = build_script.build(out_dir=str(tmp_path / "out"), root=str(root))
    assert result["index"] is None and any("requires.sdk" in p for p in result["problems"])
    assert not os.path.exists(tmp_path / "out")                                 # 有問題就什麼都不寫


@pytest.mark.parametrize("stage", [None, "gold", "Alpha"])
def test_build_refuses_tool_without_valid_stage(tmp_path, stage):
    """工具庫的每個工具都要明寫 stage（SYNOX 那邊沒寫＝alpha，這裡不准省略）；寫錯的第 1 關就不過。"""
    root = _copy_library(tmp_path)
    path = root / "packs" / "notes" / "pack.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if stage is None:
        del raw["stage"]
    else:
        raw["stage"] = stage
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    result = build_script.build(out_dir=str(tmp_path / "out"), root=str(root))
    assert result["index"] is None and any("stage" in p for p in result["problems"]), result["problems"]


def test_build_refuses_sdk_range_this_synox_does_not_meet(tmp_path):
    root = _copy_library(tmp_path)
    path = root / "packs" / "notes" / "pack.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["requires"]["sdk"] = ">=99.0"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    result = build_script.build(out_dir=str(tmp_path / "out"), root=str(root))
    assert any("SYNOX SDK >=99.0" in p for p in result["problems"])


def test_bundled_synox_sdk_must_match_the_range(tmp_path):
    """cli／mcp 工具附的 synox_sdk.py：它的 SDK_VERSION 也要在 requires.sdk 範圍內（只讀字面字，不執行）。"""
    root = _copy_library(tmp_path)
    folder = root / "packs" / "notes"
    (folder / "synox_sdk.py").write_text('SDK_VERSION = "0.1.0"\n', encoding="utf-8")
    assert build_script.bundled_sdk_version(str(folder)) == "0.1.0"
    assert any("synox_sdk.py 是 SDK 0.1.0" in p for p in build_script.problems_of(str(folder)))
    (folder / "synox_sdk.py").write_text(f'SDK_VERSION = "{author.SDK_VERSION}"\n', encoding="utf-8")
    assert not any("synox_sdk.py" in p for p in build_script.problems_of(str(folder)))


def test_catalog_only_names_existing_tools(tmp_path):
    root = _copy_library(tmp_path)
    (root / "catalog.json").write_text('{"bundles": {"x": {"title": "x", "packs": ["nope"]}}}', encoding="utf-8")
    result = build_script.build(check_only=True, root=str(root))
    assert any("nope" in p for p in result["problems"])


def test_check_mode_passes_on_this_repo():
    assert build_script.build(check_only=True)["problems"] == []
