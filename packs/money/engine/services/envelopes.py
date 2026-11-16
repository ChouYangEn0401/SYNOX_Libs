"""總覽「錢怎麼分」每一列的內容：計畫、做到多少，加上說明與「今天處理」的預設值。

用法：薪水先分成一格一格的用途（房租、手機費、存錢…），**記帳時自己就對上了**——
記到屬於這個用途的細項，那一格就跟著動；沒做到的那格後面有「今天處理」，按下去確認就記好。
這裡不寫商業規則，只把 plans／stats／recurring 已經算好的組成一列給畫面用：
- 算進來的細項（hover 說明）；
- 預繳／補繳：這一期付了別期的、別期付了這一期的（`for_period`）；
- action：按「今天處理」時預先填好的那一筆（種類、金額＝還差多少、帳戶、類別、轉到哪裡）。
  有固定項目範本就照範本；沒有就照這個用途最近一筆的記法；都沒有就只填金額，其他讓使用者選。
- items：這一格底下的固定項目（房租、手機費、AI 訂閱…）。**用途的金額是總額，項目是它的明細**，不是另一份要加的金額；
  other＝總額 − 項目加起來（電費這種金額不一定的）。有項目的格子，「今天處理」放在每個項目上，格子本身不放。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import clock
from . import periods, plans, recurring, settings, stats

TRANSFER_BUCKETS = {"saving": "saving", "invest": "investing"}          # 存錢、投資＝把錢搬去別的帳戶，不是花掉
SKIP = ("living", "debt")                                              # 生活費靠大類、分期靠排程，沒有「今天處理」

_RECENT_SQL = """
SELECT t.kind, t.amount, t.category_id, t.account_id, t.to_account_id, t.purpose, t.description
FROM transactions t
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN category_groups g ON g.id = c.group_id
WHERE t.status = 'active' AND t.kind <> 'income'
  AND (COALESCE(c.bucket_override, g.bucket_id) = :b OR (t.kind = 'transfer' AND c.id IS NULL AND t.purpose = :p))
ORDER BY t.date DESC, t.rowid DESC LIMIT 1
"""

_TAGGED_SQL = """
SELECT t.date, t.amount, t.for_period, t.description, c.name AS category,
       COALESCE(c.bucket_override, g.bucket_id) AS bucket_id, t.kind, t.purpose
