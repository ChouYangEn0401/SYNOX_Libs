"""💰 記帳：/money <金額> [類別] [備註]、/money_in、/money_transfer、/money_list、/money_month、/money_budget、
/money_undo、/money_rm、/money_edit、/money_cap、/money_cats、/money_cat_add、/money_cat_rm、/money_template。
附完整的網頁畫面（工具服務，ADR-0003）：在 SYNOX 網頁的工具頁按「開啟」再按「打開」。

資料經 pack.store.of(user)（保管箱，密文；個人版 data/money.json.enc，伺服器版每人一份 data/users/<id>/money.json.enc）：
    {"schema": 2, "next_id": 下一個編號, "budget": 數字或 null,
     "entries": [{id, kind, amount, category, memo, date, at, account?}],
     "categories": [{name, group, kind, keywords, cap?}]}
- categories：使用者自己的類別（預設空）；記帳時類別照樣可以直接打字，不在清單裡也記得下。
- id：流水號（刪掉的不重用）；date：YYYY-MM-DD（記在哪一天）；at：寫進來的時間（epoch）；account：用什麼付（只是標籤）。
- kind：expense（支出）／income（收入）／transfer（轉帳：存錢、投資、還款、繳卡費）。**「這個月花了」與預算只算 expense。**
  程式只看 kind，不靠類別名字判斷任何事。
- 第 1 版（0.1.x）沒有 schema／id／kind／date：讀的時候 upgrade() 自己補，下一次寫入就存成第 2 版（ADR-0002）。

**一份邏輯**（ADR-0003 §2.5）：每個會改資料的功能是一個模組層的核心函式（op_*：吃資料與已檢查的值、回 dict，不碰 api、pack）；
看的是 view_*。指令把結果排成訊息，畫面的動作把同一個結果回成 JSON。找不到／不合規則丟 Refuse(訊息)。
"""

import re
import time
from collections import defaultdict
from datetime import date as Date, datetime, timedelta

from isdtelekit.core.registry import DANGER, WRITE, Field

from synox.packs import kit

SCHEMA = 2
KINDS = ("expense", "income", "transfer")
UNSORTED = "待分類"
ENTRIES_LIMIT = 100
ENTRIES_MAX = 500
MONTHS_MAX = 24


class Refuse(Exception):
    """核心函式說「不行」：message 是給人看的那一句；mark 是指令回覆開頭的符號（❌ 或 💰）。"""

    def __init__(self, message: str, mark: str = "❌"):
        super().__init__(message)
        self.message, self.mark = message, mark

    @property
    def text(self) -> str:
        return f"{self.mark} {self.message}"


def now() -> datetime:
    """「現在」（測試會換掉它）。"""
    return datetime.now()


def empty() -> dict:
    """每次都給一份新的空資料。以前是模組層級的 EMPTY dict：dict(EMPTY) 是淺拷貝，第一筆會寫進共用的那個 list，
    伺服器版第二個人的第一個檔會帶著前面每個人的第一筆（2026-11-10 SYNOXPacks 的整合測試抓到）。"""
    return {"schema": SCHEMA, "next_id": 1, "entries": [], "budget": None, "categories": []}


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def upgrade(data: dict) -> dict:
    """把任何一版的資料原地補成第 2 版（跑幾次都一樣）；回同一個 dict。
    第 1 版的每筆照原本的順序拿編號、kind=expense、date 從 at 算；舊欄位都留著（退回 0.1.1 也讀得懂）。"""
    data.setdefault("entries", [])
    data.setdefault("budget", None)
    data.setdefault("categories", [])
    for c in data["categories"]:
        c.setdefault("group", c.get("name", ""))
        c.setdefault("keywords", [])
        if c.get("kind") not in KIND_LABELS:
            c["kind"] = "expense"
    entries = data["entries"]
    used = [e["id"] for e in entries if _is_id(e.get("id"))]
    next_id = max(int(data.get("next_id") or 1), max(used, default=0) + 1)
    for e in entries:
        if not _is_id(e.get("id")):
            e["id"] = next_id
            next_id += 1
        if e.get("kind") not in KINDS:
            e["kind"] = "expense"
        if not isinstance(e.get("at"), (int, float)):
            e["at"] = int(time.time())
        if not e.get("date"):
            e["date"] = datetime.fromtimestamp(e["at"]).strftime("%Y-%m-%d")
        e.setdefault("category", UNSORTED)
        e.setdefault("memo", "")
    data["next_id"] = next_id
    data["schema"] = max(int(data.get("schema") or 1), SCHEMA)
    return data


def fmt(x) -> str:
    x = float(x)
    return f"{int(x):,}" if x == int(x) else f"{x:,.2f}"


def month_key(day=None) -> str:
    return (day or now()).strftime("%Y-%m")


def in_month(e, key) -> bool:
    return str(e.get("date", "")).startswith(key)


def month_totals(data, key=None) -> dict:
    """這個月每一種各多少：{"expense": 花了, "income": 收入, "transfer": 轉帳}。"""
    key = key or month_key()
    out = {kind: 0.0 for kind in KINDS}
    for e in data.get("entries", []):
        if in_month(e, key) and e.get("kind") in out:
            out[e["kind"]] += float(e["amount"])
    return out


def month_total(data, key=None) -> float:
    """這個月花了多少：只算支出；收入與轉帳（存錢、投資、還款、繳卡費）不算。"""
    return month_totals(data, key)["expense"]


