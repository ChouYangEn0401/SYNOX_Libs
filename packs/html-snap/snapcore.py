"""🖼️ 網頁轉長圖的核心（不認識 SYNOX、不碰 synox_sdk）：設定、網址與路徑的安全檢查、切片與拼接、找瀏覽器、Playwright 渲染。

分層：
- 純邏輯（設定、parse_target、request_allowed、plan_limits、assemble、save_image）：測試直接叫，不開瀏覽器、不連網。
- 渲染：render(job) 預設是 playwright_render；測試換成假的（回 Pillow 畫的假切片）。
  playwright、PIL 一律在函式裡才 import：沒裝也能載入這個模組（status 會說缺什麼）。
"""

import io
import json
import os
import re
import shutil
import sys
import time
from urllib.parse import unquote, urlsplit

PACK = "html-snap"
SETTINGS_FILE = "settings.json"
SHOTS_DIR = "shots"

# 設定：鍵 → (型別, 預設, 最小, 最大／選項, 說明)
SPEC = {
    "width": ("int", 1280, 320, 3840, "視窗寬度（CSS px）"),
    "scale": ("number", 2.0, 0.5, 4.0, "倍率（2＝高解析）"),
    "format": ("choice", "png", None, ("png", "jpeg"), "輸出格式"),
    "quality": ("int", 90, 30, 100, "JPEG 品質"),
    "wait": ("number", 1.0, 0.0, 30.0, "載入後再等幾秒"),
    "max_height": ("int", 20000, 1000, 100000, "最多截幾 px 高（CSS px）"),
    "browser": ("choice", "auto", None, ("auto", "edge", "chrome", "chromium"), "先試哪個瀏覽器"),
    "timeout": ("int", 60, 10, 300, "開網頁最多等幾秒"),
    "dark": ("bool", False, None, None, "預設用深色模式"),
}
KEYS = tuple(SPEC)
MAX_PIXELS = 120_000_000          # 輸出圖最多幾個像素（RGB 約 360 MB）：超過就少截一點，不吃爆記憶體
JPEG_MAX_SIDE = 65000             # JPEG 一邊最多 65535
TEXTURE_LIMIT = 16000             # Chromium 單張截圖的裝置像素上限（約 16384）；每片不超過它
SLICE_CSS = 6000                  # 每片最多幾個 CSS px 高（原本工具的值）
MAX_URL = 2000
HTML_EXT = (".html", ".htm", ".xhtml")
TRUE_WORDS = ("1", "true", "yes", "y", "on", "是", "要", "開")
FALSE_WORDS = ("0", "false", "no", "n", "off", "否", "不要", "關")
BROWSER_ORDER = ("edge", "chrome", "chromium")
BROWSER_LABEL = {"edge": "Microsoft Edge", "chrome": "Google Chrome", "chromium": "Playwright 內建 Chromium"}
CHANNEL = {"edge": "msedge", "chrome": "chrome", "chromium": None}


