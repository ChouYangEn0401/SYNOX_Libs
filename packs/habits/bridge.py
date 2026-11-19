"""SYNOX 的指令與畫面 ↔ engine：一份指令表同時給「人打字／AI」和「畫面的動作」。

    COMMANDS   Cmd(name, summary, effect, fields, run, app, default, describe, ai)
    READS      畫面的讀（pack.json web.app.reads）

run(values) -> (text, data)：指令只回 text，畫面動作只回 data（一定是 dict）。
每次呼叫都綁定「這個人」的資料（storage.bound），讀改寫整段持這個人的鎖。
引擎丟的 ValueError／KeyError／LookupError／IndexError 轉成 kit.AppError（訊息原樣給人看）；其他的錯交給 SYNOX。
"""
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import Callable, Optional, Sequence, Tuple, Union

from isdtelekit.core.registry import DANGER, READ, WRITE, Field  # noqa: F401

from synox.packs import kit

from .engine import clock, errors, milestones, routines, storage

CLEAR = "-"
_WEEKDAY_CHARS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


class StoreBackend:
    """engine.storage 要的 load／save，落在這個人的 pack.store（保管箱，密文）；一份文件一個 key。"""

    def __init__(self, store):
        self.store = store

    def load(self, name, default=None):
        return self.store.load(default, key=name)

    def save(self, name, data):
        self.store.save(data, key=name)


@dataclass(frozen=True)
class Cmd:
    name: str
    summary: str
    effect: str
    fields: Sequence[Field]
    run: Callable
    app: bool = False
    default: bool = False
    ai: Union[bool, dict] = False
    describe: Optional[Callable] = None


def frozen(d):
    return MappingProxyType(dict(d))


def _store_of(pack, user):
    return pack.store.of(user)


class Session:
    """with open_session(pack, user): ……（一個人一把鎖；pack.store.of(user).lock 是 RLock，可以重入）。"""

    def __init__(self, pack, user):
        self.store = _store_of(pack, user)
        self.lock = getattr(self.store, "lock", None)           # 真的保管箱有；測試的假保管箱沒有（單執行緒，不需要）

    def __enter__(self):
        if self.lock:
            self.lock.acquire()
        self._bound = storage.bound(StoreBackend(self.store))
        self._bound.__enter__()
        return self

    def __exit__(self, *exc):
        try:
            self._bound.__exit__(*exc)
        finally:
            if self.lock:
                self.lock.release()
        return False


def as_app_error(e: Exception) -> Optional[kit.AppError]:
    """kit.AppError 原樣；引擎的「說不行」（engine/errors.py 那幾種）轉成 AppError；其他回 None（交給 SYNOX，訊息不外流）。"""
    if isinstance(e, kit.AppError):
        return e
    if isinstance(e, errors.NotFound):
        return kit.AppError("找不到這一筆", code="not_found", status=404)
    if isinstance(e, errors.NoCell):
        return kit.AppError("沒有這一格", code="not_found", status=404)
    if isinstance(e, (errors.Refused, errors.NoMatch)):
        return kit.AppError(str(e), code="refused", status=409)
    return None


# ── 欄位怎麼解讀 ────────────────────────────────────────────────────────────────

def yes(text) -> bool:
    return str(text or "").strip().lower() in ("yes", "y", "true", "1", "on", "是", "好")


def _day(text: str, empty_ok=True) -> str:
    """今天／昨天／前天、-1、YYYY-MM-DD → ISO；空＝空（引擎用今天）。"""
    t = str(text or "").strip()
    if not t:
        return ""
    words = {"今天": 0, "昨天": 1, "前天": 2}
    if t in words:
        return (clock.today() - timedelta(days=words[t])).isoformat()
    if re.fullmatch(r"-\d{1,3}", t):
        return (clock.today() + timedelta(days=int(t))).isoformat()
    try:
        return date.fromisoformat(t).isoformat()
    except ValueError:
        raise kit.AppError(f"看不懂日期「{t}」（寫 2030-01-31、今天、昨天或 -1）") from None


