"""誰看得到（ADR-0003 第 6 節 S4）：九個工具都用真的 SYNOX（synox_bot）跑一次權限。

- 個人版：陌生人打了＝未知指令，/help 也看不到。
- 伺服器版：訪客（陌生人自動登記）看不到、打了不會跑、AI 不給；
  access: member 的本機工具，成員看得到、AI 也拿得到；access: owner 的外部工具，成員看不到、打了＝未知指令、
  policy.decide(via=AI) 一個都不給（AI 工具清單裡沒有）。擁有者兩種模式都看得到。"""

import pytest

from isdtelekit.core.registry import AI, TYPED
from isdtelekit.testing import OWNER_ID
from isdtelekit.users.roles import BasicRoles as R

from synox import policy
from synox.packs import manifest as synox_manifest

from conftest import OTHER_ID, PACK_NAMES, PACKS

CONNECT = ("notion", "todoist", "google-calendar", "apple-reminders")
MEMBER = 77
UNKNOWN = "未知指令"
NO_PERMISSION = "沒有執行此指令的權限"


def _setup(synox_bot, name, mode):
    bot = synox_bot(name, mode=mode)
    m = synox_manifest.read(f"{PACKS}/{name}", strict=True)
    group = bot.app.registry.node(m.group)
    leaves = [n for n in group.walk() if not n.is_group]
    assert leaves, name
    return bot, m, group, leaves


def _user(bot, tg_id):
    bot.say(tg_id, "/start", chat_id=tg_id)                    # 陌生人第一次傳訊息＝登記（伺服器版是訪客）
    return bot.app.users.get(tg_id)


def test_every_tool_is_listed():
    assert set(CONNECT) <= set(PACK_NAMES) and len(PACK_NAMES) == 9


@pytest.mark.parametrize("name", PACK_NAMES)
def test_personal_mode_stranger_gets_unknown_command(synox_bot, name):
    bot, m, group, leaves = _setup(synox_bot, name, "personal")
    stranger = _user(bot, OTHER_ID)
    reg = bot.app.registry
    assert not reg.visible(stranger, group)
    for node in leaves:
        assert not reg.visible(stranger, node)
        assert not policy.decide(bot.app, stranger, node, via=TYPED).allowed
        assert not policy.decide(bot.app, stranger, node, via=AI).allowed
        bot.say(OTHER_ID, f"/{node.command.lstrip('/')} 1", chat_id=OTHER_ID)
        assert UNKNOWN in bot.last(chat_id=OTHER_ID), (node.path, bot.last(chat_id=OTHER_ID))
    bot.say(OTHER_ID, "/help all", chat_id=OTHER_ID)
    assert m.title not in bot.last(chat_id=OTHER_ID) and group.command not in bot.last(chat_id=OTHER_ID)
    owner = bot.app.users.get(OWNER_ID)
    assert reg.visible(owner, group) and all(reg.visible(owner, n) for n in leaves)


@pytest.mark.parametrize("name", PACK_NAMES)
def test_server_mode_guest_cannot_see_or_use(synox_bot, name):
    bot, m, group, leaves = _setup(synox_bot, name, "server")
    guest = _user(bot, OTHER_ID)
    assert guest.role == min(bot.app.users.roles)
    reg = bot.app.registry
    assert not reg.visible(guest, group)
    for node in leaves:
        assert not reg.visible(guest, node)
        assert not policy.decide(bot.app, guest, node, via=TYPED).allowed
        assert not policy.decide(bot.app, guest, node, via=AI).allowed
        bot.say(OTHER_ID, f"/{node.command.lstrip('/')} 1", chat_id=OTHER_ID)
        reply = bot.last(chat_id=OTHER_ID)
        assert UNKNOWN in reply or NO_PERMISSION in reply, (node.path, reply)
    bot.say(OTHER_ID, "/help all", chat_id=OTHER_ID)
    assert m.title not in bot.last(chat_id=OTHER_ID) and group.command not in bot.last(chat_id=OTHER_ID)


@pytest.mark.parametrize("name", PACK_NAMES)
def test_server_mode_member_sees_exactly_what_access_says(synox_bot, name):
    bot, m, group, leaves = _setup(synox_bot, name, "server")
    bot.make(MEMBER, R.MEMBER, "member")
    member = bot.app.users.get(MEMBER)
    reg = bot.app.registry
    if name in CONNECT:
        assert m.access == "owner"
        assert not reg.visible(member, group)
        for node in leaves:
            assert not reg.visible(member, node)
            assert not policy.decide(bot.app, member, node, via=TYPED).allowed
            assert not policy.decide(bot.app, member, node, via=AI).allowed       # AI 工具清單裡沒有
            bot.say(MEMBER, f"/{node.command.lstrip('/')} 1", chat_id=MEMBER)
            assert UNKNOWN in bot.last(chat_id=MEMBER), (node.path, bot.last(chat_id=MEMBER))
    else:
        assert m.access == "member"
        assert reg.visible(member, group)
        assert all(reg.visible(member, n) for n in leaves)
        ai_ok = [n.path for n in leaves if policy.decide(bot.app, member, n, via=AI).allowed]
        assert ai_ok, f"{name}: 成員的 AI 應該拿得到這個工具"
    owner = bot.app.users.get(OWNER_ID)
    assert reg.visible(owner, group) and all(reg.visible(owner, n) for n in leaves)
