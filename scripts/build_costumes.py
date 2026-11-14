"""SYNOX 衣櫃（wardrobe）：26 套外觀（costume）的產生器、打包、公開頁。

三個步驟，同一支腳本：

    python scripts/build_costumes.py extract --brand <品牌提案頁.html>   從品牌提案頁產生 costumes/<id>/（原始檔，進 repo）
    python scripts/build_costumes.py pack [--out dist]                  costumes/<id>/ → <out>/<id>-<版本>.synoxpack（只用標準函式庫）
    python scripts/build_costumes.py page                               costumes/ → docs/site/wardrobe.html（之後跑 site_stamp.py）
    python scripts/build_costumes.py check                              只檢查（不寫檔）：每一套都過「不帶程式」的規則

- extract 要 Playwright（Chromium）和 fontTools，而且要連網一次（Google Fonts 的字型，只拿來把字標轉成向量路徑，
  字型檔不進 repo、不進 pack）。品牌提案頁本身不進 repo；它是一頁自己帶全部東西的 HTML，每個系列是陣列 S 的一個物件，
  吉祥物、開場、App 圖示都由頁面裡的函式畫出來。這裡把那支 IIFE 打開一個口（window.__B），在瀏覽器裡呼叫它們，
  再把開場（HTML 排版＋CSS 動畫）量好位置、轉成純 SVG。
- pack 與 check 只用標準函式庫；scripts/build.py 打包工具時也會順便打包衣櫃、把它們寫進同一份 index.json
  （kind=theme、category「衣櫃／外觀」）。
- 格式＝SYNOX 的外觀包（kind=theme，SYNOX 的 docs/themes.md、src/synox/theme/）合約第 1 版：
  pack.json 有 summary、theme.contract=1、theme.tokens（light／dark 都給齊核心 12 色）、theme.slots（7 個 SVG）、
  theme.css、fonts（空的：不附字型）。SYNOX 安裝時把每個檔消毒（白名單重建），所以這裡產生的東西要「消毒前後一樣」：
  · theme.css：:root 只設 --th- 開頭的變數（這一套自己多用的顏色）；其他規則都在 [data-slot] 底下；
    @keyframes 用 th-<字母>- 開頭；沒有 !important（減少動態由 SYNOX 網頁自己全域關掉）。
  · SVG：沒有 style="…"（SYNOX 的 CSP 不收 inline style）：transform-origin 寫成屬性，--i 這種變數換成 class＋theme.css 的規則；
    只用 SYNOX 白名單裡的濾鏡（沒有 feTurbulence）。id 用 c<字母>- 開頭（SYNOX 會再加 ts-<slot>- 前綴，url(#…) 一起改）。
  SYNOX 那邊的實作之後如果又不一樣，改這支腳本、重新產生就好（costumes/ 不要手改）。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import links  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COSTUMES = os.path.join(ROOT, "costumes")
SITE = os.path.join(ROOT, "docs", "site")
REPO_URL = links.REPO_URL
ONLINE_URL = "https://claude.ai/artifact/USzGNERZ2477E7XzPaPCsR"           # 公開頁的線上版（Artifact）
COLLECTION = {"id": "wardrobe", "title": "SYNOX 衣櫃", "category": "衣櫃／外觀",
              "summary": "26 套外觀：整個聊天介面的顏色、吉祥物、開場一起換。不帶任何程式，換回預設隨時可以。"}
VERSION = "1.0.0"
SLOTS = ("mascot.idle", "mascot.think", "mascot.happy", "mascot.warn", "opening", "wordmark", "appicon")
SLOT_FILES = {"mascot.idle": "slots/idle.svg", "mascot.think": "slots/think.svg", "mascot.happy": "slots/happy.svg",
              "mascot.warn": "slots/warn.svg", "opening": "slots/opening.svg", "wordmark": "slots/wordmark.svg",
              "appicon": "slots/appicon.svg"}
TOKENS = ("--bg", "--surface", "--text", "--muted", "--line", "--accent", "--accent-ink", "--accent2", "--soft",
          "--ok", "--warn", "--bad", "--radius", "--font-ui", "--font-brand")
OPTIONAL_TOKENS = ("--m1", "--m2", "--mi", "--mb")
# 品牌頁的 L／D 鍵 → token 名
KEYMAP = {"bg": "--bg", "surf": "--surface", "text": "--text", "muted": "--muted", "line": "--line", "ac": "--accent",
          "acink": "--accent-ink", "ac2": "--accent2", "soft": "--soft", "ok": "--ok", "warn": "--warn", "bad": "--bad",
          "m1": "--m1", "m2": "--m2", "mi": "--mi", "mb": "--mb"}
# 品牌頁的變數名 → token 名（SVG 與 CSS 裡的 var(--x) 一起改）
RENAME = {"surf": "surface", "ac": "accent", "ac2": "accent2", "acink": "accent-ink", "rad": "radius", "uif": "font-ui"}
SLUGS = {"A": "bubble", "B": "lantern", "C": "stamp", "D": "snail", "E": "cursor", "F": "mochi", "G": "blackdog",
         "H": "teapot", "I": "marquee", "J": "tether", "K": "pulse", "L": "blocks", "M": "bell", "N": "gecko",
         "O": "puppy", "P": "lock", "Q": "sticky", "R": "bento", "S": "pangolin", "T": "magpie", "U": "mountain",
         "V": "kintsugi", "W": "pixel", "X": "circle", "Y": "clay", "Z": "cat"}
CONTRACT = 1                          # SYNOX 外觀合約的版本（pack.json 的 theme.contract）
PUBLISHER = "SYNOX 衣櫃"
STAGE = "alpha"                       # 跟 index.json 寫的一樣
MAX_PACK_BYTES = 300 * 1024           # 一套外觀的 .synoxpack 上限（SYNOX 上傳上限是 5 MB；這裡刻意小很多）
PACKED = ("pack.json", "theme.css") + tuple(SLOT_FILES.values())
SVG_NS = ('xmlns="http://www.w3.org/2000/svg"', 'xmlns:xlink="http://www.w3.org/1999/xlink"')
MAX_SLOT_BYTES = 64 * 1024            # SYNOX：一個 slot 檔、theme.css 各 64 KB
ROOT_SELECTORS = (':root', ':root[data-theme="light"]', ':root[data-theme="dark"]', ':root:not([data-theme="light"])',
                  ':root:not([data-theme="dark"])')
# SYNOX 白名單裡的 SVG 濾鏡（沒列的會被消毒拿掉）
FILTER_PRIMITIVES = ("feGaussianBlur", "feOffset", "feMerge", "feMergeNode", "feFlood", "feComposite", "feBlend",
                     "feColorMatrix", "feDropShadow", "feMorphology")


def keyframe_prefix(letter: str) -> str:
    return f"th-{letter.lower()}-"


def costume_id(letter: str) -> str:
    return f"costume-{letter.lower()}-{SLUGS[letter]}"


def library_version() -> str:
    return links.library_version(ROOT)


def release_tag() -> str:
    return links.release_tag(ROOT)


def download_url(file: str) -> str:
    """公開頁的下載網址：跟工具同一個規則（scripts/links.py 的 LINK_MODE；docs/RELEASING.md）。
    file 是帶版本的檔名（costume-a-bubble-1.0.0.synoxpack）；latest 模式自動換成別名。"""
    m = re.fullmatch(r"(.+?)-(\d+\.\d+\.\d+[^/]*)\.synoxpack", file)
    if m and links.LINK_MODE == "latest":
        return links.asset_url(links.alias_file(m.group(1)))
    return links.asset_url(file)


# ── 規則：不帶程式（check、pack、測試都用這一份）────────────────────────

FORBIDDEN = [
    (re.compile(r"<\s*script", re.I), "<script>"),
    (re.compile(r"""[\s"'/]on[a-z]+\s*=""", re.I), "on…= 事件屬性"),
    (re.compile(r"javascript\s*:", re.I), "javascript:"),
    (re.compile(r"https?:|//[a-z0-9.-]+\.[a-z]{2,}/", re.I), "外部網址"),
    (re.compile(r"@import", re.I), "@import"),
    (re.compile(r"foreignObject", re.I), "foreignObject"),
    (re.compile(r"<\s*(iframe|object|embed|image|img|use|a|style|link|audio|video)[\s>/]", re.I), "不准的元素"),
    (re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.I), "DOCTYPE／ENTITY"),
    (re.compile(r"data\s*:", re.I), "data: 網址"),
    (re.compile(r"expression\s*\(|behavior\s*:|-moz-binding", re.I), "舊 IE 的 CSS 程式"),
    (re.compile(r"url\(\s*(?!['\"]?#)", re.I), "url() 只能指向同一份檔裡的 #id"),
    (re.compile(r"(?:xlink:)?href\s*=\s*['\"](?!#)", re.I), "href 只能指向同一份檔裡的 #id"),
]


def code_problems(name: str, text: str) -> list:
    """一個檔的「不帶程式」檢查；SVG 的命名空間（xmlns）是唯一允許出現的 http。"""
    for ns in SVG_NS:
        text = text.replace(ns, "")
    return [f"{name}: 有 {what}" for rx, what in FORBIDDEN if rx.search(text)]


