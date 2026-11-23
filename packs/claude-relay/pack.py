"""🔁 對話接力：讓「因為額度停住的 Claude Code 對話」在你事先授權之後，被送一句固定的 continue 接著做下去。

    /relay_sessions [cwd=…] [limit=N]      列出這台電腦上的對話（標出授權過的）      本人、AI
    /relay_show <對話> [turns=N]           一個對話做到哪裡                         本人、AI
    /relay_check [<對話>]                  環境；給了對話就看它現在能不能送           本人、AI
    /relay_arm <對話> [hours=12] [uses=3]  授權（有期限、有次數）                     只有本人＋手機驗證器
    /relay_disarm <對話>|all               取消授權（連這個工具排了、還沒送的一起取消）  只有本人
    /relay_armed                          授權名單、剩幾次、幾點到期、最近的稽核       本人、AI
    /relay_continue <對話> [at=時間]       送固定的 continue（at＝交給 resetter 的排程器到點送）  本人、排程
    /relay_jobs、/relay_job <編號>          這個工具送出過的工作與結果                   本人、AI
    /relay_cancel <編號>                   取消這個工具排的、還沒送的工作               只有本人
    /relay_freeze                         緊急停止：之後一律拒絕，並取消還沒跑完的       本人、AI
    /relay_thaw                           解除緊急停止                               只有本人＋手機驗證器
    /relay_setup [home=…] [python=…] [budget=美元]  設定                            只有本人＋手機驗證器

邊界寫在結構裡，不靠檢查文字：
- 送出去的永遠是常數 PROMPT；權限模式永遠是常數 PERMISSION（inherit：resetter 照那個對話最後一次互動時的權限，
  而且它自己會把「略過所有權限」降成 auto）。沒有任何指令有能放自由文字送給 resetter 的參數。
- 呼叫 resetter 的 argv 只由 relay_argv() 組出來（固定幾種子指令、每個值先過嚴格格式），再經共用的 module_argv()
  變成 [python, -m, claude_timer.cli, …]；只在 install() 裡的 call() 交給 SYNOX 的 JobRunner（app.jobs：argv 陣列、
  不經 shell、有逾時、子程序環境拿掉像密鑰的變數）。tests/test_claude_relay.py 鎖住這兩件事。
- 誰能叫寫在 pack.json 的 callers／stepup（SYNOX 在執行時檢查）；這裡再用 kit.caller() 自己檢查一次（多一層，不是邊界）。

資料：設定、授權名單、凍結旗標、送出的工作編號、稽核都在 pack.store.of(user)（保管箱，密文）。
每次叫 resetter 的輸出暫存在 pack.path_of(user, "calls", …)，讀完就刪。稽核不記驗證碼、不記對話內容。
"""

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta

from isdtelekit.core.registry import DANGER, WRITE, Field
from isdtelekit.jobs.cli import clean_env
from isdtelekit.jobs.runner import TIMED_OUT, read_log

from synox.packs import kit

# >>> resetterbridge（正本 packs/_shared/resetterbridge.py；不要直接改這段，改正本再跑 scripts/sync_shared.py）
RESETTER_MODULES = {"usage": "claude_timer.core.usage",     # 看額度（不花錢，純 HTTPS GET）
                    "ping": "claude_timer.core.ping",       # 該刷新就刷新（花約 $0.00025）
                    "cli": "claude_timer.cli"}              # 接續／新對話（relay 用）


class BridgeProblem(Exception):
    """回使用者一句話（指令回訊息、畫面顯示），不算工具壞掉。"""

    def __init__(self, text):
        super().__init__(text)
        self.text = str(text)


def resolve_resetter(settings, setup_command):
    """設定 → (python 路徑, resetter 資料夾)。找不到就丟 BridgeProblem（白話：哪裡不對、怎麼改）。
    settings["home"]＝ClaudeTimerResetter 的 clone 資料夾；settings["python"]（可留空）＝要用的 Python，留空就用 home\\.venv 的。"""
    home = (settings.get("home") or "").strip().strip('"')
    if not home:
        raise BridgeProblem(f"還沒告訴我 ClaudeTimerResetter 在哪裡。請你本人打：{setup_command} home=<ClaudeTimerResetter 的資料夾>\n"
                            "（要是從原始碼跑的那份：資料夾裡有 claude_timer\\，跑過一次 scripts\\setup.bat 建好 .venv）")
    folder = os.path.abspath(os.path.expanduser(home))
    if not os.path.isfile(os.path.join(folder, "claude_timer", "__init__.py")):
        raise BridgeProblem(f"這個資料夾裡找不到 claude_timer（不是 ClaudeTimerResetter 的原始碼資料夾？）：{folder}\n"
                            f"用 {setup_command} home=… 重新設定。只有 exe 版的不能用，要 clone 專案。")
    python = (settings.get("python") or "").strip().strip('"')
    if not python:
        for rel in ((".venv", "Scripts", "python.exe"), (".venv", "bin", "python")):
            if os.path.isfile(os.path.join(folder, *rel)):
                python = os.path.join(folder, *rel)
                break
    if not python:
        raise BridgeProblem("ClaudeTimerResetter 旁邊沒有 .venv（它自己的 Python 環境）。先在它的資料夾跑一次 scripts\\setup.bat，"
                            f"或用 {setup_command} python=<python.exe> 指定。")
    if not os.path.isfile(python):
        raise BridgeProblem(f"找不到設定的 Python：{python}\n用 {setup_command} python=… 改（留空＝用 .venv 的）。")
    return python, folder


def module_argv(python, entry, tail):
    """[python, -m, <模組>, *tail]。entry 只能是 RESETTER_MODULES 的鍵；tail 每一項都得是字串，不准空位元組。"""
    module = RESETTER_MODULES.get(entry)
    if module is None:
        raise BridgeProblem(f"內部錯誤：不認得的入口 {entry!r}")
    items = [str(t) for t in tail]
    if any("\x00" in t for t in items):
        raise BridgeProblem("參數裡有不能用的字元。")
    return [python, "-m", module] + items


def last_json(text):
    """輸出裡最後一份 JSON 物件（可以印好幾行；前面是進度與雜訊）。沒有回 None。"""
    text = text or ""
    lines = text.splitlines(keepends=True)
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)
    decoder = json.JSONDecoder()
    for i in range(len(lines) - 1, -1, -1):
        if not lines[i].startswith("{"):
            continue
        try:
            data, _end = decoder.raw_decode(text[offsets[i]:])
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None
# <<< resetterbridge


class CallProblem(BridgeProblem):
    """叫 resetter 沒有得到看得懂的結果。kind＝分類（call_error／timeout／unparseable），稽核與凍結原因只放分類，
    不放錯誤原文（原文可能帶路徑或 resetter 印的任何字）。"""

    def __init__(self, text, kind):
        super().__init__(text)
        self.kind = kind

