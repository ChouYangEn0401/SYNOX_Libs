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
    ("[data-slot] .a{animation:th-b-x 1ms steps(1) calc(.35s + var(--i) * .17s) both}\n@keyframes th-b-x{to{opacity:1}}", "steps()"),
    ("[data-slot] .a{animation:1ms steps(1) 0s 1 normal forwards running th-b-x}\n@keyframes th-b-x{to{opacity:1}}", "steps()"),
    ("[data-slot] .a{transform:translateX(-50%)}", "百分比位移"),
])
def test_check_refuses_bad_css(broken, css, what):
    with open(broken / "theme.css", "a", encoding="utf-8") as f:
        f.write("\n" + css + "\n")
    problems = bc.pack_problems(str(broken))
    assert any(what in p for p in problems), problems


def test_fragile_check_leaves_normal_steps_animations_alone():
    """只擋「幾 ms 的 steps() 又停在終點」；正常長度、會一直重複、或播完不停住的 steps() 照常可以用。"""
    ok = ("[data-slot] .a{animation:0.78s steps(1) 0s 1 normal both running th-w-x}\n"
          "[data-slot] .b{animation:1.2s steps(1) 0s infinite normal none running th-w-x}\n"
          "[data-slot] .c{animation:th-e-x calc(.35s + var(--i) * .17s) linear 0s 1 normal none}\n"
          "[data-slot] .d{animation:0s linear 0s 1 normal none running th-w-x}\n")
    assert bc.fragile_animations(ok) == []


def test_percent_translate_check_only_flags_static_positioning():
    """百分比位移只擋「一般規則裡寫死的 transform」：@keyframes 裡的進場位移（I 的跑馬燈）、有寫 transform-box 的、px 都放行。"""
    ok = ("[data-slot] .a{transform:translateX(-28.5px)}\n"
          "[data-slot] .b{transform-box:fill-box;transform:translateX(-50%)}\n"
          "@keyframes th-i-x { \n  0% { transform: translateX(118%); }\n}\n")
    assert bc.percent_translates(ok) == []
    assert len(bc.percent_translates("[data-slot] .c{transform:translate(-50%, 0)}")) == 1


def _ms(expr: str, **env) -> float:
    """CSS 的時間（0.3s、120ms、calc((var(--n) - 1) * 120ms)）→ 毫秒；var(--x) 用 env 帶入。"""
    e = expr.strip()
    for k, v in env.items():
        e = e.replace(f"var(--{k})", str(v))
    e = e.replace("calc", "")
    e = re.sub(r"(\d*\.?\d+)ms\b", r"\1", e)
    e = re.sub(r"(\d*\.?\d+)s\b", r"(\1*1000)", e)
    assert re.fullmatch(r"[\d\s.+\-*/()]+", e), expr
    return float(eval(e))  # noqa: S307 — 上面已經確認只剩數字與四則運算


