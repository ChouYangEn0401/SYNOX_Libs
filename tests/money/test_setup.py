"""設定（W5）：三層類別、帳戶、帳本設定、選配功能、開新帳本、整本匯出。測試資料都是假的。"""

import csv
import io
import json
import os

import pytest

from synox.packs import kit

from .conftest import OTHER, OWNER, U


@pytest.fixture
def bot(make_money):
    return make_money()


@pytest.fixture
def screen(bot, monkeypatch):
    """畫面送來的（金額已經是最小單位）。"""
    monkeypatch.setattr(bot.module.session, "actor_of", lambda: "synox:web")
    return bot


def setup_of(bot, user=OWNER):
    return bot.read("setup", user)


def cat(bot, name, user=OWNER):
    for b in setup_of(bot, user)["catalog"]:
        for g in b["groups"]:
            for c in g["categories"]:
                if c["name"] == name:
                    return c
        for c in b["linked"]:
            if c["name"] == name:
                return c
    return None


def engine(bot):
    pkg = bot.module.__name__
    import importlib
    return importlib.import_module(f"{pkg}.engine.services.refdata"), importlib.import_module(f"{pkg}.engine.db")


def record_one(bot, user=OWNER, amount=120):
    """記一筆假的花費（記帳主線是別的包；這裡直接叫引擎）。"""
    import importlib
    ingest = importlib.import_module(f"{bot.module.__name__}.engine.services.ingest")
    with bot.module.session.open_book(bot.pack, U(user)) as book:
        ingest.run(book.conn, {"records": [{"type": "expense", "amount": amount, "date": "2030-01-15", "desc": "雞腿便當",
                                            "category": "午餐", "account": "現金", "merchant": "範例便當店"}]},
                   actor=book.actor, source=book.actor)


# ── 類別三層 ─────────────────────────────────────────────────────────────────────

def test_setup_read_has_both_pages_and_no_folder_paths(bot):
    d = setup_of(bot)
    for key in ("period", "plan", "buckets", "group_names", "catalog", "accounts", "recurring", "caps", "book",
                "settings", "currency", "features", "claimable", "living_groups", "account_kinds"):
        assert key in d, key
    assert d["book"]["encrypted"] is False
    text = json.dumps(d, ensure_ascii=False)
    assert "money" + os.sep not in text and "files" + os.sep not in text and ":\\\\" not in text


def test_group_add_rename_move_and_stop(screen):
    bot = screen
    r = bot.act("group", OWNER, {"name": "寵物", "icon": "🐶"})
    gid = r["group"]["id"]
    assert r["batch_id"] and r["group"]["bucket_id"] == "living"
    r = bot.act("group", OWNER, {"id": str(gid), "name": "毛小孩", "bucket": "fixed"})
    assert r["group"]["name"] == "毛小孩" and r["group"]["bucket_id"] == "fixed"
    r = bot.act("group", OWNER, {"id": str(gid), "active": "no"})
    assert r["group"]["active"] == 0
    # 沒改到東西：batch_id 是 None（畫面就不給復原）
    r = bot.act("group", OWNER, {"id": str(gid), "active": "no"})
    assert r["batch_id"] is None


def test_category_add_rename_stop_and_pin_to_a_bucket(screen):
    bot = screen
    bot.act("bucket", OWNER, {"label": "寵物費"})
    r = bot.act("cat_add", OWNER, {"name": "飼料", "group": "其他", "keywords": "飼料,罐罐", "default_budget": "800"})
    c = r["category"]
    assert c["group_name"] == "其他" and c["keywords"] == ["飼料", "罐罐"] and c["default_budget"] == 800
    r = bot.act("cat_add", OWNER, {"id": str(c["id"]), "name": "寵物飼料", "group": "住", "bucket": "寵物費"})
    assert r["category"]["name"] == "寵物飼料" and r["category"]["bucket_id"].startswith("extra")
    r = bot.act("cat_add", OWNER, {"id": str(c["id"]), "bucket": "-", "keywords": "-", "default_budget": "-"})
    assert r["category"]["bucket_id"] == "fixed" and r["category"]["keywords"] == [] and r["category"]["default_budget"] is None
    r = bot.act("cat_add", OWNER, {"id": str(c["id"]), "active": "no"})
    assert r["category"]["active"] == 0
    assert cat(bot, "寵物飼料") is None                       # 停用的不在設定頁的樹上


def test_living_category_cannot_be_pinned_elsewhere(screen):
    with pytest.raises(kit.AppError) as no:
        screen.act("cat_add", OWNER, {"id": str(cat(screen, "飲料")["id"]), "bucket": "fixed"})
    assert "生活費" in no.value.message


