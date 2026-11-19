"""📁 projects：engine（資料與規則、掃資料夾）、指令、畫面動作與讀、以及這個工具自己的安全規矩（靜態檢查）。

不連網、不開埠、不碰真的專案資料夾：掃描只掃 tmp_path 底下自己建的假資料夾。資料全是假的（「範例專案」）。
載入方式跟 money 的測試一樣：照 SYNOX loader 把 packs/projects/pack.py 載成套件（不經真的 SYNOX 載入，所以不吃 packs.lock）。
"""

import ast
import importlib.util
import os
import re
import sys
import time
from datetime import datetime

import pytest

from conftest import FakePack, FakeStore, PACKS
from isdtelekit.testing import OWNER_ID, BotHarness
from synox.packs import manifest as synox_manifest

PROJECTS = os.path.join(PACKS, "projects")
PACKAGE = "synoxlibs_test_projects"
NOW = datetime(2030, 3, 10, 12, 0).timestamp()           # 假的「現在」
DAY = 86400


def load_projects():
    found = sys.modules.get(PACKAGE)
    if found is not None:
        return found
    spec = importlib.util.spec_from_file_location(PACKAGE, os.path.join(PROJECTS, "pack.py"),
                                                  submodule_search_locations=[PROJECTS])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


class U:
    def __init__(self, tg_id):
        self.tg_id = tg_id


class AppPack(FakePack):
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


class ProjBot(BotHarness):
    def read(self, name, params=None, user=OWNER_ID):
        return self.pack.app_reads[name](U(user), dict(params or {}))

    def act(self, name, values=None, user=OWNER_ID):
        return self.pack.app_actions[name](U(user), dict(values or {}))


@pytest.fixture
def bot(tmp_path, monkeypatch):
    module = load_projects()
    model = sys.modules[PACKAGE + ".engine.model"]
    monkeypatch.setattr(model, "now", lambda: NOW)
    holder = {}

    def install(app):
        m = synox_manifest.read(PROJECTS, strict=True)
        app.registry.define_group(m.group, m.title, m.summary, owner_only=True)
        pack = AppPack(app, m, FakeStore(False), str(tmp_path / "data"))
        module.install(app, pack)
        holder["pack"] = pack

    b = BotHarness.create(tmp_path, install=install)
    b.__class__ = ProjBot
    b.pack, b.model = holder["pack"], model
    b.scan = sys.modules[PACKAGE + ".engine.scan"]
    return b


def make_folder(base, name, age_days, files=("a.txt",)):
    folder = os.path.join(base, name)
    os.makedirs(folder)
    stamp = NOW - age_days * DAY
    for f in files:
        path = os.path.join(folder, f)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.utime(path, (stamp, stamp))
    return folder


def projects_of(bot):
    return bot.read("projects")["projects"]


# ── 1. 一輪：加、改、暫停、回來、停止追蹤、復原 ─────────────────────────────────────

def test_add_update_pause_resume_rm_restore(bot):
    made = bot.act("add", {"name": "範例專案"})["project"]
    pid = made["id"]
    assert made["state"] == "active" and made["pinned"] is False
    with pytest.raises(Exception):
        bot.act("add", {"name": "   "})

    out = bot.act("update", {"id": pid, "status": "後端做完了", "next": "接前端", "tags": "demo, 練習, demo", "pinned": "yes"})["project"]
    assert (out["status"], out["next"], out["tags"], out["pinned"]) == ("後端做完了", "接前端", ["demo", "練習"], True)
    assert bot.act("update", {"id": pid, "next": "-"})["project"]["next"] == ""           # - ＝清掉

    other = bot.act("add", {"name": "另一個範例"})["project"]["id"]
    paused = bot.act("pause", {"id": pid, "reason": "detour", "reason_note": "先做別的", "reason_ref": other,
                               "resume_at": "2030-03-20", "next": "回來先跑測試"})["project"]
    assert paused["state"] == "paused" and paused["reason"] == "detour" and paused["reason_ref"] == other
    assert paused["resume_days"] == 10 and paused["resume_due"] is False and paused["paused_days"] == 0
    assert len(paused["log"]) >= 2

    back = bot.act("resume", {"id": pid})["project"]
    assert back["state"] == "active" and back["resume_at"] == "" and back["paused_at"] == 0
    assert back["reason"] == "detour" and back["reason_note"] == "先做別的"        # 原因留著

    gone = bot.act("rm", {"id": pid})
    assert gone["name"] == "範例專案" and [p["id"] for p in projects_of(bot)] == [other]
    assert bot.act("restore", {"id": pid})["id"] == pid
    assert {p["id"] for p in projects_of(bot)} == {pid, other}
    with pytest.raises(Exception):
        bot.act("rm", {"id": "nope1234"})
    with pytest.raises(Exception):
        bot.act("restore", {"id": "nope1234"})
    assert "範例專案" in bot.pack.app_describe["rm"](U(OWNER_ID), {"id": pid})


