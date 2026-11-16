"""跟舊版（0.3.1）相容與搬家（W6）：舊指令名照樣能打、舊資料第一次自動轉進來、CSV 匯出匯入、工具頁的小面板。

- 舊指令 add、in、transfer、list、undo、rm、edit、cat_add 由新的領域檔接手（名字一樣）；這裡放其餘的舊名字：
  month（這一期：照類別分＋收入＋轉帳＋生活費預算還剩多少）、budget（每月預算＝生活費那一格）、cap（單類上限＝
  細項的預設預算）、cats、cat_rm（＝停用）、template（＝起始類別與選配功能）；新的 import_csv。
- on_open：bridge/session.py 每次開好帳本都會叫（在這個人的寫入鎖裡）。舊資料的轉換在 legacy.py，
  那裡是全工具唯一准用 pack 的共用存放區（store）的地方，而且一定是 .of(uid)。
- 匯出 entries_csv（照篩選；kit.csv_bytes）、小面板 GET /summary（panel.html）。

金額：帳本裡一律是最小單位的整數（小數位數是帳本設定 currency.decimals）；人打的字照小數位數換算，
回給人看的字換回來。
"""

import re
from decimal import Decimal

from synox.packs import kit

from ..engine import db
from ..engine.errors import LedgerError
from ..engine.services import csvio, periods, plans, refdata, settings, starter, stats
from ..engine.services import transactions as tx
from . import legacy
from . import table
from .table import READ, WRITE, Cmd, Field, Result, frozen

DOMAIN = "compat"
SOURCE = "synox"
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
KIND_LABELS = legacy.KIND_LABELS
KIND_WORDS = {"expense": "expense", "income": "income", "transfer": "transfer", "支出": "expense", "花費": "expense",
              "收入": "income", "轉帳": "transfer"}
UNSORTED = legacy.UNSORTED
YES = ("yes", "true", "1", "是", "y")


# ── 金額與小工具 ────────────────────────────────────────────────────────────────

def unit_of(conn) -> str:
    """回覆裡金額後面的字：新台幣是「元」，其他幣別用代號（USD、JPY…）。"""
    code = str(settings.value(conn, "currency.code") or "TWD").upper()
    return "元" if code == "TWD" else code


def show(conn, amount) -> str:
    """舊指令回覆裡的數字：共用的 table.show，整數金額不印小數。"""
    return table.show(conn, amount or 0, trim=True)


def money(conn, amount) -> str:
    return f"{show(conn, amount)} {unit_of(conn)}"


def major(conn, amount):
    """最小單位 → 數字（面板用；小數位數 0 時是整數）。"""
    d = table.decimals(conn)
    if d == 0:
        return int(amount or 0)
    return float(Decimal(int(amount or 0)) / (10 ** d))


def check_month(value: str):
    return None if MONTH_RE.match(value.strip()) else "要像 2030-01"


def check_kind(value: str):
    return None if value.strip().lower() in KIND_WORDS else "只能是 支出／收入／轉帳"


def _writing(book, command: str):
    return db.write(book.conn, actor=book.actor, source=SOURCE, command=f"money {command}")


def _wrote(book, batch_id, **data) -> dict:
    return {"batch_id": batch_id, "write_seq": db.write_seq(book.conn), **data}


def _batch(w):
    """這次真的有改到東西才給 batch_id（沒改到的那一批不會留下）。"""
    return w.batch_id if w.changes else None


def _period(conn, key: str):
    key = (key or "").strip()
    if key and check_month(key):
        raise kit.AppError("月份要像 2030-01", code="invalid", status=400)
    return periods.get(conn, key or None)


def _span(period) -> str:
    """期別的日期範圍：每期從 1 號開始就不另外寫。"""
    if period.start.day == 1:
        return ""
    return f"（{period.start.month}/{period.start.day:02d}–{period.end.month}/{period.end.day:02d}）"


# ── month：這一期花了多少 ─────────────────────────────────────────────────────────

