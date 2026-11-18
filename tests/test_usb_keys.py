"""🔑 USB 鑰匙：走真的 SYNOX（synox_bot），隨身碟一律是假的（usbtether 的 InMemoryProbe＋暫存資料夾當「掛載點」）。
不碰真的隨身碟、不碰真的資料夾、不開埠（畫面用 SYNOX 的 AppClient＋FakeListen）。

usbtether 0.3.0 沒裝在測試用的 Python 裡時：要 usbtether 的測試整組跳過（原因寫在 skip 訊息），
不用 usbtether 的（沒裝時怎麼說、AI 能叫哪些、權限宣告）照跑。"""

import os
import sys

import pytest

from isdtelekit.core.registry import AI, TYPED

from conftest import OWNER_ID, PACKS

NAME = "usb-keys"
MODULE = "synox_pack_usb_keys"
AI_OK = {"keys", "drives", "lock", "unlock_check"}
HUMAN_ONLY = {"register", "reregister", "revoke", "unlock", "lock_pw", "folders", "folder_add", "folder_rm"}

try:
    import usbtether
except ImportError:                       # 沒裝：要它的測試跳過
    usbtether = None
needs_usbtether = pytest.mark.skipif(usbtether is None, reason="usbtether 0.3.0 沒裝在測試用的 Python（README「安裝 usbtether」）")


# ── 假隨身碟 ───────────────────────────────────────────────────────────

def make_stick(tmp_path, name, serial="AA00112233445566", pid="5567"):
    """一支「插著」的假隨身碟：掛載點是暫存資料夾。serial=None＝沒有可靠序號（不能登記）。"""
    from usbtether import UsbDevice, Volume
    mount = tmp_path / name
    mount.mkdir()
    device = UsbDevice(vendor_id="0781", product_id=pid, serial=serial, product="Test Stick",
                       capacity=32_000_000_000,
                       volumes=(Volume(mount_point=str(mount), volume_serial="1A2B-3C4D", filesystem="exFAT",
                                       capacity=32_000_000_000),))
    return device, str(mount)


class Rig:
    """synox_bot＋假的隨身碟清單（probe）；new_ring 換成用 probe 與檔案版的 secret store（不碰 DPAPI）。"""

    def __init__(self, bot, probe, tmp_path):
        self.bot, self.probe, self.tmp = bot, probe, tmp_path

    def say(self, text):
        self.bot.say(OWNER_ID, text)
        assert self.bot.app.tasks.wait(10)
        return self.bot.last()


@pytest.fixture
def rig(synox_bot, tmp_path, monkeypatch):
    from usbtether import FileSecretStore, InMemoryProbe, KeyRing
    bot = synox_bot(NAME)
    probe = InMemoryProbe()
    module = sys.modules[MODULE]
    monkeypatch.setattr(module, "new_ring", lambda home: KeyRing(home, probe=probe,
                                                                 secret_store=FileSecretStore(os.path.join(home, "secrets"))))
    return Rig(bot, probe, tmp_path)


def plug(rig, name="desk", **kw):
    device, mount = make_stick(rig.tmp, name, **kw)
    rig.probe.add(device)
    return device, mount


def opened(bot):
    from synox.web import apps
    from synox.web.apps.testing import AppClient, FakeListen
    manager = apps.of(bot.app)
    manager.listen = FakeListen()
    manager.start(NAME, by=OWNER_ID, via="web")
    client = AppClient(manager, NAME)
    client.enter(client.open_from_console(OWNER_ID))
    return client


def ok(resp):
    status, data = resp
    assert status == 200 and data.get("ok"), (status, data)
    return data["data"]


def refused(resp, needle):
    status, data = resp
    assert status != 200 and needle in (data.get("error") or ""), (status, data)


# ── 不用 usbtether 的 ───────────────────────────────────────────────────

