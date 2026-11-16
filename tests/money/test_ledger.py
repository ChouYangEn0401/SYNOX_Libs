"""記帳主線（W3）：記一筆到撤銷的整條線——打字的路（指令）與畫面動作的路結果一樣、兩個人互不可見。

用 conftest 的 make_money（假的 pack、假的今天 2030-01-15）；資料全是假的。
"""

import pytest

from synox.packs import kit

from .conftest import OTHER, OWNER, TODAY, U

DROP = ("id", "batch_id", "created_at", "updated_at", "source", "source_ref", "payload_hash", "items", "title", "detail")


def _query(bot, user, **filters):
    """這個人目前的帳（新的在前；直接問引擎）。"""
    with bot.module.session.open_book(bot.pack, U(user)) as book:
        return bot.module.ledger.tx.query(book.conn, limit=200, **filters)["items"]


def same_shape(t):
    return {k: v for k, v in t.items() if k not in DROP}


def closing(bot, user):
    return bot.read("passbook", user)["closing"]


# ── 記一筆 ───────────────────────────────────────────────────────────────────

def test_typed_add_records_expense_and_replies_with_amount(make_money):
    bot = make_money()
    reply = bot.command("add", OWNER, "120 午餐 範例便當店")
    assert "120" in reply and "記好了" in reply, reply
    rows = _query(bot, OWNER)
    assert len(rows) == 1
    t = rows[0]
    assert t["kind"] == "expense" and t["amount"] == 120 and t["date"] == TODAY.date().isoformat()
    assert t["desc"] == "範例便當店" and t["account"] == "現金"


def test_word_that_is_not_a_category_becomes_the_description(make_money):
    bot = make_money()
    bot.command("add", OWNER, "85 範例便當店")
    t = _query(bot, OWNER)[0]
    assert t["desc"] == "範例便當店" and t["amount"] == 85


@pytest.mark.parametrize("name,typed,values", [
    ("add", "120 午餐 範例便當店 date=昨天 time=12:10",
     {"amount": "120", "category": "午餐", "desc": "範例便當店", "date": "2030-01-14", "time": "12:10", "kind": "expense"}),
    ("in", "3000 薪資 範例公司", {"amount": "3000", "category": "薪資", "desc": "範例公司", "kind": "income"}),
    ("transfer", "500 to=銀行帳戶 account=現金", {"amount": "500", "to": "銀行帳戶", "account": "現金", "kind": "transfer"}),
    ("add", "60 refund=yes desc=退貨", {"amount": "60", "desc": "退貨", "refund": "yes", "kind": "expense"}),
])
def test_typed_and_app_paths_store_the_same(make_money, name, typed, values):
    bot = make_money()
    bot.command(name, OWNER, typed)
    data = bot.act(name, OTHER, values)
    assert data["batch_id"] and data["write_seq"] >= 1 and data["results"]
    mine, theirs = _query(bot, OWNER), _query(bot, OTHER)
    assert len(mine) == len(theirs) == 1
    assert same_shape(mine[0]) == same_shape(theirs[0])


def test_dates_people_type(make_money):
    bot = make_money()
    for text in ("今天", "昨天", "前天", "1/3", "01-04", "2030-01-05", "2030/1/6"):
        bot.command("add", OWNER, f"10 範例 date={text}")
    days = sorted(t["date"] for t in _query(bot, OWNER))
    assert days == ["2030-01-03", "2030-01-04", "2030-01-05", "2030-01-06", "2030-01-13", "2030-01-14", "2030-01-15"]
    reply = bot.command("add", OWNER, "10 範例 date=下禮拜")
    assert reply.startswith("❌") and "看不懂" in reply


def test_amount_must_be_a_positive_number(make_money):
    bot = make_money()
    for bad in ("abc", "0", "12.5"):
        reply = bot.command("add", OWNER, f"{bad} 範例")
        assert reply.startswith("❌"), (bad, reply)
    with pytest.raises(kit.AppError):
        bot.act("add", OWNER, {"amount": "-5"})
    assert _query(bot, OWNER) == []
    bot.command("add", OWNER, "1,200 範例")
    assert _query(bot, OWNER)[0]["amount"] == 1200


def test_decimals_follow_the_book_setting(make_money):
    bot = make_money()
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        book.conn.execute("UPDATE settings SET value = '2' WHERE key = 'currency.decimals'")
        assert bot.module.table.to_minor(book, "99.5") == 9950
        assert bot.module.table.show(book.conn, 9950) == "99.50"
        with pytest.raises(Exception):
            bot.module.table.to_minor(book, "1.234")