def month_view(conn, key: str = "") -> dict:
    """一期的數字（最小單位）：支出照類別、收入、轉帳、生活費預算與已花、有上限的類別。"""
    period = _period(conn, key)
    rows = stats.range_rows(conn, period.start, period.end)
    sums = {k: 0 for k in legacy.KINDS}
    by = {k: {} for k in legacy.KINDS}
    for r in rows:
        if r["kind"] == "expense" and r["financed"]:
            continue                                         # 分期買的：錢是每期還款時才算
        sums[r["kind"]] += r["amount"]
        name = r["category"] or UNSORTED
        by[r["kind"]][name] = by[r["kind"]].get(name, 0) + r["amount"]
    living_spent = sum(r["amount"] for r in rows if stats.is_living(r))
    plan = plans.get(conn, period.key)
    budget = int(plan["living_budget"] or 0) or None
    caps = []
    spent = by["expense"]
    for c in refdata.categories(conn):
        if c["kind"] != "expense" or not c.get("default_budget") or c.get("cap_mode") == "each":
            continue
        cap = int(c["default_budget"]) * (period.days if c.get("cap_mode") == "day" else 1)
        used = spent.get(c["name"], 0)
        caps.append({"category": c["name"], "cap": cap, "used": used, "left": cap - used, "over": used > cap})
    return {"month": period.key, "start": period.start.isoformat(), "end": period.end.isoformat(),
            "expense": sums["expense"], "income": sums["income"], "transfer": sums["transfer"],
            "by_category": {k: [{"category": n, "amount": a} for n, a in sorted(v.items(), key=lambda kv: -kv[1])]
                            for k, v in by.items()},
            "budget": budget, "living_spent": living_spent, "left": (budget - living_spent) if budget else None,
            "caps": caps, "span": _span(period)}


def cap_lines(conn, caps, only: str = "") -> list:
    out = []
    for c in caps:
        if only and c["category"] != only:
            continue
        if c["over"]:
            out.append(f"⚠️ {c['category']} 超過這一期的上限 {money(conn, c['cap'])}（超過 {money(conn, c['used'] - c['cap'])}）")
        else:
            out.append(f"{c['category']} 上限 {money(conn, c['cap'])}，還剩 {money(conn, c['left'])}")
    return out


def run_month(book, values):
    conn = book.conn
    v = month_view(conn, values.get("month", ""))
    lines = [f"💰 {v['month']}{v['span']} 支出共 {money(conn, v['expense'])}"]
    lines += [f"  {c['category']}：{money(conn, c['amount'])}" for c in v["by_category"]["expense"]]
    for kind, title in (("income", "💵 收入"), ("transfer", "🔁 轉帳（不算花費）")):
        rows = v["by_category"][kind]
        if rows:
            lines.append(f"{title} 共 {money(conn, v[kind])}：" + "、".join(f"{c['category']} {show(conn, c['amount'])}"
                                                                         for c in rows))
    if v["budget"]:
        left = v["left"]
        lines.append(f"生活費預算 {money(conn, v['budget'])}（生活費花了 {money(conn, v['living_spent'])}），"
                     + (f"還剩 {money(conn, left)}" if left >= 0 else f"⚠️ 超過 {money(conn, -left)}"))
    else:
        lines.append("（還沒設預算：/money_budget <金額>）")
    lines += cap_lines(conn, v["caps"])
    return Result(data=v, text="\n".join(lines))


# ── budget：每月預算＝生活費那一格 ───────────────────────────────────────────────────

def run_budget(book, values):
    conn = book.conn
    amount = table.to_minor(book, values.get("amount", ""), zero=True)
    with _writing(book, "budget") as w:
        plan = plans.set_plan(w, None, buckets={"living": amount})
        batch = _batch(w)
    spent = month_view(conn)["living_spent"]
    if not amount:
        text = "💰 預算拿掉了（生活費改回跟著各大類的預算加起來）。"
    else:
        text = f"💰 每期的生活費預算設為 {money(conn, amount)}（這一期生活費目前 {money(conn, spent)}）。"
    return Result(data=_wrote(book, batch, budget=amount or None, period=plan["period"], spent=spent), text=text)


