"""格式描述檔（JSON）：一份對帳單長什麼樣子——欄位叫什麼、表頭有哪幾欄、日期怎麼寫、每一種交易的字、契約的乘數……

程式裡**不寫任何券商的欄位名、表頭、契約、費率、日期格式**：這些全在描述檔。讀對帳單的程式（adapter，見 __init__.py 的
ADAPTERS）只認「邏輯欄位」（date、symbol、shares…），照描述檔的 columns 對到那份檔真正的表頭。

共同的欄位（每一種都要有）：

    schema      1（這份格式的版本）
    id          小寫英數字與 _（1～40 字）；指令的 format= 寫它
    kind        哪一種 adapter：trades（成交明細）、futures（期貨選擇權日報表）、realized（已實現損益）
    title       給人看的名字
    source      html（網頁存下來的表格）或 csv
    encoding    選填，預設 utf-8
    header      找表用：表頭一定有的那幾欄（也拿來自動認格式）
    columns     邏輯欄位 → 表頭的字；adapter 要的欄位（REQUIRED）一定要有，而且要列在 header 裡
    date_formats 選填，日期的寫法（strptime 的格式），預設 ["%Y/%m/%d", "%Y-%m-%d"]
    source_tag  選填，記進帳本時的來源標記（重跑不會多記就靠它＋每一列的編號），預設＝"statement:" + id

各 adapter 自己多要的欄位寫在它的 check() 與說明裡。描述檔有錯時不會猜：列出每一個問題，那份描述檔不能用。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ...errors import InvalidInput

SCHEMA = 1
SOURCES = ("html", "csv")
ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
DEFAULT_DATES = ("%Y/%m/%d", "%Y-%m-%d")
MAX_PATTERN = 300
MAX_HEADER = 40


@dataclass(frozen=True)
class Format:
    """讀好、檢查過的一份描述檔。raw＝原本的 JSON（adapter 讀自己多要的欄位）；origin＝builtin／user。"""
    id: str
    kind: str
    title: str
    source: str
    encoding: str
    header: tuple
    columns: dict = field(hash=False)
    date_formats: tuple
    source_tag: str
    raw: dict = field(hash=False, repr=False)
    origin: str
    file: str


def strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def parse(text: str, *, origin: str, file: str, adapters) -> tuple[Format | None, list[str]]:
    """描述檔的字 → (Format, [])；有問題 → (None, [每一個問題])。"""
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None, ["不是正確的 JSON"]
    if not isinstance(raw, dict):
        return None, ["最外層要是一個物件 {…}"]
    problems: list[str] = []
    if raw.get("schema") != SCHEMA:
        problems.append(f"schema 要寫 {SCHEMA}")
    fid = raw.get("id")
    if not isinstance(fid, str) or not ID_RE.match(fid):
        problems.append("id 要是小寫英數字與 _（1～40 字）")
    kind = raw.get("kind")
    adapter = adapters.get(kind) if isinstance(kind, str) else None
    if adapter is None:
        problems.append("kind 要是其中一種：" + "、".join(adapters))
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 80:
        problems.append("title 要寫（80 字以內）")
    source = raw.get("source")
    if source not in SOURCES:
        problems.append("source 要是 html 或 csv")
    encoding = raw.get("encoding", "utf-8")
    if not isinstance(encoding, str) or not re.fullmatch(r"[A-Za-z0-9_-]{2,20}", encoding):
        problems.append("encoding 寫錯了（例：utf-8、big5）")
    else:
        try:
            "".encode(encoding)
        except LookupError:
            problems.append(f"不認得的 encoding「{encoding}」")
    header = raw.get("header")
    if not strings(header) or not header or len(header) > MAX_HEADER:
        problems.append(f"header 要是表頭欄名的清單（1～{MAX_HEADER} 個）")
        header = []
    columns = raw.get("columns")
    if not isinstance(columns, dict) or not all(isinstance(k, str) and isinstance(v, str) and v.strip()
                                                for k, v in columns.items()):
        problems.append("columns 要是「邏輯欄位 → 表頭的字」的物件")
        columns = {}
    dates = raw.get("date_formats", list(DEFAULT_DATES))
    if not strings(dates) or not dates:
        problems.append("date_formats 要是日期格式的清單（例：[\"%Y/%m/%d\"]）")
        dates = list(DEFAULT_DATES)
    tag = raw.get("source_tag", f"statement:{fid}")
    if not isinstance(tag, str) or not re.fullmatch(r"[a-z0-9_:.-]{1,40}", tag):
        problems.append("source_tag 要是小寫英數字與 _ : . -（1～40 字）")
    if adapter is not None:
        keys = [h.replace(" ", "") for h in header]
        for name in adapter.REQUIRED:
            if name not in columns:
                problems.append(f"columns 少了 {name}（{adapter.TITLE}一定要有）")
            elif columns[name].replace(" ", "") not in keys:
                problems.append(f"columns.{name}「{columns[name]}」要列在 header 裡")
        unknown = sorted(set(columns) - set(adapter.REQUIRED) - set(adapter.OPTIONAL))
        if unknown:
            problems.append("columns 有不認得的邏輯欄位：" + "、".join(unknown)
                            + "（認得的：" + "、".join((*adapter.REQUIRED, *adapter.OPTIONAL)) + "）")
        problems += adapter.check(raw)
    if problems:
        return None, problems
    return Format(id=fid, kind=kind, title=title.strip(), source=source, encoding=encoding,
                  header=tuple(h.replace(" ", "") for h in header),
                  columns={k: v.replace(" ", "") for k, v in columns.items()}, date_formats=tuple(dates),
                  source_tag=tag, raw=raw, origin=origin, file=file), []


# ── adapter 共用的小工具（照描述檔解讀一格）──────────────────────────────────────────

def pattern_problem(value: Any, what: str, groups: tuple = ()) -> list[str]:
    """描述檔裡的正規表示式：要編得過、不能太長、要有那幾個具名群組。"""
    if not isinstance(value, str) or not value or len(value) > MAX_PATTERN:
        return [f"{what} 要是正規表示式（{MAX_PATTERN} 字以內）"]
    try:
        rx = re.compile(value)
    except re.error as e:
        return [f"{what} 編不過：{e}"]
    missing = [g for g in groups if g not in rx.groupindex]
    return [f"{what} 少了具名群組 (?P<{g}>…)" for g in missing]


def day(fmt: Format, text: str, where: str) -> str:
    """一格日期 → YYYY-MM-DD（照描述檔的 date_formats 一個一個試）。"""
    t = (text or "").strip()
    for f in fmt.date_formats:
        try:
            return datetime.strptime(t, f).date().isoformat()
        except ValueError:
            continue
    raise InvalidInput(f"{where}：日期「{t}」看不懂", hint="描述檔的 date_formats 要能讀這種寫法")


def num(text: str, where: str) -> float:
    """一格數字（千分位、%、空的、「-」都收）；看不懂就說是哪一格。"""
    from .tables import number
    try:
        return number(text)
    except ValueError:
        raise InvalidInput(f"{where}：「{text}」不是數字") from None


def whole(text: str, where: str) -> int:
    return int(round(num(text, where)))


def price(text: str, where: str) -> str:
    """價格照寫的（拿掉千分位）；要是數字。"""
    t = (text or "").replace(",", "").strip()
    num(t, where)
    return t or "0"


def cell_of(fmt: Format, head: list[str], row: list[str]):
    """→ cell(邏輯欄位) 這個函式：照 columns 找到那一欄；檔裡沒有那一欄（選填的）回空字串。"""
    index = {name: head.index(col) for name, col in fmt.columns.items() if col in head}

    def cell(name: str) -> str:
        i = index.get(name)
        return row[i] if i is not None and i < len(row) else ""
    return cell


def product(fmt: Format, text: str, where: str) -> tuple[str, str]:
    """「名字(代號)」這種格子 → (代號, 名字)；規則在描述檔的 product.pattern（具名群組 code、name）。"""
    spec = fmt.raw.get("product") or {}
    t = (text or "").strip()
    if spec.get("remove_spaces", True):
        t = t.replace(" ", "")
    m = re.match(spec["pattern"], t)
    if not m:
        raise InvalidInput(f"{where}：商品「{text}」看不懂", hint=str(spec.get("example") or "描述檔的 product.pattern 要能讀這種寫法"))
    return m.group("code"), m.group("name").strip()


def product_problems(raw: dict) -> list[str]:
    spec = raw.get("product")
    if not isinstance(spec, dict):
        return ["product 要寫：{\"pattern\": 有 (?P<code>…) 和 (?P<name>…) 的正規表示式}"]
    return pattern_problem(spec.get("pattern"), "product.pattern", ("code", "name"))
