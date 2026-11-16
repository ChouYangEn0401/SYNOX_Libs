"""重新產生 docs/guitar/chart_template.html（🎸 小節譜的獨立樣板頁）。

    python3 scripts/guitar_template.py           把 panel.html 的共用 CSS／HTML／JS 與和弦按法資料放進樣板
    python3 scripts/guitar_template.py --check   只檢查樣板有沒有跟上（沒跟上回 1）

樣板自己的部分（標題、顏色、id="chart-data" 的範例譜、開頭的程式）不動；只換這幾段：
- panel.html 裡 >>> 共用 CSS／HTML／JS … <<< 之間的內容 → 樣板裡同名的那幾段；
- packs/guitar/pack.py 的 library()（和弦按法）→ 樣板裡 id="chord-lib" 的 JSON。
不需要 SYNOX：pack.py 只有 install() 用到 SYNOX，這裡先放兩個空殼模組再載入它。
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(ROOT, "packs", "guitar", "panel.html")
PACK = os.path.join(ROOT, "packs", "guitar", "pack.py")
TEMPLATE = os.path.join(ROOT, "docs", "guitar", "chart_template.html")
BLOCKS = (("  /* >>> 共用 CSS", "  /* <<< 共用 CSS */"),
          ("<!-- >>> 共用 HTML", "<!-- <<< 共用 HTML -->"),
          ("  // >>> 共用 JS", "  // <<< 共用 JS"))
LIB = re.compile(r'(<script type="application/json" id="chord-lib">\n).*?(\n</script>)', re.S)


def load_pack():
    """載入 pack.py；沒裝 SYNOX 就放空殼（library() 用不到它們）。"""
    try:
        import isdtelekit.core.registry  # noqa: F401
        import synox.packs.kit  # noqa: F401
    except ImportError:
        reg = types.ModuleType("isdtelekit.core.registry")
        reg.WRITE, reg.Field = "write", (lambda *a, **k: None)
        kit = types.ModuleType("synox.packs.kit")
        packs = types.ModuleType("synox.packs")
        packs.kit = kit
        for name, mod in (("isdtelekit", types.ModuleType("isdtelekit")), ("isdtelekit.core", types.ModuleType("x")),
                          ("isdtelekit.core.registry", reg), ("synox", types.ModuleType("synox")),
                          ("synox.packs", packs), ("synox.packs.kit", kit)):
            sys.modules.setdefault(name, mod)
    spec = importlib.util.spec_from_file_location("guitar_pack", PACK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def block(text, start, end):
    """回 (開頭那行結束的位置, 結尾那行開始的位置)。"""
    a = text.index(start)
    a = text.index("\n", a) + 1
    return a, text.index(end, a)


def render():
    with open(PANEL, encoding="utf-8") as f:
        panel = f.read()
    with open(TEMPLATE, encoding="utf-8") as f:
        out = f.read()
    for start, end in BLOCKS:
        pa, pb = block(panel, start, end)
        ta, tb = block(out, start, end)
        out = out[:ta] + panel[pa:pb] + out[tb:]
    lib = json.dumps(load_pack().library(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return LIB.sub(lambda m: m.group(1) + lib + m.group(2), out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="只檢查，不寫檔")
    args = ap.parse_args()
    with open(TEMPLATE, encoding="utf-8") as f:
        now = f.read()
    new = render()
    if args.check:
        if new != now:
            print("docs/guitar/chart_template.html 沒跟上 panel.html／pack.py：跑 python3 scripts/guitar_template.py")
            return 1
        print("docs/guitar/chart_template.html 是最新的")
        return 0
    if new != now:
        with open(TEMPLATE, "w", encoding="utf-8") as f:
            f.write(new)
        print("寫好 docs/guitar/chart_template.html")
    else:
        print("docs/guitar/chart_template.html 本來就是最新的")
    return 0


if __name__ == "__main__":
    sys.exit(main())
