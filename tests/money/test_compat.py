"""記帳 0.4.0 跟 0.3.1 相容（W6）：舊指令名照樣能打（month、budget、cap、cats、cat_rm、template）、CSV 匯出匯入、
工具頁的小面板。從 0.3.1 的 tests/test_money.py、tests/test_money_app.py 的情境改寫成對新引擎的測試。

記帳主線（/money、/money_list、/money_undo…）是 W3 的：這裡用引擎直接記帳；靠主線指令的情境在主線還沒做好時跳過。
「今天」是 2030-01-15（conftest 的 TODAY），帳本每期從 1 號開始（起始檔），所以「這一期」＝2030-01。
資料一律是假的（「範例便當店」「現金」「120」）。
"""

import csv
import importlib
import importlib.util
import io
import os

import pytest

from synox.packs import kit

from .conftest import OTHER, OWNER, PACKAGE, U


# ── 共用的小工具（test_legacy.py 也用）─────────────────────────────────────────────────

def eng(name):
    """引擎的模組（跟 load_money() 載進來的是同一份）。"""
    return importlib.import_module(f"{PACKAGE}.engine.{name}")


def record(bot, uid, amount, category=None, *, kind="expense", day="2030-01-10", desc="", account="現金", to=None):
    """直接用引擎記一筆（金額是最小單位的整數）。"""
    tx, refdata, db = eng("services.transactions"), eng("services.refdata"), eng("db")
    with bot.module.session.open_book(bot.pack, U(uid)) as book:
        conn = book.conn
        cat = refdata.find_category(conn, category) if category else None
        acc = refdata.resolve_account(conn, account)
        to_id = refdata.resolve_account(conn, to)["id"] if to else None
        with db.write(conn, actor="test", source="test") as w:
            return tx.create(w, kind=kind, amount=amount, date=day, account_id=acc["id"],
                             category_id=cat["id"] if cat else None, to_account_id=to_id, description=desc)


def query(bot, uid, sql, args=()):
    with bot.module.session.open_book(bot.pack, U(uid)) as book:
        return [dict(r) for r in book.conn.execute(sql, args).fetchall()]


def view(bot, uid, month=""):
    with bot.module.session.open_book(bot.pack, U(uid)) as book:
        return bot.module.compat.month_view(book.conn, month)


def setting(bot, uid, key):
    with bot.module.session.open_book(bot.pack, U(uid)) as book:
        return eng("services.settings").get(book.conn, key)


def summary(bot, uid):
    return bot.pack.web_routes["summary"](U(uid))


def ledger_ready(bot):
    """記帳主線（W3）做好了沒（還是空殼就跳過靠它的情境）。"""
    return not hasattr(bot.node("add").fn, "todo") and not any(
        getattr(c.run, "todo", None) for c in bot.module.ledger.COMMANDS if c.name in ("add", "list"))


# ── month／budget（0.3.1：預算提醒、月份、收入與轉帳不算花費）──────────────────────────────

def test_month_shows_categories_budget_and_overrun(make_money):
    bot = make_money()
    assert "預算" in bot.command("budget", OWNER, "100")
    record(bot, OWNER, 60, "午餐")
    record(bot, OWNER, 50, "晚餐", desc="和朋友")
    out = bot.command("month", OWNER)
    assert "2030-01 支出共 110 元" in out and "午餐：60 元" in out and "晚餐：50 元" in out
    assert "生活費預算 100 元" in out and "超過 10 元" in out


def test_budget_values(make_money):
    bot = make_money()
    for bad in ("abc", "nan", "inf", "-5", "1.5"):
        assert bot.command("budget", OWNER, bad).startswith("❌"), bad
    assert view(bot, OWNER)["budget"] is None
    assert "設為 9,000 元" in bot.command("budget", OWNER, "9000")
    assert view(bot, OWNER)["budget"] == 9000
    assert "預算拿掉了" in bot.command("budget", OWNER, "0")
    assert view(bot, OWNER)["budget"] is None
    assert "還沒設預算" in bot.command("month", OWNER)


def test_backdated_entries_land_in_their_month(make_money):
    bot = make_money()
    record(bot, OWNER, 30, "早餐", day="2030-01-14")
    record(bot, OWNER, 40, "晚餐", day="2029-12-31")
    assert "支出共 30 元" in bot.command("month", OWNER)
    assert "2029-12 支出共 40 元" in bot.command("month", OWNER, "2029-12")
    assert "要像 2030-01" in bot.command("month", OWNER, "2030-13")


