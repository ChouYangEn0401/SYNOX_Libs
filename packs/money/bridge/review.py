"""檢討（W7）：檢討清單與全文、套用建議、回覆、AI 寫完存回來、週／月報表的數字、該做哪一份、一批混合紀錄。

這個工具自己不認得任何 AI：SYNOX 的 AI 讀 report（review=yes 時連寫檢討的規則一起給）→ 照手冊寫 → 呼叫 review_save；
或是有人把「寫檢討的提供者」掛到工具上（bridge/writer.py，介面在 engine/services/llm.py），review_run 就請它寫；
沒有提供者時 review_run 回 llm_unavailable（什麼都不寫），其他照常。
存的時候照原本的規則驗證（等第、每組最多 4 點、建議分配的加總要合），對不起來的照存但標出來、套用時會擋。
排程定時跑 review_due：回覆的那段字就是要送給使用者的週報／檢討書（回覆了就算送出），另外列出該寫的檢討。
欄位名是契約（app/js/routes.js 照這些名字送值）：可以加，不要改名或拿掉。
"""

import json
import re

from synox.packs import kit

from .flow import engine, int_of, json_of, source_of, stamp, text_of, write, yes
from .table import READ, WRITE, Cmd, Field, Result, frozen
from .table import decimals, show as money

DOMAIN = "review"

KINDS = ("week", "month")
POINT_GROUPS = (("good", "做得好"), ("watch", "要注意"), ("strategy", "下一步"))
MAX_POINTS = 4
FIELD_LIMIT = 2000
KEY_MARKS = ("!", "！", "※", "*")
RESULT_TYPES = (("expense", "支出"), ("income", "收入"), ("transfer", "轉帳"), ("iou", "代墊／欠款"), ("day_note", "小日記"),
                ("card_statement", "信用卡帳單"), ("installment_purchase", "分期購買"), ("balance", "餘額核對"),
                ("bill", "帳單"), ("invest_pnl", "投資損益"), ("holdings", "持股"), ("trade", "交易"))
STATUS_WORDS = (("created", "記好了"), ("would_create", "會記"), ("replaced", "換掉了"), ("replayed", "之前記過"),
                ("possible_duplicate", "可能重複"), ("unchanged", "沒變"))


def _kind(value, default=None):
    text = str(value or "").strip().lower()
    if not text:
        return default
    words = {"week": "week", "週": "week", "周": "week", "週報": "week", "month": "month", "月": "month", "月報": "month",
             "期": "month"}
    if text not in words:
        raise kit.AppError(f"種類「{value}」不對，要寫 week（週）或 month（月）", code="invalid", status=400)
    return words[text]


def _brief(rv: dict) -> dict:
    return {k: rv.get(k) for k in ("id", "kind", "kind_name", "key", "label", "status", "grade", "title", "error",
                                   "finished_at", "sent_at", "applied_period", "next_attempt_after")}


# ── 畫面的讀 ───────────────────────────────────────────────────────────────────

def read_reviews(book, params) -> dict:
    e = engine()
    conn = book.conn
    nav = e["views"].period_nav(conn, text_of(params, "period") or None)
    kind = _kind(text_of(params, "kind"))
    return {**nav, "reviews": [_brief(rv) for rv in e["reviews"].all_reviews(conn, kind, limit=60)],
            "due": [{**d, "kind_name": e["report"].KIND_NAMES[d["kind"]]} for d in e["reviews"].due(conn)]}


def read_review(book, params) -> dict:
    e = engine()
    conn = book.conn
    rv = e["reviews"].get(conn, int_of(params.get("id"), "檢討"))
    s = rv.get("suggestion")
    rv["current_plan"] = e["plans"].get(conn, s["period"], with_moves=False) if s and s.get("period") else None
    rv["bucket_names"] = e["plans"].bucket_names(conn)
    return rv


# ── 套用、回覆 ─────────────────────────────────────────────────────────────────

