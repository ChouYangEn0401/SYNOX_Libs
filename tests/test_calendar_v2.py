"""📅 calendar 0.2：行程的起訖與重複、撞時間、今日清單、月曆總覽、舊資料升級、網頁畫面的讀與動作、面板。全部是假資料。"""

import os
import re
import shutil
import subprocess

import pytest

from conftest import MONDAY, OWNER_ID, PACKS

APP = os.path.join(PACKS, "calendar", "app")


def store(bot):
    return bot.packs["calendar"].store


def read(bot, name, params=None, user=OWNER_ID):
    return bot.packs["calendar"].app_reads[name](user, params or {})


def do(bot, name, values, user=OWNER_ID):
    return bot.packs["calendar"].app_actions[name](user, values)


# ── 舊資料升級 ──────────────────────────────────────────────────────────

LEGACY = {"events": [
    {"id": 4, "title": "舊的開會", "day": "2026-11-10", "start": "14:00", "end": "15:30", "all_day": False,
     "location": "會議室", "note": "", "at": 1},
    {"id": 7, "title": "舊的旅行", "day": "2026-11-20", "start": "", "end": "", "all_day": True,
     "location": "", "note": "帶外套", "at": 2},
    {"id": 9, "title": "沒結束時間", "day": "2026-11-11", "start": "09:00", "end": "", "all_day": False,
     "location": "", "note": "", "at": 3}], "next_id": 10}


def test_legacy_data_is_read_and_ids_are_kept(make_bot, owner):
    bot = make_bot("calendar")
    store(bot).save(LEGACY)
    bot.say(owner, "/cal_list")
    last = bot.last(owner)
    assert "[4]" in last and "14:00–15:30" in last and "舊的旅行" in last and "[9]" in last and "09:00–10:00" in last
    assert store(bot).load(None) == LEGACY                          # 只讀不寫：存的還是舊格式
    bot.say(owner, "/cal 明天 18:00 新的")
    saved = store(bot).load(None)
    assert saved["v"] == 2 and saved["next_id"] == 11               # 編號接著舊的往後
    first = next(e for e in saved["events"] if e["id"] == 4)
    assert first["start"] == "2026-11-10T14:00" and first["end"] == "2026-11-10T15:30" and "day" not in first
    trip = next(e for e in saved["events"] if e["id"] == 7)
    assert trip["all_day"] and trip["start"] == trip["end"] == "2026-11-20" and trip["note"] == "帶外套"
    bot.say(owner, "/cal_rm 4")                                      # 舊編號照用
    assert "刪掉了" in bot.last(owner) and "舊的開會" in bot.last(owner)
    bot.say(owner, "/cal_edit 7 title=舊的旅行改名")
    assert "舊的旅行改名" in bot.last(owner)


