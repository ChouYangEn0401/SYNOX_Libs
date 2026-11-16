"""額度挪用：這期從 A 格挪額度去補 B 格（不記帳、不搬錢），而且要有地方好好統計。今天 2037-10-10（本期 2037-10）。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput, NotFound
from money_engine.services import batches, envelopes, ingest, moves, plans, stats, wallet
from money_engine.services import home as homepage


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        plans.add_bucket(w, "旅遊基金")                           # extra1
        plans.add_bucket(w, "水電瓦斯")                           # extra2（滾存）
        for key in ("2037-09", "2037-10"):
            plans.set_plan(w, key, income_expected=40000, buckets={"extra1": 2000, "extra2": 1500, "saving": 10000, "fixed": 3000},
                           group_budgets={"三餐": 9000})
        plans.set_bucket(w, "extra2", rollover=True, rollover_from="2037-09")
    yield c
    c.close()


def _move(conn, a, b, n, note="", period=None):
    with db.write(conn) as w:
        return moves.move(w, a, b, n, note, period)


def _row(conn, bid, key="2037-10"):
    return next(r for r in envelopes.period_rows(conn, key)[1] if r["id"] == bid)


def _reserved(conn, bid):
    return next((r["amount"] for r in wallet.summary(conn)["reserved_items"] if r["id"] == bid), 0)


def test_move_changes_both_buckets_everywhere(conn):
    before = plans.get(conn, "2037-10")
    living = before["living_budget"]
    assert _reserved(conn, "saving") == 10000
    r = _move(conn, "saving", "living", 2000, "生活費超支")
    assert (r["from_name"], r["to_name"], r["amount"], r["warnings"]) == ("存錢", "生活費", 2000, [])
    p = plans.get(conn, "2037-10")
    assert p["buckets"]["saving"] == 8000 and p["buckets"]["living"] == living + 2000
    assert p["planned"]["saving"] == 10000 and p["moves"] == {"saving": -2000, "living": 2000}
    assert p["living_budget"] == living + 2000 and p["daily_budget"] == round((living + 2000) / 31)
    assert p["group_budgets"] == before["group_budgets"]                                # 大類的軟性預算不動
    assert sum(p["buckets"].values()) == sum(before["buckets"].values())                # 總額不變（只是換格子）
    raw = plans.get(conn, "2037-10", with_moves=False)
    assert raw["buckets"]["saving"] == 10000 and raw["living_budget"] == living         # 規劃頁看的是原本的
    assert stats.period_summary(conn, "2037-10")["living"]["budget"] == living + 2000    # 還可以花多少跟著變
    h = _row(conn, "saving")
    assert (h["plan"], h["planned"], h["moved"], h["left"]) == (8000, 10000, -2000, 8000)
    lv = _row(conn, "living")
    assert lv["moved"] == 2000 and lv["plan"] == living + 2000
    assert _reserved(conn, "saving") == 8000                                             # 錢包要留的同步
    assert plans.get(conn, "2037-11")["buckets"]["saving"] == 10000                      # 只影響挪用那一期


def test_rollover_carry_counts_the_move_once(conn):
    assert plans.rollover_carry(conn, "extra2", "2037-10") == 1500
    _move(conn, "extra2", "extra1", 500, period="2037-09")                               # 9 月挪走 500
    assert plans.get(conn, "2037-09")["buckets"]["extra2"] == 1000
    assert plans.get(conn, "2037-10")["buckets"]["extra2"] == 1500                       # 10 月本身沒挪
    assert plans.rollover_carry(conn, "extra2", "2037-10") == 1000                       # 快取作廢、只少一次
    assert plans.rollover_carry(conn, "extra2", "2037-11") == 1000 + 1500                # 不會每一期再扣一次
    _move(conn, "saving", "extra2", 300)                                                 # 10 月挪進來 300
    e = _row(conn, "extra2")
    assert (e["plan"], e["carry"], e["available"]) == (1800, 1000, 2800)
    assert plans.rollover_carry(conn, "extra2", "2037-11") == 1000 + 1800


def test_undo_and_batch_revert(conn):
    r = _move(conn, "extra1", "fixed", 700)
    assert plans.get(conn)["buckets"]["fixed"] == 3700
    with db.write(conn) as w:
        u = moves.undo(w, r["id"])
    assert u["undone"] and plans.get(conn)["buckets"]["fixed"] == 3000 and plans.get(conn)["moves"] == {}
    with pytest.raises(NotFound):
        with db.write(conn) as w:
            moves.undo(w, r["id"])
    with db.write(conn) as w:
        moves.move(w, "extra1", "fixed", 700)
        batch = w.batch_id
    assert plans.get(conn)["buckets"]["extra1"] == 1300
    with db.write(conn) as w:
        batches.revert(w, batch)                                                         # 整批復原也可以
    assert plans.get(conn)["buckets"]["extra1"] == 2000


def test_stats(conn):
    _move(conn, "saving", "living", 1000, "聚餐")
    _move(conn, "saving", "living", 500)
    _move(conn, "extra1", "fixed", 300)
    st = moves.stats(conn, "2037-10")
    assert (st["count"], st["total"]) == (3, 1800)
    per = {b["id"]: b for b in st["buckets"]}
    assert per["saving"] == {"id": "saving", "name": "存錢", "in": 0, "out": 1500, "net": -1500}
    assert per["living"]["net"] == 1500 and per["fixed"]["in"] == 300 and per["extra1"]["out"] == 300
    assert st["buckets"][0]["id"] == "saving"                                            # 挪出最多的排前面
    assert st["biggest"]["amount"] == 1000 and st["biggest"]["note"] == "聚餐"
    assert (st["top_pair"]["from"], st["top_pair"]["to"], st["top_pair"]["count"], st["top_pair"]["amount"]) == ("saving", "living", 2, 1500)
    assert st["most_taken"]["id"] == "saving"
    assert moves.stats(conn, "2037-11")["count"] == 0 and moves.stats(conn, "2037-11")["biggest"] is None
    assert homepage.home(conn, "2037-10")["moves"]["stats"]["total"] == 1800


def test_validation_and_overdraw_warning(conn):
    for a, b, n in (("saving", "saving", 100), ("saving", "living", 0), ("saving", "living", -5)):
        with pytest.raises(InvalidInput):
            _move(conn, a, b, n)
    with pytest.raises(NotFound):
        _move(conn, "沒有這格", "living", 100)
    with db.write(conn) as w:
        plans.add_bucket(w, "旅行")                                                      # extra3
        plans.set_bucket(w, "extra3", active=False)
        plans.skip(w, "extra1")
    with pytest.raises(InvalidInput, match="停用"):
        _move(conn, "旅行", "living", 100)
    with pytest.raises(InvalidInput, match="跳過"):
        _move(conn, "saving", "extra1", 100)                                             # 挪進跳過的格子沒有用
    assert _move(conn, "extra1", "living", 2000)["warnings"] == []                       # 跳過的格子可以挪出去（這期不存，拿去補）
    ingest.run(conn, {"records": [{"type": "expense", "amount": 2500, "date": "2037-10-09", "account": "生活帳戶", "category": "勞保"}]},
               actor="test", source="test")
    r = _move(conn, "fixed", "living", 1000)                                             # 固定支出已經超過：照樣記，給一句
    assert r["id"] and r["warnings"] and "超過" in r["warnings"][0]
    assert plans.get(conn)["buckets"]["fixed"] == 2000


def test_move_by_name_summary_home_and_undo(conn):
    """用名字挪（「生活費」）；清單、總覽、規劃頁各自看得到該看的；拿掉之後清單是空的。"""
    with db.write(conn) as w:
        r = moves.move(w, "saving", "生活費", 1200, "超支")
        batch = w.batch_id if w.changes else None
    assert batch and r["to"] == "living" and r["warnings"] == []
    d = moves.summary(conn)
    assert d["period"] == "2037-10" and d["stats"]["total"] == 1200 and d["items"][0]["note"] == "超支"
    h = homepage.home(conn)
    assert next(e for e in h["envelopes"] if e["id"] == "saving")["moved"] == -1200 and h["moves"]["items"]
    assert plans.get(conn, "2037-10", with_moves=False)["buckets"]["saving"] == 10000      # 規劃頁：原本的計畫
    with pytest.raises(InvalidInput):
        _move(conn, "saving", "saving", 1)
    with db.write(conn) as w:
        moves.undo(w, r["id"])
    assert moves.summary(conn)["items"] == []
    with pytest.raises(NotFound):
        with db.write(conn) as w:
            moves.undo(w, 999)


def test_dry_run_writes_nothing_and_overdraw_still_records(conn):
    with db.write(conn, dry_run=True) as w:
        moves.move(w, "存錢", "living", 800)
    assert moves.list_moves(conn, "2037-10") == []                                       # 試算不寫
    with db.write(conn, actor="test:1") as w:
        r = moves.move(w, "存錢", "living", 800, "生活費超支")
    assert (r["from_name"], r["to_name"]) == ("存錢", "生活費")
    with db.write(conn) as w:
        over = moves.move(w, "旅遊基金", "fixed", 5000)
    assert over["warnings"]                                                              # 挪超過剩下的：照樣記、有提醒
    st = moves.summary(conn)["stats"]
    assert st["count"] == 2 and st["most_taken"]["id"] == "extra1"
    p = plans.get(conn, "2037-10", with_moves=False)
    assert p["buckets"]["saving"] == 10000 and p["moves"]["saving"] == -800
    with db.write(conn) as w:
        assert moves.undo(w, r["id"])["undone"]