def _end_date(text: str) -> str:
    """結束日：+30d（從今天起 30 天）或 YYYY-MM-DD。"""
    t = str(text or "").strip()
    m = re.fullmatch(r"\+(\d{1,4})d?", t)
    if m:
        return (clock.today() + timedelta(days=int(m.group(1)))).isoformat()
    return _day(t)


def _weekdays(text: str) -> list:
    out = set()
    for ch in str(text or ""):
        if ch in "1234567":
            out.add(int(ch) - 1)
        elif ch in _WEEKDAY_CHARS:
            out.add(_WEEKDAY_CHARS[ch])
    return sorted(out)


def check_day(v: str) -> Optional[str]:
    try:
        _day(v)
    except kit.AppError as e:
        return e.message
    return None


def check_positive(v: str) -> Optional[str]:
    return None if str(v).strip().isdigit() and int(v) >= 1 else "要是 1 以上的整數"


def check_choice(*names):
    def check(v):
        return None if str(v).strip().lower() in names else "只能是：" + "、".join(names)
    return check


def check_json(v: str) -> Optional[str]:
    try:
        return None if isinstance(json.loads(v), dict) else "要是 JSON 物件"
    except ValueError:
        return "不是正確的 JSON"


def _payload(values) -> dict:
    return json.loads(values["json"])


# ── 找人／找旅程 ────────────────────────────────────────────────────────────────

def resolve_routine(ref: str) -> dict:
    """id 或名字 → 一條（習慣或提醒）的 view。習慣照 find_habit 的規矩，提醒用 id 或完全一樣的名字。"""
    ref = str(ref or "").strip()
    try:
        return routines.find_habit(ref)
    except LookupError as e:
        rows = routines.list_routines()
        hit = [r for r in rows if r["id"] == ref] or [r for r in rows if r["name"].strip().lower() == ref.lower()]
        if len(hit) == 1:
            return hit[0]
        if len(hit) > 1:
            raise errors.NoMatch(f"「{ref}」對到不只一個，給 id") from None
        raise e


def resolve_journey(ref: str) -> dict:
    ref = str(ref or "").strip()
    rows = milestones.list_all()
    hit = [r for r in rows if r["id"] == ref] or [r for r in rows if r["name"].strip().lower() == ref.lower()]
    if not hit:
        hit = [r for r in rows if ref and ref.lower() in r["name"].lower()]
    if len(hit) == 1:
        return milestones.get(hit[0]["id"])
    if not hit:
        raise errors.NoMatch(f"找不到旅程「{ref}」")
    raise errors.NoMatch("對到不只一個旅程：" + "、".join(f"「{r['name']}」（id {r['id']}）" for r in hit[:6]) + "——給 id")


# ── 回覆怎麼排 ──────────────────────────────────────────────────────────────────

def habit_line(v: dict) -> str:
    q = v.get("quota")
    if q:
        status = "🎉" if q.get("met") else "⬜"
        prog = q.get("progress_text") or f"{q.get('label', '')} {q.get('count', 0)}/{q.get('times', 0)}"
    else:
        status = {"done": "✅", "cancel": "⏭️"}.get(v.get("today_state"), "⬜")
        prog = v.get("desc", "")
    tail = ""
    if v.get("paused"):
        tail = "　⏸ 暫停中"
    elif v.get("enough_now"):
        tail = "　🙆 這期夠了"
    elif v.get("kind") == "habit" and v.get("locked"):
        tail = "　🔒"
    elif v.get("not_started"):
        tail = f"　⏳ {v.get('days_to_start')} 天後開始"
    return f"{status} {v['name']}（{prog}）[{v['id']}]{tail}"


def journey_line(j: dict) -> str:
    return f"{j['emoji']} {j['name']}　{j['done']}/{j['total']}（{j['pct']}%）　[{j['id']}]" + (
        f"　下一步：{j['current']}" if j.get("current") else "")


