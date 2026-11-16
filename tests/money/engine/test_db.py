"""資料庫層：migration、寫入（batch／audit／dry-run）、版本比程式新、併發、備份。"""
import sqlite3
import threading
from datetime import date

import pytest

from money_engine import db, paths
from money_engine.errors import Conflict


def test_connect_creates_schema_and_pragmas(conn):
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"transactions", "batches", "audit_log", "periods", "categories", "accounts", "period_plans"} <= names
    assert db.write_seq(conn) == 0


def test_migrations_are_idempotent_across_connections():
    db.connect().close()
    conn = db.connect()
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    conn.close()


def _touch(w):
    w.conn.execute("INSERT INTO settings (key, value) VALUES ('x', '1')")
    w.audit("create", "settings", "x", None, {"key": "x", "value": "1"})


def test_write_commits_batch_audit_and_bumps_seq(conn):
    with db.write(conn, actor="chat:1", source="chat", command="test") as w:
        _touch(w)
        bid = w.batch_id
    assert db.write_seq(conn) == 1
    batch = conn.execute("SELECT * FROM batches WHERE id=?", (bid,)).fetchone()
    assert batch["actor"] == "chat:1" and batch["command"] == "test"
    audit = conn.execute("SELECT * FROM audit_log WHERE batch_id=?", (bid,)).fetchall()
    assert len(audit) == 1 and audit[0]["entity"] == "settings"


def test_dry_run_and_empty_batches_leave_nothing(conn):
    with db.write(conn, dry_run=True) as w:
        _touch(w)
    with db.write(conn):
        pass
    assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM settings WHERE key='x'").fetchone()[0] == 0
    assert db.write_seq(conn) == 0


def test_failure_rolls_back(conn):
    with pytest.raises(RuntimeError):
        with db.write(conn) as w:
            _touch(w)
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM settings WHERE key='x'").fetchone()[0] == 0


def test_newer_database_refuses_writes():
    conn = db.connect()
    conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
    conn.close()
    newer = db.connect()
    assert newer.newer_than_code
    with pytest.raises(Conflict):
        with db.write(newer):
            pass
    newer.close()


def test_writer_can_write_while_another_connection_holds_a_read():
    """另一條連線正在讀（交易開著），這條照樣寫得進去（WAL）。"""
    reader = db.connect()
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM transactions").fetchone()
    result = {}
    ctx = paths.current()

    def writer():
        with paths.use_home(ctx.home, today=ctx.today):        # 新執行緒的 context 是空的：帶同一本帳過去
            conn = db.connect()
            with db.write(conn) as w:
                _touch(w)
            result["seq"] = db.write_seq(conn)
            conn.close()

    t = threading.Thread(target=writer)
    t.start()
    t.join(10)
    reader.execute("COMMIT")
    reader.close()
    assert result.get("seq") == 1


def test_daily_backup_once_per_day_and_pruned(home, conn):
    with db.write(conn) as w:
        _touch(w)
    folder = db.db_path().parent / "backups"
    files = list(folder.glob("ledger-*.db"))
    assert [f.name for f in files] == ["ledger-20371010.db"]
    with db.write(conn) as w:                       # 同一天第二次不再備份
        w.conn.execute("UPDATE settings SET value='2' WHERE key='x'")
        w.audit("update", "settings", "x", None, None)
    assert len(list(folder.glob("ledger-*.db"))) == 1
    backup = sqlite3.connect(str(files[0]))
    assert backup.execute("SELECT COUNT(*) FROM settings WHERE key='x'").fetchone()[0] == 0   # 備份的是寫入前的狀態
    backup.close()
    (folder / "ledger-20370801.db").write_bytes(b"")
    with paths.use_home(home, today=date(2037, 10, 11)):
        db.daily_backup(conn)
    names = sorted(f.name for f in folder.glob("ledger-*.db"))
    assert names == ["ledger-20371010.db", "ledger-20371011.db"]
