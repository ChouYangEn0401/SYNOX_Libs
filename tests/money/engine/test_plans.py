"""期計畫：預設值、沿用上一期、改預算、六大類、撤銷。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput, NotFound
from money_engine.services import batches, plans, refdata


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:      # 模擬匯入時寫進細項預設預算
        for name, amount in [("早餐", 1550), ("午餐", 4650), ("晚餐", 6200), ("飲料", 800)]:
            refdata.update_category(w, refdata.find_category(c, name)["id"], default_budget=amount)
    yield c
    c.close()


def test_defaults_come_from_category_budgets(conn):
    p = plans.get(conn, "2037-10")
    assert p["source"] == "default"
    assert p["group_budgets"]["三餐"] == 12400 and p["group_budgets"]["吃好喝好"] == 800
    assert p["living_budget"] == 13200 and p["buckets"]["living"] == 13200
    assert p["daily_budget"] == round(13200 / 31)
    assert "捐贈" not in p["group_budgets"]                          # 不是生活費的大類不列


def test_set_group_budget_saves_and_carries_forward(conn):
    with db.write(conn) as w:
        plans.set_group_budget(w, "三餐", 9000, "2037-10")
    assert plans.get(conn, "2037-10")["group_budgets"]["三餐"] == 9000
    nxt = plans.get(conn, "2037-11")
    assert nxt["source"] == "carried" and nxt["carried_from"] == "2037-10" and nxt["group_budgets"]["三餐"] == 9000
    assert plans.get(conn, "2037-09")["source"] == "default"


def test_set_plan_buckets_and_validation(conn):
    with db.write(conn) as w:
        p = plans.set_plan(w, "2037-10", income_expected=45000,
                           buckets={"giving": 12000, "fixed": 9900, "saving": 4200, "invest": 1000, "debt": 2900},
                           group_budgets={"三餐": 9000, "吃好喝好": 2200})
    assert p["income_expected"] == 45000 and p["buckets"]["giving"] == 12000
    assert p["buckets"]["living"] == p["living_budget"] == 11200
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            plans.set_plan(w, "2037-10", buckets={"food": 1})
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            plans.set_group_budget(w, "捐贈", 100)                    # 不是生活費的大類不能設共用上限
    with pytest.raises(NotFound):
        with db.write(conn) as w:
            plans.set_group_budget(w, "不存在", 100)


def test_plan_change_can_be_reverted(conn):
    with db.write(conn) as w:
        plans.set_group_budget(w, "三餐", 9000, "2037-10")
        b = w.batch_id
    with db.write(conn) as w:
        batches.revert(w, b)
    assert plans.get(conn, "2037-10")["source"] == "default"


def test_custom_bucket_add_fill_and_disable(conn):
    """薪水怎麼分的項目不寫死——自己加用途、填金額、停用（舊金額還看得到）、living 不能停用。"""
    c = conn
    with db.write(c) as w:
        b = plans.add_bucket(w, "教育", locked=True)
    assert b["id"] == "extra1" and b["core"] is False and b["locked"] and b["active"]
    assert "extra1" in plans.bucket_ids(c) and plans.bucket_names(c)["extra1"] == "教育"
    with db.write(c) as w:
        plan = plans.set_plan(w, "2037-10", buckets={"extra1": 3000})
    assert plan["buckets"]["extra1"] == 3000
    with db.write(c) as w:
        plans.set_bucket(w, "extra1", active=False)
    assert "extra1" not in plans.bucket_ids(c)                       # 之後要分的項目不列它
    assert plans.get(c, "2037-10")["buckets"]["extra1"] == 3000      # 但舊計畫的金額還看得到
    with db.write(c) as w:
        again = plans.add_bucket(w, "教育")                           # 同名：重新打開，不會多一個
    assert again["id"] == "extra1" and again["active"] and plans.bucket_ids(c).count("extra1") == 1
    with pytest.raises(InvalidInput):
        with db.write(c) as w:
            plans.set_bucket(w, "living", active=False)
    with pytest.raises(InvalidInput):
        with db.write(c) as w:
            plans.set_plan(w, "2037-10", buckets={"nothing": 100})   # 沒有這個用途


def test_add_group_is_a_shared_cap_and_rename_keeps_budget():
    """大類＝一群細項共用的上限；細項各有自己的上限（default_budget）。改名時每期的預算跟著走。"""
    c = db.connect()
    with db.write(c) as w:
        g = refdata.add_group(w, name="娛樂")
        refdata.add_category(w, name="電影", group="娛樂", default_budget=1000)
        refdata.add_category(w, name="展覽", group="娛樂", default_budget=2000)
        plans.set_group_budget(w, "娛樂", 2500, "2037-10")            # 共用上限可以小於單項上限加總
    assert g["is_living"] and g["bucket_id"] == "living"
    assert plans.get(c, "2037-10")["group_budgets"]["娛樂"] == 2500
    caps = {x["name"]: x["default_budget"] for x in refdata.categories(c)}
    assert caps["電影"] == 1000 and caps["展覽"] == 2000
    with db.write(c) as w:
        refdata.update_group(w, g["id"], name="玩樂")
    plan = plans.get(c, "2037-10")
    assert plan["group_budgets"]["玩樂"] == 2500 and "娛樂" not in plan["group_budgets"]
    with db.write(c) as w:
        refdata.update_group(w, g["id"], active=0)
    assert "玩樂" not in [x["name"] for x in refdata.groups(c)]
    assert "玩樂" in [x["name"] for x in refdata.groups(c, include_inactive=True)]
    c.close()


def test_a_zero_saved_before_the_caps_existed_does_not_freeze_the_group(conn):
    """計畫先存了（三餐當時沒上限＝0），後來才設早 50／午 150／晚 200（每天）。
    0 不能被存下來蓋過上限、讓生活費卡在 2,500；0＝沒設，生活費跟著大類加總。"""
    with db.write(conn) as w:
        for name, cap in (("早餐", 50), ("午餐", 150), ("晚餐", 200)):
            refdata.update_category(w, refdata.find_category(conn, name)["id"], default_budget=cap, cap_basis="day")
        for name in ("飲料",):
            refdata.update_category(w, refdata.find_category(conn, name)["id"], default_budget=None)
        plans._save(w, "2037-10", {"income_expected": 40000,
                                   "buckets": {"giving": 4000, "living": 2500},
                                   "group_budgets": {"三餐": 0, "吃好喝好": 2000, "交通": 500}}, "manual")
    p = plans.get(conn, "2037-10")
    assert p["group_budgets"]["三餐"] == 12400                       # 每天 400 × 31 天
    assert p["living_follows"] and p["living_budget"] == 12400 + 2000 + 500
    # 自己填一個不一樣的數字才算「自己決定」
    with db.write(conn) as w:
        p = plans.set_plan(w, "2037-10", buckets={"living": 9000})
    assert not p["living_follows"] and p["living_budget"] == 9000


def test_saving_the_plan_form_does_not_freeze_the_shown_living_number(conn):
    """表單的「生活費」欄預設填著大類加總；原封不動按存，不能把它存成自己決定的數字。"""
    with db.write(conn) as w:
        shown = plans.get(conn, "2037-10")["buckets"]["living"]
        plans.set_plan(w, "2037-10", income_expected=40000, buckets={"giving": 5000, "living": shown})
    with db.write(conn) as w:
        plans.set_group_budget(w, "三餐", 9000, "2037-10")           # 之後改大類，生活費要跟著動
    p = plans.get(conn, "2037-10")
    assert p["living_follows"] and p["living_budget"] == 9000 + 800
    assert p["buckets"]["giving"] == 5000 and p["income_expected"] == 40000
