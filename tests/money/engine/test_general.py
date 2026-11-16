"""通用版：帳本資料夾自己的起始資料是預設、內建通用版起始資料、選配功能、午餐提醒的星期、CSV 搬家、撤銷最近一批、
匯入匯出關在檔案資料夾裡。"""
from datetime import date

import pytest

from money_engine import db, paths
from money_engine.errors import Conflict, InvalidInput, NotFound
from money_engine.services import (allowance, batches, csvio, envelopes, health, ingest, piggy, quick, refdata, report,
                                   settings, starter, stats, wallet)

TODAY = date(2037, 10, 10)


def add(conn, **rec):
    rec.setdefault("type", "expense")
    rec.setdefault("date", TODAY.isoformat())
    return ingest.run(conn, {"records": [rec]}, command="txn add")


def test_folder_seed_is_the_default(conn):
    """不給 seed：用帳本資料夾裡的 categories.json（測試用的假資料：六個用途都開）。"""
    active = [r["id"] for r in conn.execute("SELECT id FROM buckets WHERE active = 1 ORDER BY sort")]
    assert active == ["giving", "fixed", "saving", "living", "invest", "debt"]
    assert refdata.find_category(conn, "定期捐款") is not None
    assert settings.get(conn, "period_start_day") == 5
    assert settings.value(conn, "quick.lunch_weekdays") == "0,1,2,3,4"


def test_generic_seed(generic_home):
    conn = db.connect()
    groups = [r["name"] for r in conn.execute("SELECT name FROM category_groups WHERE bucket_id IS NOT NULL ORDER BY sort")]
    assert groups[:8] == ["食", "衣", "住", "行", "育", "樂", "醫療", "其他"]
    active = {r["id"] for r in conn.execute("SELECT id FROM buckets WHERE active = 1")}
    assert active == {"fixed", "saving", "living"}                          # 沒有捐贈、投資、操盤、還債
    assert [r["name"] for r in conn.execute("SELECT name FROM accounts ORDER BY sort")] == ["現金", "銀行帳戶"]
    assert refdata.find_category(conn, "定期捐款") is None
    # 程式要找得到的系統類別：轉帳、薪資、其他收入、三餐、點心宵夜、大額採買、住的那幾個
    for name in ("借出", "收回借款", "繳卡費", "帳戶互轉", "薪資", "其他收入", "早餐", "午餐", "晚餐", "點心宵夜", "大額採買",
                 "房租", "電費", "水費", "瓦斯", "手機網路"):
        cat = refdata.find_category(conn, name)
        assert cat is not None and cat["name"] == name, name
        assert refdata.is_system_category(cat), name
    assert settings.get(conn, "period_start_day") == 1
    assert settings.get(conn, "import.invest_account") is None               # 匯入用的設定已經拿掉
    conn.close()


def test_generic_reports_do_not_break(generic_home):
    conn = db.connect()
    assert add(conn, amount=120, desc="便當", time="12:10")["results"][0]["status"] == "created"
    s = stats.period_summary(conn, None)
    key = s["period"]["key"]
    w = wallet.summary(conn, key)
    health.checks(conn, key, wallet_summary=w)
    report.build(conn, "month", None)
    report.build(conn, "week", None)
    allowance.check(conn, None)
    envelopes.period_rows(conn, None)
    quick.lunch(conn)
    piggy.piggy(conn, None, None)
    assert refdata.tree(conn) and refdata.accounts(conn)
    assert s["living"]["spent"] == 120
    conn.close()


def test_setup_init_and_enable(generic_home):
    conn, out = starter.init("generic")
    assert out["seed"] == "generic"
    feats = {f["name"]: f["enabled"] for f in out["features"]}
    assert feats == {"giving": False, "invest": False, "debt": False, "payroll": False, "trading": False}
    with db.write(conn) as w:
        on = starter.enable(w, "invest")
    assert on["bucket_turned_on"] and on["added_categories"] == ["股票", "基金"]
    with db.write(conn) as w:
        again = starter.enable(w, "invest")
    assert not again["changed"]
    with db.write(conn) as w:
        starter.enable(w, "debt")
    assert refdata.find_category(conn, "信貸還款")["transfer_purpose"] == "debt"
    with pytest.raises(NotFound):
        with db.write(conn) as w:
            starter.enable(w, "nope")
    add(conn, amount=50, desc="咖啡")
    conn.close()
    with pytest.raises(Conflict):                                           # 用過了：seed 不能再換
        starter.init("generic")


