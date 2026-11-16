"""要主動送給使用者的東西：每週的花費週報、每月的檢討書。這本帳決定**送什麼、什麼時候**，
SYNOX 的排程（/money_review_due）只負責**送到使用者手上**。

- 週報：每週 `notify.weekday`（預設 4＝週五）起，這一週（週一到今天）的生活費、跟上週比、花最多的、最大一筆、
  這個月還可以花多少、卡費要留多少、要注意的事。只有數字，不叫 AI。那天電腦沒開，之後同一週內補送。
- 週檢討書：同一天（預設週五）再附一篇 AI 寫的**這一週**檢討（檢討排程早上做好的，`review.weekly` 預設開）。
  還沒做好就只送數字週報，不等它。
- 檢討書：日曆月底前 `review.month_days_before_end` 天（預設 1）那一期的月檢討（AI 寫的，檢討排程早上做）；
  做好了就送全文摘要。AI 一直沒做好（額度、失敗）超過 2 天，改送數字版月報，不讓它默默消失；
  到期超過 10 天的就不送了（剛開始用的新帳本不會收到上一期的舊檢討）。
- 每一份記在 notifications 表（kind＋key 唯一）：due() 產生或更新還沒送的、回傳清單；送出去的再用
  mark_sent() 標起來（/money_review_due 回覆的那一刻就算送出），沒標的下次再送。月檢討標送出時也標 reviews.sent_at，聊天助理推檢討摘要時就不會再送一次。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from .. import clock
from ..db import Writer, row_dict
from . import allowance, cardcash, health, plans, report, reviews, settings, stats

WEEK, WEEK_REVIEW, MONTH = "week_report", "week_review", "month_review"
FALLBACK_AFTER = 2
STALE_AFTER = 10                                   # 到期超過 10 天的檢討書就不送了（剛開始用、或很久沒開電腦）
LIMIT = 3500
WEEKDAYS = "一二三四五六日"


def _md(d: date) -> str:
    return f"{d.month}/{d.day}"


def week_text(conn: sqlite3.Connection, today: date) -> tuple[str, str]:
    """這一週（週一到今天）的花費週報。回 (key, 文字)。"""
    rep = report.build(conn, "week", include_notes=False)
    places = report.decimals_of(conn)                       # 金額照帳本的小數位數印（只管格式）
    money = report.money
    lv = rep["living"]
    start = rep["start"] if isinstance(rep["start"], date) else date.fromisoformat(str(rep["start"]))
    lines = [f"📊 這週的錢（{_md(start)}–{_md(today)}，週一到週{WEEKDAYS[today.weekday()]}）"]
    budget = round(lv["daily_budget"] * ((today - start).days + 1)) if lv["daily_budget"] else 0
    if budget:
        diff = budget - lv["spent"]
        lines.append(f"生活費花了 {money(lv['spent'], places)}（到今天的額度 {money(budget, places)}，" + (f"還剩 {money(diff, places)}）" if diff >= 0 else f"多了 {money(-diff, places)}）"))
    else:
        lines.append(f"生活費花了 {money(lv['spent'], places)}")
    pv = rep["previous"]
    if pv["spent"]:
        lines.append(f"上週一整週 {money(pv['spent'], places)}")
    groups = [g for g in rep["groups"] if g["spent"]][:3]
    if groups:
        lines.append("花最多的：" + "、".join(f"{g['name']} {money(g['spent'], places)}" for g in groups))
    if rep["top"]:
        t = rep["top"][0]
        lines.append(f"最大一筆：{t['desc']} {money(t['amount'], places)}")
    s = stats.period_summary(conn)["living"]
    if s["budget"]:
        if s["remaining"] >= 0:
            per = f"，每天 {money(s['per_day_left'], places)}、還有 {s['days_left']} 天" if s["per_day_left"] is not None else ""
            lines.append(f"這個月生活費還可以花 {money(s['remaining'], places)}{per}")
        else:
            lines.append(f"這個月生活費已經超過 {money(-s['remaining'], places)}")
    for c in cardcash.all_cards(conn):
        if c["ready"] and c["keep"]:
            lines.append(f"{c['account']}：{c['bank'] or '銀行'}要留 {money(c['keep'], places)} 給卡費（{_md(date.fromisoformat(c['bill']['due']))} 繳 {money(c['bill']['left'], places)}）")
    warn = [i["text"] for i in health.checks(conn) if i["level"] == "bad"][:3]
    if warn:
        lines.append("要注意：" + "；".join(warn))
    quota = [a["text"] for a in allowance.check(conn) if a["level"] in ("bad", "warn") and a["text"] not in warn][:3]
    if quota:
        lines.append("額度提醒：" + "；".join(quota))
    return rep["key"], "\n".join(lines)[:LIMIT]


def _review_text(conn: sqlite3.Connection, rv: dict[str, Any]) -> str:
    text = reviews.digest_text(rv, plans.bucket_names(conn), report.decimals_of(conn))
    if rv["kind"] == "week":
        return ("📝 這週的檢討書\n" + text + "\n（記帳本的「檢討」分頁可以看全文）")[:LIMIT]
    return ("📝 這個月的檢討書\n" + text + "\n（記帳本的「檢討」分頁可以看全文、按「照這個分」改下個月的分配）")[:LIMIT]


def _wanted(conn: sqlite3.Connection, today: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    weekday = int(settings.get(conn, "notify.weekday", 4))
    if today.weekday() >= weekday:
        key, text = week_text(conn, today)
        out.append({"kind": WEEK, "key": key, "title": "週報", "text": text})
        rv = reviews.by_key(conn, "week", key) if settings.get(conn, "review.weekly", True) else None
        if rv and rv["status"] == "done":                      # 早上檢討排程寫好的這一週檢討；沒好就只送數字
            out.append({"kind": WEEK_REVIEW, "key": key, "title": "週檢討書", "text": _review_text(conn, rv),
                        "review_id": rv["id"]})
    days = int(settings.get(conn, "review.month_days_before_end", 1))
    key = reviews.month_key_due(conn, today)
    due = reviews.month_due_date(key, days) if key else None
    if key and (today - due).days <= STALE_AFTER:
        rv = reviews.by_key(conn, "month", key)
        if rv and rv["status"] == "done":
            out.append({"kind": MONTH, "key": key, "title": "檢討書", "text": _review_text(conn, rv), "review_id": rv["id"]})
        elif (today - due).days >= FALLBACK_AFTER:
            rep = report.build(conn, "month", key, include_notes=False)
            if not rep["count"]:
                return out                                    # 那一期一筆都沒有：不送空的報表
            out.append({"kind": MONTH, "key": key, "title": "檢討書（數字版）",
                        "text": ("📝 這個月的數字（AI 這次沒整理到，先給你數字）\n" + report.text(rep, report.decimals_of(conn)))[:LIMIT]})
    return out


def due(w: Writer, today: date | None = None) -> list[dict[str, Any]]:
    """該送、還沒送的：新的寫進 notifications，還沒送的更新成最新的文字。回傳全部還沒送的。"""
    today = today or clock.today()
    for item in _wanted(w.conn, today):
        row = row_dict(w.conn.execute("SELECT * FROM notifications WHERE kind = ? AND key = ?",
                                      (item["kind"], item["key"])).fetchone())
        if row is None:
            w.conn.execute("INSERT INTO notifications (kind, key, title, text, review_id, created_at) VALUES (?,?,?,?,?,?)",
                           (item["kind"], item["key"], item["title"], item["text"], item.get("review_id"), w.now))
            new = row_dict(w.conn.execute("SELECT * FROM notifications WHERE kind = ? AND key = ?",
                                          (item["kind"], item["key"])).fetchone())
            w.audit("create", "notifications", new["id"], None, new)
        elif row["sent_at"] is None and (row["text"], row["title"]) != (item["text"], item["title"]):
            w.conn.execute("UPDATE notifications SET title = ?, text = ?, review_id = ? WHERE id = ?",
                           (item["title"], item["text"], item.get("review_id"), row["id"]))
            w.audit("update", "notifications", row["id"], row,
                    row_dict(w.conn.execute("SELECT * FROM notifications WHERE id = ?", (row["id"],)).fetchone()))
    return pending(w.conn)


def pending(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [row_dict(r) for r in conn.execute("SELECT * FROM notifications WHERE sent_at IS NULL ORDER BY id")]


def recent(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    return [row_dict(r) for r in conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))]


def mark_sent(w: Writer, ids: list[int]) -> list[int]:
    done = []
    for nid in ids:
        row = row_dict(w.conn.execute("SELECT * FROM notifications WHERE id = ?", (nid,)).fetchone())
        if row is None or row["sent_at"]:
            continue
        w.conn.execute("UPDATE notifications SET sent_at = ? WHERE id = ?", (w.now, nid))
        w.audit("update", "notifications", nid, row,
                row_dict(w.conn.execute("SELECT * FROM notifications WHERE id = ?", (nid,)).fetchone()))
        if row["review_id"]:
            reviews.mark_sent(w, [row["review_id"]])
        done.append(nid)
    return done
