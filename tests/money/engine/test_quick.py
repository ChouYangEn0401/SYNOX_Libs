"""快速記帳的選項（聊天按鈕用）與大量紀錄的試算摘要。今天 2037-10-10。"""
from datetime import date

import pytest

from money_engine.errors import NotFound
from money_engine.services import ingest, quick


@pytest.fixture
def conn(conn):
    rows = [("2037-10-01", "午餐", "排骨便當", 120), ("2037-10-02", "午餐", "排骨便當", 120),
            ("2037-10-03", "午餐", "排骨便當", 130), ("2037-10-04", "午餐", "牛肉麵", 180),
            ("2037-10-05", "午餐", "牛肉麵", 180), ("2037-10-06", "午餐", "滷肉飯", 70),
            ("2037-10-07", "午餐", "水餃", 90), ("2037-10-08", "午餐", "炒飯", 100),
            ("2037-10-09", "午餐", "拉麵", 260), ("2037-10-09", "飲料", "奶茶", 60),
            ("2037-10-08", "飲料", "奶茶", 60), ("2037-03-01", "購物", "很久以前", 999)]
    ingest.run(conn, {"records": [{"type": "expense", "date": d, "amount": a, "desc": desc, "category": cat}
                                  for d, cat, desc, a in rows]})
    return conn


def test_categories_by_use_and_meal_now(conn):
    d = quick.categories(conn, limit=3, now="12:30")
    assert [c["name"] for c in d["items"]] == ["午餐", "飲料", "早餐"]        # 午餐是現在這一餐也是最常用的
    assert d["items"][0]["now"] and d["items"][0]["count"] == 9 and d["has_more"] and d["total"] > 10
    d = quick.categories(conn, limit=3, now="07:50")
    assert d["items"][0]["name"] == "早餐" and d["meal_now"] == "早餐"
    names = [c["name"] for c in quick.categories(conn, limit=100)["items"]]
    assert "購物" in names and "薪資" not in names                          # 收入類別不列；超過 180 天的不算次數
    assert next(c for c in quick.categories(conn, limit=100)["items"] if c["name"] == "購物")["count"] == 0


def test_items_recent_frequent_more(conn):
    d = quick.items(conn, "午餐", limit=2)
    assert d["total"] == 6
    assert [x["desc"] for x in d["recent"]] == ["拉麵", "炒飯"]
    assert [x["desc"] for x in d["frequent"]] == ["排骨便當", "牛肉麵"]       # 跟最近重複的不再列
    assert d["frequent"][0]["amount"] == 120 and d["frequent"][0]["count"] == 3   # 常見金額＝出現最多次的
    assert {x["desc"] for x in d["more"]} == {"滷肉飯", "水餃"} and d["more_total"] == 2 and not d["has_more"]
    with pytest.raises(NotFound):
        quick.items(conn, "不存在的")


def test_quick_limit_and_recent_item_shape(conn):
    d = quick.categories(conn, limit=2, now="12:00")
    assert d["items"][0]["name"] == "午餐" and d["has_more"]
    d = quick.items(conn, "飲料")
    assert d["recent"][0] == {"desc": "奶茶", "count": 2, "last_date": "2037-10-09", "account": "現金", "amount": 60}


def test_bulk_dry_run_summary(conn):
    data = ('{"source":"chat","source_ref":"chat:9","records":['
            '{"type":"expense","amount":260,"date":"2037-10-09","desc":"拉麵"},'
            '{"type":"expense","amount":45,"date":"2037-10-09","desc":"豆漿"},'
            '{"type":"income","amount":500,"date":"2037-10-09","desc":"退稅"}]}')
    out = ingest.run(conn, data, dry_run=True)
    s = out["summary"]
    assert out["dry_run"] and out["batch_id"] is None
    assert s["new"] == 2 and s["possible_duplicate"] == 1 and s["replayed"] == 0
    assert s["expense"] == 305 and s["income"] == 500


def test_lunch_prompt_asks_only_on_weekdays_without_a_lunch_yet(conn):
    mon = date(2037, 10, 12)
    d = quick.lunch(conn, day=mon)
    assert d["ask"] and d["weekday"] and not d["recorded"]
    # 最常出現的店家＋金額（排骨便當 120 也兩次，一樣多取最近的）
    assert (d["suggestion"]["desc"], d["suggestion"]["amount"], d["suggestion"]["count"]) == ("牛肉麵", 180, 2)
    assert [(x["desc"], x["amount"]) for x in d["others"]] == [("拉麵", 260), ("炒飯", 100), ("水餃", 90)]   # 最近吃過的別家
    assert d["suggestion"]["account"] == "現金"
    assert not quick.lunch(conn, day=date(2037, 10, 10))["ask"]                       # 週六不問
    d = quick.lunch(conn, day=date(2037, 10, 9))                                       # 週五，已經有一筆拉麵 260
    assert d["weekday"] and d["recorded"] and not d["ask"]                             # 今天已經記了就不問


def test_lunch_prompt_without_history_still_asks(conn):
    d = quick.lunch(conn, day=date(2038, 3, 1))                                        # 45 天內沒有午餐
    assert d["ask"] and d["suggestion"] is None and d["others"] == []


def test_lunch_weekday_and_weekend(conn):
    d = quick.lunch(conn, day=date(2037, 10, 12))
    assert d["ask"] is True and (d["suggestion"]["desc"], d["suggestion"]["amount"]) == ("牛肉麵", 180)
    d = quick.lunch(conn, day=date(2037, 10, 11))                                      # 星期日
    assert d["ask"] is False and not d["weekday"]
