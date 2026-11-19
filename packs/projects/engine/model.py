"""專案追蹤的資料與規則：每個專案「停在哪、為什麼停、回來先做什麼」。

這個檔不碰檔案、不連網、不開程式：全部是「一包 dict ＋ 一個動作 → 改好的 dict」。存取由 pack.py 經 pack.store 做。

一筆專案刻意只有三個短答案，不是一頁 wiki：
  status  現在做到哪（最後狀態）
  next    回來的第一件事
  reason  為什麼停（分類）＋ reason_note 一句話補充；resume_at ＝ 什麼時候回來看
log 留最近幾次的快照，專案就有看得見的歷史。**不會自動刪任何東西**（放下＝只是標記；停止追蹤的先放進回收區，可以復原）。

資料形狀（pack.store 裡的一份 JSON）：
    {"projects": [...], "custom_reasons": [...], "trash": [...]}
"""

import time
import uuid
from datetime import date, datetime

LOG_KEEP = 12              # 每個專案留幾次快照：夠看出趨勢，不是資料庫
MAX_TEXT = 400
TRASH_KEEP = 10            # 停止追蹤後可以復原的筆數
COLD_DAYS = 30             # 幾天沒動算「冷掉」（程式量的，不是一個狀態）
CLEAR = "-"                # 欄位給「-」＝清掉

# 狀態的順序＝一個專案真實會走過的路：想法 → 動手 → 收工；然後才是兩種「沒走完」：暫時停下、決定不走了；最後 mirror 隔開放。
STATES = (
    {"id": "idea", "icon": "💡", "label": "構想", "hint": "還沒開始，先放著"},
    {"id": "active", "icon": "🔥", "label": "進行中", "hint": "現在正在做的"},
    {"id": "done", "icon": "✅", "label": "完成", "hint": "收工了"},
    {"id": "paused", "icon": "⏸", "label": "暫停中", "hint": "停下來了，記一下為什麼"},
    # 放下＝只是標記，資料一點都不刪；真刪一次，下一次就不敢按了
    {"id": "archived", "icon": "📦", "label": "放下了", "hint": "先不做了；資料留著，隨時撿得回來"},
    # 保管中：不是在開發的（備份、別人的專案、換機器要用）。不追進度，只要找得到、還在。
    {"id": "mirror", "icon": "🗄", "label": "保管中", "hint": "不是我在開發的：備份／別人的專案／換機器要用。只要留著、找得到就好"},
)
# 「靜止」的狀態：程式不該對它們說「你很久沒動了」
RESTING_STATES = ("done", "archived", "mirror")

# 為什麼停下來——人自己按的那一格（跟程式量出來的「冷掉」是兩種訊號）。
# ask＝按下後追問的一句；ref＝'project' 代表答案可以指向另一個專案（reason_ref）：A 為了 B 停下來，是一筆有對象的機會成本。
REASONS = (
    {"id": "api", "icon": "⛽", "label": "額度用完", "hint": "API／額度用光，等下個週期",
     "ask": "這輪的額度先給誰用？", "ref": "project"},
    {"id": "detour", "icon": "☄️", "label": "隕石插隊", "hint": "突然冒出的 side project 先做",
     "ask": "被哪個專案插隊？", "ref": "project"},
    {"id": "block", "icon": "🧱", "label": "卡住了", "hint": "技術卡關／等外部／缺資料",
     "ask": "卡在哪一關？在等什麼？", "ref": ""},
    {"id": "pivot", "icon": "🧭", "label": "換路了", "hint": "方向調整過，它不在我現在走的那條路上了",
     "ask": "那現在力氣要放到哪裡？", "ref": ""},
)
CUSTOM_ICON = "✍️"
CUSTOM_ASK = "這次是怎麼回事？"
MAX_CUSTOM_REASONS = 24

# 「什麼時候回來看」的快捷選項（天數）。刻意都是短的；不設永遠是預設，不逼你承諾一個日期。
RESUME_PRESETS = ({"days": 7, "label": "一週後"}, {"days": 14, "label": "兩週後"},
                  {"days": 30, "label": "一個月後"}, {"days": 90, "label": "三個月後"})

