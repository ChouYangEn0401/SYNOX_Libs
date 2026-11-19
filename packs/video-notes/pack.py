"""🎬 影片筆記：把影片交給這台電腦上**已經裝好的** Video Notes 做筆記（一座薄薄的橋，不附 Whisper／yt-dlp／AI）。

    /v2n <網址…> [profile=…] [with=…] [preset=…] [focus=…] [outputs=…] [redo=…]   做筆記（背景跑，做完通知）
    /v2n_status [編號]      看進度          /v2n_get [編號] [item=…] [slug=…]   拿筆記（Markdown）
    /v2n_list [關鍵字]      列出筆記庫      /v2n_combine <1,2,3> [title=…] …    合併多支
    /v2n_cancel [編號]      停掉工作        /v2n_check                           Video Notes 能不能用
    /v2n_setup [program=…] [python=…] [out=…] [timeout=…]                          設定（只有你本人）

怎麼叫 Video Notes：它的命令列＋`--json`（README「給程式呼叫」：stdout 一份 {"ok", "data"|"error"}，進度印在 stderr；
退出碼 0／2／3／4／1）。每個工作經 SYNOX 的 JobRunner（app.jobs）：argv 陣列、不開視窗、逾時與取消整棵程序樹一起收、
SYNOX 關掉時子程序跟著結束。網址前面一定放 `--`，所有選項都寫成 `--k=v` 一段，值不會被當成另一個旗標。
子程序的環境拿掉像密鑰的變數（Video Notes 自己讀它的 .env，不需要 SYNOX 的任何密鑰）。

資料：設定與工作清單在 pack.store.of(user)（保管箱）；每個工作的輸出（進度、最後的 JSON）在 pack.path_of(user, "jobs/<編號>.log")，
只留最近 KEEP_JOBS 個。筆記本身在 Video Notes 自己的輸出資料夾，這裡只讀（filesystem:any）、不改不刪。
"""

import json
import os
import re
import threading
import time

from isdtelekit.core.registry import DANGER, WRITE, Field
from isdtelekit.jobs.cli import clean_env
from isdtelekit.jobs.runner import CANCELLED, TIMED_OUT, read_log

from synox.packs import kit

KEEP_JOBS = 30
MAX_URLS = 20
DEFAULT_TIMEOUT_MIN = 180
SYNC_TIMEOUT = 120                 # --check、--library 這種只讀、不連網的呼叫
CHUNK = 3500                       # 一則訊息最多幾個字（Telegram 上限 4096）
STAGES = (("info", "影片資訊"), ("download", "下載"), ("audio", "抽音訊"), ("transcribe", "語音辨識"),
          ("notes", "AI 筆記"), ("outputs", "其他輸出"), ("notify", "通知"))
MAIN_STAGES = 5                    # 前五個每支影片一定會走（其他輸出、通知看設定）
STATE_TEXT = {"queued": "⏳ 排隊中", "running": "▶ 進行中", "done": "✅ 完成", "failed": "❌ 失敗",
              "cancelled": "⏹ 已取消", "timed_out": "⌛ 逾時被停掉", "interrupted": "⚠️ SYNOX 重開過，中斷了"}
ACTIVE = ("queued", "running")
URL_RE = re.compile(r"^https?://[^\s\"'<>]+$", re.IGNORECASE)
WORD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+\-]{0,40}$")                  # profile、preset
LIST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+\-]{0,40}(,[A-Za-z0-9][A-Za-z0-9_+\-]{0,40}){0,15}$")
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+\-]{0,80}$")
OUT_WORDS = ("downloads", "data")
SETUP_HINT = ("🎬 還沒告訴我 Video Notes 裝在哪裡。請你本人在這裡打：\n"
              "/v2n_setup program=<VideoNotes 的 exe、VideoNotes.py，或它的工具資料夾>\n"
              "（從原始碼跑的會自動用它旁邊 .venv 的 Python；要用別的 Python 再加 python=<python.exe>）")


class Problem(Exception):
    """回使用者一句話（指令回訊息、畫面顯示），不算工具壞掉。"""

    def __init__(self, text):
        super().__init__(text)
        self.text = str(text)


# ── 純函式（不碰 SYNOX，測試直接叫） ─────────────────────────────────────