def test_refund_is_a_negative_expense(make_money):
    bot = make_money()
    bot.command("add", OWNER, "300 範例耳機")
    reply = bot.command("add", OWNER, "300 refund=yes desc=範例耳機退貨")
    assert "退款" in reply
    amounts = sorted(t["amount"] for t in _query(bot, OWNER))
    assert amounts == [-300, 300]
    assert closing(bot, OWNER) == 0


def test_transfer_is_not_spending(make_money):
    bot = make_money()
    before = closing(bot, OWNER)
    bot.command("transfer", OWNER, "500 to=銀行帳戶")
    t = _query(bot, OWNER)[0]
    assert t["kind"] == "transfer" and t["account"] == "現金" and t["to_account"] == "銀行帳戶"
    assert closing(bot, OWNER) == before                     # 自己帳戶之間搬錢，錢包不變
    reply = bot.command("transfer", OWNER, "500")
    assert reply.startswith("❌") and "轉進哪個帳戶" in reply


def test_income_counts_up(make_money):
    bot = make_money()
    bot.command("in", OWNER, "3000 薪資")
    assert _query(bot, OWNER)[0]["kind"] == "income"
    assert closing(bot, OWNER) == 3000


def test_iou_lend_then_collect(make_money):
    bot = make_money()
    bot.act("iou", OWNER, {"amount": "200", "kind": "iou", "direction": "lend", "counterparty": "小明", "account": "現金"})
    bot.command("iou", OWNER, "200 direction=收回 counterparty=小明")
    rows = _query(bot, OWNER)
    assert {t["purpose"] for t in rows} == {"lend", "collect"}
    assert closing(bot, OWNER) == 0
    reply = bot.command("iou", OWNER, "50 counterparty=小明")
    assert reply.startswith("❌") and "方向" in reply


def test_add_reply_carries_the_allowance_line(make_money):
    """記完一筆的回覆帶額度提醒（引擎的 allowance.note_for 那一句）：畫面在 warnings，指令接在回覆後面（⚠️）。"""
    bot = make_money()
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        from importlib import import_module
        plans = import_module(bot.module.__name__ + ".engine.services.plans")
        db = import_module(bot.module.__name__ + ".engine.db")
        with db.write(book.conn, actor=book.actor, source="test", command="plan") as w:
            plans.set_plan(w, buckets={"saving": "500"})
    data = bot.act("transfer", OWNER, {"amount": "300", "to": "銀行帳戶", "purpose": "saving"})
    assert data["warnings"] == [], data["warnings"]
    reply = bot.command("transfer", OWNER, "200 to=銀行帳戶 purpose=saving")
    assert "⚠️" in reply and "做到了" in reply, reply


# ── 改、切成代墊再切回、長期用、算哪一期 ──────────────────────────────────────────────────

def test_edit_typed_and_app(make_money):
    bot = make_money()
    bot.command("add", OWNER, "120 午餐 範例便當店")
    tid = _query(bot, OWNER)[0]["id"]
    reply = bot.command("edit", OWNER, f"{tid} amount=150 note=加蛋")
    assert "改好了" in reply and "150" in reply
    data = bot.act("edit", OWNER, {"id": tid, "note": "-", "time": "-"})
    assert data["batch_id"] and data["txn"]["note"] == "" and data["txn"]["time"] is None
    t = _query(bot, OWNER)[0]
    assert t["amount"] == 150 and t["note"] == ""
    reply = bot.command("edit", OWNER, tid)
    assert reply.startswith("❌")


def test_switch_to_advance_and_back(make_money):
    bot = make_money()
    bot.command("add", OWNER, "300 範例訂閱")
    tid = _query(bot, OWNER)[0]["id"]
    bot.act("edit", OWNER, {"id": tid, "advance": "yes"})
    t = _query(bot, OWNER)[0]
    assert t["kind"] == "transfer" and t["purpose"] == "lend" and t["to_account"] == "代墊款"
    bot.command("edit", OWNER, f"{tid} advance=no")
    t = _query(bot, OWNER)[0]
    assert t["kind"] == "expense" and t["purpose"] is None


