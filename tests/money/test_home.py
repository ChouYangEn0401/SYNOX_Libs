"""總覽（bridge/home.py，W4）：讀（home、year、allowance、conflicts、moves）、指令（summary、check、envelope）、
動作（bucket_skip、bucket_setaside、bucket_fold、move、fixed_post、envelope_do）。

資料一律是假的（「範例便當店」「現金」「120」）；「今天」是 conftest 的 TODAY（2030-01-15），每期從 1 號開始。
帳本裡的起始資料要準備時，直接用引擎（在 open_book 裡），不靠別的工作包的指令。
"""

import importlib

import pytest

from synox.guard import origin
from synox.packs import kit

from .conftest import OTHER, OWNER

PERIOD = "2030-01"


def eng(bot, name):
    return importlib.import_module(f"{bot.module.__name__}.engine.{name}")


def prepare(bot, fn, user=OWNER):
    """在這個人的帳本裡用引擎寫一批（測試準備資料用）。"""
    db = eng(bot, "db")
    with bot.module.session.open_book(bot.pack, user) as book:
        with db.write(book.conn, actor="test", source="test") as w:
            return fn(w)


def record(bot, *records, user=OWNER):
    ingest = eng(bot, "services.ingest")
    base = {"type": "expense", "account": "現金", "date": "2030-01-14"}
    with bot.module.session.open_book(bot.pack, user) as book:
        return ingest.run(book.conn, {"records": [{**base, **r} for r in records]}, actor="test", source="test")


def with_plan(bot, user=OWNER, *, extra=None):
    """收入 40000：固定支出 10000、存錢 5000；生活費＝食 6200（一期 31 天，每天 200）。"""
    plans = eng(bot, "services.plans")

    def go(w):
        buckets = {"fixed": 10000, "saving": 5000}
        if extra:
            b = plans.add_bucket(w, extra)
            buckets[b["id"]] = 3000
        plans.set_plan(w, PERIOD, income_expected=40000, buckets=buckets, group_budgets={"食": 6200})
    prepare(bot, go, user)


def row(bot, bucket, user=OWNER, period=PERIOD):
    home = bot.read("home", user, {"period": period})
    return next(e for e in home["envelopes"] if e["id"] == bucket or e["name"] == bucket)


# ── 新帳本：全空也不壞 ─────────────────────────────────────────────────────────────

def test_a_new_book_overview_works_and_asks_for_onboarding(make_money):
    bot = make_money()
    home = bot.read("home", OWNER)
    assert home["onboarding"] is True                                   # 一次都沒寫過：畫面叫 onboard.js
    assert home["period"]["key"] == PERIOD
    assert home["living"]["spent"] == 0 and home["start"]["has_transactions"] is False
    assert {"envelopes", "calendar", "wallet", "conflicts", "moves", "health"} <= set(home)
    year = bot.read("year", OWNER)
    assert year["year"] == 2030 and len(year["months"]) == 12
    assert bot.read("allowance", OWNER)["items"][0]["key"] == "plan"    # 還沒設薪水分配
    assert bot.read("conflicts", OWNER)["count"] == 0
    assert bot.read("moves", OWNER)["items"] == []
    for name in ("summary", "check", "envelope"):
        reply = bot.command(name, OWNER)
        assert reply and not reply.startswith("❌"), (name, reply)
    assert "還沒分" in bot.command("envelope", OWNER)


def test_onboarding_goes_away_after_the_first_write(make_money):
    bot = make_money()
    record(bot, {"amount": 120, "category": "午餐", "desc": "範例便當店"})
    assert "onboarding" not in bot.read("home", OWNER)


def test_bad_period_and_year_are_plain_words(make_money):
    bot = make_money()
    with pytest.raises(kit.AppError) as no:
        bot.read("home", OWNER, {"period": "一月"})
    assert "看不懂" in no.value.message
    with pytest.raises(kit.AppError):
        bot.read("year", OWNER, {"year": "明年"})
    assert bot.command("summary", OWNER, "1月").startswith("❌")


# ── 還能花多少、每天可花：跟引擎的 stats／estimate 一致 ─────────────────────────────────────

