"""習慣提醒與養成：週期性的追蹤（資料只存在自己的資料夾、使用者自己填，不觀察任何東西）。

兩種 `kind`（旅程另有 milestones.py）：
- `reminder` 純提醒（倒垃圾、吃藥）：會重複的提醒，響過就好，不追蹤。
- `habit` 想養成：打卡＋統計。三態打卡：完成／跳過今天（自己選的，不是失敗）／空；承諾期內不可改、不可刪；
  補打卡四模式（none／h12／d3／always）；加碼模式（reps）；配額（一期幾次、哪天做都行）；時間配額（一期幾分鐘）。

心意：這不是監工。錯過只是記錄一筆事實，不是蓋「失敗」的章；漏一次不催第二句。

資料讀寫經 storage.py（呼叫的那一層決定存在哪），這個檔不碰任何路徑。"""
from __future__ import annotations

import calendar
import json
import re
import uuid
from datetime import date, datetime, time, timedelta

from . import clock, links, storage

DOC = "routines"        # storage 裡這份資料的名字


# 週期種類。`days` 是舊版唯一的模式（每 N 天），保留著讓舊資料原地可用。
# `quota` 是第五種，跟前面四種**方向相反**：前四種都在回答「哪一天要做」，
# 配額回答的是「這一期要做幾次」——哪天做由你決定。
REPEATS = [
    {"id": "daily", "label": "每天", "hint": "例如 09:00 吃藥"},
    {"id": "weekly", "label": "每週", "hint": "挑星期幾，例如 週五 洗衣服"},
    {"id": "monthly", "label": "每月", "hint": "第幾個星期幾，例如 每月第一個週一"},
    {"id": "days", "label": "每 N 天", "hint": "上次做完之後隔幾天，例如 每 14 天清書籤"},
    {"id": "quota", "label": "一期幾次", "hint": "這週做 3 次，哪天都行——做滿就收工"},
]
REPEAT_IDS = {r["id"] for r in REPEATS}
WEEKDAY_LABEL = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
NTH_LABEL = {1: "第一個", 2: "第二個", 3: "第三個", 4: "第四個", -1: "最後一個"}

# 追蹤器種類（第三種 milestone 另有 store，不在這裡）
KINDS = [
    {"id": "reminder", "label": "純提醒", "hint": "響過就好，不追蹤（倒垃圾、吃藥）"},
    {"id": "habit", "label": "想養成", "hint": "打卡＋統計，建立習慣（靈修、看盤訓練）"},
]
KIND_IDS = {k["id"] for k in KINDS}

# ---------------------------------------------------------------- habit 打卡方式
# 加碼模式：做幾次按幾次，多做的都算加分（例如「讀書 10 分鐘」可以反覆打卡）。
#
# **為什麼這是對的**：二元打卡有一個天生的缺陷——你做完第一次之後，這個工具就再也
# 給不出任何東西了，動力當場封頂。而「困難討厭的事」真正的行為改變發生在第二、三次。
# 加碼把那段變成看得見的。
#
# **三條硬規則**（沒有它們，這個功能會反過來變成羞辱機器）：
#   1. **底線永遠是 1 次，加碼沒有目標值**。一旦能設「每天 3 次」，做 2 次就變成失敗。
#      棘輪效應：昨天的加碼會偷偷變成今天的最低標。所以這裡**沒有** target 欄位。
#   2. **只呈現累積，永不做「昨天 3 次／今天 1 次」的對比**。對比是成績單，累積才是鏡子。
#   3. **不做第二條連鎖**（「連續加碼 5 天」）——那是第二個會斷掉的東西。
#
# 實作上它是**純加法**：第 1 次照樣寫進 `done_dates`，所以連鎖／完成率／矩陣／補打卡窗
# 全部一行都不用改，舊資料零遷移（沒有 `mode` ＝ `once` ＝ 現在的行為）。
HABIT_MODES = [
    {"id": "once", "label": "一次就好", "hint": "今天做到就打一次卡（原本的方式）"},
    {"id": "reps", "label": "可以加碼", "hint": "做幾次按幾次，多做的都算加分——適合「讀書 10 分鐘」這種小事"},
]
HABIT_MODE_IDS = {m["id"] for m in HABIT_MODES}
_HABIT_MODE_DEFAULT = "once"

# 補打卡四模式：讓使用者依「這個習慣要多逼自己」自由選寬鬆度。
#   none   不可補   —— 只能當天打，過了今天就補不回來（最嚴格的訓練期）
#   h12    12 小時  —— 該日結束後再給 12 小時補打卡（原本的「嚴格」）
#   d3     3 天內   —— 3 天滾動視窗回頭補（原本的「溫柔」）
#   always 隨時可補 —— 任何時候都能補回過去的打卡（最寬鬆）
# 「顯示轉紅」跟「還能不能補」是分開的兩件事——排定日沒打卡就會誠實轉紅（見 _red_after / _day_status），
# 差別只在紅了之後還點不點得動（見 _makeup_deadline）。舊資料 strict→h12、gentle→d3（見 _migrate）。
BACKFILL_MODES = [
    {"id": "none",   "label": "不可補打卡", "hint": "只能當天打卡，過了今天就補不回來（最嚴格的訓練期）"},
    {"id": "h12",    "label": "12 小時內",  "hint": "該日結束後 12 小時內都能補打卡"},
    {"id": "d3",     "label": "3 天內",     "hint": "3 天內都能回頭補打卡，怕忘記但不想中斷養成"},
    {"id": "always", "label": "隨時可補",   "hint": "任何時候都能補回過去的打卡，最寬鬆"},
]
BACKFILL_MODE_IDS = {m["id"] for m in BACKFILL_MODES}
_BACKFILL_DEFAULT = "h12"
# 舊模式名稱 → 新四模式（原地升級，不動使用者的其他資料）
_BACKFILL_ALIAS = {"strict": "h12", "gentle": "d3"}

# ---------------------------------------------------------------- 配額模式（repeat="quota"）
# 配額模式：承諾的是「一期做幾次」，哪天做不限，做滿就收工。
#
# 前四種週期都在回答「哪一天要做」；配額回答「這一期要做幾次」。它補的洞是：
# 現有模型答得出「今天該不該做」，答不出「**我這期做夠了沒**」——而成人的真實承諾
# 大多是後者（健身 3 次、寫作 2 篇）。用日排程硬套只會生出一堆假的紅格。
#
# **靈魂是「收工」那一格**：做滿的當下要當場發放行證（整期靜音、卡片換臉），
# 這是其他三個追蹤器都給不出來的東西——它們只會累積壓力，從不說「你可以停了」。
#
# 三條安全帶：
#   ① **沒做滿不結轉、不欠債**——上一期關帳就是關帳，`check()` 直接擋掉跨期補記。
#   ② **`times` 只有使用者能改**，系統永遠不自動往上調（那是加碼棘輪效應換一個殼）。
#   ③ **超額不進達標判定，只進 bonus**——做第 4 次是禮物，不是業績。
PERIODS = [
    {"id": "week", "label": "每週", "hint": "週一～週日算一期"},
    {"id": "month", "label": "每月", "hint": "日曆月算一期（1 號重新開始）"},
]
PERIOD_IDS = {p["id"] for p in PERIODS}
_PERIOD_DEFAULT = "week"
_TIMES_DEFAULT = 3
MAX_TIMES = 60
# 期末前幾天才敲（沒自己設就用這個）。週：剩 2 天；月：剩 5 天。
_NUDGE_DAYS = {"week": 2, "month": 5}
# 「什麼狀況叫我」——使用者自己勾。**期中預設完全不叫**，
# 哪天做是你的事，那正是這個模式的賣點；下面每一條都是你自己開的。
# 🚫 沒有「達標後還要提醒」這種選項：收工就是收工，不存在那個情境。
NUDGES = [
    {"id": "end", "label": "期末前還沒做滿", "default": True,
     "hint": "剩最後幾天、還差幾次時輕輕問一聲（週：剩 2 天／月：剩 5 天）"},
    {"id": "half", "label": "過了半期還沒開張", "default": False,
     "hint": "這一期一次都還沒做，過半時提一下"},
    {"id": "start", "label": "新的一期開始", "default": False,
     "hint": "新的一週/一個月開始時報一下這期要做幾次"},
]
NUDGE_IDS = {n["id"] for n in NUDGES}
_NUDGE_DEFAULT = {n["id"]: n["default"] for n in NUDGES}

# ---------------------------------------------------------------- 時間配額（unit="minutes"，§4.4）
# 時間配額：承諾的是一期花多少分鐘（例如上了一堂課，回家也要練同樣久），哪天做不限。
#
# **它是配額（§4.2）換一個單位，不是第四種追蹤器**：「這週做 3 次、哪天都行」跟「這週練 90 分、
# 哪天都行」是同一個形狀——承諾的是一期的量，哪天做是自由。所以期、收工、靜音、三條安全帶、
# 期格條、不結轉，全部沿用；只有「貨幣」從次數換成分鐘：
#   - 打卡記的是分鐘（`minutes: {ISO: 那天一共幾分}`，可以分好幾次記、會加起來）。
#   - **底線 `floor_min`** 是承諾（做到就收工、整期靜音）；**目標 `target_min`** 選填，只是往上看的方向，
#     🚫 永遠不拿來催你。超過目標的才叫加分（安全帶 ③）。
#   - 行事曆之類的外部排程可以經 links.py 的掛鉤（預設沒有）提供「留給這個習慣的時段」：還沒到的算排了，
#     不會自己變成做了——做了沒有只有你知道，所以時間過了只會問一句、等你記。
UNITS = [
    {"id": "count", "label": "次", "hint": "一期做幾次（原本的配額）"},
    {"id": "minutes", "label": "分鐘", "hint": "一期花多少時間——例如上了 1.5 小時的課，回家也練 1.5～3 小時"},
]
UNIT_IDS = {u["id"] for u in UNITS}
_UNIT_DEFAULT = "count"
MAX_PERIOD_MIN = 100 * 60     # 一期最多設 100 小時（防呆，不是建議）
MAX_DAY_MIN = 24 * 60          # 一天最多記 24 小時（同上）
#: 「趕不趕得上」的門檻：剩下的日子（含今天）平均每天要做超過這麼多分鐘、
#: **而且**超過平常步調的兩倍，才算「照這樣下去到不了底線」。見 `_pace()`。
PACE_DAILY_MIN = 60

# 打卡三態 ＋ 加碼兩個動作（rep ＝再做一次、unrep ＝按錯了退一次）
CHECK_STATES = {"done", "cancel", "none", "rep", "unrep"}
# 一天最多加到幾次。不是目標、不是上限考核——只是防呆（手滑按住不放）。
MAX_REPS = 99
# 習慣補打卡寬限：h12＝該日結束後再給 12 小時；d3＝ 3 天滾動視窗。
_MAKEUP_HOURS = 12
_MAKEUP_DAYS = 3


#: 承諾期進行中要改承諾期／刪掉時，那三道「我堅持」。流程只有這一份：
#: 畫面從 `meta()` 拿樣板、指令擋下來時照著一步一步問。{name}／{verb} 由顯示的那一方換。
INSIST_STEPS = (
    {"title": "還在承諾期 🔒", "body": "「{name}」還在承諾期，本來說好這段時間不動它的。", "yes": "我堅持要{verb}"},
    {"title": "真的要{verb}嗎？", "body": "養成到一半放掉很可惜——確定不是一時衝動？停下來想三秒也可以。",
     "yes": "對，我想清楚了"},
    {"title": "最後一次確認", "body": "按下去就會{verb}「{name}」，這個承諾就到此為止。這是你的決定，我尊重。",
     "yes": "確定{verb}"},
)


