"""總覽分頁（app/js/views/home.js＋bridge/home.py，W4）。

1. 真的 SYNOX 載入 packs/money，畫面用 SYNOX 的 AppClient（直接呼叫，不開埠）：讀與動作走得通、錯誤是白話、
   打字的路與畫面的路結果一樣。
2. 用 node 跑 home.js（最小的假 DOM，不開瀏覽器）：
   - 每個狀態都有白話字（照原本 state_text_check 的規則：錢怎麼分每一格、燈號、日曆、圖例、第一次用的三步）；
   - bridge 回的真實資料（新帳本、記了幾筆、有挪用／跳過／滾存）畫得出來，畫面上沒有 undefined／NaN／null。
資料一律是假的（「範例便當店」「現金」「120」）。
"""

import importlib
import json
import os
import sys

import pytest

from .conftest import MONEY, NODE, OWNER, TODAY, run_node_check

APP_JS = os.path.join(MONEY, "app", "js")
PERIOD = "2030-01"


# ── 1. 真的 SYNOX＋AppClient ───────────────────────────────────────────────────────

@pytest.fixture
def opened(synox_bot, monkeypatch):
    from synox.web import apps
    from synox.web.apps.testing import FakeListen

    def factory():
        bot = synox_bot("money")
        monkeypatch.setattr(sys.modules["synox_pack_money.bridge.session"], "now", lambda: TODAY)
        manager = apps.of(bot.app)
        manager.listen = FakeListen()
        manager.start("money", by=OWNER, via="web")
        client = Screen(manager, "money")
        client.enter(client.open_from_console(OWNER))
        return bot, client
    return factory


def _unwrap(resp):
    """工具服務回 {"ok": true, "data": …}；錯誤回 {"ok": false, …}（原樣給）。"""
    status, body = resp
    if isinstance(body, dict) and body.get("ok") is True and "data" in body:
        return status, body["data"]
    return status, body


def _screen_class():
    from synox.web.apps.testing import AppClient

    class _Screen(AppClient):
        def read(self, name, **params):
            return _unwrap(super().read(name, **params))

        def do(self, name, values=None, confirmed=False):
            return _unwrap(super().do(name, values, confirmed))
    return _Screen


def Screen(manager, name):
    return _screen_class()(manager, name)


def test_screen_reads_and_actions_work_through_synox(opened):
    bot, client = opened()
    status, home = client.read("home")
    assert status == 200 and home["onboarding"] is True and home["period"]["key"] == PERIOD
    for name in ("year", "allowance", "conflicts", "moves"):
        status, data = client.read(name)
        assert status == 200 and isinstance(data, dict), name
    status, data = client.do("bucket_skip", {"id": "saving", "undo": "no"})
    assert status == 200 and data["skipped"] is True and data["batch_id"]
    status, home = client.read("home", period=PERIOD)
    assert home["onboarding"] is True                  # 還沒記過帳、也沒做完第一次使用：跳過一格不算開始用（setup.needs_onboarding）
    assert next(e for e in home["envelopes"] if e["id"] == "saving")["state"] == "skipped"
    status, data = client.do("fixed_post", {"upcoming": "no"})
    assert status == 200 and data["created"] == [] and data["batch_id"] is None


def test_screen_errors_are_plain_words(opened):
    bot, client = opened()
    status, data = client.do("bucket_skip", {"id": "沒有這格"})
    assert status == 404 and "沒有「沒有這格」這一格" in data["error"]
    status, data = client.do("move", {"from_bucket": "saving", "to_bucket": "saving", "amount": "10"})
    assert status == 400 and "要不一樣" in data["error"]
    status, data = client.read("home", period="一月")
    assert status == 400 and "看不懂" in data["error"]


def test_typed_and_screen_paths_end_the_same(opened):
    a_bot, a = opened()
    a_bot.say(OWNER, "/money_move 存錢 生活費 300 note=範例")
    status, typed = a.read("moves")
    assert status == 200 and len(typed["items"]) == 1
    status, data = a.do("move", {"from_bucket": "saving", "to_bucket": "living", "amount": "300", "note": "範例"})
    assert status == 200 and data["warnings"] and data["batch_id"]          # 存錢這期沒有額度：照樣挪，回一句提醒
    status, both = a.read("moves")
    first, second = both["items"][1], both["items"][0]
    keep = ("from", "to", "amount", "note", "period")
    assert {k: first[k] for k in keep} == {k: second[k] for k in keep}
    status, data = a.do("move", {"undo": str(second["id"])})
    assert status == 200 and data["move"]["undone"] is True
    assert len(a.read("moves")[1]["items"]) == 1


