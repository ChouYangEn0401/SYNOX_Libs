"""規劃（W5）：每期收入怎麼分、各格與各大類的金額、三組改名、格子、固定項目。

欄位名是契約（app/js/routes.js 照這些名字送值）：可以加，不要改名或拿掉。
- plan：畫面的「存計畫」（PUT /api/plan）整包一次送，一個批次（可以一次復原）；
  buckets、group_budgets 是 JSON 字串（{"格子 id": 金額}、{"大類名": 金額}），只給要改的。
  人打字也可以寫「存錢:5000,生活費:15000」（格子用名字也行）。
- bucket、fixed：沒給 id＝新增；給 id＝改那一個（SYNOX 一個畫面最多 40 個動作，新增與修改共用一個）。id 也可以寫名字。
- 金額、是／否、清掉（「-」）的規則跟 setup.py 一樣（共用那裡的小工具）。
"""

import json
from typing import Any, Dict, Optional

from ..engine.services import plans, recurring, refdata, settings
from .setup import is_clear, items, out, pick, refuse, text, whole, write, yes_no
from .table import WRITE, Cmd, Field, Result, frozen, to_minor
from .table import show as table_show


def show(conn, minor) -> str:
    """規劃頁的回覆：金額前面帶幣別符號（共用的 table.show）。"""
    return table_show(conn, minor, symbol=True)

DOMAIN = "plan"

FLOWS = (("expense", "expense"), ("income", "income"), ("transfer", "transfer"), ("花錢", "expense"),
         ("支出", "expense"), ("收入", "income"), ("轉存", "transfer"), ("轉帳", "transfer"))
PURPOSES = (("saving", "saving"), ("investing", "investing"), ("prepay", "prepay"), ("card_payment", "card_payment"),
            ("other", "other"), ("存錢", "saving"), ("投資", "investing"), ("預付", "prepay"), ("繳卡費", "card_payment"),
            ("其他", "other"), ("搬錢", "other"))
CADENCES = (("monthly", "monthly"), ("bimonthly", "bimonthly"), ("每月", "monthly"), ("每個月", "monthly"),
            ("雙月", "bimonthly"), ("兩個月", "bimonthly"), ("兩個月一次", "bimonthly"))


# ── 計畫 ─────────────────────────────────────────────────────────────────────────

def _pairs(raw: str) -> Dict[str, Any]:
    """JSON 物件，或人打的「名字:金額,名字:金額」（也收 =）。"""
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError:
            raise refuse("金額表要是 JSON（例：{\"saving\": 5000}）") from None
        if not isinstance(data, dict):
            raise refuse("金額表要是 JSON 物件")
        return data
    data = {}
    for part in items(raw):
        sep = ":" if ":" in part else ("：" if "：" in part else "=")
        if sep not in part:
            raise refuse(f"「{part}」看不懂：要寫 名字:金額")
        k, v = part.split(sep, 1)
        data[k.strip()] = v.strip()
    return data


def _bucket_key(conn, key: str) -> str:
    """格子：id、名字、顯示名字都認（生活費＝living）。"""
    if key in plans.bucket_ids(conn) or key == "living":
        return key
    return refdata.bucket_by_name(conn, key)["id"]


def _money_map(book, raw: Optional[str], *, buckets: bool) -> Optional[Dict[str, int]]:
    if raw is None:
        return None
    data = {}
    for k, v in _pairs(raw).items():
        key = _bucket_key(book.conn, str(k)) if buckets else str(k)
        if v is None or (isinstance(v, str) and (is_clear(v.strip()) or not v.strip())):
            data[key] = 0
        else:
            data[key] = to_minor(book, v, f"「{k}」的金額", zero=True)
    return data


def run_plan(book, values):
    conn = book.conn
    period = text(values, "period")
    income = text(values, "income_expected")
    buckets = _money_map(book, text(values, "buckets"), buckets=True)
    groups = _money_map(book, text(values, "group_budgets"), buckets=False)
    if income is None and buckets is None and groups is None:
        raise refuse("要給至少一個：預計收入（income_expected）、各格金額（buckets）、各大類上限（group_budgets）")
    kw: Dict[str, Any] = {"buckets": buckets, "group_budgets": groups}
    if income is not None:
        if is_clear(income):
            kw["clear_income"] = True
        else:
            kw["income_expected"] = to_minor(book, income, "預計收入", zero=True)
    with write(book, "plan set") as w:
        p = plans.set_plan(w, None if period is None or is_clear(period) else period, **kw)
    names = plans.bucket_names(conn)
    parts = [f"預計收入 {show(conn, p['income_expected'])}"]
    parts += [f"{names.get(b, b)} {show(conn, v)}" for b, v in p["buckets"].items() if v]
    return Result(data=out(book, w, plan=p), text=f"{p['period']} 期的計畫：" + "、".join(parts))


