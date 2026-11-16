"""額度提醒：錢分得對不對、生活費夠不夠、哪一格該處理了。

額度是最需要 AI 主動提醒的地方，所以這裡只做一件事——把額度相關的話算成
一份清單，**總覽的燈號、聊天助理記完一筆後的提醒、週報、月檢討都吃同一份**，大家講的是同一句話：
- `check()`：隨時能問的清單（CLI `plan check`；聊天助理每次記完帳、或使用者問「額度還好嗎」時看這個）。
- `note_for()`：剛記完一筆，這筆動到的那一格做到了嗎、超過了嗎、生活費走得快不快（一句話，放進寫入結果的 warnings）。

每一條是 {key, level, text, tip}：level＝bad／warn／info；text 是一句白話，tip 是 hover 時的說明
（為什麼、怎麼處理）。沒事的時候是空的——寧可不講，也不要一直講。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from .. import clock
from . import debts, envelopes, periods, plans, settings, stats

SPENDING_SKIP = ("living", "debt", "saving", "invest")                  # 其他都是「花出去」的用途：超過計畫要提醒


def _item(level: str, text: str, key: str, tip: str = "") -> dict[str, str]:
    return {"level": level, "text": text, "key": key, "tip": tip}


def plan_line(conn: sqlite3.Connection, plan: dict[str, Any], income: int) -> dict[str, str] | None:
    """薪水分好了沒。沒分完＝緩衝（綠）；分超過＝黃，而且要講清楚是哪裡多出來的。"""
    planned = sum(int(v or 0) for v in plan["buckets"].values())
    if plan["source"] == "default" and not plan["income_expected"]:
        return _item("warn", "還沒設薪水分配", "plan", "到「規劃」填每個月收入，再分給每個用途。")
    if not income:
        return None
    if planned < income:
        return _item("good", f"還有 {income - planned:,} 沒分，留著當緩衝", "plan",
                     f"收入 {income:,}，分出去 {planned:,}。沒分的錢可以當緩衝，花超的時候用得到。")
    if planned > income:
        parts = "、".join(f"{b} {v:,}" for b, v in _biggest(conn, plan))
        return _item("warn", f"分配比收入多 {planned - income:,}", "plan",
                     f"收入 {income:,}，分出去 {planned:,}（最大的：{parts}）。"
                     "如果有兩格其實是同一筆錢（例：固定支出裡已經含房租，又另外分了一格房租），少分一邊；"
                     "不然月底一定有一格做不到。")
    return _item("good", "薪水分配好了", "plan")


def _biggest(conn: sqlite3.Connection, plan: dict[str, Any], n: int = 3) -> list[tuple[str, int]]:
    names = plans.bucket_names(conn)
    top = sorted(((names.get(b, b), int(v or 0)) for b, v in plan["buckets"].items() if v), key=lambda x: -x[1])
    return top[:n]


def check(conn: sqlite3.Connection, period_key: str | None = None) -> list[dict[str, str]]:
    """這一期額度相關的提醒（嚴重的在前）。"""
    p = periods.get(conn, period_key)
    plan = plans.get(conn, p.key)
    s = stats.period_summary(conn, p.key)
    lv = s["living"]
    income = s["allocation"]["income"] or plan["income_expected"] or 0
    out: list[dict[str, str]] = []

    line = plan_line(conn, plan, income)
    if line and line["level"] != "good":
        out.append(line)

    # 生活費比「你自己設的每日上限」還小：每天可以花的錢其實不夠你平常的花法
    caps_total = sum(int(g["cap_total"] or 0) for g in stats.caps(conn, p.key))
    if lv["budget"] and caps_total and lv["budget"] < caps_total:
        out.append(_item("warn", f"生活費 {lv['budget']:,} 比各項上限加起來（{caps_total:,}）少", "living:low",
                         f"你設的三餐、各細項上限一期加起來 {caps_total:,}，但生活費只分 {lv['budget']:,}"
                         f"（每天 {lv['daily_budget']:,}）。要不要把生活費調高，或把上限調低？"))
    elif plan["assigned_to_groups"] > lv["budget"] > 0:
        out.append(_item("warn", f"生活費的大類分了 {plan['assigned_to_groups']:,}，比生活費 {lv['budget']:,} 多", "living:groups",
                         "各大類上限加起來超過你填的生活費；大類只是參考，但加起來太多就失去提醒的意義。"))

    # 這一期走太快（只在進行中的期講）
    if p.is_open() and lv["budget"]:
        if lv["spent"] > lv["budget"]:
            out.append(_item("bad", f"生活費超支 {lv['spent'] - lv['budget']:,}", "living:over",
                             "這一期生活費已經花超過了，之後的日子就是負的；想想哪些可以不花，或下期預算要不要調。"))
        elif lv["pace"] and lv["daily_spent"] > lv["pace"] * 1.15 and lv["per_day_left"] is not None:
            out.append(_item("warn", f"生活費花得比時間快，剩下每天只有 {lv['per_day_left']:,}", "living:pace",
                             f"照天數到今天該花 {lv['pace']:,}，實際 {lv['daily_spent']:,}（本來每天 {lv['daily_budget']:,}）。"))

    # 每一格：花出去的超過計畫、還沒指定記哪些、到期沒做
    today = clock.today()
    rows = envelopes.rows(conn, plan, s["allocation"]["actual"], p)
    templates = {t["bucket"]: t for t in _templates(conn, p)}
    loose: list[str] = []                                  # 還沒對上任何細項的格子：合成一條，不要一格一條
    for e in rows:
        bid = e["id"]
        if e["plan"] and not e["categories"] and not e["items"] and bid not in ("living", "debt", "saving", "invest"):
            loose.append(e["name"])
        if e["items"] and e["other"] < 0 and e["plan"]:
            out.append(_item("warn", f"{e['name']}的項目加起來 {e['items_planned']:,}，比這一格的 {e['plan']:,} 多 {-e['other']:,}", f"items:{bid}",
                             "項目是這一格的明細，加起來不該比這一格的總額多：把這一格的總額調高，或把某個項目的金額調低。"))
        if e["state"] == "over" and bid not in SPENDING_SKIP:
            if e["rollover"]:                                  # 滾存的格子：跟「能用的」比（這期撥的＋前期留下的），不是跟計畫
                out.append(_item("warn", f"{e['name']}比能用的多 {-e['left']:,}", f"over:{bid}",
                                 f"這期能用 {e['available']:,}（撥 {e['plan']:,}，{'前期留下' if e['carry'] >= 0 else '前期多用'} "
                                 f"{abs(e['carry']):,}），這期記了 {e['used']:,}。多的下期的額度會先補。"))
            else:
                out.append(_item("warn", f"{e['name']}比計畫多 {-e['left']:,}", f"over:{bid}",
                                 f"計畫 {e['plan']:,}，這期記了 {e['used']:,}。如果其中一筆是預繳下個月的，"
                                 "到明細把那一筆的「算哪一期」改成下期，這期就不會被算超過。"))
        t = templates.get(bid)
        if t and e["state"] == "todo" and t["level"] == "warn":
            out.append(_item("warn", f"{e['name']} {t['amount'] or e['left']:,} 到期了還沒記", f"due:{bid}",
                             f"每月 {t['day_of_month']} 號到期。總覽按「今天處理」確認就記好；在聊天裡直接說「{e['name']}」也行。"))
        if e["prepaid_out"]:
            out.append(_item("info", f"{e['name']} 已預繳 {e['prepaid_out']:,}（算給別期）", f"prepaid:{bid}",
                             "這一筆現金這期付了，但算進別期的計畫；那一期到時候這一格會是做到的。"))
    if loose:
        out.append(_item("warn", f"{len(loose)} 格還沒指定記哪些類別算它：{'、'.join(loose)}", "unlinked",
                         "記帳的類別要對上那一格，它才會跟著動（現在記了也不會動）。到「設定→類別」按那一格的「🔗對應」勾選（沒有要的就在那裡新增一個類別）；"
                         "或按總覽那一格的「今天處理」，記的時候會順便對上。"))
    adv = debts.pending_advances(conn)                      # 幫別人先付、還沒收回的：提醒去報帳（不分期別，沒收回就一直在）
    if adv["total"]:
        names = [f"{w['counterparty']} {w['amount']:,}" for w in adv["who"] if w["named"]]
        oldest = adv["oldest"]
        out.append(_item("warn", f"代墊還有 {adv['total']:,} 沒收回（{adv['count']} 筆，最早 {int(oldest[5:7])}/{int(oldest[8:10])}）"
                         + (f"：{'、'.join(names)}" if names else ""), "advance",
                         "幫別人先付的錢（信用卡刷的也算）還在「代墊款」等對方還你。對方還了，總覽按「處理報帳」記一筆收回；"
                         "在聊天裡說「某某還我 500」也行。"))
    owe = adv["owe"]                                        # 別人先幫我付、我還沒還的：欠久了才提醒
    days = int(settings.value(conn, "owe.remind_days") or 30)
    if owe["total"] and (clock.today() - date.fromisoformat(owe["oldest"])).days >= days:
        late_names = {i["counterparty"] for i in owe["items"] if (clock.today() - date.fromisoformat(i["date"])).days >= days}
        late = [w for w in owe["who"] if w["counterparty"] in late_names]
        out.append(_item("warn", f"欠別人 {owe['total']:,} 還沒還（最早 {int(owe['oldest'][5:7])}/{int(owe['oldest'][8:10])}）："
                         + "、".join(f"{w['counterparty']} {w['amount']:,}" for w in late), "owe",
                         f"別人先幫你付的錢，超過 {days} 天還沒還。還了就到總覽「借來借去」按那個人的「已還」；"
                         "在聊天裡說「我還某某 300」也行。幾天提醒在設定 owe.remind_days。"))
    out += piggy_items(conn)
    order = {"bad": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda x: order[x["level"]])


def piggy_items(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """撲滿的監督：捐贈還沒做、到期該收的倉、這期違規、倉或本月停止、操盤倉還沒設可虧損。
    設計：不只記下規矩，也要追蹤做完沒，在畫面上提醒使用者守紀律。"""
    from . import books
    if not conn.execute("SELECT 1 FROM books LIMIT 1").fetchone():
        return []
    out: list[dict[str, str]] = []
    pend = books.pending(conn)
    tithes = [x for x in pend if x["kind"] == "tithe"]
    if tithes:
        total = sum(x["amount"] for x in tithes)
        out.append(_item("warn", f"捐贈還有 {total:,} 沒處理（{'、'.join(x['book'] for x in tithes[:3])}）", "tithe",
                         "收倉有賺的捐贈。到撲滿或總覽的「撲滿待處理」按 ✓；比例可以改。這是另一份金流，不會記進明細。"))
    for x in [x for x in pend if x["kind"] == "close"][:3]:
        out.append(_item("warn", f"「{x['book']}」到 {int(x['end_date'][5:7])}/{int(x['end_date'][8:10])} 結束了，該收倉", "book_due",
                         "收倉會把結果寫死、算捐贈。在撲滿那一層的「設定」或總覽的「撲滿待處理」按收倉。"))
    p = periods.current(conn)
    vio = conn.execute("SELECT COUNT(*) FROM trades t JOIN books b ON b.id = t.book_id WHERE t.violation IS NOT NULL "
                       "AND b.kind = 'real' AND t.date BETWEEN ? AND ?", (p.start.isoformat(), p.end.isoformat())).fetchone()[0]
    if vio:
        out.append(_item("bad", f"這期操盤違規 {vio} 次", "violation",
                         "停止之後又進場、或單筆賠超過單次可虧損。規矩在倉的設定裡；撲滿「檢討」一筆一筆列著。"))
    if books.layer_accounts(conn, "trading"):
        m = books.month_status(conn)
        if m["stopped"]:
            out.append(_item("bad", f"本月可虧損 {m['limit']:,} 打完了，這期剩下的日子停止操盤", "month_stop",
                             "這一期操盤加起來已經賠了這麼多。停下來，下一期再說；這期再進場會記成違規。"))
        for b in books.list_books(conn, layer="trading", kinds=("real",)):
            if b["status"] != "open":
                continue
            if b["rules_missing"]:
                out.append(_item("warn", f"「{b['name']}」還沒設可虧損", "rules",
                                 "每一倉開之前先寫好操作金額、可虧損、單次可虧損。到撲滿 → 資金操盤 → 設定。"))
            elif b["stopped"]:
                out.append(_item("warn", f"「{b['name']}」可虧損打完了，這一倉停止", "stopped",
                                 "停止之後只能離場；再進場會記成違規。想換做法就收倉、開新的一倉（先想清楚）。"))
    return out


def _templates(conn: sqlite3.Connection, p: periods.Period) -> list[dict[str, Any]]:
    from . import recurring
    return recurring.status(conn, p.key) if p.is_open() else []


def note_for(conn: sqlite3.Connection, txn: dict[str, Any]) -> str | None:
    """剛記完一筆：這筆動到的那一格怎麼樣了（只在有話要說的時候講：做到了、超過了、生活費走太快）。"""
    if txn["kind"] == "income" or txn.get("status") == "deleted":
        return None
    day = date.fromisoformat(txn["date"])
    own = periods.period_for(conn, day)
    key = txn.get("for_period") or own.key
    p = periods.get(conn, key)
    plan = plans.get(conn, p.key)
    s = stats.period_summary(conn, p.key)
    bucket = txn.get("bucket") or stats.PURPOSE_BUCKET.get(txn.get("purpose") or "")
    names = plans.bucket_names(conn)
    if bucket == "living" or (bucket is None and txn["kind"] == "expense"):
        lv = s["living"]
        if lv["budget"] and p.is_open() and lv["spent"] > lv["budget"]:
            return f"生活費這期超支 {lv['spent'] - lv['budget']:,}（預算 {lv['budget']:,}）"
        if lv["budget"] and p.is_open() and lv["pace"] and lv["daily_spent"] > lv["pace"] * 1.15 and lv["per_day_left"] is not None:
            return f"生活費花得比時間快：剩下每天 {lv['per_day_left']:,}（本來 {lv['daily_budget']:,}）"
        return None
    if not bucket:
        return None
    plan_amount = int(plan["buckets"].get(bucket) or 0)
    rolling = next((b["rollover"] for b in plans.buckets(conn) if b["id"] == bucket), False)
    if rolling:
        plan_amount = max(plan_amount + plans.rollover_carry(conn, bucket, p.key), 0)    # 滾存：能用的＝這期撥的＋前期留下的（欠太多就是 0）
    used = int(s["allocation"]["actual"].get(bucket) or 0)
    name = names.get(bucket, bucket)
    when = f"（{int(p.key[5:])}月那期）" if p.key != own.key else "這期"
    if not plan_amount and not (rolling and used):
        return None
    if used == plan_amount:
        return f"{name}{when}做到了 ✓（{used:,} / {plan_amount:,}）"
    info = next((b for b in plans.buckets(conn) if b["id"] == bucket), None)
    if info and plans.close_enough(conn, info, plan_amount, used):           # 會浮動的（水電、訂閱）：差不多就是做到了
        return f"{name}{when}差不多做到了 ≈✓（{used:,} / {plan_amount:,}，金額會浮動）"
    if used > plan_amount and bucket not in ("saving", "invest"):
        if rolling:
            return f"{name}{when} {used:,}，比能用的 {plan_amount:,} 多 {used - plan_amount:,}（多的下期的額度會先補）"
        return (f"{name}{when} {used:,}，比計畫 {plan_amount:,} 多 {used - plan_amount:,}"
                "（如果是預繳或補繳，說「11月房租」或在明細改「算哪一期」）")
    return None