# 一筆專案更新允許碰的欄位。這是唯一一份清單（pack.py 的指令欄位、畫面動作都照它）。
UPDATABLE = ("name", "path", "state", "reason", "reason_note", "reason_ref",
             "resume_at", "why_now", "status", "next", "tags", "pinned")
# 值得記進歷史的欄位：改標籤不該洗版，但「狀態變了」「下一步變了」正是要回頭看的
_LOGGED = ("state", "status", "next", "reason", "reason_note", "reason_ref", "resume_at", "why_now")

STATE_IDS = frozenset(s["id"] for s in STATES)
BUILTIN_REASON_IDS = frozenset(r["id"] for r in REASONS)


def now() -> float:
    """現在（秒）。測試換掉這一個。"""
    return time.time()


def today() -> date:
    """今天。測試換掉這一個。"""
    return datetime.fromtimestamp(now()).date()


def empty() -> dict:
    return {"projects": [], "custom_reasons": [], "trash": []}


def meta(data: dict) -> dict:
    """畫面要畫的詞彙：狀態、原因（內建＋自訂；退休的另放）、回訪日快捷。"""
    custom = data.get("custom_reasons", [])
    return {"states": list(STATES), "reasons": list(REASONS) + [r for r in custom if not r.get("legacy")],
            "legacy_reasons": [r for r in custom if r.get("legacy")], "cold_days": COLD_DAYS,
            "custom_icon": CUSTOM_ICON, "resume_presets": list(RESUME_PRESETS),
            # 前端據此決定「這張卡要不要出現 ⏸」——不要在前端再寫一份
            "resting_states": list(RESTING_STATES)}


