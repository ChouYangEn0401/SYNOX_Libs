"""待解決：帳裡彼此對不上的地方。**只標出來，不自動解決、不改任何資料。**

為什麼不自動解決：怎麼處理衝突還沒有定論，先把狀態標出來，讓使用者知道有東西要去處理。

`scan(conn)` 回一份清單，每件 `{key, level, title, why, where, ids}`：
- level：bad（紅，會出事）／warn（黃，要去看）；
- title：一句話（是什麼）；why：為什麼算對不上（白話）；
- where：去哪裡處理 `{tab, label, anchor, hint}`（tab＝網站的分頁；anchor＝那頁上的區塊；hint＝網站沒有的要怎麼做）；
- ids：牽涉的交易 id（或帳單、帳戶），給之後要做「一鍵處理」的人用。

偵測什麼、怎麼判斷：
(a) dup：可能記重複、還沒處理的組（dedup 的待確認）。一組一件。
(b) twice：最近 RECENT_DAYS 天內，**同一天、同金額、同類別的收入** 2 筆以上（例：10/6 生活帳戶兩筆 49,280 薪水）。
    已經在 (a) 裡等你確認、或你按過「不是，兩筆都留」的那幾筆不再算（不重複講、也尊重你的決定）。
(c) salary_account：薪水進了「不是薪水入帳」的帳戶。只在**能明確判斷**時才講：
    啟用中的帳戶裡，用途（purpose）寫了「薪水入帳／薪資入帳」的**剛好一個**；最近 RECENT_DAYS 天內，
    類別是「薪資」的收入記在別的帳戶。沒有或有兩個以上這種帳戶＝不知道哪個才對，不講。
(d) card：信用卡對不上——有存帳單而且有沒對上的列（problems＞0）或帳單總額跟列加起來不一樣（gap≠0）＝黃；
    過了繳款日還有沒繳的（cardcash：bill.left＞0 且 days_to_due＜0）＝紅。
(e) balance：帳戶核對過兩次以上，最近一次核對的餘額 ≠ 上一次核對＋中間記的帳（中間有沒記到的錢）。
(f) unsorted：還沒分類的支出 UNSORTED_MANY 筆以上（少少幾筆在健康檢查的「等你確認」講就好）。
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Any

from .. import clock
from . import balances, cardcash, cards as cardstmt, dedup, inbox, refdata

RECENT_DAYS = 90
UNSORTED_MANY = 5
SALARY_CATEGORY = "salary"                                   # 薪資類別的系統代號
SALARY_WORDS = ("薪水入帳", "薪資入帳")
ORDER = {"bad": 0, "warn": 1}


def _item(key: str, level: str, title: str, why: str, where: dict[str, Any], ids: list[Any]) -> dict[str, Any]:
    return {"key": key, "level": level, "title": title, "why": why, "where": where, "ids": ids}


def _where(tab: str, label: str, *, anchor: str | None = None, hint: str | None = None) -> dict[str, Any]:
    return {"tab": tab, "label": label, "anchor": anchor, "hint": hint}


def _md(iso: str) -> str:
    return f"{int(iso[5:7])}/{int(iso[8:10])}"


def _since() -> str:
    return (clock.today() - timedelta(days=RECENT_DAYS)).isoformat()


def _duplicates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for p in dedup.pending(conn):
        a, b = p["a"], p["b"]
        out.append(_item(f"dup:{p['pair_id']}", "warn",
                         f"{_md(a['date'])} 可能記重複了：{a.get('desc') or a.get('category') or '一筆'} {a['amount']:,}",
                         "同一天、金額一樣、內容很像的兩筆，可能同一筆記了兩次。",
                         _where("home", "看重複的", anchor=".dup-card"), [a["id"], b["id"]]))
    return out


def _decided_pairs(conn: sqlite3.Connection) -> set[frozenset[str]]:
    """已經在待確認（pending）或你按過「兩筆都留」（kept_both）的組：不再當成同一天收兩次。"""
    return {frozenset((r[0], r[1])) for r in conn.execute(
        "SELECT a_id, b_id FROM dedup_pairs WHERE status IN ('pending', 'kept_both')")}


def _income_twice(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT t.date, t.amount, t.category_id, c.name AS category, GROUP_CONCAT(t.id) AS ids, "
        "GROUP_CONCAT(DISTINCT a.name) AS accounts, COUNT(*) AS n "
        "FROM transactions t LEFT JOIN categories c ON c.id = t.category_id JOIN accounts a ON a.id = t.account_id "
        "WHERE t.status = 'active' AND t.kind = 'income' AND t.date >= ? "
        "GROUP BY t.date, t.amount, t.category_id HAVING COUNT(*) >= 2 ORDER BY t.date", (_since(),)).fetchall()
    if not rows:
        return []
    decided = _decided_pairs(conn)
    out = []
    for r in rows:
        ids = r["ids"].split(",")
        covered = {i for pair in decided if pair <= set(ids) for i in pair}
        if len(covered) == len(ids):
            continue
        what = r["category"] or "收入"
        out.append(_item(f"twice:{r['date']}:{r['amount']}:{r['category_id']}", "warn",
                         f"{_md(r['date'])} {r['accounts']}有 {r['n']} 筆 {r['amount']:,} 的{what}",
                         f"同一天、同金額、同類別的收入記了 {r['n']} 次；如果其實只進來一次，錢包會多算 {r['amount'] * (r['n'] - 1):,}。",
                         _where("book", "去明細看", hint="真的是兩筆就不用管；記重了刪掉一筆"), ids))
    return out


def _salary_account(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    accounts = [a for a in refdata.accounts(conn) if a["kind"] != "credit_card"]
    home = [a for a in accounts if any(w in (a.get("purpose") or "") for w in SALARY_WORDS)]
    if len(home) != 1:
        return []                                             # 沒寫或寫了好幾個：不知道哪個才對，不猜
    target = home[0]
    rows = conn.execute(
        "SELECT t.id, t.date, t.amount, a.name AS account FROM transactions t JOIN categories c ON c.id = t.category_id "
        "JOIN accounts a ON a.id = t.account_id WHERE t.status = 'active' AND t.kind = 'income' AND c.system_key = ? "
        "AND t.account_id <> ? AND t.date >= ? ORDER BY t.date", (SALARY_CATEGORY, target["id"], _since())).fetchall()
    return [_item(f"salary_account:{r['id']}", "warn",
                  f"{_md(r['date'])} 的薪水 {r['amount']:,} 記在「{r['account']}」",
                  f"你設定「{target['name']}」是薪水入帳的帳戶，這筆卻記在「{r['account']}」：可能記錯帳戶，兩個帳戶的餘額都會不準。",
                  _where("book", "去明細改帳戶", hint=f"真的是領現金就不用管；記錯了把帳戶改成「{target['name']}」"), [r["id"]])
            for r in rows]


def _cards(conn: sqlite3.Connection, cash_cards: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for c in (cash_cards if cash_cards is not None else cardcash.all_cards(conn)):
        b = c.get("bill") or {}
        if c.get("ready") and b.get("left") and (b.get("days_to_due") or 0) < 0:
            out.append(_item(f"card_overdue:{c['account_id']}", "bad",
                             f"{c['account']}的卡費過了繳款日還有 {b['left']:,} 沒繳",
                             f"繳款日是 {_md(b['due'])}，已經過了 {-b['days_to_due']} 天；如果其實繳了，是繳費那筆還沒記。",
                             _where("home", "看信用卡", anchor=".cc", hint="繳了就記一筆繳卡費；還沒繳要趕快繳"),
                             [c["account_id"]]))
    for c in cardstmt.all_cards(conn):
        st = c.get("last_statement")
        if not st or (not st["problems"] and not st["gap"]):
            continue
        bits = [f"{st['problems']} 筆沒對上" if st["problems"] else "",
                f"帳單總額跟明細加起來差 {abs(st['gap']):,}" if st["gap"] else ""]
        out.append(_item(f"card_statement:{st['id']}", "warn",
                         f"{c['account']} {_md(st['closing_date'])} 的帳單有 " + "、".join(x for x in bits if x),
                         "帳單上的和你記的帳不一樣：可能有一筆沒記、記錯金額或刷錯卡。",
                         _where("home", "看信用卡", anchor=".cc", hint="在聊天裡跟助理說「看這期帳單」，一筆一筆對"),
                         [st["id"]]))
    return out


def _balances(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    today = clock.today().isoformat()
    out = []
    for a in refdata.accounts(conn):
        snaps = conn.execute("SELECT as_of, balance FROM balance_snapshots WHERE account_id = ? AND as_of <= ? "
                             "ORDER BY as_of DESC LIMIT 2", (a["id"], today)).fetchall()
        if len(snaps) < 2:
            continue                                          # 只核對過一次＝那是起始餘額，沒得比
        last, prev = snaps[0], snaps[1]
        moved = conn.execute(balances._effect_sql(), {"a": a["id"], "since": prev["as_of"], "until": last["as_of"]}).fetchone()[0]
        diff = last["balance"] - (prev["balance"] + moved)
        if diff:
            out.append(_item(f"balance:{a['id']}", "warn",
                             f"{a['name']}核對的餘額跟記的帳差 {abs(diff):,}",
                             f"{_md(prev['as_of'])} 到 {_md(last['as_of'])} 之間，帳戶裡{'少' if diff < 0 else '多'}了 {abs(diff):,}，"
                             "但帳裡沒有記到這筆。",
                             _where("piggy", "看帳戶", hint="想起來是什麼就補記；想不起來在聊天裡跟助理說「補差額」"),
                             [a["id"]]))
    return out


def _unsorted(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    n = inbox.counts(conn)["uncategorized"]
    if n < UNSORTED_MANY:
        return []
    ids = [r[0] for r in conn.execute("SELECT id FROM transactions WHERE status = 'active' AND kind = 'expense' "
                                      "AND category_id IS NULL ORDER BY date DESC LIMIT 50")]
    return [_item("unsorted", "warn", f"有 {n} 筆花費還沒分類",
                  "沒分類的錢不知道算哪一格，生活費、日曆的顏色、每天可以花多少都會不準。",
                  _where("book", "去明細分類", hint="總覽日曆下面有「自動分類」，分不出來的再一筆一筆選"), ids)]


def scan(conn: sqlite3.Connection, *, cards: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """全部的待解決，紅的在前。cards＝cardcash.all_cards 已經算好的（總覽傳進來，不用再算一次）。只讀不寫。"""
    out = (_duplicates(conn) + _income_twice(conn) + _salary_account(conn) + _cards(conn, cards)
           + _balances(conn) + _unsorted(conn))
    return sorted(out, key=lambda x: ORDER[x["level"]])


def payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    """總覽、API、CLI 共用的樣子：幾件、一句話、清單。"""
    return {"count": len(items), "bad": sum(1 for i in items if i["level"] == "bad"), "headline": headline(items),
            "items": items}


def headline(items: list[dict[str, Any]]) -> str:
    """一句話：「沒有待解決的」或「待解決 N 件」。"""
    if not items:
        return "沒有待解決的"
    bad = sum(1 for i in items if i["level"] == "bad")
    return f"待解決 {len(items)} 件" + (f"（{bad} 件要馬上處理）" if bad else "")
