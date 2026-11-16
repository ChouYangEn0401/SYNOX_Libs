"""帳本：整本匯出成一個檔、從檔案匯入、開一本新的。

- 一本帳＝一個 SQLite 檔（帳本資料夾裡的 ledger.db）。匯出就是把整本複製成一個 .db 檔，別台電腦匯入就是整本帳。
- 換帳本（匯入、開新的）一律先把現在這本**封存**到帳本資料夾的 archive/，不刪除；要回去就再匯入封存的那個檔。
- 用 Connection.backup() 換內容，不搬檔案：網站或聊天助理正開著帳本也換得了（backup 會自己拿鎖），
  WAL 裡還沒寫回的資料也一起帶走。
- 暫存的檔（匯入前的檢查、開新的那本）也放在帳本資料夾裡（tmp/），不放共用的暫存資料夾。
- 匯入的檔先在暫存處打開、跑 migration（舊版匯出的也能用），檢查是記帳工具的帳本、版本不比程式新，才換上。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .. import clock, db, paths
from ..errors import Conflict, InvalidInput, NotFound

REQUIRED_TABLES = {"accounts", "categories", "transactions", "periods", "settings"}
MAX_IMPORT_BYTES = 500 * 1024 * 1024


def archive_dir() -> Path:
    folder = paths.data_dir() / "archive"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _tmp_dir() -> Path:
    folder = paths.data_dir() / "tmp"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _stamp() -> str:
    return clock.now().strftime("%Y%m%d-%H%M%S")


def _copy(src: sqlite3.Connection, dest_path: Path) -> Path:
    """整本複製到 dest_path（先寫 .partial，寫完才改名，不會留下半個檔）。"""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    partial = dest_path.with_suffix(".partial")
    partial.unlink(missing_ok=True)
    out = sqlite3.connect(str(partial))
    try:
        src.backup(out)
    finally:
        out.close()
    partial.replace(dest_path)
    return dest_path


def describe(conn: sqlite3.Connection) -> dict[str, Any]:
    """一本帳的大概：幾筆、從哪天到哪天、資料庫格式。"""
    row = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM transactions WHERE status = 'active'").fetchone()
    return {"schema": db.schema_version(conn), "transactions": row[0], "first_date": row[1], "last_date": row[2]}


def info(conn: sqlite3.Connection) -> dict[str, Any]:
    f = db.main_db_file(conn)
    archives = sorted(archive_dir().glob("*.db"), reverse=True)
    return {**describe(conn), "db_file": str(f) if f else None, "archive_dir": str(archive_dir()),
            "archives": [{"name": a.name, "bytes": a.stat().st_size} for a in archives[:20]]}


def archive(conn: sqlite3.Connection, reason: str) -> Path:
    """把現在這本封存成 <帳本資料夾>/archive/ledger-<時間>-<原因>.db。"""
    safe = "".join(ch for ch in reason if ch.isalnum() or ch in "-_") or "archive"
    return _copy(conn, archive_dir() / f"ledger-{_stamp()}-{safe}.db")


def export_to(conn: sqlite3.Connection, dest: Path | str) -> dict[str, Any]:
    dest = paths.confined(dest)                               # 只能寫在這本帳的檔案資料夾裡
    if dest.is_dir() or not dest.suffix:                      # 給的是資料夾（沒有副檔名）：自己取名
        dest = dest / f"記帳-{_stamp()}.db"
    _copy(conn, dest)
    return {"path": str(dest), "bytes": dest.stat().st_size, **describe(conn)}


def _replace(conn: sqlite3.Connection, source_path: Path) -> None:
    """用 source_path 的內容整本換掉 conn 這本（conn 的檔案位置不變）。"""
    src = sqlite3.connect(str(source_path))
    try:
        src.backup(conn)
    finally:
        src.close()


def check_file(path: Path | str) -> dict[str, Any]:
    """這個檔是不是記帳工具的帳本；是的話先跑 migration（在暫存副本上），回大概的內容與暫存副本的位置。"""
    path = Path(path)
    if not path.is_file():
        raise NotFound(f"找不到檔案 {path.name}")
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise InvalidInput("檔案太大，不像是一本帳")
    with open(path, "rb") as f:
        if f.read(16) != b"SQLite format 3\x00":
            raise InvalidInput("這不是記帳工具的帳本檔（要是「匯出」產生的 .db 檔）")
    staged = Path(tempfile.mkdtemp(prefix="ledger-import-", dir=str(_tmp_dir()))) / "book.db"
    shutil.copyfile(path, staged)
    wal = Path(f"{path}-wal")                                # 直接拿別人正在用的帳本檔：還沒寫回的在 -wal 裡
    if wal.is_file():
        shutil.copyfile(wal, f"{staged}-wal")
    try:
        probe = sqlite3.connect(str(staged))
        try:
            tables = {r[0] for r in probe.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            version = probe.execute("PRAGMA user_version").fetchone()[0]
        finally:
            probe.close()
    except sqlite3.DatabaseError:
        raise InvalidInput("這個檔壞掉了，打不開") from None
    missing = REQUIRED_TABLES - tables
    if missing:
        raise InvalidInput("這不是記帳工具的帳本（少了 " + "、".join(sorted(missing)) + "）")
    if version > len(db.MIGRATIONS):
        raise Conflict("這本帳是比較新的版本做的，先把這台電腦的記帳工具更新再匯入")
    book = db.connect(staged)                                 # 舊版的帳本在這裡補上新的資料表
    try:
        summary = describe(book)
    finally:
        book.close()
    return {**summary, "staged": str(staged)}


def import_from(conn: sqlite3.Connection, path: Path | str) -> dict[str, Any]:
    checked = check_file(paths.confined(path))               # 只能讀這本帳的檔案資料夾裡的檔
    archived = archive(conn, "before-import")
    _replace(conn, Path(checked["staged"]))
    shutil.rmtree(Path(checked["staged"]).parent, ignore_errors=True)
    return {"archived": str(archived), **describe(conn)}


def replace_with_seed(conn: sqlite3.Connection, seed: Any = None) -> None:
    """不封存，直接把 conn 這本換成用 seed 建的空帳本（呼叫端自己確定現在這本可以丟：例如第一次使用、還沒記過帳）。"""
    folder = Path(tempfile.mkdtemp(prefix="ledger-new-", dir=str(_tmp_dir())))
    try:
        fresh = db.connect(folder / "book.db", seed=seed)    # 跑完 migration＝預設類別、帳戶、設定
        fresh.close()
        _replace(conn, folder / "book.db")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def new_book(conn: sqlite3.Connection, *, seed: Any = None) -> dict[str, Any]:
    """封存現在這本，換成一本只有預設類別與帳戶的空帳本（seed：用哪一套起始資料，見 seed.config）。"""
    archived = archive(conn, "before-new")
    replace_with_seed(conn, seed)
    return {"archived": str(archived), **describe(conn)}


def export_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """整本帳攤平成 (表名, 那一列的 JSON)：給只能下載 CSV 的地方做整本備份（每一張表、每一列都在）。"""
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                         "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    out: list[tuple[str, str]] = [("_schema", json.dumps({"version": db.schema_version(conn)}))]
    for table in tables:
        for row in conn.execute(f'SELECT * FROM "{table}"'):
            out.append((table, json.dumps({k: row[k] for k in row.keys()}, ensure_ascii=False, sort_keys=True)))
    return out
