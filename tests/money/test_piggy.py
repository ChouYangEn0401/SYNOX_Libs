"""撲滿（bridge/piggy.py＋app/js/views/piggy.js、reconcile.js，W8）。

1. 讀與動作（bot.read／bot.act，同一個 run 也是指令）：開倉、記交易、移動平均成本、放空、收倉算結果、違規三種、
   跟基準比的影子池、期貨選擇權的名目金額、對股數不改交易明細、塞價格；沒開投資時讀 piggy 不會壞；
   倉要在這一層（引擎沒有、bridge 補的）；捐贈只在開了選配時出現；基準不預設代號、名字自己取。
2. 打字的路與畫面的路結果一樣；兩個使用者互不可見。
3. 用 node 跑 piggy.js、reconcile.js（最小的假 DOM，不開瀏覽器、不開埠）：bridge 回的真實資料畫得出來，
   畫面上沒有 undefined／NaN／null；畫面裡沒有任何特定商品代號與券商名。
資料一律是假的（「範例公司」「TEST1」「120」）；「今天」是 conftest 的 TODAY（2030-01-15）。
"""

import importlib
import json
import os
import re
import shutil
import subprocess

import pytest

from synox.packs import kit

from .conftest import MONEY, OTHER, OWNER

INV = "證券-投資"
OP = "證券-操盤"
VIEWS = os.path.join(MONEY, "app", "js", "views")
NODE = shutil.which("node")
# 不該再出現的舊字（執行時才拼出來，去識別化的檢查掃這個檔才不會中）
OLD_GIVING = "十" + "一"
OLD_BENCH = "00" + "50"
OWNER_WORD = "擁" + "有者"


def eng(bot, name):
    return importlib.import_module(f"{bot.module.__name__}.engine.{name}")


def prepare(bot, fn, user=OWNER):
    db = eng(bot, "db")
    with bot.module.session.open_book(bot.pack, user) as book:
        with db.write(book.conn, actor="test", source="test") as w:
            return fn(w)


def accounts(bot, user=OWNER, giving=False):
    """一個投資用、一個操盤用的證券帳戶；giving＝順便打開「捐贈」選配。"""
    refdata, plans = eng(bot, "services.refdata"), eng(bot, "services.plans")

    def go(w):
        refdata.add_account(w, name=INV, kind="securities", asset_class="invest")
        refdata.add_account(w, name=OP, kind="securities", asset_class="trading")
        if giving:
            plans.set_bucket(w, "giving", active=True)
    prepare(bot, go, user)


def trade(bot, level, action, symbol, shares, price, user=OWNER, **more):
    values = {"date": more.pop("date", "2030-01-10"), "action": action, "level": level, "symbol": symbol,
              "name": more.pop("name", "範例公司"), "shares": str(shares), "price": str(price)}
    values.update({k: str(v) for k, v in more.items()})
    return bot.act("trade_add", user, values)


def refused(fn, *args):
    with pytest.raises(kit.AppError) as no:
        fn(*args)
    return no.value


# ── 沒開投資：不壞、回「沒有」──────────────────────────────────────────────────────

def test_a_new_book_has_no_piggy_but_reading_it_does_not_break(make_money):
    bot = make_money()
    assert bot.read("meta", OWNER)["piggy"] is False                     # 畫面不顯示撲滿分頁（W3 的 meta）
    p = bot.read("piggy", OWNER)
    assert p["has_layers"] is False and p["level"] is None and p["pending"] == []
    assert [lv["key"] for lv in p["levels"]] == ["cash", "invest", "trading", "property"]
    assert p["bench"]["symbol"] == "" and p["bench_name"] == "基準"      # 基準不預設任何代號
    for level in ("invest", "trading"):
        inside = bot.read("piggy", OWNER, {"level": level})
        assert inside["layer"]["books"] == [] and inside["layer"]["trades_count"] == 0
        assert bot.read("piggy_trades", OWNER, {"level": level})["items"] == []
        assert bot.read("piggy_positions", OWNER, {"level": level})["items"] == []
        assert bot.read("piggy_compare", OWNER, {"level": level})["total"] == 0
    assert bot.read("books", OWNER)["books"] == []
    assert bot.read("quotes_needed", OWNER)["items"] == []
    assert bot.read("reconcile", OWNER)["count"] == 0
    assert bot.read("quotes", OWNER, {"symbols": "TEST1"})["missing"] == ["TEST1"]
    no = refused(bot.act, "trade_add", OWNER, {"date": "2030-01-10", "action": "buy", "level": "invest",
                                                "symbol": "TEST1", "shares": "10", "price": "100"})
    assert "還沒有帳戶算「投資」" in no.message
    assert "沒有「沒有這層」這一層" in refused(bot.read, "piggy", OWNER, {"level": "沒有這層"}).message


