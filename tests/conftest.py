"""共用的假東西，不連網、不開埠、不開 Telegram、不叫 AI。

SYNOX 本體從哪裡 import：scripts/_synox.py（環境變數 SYNOX_HOME，沒設就找並排的 ../SYNOX；只 import、不改它）。

兩層測試：
- make_bot（快）：SDK 的 BotHarness＋假的 pack.store（記憶體裡的 JSON）＋可以換「現在」。pack.py 的載入方式照 SYNOX loader
  （spec_from_file_location＋exec，不把 pack 資料夾放進 sys.path），測到的就是 SYNOX 真的會執行的那一份程式。
  只給不靠 SYNOX 其他模組的工具用（calendar、todo、bullet）。
- synox_bot（真的）：SYNOX 本體的 build()——真的 loader、真的保管箱加密、真的權限、伺服器版每人一份。
  跟 SYNOX 自己的 tests/conftest.py 同一套假東西（FakeAdapter、FakeChannel）。
"""

import importlib.util
import json
import os
import sys
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _synox  # noqa: E402

SYNOX_HOME = _synox.ensure()
PACKS = _synox.PACKS
sys.path.insert(0, os.path.join(PACKS, "_shared"))

from isdtelekit.testing import OWNER_ID, BotHarness, FakeChannel  # noqa: E402

from synox.packs import manifest as synox_manifest  # noqa: E402

PACK_NAMES = tuple(os.path.basename(f) for f in _synox.pack_folders())     # 全部工具
PLANNER = ("calendar", "todo", "bullet")                                     # make_bot（假 store）測得了的
OTHER_ID = 555
# 2026-11-09 是星期一；測試的「現在」固定在這一刻
MONDAY = datetime(2026, 11, 9, 9, 30)


class FakeStore:
    """pack.store 的假的：一份 JSON 放記憶體（每次存都真的轉一次 JSON，存不了的東西會出錯）；of(user) 每人一份。"""

    def __init__(self, per_user=False):
        self.data = {}
        self.per_user = per_user
        self._scoped = {}

    def of(self, user):
        if not self.per_user:
            return self
        tg_id = user if isinstance(user, int) else user.tg_id
        return self._scoped.setdefault(tg_id, FakeStore())

    def load(self, default=None, key=None):
        raw = self.data.get(key)
        return default if raw is None else json.loads(raw)      # 跟真的 Vault 一樣：沒有檔＝把 default 原樣交回

    def save(self, data, key=None):
        self.data[key] = json.dumps(data, ensure_ascii=False)

    def update(self, fn, default, key=None):
        data = self.load(default, key)
        result = fn(data)
        self.save(data, key)
        return result


class FakePack:
    """SYNOX loader 的 Pack 物件最少要有的樣子：manifest、store、group、path()、path_of()、web_route()。"""

    def __init__(self, app, m, store, data_dir):
        self.app = app
        self.manifest = m
        self.store = store
        self.data_dir = data_dir
        self.web_routes = {}

    @property
    def name(self):
        return self.manifest.name

    @property
    def group(self):
        return self.app.registry.group(self.manifest.group)

    def path(self, *parts):
        folder = os.path.join(self.data_dir, "packs", self.manifest.name)
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, *parts)

    def path_of(self, user, *parts):
        tg_id = user if isinstance(user, int) else getattr(user, "tg_id", "me")
        folder = os.path.join(self.data_dir, "users", str(tg_id), self.manifest.name, "files")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, *parts)

    def web_route(self, route, fn):
        assert route in self.manifest.web.get("api", []), f"{route} not declared in pack.json"
        self.web_routes[route] = fn