def test_numbers_match_the_engine(make_money):
    bot = make_money()
    with_plan(bot)
    record(bot, {"amount": 120, "category": "午餐", "desc": "範例便當店"},
           {"amount": 80, "category": "早餐", "desc": "範例早餐店", "date": "2030-01-15"})
    stats = eng(bot, "services.stats")
    estimate = eng(bot, "services.estimate")
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        s = stats.period_summary(book.conn, PERIOD)
        guess = estimate.for_period(book.conn, PERIOD)
        text = stats.text_summary(s)
    home = bot.read("home", OWNER)
    lv = s["living"]
    assert (home["living"]["budget"], home["living"]["spent"], home["living"]["left"]) == (6200, 200, 6000)
    assert home["living"]["left"] == lv["remaining"] and home["living"]["per_day_left"] == lv["per_day_left"]
    assert home["calendar"]["daily_budget"] == lv["daily_budget"] == 200
    assert home["calendar"]["estimate"] == guess
    reply = bot.command("summary", OWNER)
    assert text in reply
    assert f"剩下的 {lv['days_left']} 天每天 {lv['per_day_left']:,}" in reply
    assert "食 200/6,200" in reply


def test_check_lists_overspending_in_plain_words(make_money):
    bot = make_money()
    with_plan(bot)
    record(bot, {"amount": 7000, "category": "午餐", "desc": "範例便當店"})
    reply = bot.command("check", OWNER)
    assert "要處理：生活費超支 800" in reply
    items = bot.read("allowance", OWNER)
    assert items["ok"] is False and items["items"][0]["key"] == "living:over"


# ── 錢怎麼分：跳過、撥額度、併入、今天處理、一鍵記固定項目 ─────────────────────────────────

def test_skip_and_undo_two_ways(make_money):
    bot = make_money()
    with_plan(bot, extra="旅遊基金")
    data = bot.act("bucket_skip", OWNER, {"id": "extra1"})
    assert data["skipped"] is True and data["batch_id"] and data["write_seq"]
    assert row(bot, "extra1")["state"] == "skipped"
    again = bot.act("bucket_skip", OWNER, {"id": "extra1", "undo": "no"})
    assert again["changed"] is False and again["batch_id"] is None      # 沒改到東西：畫面不給復原
    reply = bot.command("bucket_skip", OWNER, "旅遊基金 undo=yes")      # 打字的路：用名字也行
    assert "取消跳過" in reply
    assert row(bot, "extra1")["state"] == "todo"


def test_living_cannot_be_skipped(make_money):
    bot = make_money()
    with_plan(bot)
    with pytest.raises(kit.AppError) as no:
        bot.act("bucket_skip", OWNER, {"id": "living"})
    assert "生活費不能跳過" in no.value.message
    with pytest.raises(kit.AppError) as no:
        bot.act("bucket_skip", OWNER, {"id": "沒有這格"})
    assert "沒有「沒有這格」這一格" in no.value.message


def rolling(bot):
    plans = eng(bot, "services.plans")
    with_plan(bot, extra="旅遊基金")
    prepare(bot, lambda w: plans.set_bucket(w, "extra1", rollover=True, rollover_from="2029-12"))


def test_set_aside_moves_money_out_and_undo_takes_it_back(make_money):
    bot = make_money()
    rolling(bot)
    assert row(bot, "extra1")["can_set_aside"] is True
    data = bot.act("bucket_setaside", OWNER, {"id": "extra1", "amount": "3000", "to": "銀行帳戶"})
    assert data["set_aside"]["amount"] == 3000 and data["set_aside"]["dest"] == "銀行帳戶" and data["batch_id"]
    e = row(bot, "extra1")
    assert e["set_aside"]["amount"] == 3000 and e["can_set_aside"] is False
    with pytest.raises(kit.AppError) as no:                             # 去向一定要說
        bot.act("bucket_setaside", OWNER, {"id": "fixed", "amount": "100"})
    assert "沒有開滾存" in no.value.message
    reply = bot.command("bucket_setaside", OWNER, "旅遊基金 undo=yes")
    assert "拿掉了" in reply
    assert row(bot, "extra1")["set_aside"] is None


