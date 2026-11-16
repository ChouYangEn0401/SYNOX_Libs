"""小日記：接在後面、相同的不重複、replace、撤銷。"""
from money_engine import db
from money_engine.services import batches, notes


def test_append_dedupe_replace_and_revert(conn):
    with db.write(conn) as w:
        notes.put(w, "2037-10-09", "下班跟同事吃麻油雞")
    with db.write(conn) as w:
        r = notes.put(w, "2037-10-09", "下班跟同事吃麻油雞")
    assert r["changed"] is False
    with db.write(conn) as w:
        notes.put(w, "2037-10-09", "有點吃太多")
        second = w.batch_id
    assert notes.get(conn, "2037-10-09") == "下班跟同事吃麻油雞；有點吃太多"
    with db.write(conn) as w:
        batches.revert(w, second)
    assert notes.get(conn, "2037-10-09") == "下班跟同事吃麻油雞"
    with db.write(conn) as w:
        notes.put(w, "2037-10-09", "重寫", replace=True)
    assert notes.between(conn, "2037-10-05", "2037-10-31") == {"2037-10-09": "重寫"}
