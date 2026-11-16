"""進階金流（W7）：只在聊天用的指令（沒有畫面動作）——信用卡、負債分期、代墊淨額、餘額核對、水電帳單、收據附檔。

每個 run 只做「字串欄位 → 引擎的參數 → 呼叫 services → 組回覆」；對照來源的信用卡、負債、餘額、帳單四組指令。
這裡的指令都不是畫面動作，欄位名由這個檔決定（不在 routes.js 裡）。
附檔只收這個人自己檔案資料夾裡的檔（引擎的 paths.confined：擋 ..、擋跑出去的絕對路徑）。

金額與日期用全工具共用的 bridge/table.py（to_minor、show、parse_day）；這裡另有 review.py 也會用的小工具：是／否、編號、寫入的外殼。
模組層級只放唯讀的東西（隔離規則 4）。
"""

import json
import re
from contextlib import contextmanager
from datetime import date

from synox.packs import kit

from .table import READ, WRITE, Cmd, Field, Result, day_or_none, frozen, parse_day, to_minor
from .table import show as money

DOMAIN = "flow"

YES = ("yes", "y", "true", "1", "是", "對", "要", "on")
NO = ("no", "n", "false", "0", "否", "不", "不要", "off")
LINE_STATUS = (("matched", "對上"), ("diff", "金額不同"), ("missing", "漏記"), ("installment", "分期"), ("fee", "手續費"),
               ("payment", "繳款"), ("ignored", "不用管"))
FIX_WORDS = (("create", "create"), ("補記", "create"), ("use_ledger", "use_ledger"), ("改成帳單金額", "use_ledger"),
             ("move", "move"), ("改到這張卡", "move"), ("ignore", "ignore"), ("不用管", "ignore"), ("忽略", "ignore"),
             ("unignore", "unignore"), ("make_debt", "make_debt"), ("建分期", "make_debt"))
DEBT_KINDS = (("card_installment", "card_installment"), ("卡分期", "card_installment"), ("分期", "card_installment"),
              ("self_loan", "self_loan"), ("自借款", "self_loan"), ("credit_loan", "credit_loan"), ("信貸", "credit_loan"))
DEBT_NATURES = (("positive", "positive"), ("正向", "positive"), ("negative", "negative"), ("負向", "negative"),
                ("消費", "negative"), ("wear", "wear"), ("損耗", "wear"))
STATEMENT_LINE_FIELDS = ("lines", "lines2", "lines3", "lines4", "lines5")


# ── 小工具（review.py 也用）──────────────────────────────────────────────────────

def engine():
    """引擎的 services（用到才 import：W2 之前的空殼也載得起來）。"""
    from ..engine import db
    from ..engine.services import (attachments, balances, bills, cardcash, cards, debts, ingest, plans, refdata,
                                   report, reviews, settings, transactions, views)
    return {"db": db, "attachments": attachments, "balances": balances, "bills": bills, "cardcash": cardcash,
            "cards": cards, "debts": debts, "ingest": ingest, "plans": plans, "refdata": refdata, "report": report,
            "reviews": reviews, "settings": settings, "transactions": transactions, "views": views}


def text_of(values, name) -> str:
    return str((values or {}).get(name) or "").strip()


def yes(value, default=None):
    """是／否欄位 → True／False；沒給回 default；看不懂丟 AppError。"""
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in YES:
        return True
    if text in NO:
        return False
    raise kit.AppError(f"「{value}」看不懂，要寫 yes 或 no", code="invalid", status=400)


def int_of(value, what: str) -> int:
    text = str(value or "").strip().lstrip("#")
    if not re.fullmatch(r"\d{1,9}", text):
        raise kit.AppError(f"{what}要是編號（數字），例如 3", code="invalid", status=400)
    return int(text)


def choice(value, table, what: str):
    text = str(value or "").strip()
    for word, key in table:
        if text.lower() == word.lower():
            return key
    raise kit.AppError(f"{what}「{text}」不對，可以是：" + "、".join(dict.fromkeys(w for w, _ in table)),
                       code="invalid", status=400)


def json_of(value, what: str):
    try:
        return json.loads(value)
    except (TypeError, ValueError) as e:
        raise kit.AppError(f"{what}不是正確的 JSON（{e}）", code="invalid", status=400) from None


