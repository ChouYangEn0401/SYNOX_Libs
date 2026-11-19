"""引擎讀寫資料的唯一入口：資料存在哪由呼叫的那一層決定（SYNOX 保管箱、測試用的記憶體）。

    with storage.bound(backend):      # backend 有 load(name, default) 與 save(name, data)
        routines.upsert({...})

綁定放在 contextvars 裡：同一時間不同的人（或不同的執行緒）各用各的，不會互相串。
沒綁定就用 RuntimeError 擋下來——引擎不會自己退回任何共用位置。
"""
import contextvars
import json
from contextlib import contextmanager

_backend = contextvars.ContextVar("habits_backend", default=None)


class Unbound(RuntimeError):
    pass


@contextmanager
def bound(backend):
    token = _backend.set(backend)
    try:
        yield backend
    finally:
        _backend.reset(token)


def _current():
    b = _backend.get()
    if b is None:
        raise Unbound("沒有綁定這個人的資料")
    return b


def load(name: str, default=None):
    return _current().load(name, default)


def save(name: str, data) -> None:
    _current().save(name, data)


class MemoryBackend:
    """測試與預設的假資料：一個 dict，每次存都真的轉一次 JSON（存不了的東西會出錯）。"""

    def __init__(self):
        self.docs = {}

    def load(self, name, default=None):
        raw = self.docs.get(name)
        return default if raw is None else json.loads(raw)

    def save(self, name, data):
        self.docs[name] = json.dumps(data, ensure_ascii=False)

