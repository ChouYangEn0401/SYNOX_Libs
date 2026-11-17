"""對帳單匯入：讀自己檔案資料夾裡的對帳單（網頁存成的 html、或 CSV），照格式描述檔寫進撲滿的交易明細。

程式裡不寫任何券商的格式：欄位、表頭、交易種類的字、契約乘數、稅率都在描述檔（engine/services/statements/）。
內建幾份範例描述檔；使用者自己的放在自己檔案資料夾的 formats/（同一個 id 用自己的）。/money_formats 列出能用的與壞掉的。

這兩個只是指令（不是畫面的動作：畫面還不能上傳檔案，SYNOX 一個畫面的動作也快滿了）。
金額：撲滿目前只支援整數幣別（跟 piggy.py 同一個守門）。
"""

from synox.packs import kit

from ..engine import db
from ..engine.services import statements
from . import table
from .flow import text_of, yes
from .piggy import _whole_only
from .table import READ, WRITE, Cmd, Field, Result, frozen

DOMAIN = "statements"
SAMPLE = 5                                   # 回覆裡每一種清單最多列幾筆（全部在 data 裡）


def _money(book, n) -> str:
    return table.show(book.conn, n)


def _few(items, fmt) -> str:
    shown = "、".join(fmt(i) for i in items[:SAMPLE])
    return shown + (f"…等 {len(items)} 筆" if len(items) > SAMPLE else "")


def _trades_text(book, r):
    head = (f"{r['file']}（{r['format']}）{r['first']}～{r['last']}：{r['rows']} 列；新記 {r['created']}"
            + (f"、帳本已經有 {r['already']}" if r["already"] else "") + (f"、之前匯過 {r['replayed']}" if r["replayed"] else ""))
    warn = []
    c = r["checks"]
    if r["skipped_day_trades"]:
        warn.append(f"當沖 {r['skipped_day_trades']} 腳沒記：要記的話加 book=<操盤那一層的倉>")
    if c.get("days_off"):
        warn.append("小計對不上的日子：" + _few(c["days_off"], lambda d: d["date"]))
    if c.get("day_trades_off"):
        warn.append("當沖淨收付對不上：" + _few(c["day_trades_off"], lambda d: f"{d['date']} {d['symbol']}"))
    if c.get("rows_off"):
        warn.append("淨收付跟算的不一樣（照對帳單記）：" + _few(c["rows_off"], lambda d: f"{d['date']} {d['symbol']}"))
    if r["differ"]:
        warn.append("帳本已經有、但金額不一樣（沒有改帳本）：" + _few(r["differ"], lambda d: f"{d['date']} {d['symbol']}"))
    if r["short"]:
        warn.append("帳上賣的比買的多（多半是漏了買進）：" + _few(r["short"], lambda i: f"{i['symbol']} {i['shares']}"))
    return head, warn


def _futures_text(book, r):
    b = r["book"]
    head = (f"{r['file']}（{r['format']}）{r['first']}～{r['last']}：{r['rows']} 列進「{b['name']}」；新記 {r['created']}"
            + (f"、之前匯過 {r['replayed']}" if r["replayed"] else "") + f"；這一倉結果 {_money(book, b['result'])}（{b['status']}）")
    warn = list(r["notes"])
    if r["check"]["contracts_off"]:
        warn.append("契約損益對不上：" + _few(r["check"]["contracts_off"], str))
    if not r["check"]["total_ok"]:
        warn.append("總計列對不上（口數、費用或損益），請看一下對帳單")
    if r["expired"]:
        warn.append("放到到期、記成 0 元歸零（價內有拿到錢要另外補）：" + _few(r["expired"], lambda x: f"{x['name']}（{x['expiry']}）"))
    if r["open"]:
        warn.append("還沒平倉：" + _few(r["open"], lambda x: f"{x['name']} {x['lots']} 口"))
    if r["rounding"]:
        warn.append("補了均價零頭（以對帳單為準）：" + _few(r["rounding"], lambda x: f"{x['symbol']} {x['amount']:+}"))
    return head, warn


def _realized_text(book, r):
    head = (f"{r['file']}（{r['format']}）{r['first']}～{r['last']}：{r['rows']} 列；帳本對上 {r['matched']}"
            + (f"、當沖 {len(r['day_trades'])}（新記 {r['created']}）" if r["day_trades"] else "")
            + (f"、等買進紀錄 {len(r['waiting'])}" if r["waiting"] else ""))
    warn = []
    if r["income_off"]:
        warn.append("淨收跟帳本不一樣（沒有改帳本）：" + _few(r["income_off"], lambda x: f"{x['date']} {x['symbol']}"))
    if r["day_trades"] and "book" not in r:
        warn.append("當沖沒補記：要補的話加 book=<操盤那一層的倉>")
    if r["waiting"]:
        warn.append("帳本沒有買進紀錄、先不記（給更早的成交明細再匯一次就會對上）：" + _few(r["waiting"], lambda x: f"{x['date']} {x['symbol']}"))
    return head, warn


def run_import_statement(book, values) -> Result:
    file = text_of(values, "file")
    if not file:
        raise kit.AppError("要寫檔名（放在自己的檔案資料夾裡）", code="invalid", status=400)
    dry = bool(yes(values.get("dry_run"), False))
    r = statements.run(book.conn, file, fmt_name=text_of(values, "format") or None, dry_run=dry, actor=book.actor,
                       account=text_of(values, "account") or None, book=text_of(values, "book") or None,
                       close=bool(yes(values.get("close"), False)))
    if r["kind"] == "futures":
        head, warn = _futures_text(book, r)
    elif r["kind"] == "realized":
        head, warn = _realized_text(book, r)
    else:
        head, warn = _trades_text(book, r)
    if dry:
        head = "試算（還沒寫進去）：" + head
    r["write_seq"] = db.write_seq(book.conn)
    return Result(data=r, text=head, warnings=warn)


def run_formats(book, values) -> Result:
    found = statements.catalog()
    items = [statements.describe(f) for f in found["formats"]]
    lines = [f"・{i['id']}：{i['title']}（{i['kind_name']}；{'自己的' if i['origin'] == 'user' else '內建'}；"
             f"選項 {'、'.join(i['options']) or '沒有'}）" for i in items]
    warn = [f"{b['file']} 不能用：" + "；".join(b["problems"][:3]) for b in found["broken"]]
    text = "能讀的對帳單格式：\n" + "\n".join(lines) + "\n自己的描述檔放在自己檔案資料夾的 formats/（複製一份內建的改）"
    return Result(data={"formats": items, "broken": found["broken"]}, text=text, warnings=warn)


COMMANDS = (
    Cmd("import_statement", "匯入對帳單（自己檔案資料夾裡的 html 或 CSV；照格式描述檔讀，重跑不會多記）", WRITE, [
        Field("file", "檔名（放在自己的檔案資料夾裡）", required=True),
        Field("format", "格式（描述檔的 id；不給＝自己認）"),
        Field("account", "證券帳戶（成交明細、已實現損益；不給＝投資那一層唯一的那個）"),
        Field("book", "記進哪一倉（期貨選擇權一定要；成交明細與已實現損益的當沖記進這一倉）"),
        Field("close", "期貨都平倉了就收倉（yes／no）"),
        Field("dry_run", "試算就好、先不寫進去（yes／no）")], _whole_only(run_import_statement), positional=1),
    Cmd("formats", "能讀哪些對帳單格式（內建的與自己放的描述檔）", READ, [], run_formats, positional=0),
)

READS = frozen({})
EXPORTS = frozen({})