# ---------------------------------------------------------------- 意義／重要度／型態
# 打卡不該只剩壓力：每條習慣帶三件事——為什麼（`why`）、重要度（`importance`）、型態（`habit_kind`）。
# 沒有這些欄位的習慣＝core＋build（純加法，不遷移）。
#
# ⚠ 型態存成 `habit_kind`，**不是** `kind`：`kind` 早就是「reminder／habit」（純提醒 vs 想養成），
# 兩個意思擠同一格會把舊資料弄壞。指令的旗標叫 `kind`，進來就換成 `habit_kind`。
IMPORTANCES = [
    {"id": "core", "label": "核心", "hint": "主清單：這是你說好要顧的"},
    {"id": "bonus", "label": "加分", "hint": "有做很好，沒做完全沒後果；放在收起來的那一區"},
]
IMPORTANCE_IDS = {i["id"] for i in IMPORTANCES}
HABIT_KINDS = [
    {"id": "build", "label": "養成", "hint": "想慢慢養成的固定節奏"},
    {"id": "bonus", "label": "加分", "hint": "有做是禮物，沒做不算什麼"},
    {"id": "nudge", "label": "提醒", "hint": "只是提醒一下，不追成績"},
]
HABIT_KIND_IDS = {k["id"] for k in HABIT_KINDS}
MAX_WHY = 200


def is_side(r: dict) -> bool:
    """放「加分／提醒」那一區（預設收起）的：重要度是加分、或型態是加分／提醒。其餘＝核心主清單。"""
    return r.get("importance") == "bonus" or r.get("habit_kind") in ("bonus", "nudge")


def insist_steps(name: str, verb: str = "修改") -> list[dict]:
    """填好名字與動詞的三道確認（指令用；網頁拿 `meta()` 的樣板自己填）。"""
    return [{k: v.format(name=name, verb=verb) for k, v in step.items()} for step in INSIST_STEPS]


def meta() -> dict:
    """UI 要畫的詞彙（前端不要自己再寫一份中文標籤）。"""
    return {"repeats": REPEATS, "weekdays": WEEKDAY_LABEL, "nths": NTH_LABEL, "kinds": KINDS,
            "backfill_modes": BACKFILL_MODES, "habit_modes": HABIT_MODES,
            "periods": PERIODS, "nudges": NUDGES, "max_times": MAX_TIMES,
            "units": UNITS, "max_period_min": MAX_PERIOD_MIN,
            "importances": IMPORTANCES, "habit_kinds": HABIT_KINDS,
            "show_missed_red": show_missed_red(),
            "insist_steps": [dict(s) for s in INSIST_STEPS]}


# ---------------------------------------------------------------- 紅字開關
#: 過期沒做的格子要不要畫紅色。預設關：關著時「沒做」是中性色＋文字，統計卡不顯示連續與完成率。
#: 存在資料裡的 settings.show_missed_red。資料本身照算（`missed` 狀態還是 missed）——只管怎麼畫。
DEFAULT_SHOW_MISSED_RED = False


def show_missed_red() -> bool:
    cfg = _load().get("settings") or {}
    if "show_missed_red" not in cfg:
        return DEFAULT_SHOW_MISSED_RED
    return bool(cfg.get("show_missed_red"))


def set_show_missed_red(on: bool) -> bool:
    data = _load()
    data.setdefault("settings", {})["show_missed_red"] = bool(on)
    _save(data)
    return show_missed_red()


def _load() -> dict:
    data = {"routines": [], "settings": {}}
    stored = storage.load(DOC, None)
    if isinstance(stored, dict):
        data.update(stored)
    if not isinstance(data.get("settings"), dict):
        data["settings"] = {}
    for r in data["routines"]:
        _migrate(r)
    return data


def _migrate(r: dict) -> None:
    """舊紀錄原地升級成新模型。不動使用者資料。"""
    r.setdefault("id", uuid.uuid4().hex[:8])
    r.setdefault("name", "未命名")
    r.setdefault("note", "")
    r.setdefault("last_done", "")
    r.setdefault("last_notified", "")
    r.setdefault("at", "")                  # "HH:MM"，空＝整天都算數
    r.setdefault("catch_up", True)
    # 原本那套程式的「底線跟著某個行程的長度」（basis／floor_x／target_x）沒有搬：舊資料裡有就丟掉，照 floor_min／target_min 算
    for gone in ("basis", "floor_x", "target_x"):
        r.pop(gone, None)
    # ---- 意義／重要度／型態。沒有＝core＋build＝原本的樣子 ----
    r.setdefault("why", "")
    if r.get("importance") not in IMPORTANCE_IDS:
        r["importance"] = "core"
    if r.get("habit_kind") not in HABIT_KIND_IDS:
        r["habit_kind"] = "build"
    # 暫停（不算失敗）：[{from, until}]，until 空＝還沒說何時回來。這幾天不排、不過期、不提醒
    if not isinstance(r.get("pauses"), list):
        r["pauses"] = []
    # 「這週夠了」：{期的 key（例 2026-W46／2026-11）: 從哪天起}。那一期剩下的日子收起來
    if not isinstance(r.get("enough"), dict):
        r["enough"] = {}
    if "repeat" not in r:
        r["repeat"] = "days"
        r.setdefault("every_days", 7)
    if r["repeat"] not in REPEAT_IDS:
        r["repeat"] = "days"
    r.setdefault("every_days", 7)
    r.setdefault("weekdays", [])            # weekly：0=週一 … 6=週日
    r.setdefault("nth", 1)                  # monthly：第幾個（-1＝最後一個）
    r.setdefault("nth_weekday", 0)
    # ---- 配額（quota）欄位。純加法，舊資料沒有這些＝完全維持原本行為 ----
    r.setdefault("period", _PERIOD_DEFAULT)  # week / month
    if r["period"] not in PERIOD_IDS:
        r["period"] = _PERIOD_DEFAULT
    r.setdefault("times", _TIMES_DEFAULT)   # 一期要做幾次（🚫 系統永遠不自動調——安全帶 ②）
    r["times"] = max(1, min(MAX_TIMES, int(r.get("times") or _TIMES_DEFAULT)))
    # 一天最多算 1 次。**預設 False**：配額的賣點就是「不限制任何一天完成」，
    # 預設擋掉「一天做完」等於在這個模式的賣點上打洞。要分開幾天做才算數的承諾，
    # 自己勾——那是你對這件事的判斷，不是工具的規矩。
    r.setdefault("cap_per_day", False)
    r.setdefault("nudge_days", 0)           # 期末前幾天敲；0＝跟著 _NUDGE_DAYS 走
    nudges = r.get("nudges")
    if not isinstance(nudges, dict):
        nudges = dict(_NUDGE_DEFAULT)
    r["nudges"] = {k: bool(nudges.get(k, _NUDGE_DEFAULT[k])) for k in NUDGE_IDS}
    # 次數配額的「每次大約多久」（分鐘，選填，0＝沒估）。只是估計，不進打卡、不進達標判定。
    r.setdefault("each_min", 0)
    # ---- 時間配額（§4.4）。沒有這些欄位＝次數配額＝原本的行為 ----
    r.setdefault("unit", _UNIT_DEFAULT)
    if r["unit"] not in UNIT_IDS:
        r["unit"] = _UNIT_DEFAULT
    r.setdefault("floor_min", 0)            # 底線（分鐘）
    r.setdefault("target_min", 0)           # 目標（分鐘），0＝沒設
    if not isinstance(r.get("minutes"), dict):
        r["minutes"] = {}                   # {ISO: 那天一共幾分}，只有時間配額會寫
    # 非排定日也能打卡（記成加分）。**預設開**——灰格的原意是「這天不用做」，
    # 不是「這天不准記」。統計完全不受影響（分子分母只走排定日），見 §4.3。
    r.setdefault("extra_days", True)
    # ---- 追蹤（habit）新欄位 ----
    r.setdefault("kind", "reminder")
    if r["kind"] not in KIND_IDS:
        r["kind"] = "reminder"
    r.setdefault("done_dates", [])          # ✓ 打卡日（ISO）
    r.setdefault("skip_dates", [])          # ✗ 取消日（ISO）
    # 加碼模式（見 HABIT_MODES）。`reps` ＝ {ISO: 次數}，只有 mode=reps 會寫。
    # 沒有這兩個欄位的舊資料 ＝ once ＝ 完全維持原本的行為，不需要任何遷移。
    r.setdefault("mode", _HABIT_MODE_DEFAULT)
    if r["mode"] not in HABIT_MODE_IDS:
        r["mode"] = _HABIT_MODE_DEFAULT
    if not isinstance(r.get("reps"), dict):
        r["reps"] = {}
    r.setdefault("start_date", "")          # habit 承諾期起
    r.setdefault("end_date", "")            # habit 承諾期迄
    r.setdefault("backfill_mode", _BACKFILL_DEFAULT)  # 補打卡：none／h12／d3／always
    r["backfill_mode"] = _BACKFILL_ALIAS.get(r["backfill_mode"], r["backfill_mode"])
    if r["backfill_mode"] not in BACKFILL_MODE_IDS:
        r["backfill_mode"] = _BACKFILL_DEFAULT
    r.setdefault("pinned", False)           # 釘到打卡 dashboard 最上面
    # 舊資料只有 last_done：把它當作第一筆打卡歷史，統計才有起點
    if r.get("last_done") and not r["done_dates"]:
        r["done_dates"] = [r["last_done"]]


def _save(data: dict) -> None:
    storage.save(DOC, data)


def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(str(text))
    except (ValueError, TypeError):
        return None


def _parse_time(text: str) -> time | None:
    try:
        h, m = str(text).split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return None


def _nth_weekday_of(year: int, month: int, weekday: int, nth: int) -> date | None:
    """該月第 nth 個星期 weekday 是哪一天（nth=-1 代表最後一個）。"""
    days = [
        date(year, month, d)
        for d in range(1, calendar.monthrange(year, month)[1] + 1)
        if date(year, month, d).weekday() == weekday
    ]
    if not days:
        return None
    if nth == -1:
        return days[-1]
    return days[nth - 1] if 1 <= nth <= len(days) else None


