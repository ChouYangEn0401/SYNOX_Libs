"""固定項目範本：薪水（含扣繳）、捐贈、房租、手機……每期一鍵記下，重按不會重複。

- 每個範本記：種類、金額（浮動的帳單可以空著，等帳單進來再記）、每月幾號、帳戶、類別、轉入帳戶、模板（薪水的扣繳）。
- post(period)：把這一期已經到期、還沒記的範本記下來（source=recurring、source_ref=recurring:<id>:<期>），
  upcoming=True 時連還沒到期的也記（提前繳，日期記今天）；走 ingest 同一套處理
  （猜類別、扣繳拆筆、轉帳用途），所以行為跟聊天助理記的一樣。雙月一次的只在單數月（以範本建立的那個月起算）記。
- status(period)：每個範本這一期記了沒、哪天到期。
"""
from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date
from typing import Any

from .. import clock
from ..db import Writer, row_dict
from ..errors import Conflict, InvalidInput, NotFound
from . import periods, refdata

FIELDS = ("name", "kind", "category_id", "account_id", "to_account_id", "amount", "day_of_month", "cadence", "template",
          "active", "note", "bucket_id")


def _due_in_period(p: periods.Period, day: int) -> date:
    """這一期裡「每月 day 號」那天（期別跨兩個月，取落在期內的那一天）。"""
    for y, m in ((p.start.year, p.start.month), (p.end.year, p.end.month)):
        d = date(y, m, min(day, calendar.monthrange(y, m)[1]))
        if p.start <= d <= p.end:
            return d
    return p.start


PURPOSES = ("saving", "investing", "prepay", "card_payment", "other")


def _present(conn: sqlite3.Connection, r: dict) -> dict[str, Any]:
    cat = refdata.category_by_id(conn, r["category_id"]) if r["category_id"] else None
    acc = refdata.account_by_id(conn, r["account_id"]) if r["account_id"] else None
    to = refdata.account_by_id(conn, r["to_account_id"]) if r["to_account_id"] else None
    tpl = json.loads(r["template"] or "{}")
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "purpose": tpl.get("purpose"),
            "category": cat and cat["name"],
            "group": cat and cat["group_name"], "bucket": cat and cat["bucket_id"], "account": acc and acc["name"],
            "to_account": to and to["name"], "amount": r["amount"], "day_of_month": r["day_of_month"],
            "cadence": r["cadence"], "bucket_id": r["bucket_id"], "template": tpl, "active": bool(r["active"]),
            "note": r["note"], "created_at": r["created_at"]}


def all_items(conn: sqlite3.Connection, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM recurring" + ("" if include_inactive else " WHERE active = 1") + " ORDER BY day_of_month, id"
    return [_present(conn, row_dict(r)) for r in conn.execute(sql)]


def _bucket_id(conn: sqlite3.Connection, bucket: str | None) -> str | None:
    """明說屬於哪個用途（id、名字、顯示名字都認）。生活費是大類在管的，固定項目不放那裡。"""
    if not bucket:
        return None
    b = refdata.bucket_by_name(conn, bucket)
    if b["is_living"]:
        raise InvalidInput("生活費是用大類管的，固定的項目要放在捐贈、固定支出、存錢這類用途底下")
    return b["id"]


def add(w: Writer, *, name: str, kind: str, amount: int | None, day_of_month: int = 5, category: str | None = None,
        account: str | None = None, to: str | None = None, cadence: str = "monthly", deductions: dict | None = None,
        purpose: str | None = None, note: str = "", bucket: str | None = None) -> dict[str, Any]:
    if kind not in ("expense", "income", "transfer"):
        raise InvalidInput(f"種類「{kind}」不對", hint="expense／income／transfer")
    if purpose and purpose not in PURPOSES:
        raise InvalidInput(f"用途「{purpose}」不對", hint="、".join(PURPOSES))
    if cadence not in ("monthly", "bimonthly"):
        raise InvalidInput("週期只有 monthly（每月）或 bimonthly（兩個月一次）")
    if not 1 <= day_of_month <= 31:
        raise InvalidInput("每月幾號要在 1–31 之間")
    if amount is not None and amount < 0:
        raise InvalidInput("金額不能是負的")
    name = (name or "").strip()
    if not name:
        raise InvalidInput("名稱不能空白")
    if w.conn.execute("SELECT 1 FROM recurring WHERE name = ?", (name,)).fetchone():
        raise Conflict(f"已經有固定項目「{name}」", hint="規劃頁點那個固定項目修改（或 /money_fixed id=…）")
    cat = refdata.find_category(w.conn, category) if category else None
    if category and cat is None:
        raise NotFound(f"找不到類別「{category}」", suggestions=refdata.category_suggestions(w.conn, category))
    if kind == "transfer" and not to:
        raise InvalidInput("轉帳的固定項目要給轉入帳戶（to）")
    acc = refdata.resolve_account(w.conn, account) if account else None
    dest = refdata.resolve_account(w.conn, to) if to else None
    bucket_id = _bucket_id(w.conn, bucket)
    template: dict[str, Any] = {"deductions": deductions} if deductions else {}
    if purpose and kind == "transfer":
        template["purpose"] = purpose                        # 固定的存錢／投資／繳卡費要算進對應的用途
    rid = w.conn.execute(
        "INSERT INTO recurring (name, kind, category_id, account_id, to_account_id, amount, day_of_month, cadence, template, "
        "note, bucket_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, kind, cat and cat["id"], acc and acc["id"], dest and dest["id"], amount, day_of_month, cadence,
         json.dumps(template, ensure_ascii=False), note, bucket_id, w.now)).lastrowid
    w.audit("create", "recurring", rid, None, row_dict(w.conn.execute("SELECT * FROM recurring WHERE id = ?", (rid,)).fetchone()))
    return _present(w.conn, row_dict(w.conn.execute("SELECT * FROM recurring WHERE id = ?", (rid,)).fetchone()))


