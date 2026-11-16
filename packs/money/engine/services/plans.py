"""期計畫：每期的六大類分配（薪水怎麼分）與生活支出各大類的預算。

找某一期的計畫：
1. 那一期有存 → 用它（沒寫到的大類補預設值）；
2. 沒有 → 沿用最近一期有存的計畫（預算通常每個月差不多），source 標 carried；
3. 一期都沒有 → 用細項的 default_budget 加總成各大類預算，source 標 default。
改預算時才把那一期實際存下來。

用途（buckets 表）＝薪水分給誰。核心的六個（`BUCKET_IDS`）是程式認得的位置（轉帳用途、生活費要靠它們算），
不能刪；名字（label）、固定住（locked＝每期一定要做到，沒做到會亮燈）、要不要用（active）都在資料庫，
而且可以**自己新增用途**（`add_bucket`、設定頁、`/money_bucket`）：
- 每期要分的項目＝現在 active 的用途（**不是寫死的六個**）；停用的用途，舊的期計畫裡還有金額就繼續看得到。
- `living`（生活費）是中心：不能停用、不能固定住，金額＝生活費各大類預算的加總。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import Writer, row_dict
from ..errors import InvalidInput
from . import periods, refdata
from .transactions import parse_amount

BUCKET_IDS = refdata.CORE_BUCKETS                     # 核心六個（定義在 refdata：它不能反過來 import plans）
DEFAULT_LABELS = {"giving": "捐贈", "fixed": "固定支出", "saving": "存錢", "living": "生活費", "invest": "投資", "debt": "還債"}


def buckets(conn: sqlite3.Connection, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    """在用的用途：id、顯示名字、正式名字、是不是生活費、固定住了沒、核心的刪不掉。照 sort 排。"""
    where = "" if include_inactive else " WHERE active = 1"
    return [{"id": r["id"], "name": r["label"] or r["name"], "official": r["name"], "is_living": bool(r["is_living"]),
             "locked": bool(r["locked"]), "active": bool(r["active"]), "core": r["id"] in BUCKET_IDS,
             "rollover": bool(r["rollover"]), "rollover_from": r["rollover_from"], "flex": bool(r["flex"])}
            for r in conn.execute(f"SELECT * FROM buckets{where} ORDER BY sort")]


def _carry_cache(conn: sqlite3.Connection) -> dict[str, Any]:
    """這一次請求（連線）內算過的滾存數字：總覽、錢包、額度提醒都要同一個數字，每一次都從頭走一遍太慢。
    資料有變（write_seq 或這條連線自己改了列）就整份作廢。"""
    token = (conn.execute("SELECT value FROM meta WHERE key = 'write_seq'").fetchone()[0], conn.total_changes)
    cache = getattr(conn, "_rollover_cache", None)
    if cache is None or cache["token"] != token:
        cache = {"token": token, "carry": {}, "plan": {}, "actual": {}}
        conn._rollover_cache = cache
    return cache


def rollover_carry(conn: sqlite3.Connection, bucket_id: str, period_key: str) -> int:
    """滾存：這個用途從開始滾存的那一期，到這一期的前一期（最多往回 36 期），每一期「計畫 − 做到」加起來。

    沒用完的留下來（正的）、超過的欠下來（負的）；這一期能用的＝這一期的計畫＋這個數字。
    沒開滾存、起算的那一期壞掉或還沒到＝0。例：水電瓦斯每期撥 1,435，上期沒帳單 → 這期帶 1,435 進來，帳單 2,870 剛好用完。
    從這一期往回一期一期走（periods.shift），不假設「連續的月份就是連續的期」（起算日是 16 號時期名會跳月）。"""
    row = conn.execute("SELECT rollover, rollover_from FROM buckets WHERE id = ?", (bucket_id,)).fetchone()
    if row is None or not row["rollover"] or not row["rollover_from"]:
        return 0
    cache = _carry_cache(conn)
    ck = (bucket_id, period_key)
    if ck in cache["carry"]:
        return cache["carry"][ck]
    from ..errors import NotFound
    from . import stats                       # stats 也用 plans，這裡才引入
    try:
        start = periods.get(conn, row["rollover_from"])
        target = periods.get(conn, period_key)
    except (InvalidInput, NotFound):
        cache["carry"][ck] = 0                                  # 起算期壞掉：當作沒開，不要讓總覽整頁壞掉
        return 0
    chain: list[periods.Period] = []
    p = periods.shift(conn, target, -1)
    while p.start >= start.start and len(chain) < 36:
        chain.append(p)
        p = periods.shift(conn, p, -1)
    carry = 0
    for q in chain:
        if q.key not in cache["plan"]:
            cache["plan"][q.key] = get(conn, q.key)["buckets"]
        if q.key not in cache["actual"]:
            cache["actual"][q.key] = stats.period_summary(conn, q.key)["allocation"]["actual"]
        carry += int(cache["plan"][q.key].get(bucket_id) or 0) - int(cache["actual"][q.key].get(bucket_id) or 0)
    cache["carry"][ck] = carry
    return carry


def bucket_ids(conn: sqlite3.Connection) -> list[str]:
    """這一期要分的用途（照 sort，停用的不算）。入口與統計都問這個，不要寫死六個。"""
    return [b["id"] for b in buckets(conn)]


def bucket_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {b["id"]: b["name"] for b in buckets(conn, include_inactive=True)}


def add_bucket(w: Writer, label: str, *, locked: bool = False) -> dict[str, Any]:
    """自己加一個用途（薪水怎麼分多一個項目）；同名但停用過的就重新打開。"""
    label = (label or "").strip()
    if not label or len(label) > 8:
        raise InvalidInput("名字要 1～8 個字", fields={"label": "長度"})
    row = row_dict(w.conn.execute("SELECT * FROM buckets WHERE label = ? OR name = ?", (label, label)).fetchone())
    if row is not None:
        if row["active"]:
            raise InvalidInput(f"已經有用途叫「{label}」")
        return set_bucket(w, row["id"], active=True)
    n = 1
    while w.conn.execute("SELECT 1 FROM buckets WHERE id = ?", (f"extra{n}",)).fetchone():
        n += 1
    bucket_id, sort = f"extra{n}", (w.conn.execute("SELECT MAX(sort) FROM buckets").fetchone()[0] or 0) + 1
    w.conn.execute("INSERT INTO buckets (id, name, label, sort, is_living, locked, active) VALUES (?,?,?,?,0,?,1)",
                   (bucket_id, label, label, sort, int(bool(locked))))
    w.audit("create", "buckets", bucket_id, None,
            row_dict(w.conn.execute("SELECT * FROM buckets WHERE id = ?", (bucket_id,)).fetchone()))
    # 名字一樣的細項直接對上（用途「房租」＝細項「房租」），不用再到設定勾一次；名字不同的由使用者自己對
    same = w.conn.execute("SELECT id FROM categories WHERE name = ? AND kind <> 'income' AND active = 1 "
                          "AND bucket_override IS NULL", (label,)).fetchone()
    if same:
        refdata.update_category(w, same["id"], bucket_override=bucket_id)
    return next(b for b in buckets(w.conn) if b["id"] == bucket_id)


FLEX_TOLERANCE = 0.1
# 「薪水怎麼分」的三組：名字由使用者自己改（設定 plan.group.must／flex／living；空白＝預設）。
# 哪一格在哪一組照 🔒：鎖起來的＝第一組（沒做到會亮燈）、沒鎖的＝第二組、生活費＝第三組。
GROUP_DEFAULTS = {"must": ("一定要做到", "答應過的、跑不掉的"), "flex": ("有餘力再做", "這個月少一點也沒關係"),
                  "living": ("剩下可以花的", "每天看的就是這個數字")}


def group_names(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    from . import settings
    return {k: {"name": (settings.get(conn, f"plan.group.{k}") or "").strip() or name, "tip": tip, "default": name}
            for k, (name, tip) in GROUP_DEFAULTS.items()}


def flex_tolerance(conn: sqlite3.Connection) -> float:
    from . import settings
    return float(settings.get(conn, "plan.flex_tolerance", FLEX_TOLERANCE))


def close_enough(conn: sqlite3.Connection, bucket: dict[str, Any], target: int, used: int) -> bool:
    """金額會浮動的格子（水電、訂閱）：做到的跟該做的差在容許範圍內（預設 ±10%）就算做完。
    水電和訂閱費本來就會浮動，落在一個範圍內就夠了，不必剛剛好。"""
    if not bucket.get("flex") or target <= 0 or used <= 0:
        return False
    return abs(used - target) <= target * flex_tolerance(conn) + 1e-9


def set_bucket(w: Writer, bucket_id: str, *, label: str | None = None, locked: bool | None = None,
               active: bool | None = None, rollover: bool | None = None, rollover_from: str | None = None,
               flex: bool | None = None) -> dict[str, Any]:
    before = row_dict(w.conn.execute("SELECT * FROM buckets WHERE id = ?", (bucket_id,)).fetchone())
    if before is None:
        raise InvalidInput(f"沒有「{bucket_id}」這個用途", hint="、".join(BUCKET_IDS))
    changes: dict[str, Any] = {}
    if label is not None:
        label = label.strip()
        if not label or len(label) > 8:
            raise InvalidInput("名字要 1～8 個字", fields={"label": "長度"})
        if w.conn.execute("SELECT 1 FROM buckets WHERE id <> ? AND (label = ? OR name = ?)",
                          (bucket_id, label, label)).fetchone():
            raise InvalidInput(f"已經有用途叫「{label}」")
        changes["label"] = label
    if locked is not None:
        if bucket_id == "living" and locked:
            raise InvalidInput("生活費是用來花的，不能固定住；要固定的是捐贈、固定支出、存錢這些")
        if locked and (rollover if rollover is not None else before["rollover"]):
            raise InvalidInput("滾存的格子不能同時固定住（固定住＝每期一定要做到，滾存＝沒用完留到下期）", fields={"locked": "滾存中"})
        changes["locked"] = int(bool(locked))
    if rollover is not None:
        if rollover and bucket_id in BUCKET_IDS:
            raise InvalidInput("核心的用途（捐贈、固定支出、存錢、生活費…）不能滾存；只有自己加的格子（例：水電瓦斯）可以",
                               fields={"rollover": "核心的用途"})
        if rollover and (locked if locked is not None else before["locked"]):
            raise InvalidInput("固定住的格子不能滾存（固定住＝每期一定要做到），先取消固定", fields={"rollover": "固定住了"})
        changes["rollover"] = int(bool(rollover))
        if rollover and not before["rollover"]:
            changes["rollover_from"] = (periods.get(w.conn, rollover_from) if rollover_from
                                        else periods.current(w.conn)).key                     # 從這一期開始算（前面的期不追溯）
        elif rollover and rollover_from:
            changes["rollover_from"] = periods.get(w.conn, rollover_from).key
        elif not rollover:
            changes["rollover_from"] = None
    elif rollover_from:
        if not before["rollover"]:
            raise InvalidInput("要先開滾存（rollover）才能指定從哪一期開始算", fields={"rollover_from": "還沒開滾存"})
        changes["rollover_from"] = periods.get(w.conn, rollover_from).key                    # 一律照期名正規化，壞的期名擋在這裡
    if flex is not None:
        if bucket_id == "living" and flex:
            raise InvalidInput("生活費本來就是拿來花的，不用標「會浮動」")
        changes["flex"] = int(bool(flex))
    if active is not None:
        if bucket_id == "living" and not active:
            raise InvalidInput("生活費是中心（網站的「還可以花多少」靠它），不能停用")
        if not active and before["active"]:
            own = [g["name"] for g in refdata.groups(w.conn) if g["bucket_id"] == bucket_id]
            if own:                                          # 不然那些大類從設定頁消失，記帳卻還選得到、錢算進停用的格子
                raise InvalidInput(f"「{before['label'] or before['name']}」底下還有大類（{'、'.join(own[:3])}），先把它們搬到別的格子或停用",
                                   hint="設定 → 類別 → 那個大類的 ✎，換「屬於哪一格」", fields={"active": "還有大類"})
            for c in refdata.categories(w.conn):              # 指定算進這一格的類別：回到原本的大類（跟著大類算）
                if c.get("bucket_override") == bucket_id:
                    refdata.update_category(w, c["id"], bucket_override=None)
        changes["active"] = int(bool(active))
    changes = {k: v for k, v in changes.items() if before[k] != v}
    if changes:
        w.conn.execute(f"UPDATE buckets SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?",
                       (*changes.values(), bucket_id))
        w.audit("update", "buckets", bucket_id, before,
                row_dict(w.conn.execute("SELECT * FROM buckets WHERE id = ?", (bucket_id,)).fetchone()))
    return next(b for b in buckets(w.conn, include_inactive=True) if b["id"] == bucket_id)


def default_group_budgets(conn: sqlite3.Connection, days: int = 30) -> dict[str, int]:
    """還沒設預算時，用細項的上限估各大類：每天的上限（cap_mode='day'）要乘這一期的天數、「每次」的沒有期總量。

    讀 cap_mode（migration 16 起每天／每次只存這裡；cap_basis 是舊欄位，後來改的不會同步過去）。
    """
    out: dict[str, int] = {}
    for g in refdata.groups(conn):
        if not g["is_living"]:
            continue
        total = 0
        for r in conn.execute("SELECT default_budget, COALESCE(cap_mode, cap_basis, 'period') AS mode FROM categories "
                              "WHERE group_id = ? AND active = 1", (g["id"],)):
            amount = int(r["default_budget"] or 0)
            if r["mode"] == "each":
                continue
            total += amount * days if r["mode"] == "day" else amount
        out[g["name"]] = total
    return out


def _parse(row: sqlite3.Row) -> dict[str, Any]:
    d = row_dict(row)
    d["buckets"] = json.loads(d["buckets"] or "{}")
    d["group_budgets"] = json.loads(d["group_budgets"] or "{}")
    return d


def _stored(conn: sqlite3.Connection, period: periods.Period) -> tuple[dict[str, Any], str | None]:
    """這一期**真的存過**的計畫（沒有就沿用最近一期存過的）。回 (原始計畫, 沿用自哪一期)。"""
    row = conn.execute("SELECT * FROM period_plans WHERE period = ?", (period.key,)).fetchone()
    source, carried_from = None, None
    if row is None:
        row = conn.execute(
            "SELECT p.* FROM period_plans p JOIN periods q ON q.key = p.period "
            "WHERE q.start_date < ? ORDER BY q.start_date DESC LIMIT 1", (period.start.isoformat(),)).fetchone()
        if row is not None:
            source, carried_from = "carried", row["period"]
    if row is None:
        return {"income_expected": None, "buckets": {}, "group_budgets": {}, "source": "default", "review_id": None}, None
    plan = _parse(row)
    if source:
        plan["source"] = source
    return plan, carried_from


def _follows(stored: dict[str, Any]) -> bool:
    """生活費是不是「跟著大類加起來走」：沒填過、或存的數字剛好等於存著的大類加總（以前存檔把算出來的值也存了）。"""
    living = int(stored["buckets"].get("living") or 0)
    return living == 0 or living == sum(int(v or 0) for v in stored["group_budgets"].values() if int(v or 0) > 0)


def move_net(conn: sqlite3.Connection, period_key: str) -> dict[str, int]:
    """這一期每一格被挪入（＋）／挪出（−）的淨額。只算挪用發生的那一期。"""
    out: dict[str, int] = {}
    for r in conn.execute("SELECT from_bucket, to_bucket, amount FROM bucket_moves WHERE period = ?", (period_key,)):
        out[r["from_bucket"]] = out.get(r["from_bucket"], 0) - int(r["amount"])
        out[r["to_bucket"]] = out.get(r["to_bucket"], 0) + int(r["amount"])
    return {k: v for k, v in out.items() if v}


def get(conn: sqlite3.Connection, period_key: str | None = None, *, with_moves: bool = True) -> dict[str, Any]:
    """一期的計畫（有效的數字）。大類預算 0＝沒設，用細項的上限估；生活費沒自己填就等於大類加總。

    額度挪用：`buckets` 是**套上這期挪用之後**的額度（計畫 − 挪出 ＋ 挪入），總覽、錢包、額度提醒、
    滾存都讀它；`planned` 是使用者在規劃頁填的原始額度、`moves` 是每一格的淨挪入／挪出。生活費參與挪用時，
    `living_budget`／`daily_budget` 也跟著變（大類的軟性預算不動）。規劃頁與改計畫的地方用 `with_moves=False`，
    才不會把挪用凍結進計畫裡。"""
    period = periods.get(conn, period_key)
    defaults = default_group_budgets(conn, period.days)
    plan, carried_from = _stored(conn, period)
    follows = _follows(plan)
    # 存著 0 的大類＝沒設過（以前第一次存檔時，當下算出來的 0 也被存下來，之後設的上限就不算數了）
    groups = {name: int(plan["group_budgets"].get(name) or 0) or amount for name, amount in defaults.items()}
    for name, amount in plan["group_budgets"].items():       # 存著但已經停用的大類也保留
        groups.setdefault(name, int(amount or 0))
    assigned = sum(groups.values())                          # 分到大類的（軟性分配）
    buckets = {b: int(plan["buckets"].get(b) or 0) for b in bucket_ids(conn)}
    for b, amount in plan["buckets"].items():                # 停用的用途，舊計畫還有金額就繼續看得到
        if b not in buckets and int(amount or 0):
            buckets[b] = int(amount or 0)
    # 生活費是使用者自己直接填的一個數字（在固定項目那裡直接輸入金額）；
    # 沒填過（或填的剛好是大類加總）就跟著「大類加起來」走。大類只是軟性分配，加起來可以比生活費少。
    living = assigned if follows else int(plan["buckets"].get("living") or 0)
    buckets["living"] = living
    planned = dict(buckets)
    net = move_net(conn, period.key)
    if with_moves:
        for b, n in net.items():
            buckets[b] = int(buckets.get(b) or 0) + n
        living = buckets["living"]
    return {"period": period.key, "income_expected": plan["income_expected"], "buckets": buckets,
            "planned": planned, "moves": net,
            "group_budgets": groups, "living_budget": living, "assigned_to_groups": assigned,
            "unassigned_living": living - assigned, "living_follows": follows, "source": plan["source"],
            "carried_from": carried_from, "review_id": plan.get("review_id"),
            "daily_budget": round(living / period.days) if living else 0}


def _save(w: Writer, period_key: str, plan: dict[str, Any], source: str, review_id: str | None = None) -> None:
    before = row_dict(w.conn.execute("SELECT * FROM period_plans WHERE period = ?", (period_key,)).fetchone())
    new = (plan["income_expected"], json.dumps(plan["buckets"], ensure_ascii=False),
           json.dumps(plan["group_budgets"], ensure_ascii=False), source, review_id)
    if before and (before["income_expected"], before["buckets"], before["group_budgets"], before["source"],
                   before["review_id"]) == new:
        return                                               # 內容一樣：不重寫（重跑匯入不會多一批）
    w.conn.execute(
        "INSERT INTO period_plans (period, income_expected, buckets, group_budgets, source, review_id, updated_at) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(period) DO UPDATE SET income_expected = excluded.income_expected, "
        "buckets = excluded.buckets, group_budgets = excluded.group_budgets, source = excluded.source, "
        "review_id = excluded.review_id, updated_at = excluded.updated_at",
        (period_key, plan["income_expected"], json.dumps(plan["buckets"], ensure_ascii=False),
         json.dumps(plan["group_budgets"], ensure_ascii=False), source, review_id, w.now))
    after = row_dict(w.conn.execute("SELECT * FROM period_plans WHERE period = ?", (period_key,)).fetchone())
    w.audit("update" if before else "create", "period_plans", period_key, before, after)


def set_group_budget(w: Writer, group: str, amount: Any, period_key: str | None = None) -> dict[str, Any]:
    g = refdata.group_by_name(w.conn, group)
    if not g["is_living"]:
        raise InvalidInput(f"「{g['name']}」不是生活支出的大類，預算請用六大類分配（plan）設定")
    value = 0 if amount in (0, "0") else parse_amount(amount, "amount")
    if value < 0:
        raise InvalidInput("預算不能是負的")
    period = periods.get(w.conn, period_key)
    stored, _ = _stored(w.conn, period)
    follows = _follows(stored)
    stored["group_budgets"][g["name"]] = value
    if follows:
        stored["buckets"]["living"] = 0                      # 還沒自己填過生活費：繼續跟著加總走
    _save(w, period.key, stored, "manual")
    return get(w.conn, period.key, with_moves=False)


def set_plan(w: Writer, period_key: str | None = None, *, income_expected: Any = None,
             buckets: dict[str, Any] | None = None, group_budgets: dict[str, Any] | None = None,
             source: str = "manual", review_id: str | None = None, clear_income: bool = False) -> dict[str, Any]:
    period = periods.get(w.conn, period_key)
    stored, _ = _stored(w.conn, period)
    follows = _follows(stored)
    if clear_income:
        stored["income_expected"] = None                     # 「-」：這期不寫預計收入
    elif income_expected is not None:
        stored["income_expected"] = parse_amount(income_expected, "income_expected")
    ids = bucket_ids(w.conn)
    for key, value in (buckets or {}).items():
        if key not in ids:
            raise InvalidInput(f"沒有「{key}」這個用途", hint="、".join(ids))
        stored["buckets"][key] = 0 if value in (0, "0") else parse_amount(value, f"buckets.{key}")
    for name, value in (group_budgets or {}).items():
        g = refdata.group_by_name(w.conn, name)
        stored["group_budgets"][g["name"]] = 0 if value in (0, "0") else parse_amount(value, f"group_budgets.{name}")
    if "living" in (buckets or {}):
        # 填的剛好等於大類加總（網站的欄位預設就是填好的）＝沒有自己決定，繼續跟著走，不把算出來的值凍結起來
        shown = sum(int(v or 0) for v in get_with(w.conn, period, stored)["group_budgets"].values())
        if stored["buckets"]["living"] == shown:
            stored["buckets"]["living"] = 0
    elif group_budgets and follows:
        stored["buckets"]["living"] = 0
    _save(w, period.key, stored, source, review_id)
    return get(w.conn, period.key, with_moves=False)


def get_with(conn: sqlite3.Connection, period: periods.Period, stored: dict[str, Any]) -> dict[str, Any]:
    """用還沒存的原始計畫算一次有效的數字（存之前先看看大類加起來是多少）。"""
    defaults = default_group_budgets(conn, period.days)
    groups = {name: int(stored["group_budgets"].get(name) or 0) or amount for name, amount in defaults.items()}
    for name, amount in stored["group_budgets"].items():
        groups.setdefault(name, int(amount or 0))
    return {"group_budgets": groups}


# ---------------------------------------------------------------- 用途認領細項

def bucket_categories(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """每個用途底下算進來的細項名字（自己認領的＋大類本來就放在這個用途的）。給設定頁與總覽的說明用。"""
    out: dict[str, list[str]] = {b["id"]: [] for b in buckets(conn, include_inactive=True)}
    for c in refdata.categories(conn):
        if c["kind"] != "income" and c["bucket_id"] in out:
            out[c["bucket_id"]].append(c["name"])
    return out


def set_bucket_categories(w: Writer, bucket_id: str, names: list[str]) -> dict[str, Any]:
    """這個用途認領哪些細項：記到這些細項的錢就算進這個用途做到的（例：用途「房租」認領細項「房租」）。

    只改「自己認領」的那一層（categories.bucket_override）：沒勾的、原本是它認領的就放開，回到大類本來的用途。
    生活費不能認領（它是大類加起來的），要把細項放進生活費就把它的大類放進去。"""
    b = next((x for x in buckets(w.conn, include_inactive=True) if x["id"] == bucket_id), None)
    if b is None:
        raise InvalidInput(f"沒有「{bucket_id}」這個用途", hint="、".join(bucket_ids(w.conn)))
    if b["is_living"]:
        raise InvalidInput("生活費是大類加起來的，不能直接認領細項", hint="想把細項放進生活費，改那個細項所在的大類")
    wanted = []
    for name in names:
        c = refdata.find_category(w.conn, name)
        if c is None:
            raise InvalidInput(f"找不到類別「{name}」", suggestions=refdata.category_suggestions(w.conn, name))
        native = w.conn.execute("SELECT bucket_id FROM category_groups WHERE id = ?", (c["group_id"],)).fetchone()[0] == bucket_id
        if c["kind"] != "expense" and not native:                # 轉帳類（勞退自提）本來就在這個用途的大類裡就不用管；要「搬過來」才擋
            raise InvalidInput(f"「{c['name']}」是{'收入' if c['kind'] == 'income' else '轉帳'}類，不能算進用途"
                               "（繳卡費、借出這些是搬錢，不是花費）")
        wanted.append(c)
    keep = {c["id"] for c in wanted}
    for c in refdata.categories(w.conn, include_inactive=True):
        group_bucket = w.conn.execute("SELECT bucket_id FROM category_groups WHERE id = ?", (c["group_id"],)).fetchone()[0]
        if c["id"] in keep:
            override = None if group_bucket == bucket_id else bucket_id    # 大類本來就在這個用途：不用另外認領
            refdata.update_category(w, c["id"], bucket_override=override)
        elif c.get("bucket_override") == bucket_id:
            refdata.update_category(w, c["id"], bucket_override=None)
    return {"bucket": bucket_id, "categories": bucket_categories(w.conn)[bucket_id]}


def set_category_bucket(w: Writer, category_id: int, bucket_id: str | None) -> dict[str, Any]:
    """一個細項自己指定算在哪個用途（空＝跟著大類）：設定頁「記帳的類別」每個細項的「算在哪一格」。
    跟 set_bucket_categories 同一個規則（只動 categories.bucket_override）：生活費不能認領、生活費底下的細項不能搬去別的用途、
    收入類不行；選到大類本來就在的那個用途＝不用另外指定。"""
    c = refdata.category_by_id(w.conn, category_id)
    group_bucket = w.conn.execute("SELECT bucket_id FROM category_groups WHERE id = ?", (c["group_id"],)).fetchone()[0]
    wanted = (bucket_id or "").strip() or None
    if wanted is not None:
        b = refdata.bucket_by_name(w.conn, wanted)           # 用途的 id、名字、顯示名字都認
        wanted = b["id"]
        if wanted != group_bucket and c["kind"] != "expense":
            raise InvalidInput(f"「{c['name']}」是{'收入' if c['kind'] == 'income' else '轉帳'}類，不能算進別的用途"
                               "（繳卡費、借出這些是搬錢，不是花費：算進用途會重複算）")
        if wanted != group_bucket:
            if b["is_living"]:
                raise InvalidInput("生活費是大類加起來的，不能直接認領細項", hint="想把細項放進生活費，改那個細項所在的大類")
            if refdata.group_by_id(w.conn, c["group_id"])["is_living"]:
                raise InvalidInput(f"「{c['name']}」在生活費底下，不能改算在別的用途", hint="要搬的話把它搬到別的大類")
    return refdata.update_category(w, category_id, bucket_override=None if wanted in (None, group_bucket) else wanted)


# ---------------------------------------------------------------- 滾存的格子：這期的額度撥好了

def skips(conn: sqlite3.Connection, period_key: str) -> set[str]:
    """這一期標了「跳過」的格子（使用者決定這期不做）。"""
    return {r["bucket_id"] for r in conn.execute("SELECT bucket_id FROM bucket_skips WHERE period = ?", (period_key,))}


def skip(w: Writer, bucket_id: str, period_key: str | None = None, *, undo: bool = False) -> dict[str, Any]:
    """某一格這期跳過：只影響那一期、下一期照常；undo＝取消。不記帳——那一格就不再算還沒處理完、
    不留錢、不提醒、沒有「今天處理」。生活費不能跳過（它是拿來花的）。"""
    b = next((x for x in buckets(w.conn, include_inactive=True) if x["id"] == bucket_id), None)
    if b is None:
        raise InvalidInput(f"沒有「{bucket_id}」這個用途", hint="、".join(f"{x['id']}（{x['name']}）" for x in buckets(w.conn)))
    if b["is_living"]:
        raise InvalidInput("生活費不能跳過（它是拿來花的，不是要做完的事）")
    p = periods.get(w.conn, period_key)
    before = row_dict(w.conn.execute("SELECT * FROM bucket_skips WHERE period = ? AND bucket_id = ?", (p.key, bucket_id)).fetchone())
    if undo:
        if before:
            w.conn.execute("DELETE FROM bucket_skips WHERE id = ?", (before["id"],))
            w.audit("delete", "bucket_skips", before["id"], before, None)
        return {"bucket": bucket_id, "name": b["name"], "period": p.key, "skipped": False, "changed": bool(before)}
    if not before:
        sid = w.conn.execute("INSERT INTO bucket_skips (period, bucket_id, created_at) VALUES (?,?,?)",
                             (p.key, bucket_id, w.now)).lastrowid
        w.audit("create", "bucket_skips", sid, None,
                row_dict(w.conn.execute("SELECT * FROM bucket_skips WHERE id = ?", (sid,)).fetchone()))
    return {"bucket": bucket_id, "name": b["name"], "period": p.key, "skipped": True, "changed": not before}


PREPAY = "預繳"                                           # set_aside 的去向：不是搬去自己的帳戶，是先付給對方（記成本月支出）


def setasides(conn: sqlite3.Connection, period_key: str) -> dict[str, dict[str, Any]]:
    """這一期哪些滾存的格子已經「撥好了」：{bucket_id: {amount, at, dest, kind, txn_id}}。
    dest＝錢去哪了（帳戶名字，或「預繳」）；舊的紀錄（以前撥額度只做記號、錢沒搬）dest 是 None。"""
    out = {}
    for r in conn.execute("SELECT s.bucket_id, s.amount, s.created_at, s.txn_id, s.dest_account_id, s.dest_label, a.name AS dest_name "
                          "FROM bucket_setaside s LEFT JOIN accounts a ON a.id = s.dest_account_id WHERE s.period = ?", (period_key,)):
        dest = r["dest_name"] or r["dest_label"]
        out[r["bucket_id"]] = {"amount": r["amount"], "at": r["created_at"], "dest": dest, "txn_id": r["txn_id"],
                               "kind": "prepay" if r["dest_label"] == PREPAY else ("transfer" if r["dest_account_id"] else "note")}
    return out


def _setaside_source(conn: sqlite3.Connection, bucket_id: str, given: str | None, period_key: str) -> dict[str, Any]:
    """錢從哪個帳戶出去：有說就用；沒說就用這一格最近付款的帳戶，再沒有就用預設付款帳戶。"""
    from . import envelopes, recurring, settings
    if given:
        return refdata.resolve_account(conn, given)
    b = next(x for x in buckets(conn) if x["id"] == bucket_id)
    name = None
    try:
        name = envelopes._action(conn, b, 1, recurring.status(conn, period_key))["account"]
    except Exception:                                      # 猜不出來就用預設，不要因為猜不到擋住
        name = None
    return refdata.resolve_account(conn, name or settings.get(conn, "default_account"))


def set_aside(w: Writer, bucket_id: str, period_key: str | None = None, *, amount: Any = None, to: str | None = None,
              from_account: str | None = None, category: str | None = None, undo: bool = False) -> dict[str, Any]:
    """滾存的格子「撥額度」：這一期的額度先處理掉，**錢要真的離開日常帳戶**才算撥好
    （預備給未來的錢要離開日常帳戶，才不會不小心花掉；以前「只做記號、錢不搬」的做法已經不用）。

    去向 `to` 二選一（一定要說，不然不知道錢去哪）：
    - 一個自己的帳戶（另一個銀行帳戶、現金、旅遊基金帳戶…）→ 記一筆轉帳（purpose=prepay＝預付；錢還是自己的，
      不算花掉、錢包不動：滾存格子要留的錢本來就在「保留」裡，再扣一次就重複了。所以就算轉進綁了用途的帳戶也不算進那一格）；
    - 「預繳」→ 直接付給對方，記成這一格的**本期支出**（帳單來的時候是補差額）。
    金額 `amount` 不給就用這期的計畫額度，可以比計畫多或少。一期一格撥一次，要改就先 undo 再撥。
    undo＝拿掉撥額度的紀錄**以及**它記下的那一筆帳。"""
    b = next((x for x in buckets(w.conn) if x["id"] == bucket_id), None)
    if b is None:
        raise InvalidInput(f"沒有「{bucket_id}」這個用途", hint="、".join(bucket_ids(w.conn)))
    if not b["rollover"]:
        raise InvalidInput(f"「{b['name']}」沒有開滾存，不用「撥額度」", hint="一般的格子記帳就會自己動；滾存的格子（水電、手機費）才需要先撥額度",
                           fields={"bucket": "沒有滾存"})
    p = periods.get(w.conn, period_key)
    row = row_dict(w.conn.execute("SELECT * FROM bucket_setaside WHERE period = ? AND bucket_id = ?", (p.key, bucket_id)).fetchone())
    if undo:
        if row:
            if row.get("txn_id"):
                from . import transactions as tx
                try:
                    tx.delete(w, row["txn_id"])
                except Exception:                           # 那一筆已經被刪掉了：撥額度的紀錄照樣拿掉
                    pass
            w.conn.execute("DELETE FROM bucket_setaside WHERE id = ?", (row["id"],))
            w.audit("delete", "bucket_setaside", row["id"], row, None)
        return {"bucket": bucket_id, "name": b["name"], "period": p.key, "set_aside": None}
    if row is not None:
        return {"bucket": bucket_id, "name": b["name"], "period": p.key,
                "set_aside": setasides(w.conn, p.key)[bucket_id], "changed": False}
    if not to:
        raise InvalidInput("撥額度要說錢放到哪裡", hint=f"給一個你自己的帳戶名字（例：旅遊基金帳戶、現金），或「{PREPAY}」＝先付給對方（記成本期支出）",
                           fields={"to": "必填"})
    value = parse_amount(amount) if amount not in (None, "") else int(get(w.conn, p.key)["buckets"].get(bucket_id) or 0)
    if value <= 0:
        raise InvalidInput("撥的金額要大於 0", hint="這一格這期沒有計畫額度的話，請給 amount", fields={"amount": "要大於 0"})
    from .. import clock
    from . import transactions as tx
    src = _setaside_source(w.conn, bucket_id, from_account, p.key)
    ref = f"setaside:{p.key}:{bucket_id}:{w.batch_id}"           # 撤銷後重撥：舊的那筆（已刪）還占著 source_ref，所以帶批次
    if to.strip() == PREPAY:
        linked = bucket_categories(w.conn).get(bucket_id, [])
        pick = category or (b["name"] if b["name"] in linked else (linked[0] if len(linked) == 1 else None))
        if pick is None:
            raise InvalidInput(f"「{b['name']}」底下有好幾個細項，預繳要記在哪一個？", hint="、".join(linked) + "（用 category 指定）",
                               fields={"category": "必填"})
        cat = refdata.find_category(w.conn, pick)
        if cat is None:
            raise InvalidInput(f"沒有「{pick}」這個細項")
        t = tx.create(w, kind="expense", amount=value, date=clock.today().isoformat(), account_id=src["id"], category_id=cat["id"],
                      description=f"{b['name']}預繳（{p.key} 撥額度）", source="setaside", source_ref=ref)
        dest_id, dest_label = None, PREPAY
    else:
        dest = refdata.resolve_account(w.conn, to)
        if dest["id"] == src["id"]:
            raise InvalidInput(f"錢本來就在「{src['name']}」，沒有離開帳戶", hint="撥額度的錢要放到別的帳戶（或選「預繳」），不然很容易不小心花掉",
                               fields={"to": "要不同帳戶"})
        t = tx.create(w, kind="transfer", amount=value, date=clock.today().isoformat(), account_id=src["id"], to_account_id=dest["id"],
                      purpose="prepay", description=f"{b['name']}撥額度（{p.key}）", source="setaside", source_ref=ref)
        dest_id, dest_label = dest["id"], None
    new_id = w.conn.execute("INSERT INTO bucket_setaside (period, bucket_id, amount, created_at, txn_id, dest_account_id, dest_label) "
                            "VALUES (?,?,?,?,?,?,?)", (p.key, bucket_id, value, w.now, t["id"], dest_id, dest_label)).lastrowid
    row = row_dict(w.conn.execute("SELECT * FROM bucket_setaside WHERE id = ?", (new_id,)).fetchone())
    w.audit("create", "bucket_setaside", new_id, None, row)
    return {"bucket": bucket_id, "name": b["name"], "period": p.key, "set_aside": setasides(w.conn, p.key)[bucket_id], "txn_id": t["id"],
            "from": src["name"]}


# ---------------------------------------------------------------- 把自己加的用途併進別的用途

def fold_bucket(w: Writer, bucket_id: str, into: str, *, add_to_parent: bool = True, to: str | None = None,
                period_key: str | None = None) -> dict[str, Any]:
    """把自己加的用途（例：房租、手機費、AI 訂閱）改成別的用途底下的「固定項目」。

    薪水怎麼分與固定的錢是同一件事——房租、手機費、AI 訂閱都是「固定支出」底下的項目。
    如果本來是各開一格，跟固定支出的總額重複算。這個動作一次做完：
    1. 這一格認領的細項改歸 into（記帳照樣自動算進去，只是現在算進 into）；
    2. 在 into 底下建一個固定項目（名字、金額＝這一格這期的計畫、細項只有一個就帶上）；
    3. 這一格的金額歸零、停用（以前的紀錄留著）；`add_to_parent`＝這格的金額**加到** into 的總額（總額不變），
       不加＝它本來就算在 into 裡面（into 的總額不動）。不確定就用預設（加上去）：寧可多算一邊讓他自己看到、也不要悄悄少算。
    存錢、投資這類是搬錢的用途，項目是定期轉帳，要說轉到哪個帳戶（to）。
    """
    from . import recurring
    b = next((x for x in buckets(w.conn, include_inactive=True) if x["id"] == bucket_id), None)
    if b is None:
        raise InvalidInput(f"沒有「{bucket_id}」這個用途")
    if b["core"] or b["is_living"]:
        raise InvalidInput("核心的用途（捐贈、固定支出、存錢、生活費、投資、還債）不能併進別的格子")
    parent = refdata.bucket_by_name(w.conn, into)
    if parent["id"] == bucket_id or parent["is_living"]:
        raise InvalidInput("要併進另一個、不是生活費的用途")
    period = periods.get(w.conn, period_key)
    amount = int(get(w.conn, period.key, with_moves=False)["buckets"].get(bucket_id) or 0)
    claimed = bucket_categories(w.conn).get(bucket_id, [])
    transfer = parent["id"] in ("saving", "invest")
    if transfer and not to:
        raise InvalidInput(f"「{parent['label'] or parent['name']}」的項目是定期轉帳，要說轉到哪個帳戶（to）", fields={"to": "必填"})
    if w.conn.execute("SELECT 1 FROM recurring WHERE name = ?", (b["name"],)).fetchone():
        raise InvalidInput(f"已經有叫「{b['name']}」的固定項目", hint="先到規劃把那個固定項目改名或停用")
    # 1. 細項改歸 into
    have = bucket_categories(w.conn).get(parent["id"], [])
    if claimed:
        set_bucket_categories(w, parent["id"], have + [c for c in claimed if c not in have])
    # 2. 建項目
    day = 5                                                  # 沒有紀錄可參考就用 5 號（發薪日）；有就用你實際付的那天
    if claimed:
        marks = ",".join("?" * len(claimed))
        last = w.conn.execute("SELECT t.date FROM transactions t JOIN categories c ON c.id = t.category_id "
                              f"WHERE t.status = 'active' AND c.name IN ({marks}) ORDER BY t.date DESC LIMIT 1", claimed).fetchone()
        if last:
            day = int(last["date"][8:10])
    item = recurring.add(w, name=b["name"], kind="transfer" if transfer else "expense", amount=amount or None, day_of_month=day,
                         category=None if transfer else (claimed[0] if len(claimed) == 1 else None), to=to if transfer else None,
                         purpose={"saving": "saving", "invest": "investing"}.get(parent["id"]) if transfer else None,
                         bucket=parent["id"])
    # 3. 金額與停用
    new_amounts = {bucket_id: 0}
    if add_to_parent and amount:
        new_amounts[parent["id"]] = int(get(w.conn, period.key, with_moves=False)["buckets"].get(parent["id"]) or 0) + amount
    set_plan(w, period.key, buckets=new_amounts)
    for g in refdata.groups(w.conn):                        # 這一格自己的大類跟著搬過去（不然停用後從設定頁消失）
        if g["bucket_id"] == bucket_id:
            refdata.update_group(w, g["id"], bucket=parent["id"])
            for c in refdata.categories(w.conn):              # 上面認領時釘到 parent 的，現在大類本來就在 parent：不用再釘
                if c["group_id"] == g["id"] and c.get("bucket_override") == parent["id"]:
                    refdata.update_category(w, c["id"], bucket_override=None)
    set_bucket(w, bucket_id, active=False)
    return {"item": item, "into": parent["id"], "amount": amount, "moved_categories": claimed, "added_to_parent": bool(add_to_parent and amount)}
