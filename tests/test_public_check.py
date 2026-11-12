# -*- coding: utf-8 -*-
"""scripts/public_check.py（公開版檢查）：這個 repo 現在要是乾淨的；每一種規則抓得到、佔位與假資料放得過。
要抓的「壞字串」一律在執行時拼出來，這個檔本身才不會被自己抓到。暫存的 git repo 只在 tmp_path 裡 git init，不連網。"""
import os
import subprocess

import pytest

import public_check as pc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AT = "@"


def kinds(text, words=()):
    return [k for _no, k in pc.scan_text(text, words)]


def test_this_repo_is_clean(capsys):
    """公開前要過：目前的檔案（git 追蹤的＋沒被忽略的新檔）沒有任何發現。"""
    assert pc.main(["--repo", ROOT]) == 0, capsys.readouterr().out


@pytest.mark.parametrize("line, kind", [
    ("path = 'C:" + "\\Users\\" + "realperson\\Documents\\x'", "本機絕對路徑"),
    ("open('/home/" + "realperson/.config')", "本機絕對路徑"),
    ("contact: someone.real" + AT + "gmail.com", "email"),
    ("TOKEN = '" + "ghp_" + "Zq8" * 12 + "'", "GitHub token"),
    ("x = '" + "GOCSPX-" + "k3Jd9Qw2LmZ8xPq4Rt7Vb1" + "'", "GOCSPX"),
    ("api_key = '" + "Zq8kP2mW9xL4vR7tB3nJ6hD1" + "'", "寫死的值"),
    ("owner_id = " + "7" + "481920365", "Telegram 長度"),
    ("PAGE = '" + "3f9a1c7e-58b2-4d06" + "-9e1f-a2c4b8d7e6f0" + "'", "UUID"),
    ("cid = '" + "48291" + "73650-q8w3e9r1t5y7u2i4o6p0.apps.googleusercontent.com'", "client id"),
])
def test_each_rule_finds_its_kind(line, kind):
    assert any(kind in k for k in kinds(line)), kinds(line)


@pytest.mark.parametrize("line", [
    "C:" + "\\Users\\<你>\\Documents\\SYNOX",                       # 佔位
    "me" + AT + "example.com、someone" + AT + "synox.invalid",       # 保留網域、佔位
    "TOKEN = '0123456789abcdef0123456789abcdef01234567'",           # 假的（123、abc）
    "PAGE_ID = '11111111-2222-3333-4444-555555555555'",             # 字元變化很少＝假的
    "CID = '1234567890-abcdefg.apps.googleusercontent.com'",
    "owner_id = 100",
    "real = 'C:" + "\\Users\\realperson'  # public-check: allow（文件範例）",
])
def test_placeholders_and_fakes_pass(line):
    assert kinds(line) == []


def test_private_words_are_found_but_not_printed(tmp_path):
    assert any("使用者名稱" in k for k in kinds("written by Rea" + "lPerson42 at home", ["RealPerson42"]))
    assert kinds("nothing here", ["RealPerson42"]) == []


def test_sensitive_files():
    assert pc.sensitive_name("data/money.json")
    assert pc.sensitive_name("packs/x/config.json")
    assert pc.sensitive_name("notes.json.enc")
    assert pc.sensitive_name("public_check.local.txt")
    assert not pc.sensitive_name("config.example.json")
    assert not pc.sensitive_name("packs/notes/pack.json")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Placeholder Bot")
    _git(root, "config", "user.email", "bot" + AT + "example.com")
    (root / "README.md").write_text("# ok\n", encoding="utf-8")
    return root


def test_main_reports_and_exits_1_without_printing_the_secret(repo, capsys):
    planted = "Zq8kP2mW9x" + "L4vR7tB3nJ6hD1"
    (repo / "tool.py").write_text("api_key = '" + planted + "'\n", encoding="utf-8")
    (repo / "data").mkdir()
    (repo / "data" / "money.json").write_text("{}", encoding="utf-8")
    assert pc.main(["--repo", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "tool.py:1" in out and "data/money.json" in out and planted not in out


def test_local_words_file_is_read_and_must_stay_untracked(repo, capsys):
    (repo / pc.LOCAL_WORDS).write_text("# 擁有者自己的名字\nRealPerson42\n", encoding="utf-8")
    (repo / "notes.md").write_text("made by Real" + "Person42\n", encoding="utf-8")
    assert "RealPerson42" in pc.private_words(str(repo))
    assert pc.main(["--repo", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "notes.md:1" in out and "使用者名稱" in out and "RealPerson42" not in out
    assert "public_check.local.txt" in out                      # 沒被 git 忽略＝會被提交，也算一項


def test_history_reports_commit_email_masked(repo, capsys):
    _git(repo, "config", "user.email", "real.person" + AT + "gmail.com")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    assert pc.main(["--repo", str(repo), "--history"]) == 1
    out = capsys.readouterr().out
    assert "r…" + AT + "gmail.com" in out and "real.person" not in out


def test_gitignore_keeps_the_local_words_file_out():
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
        assert pc.LOCAL_WORDS in f.read().split()
