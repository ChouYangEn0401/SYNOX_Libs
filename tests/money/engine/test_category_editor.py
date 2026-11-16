"""設定頁「記帳的類別」：所有類別（不只生活費）都能加、改名、換圖示、搬大類、指定算在哪一格。

有系統代號的類別（房租、午餐、借出…）可以改名，但不能停用、不能換種類；程式一律照代號找它們。
這裡直接呼叫服務層（設定頁與其他入口共用同一套檢查）。
"""
import pytest

from money_engine import db
from money_engine.errors import LedgerError, Conflict, InvalidInput
from money_engine.services import ingest, plans, refdata, stats


def _cat(conn, name):
    for b in refdata.catalog(conn):
        for g in b["groups"]:
            for c in g["categories"]:
                if c["name"] == name:
                    return c, g, b
    raise AssertionError(name)


def _edit(conn, category_id, **data):
    """跟設定頁改一個細項一樣：bucket 另外處理（空字串＝跟著大類），其餘交給 update_category。"""
    has_bucket = "bucket" in data
    bucket = data.pop("bucket", None)
    with db.write(conn) as w:
        c = refdata.update_category(w, category_id, **data)
        if has_bucket:
            c = plans.set_category_bucket(w, category_id, bucket)
    return c


def _add(conn, *, name, group, bucket=None, icon=None):
    with db.write(conn) as w:
        c = refdata.add_category(w, name=name, group=group, icon=icon)
        if bucket:
            c = plans.set_category_bucket(w, c["id"], bucket)
    return c


def _add_bucket(conn, label):
    with db.write(conn) as w:
        return plans.add_bucket(w, label)


def test_catalog_lists_every_expense_group_not_just_living(conn):
    cat = refdata.catalog(conn)
    buckets = {b["bucket"]: b for b in cat}
    assert {"giving", "fixed", "living"} <= set(buckets)                           # 捐贈、固定支出、生活費都在
    fixed = {g["name"]: [c["name"] for c in g["categories"]] for g in buckets["fixed"]["groups"]}
    assert "訂閱" in fixed["通訊訂閱"] and "房租" in fixed["住"]
    names = [c["name"] for b in cat for g in b["groups"] for c in g["categories"]]
    assert "薪資" not in names and "借出" not in names                                  # 收入與轉帳是系統用的，不在這裡


def test_system_categories_can_be_renamed_but_not_stopped(conn):
    rent, _, _ = _cat(conn, "房租")
    assert rent["system"] is True
    renamed = _edit(conn, rent["id"], name="租金")                                     # 有代號的：可以改名
    assert renamed["name"] == "租金" and refdata.system_category(conn, "bill.rent")["id"] == rent["id"]
    with pytest.raises(InvalidInput):
        _edit(conn, rent["id"], active=0)
    ok = _edit(conn, rent["id"], icon="🏠", keywords=["房東", "租金"])                  # 圖示、關鍵字照樣能改
    assert ok["icon"] == "🏠"
    sub, _, _ = _cat(conn, "訂閱")
    assert sub["system"] is False
    assert _edit(conn, sub["id"], name="線上訂閱")["name"] == "線上訂閱"
    lunch, _, _ = _cat(conn, "午餐")
    assert lunch["system"] is True                                                    # 三餐判斷靠它：不能停用


def test_add_a_category_bound_to_a_bucket_in_one_step(conn):
    """手機費那格：在「通訊訂閱」加一個「手機月租」項目、直接算進手機費這一格，記帳就自己對上。"""
    b = _add_bucket(conn, "手機費")
    c = _add(conn, name="手機月租", group="通訊訂閱", bucket=b["id"], icon="📱")
    assert (c["bucket_id"], c["icon"]) == (b["id"], "📱")
    assert "手機月租" in plans.bucket_categories(conn)[b["id"]]
    cat, _, _ = _cat(conn, "手機月租")
    assert cat["pinned"] == b["id"] and cat["bucket_name"] == "手機費"
    ingest.run(conn, {"records": [{"type": "expense", "amount": 800, "desc": "手機月租費", "category": "手機月租",
                                   "date": "2037-10-08"}]})
    assert stats.period_summary(conn, "2037-10")["allocation"]["actual"][b["id"]] == 800


