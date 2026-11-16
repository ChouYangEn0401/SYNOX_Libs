"""資料庫：連線、migration、寫入（batch＋audit＋dry-run）、每日備份。

- 每條連線：WAL、busy_timeout=5000、foreign_keys=ON；網站拿著讀取時 CLI 也寫得進去。
- MIGRATIONS 只加不改；版本在 PRAGMA user_version。資料庫比程式新（舊版網站還開著）就拒絕寫入。
- 所有寫入都經 `write()`：BEGIN IMMEDIATE → 建一個 batch → 呼叫端改資料並記 audit → COMMIT（dry-run 改成 ROLLBACK，
  所以 dry-run 的結果跟真的寫入完全一樣，只是不留下來）。沒有任何改動的 batch 不留。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

from . import clock, ids, paths
from .errors import Conflict

DB_NAME = "ledger.db"
BACKUP_KEEP_DAYS = 14                      # 每日備份留 14 天（一天一份；備份沒有加密）


class Connection(sqlite3.Connection):
    """多一個旗標：資料庫版本比程式新時設 True，write() 會拒絕。"""
    newer_than_code = False


def db_path() -> Path:
    return paths.data_dir() / DB_NAME


def connect(path: Path | str | None = None, *, migrate: bool = True, seed: Any = None) -> Connection:
    """開這本帳（沒給 path＝目前 context 的帳本資料夾；沒設定就丟 NoHome）。新帳本用 seed 建起始資料
    （None＝帳本資料夾裡的 categories.json，沒有就用內建的通用版；也可以是 "generic"、一個 JSON 檔的路徑或一個 dict）。"""
    conn = sqlite3.connect(str(path or db_path()), timeout=5.0, isolation_level=None,
                           check_same_thread=False, factory=Connection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    if migrate:
        apply_migrations(conn, seed)
    return conn


# ---------------------------------------------------------------- schema 與 migration

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def _schema_v1(conn: sqlite3.Connection, seed_source: Any = None) -> None:
    """1：起始 schema（schema.sql）＋起始資料（用途、大類、細項、帳戶、設定；seed.py）。"""
    for statement in _split_sql(SCHEMA_FILE.read_text(encoding="utf-8")):
        conn.execute(statement)
    from . import seed
    seed.install(conn, seed_source)


MIGRATIONS: list[str | Callable[..., None]] = [
    _schema_v1,         # 1：起始 schema＋起始資料
]
# 規矩：只加不改。新的改動接在後面（一個 SQL 字串，或一個收 conn 的函式）；版本存在 PRAGMA user_version。


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def apply_migrations(conn: Connection, seed_source: Any = None) -> None:
    target = len(MIGRATIONS)
    current = schema_version(conn)
    if current > target:
        conn.newer_than_code = True
        return
    if current == target:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = schema_version(conn)          # 別的程序可能剛做完
        for number in range(current + 1, target + 1):
            step = MIGRATIONS[number - 1]
            if isinstance(step, str):
                for statement in _split_sql(step):
                    conn.execute(statement)
            elif number == 1:
                step(conn, seed_source)
            else:
                step(conn)
            conn.execute(f"PRAGMA user_version = {number}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _split_sql(script: str) -> list[str]:
    """executescript 會自己 COMMIT，所以拆成一句一句在交易裡跑。schema 裡沒有含分號的字串。"""
    return [s.strip() for s in script.split(";") if s.strip()]


# ---------------------------------------------------------------- 寫入

@dataclass
class Writer:
    conn: Connection
    batch_id: str
    actor: str
    source: str
    now: str
    dry_run: bool
    changes: int = 0
    notes: list[str] = field(default_factory=list)

    def audit(self, action: str, entity: str, entity_id: Any, before: dict | None, after: dict | None) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (at, batch_id, actor, action, entity, entity_id, before, after)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (self.now, self.batch_id, self.actor, action, entity, str(entity_id),
             _dump(before), _dump(after)))
        self.changes += 1


def _dump(value: dict | None) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


@contextmanager
def write(conn: Connection, *, actor: str = "cli", source: str = "cli", command: str = "",
          note: str = "", dry_run: bool = False) -> Iterator[Writer]:
    if conn.newer_than_code:
        raise Conflict("資料庫的版本比這個程式新，為了不弄壞資料先不寫入。",
                       hint="更新記帳工具到最新版再試。")
    if not dry_run:
        daily_backup(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        writer = Writer(conn, ids.batch_id(), actor, source, clock.now_iso(), dry_run)
        conn.execute("INSERT INTO batches (id, at, actor, source, command, note) VALUES (?,?,?,?,?,?)",
                     (writer.batch_id, writer.now, actor, source, command, note))
        yield writer
        if dry_run or writer.changes == 0:
            conn.execute("ROLLBACK")
        else:
            conn.execute("UPDATE meta SET value = value + 1 WHERE key = 'write_seq'")
            conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def write_seq(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT value FROM meta WHERE key = 'write_seq'").fetchone()[0]


def row_dict(row: sqlite3.Row | None) -> dict | None:
    return None if row is None else {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------- 備份

def main_db_file(conn: sqlite3.Connection) -> Path | None:
    for row in conn.execute("PRAGMA database_list"):
        if row["name"] == "main" and row["file"]:
            return Path(row["file"])
    return None


def daily_backup(conn: sqlite3.Connection) -> Path | None:
    """每天第一次寫入前備份一份到 <資料夾>/backups/ledger-YYYYMMDD.db，留 BACKUP_KEEP_DAYS 天。

    用 Connection.backup()（不是複製檔案）：WAL 模式下直接複製 .db 會漏掉還在 -wal 裡的資料。
    """
    db_file = main_db_file(conn)
    if db_file is None:
        return None
    folder = db_file.parent / "backups"
    today = clock.today()
    target = folder / f"ledger-{today:%Y%m%d}.db"
    if target.exists():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    dest = sqlite3.connect(str(partial))
    try:
        conn.backup(dest)
    finally:
        dest.close()
    partial.replace(target)
    _prune_backups(folder, today)
    return target


def _prune_backups(folder: Path, today: date) -> None:
    cutoff = today - timedelta(days=BACKUP_KEEP_DAYS)
    for old in folder.glob("ledger-*.db"):
        stamp = old.stem.removeprefix("ledger-")
        try:
            day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
        except ValueError:
            continue
        if day < cutoff:
            old.unlink(missing_ok=True)
