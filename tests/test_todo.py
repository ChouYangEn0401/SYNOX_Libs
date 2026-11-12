"""✅ todo pack：重要度、#標籤、到期、清單篩選、完成、刪除、清掉已完成（DANGER）。"""

from datetime import datetime

from isdtelekit.core.registry import DANGER, WRITE


def items(bot):
    return bot.packs["todo"].store.load({})["items"]


def test_add_parses_priority_tags_due(make_bot, owner):
    bot = make_bot("todo")
    bot.say(owner, "/todo !! 繳電費 明天 #家事 #帳單")
    it = items(bot)[0]
    assert (it["text"], it["priority"], it["tags"], it["due"]) == ("繳電費", 2, ["家事", "帳單"], "2026-11-10")
    assert "‼️" in bot.last(owner) and "還沒做完的共 1 項" in bot.last(owner)
    bot.say(owner, "/todo 回信給房東 !")
    assert items(bot)[1]["priority"] == 1 and items(bot)[1]["due"] == ""
    bot.say(owner, "/todo 下午3點 打電話")
    assert items(bot)[2]["due"] == "2026-11-09" and items(bot)[2]["time"] == "15:00"   # 只有時間＝今天
    bot.say(owner, "/todo 報名 due=2026-11-30 priority=1")
    assert items(bot)[3]["due"] == "2026-11-30" and items(bot)[3]["priority"] == 1
    bot.say(owner, "/todo #家事")
    assert "要寫做什麼" in bot.last(owner)
    bot.say(owner, "/todo x due=亂寫")
    assert "看不懂到期" in bot.last(owner)


def test_list_filters_and_order(make_bot, owner):
    bot = make_bot("todo")
    bot.say(owner, "/todo 買牛奶")
    bot.say(owner, "/todo !! 繳稅 今天")
    bot.say(owner, "/todo 換燈泡 #家事")
    bot.set_now(datetime(2026, 11, 11, 9, 0))
    bot.say(owner, "/todo 寄包裹 今天")
    bot.say(owner, "/todo_list")
    lines = bot.last(owner).splitlines()
    assert "還沒做完（4 項）" in lines[0]
    assert "繳稅" in lines[1] and "逾期" in lines[1]               # 有期限的在前，逾期標出來
    assert "寄包裹" in lines[2]
    bot.say(owner, "/todo_list overdue")
    assert "繳稅" in bot.last(owner) and "寄包裹" not in bot.last(owner)
    bot.say(owner, "/todo_list today")
    assert "寄包裹" in bot.last(owner) and "繳稅" not in bot.last(owner)
    bot.say(owner, "/todo_list #家事")
    assert "換燈泡" in bot.last(owner) and "買牛奶" not in bot.last(owner)
    bot.say(owner, "/todo_list 亂寫")
    assert "❌" in bot.last(owner)


def test_done_rm_clear(make_bot, owner):
    bot = make_bot("todo")
    for t in ("a", "b", "c"):
        bot.say(owner, f"/todo {t}")
    bot.say(owner, "/todo_done 2")
    assert "完成：b" in bot.last(owner) and "還剩 2 項" in bot.last(owner)
    bot.say(owner, "/todo_done 2")
    assert "早就完成" in bot.last(owner)
    bot.say(owner, "/todo_done 9")
    assert "沒有 [9]" in bot.last(owner)
    bot.say(owner, "/todo_list")
    assert " b" not in bot.last(owner)
    bot.say(owner, "/todo_list all")
    assert "☑ [2] b" in bot.last(owner)
    bot.say(owner, "/todo_rm 1")
    assert "刪掉了：a" in bot.last(owner)
    bot.say(owner, "/todo_clear_done")
    assert "清掉 1 項" in bot.last(owner)
    assert [it["text"] for it in items(bot)] == ["c"]
    bot.say(owner, "/todo_clear_done")
    assert "沒有已完成" in bot.last(owner)


def test_effects(make_bot):
    reg = make_bot("todo").app.registry
    assert reg.node("todo.clear_done").effect == DANGER
    assert reg.node("todo.rm").effect == WRITE and reg.node("todo.done").effect == WRITE
    assert reg.node("todo.clear_done").command == "/todo_clear_done"


def test_panel_route(make_bot, owner):
    bot = make_bot("todo")
    bot.say(owner, "/todo 繳稅 2026-11-01")         # 寫了年份的過去日期 → 逾期
    bot.say(owner, "/todo 今天的事 今天")
    bot.say(owner, "/todo 以後的事")
    data = bot.route("todo", "today")
    assert [x["text"] for x in data["overdue"]] == ["繳稅"]
    assert [x["text"] for x in data["due_today"]] == ["今天的事"]
    assert [x["text"] for x in data["later"]] == ["以後的事"] and data["open"] == 3