def journey_text(j: dict) -> str:
    s = j["stats"]
    rows = [f"{j['emoji']} {j['name']}　{s['done']}/{s['total']}（{s['pct']}%）・{s['level_name']}・連續 {s['streak']}"
            + (f"\n目標：{j['goal']}" if j["goal"] else "")]
    for i, c in enumerate(j["cells"]):
        if c["type"] == "rest":
            rows.append(f"　{c['date']}　{c.get('sticker', '💤')} {c.get('label', '休息')}")
            continue
        mark = "✅" if c["state"] == "done" else ("👉" if c.get("is_current") else "⬜")
        no = f"{c['n']}" if c["type"] == "task" else "＋"
        rows.append(f"　{c['date']}　{mark} {no}. {c.get('title', '')}")
    return "\n".join(rows)


# ── 指令：習慣 ──────────────────────────────────────────────────────────────────

def run_add(values):
    kind = (values.get("kind") or "habit").strip().lower()
    item = {"name": values["name"].strip(), "kind": kind, "repeat": (values.get("repeat") or "daily").strip().lower()}
    if values.get("days"):
        item["weekdays"] = _weekdays(values["days"])
    if values.get("every"):
        item["every_days"] = int(values["every"])
    if values.get("period"):
        item["period"] = values["period"].strip().lower()
    if values.get("minutes"):
        item.update({"unit": "minutes", "floor_min": values["minutes"], "repeat": "quota"})
    elif values.get("times"):
        item["times"] = int(values["times"])
    if values.get("why"):
        item["why"] = values["why"]
    at = (values.get("at") or "").strip()
    if at.lower() in ("none", "no", "off", "不要"):
        item["at"] = ""
    elif at:
        item["at"] = at
    elif kind == "habit":
        item["at"] = routines.habit_default_at()
    if kind == "habit":
        item["end_date"] = _end_date(values.get("end") or "") or (clock.today() + timedelta(days=30)).isoformat()
    v = routines.upsert(item)
    extra = f"，每天 {v['at']} 提醒" if v.get("at") else ""
    return f"✅ 加好了：{habit_line(v)}{extra}", {"routine": v}


def run_save(values):
    """畫面的編輯器：json 是整張表單（id 有＝改、沒有＝新增）。承諾期內改期、刪除要 force（畫面問完三道確認才送）。"""
    item = _payload(values)
    if "weekdays" in item and isinstance(item["weekdays"], str):
        item["weekdays"] = _weekdays(item["weekdays"])
    v = routines.upsert(item)
    return f"✅ 存好了：{habit_line(v)}", {"routine": v}


def run_list(values):
    which = (values.get("filter") or "").strip().lower()
    rows = routines.list_routines()
    if which in ("reminder", "reminders", "提醒"):
        rows = [r for r in rows if r["kind"] == "reminder"]
        title = "提醒"
    elif which == "all":
        title = "全部"
    elif which in ("ended", "done", "結束"):
        rows = [r for r in rows if r["kind"] == "habit" and r["period_ended"]]
        title = "已經結束的習慣"
    else:
        rows = [r for r in rows if r["kind"] == "habit" and not r["period_ended"]]
        title = "習慣"
    if not rows:
        return f"🎯 {title}：沒有。用 /habit 讀書 加一個。", {"routines": []}
    return "\n".join([f"🎯 {title}（{len(rows)}）"] + [habit_line(r) for r in rows]), {"routines": rows}


def run_today(values):
    rows = [r for r in routines.list_routines()
            if r["due_today"] and not r["paused"] and not r["enough_now"] and not r["period_ended"]
            and not r["not_started"]]
    done = [r for r in routines.list_routines()
            if r["kind"] == "habit" and r["done_today"] and not r["period_ended"]]
    lines = ["🎯 今天"] + ([habit_line(r) for r in rows] or ["沒有還沒做的。"])
    if done:
        lines += ["做過的："] + [habit_line(r) for r in done]
    return "\n".join(lines), {"due": rows, "done": done}


def run_check(values):
    target = resolve_routine(values["ref"])
    state = (values.get("state") or "done").strip().lower()
    v = routines.check(target["id"], _day(values.get("day")), state)
    return f"✅ {habit_line(v)}", {"routine": v}


