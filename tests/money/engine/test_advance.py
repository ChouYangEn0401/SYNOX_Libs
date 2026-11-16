"""代墊（幫別人先付）：不算自己的花費，錢在「代墊款」等對方還；收回之後回來。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput, NotFound
from money_engine.services import allowance, cardcash, debts, editing, health, ingest, refdata, settings, stats, wallet
from money_engine.services import home as homepage


def _add(conn, *, amount, kind="expense", desc="", category=None, account=None, to=None, purpose=None,
         counterparty=None, date=None, merchant=""):
    """跟網站「記一筆」組的紀錄一樣：有 to 就是轉帳（沒給帳戶＝預設帳戶）。"""
    rec = {"type": "transfer" if to else kind, "amount": amount, "date": date, "desc": desc}
    if rec["type"] == "transfer":
        rec.update({"from": account or settings.get(conn, "default_account"), "to": to})
        for k, v in (("purpose", purpose), ("counterparty", counterparty), ("category", category)):
            if v:
                rec[k] = v
    else:
        if category:
            rec["category"] = category
        if account:
            rec["account"] = account
        if merchant and rec["type"] == "expense":
            rec["merchant"] = merchant
    return ingest.run(conn, {"records": [rec]}, actor="test", source="test")


def _edit(conn, tid, **fields):
    with db.write(conn) as w:
        return editing.edit(w, tid, **fields)


def _card_expense(conn, **extra):
    body = {"amount": 1200, "desc": "線上訂閱", "category": "訂閱", "account": "範例 Demo", "date": "2037-10-05",
            "merchant": "範例服務", **extra}
    return _add(conn, **body)["results"][0]["id"]


def test_advancing_is_not_a_living_expense_and_creates_the_account(conn):
    """第一次記代墊就自動建「代墊款」（應收）帳戶；不算生活費；錢包少了、對方還了就回來。"""
    _add(conn, amount=5000, desc="薪水", kind="income", category="薪資", date="2037-10-05")
    _add(conn, amount=1200, account="現金", to="代墊款", purpose="lend", desc="代墊", counterparty="朋友甲", date="2037-10-08")
    assert any(a["name"] == "代墊款" and a["kind"] == "receivable" for a in refdata.accounts(conn))
    s = stats.period_summary(conn, "2037-10")
    assert s["living"]["spent"] == 0                                              # 不是花費
    row = wallet.passbook(conn)["items"][0]
    assert (row["title"], row["amount"]) == ("代墊", -1200)                         # 錢包少了
    assert row["detail"].startswith("朋友甲・")                                    # 看得到是幫誰
    out = debts.receivables(conn)
    assert out[0]["counterparty"] == "朋友甲" and out[0]["outstanding"] == 1200
    _add(conn, amount=1200, account="代墊款", to="現金", purpose="collect", desc="收回", counterparty="朋友甲", date="2037-10-10")
    assert debts.receivables(conn)[0]["outstanding"] == 0
    assert wallet.passbook(conn)["closing"] == 5000                               # 還回來了


def test_other_missing_accounts_are_still_rejected(conn):
    with pytest.raises((InvalidInput, NotFound)):                                 # 打錯字不會悄悄多一個帳戶
        _add(conn, amount=100, account="現金", to="代墊顆", purpose="lend", date="2037-10-08")


def test_toggle_a_card_expense_into_an_advance_and_back(conn):
    """信用卡刷的訂閱其實是幫別人先付：切成代墊＝不是自己的花費、錢記在「代墊款」等對方還；切回去就是花錢。"""
    tid = _card_expense(conn)
    assert stats.period_summary(conn, "2037-10")["allocation"]["actual"]["fixed"] == 1200      # 切之前是固定支出（訂閱）

    t = _edit(conn, tid, advance=True, counterparty="公司")
    assert (t["kind"], t["purpose"], t["account"], t["to_account"], t["category"]) == ("transfer", "lend", "範例 Demo", "代墊款", "借出")
    assert t["title"] == "代墊" and "線上訂閱" in t["detail"] and "範例服務" in t["detail"]   # 大項目＝代墊，原本的字在細節
    s = stats.period_summary(conn, "2037-10")
    assert s["living"]["spent"] == 0 and s["allocation"]["actual"]["fixed"] == 0              # 不是花費
    out = debts.receivables(conn)
    assert out[0]["counterparty"] == "公司" and out[0]["outstanding"] == 1200

    t = _edit(conn, tid, advance=False)
    assert (t["kind"], t["to_account"], t["purpose"], t["category"]) == ("expense", None, None, "訂閱")   # 類別重猜（訂閱）
    assert debts.receivables(conn) == []                                                      # 不再是應收
    assert stats.period_summary(conn, "2037-10")["allocation"]["actual"]["fixed"] == 1200


def test_only_a_plain_expense_can_become_an_advance(conn):
    inc = _add(conn, amount=5000, desc="薪水", kind="income", category="薪資", date="2037-10-05")
    with pytest.raises(InvalidInput):
        _edit(conn, inc["results"][0]["id"], advance=True)
    plain = _card_expense(conn)
    assert _edit(conn, plain, advance=False)["kind"] == "expense"                             # 本來就是花錢：切回去沒事發生


def test_edit_toggles_the_advance_with_a_counterparty(conn):
    tid = _card_expense(conn)
    t = _edit(conn, tid, advance=True, counterparty="公司")
    assert t["purpose"] == "lend" and t["title"] == "代墊"
    assert _edit(conn, tid, advance=False)["kind"] == "expense"


def test_pending_advances_are_the_reminder_until_collected(conn):
    """還沒收回的代墊：總覽燈號有一句話（含對象、最早哪天）；對方還了（先借的先還）才消失。"""
    a = _card_expense(conn, date="2037-10-05")
    b = _card_expense(conn, date="2037-10-06", amount=800)
    for tid in (a, b):
        _edit(conn, tid, advance=True, counterparty="公司")
    adv = debts.pending_advances(conn)
    assert (adv["total"], adv["count"], adv["oldest"]) == (2000, 2, "2037-10-05")
    assert adv["who"] == [{"counterparty": "公司", "named": True, "amount": 2000, "count": 2}]
    line = next(i for i in health.checks(conn) if i["key"] == "advance")
    assert line["level"] == "warn" and line["text"] == "代墊還有 2,000 沒收回（2 筆，最早 10/5）：公司 2,000"
    assert homepage.home(conn)["advance"]["total"] == 2000

    _add(conn, amount=1200, account="代墊款", to="生活帳戶", purpose="collect", desc="收回", counterparty="公司", date="2037-10-09")
    adv = debts.pending_advances(conn)
    assert (adv["total"], [i["amount"] for i in adv["items"]]) == (800, [800])                 # 最早那筆先還掉
    _add(conn, amount=800, account="代墊款", to="生活帳戶", purpose="collect", desc="收回", counterparty="公司", date="2037-10-10")
    assert debts.pending_advances(conn)["total"] == 0
    assert not any(i["key"] == "advance" for i in allowance.check(conn))                       # 沒事就不講


def test_unnamed_advance_does_not_pretend_to_be_a_person(conn):
    tid = _card_expense(conn)
    _edit(conn, tid, advance=True)
    line = next(i for i in allowance.check(conn) if i["key"] == "advance")
    assert line["text"] == "代墊還有 1,200 沒收回（1 筆，最早 10/5）"                          # 沒寫對象就不列名字


def test_new_advance_on_the_card_can_be_recorded_directly(conn):
    """新記的代墊直接從信用卡出：不用先記花錢再切；卡費預留要算、存摺看得到對象與是不是分期。"""
    _add(conn, amount=1200, account="範例 Demo", to="代墊款", purpose="lend", desc="範例服務", counterparty="公司",
         date="2037-10-05")
    assert debts.pending_advances(conn)["total"] == 1200
    assert cardcash._spent_total(conn, refdata.resolve_account(conn, "範例 Demo")["id"]) == 1200
    row = wallet.passbook(conn)["items"][0]
    assert (row["title"], row["detail"].startswith("範例服務"), row["counterparty"], row["financed"]) == ("代墊", True, "公司", False)