def source_of(book) -> str:
    """寫進批次的「從哪裡來」：synox:telegram → telegram。"""
    actor = str(getattr(book, "actor", "") or "")
    return actor.split(":", 1)[1] if ":" in actor else (actor or "synox")


@contextmanager
def write(book, command: str, *, dry_run: bool = False):
    """引擎的一次寫入（一批；可以撤銷）。"""
    db = engine()["db"]
    with db.write(book.conn, actor=book.actor, source=source_of(book), command=command, dry_run=dry_run) as w:
        yield w


def stamp(book, w, data: dict) -> dict:
    """寫入的動作一律帶 batch_id（沒改到東西＝None）與 write_seq。"""
    db = engine()["db"]
    data["batch_id"] = w.batch_id if (w.changes and not w.dry_run) else None
    data["write_seq"] = db.write_seq(book.conn)
    return data


# ── 信用卡 ─────────────────────────────────────────────────────────────────────

def _detail_text(conn, d: dict) -> str:
    status = dict(LINE_STATUS)
    head = (f"帳單 #{d['id']} {d['card']} {d['closing_date']} 結帳（{d['period_start'][5:]}–{d['closing_date'][5:]}），"
            f"總額 {money(conn, d['total'])}" + (f"，{d['due_date'][5:]} 前繳" if d["due_date"] else "")
            + ("，已繳" if d["paid"] else "") + ("，對平了" if d["balanced"] else f"，還有 {d['problems']} 列要處理"))
    rows = [head]
    for ln in d["lines"]:
        led = ln["ledger"]
        rows.append(f"  列 #{ln['id']} {ln['date'][5:]} {ln['desc']} {money(conn, ln['amount'])}　{status.get(ln['status'], ln['status'])}"
                    + (f"（記帳 {led['id']}：{led['desc']} {money(conn, led['amount'])}）" if led else "")
                    + (f"　建議：{ln['suggestion']['text']}" if ln.get("suggestion") else ""))
    if d["ledger_only"]:
        rows.append("記在這張卡、帳單上沒有：")
        rows += [f"  {t['date'][5:]} {t['desc']} {money(conn, t['amount'])}（{t['reason']}）" for t in d["ledger_only"]]
    if d["gap"]:
        rows.append(f"總額跟明細加總差 {money(conn, d['gap'])}（可能有上期餘額或沒列到的項目）")
    return "\n".join(rows)


def run_card(book, values) -> Result:
    e = engine()
    conn = book.conn
    if text_of(values, "statement"):
        d = e["cards"].detail(conn, int_of(values["statement"], "帳單"))
        return Result(data=d, text=_detail_text(conn, d))
    rows = e["cards"].all_cards(conn)
    if text_of(values, "card"):
        name = e["refdata"].resolve_account(conn, values["card"])["name"]
        rows = [r for r in rows if r["account"] == name]
    if not rows:
        return Result(data={"cards": []}, text="還沒有信用卡帳戶（到設定頁加一個帳戶，種類選信用卡）")
    lines = []
    for r in rows:
        r["cash"] = e["cardcash"].card(conn, e["refdata"].account_by_id(conn, r["account_id"]))
        lines.append(r["cash"]["text"])
        st = r["last_statement"]
        lines.append(f"  未出帳 {money(conn, r['unbilled'])}（{r['unbilled_count']} 筆）"
                     + (f"，額度用了 {round(r['usage'] * 100)}%" if r["usage"] is not None else "")
                     + (f"；上期帳單 #{st['id']} {st['closing_date'][5:]} 結帳 {money(conn, st['total'])}"
                        + ("（已繳）" if st["paid"] else "") if st else "；還沒有帳單")
                     + (f"；{r['alert']}" if r["alert"] else ""))
        r["last_statement"] = st and {k: st[k] for k in ("id", "closing_date", "due_date", "total", "paid", "problems")}
    return Result(data={"cards": rows}, text="\n".join(lines))


