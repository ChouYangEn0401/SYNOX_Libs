"""• 子彈筆記（Bullet Journal，Ryder Carroll 的快速記錄法）：

  /bj <內容>          記一筆到今天：開頭寫 • * . 是任務、○ o 是事件、– - 是筆記；沒寫就看字猜（猜錯可以 kind=…）
  /bj_today           今天的每日記錄（• 待做  × 完成  > 已遷移  ✗ 不做了  ○ 事件  – 筆記）
  /bj_month [YYYY-MM] 月誌：有記東西的每一天＋這個月還沒做完的任務
  /bj_migrate         遷移：以前沒做完的任務一件一件問（搬到今天／已完成／不做了／略過），按鈕
  /bj_done <編號>     任務打勾
  /bj_rm <編號>       刪掉一筆（記錯、分類猜錯時用）
  /bj_col [名稱]      主題清單（collection）：不寫＝列出全部；寫名稱＝看那一本。記進去用 /bj <內容> col=<名稱>

資料經 pack.store.of(user)（保管箱，密文）：
    {"entries": [{id, kind: task|event|note, text, day: "YYYY-MM-DD", state: open|done|migrated|cancelled,
                  col: "", from: 舊編號|null, at: 秒}], "next_id": 1}
不連網、不開檔、不開別的程式。遷移卡片的按鈕 24 小時後過期（每個在等人的東西都有期限）。
"""

import re
import time
from datetime import date, datetime, timedelta

from isdtelekit.core.channel import buttons
from isdtelekit.core.registry import WRITE, Field

from synox.packs import kit


def now() -> datetime:
    """現在時間（測試換掉這一個）。"""
    return datetime.now()


def empty() -> dict:
    """新的空資料（每次都是新的 dict 和 list：拿模組層級的常數做 dict(...) 只是淺拷貝，第一次寫入會改到共用的 list，
    伺服器版就會把前一個人的第一筆帶進下一個人的檔案）。"""
    return {"entries": [], "next_id": 1}
KINDS = ("task", "event", "note")
KIND_NAMES = {"task": "任務", "event": "事件", "note": "筆記"}
PREFIXES = (("task", ("•", "·", "* ", ". ", "[] ", "[ ] ", "□")),
            ("event", ("○", "◯", "o ", "O ", "@ ")),
            ("note", ("–", "—", "- ", "n ")))
EVENT_WORDS = ("開會", "會議", "聚餐", "約會", "約了", "生日", "面試", "上課", "看診", "看醫生", "看牙", "演唱會",
               "婚禮", "聚會", "活動", "出差", "旅行", "考試", "比賽", "表演", "回診", "門診")
TASK_WORDS = ("記得", "要", "得", "該", "買", "繳", "寄", "回覆", "回信", "打給", "打電話", "預約", "整理", "完成",
              "處理", "交", "付", "訂", "洗", "修", "準備", "寫", "讀完", "報名", "申請", "退")
TIME_HINT = re.compile(r"\d{1,2}:\d{2}|[0-9一二三四五六七八九十兩]{1,3}\s*[點点](?![點点])")
MIGRATE_PREFIX = "bjm:"
MIGRATE_TTL = 24 * 3600
STATE_MARK = {"open": "•", "done": "×", "migrated": ">", "cancelled": "✗"}
KIND_MARK = {"event": "○", "note": "–"}
WEEK_NAMES = "一二三四五六日"


def check_ymd(value: str):
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value or "")
    if not m:
        return "要是 YYYY-MM-DD（例如 2026-11-12）"
    try:
        date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return "不是真的日期"
    return None


def check_month(value: str):
    m = re.match(r"^(\d{4})-(\d{2})$", value or "")
    return None if m and 1 <= int(m.group(2)) <= 12 else "要是 YYYY-MM（例如 2026-11）"


def check_col(value: str):
    v = value.strip()
    return None if 0 < len(v) <= 30 and not re.search(r"[\r\n=]", v) else "名稱 1–30 個字，不能有換行或 ="


def classify(text: str):
    """'• 買牛奶' → ('task', '買牛奶')；沒有符號就看字：有時間或事件字 → event；有要做的字 → task；其他 → note。"""
    t = text.strip()
    for kind, marks in PREFIXES:
        for mark in marks:
            if t.startswith(mark) and len(t) > len(mark.strip()):
                return kind, t[len(mark):].strip()
    if TIME_HINT.search(t) or any(w in t for w in EVENT_WORDS):
        return "event", t
    if any(w in t for w in TASK_WORDS):
        return "task", t
    return "note", t


