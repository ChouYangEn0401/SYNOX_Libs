# -*- coding: utf-8 -*-
"""⏱ Claude 重置計時：真的 SYNOX 載入 packs/claude-resetter，背後叫一份**假的** ClaudeTimerResetter
（測試在暫存資料夾裡寫出假的 claude_timer/core/usage.py、ping.py，印 JSON；Python 用跑測試的這一支）。
不連網、不叫 claude、不開埠、不碰 ~/.claude；子程序只有那兩支假程式。"""
import json
import os
import sys
import time

import pytest

import build as build_script
from conftest import PACK_NAMES, PACKS, load_module

from synox.packs import manifest as synox_manifest
from synox.packs import scan as synox_scan

NAME = "claude-resetter"
FOLDER = os.path.join(PACKS, NAME)

# 假的入口：把 argv 與『有沒有看到像密鑰的環境變數』記進 calls.jsonl，照 control.json 印 JSON、回結束碼
FAKE_ENTRY = '''# -*- coding: utf-8 -*-
import json, os, sys, time

ENTRY = {entry!r}
here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(here, "calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({{"entry": ENTRY, "argv": sys.argv[1:], "cwd": os.getcwd(),
                        "secret_env": sorted(k for k in os.environ if k.endswith("_TOKEN"))}}) + "\\n")
with open(os.path.join(here, "control.json"), encoding="utf-8") as f:
    control = json.load(f).get(ENTRY, {{}})
time.sleep(control.get("sleep", 0))
if "raw" in control:
    print(control["raw"])
else:
    print(json.dumps(control.get("payload", {{"ok": True}}), ensure_ascii=False))
sys.exit(control.get("code", 0))
'''

FIVE_RUNNING = {"percent": 37.0, "resets_at": "2026-11-23T05:40:00+00:00", "resets_local": "2026-11-23T13:40:00+08:00", "seconds_left": 7500}
WEEK = {"percent": 12.0, "resets_at": "2026-11-28T05:40:00+00:00", "resets_local": "2026-11-28T13:40:00+08:00", "seconds_left": 430000}
FIVE_CLOSED = {"percent": 0.0, "resets_at": None, "resets_local": None, "seconds_left": None}


def usage_payload(five=FIVE_RUNNING):
    return {"ok": True, "window_open": bool(five["resets_at"]), "windows": {"five_hour": five, "seven_day": WEEK}}


def ping_payload(action, reason, five=FIVE_RUNNING, probe=None):
    return {"ok": True, "action": action, "reason": reason, "waited_seconds": 0,
            "usage": {"windows": {"five_hour": five, "seven_day": WEEK}, "window_open": bool(five["resets_at"])}, "probe": probe}


PROBE = {"model": "fake-model", "reply": "k", "cost_usd": 0.000254, "seconds": 4.2, "binary": "claude"}


