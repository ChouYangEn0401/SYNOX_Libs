"""六個用途的名字與「固定住」存在資料庫；分配可以超過收入（只提醒）；固定住沒做到、實際花超過要抓出來。
今天 2037-10-10，本期 10/05–11/04（還有 26 天）。"""
from datetime import date

import pytest

from money_engine import db, paths
from money_engine.errors import InvalidInput
from money_engine.services import batches, health, ingest, plans
from money_engine.services import home as homepage


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        plans.set_plan(w, "2037-10", income_expected=40000,
                       buckets={"giving": 4000, "fixed": 9000, "saving": 6000, "invest": 3000, "debt": 0},
                       group_budgets={"三餐": 9000, "購物": 1000})
    ingest.run(c, {"records": [
        {"type": "income", "date": "2037-10-05", "amount": 40000, "desc": "薪水", "category": "薪資", "account": "生活帳戶"},
        {"type": "expense", "date": "2037-10-06", "amount": 9500, "desc": "房租", "category": "房租", "account": "生活帳戶"},
        {"type": "expense", "date": "2037-10-07", "amount": 1300, "desc": "外套", "category": "購物"},
    ]})
    yield c
    c.close()


def _bucket_id(conn, name):
    """用途的 id 或名字 → id（跟命令列找用途的規則一樣）。"""
    for b in plans.buckets(conn, include_inactive=True):
        if name in (b["id"], b["name"], b["official"]):
            return b["id"]
    raise InvalidInput(f"沒有「{name}」這個用途")


def test_defaults_from_the_seed(conn):
    b = {x["id"]: x for x in plans.buckets(conn)}
    assert [x["id"] for x in plans.buckets(conn)][0] == "giving"
    assert b["saving"]["name"] == "存錢" and b["saving"]["official"] == "存錢儲蓄" and not b["saving"]["locked"]
    assert b["giving"]["locked"] and b["fixed"]["locked"] and b["debt"]["locked"] and b["living"]["is_living"]


def test_rename_and_lock_are_data_and_undoable(conn):
    with db.write(conn) as w:
        plans.set_bucket(w, "saving", label="緊急預備金", locked=True)
        batch = w.batch_id
    assert plans.bucket_names(conn)["saving"] == "緊急預備金"
    env = {e["id"]: e for e in homepage.home(conn)["envelopes"]}
    assert env["saving"]["name"] == "緊急預備金" and env["saving"]["locked"]
    with db.write(conn) as w:
        batches.revert(w, batch)
    assert plans.bucket_names(conn)["saving"] == "存錢"
    with db.write(conn) as w, pytest.raises(InvalidInput):
        plans.set_bucket(w, "living", locked=True)                # 生活費是用來花的
    with db.write(conn) as w, pytest.raises(InvalidInput):
        plans.set_bucket(w, "saving", label="捐贈")                # 不能跟別的撞名


def test_health_locked_over_and_overallocation(conn):
    items = {i["key"]: i for i in health.checks(conn, "2037-10")}
    assert items["locked:giving"]["level"] == "warn" and "捐贈還差 4,000" in items["locked:giving"]["text"]
    assert "locked:fixed" not in items                             # 房租 9,500 已經超過計畫：做到了
    assert items["over:fixed"]["text"] == "固定支出比計畫多 500"
    assert items["over:groups"]["text"] == "超過共用上限：購物 300"
    assert "locked:saving" not in items                            # 存錢沒固定住：不提醒
    with db.write(conn) as w:
        plans.set_plan(w, "2037-10", buckets={"invest": 13000})    # 4,000+9,000+6,000+13,000+生活 10,000＝42,000
    plan = {i["key"]: i for i in health.checks(conn, "2037-10")}["plan"]
    assert plan["level"] == "warn" and "分配比收入多 2,000" in plan["text"]


def test_locked_turns_red_in_last_week(conn, home):
    with paths.use_home(home, today=date(2037, 10, 30)):
        items = {i["key"]: i for i in health.checks(conn, "2037-10")}
    assert items["locked:giving"]["level"] == "bad"


def test_bucket_lock_twice_leaves_no_batch_and_bad_id_is_rejected(conn):
    """改用途：真的有變才留批次（沒變就不給復原）；沒有這個用途要擋下。"""
    with db.write(conn) as w:
        b = plans.set_bucket(w, "saving", locked=True)
        first = w.batch_id if w.changes else None
    assert b["locked"] and first
    with db.write(conn) as w:
        plans.set_bucket(w, "saving", locked=True)
        again = w.batch_id if w.changes else None
    assert again is None                                           # 沒變不留批次
    assert next(x for x in plans.buckets(conn) if x["id"] == "saving")["locked"]
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            plans.set_bucket(w, "nope", locked=True)


def test_plan_set_and_lock_bucket_by_name(conn):
    with db.write(conn) as w:
        p = plans.set_plan(w, None, income_expected=42000, buckets={"giving": 4200, "saving": 7000},
                           group_budgets={"三餐": 9500})
    assert p["income_expected"] == 42000 and p["buckets"]["giving"] == 4200 and p["group_budgets"]["三餐"] == 9500
    assert p["income_expected"] - sum(p["buckets"].values()) == 8300   # 42,000 − (4,200+9,000+7,000+3,000+生活 10,500)
    with db.write(conn) as w:
        b = plans.set_bucket(w, _bucket_id(conn, "存錢"), locked=True)
    assert b["id"] == "saving" and b["locked"]
    info = {x["id"]: x for x in plans.buckets(conn, include_inactive=True)}
    assert plans.get(conn, "2037-10", with_moves=False)["buckets"]["saving"] == 7000 and info["saving"]["locked"]