def test_piggy_shows_once_there_is_a_securities_account(make_money):
    bot = make_money()
    accounts(bot)
    assert bot.read("meta", OWNER)["piggy"] is True
    assert bot.read("piggy", OWNER)["has_layers"] is True


# ── 開倉、記交易、移動平均成本、收倉 ─────────────────────────────────────────────────

def test_trades_open_a_book_average_cost_and_close(make_money):
    bot = make_money()
    accounts(bot)
    first = trade(bot, "invest", "buy", "TEST1", 100, 50)
    assert first["batch_id"] and first["trade"]["net"] == -5000 and first["trade"]["book"] == "2030 投資"   # 自動開那一年的倉
    trade(bot, "invest", "buy", "TEST1", 100, 70, date="2030-01-11")
    pos = bot.read("piggy_positions", OWNER, {"level": "invest"})
    item = pos["items"][0]
    assert item["shares"] == 200 and item["cost"] == 12000 and item["avg_price"] == 60      # 移動平均成本
    sold = trade(bot, "invest", "sell", "TEST1", 100, 80, date="2030-01-12", fee="20", tax="24")
    assert sold["trade"]["net"] == 8000 - 20 - 24
    page = bot.read("piggy_trades", OWNER, {"level": "invest"})
    assert page["total"] == 3 and page["items"][0]["realized"] == 8000 - 44 - 6000           # 賣的那筆：實現 = 淨收 − 平均成本
    book_id = first["trade"]["book_id"]
    closed = bot.act("pbook_close", OWNER, {"id": str(book_id), "do": "close"})
    assert closed["book"]["status"] == "closed" and closed["book"]["result"] == 8000 - 44 - 6000
    no = refused(bot.act, "trade_add", OWNER, {"date": "2030-01-13", "action": "buy", "level": "invest", "symbol": "TEST1",
                                                "shares": "1", "price": "1", "book": str(book_id)})
    assert "已經收倉了" in no.message and "/money_pbook_close" in no.message               # 新的指令名，不是舊命令列
    opened = bot.act("pbook_close", OWNER, {"id": str(book_id), "do": "reopen"})
    assert opened["book"]["status"] == "open"
    archived = bot.act("pbook_close", OWNER, {"id": str(book_id), "do": "archive"})
    assert archived["book"]["status"] == "archived"
    assert [b["id"] for b in bot.read("books", OWNER, {"level": "invest", "archived": "true"})["books"]] == [book_id]
    back = bot.act("pbook_close", OWNER, {"id": str(book_id), "do": "unarchive"})
    assert back["book"]["status"] == "closed"


def test_open_and_edit_a_book_with_money_and_clearing(make_money):
    bot = make_money()
    accounts(bot)
    made = bot.act("pbook", OWNER, {"level": "trading", "name": "波段一號", "capital": "100000", "max_loss": "5000",
                                    "per_trade_loss": "1000", "start": "2030-01-02"})
    b = made["book"]
    assert made["batch_id"] and b["layer"] == "trading" and b["capital"] == 100000 and b["max_loss"] == 5000
    assert b["start_date"] == "2030-01-02" and b["rules_missing"] is False
    edited = bot.act("pbook", OWNER, {"id": str(b["id"]), "max_loss": "-", "name": "-", "note": "範例做法"})
    assert edited["book"]["max_loss"] is None and edited["book"]["name"] == "波段一號"     # 「-」清掉；名字不能清＝沒給
    assert edited["book"]["rules_missing"] is True and edited["book"]["note"] == "範例做法"
    same = bot.act("pbook", OWNER, {"id": str(b["id"]), "note": "範例做法"})
    assert same["batch_id"] is None
    no = refused(bot.act, "pbook", OWNER, {"name": "沒說哪一層"})
    assert "level" in no.message


