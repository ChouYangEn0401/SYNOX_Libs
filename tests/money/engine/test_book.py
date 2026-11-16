"""帳本：匯出一個檔、在另一個資料夾（＝另一台電腦）匯入、開新帳本；換之前一定先封存；壞檔與太新的檔擋下來。"""
import sqlite3
from datetime import date

import pytest

from money_engine import db, paths
from money_engine.errors import Conflict, InvalidInput
from money_engine.services import book, ingest, transactions as tx

TODAY = date(2037, 10, 10)


def add(conn, amount, desc, day="2037-10-09"):
    ingest.run(conn, {"records": [{"type": "expense", "amount": amount, "desc": desc, "date": day}]})


def test_export_import_on_another_computer(tmp_path):
    conn = db.connect()
    add(conn, 260, "範例便當")
    out = book.export_to(conn, "out")                                 # 給資料夾：自己取名
    assert out["transactions"] == 1 and out["path"].endswith(".db")
    conn.close()
    with paths.use_home(tmp_path / "other-pc", today=TODAY, files_root=tmp_path):   # 換一台電腦
        other = db.connect()
        assert book.describe(other)["transactions"] == 0
        r = book.import_from(other, out["path"])
        assert r["transactions"] == 1 and "before-import" in r["archived"]
        assert tx.query(other)["items"][0]["desc"] == "範例便當"
        assert len(book.info(other)["archives"]) == 1                 # 原本那本（空的）封存了
        other.close()


def test_export_outside_the_files_folder_is_refused(tmp_path):
    conn = db.connect()
    with pytest.raises(InvalidInput):
        book.export_to(conn, tmp_path / "elsewhere" / "b.db")
    with pytest.raises(InvalidInput):
        book.import_from(conn, "../b.db")
    conn.close()


def test_new_book_archives_and_starts_clean():
    conn = db.connect()
    add(conn, 260, "範例便當")
    r = book.new_book(conn)
    assert r["transactions"] == 0 and "before-new" in r["archived"]
    archived = sqlite3.connect(r["archived"])
    assert archived.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1   # 舊的還在封存裡
    archived.close()
    assert db.connect().execute("SELECT COUNT(*) FROM categories").fetchone()[0] > 10  # 預設類別還在
    add(conn, 80, "早餐")                                              # 換完照樣能寫
    assert book.describe(conn)["transactions"] == 1


def test_bad_files_are_refused(home):
    junk = home / "junk.db"
    junk.write_bytes(b"not a database at all")
    with pytest.raises(InvalidInput):
        book.check_file(junk)
    other = home / "other.db"
    c = sqlite3.connect(str(other))
    c.execute("CREATE TABLE foo (x)")
    c.close()
    with pytest.raises(InvalidInput, match="少了"):
        book.check_file(other)
    newer = home / "newer.db"
    c = db.connect(newer)
    c.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")     # 不關：資料還在 -wal 裡也要讀得到
    with pytest.raises(Conflict):
        book.check_file(newer)


def test_export_new_check_import_info():
    """匯出 → 開新的 → 先檢查（不換）→ 真的匯入 → 兩個封存。"""
    conn = db.connect()
    add(conn, 260, "範例便當")
    out = book.export_to(conn, "b.db")
    assert out["transactions"] == 1
    assert book.new_book(conn)["transactions"] == 0
    checked = book.check_file(paths.confined("b.db"))
    assert checked["transactions"] == 1 and book.describe(conn)["transactions"] == 0   # 只檢查，沒換
    assert book.import_from(conn, "b.db")["transactions"] == 1
    assert len(book.info(conn)["archives"]) == 2
