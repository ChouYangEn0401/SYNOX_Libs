"""帳戶與類別：名稱解析（完全相同→別名→正規化）、建議、增改、類別樹。"""
import pytest

from money_engine import db
from money_engine.errors import Conflict, InvalidInput, NotFound
from money_engine.services import refdata


@pytest.fixture
def conn(conn):
    with db.write(conn) as w:
        refdata.add_account(w, name="測試 CARD", kind="credit_card", aliases=["card", "測試"], card_closing_day=25)
    return conn


def test_normalize():
    assert refdata.normalize("ＳＡＭＰＬＥ 卡") == "sample卡"
    assert refdata.normalize("Food・Box") == "foodbox"


def test_resolve_account_by_name_alias_and_normalized(conn):
    assert refdata.resolve_account(conn, "測試 CARD")["name"] == "測試 CARD"
    assert refdata.resolve_account(conn, "card")["name"] == "測試 CARD"
    assert refdata.resolve_account(conn, "ＣＡＲＤ")["name"] == "測試 CARD"
    assert refdata.resolve_account(conn, "測試card")["name"] == "測試 CARD"
    assert refdata.resolve_account(conn, "範例卡")["name"] == "範例 Demo"           # 起始檔裡的別名


def test_unknown_account_suggests(conn):
    with pytest.raises(NotFound) as e:
        refdata.resolve_account(conn, "測試卡")
    assert "測試 CARD" in e.value.suggestions
    with pytest.raises(InvalidInput):
        refdata.resolve_account(conn, "  ")


def test_ambiguous_normalized_match_is_conflict(conn):
    with db.write(conn) as w:
        refdata.add_account(w, name="範例 Pi", kind="credit_card", aliases=["pi卡"])
        refdata.add_account(w, name="PI 卡", kind="credit_card")
    with pytest.raises(Conflict):
        refdata.resolve_account(conn, "Pi卡")


def test_add_and_update_account(conn):
    with pytest.raises(Conflict):
        with db.write(conn) as w:
            refdata.add_account(w, name="測試 CARD", kind="credit_card")
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            refdata.add_account(w, name="x", kind="wallet")
    with db.write(conn) as w:
        a = refdata.add_account(w, name="借給朋友", kind="receivable")
        assert a["liquid"] == 0
        b = refdata.update_account(w, a["id"], purpose="借出應收", aliases=["朋友"])
    assert b["aliases"] == ["朋友"] and b["purpose"] == "借出應收"


def test_find_category_and_suggestions(conn):
    assert refdata.find_category(conn, "晚餐")["group_name"] == "三餐"
    assert refdata.find_category(conn, "晚餐")["is_living"] is True
    assert refdata.find_category(conn, "宵夜") is None
    assert refdata.find_category(conn, "") is None
    assert refdata.category_suggestions(conn, "三餐") == ["早餐", "午餐", "晚餐"]
    assert "點心宵夜" in refdata.category_suggestions(conn, "宵夜")


def test_tree_has_buckets_groups_categories(conn):
    t = refdata.tree(conn)
    assert [b["name"] for b in t][:6] == ["捐贈", "固定支出", "存錢儲蓄", "生活支出", "資產投資", "負債償還"]
    living = next(b for b in t if b["bucket"] == "living")
    assert living["groups"][0]["name"] == "三餐"
    assert t[-1]["name"] == "收入與轉帳"


def test_add_update_category(conn):
    with db.write(conn) as w:
        c = refdata.add_category(w, name="園藝", group="購物", keywords=["盆栽", "肥料"])
        assert c["group_name"] == "購物" and c["keywords"] == ["盆栽", "肥料"]
        c2 = refdata.update_category(w, c["id"], default_budget=800)
    assert c2["default_budget"] == 800
    with pytest.raises(NotFound):
        with db.write(conn) as w:
            refdata.add_category(w, name="x", group="不存在的大類")
