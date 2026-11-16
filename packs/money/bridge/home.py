"""總覽（W4）：本期還能花多少、每天可花、錢怎麼分的每一格、跳過／撥額度／併入、挪用、固定項目一鍵記下、今天處理。

欄位名是契約（app/js/routes.js 照這些名字送值）：可以加，不要改名或拿掉。
路徑裡的編號（/api/bucket/<格子>/skip）送成欄位 id；/api/moves/<編號> 的 DELETE 送成 move 的欄位 undo。

每個 run 只做「字串欄位 → 引擎的參數 → 呼叫 services → 組回覆」；計算全在 engine/services（home、stats、envelopes、
moves、allowance、recurring、plans）。寫入一律經 db.write，回傳帶 batch_id（沒改到東西是 None）與 write_seq。
模組層級只放常數（隔離規則 4）。
"""

import re

from ..engine import db
from ..engine.errors import InvalidInput, NotFound
from ..engine.services import (allowance, conflicts, envelopes, estimate, home as home_service, moves, periods, plans,
                               recurring, stats)
from . import table
from .setup import needs_onboarding
from .table import READ, WRITE, Cmd, Field, Result, frozen

DOMAIN = "home"

YES = ("yes", "y", "true", "1", "是", "對", "要", "on")
NO = ("no", "n", "false", "0", "否", "不", "不要", "off", "")
PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")

# 「錢怎麼分」每一格的狀態 → 白話（跟畫面 home.js 的 envStatus 同一套字）
STATE_WORDS = (("done", "做完了"), ("todo", "還沒做完"), ("over", "超過"), ("rolling", "留著等帳單"),
               ("none", "沒計畫"), ("open", "還在花"), ("skipped", "這期跳過"))
LEVEL_WORDS = (("bad", "要處理"), ("warn", "注意"), ("info", "參考"), ("good", "沒問題"))


# ── 小工具：字串欄位 → 引擎的參數 ────────────────────────────────────────────────────

def _text(values, name):
    return str((values or {}).get(name) or "").strip()


def _flag(values, name, default=False):
    """yes／no（也收 true／false、1／0、是／否）；沒給＝default。"""
    raw = (values or {}).get(name)
    if raw is None:
        return default
    word = str(raw).strip().lower()
    if word in YES:
        return True
    if word in NO:
        return False if word else default
    raise InvalidInput(f"「{raw}」看不懂，{name} 要寫 yes 或 no", fields={name: "yes／no"})


def _period(values, name="period"):
    """哪一期：不給＝本期；要像 2030-01。"""
    key = _text(values, name)
    if not key:
        return None
    if not PERIOD_RE.match(key):
        raise InvalidInput(f"期名「{key}」看不懂", hint="寫成 2030-01 這樣（年-月）", fields={name: "要像 2030-01"})
    return key


def _word(pairs, key):
    return next((w for k, w in pairs if k == key), key)


def _bucket(book, values, name="id"):
    """格子的 id 或名字 → id（停用的也找得到；找不到說有哪些）。"""
    ref = _text(values, name)
    if not ref:
        raise InvalidInput("要說是哪一格", fields={name: "必填"})
    for b in plans.buckets(book.conn, include_inactive=True):
        if ref in (b["id"], b["name"], b["official"]):
            return b["id"]
    raise NotFound(f"沒有「{ref}」這一格", suggestions=[b["name"] for b in plans.buckets(book.conn)][:8],
                   hint="用 /money_envelope 看有哪些格子")


def _write(book, command):
    return db.write(book.conn, actor=book.actor, source=book.actor, command=command, dry_run=False)


def _out(book, batch, data):
    return {**data, "batch_id": batch, "write_seq": db.write_seq(book.conn)}


# ── 讀 ───────────────────────────────────────────────────────────────────────────

def read_home(book, params):
    """總覽一整包（形狀照原本的 /api/home）；還沒做第一次使用時多一個 onboarding: true（畫面叫 onboard.js 的 start）。"""
    data = home_service.home(book.conn, _period(params))
    if needs_onboarding(book):                   # 跟 features 讀同一個判斷（bridge/setup.py）
        data["onboarding"] = True
    return data


def read_year(book, params):
    text = _text(params, "year")
    if text and not re.fullmatch(r"\d{4}", text):
        raise InvalidInput(f"年份「{text}」看不懂", hint="寫成 2030 這樣", fields={"year": "要像 2030"})
    return home_service.year(book.conn, int(text) if text else None)