def update(w: Writer, rid: int, **fields: Any) -> dict[str, Any]:
    before = row_dict(w.conn.execute("SELECT * FROM recurring WHERE id = ?", (rid,)).fetchone())
    if before is None:
        raise NotFound(f"找不到固定項目 #{rid}", hint="到規劃頁看固定項目")
    changes: dict[str, Any] = {}
    for key in ("name", "note", "amount", "day_of_month", "cadence", "active"):
        if fields.get(key) is not None:
            changes[key] = fields[key]
    if fields.get("clear_amount"):
        changes["amount"] = None                             # 改成浮動：每期等帳單進來再記
    if "day_of_month" in changes and not 1 <= changes["day_of_month"] <= 31:
        raise InvalidInput("每月幾號要在 1–31 之間")
    if "cadence" in changes and changes["cadence"] not in ("monthly", "bimonthly"):
        raise InvalidInput("週期只有 monthly（每月）或 bimonthly（兩個月一次）")
    if changes.get("amount") is not None and changes["amount"] < 0:
        raise InvalidInput("金額不能是負的")
    if "name" in changes and w.conn.execute("SELECT 1 FROM recurring WHERE name = ? AND id <> ?",
                                             (changes["name"], rid)).fetchone():
        raise Conflict(f"已經有固定項目「{changes['name']}」")
    if fields.get("purpose") is not None:
        purpose = fields["purpose"]
        if purpose and purpose not in PURPOSES:
            raise InvalidInput(f"用途「{purpose}」不對", hint="、".join(PURPOSES))
        tpl = json.loads(before["template"] or "{}")
        tpl.pop("purpose", None)
        if purpose:
            tpl["purpose"] = purpose
        changes["template"] = json.dumps(tpl, ensure_ascii=False)
    if "bucket" in fields and fields["bucket"] is not None:
        changes["bucket_id"] = _bucket_id(w.conn, fields["bucket"])         # 空字串＝不明說（照細項推）
    if fields.get("category") == "":
        changes["category_id"] = None                        # 不指定：記的時候用猜的
    elif fields.get("category"):
        cat = refdata.find_category(w.conn, fields["category"])
        if cat is None:
            raise NotFound(f"找不到類別「{fields['category']}」")
        changes["category_id"] = cat["id"]
    if fields.get("account") == "":
        changes["account_id"] = None                         # 不指定：用預設付款帳戶
    elif fields.get("account"):
        changes["account_id"] = refdata.resolve_account(w.conn, fields["account"])["id"]
    if fields.get("to"):
        changes["to_account_id"] = refdata.resolve_account(w.conn, fields["to"])["id"]
    if fields.get("deductions") is not None:
        changes["template"] = json.dumps({"deductions": fields["deductions"]} if fields["deductions"] else {}, ensure_ascii=False)
    changes = {k: v for k, v in changes.items() if before.get(k) != v}
    if changes:
        w.conn.execute(f"UPDATE recurring SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?", (*changes.values(), rid))
        w.audit("update", "recurring", rid, before, row_dict(w.conn.execute("SELECT * FROM recurring WHERE id = ?", (rid,)).fetchone()))
    return _present(w.conn, row_dict(w.conn.execute("SELECT * FROM recurring WHERE id = ?", (rid,)).fetchone()))


def _ref(rid: int, period_key: str) -> str:
    return f"recurring:{rid}:{period_key}"


def _applies(item: dict, p: periods.Period) -> bool:
    if item["cadence"] == "monthly":
        return True
    created = date.fromisoformat(item["created_at"][:10])
    due = _due_in_period(p, item["day_of_month"])
    return ((due.year * 12 + due.month) - (created.year * 12 + created.month)) % 2 == 0


def bucket_of_item(conn: sqlite3.Connection, item: dict) -> str | None:
    """這個固定項目算哪個用途：明說的優先；不然跟記一筆時算的規則一樣（細項→用途，轉帳沒細項看用途）。"""
    from . import stats
    if item.get("bucket_id"):
        return item["bucket_id"]
    if item["category"]:
        c = refdata.find_category(conn, item["category"])
        if c:
            return c["bucket_id"]
    if item["kind"] == "transfer":
        return stats.PURPOSE_BUCKET.get(item.get("purpose") or "")
    return None


