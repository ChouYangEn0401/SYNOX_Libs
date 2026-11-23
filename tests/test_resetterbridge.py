"""resetterbridge（packs/_shared 的正本）：找 resetter、組 argv、讀最後一份 JSON。全用假資料夾，不叫任何程式。"""

import os

import pytest

import resetterbridge as rb


def make_home(tmp_path, venv=True):
    home = tmp_path / "resetter"
    (home / "claude_timer").mkdir(parents=True)
    (home / "claude_timer" / "__init__.py").write_text("")
    py = home / ".venv" / "bin" / "python"
    if venv:
        py.parent.mkdir(parents=True)
        py.write_text("")
    return home, py


def test_resolve_finds_home_and_venv_python(tmp_path):
    home, py = make_home(tmp_path)
    assert rb.resolve_resetter({"home": str(home)}, "/x_setup") == (str(py), str(home))


def test_resolve_explicit_python_wins(tmp_path):
    home, _ = make_home(tmp_path)
    other = tmp_path / "py"
    other.write_text("")
    assert rb.resolve_resetter({"home": str(home), "python": str(other)}, "/x_setup")[0] == str(other)


@pytest.mark.parametrize("settings, word", [({}, "還沒告訴我"), ({"home": "/nope/none"}, "claude_timer")])
def test_resolve_problems_name_the_setup_command(settings, word):
    with pytest.raises(rb.BridgeProblem) as e:
        rb.resolve_resetter(settings, "/x_setup")
    assert word in e.value.text and "/x_setup" in e.value.text


def test_resolve_without_venv_or_missing_python(tmp_path):
    home, _ = make_home(tmp_path, venv=False)
    with pytest.raises(rb.BridgeProblem, match=".venv"):
        rb.resolve_resetter({"home": str(home)}, "/x_setup")
    with pytest.raises(rb.BridgeProblem, match="找不到設定的 Python"):
        rb.resolve_resetter({"home": str(home), "python": str(tmp_path / "gone")}, "/x_setup")


def test_module_argv_only_known_entries_and_no_nul():
    assert rb.module_argv("py", "usage", ["--json"]) == ["py", "-m", "claude_timer.core.usage", "--json"]
    assert rb.module_argv("py", "cli", ["sessions", "--json"])[2] == "claude_timer.cli"
    with pytest.raises(rb.BridgeProblem):
        rb.module_argv("py", "os.system", [])
    with pytest.raises(rb.BridgeProblem):
        rb.module_argv("py", "usage", ["a\x00b"])


def test_last_json_takes_the_last_object_after_noise():
    out = "[WAIT] 等一下...\nnoise {not json\n{\"ok\": false}\n{\"ok\": true,\n \"n\": 2}\n"
    assert rb.last_json(out) == {"ok": True, "n": 2}
    assert rb.last_json("沒有 JSON") is None and rb.last_json("") is None and rb.last_json(None) is None
    assert rb.last_json('[1, 2]\n') is None