# ── cap：單類上限＝細項的預設預算 ─────────────────────────────────────────────────────

def _find(conn, name: str):
    try:
        return refdata.find_category(conn, name)
    except LedgerError:
        return None


def run_cap(book, values):
    conn = book.conn
    name = values.get("category", "").strip()
    if not name:
        raise kit.AppError("要寫類別", code="invalid", status=400)
    amount = table.to_minor(book, values.get("amount", ""), zero=True)
    cat = _find(conn, name)
    if cat is not None and cat["kind"] != "expense":
        raise kit.AppError(f"「{cat['name']}」是{KIND_LABELS[cat['kind']]}類，上限只用在支出類別。", code="invalid", status=400)
    with _writing(book, "cap") as w:
        if cat is None:
            cat = legacy.ensure_category(w, name, "expense")       # 沒有這個類別：建一個支出類別（放「其他」）
        cat = refdata.update_category(w, cat["id"], default_budget=amount or None, cap_basis="period")
        batch = _batch(w)
    if not amount:
        text = f"💰 「{cat['name']}」的上限拿掉了。"
    else:
        lines = [f"💰 「{cat['name']}」每期上限設為 {money(conn, amount)}（只提醒、不擋）。"]
        lines += cap_lines(conn, month_view(conn)["caps"], only=cat["name"])
        text = "\n".join(lines)
    return Result(data=_wrote(book, batch, category=cat["name"], cap=amount or None), text=text)


# ── cats／cat_rm ─────────────────────────────────────────────────────────────────

def _cat_line(c) -> str:
    return c["name"] + ("" if c["kind"] == "expense" else f"（{KIND_LABELS[c['kind']]}）")


def run_cats(book, values):
    conn = book.conn
    name = values.get("name", "").strip()
    cats = refdata.categories(conn)
    if name:
        c = _find(conn, name)
        if c is None:
            raise kit.AppError(f"沒有「{name}」這個類別（/money_cats 看全部）。", code="not_found", status=404)
        cap = c.get("default_budget")
        basis = {"day": "每天", "each": "每次"}.get(c.get("cap_mode") or "", "每期")
        lines = [f"💰 類別「{c['name']}」",
                 f"種類：{KIND_LABELS[c['kind']]}" + ("（不算花費）" if c["kind"] == "transfer" else ""),
                 f"大類：{c['group_name']}",
                 "關鍵字：" + ("、".join(c["keywords"]) if c["keywords"] else "（沒有）"),
                 f"{basis}上限：{money(conn, cap)}" if cap else "上限：（沒設；/money_cap 類別 金額）"]
        if refdata.is_system_category(c):
            lines.append("（系統會用到的類別：可以改名字、關鍵字，不能停用）")
        return Result(data={"category": {k: c[k] for k in ("name", "group_name", "kind", "keywords", "default_budget")}},
                      text="\n".join(lines))
    groups = {}
    for c in cats:
        groups.setdefault(c["group_name"], []).append(_cat_line(c))
    lines = [f"💰 你的類別（{len(cats)} 個）"] + [f"{g}：{'、'.join(items)}" for g, items in groups.items()]
    lines.append("看一個：/money_cats 名字；加：/money_cat_add；停用：/money_cat_rm")
    return Result(data={"groups": groups, "count": len(cats)}, text="\n".join(lines))


def run_cat_rm(book, values):
    conn = book.conn
    name = values.get("name", "").strip()
    c = _find(conn, name)
    if c is None:
        raise kit.AppError(f"沒有「{name}」這個類別（/money_cats 看全部）。", code="not_found", status=404)
    with _writing(book, "cat_rm") as w:
        refdata.update_category(w, c["id"], active=0)
        batch = _batch(w)
    text = f"🗑 停用了類別「{c['name']}」。已經記的帳不動（帳上照樣寫「{c['name']}」）。"
    return Result(data=_wrote(book, batch, category=c["name"]), text=text)