# ── 常數（邊界） ──────────────────────────────────────────────────────

PROMPT = "continue"                # 送出去的永遠是這一句
PERMISSION = "inherit"             # 權限模式永遠沿用那個對話原本的（resetter 不沿用「略過所有權限」）
SETUP = "/relay_setup"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
JOB_RE = re.compile(r"[0-9a-f]{8}")                       # resetter 的工作編號（secrets.token_hex(4)）
AT_MIN_RE = re.compile(r"\+([0-9]{1,4})")                 # +30：30 分鐘後
AT_HM_RE = re.compile(r"([0-9]{1,2}):([0-9]{2})")         # 06:05：今天，過了就明天
AT_FULL_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})")   # 2026-11-24 06:05
BUDGET_RE = re.compile(r"[0-9]{1,2}(\.[0-9]{1,2})?")
HOURS, USES, BUDGET = (1, 48), (1, 10), (0.1, 20.0)
DEFAULT_HOURS, DEFAULT_USES, DEFAULT_BUDGET = 12, 3, "2"
MAX_AT_MINUTES = 48 * 60
DAILY_LIMIT = 10                   # 24 小時內最多送出幾次（全部對話加起來）
FAIL_LIMIT = 2                     # 連續幾次失敗就自動凍結
KEEP_AUDIT, KEEP_JOBS, MAX_ARMED = 200, 50, 10
INTERACTIVE = ("claude-vscode", "cli")          # resetter 的 sessions --json 的 entrypoint：VSCode 擴充、終端機
ARMABLE_ORIGINS = ("project", "worktree")       # origin：專案、分身；app（程式用）、temp（暫存）、unknown 不收
SESSIONS_SCAN = 500                # 授權時在 resetter 的對話清單找多少個
SYNC_TIMEOUT = 90                  # 看清單、看結果、取消這種很快的呼叫
SEND_TIMEOUT = 240                 # 現在就送：resetter 會盯 claude 60 秒確認沒有馬上失敗
CHUNK = 3500
REFUNDABLE = (2, 3, 4)             # 只有這三個結束碼證明 claude 沒被叫起來（找不到、被佔用、參數不對）：才退次數
UNKNOWN_KEEP = 86400               # 結果不明的那一次，最多等多久去 resetter 的清單補記
EXIT_OK, EXIT_FAILED, EXIT_NOT_FOUND, EXIT_WAIT, EXIT_BAD = 0, 1, 2, 3, 4
FINAL = ("done", "failed", "cancelled")
STATUS_TEXT = {"scheduled": "⏰ 排好了", "waiting": "⏳ 對話正被開著，resetter 每 2 分鐘補試（最多 1 小時）",
               "sent": "▶ 送出了，正在跑", "done": "✅ 做完了", "failed": "❌ 失敗", "cancelled": "⏹ 取消了"}
KINDS = ("sessions", "show", "check", "continue", "jobs", "job", "cancel")


def now():                         # 唯一碰時鐘的地方（測試會換掉）
    return time.time()


# ── 純函式（不碰 SYNOX，測試直接叫） ─────────────────────────────────────

def check_session(value):
    """對話 id：只收小寫的 UUID（8-4-4-4-12）；其他一律拒，不修正、不去空白。"""
    if not isinstance(value, str) or not UUID_RE.fullmatch(value):
        raise BridgeProblem("🔁 對話要給完整的 id（小寫英數，像 0123abcd-0000-4000-8000-00000000abcd；/relay_sessions 看清單）。")
    return value


def check_job(value):
    if not isinstance(value, str) or not JOB_RE.fullmatch(value):
        raise BridgeProblem("🔁 工作編號是 8 個小寫英數字（/relay_jobs 看清單）。")
    return value


def parse_at(value, current):
    """at 的三種寫法 → 本機時間（到分鐘）。跟 resetter 的 parse_at 同一套意思，但只收這三種嚴格格式：
    +分鐘（1–2880）、HH:MM（今天，已經過了＝明天）、YYYY-MM-DD HH:MM（要在現在之後）。"""
    if not isinstance(value, str):
        raise BridgeProblem("🔁 看不懂時間。")
    base = current.replace(second=0, microsecond=0)
    m = AT_MIN_RE.fullmatch(value)
    if m:
        minutes = int(m.group(1))
        if not 1 <= minutes <= MAX_AT_MINUTES:
            raise BridgeProblem(f"🔁 +分鐘 要在 1 到 {MAX_AT_MINUTES} 之間。")
        return base + timedelta(minutes=minutes + (1 if current > base else 0))
    m = AT_HM_RE.fullmatch(value)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23 or minute > 59:
            raise BridgeProblem("🔁 時間要是 00:00 到 23:59。")
        at = base.replace(hour=hour, minute=minute)
        return at if at > current else at + timedelta(days=1)
    m = AT_FULL_RE.fullmatch(value)
    if m:
        try:
            at = datetime(*(int(x) for x in m.groups()))
        except ValueError:
            raise BridgeProblem("🔁 不是真的日期時間。") from None
        if at <= current:
            raise BridgeProblem("🔁 這個時間已經過了。")
        return at
    raise BridgeProblem("🔁 at 只收三種寫法：+30（分鐘後）、06:05（今天，過了就明天）、2026-11-24 06:05。")


def at_text(at):
    return at.strftime("%Y-%m-%d %H:%M")


def budget_text(value):
    """每筆花費上限（美元）→ 正規化的字串（0.1–20，最多兩位小數）。"""
    if not isinstance(value, str) or not BUDGET_RE.fullmatch(value):
        raise BridgeProblem(f"🔁 budget 是美元，{BUDGET[0]} 到 {BUDGET[1]:g}（最多兩位小數，例如 1.5）。")
    amount = float(value)
    if not BUDGET[0] <= amount <= BUDGET[1]:
        raise BridgeProblem(f"🔁 budget 要在 {BUDGET[0]} 到 {BUDGET[1]:g} 之間。")
    return f"{amount:g}"


def int_in(value, low, high, name):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,4}", value) or not low <= int(value) <= high:
        raise BridgeProblem(f"🔁 {name} 要是 {low} 到 {high} 的整數。")
    return int(value)


def one_line(value, limit, name):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise BridgeProblem(f"🔁 {name} 要是一行字（{limit} 字內）。")
    return value


