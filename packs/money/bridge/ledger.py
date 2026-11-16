"""記帳主線＋明細頁（W3）：記一筆、改、刪、撤銷、小日記、重新分類、可能重複、列出、看一筆；明細頁要讀的資料。

每個 run 只做「字串欄位 → 引擎的參數 → 呼叫 engine/services → 組回覆」，邏輯都在引擎。
欄位名是契約（app/js/routes.js 照這些名字送值）：可以加欄位、加檢查、改說明，**不要改名或拿掉**。

值怎麼寫（CONTRACT.md 第 5 節）：
- 金額：帳本的小數位數是 0（預設）時就是整數；小數位數大於 0 時可以打小數（99.5），這裡照位數換成最小單位。
- 日期：YYYY-MM-DD，另外收「今天、昨天、前天、1/15、01-15、2030/1/15」。
- 是／否：yes／no（也收 true／false、1／0、是／否）。清掉：「-」。
"""

import re
from datetime import date, timedelta

from ..engine import clock, db
from ..engine.errors import InvalidInput, NotFound
from ..engine.services import (batches, classify, dedup, editing, inbox, ingest, notes, periods, quick, refdata,
                               settings, transactions as tx, views, wallet)
from . import table
from .table import CLEAR, DANGER, READ, WRITE, Cmd, Field, Result, frozen

DOMAIN = "ledger"
SOURCE = "synox"                                  # 寫入批次的來源（誰叫的另外寫在 actor：synox:<來源>）

YES = ("yes", "y", "true", "1", "是", "對", "on")
NO = ("no", "n", "false", "0", "否", "不", "off")
KIND_WORDS = (("expense", "expense"), ("支出", "expense"), ("花錢", "expense"), ("income", "income"), ("收入", "income"),
              ("transfer", "transfer"), ("轉帳", "transfer"), ("iou", "iou"), ("代墊", "iou"), ("欠款", "iou"))
DIRECTION_WORDS = (("lend", "lend"), ("代墊", "lend"), ("借出", "lend"), ("owe", "owe"), ("欠", "owe"),
                   ("別人幫我墊", "owe"), ("collect", "collect"), ("收回", "collect"), ("repay", "repay"), ("還", "repay"),
                   ("還錢", "repay"))
MEAL_RULE = ("類別可以不填：先看關鍵字；吃的東西依時間分（10:30 前早餐、15:00 前午餐、17:00 前點心、21:30 前晚餐、之後宵夜）；"
             "猜不到放待分類。買回家的原料算日用品，當場吃掉的才依時間分餐。")
LIST_DEFAULT, LIST_MAX = 10, 50


# ── 欄位 ─────────────────────────────────────────────────────────────────────

def txn_fields():
    """記一筆（add／in／transfer／iou）共用的欄位；畫面的 POST /api/txn 整包照這些名字送。
    前三個照位置填：/money 120 午餐 雞腿便當。"""
    return [Field("amount", "金額", required=True),
            Field("category", "類別（可以不填，會自己猜）"),
            Field("desc", "買了什麼／說明"),
            Field("account", "用哪個帳戶付／存到哪"),
            Field("date", "哪一天（預設今天；例如 2030-01-15、1/15、昨天）"),
            Field("time", "幾點（HH:MM，可以不填）"),
            Field("note", "備註"),
            Field("kind", "種類：expense／income／transfer／iou"),
            Field("to", "轉入哪個帳戶（轉帳）"),
            Field("purpose", "轉帳用途"),
            Field("refund", "是不是退款（yes／no）"),
            Field("for_period", "算哪一期（預繳／補繳，例如 2030-02）"),
            Field("merchant", "店家"),
            Field("nature", "長期用（stock）"),
            Field("counterparty", "對象（借給誰／欠誰）"),
            Field("direction", "代墊與欠款的方向：lend／owe／collect／repay")]


