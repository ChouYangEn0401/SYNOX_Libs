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
    assert "x 0 2 0 1 0" in bot.last(owner)                            # 預設：圖＋一行按法（沒有圖時退回這一行）
    bot.say(owner, "/chord_info Am7")
    assert "組成音" in bot.last(owner)
    bot.say(owner, "/chord_tp +2\n" + SHEET)
    reply = bot.last(owner)
    assert "G → A" in reply and "F#m" in reply and "E/G#" in reply
    bot.say(owner, "/chord_tp -1")                                       # 用上次的譜
    assert "G → F#" in bot.last(owner)
    bot.say(owner, "/chord_capo Bb")
    assert "Capo 3" in bot.last(owner)
    bot.say(owner, "/chord_arp Am F C G")
    assert "e(1)|" in bot.last(owner)
    bot.say(owner, "/chord_key Am")
    assert "Am G F E" in bot.last(owner)
    data = bot.route("guitar", "data")
    assert data["last"].startswith("前奏") and "C_major" in data["lib"] and data["patterns"]


def test_flattened_sheet_from_chat(g):
    # 聊天指令照空白切參數，換行到 pack 時已經變成空白：移法和譜在同一行、整份譜一行
    assert g.split_spec("到 Eb {\"a\": 1}") == ("到 Eb", "{\"a\": 1}")
    assert g.split_spec("G 到 A C G") == ("G 到 A", "C G")
    assert g.split_spec("+2 C G Am F") == ("+2", "C G Am F")
    assert g.split_spec("C G Am F") == (None, "C G Am F")
    flat = " ".join(SHEET.split())
    assert g.flattened(flat) and not g.flattened(SHEET) and not g.flattened("C G Am F")
    out = g.rewrite(flat, lambda ch: g.chord_name(ch, 2, (9, False)))
    assert "E/G#" in out and "這是[A]一首[E]簡單的[F#m]歌" in out and "我想你" in out
    assert [g.chord_name(c, 0, (7, False)) for c in g.chords_in(flat)][:4] == ["G", "D/F#", "Em", "C"]


def test_server_mode_keeps_sheets_apart(make_bot):
    bot = make_bot("guitar", per_user=True)
    bot.say(100, "/chord_tp +1\nC G Am F")
    assert "C G Am F" in bot.route("guitar", "data", user=100)["last"]
    assert bot.route("guitar", "data", user=555)["last"] == ""


# ── 小節譜 ──────────────────────────────────────────────────────────────────

def test_progression_bar_lines_and_plain(g):
    chart, unknown = g.parse_progression("| C | G D/F# | Am | F |")
    assert chart["sections"][0]["bars"] == [["C"], ["G", "D/F#"], ["Am"], ["F"]] and unknown == []
    chart, _ = g.parse_progression("C G Am F")                       # 沒有小節線＝一個和弦一小節
    assert chart["sections"][0]["bars"] == [["C"], ["G"], ["Am"], ["F"]]
    chart, _ = g.parse_progression("C / / / G / / / Am / G /")      # 照拍子排
    assert chart["sections"][0]["bars"] == [["C"], ["G"], ["Am", "G"]]


def test_progression_sections_specials_and_beats(g):
    text = "Key: G  Capo 2  3/4\n[主歌]\n| G . . | D/F# . G | % | N.C. |\n前奏： Em:2 C:1\n【副歌】×2\n| C | G |\n| Am | F | (x3)"
    chart, unknown = g.parse_progression(text)
    assert (chart["key"], chart["capo"], chart["time"]) == ("G", 2, "3/4") and unknown == []
    secs = chart["sections"]
    assert secs[0]["name"] == "主歌" and secs[0]["bars"] == [["G"], ["D/F#:2", "G:1"], ["%"], ["N.C."]]
    assert secs[1]["name"] == "前奏" and secs[1]["bars"] == [["Em:2", "C:1"]]      # 沒有小節線但寫了拍數：照拍子排
    assert secs[2]["name"] == "副歌" and secs[2]["repeat"] == 2 and secs[2]["bars"] == [["C"], ["G"]]
    assert secs[3]["repeat"] == 3 and secs[3]["bars"] == [["Am"], ["F"]]       # 只有這一行重複：拆成自己一段
    _, unknown = g.parse_progression("| C | 我 | G |")
    assert unknown == ["我"]