def load_module(name):
    """照 SYNOX loader 的方式把 packs/<name>/pack.py 載成模組。"""
    path = os.path.join(PACKS, name, "pack.py")
    with open(path, "rb") as f:
        source = f.read()
    spec = importlib.util.spec_from_file_location(f"synoxlibs_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(source, path, "exec"), module.__dict__)
    return module


class PackBot(BotHarness):
    """BotHarness ＋ 每個 pack 的模組、假 store、web routes；set_now() 換「現在」。"""

    modules: dict
    packs: dict

    def set_now(self, when: datetime):
        for module in self.modules.values():
            module.now = lambda when=when: when

    def route(self, pack, name, user=OWNER_ID):
        return self.packs[pack].web_routes[name](user)


@pytest.fixture
def make_bot(tmp_path):
    def factory(*names, per_user=False, now=MONDAY):
        names = names or PLANNER
        modules, packs = {}, {}

        def install(app):
            for name in names:
                m = synox_manifest.read(os.path.join(PACKS, name), strict=True)
                app.registry.define_group(m.group, m.title, m.summary, owner_only=True)   # 個人版的 access=member
                module = load_module(name)
                pack = FakePack(app, m, FakeStore(per_user), str(tmp_path / "data"))
                module.install(app, pack)
                modules[name], packs[name] = module, pack

        bot = BotHarness.create(tmp_path, install=install)
        bot.__class__ = PackBot
        bot.modules, bot.packs = modules, packs
        bot.set_now(now)
        return bot
    return factory


class SynoxBot(BotHarness):
    """BotHarness 包一個真的 SYNOX app；talk() 等 AI 那一輪（app.tasks）做完。"""

    def talk(self, user_id, text, **kw):
        route = self.say(user_id, text, **kw)
        assert self.app.tasks.wait(10), "AI turn did not finish"
        return route

    def press_and_wait(self, user_id, data, **kw):
        route = self.press(user_id, data, **kw)
        self.app.tasks.wait(10)
        return route


def trust_core(data_dir, *folders):
    """伺服器版：沒鎖的 python 工具只給擁有者（SYNOX PERM-8）。測成員能用的測試先鎖成 core（＝擁有者安裝時選「核心」）。"""
    from synox.packs import lock
    for folder in folders:
        lock.approve(str(data_dir), synox_manifest.read(folder), None, OWNER_ID, via="test", trust="core")


@pytest.fixture
def synox_bot(tmp_path):
    """真的 SYNOX（build）只載入這個 repo 的 packs/；mode="server" 時先把工具鎖成 core。"""
    from synox.app import build
    from synox.config import SynoxConfig
    from synox.llm.fake import FakeAdapter
    from synox.vault import Vault

    def factory(*names, mode="personal"):
        data = tmp_path / "data"
        config = SynoxConfig.from_dict({"owner_id": OWNER_ID, "data_dir": str(data), "vault": {"mode": "file"},
                                        "llm": {"kind": "fake"}, "mode": mode,
                                        "packs": {"enabled": list(names) if names else "all"}},
                                       require_token=False, root=str(tmp_path))
        if mode == "server":
            trust_core(data, *_synox.pack_folders())
        api = FakeChannel()
        app = build(config, home=api, adapter=FakeAdapter(None), vault=Vault(b"k" * 32, str(data)), pack_dirs=[PACKS])
        return SynoxBot(app, api)
    return factory


@pytest.fixture
def host_bot(tmp_path, monkeypatch):
    """跟 SYNOX 自己 tests/conftest.py 的 make_bot 同一個介面（script、adapter、llm、raw、supports_tools、pack_dirs），
    差在「SYNOX 自己的 pack 資料夾」換成這個 repo 的 packs/（loader.PACK_DIRS，connect／discover／reload 也看這裡）。
    從 SYNOX 搬來的 connector 測試（test_notion、test_todoist、test_gcal、test_apple_reminders）用它；
    伺服器版先把每個工具鎖成 core（＝擁有者安裝時選「核心」）。"""
    from synox.app import build
    from synox.config import SynoxConfig
    from synox.llm.fake import FakeAdapter
    from synox.packs import loader
    from synox.vault import Vault
    monkeypatch.setattr(loader, "PACK_DIRS", (PACKS,))

    def factory(script=None, *, adapter=None, llm=None, raw=None, supports_tools=True, pack_dirs=None):
        data = tmp_path / "data"
        cfg = {"owner_id": OWNER_ID, "data_dir": str(data), "vault": {"mode": "file"},
               "llm": llm or {"kind": "fake"}, **(raw or {})}
        config = SynoxConfig.from_dict(cfg, require_token=False, root=str(tmp_path))
        api = FakeChannel()
        vault = Vault(b"k" * 32, str(data))
        if adapter is None and (llm or {}).get("kind") != "none":
            adapter = FakeAdapter(script, supports_tools=supports_tools)
        if config.mode == "server":
            trust_core(data, *_synox.pack_folders())
        app = build(config, home=api, adapter=adapter, vault=vault,
                    pack_dirs=list(pack_dirs) if pack_dirs is not None else [PACKS])
        return SynoxBot(app, api)
    return factory


@pytest.fixture
def owner():
    return OWNER_ID