# ── 放空、違規三種 ───────────────────────────────────────────────────────────────

def test_short_and_three_kinds_of_violation(make_money):
    bot = make_money()
    accounts(bot)
    bot.act("piggy_set", OWNER, {"month_max_loss": "1500"})
    b = bot.act("pbook", OWNER, {"level": "trading", "name": "規矩倉", "capital": "50000", "max_loss": "2000",
                                 "per_trade_loss": "500"})["book"]
    bid = str(b["id"])
    short = trade(bot, "trading", "sell", "TEST2", 100, 100, book=bid, date="2030-01-03")
    assert short["trade"]["net"] == 10000
    pos = bot.read("piggy_positions", OWNER, {"level": "trading"})["items"][0]
    assert pos["shares"] == -100 and pos["side"] == "short"                                # 賣比買多＝放空
    cover = trade(bot, "trading", "buy", "TEST2", 100, 108, book=bid, date="2030-01-04")    # 回補賠 800 > 單次 500
    assert "超過單次可虧損" in cover["trade"]["violation"] and cover["warnings"]
    trade(bot, "trading", "buy", "TEST3", 100, 50, book=bid, date="2030-01-05")
    loss = trade(bot, "trading", "sell", "TEST3", 100, 38, book=bid, date="2030-01-06")     # 再賠 1,200：倉與本月都打完
    assert "超過單次可虧損" in loss["trade"]["violation"]
    again = trade(bot, "trading", "buy", "TEST3", 10, 40, book=bid, date="2030-01-07")
    assert "停止之後又進場" in again["trade"]["violation"] and "倉「規矩倉」" in again["trade"]["violation"]
    assert any("可虧損打完" in w for w in again["warnings"])
    other = bot.act("pbook", OWNER, {"level": "trading", "name": "第二倉", "max_loss": "9000"})["book"]
    trade(bot, "trading", "sell", "TEST3", 10, 40, book=bid, date="2030-01-08")
    month = trade(bot, "trading", "buy", "TEST4", 10, 10, book=str(other["id"]), date="2030-01-09")
    assert "本月可虧損" in month["trade"]["violation"]                                    # 本月可虧損打完還進場
    layer = bot.read("piggy", OWNER, {"level": "trading"})["layer"]
    assert layer["month"]["stopped"] is True and len(layer["violations"]) == 4


# ── 跟基準比：影子池、名字自己取 ───────────────────────────────────────────────────

def test_bench_compare_uses_a_shadow_pot_and_a_name_you_choose(make_money):
    bot = make_money()
    accounts(bot)
    ingest = eng(bot, "services.ingest")
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        ingest.run(book.conn, {"records": [{"type": "transfer", "amount": 20000, "date": "2030-01-02", "from": "銀行帳戶",
                                            "to": INV, "desc": "範例轉入"}]}, actor="test", source="test")
    trade(bot, "invest", "buy", "TEST1", 100, 100, date="2030-01-03")
    layer = bot.read("piggy", OWNER, {"level": "invest"})["layer"]
    cmp = layer["bench"]["compare"]
    assert cmp["put_in"] == 20000 and cmp["remaining"] == 20000 and cmp["shadow"] > 20000 and not cmp["beat"]
    assert layer["bench"]["symbol"] == "" and [x["years"] for x in layer["bench"]["blueprint"]] == [1, 3, 5]
    c = bot.read("piggy_compare", OWNER, {"level": "invest"})
    assert c["total"] == 1 and c["items"][0]["symbol"] == "TEST1" and c["bench_name"] == "基準"
    saved = bot.act("piggy_set", OWNER, {"bench_name": "TEST1", "bench_rate": "12%", "bench_low": "6"})
    assert saved["bench"] == {"rate": 0.12, "low": 0.06, "symbol": "TEST1"} and saved["bench_name"] == "TEST1"
    assert bot.read("piggy_compare", OWNER, {"level": "invest"})["total"] == 0              # 基準那一檔不跟自己比
    cleared = bot.act("piggy_set", OWNER, {"bench_name": "-"})
    assert cleared["bench_name"] == "基準" and cleared["bench"]["symbol"] == ""
    hidden = bot.act("piggy_hide", OWNER, {"layer": "invest", "key": c["items"][0]["key"]})
    assert hidden["batch_id"] and bot.read("piggy_compare", OWNER, {"level": "invest"})["total"] == 0
    assert bot.read("piggy_compare", OWNER, {"level": "invest", "hidden": "true"})["items"][0]["hidden"] is True
    bot.act("piggy_hide", OWNER, {"layer": "invest", "key": c["items"][0]["key"], "hidden": "no"})
    assert bot.read("piggy_compare", OWNER, {"level": "invest"})["total"] == 1