class SnapError(Exception):
    """給人看的錯：code 是 SDK 的失敗類別碼（bad_input、not_found、dependency_missing、timeout…）。"""

    def __init__(self, code, message, remedy=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.remedy = remedy


# ── 設定 ────────────────────────────────────────────────────────────────

def coerce(key, value):
    """一個設定值 → 正確型別、在範圍內；不合丟 SnapError(bad_input)。"""
    if key not in SPEC:
        raise SnapError("bad_input", f"沒有 {key} 這個設定（有：{'、'.join(KEYS)}）")
    kind, _default, low, high, label = SPEC[key]
    text = str(value).strip()
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if text.lower() in TRUE_WORDS:
            return True
        if text.lower() in FALSE_WORDS:
            return False
        raise SnapError("bad_input", f"{label}要是 是／否（yes／no）")
    if kind == "choice":
        word = text.lower()
        if key == "format" and word == "jpg":
            word = "jpeg"
        if key == "browser" and word == "msedge":
            word = "edge"
        if word not in high:
            raise SnapError("bad_input", f"{label}只能是 {'／'.join(high)}")
        return word
    try:
        number = float(text.replace(",", "")) if not isinstance(value, (int, float)) else float(value)
    except ValueError:
        raise SnapError("bad_input", f"{label}要是數字")
    if number != number or not (low <= number <= high):
        raise SnapError("bad_input", f"{label}要在 {low:g}～{high:g} 之間")
    if kind == "int":
        if number != int(number):
            raise SnapError("bad_input", f"{label}要是整數")
        return int(number)
    return float(number)


def defaults():
    return {k: SPEC[k][1] for k in KEYS}


def load_settings(home):
    """home/settings.json；壞掉或不見＝預設值（壞掉的單一項丟掉，其他照讀）。"""
    out = defaults()
    try:
        with open(os.path.join(home, SETTINGS_FILE), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return out
    if isinstance(raw, dict):
        for key in KEYS:
            if key in raw:
                try:
                    out[key] = coerce(key, raw[key])
                except SnapError:
                    pass
    return out


def save_settings(home, settings):
    os.makedirs(home, exist_ok=True)
    clean = {k: settings[k] for k in KEYS if k in settings}
    tmp = os.path.join(home, SETTINGS_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(home, SETTINGS_FILE))
    return clean


def set_setting(home, key, value):
    """改一個設定；value 是 default／預設 → 回到預設。回 (新值, 舊值)。"""
    key = str(key or "").strip().lower()
    if key not in SPEC:
        raise SnapError("bad_input", f"沒有 {key or '（空白）'} 這個設定（有：{'、'.join(KEYS)}）")
    settings = load_settings(home)
    old = settings[key]
    if str(value).strip().lower() in ("default", "reset", "預設"):
        new = SPEC[key][1]
    else:
        new = coerce(key, value)
    settings[key] = new
    save_settings(home, settings)
    return new, old


def show(value):
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def settings_lines(settings):
    lines = []
    for key in KEYS:
        kind, default, low, high, label = SPEC[key]
        if kind in ("int", "number"):
            rng = f"{low:g}～{high:g}"
        elif kind == "choice":
            rng = "／".join(high)
        else:
            rng = "是／否"
        mark = "" if settings[key] == default else f"（預設 {show(default)}）"
        lines.append(f"• {key} = {show(settings[key])}{mark}　{label}，{rng}")
    return lines


# ── 網址與路徑 ──────────────────────────────────────────────────────────

def _inside(root, path):
    """path 在 root 底下（解析符號連結之後）。不同磁碟機、網路路徑＝不在。"""
    if str(path).replace("\\", "/").startswith(("//", "/??/")):
        return False
    try:
        base = os.path.normcase(os.path.realpath(root))
        target = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([base, target]) == base
    except (OSError, ValueError):
        return False


def local_file(user_dir, raw):
    """使用者資料夾裡的一個 .html → 絕對路徑。不在裡面、不存在、不是 .html 一律拒。"""
    text = str(raw or "").strip()
    if not text:
        raise SnapError("bad_input", "沒有給檔名")
    if any(ord(c) < 32 for c in text):
        raise SnapError("bad_input", "檔名不能有換行或控制字元")
    if text.replace("\\", "/").startswith(("//", "/??/")):
        raise SnapError("bad_input", "不能用網路路徑（\\\\主機\\…）")
    if not user_dir:
        raise SnapError("needs_config", "找不到你的資料夾（要從 SYNOX 叫這個工具）")
    full = text if os.path.isabs(text) else os.path.join(user_dir, text)
    if not _inside(user_dir, full):
        raise SnapError("permission_denied", "這個檔不在你的資料夾裡：只能截你自己資料夾裡的 .html")
    full = os.path.realpath(full)
    if not full.lower().endswith(HTML_EXT):
        raise SnapError("bad_input", "只收 .html／.htm 檔")
    if not os.path.isfile(full):
        raise SnapError("not_found", f"找不到這個檔：{text}")
    return full


def parse_target(url=None, file=None, user_dir=""):
    """要截哪一頁 → {"kind": "url"|"file", "url": 給瀏覽器的網址, "path": 本機檔（file 才有）, "root": 本機檔准讀的資料夾, "label"}。
    只收 http／https，或使用者資料夾裡的 .html（file= 或 file:// 網址或沒有開頭的 x.html）。"""
    url = str(url or "").strip()
    file = str(file or "").strip()
    if url and file:
        raise SnapError("bad_input", "網址和檔案只能給一個")
    if not url and not file:
        raise SnapError("bad_input", "要給網址（http／https）或你資料夾裡的 .html 檔名（file=…）")
    if url:
        if len(url) > MAX_URL:
            raise SnapError("bad_input", f"網址太長（最多 {MAX_URL} 字）")
        if any(ord(c) < 32 or c.isspace() for c in url):
            raise SnapError("bad_input", "網址不能有空白或控制字元")
        scheme = url.split(":", 1)[0].lower() if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", url) else ""
        if scheme == "file":
            parts = urlsplit(url)
            if parts.netloc not in ("", "localhost"):
                raise SnapError("permission_denied", "file:// 不能指到別台電腦")
            path = unquote(parts.path)
            if re.match(r"^/[A-Za-z]:", path):            # file:///C:/x.html → C:/x.html
                path = path[1:]
            file = path
        elif scheme in ("http", "https"):
            parts = urlsplit(url)
            if not parts.hostname:
                raise SnapError("bad_input", "網址少了網站名稱")
            return {"kind": "url", "url": url, "path": "", "root": "", "label": parts.hostname}
        elif re.match(r"^[A-Za-z]:[\\/]", url) or url.startswith(("/", "\\")):
            file = url                                      # C:\…、/…：本機路徑（下面檢查在不在你的資料夾裡）
        elif scheme:
            raise SnapError("permission_denied", f"不收 {scheme}: 開頭的網址：只收 http、https，或你資料夾裡的 .html")
        elif url.lower().endswith(HTML_EXT) and not re.match(r"^[\w-]+(\.[\w-]+)+/", url):
            file = url                                      # 「report.html」「sub/report.html」：當成你資料夾裡的檔
        else:
            if "." not in url.split("/", 1)[0]:
                raise SnapError("bad_input", "看不懂這個網址：要 http:// 或 https:// 開頭")
            return parse_target("https://" + url, None, user_dir)
    path = local_file(user_dir, file)
    stem = os.path.splitext(os.path.basename(path))[0]
    return {"kind": "file", "url": _file_uri(path), "path": path, "root": os.path.dirname(path), "label": stem}


def _file_uri(path):
    from pathlib import Path
    return Path(path).as_uri()


def request_allowed(url, target):
    """瀏覽器每個請求（含子資源、轉址）都問這裡：http／https／data／blob／about 可以；
    file: 只有截本機檔時、而且在那個 .html 的同一個資料夾（或子資料夾）裡才可以；其他（javascript:、chrome:…）一律擋。"""
    scheme = str(url).split(":", 1)[0].lower()
    if scheme in ("http", "https", "data", "blob", "about"):
        return True
    if scheme == "file" and target.get("kind") == "file":
        parts = urlsplit(url)
        if parts.netloc not in ("", "localhost"):
            return False
        path = unquote(parts.path)
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        return _inside(target["root"], path)
    return False


# ── 這一次怎麼截 ────────────────────────────────────────────────────────

def make_job(settings, target, **overrides):
    """設定＋這一次的選項（None＝用設定）→ 一份要交給 renderer 的工作。選項一樣照 SPEC 檢查範圍。"""
    job = dict(settings)
    for key, value in overrides.items():
        if key == "selector":
            continue
        if value is not None and value != "":
            job[key] = coerce(key, value)
    selector = str(overrides.get("selector") or "").strip()
    if len(selector) > 300 or any(ord(c) < 32 for c in selector):
        raise SnapError("bad_input", "CSS selector 太長或有控制字元")
    job["selector"] = selector
    job["target"] = target
    job["fmt"] = job.pop("format")
    return job


def plan_limits(css_width, scale, max_height, fmt):
    """這個寬度與倍率下最多截幾個 CSS px 高（max_height、總像素上限、JPEG 一邊上限取最小），與每片幾個 CSS px。"""
    css_width = max(1, int(css_width))
    by_pixels = int(MAX_PIXELS / (css_width * scale * scale))
    limit = min(int(max_height), by_pixels)
    if fmt == "jpeg":
        limit = min(limit, int(JPEG_MAX_SIDE / scale))
    slice_h = max(500, min(SLICE_CSS, int(TEXTURE_LIMIT / scale)))
    return max(1, limit), slice_h


def slices(top, height, slice_h):
    """從 top 開始截 height 個 CSS px，切成每片最多 slice_h：[(y, h), …]。"""
    out = []
    y = 0
    while y < height:
        h = min(slice_h, height - y)
        out.append((top + y, h))
        y += h
    return out


def assemble(parts):
    """切片（PNG bytes）由上往下拼成一張 RGB 圖。寬度不一樣的以最寬的為準、靠左。"""
    from PIL import Image
    if not parts:
        raise SnapError("internal_bug", "沒有截到任何東西")
    images = [Image.open(io.BytesIO(b)) for b in parts]
    try:
        width = max(i.width for i in images)
        height = sum(i.height for i in images)
        canvas = Image.new("RGB", (width, height), "white")
        y = 0
        for im in images:
            canvas.paste(im.convert("RGB"), (0, y))
            y += im.height
        return canvas
    finally:
        for im in images:
            im.close()


def save_image(image, path, fmt, quality):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    if fmt == "jpeg":
        image.save(tmp, "JPEG", quality=int(quality), optimize=True)
    else:
        image.save(tmp, "PNG", optimize=False)
    os.replace(tmp, path)
    return os.path.getsize(path)


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return (s[:40].strip("-") or "page")


def out_path(home, label, fmt, now=None):
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    ext = "jpg" if fmt == "jpeg" else "png"
    folder = os.path.normpath(os.path.join(home, SHOTS_DIR))
    path = os.path.join(folder, f"{slug(label)}-{stamp}.{ext}")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{slug(label)}-{stamp}-{n}.{ext}")
        n += 1
    return path


def recent_shots(home, limit=10):
    folder = os.path.join(home, SHOTS_DIR)
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith((".png", ".jpg"))]
    except OSError:
        return []
    rows = []
    for n in names:
        p = os.path.join(folder, n)
        try:
            rows.append((os.path.getmtime(p), n, os.path.getsize(p)))
        except OSError:
            pass
    rows.sort(reverse=True)
    return rows[:limit]


def human_size(n):
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{max(1, round(n / 1024))} KB"


def snap(job, home, renderer=None, now=None):
    """截一張：renderer(job) → {"parts": [PNG bytes…], "page_height", "captured_height", "css_width", "browser", "status"}；
    拼起來、存到 home/shots/。回結果 dict（路徑、尺寸、大小、有沒有被截短…）。"""
    renderer = renderer or playwright_render
    shot = renderer(job)
    image = assemble(shot["parts"])
    try:
        path = out_path(home, job["target"]["label"], job["fmt"], now)
        size = save_image(image, path, job["fmt"], job["quality"])
        width, height = image.size
    finally:
        image.close()
    return {"path": path, "width": width, "height": height, "bytes": size, "format": job["fmt"],
            "browser": shot.get("browser", ""), "truncated": shot["captured_height"] < shot["page_height"],
            "page_height": shot["page_height"], "captured_height": shot["captured_height"],
            "http_status": shot.get("status")}


# ── 瀏覽器 ──────────────────────────────────────────────────────────────

def _env(name):
    return os.environ.get(name) or ""


def browser_candidates(kind):
    """這台電腦上這種瀏覽器可能在的位置（只看檔案在不在，不執行）。"""
    if sys.platform.startswith("win"):
        roots = [_env("PROGRAMFILES(X86)"), _env("PROGRAMFILES"), _env("LOCALAPPDATA")]
        tail = {"edge": ("Microsoft", "Edge", "Application", "msedge.exe"),
                "chrome": ("Google", "Chrome", "Application", "chrome.exe")}[kind]
        return [os.path.join(r, *tail) for r in roots if r]
    if sys.platform == "darwin":
        return {"edge": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
                "chrome": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]}[kind]
    names = {"edge": ("microsoft-edge-stable", "microsoft-edge"), "chrome": ("google-chrome-stable", "google-chrome")}[kind]
    fixed = {"edge": ["/opt/microsoft/msedge/msedge"], "chrome": ["/opt/google/chrome/chrome"]}[kind]
    return fixed + [p for p in (shutil.which(n) for n in names) if p]


