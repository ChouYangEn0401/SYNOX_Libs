"""下載連結的唯一來源（docs/RELEASING.md）：每個公開頁、README、衣櫃頁的下載網址都從這裡算，版本只看 pack.json 與 VERSION。

    python scripts/links.py            把 docs/site/*.html 與 README.md 的下載連結改成現在的版本，有改的頁重新蓋頁面版本
    python scripts/links.py --check    只檢查，有過期的連結就列出來、回 2（tests/test_links.py 也跑同一份）

發佈頁上每個工具有兩份同內容的檔（scripts/build.py 都會產生）：
- 帶版本的 `<name>-<version>.synoxpack`（例：money-0.3.1.synoxpack）、`tools-<VERSION>.zip`
- 不帶版本的別名 `<name>.synoxpack`、`tools.zip`——配 GitHub 的 `releases/latest/download/<檔名>`，網址永遠不用改

LINK_MODE（只改這一個常數，再跑一次這支）：
- "tag"（現在）：`releases/download/v<VERSION>/<name>-<version>.synoxpack`。v0.2.0 是 pre-release，
  GitHub 的 `releases/latest` 不會指到 pre-release，所以先用帶版本的檔名、掛在現在的 tag 底下。
- "latest"：`releases/latest/download/<name>.synoxpack`（別名）。擁有者把發佈頁標成 Latest（不是 pre-release）之後才切。
只用標準函式庫，不連網。
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "docs", "site")
REPO_URL = "https://github.com/ChouYangEn0401/SYNOX_Libs"
LINK_MODE = "tag"                     # "tag" | "latest"（見上面）
MODES = ("tag", "latest")

PACK_EXT = ".synoxpack"
TOOLS_ALIAS = "tools.zip"
SUMS_FILE = "SHA256SUMS"
INDEX_FILE = "index.json"
VER = r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?"


# ── 版本（只讀 pack.json 與 VERSION）────────────────────────────────────

def library_version(root: str = ROOT) -> str:
    with open(os.path.join(root, "VERSION"), encoding="utf-8") as f:
        return f.read().strip()


def release_tag(root: str = ROOT) -> str:
    return "v" + library_version(root)


def _folders(parent: str) -> list:
    if not os.path.isdir(parent):
        return []
    return [os.path.join(parent, n) for n in sorted(os.listdir(parent))
            if not n.startswith("_") and os.path.isfile(os.path.join(parent, n, "pack.json"))]


def current_versions(root: str = ROOT) -> dict:
    """{name: version}：packs/ 的工具＋costumes/ 的外觀，照 pack.json。"""
    out = {}
    for folder in _folders(os.path.join(root, "packs")) + _folders(os.path.join(root, "costumes")):
        with open(os.path.join(folder, "pack.json"), encoding="utf-8") as f:
            raw = json.load(f)
        out[raw["name"]] = raw["version"]
    return out


def tool_names(root: str = ROOT) -> list:
    return [os.path.basename(f) for f in _folders(os.path.join(root, "packs"))]


# ── 檔名與網址 ─────────────────────────────────────────────────────────

def pack_file(name: str, version: str) -> str:
    return f"{name}-{version}{PACK_EXT}"


def alias_file(name: str) -> str:
    return f"{name}{PACK_EXT}"


def tools_zip(root: str = ROOT) -> str:
    return f"tools-{library_version(root)}.zip"


def expected_assets(root: str = ROOT) -> set:
    """scripts/build.py 全部打包時 dist/ 會有的每個檔（＝發佈頁該有的資產）。"""
    names = {INDEX_FILE, SUMS_FILE, tools_zip(root), TOOLS_ALIAS}
    for name, version in current_versions(root).items():
        names |= {pack_file(name, version), alias_file(name)}
    return names


def asset_url(file: str, mode: str = None, root: str = ROOT) -> str:
    mode = mode or LINK_MODE
    if mode == "latest":
        return f"{REPO_URL}/releases/latest/download/{file}"
    return f"{REPO_URL}/releases/download/{release_tag(root)}/{file}"


def pack_url(name: str, mode: str = None, root: str = ROOT) -> str:
    """一個工具（或外觀）的下載網址：tag 模式用帶版本的檔名，latest 模式用別名。"""
    mode = mode or LINK_MODE
    if mode == "latest":
        return asset_url(alias_file(name), mode, root)
    return asset_url(pack_file(name, current_versions(root)[name]), mode, root)


def tools_url(mode: str = None, root: str = ROOT) -> str:
    mode = mode or LINK_MODE
    return asset_url(TOOLS_ALIAS if mode == "latest" else tools_zip(root), mode, root)


def release_page_url(mode: str = None, root: str = ROOT) -> str:
    mode = mode or LINK_MODE
    if mode == "latest":
        return f"{REPO_URL}/releases/latest"
    return f"{REPO_URL}/releases/tag/{release_tag(root)}"


# ── 改寫頁面：任何寫死的舊版本連結 → 現在的版本 ──────────────────────

_REPO = r"https://github\.com/ChouYangEn0401/SYNOX_Libs"
_PACK_IN = r"([a-z0-9]+(?:-[a-z0-9]+)*?)"
URL_PACK_VERSIONED = re.compile(_REPO + r"/releases/(?:download/v" + VER + r"|latest/download)/" + _PACK_IN + "-(" + VER + r")\.synoxpack", re.I)
URL_PACK_ALIAS = re.compile(_REPO + r"/releases/(?:download/v" + VER + r"|latest/download)/" + _PACK_IN + r"\.synoxpack", re.I)
URL_PACK_RAW = re.compile(_REPO + r"/raw/[^/\"'\s]+/docs/site/packs/" + _PACK_IN + "-(" + VER + r")\.synoxpack", re.I)
URL_TOOLS = re.compile(_REPO + r"/releases/(?:download/v" + VER + r"|latest/download)/tools(?:-" + VER + r")?\.zip", re.I)
URL_RELEASE_PAGE = re.compile(_REPO + r"/releases/(?:tag/v" + VER + r"|latest)(?=[\"'\s)<])", re.I)
ANY_ASSET_URL = re.compile(_REPO + r"/(?:releases/(?:download/(v" + VER + r")|latest/download)|raw/[^\"'\s)<]+)/([^\"'\s)<>]+)", re.I)
URL_SUMS = re.compile(_REPO + r"/releases/(?:download/v" + VER + r"|latest/download)/SHA256SUMS(?![\w.-])", re.I)
TEXT_PACK_FILE = re.compile(r"(?<![\w/.-])" + _PACK_IN + "-(" + VER + r")\.synoxpack")
TEXT_TOOLS_ZIP = re.compile(r"(?<![\w/.-])tools-(" + VER + r")\.zip")
TEXT_RELEASE_LABEL = re.compile(r"(發佈頁 )v" + VER)
TEXT_LIBRARY_LABEL = re.compile(r"(SYNOX 工具庫 )v" + VER)


def _meta_re(names) -> re.Pattern:
    alt = "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))
    return re.compile(r"(?<![\w-])(" + alt + r") · v(" + VER + r")")


def render(text: str, mode: str = None, root: str = ROOT) -> str:
    """把一頁（HTML 或 Markdown）的下載連結、檔名、版本字樣都換成現在的；不認得的工具名不動（check 會抓）。"""
    mode = mode or LINK_MODE
    versions = current_versions(root)

    def pack_link(m):
        return pack_url(m.group(1), mode, root) if m.group(1) in versions else m.group(0)

    text = URL_PACK_RAW.sub(pack_link, text)
    text = URL_PACK_VERSIONED.sub(pack_link, text)
    text = URL_PACK_ALIAS.sub(pack_link, text)
    text = URL_TOOLS.sub(lambda m: tools_url(mode, root), text)
    text = URL_SUMS.sub(lambda m: asset_url("SHA256SUMS", mode, root), text)
    text = URL_RELEASE_PAGE.sub(lambda m: release_page_url(mode, root), text)

    def file_text(m):
        # 網址裡的檔名上面已經處理好了；這裡只改看得到的字（<code>money-0.3.1.synoxpack</code>）
        return pack_file(m.group(1), versions[m.group(1)]) if m.group(1) in versions else m.group(0)

    text = TEXT_PACK_FILE.sub(file_text, text)
    text = TEXT_TOOLS_ZIP.sub(lambda m: tools_zip(root), text)
    text = TEXT_RELEASE_LABEL.sub(lambda m: m.group(1) + release_tag(root), text)
    text = TEXT_LIBRARY_LABEL.sub(lambda m: m.group(1) + release_tag(root), text)
    if versions:
        text = _meta_re(versions).sub(lambda m: f"{m.group(1)} · v{versions[m.group(1)]}", text)
    return text


def problems_in(label: str, text: str, mode: str = None, root: str = ROOT) -> list:
    """一頁裡的問題：連到別的 tag／別的版本、連到 build 不會產生的檔、raw 分支連結、頁面沒照 render 更新。"""
    mode = mode or LINK_MODE
    tag = release_tag(root)
    assets = expected_assets(root)
    versions = current_versions(root)
    out = []
    for m in ANY_ASSET_URL.finditer(text):
        url, url_tag, file = m.group(0), m.group(1), m.group(2)
        if "/raw/" in url:
            if file.endswith(PACK_EXT) or file.endswith(".zip"):
                out.append(f"{label}: 下載連結指到 repo 分支的檔，不是發佈頁：{url}")
            continue
        if mode == "tag" and url_tag != tag:
            out.append(f"{label}: 連到 {url_tag or 'latest'}，現在的發佈是 {tag}（LINK_MODE=tag）：{url}")
        if mode == "latest" and url_tag:
            out.append(f"{label}: LINK_MODE=latest 但連到固定的 {url_tag}：{url}")
        if file not in assets:
            out.append(f"{label}: 連到 build 不會產生的檔 {file}：{url}")
    for m in TEXT_PACK_FILE.finditer(text):
        name, ver = m.group(1), m.group(2)
        if name in versions and versions[name] != ver:
            out.append(f"{label}: 寫的是 {name} {ver}，pack.json 是 {versions[name]}")
    for m in TEXT_TOOLS_ZIP.finditer(text):
        if f"tools-{m.group(1)}.zip" != tools_zip(root):
            out.append(f"{label}: tools-{m.group(1)}.zip 過期，現在是 {tools_zip(root)}")
    if render(text, mode, root) != text:
        out.append(f"{label}: 下載連結或版本字樣沒更新（跑 python scripts/links.py）")
    return out


def public_pages(root: str = ROOT) -> list:
    """會發佈出去的頁：docs/site/*.html（含衣櫃頁）＋ README.md。"""
    site = os.path.join(root, "docs", "site")
    pages = [os.path.join(site, n) for n in sorted(os.listdir(site)) if n.endswith(".html")] if os.path.isdir(site) else []
    return pages + [os.path.join(root, "README.md")]


def check(root: str = ROOT, mode: str = None) -> list:
    out = []
    for path in public_pages(root):
        with open(path, encoding="utf-8") as f:
            out += problems_in(os.path.relpath(path, root), f.read(), mode, root)
    return out


def refresh(root: str = ROOT, mode: str = None, restamp: bool = True) -> list:
    """改寫每一頁；回有改到的檔。docs/site/ 的頁有改就重新蓋頁面版本（scripts/site_stamp.py）。"""
    changed = []
    for path in public_pages(root):
        with open(path, encoding="utf-8") as f:
            old = f.read()
        new = render(old, mode, root)
        if new != old:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new)
            changed.append(path)
    if restamp:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import site_stamp
        for path in changed:
            if path.endswith(".html"):
                site_stamp.stamp(path)
    return changed


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        problems = check()
        for p in problems:
            print("  - " + p)
        print("❌ 有過期的下載連結" if problems else f"✅ 下載連結都是最新的（LINK_MODE={LINK_MODE}）")
        return 2 if problems else 0
    for path in refresh():
        print("更新：" + os.path.relpath(path, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