def test_empty_or_broken_store_is_fine(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal_today")
    assert "今天沒有行程" in bot.last(owner)
    store(bot).save(["not", "a", "dict"])
    bot.say(owner, "/cal_week")
    assert "這 7 天沒有行程" in bot.last(owner)


# ── 行程：起訖、跨日、重複 ──────────────────────────────────────────────

def test_multi_day_and_cross_midnight(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal 出差 date=2026-11-12 end_date=2026-11-14")
    ev = store(bot).load(None)["events"][0]
    assert ev["all_day"] and ev["start"] == "2026-11-12" and ev["end"] == "2026-11-14"
    assert "整天至" in bot.last(owner)
    bot.say(owner, "/cal 夜班 date=2026-11-12 time=22:00-02:00")
    assert store(bot).load(None)["events"][1]["end"] == "2026-11-13T02:00"
    bot.say(owner, "/cal 錯 date=2026-11-12 end_date=2026-11-10")
    assert "❌" in bot.last(owner)


def test_repeat_series_occurrences_and_skip(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal 吉他課 date=2026-11-10 time=20:00-21:00 repeat=二四 until=2026-11-30")
    assert store(bot).load(None)["events"][0]["repeat"]["weekdays"] == [2, 4]
    month = read(bot, "month", {"month": "2026-11"})
    days = [d["date"] for d in month["days"] if any(i["kind"] == "event" for i in d["items"]) and d["in_month"]]
    assert days == ["2026-11-10", "2026-11-12", "2026-11-17", "2026-11-19", "2026-11-24", "2026-11-26"]
    assert month["counts"]["events"] == 1                           # 一串算一件
    bot.say(owner, "/cal_rm 1@2026-11-12")                          # 只跳過這一次
    assert "吉他課" in bot.last(owner)
    assert "2026-11-12" in store(bot).load(None)["events"][0]["repeat"]["skip"]
    day = read(bot, "day", {"date": "2026-11-12"})
    assert day["events"] == []
    bot.say(owner, "/cal_edit 1@2026-11-17 time=19:00-20:00")       # 只改這一次：拆出一筆一般的
    saved = store(bot).load(None)["events"]
    assert len(saved) == 2 and saved[1]["start"] == "2026-11-17T19:00" and "repeat" not in saved[1]
    bot.say(owner, "/cal_edit 1@2026-11-17 repeat=none")
    assert "❌" in bot.last(owner)
    bot.say(owner, "/cal_rm 1")                                      # 整串
    assert [e["id"] for e in store(bot).load(None)["events"]] == [2]


def test_status_and_cancel(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal 明天 10:00 看診")
    bot.say(owner, "/cal_status 1 not_done 感冒了")
    assert "這次沒做" in bot.last(owner)
    ev = store(bot).load(None)["events"][0]
    assert ev["status"] == "not_done" and ev["status_note"] == "感冒了"
    bot.say(owner, "/cal_status 1 cancelled")
    assert store(bot).load(None)["events"][0]["status"] == "cancelled"
    bot.say(owner, "/cal_week")
    assert "看診" not in bot.last(owner)                             # 取消的不列
    bot.say(owner, "/cal_status 1 bogus")
    assert "❌" in bot.last(owner)


# ── 撞時間 ──────────────────────────────────────────────────────────────

def test_clash_report_and_keep_both(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal 明天 14:00-15:00 甲")
    bot.say(owner, "/cal 明天 14:30-15:30 乙")
    assert "撞時間" in bot.last(owner) and "甲" in bot.last(owner)
    bot.say(owner, "/cal 全天活動 date=2026-11-10")              # 整天的不算撞
    assert "撞時間" not in bot.last(owner)
    bot.say(owner, "/cal_clash")
    assert "1 組撞時間" in bot.last(owner)
    bot.say(owner, "/cal_keep 1 2")
    assert "兩個都留" in bot.last(owner)
    bot.say(owner, "/cal_clash")
    assert "沒有撞時間" in bot.last(owner)
    assert len(read(bot, "clashes", {"all": "yes"})["clashes"]) == 1
    bot.say(owner, "/cal_keep 1 3")
    assert "沒有撞在一起" in bot.last(owner)


# ── 今日清單與 focus ───────────────────────────────────────────────────

def test_tasks_carry_over_and_focus(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal_task 買牛奶")
    bot.say(owner, "/cal_task 寫報告 priority=3 detail=第一章")
    bot.say(owner, "/cal_focus 2")
    assert "最重要的一件事：#2" in bot.last(owner)
    bot.say(owner, "/cal_today")
    out = bot.last(owner)
    assert "今天清單（0/2）" in out and "最重要：寫報告" in out
    assert out.index("寫報告") < out.index("買牛奶")                  # focus 排最前面
    bot.say(owner, "/cal_done 1")
    assert "做完了" in bot.last(owner)
    from datetime import datetime
    bot.set_now(datetime(2026, 11, 11, 8, 0))                       # 過了兩天
    bot.say(owner, "/cal_tasks")
    out = bot.last(owner)
    assert "寫報告" in out and "（順延）" in out and "買牛奶" not in out     # 沒做完的順延、做完的留在原來那天
    bot.say(owner, "/cal_task 新的")                                  # 下一次寫入才真的把日期改到今天
    saved = store(bot).load(None)
    assert next(t for t in saved["tasks"] if t["id"] == 2)["date"] == "2026-11-11"
    bot.say(owner, "/cal_focus -")
    assert "沒有指定" in bot.last(owner)
    bot.say(owner, "/cal_task_rm 2")
    assert "刪掉了" in bot.last(owner)
    bot.say(owner, "/cal_task_edit 9 title=x")
    assert "❌" in bot.last(owner)


def test_removing_a_card_clears_its_focus(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal_task 一件事")
    bot.say(owner, "/cal_focus 1")
    bot.say(owner, "/cal_task_rm 1")
    assert store(bot).load(None)["focus"] == {}


# ── 月曆總覽 ────────────────────────────────────────────────────────────

def test_month_view_is_read_only_and_grid_is_whole_weeks(make_bot, owner):
    bot = make_bot("calendar")
    bot.say(owner, "/cal 明天 14:00 看牙醫")
    bot.say(owner, "/cal_task 買牛奶")
    before = store(bot).data[None]
    m = read(bot, "month", {"month": "2026-11"})
    assert store(bot).data[None] == before
    assert len(m["days"]) % 7 == 0 and m["days"][0]["weekday"] == 0
    assert m["label"] == "2026 年 11 月" and m["prev"] == {"year": 2026, "month": 10}
    today = next(d for d in m["days"] if d["is_today"])
    assert [x["kind"] for x in today["items"]] == ["task"]
    assert m["counts"] == {"tasks": 1, "links": 0, "events": 1}
    bot.say(owner, "/cal_month")
    assert "11/10" in bot.last(owner) and "看牙醫" in bot.last(owner)
    assert read(bot, "month", {"month": "2026-12"})["next"] == {"year": 2027, "month": 1}
    with pytest.raises(Exception):
        read(bot, "month", {"month": "2026-13"})


def test_optional_link_sources_missing_or_broken_never_error(make_bot, owner):
    bot = make_bot("calendar")
    pack = bot.packs["calendar"]
    assert not hasattr(pack, "link_sources")                         # 沒有這個機制：月曆照樣畫
    assert read(bot, "month", {"month": "2026-11"})["counts"]["links"] == 0

    def habits(user, first, last):
        return [{"date": "2026-11-10", "text": "喝水", "done": True, "id": "h1"},
                {"date": "nope", "text": "壞列"}, {"date": "2026-12-31", "text": "範圍外"}]

    def broken(user, first, last):
        raise RuntimeError("來源壞了")

    pack.link_sources = [habits, broken]
    m = read(bot, "month", {"month": "2026-11"})
    links = [x for d in m["days"] for x in d["items"] if x["kind"] == "link"]
    assert [(x["text"], x["source"], x["done"]) for x in links] == [("喝水", "habits", True)]
    assert m["counts"]["links"] == 1


# ── 網頁畫面：讀、動作、匯出 ────────────────────────────────────────────

def test_web_actions_match_commands_and_work(make_bot, owner):
    bot = make_bot("calendar")
    pack = bot.packs["calendar"]
    declared = pack.manifest.web["app"]["actions"]
    assert set(pack.app_actions) == set(declared)
    for name in declared:                                            # 每個動作都有同名、同危險程度的指令
        assert str(bot.app.registry.node(f"cal.{name}").effect).split(".")[-1].upper() == declared[name].upper()
    out = do(bot, "add", {"text": "家長會", "date": "2026-11-21", "time": "19:00-20:30", "repeat": "六", "until": "2026-12-12"})
    assert out["event"]["repeat"] and out["clashes"] == []
    out = do(bot, "edit", {"n": "1", "location": "學校", "title": "家長會改"})
    assert out["event"]["location"] == "學校" and out["event"]["title"] == "家長會改"
    do(bot, "status", {"n": "1@2026-11-28", "state": "cancelled"})
    assert "2026-11-28" in store(bot).load(None)["events"][0]["repeat"]["skip"]
    t = do(bot, "task", {"text": "備課", "priority": "2"})["task"]
    assert do(bot, "focus", {"n": str(t["id"])})["focus"] == t["id"]
    assert do(bot, "done", {"n": str(t["id"]), "done": "yes"})["task"]["done"] is True
    day = read(bot, "day", {"date": "2026-11-09"})
    assert day["tasks"]["focus"]["title"] == "備課"
    assert read(bot, "agenda", {"from": "2026-11-09", "days": "30"})["count"] == 2     # 11/21、12/05（11/28 跳過、12/12 超出 30 天）
    with pytest.raises(Exception) as bad:
        do(bot, "add", {"text": "x", "date": "2026-02-30"})
    assert type(bad.value).__name__ == "AppError"
    with pytest.raises(Exception) as bad:
        do(bot, "rm", {"n": "99"})
    assert type(bad.value).__name__ == "AppError" and "找不到" in str(bad.value)
    do(bot, "task_rm", {"n": str(t["id"])})
    do(bot, "rm", {"n": "1"})
    assert store(bot).load(None)["events"] == []


def test_web_export_is_ics_without_touching_disk(make_bot, owner, tmp_path):
    bot = make_bot("calendar")
    do(bot, "add", {"text": "吉他", "date": "2026-11-10", "time": "20:00-21:00", "repeat": "二四", "until": "2026-11-30"})
    dl = bot.packs["calendar"].app_exports["ics"](OWNER_ID, {})
    text = dl.data.decode("utf-8")
    assert dl.filename.endswith(".ics") and "RRULE:FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=20261130T235959" in text
    assert text.startswith("BEGIN:VCALENDAR\r\n") and "DTSTART:20261110T200000" in text
    assert not os.path.exists(tmp_path / "data" / "users")


def test_users_do_not_see_each_other(make_bot):
    bot = make_bot("calendar", per_user=True)
    do(bot, "add", {"text": "甲的", "date": "2026-11-10", "time": "10:00"}, user=100)
    do(bot, "task", {"text": "甲的卡"}, user=100)
    assert read(bot, "agenda", {"from": "2026-11-09", "days": "7"}, user=555)["count"] == 0
    assert read(bot, "day", {"date": "2026-11-09"}, user=555)["tasks"]["total"] == 0


# ── 面板：「讀不到（Failed to fetch）」的原因 ───────────────────────────

def test_panel_uses_relative_url_and_route_has_tasks(make_bot, owner):
    html = open(os.path.join(PACKS, "calendar", "panel.html"), encoding="utf-8").read()
    assert 'fetch("api/today")' in html and "credentials" not in html and "/api/packs" not in html
    bot = make_bot("calendar")
    bot.say(owner, "/cal_task 買牛奶")
    bot.say(owner, "/cal 今天 18:00 晚餐")
    data = bot.route("calendar", "today")
    assert data["events"][0]["time"] == "18:00–19:00" and data["tasks"] == [
        {"id": 1, "title": "買牛奶", "done": False, "focus": False}]


# ── 畫面檔：照 SYNOX 的規則（沒有內嵌程式、沒有外部網址、不自己連線），JS 語法正確 ───

def app_files():
    for root, _dirs, names in os.walk(APP):
        for n in names:
            yield os.path.join(root, n)


def test_app_files_follow_the_rules():
    entry = open(os.path.join(APP, "index.html"), encoding="utf-8").read()
    scripts = re.findall(r"<script\b[^>]*>", entry)
    assert scripts[0] == '<script src="/_synox/kit.js">'
    assert all("src=" in s for s in scripts)                    # 沒有內嵌的 <script>…</script>
    for path in app_files():
        text = open(path, encoding="utf-8").read()
        rel = os.path.relpath(path, APP)
        assert not re.search(r"https?://", text), rel
        assert not re.search(r"\bfetch\(|XMLHttpRequest|localStorage|innerHTML|eval\(|serviceWorker", text), rel
        assert not re.search(r"\sstyle=\"|\son[a-z]+=\"", text), rel


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不在")
def test_app_js_parses(tmp_path):
    for n, path in enumerate(p for p in app_files() if p.endswith(".js")):
        copy = tmp_path / f"m{n}.mjs"                               # ES 模組：用 .mjs 才會照模組檢查
        copy.write_text(open(path, encoding="utf-8").read(), encoding="utf-8")
        out = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert out.returncode == 0, f"{path}: {out.stderr}"
