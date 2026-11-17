"""週報／月報檢討：哪些到期、叫 AI 寫、驗證與存檔、推送摘要、回覆、套用建議分配。

- 到期：週報＝這一週，檢討日＝`notify.weekday`（預設 4＝週五），那天還沒到就看上一週；跟數字週報同一天晚上一起送
  （預設開，`review.weekly=false` 可以關掉，見 notify.py）。跟月報一樣故意在那一段還沒結束時做，
  好讓使用者週末還來得及調整；一週一份，週末補完也不會重做；
  月報＝期名那個月的**日曆月底前 review.month_days_before_end 天**（預設 1：2026-10 期在 10/30 做，資料到那天，
  好讓使用者月底前想下個月的分配）。
  每種只補最近 2 份，往前第 3、4 份沒做的標 skipped；比第一筆記帳還早的不建。
- 一份＝(kind, key) 唯一。狀態：running（30 分鐘沒結束算卡住，可以重跑）→ done／failed（最多試 3 次）／
  deferred（額度用完，next_attempt_after 之後再試，不算失敗）／skipped（太舊、或那段時間沒有任何記帳）。
- AI 的呼叫放在兩次寫入之間（先標 running、COMMIT，叫完再寫結果），不會把資料庫鎖住好幾分鐘。
- 存之前：去掉 emoji 與「❌」（聊天助理看到「❌」會當成失敗）、截長度；建議分配要加總等於收入、
  生活支出＝各大類加總、存錢帳戶只能是給的那幾個，不合就標 valid=false（review_apply 會擋），文字照存。
- 建議不會自動套用：畫面按「照這個分」或 `/money_review_apply <id>` 才寫進 period_plans（source=review）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from .. import clock, db
from ..db import Writer, row_dict
from ..errors import Conflict, InvalidInput, NotFound
from . import allowance, periods, plans, refdata, report, settings, stats

KEEP_RECENT = 2
LOOKBACK = 4
MAX_ATTEMPTS = 3
STALE_RUNNING = timedelta(minutes=30)
EMPTY = "這段時間沒有任何記帳"
DEFER = timedelta(hours=3)
GRADES = ("A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D")
DIGEST_LIMIT = 3500
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣]")
JSON_FIELDS = ("good", "watch", "strategy", "suggestion", "problems", "metrics")


def clean(text: Any, limit: int = 0) -> str:
    out = EMOJI_RE.sub("", str(text or "")).replace("  ", " ").strip()
    return out[:limit].rstrip() if limit and len(out) > limit else out


def _now() -> datetime:
    return clock.now()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------- 讀

def present(r: dict | sqlite3.Row) -> dict[str, Any]:
    d = row_dict(r) if isinstance(r, sqlite3.Row) else dict(r)
    for k in JSON_FIELDS:
        d[k] = json.loads(d[k]) if d.get(k) else ([] if k in ("good", "watch", "strategy", "problems") else None)
    for k in ("good", "watch", "strategy"):                  # 舊的存成純字串，讀出來一律補成 {text, level}
        d[k] = _points(d[k])
    start, end = date.fromisoformat(d["start_date"]), date.fromisoformat(d["end_date"])
    d["kind_name"] = report.KIND_NAMES[d["kind"]]
    d["label"] = (f"{d['key']} 期" if d["kind"] == "month" else d["key"]) + f"（{start.month}/{start.day}–{end.month}/{end.day}）"
    when = (d.get("finished_at") or "")[:10]
    d["meta"] = (f"{when} 由 AI（{d['model']}）整理" if d["status"] == "done" and d.get("model") and d.get("source") != "manual"
                 else (f"{when} 手動存的" if d["status"] == "done" else ""))
    return d


def get(conn: sqlite3.Connection, review_id: int) -> dict[str, Any]:
    r = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    if r is None:
        raise NotFound(f"找不到檢討 #{review_id}", hint="到畫面的「檢討」分頁看有哪些")
    return present(r)


def by_key(conn: sqlite3.Connection, kind: str, key: str) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM reviews WHERE kind = ? AND key = ?", (kind, key)).fetchone()
    return present(r) if r else None


def all_reviews(conn: sqlite3.Connection, kind: str | None = None, *, limit: int = 30,
                status: str | None = None) -> list[dict[str, Any]]:
    where, args = [], []
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if status:
        where.append("status = ?")
        args.append(status)
    sql = "SELECT * FROM reviews" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY end_date DESC, kind LIMIT ?"
    return [present(r) for r in conn.execute(sql, (*args, limit))]


def latest(conn: sqlite3.Connection, kind: str = "month") -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM reviews WHERE kind = ? AND status = 'done' ORDER BY end_date DESC LIMIT 1",
                     (kind,)).fetchone()
    return present(r) if r else None


# ---------------------------------------------------------------- 到期

def _candidates(conn: sqlite3.Connection, kind: str, today: date) -> list[tuple[str, date, date]]:
    out = []
    if kind == "week":
        weekday = int(settings.get(conn, "notify.weekday", 4))
        monday = today - timedelta(days=today.weekday())
        if today.weekday() < weekday:                         # 這一週的檢討日還沒到：看上一週
            monday -= timedelta(days=7)
        for i in range(LOOKBACK):
            key = periods.week_key(monday - timedelta(days=7 * i))
            start, end = periods.week_range(key)
            out.append((key, start, end))
    else:
        days = int(settings.get(conn, "review.month_days_before_end", 1))
        p = periods.period_for(conn, today)
        if month_due_date(p.key, days) > today:
            p = periods.shift(conn, p, -1)
        for _ in range(LOOKBACK):
            out.append((p.key, p.start, p.end))
            p = periods.shift(conn, p, -1)
    return out


def month_due_date(key: str, days_before_end: int) -> date:
    """期名那個月（2026-10）的日曆月底往前幾天＝那一期的月檢討哪天做。"""
    import calendar
    y, m = int(key[:4]), int(key[5:7])
    return date(y, m, calendar.monthrange(y, m)[1]) - timedelta(days=days_before_end)


def month_key_due(conn: sqlite3.Connection, today: date | None = None) -> str | None:
    """今天這一期（或剛過的那一期）的月檢討已經到期就回期名；最近一期的到期日還沒到回 None。"""
    return _candidates(conn, "month", today or clock.today())[0][0]


def due_key(conn: sqlite3.Connection, kind: str, today: date | None = None) -> str:
    """最近一份該檢討的：週＝這一週（檢討日到了）或上一週；月＝到期日（月底前一天）已經到的那一期（跟 due() 同一個規則）。"""
    return _candidates(conn, kind, today or clock.today())[0][0]


def due(conn: sqlite3.Connection, today: date | None = None) -> list[dict[str, Any]]:
    """該做的（action=run）和該標略過的（action=skip），月報在前、舊的在前。"""
    today = today or clock.today()
    now = _now()
    first = conn.execute("SELECT MIN(date) FROM transactions WHERE status = 'active'").fetchone()[0]
    if first is None:
        return []
    out = []
    kinds = ("month", "week") if settings.get(conn, "review.weekly", True) else ("month",)
    for kind in kinds:
        items = []
        for i, (key, start, end) in enumerate(_candidates(conn, kind, today)):
            if end.isoformat() < first:
                continue
            row = by_key(conn, kind, key)
            if row and row["status"] == "skipped" and row["error"] == EMPTY and i < KEEP_RECENT and                     conn.execute("SELECT 1 FROM transactions WHERE status = 'active' AND date BETWEEN ? AND ? LIMIT 1",
                                 (start.isoformat(), end.isoformat())).fetchone():
                items.append({"kind": kind, "key": key, "action": "run", "reason": "之前沒有記帳，現在補記了"})
                continue
            if row and row["status"] in ("done", "skipped"):
                continue
            if i >= KEEP_RECENT:
                if not row or row["status"] != "running":
                    items.append({"kind": kind, "key": key, "action": "skip", "reason": f"太久以前，只補最近 {KEEP_RECENT} 份"})
                continue
            if row and row["status"] == "running" and row["started_at"] and \
                    datetime.fromisoformat(row["started_at"]) > now - STALE_RUNNING:
                continue
            if row and row["status"] == "deferred" and row["next_attempt_after"] and \
                    datetime.fromisoformat(row["next_attempt_after"]) > now:
                continue
            if row and row["status"] == "failed" and row["attempts"] >= MAX_ATTEMPTS:
                continue
            items.append({"kind": kind, "key": key, "action": "run", "reason": "到期" if not row else f"重試（{row['status']}）"})
        out += list(reversed(items))
    return out


# ---------------------------------------------------------------- 寫

def _upsert(w: Writer, kind: str, key: str, start: str, end: str, **fields: Any) -> int:
    before = row_dict(w.conn.execute("SELECT * FROM reviews WHERE kind = ? AND key = ?", (kind, key)).fetchone())
    for k in JSON_FIELDS:
        if k in fields and fields[k] is not None and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    fields["updated_at"] = w.now
    if before is None:
        data = {"kind": kind, "key": key, "start_date": start, "end_date": end, "created_at": w.now, **fields}
        rid = w.conn.execute(f"INSERT INTO reviews ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                             tuple(data.values())).lastrowid
    else:
        rid = before["id"]
        w.conn.execute(f"UPDATE reviews SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), rid))
    after = row_dict(w.conn.execute("SELECT * FROM reviews WHERE id = ?", (rid,)).fetchone())
    w.audit("update" if before else "create", "reviews", rid, before, after)
    return rid


def _write(conn: sqlite3.Connection, actor: str, command: str):
    source = actor.split(":", 1)[0]
    return db.write(conn, actor=actor, source=source, command=command)


def snapshot(rep: dict[str, Any]) -> dict[str, Any]:
    """存進 reviews.metrics 的報表快照（不含明細與小日記）。"""
    return {k: v for k, v in rep.items() if k not in ("transactions", "notes", "daily", "transactions_truncated")}


def skip(conn: sqlite3.Connection, kind: str, key: str, reason: str, *, actor: str = "review-job") -> dict[str, Any]:
    rng = report.resolve(conn, kind, key)
    with _write(conn, actor, f"review skip {kind} {key}") as w:
        rid = _upsert(w, kind, rng["key"], rng["start"].isoformat(), rng["end"].isoformat(), status="skipped",
                      error=reason, source=actor.split(":", 1)[0])
    return get(conn, rid)


# ---------------------------------------------------------------- 給 AI 的提示與格式

def _soft_caps(conn: sqlite3.Connection, rep: dict[str, Any]) -> list[dict[str, Any]]:
    """細項的「大概多少」（軟上限）與實際花了多少。

    設計：細項不設硬上限，也不一超過就提醒；只讓人大概定一個合理用量（軟上限），
    檢討時再由 AI 看有沒有太誇張。所以這些數字只給 AI 當參考，
    網站不亮燈、也不擋。
    """
    out = []
    for g in stats.caps(conn, rep["key"] if rep["kind"] == "month" else None):
        for c in g["categories"]:
            if c["cap_basis"] == "each" and c["cap"]:
                out.append({"item": c["name"], "group": g["name"], "each": c["cap"],
                            "over_times": c["over_each"], "spent": c["spent"]})
            elif c["cap_period"]:
                out.append({"item": c["name"], "group": g["name"], "about": c["cap_period"], "spent": c["spent"]})
    return out


def _context(conn: sqlite3.Connection, rep: dict[str, Any]) -> dict[str, Any]:
    """月報的建議分配要用到的：基準收入、下一期、可以用的大類與存錢帳戶；兩種檢討都帶「大概多少」。"""
    caps = _soft_caps(conn, rep)
    quota = [{"level": a["level"], "text": a["text"], "tip": a["tip"]}
             for a in allowance.check(conn, rep["key"] if rep["kind"] == "month" else None) if a["level"] != "info"][:6]
    money = currency(conn)
    if rep["kind"] != "month":
        return {"currency": money, **{k: v for k, v in (("soft_caps", caps), ("allowance", quota)) if v}}
    a = rep["allocation"]
    base = a["income"] or a["income_expected"] or sum((a["plan"] or {}).values())
    nxt = periods.shift(conn, periods.get(conn, rep["key"]), 1).key
    groups = [g["name"] for g in refdata.groups(conn) if g["is_living"]]
    accounts = [x["name"] for x in refdata.accounts(conn)
                if x["kind"] in ("bank", "securities", "e_wallet", "cash") and (x.get("reserve_group") or x["kind"] != "cash")]
    basis = "這一期實際收入" if a["income"] else ("這一期預計收入" if a["income_expected"] else "這一期計畫的六大類合計")
    return {"base": int(base or 0), "basis": basis, "next_period": nxt, "groups": groups, "accounts": accounts,
            "soft_caps": caps, "allowance": quota, "currency": money,
            "buckets": [{"id": b["id"], "name": b["name"]} for b in plans.buckets(conn)],   # 使用者自己加的也算
            "giving_now": int(plans.get(conn, nxt, with_moves=False)["buckets"].get("giving") or 0)}


def currency(conn: sqlite3.Connection) -> dict[str, Any]:
    """帳本的幣別設定（代號、符號、小數位數）：寫檢討的提示要講清楚金額的單位。"""
    return {"code": str(settings.get(conn, "currency.code") or ""),
            "symbol": str(settings.get(conn, "currency.symbol") or ""),
            "decimals": int(settings.get(conn, "currency.decimals") or 0)}


def unit_text(money: dict[str, Any] | None) -> str:
    """「金額單位是…」：小數位數 0＝資料裡的整數就是金額；有小數＝存的是最小單位（100 代表 1.00）。"""
    money = money or {}
    code, symbol, decimals = money.get("code") or "", money.get("symbol") or "", int(money.get("decimals") or 0)
    name = f"{code}（{symbol}）" if code and symbol else (code or symbol or "這本帳的幣別")
    if decimals <= 0:
        return f"金額都是整數，單位是 {name}"
    one = "1." + "0" * decimals
    return f"金額都是整數，單位是 {name} 的最小單位（資料裡的 {10 ** decimals} 代表 {one}），寫給他看時換回一般金額"


def _bucket_ids(ctx: dict[str, Any]) -> list[str]:
    """這本帳現在有哪些用途（ctx 沒給就退回核心的六個）。"""
    return [b["id"] for b in ctx.get("buckets") or []] or list(plans.BUCKET_IDS)


def schema(ctx: dict[str, Any], kind: str) -> dict[str, Any]:
    # 每一點都要標重要度（畫面會用小圖示標出哪些重要）：
    # key＝這一份裡最該記住的（收合時只看得到這些），normal＝其他。
    point = {"type": "object", "additionalProperties": False, "required": ["text", "level"],
             "properties": {"text": {"type": "string", "description": "一句話，60 字以內"},
                            "level": {"type": "string", "enum": ["key", "normal"],
                                      "description": "key＝最該記住的（每一組最多 1–2 點）"}}}
    items = {"type": "array", "items": point, "minItems": 1, "maxItems": 4}
    props: dict[str, Any] = {
        "grade": {"type": "string", "enum": list(GRADES)},
        "title": {"type": "string", "description": "一句話標題，20 字以內"},
        "summary": {"type": "string", "description": "200 字以內"},
        "good": items, "watch": items, "strategy": items,
    }
    required = ["grade", "title", "summary", "good", "watch", "strategy"]
    if kind == "month" and ctx.get("base"):
        props["suggestion"] = {
            "type": "object", "additionalProperties": False,
            "required": ["buckets", "group_budgets", "saving_priority", "reason"],
            "properties": {
                "buckets": {"type": "object", "additionalProperties": False, "required": _bucket_ids(ctx),
                            "properties": {b: {"type": "integer", "minimum": 0} for b in _bucket_ids(ctx)}},
                "group_budgets": {"type": "object", "additionalProperties": False, "required": ctx["groups"],
                                  "properties": {g: {"type": "integer", "minimum": 0} for g in ctx["groups"]}},
                "saving_priority": {"type": "array", "items": {"type": "string", "enum": ctx["accounts"] or [""]}},
                "reason": {"type": "string"},
            }}
        required.append("suggestion")
    return {"type": "object", "additionalProperties": False, "required": required, "properties": props}


def prompt(rep: dict[str, Any], ctx: dict[str, Any]) -> str:
    month = rep["kind"] == "month"
    span = "下個月" if month else "下週"
    lines = [
        f"你是使用者的理財教練。下面是他 {rep['label']} 的記帳資料（JSON，{unit_text(ctx.get('currency'))}）。",
        f"請用繁體中文寫一份{report.KIND_NAMES[rep['kind']]}檢討，語氣像朋友：直接、具體、引用數字，不說教。",
        "",
        "規則：",
        "- 不要用任何 emoji 或符號圖示（❌、✅、★ 這類都不要）。",
        "- grade：A 很好、B 還可以、C 要注意、D 失控；看生活支出跟預算比、超額天數、存錢有沒有照計畫。",
        "- title：一句話標題，20 字以內。summary：200 字以內，講這段時間花錢的樣子。",
        "- good、watch：各 2–4 點，每點 60 字以內，要引用資料裡的數字、品項或店名。",
        "- 每一點都要標 level：**key＝這一組裡最該記住的那 1–2 點**（他收合時只看得到 key），其他 normal。",
        "- 順序也要有脈絡：同一組裡先寫最重要、影響最大的，再寫細節；不要把不相干的事混在一句裡。",
        f"- strategy：2–4 點，{span}具體可以做的事（例：「午餐控制在 120 以內」「每週一天不買飲料」）。",
        "- 生活支出＝日常開銷；固定支出、捐贈、存錢、還債另外算，不要把它們當成亂花。",
        "- 資料裡的 notes 是他自己寫的小日記，可以拿來理解當天為什麼花比較多。",
    ]
    if month and ctx.get("base"):
        lines += [
            f"- suggestion：{ctx['next_period']} 期的薪水分配建議。",
            f"  - buckets {len(_bucket_ids(ctx))} 個整數（"
            + "、".join(f"{b['id']} {b['name']}" for b in (ctx.get("buckets") or
                        [{"id": b, "name": plans.DEFAULT_LABELS.get(b, b)} for b in plans.BUCKET_IDS]))
            + f"），加總必須剛好等於 {ctx['base']}（{ctx['basis']}）。",
            "  - group_budgets 是生活支出各大類的預算，加總必須等於 buckets.living；大類只能用："
            + "、".join(ctx["groups"]) + "。",
            "  - saving_priority：存錢的先後順序，只能從這些帳戶選：" + "、".join(ctx["accounts"]) + "（低於底線的優先）。",
            "  - reason：一兩句說為什麼這樣調；捐贈照他原本的比例，不要建議減少。",
        ]
    if ctx.get("allowance"):
        lines += ["", "- 額度提醒（電腦算好的，是真的問題，**一定要在 watch 或 strategy 裡回應**，講原因和具體怎麼調）："
                  + "；".join(f"{a['text']}（{a['tip']}）" if a["tip"] else a["text"] for a in ctx["allowance"])]
    if ctx.get("soft_caps"):
        parts = "、".join(
            (f"{c['item']} 每次大概 {c['each']:,}（這一期超過 {c['over_times']} 次、合計 {c['spent']:,}）"
             if "each" in c else f"{c['item']} 大概 {c['about']:,}／實際 {c['spent']:,}")
            for c in ctx["soft_caps"][:12])
        lines += ["", f"- 他心裡「大概多少」的參考值（軟上限，不是規定）：{parts}。",
                  "  只有**誇張**才講（例如到兩三倍、或吃掉整個大類），差一點點不要唸他。"]
    lines += ["", "資料：", json.dumps(rep, ensure_ascii=False, default=str)]
    return "\n".join(lines)


def _points(raw: Any) -> list[dict[str, str]]:
    """把一組重點正規化成 [{"text","level"}]：舊資料（純字串）也吃得下，沒標的就當 normal。"""
    out = []
    for x in (raw or [])[:4]:
        text = clean(x.get("text") if isinstance(x, dict) else x, 160)
        if not text:
            continue
        level = (x.get("level") if isinstance(x, dict) else "") or "normal"
        out.append({"text": text, "level": "key" if level == "key" else "normal"})
    return out


def validate(ctx: dict[str, Any], kind: str, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    problems: list[str] = []
    out: dict[str, Any] = {
        "grade": clean(data.get("grade"), 3) or None, "title": clean(data.get("title"), 40),
        "summary": clean(data.get("summary"), 400),
        **{k: _points(data.get(k)) for k in ("good", "watch", "strategy")},
    }
    if out["grade"] not in GRADES:
        problems.append(f"等第「{out['grade']}」不在 A–D 裡")
        out["grade"] = None
    sug = data.get("suggestion") if kind == "month" else None
    if sug:
        buckets = {b: int((sug.get("buckets") or {}).get(b) or 0) for b in _bucket_ids(ctx)}
        groups = {g: int(v or 0) for g, v in (sug.get("group_budgets") or {}).items()}
        blocking = []
        unknown = [g for g in groups if g not in ctx.get("groups", [])]
        if unknown:
            blocking.append("不認得的大類：" + "、".join(unknown))
        if any(v < 0 for v in [*buckets.values(), *groups.values()]):
            blocking.append("有負的金額")
        total = sum(buckets.values())
        if ctx.get("base") and total != ctx["base"]:
            blocking.append(f"分配加總 {total:,} 不等於{ctx['basis']} {ctx['base']:,}")
        if groups and sum(groups.values()) != buckets.get("living", 0):
            blocking.append(f"各大類加總 {sum(groups.values()):,} 不等於生活支出 {buckets.get('living', 0):,}")
        accounts = [a for a in (sug.get("saving_priority") or []) if a]
        dropped = [a for a in accounts if a not in ctx.get("accounts", [])]
        if dropped:
            problems.append("拿掉不認得的帳戶：" + "、".join(dropped))
        if ctx.get("giving_now") and buckets.get("giving", 0) < ctx["giving_now"]:
            problems.append(f"建議的捐贈 {buckets['giving']:,} 比 {ctx['next_period']} 期目前的 {ctx['giving_now']:,} 少，套用前自己確認")
        problems += blocking
        out["suggestion"] = {"period": ctx.get("next_period"), "base": ctx.get("base"), "buckets": buckets,
                             "group_budgets": groups, "saving_priority": [a for a in accounts if a not in dropped],
                             "reason": clean(sug.get("reason"), 200), "valid": not blocking}
    else:
        out["suggestion"] = None
    return out, problems


# ---------------------------------------------------------------- 跑

def run_one(conn: sqlite3.Connection, kind: str, key: str | None, provider, *,
            actor: str = "review-job") -> dict[str, Any]:
    """請「寫檢討的提供者」（llm.py 的介面）寫一份：先標 running（COMMIT）、叫提供者（不在交易裡）、再存結果。
    額度用完＝延後（DEFER 之後再試）；失敗＝標失敗附原因；成功照 validate 存（對不起來的照存、標出來）。"""
    from . import llm
    provider = llm.require(provider)
    rep = report.build(conn, kind, key)
    if rep["count"] == 0:
        return skip(conn, kind, rep["key"], EMPTY, actor=actor)
    ctx = _context(conn, rep)
    model = settings.get(conn, "review.model") or None
    previous = by_key(conn, kind, rep["key"])
    with _write(conn, actor, f"review start {kind} {rep['key']}") as w:
        _upsert(w, kind, rep["key"], rep["start"], rep["end"], status="running", started_at=_iso(_now()),
                attempts=(previous["attempts"] if previous else 0) + 1, error=None, next_attempt_after=None,
                metrics=snapshot(rep), model=model, source=actor.split(":", 1)[0])
    try:
        res = llm.call(provider, prompt(rep, ctx), schema(ctx, kind), {"model": model, "timeout": 300})
    except llm.ProviderQuota as e:
        later = _now() + DEFER
        with _write(conn, actor, f"review defer {kind} {rep['key']}") as w:
            rid = _upsert(w, kind, rep["key"], rep["start"], rep["end"], status="deferred", next_attempt_after=_iso(later),
                          error=clean(f"AI 的額度用完，{later:%m/%d %H:%M} 之後再試" + (f"（重置：{e.resets}）" if e.resets else "")))
        return get(conn, rid)
    except llm.ProviderFailed as e:
        with _write(conn, actor, f"review fail {kind} {rep['key']}") as w:
            rid = _upsert(w, kind, rep["key"], rep["start"], rep["end"], status="failed", error=clean(str(e), 800),
                          finished_at=_iso(_now()))
        return get(conn, rid)
    data, problems = validate(ctx, kind, res["structured"])
    with _write(conn, actor, f"review save {kind} {rep['key']}") as w:
        rid = _upsert(w, kind, rep["key"], rep["start"], rep["end"], status="done", **data, problems=problems,
                      model=res.get("model") or model, cost_usd=res.get("cost_usd"), finished_at=_iso(_now()),
                      error=None, sent_at=None)
    return get(conn, rid)


def run_due(conn: sqlite3.Connection, provider, *, actor: str = "review-job",
            today: date | None = None) -> dict[str, Any]:
    """該做的都做：太舊或沒資料的標略過、其他請提供者寫；額度用完就停（剩下的下次）。沒有提供者先丟 LlmUnavailable。"""
    from . import llm
    provider = llm.require(provider)
    out: dict[str, list] = {"done": [], "skipped": [], "deferred": [], "failed": []}
    for item in due(conn, today):
        if item["action"] == "skip":
            rv = skip(conn, item["kind"], item["key"], item["reason"], actor=actor)
        else:
            rv = run_one(conn, item["kind"], item["key"], provider, actor=actor)
        out[rv["status"]].append({"id": rv["id"], "kind": rv["kind"], "key": rv["key"], "label": rv["label"],
                                  "error": rv.get("error"), "grade": rv.get("grade")})
        if rv["status"] == "deferred":
            break                                            # 額度用完：剩下的等下次
    return out


def save_manual(w: Writer, kind: str, key: str | None, data: dict[str, Any]) -> int:
    """直接存一份檢討（例：聊天助理自己整理的、或測試）；一樣經過 validate。"""
    rep = report.build(w.conn, kind, key, with_transactions=False)
    ctx = _context(w.conn, rep)
    clean_data, problems = validate(ctx, kind, data)
    return _upsert(w, kind, rep["key"], rep["start"], rep["end"], status="done", **clean_data, problems=problems,
                   metrics=snapshot(rep), model=None, finished_at=w.now, source="manual", sent_at=None, error=None)


# ---------------------------------------------------------------- 推送、回覆、套用

def digest_text(rv: dict[str, Any], names: dict[str, str] | None = None, places: int = 0) -> str:
    """一份檢討的摘要文字；places：帳本的小數位數（建議的金額是最小單位，照它印）。"""
    lines = [f"【{rv['kind_name']}】{rv['label']}" + (f"　等第 {rv['grade']}" if rv["grade"] else "")]
    if rv["title"]:
        lines.append(rv["title"])
    if rv["summary"]:
        lines.append(rv["summary"])
    for name, key in (("做得好", "good"), ("要注意", "watch"), ("下個月" if rv["kind"] == "month" else "下週", "strategy")):
        if rv[key]:
            lines.append(name + "：")
            # 重要的用「※」不用 emoji：CLI 的 --json 不能有 emoji（聊天助理會解析）
            lines += [("※ " if p["level"] == "key" else "・") + p["text"] for p in rv[key]]
    s = rv.get("suggestion")
    if s and s.get("valid"):
        names = names or plans.DEFAULT_LABELS
        parts = "、".join(f"{names.get(b, b)} {report.money(v, places)}" for b, v in s["buckets"].items() if v)
        lines.append(f"建議 {s['period']} 期分配：{parts}（畫面的檢討頁按「照這個分」，或打 /money_review_apply {rv['id']}）")
    return clean("\n".join(lines))


def pending_digest(conn: sqlite3.Connection) -> dict[str, Any]:
    """還沒推送的檢討，組成一段 ≤3,500 字的文字；放不下的留到下次。"""
    rows = [present(r) for r in conn.execute(
        "SELECT * FROM reviews WHERE status = 'done' AND sent_at IS NULL ORDER BY end_date, kind")]
    parts, ids = [], []
    names = plans.bucket_names(conn)
    for rv in rows:
        text = digest_text(rv, names, report.decimals_of(conn))
        if parts and len("\n\n".join([*parts, text])) > DIGEST_LIMIT:
            break
        parts.append(text[:DIGEST_LIMIT])
        ids.append(rv["id"])
    rest = len(rows) - len(ids)
    body = "\n\n".join(parts)
    if rest:
        body += f"\n\n（還有 {rest} 份，下次再送，或到記帳本的「檢討」分頁看）"
    return {"text": body, "ids": ids, "count": len(ids), "remaining": rest}


def mark_sent(w: Writer, ids: list[int]) -> None:
    for rid in ids:
        before = row_dict(w.conn.execute("SELECT * FROM reviews WHERE id = ?", (rid,)).fetchone())
        w.conn.execute("UPDATE reviews SET sent_at = ?, updated_at = ? WHERE id = ?", (w.now, w.now, rid))
        w.audit("update", "reviews", rid, before, row_dict(w.conn.execute("SELECT * FROM reviews WHERE id = ?", (rid,)).fetchone()))


def reply(w: Writer, review_id: int, text: str) -> dict[str, Any]:
    get(w.conn, review_id)
    text = (text or "").strip()
    if not text:
        raise InvalidInput("回覆是空的")
    before = row_dict(w.conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone())
    w.conn.execute("UPDATE reviews SET reply = ?, replied_at = ?, updated_at = ? WHERE id = ?", (text, w.now, w.now, review_id))
    w.audit("update", "reviews", review_id, before,
            row_dict(w.conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()))
    return get(w.conn, review_id)


def apply_plan(w: Writer, review_id: int, period: str | None = None, *, force: bool = False) -> dict[str, Any]:
    rv = get(w.conn, review_id)
    s = rv.get("suggestion")
    if rv["kind"] != "month" or not s:
        raise InvalidInput(f"檢討 #{review_id} 沒有建議分配", hint="只有月報會給下期的分配建議")
    if not s.get("valid") and not force:
        raise Conflict("這份建議的數字對不起來，先不套用：" + "；".join(rv["problems"]),
                       hint="到規劃頁自己調，或加 force=yes 照套")
    target = periods.get(w.conn, period or s.get("period")).key
    plan = plans.set_plan(w, target, buckets=s["buckets"], group_budgets=s["group_budgets"] or None, source="review",
                          review_id=str(review_id))
    before = row_dict(w.conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone())
    w.conn.execute("UPDATE reviews SET applied_at = ?, applied_period = ?, updated_at = ? WHERE id = ?",
                   (w.now, target, w.now, review_id))
    w.audit("update", "reviews", review_id, before,
            row_dict(w.conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()))
    return {"review_id": review_id, "period": target, "plan": plan}
