"""檢討（W7）：報表的數字、寫檢討的提示、存檢討的驗證、套用建議、回覆、排程該送什麼、一批混合紀錄。

不叫任何 AI：檢討的內容由測試直接給（就像 SYNOX 的 AI 照手冊寫好再存回來）。資料一律是假的。
"""

import importlib
import json
from datetime import datetime

import pytest

from synox.packs import kit

from .conftest import OTHER, OWNER, U

SALARY = 45000
EXPENSES = [("2030-01-03", "12:10", 120, "排骨便當", "午餐"), ("2030-01-06", "19:00", 260, "範例火鍋", "晚餐"),
            ("2030-01-09", "15:00", 60, "奶茶", "飲料"), ("2030-01-12", "10:00", 1200, "範例耳機", "大額採買"),
            ("2030-01-14", "12:30", 130, "排骨便當", "午餐")]


def svc(bot, name):
    return importlib.import_module(f"{bot.module.__name__}.engine.services.{name}")


def records(extra=()):
    rows = [{"type": "expense", "date": d, "time": t, "amount": a, "desc": desc, "category": c} for d, t, a, desc, c in EXPENSES]
    rows.append({"type": "income", "date": "2030-01-05", "amount": SALARY, "desc": "薪水", "category": "薪資"})
    rows.append({"type": "day_note", "date": "2030-01-12", "note": "耳機壞了只好買新的"})
    return {"records": rows + list(extra)}


def seed(bot, user=OWNER, ref="chat:1:1"):
    return bot.command("ingest", user, json.dumps(records(), ensure_ascii=False) + f" source_ref={ref}")


@pytest.fixture
def bot(make_money):
    b = make_money()
    seed(b)
    return b


def good_answer(**over):
    data = {"grade": "B", "title": "午餐穩定，耳機是大筆 ❌",
            "summary": "生活支出控制得不錯 ✅，1/12 的耳機 1,200 是這期最大一筆。",
            "good": [{"text": "午餐都在 130 以內", "level": "key"}], "watch": ["耳機 1,200 一次花掉"],
            "strategy": ["飲料一週最多兩杯"],
            "suggestion": {"buckets": {"fixed": 15000, "saving": 10000, "living": 20000},
                           "group_budgets": {"食": 12000, "行": 3000, "樂": 5000},
                           "saving_priority": ["銀行帳戶", "不存在的戶頭"], "reason": "購物先收一點"}}
    data.update(over)
    return data


def save(bot, data, kind="month", key="2030-01", user=OWNER):
    return bot.act("review_save", user, {"kind": kind, "key": key, "content": json.dumps(data, ensure_ascii=False)})


# ── 報表 ───────────────────────────────────────────────────────────────────────

def test_report_numbers_match_the_engine(bot):
    reply = bot.command("report", OWNER, "month 2030-01")
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        rep = svc(bot, "report").build(book.conn, "month", "2030-01", with_transactions=False)
    spent = sum(e[2] for e in EXPENSES)
    assert rep["living"]["spent"] == spent and rep["income"] == SALARY
    assert reply.startswith("月報 2030-01 期") and f"生活支出 {spent:,}" in reply
    week = bot.command("report", OWNER, "week 2030-W02")
    assert week.startswith("週報 2030-W02") and "生活支出" in week


def test_report_full_gives_the_data_and_review_gives_the_rules(bot):
    full = bot.command("report", OWNER, "month 2030-01 full=yes")
    assert "資料（JSON）" in full and "排骨便當" in full
    text = bot.command("report", OWNER, "month 2030-01 review=yes")
    assert "理財教練" in text and "TWD" in text and "新台幣元" not in text
    assert "/money_review_save kind=month key=2030-01" in text and "JSON Schema" in text
    assert "耳機壞了只好買新的" in text                                   # 小日記也給
    assert f"加總必須剛好等於 {SALARY}" in text                              # 月報要給分配建議


def test_review_prompt_reads_the_currency_setting(bot):
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        settings, db = svc(bot, "settings"), importlib.import_module(f"{bot.module.__name__}.engine.db")
        with db.write(book.conn) as w:
            settings.put(w, "currency.code", "USD")
            settings.put(w, "currency.symbol", "$")
            settings.put(w, "currency.decimals", 2)
    text = bot.command("report", OWNER, "week 2030-W02 review=yes")
    assert "USD（$） 的最小單位" in text and "100 代表 1.00" in text and "新台幣" not in text


def test_report_rejects_a_bad_key(bot):
    assert bot.command("report", OWNER, "week 2030-01").startswith("❌")
    assert bot.command("report", OWNER, "year").startswith("❌")


# ── 存檢討 ─────────────────────────────────────────────────────────────────────

