"""交易：新增、修改、刪除（軟刪除）、查詢。所有寫入都經 Writer（有 batch、audit，可以整批撤銷）。

規則：
- 金額是整數元。支出可以是負數（退款）；收入、轉帳一定是正數。
- 轉帳要有轉入帳戶，而且不能跟轉出同一個；繳卡費、存錢、還款都是轉帳。
- 類別可以空（待分類），但給了就要跟交易種類一致（拿「繳卡費」記支出會被擋）。
- 日期不能超過明天；看起來像民國年（115-10-09）會被擋並提示。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from typing import Any

from .. import clock, ids
from ..db import Writer, row_dict
from ..errors import InvalidInput, NotFound
from . import refdata

KINDS = ("expense", "income", "transfer")
PURPOSES = ("card_payment", "saving", "investing", "prepay", "debt", "lend", "collect", "repay", "pension", "other")
# repay＝還給先幫我付的人：轉進「欠別人」，錢包在這天才少
# prepay＝預付：未來要用的錢先付掉或先放到別處（沒有預設的格子；轉進綁了用途的帳戶才算進那一格）
PURPOSE_NAMES = {"card_payment": "繳卡費", "saving": "存錢", "investing": "投資", "prepay": "預付", "debt": "還款", "lend": "代墊",
                 "collect": "收回", "repay": "還錢", "pension": "勞退", "other": "轉帳"}
CARD_NOT_SOURCE = ("saving", "investing", "pension", "collect", "card_payment", "repay")       # 信用卡不能存錢、投資、收錢；刷卡出去的只有花錢與代墊
CARD_NOT_DEST = ("lend", "collect", "repay", "saving", "investing", "prepay", "pension")      # 轉進信用卡只有繳卡費（與一般轉帳）
EDITABLE = ("date", "time", "kind", "amount", "currency", "foreign_amount", "category_id", "account_id",
            "to_account_id", "purpose", "counterparty", "description", "merchant", "note", "confidence",
            "financed", "group_id", "debt_id", "statement_line_id", "for_period", "nature")
MAX_DESC, MAX_NOTE, MAX_MERCHANT = 200, 1000, 100
# 刷在卡上的帳：花錢，或幫別人先付（借出轉帳）。錢一樣是卡上的錢——卡費要留、帳單上也看得到，
# 所以算卡費預留與對帳單的地方都用這一句，不要只寫 kind = 'expense'（刷卡代墊的訂閱費就會漏掉）。
CARD_CHARGE = "(kind = 'expense' OR (kind = 'transfer' AND purpose = 'lend'))"

DATE_RE = re.compile(r"^(\d{2,4})[-/.](\d{1,2})[-/.](\d{1,2})$")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")


# ---------------------------------------------------------------- 驗證

def parse_date(value: Any, field: str = "date", *, allow_future: bool = False) -> str:
    if isinstance(value, date):
        day = value
    else:
        text = str(value or "").strip()
        m = DATE_RE.match(text)
        if not m:
            raise InvalidInput(f"日期「{text}」看不懂", hint="寫成 YYYY-MM-DD，例如 2030-01-15", fields={field: "格式不對"})
        year, month, dd = (int(g) for g in m.groups())
        if year < 1911:
            raise InvalidInput(f"日期「{text}」看起來是民國年", hint=f"換成西元：{year + 1911}-{month:02d}-{dd:02d}",
                               fields={field: "民國年"})
        try:
            day = date(year, month, dd)
        except ValueError:
            raise InvalidInput(f"日期「{text}」不存在", fields={field: "日期不存在"}) from None
    if not allow_future and day > clock.today() + timedelta(days=1):
        raise InvalidInput(f"日期 {day.isoformat()} 在未來", hint="記帳日期不能超過明天；確認年份或月份有沒有打錯",
                           fields={field: "在未來"})
    return day.isoformat()


def parse_time(value: Any, field: str = "time") -> str | None:
    if value in (None, ""):
        return None
    m = TIME_RE.match(str(value).strip())
    if not m or int(m.group(1)) > 23:
        raise InvalidInput(f"時間「{value}」看不懂", hint="寫成 HH:MM，例如 19:30；不知道幾點就不要填",
                           fields={field: "格式不對"})
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_amount(value: Any, field: str = "amount") -> int:
    if isinstance(value, bool) or value is None:
        raise InvalidInput("金額要是整數（元）", fields={field: "缺少或不是數字"})
    if isinstance(value, float):
        if not value.is_integer():
            raise InvalidInput(f"金額 {value} 不是整數", hint="台幣以元為單位，不要有小數", fields={field: "不是整數"})
        value = int(value)
    if isinstance(value, str):
        text = value.replace(",", "").replace("，", "").strip()
        if not re.fullmatch(r"-?\d+", text):
            raise InvalidInput(f"金額「{value}」不是整數", hint="只寫數字，例如 260", fields={field: "不是整數"})
        value = int(text)
    if not isinstance(value, int):
        raise InvalidInput("金額要是整數（元）", fields={field: "不是數字"})
    if value == 0:
        raise InvalidInput("金額不能是 0", fields={field: "是 0"})
    return value


def _text(value: Any, limit: int, field: str) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise InvalidInput(f"{field} 太長（超過 {limit} 字）", fields={field: "太長"})
    return text


def _check_rules(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    kind = data["kind"]
    if kind not in KINDS:
        raise InvalidInput(f"交易種類「{kind}」不對", hint="expense（支出）／income（收入）／transfer（轉帳）")
    if kind != "expense" and data["amount"] < 0:
        raise InvalidInput("收入和轉帳的金額要是正數", hint="退款請記成支出並標 refund", fields={"amount": "是負數"})
    if kind == "transfer":
        if not data.get("to_account_id"):
            raise InvalidInput("轉帳要寫轉入帳戶（to）", fields={"to": "缺少"})
        if data["to_account_id"] == data["account_id"]:
            raise InvalidInput("轉出和轉入是同一個帳戶", fields={"to": "跟 from 一樣"})
        if data.get("purpose") and data["purpose"] not in PURPOSES:
            raise InvalidInput(f"轉帳用途「{data['purpose']}」不對", hint="、".join(PURPOSES))
    else:
        data["to_account_id"] = None
        data["purpose"] = None
    if data.get("financed") and kind != "expense":
        raise InvalidInput("只有支出可以標分期／借錢買（financed）")
    if data.get("category_id"):
        cat = refdata.category_by_id(conn, data["category_id"])
        if cat["kind"] != kind:
            label = {"expense": "支出", "income": "收入", "transfer": "轉帳"}
            raise InvalidInput(f"類別「{cat['name']}」是{label[cat['kind']]}類，不能記成{label[kind]}",
                               hint="換一個類別，或改交易種類", fields={"category": "種類不合"})
        if kind == "transfer" and not data.get("purpose"):
            data["purpose"] = cat["transfer_purpose"]
    elif kind == "income":
        raise InvalidInput("收入要有類別（例如 薪資、兼職）", fields={"category": "缺少"})
    if kind == "transfer" and not data.get("purpose"):
        data["purpose"] = "other"
    src = refdata.account_by_id(conn, data["account_id"])
    dst = refdata.account_by_id(conn, data["to_account_id"]) if data.get("to_account_id") else None
    if kind == "transfer":
        purpose = data.get("purpose")
        if src["kind"] == "credit_card" and purpose in CARD_NOT_SOURCE:
            raise InvalidInput(f"信用卡不能當「{PURPOSE_NAMES[purpose]}」的轉出", hint="只有代墊（幫別人先付）可以從信用卡出；其他的先選現金或銀行帳戶",
                               fields={"from": "信用卡"})
        if dst is not None and dst["kind"] == "credit_card" and purpose in CARD_NOT_DEST:
            raise InvalidInput(f"「{PURPOSE_NAMES[purpose]}」不能轉進信用卡", hint="轉進信用卡只有繳卡費", fields={"to": "信用卡"})
    _check_for_period(conn, data)
    _check_nature(conn, data)


def _check_nature(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    """「長期用」：只有生活費的花錢可以標（固定的、轉帳、收入本來就不在每天的日曆裡，不用標）。"""
    nature = (data.get("nature") or "").strip() or None
    if nature is None:
        data["nature"] = None
        return
    if nature != "stock":
        raise InvalidInput(f"性質「{nature}」不對", hint="目前只有 stock（長期用）；日常就不要填", fields={"nature": "只有 stock"})
    if data["kind"] != "expense" or data.get("financed"):
        raise InvalidInput("只有生活費的花錢可以標長期用", fields={"nature": "只有花錢"})
    cat = refdata.category_by_id(conn, data["category_id"]) if data.get("category_id") else None
    if cat is not None and not cat["is_living"]:
        raise InvalidInput(f"「{cat['name']}」是固定的項目，本來就不算在每天，不用標長期用", fields={"nature": "固定項目不用標"})
    data["nature"] = "stock"


def _check_for_period(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    """「算哪一期」（預繳／補繳）：只有花出去的用途可以（房租、手機費、存錢…），生活費是每天在花的，不能預繳；
    跟付款那天同一期就等於沒標。"""
    key = (data.get("for_period") or "").strip() or None
    if key is None:
        data["for_period"] = None
        return
    from . import periods
    if data["kind"] == "income":
        raise InvalidInput("收入沒有「算哪一期」", fields={"for_period": "收入不用"})
    try:
        target = periods.get(conn, key)
    except NotFound as e:
        raise InvalidInput(f"沒有「{key}」這一期", hint="期名長這樣：2030-01", fields={"for_period": "沒有這一期"}) from e
    if data["kind"] == "expense":
        cat = refdata.category_by_id(conn, data["category_id"]) if data.get("category_id") else None
        if cat is None or cat["is_living"]:
            raise InvalidInput("生活費是每天在花的，不能算到別的期", hint="預繳／補繳只有房租、手機費這類固定的錢可以",
                               fields={"for_period": "生活費不能預繳"})
    own = periods.period_for(conn, date.fromisoformat(data["date"]))
    data["for_period"] = None if own.key == target.key else target.key


# ---------------------------------------------------------------- 寫入

def _snapshot(conn: sqlite3.Connection, txn_id: str) -> dict[str, Any] | None:
    row = row_dict(conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone())
    if row is None:
        return None
    row["_items"] = [row_dict(r) for r in conn.execute(
        "SELECT name, qty, amount FROM txn_items WHERE txn_id = ? ORDER BY id", (txn_id,))]
    return row


def _items(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if not isinstance(value, list):
        raise InvalidInput("items 要是清單", fields={"items": "不是清單"})
    out = []
    for i, item in enumerate(value):
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            raise InvalidInput(f"第 {i + 1} 個品項沒有名稱", fields={f"items[{i}].name": "缺少"})
        amount = item.get("amount")
        out.append({"name": _text(item["name"], 100, f"items[{i}].name"),
                    "qty": float(item["qty"]) if item.get("qty") not in (None, "") else None,
                    "amount": parse_amount(amount, f"items[{i}].amount") if amount not in (None, "") else None})
    return out


def create(w: Writer, *, kind: str, amount: Any, date: Any, account_id: int, time: Any = None,
           category_id: int | None = None, to_account_id: int | None = None, purpose: str | None = None,
           counterparty: str | None = None, description: str = "", merchant: str = "", note: str = "",
           confidence: float | None = None, financed: bool = False, group_id: str | None = None,
           currency: str | None = None, foreign_amount: str | None = None, items: Any = None,
           for_period: str | None = None, nature: str | None = None, source: str = "cli", source_ref: str | None = None, item_idx: int = 0,
           payload_hash: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": kind, "amount": parse_amount(amount), "date": parse_date(date), "time": parse_time(time),
        "category_id": category_id, "account_id": account_id, "to_account_id": to_account_id, "purpose": purpose,
        "counterparty": _text(counterparty, 100, "counterparty") or None,
        "description": _text(description, MAX_DESC, "desc"), "merchant": _text(merchant, MAX_MERCHANT, "merchant"),
        "note": _text(note, MAX_NOTE, "note"),
        "confidence": None if confidence is None else max(0.0, min(1.0, float(confidence))),
        "financed": 1 if financed else 0, "group_id": group_id,
        "currency": (currency or None) and str(currency).upper(), "foreign_amount": foreign_amount and str(foreign_amount),
        "for_period": for_period, "nature": nature,
    }
    _check_rules(w.conn, data)
    item_rows = _items(items)
    txn_id = ids.txn_id()
    data.update({"id": txn_id, "status": "active", "batch_id": w.batch_id, "source": source,
                 "source_ref": source_ref, "item_idx": item_idx, "payload_hash": payload_hash,
                 "created_at": w.now, "updated_at": w.now})
    w.conn.execute(f"INSERT INTO transactions ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                   tuple(data.values()))
    for item in item_rows:
        w.conn.execute("INSERT INTO txn_items (txn_id, name, qty, amount) VALUES (?,?,?,?)",
                       (txn_id, item["name"], item["qty"], item["amount"]))
    w.audit("create", "transactions", txn_id, None, _snapshot(w.conn, txn_id))
    return get(w.conn, txn_id)


def update(w: Writer, txn_id: str, **changes: Any) -> dict[str, Any]:
    before = _snapshot(w.conn, txn_id)
    if before is None or before["status"] == "deleted":
        raise NotFound(f"找不到交易 {txn_id}")
    data = {k: before[k] for k in EDITABLE}
    new_items = changes.pop("items", None)
    for key, value in changes.items():
        if key not in EDITABLE:
            raise InvalidInput(f"不能改「{key}」")
        data[key] = value
    data["amount"] = parse_amount(data["amount"])
    data["date"] = parse_date(data["date"])
    data["time"] = parse_time(data["time"])
    data["description"] = _text(data["description"], MAX_DESC, "desc")
    data["note"] = _text(data["note"], MAX_NOTE, "note")
    _check_rules(w.conn, data)
    data["updated_at"] = w.now
    w.conn.execute(f"UPDATE transactions SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?",
                   (*data.values(), txn_id))
    if new_items is not None:
        w.conn.execute("DELETE FROM txn_items WHERE txn_id = ?", (txn_id,))
        for item in _items(new_items):
            w.conn.execute("INSERT INTO txn_items (txn_id, name, qty, amount) VALUES (?,?,?,?)",
                           (txn_id, item["name"], item["qty"], item["amount"]))
    w.audit("update", "transactions", txn_id, before, _snapshot(w.conn, txn_id))
    return get(w.conn, txn_id)


def delete(w: Writer, txn_id: str) -> dict[str, Any]:
    before = _snapshot(w.conn, txn_id)
    if before is None or before["status"] == "deleted":
        raise NotFound(f"找不到交易 {txn_id}")
    w.conn.execute("UPDATE transactions SET status = 'deleted', updated_at = ? WHERE id = ?", (w.now, txn_id))
    w.audit("delete", "transactions", txn_id, before, _snapshot(w.conn, txn_id))
    return get(w.conn, txn_id, include_deleted=True)


# ---------------------------------------------------------------- 讀取

SELECT_SQL = """
SELECT t.*, c.name AS category, g.name AS group_name, COALESCE(c.bucket_override, g.bucket_id) AS bucket_id, b.is_living,
       a.name AS account, a.kind AS account_kind, ta.name AS to_account
