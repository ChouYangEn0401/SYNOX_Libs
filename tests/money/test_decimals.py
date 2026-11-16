"""金額小數位數、「今天」的時差、小數帳本先不開撲滿（W9 整合收尾）。

- 金額在帳本裡一律是最小單位的整數。人打字（指令；網頁主控台打字也算）照小數位數換算，畫面送來的已經是最小單位
  （pack.py 畫面那一層開帳本時帶 screen=True；bridge/table.py 的 to_minor）。0 位小數的帳本行為完全不變。
- 回覆的字（記帳、規劃、報表、週報、檢討摘要）照小數位數印。
- dom.js 的 num／toMinor／major（tests/money/js/money_format_check.mjs）。
- 帳本設了 tz_offset：「今天」＝UTC 現在＋時差。
資料一律是假的（「範例便當店」「現金」「120」）。
"""

import importlib
import os
from datetime import date, datetime, timezone

import pytest

from synox.packs import kit

from .conftest import MONEY, NODE, OTHER, OWNER, U, run_node_check


def eng(bot, name):
    return importlib.import_module(f"{bot.module.__name__}.engine.{name}")


def amounts(bot, user=OWNER):
    with bot.module.session.open_book(bot.pack, U(user)) as book:
        return [r[0] for r in book.conn.execute("SELECT amount FROM transactions ORDER BY id")]


def last_id(bot, user=OWNER):
    with bot.module.session.open_book(bot.pack, U(user)) as book:
        return book.conn.execute("SELECT id FROM transactions ORDER BY rowid DESC LIMIT 1").fetchone()[0]


def refused(fn, *args):
    with pytest.raises(kit.AppError) as e:
        fn(*args)
    return e.value


@pytest.fixture
def cents(make_money):
    """小數位數 2 的帳本（第一次使用選美元）。"""
    bot = make_money()
    r = bot.act("init", OWNER, {"currency": "USD"})
    assert r["currency"]["decimals"] == 2
    return bot


# ── 0 位小數：跟以前完全一樣 ────────────────────────────────────────────────────────

def test_whole_currency_is_unchanged_on_both_paths(make_money):
    bot = make_money()
    assert "120" in bot.command("add", OWNER, "120 午餐 範例便當店")
    bot.act("add", OWNER, {"amount": "95", "desc": "範例便當店"})
    assert amounts(bot) == [120, 95]
    assert "小數" in bot.command("add", OWNER, "99.5 午餐")
    assert "小數" in refused(bot.act, "add", OWNER, {"amount": "99.5"}).message
    assert amounts(bot) == [120, 95]


# ── 2 位小數：打字的路 ─────────────────────────────────────────────────────────────

def test_typed_amounts_follow_two_decimals(cents):
    reply = cents.command("add", OWNER, "99.5 午餐 範例便當店")
    assert "99.50" in reply
    cents.command("add", OWNER, "120 晚餐")
    assert amounts(cents) == [9950, 12000]
    assert "最多 2 位小數" in cents.command("add", OWNER, "1.234 午餐")
    reply = cents.command("plan", OWNER, "income_expected=400.5")
    assert "400.50" in reply


def test_web_console_typing_still_counts_as_typed(cents, monkeypatch):
    """網頁主控台打字的 caller 也是 web：一樣照小數位數換算（只看呼叫的那一層，不看 caller 的種類）。"""
    monkeypatch.setattr(cents.module.session, "actor_of", lambda: "synox:web")
    cents.command("add", OWNER, "99.5 午餐")
    assert amounts(cents) == [9950]


# ── 2 位小數：畫面的路 ─────────────────────────────────────────────────────────────

def test_screen_sends_smallest_units(cents):
    r = cents.act("add", OWNER, {"amount": "9950", "desc": "範例便當店"})
    assert r["batch_id"]
    assert amounts(cents) == [9950]
    no = refused(cents.act, "add", OWNER, {"amount": "99.5"})
    assert "最小單位" in no.message
    cents.act("edit", OWNER, {"id": str(last_id(cents)), "amount": "12050"})
    assert amounts(cents) == [12050]
    r = cents.act("plan", OWNER, {"income_expected": "4000050"})
    assert r["plan"]["income_expected"] == 4000050


def test_typed_and_screen_paths_land_on_the_same_number(cents):
    cents.command("add", OWNER, "1,234.56 午餐")
    cents.act("add", OWNER, {"amount": "123456", "desc": "範例便當店"})
    a, b = amounts(cents)
    assert a == b == 123456


def test_passbook_and_meta_tell_the_screen_the_decimals(cents):
    cents.command("add", OWNER, "99.5 午餐")
    meta = cents.read("meta", OWNER)
    assert meta["currency"]["decimals"] == 2
    items = cents.read("passbook", OWNER)["items"]
    assert items[0]["raw"] == 9950                       # 畫面拿到最小單位，自己照 decimals 換成 99.50


