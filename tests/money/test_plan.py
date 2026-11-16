"""規劃（W5）：每期收入怎麼分、各格、三組改名、固定項目；金額的小數位數（0 與 2）。測試資料都是假的。"""

import importlib

import pytest

from synox.packs import kit

from .conftest import OTHER, OWNER, U


@pytest.fixture
def bot(make_money):
    return make_money()


@pytest.fixture
def screen(bot, monkeypatch):
    monkeypatch.setattr(bot.module.session, "actor_of", lambda: "synox:web")
    return bot


def svc(bot, name):
    return importlib.import_module(f"{bot.module.__name__}.engine.services.{name}")


def plan_of(bot, user=OWNER):
    return bot.read("setup", user)["plan"]


def bucket(bot, bid, user=OWNER):
    return next(b for b in bot.read("setup", user)["buckets"] if b["id"] == bid)


# ── 計畫 ─────────────────────────────────────────────────────────────────────────

def test_screen_saves_the_whole_plan_in_one_batch(screen):
    bot = screen
    r = bot.act("plan", OWNER, {"period": "2030-01", "income_expected": "40000",
                                "buckets": '{"fixed": 12000, "saving": 5000, "living": 15000}'})
    assert r["batch_id"] and r["plan"]["income_expected"] == 40000
    p = plan_of(bot)
    assert p["buckets"]["fixed"] == 12000 and p["buckets"]["saving"] == 5000 and p["buckets"]["living"] == 15000
    # 一次存＝一個批次：復原一次就全部回去
    revert = svc(bot, "batches")
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        db = importlib.import_module(f"{bot.module.__name__}.engine.db")
        with db.write(book.conn, actor=book.actor, source=book.actor, command="test revert") as w:
            revert.revert(w, r["batch_id"])
    p = plan_of(bot)
    assert p["income_expected"] is None and p["buckets"]["fixed"] == 0
    assert plan_of(bot, OTHER)["buckets"]["fixed"] == 0


def test_group_budget_alone_and_clearing_income(screen):
    bot = screen
    bot.act("plan", OWNER, {"income_expected": "40000", "group_budgets": '{"食": 8000}'})
    p = plan_of(bot)
    assert p["group_budgets"]["食"] == 8000 and p["income_expected"] == 40000
    bot.act("plan", OWNER, {"income_expected": "-"})
    assert plan_of(bot)["income_expected"] is None


def test_plan_needs_something(bot):
    with pytest.raises(kit.AppError):
        bot.act("plan", OWNER, {"period": "2030-01"})


def test_typed_plan_takes_bucket_names(bot):
    reply = bot.command("plan", OWNER, "buckets=存錢:5000,生活費:15000")
    assert "存錢" in reply and "5,000" in reply, reply
    assert plan_of(bot)["buckets"]["saving"] == 5000


def test_amounts_with_zero_decimals(bot):
    reply = bot.command("plan", OWNER, "income_expected=40000")
    assert "NT$40,000" in reply
    assert plan_of(bot)["income_expected"] == 40000
    with pytest.raises(kit.AppError) as no:
        bot.act("plan", OWNER, {"income_expected": "99.5"})
    assert "0 位小數" in no.value.message


def test_amounts_with_two_decimals(bot, monkeypatch):
    bot.act("init", OWNER, {"currency": "USD"})
    reply = bot.command("plan", OWNER, "income_expected=99.5")          # 人打字：照帳本的小數位數換
    assert "$99.50" in reply, reply
    assert plan_of(bot)["income_expected"] == 9950
    monkeypatch.setattr(bot.module.session, "actor_of", lambda: "synox:web")
    bot.act("plan", OWNER, {"income_expected": "12345"})                 # 畫面：本來就是最小單位
    assert plan_of(bot)["income_expected"] == 12345
    with pytest.raises(kit.AppError):
        bot.act("plan", OWNER, {"income_expected": "1.5"})


def test_plan_names_rename_and_reset(bot):
    r = bot.act("plan_names", OWNER, {"must": "跑不掉的"})
    assert r["group_names"]["must"]["name"] == "跑不掉的"
    r = bot.act("plan_names", OWNER, {"must": "-"})
    assert r["group_names"]["must"]["name"] == r["group_names"]["must"]["default"]
    with pytest.raises(kit.AppError):
        bot.act("plan_names", OWNER, {"flex": "這個名字真的太長了超過十個字"})


# ── 格子 ─────────────────────────────────────────────────────────────────────────