def test_income_and_transfers_do_not_count_as_spending(make_money):
    bot = make_money()
    bot.command("budget", OWNER, "1000")
    record(bot, OWNER, 300, "午餐")
    record(bot, OWNER, 50000, "薪資", kind="income", account="銀行帳戶")
    record(bot, OWNER, 5000, "帳戶互轉", kind="transfer", account="銀行帳戶", to="現金")
    out = bot.command("month", OWNER)
    assert "支出共 300 元" in out and "💵 收入 共 50,000 元：薪資 50,000" in out
    assert "🔁 轉帳（不算花費） 共 5,000 元" in out and "還剩 700 元" in out
    s = summary(bot, OWNER)
    assert s["total"] == 300 and s["income"] == 50000 and s["transfer"] == 5000 and s["left"] == 700
    assert [c["category"] for c in s["by_category"]] == ["午餐"] and s["unit"] == "元"


def test_refund_is_negative_spending(make_money):
    bot = make_money()
    record(bot, OWNER, 300, "衣服")
    record(bot, OWNER, -100, "衣服", desc="退貨")
    assert "支出共 200 元" in bot.command("month", OWNER)


# ── cap（單類上限：只提醒、不擋）─────────────────────────────────────────────────────

def test_category_cap_warns_but_does_not_block(make_money):
    bot = make_money()
    out = bot.command("cap", OWNER, "飲料 100")
    assert "每期上限設為 100 元" in out and "還剩 100 元" in out
    record(bot, OWNER, 60, "飲料")
    record(bot, OWNER, 70, "飲料")
    assert "⚠️ 飲料 超過這一期的上限 100 元（超過 30 元）" in bot.command("month", OWNER)
    assert summary(bot, OWNER)["caps"] == [{"category": "飲料", "cap": 100}]
    assert "每期上限：100 元" in bot.command("cats", OWNER, "飲料")
    assert "上限只用在支出類別" in bot.command("cap", OWNER, "薪資 100")
    assert "上限拿掉了" in bot.command("cap", OWNER, "飲料 0")
    assert "上限" not in bot.command("month", OWNER)


def test_cap_on_a_new_name_makes_an_expense_category(make_money):
    bot = make_money()
    assert "每期上限設為 50 元" in bot.command("cap", OWNER, "寵物 50")
    out = bot.command("cats", OWNER, "寵物")
    assert "種類：支出" in out and "大類：其他" in out and "每期上限：50 元" in out


# ── cats／cat_rm ─────────────────────────────────────────────────────────────────

def test_cats_list_detail_and_disable(make_money):
    bot = make_money()
    out = bot.command("cats", OWNER)
    assert "你的類別（" in out and "食：早餐" in out and "薪資（收入）" in out and "繳卡費（轉帳）" in out
    assert "咖啡" in bot.command("cats", OWNER, "飲料")
    assert "沒有「不存在」這個類別" in bot.command("cats", OWNER, "不存在")
    record(bot, OWNER, 60, "飲料")
    assert "停用了類別「飲料」" in bot.command("cat_rm", OWNER, "飲料")
    assert "沒有「飲料」" in bot.command("cat_rm", OWNER, "飲料")
    assert "飲料" not in bot.command("cats", OWNER)
    assert "飲料：60 元" in bot.command("month", OWNER)                          # 已經記的帳不動
    assert "不能停用" in bot.command("cat_rm", OWNER, "午餐")                     # 系統會用到的類別


# ── template（起始類別與選配功能；載兩次不會多）──────────────────────────────────────────

def test_template_lists_loads_and_twice_adds_nothing(make_money):
    bot = make_money()
    out = bot.command("template", OWNER)
    assert "基本" in out and "帳單" in out and "存錢與投資" in out and "捐贈" in out and "收入" in out
    assert "新增 0 個類別" in bot.command("template", OWNER, "基本")              # 新帳本本來就有
    bot.command("cat_rm", OWNER, "交際")
    assert "新增 1 個類別（交際）" in bot.command("template", OWNER, "basic")       # 停用的打開
    before = len(query(bot, OWNER, "SELECT id FROM categories WHERE active = 1"))
    out = bot.command("template", OWNER, "捐贈")
    assert "捐款" in out and "打開了：捐贈" in out
    assert "新增 0 個類別" in bot.command("template", OWNER, "giving")
    assert len(query(bot, OWNER, "SELECT id FROM categories WHERE active = 1")) == before + 1
    assert "沒有「不存在」這個範本" in bot.command("template", OWNER, "不存在")
    assert "卡分期" in bot.command("template", OWNER, "還債")                       # 選配功能的名字也收


def test_template_does_not_overwrite_user_edits(make_money):
    bot = make_money()
    bot.command("cap", OWNER, "午餐 150")
    bot.command("template", OWNER, "基本")
    lunch = query(bot, OWNER, "SELECT default_budget FROM categories WHERE name = '午餐'")[0]
    assert lunch["default_budget"] == 150


# ── 每個人只看得到自己的 ──────────────────────────────────────────────────────────────

