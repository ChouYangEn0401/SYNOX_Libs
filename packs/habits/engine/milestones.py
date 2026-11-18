"""里程碑旅程：帶期限、每日陪跑的「成長旅程」（例：30 天做一款小遊戲、每週一支影片）。

跟習慣（routines.py）刻意分開：習慣打卡養成連鎖，旅程陪你走完一個有期限的目標，
形狀是「目標＋期限＋每一格計畫＋成長紀錄」，所以自成一份資料。

一個旅程的核心是一串 cells（混合序列），每一格＝旅程上的一天：
  - `task`  ——排定好的任務日（藍圖，建立後標題鎖住）。
  - `rest`  ——插進來的休息／岔路（🍜😴🍻…）。不是失敗，只是那天照顧自己。
  - `extra` ——臨時冒出、要額外花時間的一件事。也可打卡完成。

錯過不等於取消：某天沒做，不把任務標失敗，而是插一格 rest／extra，後面的任務順延
（旅程從 30 天長成 31、32…天）。

溢時上限：建立時設 `overtime_days`（最多可拖幾天）。可以一直插入順延；但總長超過
`plan_len + overtime_days` 後，每步的 `reward_mult` 開始遞減（下限 0.35、恆 > 0）——
提醒別拖太久，但永遠不歸零、不羞辱。

衍生欄位（不存、每次算）：task 的編號 `n`、每格 `date`（start + index 天）、目前走到哪 `is_current`、
以及 `stats`。
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta

from . import clock, storage

DOC = "milestones"      # storage 裡這份資料的名字

MAX_TEXT = 400

CADENCES = [
    {"id": "daily", "label": "每天一步", "hint": "每天推進一格"},
    {"id": "weekly", "label": "每週一步", "hint": "每週推進一格，例如每週一支影片"},
]
CADENCE_IDS = {c["id"] for c in CADENCES}

# 錯過那天貼的小貼圖（畫面的貼圖選單用；詞彙由這裡給）
STICKERS = [
    ["🍜", "吃飯"], ["🎬", "看電影"], ["😴", "好累"], ["🍻", "聚餐"],
    ["🤒", "生病"], ["💼", "加班"], ["✈️", "出去玩"], ["💤", "休息"],
]

# 等級（鼓勵用）。門檻＝完成的任務數。
LEVELS = [
    [0, "啟程者"], [3, "探索者"], [7, "實踐者"],
    [14, "堅持者"], [24, "衝刺者"], [40, "抵達者"],
]

_CELL_TYPES = {"task", "rest", "extra"}

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _norm_time(s) -> str:
    """把使用者填的每日提醒時間正規化成 'HH:MM'（零補）；空字串或不合法＝不提醒（"")。"""
    m = _TIME_RE.match(str(s or "").strip())
    if not m:
        return ""
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def meta() -> dict:
    """UI 要畫的詞彙（前端不要自己再寫一份）。"""
    return {"cadences": CADENCES, "stickers": STICKERS}


# ---------------------------------------------------------------- store
def _load() -> dict:
    data = {"milestones": []}
    stored = storage.load(DOC, None)
    if isinstance(stored, dict):
        data.update(stored)
    for m in data["milestones"]:
        _migrate(m)
    return data


def _save(data: dict) -> None:
    storage.save(DOC, data)


def _migrate(m: dict) -> None:
    m.setdefault("id", uuid.uuid4().hex[:8])
    m.setdefault("name", "未命名旅程")
    m.setdefault("goal", "")
    m.setdefault("emoji", "🗺️")
    m.setdefault("cadence", "daily")
    if m["cadence"] not in CADENCE_IDS:
        m["cadence"] = "daily"
    m.setdefault("start_date", clock.today().isoformat())
    m.setdefault("overtime_days", 7)
    # 每日提醒（選填）：'HH:MM' 才提醒，''＝只上月曆、不主動叫（預設）。
    m["remind_time"] = _norm_time(m.get("remind_time", ""))
    m.setdefault("created_at", clock.now().isoformat(timespec="seconds"))
    m.setdefault("updated_at", m["created_at"])
    m.setdefault("cells", [])
    for c in m["cells"]:
        _migrate_cell(c)
    # plan_len = 建立時的藍圖任務數（不含之後插入的）。舊資料回推：非 ins 的 task 數。
    if "plan_len" not in m:
        m["plan_len"] = sum(1 for c in m["cells"] if c["type"] == "task" and not c.get("ins"))


def _migrate_cell(c: dict) -> None:
    c.setdefault("type", "task")
    if c["type"] not in _CELL_TYPES:
        c["type"] = "task"
    c.setdefault("ins", False)
    if c["type"] == "rest":
        c.setdefault("sticker", "💤")
        c.setdefault("label", "休息")
    else:  # task / extra
        c.setdefault("title", "")
        c.setdefault("desc", "")
        c.setdefault("state", "pending")          # pending | done
        if c["state"] not in ("pending", "done"):
            c["state"] = "pending"
        c.setdefault("notes", [])
        c.setdefault("mood", "")
    if c["type"] == "extra":
        c.setdefault("icon", "⚡")


def _find(data: dict, mid: str) -> dict:
    m = next((x for x in data["milestones"] if x["id"] == mid), None)
    if m is None:
        raise KeyError(mid)
    return m


def _touch(m: dict) -> None:
    m["updated_at"] = clock.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- compute
def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(str(s))
    except (ValueError, TypeError):
        return clock.today()


def _current_index(cells: list[dict]) -> int | None:
    """目前走到哪＝第一個還沒完成的 work cell（task/extra）。"""
    for i, c in enumerate(cells):
        if c["type"] in ("task", "extra") and c["state"] != "done":
            return i
    return None


def _reward_mult(m: dict) -> float:
    over = max(0, len(m["cells"]) - (int(m.get("plan_len", 0)) + int(m.get("overtime_days", 7))))
    return max(0.35, round(1 - over * 0.07, 2))


def _stats(m: dict) -> dict:
    cells = m["cells"]
    tasks = [c for c in cells if c["type"] == "task"]
    done = sum(1 for c in tasks if c["state"] == "done")
    total = len(tasks)
    pct = round(done / total * 100) if total else 0

    cur = _current_index(cells)
    end = cur if cur is not None else len(cells)
    streak = 0
    for i in range(end - 1, -1, -1):
        c = cells[i]
        if c["type"] != "task":
            continue
        if c["state"] == "done":
            streak += 1
        else:
            break

    mult = _reward_mult(m)
    level, name = 1, LEVELS[0][1]
    for k, (thr, nm) in enumerate(LEVELS):
        if done >= thr:
            level, name = k + 1, nm

    start = _parse_date(m["start_date"])
    deadline = start + timedelta(days=int(m.get("plan_len", total)) + int(m.get("overtime_days", 7)))
    days_left = (deadline - clock.today()).days

    return {
        "done": done, "total": total, "pct": pct, "streak": streak,
        "reward_mult": mult, "xp": round(done * 100 * mult),
        "level": level, "level_name": name,
        "inserts": sum(1 for c in cells if c.get("ins")),
        "over": max(0, len(cells) - (int(m.get("plan_len", 0)) + int(m.get("overtime_days", 7)))),
        "hard_deadline": deadline.isoformat(), "days_left": days_left,
        "cells": len(cells),
    }


def _view(m: dict) -> dict:
    """完整視圖：每格帶 n / date / is_current，外加 stats。前端就靠這個畫。"""
    start = _parse_date(m["start_date"])
    cur = _current_index(m["cells"])
    n = 0
    out_cells = []
    for i, c in enumerate(m["cells"]):
        cc = dict(c)
        cc["date"] = (start + timedelta(days=i)).isoformat()
        cc["is_current"] = (i == cur)
        if c["type"] == "task":
            n += 1
            cc["n"] = n
            cc["check"] = (n % 7 == 0)     # 每 7 個任務一個週里程碑
        out_cells.append(cc)
    return {
        "id": m["id"], "name": m["name"], "goal": m["goal"], "emoji": m["emoji"],
        "cadence": m["cadence"], "start_date": m["start_date"],
        "overtime_days": m["overtime_days"], "plan_len": m.get("plan_len", 0),
        "remind_time": m.get("remind_time", ""),
        "updated_at": m["updated_at"],
        "cells": out_cells, "stats": _stats(m),
    }


def _days_since_iso(s) -> int:
    """`updated_at` 是 ISO 字串。壞掉就當 0——「多久沒動」寧可少講也不能亂講。"""
    try:
        return max(0, (clock.today() - datetime.fromisoformat(str(s)).date()).days)
    except (TypeError, ValueError):
        return 0


def _summary(m: dict) -> dict:
    """清單卡片用的濃縮版（不含整串 cells）。"""
    s = _stats(m)
    cur = _current_index(m["cells"])
    cur_title = ""
    if cur is not None and m["cells"][cur]["type"] in ("task", "extra"):
        cur_title = m["cells"][cur].get("title", "")
    return {
        "id": m["id"], "name": m["name"], "emoji": m["emoji"], "goal": m["goal"],
        "current": cur_title, "remind_time": m.get("remind_time", ""),
        "idle_days": _days_since_iso(m.get("updated_at")),
        "done": s["done"], "total": s["total"], "pct": s["pct"],
        "streak": s["streak"], "days_left": s["days_left"], "level_name": s["level_name"],
    }


# ---------------------------------------------------------------- CRUD
def list_all() -> list[dict]:
    out = [_summary(m) for m in _load()["milestones"]]
    out.sort(key=lambda x: x["pct"] < 100)   # 未完成的排前面
    return out


def get(mid: str) -> dict:
    return _view(_find(_load(), mid))


def occurrences(start: str, end: str) -> list[dict]:
    """給日曆之類的外部畫面用（目前沒有任何東西接它）：落在 [start, end]（含端點）內的 milestone **任務格**。
    只投影 task 格（藍圖上排定的那些日子），rest/extra 是臨時插入的、不上月曆。
    日期沿用 _view 的算法（start + index 天），跟 milestone 頁看到的一致。"""
    lo, hi = _parse_date(start), _parse_date(end)
    if not lo or not hi:
        return []
    out: list[dict] = []
    for m in _load()["milestones"]:
        v = _view(m)
        for i, c in enumerate(v["cells"]):
            if c["type"] != "task":
                continue
            d = _parse_date(c["date"])
            if d and lo <= d <= hi:
                out.append({
                    "date": c["date"],
                    "milestone_id": v["id"],
                    # idx＝在 cells 裡的位置，complete()/edit_cell() 都用它定位。
                    "idx": i,
                    "emoji": v["emoji"],
                    "name": v["name"],
                    "title": c.get("title", ""),
                    # 格子的完成狀態在 `state`，不是 `done`。
                    "done": c.get("state") == "done",
                    "is_current": bool(c.get("is_current")),
                })
    return out


def create(payload: dict) -> dict:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("旅程要有一個名字")
    cadence = str(payload.get("cadence", "daily"))
    if cadence not in CADENCE_IDS:
        raise ValueError(f"未知的節奏：{cadence}")
    start = str(payload.get("start_date", "")).strip() or clock.today().isoformat()
    _parse_date(start)  # validate-ish

    plan = payload.get("plan") or []
    cells = []
    for step in plan:
        if isinstance(step, str):
            step = {"title": step}
        cells.append({"type": "task", "title": str(step.get("title", ""))[:MAX_TEXT],
                      "desc": str(step.get("desc", ""))[:MAX_TEXT], "state": "pending",
                      "notes": [], "mood": "", "ins": False})

    data = _load()
    m = {
        "id": uuid.uuid4().hex[:8], "name": name[:MAX_TEXT],
        "goal": str(payload.get("goal", ""))[:MAX_TEXT], "emoji": str(payload.get("emoji", "🗺️"))[:8] or "🗺️",
        "cadence": cadence, "start_date": start,
        "overtime_days": max(0, int(payload.get("overtime_days", 7))),
        "remind_time": _norm_time(payload.get("remind_time", "")),
        "cells": cells, "plan_len": len(cells),
        "created_at": clock.now().isoformat(timespec="seconds"),
        "updated_at": clock.now().isoformat(timespec="seconds"),
    }
    data["milestones"].append(m)
    _save(data)
    return _view(m)


def update(mid: str, payload: dict) -> dict:
    """只改 meta（name/goal/emoji/overtime/start）；**藍圖任務標題不從這裡改**。"""
    data = _load()
    m = _find(data, mid)
    for k in ("name", "goal", "emoji", "start_date"):
        if k in payload:
            m[k] = str(payload[k])[:MAX_TEXT]
    if "overtime_days" in payload:
        m["overtime_days"] = max(0, int(payload["overtime_days"]))
    if "remind_time" in payload:                       # ''＝清掉提醒（只上月曆）
        m["remind_time"] = _norm_time(payload["remind_time"])
    if "cadence" in payload and payload["cadence"] in CADENCE_IDS:
        m["cadence"] = payload["cadence"]
    _touch(m)
    _save(data)
    return _view(m)


def delete(mid: str) -> bool:
    data = _load()
    before = len(data["milestones"])
    data["milestones"] = [m for m in data["milestones"] if m["id"] != mid]
    if len(data["milestones"]) == before:
        return False
    _save(data)
    return True


# ---------------------------------------------------------------- cell ops
def _cell(m: dict, idx: int) -> dict:
    if idx < 0 or idx >= len(m["cells"]):
        raise IndexError(idx)
    return m["cells"][idx]


def complete(mid: str, idx: int, done: bool = True) -> dict:
    data = _load()
    m = _find(data, mid)
    c = _cell(m, idx)
    if c["type"] == "rest":
        raise ValueError("休息節點不用打卡")
    c["state"] = "done" if done else "pending"
    _touch(m)
    _save(data)
    return _view(m)


def edit_cell(mid: str, idx: int, payload: dict) -> dict:
    """成長紀錄可動的部分：心情、加一條筆記。**title/desc 藍圖不從這改**。"""
    data = _load()
    m = _find(data, mid)
    c = _cell(m, idx)
    if c["type"] == "rest":
        raise ValueError("休息節點沒有紀錄欄位")
    if "mood" in payload:
        c["mood"] = str(payload["mood"])[:MAX_TEXT]
    if payload.get("add_note"):
        c.setdefault("notes", []).append(str(payload["add_note"])[:MAX_TEXT])
    _touch(m)
    _save(data)
    return _view(m)


def insert(mid: str, idx: int, where: str, kind: str, payload: dict | None = None) -> dict:
    """在 idx 的前/後插入一格。kind: rest / extra / task。任務會重編號、後面順延。

    `blueprint=True`（只對 task 有意義）＝這是在「補藍圖」而不是事後插隊：cell 不標 `ins`、
    `plan_len` 跟著 +1。用在空旅程建立第一步、或事後補排原本計劃的一步——這樣它算進底線內、
    不會被當成溢時而縮獎勵。
    """
    payload = payload or {}
    if kind not in _CELL_TYPES:
        raise ValueError(f"未知的節點類型：{kind}")
    data = _load()
    m = _find(data, mid)
    if idx < 0 or idx > len(m["cells"]):
        raise IndexError(idx)
    at = idx + 1 if where == "after" else idx
    blueprint = bool(payload.get("blueprint")) and kind == "task"

    if kind == "rest":
        cell = {"type": "rest", "sticker": str(payload.get("sticker", "💤"))[:8],
                "label": str(payload.get("label", "休息"))[:MAX_TEXT], "ins": True}
    else:
        cell = {"type": kind, "title": str(payload.get("title", ""))[:MAX_TEXT],
                "desc": str(payload.get("desc", ""))[:MAX_TEXT], "state": "pending",
                "notes": [], "mood": "", "ins": not blueprint}
        if kind == "extra":
            cell["icon"] = "⚡"
    m["cells"].insert(at, cell)
    if blueprint:
        m["plan_len"] = int(m.get("plan_len", 0)) + 1
    _touch(m)
    _save(data)
    return _view(m)


def remove_cell(mid: str, idx: int) -> dict:
    """只能刪**插入的**節點（ins）。原始藍圖任務鎖住不能刪。"""
    data = _load()
    m = _find(data, mid)
    c = _cell(m, idx)
    if not c.get("ins"):
        raise ValueError("這是排定好的藍圖任務，鎖住了不能刪——它只是往後挪，不會消失。")
    del m["cells"][idx]
    _touch(m)
    _save(data)
    return _view(m)
