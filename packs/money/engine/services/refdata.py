"""參考資料：帳戶、類別（細項→大類→六大類）的查詢、名稱解析與增改。

名稱解析給 agent 用：依序比對「完全相同 → 別名 → 全形半形／大小寫／空白正規化後相同」；
找不到時附上最接近的幾個名字（suggestions），agent 才知道怎麼改。
"""
from __future__ import annotations

import difflib
import json
import sqlite3
import unicodedata
from typing import Any

from ..db import Writer, row_dict
from ..errors import Conflict, InvalidInput, NotFound

ACCOUNT_KINDS = ("cash", "bank", "credit_card", "securities", "e_wallet", "receivable", "loan", "pension")
# 撲滿的四層：帳戶依「錢放在哪一種水平」分層。空字串＝不歸進撲滿（信用卡是負債、代墊款是應收）。
ASSET_CLASSES = {"cash": "現金帳戶", "invest": "投資商品", "trading": "資金操盤", "property": "產"}   # 舊名「交易金」改叫「資金操盤」
# 新帳戶預設歸哪一層（照種類猜；信用卡、代墊款、勞退不歸進撲滿，所以不在這張表裡）
DEFAULT_ASSET_CLASS = {"cash": "cash", "bank": "cash", "e_wallet": "cash", "securities": "invest"}

ACCOUNT_FIELDS = ("name", "kind", "purpose", "aliases", "liquid", "floor_amount", "target_amount", "asset_class", "card_closing_day",
                  "card_due_day", "card_limit", "pay_from_account_id", "sort", "active", "note", "reserve_group", "bucket_id")
CATEGORY_FIELDS = ("name", "group_id", "kind", "keywords", "meal_slot", "default_budget", "cap_mode", "icon", "transfer_purpose",
                   "sort", "active", "bucket_override", "system_key")


def normalize(text: str) -> str:
    """全形轉半形、大小寫不分、去掉空白與間隔號：「ＡＢＣ 卡」→「abc卡」。"""
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return "".join(ch for ch in text if not ch.isspace() and ch not in "・·.-_")


def _split(csv: str) -> list[str]:
    return [p.strip() for p in (csv or "").split(",") if p.strip()]


# ---------------------------------------------------------------- 帳戶