# ── template：起始類別與選配功能 ──────────────────────────────────────────────────────

# (代號, 名字, 說明, 起始檔裡的哪幾個大類, 要打開的選配功能)。0.3.1 的五個名字照收。
TEMPLATES = (
    ("basic", "基本", "日常支出：食衣行育樂、醫療、其他", ("食", "衣", "行", "育", "樂", "醫療", "其他"), ()),
    ("bills", "帳單", "每月的固定帳單：房租、水電瓦斯、管理費、手機網路", ("住",), ()),
    ("saving", "存錢與投資", "存錢、投資、繳卡費、帳戶互轉（轉帳，不算花費）", ("存錢", "轉帳"), ("invest",)),
    ("giving", "捐贈", "捐款（打開「捐贈」那一格）", (), ("giving",)),
    ("income", "收入", "薪資、兼職、投資收益、退款回饋", ("收入",), ()),
)


def _features(conn) -> list:
    try:
        return starter.features(conn)
    except LedgerError:
        return []


def _feature_title(f) -> str:
    return (f.get("summary") or f["name"]).split("（")[0]


def find_template(conn, name: str):
    """名字或代號 → (代號, 名字, 說明, 大類, 選配功能)。選配功能（還債、薪資扣繳、資金操盤…）的名字也收。"""
    low = name.strip().lower()
    for t in TEMPLATES:
        if low in (t[0], t[1].lower()):
            return t
    for f in _features(conn):
        if low in (f["name"].lower(), _feature_title(f).lower()):
            return (f["name"], _feature_title(f), f.get("summary") or "", (), (f["name"],))
    return None


def _seed_groups() -> dict:
    from ..engine import seed
    data = seed.config()
    return {g["name"]: g for g in data.get("groups", [])}


def apply_template(w, t) -> dict:
    """起始檔裡那幾個大類的細項：沒有的補上（停用的打開）、已經有的不動；再打開選配功能。重跑不會多。"""
    conn = w.conn
    added, kept = [], []
    seed_groups = _seed_groups()
    for gname in t[3]:
        g = seed_groups.get(gname)
        if g is None:
            continue
        try:
            group = refdata.group_by_name(conn, gname)
        except LedgerError:
            group = refdata.add_group(w, name=gname, bucket=g.get("bucket") or None, icon=g.get("icon") or None)
        for c in g.get("categories", []):
            row = conn.execute("SELECT active FROM categories WHERE name = ?", (c["name"],)).fetchone()
            if row is not None and row["active"]:
                kept.append(c["name"])
                continue
            refdata.add_category(w, name=c["name"], group=group["name"], kind=c.get("kind", "expense"),
                                 keywords=c.get("keywords") or None, transfer_purpose=c.get("transfer_purpose"),
                                 meal_slot=c.get("meal_slot"), icon=c.get("icon") or None,
                                 system_key=c.get("system_key") or None)
            added.append(c["name"])
    turned_on = []
    for name in t[4]:
        out = starter.enable(w, name)
        added += out["added_categories"]
        if out["bucket_turned_on"]:
            turned_on.append(out["bucket"])
    return {"template": t[1], "added": added, "kept": kept, "turned_on": turned_on}


