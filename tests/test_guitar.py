# -*- coding: utf-8 -*-
"""🎸 吉他和弦：樂理（解析、拼寫、猜調、移調保留欄位）、每個按法的組成音、指令跑一輪、面板資料、伺服器版每人一份。"""

import pytest

from conftest import load_module

SHEET = """前奏： G  D/F#  Em  C
G              D/F#
我想你 我想你
Em              C
都是你的影子 (x2)
[Chorus]
Am7    D7sus4  G
這是[G]一首[D]簡單的[Em]歌"""


@pytest.fixture(scope="module")
def g():
    return load_module("guitar")


def test_parse_chord(g):
    c = g.parse_chord("Bbmaj7/D")
    assert (c["root"], c["quality"], c["bass"], c["suffix"]) == (10, "maj7", 2, "maj7")
    assert g.parse_chord("C#m7b5")["quality"] == "m7b5" and g.parse_chord("F#ø")["quality"] == "m7b5"
    assert g.parse_chord("Gsus")["quality"] == "sus4" and g.parse_chord("Am(maj7)")["quality"] == "mmaj7"
    assert g.parse_chord("G13")["approx"] and g.parse_chord("G13")["quality"] == "7"
    for word in ("我", "Hello", "Go", "Be", "H7", "Cx"):
        assert g.parse_chord(word) is None, word


def test_every_voicing_plays_the_right_notes(g):
    """收錄的、範本推出來的、自動找的：每條弦的音都在和弦裡、該有的音都在、最低音對。"""
    for r in range(12):
        for q in g.QUAL_ORDER:
            vs = g.voicings(r, q)
            assert vs, (g.SHARP[r], q)
            for v in vs:
                ok = g._easy_ok(r, q, v["frets"]) if v["kind"] == "easy" else g.analyze(r, q, v["frets"])
                assert ok, (g.SHARP[r], q, v)
                assert len(v["fingers"]) == 6
    for name, frets, _, _ in g.SLASH:
        c = g.parse_chord(name)
        assert g.analyze(c["root"], c["quality"], frets, c["bass"]), name
    for name in ("Ab/C", "F#m/E", "Bb/D", "G/A"):
        c = g.parse_chord(name)
        v = g.voicings(c["root"], c["quality"], c["bass"])[0]
        assert g.analyze(c["root"], c["quality"], v["frets"], c["bass"]), name


def test_spelling_follows_the_key(g):
    c = g.parse_chord("D/F#")
    assert g.chord_name(c, 3, (10, False)) == "F/A"                   # G → Bb：降記號調
    assert g.chord_name(g.parse_chord("Em"), -1, (6, False)) == "D#m"  # G → F#：升記號調
    assert g.chord_name(g.parse_chord("Cmaj7"), 1) == "C#maj7"         # 尾巴照原譜


def test_detect_key(g):
    chords = g.chords_in(SHEET)
    assert g.detect_key(chords) == (7, False)
    assert g.detect_key([g.parse_chord(x) for x in "Am F C G Am".split()]) == (9, True)


def test_transpose_keeps_columns_and_inline(g):
    out = g.rewrite("G      Em  C\n我想你\n[G]你好[D/F#]", lambda ch: g.chord_name(ch, 2, (9, False)))
    lines = out.split("\n")
    assert lines[0] == "A      F#m D" and lines[1] == "我想你" and lines[2] == "[A]你好[E/G#]"
    assert g.chord_line("我想你 我想你") is None and g.chord_line("I love you") is None
    assert g.chord_line("| C  G | Am (x2)") is not None


def test_parse_shift(g):
    src = (7, False)
    assert g.parse_shift("+2", src) == (2, None) and g.parse_shift("降1", src) == (11, None)
    assert g.parse_shift("G→Bb", src) == (3, (10, False)) and g.parse_shift("到 A", src) == (2, (9, False))
    assert g.parse_shift("hello", src) is None
    assert g.split_spec("+2 C G Am F") == ("+2", "C G Am F")


def test_capo_prefers_open_shapes(g):
    chords = [c for _, c in g.key_chords((10, False))[:6]]           # Bb 調
    best = g.capo_options(chords, (10, False))[0]
    assert best[0] == 3 and g.key_name(best[1]) == "G"


def test_commands(make_bot, owner):
    bot = make_bot("guitar")
    bot.say(owner, "/chord Am7")
    assert "x 0 2 0 1 0" in bot.last(owner) and "組成音" in bot.last(owner)
    bot.say(owner, "/chord_tp +2\n" + SHEET)
    reply = bot.last(owner)
    assert "G → A" in reply and "F#m" in reply and "E/G#" in reply
    bot.say(owner, "/chord_tp -1")                                       # 用上次的譜
    assert "G → F#" in bot.last(owner)
    bot.say(owner, "/chord_capo Bb")
    assert "Capo 3" in bot.last(owner)
    bot.say(owner, "/chord_arp Am F C G")
    assert "e|" in bot.last(owner)
    bot.say(owner, "/chord_key Am")
    assert "Am G F E" in bot.last(owner)
    data = bot.route("guitar", "data")
    assert data["last"].startswith("前奏") and "C_major" in data["lib"] and data["patterns"]


def test_server_mode_keeps_sheets_apart(make_bot):
    bot = make_bot("guitar", per_user=True)
    bot.say(100, "/chord_tp +1\nC G Am F")
    assert "C G Am F" in bot.route("guitar", "data", user=100)["last"]
    assert bot.route("guitar", "data", user=555)["last"] == ""
