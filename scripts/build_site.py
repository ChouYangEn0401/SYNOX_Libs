"""公開頁產生器：入口頁 docs/site/index.html（＋還沒有手寫說明頁的工具的簡單頁）從資料產生，新工具自動出現。

    python scripts/build_site.py                          產生（有變才寫，寫了就重新蓋頁面版本）
    python scripts/build_site.py --check                  只檢查：頁面是不是產生器現在的輸出（不是＝回 2）
    python scripts/build_site.py --list-republish         列出每一頁「本機檔 → 線上網址」（發佈後要重新發佈的）
    python scripts/build_site.py --list-republish --since origin/main   只列跟那個 git 版本比有改的頁

資料（版本只在 pack.json，這裡不寫任何版本）：
- packs/<名字>/pack.json：name、title（「emoji 名字」）、summary、version、stage、access、capabilities、group、help；
  選填的 `site` 區塊：{"emoji", "name", "line", "example", "status", "network"}（說明見 docs/CONTRIBUTING-TOOLS.md）；
- docs/site-tools.json：同樣的欄位，優先於 pack.json 的 site（不打包進 .synoxpack，改它不用升版）＋卡片順序 order；
- docs/artifacts.json：每一頁的線上網址（Artifact）；工具卡片連到它，沒有就連到 GitHub 上的 README；
- catalog.json（建議群組）、VERSION（發佈 tag）、costumes/（衣櫃幾套）。
樣板（給設計的人改）：scripts/site_index_template.html、scripts/site_tool_template.html。
  {{名字}}＝跳脫過的字，{{{名字}}}＝組好的 HTML；<!-- BEGIN:x -->…<!-- END:x --> 是重複用的片段；<!--# … --> 是只在樣板裡的註解。
docs/site/<名字>.html 已經有手寫的（沒有 GENERATED 那行註解）就不動；只有沒有頁、或頁是這支產生的，才（重新）產生。
下載連結都走 scripts/links.py（LINK_MODE），頁尾的線上網址與頁面版本照 scripts/site_stamp.py。只用標準函式庫，不連網。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
import links  # noqa: E402
import site_stamp  # noqa: E402

ROOT = links.ROOT
INDEX_TEMPLATE = os.path.join(SCRIPTS, "site_index_template.html")
TOOL_TEMPLATE = os.path.join(SCRIPTS, "site_tool_template.html")
GENERATED = "generated: scripts/build_site.py"
SITE_KEYS = ("emoji", "name", "line", "example", "status", "network")
INDEX_PAGE = "docs/site/index.html"
WARDROBE_PAGE = "docs/site/wardrobe.html"

STAGES = ("test", "alpha", "beta", "stable")
STAGE_LABEL = {"test": "🔧 test", "alpha": "🧪 alpha", "beta": "🚧 beta", "stable": "⭐ stable"}
STAGE_WORD = {"test": "測試中", "alpha": "早期版", "beta": "公測版", "stable": "穩定版"}
ACCESS = {   # (標籤, 滑過去的說明) —— 跟 SYNOX 網頁「工具庫（官方）」卡片同一份字（synox/packs/catalog.py 的 TEXT）
    "owner": ("👤 單人用", "只有安裝 SYNOX 的那個人能用；伺服器版的其他人看不到"),
    "member": ("👥 伺服器版也能用", "伺服器版裡每個人都能用，各自一份資料"),
}
SECTIONS = (("local", "存在這台電腦（不連網）"), ("online", "接外面的服務"))
NO_PAGE = "說明頁還沒寫"          # 沒有手寫說明頁（只有產生的簡單頁）
NOT_ONLINE = "說明頁還沒上線"     # 有手寫頁，但 docs/artifacts.json 還沒有它的網址


# ── 樣板 ──────────────────────────────────────────────────────────────

TEMPLATE_COMMENT = re.compile(r"<!--#.*?-->\n?", re.S)
BLOCK = re.compile(r"<!-- BEGIN:(\w+) -->\n?(.*?)<!-- END:\1 -->\n?", re.S)
PLACE = re.compile(r"\{\{\{(\w+)\}\}\}|\{\{(\w+)\}\}")


def load_template(path: str):
    """樣板 → (頁面本體, {片段名: 片段})。<!--# --> 註解拿掉、片段從本體拿掉。"""
    with open(path, encoding="utf-8") as f:
        text = TEMPLATE_COMMENT.sub("", f.read())
    blocks = {m.group(1): m.group(2) for m in BLOCK.finditer(text)}
    return BLOCK.sub("", text), blocks