def edit_fields():
    return [Field("id", "哪一筆（編號）", required=True),
            Field("amount", "金額"),
            Field("desc", "買了什麼／說明"),
            Field("category", "類別"),
            Field("account", "帳戶"),
            Field("to", "轉入哪個帳戶"),
            Field("date", "哪一天"),
            Field("time", "幾點"),
            Field("merchant", "店家"),
            Field("note", "備註（- 清掉）"),
            Field("for_period", "算哪一期（- 拿掉）"),
            Field("nature", "長期用（stock；- 拿掉）"),
            Field("counterparty", "對象"),
            Field("advance", "切成代墊（yes）或切回花錢（no）")]


# ── 小工具：字串 → 引擎要的值 ────────────────────────────────────────────────────

def _get(values, key):
    """有給、不是空白的欄位 → 去掉前後空白的字串；沒給回 None。"""
    v = (values or {}).get(key)
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _cleared(v):
    """「-」＝清掉 → 空字串；其他原樣。"""
    return "" if v == CLEAR else v


def _yes(text, field):
    t = str(text).strip().lower()
    if t in YES:
        return True
    if t in NO:
        return False
    raise InvalidInput(f"「{text}」看不懂，要寫 yes 或 no", fields={field: "要寫 yes／no"})


def _word(text, table, field, hint):
    t = str(text).strip().lower()
    for word, value in table:
        if t == word:
            return value
    raise InvalidInput(f"「{text}」看不懂", hint=hint, fields={field: "看不懂"})


def parse_day(text, today):
    """日期（共用的 table.parse_day）＋記帳自己的檢查：不能在明天之後、不收民國年（tx.parse_date）。"""
    return tx.parse_date(table.parse_day(text, today))


def _md(iso):
    return f"{int(iso[5:7])}/{int(iso[8:10])}"


def line(book, t):
    """一筆一行（大項目在前、細節在後，跟明細頁同一個寫法）。"""
    when = _md(t["date"]) + (f" {t['time']}" if t.get("time") else "")
    flow = f"{t['account']} → {t['to_account']}" if t["kind"] == "transfer" else t["account"]
    sign = "+" if t["kind"] == "income" else ""
    detail = f"・{t['detail']}" if t.get("detail") else ""
    return f"{when} {t['title']}{detail} {sign}{table.show(book.conn, t['amount'])}（{flow}）　編號 {t['id']}"


def _write(book, command):
    return db.write(book.conn, actor=book.actor, source=SOURCE, command=command)


def _seq(book):
    return db.write_seq(book.conn)


# ── 記一筆 ───────────────────────────────────────────────────────────────────

def _kind_of(book, values, default):
    """這一筆是哪一種：指令決定（in／transfer／iou）；add 看 kind 欄位、有沒有給 to、類別是哪一種。"""
    given = _get(values, "kind")
    if given:
        return _word(given, KIND_WORDS, "kind", "expense（支出）／income（收入）／transfer（轉帳）／iou（代墊與欠款）")
    if default != "expense":
        return default
    if _get(values, "to"):
        return "transfer"
    cat = refdata.find_category(book.conn, _get(values, "category") or "")
    if cat and cat["kind"] in ("income", "transfer"):
        return cat["kind"]
    return "expense"


