"""⏱ Claude 重置計時：把這台電腦上**另外裝好的** ClaudeTimerResetter 接進 SYNOX（一座薄薄的橋）。

    /resetter_usage                       額度用了多少、幾點重置（不花錢、唯讀）
    /resetter_refresh [wait=是]           該刷新就刷新、不該就不打（有打約 $0.00025）
    /resetter_refresh force=是            不管狀態強制打一次（只有你本人）
    /resetter_check                       resetter 找得到嗎、能不能讀到額度
    /resetter_setup [home=…] [python=…]   告訴我 ClaudeTimerResetter 的資料夾（只有你本人）

為什麼要有它：Claude 的 5 小時額度視窗，倒數是「第一次呼叫」那一刻才開始跑的。在你開工之前先打一次最便宜的探針，
倒數就提早開始，之後額度見底時下一輪重置剛好接上。要定時做就用 SYNOX 內建的 `/schedule_add 08:40 /resetter_refresh`，
這個工具自己不排程。

怎麼叫 resetter：`python -m claude_timer.core.usage --json`（看額度）、`python -m claude_timer.core.ping --json`
（該刷新才刷新）。每一次都經 SYNOX 的 JobRunner（app.jobs）：argv 陣列、不經 shell、有逾時、逾時整棵程序樹一起收。
子程序的環境拿掉像密鑰的變數。這個工具本身不讀 ~/.claude、不讀任何金鑰、不連網：用量查詢與探針都是 resetter 自己在本機做的。

資料：只有設定（resetter 資料夾、python）存在 pack.store.of(user)（保管箱）；不寫任何檔案。
"""

import json
import os
import threading

from isdtelekit.core.registry import READ, WRITE, Field
from isdtelekit.jobs.cli import clean_env
from isdtelekit.jobs.runner import TIMED_OUT

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

USAGE_TIMEOUT = 30                 # 看額度：一次 HTTPS GET，resetter 自己 15 秒就放棄
CHECK_TIMEOUT = 30
REFRESH_TIMEOUT = 240              # 不等待：查額度＋最多一次探針（探針自己 90 秒上限）＋再查一次額度
REFRESH_WAIT_TIMEOUT = 1200        # wait=是：再加上 ping 預設最多原地等 920 秒
SETUP_COMMAND = "/resetter_setup"
EXIT_ADVICE = {1: "多半是額度用完、網路逾時或模型暫時不能用，過一會兒再試；/resetter_usage 可以看額度。",
               2: "這台電腦找不到 claude（官方 Claude Code 命令列）。請先安裝並登入一次。",
               3: "claude 還沒登入，或登入過期了。在這台電腦打開一次 claude（或 Claude Code）就會自動換新。",
               4: "resetter 的環境沒裝好：到 ClaudeTimerResetter 的資料夾跑一次 scripts\\setup.bat（它會裝好 claude_subscription）。"}
WINDOW_LABELS = (("five_hour", "5 小時視窗"), ("seven_day", "7 天視窗"))


# ── 純函式（不碰 SYNOX，測試直接叫） ─────────────────────────────────────

def number(value):
    """JSON 裡的數字（排除布林）；不是數字回 None。"""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def left_text(seconds):
    """剩多久：2 小時 05 分／35 分／不到 1 分。"""
    seconds = number(seconds)
    if seconds is None:
        return "不知道"
    minutes = int(seconds) // 60
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小時 {minutes:02d} 分"
    return f"{minutes} 分" if minutes else "不到 1 分"


def when_text(window):
    """resetter 給的本機時間 ISO 字串（2026-11-23T13:40:00+08:00）→ 13:40；離得遠（6 小時以上，例如 7 天視窗）加上月-日。"""
    text = str(window.get("resets_local") or "")
    date, sep, rest = text.partition("T")
    clock = rest[:5]
    if not sep or len(clock) != 5 or clock[2] != ":" or len(date) != 10:
        return ""
    left = number(window.get("seconds_left"))
    return f"{date[5:]} {clock}" if left is None or left >= 6 * 3600 else clock


