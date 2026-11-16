"""卡費要準備多少錢：每張信用卡一行——這期帳單多少、幾號繳；下期已經刷多少、幾號結帳；銀行要留多少。

為什麼要這個：錢包在**刷卡那一刻**就把錢算花掉了（跟 YNAB 的做法一樣，刷卡＝錢已經用掉、只是先放著等繳），
所以「還能花多少」不會因為卡費晚一個月扣而看錯。但銀行帳戶裡的現金是**繳費那天**才真的少，
結帳日、繳款日跟發薪日又錯開，就會搞不清楚「現在銀行裡的錢有多少是卡費的」。這裡只回答這件事。

- 一期帳單＝上上次結帳日隔天到上次結帳日之間刷的（結帳日當天還算這一期，隔天才算結帳）。
  繳款日＝結帳日之後第一個「繳款日那一號」，**那天是週六日就順延到下一個上班日**（銀行的慣例；
  國定假日沒有表，所以只處理週末——帳單真的寫幾號，以傳進來的帳單為準）。
- 這期帳單：有存帳單（card_statements）就用帳單的總額，沒有就用那段時間記在這張卡上的花費＋分期到期的那一期
  加起來估（標「估的」，傳帳單給助理就準了）。結帳之後轉進這張卡的錢都算繳了這期。
- 下期已刷：上次結帳日隔天到今天記在這張卡上的花費＋這段時間到期的分期。
- 銀行要留＝這期還沒繳的＋下期已刷。銀行是這張卡的扣款帳戶（沒設就猜一個日常用的銀行帳戶，見 _pay_from）；
  那個帳戶**核對過餘額**才比較夠不夠（沒核對過的推算值從 0 開始算，不可靠）。
- 燈號：過了繳款日還沒繳＝紅；7 天內要繳而銀行不夠＝紅；銀行不夠＝黃；5 天內要繳還沒繳＝黃；其他綠。
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date, timedelta
from typing import Any

from .. import clock
from . import balances, refdata, settings, transactions as tx

SOON = 5
TIGHT = 7


def _clamp(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    n = year * 12 + (month - 1) + delta
    return n // 12, n % 12 + 1


def closing_on_or_after(day: int, d: date) -> date:
    this = _clamp(d.year, d.month, day)
    return this if this >= d else _clamp(*_shift(d.year, d.month, 1), day)


def closing_before(day: int, d: date) -> date:
    """d 之前（不含 d）最近的結帳日。"""
    this = _clamp(d.year, d.month, day)
    return this if this < d else _clamp(*_shift(d.year, d.month, -1), day)


def workday(d: date) -> date:
    """遇到六日就順延到下一個上班日（銀行扣款日的慣例；國定假日沒有表，這裡只處理週末）。"""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def due_after(closing: date, due_day: int) -> date:
    """結帳日之後第一個繳款日；那天是六日就順延到下一個上班日（銀行的繳款日通常會避開週末）。"""
    this = _clamp(closing.year, closing.month, due_day)
    raw = this if this > closing else _clamp(*_shift(closing.year, closing.month, 1), due_day)
    return workday(raw)


def _spent(conn: sqlite3.Connection, card_id: int, after: date, until: date) -> int:
    row = conn.execute(f"SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE status = 'active' AND {tx.CARD_CHARGE} "
                       "AND financed = 0 AND account_id = ? AND date > ? AND date <= ?",
                       (card_id, after.isoformat(), until.isoformat())).fetchone()
    dues = conn.execute("SELECT COALESCE(SUM(s.amount), 0) FROM debt_schedule s JOIN debts d ON d.id = s.debt_id "
                        "WHERE d.active = 1 AND d.account_id = ? AND s.status <> 'skipped' AND s.due_date > ? "
                        "AND s.due_date <= ?", (card_id, after.isoformat(), until.isoformat())).fetchone()
    return int(row[0]) + int(dues[0])


def _paid(conn: sqlite3.Connection, card_id: int, after: date, until: date) -> int:
    row = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE status = 'active' AND kind = 'transfer' "
                       "AND to_account_id = ? AND date > ? AND date <= ?",
                       (card_id, after.isoformat(), until.isoformat())).fetchone()
    return int(row[0])


def _pay_from(conn: sqlite3.Connection, card: dict[str, Any]) -> dict[str, Any] | None:
    """扣款帳戶：卡上設的那個；沒設就猜——預設付款帳戶是銀行就用它，不然用第一個不是存錢用的銀行帳戶
    （有底線、目標或備用金群組的是存錢用的），都沒有才用第一個銀行帳戶。預設付款常常是現金，卡費不會從現金扣。"""
    if card.get("pay_from_account_id"):
        row = conn.execute("SELECT id, name FROM accounts WHERE id = ?", (card["pay_from_account_id"],)).fetchone()
        if row:
            return {"id": row["id"], "name": row["name"]}
    banks = [a for a in refdata.accounts(conn) if a["kind"] == "bank"]
    default = settings.get(conn, "default_account")
    pick = (next((a for a in banks if a["name"] == default), None)
            or next((a for a in banks if not (a["floor_amount"] or a["target_amount"] or a["reserve_group"])), None)
            or (banks[0] if banks else None))
    return {"id": pick["id"], "name": pick["name"]} if pick else None


def _spent_total(conn: sqlite3.Connection, account_id: int) -> int:
    """這張卡到現在總共刷了多少（還沒設日子時，至少讓使用者看到數字）。"""
    row = conn.execute(f"SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE status = 'active' "
                       f"AND {tx.CARD_CHARGE} AND account_id = ?", (account_id,)).fetchone()
    return int(row[0] or 0)


def card(conn: sqlite3.Connection, acc: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    today = today or clock.today()
    base = {"account": acc["name"], "account_id": acc["id"]}
    if not acc.get("card_closing_day") or not acc.get("card_due_day"):
        used = conn.execute("SELECT 1 FROM transactions WHERE status = 'active' AND account_id = ? LIMIT 1",
                            (acc["id"],)).fetchone()
        spent = _spent_total(conn, acc["id"])
        text = (f"{acc['name']}：已經刷了 {spent:,}，但還沒設結帳日和繳款日——設了我才算得出銀行要留多少"
                if spent else f"{acc['name']}：到設定填結帳日、繳款日和從哪個帳戶扣，刷卡才算得出要準備多少")
        return {**base, "ready": False, "used": used is not None, "level": "warn" if used else "good",
                "spent": spent, "text": text}
    cday, dday = acc["card_closing_day"], acc["card_due_day"]
    last = closing_before(cday, today)
    before = closing_before(cday, last)
    nxt = closing_on_or_after(cday, today)
    st = conn.execute("SELECT total, due_date, closing_date FROM card_statements WHERE account_id = ? AND closing_date > ? "
                      "AND closing_date <= ? ORDER BY closing_date DESC LIMIT 1",
                      (acc["id"], before.isoformat(), today.isoformat())).fetchone()
    if st:
        bill, source = int(st["total"]), "statement"
        due = date.fromisoformat(st["due_date"]) if st["due_date"] else due_after(last, dday)
    else:
        bill, source, due = _spent(conn, acc["id"], before, last), "estimate", due_after(last, dday)
    paid = _paid(conn, acc["id"], last, today)
    left = max(bill - paid, 0)
    upcoming = _spent(conn, acc["id"], last, today)
    keep = left + upcoming
    bank = _pay_from(conn, acc)
    bank_balance = None
    if bank:
        b = balances.balance_at(conn, bank["id"], today.isoformat())
        if b["verified_on"]:
            bank_balance = b["balance"]
    days = (due - today).days
    short_now = bank_balance is not None and bank_balance < left
    short_all = bank_balance is not None and bank_balance < keep
    if left and days < 0:
        level, why = "bad", f"已經過了繳款日 {-days} 天，還有 {left:,} 沒繳"
    elif left and short_now and days <= TIGHT:
        level, why = "bad", f"{bank['name']}只有 {bank_balance:,}，不夠繳這期 {left:,}"
    elif short_all:
        level, why = "warn", f"{bank['name']}只有 {bank_balance:,}，要留 {keep:,} 給卡費"
    elif left and days <= SOON:
        level, why = "warn", f"卡費 {left:,} " + ("今天" if days == 0 else f"{days} 天後") + "要繳"
    else:
        level, why = "good", ""
    return {**base, "ready": True, "level": level, "why": why,
            "closing_day": cday, "due_day": dday,
            "bill": {"amount": bill, "paid": paid, "left": left, "closing": last.isoformat(), "due": due.isoformat(),
                     "days_to_due": days, "source": source},
            "next": {"amount": upcoming, "closing": nxt.isoformat(), "due": due_after(nxt, dday).isoformat()},
            "keep": keep, "bank": bank["name"] if bank else None, "bank_balance": bank_balance,
            "text": _text(acc["name"], bill, left, due, source, upcoming, nxt, keep, bank, bank_balance)}


def _md(d: date) -> str:
    return f"{d.month}/{d.day}"


def _text(name: str, bill: int, left: int, due: date, source: str, upcoming: int, nxt: date, keep: int,
          bank: dict | None, bank_balance: int | None) -> str:
    est = "（估的）" if source == "estimate" else ""
    this = f"本期帳單 {bill:,}{est} {_md(due)} 繳" + ("（繳好了）" if bill and not left else (f"（還差 {left:,}）" if left < bill else ""))
    if not bill:
        this = "本期沒有要繳的帳單"
    out = f"{name}：{this}；下一期帳單已刷 {upcoming:,}，{_md(nxt)} 結帳；"
    where = bank["name"] if bank else "銀行"
    out += f"{where}要預留信用卡帳款 {keep:,}"
    if bank_balance is not None:
        out += f"（現在有 {bank_balance:,}）"
    return out


def all_cards(conn: sqlite3.Connection, today: date | None = None) -> list[dict[str, Any]]:
    """每張啟用中的信用卡。**沒設結帳日、也還沒用過的卡也要列**：不然還沒設日子的卡會被藏起來，
    使用者會以為沒有信用卡功能。列出來並直接說要去設定什麼。"""
    return [card(conn, a, today) for a in refdata.accounts(conn) if a["kind"] == "credit_card"]
