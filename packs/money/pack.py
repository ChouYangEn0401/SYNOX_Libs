"""💰 記帳：/money <金額> <類別> [備註]、/money_list [筆數]、/money_month [月份]、/money_budget <金額>。

資料經 pack.store.of(user)（保管箱，密文；個人版 data/money.json.enc，伺服器版每人一份 data/users/<id>/money.json.enc）：
    {"schema": 2, "next_id": 下一個編號, "budget": 數字或 null,
     "entries": [{id, kind, amount, category, memo, date, at}],
     "categories": [{name, group, keywords}]}
- categories：使用者自己的類別（預設空）；記帳時類別照樣可以直接打字，不在清單裡也記得下。
- id：流水號（刪掉的不重用）；kind：expense（支出）；date：YYYY-MM-DD（記在哪一天）；at：寫進來的時間（epoch）。
- 第 1 版（0.1.x）沒有 schema／id／kind／date：讀的時候 upgrade() 自己補，下一次寫入就存成第 2 版（ADR-0002）。
記一筆讓這個月的總額超過預算時，回覆裡直接說。
"""

import re
import time
from collections import defaultdict
from datetime import date as Date, datetime, timedelta

from isdtelekit.core.registry import WRITE, Field

from synox.packs import kit

SCHEMA = 2
KINDS = ("expense",)
UNSORTED = "待分類"


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


def month_total(data, key=None) -> float:
    """這個月花了多少：只算支出（kind=expense）。"""
    key = key or month_key()
    return sum(float(e["amount"]) for e in data.get("entries", []) if in_month(e, key) and e.get("kind") == "expense")


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


def check_name(value: str):
    value = value.strip()
    if not value or len(value) > 20:
        return "要 1～20 個字"
    return None


