"""猜類別：關鍵字取最長、明寫餐別、食物依時間、猜不到回 None。"""
from datetime import datetime, timedelta, timezone

import pytest

from money_engine import db
from money_engine.services import classify, refdata


@pytest.mark.parametrize("desc,time,expected", [
    ("麻油雞", "20:13", "晚餐"),
    ("麻油雞", "12:10", "午餐"),
    ("鹹酥雞", "21:00", "點心宵夜"),          # 關鍵字比「雞」長，勝過依時間
    ("雞腿便當", "19:00", "晚餐"),            # 便當是三餐的關鍵字 → 依時間
    ("蛋餅豆漿", "07:40", "早餐"),
    ("牛肉麵", "23:30", "點心宵夜"),          # 深夜吃東西＝宵夜
    ("下午吃了一份炒飯", "15:30", "點心宵夜"),
    ("奶茶", None, "飲料"),
    ("衛生紙", None, "日用品"),
    ("捷運加值", None, "大眾運輸"),
    ("小黃 回家", None, "計程車"),
    ("燒肉聚餐（和同事）", "19:40", "吃好"),
    ("晚餐 漢堡", "15:00", "晚餐"),          # 明寫晚餐就照寫的
    ("３Ｃ 配件", None, "大額採買"),          # 全形也認得
])
def test_guess(conn, desc, time, expected):
    cat, reason = classify.guess(conn, desc, time=time, date="2037-10-09")
    assert cat is not None and cat["name"] == expected, reason


AMBIGUOUS_CASES = [
    ("餅乾 牛奶", "20:00", "點心宵夜"),       # 餅乾和牛奶一樣長：點心宵夜排前面；零食不分餐
    ("洋芋片", "12:00", "點心宵夜"),
    ("伴手禮", None, "禮物"),
    ("地方名產", None, "禮物"),
    ("牛奶 麥片", "08:00", "日用品"),          # 買回家的原料，不是當場吃掉的那一餐
    ("火腿蛋吐司", "07:40", "早餐"),           # 吐司留在早餐（早餐店當場吃）
    ("雞蛋糕", "16:00", "點心宵夜"),           # 雞蛋和蛋糕一樣長：點心宵夜排前面
]


@pytest.mark.parametrize("desc,time,expected", AMBIGUOUS_CASES)
def test_ambiguous_items(conn, desc, time, expected):
    """買零食、牛奶麥片、出去玩買的伴手禮：不要落到待分類，也不要被當成一餐。"""
    snack = refdata.system_category(conn, "snack")
    with db.write(conn) as w:                       # 起始檔的點心沒有「蛋糕」：這裡補上，才測得到一樣長的平手
        refdata.update_category(w, snack["id"], keywords=snack["keywords"] + ["蛋糕"])
    cat, reason = classify.guess(conn, desc, time=time, date="2037-10-09")
    assert cat is not None and cat["name"] == expected, reason


def test_unknown_and_food_without_time(conn):
    assert classify.guess(conn, "ABC-123", time=None, date="2037-10-09")[0] is None
    cat, reason = classify.guess(conn, "麻油雞", time=None, date="2037-10-09")
    assert cat is None and "幾點" in reason
    assert classify.guess(conn, "", date="2037-10-09")[0] is None


def test_today_without_time_uses_now(conn, monkeypatch):
    from money_engine import clock
    monkeypatch.setattr(clock, "now", lambda: datetime(2037, 10, 10, 12, 30, tzinfo=timezone(timedelta(hours=8))))
    assert classify.guess(conn, "麻油雞", date="2037-10-10")[0]["name"] == "午餐"


def test_income_keywords(conn):
    with db.write(conn) as w:
        refdata.add_category(w, name="賣東西", group="收入", kind="income", keywords=["賣掉", "二手"])
    assert classify.guess(conn, "十月薪水", kind="income")[0]["name"] == "薪資"
    assert classify.guess(conn, "二手書賣掉", kind="income")[0]["name"] == "賣東西"


