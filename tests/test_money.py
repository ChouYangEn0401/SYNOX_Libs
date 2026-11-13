# -*- coding: utf-8 -*-
"""💰 記帳（從 SYNOX tests/test_packs.py、tests/test_money_isolation.py 搬來；用真的 SYNOX 載入這裡的 packs/money）。"""
from isdtelekit.users.roles import BasicRoles


def test_money_budget_warning(synox_bot, owner):
    bot = synox_bot("money")
    bot.say(owner, "/money_budget 100")
    bot.say(owner, "/money 60 午餐")
    assert "預算還剩 40 元" in bot.last(owner)
    bot.say(owner, "/money 50 晚餐 和朋友")
    assert "這一筆讓這個月超過預算了" in bot.last(owner) and "（和朋友）" in bot.last(owner)
    bot.say(owner, "/money_month")
    assert "共 110 元" in bot.last(owner) and "超過 10 元" in bot.last(owner)
    bot.say(owner, "/money_list 1")
    assert "晚餐 50 元" in bot.last(owner)
    bot.say(owner, "/money abc 午餐")
    assert "要是數字" in bot.last(owner)


def test_first_entries_do_not_leak_between_users(synox_bot):
    """伺服器版：A 的第一筆帳絕對不能出現在 B 的帳裡（2026-11-10 的淺拷貝 bug 的回歸測試）。"""
    bot = synox_bot("money", mode="server")
    bot.make(77, BasicRoles.MEMBER, "a")
    bot.make(78, BasicRoles.MEMBER, "b")
    bot.say(77, "/money 50 咖啡 祕密備註A", chat_id=77)
    bot.say(78, "/money 20 茶", chat_id=78)
    bot.say(78, "/money_list", chat_id=78)
    b_view = "\n".join(bot.texts(chat_id=78))
    assert "咖啡" not in b_view and "祕密備註A" not in b_view and "茶" in b_view
    bot.say(77, "/money_list", chat_id=77)
    a_view = "\n".join(bot.texts(chat_id=77))
    assert "茶" not in a_view and "咖啡" in a_view


def test_money_data_is_encrypted_and_panel_summary(synox_bot, owner):
    bot = synox_bot("money")
    bot.say(owner, "/money 120 午餐 祕密")
    vault = bot.app.extras["vault"]
    assert "祕密".encode() not in open(vault.path("money"), "rb").read()
    pack = next(p for p in bot.app.extras["synox_packs"] if p.name == "money")
    assert "summary" in pack.web_routes


# ---- 0.2.0：資料第 2 版（ADR-0002） ----
from datetime import datetime  # noqa: E402


def _ts(y, m, d, h=12):
    return int(datetime(y, m, d, h).timestamp())


OLD = {"entries": [{"amount": 50.0, "category": "咖啡", "memo": "", "at": _ts(2026, 11, 2)},
                   {"amount": 80.0, "category": "午餐", "memo": "假資料", "at": _ts(2026, 11, 3)}],
       "budget": 1000}


def _data(bot, user=None):
    from conftest import OWNER_ID
    return bot.packs["money"].store.of(user or OWNER_ID).load(None)


def test_old_data_upgrades_on_read_and_saves_as_schema_2(make_bot, owner):
    bot = make_bot("money")
    bot.packs["money"].store.save({**OLD, "entries": [dict(e) for e in OLD["entries"]]})
    bot.say(owner, "/money_list")
    assert "#1 11/02  咖啡 50 元" in bot.last(owner) and "#2 11/03  午餐 80 元（假資料）" in bot.last(owner)
    assert "schema" not in _data(bot)                              # 只是讀：不寫回
    bot.say(owner, "/money 20 茶")
    assert "#3" in bot.last(owner)
    data = _data(bot)
    assert data["schema"] == 2 and data["next_id"] == 4 and data["budget"] == 1000
    first = data["entries"][0]
    assert first == {"amount": 50.0, "category": "咖啡", "memo": "", "at": OLD["entries"][0]["at"],
                     "id": 1, "kind": "expense", "date": "2026-11-02"}   # 舊欄位都留著（退回 0.1.1 也讀得懂）
    bot.say(owner, "/money_month")
    assert "共 150 元" in bot.last(owner)


def test_upgrade_is_idempotent_and_keeps_ids(make_bot):
    module = make_bot("money").modules["money"]
    once = module.upgrade({**OLD, "entries": [dict(e) for e in OLD["entries"]]})
    twice = module.upgrade({**once, "entries": [dict(e) for e in once["entries"]]})
    assert once == twice and [e["id"] for e in twice["entries"]] == [1, 2]
    newer = module.upgrade({"schema": 3, "entries": [], "next_id": 9})
    assert newer["schema"] == 3 and newer["next_id"] == 9                 # 比這版新的資料不降版


def test_backdated_entry_lands_in_its_month(make_bot, owner):
    bot = make_bot("money")                                            # 現在＝2026-11-09
    bot.say(owner, "/money 30 早餐 date=昨天")
    assert "記在 2026-11-08" in bot.last(owner)
    bot.say(owner, "/money 40 晚餐 date=2026-10-31")
    bot.say(owner, "/money_month")
    assert "共 30 元" in bot.last(owner)
    bot.say(owner, "/money_month 2026-10")
    assert "2026-10 共 40 元" in bot.last(owner)
    bot.say(owner, "/money 40 晚餐 date=13/40")
    assert "看不懂" in bot.last(owner)


def test_old_data_in_real_vault_upgrades(synox_bot, owner):
    bot = synox_bot("money")
    pack = next(p for p in bot.app.extras["synox_packs"] if p.name == "money")
    pack.store.of(owner).save({**OLD, "entries": [dict(e) for e in OLD["entries"]]})
    bot.say(owner, "/money 20 茶")
    assert "#3" in bot.last(owner)
    assert pack.store.of(owner).load(None)["schema"] == 2