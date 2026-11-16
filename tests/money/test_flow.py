"""進階金流（W7）：信用卡帳單與逐列對帳、繳卡費、負債分期、代墊淨額、餘額核對、水電帳單、收據附檔。

只在聊天用（沒有畫面動作）；資料一律是假的。今天是 2030-01-15（conftest 的 TODAY）。
"""

import importlib
import json
import os

import pytest

from synox.packs import kit

from .conftest import OTHER, OWNER

CARD = "範例卡"


def svc(bot, name):
    return importlib.import_module(f"{bot.module.__name__}.engine.services.{name}")


def engine_db(bot):
    return importlib.import_module(f"{bot.module.__name__}.engine.db")


def ingest(bot, rows, user=OWNER):
    return bot.command("ingest", user, json.dumps({"records": rows}, ensure_ascii=False))


def add_card(bot, user=OWNER):
    with bot.module.session.open_book(bot.pack, user) as book:
        refdata = svc(bot, "refdata")
        with engine_db(bot).write(book.conn) as w:
            refdata.add_account(w, name=CARD, kind="credit_card", card_closing_day=10, card_due_day=25, card_limit=50000,
                                pay_from_account_id=refdata.resolve_account(book.conn, "銀行帳戶")["id"])


def setting(bot, key, value, user=OWNER):
    with bot.module.session.open_book(bot.pack, user) as book:
        with engine_db(bot).write(book.conn) as w:
            svc(bot, "settings").put(w, key, value)


def count(bot, sql, user=OWNER):
    with bot.module.session.open_book(bot.pack, user) as book:
        return book.conn.execute(sql).fetchone()[0]


@pytest.fixture
def bot(make_money):
    b = make_money()
    add_card(b)
    ingest(b, [
        {"type": "expense", "amount": 120, "date": "2029-12-12", "desc": "排骨便當", "account": CARD},
        {"type": "expense", "amount": 280, "date": "2029-12-20", "desc": "範例書", "account": CARD},
        {"type": "expense", "amount": 160, "date": "2029-12-28", "desc": "範例雜貨店", "account": "現金"},
    ])
    return b


STATEMENT = ("card=範例卡 closing_date=2030-01-10 due_date=2030-01-25 total=900 "
             "lines=12/12 範例便當店 120; 12/20 範例書店 250; 12/28 範例雜貨店 160; 01/02 範例計程車 180; 01/05 年費 190")


def statement_lines(bot):
    sid = count(bot, "SELECT MAX(id) FROM card_statements")
    st = bot.command("card", OWNER, f"statement={sid}")
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        d = svc(bot, "cards").detail(book.conn, sid)
    return sid, st, {ln["desc"]: ln for ln in d["lines"]}


# ── 信用卡 ─────────────────────────────────────────────────────────────────────

def test_card_with_no_cards(make_money):
    bot = make_money()
    assert "還沒有信用卡帳戶" in bot.command("card", OWNER)


def test_statement_lines_get_each_state_and_resend_is_not_doubled(bot):
    reply = bot.command("card_statement", OWNER, STATEMENT)
    assert reply.startswith("帳單記好了") and "總額 900" in reply
    sid, text, lines = statement_lines(bot)
    assert lines["範例便當店"]["status"] == "matched" and lines["範例便當店"]["date"] == "2029-12-12"   # 12/12 是去年
    assert lines["範例書店"]["status"] == "diff"                         # 帳單 250、記帳 280
    assert lines["範例計程車"]["status"] == "missing"                    # 漏記
    assert lines["範例雜貨店"]["status"] == "missing" and lines["範例雜貨店"]["suggestion"]["action"] == "move"
    assert lines["年費"]["status"] == "fee"
    assert "對上" in text and "金額不同" in text and "漏記" in text and "手續費" in text
    assert "⚠️" in reply                                                # 漏記、金額不同的提醒
    again = bot.command("card_statement", OWNER, STATEMENT)
    assert again.startswith("這張帳單之前記過了")
    assert count(bot, "SELECT COUNT(*) FROM card_statements") == 1


def test_fix_each_line_then_pay(bot):
    bot.command("card_statement", OWNER, STATEMENT)
    sid, _, lines = statement_lines(bot)
    assert "金額不同" not in bot.command("card_match", OWNER, f"{lines['範例書店']['id']} action=改成帳單金額").split("\n")[2]
    bot.command("card_match", OWNER, f"{lines['範例計程車']['id']} action=補記")
    bot.command("card_match", OWNER, f"{lines['範例雜貨店']['id']} action=改到這張卡")
    bot.command("card_match", OWNER, f"{lines['年費']['id']} action=補記")
    _, text, lines = statement_lines(bot)
    assert {ln["status"] for ln in lines.values()} == {"matched"} and "對平了" in text
    reply = bot.command("card_pay", OWNER, str(sid))
    assert reply.startswith("記好了：銀行帳戶 → 範例卡 900") and "不算支出" in reply
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        assert svc(bot, "cards").detail(book.conn, sid)["paid"]
    assert "已繳" in bot.command("card", OWNER) and "❌" in bot.command("card_pay", OWNER, str(sid))   # 繳清了