def test_review_save_cleans_and_flags(bot):
    out = save(bot, good_answer())
    rv = out["review"]
    assert out["batch_id"] and out["write_seq"] and rv["status"] == "done" and rv["source"] == "manual"
    assert rv["title"] == "午餐穩定，耳機是大筆" and "✅" not in rv["summary"]       # emoji 拿掉
    assert rv["good"] == [{"text": "午餐都在 130 以內", "level": "key"}]
    assert rv["suggestion"]["valid"] and rv["suggestion"]["period"] == "2030-02"
    assert rv["suggestion"]["saving_priority"] == ["銀行帳戶"]
    assert any("不存在的戶頭" in p for p in out["warnings"])


def test_review_save_rules(bot):
    out = save(bot, good_answer(grade="Z", watch=["一", "二", "三", "四", "五"]))
    assert out["review"]["grade"] is None and any("等第" in p for p in out["warnings"])
    assert len(out["review"]["watch"]) == 4 and any("只留前 4 點" in p for p in out["warnings"])
    bad = good_answer()
    bad["suggestion"]["buckets"]["saving"] = 9999                         # 加總不等於收入
    out = save(bot, bad)
    assert out["review"]["suggestion"]["valid"] is False and any("不等於" in p for p in out["warnings"])
    with pytest.raises(kit.AppError) as no:
        bot.act("review_apply", OWNER, {"id": str(out["id"])})
    assert "對不起來" in no.value.message and "force=yes" in no.value.message
    groups = good_answer()
    groups["suggestion"]["group_budgets"] = {"食": 1}                       # 各大類加總不等於生活費
    assert save(bot, groups)["review"]["suggestion"]["valid"] is False


@pytest.mark.parametrize("values, words", [
    ({"kind": "month", "content": "{壞掉"}, "JSON"),
    ({"kind": "month", "content": "[1, 2]"}, "JSON 物件"),
    ({"kind": "month", "content": "{}"}, "空的"),
    ({"kind": "year", "content": "{\"title\": \"x\"}"}, "week"),
    ({"kind": "month", "title": "x", "suggestion": "[1]"}, "suggestion"),
])
def test_review_save_refuses_bad_input(bot, values, words):
    with pytest.raises(kit.AppError) as no:
        bot.act("review_save", OWNER, values)
    assert words in no.value.message


def test_review_save_in_pieces_by_command(bot):
    reply = bot.command("review_save", OWNER, "week key=2030-W02 grade=B+ title=這週還可以 summary=午餐都很穩 "
                                              "good=!午餐都在 130 以內;沒有亂買 watch=耳機 1,200 strategy=飲料少一杯")
    assert reply.startswith("存好了：檢討 #") and "※ 午餐都在 130 以內" in reply and "・沒有亂買" in reply
    rv = bot.read("reviews", OWNER, {"kind": "week"})["reviews"][0]
    assert rv["key"] == "2030-W02" and rv["grade"] == "B+" and rv["title"] == "這週還可以"


# ── 畫面：清單、全文、套用、回覆 ──────────────────────────────────────────────────────

def test_reads_have_the_shapes_the_page_uses(bot):
    rid = save(bot, good_answer())["id"]
    lst = bot.read("reviews", OWNER, {"period": "2030-01", "kind": "month"})
    assert {"period", "prev", "next", "reviews", "due"} <= set(lst)
    assert [r["id"] for r in lst["reviews"]] == [rid] and lst["reviews"][0]["status"] == "done"
    rv = bot.read("review", OWNER, {"id": str(rid)})
    assert rv["bucket_names"]["living"] and rv["current_plan"]["period"] == "2030-02"
    assert rv["label"].startswith("2030-01 期") and "metrics" in rv
    with pytest.raises(kit.AppError):
        bot.read("review", OWNER, {"id": "abc"})
    with pytest.raises(kit.AppError) as no:
        bot.read("review", OWNER, {"id": "999"})
    assert "找不到" in no.value.message and not any(w in no.value.message for w in ("ledger.db", "LedgerError"))


def test_apply_changes_next_period_plan_and_can_be_undone(bot):
    rid = save(bot, good_answer())["id"]
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        before = svc(bot, "plans").get(book.conn, "2030-02")["buckets"].get("living")
    r = bot.act("review_apply", OWNER, {"id": str(rid)})
    assert r["period"] == "2030-02" and r["batch_id"] and r["write_seq"]
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        plan = svc(bot, "plans").get(book.conn, "2030-02")
    assert plan["buckets"]["living"] == 20000 != before and plan["source"] == "review"
    assert bot.read("review", OWNER, {"id": str(rid)})["applied_period"] == "2030-02"
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        batches, db = svc(bot, "batches"), importlib.import_module(f"{bot.module.__name__}.engine.db")
        with db.write(book.conn) as w:
            batches.revert(w, r["batch_id"])
    assert bot.read("review", OWNER, {"id": str(rid)})["applied_period"] is None


def test_apply_by_command_says_what_changed(bot):
    rid = save(bot, good_answer())["id"]
    reply = bot.command("review_apply", OWNER, str(rid))
    assert reply.startswith("2030-02 期的計畫改成檢討") and "生活費 20,000" in reply