def window_line(label, window):
    if not isinstance(window, dict):
        return ""
    percent = number(window.get("percent"))
    used = f"已用 {percent:.0f}%" if percent is not None else "已用多少不知道"
    if not window.get("resets_at"):
        return f"{label}：{used}，還沒開始計時"
    when = when_text(window)
    return f"{label}：{used}，{when + ' ' if when else ''}重置（剩 {left_text(window.get('seconds_left'))}）"


def window_lines(windows):
    windows = windows if isinstance(windows, dict) else {}
    return [line for key, label in WINDOW_LABELS for line in [window_line(label, windows.get(key))] if line]


def next_reset(windows):
    """『下次重置』一句話：5 小時視窗幾點重置、還剩多久；還沒開始計時就明說。"""
    five = (windows or {}).get("five_hour") if isinstance(windows, dict) else None
    if not isinstance(five, dict):
        return ""
    if not five.get("resets_at"):
        return "5 小時視窗還沒開始計時"
    when = when_text(five)
    return f"下次重置：{when + '（' if when else ''}剩 {left_text(five.get('seconds_left'))}{'）' if when else ''}"


def short(text, size=160):
    text = " ".join(str(text or "").split())
    return text if len(text) <= size else text[:size] + "…"


def failure_text(data, code):
    """resetter 說『沒成功』：它的原因（reason）原樣轉述，再加一句怎麼辦（照結束碼）。"""
    reason = short((data or {}).get("reason") or "原因沒說")
    lines = [f"❌ {reason}"]
    detail = short((data or {}).get("detail"), 120)
    if detail and detail != reason:
        lines.append(f"細節：{detail}")
    advice = EXIT_ADVICE.get(code)
    if advice:
        lines.append(f"怎麼辦：{advice}")
    return "\n".join(lines)


def usage_text(data):
    windows = data.get("windows")
    lines = ["⏱ 額度用量（這次查詢不花錢）"] + ["  " + line for line in window_lines(windows)]
    five = windows.get("five_hour") if isinstance(windows, dict) else None
    if data.get("window_open") is False or (isinstance(five, dict) and not five.get("resets_at")):
        lines.append("5 小時視窗還沒開始計時：現在 /resetter_refresh 才有意義。")
    elif len(lines) == 1:
        lines.append("  （resetter 沒有回報視窗資料）")
    return "\n".join(lines)


def refresh_text(data, code):
    """ping --json 的結果 → 一句白話：有沒有真的打、為什麼、下次重置。"""
    action = data.get("action")
    reason = short(data.get("reason") or "")
    if data.get("ok") is not True:
        return "⏱ 刷新沒成功\n" + failure_text(data, code)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    windows = usage.get("windows")
    if action == "refreshed":
        probe = data.get("probe") if isinstance(data.get("probe"), dict) else {}
        cost, seconds = number(probe.get("cost_usd")), number(probe.get("seconds"))
        spent = (f"花了 ${cost:.5f}" if cost is not None else "花了約 $0.00025") + (f"、{seconds:.1f} 秒" if seconds is not None else "")
        lines = [f"✅ 已刷新：打了一次最便宜的探針（{spent}）", f"為什麼打：{reason}"]
        waited = number(data.get("waited_seconds"))
        if waited:
            lines.append(f"原地等了 {left_text(waited)}")
    elif action == "skipped":
        lines = ["👌 這次不用打（沒花錢）", f"為什麼：{reason}"]
    elif action == "waiting":
        lines = ["⏳ 這次先沒打（沒花錢）", f"為什麼：{reason}",
                 "快重置了，現在打是打在舊視窗上。等重置過後再 /resetter_refresh 一次，或加 wait=是 讓我等它重置完（最多約 15 分鐘）。"]
    else:
        lines = [f"⏱ resetter 回了沒看過的結果（action={short(action, 40)}）。{reason}"]
    after = next_reset(windows)
    if after:
        lines.append(after)
    elif isinstance(usage.get("error"), str):
        lines.append("（這次沒讀到額度：" + short(usage["error"], 80) + "）")
    return "\n".join(lines)


def refresh_argv(force, wait):
    """ping 的旗標：--json 一定要；force＝--force；不等待＝--no-wait（跟 resetter 自己的排程同一條路）。"""
    return ["--json"] + (["--force"] if force else []) + ([] if wait else ["--no-wait"])


def truthy(value):
    return str(value or "").strip() in ("是", "yes", "y", "true", "1")