def run_template(book, values):
    conn = book.conn
    name = values.get("name", "").strip()
    if not name:
        lines = ["📋 起始類別與選配功能（載入後可以自己改、停用，也可以另外加）："]
        for key, title, about, _groups, _features_ in TEMPLATES:
            lines.append(f"・{title}（{key}）：{about}")
        for f in _features(conn):
            if not any(f["name"] in t[4] for t in TEMPLATES):
                lines.append(f"・{_feature_title(f)}（{f['name']}）：{f.get('summary') or ''}"
                             + ("（已經開了）" if f.get("enabled") else ""))
        lines.append("載入：/money_template 基本（載兩次不會重複，也不會蓋掉你改過的）")
        return Result(data={"templates": [t[0] for t in TEMPLATES], "features": [f["name"] for f in _features(conn)]},
                      text="\n".join(lines))
    t = find_template(conn, name)
    if t is None:
        raise kit.AppError(f"沒有「{name}」這個範本（/money_template 看有哪些）。", code="not_found", status=404)
    with _writing(book, "template") as w:
        out = apply_template(w, t)
        batch = _batch(w)
    added = out["added"]
    lines = [f"📋 載入「{out['template']}」：新增 {len(added)} 個類別" + (f"（{'、'.join(added)}）" if added else "")]
    if out["kept"]:
        lines.append(f"已經有的 {len(out['kept'])} 個沒動：{'、'.join(out['kept'])}")
    if out["turned_on"]:
        lines.append("打開了：" + "、".join(plans.bucket_names(conn).get(b, b) for b in out["turned_on"]))
    lines.append("看全部：/money_cats")
    return Result(data=_wrote(book, batch, **out), text="\n".join(lines))


# ── CSV：匯出（畫面）與匯入（指令）────────────────────────────────────────────────────

CSV_HEADER = ("日期", "時間", "類型", "金額", "類別", "帳戶", "轉入帳戶", "說明", "商家", "備註", "編號")
FILTER_LABELS = {"month": "月份（YYYY-MM）", "kind": "種類", "category": "類別", "keyword": "關鍵字"}


def check_filters(params: dict) -> dict:
    out = {}
    for k, label in FILTER_LABELS.items():
        v = str(params.get(k) or "").strip()
        if not v:
            continue
        if k == "month":
            problem = check_month(v)
        elif k == "kind":
            problem = check_kind(v)
        else:
            problem = None if len(v) <= 40 else "太長了"
        if problem:
            raise kit.AppError(f"{label}{problem}", code="invalid", status=400)
        out[k] = v
    return out


def export_rows(conn, filters: dict) -> list:
    """照篩選的帳（舊到新）→ CSV 的每一列；金額是帳本的最小單位整數（匯回來一樣）。"""
    start = end = None
    if filters.get("month"):
        start, end = csvio.month_range(filters["month"])
    kind = KIND_WORDS.get(filters["kind"].lower()) if filters.get("kind") else None
    cat = filters.get("category", "").lower()
    kw = filters.get("keyword", "").lower()
    out, cursor = [], None
    while True:
        page = tx.query(conn, start=start, end=end, kind=kind, limit=500, cursor=cursor, order="asc")
        for t in page["items"]:
            if cat and (t["category"] or UNSORTED).lower() != cat:
                continue
            if kw and kw not in " ".join(str(t[k] or "") for k in ("category", "desc", "merchant", "note", "account")).lower():
                continue
            out.append([t["date"], t["time"] or "", KIND_LABELS[t["kind"]], int(t["amount"]), t["category"] or "",
                        t["account"] or "", t["to_account"] or "", t["desc"] or "", t["merchant"] or "", t["note"] or "",
                        t["id"]])
        cursor = page["next_cursor"]
        if not cursor:
            return out


def export_entries_csv(book, params):
    filters = check_filters(params)
    rows = export_rows(book.conn, filters)
    name = f"money-{filters.get('month') or 'all'}.csv"
    return kit.Download(name, "text/csv; charset=utf-8", kit.csv_bytes(list(CSV_HEADER), rows))