def accounts(conn: sqlite3.Connection, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM accounts" + ("" if include_inactive else " WHERE active = 1") + " ORDER BY sort, id"
    out = []
    for r in conn.execute(sql):
        d = row_dict(r)
        d["aliases"] = _split(d["aliases"])
        out.append(d)
    return out


def account_by_id(conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise NotFound(f"找不到帳戶 #{account_id}")
    d = row_dict(row)
    d["aliases"] = _split(d["aliases"])
    return d


def _match(name: str, items: list[dict[str, Any]], *, field_label: str) -> dict[str, Any] | None:
    for item in items:                                   # 1. 完全相同
        if item["name"] == name:
            return item
    for item in items:                                   # 2. 別名
        if name in item.get("aliases", []):
            return item
    key = normalize(name)
    hits = [i for i in items if normalize(i["name"]) == key or key in {normalize(a) for a in i.get("aliases", [])}]
    if len(hits) == 1:                                   # 3. 正規化後相同（唯一才算）
        return hits[0]
    if len(hits) > 1:
        raise Conflict(f"「{name}」同時符合好幾個{field_label}", suggestions=[h["name"] for h in hits])
    return None


def suggest(name: str, candidates: list[str], limit: int = 3) -> list[str]:
    key = normalize(name)
    contains = [c for c in candidates if key and (key in normalize(c) or normalize(c) in key)]
    close = difflib.get_close_matches(name, candidates, n=limit, cutoff=0.3)
    out: list[str] = []
    for c in contains + close:
        if c not in out:
            out.append(c)
    return out[:limit]


def resolve_account(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    if not name or not name.strip():
        raise InvalidInput("沒有寫帳戶")
    items = accounts(conn)
    hit = _match(name.strip(), items, field_label="帳戶")
    if hit:
        return hit
    names = [a["name"] for a in items] + [al for a in items for al in a["aliases"]]
    picks = []
    for s in suggest(name, names):
        owner = next(a["name"] for a in items if s == a["name"] or s in a["aliases"])
        if owner not in picks:
            picks.append(owner)
    raise NotFound(f"找不到帳戶「{name}」", hint="設定頁的「帳戶」看清單與別名；或先在那裡加這個帳戶",
                   suggestions=picks or [a["name"] for a in items[:3]])


def _check_account(conn: sqlite3.Connection, data: dict[str, Any], account_id: int | None = None) -> None:
    """名稱不重複、日子在 1–31、水位與額度不是負的、扣款帳戶不是自己。"""
    if "name" in data:
        data["name"] = (data["name"] or "").strip()
        if not data["name"]:
            raise InvalidInput("帳戶名稱不能空白", fields={"name": "必填"})
        if conn.execute("SELECT 1 FROM accounts WHERE name = ? AND id IS NOT ?", (data["name"], account_id)).fetchone():
            raise Conflict(f"已經有帳戶「{data['name']}」")
    if data.get("asset_class") not in (None, "") and data["asset_class"] not in ASSET_CLASSES:
        raise InvalidInput("撲滿的分層只有：" + "、".join(f"{k}（{v}）" for k, v in ASSET_CLASSES.items()),
                           fields={"asset_class": "／".join(ASSET_CLASSES)})
    for key, label in (("card_closing_day", "結帳日"), ("card_due_day", "繳款日")):
        if data.get(key) is not None and not 1 <= int(data[key]) <= 31:
            raise InvalidInput(f"{label}要在 1–31 之間", fields={key: "1–31"})
    for key, label in (("floor_amount", "底線水位"), ("target_amount", "目標水位"), ("card_limit", "額度")):
        if data.get(key) is not None and int(data[key]) < 0:
            raise InvalidInput(f"{label}不能是負的", fields={key: "不能是負的"})
    if data.get("floor_amount") and data.get("target_amount") and data["target_amount"] < data["floor_amount"]:
        raise InvalidInput("目標水位要大於等於底線水位", fields={"target_amount": "比底線小"})
    if account_id is not None and data.get("pay_from_account_id") == account_id:
        raise InvalidInput("扣款帳戶不能是這張卡自己")
    if "reserve_group" in data and not (data["reserve_group"] or "").strip():
        data["reserve_group"] = None                          # 空白＝不屬於任何備用金群組


def _account_bucket(conn: sqlite3.Connection, fields: dict[str, Any]) -> None:
    """「轉進這個帳戶的錢算哪一格」：`bucket`（id、名字、顯示名字都認）或 `bucket_id` → 存成 bucket_id；
    空字串／None＝不綁。只能綁開著、不是生活費也不是還債的格子（生活費是每天花的、還債照排程算）。"""
    if "bucket" in fields:
        fields["bucket_id"] = fields.pop("bucket")
    if "bucket_id" not in fields:
        return
    value = str(fields["bucket_id"] or "").strip()
    if not value:
        fields["bucket_id"] = None
        return
    try:
        b = bucket_by_name(conn, value)
    except NotFound as e:
        raise InvalidInput(f"沒有開著的格子叫「{value}」", hint="只能選規劃頁正在用的格子（例：存錢、旅遊基金）",
                           suggestions=e.suggestions, fields={"bucket": "沒有這一格"}) from None
    if b["is_living"] or b["id"] == "debt":
        raise InvalidInput(f"「{b['label'] or b['name']}」不能綁在帳戶上", hint="生活費是每天在花的、還債照排程算；要綁的是存錢、旅遊基金這種要存起來的格子",
                           fields={"bucket": "生活費／還債不行"})
    fields["bucket_id"] = b["id"]


def _check_account_bucket(kind: str, bucket_id: Any) -> None:
    if bucket_id and kind == "credit_card":
        raise InvalidInput("信用卡不能綁格子", hint="轉進信用卡是繳卡費（刷卡那天已經算過了），不是做到哪一格",
                           fields={"bucket": "信用卡"})


def add_account(w: Writer, *, name: str, kind: str, **fields: Any) -> dict[str, Any]:
    if kind not in ACCOUNT_KINDS:
        raise InvalidInput(f"帳戶種類「{kind}」不對", hint="可以是：" + "、".join(ACCOUNT_KINDS))
    _account_bucket(w.conn, fields)
    _check_account_bucket(kind, fields.get("bucket_id"))
    _check_account(w.conn, {"name": name, **fields})
    name = name.strip()
    data = {"name": name, "kind": kind}
    data.update({k: v for k, v in fields.items() if k in ACCOUNT_FIELDS and v is not None})
    if isinstance(data.get("aliases"), list):
        data["aliases"] = ",".join(data["aliases"])
    if "liquid" not in data:
        data["liquid"] = 0 if kind in ("receivable", "pension", "loan") else 1
    if "asset_class" not in data:                            # 撲滿的哪一層：沒給就照種類猜（之後可以自己改）
        data["asset_class"] = DEFAULT_ASSET_CLASS.get(kind, "")
    cols = ", ".join(data)
    new_id = w.conn.execute(f"INSERT INTO accounts ({cols}) VALUES ({', '.join('?' * len(data))})",
                            tuple(data.values())).lastrowid
    after = row_dict(w.conn.execute("SELECT * FROM accounts WHERE id = ?", (new_id,)).fetchone())
    w.audit("create", "accounts", new_id, None, after)
    return account_by_id(w.conn, new_id)


RECEIVABLE = "代墊款"


def receivable_account(w: Writer) -> dict[str, Any]:
    """幫別人先付的錢放的「應收」帳戶；第一次用就自動建（記代墊、把一筆花錢切成代墊都走這裡）。"""
    try:
        return resolve_account(w.conn, RECEIVABLE)
    except NotFound:
        return add_account(w, name=RECEIVABLE, kind="receivable", purpose="幫別人先付的錢，等對方還")


PAYABLE = "欠別人"


def payable_account(w: Writer) -> dict[str, Any]:
    """別人先幫我付、我還沒還的錢記在這個「負債」帳戶（kind=loan）；第一次記「別人幫我墊」就自動建。"""
    try:
        return resolve_account(w.conn, PAYABLE)
    except NotFound:
        return add_account(w, name=PAYABLE, kind="loan", purpose="別人先幫我付的錢，等我還")


def update_account(w: Writer, account_id: int, **fields: Any) -> dict[str, Any]:
    before = row_dict(w.conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone())
    if before is None:
        raise NotFound(f"找不到帳戶 #{account_id}")
    fields = dict(fields)
    _account_bucket(w.conn, fields)
    changes = {k: v for k, v in fields.items() if k in ACCOUNT_FIELDS}
    if isinstance(changes.get("aliases"), list):
        changes["aliases"] = ",".join(changes["aliases"])
    if "kind" in changes and changes["kind"] not in ACCOUNT_KINDS:
        raise InvalidInput(f"帳戶種類「{changes['kind']}」不對")
    if "kind" in changes and changes["kind"] != before["kind"] and "receivable" in (changes["kind"], before["kind"]):
        raise InvalidInput("代墊款（別人欠我的）的種類不能改，別的帳戶也不能改成它",
                           hint="代墊、報帳靠這個帳戶；要換就另外加一個帳戶", fields={"kind": "代墊款"})
    if "bucket_id" in changes or "kind" in changes:
        _check_account_bucket(changes.get("kind", before["kind"]), changes.get("bucket_id", before.get("bucket_id")))
    _check_account(w.conn, {**before, **changes}, account_id)
    if "active" in changes and not changes["active"] and before["active"]:
        row = w.conn.execute("SELECT value FROM settings WHERE key = 'default_account'").fetchone()
        if row and json.loads(row[0]) == before["name"]:
            raise InvalidInput("這是預設付款帳戶，先換一個預設，再停用它",
                               hint="/money_setting default_account <別的帳戶>")
    if "reserve_group" in changes and not (changes["reserve_group"] or "").strip():
        changes["reserve_group"] = None
    if "name" in changes:
        changes["name"] = changes["name"].strip()
    changes = {k: v for k, v in changes.items() if before.get(k) != v}       # 沒變的欄位不寫、不留 audit
    if not changes:
        return account_by_id(w.conn, account_id)
    sets = ", ".join(f"{k} = ?" for k in changes)
    w.conn.execute(f"UPDATE accounts SET {sets} WHERE id = ?", (*changes.values(), account_id))
    after = row_dict(w.conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone())
    w.audit("update", "accounts", account_id, before, after)
    return account_by_id(w.conn, account_id)


# ---------------------------------------------------------------- 類別

CATEGORY_SQL = """
SELECT c.*, g.name AS group_name, COALESCE(c.bucket_override, g.bucket_id) AS bucket_id,
       COALESCE(NULLIF(b.label, ''), b.name) AS bucket_name, b.is_living
FROM categories c JOIN category_groups g ON g.id = c.group_id LEFT JOIN buckets b ON b.id = COALESCE(c.bucket_override, g.bucket_id)
"""


# 程式用「系統代號」去找的類別（categories.system_key）：借出、繳卡費、薪水扣繳的勞保健保、帳單的房租電費、三餐判斷…。
# 代號 → 起始檔裡的預設名字（名字只是預設，使用者可以改；程式一律照代號找）。
# 有代號的類別可以改名，但不能停用、不能換種類（停用或換種類會讓記帳的某個功能找不到它）。
# 收入與轉帳的類別一律算系統的；三餐（有 meal_slot）也是（不能停用）。
SYSTEM_KEYS = {
    "lend": "借出", "collect": "收回借款", "card_payment": "繳卡費", "transfer": "帳戶互轉", "income.other": "其他收入",
    "salary": "薪資", "debt.self_loan": "自借款還款", "debt.credit_loan": "信貸還款", "big_purchase": "大額採買",
    "payroll.labor_insurance": "勞保", "payroll.health_insurance": "健保", "payroll.income_tax": "所得稅",
    "payroll.pension": "勞退自提", "snack": "點心宵夜", "bill.phone": "手機網路", "bill.rent": "房租",
    "bill.electricity": "電費", "bill.water": "水費", "bill.gas": "瓦斯", "meal.breakfast": "早餐", "meal.lunch": "午餐",
    "meal.dinner": "晚餐", "fee.interest": "利息", "fee.late": "浪費", "misc": "雜支"}


def is_system_category(cat: dict[str, Any]) -> bool:
    return cat["kind"] != "expense" or bool(cat.get("meal_slot")) or bool(cat.get("system_key"))


def system_category(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    """照系統代號找（開著的）細項；找不到回 None（跟 find_category 一樣：記帳時找不到類別不擋）。"""
    row = conn.execute(CATEGORY_SQL + " WHERE c.system_key = ? AND c.active = 1 AND g.active = 1", (key,)).fetchone()
    return _cat(row) if row is not None else None


def system_name(conn: sqlite3.Connection, key: str) -> str:
    """系統代號 → 這本帳裡現在的名字（停用或沒有就用預設名字）。"""
    row = conn.execute("SELECT name FROM categories WHERE system_key = ?", (key,)).fetchone()
    return row["name"] if row is not None else SYSTEM_KEYS.get(key, key)


CAP_MODES = ("period", "day", "each")                        # 每月／每天／每次


def _cap_mode(fields: dict[str, Any]) -> None:
    """外面一律講 cap_basis（相容舊的），裡面存 cap_mode（多了 each）。"""
    if "cap_basis" in fields:
        fields["cap_mode"] = fields.pop("cap_basis")
    mode = fields.get("cap_mode")
    if mode is not None and mode not in CAP_MODES:
        raise InvalidInput("上限算法只有 period（每月）、day（每天）、each（每次）",
                           fields={"cap_basis": "period／day／each"})


def _cat(row: sqlite3.Row) -> dict[str, Any]:
    d = row_dict(row)
    d["keywords"] = _split(d["keywords"])
    d["is_living"] = bool(d["is_living"])
    d["cap_basis"] = d.get("cap_mode") or "period"           # 對外還是叫 cap_basis
    return d


def categories(conn: sqlite3.Connection, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    where = "" if include_inactive else " WHERE c.active = 1 AND g.active = 1"
    return [_cat(r) for r in conn.execute(CATEGORY_SQL + where + " ORDER BY g.sort, c.sort, c.id")]


def category_by_id(conn: sqlite3.Connection, category_id: int) -> dict[str, Any]:
    row = conn.execute(CATEGORY_SQL + " WHERE c.id = ?", (category_id,)).fetchone()
    if row is None:
        raise NotFound(f"找不到類別 #{category_id}")
    return _cat(row)


def find_category(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    """找細項；找不到回 None（記帳時找不到類別不擋，放待分類）。"""
    if not name or not name.strip():
        return None
    return _match(name.strip(), categories(conn), field_label="類別")


def category_suggestions(conn: sqlite3.Connection, name: str) -> list[str]:
    cats = categories(conn)
    groups = {c["group_name"] for c in cats}
    if name in groups:                                   # 給了大類名：建議那個大類的細項
        return [c["name"] for c in cats if c["group_name"] == name][:5]
    return suggest(name, [c["name"] for c in cats])


GROUP_FIELDS = ("name", "bucket_id", "sort", "active", "icon")


def groups(conn: sqlite3.Connection, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    where = "" if include_inactive else " WHERE g.active = 1"
    rows = conn.execute("SELECT g.*, COALESCE(NULLIF(b.label, ''), b.name) AS bucket_name, b.is_living FROM category_groups g "
                        f"LEFT JOIN buckets b ON b.id = g.bucket_id{where} ORDER BY g.sort, g.id")
    return [dict(row_dict(r), is_living=bool(r["is_living"])) for r in rows]


def group_by_id(conn: sqlite3.Connection, group_id: int) -> dict[str, Any]:
    for g in groups(conn, include_inactive=True):
        if g["id"] == group_id:
            return g
    raise NotFound(f"找不到大類 #{group_id}")


def bucket_by_name(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """用途：id、正式名字、顯示名字都認。"""
    rows = [row_dict(r) for r in conn.execute("SELECT * FROM buckets WHERE active = 1 ORDER BY sort")]
    for b in rows:
        if name in (b["id"], b["name"], b["label"]) or normalize(b["label"] or b["name"]) == normalize(name):
            return b
    raise NotFound(f"找不到用途「{name}」", suggestions=suggest(name, [b["label"] or b["name"] for b in rows]))


def add_group(w: Writer, *, name: str, bucket: str | None = None, sort: int | None = None, icon: str | None = None) -> dict[str, Any]:
    """自己加一個大類（＝一群細項共用的上限）。bucket 不給就放在生活費底下；同名但停用過的重新打開。
    圖示只從 icon 來，名字就是名字（不從名字裡猜圖示）。"""
    name = (name or "").strip()
    icon = (icon or "").strip()
    if not name:
        raise InvalidInput("大類名稱不能空白", fields={"name": "必填"})
    row = row_dict(w.conn.execute("SELECT * FROM category_groups WHERE name = ?", (name,)).fetchone())
    if row is not None:
        if row["active"]:
            raise Conflict(f"已經有大類「{name}」")
        return update_group(w, row["id"], active=1, **({"bucket": bucket} if bucket else {}), **({"icon": icon} if icon else {}))
    bucket_id = bucket_by_name(w.conn, bucket)["id"] if bucket else "living"
    if sort is None:
        sort = (w.conn.execute("SELECT MAX(sort) FROM category_groups").fetchone()[0] or 0) + 1
    gid = w.conn.execute("INSERT INTO category_groups (name, bucket_id, sort, icon) VALUES (?,?,?,?)",
                         (name, bucket_id, sort, (icon or "").strip())).lastrowid
    w.audit("create", "category_groups", gid, None,
            row_dict(w.conn.execute("SELECT * FROM category_groups WHERE id = ?", (gid,)).fetchone()))
    return group_by_id(w.conn, gid)


def _rename_group_in_plans(w: Writer, old: str, new: str) -> None:
    """大類改名：每一期計畫裡的預算要跟著改 key，不然那個大類的預算會不見。"""
    for period in [r["period"] for r in w.conn.execute("SELECT period FROM period_plans").fetchall()]:
        before = row_dict(w.conn.execute("SELECT * FROM period_plans WHERE period = ?", (period,)).fetchone())
        data = json.loads(before["group_budgets"] or "{}")
        if old not in data:
            continue
        data[new] = data.pop(old)
        w.conn.execute("UPDATE period_plans SET group_budgets = ?, updated_at = ? WHERE period = ?",
                       (json.dumps(data, ensure_ascii=False), w.now, period))
        w.audit("update", "period_plans", period, before,
                row_dict(w.conn.execute("SELECT * FROM period_plans WHERE period = ?", (period,)).fetchone()))


def update_group(w: Writer, group_id: int, **fields: Any) -> dict[str, Any]:
    """改大類：名字（每期計畫的預算跟著改）、歸哪個用途、排序、停用（停用不刪資料，歷史還看得到）。"""
    before = row_dict(w.conn.execute("SELECT * FROM category_groups WHERE id = ?", (group_id,)).fetchone())
    if before is None:
        raise NotFound(f"找不到大類 #{group_id}")
    changes = {k: v for k, v in fields.items() if k in GROUP_FIELDS and v is not None}
    if fields.get("bucket"):
        changes["bucket_id"] = bucket_by_name(w.conn, fields["bucket"])["id"]
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        if not changes["name"]:
            raise InvalidInput("大類名稱不能空白", fields={"name": "必填"})
        if w.conn.execute("SELECT 1 FROM category_groups WHERE name = ? AND id <> ?",
                          (changes["name"], group_id)).fetchone():
            raise Conflict(f"已經有大類「{changes['name']}」")
    changes = {k: v for k, v in changes.items() if before.get(k) != v}
    if changes.get("active") == 0:
        keep = [c["name"] for c in categories(w.conn) if c["group_id"] == group_id and is_system_category(c)]
        if keep:
            raise InvalidInput(f"「{before['name']}」底下有系統記帳會用到的類別（{'、'.join(keep[:3])}），不能整個大類停用",
                               hint="把用不到的項目個別停用就好", fields={"active": "有系統用的類別"})
    if not changes:
        return group_by_id(w.conn, group_id)
    w.conn.execute(f"UPDATE category_groups SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?",
                   (*changes.values(), group_id))
    w.audit("update", "category_groups", group_id, before,
            row_dict(w.conn.execute("SELECT * FROM category_groups WHERE id = ?", (group_id,)).fetchone()))
    if "name" in changes:
        _rename_group_in_plans(w, before["name"], changes["name"])
    return group_by_id(w.conn, group_id)


def group_by_name(conn: sqlite3.Connection, name: str, *, include_inactive: bool = False) -> dict[str, Any]:
    gs = groups(conn, include_inactive=include_inactive)
    for g in gs:
        if g["name"] == name or normalize(g["name"]) == normalize(name):
            return g
    raise NotFound(f"找不到大類「{name}」", suggestions=suggest(name, [g["name"] for g in gs]))


def buckets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row_dict(r), is_living=bool(r["is_living"])) for r in conn.execute("SELECT * FROM buckets ORDER BY sort")]


def tree(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """六大類 → 大類 → 細項，給 ref 與網頁設定頁。收入與轉帳放在最後（bucket 為 null）。"""
    cats = categories(conn)
    out = []
    bucket_rows = buckets(conn) + [{"id": None, "name": "收入與轉帳", "is_living": False}]
    for b in bucket_rows:
        gs = [g for g in groups(conn) if g["bucket_id"] == b["id"]]
        if not gs:
            continue
        out.append({"bucket": b["id"], "name": b["name"], "is_living": b["is_living"], "groups": [
            {"id": g["id"], "name": g["name"], "icon": g.get("icon") or "", "categories": [
                {"id": c["id"], "name": c["name"], "kind": c["kind"], "keywords": c["keywords"],
                 "meal_slot": c["meal_slot"], "default_budget": c["default_budget"], "icon": c.get("icon") or ""}
                for c in cats if c["group_id"] == g["id"]]}
            for g in gs]})
    return out


CORE_BUCKETS = ("giving", "fixed", "saving", "living", "invest", "debt")   # 核心六個用途；plans.BUCKET_IDS 就是它（plans import 這裡，不能反過來）


def catalog(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """設定頁「類別」三層：用途（格子）→ 大類 → 細項。只列花錢的細項（收入、轉帳是系統用的，不在這裡改）。

    每一個在用的用途都列（自己加的格子沒有自己的大類也列，才看得到、改得到「哪些類別算進這一格」）；
    存錢、投資、還債這種核心格子記的是轉帳，沒有花錢的類別就不列。
    linked＝別的大類的細項、自己指定算進這一格的（例：通訊訂閱的「手機網路」算進「手機費」）。
    每個細項帶：算在哪個用途（bucket）、是不是自己指定的（pinned）、系統用的（system＝名字不能改）。"""
    allc = categories(conn)
    cats = [c for c in allc if c["kind"] == "expense"]
    has_any = {c["group_id"] for c in allc}
    has_expense = {c["group_id"] for c in cats}
    all_groups = groups(conn)
    item = lambda c: {"id": c["id"], "name": c["name"], "icon": c.get("icon") or "", "keywords": c["keywords"],  # noqa: E731
                      "group_id": c["group_id"], "group": c["group_name"], "bucket": c["bucket_id"],
                      "bucket_name": c["bucket_name"], "pinned": c.get("bucket_override"), "system": is_system_category(c)}
    out = []
    for b in buckets(conn):
        if not b["active"]:
            continue
        # 只有收入／轉帳類別的大類（投資、轉帳）不列；自己剛加、還沒有項目的大類要列（才能往裡面加項目）
        gs = [g for g in all_groups if g["bucket_id"] == b["id"] and (g["id"] in has_expense or g["id"] not in has_any)]
        own = {g["id"] for g in gs}
        linked = [item(c) for c in cats if c.get("bucket_override") == b["id"] and c["group_id"] not in own]
        core = b["id"] in CORE_BUCKETS
        if core and not gs and not linked:
            continue
        out.append({"bucket": b["id"], "name": b["label"] or b["name"], "is_living": b["is_living"], "core": core,
                    "groups": [{"id": g["id"], "name": g["name"], "icon": g.get("icon") or "",
                                "categories": [item(c) for c in cats if c["group_id"] == g["id"]]} for g in gs],
                    "linked": linked})
    return out


def add_category(w: Writer, *, name: str, group: str, kind: str = "expense", **fields: Any) -> dict[str, Any]:
    if kind not in ("expense", "income", "transfer"):
        raise InvalidInput(f"類別種類「{kind}」不對", hint="expense／income／transfer")
    name = (name or "").strip()                              # 圖示只從 icon 來，名字就是名字
    if not name:
        raise InvalidInput("類別名稱不能空白", fields={"name": "必填"})
    old = row_dict(w.conn.execute("SELECT * FROM categories WHERE name = ?", (name,)).fetchone())
    if old is not None and (old["active"] or old["kind"] != kind):
        raise Conflict(f"已經有類別「{name}」")
    if (fields.get("default_budget") or 0) < 0:
        raise InvalidInput("預設預算不能是負的", fields={"default_budget": "不能是負的"})
    _cap_mode(fields)
    if fields.get("system_key") and w.conn.execute("SELECT 1 FROM categories WHERE system_key = ? AND name <> ?",
                                                   (fields["system_key"], name)).fetchone():
        fields["system_key"] = None                          # 這個代號已經有別的類別在用：新的這個就不帶代號
    g = group_by_name(w.conn, group)
    if old is not None:                                      # 以前停用過的同名類別：重新打開（歷史紀錄還在它名下），搬到這個大類
        keep = {k: v for k, v in fields.items() if k in CATEGORY_FIELDS and v is not None and k not in ("name", "kind")}
        keep.pop("bucket_override", None)
        return update_category(w, old["id"], active=1, group_id=g["id"], bucket_override=None, **keep)    # 舊的「算在哪一格」不沿用
    data = {"name": name, "group_id": g["id"], "kind": kind}
    data.update({k: v for k, v in fields.items() if k in CATEGORY_FIELDS and v is not None})
    if isinstance(data.get("keywords"), list):
        data["keywords"] = ",".join(data["keywords"])
    new_id = w.conn.execute(f"INSERT INTO categories ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                            tuple(data.values())).lastrowid
    w.audit("create", "categories", new_id, None,
            row_dict(w.conn.execute("SELECT * FROM categories WHERE id = ?", (new_id,)).fetchone()))
    return category_by_id(w.conn, new_id)


def update_category(w: Writer, category_id: int, **fields: Any) -> dict[str, Any]:
    before = row_dict(w.conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone())
    if before is None:
        raise NotFound(f"找不到類別 #{category_id}")
    _cap_mode(fields)                                        # cap_basis → cap_mode（要在過濾欄位之前）
    changes = {k: v for k, v in fields.items() if k in CATEGORY_FIELDS and k != "system_key"}   # 系統代號不能改
    if isinstance(changes.get("keywords"), list):
        changes["keywords"] = ",".join(changes["keywords"])
    if "name" in changes:
        changes["name"] = (changes["name"] or "").strip()
        if not changes["name"]:
            raise InvalidInput("類別名稱不能空白", fields={"name": "必填"})
        if w.conn.execute("SELECT 1 FROM categories WHERE name = ? AND id <> ?", (changes["name"], category_id)).fetchone():
            raise Conflict(f"已經有類別「{changes['name']}」")
    if changes.get("default_budget") is not None and changes["default_budget"] < 0:
        raise InvalidInput("預設預算不能是負的", fields={"default_budget": "不能是負的"})
    if "group_id" in changes and "bucket_override" not in fields and before.get("bucket_override"):
        new_group = group_by_id(w.conn, changes["group_id"])
        if new_group["is_living"] or new_group["bucket_id"] == before["bucket_override"]:
            changes["bucket_override"] = None       # 搬進生活費的大類（或剛好搬到它指定的那個用途）：舊的指定不留著，不然算進生活費卻記在別格
    changes = {k: v for k, v in changes.items() if before.get(k) != v}       # 沒變的欄位不寫、不留 audit
    if changes.get("active") == 0 and is_system_category(before):
        raise InvalidInput(f"「{before['name']}」是系統記帳會用到的類別，不能停用",
                           hint="名字、圖示、關鍵字、算在哪一格、搬到別的大類都可以改",
                           fields={"active": "系統用的"})
    if "kind" in changes and before.get("system_key"):
        raise InvalidInput(f"「{before['name']}」是系統記帳會用到的類別，不能換種類",
                           hint="名字、圖示、關鍵字、算在哪一格、搬到別的大類都可以改",
                           fields={"kind": "系統用的"})
    if not changes:
        return category_by_id(w.conn, category_id)
    w.conn.execute(f"UPDATE categories SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?",
                   (*changes.values(), category_id))
    w.audit("update", "categories", category_id, before,
            row_dict(w.conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()))
    return category_by_id(w.conn, category_id)