FROM transactions t
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN category_groups g ON g.id = c.group_id
WHERE t.status = 'active' AND t.for_period IS NOT NULL AND (t.for_period = ? OR t.date BETWEEN ? AND ?)
"""


def _name(conn: sqlite3.Connection, table: str, row_id: int | None) -> str | None:
    if not row_id:
        return None
    r = conn.execute(f"SELECT name FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return r["name"] if r else None


def _bucket_of_tagged(r: sqlite3.Row) -> str | None:
    return r["bucket_id"] or stats.PURPOSE_BUCKET.get(r["purpose"] or "")


def _action(conn: sqlite3.Connection, bucket: dict[str, Any], left: int, templates: list[dict[str, Any]]) -> dict[str, Any]:
    """按「今天處理」時預先填好的一筆。"""
    b = bucket["id"]
    tpl = next((t for t in templates if t["bucket"] == b and t["amount"]), None) or next(
        (t for t in templates if t["bucket"] == b), None)
    purpose = TRANSFER_BUCKETS.get(b) or (tpl and tpl["purpose"]) or None
    recent = conn.execute(_RECENT_SQL, {"b": b, "p": purpose or ""}).fetchone()
    kind = "transfer" if (b in TRANSFER_BUCKETS or (tpl and tpl["kind"] == "transfer")
                          or (tpl is None and recent and recent["kind"] == "transfer")) else "expense"
    category = (tpl and tpl["category"]) or (_name(conn, "categories", recent["category_id"]) if recent else None)
    if category is None:
        linked = plans.bucket_categories(conn).get(b, [])
        category = linked[0] if len(linked) == 1 else None
    account = (tpl and tpl["account"]) or (_name(conn, "accounts", recent["account_id"]) if recent else None) \
        or settings.get(conn, "default_account")
    to = (tpl and tpl["to_account"]) or (_name(conn, "accounts", recent["to_account_id"]) if recent and kind == "transfer" else None)
    amount = (tpl and tpl["amount"]) or left
    return {"kind": kind, "purpose": purpose if kind == "transfer" else None, "amount": min(amount, left) if left > 0 else amount,
            "item": bucket["name"], "category": category, "account": account, "to": to,
            "from_template": bool(tpl), "date": clock.today().isoformat()}


def _item(conn: sqlite3.Connection, t: dict[str, Any], period: Any) -> dict[str, Any]:
    """一個固定項目在這一期的樣子；沒做的、這一期還在進行中，就帶「今天處理」預先填好的那一筆。"""
    done = bool(t["posted_txn_id"])
    action = None
    if not done and period.is_open():
        transfer = t["kind"] == "transfer"
        action = {"kind": "transfer" if transfer else "expense", "purpose": t.get("purpose") if transfer else None,
                  "amount": t["amount"], "item": t["name"], "category": None if transfer else t["category"],
                  "account": t["account"] or settings.get(conn, "default_account"), "to": t["to_account"],
                  "date": clock.today().isoformat(), "from_template": True}
    return {"id": t["id"], "name": t["name"], "amount": t["amount"], "day": t["day_of_month"], "due": t["due_date"],
            "state": t["state"], "level": t["level"], "done": done, "paid": t["posted_amount"], "action": action}


def rows(conn: sqlite3.Connection, plan: dict[str, Any], actual: dict[str, int], period: Any) -> list[dict[str, Any]]:
    """用途照資料庫排（生活費放第一個）：使用者自己加的用途也要出現在總覽。"""
    bucket_rows = plans.buckets(conn)
    order = [b for b in bucket_rows if b["id"] == "living"] + [b for b in bucket_rows if b["id"] != "living"]
    linked = plans.bucket_categories(conn)
    templates = recurring.status(conn, period.key)
    tagged = conn.execute(_TAGGED_SQL, (period.key, period.start.isoformat(), period.end.isoformat())).fetchall()
    asides = plans.setasides(conn, period.key)
    skipped = plans.skips(conn, period.key)
    out = []
    for b in order:
        bid = b["id"]
        p, a = int(plan["buckets"].get(bid) or 0), int(actual.get(bid) or 0)
        carry = plans.rollover_carry(conn, bid, period.key) if b["rollover"] else 0     # 滾存：前幾期沒用完（＋）或欠下來（−）的
        avail = p + carry                                     # 這一期能用的＝這一期撥的＋帶進來的（沒滾存就等於計畫）
        if bid == "living":
            state = "over" if p and a > p else "open"         # 生活費：還在花
        elif b["rollover"]:
            if not p and not a and not carry:
                state = "none"
            elif plans.close_enough(conn, b, avail, a):
                state = "done"                                # 會浮動的：差不多就算做完（剩的照樣留到下期）
            elif a > avail:
                state = "over"                                # 超過能用的（含前期留下的）
            else:
                state = "done" if a == avail and avail else "rolling"      # 用完了／還有留著等帳單來（不是沒做到，不提醒）
        elif not p and not a:
            state = "none"                                    # 沒計畫也沒用
        elif plans.close_enough(conn, b, p, a):
            state = "done"                                    # 會浮動的（水電、訂閱）：差不多就算做完
        elif a > p:
            state = "over"                                    # 比計畫多（或沒計畫卻用了）
        else:
            state = "done" if a == p else "todo"              # 做完了／還沒做完
        if bid in skipped and bid != "living":
            state = "skipped"                                 # 這期跳過：不做了，不提醒、不留錢
        coming = sum(t["amount"] for t in tagged if t["for_period"] == period.key and _bucket_of_tagged(t) == bid
                     and not period.contains(_date(t["date"])))      # 別天付的、算這一期的（預繳進來）
        ahead = sum(t["amount"] for t in tagged if t["for_period"] != period.key and _bucket_of_tagged(t) == bid
                    and period.contains(_date(t["date"])))           # 這期付的、算別期的（預繳出去）
        items = [_item(conn, t, period) for t in templates if t["bucket"] == bid and t["kind"] != "income"]
        if bid in skipped:
            for it in items:
                it["action"] = None                           # 整格跳過：底下的固定項目也不用處理
        planned = sum(i["amount"] or 0 for i in items)
        moved = int((plan.get("moves") or {}).get(bid) or 0)  # 這期挪入（＋）／挪出（−）；plan 已經套上了
        row = {"id": bid, "name": b["name"], "locked": b["locked"], "plan": p, "used": a, "left": avail - a, "state": state,
               "moved": moved, "planned": p - moved,         # planned＝規劃頁填的原本額度
               "rollover": b["rollover"], "carry": carry, "available": avail, "flex": b["flex"], "skipped": state == "skipped",
               "near": state == "done" and a != avail,       # 差不多就算做完的（畫面寫「≈ 做完」）
               # 滾存的格子：這期的額度撥好了沒（錢留在帳戶）；keep＝現在還留著、等帳單的錢
               "set_aside": asides.get(bid), "can_set_aside": bool(b["rollover"] and p and period.is_open() and bid not in asides and state == "rolling"),   # 帳單已經來了（用完／超過）就不用撥
               "keep": max(avail - a, 0) if b["rollover"] else 0,
               "categories": linked.get(bid, [])[:8], "prepaid_in": coming, "prepaid_out": ahead, "action": None,
               # 項目是總額的明細：「其他」＝總額 − 項目加起來（電費這種金額不一定的）；項目加起來比總額多就是負的
               "items": items, "items_planned": planned, "other": p - planned,
               "other_used": a - sum(i["paid"] or 0 for i in items if i["done"])}
        if state in ("todo", "rolling") and bid not in SKIP and period.is_open() and not items and avail - a > 0:
            # 滾存的格子也可以直接「今天處理」（帳單來了就記），不一定要先撥額度
            row["action"] = _action(conn, b, avail - a, templates)
        out.append(row)
    return out


def _date(text: str):
    from datetime import date
    return date.fromisoformat(text)


# ---------------------------------------------------------------- CLI：envelope list／do（聊天助理用）

def period_rows(conn: sqlite3.Connection, period_key: str | None = None) -> tuple[Any, list[dict[str, Any]]]:
    from . import stats
    p = periods.get(conn, period_key)
    s = stats.period_summary(conn, p.key)
    return p, rows(conn, plans.get(conn, p.key), s["allocation"]["actual"], p)


def find(conn: sqlite3.Connection, ref: str, period_key: str | None = None) -> tuple[Any, dict[str, Any], dict[str, Any] | None]:
    """「格子 id 或名字」或「固定項目的編號或名字」→ (期, 那一格, 那個項目或 None)。"""
    p, all_rows = period_rows(conn, period_key)
    text = str(ref).strip()
    for r in all_rows:
        if text in (r["id"], r["name"]):
            return p, r, None
    for r in all_rows:
        for it in r.get("items") or []:
            if text in (str(it["id"]), it["name"]):
                return p, r, it
    from ..errors import NotFound
    raise NotFound(f"這一期沒有「{text}」這一格或固定項目",
                   suggestions=[r["name"] for r in all_rows if r["id"] != "living"][:8], hint="用 /money_envelope 看有哪些")


def do(w: Any, ref: str, *, period_key: str | None = None, amount: int | None = None, day: str | None = None,
       category: str | None = None, source: str = "cli", source_ref: str | None = None) -> dict[str, Any]:
    """網站的「今天處理」：照那一格（或那個固定項目）預先填好的那一筆記下來。amount 給了就照給的（浮動的帳單）。
    固定項目用跟「一鍵記下固定的」同一個冪等鍵（重按不會多記）；整格用 source_ref（聊天助理給訊息編號）。"""
    from .. import schemas
    from ..errors import InvalidInput
    from . import ingest
    p, row, item = find(w.conn, ref, period_key)
    target = item or row
    if row.get("skipped"):
        raise InvalidInput(f"「{row['name']}」這期跳過了", hint=f"要記就先取消：/money_bucket_skip {row['id']} undo=yes")
    a = target.get("action")
    if not a:
        raise InvalidInput(f"「{target['name']}」這期沒有要處理的（做完了、或還沒有計畫）", hint="用 /money_envelope 看狀態")
    money = int(amount) if amount not in (None, "") else int(a["amount"] or 0)
    if money <= 0:
        raise InvalidInput("金額要大於 0", hint="金額不一定的帳單：給 amount=實際金額")
    cat = category or a.get("category")
    if a["kind"] == "transfer":
        rec: dict[str, Any] = {"type": "transfer", "amount": money, "date": day or a["date"], "from": a["account"],
                               "to": a["to"], "desc": a["item"]}
        if a.get("purpose"):
            rec["purpose"] = a["purpose"]
        if not a.get("to"):
            raise InvalidInput(f"「{target['name']}」不知道要轉到哪個帳戶", hint="在網站按一次「今天處理」選好，之後就照上次的")
    else:
        if not cat:
            raise InvalidInput(f"「{target['name']}」還沒指定記哪個類別，記了這一格也不會動",
                               hint="給 category=類別，或到記帳畫面「設定」那一格的 🔗 對應")
        rec = {"type": "expense", "amount": money, "date": day or a["date"], "desc": a["item"], "account": a["account"],
               "category": cat}
    if cat and a["kind"] == "transfer":
        rec["category"] = cat
    model = schemas.RECORD_TYPES[rec["type"]].model_validate(rec)
    if item is not None:
        handler = ingest.HANDLERS[rec["type"]]
        res = handler(w, model, 0, "recurring", recurring._ref(item["id"], p.key))
    else:
        handler = ingest.HANDLERS[rec["type"]]
        res = handler(w, model, 0, source, source_ref)
    warnings = res.pop("_warnings", [])
    return {"period": p.key, "bucket": row["id"], "name": target["name"], "item": item["id"] if item else None,
            "record": res, "warnings": warnings}
