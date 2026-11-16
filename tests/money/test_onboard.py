"""第一次使用（W5）：幣別、每期起算日、起始類別（通用／空白）、選配功能。測試資料都是假的。"""

import importlib

import pytest

from synox.packs import kit

from .conftest import OTHER, OWNER, U


@pytest.fixture
def bot(make_money):
    return make_money()


def names(bot, user=OWNER):
    refdata = importlib.import_module(f"{bot.module.__name__}.engine.services.refdata")
    with bot.module.session.open_book(bot.pack, U(user)) as book:
        return {c["name"]: c for c in refdata.categories(book.conn)}


def record_one(bot, user=OWNER):
    ingest = importlib.import_module(f"{bot.module.__name__}.engine.services.ingest")
    with bot.module.session.open_book(bot.pack, U(user)) as book:
        ingest.run(book.conn, {"records": [{"type": "expense", "amount": 120, "date": "2030-01-15", "desc": "雞腿便當",
                                            "category": "午餐", "account": "現金"}]}, actor=book.actor, source=book.actor)


def test_a_new_book_asks_for_onboarding(bot):
    f = bot.read("features", OWNER)
    assert f["onboarding"] and f["can_seed"]
    assert f["currency"]["code"] == "TWD" and f["currency"]["decimals"] == 0 and f["start_day"] == 1
    assert {s["id"] for s in f["seeds"]} == {"generic", "blank"}
    assert any(c["code"] == "USD" and c["decimals"] == 2 for c in f["currencies"])
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        assert bot.module.setup.needs_onboarding(book)


def test_init_with_defaults_marks_it_done(bot):
    r = bot.act("init", OWNER, {})
    assert r["done"] and r["batch_id"] and r["currency"]["code"] == "TWD"
    f = bot.read("features", OWNER)
    assert not f["onboarding"] and not f["can_seed"]
    assert bot.read("features", OTHER)["onboarding"]                     # 別人的還沒做


def test_init_generic_with_start_day_and_features(bot):
    r = bot.act("init", OWNER, {"seed": "通用", "start_day": "25", "features": "捐贈,invest"})
    assert r["seed"] == "generic" and r["start_day"] == 25 and set(r["turned_on"]) == {"giving", "invest"}
    on = {f["name"] for f in r["features"] if f["enabled"]}
    assert {"giving", "invest"} <= on
    assert "衣服" in names(bot) and "捐款" in names(bot)
    assert bot.read("setup", OWNER)["period"]["key"] == "2030-01"       # 1/15 落在 12/25～1/24 那一期


def test_init_blank_keeps_only_system_categories(bot):
    r = bot.act("init", OWNER, {"seed": "空白"})
    assert r["seed"] == "blank"
    have = names(bot)
    assert "午餐" in have and "繳卡費" in have and "薪資" in have and "房租" in have
    assert "衣服" not in have and "飲料" not in have
    assert all(c["system_key"] for c in have.values())
    assert "衣服" in names(bot, OTHER)                                   # 別人的帳本沒被動到
    starter = importlib.import_module(f"{bot.module.__name__}.engine.services.starter")
    seed = importlib.import_module(f"{bot.module.__name__}.engine.seed")
    starter.blank_seed()
    assert any(c["name"] == "衣服" for g in seed.config("generic")["groups"] for c in g["categories"])


def test_blank_book_still_records(bot):
    bot.act("init", OWNER, {"seed": "blank"})
    record_one(bot)
    assert bot.read("book_info", OWNER)["transactions"] == 1


def test_seed_is_only_chosen_once(bot):
    bot.act("init", OWNER, {"seed": "generic"})
    with pytest.raises(kit.AppError) as no:
        bot.act("init", OWNER, {"seed": "blank"})
    assert "新帳本" in no.value.message
    r = bot.act("init", OWNER, {"start_day": "5"})                       # 不換起始類別就可以再按一次
    assert r["start_day"] == 5


