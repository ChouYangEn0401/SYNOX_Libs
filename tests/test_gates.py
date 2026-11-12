"""三個 pack 都要過 SYNOX 的第 1 關（pack.json 宣告檢查，嚴格版）與第 2 關（靜態掃描），
再加一層這個 repo 自己的規矩：不連網、不開別的程式、不動態執行，碰檔案只准 calendar 的 _write_own／os.remove（pack.path 底下）。"""

import ast
import os
import re

import pytest

from synox.packs import manifest as synox_manifest
from synox.packs import scan as synox_scan

from conftest import PACK_NAMES, PACKS

# pack 名稱 → 允許的 capabilities（其他一律不准）
ALLOWED_CAPS = {"calendar": ["filesystem:own"], "todo": [], "bullet": []}
FORBIDDEN_IMPORTS = ("requests", "urllib", "http", "socket", "ssl", "subprocess", "multiprocessing", "ctypes",
                     "importlib", "pickle", "marshal", "shutil", "pathlib", "asyncio", "threading")
FORBIDDEN_TEXT = (r"\bos\.system\b", r"\bos\.popen\b", r"\bsubprocess\b", r"\beval\(", r"\bexec\(", r"__import__",
                  r"\bos\.environ\b", r"\bgetenv\b", r"app\.jobs", r"\bapi\.post\(")


def folder(name):
    return os.path.join(PACKS, name)


def source(name):
    with open(os.path.join(folder(name), "pack.py"), "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("name", PACK_NAMES)
def test_gate1_manifest_strict(name):
    m = synox_manifest.read(folder(name), strict=True)
    assert m.kind == "python" and m.access == "member" and m.storage == "vault"
    assert m.capabilities_declared and m.capabilities == ALLOWED_CAPS[name]
    assert m.web["panel"] == "panel.html" and m.web["api"] == ["today"]
    assert os.path.isfile(m.skill_path) and os.path.isfile(os.path.join(folder(name), "README.md"))


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
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        else:
            continue
        for mod in mods:
            assert mod.split(".")[0] not in FORBIDDEN_IMPORTS, f"{name}: import {mod}"
            assert mod.split(".")[0] in ("re", "time", "datetime", "dataclasses", "os", "isdtelekit", "synox"), \
                f"{name}: unexpected import {mod}"
    for pattern in FORBIDDEN_TEXT:
        assert not re.search(pattern, text), f"{name}: {pattern}"
    opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "open"]
    os_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and getattr(n.func.value, "id", None) == "os"]
    if name == "calendar":
        # 唯一的 open 在 _write_own 裡、唯一的 os.* 是送完 .ics 之後的 os.remove；路徑都來自 pack.path()
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_write_own")
        assert len(opens) == 1 and opens[0] in list(ast.walk(fn))
        assert [n.func.attr for n in os_calls] == ["remove"]
        assert "path = pack.path(" in text and "_write_own(path," in text and "os.remove(path)" in text
    else:
        assert not opens and not os_calls, f"{name} must not touch files"
        assert "import os" not in text


def test_no_personal_data_in_repo():
    """repo 裡不放真的 Telegram id、token 或 email（範例一律佔位字）。"""
    root = os.path.dirname(PACKS)
    token = re.compile(r"\d{8,10}:[A-Za-z0-9_-]{30,}")
    mail = re.compile(r"[\w.+-]+@(?!synox\.local|anthropic\.com)[\w-]+\.[\w.]+")
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", ".venv", "__pycache__", ".pytest_cache")]
        for f in files:
            if not f.endswith((".py", ".md", ".json", ".html")):
                continue
            with open(os.path.join(base, f), "r", encoding="utf-8") as fh:
                text = fh.read()
            assert not token.search(text), f"{f}: looks like a bot token"
            assert not mail.search(text), f"{f}: has an email address"


@pytest.mark.parametrize("name", PACK_NAMES)
def test_synox_sdk_check_and_zip(name, tmp_path):
    """synox-sdk check／zip（等於 SYNOX 的 scripts/pack_zip.py）：過第 1、2 關，打包出 .synoxpack。"""
    import zipfile
    sdk = pytest.importorskip("synox.sdk")
    check = sdk.Pack.load(folder(name))
    assert [p for p in check.problems if not p.startswith("integrity")] == []
    path = sdk.zip_pack(folder(name), str(tmp_path))
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
    assert {"pack.json", "pack.py", "SKILL.md", "README.md", "panel.html"} <= names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
