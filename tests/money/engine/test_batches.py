"""整批撤銷：新增、修改、刪除都還原得回來；撤銷可以再撤銷；之後被改過就衝突。"""
import pytest

from money_engine import db
from money_engine.errors import Conflict, NotFound
from money_engine.services import batches, refdata, settings, transactions as tx


def _cash(conn):
    return refdata.resolve_account(conn, "現金")["id"]


def test_revert_creation_deletes_and_revert_again_restores(conn):
    with db.write(conn, command="ingest") as w:
        a = tx.create(w, kind="expense", amount=100, date="2037-10-09", account_id=_cash(conn), description="便當",
                      items=[{"name": "便當", "amount": 100}])
        tx.create(w, kind="expense", amount=60, date="2037-10-09", account_id=_cash(conn), description="飲料")
        first = w.batch_id
    with db.write(conn, command="batch revert") as w:
        result = batches.revert(w, first)
        undo = w.batch_id
    assert result["changes"] == 2
    assert tx.query(conn)["total"] == 0
    assert batches.get(conn, first)["reverted_by"] == undo
    with db.write(conn) as w:
        batches.revert(w, undo)
    back = tx.get(conn, a["id"])
    assert back["status"] == "active" and back["items"] == [{"name": "便當", "qty": None, "amount": 100}]


def test_revert_update_and_delete(conn):
    with db.write(conn) as w:
        t = tx.create(w, kind="expense", amount=280, date="2037-09-10", account_id=_cash(conn))
    with db.write(conn) as w:
        tx.update(w, t["id"], amount=250, items=[{"name": "小籠包"}])
        edit = w.batch_id
    with db.write(conn) as w:
        batches.revert(w, edit)
    got = tx.get(conn, t["id"])
    assert got["amount"] == 280 and got["items"] == []
    with db.write(conn) as w:
        tx.delete(w, t["id"])
        gone = w.batch_id
    with db.write(conn) as w:
        batches.revert(w, gone)
    assert tx.get(conn, t["id"])["status"] == "active"


def test_revert_settings(conn):
    with db.write(conn) as w:
        settings.put(w, "period_start_day", 1)
        b = w.batch_id
    with db.write(conn) as w:
        batches.revert(w, b)
    assert settings.get(conn, "period_start_day") == 5


def test_conflicts(conn):
    with db.write(conn) as w:
        t = tx.create(w, kind="expense", amount=100, date="2037-10-09", account_id=_cash(conn))
        first = w.batch_id
    with db.write(conn) as w:
        tx.update(w, t["id"], amount=120)
    with pytest.raises(Conflict) as e:
        with db.write(conn) as w:
            batches.revert(w, first)
    assert "之後又被改過" in e.value.message
    assert tx.get(conn, t["id"])["amount"] == 120          # 失敗的撤銷什麼都沒動
    with pytest.raises(NotFound):
        with db.write(conn) as w:
            batches.revert(w, "b_nope")


def test_cannot_revert_twice(conn):
    with db.write(conn) as w:
        tx.create(w, kind="expense", amount=100, date="2037-10-09", account_id=_cash(conn))
        first = w.batch_id
    with db.write(conn) as w:
        batches.revert(w, first)
    with pytest.raises(Conflict):
        with db.write(conn) as w:
            batches.revert(w, first)


def test_recent_lists_batches_with_change_counts(conn):
    with db.write(conn, actor="chat:9", command="ingest") as w:
        tx.create(w, kind="expense", amount=100, date="2037-10-09", account_id=_cash(conn))
    listed = batches.recent(conn)
    assert listed[0]["actor"] == "chat:9" and listed[0]["changes"] == 1
