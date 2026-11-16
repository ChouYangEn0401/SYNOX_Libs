"""設定與第一次使用（W5）：大類、細項、帳戶、帳本設定、選配功能、第一次使用、開新帳本、整本匯出。

欄位名是契約（app/js/routes.js 照這些名字送值）：可以加，不要改名或拿掉。
- group、cat_add、account：沒給 id＝新增；給 id＝改那一個（SYNOX 一個畫面最多 40 個動作，新增與修改共用一個）。
  id 也可以直接寫名字（人打字比較方便）。
- cat_add 沿用 0.3.1 的名字：人打字時，名字已經有了就是「改那一個」（換大類、加關鍵字），跟舊版一樣；
  畫面送的一律照「沒給 id＝新增」（同名的會被擋下來，不會悄悄改掉別的類別）。
- 清單（關鍵字、別名）一律是逗號字串；清掉用「-」。是／否收 yes／no、true／false、1／0、是／否。
- 金額：畫面送來的已經是最小單位的整數；人打字（指令）照帳本的小數位數換（`99.5` → 9950，當小數位數是 2）。

這個檔另外放 plan.py 也用的小工具（欄位怎麼讀、金額怎麼換、寫入怎麼包），以及給總覽用的 `needs_onboarding(book)`
（還沒做第一次使用 → 總覽回 {"onboarding": true}）。
"""

from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from synox.packs import kit

from ..engine import db
from ..engine.services import book as book_svc
from ..engine.services import periods, plans, recurring, refdata, settings, starter, stats, views
from .table import CLEAR, DANGER, WRITE, Cmd, Field, Result, frozen, to_minor, typed
from .table import decimals as book_decimals

DOMAIN = "setup"

SETUP_DONE = "setup.done"                  # 帳本設定：第一次使用做完了沒（True＝做完）
LEGACY = "legacy.v03"                      # 帳本設定：舊版（0.3.x）的資料轉進來過（compat／legacy 寫的）
CONFIRM = "開新帳本"                        # 開新帳本要打的字（畫面與指令一樣）
MAX_EXPORT = 20 * 1024 * 1024               # SYNOX 的匯出上限

YES = ("yes", "y", "true", "1", "是", "要", "開", "on")
NO = ("no", "n", "false", "0", "否", "不要", "關", "off")

ACCOUNT_KINDS = ("cash", "bank", "credit_card", "securities", "e_wallet")       # 這裡能建的帳戶種類
KIND_WORDS = (("現金", "cash"), ("銀行", "bank"), ("信用卡", "credit_card"), ("證券", "securities"),
              ("電子錢包", "e_wallet"))
CAT_KINDS = (("expense", "expense"), ("income", "income"), ("transfer", "transfer"), ("支出", "expense"),
             ("花錢", "expense"), ("花費", "expense"), ("收入", "income"), ("轉帳", "transfer"), ("轉存", "transfer"))

# 第一次使用能選的幣別（代號、符號、小數位數、名字）；其他的到設定頁自己填代號、符號、小數位數
CURRENCIES = (("TWD", "NT$", 0, "新台幣"), ("USD", "$", 2, "美元"), ("JPY", "¥", 0, "日圓"), ("HKD", "HK$", 2, "港幣"),
              ("CNY", "¥", 2, "人民幣"), ("EUR", "€", 2, "歐元"), ("GBP", "£", 2, "英鎊"), ("KRW", "₩", 0, "韓元"),
              ("SGD", "S$", 2, "新加坡幣"), ("AUD", "A$", 2, "澳幣"), ("CAD", "C$", 2, "加幣"))

# 設定頁列出來的帳本設定（鍵、給人看的名字）
SETTING_ROWS = (("currency.code", "幣別代號"), ("currency.symbol", "幣別符號"), ("currency.decimals", "小數位數"),
                ("period_start_day", "每期從幾號開始"), ("tz_offset", "時差（小時；空白＝跟著這台電腦）"),
                ("default_account", "預設付款帳戶"), ("quick.lunch_weekdays", "午餐提醒在星期幾"),
                ("notify.weekday", "每週報告在星期幾"), ("plan.flex_tolerance", "會浮動的格子差多少算做完"))
