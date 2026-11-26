"""🖼️ 網頁轉長圖（html-snap，kind=cli）：不開瀏覽器、不連網。

- 渲染換成假的 renderer（回 Pillow 畫的假切片，或連拼接都換掉），測參數、設定、拼接、存檔、成功與失敗信封；
- 網址與路徑的安全檢查（只收 http／https 與使用者資料夾裡的 .html；file:// 指到別處、javascript: 一律拒）；
- 瀏覽器的順序與找不到時的人話（假的 playwright 物件，不真的開）；
- pack.json 的 cli.commands 跟 tool.py 的 @tool 對得上（SDK 的 commands_for）。
測試環境（SYNOX 的 venv）沒裝 playwright、pillow 也能跑：需要 Pillow 的幾條（真的拼圖）用 importorskip。
"""

import importlib.util
import io
import json
import os
import sys

import pytest

from conftest import PACKS

FOLDER = os.path.join(PACKS, "html-snap")


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(FOLDER, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mods():
    """照 SYNOX 跑 `{python} ./tool.py` 的樣子：tool.py 旁邊的 snapcore、synox_sdk 用同名模組 import。"""
    saved = {k: sys.modules.get(k) for k in ("snapcore", "synox_sdk")}
    sdk = _load("synox_sdk", "synox_sdk.py")
    core = _load("snapcore", "snapcore.py")
    tool = _load("html_snap_tool", "tool.py")
    yield sdk, core, tool
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
    sys.modules.pop("html_snap_tool", None)


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    folder = tmp_path / "user"
    folder.mkdir()
    monkeypatch.setenv("SYNOX_USER_DATA_DIR", str(folder))
    return folder


def run(mods, *argv):
    """跟 SYNOX 一樣叫命令列殼（帶 --json），回最後一行的信封。"""
    sdk, _core, tool = mods
    out = io.StringIO()
    code = sdk.run_cli(list(argv) + ["--json"], out=out,
                       tools=[tool.snap, tool.settings, tool.set_, tool.status, tool.list_])
    envelope = json.loads(out.getvalue().strip().splitlines()[-1])
    assert (code == 0) == envelope["ok"], envelope
    return envelope


def fake_slices(colors, width=40, height=30):
    from PIL import Image
    out = []
    for c in colors:
        buf = io.BytesIO()
        Image.new("RGB", (width, height), c).save(buf, "PNG")
        out.append(buf.getvalue())
    return out


class FakeImage:
    """沒裝 Pillow 時代替拼好的圖：只要 size、close。"""

    def __init__(self, w, h):
        self.size = (w, h)

    def close(self):
        pass


@pytest.fixture
def fake_render(mods, monkeypatch):
    """假的 renderer：記下收到的工作；每片高 = 切片高。拼接、存檔換成不靠 Pillow 的（寫幾個位元組）。"""
    _sdk, core, tool = mods
    seen = []
    page = {"height": 2500, "status": 200}

    def renderer(job):
        seen.append(job)
        limit, slice_h = core.plan_limits(job["width"], job["scale"], job["max_height"], job["fmt"])
        captured = min(page["height"], limit)
        parts = [(y, h) for y, h in core.slices(0, captured, slice_h)]
        return {"parts": parts, "page_height": page["height"], "captured_height": captured, "css_width": job["width"],
                "browser": "edge", "status": page["status"]}

    def assemble(parts):
        return FakeImage(int(seen[-1]["width"] * seen[-1]["scale"]), int(sum(h for _y, h in parts) * seen[-1]["scale"]))

    def save_image(image, path, fmt, quality):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x" * 2048)
        return 2048

    monkeypatch.setattr(tool, "RENDERER", renderer)
    monkeypatch.setattr(core, "assemble", assemble)
    monkeypatch.setattr(core, "save_image", save_image)
    return seen, page


# ── 設定 ────────────────────────────────────────────────────────────────

def test_settings_defaults_set_and_reset(mods, user_dir):
    env = run(mods, "settings")
    assert env["data"]["width"] == 1280 and env["data"]["format"] == "png" and "width = 1280" in env["message"]
    env = run(mods, "set", "width", "1024")
    assert env["message"] == "⚙️ width：1280 → 1024"
    assert json.loads((user_dir / "html-snap" / "settings.json").read_text(encoding="utf-8"))["width"] == 1024
    assert "（預設 1280）" in run(mods, "settings")["message"]
    assert run(mods, "set", "format", "JPG")["data"]["value"] == "jpeg"
    assert run(mods, "set", "dark", "是")["data"]["value"] is True
    assert run(mods, "set", "browser", "msedge")["data"]["value"] == "edge"
    assert run(mods, "set", "width", "default")["data"]["value"] == 1280


@pytest.mark.parametrize("key,value,needle", [("width", "10", "320～3840"), ("scale", "abc", "數字"),
                                              ("quality", "50.5", "整數"), ("browser", "firefox", "auto／edge"),
                                              ("max_height", "1e9", "1000～100000"), ("dark", "maybe", "是／否")])
def test_set_rejects_bad_values_with_a_failure_envelope(mods, user_dir, key, value, needle):
    env = run(mods, "set", key, value)
    assert env["ok"] is False and env["code"] == "bad_input" and needle in env["error"]
    assert not (user_dir / "html-snap" / "settings.json").exists()


def test_set_unknown_key_is_refused_by_the_sdk(mods, user_dir):
    env = run(mods, "set", "colour", "red")
    assert env["ok"] is False and "只能是" in env["error"]


def test_broken_settings_file_falls_back_per_item(mods, user_dir):
    folder = user_dir / "html-snap"
    folder.mkdir()
    (folder / "settings.json").write_text('{"width": 99999, "scale": 3, "format": "gif"}', encoding="utf-8")
    data = run(mods, "settings")["data"]
    assert data["width"] == 1280 and data["scale"] == 3.0 and data["format"] == "png"
    (folder / "settings.json").write_text("{壞掉", encoding="utf-8")
    assert run(mods, "settings")["data"]["scale"] == 2.0


def test_without_a_user_folder_says_so(mods, monkeypatch):
    monkeypatch.delenv("SYNOX_USER_DATA_DIR", raising=False)
    env = run(mods, "settings")
    assert env["ok"] is False and env["code"] == "needs_config"


# ── 網址與路徑 ──────────────────────────────────────────────────────────

def test_targets_that_are_accepted(mods, tmp_path):
    _sdk, core, _tool = mods
    (tmp_path / "pages").mkdir()
    page = tmp_path / "pages" / "report.html"
    page.write_text("<p>hi</p>", encoding="utf-8")
    t = core.parse_target("https://example.com/a?b=1", None, str(tmp_path))
    assert t["kind"] == "url" and t["url"] == "https://example.com/a?b=1" and t["label"] == "example.com"
    assert core.parse_target("example.com/news", None, str(tmp_path))["url"] == "https://example.com/news"
    for given in ("pages/report.html", str(page), page.as_uri()):
        t = core.parse_target(given, None, str(tmp_path))
        assert t["kind"] == "file" and os.path.samefile(t["path"], page) and t["label"] == "report", given
    t = core.parse_target(None, "pages/report.html", str(tmp_path))
    assert t["url"].startswith("file:") and os.path.samefile(t["root"], tmp_path / "pages")


@pytest.mark.parametrize("url,code", [
    ("javascript:alert(1)", "permission_denied"), ("data:text/html,<p>x", "permission_denied"),
    ("chrome://settings", "permission_denied"), ("ftp://example.com/x", "permission_denied"),
    ("file://otherhost/share/x.html", "permission_denied"), ("http://", "bad_input"),
    ("https://exa mple.com", "bad_input"), ("notaurl", "bad_input"), ("\\\\host\\share\\x.html", "bad_input"),
    ("https://example.com/" + "a" * 2100, "bad_input")])
def test_targets_that_are_refused(mods, tmp_path, url, code):
    _sdk, core, _tool = mods
    with pytest.raises(core.SnapError) as e:
        core.parse_target(url, None, str(tmp_path))
    assert e.value.code == code, e.value.message


def test_local_files_must_stay_inside_the_user_folder(mods, tmp_path):
    _sdk, core, _tool = mods
    inside = tmp_path / "user"
    inside.mkdir()
    outside = tmp_path / "secret.html"
    outside.write_text("<p>secret</p>", encoding="utf-8")
    (inside / "notes.txt").write_text("x", encoding="utf-8")
    cases = {"../secret.html": "permission_denied", str(outside): "permission_denied",
             outside.as_uri(): "permission_denied", "notes.txt": "bad_input", "missing.html": "not_found"}
    for given, code in cases.items():
        with pytest.raises(core.SnapError) as e:
            core.parse_target(None, given, str(inside)) if not given.startswith("file:") \
                else core.parse_target(given, None, str(inside))
        assert e.value.code == code, (given, e.value.message)
    with pytest.raises(core.SnapError) as e:
        core.parse_target("https://example.com", "x.html", str(inside))
    assert "只能給一個" in e.value.message
    with pytest.raises(core.SnapError):
        core.parse_target("", "", str(inside))


def test_browser_requests_are_filtered(mods, tmp_path):
    """截網址時一律不准讀本機檔；截本機檔時只准讀那個 .html 的資料夾（含子資料夾）。"""
    _sdk, core, _tool = mods
    web = {"kind": "url", "root": ""}
    local = {"kind": "file", "root": str(tmp_path / "pages")}
    (tmp_path / "pages" / "img").mkdir(parents=True)
    assert core.request_allowed("https://cdn.example.com/a.css", web)
    assert core.request_allowed("data:image/png;base64,AAAA", web)
    assert not core.request_allowed((tmp_path / "pages" / "a.png").as_uri(), web)
    assert not core.request_allowed("javascript:alert(1)", web)
    assert not core.request_allowed("chrome://settings", web)
    assert core.request_allowed((tmp_path / "pages" / "img" / "a.png").as_uri(), local)
    assert not core.request_allowed((tmp_path / "other.json").as_uri(), local)
    assert not core.request_allowed("file://otherhost/x.png", local)


# ── 尺寸上限與切片 ──────────────────────────────────────────────────────

def test_limits_and_slices(mods):
    _sdk, core, _tool = mods
    limit, slice_h = core.plan_limits(1280, 2, 20000, "png")
    assert limit == 20000 and slice_h == 6000
    limit, slice_h = core.plan_limits(3840, 4, 100000, "png")
    assert limit * 3840 * 16 <= core.MAX_PIXELS and slice_h * 4 <= core.TEXTURE_LIMIT
    limit, _ = core.plan_limits(800, 2, 100000, "jpeg")
    assert limit * 2 <= core.JPEG_MAX_SIDE
    parts = core.slices(100, 13000, 6000)
    assert parts == [(100, 6000), (6100, 6000), (12100, 1000)]
    assert core.slices(0, 0, 6000) == []


def test_make_job_validates_overrides(mods):
    _sdk, core, _tool = mods
    target = {"kind": "url", "url": "https://example.com", "label": "example.com", "root": "", "path": ""}
    job = core.make_job(core.defaults(), target, width="800", scale=None, dark="yes", format="jpg", selector=" #main ")
    assert job["width"] == 800 and job["scale"] == 2.0 and job["dark"] is True and job["fmt"] == "jpeg"
    assert job["selector"] == "#main" and "format" not in job
    with pytest.raises(core.SnapError):
        core.make_job(core.defaults(), target, width=99999)
    with pytest.raises(core.SnapError):
        core.make_job(core.defaults(), target, selector="a\nb")


# ── 截圖（假的 renderer） ───────────────────────────────────────────────

def test_snap_url_with_fake_renderer(mods, user_dir, fake_render):
    seen, _page = fake_render
    run(mods, "set", "width", "1000")
    env = run(mods, "snap", "https://example.com", "--dark=yes", "--selector=#main")
    assert env["ok"], env
    job = seen[-1]
    assert job["width"] == 1000 and job["dark"] is True and job["selector"] == "#main"
    assert job["target"]["url"] == "https://example.com"
    data = env["data"]
    assert data["width"] == 2000 and data["height"] == 5000 and data["format"] == "png" and not data["truncated"]
    assert os.path.isfile(data["path"]) and os.path.dirname(data["path"]) == str(user_dir / "html-snap" / "shots")
    assert os.path.basename(data["path"]).startswith("example-com-")
    assert "2000×5000 px" in env["message"] and "Microsoft Edge" in env["message"]
    listing = run(mods, "list")["message"]
    assert os.path.basename(data["path"]) in listing


def test_snap_local_file_jpeg_and_truncation(mods, user_dir, fake_render):
    seen, page = fake_render
    (user_dir / "report.html").write_text("<p>hi</p>", encoding="utf-8")
    page["height"], page["status"] = 50000, 404
    run(mods, "set", "max_height", "3000")
    env = run(mods, "snap", "--file=report.html", "--format=jpeg", "--quality=70", "--width=600", "--scale=1")
    assert env["ok"], env
    assert seen[-1]["target"]["kind"] == "file" and seen[-1]["quality"] == 70
    assert env["data"]["path"].endswith(".jpg") and env["data"]["truncated"] is True
    assert env["data"]["height"] == 3000
    assert "只截了前 3000 px" in env["message"] and "網站回 404" in env["message"]


def test_snap_refuses_bad_input_before_rendering(mods, user_dir, fake_render):
    seen, _page = fake_render
    for argv, code in ((["snap", "javascript:alert(1)"], "permission_denied"),
                       (["snap", "--file=../x.html"], "permission_denied"),
                       (["snap", "https://example.com", "--width=5"], "bad_input"),
                       (["snap"], "bad_input")):
        env = run(mods, *argv)
        assert env["ok"] is False and env["code"] == code, (argv, env)
        assert env["failure"]["v"] == 1
    assert seen == []


def test_renderer_failures_become_failure_envelopes(mods, user_dir, monkeypatch):
    _sdk, core, tool = mods

    def broken(job):
        raise core.no_browser_error(["edge", "chrome", "chromium"])

    monkeypatch.setattr(tool, "RENDERER", broken)
    env = run(mods, "snap", "https://example.com")
    assert env["ok"] is False and env["code"] == "dependency_missing"
    assert "playwright install chromium" in env["error"] and "Microsoft Edge" in env["error"]
    assert env["failure"]["remedy"]["kind"] == "needs_owner"

    def slow(job):
        raise core.SnapError("timeout", "網頁太久沒開好")

    monkeypatch.setattr(tool, "RENDERER", slow)
    assert run(mods, "snap", "https://example.com")["code"] == "timeout"


@pytest.mark.parametrize("text,code", [("Timeout 60000ms exceeded", "timeout"),
                                       ("net::ERR_NAME_NOT_RESOLVED at https://x", "bad_input"),
                                       ("net::ERR_INTERNET_DISCONNECTED", "network_unreachable"),
                                       ("net::ERR_CERT_DATE_INVALID", "upstream_unavailable"),
                                       ("net::ERR_BLOCKED_BY_CLIENT", "permission_denied"),
                                       ("something else", "upstream_unavailable")])
def test_navigation_errors_are_classified(mods, text, code):
    _sdk, core, _tool = mods
    assert core.classify_nav_error(text)[0] == code


# ── 瀏覽器 ──────────────────────────────────────────────────────────────

class FakeChromium:
    def __init__(self, works):
        self.works = works
        self.tried = []

    def launch(self, channel=None):
        self.tried.append(channel)
        if channel in self.works:
            return f"browser:{channel}"
        raise RuntimeError(f"no {channel}")


class FakePlaywright:
    def __init__(self, works):
        self.chromium = FakeChromium(works)


def test_launch_order_and_fallback(mods):
    _sdk, core, _tool = mods
    p = FakePlaywright({"chrome"})
    assert core.launch(p, "auto") == ("browser:chrome", "chrome") and p.chromium.tried == ["msedge", "chrome"]
    p = FakePlaywright({None, "msedge"})
    assert core.launch(p, "chromium") == ("browser:None", "chromium") and p.chromium.tried == [None]
    p = FakePlaywright(set())
    with pytest.raises(core.SnapError) as e:
        core.launch(p, "chrome")
    assert p.chromium.tried == ["chrome", "msedge", None] and e.value.code == "dependency_missing"
    assert "-m playwright install chromium" in e.value.message


def test_status_reports_packages_and_browsers(mods, user_dir, monkeypatch):
    _sdk, core, _tool = mods
    monkeypatch.setattr(core, "package_status", lambda: {"playwright": "1.65.2", "pillow": "13.0.0"})
    monkeypatch.setattr(core, "find_browsers", lambda: {"edge": "", "chrome": "/x/chrome", "chromium": ""})
    env = run(mods, "status")
    assert env["data"]["ready"] is True and env["data"]["first"] == "chrome" and "會先用：Google Chrome" in env["message"]
    monkeypatch.setattr(core, "package_status", lambda: {"playwright": "", "pillow": ""})
    monkeypatch.setattr(core, "find_browsers", lambda: {"edge": "", "chrome": "", "chromium": ""})
    env = run(mods, "status")
    assert env["ok"] and env["data"]["ready"] is False
    assert "packs env rebuild html-snap" in env["message"] and "playwright install chromium" in env["message"]


def test_status_runs_for_real_without_playwright_or_browsers_launching(mods, user_dir):
    """真的 package_status／find_browsers（只 import、只看檔案在不在）：一定回得出來，不丟例外。"""
    env = run(mods, "status")
    assert env["ok"] and set(env["data"]["browsers"]) == {"edge", "chrome", "chromium"}


def test_playwright_and_pillow_are_imported_lazily(mods):
    """模組最上層不 import playwright、PIL：沒裝也能載入（status 才說得出缺什麼）。"""
    import ast
    for name in ("snapcore.py", "tool.py"):
        with open(os.path.join(FOLDER, name), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods_ = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                assert not any(m.split(".")[0] in ("playwright", "PIL") for m in mods_), (name, mods_)


# ── 真的拼接（要 Pillow） ───────────────────────────────────────────────

def test_assemble_stitches_slices_without_cutting(mods):
    pytest.importorskip("PIL")
    _sdk, core, _tool = mods
    image = core.assemble(fake_slices([(255, 0, 0), (0, 255, 0), (0, 0, 255)]))
    try:
        assert image.size == (40, 90)
        assert image.getpixel((5, 29)) == (255, 0, 0) and image.getpixel((5, 30)) == (0, 255, 0)
        assert image.getpixel((5, 89)) == (0, 0, 255)
    finally:
        image.close()


def test_snap_with_real_stitching_and_saving(mods, user_dir, monkeypatch):
    PIL = pytest.importorskip("PIL")
    from PIL import Image
    _sdk, core, tool = mods

    def renderer(job):
        return {"parts": fake_slices([(10, 20, 30)] * 3, width=50, height=40), "page_height": 120,
                "captured_height": 120, "css_width": 50, "browser": "chromium", "status": 200}

    monkeypatch.setattr(tool, "RENDERER", renderer)
    env = run(mods, "snap", "https://example.com", "--format=jpeg", "--quality=80")
    assert env["ok"], env
    with Image.open(env["data"]["path"]) as im:
        assert im.format == "JPEG" and im.size == (50, 120)
    env = run(mods, "snap", "https://example.com")
    with Image.open(env["data"]["path"]) as im:
        assert im.format == "PNG" and im.size == (50, 120) and env["data"]["bytes"] == os.path.getsize(env["data"]["path"])
    assert PIL


# ── pack.json 與程式對得上 ──────────────────────────────────────────────

def test_pack_json_commands_match_the_tools(mods):
    sdk, _core, tool = mods
    with open(os.path.join(FOLDER, "pack.json"), encoding="utf-8") as f:
        raw = json.load(f)
    declared = {c["name"]: c for c in raw["cli"]["commands"]}
    generated = {c["name"]: c for c in sdk.commands_for([tool.snap, tool.settings, tool.set_, tool.status, tool.list_])}
    assert set(declared) == set(generated) == {"snap", "settings", "set", "status", "list"}
    for name, gen in generated.items():
        dec = declared[name]
        assert dec["argv"] == gen["argv"] and dec["effect"] == gen["effect"], name
        assert [(f["name"], f.get("kind"), f.get("required"), f.get("choices")) for f in dec.get("fields", [])] == \
               [(f["name"], f.get("kind"), f.get("required"), f.get("choices")) for f in gen.get("fields", [])], name
    assert raw["kind"] == "cli" and raw["runtime"]["python"] == "auto" and raw["health"]["check"] == "status"
    assert all("==" in p for p in raw["requires"]["pip"])
    with open(os.path.join(FOLDER, "synox_sdk.py"), encoding="utf-8") as f:
        assert f'SDK_VERSION = "{sdk.SDK_VERSION}"' in f.read()
