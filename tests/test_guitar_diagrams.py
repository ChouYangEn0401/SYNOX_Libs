# -*- coding: utf-8 -*-
"""🎸 和弦圖：畫出來的點／橫按／x／o 跟查表的按法一致、PNG／SVG 格式、回覆的形狀（圖＋一行按法、翻頁按鈕、經 AI 不出按鈕）、
拼出來的和弦名稱（F major 7）、資料修正（「吉他和弦全書」對照）、TAB 等寬、換和弦、刷法。"""

import struct
import zlib

import pytest

from isdtelekit.core.channel import Inbound

from conftest import load_module

OWNER = 100


@pytest.fixture(scope="module")
def g():
    return load_module("guitar")


def first(g, name):
    ch = g.parse_chord_loose(name)
    return ch, g.voicings(ch["root"], ch["quality"], ch["bass"])[0]


def decode(g, shapes, x0=0.0):
    """從圖元倒推每條弦按第幾格（None＝×、0＝○），驗證「畫的」＝「查的」。"""
    base = next((int(s[3][:-2]) for s in shapes if s[0] == "text" and str(s[3]).endswith("fr")), 0)
    sx = [x0 + g.GRID_X + i * g.STRING_GAP for i in range(6)]
    mark_y = g.GRID_Y - 13

    def fret_at(y):
        r = round((y - g.GRID_Y) / g.FRET_GAP + 0.5)
        return r if base == 0 else r + base - 1
    out = [None] * 6
    for i, x in enumerate(sx):
        if any(s[0] == "circle" and abs(s[1] - x) < .01 and abs(s[2] - mark_y) < .01 for s in shapes):
            out[i] = 0
        dots = [s for s in shapes if s[0] == "circle" and abs(s[1] - x) < .01 and s[2] > g.GRID_Y]
        bars = [s for s in shapes if s[0] == "seg" and s[5] == 14 and s[1] - .01 <= x <= s[3] + .01]
        frets = [fret_at(s[2]) for s in dots] + [fret_at(s[2]) for s in bars]
        if frets:
            out[i] = max(frets)                 # 橫按上面再按高一格的：高的那格響
    return tuple(out)


# ── 畫圖：點、橫按、×、○ ──────────────────────────────────────────────

@pytest.mark.parametrize("name,frets,barres,base", [
    ("D/F#", (2, None, 0, 2, 3, 2), [], 0),
    ("Bb", (None, 1, 3, 3, 3, 1), [(1, 1, 5, 1), (3, 2, 4, 3)], 0),
    ("F", (1, 3, 3, 2, 1, 1), [(1, 0, 5, 1)], 0),
    ("Gm", (3, 5, 5, 3, 3, 3), [(3, 0, 5, 1)], 0),
    ("Cm", (None, 3, 5, 5, 4, 3), [(3, 1, 5, 1)], 0),
    ("C#m7", (None, 4, 6, 4, 5, 4), [(4, 1, 5, 1)], 4),                     # 高把位：標 4fr
])
def test_voicing_barres_and_base_fret(g, name, frets, barres, base):
    ch, v = first(g, name)
    assert v["frets"] == frets
    assert g.find_barres(v["frets"], v["fingers"]) == barres
    assert g.base_fret(v["frets"]) == base


@pytest.mark.parametrize("name", ["D/F#", "Bb", "F", "Gm", "Cm", "C#m7", "Am7", "G", "Bm", "E7", "Fmaj7", "Ab9",
                                  "C/E", "Dsus4", "F#m7b5", "Ebdim7", "A6"])
def test_drawing_equals_the_lookup(g, name):
    ch, v = first(g, name)
    shapes = g.diagram_shapes(g.own_name(ch), v["frets"], v["fingers"], ch["root"])
    assert decode(g, shapes) == tuple(v["frets"]), name
    texts = [s[3] for s in shapes if s[0] == "text"]
    assert texts[0] == g.own_name(ch) and g.frets_text(v["frets"]) in texts           # 名稱、底下一行按法
    muted = [s for s in shapes if s[0] == "seg" and s[5] == 1.6]
    assert len(muted) == 2 * sum(1 for f in v["frets"] if f is None)                  # 每個 × 兩筆