def test_own_bucket_can_roll_over_core_cannot(bot):
    r = bot.act("bucket", OWNER, {"label": "水電瓦斯"})
    bid = r["bucket"]["id"]
    assert not r["bucket"]["core"]
    r = bot.act("bucket", OWNER, {"id": bid, "rollover": "yes", "flex": "yes"})
    assert r["bucket"]["rollover"] and r["bucket"]["rollover_from"] == "2030-01" and r["bucket"]["flex"]
    with pytest.raises(kit.AppError) as no:
        bot.act("bucket", OWNER, {"id": "saving", "rollover": "yes"})
    assert "核心" in no.value.message
    with pytest.raises(kit.AppError):
        bot.act("bucket", OWNER, {"id": bid, "locked": "yes"})          # 滾存的不能同時固定住


def test_bucket_by_name_lock_claim_and_stop(screen):
    bot = screen
    r = bot.act("bucket", OWNER, {"label": "手機費", "locked": "yes"})
    bid = r["bucket"]["id"]
    assert r["bucket"]["locked"]
    r = bot.act("bucket", OWNER, {"id": "手機費", "categories": "手機網路,訂閱"})
    assert set(bot.read("setup", OWNER)["bucket_categories"][bid]) == {"手機網路", "訂閱"}
    bot.act("bucket", OWNER, {"id": bid, "categories": "-"})
    assert bot.read("setup", OWNER)["bucket_categories"][bid] == []
    bot.act("bucket", OWNER, {"id": bid, "add_categories": "手機網路"})
    assert bot.read("setup", OWNER)["bucket_categories"][bid] == ["手機網路"]
    r = bot.act("bucket", OWNER, {"id": bid, "active": "no"})
    assert not r["bucket"]["active"]
    assert all(b["id"] != bid for b in bot.read("setup", OWNER)["buckets"])


def test_living_cannot_be_locked_or_stopped(bot):
    with pytest.raises(kit.AppError):
        bot.act("bucket", OWNER, {"id": "living", "locked": "yes"})
    with pytest.raises(kit.AppError):
        bot.act("bucket", OWNER, {"id": "living", "active": "no"})


def test_new_bucket_needs_a_short_name(bot):
    with pytest.raises(kit.AppError):
        bot.act("bucket", OWNER, {})
    with pytest.raises(kit.AppError):
        bot.act("bucket", OWNER, {"label": "這個名字超過八個字了啦"})


# ── 固定項目 ──────────────────────────────────────────────────────────────────────

def test_fixed_item_add_edit_and_stop(screen):
    bot = screen
    r = bot.act("fixed", OWNER, {"name": "房租", "kind": "expense", "amount": "12000", "day_of_month": "5",
                                 "category": "房租", "bucket": "fixed"})
    item = r["item"]
    assert item["amount"] == 12000 and item["bucket_id"] == "fixed" and r["batch_id"]
    r = bot.act("fixed", OWNER, {"id": str(item["id"]), "amount": "-", "note": "範例房東"})
    assert r["item"]["amount"] is None and r["item"]["note"] == "範例房東"
    with pytest.raises(kit.AppError) as no:
        bot.act("fixed", OWNER, {"id": str(item["id"]), "kind": "income"})
    assert "種類不能改" in no.value.message
    r = bot.act("fixed", OWNER, {"id": "房租", "active": "no"})
    assert not r["item"]["active"]
    assert bot.read("setup", OWNER)["recurring"] == []


def test_fixed_transfer_needs_a_destination(bot):
    with pytest.raises(kit.AppError):
        bot.act("fixed", OWNER, {"name": "存旅遊", "kind": "轉存", "amount": "3000"})
    r = bot.act("fixed", OWNER, {"name": "存旅遊", "kind": "轉存", "amount": "3000", "to": "銀行帳戶",
                                 "account": "現金", "purpose": "存錢"})
    assert r["item"]["purpose"] == "saving" and r["item"]["to_account"] == "銀行帳戶"


def test_fixed_items_post_once_per_period(screen):
    """一鍵記下（總覽的按鈕是別的包；這裡直接叫引擎）：重按不重複。"""
    bot = screen
    bot.act("fixed", OWNER, {"name": "房租", "kind": "expense", "amount": "12000", "day_of_month": "5",
                             "category": "房租", "bucket": "fixed"})
    rec = svc(bot, "recurring")
    db = importlib.import_module(f"{bot.module.__name__}.engine.db")
    posted = []
    for _ in range(2):
        with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
            with db.write(book.conn, actor=book.actor, source=book.actor, command="recurring post") as w:
                posted.append(rec.post(w))
    assert [c["name"] for c in posted[0]["created"]] == ["房租"]
    assert posted[1]["created"] == []
    state = bot.read("setup", OWNER)["recurring"][0]
    assert state["level"] == "good" and state["posted_amount"] == 12000
    assert bot.read("book_info", OTHER)["transactions"] == 0