def test_validation_and_normalization(bot):
    pid = bot.act("add", {"name": "範例"})["project"]["id"]
    for bad in ({"state": "nope"}, {"reason": "nope"}, {"resume_at": "2030-13-45"}, {"name": "-"}):
        with pytest.raises(Exception):
            bot.act("update", {"id": pid, **bad})
    # 手改壞的資料：缺欄位、壞狀態、壞日期都不能讓清單打不開
    store = bot.pack.store.of(OWNER_ID)
    store.save({"projects": [{"name": "壞的", "state": "weird", "resume_at": "亂寫"}, "不是 dict"], "trash": "x"})
    rows = projects_of(bot)
    assert len(rows) == 1 and rows[0]["state"] == "active" and rows[0]["resume_days"] is None and rows[0]["id"]


def test_reorder_and_pinned_list(bot):
    ids = [bot.act("add", {"name": n})["project"]["id"] for n in "abc"]
    assert bot.act("reorder", {"ids": ",".join(reversed(ids))})["ok"] is True
    assert [p["id"] for p in projects_of(bot)] == list(reversed(ids))
    bot.act("update", {"id": ids[0], "pinned": "yes"})
    assert [p["id"] for p in bot.read("projects")["pinned"]] == [ids[0]]


def test_custom_reasons(bot):
    r = bot.act("reason_add", {"label": "等對方回覆"})["reason"]
    assert r["custom"] and bot.act("reason_add", {"label": "等對方回覆"})["reason"]["id"] == r["id"]    # 同名＝同一顆
    pid = bot.act("add", {"name": "範例"})["project"]["id"]
    assert bot.act("pause", {"id": pid, "reason": r["id"]})["project"]["reason"] == r["id"]
    bot.act("reason_retire", {"id": r["id"]})
    data = bot.read("projects")
    assert r["id"] not in [x["id"] for x in data["reasons"]] and r["id"] in [x["id"] for x in data["legacy_reasons"]]
    assert projects_of(bot)[0]["reason"] == r["id"]                                  # 舊紀錄照樣在
    with pytest.raises(Exception):
        bot.act("reason_retire", {"id": "c0000000"})
    with pytest.raises(Exception):
        bot.act("reason_add", {"label": "  "})


def test_states_cold_due_counts(bot, tmp_path):
    base = str(tmp_path)
    old = make_folder(base, "old", 45)
    fresh = make_folder(base, "fresh", 1)
    ids = {}
    for name, path, state in (("old", old, "active"), ("fresh", fresh, "active"), ("done", old, "done"), ("mirror", old, "mirror")):
        ids[name] = bot.act("add", {"name": name, "path": path})["project"]["id"]
        bot.act("update", {"id": ids[name], "state": state})
    before = {p["name"]: p for p in projects_of(bot)}
    assert not any(p["is_cold"] for p in before.values())                       # 沒掃過：不冤枉人
    assert bot.act("scan", {})["scanned"] == 4
    after = {p["name"]: p for p in projects_of(bot)}
    assert after["old"]["is_cold"] and after["old"]["idle_days"] == 45 and after["old"]["idle_is_real"]
    assert not after["fresh"]["is_cold"] and after["fresh"]["idle_days"] == 1
    assert not after["done"]["is_cold"] and not after["mirror"]["is_cold"]      # 靜止的不算冷掉
    assert bot.read("projects")["counts"]["cold"] == 1
    bot.act("update", {"id": ids["fresh"], "resume_at": "2030-03-09"})
    bot.act("update", {"id": ids["done"], "resume_at": "2030-03-09"})
    c = bot.read("projects")["counts"]
    assert c["due_back"] == 1 and c["done"] == 1 and c["mirror"] == 1
    assert [d["name"] for d in bot.read("due_back")["due"]] == ["fresh"]


# ── 2. 掃描：只讀、只看使用者給的路徑 ───────────────────────────────────────────────

