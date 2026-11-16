"""「今天」與時間戳：照這本帳的時差。

時差（小時，可以是 5.5）是帳本設定 `tz_offset`，由開帳本的人帶進 paths 的 context；沒設定就跟著這台電腦。
固定時差、不用時區資料庫（Windows 的 Python 沒附 tzdata）。
context 裡的 `today` 可以把「今天」固定住（測試、或呼叫端要跟自己的日期一致；時分秒仍是現在的）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from . import paths


def _hours(raw) -> float | None:
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return None
    return hours if -12 <= hours <= 14 else None


def _zone() -> timezone:
    ctx = paths.current_or_none()
    hours = _hours(ctx.tz_offset) if ctx is not None and ctx.tz_offset is not None else None
    if hours is None:
        local = datetime.now().astimezone().tzinfo
        return local if isinstance(local, timezone) else timezone(datetime.now().astimezone().utcoffset() or timedelta(0))
    return timezone(timedelta(hours=hours))


def now() -> datetime:
    real = datetime.now(_zone())
    ctx = paths.current_or_none()
    fixed = ctx.today if ctx is not None else None
    if fixed:
        return real.replace(year=fixed.year, month=fixed.month, day=fixed.day)
    return real


def today() -> date:
    return now().date()


def now_iso() -> str:
    """寫進 created_at／updated_at 的時間戳，格式是 YYYY-MM-DDTHH:MM:SS+08:00（ISO 8601，帶時差）。"""
    return now().isoformat(timespec="seconds")
