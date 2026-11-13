"""💰 記帳的完整畫面（ADR-0003 §3）：用真的 SYNOX 載入 packs/money，畫面用 SYNOX 的 AppClient（直接呼叫，不開埠）。"""
import csv
import io
import json
import os
import sys
from datetime import datetime

import pytest

from isdtelekit.users.roles import BasicRoles

from conftest import OWNER_ID, PACKS

MONEY = os.path.join(PACKS, "money")
FIXED_NOW = datetime(2026, 11, 9, 9, 30)


def _now(monkeypatch):
    mod = sys.modules["synox_pack_money"]
    monkeypatch.setattr(mod, "now", lambda: FIXED_NOW)
    return mod


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
    return manager, client


def entries_of(bot, user=OWNER_ID):
    vault = bot.app.extras["vault"]
    box = vault.for_user(user) if bot.app.extras["synox_config"].mode == "server" else vault
    data = box.load_json("money", {}) or {}
    return [{k: v for k, v in e.items() if k != "at"} for e in data.get("entries", [])], \
        {k: v for k, v in data.items() if k != "entries"}


def test_gates_tier_and_specs(synox_bot):
    from synox.packs import scan, tier
    from synox.packs.manifest import read
    m = read(MONEY, strict=True)
    report = scan.scan(m)
    assert report.problems(m) == [] and not [f for f in report.findings if f.kind == "app"]
    assert tier.assess(m, report)["tier"] == "T1"
    bot = synox_bot("money")
    from synox.web import apps
    manager = apps.of(bot.app)
    assert "money" in manager.specs() and manager.problems() == {}


ACTIONS = [
    ("add", "/money 120 午餐 跟同事", {"amount": "120", "category": "午餐", "memo": "跟同事"}),
    ("in", "/money_in 3000 薪資", {"amount": "3000", "category": "薪資"}),
    ("transfer", "/money_transfer 500 存錢", {"amount": "500", "category": "存錢"}),
    ("budget", "/money_budget 9000", {"amount": "9000"}),
    ("cap", "/money_cap 飲料 300", {"category": "飲料", "amount": "300"}),
    ("cat_add", "/money_cat_add 飲料 食 keywords=咖啡", {"name": "飲料", "group": "食", "keywords": "咖啡"}),
    ("template", "/money_template 基本", {"name": "基本"}),
]


@pytest.mark.parametrize("action,typed,values", ACTIONS)
def test_command_and_action_store_the_same(synox_bot, monkeypatch, action, typed, values):
    """同一件事兩條路：伺服器版兩個成員，一個打指令、一個在畫面上做，兩人保管箱裡的資料一樣（at 除外）。"""
    bot = synox_bot("money", mode="server")
    _now(monkeypatch)
    for who in (77, 78):
        bot.make(who, BasicRoles.MEMBER, f"u{who}")
        bot.say(who, "/money 10 先有一筆", chat_id=who)
    bot.say(77, typed, chat_id=77)
    _m, client = opened(bot, 78)
    status, data = client.do(action, values)
    assert status == 200, data
    assert entries_of(bot, 77) == entries_of(bot, 78)


def test_undo_rm_edit_both_ways(synox_bot, monkeypatch):
    bot = synox_bot("money", mode="server")
    _now(monkeypatch)
    for who in (77, 78):
        bot.make(who, BasicRoles.MEMBER, f"u{who}")
        for line in ("/money 10 早餐", "/money 20 午餐", "/money 30 晚餐"):
            bot.say(who, line, chat_id=who)
    _m, client = opened(bot, 78)
    for typed, (action, values) in (("/money_undo", ("undo", {})), ("/money_rm 1", ("rm", {"n": "1"})),
                                    ("/money_edit 2 amount=25 memo=改過", ("edit", {"n": "2", "amount": "25", "memo": "改過"}))):
        bot.say(77, typed, chat_id=77)
        status, data = client.do_confirmed(action, values)
        assert status == 200, data
    assert entries_of(bot, 77) == entries_of(bot, 78)