@pytest.fixture
def resetter(tmp_path):
    """假的 ClaudeTimerResetter 原始碼資料夾；control(**入口) 換假程式的輸出，calls() 讀它每次被叫的紀錄。"""
    root = tmp_path / "fake-resetter"
    core = root / "claude_timer" / "core"
    core.mkdir(parents=True)
    (root / "claude_timer" / "__init__.py").write_text("", encoding="utf-8")
    (core / "__init__.py").write_text("", encoding="utf-8")
    for entry in ("usage", "ping"):
        (core / f"{entry}.py").write_text(FAKE_ENTRY.format(entry=entry), encoding="utf-8")

    class Fake:
        path = str(root)

        def control(self, **entries):
            (root / "control.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

        def calls(self):
            path = root / "calls.jsonl"
            if not path.is_file():
                return []
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    fake = Fake()
    fake.control()
    return fake


@pytest.fixture
def rs_bot(tmp_path):
    """真的 SYNOX（同 conftest.synox_bot），先把這個工具鎖進 packs.lock（＝擁有者在安裝頁按過確認）。"""
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
        adapter = FakeAdapter(script)
        app = build(config, home=api, adapter=adapter, vault=Vault(b"k" * 32, str(data)), pack_dirs=[PACKS])
        bot = SynoxBot(app, api)
        bot.adapter = adapter
        return bot
    return factory


@pytest.fixture
def rs(rs_bot, owner, resetter):
    bot = rs_bot()
    bot.say(owner, f"/resetter_setup home={resetter.path} python={sys.executable}")
    assert "找得到 resetter" in bot.last(owner), bot.last(owner)
    bot.fake = resetter
    return bot


def wait_calls(fake, n, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(fake.calls()) >= n:
            return fake.calls()
        time.sleep(0.05)
    raise AssertionError(f"fake resetter was called {len(fake.calls())} times, wanted {n}")


def ask(bot, owner, text):
    """打一個指令、等背景做完，回最後一則回覆。"""
    bot.talk(owner, text)
    return bot.last(owner)


# ── 宣告、安全關卡 ─────────────────────────────────────────────────────

def test_manifest_is_public_owner_only_and_honest():
    m = synox_manifest.read(FOLDER, strict=True)
    assert "publish" not in m.raw and NAME in PACK_NAMES
    assert m.access == "owner" and m.surfaces == ["dm"] and m.data == "user" and m.storage == "vault"
    assert m.group == "resetter" and m.stage == "test" and m.version.endswith("-alpha.1")
    assert m.capabilities == ["subprocess"]                      # 最小：只經 JobRunner 叫別的程式
    report = synox_scan.scan(m)
    assert report.problems(m) == [], report.problems(m)
    assert report.required_capabilities == ["subprocess"]
    assert m.raw["callers"]["setup"] == ["human"]
    assert m.raw["callers"]["refresh"] == ["human", "ai", "schedule"]


def test_named_build_passes_synox_gates(tmp_path):
    result = build_script.build([NAME], out_dir=str(tmp_path))
    assert result["problems"] == [], result["problems"]
    assert os.path.isfile(tmp_path / result["index"]["packs"][0]["file"])
    assert NAME not in build_script.private_names()


def test_built_pack_installs_into_an_empty_synox(tmp_path):
    """`python -m synox install <檔> --yes`（第 1、2 關 → 核准 → packs.lock）裝進空的 SYNOX，沒設定時回一句怎麼設。"""
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
    config = SynoxConfig.from_dict({"owner_id": OWNER_ID, "data_dir": str(data), "vault": {"mode": "file"},
                                    "llm": {"kind": "fake"}}, require_token=False, root=str(tmp_path))
    api = FakeChannel()
    bot = SynoxBot(build(config, home=api, adapter=FakeAdapter(None), vault=Vault(b"k" * 32, str(data)),
                         pack_dirs=[str(packs_dir)]), api)
    bot.talk(OWNER_ID, "/resetter_refresh")
    assert "/resetter_setup home=" in bot.last(OWNER_ID), bot.last(OWNER_ID)


def test_pack_has_no_local_paths_or_secrets():
    bad = ("C:\\Users", "C:/Users", "/home/", "/Users/", "sk-", "AIza", "@gmail", "@outlook")
    for here, _dirs, files in os.walk(FOLDER):
        if "__pycache__" in here:
            continue
        for f in files:
            text = open(os.path.join(here, f), encoding="utf-8").read()
            assert not [b for b in bad if b in text], (f, [b for b in bad if b in text])


def test_pack_never_touches_files_credentials_or_network():
    """工具自己不讀 ~/.claude、不讀金鑰、不連網、不開檔（只有 pack.store 與 JobRunner）。"""
    import ast
    tree = ast.parse(open(os.path.join(FOLDER, "pack.py"), encoding="utf-8").read())
    tree.body = tree.body[1:]                                          # 模組開頭的說明文字可以提到 ~/.claude（說它不碰）
    text = ast.unparse(tree)
    for word in (".claude", "credentials", "accessToken", "open(", "requests", "urllib", "socket", "shell=True", "getenv", "environ"):
        assert word not in text, word


# ── 純函式 ─────────────────────────────────────────────────────────────

def test_time_and_window_text():
    m = load_module(NAME)
    assert m.left_text(7500) == "2 小時 05 分" and m.left_text(2100) == "35 分" and m.left_text(20) == "不到 1 分"
    assert m.left_text(None) == "不知道" and m.left_text(True) == "不知道"
    assert m.when_text(FIVE_RUNNING) == "13:40"                       # 5 小時視窗只寫時間
    assert m.when_text(WEEK) == "11-28 13:40"                         # 7 天視窗帶日期
    assert m.when_text({"resets_local": "garbage"}) == ""
    assert m.window_line("5 小時視窗", FIVE_RUNNING) == "5 小時視窗：已用 37%，13:40 重置（剩 2 小時 05 分）"
    assert "還沒開始計時" in m.window_line("5 小時視窗", FIVE_CLOSED)
    assert m.window_line("x", None) == ""


def test_refresh_argv_is_just_the_documented_flags():
    m = load_module(NAME)
    assert m.refresh_argv(False, False) == ["--json", "--no-wait"]    # 跟 resetter 自己排程的 --tick 同一條路（不原地等）
    assert m.refresh_argv(True, False) == ["--json", "--force", "--no-wait"]
    assert m.refresh_argv(False, True) == ["--json"]                  # 要等：ping 預設最多原地等 920 秒
    assert m.module_argv("/py", "ping", ["--json"]) == ["/py", "-m", "claude_timer.core.ping", "--json"]
    with pytest.raises(m.BridgeProblem):
        m.module_argv("/py", "os", [])                                # 只認三個入口
    with pytest.raises(m.BridgeProblem):
        m.module_argv("/py", "ping", ["a\x00b"])


def test_refresh_text_says_what_happened():
    m = load_module(NAME)
    done = m.refresh_text(ping_payload("refreshed", "5 小時視窗還沒開始計時，打了才會啟動重置時鐘", FIVE_RUNNING, PROBE), 0)
    assert "✅ 已刷新" in done and "$0.00025" in done and "4.2 秒" in done and "還沒開始計時" in done and "下次重置：13:40（剩 2 小時 05 分）" in done
    skipped = m.refresh_text(ping_payload("skipped", "5 小時視窗還在跑（剩 2h05m），重置時鐘早就啟動了"), 0)
    assert "不用打" in skipped and "沒花錢" in skipped and "重置時鐘早就啟動了" in skipped and "13:40" in skipped
    waiting = m.refresh_text(ping_payload("waiting", "再 0h10m 就重置，等它重置完再打"), 0)
    assert "沒打" in waiting and "wait=是" in waiting
    failed = m.refresh_text({"ok": False, "action": "refreshed", "reason": "未登入或認證失敗（請執行 claude 登入）", "detail": "x" * 400}, 3)
    assert failed.startswith("⏱ 刷新沒成功") and "未登入" in failed and "怎麼辦" in failed and "x" * 200 not in failed
    unknown = m.refresh_text({"ok": True, "action": "???", "reason": "r"}, 0)
    assert "沒看過的結果" in unknown


# ── 沒設定、設定的規矩 ───────────────────────────────────────────────────

def test_without_setup_every_command_says_how(rs_bot, owner):
    bot = rs_bot()
    for cmd in ("/resetter_usage", "/resetter_refresh", "/resetter_check"):
        bot.talk(owner, cmd)
        assert "/resetter_setup home=" in bot.last(owner), (cmd, bot.last(owner))
    bot.say(owner, "/resetter_setup")
    assert "還沒設定" in bot.last(owner)


def test_setup_checks_the_paths(rs_bot, owner, tmp_path, resetter):
    bot = rs_bot()
    bot.say(owner, f"/resetter_setup home={tmp_path / 'nope'}")
    assert "找不到" in bot.last(owner)
    bot.say(owner, f"/resetter_setup home={resetter.path}/claude_timer/__init__.py")           # 檔案不是資料夾
    assert "要是資料夾" in bot.last(owner) and "exe" in bot.last(owner)
    empty = tmp_path / "empty"
    empty.mkdir()
    bot.say(owner, f"/resetter_setup home={empty}")                                            # 資料夾但不是 resetter
    assert "找不到 claude_timer" in bot.last(owner)
    bot.say(owner, f"/resetter_setup home={resetter.path}")                                    # 沒有 .venv、沒給 python
    assert ".venv" in bot.last(owner)
    bot.say(owner, f"/resetter_setup python={resetter.path}")                                  # python 要是檔案
    assert "python 要是" in bot.last(owner)
    bot.say(owner, f"/resetter_setup python={sys.executable}")
    assert "找得到 resetter" in bot.last(owner)
    bot.say(owner, "/resetter_setup home=-")
    assert "還沒設定" in bot.last(owner)


def test_setup_is_human_only_and_not_an_ai_tool(rs_bot):
    from isdtelekit.core.registry import AI, ROUTINE
    from synox import policy
    bot = rs_bot()
    reg = bot.app.registry
    owner = bot.app.users.get(bot.app.config.owner_id)
    assert reg.node("resetter.setup").ai is False
    for via in (AI, ROUTINE):
        assert not policy.decide(bot.app, owner, reg.node("resetter.setup"), via=via).allowed, via
    # 排程、AI 都叫得動 usage、refresh、check
    for leaf in ("usage", "refresh"):
        for via in (AI, ROUTINE):
            assert policy.decide(bot.app, owner, reg.node(f"resetter.{leaf}"), via=via).allowed, (leaf, via)
    assert reg.node("resetter.refresh").ai is True


# ── 整條路（假的 resetter） ─────────────────────────────────────────────

def test_usage_reads_the_quota_and_calls_only_the_usage_module(rs, owner):
    rs.fake.control(usage={"payload": usage_payload()})
    text = ask(rs, owner, "/resetter_usage")
    assert "不花錢" in text and "5 小時視窗：已用 37%，13:40 重置（剩 2 小時 05 分）" in text and "7 天視窗：已用 12%，11-28 13:40" in text
    (call,) = rs.fake.calls()
    assert call["entry"] == "usage" and call["argv"] == ["--json"]
    assert call["cwd"] == rs.fake.path                               # 在 resetter 資料夾裡跑（-m 才找得到 claude_timer）


def test_usage_says_when_the_window_is_not_open(rs, owner):
    rs.fake.control(usage={"payload": usage_payload(FIVE_CLOSED)})
    text = ask(rs, owner, "/resetter_usage")
    assert "還沒開始計時" in text and "/resetter_refresh 才有意義" in text


def test_usage_failures_are_explained(rs, owner):
    rs.fake.control(usage={"payload": {"ok": False, "reason": "token 過期或無效（開一次 Claude Code 就會自動換新）", "detail": "HTTP 401"}, "code": 3})
    text = ask(rs, owner, "/resetter_usage")
    assert "讀不到額度" in text and "token 過期" in text and "claude 還沒登入" in text
    rs.fake.control(usage={"raw": "Traceback (most recent call last):\n  boom", "code": 1})
    assert "沒有回傳看得懂的結果" in ask(rs, owner, "/resetter_usage")


def test_refresh_when_the_window_is_closed_fires_the_probe(rs, owner):
    rs.fake.control(ping={"payload": ping_payload("refreshed", "5 小時視窗還沒開始計時，打了才會啟動重置時鐘", FIVE_RUNNING, PROBE)})
    text = ask(rs, owner, "/resetter_refresh")
    assert "✅ 已刷新" in text and "$0.00025" in text and "下次重置：13:40" in text
    (call,) = rs.fake.calls()
    assert call["entry"] == "ping" and call["argv"] == ["--json", "--no-wait"]


def test_refresh_skips_when_the_window_is_already_running(rs, owner):
    rs.fake.control(ping={"payload": ping_payload("skipped", "5 小時視窗還在跑（剩 2h05m），重置時鐘早就啟動了")})
    text = ask(rs, owner, "/resetter_refresh")
    assert "不用打" in text and "沒花錢" in text and "13:40" in text and "已刷新" not in text


def test_refresh_near_reset_waits_by_default_and_says_so(rs, owner):
    rs.fake.control(ping={"payload": ping_payload("waiting", "再 0h10m 就重置，等它重置完再打")})
    text = ask(rs, owner, "/resetter_refresh")
    assert "沒打" in text and "wait=是" in text
    assert rs.fake.calls()[-1]["argv"] == ["--json", "--no-wait"]
    ask(rs, owner, "/resetter_refresh wait=是")                        # 人要求等：不帶 --no-wait（ping 預設最多原地等 15 分鐘多一點）
    assert rs.fake.calls()[-1]["argv"] == ["--json"]


def test_refresh_failures_are_explained(rs, owner):
    for code, reason, advice in ((1, "呼叫失敗（額度用完 / 逾時 / 模型錯誤）", "額度用完"), (2, "找不到 claude 執行檔（請安裝官方 Claude Code）", "安裝並登入"),
                                 (3, "未登入或認證失敗（請執行 claude 登入）", "還沒登入"), (4, "套件沒裝好：請 pip install …", "setup.bat")):
        rs.fake.control(ping={"payload": {"ok": False, "action": "refreshed", "reason": reason, "detail": "d"}, "code": code})
        text = ask(rs, owner, "/resetter_refresh")
        assert "刷新沒成功" in text and reason[:8] in text and "怎麼辦" in text and advice in text, (code, text)
    rs.fake.control(ping={"raw": "oops not json", "code": 4})
    assert "沒有回傳看得懂的結果" in ask(rs, owner, "/resetter_refresh")


def test_refresh_timeout_is_a_plain_sentence(rs, owner, monkeypatch):
    mod = __import__("sys").modules["synox_pack_claude_resetter"]
    monkeypatch.setattr(mod, "REFRESH_TIMEOUT", 1)
    rs.fake.control(ping={"sleep": 30, "payload": {"ok": True}})
    text = ask(rs, owner, "/resetter_refresh")
    assert "超過 1 秒沒有回應" in text and "/resetter_usage" in text


def test_refresh_does_not_fire_twice_at_once(rs, owner):
    rs.fake.control(ping={"sleep": 1.5, "payload": ping_payload("refreshed", "r", FIVE_RUNNING, PROBE)})
    rs.say(owner, "/resetter_refresh")
    wait_calls(rs.fake, 1)                                             # 第一次已經開始（假程式在睡）
    rs.say(owner, "/resetter_refresh")
    assert "還在跑" in rs.last(owner)
    time.sleep(2.5)
    assert len(rs.fake.calls()) == 1                                   # 沒有第二次探針
    rs.talk(owner, "/resetter_refresh")                                # 做完之後又可以叫
    assert len(rs.fake.calls()) == 2


def test_force_is_only_for_the_human(rs_bot, owner, resetter):
    """force=是：本人照打（帶 --force）；AI、排程叫都被擋，假的 resetter 一次都沒被叫。"""
    from isdtelekit.core.channel import ROUTINE_CHANNEL, Inbound
    from isdtelekit.core.registry import ROUTINE
    bot = rs_bot([{"tool": "resetter__refresh", "args": {"force": "是"}}])
    bot.say(owner, f"/resetter_setup home={resetter.path} python={sys.executable}")
    resetter.control(ping={"payload": ping_payload("refreshed", "--force：不看用量一定打", FIVE_RUNNING, PROBE)})
    bot.talk(owner, "幫我強制刷新")                                       # AI 帶 force
    time.sleep(0.5)
    assert resetter.calls() == [], bot.texts(owner)
    tool_results = [m.content for m in bot.adapter.calls[-1][0] if m.role == "tool"]
    assert len(tool_results) == 1 and "本人" in tool_results[0], tool_results      # AI 真的叫了，看到的是『要你本人』，不是默默失敗
    inbound = Inbound(ROUTINE_CHANNEL, user_id=owner, chat_id=owner, text="/resetter_refresh force=是", username="schedule")
    bot.app.dispatcher.handle(bot.app.home, inbound, via=ROUTINE)         # 排程到點：同樣被擋
    time.sleep(0.5)
    assert resetter.calls() == []
    bot.talk(owner, "/resetter_refresh force=是")                         # 本人
    assert "✅ 已刷新" in bot.last(owner)
    assert resetter.calls()[-1]["argv"] == ["--json", "--force", "--no-wait"]


def test_schedule_can_call_refresh(rs, owner):
    """SYNOX 內建的 /schedule_add 排得進去；到點（via=ROUTINE）真的叫得動 refresh。"""
    from isdtelekit.core.channel import ROUTINE_CHANNEL, Inbound
    from isdtelekit.core.registry import ROUTINE
    rs.fake.control(ping={"payload": ping_payload("skipped", "5 小時視窗還在跑（剩 2h05m），重置時鐘早就啟動了")})
    rs.say(owner, "/schedule_add 08:40 /resetter_refresh")
    assert "每天 08:40 替你執行：/resetter_refresh" in rs.last(owner), rs.last(owner)
    rs.say(owner, "/schedule_add 08:41 /resetter_setup home=/tmp")        # 設定只有本人：排進排程會被拒
    assert "不能排進排程" in rs.last(owner), rs.last(owner)
    inbound = Inbound(ROUTINE_CHANNEL, user_id=owner, chat_id=owner, text="/resetter_refresh", username="schedule")
    rs.app.dispatcher.handle(rs.app.home, inbound, via=ROUTINE)
    calls = wait_calls(rs.fake, 1)
    assert calls[0]["entry"] == "ping" and calls[0]["argv"] == ["--json", "--no-wait"]
    deadline = time.time() + 10
    while time.time() < deadline and not any("不用打" in t for t in rs.texts(owner)):
        time.sleep(0.1)
    assert any("不用打" in t for t in rs.texts(owner))             # 結果通知到你


def test_ai_can_refresh_and_gets_the_answer(rs_bot, owner, resetter):
    bot = rs_bot([{"tool": "resetter__refresh", "args": {}}])
    bot.say(owner, f"/resetter_setup home={resetter.path} python={sys.executable}")
    resetter.control(ping={"payload": ping_payload("refreshed", "r", FIVE_RUNNING, PROBE)})
    bot.talk(owner, "今天預計重度使用，先幫我刷新")
    assert resetter.calls()[-1]["argv"] == ["--json", "--no-wait"]
    tool_results = [m.content for m in bot.adapter.calls[-1][0] if m.role == "tool"]
    assert len(tool_results) == 1 and "已刷新" in tool_results[0] and "$0.00025" in tool_results[0], tool_results     # 結果交回 AI（同步），不是丟背景


def test_ai_cannot_wait_fifteen_minutes(rs_bot, owner, resetter):
    bot = rs_bot([{"tool": "resetter__refresh", "args": {"wait": "是"}}])
    bot.say(owner, f"/resetter_setup home={resetter.path} python={sys.executable}")
    bot.talk(owner, "刷新並等到重置")
    assert resetter.calls() == []
    tool_results = [m.content for m in bot.adapter.calls[-1][0] if m.role == "tool"]
    assert len(tool_results) == 1 and "不能等" in tool_results[0], tool_results


def test_check_reports_each_step(rs, owner, tmp_path):
    rs.fake.control(usage={"payload": usage_payload()})
    text = ask(rs, owner, "/resetter_check")
    assert "找得到 ClaudeTimerResetter" in text and "讀得到額度" in text and "不會花錢" in text
    assert [c["entry"] for c in rs.fake.calls()] == ["usage"]           # 檢查不打探針
    rs.fake.control(usage={"payload": {"ok": False, "reason": "讀不到本機憑證", "detail": "x"}, "code": 4})
    text = ask(rs, owner, "/resetter_check")
    assert "讀不到額度" in text and "讀不到本機憑證" in text and "怎麼辦" in text
    os.remove(os.path.join(rs.fake.path, "claude_timer", "core", "ping.py"))     # 舊版 resetter 沒有 ping
    assert "太舊" in ask(rs, owner, "/resetter_check") and "ping.py" in rs.last(owner)


def test_every_call_is_an_argv_array_with_only_expected_flags(rs, owner, monkeypatch):
    monkeypatch.setenv("FAKE_SERVICE_TOKEN", "must-not-leak")
    rs.fake.control(usage={"payload": usage_payload()}, ping={"payload": ping_payload("skipped", "r")})
    for cmd in ("/resetter_usage", "/resetter_refresh", "/resetter_refresh force=是", "/resetter_refresh wait=是", "/resetter_check"):
        ask(rs, owner, cmd)
    calls = rs.fake.calls()
    assert len(calls) == 5
    allowed = {"--json", "--force", "--no-wait"}
    for call in calls:
        assert set(call["argv"]) <= allowed and all(isinstance(a, str) for a in call["argv"]), call
        assert call["secret_env"] == [], call                           # 像密鑰的環境變數沒有傳給子程序


def test_nothing_plaintext_is_left_and_settings_stay_in_the_vault(rs, owner, tmp_path):
    rs.fake.control(usage={"payload": usage_payload()}, ping={"payload": ping_payload("refreshed", "r", FIVE_RUNNING, PROBE)})
    ask(rs, owner, "/resetter_usage")
    ask(rs, owner, "/resetter_refresh")
    files = tmp_path / "data" / NAME / "files"
    assert not files.exists() or [p for p in files.rglob("*") if p.is_file()] == []
    raw = b"".join(p.read_bytes() for p in (tmp_path / "data").rglob("*.enc"))
    assert rs.fake.path.encode() not in raw                              # 設定是密文