def install(app, pack):
    g = pack.group

    def load(user):
        data = pack.store.of(user).load(None)
        return upgrade(data if isinstance(data, dict) else empty())

    def update(user, fn):
        """讀 → 補成第 2 版 → fn(data) → 存（整段持鎖）；回 fn 的回傳值。"""
        return pack.store.of(user).update(lambda d: fn(upgrade(d)), empty())

    def add(api, chat_id, user, values):
        amount = float(values["amount"].replace(",", ""))
        if amount <= 0:
            api.send_message(chat_id, "❌ 金額要大於 0。")
            return
        today = now().date()
        day = parse_day(values["date"], today) if values.get("date") else today
        at = int(time.time()) if day == today else int(time.mktime(datetime(day.year, day.month, day.day, 12).timetuple()))
        key = day.strftime("%Y-%m")

        def append(d):
            typed = values["category"].strip()
            known = find_cat(d["categories"], typed)            # 自己的類別：照存著的寫法；不在清單裡的照打的字記
            entry = {"id": d["next_id"], "kind": "expense", "amount": amount,
                     "category": known["name"] if known else typed,
                     "memo": values.get("memo", "").strip(), "date": day.isoformat(), "at": at}
            d["next_id"] += 1
            d["entries"].append(entry)
            return entry, month_total(d, key) - amount, month_total(d, key), d.get("budget")

        entry, before, after, budget = update(user, append)
        memo = f"（{entry['memo']}）" if entry["memo"] else ""
        when = "" if day == today else f"（記在 {day.isoformat()}）"
        lines = [f"💰 記了 #{entry['id']}：{entry['category']} {fmt(amount)} 元{memo}{when}",
                 f"{key} 共 {fmt(after)} 元"]
        if budget:
            if before <= float(budget) < after:
                lines.append(f"⚠️ 這一筆讓這個月超過預算了（預算 {fmt(budget)} 元，超過 {fmt(after - float(budget))} 元）")
            elif after > float(budget):
                lines.append(f"⚠️ 這個月已經超過預算 {fmt(budget)} 元（超過 {fmt(after - float(budget))} 元）")
            else:
                lines.append(f"預算還剩 {fmt(float(budget) - after)} 元")
        api.send_message(chat_id, "\n".join(lines))

    def line_of(e) -> str:
        memo = f"（{e['memo']}）" if e.get("memo") else ""
        return f"#{e['id']} {e['date'][5:].replace('-', '/')}  {e['category']} {fmt(e['amount'])} 元{memo}"

    def list_(api, chat_id, user, values):
        n = int(values.get("n") or 10)
        entries = load(user)["entries"][-n:]
        if not entries:
            api.send_message(chat_id, "💰 還沒有記帳。用 /money <金額> <類別> 記一筆。")
            return
        lines = [f"💰 最近 {len(entries)} 筆"] + [line_of(e) for e in reversed(entries)]
        api.send_message(chat_id, "\n".join(lines))

    def by_category(data, key):
        out = defaultdict(float)
        for e in data["entries"]:
            if in_month(e, key) and e["kind"] == "expense":
                out[e["category"]] += float(e["amount"])
        return out

    def month(api, chat_id, user, values):
        data = load(user)
        key = values.get("month", "").strip() or month_key()
        by_cat = by_category(data, key)
        total = sum(by_cat.values())
        lines = [f"💰 {key} 共 {fmt(total)} 元"]
        lines += [f"  {cat}：{fmt(v)} 元" for cat, v in sorted(by_cat.items(), key=lambda kv: -kv[1])]
        budget = data.get("budget")
        if budget:
            left = float(budget) - total
            lines.append(f"預算 {fmt(budget)} 元，" + (f"還剩 {fmt(left)} 元" if left >= 0 else f"⚠️ 超過 {fmt(-left)} 元"))
        else:
            lines.append("（還沒設預算：/money_budget <金額>）")
        api.send_message(chat_id, "\n".join(lines))

    def budget(api, chat_id, user, values):
        amount = float(values["amount"].replace(",", ""))
        if amount < 0:
            api.send_message(chat_id, "❌ 預算不能是負的。")
            return

        def set_budget(d):
            d["budget"] = amount or None
            return month_total(d)

        total = update(user, set_budget)
        if not amount:
            api.send_message(chat_id, "💰 預算拿掉了。")
            return
        api.send_message(chat_id, f"💰 每月預算設為 {fmt(amount)} 元（這個月目前 {fmt(total)} 元）。")

    # ---- 類別：使用者自己的資料（程式不靠類別名字判斷任何事） ----

    def cat_line(c) -> str:
        return c["name"]

    def cats(api, chat_id, user, values):
        data = load(user)
        name = values.get("name", "").strip()
        if name:
            c = find_cat(data["categories"], name)
            if not c:
                api.send_message(chat_id, f"❌ 沒有「{name}」這個類別（/money_cats 看全部）。")
                return
            lines = [f"💰 類別「{c['name']}」", f"大類：{c['group']}",
                     "關鍵字：" + ("、".join(c["keywords"]) if c["keywords"] else "（沒有）")]
            api.send_message(chat_id, "\n".join(lines))
            return
        if not data["categories"]:
            api.send_message(chat_id, "💰 你還沒有自己的類別（記帳時類別可以直接打字）。\n"
                                      "加一個：/money_cat_add <名稱> [大類] keywords=咖啡,拿鐵")
            return
        groups = defaultdict(list)
        for c in data["categories"]:
            groups[c["group"]].append(cat_line(c))
        lines = [f"💰 你的類別（{len(data['categories'])} 個）"]
        lines += [f"{grp}：{'、'.join(items)}" for grp, items in groups.items()]
        api.send_message(chat_id, "\n".join(lines))

    def cat_add(api, chat_id, user, values):
        name = values["name"].strip()
        group = values.get("group", "").strip()
        words = split_words(values.get("keywords", ""))

        def put(d):
            c = find_cat(d["categories"], name)
            if c is None:
                c = {"name": name, "group": group or name, "keywords": []}
                d["categories"].append(c)
                created = True
            else:
                created = False
                if group:
                    c["group"] = group
            c["keywords"] += [w for w in words if w.lower() not in {k.lower() for k in c["keywords"]}]
            return created, dict(c)

        created, c = update(user, put)
        head = "新增了" if created else "更新了"
        kw = f"；關鍵字：{'、'.join(c['keywords'])}" if c["keywords"] else ""
        api.send_message(chat_id, f"💰 {head}類別「{c['name']}」（大類：{c['group']}{kw}）")

    def cat_rm(api, chat_id, user, values):
        name = values["name"].strip()

        def drop(d):
            c = find_cat(d["categories"], name)
            if c is not None:
                d["categories"].remove(c)
            return c

        gone = update(user, drop)
        if gone is None:
            api.send_message(chat_id, f"❌ 沒有「{name}」這個類別（/money_cats 看全部）。")
            return
        api.send_message(chat_id, f"🗑 刪掉類別「{gone['name']}」。已經記的帳不動（帳上照樣寫「{gone['name']}」）。")

    kit.command(g, "add", add, "記一筆支出", default=True, effect=WRITE, positional=3, fields=[
        Field("amount", "金額", required=True, check=kit.number),
        Field("category", "類別", required=True),
        Field("memo", "備註"),
        Field("date", "哪一天（預設今天；例如 2026-11-08、11/8、昨天）", check=check_day)])
    kit.command(g, "list", list_, "最近幾筆（預設 10）", fields=[Field("n", "筆數", check=kit.positive_int)])
    kit.command(g, "month", month, "這個月花了多少（分類別；可以給月份，例如 2026-10）",
                fields=[Field("month", "月份（YYYY-MM）", check=check_month)])
    kit.command(g, "budget", budget, "設每月預算（0＝拿掉）", effect=WRITE,
                fields=[Field("amount", "金額", required=True, check=kit.number)])
    kit.command(g, "cats", cats, "列出自己的類別（給名稱看那一個的關鍵字）",
                fields=[Field("name", "類別名稱")])
    kit.command(g, "cat_add", cat_add, "新增類別，或幫已有的類別改大類、加關鍵字", effect=WRITE, positional=2, fields=[
        Field("name", "類別名稱", required=True, check=check_name),
        Field("group", "大類（例如 食；預設跟名稱一樣）"),
        Field("keywords", "關鍵字（逗號分開，例如 咖啡,拿鐵）")])
    kit.command(g, "cat_rm", cat_rm, "刪掉一個類別（已經記的帳不動）", effect=WRITE,
                fields=[Field("name", "類別名稱", required=True)])

    def summary(user):
        """Web UI 面板（panel.html）用：這個月的總額、預算、各類別。只回這個人自己的。"""
        data = load(user)
        key = month_key()
        by_cat = by_category(data, key)
        total = sum(by_cat.values())
        budget = float(data["budget"]) if data.get("budget") else None
        return {"month": key, "total": round(total, 2), "budget": budget,
                "left": round(budget - total, 2) if budget else None,
                "by_category": [{"category": c, "amount": round(v, 2)}
                                for c, v in sorted(by_cat.items(), key=lambda kv: -kv[1])]}

    if hasattr(pack, "web_route"):
        pack.web_route("summary", summary)
