"""每個工具都要過 SYNOX 的第 1 關（pack.json 宣告檢查，嚴格版）與第 2 關（靜態掃描），宣告 requires.sdk（ADR-0001），
再加一層這個 repo 自己的規矩：
- 本機工具（notes、remind、todo、money、calendar、bullet）：不連網、不開別的程式、不動態執行，
  碰檔案只准 calendar 的 _write_own／os.remove（pack.path_of 底下）。
- 接外面服務的（notion、todoist、google-calendar、apple-reminders，2026-11-12 從 SYNOX connectors/ 搬來）：
  只宣告、也只用得到 network（requests）；一樣不開別的程式、不動態執行、不碰檔案、不讀環境變數；只給擁有者（access: owner）。"""

import ast
import os
import re

import pytest

from synox.packs import manifest as synox_manifest
from synox.packs import scan as synox_scan
from synox.sdk import author

from conftest import PACK_NAMES, PACKS, PLANNER

CONNECT = ("notion", "todoist", "google-calendar", "apple-reminders")
# pack 名稱 → 宣告的 capabilities（其他一律不准；每個都要明寫，沒有就寫 []——ADR-0003 C5）
ALLOWED_CAPS = {"calendar": ["filesystem:own"], "todo": [], "bullet": [], "money": [], "notes": [],
                **{name: ["network"] for name in CONNECT}}
ALLOWED_IMPORTS = ("re", "time", "datetime", "dataclasses", "collections", "os", "isdtelekit", "synox")
# 接外面服務的多准：requests、urllib.parse、編碼／雜湊／亂數、XML（CalDAV）、時區、型別、log
CONNECT_IMPORTS = ALLOWED_IMPORTS + ("requests", "urllib", "base64", "hashlib", "secrets", "uuid", "xml", "zoneinfo",
                                     "typing", "logging", "__future__")
FORBIDDEN_IMPORTS = ("requests", "urllib", "http", "socket", "ssl", "subprocess", "multiprocessing", "ctypes",
                     "importlib", "pickle", "marshal", "shutil", "pathlib", "asyncio", "threading")
CONNECT_FORBIDDEN = tuple(m for m in FORBIDDEN_IMPORTS if m not in ("requests", "urllib"))
FORBIDDEN_TEXT = (r"\bos\.system\b", r"\bos\.popen\b", r"\bsubprocess\b", r"\beval\(", r"\bexec\(", r"__import__",
                  r"\bos\.environ\b", r"\bgetenv\b", r"app\.jobs", r"\bapi\.post\(")


def folder(name):
    return os.path.join(PACKS, name)


def source(name):
    with open(os.path.join(folder(name), "pack.py"), "r", encoding="utf-8") as f:
        return f.read()


def test_every_tool_is_listed():
    assert set(PACK_NAMES) == set(ALLOWED_CAPS)


@pytest.mark.parametrize("name", PACK_NAMES)
def test_gate1_manifest_strict(name):
    m = synox_manifest.read(folder(name), strict=True)
    assert m.kind == "python" and m.access == ("owner" if name in CONNECT else "member") and m.name == name
    assert os.path.isfile(m.skill_path) and os.path.isfile(os.path.join(folder(name), "README.md"))
    assert m.capabilities_declared and m.capabilities == ALLOWED_CAPS[name]
    if name in PLANNER:
        assert m.storage == "vault" and m.web["panel"] == "panel.html" and m.web["api"] == ["today"]


@pytest.mark.parametrize("name", PACK_NAMES)
def test_declares_sdk_range_that_this_synox_satisfies(name):
    """ADR-0001：每個工具都宣告 requires.sdk（與 requires.synox），而且拿來測試的這份 SYNOX 合。"""
    m = synox_manifest.read(folder(name), strict=True)
    assert m.requires.get("sdk") and m.requires.get("synox")
    assert synox_manifest.synox_problem(m) is None
    assert synox_manifest.sdk_problem(m, author.SDK_VERSION) is None


@pytest.mark.parametrize("name", PACK_NAMES)
def test_declares_stage(name):
    """每個工具都明寫穩定度（test／alpha／beta／stable；README「穩定度」），SYNOX 讀得到同一個值。"""
    m = synox_manifest.read(folder(name), strict=True)
    assert m.stage_declared and m.stage in synox_manifest.STAGES and m.raw["stage"] == m.stage