DAY_WORDS = {"今天": 0, "昨天": 1, "前天": 2}


def parse_day(text: str, today: Date):
    """「2026-11-08」「11/8」「11-08」「昨天」→ date；看不懂回 None。只有月日的算今年。"""
    text = (text or "").strip()
    if text in DAY_WORDS:
        return today - timedelta(days=DAY_WORDS[text])
    m = re.fullmatch(r"(?:(\d{4})[-/])?(\d{1,2})[-/](\d{1,2})", text)
    if not m:
        return None
    try:
        return Date(int(m.group(1) or today.year), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def check_day(value: str):
    return None if parse_day(value, now().date()) else "看不懂（例如 2026-11-08、11/8、昨天）"


def check_month(value: str):
    return None if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value.strip()) else "要像 2026-11"


def split_words(text: str) -> list:
    """「咖啡,拿鐵、奶茶 茶」→ ["咖啡", "拿鐵", "奶茶", "茶"]。"""
    return [w for w in re.split(r"[,，、;；\s]+", text or "") if w]


def find_cat(categories, name: str):
    """名字一樣（不分大小寫）的那個類別，沒有回 None。"""
    low = (name or "").strip().lower()
    return next((c for c in categories if c["name"].lower() == low), None)


# 範本（ADR-0002）：選配，預設一個都不載；/money_template <名字> 才合併進使用者自己的類別（同名跳過）。
# 每項：(名稱, 大類, kind, 關鍵字)。名稱本身也算關鍵字，所以關鍵字不重寫名稱。
# 關鍵字只放通用詞，不放品牌、店名、業者名；想更準的人自己 /money_cat_add <名稱> keywords=… 加。
# 「捐贈」裡的「十一奉獻」是作者刻意保留的（作者自己的工具），不是漏改的。
TEMPLATES = (
    ("basic", "基本", "日常支出：三餐、飲料、衣食住行育樂、醫療", (
        ("早餐", "食", "expense", "蛋餅 三明治 吐司 飯糰"),
        ("午餐", "食", "expense", "便當"),
        ("晚餐", "食", "expense", ""),
        ("點心宵夜", "食", "expense", "點心 宵夜 零食 甜點 蛋糕"),
        ("飲料", "食", "expense", "咖啡 拿鐵 奶茶 果汁 紅茶 綠茶 手搖"),
        ("衣著", "衣", "expense", "衣服 褲子 鞋子 外套 襪子"),
        ("居家", "住", "expense", "日用品 衛生紙 洗衣精 清潔 家具"),
        ("交通", "行", "expense", "公車 捷運 火車 高鐵 計程車 加油 停車 油錢"),
        ("學習", "育", "expense", "書籍 課程 文具 學費 補習"),
        ("娛樂", "樂", "expense", "電影 遊戲 旅遊 唱歌 門票"),
        ("醫療", "醫療", "expense", "看診 掛號 藥局 牙醫 健檢"),
        ("其他", "其他", "expense", ""),
    )),
    ("bills", "帳單", "每月的固定帳單", (
        ("房租", "帳單", "expense", "租金"),
        ("水電瓦斯", "帳單", "expense", "水費 電費 瓦斯"),
        ("管理費", "帳單", "expense", ""),
        ("手機網路", "帳單", "expense", "電話費 手機費 網路費"),
        ("訂閱", "帳單", "expense", "月費 會員費"),
    )),
    ("saving", "存錢與投資", "存錢、投資、還款、繳卡費（轉帳，不算花費）；利息", (
        ("存錢", "存錢與投資", "transfer", "儲蓄 定存"),
        ("投資", "存錢與投資", "transfer", "股票 基金 定期定額"),
        ("還款", "存錢與投資", "transfer", "貸款 房貸 車貸 學貸"),
        ("繳卡費", "存錢與投資", "transfer", "卡費 信用卡費"),
        ("利息", "存錢與投資", "expense", ""),
    )),
    ("giving", "捐贈", "捐款、十一奉獻", (
        ("捐款", "捐贈", "expense", "捐贈 公益"),
        ("十一奉獻", "捐贈", "expense", "奉獻"),
    )),
    ("income", "收入", "薪資、兼職、退款回饋", (
        ("薪資", "收入", "income", "薪水 獎金"),
        ("兼職", "收入", "income", "打工 接案"),
        ("退款回饋", "收入", "income", "退款 退費 回饋"),
        ("其他收入", "收入", "income", "紅包"),
    )),
)
KIND_LABELS = {"expense": "支出", "income": "收入", "transfer": "轉帳"}
KIND_WORDS = {**{k: k for k in KIND_LABELS}, **{v: k for k, v in KIND_LABELS.items()}, "花費": "expense"}


def check_kind(value: str):
    return None if value.strip().lower() in KIND_WORDS else "只能是 支出／收入／轉帳"


def find_template(name: str):
    low = (name or "").strip().lower()
    return next((t for t in TEMPLATES if low in (t[0], t[1].lower())), None)


def merge_template(categories: list, template) -> tuple:
    """範本的每一項合併進 categories；已經有同名的（不分大小寫）跳過、不覆蓋。回 (新增的名稱, 跳過的名稱)。"""
    added, skipped = [], []
    for name, group, kind, words in template[3]:
        if find_cat(categories, name):
            skipped.append(name)
            continue
        categories.append({"name": name, "group": group, "kind": kind, "keywords": split_words(words)})
        added.append(name)
    return added, skipped