def _statement_lines(book, text, closing):
    """「01/05 範例便當店 120; 01/06 範例超商 70」→ 帳單的逐列（分號或換行分開；金額照帳本的小數位數）。"""
    out = []
    ref = date.fromisoformat(closing) if closing else None
    for raw in re.split(r"[;；\n]+", text):
        part = raw.strip()
        if not part:
            continue
        bits = part.split()
        if len(bits) < 3:
            raise kit.AppError(f"帳單這一列看不懂：「{part}」（要寫「日期 說明 金額」，例如 01/05 範例便當店 120）",
                               code="invalid", status=400)
        day = parse_day(bits[0], ref or book.today, "帳單列的日期")
        if ref and not re.match(r"\d{4}", bits[0]) and day > ref.isoformat():
            day = date.fromisoformat(day).replace(year=ref.year - 1).isoformat()    # 12/28 在 1 月結帳的帳單上＝去年
        out.append({"date": day, "desc": " ".join(bits[1:-1]), "amount": to_minor(book, bits[-1], "帳單列的金額",
                                                                                        negative=True, zero=True)})
    return out


def run_card_statement(book, values) -> Result:
    e = engine()
    conn = book.conn
    rec = {}
    if text_of(values, "content"):
        rec = json_of(values["content"], "帳單內容")
        if not isinstance(rec, dict):
            raise kit.AppError("帳單內容要是一個 JSON 物件（card、closing_date、total、lines）", code="invalid", status=400)
    rec = {k: v for k, v in rec.items() if k != "type"}
    for name in ("card", "due_date", "period_start", "closing_date"):
        if text_of(values, name):
            rec[name] = (text_of(values, name) if name == "card"
                         else parse_day(values[name], book.today, {"closing_date": "結帳日", "due_date": "繳款截止日",
                                                                    "period_start": "帳單起日"}[name]))
    for name, what in (("total", "帳單總額"), ("min_payment", "最低應繳")):
        if text_of(values, name):
            rec[name] = to_minor(book, values[name], what, negative=True, zero=True)
    extra = []
    for name in STATEMENT_LINE_FIELDS:
        if text_of(values, name):
            extra += _statement_lines(book, values[name], rec.get("closing_date"))
    if extra:
        rec["lines"] = list(rec.get("lines") or []) + extra
    missing = [label for key, label in (("card", "哪張卡（card）"), ("closing_date", "結帳日（closing_date）"),
                                        ("total", "總額（total）"), ("lines", "逐列明細（lines）")) if not rec.get(key)]
    if missing:
        raise kit.AppError("帳單還少了：" + "、".join(missing), code="invalid", status=400)
    payload = {"records": [{"type": "card_statement", **rec}]}
    ref = text_of(values, "source_ref")
    out = e["ingest"].run(conn, payload, actor=book.actor, source=source_of(book), source_ref=ref or None,
                          command="card_statement")
    r = out["results"][0]
    d = e["cards"].detail(conn, r["statement_id"])
    head = {"replayed": "這張帳單之前記過了（內容一樣，沒有重寫）", "replaced": "帳單更新了"}.get(r["status"], "帳單記好了")
    data = {"status": r["status"], "statement": d, "batch_id": out["batch_id"],
            "write_seq": e["db"].write_seq(conn)}
    return Result(data=data, text=head + "\n" + _detail_text(conn, d), warnings=list(out["warnings"]))


def run_card_match(book, values) -> Result:
    e = engine()
    line = int_of(values.get("line"), "帳單的列")
    txn = text_of(values, "txn") or None
    action = choice(values["action"], FIX_WORDS, "處理方式") if text_of(values, "action") else None
    if not txn and not action:
        raise kit.AppError("要給 txn（對到哪一筆帳），或 action（補記／改成帳單金額／改到這張卡／不用管）",
                           code="invalid", status=400)
    with write(book, "card_match" if not action else f"card_match {action}") as w:
        d = (e["cards"].fix(w, line, action, txn_id=txn) if action else e["cards"].match(w, line, txn))
    data = stamp(book, w, {"statement": d})
    return Result(data=data, text=_detail_text(book.conn, d))


def run_card_pay(book, values) -> Result:
    e = engine()
    conn = book.conn
    amount = to_minor(book, values["amount"], "繳多少", zero=True) if text_of(values, "amount") else None
    with write(book, "card_pay") as w:
        r = e["cards"].pay(w, int_of(values.get("id"), "帳單"), from_account=text_of(values, "from_account") or None,
                           amount=amount, day=day_or_none(values, "date", book.today))
    data = stamp(book, w, r)
    p = r["payment"]
    return Result(data=data, text=f"記好了：{p['account']} → {p['to_account']} {money(conn, p['amount'])}"
                                  "（繳卡費是轉帳，不算支出）")