def test_private_data_stays_with_its_owner(make_money):
    bot = make_money(per_user=True)
    bot.command("cap", OWNER, "祕密類別A 999")
    record(bot, OWNER, 50, "祕密類別A", desc="祕密備註A")
    record(bot, OWNER, 12345, "薪資", kind="income")
    bot.command("budget", OWNER, "4321")
    seen = [bot.command(name, OTHER, text) for name, text in
            (("month", ""), ("cats", ""), ("cats", "祕密類別A"), ("template", ""), ("cap", "飲料 10"))]
    seen.append(str(summary(bot, OTHER)))
    blob = "\n".join(seen)
    assert "祕密" not in blob.replace("「祕密類別A」", "") and "12,345" not in blob and "12345" not in blob
    assert "4,321" not in blob and "999" not in blob


# ── CSV：匯出（畫面）、匯入（指令）──────────────────────────────────────────────────

def export(bot, uid, params=None):
    return bot.pack.app_exports["entries_csv"](U(uid), dict(params or {}))


def rows_of(download):
    text = download.data.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:], newline="")))


def test_export_filters_and_formula_guard(make_money):
    bot = make_money()
    record(bot, OWNER, 120, "午餐", desc="範例便當店")
    record(bot, OWNER, 80, "飲料", desc="=1+1", day="2030-01-11")
    record(bot, OWNER, 40, "飲料", desc="拿鐵", day="2029-12-20")
    record(bot, OWNER, 3000, "薪資", kind="income", account="銀行帳戶")
    d = export(bot, OWNER, {"month": "2030-01"})
    assert d.filename == "money-2030-01.csv" and d.content_type.startswith("text/csv")
    rows = rows_of(d)
    assert rows[0] == ["日期", "時間", "類型", "金額", "類別", "帳戶", "轉入帳戶", "說明", "商家", "備註", "編號"]
    assert [r[3] for r in rows[1:]] == ["120", "3000", "80"] or sorted(r[3] for r in rows[1:]) == ["120", "3000", "80"]
    assert "'=1+1" in {r[7] for r in rows[1:]}
    assert len(rows_of(export(bot, OWNER))) - 1 == 4
    assert [r[4] for r in rows_of(export(bot, OWNER, {"category": "飲料"}))[1:]] == ["飲料", "飲料"]
    assert [r[2] for r in rows_of(export(bot, OWNER, {"kind": "收入"}))[1:]] == ["收入"]
    assert [r[7] for r in rows_of(export(bot, OWNER, {"keyword": "便當"}))[1:]] == ["範例便當店"]
    for bad in ({"month": "bad"}, {"kind": "x"}):
        with pytest.raises(kit.AppError) as no:
            export(bot, OWNER, bad)
        assert no.value.status == 400