def relay_argv(kind, *, session="", job="", at="", budget="", cwd="", limit=0, turns=0, everything=False):
    """**唯一**組 resetter 參數的地方：claude_timer.cli 後面接的那一段。只有 KINDS 這幾種；
    每個值都寫成 --旗標=值 一段，對話 id 與工作編號放在 -- 後面（不會被當成旗標）。
    continue 永遠是：resume --json --prompt=continue --permission-mode=inherit --max-budget-usd=<預算> [--at=…] -- <對話>。"""
    if kind == "sessions":
        tail = ["sessions", "--json"]
        if cwd:
            tail.append("--cwd=" + one_line(cwd, 400, "cwd"))
        if limit:
            tail.append(f"--limit={int_in(str(limit), 1, SESSIONS_SCAN, 'limit')}")
        if everything:
            tail.append("--all")
        return tail
    if kind == "show":
        tail = ["show", "--json"]
        if turns:
            tail.append(f"--turns={int_in(str(turns), 1, 20, 'turns')}")
        return tail + ["--", check_session(session)]
    if kind == "check":
        return ["check", "--json", "--", check_session(session)]
    if kind == "continue":
        if not isinstance(budget, str) or not BUDGET_RE.fullmatch(budget):
            raise BridgeProblem("🔁 內部錯誤：花費上限的格式不對。")
        tail = ["resume", "--json", "--prompt=" + PROMPT, "--permission-mode=" + PERMISSION, "--max-budget-usd=" + budget]
        if at:
            if not isinstance(at, str) or not AT_FULL_RE.fullmatch(at):
                raise BridgeProblem("🔁 內部錯誤：時間的格式不對。")
            tail.append("--at=" + at)
        return tail + ["--", check_session(session)]
    if kind == "jobs":
        return ["jobs", "--json", "--limit=200"]
    if kind == "job":
        return ["job", "--json", "--", check_job(job)]
    if kind == "cancel":
        return ["cancel", "--json", "--", check_job(job)]
    raise BridgeProblem(f"🔁 內部錯誤：不認得的動作 {kind!r}")


def when_text(ts):
    return time.strftime("%m/%d %H:%M", time.localtime(ts or 0))


def chunks(text, size=CHUNK):
    text = text or ""
    out = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        out.append(text)
    return out


