"""習慣引擎：新增、打卡、承諾鎖、補打卡窗、加碼、配額、時間配額、暫停、這期夠了。全部假資料。"""
from datetime import date, datetime, timedelta

import pytest

T = date(2030, 1, 15)          # 星期二


def iso(n):
    return (T + timedelta(days=n)).isoformat()


def add(R, **kw):
    item = {"name": "讀書", "kind": "habit", "repeat": "daily", "start_date": iso(-10), "end_date": iso(40)}
    item.update(kw)
    return R.upsert(item)


def test_upsert_defaults_and_list(R):
    v = add(R)
    assert v["kind"] == "habit" and v["importance"] == "core" and v["habit_kind"] == "build"
    assert v["section"] == "core" and v["backfill_mode"] == "h12" and v["mode"] == "once"
    assert R.list_routines()[0]["id"] == v["id"]
    for gone in ("tool", "alarm", "cal_hidden", "override_dnd", "direction", "directions", "rest", "basis"):
        assert gone not in v


def test_validation_errors(R):
    with pytest.raises(ValueError):
        R.upsert({"name": ""})
    with pytest.raises(ValueError):
        R.upsert({"name": "a", "kind": "habit", "repeat": "daily"})            # 要有結束日
    with pytest.raises(ValueError):
        R.upsert({"name": "a", "repeat": "weekly"})                            # 要選星期
    with pytest.raises(ValueError):
        R.upsert({"name": "a", "repeat": "quota"})                             # 配額要搭配想養成
    with pytest.raises(ValueError):
        add(R, unit="minutes", repeat="daily")                                 # 時間配額要搭配 quota
    with pytest.raises(ValueError):
        add(R, importance="nope")


def test_check_states_and_future(R, when):
    v = add(R)
    r = R.check(v["id"], iso(0), "done")
    assert iso(0) in r["done_dates"] and r["today_state"] == "done"
    r = R.check(v["id"], iso(0), "cancel")
    assert iso(0) in r["skip_dates"] and iso(0) not in r["done_dates"]
    r = R.check(v["id"], iso(0), "none")
    assert r["today_state"] == "none"
    with pytest.raises(ValueError):
        R.check(v["id"], iso(1), "done")
    with pytest.raises(KeyError):
        R.check("nope", iso(0), "done")
    with pytest.raises(ValueError):
        R.check(v["id"], iso(0), "wat")


def test_backfill_modes(R, when):
    for mode, day, ok in [("none", -1, False), ("h12", -1, False), ("d3", -2, True), ("d3", -6, False),
                          ("always", -9, True)]:
        v = add(R, name=f"{mode}{day}", backfill_mode=mode)
        if ok:
            R.check(v["id"], iso(day), "done")
        else:
            with pytest.raises(ValueError):
                R.check(v["id"], iso(day), "done")


def test_h12_window(R, when):
    v = add(R, backfill_mode="h12")
    when.set(datetime(2030, 1, 15, 8, 0))                     # 昨天結束後 8 小時，還在窗內
    R.check(v["id"], iso(-1), "done")
    when.set(datetime(2030, 1, 15, 13, 0))
    v2 = add(R, name="另一個", backfill_mode="h12")
    with pytest.raises(ValueError):
        R.check(v2["id"], iso(-1), "done")


def test_commitment_lock_and_force(R):
    v = add(R)
    with pytest.raises(ValueError):
        R.delete(v["id"])
    steps = R.insist_steps("讀書", "刪除")
    assert len(steps) == 3 and "讀書" in steps[0]["body"]
    assert R.delete(v["id"], force=True) is True
    assert R.delete("nope") is False
    old = add(R, name="舊的", start_date=iso(-30), end_date=iso(-5))
    assert R.delete(old["id"]) is True                        # 承諾期結束後可以直接刪


def test_commitment_dates_locked_unless_forced(R):
    v = add(R)
    R.upsert({"id": v["id"], "name": "讀書", "kind": "habit", "end_date": iso(99)})
    assert R.raw(v["id"])["end_date"] == iso(40)
    R.upsert({"id": v["id"], "name": "讀書", "kind": "habit", "end_date": iso(99), "force": True})
    assert R.raw(v["id"])["end_date"] == iso(99)