FROM transactions t
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN category_groups g ON g.id = c.group_id
LEFT JOIN buckets b ON b.id = COALESCE(c.bucket_override, g.bucket_id)
JOIN accounts a ON a.id = t.account_id
LEFT JOIN accounts ta ON ta.id = t.to_account_id
"""


def present(row: sqlite3.Row, items: list[dict] | None = None) -> dict[str, Any]:
    """對外的樣子（CLI JSON 與網站 API 共用）。"""
    from . import labels
    d = row_dict(row)
    lab = labels.split(desc=d["description"], merchant=d["merchant"], category=d["category"], note=d["note"], kind=d["kind"],
                     purpose=d["purpose"])
    return {
        "title": lab["title"], "detail": "・".join(lab["detail"]),        # 大項目＋細節（畫面與聊天助理都照這個寫）
        "id": d["id"], "date": d["date"], "time": d["time"], "kind": d["kind"], "amount": d["amount"],
        "category": d["category"], "category_id": d["category_id"], "group": d["group_name"],
        "bucket": d["bucket_id"], "is_living": bool(d["is_living"]),
        "account": d["account"], "account_id": d["account_id"], "account_kind": d["account_kind"],
        "to_account": d["to_account"], "to_account_id": d["to_account_id"], "purpose": d["purpose"],
        "counterparty": d["counterparty"], "desc": d["description"], "merchant": d["merchant"], "note": d["note"],
        "confidence": d["confidence"], "status": d["status"], "financed": bool(d["financed"]),
        "group_id": d["group_id"], "debt_id": d["debt_id"], "statement_line_id": d["statement_line_id"],
        "currency": d["currency"], "foreign_amount": d["foreign_amount"], "for_period": d["for_period"], "nature": d["nature"],
        "source": d["source"], "source_ref": d["source_ref"], "batch_id": d["batch_id"],
        "created_at": d["created_at"], "updated_at": d["updated_at"],
        "items": items if items is not None else [],
    }


def get(conn: sqlite3.Connection, txn_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
    row = conn.execute(SELECT_SQL + " WHERE t.id = ?", (txn_id,)).fetchone()
    if row is None or (row["status"] == "deleted" and not include_deleted):
        raise NotFound(f"找不到交易 {txn_id}", hint="用 /money_list 找編號")
    items = [row_dict(r) for r in conn.execute("SELECT name, qty, amount FROM txn_items WHERE txn_id = ? ORDER BY id",
                                               (txn_id,))]
    return present(row, items)


def query(conn: sqlite3.Connection, *, start: str | None = None, end: str | None = None,
          kind: str | None = None, category_id: int | None = None, group_id: int | None = None,
          account_id: int | None = None, uncategorized: bool = False, text: str | None = None,
          include_deleted: bool = False, limit: int = 50, cursor: str | None = None,
          order: str = "desc") -> dict[str, Any]:
    where, args = [], []
    if not include_deleted:
        where.append("t.status = 'active'")
    for column, value in (("t.date >= ?", start), ("t.date <= ?", end), ("t.kind = ?", kind),
                          ("t.category_id = ?", category_id), ("c.group_id = ?", group_id),
                          ("(t.account_id = ? OR t.to_account_id = ?)", account_id)):
        if value is not None:
            where.append(column)
            args.extend([value, value] if column.startswith("(t.account_id") else [value])
    if uncategorized:
        where.append("t.category_id IS NULL AND t.kind = 'expense'")
    if text:
        where.append("(t.description LIKE ? OR t.merchant LIKE ? OR t.note LIKE ?)")
        args.extend([f"%{text}%"] * 3)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM transactions t LEFT JOIN categories c ON c.id = t.category_id"
                         + clause, args).fetchone()[0]
    offset = int(cursor) if cursor and str(cursor).isdigit() else 0
    limit = max(1, min(int(limit), 500))
    direction = "DESC" if order == "desc" else "ASC"
    rows = conn.execute(SELECT_SQL + clause + f" ORDER BY t.date {direction}, COALESCE(t.time, '') {direction}, "
                        f"t.created_at {direction} LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
    items_by_txn: dict[str, list[dict]] = {}
    if rows:
        marks = ",".join("?" * len(rows))
        for r in conn.execute(f"SELECT txn_id, name, qty, amount FROM txn_items WHERE txn_id IN ({marks}) ORDER BY id",
                              [r["id"] for r in rows]):
            items_by_txn.setdefault(r["txn_id"], []).append({"name": r["name"], "qty": r["qty"], "amount": r["amount"]})
    nxt = offset + len(rows)
    return {"items": [present(r, items_by_txn.get(r["id"], [])) for r in rows], "total": total,
            "next_cursor": str(nxt) if nxt < total else None}


def payload_hash(payload: dict[str, Any]) -> str:
    """重送判斷用：同一份內容不管欄位順序都得到同一個值。"""
    import hashlib
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:32]