def test_system_category_can_be_renamed_but_not_stopped_or_changed_kind(screen):
    bot = screen
    lunch = cat(bot, "午餐")
    assert lunch["system"]
    r = bot.act("cat_add", OWNER, {"id": str(lunch["id"]), "name": "中餐", "icon": "🍜"})
    assert r["category"]["name"] == "中餐" and r["category"]["system_key"] == "meal.lunch"
    with pytest.raises(kit.AppError) as no:
        bot.act("cat_add", OWNER, {"id": str(lunch["id"]), "active": "no"})
    assert "不能停用" in no.value.message and "meal" not in no.value.message
    with pytest.raises(kit.AppError) as no:
        bot.act("cat_add", OWNER, {"id": str(lunch["id"]), "kind": "收入"})
    assert "不能換種類" in no.value.message


def test_group_with_system_categories_cannot_be_stopped(screen):
    g = next(g for b in setup_of(screen)["catalog"] for g in b["groups"] if g["name"] == "食")
    with pytest.raises(kit.AppError):
        screen.act("group", OWNER, {"id": str(g["id"]), "active": "no"})


def test_typed_cat_add_on_an_existing_name_edits_it_like_0_3_1(bot):
    reply = bot.command("cat_add", OWNER, "午餐 食 keywords=便當,自助餐")
    assert reply.startswith("改好了"), reply
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        refdata, _db = engine(bot)
        c = next(c for c in refdata.categories(book.conn) if c["name"] == "午餐")
    assert "便當" in c["keywords"] and "自助餐" in c["keywords"]


def test_screen_cat_add_on_an_existing_name_is_refused(screen):
    with pytest.raises(kit.AppError) as no:
        screen.act("cat_add", OWNER, {"name": "午餐", "group": "食"})
    assert "已經有" in no.value.message


# ── 帳戶 ─────────────────────────────────────────────────────────────────────────

def test_account_add_edit_and_bind_to_a_bucket(screen):
    bot = screen
    r = bot.act("account", OWNER, {"name": "旅遊基金", "kind": "bank", "bucket": "saving", "floor_amount": "1000",
                                   "aliases": "旅遊,出國"})
    a = r["account"]
    assert a["bucket_id"] == "saving" and a["floor_amount"] == 1000 and a["aliases"] == ["旅遊", "出國"]
    r = bot.act("account", OWNER, {"id": str(a["id"]), "bucket": "-", "aliases": "-", "floor_amount": "-"})
    assert r["account"]["bucket_id"] is None and r["account"]["aliases"] == [] and r["account"]["floor_amount"] is None


def test_credit_card_cannot_bind_a_bucket(screen):
    with pytest.raises(kit.AppError) as no:
        screen.act("account", OWNER, {"name": "範例信用卡", "kind": "credit_card", "bucket": "saving"})
    assert "信用卡不能綁" in no.value.message
    r = screen.act("account", OWNER, {"name": "範例信用卡", "kind": "信用卡", "card_closing_day": "5",
                                      "card_due_day": "20", "pay_from": "銀行帳戶"})
    assert r["account"]["kind"] == "credit_card" and r["account"]["card_due_day"] == 20


def test_default_account_cannot_be_stopped(screen):
    cash = next(a for a in setup_of(screen)["accounts"] if a["name"] == "現金")
    with pytest.raises(kit.AppError) as no:
        screen.act("account", OWNER, {"id": str(cash["id"]), "active": "no"})
    assert "預設付款帳戶" in no.value.message and not any(w in no.value.message for w in ("ledger.db", "LedgerError"))


# ── 帳本設定 ──────────────────────────────────────────────────────────────────────

def test_setting_by_key_or_plain_word_and_clear_back_to_default(bot):
    bot.act("setting", OWNER, {"key": "時差", "value": "8"})
    assert next(s for s in setup_of(bot)["settings"] if s["key"] == "tz_offset")["value"] == 8
    bot.act("setting", OWNER, {"key": "tz_offset", "value": "-"})
    assert next(s for s in setup_of(bot)["settings"] if s["key"] == "tz_offset")["value"] is None
    r = bot.act("setting", OWNER, {"key": "午餐提醒", "value": "一,三,五"})
    assert r["value"] == "0,2,4"
    r = bot.act("setting", OWNER, {"key": "週報", "value": "週日"})
    assert r["value"] == 6
    with pytest.raises(kit.AppError):
        bot.act("setting", OWNER, {"key": "period_start_day", "value": "-"})
    with pytest.raises(kit.AppError):
        bot.act("setting", OWNER, {"key": "default_account", "value": "-"})
    with pytest.raises(kit.AppError):
        bot.act("setting", OWNER, {"key": "沒有這個", "value": "1"})