def _record(book, values, default_kind):
    """欄位 → ingest 的一筆紀錄（跟原本網站的 POST /api/txn 同一個做法）。"""
    kind = _kind_of(book, values, default_kind)
    amount = table.to_minor(book, _get(values, "amount") or "")
    day = parse_day(_get(values, "date") or "今天", book.today)
    desc = _get(values, "desc") or ""
    category = _get(values, "category")
    if category and kind in ("expense", "income") and refdata.find_category(book.conn, category) is None and not desc:
        desc, category = category, None                   # /money 120 雞腿便當：第二個字不是類別就當內容，類別用猜的
    common = {"amount": amount, "date": day}
    for key in ("time", "note"):
        if _get(values, key):
            common[key] = _get(values, key)
    if kind == "iou":
        direction = _get(values, "direction")
        if direction:
            direction = _word(direction, DIRECTION_WORDS, "direction",
                              "lend＝幫別人先付、owe＝別人幫我付、collect＝對方還我、repay＝我還對方")
        return "iou", {"type": "iou", "direction": direction, "who": _get(values, "counterparty"), "desc": desc,
                       "account": _get(values, "account"), "category": category, **common}
    rec = {"type": kind, "desc": desc, **common}
    if _get(values, "for_period"):
        rec["for_period"] = _get(values, "for_period")
    if kind == "transfer":
        to = _get(values, "to")
        if not to:
            what = f"「{category}」是轉帳，" if category else "轉帳"
            raise InvalidInput(f"{what}要寫轉進哪個帳戶", hint="例：to=銀行帳戶", fields={"to": "缺少"})
        rec.update({"from": _get(values, "account") or settings.get(book.conn, "default_account") or "現金", "to": to})
        for key in ("purpose", "counterparty", "category"):
            if _get(values, key):
                rec[key] = _get(values, key)
        return kind, rec
    if category:
        rec["category"] = category
    if _get(values, "account"):
        rec["account"] = _get(values, "account")
    if kind == "expense":
        if _get(values, "merchant"):
            rec["merchant"] = _get(values, "merchant")
        if _get(values, "nature") and _cleared(_get(values, "nature")):
            rec["nature"] = _get(values, "nature")
        if _get(values, "refund") and _yes(_get(values, "refund"), "refund"):
            rec["refund"] = True
    return kind, rec


def _record_text(book, out):
    results = out.get("results") or []
    if not results:
        return "沒有記到任何東西"
    res = results[0]
    t = tx.get(book.conn, res["id"], include_deleted=True)
    if res.get("status") == "replayed":
        return f"這筆已經記過了：{line(book, t)}"
    head = "↩️ 記好了（退款）" if t["kind"] == "expense" and t["amount"] < 0 else "💰 記好了"
    return f"{head}：{line(book, t)}\n記錯了：/money_undo"


def _add_as(default_kind, command):
    def run(book, values):
        _kind, rec = _record(book, values, default_kind)
        out = ingest.run(book.conn, {"records": [rec]}, actor=book.actor, source=SOURCE, command=command)
        out["write_seq"] = _seq(book)
        warnings = list(out.get("warnings") or [])
        return Result(data=out, text=_record_text(book, out), warnings=warnings)
    run.kind = default_kind
    return run


run_add = _add_as("expense", "add")
run_in = _add_as("income", "in")
run_transfer = _add_as("transfer", "transfer")
run_iou = _add_as("iou", "iou")


# ── 改、刪、撤銷 ─────────────────────────────────────────────────────────────────

def _id(values, field="id"):
    v = _get(values, field)
    if not v:
        raise InvalidInput("要寫哪一筆（編號）", hint="編號在 /money_list 的每一行最後面", fields={field: "缺少"})
    return v


def run_edit(book, values):
    txn_id = _id(values)
    changes = {}
    if _get(values, "amount"):
        changes["amount"] = table.to_minor(book, _get(values, "amount"))
    if _get(values, "date"):
        changes["date"] = parse_day(_get(values, "date"), book.today)
    for key in ("desc", "category", "time", "merchant", "note", "for_period", "nature", "counterparty"):
        v = _get(values, key)
        if v is not None:
            changes[key] = _cleared(v)
    for key in ("account", "to"):
        if _get(values, key):
            changes[key] = _get(values, key)
    if _get(values, "advance"):
        changes["advance"] = _yes(_get(values, "advance"), "advance")
    if not changes:
        raise InvalidInput("要改什麼？", hint="例：/money_edit <編號> amount=150 category=晚餐", fields={"id": "沒有要改的"})
    with _write(book, "edit") as w:
        t = editing.edit(w, txn_id, **changes)
        batch_id = w.batch_id if w.changes else None
    t = tx.get(book.conn, txn_id, include_deleted=True)
    return Result(data={"txn": t, "batch_id": batch_id, "write_seq": _seq(book)}, text=f"✏️ 改好了：{line(book, t)}")