@pytest.mark.parametrize("desc,merchant,time,expected", [
    ("範例雲端 月費", "SAMPLE*CLOUD", "15:42", "訂閱"),
    ("藍牙耳機", "", None, "購物"),
    ("SAMPLE APP 訂閱", "SAMPLE* APP SUB", None, "訂閱"),
    ("範例廚房", "FP一範例廚房", "12:46", "午餐"),                # 帳單上的店名也看得出是吃的，依時間分餐
    ("範例炸雞", "範例炸雞 Sample", "13:00", "午餐"),
])
def test_statement_lines_are_classified_like_everything_else(conn, desc, merchant, time, expected):
    cat, _ = classify.guess(conn, desc, merchant=merchant, time=time, date="2037-10-05")
    assert cat is not None and cat["name"] == expected


def test_a_correction_teaches_the_next_time(conn):
    """改過一次類別，下次同一家店自己歸過去（不管從哪裡改類別都算）；吃的不學（餐別看時間）。"""
    from money_engine.services import editing, ingest

    def add(desc, **kw):
        out = ingest.run(conn, {"records": [{"type": "expense", "amount": 300, "date": "2037-10-09", "desc": desc, **kw}]},
                         actor="t", source="t")
        return out["results"][0]
    assert classify.guess(conn, "範例商行", merchant="範例商行本店", date="2037-10-09")[0] is None
    first = add("範例商行", merchant="範例商行本店")
    assert first.get("category") in (None, "待分類")
    with db.write(conn) as w:
        editing.edit(w, first["id"], category="進修")
    cat, why = classify.guess(conn, "範例商行 二號店", merchant="", date="2037-10-20")
    assert cat["name"] == "進修" and "之前" in why
    again = add("範例商行", merchant="範例商行本店")
    assert again["category"] == "進修"
    meal = add("某某自助餐", merchant="某某自助餐", time="12:30")
    with db.write(conn) as w:
        editing.edit(w, meal["id"], category="午餐")             # 吃的：不學店名
    assert conn.execute("SELECT COUNT(*) FROM merchant_aliases WHERE pattern LIKE '%自助餐%'").fetchone()[0] == 0


def test_reclassify_fixes_the_unsorted_rows_in_one_undoable_batch(conn):
    """停在待分類的帳單：按「自動分類」才改，猜不出的不動，一個批次可以復原。"""
    from money_engine.services import batches, transactions as tx
    acc = refdata.resolve_account(conn, "範例 Demo")["id"]
    with db.write(conn) as w:
        ids = {name: tx.create(w, kind="expense", amount=amt, date="2037-10-05", time=t, account_id=acc, description=name,
                               merchant=name)["id"]
               for name, amt, t in (("範例雲端月費", 300, "15:42"), ("範例廚房", 120, "12:46"), ("神秘商行", 99, "10:00"))}
    with db.write(conn) as w:
        done = classify.reclassify(w)
        batch = w.batch_id
    by = {d["desc"]: d["category"] for d in done}
    assert by == {"範例雲端月費": "訂閱", "範例廚房": "午餐"}                 # 神秘商行猜不出：不動
    assert tx.get(conn, ids["神秘商行"])["category"] is None
    assert tx.get(conn, ids["範例雲端月費"])["category"] == "訂閱"
    with db.write(conn) as w:
        batches.revert(w, batch)
    assert tx.get(conn, ids["範例雲端月費"])["category"] is None             # 一次全退


def test_reclassify_returns_a_batch_for_undo_only_when_something_changed(conn):
    """有改才留批次（才有東西可以復原）；沒有東西可改就不留。"""
    from money_engine.services import transactions as tx
    with db.write(conn) as w:
        tx.create(w, kind="expense", amount=300, date="2037-10-06", time="10:07",
                  account_id=refdata.resolve_account(conn, "範例 Demo")["id"], description="範例雲端月費",
                  merchant="範例雲端月費")
    with db.write(conn) as w:
        done = classify.reclassify(w)
        batch_id = w.batch_id if w.changes else None
    assert len(done) == 1 and done[0]["category"] == "訂閱" and batch_id
    assert conn.execute("SELECT 1 FROM batches WHERE id = ?", (batch_id,)).fetchone()
    with db.write(conn) as w:
        again = classify.reclassify(w)
        empty = w.batch_id
        changed = w.changes
    assert again == [] and changed == 0
    assert conn.execute("SELECT 1 FROM batches WHERE id = ?", (empty,)).fetchone() is None