def fill(text: str, data: dict, where: str = "樣板") -> str:
    """{{x}}＝html 跳脫、{{{x}}}＝原樣。樣板用了資料裡沒有的名字＝錯（打錯字馬上知道）。"""
    def sub(m):
        key = m.group(1) or m.group(2)
        if key not in data:
            raise KeyError(f"{where} 用了 {{{{{key}}}}}，但產生器沒有這個資料（可用：{'、'.join(sorted(data))}）")
        value = "" if data[key] is None else str(data[key])
        return value if m.group(1) else html.escape(value, quote=True)
    return PLACE.sub(sub, text)


# ── 資料 ──────────────────────────────────────────────────────────────

def _load_json(path: str, default):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def registry(root: str = ROOT) -> list:
    """docs/artifacts.json 的 pages（每一頁：title、url、local、tool、made_by）。"""
    return list(_load_json(os.path.join(root, "docs", "artifacts.json"), {}).get("pages") or [])


def online_urls(root: str = ROOT) -> dict:
    """{本機檔（docs/site/x.html）: 線上網址}。"""
    return {p["local"]: p["url"] for p in registry(root) if p.get("local") and p.get("url")}


def site_overrides(root: str = ROOT) -> dict:
    return _load_json(os.path.join(root, "docs", "site-tools.json"), {})


def split_title(title: str):
    """「📒 隨手記」→ ("📒", "隨手記")；開頭不是符號就沒有 emoji。"""
    head, _, rest = (title or "").strip().partition(" ")
    if rest and head and not any(ch.isalnum() for ch in head):
        return head, rest.strip()
    return "", (title or "").strip()