def run_rm(book, values):
    txn_id = _id(values)
    with _write(book, "rm") as w:
        t = tx.delete(w, txn_id)
        batch_id = w.batch_id
    return Result(data={"deleted": txn_id, "txn": t, "batch_id": batch_id, "write_seq": _seq(book)},
                  text=f"🗑 刪掉了：{line(book, t)}\n要救回來：/money_undo")


def describe_rm(book, values):
    t = tx.get(book.conn, _id(values))
    return f"刪掉這一筆：{line(book, t)}"


def _revert_text(r, what="撤銷了"):
    return f"↩️ {what}（{r['changes']} 個改動）；反悔的話再撤銷一次：/money_revert {r['by']}"


def run_undo(book, values):
    last = batches.last_undoable(book.conn)
    with _write(book, "undo") as w:
        r = batches.revert(w, last["id"])
    r.update({"command": last["command"], "batch_id": r["by"], "write_seq": _seq(book)})
    return Result(data=r, text=_revert_text(r, f"撤銷了最近一次（{last['command']}）"))


def run_revert(book, values):
    batch_id = _id(values)
    with _write(book, "revert") as w:
        r = batches.revert(w, batch_id)
    r.update({"batch_id": r["by"], "write_seq": _seq(book)})
    return Result(data=r, text=_revert_text(r))


def describe_revert(book, values):
    b = batches.get(book.conn, _id(values))
    return f"撤銷那一次寫入（{b['command']}，{len(b['changes'])} 個改動）"


# ── 小日記、重新分類、可能重複 ─────────────────────────────────────────────────────────

def run_note(book, values):
    day = parse_day(_get(values, "date") or "今天", book.today)
    replace = _yes(_get(values, "replace"), "replace") if _get(values, "replace") else False
    with _write(book, "note") as w:
        r = notes.put(w, day, _get(values, "text") or "", replace=replace)
        batch_id = w.batch_id if w.changes else None
    r.update({"batch_id": batch_id, "write_seq": _seq(book)})
    return Result(data=r, text=f"📝 {_md(r['date'])} 小日記：{r['note']}")


def run_reclassify(book, values):
    with _write(book, "reclassify") as w:
        done = classify.reclassify(w)
        batch_id = w.batch_id if w.changes else None
    text = (f"分好了 {len(done)} 筆：" + "、".join(f"{d['desc']}→{d['category']}" for d in done)) if done else \
        "沒有能自動分的（剩下的請在明細一筆一筆選，改一次就會記住這家店）"
    return Result(data={"changed": done, "count": len(done), "batch_id": batch_id, "write_seq": _seq(book)}, text=text)


def _pair_text(book, p):
    return f"第 {p['pair_id']} 組（{p['reason']}）\n  較舊：{line(book, p['a'])}\n  較新：{line(book, p['b'])}"


def run_dedup(book, values):
    pair = _get(values, "id")
    if not pair:
        with _write(book, "dedup scan") as w:
            found = dedup.scan(w)
            batch_id = w.batch_id if w.changes else None
        pairs = dedup.pending(book.conn)
        text = "\n".join([f"新找到 {len(found)} 組可能重複" + ("；還沒處理的：" if pairs else "")]
                         + [_pair_text(book, p) for p in pairs])
        if pairs:
            text += "\n處理：/money_dedup <組> keep_both（兩筆都留）／delete_newer（刪較新的）／merge（合併）"
        return Result(data={"found": len(found), "pairs": pairs, "batch_id": batch_id, "write_seq": _seq(book)},
                      text=text)
    if not pair.isdigit():
        raise InvalidInput(f"組號「{pair}」要是數字", fields={"id": "要是數字"})
    action = _get(values, "action")
    if not action:
        raise InvalidInput("要寫怎麼處理", hint="keep_both（兩筆都留）／delete_newer（刪較新的）／merge（合併）",
                           fields={"action": "缺少"})
    with _write(book, "dedup") as w:
        r = dedup.resolve(w, int(pair), action, expect_a=_get(values, "expect_a"), expect_b=_get(values, "expect_b"))
        r["batch_id"] = w.batch_id
    r["write_seq"] = _seq(book)
    label = {"kept_both": "兩筆都留著", "deleted": "刪掉較新的那筆", "merged": "合併成一筆"}[r["status"]]
    return Result(data=r, text=f"第 {pair} 組：{label}。留下的是：{line(book, r['kept'])}")