def test_futures_use_notional_and_are_not_compared(make_money):
    bot = make_money()
    accounts(bot)
    t = trade(bot, "trading", "buy", "TESTF", 2, 100, instrument="future", multiplier="50", fee="30")["trade"]
    assert t["gross"] == 2 * 100 * 50 and t["net"] == -(10000 + 30) and t["unit"] != "股"   # 名目金額＝口數×點數×每點
    no = refused(bot.act, "trade_add", OWNER, {"date": "2030-01-10", "action": "buy", "level": "trading",
                                                "symbol": "TESTF", "shares": "1", "price": "1", "instrument": "future"})
    assert "每點多少錢" in no.message and "台指" not in no.message
    assert bot.read("piggy_compare", OWNER, {"level": "trading"})["total"] == 0              # 期貨不跟基準比
    detail = bot.read("piggy_book", OWNER, {"id": str(t["book_id"])})
    assert detail["book"]["derivatives"] == 1 and detail["bench_name"] == "基準"


# ── 倉要在這一層（bridge 補的規矩）──────────────────────────────────────────────────

def test_a_book_must_belong_to_the_level(make_money):
    bot = make_money()
    accounts(bot)
    sim = bot.act("pbook", OWNER, {"level": "trading", "name": "模擬一號", "kind": "sim", "max_loss": "1000"})["book"]
    for name, params in (("piggy_trades", {"level": "invest", "book": str(sim["id"])}),
                         ("piggy_positions", {"level": "invest", "book": str(sim["id"])}),
                         ("piggy_compare", {"level": "invest", "book": str(sim["id"])})):
        no = refused(bot.read, name, OWNER, params)
        assert "不在這一層" in no.message and "資金操盤" in no.message, name
    no = refused(bot.act, "trade_add", OWNER, {"date": "2030-01-10", "action": "buy", "level": "invest",
                                                "book": str(sim["id"]), "symbol": "TEST1", "shares": "1", "price": "1"})
    assert "不在這一層" in no.message
    ok = bot.read("piggy_trades", OWNER, {"level": "trading", "book": str(sim["id"])})
    assert ok["items"] == [] and ok["total"] == 0


# ── 捐贈：只在開了選配時出現 ─────────────────────────────────────────────────────────