def test_rollover_carry_comes_from_last_period(make_money):
    bot = make_money()
    rolling(bot)
    plans = eng(bot, "services.plans")

    def last_month(w):
        plans.set_plan(w, "2029-12", buckets={"extra1": 3000})
        plans.set_bucket_categories(w, "extra1", ["旅遊"])
    prepare(bot, last_month)
    record(bot, {"amount": 1000, "category": "旅遊", "desc": "範例車票", "date": "2029-12-10"})
    e = row(bot, "extra1")
    assert (e["carry"], e["available"], e["state"]) == (2000, 5000, "rolling")
    assert "旅遊基金（extra1）0 / 5,000・留著等帳單" in bot.command("envelope", OWNER)


def test_fold_turns_a_bucket_into_a_fixed_item(make_money):
    bot = make_money()
    with_plan(bot, extra="手機費")
    data = bot.act("bucket_fold", OWNER, {"id": "extra1", "into": "fixed"})
    assert data["item"]["name"] == "手機費" and data["added_to_parent"] is True and data["batch_id"]
    assert row(bot, "fixed")["plan"] == 13000
    assert [it["name"] for it in row(bot, "fixed")["items"]] == ["手機費"]
    assert "extra1" not in [e["id"] for e in bot.read("home", OWNER)["envelopes"]]


def test_envelope_do_records_the_prefilled_one(make_money):
    bot = make_money()
    with_plan(bot)
    with pytest.raises(kit.AppError) as no:                             # 固定支出底下好幾個細項：要說記哪一個
        bot.act("envelope_do", OWNER, {"id": "fixed"})
    assert "還沒指定記哪個類別" in no.value.message
    data = bot.act("envelope_do", OWNER, {"id": "fixed", "amount": "8000", "category": "房租", "date": "今天"})
    assert data["record"]["amount"] == 8000 and data["record"]["date"] == "2030-01-15" and data["batch_id"]
    e = row(bot, "fixed")
    assert (e["used"], e["left"], e["state"]) == (8000, 2000, "todo")
    reply = bot.command("envelope_do", OWNER, "固定支出 category=電費 date=1/14")   # 不給金額＝照預填的（還差多少）
    assert reply.startswith("記下：2030-01-14 固定支出 2,000")
    assert row(bot, "fixed")["state"] == "done"


def test_fixed_post_twice_does_not_double(make_money):
    bot = make_money()
    with_plan(bot)
    recurring = eng(bot, "services.recurring")
    prepare(bot, lambda w: recurring.add(w, name="房租", kind="expense", amount=8000, day_of_month=5, category="房租",
                                         bucket="fixed"))
    first = bot.act("fixed_post", OWNER, {})
    assert [c["name"] for c in first["created"]] == ["房租"] and first["batch_id"]
    second = bot.act("fixed_post", OWNER, {})
    assert second["created"] == [] and second["batch_id"] is None
    assert "沒有要記的固定項目" in bot.command("fixed_post", OWNER)
    assert row(bot, "fixed")["used"] == 8000


# ── 挪用 ──────────────────────────────────────────────────────────────────────────

def test_move_and_undo(make_money):
    bot = make_money()
    with_plan(bot)
    data = bot.act("move", OWNER, {"from_bucket": "saving", "to_bucket": "living", "amount": "500", "note": "這期聚餐多",
                                   "period": PERIOD})
    m = data["move"]
    assert (m["from"], m["to"], m["amount"]) == ("saving", "living", 500) and data["warnings"] == [] and data["batch_id"]
    home = bot.read("home", OWNER)
    assert home["living"]["budget"] == 6700 and row(bot, "saving")["moved"] == -500
    listed = bot.read("moves", OWNER)
    assert listed["stats"]["count"] == 1 and listed["items"][0]["note"] == "這期聚餐多"
    undone = bot.act("move", OWNER, {"undo": str(m["id"])})
    assert undone["move"]["undone"] is True and undone["batch_id"]
    assert bot.read("moves", OWNER)["items"] == [] and bot.read("home", OWNER)["living"]["budget"] == 6200