def guess(categories, text: str):
    """text 裡含哪個類別的名稱或關鍵字（不分大小寫）→ (類別, 對到的字)；最長的字優先，一樣長的照清單順序。沒有回 None。"""
    low = (text or "").lower()
    best = None
    for c in categories:
        for word in [c["name"], *c.get("keywords", [])]:
            if word and word.lower() in low and (best is None or len(word) > len(best[1])):
                best = (c, word)
    return best


def check_id(value: str):
    return kit.positive_int(value.strip().lstrip("#"))


def check_name(value: str):
    value = value.strip()
    if not value or len(value) > 20:
        return "要 1～20 個字"
    return None


# ── 共用的小計算 ───────────────────────────────────────────────────────────

def by_category(data, key, kind="expense"):
    out = defaultdict(float)
    for e in data["entries"]:
        if in_month(e, key) and e["kind"] == kind:
            out[e["category"]] += float(e["amount"])
    return sorted(out.items(), key=lambda kv: -kv[1])


def cap_lines(data, key, only=None) -> list:
    """有設上限的支出類別：這個月花了多少、還剩多少或超過多少（only＝只看這個類別）。"""
    spent = dict(by_category(data, key))
    out = []
    for c in data["categories"]:
        if not c.get("cap") or (only and c["name"] != only):
            continue
        used, cap = spent.get(c["name"], 0.0), float(c["cap"])
        if used > cap:
            out.append(f"⚠️ {c['name']} 超過這個月的上限 {fmt(cap)} 元（超過 {fmt(used - cap)} 元）")
        else:
            out.append(f"{c['name']} 上限 {fmt(cap)} 元，還剩 {fmt(cap - used)} 元")
    return out


def line_of(e) -> str:
    memo = f"（{e['memo']}）" if e.get("memo") else ""
    paid = f"［{e['account']}］" if e.get("account") else ""
    mark = {"income": "收入｜", "transfer": "轉帳｜"}.get(e["kind"], "")
    return f"#{e['id']} {e['date'][5:].replace('-', '/')}  {mark}{e['category']} {fmt(e['amount'])} 元{memo}{paid}"


def resolve(categories, typed: str, memo: str, forced):
    """打的類別 → (類別名, kind, 備註, 提示)。
    forced＝None（/money）：kind 跟著類別走（存錢類別就是轉帳），不認得的算支出；
    forced＝income／transfer（/money_in、/money_transfer）：kind 就是它，猜類別也只在同一種裡猜。"""
    known = find_cat(categories, typed) if typed else None
    if known:                                               # 自己的類別：照存著的寫法
        return known["name"], forced or known["kind"], memo, ""
    pool = [c for c in categories if c["kind"] == forced] if forced else categories
    hit = guess(pool, f"{typed} {memo}") if typed else None
    if hit:
        known, word = hit
        return known["name"], forced or known["kind"], f"{typed} {memo}".strip(), f"（從「{word}」猜是 {known['name']}；猜錯就 /money_undo 重記）"
    if typed:                                               # 不在清單裡、也猜不到：照打的字記
        return typed, forced or "expense", memo, ""
    return UNSORTED, forced or "expense", memo, f"（沒給類別，先放「{UNSORTED}」）"


def money_lines(totals, key, kind, amount, budget, caps) -> list:
    """記一筆／改一筆之後的那幾句（這個月共多少、預算、上限）。"""
    lines = []
    spent = totals["expense"]
    if kind == "income":
        lines.append(f"{key} 收入共 {fmt(totals['income'])} 元")
    elif kind == "transfer":
        lines.append(f"（轉帳不算花費；{key} 花了 {fmt(spent)} 元）")
    else:
        lines.append(f"{key} 共 {fmt(spent)} 元")
        if budget:
            before = spent - amount
            if before <= float(budget) < spent:
                lines.append(f"⚠️ 這一筆讓這個月超過預算了（預算 {fmt(budget)} 元，超過 {fmt(spent - float(budget))} 元）")
            elif spent > float(budget):
                lines.append(f"⚠️ 這個月已經超過預算 {fmt(budget)} 元（超過 {fmt(spent - float(budget))} 元）")
            else:
                lines.append(f"預算還剩 {fmt(float(budget) - spent)} 元")
        lines += caps
    return lines


def _amount(text) -> float:
    """已經過欄位檢查的金額 → 數字。再擋一次 nan／inf（舊版 SYNOX 的 kit.number 會放它們過去）。"""
    value = float(str(text).replace(",", ""))
    if value != value or value in (float("inf"), float("-inf")):     # nan、inf
        raise Refuse("金額要是一般的數字。")
    return value


def check_amount(value: str):
    """記帳的金額：先看是不是數字（跟以前同一句），再照 kit.amount（大於 0、最多兩位小數、不超過十億）。"""
    return kit.number(value) or kit.amount(value)


def check_limit(value: str):
    """預算、上限：0＝拿掉；其他照 kit.amount。"""
    problem = kit.number(value)
    if problem:
        return problem
    try:
        if float(value.replace(",", "")) == 0:
            return None
    except ValueError:
        return "要是數字"
    return kit.amount(value)


# ── 核心函式：會改資料的（指令與畫面都叫它們）─────────────────────────────────

