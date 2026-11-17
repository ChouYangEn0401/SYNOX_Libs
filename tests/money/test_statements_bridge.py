"""0.5.0 的三個新指令：/money_import_statement、/money_formats、/money_review_run（經 SYNOX 那一層，用假的 pack）。

資料全是假的（假代號、假金額）；寫檢討只用假的提供者，不叫任何 AI。
"""
import importlib
import json
import os

from .conftest import OTHER, OWNER

TRADES_CSV = ("日期,代號,名稱,買賣,股數,價格,手續費,交易稅\r\n"
              "2030-01-02,TST1,範例甲,買進,100,50,7,0\r\n"
              "2030-01-03,TST1,範例甲,賣出,40,60,3,7\r\n")


def engine(bot, name):
    return importlib.import_module(f"{bot.module.__name__}.engine.{name}")


def put(bot, user, name, text):
    path = bot.pack.path_of(user, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def securities(bot, user):
    refdata, db = engine(bot, "services.refdata"), engine(bot, "db")
    with bot.module.session.open_book(bot.pack, user) as book:
        with db.write(book.conn) as w:
            refdata.add_account(w, name="證券-投資", kind="securities", asset_class="invest")


def test_import_statement_reads_own_csv_and_never_doubles(make_money):
    bot = make_money()
    securities(bot, OWNER)
    put(bot, OWNER, "mine.csv", TRADES_CSV)
    out = bot.command("import_statement", OWNER, "mine.csv dry_run=yes")
    assert out.startswith("試算") and "trades_csv" in out and "新記 2" in out
    out = bot.command("import_statement", OWNER, "mine.csv")
    assert "新記 2" in out
    again = bot.command("import_statement", OWNER, "mine.csv")
    assert "新記 0" in again and "之前匯過 2" in again
    # 別人的帳本完全沒動到；他也讀不到我的檔
    out = bot.command("import_statement", OTHER, "mine.csv")
    assert out.startswith("❌") and "找不到檔案" in out


def test_import_statement_errors_are_plain_and_have_no_paths(make_money):
    bot = make_money()
    home = bot.pack.path_of(OWNER)
    for text, word in (("nope.csv", "找不到檔案"), ("../../outside.csv", "自己的資料夾"), ("mine.csv format=no_such", "沒有")):
        put(bot, OWNER, "mine.csv", TRADES_CSV)
        out = bot.command("import_statement", OWNER, text)
        assert out.startswith("❌") and word in out, text
        assert home not in out and os.sep + "files" not in out
    out = bot.command("import_statement", OWNER, "mine.csv")
    assert out.startswith("❌") and "證券帳戶" in out                          # 還沒有證券帳戶：講白話


def test_formats_lists_builtin_and_own_and_broken(make_money):
    bot = make_money()
    put(bot, OWNER, "formats/mine.json", json.dumps({"schema": 1, "id": "mine", "kind": "trades"}))
    out = bot.command("formats", OWNER)
    assert "tw_trades_html" in out and "trades_csv" in out and "內建" in out
    assert "⚠️ mine.json 不能用" in out
    assert "mine.json" not in bot.command("formats", OTHER)                   # 自己的描述檔只有自己看得到


def test_review_run_without_provider_is_llm_unavailable(make_money):
    bot = make_money()
    bot.command("add", OWNER, "120 午餐")
    out = bot.command("review_run", OWNER)
    assert out.startswith("❌") and "還沒有接上會寫檢討的 AI" in out and "/money_review_save" in out


def test_review_run_uses_the_registered_provider(make_money):
    bot = make_money()
    bot.command("add", OWNER, "120 午餐")
    calls = []

    def fake(prompt, schema, opts):
        calls.append(prompt)
        return {"structured": {"grade": "B", "title": "範例標題", "summary": "範例摘要", "good": ["範例"], "watch": [],
                               "strategy": [], "suggestion": None}, "model": "fake", "cost_usd": None}

    bot.module.session.writer.register(bot.pack, fake)
    out = bot.command("review_run", OWNER, "kind=week")
    assert "寫好了" in out and "範例標題" in out and len(calls) == 1
    bot.module.session.writer.register(bot.pack, None)
    assert bot.command("review_run", OWNER, "kind=week").startswith("❌")
