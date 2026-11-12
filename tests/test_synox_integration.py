"""用 SYNOX 本體（真的 loader、真的保管箱加密、真的 AI 工具清單）載入這個 repo 的 packs/。
跟 SYNOX 自己的 tests/conftest.py 同一套假東西：FakeAdapter、FakeChannel，不連網、不開埠。"""

import os
import sys

import pytest

from isdtelekit.testing import OWNER_ID, FakeChannel

from synox.app import build
from synox.config import SynoxConfig
from synox.llm.fake import FakeAdapter
from synox.vault import Vault

from conftest import PACKS, ROOT


def make_app(tmp_path, mode="personal"):
    data = tmp_path / "data"
    config = SynoxConfig.from_dict({"owner_id": OWNER_ID, "data_dir": str(data), "vault": {"mode": "file"},
                                    "llm": {"kind": "fake"}, "mode": mode}, require_token=False, root=str(tmp_path))
    api = FakeChannel()
    vault = Vault(b"k" * 32, str(data))
    app = build(config, home=api, adapter=FakeAdapter(None), vault=vault, pack_dirs=[PACKS])
    return app, api, data


def say(app, api, user_id, text):
    from isdtelekit.core.channel import Inbound
    app.dispatcher.handle(api, Inbound("test", user_id, user_id, text, username=f"u{user_id}"), "typed")
    return api.last(user_id)


def test_synox_loads_all_three_packs_encrypted(tmp_path):
    app, api, data = make_app(tmp_path)
    report = app.extras["synox_pack_report"]
    status = {i.name: i.status for i in report.items}
    assert status == {"calendar": "loaded", "todo": "loaded", "bullet": "loaded"}, \
        [(i.name, i.status, i.detail) for i in report.items]
    assert "加好了" in say(app, api, OWNER_ID, "/cal 明天 14:00 看牙醫")
    assert "加好了" in say(app, api, OWNER_ID, "/todo !! 繳電費 明天")
    assert "記到今天" in say(app, api, OWNER_ID, "/bj • 買牛奶")
    files = sorted(f for f in os.listdir(data) if f.endswith(".json.enc"))
    assert {"calendar.json.enc", "todo.json.enc", "bullet.json.enc"} <= set(files)
    for f in ("calendar.json.enc", "todo.json.enc", "bullet.json.enc"):
        raw = (data / f).read_bytes()
        assert "看牙醫".encode() not in raw and "繳電費".encode() not in raw and "買牛奶".encode() not in raw


def test_strangers_cannot_see_personal_packs(tmp_path):
    app, api, _ = make_app(tmp_path)
    say(app, api, OWNER_ID, "/cal 明天 14:00 看牙醫")
    reply = say(app, api, 555, "/cal_list")
    assert "看牙醫" not in reply


def test_web_routes_and_ai_tools(tmp_path):
    app, api, _ = make_app(tmp_path)
    packs = {p.name: p for p in app.extras["synox_packs"]}
    say(app, api, OWNER_ID, "/todo 今天要做 今天")
    owner = app.users.get(OWNER_ID) if hasattr(app.users, "get") else OWNER_ID
    assert packs["todo"].web_routes["today"](owner)["due_today"][0]["text"] == "今天要做"
    assert set(packs["calendar"].web_routes) == {"today"} and set(packs["bullet"].web_routes) == {"today"}

    from synox.agent import tools
    names = {n.path for n in app.registry.root.walk() if not n.is_group}
    assert {"cal.add", "cal.ics", "todo.clear_done", "bj.migrate"} <= names
    if hasattr(tools, "schema"):
        node = app.registry.node("cal.add")
        props = tools.schema(node)
        assert "date" in str(props) and "time" in str(props)


def test_server_mode_each_person_has_own_calendar(tmp_path):
    app, api, data = make_app(tmp_path, mode="server")
    from isdtelekit.users.roles import next_up
    member = next_up(app.users.roles, min(app.users.roles))
    app.users.get_or_create(555, "u555")
    app.users.set_role(555, member, actor_id=OWNER_ID)
    say(app, api, OWNER_ID, "/cal 明天 14:00 擁有者的行程")
    say(app, api, 555, "/cal 明天 15:00 成員的行程")
    assert "成員的行程" in say(app, api, 555, "/cal_list") and "擁有者的行程" not in api.last(555)
    assert os.path.isfile(data / "users" / "555" / "calendar.json.enc")


def test_sync_script_check_passes():
    """packs/_shared 的正本與各 pack 的副本一致（scripts/sync_shared.py --check）。"""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import sync_shared
    assert sync_shared.main(["--check"]) == 0


@pytest.mark.parametrize("name", ["calendar", "todo"])
def test_inlined_parser_behaves_like_canonical(make_bot, name):
    from datetime import date

    import zhdate
    module = make_bot(name).modules[name]
    for text in ("明天 14:00 看牙醫", "下週一下午三點半 開會", "今天的事 今天"):
        a, b = zhdate.parse_when(text, date(2026, 11, 9)), module.parse_when(text, date(2026, 11, 9))
        assert (a.day, a.start, a.end, a.rest) == (b.day, b.start, b.end, b.rest)


def test_coexists_with_synox_builtin_packs(tmp_path):
    """跟 SYNOX 出廠的 notes、money、remind 與 connectors 一起載入：群組、指令名都不撞。"""
    from synox.packs import loader
    data = tmp_path / "data"
    config = SynoxConfig.from_dict({"owner_id": OWNER_ID, "data_dir": str(data), "vault": {"mode": "file"},
                                    "llm": {"kind": "fake"}}, require_token=False, root=str(tmp_path))
    api = FakeChannel()
    app = build(config, home=api, adapter=FakeAdapter(None), vault=Vault(b"k" * 32, str(data)),
                pack_dirs=list(loader.PACK_DIRS) + [PACKS])
    status = {i.name: i.status for i in app.extras["synox_pack_report"].items}
    assert all(status[n] == "loaded" for n in ("calendar", "todo", "bullet", "notes", "money")), status
    assert "加好了" in say(app, api, OWNER_ID, "/todo 買牛奶")
    assert "記下了" in say(app, api, OWNER_ID, "/note 買牛奶")
