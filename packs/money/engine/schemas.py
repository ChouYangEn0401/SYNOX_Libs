"""ingest 的紀錄格式（手寫檢查，不靠外部套件）。驗證與 json_schema() 印出的格式是同一份宣告，不會兩邊不一致。

欄位命名給 agent 看得懂：desc（買了什麼）、account（付款帳戶名稱或別名）、category（細項名稱，可不填）。
不認得的欄位會被指出來（extra_forbidden），不會被默默丟掉。

每個紀錄類別有 model_validate(dict) → 物件（屬性就是欄位）、model_dump(by_alias=…) → dict（全部欄位，照宣告的順序）、
model_json_schema(by_alias=…)。驗證失敗丟 ValidationError（.errors() 是一串 {"type","loc","msg","ctx"}），
translate() 把它轉成 InvalidInput（錯誤訊息的對照表在 MESSAGES）。
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable, Optional

from .errors import InvalidInput

_REQUIRED = object()


class ValidationError(ValueError):
    """一份紀錄有一個或多個欄位不對。errors()：[{"type", "loc", "msg", "ctx"}]，loc 是欄位路徑（tuple）。"""

    def __init__(self, errors: list[dict[str, Any]]):
        super().__init__(f"{len(errors)} validation error(s)")
        self._errors = errors

    def errors(self) -> list[dict[str, Any]]:
        return list(self._errors)


def _amount(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("要是整數")
    if isinstance(value, str):
        text = value.replace(",", "").replace("，", "").strip()
        return int(text) if text.lstrip("-").isdigit() else value
    return value


class F:
    """一個欄位的宣告。kind：str／int／float／bool／literal／list／dict_int／any。"""

    def __init__(self, name: str, kind: str, default: Any = _REQUIRED, *, optional: bool = False, alias: str | None = None,
                 description: str = "", min_length: int | None = None, max_length: int | None = None,
                 ge: float | None = None, le: float | None = None, values: tuple = (), item: Any = None,
                 before: Callable[[Any], Any] | None = None, factory: Callable[[], Any] | None = None):
        self.kind, self.default, self.optional, self.alias = kind, default, optional, alias
        self.description, self.min_length, self.max_length, self.ge, self.le = description, min_length, max_length, ge, le
        self.values, self.item, self.before, self.factory = values, item, before, factory
        self.name = name

    @property
    def required(self) -> bool:
        return self.default is _REQUIRED and self.factory is None

    def make_default(self) -> Any:
        return self.factory() if self.factory is not None else self.default


def _err(errors: list, kind: str, loc: tuple, msg: str, **ctx: Any) -> None:
    errors.append({"type": kind, "loc": loc, "msg": msg, "ctx": ctx})


_TRUE = {"1", "on", "t", "true", "y", "yes"}
_FALSE = {"0", "off", "f", "false", "n", "no"}


def _expected(values: tuple) -> str:
    shown = [repr(v) for v in values]
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " or " + shown[-1]


def _check(f: F, value: Any, loc: tuple, errors: list) -> Any:
    """→ 轉好的值；有錯就記在 errors、回 None。"""
    if f.before is not None:
        try:
            value = f.before(value)
        except ValueError as e:
            _err(errors, "value_error", loc, f"Value error, {e}")
            return None
    if value is None and f.optional:
        return None
    kind = f.kind
    if kind == "any":
        return value
    if kind == "str":
        if not isinstance(value, str):
            _err(errors, "string_type", loc, "Input should be a valid string")
            return None
        if f.min_length is not None and len(value) < f.min_length:
            _err(errors, "string_too_short", loc, f"String should have at least {f.min_length} character", min_length=f.min_length)
            return None
        if f.max_length is not None and len(value) > f.max_length:
            _err(errors, "string_too_long", loc, f"String should have at most {f.max_length} characters", max_length=f.max_length)
            return None
        return value
    if kind == "int":
        out = _int(value, loc, errors)
        return None if out is None else _bounds(f, out, loc, errors)
    if kind == "float":
        if isinstance(value, bool):
            out: Any = float(value)
        elif isinstance(value, (int, float)):
            out = float(value)
        elif isinstance(value, str):
            try:
                out = float(value.strip())
            except ValueError:
                _err(errors, "float_parsing", loc, "Input should be a valid number, unable to parse string as a number")
                return None
        else:
            _err(errors, "float_type", loc, "Input should be a valid number")
            return None
        return _bounds(f, out, loc, errors)
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
            return value.strip().lower() in _TRUE
        _err(errors, "bool_parsing", loc, "Input should be a valid boolean")
        return None
    if kind == "literal":
        if any(value == v and type(value) is type(v) for v in f.values):
            return value
        _err(errors, "literal_error", loc, f"Input should be {_expected(f.values)}", expected=_expected(f.values))
        return None
    if kind == "dict_int":
        if not isinstance(value, dict):
            _err(errors, "dict_type", loc, "Input should be a valid dictionary")
            return None
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):
                _err(errors, "string_type", loc + (k,), "Input should be a valid string")
                continue
            n = _int(v, loc + (k,), errors)
            if n is not None:
                out[k] = n
        return out
    if kind == "list":
        if not isinstance(value, (list, tuple)):
            _err(errors, "list_type", loc, "Input should be a valid list")
            return None
        if f.min_length is not None and len(value) < f.min_length:
            _err(errors, "too_short", loc, f"List should have at least {f.min_length} item after validation", min_length=f.min_length)
            return None
        if f.max_length is not None and len(value) > f.max_length:
            _err(errors, "too_long", loc, f"List should have at most {f.max_length} items after validation", max_length=f.max_length)
            return None
        out = []
        for i, v in enumerate(value):
            if isinstance(f.item, type):
                out.append(f.item.validate_at(v, loc + (i,), errors))
            else:
                check_item = f.item
                out.append(check_item(v, loc + (i,), errors))
        return out
    raise AssertionError(f"unknown field kind {kind}")


def _int(value: Any, loc: tuple, errors: list) -> Optional[int]:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        _err(errors, "int_from_float", loc, "Input should be a valid integer, got a number with a fractional part")
        return None
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                number = None
            if number is not None and number.is_integer():
                return int(number)
            kind = "int_from_float" if number is not None else "int_parsing"
            _err(errors, kind, loc, "Input should be a valid integer, unable to parse string as an integer")
            return None
    _err(errors, "int_type", loc, "Input should be a valid integer")
    return None


def _bounds(f: F, value: Any, loc: tuple, errors: list) -> Any:
    if f.ge is not None and value < f.ge:
        _err(errors, "greater_than_equal", loc, f"Input should be greater than or equal to {f.ge}", ge=f.ge)
        return None
    if f.le is not None and value > f.le:
        _err(errors, "less_than_equal", loc, f"Input should be less than or equal to {f.le}", le=f.le)
        return None
    return value


class _Model:
    """手寫的紀錄格式基底（每個格式是 model() 做出來的 dataclass，SPEC 是欄位宣告）：
    不認得的欄位擋下來；alias 與 Python 名字都收。"""

    SPEC: tuple = ()
    DOC = ""

    @classmethod
    def validate_at(cls, data: Any, loc: tuple, errors: list) -> Any:
        if isinstance(data, _Model):
            data = data.model_dump(by_alias=True)
        if not isinstance(data, dict):
            _err(errors, "model_type", loc, "Input should be a valid dictionary")
            return None
        before = len(errors)
        known: set[str] = set()
        out: dict[str, Any] = {}
        for f in cls.SPEC:
            name = f.name
            key = f.alias or name
            known.update({key, name})
            if key in data:
                raw = data[key]
            elif name in data:
                raw = data[name]
            elif f.required:
                _err(errors, "missing", loc + (key,), "Field required")
                continue
            else:
                out[name] = f.make_default()
                continue
            out[name] = _check(f, raw, loc + (key,), errors)
        for key in data:
            if key not in known:
                _err(errors, "extra_forbidden", loc + (key,), "Extra inputs are not permitted")
        if len(errors) > before:
            return None
        return cls(**out)

    @classmethod
    def model_validate(cls, data: Any):
        errors: list[dict[str, Any]] = []
        obj = cls.validate_at(data, (), errors)
        if errors:
            raise ValidationError(errors)
        return obj

    def model_dump(self, *, by_alias: bool = False) -> dict[str, Any]:
        """全部欄位（照宣告的順序；巢狀的也轉成 dict）。"""
        values = dataclasses.asdict(self)
        if not by_alias:
            return values
        return {(f.alias or f.name): values[f.name] for f in self.SPEC}

    @classmethod
    def model_json_schema(cls, *, by_alias: bool = True) -> dict[str, Any]:
        props: dict[str, Any] = {}
        required: list[str] = []
        for f in cls.SPEC:
            key = (f.alias or f.name) if by_alias else f.name
            props[key] = _field_schema(f, by_alias)
            if f.required:
                required.append(key)
        schema: dict[str, Any] = {"title": cls.__name__, "type": "object", "properties": props,
                                  "additionalProperties": False}
        if cls.DOC:
            schema["description"] = cls.DOC
        if required:
            schema["required"] = required
        return schema


def model(name: str, *spec: F, doc: str = "") -> type:
    """一個紀錄格式：dataclass（屬性就是欄位，可以改）＋ _Model 的驗證與輸出。"""
    fields: list[Any] = []
    for f in spec:
        if f.required:
            fields.append((f.name, Any))
        elif f.factory is not None:
            fields.append((f.name, Any, dataclasses.field(default_factory=f.factory)))
        else:
            fields.append((f.name, Any, dataclasses.field(default=f.default)))
    return dataclasses.make_dataclass(name, fields, bases=(_Model,), kw_only=True,
                                      namespace={"SPEC": tuple(spec), "DOC": doc})


_JSON_TYPES = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}


def _field_schema(f: F, by_alias: bool) -> dict[str, Any]:
    if f.kind in _JSON_TYPES:
        out: dict[str, Any] = {"type": _JSON_TYPES[f.kind]}
    elif f.kind == "literal":
        out = {"enum": list(f.values)}
    elif f.kind == "dict_int":
        out = {"type": "object", "additionalProperties": {"type": "integer"}}
    elif f.kind == "list":
        item = f.item.model_json_schema(by_alias=by_alias) if isinstance(f.item, type) \
            else {"type": "object", "description": "一筆紀錄（看 type 決定格式）"}
        out = {"type": "array", "items": item}
    else:
        out = {}
    if f.kind == "str":
        if f.min_length is not None:
            out["minLength"] = f.min_length
        if f.max_length is not None:
            out["maxLength"] = f.max_length
    if f.kind == "list":
        if f.min_length is not None:
            out["minItems"] = f.min_length
        if f.max_length is not None:
            out["maxItems"] = f.max_length
    if f.ge is not None:
        out["minimum"] = f.ge
    if f.le is not None:
        out["maximum"] = f.le
    if f.optional:
        out = {"anyOf": [out, {"type": "null"}]}
    if f.description:
        out["description"] = f.description
    if not f.required:
        default = f.make_default()
        out["default"] = default
    return out


def S(name: str, default: Any = _REQUIRED, **kw: Any) -> F:
    return F(name, "str", default, **kw)


def OS(name: str, description: str = "", **kw: Any) -> F:
    return F(name, "str", None, optional=True, description=description, **kw)


def I(name: str, default: Any = _REQUIRED, *, amount: bool = False, **kw: Any) -> F:
    return F(name, "int", default, before=_amount if amount else None, **kw)


def OI(name: str, description: str = "", *, amount: bool = False, **kw: Any) -> F:
    return F(name, "int", None, optional=True, description=description, before=_amount if amount else None, **kw)


def OF(name: str, description: str = "", **kw: Any) -> F:
    return F(name, "float", None, optional=True, description=description, **kw)


def B(name: str, default: bool = False, description: str = "") -> F:
    return F(name, "bool", default, description=description)


def L(name: str, *values: str, default: Any = _REQUIRED, optional: bool = False, description: str = "") -> F:
    return F(name, "literal", default, optional=optional, values=values, description=description)


# ---------------------------------------------------------------- 紀錄格式


Item = model(
    "Item",
    S("name", min_length=1, max_length=100, description="品項名稱"),
    OI("amount", "這個品項多少錢（元）", amount=True),
    OF("qty", "數量"),)

ExpenseRecord = model(
    "ExpenseRecord",
    L("type", "expense"),
    I("amount", amount=True, description="元，整數。退款也寫正數，另外加 refund: true"),
    S("date", description="YYYY-MM-DD（西元）"),
    OS("time", "HH:MM；一定要填：使用者說了就用他說的，沒說就用他叫你記的那一刻（他所在地的當地時間）"),
    S("desc", "", max_length=200, description="買了什麼（例：雞腿便當）"),
    OS("category", "細項名稱（例：晚餐）；不填會依內容和時間猜"),
    OS("account", "付款帳戶名稱或別名；不填用預設帳戶"),
    S("merchant", "", max_length=100, description="店家"),
    S("note", "", max_length=1000, description="備註"),
    F("items", "list", item=Item, factory=list, description="收據上的品項"),
    OF("confidence", "你對這筆判讀的把握（0–1）；低於 0.6 會進待確認", ge=0, le=1),
    B("refund", False, "退款：會存成負的支出"),
    B("financed", False, "分期或借錢買的（不算進當期的薪水分配）"),
    OS("currency", "刷卡外幣幣別，例 USD"),
    OS("foreign_amount", "外幣金額，例 \"12.99\""),
    OS("attach", "收據圖檔的路徑（會複製保存）"),
    L("nature", "stock", default=None, optional=True,
               description="長期用（囤貨、3C、一年份的東西）：算在生活費總額，但不算在每天的日曆。"
                           "平常不要填；固定的項目（房租、訂閱）本來就不算在每天，也不用填"),
    OS("for_period", "算進哪一期（預繳／補繳），例 2030-02；不填就照付款日。"
                    "desc 寫「2月房租」之類程式也會自己對（生活費不行）"),)

IncomeRecord = model(
    "IncomeRecord",
    L("type", "income"),
    I("amount", amount=True, description="元；有 deductions 時是「總額」（扣繳前）"),
    S("date"),
    OS("time"),
    S("desc", "", max_length=200),
    OS("category", "薪資、兼職、投資收益、賣東西、退款回饋、其他收入；不填會猜"),
    OS("account", "入帳帳戶；不填用預設帳戶"),
    S("note", "", max_length=1000),
    F("deductions", "dict_int", factory=dict,
                   description="薪水扣繳，例 {\"勞保\":1000,\"健保\":600,\"所得稅\":800,\"勞退自提\":800}"),
    OF("confidence", ge=0, le=1),)

TransferRecord = model(
    "TransferRecord",
    L("type", "transfer"),
    I("amount", amount=True, description="元，正數"),
    S("date"),
    OS("time"),
    S("from_", alias="from", description="轉出帳戶"),
    S("to", description="轉入帳戶"),
    L("purpose", "card_payment", "saving", "investing", "prepay", "debt", "lend", "collect", "repay", "pension", "other",
                default=None, optional=True,
                description="不填會依帳戶判斷：轉進信用卡＝card_payment、轉進應收＝lend；prepay＝預付（未來要用的錢先付掉或先放到別處）"),
    OS("category", "例：繳卡費、緊急預備金、股票、借出"),
    S("desc", "", max_length=200),
    OS("counterparty", "借出／收回的對象"),
    S("note", "", max_length=1000),
    OF("confidence", ge=0, le=1),
    OS("for_period", "算進哪一期（預繳／補繳），例 2030-02；不填就照付款日"),)

DayNoteRecord = model(
    "DayNoteRecord",
    L("type", "day_note"),
    S("date"),
    S("note", min_length=1, max_length=1000, description="小日記"),
    B("replace", False, "true＝整段換掉；預設接在原本的後面"),)

StatementLine = model(
    "StatementLine",
    S("date", description="消費日 YYYY-MM-DD"),
    S("desc", min_length=1, max_length=200, description="帳單上寫的（店家）"),
    I("amount", amount=True, description="元；退款、繳款是負數"),
    OS("post_date", "入帳日"),
    L("type", "purchase", "installment", "fee", "refund", "payment", default=None, optional=True,
             description="不填會自動判斷：負數＝退款或繳款、「3/12期」＝分期、年費利息手續費＝手續費"),
    OS("installment", "分期第幾期，例 \"11/12\""),
    OS("currency", "外幣幣別"),
    OS("foreign_amount", "外幣金額"),)

CardStatementRecord = model(
    "CardStatementRecord",
    L("type", "card_statement"),
    S("card", description="信用卡帳戶名稱或別名"),
    S("closing_date", description="結帳日 YYYY-MM-DD"),
    I("total", amount=True, description="本期應繳總額（元）"),
    OS("due_date", "繳款截止日"),
    OS("period_start", "帳單期間起日；不填＝上個月結帳日的隔天"),
    OI("min_payment", "最低應繳"),
    F("lines", "list", item=StatementLine, min_length=1, max_length=400, description="帳單明細，一列一筆"),)

InstallmentPurchaseRecord = model(
    "InstallmentPurchaseRecord",
    L("type", "installment_purchase"),
    S("name", min_length=1, max_length=100, description="買了什麼（例：手機）"),
    S("card", description="刷哪張信用卡"),
    I("total", amount=True, description="總價（元）"),
    I("periods", ge=2, le=60, description="分幾期"),
    S("date", description="購買日 YYYY-MM-DD"),
    OI("per_period", "每期金額；不填＝總價÷期數（零頭放第一期）"),
    OS("first_due", "第一期出現在帳單上的日期；不填＝購買日"),
    OS("category", "細項（例：大額採買、進修）；不填會猜"),
    L("nature", "positive", "negative", "wear", default="negative",
               description="positive 進修／投資自己、negative 消費、wear 生活必要"),
    S("note", "", max_length=1000),)

BalanceRecord = model(
    "BalanceRecord",
    L("type", "balance"),
    S("account", description="帳戶名稱或別名"),
    I("balance", amount=True, description="那天結束時的餘額（元）；信用卡是負的"),
    OS("as_of", "哪一天 YYYY-MM-DD；不填＝今天"),
    S("note", "", max_length=200),)

BillRecord = model(
    "BillRecord",
    L("type", "bill"),
    S("bill", description="電費／水費／瓦斯／手機費／房租…"),
    I("amount", amount=True, description="應繳金額（元）"),
    OS("period_end", "計費期間迄日 YYYY-MM-DD（帳單上的「計費期間」後面那天）"),
    OS("period_start", "計費期間起日"),
    OF("usage", "用量（度）"),
    OS("unit", "用量單位，預設「度」"),
    OS("due_date", "繳費期限"),
    B("paid", False, "已經繳了：會同時記一筆支出"),
    OS("paid_date", "繳費日（給了就當作已繳）"),
    OS("account", "從哪個帳戶繳（預設帳戶）"),
    S("note", "", max_length=200),)

InvestPnlRecord = model(
    "InvestPnlRecord",
    L("type", "invest_pnl"),
    S("month", description="YYYY-MM"),
    I("pnl", amount=True, description="這個月的損益（元，賠了寫負的）"),
    OS("account", "投資帳戶（預設第一個證券帳戶）"),
    OI("market_value", "月底市值（給了會當成核對餘額）"),
    OI("net_deposit", "這個月淨入金（元）"),
    S("note", "", max_length=300, description="心得"),)

Holding = model(
    "Holding",
    S("name", min_length=1, max_length=60, description="標的名稱"),
    I("market_value", amount=True, description="市值（元）"),
    OS("bucket", "長單股票／短單股票／定存型股票／彈性本金…"),
    OS("symbol", "代號，例 TEST1"),
    OI("cost", "成本（元）"),)

HoldingsRecord = model(
    "HoldingsRecord",
    L("type", "holdings"),
    F("items", "list", item=Holding, min_length=1, max_length=100),
    OS("as_of", "哪一天 YYYY-MM-DD；不填＝今天"),
    OS("account", "投資帳戶"),)

TradeRecord = model(
    "TradeRecord",
    L("type", "trade"),
    S("date", description="成交日 YYYY-MM-DD"),
    L("action", "buy", "sell", "dividend", "fee", "other", description="買進／賣出／股利／費用／其他"),
    OS("symbol", "代號，例 TEST1", max_length=20),
    OS("name", "名稱，例 範例ETF", max_length=60),
    OI("shares", "股數（零股照填）", ge=0),
    OF("price", "成交單價"),
    OI("gross", "價金（不填就用股數×單價算）"),
    OI("fee", "手續費"),
    OI("tax", "交易稅"),
    OI("net", "淨收付（正＝進帳、負＝出帳）；不填就自己算"),
    OS("account", "證券帳戶（預設第一個證券帳戶）；撲滿的投資／操盤用 證券-投資、證券-操盤"),
    S("note", "", max_length=300),
    OS("book", "記進哪一倉（名字或編號）；不填：投資＝那一年的倉、操盤＝最新開著的倉", max_length=30),
    OS("time", "成交時間 HH:MM（成交回報上有就填；當沖一定要）"),
    B("day_trade", False, "當沖（同一天買賣同一檔）"),
    OF("stop_price", "停損價（下單時設的）"),
    doc="對帳單上的一列（股票、ETF）。證券帳戶裡面的事，不會重複算到錢包。",)

IOU_DIRECTIONS = {"lend": "你幫別人先付（別人欠你）", "owe": "別人幫你先付（你欠別人）",
                  "collect": "對方還你", "repay": "你還對方"}


IouRecord = model(
    "IouRecord",
    L("type", "iou"),
    OS("direction", "lend＝你幫別人先付（別人欠你）、owe＝別人幫你先付（你欠別人）、"
                   "collect＝對方還你、repay＝你還對方。一定要寫；聽不出方向就先問使用者"),
    OS("who", "對方的名字（例：小明）；一定要寫，前後要寫得一樣才對得上", max_length=50),
    I("amount", amount=True, description="元，正數"),
    S("date"),
    OS("time"),
    S("desc", "", max_length=200, description="買了什麼（例：午餐、電影票）；owe 會拿來猜類別"),
    OS("category", "只有 owe：那筆東西的細項（例：午餐）；不填會猜"),
    OS("account", "lend／repay＝從哪個帳戶付（lend 可以是信用卡）；collect＝收進哪個帳戶；"
                 "owe 不用寫。不填用預設帳戶"),
    S("note", "", max_length=1000),
    doc="代墊與欠款：四個方向一個格式。direction 一定要寫，沒寫或寫錯整批擋下來，不會猜。",)

RECORD_TYPES: dict[str, type] = {
    "expense": ExpenseRecord, "income": IncomeRecord, "transfer": TransferRecord, "day_note": DayNoteRecord,
    "card_statement": CardStatementRecord, "installment_purchase": InstallmentPurchaseRecord,
    "balance": BalanceRecord, "bill": BillRecord, "invest_pnl": InvestPnlRecord, "holdings": HoldingsRecord,
    "trade": TradeRecord, "iou": IouRecord,
}



def _record(value: Any, loc: tuple, errors: list) -> Any:
    """一筆紀錄：看 type 決定格式（discriminated union）。"""
    if not isinstance(value, dict):
        _err(errors, "model_type", loc, "Input should be a valid dictionary")
        return None
    tag = value.get("type")
    if "type" not in value:
        _err(errors, "union_tag_not_found", loc, "Unable to extract tag using discriminator 'type'")
        return None
    kind = RECORD_TYPES.get(tag) if isinstance(tag, str) else None
    if kind is None:
        _err(errors, "union_tag_invalid", loc, f"Input tag {tag!r} found using 'type' does not match any of the expected tags")
        return None
    return kind.validate_at(value, loc + (tag,), errors)


IngestPayload = model(
    "IngestPayload",
    OS("source", "telegram、web、cli…；不填照呼叫的人"),
    OS("source_ref", "同一則訊息永遠給同一個值（例 chat:<對話>:<訊息>），重送才不會重複", max_length=200),
    F("records", "list", item=_record, min_length=1, max_length=200),
)


def json_schema(record_type: str | None = None) -> dict[str, Any]:
    if record_type:
        kind = RECORD_TYPES.get(record_type)
        if kind is None:
            raise InvalidInput(f"沒有「{record_type}」這種紀錄", hint="可以是：" + "、".join(RECORD_TYPES))
        return kind.model_json_schema(by_alias=True)
    schema = IngestPayload.model_json_schema(by_alias=True)
    schema["properties"]["records"]["items"] = {"oneOf": [m.model_json_schema(by_alias=True) for m in RECORD_TYPES.values()]}
    return schema


MESSAGES = {
    "missing": "缺少（必填）", "int_parsing": "要是整數", "int_from_float": "要是整數（不能有小數）",
    "int_type": "要是整數", "extra_forbidden": "不認得這個欄位", "union_tag_not_found": "缺少 type",
    "union_tag_invalid": "type 不對（可以是 " + "、".join(RECORD_TYPES) + "）", "string_too_long": "太長",
    "string_too_short": "不能是空的", "too_long": "太多筆（一次最多 200 筆）", "too_short": "至少要一筆",
    "float_parsing": "要是數字", "greater_than_equal": "太小", "less_than_equal": "太大",
    "bool_parsing": "要是 true 或 false", "literal_error": "值不對", "string_type": "要是文字",
    "list_type": "要是清單", "dict_type": "要是物件", "model_type": "要是物件", "value_error": "值不對",
    "float_type": "要是數字",
}


def _loc(loc: tuple[Any, ...]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part in RECORD_TYPES:                      # 一筆紀錄的格式會多一層 type 名稱
            continue
        else:
            out += ("." if out else "") + ("from" if part == "from_" else str(part))
    return out or "(整份)"


def translate(err: ValidationError, *, prefix: str = "") -> InvalidInput:
    fields: dict[str, str] = {}
    for e in err.errors():
        msg = MESSAGES.get(e["type"], e["msg"])
        if e["type"] == "literal_error" and e.get("ctx", {}).get("expected"):
            msg = f"值不對，可以是 {e['ctx']['expected']}"
        fields[prefix + _loc(e["loc"])] = msg
    first = next(iter(fields.items()))
    return InvalidInput(f"內容有 {len(fields)} 個問題（{first[0]}：{first[1]}），整批都沒有寫入",
                        hint="照 fields 改好再送；格式用 json_schema() 看", fields=fields)