def test_ai_sees_only_the_safe_commands(synox_bot):
    """AI 只拿得到「看」與「上鎖」；解開、登記、撤銷、密碼、資料夾都只有本人（ai=False＋pack.json callers human）。"""
    from synox import policy
    from synox.guard import origin
    bot = synox_bot(NAME)
    group = bot.app.registry.node("usb")
    leaves = {n.name: n for n in group.walk() if not n.is_group}
    assert set(leaves) == AI_OK | HUMAN_ONLY
    owner = bot.app.users.get(OWNER_ID)
    for name, node in leaves.items():
        allowed = policy.decide(bot.app, owner, node, via=AI).allowed
        assert allowed == (name in AI_OK), name
        assert node.ai == (name in AI_OK), name
        with origin.using(origin.Origin("telegram", OWNER_ID, surface="telegram")):      # 本人在 Telegram 打
            assert policy.decide(bot.app, owner, node, via=TYPED).allowed, name
    # 會改資料的是 WRITE；救不回來的（重新登記、撤銷）是 DANGER；AI 能叫的沒有 DANGER
    assert {n for n, node in leaves.items() if str(node.effect).upper() == "DANGER"} == {"reregister", "revoke"}
    # AI 的工具參數裡沒有密碼欄
    for name in AI_OK:
        assert "password" not in {f.name for f in leaves[name].fields}, name


def test_without_usbtether_every_command_says_how_to_install(synox_bot, monkeypatch):
    monkeypatch.setitem(sys.modules, "usbtether", None)          # import usbtether → ImportError
    bot = synox_bot(NAME)
    for text in ("/usb_keys", "/usb_drives", "/usb_lock 書桌 你好"):
        bot.say(OWNER_ID, text)
        assert bot.app.tasks.wait(10)
        assert "usbtether 函式庫還沒裝" in bot.last(), text


def test_manifest_declares_dependency_and_scope():
    from synox.packs import manifest as synox_manifest
    m = synox_manifest.read(os.path.join(PACKS, NAME), strict=True)
    assert m.access == "owner" and m.capabilities == ["filesystem:any", "subprocess"]
    assert m.requires["pip"] == ["usbtether==0.3.0 --hash=sha256:"
                                 "945ee70ff2f3ab14ebc86ad5ab22b68c511542acc91e30db8ac7020114947099"]
    assert set(m.raw["callers"]) == HUMAN_ONLY and all(v == ["human"] for v in m.raw["callers"].values())
    app = m.raw["web"]["app"]
    assert "remote" not in app                                    # 不從手機用
    assert set(app["actions"]) <= HUMAN_ONLY


# ── 鑰匙：列、登記、撤銷 ─────────────────────────────────────────────────

@needs_usbtether
def test_register_list_and_drives(rig):
    assert "還沒有登記" in rig.say("/usb_keys")
    _, mount = plug(rig)
    plug(rig, "cheap", serial=None, pid="1111")
    drives = rig.say("/usb_drives")
    assert "✅ 可以登記" in drives and "沒有可靠的序號" in drives
    assert "登記好了：「書桌」" in rig.say(f"/usb_register {mount} 書桌")
    keys = rig.say("/usb_keys")
    assert "🟢 書桌" in keys and "💻 記住在這台" in keys and "插著 1 把" in keys
    assert "已經是「書桌」" in rig.say("/usb_drives")
    assert "已經登記過" in rig.say(f"/usb_register {mount} 另一把") or "已經有鑰匙資料" in rig.bot.last()
    _, other = plug(rig, "spare", serial="BB99887766554433", pid="2222")
    assert "已經有一把叫「書桌」" in rig.say(f"/usb_register {other} 書桌")


@needs_usbtether
def test_password_is_never_taken_from_chat(rig):
    _, mount = plug(rig)
    reply = rig.say(f"/usb_register {mount} 書桌 protect=密碼 password=hunter2-fake")
    assert "密碼不要打在聊天裡" in reply and "hunter2" not in reply
    assert "還沒有登記" in rig.say("/usb_keys")
    assert "要在畫面上輸入密碼" in rig.say(f"/usb_register {mount} 書桌 protect=密碼")


