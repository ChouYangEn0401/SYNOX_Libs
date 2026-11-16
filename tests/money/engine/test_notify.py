"""要主動送的週報、檢討書。今天 2037-10-10 是週六；本期 2037-10＝10/05–11/04，檢討書 10/30。"""
from datetime import date

import pytest

from money_engine import db, paths
from money_engine.services import ingest, notify, plans, reviews, settings


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        plans.set_plan(w, "2037-10", income_expected=40000, buckets={"giving": 4000},
                       group_budgets={"三餐": 9300, "購物": 3100})
    ingest.run(c, {"records": [
        {"type": "expense", "date": "2037-10-05", "amount": 120, "desc": "排骨便當", "category": "午餐"},
        {"type": "expense", "date": "2037-10-08", "amount": 1300, "desc": "燒肉", "category": "吃好"},
        {"type": "expense", "date": "2037-10-09", "amount": 800, "desc": "外套", "category": "購物"},
        {"type": "expense", "date": "2037-10-02", "amount": 900, "desc": "上週的", "category": "購物"},
    ]})
    return c


def test_week_report_on_or_after_friday(conn):
    with db.write(conn) as w:
        items = notify.due(w)
    assert [i["kind"] for i in items] == [notify.WEEK] and items[0]["key"] == "2037-W41"
    text = items[0]["text"]
    assert text.startswith("📊 這週的錢（10/5–10/10，週一到週六）")
    assert "生活費花了 2,220" in text and "上週一整週 900" in text
    assert "花最多的：吃好喝好 1,300、購物 800" in text and "最大一筆：燒肉 1,300" in text
    assert "這個月生活費還可以花" in text and "❌" not in text
    with db.write(conn) as w:
        again = notify.due(w)
    assert [i["id"] for i in again] == [items[0]["id"]]                     # 同一週不會多一份
    with db.write(conn) as w:
        assert notify.mark_sent(w, [items[0]["id"]]) == [items[0]["id"]]
        assert notify.due(w) == [] and notify.mark_sent(w, [items[0]["id"]]) == []


def test_week_report_waits_for_the_weekday(conn, home):
    with paths.use_home(home, today=date(2037, 10, 8)):                     # 週四
        with db.write(conn) as w:
            assert notify.WEEK not in [i["kind"] for i in notify.due(w)]
            settings.put(w, "notify.weekday", 3)
            assert notify.WEEK in [i["kind"] for i in notify.due(w)]


def test_week_letter_goes_with_the_numbers(conn):
    """週五晚上除了數字週報，再附一篇這一週的檢討書（事先寫好存進來的）。"""
    with db.write(conn) as w:
        rid = reviews.save_manual(w, "week", "2037-W41", {
            "grade": "B", "title": "外食偏多", "summary": "這週生活費還在日額度裡。", "good": ["午餐穩"],
            "watch": ["燒肉 1,300 ❌"], "strategy": ["週末外食只一次"]})
        items = notify.due(w)
    assert [(i["kind"], i["key"]) for i in items] == [(notify.WEEK, "2037-W41"), (notify.WEEK_REVIEW, "2037-W41")]
    assert items[1]["text"].startswith("📝 這週的檢討書") and "外食偏多" in items[1]["text"]
    assert "下週：" in items[1]["text"] and "❌" not in items[1]["text"]
    with db.write(conn) as w:
        notify.mark_sent(w, [i["id"] for i in items])
        assert notify.due(w) == []                                          # 送過了不會再送
    assert reviews.get(conn, rid)["sent_at"]                                # 檢討的摘要不會再送一次


def test_week_letter_waits_and_can_be_turned_off(conn):
    with db.write(conn) as w:
        assert [i["kind"] for i in notify.due(w)] == [notify.WEEK]          # 檢討還沒做好：只送數字，不等它
        settings.put(w, "review.weekly", False)
        reviews.save_manual(w, "week", "2037-W41", {"grade": "B", "title": "外食偏多", "summary": "還在日額度裡。",
                                                    "good": ["午餐穩"], "watch": ["燒肉"], "strategy": ["少一次"]})
        assert [i["kind"] for i in notify.due(w)] == [notify.WEEK]          # 關掉就只有數字週報


def test_month_review_letter(conn, home):
    with paths.use_home(home, today=date(2037, 10, 30)):
        with db.write(conn) as w:
            settings.put(w, "notify.weekday", 6)                            # 週五的週報先不看
            assert notify.due(w) == []                                      # 檢討還沒做好：先等
            rid = reviews.save_manual(w, "month", "2037-10", {
                "grade": "B", "title": "吃好偏高", "summary": "生活費還在預算裡。", "good": ["午餐穩"], "watch": ["燒肉"],
                "strategy": ["下個月吃好 2,000"]})
            items = notify.due(w)
        assert [(i["kind"], i["key"]) for i in items] == [(notify.MONTH, "2037-10")]
        assert items[0]["text"].startswith("📝 這個月的檢討書") and "吃好偏高" in items[0]["text"]
        with db.write(conn) as w:
            notify.mark_sent(w, [items[0]["id"]])
    assert reviews.get(conn, rid)["sent_at"]                                 # 檢討的摘要不會再送一次


def test_month_fallback_when_the_review_never_arrived(conn, home):
    """月檢討一直沒存進來：過幾天改送數字版，不讓它默默消失。"""
    with paths.use_home(home, today=date(2037, 11, 1)):
        with db.write(conn) as w:
            settings.put(w, "notify.weekday", 6)
            items = [i for i in notify.due(w) if i["kind"] == notify.MONTH]
    assert items and items[0]["title"] == "檢討書（數字版）" and "先給你數字" in items[0]["text"]


def test_due_then_sent_then_nothing_left(conn):
    """送出去之後記成已送：下一次就沒有要送的；最近的清單看得到送出的時間。"""
    with db.write(conn) as w:
        items = notify.due(w)
    assert len(items) == 1
    mid = items[0]["id"]
    with db.write(conn) as w:
        assert notify.mark_sent(w, [mid]) == [mid]
    with db.write(conn) as w:
        assert notify.due(w) == []
    assert next(r for r in notify.recent(conn) if r["id"] == mid)["sent_at"]
