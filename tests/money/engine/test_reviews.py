"""週報／月報檢討（不叫 AI 的部分）：到期判斷、存檢討（清掉 emoji、驗證建議）、推送、回覆、套用、補記後重做。

檢討的內容一律用 save_manual 直接存（跟外部整理好再存進來的路一樣，都會經過 validate）。
"""
from datetime import date

import pytest

from money_engine import db
from money_engine.errors import Conflict
from money_engine.services import batches, ingest, plans, report, reviews, settings

from .reviewdata import EXPENSES, seed


def good_answer(base=45000, living=12500, **over):
    data = {"grade": "B", "title": "午餐穩定，吃好喝好偏高 ❌",
            "summary": "這期生活支出比預算少 ✅，但 9/30 燒肉 1,200 一次吃掉半個月吃好喝好。",
            "good": ["午餐大多在 140 以內"], "watch": ["購物 2,400 的耳機超出預算"], "strategy": ["飲料一週最多兩杯"],
            "suggestion": {"buckets": {"giving": 4500, "fixed": 12000, "saving": 8000, "living": living, "invest": 5000,
                                       "debt": base - 4500 - 12000 - 8000 - living - 5000},
                           "group_budgets": {}, "saving_priority": ["生活帳戶", "不存在的戶頭"], "reason": "購物先收一點"}}
    data.update(over)
    return data


def save(conn, kind, key, data):
    with db.write(conn) as w:
        rid = reviews.save_manual(w, kind, key, data)
    return reviews.get(conn, rid)


@pytest.fixture
def conn():
    c = db.connect()
    seed(c)
    return c


def groups_for(conn, living):
    """建議裡的各大類預算：照現在的生活支出大類，加總等於 living。"""
    names = [g for g in plans.default_group_budgets(conn)]
    out = {g: 0 for g in names}
    out[names[0]] = living
    return out


def test_month_review_day_and_weekly_default_on():
    """月檢討在期名那個月的日曆月底前一天（設定可改）；週檢討預設開，review.weekly=false 才關掉。"""
    c = db.connect()
    ingest.run(c, {"records": [{"type": "expense", "date": "2037-10-06", "amount": 80, "desc": "早餐", "category": "早餐"}]})
    assert reviews.month_due_date("2037-10", 1) == date(2037, 10, 30) and reviews.month_due_date("2037-02", 1) == date(2037, 2, 27)
    assert "week" in [i["kind"] for i in reviews.due(c, date(2037, 10, 29))]      # 預設開：週檢討會到期
    with db.write(c) as w:
        settings.put(w, "review.weekly", False)
    assert [i["kind"] for i in reviews.due(c, date(2037, 10, 29))] == []          # 10-29：還沒到、上一期沒記帳
    assert [(i["kind"], i["key"]) for i in reviews.due(c, date(2037, 10, 30))] == [("month", "2037-10")]
    assert reviews.month_key_due(c, date(2037, 11, 3)) == "2037-10"                # 2037-10 期到 11/04
    with db.write(c) as w:
        settings.put(w, "review.month_days_before_end", 3)
    assert reviews.due(c, date(2037, 10, 28))[0]["key"] == "2037-10" and not reviews.due(c, date(2037, 10, 27))


def test_due(conn):
    items = reviews.due(conn)
    assert [(i["kind"], i["key"], i["action"]) for i in items] == [
        ("month", "2037-09", "run"),
        ("week", "2037-W38", "skip"), ("week", "2037-W39", "skip"), ("week", "2037-W40", "run"), ("week", "2037-W41", "run")]
    assert reviews.due(db.connect(":memory:")) == []             # 沒有任何記帳：什麼都不做


def test_prompt_and_schema_for_month_and_week(conn):
    """月檢討的格式要求分配建議、提示裡有收入與小日記；週檢討不給分配建議。"""
    month = report.build(conn, "month", "2037-09", with_transactions=False)
    ctx = reviews._context(conn, month)
    assert "suggestion" in reviews.schema(ctx, "month")["properties"]
    text = reviews.prompt(month, ctx)
    assert "45000" in text and "朋友生日" in text
    week = report.build(conn, "week", "2037-W40", with_transactions=False)
    assert "suggestion" not in reviews.schema(reviews._context(conn, week), "week")["properties"]