# ── 負債與分期、代墊淨額 ────────────────────────────────────────────────────────────

def _debt_line(conn, d: dict) -> str:
    nxt = d["next"]
    return (f"#{d['id']} {d['name']}（{d['kind_label']}）每期 {money(conn, d['per_period'])}，"
            f"{d['paid_count']}/{d['periods']} 期，還剩 {money(conn, d['remaining'])}"
            + (f"，下一期 {nxt['due_date']} {money(conn, nxt['amount'])}" if nxt else "，已還完")
            + (f"，有 {len(d['overdue'])} 期過期沒付" if d["overdue"] else ""))


def run_debt(book, values) -> Result:
    e = engine()
    conn = book.conn
    do = (text_of(values, "do") or ("show" if text_of(values, "id") else "list")).lower()
    debts = e["debts"]
    if do in ("list", "all", "列出"):
        rows = debts.all_debts(conn, include_done=do == "all")
        monthly = sum(d["per_period"] for d in rows if not d["done"])
        text = "\n".join([f"每月固定要還 {money(conn, monthly)}"] + [_debt_line(conn, d) for d in rows]) if rows else "沒有負債"
        return Result(data={"debts": rows, "monthly": monthly}, text=text)
    if do in ("show", "看"):
        d = debts.get(conn, int_of(values.get("id"), "負債"))
        rows = [f"  第 {r['no']} 期 {r['due_date']} {money(conn, r['amount'])} {'已付' if r['status'] == 'paid' else '未付'}"
                for r in d["schedule"]]
        return Result(data=d, text="\n".join([_debt_line(conn, d), *rows]))
    if do in ("add", "新增"):
        name = text_of(values, "name")
        if not name or not text_of(values, "periods") or not text_of(values, "start"):
            raise kit.AppError("新增負債要給 name（名字）、periods（幾期）、start（第一期的日期），"
                               "再加 principal（本金）或 per（每期金額）", code="invalid", status=400)
        kind = choice(values.get("kind") or "card_installment", DEBT_KINDS, "負債種類")
        nature = choice(values.get("nature") or "positive", DEBT_NATURES, "負債性質")
        with write(book, "debt add") as w:
            r = debts.create(w, name=name, kind=kind, periods=int_of(values["periods"], "期數"),
                             principal=to_minor(book, values["principal"], "本金", zero=True) if text_of(values, "principal") else None,
                             per_period=to_minor(book, values["per"], "每期金額", zero=True) if text_of(values, "per") else None,
                             start_date=parse_day(values["start"], book.today, "第一期的日期"),
                             account=text_of(values, "account") or None, lender=text_of(values, "lender") or None,
                             nature=nature, paid_before=day_or_none(values, "paid_before", book.today),
                             note=text_of(values, "note"), source=source_of(book))
        data = stamp(book, w, r)
        return Result(data=data, text="新增：" + _debt_line(conn, r["debt"]))
    if do in ("pay", "還"):
        with write(book, "debt pay") as w:
            r = debts.pay(w, int_of(values.get("id"), "負債"), no=int_of(values["no"], "第幾期") if text_of(values, "no") else None,
                          from_account=text_of(values, "from_account") or None,
                          day=day_or_none(values, "date", book.today))
        data = stamp(book, w, r)
        pay = r["payment"]
        return Result(data=data, text=f"第 {r['paid_no']} 期付了" + (
            f"：{pay['account']} → {pay['to_account']} {money(conn, pay['amount'])}（轉帳）" if pay
            else "（卡分期：錢在信用卡帳單裡，這裡只標成已付）"))
    if do in ("edit", "改"):
        active = yes(values.get("active"))
        with write(book, "debt edit") as w:
            d = debts.update(w, int_of(values.get("id"), "負債"), name=text_of(values, "name") or None,
                             kind=choice(values["kind"], DEBT_KINDS, "負債種類") if text_of(values, "kind") else None,
                             nature=choice(values["nature"], DEBT_NATURES, "負債性質") if text_of(values, "nature") else None,
                             account=text_of(values, "account") or None, lender=text_of(values, "lender") or None,
                             note=text_of(values, "note") or None, active=None if active is None else int(active))
        data = stamp(book, w, {"debt": d})
        return Result(data=data, text="改好了：" + _debt_line(conn, d))
    raise kit.AppError(f"do=「{do}」不對，可以是 list、all、show、add、pay、edit", code="invalid", status=400)