def _css_media_blocks(css: str, media: str = ""):
    """(外面包的 @media 條件或 "", 前綴, 內容)：一層一層拆開，@media 往裡拆；@keyframes 回 ("@keyframes 名字", 內容)。"""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    i, n = 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0:
            break
        head = css[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            k += 1
        body = css[j + 1:k - 1]
        if head.startswith("@media"):
            cond = head[len("@media"):].strip()
            yield from _css_media_blocks(body, f"{media} and {cond}" if media else cond)
        else:
            yield media, head, body
        i = k


def _css_blocks(css: str):
    """(前綴, 內容)：跟 _css_media_blocks 一樣，不管外面的 @media。"""
    for _media, head, body in _css_media_blocks(css):
        yield head, body


ANIM_WORDS = {"none", "infinite", "normal", "reverse", "alternate", "alternate-reverse", "forwards", "backwards", "both",
              "running", "paused", "ease", "ease-in", "ease-out", "ease-in-out", "linear", "step-start", "step-end",
              "auto", "initial", "inherit", "unset"}


def css_problems(name: str, css: str, letter: str) -> list:
    out = []
    prefix = keyframe_prefix(letter)
    if "!important" in css.replace(" ", "").lower():
        out.append(f"{name}: 有 !important（SYNOX 不收）")
    defined = set(re.findall(r"@keyframes\s+([\w-]+)", css))
    for head, body in _css_blocks(css):
        if head.startswith("@keyframes"):
            kf = head.split(None, 1)[1].strip()
            if not kf.startswith(prefix):
                out.append(f"{name}: keyframes {kf} 要用 {prefix} 開頭")
            continue
        if head.startswith("@"):
            out.append(f"{name}: 不准的 at-rule {head[:40]}")
            continue
        sels = [" ".join(s.split()) for s in head.split(",")]
        if all(s in ROOT_SELECTORS for s in sels):
            for k, _v in decls_of(body):
                if not k.startswith("--th-"):
                    out.append(f"{name}: :root 只能設 --th- 開頭的變數（{k}）")
            continue
        for sel in sels:
            if "[data-slot]" not in sel and ".theme-slot-" not in sel:
                out.append(f"{name}: 規則 {sel.strip()[:60]} 沒有限定在 [data-slot] 底下")
        for m in re.finditer(r"animation(?:-name)?\s*:([^;]*)", body):
            for part in m.group(1).split(","):
                for tok in part.split():
                    if re.match(r"^[A-Za-z_][\w-]*$", tok) and tok not in ANIM_WORDS and tok not in defined:
                        out.append(f"{name}: 動畫 {tok} 沒有對應的 @keyframes（要用 {prefix} 開頭）")
    media = re.findall(r"@media\s*([^{]*)\{", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    for m in media:
        if "prefers-color-scheme" not in m and "prefers-reduced-motion" not in m:
            out.append(f"{name}: @media {m.strip()} 不准（只收深淺色、減少動態）")
    return out


def pack_problems(folder: str) -> list:
    """costumes/<id>/ 的全部檢查：pack.json 的格式、7 個 slot、light＋dark 的 token、不帶程式、大小。"""
    name = os.path.basename(folder)
    try:
        with open(os.path.join(folder, "pack.json"), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        return [f"{name}: pack.json 讀不了：{e}"]
    out = []
    m = re.match(r"^costume-([a-z])-[a-z0-9]+$", name)
    if not m:
        return [f"{name}: 資料夾名要是 costume-<字母>-<名字>"]
    letter = m.group(1).upper()
    if raw.get("name") != name:
        out.append(f"{name}: pack.json 的 name 跟資料夾不一樣")
    if raw.get("kind") != "theme":
        out.append(f"{name}: kind 要是 theme")
    if not re.match(r"^\d+\.\d+\.\d+$", str(raw.get("version", ""))):
        out.append(f"{name}: version 要是 x.y.z")
    for key in ("title", "summary"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            out.append(f"{name}: 要寫 {key}")
    theme = raw.get("theme") or {}
    if theme.get("contract") != CONTRACT:
        out.append(f"{name}: theme.contract 要是 {CONTRACT}（SYNOX 外觀合約的版本）")
    tokens = theme.get("tokens") or {}
    for mode in ("light", "dark"):
        got = tokens.get(mode) or {}
        missing = [t for t in TOKENS if not str(got.get(t, "")).strip()]
        if missing:
            out.append(f"{name}: {mode} 少了 token {' '.join(missing)}")
        extra = [t for t in got if t not in TOKENS + OPTIONAL_TOKENS]
        if extra:
            out.append(f"{name}: {mode} 有不認得的 token {' '.join(extra)}")
        for t, v in got.items():
            out += code_problems(f"{name}: token {t}", str(v))
            if re.search(r"[;{}<>]", str(v)):
                out.append(f"{name}: token {t} 的值有 ; {{ }} < >")
    slots = theme.get("slots") or {}
    if set(slots) != set(SLOTS):
        out.append(f"{name}: slots 要剛好是 {' '.join(SLOTS)}")
    files = list(slots.values()) + [theme.get("css") or "theme.css"]
    for rel in files:
        if not isinstance(rel, str) or rel.startswith("/") or ".." in rel.split("/"):
            out.append(f"{name}: 檔名 {rel!r} 不對")
            continue
        path = os.path.join(folder, *rel.split("/"))
        if not os.path.isfile(path):
            out.append(f"{name}: 少了 {rel}")
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        out += code_problems(f"{name}/{rel}", text)
        if rel.endswith(".svg"):
            if not text.lstrip().startswith("<svg") or 'xmlns="http://www.w3.org/2000/svg"' not in text.split(">", 1)[0]:
                out.append(f"{name}/{rel}: 要是 <svg xmlns=…> 開頭的單一 SVG")
            for i in re.findall(r'\sid="([^"]+)"', text):
                if not i.startswith(f"c{letter.lower()}-"):
                    out.append(f"{name}/{rel}: id {i} 要用 c{letter.lower()}- 開頭（多個 SVG 放在同一頁時才不會撞）")
            if re.search(r"\sstyle\s*=", text):
                out.append(f"{name}/{rel}: 有 style=\"…\"（SYNOX 的網頁不收 inline style：寫成屬性或 class）")
            for el in sorted(set(re.findall(r"<(fe[A-Za-z]+)", text)) - set(FILTER_PRIMITIVES)):
                out.append(f"{name}/{rel}: 有 <{el}>（SYNOX 的白名單沒有這個濾鏡）")
            if len(text.encode("utf-8")) > MAX_SLOT_BYTES:
                out.append(f"{name}/{rel}: 太大（一個 slot 最多 {MAX_SLOT_BYTES // 1024} KB）")
        elif rel.endswith(".css"):
            out += css_problems(f"{name}/{rel}", text, letter)
            if len(text.encode("utf-8")) > MAX_SLOT_BYTES:
                out.append(f"{name}/{rel}: 太大（theme.css 最多 {MAX_SLOT_BYTES // 1024} KB）")
    if theme.get("fonts"):
        out.append(f"{name}: 衣櫃的外觀不附字型（fonts 要是空的）")
    size = sum(os.path.getsize(os.path.join(folder, *p.split("/"))) for p in PACKED if os.path.isfile(os.path.join(folder, *p.split("/"))))
    if size > MAX_PACK_BYTES:
        out.append(f"{name}: 太大（{size} bytes，上限 {MAX_PACK_BYTES}）")
    return out


def costume_folders() -> list:
    if not os.path.isdir(COSTUMES):
        return []
    return sorted(os.path.join(COSTUMES, d) for d in os.listdir(COSTUMES)
                  if d.startswith("costume-") and os.path.isfile(os.path.join(COSTUMES, d, "pack.json")))


# ── 打包（只用標準函式庫）────────────────────────────────────────────

def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def zip_costume(folder: str, out_dir: str) -> str:
    """跟 SYNOX 的 zip_pack 同一個格式：pack.json 在最上層、多一段 integrity.sha256（每個檔的雜湊）。
    只放 PACKED 列的檔（about.json 是給公開頁用的，不進 pack）。zip 的時間固定，同樣的內容打出一樣的檔。"""
    with open(os.path.join(folder, "pack.json"), encoding="utf-8") as f:
        raw = json.load(f)
    hashes = {}
    for rel in PACKED[1:]:
        with open(os.path.join(folder, *rel.split("/")), "rb") as f:
            hashes[rel] = sha256_bytes(f.read())
    raw["integrity"] = {"sha256": dict(sorted(hashes.items()))}
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, f"{raw['name']}-{raw['version']}.synoxpack")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        def put(name, data):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
        put("pack.json", json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
        for rel in sorted(hashes):
            with open(os.path.join(folder, *rel.split("/")), "rb") as f:
                put(rel, f.read())
    with open(target, "wb") as f:
        f.write(buf.getvalue())
    return target


def about(folder: str) -> dict:
    path = os.path.join(folder, "about.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def index_entry(folder: str, path: str) -> dict:
    """index.json 的一筆（跟工具同一個清單；SYNOX 的「功能與安裝」靠 kind／category 把它們放在「衣櫃／外觀」）。"""
    with open(os.path.join(folder, "pack.json"), encoding="utf-8") as f:
        raw = json.load(f)
    info = about(folder)
    with open(path, "rb") as f:
        blob = f.read()
    return {"name": raw["name"], "title": raw["title"], "summary": raw["summary"], "version": raw["version"],
            "stage": raw.get("stage", STAGE), "kind": "theme", "category": COLLECTION["category"], "collection": COLLECTION["id"],
            "series": info.get("letter", ""), "line": info.get("one", ""),
            "group": "", "access": "owner", "capabilities": [], "requires": {}, "bundles": [],
            "file": os.path.basename(path), "sha256": sha256_bytes(blob), "size": len(blob)}


def pack_all(out_dir: str) -> dict:
    """costumes/ → out_dir/*.synoxpack；回 {"entries": [...], "problems": [...]}（有問題就什麼都不寫）。"""
    folders = costume_folders()
    problems = [p for f in folders for p in pack_problems(f)]
    if problems:
        return {"entries": [], "problems": problems}
    entries = []
    for folder in folders:
        path = zip_costume(folder, out_dir)
        size = os.path.getsize(path)
        if size > MAX_PACK_BYTES:
            problems.append(f"{os.path.basename(folder)}: 打包後 {size} bytes，超過上限")
        entries.append(index_entry(folder, path))
    return {"entries": entries, "problems": problems}


# ── 公開頁 docs/site/wardrobe.html ─────────────────────────────────────

def _scope_css(css: str, scope: str) -> str:
    """theme.css 原本假設整頁只有一套外觀；公開頁同時放 26 套，所以每條規則多限定在 .<scope> 底下。
    :root 那幾段（--th- 變數）不搬：公開頁用 about.json 的 extras 在 .<scope> 上設（26 套的同名變數才不會互蓋）。"""
    out = []
    for media, head, body in _css_media_blocks(css):
        if head.startswith("@keyframes"):
            out.append(f"{head}{{{body}}}")
            continue
        if all(" ".join(s.split()) in ROOT_SELECTORS for s in head.split(",")):
            continue
        sels = ",".join(s.replace("[data-slot]", f".{scope} [data-slot]") for s in head.split(","))
        rule = f"{sels}{{{body}}}"
        out.append(f"@media {media}{{{rule}}}" if media else rule)
    return "\n".join(out)


def _svg_inline(text: str, scope_letter: str, n: int) -> str:
    """SVG 檔 → 放進頁面的 inline SVG（拿掉 xmlns、id 加上第幾份，同一頁放好幾份時不撞）。"""
    text = text.strip()
    text = re.sub(r'\sid="(c[a-z]-[^"]+)"', lambda m: f' id="{m.group(1)}-{n}"', text)
    text = re.sub(r"url\(#(c[a-z]-[^)]+)\)", lambda m: f"url(#{m.group(1)}-{n})", text)
    return text


def page_html() -> str:
    folders = costume_folders()
    tag = release_tag()
    cards, tokens_css, theme_css, nav = [], [], [], []
    for n, folder in enumerate(folders):
        with open(os.path.join(folder, "pack.json"), encoding="utf-8") as f:
            raw = json.load(f)
        info = about(folder)
        letter = info["letter"]
        scope = f"cw-{letter.lower()}"
        toks = raw["theme"]["tokens"]

        def block(d):
            return ";".join(f"{k}:{v}" for k, v in d.items() if k not in ("--font-ui", "--font-brand"))
        extras_l = ";".join(f"--th-{k}:{v}" for k, v in info.get("extras", {}).get("light", {}).items())
        extras_d = ";".join(f"--th-{k}:{v}" for k, v in info.get("extras", {}).get("dark", {}).items())
        tokens_css.append(f".{scope}{{{block(toks['light'])};{extras_l}}}")
        tokens_css.append(f"@media (prefers-color-scheme:dark){{:root:not([data-theme=\"light\"]) .{scope}{{{block(toks['dark'])};{extras_d}}}}}")
        tokens_css.append(f":root[data-theme=\"dark\"] .{scope}{{{block(toks['dark'])};{extras_d}}}")
        with open(os.path.join(folder, "theme.css"), encoding="utf-8") as f:
            css = f.read()
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        theme_css.append(_scope_css(css, scope))

        def slot(key, k=0):
            with open(os.path.join(folder, *raw["theme"]["slots"][key].split("/")), encoding="utf-8") as f:
                return _svg_inline(f.read(), letter, n * 10 + k)
        states = [("mascot.idle", "平常"), ("mascot.think", "想事情"), ("mascot.happy", "完成"), ("mascot.warn", "要你確認")]
        file = f"{raw['name']}-{raw['version']}.synoxpack"
        url = download_url(file)
        notes = "".join(f"<li>{html.escape(t)}</li>" for t in info.get("caution", []))
        swatch = "".join(f'<i style="background:var({t})" title="{t}"></i>' for t in ("--bg", "--surface", "--accent", "--accent2", "--soft", "--m1"))
        nav.append(f'<a href="#{raw["name"]}" title="{letter} {html.escape(raw["title"])}"><span class="nd" style="background:{info["ic"]}"></span>{letter}<span class="nn">　{html.escape(raw["title"])}</span></a>')
        cards.append(f"""
<section class="cw {scope}" id="{raw['name']}" aria-labelledby="h-{scope}">
  <div class="cw-head">
    <span class="cw-icon" data-slot="appicon">{slot('appicon')}</span>
    <div class="cw-title">
      <p class="cw-tag">系列 {letter} · <code>{raw['name']}</code></p>
      <h2 id="h-{scope}">{html.escape(raw['title'])}</h2>
      <p class="cw-one">{html.escape(info.get('one', ''))}</p>
    </div>
  </div>
  <div class="cw-body">
    <div class="cw-states">{''.join(f'<figure data-slot="{k}">{slot(k, i + 1)}<figcaption>{t}</figcaption></figure>' for i, (k, t) in enumerate(states))}</div>
    <div class="cw-open">
      <div class="cw-screen" data-slot="opening" data-letter="{letter}">{slot('opening', 6)}</div>
      <button class="cw-replay" type="button" data-replay="{letter}">↻ 重播開場</button>
    </div>
  </div>
  <div class="cw-foot">
    <div class="cw-wm" data-slot="wordmark">{slot('wordmark', 7)}<span class="cw-sw">{swatch}</span></div>
    <a class="cw-dl" href="{url}" target="_blank" rel="noopener">下載 .synoxpack</a>
  </div>
  {f'<details class="cw-note"><summary>要注意</summary><ul>{notes}</ul></details>' if notes else ''}
</section>""")
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wardrobe_template.html")
    with open(template_path, encoding="utf-8") as f:
        page = f.read()
    stamp_old = ""
    current = os.path.join(SITE, "wardrobe.html")
    if os.path.isfile(current):
        with open(current, encoding="utf-8") as f:
            m = re.search(r"<span data-stamp>(.*?)</span>", f.read(), re.S)
            stamp_old = m.group(1) if m else ""
    repl = {
        "{{COUNT}}": str(len(folders)), "{{TAG}}": tag, "{{VERSION}}": library_version(),
        "{{RELEASE}}": links.release_page_url(), "{{SUMS}}": links.asset_url("SHA256SUMS"), "{{NAV}}": "".join(nav),
        "{{TOKENS_CSS}}": "\n".join(tokens_css), "{{THEME_CSS}}": "\n".join(theme_css),
        "{{CARDS}}": "\n".join(cards), "{{STAMP}}": stamp_old, "{{ONLINE}}": ONLINE_URL,
    }
    for k, v in repl.items():
        page = page.replace(k, v)
    return page


# ── extract：品牌提案頁 → costumes/<id>/（要 Playwright、fontTools、連網一次）─────────────

CONVERT_JS = r"""
() => {
/* HTML（排好版的開場／字標）→ 純 SVG。動畫一律關掉、transform 拿掉之後量位置；CSS 規則另外由 Python 轉過去。
   每個 HTML 元素變成 <g class="原本的 class t-<tag> x">，自己的背景／框／字放在第一個子元素 <svg class="v">；
   transform-origin 用量到的絕對座標（transform-box: view-box），跟原本 HTML 的轉軸一樣。 */
window.__conv = function (root, P, label) {
  const O = root.getBoundingClientRect();
  let nid = 0; const defs = []; const glyphs = []; const warn = [];
  const n = v => String(Math.round(v * 100) / 100);
  const esc = t => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const px = v => parseFloat(v) || 0;
  const alpha = c => { const m = /rgba?\(([^)]*)\)/.exec(c || ''); if (!m) return c && c !== 'transparent' ? 1 : 0; const p = m[1].split(/[ ,\/]+/).filter(Boolean); return p.length > 3 ? parseFloat(p[3]) : 1; };
  const RULES = [];
  (function walk(rs) { for (const r of rs) { if (r.type === 1) RULES.push(r); else if (r.cssRules) walk(r.cssRules); } })(
    [].concat(...[...document.styleSheets].map(s => { try { return [...s.cssRules]; } catch (e) { return []; } })));
  function specified(el, pseudo, props) {
    let found = '';
    for (const r of RULES) {
      let sel = r.selectorText;
      if (pseudo) { if (sel.indexOf('::' + pseudo) < 0) continue; sel = sel.split(',').filter(s => s.indexOf('::' + pseudo) >= 0).map(s => s.replace('::' + pseudo, '')).join(','); }
      else if (sel.indexOf('::') >= 0) { sel = sel.split(',').filter(s => s.indexOf('::') < 0).join(','); if (!sel) continue; }
      try { if (!el.matches(sel)) continue; } catch (e) { continue; }
      for (const p of props) { const v = r.style.getPropertyValue(p); if (v) found = v; }
    }
    return found;
  }
  function radii(cs, w, h) {
    return ['borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomRightRadius', 'borderBottomLeftRadius'].map(k => {
      const p = cs[k].split(' '); const a = p[0], b = p[1] || p[0];
      const f = (v, ref) => v.endsWith('%') ? parseFloat(v) / 100 * ref : px(v);
      return [f(a, w), f(b, h)];
    });
  }
  function geo(x, y, w, h, R) {
    if (w <= 0 || h <= 0) return null;
    R = R.map(r => [Math.max(0, r[0]), Math.max(0, r[1])]);
    const f = Math.min(1, w / ((R[0][0] + R[1][0]) || 1e-9), w / ((R[3][0] + R[2][0]) || 1e-9), h / ((R[0][1] + R[3][1]) || 1e-9), h / ((R[1][1] + R[2][1]) || 1e-9));
    R = R.map(r => [r[0] * f, r[1] * f]);
    const same = R.every(r => Math.abs(r[0] - R[0][0]) < .01 && Math.abs(r[1] - R[0][1]) < .01);
    if (same) return { tag: 'rect', a: `x="${n(x)}" y="${n(y)}" width="${n(w)}" height="${n(h)}"` + (R[0][0] || R[0][1] ? ` rx="${n(R[0][0])}" ry="${n(R[0][1])}"` : '') };
    const [tl, tr, br, bl] = R;
    return { tag: 'path', a: `d="M${n(x + tl[0])} ${n(y)}H${n(x + w - tr[0])}A${n(tr[0])} ${n(tr[1])} 0 0 1 ${n(x + w)} ${n(y + tr[1])}V${n(y + h - br[1])}A${n(br[0])} ${n(br[1])} 0 0 1 ${n(x + w - br[0])} ${n(y + h)}H${n(x + bl[0])}A${n(bl[0])} ${n(bl[1])} 0 0 1 ${n(x)} ${n(y + h - bl[1])}V${n(y + tl[1])}A${n(tl[0])} ${n(tl[1])} 0 0 1 ${n(x + tl[0])} ${n(y)}Z"` };
  }
  const shape = (g, attrs) => g ? `<${g.tag} ${g.a} ${attrs}/>` : '';
  function splitTop(s, sep) { const out = []; let d = 0, cur = ''; for (const ch of s) { if (ch === '(') d++; if (ch === ')') d--; if (ch === sep && !d) { out.push(cur.trim()); cur = ''; } else cur += ch; } if (cur.trim()) out.push(cur.trim()); return out; }
  function colorOf(tok) {
    tok = tok.trim();
    if (tok === 'transparent') return { c: null, o: 0 };
    if (/^var\(--[\w-]+\)$/.test(tok)) { const name = tok.slice(4, -1); const lit = getComputedStyle(root).getPropertyValue(name).trim(); return { c: `var(${name}, ${lit})`, o: 1 }; }
    if (/^(#[0-9a-f]{3,8}|rgba?\([^)]*\)|[a-z]+)$/i.test(tok)) { const a = alpha(tok); return { c: tok.replace(/rgba\(([^,]+),([^,]+),([^,]+),[^)]*\)/, 'rgb($1,$2,$3)'), o: a }; }
    return null;
  }
  function gradient(spec, x, y, w, h) {
    const i = spec.search(/(radial|linear)-gradient\(/); if (i < 0) return null;
    let d = 0, j = spec.indexOf('(', i); const start = j;
    for (; j < spec.length; j++) { if (spec[j] === '(') d++; if (spec[j] === ')') { d--; if (!d) break; } }
    const kind = spec.slice(i, start).startsWith('radial') ? 'radial' : 'linear';
    const args = splitTop(spec.slice(start + 1, j), ',');
    let head = args[0], stopsRaw = args.slice(1);
    if (/^(var|#|rgb|transparent)/.test(head)) { head = ''; stopsRaw = args; }
    const stops = [];
    for (const s of stopsRaw) {
      const parts = splitTop(s, ' ');
      const col = colorOf(parts[0]); if (!col) return null;
      let pos = null; if (parts[1]) pos = parts[1].endsWith('%') ? parseFloat(parts[1]) / 100 : null;
      stops.push({ col, pos }); if (parts[2]) stops.push({ col, pos: parseFloat(parts[2]) / 100 });
    }
    if (stops.length < 2) return null;
    if (stops[0].pos == null) stops[0].pos = 0; if (stops[stops.length - 1].pos == null) stops[stops.length - 1].pos = 1;
    for (let a = 0; a < stops.length; a++) if (stops[a].pos == null) { let b = a; while (stops[b].pos == null) b++; const p0 = stops[a - 1].pos, p1 = stops[b].pos; for (let c = a; c < b; c++) stops[c].pos = p0 + (p1 - p0) * (c - a + 1) / (b - a + 1); }
    stops.forEach((s, k) => { if (!s.col.c) { const nb = stops[k + 1] && stops[k + 1].col.c ? stops[k + 1] : stops[k - 1]; s.col = { c: nb ? nb.col.c : '#000', o: 0 }; } });
    const id = `${P}-g${nid++}`;
    const st = stops.map(s => `<stop offset="${n(s.pos)}" stop-color="${s.col.c}"${s.col.o < 1 ? ` stop-opacity="${n(s.col.o)}"` : ''}/>`).join('');
    if (kind === 'radial') {
      let cx = x + w / 2, cy = y + h / 2, rx = null, ry = null;
      const at = /at\s+([\d.]+)%\s+([\d.]+)%/.exec(head); if (at) { cx = x + w * parseFloat(at[1]) / 100; cy = y + h * parseFloat(at[2]) / 100; }
      const sz = /^([\d.]+)px\s+([\d.]+)px/.exec(head); if (sz) { rx = parseFloat(sz[1]); ry = parseFloat(sz[2]); }
      const cs1 = /^([\d.]+)px(\s|$)/.exec(head); if (!sz && cs1) rx = ry = parseFloat(cs1[1]);
      if (rx == null) { const far = Math.max(...[[x, y], [x + w, y], [x, y + h], [x + w, y + h]].map(p => Math.hypot(p[0] - cx, p[1] - cy))); if (/circle/.test(head)) rx = ry = far; else { const fx = Math.max(cx - x, x + w - cx), fy = Math.max(cy - y, y + h - cy); rx = fx * Math.SQRT2; ry = fy * Math.SQRT2; } }
      defs.push(`<radialGradient id="${id}" gradientUnits="userSpaceOnUse" cx="${n(cx)}" cy="${n(cy)}" r="${n(rx)}" gradientTransform="translate(${n(cx)} ${n(cy)}) scale(1 ${n(ry / rx)}) translate(${n(-cx)} ${n(-cy)})">${st}</radialGradient>`);
    } else {
      let ang = 180; const am = /^(-?[\d.]+)deg/.exec(head); if (am) ang = parseFloat(am[1]);
      const tm = { 'to top': 0, 'to right': 90, 'to bottom': 180, 'to left': 270 }; if (tm[head]) ang = tm[head];
      const t = ang * Math.PI / 180, sx = Math.sin(t), sy = -Math.cos(t), L = Math.abs(w * sx) + Math.abs(h * sy), cx = x + w / 2, cy = y + h / 2;
      defs.push(`<linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="${n(cx - sx * L / 2)}" y1="${n(cy - sy * L / 2)}" x2="${n(cx + sx * L / 2)}" y2="${n(cy + sy * L / 2)}">${st}</linearGradient>`);
    }
    return id;
  }
  function boxOf(el) { const r = el.getBoundingClientRect(); return { x: r.left - O.left, y: r.top - O.top, w: r.width, h: r.height }; }
  function pseudoBox(el, cs, ps, b) {
    if (ps.position !== 'absolute' && ps.position !== 'fixed') return null;
    const cbx = b.x + px(cs.borderLeftWidth), cby = b.y + px(cs.borderTopWidth);
    const cbw = b.w - px(cs.borderLeftWidth) - px(cs.borderRightWidth), cbh = b.h - px(cs.borderTopWidth) - px(cs.borderBottomWidth);
    const L = ps.left, R = ps.right, T = ps.top, B = ps.bottom;
    let w = ps.width === 'auto' ? cbw - px(L) - px(R) : px(ps.width), h = ps.height === 'auto' ? cbh - px(T) - px(B) : px(ps.height);
    let x = L !== 'auto' ? cbx + px(L) + px(ps.marginLeft) : cbx + cbw - px(R) - w - px(ps.marginRight);
    let y = T !== 'auto' ? cby + px(T) + px(ps.marginTop) : cby + cbh - px(B) - h - px(ps.marginBottom);
    return { x, y, w, h };
  }
  function visuals(el, cs, b, pseudo) {
    let out = '';
    const R = radii(cs, b.w, b.h);
    if (cs.boxShadow && cs.boxShadow !== 'none') {
      for (const s of splitTop(cs.boxShadow, ',')) {
        if (/inset/.test(s)) continue;
        const col = (/rgba?\([^)]*\)/.exec(s) || [''])[0]; const nums = s.replace(/rgba?\([^)]*\)/, '').trim().split(/\s+/).map(px);
        const [ox, oy, blur, spread] = [nums[0] || 0, nums[1] || 0, nums[2] || 0, nums[3] || 0];
        if (blur || alpha(col) === 0) continue;
        out += shape(geo(b.x + ox - spread, b.y + oy - spread, b.w + 2 * spread, b.h + 2 * spread, R.map(r => [r[0] + spread, r[1] + spread])), `class="sh" fill="${col}"`);
      }
    }
    const mask = cs.maskImage || cs.webkitMaskImage || '';
    const bgc = cs.backgroundColor;
    const bgSpec = specified(el, pseudo, ['background-color', 'background']);
    const bgTok = colorOf(bgSpec.trim());
    const fill = bgTok && bgTok.c ? bgTok.c : bgc;
    if (mask.indexOf('data:image/svg+xml') >= 0) {
      const raw = decodeURIComponent(/url\("?(data:image\/svg\+xml[^")]*)/.exec(mask)[1].replace(/^data:image\/svg\+xml(;utf8)?,/, ''));
      const doc = new DOMParser().parseFromString(raw, 'image/svg+xml').documentElement;
      doc.querySelectorAll('[fill]').forEach(e => e.removeAttribute('fill'));
      out += `<svg class="bg" x="${n(b.x)}" y="${n(b.y)}" width="${n(b.w)}" height="${n(b.h)}" viewBox="${doc.getAttribute('viewBox')}" fill="${fill}" overflow="visible">${doc.innerHTML.replace(/ xmlns="[^"]*"/g, '')}</svg>`;
    } else if (alpha(bgc) > 0) {
      out += shape(geo(b.x, b.y, b.w, b.h, R), `class="bg" fill="${fill}"`);
    }
    if (cs.backgroundImage && cs.backgroundImage !== 'none') {
      const spec = specified(el, pseudo, ['background-image', 'background']);
      const size = cs.backgroundSize;
      const tile = /^([\d.]+)px ([\d.]+)px/.exec(size || '');
      if (tile && spec) {
        // 格線（W 的液晶格）：每一層是「一條 1px 的線＋透明」的 linear-gradient，變成 SVG pattern
        const tw = parseFloat(tile[1]), th = parseFloat(tile[2]); let lines = '';
        for (const layer of splitTop(spec, ',').filter(x => /linear-gradient/.test(x))) {
          const inner = layer.slice(layer.indexOf('(') + 1, layer.lastIndexOf(')'));
          const args = splitTop(inner, ','); const vertical = /^90deg/.test(args[0]);
          const first = splitTop((/deg|^to /.test(args[0]) ? args[1] : args[0]) || '', ' ');
          const col = colorOf(first[0] || ''); const w = px(first[1] || '1px');
          if (!col || !col.c) continue;
          lines += vertical ? `<rect width="${n(w)}" height="${n(th)}" fill="${col.c}"${col.o < 1 ? ` fill-opacity="${n(col.o)}"` : ''}/>`
                            : `<rect width="${n(tw)}" height="${n(w)}" fill="${col.c}"${col.o < 1 ? ` fill-opacity="${n(col.o)}"` : ''}/>`;
        }
        if (lines) { const id = `${P}-p${nid++}`; defs.push(`<pattern id="${id}" patternUnits="userSpaceOnUse" x="${n(b.x)}" y="${n(b.y)}" width="${n(tw)}" height="${n(th)}">${lines}</pattern>`);
          out += shape(geo(b.x, b.y, b.w, b.h, R), `class="bgi" fill="url(#${id})"`); }
        else warn.push('tiled gradient not converted on ' + (el.className || el.tagName));
      } else if (size && size !== 'auto' && size !== 'auto auto') warn.push('skip tiled gradient on ' + (el.className || el.tagName) + (pseudo ? '::' + pseudo : ''));
      else {
        const id = spec && gradient(spec, b.x, b.y, b.w, b.h);
        if (id) out += shape(geo(b.x, b.y, b.w, b.h, R), `class="bgi" fill="url(#${id})"`);
        else warn.push('gradient not converted on ' + (el.className || el.tagName) + ': ' + (spec || cs.backgroundImage).slice(0, 80));
      }
    }
    const bw = px(cs.borderTopWidth);
    if (bw > 0 && cs.borderTopStyle !== 'none' && alpha(cs.borderTopColor) > 0) {
      const dash = cs.borderTopStyle === 'dotted' ? ` stroke-dasharray="0 ${n(bw * 2)}" stroke-linecap="round"` : cs.borderTopStyle === 'dashed' ? ` stroke-dasharray="${n(bw * 2)} ${n(bw)}"` : '';
      const bSpec = specified(el, pseudo, ['border-color', 'border-top-color', 'border']);
      const bTok = bSpec ? splitTop(bSpec, ' ').map(colorOf).filter(c => c && c.c).pop() : null;
      out += shape(geo(b.x + bw / 2, b.y + bw / 2, b.w - bw, b.h - bw, R.map(r => [r[0] - bw / 2, r[1] - bw / 2])), `class="bd" fill="none" stroke="${bTok ? bTok.c : cs.borderTopColor}" stroke-width="${n(bw)}"${dash}`);
    }
    return out;
  }
  function fontAttrs(cs) {
    return `font-family="${esc(cs.fontFamily.replace(/"/g, "'"))}" font-size="${n(px(cs.fontSize))}" font-weight="${cs.fontWeight}"` +
      (cs.letterSpacing !== 'normal' && px(cs.letterSpacing) ? ` letter-spacing="${n(px(cs.letterSpacing))}"` : '') + (cs.fontStyle === 'italic' ? ' font-style="italic"' : '');
  }
  function texts(el, cs, b) {
    let out = '';
    const isLetter = el.classList.contains('l') && el.closest('.wm');
    const flexy = /flex|grid/.test(cs.display);
    for (const node of [...el.childNodes]) {
      if (node.nodeType !== 3 || !node.data.trim()) continue;
      const range = document.createRange(); range.selectNodeContents(node); const rects = [...range.getClientRects()].filter(r => r.width > 0);
      if (!rects.length) continue;
      if (rects.length > 1 && !isLetter) warn.push('text wraps: ' + node.data.trim().slice(0, 20));
      let base;
      if (!flexy) {
        const mk = document.createElement('x-b'); mk.style.cssText = 'display:inline-block;width:0;height:0;vertical-align:baseline;margin:0;padding:0;border:0';
        el.insertBefore(mk, node); base = mk.getBoundingClientRect().top - O.top; mk.remove();
      } else base = rects[0].top - O.top + rects[0].height * .78;
      const x = rects[0].left - O.left;
      const pb = getComputedStyle(el, '::before');
      if (pb.content && pb.content !== 'none' && pb.content !== '""' && pb.content !== 'normal' && pb.position !== 'absolute') {
        const t = pb.content.replace(/^"|"$/g, '');
        out += `<text class="ps-text" x="${n(b.x + px(cs.borderLeftWidth) + px(cs.paddingLeft))}" y="${n(base)}" ${fontAttrs(pb)} fill="currentColor" xml:space="preserve">${esc(t)}</text>`;
      }
      if (isLetter) {
        const ch = node.data.trim();
        glyphs.push({ ch, family: cs.fontFamily.split(',')[0].replace(/["']/g, '').trim(), weight: parseInt(cs.fontWeight, 10), size: px(cs.fontSize), x, y: base });
        out += `<path class="gl" d="@@G${glyphs.length - 1}@@" fill="currentColor"/>`;
      } else {
        out += `<text x="${n(x)}" y="${n(base)}" ${fontAttrs(cs)} fill="currentColor" xml:space="preserve">${esc(node.data.replace(/\s+/g, ' ').trim())}</text>`;
      }
    }
    return out;
  }
  function gAttrs(el, cs, b, extraCls) {
    const tag = el.tagName.toLowerCase();
    const cls = [...el.classList, 't-' + tag, 'x'].concat(extraCls || []).join(' ');
    const to = cs.transformOrigin.split(' ').map(px);
    let style = `transform-box:view-box;transform-origin:${n(b.x + to[0])}px ${n(b.y + to[1])}px`;
    if (el.style) for (const p of el.style) if (p.startsWith('--')) style += `;${p}:${el.style.getPropertyValue(p).trim()}`;
    let clip = '';
    if (cs.overflow === 'hidden' || cs.overflowX === 'hidden' || cs.overflow === 'clip') {
      const id = `${P}-k${nid++}`;
      const g = geo(b.x, b.y, b.w, b.h, radii(cs, b.w, b.h));
      defs.push(`<clipPath id="${id}">${shape(g, '')}</clipPath>`); clip = ` clip-path="url(#${id})"`;
    }
    return { a: `class="${cls}" style="${esc(style)}"`, clip };
  }
  function zOf(el) { const cs = getComputedStyle(el); return cs.position !== 'static' && cs.zIndex !== 'auto' ? parseInt(cs.zIndex, 10) : 0; }
  function sortKids(el) { return [...el.children].map((c, i) => ({ c, i, z: zOf(c) })).sort((a, b) => a.z - b.z || a.i - b.i).map(o => o.c); }
  let HOIST = new Set();
  function render(el) {
    if (HOIST.has(el)) return '';
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || el.tagName === 'X-B') return '';
    const b = boxOf(el);
    if (el instanceof SVGSVGElement) {
      const keep = ['class', 'viewBox', 'preserveAspectRatio'].filter(a => el.getAttribute(a)).map(a => `${a}="${esc(el.getAttribute(a))}"`).join(' ');
      return `<svg ${keep} x="${n(b.x)}" y="${n(b.y)}" width="${n(b.w)}" height="${n(b.h)}" overflow="visible">${el.innerHTML}</svg>`;
    }
    let own = visuals(el, cs, b, '') + texts(el, cs, b);
    let before = '', after = '';
    for (const which of ['before', 'after']) {
      const ps = getComputedStyle(el, '::' + which);
      if (!ps.content || ps.content === 'none' || ps.content === 'normal' || ps.display === 'none') continue;
      if (ps.content !== '""') { if (ps.position === 'absolute') warn.push('text pseudo skipped'); continue; }
      const pb = pseudoBox(el, cs, ps, b); if (!pb) { warn.push('static pseudo skipped on ' + el.className); continue; }
      const to = ps.transformOrigin.split(' ').map(px);
      let style = `transform-box:view-box;transform-origin:${n(pb.x + to[0])}px ${n(pb.y + to[1])}px`;
      const vis = visuals(el, ps, pb, which);
      const s = `<g class="ps-${which} x" style="${esc(style)}"><svg class="v" overflow="visible">${vis}</svg></g>`;
      if (which === 'before') before = s; else after = s;
    }
    const kids = sortKids(el).map(render).join('');
    const ga = gAttrs(el, cs, b);
    // overflow:hidden 只剪裡面的東西（自己的外框陰影不剪），所以剪的是包住子元素的那一層
    const inner = ga.clip ? `<g class="xc"${ga.clip}>${kids}</g>` : kids;
    return `<g ${ga.a}><svg class="v" overflow="visible">${own}</svg>${before}${inner}${after}</g>`;
  }
  // z-index：根層有 z>0 的元素時，裡面（沒有自己堆疊層的）z>0 元素拉到根層照順序畫（例：B 的暗幕在燈籠下面、字的上面）
  const rootKids = sortKids(root);
  const hoisted = [];
  if (rootKids.some(k => zOf(k) > 0)) {
    for (const k of rootKids) k.querySelectorAll('*').forEach(e => {
      if (zOf(e) <= 0) return;
      for (let a = e.parentElement; a && a !== k.parentElement; a = a.parentElement) { const c = getComputedStyle(a); if (c.zIndex !== 'auto' || parseFloat(c.opacity) < 1) return; }
      hoisted.push(e);
    });
  }
  HOIST = new Set(hoisted);
  const rcs = getComputedStyle(root);
  const rb = { x: 0, y: 0, w: O.width, h: O.height };
  const items = rootKids.map((c, i) => ({ z: zOf(c), i, s: render(c) }));
  HOIST = new Set();
  hoisted.forEach((e, i) => {
    let s = render(e);
    for (let a = e.parentElement; a && a !== root; a = a.parentElement) { const cs = getComputedStyle(a); s = `<g ${gAttrs(a, cs, boxOf(a)).a}>${s}</g>`; }
    items.push({ z: zOf(e), i: 1000 + i, s });
  });
  items.sort((a, b) => a.z - b.z || a.i - b.i);
  let rootAfter = '';
  const pa = getComputedStyle(root, '::after');
  if (pa.content === '""') { const pb = pseudoBox(root, rcs, pa, rb); if (pb) rootAfter = `<g class="ps-after x"><svg class="v" overflow="visible">${visuals(root, pa, pb, 'after')}</svg></g>`; }
  const own = visuals(root, rcs, rb, '');
  const cls = [...root.classList].join(' ');
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${n(O.width)} ${n(O.height)}" class="${cls}" role="img" aria-label="${esc(label)}">` +
    (defs.length ? `<defs>${defs.join('')}</defs>` : '') + `<svg class="v" overflow="visible">${own}</svg>` + items.map(i => i.s).join('') + rootAfter + '</svg>';
  return { svg, glyphs, warn };
};

/* 規則有沒有用到、用在哪：converted＝轉出來的 <g class="x">（只留動畫類的屬性）；internal＝原本就是 SVG 的元素（全部保留） */
window.__classify = function (host, sels) {
  return sels.map(sel => {
    let test = sel.replace(/^:root(\[[^\]]*\]|:not\([^)]*\))*\s*/, '').replace(/:(hover|focus|focus-visible|active)\b/g, '');
    let els;
    try { els = [...host.querySelectorAll(test)]; } catch (e) { return 'bad'; }
    if (!els.length) return 'none';
    const conv = els.some(e => e.classList.contains('x') || e.classList.contains('v') || e.closest('svg.v') || (e.tagName.toLowerCase() === 'svg' && e.parentElement && (e.parentElement.classList.contains('x') || e.parentElement.hasAttribute('data-slot'))));
    return conv ? 'converted' : 'internal';
  });
};
}
"""

FREEZE_CSS = """
.cw-sbx *:not(svg *):not(svg), .cw-sbx *::before, .cw-sbx *::after {
  animation: none !important; transition: none !important; transform: none !important; translate: none !important; rotate: none !important; scale: none !important; }
.cw-sbx svg { animation: none !important; }
"""

CONVERTED_PROPS = re.compile(r"^(animation(-[a-z-]+)?|transform|translate|rotate|scale|opacity|filter|clip-path|color|visibility|"
                             r"mix-blend-mode|stroke(-[a-z-]+)?|fill(-opacity)?|--[\w-]+)$")
HTML_TAGS = ("i", "b", "span", "div", "em", "small", "p", "strong", "u", "s")


def split_top(s: str, sep: str = ",") -> list:
    out, depth, cur, quote = [], 0, "", ""
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [p.strip() for p in out if p.strip()]


def decls_of(css_text: str) -> list:
    out = []
    for d in split_top(css_text, ";"):
        if ":" in d:
            k, v = d.split(":", 1)
            out.append((k.strip(), v.strip()))
    return out


def rename_vars(text: str) -> str:
    return re.sub(r"var\(--(ac2|acink|ac|surf|rad|uif)(?=[,)\s])", lambda m: f"var(--{RENAME[m.group(1)]}", text)


def translate_selector(sel: str, sid: str):
    refs = re.findall(r"\.(?:s|op)-([A-Z])\b", sel)
    if any(r != sid for r in refs):
        return None
    # 原本的 .s-X 換成多一個 [data-slot]（[data-slot][data-slot]），跟共用規則（前面加一個 [data-slot]）比的時候特異度不變
    had = bool(re.search(r"\.s-" + sid + r"\b", sel))
    q = re.sub(r"\.s-" + sid + r"\b", " ", sel)
    q = re.sub(r"\s+", " ", q).strip()
    if not q or q.startswith((">", "+", "~")):
        return None
    tag_rx = r"(^|[\s>+~(])(" + "|".join(HTML_TAGS) + r")(?=$|[\s.:#\[>+~)])"
    q = re.sub(tag_rx, lambda m: m.group(1) + ".t-" + m.group(2), q)
    q = q.replace("::before", " > .ps-before").replace("::after", " > .ps-after")
    q = q.replace(":nth-child(", ":nth-of-type(").replace(":first-child", ":first-of-type").replace(":last-child", ":last-of-type")
    scope = "[data-slot][data-slot]" if had else "[data-slot]"
    m = re.match(r"^(:root(?:\[[^\]]*\]|:not\([^)]*\))*)\s*(.*)$", q)
    if m:
        if not m.group(2):
            return None
        q = f"{m.group(1)} {scope} {m.group(2)}"
    else:
        q = f"{scope} {q}"
    return re.sub(r"\s+", " ", q)


def font_ttf(family: str, weight: int, cache: str) -> bytes:
    """Google Fonts 的 TTF（只要 S Y N O X 的大小寫），存在 cache 資料夾；之後不再連網。"""
    import urllib.request
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, f"{family.replace(' ', '_')}-{weight}.ttf")
    if os.path.isfile(path):
        with open(path, "rb") as f:
            return f.read()
    fam = family.replace(" ", "+")
    for spec in (f"{fam}:wght@{weight}", fam):
        url = f"https://fonts.googleapis.com/css2?family={spec}&text=SYNOXsynox"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/4.0"})
        try:
            css = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
        except Exception:                                      # noqa: BLE001
            continue
        m = re.search(r"url\((https://[^)]+)\)", css)
        if m:
            blob = urllib.request.urlopen(m.group(1), timeout=30).read()
            with open(path, "wb") as f:
                f.write(blob)
            return blob
    raise SystemExit(f"拿不到字型 {family} {weight}")


def glyph_path(glyph: dict, cache: str, fonts: dict) -> str:
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    key = (glyph["family"], glyph["weight"])
    if key not in fonts:
        fonts[key] = TTFont(io.BytesIO(font_ttf(glyph["family"], glyph["weight"], cache)))
    font = fonts[key]
    gs, cmap, upm = font.getGlyphSet(), font.getBestCmap(), font["head"].unitsPerEm
    if ord(glyph["ch"]) not in cmap:
        raise SystemExit(f"{glyph['family']} 沒有 {glyph['ch']}")
    k = glyph["size"] / upm
    pen = SVGPathPen(gs, ntos=lambda v: ("%.2f" % v).rstrip("0").rstrip("."))
    gs[cmap[ord(glyph["ch"])]].draw(TransformPen(pen, (k, 0, 0, -k, glyph["x"], glyph["y"])))
    return pen.getCommands()


def add_fallbacks(svg: str, light: dict) -> str:
    """SVG 裡的 var(--x) 補上淺色的值當後備：單獨打開這個 SVG（或 SYNOX 用 <img> 顯示）時顏色也是對的。"""
    def sub(m):
        name = m.group(1)
        return f"var(--{name}, {light[name]})" if name in light else m.group(0)
    return re.sub(r"var\(--([\w-]+)\)", sub, svg)


def prefix_ids(svg: str, p: str) -> str:
    ids = set(re.findall(r'\sid="([^"]+)"', svg))
    for i in sorted(ids, key=len, reverse=True):
        if i.startswith(p + "-"):
            continue
        svg = svg.replace(f'id="{i}"', f'id="{p}-{i}"').replace(f"url(#{i})", f"url(#{p}-{i})").replace(f'href="#{i}"', f'href="#{p}-{i}"')
    return svg


def rename_extras(text: str, names) -> str:
    """這一套自己多用的變數（不是 SYNOX 的 token）改成 --th-<名字>：SYNOX 的 :root 只收合約 token 與 --th- 開頭的。"""
    if not names:
        return text
    rx = r"(?<![\w-])--(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")(?![\w-])"
    return re.sub(rx, r"--th-\1", text)


def strip_filters(svg: str) -> str:
    """SYNOX 白名單外的濾鏡元件（例如 Y 的 feTurbulence 顆粒）連同吃它結果的那一串拿掉，剩下的濾鏡照常算。
    不拿掉的話 SYNOX 消毒時只拿掉那一個元件，後面 in="…" 指到不存在的結果，整個濾鏡會算錯。"""
    dead = set()

    def prim(m):
        tag, name = m.group(0), m.group(1) or m.group(2)
        uses = set(re.findall(r'\sin2?="([^"]+)"', tag))
        if name in FILTER_PRIMITIVES and not (uses & dead):
            return tag
        if m.group(2):                                   # 裡面還有東西的（<feComponentTransfer>…）：要另外處理
            raise SystemExit(f"不會拿掉裡面有東西的 <{name}>：build_costumes.py 要跟著改")
        r = re.search(r'\sresult="([^"]+)"', tag)
        if r:
            dead.add(r.group(1))
        return ""
    return re.sub(r"<(fe[A-Za-z]+)\b[^<>]*?(?:/>|>\s*</\1>)|<(fe[A-Za-z]+)\b[^<>]*>", prim, svg)


# style="…" 裡可以直接變成 SVG 屬性的（SYNOX 的消毒也是這樣轉；先在這裡轉好，消毒前後才會一樣）
LIFT_ATTRS = {"fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
              "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset", "stroke-opacity", "opacity", "stop-color",
              "stop-opacity", "clip-path", "clip-rule", "mask", "filter", "flood-color", "flood-opacity", "font-family",
              "font-size", "font-weight", "font-style", "text-anchor", "dominant-baseline", "letter-spacing",
              "word-spacing", "visibility", "display", "vector-effect", "paint-order", "shape-rendering", "overflow",
              "color", "mix-blend-mode", "isolation", "transform-origin"}


def lift_styles(svg: str, p: str, classes: dict) -> str:
    """SVG 的 style="…" 拿掉（SYNOX 網頁的 CSP 不收 inline style，消毒時只留顏色、線條這類）：
    transform-origin 這類變成同名屬性；transform-box:view-box 是 SVG 的預設，直接拿掉；
    --i、--dx 這種自訂變數換成一個 class（classes：宣告 → class 名），規則由呼叫的人寫進 theme.css。"""
    def tag(m):
        t = m.group(0)
        sm = re.search(r'\sstyle="([^"]*)"', t)
        if not sm:
            return t
        attrs, custom = [], []
        for k, v in decls_of(html.unescape(sm.group(1))):
            if k.startswith("--"):
                custom.append(f"{k}:{v}")
            elif k.lower() == "transform-box" and v == "view-box":
                continue
            elif k.lower() in LIFT_ATTRS:
                attrs.append(f' {k.lower()}="{html.escape(v, quote=True)}"')
            else:
                raise SystemExit(f"style 裡有 {k}：SYNOX 不收 inline style，build_costumes.py 要跟著處理")
        t = t[:sm.start()] + "".join(attrs) + t[sm.end():]
        if custom:
            cls = classes.setdefault(";".join(custom), f"{p}-v{len(classes)}")
            cm = re.search(r'\sclass="([^"]*)"', t)
            if cm:
                t = t[:cm.start()] + f' class="{cm.group(1)} {cls}"' + t[cm.end():]
            else:
                t = re.sub(r"^<([A-Za-z][\w:-]*)", lambda mm: f'<{mm.group(1)} class="{cls}"', t)
        return t
    return re.sub(r"<[A-Za-z][^<>]*>", tag, svg)


def clean_svg(svg: str) -> str:
    svg = svg.replace(' xmlns="http://www.w3.org/2000/svg"', "")
    svg = re.sub(r"\s(role|aria-label|aria-hidden)=\"[^\"]*\"", "", svg)
    return svg


def extract(brand: str, cache: str, only=None) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("extract 要 Playwright：pip install playwright && playwright install chromium")
    import tempfile
    with open(brand, encoding="utf-8") as f:
        src = f.read()
    marker = "S.sort(function (a, b) { return a.id < b.id ? -1 : a.id > b.id ? 1 : 0; });"
    if src.count(marker) != 1:
        raise SystemExit("品牌頁的格式跟預期不一樣（找不到排序 S 的那一行），這支腳本要跟著改")
    src = src.replace(marker, marker + "\nwindow.__B={S:S,BY:BY,mascot:mascot,glyph:glyph,opening:opening,wmLetters:wmLetters};\n")
    tmp = tempfile.mkdtemp(prefix="costumes-")
    page_path = os.path.join(tmp, "brand.html")
    with open(page_path, "w", encoding="utf-8") as f:
        f.write(src)
    launch = {"executable_path": os.environ.get("CHROMIUM_PATH") or None}
    if os.environ.get("HTTPS_PROXY"):
        launch["proxy"] = {"server": os.environ["HTTPS_PROXY"]}
    fonts = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(**{k: v for k, v in launch.items() if v})
        page = browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 900}, color_scheme="light").new_page()
        page.goto("file://" + page_path)
        page.wait_for_function("document.fonts.status === 'loaded'")
        page.wait_for_timeout(1500)
        page.evaluate("document.documentElement.setAttribute('data-theme','light')")
        page.evaluate(CONVERT_JS)
        page.add_style_tag(content=FREEZE_CSS)
        rules = page.evaluate("""() => { const out = []; function walk(rs, media) { for (const r of rs) {
            if (r.type === 1) out.push({ media, sel: r.selectorText, css: r.style.cssText });
            else if (r.type === 4) walk(r.cssRules, media ? media + ' and ' + r.media.mediaText : r.media.mediaText);
            else if (r.type === 7) out.push({ media, kf: r.name, css: r.cssText }); } }
            for (const ss of document.styleSheets) { try { walk(ss.cssRules, ''); } catch (e) {} } return out; }""")
        series = page.evaluate("() => __B.S.map(s => ({id: s.id, name: s.name, one: s.one, why: s.why, zh: s.zh, rad: s.rad, uif: s.uif, wm: s.wm, L: s.L, D: s.D, ic: s.ic}))")
        for s in series:
            sid = s["id"]
            if only and sid not in only:
                continue
            p_ = f"c{sid.lower()}"
            states = {st: page.evaluate("([id, st]) => __B.mascot(__B.BY[id], st)", [sid, st]) for st in ("idle", "think", "happy", "warn")}
            appicon = page.evaluate("id => __B.glyph(__B.BY[id])", sid)
            res = page.evaluate("""([id, label]) => {
                const old = document.getElementById('cw-host'); if (old) old.remove();
                const host = document.createElement('div'); host.id = 'cw-host'; host.className = 'series s-' + id + ' cw-sbx';
                host.style.cssText = 'position:absolute;left:0;top:0;width:480px;padding:0;margin:0;border:0;z-index:99';
                host.innerHTML = '<div class="op op-' + id + '" data-op="' + id + '">' + __B.opening(__B.BY[id]) + '</div>' +
                  '<div style="padding:0"><div class="wordmark" style="display:inline-block;padding:4px 6px;font-size:72px;line-height:1.15;background:none">' + __B.wmLetters(__B.BY[id]) + '</div></div>';
                document.body.appendChild(host);
                const op = __conv(host.querySelector('.op'), 'c' + id.toLowerCase(), label);
                const wm = __conv(host.querySelector('.wordmark'), 'c' + id.toLowerCase() + 'w', 'SYNOX');
                return { op, wm };
            }""", [sid, f"{s['name']}：開場"])
            for part in ("op", "wm"):
                for w in res[part]["warn"]:
                    print(f"  {sid} {part}: {w}")
            slots = {}
            for st, svg in states.items():
                slots[f"mascot.{st}"] = re.sub(r"^<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', svg.replace(' role="img"', "").replace(' aria-label="', ' aria-label="', 1))
            slots["appicon"] = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="{s["name"]}">{appicon}</svg>'
            for part, key in (("op", "opening"), ("wm", "wordmark")):
                svg = res[part]["svg"]
                for i, g in enumerate(res[part]["glyphs"]):
                    svg = svg.replace(f"@@G{i}@@", glyph_path(g, cache, fonts))
                slots[key] = svg
            kfp = keyframe_prefix(sid)
            extras_l = {k: v for k, v in s["L"].items() if k not in KEYMAP}
            extras_d = {k: v for k, v in s["D"].items() if k not in KEYMAP}
            extra_names = sorted(set(extras_l) | set(extras_d))

            def fix(text):
                return rename_extras(rename_vars(text), extra_names)
            light = {RENAME.get(k, k): v for k, v in s["L"].items() if k in KEYMAP or k in RENAME}
            light.update({f"th-{k}": fix(v) for k, v in extras_l.items()})
            var_classes = {}
            for key in slots:
                svg = fix(slots[key])
                svg = prefix_ids(svg, p_)
                svg = strip_filters(svg)
                svg = lift_styles(svg, p_, var_classes)
                svg = add_fallbacks(svg, light)
                slots[key] = svg
            # ── CSS：挑出這個系列用到的規則，轉成 [data-slot] 底下的
            cands = []
            for r in rules:
                if "sel" not in r:
                    continue
                media = r["media"] or ""
                if media and "prefers-color-scheme" not in media:
                    continue
                for sel in split_top(r["sel"]):
                    t = translate_selector(sel, sid)
                    if t:
                        cands.append((media, t, r["css"]))
            kinds = page.evaluate("""([slots, sels]) => {
                const old = document.getElementById('cw-test'); if (old) old.remove();
                const host = document.createElement('div'); host.id = 'cw-test';
                host.innerHTML = Object.entries(slots).map(([k, v]) => '<div data-slot="' + k + '">' + v + '</div>').join('');
                document.body.appendChild(host);
                const out = __classify(host, sels); host.remove(); return out; }""", [slots, [c[1] for c in cands]])
            kf_names = {r["kf"] for r in rules if "kf" in r}
            used, out_rules = set(), []

            def anim(value):
                parts = []
                for part in split_top(value):
                    toks = part.split()
                    for i, tok in enumerate(toks):
                        if tok in kf_names:
                            used.add(tok)
                            toks[i] = f"{kfp}{tok}"
                    parts.append(" ".join(toks))
                return ", ".join(parts)
            for (media, sel, css), kind in zip(cands, kinds):
                if kind not in ("converted", "internal"):
                    continue
                keep, derived = [], []
                for k, v in decls_of(css):
                    if k in ("animation", "animation-name"):
                        v = anim(v)
                    if kind == "internal":
                        keep.append((k, v))
                        continue
                    if CONVERTED_PROPS.match(k):
                        keep.append((k, v))
                    elif k in ("background", "background-color") and re.match(r"^(var\(--[\w-]+\)|#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|transparent|none)$", v):
                        derived.append((" > .v > .bg", "fill", "none" if v in ("none", "transparent") else v))
                    elif k in ("border", "border-color") and not re.search(r"gradient", v):
                        col = [t for t in split_top(v, " ") if re.match(r"^(var\(|#|rgb)", t)]
                        if col:
                            derived.append((" > .v > .bd", "stroke", col[-1]))
                    elif k == "text-shadow" and v != "none":
                        # 字標的字是路徑，不是文字：text-shadow 改成一樣效果的 drop-shadow
                        keep.append(("filter", " ".join(f"drop-shadow({sh})" for sh in split_top(v))))
                    elif k == "box-shadow":
                        col = re.findall(r"(var\(--[\w-]+\)|rgba?\([^)]*\)|#[0-9a-fA-F]{3,8})", v)
                        if col and "inset" not in v:
                            derived.append((" > .v > .sh", "fill", col[-1]))
                if keep:
                    out_rules.append((media, sel, keep))
                for suffix, prop, val in derived:
                    out_rules.append((media, sel + suffix, [(prop, val)]))
            css_lines = [f"/* {s['name']}（系列 {sid}）· SYNOX 衣櫃 · 由 scripts/build_costumes.py 從品牌提案頁產生，不要手改 */"]
            # 減少動態：SYNOX 網頁自己全域關掉動畫（這裡寫不了 !important）
            if extras_l:
                css_lines.append("/* 這一套自己多用的顏色（不是合約 token；SYNOX 的 :root 只收 token 與 --th- 開頭的） */")
                css_lines.append(":root{" + ";".join(f"--th-{k}:{fix(v)}" for k, v in extras_l.items()) + "}")
                dark = ";".join(f"--th-{k}:{fix(v)}" for k, v in extras_d.items())
                css_lines.append('@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){' + dark + "}}")
                css_lines.append(':root[data-theme="dark"]{' + dark + "}")
            for media, sel, decls in out_rules:
                body = ";".join(f"{fix(k) if k.startswith('--') else k}:{fix(v)}" for k, v in decls)
                rule = f"{sel}{{{body}}}"
                css_lines.append(f"@media {media}{{{rule}}}" if media else rule)
            if var_classes:
                css_lines.append("/* 開場、字標裡每個元素自己的變數（原本寫在 style=\"…\"） */")
                for decl, cls in var_classes.items():
                    css_lines.append(f"[data-slot] .{cls}{{{fix(decl)}}}")
            for r in rules:
                if r.get("kf") in used:
                    text = re.sub(r"^@keyframes\s+[\w-]+", f"@keyframes {kfp}{r['kf']}", r["css"])
                    css_lines.append(fix(text))
            theme_css = "\n".join(css_lines) + "\n"
            write_costume(s, slots, theme_css, extras_l, extras_d)
        browser.close()


def tokens_of(s: dict, mode: str) -> dict:
    src = s["L"] if mode == "light" else s["D"]
    out = {KEYMAP[k]: v for k, v in src.items() if k in KEYMAP}
    out["--radius"] = s["rad"]
    out["--font-ui"] = s["uif"].replace('"', "'")
    out["--font-brand"] = f"'{s['wm']['font']}'," + s["uif"].replace('"', "'")
    return {k: out[k] for k in TOKENS + OPTIONAL_TOKENS if k in out}


def write_costume(s: dict, slots: dict, theme_css: str, extras_l: dict, extras_d: dict) -> None:
    cid = costume_id(s["id"])
    folder = os.path.join(COSTUMES, cid)
    os.makedirs(os.path.join(folder, "slots"), exist_ok=True)
    why = {w.split("|", 1)[0]: w.split("|", 1)[1] for w in s["why"]}
    pack = {"name": cid, "kind": "theme", "version": VERSION, "title": s["name"],
            "summary": f"{s['one']}。SYNOX 衣櫃 · 系列 {s['id']}：整個聊天介面的顏色、吉祥物、開場一起換（只有圖和顏色，不帶程式）。",
            "publisher": PUBLISHER, "stage": STAGE,
            "theme": {"contract": CONTRACT, "tokens": {"light": tokens_of(s, "light"), "dark": tokens_of(s, "dark")},
                      "slots": dict(SLOT_FILES), "css": "theme.css", "fonts": []}}
    with open(os.path.join(folder, "pack.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(pack, ensure_ascii=False, indent=2) + "\n")
    for key, rel in SLOT_FILES.items():
        with open(os.path.join(folder, *rel.split("/")), "w", encoding="utf-8", newline="\n") as f:
            f.write(slots[key] + "\n")
    with open(os.path.join(folder, "theme.css"), "w", encoding="utf-8", newline="\n") as f:
        f.write(theme_css)
    info = {"letter": s["id"], "one": s["one"], "tagline": s["zh"], "personality": why.get("個性", ""),
            "fit": why.get("為什麼適合", ""), "caution": [t.strip() for t in re.split(r"(?<=。)", why.get("要注意", "")) if t.strip()],
            "wordmark": s["wm"].get("spec", ""), "wordmark_font": s["wm"]["font"], "ic": s["ic"][0],
            "extras": {"light": extras_l, "dark": extras_d}}
    with open(os.path.join(folder, "about.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(info, ensure_ascii=False, indent=2) + "\n")
    print(f"{cid}: " + ", ".join(f"{k} {len(v.encode())}B" for k, v in slots.items()) + f", css {len(theme_css.encode())}B")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SYNOX 衣櫃：產生、檢查、打包 26 套外觀，產生公開頁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract", help="品牌提案頁 → costumes/<id>/")
    e.add_argument("--brand", required=True)
    e.add_argument("--fonts-cache", default=os.path.join(ROOT, ".cache", "costume-fonts"))
    e.add_argument("--only", nargs="*", help="只做這幾個系列（字母）")
    pk = sub.add_parser("pack", help="costumes/ → .synoxpack")
    pk.add_argument("--out", default=os.path.join(ROOT, "dist"))
    sub.add_parser("page", help="costumes/ → docs/site/wardrobe.html")
    sub.add_parser("check", help="只檢查")
    args = ap.parse_args(argv)
    if args.cmd == "extract":
        extract(args.brand, args.fonts_cache, set(args.only or []) or None)
        return 0
    if args.cmd == "check":
        problems = [p for f in costume_folders() for p in pack_problems(f)]
        for p in problems:
            print("  - " + p)
        print("❌ 有問題" if problems else f"✅ {len(costume_folders())} 套外觀都過了")
        return 2 if problems else 0
    if args.cmd == "pack":
        result = pack_all(args.out)
        if result["problems"]:
            print("❌ 沒有打包：")
            for p in result["problems"]:
                print("  - " + p)
            return 2
        for e_ in result["entries"]:
            print(f"{e_['file']}  {e_['size']} bytes  sha256 {e_['sha256'][:12]}…")
        return 0
    if args.cmd == "page":
        path = os.path.join(SITE, "wardrobe.html")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(page_html())
        print(path + "（記得跑 scripts/site_stamp.py wardrobe.html）")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