def read_allowance(book, params):
    items = allowance.check(book.conn, _period(params))
    return {"items": items, "ok": not [i for i in items if i["level"] != "info"]}


def read_conflicts(book, params):
    """待解決：帳裡對不上的地方。只讀，不改任何資料。"""
    return conflicts.payload(conflicts.scan(book.conn))


def read_moves(book, params):
    return moves.summary(book.conn, _period(params))


# ── 指令（只讀）──────────────────────────────────────────────────────────────────────

def run_summary(book, values):
    """本期還能花多少、每天可花、各大類、要注意幾件。"""
    conn = book.conn
    p = periods.get(conn, _period(values))
    s = stats.period_summary(conn, p.key)
    lv = s["living"]
    lines = [stats.text_summary(s)]
    if lv["budget"] and s["period"]["open"]:
        per_day = lv["per_day_left"]
        lines.append(f"每天可花：本來 {table.show(book.conn, lv['daily_budget'])}"
                     + (f"，剩下的 {lv['days_left']} 天每天 {table.show(book.conn, per_day)}" if per_day is not None else ""))
    guess_data = estimate.for_period(conn, p.key, living=lv)
    guess = estimate.text(guess_data)
    if guess:
        lines.append(guess)
    groups = sorted((g for g in s["groups"] if g["spent"] or g["budget"]), key=lambda g: -g["spent"])[:5]
    if groups:
        lines.append("各大類：" + "、".join(
            f"{g['name']} {table.show(book.conn, g['spent'])}" + (f"/{table.show(book.conn, g['budget'])}" if g["budget"] else "") for g in groups))
    items = allowance.check(conn, p.key)
    serious = [i for i in items if i["level"] in ("bad", "warn")]
    if serious:
        lines.append(f"要注意 {len(serious)} 件（/money_check 看）：{serious[0]['text']}")
    data = {"period": p.key, "living": lv, "estimate": guess_data,
            "groups": s["groups"], "allowance": items}
    return Result(data=data, text="\n".join(lines))


def run_check(book, values):
    """額度提醒（總覽燈號、記完一筆的提醒用的同一份）。"""
    p = periods.get(book.conn, _period(values))
    items = allowance.check(book.conn, p.key)
    if not items:
        text = f"{p.key} 期：額度都沒問題 ✅"
    else:
        text = "\n".join([f"{p.key} 期的額度提醒："]
                         + [f"・{_word(LEVEL_WORDS, i['level'])}：{i['text']}" for i in items])
    return Result(data={"period": p.key, "items": items, "ok": not [i for i in items if i["level"] != "info"]},
                  text=text)


def _envelope_line(book, r):
    target = r["available"] if r.get("rollover") else r["plan"]
    line = f"{r['name']}（{r['id']}）{table.show(book.conn, r['used'])} / {table.show(book.conn, target)}・{_word(STATE_WORDS, r['state'])}"
    if r.get("near"):
        line += "（差不多，算做完）"
    if r.get("moved"):
        line += f"（這期{'挪入' if r['moved'] > 0 else '挪出'} {table.show(book.conn, abs(r['moved']))}）"
    if r.get("action"):
        a = r["action"]
        line += f"；今天處理會記 {table.show(book.conn, a['amount'])}" + (f"・{a['category']}" if a.get("category") else "") \
            + (f"・{a['account']}" if a.get("account") else "")
    return line


def run_envelope(book, values):
    """錢怎麼分的每一格：計畫、做到多少、狀態、今天處理會記的那一筆、底下的固定項目。"""
    p, rows = envelopes.period_rows(book.conn, _period(values))
    lines = [f"{p.key} 期 錢怎麼分："]
    for r in rows:
        if not (r["plan"] or r["used"] or r.get("carry") or r.get("skipped") or r.get("moved")):
            continue
        lines.append("・" + _envelope_line(book, r))
        for it in r.get("items") or []:
            tail = ""
            if it.get("action") and it["action"].get("amount"):
                tail = f"（今天處理 {table.show(book.conn, it['action']['amount'])}）"
            lines.append(f"    #{it['id']} {it['name']}：{it['state']}{tail}")
    if len(lines) == 1:
        lines.append("還沒分。到記帳畫面的「規劃」填每期收入怎麼分。")
    return Result(data={"period": p.key, "envelopes": rows}, text="\n".join(lines))