def run_lend(book, values) -> Result:
    e = engine()
    conn = book.conn
    people = e["debts"].people(conn)
    pend = e["debts"].pending_advances(conn)
    lines = []
    for r in people:
        net = r["net"]
        head = (f"{r['counterparty']} 欠我 {money(conn, net)}" if net > 0
                else f"我欠 {r['counterparty']} {money(conn, -net)}" if net < 0 else f"{r['counterparty']} 兩清")
        lines.append(head + f"（我先付 {money(conn, r['lent'])}、收回 {money(conn, r['collected'])}、"
                            f"他先付 {money(conn, r['owed'])}、我還 {money(conn, r['repaid'])}）")
    data = {"people": people, "they_owe": pend["total"], "i_owe": pend["owe"]["total"]}
    if lines:
        lines.append(f"合計：別人欠我 {money(conn, pend['total'])}、我欠別人 {money(conn, pend['owe']['total'])}")
    return Result(data=data, text="\n".join(lines) or "沒有代墊，也沒有欠別人")


# ── 餘額核對 ───────────────────────────────────────────────────────────────────

def run_balance(book, values) -> Result:
    e = engine()
    conn = book.conn
    balances = e["balances"]
    if text_of(values, "adjust"):
        with write(book, "balance adjust") as w:
            r = balances.adjust(w, int_of(values["adjust"], "核對紀錄"))
        data = stamp(book, w, r)
        t = r["adjustment"]
        return Result(data=data, text=f"補了一筆{'支出' if t['kind'] == 'expense' else '收入'} {money(conn, t['amount'])}"
                                      f"（{t['account']}，{t['date']}）" + ("，記得分類" if t["kind"] == "expense" else ""))
    if not text_of(values, "account") or not text_of(values, "balance"):
        if text_of(values, "account") or text_of(values, "balance"):
            raise kit.AppError("核對餘額要同時給 account（哪個帳戶）與 balance（實際餘額）", code="invalid", status=400)
        o = balances.overview(conn, None)
        lines = [f"資產 {money(conn, o['assets'])}、卡債 {money(conn, o['card_debt'])}、其他負債 {money(conn, o['other_debt'])}、"
                 f"淨值 {money(conn, o['net_worth'])}"]
        lines += [f"  {a['name']} {money(conn, a['balance'])}　本期 {money(conn, a['change'])}　"
                  + (f"核對 {a['verified_on']}" if a["verified_on"] else "沒核對過") for a in o["accounts"]]
        return Result(data=o, text="\n".join(lines))
    amount = to_minor(book, values["balance"], "實際餘額", negative=True, zero=True)
    with write(book, "balance") as w:
        r = balances.set_snapshot(w, account=values["account"], balance=amount,
                                  as_of=day_or_none(values, "as_of", book.today), note=text_of(values, "note"),
                                  source=source_of(book))
    data = stamp(book, w, r)
    if r["first"]:
        text = f"{r['account']} {r['as_of']}：{money(conn, r['balance'])}（第一次核對，當作起始餘額）"
    elif r["diff"]:
        text = (f"{r['account']} {r['as_of']}：實際 {money(conn, r['balance'])}，照記帳推算 {money(conn, r['derived'])}，"
                f"差 {money(conn, r['diff'])}；要補一筆差額就打 /money_balance adjust={r['snapshot_id']}")
    else:
        text = f"{r['account']} {r['as_of']}：{money(conn, r['balance'])}，跟記帳推算的一樣"
    return Result(data=data, text=text)


# ── 水電瓦斯帳單 ────────────────────────────────────────────────────────────────

def _bill_line(conn, b: dict) -> str:
    return (f"#{b['id']} {b['label']} {b['period_end']} {money(conn, b['amount'])}"
            + (f"（{b['usage']:g} {b['unit'] or ''}）" if b["usage"] else "")
            + ("，已繳" if b["paid"] else (f"，{b['due_date']} 到期" if b["due_date"] else "，還沒繳")))