def _period_bounds(period: str, day: date) -> tuple[date, date]:
    """`day` 落在哪一期的 [起, 迄]。週＝週一～週日（跟系統其他地方 0=週一 一致）；
    月＝日曆月。**刻意不做滾動 30 天**：滾動視窗算得更公平，但人腦看不懂自己的分母是
    哪 30 天，也就永遠等不到「收工」那一刻——收工感 > 統計精確度。"""
    if period == "month":
        last = calendar.monthrange(day.year, day.month)[1]
        return day.replace(day=1), day.replace(day=last)
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def _period_shift(period: str, day: date, n: int) -> date:
    """往後 n 期（n 可為負）的那一期起日。"""
    start, _ = _period_bounds(period, day)
    if period == "month":
        m = start.month - 1 + n
        return date(start.year + m // 12, m % 12 + 1, 1)
    return start + timedelta(days=7 * n)


def _period_key(period: str, day: date) -> str:
    start, _ = _period_bounds(period, day)
    if period == "month":
        return start.strftime("%Y-%m")
    iso = start.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _period_label(period: str, day: date) -> str:
    start, _ = _period_bounds(period, day)
    return f"{start.month} 月" if period == "month" else f"{start.month}/{start.day}"


def _quota_count(r: dict, start: date, end: date) -> int:
    """這一期做了幾次。`cap_per_day` 開著就一天只算 1 次——但**次數照樣記在 `reps` 裡**
    （記錄永遠誠實，只有「算不算進度」跟著設定走）。`done_dates` 裡沒有 reps 的日子
    （從別種週期改過來的舊資料）算 1 次，不然歷史會憑空消失。"""
    cap = bool(r.get("cap_per_day"))
    reps = r.get("reps") or {}
    done = set(r.get("done_dates") or [])
    total = 0
    for iso in set(reps) | done:
        day = _parse_date(iso)
        if not day or not (start <= day <= end):
            continue
        n = max(int(reps.get(iso, 0) or 0), 1 if iso in done else 0)
        if n <= 0:
            continue
        total += 1 if cap else n
    return total


def quota_state(r: dict, day: date | None = None, now: datetime | None = None) -> dict:
    """`day` 那一期的現況。前端的「本週 2/3 ／ 🎉 收工」整個長在這上面。
    時間配額（`unit="minutes"`）走 `time_state()`——同一個位置、多了分鐘那幾個欄位。"""
    if is_time(r):
        return time_state(r, day, now)
    day = day or clock.today()
    period = r.get("period", _PERIOD_DEFAULT)
    times = max(1, int(r.get("times") or _TIMES_DEFAULT))
    start, end = _period_bounds(period, day)
    c_start, c_end = _commitment_window(r)
    count = _quota_count(r, start, end)
    return {
        "period": period,
        "key": _period_key(period, day),
        "label": _period_label(period, day),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "times": times,
        "count": count,
        "remaining": max(0, times - count),
        "met": count >= times,
        # 超出的次數＝禮物，不是業績（安全帶 ③）。它只餵給「✨ 加分」，不進達標判定。
        "bonus": max(0, count - times),
        "days_left": max(0, (end - day).days),
        "cap_per_day": bool(r.get("cap_per_day")),
        # 承諾期把這一期切了一半 → 不列入完成率分母（半期不算分母，寧可少算你一次失敗）
        "partial": bool((c_start and c_start > start) or (c_end and c_end < end)),
    }


# ---------------------------------------------------------------- 時間配額（§4.4）的本體
def is_time(r: dict) -> bool:
    """這條是時間配額嗎（想養成＋一期幾次＋用分鐘算）。"""
    return (r.get("unit") == "minutes" and r.get("repeat") == "quota"
            and r.get("kind") == "habit")


_MIN_RE = re.compile(
    r"^([+-]?)\s*(?:(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours|小時|個小時|鐘頭|個鐘頭)\s*)?"
    r"(?:(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes|分鐘|分)?)?$")


def parse_minutes(value, allow_negative: bool = False) -> int | None:
    """「90」「90m」「90分」「1.5h」「1h30m」「1小時30分」「1:30」→ 分鐘。認不出來（或是 0）回 None——**不猜**。
    純數字一律當分鐘。`allow_negative`：打卡按錯了要能扣回來（「-15」）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(round(value))
    else:
        s = str(value).strip().lower().replace("：", ":").replace(" ", "")
        if not s:
            return None
        hm = re.fullmatch(r"([+-]?)(\d{1,3}):(\d{2})", s)
        m = None if hm else _MIN_RE.match(s)
        if hm:
            n = (int(hm.group(2)) * 60 + int(hm.group(3))) * (-1 if hm.group(1) == "-" else 1)
        elif m and (m.group(2) or m.group(3)):
            total = float(m.group(2) or 0) * 60 + float(m.group(3) or 0)
            n = int(round(total)) * (-1 if m.group(1) == "-" else 1)
        else:
            return None
    if n < 0 and not allow_negative:
        return None
    return n or None


def fmt_min(n) -> str:
    """90 → 「1 小時 30 分」、45 → 「45 分」、120 → 「2 小時」。"""
    h, m = divmod(max(0, int(n or 0)), 60)
    if h and m:
        return f"{h} 小時 {m} 分"
    return f"{h} 小時" if h else f"{m} 分"


def _period_word(period: str) -> str:
    return "這個月" if period == "month" else "這週"


def _goal(r: dict, period: str, start: date) -> dict:
    """這一期的底線／目標（分鐘）。目標不比底線多＝沒設目標（0）。"""
    floor, target = int(r.get("floor_min") or 0), int(r.get("target_min") or 0)
    return {"floor": max(0, floor), "target": target if target > floor else 0}


def _minutes_done(r: dict, start: date, end: date) -> int:
    """這一期記了幾分（`minutes` 裡落在這期的那幾天加起來）。"""
    total = 0
    for iso, n in (r.get("minutes") or {}).items():
        day = _parse_date(iso)
        if day and start <= day <= end:
            total += max(0, int(n or 0))
    return total


def _linked(r: dict, start: date, end: date) -> list[dict]:
    """這一期裡留給這條習慣的時段（來自 links 的掛鉤；沒接任何東西就是空的），照時間排。"""
    return links.habit_blocks(r["id"], start, end)


def _public(b: dict, extra: dict | None = None) -> dict:
    return {**{k: v for k, v in b.items() if not k.startswith("_")}, **(extra or {})}


def _pace(floor: int, done: int, planned: int, start: date, end: date, day: date,
          days_open: int) -> dict:
    """趕不趕得上底線——**一條講得出口的規則**，不是模型猜的：

    1. 還沒排進行程的那一段（`remaining_to_plan`），剩下的日子（含今天）平均每天要做超過
       `PACE_DAILY_MIN`（60）分、**而且**超過平常步調的兩倍 → `pace`。
       （平常步調＝底線平均攤到每一天；一週 10 小時的人一天 86 分是常態，不該被當成趕不上。）
    2. 過了半期（週：週四起；月：16 號起）還一分鐘都沒做、也沒排任何時段 → `not_started`。
    已經做到底線、這期沒有底線、或還差的都排進行程了 → 不算。
    """
    total_days = (end - start).days + 1
    passed = (day - start).days
    remaining_to_plan = max(0, floor - done - planned)
    cap = max(PACE_DAILY_MIN, -(-2 * floor // total_days))
    per_day = -(-remaining_to_plan // max(1, days_open))
    reason = ""
    if floor and done < floor and remaining_to_plan:
        if per_day > cap:
            reason = "pace"
        elif done == 0 and planned == 0 and passed >= total_days // 2:
            reason = "not_started"
    return {"behind": bool(reason), "behind_reason": reason, "need_per_day": per_day,
            "pace_cap": cap}


def time_state(r: dict, day: date | None = None, now: datetime | None = None) -> dict:
    """時間配額 `day` 那一期的現況（跟 `quota_state()` 同一個位置，多了分鐘那幾個欄位）。

    `now` 決定「排了」與「時間已經過了」的分界：還沒結束的綁定行程（只算還沒過去的那段）＝排了；
    已經結束、那天又沒記時間的＝`unlogged`（只會問一句，**不會自己算成做了**）。
    沒給 `now`：問的是今天就用現在，問別天就用那天 00:00（簡報的事實就是這樣：只看日期、不看幾點）。
    """
    if now is None:
        now = clock.now() if day in (None, clock.today()) else datetime.combine(day, time())
    day = day or now.date()
    period = r.get("period", _PERIOD_DEFAULT)
    start, end = _period_bounds(period, day)
    c_start, c_end = _commitment_window(r)
    g = _goal(r, period, start)
    floor, target = g["floor"], g["target"]
    done = _minutes_done(r, start, end)
    minutes = r.get("minutes") or {}
    planned_blocks, unlogged, planned = [], [], 0
    for b in _linked(r, start, end):
        if b["_e"] > now:
            left = int((b["_e"] - max(b["_s"], now)).total_seconds() // 60)
            planned += left
            planned_blocks.append(_public(b, {"left": left}))
        elif not int(minutes.get(b["date"], 0) or 0):
            unlogged.append(_public(b))
    days_left = max(0, (end - day).days)
    days_open = days_left + 1
    if c_end and c_end < end:
        days_open = max(0, min(days_open, (c_end - day).days + 1))
    no_floor = floor <= 0
    met = done >= floor
    pace = _pace(floor, done, planned, start, end, day, days_open)
    q = {
        "unit": "minutes", "period": period,
        "key": _period_key(period, day), "label": _period_label(period, day),
        "start": start.isoformat(), "end": end.isoformat(),
        "done_min": done, "floor": floor, "target": target,
        "planned_min": planned, "planned": planned_blocks, "unlogged": unlogged,
        "remaining_to_floor": max(0, floor - done),
        "remaining_to_target": max(0, target - done) if target else 0,
        # 還沒排進行程、也還沒做的那一段＝「這週要花、但行事曆上看不到」的時間。從底線算，不從目標。
        "remaining_to_plan": max(0, floor - done - planned),
        "remaining_to_plan_target": max(0, target - done - planned) if target else 0,
        "days_left": days_left, "days_open": days_open,
        **pace,
        "met": met,                                  # 只看底線（安全帶 ③：目標、超額都不進達標判定）
        "target_met": bool(target and done >= target),
        # 加分＝超過目標的（沒設目標就是超過底線的；這期沒底線＝全部都是加分）
        "bonus": done if no_floor else max(0, done - (target or floor)),
        "no_floor": no_floor,
        "partial": bool((c_start and c_start > start) or (c_end and c_end < end)),
        # 跟次數配額同名的欄位：舊的讀法（月曆、報表）不會壞，數字是分鐘
        "count": done, "times": floor, "remaining": max(0, floor - done), "cap_per_day": False,
    }
    q["progress_short"] = f"{done} 分" if no_floor else f"{done}/{floor} 分"
    q["progress_text"] = _time_progress_text(q)
    q["pace_text"] = _time_pace_text(q)
    q["say"] = _time_say(q)
    return q


def _target_tail(q: dict) -> str:
    return f"（目標 {q['target']}）" if q["target"] else ""


def _time_progress_text(q: dict) -> str:
    """「這週 45/90 分（目標 180）」——卡片、聊天指令、簡報共用這一句。"""
    w = _period_word(q["period"])
    if q["no_floor"]:
        return f"{w}沒有底線，不設底線" + (f"（記了 {q['done_min']} 分，都算加分）" if q["done_min"] else "")
    head = f"{w} {q['done_min']}/{q['floor']} 分"
    if q["target_met"]:
        return f"{head}，目標 {q['target']} 也到了"
    if q["met"]:
        return f"{head}，底線到了{_target_tail(q)}"
    return head + _target_tail(q)


def _time_pace_text(q: dict) -> str:
    """把「趕不趕得上」那條規則套在這期的數字上講一遍（講得出為什麼，才不會像在被監視）。"""
    if q["no_floor"] or q["met"]:
        return ""
    if not q["remaining_to_plan"]:
        return f"還差的 {fmt_min(q['remaining_to_floor'])}都排進行程了"
    half = "半個月" if q["period"] == "month" else "半週"
    if q["behind_reason"] == "not_started":
        return f"過了{half}還沒開始，也還沒排時段"
    base = (f"剩下 {q['days_open']} 天（含今天）、還沒排的 {fmt_min(q['remaining_to_plan'])}，"
            f"平均一天 {q['need_per_day']} 分")
    if q["behind_reason"] == "pace":
        base += f"（超過一天 {q['pace_cap']} 分）"
    return base


def _time_say(q: dict) -> str:
    """一句邀請：只講還差多少、還有幾天、要不要排——🚫 不講落後、不比上週。"""
    w = _period_word(q["period"])
    if q["no_floor"]:
        return f"{w}不設底線——想做就做，都算加分"
    if q["met"]:
        if q["target"] and not q["target_met"]:
            return (f"{w}底線到了 🎉 想往目標再走的話還有 {fmt_min(q['remaining_to_target'])}，"
                    "不做也完全沒關係")
        tail = f"，多的 {fmt_min(q['bonus'])} 是加分 ✨" if q["bonus"] else ""
        return f"{w}收工了 🎉 剩下的時間是你的{tail}"
    left = f"還有 {q['days_open']} 天" if q["days_open"] > 1 else "今天是最後一天"
    gap = fmt_min(q["remaining_to_floor"])
    if not q["remaining_to_plan"]:
        return f"{w}還差 {gap}到底線，都排進行程了，照著走就夠"
    if q["behind_reason"] == "not_started":
        return f"{w}還沒開始，{left}——要不要先排一段時間？"
    if q["behind_reason"] == "pace":
        return f"{w}還差 {gap}到底線，{left}——要不要先排一段進行程？"
    return f"{w}還差 {gap}到底線，{left}，夠的"


def _time_met(r: dict, day: date) -> bool:
    """`_due_on()` 用的便宜版：只算底線跟做了多少（不看排了什麼）。"""
    period = r.get("period", _PERIOD_DEFAULT)
    start, end = _period_bounds(period, day)
    return _minutes_done(r, start, end) >= _goal(r, period, start)["floor"]


def _next_time_due(r: dict, after: date, periods: int = 60) -> date | None:
    """時間配額的「下一次有事」：一期一期往後看，第一個還沒做到底線的期（那週沒課就跳過）。
    """
    period = r.get("period", _PERIOD_DEFAULT)
    _s, c_end = _commitment_window(r)
    for k in range(periods + 1):
        p_start = _period_shift(period, after, k)
        day = max(after, p_start)
        if c_end and day > c_end:
            return None
        if not _in_commitment(r, day):
            continue
        p_end = _period_bounds(period, p_start)[1]
        if _minutes_done(r, p_start, p_end) < _goal(r, period, p_start)["floor"]:
            return day
    return None


def time_views(now: datetime | None = None) -> list[dict]:
    """還在承諾期內的時間配額習慣（`_view()`，用給定的 `now` 算）。助手、簡報、找空檔共用。"""
    now = now or clock.now()
    out = []
    for r in _load()["routines"]:
        if not is_time(r):
            continue
        v = _view(r, now)
        if not v["not_started"] and not v["period_ended"]:
            out.append(v)
    out.sort(key=lambda v: v["name"])
    return out


def _commitment_window(r: dict) -> tuple[date | None, date | None]:
    """habit 的承諾期 `[start, end]`。reminder 沒有承諾期，回 (None, None)。"""
    if r.get("kind") != "habit":
        return None, None
    return _parse_date(r.get("start_date")), _parse_date(r.get("end_date"))


def _in_commitment(r: dict, day: date) -> bool:
    """這天在承諾期內嗎？reminder 永遠算在內（它沒有起訖）。"""
    start, end = _commitment_window(r)
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def _paused_on(r: dict, day: date) -> bool:
    """這天在「暫停」裡嗎。暫停不算失敗：不排、不過期、不提醒、不算進連續或完成率。"""
    iso = day.isoformat()
    for p in r.get("pauses") or []:
        if not isinstance(p, dict):
            continue
        start, until = str(p.get("from") or ""), str(p.get("until") or "")
        if start and start <= iso and (not until or iso <= until):
            return True
    return False


def _enough_period(r: dict) -> str:
    """「這週夠了」以哪一期為單位：一期幾次的照它自己的期（週／月），其他一律是這週。"""
    return r.get("period", _PERIOD_DEFAULT) if r.get("repeat") == "quota" else "week"


def _enough_on(r: dict, day: date) -> bool:
    """這天在「這一期夠了」之後嗎：從說夠了那天起到這一期結束，收起來、不提醒、不算沒做。"""
    since = (r.get("enough") or {}).get(_period_key(_enough_period(r), day))
    return bool(since) and day.isoformat() >= str(since)


def _resting(r: dict, day: date) -> bool:
    """暫停中或這期夠了——這天不算「該做」。"""
    return r.get("kind") == "habit" and (_paused_on(r, day) or _enough_on(r, day))


def _due_on(r: dict, day: date) -> bool:
    """這一天輪到這個提醒了嗎？（只看日期，不看時間）

    **承諾期外一律 False。** 週期樣式（daily/weekly/monthly）本身不知道起訖日，
    所以「每週四五、8/6 才起跑」的習慣，在 7/30 那個週四也會被判成該做——於是月曆
    畫了它、排程器也真的彈了視窗（實機重現：`last_notified` 停在起跑前一週）。
    起訖日是使用者排的，排定之外不該有任何動靜，門檻就設在這裡一次擋掉：
    `_view().due_today` / `should_notify`、`calendar_view`、`_next_due` 全走這條。
    """
    if not _in_commitment(r, day) or _resting(r, day):
        return False
    kind = r["repeat"]
    if kind == "quota":
        # 配額沒有「該做的那一天」——這裡回答的是「這一期還有沒有事沒做完」。
        # 所以達標的當下 `due_today` 就變 False：卡片換臉、月曆那格消失＝**收工**。
        # ⚠ 它**不代表要提醒你**（那會變成每天催）——提醒另外走 `_quota_notify()`。
        if is_time(r):
            return not _time_met(r, day)      # 時間配額：到底線就收工（那期沒底線＝本來就沒事）
        return not quota_state(r, day)["met"]
    if kind == "daily":
        return True
    if kind == "weekly":
        return day.weekday() in (r.get("weekdays") or [])
    if kind == "monthly":
        target = _nth_weekday_of(day.year, day.month,
                                 int(r.get("nth_weekday", 0)), int(r.get("nth", 1)))
        return target == day
    # days：從上次做完起算隔幾天（沒做過就是現在該做）
    if not r.get("last_done"):
        return True
    try:
        last = date.fromisoformat(r["last_done"])
    except ValueError:
        return True
    return (day - last).days >= max(1, int(r.get("every_days", 7)))


def _scheduled_on(r: dict, day: date, anchor: date) -> bool:
    """統計用的『這天原本該做嗎』——不依賴會變動的 last_done，所以連鎖/完成率穩定。
    `anchor` 是 `days` 模式的起算日（用承諾期起或第一筆打卡）。
    暫停中、這期夠了的日子不算該做。"""
    if _resting(r, day):
        return False
    kind = r["repeat"]
    if kind == "quota":
        return False        # 配額模式沒有任何一天「該做」——統計改以「期」為單位（_quota_stats）
    if kind == "daily":
        return True
    if kind == "weekly":
        return day.weekday() in (r.get("weekdays") or [])
    if kind == "monthly":
        target = _nth_weekday_of(day.year, day.month,
                                 int(r.get("nth_weekday", 0)), int(r.get("nth", 1)))
        return target == day
    # days：從 anchor 起，每 every_days 一次
    step = max(1, int(r.get("every_days", 7)))
    return day >= anchor and (day - anchor).days % step == 0


#: ⭐ **新建習慣預設有提醒**（沒選就不用）。
#: 只管指令新建：沒說幾點、也沒說不要提醒，就用這個時間；settings.habit_remind＝{"enabled": false, "at": "21:00"} 可以關掉或換時間。
DEFAULT_HABIT_REMIND = {"at": "21:00", "enabled": True}


def habit_default_at() -> str:
    """新建習慣預設的提醒時間；關掉或設定壞掉回空字串（＝不定時叫）。"""
    cfg = {**DEFAULT_HABIT_REMIND, **(_load().get("settings", {}).get("habit_remind") or {})}
    if not cfg.get("enabled", True):
        return ""
    at = str(cfg.get("at") or "").strip()
    return at if _parse_time(at) is not None else ""


def _rolling_walk(r: dict, anchor: date, horizon: date, today: date) -> dict:
    """`every_days` 的統計：**滾動窗口**，不是固定格子。

    任何連續 N 天裡打過一次就過關——早打晚打都行。做法是一個往前推的「期限」：
    起點是 `anchor + N-1`；每打一次卡，期限就推到那天 + N；期限那天沒打、也沒跳過，
    才算漏一次（期限再往後推 N 天）。今天剛好是期限而還沒打，是 pending，不算漏。

    固定格子（`_scheduled_on` 的舊算法）會把「第 2 天才打」記成「第 1 天漏了」；
    這跟 `_due_on()`（從上次做完起算）本來就是兩套邏輯，現在對齊成同一套。
    回 {"done": 有打的日子, "missed": 漏掉的期限日, "pending": 還在等的下一個期限日（可能在未來）}。
    """
    n = max(1, int(r.get("every_days", 7)))
    done = set(r.get("done_dates", []))
    skip = set(r.get("skip_dates", []))
    done_days: list[date] = []
    missed: list[date] = []
    deadline = anchor + timedelta(days=n - 1)
    d = anchor
    while d <= horizon:
        iso = d.isoformat()
        if iso in done:
            done_days.append(d)
            deadline = d + timedelta(days=n)
        elif d == deadline and (iso in skip or _resting(r, d)):
            deadline = d + timedelta(days=n)          # 跳過／暫停／這週夠了＝這一窗休息，不算漏
        elif d == deadline and d < today:
            missed.append(d)
            deadline = d + timedelta(days=n)
        d += timedelta(days=1)
    return {"done": done_days, "missed": missed, "pending": deadline}


def _next_due(r: dict, after: date, limit: int = 400) -> date | None:
    if is_time(r):
        return _next_time_due(r, after)
    for i in range(limit):
        day = after + timedelta(days=i)
        if _due_on(r, day):
            return day
    return None


def describe(r: dict) -> str:
    """一行人話：「每週 週五 20:00」。UI 直接顯示這個字串。"""
    kind = r["repeat"]
    if kind == "daily":
        base = "每天"
    elif kind == "weekly":
        wd = sorted(d for d in (r.get("weekdays") or []) if 0 <= d <= 6)
        days = [WEEKDAY_LABEL[d] for d in wd]
        if wd == [0, 1, 2, 3, 4]:
            base = "平日（週一～週五）"        
        elif wd == [5, 6]:
            base = "週末（週六、週日）"
        else:
            base = "每週 " + "、".join(days) if days else "每週（還沒選星期）"
    elif kind == "monthly":
        base = (f"每月{NTH_LABEL.get(int(r.get('nth', 1)), '第一個')}"
                f"{WEEKDAY_LABEL[int(r.get('nth_weekday', 0))]}")
    elif kind == "quota" and is_time(r):
        unit = "每月" if r.get("period") == "month" else "每週"
        tgt = int(r.get("target_min") or 0)
        floor = int(r.get("floor_min") or 0)
        base = (f"{unit} {fmt_min(floor)}" + (f"（目標 {fmt_min(tgt)}）" if tgt > floor else "")
                + "・哪天都行")
        return base                        # 跟次數配額一樣不掛時間
    elif kind == "quota":
        unit = "每月" if r.get("period") == "month" else "每週"
        base = f"{unit} {max(1, int(r.get('times') or _TIMES_DEFAULT))} 次・哪天都行"
        if r.get("cap_per_day"):
            base += "（一天算 1 次）"
        return base                        # 配額不掛時間——`at` 只是提醒要幾點敲
    else:
        base = f"每 {max(1, int(r.get('every_days', 7)))} 天"
    return f"{base} {r['at']}".strip() if r.get("at") else base


def _habit_anchor(r: dict, today: date) -> date:
    """統計的起算日：承諾期起 > 第一筆打卡 > 今天。"""
    start = _parse_date(r.get("start_date"))
    if start:
        return start
    dates = [d for d in (_parse_date(x) for x in r.get("done_dates", [])) if d]
    return min(dates) if dates else today


def quota_cells(r: dict, today: date, limit: int = 0) -> list[dict]:
    """從起算日到今天，一期一格（舊到新）。給統計與前端的期格條共用一份，
    兩邊就不會各算一套。`limit`>0 只回最後幾格。"""
    period = r.get("period", _PERIOD_DEFAULT)
    times = max(1, int(r.get("times") or _TIMES_DEFAULT))
    anchor = _habit_anchor(r, today)
    c_end = _parse_date(r.get("end_date"))
    horizon = min(today, c_end) if c_end else today
    cur = _period_bounds(period, anchor)[0]
    last = _period_bounds(period, horizon)[0]
    cells: list[dict] = []
    timed = is_time(r)
    enough = r.get("enough") or {}
    while cur <= last and len(cells) < 400:
        start, end = _period_bounds(period, cur)
        # 這一期有暫停過、或說過夠了：不進分母、不算斷（跟半期同一個道理）
        excused = _period_key(period, cur) in enough or any(
            _paused_on(r, start + timedelta(days=i)) for i in range((end - start).days + 1))
        if timed:
            g = _goal(r, period, start)
            done = _minutes_done(r, start, end)
            cells.append({
                "key": _period_key(period, cur), "label": _period_label(period, cur),
                "start": start.isoformat(), "end": end.isoformat(),
                "unit": "minutes", "count": done, "times": g["floor"], "target": g["target"],
                "met": done >= g["floor"],
                # 這期沒有底線（例如那週課被跳過）：不算達標、也不算沒達標——不列入（跟半期同一個道理）
                "no_floor": g["floor"] <= 0,
                "current": start <= today <= end,
                "partial": anchor > start or bool(c_end and c_end < end),
                "excused": excused,
            })
            cur = _period_shift(period, cur, 1)
            continue
        count = _quota_count(r, start, end)
        cells.append({
            "key": _period_key(period, cur),
            "label": _period_label(period, cur),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "count": count,
            "times": times,
            "met": count >= times,
            "current": start <= today <= end,
            # 起跑/結束把這一期切一半 → 不列入分母（半期不算分母，§4.2）
            "partial": anchor > start or bool(c_end and c_end < end),
            "excused": excused,
        })
        cur = _period_shift(period, cur, 1)
    return cells[-limit:] if limit else cells


def _quota_stats(r: dict, today: date) -> dict:
    """配額模式的統計——**單位從「天」換成「期」**。

    連鎖＝連續幾期達標（仍然是**唯一一條**連鎖，沒有違反 §4.1 規則 ③）；
    完成率＝達標期數／已關帳的期數。**沒做滿的那一期不會結轉成下一期的債**（安全帶 ①）。
    """
    cells = quota_cells(r, today)
    # 時間配額「這期沒有底線」（那週沒課）跟半期一樣：不進分母、不算連鎖也不算斷
    skip = lambda c: c["current"] or c["partial"] or c.get("no_floor") or c.get("excused")   # noqa: E731
    closed = [c for c in cells if not skip(c)]
    completed = [c for c in closed if c["met"]]

    streak = longest = run = 0
    for c in cells:
        if skip(c) and not (c["met"] and not c.get("no_floor")):
            continue            # 還沒過完／半期／沒底線：不算達標也**不算斷**（寬限，原則 1）
        if c["met"]:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    # 目前連鎖：從最後往回數，同樣跳過「還沒過完的這一期」
    for c in reversed(cells):
        if skip(c) and not (c["met"] and not c.get("no_floor")):
            continue
        if c["met"]:
            streak += 1
        else:
            break
    q = quota_state(r, today)
    return {
        "scheduled": len(closed),
        "completed": len(completed),
        "rate": round(len(completed) / len(closed) * 100) if closed else 0,
        "current_streak": streak,
        "longest_streak": longest,
        "quota": q,
        "periods": cells[-12:],
    }


def habit_stats(r: dict, today: date | None = None) -> dict:
    """連鎖 / 最長連鎖 / 完成率 / 剩幾天——全部從 done_dates + 排程算。
    連鎖以『該做的日子』為單位（每週三五的習慣，連續兩個排定日都做＝連鎖 2）。
    配額模式（`repeat=quota`）改以『期』為單位，見 `_quota_stats()`。"""
    today = today or clock.today()
    if r.get("repeat") == "quota":
        start_d, end_d = _commitment_window(r)
        return {
            **_quota_stats(r, today),
            "days_left": (end_d - today).days if end_d and today <= end_d else 0,
            **_reps_stats(r, today),
            "days_to_start": (start_d - today).days if start_d and today < start_d else 0,
            "not_started": bool(start_d and today < start_d),
            "ended": bool(end_d and today > end_d),
            "start": r.get("start_date", ""),
            "end": r.get("end_date", ""),
        }
    anchor = _habit_anchor(r, today)
    end = _parse_date(r.get("end_date")) or today
    horizon = min(today, end)
    done = set(r.get("done_dates", []))

    scheduled: list[date] = []
    if r.get("repeat") == "days":
        # 滾動窗口：打過的每一次＋漏掉的期限日（「今天還在等」不算，跟固定格子的寬限一樣）
        w = _rolling_walk(r, anchor, horizon, today)
        scheduled = sorted(w["done"] + w["missed"])
    elif anchor <= horizon:
        d = anchor
        while d <= horizon:
            if _scheduled_on(r, d, anchor):
                scheduled.append(d)
            d += timedelta(days=1)

    completed = [d for d in scheduled if d.isoformat() in done]
    rate = round(len(completed) / len(scheduled) * 100) if scheduled else 0

    # 目前連鎖：從最近的排定日往回數連續有打卡的；今天若還沒打不算破功（還沒過完）
    current = 0
    for d in reversed(scheduled):
        if d == today and d.isoformat() not in done:
            continue  # 今天還在寬限，不算斷
        if d.isoformat() in done:
            current += 1
        else:
            break

    # 最長連鎖
    longest = run = 0
    for d in scheduled:
        if d.isoformat() in done:
            run += 1
            longest = max(longest, run)
        else:
            run = 0

    start_d, end_d = _commitment_window(r)
    days_left = (end_d - today).days if end_d and today <= end_d else 0
    return {
        "scheduled": len(scheduled),
        "completed": len(completed),
        "rate": rate,
        "current_streak": current,
        "longest_streak": longest,
        "days_left": days_left,
        **_reps_stats(r, today),
        "days_to_start": (start_d - today).days if start_d and today < start_d else 0,
        "not_started": bool(start_d and today < start_d),
        "ended": bool(end_d and today > end_d),
        "start": r.get("start_date", ""),
        "end": r.get("end_date", ""),
    }


def _quota_day_status(r: dict, day: date, today: date, anchor: date) -> str:
    """配額模式的格子：**永遠不會有 missed**。

    這是這個模式最重要的一句話——沒有任何一天「該做而沒做」，只有「一期結束了還沒做滿」。
    日格在這裡只負責畫**事實**（哪幾天你真的做了），義務不畫，因為根本不存在。
    可點的範圍＝**本期之內**：上一期關帳就是關帳（安全帶 ①，`check()` 也會擋）。
    """
    iso = day.isoformat()
    if iso in r.get("done_dates", []):
        return "done"
    if iso in r.get("skip_dates", []):
        return "skip"
    if day > today:
        return "future"
    end = _parse_date(r.get("end_date"))
    if day < anchor or (end and day > end):
        return "na"
    p_start, p_end = _period_bounds(r.get("period", _PERIOD_DEFAULT), today)
    return "pending" if p_start <= day <= p_end else "na"


def _quota_notify(r: dict, q: dict, today: date) -> tuple[bool, str]:
    """「什麼狀況叫我」——使用者自己勾的觸發條件（NUDGES）。回 (要不要叫, 說什麼)。

    **達標就閉嘴，而且沒有開關**：收工就是收工，不存在「做滿了還要提醒你」的情境。
    期中不主動叫也是預設立場；下面每一條都是使用者自己開的。全部用邀請句（§7）。
    """
    if q["met"]:
        return False, ""
    n = r.get("nudges") or _NUDGE_DEFAULT
    period = q["period"]
    unit = "這個月" if period == "month" else "這週"
    left, done, remain = q["days_left"], q["count"], q["remaining"]
    if q.get("unit") == "minutes":
        # 時間配額：同一張「什麼狀況叫我」，話換成分鐘版（`time_state()` 的 `say`，一樣是邀請句）
        if n.get("start") and today.isoformat() == q["start"]:
            return True, (f"新的一{'個月' if period == 'month' else '週'}，"
                          f"底線 {fmt_min(q['floor'])}"
                          + (f"、目標 {fmt_min(q['target'])}" if q["target"] else "") + "。")
        if n.get("end"):
            window = int(r.get("nudge_days") or 0) or _NUDGE_DAYS.get(period, 2)
            if left <= window:
                return True, q["say"]
        if n.get("half") and done == 0:
            total = (_parse_date(q["end"]) - _parse_date(q["start"])).days + 1
            if total - left - 1 >= total / 2:
                return True, f"{unit}還沒開始，要不要今天起個頭？"
        return False, ""
    if n.get("start") and today.isoformat() == q["start"]:
        return True, f"新的一{'個月' if period == 'month' else '週'}，{q['times']} 次。"
    if n.get("end"):
        window = int(r.get("nudge_days") or 0) or _NUDGE_DAYS.get(period, 2)
        if left <= window:
            tail = "，要不要挑一天？" if left else "，今天是最後一天了"
            return True, f"{unit}還差 {remain} 次{tail}"
    if n.get("half") and done == 0:
        total = (_parse_date(q["end"]) - _parse_date(q["start"])).days + 1
        if total - left - 1 >= total / 2:
            return True, f"{unit}還沒開張，要不要今天起個頭？"
    return False, ""


def _reps_stats(r: dict, today: date) -> dict:
    """加分統計。**只有累積，沒有平均、沒有達成率、沒有跟昨天比**（規則 2）。

    `bonus` ＝ 超出「有做到」的那些次數，也就是你多做的部分——這是唯一一個
    專門講「你做得比原本更好」的數字，其餘統計一律照舊，不受加碼影響。
    """
    if r.get("kind") != "habit" or r.get("mode") != "reps":
        return {}
    reps = {k: int(v) for k, v in r.get("reps", {}).items() if int(v) > 0}
    total = sum(reps.values())
    week_start = today - timedelta(days=6)          # 近 7 個日曆天（含今天）
    best_iso, best_n = "", 0
    for iso, n in sorted(reps.items()):
        if n >= best_n:                             # 平手取**最近**那天，往前看比較有力
            best_iso, best_n = iso, n
    return {
        "reps_today": reps.get(today.isoformat(), 0),
        "reps_week": sum(n for iso, n in reps.items() if week_start.isoformat() <= iso),
        "reps_total": total,
        "bonus": max(0, total - len(reps)),
        "best_day": best_iso,
        "best_reps": best_n,
    }


def _day_status(r: dict, day: date, today: date, now: datetime, anchor: date,
                walk: dict | None = None) -> str:
    """打卡 dashboard 單一格子的狀態：done / extra / skip / missed / na / pending / future。
    前端只用 4 種顏色（綠/紅/灰/空白），但這裡照實回傳細節，灰跟空白怎麼分是前端的事——
    這樣「不需要做」跟「取消」跟「還沒到」都留得住各自的意思，滑鼠移過去才講清楚。

    `extra`＝**這天不用做、你還是做了**（§4.3）。它跟 `done` 分開是刻意的：畫成一樣的滿綠，
    「額外的」跟「該做的」就在視覺上混成一團，完成率（只算排定日）跟眼睛看到的東西會對不上。
    """
    iso = day.isoformat()
    # 暫停／這期夠了：沒打卡的日子畫成「休息」，不是沒做——有打卡的照實畫 done
    if (_resting(r, day) and iso not in r.get("done_dates", []) and iso not in r.get("skip_dates", [])
            and _in_commitment(r, day) and day >= anchor):
        return "paused" if _paused_on(r, day) else "enough"
    if r.get("repeat") == "quota":
        return _quota_day_status(r, day, today, anchor)
    end = _parse_date(r.get("end_date"))
    if r.get("repeat") == "days":
        # 滾動窗口（見 `_rolling_walk`）：打了就是 done；漏掉的期限日才紅；其餘日子「不用做」
        if iso in r.get("done_dates", []):
            return "done"
        if iso in r.get("skip_dates", []):
            return "skip"
        if day > today:
            return "future"
        if day < anchor or (end and day > end):
            return "na"
        w = walk or _rolling_walk(r, anchor, min(today, end) if end else today, today)
        if day in w["missed"]:
            return "missed" if now > _red_after(day, r.get("backfill_mode", _BACKFILL_DEFAULT)) else "pending"
        return "pending" if day == w["pending"] else "na"
    on_duty = (day >= anchor and not (end and day > end)
               and _scheduled_on(r, day, anchor))
    if iso in r.get("done_dates", []):
        return "done" if on_duty else "extra"
    if iso in r.get("skip_dates", []):
        return "skip"
    if day > today:
        return "future"
    # `_scheduled_on()` 只看週期樣式（daily/weekly/monthly 不看 anchor），承諾期外要另外擋——
    # 不然還沒開始養成的那幾天（start_date 之前，例如下個月才起跑的習慣）會被誤判成
    # 「錯過」或今天那格畫出可打卡的框，其實那天根本不該做。這裡一律回「na」（灰、不可點）。
    end = _parse_date(r.get("end_date"))
    if day < anchor or (end and day > end):
        return "na"
    if not _scheduled_on(r, day, anchor):
        return "na"
    if now > _red_after(day, r.get("backfill_mode", _BACKFILL_DEFAULT)):
        return "missed"
    return "pending"


def habit_matrix(r: dict, today: date, now: datetime, days: int = 31) -> list[dict]:
    """最近 `days` 天（含今天）的打卡矩陣，舊到新排序。給統一打卡 dashboard 用。

    加碼模式多給一個 `reps`：那格做了幾次。**顏色不因為次數變深**——
    顏色只講「有沒有做到」，次數是寫在格子裡的數字（規則 2：不製造視覺上的互相比較）。
    """
    anchor = _habit_anchor(r, today)
    start = today - timedelta(days=days - 1)
    reps = r.get("reps", {}) if r.get("mode") == "reps" else {}
    walk = None
    if r.get("repeat") == "days":
        end = _parse_date(r.get("end_date"))
        walk = _rolling_walk(r, anchor, min(today, end) if end else today, today)
    cells = [{"date": d.isoformat(), "status": _day_status(r, d, today, now, anchor, walk),
              "reps": int(reps.get(d.isoformat(), 0))}
             for d in (start + timedelta(days=i) for i in range(days))]
    if is_time(r):
        # 時間配額：那天記了幾分（格子顏色照舊只講「有沒有做」，分鐘數寫在格子裡／滑過去看）
        mins = r.get("minutes") or {}
        for c in cells:
            c["minutes"] = int(mins.get(c["date"], 0) or 0)
    return cells


def _today_state(r: dict, today: date) -> str:
    iso = today.isoformat()
    if iso in r.get("done_dates", []):
        return "done"
    if iso in r.get("skip_dates", []):
        return "cancel"
    return "none"


def _habit_locked(r: dict, today: date) -> bool:
    """承諾期**進行中**的 habit：不可刪、承諾期不可改。

    起跑日之前不鎖——那時候還沒有承諾可以撐住，排錯日期就該能直接刪掉重來，
    不必為了一條還沒開始的習慣按三次「我堅持要刪」。
    """
    if r.get("kind") != "habit":
        return False
    start, end = _commitment_window(r)
    if start and today < start:
        return False
    return bool(end and today <= end)


def _view(r: dict, now: datetime) -> dict:
    today = now.date()
    days_since = None
    if r.get("last_done"):
        try:
            days_since = (today - date.fromisoformat(r["last_done"])).days
        except ValueError:
            pass
    done_today = r.get("last_done") == today.isoformat()
    due_today = _due_on(r, today) and not done_today
    at = _parse_time(r.get("at", ""))
    start_d, end_d = _commitment_window(r)
    not_started = bool(start_d and today < start_d)
    period_ended = bool(end_d and today > end_d)

    # 時間到了沒？沒設時間＝整天都算「到了」
    time_reached = at is None or now.time() >= at
    # 過了時間但還沒做：catch_up 決定要不要繼續叫
    missed = bool(due_today and at is not None and now.time() >= at)
    should_notify = bool(due_today and time_reached
                         and (r.get("catch_up", True) or not _passed_grace(now, at)))
    quota = nudge = None
    if r.get("repeat") == "quota":
        quota = quota_state(r, today, now)
        # 配額的「還有事沒做完」＝**這一期**沒做滿，跟今天有沒有打卡無關
        # （今天做了 1 次、這週還差 2 次 → 當然還是 due）。收工的唯一定義是做滿。
        due_today = not quota["met"] and not _resting(r, today)
        # 配額**永遠不會 missed**：沒有哪一天該做而沒做（只有一期沒做滿）。
        # 而「該不該叫」跟「這期做完了沒」是兩件事——不然就變成每天催（見 _quota_notify）。
        missed = False
        fire, nudge = _quota_notify(r, quota, today)
        should_notify = bool(fire and time_reached and _in_commitment(r, today) and not _resting(r, today))
    paused_now = r.get("kind") == "habit" and _paused_on(r, today)
    open_pause = next((p for p in (r.get("pauses") or [])
                       if isinstance(p, dict) and _paused_on({"pauses": [p]}, today)), None)
    out = {
        "id": r["id"],
        "name": r["name"],
        "note": r.get("note", ""),
        "kind": r.get("kind", "reminder"),
        # ---- 意義／重要度／型態／多條路／暫停／這期夠了 ----
        "why": r.get("why", ""),
        "importance": r.get("importance", "core"),
        "habit_kind": r.get("habit_kind", "build"),
        "section": "side" if is_side(r) else "core",     # 網頁：side＝「加分／提醒」那一區（預設收起）
        "paused": bool(paused_now),
        "paused_until": str((open_pause or {}).get("until") or "") if paused_now else "",
        "pauses": [dict(p) for p in (r.get("pauses") or []) if isinstance(p, dict)],
        "enough_now": r.get("kind") == "habit" and _enough_on(r, today),
        "repeat": r["repeat"],
        "every_days": max(1, int(r.get("every_days", 7))),
        "weekdays": r.get("weekdays") or [],
        "nth": int(r.get("nth", 1)),
        "nth_weekday": int(r.get("nth_weekday", 0)),
        "at": r.get("at", ""),
        "catch_up": bool(r.get("catch_up", True)),
        "backfill_mode": r.get("backfill_mode", _BACKFILL_DEFAULT),
        "mode": r.get("mode", _HABIT_MODE_DEFAULT),
        # ---- 配額（§4.2）。非配額的條目 `quota` 是 None，前端用它一句話分流 ----
        "period": r.get("period", _PERIOD_DEFAULT),
        "times": max(1, int(r.get("times") or _TIMES_DEFAULT)),
        "cap_per_day": bool(r.get("cap_per_day")),
        "each_min": int(r.get("each_min") or 0),      # 次數配額：每次大約多久（「時間夠不夠」用）
        "nudges": dict(r.get("nudges") or _NUDGE_DEFAULT),
        "nudge_days": int(r.get("nudge_days") or 0),
        "quota": quota,
        "nudge": nudge or "",
        # ---- 時間配額（§4.4）。`unit="count"`＝原本的次數配額（或根本不是配額）----
        "unit": "minutes" if is_time(r) else _UNIT_DEFAULT,
        "floor_min": int(r.get("floor_min") or 0),
        "target_min": int(r.get("target_min") or 0),
        "minutes": dict(r.get("minutes") or {}) if is_time(r) else {},
        "today_minutes": int((r.get("minutes") or {}).get(today.isoformat(), 0) or 0),
        # 非排定日也能打卡（§4.3）。灰格＝「不用做」，不是「不准記」。
        "extra_days": bool(r.get("extra_days", True)),
        "reps": dict(r.get("reps", {})),
        "today_reps": int(r.get("reps", {}).get(today.isoformat(), 0)),
        "pinned": bool(r.get("pinned", False)),
        "start_date": r.get("start_date", ""),
        "end_date": r.get("end_date", ""),
        "done_dates": list(r.get("done_dates", [])),
        "skip_dates": list(r.get("skip_dates", [])),
        "today_state": _today_state(r, today),
        "last_done": r.get("last_done", ""),
        "last_notified": r.get("last_notified", ""),
        "days_since": days_since,
        "done_today": done_today,
        "due_today": due_today,
        "missed": missed,
        "should_notify": should_notify,
        "notified_today": r.get("last_notified") == today.isoformat(),
        "next_due": (_next_due(r, today).isoformat()
                     if not due_today and _next_due(r, today) else ""),
        "desc": describe(r),
        "locked": _habit_locked(r, today),
        # 承諾期狀態：前端用這三個決定 ✓/✗ 要不要 disable，別自己再算一次日期。
        # ✓ 起跑前仍可按（提早做到算超前進度）、✗ 不行；結束後兩個都關。
        "not_started": not_started,
        "period_ended": period_ended,
        "days_to_start": (start_d - today).days if not_started and start_d else 0,
        "can_check": not period_ended,
        # 配額模式沒有 ✗：「跳過今天」預設了今天有義務，而配額模式今天本來就沒有義務。
        # 這一期不想做就是不做，期末沒做滿照實記一筆——不需要一顆按鈕來宣告。
        "can_cancel": not (not_started or period_ended or r.get("repeat") == "quota"),
        # 舊欄位：報表與舊前端還在讀，留著別拿掉
        "overdue": due_today,
    }
    if r.get("kind") == "habit":
        out["stats"] = habit_stats(r, today)
        out["matrix"] = habit_matrix(r, today, now)
    return out


def _passed_grace(now: datetime, at: time | None, grace_min: int = 90) -> bool:
    """沒有 catch_up 的提醒，超過 N 分鐘就當作「錯過了，別再吵」。

    倒垃圾那種——垃圾車走了就是走了，再跳提醒只是噪音。
    """
    if at is None:
        return False
    target = datetime.combine(now.date(), at)
    return (now - target) > timedelta(minutes=grace_min)


def list_routines() -> list[dict]:
    """全部提醒＋算好的狀態。排序：今天該做的排前面，再來按時間。"""
    now = clock.now()
    out = [_view(r, now) for r in _load()["routines"]]
    out.sort(key=lambda r: (not r["due_today"], r["at"] or "99:99", r["name"]))
    return out


_MISSING = object()


def _clean_time(item: dict, rec: dict, kind: str, repeat: str) -> dict:
    """時間配額的欄位（`unit`／`floor_min`／`target_min`）。回要寫進紀錄的那幾格。
    改回次數時只換 `unit`——分鐘紀錄跟底線**留著不刪**（那是你真的做過的事，跟 `reps` 同一條規矩）。"""
    unit = str(item.get("unit", rec.get("unit", _UNIT_DEFAULT)) or _UNIT_DEFAULT).strip().lower()
    unit = {"min": "minutes", "mins": "minutes", "minute": "minutes", "分": "minutes",
            "分鐘": "minutes", "times": "count", "次": "count"}.get(unit, unit)
    if unit not in UNIT_IDS:
        raise ValueError(f"未知的單位：{unit}（count＝次、minutes＝分鐘）")
    if unit == "count":
        return {"unit": "count"}
    if kind != "habit" or repeat != "quota":
        raise ValueError("用時間算的要搭配「想養成」＋「一期幾次」——一期花多少時間、哪天做都行。")

    def mins(key: str, alias: str) -> int:
        raw = item[key] if key in item else item.get(alias, _MISSING)
        if raw is _MISSING:
            return int(rec.get(key) or 0)
        if raw in (None, "", 0, "0"):
            return 0
        n = parse_minutes(raw)
        if n is None:
            raise ValueError(f"看不懂這個時間：{raw!r}（可以寫 90、90m、1.5h、1h30m）")
        return n

    floor, target = mins("floor_min", "floor"), mins("target_min", "target")
    if floor <= 0:
        raise ValueError("用時間算的習慣要有底線：一期至少幾分鐘（例如 90 或 1.5h）")
    if max(floor, target) > MAX_PERIOD_MIN:
        raise ValueError(f"一期最多設 {MAX_PERIOD_MIN // 60} 小時")
    if target and target <= floor:
        raise ValueError("目標要比底線多（不想設目標就留白）")
    return {"unit": "minutes", "floor_min": floor, "target_min": target}


def _clean_each(raw) -> int:
    """「每次大約多久」：30、30m、1h、1.5h……留白／0／off＝沒估（0）。認不出來就講，不猜。"""
    if raw in (None, "", 0, "0") or str(raw).strip().lower() in ("off", "none", "不用", "清掉"):
        return 0
    n = parse_minutes(raw)
    if n is None:
        raise ValueError(f"看不懂每次大約多久：{raw!r}（可以寫 30、30m、1h、1.5h）")
    if n > MAX_DAY_MIN:
        raise ValueError("每次最多估 24 小時")
    return n


def upsert(item: dict) -> dict:
    """新增或更新。只認得的欄位會被寫進去，其餘忽略。
    habit 的承諾期 start/end 一旦設定就不可改（承諾鎖）。"""
    name = str(item.get("name", "")).strip()
    if not name:
        raise ValueError("提醒需要一個名字")
    data = _load()
    rid = str(item.get("id") or "").strip() or uuid.uuid4().hex[:8]
    rec = next((r for r in data["routines"] if r["id"] == rid), None)
    is_new = rec is None
    if rec is None:
        rec = {"id": rid, "last_done": "", "last_notified": ""}
        data["routines"].append(rec)
    _migrate(rec)

    kind = str(item.get("kind", rec.get("kind", "reminder")))
    if kind not in KIND_IDS:
        raise ValueError(f"未知的追蹤類型：{kind}")
    repeat = str(item.get("repeat", rec["repeat"]))
    if repeat not in REPEAT_IDS:
        raise ValueError(f"未知的週期：{repeat}")
    at = str(item.get("at", rec.get("at", ""))).strip()
    if at and _parse_time(at) is None:
        raise ValueError(f"時間格式要像 20:00，收到的是：{at}")
    weekdays = item.get("weekdays", rec.get("weekdays"))
    weekdays = sorted({int(d) for d in (weekdays or []) if 0 <= int(d) <= 6})
    if repeat == "weekly" and not weekdays:
        raise ValueError("每週提醒至少要選一個星期幾")
    backfill_mode = str(item.get("backfill_mode", rec.get("backfill_mode", _BACKFILL_DEFAULT)))
    backfill_mode = _BACKFILL_ALIAS.get(backfill_mode, backfill_mode)
    if backfill_mode not in BACKFILL_MODE_IDS:
        raise ValueError(f"未知的補打卡模式：{backfill_mode}")
    habit_mode = str(item.get("mode", rec.get("mode", _HABIT_MODE_DEFAULT)))
    if habit_mode not in HABIT_MODE_IDS:
        raise ValueError(f"未知的打卡方式：{habit_mode}")
    period = str(item.get("period", rec.get("period", _PERIOD_DEFAULT)))
    if period not in PERIOD_IDS:
        raise ValueError(f"未知的期間單位：{period}")
    try:
        times = int(item.get("times", rec.get("times", _TIMES_DEFAULT)))
    except (TypeError, ValueError):
        raise ValueError("一期要做幾次請填數字")
    if repeat == "quota" and not 1 <= times <= MAX_TIMES:
        raise ValueError(f"一期的次數要在 1～{MAX_TIMES} 之間")
    if repeat == "quota" and kind != "habit":
        # 純提醒沒有打卡，配額（做幾次）就無從算起——與其存一筆永遠不會動的資料，不如講清楚。
        raise ValueError("「一期幾次」要搭配「想養成」——純提醒沒有打卡，次數算不出來。")
    nudges = item.get("nudges", rec.get("nudges"))
    if not isinstance(nudges, dict):
        nudges = dict(_NUDGE_DEFAULT)
    nudges = {k: bool(nudges.get(k, _NUDGE_DEFAULT[k])) for k in NUDGE_IDS}
    timed = _clean_time(item, rec, kind, repeat)       # 時間配額（§4.4）；先驗完再寫，失敗不留半成品
    each_min = _clean_each(item.get("each_min", item.get("each", rec.get("each_min", 0))))
    importance = str(item.get("importance", rec.get("importance", "core")) or "core").strip().lower()
    if importance not in IMPORTANCE_IDS:
        raise ValueError(f"重要度只有 core（核心）／bonus（加分），收到的是：{importance}")
    habit_kind = str(item.get("habit_kind", rec.get("habit_kind", "build")) or "build").strip().lower()
    if habit_kind not in HABIT_KIND_IDS:
        raise ValueError(f"型態只有 build（養成）／bonus（加分）／nudge（提醒），收到的是：{habit_kind}")
    rec.update({
        **timed,
        "name": name[:40],
        "note": str(item.get("note", rec.get("note", "")))[:200],
        "kind": kind,
        "repeat": repeat,
        "every_days": max(1, int(item.get("every_days", rec.get("every_days", 7)))),
        "weekdays": weekdays,
        "nth": int(item.get("nth", rec.get("nth", 1))),
        "nth_weekday": int(item.get("nth_weekday", rec.get("nth_weekday", 0))),
        "at": at,
        "catch_up": bool(item.get("catch_up", rec.get("catch_up", True))),
        "backfill_mode": backfill_mode,
        # 打卡方式可以隨時改（跟承諾期不同，它不是承諾）。改成 once 時 `reps` **留著不刪**
        # ——那是你真的做過的事；哪天改回加碼，數字還在。
        # ⚠ 配額**一律用加碼記帳**：次數就是這個模式的貨幣，`once` 在這裡沒有意義
        #   （做 3 次卻只記得住 1 次，本期進度就永遠算不對）。
        "mode": "reps" if repeat == "quota" else (habit_mode if kind == "habit" else _HABIT_MODE_DEFAULT),
        "period": period,
        "times": max(1, min(MAX_TIMES, times)),
        "cap_per_day": bool(item.get("cap_per_day", rec.get("cap_per_day", False))),
        "nudges": nudges,
        "nudge_days": max(0, int(item.get("nudge_days", rec.get("nudge_days", 0)) or 0)),
        "extra_days": bool(item.get("extra_days", rec.get("extra_days", True))),
        "why": str(item.get("why", rec.get("why", "")) or "").strip()[:MAX_WHY],
        "importance": importance,
        "habit_kind": habit_kind,
        "each_min": each_min,
    })

    if kind == "habit":
        # 承諾期：預設一旦設過就鎖住（＝「撐住」，對養成好習慣是有幫助的）。但人生會變動，硬鎖不好，
        # 所以帶 `force`（前端連按幾次「我堅持要改」後）就允許改動承諾期——尊重使用者最終的決定。
        force = bool(item.get("force"))
        if force or not rec.get("start_date"):
            new_start = str(item.get("start_date", "")).strip()
            rec["start_date"] = new_start or rec.get("start_date") or clock.today().isoformat()
        if force or not rec.get("end_date"):
            new_end = str(item.get("end_date", "")).strip() or rec.get("end_date", "")
            if not new_end:
                raise ValueError("想養成的習慣要設一個結束日（承諾期）")
            start_d = _parse_date(rec["start_date"])
            end_d = _parse_date(new_end)
            if not end_d:
                raise ValueError(f"結束日格式要像 2030-01-31，收到的是：{new_end}")
            if start_d and end_d < start_d:
                raise ValueError("結束日不能早於開始日")
            rec["end_date"] = new_end
    else:
        # 從 habit 改回 reminder：清掉承諾期（reminder 沒有訓練期）
        rec["start_date"] = ""
        rec["end_date"] = ""

    _save(data)
    return _view(rec, clock.now())


def _day_end(day: date) -> datetime:
    return datetime.combine(day, time.max)


def _red_after(day: date, mode: str) -> datetime:
    """排定日沒打卡、過了這個時間點就誠實轉紅（missed）。
    none＝當天一結束就紅（沒有寬限）；其餘＝該日結束後再給 12 小時才轉紅。
    這是『顯示』門檻，跟『還能不能補』（_makeup_deadline）刻意分開——
    d3/always 會先紅、但還點得動，紅只是誠實提醒。"""
    if mode == "none":
        return _day_end(day)
    return _day_end(day) + timedelta(hours=_MAKEUP_HOURS)


def _makeup_deadline(day: date, mode: str) -> datetime:
    """補打卡的最後期限（過了就真的點不動了）：
    none＝該日結束；h12＝＋12 小時；d3＝＋3 天；always＝永不到期。"""
    if mode == "none":
        return _day_end(day)
    if mode == "d3":
        return _day_end(day) + timedelta(days=_MAKEUP_DAYS)
    if mode == "always":
        return datetime.max
    return _day_end(day) + timedelta(hours=_MAKEUP_HOURS)  # h12（含未知值的保底）


def _can_makeup(day: date, now: datetime, mode: str = _BACKFILL_DEFAULT) -> bool:
    return now <= _makeup_deadline(day, mode)


def check(rid: str, day_iso: str = "", state: str = "done") -> dict:
    """打卡三態：done ✓ / cancel ✗ / none 清空，加碼兩動作：rep ＋1 / unrep －1。
    回傳更新後的 view。
    - 不能打未來。
    - habit 的 ✓（含 rep）只能在補打卡窗內（依 `backfill_mode`：h12／d3／always；
      reminder 不受此限，補提醒走 catch_up）。"""
    if state not in CHECK_STATES:
        raise ValueError(f"未知的打卡狀態：{state}")
    day = _parse_date(day_iso) or clock.today()
    today = clock.today()
    now = clock.now()
    if day > today:
        raise ValueError("不能打未來的卡")

    data = _load()
    rec = next((r for r in data["routines"] if r["id"] == rid), None)
    if rec is None:
        raise KeyError("not found")
    _migrate(rec)

    # 承諾期外的打卡：起跑前只放行 ✓（提早做到算超前，使用者要的），✗「跳過今天」
    # 在還沒開始的日子沒有意義；承諾期結束後兩者都關掉。前端也會 disable 按鈕，
    # 但真正把關在這裡——UI 能點什麼是提示，不是保證。
    start_d, end_d = _commitment_window(rec)
    if end_d and day > end_d:
        raise ValueError(f"承諾期已在 {end_d.isoformat()} 結束了，想繼續就開一條新的習慣。")
    if start_d and day < start_d and state == "cancel":
        raise ValueError(f"{start_d.isoformat()} 才起跑，還沒開始的日子沒有「跳過」可言。")

    if is_time(rec) and state != "none":
        # 時間配額記的是分鐘：「做了」要講做了多久，不然本期進度算不出來。清掉（none）照常。
        raise ValueError(f"「{rec['name']}」是用時間記的——做了多久？"
                         "（habit.checkin --minutes 45；也可以寫 1h、1.5h）")
    if state in ("rep", "unrep") and rec.get("mode") != "reps":
        raise ValueError("這條習慣不是「可以加碼」模式——先在編輯裡把打卡方式改成加碼。")

    # 配額模式走自己的補記規則，**不看 backfill_mode**：本期內哪一天都能記
    # （「哪天做都行」本來就包含「週五才想起來週一做過」），但**跨期一律擋掉**——
    # 上一期關帳就是關帳。能回頭補上一期，就等於能把沒做滿的歷史改掉，
    # 那條「不結轉、不欠債」的安全帶也會跟著失效（安全帶 ①）。
    if rec.get("repeat") == "quota":
        p_start, p_end = _period_bounds(rec.get("period", _PERIOD_DEFAULT), today)
        if not (p_start <= day <= p_end):
            unit = "這個月" if rec.get("period") == "month" else "這週"
            raise ValueError(f"{unit}以外的日子不能改了——上一期已經關帳，"
                             f"沒做滿的不會結轉、也不用補回來。")
    mode = rec.get("backfill_mode", _BACKFILL_DEFAULT)
    if (rec.get("kind") == "habit" and rec.get("repeat") != "quota"
            and state in ("done", "rep") and not _can_makeup(day, now, mode)):
        raise ValueError({
            "none": "這個習慣設成「不可補打卡」——只能當天打，今天已經過了。",
            "h12": "超過補打卡時間了——只能補該日結束後 12 小時內的卡。",
            "d3": "超過補打卡時間了——只能補 3 天內的卡。",
        }.get(mode, "超過補打卡時間了。"))

    iso = day.isoformat()
    done = set(rec.get("done_dates", []))
    skip = set(rec.get("skip_dates", []))
    reps = dict(rec.get("reps", {}))
    n = int(reps.get(iso, 0))
    done.discard(iso)
    skip.discard(iso)
    if state == "done":
        done.add(iso)
        # 加碼模式下按 ✓ ＝「今天有做到」，次數至少是 1（本來就有更多次就不動它）。
        if rec.get("mode") == "reps":
            reps[iso] = max(1, n)
    elif state == "cancel":
        skip.add(iso)
        reps.pop(iso, None)          # 打了 ✗ 還留著次數，那張表就自相矛盾
    elif state == "none":
        reps.pop(iso, None)          # 清掉打卡＝連同加碼一起清（不留孤兒數字）
        if isinstance(rec.get("minutes"), dict):
            rec["minutes"].pop(iso, None)      # 時間配額：那天記的分鐘一起清
    elif state == "rep":
        # 再做一次。**第一次按也算打卡**——這樣「今天做了 3 次」不需要先按 ✓ 再按三次 ＋1，
        # 少一個沒有意義的步驟（而 done_dates 照樣被寫進去，所有既有統計原封不動）。
        done.add(iso)
        reps[iso] = min(MAX_REPS, n + 1)
    elif state == "unrep":
        # 按錯了退一次。退到 0 ＝ 回到「還沒打卡」，而不是留一個 0 在那裡——
        # 「按錯」應該能完全復原，這是敢按下去的前提。
        if n <= 1:
            reps.pop(iso, None)
        else:
            reps[iso] = n - 1
            done.add(iso)
    rec["done_dates"] = sorted(done)
    rec["skip_dates"] = sorted(skip)
    rec["reps"] = dict(sorted(reps.items()))
    rec["last_done"] = rec["done_dates"][-1] if rec["done_dates"] else ""
    _save(data)
    return _view(rec, now)


def log_minutes(rid: str, minutes, day_iso: str = "") -> dict:
    """時間配額的打卡：替 `day`（預設今天）**加上** `minutes` 分鐘——同一天記好幾次會加起來
    （跟加碼的 ＋1 同一個道理）。負的＝按錯了扣回來，扣到 0 就等於那天沒記。回更新後的 view，
    多帶 `logged`（這次加了幾分）與 `day_minutes`（那天一共幾分）。

    規則跟次數配額一樣：不能記未來、承諾期外不行、**只能記這一期**（上一期關帳就是關帳——
    能回頭補，就等於能把沒做到的歷史改掉，安全帶 ① 會失效）。"""
    delta = parse_minutes(minutes, allow_negative=True)
    if delta is None:
        raise ValueError(f"看不懂做了多久：{minutes!r}（可以寫 45、45m、1h、1.5h；按錯了用 -15 扣回來）")
    day = _parse_date(day_iso) or clock.today()
    today = clock.today()
    now = clock.now()
    if day > today:
        raise ValueError("不能記未來的時間")
    data = _load()
    rec = next((r for r in data["routines"] if r["id"] == rid), None)
    if rec is None:
        raise KeyError("not found")
    _migrate(rec)
    if not is_time(rec):
        raise ValueError(f"「{rec['name']}」不是用時間算的習慣——直接打卡就好（不用給分鐘）")
    _start, end_d = _commitment_window(rec)
    if end_d and day > end_d:
        raise ValueError(f"承諾期已在 {end_d.isoformat()} 結束了，想繼續就開一條新的習慣。")
    p_start, p_end = _period_bounds(rec.get("period", _PERIOD_DEFAULT), today)
    if not (p_start <= day <= p_end):
        raise ValueError(f"{_period_word(rec.get('period'))}以外的日子不能改了——上一期已經關帳，"
                         "沒做到的不會結轉、也不用補回來。")
    iso = day.isoformat()
    mins = dict(rec.get("minutes") or {})
    old = int(mins.get(iso, 0) or 0)
    new = max(0, min(MAX_DAY_MIN, old + delta))
    done = set(rec.get("done_dates", []))
    if new:
        mins[iso] = new
        done.add(iso)                         # 有記時間的那天＝有做（矩陣、連續、「今天做了」照舊）
    else:
        mins.pop(iso, None)
        done.discard(iso)
    rec["minutes"] = dict(sorted(mins.items()))
    rec["done_dates"] = sorted(done)
    rec["last_done"] = rec["done_dates"][-1] if rec["done_dates"] else ""
    _save(data)
    return {**_view(rec, now), "logged": new - old, "day_minutes": new, "logged_date": iso}


def mark_done(rid: str) -> bool:
    """相容舊呼叫（報表/舊前端的 ✓）：等同今天打 done。"""
    try:
        check(rid, "", "done")
        return True
    except KeyError:
        return False


def mark_notified(rid: str) -> bool:
    """記下「今天叫過了」，避免同一天一直跳。"""
    data = _load()
    for r in data["routines"]:
        if r["id"] == rid:
            _migrate(r)
            r["last_notified"] = clock.today().isoformat()
            _save(data)
            return True
    return False


def pin(rid: str, pinned: bool) -> dict | None:
    """釘/取消釘到打卡 dashboard 最上面（跟 ProjDeck／工具快捷列同一套語言）。"""
    data = _load()
    rec = next((r for r in data["routines"] if r["id"] == rid), None)
    if rec is None:
        return None
    _migrate(rec)
    rec["pinned"] = bool(pinned)
    _save(data)
    return _view(rec, clock.now())


def _habit_rec(data: dict, rid: str) -> dict:
    rec = next((r for r in data["routines"] if r["id"] == rid), None)
    if rec is None:
        raise KeyError("not found")
    _migrate(rec)
    if rec.get("kind") != "habit":
        raise ValueError(f"「{rec['name']}」是純提醒，沒有暫停／這期夠了可言")
    return rec


def pause(rid: str, on: bool = True, until: str = "") -> dict:
    """⏸ 暫停／接著做。暫停**不算失敗**：暫停的日子不排、不過期、不提醒、不打斷連續。
    承諾期**不動**（暫停不是改承諾；刪除保護、三道「我堅持」照舊）。

    - on：從今天起暫停；`until`（ISO）＝哪天以後回來（含那天都算暫停），空＝自己說回來才回來。
      已經在暫停中＝只改 until。
    - off：今天起回來。今天才開始的那段直接拿掉；之前開始的那段收在昨天。打卡紀錄一筆都不動。"""
    today = clock.today()
    until_d = _parse_date(until) if until else None
    if until and not until_d:
        raise ValueError(f"看不懂哪天回來：{until!r}（寫 2030-01-31 這樣）")
    if on and until_d and until_d < today:
        raise ValueError("回來的日子不能早於今天")
    data = _load()
    rec = _habit_rec(data, rid)
    pauses = [dict(p) for p in rec.get("pauses") or [] if isinstance(p, dict)]
    cur = next((p for p in pauses if _paused_on({"pauses": [p]}, today)), None)
    if on:
        if cur:
            cur["until"] = until_d.isoformat() if until_d else ""
        else:
            pauses.append({"from": today.isoformat(), "until": until_d.isoformat() if until_d else ""})
    elif cur:
        if cur["from"] >= today.isoformat():
            pauses.remove(cur)
        else:
            cur["until"] = (today - timedelta(days=1)).isoformat()
    rec["pauses"] = sorted(pauses, key=lambda p: p.get("from", ""))
    _save(data)
    return _view(rec, clock.now())


def set_enough(rid: str, on: bool = True) -> dict:
    """🙆 這一期夠了：從今天到這一期結束（一期幾次的照它的週／月，其他＝這週）收起來、不再提醒，
    剩下的日子不算沒做。off＝收回（這期又要了）。不動承諾期、不動打卡紀錄；下一期自動回來。"""
    today = clock.today()
    data = _load()
    rec = _habit_rec(data, rid)
    key = _period_key(_enough_period(rec), today)
    enough = dict(rec.get("enough") or {})
    if on:
        enough.setdefault(key, today.isoformat())
    else:
        enough.pop(key, None)
    rec["enough"] = dict(sorted(enough.items()))
    _save(data)
    return {**_view(rec, clock.now()), "enough_key": key,
            "enough_until": _period_bounds(_enough_period(rec), today)[1].isoformat()}


def raw(rid: str) -> dict | None:
    """一條的原始紀錄（複本；刪掉之前留一份給呼叫端，跟 task.delete 的 `deleted` 同一個用途）。"""
    rec = next((r for r in _load()["routines"] if r["id"] == rid), None)
    return json.loads(json.dumps(rec)) if rec else None


def find_habit(ref: str) -> dict:
    """id 或名字 → 一條習慣（`_view()`）。指令與畫面共用，免得兩邊對到不同筆。

    順序：id 完全一樣 → 名字完全一樣（好幾條同名時，留**今天在承諾期內**的那一條——例如「早睡」
    分兩段，一段接一段）→ 名字裡有這幾個字、只有一條。對不到或對到好幾條就 raise LookupError，
    訊息裡每一條都帶 id 與承諾期（叫人給 id，就要把 id 列給他）。"""
    q = str(ref or "").strip()
    if not q:
        raise LookupError("要哪一個習慣？")
    rows = [r for r in list_routines() if r.get("kind") == "habit"]
    hit = next((r for r in rows if r["id"] == q), None)
    if hit:
        return hit
    same = [r for r in rows if r["name"].strip().lower() == q.lower()]
    live = [r for r in same if not r["not_started"] and not r["period_ended"]]
    if len(same) == 1 or len(live) == 1:
        return same[0] if len(same) == 1 else live[0]
    part = same or [r for r in rows if q.lower() in r["name"].lower()]
    if len(part) == 1:
        return part[0]
    if not part:
        raise LookupError(f"找不到叫「{q}」的習慣")
    names = "、".join(f'「{r["name"]}」（id {r["id"]}，{r.get("start_date") or "?"}～{r.get("end_date") or "?"}）'
                      for r in part[:6])
    raise LookupError(f"「{q}」對到不只一個習慣：{names}——給 id")


def delete(rid: str, force: bool = False) -> bool:
    """刪除。承諾期未結束的 habit 預設擋下來（承諾鎖，raise ValueError）；
    帶 `force=True`（前端連按幾次「我堅持要刪」後）就放行——鐵了心要刪還是尊重使用者。"""
    data = _load()
    rec = next((r for r in data["routines"] if r["id"] == rid), None)
    if rec is None:
        return False
    _migrate(rec)
    if _habit_locked(rec, clock.today()) and not force:
        raise ValueError("承諾期還沒結束，撐住！週期結束後才能刪掉這條習慣。")
    data["routines"] = [r for r in data["routines"] if r["id"] != rid]
    _save(data)
    return True
