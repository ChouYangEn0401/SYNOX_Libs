"""共用的假東西：SDK 的 BotHarness ＋ 假的 pack.store（記憶體裡的 JSON），不連網、不開埠、不開 Telegram、不叫 AI。

SYNOX 本體從哪裡 import：環境變數 SYNOX_HOME，沒設就找跟這個 repo 並排的 ../SYNOX（只 import、不改它）。
pack.py 的載入方式照 SYNOX loader（spec_from_file_location ＋ exec，不把 pack 資料夾放進 sys.path），
所以這裡測到的就是 SYNOX 真的會執行的那一份程式。
"""

import importlib.util
import json
import os
import sys
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKS = os.path.join(ROOT, "packs")
SYNOX_HOME = os.environ.get("SYNOX_HOME") or os.path.join(os.path.dirname(ROOT), "SYNOX")
for sub in ("src", "vendor"):
    path = os.path.join(SYNOX_HOME, sub)
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)
sys.path.insert(0, os.path.join(PACKS, "_shared"))

from isdtelekit.testing import OWNER_ID, BotHarness  # noqa: E402

from synox.packs import manifest as synox_manifest  # noqa: E402

PACK_NAMES = ("calendar", "todo", "bullet")
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
    """SYNOX loader 的 Pack 物件最少要有的樣子：manifest、store、group、path()、web_route()。"""

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

    def web_route(self, route, fn):
        assert route in self.manifest.web.get("api", []), f"{route} not declared in pack.json"
        self.web_routes[route] = fn


def load_module(name):
    """照 SYNOX loader 的方式把 packs/<name>/pack.py 載成模組。"""
    path = os.path.join(PACKS, name, "pack.py")
    with open(path, "rb") as f:
        source = f.read()
    spec = importlib.util.spec_from_file_location(f"synoxpacks_test_{name}", path)
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
        names = names or PACK_NAMES
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


@pytest.fixture
def owner():
    return OWNER_ID
