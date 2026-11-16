"""ingest：驗證與中文錯誤、全有或全無、猜類別、退款、薪水扣繳、轉帳用途、日記、dry-run。"""
import pytest

from money_engine import schemas
from money_engine.errors import Conflict, InvalidInput, NotFound
from money_engine.services import ingest, notes, transactions as tx

CARD = "範例 Demo"                  # 起始檔裡的信用卡（別名 demo）


def test_receipt_with_items_and_note(conn):
    out = ingest.run(conn, {"source": "chat", "source_ref": "chat:1:100", "records": [
        {"type": "expense", "amount": 260, "date": "2037-10-09", "time": "20:13", "desc": "麻油雞", "account": "demo",
         "items": [{"name": "麻油雞", "amount": 200}, {"name": "燙青菜", "amount": 60}], "confidence": 0.9},
        {"type": "day_note", "date": "2037-10-09", "note": "下班跟同事吃麻油雞"}]}, actor="chat:1")
    r0, r1 = out["results"]
    assert r0["status"] == "created" and r0["category"] == "晚餐" and r0["category_guessed"] is True
    assert r0["account"] == CARD and out["batch_id"].startswith("b_")
    assert tx.get(conn, r0["id"])["source"] == "chat" and tx.get(conn, r0["id"])["source_ref"] == "chat:1:100"
    assert r1["type"] == "day_note" and notes.get(conn, "2037-10-09") == "下班跟同事吃麻油雞"


def test_single_record_object_and_default_account(conn):
    out = ingest.run(conn, '{"type":"expense","amount":"1,280","date":"2037-10-08","desc":"衛生紙"}')
    r = out["results"][0]
    assert r["amount"] == 1280 and r["account"] == "現金" and r["category"] == "日用品"


def test_unknown_category_falls_back_with_warning(conn):
    out = ingest.run(conn, {"records": [{"type": "expense", "amount": 87, "date": "2037-10-07", "desc": "ABC-123",
                                         "category": "超商"}]})
    assert out["results"][0]["category"] is None
    assert any("沒有「超商」" in w for w in out["warnings"]) and any("待分類" in w for w in out["warnings"])


def test_validation_errors_are_chinese_and_point_at_fields(conn):
    with pytest.raises(InvalidInput) as e:
        ingest.run(conn, {"records": [
            {"type": "expense", "amount": 12.5, "date": "2037-10-09"},
            {"type": "expense", "amount": 10, "date": "2037-10-09", "categry": "午餐"},
            {"type": "expens", "amount": 1, "date": "2037-10-09"}]})
    f = e.value.fields
    assert f["records[0].amount"].startswith("要是整數")
    assert f["records[1].categry"] == "不認得這個欄位"
    assert "type 不對" in f["records[2]"]
    with pytest.raises(InvalidInput) as e:
        ingest.run(conn, "{not json")
    assert "JSON 壞掉" in e.value.message


def test_all_or_nothing_with_index_in_error(conn):
    with pytest.raises(NotFound) as e:
        ingest.run(conn, {"records": [{"type": "expense", "amount": 100, "date": "2037-10-09", "desc": "便當"},
                                      {"type": "expense", "amount": 50, "date": "2037-10-09", "account": "Demo卡"}]})
    assert e.value.message.startswith("第 2 筆") and "records[1]" in next(iter(e.value.fields))
    assert CARD in e.value.suggestions
    assert tx.query(conn)["total"] == 0


def test_refund_and_dry_run(conn):
    out = ingest.run(conn, {"records": [{"type": "expense", "amount": 300, "date": "2037-10-09", "desc": "網購退貨",
                                         "category": "購物", "refund": True}]}, dry_run=True)
    assert out["dry_run"] and out["batch_id"] is None and out["results"][0]["status"] == "would_create"
    assert out["results"][0]["amount"] == -300 and tx.query(conn)["total"] == 0


def test_salary_with_deductions_is_one_group(conn):
    out = ingest.run(conn, {"records": [{"type": "income", "amount": 40000, "date": "2037-10-05", "desc": "十月薪水",
                                         "account": "生活帳戶",
                                         "deductions": {"勞保": 1000, "健保": 600, "所得稅": 800, "勞退自提": 800}}]})
    r = out["results"][0]
    assert r["category"] == "薪資" and r["net"] == 40000 - 3200 and len(r["deductions"]) == 4
    group = tx.get(conn, r["id"])["group_id"]
    assert group and all(tx.get(conn, d["id"])["group_id"] == group for d in r["deductions"])
    pension = next(d for d in r["deductions"] if d["category"] == "勞退自提")
    assert tx.get(conn, pension["id"])["to_account"] == "勞退個人專戶"
    assert any("勞退個人專戶" in w for w in out["warnings"])
    with pytest.raises(NotFound):
        ingest.run(conn, {"records": [{"type": "income", "amount": 100, "date": "2037-10-05", "category": "薪資",
                                       "deductions": {"伙食費": 10}}]})