@pytest.mark.parametrize("values,field", [({"amount": "abc"}, "amount"), ({"amount": "10", "date": "明年某天"}, "date")])
def test_field_messages_match_commands(synox_bot, values, field):
    bot = synox_bot("money")
    _m, client = opened(bot)
    status, data = client.do("add", values)
    bot.say(OWNER_ID, "/money " + " ".join(f"{k}={v}" for k, v in values.items()))
    assert status == 400 and data["fields"][field] in bot.last(OWNER_ID)
    status, data = client.do("in", {"amount": "10", "kind": "x"})
    assert status == 400
    status, data = client.do("cat_add", {"name": "x" * 21})
    bot.say(OWNER_ID, "/money_cat_add " + "x" * 21)
    assert status == 400 and data["fields"]["name"] in bot.last(OWNER_ID)


def test_rm_confirm_and_missing(synox_bot):
    bot = synox_bot("money")
    bot.say(OWNER_ID, "/money 120 午餐 跟同事")
    _m, client = opened(bot)
    status, data = client.do("rm", {"n": "1"})
    assert status == 409 and "午餐 120 元" in data["confirm"]["text"] and "不能復原" in data["confirm"]["text"]
    assert len(entries_of(bot)[0]) == 1
    assert client.do("rm", {"n": "1"}, confirmed=True)[0] == 200 and entries_of(bot)[0] == []
    status, data = client.do("rm", {"n": "99"}, confirmed=True)
    assert status == 409 and data["code"] == "refused" and data["error"] == "沒有 #99 這筆（/money_list 看編號）。"


def test_edit_rules(synox_bot, monkeypatch):
    bot = synox_bot("money")
    _now(monkeypatch)
    bot.say(OWNER_ID, "/money_cat_add 存錢 kind=轉帳")
    bot.say(OWNER_ID, "/money_budget 100")
    bot.say(OWNER_ID, "/money 50 午餐 跟同事 account=現金")
    bot.say(OWNER_ID, "/money_edit 1 amount=150")
    assert "✏️ 改好了：#1" in bot.last(OWNER_ID) and "150 元" in bot.last(OWNER_ID) and "超過預算" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/money_edit 1 memo=- account=-")
    e = entries_of(bot)[0][0]
    assert e["memo"] == "" and "account" not in e and e["amount"] == 150
    bot.say(OWNER_ID, "/money_edit 1 category=存錢")
    assert entries_of(bot)[0][0]["kind"] == "transfer"
    bot.say(OWNER_ID, "/money_edit 1 category=不在清單")
    assert entries_of(bot)[0][0]["kind"] == "transfer" and entries_of(bot)[0][0]["category"] == "不在清單"
    bot.say(OWNER_ID, "/money_edit 1 category=-")
    assert entries_of(bot)[0][0]["category"] == "待分類"
    bot.say(OWNER_ID, "/money_edit 9 amount=1")
    assert "沒有 #9 這筆" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/money_edit 1")
    assert "至少一個" in bot.last(OWNER_ID)
    _m, client = opened(bot)
    status, data = client.do("edit", {"n": "1", "date": "2026-10-03"})
    assert status == 200 and data["data"]["entry"]["date"] == "2026-10-03"
    assert client.do("edit", {"n": "1"})[1]["error"].startswith("要給至少一個")