def test_d_slash_f_sharp_positions(g):
    ch, v = first(g, "D/F#")
    shapes = g.diagram_shapes("D/F#", v["frets"], v["fingers"], ch["root"])
    sx = [g.GRID_X + i * g.STRING_GAP for i in range(6)]
    dots = {round(s[1], 1): (s[2], s[4]) for s in shapes if s[0] == "circle" and s[2] > g.GRID_Y}
    row = lambda f: g.GRID_Y + (f - .5) * g.FRET_GAP                                   # noqa: E731
    assert dots[round(sx[0], 1)][0] == row(2) and dots[round(sx[4], 1)][0] == row(3)  # 第6弦第2格、第2弦第3格
    assert dots[round(sx[3], 1)][1] == g.DOT and dots[round(sx[0], 1)][1] == g.DOT    # F#、A：不是根音
    open_d = [s for s in shapes if s[0] == "circle" and abs(s[1] - sx[2]) < .01 and s[2] < g.GRID_Y]
    assert open_d and open_d[0][4] == g.ROOT_DOT                                       # 第4弦空弦 D＝根音（綠）
    numbers = {round(s[1], 1): s[3] for s in shapes if s[0] == "text" and s[2] > g.GRID_Y and s[3].isdigit()
               and s[2] < g.GRID_Y + g.FRETS_SHOWN * g.FRET_GAP}
    assert [numbers[round(sx[i], 1)] for i in (0, 3, 4, 5)] == ["1", "2", "4", "3"]


def test_png_and_svg_are_valid_and_svg_passes_the_synox_whitelist(g):
    cells = [g.diagram_cell(*first(g, n)) for n in "D/F# Bb F Gm Cm".split()]
    img = g.render_cells(cells, "D/F# Bb F Gm Cm 和弦圖")
    png = img["png"]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (5 * g.CELL_W * g.PNG_SCALE, g.CELL_H * g.PNG_SCALE)
    idat = png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8]
    assert len(zlib.decompress(idat)) == h * (1 + w * 3)
    assert img["svg"].startswith('<svg xmlns="http://www.w3.org/2000/svg"') and "<script" not in img["svg"]
    chatmedia = pytest.importorskip("synox.web.chatmedia")
    data, sw, sh = chatmedia.sanitize_svg(img["svg"].encode("utf-8"))
    assert (sw, sh) == (5 * g.CELL_W * 2, g.CELL_H * 2) and b"D/F#" in data


def test_every_glyph_is_7_by_5(g):
    for ch, rows in g.FONT.items():
        assert [len(r) for r in rows.split("|")] == [5] * 7, ch


# ── 名稱：拼出來的、全形的，看不懂就說 ────────────────────────────────

@pytest.mark.parametrize("text,root,quality,bass", [
    ("F major 7", 5, "maj7", None), ("Fmajor7", 5, "maj7", None), ("Fmaj 7", 5, "maj7", None),
    ("F M7", 5, "maj7", None), ("FΔ7", 5, "maj7", None), ("Fmaj7", 5, "maj7", None),
    ("F major", 5, "major", None), ("Fmajor", 5, "major", None),
    ("F minor 7", 5, "m7", None), ("Fmin7", 5, "m7", None), ("Fminor", 5, "minor", None), ("F minor", 5, "minor", None),
    ("C dominant 7", 0, "7", None), ("B diminished", 11, "dim", None), ("Bdim", 11, "dim", None),
    ("C augmented", 0, "aug", None), ("Caug", 0, "aug", None), ("F suspended 4", 5, "sus4", None),
    ("Fsus2", 5, "sus2", None), ("Cadd9", 0, "add9", None), ("A6", 9, "6", None), ("G9", 7, "9", None),
    ("D/F#", 2, "major", 6), ("D/F＃", 2, "major", 6), ("Ｄ／Ｆ＃", 2, "major", 6),
    ("B♭", 10, "major", None), ("Ｂｂ", 10, "major", None), ("C＃m７", 1, "m7", None), ("E♭ minor", 3, "minor", None),
])
def test_spelled_out_and_full_width_names(g, text, root, quality, bass):
    ch = g.parse_chord_loose(text)
    assert ch and (ch["root"], ch["quality"], ch["bass"]) == (root, quality, bass), text


def test_words_group_into_chords_and_unknown_is_never_guessed(g):
    assert g.group_chord_words("F major 7 Bb") == ["F major 7", "Bb"]
    assert g.group_chord_words("D/F# Bb F Gm Cm") == ["D/F#", "Bb", "F", "Gm", "Cm"]
    assert g.group_chord_words("C minor 7 G dominant 7") == ["C minor 7", "G dominant 7"]
    chords, bad = g.resolve_chords("Fx7")
    assert chords == [] and bad and "Fx7" in bad[0] and "是不是" in bad[0]
    chords, bad = g.resolve_chords("F majr 7 G")
    assert [g.own_name(c) for c in chords] == ["G"] and "majr" in bad[0]       # F 不會被當成 F 大三和弦