def test_long_term_use(make_money):
    bot = make_money()
    bot.act("add", OWNER, {"amount": "900", "desc": "範例耳機", "nature": "stock"})
    tid = _query(bot, OWNER)[0]["id"]
    assert _query(bot, OWNER)[0]["nature"] == "stock"
    bot.command("edit", OWNER, f"{tid} nature=-")
    assert not _query(bot, OWNER)[0]["nature"]


def test_which_period_it_counts_for(make_money):
    bot = make_money()
    bot.act("add", OWNER, {"amount": "8000", "category": "房租", "for_period": "2030-02"})
    rows = _query(bot, OWNER)
    if not rows or rows[0]["category"] != "房租":
        pytest.skip("起始類別沒有房租")
    tid = rows[0]["id"]
    assert rows[0]["for_period"] == "2030-02"
    bot.act("edit", OWNER, {"id": tid, "for_period": "-"})
    assert not _query(bot, OWNER)[0]["for_period"]


# ── 刪、撤銷 ─────────────────────────────────────────────────────────────────

def test_rm_then_undo_brings_it_back(make_money):
    bot = make_money()
    bot.command("add", OWNER, "120 午餐")
    tid = _query(bot, OWNER)[0]["id"]
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        assert "刪掉這一筆" in bot.module.ledger.describe_rm(book, {"id": tid})
    data = bot.act("rm", OWNER, {"id": tid})
    assert data["batch_id"] and _query(bot, OWNER) == []
    assert closing(bot, OWNER) == 0
    reply = bot.command("undo", OWNER)
    assert "撤銷" in reply
    assert [t["id"] for t in _query(bot, OWNER)] == [tid]
    assert closing(bot, OWNER) == -120


def test_undo_restores_the_balance(make_money):
    bot = make_money()
    bot.command("in", OWNER, "1000 薪資")
    bot.command("add", OWNER, "250 範例")
    assert closing(bot, OWNER) == 750
    data = bot.act("undo", OWNER)
    assert data["batch_id"] == data["by"] and data["write_seq"]
    assert closing(bot, OWNER) == 1000
    bot.act("revert", OWNER, {"id": data["by"]})                 # 撤銷「撤銷」＝回來
    assert closing(bot, OWNER) == 750


def test_revert_same_batch_twice_does_not_double(make_money):
    """同一批送兩次撤銷：第二次被擋，餘額不會多回來一次。"""
    bot = make_money()
    bot.command("in", OWNER, "1000 薪資")
    data = bot.act("add", OWNER, {"amount": "300"})
    bot.act("revert", OWNER, {"id": data["batch_id"]})
    assert closing(bot, OWNER) == 1000
    with pytest.raises(kit.AppError) as no:
        bot.act("revert", OWNER, {"id": data["batch_id"]})
    assert "撤銷過" in no.value.message
    assert closing(bot, OWNER) == 1000
    reply = bot.command("revert", OWNER, data["batch_id"])
    assert reply.startswith("❌")


def test_undo_with_nothing_to_undo(make_money):
    bot = make_money()
    reply = bot.command("undo", OWNER)
    assert reply.startswith("❌") and "沒有可以撤銷" in reply


def test_revert_describe_names_the_batch(make_money):
    bot = make_money()
    data = bot.act("add", OWNER, {"amount": "300"})
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        assert "add" in bot.module.ledger.describe_revert(book, {"id": data["batch_id"]})


# ── 小日記、重新分類、可能重複 ─────────────────────────────────────────────────────────

def test_note_appends_and_replaces(make_money):
    bot = make_money()
    bot.command("note", OWNER, "今天 天氣很好")
    data = bot.act("note", OWNER, {"date": "2030-01-15", "text": "吃了便當"})
    assert data["note"] == "天氣很好；吃了便當" and data["batch_id"]
    data = bot.act("note", OWNER, {"date": "2030-01-15", "text": "重寫", "replace": "yes"})
    assert data["note"] == "重寫"


def test_reclassify_runs(make_money):
    bot = make_money()
    reply = bot.command("reclassify", OWNER)
    assert "沒有能自動分的" in reply or "分好了" in reply
    data = bot.act("reclassify", OWNER)
    assert data["count"] == 0 and data["batch_id"] is None


