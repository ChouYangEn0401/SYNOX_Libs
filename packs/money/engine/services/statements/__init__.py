"""對帳單匯入：**一個通用的讀取器＋格式描述檔＋adapter 登記表**（程式裡不寫任何券商的格式）。

    格式描述檔（JSON）  一份對帳單長什麼樣子：表頭、欄位對照、日期寫法、交易種類的字、契約乘數、稅率……
                        內建的放在工具裡的 engine/formats/；使用者自己的放在自己的檔案資料夾的 formats/（同一個 id 用自己的）
    adapter            一種「對帳單內容」怎麼變成帳本裡的交易：trades（成交明細）、futures（期貨選擇權日報表）、
                        realized（已實現損益）。每個 adapter 是這個資料夾裡的一個模組，登記在 ADAPTERS
    讀取器              tables.py（html 表格、CSV）、files.py（唯一碰檔案的地方，只讀）

別家券商：格式一樣是這三種之一 → 只要加一份描述檔（不用改程式）；完全不一樣的內容 → 加一個 adapter 模組、登記進 ADAPTERS。

adapter 的介面（模組層級）：
    KIND、TITLE                     名字
    REQUIRED、OPTIONAL              描述檔 columns 要有／可以有的邏輯欄位
    OPTIONS                         run 收的選項（account、book、close）
    check(raw) -> [問題]             描述檔裡這一種多要的欄位對不對
    read(fmt, 表頭, 列, 檔名) -> data  照描述檔把表讀成一列列（還不寫帳本）
    run(conn, fmt, data, **選項, dry_run, actor) -> 回報   寫進帳本（一批寫入、重跑不會多、全部核對）
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any

from ...errors import InvalidInput, NotFound
from . import files, formats, futures, realized, tables, trades

ADAPTERS = MappingProxyType({m.KIND: m for m in (trades, futures, realized)})


def _load(origin: str, items: list[tuple[str, str]]) -> tuple[list[formats.Format], list[dict[str, Any]]]:
    good, bad = [], []
    for file, text in items:
        fmt, problems = formats.parse(text, origin=origin, file=file, adapters=ADAPTERS)
        if fmt is None:
            bad.append({"file": file, "origin": origin, "problems": problems})
        else:
            good.append(fmt)
    return good, bad


def catalog() -> dict[str, Any]:
    """所有能用的描述檔（自己的同一個 id 蓋過內建的）＋壞掉的（列出每一個問題）。"""
    built, bad_built = _load("builtin", files.builtin_formats())
    mine, bad_mine = _load("user", files.user_formats())
    by_id: dict[str, formats.Format] = {f.id: f for f in built}
    dup = [f.id for f in mine if [m.id for m in mine].count(f.id) > 1]
    for f in mine:
        if f.id in dup:
            continue
        by_id[f.id] = f
    broken = bad_built + bad_mine + [{"file": f.file, "origin": "user", "problems": [f"id「{f.id}」跟另一份自己的描述檔重複"]}
                                     for f in mine if f.id in dup]
    return {"formats": sorted(by_id.values(), key=lambda f: (f.kind, f.id)), "broken": broken}


def describe(fmt: formats.Format) -> dict[str, Any]:
    return {"id": fmt.id, "kind": fmt.kind, "kind_name": ADAPTERS[fmt.kind].TITLE, "title": fmt.title, "source": fmt.source,
            "origin": fmt.origin, "file": fmt.file, "options": list(ADAPTERS[fmt.kind].OPTIONS)}


def get(name: str) -> formats.Format:
    found = catalog()
    for f in found["formats"]:
        if f.id == name:
            return f
    broken = [b for b in found["broken"] if b["file"].rsplit(".", 1)[0] == name]
    if broken:
        raise InvalidInput(f"描述檔「{name}」有問題，不能用", hint="；".join(broken[0]["problems"][:5]))
    raise NotFound(f"沒有「{name}」這個格式", suggestions=[f.id for f in found["formats"]][:8],
                   hint="用 /money_formats 看有哪些；自己的描述檔放在自己檔案資料夾的 formats/")


def detect(data: bytes) -> tuple[formats.Format, list, list]:
    """沒指定格式：看檔案是 html 還是 csv，找表頭對得上的描述檔；剛好一份才用（不猜）。"""
    source = tables.guess_source(data)
    hits = []
    cache: dict[str, list] = {}
    for fmt in catalog()["formats"]:
        if fmt.source != source:
            continue
        key = fmt.encoding.lower()
        if key not in cache:
            cache[key] = tables.read(data, source, fmt.encoding)
        found = tables.find(cache[key], fmt.header)
        if found:
            hits.append((fmt, *found))
    if not hits:
        raise InvalidInput("認不出這是哪一種對帳單", hint="用 format= 指定；或在自己的檔案資料夾的 formats/ 放一份描述檔")
    if len(hits) > 1:
        raise InvalidInput("好幾種格式都對得上，要指定一個", suggestions=[h[0].id for h in hits], hint="format=<其中一個>")
    return hits[0]


def read(file: str, fmt_name: str | None = None) -> tuple[formats.Format, dict[str, Any]]:
    """讀對帳單（還不寫帳本）→ (用的格式, 讀好的資料)。"""
    name, data = files.statement(file)
    if fmt_name:
        fmt = get(fmt_name)
        found = tables.find(tables.read(data, fmt.source, fmt.encoding), fmt.header)
        if not found:
            raise InvalidInput(f"這個檔裡找不到「{fmt.title}」的表", hint="表頭要有：" + "、".join(fmt.header))
        head, body = found
    else:
        fmt, head, body = detect(data)
    return fmt, ADAPTERS[fmt.kind].read(fmt, head, body, name)


def run(conn, file: str, *, fmt_name: str | None = None, dry_run: bool = False, actor: str = "import",
        **options: Any) -> dict[str, Any]:
    """讀對帳單、照描述檔的 kind 交給那個 adapter 寫進帳本。options：account、book、close（看 adapter 的 OPTIONS）。"""
    fmt, data = read(file, fmt_name)
    adapter = ADAPTERS[fmt.kind]
    extra = sorted(k for k, v in options.items() if v not in (None, "", False) and k not in adapter.OPTIONS)
    if extra:
        raise InvalidInput(f"「{fmt.title}」用不到 " + "、".join(extra), hint="這種對帳單收：" + ("、".join(adapter.OPTIONS) or "（沒有選項）"))
    picked = {k: v for k, v in options.items() if k in adapter.OPTIONS}
    return adapter.run(conn, fmt, data, dry_run=dry_run, actor=actor, **picked)
