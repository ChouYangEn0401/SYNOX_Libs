"""「這個人的帳本」的唯一入口（隔離規則，見 CONTRACT.md「隔離九條」）。

    with session.open_book(pack, user) as book:                 指令（人打字、AI、排程）
    with session.open_book(pack, user, screen=True) as book:    完整畫面的讀、動作、匯出
        ...book.conn、book.actor、book.today、book.screen、book.path("x.csv")...

- 帳本資料夾只從 pack.path_of(<這個人的整數編號>, "book") 來（個人版 data/money/files/book/，
  伺服器版 data/users/<id>/money/files/book/）；沒有整數編號就丟 NoUser，不退回任何共用位置。
- 每次呼叫開、用完關；連線不放在模組或 pack 物件上。
- 每個人一把寫入鎖（照編號；同一條執行緒可以重入）。鎖放在 pack 物件上（install 時 attach），重新載入工具就換新的。
- screen：這次是不是畫面送來的（金額已經是最小單位；bridge/table.py 的 to_minor 照這個決定要不要換算）。
  只看呼叫的那一層，不看 caller 的種類（SYNOX 網頁主控台打字的 caller 也是 web，但那是人打的字）。
- 「今天」：帳本設了時差（tz_offset）就是 UTC 的現在＋時差；沒設就跟著這台電腦（now()）。
- 開好之後叫 compat.on_open(pack, uid, book)：舊版（0.3.1）的資料第一次轉進來由 compat／legacy 決定（W6）。

**儲存層**：帳本是這個人資料夾裡的一個 SQLite 檔，**沒有加密**（0.4.0 第一版；CONTRACT.md「儲存層」）。
日後要改成放進 SYNOX 保管箱，只要改這個檔的 _connect／_close（其他檔都只拿 book.conn）。

_connect／_close 在 engine 的 home context（contextvars）裡開連線，關的時候清掉 context；
日後要改成加密儲存，只動這兩處。
"""

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from synox.packs import kit

from . import compat, writer

BOOK_DIR = "book"                 # 這個人的檔案資料夾底下，引擎的 home（資料庫、附檔、備份、封存都在裡面）


class NoUser(RuntimeError):
    """沒有這個人的整數編號：不開任何帳本（不會退回共用的資料夾）。"""


def now() -> datetime:
    """「現在」（測試會換掉它）。"""
    return datetime.now()


def utc_now() -> datetime:
    """UTC 的現在（測試會換掉它）；預設從 now() 換算（now() 沒帶時區＝這台電腦的當地時間）。"""
    return now().astimezone(timezone.utc)


def _hours(raw) -> Optional[float]:
    """帳本設定的時差 → 小時（-12～14）；沒設或壞掉回 None（跟引擎 clock 同一個範圍）。"""
    if raw is None or raw == "":
        return None
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return None
    return hours if -12 <= hours <= 14 else None


def today_of(tz_offset) -> date:
    """這本帳的「今天」：有時差＝UTC 現在＋時差的日期；沒有＝這台電腦的今天。"""
    hours = _hours(tz_offset)
    if hours is None:
        return now().date()
    return (utc_now() + timedelta(hours=hours)).date()


@dataclass
class Book:
    """一次呼叫拿到的帳本。conn：資料庫連線；actor：寫進改動紀錄的「誰」（synox:<來源>，不放名字）；
    today：帳本的今天；user_id：SYNOX 給的整數編號；path(*parts)：這個人自己的檔案資料夾裡的路徑（擋 ..、絕對路徑）；
    fresh：這次是不是剛建好的空帳本；screen：這次是不是畫面送來的（金額已經是最小單位）；
    reviewer：寫檢討的提供者（bridge/writer.py；沒有＝None，/money_review_run 回 llm_unavailable）。"""
    conn: Any
    actor: str
    today: Any
    user_id: int
    path: Callable[..., str]
    fresh: bool = False
    screen: bool = False
    reviewer: Optional[Callable] = None


class Locks:
    """每個人一把寫入鎖（RLock：同一條執行緒裡巢狀開也不會卡住）。"""

    def __init__(self):
        self._mu = threading.Lock()
        self._by_user = {}

    def of(self, user_id: int):
        with self._mu:
            lock = self._by_user.get(user_id)
            if lock is None:
                lock = self._by_user[user_id] = threading.RLock()
            return lock


def attach(pack) -> None:
    """install() 一開始叫：把這個工具的鎖表掛在 pack 上（重新載入＝新的 pack、新的鎖表）。"""
    if getattr(pack, "money_locks", None) is None:
        pack.money_locks = Locks()


def user_id_of(user) -> int:
    uid = user if isinstance(user, int) else getattr(user, "tg_id", None)
    if isinstance(uid, bool) or not isinstance(uid, int):
        raise NoUser("money: open_book needs a user with an integer id")
    return uid


def actor_of() -> str:
    """改動紀錄的「誰」：synox:<這一次是誰叫的>（telegram／web／remote／ai／schedule…），不寫名字。"""
    try:
        kind = kit.caller().kind
    except Exception:
        kind = "unknown"
    return f"synox:{kind or 'unknown'}"


def _connect(home: str):
    """→ (連線, 是不是新建的, 今天)。引擎的 paths.use_home(home) 進 context（今天＝這次呼叫的 now()；檔案資料夾＝這個人自己的
    檔案資料夾，也就是 home 的上一層）→ 引擎的 db.connect()；帳本設定了時差（tz_offset）就帶進 context 給 clock 用。
    context 的還原交給 _close（掛在連線上，不放在模組或 pack 上）。"""
    from contextlib import ExitStack

    from ..engine import db as engine_db, paths as engine_paths
    from ..engine.services import settings as engine_settings

    stack = ExitStack()
    today = now().date()
    files_root = os.path.dirname(os.path.normpath(home))
    try:
        stack.enter_context(engine_paths.use_home(home, today=today, files_root=files_root))
        fresh = not os.path.exists(os.path.join(home, engine_db.DB_NAME))
        conn = engine_db.connect()
        try:
            tz = engine_settings.get(conn, "tz_offset")
            if _hours(tz) is not None:
                today = today_of(tz)
                stack.enter_context(engine_paths.use_home(home, today=today, tz_offset=tz, files_root=files_root))
        except BaseException:
            conn.close()
            raise
    except BaseException:
        stack.close()
        raise
    conn.money_context = stack
    return conn, fresh, today


def _close(conn) -> None:
    """關連線、清掉引擎的 context（照進去的相反順序還原）。"""
    if conn is None:
        return
    stack = getattr(conn, "money_context", None)
    try:
        conn.close()
    finally:
        if stack is not None:
            stack.close()


@contextmanager
def open_book(pack, user, *, screen: bool = False):
    uid = user_id_of(user)
    locks: Optional[Locks] = getattr(pack, "money_locks", None)
    if locks is None:
        raise RuntimeError("money: session.attach(pack) was not called in install()")
    home = pack.path_of(uid, BOOK_DIR)
    os.makedirs(home, exist_ok=True)

    def own_path(*parts: str) -> str:
        return pack.path_of(uid, *parts)

    with locks.of(uid):
        conn, fresh, today = _connect(home)
        try:
            book = Book(conn=conn, actor=actor_of(), today=today, user_id=uid, path=own_path, fresh=fresh,
                        screen=bool(screen), reviewer=writer.provider_of(pack))
            compat.on_open(pack, uid, book)
            yield book
        finally:
            _close(conn)