def resolve_program(settings):
    """設定 → (argv 前綴, cwd)。找不到就丟 Problem（白話：哪裡不對、怎麼改）。"""
    program = (settings.get("program") or "").strip().strip('"')
    if not program:
        raise Problem(SETUP_HINT)
    path = os.path.abspath(os.path.expanduser(program))
    if os.path.isdir(path):
        script = os.path.join(path, "VideoNotes.py")
        if os.path.isfile(script):
            path = script
        else:
            exes = sorted((n for n in os.listdir(path) if n.lower().startswith("videonotes") and n.lower().endswith(".exe")),
                          key=lambda n: os.path.getmtime(os.path.join(path, n)), reverse=True)
            if not exes:
                raise Problem(f"🎬 這個資料夾裡找不到 VideoNotes.py 或 VideoNotes 的 exe：{path}\n用 /v2n_setup program=… 改。")
            path = os.path.join(path, exes[0])
    if not os.path.isfile(path):
        raise Problem(f"🎬 找不到設定的 Video Notes：{path}\n可能搬家或刪掉了；用 /v2n_setup program=… 重新設定。")
    folder = os.path.dirname(path)
    if path.lower().endswith(".exe"):
        return [path], folder
    if path.lower().endswith(".py"):
        python = (settings.get("python") or "").strip().strip('"')
        if not python:
            for rel in ((".venv", "Scripts", "python.exe"), (".venv", "bin", "python")):
                if os.path.isfile(os.path.join(folder, *rel)):
                    python = os.path.join(folder, *rel)
                    break
        if not python:
            raise Problem("🎬 VideoNotes.py 旁邊沒有 .venv（它自己的 Python 環境）。先照 Video Notes 的說明跑一次 run.bat 建好，"
                          "或用 /v2n_setup python=<python.exe> 指定。")
        if not os.path.isfile(python):
            raise Problem(f"🎬 找不到設定的 Python：{python}\n用 /v2n_setup python=… 改（留空＝用 .venv 的）。")
        return [python, path], folder
    raise Problem("🎬 program 要是 VideoNotes 的 .exe、VideoNotes.py，或它的工具資料夾。")


def parse_envelope(text):
    """輸出裡最後一份 {"ok": …} JSON（Video Notes 用 indent=2 印好幾行；前面是 stderr 的進度）。沒有回 None。"""
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
        if isinstance(data, dict) and "ok" in data:
            return data
    return None


def progress_of(text):
    """stderr 的進度行（「[標題] 下載：完成」「[標題] 語音辨識…」）→ [{"label", "stage", "state", "done"}]，照出現順序。"""
    items = {}
    for raw in (text or "").splitlines():
        m = re.match(r"^\[(.{1,60}?)\] (.+)$", raw.strip())
        if not m:
            continue
        label, msg = m.group(1), m.group(2)
        item = items.setdefault(label, {"label": label, "stage": "", "state": "", "done": 0, "last": ""})
        item["last"] = msg[:200]
        for key, name in STAGES:
            if msg == f"{name}…":
                item.update(stage=name, state="running")
            elif msg.startswith(f"{name}："):
                word = msg[len(name) + 1:]
                state = "failed" if word.startswith("失敗") else "done"
                item.update(stage=name, state=state)
                if state == "done" and key in dict(STAGES[:MAIN_STAGES]):
                    item["done"] += 1
    return list(items.values())


def progress_text(items):
    lines = []
    for it in items:
        if it["state"] == "running":
            now = f"{it['stage']}中"
        elif it["state"] == "failed":
            now = f"{it['stage']}失敗"
        elif it["stage"]:
            now = f"{it['stage']}完成"
        else:
            now = it["last"] or "準備中"
        lines.append(f"  · {it['label']}：{now}（{min(it['done'], MAIN_STAGES)}/{MAIN_STAGES}）")
    return "\n".join(lines)


def error_summary(err):
    """Video Notes 的錯誤合約 → {"code", "reason", "action"}（只留給人看的部分）。"""
    if not isinstance(err, dict):
        return {"code": "internal_bug", "reason": str(err or "處理失敗")[:300], "action": ""}
    remedy = err.get("remedy") or {}
    return {"code": str(err.get("code") or ""), "reason": str(err.get("reason") or "處理失敗")[:300],
            "action": str(remedy.get("action") or "")[:300] if isinstance(remedy, dict) else ""}


def summarize_result(kind, data):
    """--json 的 data → 存進工作紀錄的精簡結果（不存整份筆記；筆記在 notes_file）。"""
    def video(e):
        if not isinstance(e, dict):
            return None
        return {"title": str(e.get("title") or e.get("url") or "")[:200], "url": str(e.get("url") or "")[:500],
                "folder": str(e.get("folder") or ""), "notes_file": str(e.get("notes_file") or ""),
                "status": str(e.get("status") or ""), "error": error_summary(e["error"]) if e.get("error") else None}
    out = {"videos": [], "collection": None}
    if kind == "combine" or (isinstance(data, dict) and "collection" in data):
        out["videos"] = [v for v in map(video, (data or {}).get("videos") or []) if v]
        col = (data or {}).get("collection")
        if isinstance(col, dict):
            out["collection"] = {"title": str(col.get("title") or "合併")[:200], "folder": str(col.get("folder") or ""),
                                 "notes_file": str(col.get("notes_file") or "")}
    elif isinstance(data, list):
        out["videos"] = [v for v in map(video, data) if v]
    elif isinstance(data, dict):
        out["videos"] = [v for v in [video(data)] if v]
    return out


def note_files(result):
    """工作結果裡的筆記：[(標題, notes_file)]（合併的那份放最前面）。"""
    out = []
    col = (result or {}).get("collection")
    if col and col.get("notes_file"):
        out.append((col.get("title") or "合併", col["notes_file"]))
    for v in (result or {}).get("videos") or []:
        if v.get("notes_file"):
            out.append((v.get("title") or v.get("url") or "影片", v["notes_file"]))
    return out