@needs_usbtether
def test_lock_check_unlock_message_and_unplugged(rig):
    _, mount = plug(rig)
    rig.say(f"/usb_register {mount} 書桌")
    reply = rig.say("/usb_lock 書桌 明天 9 點開會")
    assert "鎖好了" in reply and "明天" not in reply
    msg = next(line for line in reply.splitlines() if line.startswith("utmsg1:"))
    assert "要用「書桌」解開（現在插著）" in rig.say(f"/usb_unlock_check {msg}")
    assert "明天 9 點開會" in rig.say(f"/usb_unlock {msg}")
    rig.probe.clear()                                              # 拔掉
    assert "沒插著" in rig.say(f"/usb_unlock {msg}")
    assert "現在沒插著" in rig.say(f"/usb_unlock_check {msg}")
    assert "不是 USB 鑰匙鎖過的" in rig.say("/usb_unlock_check hello")
    assert "沒有叫「別把」的鑰匙" in rig.say("/usb_lock 別把 hi")


@needs_usbtether
def test_revoke_needs_the_name_again_and_then_data_is_gone(rig):
    _, mount = plug(rig)
    rig.say(f"/usb_register {mount} 書桌")
    msg = next(x for x in rig.say("/usb_lock 書桌 祕密").splitlines() if x.startswith("utmsg1:"))
    warn = rig.say("/usb_revoke 書桌")
    assert "永遠打不開" in warn and "🟢 書桌" in rig.say("/usb_keys")
    assert "撤銷了「書桌」" in rig.say("/usb_revoke 書桌 confirm=書桌")
    assert "還沒有登記" in rig.say("/usb_keys")
    assert "已經撤銷" in rig.say(f"/usb_unlock {msg}")


@needs_usbtether
def test_reregister_needs_overwrite_word(rig):
    _, mount = plug(rig)
    rig.say(f"/usb_register {mount} 書桌")
    rig.say("/usb_revoke 書桌 confirm=書桌")
    assert "confirm 填「覆蓋」" in rig.say(f"/usb_reregister {mount} 新書桌")
    assert "重新登記好了" in rig.say(f"/usb_reregister {mount} 新書桌 confirm=覆蓋")
    assert "🟢 新書桌" in rig.say("/usb_keys")


# ── 檔案：只在設定的資料夾裡、不覆蓋 ───────────────────────────────────────

@needs_usbtether
def test_files_only_inside_allowed_folders_and_never_overwrite(rig):
    _, mount = plug(rig)
    rig.say(f"/usb_register {mount} 書桌")
    assert "還沒有可以用的資料夾" in rig.say("/usb_lock 書桌 file=1/a.txt")
    work = rig.tmp / "work"
    (work / "sub").mkdir(parents=True)
    (work / "sub" / "a.txt").write_text("hello file", encoding="utf-8")
    (rig.tmp / "outside.txt").write_text("nope", encoding="utf-8")
    assert "第 1 個資料夾" in rig.say(f"/usb_folder_add {work}")
    assert "⛔" in rig.say("/usb_lock 書桌 file=1/../outside.txt")
    assert "⛔" in rig.say(f"/usb_lock 書桌 file={rig.tmp / 'outside.txt'}")
    assert "a.txt → a.txt.utenc" in rig.say("/usb_lock 書桌 file=1/sub/a.txt")
    assert (work / "sub" / "a.txt.utenc").is_file() and (work / "sub" / "a.txt").is_file()    # 原檔不刪
    assert "免得蓋掉" in rig.say("/usb_lock 書桌 file=1/sub/a.txt")
    assert "要用「書桌」解開" in rig.say("/usb_unlock_check file=1/sub/a.txt.utenc")
    assert "免得蓋掉" in rig.say("/usb_unlock file=1/sub/a.txt.utenc")
    (work / "sub" / "a.txt").unlink()
    assert "解開了" in rig.say("/usb_unlock file=1/sub/a.txt.utenc")
    assert (work / "sub" / "a.txt").read_text(encoding="utf-8") == "hello file"
    assert "拿掉了" in rig.say("/usb_folder_rm 1")
    assert "還沒有可以用的資料夾" in rig.say("/usb_unlock_check file=1/sub/a.txt.utenc")


