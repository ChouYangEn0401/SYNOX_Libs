"""設定的規則（服務層）：帳戶、類別、設定值、期計畫、固定項目、投資設定的檢查；沒變不留批次、可以撤銷。"""
import pytest

from money_engine import db
from money_engine.errors import Conflict, InvalidInput
from money_engine.services import batches, invest, periods, plans, recurring, refdata, settings


def write(conn, fn):
    with db.write(conn) as w:
        out = fn(w)
        changed = w.changes
    return out, changed


def test_account_rules(conn):
    a, _ = write(conn, lambda w: refdata.add_account(w, name="測試 Card", kind="credit_card", aliases=["card", "測試卡"],
                                                     card_closing_day=25, card_due_day=10))
    assert a["aliases"] == ["card", "測試卡"] and refdata.resolve_account(conn, "card")["name"] == "測試 Card"
    with pytest.raises(InvalidInput) as e:
        write(conn, lambda w: refdata.add_account(w, name="壞卡", kind="credit_card", card_closing_day=40))
    assert e.value.fields == {"card_closing_day": "1–31"}
    with pytest.raises(Conflict):
        write(conn, lambda w: refdata.add_account(w, name="測試 Card", kind="bank"))
    with pytest.raises(Conflict):                                   # 改名撞到別的帳戶：衝突，不是 SQLite 例外
        write(conn, lambda w: refdata.update_account(w, a["id"], name="現金"))
    bank, _ = write(conn, lambda w: refdata.add_account(w, name="備用銀行", kind="bank"))
    r, changed = write(conn, lambda w: refdata.update_account(w, bank["id"], floor_amount=30000, target_amount=90000,
                                                               reserve_group="緊急預備金"))
    assert r["reserve_group"] == "緊急預備金" and changed
    _, changed = write(conn, lambda w: refdata.update_account(w, bank["id"], floor_amount=30000))
    assert changed == 0                                             # 沒變：不留批次
    r, _ = write(conn, lambda w: refdata.update_account(w, bank["id"], reserve_group="", floor_amount=None))
    assert r["reserve_group"] is None and r["floor_amount"] is None
    with pytest.raises(InvalidInput):                               # 目標比底線小
        write(conn, lambda w: refdata.update_account(w, bank["id"], floor_amount=50000, target_amount=10000))
    cash = refdata.resolve_account(conn, "現金")
    with pytest.raises(InvalidInput, match="預設付款"):
        write(conn, lambda w: refdata.update_account(w, cash["id"], active=0))


def test_account_edit_can_be_reverted(conn):
    bank, _ = write(conn, lambda w: refdata.add_account(w, name="備用銀行", kind="bank"))
    with db.write(conn) as w:
        refdata.update_account(w, bank["id"], purpose="緊急預備")
        batch = w.batch_id
    with db.write(conn) as w:
        batches.revert(w, batch)
    assert refdata.account_by_id(conn, bank["id"])["purpose"] == ""


def test_category_rules(conn):
    c, _ = write(conn, lambda w: refdata.add_category(w, name="園藝", group="購物", keywords=["盆栽", "肥料"],
                                                      default_budget=800))
    assert c["group_name"] == "購物" and c["keywords"] == ["盆栽", "肥料"] and c["default_budget"] == 800
    other = refdata.group_by_name(conn, "學習")["id"]
    e, _ = write(conn, lambda w: refdata.update_category(w, c["id"], group_id=other, keywords=["盆栽"], default_budget=500))
    assert e["group_name"] == "學習" and e["keywords"] == ["盆栽"]
    _, changed = write(conn, lambda w: refdata.update_category(w, c["id"], default_budget=500))
    assert changed == 0
    with pytest.raises(Conflict):
        write(conn, lambda w: refdata.update_category(w, c["id"], name="晚餐"))
    with pytest.raises(InvalidInput):
        write(conn, lambda w: refdata.update_category(w, c["id"], default_budget=-1))
    write(conn, lambda w: refdata.update_category(w, c["id"], active=0))
    assert refdata.find_category(conn, "園藝") is None              # 停用的不再拿來記帳


def test_setting_values(conn):
    write(conn, lambda w: refdata.add_account(w, name="測試 Card", kind="credit_card", aliases=["card"]))
    assert settings.coerce(conn, "default_account", "card") == "測試 Card"
    assert settings.coerce(conn, "review.include_notes", "false") is False
    assert settings.coerce(conn, "period_start_day", "10") == 10
    write(conn, lambda w: settings.put(w, "period_start_day", 10))
    assert periods.current(conn).key == "2037-10"                  # 已經產生的期不會被改掉
    # 拿掉的設定（叫外部 AI 的、券商匯入用的）不能再改
    for key, value in (("period_start_day", 40), ("secret", 1), ("review.max_budget_usd", 50),
                       ("review.model", "x"), ("import.invest_account", "現金")):
        with pytest.raises(InvalidInput):
            settings.coerce(conn, key, value)
    assert settings.coerce(conn, "currency.code", "usd") == "USD"
    assert settings.coerce(conn, "tz_offset", "5.5") == 5.5


def test_plan_rules(conn):
    p, _ = write(conn, lambda w: plans.set_plan(w, "2037-10", income_expected=45000,
                                                 buckets={"giving": 4500, "fixed": 12000, "saving": 5000},
                                                 group_budgets={"三餐": 9000, "交通": 1500}))
    assert p["source"] == "manual" and p["buckets"]["giving"] == 4500 and p["buckets"]["living"] == p["living_budget"] == 10500
    with pytest.raises(InvalidInput):
        write(conn, lambda w: plans.set_plan(w, "2037-10", buckets={"fun": 1}))


def test_recurring_rules(conn):
    write(conn, lambda w: refdata.add_account(w, name="薪轉戶", kind="bank"))
    item, _ = write(conn, lambda w: recurring.add(w, name="薪水", kind="income", amount=45000, day_of_month=5,
                                                  category="薪資", account="薪轉戶", deductions={"勞保": 1000, "健保": 600}))
    assert item["template"] == {"deductions": {"勞保": 1000, "健保": 600}} and item["account"] == "薪轉戶"
    e, _ = write(conn, lambda w: recurring.update(w, item["id"], clear_amount=True, account="", deductions={}))
    assert e["amount"] is None and e["account"] is None and e["template"] == {}
    with pytest.raises(InvalidInput):
        write(conn, lambda w: recurring.update(w, item["id"], day_of_month=32))
    with pytest.raises(Conflict):
        write(conn, lambda w: recurring.add(w, name="薪水", kind="income", amount=1))
    off, _ = write(conn, lambda w: recurring.update(w, item["id"], active=0))
    assert off["active"] is False


def test_invest_config(conn):
    write(conn, lambda w: refdata.add_account(w, name="證券戶", kind="securities"))
    cfg, _ = write(conn, lambda w: invest.set_config(w, capital=100000, start="2036-09-04", target_year=0.2))
    assert cfg == {"capital": 100000, "start": "2036-09-04", "target_year": 0.2}
    assert settings.coerce(conn, "invest.default_account", "證券戶") == "證券戶"
    assert settings.coerce(conn, "invest.default_account", "") is None
    with pytest.raises(InvalidInput):
        write(conn, lambda w: invest.set_config(w, target_year=22))