# ── 列出、看一筆 ──────────────────────────────────────────────────────────────────

def _month_range(text):
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise InvalidInput(f"月份「{text}」看不懂", hint="寫成 YYYY-MM，例如 2030-01", fields={"month": "看不懂"})
    year, month = int(m.group(1)), int(m.group(2))
    end = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return f"{year:04d}-{month:02d}-01", end.isoformat()


def run_list(book, values):
    conn = book.conn
    n = _get(values, "n")
    if n and not n.isdigit():
        raise InvalidInput(f"筆數「{n}」要是數字", fields={"n": "要是數字"})
    limit = max(1, min(int(n or LIST_DEFAULT), LIST_MAX))
    if _get(values, "date"):
        start = end = parse_day(_get(values, "date"), book.today)
    elif _get(values, "month"):
        start, end = _month_range(_get(values, "month"))
    else:
        p = periods.get(conn, _get(values, "period"))
        start, end = p.start.isoformat(), p.end.isoformat()
    category = _get(values, "category")
    cat = refdata.find_category(conn, category) if category else None
    if category and cat is None:
        raise NotFound(f"找不到類別「{category}」", suggestions=refdata.category_suggestions(conn, category))
    group = refdata.group_by_name(conn, _get(values, "group")) if _get(values, "group") else None
    account = refdata.resolve_account(conn, _get(values, "account")) if _get(values, "account") else None
    kind = _get(values, "kind")
    if kind:
        kind = _word(kind, KIND_WORDS, "kind", "支出／收入／轉帳")
        if kind == "iou":
            kind = "transfer"
    page = tx.query(conn, start=start, end=end, kind=kind, category_id=cat and cat["id"],
                    group_id=group and group["id"], account_id=account and account["id"],
                    text=_get(values, "q") or _get(values, "keyword"), limit=limit)
    page["range"] = {"start": start, "end": end}
    head = f"{_md(start)}～{_md(end)}，共 {page['total']} 筆" + (f"（最近 {len(page['items'])} 筆）"
                                                             if page["total"] > len(page["items"]) else "")
    text = "\n".join([head, *(line(book, t) for t in page["items"])]) if page["items"] else \
        f"{_md(start)}～{_md(end)} 沒有符合的記帳"
    return Result(data=page, text=text)


def run_show(book, values):
    txn_id = _id(values)
    t = tx.get(book.conn, txn_id, include_deleted=True)
    t["history"] = [dict(r) for r in book.conn.execute(
        "SELECT a.at, a.actor, a.action, a.batch_id FROM audit_log a WHERE entity = 'transactions' AND entity_id = ? "
        "ORDER BY a.id", (txn_id,))]
    lines = [line(book, t) + ("（已刪除）" if t["status"] == "deleted" else "")]
    lines += [f"  品項：{i['name']}" + (f" {table.show(book.conn, i['amount'])}" if i.get("amount") is not None else "")
              for i in t["items"]]
    if t["note"]:
        lines.append(f"  備註：{t['note']}")
    if t["for_period"]:
        lines.append(f"  算進 {t['for_period']} 那一期")
    lines += [f"  {h['at'][:16].replace('T', ' ')} {h['action']}（批次 {h['batch_id']}）" for h in t["history"]]
    return Result(data=t, text="\n".join(lines))


# ── 畫面的讀 ───────────────────────────────────────────────────────────────────