def run_plan_names(book, values):
    conn = book.conn
    given = {k: text(values, k) for k in ("must", "flex", "living")}
    if all(v is None for v in given.values()):
        raise refuse("要給至少一組的名字（must、flex、living；- 回到預設）")
    with write(book, "plan groups") as w:
        for k, v in given.items():
            if v is not None:
                key = f"plan.group.{k}"
                settings.put(w, key, settings.coerce(conn, key, "" if is_clear(v) else v))
    names = plans.group_names(conn)
    return Result(data=out(book, w, group_names=names), text="、".join(g["name"] for g in names.values()))


# ── 格子 ─────────────────────────────────────────────────────────────────────────

def _bucket_id(conn, raw: str) -> str:
    for b in plans.buckets(conn, include_inactive=True):
        if raw in (b["id"], b["name"], b["official"]):
            return b["id"]
    return refdata.bucket_by_name(conn, raw)["id"]


def run_bucket(book, values):
    conn = book.conn
    bid = text(values, "id")
    label = text(values, "label")
    locked = yes_no(text(values, "locked"), "固定住")
    kw: Dict[str, Any] = {"active": yes_no(text(values, "active"), "開著"),
                          "rollover": yes_no(text(values, "rollover"), "滾存"),
                          "flex": yes_no(text(values, "flex"), "會浮動")}
    since = text(values, "rollover_from")
    if since is not None and not is_clear(since):
        kw["rollover_from"] = since
    cats, more = text(values, "categories"), text(values, "add_categories")
    with write(book, "bucket add" if bid is None else "bucket edit") as w:
        if bid is None:
            if not label or is_clear(label):
                raise refuse("新的一格要有名字（1～8 個字）")
            b = plans.add_bucket(w, label, locked=bool(locked))
            bid = b["id"]
        else:
            bid = _bucket_id(conn, bid)
            if label is not None:
                kw["label"] = "" if is_clear(label) else label
            if locked is not None:
                kw["locked"] = locked
        kw = {k: v for k, v in kw.items() if v is not None}
        if kw:
            plans.set_bucket(w, bid, **kw)
        if cats is not None:
            plans.set_bucket_categories(w, bid, items(cats))
        if more:
            have = plans.bucket_categories(w.conn).get(bid, [])
            plans.set_bucket_categories(w, bid, have + [n for n in items(more) if n not in have])
        b = next(x for x in plans.buckets(w.conn, include_inactive=True) if x["id"] == bid)
    state = "" if b["active"] else "（停用）"
    return Result(data=out(book, w, bucket=b), text=f"「{b['name']}」好了{state}" + ("、🔒 一定要做到" if b["locked"] else "")
                  + ("、↻ 滾存" if b["rollover"] else "") + ("、≈ 會浮動" if b["flex"] else ""))


# ── 固定項目 ──────────────────────────────────────────────────────────────────────

def _recurring_id(conn, raw: str) -> int:
    if raw.isdigit():
        return int(raw)
    row = conn.execute("SELECT id FROM recurring WHERE name = ?", (raw,)).fetchone()
    if row is None:
        raise refuse(f"找不到固定項目「{raw}」", status=404)
    return row["id"]


def _word(raw: Optional[str], table, what: str) -> Optional[str]:
    if raw is None:
        return None
    hit = pick(raw, table)
    if hit is None:
        raise refuse(f"{what}看不懂：「{raw}」")
    return hit


