"""ingest：一批混合紀錄寫進去（聊天助理的主要入口；/money_add 也走這裡，行為才一致）。

- 先用 schemas 驗證整份（拼錯欄位、型別不對都在這裡擋），再在一個寫入交易裡逐筆處理；
  任何一筆失敗整批不寫（全有或全無），錯誤指出是第幾筆的哪個欄位。
- 帳戶找不到 → 結束碼 3 附建議；類別找不到 → 放待分類並警告；類別沒給 → 猜（classify）。
- source_ref＋第幾筆是冪等鍵（item_idx＝第幾筆×100，薪水扣繳等附屬列用 +1、+2…）：
  同內容重送 → replayed（不新增）；內容不同 → 衝突，除非 replace=True（只有支出、轉帳能直接取代）。
- 收據圖 sha256 相同、金額相同 → replayed。
- 支出與收入寫入後立刻跟既有的比（dedup.check），像的話回 possible_duplicate 和 duplicate_of，讓 agent 當下問。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date as _date
from typing import Any, Callable

from .. import db, ids
from ..db import Writer
from ..errors import Conflict, InvalidInput, LedgerError, NotFound
from ..schemas import (BalanceRecord, BillRecord, CardStatementRecord, DayNoteRecord, ExpenseRecord,
                               HoldingsRecord, IncomeRecord, IngestPayload, IOU_DIRECTIONS, IouRecord, InstallmentPurchaseRecord, InvestPnlRecord,
                               TradeRecord, TransferRecord, ValidationError, translate)
from . import (attachments, balances, bills, cards, classify, debts, dedup, invest, labels, notes, periods, refdata,
                                settings, transactions as tx)

STEP = 100


def parse(text_or_obj: str | dict | list) -> IngestPayload:
    obj = text_or_obj
    if isinstance(text_or_obj, str):
        try:
            obj = json.loads(text_or_obj)
        except json.JSONDecodeError as e:
            raise InvalidInput(f"JSON 壞掉了（第 {e.lineno} 行第 {e.colno} 字：{e.msg}）",
                               hint="確認引號、逗號、括號都有成對") from None
    if isinstance(obj, list):
        obj = {"records": obj}
    elif isinstance(obj, dict) and "type" in obj and "records" not in obj:
        obj = {"records": [obj]}
    if not isinstance(obj, dict):
        raise InvalidInput("內容要是 JSON 物件（{\"records\": [...]}）")
    try:
        return IngestPayload.model_validate(obj)
    except ValidationError as e:
        raise translate(e) from None


def _account(conn: sqlite3.Connection, name: str | None) -> dict[str, Any]:
    if not name:
        name = settings.get(conn, "default_account") or "現金"
    return refdata.resolve_account(conn, name)


def _category(conn, name, *, kind, desc, time, date, merchant="") -> tuple[dict | None, bool, str, list[str]]:
    """回 (細項, 是不是猜的, 理由, 警告)。"""
    warnings: list[str] = []
    if name:
        cat = refdata.find_category(conn, name)
        if cat and cat["kind"] == kind:
            return cat, False, "你給的", warnings
        sugg = refdata.category_suggestions(conn, name)
        if cat:
            warnings.append(f"類別「{name}」不能用在這種交易，改用猜的")
        else:
            warnings.append(f"沒有「{name}」這個類別，改用猜的" + (f"（是不是：{'、'.join(sugg)}）" if sugg else ""))
    cat, reason = classify.guess(conn, desc, time=time, date=date, merchant=merchant, kind=kind)
    return cat, cat is not None, reason, warnings


# ---------------------------------------------------------------- 冪等與重複

class Replay(Exception):
    """這筆已經記過了（同 source_ref 同內容、或同一張收據）：不新增，回原本那筆。"""

    def __init__(self, txn_id: str, why: str):
        super().__init__(why)
        self.txn_id, self.why = txn_id, why


def _existing(w: Writer, source: str, ref: str | None, idx: int, phash: str, *, replace: bool,
              replaceable: bool) -> str | None:
    """回要被取代的那筆 id（replace 時）；沒有既有的回 None；重送丟 Replay；內容不同丟 Conflict。"""
    if not ref:
        return None
    row = w.conn.execute("SELECT id, payload_hash, status FROM transactions WHERE source = ? AND source_ref = ? "
                         "AND item_idx = ?", (source, ref, idx)).fetchone()
    if row is None:
        return None
    if row["payload_hash"] == phash:
        raise Replay(row["id"], "同一則訊息重送" + ("（那筆之前被刪掉了，沒有重新記）" if row["status"] == "deleted" else ""))
    if replace and replaceable:
        return row["id"]
    old = tx.get(w.conn, row["id"], include_deleted=True)
    raise Conflict(f"這則訊息（{ref}）之前記過，但內容不一樣（原本：{old['date']} {old['desc']} {old['amount']:,}）",
                   hint="內容有改就加上 replace（取代原本那筆；只有支出、轉帳可以）；不是同一則訊息就換一個 source_ref",
                   extra={"existing": old})


def _base_result(w: Writer, index: int, rtype: str, t: dict[str, Any]) -> dict[str, Any]:
    return {"index": index, "type": rtype, "status": "would_create" if w.dry_run else "created", "id": t["id"],
            "date": t["date"], "time": t["time"], "amount": t["amount"], "desc": t["desc"], "category": t["category"],
            "account": t["account"]}


def _replayed(w: Writer, index: int, rtype: str, r: Replay) -> dict[str, Any]:
    t = tx.get(w.conn, r.txn_id, include_deleted=True)
    res = _base_result(w, index, rtype, t)
    res.update({"status": "replayed", "replay_reason": r.why,
                "_warnings": [f"「{t['desc'] or t['amount']}」{r.why}，沒有新增"]})
    return res


def _flag_duplicate(w: Writer, res: dict[str, Any]) -> None:
    pair = dedup.check(w, res["id"])
    if not pair:
        return
    other = pair["duplicate_of"]
    res.update({"status": "possible_duplicate", "duplicate_of": other, "pair_id": pair["pair_id"]})
    when = other["date"][5:] + (" " + other["time"] if other["time"] else "")
    res["_warnings"].append(f"「{res['desc'] or res['amount']}」看起來跟 {when}「{other['desc']}」{other['amount']:,} 重複"
                            f"（{pair['reason']}）；兩筆都先算，請問使用者要怎麼處理（/money_dedup {pair['pair_id']} keep_both／delete_newer／merge）")


# ---------------------------------------------------------------- 各種紀錄

def _expense(w: Writer, rec: ExpenseRecord, index: int, source: str, ref: str | None,
             replace: bool = False) -> dict[str, Any]:
    date = tx.parse_date(rec.date)
    time = tx.parse_time(rec.time)
    account = _account(w.conn, rec.account)
    amount = -abs(rec.amount) if rec.refund else rec.amount
    phash = tx.payload_hash(rec.model_dump(by_alias=True))
    attach = attachments.inspect(rec.attach) if rec.attach else None
    try:
        target = _existing(w, source, ref, index * STEP, phash, replace=replace, replaceable=True)
        if attach and target is None:
            again = attachments.replay_of(w.conn, attach["sha256"], amount)
            if again:
                raise Replay(again, "同一張收據重送")
    except Replay as r:
        return _replayed(w, index, "expense", r)
    rec.desc, rec.note = labels.tidy(rec.desc, rec.note, kind="expense")     # 網路轉之類銀行的字搬到備註
    cat, guessed, reason, warnings = _category(w.conn, rec.category, kind="expense", desc=rec.desc, time=time,
                                               date=date, merchant=rec.merchant)
    for_period, for_note = _for_period(w, rec.for_period, rec.desc, date,
                                       living=cat is None or bool(cat["is_living"]) or rec.financed)
    if for_note:
        warnings.append(for_note)
    fields = dict(amount=amount, date=date, time=time, account_id=account["id"],
                  category_id=cat["id"] if cat else None, description=rec.desc, merchant=rec.merchant,
                  note=rec.note, confidence=rec.confidence, financed=rec.financed, currency=rec.currency,
                  foreign_amount=rec.foreign_amount, for_period=for_period, nature=rec.nature)
    items = [i.model_dump() for i in rec.items]
    if target:
        t = tx.update(w, target, **fields, items=items)
        w.conn.execute("UPDATE transactions SET payload_hash = ? WHERE id = ?", (phash, target))
    else:
        t = tx.create(w, kind="expense", **fields, items=items, source=source, source_ref=ref,
                      item_idx=index * STEP, payload_hash=phash)
    if attach:
        attachments.save(w, t["id"], attach)
    res = _base_result(w, index, "expense", t)
    res.update({"category_guessed": guessed, "category_reason": reason})
    if target:
        res["status"] = "replaced"
    if cat is None:
        warnings.append(f"「{rec.desc or rec.amount}」{reason}，先放待分類")
    if rec.confidence is not None and rec.confidence < 0.6:
        warnings.append(f"「{rec.desc or rec.amount}」把握度 {rec.confidence:.1f}，放進待確認")
    over = _over_each(cat, amount)                           # 軟上限：只在這一刻輕輕說一句，不擋也不亮燈
    if over:
        warnings.append(over)
    if not rec.financed:
        _allowance_note(w, t, warnings)
    res["_warnings"] = warnings
    if not target:
        _flag_duplicate(w, res)
    return res


def _for_period(w: Writer, given: str | None, desc: str, date: str, *, living: bool) -> tuple[str | None, str | None]:
    """算哪一期：有給就用（驗證在 transactions）；沒給就看 desc 有沒有「11月」這種寫法。
    生活費不猜。回 (期名, 提醒的話)。"""
    if given:
        return given, None
    if living:
        return None, None
    day = _date.fromisoformat(date)
    key = periods.guess_for(w.conn, desc, day)
    if not key:
        return None, None
    way = "預繳" if key > periods.period_for(w.conn, day).key else "補繳"
    return key, f"「{desc}」算進 {key} 期（{way}）；不對的話在明細改「算哪一期」"


def _allowance_note(w: Writer, txn: dict[str, Any], warnings: list[str]) -> None:
    """額度提醒（services/allowance）：這一筆讓哪一格做到了、超過了，或生活費走太快——一句話。"""
    from . import allowance
    note = allowance.note_for(w.conn, txn)
    if note:
        warnings.append(note)


def _over_each(cat: dict[str, Any] | None, amount: int) -> str | None:
    """這一筆比「每一次最多花多少」還多的話，回一句話（軟性提醒：不擋，超過才說）。

    只有設了「每次」上限的細項才會講；講完就算了，不亮燈、不擋、不會進健康檢查。
    """
    if not cat or (cat.get("cap_basis") or "period") != "each":
        return None
    cap = int(cat.get("default_budget") or 0)
    if not cap or amount <= cap:
        return None
    return f"{cat['name']} {amount:,}，比你抓的每次 {cap:,} 多 {amount - cap:,}（只是提醒，沒擋）"


def _pension_account(w: Writer) -> tuple[dict[str, Any], list[str]]:
    for a in refdata.accounts(w.conn):
        if a["kind"] == "pension":
            return a, []
    a = refdata.add_account(w, name="勞退個人專戶", kind="pension", purpose="勞退自提（不能動用）")
    return a, ["第一次記勞退自提，自動建立帳戶「勞退個人專戶」"]


def _income(w: Writer, rec: IncomeRecord, index: int, source: str, ref: str | None,
            replace: bool = False) -> dict[str, Any]:
    date = tx.parse_date(rec.date)
    time = tx.parse_time(rec.time)
    account = _account(w.conn, rec.account)
    phash = tx.payload_hash(rec.model_dump(by_alias=True))
    try:
        _existing(w, source, ref, index * STEP, phash, replace=replace, replaceable=False)
    except Replay as r:
        return _replayed(w, index, "income", r)
    rec.desc, rec.note = labels.tidy(rec.desc, rec.note, kind="income")
    cat, guessed, reason, warnings = _category(w.conn, rec.category, kind="income", desc=rec.desc, time=time, date=date)
    if cat is None:
        cat = refdata.system_category(w.conn, "income.other")
        guessed, reason = True, "猜不出來，先記「其他收入」"
        warnings.append(f"收入「{rec.desc or rec.amount}」{reason}")
    if rec.deductions and sum(rec.deductions.values()) >= rec.amount:
        raise InvalidInput("扣繳加起來比總額還多", fields={"deductions": "太多"})
    group_id = ("g_" + ids.ulid()) if rec.deductions else None
    t = tx.create(w, kind="income", amount=rec.amount, date=date, time=time, account_id=account["id"],
                  category_id=cat["id"], description=rec.desc, note=rec.note, confidence=rec.confidence,
                  group_id=group_id, source=source, source_ref=ref, item_idx=index * STEP, payload_hash=phash)
    res = _base_result(w, index, "income", t)
    res.update({"category_guessed": guessed, "category_reason": reason, "deductions": []})
    for k, (name, amount) in enumerate(rec.deductions.items(), start=1):
        dcat = refdata.find_category(w.conn, name)
        if dcat is None:
            raise NotFound(f"扣繳項目「{name}」不是已知的類別", fields={f"deductions.{name}": "找不到"},
                           suggestions=refdata.category_suggestions(w.conn, name) or ["勞保", "健保", "所得稅", "勞退自提"])
        common = dict(amount=amount, date=date, time=time, account_id=account["id"], category_id=dcat["id"],
                      description=f"{rec.desc or cat['name']}扣繳：{name}", group_id=group_id, source=source,
                      source_ref=ref, item_idx=index * STEP + k, payload_hash=phash)
        if dcat["kind"] == "transfer":
            pension, notes_ = _pension_account(w)
            warnings.extend(notes_)
            d = tx.create(w, kind="transfer", to_account_id=pension["id"], **common)
        elif dcat["kind"] == "expense":
            d = tx.create(w, kind="expense", **common)
        else:
            raise InvalidInput(f"扣繳項目「{name}」是收入類，不能當扣繳")
        res["deductions"].append({"id": d["id"], "category": d["category"], "amount": d["amount"]})
    res["net"] = rec.amount - sum(rec.deductions.values())
    res["_warnings"] = warnings
    _flag_duplicate(w, res)
    return res


RECEIVABLE = refdata.RECEIVABLE


def _account_or_receivable(w: Writer, name: str, purpose: str | None) -> dict[str, Any]:
    """借出（代墊）與收回都要有一個「應收」帳戶；第一次用「代墊款」就自動建，不用先去設定加。
    只有這個名字會自動建（打錯別的字還是會被擋下來問）。"""
    try:
        return refdata.resolve_account(w.conn, name)
    except NotFound:
        if purpose in ("lend", "collect") and (name or "").strip() == RECEIVABLE:
            return refdata.receivable_account(w)
        raise


def _transfer(w: Writer, rec: TransferRecord, index: int, source: str, ref: str | None,
              replace: bool = False) -> dict[str, Any]:
    date = tx.parse_date(rec.date)
    time = tx.parse_time(rec.time)
    src = _account_or_receivable(w, rec.from_, rec.purpose)
    dst = _account_or_receivable(w, rec.to, rec.purpose)
    phash = tx.payload_hash(rec.model_dump(by_alias=True))
    try:
        target = _existing(w, source, ref, index * STEP, phash, replace=replace, replaceable=True)
    except Replay as r:
        return _replayed(w, index, "transfer", r)
    purpose = rec.purpose
    if purpose is None:
        if dst["kind"] == "credit_card":
            purpose = "card_payment"
        elif dst["kind"] == "receivable":
            purpose = "lend"
        elif src["kind"] == "receivable":
            purpose = "collect"
        elif dst["kind"] == "pension":
            purpose = "pension"
    warnings: list[str] = []
    cat = None
    if rec.category:
        cat = refdata.find_category(w.conn, rec.category)
        if cat is None or cat["kind"] != "transfer":
            warnings.append(f"「{rec.category}」不是轉帳類別，不套類別")
            cat = None
    if cat is None and purpose in ("card_payment", "lend", "collect"):
        cat = refdata.system_category(w.conn, {"card_payment": "card_payment", "lend": "lend", "collect": "collect"}[purpose])
    if cat is None and not purpose:
        cat, _ = classify.guess(w.conn, rec.desc, kind="transfer")
    for_period, for_note = _for_period(w, rec.for_period, rec.desc, date, living=False)
    if for_note:
        warnings.append(for_note)
    fields = dict(amount=rec.amount, date=date, time=time, account_id=src["id"], to_account_id=dst["id"],
                  purpose=purpose, category_id=cat["id"] if cat else None, counterparty=rec.counterparty,
                  description=rec.desc, note=rec.note, confidence=rec.confidence, for_period=for_period)
    if target:
        t = tx.update(w, target, kind="transfer", **fields)
        w.conn.execute("UPDATE transactions SET payload_hash = ? WHERE id = ?", (phash, target))
    else:
        t = tx.create(w, kind="transfer", **fields, source=source, source_ref=ref, item_idx=index * STEP,
                      payload_hash=phash)
    res = _base_result(w, index, "transfer", t)
    _allowance_note(w, t, warnings)
    res.update({"to_account": t["to_account"], "purpose": t["purpose"], "_warnings": warnings})
    if target:
        res["status"] = "replaced"
    return res


def _day_note(w: Writer, rec: DayNoteRecord, index: int, source: str, ref: str | None,
              replace: bool = False) -> dict[str, Any]:
    r = notes.put(w, rec.date, rec.note, replace=rec.replace)
    status = ("would_create" if w.dry_run else "created") if r["changed"] else "unchanged"
    return {"index": index, "type": "day_note", "status": status, "date": r["date"], "note": r["note"], "_warnings": []}


def _card_statement(w: Writer, rec: CardStatementRecord, index: int, source: str, ref: str | None,
                    replace: bool = False) -> dict[str, Any]:
    r = cards.save_statement(w, card=rec.card, closing_date=rec.closing_date, total=rec.total,
                             lines=[ln.model_dump() for ln in rec.lines], due_date=rec.due_date,
                             period_start=rec.period_start, min_payment=rec.min_payment, source=source, source_ref=ref,
                             lines_hash=tx.payload_hash(rec.model_dump(by_alias=True)))
    st = r["statement"]
    status = {"created": "would_create" if w.dry_run else "created", "updated": "replaced"}.get(r["status"], r["status"])
    warnings = []
    missing = [ln for ln in st["lines"] if ln["status"] == "missing"]
    diff = [ln for ln in st["lines"] if ln["status"] == "diff"]
    fees = [ln for ln in st["lines"] if ln["status"] == "fee"]
    if missing:
        warnings.append(f"{st['card']} 帳單有 {len(missing)} 筆在記帳裡找不到（漏記？）：" +
                        "、".join(f"{ln['date'][5:]} {ln['desc']} {ln['amount']:,}" for ln in missing[:5]))
    if diff:
        warnings.append(f"{len(diff)} 筆金額跟記帳不同：" + "、".join(
            f"{ln['desc']} 帳單 {ln['amount']:,}／記帳 {ln['ledger']['amount']:,}" for ln in diff[:5]))
    if fees:
        warnings.append(f"{len(fees)} 筆手續費／利息還沒記：" + "、".join(f"{ln['desc']} {ln['amount']:,}" for ln in fees))
    if st["gap"]:
        warnings.append(f"帳單總額跟明細加總差 {st['gap']:,} 元（可能有上期餘額或沒列到的項目）")
    return {"index": index, "type": "card_statement", "status": status, "statement_id": st["id"], "card": st["card"],
            "closing_date": st["closing_date"], "due_date": st["due_date"], "total": st["total"],
            "matched": sum(1 for ln in st["lines"] if ln["status"] == "matched"), "problems": st["problems"],
            "gap": st["gap"], "balanced": st["balanced"], "ledger_only": len(st["ledger_only"]), "_warnings": warnings}


def _installment_purchase(w: Writer, rec: InstallmentPurchaseRecord, index: int, source: str, ref: str | None,
                          replace: bool = False) -> dict[str, Any]:
    phash = tx.payload_hash(rec.model_dump(by_alias=True))
    try:
        _existing(w, source, ref, index * STEP, phash, replace=False, replaceable=False)
    except Replay as r:
        return _replayed(w, index, "installment_purchase", r)
    card = cards._card(w.conn, rec.card)
    date = tx.parse_date(rec.date)
    cat, guessed, reason, warnings = _category(w.conn, rec.category, kind="expense", desc=rec.name, time=None, date=date)
    if cat is None:
        cat = refdata.system_category(w.conn, "big_purchase")
    t = tx.create(w, kind="expense", amount=rec.total, date=date, account_id=card["id"], category_id=cat["id"],
                  description=rec.name, note=rec.note or f"分 {rec.periods} 期", financed=True, source=source,
                  source_ref=ref, item_idx=index * STEP, payload_hash=phash)
    d = debts.create(w, name=rec.name, kind="card_installment", periods=rec.periods, principal=rec.total,
                     per_period=rec.per_period, start_date=rec.first_due or date, account=card["name"],
                     nature=rec.nature, purchase_txn_id=t["id"], source=source,
                     source_ref=f"{ref}:{index}" if ref else None)["debt"]
    w.conn.execute("UPDATE transactions SET debt_id = ? WHERE id = ?", (d["id"], t["id"]))
    res = _base_result(w, index, "installment_purchase", t)
    res.update({"debt_id": d["id"], "periods": d["periods"], "per_period": d["per_period"],
                "first": d["schedule"][0]["amount"], "category": cat["name"], "_warnings": warnings})
    return res


def _balance(w: Writer, rec: BalanceRecord, index: int, source: str, ref: str | None,
             replace: bool = False) -> dict[str, Any]:
    r = balances.set_snapshot(w, account=rec.account, balance=rec.balance, as_of=rec.as_of, note=rec.note, source=source)
    warnings = []
    if r["can_adjust"]:
        warnings.append(f"{r['account']} 核對 {r['balance']:,}，但照記帳推算是 {r['derived']:,}，差 {r['diff']:,}"
                        f"（可能有沒記到的{'支出' if r['diff'] < 0 else '收入'}）；要補一筆就打 /money_balance adjust={r['snapshot_id']}")
    elif r["first"]:
        warnings.append(f"{r['account']} 第一次核對，{r['balance']:,} 當作起始餘額")
    return {"index": index, "type": "balance", "status": "would_create" if w.dry_run else "created", **r,
            "_warnings": warnings}


def _bill(w: Writer, rec: BillRecord, index: int, source: str, ref: str | None, replace: bool = False) -> dict[str, Any]:
    r = bills.save(w, bill=rec.bill, amount=rec.amount, period_end=rec.period_end, period_start=rec.period_start,
                   usage=rec.usage, unit=rec.unit, due_date=rec.due_date, paid=rec.paid, paid_date=rec.paid_date,
                   account=rec.account, note=rec.note, source=source, source_ref=ref)
    b = r["bill"]
    status = "replayed" if r["status"] == "replayed" else ("would_create" if w.dry_run else "created")
    warnings = []
    if not b["paid"] and b["due_date"]:
        warnings.append(f"{b['label']} {b['amount']:,} 還沒繳，{b['due_date'][5:]} 到期（繳了打 /money_bill pay id={b['id']}）")
    t = bills.trend(w.conn, b["bill_type"])
    if t["insight"] and b["bill_type"] == "electricity":
        warnings.append(t["insight"])
    return {"index": index, "type": "bill", "status": status, "bill_id": b["id"], "bill": b["label"],
            "amount": b["amount"], "usage": b["usage"], "unit": b["unit"], "paid": b["paid"], "due_date": b["due_date"],
            "summer": b["summer"], "id": b["txn_id"], "_warnings": warnings}


def _invest_pnl(w: Writer, rec: InvestPnlRecord, index: int, source: str, ref: str | None,
                replace: bool = False) -> dict[str, Any]:
    r = invest.add_month(w, month=rec.month, pnl=rec.pnl, account=rec.account, market_value=rec.market_value,
                         net_deposit=rec.net_deposit, note=rec.note, source=source)
    m = r["month"]
    s = invest.summary(w.conn)
    status = {"created": "would_create" if w.dry_run else "created", "updated": "replaced"}.get(r["status"], r["status"])
    warn = []
    if s["target_monthly"]:
        warn.append(f"{m['month']} 損益 {m['pnl']:,}（每月目標 {s['target_monthly']:,}），累計 {s['total']:,}"
                    + (f"，報酬率 {s['return'] * 100:.1f}%" if s["return"] is not None else ""))
    return {"index": index, "type": "invest_pnl", "status": status, "month": m["month"], "pnl": m["pnl"],
            "account": m["account"], "market_value": m["market_value"], "_warnings": warn}


def _holdings(w: Writer, rec: HoldingsRecord, index: int, source: str, ref: str | None,
              replace: bool = False) -> dict[str, Any]:
    r = invest.set_holdings(w, items=[i.model_dump() for i in rec.items], as_of=rec.as_of, account=rec.account, source=source)
    return {"index": index, "type": "holdings", "status": "would_create" if w.dry_run else "created", "account": r["account"],
            "as_of": r["as_of"], "count": r["count"], "total": r["holdings"]["total"], "_warnings": []}


def _trade(w: Writer, rec: TradeRecord, index: int, source: str, ref: str | None,
           replace: bool = False) -> dict[str, Any]:
    """對帳單的一列（買／賣／股利）。ref 有給就是冪等鍵：同一張對帳單重送不會變兩筆。"""
    from . import trades
    r = trades.add(w, date=rec.date, action=rec.action, symbol=rec.symbol or "", name=rec.name or "",
                   shares=rec.shares or 0, price=rec.price, gross=rec.gross, fee=rec.fee or 0, tax=rec.tax or 0,
                   net=rec.net, account=rec.account, note=rec.note, source=source,
                   source_ref=f"{ref}#{index}" if ref else None, book=rec.book, time=rec.time,
                   day_trade=rec.day_trade, stop_price=rec.stop_price)
    status = r["status"] if r["status"] != "created" else ("would_create" if w.dry_run else "created")
    label = f"{r['symbol']} {r['name']}".strip() or r["action_name"]
    return {"index": index, "type": "trade", "status": status, "id": r["id"], "date": r["date"],
            "action": r["action"], "action_name": r["action_name"], "symbol": r["symbol"], "name": r["name"],
            "shares": r["shares"], "net": r["net"], "account": r["account"], "book": r.get("book"),
            "violation": r.get("violation"),
            "_warnings": ([f"{label} 這一列記過了"] if status == "replayed" else [])
                         + ([f"{label}：違規——{r['violation']}"] if r.get("violation") and status != "replayed" else [])}


def _iou(w: Writer, rec: IouRecord, index: int, source: str, ref: str | None,
         replace: bool = False) -> dict[str, Any]:
    """代墊與欠款一個入口：方向與對象一定要寫——聊天助理聽不出方向時要先問，不能猜。

    lend＝帳戶 → 代墊款（借出轉帳）；collect＝代墊款 → 帳戶；owe＝記在「欠別人」帳戶的花錢（算你的花費，錢包要等還的那天才扣）；
    repay＝帳戶 → 欠別人（purpose repay，錢包這天才少）。"""
    direction = (rec.direction or "").strip().lower()
    if direction not in IOU_DIRECTIONS:
        raise InvalidInput("方向沒說清楚：是你幫別人付、還是別人幫你付？" if not direction else f"方向「{rec.direction}」看不懂",
                           hint="lend＝你幫別人先付、owe＝別人幫你先付（你欠他）、collect＝對方還你、repay＝你還對方；"
                                "聽不出來就先問使用者一句，不要猜",
                           fields={"direction": "缺少" if not direction else "不對"})
    who = (rec.who or "").strip()
    if not who:
        raise InvalidInput("要寫是誰（who）", hint="例：小明；之後還錢時名字要寫得一樣才對得上", fields={"who": "缺少"})
    if rec.amount <= 0:
        raise InvalidInput("金額要是正數", fields={"amount": "不是正數"})
    if direction == "owe":
        payable = refdata.payable_account(w)
        e = ExpenseRecord(type="expense", amount=rec.amount, date=rec.date, time=rec.time, desc=rec.desc,
                          category=rec.category, account=payable["name"], note=rec.note)
        res = _expense(w, e, index, source, ref, replace)
        if res["status"] != "replayed":
            tx.update(w, res["id"], counterparty=who)
    else:
        mine = rec.account or settings.get(w.conn, "default_account") or "現金"
        if direction in ("lend", "collect"):
            other = refdata.receivable_account(w)["name"]
        else:
            other = refdata.payable_account(w)["name"]
        src, dst = (other, mine) if direction == "collect" else (mine, other)
        t = TransferRecord.model_validate({"type": "transfer", "amount": rec.amount, "date": rec.date, "time": rec.time,
                                           "from": src, "to": dst, "purpose": direction, "counterparty": who,
                                           "desc": rec.desc, "note": rec.note})
        res = _transfer(w, t, index, source, ref, replace)
        if direction == "repay" and res["status"] != "replayed":
            if not any(p["counterparty"] == who and p["owed"] for p in debts.people(w.conn)):
                res["_warnings"].append(f"沒有記過欠{who}的錢；名字要跟當初寫的一樣才對得上（/money_lend 看名字）")
    res.update({"direction": direction, "who": who})
    return res


HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "expense": _expense, "income": _income, "transfer": _transfer, "day_note": _day_note,
    "card_statement": _card_statement, "installment_purchase": _installment_purchase, "balance": _balance, "bill": _bill,
    "invest_pnl": _invest_pnl, "holdings": _holdings, "trade": _trade, "iou": _iou,
}


def _prefixed(err: LedgerError, index: int) -> LedgerError:
    err.fields = {f"records[{index}].{k}" if not k.startswith("records[") else k: v for k, v in err.fields.items()} \
        or {f"records[{index}]": err.message}
    err.message = f"第 {index + 1} 筆：{err.message}（整批都沒有寫入）"
    return err


def run(conn: db.Connection, text_or_obj: str | dict | list, *, actor: str = "cli", source: str | None = None,
        source_ref: str | None = None, dry_run: bool = False, command: str = "ingest",
        replace: bool = False) -> dict[str, Any]:
    payload = parse(text_or_obj)
    src = payload.source or source or (actor.split(":", 1)[0] if ":" in actor else actor)
    ref = payload.source_ref or source_ref
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    with db.write(conn, actor=actor, source=src, command=command, dry_run=dry_run) as w:
        for index, rec in enumerate(payload.records):
            try:
                handler = HANDLERS[rec.type]
                res = handler(w, rec, index, src, ref, replace)
            except LedgerError as e:
                raise _prefixed(e, index) from None
            warnings.extend(f"第 {index + 1} 筆：{m}" if len(payload.records) > 1 else m for m in res.pop("_warnings"))
            results.append(res)
        batch_id = w.batch_id
        changed = w.changes > 0
    return {"batch_id": batch_id if (changed and not dry_run) else None, "dry_run": dry_run, "source_ref": ref,
            "results": results, "warnings": warnings, "summary": summarize(results)}


NEW = ("created", "would_create", "replaced")


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """一批的總數：大量截圖先 dry-run 時，AI 拿這個跟使用者說「新的幾筆、可能重複幾筆、已記過幾筆、合計多少」。"""
    money = [r for r in results if r["type"] in ("expense", "income", "transfer")]
    fresh = [r for r in money if r.get("status") in NEW or r.get("status") == "possible_duplicate"]
    return {"records": len(results),
            "new": sum(1 for r in money if r.get("status") in NEW),
            "possible_duplicate": sum(1 for r in money if r.get("status") == "possible_duplicate"),
            "replayed": sum(1 for r in money if r.get("status") == "replayed"),
            "guessed_category": sum(1 for r in fresh if r.get("category_guessed")),
            "uncategorized": sum(1 for r in fresh if r["type"] == "expense" and not r.get("category")),
            "expense": sum(r["amount"] for r in fresh if r["type"] == "expense"),
            "income": sum(r["amount"] for r in fresh if r["type"] == "income"),
            "transfer": sum(r["amount"] for r in fresh if r["type"] == "transfer")}
