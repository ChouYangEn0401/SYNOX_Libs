"""第一次用與選配功能。

- `init(seed)`：開一本用指定 seed 的帳本。帳本已經有寫入就不動（Conflict）；有檔案但一筆都沒寫過（例如先打了
  一個查詢指令、用錯 seed 建出來的）就照 /money_book_new 的做法封存後換新的。
- `features()`／`enable(name)`：seed 的 `optional`（捐贈、投資、還債、薪資扣繳、資金操盤）——加那幾個大類與細項、
  打開那一格用途。重跑不會多；這本帳已經有那個功能的任一細項（例如從舊帳本搬來的）就不加類別，只確定那一格開著。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .. import db, seed
from ..db import Writer
from ..errors import Conflict, InvalidInput, NotFound
from . import plans, refdata


def _used(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT 1 FROM batches LIMIT 1").fetchone()
                or conn.execute("SELECT 1 FROM transactions LIMIT 1").fetchone())


def init(seed_name: str) -> tuple[sqlite3.Connection, dict[str, Any]]:
    """回 (連線, 結果)。seed_name：generic／JSON 檔路徑。seed 用參數傳給 db.connect（不寫環境變數）。"""
    try:
        path = seed.seed_file(seed_name)
    except ValueError as e:
        raise InvalidInput(str(e), fields={"seed": seed_name}) from None
    existed = db.db_path().exists()
    conn = db.connect(seed=str(path))
    replaced = None
    if existed:
        if _used(conn):
            raise Conflict("這本帳已經在用了（有寫入），seed 只在開新帳本時決定",
                           hint="要換：先開一本新的（現在這本先封存），或開選配功能加你要的那幾格")
        from . import book
        replaced = book.new_book(conn, seed=str(path))["archived"]
    if (paths_file := (db.db_path().parent / "categories.json")).exists():
        note = f"帳本資料夾有自己的 {paths_file.name}，以它為準（seed 參數沒用到）"
    else:
        note = ""
    name = seed.config(str(path)).get("name") or path.stem
    return conn, {"seed": name, "replaced_empty": replaced, "note": note, "features": features(conn)}


SEED_NAMES = (("generic", "通用"), ("blank", "空白"))          # 第一次使用能選的起始類別


def blank_seed() -> dict[str, Any]:
    """「空白」的起始資料：通用版拿掉一般的類別，只留系統記帳會用到的（有 system_key 的：三餐、繳卡費、借出、薪資…）
    和它們的大類；用途、帳戶、設定、選配功能照通用版。每次呼叫給一份新的 dict（不共用）。"""
    data = seed.config("generic")
    data = json.loads(json.dumps(data, ensure_ascii=False))       # 深拷貝：不動到讀進來的那一份
    groups = []
    for g in data["groups"]:
        cats = [c for c in g["categories"] if c.get("system_key")]
        if cats:
            groups.append({**g, "categories": cats})
    data["groups"] = groups
    data["name"] = "blank"
    return data


def seed_source(name: str | None) -> Any:
    """第一次使用選的起始類別 → 給 db.connect／book 的 seed（generic／空白）。認中文名字。"""
    key = (name or "").strip().lower()
    if key in ("", "generic", "通用", "通用版"):
        return "generic"
    if key in ("blank", "空白", "empty"):
        return blank_seed()
    raise InvalidInput(f"沒有「{name}」這種起始類別", hint="通用（食衣住行育樂醫療其他）或空白（只留系統用的）",
                       suggestions=[label for _key, label in SEED_NAMES], fields={"seed": "通用／空白"})


def has_transactions(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM transactions LIMIT 1").fetchone() is not None


def reseed(conn: sqlite3.Connection, name: str | None) -> dict[str, Any]:
    """還沒記過任何一筆帳的帳本：整本換成用 name 這套起始資料建的新帳本（不封存：裡面沒有帳）。
    已經有帳就丟 Conflict（起始類別只在開帳本時決定）。"""
    if has_transactions(conn):
        raise Conflict("這本帳已經有帳了，起始類別只在開新帳本時決定",
                       hint="要換：先開一本新的（現在這本會先封存），或到設定頁自己加、改類別")
    source = seed_source(name)
    from . import book
    book.replace_with_seed(conn, source)
    return {"seed": "blank" if isinstance(source, dict) else source}


def _cat_names(f: dict[str, Any]) -> list[str]:
    return [c["name"] for g in f.get("groups", []) for c in g.get("categories", [])]


def _present(conn: sqlite3.Connection, f: dict[str, Any]) -> bool:
    """這本帳已經有這個功能的任何一個細項（例如舊帳本：捐贈、股票、卡分期、勞保…都有）＝類別那邊已經好了，不再加。"""
    names = _cat_names(f)
    return not names or any(conn.execute("SELECT 1 FROM categories WHERE name = ? AND active = 1", (n,)).fetchone()
                            for n in names)


def features(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    buckets = {b["id"]: b for b in plans.buckets(conn, include_inactive=True)}
    for name, f in seed.optional().items():
        b = buckets.get(f.get("bucket", ""))
        names = [g["name"] for g in f.get("groups", [])]
        out.append({"name": name, "summary": f.get("summary", ""), "bucket": f.get("bucket"),
                    "enabled": bool(b and b["active"]) and _present(conn, f), "groups": names})
    return out


def enable(w: Writer, name: str) -> dict[str, Any]:
    opts = seed.optional()
    if name not in opts:
        raise NotFound(f"沒有「{name}」這個功能", suggestions=list(opts), hint="設定頁的「選配功能」看有哪些")
    f = opts[name]
    added_groups, added_cats = [], []
    bucket = f.get("bucket")
    turned_on = False
    if bucket:                                               # 先打開那一格：停用的用途底下不能加大類
        row = w.conn.execute("SELECT active FROM buckets WHERE id = ?", (bucket,)).fetchone()
        if row is not None and not row["active"]:
            plans.set_bucket(w, bucket, active=True)
            turned_on = True
    for g in ([] if _present(w.conn, f) else f.get("groups", [])):       # 已經有自己的一套（舊帳本）就不加
        try:
            refdata.group_by_name(w.conn, g["name"])
        except NotFound:
            refdata.add_group(w, name=g["name"], bucket=g.get("bucket"), icon=g.get("icon") or None)
            added_groups.append(g["name"])
        for c in g.get("categories", []):
            if w.conn.execute("SELECT 1 FROM categories WHERE name = ? AND active = 1", (c["name"],)).fetchone():
                continue
            refdata.add_category(w, name=c["name"], group=g["name"], kind=c.get("kind", "expense"),
                                 keywords=c.get("keywords") or None, transfer_purpose=c.get("transfer_purpose"),
                                 meal_slot=c.get("meal_slot"), icon=c.get("icon") or None,
                                 system_key=c.get("system_key") or None)
            added_cats.append(c["name"])
    return {"feature": name, "bucket": bucket, "bucket_turned_on": turned_on, "added_groups": added_groups,
            "added_categories": added_cats, "changed": bool(turned_on or added_groups or added_cats)}