def run_fixed(book, values):
    conn = book.conn
    rid = text(values, "id")
    purpose = text(values, "purpose")
    cadence = _word(text(values, "cadence"), CADENCES, "多久一次")
    active = yes_no(text(values, "active"), "開著")
    raw_amount = text(values, "amount")
    day = whole(text(values, "day_of_month"), "每月幾號", 1, 31)
    if rid is None:
        name, kind = text(values, "name"), _word(text(values, "kind"), FLOWS, "種類")
        if not name or is_clear(name) or kind is None:
            raise refuse("固定項目要有名字和種類（花錢、收入、轉存）")
        opt = {k: text(values, k) for k in ("category", "account", "to", "bucket")}
        opt = {k: (None if v is None or is_clear(v) else v) for k, v in opt.items()}
        note = text(values, "note")
        with write(book, "recurring add") as w:
            item = recurring.add(w, name=name, kind=kind,
                                 amount=None if raw_amount is None or is_clear(raw_amount) else to_minor(book, raw_amount, "金額", zero=True),
                                 day_of_month=day or 5, cadence=cadence or "monthly",
                                 purpose=None if purpose is None or is_clear(purpose) else _word(purpose, PURPOSES, "用途"),
                                 note="" if note is None or is_clear(note) else note, **opt)
            if active is False:
                item = recurring.update(w, item["id"], active=0)
        return Result(data=out(book, w, item=item), text=f"加了固定項目：{item['name']}（每月 {item['day_of_month']} 號）")
    target = _recurring_id(conn, rid)
    if text(values, "kind") is not None:
        before = conn.execute("SELECT kind FROM recurring WHERE id = ?", (target,)).fetchone()
        if before is None or _word(text(values, "kind"), FLOWS, "種類") != before["kind"]:
            raise refuse("種類不能改：要換就加一個新的，把這個停掉")
    fields: Dict[str, Any] = {"day_of_month": day, "cadence": cadence}
    name = text(values, "name")
    if name is not None:
        if is_clear(name):
            raise refuse("名字不能空白")
        fields["name"] = name
    note = text(values, "note")
    if note is not None:
        fields["note"] = "" if is_clear(note) else note
    if raw_amount is not None:
        if is_clear(raw_amount):
            fields["clear_amount"] = True
        else:
            fields["amount"] = to_minor(book, raw_amount, "金額", zero=True)
    if active is not None:
        fields["active"] = int(active)
    if purpose is not None:
        fields["purpose"] = "" if is_clear(purpose) else _word(purpose, PURPOSES, "用途")
    for key in ("bucket", "category", "account"):
        v = text(values, key)
        if v is not None:
            fields[key] = "" if is_clear(v) else v
    to = text(values, "to")
    if to is not None:
        if is_clear(to):
            raise refuse("轉存一定要有轉入的帳戶；不要了就把這一項停掉")
        fields["to"] = to
    with write(book, "recurring edit") as w:
        item = recurring.update(w, target, **{k: v for k, v in fields.items() if v is not None})
    return Result(data=out(book, w, item=item), text=f"改好了：{item['name']}" + ("" if item["active"] else "（停掉了）"))


COMMANDS = (
    Cmd("plan", "改某一期收入怎麼分：預計收入、各格金額、各大類上限（只給要改的）", WRITE, [
        Field("period", "哪一期（不給＝這一期）"),
        Field("income_expected", "預計收入（- 清掉）"),
        Field("buckets", "各格金額（JSON：{\"格子\": 金額}；或 名字:金額,名字:金額）"),
        Field("group_budgets", "各大類上限（JSON：{\"大類\": 金額}；或 名字:金額,名字:金額）")], run_plan,
        positional=0, app=True),
    Cmd("plan_names", "收入怎麼分的三組改名（- 回到預設）", WRITE, [
        Field("must", "一定要做到的那組"),
        Field("flex", "有餘力再做的那組"),
        Field("living", "剩下可以花的那組")], run_plan_names, app=True),
    Cmd("bucket", "加一格用途（不給 id），或改一格：名字、固定住、停用、認領的細項、滾存、會浮動", WRITE, [
        Field("id", "哪一格（id 或名字；不給＝新增）"),
        Field("label", "名字"),
        Field("locked", "固定住，一定要做到（yes／no）"),
        Field("active", "開著（yes／no）"),
        Field("categories", "認領哪些細項（逗號分開，整組取代；- 全放開）"),
        Field("add_categories", "再多認領這幾個（逗號分開）"),
        Field("rollover", "沒用完的留到下期（yes／no）"),
        Field("rollover_from", "從哪一期開始算"),
        Field("flex", "金額會浮動（yes／no）")], run_bucket, positional=0, app=True),
    Cmd("fixed", "加一個固定項目（不給 id；每月的薪水、房租、訂閱…），或改一個", WRITE, [
        Field("id", "哪一個（編號或名字；不給＝新增）"),
        Field("name", "名字"),
        Field("kind", "種類：花錢／收入／轉存（新增才要，之後不能改）"),
        Field("amount", "金額（浮動的不給；- 清掉）"),
        Field("day_of_month", "每月幾號"),
        Field("category", "細項（- 不指定）"),
        Field("account", "帳戶（- 用預設）"),
        Field("to", "轉入哪個帳戶（轉存）"),
        Field("purpose", "轉存用途：存錢／投資／預付／繳卡費／其他"),
        Field("cadence", "多久一次：每月／兩個月一次"),
        Field("note", "備註"),
        Field("active", "開著（yes／no）"),
        Field("bucket", "屬於哪一格（- 照細項自己對）")], run_fixed, positional=0, app=True),
)

READS = frozen({})
EXPORTS = frozen({})