def test_giving_only_shows_when_the_option_is_on(make_money):
    bot = make_money()
    accounts(bot)
    b = bot.act("pbook", OWNER, {"level": "invest", "name": "贏的倉"})["book"]
    trade(bot, "invest", "buy", "TEST1", 100, 100, book=str(b["id"]), date="2030-01-03")
    trade(bot, "invest", "sell", "TEST1", 100, 120, book=str(b["id"]), date="2030-01-04")
    closed = bot.act("pbook_close", OWNER, {"id": str(b["id"]), "do": "close"})
    assert closed["book"]["result"] == 2000
    p = bot.read("piggy", OWNER)
    assert p["giving"] is False and not [x for x in p["pending"] if x["kind"] == "tithe"]
    no = refused(bot.act, "pbook", OWNER, {"id": str(b["id"]), "done": "yes"})
    assert "捐贈" in no.message
    assert "捐贈" not in bot.command("piggy_set", OWNER, "tithe_rate=20%")                 # 沒開：不改、也不提
    assert "捐贈" not in bot.command("pbook_close", OWNER, f"{b['id']} archive")
    bot.act("pbook_close", OWNER, {"id": str(b["id"]), "do": "unarchive"})
    prepare(bot, lambda w: eng(bot, "services.plans").set_bucket(w, "giving", active=True))
    p = bot.read("piggy", OWNER)
    todo = [x for x in p["pending"] if x["kind"] == "tithe"]
    assert p["giving"] is True and todo and todo[0]["amount"] == 200                         # 預設 10%
    rate = bot.act("pbook", OWNER, {"id": str(b["id"]), "tithe_rate": "15%"})
    assert rate["book"]["tithe_amount"] == 300
    done = bot.act("pbook", OWNER, {"id": str(b["id"]), "done": "yes"})
    assert done["book"]["tithe_done_at"] == "2030-01-15"
    assert not [x for x in bot.read("piggy", OWNER)["pending"] if x["kind"] == "tithe"]
    reply = bot.command("pbook", OWNER, f"id={b['id']} done=no")
    assert "捐贈 300" in reply and "還沒做" in reply, reply


def test_giving_reply_text_appears_only_when_on(make_money):
    bot = make_money()
    accounts(bot, giving=True)
    b = bot.act("pbook", OWNER, {"level": "invest", "name": "贏的倉"})["book"]
    trade(bot, "invest", "buy", "TEST1", 10, 100, book=str(b["id"]), date="2030-01-03")
    trade(bot, "invest", "sell", "TEST1", 10, 150, book=str(b["id"]), date="2030-01-04")
    reply = bot.command("pbook_close", OWNER, f"{b['id']} close")
    assert "收倉了" in reply and "捐贈 50" in reply and OLD_GIVING not in reply


# ── 對股數、塞價格 ───────────────────────────────────────────────────────────────

def test_count_shares_does_not_touch_trades(make_money):
    bot = make_money()
    accounts(bot)
    trade(bot, "invest", "buy", "TEST1", 100, 50, date="2030-01-03")
    before = bot.read("piggy_trades", OWNER, {"level": "invest"})
    r = bot.act("count", OWNER, {"account": INV, "symbol": "TEST1", "shares": "120"})
    assert r["batch_id"] and r["counted"] == 120 and r["derived"] == 100 and r["match"] is False and r["diff"] == 20
    after = bot.read("piggy_trades", OWNER, {"level": "invest"})
    assert after["items"] == before["items"] and after["total"] == before["total"]           # 交易明細一筆都沒動
    check = bot.read("reconcile", OWNER)
    assert any(i["key"].startswith("stock:mismatch") and i["level"] == "bad" for i in check["items"])
    ok = bot.act("count", OWNER, {"account": INV, "symbol": "test1", "shares": "100"})
    assert ok["match"] is True and ok["batch_id"]
    reply = bot.command("count", OWNER, f"{INV} TEST1 100")
    assert "對上了" in reply
    hints = " ".join(i["where"]["hint"] for i in bot.read("reconcile", OWNER)["items"])
    assert "Telegram" not in hints and "balance set" not in hints


def test_price_push_and_quotes(make_money):
    bot = make_money()
    accounts(bot)
    trade(bot, "invest", "buy", "TEST1", 10, 100, date="2030-01-03")
    need = bot.read("quotes_needed", OWNER)["items"]
    assert [x["symbol"] for x in need] == ["TEST1"] and need[0]["last_price"] is None
    pushed = bot.act("price_push", OWNER, {"prices": "TEST1=101.5 TEST2=33", "at": "2030-01-14 13:30"})
    assert pushed["batch_id"] and {r["symbol"] for r in pushed["items"]} == {"TEST1", "TEST2"}
    q = bot.read("quotes", OWNER, {"symbols": "test1,TEST9"})
    assert q["prices"]["TEST1"]["price"] == 101.5 and q["missing"] == ["TEST9"]
    c = bot.read("piggy_compare", OWNER, {"level": "invest", "quotes": "true"})
    assert c["items"][0]["remaining"] == 1015
    same = bot.act("price_push", OWNER, {"prices": "TEST1=101.5", "at": "2030-01-14 13:30"})
    assert same["batch_id"] is None
    no = refused(bot.act, "price_push", OWNER, {"prices": "亂寫"})
    assert "TEST1=101.5" in no.message