# 人打字時可以用的中文名字
SETTING_WORDS = (("幣別", "currency.code"), ("幣別代號", "currency.code"), ("幣別符號", "currency.symbol"),
                 ("符號", "currency.symbol"), ("小數位數", "currency.decimals"), ("小數", "currency.decimals"),
                 ("起算日", "period_start_day"), ("每期起算日", "period_start_day"), ("時差", "tz_offset"),
                 ("時區", "tz_offset"), ("預設帳戶", "default_account"), ("預設付款帳戶", "default_account"),
                 ("午餐提醒", "quick.lunch_weekdays"), ("週報", "notify.weekday"), ("每週報告", "notify.weekday"),
                 ("浮動容許", "plan.flex_tolerance"), ("容許比例", "plan.flex_tolerance"))
WEEKDAY_CHARS = (("一", "0"), ("二", "1"), ("三", "2"), ("四", "3"), ("五", "4"), ("六", "5"), ("日", "6"), ("天", "6"))
WEEKDAYS = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")


# ── 欄位怎麼讀（plan.py 也用）─────────────────────────────────────────────────────────

def refuse(message: str, status: int = 400) -> kit.AppError:
    return kit.AppError(message, code="invalid", status=status)


def text(values: Dict[str, str], key: str) -> Optional[str]:
    """欄位的字（去掉前後空白）；沒給或空白回 None。「-」原樣回（清掉）。"""
    raw = values.get(key)
    if raw is None:
        return None
    raw = str(raw).strip()
    return raw or None


def is_clear(value: Optional[str]) -> bool:
    return value == CLEAR


def yes_no(value: Optional[str], label: str) -> Optional[bool]:
    if value is None:
        return None
    v = value.strip().lower()
    if v in YES:
        return True
    if v in NO:
        return False
    raise refuse(f"{label}要寫 yes 或 no")


def items(value: Optional[str]) -> List[str]:
    """逗號清單（也收、與，）。"""
    if value is None or is_clear(value):
        return []
    for sep in ("、", "，"):
        value = value.replace(sep, ",")
    return [p.strip() for p in value.split(",") if p.strip()]


def whole(value: Optional[str], label: str, low: Optional[int] = None, high: Optional[int] = None) -> Optional[int]:
    if value is None:
        return None
    try:
        n = int(value.strip())
    except ValueError:
        raise refuse(f"{label}要是整數") from None
    if (low is not None and n < low) or (high is not None and n > high):
        raise refuse(f"{label}要在 {low}～{high} 之間")
    return n


@contextmanager
def write(book, command: str):
    """一次寫入＝一個批次（可以整批復原）；改動紀錄的「誰」是 book.actor。"""
    with db.write(book.conn, actor=book.actor, source=book.actor, command=command) as w:
        yield w


def out(book, w, **data) -> dict:
    """寫入的回傳：batch_id（沒改到東西是 None，畫面就不給復原）＋寫入序號。"""
    return {**data, "batch_id": w.batch_id if w.changes else None, "write_seq": db.write_seq(book.conn)}


def pick(word: str, table) -> Optional[str]:
    w = word.strip().lower()
    for k, v in table:
        if w == k.lower():
            return v
    return None


# ── 第一次使用 ─────────────────────────────────────────────────────────────────────

def needs_onboarding(book) -> bool:
    """要不要跑第一次使用的引導（全工具只有這一個判斷；總覽 home 與 features 讀都叫它）：
    沒有 setup.done，而且這本帳還沒有資料——舊版（0.3.x）的資料轉進來過（legacy.v03）或已經記過帳（例如先在聊天打了
    /money 120 才開畫面）都算有資料，就不引導，也不再問起始類別。"""
    return not settings.get(book.conn, SETUP_DONE) and not has_data(book.conn)