def _text(value, limit: int = MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


def _clean_tags(value) -> list:
    if isinstance(value, str):
        value = value.replace("，", ",").split(",")
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for tag in value:
        tag = str(tag).strip()[:24]
        if tag and tag not in out:
            out.append(tag)
    return out[:8]


def _parse_date(text):
    """壞掉的日期字串一律當作「沒設」。一個手改壞的欄位不該讓整張清單打不開。"""
    try:
        return date.fromisoformat(str(text or ""))
    except ValueError:
        return None


def _days_since(epoch) -> int:
    try:
        then = datetime.fromtimestamp(int(epoch)).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return 0
    return max(0, (today() - then).days)


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def normalize(data) -> dict:
    """把存起來的東西補齊成完整的樣子（就地改，回傳同一包）。壞掉的值退回預設，不丟錯。"""
    if not isinstance(data, dict):
        data = {}
    for key in ("projects", "custom_reasons", "trash"):
        rows = data.get(key)
        data[key] = [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []
    for i, p in enumerate(data["projects"]):
        _fill(p, i)
    data["projects"].sort(key=lambda p: (p.get("order", 0), p.get("created_at", 0)))
    return data


def _fill(p: dict, i: int = 0) -> None:
    stamp = int(now())
    defaults = (("name", "未命名專案"), ("path", ""), ("state", "active"), ("reason", ""), ("reason_note", ""),
                ("reason_ref", ""), ("resume_at", ""), ("paused_at", 0), ("why_now", ""), ("status", ""),
                ("next", ""), ("last_active", 0), ("last_commit", 0), ("activity_source", ""), ("scanned_at", 0),
                ("pinned", False), ("order", i), ("created_at", stamp))
    p.setdefault("id", _new_id())
    for key, default in defaults:
        p.setdefault(key, default)
    p.setdefault("tags", [])
    p.setdefault("log", [])
    p.setdefault("updated_at", p["created_at"])
    if p["state"] not in STATE_IDS:
        p["state"] = "active"


def _find(data: dict, pid: str) -> dict:
    for p in data["projects"]:
        if p["id"] == pid:
            return p
    raise ValueError(f"找不到專案：{pid}")


def reason_ids(data: dict) -> set:
    return set(BUILTIN_REASON_IDS) | {r["id"] for r in data.get("custom_reasons", [])}


def view(p: dict, resolved: str = "", exists: bool = False) -> dict:
    """一筆專案加上畫面不該自己重算的東西。resolved／exists 由 pack 層看資料夾後給（這裡不碰檔案）。"""
    idle = _days_since(p.get("last_active") or p.get("updated_at"))
    # 「冷掉」＝程式量出來的，橫跨所有狀態。三個條件缺一不可：不是靜止的狀態、真的掃描過、超過 COLD_DAYS 天沒動。
    # 沒掃過時 idle 是拿「這張卡被編輯的時間」猜的，拿猜的去強制收攏會冤枉正在開發的專案。
    is_cold = p.get("state") not in RESTING_STATES and bool(p.get("last_active")) and idle >= COLD_DAYS
    resume = _parse_date(p.get("resume_at"))
    resume_days = (resume - today()).days if resume else None
    return {
        **p,
        "resolved_path": resolved, "path_exists": exists,
        "idle_days": idle, "idle_is_real": bool(p.get("last_active")), "is_cold": is_cold,
        # 停了多久：只有真的在暫停中、記得起停日的才算得出來（舊資料 None，誠實：不編數字）
        "paused_days": _days_since(p["paused_at"]) if p.get("state") == "paused" and p.get("paused_at") else None,
        "resume_days": resume_days,
        "resume_due": bool(resume_days is not None and resume_days <= 0 and p.get("state") not in RESTING_STATES),
        "commit_days": _days_since(p["last_commit"]) if p.get("last_commit") else None,
    }


def list_all(data: dict, resolve=None) -> dict:
    """resolve(path) -> (resolved, exists)；沒給＝不看資料夾。"""
    projects = []
    for p in data["projects"]:
        resolved, exists = resolve(p["path"]) if (resolve and p.get("path")) else ("", False)
        projects.append(view(p, resolved, exists))
    counts = {s["id"]: 0 for s in STATES}
    for p in projects:
        counts[p["state"]] = counts.get(p["state"], 0) + 1
    # cold／due_back 跟狀態是重疊的（一個專案可以同時是「暫停中」和「冷掉」），所以總和不等於專案數，這是對的
    counts["cold"] = sum(1 for p in projects if p["is_cold"])
    counts["due_back"] = sum(1 for p in projects if p["resume_due"])
    return {"projects": projects, "counts": counts, "pinned": [p for p in projects if p.get("pinned")], **meta(data)}


def add(data: dict, name: str, path: str = "") -> dict:
    name = _text(name, 60)
    if not name:
        raise ValueError("專案名稱不能是空的")
    stamp = int(now())
    rec = {"id": _new_id(), "name": name, "path": _text(path), "state": "active", "reason": "", "reason_note": "",
           "reason_ref": "", "resume_at": "", "paused_at": 0, "why_now": "", "status": "", "next": "",
           "last_active": 0, "last_commit": 0, "activity_source": "", "scanned_at": 0, "tags": [], "pinned": False,
           "log": [], "order": len(data["projects"]), "created_at": stamp, "updated_at": stamp}
    data["projects"].append(rec)
    return rec


def update(data: dict, pid: str, **fields) -> dict:
    """只動有給的欄位。值是 "-"（CLEAR）＝清成空的。"""
    p = _find(data, pid)
    before = {k: p.get(k) for k in _LOGGED}
    ok_reasons = reason_ids(data)
    for k, v in fields.items():
        if k not in UPDATABLE or v is None:
            continue
        if isinstance(v, str) and v.strip() == CLEAR:
            v = ""
        if k == "name":
            v = _text(v, 60)
            if not v:
                raise ValueError("專案名稱不能是空的")
            p["name"] = v
        elif k == "path":
            p["path"] = _text(v)
        elif k == "state":
            v = str(v or "").strip()
            if v not in STATE_IDS:
                raise ValueError(f"未知的狀態：{v}")
            # 離開暫停不清掉原因：「上次是被誰插隊的」正是三個月後最想知道的事
            if v == "paused" and p["state"] != "paused":
                p["paused_at"] = int(now())
            elif v != "paused":
                p["paused_at"] = 0
            p["state"] = v
        elif k == "reason":
            v = str(v or "").strip()
            if v and v not in ok_reasons:
                raise ValueError(f"未知的暫停原因：{v}")
            p["reason"] = v
        elif k == "resume_at":
            v = str(v or "").strip()
            if v and not _parse_date(v):
                raise ValueError(f"看不懂的日期：{v}（要 YYYY-MM-DD）")
            p["resume_at"] = v
        elif k in ("status", "next", "reason_note", "why_now"):
            p[k] = _text(v)
        elif k == "reason_ref":
            # 不驗那個專案 id 還在不在：被讓位的專案之後可能停止追蹤，那時這裡安靜地顯示不出來就好
            p["reason_ref"] = str(v or "").strip()[:32]
        elif k == "tags":
            p["tags"] = _clean_tags(v)
        elif k == "pinned":
            p["pinned"] = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "y", "on", "是")
    if any(p.get(k) != before[k] for k in _LOGGED):
        p["log"].append({"at": int(now()), "state": p["state"], "status": p["status"], "next": p["next"],
                         "reason": p["reason"], "reason_note": p["reason_note"], "reason_ref": p["reason_ref"],
                         "resume_at": p["resume_at"]})
        p["log"] = p["log"][-LOG_KEEP:]
        p["updated_at"] = int(now())
    return p


def delete(data: dict, pid: str):
    """停止追蹤：原樣放進回收區（最近 TRASH_KEEP 筆），可以 restore。回傳那筆紀錄；找不到回 None。"""
    hit = next((p for p in data["projects"] if p["id"] == pid), None)
    if hit is None:
        return None
    data["projects"] = [p for p in data["projects"] if p["id"] != pid]
    data["trash"] = (data["trash"] + [hit])[-TRASH_KEEP:]
    return hit


def restore(data: dict, pid: str):
    """把停止追蹤的那筆原樣放回去（id 要保住：別的專案的「讓位給誰」指著它）。"""
    hit = next((p for p in data["trash"] if p.get("id") == pid), None)
    if hit is None:
        return None
    data["trash"] = [p for p in data["trash"] if p.get("id") != pid]
    if not any(p["id"] == pid for p in data["projects"]):
        _fill(hit, len(data["projects"]))
        hit["order"] = len(data["projects"])
        data["projects"].append(hit)
    return hit


def reorder(data: dict, ids) -> bool:
    pos = {pid: i for i, pid in enumerate(ids)}
    if not pos:
        return False
    for p in data["projects"]:
        p["order"] = pos.get(p["id"], len(pos))
    data["projects"].sort(key=lambda p: p["order"])
    for i, p in enumerate(data["projects"]):
        p["order"] = i
    return True


def add_reason(data: dict, label: str, icon: str = "", ask: str = "") -> dict:
    """新增一個自訂原因。同名的直接回傳既有那一筆（含已退休的：你又打了一次，等於想撿回來）。"""
    label = _text(label, 12)
    if not label:
        raise ValueError("原因要有名字（最多 12 個字）")
    for r in data["custom_reasons"]:
        if r["label"] == label:
            r.pop("legacy", None)
            return r
    if len([r for r in data["custom_reasons"] if not r.get("legacy")]) >= MAX_CUSTOM_REASONS:
        raise ValueError(f"自訂原因最多 {MAX_CUSTOM_REASONS} 個——先退休幾個沒在用的")
    rec = {"id": "c" + uuid.uuid4().hex[:7], "icon": _text(icon, 4) or CUSTOM_ICON, "label": label,
           "hint": "你自己加的原因", "custom": True, "ask": _text(ask, 40) or CUSTOM_ASK, "ref": ""}
    data["custom_reasons"].append(rec)
    return rec


def retire_reason(data: dict, rid: str) -> dict:
    """退休，不是刪除：既有紀錄照樣顯示得出來，只是不再出現在選單裡。"""
    for r in data["custom_reasons"]:
        if r["id"] == rid:
            r["legacy"] = True
            return r
    raise ValueError(f"找不到這個原因：{rid}")


def apply_scan(data: dict, results: dict) -> int:
    """results：{專案 id: scan.scan_one 的結果}。回傳實際套用幾個（專案掃的途中被刪了就略過）。"""
    n = 0
    for p in data["projects"]:
        r = results.get(p["id"])
        if r is None:
            continue
        p["scanned_at"] = int(now())
        p["last_active"] = r["last_active"]
        p["last_commit"] = r["last_commit"]
        p["activity_source"] = r["source"]
        n += 1
    return n