# ── 刪一列、打字的路＝畫面的路、兩個人互不可見 ──────────────────────────────────────────

def test_trade_rm_and_undo_bring_it_back(make_money):
    bot = make_money()
    accounts(bot)
    t = trade(bot, "invest", "buy", "TEST1", 10, 100)["trade"]
    gone = bot.act("trade_rm", OWNER, {"id": str(t["id"])})
    assert gone["batch_id"] and bot.read("piggy_trades", OWNER, {"level": "invest"})["total"] == 0
    bot.act("revert", OWNER, {"id": gone["batch_id"]})
    assert bot.read("piggy_trades", OWNER, {"level": "invest"})["total"] == 1
    assert "找不到交易明細" in refused(bot.act, "trade_rm", OWNER, {"id": "9999"}).message


def test_typed_and_screen_paths_end_the_same(make_money):
    bot = make_money()
    accounts(bot)
    reply = bot.command("trade_add", OWNER, "昨天 買 level=invest symbol=TEST1 name=範例公司 shares=1,000 price=12.5 fee=20")
    assert reply.startswith("📈 記好了") and "TEST1" in reply and "編號" in reply, reply
    trade(bot, "invest", "buy", "TEST1", 1000, 12.5, date="2030-01-14", fee="20")
    items = bot.read("piggy_trades", OWNER, {"level": "invest"})["items"]
    keep = ("date", "action", "symbol", "name", "shares", "price", "gross", "fee", "tax", "net", "account", "book_id")
    assert len(items) == 2 and {k: items[0][k] for k in keep} == {k: items[1][k] for k in keep}
    reply = bot.command("pbook", OWNER, "level=trading name=打字開的 capital=1000 max_loss=100")
    assert reply.startswith("🐷 開好一倉") and "打字開的" in reply, reply
    assert "設定存好了" in bot.command("piggy_set", OWNER, "bench_rate=15%")
    reply = bot.command("price_push", OWNER, "TEST1=13")
    assert "收到 1 檔價格" in reply, reply


def test_two_people_cannot_see_each_other(make_money):
    bot = make_money()
    accounts(bot, OWNER)
    accounts(bot, OTHER)
    t = trade(bot, "invest", "buy", "TEST1", 10, 100, user=OWNER)["trade"]
    bot.act("price_push", OWNER, {"prices": "TEST1=111"})
    assert bot.read("piggy_trades", OTHER, {"level": "invest"})["total"] == 0
    assert bot.read("books", OTHER)["books"] == []
    assert bot.read("quotes", OTHER, {"symbols": "TEST1"})["missing"] == ["TEST1"]
    assert "找不到" in refused(bot.read, "piggy_book", OTHER, {"id": str(t["book_id"])}).message
    assert "找不到" in refused(bot.act, "trade_rm", OTHER, {"id": str(t["id"])}).message
    assert bot.read("piggy_trades", OWNER, {"level": "invest"})["total"] == 1


# ── 畫面的檔：沒有特定代號、舊的用語、會連外或寫 HTML 的寫法 ─────────────────────────────────────

def test_screens_have_no_brand_codes_or_old_words():
    for name in ("piggy.js", "reconcile.js"):
        text = open(os.path.join(VIEWS, name), encoding="utf-8").read()
        assert not re.search(r'\b00\d{2}\b|\b\d{4}\.TW\b|"例：\d{4}"', text), name      # 範例代號一律用 TEST1
        assert OLD_GIVING not in text and "Telegram" not in text and OWNER_WORD not in text, name
        assert not re.search(r"ADR 00[0-3]\d", text) and "style=\"" not in text and "innerHTML" not in text, name
        for word in ("fetch(", "setInterval(", "http", "sessionStorage", "location.href"):
            assert word not in text, (name, word)


# ── node 跑 piggy.js：真實資料畫得出來 ────────────────────────────────────────────────