def run_bill(book, values) -> Result:
    e = engine()
    conn = book.conn
    bills = e["bills"]
    do = (text_of(values, "do") or "list").lower()
    kind = text_of(values, "type") or None
    if do in ("list", "列出"):
        rows = bills.all_bills(conn, kind)
        return Result(data={"bills": rows}, text="\n".join(_bill_line(conn, b) for b in rows) or "還沒有帳單")
    if do in ("trend", "趨勢"):
        t = bills.trend(conn, kind or "electricity")
        lines = [f"{t['label']}："] + [
            f"  {p['label']} {money(conn, p['amount'])}" + (f"・{p['usage']:g} {p['unit'] or ''}" if p["usage"] else "")
            + ("（夏月）" if p["summer"] else "") for p in t["points"]]
        if t["insight"]:
            lines.append(t["insight"])
        return Result(data=t, text="\n".join(lines) if t["points"] else f"還沒有{t['label']}的帳單")
    if do in ("add", "新增"):
        if not kind or not text_of(values, "amount"):
            raise kit.AppError("新增帳單要給 type（電費、水費、瓦斯…）與 amount（應繳金額）", code="invalid", status=400)
        usage = text_of(values, "usage")
        try:
            usage_value = float(usage) if usage else None
        except ValueError:
            raise kit.AppError("用量（usage）要是數字，例如 350", code="invalid", status=400) from None
        paid_day = day_or_none(values, "date", book.today)
        with write(book, "bill add") as w:
            r = bills.save(w, bill=kind, amount=to_minor(book, values["amount"], "應繳金額", zero=True),
                           period_end=day_or_none(values, "period_end", book.today, "計費期間迄日"),
                           period_start=day_or_none(values, "period_start", book.today, "計費期間起日"),
                           usage=usage_value, due_date=day_or_none(values, "due_date", book.today, "繳費期限"),
                           paid=bool(yes(values.get("paid"), False)), paid_date=paid_day,
                           account=text_of(values, "account") or None, note=text_of(values, "note"),
                           source=source_of(book))
        data = stamp(book, w, r)
        b = r["bill"]
        head = "這張帳單記過了" if r["status"] == "replayed" else "帳單記好了"
        tail = [] if b["paid"] or not b["due_date"] else [f"{b['label']} 還沒繳，{b['due_date'][5:]} 到期（繳了打 /money_bill pay id={b['id']}）"]
        return Result(data=data, text=f"{head}：{_bill_line(conn, b)}", warnings=tail)
    if do in ("pay", "繳"):
        with write(book, "bill pay") as w:
            r = bills.pay(w, int_of(values.get("id"), "帳單"), account=text_of(values, "account") or None,
                          day=day_or_none(values, "date", book.today))
        data = stamp(book, w, r)
        p = r["payment"]
        return Result(data=data, text=f"記好了：{p['desc']} {money(conn, p['amount'])}（{p['account']}）")
    raise kit.AppError(f"do=「{do}」不對，可以是 list、trend、add、pay", code="invalid", status=400)


# ── 收據附檔 ───────────────────────────────────────────────────────────────────

def run_attach(book, values) -> Result:
    e = engine()
    conn = book.conn
    t = e["transactions"].get(conn, text_of(values, "id"))
    info = e["attachments"].inspect(text_of(values, "file"))
    with write(book, "attach") as w:
        a = e["attachments"].save(w, t["id"], info)
    data = stamp(book, w, {"attachment": a, "txn_id": t["id"]})
    return Result(data=data, text=f"收據掛好了：{t['date']} {t['title']} {money(conn, t['amount'])}（{info['name']}）")


