"""SYNOX 衣櫃（costumes/）：26 套外觀都是「只有圖和顏色、不帶程式」的 theme pack。

規則（scripts/build_costumes.py 的 pack_problems，打包時也擋）：
- 不准 <script、on…= 事件屬性、javascript:、外部網址（http；SVG 的 xmlns 命名空間例外）、@import、foreignObject、data: 網址；
- 7 個 slot（吉祥物 4 種狀態、開場、字標、App 圖示）都在，light 與 dark 的 token 都齊；pack.json 有 summary、theme.contract；
- 照 SYNOX 外觀合約第 1 版（SYNOX 的 docs/themes.md）：CSS 的 :root 只設 --th- 變數、其他規則在 [data-slot] 底下、
  keyframes 用 th-<字母>- 開頭、沒有 !important；SVG 沒有 style="…"、只用 SYNOX 白名單裡的濾鏡；
- 一套打包後不超過 MAX_PACK_BYTES。
這裡只用標準函式庫（不需要 SYNOX）；最後一個測試有 SYNOX 的原始碼時（SYNOX_SRC 或旁邊的 ../SYNOX/src）
直接跑 SYNOX 安裝時的第 1、2 關與消毒，確認每一套都裝得進去、消毒前後一樣。"""
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_costumes as bc  # noqa: E402

FOLDERS = bc.costume_folders()
NAMES = [os.path.basename(f) for f in FOLDERS]


def test_there_are_26_costumes_a_to_z():
    letters = [re.match(r"^costume-([a-z])-", n).group(1) for n in NAMES]
    assert letters == [chr(c) for c in range(ord("a"), ord("z") + 1)]
    assert NAMES == [bc.costume_id(chr(c)) for c in range(ord("A"), ord("Z") + 1)]


@pytest.mark.parametrize("name", NAMES)
def test_costume_passes_no_code_rules(name):
    folder = os.path.join(bc.COSTUMES, name)
    assert bc.pack_problems(folder) == []


