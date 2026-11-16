"""記帳工具的測試工具（不連網、不開埠、不叫 AI）。

- load_money()：照「多檔工具」的方式把 packs/money/pack.py 載成套件（有 __path__，bridge/、engine/ 用相對 import）。
  SYNOX 本體的 loader 支援 python.package 之前（W1），測試先用這個；之後 test_contract 的 xfail 那條會轉綠。
- make_money(per_user=True, now=TODAY)：BotHarness＋假的 pack（跟 tests/conftest.py 的 make_bot 同一套假東西，
  多了工具服務的 app_read／app_action／app_export）；「現在」固定在 TODAY（假日期）。
- 兩個假使用者：OWNER（100）與 OTHER（555）。
- run_node_check(腳本, *參數)：跑 tests/money/js/ 裡的純 node 檢查（不開瀏覽器、不開埠），回它最後一行印的 JSON。
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

import pytest

from conftest import OTHER_ID, PACKS, FakePack, FakeStore  # tests/conftest.py（根目錄那一份）
from isdtelekit.testing import OWNER_ID, BotHarness
from synox.packs import manifest as synox_manifest

MONEY = os.path.join(PACKS, "money")
PACKAGE = "synoxlibs_test_money"
TODAY = datetime(2030, 1, 15, 12, 30)          # 假的「今天」（不用真實日期）
OWNER = OWNER_ID
OTHER = OTHER_ID
USERS = (OWNER, OTHER)
NODE = shutil.which("node")
JS_CHECKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "js")


def run_node_check(script, *args):
    """node tests/money/js/<script> <args…> → 最後一行印的 JSON。node 失敗就讓測試失敗（印出 stderr）。"""
    out = subprocess.run([NODE, os.path.join(JS_CHECKS, script), *args], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


class U:
    """最小的使用者物件（SYNOX 給工具的 user 都有整數 tg_id）。"""

    def __init__(self, tg_id):
        self.tg_id = tg_id


def load_money():
    """packs/money/pack.py → 套件模組（同一輪測試只載一次；不寫 __pycache__ 到工具資料夾）。"""
    found = sys.modules.get(PACKAGE)
    if found is not None:
        return found
    spec = importlib.util.spec_from_file_location(PACKAGE, os.path.join(MONEY, "pack.py"),
                                                  submodule_search_locations=[MONEY])
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
    return synox_manifest.read(MONEY, strict=True)


class AppPack(FakePack):
    """tests/conftest.py 的 FakePack＋工具服務（跟 SYNOX loader 的 Pack 一樣：沒宣告的名字掛了就 ValueError）。"""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.app_reads, self.app_actions, self.app_describe, self.app_exports = {}, {}, {}, {}

    def path_of(self, user, *parts):
        """跟 SYNOX 一樣：不准絕對路徑、不准用 .. 跑出這個人的資料夾。"""
        target = super().path_of(user, *parts)
        root = os.path.normpath(super().path_of(user))
        if any(os.path.isabs(p) for p in parts) or os.path.commonpath([root, os.path.normpath(target)]) != root:
            raise ValueError(f"path_of: outside the user's folder: {parts!r}")
        return os.path.normpath(target)

    def _decl(self, kind):
        app = (self.manifest.web or {}).get("app") or {}
        return list(app.get(kind) or [])

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

    def app_export(self, name, fn):
        if name not in self._decl("exports"):
            raise ValueError(f"app export {name!r} is not declared")
        self.app_exports[name] = fn


class MoneyBot(BotHarness):
    module = None
    pack = None

    def node(self, name):
        return self.app.registry.node(f"{self.pack.manifest.group}.{name}")

    def command(self, name, user, text=""):
        """直接叫指令的 handler（不經權限；兩個使用者都能測）→ 回這次送出的最後一句。"""
        before = len(self.texts())
        self.node(name).fn(self.api, user, U(user), text.split())
        sent = self.texts()[before:]
        return sent[-1] if sent else ""

    def read(self, name, user, params=None):
        return self.pack.app_reads[name](U(user), dict(params or {}))

    def act(self, name, user, values=None):
        return self.pack.app_actions[name](U(user), dict(values or {}))


@pytest.fixture
def make_money(tmp_path, monkeypatch):
    def factory(per_user=True, now=TODAY):
        module = load_money()
        monkeypatch.setattr(module.session, "now", lambda: now)
        holder = {}

        def install(app):
            m = read_manifest()
            app.registry.define_group(m.group, m.title, m.summary, owner_only=not per_user)
            pack = AppPack(app, m, FakeStore(per_user), str(tmp_path / "data"))
            module.install(app, pack)
            holder["pack"] = pack

        bot = BotHarness.create(tmp_path, install=install)
        bot.__class__ = MoneyBot
        bot.module, bot.pack = module, holder["pack"]
        return bot
    return factory
