# -*- coding: utf-8 -*-
"""🔁 對話接力：真的 SYNOX 載入 packs/claude-relay，背後叫一支**假的** ClaudeTimerResetter
（暫存資料夾裡的 claude_timer/cli.py：照子指令印 JSON、把收到的 argv 記進 argv.jsonl）。
不叫 claude、不連網、不開埠；子程序只有那支假程式（用跑測試的這個 Python）。"""
import ast
import json
import os
import re
import sys
import time

import pytest

from isdtelekit.core.registry import AI, ROUTINE, SCHEDULE, TYPED

import build as build_script
from conftest import ALL_PACK_NAMES, OWNER_ID, PACK_NAMES, PACKS, load_module

from synox.packs import manifest as synox_manifest
from synox.packs import scan as synox_scan

NAME = "claude-relay"
MODULE = "synox_pack_claude_relay"
FOLDER = os.path.join(PACKS, NAME)
SID = "0123abcd-0000-4000-8000-00000000abcd"
SID2 = "0123abcd-1111-4000-8000-00000000abcd"
APP_SID, TEMP_SID, PIPE_SID, OLD_SID = (f"0123abcd-{n}{n}{n}{n}-4000-8000-00000000abcd" for n in (5, 6, 7, 8))
AI_OK = {"sessions", "show", "check", "armed", "jobs", "job", "freeze"}
HUMAN_ONLY = {"arm", "disarm", "cancel", "thaw", "setup"}
STEPUP = {"arm", "thaw", "setup"}
CONTINUE_ARGV = ["resume", "--json", "--prompt=continue", "--permission-mode=inherit", "--max-budget-usd=2", "--", SID]