def is_hand_written(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as f:
        return GENERATED not in f.read()


def help_example(raw: dict) -> str:
    """help 區塊的第一個範例 → 「/群組 參數」。主要動作（add、或跟群組同名）＝/群組，其他＝/群組_指令（猜的；不對就寫 site.example）。"""
    block = raw.get("help") or {}
    commands = block.get("commands") or {}
    order = list(block.get("featured") or []) + [n for n in commands if n not in (block.get("featured") or [])]
    for cmd in order:
        examples = (commands.get(cmd) or {}).get("examples") or []
        if examples:
            text = examples[0]["text"] if isinstance(examples[0], dict) else str(examples[0])
            return f"{slash(raw, cmd)} {text}".strip()
    return ""


def slash(raw: dict, cmd: str) -> str:
    group = raw.get("group") or raw["name"]
    return f"/{group}" if cmd in ("add", group) else f"/{group}_{cmd}"


def tools(root: str = ROOT) -> list:
    """每個工具給頁面用的資料（照 docs/site-tools.json 的 order，再照 pack.json 的 order、名字）。"""
    over = site_overrides(root)
    urls = online_urls(root)
    catalog = _load_json(os.path.join(root, "catalog.json"), {}).get("bundles") or {}
    out = []
    for name in links.tool_names(root):
        with open(os.path.join(root, "packs", name, "pack.json"), encoding="utf-8") as f:
            raw = json.load(f)
        site = dict(raw.get("site") or {})
        site.update((over.get("tools") or {}).get(name) or {})
        unknown = sorted(set(site) - set(SITE_KEYS))
        if unknown:
            raise ValueError(f"{name}：site 不認得 {'、'.join(unknown)}（只能是 {'、'.join(SITE_KEYS)}）")
        emoji, title = split_title(raw.get("title", ""))
        caps = raw.get("capabilities") or []
        network = bool(site["network"]) if "network" in site else "network" in caps
        local = f"docs/site/{name}.html"
        hand = is_hand_written(os.path.join(root, local))
        out.append({
            "name": name, "version": raw["version"], "stage": raw.get("stage") or "alpha",
            "access": "owner" if raw.get("access", "owner") == "owner" else "member",
            "emoji": site.get("emoji") or emoji or "🧩", "title": site.get("name") or title or name,
            "full_title": raw.get("title") or name, "summary": raw.get("summary") or "",
            "line": site.get("line") or raw.get("summary") or "",
            "example": site["example"] if "example" in site else help_example(raw),
            "status": site.get("status") or "", "network": network,
            "bundles": [b.get("title", k).split("：")[0] for k, b in catalog.items() if name in (b.get("packs") or [])],
            "help": raw.get("help") or {}, "group": raw.get("group") or name, "raw": raw,
            "local": local, "hand_written": hand, "online_url": urls.get(local, ""),
            "order": raw.get("order", 1000),
        })
    order = list(over.get("order") or [])
    out.sort(key=lambda t: (order.index(t["name"]) if t["name"] in order else len(order), t["order"], t["name"]))
    return out


def readme_url(name: str) -> str:
    return f"{links.REPO_URL}/blob/main/packs/{name}/README.md"


def page_note(t: dict) -> str:
    if not t["hand_written"]:
        return NO_PAGE
    return "" if t["online_url"] else NOT_ONLINE


# ── 入口頁 ────────────────────────────────────────────────────────────

def _pill(blocks, text, tone="", hint=""):
    return fill(blocks["pill"], {"text": text, "tone": f" {tone}" if tone else "",
                                 "title_attr": f' title="{html.escape(hint, quote=True)}"' if hint else ""}, "pill")


def card_data(t: dict, blocks: dict) -> dict:
    pills = [_pill(blocks, STAGE_LABEL.get(t["stage"], t["stage"]), "stage")]
    if t["status"]:
        pills.append(_pill(blocks, t["status"]))
    elif not t["network"]:
        pills.append(_pill(blocks, "不連網", "ok"))
    else:
        pills.append(_pill(blocks, "要連網"))
    label, hint = ACCESS[t["access"]]
    pills.append(_pill(blocks, label, "", hint))
    if page_note(t):
        pills.append(_pill(blocks, page_note(t)))
    return {"href": t["online_url"] or readme_url(t["name"]), "emoji": t["emoji"], "title": t["title"],
            "name": t["name"], "version": t["version"], "line": t["line"],
            "example": fill(blocks["example"], {"text": t["example"]}, "example") if t["example"] else "",
            "pills": "".join(pills)}


def generated_note(template: str) -> str:
    return (f"<!-- {GENERATED}（樣板 {template}；資料 packs/*/pack.json、docs/site-tools.json、docs/artifacts.json）。"
            "不要手改這一頁：改樣板或資料，再跑 python scripts/build_site.py。 -->")


def index_data(all_tools: list, blocks: dict, root: str = ROOT) -> dict:
    urls = online_urls(root)
    sections = []
    for key, title in SECTIONS:
        group = [t for t in all_tools if ("online" if t["network"] else "local") == key]
        if group:
            cards = "".join(fill(blocks["card"], card_data(t, blocks), "card") for t in group).rstrip("\n")
            sections.append(fill(blocks["section"], {"title": title, "cards": cards}, "section"))
    present = {t["stage"] for t in all_tools}
    data = {
        "generated_note": generated_note("scripts/site_index_template.html"),
        "sections": "".join(sections).rstrip("\n"),
        "tool_count": len(all_tools),
        "online_count": sum(1 for t in all_tools if t["network"]),
        "online_names": "、".join(t["title"] for t in all_tools if t["network"]),
        "local_count": sum(1 for t in all_tools if not t["network"]),
        "costume_count": len(links._folders(os.path.join(root, "costumes"))),
        "tools_url": links.tools_url(root=root), "tools_zip": links.tools_zip(root),
        "release_url": links.release_page_url(root=root), "tag": links.release_tag(root),
        "wardrobe_url": urls.get(WARDROBE_PAGE, "wardrobe.html"),
        "online_url": urls.get(INDEX_PAGE, ""),
    }
    for stage in STAGES:
        data[f"now_{stage}"] = ' class="now"' if stage in present else ""
        data[f"all_here_{stage}"] = blocks["all_here"] if present == {stage} else ""
    return data


def index_html(root: str = ROOT) -> str:
    body, blocks = load_template(INDEX_TEMPLATE)
    return links.render(fill(body, index_data(tools(root), blocks, root), "site_index_template.html"), root=root)


# ── 工具的簡單頁（沒有手寫頁時）─────────────────────────────────────────

def tool_page_data(t: dict, blocks: dict, root: str = ROOT) -> dict:
    def badge(text, tone="", hint=""):
        return fill(blocks["badge"], {"text": text, "tone": f" {tone}" if tone else "",
                                      "title_attr": f' title="{html.escape(hint, quote=True)}"' if hint else ""}, "badge")
    badges = [badge(f"{STAGE_LABEL.get(t['stage'], t['stage'])} {STAGE_WORD.get(t['stage'], '')}".strip(), "stage")]
    if t["status"]:
        badges.append(badge(t["status"]))
    badges.append(badge("要連網") if t["network"] else badge("不連網", "good"))
    label, hint = ACCESS[t["access"]]
    badges.append(badge(label, "", hint))
    commands = t["help"].get("commands") or {}
    featured = list(t["help"].get("featured") or [])
    items = []
    for cmd in featured + [c for c in commands if c not in featured]:
        entry = commands.get(cmd) or {}
        examples = entry.get("examples") or []
        text = (examples[0]["text"] if isinstance(examples[0], dict) else str(examples[0])) if examples else ""
        items.append(fill(blocks["cmd"], {"example": f"{slash(t['raw'], cmd)} {text}".strip(),
                                          "plain": entry.get("plain", "")}, "cmd"))
    urls = online_urls(root)
    online = t["online_url"]
    version = t["version"]
    return {
        "generated_note": generated_note("scripts/site_tool_template.html"),
        "title": t["title"], "full_title": t["full_title"], "emoji": t["emoji"], "name": t["name"], "version": version,
        "bundle_meta": f" · 群組：{'、'.join(t['bundles'])}" if t["bundles"] else "",
        "summary": t["summary"], "badges": "\n      ".join(badges),
        "commands": fill(blocks["cmds"], {"items": "".join(items).rstrip("\n")}, "cmds") if items else "",
        "pack_file": links.pack_file(t["name"], version), "pack_url": links.pack_url(t["name"], root=root),
        "tools_url": links.tools_url(root=root), "tools_zip": links.tools_zip(root), "tag": links.release_tag(root),
        "readme_url": readme_url(t["name"]), "source_url": f"{links.REPO_URL}/tree/main/packs/{t['name']}",
        "overview_url": urls.get(INDEX_PAGE, "index.html"), "online_url": online,
        "online_link": (f'<a href="{html.escape(online, quote=True)}">{html.escape(online)}</a>' if online
                        else "還沒發佈（發佈後把網址寫進 docs/artifacts.json，再跑 python scripts/build_site.py）"),
    }


def tool_html(t: dict, root: str = ROOT) -> str:
    body, blocks = load_template(TOOL_TEMPLATE)
    return links.render(fill(body, tool_page_data(t, blocks, root), "site_tool_template.html"), root=root)


# ── 寫檔與檢查 ────────────────────────────────────────────────────────

def _blank(text: str) -> str:
    return site_stamp.STAMP.sub(r"\1\3", text)


def outputs(root: str = ROOT) -> dict:
    """{本機檔（相對 root）: 產生器現在的輸出（頁面版本留空）}。"""
    out = {INDEX_PAGE: index_html(root)}
    for t in tools(root):
        if not t["hand_written"]:
            out[t["local"]] = tool_html(t, root)
    return out


def orphans(root: str = ROOT) -> list:
    """產生過、但工具已經不在了的頁（docs/site/<名字>.html 有 GENERATED、不是入口頁、packs/ 沒有這個工具）。"""
    site = os.path.join(root, "docs", "site")
    names = set(links.tool_names(root))
    out = []
    for n in sorted(os.listdir(site)) if os.path.isdir(site) else []:
        rel = f"docs/site/{n}"
        if n.endswith(".html") and rel != INDEX_PAGE and n[:-5] not in names and not is_hand_written(os.path.join(site, n)):
            out.append(rel)
    return out


def check(root: str = ROOT) -> list:
    problems = []
    for rel, want in outputs(root).items():
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            problems.append(f"{rel}: 還沒產生（跑 python scripts/build_site.py）")
            continue
        with open(path, encoding="utf-8") as f:
            if _blank(f.read()) != _blank(want):
                problems.append(f"{rel}: 不是產生器現在的輸出（跑 python scripts/build_site.py；要改版面就改樣板，不要改這一頁）")
    problems += [f"{rel}: 工具已經不在了，這一頁是產生的（跑 python scripts/build_site.py 會刪掉）" for rel in orphans(root)]
    return problems


def write(root: str = ROOT) -> list:
    """產生每一頁；有變才寫、寫了重新蓋頁面版本。回有改到的檔（相對 root）。"""
    changed = []
    for rel, want in outputs(root).items():
        path = os.path.join(root, rel)
        old = ""
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                old = f.read()
        if _blank(old) == _blank(want):
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(want)
        site_stamp.stamp(path)
        changed.append(rel)
    for rel in orphans(root):
        os.remove(os.path.join(root, rel))
        changed.append(rel + "（刪掉）")
    return changed


def changed_since(ref: str, root: str = ROOT) -> set:
    """跟 git 的 ref 比有改的檔（含還沒 commit 的）；git 不能用就回 None（＝全部列）。"""
    try:
        diff = subprocess.run(["git", "diff", "--name-only", ref, "--", "docs/site"], cwd=root,
                              capture_output=True, text=True, check=True).stdout
        new = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--", "docs/site"], cwd=root,
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {line.strip() for line in (diff + new).splitlines() if line.strip()}


def republish_list(root: str = ROOT, since: str = None) -> list:
    """[(本機檔, 線上網址或 "")]：docs/site/ 的每一頁（since＝只列跟那個 git 版本比有改的）。"""
    urls = online_urls(root)
    site = os.path.join(root, "docs", "site")
    pages = [f"docs/site/{n}" for n in sorted(os.listdir(site)) if n.endswith(".html")] if os.path.isdir(site) else []
    only = changed_since(since, root) if since else None
    return [(rel, urls.get(rel, "")) for rel in pages if only is None or rel in only]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="產生公開頁（入口頁＋沒有手寫頁的工具頁）")
    parser.add_argument("--check", action="store_true", help="只檢查，不寫檔（過期回 2）")
    parser.add_argument("--list-republish", action="store_true", help="列出每一頁：本機檔 → 線上網址")
    parser.add_argument("--since", metavar="GIT_REF", help="跟 --list-republish 一起用：只列跟這個 git 版本比有改的頁")
    args = parser.parse_args(argv)
    if args.list_republish:
        rows = republish_list(since=args.since)
        for rel, url in rows:
            print(f"{rel} → {url or '（還沒有網址：用 Artifact 第一次發佈，網址寫進 docs/artifacts.json，再跑一次 build_site.py）'}")
        if not rows:
            print("沒有要重新發佈的頁")
        return 0
    if args.check:
        problems = check()
        for p in problems:
            print("  - " + p)
        print("❌ 公開頁不是最新的" if problems else "✅ 公開頁是產生器現在的輸出")
        return 2 if problems else 0
    changed = write()
    for rel in changed:
        print("產生：" + rel)
    if not changed:
        print("公開頁沒有變")
    return 0


if __name__ == "__main__":
    sys.exit(main())
