# -*- coding: utf-8 -*-
"""⏰ 提醒：工具本身只把 SYNOX 的 /remind_* 掛到自己的群組（程式在 SYNOX 的 synox.schedule，那邊有完整測試）。
這裡測「裝了這個工具，/remind_* 就能用、排程器會響」。"""
from datetime import datetime


def test_remind_works_once_installed(synox_bot, owner):
    bot = synox_bot("remind")
    bot.say(owner, "/remind_add 08:00 吃藥")
    assert "每天 08:00 提醒你：吃藥" in bot.last(owner)
    runner = bot.app.extras["synox_schedule_runner"]
    assert len(runner.tick(datetime(2026, 11, 9, 8, 0, 10))) == 1 and bot.last(owner) == "⏰ 提醒：吃藥"
    bot.say(owner, "/remind_list")
    assert "1. 每天 08:00  吃藥" in bot.last(owner)


def test_without_remind_pack_there_is_no_remind_command(synox_bot, owner):
    bot = synox_bot("notes")
    assert bot.app.registry.node("remind") is None
    assert bot.app.registry.node("schedule") is not None        # /schedule_* 是 SYNOX 本體的功能
