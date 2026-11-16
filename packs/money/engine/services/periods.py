"""期別：從發薪日起算的「一個月」。

- 起算日在 settings.period_start_day（預設 5）；29–31 遇到小月夾到月底。
- 每一期產生過就存進 periods 表；新的一期從上一期的隔天開始，結束在「至少 15 天後、下一個起算日」的前一天。
  所以之後改起算日只影響還沒產生的期，舊資料不會換期。
- 期名取期中那天所在的月份：起算日 5 號時 2026-10＝10/05–11/04。撞名就加 b。
- 週用 ISO 週，鍵是 2026-W41。
"""
from __future__ import annotations

import calendar
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from .. import clock
from ..errors import InvalidInput, NotFound
from . import settings

MIN_LEN = timedelta(days=15)


@dataclass(frozen=True)
class Period:
    key: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def elapsed(self, today: date | None = None) -> int:
        """到今天（含）過了幾天；還沒開始是 0，已經結束是全長。"""
        today = today or clock.today()
        if today < self.start:
            return 0
        if today > self.end:
            return self.days
        return (today - self.start).days + 1

    def is_open(self, today: date | None = None) -> bool:
        return self.contains(today or clock.today())

    def to_dict(self, today: date | None = None) -> dict:
        today = today or clock.today()
        return {"key": self.key, "start": self.start.isoformat(), "end": self.end.isoformat(), "days": self.days,
                "elapsed": self.elapsed(today), "open": self.is_open(today)}


def start_day(conn: sqlite3.Connection) -> int:
    value = int(settings.get(conn, "period_start_day", 5))
    if not 1 <= value <= 31:
        raise InvalidInput(f"期別起算日 {value} 不在 1–31 之間", hint="/money_setting period_start_day <1–31>")
    return value


def _clamp(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _boundary_on_or_after(day: date, sday: int) -> date:
    candidate = _clamp(day.year, day.month, sday)
    if candidate < day:
        y, m = _shift_month(day.year, day.month, 1)
        candidate = _clamp(y, m, sday)
    return candidate


def _boundary_on_or_before(day: date, sday: int) -> date:
    candidate = _clamp(day.year, day.month, sday)
    if candidate > day:
        y, m = _shift_month(day.year, day.month, -1)
        candidate = _clamp(y, m, sday)
    return candidate


def _key_for(start: date, end: date) -> str:
    mid = start + (end - start) / 2
    return f"{mid.year:04d}-{mid.month:02d}"


def _row(r: sqlite3.Row) -> Period:
    return Period(r["key"], date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"]))


def _insert(conn: sqlite3.Connection, start: date, end: date) -> None:
    key = _key_for(start, end)
    while conn.execute("SELECT 1 FROM periods WHERE key = ? AND start_date <> ?", (key, start.isoformat())).fetchone():
        key += "b"
    conn.execute("INSERT OR IGNORE INTO periods (key, start_date, end_date) VALUES (?,?,?)",
                 (key, start.isoformat(), end.isoformat()))


def period_for(conn: sqlite3.Connection, day: date) -> Period:
    """這一天屬於哪一期；還沒產生就接著產生。"""
    found = conn.execute("SELECT * FROM periods WHERE start_date <= ? AND end_date >= ?",
                         (day.isoformat(), day.isoformat())).fetchone()
    if found:
        return _row(found)
    sday = start_day(conn)
    bounds = conn.execute("SELECT MIN(start_date), MAX(end_date) FROM periods").fetchone()
    if bounds[0] is None:
        start = _boundary_on_or_before(day, sday)
        _insert(conn, start, _boundary_on_or_after(start + MIN_LEN, sday) - timedelta(days=1))
    else:
        first, last = date.fromisoformat(bounds[0]), date.fromisoformat(bounds[1])
        while day > last:
            start = last + timedelta(days=1)
            last = _boundary_on_or_after(start + MIN_LEN, sday) - timedelta(days=1)
            _insert(conn, start, last)
        while day < first:
            end = first - timedelta(days=1)
            first = _boundary_on_or_before(end - MIN_LEN + timedelta(days=1), sday)
            _insert(conn, first, end)
    return period_for(conn, day)


def current(conn: sqlite3.Connection) -> Period:
    return period_for(conn, clock.today())


KEY_RE = re.compile(r"^\d{4}-\d{2}b*$")


def get(conn: sqlite3.Connection, key: str | None) -> Period:
    """期名 → Period；不給就是本期。還沒產生過的期名會試著用該月 15 號找。"""
    if not key:
        return current(conn)
    if not KEY_RE.match(key):
        raise InvalidInput(f"期名「{key}」看不懂", hint="期名長這樣：2026-10（＝10/05 起的那一期）")
    row = conn.execute("SELECT * FROM periods WHERE key = ?", (key,)).fetchone()
    if row:
        return _row(row)
    year, month = int(key[:4]), int(key[5:7])
    if not 1 <= month <= 12:
        raise InvalidInput(f"期名「{key}」的月份不對")
    p = period_for(conn, date(year, month, 15))
    if p.key != key:
        raise NotFound(f"沒有「{key}」這一期", suggestions=[p.key])
    return p


MONTH_RE = re.compile(r"(\d{1,2})\s*月")


def guess_for(conn: sqlite3.Connection, text: str, day: date) -> str | None:
    """「11月房租」這種寫法：說的月份不是付款那一期的月份，而且剛好是前一期、下一期或下下期，就回那一期的名字。
    期名取期中的月份（10/05～11/04＝10 月），所以「11月房租」＝2026-11 期。猜不出來回 None（照付款當天算）。"""
    m = MONTH_RE.search(text or "")
    if not m:
        return None
    month = int(m.group(1))
    own = period_for(conn, day)
    if not 1 <= month <= 12 or month == int(own.key[5:7]):
        return None
    for delta in (1, 2, -1):
        p = own
        for _ in range(abs(delta)):
            p = shift(conn, p, 1 if delta > 0 else -1)
        if int(p.key[5:7]) == month:
            return p.key
    return None


def shift(conn: sqlite3.Connection, period: Period, delta: int) -> Period:
    """前一期（-1）或下一期（+1）。"""
    if delta < 0:
        return period_for(conn, period.start - timedelta(days=1))
    return period_for(conn, period.end + timedelta(days=1))


def list_recent(conn: sqlite3.Connection, count: int = 12) -> list[Period]:
    rows = conn.execute("SELECT * FROM periods WHERE start_date <= ? ORDER BY start_date DESC LIMIT ?",
                        (clock.today().isoformat(), count)).fetchall()
    return [_row(r) for r in rows]


# ---------------------------------------------------------------- 週

def week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_range(key: str) -> tuple[date, date]:
    m = re.match(r"^(\d{4})-W(\d{2})$", key)
    if not m:
        raise InvalidInput(f"週的寫法「{key}」看不懂", hint="長這樣：2026-W41")
    monday = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    return monday, monday + timedelta(days=6)
