"""SYNOX 衣櫃公開頁（docs/site/wardrobe.html，由 scripts/build_costumes.py page 從 costumes/ 產生）：開場動畫與「↻ 重播開場」。

這裡只做靜態檢查（不開瀏覽器）。瀏覽器實測的結果寫在 commit 訊息裡；這些測試擋的是當時找到的三個原因不要再回來：
- 公開頁把每條規則限定在那張卡片（.cw-<字母>）時，G–Z 自己的規則寫成 [data-slot][data-slot]，兩個都被換掉就一條都對不到
  （G–Z 的開場動畫在公開頁上根本沒播，按重播當然也沒反應）；
- 重播不能只靠 cloneNode：改成「先把動畫全關、逼瀏覽器算一次、再放回去」，播不完的直接跳到終點；
- 系統開了減少動態效果時不播、畫面是終態。"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_costumes as bc  # noqa: E402

TEMPLATE = os.path.join(ROOT, "scripts", "wardrobe_template.html")


def _page():
    with open(os.path.join(bc.SITE, "wardrobe.html"), encoding="utf-8") as f:
        return f.read()


def _template():
    with open(TEMPLATE, encoding="utf-8") as f:
        return f.read()


def _script(text):
    return "".join(re.findall(r"<script>(.*?)</script>", text, re.S))


def test_scope_only_prefixes_the_first_data_slot():
    css = ("[data-slot][data-slot] .op-W .wboot{opacity:0}\n"
           "[data-slot] .op .ow{color:red}\n"
           "@media (prefers-color-scheme:dark){:root:not([data-theme=\"light\"]) [data-slot][data-slot] .a{fill:red}}\n")
    out = bc._scope_css(css, "cw-w")
    assert ".cw-w [data-slot][data-slot] .op-W .wboot{opacity:0}" in out
    assert ".cw-w [data-slot] .op .ow{color:red}" in out
    assert ':root:not([data-theme="light"]) .cw-w [data-slot][data-slot] .a{fill:red}' in out
    assert ".cw-w [data-slot].cw-w" not in out


def test_page_has_no_unreachable_series_rules():
    """以前 G–Z 的規則在公開頁上長這樣：.cw-g [data-slot].cw-g [data-slot] …（要同一個元素又是卡片又是 slot）。"""
    page = _page()
    assert not re.search(r"\.cw-[a-z] \[data-slot\]\.cw-[a-z]", page)
    for folder in bc.costume_folders():
        letter = bc.about(folder)["letter"].lower()
        with open(os.path.join(folder, "theme.css"), encoding="utf-8") as f:
            css = f.read()
        if "[data-slot][data-slot]" in css:
            assert f".cw-{letter} [data-slot][data-slot] " in page, letter


def test_page_matches_generator_output():
    """公開頁就是產生器現在的輸出（除了最下面的頁面版本）：改了樣板或 costumes/ 要重新產生、重新蓋版本。"""
    stamp = re.compile(r"<span data-stamp>.*?</span>", re.S)
    assert stamp.sub("", bc.page_html()) == stamp.sub("", _page())


def test_cursor_letters_on_the_page_end_visible():
    page = _page()
    m = re.search(r"\.cw-e \[data-slot\] \.op-E \.l\{animation:([^}]*)\}", page)
    assert m, "E 的打字規則不在公開頁上"
    toks = bc.split_top(m.group(1), " ")
    assert "none" in toks and not {"both", "forwards"} & set(toks) and "steps(" not in m.group(1)


def test_replay_restarts_by_toggling_animation_off_and_on():
    for text in (_template(), _page()):
        css = re.search(r"<style>(.*?)</style>", text, re.S).group(1)
        assert re.search(r"\.cw-screen\.cw-rs svg,\s*\.cw-screen\.cw-rs svg \*\s*\{\s*animation:\s*none !important;", css)
        js = _script(text)
        body = re.search(r"function replay\(screen\) \{(.*?)\n  \}", js, re.S).group(1)
        add, flush, remove = body.find('classList.add("cw-rs")'), body.find("offsetWidth"), body.find('classList.remove("cw-rs")')
        assert -1 < add < flush < remove, "要先加 cw-rs、逼瀏覽器算一次樣式、再拿掉"
        assert re.search(r"try \{[^}]*offsetWidth[^}]*\} finally \{ screen\.classList\.remove\(\"cw-rs\"\)", body)
        assert ".cloneNode(" not in js and ".replaceWith(" not in js


def test_replay_never_leaves_the_opening_half_way():
    js = _script(_page())
    assert "a.finish()" in js and "endTime" in js and "setTimeout" in js      # 播不完的（瀏覽器沒在算畫面）跳到終點
    assert "cwRun" in js                                                      # 連按兩次：舊的那輪不去動新的


def test_replay_respects_reduced_motion():
    page = _page()
    assert "@media (prefers-reduced-motion: reduce) { * { animation: none !important;" in page
    js = _script(page)
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in js
    assert re.search(r"function replay\(screen\) \{\s*if \(!screen \|\| \(still && still\.matches\)\) return;", js)


def test_replay_button_points_at_its_own_screen():
    page = _page()
    cards = re.findall(r'<div class="cw-open">\s*<div class="cw-screen" data-slot="opening" data-letter="([A-Z])">.*?'
                       r'<button class="cw-replay" type="button" data-replay="([A-Z])">', page, re.S)
    assert len(cards) == len(bc.costume_folders()) and all(a == b for a, b in cards)
    assert 'b.closest(".cw-open").querySelector(".cw-screen")' in _script(page)