def mark(e: dict) -> str:
    return STATE_MARK.get(e.get("state", "open"), "•") if e["kind"] == "task" else KIND_MARK[e["kind"]]


def line(e: dict, with_day: bool = False) -> str:
    day = ""
    if with_day:
        d = date.fromisoformat(e["day"])
        day = f"{d.month:02d}/{d.day:02d} "
    return f"[{e['id']}] {day}{mark(e)} {e['text']}"


def migrate_candidates(entries, today: date):
    iso = today.isoformat()
    return sorted((e for e in entries if e["kind"] == "task" and e.get("state", "open") == "open"
                   and not e.get("col") and e["day"] < iso), key=lambda e: e["id"])


def install(app, pack):
    g = pack.group

    def entries(user):
        return list((pack.store.of(user).load(None) or {}).get("entries", []))

    def append(user, entry):
        def fn(d):
            d.setdefault("entries", [])
            entry["id"] = int(d.get("next_id") or 1)
            d["next_id"] = entry["id"] + 1
            d["entries"].append(entry)
            return entry["id"]
        return pack.store.of(user).update(fn, empty())

    def add(api, chat_id, user, values):
        kind, text = classify(values["text"])
        if values.get("kind"):
            kind = values["kind"].strip().lower()
        if not text:
            api.send_message(chat_id, "❌ 要寫內容。例：/bj • 買牛奶、/bj ○ 14:00 開會、/bj – 今天很累")
            return
        entry = {"kind": kind, "text": text, "day": values.get("date") or now().date().isoformat(),
                 "state": "open", "col": values.get("col", "").strip(), "from": None, "at": int(time.time())}
        append(user, entry)
        where = f"「{entry['col']}」" if entry["col"] else "今天"
        hint = "" if values.get("kind") else f"\n（{KIND_NAMES[kind]}；猜錯了就 /bj_rm {entry['id']} 再用 • ○ – 開頭重記）"
        api.send_message(chat_id, f"📓 記到{where}：{line(entry)}{hint}")

    def today_(api, chat_id, user, values):
        today = now().date()
        rows = [e for e in entries(user) if e["day"] == today.isoformat() and not e.get("col")]
        head = f"📓 {today.month:02d}/{today.day:02d}（{WEEK_NAMES[today.weekday()]}）"
        lines = [head] + ([line(e) for e in rows] or ["還沒記東西。/bj • 買牛奶、/bj ○ 14:00 開會、/bj – 想法"])
        old = migrate_candidates(entries(user), today)
        if old:
            lines.append(f"----\n以前還有 {len(old)} 件任務沒處理 → /bj_migrate")
        api.send_message(chat_id, "\n".join(lines))

    def month(api, chat_id, user, values):
        today = now().date()
        key = values.get("month") or today.strftime("%Y-%m")
        rows = [e for e in entries(user) if e["day"].startswith(key) and not e.get("col")]
        if not rows:
            api.send_message(chat_id, f"📅 {key} 月誌：這個月還沒記東西。")
            return
        lines = [f"📅 {key} 月誌"]
        by_day = {}
        for e in rows:
            by_day.setdefault(e["day"], []).append(e)
        for day in sorted(by_day):
            d = date.fromisoformat(day)
            items = by_day[day]
            events = [e["text"] for e in items if e["kind"] == "event"]
            counts = []
            tasks = [e for e in items if e["kind"] == "task"]
            if tasks:
                counts.append(f"•{sum(1 for e in tasks if e.get('state', 'open') == 'open')}/{len(tasks)}")
            notes = sum(1 for e in items if e["kind"] == "note")
            if notes:
                counts.append(f"–{notes}")
            star = "▸" if d == today else " "
            text = "、".join(events[:3]) + ("…" if len(events) > 3 else "")
            lines.append(f"{star}{d.day:02d} {WEEK_NAMES[d.weekday()]} {('○ ' + text) if text else ''} {' '.join(counts)}"
                         .rstrip())
        open_tasks = [e for e in rows if e["kind"] == "task" and e.get("state", "open") == "open"]
        lines.append("----")
        lines.append(f"這個月還沒做完的任務（{len(open_tasks)}）" if open_tasks else "這個月的任務都處理完了 🎉")
        lines += [line(e, with_day=True) for e in open_tasks[:30]]
        api.send_message(chat_id, "\n".join(lines))

    def card(entry, left: int):
        d = date.fromisoformat(entry["day"])
        text = (f"🔀 遷移（還有 {left} 件）\n{line(entry)}\n記在 {d.month:02d}/{d.day:02d}，現在要怎麼辦？")
        markup = buttons([("➡️ 搬到今天", f"{MIGRATE_PREFIX}m:{entry['id']}"), ("× 已完成", f"{MIGRATE_PREFIX}d:{entry['id']}")],
                         [("✗ 不做了", f"{MIGRATE_PREFIX}x:{entry['id']}"), ("略過", f"{MIGRATE_PREFIX}s:{entry['id']}")])
        return text, markup

    def migrate(api, chat_id, user, values):
        old = migrate_candidates(entries(user), now().date())
        if not old:
            api.send_message(chat_id, "🔀 沒有要遷移的任務 🎉")
            return
        text, markup = card(old[0], len(old))
        api.send_message(chat_id, text, reply_markup=markup)

    def on_migrate(api, cb):
        try:
            _, action, raw_id = cb.data.split(":", 2)
            entry_id = int(raw_id)
        except ValueError:
            api.answer_callback_query(cb.cq_id, text="看不懂這顆按鈕")
            return
        if action not in ("m", "d", "x", "s"):
            api.answer_callback_query(cb.cq_id, text="看不懂這顆按鈕")
            return
        if cb.message_date and time.time() - float(cb.message_date) > MIGRATE_TTL:
            api.answer_callback_query(cb.cq_id, text="⌛ 這張卡超過 24 小時了，請重新 /bj_migrate")
            api.remove_buttons(cb.chat_id, cb.message_id)
            return
        user = cb.clicker_id
        today = now().date()

        def apply(d):
            lst = d.setdefault("entries", [])
            target = next((e for e in lst if e["id"] == entry_id), None)
            if target is None or target.get("state", "open") != "open" or target["kind"] != "task":
                return "gone"
            if action == "m":
                target["state"] = "migrated"
                new = {"kind": "task", "text": target["text"], "day": today.isoformat(), "state": "open", "col": "",
                       "from": target["id"], "at": int(time.time()), "id": int(d.get("next_id") or 1)}
                d["next_id"] = new["id"] + 1
                lst.append(new)
                return f"搬到今天了（新編號 {new['id']}）"
            if action == "d":
                target["state"] = "done"
                return "打勾了"
            if action == "x":
                target["state"] = "cancelled"
                return "劃掉了"
            return "略過"

        said = pack.store.of(user).update(apply, empty())
        api.answer_callback_query(cb.cq_id, text="這件已經處理過了" if said == "gone" else said)
        rest = [e for e in migrate_candidates(entries(user), today) if e["id"] > entry_id]
        if rest:
            text, markup = card(rest[0], len(rest))
            api.edit_message_text(cb.chat_id, cb.message_id, text, reply_markup=markup)
            return
        left = migrate_candidates(entries(user), today)
        tail = f"略過的還有 {len(left)} 件，下次 /bj_migrate 再問。" if left else "以前的任務都處理完了。"
        api.edit_message_text(cb.chat_id, cb.message_id, f"🔀 這一輪遷移完成。{tail}\n今天的清單：/bj_today")
        api.remove_buttons(cb.chat_id, cb.message_id)

    def done(api, chat_id, user, values):
        n = int(values["n"])

        def mark_done(d):
            e = next((x for x in d.setdefault("entries", []) if x["id"] == n), None)
            if e is None:
                return None
            if e["kind"] != "task":
                return "not_task", dict(e)
            was = e.get("state", "open")
            if was == "open":
                e["state"] = "done"
            return was, dict(e)

        found = pack.store.of(user).update(mark_done, empty())
        if found is None:
            api.send_message(chat_id, f"❌ 沒有 [{n}] 這一筆（/bj_today 看編號）。")
            return
        was, e = found
        if was == "not_task":
            api.send_message(chat_id, f"[{n}] 是{KIND_NAMES[e['kind']]}，只有任務能打勾。")
        elif was != "open":
            api.send_message(chat_id, f"[{n}] 已經是 {mark(e)} 了：{e['text']}")
        else:
            api.send_message(chat_id, f"× 完成：{e['text']}")

    def rm(api, chat_id, user, values):
        n = int(values["n"])

        def drop(d):
            lst = d.setdefault("entries", [])
            for i, e in enumerate(lst):
                if e["id"] == n:
                    return lst.pop(i)
            return None

        gone = pack.store.of(user).update(drop, empty())
        if gone is None:
            api.send_message(chat_id, f"❌ 沒有 [{n}] 這一筆。")
            return
        api.send_message(chat_id, f"🗑 刪掉了：{line(gone)}")

    def col(api, chat_id, user, values):
        name = values.get("name", "").strip()
        all_entries = entries(user)
        if not name:
            counts = {}
            for e in all_entries:
                if e.get("col"):
                    counts[e["col"]] = counts.get(e["col"], 0) + 1
            if not counts:
                api.send_message(chat_id, "📚 還沒有主題清單。記一筆就有：/bj 原子習慣 col=書單")
                return
            api.send_message(chat_id, "\n".join(["📚 主題清單"] + [f"• {k}（{v}）" for k, v in sorted(counts.items())]
                                                + ["看一本：/bj_col <名稱>"]))
            return
        rows = [e for e in all_entries if e.get("col") == name]
        if not rows:
            api.send_message(chat_id, f"📚「{name}」是空的。記一筆：/bj <內容> col={name}")
            return
        api.send_message(chat_id, "\n".join([f"📚 {name}（{len(rows)}）"] + [line(e) for e in rows]))

    kit.command(g, "add", add, "快速記一筆（• 任務 ○ 事件 – 筆記）", default=True, effect=WRITE, positional=1,
                usage="<內容>（例：• 買牛奶、○ 14:00 開會、– 今天很累） [kind=task|event|note] [col=…]",
                fields=[Field("text", "內容（可以用 • ○ – 開頭指定種類）", required=True),
                        Field("kind", "種類 task／event／note（不寫＝看開頭符號或字猜）", choices=KINDS),
                        Field("col", "記進哪一本主題清單（不寫＝今天的每日記錄）", check=check_col),
                        Field("date", "記在哪一天 YYYY-MM-DD（補記用；不寫＝今天）", check=check_ymd)])
    kit.command(g, "today", today_, "今天的每日記錄")
    kit.command(g, "month", month, "月誌（每一天＋還沒做完的任務）",
                fields=[Field("month", "月份 YYYY-MM（不寫＝這個月）", check=check_month)])
    migrate_node = kit.command(g, "migrate", migrate, "遷移：以前沒做完的任務，一件一件決定（按鈕）", effect=WRITE)
    kit.command(g, "done", done, "任務打勾（編號看 /bj_today）", effect=WRITE,
                fields=[Field("n", "編號", required=True, check=kit.positive_int)])
    kit.command(g, "rm", rm, "刪掉一筆（記錯時用）", effect=WRITE,
                fields=[Field("n", "編號", required=True, check=kit.positive_int)])
    kit.command(g, "col", col, "主題清單：不寫＝列出全部，寫名稱＝看那一本",
                fields=[Field("name", "清單名稱", check=check_col)])
    try:
        app.registry.bind_callback(MIGRATE_PREFIX, migrate_node.path, on_migrate)
    except ValueError:
        pass        # pack reload：舊的前綴還綁著（SYNOX 已知限制），沿用舊的那一個

    def today_route(user):
        """Web UI 面板（panel.html）：今天的每日記錄＋以前沒處理的任務數。只回這個人自己的。"""
        today = now().date()
        all_entries = entries(user)
        rows = [e for e in all_entries if e["day"] == today.isoformat() and not e.get("col")]
        return {"today": today.isoformat(),
                "label": f"{today.month:02d}/{today.day:02d}（{WEEK_NAMES[today.weekday()]}）",
                "entries": [{"id": e["id"], "mark": mark(e), "kind": e["kind"], "state": e.get("state", "open"),
                             "text": e["text"]} for e in rows],
                "to_migrate": len(migrate_candidates(all_entries, today))}

    pack.web_route("today", today_route)
