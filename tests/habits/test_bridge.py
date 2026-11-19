"""指令與畫面動作：同一份指令表；每個人的資料互相看不到；錯誤轉成人看得懂的一句。"""
import pytest

from synox.packs import kit

from .hbot import OTHER, OWNER, U, make_habits, read_manifest  # noqa: F401


def decl():
    return read_manifest().web["app"]


def test_manifest_matches_command_table(make_habits):
    bot = make_habits()
    bridge = bot.module.bridge
    app_cmds = {c.name: c.effect for c in bridge.COMMANDS if c.app}
    assert app_cmds == dict(decl()["actions"]), "每個畫面動作都要有同名同危險程度的指令"
    assert set(bridge.READS) == set(decl()["reads"])
    assert set(bot.pack.app_actions) == set(app_cmds) and set(bot.pack.app_reads) == set(bridge.READS)
    for c in bridge.COMMANDS:
        assert bot.node(c.name) is not None


def test_add_list_check_flow(make_habits):
    bot = make_habits()
    out = bot.command("add", OWNER, "讀書")
    assert "加好了" in out and "21:00" in out
    assert "讀書" in bot.command("list", OWNER)
    assert "✅" in bot.command("check", OWNER, "讀書")
    assert "做過的" in bot.command("today", OWNER)
    assert "找不到" in bot.command("check", OWNER, "不存在")


def test_quota_and_time_quota_commands(make_habits):
    bot = make_habits()
    assert "加好了" in bot.command("add", OWNER, "運動 repeat=quota times=2")
    assert "加好了" in bot.command("add", OWNER, "練琴 minutes=90")
    assert "記了" in bot.command("log", OWNER, "練琴 45")
    assert "做了多久" in bot.command("check", OWNER, "練琴")


def test_locked_delete_needs_force(make_habits):
    bot = make_habits()
    bot.command("add", OWNER, "讀書")
    assert "承諾" in bot.command("rm", OWNER, "讀書")
    assert "刪掉了" in bot.command("rm", OWNER, "讀書 force=yes")
    assert "沒有" in bot.command("list", OWNER)


def test_actions_return_dicts_and_reads(make_habits):
    bot = make_habits()
    saved = bot.act("save", OWNER, {"json": '{"name": "喝水", "kind": "habit", "repeat": "daily", "end_date": "2030-03-01"}'})
    rid = saved["routine"]["id"]
    assert bot.read("routines", OWNER)["routines"][0]["id"] == rid
    meta = bot.read("meta", OWNER)
    assert "insist_steps" in meta and "cadences" in meta
    assert bot.act("check", OWNER, {"ref": rid, "state": "done"})["routine"]["today_state"] == "done"
    assert bot.act("display", OWNER, {"red": "yes"})["show_missed_red"] is True
    with pytest.raises(kit.AppError):
        bot.act("check", OWNER, {"ref": "nope"})
    with pytest.raises(kit.AppError) as e:
        bot.act("rm", OWNER, {"ref": rid})
    assert e.value.code == "insist"
    assert "喝水" in bot.pack.app_describe["rm"](U(OWNER), {"ref": rid})
    assert bot.act("rm", OWNER, {"ref": rid, "force": "yes"})["deleted"] == rid


def test_journey_commands_and_actions(make_habits):
    bot = make_habits()
    assert "3 步" in bot.command("ms_new", OWNER, "小遊戲 steps=畫角色;寫關卡;上架")
    assert "1/3" in bot.command("ms_done", OWNER, "小遊戲 1")
    assert "順延" in bot.command("ms_miss", OWNER, "小遊戲 label=生病")
    shown = bot.command("ms_show", OWNER, "小遊戲")
    assert "生病" in shown and "寫關卡" in shown
    mid = bot.read("milestones", OWNER)["milestones"][0]["id"]
    got = bot.read("milestone", OWNER, {"id": mid})
    assert got["stats"]["done"] == 1 and len(got["cells"]) == 4
    j = bot.act("ms_insert", OWNER, {"id": mid, "idx": "0", "where": "after", "kind": "extra", "title": "臨時的事"})["milestone"]
    assert j["cells"][1]["type"] == "extra"
    with pytest.raises(kit.AppError):
        bot.act("ms_remove", OWNER, {"id": mid, "idx": "0"})                  # 藍圖鎖住
    j = bot.act("ms_edit", OWNER, {"id": mid, "idx": "0", "mood": "不錯", "note": "順利"})["milestone"]
    assert j["cells"][0]["mood"] == "不錯"
    assert bot.act("ms_save", OWNER, {"json": '{"id": "%s", "name": "改名"}' % mid})["milestone"]["name"] == "改名"
    assert bot.act("ms_complete", OWNER, {"id": mid, "idx": "3"})["milestone"]["stats"]["done"] == 2
    assert bot.act("ms_rm", OWNER, {"ref": mid})["deleted"] == mid
    assert "還沒有旅程" in bot.command("ms_list", OWNER)


def test_users_cannot_see_each_other(make_habits):
    bot = make_habits(per_user=True)
    bot.command("add", OWNER, "讀書")
    bot.command("ms_new", OWNER, "旅程 steps=a;b")
    assert bot.read("routines", OTHER)["routines"] == []
    assert bot.read("milestones", OTHER)["milestones"] == []
    assert "沒有" in bot.command("list", OTHER)
    with pytest.raises(kit.AppError):
        bot.act("check", OTHER, {"ref": "讀書"})


def test_summary_route(make_habits):
    bot = make_habits()
    bot.command("add", OWNER, "讀書")
    s = bot.pack.web_routes["summary"](OWNER)
    assert s["habits"] == 1 and s["due"][0]["name"] == "讀書" and s["milestones"] == []