def test_reply(bot):
    rid = save(bot, good_answer())["id"]
    r = bot.act("review_reply", OWNER, {"id": str(rid), "text": "收到，下個月少買一點"})
    assert r["reply"] == "收到，下個月少買一點" and r["batch_id"]
    assert bot.read("review", OWNER, {"id": str(rid)})["reply"] == "收到，下個月少買一點"
    assert bot.command("review_reply", OWNER, f"{rid}").startswith("用法")             # 少了回覆


def test_week_review_has_no_suggestion(bot):
    out = save(bot, good_answer(), kind="week", key="2030-W02")
    assert out["review"]["suggestion"] is None


def test_other_person_sees_none_of_it(bot):
    rid = save(bot, good_answer())["id"]
    assert bot.read("reviews", OTHER, {})["reviews"] == []
    with pytest.raises(kit.AppError):
        bot.read("review", OTHER, {"id": str(rid)})
    assert "生活支出 0" in bot.command("report", OTHER, "month 2030-01")


# ── 排程：該送什麼、該寫哪一份 ─────────────────────────────────────────────────────

def test_review_due_sends_the_week_report_once(make_money):
    bot = make_money(now=datetime(2030, 2, 1, 20, 0))                    # 週五（預設週報那天）
    seed(bot)
    peek = bot.command("review_due", OWNER, "peek=yes")
    assert "這週的錢" in peek and "該寫檢討了" in peek and "月報 2030-01" in peek
    first = bot.command("review_due", OWNER)
    assert "這週的錢" in first and "/money_report" in first
    again = bot.command("review_due", OWNER)
    assert "這週的錢" not in again and "該寫檢討了" in again             # 送過的不再送；檢討還沒寫就繼續提醒


def test_review_due_lists_the_month_review_then_sends_it(make_money):
    bot = make_money(now=datetime(2030, 1, 31, 20, 0))                   # 1 月期的月檢討日（月底前一天）已到
    seed(bot)
    first = bot.command("review_due", OWNER)
    assert "該寫檢討了" in first and "月報 2030-01" in first and "檢討書" not in first
    save(bot, good_answer())
    after = bot.command("review_due", OWNER)
    assert "這個月的檢討書" in after and "月報 2030-01" not in after
    assert "money plan_apply" not in after and "/money_review_apply" in after
    assert "檢討書" not in bot.command("review_due", OWNER)              # 送過了


def test_review_due_with_no_records(make_money):
    bot = make_money()
    assert bot.command("review_due", OWNER) == "現在沒有要送的，也沒有該寫的檢討"


# ── 一批混合紀錄 ───────────────────────────────────────────────────────────────

def test_ingest_counts_and_resend_is_not_doubled(make_money):
    bot = make_money()
    first = seed(bot)
    assert first.startswith("這一批 7 筆：新記 6") and "支出 1,770" in first and "收入 45,000" in first
    again = seed(bot)
    assert "新記 0" in again and "之前記過 6" in again
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        n = book.conn.execute("SELECT COUNT(*) FROM transactions WHERE status = 'active'").fetchone()[0]
    assert n == 6


def test_ingest_dry_run_and_errors(make_money):
    bot = make_money()
    assert "ingest" not in bot.pack.app_actions                             # 只給聊天與 AI，不是畫面動作
    reply = bot.command("ingest", OWNER, json.dumps(records(), ensure_ascii=False) + " dry_run=yes")
    assert reply.startswith("試算（還沒寫進去）")
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        assert book.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert bot.command("ingest", OWNER, "{壞掉").startswith("❌ JSON 壞掉了")
    bad = {"records": [{"type": "expense", "date": "2030-01-03", "amount": 120, "desc": "x", "account": "不存在的帳戶"}]}
    assert "整批都沒有寫入" in bot.command("ingest", OWNER, json.dumps(bad, ensure_ascii=False))


def test_ingest_too_long_asks_for_batches(make_money):
    bot = make_money()
    cmd = next(c for c in bot.module.review.COMMANDS if c.name == "ingest")
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        with pytest.raises(kit.AppError) as no:
            cmd.run(book, {"records": "x" * 2001})
    assert "分幾批" in no.value.message


def test_ingest_receipt_must_be_in_own_folder(make_money):
    bot = make_money()
    rec = {"type": "expense", "date": "2030-01-03", "amount": 120, "desc": "範例便當店", "attach": "../../secret.png"}
    reply = bot.command("ingest", OWNER, json.dumps(rec, ensure_ascii=False))
    assert reply.startswith("❌") and "自己的資料夾" in reply


def test_review_and_ingest_are_the_only_ai_tools_here(make_money):
    bot = make_money()
    ai = sorted(c.name for c in bot.module.review.COMMANDS if c.ai)
    assert ai == ["ingest", "report", "review_save"]
    assert bot.node("review_save").effect and U(OWNER).tg_id == OWNER