# ── 資料（對照「吉他和弦全書」） ─────────────────────────────────────

def test_artifact_easy_voicings_are_offered(g):
    bm = [v for v in g.voicings(11, "minor") if v["frets"] == (None, None, 0, 4, 3, 2)]
    assert bm and "低音 D" in bm[0]["label"]
    gg = [v for v in g.voicings(7, "major") if v["frets"] == (None, None, 0, 0, 0, 3)]
    assert gg and "低音 D" in gg[0]["label"]


def test_easy_tips_say_when_the_bass_is_not_the_root(g):
    for key, note in (("B_major", "F#"), ("A#_major", "F"), ("C_minor", "G")):
        frets, _f, tip = g.EASY[key]
        assert note in tip and "不是根音" in tip
        assert g.tones_of(frets)[0] != g.note_pc(key.split("_")[0])


def test_no_voicing_needs_more_than_four_frets(g):
    for r in range(12):
        for q in g.QUAL_ORDER:
            for v in g.voicings(r, q):
                pressed = [f for f in v["frets"] if f]
                assert not pressed or max(pressed) - min(pressed) <= 3, (g.SHARP[r], q, v)


def test_finger_numbers_are_physically_possible(g):
    """同一根手指按兩條以上的弦＝橫按：要同一格、中間的弦都不比那格低；不同的格不能用同一根手指。"""
    for r in range(12):
        for q in g.QUAL_ORDER:
            for v in g.voicings(r, q):
                fingers = v["fingers"]
                for k in (1, 2, 3, 4):
                    idx = [i for i in range(6) if fingers[i] == k and v["frets"][i]]
                    if len(idx) > 1:
                        assert len({v["frets"][i] for i in idx}) == 1, (g.SHARP[r], q, v)
                        assert g.find_barres(v["frets"], fingers), (g.SHARP[r], q, v)


def test_fingers_follow_the_frets_and_never_sit_on_open_strings(g):
    """手指號碼跟格數同方向（小號的手指不會在大號手指的更高格）、空弦／不彈的弦沒有手指、按的弦一定有手指；
    兩指之間撐太開（例如食指第 2 格、中指第 5 格）只能是標了「要撐開」的。"""
    for r in range(12):
        for q in g.QUAL_ORDER:
            for v in g.voicings(r, q):
                frets, fingers = v["frets"], v["fingers"]
                for f, k in zip(frets, fingers):
                    assert (k in (0, None)) if not f else k in (1, 2, 3, 4), (g.SHARP[r], q, v)
                at = {}
                for f, k in zip(frets, fingers):
                    if f:
                        at.setdefault(k, []).append(f)
                for a in at:
                    for b in at:
                        if a < b:
                            assert min(at[a]) <= max(at[b]), (g.SHARP[r], q, v)
                if g.stretched(frets, fingers):
                    assert "要撐開" in v["tip"], (g.SHARP[r], q, v)


def test_a_shape_dim7_and_m7b5_fingers(g):
    assert g.A_SHAPE["dim7"] == ((None, 0, 1, -1, 1, None), (None, 2, 3, 1, 4, None))     # Cdim7＝x 3 4 2 4 x
    assert g.A_SHAPE["m7b5"][1] == (None, 1, 3, 2, 4, None)
    assert "add9" not in g.A_SHAPE and "madd9" not in g.A_SHAPE
    cdim7 = [v for v in g.voicings(0, "dim7") if v["frets"] == (None, 3, 4, 2, 4, None)]
    assert cdim7 and cdim7[0]["fingers"] == (None, 2, 3, 1, 4, None)
    fdim7 = [v for v in g.voicings(5, "dim7") if 0 in v["frets"]]                        # 滑到第 0 格的不放橫按手指
    assert all(k == 0 for v in fdim7 for f, k in zip(v["frets"], v["fingers"]) if f == 0)


def test_add9_shapes_do_not_make_you_stretch(g):
    amadd9 = g.voicings(9, "madd9")[0]
    assert amadd9["frets"] == (None, 0, 2, 5, 0, 0) and amadd9["fingers"][3] == 4         # 第 5 格用小指，不是中指


# ── 回覆：圖＋一行按法、翻頁、經 AI ──────────────────────────────────