def test_bench_symbol_may_be_blank(bot):
    """撲滿的基準不預設代號：空白要收（以前會被擋，預設卻是空白）。"""
    bot.act("setting", OWNER, {"key": "bench.symbol", "value": "TEST1"})
    r = bot.act("setting", OWNER, {"key": "bench.symbol", "value": "-"})
    assert r["value"] == ""


def test_currency_decimals_cannot_change_once_there_are_entries(bot):
    record_one(bot)
    with pytest.raises(kit.AppError) as no:
        bot.act("setting", OWNER, {"key": "currency.decimals", "value": "2"})
    assert "新帳本" in no.value.message
    with pytest.raises(kit.AppError):
        bot.act("setting", OWNER, {"key": "幣別", "value": "USD"})
    r = bot.act("setting", OWNER, {"key": "幣別符號", "value": "$"})       # 符號隨時可以改
    assert r["currency"]["symbol"] == "$"


def test_empty_book_may_change_decimals(bot):
    r = bot.act("setting", OWNER, {"key": "currency.decimals", "value": "2"})
    assert r["currency"]["decimals"] == 2
    r = bot.act("setting", OWNER, {"key": "幣別", "value": "JPY"})          # 認得的幣別：符號、小數位數一起換
    assert r["currency"] == {"code": "JPY", "symbol": "¥", "decimals": 0, "name": "日圓"}


def test_settings_are_per_person(bot):
    bot.act("setting", OWNER, {"key": "currency.symbol", "value": "$"})
    assert setup_of(bot, OTHER)["currency"]["symbol"] == "NT$"


# ── 選配功能 ──────────────────────────────────────────────────────────────────────

def test_feature_on_twice_adds_nothing_the_second_time(bot):
    r = bot.act("feature_on", OWNER, {"name": "捐贈"})
    assert r["changed"] and r["batch_id"] and "捐款" in r["added_categories"]
    count = len(setup_of(bot)["claimable"])
    r = bot.act("feature_on", OWNER, {"name": "giving"})
    assert not r["changed"] and r["batch_id"] is None
    assert len(setup_of(bot)["claimable"]) == count
    assert next(f for f in bot.read("features", OWNER)["features"] if f["name"] == "giving")["enabled"]
    assert not next(f for f in bot.read("features", OTHER)["features"] if f["name"] == "giving")["enabled"]


def test_unknown_feature_is_refused_with_suggestions(bot):
    reply = bot.command("feature_on", OWNER, "瞎掰")
    assert reply.startswith("❌") and "是不是" in reply


# ── 帳本：開新的、整本匯出 ──────────────────────────────────────────────────────────

def test_book_new_archives_the_old_one_and_starts_empty(bot):
    record_one(bot)
    assert bot.read("book_info", OWNER)["transactions"] == 1
    with pytest.raises(kit.AppError):
        bot.act("book_new", OWNER, {"confirm": "好"})
    r = bot.act("book_new", OWNER, {"confirm": "開新帳本"})
    assert r["transactions"] == 0 and r["archived"].endswith(".db") and os.sep not in r["archived"]
    info = bot.read("book_info", OWNER)
    assert info["transactions"] == 0 and any(a["name"] == r["archived"] for a in info["archives"])
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        archive = os.path.join(os.path.dirname(book.path("x")), "book", "archive", r["archived"])
    assert os.path.isfile(archive)
    assert bot.read("book_info", OTHER)["archives"] == []


def test_book_new_has_a_confirm_sentence(bot):
    record_one(bot)
    text = bot.pack.app_describe["book_new"](U(OWNER), {"confirm": "開新帳本"})
    assert "1 筆" in text and "封存" in text


def test_book_export_is_a_csv_of_every_table(bot):
    record_one(bot)
    dl = bot.pack.app_exports["book"](U(OWNER), {})
    assert dl.content_type == "text/csv; charset=utf-8" and dl.filename.endswith(".csv")
    rows = list(csv.reader(io.StringIO(dl.data.decode("utf-8-sig"))))
    assert rows[0] == ["table", "data"]
    tables = {r[0] for r in rows[1:]}
    assert {"transactions", "categories", "accounts", "settings", "_schema"} <= tables
    txn = json.loads(next(r[1] for r in rows[1:] if r[0] == "transactions"))
    assert txn["amount"] == 120 and txn["merchant"] == "範例便當店"
