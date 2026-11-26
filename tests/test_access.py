"""誰看得到（ADR-0006 第 6 節 S4）：每個工具都用真的 SYNOX（synox_bot）跑一次權限。

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

CONNECT = ("notion", "todoist", "google-calendar", "google-sheets", "apple-reminders")
CONNECT = CONNECT + ("speech",)                  # speech：用擁有者的 Azure／OpenAI／Google 金鑰（ADR-0011）
# 只給擁有者的工具：接外面服務的（用擁有者的帳號）＋叫這台電腦上程式的（video-notes：設定的程式會被 SYNOX 執行）
# 只給擁有者的本機工具（碰硬體、碰使用者電腦上任意檔案）：權限跟接外面服務的一樣
# workspace：讀寫你電腦上工作區資料夾（ADR-0009：單人版專用）
OWNER_ONLY = CONNECT + ("video-notes", "usb-keys", "projects", "workspace")
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


def _say(bot, tg_id, text):
    """陌生人／訪客每天只能傳 30 則（SYNOX 的配額）；money、diary 的指令比 30 個多，每打一個先清掉今天的用量，
    才看得到「未知指令」而不是「超過每日訊息上限」（被配額擋下也沒跑，但這裡要驗的是權限）。"""
    if bot.app.limiter is not None:
        bot.app.limiter.reset_rate_limit(tg_id)
    bot.say(tg_id, text, chat_id=tg_id)


def _can_see(reg, user, node) -> bool:
    """看得到＝權限給。ai=False 的指令（只准本人在 Telegram／網頁打，例如 money_setting、diary_import）在這裡沒有「誰在打」
    的情境，authorize 回 reason="caller"——那是「要本人打」，不是權限不夠，所以也算看得到。"""
    return reg.visible(user, node) or reg.authorize(user, node, TYPED).reason == "caller"


def _user(bot, tg_id):
    bot.say(tg_id, "/start", chat_id=tg_id)                    # 陌生人第一次傳訊息＝登記（伺服器版是訪客）
    return bot.app.users.get(tg_id)


def test_every_tool_is_listed():
    assert set(OWNER_ONLY) <= set(PACK_NAMES) and {"money", "diary"} <= set(PACK_NAMES)


def test_workspace_writes_are_human_only_in_the_real_synox(synox_bot, tmp_path):
    """workspace（ADR-0009）：真的 SYNOX 載入後，root／new／mkdir 不給 AI（工具清單沒有、policy 拒）、
    門禁核心的範圍只准 human；看的指令擁有者的 AI 拿得到。"""
    import sys

    from synox.guard.testing import as_origin
    bot = synox_bot("workspace")
    (tmp_path / "home" / "Documents").mkdir(parents=True)
    sys.modules["synox_pack_workspace"].home_dir = lambda: str(tmp_path / "home")   # 萬一擋不住也不碰真的家目錄
    reg = bot.app.registry
    owner = bot.app.users.get(OWNER_ID)
    for name in ("root", "new", "mkdir"):
        node = reg.node(f"ws.{name}")
        assert node.ai is False and not policy.decide(bot.app, owner, node, via=AI).allowed, name
    for name in ("ls", "read", "tree", "find"):
        assert policy.decide(bot.app, owner, reg.node(f"ws.{name}"), via=AI).allowed, name
    with as_origin("ai", OWNER_ID):
        bot.say(OWNER_ID, "/ws_root default")
    assert "還沒設定工作區" not in bot.last(OWNER_ID) and "設定好了" not in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/ws_status")
    assert "還沒設定工作區" in bot.last(OWNER_ID)
    assert not (tmp_path / "home" / "Documents" / "SYNOX_Workspace").exists()


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
        _say(bot, OTHER_ID, f"/{node.command.lstrip('/')} 1")
        assert UNKNOWN in bot.last(chat_id=OTHER_ID), (node.path, bot.last(chat_id=OTHER_ID))
    _say(bot, OTHER_ID, "/help all")
    assert m.title not in bot.last(chat_id=OTHER_ID) and group.command not in bot.last(chat_id=OTHER_ID)
    owner = bot.app.users.get(OWNER_ID)
    assert reg.visible(owner, group) and all(_can_see(reg, owner, n) for n in leaves)


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
        _say(bot, OTHER_ID, f"/{node.command.lstrip('/')} 1")
        reply = bot.last(chat_id=OTHER_ID)
        assert UNKNOWN in reply or NO_PERMISSION in reply, (node.path, reply)
    _say(bot, OTHER_ID, "/help all")
    assert m.title not in bot.last(chat_id=OTHER_ID) and group.command not in bot.last(chat_id=OTHER_ID)


@pytest.mark.parametrize("name", PACK_NAMES)
def test_server_mode_member_sees_exactly_what_access_says(synox_bot, name):
    bot, m, group, leaves = _setup(synox_bot, name, "server")
    bot.make(MEMBER, R.MEMBER, "member")
    member = bot.app.users.get(MEMBER)
    reg = bot.app.registry
    if name in OWNER_ONLY:
        assert m.access == "owner"
        assert not reg.visible(member, group)
        for node in leaves:
            assert not reg.visible(member, node)
            assert not policy.decide(bot.app, member, node, via=TYPED).allowed
            assert not policy.decide(bot.app, member, node, via=AI).allowed       # AI 工具清單裡沒有
            _say(bot, MEMBER, f"/{node.command.lstrip('/')} 1")
            assert UNKNOWN in bot.last(chat_id=MEMBER), (node.path, bot.last(chat_id=MEMBER))
    else:
        assert m.access == "member"
        assert reg.visible(member, group)
        assert all(_can_see(reg, member, n) for n in leaves)
        ai_ok = [n.path for n in leaves if policy.decide(bot.app, member, n, via=AI).allowed]
        assert ai_ok, f"{name}: 成員的 AI 應該拿得到這個工具"
    owner = bot.app.users.get(OWNER_ID)
    assert reg.visible(owner, group) and all(_can_see(reg, owner, n) for n in leaves)