# ── 2. node 跑 home.js ───────────────────────────────────────────────────────────

# 腳本在 tests/money/js/（共用的假 DOM 在 prelude.mjs）；conftest.run_node_check 跑、讀回最後一行 JSON。


def home_js():
    return os.path.join(APP_JS, "views", "home.js")


@pytest.mark.skipif(NODE is None, reason="這台電腦沒有 node")
def test_every_state_has_plain_words():
    results = run_node_check("state_text_check.mjs", home_js())
    failed = [f"{label}：{why}" for label, why in results if why]
    assert len(results) > 30 and not failed, failed


def eng(bot, name):
    return importlib.import_module(f"{bot.module.__name__}.engine.{name}")


def busy_book(bot):
    """記了幾筆、有計畫、有挪用、有跳過、有滾存的格子、有固定項目、有沒分類的。"""
    db, plans, recurring, ingest = (eng(bot, n) for n in ("db", "services.plans", "services.recurring",
                                                           "services.ingest"))
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        with db.write(book.conn, actor="test", source="test") as w:
            plans.add_bucket(w, "旅遊基金")
            plans.add_bucket(w, "健身")
            plans.set_bucket(w, "extra1", rollover=True, rollover_from=PERIOD)
            plans.set_plan(w, PERIOD, income_expected=40000,
                           buckets={"fixed": 10000, "saving": 5000, "extra1": 3000, "extra2": 800},
                           group_budgets={"食": 6200})
            recurring.add(w, name="房租", kind="expense", amount=8000, day_of_month=5, category="房租", bucket="fixed")
        base = {"type": "expense", "account": "現金", "date": "2030-01-14"}
        ingest.run(book.conn, {"records": [{**base, "amount": 120, "category": "午餐", "desc": "範例便當店"},
                                           {**base, "amount": 90, "desc": "沒分類的東西", "date": "2030-01-10"},
                                           {**base, "amount": 2500, "category": "大額採買", "desc": "範例吸塵器",
                                            "nature": "stock"},
                                           {**base, "type": "income", "amount": 30000, "category": "薪資",
                                            "account": "銀行帳戶", "date": "2030-01-05", "desc": "範例公司"}]},
                   actor="test", source="test")
    bot.act("move", OWNER, {"from_bucket": "saving", "to_bucket": "living", "amount": "500", "note": "範例"})
    bot.act("bucket_skip", OWNER, {"id": "extra2"})


@pytest.mark.skipif(NODE is None, reason="這台電腦沒有 node")
@pytest.mark.parametrize("busy", [False, True], ids=["new-book", "busy-book"])
def test_real_payloads_render_without_broken_words(make_money, busy, tmp_path):
    bot = make_money()
    if busy:
        busy_book(bot)
    data = {"home": bot.read("home", OWNER), "year": bot.read("year", OWNER)}
    payload = tmp_path / "payloads.json"
    payload.write_text(json.dumps(data), encoding="utf-8")
    shown = run_node_check("render_home.mjs", home_js(), str(payload))
    for key in ("text", "yearText"):
        for broken in ("undefined", "NaN", "null", "[object"):
            assert broken not in shown[key], (key, broken, shown[key][:2000])
    # 新帳本會多讀一次 features（第一次使用的引導用；這個假環境讀不到完整資料，引導就不擋總覽）
    assert shown["calls"] == (["home", "year"] if busy else ["home", "features", "year"])
    if not busy:
        assert shown["onboardDone"] is True                       # 新帳本：叫了 onboard.js，這次不再叫
        assert "第一次用？照這三步開始" in shown["text"]
    else:
        assert shown["onboardDone"] is False
        text = shown["text"]
        for words in ("生活費還可以花", "錢怎麼分", "做完的 1 格 ✓", "挪入 +500", "這期的挪用", "旅遊基金", "長期用", "健康檢查",
                      "薪水週期：✓ 已入帳"):
            assert words in text, words