COMMANDS = (
    Cmd("card", "每張信用卡：這期要繳多少、幾號繳、扣款帳戶要留多少；給 statement 看一張帳單的逐列對帳", READ, [
        Field("card", "哪張卡（不給＝全部）"),
        Field("statement", "看哪一張帳單的逐列（帳單編號）")], run_card, positional=1),
    Cmd("card_statement", "寫入一張信用卡帳單與逐列明細，然後跟記帳對帳", WRITE, [
        Field("card", "哪張卡"),
        Field("closing_date", "結帳日"),
        Field("total", "本期應繳總額"),
        Field("due_date", "繳款截止日"),
        Field("period_start", "帳單期間起日（不給＝上個月結帳日的隔天）"),
        Field("min_payment", "最低應繳"),
        Field("lines", "逐列：「日期 說明 金額」，用分號分開（例 01/05 範例便當店 120; 01/06 範例超商 70）"),
        Field("lines2", "逐列（接著寫，太長時分開放）"),
        Field("lines3", "逐列（接著寫）"),
        Field("lines4", "逐列（接著寫）"),
        Field("lines5", "逐列（接著寫）"),
        Field("content", "整張帳單（JSON：card、closing_date、total、lines；金額是最小單位）"),
        Field("source_ref", "這張帳單的來源編號（重送不會多記）")], run_card_statement, positional=0),
    Cmd("card_match", "帳單的一列：手動對到一筆帳，或處理沒對上的（補記、改成帳單金額、改到這張卡、不用管）", WRITE, [
        Field("line", "帳單的哪一列（列編號）", required=True),
        Field("txn", "對到哪一筆帳（帳的編號）"),
        Field("action", "或怎麼處理：補記／改成帳單金額／改到這張卡／不用管／建分期")], run_card_match, positional=2),
    Cmd("card_pay", "記一筆繳卡費（轉帳，不算支出）", WRITE, [
        Field("id", "哪一張帳單（帳單編號）", required=True),
        Field("from_account", "從哪個帳戶扣（不給＝卡片設定的扣款帳戶）"),
        Field("amount", "繳多少（不給＝還沒繳的金額）"),
        Field("date", "哪一天（不給＝今天）")], run_card_pay, positional=1),
    Cmd("debt", "負債與分期：列出、看排程、新增、還一期、修改", WRITE, [
        Field("do", "list／all／show／add／pay／edit（不給＝列出）"),
        Field("id", "哪一筆負債（編號）"),
        Field("name", "名字（例 範例手機分期）"),
        Field("kind", "種類：卡分期／自借款／信貸"),
        Field("periods", "幾期"),
        Field("principal", "本金"),
        Field("per", "每期金額（不給＝本金÷期數，零頭放第一期）"),
        Field("start", "第一期的日期"),
        Field("account", "卡分期＝哪張卡；自借款＝從哪個帳戶還；信貸＝負債帳戶"),
        Field("lender", "自借款：借自哪個帳戶"),
        Field("nature", "性質：正向／負向／損耗"),
        Field("paid_before", "這天以前到期的期數當作已付"),
        Field("no", "還第幾期（不給＝最早還沒付的那期）"),
        Field("from_account", "還款從哪個帳戶付"),
        Field("date", "還款日（不給＝今天）"),
        Field("note", "備註"),
        Field("active", "還算不算這筆（yes／no）")], run_debt, positional=2),
    Cmd("lend", "代墊與欠款：每個人的淨額，誰欠我、我欠誰", READ, [], run_lend),
    Cmd("balance", "核對帳戶餘額（看差多少、可以補差額）；不給帳戶＝列出每個帳戶的餘額", WRITE, [
        Field("account", "哪個帳戶"),
        Field("balance", "實際餘額（信用卡寫負的）"),
        Field("as_of", "哪一天結束時（不給＝今天）"),
        Field("note", "備註"),
        Field("adjust", "補一筆差額：核對紀錄的編號")], run_balance, positional=2),
    Cmd("bill", "水電瓦斯帳單：列出、趨勢、新增、記繳費", WRITE, [
        Field("do", "list／trend／add／pay（不給＝列出）"),
        Field("id", "哪一張（帳單編號）"),
        Field("type", "種類：電費、水費、瓦斯、手機網路、房租"),
        Field("amount", "應繳金額"),
        Field("usage", "用量（度）"),
        Field("period_start", "計費期間起日"),
        Field("period_end", "計費期間迄日"),
        Field("due_date", "繳費期限"),
        Field("paid", "已經繳了（yes／no）"),
        Field("account", "從哪個帳戶繳"),
        Field("date", "繳費日"),
        Field("note", "備註")], run_bill, positional=2),
    Cmd("attach", "把自己檔案資料夾裡的一個收據檔掛到一筆帳上", WRITE, [
        Field("id", "哪一筆帳（帳的編號）", required=True),
        Field("file", "檔名（自己檔案資料夾裡的，寫相對路徑）", required=True)], run_attach, positional=2),
)

READS = frozen({})
EXPORTS = frozen({})
