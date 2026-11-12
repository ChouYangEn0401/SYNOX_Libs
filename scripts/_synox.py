"""找 SYNOX（主程式、host）並讓它 import 得到。工具庫不複製 SYNOX 的任何程式：打包、檢查、測試都直接用那一份。

找的順序（scripts/build.py、tests/conftest.py 都經這裡；不靠「剛好放在旁邊」）：
1. 環境變數 SYNOX_HOME＝SYNOX 資料夾（裡面有 src/synox；SYNOX 自己的 config.ROOT 也認這個變數）。
   設了卻不對 → 直接報錯，不往下猜。
2. 跑這支的 Python 自己就 import 得到 synox（用 <SYNOX>\\.venv\\Scripts\\python.exe 跑，`pip install -e` 裝的那份）→ 用它。
3. 後備：跟這個 repo 並排的 ../SYNOX——只是維護者的方便（how＝"sibling"）。
都沒有 → 結束，列出試過哪些地方、怎麼設。只 import、不改它。
"""

from __future__ import annotations

import importlib.util
import os
import sys

LIBS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKS = os.path.join(LIBS_ROOT, "packs")
HOW_TO = ("設環境變數 SYNOX_HOME=<SYNOX 資料夾>（git clone 下來的那個，裡面有 src\\synox），"
          "或用 SYNOX 的 Python 跑（<SYNOX>\\.venv\\Scripts\\python.exe）。")


class SynoxNotFound(RuntimeError):
    pass


def _is_synox_folder(root: str) -> bool:
    return os.path.isfile(os.path.join(root, "src", "synox", "__init__.py"))


def locate(environ=None, find_spec=importlib.util.find_spec, libs_root: str = LIBS_ROOT) -> tuple:
    """→ (SYNOX 資料夾或 ""（import 得到、但不是原始碼版面）, 怎麼找到的：env｜python｜sibling)。
    找不到丟 SynoxNotFound（訊息列出試過的地方）。不 import synox（只問 find_spec）。"""
    environ = os.environ if environ is None else environ
    home_dir = (environ.get("SYNOX_HOME") or "").strip()
    if home_dir:
        root = os.path.abspath(home_dir)
        if not _is_synox_folder(root):
            raise SynoxNotFound(f"SYNOX_HOME={root} 不是 SYNOX 資料夾（裡面沒有 src\\synox\\__init__.py）。\n{HOW_TO}")
        return root, "env"
    tried = ["環境變數 SYNOX_HOME：沒設"]
    try:
        spec = find_spec("synox")
    except (ImportError, ValueError):
        spec = None
    if spec is not None and spec.origin:
        src = os.path.dirname(os.path.dirname(os.path.abspath(spec.origin)))      # …/src/synox/__init__.py → …/src
        root = os.path.dirname(src) if os.path.basename(src).lower() == "src" else ""
        return (root if root and _is_synox_folder(root) else ""), "python"
    tried.append(f"這個 Python（{sys.executable}）：import 不到 synox")
    sibling = os.path.join(os.path.dirname(libs_root), "SYNOX")           # clean-room: sibling-fallback
    if _is_synox_folder(sibling):
        return os.path.abspath(sibling), "sibling"
    tried.append(f"工具庫旁邊的 {sibling}：沒有")
    raise SynoxNotFound("找不到 SYNOX：\n  - " + "\n  - ".join(tried) + "\n" + HOW_TO)


def home() -> str:
    """SYNOX 資料夾（找不到、或 import 得到但不是原始碼版面時回 ""）。"""
    try:
        return locate()[0]
    except SynoxNotFound:
        return ""


def ensure() -> str:
    """讓 synox、isdtelekit import 得到，回 SYNOX 資料夾（不是原始碼版面時回 ""）；找不到就結束並說怎麼設。"""
    try:
        root, _how = locate()
    except SynoxNotFound as e:
        raise SystemExit(str(e))
    if root:
        for sub in ("vendor", "src"):
            path = os.path.join(root, sub)
            if path not in sys.path:
                sys.path.insert(0, path)
    return root


def pack_folders(packs_dir: str = PACKS) -> list:
    """packs/ 底下每個有 pack.json 的資料夾（_ 開頭的是共用程式，例如 _shared，跳過）。"""
    out = []
    for name in sorted(os.listdir(packs_dir)):
        folder = os.path.join(packs_dir, name)
        if not name.startswith("_") and os.path.isfile(os.path.join(folder, "pack.json")):
            out.append(folder)
    return out
