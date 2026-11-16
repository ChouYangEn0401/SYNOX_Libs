"""0.3.1 的舊資料第一次打開就自動轉進新帳本（W6，bridge/legacy.py）。

舊資料在 pack.store.of(user)（測試用 conftest 的 FakeStore；真的 SYNOX 是保管箱）。轉換：一批寫入、只做一次、
舊資料另存到 legacy-0.3 鍵（不刪）、原本那一份換成「已經搬走」的記號。資料一律是假的。
"""

import json
from datetime import datetime

import pytest

from .conftest import OTHER, OWNER, U
from .test_compat import eng, query, record, setting, summary, view

LEGACY_ROWS = "SELECT * FROM transactions WHERE source = 'legacy' ORDER BY source_ref"


def ts(y, m, d, h=12):
    return int(datetime(y, m, d, h).timestamp())


def schema2():
    return {"schema": 2, "next_id": 7, "budget": 1000,
            "entries": [
                {"id": 1, "kind": "expense", "amount": 120.0, "category": "午餐", "memo": "範例便當店",
                 "date": "2030-01-03", "at": ts(2030, 1, 3), "account": "現金"},
                {"id": 2, "kind": "expense", "amount": 99.0, "category": "寵物", "memo": "",
                 "date": "2029-12-20", "at": ts(2029, 12, 20), "account": "範例行動支付"},
                {"id": 3, "kind": "income", "amount": 30000.0, "category": "薪資", "memo": "", "date": "2030-01-05",
                 "at": ts(2030, 1, 5), "account": "範例銀行帳戶"},
                {"id": 5, "kind": "transfer", "amount": 5000.0, "category": "存錢", "memo": "每月存", "date": "2030-01-06",
                 "at": ts(2030, 1, 6)},
                {"id": 6, "kind": "expense", "amount": 45.0, "category": "待分類", "memo": "不知道", "date": "2030-01-07",
                 "at": ts(2030, 1, 7), "account": "範例信用卡"},
            ],
            "categories": [
                {"name": "午餐", "group": "食", "kind": "expense", "keywords": ["便當", "範例關鍵字"]},
                {"name": "咖啡", "group": "飲品", "kind": "expense", "keywords": ["拿鐵"], "cap": 300},
                {"name": "存錢", "group": "存錢與投資", "kind": "transfer", "keywords": ["定存"]},
                {"name": "薪資", "group": "收入", "kind": "income", "keywords": ["薪水"]},
            ]}


def schema1():
    return {"entries": [{"amount": 50.0, "category": "咖啡", "memo": "", "at": ts(2030, 1, 2)},
                        {"amount": 80.0, "category": "午餐", "memo": "假資料", "at": ts(2030, 1, 3)}],
            "budget": 1000}


def put_old(bot, uid, data):
    bot.pack.store.of(uid).save(json.loads(json.dumps(data)))


def store_of(bot, uid, key=None):
    return bot.pack.store.of(uid).load(None, key)


def test_schema_2_moves_over_on_first_open(make_money):
    bot = make_money()
    old = schema2()
    put_old(bot, OWNER, old)
    out = bot.command("month", OWNER)
    assert "2030-01 支出共 165 元" in out and "午餐：120 元" in out and "待分類：45 元" in out
    assert "收入 共 30,000 元" in out and "轉帳（不算花費） 共 5,000 元" in out
    assert "生活費預算 1,000 元" in out
    rows = query(bot, OWNER, LEGACY_ROWS)
    assert [r["source_ref"] for r in rows] == [f"legacy-0.3:{n}" for n in (1, 2, 3, 5, 6)]
    assert len({r["batch_id"] for r in rows}) == 1                               # 一批：可以整批撤銷
    lunch = rows[0]
    assert lunch["description"] == "範例便當店" and lunch["amount"] == 120 and lunch["date"] == "2030-01-03"
    assert "2029-12 支出共 99 元" in bot.command("month", OWNER, "2029-12")
    # 類別：起始類別補關鍵字、舊的類別建起來、上限搬成細項預算、隨手打的類別也建
    cats = {r["name"]: r for r in query(bot, OWNER, "SELECT c.*, g.name AS g FROM categories c "
                                                   "JOIN category_groups g ON g.id = c.group_id")}
    assert "範例關鍵字" in cats["午餐"]["keywords"].split(",") and cats["午餐"]["g"] == "食"
    assert cats["咖啡"]["g"] == "飲品" and cats["咖啡"]["default_budget"] == 300 and cats["咖啡"]["kind"] == "expense"
    assert cats["存錢"]["kind"] == "transfer" and cats["存錢"]["transfer_purpose"] == "saving"
    assert cats["寵物"]["g"] == "其他"
    assert "定存" in cats["存錢"]["keywords"] and "薪水" in cats["薪資"]["keywords"]
    # 帳戶：付款方式 → 帳戶（種類照名字猜，猜不到是電子錢包）；轉帳轉到「轉出（舊版）」
    accounts = {r["name"]: r for r in query(bot, OWNER, "SELECT * FROM accounts")}
    assert accounts["範例行動支付"]["kind"] == "e_wallet" and accounts["範例銀行帳戶"]["kind"] == "bank"
    assert accounts["範例信用卡"]["kind"] == "credit_card"
    out_acc = accounts["轉出（舊版）"]
    assert out_acc["liquid"] == 0
    saving = rows[3]
    assert saving["kind"] == "transfer" and saving["to_account_id"] == out_acc["id"] and saving["purpose"] == "saving"
    assert saving["account_id"] == accounts["現金"]["id"]                       # 沒寫付款方式：預設帳戶
    # 舊資料：另存一份、原本那一份換成記號；設定裡記一筆
    assert store_of(bot, OWNER, "legacy-0.3") == old
    assert store_of(bot, OWNER)["schema"] == "moved"
    done = setting(bot, OWNER, "legacy.v03")
    assert done["entries"] == 5 and done["skipped"] == 0


