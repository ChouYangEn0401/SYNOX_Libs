# -*- coding: utf-8 -*-
"""🎬 影片筆記：真的 SYNOX 載入 packs/video-notes，背後叫一支**假的** Video Notes
（tests/fixtures/fake_videonotes.py）。不下載影片、不跑 Whisper、不叫 AI、不開埠；子程序只有那支假程式。"""
import json
import os
import sys
import time

import pytest

import build as build_script
from conftest import PACK_NAMES, PACKS, ROOT, load_module

from synox.packs import manifest as synox_manifest
from synox.packs import scan as synox_scan

NAME = "video-notes"
FOLDER = os.path.join(PACKS, NAME)
FAKE = os.path.join(ROOT, "tests", "fixtures", "fake_videonotes.py")
URL = "https://example.com/watch?v=abc123"


@pytest.fixture
def vn_bot(tmp_path):
    """真的 SYNOX（同 conftest.synox_bot），先把這個工具鎖進 packs.lock（＝擁有者在安裝頁按過確認；
    現在的 SYNOX 不載入沒經過安裝頁的工具）。同一個 tmp_path 再叫一次＝同一份資料重開 SYNOX。"""
    from isdtelekit.testing import OWNER_ID, FakeChannel
    from synox.app import build
    from synox.config import SynoxConfig
    from synox.llm.fake import FakeAdapter
    from synox.packs import lock
    from synox.vault import Vault

    from conftest import SynoxBot

    def factory(script=None):
        data = tmp_path / "data"
        config = SynoxConfig.from_dict({"owner_id": OWNER_ID, "data_dir": str(data), "vault": {"mode": "file"},
                                        "llm": {"kind": "fake"}, "packs": {"enabled": [NAME]}},
                                       require_token=False, root=str(tmp_path))
        lock.approve(str(data), synox_manifest.read(FOLDER), None, OWNER_ID, via="test", trust="third-party")
        api = FakeChannel()
        app = build(config, home=api, adapter=FakeAdapter(script), vault=Vault(b"k" * 32, str(data)), pack_dirs=[PACKS])
        return SynoxBot(app, api)
    return factory


@pytest.fixture
def vn(vn_bot, owner, tmp_path):
    bot = vn_bot()
    out = tmp_path / "notes-out"
    bot.say(owner, f"/v2n_setup program={FAKE} python={sys.executable} out={out} timeout=5")
    assert "找得到 Video Notes" in bot.last(owner), bot.last(owner)
    bot.out = out
    return bot