def run_log(values):
    target = resolve_routine(values["ref"])
    v = routines.log_minutes(target["id"], values["minutes"], _day(values.get("day")))
    return (f"✅ 記了 {routines.fmt_min(abs(v['logged']))}（那天共 {routines.fmt_min(v['day_minutes'])}）\n"
            + (v["quota"].get("say") or habit_line(v))), {"routine": v}


def run_pause(values):
    target = resolve_routine(values["ref"])
    on = not yes(values.get("off"))
    v = routines.pause(target["id"], on, values.get("until") or "")
    return ("⏸ 暫停了" + (f"，{v['paused_until']} 回來" if v["paused_until"] else "，自己說回來才回來")
            if on else "▶️ 接著做了"), {"routine": v}


def run_enough(values):
    target = resolve_routine(values["ref"])
    on = not yes(values.get("off"))
    v = routines.set_enough(target["id"], on)
    return ("🙆 這一期夠了，到 " + v["enough_until"] + " 都不再提醒" if on else "這一期又要做了"), {"routine": v}


def run_pin(values):
    target = resolve_routine(values["ref"])
    v = routines.pin(target["id"], not yes(values.get("off")))
    return ("📌 釘上去了" if v["pinned"] else "取消釘選"), {"routine": v}


def run_rm(values):
    target = resolve_routine(values["ref"])
    force = yes(values.get("force"))
    try:
        routines.delete(target["id"], force=force)
    except ValueError as e:
        steps = routines.insist_steps(target["name"], "刪除")
        raise kit.AppError(f"{e}　要刪的話再確認一次：{steps[0]['body']}　確定就加 force=yes。",
                           code="insist", status=409) from None
    return f"🗑️ 刪掉了「{target['name']}」", {"deleted": target["id"]}


def describe_rm(values):
    try:
        target = resolve_routine(values["ref"])
    except LookupError:
        return "刪掉這一條"
    return f"刪掉「{target['name']}」" + (f"（承諾期到 {target['end_date']}，還沒結束）" if target.get("locked") else "")


def run_notified(values):
    target = resolve_routine(values["ref"])
    return "✅ 記下今天叫過了", {"ok": routines.mark_notified(target["id"])}


def run_display(values):
    on = routines.set_show_missed_red(yes(values.get("red")))
    return ("沒做的格子會畫紅色" if on else "沒做的格子不畫紅色（中性色＋文字）"), {"show_missed_red": on}


# ── 指令：里程碑旅程 ────────────────────────────────────────────────────────────

def run_ms_list(values):
    rows = milestones.list_all()
    if not rows:
        return "🗺️ 還沒有旅程。用 /habit_ms_new 名字 步驟1;步驟2 開一個。", {"milestones": []}
    return "\n".join([f"🗺️ 旅程（{len(rows)}）"] + [journey_line(r) for r in rows]), {"milestones": rows}


def run_ms_show(values):
    j = resolve_journey(values["ref"])
    return journey_text(j), {"milestone": j}


def run_ms_new(values):
    steps = [s.strip() for s in re.split(r"[;；\n]", values.get("steps") or "") if s.strip()]
    payload = {"name": values["name"], "goal": values.get("goal", ""), "plan": steps}
    if values.get("start"):
        payload["start_date"] = _day(values["start"])
    if values.get("cadence"):
        payload["cadence"] = values["cadence"].strip().lower()
    j = milestones.create(payload)
    return f"🗺️ 開始了：{j['emoji']} {j['name']}（{j['stats']['total']} 步）[{j['id']}]", {"milestone": j}


def run_ms_done(values):
    j = resolve_journey(values["ref"])
    n = int(values["n"])
    idx = next((i for i, c in enumerate(j["cells"]) if c["type"] == "task" and c.get("n") == n), None)
    if idx is None:
        raise kit.AppError(f"沒有第 {n} 步（這個旅程有 {j['stats']['total']} 步）")
    j = milestones.complete(j["id"], idx, not yes(values.get("undo")))
    s = j["stats"]
    return f"{'↩️ 取消完成' if yes(values.get('undo')) else '✅ 完成第 ' + str(n) + ' 步'}　{s['done']}/{s['total']}・{s['level_name']}", {"milestone": j}


