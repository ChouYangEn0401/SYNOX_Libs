"""🖼️ 網頁轉長圖：把網址或你資料夾裡的 .html 截成一張不截斷的長圖（PNG／JPEG）。kind=cli，用 synox_sdk 的 @tool。

指令：snap（截圖）、settings（看設定）、set（改設定）、status（健康檢查）、list（最近的截圖）。
資料都在 <你的資料夾>/html-snap/：settings.json（設定）、shots/（截好的圖）。
真的渲染在 snapcore.playwright_render；測試把 RENDERER 換成假的（不開瀏覽器、不連網）。
"""

import os
from pathlib import Path
from typing import Literal, Optional

import snapcore
from synox_sdk import Ok, context, fail, serve, tool

RENDERER = None          # None＝snapcore.playwright_render；測試換成假的


def home():
    """這個人的資料夾裡、這個工具的子資料夾（設定與截圖都在這裡）。"""
    user_dir = context().user_data_dir
    if not user_dir:
        fail("needs_config", "找不到你的資料夾：這個工具要從 SYNOX 叫（或設 SYNOX_USER_DATA_DIR 試跑）。")
    return os.path.normpath(os.path.join(user_dir, snapcore.PACK))


def _fail(e):
    fail(e.code, e.message, remedy=e.remedy)


@tool("WRITE", default=True, scenario="使用者想把一個網頁或 html 檔存成一整張長圖時")
def snap(url: str = "", file: Optional[Path] = None, width: Optional[int] = None, scale: Optional[float] = None,
         wait: Optional[float] = None, selector: str = "", dark: Optional[bool] = None,
         format: Optional[Literal["png", "jpeg"]] = None, quality: Optional[int] = None):
    """把網頁截成一整張不截斷的長圖，存到你的資料夾。

    Args:
        url: 網址（http 或 https）
        file: 你資料夾裡的 html 檔
        width: 視窗寬度 px
        scale: 倍率，2 是高解析
        wait: 載入後再等幾秒
        selector: 只截這個 CSS selector 的元素
        dark: 用深色模式
        format: png 或 jpeg
        quality: JPEG 品質 30 到 100
    """
    folder = home()
    try:
        target = snapcore.parse_target(url, str(file) if file else "", context().user_data_dir)
        job = snapcore.make_job(snapcore.load_settings(folder), target, width=width, scale=scale, wait=wait,
                                dark=dark, format=format, quality=quality, selector=selector)
        result = snapcore.snap(job, folder, renderer=RENDERER)
    except snapcore.SnapError as e:
        _fail(e)
    rel = os.path.relpath(result["path"], context().user_data_dir)
    lines = [f"🖼️ 存好了：{rel}",
             f"{result['width']}×{result['height']} px，{snapcore.human_size(result['bytes'])}，"
             f"{result['format'].upper()}，用 {snapcore.BROWSER_LABEL.get(result['browser'], result['browser'] or '瀏覽器')}"]
    if result["truncated"]:
        lines.append(f"⚠️ 網頁有 {result['page_height']} px 高，超過上限，只截了前 {result['captured_height']} px"
                     "（/snap_set max_height 可以調高；太大會被總像素上限擋）")
    status = result.get("http_status")
    if status and status >= 400:
        lines.append(f"⚠️ 網站回 {status}：截到的可能是錯誤頁")
    lines.append(f"完整路徑：{result['path']}")
    return Ok("\n".join(lines), {k: result[k] for k in ("path", "width", "height", "bytes", "format", "browser",
                                                         "truncated")})


@tool("READ", scenario="使用者想看網頁轉長圖的預設寬度、倍率、格式時")
def settings():
    """看現在的設定（預設寬度、倍率、格式…）。"""
    current = snapcore.load_settings(home())
    lines = ["⚙️ 網頁轉長圖的設定："] + snapcore.settings_lines(current)
    lines.append("改：/snap_set <名稱> <值>（例如 /snap_set width 1024）；值寫 default＝回到預設")
    return Ok("\n".join(lines), current)


@tool("WRITE", "改一個設定", name="set", scenario="使用者想改網頁轉長圖的預設寬度、倍率、格式、等待秒數、最大高度或瀏覽器時")
def set_(key: Literal["width", "scale", "format", "quality", "wait", "max_height", "browser", "timeout", "dark"],
        value: str):
    """改一個設定。

    Args:
        key: 設定名稱
        value: 新的值，default 是回到預設
    """
    try:
        new, old = snapcore.set_setting(home(), key, value)
    except snapcore.SnapError as e:
        _fail(e)
    return Ok(f"⚙️ {key}：{snapcore.show(old)} → {snapcore.show(new)}", {"key": key, "value": new})


@tool("READ", scenario="網頁轉長圖不能用、想知道缺什麼時")
def status():
    """檢查能不能用：playwright、pillow 裝了沒、找得到哪個瀏覽器。"""
    pkgs = snapcore.package_status()
    found = snapcore.find_browsers()
    pref = snapcore.load_settings(home())["browser"] if context().user_data_dir else "auto"
    lines = ["🩺 網頁轉長圖（alpha）"]
    for name in ("playwright", "pillow"):
        lines.append(f"{'✅' if pkgs[name] else '❌'} {name}：{pkgs[name] or '沒裝（python -m synox packs env rebuild html-snap）'}")
    order = snapcore.browser_order(pref)
    for kind in order:
        lines.append(f"{'✅' if found[kind] else '—'} {snapcore.BROWSER_LABEL[kind]}：{'找到' if found[kind] else '沒找到'}")
    first = next((k for k in order if found[k]), "")
    if first:
        lines.append(f"會先用：{snapcore.BROWSER_LABEL[first]}")
    else:
        lines.append(f"⚠️ 一個瀏覽器都沒找到：裝 Edge 或 Chrome，或在 SYNOX 那台電腦執行 {snapcore.install_hint()}")
    ready = bool(pkgs["playwright"] and pkgs["pillow"] and first)
    lines.append("可以用了" if ready else "還不能用：照上面 ❌／⚠️ 那幾行修")
    return Ok("\n".join(lines), {"ready": ready, "packages": pkgs, "browsers": {k: bool(v) for k, v in found.items()},
                                 "first": first})


@tool("READ", name="list", scenario="使用者想看最近截了哪些長圖時")
def list_():
    """列出最近截的 10 張圖。"""
    folder = home()
    rows = snapcore.recent_shots(folder)
    if not rows:
        return "還沒有截過圖。/snap 網址 試試看。"
    lines = [f"🖼️ 最近的截圖（在 {os.path.join(snapcore.PACK, snapcore.SHOTS_DIR)}）："]
    lines += [f"• {name}（{snapcore.human_size(size)}）" for _t, name, size in rows]
    return "\n".join(lines)


if __name__ == "__main__":
    serve()