def test_converting_happens_once(make_money):
    bot = make_money()
    put_old(bot, OWNER, schema2())
    bot.command("month", OWNER)
    bot.command("cats", OWNER)
    put_old(bot, OWNER, schema2())                     # 就算原本那一份又出現（例如從備份還原），帳本已經在用：不再轉
    bot.command("month", OWNER)
    assert len(query(bot, OWNER, LEGACY_ROWS)) == 5
    assert len(query(bot, OWNER, "SELECT id FROM batches WHERE source = 'legacy'")) == 1


def test_schema_1_gets_numbers_dates_and_kinds(make_money):
    bot = make_money()
    put_old(bot, OWNER, schema1())
    assert "支出共 130 元" in bot.command("month", OWNER)
    rows = query(bot, OWNER, LEGACY_ROWS)
    assert [(r["source_ref"], r["date"], r["kind"]) for r in rows] == [
        ("legacy-0.3:1", "2030-01-02", "expense"), ("legacy-0.3:2", "2030-01-03", "expense")]
    assert rows[1]["description"] == "假資料"
    assert view(bot, OWNER)["budget"] == 1000
    assert store_of(bot, OWNER, "legacy-0.3") == schema1()


def test_cents_switch_the_book_to_two_decimals(make_money):
    bot = make_money()
    put_old(bot, OWNER, {"schema": 2, "next_id": 3, "budget": None, "categories": [],
                         "entries": [{"id": 1, "kind": "expense", "amount": 55.5, "category": "飲料", "memo": "",
                                      "date": "2030-01-02", "at": ts(2030, 1, 2)},
                                     {"id": 2, "kind": "expense", "amount": 10, "category": "飲料", "memo": "",
                                      "date": "2030-01-03", "at": ts(2030, 1, 3)}]})
    out = bot.command("month", OWNER)
    assert "支出共 65.50 元" in out
    assert [r["amount"] for r in query(bot, OWNER, LEGACY_ROWS)] == [5550, 1000]
    assert setting(bot, OWNER, "currency.decimals") == 2
    assert summary(bot, OWNER)["total"] == 65.5


def test_bad_rows_are_skipped_not_fatal(make_money):
    bot = make_money()
    data = schema1()
    data["entries"].append({"id": 9, "kind": "expense", "amount": 10, "category": "午餐", "date": "2031-06-01", "memo": ""})
    data["entries"].append({"id": 10, "kind": "expense", "amount": "abc", "category": "午餐", "date": "2030-01-02"})
    put_old(bot, OWNER, data)
    assert "支出共 130 元" in bot.command("month", OWNER)
    done = setting(bot, OWNER, "legacy.v03")
    assert done["entries"] == 2 and done["skipped"] == 2
    assert store_of(bot, OWNER, "legacy-0.3")["entries"][-1]["id"] == 10       # 轉不過去的還在另存的那一份


def test_each_user_converts_their_own(make_money):
    bot = make_money(per_user=True)
    a, b = schema2(), schema1()
    a["entries"][0]["memo"] = "祕密備註A"
    put_old(bot, OWNER, a)
    put_old(bot, OTHER, b)
    bot.command("month", OTHER)
    assert [r["description"] for r in query(bot, OTHER, LEGACY_ROWS)] == ["", "假資料"]
    assert store_of(bot, OWNER)["schema"] == 2                                  # A 還沒打開：A 的舊資料沒被動
    bot.command("month", OWNER)
    assert "祕密備註A" in [r["description"] for r in query(bot, OWNER, LEGACY_ROWS)]
    assert "祕密備註A" not in json.dumps(query(bot, OTHER, "SELECT * FROM transactions"), ensure_ascii=False)
    assert len(query(bot, OTHER, LEGACY_ROWS)) == 2 and len(query(bot, OWNER, LEGACY_ROWS)) == 5


