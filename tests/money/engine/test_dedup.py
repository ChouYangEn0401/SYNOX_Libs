"""去重：打分案例、寫入當下建組、同一批不比、處理三種方式、處理過不再問、改過就衝突、撤銷。"""
import pytest

from money_engine import db
from money_engine.errors import Conflict, InvalidInput
from money_engine.services import batches, dedup, refdata, transactions as tx


def _row(**kw):
    base = {"amount": 260, "account_id": 1, "description": "", "merchant": "", "date": "2037-10-09", "time": None}
    base.update(kw)
    return base


@pytest.mark.parametrize("a,b,flagged", [
    (_row(description="麻油雞", time="20:13"), _row(description="麻油雞晚餐", time="20:15"), True),
    (_row(description="捷運加值", amount=500), _row(description="交通卡加值 500", amount=500), True),   # 同帳戶同金額
    (_row(description="雞腿便當", time="12:00"), _row(description="雞腿便當", time="19:00"), False),   # 中午、晚上各一個
    (_row(description="美式咖啡"), _row(description="奶茶", account_id=2), False),                    # 不同帳戶、內容不像
    (_row(description="美式咖啡"), _row(description="美式咖啡", account_id=2), True),
])
def test_scores(a, b, flagged):
    s, reasons = dedup.score(a, b)
    assert (s >= dedup.THRESHOLD) is flagged, (s, reasons)


def _add(conn, desc, amount=260, account="現金", day="2037-10-09", time=None):
    acc = refdata.resolve_account(conn, account)["id"]
    with db.write(conn) as w:
        t = tx.create(w, kind="expense", amount=amount, date=day, time=time, account_id=acc, description=desc)
        pair = dedup.check(w, t["id"])
    return t, pair


def test_check_creates_pair_once_and_skips_same_batch(conn):
    a, p0 = _add(conn, "麻油雞", time="20:13")
    b, p1 = _add(conn, "麻油雞晚餐", time="20:15")
    assert p0 is None and p1 and p1["duplicate_of"]["id"] == a["id"]
    assert [p["a"]["id"] for p in dedup.pending(conn)] == [a["id"]]
    with db.write(conn) as w:
        assert dedup.scan(w) == []                    # 同一對不再建
    acc = refdata.resolve_account(conn, "現金")["id"]
    with db.write(conn) as w:                         # 同一批的兩筆不互相比
        tx.create(w, kind="expense", amount=99, date="2037-10-08", account_id=acc, description="飯糰")
        y = tx.create(w, kind="expense", amount=99, date="2037-10-08", account_id=acc, description="飯糰")
        assert dedup.check(w, y["id"]) is None


def test_keep_both_is_never_asked_again(conn):
    _add(conn, "麻油雞", time="20:13")
    _, pair = _add(conn, "麻油雞晚餐", time="20:15")
    with db.write(conn) as w:
        r = dedup.resolve(w, pair["pair_id"], "keep_both")
    assert r["status"] == "kept_both" and dedup.pending(conn) == []
    with db.write(conn) as w:
        assert dedup.scan(w) == []


def test_delete_newer_and_revert(conn):
    _add(conn, "麻油雞", time="20:13")
    b, pair = _add(conn, "麻油雞晚餐", time="20:15")
    with db.write(conn) as w:
        dedup.resolve(w, pair["pair_id"], "delete_newer")
        bid = w.batch_id
    assert tx.get(conn, b["id"], include_deleted=True)["status"] == "deleted"
    with db.write(conn) as w:
        batches.revert(w, bid)
    assert tx.get(conn, b["id"])["status"] == "active" and len(dedup.pending(conn)) == 1


def test_merge_keeps_older_and_fills_details(conn):
    a, _ = _add(conn, "麻油雞")
    acc = refdata.resolve_account(conn, "現金")["id"]
    with db.write(conn) as w:
        b = tx.create(w, kind="expense", amount=260, date="2037-10-09", time="20:13", account_id=acc,
                      description="範例麻油雞店", merchant="範例麻油雞店", items=[{"name": "麻油雞", "amount": 200}])
        pair = dedup.check(w, b["id"])
    with db.write(conn) as w:
        r = dedup.resolve(w, pair["pair_id"], "merge")
    kept = r["kept"]
    assert kept["id"] == a["id"] and kept["time"] == "20:13" and kept["merchant"] == "範例麻油雞店"
    assert kept["items"][0]["name"] == "麻油雞"
    assert tx.get(conn, b["id"], include_deleted=True)["status"] == "deleted"


def test_resolve_guards(conn):
    _add(conn, "麻油雞")
    b, pair = _add(conn, "麻油雞晚餐")
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            dedup.resolve(w, pair["pair_id"], "both")
    with pytest.raises(Conflict):
        with db.write(conn) as w:
            dedup.resolve(w, pair["pair_id"], "keep_both", expect_b="2000-01-01T00:00:00+08:00")
    with db.write(conn) as w:
        tx.delete(w, b["id"])
    assert dedup.pending(conn) == []                # 其中一筆刪掉了就不再出現
    with pytest.raises(Conflict):
        with db.write(conn) as w:
            dedup.resolve(w, pair["pair_id"], "keep_both")