def test_manual_match_learns_and_rejects_double_use(bot):
    bot.command("card_statement", OWNER, STATEMENT)
    _, _, lines = statement_lines(bot)
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        txn = book.conn.execute("SELECT id FROM transactions WHERE description = '排骨便當'").fetchone()[0]
    reply = bot.command("card_match", OWNER, f"{lines['範例計程車']['id']} {txn}")
    assert reply.startswith("❌") and "已經對到" in reply                 # 那一筆已經對到便當那一列
    assert bot.command("card_match", OWNER, f"{lines['範例計程車']['id']}").startswith("❌")   # 沒說對到哪一筆、也沒說怎麼處理
    assert "不對" in bot.command("card_match", OWNER, f"{lines['範例計程車']['id']} action=亂寫")


def test_statement_needs_the_basics_and_json_works_too(bot):
    assert "帳單還少了" in bot.command("card_statement", OWNER, "card=範例卡 total=100")
    content = {"card": CARD, "closing_date": "2030-01-10", "total": 120,
               "lines": [{"date": "2029-12-12", "desc": "範例便當店", "amount": 120}]}
    reply = bot.command("card_statement", OWNER, "content=" + json.dumps(content, ensure_ascii=False))
    assert reply.startswith("帳單記好了") and "對平了" in reply
    assert "不是信用卡" in bot.command("card_statement", OWNER, STATEMENT.replace("card=範例卡", "card=現金"))
    assert "看不懂" in bot.command("card_statement", OWNER, "card=範例卡 closing_date=2030-01-10 total=1 lines=壞掉")


def test_card_status_lists_unbilled_and_alerts(bot):
    reply = bot.command("card", OWNER)
    assert reply.startswith(CARD) and "未出帳" in reply and "還沒有帳單" in reply
    assert bot.command("card", OTHER) .startswith("還沒有信用卡帳戶")    # 別人的帳本看不到這張卡


# ── 負債與分期 ──────────────────────────────────────────────────────────────────

def test_installment_remainder_goes_to_the_first_period(bot):
    reply = bot.command("debt", OWNER, "add name=範例手機分期 kind=卡分期 periods=12 principal=20000 start=2030-01-10 account=範例卡")
    assert reply.startswith("新增：#") and "0/12 期" in reply
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        d = svc(bot, "debts").all_debts(book.conn)[0]
    amounts = [r["amount"] for r in d["schedule"]]
    assert amounts[0] == 1674 and set(amounts[1:]) == {1666} and sum(amounts) == 20000
    show = bot.command("debt", OWNER, f"show {d['id']}")
    assert "第 1 期 2030-01-10 1,674" in show and "第 12 期" in show
    paid = bot.command("debt", OWNER, f"pay {d['id']}")
    assert paid.startswith("第 1 期付了（卡分期")
    assert "每月固定要還" in bot.command("debt", OWNER) and "1/12 期" in bot.command("debt", OWNER)


def test_self_loan_pay_records_a_transfer(bot):
    bot.command("debt", OWNER, "add name=範例自借款 kind=自借款 periods=3 principal=3000 start=2030-01-01 "
                               "account=現金 lender=銀行帳戶")
    did = count(bot, "SELECT MAX(id) FROM debts")
    reply = bot.command("debt", OWNER, f"pay {did}")
    assert "現金 → 銀行帳戶 1,000（轉帳）" in reply
    assert count(bot, "SELECT COUNT(*) FROM transactions WHERE kind = 'transfer' AND purpose = 'debt'") == 1
    edited = bot.command("debt", OWNER, f"edit {did} name=改過的名字")
    assert edited.startswith("改好了：#") and "改過的名字" in edited


def test_debt_refuses_bad_input(bot):
    assert "新增負債要給" in bot.command("debt", OWNER, "add name=範例")
    assert "不對" in bot.command("debt", OWNER, "add name=範例 periods=2 principal=100 start=2030-01-10 kind=亂寫")
    assert "卡分期要掛在信用卡" in bot.command("debt", OWNER, "add name=範例 periods=2 principal=100 start=2030-01-10 account=現金")
    assert "找不到負債" in bot.command("debt", OWNER, "show 99") and not any(w in bot.command("debt", OWNER, "show 99") for w in ("ledger.db", "LedgerError"))
    assert "do=" in bot.command("debt", OWNER, "亂寫")


# ── 代墊淨額 ───────────────────────────────────────────────────────────────────

def test_lend_shows_each_person(bot):
    assert bot.command("lend", OWNER) == "沒有代墊，也沒有欠別人"
    ingest(bot, [{"type": "iou", "direction": "lend", "who": "小明", "amount": 300, "date": "2030-01-10", "desc": "午餐"},
                 {"type": "iou", "direction": "collect", "who": "小明", "amount": 100, "date": "2030-01-12"}])
    reply = bot.command("lend", OWNER)
    assert reply.startswith("小明 欠我 200") and "別人欠我 200" in reply
    assert bot.command("lend", OTHER) == "沒有代墊，也沒有欠別人"


# ── 餘額核對 ───────────────────────────────────────────────────────────────────