def test_scan_one_git_files_missing_relative(bot, tmp_path):
    scan = bot.scan
    git = make_folder(str(tmp_path), "repo", 20)
    logs = os.path.join(git, ".git", "logs")
    os.makedirs(logs)
    commit_ts = int(NOW - 3 * DAY)
    with open(os.path.join(logs, "HEAD"), "w", encoding="utf-8") as f:
        f.write(f"{'0' * 40} {'a' * 40} Test User <test@example.invalid> {commit_ts - 100} +0800\tcommit (initial): one\n"
                f"{'a' * 40} {'b' * 40} Test User <test@example.invalid> {commit_ts} +0800\tcommit: two\n")
    got = scan.scan_one(git)
    assert got == {"last_active": commit_ts, "last_commit": commit_ts, "source": "git"}
    plain = scan.scan_one(make_folder(str(tmp_path), "plain", 7))
    assert plain["source"] == "files" and plain["last_commit"] == 0 and plain["last_active"] == int(NOW - 7 * DAY)
    assert scan.scan_one(os.path.join(str(tmp_path), "nope"))["source"] == "missing"
    assert scan.scan_one("")["source"] == "nopath"
    assert scan.scan_one("relative/folder")["source"] == "missing"            # 相對路徑不收
    assert scan.resolve("relative/folder") == ("", False)
    assert scan.last_commit(str(tmp_path)) is None                             # 不是 git repo 不是錯
    skip = make_folder(str(tmp_path), "skip", 30)
    os.makedirs(os.path.join(skip, "node_modules"))                            # 被工具動來動去的資料夾不算
    assert scan.newest_mtime(skip) == int(NOW - 30 * DAY)


def test_scan_never_writes(bot, tmp_path):
    folder = make_folder(str(tmp_path), "keep", 5, files=("a.txt", "b.txt"))
    before = sorted((n, os.stat(os.path.join(folder, n)).st_mtime) for n in os.listdir(folder))
    pid = bot.act("add", {"name": "範例", "path": folder})["project"]["id"]
    bot.act("scan", {"id": pid})
    assert sorted((n, os.stat(os.path.join(folder, n)).st_mtime) for n in os.listdir(folder)) == before
    with pytest.raises(Exception):
        bot.act("scan", {"id": "nope1234"})
    assert bot.act("scan", {"id": ""})["scanned"] == 1


def test_path_check_and_view(bot, tmp_path):
    folder = make_folder(str(tmp_path), "here", 1)
    assert bot.read("path_check", {"path": folder})["exists"] is True
    assert bot.read("path_check", {"path": os.path.join(folder, "x")})["exists"] is False
    rel = bot.read("path_check", {"path": "some/where"})
    assert rel["exists"] is False and rel["relative"] is True
    pid = bot.act("add", {"name": "範例", "path": folder})["project"]
    assert pid["path_exists"] is True and pid["resolved_path"] == folder


# ── 3. 指令 ──────────────────────────────────────────────────────────────────────

def test_commands(bot, tmp_path):
    owner = OWNER_ID
    bot.say(owner, "/proj 範例專案")
    assert "開始追蹤" in bot.last(owner)
    pid = projects_of(bot)[0]["id"]
    bot.say(owner, f"/proj_update {pid} next=接前端 status=做到一半")
    assert "▸ 接前端" in bot.last(owner)
    bot.say(owner, f"/proj_pause {pid} reason=block resume_at=2030-03-01")
    assert "⏸" in bot.last(owner) and "說好 9 天前回來看" in bot.last(owner)
    bot.say(owner, "/proj_list due")
    assert "範例專案" in bot.last(owner)
    bot.say(owner, f"/proj_show {pid}")
    assert "卡住了" in bot.last(owner) and "回來先做什麼：接前端" in bot.last(owner)
    bot.say(owner, f"/proj_resume {pid}")
    assert "▶" in bot.last(owner)
    bot.say(owner, f"/proj_pause {pid} reason=bogus")
    assert "❌" in bot.last(owner)
    bot.say(owner, "/proj_show zzzzzzzz")
    assert "❌" in bot.last(owner)
    bot.say(owner, "/proj_list cold")
    assert "沒有符合" in bot.last(owner)
    bot.say(owner, "/proj_list 亂寫")
    assert "❌" in bot.last(owner)
    bot.say(owner, "/proj_scan")
    assert "掃描了 1 個專案" in bot.last(owner)
    bot.say(owner, f"/proj_rm {pid}")
    assert "停止追蹤" in bot.last(owner) and projects_of(bot) == []
    bot.say(owner, f"/proj_restore {pid}")
    assert "復原" in bot.last(owner) and len(projects_of(bot)) == 1
    bot.say(owner, "/proj_reason_add 等對方回覆")
    assert "多了一個原因" in bot.last(owner)


def test_effects_and_app_actions_match_commands(bot):
    from isdtelekit.core.registry import DANGER
    reg = bot.app.registry
    assert reg.node("proj.rm").effect == DANGER
    declared = synox_manifest.read(PROJECTS, strict=True).web["app"]["actions"]
    for name, effect in declared.items():
        assert str(reg.node(f"proj.{name}").effect).lower() == effect.lower(), name
        assert name in bot.pack.app_actions, name
    assert set(bot.pack.app_reads) == {"projects", "path_check", "due_back"}