def test_book_with_entries_skips_the_seed_step(bot):
    """已經記過帳的帳本（例如先在聊天打了 /money 120 才開畫面）：不跑引導、不能選起始類別；init 的其他欄位照做。"""
    record_one(bot)
    f = bot.read("features", OWNER)
    assert not f["onboarding"] and not f["can_seed"]
    assert "onboarding" not in bot.read("home", OWNER)                   # 總覽與 features 同一個判斷
    with pytest.raises(kit.AppError):
        bot.act("init", OWNER, {"seed": "blank"})
    bot.act("init", OWNER, {"start_day": "1"})
    assert not bot.read("features", OWNER)["onboarding"]
    assert bot.read("book_info", OWNER)["transactions"] == 1


def mark_legacy(bot, user=OWNER):
    """舊版（0.3.x）資料轉進來之後，compat 會寫一筆 legacy.v03（這裡直接寫，模擬轉過、但一筆都沒轉到的情況）。"""
    settings = importlib.import_module(f"{bot.module.__name__}.engine.services.settings")
    db = importlib.import_module(f"{bot.module.__name__}.engine.db")
    with bot.module.session.open_book(bot.pack, U(user)) as book:
        with db.write(book.conn, actor=book.actor, source=book.actor, command="legacy") as w:
            settings.put(w, "currency.decimals", 2)
            settings.put(w, "legacy.v03", {"converted": 0, "skipped": 0, "at": "2030-01-15"})


def test_legacy_book_skips_the_seed_step_and_keeps_its_decimals(bot):
    mark_legacy(bot)
    f = bot.read("features", OWNER)
    assert not f["onboarding"] and not f["can_seed"]                     # 舊資料轉進來過：不引導
    assert "onboarding" not in bot.read("home", OWNER)
    with pytest.raises(kit.AppError):
        bot.act("init", OWNER, {"seed": "generic"})
    with pytest.raises(kit.AppError) as no:
        bot.act("init", OWNER, {"currency": "TWD"})                      # 0 位小數：舊資料的金額會差 100 倍
    assert "小數位數" in no.value.message
    r = bot.act("init", OWNER, {"currency": "USD", "start_day": "1"})    # 一樣是 2 位小數：可以
    assert r["currency"]["decimals"] == 2
    with pytest.raises(kit.AppError):
        bot.act("setting", OWNER, {"key": "currency.decimals", "value": "0"})


def test_init_with_two_decimal_currency(bot):
    r = bot.act("init", OWNER, {"currency": "美元"})
    assert r["currency"] == {"code": "USD", "symbol": "$", "decimals": 2, "name": "美元"}
    reply = bot.command("plan", OWNER, "income_expected=1234.56")
    assert "$1,234.56" in reply, reply


def test_bad_inputs_change_nothing(bot):
    for values in ({"currency": "瞎掰幣"}, {"start_day": "40"}, {"seed": "瞎掰"}):
        with pytest.raises(kit.AppError):
            bot.act("init", OWNER, values)
    assert bot.read("features", OWNER)["onboarding"]
    assert "衣服" in names(bot)


def test_book_new_starts_onboarding_again(bot):
    bot.act("init", OWNER, {})
    bot.act("book_new", OWNER, {"confirm": "開新帳本"})
    assert bot.read("features", OWNER)["onboarding"]


def test_typed_init_replies_in_plain_words(bot):
    reply = bot.command("init", OWNER, "currency=TWD start_day=1 seed=通用")
    assert reply.startswith("好了") and "新台幣" in reply and "通用" in reply, reply


def test_onboarding_rule_is_the_same_for_home_and_features(make_money):
    """第一次使用的判斷只有一個（bridge/setup.py needs_onboarding）：全新帳本要引導；做完 init 就不再引導；
    只有別的寫入（例如跳過一格）但沒記過帳也還是要引導。"""
    fresh = make_money()
    assert fresh.read("features", OWNER)["onboarding"] and fresh.read("home", OWNER)["onboarding"]
    assert fresh.read("features", OWNER)["can_seed"]
    fresh.act("bucket_skip", OWNER, {"id": "saving"})
    assert fresh.read("features", OWNER)["onboarding"] and fresh.read("home", OWNER)["onboarding"]
    fresh.act("init", OWNER, {"start_day": "1"})
    assert not fresh.read("features", OWNER)["onboarding"] and "onboarding" not in fresh.read("home", OWNER)
    other = fresh.read("features", OTHER)                               # 另一個人的帳本不受影響
    assert other["onboarding"]