def run_review_apply(book, values) -> Result:
    e = engine()
    rid = int_of(values.get("id"), "檢討")
    with write(book, "review_apply") as w:
        r = e["reviews"].apply_plan(w, rid, text_of(values, "period") or None, force=bool(yes(values.get("force"), False)))
    data = stamp(book, w, r)
    names = e["plans"].bucket_names(book.conn)
    parts = "、".join(f"{names.get(b, b)} {money(book.conn, v)}" for b, v in r["plan"]["buckets"].items() if v)
    return Result(data=data, text=f"{r['period']} 期的計畫改成檢討 #{rid} 的建議：{parts}")


def run_review_reply(book, values) -> Result:
    e = engine()
    with write(book, "review_reply") as w:
        rv = e["reviews"].reply(w, int_of(values.get("id"), "檢討"), text_of(values, "text"))
    data = stamp(book, w, {"id": rv["id"], "reply": rv["reply"]})
    return Result(data=data, text=f"記下了你對檢討 #{rv['id']} 的回覆")


# ── AI 寫完存回來 ───────────────────────────────────────────────────────────────

def _points(value, what: str, notes: list):
    """一組重點：JSON 清單（[{"text","level"}] 或字串），或一行一點／分號分開的字（開頭寫 ! 或 ※＝重要）。"""
    if isinstance(value, list):
        items = value
    else:
        text = str(value or "").strip()
        if not text:
            return []
        if text.startswith("["):
            items = json_of(text, what)
            if not isinstance(items, list):
                raise kit.AppError(f"{what}要是清單", code="invalid", status=400)
        else:
            items = []
            for part in re.split(r"[\n;；]+", text):
                part = part.strip().lstrip("-・•").strip()
                if not part:
                    continue
                key = part.startswith(KEY_MARKS)
                items.append({"text": part.lstrip("".join(KEY_MARKS)).strip(), "level": "key" if key else "normal"})
    if len(items) > MAX_POINTS:
        notes.append(f"{what}寫了 {len(items)} 點，只留前 {MAX_POINTS} 點")
    return items


def _review_data(values) -> tuple:
    """欄位 → 一份檢討的 dict（content 的 JSON 打底，個別欄位蓋上去；太長時可以分開送）。"""
    notes: list = []
    data = {}
    if text_of(values, "content"):
        data = json_of(values["content"], "檢討內容")
        if not isinstance(data, dict):
            raise kit.AppError("檢討內容要是一個 JSON 物件（grade、title、summary、good、watch、strategy、suggestion）",
                               code="invalid", status=400)
    for name in ("grade", "title", "summary"):
        if text_of(values, name):
            data[name] = text_of(values, name)
    for name, label in POINT_GROUPS:
        if text_of(values, name):
            data[name] = values[name]
        if name in data:
            data[name] = _points(data[name], label, notes)
    if text_of(values, "suggestion"):
        data["suggestion"] = json_of(values["suggestion"], "分配建議")
    if data.get("suggestion") is not None and not isinstance(data["suggestion"], dict):
        raise kit.AppError("分配建議（suggestion）要是 JSON 物件（buckets、group_budgets、saving_priority、reason）",
                           code="invalid", status=400)
    if not any(data.get(k) for k in ("title", "summary", "good", "watch", "strategy")):
        raise kit.AppError("檢討是空的：至少要有 title、summary 或 good／watch／strategy 其中一個", code="invalid", status=400)
    return data, notes


def run_review_save(book, values) -> Result:
    e = engine()
    kind = _kind(values.get("kind"))
    if kind is None:
        raise kit.AppError("要寫 kind：week（週檢討）或 month（月檢討）", code="invalid", status=400)
    data, notes = _review_data(values)
    reviews = e["reviews"]
    with write(book, "review_save") as w:
        rid = reviews.save_manual(w, kind, text_of(values, "key") or None, data)
    out = stamp(book, w, {"id": rid})
    rv = reviews.get(book.conn, rid)
    rv.pop("metrics", None)
    problems = list(rv.get("problems") or [])
    out.update({"review": rv, "problems": problems})
    text = f"存好了：檢討 #{rid}\n" + reviews.digest_text(rv, e["plans"].bucket_names(book.conn), decimals(book.conn))
    return Result(data=out, text=text, warnings=notes + problems)


