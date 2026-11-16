"""猜類別：讓記帳不用每次選類別（每筆都要手動選類別是記帳最麻煩的地方）。

0. 你以前改過的：把「某家店」改成某個類別，下次同一家店就直接歸那裡（merchant_aliases；吃的不學，餐別看時間）。
1. 細項的關鍵字：取最長的那個符合（越長越具體：「鹹酥雞」比「雞」準）。
2. 明寫「早餐／午餐／晚餐」就照寫的。
3. 看得出是吃的（關鍵字落在三餐，或內容像食物）→ 依時間分：
   10:30 前早餐、15:00 前午餐、17:00 前下午點心、21:30 前晚餐、之後宵夜。
4. 都不像 → 回 None，記帳時放「待分類」，不擋。
沒給時間、而且日期是今天，就用現在的時間；其他日期沒時間就不猜餐別。
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from .. import clock
from . import refdata

FOOD_RE = re.compile(r"飯|麵|便當|雞|鴨|牛|豬|魚|餐|餃|粥|蛋餅|飯糰|吐司|三明治|漢堡|披薩|pizza|滷味|咖哩|鍋|小吃|丼|"
                     r"拉麵|壽司|燒臘|排骨|水煎包|包子|饅頭|涼麵|米粉|肉圓|蚵仔煎|潤餅|炒|湯|定食|速食|"
                     r"廚房|食堂|小館|餐酒|烤|火鍋|牛排|炸",
                     re.IGNORECASE)
EXPLICIT_MEAL = {"早餐": "breakfast", "早午餐": "breakfast", "午餐": "lunch", "中餐": "lunch", "晚餐": "dinner"}
SNACK = "snack"                                         # 點心宵夜的系統代號


def meal_key_by_time(hhmm: str) -> str:
    """回細項的系統代號（meal.breakfast／meal.lunch／snack／meal.dinner）。"""
    hour, minute = int(hhmm[:2]), int(hhmm[3:5])
    t = hour + minute / 60
    if t < 10.5:
        return "meal.breakfast"
    if t < 15:
        return "meal.lunch"
    if t < 17:
        return SNACK
    if t < 21.5:
        return "meal.dinner"
    return SNACK


def meal_by_time(hhmm: str) -> str:
    """回細項的預設名字（這本帳裡的名字用 refdata.system_name(conn, meal_key_by_time(...))）。"""
    return refdata.SYSTEM_KEYS[meal_key_by_time(hhmm)]


def _learned(conn: sqlite3.Connection, text: str, cats: list[dict[str, Any]]) -> tuple[dict[str, Any], str] | None:
    """你以前改過類別的店：用最長的符合。"""
    from . import cards
    key = cards.merchant_key(text)
    if not key:
        return None
    by_id = {c["id"]: c for c in cats}
    best = None
    for r in conn.execute("SELECT pattern, merchant, category_id FROM merchant_aliases WHERE category_id IS NOT NULL"):
        c = by_id.get(r["category_id"])
        if c and not c["meal_slot"] and r["pattern"] and r["pattern"] in key and (best is None or len(r["pattern"]) > len(best[0])):
            best = (r["pattern"], r["merchant"], c)
    return (best[2], best[1]) if best else None


def learn(w: Any, txn: dict[str, Any]) -> bool:
    """你把一筆改成某個類別：記住「這家店」＝這個類別，下次同一家店自己歸過去（聊天助理、網站、CLI 都算）。

    只學不是吃的（餐別看時間，同一家店中午晚上不同類別）、而且有店名或內容可以認的；
    內容只是類別名字（午餐）或銀行的字（網路轉）就不學。"""
    from . import cards, labels
    if txn["kind"] != "expense" or not txn.get("category_id"):
        return False
    cat = refdata.category_by_id(w.conn, txn["category_id"])
    if cat["meal_slot"]:
        return False
    learned = False
    for text in dict.fromkeys(t.strip() for t in (txn.get("merchant"), txn.get("desc")) if t and t.strip()):
        key = cards.merchant_key(text)             # 帳單上的店名與你寫的名字各學一份，之後哪一種寫法都認得
        if len(key) < 2 or labels.is_filler(text, cat["name"]):
            continue
        w.conn.execute("INSERT INTO merchant_aliases (pattern, merchant, category_id, created_at) VALUES (?,?,?,?) "
                       "ON CONFLICT(pattern) DO UPDATE SET merchant = excluded.merchant, category_id = excluded.category_id",
                       (key, text, cat["id"], w.now))
        learned = True
    return learned


def reclassify(w: Any) -> list[dict[str, Any]]:
    """把「待分類」的支出用現在的規則再猜一次（訂閱關鍵字、你以前教過的店、吃的依時間）；猜得出的才改，猜不出的不動。

    舊帳裡常有從信用卡帳單匯入、停在待分類的支出（例如 AI 訂閱），新的規則只對之後新記的有效，所以給一個「自動分類」，
    使用者按了才動、整個一個批次（網站的「復原」一次全退）。回改了哪些：[{id, date, amount, desc, category, reason}]。"""
    from . import transactions as tx
    cats = {c["id"]: c for c in refdata.categories(w.conn)}
    done: list[dict[str, Any]] = []
    rows = w.conn.execute(
        "SELECT id, date, time, amount, description, merchant FROM transactions "
        "WHERE status = 'active' AND kind = 'expense' AND category_id IS NULL AND financed = 0 ORDER BY date, rowid").fetchall()
    for r in rows:
        cat, reason = guess(w.conn, r["description"], time=r["time"], date=r["date"], merchant=r["merchant"], kind="expense")
        if cat is None or cat["id"] not in cats:
            continue
        tx.update(w, r["id"], category_id=cat["id"])
        done.append({"id": r["id"], "date": r["date"], "amount": r["amount"], "desc": r["description"] or r["merchant"],
                     "category": cat["name"], "reason": reason})
    return done


def guess(conn: sqlite3.Connection, desc: str, *, time: str | None = None, date: str | None = None,
          merchant: str = "", kind: str = "expense") -> tuple[dict[str, Any] | None, str]:
    """回 (細項, 理由)。猜不到回 (None, 理由)。"""
    text = f"{desc or ''} {merchant or ''}".strip()
    if not text:
        return None, "沒有內容可以猜"
    norm = refdata.normalize(text)
    cats = [c for c in refdata.categories(conn) if c["kind"] == kind]
    by_key = {c["system_key"]: c for c in cats if c.get("system_key")}
    learned = _learned(conn, text, cats)
    if learned is not None:
        return learned[0], f"你之前把「{learned[1]}」歸在這裡"
    if time is None and date == clock.today().isoformat():
        time = clock.now().strftime("%H:%M")

    for word, slot in EXPLICIT_MEAL.items():
        if word in text:
            hit = next((c for c in cats if c["meal_slot"] == slot), None)
            if hit:
                return hit, f"內容寫了「{word}」"

    best, best_kw = None, ""
    for c in cats:
        for kw in c["keywords"]:
            k = refdata.normalize(kw)
            if k and k in norm and len(k) > len(refdata.normalize(best_kw)):
                best, best_kw = c, kw
    looks_like_food = bool(FOOD_RE.search(text))
    if best is not None and not best["meal_slot"]:
        return best, f"內容有「{best_kw}」"
    if kind == "expense" and (best is not None or looks_like_food):
        if time:
            key = meal_key_by_time(time)
            if key in by_key:
                return by_key[key], f"看起來是吃的，依時間 {time}"
        if best is not None:
            return best, f"內容有「{best_kw}」"
        return None, "看起來是吃的，但不知道幾點，分不出哪一餐"
    return None, "猜不出類別"