def test_dedup_scan_and_resolve(make_money):
    bot = make_money()
    bot.command("add", OWNER, "120 範例便當店 time=12:00")
    first = bot.act("add", OWNER, {"amount": "120", "desc": "範例便當店", "time": "12:05"})
    res = first["results"][0]
    assert res["status"] == "possible_duplicate" and any("/money_dedup" in w for w in first["warnings"])
    pair = res["pair_id"]
    assert bot.read("inbox", OWNER)["counts"]["duplicates"] == 1
    scan = bot.act("dedup", OWNER)
    assert "pairs" in scan and len(scan["pairs"]) == 1
    with pytest.raises(kit.AppError):
        bot.act("dedup", OWNER, {"id": str(pair)})                  # 沒說怎麼處理
    reply = bot.command("dedup", OWNER, f"{pair} delete_newer")
    assert "刪掉較新的" in reply
    assert len(_query(bot, OWNER)) == 1


# ── 列出、看一筆 ──────────────────────────────────────────────────────────────────

def test_list_filters_and_show(make_money):
    bot = make_money()
    bot.command("add", OWNER, "120 午餐 範例便當店")
    bot.command("add", OWNER, "45 範例飲料店 date=昨天")
    bot.command("in", OWNER, "3000 薪資")
    reply = bot.command("list", OWNER)
    assert "共 3 筆" in reply and "範例便當店" in reply
    assert "共 1 筆" in bot.command("list", OWNER, "q=飲料")
    assert "共 1 筆" in bot.command("list", OWNER, "kind=收入")
    assert "共 1 筆" in bot.command("list", OWNER, "date=昨天")
    assert "共 3 筆" in bot.command("list", OWNER, "month=2030-01")
    assert "最近 1 筆" in bot.command("list", OWNER, "1")
    no = bot.command("list", OWNER, "category=不存在的類別")
    assert no.startswith("❌")
    tid = _query(bot, OWNER, text="便當")[0]["id"]
    shown = bot.command("show", OWNER, tid)
    assert tid in shown and "create" in shown


# ── 畫面的讀 ───────────────────────────────────────────────────────────────────

def test_reads_have_the_shapes_the_page_uses(make_money):
    bot = make_money()
    bot.command("add", OWNER, "120 午餐 範例便當店")
    meta = bot.read("meta", OWNER)
    assert meta["today"] == "2030-01-15" and meta["current"].startswith("2030-01") and meta["write_seq"] == 1
    assert meta["piggy"] is False and meta["default_account"] == "現金" and "total" in meta["inbox"]
    ref = bot.read("ref", OWNER)
    assert {a["name"] for a in ref["accounts"]} >= {"現金", "銀行帳戶"}
    assert "expense" in ref["categories_by_kind"] and ref["category_bucket"]
    assert [p["which"] for p in ref["periods"]] == ["prev", "current", "next"]
    pb = bot.read("passbook", OWNER)
    assert pb["period"]["key"] == meta["current"] and pb["items"][0]["amount"] == -120 and pb["closing"] == -120
    assert "wallet" in pb and "prev" in pb
    items = bot.read("items", OWNER, {"category": "午餐"})
    assert "範例便當店" in items["items"]
    assert bot.read("items", OWNER, {}) == {"category": None, "items": []}
    guess = bot.read("guess", OWNER, {"desc": "範例便當", "time": "12:30"})
    assert set(guess) == {"category", "group", "reason"}
    tid = pb["items"][0]["id"]
    assert bot.read("txn", OWNER, {"id": tid})["id"] == tid
    assert bot.read("batches", OWNER, {"limit": "5"})["batches"][0]["command"] == "add"


def test_piggy_tab_shows_when_there_is_a_securities_account(make_money):
    bot = make_money()
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        book.conn.execute("INSERT INTO accounts (name, kind) VALUES ('範例證券', 'securities')")
    assert bot.read("meta", OWNER)["piggy"] is True
    assert bot.read("meta", OTHER)["piggy"] is False


# ── 兩個人互不可見 ────────────────────────────────────────────────────────────────

def test_two_people_never_see_each_other(make_money):
    bot = make_money(per_user=True)
    bot.command("add", OWNER, "120 午餐 範例便當店")
    tid = _query(bot, OWNER)[0]["id"]
    assert _query(bot, OTHER) == []
    assert "範例便當店" not in bot.command("list", OTHER)
    assert bot.read("passbook", OTHER)["items"] == []
    for name, values in (("edit", {"id": tid, "amount": "1"}), ("rm", {"id": tid})):
        with pytest.raises(kit.AppError):
            bot.act(name, OTHER, values)
    with pytest.raises(kit.AppError):
        bot.read("txn", OTHER, {"id": tid})
    assert bot.command("undo", OTHER).startswith("❌")
    assert _query(bot, OWNER)[0]["amount"] == 120