def test_each_user_has_their_own_data(tmp_path, monkeypatch):
    module = load_projects()
    holder = {}

    def install(app):
        m = synox_manifest.read(PROJECTS, strict=True)
        app.registry.define_group(m.group, m.title, m.summary, owner_only=False)
        pack = AppPack(app, m, FakeStore(True), str(tmp_path / "data"))
        module.install(app, pack)
        holder["pack"] = pack

    b = BotHarness.create(tmp_path, install=install)
    b.__class__ = ProjBot
    b.pack = holder["pack"]
    b.act("add", {"name": "我的"}, user=100)
    assert [p["name"] for p in b.read("projects", user=100)["projects"]] == ["我的"]
    assert b.read("projects", user=555)["projects"] == []


# ── 4. 這個工具自己的安全規矩（跟 tests/test_gates.py 的通用規則互補）───────────────

def sources():
    out = {}
    for base, dirs, files in os.walk(PROJECTS):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                with open(os.path.join(base, f), encoding="utf-8") as fh:
                    out[os.path.relpath(os.path.join(base, f), PROJECTS).replace(os.sep, "/")] = fh.read()
    return out


def test_static_rules_files_only_in_scan_py_and_read_only():
    allowed = ("re", "time", "datetime", "uuid", "os", "isdtelekit", "synox")
    forbidden = ("requests", "urllib", "http", "socket", "ssl", "subprocess", "multiprocessing", "ctypes", "importlib", "pickle",
                 "marshal", "shutil", "pathlib", "asyncio", "threading", "tempfile")
    for rel, text in sources().items():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                mods = [node.module or ""]
            else:
                continue
            for mod in mods:
                top = mod.split(".")[0]
                assert top not in forbidden, f"{rel}: import {mod}"
                assert top in allowed, f"{rel}: unexpected import {mod}"
                if top == "os":
                    assert rel == "engine/scan.py", f"{rel}: os 只准在 engine/scan.py"
        for pattern in (r"\bos\.system\b", r"\bos\.popen\b", r"\bsubprocess\b", r"\beval\(", r"\bexec\(", r"__import__",
                        r"\bos\.environ\b", r"\bgetenv\b", r"app\.jobs", r"\bapi\.post\(", r"pack\.path\("):
            assert not re.search(pattern, text), f"{rel}: {pattern}"
        if rel != "engine/scan.py":
            assert "open(" not in text.replace("isopen(", "") or rel == "engine/scan.py", rel
    scan_text = sources()["engine/scan.py"]
    # 只讀：open 只有一次、而且是 "rb"；沒有任何寫、刪、改名的呼叫
    assert re.findall(r"\bopen\(([^)]*)\)", scan_text) == ['log, "rb"']
    for bad in (r"\.write", r"os\.remove", r"os\.unlink", r"os\.rename", r"os\.replace", r"os\.makedirs", r"os\.mkdir", r"os\.rmdir",
                r"os\.utime", r"os\.chmod"):
        assert not re.search(bad, scan_text), bad


def test_no_leftover_private_names_or_old_app_symbols():
    """從舊的個人管家搬來時不能留下的東西：舊的品牌名、本機路徑、沒搬的功能的名字。"""
    allowed_words = ()
    blob = "\n".join(sources().values())
    for root, _dirs, files in os.walk(PROJECTS):
        if "__pycache__" in root:
            continue
        for f in files:
            if f.endswith((".md", ".json", ".js", ".html", ".css")):
                with open(os.path.join(root, f), encoding="utf-8") as fh:
                    blob += "\n" + fh.read()
    for needle in ("ISD" + "PCS", "LS" + "C", "lsc.", "C:\\Users", "C:/Users", "/Users/", "storeio", "config.data_path", "subst_vars",
                   "projects.local.json", "projsync", "weekly.", "direction.", "openers", "open_project"):
        assert needle not in blob.replace("README", ""), needle


def test_js_has_no_inline_script_innerhtml_or_network():
    app = os.path.join(PROJECTS, "app")
    html = open(os.path.join(app, "index.html"), encoding="utf-8").read()
    assert "<script src=\"/_synox/kit.js\">" in html and html.index("kit.js") < html.index("js/main.js")
    assert not re.search(r"<script(?![^>]*src)", html) and " onclick=" not in html and "<iframe" not in html
    for name in ("main.js", "dom.js"):
        js = open(os.path.join(app, "js", name), encoding="utf-8").read()
        for bad in ("innerHTML", "fetch(", "XMLHttpRequest", "WebSocket", "localStorage", "setInterval", "eval(", "http://", "https://"):
            assert bad not in js, f"{name}: {bad}"