def _piggy_visible(conn):
    """撲滿分頁要不要出現：有在用的證券帳戶，或開了投資／資金操盤。小數位數不是 0 的帳本先不開放撲滿（bridge/piggy.py）。"""
    if table.decimals(conn):
        return False
    if conn.execute("SELECT 1 FROM accounts WHERE kind = 'securities' AND active = 1 LIMIT 1").fetchone():
        return True
    return conn.execute("SELECT 1 FROM buckets WHERE id IN ('invest', 'trading') AND active = 1 LIMIT 1").fetchone() is not None


def read_meta(book, params):
    conn = book.conn
    now = clock.now()
    return {"write_seq": _seq(book), "today": now.date().isoformat(), "now": now.strftime("%H:%M"),
            "current": periods.current(conn).key, "default_account": settings.get(conn, "default_account"),
            "inbox": inbox.counts(conn), "piggy": _piggy_visible(conn),
            "currency": {"code": settings.get(conn, "currency.code"), "symbol": settings.get(conn, "currency.symbol"),
                         "decimals": table.decimals(conn)}}


def ref_payload(conn, *, keywords=False):
    """參考資料：類別（照用途、大類分組，也照種類分給「記一筆」的選單）、帳戶、圖示、今天、本期。"""
    now = clock.now()
    cats, icons, by_kind = [], {}, {}
    for b in refdata.tree(conn):
        for g in b["groups"]:
            items = [{"name": c["name"], "keywords": c["keywords"]} if keywords else c["name"] for c in g["categories"]]
            cats.append({"bucket": b["name"], "group": g["name"], "items": items})
            for c in g["categories"]:
                by_kind.setdefault(c["kind"], {}).setdefault(g["name"], []).append(c["name"])
                if c.get("icon"):
                    icons[c["name"]] = c["icon"]
            if g.get("icon"):
                icons[g["name"]] = g["icon"]
    return {
        "today": now.date().isoformat(), "now": now.strftime("%H:%M"),
        "period": periods.current(conn).to_dict(),
        "period_start_day": periods.start_day(conn),
        "default_account": settings.get(conn, "default_account"),
        "accounts": [{"name": a["name"], "kind": a["kind"], "aliases": a["aliases"], "purpose": a["purpose"]}
                     for a in refdata.accounts(conn)],
        "categories": cats,
        "categories_by_kind": {k: [{"group": g, "items": items} for g, items in v.items()] for k, v in by_kind.items()},
        "icons": icons,
        "rules": MEAL_RULE,
    }


def read_ref(book, params):
    conn = book.conn
    d = ref_payload(conn)
    cur = periods.current(conn)
    around = [("prev", periods.shift(conn, cur, -1)), ("current", cur), ("next", periods.shift(conn, cur, 1))]
    d["category_bucket"] = {c["name"]: {"bucket": c["bucket_id"], "bucket_name": c["bucket_name"], "living": c["is_living"]}
                            for c in refdata.categories(conn)}
    d["periods"] = [{"which": w, "key": p.key, "month": int(p.key[5:7])} for w, p in around]
    return d


def read_guess(book, params):
    cat, reason = classify.guess(book.conn, _get(params, "desc") or "", time=_get(params, "time"),
                                 date=_get(params, "date"))
    return {"category": cat["name"] if cat else None, "group": cat["group_name"] if cat else None, "reason": reason}


def read_items(book, params):
    category = _get(params, "category")
    if not category:
        return {"category": None, "items": []}
    d = quick.items(book.conn, category, limit=6)
    return {"category": d["category"], "items": [x["desc"] for x in d["recent"] + d["frequent"] + d["more"]]}


def read_passbook(book, params):
    nav = views.period_nav(book.conn, _get(params, "period"))
    key = nav["period"]["key"]
    pb = wallet.passbook(book.conn, key)
    pb.pop("period")                                     # 導覽的 period（物件）優先
    return {**pb, **nav, "wallet": wallet.summary(book.conn, key)}


def read_txn(book, params):
    return tx.get(book.conn, _id(params), include_deleted=True)


def read_inbox(book, params):
    return inbox.details(book.conn, 20)


def read_batches(book, params):
    n = _get(params, "limit")
    limit = max(1, min(int(n), 100)) if n and n.isdigit() else 20
    return {"batches": batches.recent(book.conn, limit)}