def test_reps_mode(R):
    v = add(R, mode="reps")
    R.check(v["id"], iso(0), "rep")
    r = R.check(v["id"], iso(0), "rep")
    assert r["today_reps"] == 2
    r = R.check(v["id"], iso(0), "unrep")
    assert r["today_reps"] == 1
    r = R.check(v["id"], iso(0), "unrep")
    assert r["today_reps"] == 0 and r["today_state"] == "none"
    once = add(R, name="一次")
    with pytest.raises(ValueError):
        R.check(once["id"], iso(0), "rep")


def test_count_quota(R):
    v = add(R, name="運動", repeat="quota", times=2, period="week")
    assert v["mode"] == "reps" and v["quota"]["met"] is False and v["can_cancel"] is False
    R.check(v["id"], iso(0), "rep")
    r = R.check(v["id"], iso(-1), "rep")
    assert r["quota"]["met"] is True and r["due_today"] is False
    with pytest.raises(ValueError):
        R.check(v["id"], iso(-8), "rep")                      # 上一期已關帳


def test_time_quota(R):
    v = add(R, name="練琴", repeat="quota", unit="minutes", floor_min="1h", target_min="2h")
    assert R.is_time(v) and v["quota"]["floor"] == 60 and v["quota"]["target"] == 120
    with pytest.raises(ValueError):
        R.check(v["id"], iso(0), "done")
    r = R.log_minutes(v["id"], "45")
    assert r["day_minutes"] == 45 and r["quota"]["met"] is False
    r = R.log_minutes(v["id"], "30m")
    assert r["quota"]["met"] is True and r["quota"]["done_min"] == 75
    r = R.log_minutes(v["id"], "-75")
    assert r["day_minutes"] == 0 and iso(0) not in r["done_dates"]
    with pytest.raises(ValueError):
        R.log_minutes(v["id"], "abc")
    with pytest.raises(ValueError):
        add(R, name="x", repeat="quota", unit="minutes")      # 沒底線
    plain = add(R, name="plain")
    with pytest.raises(ValueError):
        R.log_minutes(plain["id"], "10")


def test_parse_minutes_and_fmt(R):
    assert R.parse_minutes("1h30m") == 90 and R.parse_minutes("1:30") == 90 and R.parse_minutes("1.5h") == 90
    assert R.parse_minutes("90分") == 90 and R.parse_minutes(0) is None and R.parse_minutes("-5") is None
    assert R.parse_minutes("-5", allow_negative=True) == -5
    assert R.fmt_min(90) == "1 小時 30 分" and R.fmt_min(120) == "2 小時" and R.fmt_min(45) == "45 分"


def test_links_hook_is_soft(R):
    from habits_engine import links
    v = add(R, name="練琴", repeat="quota", unit="minutes", floor_min=60)
    assert v["quota"]["planned"] == [] and v["quota"]["unlogged"] == []
    links.set_habit_blocks(lambda hid, a, b: 1 / 0)           # 壞掉的提供者不能拖垮這裡
    assert R.list_routines()[0]["quota"]["planned"] == []
    links.set_habit_blocks(lambda hid, a, b: [{
        "id": "e1", "title": "練琴時段", "date": T.isoformat(), "from": "22:00", "to": "23:00",
        "start_at": "2030-01-15T22:00", "end_at": "2030-01-15T23:00", "minutes": 60,
        "_s": datetime(2030, 1, 15, 22), "_e": datetime(2030, 1, 15, 23)}])
    q = R.list_routines()[0]["quota"]
    assert q["planned_min"] == 60 and "_s" not in q["planned"][0]


def test_pause_and_enough(R):
    v = add(R, name="運動", repeat="quota", times=3)
    r = R.pause(v["id"], True, iso(3))
    assert r["paused"] is True and r["paused_until"] == iso(3)
    r = R.pause(v["id"], False)
    assert r["paused"] is False
    r = R.set_enough(v["id"], True)
    assert r["enough_now"] is True and "enough_until" in r
    assert R.set_enough(v["id"], False)["enough_now"] is False
    with pytest.raises(ValueError):
        R.pause(v["id"], True, iso(-3))
    rem = R.upsert({"name": "倒垃圾", "repeat": "weekly", "weekdays": [1]})
    with pytest.raises(ValueError):
        R.pause(rem["id"])


