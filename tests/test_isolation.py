"""多人契約（ADR-0003 第 6 節 S1、S2、S3、S5）：本機五個工具用真的 SYNOX（synox_bot）跑兩種模式。

- 伺服器版：兩個成員＋擁有者各寫一筆，每個讀的指令都只看到自己的（S1）；檔在 data/users/<id>/、是密文、
  data/ 根目錄沒有這個工具的共用檔（S2）；面板 route(A) 沒有 B 的字（S3）。
- 個人版：同一組指令照跑，檔在 data/<name>.json.enc（S5）。
最後一條是舊的回歸測試（假 store）：每個人的第一筆不能帶到別人的檔案裡（dict(EMPTY) 淺拷貝會共用 list）。"""

import os
import sys

import pytest

from isdtelekit.testing import OWNER_ID
from isdtelekit.users.roles import BasicRoles as R

from conftest import MONDAY

A, B = 77, 78                       # 兩個成員


def words(who):
    """每個人寫進去的字：只會出現在自己的回覆、面板、檔案裡。"""
    return {OWNER_ID: "擁有者祕密", A: "甲成員祕密", B: "乙成員祕密"}[who]


# 工具 → (寫一筆的指令（{} 是那個人的字）, 讀的指令, 面板 route（沒有＝None）, 面板會不會列出那個字)
CASES = {
    "notes": ("/note {}", ("/note_list", "/note_list 祕密"), None, False),
    "todo": ("/todo {} 今天", ("/todo_list", "/todo_list all", "/todo_list today"), "today", True),
    "calendar": ("/cal 今天 14:00 {}", ("/cal_today", "/cal_week", "/cal_list"), "today", True),
    "bullet": ("/bj • {}", ("/bj_today", "/bj_month"), "today", True),
    "money": ("/money 50 {}", ("/money_list", "/money_list keyword=祕密", "/money_month"), "summary", False),
}
# money 的面板只有金額與類別：另外用金額分辨（每個人記不同的錢）
AMOUNTS = {OWNER_ID: "4321", A: "5678", B: "8765"}


def _bot(synox_bot, name, mode):
    bot = synox_bot(name, mode=mode)
    module = sys.modules["synox_pack_" + name.replace("-", "_")]
    module.now = lambda: MONDAY                               # 「今天」固定（calendar、todo 的今天到期）
    pack = next(p for p in bot.app.extras["synox_packs"] if p.name == name)
    return bot, pack


def _write(bot, name, who):
    add = CASES[name][0].format(words(who))
    if name == "money":
        add = f"/money {AMOUNTS[who]} {words(who)}"
    bot.say(who, add, chat_id=who)
    return bot.last(chat_id=who)


def _view(bot, name, who):
    """這個人打每個讀的指令看到的全部字。"""
    for text in CASES[name][1]:
        bot.say(who, text, chat_id=who)
    return "\n".join(bot.texts(chat_id=who))


def _others(who, people):
    return [p for p in people if p != who]


def _leaks(text, name, others):
    found = [words(o) for o in others if words(o) in text]
    if name == "money":
        found += [AMOUNTS[o] for o in others if AMOUNTS[o] in text.replace(",", "")]
    return found


@pytest.mark.parametrize("name", sorted(CASES))
def test_server_mode_each_person_sees_only_their_own(synox_bot, name):
    """S1＋S3：伺服器版兩個成員＋擁有者各寫一筆；每個讀的指令、面板都只有自己的（擁有者也看不到成員的）。"""
    bot, pack = _bot(synox_bot, name, "server")
    bot.make(A, R.MEMBER, "a")
    bot.make(B, R.MEMBER, "b")
    people = (OWNER_ID, A, B)
    for who in people:
        reply = _write(bot, name, who)
        assert "未知" not in reply and "沒有這個指令" not in reply, reply
    for who in people:
        seen = _view(bot, name, who)
        if name == "money":
            assert words(who) in seen or AMOUNTS[who] in seen.replace(",", ""), seen
        else:
            assert words(who) in seen, seen
        assert _leaks(seen, name, _others(who, people)) == [], seen
    route = CASES[name][2]
    if route:
        for who in people:
            panel = str(pack.web_routes[route](who))
            assert _leaks(panel, name, _others(who, people)) == [], panel
            if CASES[name][3]:
                assert words(who) in panel, panel
            if name == "money":
                assert pack.web_routes[route](who)["total"] == float(AMOUNTS[who])


