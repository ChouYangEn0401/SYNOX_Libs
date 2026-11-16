"""一個月的帳匯出成 CSV、從 CSV 匯入。

給別人搬家用：很多記帳 app 沒有 API，只有 CSV 匯出匯入；試算表也打得開。
- 匯出：日曆月（不是期別：別的 app 都用日曆月），UTF-8 有 BOM（Excel 直接開不會亂碼），舊的在上面。
- 匯入：欄位照 COLUMNS（中文欄名也認，ALIASES），每一列變成一筆 ingest 紀錄，所以規則跟記一筆完全一樣
  （類別找不到放待分類、帳戶找不到結束碼 3）。冪等：source_ref＝檔案內容的 sha256，同一個檔再匯一次只會回「已記過」。
  一次最多 MAX_ROWS 列；每 200 列一批（ingest 的上限），每批都能 /money_revert。

檔案路徑由呼叫的人給，一定在這本帳的檔案資料夾裡（paths.confined），這裡只讀、只寫那一個檔。
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import io
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..errors import InvalidInput, LedgerError, NotFound
from . import refdata, transactions as tx

COLUMNS = ["date", "time", "type", "amount", "category", "account", "to_account", "description", "merchant", "note", "id"]
ALIASES = {"日期": "date", "時間": "time", "類型": "type", "收支": "type", "金額": "amount", "類別": "category",
           "分類": "category", "帳戶": "account", "轉入帳戶": "to_account", "說明": "description", "項目": "description",
           "名稱": "description", "desc": "description", "商家": "merchant", "店家": "merchant", "備註": "note"}
TYPES = {"expense": "expense", "支出": "expense", "花費": "expense", "": "expense",
         "income": "income", "收入": "income", "transfer": "transfer", "轉帳": "transfer"}
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
MAX_ROWS = 5000
CHUNK = 200


def month_range(month: str) -> tuple[str, str]:
    m = MONTH_RE.match(str(month or "").strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise InvalidInput("月份要寫 YYYY-MM（西元）", hint="例：2030-01", fields={"month": "格式"})
    y, mo = int(m.group(1)), int(m.group(2))
    return f"{y:04d}-{mo:02d}-01", f"{y:04d}-{mo:02d}-{calendar.monthrange(y, mo)[1]:02d}"


def month_rows(conn: sqlite3.Connection, month: str) -> list[dict[str, Any]]:
    start, end = month_range(month)
    out, cursor = [], None
    while True:
        page = tx.query(conn, start=start, end=end, limit=500, cursor=cursor, order="asc")
        for t in page["items"]:
            out.append({"date": t["date"], "time": t["time"] or "", "type": t["kind"], "amount": t["amount"],
                        "category": t["category"] or "", "account": t["account"] or "", "to_account": t["to_account"] or "",
                        "description": t["desc"] or "", "merchant": t["merchant"] or "", "note": t["note"] or "",
                        "id": t["id"]})
        cursor = page["next_cursor"]
        if not cursor:
            return out


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def confined(raw: str | Path | None, *, default: Path | None = None) -> Path:
    """檔案路徑：只能在這本帳的檔案資料夾裡（paths.confined；不能讀寫別人的檔）；相對路徑以它為準。"""
    from .. import paths
    return paths.confined(raw, default=default)


def export_month(conn: sqlite3.Connection, month: str, out: str | Path | None = None) -> dict[str, Any]:
    from .. import paths
    rows = month_rows(conn, month)
    path = confined(out, default=paths.data_dir() / "exports")
    if path.is_dir() or path.suffix.lower() != ".csv":           # 不是 .csv 結尾＝資料夾（沒有就建）
        if path.exists() and not path.is_dir():
            raise InvalidInput("匯出的檔名要是 .csv 結尾，或一個資料夾", fields={"out": Path(str(out)).name})
        path = path / f"ledger-{month}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_csv(rows), encoding="utf-8-sig")
    totals = {k: sum(r["amount"] for r in rows if r["type"] == k) for k in ("expense", "income", "transfer")}
    return {"month": month, "path": str(path), "rows": len(rows), "columns": COLUMNS, "totals": totals}


def _norm_header(name: str) -> str:
    key = (name or "").strip().lstrip("﻿")
    return ALIASES.get(key, key.lower())


def read_rows(path: str | Path) -> tuple[list[dict[str, str]], str]:
    p = confined(path)
    if not p.is_file():
        raise NotFound(f"找不到檔案「{p.name}」", hint="檔案要放在自己的檔案資料夾裡，寫檔名就好")
    raw = p.read_bytes()
    if len(raw) > 5 * 1024 * 1024:
        raise InvalidInput("CSV 太大（超過 5MB），請分成幾個檔")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp950")                       # Excel 存的「CSV」在繁中 Windows 常是 Big5
        except UnicodeDecodeError:
            raise InvalidInput("CSV 要是 UTF-8 或 Big5 編碼") from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise InvalidInput("CSV 是空的")
    fields = [_norm_header(f) for f in reader.fieldnames]
    missing = [c for c in ("date", "amount") if c not in fields]
    if missing:
        raise InvalidInput(f"CSV 少了欄位：{'、'.join(missing)}", hint="第一列要是欄名，至少 date、amount（其他欄位見匯出的 CSV）")
    rows = [{_norm_header(k): (v or "").strip() for k, v in r.items() if k is not None} for r in reader]
    rows = [r for r in rows if any(r.values())]
    if len(rows) > MAX_ROWS:
        raise InvalidInput(f"一次最多 {MAX_ROWS} 列（這個檔 {len(rows)} 列），請分成幾個檔")
    return rows, hashlib.sha256(raw).hexdigest()[:16]


def record(conn: sqlite3.Connection, row: dict[str, str], line: int) -> dict[str, Any]:
    """CSV 的一列 → ingest 紀錄。帳戶先在這裡解析，錯了講得出是第幾列。"""
    kind = TYPES.get(row.get("type", "").strip().lower())
    if kind is None:
        raise InvalidInput(f"第 {line} 列：type 只能是 expense／income／transfer（或 支出／收入／轉帳）",
                           fields={f"row{line}.type": row.get("type", "")})
    text = row.get("amount", "").replace(",", "").replace("NT$", "").replace("$", "").strip()
    try:
        amount = int(text) if text and "." not in text else int(round(float(text)))
    except ValueError:
        raise InvalidInput(f"第 {line} 列：金額「{row.get('amount')}」不是數字", fields={f"row{line}.amount": "整數元"}) from None
    rec: dict[str, Any] = {"type": kind, "date": row["date"].replace("/", "-"), "desc": row.get("description", "")[:200]}
    for key in ("time", "note"):
        if row.get(key):
            rec[key] = row[key][:1000]
    for key in ("account", "to_account"):
        if row.get(key):
            try:
                refdata.resolve_account(conn, row[key])
            except NotFound as e:
                raise NotFound(f"第 {line} 列：找不到帳戶「{row[key]}」", suggestions=e.suggestions,
                               hint="先用 /money_account 建好這個帳戶，或把 CSV 的帳戶改成設定頁帳戶清單裡的名字") from None
    if kind == "transfer":
        if not row.get("account") or not row.get("to_account"):
            raise InvalidInput(f"第 {line} 列：轉帳要有 account（轉出）和 to_account（轉入）")
        rec.update({"from": row["account"], "to": row["to_account"], "amount": abs(amount)})
        if row.get("category"):
            rec["category"] = row["category"]
        return rec
    if row.get("category"):
        rec["category"] = row["category"]
    if row.get("account"):
        rec["account"] = row["account"]
    if kind == "expense":
        rec["amount"] = abs(amount)
        if amount < 0:
            rec["refund"] = True
        if row.get("merchant"):
            rec["merchant"] = row["merchant"][:100]
    else:
        if amount < 0:
            raise InvalidInput(f"第 {line} 列：收入不能是負的", fields={f"row{line}.amount": "正數"})
        rec["amount"] = amount
    return rec


def import_csv(conn: sqlite3.Connection, path: str | Path, *, actor: str, source: str, dry_run: bool) -> dict[str, Any]:
    from . import ingest
    rows, digest = read_rows(path)
    records = [record(conn, r, i + 2) for i, r in enumerate(rows)]       # 第 1 列是欄名
    batches, results, warnings = [], [], []
    summary = {"records": 0, "new": 0, "replayed": 0, "possible_duplicate": 0, "expense": 0, "income": 0, "transfer": 0}
    for n, start in enumerate(range(0, len(records), CHUNK)):
        chunk = records[start:start + CHUNK]
        try:
            out = ingest.run(conn, {"records": chunk}, actor=actor, source="csv", source_ref=f"csv:{digest}:{n}",
                             dry_run=dry_run, command="money import_csv")
        except LedgerError as e:
            e.message = f"CSV 第 {start + 2} 列起的這一批：{e.message}"
            raise
        if out["batch_id"]:
            batches.append(out["batch_id"])
        results.extend(out["results"])
        warnings.extend(out["warnings"])
        for k in summary:
            summary[k] += out["summary"].get(k, 0) or 0
    return {"file": Path(path).name, "rows": len(rows), "dry_run": dry_run, "batch_ids": batches, "source_ref": f"csv:{digest}",
            "summary": summary, "warnings": warnings[:50], "results": results[:50]}