# ── 表 ───────────────────────────────────────────────────────────────────────

COMMANDS = (
    Cmd("add", "記一筆支出（類別是收入或轉帳類的，就照那一種記）", WRITE, txn_fields(), run_add,
        ai={"scenario": "使用者說花了多少錢、買了什麼、要記一筆支出時"}, positional=3, app=True, default=True),
    Cmd("in", "記一筆收入（不算花費）", WRITE, txn_fields(), run_in,
        ai={"scenario": "使用者說收到錢、薪水進來、退款回來時"}, positional=3, app=True),
    Cmd("transfer", "記一筆轉帳：存錢、繳卡費、還款、轉到別的帳戶（不算花費）", WRITE, txn_fields(), run_transfer,
        ai={"scenario": "使用者說存錢、繳信用卡費、還款、把錢轉到另一個帳戶時"}, positional=3, app=True),
    Cmd("iou", "代墊與欠款：幫別人先付、別人幫我付、收回、還錢", WRITE, txn_fields(), run_iou, positional=3, app=True),
    Cmd("edit", "改某一筆（只改有給的；- 清掉）", WRITE, edit_fields(), run_edit,
        ai={"scenario": "使用者說某一筆記錯了要改金額、類別、日期時（先查出編號）"}, positional=1, app=True),
    Cmd("rm", "刪掉某一筆（可以整批復原）", DANGER, [Field("id", "哪一筆（編號）", required=True)], run_rm,
        positional=1, app=True, describe=describe_rm),
    Cmd("undo", "撤銷最近一次寫入（整批）", WRITE, [], run_undo,
        ai={"scenario": "使用者說剛剛那筆記錯了、要取消剛剛的動作時"}, app=True),
    Cmd("revert", "撤銷指定的那一批寫入", DANGER, [Field("id", "哪一批（批次編號）", required=True)], run_revert,
        positional=1, app=True, describe=describe_revert),
    Cmd("note", "寫這一天的小日記（預設接在後面）", WRITE, [
        Field("date", "哪一天", required=True),
        Field("text", "內容", required=True),
        Field("replace", "整段換掉（yes／no）")], run_note, positional=2, app=True),
    Cmd("reclassify", "待分類的支出用現在的規則再猜一次", WRITE, [], run_reclassify, app=True),
    Cmd("dedup", "可能記重複的：不給 id＝重新找一次；給 id＝處理那一組（兩筆都留、刪掉新的、合併）", WRITE, [
        Field("id", "哪一組（不給＝重新找一次）"),
        Field("action", "keep_both／delete_newer／merge"),
        Field("expect_a", "第一筆最後修改的時間（畫面用）"),
        Field("expect_b", "第二筆最後修改的時間（畫面用）")], run_dedup, positional=2, app=True),
    Cmd("list", "最近幾筆（可以用期別、日期、類別、大類、帳戶、關鍵字、月份、種類篩）", READ, [
        Field("n", "筆數"),
        Field("period", "哪一期（例如 2030-01）"),
        Field("date", "哪一天"),
        Field("category", "類別"),
        Field("group", "大類"),
        Field("account", "帳戶"),
        Field("q", "找字"),
        Field("keyword", "關鍵字（舊版的名字，同 q）"),
        Field("month", "月份（YYYY-MM）"),
        Field("kind", "種類：支出／收入／轉帳")], run_list,
        ai={"scenario": "使用者問最近記了什麼、找某一筆、某類別記了哪些時"}, positional=1),
    Cmd("show", "看一筆的完整內容（品項、收據、改動紀錄）", READ, [Field("id", "哪一筆（編號）", required=True)],
        run_show, ai={"scenario": "使用者要看某一筆的細節時"}, positional=1),
)

READS = frozen({"meta": read_meta, "ref": read_ref, "guess": read_guess, "items": read_items, "passbook": read_passbook,
                "txn": read_txn, "inbox": read_inbox, "batches": read_batches})
EXPORTS = frozen({})