def _currency(raw: str):
    w = raw.strip()
    for code, symbol, places, name in CURRENCIES:
        if w.upper() == code or w == name:
            return code, symbol, places, name
    raise refuse(f"沒有「{raw}」這個幣別可以直接選；別的幣別到設定頁自己填代號、符號、小數位數",
                 status=400)


def _currency_now(conn) -> dict:
    code = settings.value(conn, "currency.code")
    name = next((n for c, _s, _p, n in CURRENCIES if c == code), code)
    return {"code": code, "symbol": settings.value(conn, "currency.symbol"), "decimals": book_decimals(conn), "name": name}


def has_data(conn) -> bool:
    """這本帳已經有資料了嗎：記過帳，或是舊版（0.3.x）的資料轉進來過（compat 寫的 legacy.v03）。"""
    return starter.has_transactions(conn) or settings.get(conn, LEGACY) is not None


def _guard_decimals(conn, places: int) -> None:
    if places != book_decimals(conn) and has_data(conn):
        raise refuse("這本帳已經有帳了，改小數位數會讓每一筆金額差 10 倍以上；要換幣別，請開一本新帳本", status=409)


def _put_currency(w, conn, cur) -> None:
    code, symbol, places, _name = cur
    _guard_decimals(conn, places)
    settings.put(w, "currency.code", code)
    settings.put(w, "currency.symbol", symbol)
    settings.put(w, "currency.decimals", places)


def _feature(conn, raw: str) -> str:
    """選配功能：代號（giving…）或名字（捐贈…）都認。"""
    w = raw.strip()
    for f in starter.features(conn):
        label = (f["summary"] or "").split("（")[0]
        if w.lower() == f["name"] or w == label:
            return f["name"]
    return w                                             # 不認得的交給引擎（會回「沒有這個功能」＋建議）


def run_init(book, values):
    conn = book.conn
    seed_word = text(values, "seed")
    cur = _currency(text(values, "currency")) if text(values, "currency") else None
    start = whole(text(values, "start_day"), "每期從幾號開始", 1, 31)
    wanted = [_feature(conn, n) for n in items(text(values, "features"))]
    seeded = None
    if seed_word is not None:
        if settings.get(conn, SETUP_DONE) or has_data(conn):
            raise refuse("起始類別只在開新帳本、還沒有資料時選；要換，先開一本新帳本（現在這本會先封存）", status=409)
        seeded = starter.reseed(conn, seed_word)["seed"]          # 已經有帳就丟（起始類別只在開帳本時決定）
    if cur is not None:
        _guard_decimals(conn, cur[2])
    turned_on = []
    with write(book, "init") as w:
        if cur is not None:
            _put_currency(w, conn, cur)
        if start is not None:
            settings.put(w, "period_start_day", start)
        for name in wanted:
            if starter.enable(w, name)["changed"]:
                turned_on.append(name)
        settings.put(w, SETUP_DONE, True)
    now = _currency_now(conn)
    day = periods.start_day(conn)
    labels = {key: label for key, label in starter.SEED_NAMES}
    on = [f for f in starter.features(conn) if f["enabled"]]
    lines = [f"好了：幣別 {now['name']}（{now['symbol']}，{now['decimals']} 位小數）、每期從 {day} 號開始"
             + (f"、起始類別「{labels.get(seeded, seeded)}」" if seeded else "")]
    lines.append("選配功能開著的：" + ("、".join(f["summary"].split("（")[0] for f in on) or "沒有"))
    data = out(book, w, done=True, currency=now, start_day=day, seed=seeded, features=starter.features(conn),
               turned_on=turned_on)
    return Result(data=data, text="\n".join(lines))


# ── 讀 ─────────────────────────────────────────────────────────────────────────

def _book_view(conn) -> dict:
    """帳本的大概（不給資料夾位置：隔離規則 9）。"""
    i = book_svc.info(conn)
    return {"transactions": i["transactions"], "first_date": i["first_date"], "last_date": i["last_date"],
            "schema": i["schema"], "archives": i["archives"], "encrypted": False,
            "backups_kept": db.BACKUP_KEEP_DAYS}


