"""里程碑引擎：建旅程、打卡、錯過順延（rest／extra）、藍圖鎖、溢時縮獎勵。全部假資料。"""
import pytest


def make(M, n=5, **kw):
    payload = {"name": "做小遊戲", "goal": "30 天做完", "start_date": "2030-01-10",
               "plan": [f"第 {i + 1} 步" for i in range(n)], "overtime_days": 2}
    payload.update(kw)
    return M.create(payload)


def test_create_and_view(M):
    v = make(M)
    assert v["plan_len"] == 5 and [c["n"] for c in v["cells"]] == [1, 2, 3, 4, 5]
    assert v["cells"][0]["date"] == "2030-01-10" and v["cells"][0]["is_current"] is True
    assert v["stats"]["total"] == 5 and v["stats"]["level_name"] == "啟程者"
    for gone in ("project_id", "direction"):
        assert gone not in v
    with pytest.raises(ValueError):
        M.create({"name": ""})
    with pytest.raises(ValueError):
        M.create({"name": "x", "cadence": "hourly"})


def test_complete_and_stats(M):
    v = make(M)
    for i in range(3):
        v = M.complete(v["id"], i)
    assert v["stats"]["done"] == 3 and v["stats"]["pct"] == 60 and v["stats"]["streak"] == 3
    assert v["stats"]["level_name"] == "探索者" and v["cells"][3]["is_current"] is True
    v = M.complete(v["id"], 2, done=False)
    assert v["stats"]["done"] == 2
    listing = M.list_all()
    assert listing[0]["done"] == 2 and listing[0]["current"] == "第 3 步"
    with pytest.raises(IndexError):
        M.complete(v["id"], 99)


def test_miss_inserts_rest_and_shifts(M):
    v = make(M)
    v = M.insert(v["id"], 1, "before", "rest", {"sticker": "🍜", "label": "吃飯"})
    assert len(v["cells"]) == 6 and v["cells"][1]["type"] == "rest" and v["stats"]["inserts"] == 1
    assert v["cells"][2]["n"] == 2 and v["cells"][2]["date"] == "2030-01-12"      # 後面順延一天
    with pytest.raises(ValueError):
        M.complete(v["id"], 1)                                                    # 休息不用打卡
    v = M.remove_cell(v["id"], 1)
    assert len(v["cells"]) == 5


def test_blueprint_locked_and_extra(M):
    v = make(M)
    with pytest.raises(ValueError):
        M.remove_cell(v["id"], 0)
    v = M.insert(v["id"], 0, "after", "extra", {"title": "臨時的事"})
    assert v["cells"][1]["type"] == "extra" and v["cells"][1]["icon"] == "⚡"
    assert v["plan_len"] == 5
    v = M.insert(v["id"], 0, "after", "task", {"title": "補排的一步", "blueprint": True})
    assert v["plan_len"] == 6 and v["cells"][1]["ins"] is False
    with pytest.raises(ValueError):
        M.insert(v["id"], 0, "after", "nope")


def test_overtime_shrinks_reward_but_never_zero(M):
    v = make(M, n=2, overtime_days=0)
    assert v["stats"]["reward_mult"] == 1
    for _ in range(40):
        v = M.insert(v["id"], 0, "after", "rest")
    assert v["stats"]["reward_mult"] == 0.35 and v["stats"]["over"] == 40


def test_edit_cell_notes_and_update_delete(M):
    v = make(M)
    v = M.edit_cell(v["id"], 0, {"mood": "不錯", "add_note": "順利"})
    assert v["cells"][0]["mood"] == "不錯" and v["cells"][0]["notes"] == ["順利"]
    v = M.update(v["id"], {"name": "新名字", "remind_time": "7:05", "cadence": "weekly"})
    assert v["name"] == "新名字" and v["remind_time"] == "07:05" and v["cadence"] == "weekly"
    assert v["cells"][0]["title"] == "第 1 步"                                    # 藍圖標題不從這裡改
    assert M.delete(v["id"]) is True and M.delete(v["id"]) is False
    with pytest.raises(KeyError):
        M.get(v["id"])


def test_occurrences_feed_only_tasks(M):
    v = make(M)
    M.insert(v["id"], 0, "after", "rest")
    M.complete(v["id"], 0)
    rows = M.occurrences("2030-01-10", "2030-01-20")
    assert len(rows) == 5 and rows[0]["done"] is True and rows[1]["done"] is False
    assert all(r["milestone_id"] == v["id"] for r in rows)


def test_old_record_migrates(M, mem):
    mem.save("milestones", {"milestones": [{"id": "abc12345", "name": "舊旅程", "project_id": "p", "direction": "d",
                                             "cells": [{"type": "task", "title": "t", "shot": "x.png"}]}]})
    v = M.get("abc12345")
    assert v["plan_len"] == 1 and v["cadence"] == "daily"
