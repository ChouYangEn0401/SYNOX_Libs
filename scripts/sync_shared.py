"""把 packs/_shared/ 的正本複製進每個用到它的 pack.py（夾在 `# >>> <名字>` 與 `# <<< <名字>` 之間）。

為什麼不直接 import：SYNOX 的 loader 只執行 pack 資料夾裡的 pack.py，不把資料夾放進 sys.path，
pack 之間也不能互相 import（SYNOX docs/AGENT.md invariant 3），所以共用的程式只能一份一份放進去。

    python scripts/sync_shared.py          改寫有差異的 pack.py
    python scripts/sync_shared.py --check  只檢查（有差異就 exit 1；tests/test_shared_sync.py 也檢查）
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED = os.path.join(ROOT, "packs", "_shared")
# 正本 → 用到它的 pack
TARGETS = {"zhdate": ("calendar", "todo", "google-calendar", "todoist")}


def block(text: str, name: str) -> str:
    """從『行首的 # >>> 名字』到『行首的 # <<< 名字』那一行結尾（說明文字裡提到標記不算）。"""
    m = re.search(rf"^# >>> {re.escape(name)}\b.*?^# <<< {re.escape(name)}[^\n]*$", text, re.M | re.S)
    if m is None:
        raise ValueError(f"找不到 {name} 的標記")
    return m.group(0)


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main(argv) -> int:
    check = "--check" in argv
    stale = []
    for name, packs in TARGETS.items():
        canonical = block(read(os.path.join(SHARED, name + ".py")), name)
        for pack in packs:
            path = os.path.join(ROOT, "packs", pack, "pack.py")
            text = read(path)
            current = block(text, name)
            if current == canonical:
                continue
            stale.append(f"{pack}/pack.py ({name})")
            if not check:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text.replace(current, canonical))
    if stale:
        print(("過期：" if check else "更新了：") + "、".join(stale))
        return 1 if check else 0
    print("全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