def wait_done(bot, owner, n, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        bot.say(owner, f"/v2n_status {n}")
        first = bot.last(owner).splitlines()[0]
        if "排隊中" not in first and "進行中" not in first:
            return bot.last(owner)
        time.sleep(0.2)
    raise AssertionError(f"job {n} did not finish: {bot.last(owner)}")


def wait_log(bot, owner, n, text, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        bot.say(owner, f"/v2n_status {n}")
        if text in bot.last(owner):
            return bot.last(owner)
        time.sleep(0.2)
    raise AssertionError(f"{text!r} never showed up: {bot.last(owner)}")


def argv_calls(out):
    """假的 Video Notes 每次被叫記下的 {"argv", "urls"}（urls＝從 --file 讀到的）。"""
    with open(out / "_argv.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def job_files(tmp_path):
    """這個工具在 SYNOX 資料夾裡留下的明文檔（個人版：data/video-notes/files/）。"""
    base = tmp_path / "data" / NAME / "files"
    return sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()) if base.is_dir() else []


# ── 宣告、安全關卡 ─────────────────────────────────────────────────────

def test_manifest_is_public_owner_only_and_honest():
    m = synox_manifest.read(FOLDER, strict=True)
    assert "publish" not in m.raw and NAME in PACK_NAMES
    assert m.access == "owner" and m.surfaces == ["dm"] and m.data == "user" and m.storage == "vault"
    assert sorted(m.capabilities) == ["filesystem:any", "subprocess"]
    report = synox_scan.scan(m)
    assert report.problems(m) == [], report.problems(m)
    assert m.raw["callers"]["setup"] == ["human"]


def test_named_build_passes_synox_gates(tmp_path):
    """SYNOX 的第 1、2 關（zip_pack）過得了；是公開的工具，指名打包寫到一般的 dist/。"""
    result = build_script.build([NAME], out_dir=str(tmp_path))
    assert result["problems"] == [], result["problems"]
    assert os.path.isfile(tmp_path / result["index"]["packs"][0]["file"])
    assert NAME not in build_script.private_names()
    assert build_script.default_out([NAME]).endswith("dist")


def test_built_pack_installs_into_an_empty_synox(tmp_path):
    """擁有者的裝法之一：`python -m synox install <檔> --yes`（同一條安全檢查：第 1、2 關 → 核准 → packs.lock）。
    裝進暫存資料夾的空 SYNOX，裝好之後指令在、沒設定時回一句怎麼設。"""
    from isdtelekit.testing import OWNER_ID, FakeChannel
    from synox.__main__ import main
    from synox.app import build
    from synox.config import SynoxConfig
    from synox.llm.fake import FakeAdapter
    from synox.vault import Vault

    from conftest import SynoxBot
    result = build_script.build([NAME], out_dir=str(tmp_path / "dist"))
    assert result["problems"] == [], result["problems"]
    file = tmp_path / "dist" / result["index"]["packs"][0]["file"]
    data, packs_dir = tmp_path / "data", tmp_path / "packs"
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"owner_id": OWNER_ID, "data_dir": str(data), "vault": {"mode": "file"}}), encoding="utf-8")
    assert main(["--config", str(cfg), "install", str(file), "--yes", "--packs-dir", str(packs_dir)]) == 0
    assert (packs_dir / NAME / "pack.json").is_file()
    config = SynoxConfig.from_dict({"owner_id": OWNER_ID, "data_dir": str(data), "vault": {"mode": "file"},
                                    "llm": {"kind": "fake"}}, require_token=False, root=str(tmp_path))
    api = FakeChannel()
    bot = SynoxBot(build(config, home=api, adapter=FakeAdapter(None), vault=Vault(b"k" * 32, str(data)),
                         pack_dirs=[str(packs_dir)]), api)
    bot.say(OWNER_ID, f"/v2n {URL}")
    assert "/v2n_setup program=" in bot.last(OWNER_ID), bot.last(OWNER_ID)


def test_pack_has_no_local_paths_or_secrets():
    bad = ("C:\\Users", "C:/Users", "/home/", "/Users/", "sk-", "AIza", "@gmail", "@outlook")
    for here, _dirs, files in os.walk(FOLDER):
        if "__pycache__" in here:
            continue
        for f in files:
            text = open(os.path.join(here, f), encoding="utf-8").read()
            assert not [b for b in bad if b in text], (f, [b for b in bad if b in text])


def test_app_page_follows_the_library_rules():
    from test_apps_common import FORBIDDEN_JS
    text = open(os.path.join(FOLDER, "app", "app.js"), encoding="utf-8").read()
    assert not [w for w in FORBIDDEN_JS if w in text]
    entry = open(os.path.join(FOLDER, "app", "index.html"), encoding="utf-8").read()
    assert entry.index('<script src="/_synox/kit.js"></script>') < entry.index("app.js")


# ── 純函式 ─────────────────────────────────────────────────────────────

def test_parse_envelope_takes_the_last_indented_json():
    m = load_module(NAME)
    text = ("[影片] 下載…\n{ not json\n[影片] 下載：完成\n"
            + json.dumps({"ok": True, "data": {"title": "x", "notes": "{\n}"}}, ensure_ascii=False, indent=2) + "\n")
    assert m.parse_envelope(text) == {"ok": True, "data": {"title": "x", "notes": "{\n}"}}
    assert m.parse_envelope("Traceback\n  boom\n") is None


def test_progress_counts_main_stages():
    m = load_module(NAME)
    items = m.progress_of("[A] 影片資訊…\n[A] 影片資訊：完成\n[A] 下載：沿用既有檔案\n[A] 抽音訊…\n[B] 影片資訊…\n")
    assert [(i["label"], i["stage"], i["state"], i["done"]) for i in items] == [
        ("A", "抽音訊", "running", 2), ("B", "影片資訊", "running", 0)]
    assert "抽音訊中（2/5）" in m.progress_text(items)