# ── 畫面（web.app）：密碼只從這裡收、危險動作要確認 ────────────────────────────

@needs_usbtether
def test_app_password_key_flow(rig):
    _, mount = plug(rig)
    client = opened(rig.bot)
    assert ok(client.read("keys")) == {"ready": True, "message": "", "keys": []}
    drives = ok(client.read("drives"))["drives"]
    assert [d["can_register"] for d in drives] == [True]
    out = ok(client.do("register", {"mount": mount, "label": "書桌", "protect": "密碼", "password": "fake-pass-1"}))
    assert out["protect"] == "password"
    keys = ok(client.read("keys"))["keys"]
    assert keys[0]["label"] == "書桌" and keys[0]["present"] and keys[0]["protect"] == "password"
    refused(client.do("lock_pw", {"key": "書桌", "text": "hi"}), "要密碼")
    locked = ok(client.do("lock_pw", {"key": "書桌", "text": "只給我看", "password": "fake-pass-1"}))
    assert locked["message"].startswith("utmsg1:")
    assert ok(client.read("check", text=locked["message"][:200]))["label"] == "書桌"
    refused(client.do("unlock", {"text": locked["message"], "password": "wrong-pass"}), "密碼不對")
    assert ok(client.do("unlock", {"text": locked["message"], "password": "fake-pass-1"}))["text"] == "只給我看"
    # 聊天那條路：要密碼的鑰匙鎖不了、也不收密碼
    assert "要密碼" in rig.say("/usb_lock 書桌 hi")


@needs_usbtether
def test_app_danger_actions_ask_first(rig):
    _, mount = plug(rig)
    client = opened(rig.bot)
    ok(client.do("register", {"mount": mount, "label": "書桌"}))
    status, data = client.do("revoke", {"key": "書桌", "confirm": "書桌"})
    assert status == 409 and data["code"] == "confirm" and "永遠打不開" in data["confirm"]["text"]
    refused(client.do("revoke", {"key": "書桌", "confirm": "不對"}, confirmed=True), "再打一次鑰匙的名字")
    ok(client.do("revoke", {"key": "書桌", "confirm": "書桌"}, confirmed=True))
    assert ok(client.read("keys"))["keys"] == []


@needs_usbtether
def test_app_browse_stays_inside_folders(rig):
    work = rig.tmp / "work"
    (work / "docs").mkdir(parents=True)
    (work / "docs" / "b.txt").write_text("x", encoding="utf-8")
    (work / ".hidden").write_text("x", encoding="utf-8")
    client = opened(rig.bot)
    refused(client.do("folder_add", {"path": "relative/path"}), "完整的資料夾路徑")
    ok(client.do("folder_add", {"path": str(work)}))
    top = ok(client.read("browse", folder="1"))
    assert top["dirs"] == ["docs"] and top["files"] == []
    assert ok(client.read("browse", folder="1", sub="docs"))["files"] == [{"name": "b.txt", "locked": False}]
    refused(client.read("browse", folder="1", sub=".."), "只能看你設定的資料夾裡面")


@needs_usbtether
def test_typed_commands_run_in_the_background(rig, monkeypatch):
    """讀隨身碟要 2 秒左右：人在聊天打的丟到 app.tasks（不卡住收訊），結果一樣回來。"""
    names = []
    real = rig.bot.app.tasks.run
    monkeypatch.setattr(rig.bot.app.tasks, "run", lambda fn, name="task": (names.append(name), real(fn, name=name))[1])
    assert "還沒有登記" in rig.say("/usb_keys")
    assert "pack-usb-keys" in names