# ── 報表的數字（唯讀）──────────────────────────────────────────────────────────────

def _report_key(values, kind):
    key = text_of(values, "key") or text_of(values, "period")
    if key and kind == "week" and not re.fullmatch(r"\d{4}-W\d{2}", key):
        raise kit.AppError(f"週要寫成 2030-W03 這樣（現在是「{key}」）", code="invalid", status=400)
    if key and kind == "month" and not re.fullmatch(r"\d{4}-\d{2}", key):
        raise kit.AppError(f"期要寫成 2030-01 這樣（現在是「{key}」）", code="invalid", status=400)
    return key or None


def run_report(book, values) -> Result:
    e = engine()
    conn = book.conn
    key_text = text_of(values, "key") or text_of(values, "period")
    kind = _kind(values.get("kind"), "week" if re.fullmatch(r"\d{4}-W\d{2}", key_text or "") else "month")
    key = _report_key(values, kind)
    full = bool(yes(values.get("full"), False))
    for_review = bool(yes(values.get("review"), False))
    reviews = e["reviews"]
    if for_review:
        key = key or reviews.due_key(conn, kind)
    rep = e["report"].build(conn, kind, key, with_transactions=full, include_notes=None if (full or for_review) else False)
    if not for_review:
        text = e["report"].text(rep, decimals(book.conn))
        if full:
            text += "\n\n資料（JSON）：\n" + json.dumps(rep, ensure_ascii=False, default=str)
        return Result(data=rep, text=text)
    ctx = reviews._context(conn, rep)
    schema = reviews.schema(ctx, kind)
    text = "\n".join([
        reviews.prompt(rep, ctx), "",
        "回覆的格式（JSON Schema）：" + json.dumps(schema, ensure_ascii=False, separators=(",", ":")), "",
        f"寫好之後存回帳本：/money_review_save kind={kind} key={rep['key']} content=<上面格式的 JSON>"
        "（太長就把 good、watch、strategy、suggestion 分開放進同名的欄位）。不要用 emoji。"])
    return Result(data={"report": rep, "context": ctx, "schema": schema}, text=text)


# ── 排程：該送什麼、該寫哪一份 ─────────────────────────────────────────────────────

def run_review_due(book, values) -> Result:
    """週報、檢討書：做好的就回在這裡（回了就算送出）；該寫的檢討列出來；太舊的標成略過。peek=yes 只看不標。"""
    e = engine()
    conn = book.conn
    peek = bool(yes(values.get("peek"), False))
    reviews, notify, names = e["reviews"], _notify(), e["report"].KIND_NAMES
    items = reviews.due(conn, book.today)
    skipped = []
    if not peek:
        for item in items:
            if item["action"] == "skip":
                reviews.skip(conn, item["kind"], item["key"], item["reason"], actor=book.actor)
                skipped.append(item)
    with write(book, "review_due", dry_run=peek) as w:
        messages = notify.due(w, book.today)
        if not peek and messages:
            notify.mark_sent(w, [m["id"] for m in messages])
    out = stamp(book, w, {})
    todo = [i for i in items if i["action"] == "run"]
    lines = [m["text"] for m in messages]
    if todo:
        lines.append("該寫檢討了：" + "、".join(f"{names[i['kind']]} {i['key']}（{i['reason']}）" for i in todo)
                     + "。請 AI 助理照記帳手冊寫：先 /money_report kind=<week 或 month> key=<上面那個> review=yes，"
                       "寫完用 /money_review_save 存回來。")
    out.update({"messages": [{k: m[k] for k in ("id", "kind", "key", "title", "text")} for m in messages],
                "write": todo, "skipped": skipped, "sent": not peek and bool(messages)})
    return Result(data=out, text="\n\n".join(lines) or "現在沒有要送的，也沒有該寫的檢討")


