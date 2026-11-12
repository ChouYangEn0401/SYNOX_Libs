"""scripts/_synox.py 找 SYNOX 的順序：SYNOX_HOME → 這個 Python 自己 import 得到的 → 並排的 ../SYNOX（後備）→ 說清楚的錯誤。
全部用暫存資料夾與假的 find_spec；不 import 別的 SYNOX。"""

import os
import types

import pytest

import _synox


def fake_synox(folder):
    pkg = folder / "src" / "synox"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    return str(folder)


def no_spec(name):
    return None


def test_synox_home_wins_and_must_be_right(tmp_path):
    root = fake_synox(tmp_path / "MySynox")
    assert _synox.locate({"SYNOX_HOME": root}, find_spec=no_spec, libs_root=str(tmp_path / "libs")) == (root, "env")
    with pytest.raises(_synox.SynoxNotFound, match="不是 SYNOX 資料夾"):
        _synox.locate({"SYNOX_HOME": str(tmp_path / "nope")}, find_spec=no_spec)


def test_interpreter_that_already_has_synox(tmp_path):
    root = fake_synox(tmp_path / "Elsewhere" / "SYNOX-x")
    spec = types.SimpleNamespace(origin=os.path.join(root, "src", "synox", "__init__.py"))
    assert _synox.locate({}, find_spec=lambda n: spec, libs_root=str(tmp_path / "libs")) == (root, "python")
    site = types.SimpleNamespace(origin=str(tmp_path / "venv" / "Lib" / "site-packages" / "synox" / "__init__.py"))
    assert _synox.locate({}, find_spec=lambda n: site, libs_root=str(tmp_path / "libs")) == ("", "python")


def test_sibling_is_only_a_fallback(tmp_path):
    libs = tmp_path / "SYNOX_Libs"
    libs.mkdir()
    with pytest.raises(_synox.SynoxNotFound) as e:
        _synox.locate({}, find_spec=no_spec, libs_root=str(libs))
    assert "SYNOX_HOME" in str(e.value) and "import 不到 synox" in str(e.value)
    root = fake_synox(tmp_path / "SYNOX")
    assert _synox.locate({}, find_spec=no_spec, libs_root=str(libs)) == (os.path.abspath(root), "sibling")


def test_this_run_found_synox():
    root, how = _synox.locate()
    assert how in ("env", "python", "sibling")