def run_ms_miss(values):
    """今天沒做：不算失敗，在目前這一步前面插一格休息（或臨時的事），後面順延。"""
    j = resolve_journey(values["ref"])
    cur = next((i for i, c in enumerate(j["cells"]) if c.get("is_current")), len(j["cells"]))
    kind = (values.get("kind") or "rest").strip().lower()
    payload = {"label": values.get("label") or "休息", "title": values.get("label") or "臨時的事"}
    if values.get("sticker"):
        payload["sticker"] = values["sticker"]
    where = "before" if cur < len(j["cells"]) else "after"
    j = milestones.insert(j["id"], min(cur, max(0, len(j["cells"]) - 1)), where, kind, payload)
    return f"{'🍜' if kind == 'rest' else '⚡'} 記下了，後面的步驟順延一天（不是失敗）", {"milestone": j}


def run_ms_note(values):
    j = resolve_journey(values["ref"])
    n = int(values["n"])
    idx = next((i for i, c in enumerate(j["cells"]) if c["type"] == "task" and c.get("n") == n), None)
    if idx is None:
        raise kit.AppError(f"沒有第 {n} 步")
    payload = {}
    if values.get("mood"):
        payload["mood"] = values["mood"]
    if values.get("note"):
        payload["add_note"] = values["note"]
    j = milestones.edit_cell(j["id"], idx, payload)
    return "📝 記下了", {"milestone": j}


def run_ms_rm(values):
    j = resolve_journey(values["ref"])
    milestones.delete(j["id"])
    return f"🗑️ 刪掉了旅程「{j['name']}」", {"deleted": j["id"]}


def describe_ms_rm(values):
    try:
        return f"刪掉旅程「{resolve_journey(values['ref'])['name']}」和它所有的紀錄"
    except LookupError:
        return "刪掉這個旅程"


# 畫面專用（用 id 與格子位置，不用名字與步驟編號）
def run_ms_save(values):
    p = _payload(values)
    mid = str(p.pop("id", "") or "")
    j = milestones.update(mid, p) if mid else milestones.create(p)
    return "✅ 存好了", {"milestone": j}


def run_ms_complete(values):
    j = milestones.complete(values["id"], int(values["idx"]), not yes(values.get("undo")))
    return "✅", {"milestone": j}


def run_ms_insert(values):
    payload = {k: values[k] for k in ("title", "desc", "label", "sticker") if values.get(k)}
    if yes(values.get("blueprint")):
        payload["blueprint"] = True
    j = milestones.insert(values["id"], int(values["idx"]), (values.get("where") or "after"), values["kind"], payload)
    return "✅", {"milestone": j}


def run_ms_edit(values):
    payload = {}
    if values.get("mood"):
        payload["mood"] = values["mood"]
    if values.get("note"):
        payload["add_note"] = values["note"]
    j = milestones.edit_cell(values["id"], int(values["idx"]), payload)
    return "✅", {"milestone": j}


def run_ms_remove(values):
    j = milestones.remove_cell(values["id"], int(values["idx"]))
    return "✅", {"milestone": j}


# ── 欄位 ────────────────────────────────────────────────────────────────────────

_REF = Field("ref", "哪一條（名字或 id）", required=True)
_JREF = Field("ref", "哪個旅程（名字或 id）", required=True)
_DAY = Field("day", "哪一天（今天、昨天、-1、2030-01-31；不寫＝今天）", check=check_day)
_STATES = ("done", "cancel", "none", "rep", "unrep")

