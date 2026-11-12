# -*- coding: utf-8 -*-
"""📒 隨手記（從 SYNOX tests/test_packs.py 搬來；用真的 SYNOX 載入這裡的 packs/notes）。"""


def test_notes_commands(synox_bot, owner):
    bot = synox_bot("notes")
    bot.say(owner, "/note 買牛奶")
    bot.say(owner, "/note 繳電費")
    bot.say(owner, "/note_list 牛奶")
    assert "買牛奶" in bot.last(owner) and "繳電費" not in bot.last(owner)
    bot.say(owner, "/note_rm 5")
    assert "沒有第 5 筆" in bot.last(owner)
    bot.say(owner, "/note_rm 1")
    assert "買牛奶" in bot.last(owner)
    raw = open(bot.app.extras["vault"].path("notes"), "rb").read()
    assert "繳電費".encode() not in raw
