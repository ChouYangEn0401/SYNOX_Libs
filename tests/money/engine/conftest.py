"""記帳引擎的測試工具（不靠 SYNOX、不連網、不叫 AI）。

- 引擎載成獨立的套件 `money_engine`（packs/money/engine；裡面一律相對 import），測試寫 `from money_engine import db`、
  `from money_engine.services import transactions as tx`。
- 每一條測試自動有自己的帳本資料夾（tmp_path/home，paths.use_home），「今天」固定在 TODAY（假日期）。
  沒有這個 context 時引擎一律丟 NoHome（不會退回任何共用位置）。
- 帳本資料夾裡先放 seed_test.json（假的起始資料，當成這本帳自己的 categories.json）：新帳本的類別、帳戶、設定照它建。
  要測內建通用版的：`db.connect(seed="generic")`，或用 fixture `generic_home`。
- 測試資料一律用假的（「範例便當店」「現金」「120」）；日期都在假的年份（TODAY 那一年前後）。

TODAY 是 2037-10-10（星期六）：引擎測試的日期大多落在「每期從 5 號開始」的十月那一期，星期幾也有意義
（午餐提醒、每週報告），這一年的月曆跟測試原本設計時用的那一年一模一樣，日期才能照原樣用。
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import sys
from datetime import date

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "packs", "money", "engine"))
PACKAGE = "money_engine"
TODAY = date(2037, 10, 10)
SEED = os.path.join(HERE, "seed_test.json")


def load_engine():
    """packs/money/engine → 套件 money_engine（同一輪測試只載一次；不寫 __pycache__ 到工具資料夾）。"""
    found = sys.modules.get(PACKAGE)
    if found is not None:
        return found
    spec = importlib.util.spec_from_file_location(PACKAGE, os.path.join(ENGINE, "__init__.py"),
                                                  submodule_search_locations=[ENGINE])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
        for name in ("errors", "paths", "clock", "ids", "db", "seed", "schemas"):
            importlib.import_module(f"{PACKAGE}.{name}")
        for file in sorted(os.listdir(os.path.join(ENGINE, "services"))):
            if file.endswith(".py") and file != "__init__.py":
                importlib.import_module(f"{PACKAGE}.services.{file[:-3]}")
    except BaseException:
        for name in [n for n in sys.modules if n == PACKAGE or n.startswith(PACKAGE + ".")]:
            del sys.modules[name]
        raise
    finally:
        sys.dont_write_bytecode = before
    return module


load_engine()


@pytest.fixture(autouse=True)
def home(tmp_path):
    """每一條測試自己的帳本資料夾＋固定的今天；放好假的起始資料（seed_test.json）。"""
    from money_engine import paths
    folder = tmp_path / "home"
    folder.mkdir()
    shutil.copyfile(SEED, folder / "categories.json")
    with paths.use_home(folder, today=TODAY):
        yield folder


@pytest.fixture
def generic_home(home):
    """這本帳用內建的通用版起始資料（拿掉測試用的 categories.json）。"""
    (home / "categories.json").unlink()
    return home


@pytest.fixture
def conn():
    from money_engine import db
    c = db.connect()
    yield c
    c.close()