def _notify():
    from ..engine.services import notify
    return notify


STATUS_NAMES = (("done", "寫好了"), ("skipped", "略過"), ("deferred", "延後（AI 額度用完）"), ("failed", "失敗"),
                ("running", "寫到一半"))


def run_review_run(book, values) -> Result:
    """請掛在工具上的「寫檢討的提供者」寫：給 kind（＋key）＝寫那一份；都不給＝把該寫的都寫（額度用完就停）。
    沒有提供者 → llm_unavailable（引擎丟，table 轉成「❌ …」），什麼都不寫。"""
    e = engine()
    conn = book.conn
    reviews, names = e["reviews"], e["report"].KIND_NAMES
    status = dict(STATUS_NAMES)
    kind = _kind(values.get("kind"))
    if kind is None and (text_of(values, "key") or text_of(values, "period")):
        raise kit.AppError("給了 key 也要給 kind（week 或 month）", code="invalid", status=400)
    if kind is not None:
        rv = reviews.run_one(conn, kind, _report_key(values, kind), book.reviewer, actor=book.actor)
        rv.pop("metrics", None)
        text = f"{names[rv['kind']]} {rv['key']}：{status.get(rv['status'], rv['status'])}"
        if rv["status"] == "done":
            text += "\n" + reviews.digest_text(rv, e["plans"].bucket_names(conn), decimals(conn))
        elif rv.get("error"):
            text += f"（{rv['error']}）"
        return Result(data={"review": rv, "write_seq": e["db"].write_seq(conn)}, text=text,
                      warnings=list(rv.get("problems") or []))
    out = reviews.run_due(conn, book.reviewer, actor=book.actor, today=book.today)
    lines = [f"{status[k]}：" + "、".join(f"{names[r['kind']]} {r['key']}" + (f"（{r['error']}）" if r.get("error") and k != "done" else "")
                                       for r in out[k]) for k in ("done", "skipped", "deferred", "failed") if out[k]]
    return Result(data={**out, "write_seq": e["db"].write_seq(conn)}, text="\n".join(lines) or "現在沒有該寫的檢討")


# ── 一批混合紀錄 ───────────────────────────────────────────────────────────────

def _result_line(conn, r: dict) -> str:
    kind = dict(RESULT_TYPES).get(r.get("type"), r.get("type") or "")
    status = dict(STATUS_WORDS).get(r.get("status"), r.get("status") or "")
    if "amount" in r and r.get("type") in ("expense", "income", "transfer", "iou", "installment_purchase", "bill"):
        what = r.get("desc") or r.get("bill") or r.get("who") or ""
        cat = f"（{r['category']}）" if r.get("category") else ("（待分類）" if r.get("type") == "expense" else "")
        return f"・{kind} {money(conn, r['amount'])} {what}{cat}　{status}".rstrip()
    return f"・{kind}　{status}"


