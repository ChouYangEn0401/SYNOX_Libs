"""• bullet pack：快速記錄分類、今天、月誌、遷移按鈕、打勾、主題清單。"""

from datetime import datetime

from isdtelekit.core.registry import READ, WRITE


def entries(bot):
    return bot.packs["bullet"].store.load({})["entries"]


def test_classify(make_bot):
    classify = make_bot("bullet").modules["bullet"].classify
    assert classify("• 買牛奶") == ("task", "買牛奶")
    assert classify("*寄包裹") == ("task", "寄包裹") or classify("* 寄包裹") == ("task", "寄包裹")
    assert classify("○ 和小明吃飯") == ("event", "和小明吃飯")
    assert classify("o 散步") == ("event", "散步")
    assert classify("– 今天很累") == ("note", "今天很累")
    assert classify("- 想法：做一個 app") == ("note", "想法：做一個 app")
    assert classify("14:00 開會")[0] == "event"
    assert classify("下午三點 面試")[0] == "event"
    assert classify("記得繳電費")[0] == "task"
    assert classify("天氣很好")[0] == "note"


def test_rapid_log_today_and_done(make_bot, owner):
    bot = make_bot("bullet")
    bot.say(owner, "/bj • 買牛奶")
    assert "記到今天：[1] • 買牛奶" in bot.last(owner) and "任務" in bot.last(owner)
    bot.say(owner, "/bj ○ 14:00 開會")
    bot.say(owner, "/bj 今天很累 kind=note")
    assert "猜錯" not in bot.last(owner)                          # 指定了種類就不提示
    bot.say(owner, "/bj_done 1")
    assert "完成：買牛奶" in bot.last(owner)
    bot.say(owner, "/bj_done 2")
    assert "只有任務能打勾" in bot.last(owner)
    bot.say(owner, "/bj_today")
    text = bot.last(owner)
    assert "[1] × 買牛奶" in text and "[2] ○ 14:00 開會" in text and "[3] – 今天很累" in text
    bot.say(owner, "/bj_rm 3")
    assert "刪掉了" in bot.last(owner) and len(entries(bot)) == 2
    bot.say(owner, "/bj x kind=亂寫")
    assert "❌" in bot.last(owner)


def test_migration_with_buttons(make_bot, owner):
    bot = make_bot("bullet")
    bot.say(owner, "/bj • 繳電費")          # 1
    bot.say(owner, "/bj • 回信")            # 2
    bot.say(owner, "/bj • 換燈泡")          # 3
    bot.say(owner, "/bj – 一則筆記")        # 4（筆記不遷移）
    bot.say(owner, "/bj_migrate")
    assert "沒有要遷移" in bot.last(owner)                          # 今天記的不算「以前」

    bot.set_now(datetime(2026, 11, 11, 8, 0))
    bot.say(owner, "/bj_today")
    assert "以前還有 3 件任務沒處理" in bot.last(owner)
    bot.say(owner, "/bj_migrate")
    assert "還有 3 件" in bot.last(owner) and "繳電費" in bot.last(owner)
    assert bot.buttons(owner) == ["bjm:m:1", "bjm:d:1", "bjm:x:1", "bjm:s:1"]
    card_id = bot.api.sent[-1].message_id

    bot.press(owner, "bjm:m:1")
    assert bot.answers()[-1].startswith("搬到今天了")
    assert "回信" in bot.api.text_of(card_id)                     # 同一張卡改成下一件
    bot.press(owner, "bjm:d:2")
    bot.press(owner, "bjm:s:3")
    assert "這一輪遷移完成" in bot.api.text_of(card_id) and "略過的還有 1 件" in bot.api.text_of(card_id)

    by_id = {e["id"]: e for e in entries(bot)}
    assert by_id[1]["state"] == "migrated" and by_id[2]["state"] == "done" and by_id[3]["state"] == "open"
    new = by_id[5]
    assert (new["text"], new["day"], new["from"], new["state"]) == ("繳電費", "2026-11-11", 1, "open")

    bot.say(owner, "/bj_today")
    assert "[5] • 繳電費" in bot.last(owner)
    bot.press(owner, "bjm:m:1", message_id=card_id)                # 已經處理過的再按一次
    assert bot.answers()[-1] == "這件已經處理過了"
    assert len([e for e in entries(bot) if e["text"] == "繳電費"]) == 2


def test_migration_card_expires(make_bot, owner):
    bot = make_bot("bullet")
    bot.say(owner, "/bj • 舊任務")
    bot.set_now(datetime(2026, 11, 12, 8, 0))
    bot.say(owner, "/bj_migrate")
    bot.press(owner, "bjm:m:1", ago=25 * 3600)
    assert "超過 24 小時" in bot.answers()[-1]
    assert entries(bot)[0]["state"] == "open"


def test_forged_button_is_refused_for_strangers(make_bot, owner):
    bot = make_bot("bullet")
    bot.say(owner, "/bj • 我的任務")
    bot.set_now(datetime(2026, 11, 12, 8, 0))
    bot.say(555, "/bj_migrate")
    bot.press(555, "bjm:d:1")
    assert entries(bot)[0]["state"] == "open"                       # 個人版：別人按不了


def test_month_and_collections(make_bot, owner):
    bot = make_bot("bullet")
    bot.say(owner, "/bj ○ 10:00 面試")
    bot.say(owner, "/bj • 寫履歷")
    bot.say(owner, "/bj 原子習慣 col=書單")
    bot.say(owner, "/bj 被討厭的勇氣 col=書單")
    bot.say(owner, "/bj 九份 col=想去的地方")
    bot.say(owner, "/bj • 回顧 date=2026-11-02")
    bot.say(owner, "/bj_today")
    assert "原子習慣" not in bot.last(owner)                      # 主題清單不混進每日記錄
    bot.say(owner, "/bj_month")
    text = bot.last(owner)
    assert "2026-11 月誌" in text and "▸09 一 ○ 10:00 面試 •1/1" in text and " 02 一" in text
    assert "還沒做完的任務（2）" in text
    bot.say(owner, "/bj_month 2026-10")
    assert "還沒記東西" in bot.last(owner)
    bot.say(owner, "/bj_col")
    assert "書單（2）" in bot.last(owner) and "想去的地方（1）" in bot.last(owner)
    bot.say(owner, "/bj_col 書單")
    assert "原子習慣" in bot.last(owner) and "九份" not in bot.last(owner)
    bot.say(owner, "/bj_col 沒有這本")
    assert "是空的" in bot.last(owner)


def test_effects_and_panel(make_bot, owner):
    bot = make_bot("bullet")
    reg = bot.app.registry
    assert reg.node("bj.migrate").effect == WRITE and reg.node("bj.today").effect == READ
    assert reg.node("bj.add").command == "/bj" and reg.node("bj.col").command == "/bj_col"
    bot.say(owner, "/bj • 今天的事")
    data = bot.route("bullet", "today")
    assert data["entries"][0]["mark"] == "•" and data["to_migrate"] == 0


def test_server_mode_keeps_people_apart(make_bot):
    bot = make_bot("bullet", per_user=True)
    store = bot.packs["bullet"].store
    store.of(100).save({"entries": [], "next_id": 1})
    bot.say(100, "/bj • 我的")
    assert store.of(100).load({})["entries"][0]["text"] == "我的"
    assert store.of(555).load(None) is None