# ── 安裝 ──────────────────────────────────────────────────────────────

def install(app, pack):
    g = pack.group
    lock = threading.Lock()
    running = set()                     # 正在刷新的人（同一個人不會同時打兩次）

    def uid(user):
        return user if isinstance(user, int) else getattr(user, "tg_id", None)

    def settings(user):
        return (pack.store.of(user).load({}) or {}).get("settings") or {}

    def run(user, entry, tail, timeout):
        """叫 resetter 的一個入口：(Job, 輸出裡最後一份 JSON 或 None)。找不到 resetter 丟 BridgeProblem。"""
        python, folder = resolve_resetter(settings(user), SETUP_COMMAND)
        argv = module_argv(python, entry, tail)
        try:
            job = app.jobs.run(f"claude-resetter-{entry}", argv, cwd=folder, timeout=timeout, env=clean_env())
        except OSError as e:
            raise BridgeProblem(f"⏱ 叫不起 resetter 的 Python：{type(e).__name__}（檔案被擋或不是程式？用 {SETUP_COMMAND} 看設定）") from None
        text = job.output
        if job.state == TIMED_OUT:
            raise BridgeProblem(f"⏱ resetter 超過 {timeout} 秒沒有回應，先停掉了。"
                                + ("這次有沒有打到不確定，用 /resetter_usage 看 5 小時視窗開了沒。" if entry == "ping" else ""))
        data = last_json(text)
        if data is None:
            tail_text = "\n".join(text.strip().splitlines()[-4:])
            raise BridgeProblem("⏱ resetter 沒有回傳看得懂的結果（版本太舊、還沒有 --json？）。最後幾行：\n" + short(tail_text, 400))
        return job, data

    def say(api, chat_id, text):
        api.send_message(chat_id, text)

    def guarded(fn):
        def go(api, chat_id, user, values):
            try:
                fn(api, chat_id, user, values)
            except BridgeProblem as p:
                api.send_message(chat_id, p.text)
        return go

    def slow(fn):
        """會等子程序的指令：經 AI 呼叫時照常同步（結果要交回 AI）；人打的、排程叫的丟背景，不卡住收訊。"""
        def go(api, chat_id, user, values):
            if getattr(api, "via_ai", False) is True or getattr(api, "sync_result", False) is True:
                fn(api, chat_id, user, values)
            else:
                app.tasks.run(lambda: fn(api, chat_id, user, values), name=f"pack-{pack.name}")
        return go

    # ── 指令 ──

    def cmd_usage(api, chat_id, user, values):
        job, data = run(user, "usage", ["--json"], USAGE_TIMEOUT)
        if data.get("ok") is not True:
            say(api, chat_id, "⏱ 讀不到額度\n" + failure_text(data, job.returncode))
            return
        say(api, chat_id, usage_text(data))

    def cmd_refresh(api, chat_id, user, values):
        force, wait = truthy(values.get("force")), truthy(values.get("wait"))
        key = uid(user)
        with lock:
            if key in running:
                say(api, chat_id, "⏱ 上一次刷新還在跑，等它做完再說（不會重複打）。")
                return
            running.add(key)
        try:
            job, data = run(user, "ping", refresh_argv(force, wait), REFRESH_WAIT_TIMEOUT if wait else REFRESH_TIMEOUT)
        finally:
            with lock:
                running.discard(key)
        say(api, chat_id, refresh_text(data, job.returncode))

    refresh_job = slow(guarded(cmd_refresh))

    def refresh_entry(api, chat_id, user, values):
        # 這兩件要在『誰叫的』還看得出來的這一刻擋（丟背景之後就看不出了）
        if truthy(values.get("force")):
            kit.require_human()                      # 強制打會花錢：只有你本人，AI 與排程都不行
        if truthy(values.get("wait")) and getattr(api, "via_ai", False) is True:
            say(api, chat_id, "⏱ AI 呼叫時不能等：等重置最久要 15 分鐘，會卡住這一輪。不加 wait 直接刷新，或請你本人（或排程）用 wait=是。")
            return
        refresh_job(api, chat_id, user, values)

    def cmd_check(api, chat_id, user, values):
        s = settings(user)
        python, folder = resolve_resetter(s, SETUP_COMMAND)
        lines = ["⏱ resetter 檢查", f"  ✅ 找得到 ClaudeTimerResetter：{folder}", f"  ✅ 用的 Python：{python}"]
        missing = [name for name in ("usage.py", "ping.py") if not os.path.isfile(os.path.join(folder, "claude_timer", "core", name))]
        if missing:
            lines.append("  ❌ 這份 resetter 太舊，缺少：" + "、".join(missing) + "（更新 ClaudeTimerResetter 再試）。")
            say(api, chat_id, "\n".join(lines))
            return
        job, data = run(user, "usage", ["--json"], CHECK_TIMEOUT)
        if data.get("ok") is True:
            lines.append("  ✅ 讀得到額度（代表 claude 登入過、憑證沒過期）")
            lines.append("  ℹ️ 探針本身（claude 命令列能不能呼叫）要真的刷新一次才知道，檢查不會花錢去試。")
        else:
            lines.append("  ❌ 讀不到額度：" + short(data.get("reason") or "原因沒說"))
            advice = EXIT_ADVICE.get(job.returncode)
            if advice:
                lines.append(f"  怎麼辦：{advice}")
        say(api, chat_id, "\n".join(lines))

    def cmd_setup(api, chat_id, user, values):
        kit.require_human()
        updates = {}
        for key, label in (("home", "ClaudeTimerResetter 的資料夾"), ("python", "Python")):
            if key not in values:
                continue
            value = values[key].strip().strip('"')
            if value in ("-", "清除", "none"):
                value = ""
            if value:
                value = os.path.abspath(os.path.expanduser(value))
                if not os.path.exists(value):
                    raise BridgeProblem(f"⏱ 找不到{label}：{value}")
                if key == "home" and not os.path.isdir(value):
                    raise BridgeProblem(f"⏱ home 要是資料夾（ClaudeTimerResetter 原始碼的那一層，裡面有 claude_timer\\）：{value}\n"
                                        "只有 ClaudeTimerResetter.exe 的不能用：要 clone 專案、跑一次 scripts\\setup.bat。")
                if key == "python" and not os.path.isfile(value):
                    raise BridgeProblem(f"⏱ python 要是 python 程式本身（python.exe 或 python）：{value}")
            updates[key] = value
        if updates:
            pack.store.of(user).update(lambda d: (d.setdefault("settings", {}).update(updates), None)[1], {})
        s = settings(user)
        lines = ["⏱ Claude 重置計時的設定" + ("（改好了）" if updates else "") + "：",
                 f"  home：{s.get('home') or '（還沒設定）'}",
                 f"  python：{s.get('python') or '（自動：用 home 底下的 .venv）'}"]
        try:
            resolve_resetter(s, SETUP_COMMAND)
            lines.append("  ✅ 找得到 resetter。用 /resetter_check 看能不能讀到額度、/resetter_usage 看現在的額度。")
        except BridgeProblem as p:
            lines.append(p.text)
        say(api, chat_id, "\n".join(lines))

    kit.command(g, "usage", slow(guarded(cmd_usage)), "看 Claude 額度用了多少、5 小時視窗幾點重置（不花錢）", effect=READ)
    # WRITE：會花約 $0.00025（打一次最便宜的探針），但只有『該刷新』時才打；AI 與排程都能叫，force 只有本人
    kit.command(g, "refresh", refresh_entry, "該刷新就刷新、不該就不打：提早啟動 5 小時額度視窗的重置時鐘（有打約 $0.00025）",
                effect=WRITE,
                fields=[Field("force", "不管狀態強制打一次（會花錢，只有你本人能下）", choices=("是", "否")),
                        Field("wait", "快重置時等它重置完再打，最多約 15 分鐘（本人或排程用）", choices=("是", "否"))])
    kit.command(g, "check", slow(guarded(cmd_check)), "檢查 resetter 找得到嗎、讀得到額度嗎（不花錢）", effect=READ)
    kit.command(g, "setup", guarded(cmd_setup), "設定 ClaudeTimerResetter 的資料夾（只有你本人能改）", effect=WRITE, ai=False,
                fields=[Field("home", "ClaudeTimerResetter 原始碼的資料夾（裡面有 claude_timer）"),
                        Field("python", "要用哪個 Python（留空＝home 底下 .venv 的）")], positional=0)