def test_reminder_kind_due_and_notify(R, when):
    v = R.upsert({"name": "倒垃圾", "repeat": "weekly", "weekdays": [1], "at": "20:00"})   # 週二
    assert v["kind"] == "reminder" and v["due_today"] is True and v["should_notify"] is True
    assert R.mark_notified(v["id"]) is True
    assert R.list_routines()[0]["notified_today"] is True
    assert R.mark_done(v["id"]) is True and R.list_routines()[0]["done_today"] is True


def test_weekly_monthly_days_describe_and_next_due(R):
    wk = R.upsert({"name": "洗衣服", "repeat": "weekly", "weekdays": [4]})
    mo = R.upsert({"name": "月會", "repeat": "monthly", "nth": 1, "nth_weekday": 0})
    ev = R.upsert({"name": "清書籤", "repeat": "days", "every_days": 14})
    assert "週五" in wk["desc"] and "第一個" in mo["desc"]
    assert wk["next_due"] == iso(3)
    assert mo["next_due"] == "2030-02-04"
    assert ev["every_days"] == 14


def test_find_habit(R):
    a = add(R, name="喝水")
    add(R, name="伸展", start_date=iso(-30), end_date=iso(-20))
    assert R.find_habit(a["id"])["id"] == a["id"]
    assert R.find_habit("喝水")["id"] == a["id"]
    with pytest.raises(LookupError):
        R.find_habit("不存在")
    add(R, name="喝水機")
    with pytest.raises(LookupError):
        R.find_habit("喝")


def test_pin_and_settings(R):
    v = add(R)
    assert R.pin(v["id"], True)["pinned"] is True and R.pin("nope", True) is None
    assert R.show_missed_red() is False
    assert R.set_show_missed_red(True) is True and R.meta()["show_missed_red"] is True
    assert R.habit_default_at() == "21:00"


def test_stats_and_matrix_shapes(R):
    v = add(R, start_date=iso(-6), backfill_mode="always")
    for n in (-1, 0):
        R.check(v["id"], iso(n), "done")
    r = R.list_routines()[0]
    assert {"completed", "scheduled", "rate", "current_streak", "longest_streak"} <= set(r["stats"])
    assert len(r["matrix"]) >= 7 and all("status" in c for c in r["matrix"])


def test_rolling_every_two_days(R):
    rec = {"id": "h1", "name": "寫日記", "kind": "habit", "repeat": "days", "every_days": 2,
           "done_dates": [iso(n) for n in (-8, -6, -4, -2, 0)], "skip_dates": [],
           "start_date": iso(-9), "end_date": iso(60)}
    st = R.habit_stats(rec, T)
    assert st["completed"] == 5 and st["scheduled"] == 5 and st["rate"] == 100
    gap = dict(rec, done_dates=[iso(-8), iso(-2), iso(0)])
    w = R._rolling_walk(gap, T - timedelta(days=9), T, T)
    assert w["missed"] == [T - timedelta(days=6), T - timedelta(days=4)]


def test_migrate_old_record(R, mem):
    mem.save("routines", {"routines": [{"id": "old1", "name": "舊的", "last_done": iso(-2), "tool": "x",
                                        "direction": "y", "alarm": True}]})
    v = R.list_routines()[0]
    assert v["done_dates"] == [iso(-2)] and v["repeat"] == "days"


def test_old_basis_data_is_ignored_without_error(R):
    """原本那套程式的 basis（底線跟著行事曆行程的長度）沒有搬：舊資料裡有也照常讀，欄位丟掉、照 floor_min 算。"""
    v = add(R, repeat="quota", unit="minutes", floor_min=60, period="week")
    data = R.storage.load(R.DOC, None)
    rec = next(r for r in data["routines"] if r["id"] == v["id"])
    rec.update({"basis": {"event": "7", "title": "吉他課", "floor_x": 1, "target_x": 2}, "floor_x": 1, "target_x": 2})
    R.storage.save(R.DOC, data)
    got = next(r for r in R.list_routines() if r["id"] == v["id"])
    assert got["floor_min"] == 60 and not {"basis", "floor_x", "target_x"} & set(got)


def test_bad_number_in_a_field_is_a_refusal_not_a_crash(R):
    """欄位裡看不懂的數字回「說不行」（bridge 轉成給人看的話）；程式自己的 KeyError 不會被當成「找不到」。"""
    errors = R.errors
    with pytest.raises(errors.Refused):
        add(R, repeat="days", every_days="abc")
