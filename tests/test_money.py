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