@pytest.mark.parametrize("name", PACK_NAMES)
def test_declares_scope(name):
    """ADR-0003 入口標準：只能私訊、資料每人一份、落地是密文；access、surfaces、data、storage、capabilities 都明寫
    （SYNOX 有預設值的也寫，預設值改了不會悄悄變）。"""
    m = synox_manifest.read(folder(name), strict=True)
    assert m.surfaces == ["dm"] and m.data == "user" and m.storage == "vault"
    assert m.surfaces_declared and m.data_declared and m.capabilities_declared
    assert all(key in m.raw for key in ("access", "surfaces", "data", "storage", "capabilities"))
    assert m.raw["surfaces"] == ["dm"] and m.raw["data"] == "user" and m.raw["storage"] == "vault"


@pytest.mark.parametrize("name", PACK_NAMES)
def test_gate2_static_scan_passes(name):
    m = synox_manifest.read(folder(name), strict=True)
    report = synox_scan.scan(m)
    assert report.problems(m) == [], report.format(m)
    assert report.required_capabilities == ALLOWED_CAPS[name], report.format(m)


@pytest.mark.parametrize("name", PACK_NAMES)
def test_no_network_subprocess_exec_or_stray_files(name):
    text = source(name)
    tree = ast.parse(text)
    forbidden, allowed = (CONNECT_FORBIDDEN, CONNECT_IMPORTS) if name in CONNECT else (FORBIDDEN_IMPORTS, ALLOWED_IMPORTS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        else:
            continue
        for mod in mods:
            assert mod.split(".")[0] not in forbidden, f"{name}: import {mod}"
            assert mod.split(".")[0] in allowed, f"{name}: unexpected import {mod}"
            if name in CONNECT and mod.split(".")[0] == "urllib":
                assert mod == "urllib.parse", f"{name}: 連網只准用 requests（{mod}）"
    for pattern in FORBIDDEN_TEXT:
        assert not re.search(pattern, text), f"{name}: {pattern}"
    opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "open"]
    os_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and getattr(n.func.value, "id", None) == "os"]
    if name == "calendar":
        # 唯一的 open 在 _write_own 裡、唯一的 os.* 是送完 .ics 之後的 os.remove；路徑都來自 pack.path_of(user, …)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_write_own")
        assert len(opens) == 1 and opens[0] in list(ast.walk(fn))
        assert [n.func.attr for n in os_calls] == ["remove"]
        assert "path = pack.path_of(user, " in text and "_write_own(path," in text and "os.remove(path)" in text
        assert "pack.path(" not in text                 # data: user 不能用全部人共用的資料夾
    else:
        assert not opens and not os_calls, f"{name} must not touch files"
        assert "import os" not in text


def test_no_personal_data_in_repo():
    """repo 裡不放真的 Telegram id、token 或 email（範例一律佔位字）。哪些 email 算佔位，跟 scripts/public_check.py 同一份規則
    （完整的公開版檢查在 tests/test_public_check.py；這裡是不靠 git 的快速版，連 .gitignore 掉的檔也看）。"""
    import public_check
    root = os.path.dirname(PACKS)
    token = re.compile(r"\d{8,10}:[A-Za-z0-9_-]{30,}")
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", ".venv", "__pycache__", ".pytest_cache", "dist")]
        for f in files:
            if not f.endswith((".py", ".md", ".json", ".html")):
                continue
            with open(os.path.join(base, f), "r", encoding="utf-8") as fh:
                text = fh.read()
            assert not token.search(text), f"{f}: looks like a bot token"
            assert all(public_check.email_allowed(m.group(0)) for m in public_check.EMAIL.finditer(text)), \
                f"{f}: has an email address"


@pytest.mark.parametrize("name", PACK_NAMES)
def test_synox_sdk_check_and_zip(name, tmp_path):
    """synox-sdk check／zip（等於 SYNOX 的 scripts/pack_zip.py）：過第 1、2 關，打包出 .synoxpack。"""
    import zipfile

    from synox import sdk
    check = sdk.Pack.load(folder(name))
    assert [p for p in check.problems if not p.startswith("integrity")] == []
    path = sdk.zip_pack(folder(name), str(tmp_path))
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
    assert {"pack.json", "pack.py", "SKILL.md", "README.md"} <= names
    if os.path.isfile(os.path.join(folder(name), "panel.html")):
        assert "panel.html" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