def run_import_csv(book, values):
    conn = book.conn
    name = values.get("file", "").strip()
    if not name:
        raise kit.AppError("要寫檔名（放在自己的檔案資料夾裡）", code="invalid", status=400)
    try:
        path = book.path(name)
    except ValueError:
        raise kit.AppError("檔案要放在自己的檔案資料夾裡（寫檔名就好）", code="invalid", status=400) from None
    dry = values.get("dry_run", "").strip().lower() in YES
    out = csvio.import_csv(conn, path, actor=book.actor, source=SOURCE, dry_run=dry)
    s = out["summary"]
    head = "🧪 檢查好了（沒有寫進去）" if dry else "📥 匯入好了"
    lines = [f"{head}：{out['rows']} 列，新記 {s['new']} 筆"
             + (f"、已經記過 {s['replayed']} 筆（沒有重複記）" if s["replayed"] else "")]
    totals = [f"{KIND_LABELS[k]} {s[k]} 筆" for k in legacy.KINDS if s.get(k)]
    if totals:
        lines.append("、".join(totals))
    if s.get("possible_duplicate"):
        lines.append(f"其中 {s['possible_duplicate']} 筆可能跟原本的重複（總覽的「待解決」看得到）")
    warnings = [str(w.get("message") if isinstance(w, dict) else w) for w in out["warnings"][:5]]
    data = {"rows": out["rows"], "summary": s, "batch_ids": out["batch_ids"], "dry_run": dry,
            "write_seq": db.write_seq(conn)}
    return Result(data=data, text="\n".join(lines), warnings=warnings)


# ── 工具頁的小面板（pack.json web.api：GET /summary）──────────────────────────────────

def summary(book) -> dict:
    """panel.html 用：這一期花了多少（只算支出）、收入、轉帳、生活費預算、各類別、上限。只回這個人自己的。
    金額是給人看的數字（已經照小數位數換好）。"""
    conn = book.conn
    v = month_view(conn)
    return {"month": v["month"], "total": major(conn, v["expense"]), "income": major(conn, v["income"]),
            "transfer": major(conn, v["transfer"]), "budget": major(conn, v["budget"]) if v["budget"] else None,
            "spent": major(conn, v["living_spent"]), "left": major(conn, v["left"]) if v["budget"] else None,
            "by_category": [{"category": c["category"], "amount": major(conn, c["amount"])}
                            for c in v["by_category"]["expense"]],
            "caps": [{"category": c["category"], "cap": major(conn, c["cap"])} for c in v["caps"]],
            "unit": unit_of(conn), "decimals": table.decimals(conn)}


def on_open(pack, uid, book):
    """每次開好帳本叫一次：帳本還沒寫過東西、而且這個人有 0.3.1 的舊資料 → 轉進來（legacy.py）。"""
    return legacy.on_open(pack, uid, book)


COMMANDS = (
    Cmd("month", "這一期花了多少（分類別；可以給月份，例如 2030-01）", READ,
        [Field("month", "月份（YYYY-MM）", check=check_month)], run_month,
        ai={"scenario": "使用者問某個月各類別花了多少時"}, positional=1),
    Cmd("budget", "設每月預算（＝生活費那一格；0＝拿掉）", WRITE, [Field("amount", "金額", required=True)], run_budget),
    Cmd("cap", "設某個類別每期的上限（0＝拿掉；只提醒、不擋）", WRITE, [
        Field("category", "類別", required=True),
        Field("amount", "金額", required=True)], run_cap),
    Cmd("cats", "列出自己的類別（給名字看那一個的關鍵字）", READ, [Field("name", "類別名字")], run_cats, positional=1),
    Cmd("cat_rm", "停用一個類別（已經記的帳不動）", WRITE, [Field("name", "類別名字", required=True)], run_cat_rm),
    Cmd("template", "起始類別與選配功能：不給名字＝列出有哪些；給名字＝載入（載兩次不會多）", WRITE, [
        Field("name", "名字（例如 基本、帳單、存錢與投資、捐贈、收入）")], run_template, positional=1),
    Cmd("import_csv", "從自己檔案資料夾裡的 CSV 匯入（同一個檔匯兩次不會多）", WRITE, [
        Field("file", "檔名（自己檔案資料夾裡的）", required=True),
        Field("dry_run", "只檢查不寫（yes／no）")], run_import_csv),
)

READS = frozen({})
EXPORTS = frozen({"entries_csv": export_entries_csv})
WEB_ROUTES = frozen({"summary": summary})