def test_balance_check_diff_and_adjust(bot):
    first = bot.command("balance", OWNER, "銀行帳戶 10000 as_of=2030-01-01")
    assert "第一次核對" in first
    ingest(bot, [{"type": "expense", "amount": 500, "date": "2030-01-05", "desc": "範例超市", "account": "銀行帳戶"}])
    second = bot.command("balance", OWNER, "銀行帳戶 9000 as_of=2030-01-10")
    assert "照記帳推算 9,500" in second and "差 -500" in second and "/money_balance adjust=" in second
    sid = count(bot, "SELECT MAX(id) FROM balance_snapshots")
    fixed = bot.command("balance", OWNER, f"adjust={sid}")
    assert fixed.startswith("補了一筆支出 500")
    assert "跟記帳推算的一樣" in bot.command("balance", OWNER, "銀行帳戶 9000 as_of=2030-01-10 note=再對一次")
    listing = bot.command("balance", OWNER)
    assert listing.startswith("資產") and "銀行帳戶 9,000" in listing
    assert "同時給" in bot.command("balance", OWNER, "account=銀行帳戶")


# ── 水電瓦斯帳單 ────────────────────────────────────────────────────────────────

def test_bill_add_pay_and_trend(bot):
    added = bot.command("bill", OWNER, "add type=電費 amount=1200 usage=350 period_start=2029-11-20 "
                                       "period_end=2030-01-05 due_date=2030-01-20")
    assert added.startswith("帳單記好了") and "還沒繳" in added
    bid = count(bot, "SELECT MAX(id) FROM bills")
    paid = bot.command("bill", OWNER, f"pay {bid} account=銀行帳戶")
    assert paid.startswith("記好了：電費") and "1,200" in paid
    assert "已繳" in bot.command("bill", OWNER)
    assert "350" in bot.command("bill", OWNER, "trend type=電費")
    again = bot.command("bill", OWNER, "add type=電費 amount=1200 usage=350 period_end=2030-01-05")
    assert again.startswith("這張帳單記過了")
    assert "已經記過繳費" in bot.command("bill", OWNER, f"pay {bid}")
    assert "要給 type" in bot.command("bill", OWNER, "add amount=100")


# ── 收據附檔 ───────────────────────────────────────────────────────────────────

def test_attach_only_from_own_folder(bot):
    with bot.module.session.open_book(bot.pack, OWNER) as book:
        txn = book.conn.execute("SELECT id FROM transactions WHERE description = '排骨便當'").fetchone()[0]
        path = book.path("receipt.png")
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n fake receipt")
    reply = bot.command("attach", OWNER, f"{txn} receipt.png")
    assert reply.startswith("收據掛好了") and "receipt.png" in reply
    assert count(bot, "SELECT COUNT(*) FROM attachments") == 1
    assert "自己的資料夾" in bot.command("attach", OWNER, f"{txn} ../../{os.path.basename(path)}")
    assert "自己的資料夾" in bot.command("attach", OWNER, f"{txn} {os.path.abspath(os.sep)}secret.png")
    assert "找不到" in bot.command("attach", OWNER, f"{txn} nothing.png")
    assert "找不到交易" in bot.command("attach", OTHER, f"{txn} receipt.png")      # 別人的編號在自己帳本裡找不到


# ── 金額照帳本的小數位數 ─────────────────────────────────────────────────────────────

def test_amounts_follow_the_book_decimals(bot):
    setting(bot, "currency.decimals", 2)
    reply = bot.command("balance", OWNER, "現金 99.5 as_of=2030-01-01")
    assert "99.50" in reply
    assert count(bot, "SELECT balance FROM balance_snapshots WHERE note = ''") == 9950
    assert "小數位數" in bot.command("balance", OWNER, "現金 1.005")
    setting(bot, "currency.decimals", 0)
    assert "小數位數" in bot.command("balance", OWNER, "現金 1.5")


def test_flow_helpers():
    from datetime import date
    from importlib import import_module
    from .conftest import load_money
    flow = import_module(load_money().__name__ + ".bridge.flow")
    today = date(2030, 1, 15)
    assert flow.parse_day("昨天", today) == "2030-01-14" and flow.parse_day("1/3", today) == "2030-01-03"
    assert flow.parse_day("2029-12-31", today) == "2029-12-31" and flow.parse_day("01-05", today) == "2030-01-05"
    with pytest.raises(Exception, match="看不懂"):            # 共用的 table.parse_day：引擎的 InvalidInput
        flow.parse_day("2/30", today)
    assert flow.yes("是") is True and flow.yes("no") is False and flow.yes("", None) is None
    with pytest.raises(kit.AppError):
        flow.yes("也許")
    with pytest.raises(kit.AppError):
        flow.int_of("abc", "帳單")
    assert flow.int_of("#12", "帳單") == 12


def test_flow_commands_are_chat_only(make_money):
    bot = make_money()
    names = {c.name for c in bot.module.flow.COMMANDS}
    assert names == {"card", "card_statement", "card_match", "card_pay", "debt", "lend", "balance", "bill", "attach"}
    assert not names & set(bot.pack.app_actions) and not any(c.ai for c in bot.module.flow.COMMANDS)