def read_note(path):
    """讀一份筆記：只收 Video Notes 產生的 notes.<slug>.md。"""
    name = os.path.basename(path or "")
    if not (name.startswith("notes.") and name.endswith(".md")) or not os.path.isfile(path):
        raise Problem("🎬 找不到這份筆記的檔案（可能被搬走或刪掉了）。")
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def latest_note(folder, slug=""):
    """一個影片／合併資料夾裡的筆記：給 slug 就拿那份，不給拿最新改過的那份。"""
    if not os.path.isdir(folder or ""):
        raise Problem("🎬 找不到這一項的資料夾（可能被搬走或刪掉了）；/v2n_list 重新列一次。")
    if slug:
        if not SLUG_RE.match(slug):
            raise Problem("🎬 slug 只能有英數字與 _ . + -")
        return os.path.join(folder, f"notes.{slug}.md")
    names = [n for n in os.listdir(folder) if n.startswith("notes.") and n.endswith(".md")]
    if not names:
        raise Problem("🎬 這一項還沒有筆記（可能只做了逐字稿）。")
    names.sort(key=lambda n: os.path.getmtime(os.path.join(folder, n)), reverse=True)
    return os.path.join(folder, names[0])


def chunks(text, size=CHUNK):
    """長文切成幾則訊息（盡量在換行切）。"""
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


def notes_argv(values):
    """做筆記的參數 → Video Notes 的 argv 尾段（--k=v 一段；網址放在 -- 後面）。不合就丟 Problem。"""
    urls = [u for u in re.split(r"[\s,，]+", values.get("urls") or "") if u]
    if not urls:
        raise Problem("🎬 要給影片網址（http 或 https 開頭；好幾支用空白隔開）。")
    bad = [u for u in urls if not URL_RE.match(u)]
    if bad:
        raise Problem(f"🎬 這些不像影片網址：{'、'.join(bad[:3])}")
    if len(urls) > MAX_URLS:
        raise Problem(f"🎬 一次最多 {MAX_URLS} 支。")
    argv = []
    for key, flag in (("profile", "--profile"), ("preset", "--preset")):
        value = (values.get(key) or "").strip()
        if value:
            if not WORD_RE.match(value):
                raise Problem(f"🎬 {key} 只能有英數字與 _ + -（例如 news、finance）。")
            argv.append(f"{flag}={value}")
    for key, flag in (("with", "--with"), ("outputs", "--outputs"), ("redo", "--redo")):
        value = re.sub(r"\s+", "", values.get(key) or "")
        if value:
            if not LIST_RE.match(value):
                raise Problem(f"🎬 {key} 寫成逗號隔開的名字（例如 timeline,actions）。")
            argv.append(f"{flag}={value}")
    focus = " ".join((values.get("focus") or "").split())
    if focus:
        argv.append(f"--focus={focus[:300]}")
    if not (values.get("redo") or "").strip():
        argv.append("--reuse")                    # 處理過的影片：沿用已有的檔（不會在背景問問題）
    return argv, urls


# ── 安裝 ──────────────────────────────────────────────────────────────