def test_reads(synox_bot, monkeypatch):
    bot = synox_bot("money")
    _now(monkeypatch)
    for line in ("/money 120 午餐", "/money 80 咖啡 date=2026-10-05", "/money_in 3000 薪資", "/money_budget 1000"):
        bot.say(OWNER_ID, line)
    _m, client = opened(bot)
    status, month = client.read("month", month="2026-11")
    data = month["data"]
    assert set(data) == {"month", "expense", "income", "transfer", "budget", "left", "by_category", "caps", "months"}
    bot.say(OWNER_ID, "/money_month 2026-11")
    assert f"支出共 {int(data['expense'])} 元" in bot.last(OWNER_ID) and data["months"] == ["2026-11", "2026-10"]
    ent = client.read("entries")[1]["data"]
    assert [e["id"] for e in ent["entries"]] == [3, 2, 1] and ent["more"] is False and ent["sums"]["income"] == 3000
    assert [e["id"] for e in client.read("entries", month="2026-10")[1]["data"]["entries"]] == [2]
    assert [e["id"] for e in client.read("entries", kind="收入")[1]["data"]["entries"]] == [3]
    assert [e["id"] for e in client.read("entries", keyword="午")[1]["data"]["entries"]] == [1]
    page = client.read("entries", limit="1")[1]["data"]
    assert len(page["entries"]) == 1 and page["more"] is True
    assert [e["id"] for e in client.read("entries", before="3")[1]["data"]["entries"]] == [2, 1]
    cats = client.read("categories")[1]["data"]
    assert set(cats) == {"categories", "templates", "kinds", "unsorted"}
    status, bad = client.read("month", month="2026-13")
    assert status == 400 and "要像 2026-11" in bad["error"]


def test_server_isolation_and_ciphertext(synox_bot):
    bot = synox_bot("money", mode="server")
    for who in (77, 78):
        bot.make(who, BasicRoles.MEMBER, f"u{who}")
    _m, a = opened(bot, 77)
    _m, b = opened(bot, 78)
    assert a.do("add", {"amount": "50", "category": "祕密類別"})[0] == 200
    assert b.read("entries")[1]["data"]["entries"] == []
    assert b.do("rm", {"n": "1"}, confirmed=True)[0] == 409
    raw = open(bot.app.extras["vault"].for_user(77).path("money"), "rb").read()
    assert "祕密類別".encode() not in raw


def test_old_synox_without_app_hooks(make_bot):
    """假的舊 SYNOX（pack 沒有 app_read）：install() 照樣成功、指令都在。"""
    bot = make_bot("money")
    assert bot.app.registry.node("money.edit") is not None and bot.app.registry.node("money.add") is not None


def test_export(synox_bot, monkeypatch):
    bot = synox_bot("money")
    _now(monkeypatch)
    for memo in ("=1+1", "@x", "-5", 'a,"b"\nc'):
        bot.say(OWNER_ID, f"/money 1234 午餐 memo={memo}" if "\n" not in memo else "/money 1234 午餐")
    _m, client = opened(bot)
    mod = sys.modules["synox_pack_money"]
    calls = []
    real = mod.export_rows
    monkeypatch.setattr(mod, "export_rows", lambda d, f: calls.append(1) or real(d, f))
    status, _h, body = client.export("entries_csv", {"month": "2026-11"})
    assert status == 409
    status, headers, body = client.export("entries_csv", {"month": "2026-11"}, confirmed=True)
    assert status == 200 and dict(headers)["Content-Disposition"] == 'attachment; filename="money-2026-11.csv"'
    assert body.startswith("﻿".encode("utf-8"))
    rows = list(csv.reader(io.StringIO(body.decode("utf-8")[1:], newline="")))
    assert rows[0] == ["編號", "日期", "種類", "類別", "金額", "備註", "用什麼付"]
    assert len(rows) - 1 == len(client.read("entries", month="2026-11")[1]["data"]["entries"])
    assert rows[1][4] == "1234" and {r[5] for r in rows[1:4]} == {"'=1+1", "'@x", "'-5"}
    assert client.export("entries_csv", {"month": "bad"}, confirmed=True)[0] == 400
    src = open(os.path.join(MONEY, "pack.py"), encoding="utf-8").read()
    assert "import csv" not in src and "﻿" not in src


def test_export_refused_on_server(synox_bot, monkeypatch):
    bot = synox_bot("money", mode="server")
    bot.make(77, BasicRoles.MEMBER, "a")
    _m, client = opened(bot, 77)
    mod = sys.modules["synox_pack_money"]
    calls = []
    monkeypatch.setattr(mod, "export_rows", lambda d, f: calls.append(1))
    status, _h, body = client.export("entries_csv", {}, confirmed=True)
    assert status == 403 and json.loads(body)["code"] == "export_encrypted_only" and calls == []
