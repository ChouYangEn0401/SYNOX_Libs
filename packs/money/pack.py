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
        first = False

        def append(d):
            nonlocal first
            first = not d["entries"] and not d["categories"]
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
        if first:
            lines.append("（提示：/money_template 可以載入現成的類別範本；不載也照樣能記）")
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
                                      "加一個：/money_cat_add <名稱> [大類] keywords=咖啡,拿鐵\n"
                                      "或載入現成的範本：/money_template")
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
                c = {"name": name, "group": group or name, "kind": "expense", "keywords": []}
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

    def template(api, chat_id, user, values):
        name = values.get("name", "").strip()
        if not name:
            lines = ["📋 類別範本（不會自己載入；載入後可以自己改、刪，也可以另外加）："]
            for key, title, about, items in TEMPLATES:
                lines.append(f"・{title}（{key}）：{about}——{'、'.join(i[0] for i in items)}")
            lines.append("載入：/money_template 基本（載兩次不會重複，也不會蓋掉你改過的）")
            api.send_message(chat_id, "\n".join(lines))
            return
        t = find_template(name)
        if t is None:
            api.send_message(chat_id, f"❌ 沒有「{name}」這個範本（/money_template 看有哪些）。")
            return
        added, skipped = update(user, lambda d: merge_template(d["categories"], t))
        lines = [f"📋 載入範本「{t[1]}」：新增 {len(added)} 個類別" + (f"（{'、'.join(added)}）" if added else "")]
        if skipped:
            lines.append(f"已經有的 {len(skipped)} 個沒動：{'、'.join(skipped)}")
        lines.append("看全部：/money_cats")
        api.send_message(chat_id, "\n".join(lines))

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
    kit.command(g, "template", template, "類別範本：不給名字＝列出有哪些；給名字＝載入（同名跳過）", effect=WRITE,
                fields=[Field("name", "範本名稱（例如 基本、帳單、存錢與投資、捐贈、收入）")])

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
