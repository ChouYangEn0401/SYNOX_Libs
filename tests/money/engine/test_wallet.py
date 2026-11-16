"""錢包（存摺）、保留、自由可用、健康檢查、首頁與一年。今天 2037-10-10，本期 10/05–11/04。"""
import pytest

from money_engine import db
from money_engine.services import balances, debts, health, home, ingest, plans, refdata, wallet

CARD = "範例 Demo"
RESERVE = "預備金帳戶"


@pytest.fixture
def conn(conn):
    with db.write(conn) as w:
        refdata.add_account(w, name=RESERVE, kind="bank", floor_amount=50000, target_amount=90000)
        refdata.add_account(w, name="借給朋友", kind="receivable")
        plans.set_plan(w, "2037-10", income_expected=40000,
                       buckets={"giving": 4000, "fixed": 9000, "saving": 6000, "invest": 3000, "debt": 2000},
                       group_budgets={"三餐": 12000, "交通": 1000})
    ingest.run(conn, {"records": [
        {"type": "expense", "date": "2037-10-04", "amount": 120, "desc": "早餐", "category": "早餐"},
        {"type": "income", "date": "2037-10-05", "amount": 40000, "desc": "薪水", "category": "薪資", "account": "生活帳戶"},
        {"type": "expense", "date": "2037-10-06", "amount": 260, "desc": "麻油雞", "category": "晚餐", "account": CARD},
        {"type": "transfer", "date": "2037-10-07", "amount": 5000, "from": "生活帳戶", "to": CARD, "desc": "繳卡費"},
        {"type": "transfer", "date": "2037-10-07", "amount": 3000, "from": "生活帳戶", "to": RESERVE,
         "purpose": "saving", "desc": "存預備金"},
        {"type": "installment_purchase", "name": "手機", "card": CARD, "total": 24000, "periods": 12,
         "date": "2037-10-08", "category": "大額採買"},
        {"type": "transfer", "date": "2037-10-09", "amount": 500, "from": "現金", "to": "借給朋友", "desc": "借朋友"},
    ]})
    return conn


def phone_dues(c):
    d = next(x for x in debts.all_debts(c) if x["name"] == "手機")
    return [s for s in d["schedule"] if "2037-10-05" <= s["due_date"] <= "2037-11-04"]


def test_passbook_like_a_bank_statement(conn):
    book = wallet.passbook(conn, "2037-10")
    assert book["opening"] == -120                          # 薪水前先花了：上一期結束是負的
    descs = [i["desc"] for i in reversed(book["items"])]
    card = next(i for i in book["items"] if i["desc"] == "繳卡費")      # 自己帳戶互轉：列出來、餘額不變
    assert card["amount"] == 0 and card["raw"] == 5000 and card["note"].startswith(f"生活帳戶 → {CARD}")
    assert descs[:2] == ["薪水", "麻油雞"] and "存預備金" in descs and "借朋友" in descs
    phone = next(i for i in book["items"] if i["desc"] == "手機")
    assert phone["amount"] == 0 and "24,000" in phone["note"]   # 分期買的：當下不扣
    due_now = sum(s["amount"] for s in phone_dues(conn) if s["due_date"] <= "2037-10-10")
    expected = -120 + 40000 - 260 - 3000 - 500 - due_now
    assert book["closing"] == expected and book["items"][0]["balance"] == expected   # 新的在最上面
    ordered = list(reversed(book["items"]))
    running = book["opening"]
    for item in ordered:                                       # 每一行的餘額＝上一行＋這一筆
        running += item["amount"]
        assert item["balance"] == running


def test_reserved_and_free(conn):
    w = wallet.summary(conn, "2037-10")
    reserved = {r["id"]: r["amount"] for r in w["reserved_items"]}
    upcoming = sum(s["amount"] for s in phone_dues(conn) if s["due_date"] > "2037-10-10")
    assert reserved["giving"] == 4000 and reserved["fixed"] == 9000
    assert reserved["saving"] == 3000 and reserved["invest"] == 3000     # 存錢計畫 6,000 已經存了 3,000
    assert reserved.get("debt", 0) == upcoming
    assert w["free"] == w["wallet"] - w["reserved"]
    assert wallet.summary(conn, "2037-09")["reserved"] == 0             # 過去的期不保留


def test_health_checks(conn):
    items = health.checks(conn, "2037-10")
    texts = {i["key"]: i for i in items}
    assert texts["living"]["level"] == "good"
    assert texts["plan"]["level"] == "good" and "3,000 沒分，留著當緩衝" in texts["plan"]["text"]
    assert texts["reserve"]["level"] == "bad" and RESERVE in texts["reserve"]["text"]
    assert texts["debt"]["level"] == "good" and "收入的 5%" in texts["debt"]["text"]
    assert [i["level"] for i in items] == sorted((i["level"] for i in items), key=lambda x: health.ORDER[x])
    assert health.headline(items)["level"] == "bad"


def test_home_and_year(conn):
    h = home.home(conn, "2037-10")
    assert h["living"]["budget"] == 13000 and h["living"]["spent"] == 260 and h["living"]["left"] == 12740
    env = {e["id"]: e for e in h["envelopes"]}
    assert [e["id"] for e in h["envelopes"]] == ["living", "giving", "fixed", "saving", "invest", "debt"]
    assert env["saving"]["used"] == 3000 and env["saving"]["state"] == "todo" and env["giving"]["state"] == "todo"
    assert h["plan"]["income"] == 40000 and h["plan"]["unassigned"] == 3000
    d = h["debts"]["items"][0]
    assert d["name"] == "手機" and d["periods"] == 12 and h["debts"]["monthly"] == 2000 and h["debts"]["share"] == 0.05
    assert h["start"] == {"has_plan": True, "has_transactions": True} and h["review"] is None
    y = home.year(conn, 2037)
    oct_ = y["months"][9]
    assert oct_["income"] == 40000 and oct_["living"] == 260 and oct_["saved"] == 3000
    assert y["months"][10]["future"] and y["income"] == 40000 and y["next"] is None


def test_new_book_is_clean_and_negative_wallet_is_ok():
    c = db.connect()
    h = home.home(c)
    assert h["start"] == {"has_plan": False, "has_transactions": False}
    assert h["wallet"]["wallet"] == 0 and h["debts"]["items"] == []
    assert {i["text"] for i in h["health"]} >= {"還沒設生活費預算", "還沒設薪水分配"}
    ingest.run(c, {"records": [{"type": "expense", "date": "2037-10-09", "amount": 80, "desc": "早餐"}]})
    w = wallet.summary(c)
    assert w["wallet"] == -80                                    # 薪水還沒進來可以是負的
    assert any("錢包是負的" in i["text"] for i in health.checks(c))
    c.close()


def test_home_passbook_and_year_agree(conn):
    """首頁、存摺、一年三個畫面用的資料彼此一致（同一份錢包）。"""
    h = home.home(conn)
    assert h["period"]["key"] == "2037-10" and h["wallet"]["wallet"] == wallet.summary(conn)["wallet"]
    p = wallet.passbook(conn, "2037-10")
    assert p["opening"] == -120 and p["items"] and "free" in wallet.summary(conn, "2037-10")
    assert len(home.year(conn, 2037)["months"]) == 12
    assert balances.reserves(conn)
