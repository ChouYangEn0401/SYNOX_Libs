"""信用卡：帳單匯入、帳單 ↔ 記帳的對帳、補記、卡片狀態。

對帳的想法：刷卡當下已經記成「支出、帳戶＝這張卡」，帳單上的每一列應該對到一筆。
- 候選：這張卡、還沒被別的帳單列對走的支出，日期在帳單期間起 −7 天到結帳日（前期沒對上的也算）。
- 打分：金額完全相同 1.0／外幣金額相同 0.9／差 ≤ max(2%, 10 元) 0.5（＝金額不同）；
  加上日期距離（0 天 +0.3 … 7 天 +0.05）與商家相似度（商家對照表命中 +0.3，否則雙字詞相似度）。
- 一對一：分數高的先配。完全相同的金額、日期差 ≤5 天就算對上（帳單寫超商店名、記帳寫「飲料」很常見）。
- 列的種類：消費、退款（負數）、分期（期數由負債排程算，P7）、手續費（年費／利息／國外交易手續費／滯納金）、繳款。
- 沒對上的列 → 漏記（可以補記；補記前先看現金／銀行有沒有同一筆，可能只是記錯帳戶）；
  金額差一點 → 金額不同（可以改成帳單的金額）；記在卡上但帳單沒有 → 多半是下期才入帳。
- 等式：帳單總額 ＝ 各列加總；對不平就把差額列出來（可能有上期餘額或沒列到的項目）。
- 使用者手動配對時，把帳單上的寫法記進商家對照，下次自動對上。
"""
from __future__ import annotations

import calendar
import re
import sqlite3
from datetime import date, timedelta
from typing import Any

from .. import clock
from ..db import Writer, row_dict
from ..errors import Conflict, InvalidInput, NotFound
from . import classify, refdata, transactions as tx
from .dedup import similarity

LINE_TYPES = ("purchase", "installment", "fee", "refund", "payment")
FIX_ACTIONS = ("create", "use_ledger", "ignore", "move", "unignore", "make_debt")
# 行動支付的前綴（「某某pay-店名」「pay…*店名」）：只用通用的寫法，不列任何品牌（normalize 之後空白與 - 已經拿掉）
PREFIX_RE = re.compile(r"^(?:[a-z0-9]{1,10}pay|pay[a-z]{0,3}\*|行動支付|電子支付)", re.IGNORECASE)
INSTALLMENT_RE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*期|分期\D*(\d{1,2})\s*/\s*(\d{1,2})")
FEE_WORDS = ("年費", "循環利息", "利息", "手續費", "滯納金", "違約金", "服務費")
PAYMENT_WORDS = ("繳款", "自動扣繳", "轉帳繳", "已繳", "感謝您")


def merchant_key(text: str) -> str:
    s = refdata.normalize(text)
    s = PREFIX_RE.sub("", s)
    s = re.sub(r"[*#＊]+", "", s)
    s = re.sub(r"\d{3,}$", "", s)
    return s


def infer_type(desc: str, amount: int, installment: str | None = None) -> tuple[str, int | None, int | None]:
    m = INSTALLMENT_RE.search(installment or "") or INSTALLMENT_RE.search(desc or "")
    if m:
        nums = [g for g in m.groups() if g]
        return "installment", int(nums[0]), int(nums[1])
    if amount < 0:
        return ("payment" if any(w in desc for w in PAYMENT_WORDS) else "refund"), None, None
    if any(w in desc for w in FEE_WORDS):
        return "fee", None, None
    return "purchase", None, None


