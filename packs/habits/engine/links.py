"""跟其他工具連動的掛鉤（目前都是空的：這個工具單獨就能完整使用）。

SYNOX 還沒有給工具「呼叫別的工具」的機制（只有 requires.packs 的硬相依與 provides 的自述），
所以這裡不去猜、不去 import 別的工具，只留幾個有名字的掛鉤：日後主機有了安全的跨工具介面，
只要在 pack.py 的 install() 裡把函式掛上來，引擎與畫面都不用改。沒掛＝回空的，什麼都不會出錯。
"""

_habit_blocks = None        # fn(habit_id, start: date, end: date) -> [{"id","title","date","from","to","start_at","end_at","minutes","_s","_e"}]


def set_habit_blocks(fn) -> None:
    """掛上「這段時間是留給這條習慣的行程」的提供者（例如行事曆）。None＝拿掉。"""
    global _habit_blocks
    _habit_blocks = fn


def habit_blocks(habit_id, start, end) -> list:
    if _habit_blocks is None:
        return []
    try:
        return list(_habit_blocks(habit_id, start, end) or [])
    except Exception:
        return []            # 別的工具壞了不能拖垮這裡