def _settings_view(conn) -> list:
    rows = []
    for key, label in SETTING_ROWS:
        value = settings.value(conn, key)
        if key == "plan.flex_tolerance" and value is None:
            value = plans.FLEX_TOLERANCE
        if key == "notify.weekday" and value is None:
            value = 4                                         # 週報預設週五
        rows.append({"key": key, "label": label, "value": value})
    return rows


def read_setup(book, params):
    conn = book.conn
    nav = views.period_nav(conn, text(params, "period"))
    plan = plans.get(conn, nav["period"]["key"], with_moves=False)   # 規劃頁填的是原本的計畫；挪用在總覽
    living = [g["name"] for g in refdata.groups(conn) if g["is_living"]]
    accounts = refdata.accounts(conn)
    names = {a["id"]: a["name"] for a in accounts}
    return {**nav, "plan": plan,
            "buckets": plans.buckets(conn),
            "group_names": plans.group_names(conn),
            "flex_tolerance": plans.flex_tolerance(conn),
            "bucket_categories": plans.bucket_categories(conn),
            "claimable": [{"name": c["name"], "group": c["group_name"], "kind": c["kind"], "bucket": c["bucket_id"],
                           "bucket_name": c["bucket_name"], "living": c["is_living"],
                           "pinned": c.get("bucket_override")}
                          for c in refdata.categories(conn) if c["kind"] != "income"],
            "living_groups": [{"name": g, "budget": int(plan["group_budgets"].get(g) or 0)} for g in living],
            "caps": stats.caps(conn, nav["period"]["key"]),
            "catalog": refdata.catalog(conn),
            "accounts": [{**{k: a[k] for k in ("id", "name", "kind", "aliases", "floor_amount", "target_amount",
                                               "card_closing_day", "card_due_day", "asset_class")},
                          "pay_from": names.get(a["pay_from_account_id"]), "bucket": a.get("bucket_id")}
                         for a in accounts],
            "account_kinds": list(ACCOUNT_KINDS),
            "recurring": recurring.status(conn, nav["period"]["key"]),
            "book": _book_view(conn),
            "settings": _settings_view(conn),
            "currency": _currency_now(conn),
            "features": starter.features(conn),
            "onboarding": needs_onboarding(book)}


def read_features(book, params):
    conn = book.conn
    return {"features": starter.features(conn),
            "onboarding": needs_onboarding(book),
            "can_seed": needs_onboarding(book) and not has_data(conn),
            "seeds": [{"id": "generic", "name": "通用", "summary": "食、衣、住、行、育、樂、醫療、其他＋收入＋轉帳"},
                      {"id": "blank", "name": "空白", "summary": "只留系統記帳會用到的（三餐、房租水電、繳卡費、借出…），其他自己加"}],
            "currencies": [{"code": c, "symbol": s, "decimals": p, "name": n} for c, s, p, n in CURRENCIES],
            "currency": _currency_now(conn),
            "start_day": periods.start_day(conn)}


def read_book(book, params):
    return _book_view(book.conn)


def export_book(book, params):
    """整本帳：SYNOX 的匯出只收 CSV，所以每一列寫成「表名, 那一列的 JSON」（每一張表、每一列都在）。"""
    rows = book_svc.export_rows(book.conn)
    data = kit.csv_bytes(["table", "data"], rows)
    if len(data) > MAX_EXPORT:
        raise refuse("這本帳太大，畫面下載不了（上限 20 MB）", status=409)
    return kit.Download(filename=f"money-book-{book.today:%Y%m%d}.csv", content_type="text/csv; charset=utf-8",
                        data=data)


# ── 大類 ─────────────────────────────────────────────────────────────────────────

def _group(conn, raw: str) -> dict:
    if raw.isdigit():
        return refdata.group_by_id(conn, int(raw))
    return refdata.group_by_name(conn, raw, include_inactive=True)