def _clamp(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _prev_month(d: date) -> tuple[int, int]:
    return (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)


# ---------------------------------------------------------------- 帳單寫入

def _card(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    acc = refdata.resolve_account(conn, name)
    if acc["kind"] != "credit_card":
        raise InvalidInput(f"「{acc['name']}」不是信用卡帳戶", hint="帳單要掛在信用卡帳戶上（到設定頁加帳戶，種類選信用卡）")
    return acc


def _audit_row(w: Writer, table: str, row_id: int, action: str, before: dict | None) -> None:
    after = row_dict(w.conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone())
    w.audit(action, table, row_id, before, after)


def save_statement(w: Writer, *, card: str, closing_date: str, total: int, lines: list[dict[str, Any]],
                   due_date: str | None = None, period_start: str | None = None, min_payment: int | None = None,
                   source: str = "cli", source_ref: str | None = None, lines_hash: str | None = None) -> dict[str, Any]:
    """寫入（或更新）一張帳單，接著對帳。同一張卡同一個結帳日只會有一張：重送內容一樣就不動。"""
    acc = _card(w.conn, card)
    closing = tx.parse_date(closing_date, "closing_date")
    existing = row_dict(w.conn.execute("SELECT * FROM card_statements WHERE account_id = ? AND closing_date = ?",
                                       (acc["id"], closing)).fetchone())
    if existing and lines_hash and existing["lines_hash"] == lines_hash:
        return {"status": "replayed", "statement": detail(w.conn, existing["id"])}
    if not period_start:
        y, m = _prev_month(date.fromisoformat(closing))
        period_start = (_clamp(y, m, date.fromisoformat(closing).day) + timedelta(days=1)).isoformat()
    fields = {"account_id": acc["id"], "period_start": tx.parse_date(period_start, "period_start"),
              "closing_date": closing, "due_date": tx.parse_date(due_date, "due_date", allow_future=True) if due_date else None,
              "total": total, "min_payment": min_payment, "source": source, "source_ref": source_ref,
              "lines_hash": lines_hash, "batch_id": w.batch_id, "updated_at": w.now}
    manual: dict[tuple, dict] = {}
    if existing:
        sid = existing["id"]
        for old in w.conn.execute("SELECT * FROM statement_lines WHERE statement_id = ?", (sid,)).fetchall():
            if old["manual"] or old["status"] == "ignored":
                manual[(old["txn_date"], old["amount"], old["description"])] = row_dict(old)
            before = row_dict(old)
            w.conn.execute("DELETE FROM statement_lines WHERE id = ?", (old["id"],))
            w.audit("delete", "statement_lines", old["id"], before, None)
        w.conn.execute(f"UPDATE card_statements SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                       (*fields.values(), sid))
        _audit_row(w, "card_statements", sid, "update", existing)
        status = "updated"
    else:
        fields["created_at"] = w.now
        sid = w.conn.execute(f"INSERT INTO card_statements ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                             tuple(fields.values())).lastrowid
        _audit_row(w, "card_statements", sid, "create", None)
        status = "created"
    for seq, line in enumerate(lines):
        ltype = line.get("type")
        auto_type, no, of = infer_type(line["desc"], line["amount"], line.get("installment"))
        ltype = ltype or auto_type
        if ltype not in LINE_TYPES:
            raise InvalidInput(f"帳單第 {seq + 1} 列的種類「{ltype}」不對", hint="、".join(LINE_TYPES))
        row = {"statement_id": sid, "seq": seq, "post_date": line.get("post_date"),
               "txn_date": tx.parse_date(line["date"], f"lines[{seq}].date"), "description": line["desc"][:200],
               "amount": line["amount"], "currency": line.get("currency"), "foreign_amount": line.get("foreign_amount"),
               "line_type": ltype, "installment_no": no, "installment_total": of,
               "status": {"installment": "installment", "fee": "fee", "payment": "payment"}.get(ltype, "missing")}
        kept = manual.get((row["txn_date"], row["amount"], row["description"]))
        if kept:
            row.update({"status": kept["status"], "manual": kept["manual"], "matched_txn_id": kept["matched_txn_id"],
                        "note": kept["note"]})
        lid = w.conn.execute(f"INSERT INTO statement_lines ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                             tuple(row.values())).lastrowid
        _audit_row(w, "statement_lines", lid, "create", None)
    reconcile(w, sid)
    return {"status": status, "statement": detail(w.conn, sid)}


# ---------------------------------------------------------------- 對帳

def _aliases(conn: sqlite3.Connection) -> dict[str, dict]:
    return {r["pattern"]: row_dict(r) for r in conn.execute("SELECT * FROM merchant_aliases")}


def score(line: dict, txn: dict, aliases: dict[str, dict]) -> tuple[float, str] | None:
    """回 (分數, 種類)；種類 exact／fx／near（near＝金額不同）。對不上回 None。"""
    la, ta = line["amount"], txn["amount"]
    days = abs((date.fromisoformat(line["txn_date"]) - date.fromisoformat(txn["date"])).days)
    if days > 10:
        return None
    lkey = merchant_key(line["description"])
    tkey = merchant_key(f"{txn['description']} {txn['merchant']}")
    alias = aliases.get(lkey)
    if alias and (refdata.normalize(alias["merchant"]) in tkey or tkey in refdata.normalize(alias["merchant"])):
        sim = 1.0
    else:
        sim = similarity(lkey, tkey)
    if la == ta:
        a, kind = 1.0, "exact"
    elif (line.get("foreign_amount") and txn.get("foreign_amount") and line.get("currency") == txn.get("currency")
          and abs(float(line["foreign_amount"]) - float(txn["foreign_amount"])) <= 0.01):
        a, kind = 0.9, "fx"
    elif line["line_type"] in ("purchase", "refund") and (la > 0) == (ta > 0):
        typo = sorted(str(abs(la))) == sorted(str(abs(ta)))           # 285 記成 258 這種數字對調
        if (abs(la - ta) <= max(round(abs(la) * 0.02), 10) or (days <= 1 and typo)
                or (days <= 1 and abs(la - ta) <= abs(la) * 0.15 and sim >= 0.2)):
            a, kind = 0.5, "near"
        else:
            return None
    else:
        return None
    d = {0: 0.3, 1: 0.25, 2: 0.2, 3: 0.15}.get(days, 0.05 if days <= 7 else 0.0)
    t = 0.3 * sim if sim >= 0.2 else 0.0
    if kind == "exact" and days > 5 and t == 0:
        return None
    if kind == "near" and days > 2 and t == 0:
        return None
    return round(a + d + t, 3), kind


def _window(st: dict) -> tuple[str, str]:
    start = date.fromisoformat(st["period_start"]) - timedelta(days=7)
    return start.isoformat(), st["closing_date"]


def _linked_elsewhere(conn: sqlite3.Connection, statement_id: int) -> set[str]:
    return {r[0] for r in conn.execute("SELECT matched_txn_id FROM statement_lines WHERE matched_txn_id IS NOT NULL "
                                       "AND statement_id <> ?", (statement_id,))}


def _candidates(conn: sqlite3.Connection, st: dict) -> list[dict]:
    start, end = _window(st)
    early = (date.fromisoformat(start) - timedelta(days=60)).isoformat()
    taken = _linked_elsewhere(conn, st["id"])
    rows = conn.execute(f"SELECT * FROM transactions WHERE status = 'active' AND {tx.CARD_CHARGE} AND account_id = ? "
                        "AND date BETWEEN ? AND ?", (st["account_id"], early, end)).fetchall()
    return [row_dict(r) for r in rows if r["id"] not in taken]


def reconcile(w: Writer, statement_id: int) -> dict[str, Any]:
    st = row_dict(w.conn.execute("SELECT * FROM card_statements WHERE id = ?", (statement_id,)).fetchone())
    if st is None:
        raise NotFound(f"找不到帳單 #{statement_id}")
    lines = [row_dict(r) for r in w.conn.execute("SELECT * FROM statement_lines WHERE statement_id = ? ORDER BY seq",
                                                 (statement_id,))]
    aliases = _aliases(w.conn)
    from . import debts as debts_service
    debts_service.unlink_statement(w, statement_id)
    for ln in lines:
        if ln["line_type"] == "installment" and ln["status"] != "ignored":
            sid = debts_service.link_statement_line(w, ln, st["account_id"])
            if sid != ln["debt_schedule_id"]:
                w.conn.execute("UPDATE statement_lines SET debt_schedule_id = ? WHERE id = ?", (sid, ln["id"]))
                _audit_row(w, "statement_lines", ln["id"], "update", ln)
                ln["debt_schedule_id"] = sid
    pinned = {ln["matched_txn_id"] for ln in lines if ln["manual"] and ln["matched_txn_id"]}
    cands = [c for c in _candidates(w.conn, st) if c["id"] not in pinned]
    open_lines = [ln for ln in lines if not ln["manual"] and ln["status"] != "ignored"
                  and ln["line_type"] in ("purchase", "refund", "fee")]
    pairs = []
    for ln in open_lines:
        for c in cands:
            sc = score(ln, c, aliases)
            if sc:
                pairs.append((sc[0], ln["id"], c["id"], sc[1]))
    pairs.sort(key=lambda p: -p[0])
    used_lines: dict[int, tuple] = {}
    used_txns: set[str] = set()
    for sc, lid, tid, kind in pairs:
        if lid in used_lines or tid in used_txns:
            continue
        used_lines[lid] = (tid, sc, kind)
        used_txns.add(tid)
    for ln in open_lines:
        hit = used_lines.get(ln["id"])
        if hit:
            new = {"matched_txn_id": hit[0], "score": hit[1], "status": "diff" if hit[2] == "near" else "matched"}
        else:
            new = {"matched_txn_id": None, "score": None,
                   "status": "fee" if ln["line_type"] == "fee" else "missing"}
        if any(ln[k] != v for k, v in new.items()):
            w.conn.execute("UPDATE statement_lines SET matched_txn_id = ?, score = ?, status = ? WHERE id = ?",
                           (new["matched_txn_id"], new["score"], new["status"], ln["id"]))
            _audit_row(w, "statement_lines", ln["id"], "update", ln)
    _refresh_status(w, statement_id)
    return detail(w.conn, statement_id)


def _refresh_status(w: Writer, statement_id: int) -> None:
    before = row_dict(w.conn.execute("SELECT * FROM card_statements WHERE id = ?", (statement_id,)).fetchone())
    d = detail(w.conn, statement_id)
    status = "paid" if d["paid"] else ("balanced" if d["balanced"] else "open")
    if before["status"] != status:
        w.conn.execute("UPDATE card_statements SET status = ?, updated_at = ? WHERE id = ?", (status, w.now, statement_id))
        _audit_row(w, "card_statements", statement_id, "update", before)


# ---------------------------------------------------------------- 讀取

def _line_out(conn: sqlite3.Connection, ln: dict) -> dict[str, Any]:
    out = {"id": ln["id"], "seq": ln["seq"], "date": ln["txn_date"], "post_date": ln["post_date"],
           "desc": ln["description"], "amount": ln["amount"], "type": ln["line_type"], "status": ln["status"],
           "installment": f"{ln['installment_no']}/{ln['installment_total']}" if ln["installment_no"] else None,
           "currency": ln["currency"], "foreign_amount": ln["foreign_amount"], "manual": bool(ln["manual"]),
           "debt_schedule_id": ln["debt_schedule_id"], "ledger": None, "suggestion": None}
    if ln["matched_txn_id"]:
        out["ledger"] = tx.get(conn, ln["matched_txn_id"], include_deleted=True)
    return out


def _other_account_guess(conn: sqlite3.Connection, ln: dict, card_id: int) -> dict | None:
    """漏記的那一列：現金／銀行有沒有同金額、日期差 ≤2 天的一筆（可能記錯帳戶）。"""
    d = date.fromisoformat(ln["txn_date"])
    row = conn.execute(
        "SELECT t.id FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.status = 'active' "
        "AND t.kind = 'expense' AND t.amount = ? AND t.account_id <> ? AND a.kind <> 'credit_card' "
        "AND t.date BETWEEN ? AND ? AND t.id NOT IN (SELECT matched_txn_id FROM statement_lines "
        "WHERE matched_txn_id IS NOT NULL) LIMIT 1",
        (ln["amount"], card_id, (d - timedelta(days=2)).isoformat(), (d + timedelta(days=2)).isoformat())).fetchone()
    return tx.get(conn, row[0]) if row else None


def detail(conn: sqlite3.Connection, statement_id: int) -> dict[str, Any]:
    st = row_dict(conn.execute("SELECT s.*, a.name AS card FROM card_statements s JOIN accounts a ON a.id = s.account_id "
                               "WHERE s.id = ?", (statement_id,)).fetchone())
    if st is None:
        raise NotFound(f"找不到帳單 #{statement_id}", hint="用 /money_card 看有哪些帳單")
    lines = [row_dict(r) for r in conn.execute("SELECT * FROM statement_lines WHERE statement_id = ? ORDER BY seq",
                                               (statement_id,))]
    out_lines = []
    for ln in lines:
        o = _line_out(conn, ln)
        if ln["line_type"] == "installment" and not ln["debt_schedule_id"] and ln["status"] != "ignored":
            o["suggestion"] = {"action": "make_debt",
                               "text": "還沒有這筆分期的計畫：建一個，之後每期自動對上、算進負債償還"}
        elif ln["line_type"] == "installment" and ln["debt_schedule_id"]:
            sched = conn.execute("SELECT s.no, d.periods, d.name FROM debt_schedule s JOIN debts d ON d.id = s.debt_id "
                                 "WHERE s.id = ?", (ln["debt_schedule_id"],)).fetchone()
            if sched:
                o["debt"] = {"name": sched["name"], "no": sched["no"], "periods": sched["periods"]}
        if ln["status"] == "missing" and ln["line_type"] in ("purchase", "refund"):
            other = _other_account_guess(conn, ln, st["account_id"])
            if other:
                o["suggestion"] = {"action": "move", "txn": other,
                                   "text": f"現金／銀行有一筆 {other['date'][5:]}「{other['desc']}」{other['amount']:,}，可能記錯帳戶"}
        out_lines.append(o)
    sums: dict[str, int] = {}
    for o in out_lines:
        sums[o["status"]] = sums.get(o["status"], 0) + o["amount"]
    lines_total = sum(o["amount"] for o in out_lines)
    matched_ledger = sum(o["ledger"]["amount"] for o in out_lines if o["ledger"] and o["status"] in ("matched", "diff"))
    start, end = _window(st)
    linked = {o["ledger"]["id"] for o in out_lines if o["ledger"]}
    taken = _linked_elsewhere(conn, statement_id)
    ledger_only = []
    for r in conn.execute(f"SELECT id, date FROM transactions WHERE status = 'active' AND {tx.CARD_CHARGE} "
                          "AND account_id = ? AND date BETWEEN ? AND ? ORDER BY date",
                          (st["account_id"], st["period_start"], end)):
        if r["id"] in linked or r["id"] in taken:
            continue
        t = tx.get(conn, r["id"])
        near_close = date.fromisoformat(r["date"]) >= date.fromisoformat(end) - timedelta(days=3)
        t["reason"] = "刷卡日離結帳日太近，多半下期才入帳" if near_close else "帳單上找不到：可能下期才入帳、刷錯卡，或金額記錯"
        ledger_only.append(t)
    problems = [o for o in out_lines if o["status"] in ("missing", "diff", "fee")
                or (o["type"] == "installment" and o["status"] != "ignored" and not o["debt_schedule_id"])]
    gap = st["total"] - lines_total
    paid_row = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE status = 'active' "
                            "AND kind = 'transfer' AND to_account_id = ? AND date >= ?",
                            (st["account_id"], st["closing_date"])).fetchone()[0]
    due = date.fromisoformat(st["due_date"]) if st["due_date"] else None
    today = clock.today()
    return {
        "id": st["id"], "card": st["card"], "account_id": st["account_id"], "period_start": st["period_start"],
        "closing_date": st["closing_date"], "due_date": st["due_date"], "total": st["total"],
        "min_payment": st["min_payment"], "status": st["status"],
        "paid": paid_row >= st["total"] > 0, "paid_amount": paid_row,
        "days_to_due": (due - today).days if due else None,
        "lines": out_lines, "ledger_only": ledger_only, "problems": len(problems),
        "sums": {"matched": sums.get("matched", 0), "diff_statement": sums.get("diff", 0),
                 "missing": sums.get("missing", 0), "installment": sums.get("installment", 0),
                 "fee": sums.get("fee", 0), "payment": sums.get("payment", 0), "ignored": sums.get("ignored", 0),
                 "lines_total": lines_total, "matched_ledger": matched_ledger,
                 "diff_ledger": sum(o["ledger"]["amount"] for o in out_lines if o["status"] == "diff" and o["ledger"])},
        "gap": gap, "balanced": not problems and gap == 0,
    }


def statements(conn: sqlite3.Connection, card: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
    args: list[Any] = []
    where = ""
    if card:
        where = " WHERE s.account_id = ?"
        args.append(_card(conn, card)["id"])
    rows = conn.execute("SELECT s.id FROM card_statements s" + where + " ORDER BY s.closing_date DESC LIMIT ?",
                        (*args, limit)).fetchall()
    out = []
    for r in rows:
        d = detail(conn, r["id"])
        out.append({k: d[k] for k in ("id", "card", "closing_date", "due_date", "total", "status", "paid",
                                       "problems", "gap", "days_to_due")})
    return out


def _next_closing(day: int, today: date) -> date:
    this = _clamp(today.year, today.month, day)
    if today <= this:
        return this
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return _clamp(y, m, day)


def card_status(conn: sqlite3.Connection, account: dict[str, Any]) -> dict[str, Any]:
    today = clock.today()
    last = conn.execute("SELECT id FROM card_statements WHERE account_id = ? ORDER BY closing_date DESC LIMIT 1",
                        (account["id"],)).fetchone()
    last_d = detail(conn, last["id"]) if last else None
    if account.get("card_closing_day"):
        nxt = _next_closing(account["card_closing_day"], today)
        y, m = _prev_month(nxt)
        cycle_start = _clamp(y, m, account["card_closing_day"]) + timedelta(days=1)
    else:
        nxt, cycle_start = None, today.replace(day=1)
    since = date.fromisoformat(last_d["closing_date"]) + timedelta(days=1) if last_d else cycle_start
    taken = {r[0] for r in conn.execute("SELECT matched_txn_id FROM statement_lines WHERE matched_txn_id IS NOT NULL")}
    unbilled_rows = [r for r in conn.execute(
        f"SELECT id, amount FROM transactions WHERE status = 'active' AND {tx.CARD_CHARGE} AND account_id = ? "
        "AND date >= ? AND date <= ?", (account["id"], since.isoformat(), today.isoformat())) if r["id"] not in taken]
    pending_prev = [t for t in (last_d["ledger_only"] if last_d else []) if t["id"] not in {r["id"] for r in unbilled_rows}]
    unbilled = sum(r["amount"] for r in unbilled_rows) + sum(t["amount"] for t in pending_prev)
    unpaid = last_d["total"] - last_d["paid_amount"] if last_d and not last_d["paid"] else 0
    alert = None
    if last_d and not last_d["paid"] and last_d["days_to_due"] is not None and last_d["days_to_due"] <= 5:
        when = "今天" if last_d["days_to_due"] == 0 else (f"{last_d['days_to_due']} 天後" if last_d["days_to_due"] > 0
                                                         else f"已過 {-last_d['days_to_due']} 天")
        alert = f"{account['name']} 卡費 {last_d['total']:,} {when}到期"
    elif last_d and last_d["problems"]:
        alert = f"{account['name']} 帳單有 {last_d['problems']} 筆沒對上"
    return {"account": account["name"], "account_id": account["id"], "closing_day": account.get("card_closing_day"),
            "due_day": account.get("card_due_day"), "limit": account.get("card_limit"),
            "next_closing": nxt.isoformat() if nxt else None, "unbilled": unbilled,
            "unbilled_count": len(unbilled_rows) + len(pending_prev), "unpaid": unpaid,
            "usage": round((unpaid + unbilled) / account["card_limit"], 3) if account.get("card_limit") else None,
            "last_statement": last_d, "alert": alert}


def all_cards(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [card_status(conn, a) for a in refdata.accounts(conn) if a["kind"] == "credit_card"]


# ---------------------------------------------------------------- 處理沒對上的列

def _line(conn: sqlite3.Connection, line_id: int) -> dict[str, Any]:
    ln = row_dict(conn.execute("SELECT * FROM statement_lines WHERE id = ?", (line_id,)).fetchone())
    if ln is None:
        raise NotFound(f"找不到帳單明細 #{line_id}", hint="用 /money_card statement=<帳單編號> 看每一列的編號")
    return ln


def _learn_alias(w: Writer, line_desc: str, txn: dict) -> None:
    if txn.get("kind") != "expense":
        return                                              # 代墊（借出轉帳）對到帳單：不學「這家店＝借出」，之後補記會被轉帳類別擋下
    key = merchant_key(line_desc)
    merchant = txn["merchant"] or txn["desc"]
    if not key or not merchant or similarity(key, merchant_key(merchant)) >= 0.5:
        return
    w.conn.execute("INSERT INTO merchant_aliases (pattern, merchant, category_id, created_at) VALUES (?,?,?,?) "
                   "ON CONFLICT(pattern) DO UPDATE SET merchant = excluded.merchant, category_id = excluded.category_id",
                   (key, merchant, txn["category_id"], w.now))


def match(w: Writer, line_id: int, txn_id: str) -> dict[str, Any]:
    ln = _line(w.conn, line_id)
    t = tx.get(w.conn, txn_id)
    other = w.conn.execute("SELECT id FROM statement_lines WHERE matched_txn_id = ? AND id <> ?", (txn_id, line_id)).fetchone()
    if other:
        raise Conflict(f"這筆記帳已經對到帳單明細 #{other[0]}", hint="先把那一列改掉，或選另一筆")
    status = "matched" if t["amount"] == ln["amount"] else "diff"
    w.conn.execute("UPDATE statement_lines SET matched_txn_id = ?, status = ?, manual = 1 WHERE id = ?",
                   (txn_id, status, line_id))
    _audit_row(w, "statement_lines", line_id, "update", ln)
    _learn_alias(w, ln["description"], t)
    _refresh_status(w, ln["statement_id"])
    return detail(w.conn, ln["statement_id"])


def fix(w: Writer, line_id: int, action: str, *, txn_id: str | None = None) -> dict[str, Any]:
    if action not in FIX_ACTIONS:
        raise InvalidInput(f"處理方式「{action}」不對", hint="create（補記）／use_ledger（改成帳單金額）／move（把那筆改到這張卡）"
                                                          "／ignore（不用管）／unignore")
    ln = _line(w.conn, line_id)
    st = row_dict(w.conn.execute("SELECT * FROM card_statements WHERE id = ?", (ln["statement_id"],)).fetchone())
    if action == "ignore":
        w.conn.execute("UPDATE statement_lines SET status = 'ignored', manual = 1 WHERE id = ?", (line_id,))
        _audit_row(w, "statement_lines", line_id, "update", ln)
    elif action == "unignore":
        w.conn.execute("UPDATE statement_lines SET status = 'missing', manual = 0, matched_txn_id = NULL WHERE id = ?",
                       (line_id,))
        _audit_row(w, "statement_lines", line_id, "update", ln)
        reconcile(w, ln["statement_id"])
    elif action == "use_ledger":
        if ln["status"] != "diff" or not ln["matched_txn_id"]:
            raise Conflict("這一列不是「金額不同」，沒有可以改的記帳")
        tx.update(w, ln["matched_txn_id"], amount=ln["amount"], confidence=None)
        w.conn.execute("UPDATE statement_lines SET status = 'matched', manual = 1 WHERE id = ?", (line_id,))
        _audit_row(w, "statement_lines", line_id, "update", ln)
    elif action == "move":
        target = txn_id
        if not target:
            other = _other_account_guess(w.conn, ln, st["account_id"])
            if other is None:
                raise NotFound("找不到可以改到這張卡的記帳", hint="給 txn=<那一筆帳的編號>")
            target = other["id"]
        tx.update(w, target, account_id=st["account_id"], confidence=None)
        return match(w, line_id, target)
    elif action == "make_debt":
        if ln["line_type"] != "installment":
            raise Conflict("這一列不是分期")
        from . import debts as debts_service
        no = ln["installment_no"] or 1
        total = ln["installment_total"] or no
        name = re.sub(r"分期|\d{1,2}\s*/\s*\d{1,2}\s*期?", "", ln["description"]).strip(" -　") or ln["description"]
        txn_day = date.fromisoformat(ln["txn_date"])
        start = debts_service._add_months(txn_day, -(no - 1), txn_day.day)
        debts_service.create(w, name=name, kind="card_installment", periods=total, per_period=ln["amount"],
                             start_date=start.isoformat(), account=refdata.account_by_id(w.conn, st["account_id"])["name"],
                             paid_before=txn_day.isoformat(), note="從信用卡帳單的分期列建立", source="statement",
                             source_ref=f"stmt-debt:{st['id']}:{ln['seq']}")
        reconcile(w, ln["statement_id"])
    elif action == "create":
        if ln["matched_txn_id"]:
            raise Conflict("這一列已經對到記帳了")
        alias = _aliases(w.conn).get(merchant_key(ln["description"]))
        cat = refdata.category_by_id(w.conn, alias["category_id"]) if alias and alias["category_id"] else None
        if cat is not None and cat["kind"] != "expense":
            cat = None                                      # 以前學到的轉帳類別（借出）不能拿來補記支出
        if cat is None and ln["line_type"] == "fee":
            key = "fee.interest" if "利息" in ln["description"] else ("fee.late" if "滯納" in ln["description"] or "違約" in ln["description"]
                                                                      else "misc")
            cat = refdata.system_category(w.conn, key)
        if cat is None:
            cat, _ = classify.guess(w.conn, ln["description"], kind="expense")
        t = tx.create(w, kind="expense", amount=ln["amount"], date=ln["txn_date"], account_id=st["account_id"],
                      category_id=cat["id"] if cat else None, description=(alias["merchant"] if alias else ln["description"]),
                      merchant=ln["description"][:100], currency=ln["currency"], foreign_amount=ln["foreign_amount"],
                      note="從信用卡帳單補記", source="statement", source_ref=f"stmt:{st['id']}:{ln['seq']}")
        w.conn.execute("UPDATE statement_lines SET matched_txn_id = ?, status = 'matched', manual = 1 WHERE id = ?",
                       (t["id"], line_id))
        _audit_row(w, "statement_lines", line_id, "update", ln)
    _refresh_status(w, ln["statement_id"])
    return detail(w.conn, ln["statement_id"])


def pay(w: Writer, statement_id: int, *, from_account: str | None = None, amount: int | None = None,
        day: str | None = None) -> dict[str, Any]:
    """記一筆繳卡費（轉帳：銀行 → 卡，不算支出）。"""
    d = detail(w.conn, statement_id)
    acc = refdata.account_by_id(w.conn, d["account_id"])
    src = refdata.resolve_account(w.conn, from_account) if from_account else (
        refdata.account_by_id(w.conn, acc["pay_from_account_id"]) if acc.get("pay_from_account_id") else None)
    if src is None:
        raise InvalidInput("不知道從哪個帳戶繳", hint="給 from_account=帳戶，或到設定頁替這張卡設扣款帳戶")
    pay_amount = amount or (d["total"] - d["paid_amount"])
    if pay_amount <= 0:
        raise Conflict("這張帳單已經繳清了")
    cat = refdata.system_category(w.conn, "card_payment")
    t = tx.create(w, kind="transfer", amount=pay_amount, date=day or clock.today().isoformat(), account_id=src["id"],
                  to_account_id=acc["id"], purpose="card_payment", category_id=cat["id"],
                  description=f"繳 {acc['name']} 卡費（{d['closing_date'][5:]} 結帳）", source="statement",
                  source_ref=f"stmt-pay:{statement_id}:{pay_amount}")
    before = row_dict(w.conn.execute("SELECT * FROM card_statements WHERE id = ?", (statement_id,)).fetchone())
    w.conn.execute("UPDATE card_statements SET paid_txn_id = ? WHERE id = ?", (t["id"], statement_id))
    _audit_row(w, "card_statements", statement_id, "update", before)
    _refresh_status(w, statement_id)
    return {"payment": t, "statement": detail(w.conn, statement_id)}
