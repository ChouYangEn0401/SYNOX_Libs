"""基準尺度：同樣的錢進出，換成「基準年 20%」會剩多少；未來的藍圖。

設計：
- 投資的報酬拿一個大盤型的基準（預設年化 20%）來量，並給一張藍圖：照這個年率，一年後、三年後、五年後會變多少。
- 買個股時要算它有沒有勝過基準；不論輸贏都一直列著提醒，直到使用者自己把它收起來。
- 年率可以在設定調整（長期常見的假設比較保守，約 10%，見 LOW_DEFAULT）。

**怎麼比（不抓股價）**：一池影子錢——每一筆投入（買進、轉進來）當天放進池子，每一筆拿回來（賣出、股利、轉出）
當天從池子拿走同樣的錢，池子照 (1＋年率)^(天數/365) 長大。最後實際剩下的（持股用成本、或塞進來的價格）跟池子比：
實際剩得多＝贏基準。進出完全一樣，所以只比「剩下的」就公平。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Iterable

from .. import clock
from . import settings

RATE_DEFAULT, LOW_DEFAULT, SYMBOL_DEFAULT = 0.20, 0.10, ""          # 基準不預設代號（名字自己填），只給年率
YEARS = (1, 3, 5)                       # 藍圖只算到 5 年（再往後的數字太不確定，列出來沒有意義）


def config(conn: sqlite3.Connection) -> dict[str, Any]:
    return {"rate": float(settings.get(conn, "bench.rate", RATE_DEFAULT)),
            "low": float(settings.get(conn, "bench.low", LOW_DEFAULT)),
            "symbol": str(settings.get(conn, "bench.symbol", SYMBOL_DEFAULT) or SYMBOL_DEFAULT)}


def grow(amount: float, days: int, rate: float) -> float:
    return amount * (1 + rate) ** (max(days, 0) / 365)


class Pot:
    """影子池：照時間順序放錢、拿錢，最後看值多少。"""

    def __init__(self, rate: float) -> None:
        self.rate, self.value, self.day = rate, 0.0, None
        self.put_in = self.taken = 0

    def _to(self, day: date) -> None:
        if self.day is not None and day > self.day:
            self.value = grow(self.value, (day - self.day).days, self.rate)
        if self.day is None or day > self.day:
            self.day = day

    def move(self, day: date, amount: int) -> None:
        """amount 正＝放進去（投入）、負＝拿出來。"""
        self._to(day)
        self.value += amount
        if amount > 0:
            self.put_in += amount
        else:
            self.taken += -amount

    def at(self, day: date) -> float:
        self._to(day)
        return self.value


def trade_flow(r: Any) -> int:
    """交易明細 → 影子池的進出：買進＝投入（付出去的錢），賣出、股利＝拿回來，費用＝多投入。"""
    return -int(r["net"])


def compare(flows: Iterable[tuple[str, int]], remaining: int, rate: float, today: date | None = None) -> dict[str, Any]:
    """flows＝(日期, 投入＋/拿回−)，照時間排好；remaining＝實際現在剩多少（持股成本或現值）。"""
    today = today or clock.today()
    pot = Pot(rate)
    first = None
    for day, amount in flows:
        d = date.fromisoformat(day[:10])
        first = first or d
        pot.move(d, amount)
    shadow = round(pot.at(today))
    actual_gain = remaining + pot.taken - pot.put_in
    shadow_gain = shadow + pot.taken - pot.put_in
    return {"put_in": pot.put_in, "taken": pot.taken, "remaining": remaining, "shadow": shadow,
            "actual_gain": actual_gain, "shadow_gain": shadow_gain, "diff": remaining - shadow,
            "beat": remaining >= shadow, "since": first.isoformat() if first else None}


def blueprint(now: int, monthly: int, rate: float, low: float) -> list[dict[str, Any]]:
    """目前已有 now、每月再放 monthly，照尺度與保守線算 1、3、5 年後（月複利、月底放錢）。"""
    def fv(r: float, years: int) -> int:
        i = (1 + r) ** (1 / 12) - 1
        n = years * 12
        grown = now * (1 + r) ** years
        added = monthly * (((1 + i) ** n - 1) / i if i else n)
        return round(grown + added)
    return [{"years": y, "put_in": now + monthly * 12 * y, "bench": fv(rate, y), "low": fv(low, y)} for y in YEARS]


def red_line(now: int, monthly: int, rate: float, start: date | None = None, months: int = 60) -> list[dict[str, Any]]:
    """未來的期望紅線：從今天起每個月一點（給線圖畫虛線）。"""
    start = start or clock.today()
    i = (1 + rate) ** (1 / 12) - 1
    out, v = [], float(now)
    for m in range(0, months + 1):
        y, mo = divmod(start.month - 1 + m, 12)
        day = start if m == 0 else date(start.year + y, mo + 1, 1)      # 第一點是今天（從現在的金額出發）
        out.append({"date": day.isoformat(), "value": round(v)})
        v = v * (1 + i) + monthly
    return out