def playwright_browsers_dirs():
    custom = _env("PLAYWRIGHT_BROWSERS_PATH")
    if custom and custom != "0":
        return [custom]
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        base = _env("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return [os.path.join(base, "ms-playwright")]
    if sys.platform == "darwin":
        return [os.path.join(home, "Library", "Caches", "ms-playwright")]
    return [os.path.join(home, ".cache", "ms-playwright")]


def find_browsers():
    """{edge: 路徑或 ""、chrome: …、chromium: …}（chromium＝Playwright 下載的那份）。"""
    found = {}
    for kind in ("edge", "chrome"):
        found[kind] = next((p for p in browser_candidates(kind) if os.path.isfile(p)), "")
    found["chromium"] = ""
    for folder in playwright_browsers_dirs():
        try:
            names = sorted(n for n in os.listdir(folder) if n.startswith("chromium"))
        except OSError:
            continue
        if names:
            found["chromium"] = os.path.join(folder, names[-1])
            break
    return found


def browser_order(pref):
    pref = pref if pref in BROWSER_ORDER else "auto"
    if pref == "auto":
        return list(BROWSER_ORDER)
    return [pref] + [b for b in BROWSER_ORDER if b != pref]


def install_hint():
    return f'"{sys.executable}" -m playwright install chromium'


def no_browser_error(tried):
    return SnapError("dependency_missing",
                     f"找不到能用的瀏覽器（試過：{'、'.join(BROWSER_LABEL[b] for b in tried)}）。"
                     f"修法：裝 Microsoft Edge 或 Google Chrome；或在 SYNOX 那台電腦執行這一行裝內建 Chromium：{install_hint()}",
                     remedy={"kind": "needs_owner", "id": "install_browser", "summary": "裝 Edge／Chrome，或裝 Playwright 內建 Chromium"})


def package_status():
    """playwright、pillow 裝了沒（版本）；沒裝的回 ""。只 import，不開瀏覽器。"""
    out = {}
    try:
        import PIL
        out["pillow"] = str(PIL.__version__)
    except Exception:
        out["pillow"] = ""
    try:
        from playwright import _repo_version
        out["playwright"] = str(_repo_version.version)
    except Exception:
        try:
            import playwright  # noqa: F401
            out["playwright"] = "已裝"
        except Exception:
            out["playwright"] = ""
    return out


def classify_nav_error(text):
    """Playwright 開網頁失敗的訊息 → (類別碼, 人話)。"""
    t = str(text)
    if "Timeout" in t or "timeout" in t:
        return "timeout", "網頁太久沒開好（超過設定的 timeout 秒數）"
    if "ERR_NAME_NOT_RESOLVED" in t:
        return "bad_input", "找不到這個網站（網址打錯，或這台電腦沒網路）"
    if "ERR_INTERNET_DISCONNECTED" in t or "ERR_NETWORK_CHANGED" in t:
        return "network_unreachable", "這台電腦現在連不上網路"
    if "ERR_CERT" in t or "SSL" in t:
        return "upstream_unavailable", "網站的安全憑證有問題，瀏覽器拒絕開"
    if "ERR_ABORTED" in t or "ERR_BLOCKED" in t:
        return "permission_denied", "這個網址被擋下來（不准的開頭或本機檔）"
    if "ERR_CONNECTION" in t:
        return "upstream_unavailable", "網站沒有回應（連線被拒或中斷）"
    return "upstream_unavailable", "網頁開不起來"


# 捲到底觸發 lazy load（最多捲到 limit），再捲回頂端
SCROLL_JS = """async (limit) => {
  const h = () => Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0);
  for (let y = 0; y < Math.min(h(), limit); y += 600) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 80)); }
  window.scrollTo(0, 0);
}"""
SIZE_JS = """() => [Math.max(document.documentElement.scrollWidth, window.innerWidth),
  Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)]"""
RECT_JS = """(el) => { const r = el.getBoundingClientRect();
  return [r.left + window.scrollX, r.top + window.scrollY, r.width, r.height]; }"""


def launch(p, pref):
    """照偏好試 Edge → Chrome → Playwright 內建 Chromium；回 (browser, 用的是哪個)。都不行丟 SnapError。"""
    tried = []
    for kind in browser_order(pref):
        tried.append(kind)
        try:
            channel = CHANNEL[kind]
            return (p.chromium.launch(channel=channel) if channel else p.chromium.launch()), kind
        except Exception:
            continue
    raise no_browser_error(tried)


def playwright_render(job):
    """真的開瀏覽器截圖（測試不會走這裡）。"""
    try:
        from playwright.sync_api import Error as PwError
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SnapError("dependency_missing", "這個工具的環境沒建好（少了 playwright）。修法：python -m synox packs env rebuild html-snap",
                        remedy={"kind": "needs_owner", "id": "rebuild_env", "summary": "重建這個工具的環境"})
    target = job["target"]
    timeout_ms = int(job["timeout"] * 1000)
    with sync_playwright() as p:
        browser, used = launch(p, job["browser"])
        try:
            ctx = browser.new_context(viewport={"width": job["width"], "height": 900}, device_scale_factor=job["scale"],
                                      color_scheme="dark" if job["dark"] else "light", accept_downloads=False)
            ctx.set_default_timeout(timeout_ms)

            def guard(route):
                if request_allowed(route.request.url, target):
                    route.continue_()
                else:
                    route.abort("blockedbyclient")

            ctx.route("**/*", guard)
            page = ctx.new_page()
            try:
                response = page.goto(target["url"], wait_until="load", timeout=timeout_ms)
            except PwError as e:
                code, text = classify_nav_error(str(e))
                raise SnapError(code, text)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
            except PwError:
                pass                                         # 一直有請求的網頁（廣告、輪詢）：不等了，照樣截
            limit_css, slice_h = plan_limits(job["width"], job["scale"], job["max_height"], job["fmt"])
            page.evaluate(SCROLL_JS, limit_css)
            page.wait_for_timeout(int(job["wait"] * 1000))
            page.evaluate("() => document.fonts ? document.fonts.ready.then(() => true) : true")
            css_w, css_h = page.evaluate(SIZE_JS)
            page.set_viewport_size({"width": int(css_w), "height": int(min(css_h, 2000))})
            page.wait_for_timeout(200)
            css_w, css_h = page.evaluate(SIZE_JS)
            left, top, width, height = 0, 0, int(css_w), int(css_h)
            if job["selector"]:
                try:
                    el = page.query_selector(job["selector"])
                except PwError:
                    raise SnapError("bad_input", "CSS selector 寫錯了（瀏覽器看不懂）")
                if el is None:
                    raise SnapError("not_found", f"網頁裡找不到 {job['selector']}")
                left, top, width, height = (int(round(v)) for v in page.evaluate(RECT_JS, el))
                if width <= 0 or height <= 0:
                    raise SnapError("not_found", f"{job['selector']} 是看不見的元素（寬或高是 0）")
            limit_css, slice_h = plan_limits(width, job["scale"], job["max_height"], job["fmt"])
            captured = min(height, limit_css)
            parts = [page.screenshot(full_page=True, type="png",
                                     clip={"x": left, "y": y, "width": width, "height": h})
                     for y, h in slices(top, captured, slice_h)]
            return {"parts": parts, "page_height": height, "captured_height": captured, "css_width": width,
                    "browser": used, "status": response.status if response is not None else None}
        except PwError as e:
            code, text = classify_nav_error(str(e))
            raise SnapError(code, text)
        finally:
            browser.close()