def test_transfer_purpose_inferred_from_accounts(conn):
    out = ingest.run(conn, {"records": [
        {"type": "transfer", "amount": 5000, "date": "2037-10-10", "from": "生活帳戶", "to": "demo"}]})
    r = out["results"][0]
    assert r["purpose"] == "card_payment" and r["category"] == "繳卡費" and r["to_account"] == CARD


def test_resend_is_replayed_changed_content_conflicts_or_replaces(conn):
    body = {"source_ref": "chat:1:5", "records": [{"type": "expense", "amount": 10, "date": "2037-10-09", "desc": "糖果"}]}
    first = ingest.run(conn, body)["results"][0]
    again = ingest.run(conn, body)
    assert again["results"][0]["status"] == "replayed" and again["results"][0]["id"] == first["id"]
    assert again["batch_id"] is None and tx.query(conn)["total"] == 1
    body["records"][0]["amount"] = 12
    with pytest.raises(Conflict) as e:
        ingest.run(conn, body)
    assert e.value.extra["existing"]["amount"] == 10
    out = ingest.run(conn, body, replace=True)
    assert out["results"][0]["status"] == "replaced" and tx.get(conn, first["id"])["amount"] == 12
    assert ingest.run(conn, body)["results"][0]["status"] == "replayed"


def test_income_with_same_ref_but_new_content_cannot_be_replaced(conn):
    body = {"source_ref": "chat:1:6", "records": [{"type": "income", "amount": 100, "date": "2037-10-05", "category": "兼職"}]}
    ingest.run(conn, body)
    body["records"][0]["amount"] = 200
    with pytest.raises(Conflict):
        ingest.run(conn, body, replace=True)


def test_attachment_saved_and_same_receipt_is_replay(conn, home, tmp_path):
    """收據檔只能從這本帳的檔案資料夾來（相對路徑以它為準）；同一張圖再送一次＝重送。"""
    img = home / "receipt.jpg"
    img.write_bytes(b"fake-jpeg-bytes")
    rec = {"type": "expense", "amount": 260, "date": "2037-10-09", "desc": "麻油雞", "attach": str(img)}
    r1 = ingest.run(conn, {"source_ref": "chat:1:7", "records": [rec]})["results"][0]
    from money_engine.services import attachments
    files = attachments.for_txn(conn, r1["id"])
    assert len(files) == 1 and (attachments.folder() / files[0]["path"]).read_bytes() == b"fake-jpeg-bytes"
    r2 = ingest.run(conn, {"source_ref": "chat:1:8", "records": [dict(rec, attach="receipt.jpg")]})["results"][0]
    assert r2["status"] == "replayed" and r2["id"] == r1["id"]             # 不同訊息、同一張圖（相對路徑）
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"other-bytes")
    for bad in (str(outside), "../outside.jpg", "missing.jpg"):
        with pytest.raises(InvalidInput):
            ingest.run(conn, {"records": [dict(rec, attach=bad)]})


def test_possible_duplicate_is_flagged_but_counted(conn):
    ingest.run(conn, {"source_ref": "web:1", "records": [
        {"type": "expense", "amount": 260, "date": "2037-10-09", "time": "20:13", "desc": "麻油雞", "account": "demo"}]})
    out = ingest.run(conn, {"source_ref": "chat:1:9", "records": [
        {"type": "expense", "amount": 260, "date": "2037-10-09", "time": "20:15", "desc": "麻油雞晚餐", "account": "demo"}]},
        actor="chat:1")
    r = out["results"][0]
    assert r["status"] == "possible_duplicate" and r["duplicate_of"]["desc"] == "麻油雞" and r["pair_id"]
    assert any("重複" in w for w in out["warnings"])
    assert tx.query(conn)["total"] == 2


def test_schema_matches_validator():
    s = schemas.json_schema()
    assert s["properties"]["records"]["maxItems"] == 200
    e = schemas.json_schema("transfer")
    assert "from" in e["properties"] and "from_" not in e["properties"]
    with pytest.raises(InvalidInput):
        schemas.json_schema("nope")
