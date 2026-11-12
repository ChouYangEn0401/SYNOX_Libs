"""找 SYNOX（主程式、host）並讓它 import 得到。工具庫不複製 SYNOX 的任何程式：打包、檢查、測試都直接用那一份。

找的順序：環境變數 SYNOX_HOME（SYNOX 資料夾；SYNOX 自己的 config.ROOT 也認這個變數）→ 跟這個 repo 並排的 ../SYNOX。
只 import、不改它。
"""

from __future__ import annotations

import os
import sys

LIBS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKS = os.path.join(LIBS_ROOT, "packs")


def home() -> str:
    return os.path.abspath(os.environ.get("SYNOX_HOME") or os.path.join(os.path.dirname(LIBS_ROOT), "SYNOX"))


def ensure() -> str:
    """把 <SYNOX>/src、<SYNOX>/vendor 放到 sys.path 最前面，回 SYNOX 資料夾；找不到就結束並說怎麼設。"""
    root = home()
    if not os.path.isfile(os.path.join(root, "src", "synox", "__init__.py")):
        raise SystemExit(f"找不到 SYNOX：{root}\n設環境變數 SYNOX_HOME=<SYNOX 資料夾>，或把 SYNOX 放在工具庫旁邊（../SYNOX）。")
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
