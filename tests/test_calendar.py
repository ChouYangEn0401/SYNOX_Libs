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


# ── 伺服器版：匯出檔（ADR-0003 S8） ─────────────────────────────────────

def test_server_mode_ics_export_leaves_nothing_behind(synox_bot):
    """S8：伺服器版成員匯出 .ics：檔寫在自己的 data/users/<id>/calendar/files/、送完那個資料夾是空的；別人的資料夾沒有東西。"""
    import sys

    from isdtelekit.users.roles import BasicRoles as R
    bot = synox_bot("calendar", mode="server")
    sys.modules["synox_pack_calendar"].now = lambda: MONDAY
    member, other = 77, 78
    bot.make(member, R.MEMBER, "a")
    bot.make(other, R.MEMBER, "b")
    bot.say(member, "/cal 明天 14:00 甲的行程", chat_id=member)
    bot.say(other, "/cal 明天 15:00 乙的行程", chat_id=other)
    captured = {}
    real = bot.api.send_document

    def capture(chat_id, document, caption=""):
        with open(document, "rb") as f:
            captured["bytes"] = f.read()
        captured["path"] = document
        return real(chat_id, document, caption)

    bot.api.send_document = capture
    bot.say(member, "/cal_ics", chat_id=member)
    data = bot.app.config.data_dir
    mine = os.path.join(data, "users", str(member), "calendar", "files")
    assert os.path.dirname(captured["path"]) == mine
    text = captured["bytes"].decode("utf-8")
    assert "甲的行程" in text and "乙的行程" not in text
    assert os.listdir(mine) == []                                   # 送完就刪，什麼都不留
    for who in (other, OWNER_ID):                                   # 別人的資料夾沒有匯出檔
        folder = os.path.join(data, "users", str(who), "calendar", "files")
        assert not os.path.isdir(folder) or os.listdir(folder) == []
    assert not os.path.exists(os.path.join(data, "calendar"))       # 也沒有寫到個人版那個共用位置
    assert not os.path.exists(os.path.join(data, "packs", "calendar"))


def test_personal_mode_ics_export_uses_real_folder(synox_bot, owner):
    """個人版（真的 SYNOX）：匯出檔在 data/calendar/files/、送完是空的；沒有 data/users/（ADR-0003 S5、P7）。"""
    import sys
    bot = synox_bot("calendar")
    sys.modules["synox_pack_calendar"].now = lambda: MONDAY
    bot.say(owner, "/cal 明天 14:00 看牙醫")
    seen = {}
    real = bot.api.send_document

    def capture(chat_id, document, caption=""):
        seen["path"] = document
        return real(chat_id, document, caption)

    bot.api.send_document = capture
    bot.say(owner, "/cal_ics")
    folder = os.path.join(bot.app.config.data_dir, "calendar", "files")
    assert os.path.dirname(seen["path"]) == folder and os.listdir(folder) == []
    assert not os.path.exists(os.path.join(bot.app.config.data_dir, "users"))


def test_ics_file_name_is_unique_and_old_exports_are_cleaned(make_bot, owner):
    """P1（ADR-0003）：每次匯出的檔名都不一樣（同一個人同時匯出兩次不互踩）；匯出前清掉留下的舊匯出檔
    （舊版的 export.ics、10 分鐘以前的），剛剛（10 分鐘內）的與不是匯出檔的不動。"""
    bot = make_bot("calendar")
    bot.say(owner, "/cal 明天 14:00 看牙醫")
    pack, module = bot.packs["calendar"], bot.modules["calendar"]
    names = []
    real = bot.api.send_document

    def capture(chat_id, document, caption=""):
        names.append(os.path.basename(document))
        return real(chat_id, document, caption)

    bot.api.send_document = capture
    folder = pack.path_of(owner)
    old = module.export_name(datetime(2026, 11, 9, 9, 0))           # 30 分鐘前
    recent = module.export_name(datetime(2026, 11, 9, 9, 25))       # 5 分鐘前（可能另一次匯出正在送）
    for leftover in ("export.ics", old, recent, "keep.txt"):
        with open(os.path.join(folder, leftover), "w", encoding="utf-8") as f:
            f.write("BEGIN:VCALENDAR")
    bot.say(owner, "/cal_ics")
    bot.say(owner, "/cal_ics")
    assert len(names) == 2 and names[0] != names[1]
    assert all(module.EXPORT_NAME.match(n) and n.startswith("export-20261109093000-") for n in names)
    assert sorted(os.listdir(folder)) == sorted([recent, "keep.txt"])
    assert module.stale_exports(["export.ics", old, recent, "keep.txt", "export-x.ics"],
                                datetime(2026, 11, 9, 9, 30)) == ["export.ics", old]