def op_add(d: dict, values: dict, forced, today: Date) -> dict:
    """記一筆。回 {"entry", "notes"}；notes＝回覆第一行以後的那幾句。"""
    amount = _amount(values["amount"])
    if amount <= 0:
        raise Refuse("金額要大於 0。")
    day = parse_day(values["date"], today) if values.get("date") else today
    at = int(time.time()) if day == today else int(time.mktime(datetime(day.year, day.month, day.day, 12).timetuple()))
    key = day.strftime("%Y-%m")
    account = values.get("account", "").strip()
    first = not d["entries"] and not d["categories"]
    name, kind, memo, hint = resolve(d["categories"], values.get("category", "").strip(),
                                     values.get("memo", "").strip(), forced)
    entry = {"id": d["next_id"], "kind": kind, "amount": amount, "category": name,
             "memo": memo, "date": day.isoformat(), "at": at}
    if account:
        entry["account"] = account
    d["next_id"] += 1
    d["entries"].append(entry)
    caps = cap_lines(d, key, name) if kind == "expense" else []
    notes = ([hint] if hint else []) + money_lines(month_totals(d, key), key, kind, amount, d.get("budget"), caps)
    if first:
        notes.append("（提示：/money_template 可以載入現成的類別範本；不載也照樣能記）")
    return {"entry": dict(entry), "notes": notes, "today": day == today}


EDIT_FIELDS = ("amount", "category", "memo", "date", "account", "kind")


def op_edit(d: dict, values: dict, today: Date) -> dict:
    """改一筆（只改有給的；memo=- 與 account=- 清空，category=- 改回待分類）。回 {"entry", "notes"}。"""
    n = int(str(values["n"]).strip().lstrip("#"))
    e = next((x for x in d["entries"] if x["id"] == n), None)
    if e is None:
        raise Refuse(f"沒有 #{n} 這筆（/money_list 看編號）。")
    given = {k: str(values[k]).strip() for k in EDIT_FIELDS if values.get(k) not in (None, "")}
    if not given:
        raise Refuse("要給至少一個要改的欄位（例如 /money_edit 12 amount=150）。")
    if "amount" in given:
        amount = _amount(given["amount"])
        if amount <= 0:
            raise Refuse("金額要大於 0。")
        e["amount"] = amount
    if "date" in given:
        day = parse_day(given["date"], today)
        if day is None:
            raise Refuse("日期看不懂（例如 2026-11-08、11/8、昨天）。")
        e["date"] = day.isoformat()
    if "memo" in given:
        e["memo"] = "" if given["memo"] == "-" else given["memo"]
    if "account" in given:
        if given["account"] == "-":
            e.pop("account", None)
        else:
            e["account"] = given["account"]
    if "category" in given:
        typed = given["category"]
        if typed == "-":
            e["category"] = UNSORTED
        else:
            known = find_cat(d["categories"], typed)
            e["category"] = known["name"] if known else typed
            if known and "kind" not in given:
                e["kind"] = known["kind"]
    if "kind" in given:
        kind = KIND_WORDS.get(given["kind"].lower())
        if kind is None:
            raise Refuse("種類只能是 支出／收入／轉帳。")
        e["kind"] = kind
    notes = []
    if set(given) & {"amount", "category", "date", "kind"}:
        key = e["date"][:7]
        caps = cap_lines(d, key, e["category"]) if e["kind"] == "expense" else []
        notes = money_lines(month_totals(d, key), key, e["kind"], 0.0, d.get("budget"), caps)
    return {"entry": dict(e), "notes": notes}


def op_undo(d: dict) -> dict:
    if not d["entries"]:
        raise Refuse("沒有可以撤銷的帳。", mark="💰")
    return {"removed": d["entries"].pop()}


def op_rm(d: dict, n: int) -> dict:
    e = next((x for x in d["entries"] if x["id"] == n), None)
    if e is None:
        raise Refuse(f"沒有 #{n} 這筆（/money_list 看編號）。")
    d["entries"].remove(e)
    return {"removed": e}


def op_budget(d: dict, amount: float) -> dict:
    if amount < 0:
        raise Refuse("預算不能是負的。")
    d["budget"] = amount or None
    return {"budget": d["budget"], "spent": month_total(d)}


def op_cap(d: dict, name: str, amount: float) -> dict:
    if amount < 0:
        raise Refuse("上限不能是負的。")
    c = find_cat(d["categories"], name)
    if c is None:
        c = {"name": name, "group": name, "kind": "expense", "keywords": []}
        d["categories"].append(c)
    if c["kind"] != "expense":
        raise Refuse(f"「{c['name']}」是{KIND_LABELS[c['kind']]}類，上限只用在支出類別。")
    if amount:
        c["cap"] = amount
    else:
        c.pop("cap", None)
    return {"category": dict(c), "notes": cap_lines(d, month_key(), c["name"])}


def op_cat_add(d: dict, name: str, group: str, words: list, kind: str) -> dict:
    c = find_cat(d["categories"], name)
    if c is None:
        c = {"name": name, "group": group or name, "kind": kind or "expense", "keywords": []}
        d["categories"].append(c)
        created = True
    else:
        created = False
        if group:
            c["group"] = group
        if kind:
            c["kind"] = kind
    c["keywords"] += [w for w in words if w.lower() not in {k.lower() for k in c["keywords"]}]
    return {"created": created, "category": dict(c)}


def op_cat_rm(d: dict, name: str) -> dict:
    c = find_cat(d["categories"], name)
    if c is None:
        raise Refuse(f"沒有「{name}」這個類別（/money_cats 看全部）。")
    d["categories"].remove(c)
    return {"removed": c}