def test_enable_on_a_used_book_adds_no_groups(conn):
    """已經有自己一套類別的帳本：打開選配功能不加大類；只有資金操盤那一格本來不開。"""
    with db.write(conn) as w:
        outs = {name: starter.enable(w, name) for name in ("giving", "invest", "debt", "payroll", "trading")}
    assert conn.execute("SELECT active FROM buckets WHERE id = 'trading'").fetchone()[0] == 1
    assert outs["trading"]["bucket_turned_on"]
    assert all(not o["added_groups"] for o in outs.values())
    for name in ("invest", "debt", "payroll", "trading"):
        assert outs[name]["added_categories"] == [], name
    # 捐贈：測試資料的捐贈大類裡沒有叫「捐款」的細項，所以只補那一個進原本的大類（不另開大類）
    assert outs["giving"]["added_categories"] == ["捐款"]
    assert refdata.find_category(conn, "捐款")["group_name"] == "捐贈"


def test_lunch_weekdays_setting(conn):
    assert quick.lunch(conn, day=TODAY)["weekday"] is False                  # 週六
    with db.write(conn) as w:
        settings.put(w, "quick.lunch_weekdays", settings.coerce(conn, "quick.lunch_weekdays", "5,6"))
    assert quick.lunch(conn, day=TODAY)["weekday"] is True
    with pytest.raises(InvalidInput):
        settings.coerce(conn, "quick.lunch_weekdays", "8")


def test_csv_round_trip(generic_home, tmp_path):
    conn = db.connect()
    add(conn, amount=120, desc="便當", date="2037-10-03", time="12:10")
    add(conn, type="income", amount=3000, category="薪資", account="銀行帳戶", date="2037-10-05")
    ingest.run(conn, {"records": [{"type": "transfer", "amount": 500, "from": "銀行帳戶", "to": "現金", "date": "2037-10-06",
                                   "desc": ""}]})
    add(conn, amount=99, desc="上個月的", date="2037-09-30")
    out = csvio.export_month(conn, "2037-10", "x")
    assert out["rows"] == 3 and out["totals"]["income"] == 3000
    exported = generic_home / "x" / "ledger-2037-10.csv"
    text = exported.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == "date,time,type,amount,category,account,to_account,description,merchant,note,id"
    with pytest.raises(InvalidInput):
        csvio.export_month(conn, "2037-13", "y")
    conn.close()

    with paths.use_home(tmp_path / "friend", today=TODAY, files_root=tmp_path):     # 另一個人的帳本
        friend = db.connect()
        first = csvio.import_csv(friend, exported, actor="cli", source="cli", dry_run=False)
        assert first["summary"]["new"] == 3 and len(first["batch_ids"]) == 1
        again = csvio.import_csv(friend, exported, actor="cli", source="cli", dry_run=False)
        assert again["summary"]["replayed"] == 3 and again["batch_ids"] == []
        zh = tmp_path / "zh.csv"
        zh.write_text("日期,金額,類別,說明,帳戶\n2037/10/07,80,早餐,蛋餅,現金\n2037-10-08,-30,,退款,現金\n", encoding="utf-8")
        out = csvio.import_csv(friend, zh, actor="cli", source="cli", dry_run=False)
        assert out["summary"]["new"] == 2
        bad = tmp_path / "bad.csv"
        bad.write_text("date,amount,account\n2037-10-07,80,沒有這個帳戶\n", encoding="utf-8")
        with pytest.raises(NotFound, match="第 2 列"):
            csvio.import_csv(friend, bad, actor="cli", source="cli", dry_run=False)
        friend.close()


def _undo(conn):
    last = batches.last_undoable(conn)
    with db.write(conn, command="batch undo") as w:
        return batches.revert(w, last["id"])


def test_batch_undo(conn):
    with pytest.raises(NotFound):
        _undo(conn)
    add(conn, amount=10, desc="a")
    second = add(conn, amount=20, desc="b")
    out = _undo(conn)
    assert out["reverted"] == second["batch_id"]
    out2 = _undo(conn)                                                       # 再一次：撤的是更早那一批（撤銷本身不算）
    assert out2["reverted"] != second["batch_id"]
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE status = 'active'").fetchone()[0] == 0


def test_files_root_confines_import_and_export(home, tmp_path):
    conn = db.connect()
    add(conn, amount=10, desc="a")
    out = csvio.export_month(conn, "2037-10")                               # 不給 out：帳本資料夾的 exports/
    assert out["path"].endswith("ledger-2037-10.csv") and "exports" in out["path"]
    conn.close()
    root = tmp_path / "mine"
    outside = tmp_path / "other.csv"
    outside.write_text("date,amount\n2037-10-01,5\n", encoding="utf-8")
    with paths.use_home(home, today=TODAY, files_root=root):
        conn = db.connect()
        ok = csvio.export_month(conn, "2037-10", "back.csv")
        assert ok["path"] == str((root / "back.csv").resolve())
        for call in (lambda: csvio.import_csv(conn, outside, actor="cli", source="cli", dry_run=False),
                     lambda: csvio.import_csv(conn, "../other.csv", actor="cli", source="cli", dry_run=False),
                     lambda: csvio.export_month(conn, "2037-10", outside)):
            with pytest.raises(InvalidInput):
                call()
        assert csvio.import_csv(conn, "back.csv", actor="cli", source="cli", dry_run=False)["rows"] == 1
        conn.close()
