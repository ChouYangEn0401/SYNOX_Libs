"""整個工具（pack.py＋bridge.py）的測試用假環境：BotHarness＋假的 pack（含工具服務的 app_read／app_action）。"""
import importlib.util
import os
import sys

import pytest

from conftest import PACKS, FakePack, FakeStore  # tests/conftest.py（根目錄那一份）
from isdtelekit.testing import OWNER_ID, BotHarness
from synox.packs import manifest as synox_manifest

HABITS = os.path.join(PACKS, "habits")
PACKAGE = "synoxlibs_test_habits"
OWNER = OWNER_ID
OTHER = 555


class U:
    """最小的使用者物件（SYNOX 給工具的 user 都有整數 tg_id）。"""

    def __init__(self, tg_id):
        self.tg_id = tg_id


def load_habits():
    """packs/habits/pack.py → 套件模組（同一輪測試只載一次；不寫 __pycache__ 到工具資料夾）。"""
    found = sys.modules.get(PACKAGE)
    if found is not None:
        return found
    spec = importlib.util.spec_from_file_location(PACKAGE, os.path.join(HABITS, "pack.py"),
                                                  submodule_search_locations=[HABITS])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except BaseException:
        for name in [n for n in sys.modules if n == PACKAGE or n.startswith(PACKAGE + ".")]:
            del sys.modules[name]
        raise
    finally:
        sys.dont_write_bytecode = before
    return module


def read_manifest():
    return synox_manifest.read(HABITS, strict=True)


class AppPack(FakePack):
    """FakePack＋工具服務（跟 SYNOX loader 的 Pack 一樣：沒宣告的名字掛了就 ValueError）。"""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.app_reads, self.app_actions, self.app_describe = {}, {}, {}

    def _decl(self, kind):
        return list(((self.manifest.web or {}).get("app") or {}).get(kind) or [])

    def app_read(self, name, fn):
        if name not in self._decl("reads"):
            raise ValueError(f"app read {name!r} is not declared")
        self.app_reads[name] = fn

    def app_action(self, name, fn, *, describe=None):
        if name not in self._decl("actions"):
            raise ValueError(f"app action {name!r} is not declared")
        self.app_actions[name] = fn
        if describe is not None:
            self.app_describe[name] = describe


class HabitsBot(BotHarness):
    module = None
    pack = None

    def node(self, name):
        return self.app.registry.node(f"{self.pack.manifest.group}.{name}")

    def command(self, name, user, text=""):
        """直接叫指令的 handler（不經權限）→ 回這次送出的最後一句。"""
        before = len(self.texts())
        self.node(name).fn(self.api, user, U(user), text.split())
        sent = self.texts()[before:]
        return sent[-1] if sent else ""

    def read(self, name, user, params=None):
        return self.pack.app_reads[name](U(user), dict(params or {}))

    def act(self, name, user, values=None):
        return self.pack.app_actions[name](U(user), dict(values or {}))


@pytest.fixture
def make_habits(tmp_path, when, monkeypatch):
    def factory(per_user=True):
        module = load_habits()
        monkeypatch.setattr(module.bridge.clock, "now", lambda: when.value)       # 套件裡的那一份 clock
        holder = {}

        def install(app):
            m = read_manifest()
            app.registry.define_group(m.group, m.title, m.summary, owner_only=not per_user)
            pack = AppPack(app, m, FakeStore(per_user), str(tmp_path / "data"))
            module.install(app, pack)
            holder["pack"] = pack

        bot = BotHarness.create(tmp_path, install=install)
        bot.__class__ = HabitsBot
        bot.module, bot.pack = module, holder["pack"]
        return bot
    return factory