def op_template(d: dict, name: str) -> dict:
    t = find_template(name)
    if t is None:
        raise Refuse(f"沒有「{name}」這個範本（/money_template 看有哪些）。")
    added, skipped = merge_template(d["categories"], t)
    return {"template": t[1], "added": added, "skipped": skipped}


# ── 看的（畫面的讀與匯出）───────────────────────────────────────────────────

def view_month(d: dict, key: str) -> dict:
    totals = month_totals(d, key)
    budget = float(d["budget"]) if d.get("budget") else None
    spent = dict(by_category(d, key))
    months = sorted({str(e.get("date", ""))[:7] for e in d["entries"] if e.get("date")}, reverse=True)[:MONTHS_MAX]
    caps = []
    for c in d["categories"]:
        if c.get("cap"):
            used, cap = spent.get(c["name"], 0.0), float(c["cap"])
            caps.append({"category": c["name"], "cap": cap, "used": round(used, 2), "left": round(cap - used, 2),
                         "over": used > cap})
    return {"month": key, "expense": round(totals["expense"], 2), "income": round(totals["income"], 2),
            "transfer": round(totals["transfer"], 2), "budget": budget,
            "left": round(budget - totals["expense"], 2) if budget else None,
            "by_category": {k: [{"category": c, "amount": round(v, 2)} for c, v in by_category(d, key, k)]
                            for k in KINDS},
            "caps": caps, "months": months}


def _filtered(d: dict, filters: dict) -> list:
    kw = (filters.get("keyword") or "").strip().lower()
    cat = (filters.get("category") or "").strip().lower()
    key = (filters.get("month") or "").strip()
    kind = KIND_WORDS.get((filters.get("kind") or "").strip().lower(), "")
    return [e for e in d["entries"]
            if (not kw or kw in f"{e['category']} {e['memo']} {e.get('account', '')}".lower())
            and (not cat or e["category"].lower() == cat)
            and (not key or in_month(e, key))
            and (not kind or e["kind"] == kind)]


def _public(e: dict) -> dict:
    return {"id": e["id"], "kind": e["kind"], "amount": e["amount"], "category": e["category"], "memo": e.get("memo", ""),
            "date": e["date"], "account": e.get("account", "")}


def view_entries(d: dict, filters: dict) -> dict:
    rows = _filtered(d, filters)
    sums = {k: round(sum(float(e["amount"]) for e in rows if e["kind"] == k), 2) for k in KINDS}
    limit = min(int(filters.get("limit") or ENTRIES_LIMIT), ENTRIES_MAX)
    before = int(filters["before"]) if filters.get("before") else None
    newest_first = [e for e in reversed(rows) if before is None or e["id"] < before]
    page = newest_first[:limit]
    return {"entries": [_public(e) for e in page], "sums": sums, "more": len(newest_first) > limit}


def view_categories(d: dict) -> dict:
    return {"categories": [{"name": c["name"], "group": c["group"], "kind": c["kind"], "keywords": list(c["keywords"]),
                            "cap": c.get("cap")} for c in d["categories"]],
            "templates": [{"key": k, "title": t, "about": a, "items": [i[0] for i in items]}
                          for k, t, a, items in TEMPLATES],
            "kinds": dict(KIND_LABELS), "unsorted": UNSORTED}


EXPORT_HEADER = ("編號", "日期", "種類", "類別", "金額", "備註", "用什麼付")


def export_rows(d: dict, filters: dict) -> tuple:
    """匯出（ADR-0003 §2.7）：跟 entries 一樣的篩選、沒有筆數上限、照日期舊到新。金額給數字、其他給字串。"""
    rows = sorted(_filtered(d, filters), key=lambda e: (e["date"], e["id"]))
    out = []
    for e in rows:
        amount = float(e["amount"])
        out.append([e["id"], e["date"], KIND_LABELS[e["kind"]], e["category"], int(amount) if amount == int(amount) else amount,
                    e.get("memo", ""), e.get("account", "")])
    return list(EXPORT_HEADER), out


FILTER_LABELS = {"month": "月份（YYYY-MM）", "kind": "種類", "limit": "筆數", "before": "編號", "category": "類別",
                 "keyword": "關鍵字"}


def _filter_problem(key: str, value: str):
    if key == "month":
        return check_month(value)
    if key == "kind":
        return check_kind(value)
    if key in ("limit", "before"):
        return kit.positive_int(value)
    return None if len(value) <= 40 else "太長了"


def check_filters(params: dict) -> dict:
    """畫面送來的篩選：照指令那幾個欄位的檢查；不合丟 Refuse（畫面那層轉成 400）。"""
    out = {}
    for k in FILTER_LABELS:
        v = str(params.get(k) or "").strip()
        if not v:
            continue
        problem = _filter_problem(k, v)
        if problem:
            raise Refuse(f"{FILTER_LABELS[k]}{problem}")
        out[k] = v
    return out


