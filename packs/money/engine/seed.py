"""起始資料：用途、大類、細項（含猜類別的關鍵字、icon、系統代號）、預設帳戶與設定。

內容不寫在程式裡，放在 [seeds/generic.json](seeds/generic.json)（食衣住行育樂醫療其他、一個現金、一個銀行帳戶）。
開新帳本時用哪一套（先到先用）：呼叫端明確給的 seed（"generic"、JSON 檔路徑或一個 dict）＞
帳本資料夾裡自己的 `categories.json` ＞ 內建的 generic。不讀環境變數。
只影響**新帳本**；已經在用的帳本要增改類別，用設定（refdata 的 group／category）。

seed 可以寫 `buckets[].label/active/locked`（只套到全新的帳本）、`categories[].system_key`（程式用代號找類別）
與 `optional`（開了才加的大類）。**金額（預算、水位）不寫在這裡**：那是個人資料，在設定頁填。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

GENERIC = Path(__file__).parent / "seeds" / "generic.json"
SEEDS = {"generic": GENERIC}

# 核心用途沒有寫 label 時的顯示名字，與預設「固定住」（一定要做到）的那幾格
DEFAULT_LABELS = {"giving": "捐贈", "fixed": "固定支出", "saving": "存錢", "living": "生活費", "invest": "投資", "debt": "還債"}
DEFAULT_LOCKED = ("giving", "fixed", "debt")


def seed_file(name: str | None = None) -> Path:
    """`generic`／一個 JSON 檔的路徑 → 檔案。不給＝generic。找不到檔案丟 ValueError。"""
    name = (name or "").strip()
    if not name:
        return GENERIC
    path = SEEDS.get(name.lower()) or Path(name)
    if not path.is_file():
        raise ValueError(f"找不到 seed「{name}」（可以用 {'、'.join(SEEDS)}，或一個 JSON 檔的路徑）")
    return path


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def config(source: Any = None) -> dict[str, Any]:
    """起始類別樹。source：dict（直接用）、"generic" 或 JSON 檔路徑；None＝帳本資料夾的 categories.json，再沒有就 generic。"""
    if isinstance(source, dict):
        return source
    if source:
        return _load(seed_file(str(source)))
    try:
        from . import paths
        ctx = paths.current_or_none()
        mine = ctx.home / "categories.json" if ctx is not None else None
        if mine is not None and mine.exists():
            return _load(mine)
    except Exception:                                        # 檔案壞掉：用內建的
        pass
    return _load(GENERIC)


def optional(source: Any = None) -> dict[str, Any]:
    """能另外開的功能：這本的 seed 有寫 optional 就用它，沒有就借 generic 的。"""
    return config(source).get("optional") or _load(GENERIC)["optional"]


def icons(source: Any = None) -> dict[str, str]:
    """名字 → icon（大類與細項都在同一張表裡）。"""
    data = config(source)
    out = {g["name"]: g.get("icon", "") for g in data["groups"]}
    for g in data["groups"]:
        for c in g["categories"]:
            out[c["name"]] = c.get("icon", "")
    return {k: v for k, v in out.items() if v}


def system_keys(source: Any = None) -> dict[str, str]:
    """系統代號 → 這套 seed 裡的類別名字（含 optional 的）。"""
    data = config(source)
    groups = list(data["groups"]) + [g for f in (data.get("optional") or {}).values() for g in f.get("groups", [])]
    return {c["system_key"]: c["name"] for g in groups for c in g["categories"] if c.get("system_key")}


def install(conn: sqlite3.Connection, source: Any = None) -> None:
    """新帳本：把起始的用途、大類、細項、帳戶、設定寫進去（在建 schema 的同一個交易裡）。"""
    data = config(source)
    for sort, b in enumerate(data["buckets"]):
        label = b["label"] if "label" in b else DEFAULT_LABELS.get(b["id"], b["name"])
        locked = int(bool(b["locked"])) if "locked" in b else int(b["id"] in DEFAULT_LOCKED)
        active = int(bool(b["active"])) if "active" in b else 1
        conn.execute("INSERT INTO buckets (id, name, sort, is_living, label, locked, active) VALUES (?,?,?,?,?,?,?)",
                     (b["id"], b["name"], sort, int(b["is_living"]), label, locked, active))
    if not conn.execute("SELECT 1 FROM buckets WHERE id = 'trading' OR name = '資金操盤' OR label = '資金操盤'").fetchone():
        sort = (conn.execute("SELECT MAX(sort) FROM buckets").fetchone()[0] or 0) + 1   # 資金操盤：先建好但不開
        conn.execute("INSERT INTO buckets (id, name, label, sort, is_living, locked, active) "
                     "VALUES ('trading', '資金操盤', '資金操盤', ?, 0, 0, 0)", (sort,))
    for gsort, g in enumerate(data["groups"]):
        gid = conn.execute("INSERT INTO category_groups (name, bucket_id, sort, icon) VALUES (?,?,?,?)",
                           (g["name"], g["bucket"], gsort, g.get("icon", ""))).lastrowid
        for csort, c in enumerate(g["categories"]):
            conn.execute(
                "INSERT INTO categories (name, group_id, kind, keywords, meal_slot, transfer_purpose, sort, icon, system_key)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (c["name"], gid, c["kind"], c["keywords"], c["meal_slot"], c["transfer_purpose"], csort, c.get("icon", ""),
                 c.get("system_key") or None))
    for sort, a in enumerate(data["accounts"]):
        kind = a["kind"]
        asset = {"cash": "cash", "bank": "cash", "e_wallet": "cash", "securities": "invest"}.get(kind, "")
        conn.execute("INSERT INTO accounts (name, kind, purpose, aliases, liquid, sort, asset_class) VALUES (?,?,?,?,?,?,?)",
                     (a["name"], kind, a["purpose"], a["aliases"], int(a["liquid"]), sort, asset))
    for key, value in data["settings"].items():
        conn.execute("INSERT INTO settings (key, value) VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