@pytest.mark.parametrize("name", NAMES)
def test_costume_has_all_slots_and_tokens(name):
    with open(os.path.join(bc.COSTUMES, name, "pack.json"), encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["kind"] == "theme" and raw["name"] == name
    theme = raw["theme"]
    assert set(theme["slots"]) == set(bc.SLOTS)
    for rel in theme["slots"].values():
        assert os.path.isfile(os.path.join(bc.COSTUMES, name, *rel.split("/")))
    for mode in ("light", "dark"):
        assert set(bc.TOKENS) <= set(theme["tokens"][mode])
    assert theme["fonts"] == [] and theme["contract"] == bc.CONTRACT
    assert raw["summary"].strip() and "description" not in raw


@pytest.mark.parametrize("name", NAMES)
def test_every_css_variable_is_defined(name):
    """theme.css 用到的 var(--x) 都有定義（token、這一套自己多用的顏色、或規則裡設的）；SVG 裡沒有後備值的也一樣。"""
    folder = os.path.join(bc.COSTUMES, name)
    with open(os.path.join(folder, "theme.css"), encoding="utf-8") as f:
        css = f.read()
    svgs = "".join(open(os.path.join(folder, *rel.split("/")), encoding="utf-8").read() for rel in bc.SLOT_FILES.values())
    defined = set(bc.TOKENS + bc.OPTIONAL_TOKENS) | set(re.findall(r"(--[\w-]+)\s*:", css + svgs))
    used = set(re.findall(r"var\((--[\w-]+)", css)) | set(re.findall(r"var\((--[\w-]+)\)", svgs))
    assert used <= defined, sorted(used - defined)


def test_pack_writes_synoxpack_with_integrity(tmp_path):
    result = bc.pack_all(str(tmp_path))
    assert result["problems"] == []
    assert [e["name"] for e in result["entries"]] == NAMES
    for e in result["entries"]:
        path = tmp_path / e["file"]
        blob = path.read_bytes()
        assert len(blob) == e["size"] <= bc.MAX_PACK_BYTES
        assert hashlib.sha256(blob).hexdigest() == e["sha256"]
        assert e["kind"] == "theme" and e["category"] == "衣櫃／外觀"
        with zipfile.ZipFile(path) as z:
            assert sorted(z.namelist()) == sorted(bc.PACKED)                   # 只有這些檔（about.json 不進 pack）
            raw = json.loads(z.read("pack.json"))
            for rel, digest in raw["integrity"]["sha256"].items():
                data = z.read(rel)
                assert hashlib.sha256(data).hexdigest() == digest
                assert bc.code_problems(rel, data.decode("utf-8")) == []
    again = bc.pack_all(str(tmp_path / "again"))                                # 同樣的內容打出一樣的檔
    assert [e["sha256"] for e in again["entries"]] == [e["sha256"] for e in result["entries"]]


# ── 檢查本身真的擋得住（拿一套複製出來改壞）──────────────────────────────

BAD_SVG = [
    ('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "<script>"),
    ('<svg xmlns="http://www.w3.org/2000/svg" onload="x()"></svg>', "on…="),
    ('<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:x()"/></svg>', "javascript:"),
    ('<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/a.png"/></svg>', "外部網址"),
    ('<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>', "foreignObject"),
    ('<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(data:x)"/></svg>', "url()"),
    ('<svg xmlns="http://www.w3.org/2000/svg"><style>rect{}</style></svg>', "不准的元素"),
    ('<svg xmlns="http://www.w3.org/2000/svg"><g style="--i:2"/></svg>', "style="),
    ('<svg xmlns="http://www.w3.org/2000/svg"><filter id="cb-f"><feTurbulence/></filter></svg>', "feTurbulence"),
]


@pytest.fixture
def broken(tmp_path):
    src = FOLDERS[1]
    dst = tmp_path / os.path.basename(src)
    shutil.copytree(src, dst)
    return dst


@pytest.mark.parametrize("svg,what", BAD_SVG)
def test_check_refuses_code_in_svg(broken, svg, what):
    (broken / "slots" / "idle.svg").write_text(svg, encoding="utf-8")
    problems = bc.pack_problems(str(broken))
    assert any(what in p for p in problems), problems


@pytest.mark.parametrize("css,what", [
    ("@import 'x.css';", "@import"),
    ("[data-slot] .a{background:url(https://example.com/x.png)}", "外部網址"),
    (".masc .eye{fill:red}", "沒有限定在 [data-slot]"),
    ("@keyframes blink{to{opacity:0}}", "keyframes blink"),
    ("@keyframes cb-blink{to{opacity:0}}", "th-b-"),
    ("[data-slot] .a{animation:nope 1s infinite}", "nope"),
    ("[data-slot] .a{opacity:0!important}", "!important"),
    (":root{--bg:red}", ":root 只能設 --th-"),
    ("@media (max-width:300px){[data-slot] .a{fill:red}}", "@media"),
    ("@font-face{font-family:x}", "at-rule"),
])
def test_check_refuses_bad_css(broken, css, what):
    with open(broken / "theme.css", "a", encoding="utf-8") as f:
        f.write("\n" + css + "\n")
    problems = bc.pack_problems(str(broken))
    assert any(what in p for p in problems), problems


def test_check_refuses_missing_slot_token_and_size(broken):
    raw = json.loads((broken / "pack.json").read_text(encoding="utf-8"))
    del raw["theme"]["slots"]["mascot.warn"]
    del raw["theme"]["tokens"]["dark"]["--accent"]
    (broken / "pack.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with open(broken / "slots" / "opening.svg", "a", encoding="utf-8") as f:
        f.write("<!--" + "x" * bc.MAX_PACK_BYTES + "-->")
    problems = " / ".join(bc.pack_problems(str(broken)))
    assert "slots" in problems and "--accent" in problems and "太大" in problems


def test_pack_refuses_broken_costume_and_writes_nothing(broken, tmp_path, monkeypatch):
    (broken / "slots" / "idle.svg").write_text(BAD_SVG[0][0], encoding="utf-8")
    monkeypatch.setattr(bc, "costume_folders", lambda: [str(broken)])
    out = tmp_path / "out"
    result = bc.pack_all(str(out))
    assert result["problems"] and not out.exists()


def test_public_page_lists_every_costume_with_download_link():
    with open(os.path.join(bc.SITE, "wardrobe.html"), encoding="utf-8") as f:
        page = f.read()
    for name in NAMES:
        with open(os.path.join(bc.COSTUMES, name, "pack.json"), encoding="utf-8") as f:
            version = json.load(f)["version"]
        assert f'id="{name}"' in page
        assert bc.download_url(f"{name}-{version}.synoxpack") in page
    assert "<script" in page                    # 頁面本身可以有（深淺色切換、重播）；pack 裡不行
    for internal in ("內部版", "選這個", "複製我的選擇"):
        assert internal not in page


def test_generator_helpers_make_svg_that_survives_synox_sanitizer():
    classes = {}
    svg = bc.lift_styles('<g class="l x" style="transform-box:view-box;transform-origin:5px 6px;--i:3"><svg class="v"/></g>'
                         '<g style="--i:3"/>', "cb", classes)
    assert svg == '<g class="l x cb-v0" transform-origin="5px 6px"><svg class="v"/></g><g class="cb-v0"/>'
    assert classes == {"--i:3": "cb-v0"}
    chain = ('<filter id="f"><feOffset in="SourceAlpha" result="o"/><feTurbulence result="n"></feTurbulence>'
             '<feColorMatrix in="n" result="na"/><feComposite in="na" in2="o" result="gr"/>'
             '<feMerge><feMergeNode in="o"/><feMergeNode in="gr"/></feMerge></filter>')
    assert bc.strip_filters(chain) == ('<filter id="f"><feOffset in="SourceAlpha" result="o"/>'
                                       '<feMerge><feMergeNode in="o"/></feMerge></filter>')
    assert bc.rename_extras("fill:var(--moon);--moon:1;var(--moonx);--th-moon", ["moon"]) == \
        "fill:var(--th-moon);--th-moon:1;var(--moonx);--th-moon"


def test_public_page_keeps_series_variables_scoped():
    """theme.css 的 :root（--th- 變數）不能原樣搬到公開頁（26 套的同名變數會互蓋）：公開頁在 .cw-<字母> 上設。"""
    with open(os.path.join(bc.SITE, "wardrobe.html"), encoding="utf-8") as f:
        page = f.read()
    assert not re.search(r"(^|[}\s]):root\s*\{\s*--th-", page)
    assert ".cw-g{" in page and "--th-moon:" in page


def _synox_src():
    for cand in (os.environ.get("SYNOX_SRC"), os.path.join(os.path.dirname(ROOT), "SYNOX", "src")):
        if cand and os.path.isfile(os.path.join(cand, "synox", "theme", "pack.py")):
            return cand
    return None


@pytest.mark.skipif(_synox_src() is None, reason="沒有 SYNOX 的原始碼（設 SYNOX_SRC 或放在 ../SYNOX）")
def test_every_costume_installs_in_real_synox(tmp_path):
    """用 SYNOX 自己的程式：上傳 .synoxpack 那條路（第 1 關宣告、第 2 關消毒＋integrity），再照網頁每次載入那樣重新消毒；
    消毒不能拿掉任何東西（notes 是空的），@keyframes 一個都不能少。"""
    sys.path.insert(0, _synox_src())
    try:
        from synox.packs import admin
        from synox.packs.manifest import read
        from synox.theme import pack as theme_pack
    except ImportError as e:                                   # SYNOX 的相依套件沒裝
        pytest.skip(f"SYNOX 載不起來：{e}")
    result = bc.pack_all(str(tmp_path / "dist"))
    assert result["problems"] == []
    for e in result["entries"]:
        data = tmp_path / ("data-" + e["name"])
        blob = (tmp_path / "dist" / e["file"]).read_bytes()
        token, summary = admin.stage(blob, str(data))
        assert summary["scan"]["ok"] and summary["kind"] == "theme", e["name"]
        staged = os.path.join(admin.staging_root(str(data)), token, "pack")
        m = read(staged, strict=True)
        bundle = theme_pack.compile_theme(staged, m.name, m.theme)
        assert bundle.notes == [], (e["name"], bundle.notes)
        assert set(bundle.slots) == set(bc.SLOTS)
        with open(os.path.join(staged, "theme.css"), encoding="utf-8") as f:
            src = f.read()
        kept = set(re.findall(r"@keyframes\s+([\w-]+)", bundle.stylesheet()))
        assert set(re.findall(r"@keyframes\s+([\w-]+)", src)) <= kept, e["name"]
