"""收據附檔：複製進 <帳本資料夾>/attachments/<年>/<月>/<sha256 前 16 碼>.<副檔名>，交易刪掉檔案也留著（撤銷救得回來）。

同一張圖（sha256 相同）而且金額相同、那筆交易還在 → 視為重送。
收據檔只能來自這本帳的檔案資料夾（paths.confined；相對路徑以它為準），不能讀別處的檔。
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .. import clock, paths
from ..db import Writer, row_dict
from ..errors import InvalidInput

ALLOWED = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
           ".webp": "image/webp", ".bmp": "image/bmp", ".heic": "image/heic", ".pdf": "application/pdf"}
MAX_BYTES = 20 * 1024 * 1024


def folder() -> Path:
    return paths.data_dir() / "attachments"


def inspect(path_text: str) -> dict[str, Any]:
    try:
        path = paths.confined(path_text)
    except InvalidInput:
        raise InvalidInput("收據檔案要放在自己的資料夾裡", fields={"attach": "不在自己的資料夾"}) from None
    if not path.is_file():
        raise InvalidInput(f"找不到收據檔案：{path_text}", fields={"attach": "檔案不存在"})
    ext = path.suffix.lower()
    if ext not in ALLOWED:
        raise InvalidInput(f"收據檔案類型 {ext} 不支援", hint="可以是：" + "、".join(ALLOWED), fields={"attach": "類型不支援"})
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise InvalidInput("收據檔案超過 20MB", fields={"attach": "太大"})
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": path, "sha256": digest, "ext": ext, "mime": ALLOWED[ext], "size": size, "name": path.name}


def replay_of(conn: sqlite3.Connection, sha256: str, amount: int) -> str | None:
    row = conn.execute("SELECT t.id FROM attachments a JOIN transactions t ON t.id = a.txn_id "
                       "WHERE a.sha256 = ? AND t.amount = ? AND t.status = 'active' LIMIT 1", (sha256, amount)).fetchone()
    return row[0] if row else None


def save(w: Writer, txn_id: str, info: dict[str, Any]) -> dict[str, Any]:
    today = clock.today()
    rel = Path(f"{today:%Y}") / f"{today:%m}" / f"{info['sha256'][:16]}{info['ext']}"
    if not w.dry_run:
        dest = folder() / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(info["path"], dest)
    aid = w.conn.execute("INSERT INTO attachments (txn_id, sha256, path, mime, original_name, size, created_at) "
                         "VALUES (?,?,?,?,?,?,?)", (txn_id, info["sha256"], rel.as_posix(), info["mime"], info["name"],
                                                    info["size"], w.now)).lastrowid
    after = row_dict(w.conn.execute("SELECT * FROM attachments WHERE id = ?", (aid,)).fetchone())
    w.audit("create", "attachments", aid, None, after)
    return after


def for_txn(conn: sqlite3.Connection, txn_id: str) -> list[dict[str, Any]]:
    return [row_dict(r) for r in conn.execute("SELECT * FROM attachments WHERE txn_id = ? ORDER BY id", (txn_id,))]