# ── 動作（同時是指令）──────────────────────────────────────────────────────────────

def run_bucket_skip(book, values):
    bucket = _bucket(book, values)
    undo = _flag(values, "undo")
    with _write(book, "bucket skip") as w:
        r = plans.skip(w, bucket, _period(values), undo=undo)
        batch = w.batch_id if w.changes else None
    text = (f"{r['name']} {r['period']} 期跳過了（不用記帳；下一期照常）" if r["skipped"]
            else f"{r['name']} {r['period']} 期取消跳過")
    return Result(data=_out(book, batch, r), text=text + ("" if r["changed"] else "（本來就是這樣）"))


def run_bucket_setaside(book, values):
    bucket = _bucket(book, values)
    undo = _flag(values, "undo")
    with _write(book, "bucket setaside") as w:
        r = plans.set_aside(w, bucket, _period(values), amount=table.amount_of(book, values, zero=True, negative=True), to=_text(values, "to") or None,
                            from_account=_text(values, "from_account") or None,
                            category=_text(values, "category") or None, undo=undo)
        batch = w.batch_id if w.changes else None
    s = r.get("set_aside")
    if undo:
        text = f"{r['name']} {r['period']} 期的撥額度拿掉了（連同那一筆帳）"
    elif r.get("changed") is False:
        text = f"{r['name']} {r['period']} 期已經撥過了：{table.show(book.conn, s['amount'])} → {s['dest'] or '沒搬'}（要改先拿掉再撥）"
    else:
        where = "預繳給對方，記成本期支出" if s["kind"] == "prepay" else f"從「{r.get('from')}」轉到「{s['dest']}」"
        text = f"{r['name']} {r['period']} 期撥了 {table.show(book.conn, s['amount'])}：{where}"
    return Result(data=_out(book, batch, r), text=text)


def run_bucket_fold(book, values):
    bucket = _bucket(book, values)
    into = _text(values, "into")
    if not into:
        raise InvalidInput("要說併進哪一格", fields={"into": "必填"})
    with _write(book, "bucket fold") as w:
        r = plans.fold_bucket(w, bucket, into, add_to_parent=_flag(values, "add_to_parent", True),
                              to=_text(values, "to") or None, period_key=_period(values))
        batch = w.batch_id if w.changes else None
    item = r["item"]
    text = (f"併好了：「{item['name']}」變成這一格底下的固定項目"
            + (f"，金額 {table.show(book.conn, r['amount'])} 加到那一格的總額" if r["added_to_parent"] else "")
            + (f"；細項 {'、'.join(r['moved_categories'])} 跟著搬過去" if r["moved_categories"] else ""))
    return Result(data=_out(book, batch, r), text=text)


def run_move(book, values):
    undo = _text(values, "undo")
    if undo:
        if not re.fullmatch(r"\d+", undo):
            raise InvalidInput(f"挪用的編號「{undo}」看不懂", fields={"undo": "要是編號"})
        with _write(book, "plan move undo") as w:
            r = moves.undo(w, int(undo))
            batch = w.batch_id if w.changes else None
        text = f"拿掉挪用：{r['from_name']} → {r['to_name']} {table.show(book.conn, r['amount'])}"
        return Result(data=_out(book, batch, {"move": r}), text=text)
    amount = table.amount_of(book, values, required=True, zero=True, negative=True)
    with _write(book, "plan move") as w:
        r = moves.move(w, _text(values, "from_bucket"), _text(values, "to_bucket"), amount, _text(values, "note"),
                       _period(values))
        batch = w.batch_id if w.changes else None
    warnings = list(r.pop("warnings", []) or [])
    text = f"{r['period']} 期 {r['from_name']} → {r['to_name']} 挪了 {table.show(book.conn, r['amount'])}（編號 {r['id']}）"
    return Result(data=_out(book, batch, {"move": r, "warnings": warnings}), text=text, warnings=warnings)


def run_fixed_post(book, values):
    with _write(book, "recurring post") as w:
        r = recurring.post(w, _period(values), upcoming=_flag(values, "upcoming"))
        batch = w.batch_id if w.changes else None
    if r["created"]:
        text = f"記下 {len(r['created'])} 筆固定項目：" + "、".join(
            f"{c['name']} {table.show(book.conn, c['amount'])}" for c in r["created"])
    else:
        text = "沒有要記的固定項目（都記過了、還沒到期，或金額不一定）"
    return Result(data=_out(book, batch, r), text=text)


