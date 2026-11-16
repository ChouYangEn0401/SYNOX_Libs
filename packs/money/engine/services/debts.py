"""負債與分期：卡分期、自借款、信貸，每一筆都有每月排程；外加借給別人的錢（應收）。

- 卡分期：購買記一筆 financed 支出（不算進當期的薪水分配）；每期出現在信用卡帳單上，對帳時對到排程的那一期。
- 自借款：從自己的準備金借（例如緊急預備金）；還款＝生活帳戶 → 準備金的轉帳（purpose=debt，帶 debt_id）。
- 信貸：負債帳戶（kind=loan）；還款＝銀行 → 負債帳戶的轉帳；利息另記支出。
- 薪水分配的「負債償還」＝當期到期的排程加總＋沒連到排程的還款轉帳（stats.py），不重複算。
- 排程的列不是交易。第一期吸收除不盡的零頭（銀行慣例，舊表的 1674＋1666×11＝20000 就是這樣）。
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date
from typing import Any

from .. import clock
from ..db import Writer, row_dict
from ..errors import Conflict, InvalidInput, NotFound
from . import refdata, transactions as tx

KINDS = {"card_installment": "卡分期", "self_loan": "自借款", "credit_loan": "信貸"}
NATURES = {"positive": "正向（進修、投資自己）", "negative": "負向（消費）", "wear": "生活正常損耗"}


def _add_months(d: date, months: int, day: int) -> date:
    index = d.year * 12 + (d.month - 1) + months
    y, m = index // 12, index % 12 + 1
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def schedule_amounts(principal: int, periods: int, per_period: int | None) -> list[int]:
    if periods < 1:
        raise InvalidInput("期數至少 1 期")
    if per_period is None:
        per_period = principal // periods
    rest = principal - per_period * periods
    amounts = [per_period] * periods
    amounts[0] += rest                       # 零頭放第一期
    if amounts[0] <= 0:
        raise InvalidInput("每期金額乘上期數超過本金太多", hint="確認本金與每期金額")
    return amounts


def _audit(w: Writer, table: str, row_id: int, action: str, before: dict | None) -> None:
    w.audit(action, table, row_id, before, row_dict(w.conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()))


def create(w: Writer, *, name: str, kind: str, periods: int, start_date: str, principal: int | None = None,
           per_period: int | None = None, account: str | None = None, lender: str | None = None,
           nature: str = "positive", purchase_txn_id: str | None = None, note: str = "", paid_before: str | None = None,
           source: str = "cli", source_ref: str | None = None) -> dict[str, Any]:
    """建一筆負債與排程。paid_before：這天以前到期的期數當作已付（舊表匯入、帳單上已經到第 N 期時用）。"""
    if kind not in KINDS:
        raise InvalidInput(f"負債種類「{kind}」不對", hint="card_installment（卡分期）／self_loan（自借款）／credit_loan（信貸）")
    if nature not in NATURES:
        raise InvalidInput(f"負債性質「{nature}」不對", hint="positive／negative／wear")
    if principal is None and per_period is None:
        raise InvalidInput("本金和每期金額至少給一個")
    if principal is None:
        principal = per_period * periods
    amounts = schedule_amounts(principal, periods, per_period)
    start = date.fromisoformat(tx.parse_date(start_date, "start_date", allow_future=True))
    acc = refdata.resolve_account(w.conn, account) if account else None
    if kind == "card_installment" and (acc is None or acc["kind"] != "credit_card"):
        raise InvalidInput("卡分期要掛在信用卡帳戶上", fields={"account": "要是信用卡"})
    if kind == "credit_loan" and acc is not None and acc["kind"] != "loan":
        raise InvalidInput("信貸要掛在負債帳戶（kind=loan）上", hint="先到設定頁加一個負債帳戶（種類選負債）")
    lend = refdata.resolve_account(w.conn, lender) if lender else None
    if source_ref:
        dup = w.conn.execute("SELECT id FROM debts WHERE source = ? AND source_ref = ?", (source, source_ref)).fetchone()
        if dup:
            return {"status": "replayed", "debt": get(w.conn, dup[0])}
    did = w.conn.execute(
        "INSERT INTO debts (name, kind, principal, periods, per_period, nature, account_id, lender_account_id, start_date, "
        "purchase_txn_id, note, source, source_ref, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name.strip()[:100], kind, principal, periods, amounts[-1], nature, acc and acc["id"], lend and lend["id"],
         start.isoformat(), purchase_txn_id, note, source, source_ref, w.now)).lastrowid
    _audit(w, "debts", did, "create", None)
    cutoff = date.fromisoformat(paid_before) if paid_before else None
    for i, amount in enumerate(amounts):
        due = _add_months(start, i, start.day)
        status = "paid" if cutoff and due < cutoff else "due"
        sid = w.conn.execute("INSERT INTO debt_schedule (debt_id, no, due_date, amount, status, note) VALUES (?,?,?,?,?,?)",
                             (did, i + 1, due.isoformat(), amount, status,
                              "建立前就到期，當作已付" if status == "paid" else "")).lastrowid
        _audit(w, "debt_schedule", sid, "create", None)
    return {"status": "created", "debt": get(w.conn, did)}


def _present(conn: sqlite3.Connection, d: dict) -> dict[str, Any]:
    rows = [row_dict(r) for r in conn.execute("SELECT * FROM debt_schedule WHERE debt_id = ? ORDER BY no", (d["id"],))]
    today = clock.today().isoformat()
    paid = [r for r in rows if r["status"] == "paid"]
    due = [r for r in rows if r["status"] == "due"]
    nxt = due[0] if due else None
    overdue = [r for r in due if r["due_date"] < today]
    acc = refdata.account_by_id(conn, d["account_id"]) if d["account_id"] else None
    lend = refdata.account_by_id(conn, d["lender_account_id"]) if d["lender_account_id"] else None
    return {"id": d["id"], "name": d["name"], "kind": d["kind"], "kind_label": KINDS[d["kind"]],
            "nature": d["nature"], "nature_label": NATURES[d["nature"]], "principal": d["principal"],
            "periods": d["periods"], "per_period": d["per_period"], "start_date": d["start_date"],
            "account": acc and acc["name"], "lender": lend and lend["name"], "note": d["note"], "active": bool(d["active"]),
            "paid_count": len(paid), "paid_amount": sum(r["amount"] for r in paid),
            "remaining": sum(r["amount"] for r in due), "next": nxt, "overdue": overdue,
            "done": not due, "schedule": rows, "purchase_txn_id": d["purchase_txn_id"]}


def get(conn: sqlite3.Connection, debt_id: int) -> dict[str, Any]:
    d = row_dict(conn.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone())
    if d is None:
        raise NotFound(f"找不到負債 #{debt_id}", hint="用 /money_debt 看有哪些負債")
    return _present(conn, d)


def all_debts(conn: sqlite3.Connection, *, include_done: bool = False) -> list[dict[str, Any]]:
    out = [_present(conn, row_dict(r)) for r in conn.execute("SELECT * FROM debts WHERE active = 1 ORDER BY start_date, id")]
    return out if include_done else [d for d in out if not d["done"]]


def due_between(conn: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    """這段期間到期的每一期（薪水分配的「負債償還」用）。"""
    rows = conn.execute("SELECT s.*, d.name, d.kind FROM debt_schedule s JOIN debts d ON d.id = s.debt_id "
                        "WHERE d.active = 1 AND s.status <> 'skipped' AND s.due_date BETWEEN ? AND ? ORDER BY s.due_date",
                        (start, end)).fetchall()
    return [row_dict(r) for r in rows]


def update(w: Writer, debt_id: int, **fields: Any) -> dict[str, Any]:
    before = row_dict(w.conn.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone())
    if before is None:
        raise NotFound(f"找不到負債 #{debt_id}")
    changes: dict[str, Any] = {}
    for key in ("name", "note"):
        if fields.get(key) is not None:
            changes[key] = fields[key]
    if fields.get("kind"):
        if fields["kind"] not in KINDS:
            raise InvalidInput(f"負債種類「{fields['kind']}」不對")
        changes["kind"] = fields["kind"]
    if fields.get("nature"):
        if fields["nature"] not in NATURES:
            raise InvalidInput(f"負債性質「{fields['nature']}」不對")
        changes["nature"] = fields["nature"]
    if fields.get("account"):
        changes["account_id"] = refdata.resolve_account(w.conn, fields["account"])["id"]
    if fields.get("lender"):
        changes["lender_account_id"] = refdata.resolve_account(w.conn, fields["lender"])["id"]
    if fields.get("active") is not None:
        changes["active"] = int(fields["active"])
    if changes:
        w.conn.execute(f"UPDATE debts SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?", (*changes.values(), debt_id))
        _audit(w, "debts", debt_id, "update", before)
    return get(w.conn, debt_id)


def _schedule_row(conn: sqlite3.Connection, debt_id: int, no: int | None) -> dict[str, Any]:
    if no is None:
        row = conn.execute("SELECT * FROM debt_schedule WHERE debt_id = ? AND status = 'due' ORDER BY no LIMIT 1",
                           (debt_id,)).fetchone()
        if row is None:
            raise Conflict(f"負債 #{debt_id} 已經全部還完了")
    else:
        row = conn.execute("SELECT * FROM debt_schedule WHERE debt_id = ? AND no = ?", (debt_id, no)).fetchone()
        if row is None:
            raise NotFound(f"負債 #{debt_id} 沒有第 {no} 期")
    return row_dict(row)


def pay(w: Writer, debt_id: int, *, no: int | None = None, from_account: str | None = None,
        day: str | None = None) -> dict[str, Any]:
    """還一期。卡分期只標記已付（錢會在信用卡帳單裡）；自借款、信貸記一筆轉帳。"""
    d = get(w.conn, debt_id)
    row = _schedule_row(w.conn, debt_id, no)
    if row["status"] == "paid":
        raise Conflict(f"第 {row['no']} 期已經付過了")
    txn = None
    if d["kind"] in ("self_loan", "credit_loan"):
        dest = d["lender"] if d["kind"] == "self_loan" else d["account"]
        if not dest:
            raise InvalidInput("不知道要還到哪個帳戶", hint="用 /money_debt do=edit id=<編號> lender=<借自哪個帳戶>（自借款）或 account=<負債帳戶>（信貸）")
        src = from_account or (d["account"] if d["kind"] == "self_loan" and d["account"] else None)
        if not src:
            from . import settings
            src = settings.get(w.conn, "default_account")
        cat = refdata.system_category(w.conn, "debt.self_loan" if d["kind"] == "self_loan" else "debt.credit_loan")
        t = tx.create(w, kind="transfer", amount=row["amount"], date=day or clock.today().isoformat(),
                      account_id=refdata.resolve_account(w.conn, src)["id"],
                      to_account_id=refdata.resolve_account(w.conn, dest)["id"], purpose="debt",
                      category_id=cat and cat["id"], description=f"{d['name']} 第 {row['no']}/{d['periods']} 期",
                      source="debt", source_ref=f"debt:{debt_id}:{row['no']}")
        w.conn.execute("UPDATE transactions SET debt_id = ? WHERE id = ?", (debt_id, t["id"]))
        txn = tx.get(w.conn, t["id"])
    w.conn.execute("UPDATE debt_schedule SET status = 'paid', paid_txn_id = ? WHERE id = ?",
                   (txn and txn["id"], row["id"]))
    _audit(w, "debt_schedule", row["id"], "update", row)
    return {"debt": get(w.conn, debt_id), "paid_no": row["no"], "payment": txn}


def link_statement_line(w: Writer, line: dict, card_account_id: int) -> int | None:
    """帳單上的分期列 → 對到卡分期排程的那一期（同一張卡、金額 ±10、期數相同或到期日在帳單期間附近）。"""
    rows = w.conn.execute(
        "SELECT s.*, d.periods FROM debt_schedule s JOIN debts d ON d.id = s.debt_id WHERE d.active = 1 "
        "AND d.kind = 'card_installment' AND d.account_id = ? AND s.statement_line_id IS NULL AND s.status <> 'skipped' "
        "ORDER BY s.due_date", (card_account_id,)).fetchall()
    best = None
    for r in rows:
        if abs(r["amount"] - line["amount"]) > 10:
            continue
        if line.get("installment_no") and line["installment_no"] != r["no"]:
            continue
        if line.get("installment_total") and line["installment_total"] != r["periods"]:
            continue
        gap = abs((date.fromisoformat(r["due_date"]) - date.fromisoformat(line["txn_date"])).days)
        if not line.get("installment_no") and gap > 45:
            continue
        if best is None or gap < best[0]:
            best = (gap, r)
    if best is None:
        return None
    r = row_dict(best[1])
    before = dict(r)
    before.pop("periods", None)
    w.conn.execute("UPDATE debt_schedule SET statement_line_id = ?, status = 'paid' WHERE id = ?", (line["id"], r["id"]))
    _audit(w, "debt_schedule", r["id"], "update", before)
    return r["id"]


def unlink_statement(w: Writer, statement_id: int) -> None:
    for r in w.conn.execute("SELECT s.* FROM debt_schedule s JOIN statement_lines l ON l.id = s.statement_line_id "
                            "WHERE l.statement_id = ?", (statement_id,)).fetchall():
        before = row_dict(r)
        w.conn.execute("UPDATE debt_schedule SET statement_line_id = NULL, status = 'due' WHERE id = ?", (r["id"],))
        _audit(w, "debt_schedule", r["id"], "update", before)


_RECEIVABLE_SQL = (
    "SELECT t.*, a.name AS to_name, f.name AS from_name, a.kind AS to_kind, f.kind AS from_kind FROM transactions t "
    "JOIN accounts a ON a.id = t.to_account_id JOIN accounts f ON f.id = t.account_id "
    "WHERE t.status = 'active' AND t.kind = 'transfer' AND (a.kind = 'receivable' OR f.kind = 'receivable') "
    "ORDER BY t.date, t.rowid")


def _receivable_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """進出應收帳戶（代墊款）的每一筆轉帳：借出（轉進應收）與收回（從應收轉出），先發生的在前。"""
    return conn.execute(_RECEIVABLE_SQL).fetchall()


def pending_advances(conn: sqlite3.Connection) -> dict[str, Any]:
    """代墊還沒收回的（總覽的「處理報帳」提醒用）：每一筆借出，扣掉同一個對象已經收回的——先借的先還，從最早的扣起。

    回 {total, count, oldest, who:[{counterparty, named, amount, count}], items:[{id, date, amount, label, counterparty}],
    owe:{同樣的形狀}}；外層＝別人欠我，`owe`＝我欠別人（別人先幫我付）。同一個人兩邊都有就抵掉、只留淨額。
    沒有的時候 total 是 0。對象沒寫就用帳戶的名字（named＝False：畫面上不當成人名講）。"""
    rows = _receivable_rows(conn)
    per: dict[str, dict[str, Any]] = {}
    for r in rows:
        lent = r["to_kind"] == "receivable"
        named = bool((r["counterparty"] or "").strip())
        who = (r["counterparty"] or "").strip() or (r["to_name"] if lent else r["from_name"])
        slot = per.setdefault(who, {"named": named, "lent": [], "collected": 0})
        slot["named"] = slot["named"] or named
        if lent:
            slot["lent"].append({"id": r["id"], "date": r["date"], "amount": r["amount"], "left": r["amount"],
                                 "label": (r["description"] or r["merchant"] or "").strip(), "counterparty": who})
        else:
            slot["collected"] += r["amount"]
    items: list[dict[str, Any]] = []
    for slot in per.values():
        paid = slot["collected"]
        for e in slot["lent"]:
            take = min(e["left"], paid)
            e["left"] -= take
            paid -= take
        slot["excess"] = paid                                  # 這個對象收回的比借的多
    # 切成代墊時沒寫對象、之後才寫「公司還我 1,910」：有名字的收回多出來的，去扣沒寫對象的借出（先借的先還）
    pool = sorted((e for slot in per.values() if not slot["named"] for e in slot["lent"] if e["left"] > 0), key=lambda e: e["date"])
    for slot in per.values():
        if slot["named"] and slot["excess"] > 0:
            for e in pool:
                take = min(e["left"], slot["excess"])
                e["left"] -= take
                slot["excess"] -= take
    for slot in per.values():
        items += [{**{k: e[k] for k in ("id", "date", "label", "counterparty")}, "named": slot["named"], "amount": e["left"]}
                  for e in slot["lent"] if e["left"] > 0]
    owe_items = _open_payables(conn)
    _net_same_person(items, owe_items)
    out = _side(items)
    out["owe"] = _side(owe_items)
    return out


def _side(items: list[dict[str, Any]]) -> dict[str, Any]:
    """一邊（別人欠我／我欠別人）：還沒還的每一筆 → 總額、筆數、最早、每個人一行（金額大的在前）。"""
    items = sorted((i for i in items if i["amount"] > 0), key=lambda x: x["date"])
    per: dict[str, dict[str, Any]] = {}
    for i in items:
        p = per.setdefault(i["counterparty"], {"counterparty": i["counterparty"], "named": i["named"], "amount": 0, "count": 0})
        p["amount"] += i["amount"]
        p["count"] += 1
    return {"total": sum(i["amount"] for i in items), "count": len(items), "oldest": items[0]["date"] if items else None,
            "who": sorted(per.values(), key=lambda x: -x["amount"]), "items": items}


_PAYABLE_SQL = (
    "SELECT t.*, a.name AS acc_name, ta.name AS to_name FROM transactions t "
    "JOIN accounts a ON a.id = t.account_id LEFT JOIN accounts ta ON ta.id = t.to_account_id "
    "WHERE t.status = 'active' AND ((t.kind = 'expense' AND a.kind = 'loan') OR (t.kind = 'transfer' AND t.purpose = 'repay')) "
    "ORDER BY t.date, t.rowid")


def _payable_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """別人先幫我付（記在負債帳戶的花錢）與我還對方（purpose repay 的轉帳），先發生的在前。"""
    return conn.execute(_PAYABLE_SQL).fetchall()


def _open_payables(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """我欠別人、還沒還的每一筆：同一個人先欠的先還（跟代墊的收回一樣）。"""
    per: dict[str, dict[str, Any]] = {}
    for r in _payable_rows(conn):
        owe = r["kind"] == "expense"
        named = bool((r["counterparty"] or "").strip())
        who = (r["counterparty"] or "").strip() or (r["acc_name"] if owe else r["to_name"])
        slot = per.setdefault(who, {"named": named, "owed": [], "repaid": 0})
        slot["named"] = slot["named"] or named
        if owe:
            slot["owed"].append({"id": r["id"], "date": r["date"], "amount": r["amount"], "counterparty": who,
                                 "label": (r["description"] or r["merchant"] or "").strip()})
        else:
            slot["repaid"] += r["amount"]
    out: list[dict[str, Any]] = []
    for slot in per.values():
        paid = slot["repaid"]
        for e in slot["owed"]:
            take = min(e["amount"], paid)
            paid -= take
            if e["amount"] - take > 0:
                out.append({**e, "amount": e["amount"] - take, "named": slot["named"]})
    return out


def _net_same_person(theirs: list[dict[str, Any]], mine: list[dict[str, Any]]) -> None:
    """同一個人兩邊都有（小明欠我 500、我也欠小明 200）：互相抵掉，只剩淨額那一邊（小明欠我 300）。
    只抵有寫名字的；從最早的一筆扣起。直接改兩邊的 amount。"""
    def total(items: list[dict[str, Any]], who: str) -> int:
        return sum(i["amount"] for i in items if i["counterparty"] == who and i["named"])

    for who in {i["counterparty"] for i in mine if i["named"]}:
        off = min(total(theirs, who), total(mine, who))
        for side in (theirs, mine):
            left = off
            for i in sorted((i for i in side if i["counterparty"] == who and i["named"]), key=lambda x: x["date"]):
                take = min(i["amount"], left)
                i["amount"] -= take
                left -= take


def people(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """每個人的淨額（/money_lend）：代墊出去、收回、他先幫我付、我還他，net＞0＝他欠我、＜0＝我欠他。"""
    out: dict[str, dict[str, Any]] = {}

    def slot(who: str) -> dict[str, Any]:
        return out.setdefault(who, {"counterparty": who, "lent": 0, "collected": 0, "owed": 0, "repaid": 0, "last_date": None})

    for r in receivables(conn):
        s = slot(r["counterparty"])
        s["lent"], s["collected"], s["last_date"] = r["lent"], r["collected"], r["last_date"]
    for r in _payable_rows(conn):
        owe = r["kind"] == "expense"
        s = slot((r["counterparty"] or "").strip() or (r["acc_name"] if owe else r["to_name"]))
        s["owed" if owe else "repaid"] += r["amount"]
        s["last_date"] = max(s["last_date"] or r["date"], r["date"])
    for s in out.values():
        s["net"] = (s["lent"] - s["collected"]) - (s["owed"] - s["repaid"])
        s["direction"] = "they_owe" if s["net"] > 0 else ("i_owe" if s["net"] < 0 else "even")
    return sorted(out.values(), key=lambda x: -abs(x["net"]))


def receivables(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """借給別人的錢：依對象（沒寫就依應收帳戶）加總借出與收回。"""
    rows = _receivable_rows(conn)
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        lent = r["to_kind"] == "receivable"
        account = r["to_name"] if lent else r["from_name"]
        who = r["counterparty"] or account
        item = out.setdefault(who, {"counterparty": who, "account": account, "lent": 0, "collected": 0, "last_date": None})
        item["lent" if lent else "collected"] += r["amount"]
        item["last_date"] = r["date"]
    for item in out.values():
        item["outstanding"] = item["lent"] - item["collected"]
    # 沒寫對象的借出（對象＝帳戶名）被有名字的收回多出來的部分扣掉，不要變成「代墊款 +1,910」與「公司 −1,910」兩行
    unnamed = [it for it in out.values() if it["counterparty"] == it["account"]]
    for it in out.values():
        if it["counterparty"] != it["account"] and it["outstanding"] < 0:
            for u in unnamed:
                take = min(max(u["outstanding"], 0), -it["outstanding"])
                if take:
                    it["collected"] -= take
                    u["collected"] += take
                    it["outstanding"] += take
                    u["outstanding"] -= take
    return sorted(out.values(), key=lambda x: -x["outstanding"])