def _matched(conn: sqlite3.Connection, item: dict, p: periods.Period) -> dict[str, Any] | None:
    """這一期你自己（或聊天助理）已經記過這一項了嗎？同細項、同轉入帳戶的一筆就算（付款日在這一期，或標了「算這一期」）。
    這樣自己先記好的，「記下到期的」不會再多記一筆（只要當天記對，到期時就會自己對上）。"""
    where = ("status = 'active' AND kind = ? AND ((date BETWEEN ? AND ? AND for_period IS NULL) OR for_period = ?) "
             "AND (source <> 'recurring' OR source_ref NOT LIKE 'recurring:%')")
    args: list[Any] = [item["kind"], p.start.isoformat(), p.end.isoformat(), p.key]
    if item["kind"] == "transfer":
        if not item["to_account"]:
            return None
        dest = refdata.resolve_account(conn, item["to_account"])
        where += " AND to_account_id = ?"
        args.append(dest["id"])
        if item.get("purpose"):
            where += " AND purpose = ?"
            args.append(item["purpose"])
    elif item["category"]:
        c = refdata.find_category(conn, item["category"])
        if c is None:
            return None
        where += " AND category_id = ?"
        args.append(c["id"])
    else:
        return None
    rows = conn.execute(f"SELECT id, amount FROM transactions WHERE {where} ORDER BY date DESC", args).fetchall()
    # 這一期同一項記了好幾筆（例：AI 訂閱同時訂了兩家）就加起來，id 取最近一筆
    return {"id": rows[0]["id"], "amount": sum(r["amount"] for r in rows)} if rows else None


def status(conn: sqlite3.Connection, period_key: str | None = None) -> list[dict[str, Any]]:
    p = periods.get(conn, period_key)
    today = clock.today()
    from . import plans
    skipped = plans.skips(conn, p.key)
    out = []
    for item in all_items(conn):
        if not _applies(item, p):
            continue
        due = _due_in_period(p, item["day_of_month"])
        posted = conn.execute("SELECT id, amount FROM transactions WHERE source = 'recurring' AND source_ref = ? "
                              "AND item_idx = 0 AND status = 'active'", (_ref(item["id"], p.key),)).fetchone()
        by_hand = None if posted else _matched(conn, item, p)
        posted = posted or by_hand
        if posted:
            state, level = ("已記（你記的）" if by_hand else "已記"), "good"
        elif bucket_of_item(conn, item) in skipped:
            state, level = "這期跳過", "skip"                    # 整格這期跳過：不提醒、一鍵記也不記它
        elif item["amount"] is None:
            state, level = "等帳單進來", "info"
        elif due <= today:
            state, level = "到期了，還沒記", "warn"
        else:
            state, level = f"{due.month}/{due.day} 到期", "info"
        out.append({**item, "due_date": due.isoformat(), "posted_txn_id": posted and posted["id"],
                    "posted_amount": posted and posted["amount"], "state": state, "level": level,
                    "bucket": bucket_of_item(conn, item)})
    return out


def _record(item: dict, due: date, default_account: str) -> dict[str, Any]:
    rec: dict[str, Any] = {"type": item["kind"], "amount": item["amount"], "date": due.isoformat(), "desc": item["name"]}
    if item["category"]:
        rec["category"] = item["category"]
    if item["kind"] == "transfer":
        rec.update({"from": item["account"] or default_account, "to": item["to_account"]})
        if item.get("purpose"):
            rec["purpose"] = item["purpose"]                 # 存錢／投資／繳卡費：記成那個用途
    else:
        rec["account"] = item["account"] or default_account
        if item["kind"] == "income" and item["template"].get("deductions"):
            rec["deductions"] = item["template"]["deductions"]
    return rec


def post(w: Writer, period_key: str | None = None, *, upcoming: bool = False) -> dict[str, Any]:
    """把這一期已經到期、還沒記的固定項目記下來；upcoming＝連還沒到期的也記（提前繳，日期記今天）。"""
    from .. import schemas
    from . import ingest, settings
    p = periods.get(w.conn, period_key)
    today = clock.today()
    default_account = settings.get(w.conn, "default_account") or "現金"
    created, skipped = [], []
    for item in status(w.conn, p.key):
        if item["posted_txn_id"] or item["amount"] is None or item["level"] == "skip":
            skipped.append({"name": item["name"], "state": item["state"]})
            continue
        due = date.fromisoformat(item["due_date"])
        if due > today:
            if not upcoming:
                skipped.append({"name": item["name"], "state": item["state"]})
                continue
            due = today
        rec = _record(item, due, default_account)
        model = schemas.RECORD_TYPES[rec["type"]].model_validate(rec)
        handler = ingest.HANDLERS[rec["type"]]
        res = handler(w, model, 0, "recurring", _ref(item["id"], p.key))
        res.pop("_warnings", None)
        created.append({"name": item["name"], "id": res["id"], "amount": res["amount"], "date": res["date"],
                        "category": res.get("category")})
    return {"period": p.key, "created": created, "skipped": skipped}