PRELUDE = r"""
class MNode {}
class El extends MNode {
  constructor(tag) { super(); this.tag = tag; this.children = []; this.attrs = {}; this.style = {}; this.dataset = {}; this._text = "";
    this.classList = { toggle() {}, add() {}, remove() {}, contains: () => false }; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener() {}
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = kids; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text; }
}
class Txt extends MNode { constructor(t) { super(); this.text = String(t); } }
globalThis.Node = MNode;
globalThis.window = globalThis;
globalThis.scrollTo = () => {};
globalThis.document = { createElement: (t) => new El(t), createElementNS: (ns, t) => new El(t),
  createTextNode: (t) => new Txt(t), querySelector: () => null };
const allText = (n) => (n instanceof Txt ? n.text : n instanceof El
  ? n._text + Object.values(n.attrs).filter((v) => typeof v === "string").join(" ") + n.children.map(allText).join("") : "");
"""

RENDER = r"""
const calls = [];
const pick = (name, params) => {
  const key = name === "piggy" ? `piggy:${params.level || ""}` : name;
  if (!(key in DATA)) throw { code: "not_found", error: `沒有準備 ${key}` };
  return DATA[key];
};
globalThis.SynoxApp = { read: async (name, params) => { calls.push(name); return pick(name, params || {}); },
  act: async () => ({}), download: async () => true };
const piggy = await import(PIGGY);
const { reconcileCard } = await import(RECONCILE);
const settle = () => new Promise((r) => setTimeout(r, 20));
const app = { period: null, meta: { today: "2030-01-15" }, setNav() {}, hideNav() {}, go() {}, toast() {}, openModal() {},
  closeModal() {}, render() {}, afterWrite: async () => {} };
const pages = {};
const show = async (label, level, tab, book) => {
  app.piggyLevel = level; app.piggyTab = tab; app.piggyBook = book;
  const node = await piggy.render(app);
  await settle();
  pages[label] = allText(node);
};
await show("top", null, "home");
for (const level of LEVELS) for (const tab of ["home", "positions", "trades", "review", "setup"]) await show(`${level}:${tab}`, level, tab);
if (BOOK) await show("book", BOOK_LEVEL, "book", BOOK);
const card = reconcileCard(app, "ledger");
await settle();
pages.ledgerCard = allText(card);
pages.pending = allText(piggy.pendingList(app, DATA["piggy:"].pending) || "");
console.log(JSON.stringify({ pages, calls, giving: app.piggyGiving }));
"""


def file_url(path):
    return "file:///" + os.path.abspath(path).replace("\\", "/")


def run_node(script, folder):
    path = os.path.join(str(folder), "check_piggy.mjs")
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)
    out = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def busy_piggy(bot, giving):
    accounts(bot, giving=giving)
    ingest = eng(bot, "services.ingest")
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        ingest.run(book.conn, {"records": [{"type": "transfer", "amount": 30000, "date": "2029-12-02", "from": "銀行帳戶",
                                            "to": INV, "desc": "範例轉入"}]}, actor="test", source="test")
    trade(bot, "invest", "buy", "TEST1", 100, 100, date="2029-12-03")
    trade(bot, "invest", "sell", "TEST1", 50, 130, date="2030-01-03")
    win = bot.act("pbook", OWNER, {"level": "invest", "name": "贏的倉", "start": "2029-01-01", "end": "2029-06-30"})["book"]
    trade(bot, "invest", "buy", "TEST2", 10, 100, book=str(win["id"]), date="2029-03-03")
    trade(bot, "invest", "sell", "TEST2", 10, 160, book=str(win["id"]), date="2029-04-03")
    bot.act("pbook_close", OWNER, {"id": str(win["id"]), "do": "close"})
    op = bot.act("pbook", OWNER, {"level": "trading", "name": "波段一號", "capital": "50000", "max_loss": "500",
                                  "per_trade_loss": "200"})["book"]
    trade(bot, "trading", "buy", "TEST3", 100, 50, book=str(op["id"]), date="2030-01-05", stop_price="48")
    trade(bot, "trading", "sell", "TEST3", 100, 40, book=str(op["id"]), date="2030-01-06")
    trade(bot, "trading", "buy", "TESTF", 1, 100, book=str(op["id"]), date="2030-01-07", instrument="future",
          multiplier="50")
    bot.act("count", OWNER, {"account": INV, "symbol": "TEST1", "shares": "60"})
    bot.act("price_push", OWNER, {"prices": "TEST1=140"})
    return op["id"]