def run_group(book, values):
    conn = book.conn
    gid = text(values, "id")
    name, bucket, icon = text(values, "name"), text(values, "bucket"), text(values, "icon")
    active = yes_no(text(values, "active"), "開著")
    if gid is None:
        if not name or is_clear(name):
            raise refuse("大類要有名字")
        with write(book, "group add") as w:
            g = refdata.add_group(w, name=name, bucket=None if is_clear(bucket) else bucket,
                                  icon=None if is_clear(icon) else icon)
        where = g["bucket_name"] or "收入與轉帳"
        return Result(data=out(book, w, group=g), text=f"加了大類「{g['name']}」（在{where}底下）")
    target = _group(conn, gid)
    fields: Dict[str, Any] = {}
    if name is not None:
        if is_clear(name):
            raise refuse("大類名稱不能空白")
        fields["name"] = name
    if bucket is not None and not is_clear(bucket):
        fields["bucket"] = bucket
    if icon is not None:
        fields["icon"] = "" if is_clear(icon) else icon
    if active is not None:
        fields["active"] = int(active)
    with write(book, "group edit") as w:
        g = refdata.update_group(w, target["id"], **fields)
    return Result(data=out(book, w, group=g),
                  text=f"改好了：{g['name']}（{g['bucket_name'] or '收入與轉帳'}）" + ("" if g["active"] else "、已停用"))


# ── 細項 ─────────────────────────────────────────────────────────────────────────

def _category(conn, raw: str) -> dict:
    if raw.isdigit():
        return refdata.category_by_id(conn, int(raw))
    for c in refdata.categories(conn, include_inactive=True):
        if c["name"] == raw:
            return c
    found = refdata.find_category(conn, raw)
    if found is None:
        raise refuse(f"找不到類別「{raw}」", status=404)
    return found