def test_a_category_can_be_pointed_at_another_bucket_and_back(conn):
    b = _add_bucket(conn, "雲端服務")
    sub, _, _ = _cat(conn, "訂閱")
    _edit(conn, sub["id"], bucket=b["id"])
    assert _cat(conn, "訂閱")[0]["bucket"] == b["id"]
    _edit(conn, sub["id"], bucket="")                                                  # 空字串＝跟著大類
    c, _, _ = _cat(conn, "訂閱")
    assert (c["bucket"], c["pinned"]) == ("fixed", None)
    _edit(conn, sub["id"], bucket="fixed")                                             # 選到大類本來的用途＝不用另外指定
    assert _cat(conn, "訂閱")[0]["pinned"] is None


def test_binding_rules_match_the_link_modal(conn):
    sub, _, _ = _cat(conn, "訂閱")
    with pytest.raises(InvalidInput):
        _edit(conn, sub["id"], bucket="living")                                        # 生活費不能直接認領
    lunch, _, _ = _cat(conn, "午餐")
    with pytest.raises(InvalidInput):
        _edit(conn, lunch["id"], bucket="fixed")                                       # 生活費底下的不能搬去別的用途
    with pytest.raises(LedgerError) as e:
        _edit(conn, sub["id"], bucket="不存在的用途")
    assert e.value.code in ("invalid_input", "not_found")


def test_adding_a_stopped_category_name_reopens_it(conn):
    sub, _, _ = _cat(conn, "訂閱")
    _edit(conn, sub["id"], active=0)
    _add(conn, name="訂閱", group="通訊訂閱")
    assert _cat(conn, "訂閱")[0]["id"] == sub["id"]                                    # 同一個，歷史紀錄還在它名下
    with pytest.raises(Conflict):
        _add(conn, name="訂閱", group="通訊訂閱")                                      # 沒停用就是重複


def test_add_group_with_icon_and_bucket(conn):
    with db.write(conn) as w:
        g = refdata.add_group(w, name="結婚", bucket="fixed", icon="💍")
    assert (g["bucket_id"], g["icon"]) == ("fixed", "💍")
    assert any(grp["name"] == "結婚" for b in refdata.catalog(conn) for grp in b["groups"])


def test_category_bucket_add_clear_and_rename(conn):
    b = _add_bucket(conn, "結婚預備")
    c = _add(conn, name="喜餅", group="通訊訂閱", bucket="結婚預備")
    assert c["bucket_name"] == "結婚預備" and c["bucket_id"] == b["id"]
    c = _edit(conn, c["id"], bucket="")
    assert c["bucket_id"] == "fixed"
    rent = refdata.system_category(conn, "bill.rent")
    assert _edit(conn, rent["id"], name="租金")["name"] == "租金"                      # 有代號的也能改名


def test_service_level_guard_covers_every_entry_alike(conn):
    lend = refdata.system_category(conn, "lend")
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            refdata.update_category(w, lend["id"], active=0)
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            refdata.update_category(w, lend["id"], kind="expense")
    with db.write(conn) as w:
        refdata.update_category(w, lend["id"], name="代付")
    assert refdata.system_name(conn, "lend") == "代付"


def test_catalog_skips_groups_that_only_hold_transfers_but_keeps_new_empty_groups(conn):
    def names():
        return [g["name"] for b in refdata.catalog(conn) for g in b["groups"]]
    assert "投資" not in names() and "轉帳" not in names()                              # 只有搬錢的類別：不是記帳選項
    with db.write(conn) as w:
        refdata.add_group(w, name="結婚", bucket="fixed")
    assert "結婚" in names()                                                            # 剛加、還沒有項目：要列出來才能往裡面加