def short(value, n):
    text = " ".join(str(value or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def blank():
    return {"settings": {}, "armed": {}, "frozen": False, "frozen_why": "", "frozen_at": 0, "fails": 0,
            "sent": [], "jobs": [], "audit": [], "unknown": []}


def fill(d):
    for key, value in blank().items():
        d.setdefault(key, value)
    return d


# ── 安裝 ──────────────────────────────────────────────────────────────

def install(app, pack):
    g = pack.group
    lock = threading.RLock()

    def load(user):
        return fill(pack.store.of(user).load({}) or {})

    def change(user, fn):
        with lock:
            return pack.store.of(user).update(lambda d: fn(fill(d)), {})

    def source():
        """這一次是誰叫的：human／schedule／ai／remote／system／unknown（SYNOX 的來源標籤，工具改不了）。"""
        c = kit.caller()
        return "human" if c.human else str(c.kind)

    def need_human(src):
        if src != "human":
            raise BridgeProblem("🔐 這個動作要你本人在 Telegram 或 SYNOX 網頁做。")

    def audit(d, act, src, sid, result):
        """稽核一筆：時間、動作、來源、對話前 8 碼、結果。不記驗證碼、不記對話內容；只留最近 KEEP_AUDIT 筆。"""
        d["audit"].append({"t": int(now()), "act": act, "src": src, "sid": (sid or "")[:8], "result": str(result)[:60]})
        del d["audit"][:-KEEP_AUDIT]

    def settings(user):
        return load(user)["settings"]

    def budget_of(user):
        return settings(user).get("budget") or DEFAULT_BUDGET

    def remove(path):
        try:
            os.remove(path)
        except OSError:
            pass

    def call(user, kind, timeout=SYNC_TIMEOUT, **kw):
        """叫 resetter 一次（等它結束）→ (結束碼, 它印的 JSON)。argv 只從 relay_argv 來。"""
        python, home = resolve_resetter(settings(user), SETUP)
        argv = module_argv(python, "cli", relay_argv(kind, **kw))
        log = pack.path_of(user, "calls", f"{kind}-{threading.get_ident()}-{time.monotonic_ns()}.log")
        try:
            job = app.jobs.run(f"claude-relay-{kind}", argv, cwd=home, timeout=timeout, env=clean_env(), log_path=log)
        except OSError as e:
            remove(log)
            raise CallProblem(f"🔁 叫不起 ClaudeTimerResetter：{type(e).__name__}（{SETUP} 看設定）", "call_error") from None
        text = read_log(job.log_path)
        remove(job.log_path)                      # 明文的輸出不留
        if job.state == TIMED_OUT:
            raise CallProblem(f"🔁 ClaudeTimerResetter 超過 {timeout} 秒沒有回應，先停掉了。", "timeout")
        data = last_json(text)
        if data is None:
            tail = short("\n".join(text.strip().splitlines()[-3:]), 300)
            raise CallProblem("🔁 ClaudeTimerResetter 沒有回傳看得懂的結果（版本太舊？）。最後幾行：" + tail, "unparseable")
        return job.returncode, data

    # ── 工作狀態、失敗計數、凍結 ──

    def ours(d, job_id):
        return next((r for r in d["jobs"] if r["id"] == job_id), None)

    def trip(d, src, why):
        """凍結（已經凍結就不動）。回 True＝這次才凍結的。"""
        if d["frozen"]:
            return False
        d.update(frozen=True, frozen_why=why, frozen_at=int(now()))
        audit(d, "freeze", src, "", why)
        return True

    def note_failure(d, sid, kind):
        """kind 只是分類（failed:exit1、timeout、unparseable、job_failed…），不是錯誤原文。"""
        d["fails"] += 1
        if d["fails"] >= FAIL_LIMIT:
            return trip(d, "system", f"連續 {FAIL_LIMIT} 次失敗，自動凍結（最後一次：{kind}）")
        return False

    def cancel_pending(user, src, sids=None, ids=None):
        """取消這個工具排的、還沒跑完的工作（sids 給了就只取消那幾個對話的）。回 (取消了的, 取消不了的說明)。"""
        pending = [r for r in load(user)["jobs"] if r["status"] not in FINAL and (sids is None or r["sid"] in sids)
                   and (ids is None or r["id"] in ids)]
        done, problems = [], []
        for rec in pending:
            try:
                code, _data = call(user, "cancel", job=rec["id"])
            except BridgeProblem as p:
                problems.append(p.text)
                break
            if code == EXIT_OK:
                done.append(rec["id"])
            else:
                problems.append(f"工作 {rec['id']} 取消不了（結束碼 {code}）")

        def mark(d):
            for job_id in done:
                r = ours(d, job_id)
                if r is not None:
                    r["status"] = "cancelled"
                    r["counted"] = True
                audit(d, "cancel", src, r["sid"] if r else "", "ok")
        if done:
            change(user, mark)
        return done, problems

    def created_ts(row):
        try:
            return datetime.fromisoformat(str(row.get("created") or "")).timestamp()
        except ValueError:
            return None

    def adopt_unknown(d, rows):
        """逾時或看不懂輸出的那幾次：resetter 可能已經建了工作。照「同一個對話、resume、continue、那段時間建的」
        從它的清單找出來補記（之後照常追狀態、可以取消、凍結時一起收）；找不到的等到 UNKNOWN_KEEP 就放棄。"""
        mine = {r["id"] for r in d["jobs"]}
        stamp = now()
        keep = []
        for marker in d["unknown"]:
            found = [r for jid, r in rows.items()
                     if jid not in mine and JOB_RE.fullmatch(jid) and r.get("session_id") == marker["sid"]
                     and r.get("kind") == "resume" and r.get("prompt") == PROMPT
                     and created_ts(r) is not None and marker["since"] - 120 <= created_ts(r) <= marker["since"] + SEND_TIMEOUT + 120]
            for row in found:
                status = str(row.get("status") or "")
                d["jobs"].append({"id": str(row["id"]), "sid": marker["sid"], "at": short(row.get("at"), 16).replace("T", " "),
                                  "created": int(marker["since"]), "src": marker.get("src", "unknown"),
                                  "status": status if status in STATUS_TEXT else "sent", "cost": 0.0, "error": "",
                                  "counted": False, "auth": marker.get("auth", "")})
                mine.add(str(row["id"]))
                audit(d, "adopt", "system", marker["sid"], "ok:" + str(row["id"]))
            if not found and marker["since"] > stamp - UNKNOWN_KEEP:
                keep.append(marker)
            elif not found:
                audit(d, "adopt", "system", marker["sid"], "gave_up")
        d["unknown"] = keep
        del d["jobs"][:-KEEP_JOBS]

    def refresh(user):
        """用 resetter 的 jobs --json 更新這個工具自己送出的工作；失敗的算進連續失敗。回要告訴使用者的話（可能是空的）。"""
        current = load(user)
        if not [r for r in current["jobs"] if r["status"] not in FINAL] and not current["unknown"]:
            return ""
        _code, data = call(user, "jobs")
        rows = {str(r.get("id")): r for r in data.get("jobs") or [] if isinstance(r, dict)}

        def update(d):
            tripped = False
            adopt_unknown(d, rows)
            for rec in d["jobs"]:
                row = rows.get(rec["id"])
                if row is None or rec["status"] in FINAL:
                    continue
                status = str(row.get("status") or rec["status"])
                rec["status"] = status if status in STATUS_TEXT else rec["status"]
                try:
                    rec["cost"] = round(float(row.get("cost_usd") or 0.0), 4)
                except (TypeError, ValueError):
                    pass
                rec["error"] = short(row.get("error"), 200)
                if rec["status"] in FINAL and not rec.get("counted"):
                    rec["counted"] = True
                    if rec["status"] == "failed":
                        audit(d, "job_failed", "system", rec["sid"], "failed")
                        tripped = note_failure(d, rec["sid"], "job_failed") or tripped
                    elif rec["status"] == "done":
                        d["fails"] = 0
            return tripped
        if change(user, update):
            cancel_pending(user, "system")
            return f"⛔ 連續 {FAIL_LIMIT} 次失敗，已經自動凍結、取消還沒跑完的工作。看 /relay_jobs，確定沒問題再 /relay_thaw。"
        return ""

    def stale(d, rec, stamp):
        """還沒送（scheduled／waiting）、但授權已經不在、過期、或換成另一筆授權的工作。"""
        if rec["status"] not in ("scheduled", "waiting"):
            return False
        entry = d["armed"].get(rec["sid"])
        return entry is None or entry["until"] <= stamp or bool(rec.get("auth")) and entry.get("auth") != rec.get("auth")

    def sweep(user):
        """每個指令進來先做一次：授權過期或撤回、工作卻還沒送的，問過 resetter 的最新狀態後取消。
        （resetter 的一次性工作錯過時間會在下次開機補跑、fire 不看遲到；這是 relay 這邊唯一收得回來的機會。）"""
        if not any(stale(load(user), r, now()) for r in load(user)["jobs"]):
            return ""
        try:
            notice = refresh(user)
            d, stamp = load(user), now()
            ids = {r["id"] for r in d["jobs"] if stale(d, r, stamp)}
            done, problems = cancel_pending(user, "system", ids=ids) if ids else ([], [])
        except BridgeProblem as p:
            return "⚠ 有授權已過期、還沒送的工作，但現在問不到 ClaudeTimerResetter：" + p.text
        lines = [notice] if notice else []
        if done:
            lines.append(f"🧹 授權已過期或撤回：取消了 {len(done)} 筆還沒送的工作（{'、'.join(done)}）。")
        if problems:
            lines.append("⚠ 有些取消不了：" + "；".join(problems))
        return "\n".join(lines)

    # ── 核心 ──

    def core_continue(user, sid, at_raw, src):
        if src not in ("human", "schedule"):        # pack.json 的 callers 已經擋了；這裡多一層
            raise BridgeProblem("🔐 continue 只收你本人或排程。")
        sid = check_session(sid)
        at = parse_at(at_raw, datetime.fromtimestamp(now())) if at_raw else None
        resolve_resetter(settings(user), SETUP)     # 沒設定好就先講，不算失敗、不扣次數
        notice = refresh(user)
        stamp = now()

        def reserve(d):
            entry = d["armed"].get(sid)
            recent = [t for t in d["sent"] if t > stamp - 86400]
            reason = None
            if d["frozen"]:
                reason = ("frozen", f"⛔ 現在是凍結的（{d['frozen_why']}），不送。確定沒問題再 /relay_thaw。")
            elif entry is None:
                reason = ("not_armed", f"🔁 這個對話沒有授權。要讓它可以自動接著做，請你本人打：/relay_arm {sid}")
            elif entry["until"] <= stamp:
                reason = ("expired", f"🔁 這個對話的授權已經過期（{when_text(entry['until'])}）。要的話重新 /relay_arm。")
            elif entry["uses"] <= 0:
                reason = ("used_up", "🔁 這個對話授權的次數用完了。要的話重新 /relay_arm。")
            elif len(recent) >= DAILY_LIMIT:
                reason = ("daily_limit", f"🔁 24 小時內已經送了 {DAILY_LIMIT} 次（上限），先不送。")
            elif at is not None and at.timestamp() > entry["until"]:
                reason = ("at_after_expiry", f"🔁 {at_text(at)} 超過授權期限（{when_text(entry['until'])}）。")
            if reason:
                audit(d, "continue", src, sid, "refused:" + reason[0])
                return None, reason[1]
            entry["uses"] -= 1
            d["sent"] = recent + [stamp]
            return dict(entry), ""
        entry, refusal = change(user, reserve)
        if entry is None:
            raise BridgeProblem("\n".join(x for x in (notice, refusal) if x))

        def settle(result, refund=False, keep_stamp=True, record=None, failure=None, unknown=False):
            def fn(d):
                same = d["armed"].get(sid)
                if refund and same is not None and same.get("auth") == entry.get("auth"):   # 退到同一筆授權，不是後來重新授權的
                    same["uses"] += 1
                if unknown:
                    d["unknown"].append({"sid": sid, "since": stamp, "src": src, "auth": entry.get("auth", "")})
                if not keep_stamp and stamp in d["sent"]:
                    d["sent"].remove(stamp)
                if record:
                    d["jobs"].append(record)
                    del d["jobs"][:-KEEP_JOBS]
                audit(d, "continue", src, sid, result)
                return note_failure(d, sid, failure) if failure else False
            return change(user, fn)

        try:
            code, data = call(user, "continue", timeout=SEND_TIMEOUT, session=sid, budget=budget_of(user),
                              at=at_text(at) if at else "")
        except BridgeProblem as p:
            kind = getattr(p, "kind", "call_error")         # 逾時、看不懂、叫不起：不知道 claude 有沒有被叫起來，不退次數
            tripped = settle("unknown:" + kind, failure=kind, unknown=kind != "call_error")
            return "\n".join(x for x in (notice, p.text, "（結果不明：這次的次數不退；resetter 那邊要是建了工作，"
                                                    "下次 /relay_jobs 會補記進來。）", freeze_line(user, tripped)) if x)
        job_id = str(data.get("id") or "")
        job_id = job_id if JOB_RE.fullmatch(job_id) else ""
        status = str(data.get("status") or "")
        record = {"id": job_id, "sid": sid, "at": at_text(at) if at else "", "created": int(stamp), "src": src,
                  "status": status if status in STATUS_TEXT else "sent", "cost": 0.0, "error": "", "counted": False,
                  "auth": entry.get("auth", "")}
        if code == EXIT_OK and job_id:
            def keep_or_revoke(d):
                """記下這一筆，同時再看一次：送出去的那幾秒裡被凍結、撤回或換了授權，就當場取消、不扣次數。"""
                d["jobs"].append(record)
                del d["jobs"][:-KEEP_JOBS]
                same = d["armed"].get(sid)
                still = same is not None and same.get("auth") == entry.get("auth") and same["until"] > now()
                if still and not d["frozen"]:
                    audit(d, "continue", src, sid, "ok:" + job_id)
                    return ""
                if still:
                    same["uses"] += 1
                if stamp in d["sent"]:
                    d["sent"].remove(stamp)
                why = "frozen" if d["frozen"] else "disarmed"
                audit(d, "continue", src, sid, f"revoked:{why}")
                return why
            revoked = change(user, keep_or_revoke)
            if revoked:
                done, problems = cancel_pending(user, "system", {sid})
                what = "凍結了" if revoked == "frozen" else "撤回（或換了）授權"
                line = (f"⛔ 送出的同時被{what}：剛建立的工作 {job_id} "
                        + ("已經取消" if job_id in done else "取消不了（" + "；".join(problems or ["可能已經送出"]) + "）")
                        + "，沒有扣次數。")
                return "\n".join(x for x in (notice, line) if x)
            left = f"這個對話還剩 {entry['uses']} 次、授權到 {when_text(entry['until'])}。"
            if at:
                head = (f"⏰ 排好了：{at_text(at)} 由 ClaudeTimerResetter 送 continue（SYNOX 關著也會送；"
                        f"到時對話若還開著，它每 2 分鐘補試、最多 1 小時，不搶）。工作 {job_id}。")
            else:
                head = f"▶ 送出 continue 了（工作 {job_id}）。做完了沒：/relay_job {job_id}"
            return "\n".join(x for x in (notice, head, left) if x)
        if code == EXIT_WAIT:
            settle("busy", refund=True, keep_stamp=False)
            holder = short(data.get("error"), 160)
            if job_id:                                # 現在送、被佔用：resetter 不會自己補試，留著的那筆收掉
                try:
                    call(user, "cancel", job=job_id)
                except BridgeProblem:
                    pass
            return "\n".join(x for x in (notice,
                                         f"⏳ 對話正被開著（{holder or '被別的 Claude Code 佔用'}），現在送不出去；沒有扣次數。\n"
                                         "關掉那個對話之後再叫一次，或改用 at=+5 交給 resetter 的排程器"
                                         "（到點還被開著，它每 2 分鐘自動補試、最多 1 小時，不搶）。") if x)
        why = short(data.get("error"), 200) or f"結束碼 {code}"
        if job_id:
            record.update(status="failed", error=why, counted=True)
        kind = f"failed:exit{code}" if job_id or code != EXIT_OK else "failed:noid"
        refund = code in REFUNDABLE                 # 結束碼 1（claude 可能起來過又失敗）不退
        tripped = settle(kind, refund=refund, keep_stamp=not refund, record=record if job_id else None, failure=kind,
                         unknown=kind == "failed:noid")
        used = "沒有扣次數" if refund else "這次的次數不退（claude 可能已經被叫起來過）"
        return "\n".join(x for x in (notice, f"❌ 沒送成：{why}（{used}）", freeze_line(user, tripped)) if x)

    def freeze_line(user, tripped):
        if not tripped:
            return ""
        done, problems = cancel_pending(user, "system")
        line = f"⛔ 連續 {FAIL_LIMIT} 次失敗，已經自動凍結" + (f"、取消了 {len(done)} 筆還沒跑完的工作" if done else "")
        line += "。確定沒問題再 /relay_thaw。"
        return line + ("\n" + "；".join(problems) if problems else "")

    def core_arm(user, sid, hours, uses, src):
        need_human(src)
        sid = check_session(sid)
        _code, data = call(user, "sessions", limit=SESSIONS_SCAN, everything=True)
        row = next((r for r in data.get("sessions") or [] if isinstance(r, dict) and r.get("session_id") == sid), None)
        if row is None:
            change(user, lambda d: audit(d, "arm", src, sid, "refused:not_found"))
            raise BridgeProblem(f"🔁 在這台電腦最近 {SESSIONS_SCAN} 個對話裡找不到 {sid}（/relay_sessions 看清單）。")
        origin, entry_point = str(row.get("origin") or ""), str(row.get("entrypoint") or "")
        if origin not in ARMABLE_ORIGINS or entry_point not in INTERACTIVE:
            why = ("這是程式用 claude -p 開的對話（或來源看不出來）" if entry_point not in INTERACTIVE
                   else "這個對話在暫存或程式自己的資料夾裡")
            change(user, lambda d: audit(d, "arm", src, sid, f"refused:origin:{short(origin, 12)}/{short(entry_point, 16)}"))
            raise BridgeProblem(f"🔁 不授權：{why}。只授權你自己在 VSCode 或終端機開、放在專案資料夾的對話——"
                                "程式開的有自己的主人在管，而且沒有「你互動時的權限」可以沿用。")
        code, checked = call(user, "check", session=sid)
        if code not in (EXIT_OK, EXIT_WAIT):
            problems = checked.get("problems") or [checked.get("error") or f"結束碼 {code}"]
            change(user, lambda d: audit(d, "arm", src, sid, f"refused:check:exit{code}"))
            raise BridgeProblem("🔁 不授權：resetter 檢查這個對話送不出去——" + "；".join(short(p, 160) for p in problems[:3]))
        stamp = now()
        entry = {"cwd": short(row.get("cwd"), 300), "label": short(row.get("label") or row.get("project"), 60),
                 "branch": short(row.get("branch"), 60), "until": int(stamp + hours * 3600), "uses": uses,
                 "armed_at": int(stamp), "auth": f"{int(stamp)}.{time.monotonic_ns() % 1000000:06d}"}

        def put(d):
            for old in [k for k, v in d["armed"].items() if v["until"] <= stamp]:
                del d["armed"][old]
            if sid not in d["armed"] and len(d["armed"]) >= MAX_ARMED:
                audit(d, "arm", src, sid, "refused:too_many")
                return False
            if sid in d["armed"]:                    # 還有效的授權重新設定＝延長／改次數：同一筆授權，排好的工作照舊
                entry["auth"] = d["armed"][sid].get("auth") or entry["auth"]
            d["armed"][sid] = entry
            audit(d, "arm", src, sid, f"ok:{hours}h/{uses}")
            return True
        if not change(user, put):
            raise BridgeProblem(f"🔁 最多同時授權 {MAX_ARMED} 個對話；先 /relay_disarm 不用的。")
        frozen = load(user)["frozen"]
        return "\n".join([
            f"✅ 授權了：{entry['label'] or sid[:8]}（{entry['branch'] or '沒有分支'}）",
            f"  資料夾：{entry['cwd']}",
            f"  到 {when_text(entry['until'])} 為止、最多 {uses} 次，每次只送 continue、權限模式 inherit（見下）。",
            f"  現在送：/relay_continue {sid}",
            f"  排時間：/schedule_add 06:05 /relay_continue {sid}　或　/relay_continue {sid} at=06:05",
            "⚠ continue 會讓它帶著原本的工具權限接著做停住前在做的事；背景沒有人能按核准，要核准的動作會停住。",
            "⚠ 權限是 resetter 從對話紀錄讀「你最後一次在 VSCode／終端機互動時」的模式：讀不到就退回「自動同意改檔」模式，"
            "原本是「略過所有權限」的會降成 auto（分類器擋危險的）。這裡看不出它會讀到哪一個，不確定就先 /relay_show 看一下。",
        ] + (["⏳ 這個對話現在正被開著：送的時候要先關掉它。"] if code == EXIT_WAIT else [])
          + (["⛔ 現在是凍結的：要先 /relay_thaw 才會真的送。"] if frozen else []))

    def core_disarm(user, target, src):
        need_human(src)
        target = (target or "").strip()
        data = load(user)
        if target == "all":
            sids = list(data["armed"])
        else:
            sids = [check_session(target)]
        pending = [r for r in data["jobs"] if r["status"] not in FINAL and (target == "all" or r["sid"] in sids)]
        if not [x for x in sids if x in data["armed"]] and not pending:
            return "🔁 現在沒有授權任何對話。" if target == "all" else "🔁 這個對話本來就沒有授權，也沒有還沒送的工作。"
        done, problems = [], []
        if pending:                                  # 授權已經過期被清掉的對話，排好的工作也照樣收
            done, problems = cancel_pending(user, src, None if target == "all" else set(sids))
        sids = [x for x in sids if x in data["armed"]]

        def drop(d):
            for sid in sids:
                d["armed"].pop(sid, None)
                audit(d, "disarm", src, sid, "ok")
        change(user, drop)
        lines = [f"✅ 取消了 {len(sids)} 個對話的授權。" if sids else "🔁 這個對話的授權本來就不在了。"]
        if done:
            lines.append(f"  連同還沒送的 {len(done)} 筆工作一起取消了。")
        if problems:
            lines.append("  ⚠ " + "；".join(problems))
        return "\n".join(lines)

    def core_armed(user):
        d = load(user)
        stamp = now()
        lines = ["🔁 對話接力" + ("　⛔ 凍結中：" + d["frozen_why"] if d["frozen"] else "　✅ 沒有凍結")]
        sent = len([t for t in d["sent"] if t > stamp - 86400])
        lines.append(f"24 小時內送了 {sent}/{DAILY_LIMIT} 次；每筆花費上限 {d['settings'].get('budget') or DEFAULT_BUDGET} 美元；"
                     f"連續失敗 {d['fails']}/{FAIL_LIMIT}")
        if not d["armed"]:
            lines.append("還沒有授權任何對話（你本人用 /relay_arm <對話> 授權）。")
        for sid, e in sorted(d["armed"].items(), key=lambda kv: kv[1]["until"]):
            state = "已過期" if e["until"] <= stamp else f"剩 {e['uses']} 次、到 {when_text(e['until'])}"
            lines.append(f"• {sid}  {e.get('label') or ''}（{e.get('branch') or '-'}）：{state}")
        if d["audit"]:
            lines.append("最近的紀錄：")
            for a in d["audit"][-10:][::-1]:
                lines.append(f"  {when_text(a['t'])} {a['act']}（{a['src']}）{a['sid'] or ''} → {a['result']}")
        return "\n".join(lines)

    def core_jobs(user):
        notice = refresh(user)
        d = load(user)
        if not d["jobs"]:
            return "\n".join(x for x in (notice, "🔁 這個工具還沒送出過工作。") if x)
        lines = [x for x in (notice, "🔁 這個工具送出過的工作（新的在上面）：") if x]
        for r in d["jobs"][-15:][::-1]:
            when = f"排在 {r['at']}" if r.get("at") else f"{when_text(r['created'])} 送"
            cost = f"、${r['cost']:.2f}" if r.get("cost") else ""
            lines.append(f"• {r['id']}  對話 {r['sid'][:8]}  {when}（{r['src']}）：{STATUS_TEXT.get(r['status'], r['status'])}{cost}")
            if r.get("error"):
                lines.append(f"    {r['error']}")
        return "\n".join(lines)

    def core_job(user, job_id, src):
        job_id = check_job((job_id or "").strip())
        if ours(load(user), job_id) is None:
            raise BridgeProblem("🔁 這不是這個工具送出的工作（/relay_jobs 看清單）。")
        notice = refresh(user)
        code, data = call(user, "job", job=job_id)
        if code == EXIT_NOT_FOUND:
            raise BridgeProblem("🔁 ClaudeTimerResetter 那邊已經沒有這筆了。")
        status = str(data.get("status") or "")
        lines = [x for x in (notice, f"🔁 工作 {job_id}：{STATUS_TEXT.get(status, status or '不明')}") if x]
        if data.get("session_id"):
            lines.append(f"  對話：{short(data.get('session_id'), 40)}")
        if data.get("at"):
            lines.append(f"  排在：{short(data.get('at'), 30)}")
        try:
            cost = float(data.get("cost_usd") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        if cost:
            lines.append(f"  花費：${cost:.2f}")
        if data.get("error"):
            lines.append(f"  說明：{short(data.get('error'), 300)}")
        if data.get("result"):
            lines.append(f"  它最後說：{short(data.get('result'), 400)}" if src == "human" else "  它最後說：（只給使用者本人看）")
        if data.get("changes"):
            lines.append(f"  改動：{short(data.get('changes'), 200)}")
        return "\n".join(lines)

    def core_cancel(user, job_id, src):
        need_human(src)
        job_id = check_job((job_id or "").strip())
        rec = ours(load(user), job_id)
        if rec is None:
            raise BridgeProblem("🔁 這不是這個工具送出的工作（/relay_jobs 看清單）。")
        if rec["status"] in FINAL:
            raise BridgeProblem(f"🔁 工作 {job_id} 已經結束了（{STATUS_TEXT.get(rec['status'], rec['status'])}）。")
        code, data = call(user, "cancel", job=job_id)
        if code != EXIT_OK or job_id not in (data.get("cancelled") or []):
            change(user, lambda d: audit(d, "cancel", src, rec["sid"], f"failed:exit{code}"))
            raise BridgeProblem(f"🔁 取消不了工作 {job_id}（可能已經結束了；/relay_job {job_id} 看一下）。")

        def mark(d):
            r = ours(d, job_id)
            if r is not None:
                r.update(status="cancelled", counted=True)
            audit(d, "cancel", src, rec["sid"], "ok")
        change(user, mark)
        return f"⏹ 取消了工作 {job_id}（次數不退）。"

    def core_freeze(user, src):
        newly = change(user, lambda d: trip(d, src, f"手動凍結（{src}）"))
        try:
            done, problems = cancel_pending(user, src)
        except BridgeProblem as p:
            done, problems = [], [p.text]
        lines = ["⛔ 凍結了：之後所有 continue 一律拒絕。" if newly else "⛔ 本來就是凍結的。"]
        if done:
            lines.append(f"  取消了 {len(done)} 筆還沒跑完的工作：{'、'.join(done)}")
        if problems:
            lines.append("  ⚠ 有些取消不了：" + "；".join(problems))
        lines.append("  解除要你本人＋手機驗證器：/relay_thaw")
        return "\n".join(lines)

    def core_thaw(user, src):
        need_human(src)

        def fn(d):
            was = d["frozen"]
            d.update(frozen=False, frozen_why="", frozen_at=0, fails=0)
            audit(d, "thaw", src, "", "ok" if was else "noop")
            return was
        return "✅ 解除凍結了，連續失敗歸零。" if change(user, fn) else "🔁 本來就沒有凍結。"

    def core_setup(user, values, src):
        need_human(src)
        updates = {}
        for key in ("home", "python"):
            if key in values:
                value = values[key].strip().strip('"')
                if value in ("-", "清除", "none"):
                    value = ""
                if value:
                    one_line(value, 400, key)
                    if key == "home" and not os.path.isdir(value):
                        raise BridgeProblem(f"🔁 找不到這個資料夾：{value}")
                    if key == "python" and not os.path.isfile(value):
                        raise BridgeProblem(f"🔁 找不到這個 Python：{value}")
                updates[key] = value
        if "budget" in values:
            updates["budget"] = budget_text(values["budget"].strip())
        if updates:
            def fn(d):
                d["settings"].update(updates)
                audit(d, "setup", src, "", "ok:" + ",".join(sorted(updates)))     # 只記改了哪幾個欄位
            change(user, fn)
        s = settings(user)
        lines = ["🔁 對話接力的設定" + ("（改好了）" if updates else "") + "：",
                 f"  home：{s.get('home') or '（還沒設定）'}",
                 f"  python：{s.get('python') or '（自動：用它旁邊的 .venv）'}",
                 f"  budget：每筆最多 {s.get('budget') or DEFAULT_BUDGET} 美元"]
        try:
            resolve_resetter(s, SETUP)
            lines.append("  ✅ 找得到 ClaudeTimerResetter。用 /relay_check 試一次。")
        except BridgeProblem as p:
            lines.append(p.text)
        return "\n".join(lines)

    def core_check(user, sid):
        s = settings(user)
        python, home = resolve_resetter(s, SETUP)
        d = load(user)
        lines = [f"🔁 ClaudeTimerResetter：{home}", f"  Python：{python}",
                 "  ⛔ 凍結中" if d["frozen"] else "  ✅ 沒有凍結"]
        if not sid:
            code, data = call(user, "sessions", limit=1)
            lines.append("  ✅ 叫得動（看得到對話清單）" if code == EXIT_OK else f"  ❌ 叫不動：{short(data.get('error'), 200)}")
            return "\n".join(lines)
        sid = check_session(sid)
        code, data = call(user, "check", session=sid)
        entry = d["armed"].get(sid)
        lines.append(f"  授權：剩 {entry['uses']} 次、到 {when_text(entry['until'])}" if entry and entry["until"] > now()
                     else "  授權：沒有（或過期了）")
        if code == EXIT_OK:
            lines.append("  ✅ 現在可以送")
        elif code == EXIT_WAIT:
            lines.append(f"  ⏳ 對話正被開著：{short(data.get('holder') or data.get('error'), 200)}")
        elif code == EXIT_NOT_FOUND:
            lines.append("  ❌ 找不到這個對話")
        else:
            problems = data.get("problems") or [data.get("error") or f"結束碼 {code}"]
            lines.extend(f"  ❌ {short(p, 200)}" for p in problems[:5])
        return "\n".join(lines)

    def core_sessions(user, values, src):
        cwd = (values.get("cwd") or "").strip().strip('"')
        limit = int_in(values["limit"].strip(), 1, 200, "limit") if values.get("limit") else 30
        _code, data = call(user, "sessions", cwd=cwd, limit=limit)
        rows = [r for r in data.get("sessions") or [] if isinstance(r, dict)]
        armed = load(user)["armed"]
        stamp = now()
        if not rows:
            return "🔁 沒有找到對話。"
        lines = [f"🔁 這台電腦上的對話（{len(rows)} 個；✅＝授權過）："]
        for r in rows:
            sid = str(r.get("session_id") or "")
            mark = "✅" if sid in armed and armed[sid]["until"] > stamp else "·"
            busy = " ● 開著" if r.get("busy") else ""
            lines.append(f"{mark} {sid}  {short(r.get('relative'), 12)}  {short(r.get('label'), 24)}"
                         f"（{short(r.get('branch'), 20) or '-'}）{busy}")
            last = f"｜{short(r.get('last_prompt'), 40)}" if src == "human" else ""     # 你最後說的話只給你本人
            lines.append(f"    {short(r.get('title'), 40)}{last}")
        return "\n".join(lines)

    def core_show(user, sid, turns, src):
        """只看 resetter 一般清單（不含暫存、程式開的）列得到的對話。對話內容（最後一句、它回的、最近幾輪）只給你本人；
        AI 叫的只拿到標題、資料夾、時間、輪數。"""
        sid = check_session(sid)
        turns = int_in(turns, 1, 20, "turns") if turns else 0
        _code, listed = call(user, "sessions", limit=SESSIONS_SCAN)
        if not [r for r in listed.get("sessions") or [] if isinstance(r, dict) and r.get("session_id") == sid]:
            raise BridgeProblem("🔁 這個對話不在一般的對話清單裡（暫存、程式開的、或太舊），不顯示。/relay_sessions 看清單。")
        person = src == "human"
        code, data = call(user, "show", session=sid, turns=turns if person else 0)
        if code != EXIT_OK:
            raise BridgeProblem(f"🔁 看不到這個對話：{short(data.get('error'), 200)}")
        lines = [f"🔁 {short(data.get('title'), 80)}",
                 f"  資料夾：{short(data.get('cwd'), 200)}",
                 f"  {short(data.get('started'), 20)} → {short(data.get('ended'), 20)}，{data.get('turns', '?')} 輪"]
        if not person:
            lines.append("  （對話內容只給使用者本人看：請他自己打 /relay_show。）")
            return "\n".join(lines)
        lines += [f"  最後你說：{short(data.get('last_human'), 300)}",
                  f"  它回：{short(data.get('last_assistant'), 400)}"]
        for t in data.get("last_turns") or []:
            if isinstance(t, dict):
                lines.append(f"── 第 {t.get('n')} 輪 {short(t.get('when'), 20)}")
                lines.append(f"  你：{short(t.get('prompt'), 300)}")
                lines.append(f"  它：{short(t.get('reply'), 500)}")
        return "\n".join(lines)

    # ── 指令 ──

    def say(api, chat_id, text):
        for part in chunks(text):
            api.send_message(chat_id, part)

    def command(fn, background=True):
        """fn(user, values, src) → 回覆的字。來源（src）在這裡、還在原本的執行緒時就讀好（背景執行緒讀不到）。
        會叫 resetter 的：經 AI 的同步（結果要交回 AI）；人打的、排程的丟背景，不卡住收訊。"""
        def run(api, chat_id, user, values):
            src = source()

            def work():
                try:
                    swept = sweep(user)
                    if swept:
                        say(api, chat_id, swept)
                    say(api, chat_id, fn(user, values, src))
                except BridgeProblem as p:
                    api.send_message(chat_id, p.text)
            if not background or getattr(api, "via_ai", False) is True or getattr(api, "sync_result", False) is True:
                work()
            else:
                app.tasks.run(work, name=f"pack-{pack.name}")
        return run

    session_field = Field("session", "對話 id（/relay_sessions 看）", required=True)
    kit.command(g, "sessions", command(lambda u, v, s: core_sessions(u, v, s)),
                "列出這台電腦上的 Claude Code 對話（標出授權過的）",
                fields=[Field("cwd", "只列這個資料夾的"), Field("limit", "最多幾個（預設 30）")], positional=0)
    kit.command(g, "show", command(lambda u, v, s: core_show(u, v["session"], (v.get("turns") or "").strip(), s)),
                "看一個對話做到哪裡（期間、最後一句、最近幾輪）",
                fields=[session_field, Field("turns", "多看最近幾輪（1–20）")], positional=1)
    kit.command(g, "check", command(lambda u, v, s: core_check(u, (v.get("session") or "").strip())),
                "檢查 ClaudeTimerResetter 叫不叫得動；給了對話就看它現在能不能送（不花錢）",
                fields=[Field("session", "對話 id（不給＝只檢查環境）")], positional=1)
    kit.command(g, "arm", command(lambda u, v, s: core_arm(u, v["session"],
                                                           int_in((v.get("hours") or str(DEFAULT_HOURS)).strip(), *HOURS, "hours"),
                                                           int_in((v.get("uses") or str(DEFAULT_USES)).strip(), *USES, "uses"), s)),
                "授權一個對話可以被自動送 continue（有期限與次數；只有你本人，要手機驗證器）",
                effect=DANGER, ai=False, positional=1,
                fields=[session_field, Field("hours", "授權幾小時（1–48，預設 12）"), Field("uses", "最多幾次（1–10，預設 3）")])
    kit.command(g, "disarm", command(lambda u, v, s: core_disarm(u, v["session"], s)),
                "取消授權（all＝全部）；這個工具排了、還沒送的工作一起取消", effect=WRITE, ai=False, positional=1,
                fields=[Field("session", "對話 id 或 all", required=True)])
    kit.command(g, "armed", command(lambda u, v, s: core_armed(u), background=False),
                "看授權名單、剩幾次、幾點到期、是否凍結，與最近的稽核紀錄")
    kit.command(g, "continue", command(lambda u, v, s: core_continue(u, v["session"], (v.get("at") or "").strip(), s)),
                "對已授權的對話送固定的 continue（at＝交給 resetter 的排程器到點送）；只有你本人或排程",
                effect=DANGER, ai=False, positional=1,
                fields=[session_field, Field("at", "什麼時候送：+30、06:05 或 2026-11-24 06:05（不給＝現在）")])
    kit.command(g, "jobs", command(lambda u, v, s: core_jobs(u)), "看這個工具送出過的工作與狀態")
    kit.command(g, "job", command(lambda u, v, s: core_job(u, v["job"], s)), "看一筆工作的結果（它最後說什麼、花多少）",
                fields=[Field("job", "工作編號（/relay_jobs 看）", required=True)], positional=1)
    kit.command(g, "cancel", command(lambda u, v, s: core_cancel(u, v["job"], s)), "取消這個工具排的、還沒送的工作",
                effect=WRITE, ai=False, positional=1, fields=[Field("job", "工作編號（/relay_jobs 看）", required=True)])
    kit.command(g, "freeze", command(lambda u, v, s: core_freeze(u, s)),
                "緊急停止：之後所有 continue 一律拒絕，並取消這個工具排的、還沒跑完的工作", effect=WRITE)
    kit.command(g, "thaw", command(lambda u, v, s: core_thaw(u, s), background=False),
                "解除緊急停止（只有你本人，要手機驗證器）", effect=WRITE, ai=False)
    kit.command(g, "setup", command(lambda u, v, s: core_setup(u, v, s), background=False),
                "設定 ClaudeTimerResetter 在哪裡、每筆花費上限（只有你本人，要手機驗證器）", effect=WRITE, ai=False,
                fields=[Field("home", "ClaudeTimerResetter 的資料夾"), Field("python", "要用哪個 Python（留空＝它旁邊的 .venv）"),
                        Field("budget", "每筆最多花幾美元（0.1–20，預設 2）")], positional=0)