def test_notes_argv_keeps_values_in_one_piece(tmp_path):
    m = load_module(NAME)
    argv, urls = m.notes_argv({"urls": f"{URL} https://youtu.be/x", "profile": "news", "with": "timeline, actions",
                               "focus": "AI  伺服器 --gui"})
    assert urls == [URL, "https://youtu.be/x"]
    assert argv == ["--profile=news", "--with=timeline,actions", "--focus=AI 伺服器 --gui", "--reuse"]
    for values in ({"urls": "--gui"}, {"urls": "file:///etc/passwd"}, {"urls": URL, "profile": "--yes"},
                   {"urls": URL, "with": "a;b"}, {"urls": ""}):
        with pytest.raises(m.Problem):
            m.notes_argv(values)


def test_resolve_program_shapes(tmp_path):
    m = load_module(NAME)
    with pytest.raises(m.Problem, match="還沒告訴我"):
        m.resolve_program({})
    tool = tmp_path / "tool"
    (tool / ".venv" / "Scripts").mkdir(parents=True)
    (tool / "VideoNotes.py").write_text("", encoding="utf-8")
    with pytest.raises(m.Problem, match=".venv"):
        m.resolve_program({"program": str(tool)})
    (tool / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    assert m.resolve_program({"program": str(tool)}) == (
        [str(tool / ".venv" / "Scripts" / "python.exe"), str(tool / "VideoNotes.py")], str(tool))
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    (exe_dir / "VideoNotes(v2.2.0).exe").write_bytes(b"")
    assert m.resolve_program({"program": str(exe_dir)})[0] == [str(exe_dir / "VideoNotes(v2.2.0).exe")]
    with pytest.raises(m.Problem, match="找不到"):
        m.resolve_program({"program": str(tmp_path / "gone.exe")})


# ── 沒設定：一句話，不出錯 ──────────────────────────────────────────────

def test_without_setup_every_command_says_how(vn_bot, owner):
    bot = vn_bot()
    for cmd in (f"/v2n {URL}", "/v2n_list", "/v2n_check", "/v2n_combine 1,2"):
        bot.talk(owner, cmd)
        assert "/v2n_setup program=" in bot.last(owner), (cmd, bot.last(owner))
    bot.say(owner, "/v2n_status")
    assert "還沒有工作" in bot.last(owner)
    bot.say(owner, "/v2n_setup")
    assert "還沒設定" in bot.last(owner)


def test_setup_is_not_an_ai_tool(vn_bot):
    bot = vn_bot()
    node = bot.app.registry.node("v2n.setup")
    assert node is not None and node.ai is False
    assert bot.app.registry.node("v2n.add").ai is True


def test_setup_refuses_missing_files(vn_bot, owner, tmp_path):
    bot = vn_bot()
    bot.say(owner, f"/v2n_setup program={tmp_path / 'nope.exe'}")
    assert "找不到" in bot.last(owner)
    bot.say(owner, "/v2n_setup out=relative/dir")
    assert "完整的資料夾路徑" in bot.last(owner)


# ── 整條路（假的 Video Notes） ───────────────────────────────────────────

def test_notes_job_end_to_end(vn, owner):
    vn.say(owner, f"/v2n {URL} profile=news with=timeline focus=AI 伺服器")
    assert "工作 #1 排進去了" in vn.last(owner)
    done = wait_done(vn, owner, 1)
    assert "✅ 完成" in done and "假影片 abc123" in done
    call = argv_calls(vn.out)[-1]
    argv = call["argv"]
    assert call["urls"] == [URL] and URL not in " ".join(argv)            # 網址走 --file，不在 argv
    assert any(a.startswith("--file=") for a in argv)
    assert {"--profile=news", "--with=timeline", "--focus=AI 伺服器", "--reuse", "--json", f"--out={vn.out}"} <= set(argv)
    vn.talk(owner, "/v2n_get 1")
    assert "這是假的筆記（abc123）" in vn.last(owner) and "📝 假影片 abc123" in vn.last(owner)
    vn.talk(owner, "/v2n_get")                                           # 不給＝最新做好的
    assert "這是假的筆記（abc123）" in vn.last(owner)


def test_no_plaintext_job_files_or_urls_in_logs(vn, owner, tmp_path, caplog):
    """跑完、列完筆記庫之後，SYNOX 資料夾裡沒有留下明文的輸出（標題、筆記路徑）或網址清單；
    網址也不在 SYNOX 的 log（JobRunner 會記 argv）。結果只在保管箱（密文）。"""
    import logging
    caplog.set_level(logging.INFO)
    vn.say(owner, f"/v2n {URL}")
    wait_done(vn, owner, 1)
    vn.talk(owner, "/v2n_list")
    assert "假影片 abc123" in vn.last(owner)
    assert job_files(tmp_path) == []
    jobs_lines = "\n".join(r.getMessage() for r in caplog.records if "[JOBS]" in r.getMessage())
    assert "video-notes-1 started" in jobs_lines and URL not in jobs_lines and "abc123" not in jobs_lines
    raw = b"".join(p.read_bytes() for p in (tmp_path / "data").rglob("*.enc"))
    assert "假影片".encode() not in raw and URL.encode() not in raw


def test_library_get_item_and_combine(vn, owner):
    for i, url in enumerate((URL, "https://example.com/watch?v=def456", "https://example.com/watch?v=ghi789"), 1):
        vn.say(owner, f"/v2n {url}")
        wait_done(vn, owner, i)
    vn.talk(owner, "/v2n_list")
    listing = vn.last(owner)
    assert "1. [影片]" in listing and "假影片 abc123" in listing and "general" in listing
    vn.talk(owner, "/v2n_list def456")
    assert "def456" in vn.last(owner) and "abc123" not in vn.last(owner)
    vn.talk(owner, "/v2n_get item=1")
    assert "這是假的筆記" in vn.last(owner)
    vn.say(owner, "/v2n_combine 1,2 title=今天的新聞")
    assert "工作 #4" in vn.last(owner)
    assert "✅ 完成" in wait_done(vn, owner, 4)
    vn.talk(owner, "/v2n_get 4")
    assert "合併了 2 支" in vn.last(owner)
    vn.say(owner, "/v2n_combine 1,2,3")                                  # 規模大：假的 Video Notes 要求 --yes
    failed = wait_done(vn, owner, 5)
    assert "❌ 失敗" in failed and "規模較大需要確認" in failed
    vn.say(owner, "/v2n_combine 1,2,3 yes=是")
    assert "✅ 完成" in wait_done(vn, owner, 6)
    assert "--yes" in argv_calls(vn.out)[-1]["argv"]
    vn.say(owner, "/v2n_combine 1")
    assert "至少兩個" in vn.last(owner)


def test_failures_explain_why_and_what_to_do(vn, owner):
    vn.say(owner, "/v2n https://example.com/fail")
    text = wait_done(vn, owner, 1)
    assert "❌ 失敗" in text and "連不上影片網站" in text and "檢查網路後再試一次" in text
    vn.say(owner, "/v2n https://example.com/garbage")
    assert "沒有回傳看得懂的結果" in wait_done(vn, owner, 2)
    vn.say(owner, "/v2n notaurl")
    assert "不像影片網址" in vn.last(owner)
    vn.say(owner, "/v2n --gui")
    assert "不像影片網址" in vn.last(owner)


def test_progress_queue_and_cancel(vn, owner):
    vn.say(owner, "/v2n https://example.com/slow")
    vn.say(owner, f"/v2n {URL}")
    assert "工作 #2 排進去了" in vn.last(owner)
    text = wait_log(vn, owner, 1, "抽音訊中")
    assert "（2/5）" in text
    vn.say(owner, "/v2n_status")
    assert "#2 ⏳ 排隊中" in vn.last(owner)
    vn.say(owner, "/v2n_cancel 2")
    assert "還沒開始，取消了" in vn.last(owner)
    vn.say(owner, "/v2n_cancel 1")
    assert "停掉工作 #1" in vn.last(owner)
    assert "⏹ 已取消" in wait_done(vn, owner, 1)
    vn.say(owner, "/v2n_cancel")
    assert "沒有在做或排隊" in vn.last(owner)


def test_ai_combine_always_asks_first(vn_bot, owner, tmp_path):
    """AI 想合併（會花 AI 額度）：SYNOX 先送確認卡給你，不管 Video Notes 自己覺得規模大不大；按了才排進去。"""
    bot = vn_bot([{"tool": "v2n__combine", "args": {"items": "1,2", "title": "今天"}}])
    out = tmp_path / "out"
    bot.say(owner, f"/v2n_setup program={FAKE} python={sys.executable} out={out}")
    for i, url in enumerate((URL, "https://example.com/watch?v=def456"), 1):
        bot.say(owner, f"/v2n {url}")
        wait_done(bot, owner, i)
    bot.talk(owner, "/v2n_list")
    bot.talk(owner, "把第一、二支合併")
    assert "AI 想執行：/v2n_combine" in bot.last(owner), bot.last(owner)
    bot.say(owner, "/v2n_status")
    assert "#3" not in bot.last(owner)                                    # 還沒按：什麼都沒排
    go = [b for b in bot.buttons(owner) if b.startswith("agent_ok:go:")][0]
    bot.press_and_wait(owner, go)
    assert "✅ 完成" in wait_done(bot, owner, 3)


def test_interrupted_jobs_after_restart(vn_bot, owner, tmp_path):
    """上一次 SYNOX 開著時跑的工作（這次啟動沒開過的）→ 新的 SYNOX 標成「中斷了」，不會一直顯示進行中；排隊的照樣開始。"""
    bot = vn_bot()
    out = tmp_path / "out"
    bot.say(owner, f"/v2n_setup program={FAKE} python={sys.executable} out={out}")
    bot.say(owner, "/v2n https://example.com/slow")
    bot.say(owner, f"/v2n {URL}")
    wait_log(bot, owner, 1, "抽音訊中")
    again = vn_bot()                                  # 同一份資料、新的 SYNOX（它的 JobRunner 沒開過工作 #1）
    try:
        again.say(owner, "/v2n_status 1")
        assert "中斷了" in again.last(owner).splitlines()[0], again.last(owner)
        assert "✅ 完成" in wait_done(again, owner, 2)     # 排隊的 #2 由新的 SYNOX 開始、做完
    finally:
        for job_id in list(bot.app.jobs._handles):      # 收掉舊的那支假程式（真的 SYNOX 關掉時它會跟著結束）
            bot.app.jobs.cancel(job_id)
        for job_id in list(again.app.jobs._handles):
            again.app.jobs.cancel(job_id)


# ── 工具畫面（SYNOX 的 AppClient，直接呼叫、不開埠） ───────────────────────

def opened(bot, who):
    from synox.web import apps
    from synox.web.apps.testing import AppClient, FakeListen
    manager = apps.of(bot.app)
    if not isinstance(manager.listen, FakeListen):
        manager.listen = FakeListen()
    if NAME not in manager.running():
        manager.start(NAME, by=who, via="web")
    client = AppClient(manager, NAME)
    client.enter(client.open_from_console(who))
    return client


def ok(resp):
    status, data = resp
    assert status == 200 and data.get("ok"), (status, data)
    return data["data"]


def test_app_reads_and_actions(vn, owner):
    from synox.web import apps
    assert apps.of(vn.app).problems() == {}
    client = opened(vn, owner)
    first = ok(client.read("jobs"))
    assert first["ready"] is True and first["jobs"] == []
    job = ok(client.do("add", {"urls": URL, "profile": "quick"}))["job"]
    wait_done(vn, owner, job)
    jobs = ok(client.read("jobs"))["jobs"]
    assert jobs[0]["state"] == "done"
    detail = ok(client.read("job", id=str(job)))
    assert detail["notes"][0]["markdown"].startswith("# 假影片 abc123")
    lib = ok(client.read("library"))
    assert lib["items"][0]["notes"] == ["general"]
    note = ok(client.read("note", item="1"))
    assert "這是假的筆記" in note["markdown"]
    status, data = client.do("cancel", {})
    assert status != 200 and "沒有在做或排隊" in json.dumps(data, ensure_ascii=False)
    status, data = client.do("add", {"urls": "notaurl"})
    assert status != 200 and "不像影片網址" in json.dumps(data, ensure_ascii=False)


def test_app_says_how_to_set_up(vn_bot, owner):
    bot = vn_bot()
    data = ok(opened(bot, owner).read("jobs"))
    assert data["ready"] is False and "/v2n_setup" in data["hint"]
