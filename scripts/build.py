"""每個工具 → dist/<name>-<version>.synoxpack，再寫一份目錄 dist/index.json（使用者拿到一堆壓縮檔，自己挑要裝哪個）。

    <SYNOX>\\.venv\\Scripts\\python.exe scripts\\build.py                 全部
    <SYNOX>\\.venv\\Scripts\\python.exe scripts\\build.py money notes     只打這幾個（index.json 只列這幾個）
    <SYNOX>\\.venv\\Scripts\\python.exe scripts\\build.py --check         只檢查、不寫檔

打包本身用 SYNOX 的 synox.sdk.zip_pack（＝安裝時的第 1、2 關，沒過不打包；zip 裡的 pack.json 帶 integrity.sha256）。
這裡另外擋（ADR-0001）：
- 每個工具都要寫 requires.sdk，而且範圍包含「拿來打包的這份 SYNOX」的 SDK_VERSION；requires.synox 也要合；
- 工具資料夾裡附了 synox_sdk.py（cli／mcp 工具）的話，它的 SDK_VERSION 也要在 requires.sdk 範圍內；
- 每個工具都要明寫 stage（test／alpha／beta／stable；README「穩定度」），index.json 也帶它；
- 每個工具都要明寫 access、surfaces、data、storage、capabilities（ADR-0003「入口標準」；SYNOX 有預設值的也要寫，
  預設值哪天改了不會悄悄變）；
- catalog.json 的建議群組只能列存在的工具。
SYNOX 在哪：scripts/_synox.py（SYNOX_HOME → 跑這支的 Python 裝的那份 → 後備並排的 ../SYNOX）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _synox  # noqa: E402

ROOT = _synox.LIBS_ROOT
SDK_FILE = "synox_sdk.py"
SDK_VERSION_RE = re.compile(r'^SDK_VERSION\s*=\s*"([0-9][0-9A-Za-z.\-]*)"', re.M)


def library_version(root: str = ROOT) -> str:
    with open(os.path.join(root, "VERSION"), "r", encoding="utf-8") as f:
        return f.read().strip()


def load_catalog(root: str = ROOT) -> dict:
    path = os.path.join(root, "catalog.json")
    if not os.path.isfile(path):
        return {"bundles": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def bundled_sdk_version(folder: str):
    """工具資料夾裡附的 synox_sdk.py 的 SDK_VERSION（沒附回 None；只讀字面字，不執行它）。"""
    path = os.path.join(folder, SDK_FILE)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        found = SDK_VERSION_RE.search(f.read())
    return found.group(1) if found else ""


def problems_of(folder: str) -> list:
    """一個工具資料夾在打包前的問題（ADR-0001 的規則；第 1、2 關由 zip_pack 另外跑）。"""
    from synox.packs.manifest import ManifestError, read, sdk_problem, synox_problem
    name = os.path.basename(folder)
    try:
        m = read(folder, strict=True)
    except ManifestError as e:
        return [f"{name}: pack.json 不合：{e}"]
    out = []
    spec = (m.requires or {}).get("sdk")
    if not spec:
        out.append(f"{name}: pack.json 沒寫 requires.sdk（例如 \"sdk\": \">=0.2,<0.3\"；ADR-0001）")
    problem = synox_problem(m)
    if problem:
        out.append(f"{name}: {problem}")
    bundled = bundled_sdk_version(folder)
    if bundled is not None:
        if not bundled:
            out.append(f"{name}: 附的 {SDK_FILE} 找不到 SDK_VERSION")
        elif spec and sdk_problem(m, bundled):
            out.append(f"{name}: 附的 {SDK_FILE} 是 SDK {bundled}，不在 requires.sdk {spec} 範圍內（重新複製一份新的）")
    if m.name != name:
        out.append(f"{name}: 資料夾名跟 pack.json 的 name（{m.name}）不一樣")
    out += stage_problems(name, m.raw)
    out += scope_problems(name, m.raw)
    return out


def stage_problems(name: str, raw: dict) -> list:
    """工具庫的規矩：每個工具都要明寫 stage（SYNOX 那邊沒寫＝alpha，這裡不准省略，讓使用者在目錄就看得到）。
    值的檢查用 SYNOX 的 manifest.STAGES（同一份規則）；SYNOX 太舊、不認得 stage 就不打包。"""
    try:
        from synox.packs.manifest import STAGES
    except ImportError:
        return [f"{name}: 拿來打包的 SYNOX 太舊、不認得 stage（要含 pack.json stage 的版本）"]
    if "stage" not in raw:
        return [f"{name}: pack.json 沒寫 stage（{'、'.join(STAGES)} 其中一個；工具庫的每個工具都要明寫，意思見 README「穩定度」）"]
    if raw.get("stage") not in STAGES:
        return [f"{name}: stage 只能是 {'、'.join(STAGES)}（現在是 {raw.get('stage')!r}）"]
    return []


SCOPE_FIELDS = (("access", "誰看得到：本機工具 member、接外面服務的 owner"),
                ("surfaces", '在哪裡能用：["dm"]'),
                ("data", '資料屬於誰："user"（每人一份）'),
                ("storage", '落地怎麼存："vault"（密文）'),
                ("capabilities", '要什麼能力：沒有也要寫 []'))


def scope_problems(name: str, raw: dict) -> list:
    """工具庫的規矩（ADR-0003 入口標準）：access、surfaces、data、storage、capabilities 都要明寫。
    值對不對由 SYNOX 的第 1 關檢查（read(strict=True)）；這裡只擋「沒寫、靠預設值」。"""
    return [f"{name}: pack.json 沒寫 {key}（{why}；工具庫的每個工具都要明寫，見 docs/adr/0003）"
            for key, why in SCOPE_FIELDS if key not in raw]


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def entry_for(m, path: str, bundles: dict) -> dict:
    return {"name": m.name, "title": m.title, "summary": m.summary, "version": m.version, "stage": m.raw.get("stage"),
            "kind": m.kind,
            "group": m.group, "access": m.access, "capabilities": sorted(m.capabilities),
            "requires": {k: v for k, v in (m.requires or {}).items() if k in ("synox", "sdk") and v},
            "bundles": sorted(k for k, b in bundles.items() if m.name in b.get("packs", [])),
            "file": os.path.basename(path), "sha256": sha256_of(path), "size": os.path.getsize(path)}


def build(names=None, out_dir: str = None, check_only: bool = False, root: str = ROOT) -> dict:
    """打包 → {"index": index.json 的內容, "problems": [...]}；有問題就什麼都不寫。"""
    _synox.ensure()
    import synox
    from synox.packs.manifest import read
    from synox.sdk import author, zip_pack

    folders = _synox.pack_folders(os.path.join(root, "packs"))
    if names:
        known = {os.path.basename(f): f for f in folders}
        unknown = [n for n in names if n not in known]
        if unknown:
            return {"index": None, "problems": [f"沒有這個工具：{'、'.join(unknown)}"]}
        folders = [known[n] for n in names]
    catalog = load_catalog(root)
    bundles = catalog.get("bundles") or {}
    all_names = {os.path.basename(f) for f in _synox.pack_folders(os.path.join(root, "packs"))}
    problems = [f"catalog.json 的 {key} 列了不存在的工具 {n}" for key, b in bundles.items()
                for n in b.get("packs", []) if n not in all_names]
    for folder in folders:
        problems += problems_of(folder)
    if problems or check_only:
        return {"index": None, "problems": problems}
    out_dir = out_dir or os.path.join(root, "dist")
    entries = []
    for folder in folders:
        try:
            path = zip_pack(folder, out_dir)
        except ValueError as e:                      # 第 1、2 關沒過
            problems.append(f"{os.path.basename(folder)}: {e}")
            continue
        entries.append(entry_for(read(folder, strict=True), path, bundles))
    if problems:
        return {"index": None, "problems": problems}
    index = {"library": "SYNOX_Libs", "library_version": library_version(root),
             "built_with": {"synox": synox.__version__, "sdk": author.SDK_VERSION},
             "packs": entries, "bundles": bundles}
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    return {"index": index, "problems": []}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="工具 → .synoxpack ＋ index.json")
    parser.add_argument("names", nargs="*", help="只打這幾個（預設全部）")
    parser.add_argument("--out", default=os.path.join(ROOT, "dist"))
    parser.add_argument("--check", action="store_true", help="只檢查，不寫檔")
    args = parser.parse_args(argv)
    result = build(args.names or None, args.out, args.check)
    if result["problems"]:
        print("❌ 沒有打包：")
        for p in result["problems"]:
            print("  - " + p)
        return 2
    if args.check:
        print("✅ 檢查都過了")
        return 0
    for e in result["index"]["packs"]:
        print(f"{e['file']}  {e['stage']}  sdk {e['requires'].get('sdk', '?')}  sha256 {e['sha256'][:12]}…")
    print(os.path.join(args.out, "index.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