def install(app, pack):
    g = pack.group
    lock = threading.RLock()                    # 同一個執行緒可以再拿（launch 裡面會改紀錄）
    live = {}                                   # (tg_id, 工作編號) → JobRunner 的 job id（這次啟動開的才有）

    def uid(user):
        return user if isinstance(user, int) else getattr(user, "tg_id", None)

    def store(user):
        return pack.store.of(user)

    def load(user):
        data = store(user).load({}) or {}
        data.setdefault("settings", {})
        data.setdefault("jobs", [])
        data.setdefault("next", 1)
        data.setdefault("library", [])
        return data

    def change(user, fn):
        with lock:
            return store(user).update(lambda d: fn(_defaults(d)), {})

    def _defaults(d):
        d.setdefault("settings", {})
        d.setdefault("jobs", [])
        d.setdefault("next", 1)
        d.setdefault("library", [])
        return d

    def settings(user):
        return load(user)["settings"]

    def env():
        return clean_env()

    def log_path(user, name):
        """執行中的工作輸出（JobRunner 只會寫檔）。**只在跑的時候存在**：讀完結果就刪（finished／run_sync），
        結果只存進保管箱（pack.store，密文）。"""
        return pack.path_of(user, "jobs", f"{name}.log")

    def urls_path(user, n):
        """網址清單（--file）：不放在 argv（JobRunner 會把 argv 寫進 SYNOX 的 log），跑完就刪。"""
        path = pack.path_of(user, "jobs", f"{n}.urls")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def remove(*paths):
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass

    def timeout_s(user):
        try:
            minutes = int(settings(user).get("timeout") or DEFAULT_TIMEOUT_MIN)
        except ValueError:
            minutes = DEFAULT_TIMEOUT_MIN
        return max(5, minutes) * 60

    def out_args(user):
        out = (settings(user).get("out") or "").strip()
        return [f"--out={out}"] if out else []

    # ── 一次就做完的呼叫（--check、--library：只讀、不連網） ──

    def run_sync(user, tail, name):
        prefix, cwd = resolve_program(settings(user))
        try:
            job = app.jobs.run(f"video-notes-{name}", prefix + tail + ["--json"], cwd=cwd, timeout=SYNC_TIMEOUT,
                               env=env(), log_path=log_path(user, f"sync-{name}"))
        except OSError as e:
            raise Problem(f"🎬 叫不起 Video Notes：{type(e).__name__}（檔案被擋或不是程式？用 /v2n_check 看）") from None
        text = read_log(job.log_path)
        remove(job.log_path)                    # 筆記庫的標題、路徑不留在明文檔
        envelope = parse_envelope(text)
        if job.state == TIMED_OUT:
            raise Problem(f"🎬 Video Notes 超過 {SYNC_TIMEOUT} 秒沒有回應，先停掉了。")
        if envelope is None:
            tail_text = "\n".join(text.strip().splitlines()[-5:])
            raise Problem("🎬 Video Notes 沒有回傳看得懂的結果（版本太舊、還不支援 --json？）。最後幾行：\n" + tail_text[-600:])
        return envelope

    # ── 背景工作 ──

    def find(data, n):
        for rec in data["jobs"]:
            if rec["id"] == n:
                return rec
        return None

    def mark_interrupted(user):
        """這次啟動沒開過、卻還記成進行中的工作＝上一次 SYNOX 開著時跑的，SYNOX 關掉時已經跟著結束。"""
        me = uid(user)

        def fix(d):
            changed = False
            for rec in d["jobs"]:
                if rec["state"] == "running" and (me, rec["id"]) not in live:
                    rec.update(state="interrupted", finished=int(time.time()))
                    remove(log_path(user, str(rec["id"])), urls_path(user, rec["id"]))
                    changed = True
            return changed
        change(user, fix)

    def pump(user):
        """沒有工作在跑就開下一個排隊的（一次只跑一個：同一張顯示卡跑兩個語音辨識只會互搶）。
        整段拿著鎖：兩個執行緒同時 pump 也只會開一個。"""
        me = uid(user)
        with lock:
            if any(k[0] == me for k in live):
                return
            nxt = next((r for r in load(user)["jobs"] if r["state"] == "queued"), None)
            if nxt is not None:
                launch(user, nxt["id"], nxt["argv"], nxt.get("urls") or [])

    def launch(user, n, tail, urls):
        """開工作 #n。開程式、記進 live、紀錄改成 running 在同一段鎖裡：程式就算馬上結束，
        finished()（JobRunner 的執行緒）也要等這段做完才拿得到鎖，「完成」不會被「進行中」蓋掉。"""
        me = uid(user)
        try:
            prefix, cwd = resolve_program(settings(user))
        except Problem as p:
            finish_record(user, n, "failed", error={"code": "needs_config", "reason": p.text, "action": ""})
            return
        if urls:                                 # 網址放檔案（--file），不進 argv、不進 SYNOX 的 log
            with open(urls_path(user, n), "w", encoding="utf-8") as f:
                f.write("\n".join(urls) + "\n")
            tail = tail + [f"--file={urls_path(user, n)}"]
        with lock:
            try:
                job = app.jobs.start(f"video-notes-{n}", prefix + tail, cwd=cwd, timeout=timeout_s(user), env=env(),
                                     log_path=log_path(user, str(n)),
                                     on_done=lambda job, user=user, n=n: finished(user, n, job))
            except OSError as e:
                remove(urls_path(user, n))
                finish_record(user, n, "failed", error={"code": "dependency_missing", "action": "",
                                                        "reason": f"叫不起 Video Notes（{type(e).__name__}）"})
                return
            live[(me, n)] = job.id

            def started(d):
                r = find(d, n)
                if r is not None and r["state"] == "queued":
                    r.update(state="running", started=int(time.time()), runner=job.id)
            change(user, started)
        report("running", f"工作 {n} 開始了")

    def finished(user, n, job):
        me = uid(user)
        with lock:
            live.pop((me, n), None)
        text = read_log(job.log_path)
        remove(job.log_path, urls_path(user, n))  # 結果（精簡過）只進保管箱；明文的輸出不留
        envelope = parse_envelope(text)
        rec = find(load(user), n) or {}
        kind = rec.get("kind", "notes")
        result = summarize_result(kind, (envelope or {}).get("data"))
        if job.state == CANCELLED:
            state, error = "cancelled", None
        elif job.state == TIMED_OUT:
            state, error = "timed_out", {"code": "timeout", "reason": "超過設定的時間還沒做完，停掉了（已經做好的檔案都還在）",
                                         "action": "用 /v2n_setup timeout=<分鐘> 放寬，再做一次會沿用做好的部分"}
        elif envelope is None:
            tail_text = "\n".join(text.strip().splitlines()[-3:])[-300:]
            state, error = "failed", {"code": "internal_bug", "reason": "Video Notes 沒有回傳看得懂的結果：" + tail_text,
                                      "action": ""}
        elif envelope.get("ok"):
            state, error = "done", None
        else:
            state, error = "failed", error_summary(envelope.get("error"))
        finish_record(user, n, state, error=error, result=result)
        pump(user)

    def finish_record(user, n, state, error=None, result=None):
        def done(d):
            r = find(d, n)
            if r is not None:
                r.update(state=state, finished=int(time.time()), error=error, result=result or r.get("result"))
            # 只留最近 KEEP_JOBS 個（做完的才丟），log 一起清
            ended = [x for x in d["jobs"] if x["state"] not in ACTIVE]
            for old in ended[:-KEEP_JOBS] if len(ended) > KEEP_JOBS else []:
                d["jobs"].remove(old)
            return r
        rec = change(user, done)
        if rec is None:
            return
        tell(user, done_text(rec))
        report("failed" if state == "failed" else "ok", f"工作 {n}：{STATE_TEXT.get(state, state)}", tag="video-notes-done")

    def tell(user, text):
        me = uid(user)
        if isinstance(me, int):
            for part in chunks(text):
                app.notify(me, part)

    def report(state, message, tag=""):
        try:
            pack.report_status(state, message, tag=tag)
        except Exception:                       # 舊版 SYNOX、測試的假 pack：沒有狀態回報也照做
            pass

    def submit(user, kind, argv, label, chat_id=None, urls=()):
        resolve_program(settings(user))         # 還沒設定（或路徑壞了）就先講，不排進去
        mark_interrupted(user)

        def add(d):
            n = d["next"]
            d["next"] = n + 1
            d["jobs"].append({"id": n, "kind": kind, "label": label, "argv": argv, "urls": list(urls), "state": "queued",
                              "created": int(time.time()), "chat": chat_id})
            return n
        n = change(user, add)
        pump(user)
        return n

    # ── 給人看的字 ──

    def job_line(rec):
        when = time.strftime("%m/%d %H:%M", time.localtime(rec.get("created") or 0))
        return f"#{rec['id']} {STATE_TEXT.get(rec['state'], rec['state'])}  {rec.get('label', '')}  （{when}）"

    def done_text(rec):
        head = f"🎬 工作 #{rec['id']}：{STATE_TEXT.get(rec['state'], rec['state'])}  {rec.get('label', '')}"
        lines = [head]
        res = rec.get("result") or {}
        for v in res.get("videos") or []:
            mark = "✓" if not v.get("error") else "✗"
            lines.append(f"  {mark} {v.get('title') or v.get('url')}")
            if v.get("error"):
                lines.append(f"     原因：{v['error']['reason']}")
        if res.get("collection"):
            lines.append(f"  ✓ 合併：{res['collection']['title']}")
        err = rec.get("error")
        if err:
            lines.append(f"原因：{err['reason']}")
            if err.get("action"):
                lines.append(f"怎麼辦：{err['action']}")
        if rec["state"] == "done":
            lines.append(f"拿筆記：/v2n_get {rec['id']}")
        return "\n".join(lines)

    def job_detail(user, rec):
        lines = [job_line(rec)]
        if rec["state"] == "running":
            items = progress_of(read_log(log_path(user, str(rec["id"]))))
            lines.append(progress_text(items) or "  · 準備中…")
        elif rec["state"] not in ACTIVE:
            lines.append(done_text(rec).split("\n", 1)[1] if "\n" in done_text(rec) else "")
        return "\n".join(x for x in lines if x)

    # ── 核心（指令與畫面共用；不合丟 Problem） ──

    def core_add(user, values, chat_id=None):
        tail, urls = notes_argv(values)
        argv = tail + out_args(user) + ["--json"]
        label = urls[0] if len(urls) == 1 else f"{len(urls)} 支影片"
        n = submit(user, "notes", argv, label[:120], chat_id, urls=urls)
        return n

    def core_status(user, n=None):
        mark_interrupted(user)
        pump(user)
        data = load(user)
        if n is not None:
            rec = find(data, n)
            if rec is None:
                raise Problem(f"🎬 沒有工作 #{n}（/v2n_status 看全部）。")
            return job_detail(user, rec)
        jobs = data["jobs"][-10:][::-1]
        if not jobs:
            return "🎬 還沒有工作。用 /v2n <網址> 開始。"
        return "\n".join(["🎬 最近的工作："] + [job_detail(user, r) if r["state"] == "running" else job_line(r) for r in jobs])

    def core_cancel(user, n=None):
        mark_interrupted(user)
        data = load(user)
        if n is None:
            active = [r for r in data["jobs"] if r["state"] in ACTIVE]
            if not active:
                raise Problem("🎬 現在沒有在做或排隊的工作。")
            n = active[-1]["id"]
        rec = find(data, n)
        if rec is None:
            raise Problem(f"🎬 沒有工作 #{n}。")
        if rec["state"] == "queued":
            def drop(d):
                r = find(d, n)
                if r is not None:
                    r.update(state="cancelled", finished=int(time.time()))
            change(user, drop)
            return f"⏹ 工作 #{n} 還沒開始，取消了。"
        if rec["state"] != "running":
            raise Problem(f"🎬 工作 #{n} 已經結束了（{STATE_TEXT.get(rec['state'], rec['state'])}）。")
        runner = live.get((uid(user), n))
        if runner is None or not app.jobs.cancel(runner):
            raise Problem(f"🎬 停不掉工作 #{n}（可能剛好做完了），/v2n_status {n} 看一下。")
        return f"⏹ 停掉工作 #{n} 了（已經做好的檔案都留著，再做一次會沿用）。"

    def notes_of_job(user, n=None):
        data = load(user)
        if n is None:
            done = [r for r in data["jobs"] if r["state"] == "done"]
            if not done:
                raise Problem("🎬 還沒有做好的筆記。")
            rec = done[-1]
        else:
            rec = find(data, n)
            if rec is None:
                raise Problem(f"🎬 沒有工作 #{n}。")
        files = note_files(rec.get("result"))
        if not files:
            if rec["state"] in ACTIVE:
                raise Problem(f"🎬 工作 #{rec['id']} 還沒做完（/v2n_status {rec['id']} 看進度）。")
            raise Problem(f"🎬 工作 #{rec['id']} 沒有筆記（{STATE_TEXT.get(rec['state'], rec['state'])}）。")
        return [{"title": title, "markdown": read_note(path)} for title, path in files]

    def library(user, keyword=""):
        envelope = run_sync(user, ["--library"] + out_args(user), "library")
        if not envelope.get("ok"):
            err = error_summary(envelope.get("error"))
            raise Problem(f"🎬 讀不到筆記庫：{err['reason']}" + (f"\n怎麼辦：{err['action']}" if err["action"] else ""))
        data = envelope.get("data") or {}
        items = []
        for kind, rows in (("影片", data.get("videos") or []), ("合併", data.get("collections") or [])):
            for row in rows:
                if isinstance(row, dict) and row.get("folder"):
                    items.append({"type": kind, "title": str(row.get("title") or row.get("name") or "")[:200],
                                  "folder": str(row["folder"]), "notes": [str(s) for s in row.get("notes") or []][:20],
                                  "updated": row.get("updated") or 0})
        items.sort(key=lambda x: x["updated"], reverse=True)
        for i, it in enumerate(items, 1):
            it["n"] = i

        def keep(d):
            d["library"] = [{k: it[k] for k in ("n", "type", "title", "folder", "notes")} for it in items]
        change(user, keep)
        kw = (keyword or "").strip().lower()
        return [it for it in items if not kw or kw in it["title"].lower()]

    def library_item(user, n):
        cached = load(user)["library"]
        if not cached:
            library(user)
            cached = load(user)["library"]
        it = next((x for x in cached if x["n"] == n), None)
        if it is None:
            raise Problem(f"🎬 筆記庫沒有第 {n} 項（/v2n_list 重新列一次）。")
        return it

    def core_combine(user, values, chat_id=None):
        picks = [p for p in re.split(r"[\s,，、]+", values.get("items") or "") if p]
        if len(picks) < 2 or not all(p.isdigit() for p in picks):
            raise Problem("🎬 給筆記庫的編號，至少兩個，用逗號隔開（例如 1,2,3；/v2n_list 看編號）。")
        folders = []
        for p in dict.fromkeys(picks):
            folder = library_item(user, int(p))["folder"]
            if "," in folder:
                raise Problem(f"🎬 第 {p} 項的資料夾名稱有逗號，Video Notes 的合併分不開它；先在 Video Notes 裡合併。")
            folders.append(folder)
        argv = ["--combine", "--folders=" + ",".join(folders)]
        for key, flag in (("profile", "--profile"), ("preset", "--preset")):
            value = (values.get(key) or "").strip()
            if value:
                if not WORD_RE.match(value):
                    raise Problem(f"🎬 {key} 只能有英數字與 _ + -。")
                argv.append(f"{flag}={value}")
        title = " ".join((values.get("title") or "").split())[:100]
        if title:
            argv.append(f"--title={title}")
        if (values.get("yes") or "").strip() == "是":
            argv.append("--yes")
        argv += out_args(user) + ["--json"]
        return submit(user, "combine", argv, f"合併 {len(folders)} 支" + (f"：{title}" if title else ""), chat_id)

    # ── 指令 ──

    def say(api, chat_id, text):
        for part in chunks(text):
            api.send_message(chat_id, part)

    def guarded(fn):
        def run(api, chat_id, user, values):
            try:
                fn(api, chat_id, user, values)
            except Problem as p:
                api.send_message(chat_id, p.text)
        return run

    def slow(fn):
        """會等子程序的指令：經 AI 呼叫時照常同步（結果要交回 AI）；人打的丟背景，不卡住收訊。"""
        def run(api, chat_id, user, values):
            if getattr(api, "via_ai", False) is True or getattr(api, "sync_result", False) is True:
                fn(api, chat_id, user, values)
            else:
                app.tasks.run(lambda: fn(api, chat_id, user, values), name=f"pack-{pack.name}")
        return run

    def number(values, key):
        raw = (values.get(key) or "").strip().lstrip("#")
        if not raw:
            return None
        if not raw.isdigit():
            raise Problem(f"🎬 {key} 要是數字。")
        return int(raw)

    def cmd_add(api, chat_id, user, values):
        n = core_add(user, values, chat_id)
        api.send_message(chat_id, f"⏳ 工作 #{n} 排進去了，做完通知你。進度：/v2n_status {n}")

    def cmd_status(api, chat_id, user, values):
        say(api, chat_id, core_status(user, number(values, "n")))

    def cmd_get(api, chat_id, user, values):
        item = number(values, "item")
        if item is not None:
            it = library_item(user, item)
            path = latest_note(it["folder"], (values.get("slug") or "").strip())
            notes = [{"title": it["title"], "markdown": read_note(path)}]
        else:
            notes = notes_of_job(user, number(values, "n"))
        for note in notes:
            say(api, chat_id, f"📝 {note['title']}\n\n{note['markdown']}")

    def cmd_list(api, chat_id, user, values):
        items = library(user, values.get("keyword", ""))
        if not items:
            api.send_message(chat_id, "🎬 筆記庫是空的（或沒有符合的）。")
            return
        lines = [f"🎬 筆記庫（{len(items)} 項；/v2n_get item=<編號> 拿筆記、/v2n_combine 1,2 合併）"]
        for it in items[:30]:
            slugs = "、".join(it["notes"]) or "還沒有筆記"
            lines.append(f"{it['n']}. [{it['type']}] {it['title']}（{slugs}）")
        if len(items) > 30:
            lines.append(f"…還有 {len(items) - 30} 項，加關鍵字縮小範圍。")
        say(api, chat_id, "\n".join(lines))

    def cmd_combine(api, chat_id, user, values):
        n = core_combine(user, values, chat_id)
        api.send_message(chat_id, f"⏳ 合併排進去了（工作 #{n}），做完通知你。")

    def cmd_cancel(api, chat_id, user, values):
        api.send_message(chat_id, core_cancel(user, number(values, "n")))

    def cmd_setup(api, chat_id, user, values):
        kit.require_human()
        updates = {}
        for key in ("program", "python"):
            if key in values:
                value = values[key].strip().strip('"')
                if value in ("-", "清除", "none"):
                    value = ""
                if value and not os.path.exists(value):
                    raise Problem(f"🎬 找不到：{value}")
                updates[key] = value
        if "out" in values:
            value = values["out"].strip().strip('"')
            if value and value not in OUT_WORDS and not os.path.isabs(value):
                raise Problem("🎬 out 要是 downloads、data，或一個完整的資料夾路徑（留空＝用 Video Notes 自己的設定）。")
            updates["out"] = "" if value in ("-", "清除", "none") else value
        if "timeout" in values:
            value = values["timeout"].strip()
            if not value.isdigit() or not 5 <= int(value) <= 24 * 60:
                raise Problem("🎬 timeout 是分鐘數，5 到 1440。")
            updates["timeout"] = value
        if updates:
            change(user, lambda d: d["settings"].update(updates))
        s = settings(user)
        lines = ["🎬 影片筆記的設定" + ("（改好了）" if updates else "") + "：",
                 f"  program：{s.get('program') or '（還沒設定）'}",
                 f"  python：{s.get('python') or '（自動：用它旁邊的 .venv）'}",
                 f"  out：{s.get('out') or '（用 Video Notes 自己的設定）'}",
                 f"  timeout：{s.get('timeout') or DEFAULT_TIMEOUT_MIN} 分鐘"]
        try:
            resolve_program(s)
            lines.append("  ✅ 找得到 Video Notes。用 /v2n_check 檢查它的環境（ffmpeg、AI…）。")
        except Problem as p:
            lines.append(p.text)
        api.send_message(chat_id, "\n".join(lines))

    def cmd_check(api, chat_id, user, values):
        envelope = run_sync(user, ["--check"], "check")
        data = envelope.get("data") or {}
        checks = data.get("checks") or []
        bad = [c for c in checks if isinstance(c, dict) and c.get("level") in ("error", "warn")]
        head = "✅ Video Notes 可以用" if envelope.get("ok") else "❌ Video Notes 有問題"
        lines = [f"🎬 {head}（檢查了 {len(checks)} 項）"]
        for c in bad[:15]:
            mark = "❌" if c.get("level") == "error" else "⚠️"
            lines.append(f"{mark} {c.get('name', '')}：{str(c.get('message', ''))[:200]}")
        if not envelope.get("ok") and envelope.get("error"):
            err = error_summary(envelope["error"])
            if err["action"]:
                lines.append(f"怎麼辦：{err['action']}")
        say(api, chat_id, "\n".join(lines))

    url_field = Field("urls", "影片網址（好幾支用空白隔開）", required=True)
    add_fields = [url_field,
                  Field("profile", "整理方式：auto、knowledge、news、podcast、quick、finance、general（或你自訂的）"),
                  Field("with", "加料，逗號隔開：timeline、actions、quotes、glossary、brief、detailed、english"),
                  Field("preset", "常用組合（例如 news-roundup、course、daily-news）"),
                  Field("focus", "特別想知道的主題"),
                  Field("outputs", "額外的輸出形式，逗號隔開（例如 tree-md,html-tabs）"),
                  Field("redo", "要重做的階段：download、transcribe、notes 或 all（不給＝沿用做過的）")]
    kit.command(g, "add", guarded(cmd_add), "影片做筆記：把影片網址交給 Video Notes 下載、轉逐字稿、整理成筆記（背景做，做完通知）",
                default=True, effect=WRITE, fields=add_fields, positional=1)
    kit.command(g, "status", guarded(cmd_status), "看進度：最近的工作，或某個工作做到哪裡",
                fields=[Field("n", "工作編號")])
    kit.command(g, "get", slow(guarded(cmd_get)), "拿筆記：某個工作（或筆記庫某一項）的 Markdown 筆記全文",
                fields=[Field("n", "工作編號（不給＝最新做好的）"), Field("item", "筆記庫的編號（/v2n_list）"),
                        Field("slug", "哪一份（同一支影片有好幾種整理方式時）")], positional=1)
    kit.command(g, "list", slow(guarded(cmd_list)), "列出筆記庫：已經整理過的影片與合併（編號給 get、combine 用）",
                fields=[Field("keyword", "標題關鍵字")])
    # DANGER：合併會花 AI 額度，AI 想叫它時 SYNOX 一律先送確認卡給你按（跟 Video Notes 自己的「規模大要確認」無關）；
    # 你自己打的照常直接做，工具畫面上會先跳確認框。
    kit.command(g, "combine", guarded(cmd_combine), "合併多支：把筆記庫裡好幾支影片的分析合併成一份筆記（背景做，會用到 AI）",
                effect=DANGER, positional=1,
                fields=[Field("items", "筆記庫的編號，至少兩個，逗號隔開", required=True), Field("title", "合併後的標題"),
                        Field("preset", "常用組合（例如 news-roundup、course）"), Field("profile", "整理方式"),
                        Field("yes", "規模很大也照做", choices=("是", "否"))])
    kit.command(g, "cancel", guarded(cmd_cancel), "停掉一個還在做或排隊的工作（做好的檔案留著）", effect=WRITE,
                fields=[Field("n", "工作編號（不給＝最新的那個）")])
    kit.command(g, "setup", guarded(cmd_setup), "設定 Video Notes 裝在哪裡（只有你本人能改）", effect=WRITE, ai=False,
                fields=[Field("program", "VideoNotes 的 exe、VideoNotes.py 或工具資料夾"),
                        Field("python", "要用哪個 Python（只有 VideoNotes.py 需要；留空＝它旁邊的 .venv）"),
                        Field("out", "筆記輸出在哪：downloads、data 或資料夾路徑（留空＝Video Notes 自己的設定）"),
                        Field("timeout", "一個工作最多跑幾分鐘")], positional=0)
    kit.command(g, "check", slow(guarded(cmd_check)), "檢查 Video Notes 能不能用（ffmpeg、JS 環境、yt-dlp、AI 金鑰有沒有設…，不連網）")

    # ── 畫面（web.app）：讀與動作，跟指令同一份核心 ──

    def app_call(fn):
        def run(*args):
            try:
                return fn(*args)
            except Problem as p:
                raise kit.AppError(p.text) from None
        return run

    def read_jobs(user, params):
        mark_interrupted(user)
        pump(user)
        try:
            resolve_program(settings(user))
            ready, hint = True, ""
        except Problem as p:
            ready, hint = False, p.text
        rows = []
        for rec in load(user)["jobs"][-20:][::-1]:
            row = {"id": rec["id"], "state": rec["state"], "state_text": STATE_TEXT.get(rec["state"], rec["state"]),
                   "label": rec.get("label", ""), "created": rec.get("created"), "error": rec.get("error"),
                   "progress": []}
            if rec["state"] == "running":
                row["progress"] = progress_of(read_log(log_path(user, str(rec["id"]))))
            rows.append(row)
        return {"ready": ready, "hint": hint, "jobs": rows}

    def read_job(user, params):
        n = int(params.get("id") or 0) if str(params.get("id") or "").isdigit() else 0
        rec = find(load(user), n)
        if rec is None:
            raise Problem("🎬 沒有這個工作。")
        notes = []
        if rec["state"] == "done":
            try:
                notes = notes_of_job(user, n)
            except Problem as p:
                notes = [{"title": "（讀不到筆記）", "markdown": p.text}]
        return {"id": n, "detail": job_detail(user, rec), "state": rec["state"], "notes": notes}

    def read_library(user, params):
        return {"items": library(user, params.get("keyword", ""))}

    def read_one_note(user, params):
        raw = str(params.get("item") or "")
        if not raw.isdigit():
            raise Problem("🎬 要給筆記庫的編號。")
        it = library_item(user, int(raw))
        path = latest_note(it["folder"], (params.get("slug") or "").strip())
        return {"title": it["title"], "markdown": read_note(path)}

    if hasattr(pack, "app_read"):                    # 舊版 SYNOX 沒有工具畫面：指令照樣能用
        pack.app_read("jobs", app_call(read_jobs))
        pack.app_read("job", app_call(read_job))
        pack.app_read("library", app_call(read_library))
        pack.app_read("note", app_call(read_one_note))
        pack.app_action("add", app_call(lambda user, values: {"job": core_add(user, values, uid(user))}))
        pack.app_action("cancel", app_call(lambda user, values: {"message": core_cancel(user, number(values, "n"))}))
        pack.app_action("combine", app_call(lambda user, values: {"job": core_combine(user, values, uid(user))}))
