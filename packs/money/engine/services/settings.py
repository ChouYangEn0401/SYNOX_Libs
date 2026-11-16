"""設定：key/value，值存 JSON。改設定也是一次寫入（有 batch、audit，可以撤銷）。

EDITABLE 是使用者可以改的設定（指令 /money_setting 和網站設定頁共用 coerce 的檢查）；
其他 key（invest.capital 之類）由各自的服務寫。`clear` 把一個設定拿掉（回到 DEFAULTS）。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import Writer
from ..errors import InvalidInput

EDITABLE: dict[str, type] = {"period_start_day": int, "default_account": str, "review.include_notes": bool,
                             "invest.default_account": str, "review.weekly": bool,
                             "review.month_days_before_end": int, "notify.weekday": int,
                             # 撲滿：基準尺度、保守線、比較時排除哪一檔、捐贈比例、全域本月可虧損
                             "bench.rate": float, "bench.low": float, "bench.symbol": str, "tithe.rate": float,
                             "trading.month_max_loss": int,
                             # 規劃：會浮動的格子差多少算做完、三組的名字
                             "plan.flex_tolerance": float, "plan.group.must": str, "plan.group.flex": str,
                             "plan.group.living": str,
                             # 以前寫在程式裡的預設，搬成設定（沒設＝DEFAULTS）
                             "quick.lunch_weekdays": str,
                             # 欠別人的錢幾天沒還才提醒
                             "owe.remind_days": int,
                             # 帳本設定：幣別（一本帳一種；金額存最小單位的整數，小數位數給畫面換算）、時差（小時）
                             "currency.code": str, "currency.symbol": str, "currency.decimals": int, "tz_offset": float}
# 沒存進設定表時用的值；起始檔（seeds/generic.json）會另外寫自己的
DEFAULTS: dict[str, Any] = {"quick.lunch_weekdays": "0,1,2,3,4", "owe.remind_days": 30,
                            "currency.code": "TWD", "currency.symbol": "NT$", "currency.decimals": 0, "tz_offset": None}
# 小數設定各自的範圍（給 % 也行：20 → 0.2）
FLOAT_RANGE = {"bench.rate": (0.0, 1.0, True), "bench.low": (0.0, 1.0, True),
               "tithe.rate": (0.0, 1.0, True), "plan.flex_tolerance": (0.0, 0.5, True)}


def get(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return default if row is None else json.loads(row[0])


def value(conn: sqlite3.Connection, key: str) -> Any:
    """有存就用存的，沒有就用 DEFAULTS（給「以前寫死、現在可以改」的那幾個）。"""
    return get(conn, key, DEFAULTS.get(key))


def weekdays(text: Any) -> set[int]:
    """「0,1,2,3,4」→ {0,…,4}（0＝週一）；壞的值丟 InvalidInput。"""
    out = set()
    for part in str(text or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or not 0 <= int(part) <= 6:
            raise InvalidInput("星期要寫 0–6，逗號分隔", hint="0＝週一……4＝週五、6＝週日；例 0,1,2,3,4")
        out.add(int(part))
    return out


def all_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    return {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings ORDER BY key")}


def coerce(conn: sqlite3.Connection, key: str, raw: Any) -> Any:
    """把 CLI 字串或網頁 JSON 轉成設定值並檢查；帳戶名稱換成正式名稱（別名也可以）。"""
    from . import refdata
    kind = EDITABLE.get(key)
    if kind is None:
        raise InvalidInput(f"「{key}」不是可以改的設定", hint="可以改：" + "、".join(EDITABLE))
    if kind is bool:
        if isinstance(raw, bool):
            value: Any = raw
        elif str(raw).strip().lower() in ("true", "1", "false", "0"):
            value = str(raw).strip().lower() in ("true", "1")
        else:
            raise InvalidInput(f"{key} 要是 true 或 false")
    elif key == "tz_offset":
        if isinstance(raw, bool):
            raise InvalidInput("時差要是數字（小時）", hint="例：8、-5、5.5")
        try:
            value = float(str(raw).strip())
        except ValueError:
            raise InvalidInput("時差要是數字（小時）", hint="例：8、-5、5.5") from None
        if not -12 <= value <= 14:
            raise InvalidInput("時差要在 -12～14 小時之間", hint="例：8、-5、5.5")
        value = int(value) if value.is_integer() else value
    elif kind is int:
        if isinstance(raw, bool) or not str(raw).strip().isdigit():
            raise InvalidInput(f"{key} 要是整數")
        value = int(str(raw).strip())
    elif kind is float:
        text = str(raw).strip()
        try:
            value = float(text.rstrip("%"))
        except ValueError:
            raise InvalidInput(f"{key} 要是數字") from None
        low, high, percent = FLOAT_RANGE.get(key, (0.0, 5.0, False))
        if percent and (text.endswith("%") or value > 1):
            value = value / 100                               # 「20」「20%」都是 20%
        if isinstance(raw, bool) or not low < value <= high and not (percent and value == 0 and key == "tithe.rate"):
            raise InvalidInput(f"{key} 要在 0～100% 之間", hint="例：0.2 或 20%")
        value = round(value, 4)
    else:
        value = str(raw if raw is not None else "").strip()
    if key == "period_start_day" and not 1 <= value <= 31:
        raise InvalidInput("起算日要在 1–31 之間", hint="通常是發薪日；29–31 遇到小月會夾到月底")
    if key == "review.month_days_before_end" and not 0 <= value <= 10:
        raise InvalidInput("月底前幾天要在 0–10 之間", hint="1＝月底前一天做檢討書")
    if key.startswith("plan.group.") and len(value) > 10:
        raise InvalidInput("組的名字最多 10 個字", hint="空白＝用預設的名字")
    if key == "bench.symbol" and len(value) > 20:
        raise InvalidInput("比較用的代號最多 20 個字", hint="空白＝不設（只用年率比）")
    if key == "notify.weekday" and not 0 <= value <= 6:
        raise InvalidInput("星期要是 0–6", hint="0＝週一……4＝週五、6＝週日")
    if key == "quick.lunch_weekdays":
        value = ",".join(str(d) for d in sorted(weekdays(value)))          # 空字串＝哪天都不問
    if key == "currency.code":
        value = value.upper()
        if not (1 <= len(value) <= 10 and value.isalnum()):
            raise InvalidInput("幣別代號要是 1–10 個英文字母或數字", hint="例：TWD、USD、JPY")
    if key == "currency.symbol" and len(value) > 5:
        raise InvalidInput("幣別符號最多 5 個字", hint="例：NT$、$、¥")
    if key == "currency.decimals" and not 0 <= value <= 3:
        raise InvalidInput("小數位數要在 0–3 之間", hint="新台幣、日圓是 0；美元是 2")
    if key == "default_account":
        if not value:
            raise InvalidInput("預設付款帳戶不能空白")
        value = refdata.resolve_account(conn, value)["name"]
    if key == "invest.default_account":
        value = refdata.resolve_account(conn, value)["name"] if value else None    # 空白＝第一個證券帳戶
    return value


def clear(w: Writer, key: str) -> None:
    """拿掉一個設定（之後讀到的是 DEFAULTS）；本來就沒有就什麼都不做、不留 audit。"""
    before = w.conn.execute("SELECT key, value FROM settings WHERE key = ?", (key,)).fetchone()
    if before is None:
        return
    w.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    w.audit("delete", "settings", key, {"key": key, "value": before["value"]}, None)


def put(w: Writer, key: str, value: Any) -> None:
    before = w.conn.execute("SELECT key, value FROM settings WHERE key = ?", (key,)).fetchone()
    encoded = json.dumps(value, ensure_ascii=False)
    if before and before["value"] == encoded:
        return                                               # 一樣就不重寫、不留 audit
    w.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, encoded))
    w.audit("update" if before else "create", "settings", key,
            {"key": key, "value": before["value"]} if before else None, {"key": key, "value": encoded})