# ── 回覆的字：報表、週報、檢討摘要 ───────────────────────────────────────────────────

def test_report_text_prints_decimals(cents):
    cents.command("add", OWNER, "99.5 午餐")
    reply = cents.command("report", OWNER, "")
    assert "99.50" in reply and "9,950" not in reply


def test_engine_money_format():
    from importlib import import_module

    from .conftest import load_money
    report = import_module(load_money().__name__ + ".engine.services.report")
    assert report.money(1234567) == "1,234,567" and report.money(-5, signed=True) == "-5"
    assert report.money(5, signed=True) == "+5"
    assert report.money(9950, 2) == "99.50" and report.money(-123456, 2) == "-1,234.56"
    assert report.money(250, 2, signed=True) == "+2.50"


def test_week_report_prints_decimals(cents):
    cents.command("add", OWNER, "99.5 午餐")
    notify = eng(cents, "services.notify")
    with cents.module.session.open_book(cents.pack, U(OWNER)) as book:
        _key, text = notify.week_text(book.conn, book.today)
    assert "99.50" in text


# ── dom.js（純 node）──────────────────────────────────────────────────────────────

@pytest.mark.skipif(NODE is None, reason="這台電腦沒有 node")
def test_dom_money_helpers():
    results = run_node_check("money_format_check.mjs", os.path.join(MONEY, "app", "js", "dom.js"))
    failed = [f"{label}：{why}" for label, why in results if why]
    assert len(results) >= 12 and not failed, failed


# ── 撲滿：小數帳本先不開放 ───────────────────────────────────────────────────────────

def securities(bot):
    db, refdata = eng(bot, "db"), eng(bot, "services.refdata")
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        with db.write(book.conn, actor="test", source="test") as w:
            refdata.add_account(w, name="範例證券", kind="securities", asset_class="invest")


def test_piggy_is_hidden_and_refused_on_a_decimal_book(cents):
    securities(cents)
    assert cents.read("meta", OWNER)["piggy"] is False
    for name, values in (("piggy_set", {"bench_rate": "10%"}),
                         ("trade_add", {"date": "2030-01-10", "action": "buy", "level": "invest", "symbol": "TEST1",
                                        "shares": "10", "price": "100"})):
        assert "撲滿目前只支援整數幣別" in refused(cents.act, name, OWNER, values).message
    assert "撲滿目前只支援整數幣別" in refused(cents.read, "piggy", OWNER, {}).message
    assert "撲滿目前只支援整數幣別" in cents.command("piggy_set", OWNER, "bench_rate=10%")
    cents.read("reconcile", OWNER)                        # 對明細卡（明細頁）照常


def test_piggy_still_works_on_a_whole_currency_book(make_money):
    bot = make_money()
    securities(bot)
    assert bot.read("meta", OWNER)["piggy"] is True
    assert bot.read("piggy", OWNER)["has_layers"] is True


# ── 「今天」照帳本的時差 ─────────────────────────────────────────────────────────────

def set_tz(bot, hours, user=OWNER):
    db, settings = eng(bot, "db"), eng(bot, "services.settings")
    with bot.module.session.open_book(bot.pack, U(user)) as book:
        with db.write(book.conn, actor="test", source="test") as w:
            settings.put(w, "tz_offset", hours)


def today_of(bot, user=OWNER):
    with bot.module.session.open_book(bot.pack, U(user)) as book:
        return book.today, eng(bot, "clock").today()


def test_today_uses_utc_plus_the_book_offset(make_money, monkeypatch):
    bot = make_money()
    session = bot.module.session
    monkeypatch.setattr(session, "utc_now", lambda: datetime(2030, 1, 15, 20, 0, tzinfo=timezone.utc))
    assert today_of(bot) == (date(2030, 1, 15), date(2030, 1, 15))          # 沒設時差：跟著這台電腦（now()）
    set_tz(bot, 8)
    assert today_of(bot) == (date(2030, 1, 16), date(2030, 1, 16))          # UTC 20:00＋8＝隔天 04:00
    set_tz(bot, -5)
    assert today_of(bot) == (date(2030, 1, 15), date(2030, 1, 15))
    assert today_of(bot, OTHER)[0] == date(2030, 1, 15)                       # 另一個人的帳本不受影響
    set_tz(bot, 8)
    bot.command("add", OWNER, "120 午餐 date=今天")
    with session.open_book(bot.pack, U(OWNER)) as book:
        assert book.conn.execute("SELECT date FROM transactions").fetchone()[0] == "2030-01-16"


def test_bad_offset_falls_back_to_this_computer(make_money, monkeypatch):
    bot = make_money()
    monkeypatch.setattr(bot.module.session, "utc_now", lambda: datetime(2030, 1, 15, 20, 0, tzinfo=timezone.utc))
    set_tz(bot, 99)
    assert today_of(bot)[0] == date(2030, 1, 15)
