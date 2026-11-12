"""📅 calendar pack：加、看、刪、匯出 .ics、面板資料。"""

import os
from datetime import datetime

from isdtelekit.core.registry import READ, WRITE

from conftest import MONDAY, OWNER_ID


def events(bot):
    return bot.packs["calendar"].store.load({})["events"]


def test_add_natural_language_and_views(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal 明天 14:00 看牙醫 location=仁愛診所")
    assert "加好了" in bot.last(owner) and "[1]" in bot.last(owner) and "14:00–15:00" in bot.last(owner)
    assert "/remind_add 13:30 看牙醫 date=2026-11-10" in bot.last(owner)
    bot.say(owner, "/cal 今天 下午3點到4點半 開會")
    bot.say(owner, "/cal 11/20 整天 員工旅遊")
    e = events(bot)
    assert [x["title"] for x in e] == ["看牙醫", "開會", "員工旅遊"]
    assert e[0]["location"] == "仁愛診所" and e[2]["all_day"] and e[1]["end"] == "16:30"

    bot.say(owner, "/cal_today")
    assert "開會" in bot.last(owner) and "看牙醫" not in bot.last(owner)
    bot.say(owner, "/cal_week")
    assert "看牙醫" in bot.last(owner) and "開會" in bot.last(owner) and "員工旅遊" not in bot.last(owner)
    bot.say(owner, "/cal_list 2")
    assert "接下來 2 個" in bot.last(owner) and "員工旅遊" not in bot.last(owner)
    bot.say(owner, "/cal_list")
    assert "整天 員工旅遊" in bot.last(owner)


def test_structured_fields_for_ai(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal 家長會 date=2026-11-21 time=19:00-20:30 note=帶聯絡簿")
    e = events(bot)[0]
    assert (e["day"], e["start"], e["end"], e["title"], e["note"]) == ("2026-11-21", "19:00", "20:30", "家長會", "帶聯絡簿")
    bot.say(owner, "/cal x date=2026-02-30")
    assert "❌" in bot.last(owner) and len(events(bot)) == 1


def test_errors_and_rm(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal 看牙醫")
    assert "看不出是哪一天" in bot.last(owner)
    bot.say(owner, "/cal 2/30 吃飯")
    assert "不是真的日期" in bot.last(owner)
    bot.say(owner, "/cal 明天 14:00")
    assert "要有標題" in bot.last(owner)
    bot.say(owner, "/cal")
    assert "用法" in bot.last(owner)
    bot.say(owner, "/cal 明天 9:00 a")
    bot.say(owner, "/cal 明天 10:00 b")
    bot.say(owner, "/cal_rm 1")
    assert "刪掉了" in bot.last(owner) and [x["id"] for x in events(bot)] == [2]   # 編號固定，不會往前遞補
    bot.say(owner, "/cal_rm 1")
    assert "沒有 [1]" in bot.last(owner)


def test_effects(make_bot):
    bot = make_bot("calendar")
    reg = bot.app.registry
    assert reg.node("cal.add").effect == WRITE and reg.node("cal.rm").effect == WRITE
    assert reg.node("cal.today").effect == READ and reg.node("cal.ics").effect == READ


def test_ics_export_is_valid_and_leaves_no_plaintext(make_bot, owner, tmp_path):
    bot = make_bot("calendar")
    bot.say(owner, "/cal_ics")
    assert "空的" in bot.last(owner)
    bot.say(owner, "/cal 明天 14:00 看牙醫, 洗牙; 複診 location=診所")
    bot.say(owner, "/cal 11/20 員工旅遊")
    captured = {}
    real = bot.api.send_document

    def capture(chat_id, document, caption=""):
        with open(document, "rb") as f:
            captured["bytes"] = f.read()
        captured["path"] = document
        return real(chat_id, document, caption)

    bot.api.send_document = capture
    bot.say(owner, "/cal_ics")
    text = captured["bytes"].decode("utf-8")
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert text.count("BEGIN:VEVENT") == 2
    assert "DTSTART:20261110T140000" in text and "DTEND:20261110T150000" in text
    assert "DTSTART;VALUE=DATE:20261120" in text and "DTEND;VALUE=DATE:20261121" in text
    assert "SUMMARY:看牙醫\\, 洗牙\\; 複診" in text and "LOCATION:診所" in text
    assert all(len(line.encode("utf-8")) <= 75 for line in text.split("\r\n"))
    assert captured["path"].startswith(str(tmp_path / "data" / "users" / str(owner) / "calendar" / "files"))  # 自己的資料夾
    assert not os.path.exists(captured["path"])                   # 送完就刪
    assert bot.api.files[-1][0] == "document"


def test_ics_folding_keeps_characters_whole(make_bot):
    bot = make_bot("calendar")
    m = bot.modules["calendar"]
    folded = m.ics_fold("SUMMARY:" + "很長的標題" * 20)
    parts = folded.split("\r\n ")
    assert len(parts) > 1 and all(len(p.encode("utf-8")) <= 75 for p in parts)
    assert "".join(parts) == "SUMMARY:" + "很長的標題" * 20


def test_panel_route(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal 今天 18:00 晚餐")
    bot.say(owner, "/cal 下週三 回診")
    data = bot.route("calendar", "today")
    assert data["today"] == "2026-11-09"
    assert [e["title"] for e in data["events"]] == ["晚餐"]          # 下週三超過 7 天
    assert data["events"][0]["time"] == "18:00–19:00"


def test_now_moves(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal 明天 8:00 早會")
    bot.set_now(datetime(2026, 11, 10, 7, 0))
    bot.say(owner, "/cal_today")
    assert "早會" in bot.last(owner)
    assert MONDAY.weekday() == 0 and OWNER_ID == owner
