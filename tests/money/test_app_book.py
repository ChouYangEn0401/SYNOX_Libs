"""明細頁（W3）走真的 SYNOX：真的 loader 把 packs/money 當多檔工具載入，畫面用 SYNOX 的 AppClient（直接呼叫，不開埠）。

- 畫面送的值跟 book.js 一樣（金額、種類、帳戶都是字串）；打字的路（/money …）跟畫面的路記出來的一樣。
- 危險動作（刪一筆、撤銷指定那一批）SYNOX 會先要確認。
- 伺服器版：兩個成員各自一本帳，互相看不到。
"""

import os
import shutil
import subprocess
import sys

import pytest

from isdtelekit.users.roles import BasicRoles

from conftest import OWNER_ID

from .conftest import MONEY, TODAY

NODE = shutil.which("node")


def _fix_today(monkeypatch):
    session = sys.modules["synox_pack_money.bridge.session"]
    monkeypatch.setattr(session, "now", lambda: TODAY)


def opened(bot, who=OWNER_ID):
    from synox.web import apps
    from synox.web.apps.testing import AppClient, FakeListen
    manager = apps.of(bot.app)
    if not isinstance(manager.listen, FakeListen):
        manager.listen = FakeListen()
    if "money" not in manager.running():
        manager.start("money", by=OWNER_ID, via="web")
    client = AppClient(manager, "money")
    client.enter(client.open_from_console(who))
    return client


def ok(resp):
    status, data = resp
    assert status == 200 and data.get("ok"), (status, data)
    return data["data"]


@pytest.fixture
def money_app(synox_bot, monkeypatch):
    def factory(mode="personal"):
        bot = synox_bot("money", mode=mode)
        _fix_today(monkeypatch)
        return bot
    return factory


def test_page_reads_and_record_like_book_js(money_app):
    bot = money_app()
    client = opened(bot)
    meta = ok(client.read("meta"))
    assert meta["today"] == "2030-01-15" and meta["piggy"] is False
    ref = ok(client.read("ref"))
    assert ref["default_account"] == "現金" and ref["categories_by_kind"]["expense"]
    # book.js 的「記一筆」：POST /api/txn → 轉接器挑 add，body 每一格變成字串
    data = ok(client.do("add", {"amount": "120", "desc": "範例便當店", "account": "現金", "date": "2030-01-15",
                                "kind": "expense", "category": "午餐"}))
    assert data["results"][0]["amount"] == 120 and data["batch_id"] and data["write_seq"] == meta["write_seq"] + 1
    pb = ok(client.read("passbook", period=meta["current"]))
    assert pb["closing"] == -120 and pb["items"][0]["item"] == "範例便當店"
    assert pb["period"]["key"] == meta["current"] and "prev" in pb
    items = ok(client.read("items", category="午餐"))
    assert items["items"] == ["範例便當店"]
    # 點開一筆改金額（PATCH /api/txn/:id → edit）
    tid = pb["items"][0]["id"]
    data = ok(client.do("edit", {"id": tid, "amount": "150"}))
    assert data["txn"]["amount"] == 150 and data["batch_id"]
    assert ok(client.read("txn", id=tid))["amount"] == 150


def test_typed_and_page_paths_agree(money_app):
    bot = money_app()
    bot.say(OWNER_ID, "/money 120 午餐 範例便當店")
    reply = bot.api.last(OWNER_ID)
    assert "120" in reply and "記好了" in reply
    client = opened(bot)
    ok(client.do("add", {"amount": "120", "category": "午餐", "desc": "範例便當店", "kind": "expense"}))
    rows = ok(client.read("passbook"))["items"]
    assert len(rows) == 2
    keep = ("title", "detail", "category", "account", "amount", "kind", "date")
    assert {k: rows[0][k] for k in keep} == {k: rows[1][k] for k in keep}


def test_danger_actions_ask_first_and_undo_restores(money_app):
    bot = money_app()
    client = opened(bot)
    ok(client.do("in", {"amount": "1000", "category": "薪資", "kind": "income"}))
    ok(client.do("add", {"amount": "50", "desc": "範例飲料", "kind": "expense"}))
    undone = ok(client.do("undo", {}))                                         # 剛剛那筆不要了
    assert undone["batch_id"] and ok(client.read("passbook"))["closing"] == 1000
    assert ok(client.read("batches", limit="3"))["batches"][0]["command"] == "undo"
    added = ok(client.do("add", {"amount": "300", "desc": "範例", "kind": "expense"}))
    tid = added["results"][0]["id"]
    status, data = client.do("rm", {"id": tid})
    assert status == 409 and data.get("code") == "confirm"                    # SYNOX 先問
    assert "刪掉這一筆" in str(data)
    removed = ok(client.do_confirmed("rm", {"id": tid}))
    assert removed["batch_id"]
    assert ok(client.read("passbook"))["closing"] == 1000
    ok(client.do_confirmed("revert", {"id": removed["batch_id"]}))            # 畫面上的「復原」
    assert ok(client.read("passbook"))["closing"] == 700


def test_engine_errors_come_back_in_plain_words(money_app):
    bot = money_app()
    client = opened(bot)
    status, data = client.do("transfer", {"amount": "500", "to": "不存在的帳戶"})
    assert status in (400, 404, 409), (status, data)
    assert "不存在的帳戶" in str(data) and "C:" not in str(data)


def test_two_members_never_see_each_other(money_app):
    bot = money_app(mode="server")
    for who in (77, 78):
        bot.make(who, BasicRoles.MEMBER, f"u{who}")
    bot.say(77, "/money 120 午餐 範例便當店", chat_id=77)
    assert "記好了" in bot.api.last(77)
    other = opened(bot, 78)
    assert ok(other.read("passbook"))["items"] == []
    mine = opened(bot, 77)
    rows = ok(mine.read("passbook"))["items"]
    assert len(rows) == 1
    status, _data = other.do("edit", {"id": rows[0]["id"], "amount": "1"})
    assert status != 200
    status, _data = other.read("txn", id=rows[0]["id"])
    assert status == 404
    assert ok(mine.read("txn", id=rows[0]["id"]))["amount"] == 120


@pytest.mark.skipif(NODE is None, reason="這台沒有 node")
def test_book_js_parses_and_keeps_its_exports():
    path = os.path.join(MONEY, "app", "js", "views", "book.js")
    out = subprocess.run([NODE, "--check", path], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    with open(path, encoding="utf-8") as f:
        text = f.read()
    for word in ("innerHTML", "style=", "sessionStorage", "location.href", "fetch(", "setInterval("):
        assert word not in text, word
    for name in ("export async function render", "export function recordForm", "export function payKey",
                 "export const KINDS"):
        assert name in text, name