def test_blocks_opening_bar_is_centred_and_full_while_the_mascot_is_done():
    """系列 L（格格）的開場：吉祥物底下那排 6 格綠色載入條。
    以前：translateX(-50%) 在 SVG 裡照整張畫面算（-240px），整條跑到最左邊；第 6 格 0.9s 才亮、0.95s 就收，滿格只露約 40ms。
    現在：條置中在吉祥物正下方；吉祥物長完（clip 動畫結束）時條已經滿格，滿格至少留 150ms，在字標開始掉下來之前才收。"""
    folder = os.path.join(bc.COSTUMES, "costume-l-blocks")
    with open(os.path.join(folder, "slots", "opening.svg"), encoding="utf-8") as f:
        svg = f.read()
    with open(os.path.join(folder, "theme.css"), encoding="utf-8") as f:
        css = f.read()
    rules = {}
    for head, body in bc._css_blocks(css):
        rules.setdefault(head, []).extend(bc.decls_of(body))
    # 位置：6 格的中心＋位移＝吉祥物的中心
    bar = svg[svg.index('class="l-bar'):svg.index('class="wm ow')]
    cells = [(float(x), float(w)) for x, w in re.findall(r'<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', bar)]
    assert len(cells) == 6
    shift = dict(rules["[data-slot][data-slot] .op-L .l-bar"])["transform"]
    m = re.fullmatch(r"translateX\((-?[\d.]+)px\)", shift)
    assert m, shift                                              # 不能用百分比（SVG 裡照整張畫面算）
    centre = (min(x for x, _ in cells) + max(x + w for x, w in cells)) / 2 + float(m.group(1))
    masc = re.search(r'<svg class="masc[^"]*"[^>]* x="([\d.]+)"[^>]* width="([\d.]+)"', svg)
    assert abs(centre - (float(masc.group(1)) + float(masc.group(2)) / 2)) < 1, centre
    # 時間：每格 blink（亮、暗、亮）最後一次亮起＝這一格走好；6 格都走好＝滿格
    anim = dict(rules["[data-slot][data-slot] .op-L .l-bar .t-i"])["animation"]
    blink, out = bc.split_top(anim)
    bt, ot = bc.split_top(blink, " "), bc.split_top(out, " ")
    assert bt[0] == "th-l-l-o-blink" and ot[0] == "th-l-l-o-out"
    stops = {}
    for sel, body in bc._css_blocks(dict(bc._css_blocks(css))["@keyframes th-l-l-o-blink"]):
        for s in sel.split(","):
            stops[float(s.strip().rstrip("%")) / 100] = float(dict(bc.decls_of(body))["opacity"])
    last_dark = max(p for p, o in stops.items() if o == 0)
    solid = min(p for p, o in stops.items() if o == 1 and p > last_dark)
    full = max(_ms(bt[3], n=n) + _ms(bt[1]) * solid for n in range(1, 7))
    gone = _ms(ot[3])
    om = bc.split_top(dict(rules["[data-slot][data-slot] .op-L .om"])["animation"], " ")
    mascot_done = _ms(om[0]) + _ms(om[2])
    letters = dict(rules["[data-slot][data-slot] .op-L .l"])["animation"]
    first_letter = _ms(bc.split_top(letters, " ")[3], i=0)
    assert full <= mascot_done, (full, mascot_done)                     # 吉祥物長完的時候條已經滿格
    assert gone - max(full, mascot_done) >= 150, (full, mascot_done, gone)   # 滿格＋吉祥物一起看得到一段時間
    assert gone <= first_letter, (gone, first_letter)                   # 字標掉下來之前收掉，不會疊在字上
    wrap = bc.split_top(dict(rules["[data-slot][data-slot] .op-L .l-bar"])["animation"], " ")
    assert _ms(wrap[0]) >= gone                                         # 外面那層不會比格子先消失


def test_cursor_opening_types_all_five_letters_and_keeps_them():
    """系列 E 的開場：S Y N O X 五個字都在（第 5 個是 X），而且動畫播完一定看得到。
    以前是「1ms steps(1) 延遲 both」：終點進度算成 0.99999999999989，steps(1) 取整成 0，X 一直停在透明。
    現在是整段藏起來、時間＝要等多久、fill none：播完就是元素原本的樣子；打字動畫不靠「剛好算到 1」。"""
    folder = os.path.join(bc.COSTUMES, "costume-e-cursor")
    with open(os.path.join(folder, "slots", "opening.svg"), encoding="utf-8") as f:
        svg = f.read()
    letters = re.findall(r'<g class="l [^"]*\b(ce-v\d)"[^>]*><svg class="v"[^>]*><path class="gl" d="M[^"]+"', svg)
    assert letters == [f"ce-v{i}" for i in range(5)]
    with open(os.path.join(folder, "theme.css"), encoding="utf-8") as f:
        css = f.read()
    rules = {head: body for head, body in bc._css_blocks(css)}
    anim = dict(bc.decls_of(rules["[data-slot] .op-E .l"]))["animation"]
    toks = bc.split_top(anim, " ")
    assert "th-e-oE-key" in toks and "none" in toks and not {"both", "forwards"} & set(toks), anim
    assert "steps(" not in anim
    assert re.sub(r"\s+", "", rules["@keyframes th-e-oE-key"]) == "0%,100%{opacity:0;}"   # 只在等的時候藏
    for head, body in rules.items():                                                     # 字本身沒有被藏起來
        if re.search(r"\.l\b|ce-v\d", head):
            assert not re.search(r"(^|;)\s*(opacity\s*:\s*0|visibility\s*:\s*hidden)", body), head