def _cat_kind(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    kind = pick(raw, CAT_KINDS)
    if kind is None:
        raise refuse("種類只有：支出、收入、轉帳")
    return kind


def run_cat_add(book, values):
    conn = book.conn
    cid, name = text(values, "id"), text(values, "name")
    kind = _cat_kind(text(values, "kind"))
    target = None
    merge = False
    if cid is not None:
        target = _category(conn, cid)
    elif name and typed(book):                        # 0.3.1 的寫法：名字已經有了＝改那一個（關鍵字是加上去）
        target = next((c for c in refdata.categories(conn) if c["name"] == name), None)
        merge = target is not None
    if target is None:
        return _cat_new(book, values, name, kind)
    return _cat_edit(book, values, target, kind, merge)


def _cat_fields(book, values) -> Dict[str, Any]:
    out_: Dict[str, Any] = {}
    budget = text(values, "default_budget")
    if budget is not None:
        out_["default_budget"] = None if is_clear(budget) else to_minor(book, budget, "單項上限", zero=True)
    basis = text(values, "cap_basis")
    if basis is not None:
        out_["cap_basis"] = None if is_clear(basis) else basis
    icon = text(values, "icon")
    if icon is not None:
        out_["icon"] = "" if is_clear(icon) else icon
    return out_


def _cat_new(book, values, name, kind):
    group = text(values, "group")
    if not name or not group or is_clear(name) or is_clear(group):
        raise refuse("細項要有名字，也要說放在哪個大類")
    extra = _cat_fields(book, values)
    if extra.get("icon") == "":
        extra.pop("icon")
    bucket = text(values, "bucket")
    with write(book, "category add") as w:
        c = refdata.add_category(w, name=name, group=group, kind=kind or "expense",
                                 keywords=items(text(values, "keywords")), **extra)
        if bucket and not is_clear(bucket):
            c = plans.set_category_bucket(w, c["id"], bucket)
    return Result(data=out(book, w, category=c), text=f"新增類別：{c['group_name']}／{c['name']}（算在 {c['bucket_name'] or '收入與轉帳'}）")


def _cat_edit(book, values, target, kind, merge):
    conn = book.conn
    if kind is not None and kind != target["kind"]:
        if refdata.is_system_category(target):
            raise refuse(f"「{target['name']}」是記帳時系統會用到的類別，不能換種類（名字、圖示、關鍵字都可以改）")
        raise refuse("種類不能改：要換就加一個新的，把這個停掉")
    fields = _cat_fields(book, values)
    name = text(values, "name")
    if name is not None and not merge:
        if is_clear(name):
            raise refuse("類別名稱不能空白")
        fields["name"] = name
    group = text(values, "group")
    if group is not None and not is_clear(group):
        fields["group_id"] = refdata.group_by_name(conn, group)["id"]
    words = text(values, "keywords")
    if words is not None:
        fields["keywords"] = items(words) if not merge else \
            target["keywords"] + [k for k in items(words) if k not in target["keywords"]]
    active = yes_no(text(values, "active"), "開著")
    if active is not None:
        if not active and refdata.is_system_category(target):
            raise refuse(f"「{target['name']}」是記帳時系統會用到的類別，不能停用（名字、圖示、關鍵字、算在哪一格都可以改）")
        fields["active"] = int(active)
    bucket = text(values, "bucket")
    with write(book, "category edit") as w:
        c = refdata.update_category(w, target["id"], **fields)
        if bucket is not None:
            c = plans.set_category_bucket(w, target["id"], None if is_clear(bucket) else bucket)
    return Result(data=out(book, w, category=c),
                  text=f"改好了：{c['group_name']}／{c['name']}（算在 {c['bucket_name'] or '收入與轉帳'}）"
                  + ("" if c["active"] else "、已停用"))


# ── 帳戶 ─────────────────────────────────────────────────────────────────────────

def _account(conn, raw: str) -> dict:
    if raw.isdigit():
        return refdata.account_by_id(conn, int(raw))
    for a in refdata.accounts(conn, include_inactive=True):
        if a["name"] == raw:
            return a
    return refdata.resolve_account(conn, raw)


def _account_kind(raw: str) -> str:
    kind = raw if raw in ACCOUNT_KINDS else pick(raw, KIND_WORDS)
    if kind is None:
        raise refuse("帳戶種類可以是：現金、銀行、信用卡、證券、電子錢包")
    return kind


def _account_fields(book, values, *, editing: bool) -> Dict[str, Any]:
    conn = book.conn
    fields: Dict[str, Any] = {}
    aliases = text(values, "aliases")
    if aliases is not None:
        fields["aliases"] = items(aliases)
    for key, label in (("floor_amount", "底線"), ("target_amount", "目標")):
        v = text(values, key)
        if v is not None:
            fields[key] = None if is_clear(v) else to_minor(book, v, label, zero=True)
    for key, label in (("card_closing_day", "結帳日"), ("card_due_day", "繳款日")):
        v = text(values, key)
        if v is not None:
            fields[key] = None if is_clear(v) else whole(v, label, 1, 31)
    pay = text(values, "pay_from")
    if pay is not None:
        fields["pay_from_account_id"] = None if is_clear(pay) else refdata.resolve_account(conn, pay)["id"]
    level = text(values, "asset_class")
    if level is not None:
        fields["asset_class"] = "" if is_clear(level) else level
    bucket = text(values, "bucket")
    if bucket is not None:
        fields["bucket"] = "" if is_clear(bucket) else bucket
    if not editing:
        fields = {k: v for k, v in fields.items() if v not in (None, "")}
    return fields


def run_account(book, values):
    conn = book.conn
    aid = text(values, "id")
    name, kind = text(values, "name"), text(values, "kind")
    if aid is None:
        if not name or not kind or is_clear(name):
            raise refuse("帳戶要有名字和種類（現金、銀行、信用卡、證券、電子錢包）")
        fields = _account_fields(book, values, editing=False)
        with write(book, "account add") as w:
            a = refdata.add_account(w, name=name, kind=_account_kind(kind), **fields)
        return Result(data=out(book, w, account=a), text=f"新增帳戶：{a['name']}")
    target = _account(conn, aid)
    fields = _account_fields(book, values, editing=True)
    if name is not None:
        if is_clear(name):
            raise refuse("帳戶名稱不能空白")
        fields["name"] = name
    if kind is not None and kind != target["kind"]:
        fields["kind"] = _account_kind(kind)
    active = yes_no(text(values, "active"), "開著")
    if active is not None:
        fields["active"] = int(active)
    with write(book, "account edit") as w:
        a = refdata.update_account(w, target["id"], **fields)
    return Result(data=out(book, w, account=a), text=f"改好了：{a['name']}" + ("" if a["active"] else "（不用了）"))


# ── 帳本設定 ──────────────────────────────────────────────────────────────────────

def _setting_key(raw: str) -> str:
    if raw in settings.EDITABLE:
        return raw
    key = pick(raw, SETTING_WORDS)
    if key is None:
        raise refuse(f"「{raw}」不是可以改的設定；可以改：" + "、".join(label for _k, label in SETTING_ROWS))
    return key


def _weekday_text(value: str) -> str:
    """「一,三,五」「週一、週五」「平日」→ 0–6 的逗號清單（0＝週一）。數字原樣。"""
    v = value.strip()
    if v in ("平日", "週一到週五"):
        return "0,1,2,3,4"
    if v in ("每天", "天天"):
        return "0,1,2,3,4,5,6"
    for word in ("星期", "禮拜", "週", "周"):
        v = v.replace(word, "")
    for ch, digit in WEEKDAY_CHARS:
        v = v.replace(ch, digit)
    return v


def run_setting(book, values):
    conn = book.conn
    raw_key, raw = text(values, "key"), values.get("value")
    if raw_key is None or raw is None:
        raise refuse("要說改哪一個設定、改成什麼")
    key = _setting_key(raw_key)
    raw = str(raw).strip()
    with write(book, "setting set") as w:
        if is_clear(raw) or raw == "":
            if key == "default_account":
                raise refuse("預設付款帳戶不能空白")
            if key == "quick.lunch_weekdays":
                settings.put(w, key, "")                     # 空白＝哪天都不問
            elif key in settings.DEFAULTS:
                settings.clear(w, key)                       # 回到預設（時差：跟著這台電腦）
            elif settings.EDITABLE[key] is str:
                settings.put(w, key, settings.coerce(conn, key, ""))
            else:
                raise refuse("這個設定不能清掉，要給一個值")
        else:
            if settings.EDITABLE[key] is bool:
                raw = "true" if yes_no(raw, key) else "false"
            if key in ("quick.lunch_weekdays", "notify.weekday"):
                raw = _weekday_text(raw)
            value = settings.coerce(conn, key, raw)
            if key == "currency.decimals":
                _guard_decimals(conn, value)
            if key == "currency.code":
                known = next((c for c in CURRENCIES if c[0] == value), None)
                if known is not None:
                    _put_currency(w, conn, known)            # 認得的幣別：符號、小數位數一起換
            settings.put(w, key, value)
    value = settings.value(conn, key)
    label = next((lbl for k, lbl in SETTING_ROWS if k == key), key)
    shown = "（預設）" if value is None else value
    if key == "notify.weekday" and isinstance(value, int):
        shown = WEEKDAYS[value]
    return Result(data=out(book, w, key=key, value=value, settings=_settings_view(conn), currency=_currency_now(conn)),
                  text=f"{label}：{shown}")


# ── 選配功能、開新帳本 ─────────────────────────────────────────────────────────────

def run_feature_on(book, values):
    conn = book.conn
    name = _feature(conn, text(values, "name") or "")
    with write(book, "feature on") as w:
        r = starter.enable(w, name)
    if not r["changed"]:
        msg = f"{name} 本來就開著了"
    else:
        bits = ([f"打開「{plans.bucket_names(conn).get(r['bucket'], r['bucket'])}」那一格"] if r["bucket_turned_on"] else []) \
            + ([f"加了大類 {'、'.join(r['added_groups'])}"] if r["added_groups"] else []) \
            + ([f"細項 {'、'.join(r['added_categories'])}"] if r["added_categories"] else [])
        msg = "；".join(bits) or "開好了"
    return Result(data=out(book, w, **r, features=starter.features(conn)), text=msg)


def describe_book_new(book, values):
    n = book_svc.describe(book.conn)["transactions"]
    return f"開一本新的空帳本：現在這本（{n} 筆）會先封存在你自己的資料夾，不會刪掉。"


def run_book_new(book, values):
    if text(values, "confirm") != CONFIRM:
        raise refuse(f"要打「{CONFIRM}」才會換（現在這本會先封存，不會刪掉）")
    r = book_svc.new_book(book.conn)
    archived = r["archived"].replace("\\", "/").rsplit("/", 1)[-1]          # 只給檔名，不給資料夾位置
    data = {"archived": archived, "transactions": r["transactions"], "batch_id": None,
            "write_seq": db.write_seq(book.conn)}
    return Result(data=data, text=f"開了一本新的空帳本；原本那本封存成 {archived}（在你自己的資料夾）")


COMMANDS = (
    Cmd("group", "加一個大類（不給 id；一群細項共用的上限），或改一個：名字、哪一格、圖示、停用", WRITE, [
        Field("id", "哪一個（編號或名字；不給＝新增）"),
        Field("name", "名字"),
        Field("bucket", "放在哪一格（新增時不給＝生活費）"),
        Field("icon", "圖示"),
        Field("active", "開著（yes／no）")], run_group, positional=0, app=True),
    Cmd("cat_add", "新增類別（細項），或改一個（給 id）", WRITE, [
        Field("name", "名字"),
        Field("group", "放在哪個大類"),
        Field("keywords", "關鍵字（逗號分開；- 清掉）"),
        Field("kind", "種類：支出／收入／轉帳（新增才要，之後不能改）"),
        Field("id", "哪一個（編號或名字；不給＝新增）"),
        Field("default_budget", "單項上限（- 清掉）"),
        Field("cap_basis", "上限怎麼算：period＝每期、day＝每天、each＝每次"),
        Field("icon", "圖示"),
        Field("active", "開著（yes／no）"),
        Field("bucket", "直接算在哪一格（- 跟著大類）")], run_cat_add, positional=2, app=True),
    Cmd("account", "加一個帳戶（不給 id），或改一個", WRITE, [
        Field("id", "哪一個（編號或名字；不給＝新增）"),
        Field("name", "名字"),
        Field("kind", "種類：現金／銀行／信用卡／證券／電子錢包"),
        Field("aliases", "別名（逗號分開；- 清掉）"),
        Field("floor_amount", "底線（- 清掉）"),
        Field("target_amount", "目標（- 清掉）"),
        Field("card_closing_day", "信用卡結帳日"),
        Field("card_due_day", "信用卡繳款日"),
        Field("pay_from", "卡費從哪個帳戶扣（- 清掉）"),
        Field("asset_class", "算撲滿哪一層：cash／invest／trading／property（- 不算）"),
        Field("bucket", "轉進來的錢算哪一格（- 不綁）"),
        Field("active", "開著（yes／no）")], run_account, positional=0, app=True),
    Cmd("setting", "改一個帳本設定（幣別、每期起算日、時差、提醒的星期幾…；- 回到預設）", WRITE, [
        Field("key", "哪一個設定", required=True),
        Field("value", "改成什麼", required=True)], run_setting, positional=2, app=True),
    Cmd("feature_on", "打開一個選配功能（捐贈、投資、還債、薪資扣繳、資金操盤）", WRITE,
        [Field("name", "哪一個", required=True)], run_feature_on, positional=1, app=True),
    Cmd("init", "第一次使用：幣別、每期起算日、起始類別（通用／空白）、要開的選配功能", WRITE, [
        Field("currency", "幣別（例：TWD、新台幣）"),
        Field("start_day", "每期從幾號開始"),
        Field("seed", "起始類別：通用／空白"),
        Field("features", "要開的選配功能（逗號分開）")], run_init, app=True),
    Cmd("book_new", "開一本新的空帳本（現在這本先封存在自己的資料夾）", DANGER, [
        Field("confirm", f"確認的字（打「{CONFIRM}」）")], run_book_new, positional=1, app=True,
        describe=describe_book_new),
)

READS = frozen({"setup": read_setup, "features": read_features, "book_info": read_book})
EXPORTS = frozen({"book": export_book})