class Shots:
    """在 FakeChannel 上加 send_image／edit_image（像網頁對話），記下送出的圖。"""

    def __init__(self, api):
        self.sent, self.edits = [], []
        api.send_image = self.send_image
        api.edit_image = self.edit_image

    def send_image(self, chat_id, images, caption="", reply_markup=None, **kw):
        self.sent.append({"images": images, "caption": caption, "markup": reply_markup})
        return {"ok": True, "result": {"message_id": 99}}

    def edit_image(self, chat_id, message_id, images, caption=None, reply_markup=None, **kw):
        self.edits.append({"id": message_id, "images": images, "caption": caption, "markup": reply_markup})
        return {"ok": True}


def labels(markup):
    return [b["text"] for row in (markup or {}).get("inline_keyboard", []) for b in row]


def datas(markup):
    return [b["callback_data"] for row in (markup or {}).get("inline_keyboard", []) for b in row]


@pytest.fixture
def bot(make_bot):
    pytest.importorskip("synox.packs.kit").send_image                      # SYNOX SDK 0.2.5 起
    return make_bot("guitar")


def test_many_chords_reply_with_one_picture(bot, g):
    shots = Shots(bot.api)
    bot.say(OWNER, "/chord D/F# Bb F Gm Cm")
    (out,) = shots.sent
    img = out["images"][0]
    assert set(img) == {"svg", "png", "alt"} and "D/F# Bb F Gm Cm" in img["alt"]
    for name, frets in (("D/F#", "2 x 0 2 3 2"), ("Bb", "x 1 3 3 3 1"), ("F", "1 3 3 2 1 1"),
                        ("Gm", "3 5 5 3 3 3"), ("Cm", "x 3 5 5 4 3")):
        assert f"{name}：{frets}" in out["caption"] and frets in img["svg"]
    assert "F 換按法" in labels(out["markup"]) and "📖 說明" in labels(out["markup"])
    assert "組成音" not in out["caption"]                                    # 說明不預設倒出來


def test_single_chord_pages_through_voicings_in_place(bot, g):
    shots = Shots(bot.api)
    bot.say(OWNER, "/chord F")
    out = shots.sent[0]
    n = len(g.voicings(5, "major"))
    assert f"1／{n}" in out["caption"] and "1 3 3 2 1 1" in out["caption"]
    assert labels(out["markup"])[:3] == ["‹ 上一個", f"1／{n}", "下一個 ›"]
    nxt = datas(out["markup"])[2]
    bot.api.send_message(OWNER, out["caption"], reply_markup=out["markup"])  # 讓 harness 找得到這顆按鈕
    bot.press(OWNER, nxt)
    edit = shots.edits[-1]
    second = g.voicings(5, "major")[1]
    assert f"2／{n}" in edit["caption"] and g.frets_text(second["frets"]) in edit["caption"]
    assert g.frets_text(second["frets"]) in edit["images"][0]["svg"]
    bot.api.send_message(OWNER, "x", reply_markup=out["markup"])
    bot.press(OWNER, datas(out["markup"])[-1])                              # 📖 說明
    assert "組成音" in bot.last(OWNER)


def test_detail_word_adds_the_explanation(bot):
    Shots(bot.api)
    bot.say(OWNER, "/chord Am7 說明")
    assert "組成音" in bot.last(OWNER)
    bot.say(OWNER, "/chord_info Am7")
    assert "組成音" in bot.last(OWNER) and "x 0 2 0 1 0" in bot.last(OWNER)


def test_ai_calls_get_text_and_the_user_gets_the_picture_without_buttons(bot):
    capture = pytest.importorskip("synox.agent.capture")
    shots = Shots(bot.api)
    cap = capture.CaptureApi(bot.api, OWNER)
    bot.app.dispatcher.handle(cap, Inbound("test", OWNER, OWNER, "/chord D/F# Bb F Gm Cm", username="u"))
    seen = cap.close()
    assert "D/F#：2 x 0 2 3 2" in seen and "已直接傳給使用者 1 張圖" in seen
    assert shots.sent and shots.sent[0]["markup"] is None


def test_without_image_support_it_falls_back_to_text(make_bot):
    bot = make_bot("guitar")
    bot.api.send_photo = lambda *a, **kw: {"ok": False}                    # channel 不收圖
    bot.say(OWNER, "/chord D/F# Bb")
    assert "D/F#：2 x 0 2 3 2" in bot.last(OWNER)