@pytest.mark.skipif(NODE is None, reason="這台電腦沒有 node")
@pytest.mark.parametrize("case", ["empty", "busy-giving-off", "busy-giving-on"])
def test_real_payloads_render_without_broken_words(make_money, case, tmp_path):
    bot = make_money()
    book_id = None
    if case == "empty":
        accounts(bot)
    else:
        book_id = busy_piggy(bot, giving=case.endswith("on"))
    data = {"piggy:": bot.read("piggy", OWNER), "reconcile": bot.read("reconcile", OWNER),
            "quotes": bot.read("quotes", OWNER, {"symbols": "TEST1"})}
    for level in ("invest", "trading", "cash", "property"):
        data[f"piggy:{level}"] = bot.read("piggy", OWNER, {"level": level})
    data["piggy_trades"] = bot.read("piggy_trades", OWNER, {"level": "trading"})
    data["piggy_positions"] = bot.read("piggy_positions", OWNER, {"level": "invest"})
    data["piggy_compare"] = bot.read("piggy_compare", OWNER, {"level": "invest"})
    data["books"] = bot.read("books", OWNER, {"level": "invest", "archived": "true"})
    if book_id:
        data["piggy_book"] = bot.read("piggy_book", OWNER, {"id": str(book_id)})
    script = (PRELUDE + f"const PIGGY = {json.dumps(file_url(os.path.join(VIEWS, 'piggy.js')))};\n"
              f"const RECONCILE = {json.dumps(file_url(os.path.join(VIEWS, 'reconcile.js')))};\n"
              f"const DATA = {json.dumps(data)};\nconst LEVELS = {json.dumps(['invest', 'trading', 'cash', 'property'])};\n"
              f"const BOOK = {json.dumps(book_id)};\nconst BOOK_LEVEL = \"trading\";\n" + RENDER)
    shown = run_node(script, tmp_path)
    pages = shown["pages"]
    for label, text in pages.items():
        for broken in ("undefined", "NaN", "null", "[object", "沒有準備", OLD_BENCH):
            assert broken not in text, (label, broken, text[:1500])
    assert "帳戶水平" in pages["top"] and "對明細" in pages["ledgerCard"]
    assert "跟基準比" in pages["invest:home"]
    assert "基準叫什麼" in pages["invest:setup"]
    if case == "empty":
        return
    assert "操盤三條規矩" in pages["trading:home"] and "違規紀錄" in pages["trading:review"]
    assert "部位狀況" in pages["invest:positions"] and "對明細" in pages["invest:positions"]
    assert "波段一號" in pages["book"] and "名目" in pages["trading:trades"]
    giving = case.endswith("on")
    assert shown["giving"] is giving
    for label in ("top", "invest:review", "invest:setup", "pending"):
        assert ("捐贈" in pages[label]) is giving, label


# ── 真的 SYNOX＋AppClient（不開埠）：欄位檢查、路徑 id、改名的欄位都走得通 ────────────────────

from .test_app_home import opened  # noqa: E402,F401  （同一套：真的載入 packs/money、FakeListen）


def test_screen_reads_and_actions_go_through_synox(opened):  # noqa: F811
    bot, client = opened()
    status, data = client.read("piggy")
    assert status == 200 and data["has_layers"] is False and data["bench_name"] == "基準"
    status, data = client.do("pbook", {"level": "invest", "name": "畫面開的"})
    assert status == 404 and "還沒有帳戶算「投資」" in data["error"]
    status, data = client.do("piggy_set", {"bench_name": "範例指數", "bench_rate": "10%"})
    assert status == 200 and data["bench_name"] == "範例指數" and data["batch_id"]
    status, data = client.read("piggy_trades", level="invest", book="1")
    assert status == 404 and "找不到倉" in data["error"]
    status, data = client.read("piggy_trades")
    assert status == 400 and "invest" in data["error"]