def test_move_over_what_is_left_still_goes_with_a_warning(make_money):
    bot = make_money()
    with_plan(bot)
    reply = bot.command("move", OWNER, "存錢 生活費 6000")
    assert "存錢 → 生活費 挪了 6,000" in reply and "⚠️" in reply
    with pytest.raises(kit.AppError) as no:
        bot.act("move", OWNER, {"from_bucket": "saving", "to_bucket": "saving", "amount": "10"})
    assert "要不一樣" in no.value.message
    with pytest.raises(kit.AppError):
        bot.act("move", OWNER, {"from_bucket": "saving", "to_bucket": "living"})            # 沒說挪多少


def test_typed_and_screen_moves_end_the_same(make_money):
    a, b = make_money(), make_money()
    for bot in (a, b):
        with_plan(bot)
    a.command("move", OWNER, "存錢 生活費 300 note=範例")
    b.act("move", OWNER, {"from_bucket": "saving", "to_bucket": "living", "amount": "300", "note": "範例"})
    strip = lambda d: [{k: v for k, v in m.items() if k not in ("at", "date")} for m in d["items"]]   # noqa: E731
    assert strip(a.read("moves", OWNER)) == strip(b.read("moves", OWNER))
    assert a.read("home", OWNER)["living"] == b.read("home", OWNER)["living"]


# ── 待解決：只讀 ───────────────────────────────────────────────────────────────────

def test_reading_never_writes(make_money):
    bot = make_money()
    with_plan(bot)
    record(bot, {"amount": 120, "category": "午餐", "desc": "範例便當店"},
           {"amount": 90, "desc": "沒分類的東西"},
           {"type": "income", "amount": 3000, "category": "兼職", "account": "銀行帳戶", "desc": "範例打工"},
           {"type": "income", "amount": 3000, "category": "兼職", "account": "銀行帳戶", "desc": "範例打工"})   # 同一天收兩次
    db = eng(bot, "db")

    def state():
        with bot.module.session.open_book(bot.pack, OWNER) as book:
            return (db.write_seq(book.conn), book.conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0],
                    book.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0])
    before = state()
    cf = bot.read("conflicts", OWNER)
    assert cf["count"] >= 1
    for name in ("home", "year", "allowance", "conflicts", "moves"):
        bot.read(name, OWNER)
    for name in ("summary", "check", "envelope"):
        bot.command(name, OWNER)
    assert state() == before


# ── 兩個人互不相干 ─────────────────────────────────────────────────────────────────

def test_two_people_do_not_see_each_other(make_money):
    bot = make_money()
    with_plan(bot, OWNER)
    with_plan(bot, OTHER)
    bot.act("move", OWNER, {"from_bucket": "saving", "to_bucket": "living", "amount": "500"})
    record(bot, {"amount": 120, "category": "午餐", "desc": "範例便當店"}, user=OWNER)
    assert bot.read("moves", OTHER)["items"] == []
    other = bot.read("home", OTHER)
    assert other["living"]["budget"] == 6200 and other["living"]["spent"] == 0
    with pytest.raises(kit.AppError):                                     # 別人的挪用編號在自己的帳本裡找不到
        bot.act("move", OTHER, {"undo": str(bot.read("moves", OWNER)["items"][0]["id"])})
    assert len(bot.read("moves", OWNER)["items"]) == 1


# ── 有小數的幣別 ──────────────────────────────────────────────────────────────────

def test_amounts_follow_the_book_decimals(make_money):
    bot = make_money()
    with_plan(bot)
    settings = eng(bot, "services.settings")
    prepare(bot, lambda w: settings.put(w, "currency.decimals", 2))
    reply = bot.command("move", OWNER, "存錢 生活費 12.5")                # 人打字：照小數位數轉（12.50 → 1250）
    assert "挪了 12.50" in reply
    with origin.using(origin.Origin("web", OWNER)):                       # 畫面送來的已經是最小單位
        bot.act("move", OWNER, {"from_bucket": "saving", "to_bucket": "living", "amount": "1250"})
    assert [m["amount"] for m in bot.read("moves", OWNER)["items"]] == [1250, 1250]
    with pytest.raises(kit.AppError):                                     # 小數比帳本的多
        with origin.using(origin.Origin("web", OWNER)):
            bot.act("move", OWNER, {"from_bucket": "saving", "to_bucket": "living", "amount": "12.345"})
