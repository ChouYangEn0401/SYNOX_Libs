"""把對帳單讀成「一張張表、一列列格子」（只用標準函式庫）：網頁存下來的 html 表格、或 CSV。

- html：每個 <table> 一張表（巢狀的表各自算一張）；<input value="…"> 的 value 也當成格子的字（有的網頁把日期放在那裡）；
  <br> 換成空白；連續空白縮成一個。
- csv：整個檔就是一張表。

`find(tables, must_have)` 找出表頭含有那幾欄的那一張 → (表頭, 其餘的列)。比對表頭時拿掉空白。
這個檔不碰檔案：內容由 files.py 讀好傳進來。
"""
from __future__ import annotations

import csv
import io
import re
from html.parser import HTMLParser

SPACES = re.compile(r"\s+")


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")
        elif tag == "input" and self._cell is not None:
            value = dict(attrs).get("value")
            if value:
                self._cell.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._stack and self._cell is not None:
            text = SPACES.sub(" ", "".join(self._cell).replace("\xa0", " ")).strip()
            if self._stack[-1]:
                self._stack[-1][-1].append(text)
            self._cell = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def decode(data: bytes, encoding: str = "utf-8") -> str:
    """位元組 → 字（utf-8 會先拿掉 BOM；壞的字元換成 �，不中斷）。"""
    enc = (encoding or "utf-8").lower()
    if enc in ("utf-8", "utf8"):
        enc = "utf-8-sig"
    return data.decode(enc, errors="replace")


def html_tables(text: str) -> list[list[list[str]]]:
    p = _Tables()
    p.feed(text)
    p.close()
    return [[r for r in t if any(r)] for t in p.tables]


def csv_tables(text: str) -> list[list[list[str]]]:
    rows = [[SPACES.sub(" ", c.replace("\xa0", " ")).strip() for c in r] for r in csv.reader(io.StringIO(text))]
    return [[r for r in rows if any(r)]]


def read(data: bytes, source: str, encoding: str = "utf-8") -> list[list[list[str]]]:
    """照描述檔的 source（html／csv）讀成表。"""
    text = decode(data, encoding)
    return csv_tables(text) if source == "csv" else html_tables(text)


def guess_source(data: bytes) -> str:
    """沒指定格式時：看起來像網頁（有 <table）就是 html，不然當 CSV。"""
    head = data[:200_000].lower()
    return "html" if b"<table" in head else "csv"


def _key(cell: str) -> str:
    return (cell or "").replace(" ", "")


def find(tables: list[list[list[str]]], must_have) -> tuple[list[str], list[list[str]]] | None:
    """表頭含有這幾欄的第一張表 → (表頭, 其餘的列)；沒有回 None。"""
    want = [_key(m) for m in must_have]
    for t in tables:
        for i, row in enumerate(t):
            keys = [_key(c) for c in row]
            if all(m in keys for m in want):
                return keys, t[i + 1:]
    return None


def number(text: str) -> float:
    """「1,234」「-5」「9%」「」「-」→ 數字（空的與「-」＝0）。"""
    t = (text or "").replace(",", "").replace("%", "").strip()
    if t in ("", "-"):
        return 0.0
    return float(t)