def test_progression_from_a_lyric_sheet(g):
    chart, _ = g.parse_progression(SHEET)
    intro, again, chorus = chart["sections"]
    assert intro["name"] == "前奏" and [b[0] for b in intro["bars"]] == ["G", "D/F#", "Em", "C", "G", "D/F#"]
    assert intro["lyrics"][4] == "我想你 我想你"                      # 和弦下面那行歌詞分到小節
    assert again["bars"] == [["Em"], ["C"]] and again["repeat"] == 2 and again["lyrics"][0] == "都是你的影子"   # 歌詞行尾的 (x2)
    assert chorus["name"] == "Chorus" and [b[0] for b in chorus["bars"]][-3:] == ["G", "D", "Em"]
    assert chorus["lyrics"][-1] == "歌"


def test_chart_json_normalize_and_transpose(g):
    text = """```json
{"title": "練習", "key": "C", "time": "4/4", "bars_per_line": 5,
 "sections": [{"name": "A", "repeat": 2, "bars": [["C"], ["G", "D/F#"], "Am . . G", ["%"], ["Xyz"]]},],
 "image": {"marks": [{"x": 0.1, "y": 0.2, "chord": "Am"}, {"x": 3, "y": 0.2, "chord": "C"}]}}
```"""
    chart, problems = g.chart_input(text)
    assert chart["sections"][0]["bars"] == [["C"], ["G", "D/F#"], ["Am:3", "G:1"], ["%"], ["?"]]
    assert chart["bars_per_line"] == 5 and chart["image"]["marks"] == [{"x": 0.1, "y": 0.2, "chord": "Am"}]
    assert any("Xyz" in p for p in problems)
    up = g.transpose_chart(chart, 3, (3, False))                     # C → Eb：降記號調
    assert up["key"] == "Eb" and up["sections"][0]["bars"][1] == ["Bb", "F/A"] and up["sections"][0]["bars"][2] == ["Cm:3", "Bb:1"]
    assert up["image"]["marks"][0]["chord"] == "Cm" and chart["sections"][0]["bars"][0] == ["C"]   # 原本的不動
    assert g.read_json('{"a": [1, 2.5, true, null, "\\u00e9"],}') == {"a": [1, 2.5, True, None, "é"]}
    assert g.read_json('{"a": 1') is None and g.chart_input("{oops")[0] is None


def test_chart_text_grid(g):
    chart, _ = g.chart_input("[主歌]\n| C | G D/F# | Am . . G | % | F |")
    text = g.chart_text(chart)
    assert text.split("\n") == ["【主歌】", "| C        | G  D/F#  | Am . . G | %        |", "| F        |"]


def test_chart_command_and_data(make_bot, owner):
    bot = make_bot("guitar")
    bot.say(owner, "/chord_chart [主歌]\n| C | G D/F# | Am | F |")
    reply = bot.last(owner)
    assert "【主歌】" in reply and "| C " in reply and "G  D/F#" in reply and "小節譜" in reply
    bot.say(owner, "/chord_chart +2")                                  # 用上次的譜
    reply = bot.last(owner)
    assert "C → D" in reply and "A  E/G#" in reply
    chart = bot.route("guitar", "data")["chart"]
    assert chart["key"] == "D" and chart["sections"][0]["bars"][1] == ["A", "E/G#"]
    bot.say(owner, "/chord_chart 到 Eb\n{\"sections\": [{\"bars\": [[\"C\"], [\"G\"]]}]}")
    assert "| Eb" in bot.last(owner)
    bot.say(owner, "/chord_chart hello")
    assert "用法" in bot.last(owner)


def test_server_mode_keeps_charts_apart(make_bot):
    bot = make_bot("guitar", per_user=True)
    bot.say(100, "/chord_chart C G Am F")
    assert bot.route("guitar", "data", user=100)["chart"]["sections"][0]["bars"][0] == ["C"]
    assert bot.route("guitar", "data", user=555)["chart"] is None
