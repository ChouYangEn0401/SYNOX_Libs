"""這本帳的檔案放哪裡——每次呼叫明確設定，沒設定就丟錯（不退回任何共用位置）。

帳本資料夾（資料庫、附檔、備份、封存都在裡面）由呼叫的人用 `use_home(path)` 設定，存在 contextvars 裡：
同一個程序、多條執行緒或多個使用者同時用，各自看到自己設定的那一個，不會互相蓋掉。
不讀環境變數、沒有預設的資料夾。

    with paths.use_home(folder, today=None, tz_offset=None):
        conn = db.connect()
        ...

同一份 context 也帶 `today`（固定「今天」，測試或呼叫端要跟自己的日期一致時用）與 `tz_offset`
（帳本設定的時差，小時；None＝跟著這台電腦），clock 從這裡讀。
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator, Optional

from .errors import NoHome


@dataclass(frozen=True)
class Context:
    home: Path
    today: Optional[date] = None          # 固定「今天」；None＝照時鐘
    tz_offset: Optional[float] = None     # 時差（小時）；None＝跟著這台電腦
    files_root: Optional[Path] = None     # 匯入匯出、收據可以用的檔案資料夾；None＝帳本資料夾本身


_CONTEXT: contextvars.ContextVar[Optional[Context]] = contextvars.ContextVar("money_engine_context", default=None)


def current() -> Context:
    """目前的設定；沒設定就丟 NoHome。"""
    ctx = _CONTEXT.get()
    if ctx is None:
        raise NoHome("帳本資料夾還沒設定，不開任何帳本", hint="呼叫端要先用 paths.use_home(資料夾)")
    return ctx


def current_or_none() -> Optional[Context]:
    return _CONTEXT.get()


@contextmanager
def use_home(home: Path | str, *, today: Optional[date] = None, tz_offset: Optional[float] = None,
             files_root: Path | str | None = None) -> Iterator[Path]:
    """在這個 with 裡，引擎的帳本資料夾就是 home（離開就還原成原本的）。
    files_root：匯入匯出、收據檔可以放的資料夾（要包含 home 或就是 home；不給＝home）。"""
    path = Path(home)
    token = _CONTEXT.set(Context(path, today, tz_offset, Path(files_root) if files_root else None))
    try:
        yield path
    finally:
        _CONTEXT.reset(token)


def data_dir() -> Path:
    """這本帳的資料夾；沒設定就丟 NoHome（呼叫當下才讀，不在 import 時算好存起來）。"""
    path = current().home
    path.mkdir(parents=True, exist_ok=True)
    return path


def files_root() -> Path:
    """匯入匯出、收據檔可以用的資料夾（沒另外設定＝帳本資料夾）。"""
    ctx = current()
    root = (ctx.files_root or ctx.home).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def confined(raw: str | Path | None, *, default: Path | None = None) -> Path:
    """呼叫端給的檔案路徑 → 一定在 files_root() 裡面的完整路徑（相對路徑以它為準；擋 ..、擋跑出去的絕對路徑）。
    錯誤訊息不帶資料夾位置。"""
    from .errors import InvalidInput
    if raw in (None, ""):
        if default is None:
            raise InvalidInput("要給檔案路徑")
        path = Path(default)
    else:
        path = Path(raw)
    root = files_root()
    full = (path if path.is_absolute() else root / path).resolve()
    if full != root and root not in full.parents:
        raise InvalidInput("檔案要放在自己的資料夾裡", hint="寫相對路徑就好（從自己的檔案資料夾算起）",
                           fields={"path": str(raw if raw not in (None, "") else path.name)})
    return full