COMMANDS = (
    Cmd("add", "加一個習慣（或提醒）", WRITE,
        [Field("name", "名字", required=True),
         Field("repeat", "週期：daily／weekly／monthly／days／quota", check=check_choice("daily", "weekly", "monthly", "days", "quota")),
         Field("days", "每週的星期幾（例：135 或 一三五；週一是 1）"),
         Field("every", "每幾天一次（repeat=days 用）", check=check_positive),
         Field("times", "一期做幾次（repeat=quota 用）", check=check_positive),
         Field("period", "一期是 week 還是 month", check=check_choice("week", "month")),
         Field("minutes", "一期花多少時間（例：90、1.5h；寫了就是時間配額）"),
         Field("end", "承諾到哪天（+30d 或 2030-01-31；不寫＝30 天）", check=lambda v: None if re.fullmatch(r"\+\d{1,4}d?", v.strip()) else check_day(v)),
         Field("at", "每天幾點提醒（20:30；none＝不提醒；不寫＝預設 21:00）"),
         Field("why", "為什麼要做（選填）"),
         Field("kind", "habit（想養成，預設）或 reminder（純提醒）", check=check_choice("habit", "reminder"))],
        run_add, default=True, ai={"scenario": "使用者想養成一個習慣、或要一個週期性的提醒時加一條"}),
    Cmd("save", "用完整表單新增或修改一條（畫面的編輯器）", WRITE,
        [Field("json", "整張表單（JSON）", required=True, check=check_json)], run_save, app=True),
    Cmd("list", "列出習慣（all／reminder／ended）", READ,
        [Field("filter", "all／reminder／ended（不寫＝進行中的習慣）")], run_list, ai={"scenario": "使用者問有哪些習慣、進度如何"}),
    Cmd("today", "今天還有什麼沒做", READ, [], run_today, ai={"scenario": "使用者問今天要做哪些習慣或提醒"}),
    Cmd("check", "打卡：done／cancel（跳過）／none（清掉）／rep（加碼＋1）／unrep（退一次）", WRITE,
        [_REF, Field("state", "狀態（不寫＝done）", check=check_choice(*_STATES)), _DAY], run_check, app=True,
        ai={"scenario": "使用者說某個習慣今天做了、要跳過或補打卡時"}),
    Cmd("log", "時間配額記時間（可以分幾次記；負的＝扣回來）", WRITE,
        [_REF, Field("minutes", "做了多久（45、1h、1.5h、-15）", required=True), _DAY], run_log, app=True,
        ai={"scenario": "使用者說某個用時間算的習慣做了多久"}),
    Cmd("pause", "暫停一條習慣（不算失敗）；off=yes 接著做", WRITE,
        [_REF, Field("until", "哪天回來（不寫＝自己說回來才回來）", check=check_day), Field("off", "yes＝接著做")],
        run_pause, app=True),
    Cmd("enough", "這一期夠了（剩下的日子不算沒做）；off=yes 收回", WRITE,
        [_REF, Field("off", "yes＝收回")], run_enough, app=True),
    Cmd("pin", "釘到最上面；off=yes 取消", WRITE, [_REF, Field("off", "yes＝取消釘選")], run_pin, app=True),
    Cmd("rm", "刪掉一條（承諾期內要 force=yes）", DANGER,
        [_REF, Field("force", "承諾期還沒結束也要刪（yes）")], run_rm, app=True, describe=describe_rm),
    Cmd("notified", "記下今天已經提醒過這一條", WRITE, [_REF], run_notified),
    Cmd("display", "沒做的格子要不要畫紅色（red=yes／no）", WRITE,
        [Field("red", "yes＝畫紅色、no＝不畫（預設不畫）", required=True, check=check_choice("yes", "no"))],
        run_display, app=True),
    Cmd("ms_list", "列出旅程", READ, [], run_ms_list, ai={"scenario": "使用者問有哪些里程碑旅程、走到哪"}),
    Cmd("ms_show", "看一個旅程每一步", READ, [_JREF], run_ms_show),
    Cmd("ms_new", "開一個旅程（步驟用 ; 分開）", WRITE,
        [Field("name", "旅程名字", required=True), Field("steps", "每一步（用 ; 分開）"), Field("goal", "目標（選填）"),
         Field("start", "從哪天開始（不寫＝今天）", check=check_day),
         Field("cadence", "daily（每天一步）或 weekly", check=check_choice("daily", "weekly"))],
        run_ms_new, ai={"scenario": "使用者要為一個有期限的目標排一段逐步的旅程"}),
    Cmd("ms_done", "完成旅程的第 n 步（undo=yes 取消）", WRITE,
        [_JREF, Field("n", "第幾步", required=True, check=check_positive), Field("undo", "yes＝取消完成")], run_ms_done,
        ai={"scenario": "使用者說旅程的某一步做完了"}),
    Cmd("ms_miss", "今天沒做：插一格休息（或臨時的事），後面順延", WRITE,
        [_JREF, Field("kind", "rest（預設）或 extra", check=check_choice("rest", "extra")),
         Field("label", "寫什麼（吃飯、生病…）"), Field("sticker", "貼圖（🍜😴…）")], run_ms_miss),
    Cmd("ms_note", "替一步記心情或加一條筆記", WRITE,
        [_JREF, Field("n", "第幾步", required=True, check=check_positive), Field("mood", "心情"), Field("note", "筆記")],
        run_ms_note),
    Cmd("ms_rm", "刪掉一個旅程", DANGER, [_JREF], run_ms_rm, app=True, describe=describe_ms_rm),
    Cmd("ms_save", "新增或修改旅程本身（畫面）", WRITE,
        [Field("json", "旅程（JSON；有 id＝改）", required=True, check=check_json)], run_ms_save, app=True),
    Cmd("ms_complete", "完成或取消完成一格（畫面）", WRITE,
        [Field("id", "旅程 id", required=True), Field("idx", "第幾格（從 0）", required=True), Field("undo", "yes＝取消完成")],
        run_ms_complete, app=True),
    Cmd("ms_insert", "插入一格（畫面）", WRITE,
        [Field("id", "旅程 id", required=True), Field("idx", "在第幾格旁邊（從 0）", required=True),
         Field("where", "before 或 after", check=check_choice("before", "after")),
         Field("kind", "rest／extra／task", required=True, check=check_choice("rest", "extra", "task")),
         Field("title", "標題"), Field("desc", "說明"), Field("label", "休息寫什麼"), Field("sticker", "貼圖"),
         Field("blueprint", "yes＝補進藍圖（不算插隊）")],
        run_ms_insert, app=True),
    Cmd("ms_edit", "替一格記心情或筆記（畫面）", WRITE,
        [Field("id", "旅程 id", required=True), Field("idx", "第幾格（從 0）", required=True), Field("mood", "心情"),
         Field("note", "筆記")], run_ms_edit, app=True),
    Cmd("ms_remove", "刪掉插進來的一格（畫面；排定的藍圖步驟刪不掉）", WRITE,
        [Field("id", "旅程 id", required=True), Field("idx", "第幾格（從 0）", required=True)], run_ms_remove, app=True),
)


# ── 畫面的讀 ────────────────────────────────────────────────────────────────────

def read_meta(params):
    return {**routines.meta(), **milestones.meta(), "today": clock.today().isoformat()}


def read_routines(params):
    return {"routines": routines.list_routines()}


def read_milestones(params):
    return {"milestones": milestones.list_all()}


def read_milestone(params):
    return milestones.get(str(params.get("id") or ""))


READS = frozen({"meta": read_meta, "routines": read_routines, "milestones": read_milestones,
                "milestone": read_milestone})


def positional_of(cmd: Cmd) -> int:
    """打字時照位置填的欄位數：add 只有名字；其餘照必填的個數（沒有必填的＝0，後面的欄位用 key=value 寫）。"""
    if cmd.name == "add":
        return 1
    return sum(1 for f in cmd.fields if f.required)


# 同樣的名字不能在指令表出現兩次
assert len({c.name for c in COMMANDS}) == len(COMMANDS)
assert sum(1 for c in COMMANDS if c.default) == 1
assert all(len(c.fields) <= 20 for c in COMMANDS)