def test_css_fixups_are_already_in_the_costumes_and_versions_match():
    """costumes/ 是 extract 的輸出：CSS_FIXUPS 換過的樣子要已經在檔案裡（重跑 extract 不會變），升過的版本也一致。"""
    for letter in bc.CSS_FIXUPS:
        folder = os.path.join(bc.COSTUMES, bc.costume_id(letter))
        with open(os.path.join(folder, "theme.css"), encoding="utf-8") as f:
            css = f.read()
        assert bc.apply_fixups(letter, css) == css
        for old, new in bc.CSS_FIXUPS[letter]:
            assert old not in css and new in css
    for name in NAMES:
        letter = re.match(r"^costume-([a-z])-", name).group(1).upper()
        with open(os.path.join(bc.COSTUMES, name, "pack.json"), encoding="utf-8") as f:
            assert json.load(f)["version"] == bc.VERSIONS.get(letter, bc.VERSION), name


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


# ── 顏色變數清單（docs/costume-token-usage.md；給 SYNOX 套用前的缺漏檢查）───────────────────

def test_token_usage_doc_is_current():
    """docs/costume-token-usage.md 是 build_costumes.py tokens --write 的輸出（改了外觀要重跑）。"""
    with open(bc.TOKEN_DOC, encoding="utf-8") as f:
        assert f.read() == bc.token_report(), "重跑 python scripts/build_costumes.py tokens --write"


@pytest.mark.parametrize("name", NAMES)
def test_costume_only_uses_variables_synox_or_itself_provides(name):
    """每個 var(--x) 不是核心 token、就是 pack.json 給了的吉祥物／進階 token、這一套 :root 宣告的 --th-，或規則裡設的區域變數。"""
    r = bc.token_usage(os.path.join(bc.COSTUMES, name))
    assert r["groups"]["unknown"] == [] and r["not_in_pack"] == [], (r["groups"]["unknown"], r["not_in_pack"])
    assert all(n.startswith("--th-") for n in r["groups"]["own"])


def test_token_usage_finds_hardcoded_colors_but_not_var_fallbacks():
    assert bc._strip_var_fallbacks("color-mix(in srgb, var(--m1, #a3d91a) 76%, #1a2400)") == \
        "color-mix(in srgb, var(--m1) 76%, #1a2400)"
    r = bc.token_usage(os.path.join(bc.COSTUMES, "costume-l-blocks"))
    colors = {(h["file"], h["color"]) for h in r["hardcoded"]}
    assert ("mascot.idle", "#1a2400") in colors and ("mascot.idle", "#fff") in colors
    assert not any(c == "#a3d91a" for _f, c in colors)                    # 只出現在 var(--m1, #a3d91a) 的後備值


@pytest.mark.skipif(_synox_src() is None, reason="沒有 SYNOX 的原始碼（設 SYNOX_SRC 或放在 ../SYNOX）")
def test_token_lists_match_synox_contract():
    """build_costumes.py 抄的 token 名字跟 SYNOX 的合約一樣（SYNOX 加了名字，這裡要跟著加，報告才不會把它當成不認得）。"""
    sys.path.insert(0, _synox_src())
    from synox.theme import contract
    assert [t[2:] for t in bc.TOKENS] == list(contract.TOKENS)
    assert [t[2:] for t in bc.OPTIONAL_TOKENS] == list(contract.MASCOT_TOKENS)
    assert [t[2:] for t in bc.ADVANCED_TOKENS] == list(contract.ADVANCED_TOKENS)