FAKE_CLI = r'''
import json, os, sys
HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load(name, default):
    try:
        with open(os.path.join(HOME, name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save(name, data):
    with open(os.path.join(HOME, name), "w", encoding="utf-8") as f:
        json.dump(data, f)

def main(argv):
    with open(os.path.join(HOME, "argv.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(argv) + "\n")
    b = load("behavior.json", {})
    jobs = load("jobs.json", {})
    cmd = argv[0]
    target = argv[argv.index("--") + 1] if "--" in argv else ""
    code, out = 0, {}
    if cmd == "sessions":
        rows = [r for r in b.get("sessions", []) if "--all" in argv or r.get("origin") not in ("app", "temp")]
        out = {"sessions": rows, "total": len(rows)}
    elif cmd == "show":
        out = {"session_id": target, "cwd": "/projects/demo", "title": "假的對話", "turns": 5,
               "last_human": "請繼續", "last_assistant": "做到一半", "started": "", "ended": ""}
        if any(a.startswith("--turns=") for a in argv):
            out["last_turns"] = [{"n": 5, "when": "", "prompt": "第五輪的提問", "reply": "第五輪的回答"}]
    elif cmd == "check":
        code = b.get("check_exit", 0)
        out = {"session_id": target, "ok": code == 0, "problems": [], "busy": code == 3, "holder": "VSCode 開著"}
    elif cmd == "resume":
        import time
        time.sleep(b.get("resume_sleep", 0))
        code = b.get("resume_exit", 0)
        if code == 99:
            print("Traceback: boom")
            return 1
        if code in (2, 4):
            out = {"error": "找不到對話" if code == 2 else "參數不對"}
        else:
            job_id = format(len(jobs) + 0xa0, "08x")
            at = next((a[5:] for a in argv if a.startswith("--at=")), "")
            status = {0: "scheduled" if at else "sent", 1: "failed", 3: "waiting"}[code]
            jobs[job_id] = {"id": job_id, "kind": "resume", "session_id": target, "at": at, "status": status,
                            "error": "VSCode 開著；關掉之後會自動補送" if code == 3 else (b.get("resume_error", "claude 秒退") if code == 1 else ""),
                            "result": "", "cost_usd": 0.0, "prompt": "continue",
                            "created": __import__("datetime").datetime.now().isoformat(timespec="seconds")}
            save("jobs.json", jobs)
            if b.get("garble"):                       # 建了工作、但輸出看不懂（例如被別的東西印壞）
                print("Traceback: boom")
                return 1
            out = jobs[job_id]
    elif cmd == "jobs":
        out = {"jobs": list(jobs.values()) + b.get("foreign_jobs", [])}
    elif cmd == "job":
        if target in jobs:
            out = dict(jobs[target], changes=" a.py | 2 +-")
        else:
            code, out = 2, {"error": "沒有這筆"}
    elif cmd == "cancel":
        if target in jobs:
            jobs[target]["status"] = "cancelled"
            save("jobs.json", jobs)
            out = {"cancelled": [target]}
        else:
            code, out = 2, {"error": "沒有這筆"}
    print("[noise] 進度")
    print(json.dumps(out, ensure_ascii=False))
    return code

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


def make_resetter(tmp_path, sessions=(SID, SID2)):
    home = tmp_path / "resetter"
    (home / "claude_timer").mkdir(parents=True)
    (home / "claude_timer" / "__init__.py").write_text("", encoding="utf-8")
    (home / "claude_timer" / "cli.py").write_text(FAKE_CLI, encoding="utf-8")
    rows = [{"session_id": s, "cwd": f"/projects/demo{i}", "label": f"demo{i}", "branch": "main", "relative": "3 小時前",
             "title": "假的對話", "last_prompt": "請繼續", "busy": False, "origin": "project", "entrypoint": "claude-vscode"}
            for i, s in enumerate(sessions)]
    rows += [{"session_id": sid, "cwd": f"/tmp/x{n}", "label": f"x{n}", "branch": "", "relative": "1 天前", "title": "程式開的",
              "last_prompt": "做事", "busy": False, "origin": origin, "entrypoint": entry}
             for n, (sid, origin, entry) in enumerate(((APP_SID, "app", "sdk-cli"), (TEMP_SID, "temp", "cli"),
                                                         (PIPE_SID, "project", "sdk-cli"), (OLD_SID, "project", "")))]
    (home / "behavior.json").write_text(json.dumps({"sessions": rows}), encoding="utf-8")
    return home


class Rig:
    def __init__(self, bot, home):
        self.bot, self.home = bot, home
        self.module = sys.modules[MODULE]

    def say(self, text):
        self.bot.say(OWNER_ID, text)
        assert self.bot.app.tasks.wait(10)
        return self.bot.last(OWNER_ID)

    def behave(self, **kw):
        path = self.home / "behavior.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(kw)
        path.write_text(json.dumps(data), encoding="utf-8")

    def calls(self):
        path = self.home / "argv.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def resumes(self):
        return [c for c in self.calls() if c[0] == "resume"]

    def jobs(self):
        path = self.home / "jobs.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def set_job(self, job_id, **kw):
        jobs = self.jobs()
        jobs[job_id].update(kw)
        (self.home / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")

    def data(self):
        return self.module_store().load({})

    def module_store(self):
        pack = next(p for p in self.bot.app.extras["synox_packs"] if p.name == NAME)
        return pack.store.of(self.bot.app.users.get(OWNER_ID))

    def authenticator(self):
        from synox import guard
        from synox.guard.testing import Clock, code_for, enroll
        self.guard, self.clock = guard.of(self.bot.app), Clock()
        secret = enroll(self.guard, OWNER_ID, self.clock)
        return lambda: code_for(secret, self.clock)

    def unlock(self):
        code = self.authenticator()
        self.say(f"/unlock {code()}")


@pytest.fixture
def rig(synox_bot, tmp_path):
    bot = synox_bot(NAME)
    r = Rig(bot, make_resetter(tmp_path))
    r.unlock()
    reply = r.say(f"/relay_setup home={r.home} python={sys.executable}")
    assert "找得到 ClaudeTimerResetter" in reply, reply
    return r


def armed(rig, sid=SID, hours=12, uses=3):
    reply = rig.say(f"/relay_arm {sid} hours={hours} uses={uses}")
    assert "授權了" in reply, reply
    return reply


# ── 宣告 ─────────────────────────────────────────────────────────────

def test_manifest_is_private_owner_only_and_scoped():
    m = synox_manifest.read(FOLDER, strict=True)
    assert m.raw["publish"] is False and NAME not in PACK_NAMES and NAME in ALL_PACK_NAMES
    assert m.access == "owner" and m.surfaces == ["dm"] and m.data == "user" and m.storage == "vault"
    assert m.capabilities == ["filesystem:own", "subprocess"]
    assert m.version.endswith("-alpha.1") and m.raw["stage"] == "test"
    callers = m.raw["callers"]
    assert {k for k, v in callers.items() if v == ["human", "ai"]} == AI_OK
    assert {k for k, v in callers.items() if v == ["human"]} == HUMAN_ONLY
    assert callers["continue"] == ["human", "schedule"]
    assert set(m.raw["stepup"]) == STEPUP
    report = synox_scan.scan(m)
    assert report.problems(m) == [], report.format(m)


def test_private_pack_stays_out_of_published_index(tmp_path):
    """publish: false：全部打包的 index.json 沒有它；指名打包照樣過 SYNOX 第 1、2 關，寫到 dist/private/。"""
    assert NAME in build_script.private_names()
    assert build_script.default_out([NAME]).endswith(os.path.join("dist", "private"))
    result = build_script.build([NAME], out_dir=str(tmp_path / "one"))
    assert result["problems"] == [], result["problems"]
    full = build_script.build(out_dir=str(tmp_path / "all"))
    assert NAME not in {e["name"] for e in full["index"]["packs"]}
    assert not [n for n in os.listdir(tmp_path / "all") if NAME in n]


def test_policy_ai_remote_schedule_and_stepup(synox_bot):
    """AI 只看得到唯讀與 freeze；continue 收本人與排程、AI 與手機遠端不行；arm／thaw／setup 沒設定驗證器一律不行。"""
    from synox import policy
    from synox.guard.testing import as_origin
    bot = synox_bot(NAME)
    leaves = {n.name: n for n in bot.app.registry.node("relay").walk() if not n.is_group}
    assert set(leaves) == AI_OK | HUMAN_ONLY | {"continue"}
    owner = bot.app.users.get_or_create(OWNER_ID)
    for name, node in leaves.items():
        assert policy.decide(bot.app, owner, node, via=AI).allowed == (name in AI_OK), name
        assert node.ai == (name in AI_OK), name
        with as_origin("telegram", OWNER_ID):
            d = policy.decide(bot.app, owner, node, via=TYPED, chat_id=OWNER_ID)
            if name in STEPUP:
                assert not d.allowed and d.reason == "stepup_unset", name
            else:
                assert d.allowed, (name, d)
        with as_origin("remote", OWNER_ID):
            d = policy.decide(bot.app, owner, node, via=TYPED, chat_id=OWNER_ID)
            assert not d.allowed, name
    cont = leaves["continue"]
    with as_origin("schedule", OWNER_ID):
        assert policy.decide(bot.app, owner, cont, via=SCHEDULE, chat_id=OWNER_ID).allowed
        assert policy.decide(bot.app, owner, cont, via=ROUTINE, chat_id=OWNER_ID).allowed
        for name in HUMAN_ONLY | {"freeze"}:
            assert not policy.decide(bot.app, owner, leaves[name], via=ROUTINE, chat_id=OWNER_ID).allowed, name
    assert {n for n, node in leaves.items() if str(node.effect).upper() == "DANGER"} == {"arm", "continue"}


def test_stepup_commands_need_an_unlocked_authenticator(synox_bot, tmp_path):
    bot = synox_bot(NAME)
    r = Rig(bot, make_resetter(tmp_path))
    assert "手機驗證器" in r.say(f"/relay_setup home={r.home} python={sys.executable}")    # 沒設定驗證器
    code = r.authenticator()
    assert "解鎖" in r.say(f"/relay_setup home={r.home} python={sys.executable}")          # 設定了、還沒解鎖
    assert "解鎖" in r.say("/relay_thaw")
    r.say(f"/unlock {code()}")
    assert "找得到 ClaudeTimerResetter" in r.say(f"/relay_setup home={r.home} python={sys.executable}")


def test_ai_can_freeze_but_not_continue(host_bot, tmp_path):
    bot = host_bot([{"tool": "relay__freeze", "args": {}}, "停了",
                    {"tool": "relay__continue", "args": {"session": SID}}, "好"])
    bot.talk(OWNER_ID, "快停下來")
    adapter = bot.app.extras["synox_agent"].adapter
    names = {t.name for t in adapter.calls[0][1]}
    assert "relay__freeze" in names and "relay__armed" in names
    assert not names & {"relay__continue", "relay__arm", "relay__thaw", "relay__setup", "relay__disarm", "relay__cancel"}
    assert "凍結了" in adapter.calls[1][0][-1].content
    bot.talk(OWNER_ID, "那幫我接著做")
    assert "relay__continue" in adapter.calls[3][0][-1].content and "凍結了" not in adapter.calls[3][0][-1].content


# ── argv：唯一組 argv 的函式，輸出鎖死 ─────────────────────────────────────

def test_relay_argv_outputs_are_locked():
    m = load_module(NAME)
    assert m.PROMPT == "continue" and m.PERMISSION == "inherit"
    assert m.relay_argv("continue", session=SID, budget="2") == CONTINUE_ARGV
    assert m.relay_argv("continue", session=SID, budget="1.5", at="2026-11-24 06:05") == [
        "resume", "--json", "--prompt=continue", "--permission-mode=inherit", "--max-budget-usd=1.5",
        "--at=2026-11-24 06:05", "--", SID]
    assert m.relay_argv("sessions") == ["sessions", "--json"]
    assert m.relay_argv("sessions", cwd="/p/x", limit=5, everything=True) == ["sessions", "--json", "--cwd=/p/x", "--limit=5", "--all"]
    assert m.relay_argv("show", session=SID, turns=2) == ["show", "--json", "--turns=2", "--", SID]
    assert m.relay_argv("check", session=SID) == ["check", "--json", "--", SID]
    assert m.relay_argv("jobs") == ["jobs", "--json", "--limit=200"]
    assert m.relay_argv("job", job="a1b2c3d4") == ["job", "--json", "--", "a1b2c3d4"]
    assert m.relay_argv("cancel", job="a1b2c3d4") == ["cancel", "--json", "--", "a1b2c3d4"]
    for kind in ("new", "release", "stash", "fire", "watch", "resume", "--take-over"):
        with pytest.raises(m.BridgeProblem):
            m.relay_argv(kind, session=SID, budget="2")
    for bad in ("2; rm", "--fork", "", "100", "2.123"):
        with pytest.raises(m.BridgeProblem):
            m.relay_argv("continue", session=SID, budget=bad)
    for bad in ("+30", "06:05", "2026-11-24 06:05 --take-over", "2026-11-24T06:05"):
        with pytest.raises(m.BridgeProblem):
            m.relay_argv("continue", session=SID, budget="2", at=bad)


@pytest.mark.parametrize("bad", ["--take-over", "x;rm -rf ~", SID + "\n--fork", SID + "\x00", SID.upper(), " " + SID,
                                 SID + " ", SID[:-1], SID + "0", "0123abcd000040008000000000abcd", "../" + SID,
                                 "０123abcd-0000-4000-8000-00000000abcd"])
def test_session_ids_are_strict(bad):
    m = load_module(NAME)
    with pytest.raises(m.BridgeProblem):
        m.check_session(bad)
    with pytest.raises(m.BridgeProblem):
        m.relay_argv("continue", session=bad, budget="2")


@pytest.mark.parametrize("bad", ["--take-over", "x;rm", "+30\n", "+0", "+3000", "+-5", "+1.5", "24:00", "6:60", "06:05:00",
                                 "2026-02-30 06:05", "2020-01-01 06:05", "tomorrow", "+３０", "06：05", "06:05\x00", ""])
def test_at_formats_are_strict(bad):
    from datetime import datetime
    m = load_module(NAME)
    with pytest.raises(m.BridgeProblem):
        m.parse_at(bad, datetime(2026, 11, 23, 22, 0, 30))


def test_at_formats_resolve_like_the_resetter():
    from datetime import datetime
    m = load_module(NAME)
    current = datetime(2026, 11, 23, 22, 0, 30)
    assert m.parse_at("+30", current) == datetime(2026, 11, 23, 22, 31)
    assert m.parse_at("23:15", current) == datetime(2026, 11, 23, 23, 15)
    assert m.parse_at("06:05", current) == datetime(2026, 11, 24, 6, 5)          # 過了就明天
    assert m.parse_at("2026-11-24 06:05", current) == datetime(2026, 11, 24, 6, 5)


def test_pack_py_builds_resetter_argv_in_exactly_one_place():
    """靜態：module_argv 只在 call() 裡被叫一次，而且尾段只來自 relay_argv；app.jobs 只在 call() 裡；
    整份 pack.py 的字串常數裡沒有搶佔、分支、釋放、開新對話、改權限、工具白名單、通知這些旗標或子指令。"""
    text = open(os.path.join(FOLDER, "pack.py"), encoding="utf-8").read()
    tree = ast.parse(text)
    owners = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                owners[id(node)] = fn.name
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    argv_calls = [n for n in calls if getattr(n.func, "id", None) == "module_argv"]
    assert [owners[id(n)] for n in argv_calls] == ["call"]
    assert ast.unparse(argv_calls[0].args[2]).startswith("relay_argv(")
    jobs_calls = [n for n in calls if ast.unparse(n.func).startswith("app.jobs.")]
    assert [owners[id(n)] for n in jobs_calls] == ["call"] and ast.unparse(jobs_calls[0].func) == "app.jobs.run"
    assert [owners[id(n)] for n in calls if getattr(n.func, "id", None) == "relay_argv"] == ["call"]
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    banned = ("--take-over", "--fork", "--even-busy", "--allowed-tools", "--disallowed-tools", "--notify", "--sandbox",
              "--no-require-clean", "--dry-run", "bypassPermissions", "acceptEdits", "dontAsk")
    for s in strings:
        assert not [b for b in banned if b in s], s
    for word in ("new", "release", "stash", "fire", "watch"):
        assert word not in strings, word
    assert "shell=True" not in text and "subprocess" not in text.replace('"subprocess"', "")
    for pattern in (r"\bos\.system\b", r"\beval\(", r"\bexec\(", r"__import__", r"\bos\.environ\b", r"\bgetenv\b", r"\bapi\.post\("):
        assert not re.search(pattern, text), pattern
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    assert mods <= {"json", "os", "re", "threading", "time", "datetime", "isdtelekit", "synox"}, mods
    opens = [n for n in calls if getattr(n.func, "id", None) == "open"]
    assert opens == []                                   # 不自己開檔；resetter 的輸出由 JobRunner 寫、read_log 讀


def test_shared_bridge_block_matches_canonical():
    import sync_shared
    canonical = sync_shared.block(sync_shared.read(os.path.join(sync_shared.SHARED, "resetterbridge.py")), "resetterbridge")
    assert sync_shared.block(sync_shared.read(os.path.join(FOLDER, "pack.py")), "resetterbridge") == canonical
    assert NAME in sync_shared.TARGETS["resetterbridge"]


# ── continue：授權、argv、拒絕 ──────────────────────────────────────────

def test_unarmed_is_refused_then_armed_sends_exact_argv(rig):
    reply = rig.say(f"/relay_continue {SID}")
    assert "沒有授權" in reply and f"/relay_arm {SID}" in reply and rig.resumes() == []
    armed(rig)
    reply = rig.say(f"/relay_continue {SID}")
    assert "送出 continue 了" in reply and "還剩 2 次" in reply, reply
    assert rig.resumes() == [CONTINUE_ARGV]
    assert rig.data()["armed"][SID]["cwd"] == "/projects/demo0"


def test_at_goes_to_the_resetter_scheduler_inside_the_window(rig, monkeypatch):
    from datetime import datetime
    base = datetime(2026, 11, 23, 22, 0, 30).timestamp()
    monkeypatch.setattr(rig.module, "now", lambda: base)
    armed(rig, hours=12)
    reply = rig.say(f"/relay_continue {SID} at=06:05")
    assert "排好了" in reply and "2026-11-24 06:05" in reply, reply
    assert rig.resumes()[-1] == CONTINUE_ARGV[:5] + ["--at=2026-11-24 06:05", "--", SID]
    assert "超過授權期限" in rig.say(f"/relay_continue {SID} at=2026-11-24 10:05")
    assert "超過授權期限" in rig.say(f"/relay_continue {SID} at=+721")
    for bad in ("--take-over", "x;rm", "tomorrow"):
        assert "at 只收三種寫法" in rig.say(f"/relay_continue {SID} at={bad}"), bad
    assert len(rig.resumes()) == 1


def test_expired_used_up_frozen_and_daily_limit(rig, monkeypatch):
    clock = {"t": time.time()}
    monkeypatch.setattr(rig.module, "now", lambda: clock["t"])
    armed(rig, hours=1, uses=1)
    assert "送出 continue" in rig.say(f"/relay_continue {SID}")
    assert "次數用完" in rig.say(f"/relay_continue {SID}")
    armed(rig, hours=1, uses=2)
    clock["t"] += 3601
    assert "過期" in rig.say(f"/relay_continue {SID}")
    armed(rig, hours=1, uses=2)
    assert "凍結了" in rig.say("/relay_freeze")
    assert "凍結的" in rig.say(f"/relay_continue {SID}")
    assert "解除凍結了" in rig.say("/relay_thaw")
    # 每日上限：24 小時內 10 次（全部對話加起來）
    rig.module_store().update(lambda d: d.__setitem__("sent", [clock["t"] - 100] * 10), {})
    assert "上限" in rig.say(f"/relay_continue {SID}")
    clock["t"] += 200
    rig.module_store().update(lambda d: d.__setitem__("sent", [clock["t"] - 86401] * 10 + [clock["t"] - 5] * 9), {})
    assert "送出 continue" in rig.say(f"/relay_continue {SID}")
    assert len(rig.resumes()) == 2
    results = [a["result"] for a in rig.data()["audit"] if a["act"] == "continue"]
    assert results[0].startswith("ok:") and "refused:used_up" in results and "refused:expired" in results
    assert "refused:frozen" in results and "refused:daily_limit" in results


def test_continue_from_a_synox_schedule_runs_as_schedule(rig):
    from datetime import datetime
    armed(rig)
    rig.say(f"/schedule_add 06:05 /relay_continue {SID}")
    assert f"/relay_continue {SID}" in rig.say("/schedule_list")
    rig.bot.app.extras["synox_schedule_runner"].tick(datetime(2026, 11, 24, 6, 5, 5))
    assert rig.bot.app.tasks.wait(10)
    assert rig.resumes() == [CONTINUE_ARGV]
    last = [a for a in rig.data()["audit"] if a["act"] == "continue"][-1]
    assert last["src"] == "schedule" and last["result"].startswith("ok:")


@pytest.mark.parametrize("sid", [APP_SID, TEMP_SID, PIPE_SID, OLD_SID])
def test_arm_refuses_program_temp_and_unknown_origin_sessions(rig, sid):
    """資安審查 3：程式開的（entrypoint 不是 VSCode／終端機）、暫存或程式資料夾裡的、看不出來源的，都不授權。"""
    reply = rig.say(f"/relay_arm {sid}")
    assert "不授權" in reply and sid not in rig.data()["armed"], reply
    assert rig.data()["audit"][-1]["result"].startswith("refused:origin")
    assert not [c for c in rig.calls() if c[0] == "check"]


def test_arm_runs_check_and_warns_about_permission_fallback(rig):
    rig.behave(check_exit=1)
    assert "送不出去" in rig.say(f"/relay_arm {SID}") and SID not in rig.data()["armed"]
    rig.behave(check_exit=3)
    reply = armed(rig)
    assert "正被開著" in reply and "自動同意改檔" in reply and "auto" in reply
    assert rig.calls()[-1] == ["check", "--json", "--", SID]


def test_arm_needs_a_listed_session_and_disarm_cancels_pending(rig):
    other = "0123abcd-2222-4000-8000-00000000abcd"
    assert "找不到" in rig.say(f"/relay_arm {other}")
    assert "1 到 48" in rig.say(f"/relay_arm {SID} hours=49")
    assert "1 到 10" in rig.say(f"/relay_arm {SID} uses=0")
    assert "完整的 id" in rig.say("/relay_arm --take-over")
    armed(rig)
    armed(rig, SID2)
    rig.say(f"/relay_continue {SID} at=+30")
    pending = list(rig.jobs())
    reply = rig.say(f"/relay_disarm {SID}")
    assert "取消了 1 個對話" in reply and "1 筆工作" in reply
    assert rig.jobs()[pending[0]]["status"] == "cancelled"
    assert SID not in rig.data()["armed"] and SID2 in rig.data()["armed"]
    assert "取消了 1 個對話" in rig.say("/relay_disarm all") and rig.data()["armed"] == {}


# ── 被佔用、失敗、凍結 ───────────────────────────────────────────────────

def test_busy_now_does_not_use_a_turn_and_leaves_nothing_waiting(rig):
    armed(rig, uses=2)
    rig.behave(resume_exit=3)
    reply = rig.say(f"/relay_continue {SID}")
    assert "對話正被開著" in reply and "沒有扣次數" in reply and "at=+5" in reply, reply
    assert rig.data()["armed"][SID]["uses"] == 2 and rig.data()["sent"] == [] and rig.data()["fails"] == 0
    assert [j["status"] for j in rig.jobs().values()] == ["cancelled"]        # 現在送被佔用：不留沒人管的等待中工作
    rig.behave(resume_exit=0)
    rig.say(f"/relay_continue {SID} at=+10")
    job_id = [j for j, v in rig.jobs().items() if v["status"] == "scheduled"][0]
    rig.set_job(job_id, status="waiting")
    assert "每 2 分鐘補試" in rig.say("/relay_jobs")


def test_two_failures_in_a_row_freeze_and_cancel(rig):
    armed(rig, uses=5)
    rig.say(f"/relay_continue {SID} at=+30")                    # 一筆排著、還沒送
    scheduled = list(rig.jobs())[0]
    rig.behave(resume_exit=1)
    reply = rig.say(f"/relay_continue {SID}")
    assert "沒送成" in reply and "claude 秒退" in reply and "凍結" not in reply, reply
    rig.behave(resume_exit=99)                                   # 看不懂的輸出也算失敗
    reply = rig.say(f"/relay_continue {SID}")
    assert "自動凍結" in reply and "取消了 1 筆" in reply, reply
    assert rig.jobs()[scheduled]["status"] == "cancelled"
    data = rig.data()
    assert data["frozen"] and data["armed"][SID]["uses"] == 2          # 結束碼 1 與看不懂的輸出都不退次數
    assert "凍結的" in rig.say(f"/relay_continue {SID}")


def test_refund_only_when_claude_was_provably_not_started(rig):
    """只有結束碼 2、3、4 退次數；1 不退；逾時、看不懂的輸出不退，稽核標 unknown。"""
    armed(rig, uses=9)
    for code, refunded in ((2, True), (3, True), (4, True), (1, False)):
        before = rig.data()["armed"][SID]["uses"]
        rig.behave(resume_exit=code)
        reply = rig.say(f"/relay_continue {SID}")
        assert rig.data()["armed"][SID]["uses"] == (before if refunded else before - 1), (code, reply)
        rig.say("/relay_thaw")
    rig.behave(resume_exit=99)
    rig.say(f"/relay_continue {SID}")
    assert rig.data()["armed"][SID]["uses"] == 7 and rig.data()["audit"][-1]["result"] == "unknown:unparseable"


def test_timeout_does_not_refund(rig, monkeypatch):
    armed(rig, uses=3)
    monkeypatch.setattr(rig.module, "SEND_TIMEOUT", 1)
    rig.behave(resume_sleep=5)
    reply = rig.say(f"/relay_continue {SID}")
    assert "沒有回應" in reply and "結果不明" in reply, reply
    assert rig.data()["armed"][SID]["uses"] == 2 and rig.data()["audit"][-1]["result"] == "unknown:timeout"


def test_unknown_outcome_is_adopted_from_the_resetter_list(rig):
    armed(rig, uses=3)
    rig.behave(garble=True)
    reply = rig.say(f"/relay_continue {SID} at=+30")
    assert "結果不明" in reply and rig.data()["jobs"] == [] and len(rig.data()["unknown"]) == 1
    job_id = list(rig.jobs())[0]
    rig.behave(garble=False)
    assert job_id in rig.say("/relay_jobs")                      # 補記進來了
    d = rig.data()
    assert d["unknown"] == [] and [j["id"] for j in d["jobs"]] == [job_id]
    assert any(a["act"] == "adopt" and a["result"] == "ok:" + job_id for a in d["audit"])
    rig.say("/relay_freeze")                                      # 補記的那筆，凍結時一樣取消
    assert rig.jobs()[job_id]["status"] == "cancelled"


def test_refund_goes_only_to_the_same_authorization(rig):
    """送出去的那幾秒裡被撤回又重新授權：被佔用退回的那一次不能加到新的授權上。"""
    armed(rig, uses=2)
    rig.behave(resume_exit=3, resume_sleep=2)
    rig.bot.say(OWNER_ID, f"/relay_continue {SID}")
    time.sleep(0.7)
    rig.say(f"/relay_disarm {SID}")
    armed(rig, uses=2)
    assert rig.bot.app.tasks.wait(20)
    assert rig.data()["armed"][SID]["uses"] == 2


@pytest.mark.parametrize("stop", ["/relay_freeze", f"/relay_disarm {SID}"])
def test_freeze_or_disarm_during_an_inflight_continue_cancels_it(rig, stop):
    """資安審查 1：continue 已經在叫 resetter（假的 resume 睡 2 秒），這時凍結或撤回授權：那一筆一定被取消、不扣次數。"""
    armed(rig, uses=3)
    rig.behave(resume_sleep=2)
    rig.bot.say(OWNER_ID, f"/relay_continue {SID} at=+30")
    time.sleep(0.8)
    rig.bot.say(OWNER_ID, stop)
    assert rig.bot.app.tasks.wait(20)
    assert [v["status"] for v in rig.jobs().values()] == ["cancelled"]
    d = rig.data()
    assert "revoked:" in [a["result"] for a in d["audit"] if a["act"] == "continue"][-1]
    assert d["sent"] == [] and [j["status"] for j in d["jobs"]] == ["cancelled"]
    if stop == "/relay_freeze":
        assert d["frozen"] and d["armed"][SID]["uses"] == 3
    else:
        assert SID not in d["armed"]
    assert "送出的同時被" in "\n".join(rig.bot.texts(OWNER_ID))


def test_expired_authorization_jobs_are_swept_on_the_next_command(rig, monkeypatch):
    """資安審查 2：授權過期（之後被 arm 清掉）時，排好、還沒送的工作在下一個指令進來時取消。"""
    clock = {"t": time.time()}
    monkeypatch.setattr(rig.module, "now", lambda: clock["t"])
    armed(rig, hours=1, uses=1)
    assert "排好了" in rig.say(f"/relay_continue {SID} at=+50")
    job_id = list(rig.jobs())[0]
    clock["t"] += 3601
    armed(rig, SID2)                                   # 清掉過期的 SID；sweep 在這個指令進來時就收
    assert SID not in rig.data()["armed"]
    assert rig.jobs()[job_id]["status"] == "cancelled"
    assert any("取消了 1 筆還沒送的工作" in t for t in rig.bot.texts(OWNER_ID))


def test_disarm_without_authorization_still_cancels_pending(rig):
    armed(rig)
    rig.say(f"/relay_continue {SID} at=+30")
    job_id = list(rig.jobs())[0]
    rig.module_store().update(lambda d: d["armed"].clear(), {})      # 例如授權過期被清掉（sweep 或 disarm 自己，哪個先都要收掉）
    reply = rig.say(f"/relay_disarm {SID}")
    assert rig.jobs()[job_id]["status"] == "cancelled", reply
    assert "本來就沒有授權，也沒有還沒送的工作" in rig.say(f"/relay_disarm {SID}")


def test_rearm_keeps_scheduled_jobs(rig):
    armed(rig, hours=2)
    rig.say(f"/relay_continue {SID} at=+30")
    job_id = list(rig.jobs())[0]
    armed(rig, hours=12)                              # 延長：同一筆授權，排好的照舊
    rig.say("/relay_armed")
    assert rig.jobs()[job_id]["status"] == "scheduled"


def test_job_reported_failed_counts_toward_freeze(rig):
    armed(rig, uses=5)
    rig.say(f"/relay_continue {SID}")
    rig.say(f"/relay_continue {SID}")
    first, second = list(rig.jobs())
    rig.set_job(first, status="failed", error="claude 回報錯誤：error_max_budget_usd")
    assert "失敗" in rig.say("/relay_jobs") and not rig.data()["frozen"]
    rig.set_job(second, status="failed")
    assert "自動凍結" in rig.say(f"/relay_job {second}")
    assert rig.data()["frozen"]
    assert "解除凍結" in rig.say("/relay_thaw") and rig.data()["fails"] == 0


def test_freeze_reason_and_audit_keep_only_categories(rig):
    """凍結原因與稽核只放分類；resetter 的錯誤原文只在那一筆工作的 error，而且截斷。"""
    armed(rig, uses=5)
    secret = "private-detail " + "x" * 1000
    rig.behave(resume_exit=1, resume_error=secret)
    rig.say(f"/relay_continue {SID}")
    rig.say(f"/relay_continue {SID}")
    d = rig.data()
    assert d["frozen"] and d["frozen_why"].endswith("（最後一次：failed:exit1）"), d["frozen_why"]
    blob = json.dumps(d["audit"], ensure_ascii=False) + d["frozen_why"]
    assert "private" not in blob and "xxx" not in blob
    assert [a["result"] for a in d["audit"] if a["act"] == "continue"] == ["failed:exit1", "failed:exit1"]
    assert all(len(j["error"]) <= 200 and "private" in j["error"] for j in d["jobs"])
    rig.say("/relay_thaw")
    rig.behave(resume_exit=99)
    rig.say(f"/relay_continue {SID}")
    assert rig.data()["audit"][-1]["result"] == "unknown:unparseable"


def test_audit_keeps_200_and_has_no_content_or_codes(rig):
    armed(rig)
    for _ in range(3):
        rig.say(f"/relay_continue {SID2}")                       # 沒授權：每次留一筆拒絕
    rig.module_store().update(lambda d: d["audit"].extend([dict(d["audit"][-1])] * 250), {})
    rig.say(f"/relay_continue {SID2}")
    audit = rig.data()["audit"]
    assert len(audit) == 200
    assert set(audit[0]) == {"t", "act", "src", "sid", "result"}
    blob = json.dumps(audit, ensure_ascii=False)
    assert SID not in blob and SID[:8] in blob                   # 只記前 8 碼
    assert "continue" in blob and "假的對話" not in blob and "請繼續" not in blob and "/projects" not in blob
    assert not re.search(r"\b\d{6}\b", blob.replace(SID[:8], ""))
    setup = rig.say("/relay_setup budget=1.5")
    assert "1.5" in setup
    assert rig.data()["audit"][-1]["result"] == "ok:budget"
    assert "最近的紀錄" in rig.say("/relay_armed")


def test_jobs_lists_only_our_own_and_cancel_is_scoped(rig):
    armed(rig)
    rig.behave(foreign_jobs=[{"id": "ffffffff", "status": "scheduled", "session_id": SID, "kind": "new"}])
    rig.say(f"/relay_continue {SID} at=+30")
    mine = list(rig.jobs())[0]
    listing = rig.say("/relay_jobs")
    assert mine in listing and "ffffffff" not in listing
    assert "不是這個工具送出的" in rig.say("/relay_job ffffffff")
    assert "不是這個工具送出的" in rig.say("/relay_cancel ffffffff")
    assert "8 個小寫英數字" in rig.say("/relay_cancel --all")
    assert "取消了工作" in rig.say(f"/relay_cancel {mine}")
    assert rig.jobs()[mine]["status"] == "cancelled"
    assert "已經結束" in rig.say(f"/relay_cancel {mine}")
    assert not [c for c in rig.calls() if c[0] == "cancel" and c[-1] == "ffffffff"]


def test_read_only_commands_and_check(rig):
    armed(rig)
    listing = rig.say("/relay_sessions")
    assert f"✅ {SID}" in listing and f"· {SID2}" in listing
    assert "做到一半" in rig.say(f"/relay_show {SID} turns=2")
    assert rig.calls()[-1] == ["show", "--json", "--turns=2", "--", SID]
    assert "叫得動" in rig.say("/relay_check")
    assert "現在可以送" in rig.say(f"/relay_check {SID}")
    rig.behave(check_exit=3)
    assert "正被開著" in rig.say(f"/relay_check {SID}")
    assert "剩 3 次" in rig.say("/relay_armed")


def test_ai_reads_summaries_not_conversation_content(host_bot, tmp_path):
    """資安審查 4：AI 叫 show 只拿到標題、時間、輪數（不帶 --turns、不給最後一句與回覆）；sessions 不給最後一句；
    job 的結果原文不給 AI、改動壓成一行。本人看得到全部。"""
    home = make_resetter(tmp_path)
    jobs = {"a1b2c3d4": {"id": "a1b2c3d4", "kind": "resume", "session_id": SID, "status": "done", "result": "它最後的回答",
                         "error": "", "cost_usd": 0.1, "prompt": "continue", "at": ""}}
    (home / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    bot = host_bot([{"tool": "relay__show", "args": {"session": SID, "turns": "3"}}, "好",
                    {"tool": "relay__sessions", "args": {}}, "好",
                    {"tool": "relay__job", "args": {"job": "a1b2c3d4"}}, "好"])
    store = next(p for p in bot.app.extras["synox_packs"] if p.name == NAME).store.of(bot.app.users.get_or_create(OWNER_ID))
    store.save({"settings": {"home": str(home), "python": sys.executable},
                "jobs": [{"id": "a1b2c3d4", "sid": SID, "at": "", "created": 0, "src": "human", "status": "sent",
                          "cost": 0.0, "error": "", "counted": False, "auth": ""}]})
    adapter = None
    for prompt in ("看一下那個對話", "列對話", "看結果"):
        bot.talk(OWNER_ID, prompt)
        adapter = bot.app.extras["synox_agent"].adapter
    shown, listed, job = (adapter.calls[i][0][-1].content for i in (1, 3, 5))
    assert "假的對話" in shown and "5 輪" in shown and "只給使用者本人看" in shown
    assert "請繼續" not in shown and "做到一半" not in shown and "第五輪" not in shown
    calls = [json.loads(x) for x in (home / "argv.jsonl").read_text(encoding="utf-8").splitlines()]
    assert ["show", "--json", "--", SID] in calls and not [c for c in calls if c[0] == "show" and "--turns=3" in c]
    assert SID in listed and "請繼續" not in listed
    assert "它最後的回答" not in job and "a.py | 2 +-" in job and "\n    a.py" not in job


def test_show_only_for_listed_sessions_and_owner_sees_turns(rig):
    assert "不在一般的對話清單" in rig.say(f"/relay_show {APP_SID}")
    assert not [c for c in rig.calls() if c[0] == "show"]
    reply = rig.say(f"/relay_show {SID} turns=2")
    assert "第五輪的回答" in reply and "做到一半" in reply
    assert "請繼續" in rig.say("/relay_sessions")


def test_not_configured_says_how(synox_bot):
    bot = synox_bot(NAME)
    bot.say(OWNER_ID, "/relay_sessions")
    assert bot.app.tasks.wait(10)
    assert "/relay_setup home=" in bot.last(OWNER_ID)