@pytest.mark.parametrize("name", sorted(CASES))
def test_server_mode_files_are_per_person_and_encrypted(synox_bot, name):
    """S2：伺服器版檔在 data/users/<id>/<name>.json.enc、是密文；data/ 根目錄沒有這個工具的共用檔。"""
    bot, pack = _bot(synox_bot, name, "server")
    bot.make(A, R.MEMBER, "a")
    bot.make(B, R.MEMBER, "b")
    for who in (OWNER_ID, A, B):
        _write(bot, name, who)
    data = bot.app.config.data_dir
    for who in (OWNER_ID, A, B):
        path = os.path.join(data, "users", str(who), f"{name}.json.enc")
        assert os.path.isfile(path) and pack.store.of(who).file() == path
        with open(path, "rb") as f:
            raw = f.read()
        assert not [w for w in (words(OWNER_ID), words(A), words(B)) if w.encode() in raw]
    assert not os.path.exists(os.path.join(data, f"{name}.json.enc"))
    assert not os.path.exists(os.path.join(data, f"{name}.json"))
    assert not os.path.exists(os.path.join(data, "packs", name))


@pytest.mark.parametrize("name", sorted(CASES))
def test_personal_mode_same_commands_one_shared_file(synox_bot, name):
    """S5：個人版同一組指令照跑，擁有者看得到自己寫的；檔在 data/<name>.json.enc（密文），沒有 data/users/。"""
    bot, pack = _bot(synox_bot, name, "personal")
    _write(bot, name, OWNER_ID)
    seen = _view(bot, name, OWNER_ID)
    if name == "money":
        assert AMOUNTS[OWNER_ID] in seen.replace(",", ""), seen
    else:
        assert words(OWNER_ID) in seen, seen
    route = CASES[name][2]
    if route and CASES[name][3]:
        assert words(OWNER_ID) in str(pack.web_routes[route](OWNER_ID))
    data = bot.app.config.data_dir
    path = os.path.join(data, f"{name}.json.enc")
    assert os.path.isfile(path) and pack.store.of(OWNER_ID).file() == path
    with open(path, "rb") as f:
        assert words(OWNER_ID).encode() not in f.read()
    assert not os.path.exists(os.path.join(data, "users"))


# ── 回歸：第一次寫入不共用 list（假 store，直接叫 handler） ─────────────────────

FIRST_WRITE = {"calendar": ("/cal 明天 9:00 {}", "events", "title"),
               "todo": ("/todo {}", "items", "text"),
               "bullet": ("/bj • {}", "entries", "text")}


@pytest.mark.parametrize("name", sorted(FIRST_WRITE))
def test_first_write_of_each_person_stays_private(make_bot, name):
    bot = make_bot(name, per_user=True)
    cmd, key, field = FIRST_WRITE[name]
    store = bot.packs[name].store
    # 假 pack 的權限只給擁有者；這裡直接呼叫 handler 的路徑：兩個人各自第一次寫入
    node = bot.app.registry.node(f"{bot.packs[name].manifest.group}.add")

    class U:
        def __init__(self, tg_id):
            self.tg_id = tg_id

    node.fn(bot.api, 100, U(100), cmd.format("擁有者的").split()[1:])
    node.fn(bot.api, 555, U(555), cmd.format("別人的").split()[1:])
    mine = [x[field] for x in store.of(100).load({})[key]]
    theirs = [x[field] for x in store.of(555).load({})[key]]
    assert mine == ["擁有者的"] and theirs == ["別人的"]


def test_fake_pack_follows_real_path_rules(make_bot):
    """假 pack（make_bot）伺服器版跟真的一樣：data: user 不准用共用的 pack.path()、path_of 一定要給人（ADR-0003 P7）。"""
    from synox.packs.loader import PackPathError
    pack = make_bot("calendar", per_user=True).packs["calendar"]
    with pytest.raises(PackPathError):
        pack.path("x.ics")
    with pytest.raises(PackPathError):
        pack.path_of(None, "x.ics")
    assert pack.path_of(77, "x.ics").endswith(os.path.join("users", "77", "calendar", "files", "x.ics"))
