"""健康檢查：電腦自己看的幾件事，紅黃綠燈加一句話。

順序就是嚴重程度：紅（bad）→ 黃（warn）→ 綠（good）。每一條都是使用者一眼看得懂的一句話，
細節在別的地方（明細、檢討）。AI 的月報是另一件事（reviews）；這裡是隨時都在算的規則：
- 生活費：超支＝紅；花得比時間快（超過照天數該花的 10%）＝黃；不然綠。沒設預算＝黃。
- 分配：薪水還有沒分出去的＝**綠**（那是緩衝，花超的時候用得到）；分配加起來比收入多＝黃（可以這樣設，但要提醒）。
- 固定住的用途（buckets.locked）這期還沒做到：最後一週或期已結束＝紅，不然黃。
- 實際花超過計畫：捐贈、固定支出比計畫多＝黃；生活費的大類超過**共用上限**＝黃（一句列出全部）。
  細項的單項上限是**軟上限**（心裡的合理用量），**不亮燈**——超過一點是常態，交給月檢討時 AI 看
  「有沒有誇張到要講」（不必一超過就提醒，太誇張的再由 AI 在檢討時點出來）。
- 錢包：負的＝黃（薪水還沒進來）；保留的錢比錢包多＝紅（要留的錢不夠了）。
- 預備金：低於底線＝紅。
- 分期：每月要還的占收入 30% 以上＝紅、15% 以上＝黃。
- 卡費（services/cardcash.py）：過了繳款日、或 7 天內要繳而扣款帳戶不夠＝紅；不夠留＝黃；5 天內要繳＝黃。
  有刷過的卡還沒設結帳日／繳款日＝黃（算不出要留多少）。
- 帳單：7 天內到期＝黃。固定的錢到期了還沒記＝黃。待確認（可能重複、待分類）＝黃。
- 跟「待解決」（services/conflicts.py）講同一件事的那幾條標 `also: "conflicts"`，網站只在待解決講一次；
  CLI 與推送照舊全部列。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import allowance, balances, bills, cardcash, cards, conflicts, debts, inbox, plans, recurring, stats

SPENDING = ("giving", "fixed")                               # 花出去的用途：超過計畫要提醒（生活費另外看）
ORDER = {"bad": 0, "warn": 1, "good": 2}


def _item(level: str, text: str, key: str, tip: str = "") -> dict[str, str]:
    return {"level": level, "text": text, "key": key, "tip": tip}


def checks(conn: sqlite3.Connection, period_key: str | None = None, *, wallet_summary: dict | None = None) -> list[dict[str, str]]:
    from . import wallet
    s = stats.period_summary(conn, period_key)
    plan = plans.get(conn, s["period"]["key"])
    lv = s["living"]
    out: list[dict[str, str]] = []

    if not lv["budget"]:
        out.append(_item("warn", "還沒設生活費預算", "living"))
    elif lv["spent"] > lv["budget"]:
        out.append(_item("bad", f"生活費超支 {lv['spent'] - lv['budget']:,}", "living"))
    elif s["period"]["open"] and lv["pace"] and lv["daily_spent"] > lv["pace"] * 1.1:       # 長期用的不算在「每天」
        out.append(_item("warn", f"生活費花得比時間快（多 {lv['daily_spent'] - lv['pace']:,}）", "living"))
    else:
        out.append(_item("good", "生活費正常", "living"))

    income = s["allocation"]["income"] or plan["income_expected"] or 0
    line = allowance.plan_line(conn, plan, income)            # 沒分完＝緩衝（綠）；分超過＝黃（可以，但要講清楚哪裡多）
    if line:
        out.append(line)

    out += _locked_and_over(s, plan, conn)
    have = {i["key"] for i in out}                            # 額度提醒（services/allowance）：同一份清單，不重複講
    out += [a for a in allowance.check(conn, s["period"]["key"])
            if a["level"] != "info" and a["key"] not in have and a["key"] not in ("plan", "living:over", "living:pace")
            and not a["key"].startswith("over:")]

    w = wallet_summary or wallet.summary(conn, s["period"]["key"])
    if w["free"] < 0 <= w["wallet"]:
        out.append(_item("bad", f"要留的錢不夠 {-w['free']:,}", "wallet"))
    elif w["wallet"] < 0:
        out.append(_item("warn", f"錢包是負的 {w['wallet']:,}（薪水還沒進來？）", "wallet"))

    for r in balances.reserves(conn):
        if r["level"] == "crit":
            out.append(_item("bad", f"{r['name']}低於底線，差 {r['gap']:,}", "reserve"))

    active = debts.all_debts(conn)
    monthly = sum(d["per_period"] for d in active)
    if monthly and income:
        share = monthly / income
        level = "bad" if share >= 0.3 else ("warn" if share >= 0.15 else "good")
        out.append(_item(level, f"每月還分期 {monthly:,}（收入的 {round(share * 100)}%）", "debt"))

    for c in cardcash.all_cards(conn):                         # 卡費要準備多少、快到期、銀行夠不夠
        if c["level"] != "good":
            it = _item(c["level"], f"{c['account']}：{c['why']}" if c.get("why") else c["text"], "card")
            if c.get("ready") and (c["bill"]["left"] and c["bill"]["days_to_due"] < 0):
                it["also"] = "conflicts"                       # 過了繳款日：「待解決」也會列，網站只講一次
            out.append(it)
    for c in cards.all_cards(conn):                            # 傳過帳單、有幾列沒對上
        st = c.get("last_statement")
        if st and st["problems"]:
            out.append({**_item("warn", f"{c['account']} 帳單有 {st['problems']} 筆沒對上", "card"), "also": "conflicts"})
    for b in bills.alerts(conn):
        out.append(_item("warn", b["alert"], "bill"))
    if s["period"]["open"]:
        due = [r["name"] for r in recurring.status(conn, s["period"]["key"]) if r["level"] == "warn"]
        if due:
            out.append(_item("warn", "固定的還沒記：" + "、".join(due), "recurring"))
    counts = inbox.counts(conn)
    todo = counts.get("duplicates", 0) + counts.get("uncategorized", 0)
    if todo:
        it = _item("warn", f"有 {todo} 筆等你確認", "inbox")
        if not 0 < counts.get("uncategorized", 0) < conflicts.UNSORTED_MANY:
            it["also"] = "conflicts"     # 重複、很多筆沒分類都在「待解決」列了，網站只講一次；少少幾筆沒分類只有這裡講
        out.append(it)
    return sorted(out, key=lambda x: ORDER[x["level"]])


def _locked_and_over(s: dict[str, Any], plan: dict[str, Any], conn: sqlite3.Connection) -> list[dict[str, str]]:
    """固定住的用途沒做到（還有一週以內或已經結束＝紅，不然黃）；花錢的用途、生活費各大類實際花超過計畫＝黃。
    存錢、投資做得比計畫多不算超過。"""
    out: list[dict[str, str]] = []
    actual = s["allocation"]["actual"]
    days_left = s["living"]["days_left"]
    closing = not s["period"]["open"] or days_left <= 7
    skipped = plans.skips(conn, s["period"]["key"])
    for b in plans.buckets(conn):
        p, a = int(plan["buckets"].get(b["id"]) or 0), int(actual.get(b["id"]) or 0)
        if b["id"] in skipped or plans.close_enough(conn, b, p, a):      # 這期跳過的、會浮動差不多做到的：不唸
            continue                                          # 會浮動的格子差不多做到了：不唸
        if b["locked"] and p and a < p:
            out.append(_item("bad" if closing else "warn", f"{b['name']}還差 {p - a:,}（固定住的，一定要做到）", f"locked:{b['id']}"))
        elif b["id"] in SPENDING and p and a > p:
            out.append(_item("warn", f"{b['name']}比計畫多 {a - p:,}", f"over:{b['id']}"))
    over = [f"{g['name']} {g['spent'] - g['budget']:,}" for g in s["groups"] if g["budget"] and g["spent"] > g["budget"]]
    if over:
        out.append(_item("warn", "超過共用上限：" + "、".join(over), "over:groups"))
    return out


def headline(items: list[dict[str, Any]]) -> dict[str, Any]:
    """整體燈號：有紅＝紅、有黃＝黃、都綠＝綠。"""
    levels = {i["level"] for i in items}
    level = "bad" if "bad" in levels else ("warn" if "warn" in levels else "good")
    return {"level": level, "bad": sum(1 for i in items if i["level"] == "bad"),
            "warn": sum(1 for i in items if i["level"] == "warn")}
