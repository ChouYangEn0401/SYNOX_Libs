"""選用的外部項目（月曆上顯示別的工具的東西）：**預設什麼都沒有**，沒有也不會出錯。

為什麼是一個空的掛勾：SYNOX 的工具彼此隔離（不能互相 import、也沒有讀別的工具資料的 API），所以行事曆不能自己去「找」
習慣、旅程、專案、待辦。如果之後 SYNOX 提供了這種機制，只要讓 `pack.link_sources` 是一串函式就會接上：

    pack.link_sources = [fn, …]
    fn(user, first_day: date, last_day: date) -> [{"date": "YYYY-MM-DD", "text": "…", "done": False, "at": "HH:MM", "id": "…"}, …]

每個來源的錯一律吞掉（月曆照樣畫、只是少那一種）；回來的每一列都會被檢查，格式不對的丟掉。
來源的名字取函式的 __name__（畫面上小字標出來源），沒有名字就寫「外部」。
"""

from __future__ import annotations

from datetime import date

MAX_ROWS = 400
MAX_TEXT = 80


def collect(sources, user, first: date, last: date) -> list:
    """sources＝pack.link_sources（可以是 None）。回 [{date, text, done, at, id, source}…]，已經檢查過、依日期排好。"""
    out = []
    for fn in sources or ():
        try:
            rows = list(fn(user, first, last) or [])
        except Exception:                                  # 外部來源壞了不能拖垮月曆
            continue
        name = str(getattr(fn, "__name__", "") or "外部")[:24]
        for r in rows[:MAX_ROWS]:
            try:
                d = date.fromisoformat(str(r["date"])[:10])
                text = str(r["text"]).strip()[:MAX_TEXT]
            except (KeyError, TypeError, ValueError):
                continue
            if not text or not first <= d <= last:
                continue
            out.append({"date": d.isoformat(), "text": text, "done": bool(r.get("done")),
                        "at": str(r.get("at") or "")[:5], "id": str(r.get("id") or ""), "source": name})
    out.sort(key=lambda r: (r["date"], r["at"] or "99:99", r["text"]))
    return out[:MAX_ROWS]