def run_envelope_do(book, values):
    ref = _text(values, "id")
    if not ref:
        raise InvalidInput("要說是哪一格或哪個固定項目", fields={"id": "必填"})
    with _write(book, "envelope do") as w:
        r = envelopes.do(w, ref, period_key=_period(values), amount=table.amount_of(book, values, zero=True, negative=True), day=table.day_or_none(values, "date", book.today),
                         category=_text(values, "category") or None, source=book.actor)
        batch = w.batch_id if w.changes else None
    warnings = list(r.pop("warnings", []) or [])
    rec = r["record"]
    status = {"created": "記下", "replayed": "已經記過"}.get(rec.get("status"), "記下")
    text = (f"{status}：{rec.get('date', '')} {r['name']} {table.show(book.conn, rec.get('amount'))}"
            + (f"・{rec['category']}" if rec.get("category") else "") + (f"（{rec['account']}）" if rec.get("account") else ""))
    return Result(data=_out(book, batch, {**r, "warnings": warnings}), text=text, warnings=warnings)


COMMANDS = (
    Cmd("summary", "本期還能花多少、每天可花、各大類、要注意的事", READ, [Field("period", "哪一期（例如 2030-01）")],
        run_summary, ai={"scenario": "使用者問這個月還能花多少、花了多少、每天可以花多少時"}, positional=1),
    Cmd("check", "額度提醒：分配、超支、走太快、到期沒做的固定項目", READ, [Field("period", "哪一期")],
        run_check, ai={"scenario": "使用者問預算有沒有問題、有沒有超支、該注意什麼時"}, positional=1),
    Cmd("envelope", "列出錢怎麼分的每一格（計畫、做到多少、還剩）", READ, [Field("period", "哪一期")], run_envelope,
        positional=1),
    Cmd("bucket_skip", "某一格這期跳過（undo=yes 取消）", WRITE, [
        Field("id", "哪一格", required=True),
        Field("period", "哪一期"),
        Field("undo", "取消跳過（yes／no）")], run_bucket_skip, positional=1, app=True),
    Cmd("bucket_setaside", "滾存的格子撥額度（錢轉去別的帳戶或預繳；undo=yes 拿掉）", WRITE, [
        Field("id", "哪一格", required=True),
        Field("period", "哪一期"),
        Field("undo", "拿掉（yes／no）"),
        Field("amount", "撥多少（不給＝這期的計畫額度）"),
        Field("to", "錢放去哪（帳戶名字，或「預繳」）"),
        Field("from_account", "從哪個帳戶出去"),
        Field("category", "預繳時記在哪個細項")], run_bucket_setaside, positional=1, app=True),
    Cmd("bucket_fold", "把自己加的格子併進別的格子底下，變成它的固定項目", WRITE, [
        Field("id", "哪一格", required=True),
        Field("into", "併進哪一格", required=True),
        Field("add_to_parent", "金額加到那一格的總額（yes／no）"),
        Field("to", "以後轉進哪個帳戶"),
        Field("period", "從哪一期開始")], run_bucket_fold, positional=2, app=True),
    Cmd("move", "這期從一格挪額度去補另一格（不記帳、不搬錢）；undo=<編號> 拿掉一筆挪用", WRITE, [
        Field("from_bucket", "從哪一格"),
        Field("to_bucket", "挪去哪一格"),
        Field("amount", "挪多少"),
        Field("note", "為什麼"),
        Field("period", "哪一期"),
        Field("undo", "拿掉哪一筆挪用（編號）")], run_move, positional=3, app=True),
    Cmd("fixed_post", "把到期還沒記的固定項目記下來（重按不重複）", WRITE, [
        Field("period", "哪一期"),
        Field("upcoming", "連還沒到期的也記（yes／no）")], run_fixed_post, app=True),
    Cmd("envelope_do", "今天處理：照預先填好的那一筆記下來", WRITE, [
        Field("id", "哪一格或哪個固定項目", required=True),
        Field("amount", "金額（不給＝照預填的）"),
        Field("date", "哪一天"),
        Field("category", "細項"),
        Field("period", "哪一期")], run_envelope_do, positional=1, app=True),
)

READS = frozen({"home": read_home, "year": read_year, "allowance": read_allowance, "conflicts": read_conflicts,
                "moves": read_moves})
EXPORTS = frozen({})