def run_ingest(book, values) -> Result:
    e = engine()
    conn = book.conn
    raw = str(values.get("records") or "")
    if len(raw) > FIELD_LIMIT:
        raise kit.AppError(f"一次送的內容太長（{len(raw)} 字，上限 {FIELD_LIMIT}），請分幾批送，每批用不同的 source_ref",
                           code="invalid", status=400)
    dry_run = bool(yes(values.get("dry_run"), False))
    out = e["ingest"].run(conn, raw, actor=book.actor, source=source_of(book), source_ref=text_of(values, "source_ref") or None,
                          dry_run=dry_run, command="ingest")
    s = out["summary"]
    head = ("試算（還沒寫進去）：" if dry_run else "") + f"這一批 {s['records']} 筆：新記 {s['new']}"
    if s["possible_duplicate"]:
        head += f"、可能重複 {s['possible_duplicate']}"
    if s["replayed"]:
        head += f"、之前記過 {s['replayed']}"
    sums = [f"{name} {money(conn, s[key])}" for key, name in (("expense", "支出"), ("income", "收入"), ("transfer", "轉帳"))
            if s[key]]
    if sums:
        head += "；" + "、".join(sums)
    lines = [head] + [_result_line(conn, r) for r in out["results"]]
    data = dict(out)
    data["write_seq"] = e["db"].write_seq(conn)
    return Result(data=data, text="\n".join(lines), warnings=list(out["warnings"]))


COMMANDS = (
    Cmd("review_apply", "把檢討建議的分配寫進那一期的計畫", WRITE, [
        Field("id", "哪一份檢討", required=True),
        Field("period", "寫進哪一期"),
        Field("force", "數字對不起來也照寫（yes／no）")], run_review_apply, positional=1, app=True),
    Cmd("review_reply", "回覆一份檢討", WRITE, [
        Field("id", "哪一份檢討", required=True),
        Field("text", "回覆", required=True)], run_review_reply, positional=2, app=True),
    Cmd("review_save", "存一份寫好的檢討（內容是一段 JSON；太長可以分欄位送）", WRITE, [
        Field("kind", "week／month", required=True),
        Field("key", "哪一週或哪一期（例 2030-W03、2030-01）"),
        Field("content", "檢討內容（JSON：grade、title、summary、good、watch、strategy、suggestion）"),
        Field("grade", "等第（A、A-、B+、B、B-、C+、C、C-、D）"),
        Field("title", "一句話標題"),
        Field("summary", "這段時間花錢的樣子"),
        Field("good", "做得好（JSON 清單，或一行一點；開頭寫 ! 是重要的）"),
        Field("watch", "要注意（同上）"),
        Field("strategy", "下一步（同上）"),
        Field("suggestion", "月檢討的分配建議（JSON）")], run_review_save,
        ai={"scenario": "照記帳手冊寫完週檢討或月檢討，要存回帳本時"}, positional=1, app=True),
    Cmd("report", "週或月的數字（唯讀；寫檢討用）", READ, [
        Field("kind", "week／month（不給＝月）"),
        Field("key", "哪一週或哪一期（例 2030-W03、2030-01；不給＝現在這一段）"),
        Field("period", "哪一期（同 key）"),
        Field("full", "連明細與小日記（yes／no）"),
        Field("review", "要寫檢討：連寫檢討的規則與格式一起給（yes／no）")], run_report,
        ai={"scenario": "要寫檢討、或使用者問這週這個月花在哪裡時"}, positional=2),
    Cmd("review_due", "現在該送的週報與檢討書、該寫哪一份檢討（給排程；回覆了就算送出）", WRITE, [
        Field("peek", "只看，不標成送出（yes／no）")], run_review_due, positional=0),
    Cmd("review_run", "請接上的 AI 寫檢討（沒接上就回「沒有 AI」，其他照常）", WRITE, [
        Field("kind", "week／month（不給＝把該寫的都寫）"),
        Field("key", "哪一週或哪一期（例 2030-W03、2030-01）")], run_review_run, positional=0),
    Cmd("ingest", "一批混合紀錄（內容是一段 JSON；太長請分批）", WRITE, [
        Field("records", "紀錄（JSON）", required=True),
        Field("source_ref", "這一批的來源編號（重送不會多記）"),
        Field("dry_run", "試算就好、先不寫進去（yes／no）")], run_ingest,
        ai={"scenario": "一則訊息裡有好幾筆帳、或一張收據有很多品項要一次記下時"}, positional=1),
)

READS = frozen({"reviews": read_reviews, "review": read_review})
EXPORTS = frozen({})