def test_csv_round_trip_into_an_empty_book(make_money):
    bot = make_money(per_user=True)
    record(bot, OWNER, 120, "午餐", desc="範例便當店")
    record(bot, OWNER, -20, "午餐", desc="退一個菜")
    record(bot, OWNER, 3000, "薪資", kind="income", account="銀行帳戶")
    record(bot, OWNER, 500, "帳戶互轉", kind="transfer", account="銀行帳戶", to="現金")
    record(bot, OWNER, 77, "早餐", day="2029-12-30")
    data = export(bot, OWNER).data
    path = bot.pack.path_of(OTHER, "move.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    out = bot.command("import_csv", OTHER, "move.csv dry_run=yes")
    assert "沒有寫進去" in out and view(bot, OTHER)["expense"] == 0
    out = bot.command("import_csv", OTHER, "move.csv")
    assert "匯入好了：5 列，新記 5 筆" in out
    for month in ("2030-01", "2029-12"):
        a, b = view(bot, OWNER, month), view(bot, OTHER, month)
        assert (a["expense"], a["income"], a["transfer"]) == (b["expense"], b["income"], b["transfer"]), month
        assert a["by_category"] == b["by_category"], month
    again = bot.command("import_csv", OTHER, "move.csv")
    assert "新記 0 筆" in again and "已經記過 5 筆" in again
    assert view(bot, OTHER)["expense"] == 100


def test_import_csv_stays_in_own_folder_and_errors_have_no_paths(make_money):
    bot = make_money()
    out = bot.command("import_csv", OWNER, "nope.csv")
    assert out.startswith("❌") and "nope.csv" in out
    home = bot.pack.path_of(OWNER)
    assert home not in out and os.sep + "files" not in out
    out = bot.command("import_csv", OWNER, "../../別人的.csv")
    assert out.startswith("❌") and "自己的檔案資料夾" in out


def test_import_csv_accepts_chinese_headers_and_needs_known_accounts(make_money):
    bot = make_money()
    path = bot.pack.path_of(OWNER, "other-app.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("日期,類型,金額,類別,帳戶,說明\r\n2030-01-05,支出,90,午餐,現金,範例便當店\r\n")
    assert "新記 1 筆" in bot.command("import_csv", OWNER, "other-app.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("日期,類型,金額,帳戶\r\n2030-01-06,支出,90,範例不存在的帳戶\r\n")
    out = bot.command("import_csv", OWNER, "other-app.csv")
    assert out.startswith("❌") and "找不到帳戶" in out and "/money_account" in out


# ── 工具頁的小面板 ───────────────────────────────────────────────────────────────

def test_panel_summary_shape(make_money):
    bot = make_money()
    s = summary(bot, OWNER)
    assert set(s) == {"month", "total", "income", "transfer", "budget", "spent", "left", "by_category", "caps", "unit",
                      "decimals"}
    assert s["month"] == "2030-01" and s["total"] == 0 and s["budget"] is None and s["left"] is None


def test_panel_html_reads_the_summary_route():
    from .conftest import MONEY
    with open(os.path.join(MONEY, "panel.html"), encoding="utf-8") as f:
        html = f.read()
    assert "/api/packs/money/summary" in html and "innerHTML" not in html and "http" not in html.replace("/api/", "")
    for key in ("d.total", "d.budget", "d.spent", "d.left", "d.by_category", "d.unit"):
        assert key in html, key


def test_two_decimal_books_show_cents(make_money):
    bot = make_money()
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        with eng("db").write(book.conn, actor="test", source="test") as w:
            eng("services.settings").put(w, "currency.decimals", 2)
            eng("services.settings").put(w, "currency.code", "USD")
    out = bot.command("budget", OWNER, "99.5")
    assert "99.50 USD" in out
    assert view(bot, OWNER)["budget"] == 9950
    assert "最多 2 位小數" in bot.command("budget", OWNER, "1.234")
    record(bot, OWNER, 1250, "午餐")
    assert "12.50 USD" in bot.command("month", OWNER)
    s = summary(bot, OWNER)
    assert s["total"] == 12.5 and s["unit"] == "USD" and s["decimals"] == 2


# ── 0.3.1 的打字方式（記帳主線做好之後才測得到）──────────────────────────────────────────

def test_old_typing_with_the_ledger(make_money):
    bot = make_money()
    if not ledger_ready(bot):
        pytest.skip("記帳主線（W3）還是空殼")
    bot.command("budget", OWNER, "100")
    assert bot.command("add", OWNER, "60 午餐").startswith(("💰", "✅", "📝")) or "60" in bot.command("list", OWNER)
    bot.command("add", OWNER, "50 晚餐 和朋友")
    assert "支出共 110 元" in bot.command("month", OWNER)
    assert "晚餐" in bot.command("list", OWNER, "1")
    out = bot.command("list", OWNER, "keyword=朋友")
    assert "晚餐" in out and "午餐" not in out
    assert bot.command("add", OWNER, "abc 午餐").startswith("❌")


# ── 真的 SYNOX：匯出走 SYNOX 的工具服務（個人版先問、伺服器版不給）──────────────────────────────

def _synox_loads_packages():
    return importlib.util.find_spec("synox.packs.pkgload") is not None


def _opened(bot, who):
    from synox.web import apps
    from synox.web.apps.testing import AppClient, FakeListen
    manager = apps.of(bot.app)
    if not isinstance(manager.listen, FakeListen):
        manager.listen = FakeListen()
    if "money" not in manager.running():
        manager.start("money", by=OWNER, via="web")
    client = AppClient(manager, "money")
    client.enter(client.open_from_console(who))
    return client


@pytest.mark.skipif(not _synox_loads_packages(), reason="這份 SYNOX 還沒有多檔工具（python.package）")
def test_real_synox_export_asks_first_and_is_refused_on_server(synox_bot):
    from isdtelekit.users.roles import BasicRoles
    bot = synox_bot("money")
    bot.say(OWNER, "/money_budget 500")
    client = _opened(bot, OWNER)
    status, _h, _body = client.export("entries_csv", {"month": "2030-01"})
    assert status == 409                                                          # 個人版：先提醒沒加密
    status, headers, body = client.export("entries_csv", {"month": "2030-01"}, confirmed=True)
    assert status == 200 and dict(headers)["Content-Disposition"] == 'attachment; filename="money-2030-01.csv"'
    assert body.startswith("﻿".encode("utf-8"))
    assert client.export("entries_csv", {"month": "bad"}, confirmed=True)[0] == 400
    server = synox_bot("money", mode="server")
    server.make(77, BasicRoles.MEMBER, "a")
    status, _h, body = _opened(server, 77).export("entries_csv", {}, confirmed=True)
    assert status == 403