def test_saved_reviews_are_cleaned_and_digested(conn):
    answer = good_answer()
    answer["suggestion"]["group_budgets"] = groups_for(conn, 12500)
    rv = save(conn, "month", "2037-09", answer)
    save(conn, "week", "2037-W40", good_answer())
    assert rv["title"] == "午餐穩定，吃好喝好偏高" and "✅" not in rv["summary"] and rv["source"] == "manual"
    assert rv["suggestion"]["valid"] and rv["suggestion"]["period"] == "2037-10"
    assert rv["suggestion"]["saving_priority"] == ["生活帳戶"] and "不存在的戶頭" in rv["problems"][0]
    assert rv["metrics"]["living"]["spent"] == sum(e[2] for e in EXPENSES) and "transactions" not in rv["metrics"]
    assert reviews.latest(conn, "week")["suggestion"] is None                  # 週報不給分配建議
    assert not [i for i in reviews.due(conn) if i["action"] == "run" and i["key"] in ("2037-09", "2037-W40")]
    d = reviews.pending_digest(conn)
    assert d["count"] == 2 and "【月報】2037-09 期" in d["text"] and "❌" not in d["text"] and len(d["text"]) <= 3500
    assert "建議 2037-10 期分配" in d["text"]
    with db.write(conn) as w:
        reviews.mark_sent(w, d["ids"])
    assert reviews.pending_digest(conn)["count"] == 0


def test_apply_plan_and_revert(conn):
    answer = good_answer(living=11000)
    answer["suggestion"]["group_budgets"] = groups_for(conn, 11000)
    rv = save(conn, "month", "2037-09", answer)
    with db.write(conn) as w:
        r = reviews.apply_plan(w, rv["id"])
        batch = w.batch_id
    p = plans.get(conn, "2037-10")
    assert r["period"] == "2037-10" and p["source"] == "review" and p["review_id"] == str(rv["id"])
    assert p["buckets"]["living"] == 11000 and p["living_budget"] == 11000
    assert reviews.get(conn, rv["id"])["applied_period"] == "2037-10"
    with db.write(conn) as w:
        batches.revert(w, batch)
    assert plans.get(conn, "2037-10")["source"] != "review" and reviews.get(conn, rv["id"])["applied_period"] is None


def test_invalid_suggestion_is_kept_but_not_applied(conn):
    answer = good_answer()
    answer["suggestion"]["buckets"]["invest"] = 9999                # 加總不等於收入
    answer["suggestion"]["group_budgets"] = groups_for(conn, 12500)
    rv = save(conn, "month", "2037-09", answer)
    assert rv["status"] == "done" and not rv["suggestion"]["valid"] and any("不等於" in p for p in rv["problems"])
    with pytest.raises(Conflict):
        with db.write(conn) as w:
            reviews.apply_plan(w, rv["id"])
    assert "建議" not in reviews.digest_text(rv)                     # 對不起來的建議不推送
    with db.write(conn) as w:
        assert reviews.apply_plan(w, rv["id"], force=True)["period"] == "2037-10"


def test_week_digest_once_and_reply(conn):
    rv = save(conn, "week", "2037-W40", {"grade": "B+", "title": "穩穩的一週", "summary": "週摘要", "good": ["好"],
                                         "watch": ["注意"], "strategy": ["下週"]})
    assert rv["status"] == "done" and rv["grade"] == "B+"
    d = reviews.pending_digest(conn)
    assert d["count"] == 1 and "❌" not in d["text"]
    with db.write(conn) as w:
        reviews.mark_sent(w, d["ids"])
    again = reviews.pending_digest(conn)
    assert again["count"] == 0 and again["text"] == ""
    with db.write(conn) as w:
        assert reviews.reply(w, rv["id"], "好，下週少買飲料")["reply"] == "好，下週少買飲料"


def test_save_then_apply_and_list(conn):
    answer = good_answer(living=12000)
    answer["suggestion"]["group_budgets"] = groups_for(conn, 12000)
    rv = save(conn, "month", "2037-09", answer)
    assert rv["source"] == "manual" and rv["suggestion"]["valid"]
    with db.write(conn) as w:
        r = reviews.apply_plan(w, rv["id"])
    assert r["period"] == "2037-10" and r["plan"]["buckets"]["living"] == 12000
    assert plans.get(conn, "2037-10")["source"] == "review"
    assert reviews.all_reviews(conn)[0]["key"] == "2037-09"