def install(app, pack):
    g = pack.group

    def load(user):
        data = pack.store.of(user).load(None)
        return upgrade(data if isinstance(data, dict) else empty())

    def update(user, fn):
        """讀 → 補成第 2 版 → fn(data) → 存（整段持鎖）；回 fn 的回傳值。"""
        return pack.store.of(user).update(lambda d: fn(upgrade(d)), empty())

    def run(api, chat_id, user, fn):
        """指令那一層：核心函式丟 Refuse → 回「❌ …」。"""
        try:
            return update(user, fn)
        except Refuse as no:
            api.send_message(chat_id, no.text)
            return None

    # ---- 指令（回覆的字跟 0.2.0 一個字都不變）----

    def recorder(forced):
        def add(api, chat_id, user, values):
            today = now().date()
            r = run(api, chat_id, user, lambda d: op_add(d, values, forced, today))
            if r is None:
                return
            e = r["entry"]
            memo = f"（{e['memo']}）" if e["memo"] else ""
            when = "" if r["today"] else f"（記在 {e['date']}）"
            paid = f"［{e['account']}］" if e.get("account") else ""
            head = {"expense": "💰 記了", "income": "💵 記了收入", "transfer": "🔁 記了轉帳"}[e["kind"]]
            api.send_message(chat_id, "\n".join([f"{head} #{e['id']}：{e['category']} {fmt(e['amount'])} 元{memo}{paid}{when}"]
                                                + r["notes"]))
        return add

    def edit(api, chat_id, user, values):
        today = now().date()
        r = run(api, chat_id, user, lambda d: op_edit(d, values, today))
        if r is not None:
            api.send_message(chat_id, "\n".join([f"✏️ 改好了：{line_of(r['entry'])}"] + r["notes"]))

    def undo(api, chat_id, user, values):
        r = run(api, chat_id, user, op_undo)
        if r is not None:
            api.send_message(chat_id, f"↩️ 撤銷了最後一筆：{line_of(r['removed'])}")

    def rm(api, chat_id, user, values):
        n = int(values["n"].strip().lstrip("#"))
        r = run(api, chat_id, user, lambda d: op_rm(d, n))
        if r is not None:
            api.send_message(chat_id, f"🗑 刪掉了：{line_of(r['removed'])}")

    def list_(api, chat_id, user, values):
        n = int(values.get("n") or 10)
        everything = load(user)["entries"]
        if not everything:
            api.send_message(chat_id, "💰 還沒有記帳。用 /money <金額> <類別> 記一筆。")
            return
        kw = values.get("keyword", "").strip().lower()
        cat = values.get("category", "").strip().lower()
        key = values.get("month", "").strip()
        kind = KIND_WORDS.get(values.get("kind", "").strip().lower(), "")
        rows = _filtered({"entries": everything}, values)[-n:]
        filters = "、".join(x for x in (f"含「{values['keyword'].strip()}」" if kw else "",
                                        f"類別「{values['category'].strip()}」" if cat else "", key,
                                        KIND_LABELS[kind] if kind else "") if x)
        if not rows:
            api.send_message(chat_id, f"💰 沒有符合的帳（{filters}）。")
            return
        head = f"💰 最近 {len(rows)} 筆" + (f"（{filters}）" if filters else "")
        lines = [head] + [line_of(e) for e in reversed(rows)]
        if kw or cat or key or kind:
            lines.append("合計：" + "、".join(f"{KIND_LABELS[k]} {fmt(sum(float(e['amount']) for e in rows if e['kind'] == k))} 元"
                                             for k in KINDS if any(e["kind"] == k for e in rows)))
        api.send_message(chat_id, "\n".join(lines))

    def cap(api, chat_id, user, values):
        name = values["category"].strip()
        amount = _amount(values["amount"])
        r = run(api, chat_id, user, lambda d: op_cap(d, name, amount))
        if r is None:
            return
        c = r["category"]
        if not amount:
            api.send_message(chat_id, f"💰 「{c['name']}」的上限拿掉了。")
        else:
            api.send_message(chat_id, "\n".join([f"💰 「{c['name']}」每月上限設為 {fmt(amount)} 元（只提醒、不擋）。"]
                                                + r["notes"]))

    def month(api, chat_id, user, values):
        data = load(user)
        key = values.get("month", "").strip() or month_key()
        spend = by_category(data, key)
        total = sum(v for _, v in spend)
        lines = [f"💰 {key} 支出共 {fmt(total)} 元"]
        lines += [f"  {cat}：{fmt(v)} 元" for cat, v in spend]
        for kind, title in (("income", "💵 收入"), ("transfer", "🔁 轉帳（不算花費）")):
            rows = by_category(data, key, kind)
            if rows:
                lines.append(f"{title} 共 {fmt(sum(v for _, v in rows))} 元：" + "、".join(f"{c} {fmt(v)}" for c, v in rows))
        budget = data.get("budget")
        if budget:
            left = float(budget) - total
            lines.append(f"預算 {fmt(budget)} 元，" + (f"還剩 {fmt(left)} 元" if left >= 0 else f"⚠️ 超過 {fmt(-left)} 元"))
        else:
            lines.append("（還沒設預算：/money_budget <金額>）")
        lines += cap_lines(data, key)
        api.send_message(chat_id, "\n".join(lines))

    def budget(api, chat_id, user, values):
        amount = _amount(values["amount"])
        r = run(api, chat_id, user, lambda d: op_budget(d, amount))
        if r is None:
            return
        if not amount:
            api.send_message(chat_id, "💰 預算拿掉了。")
            return
        api.send_message(chat_id, f"💰 每月預算設為 {fmt(amount)} 元（這個月目前 {fmt(r['spent'])} 元）。")

    # ---- 類別：使用者自己的資料（程式不靠類別名字判斷任何事） ----

    def cat_line(c) -> str:
        return c["name"] + ("" if c["kind"] == "expense" else f"（{KIND_LABELS[c['kind']]}）")

    def cats(api, chat_id, user, values):
        data = load(user)
        name = values.get("name", "").strip()
        if name:
            c = find_cat(data["categories"], name)
            if not c:
                api.send_message(chat_id, f"❌ 沒有「{name}」這個類別（/money_cats 看全部）。")
                return
            lines = [f"💰 類別「{c['name']}」", f"種類：{KIND_LABELS[c['kind']]}"
                     + ("（不算花費）" if c["kind"] == "transfer" else ""), f"大類：{c['group']}",
                     "關鍵字：" + ("、".join(c["keywords"]) if c["keywords"] else "（沒有）"),
                     f"每月上限：{fmt(c['cap'])} 元" if c.get("cap") else "每月上限：（沒設；/money_cap 類別 金額）"]
            api.send_message(chat_id, "\n".join(lines))
            return
        if not data["categories"]:
            api.send_message(chat_id, "💰 你還沒有自己的類別（記帳時類別可以直接打字）。\n"
                                      "加一個：/money_cat_add <名稱> [大類] keywords=咖啡,拿鐵\n"
                                      "或載入現成的範本：/money_template")
            return
        groups = defaultdict(list)
        for c in data["categories"]:
            groups[c["group"]].append(cat_line(c))
        lines = [f"💰 你的類別（{len(data['categories'])} 個）"]
        lines += [f"{grp}：{'、'.join(items)}" for grp, items in groups.items()]
        api.send_message(chat_id, "\n".join(lines))

    def cat_args(values):
        return (values["name"].strip(), values.get("group", "").strip(), split_words(values.get("keywords", "")),
                KIND_WORDS.get(values.get("kind", "").strip().lower(), ""))

    def cat_add(api, chat_id, user, values):
        r = run(api, chat_id, user, lambda d: op_cat_add(d, *cat_args(values)))
        c = r["category"]
        head = "新增了" if r["created"] else "更新了"
        kw = f"；關鍵字：{'、'.join(c['keywords'])}" if c["keywords"] else ""
        api.send_message(chat_id, f"💰 {head}類別「{c['name']}」（{KIND_LABELS[c['kind']]}，大類：{c['group']}{kw}）")

    def cat_rm(api, chat_id, user, values):
        name = values["name"].strip()
        r = run(api, chat_id, user, lambda d: op_cat_rm(d, name))
        if r is not None:
            gone = r["removed"]
            api.send_message(chat_id, f"🗑 刪掉類別「{gone['name']}」。已經記的帳不動（帳上照樣寫「{gone['name']}」）。")

    def template(api, chat_id, user, values):
        name = values.get("name", "").strip()
        if not name:
            lines = ["📋 類別範本（不會自己載入；載入後可以自己改、刪，也可以另外加）："]
            for key, title, about, items in TEMPLATES:
                lines.append(f"・{title}（{key}）：{about}——{'、'.join(i[0] for i in items)}")
            lines.append("載入：/money_template 基本（載兩次不會重複，也不會蓋掉你改過的）")
            api.send_message(chat_id, "\n".join(lines))
            return
        r = run(api, chat_id, user, lambda d: op_template(d, name))
        if r is None:
            return
        added, skipped = r["added"], r["skipped"]
        lines = [f"📋 載入範本「{r['template']}」：新增 {len(added)} 個類別" + (f"（{'、'.join(added)}）" if added else "")]
        if skipped:
            lines.append(f"已經有的 {len(skipped)} 個沒動：{'、'.join(skipped)}")
        lines.append("看全部：/money_cats")
        api.send_message(chat_id, "\n".join(lines))

    def entry_fields():
        return [Field("amount", "金額", required=True, check=check_amount),
                Field("category", "類別（可以不填）"),
                Field("memo", "備註"),
                Field("date", "哪一天（預設今天；例如 2026-11-08、11/8、昨天）", check=check_day),
                Field("account", "用什麼付／存到哪（例如 現金、信用卡；只是標籤）")]

    kit.command(g, "add", recorder(None), "記一筆支出（類別是收入或轉帳類的，就照那一種記）", default=True, effect=WRITE,
                positional=3, fields=entry_fields())
    kit.command(g, "in", recorder("income"), "記一筆收入（不算花費）", effect=WRITE,
                positional=3, fields=entry_fields())
    kit.command(g, "transfer", recorder("transfer"), "記一筆轉帳：存錢、投資、還款、繳卡費（不算花費、不吃預算）",
                effect=WRITE, positional=3, fields=entry_fields())
    kit.command(g, "list", list_, "最近幾筆（預設 10；可以用關鍵字、類別、月份、種類篩）", positional=1, fields=[
        Field("n", "筆數", check=kit.positive_int),
        Field("keyword", "關鍵字（找類別、備註、付款方式）"),
        Field("category", "類別"),
        Field("month", "月份（YYYY-MM）", check=check_month),
        Field("kind", "種類：支出／收入／轉帳", check=check_kind)])
    kit.command(g, "undo", undo, "撤銷最後記的那一筆", effect=WRITE)
    kit.command(g, "rm", rm, "刪掉某一筆（編號看 /money_list）", effect=DANGER,
                fields=[Field("n", "編號", required=True, check=check_id)])
    kit.command(g, "edit", edit, "改某一筆（只改有給的；memo=- 清空備註，category=- 改回待分類）", effect=WRITE,
                positional=1, fields=[
                    Field("n", "編號", required=True, check=check_id),
                    Field("amount", "金額", check=check_amount),
                    Field("category", "類別"),
                    Field("memo", "備註"),
                    Field("date", "哪一天（例如 2026-11-08、11/8、昨天）", check=check_day),
                    Field("account", "用什麼付"),
                    Field("kind", "種類：支出／收入／轉帳", check=check_kind)])
    kit.command(g, "month", month, "這個月花了多少（分類別；可以給月份，例如 2026-10）",
                fields=[Field("month", "月份（YYYY-MM）", check=check_month)])
    kit.command(g, "budget", budget, "設每月預算（0＝拿掉）", effect=WRITE,
                fields=[Field("amount", "金額", required=True, check=check_limit)])
    kit.command(g, "cap", cap, "設某個支出類別每月的上限（0＝拿掉；只提醒、不擋）", effect=WRITE, fields=[
        Field("category", "類別", required=True, check=check_name),
        Field("amount", "金額", required=True, check=check_limit)])
    kit.command(g, "cats", cats, "列出自己的類別（給名稱看那一個的關鍵字）",
                fields=[Field("name", "類別名稱")])
    kit.command(g, "cat_add", cat_add, "新增類別，或幫已有的類別改大類、種類、加關鍵字", effect=WRITE, positional=2, fields=[
        Field("name", "類別名稱", required=True, check=check_name),
        Field("group", "大類（例如 食；預設跟名稱一樣）"),
        Field("keywords", "關鍵字（逗號分開，例如 咖啡,拿鐵）"),
        Field("kind", "種類：支出／收入／轉帳（預設支出；轉帳不算花費）", check=check_kind)])
    kit.command(g, "cat_rm", cat_rm, "刪掉一個類別（已經記的帳不動）", effect=WRITE,
                fields=[Field("name", "類別名稱", required=True)])
    kit.command(g, "template", template, "類別範本：不給名字＝列出有哪些；給名字＝載入（同名跳過）", effect=WRITE,
                fields=[Field("name", "範本名稱（例如 基本、帳單、存錢與投資、捐贈、收入）")])

    def summary(user):
        """Web UI 面板（panel.html）用：這個月花了多少（total，只算支出）、收入、轉帳、預算、各類別。只回這個人自己的。"""
        v = view_month(load(user), month_key())
        return {"month": v["month"], "total": v["expense"], "income": v["income"], "transfer": v["transfer"],
                "budget": v["budget"], "left": v["left"], "by_category": v["by_category"]["expense"],
                "caps": [{"category": c["category"], "cap": c["cap"]} for c in v["caps"]]}

    if hasattr(pack, "web_route"):
        pack.web_route("summary", summary)

    # ---- 完整的網頁畫面（工具服務，ADR-0003；舊版 SYNOX 沒有這些方法：照樣能用指令）----
    if not hasattr(pack, "app_read"):
        return

    def act(user, fn):
        """畫面那一層：核心函式丟 Refuse → kit.AppError（訊息跟指令那句一樣，去掉開頭的符號）。"""
        try:
            return update(user, fn)
        except Refuse as no:
            raise kit.AppError(no.message)

    def reading(fn):
        def read(user, params):
            try:
                filters = check_filters(params)
            except Refuse as no:
                raise kit.AppError(no.message, code="invalid", status=400)
            return fn(load(user), filters)
        return read

    pack.app_read("month", reading(lambda d, f: view_month(d, f.get("month") or month_key())))
    pack.app_read("entries", reading(view_entries))
    pack.app_read("categories", reading(lambda d, f: view_categories(d)))

    def adder(forced):
        return lambda user, values: {k: v for k, v in act(user, lambda d: op_add(d, values, forced, now().date())).items()
                                     if k != "today"}

    pack.app_action("add", adder(None))
    pack.app_action("in", adder("income"))
    pack.app_action("transfer", adder("transfer"))
    pack.app_action("edit", lambda user, values: act(user, lambda d: op_edit(d, values, now().date())))
    pack.app_action("undo", lambda user, values: act(user, op_undo))

    def rm_text(user, values):
        n = int(values["n"].strip().lstrip("#"))
        e = next((x for x in load(user)["entries"] if x["id"] == n), None)
        if e is None:
            return f"刪掉 #{n}？"
        return f"刪掉 {line_of(e)}？刪了不能復原。"

    pack.app_action("rm", lambda user, values: act(user, lambda d: op_rm(d, int(values["n"].strip().lstrip("#")))),
                    describe=rm_text)
    pack.app_action("budget", lambda user, values: act(user, lambda d: op_budget(d, _amount(values["amount"]))))
    pack.app_action("cap", lambda user, values: act(user, lambda d: op_cap(d, values["category"].strip(),
                                                                         _amount(values["amount"]))))
    pack.app_action("cat_add", lambda user, values: act(user, lambda d: op_cat_add(d, *cat_args(values))))
    pack.app_action("cat_rm", lambda user, values: act(user, lambda d: op_cat_rm(d, values["name"].strip())))
    pack.app_action("template", lambda user, values: act(user, lambda d: op_template(d, values.get("name", "").strip())))

    def export(user, params):
        try:
            filters = check_filters(params)
        except Refuse as no:
            raise kit.AppError(no.message, code="invalid", status=400)
        header, rows = export_rows(load(user), filters)
        name = f"money-{filters.get('month') or 'all'}.csv"
        return kit.Download(name, "text/csv; charset=utf-8", kit.csv_bytes(header, rows))

    pack.app_export("entries_csv", export)
