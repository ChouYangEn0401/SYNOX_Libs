"""指令表的格式（契約，見 CONTRACT.md「指令表」）：每個領域檔（ledger、home、plan、setup、flow、review、piggy、compat）匯出

    DOMAIN     = "ledger"
    COMMANDS   = (Cmd(...), ...)                              指令；app=True 的同時是畫面的動作（同名、同危險程度）
    READS      = frozen({"home": fn(book, params) -> dict})   畫面的讀
    EXPORTS    = frozen({"book": fn(book, params) -> kit.Download})
    WEB_ROUTES = frozen({"summary": fn(book) -> dict})        工具頁小面板（pack.json web.api），只有 compat 有

表一律唯讀（tuple、frozen()）：模組層級不准有會變的 dict／list（隔離規則 4），這四個表也不例外。

run(book, values) -> Result(data, text, warnings)：同一個 run 同時給指令（回 text）與畫面動作（回 data）。
book 由 bridge/session.py 的 open_book 給；values／params 一律是「欄位名 → 字串」。

這個檔只放格式與共用的小工具，不開資料庫、不碰 pack；模組層級不放任何會變的東西（隔離規則 4）。
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from isdtelekit.core.registry import DANGER, READ, WRITE, Field  # noqa: F401  （領域檔從這裡拿）

from synox.packs import kit

TODO_TEXT = "這個功能還在做（記帳 0.4.0 正在重做），先用別的方式記下來。"
DOMAINS = ("ledger", "home", "plan", "setup", "flow", "review", "piggy", "compat", "statements")
CLEAR = "-"                      # 欄位給「-」＝清掉（跟 0.3.1 一樣）
STATUS = MappingProxyType({"invalid": 400, "not_found": 404, "conflict": 409})
MAX_DECIMALS = 3                 # 帳本的小數位數只收 0～3（設定頁的選單也是這幾個）
MAX_AMOUNT = 10 ** 12            # 最小單位；再大就當打錯
DAY_WORDS = (("今天", 0), ("昨天", 1), ("前天", 2))
DAY_RE = re.compile(r"(?:(\d{4})[-/.])?(\d{1,2})[-/.](\d{1,2})")


def frozen(table: Dict[str, Callable]) -> "MappingProxyType":
    """領域檔的 READS／EXPORTS／WEB_ROUTES 用：唯讀的 dict。"""
    return MappingProxyType(dict(table))


@dataclass(frozen=True)
class Cmd:
    """一個指令（app=True 時也是畫面的同名動作）。

    name       小寫英數字與 _；指令是 /money_<name>
    summary    一句話（/help、AI 的工具說明、畫面上的表單標題）
    effect     READ／WRITE／DANGER（畫面動作的危險程度跟它一樣；pack.json web.app.actions 要寫同一個）
    fields     isdtelekit 的 Field 清單（最多 20 個；畫面送來的欄位名一定要在這裡，不然 SYNOX 直接擋）
    run        run(book, values) -> Result
    ai         False＝不給 AI；{"scenario": "一句話"}＝給 AI（情境一句，120 字內、一行）
    positional 打字時照位置填幾個欄位；None＝必填欄位的個數
    app        True＝也是畫面的動作（名字要寫在 pack.json web.app.actions）
    default    True＝群組的預設指令（/money 120 午餐）；全工具只有一個
    describe   危險動作確認框的那一句：describe(book, values) -> str
    """
    name: str
    summary: str
    effect: str
    fields: Sequence[Field]
    run: Callable
    ai: Union[bool, Dict[str, str]] = False
    positional: Optional[int] = None
    app: bool = False
    default: bool = False
    describe: Optional[Callable] = None


@dataclass
class Result:
    """run() 的回傳。data＝畫面拿到的那包（寫入的要帶 batch_id、write_seq）；text＝指令回給人的字；
    warnings＝要提醒的幾句（指令接在 text 後面；畫面放在 data["warnings"]）。"""
    data: Dict[str, Any] = field(default_factory=dict)
    text: str = ""
    warnings: List[str] = field(default_factory=list)


def todo(name: str) -> Callable:
    """還沒做的指令／動作：一律丟 kit.AppError（指令回「❌ 這個功能還在做」、畫面跳同一句）。"""
    def run(book, values):
        raise kit.AppError(TODO_TEXT, code="todo", status=501)
    run.todo = name
    return run


def todo_read(name: str) -> Callable:
    def read(book, params):
        raise kit.AppError(TODO_TEXT, code="todo", status=501)
    read.todo = name
    return read


def todo_export(name: str) -> Callable:
    def export(book, params):
        raise kit.AppError(TODO_TEXT, code="todo", status=501)
    export.todo = name
    return export


def positional_of(cmd: Cmd) -> int:
    if cmd.positional is not None:
        return max(0, min(int(cmd.positional), len(cmd.fields)))
    return sum(1 for f in cmd.fields if f.required)


@dataclass
class Tables:
    commands: List[Cmd]
    reads: Dict[str, Callable]
    exports: Dict[str, Callable]
    web_routes: Dict[str, Callable]


def collect(modules: Sequence[Any]) -> Tables:
    """把 8 個領域檔的表收成一份；同一個名字出現兩次＝契約錯了，直接丟 ValueError（載入就失敗，不會悄悄蓋掉）。"""
    commands: List[Cmd] = []
    reads: Dict[str, Callable] = {}
    exports: Dict[str, Callable] = {}
    routes: Dict[str, Callable] = {}
    seen: Dict[str, str] = {}
    defaults = []
    for module in modules:
        where = module.DOMAIN
        for cmd in module.COMMANDS:
            if cmd.name in seen:
                raise ValueError(f"money: command {cmd.name!r} declared twice ({seen[cmd.name]}, {where})")
            seen[cmd.name] = where
            if len(cmd.fields) > 20:
                raise ValueError(f"money: command {cmd.name!r} has more than 20 fields")
            if cmd.default:
                defaults.append(cmd.name)
            commands.append(cmd)
        for kind, target, table in (("read", reads, module.READS), ("export", exports, module.EXPORTS),
                                    ("web route", routes, getattr(module, "WEB_ROUTES", None) or {})):
            for name, fn in table.items():
                if name in target:
                    raise ValueError(f"money: {kind} {name!r} declared twice")
                target[name] = fn
    if len(defaults) > 1:
        raise ValueError(f"money: more than one default command: {defaults}")
    return Tables(commands, reads, exports, routes)


# ── 錯誤怎麼轉（契約，見 CONTRACT.md「錯誤」）────────────────────────────────────────────

def _engine_error_class():
    """engine/errors.py 的 LedgerError；引擎還沒搬進來（W2 之前）回 None。"""
    try:
        from ..engine.errors import LedgerError
    except ModuleNotFoundError as e:
        missing = (e.name or "").rsplit(".", 1)[-1]
        if missing in ("engine", "errors"):
            return None
        raise
    return LedgerError


def engine_message(e: Exception) -> str:
    """引擎的錯 → 一句給人看的字：訊息＋「是不是：…」＋下一步（畫面原本的 errorText 也是這樣排）。"""
    parts = [str(getattr(e, "message", "") or "出了點問題")]
    suggestions = [str(s) for s in (getattr(e, "suggestions", None) or [])]
    if suggestions:
        parts.append("是不是：" + "、".join(suggestions))
    hint = str(getattr(e, "hint", "") or "")
    if hint:
        parts.append(hint)
    return "　".join(parts)


def as_app_error(e: Exception) -> Optional[kit.AppError]:
    """kit.AppError 原樣；引擎的錯轉成 kit.AppError（code 沿用、狀態碼照種類）；其他回 None（交給 SYNOX：訊息不外流）。"""
    if isinstance(e, kit.AppError):
        return e
    cls = _engine_error_class()
    if cls is not None and isinstance(e, cls):
        code = str(getattr(e, "code", "") or "refused")
        return kit.AppError(engine_message(e), code=code, status=STATUS.get(code, 409))
    return None


# ── 回覆怎麼排 ───────────────────────────────────────────────────────────────────

def reply_text(result: Result) -> str:
    """指令的回覆：text＋每一句提醒一行（⚠️ 開頭）。"""
    lines = [result.text or "✅ 好了"]
    lines += [f"⚠️ {w}" for w in result.warnings or []]
    return "\n".join(lines)


def action_data(result: Result) -> dict:
    """畫面動作的回傳：data（一定是 dict）；有提醒而 data 沒有 warnings 鍵時補上。"""
    data = dict(result.data or {})
    if result.warnings and "warnings" not in data:
        data["warnings"] = list(result.warnings)
    return data


# ── 值怎麼解讀：金額與日期（全工具共用一套；CONTRACT.md「金額與小數位數」）──────────────────────────
#
# 金額在帳本裡一律是最小單位的整數（小數位數是帳本設定 currency.decimals，0～3）。
# - 人打字（聊天指令、SYNOX 網頁主控台打字、AI、排程）：照小數位數換算——小數位數 2 時「99.5」→ 9950。
# - 畫面送來（完整畫面的動作、讀、匯出；手機入口也是）：畫面已經換成最小單位，這裡只收整數、不再換算。
# 分辨靠 book.screen：pack.py 的畫面那一層開帳本時帶 screen=True，指令那一層一律是 False
# （不看 caller 的種類：網頁主控台打字的 caller 也是 web，但它是人打的字）。

def _settings():
    from ..engine.services import settings
    return settings


def _invalid(message: str, field: str, why: str, hint: str = ""):
    from ..engine.errors import InvalidInput
    return InvalidInput(message, hint=hint or None, fields={field: why})


def decimals(conn) -> int:
    """帳本的小數位數（0～3；壞掉的值當 0）。"""
    try:
        return max(0, min(MAX_DECIMALS, int(_settings().value(conn, "currency.decimals") or 0)))
    except (TypeError, ValueError):
        return 0


def typed(book) -> bool:
    """這一次的值是人打的字嗎？（畫面送來的是 False）"""
    return not getattr(book, "screen", False)


def to_minor(book, raw: Any, label: str = "金額", *, field: str = "amount", zero: bool = False,
             negative: bool = False) -> int:
    """金額字串 → 最小單位的整數。人打字照帳本的小數位數換算（可以有千分位、「元」、幣別符號）；畫面送來的已經是最小單位。
    zero：0 可以；negative：負的可以（退款、信用卡餘額）。看不懂、小數太多、太大都丟 InvalidInput（欄位名是 field）。"""
    if isinstance(raw, bool):
        raise _invalid(f"{label}要是數字", field, "不是數字", "只寫數字，例如 120")
    shown = "" if raw is None else str(raw).strip()
    t = shown.replace(",", "").replace("，", "").replace("元", "").replace(" ", "")
    symbol = str(_settings().value(book.conn, "currency.symbol") or "")
    if symbol and not symbol[-1:].isdigit():
        t = t.replace(symbol, "")
    t = t.replace("$", "")
    try:
        number = Decimal(t)
    except (InvalidOperation, ValueError):
        number = None
    if number is None or not number.is_finite() or "e" in t.lower():
        raise _invalid(f"{label}「{shown}」不是數字", field, "不是數字", "只寫數字，例如 120")
    places = decimals(book.conn)
    human = typed(book)
    if human:
        number = number * (Decimal(10) ** places)
    if number != number.to_integral_value():
        hint = (f"這本帳最多 {places} 位小數（帳本的小數位數是 {places}）" if human
                else f"這本帳是 {places} 位小數，畫面送來的金額要是最小單位的整數")
        raise _invalid(f"{label}「{shown}」的小數太多", field, "小數太多", hint)
    n = int(number)
    if n < 0 and not negative:
        raise _invalid(f"{label}不能是負的", field, "是負的")
    if n == 0 and not zero:
        raise _invalid(f"{label}不能是 0", field, "是 0")
    if abs(n) > MAX_AMOUNT:
        raise _invalid(f"{label}太大了", field, "太大")
    return n


def amount_of(book, values, name: str = "amount", label: str = "金額", *, required: bool = False,
              **rules) -> Optional[int]:
    """values[name] 的金額（規則同 to_minor）；沒給或空白回 None（required 時丟錯）。"""
    raw = str((values or {}).get(name) or "").strip()
    if not raw:
        if required:
            raise _invalid(f"要給{label}", name, "必填")
        return None
    return to_minor(book, raw, label, field=name, **rules)


def show(conn, minor: Any, *, symbol: bool = False, trim: bool = False, none: str = "—") -> str:
    """最小單位的整數 → 給人看的金額（千分位；照帳本的小數位數）。symbol：前面加幣別符號；
    trim：整數金額不印小數（舊指令的回覆用）；None 回 none。小數位數 0 時跟以前完全一樣（120 → 「120」）。"""
    if minor is None:
        return none
    minor = int(minor)
    places = decimals(conn)
    if not places:
        text = f"{minor:,}"
    elif trim and minor % (10 ** places) == 0:
        text = f"{minor // (10 ** places):,}"
    else:
        text = f"{Decimal(minor) / (Decimal(10) ** places):,.{places}f}"
    return f"{_settings().value(conn, 'currency.symbol') or ''}{text}" if symbol else text


def parse_day(value: Any, today: date, label: str = "日期", *, field: str = "date") -> str:
    """「2030-01-15」「2030/1/15」「1/15」「01-15」「今天」「昨天」「前天」→ YYYY-MM-DD；只有月日的算今年。
    看不懂或日期不存在丟 InvalidInput。"""
    text = str(value or "").strip()
    for word, back in DAY_WORDS:
        if text == word:
            return (today - timedelta(days=back)).isoformat()
    m = DAY_RE.fullmatch(text)
    if m:
        try:
            return date(int(m.group(1) or today.year), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    raise _invalid(f"{label}「{text}」看不懂", field, "看不懂", "例如 2030-01-15、1/15、昨天")


def day_or_none(values, name: str, today: date, label: str = "日期") -> Optional[str]:
    """values[name] 的日期；沒給或空白回 None。"""
    text = str((values or {}).get(name) or "").strip()
    return parse_day(text, today, label, field=name) if text else None