def test_giving_cut_is_flagged(conn):
    with db.write(conn) as w:
        plans.set_plan(w, "2037-10", buckets={"giving": 5000})
    answer = good_answer()                                          # 建議捐贈 4,500，比 10 月期的 5,000 少
    answer["suggestion"]["group_budgets"] = groups_for(conn, 12500)
    rv = save(conn, "month", "2037-09", answer)
    assert rv["suggestion"]["valid"] and any("捐贈 4,500 比 2037-10 期目前的 5,000 少" in p for p in rv["problems"])


def test_empty_skip_is_redone_after_backfill():
    conn = db.connect()
    with db.write(conn) as w:
        settings.put(w, "review.weekly", True)
    ingest.run(conn, {"records": [{"type": "expense", "date": "2037-09-06", "amount": 50, "desc": "早餐", "category": "早餐"}]})
    rv = reviews.skip(conn, "week", "2037-W40", reviews.EMPTY)
    assert rv["status"] == "skipped" and rv["error"] == reviews.EMPTY
    assert ("week", "2037-W40") not in [(i["kind"], i["key"]) for i in reviews.due(conn) if i["action"] == "run"]
    ingest.run(conn, {"records": [{"type": "expense", "date": "2037-10-01", "amount": 80, "desc": "午餐", "category": "午餐"}]})
    item = next(i for i in reviews.due(conn) if i["key"] == "2037-W40")
    assert item["action"] == "run" and "補記" in item["reason"]
    assert save(conn, "week", "2037-W40", good_answer())["status"] == "done"


def test_due_key_without_a_given_key(conn):
    assert reviews.due_key(conn, "week") == "2037-W41"            # 今天週六＝檢討日過了，看這一週
    assert reviews.due_key(conn, "month") == "2037-09"


def test_week_review_day_follows_notify_weekday(conn):
    """週檢討＝這一週，檢討日跟數字週報同一天（notify.weekday，預設 4＝週五）；那天還沒到就看上一週。"""
    assert reviews.due_key(conn, "week", date(2037, 10, 8)) == "2037-W40"       # 週四：這一週還沒到檢討日
    assert reviews.due_key(conn, "week", date(2037, 10, 9)) == "2037-W41"       # 週五：這一週
    assert reviews.due_key(conn, "week", date(2037, 10, 11)) == "2037-W41"      # 週日：還是這一週
    with db.write(conn) as w:
        settings.put(w, "notify.weekday", 6)                                    # 改成週日才送
    assert reviews.due_key(conn, "week", date(2037, 10, 9)) == "2037-W40"
    assert reviews.due_key(conn, "week", date(2037, 10, 11)) == "2037-W41"


def test_points_carry_importance(conn):
    """每一點都有重要度（key／normal）——收合時只看 key，推送時 key 前面是「※」。
    舊的檢討（純字串）讀出來也會補成 {text, level}。"""
    answer = good_answer()
    answer["good"] = [{"text": "午餐穩定在 140 以內", "level": "key"}, {"text": "飲料少買了兩杯", "level": "normal"}]
    answer["watch"] = [{"text": "燒肉 1,200 一次吃掉半個月吃好喝好", "level": "key"}]
    answer["suggestion"]["group_budgets"] = groups_for(conn, 12500)
    rv = save(conn, "month", "2037-09", answer)
    assert rv["good"][0] == {"text": "午餐穩定在 140 以內", "level": "key"}
    assert rv["good"][1]["level"] == "normal"
    text = reviews.digest_text(rv, plans.bucket_names(conn))
    assert "※ 午餐穩定在 140 以內" in text and "・飲料少買了兩杯" in text   # 推送不能有 emoji

    # 舊資料：純字串存的檢討，讀出來不能壞
    conn.execute("UPDATE reviews SET good = ? WHERE id = ?", ('["舊的一句話"]', rv["id"]))
    again = reviews.get(conn, rv["id"])
    assert again["good"] == [{"text": "舊的一句話", "level": "normal"}]
