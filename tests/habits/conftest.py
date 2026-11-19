"""習慣與里程碑的測試工具（不連網、不開埠、不叫 AI）。

- 引擎載成獨立的套件 `habits_engine`（packs/habits/engine；裡面一律相對 import），所以不靠 packs.lock、不靠 SYNOX 載入器。
- 每一條測試自動有自己的記憶體資料（MemoryBackend），「現在」固定在 NOW（假日期，2030-01-15 星期二）。
"""
import importlib
import importlib.util
import os
import sys
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE = os.path.join(ROOT, "packs", "habits", "engine")
NOW = datetime(2030, 1, 15, 21, 30)


def _load_engine():
    if "habits_engine" in sys.modules:
        return sys.modules["habits_engine"]
    spec = importlib.util.spec_from_file_location(
        "habits_engine", os.path.join(ENGINE, "__init__.py"), submodule_search_locations=[ENGINE])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["habits_engine"] = mod
    spec.loader.exec_module(mod)
    for name in ("clock", "storage", "links", "routines", "milestones"):
        importlib.import_module(f"habits_engine.{name}")
    return mod


_load_engine()
from habits_engine import clock, links, milestones, routines, storage  # noqa: E402


class Clock:
    def __init__(self):
        self.value = NOW

    def set(self, value):
        self.value = value


@pytest.fixture
def when(monkeypatch):
    c = Clock()
    monkeypatch.setattr(clock, "now", lambda: c.value)
    return c


@pytest.fixture
def mem(when):
    backend = storage.MemoryBackend()
    with storage.bound(backend):
        yield backend


@pytest.fixture(autouse=True)
def _no_links():
    links.set_habit_blocks(None)
    yield
    links.set_habit_blocks(None)


@pytest.fixture
def R(mem):
    return routines


@pytest.fixture
def M(mem):
    return milestones