def test_personal_mode_reads_the_shared_store(make_money):
    bot = make_money(per_user=False)
    put_old(bot, OWNER, schema1())
    assert "支出共 130 元" in bot.command("month", OWNER)


def test_nothing_to_move_writes_nothing(make_money):
    bot = make_money()
    for empty in (None, {}, {"schema": 2, "next_id": 1, "entries": [], "budget": None, "categories": []},
                  {"schema": "moved", "moved_to": "legacy-0.3"}, {"schema": 9, "entries": [{"amount": 1}]}):
        if empty is not None:
            put_old(bot, OWNER, empty)
        bot.command("month", OWNER)
        assert query(bot, OWNER, "SELECT id FROM batches") == [], empty     # 沒有寫入：第一次使用的引導照樣能選起始類別


def test_new_book_after_moving_does_not_convert_again(make_money):
    bot = make_money()
    put_old(bot, OWNER, schema1())
    bot.command("month", OWNER)
    legacy = bot.module.compat.legacy
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        assert legacy.maybe_convert(bot.pack, OWNER, book) is None                # 已經在用
    assert legacy.has_content(store_of(bot, OWNER)) is False                       # 搬走的記號不算舊資料


def test_a_failed_conversion_does_not_lock_people_out(make_money, monkeypatch):
    bot = make_money()
    put_old(bot, OWNER, schema1())
    legacy = bot.module.compat.legacy

    def boom(*a, **k):
        raise RuntimeError("壞掉了")

    monkeypatch.setattr(legacy, "convert", boom)
    assert "支出共 0 元" in bot.command("month", OWNER)
    assert store_of(bot, OWNER) == schema1() and store_of(bot, OWNER, "legacy-0.3") is None
    monkeypatch.undo()
    monkeypatch.setattr(bot.module.session, "now", lambda: datetime(2030, 1, 15, 12, 30))
    assert "支出共 130 元" in bot.command("month", OWNER)                         # 下次打開再試


def test_upgrade_does_not_touch_the_input_and_keeps_ids():
    from .conftest import load_money
    legacy = load_money().compat.legacy
    raw = schema1()
    once = legacy.upgrade(raw)
    assert raw == schema1() and [e["id"] for e in once["entries"]] == [1, 2] and once["next_id"] == 3
    twice = legacy.upgrade(once)
    assert [e["id"] for e in twice["entries"]] == [1, 2]
    kept = legacy.upgrade({"schema": 2, "next_id": 9, "entries": [{"id": 4, "amount": 1, "date": "2030-01-01"}, {"amount": 2}]})
    assert [e["id"] for e in kept["entries"]] == [4, 9]


def test_after_moving_the_old_style_commands_keep_working(make_money):
    bot = make_money()
    put_old(bot, OWNER, schema2())
    assert "咖啡" in bot.command("cats", OWNER, "咖啡")
    out = bot.command("cap", OWNER, "咖啡 200")
    assert "每期上限設為 200 元" in out
    record(bot, OWNER, 250, "咖啡")
    assert "⚠️ 咖啡 超過這一期的上限 200 元（超過 50 元）" in bot.command("month", OWNER)


def _synox_loads_packages():
    import importlib.util
    return importlib.util.find_spec("synox.packs.pkgload") is not None


@pytest.mark.skipif(not _synox_loads_packages(), reason="這份 SYNOX 還沒有多檔工具（python.package）")
def test_real_synox_vault_data_moves_over(synox_bot, owner):
    import time
    bot = synox_bot("money")                                                       # 真的時鐘：用今天的假資料
    pack = next(p for p in bot.app.extras["synox_packs"] if p.name == "money")
    old = {"entries": [{"amount": 50.0, "category": "咖啡", "memo": "", "at": int(time.time()) - 60},
                       {"amount": 80.0, "category": "午餐", "memo": "假資料", "at": int(time.time())}], "budget": 1000}
    pack.store.of(owner).save(old)
    bot.say(owner, "/money_cats 咖啡")
    assert "咖啡" in bot.last(owner)
    assert pack.store.of(owner).load(None, "legacy-0.3") == old
    assert pack.store.of(owner).load(None)["schema"] == "moved"
    with open(pack.store.of(owner).file("legacy-0.3"), "rb") as f:
        assert "假資料".encode() not in f.read()                                   # 另存的那一份還是密文