def test_arp_keeps_the_quality_and_lines_up(bot, g):
    Shots(bot.api)
    bot.say(OWNER, "/chord_arp F major 7")
    text = bot.api.sent[-1].text
    assert "<pre>" in text and "T：拇指，彈根音（最低那條弦）" in text and "1：食指，第 3 弦（G）" in text
    tab = text.split("<pre>")[1].split("</pre>")[0]
    lines = tab.split("\n")
    assert lines[0].strip().startswith("Fmaj7") and lines[1].startswith("e(1)") and lines[6].startswith("E(6)")
    assert len({len(x) for x in lines[1:]}) == 1                            # 六條弦一樣長（等寬對齊）
    assert "3" in lines[4] and "2" in lines[3] and "1" in lines[2]          # Fmaj7＝x x 3 2 1 0：第4弦第3格…
    bot.say(OWNER, "/chord_arp Fmajr7")
    assert "看不懂" in bot.last(OWNER)


def test_change_and_strum(bot, g):
    Shots(bot.api)
    bot.say(OWNER, "/chord_change C Am")
    reply = bot.last(OWNER)
    assert "C → Am" in reply and "不要動" in reply                          # 食指、中指都不用動
    r = g.analyze_change("Bm", g.EASY["B_minor"] and {"frets": g.EASY["B_minor"][0], "fingers": g.EASY["B_minor"][1]},
                         "Cm", {"frets": g.EASY["C_minor"][0], "fingers": g.EASY["C_minor"][1]})
    assert "平移 1 格" in r["shift"]
    bot.say(OWNER, "/chord_strum")
    assert "Folk Rock" in bot.last(OWNER)
    bot.say(OWNER, "/chord_strum folk rock")
    from isdtelekit.core.richtext import plain
    text = bot.api.sent[-1].text
    assert "<pre>" in text
    text = plain(text)
    assert "D - D U - U D U" in text and "1 & 2 & 3 & 4 &" in text


# ── 其他指令：組成音、最好按、一個根音、入門、和弦進行 ─────────────────────────

def test_tones_names_intervals_and_strings(bot):
    Shots(bot.api)
    bot.say(OWNER, "/chord_tones Cmaj7")
    reply = bot.last(OWNER)
    for part in ("C：根音", "E：大三度", "G：完全五度", "B：大七度", "e（1 弦）E", "E（6 弦）不彈"):
        assert part in reply, part
    bot.say(OWNER, "/chord_tones Bdim7")
    assert "減七度" in bot.last(OWNER)                       # dim7 的 9 個半音不叫大六度
    bot.say(OWNER, "/chord_tones D/F#")
    assert "低音彈 F#" in bot.last(OWNER)


def test_easy_picks_the_no_barre_voicing(bot, g):
    shots = Shots(bot.api)
    bot.say(OWNER, "/chord_easy F Bm B")
    cap = shots.sent[0]["caption"]
    assert "F　x x 3 2 1 1" in cap and "Bm　x x 4 4 3 2" in cap
    assert "不是根音 B" in cap                                # 簡易 B 的低音是 F#
    assert len(shots.sent[0]["images"]) == 1 and "x x 3 2 1 1" in shots.sent[0]["images"][0]["svg"]


def test_all_basic_and_prog_send_one_picture(bot):
    shots = Shots(bot.api)
    bot.say(OWNER, "/chord_all bb")
    out = shots.sent[-1]
    assert out["caption"].startswith("🎸 Bb 開頭") and "Bbm7" in out["caption"] and "x 1 3 3 3 1" in out["images"][0]["svg"]
    bot.say(OWNER, "/chord_basic")
    assert "C：x 3 2 0 1 0" in shots.sent[-1]["caption"] and "Dm：x x 0 2 3 1" in shots.sent[-1]["caption"]
    bot.say(OWNER, "/chord_prog G 卡農")
    cap = shots.sent[-1]["caption"]
    assert "G → D → Em → Bm → C → G → C → D" in cap and "Bm　" in cap
    bot.say(OWNER, "/chord_prog Am 3")
    assert "Am → G → F → E" in shots.sent[-1]["caption"]                 # 小調的 V 借大三和弦
    bot.say(OWNER, "/chord_prog G 不存在")
    assert "卡農" in bot.last(OWNER) and "❓" in bot.last(OWNER)


def test_text_fallbacks_fence_the_tab(g):
    _html, text = g.arp_reply([g.parse_chord("Am")], g.PATTERNS[0][1])
    assert "\n```\ne(1)" not in text and "```" in text
    body = text.split("```")[1]
    assert "e(1)" in body and "E(6)" in body
    assert "e（1 弦）＝最細的弦" in text
