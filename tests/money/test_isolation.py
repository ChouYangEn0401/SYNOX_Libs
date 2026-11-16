"""多使用者隔離的閘門（packs/money/CONTRACT.md「隔離九條」）。

1. 兩個人交錯跑每一個讀、每一個畫面動作、每一個指令（名字從 pack.json 與指令表抓，新加的自動被測到）：
   B 讀到、收到的任何東西都沒有 A 的字；B 拿 A 的編號去改、去刪，A 的資料夾一個位元都不變。
2. A 把每個動作都做過一次，B 的資料夾裡每個檔的雜湊都不變。
3. 兩條執行緒同時各記 200 筆：兩本帳的筆數與合計各自正確。
4. 沒設定帳本資料夾就叫引擎 → 一定丟錯（不會退回任何共用位置）。
5. 個人版（單一使用者）照常能記能讀。
靜態的那幾條（不用共用資料夾、pack.store 只准 .of、模組層級沒有快取、碰檔案只准在哪幾個檔）在 tests/test_gates.py。
資料一律是假的（「範例便當店」「現金」「120」）。
"""

import hashlib
import importlib
import json
import os
import threading

import pytest

from synox.packs import kit

from .conftest import OTHER, OWNER, U, load_money, read_manifest

MARK = "範例隔離甲店"            # 只有 A 會寫進帳本的字；B 那邊出現就是漏了
MANIFEST = read_manifest()
APP = MANIFEST.web["app"]
READS = list(APP["reads"])
ACTIONS = list(APP["actions"])
COMMANDS = [c.name for m in load_money().DOMAINS for c in m.COMMANDS]


def eng(bot, name):
    return importlib.import_module(f"{bot.module.__name__}.engine.{name}")


def folder_of(bot, uid):
    return bot.pack.path_of(uid)


def hashes(path):
    """資料夾裡每個檔 → sha256（沒有資料夾回空的）。"""
    out = {}
    for base, _dirs, files in os.walk(path):
        for f in files:
            full = os.path.join(base, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, path)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def cmd_of(bot, name):
    return next(c for m in bot.module.DOMAINS for c in m.COMMANDS if c.name == name)


def ids_of(bot, uid):
    with bot.module.session.open_book(bot.pack, U(uid)) as book:
        txn = book.conn.execute("SELECT id FROM transactions ORDER BY rowid DESC LIMIT 1").fetchone()
        batch = book.conn.execute("SELECT id FROM batches ORDER BY rowid DESC LIMIT 1").fetchone()
    return (txn[0] if txn else ""), (batch[0] if batch else "")


def generic_values(bot, name, uid):
    """一個動作／指令的每個欄位都給一個看起來合理的值；編號類的欄位給 uid 那個人的最新一筆（拿別人的編號試試看）。"""
    txn, batch = ids_of(bot, uid)
    values = {"amount": "120", "date": "2030-01-14", "desc": MARK, "category": "午餐", "account": "現金", "note": MARK,
              "name": MARK, "text": MARK, "period": "2030-01", "id": str(txn), "batch": str(batch), "kind": "expense"}
    if name in ("revert",):
        values["id"] = str(batch)
    cmd = cmd_of(bot, name)
    return {f.name: values.get(f.name, "1") for f in cmd.fields}


def call(fn, *args):
    """跑一次；工具給人看的錯（kit.AppError）算正常結果，回它的訊息；其他的錯讓測試失敗。"""
    try:
        return fn(*args)
    except kit.AppError as e:
        return {"refused": e.message}


def seed_owner(bot):
    """A 的帳本：記幾筆（含 MARK）、寫一段小日記、加一個帳戶。"""
    bot.command("add", OWNER, f"120 午餐 {MARK}")
    bot.command("add", OWNER, f"300 晚餐 {MARK} date=2030-01-14")
    bot.act("note", OWNER, {"date": "2030-01-14", "text": MARK})
    bot.read("meta", OTHER)                                       # B 也有一本（空的）帳本
    assert MARK in json.dumps(bot.read("passbook", OWNER), ensure_ascii=False)


def text_of(value):
    return json.dumps(value, ensure_ascii=False, default=str)


@pytest.fixture
def bot(make_money):
    bot = make_money()
    seed_owner(bot)
    return bot


def test_names_come_from_the_manifest():
    assert len(READS) >= 20 and len(ACTIONS) >= 30 and set(ACTIONS) <= set(COMMANDS)


@pytest.mark.parametrize("name", READS)
def test_other_user_never_reads_my_words(bot, name):
    for params in ({}, {"id": ids_of(bot, OWNER)[0]}, {"period": "2030-01"}):
        assert MARK not in text_of(call(bot.read, name, OTHER, params)), (name, params)


@pytest.mark.parametrize("name", ACTIONS)
def test_other_users_action_with_my_ids_leaves_my_folder_alone(bot, name):
    """B 拿 A 的編號（帳、批次）去做每一個動作：A 的資料夾一個位元都不變，B 拿到的回覆裡沒有 A 的字。"""
    mine = hashes(folder_of(bot, OWNER))
    values = generic_values(bot, name, OWNER)
    values = {k: (v if v != MARK else "範例乙") for k, v in values.items()}
    out = call(bot.act, name, OTHER, values)
    assert MARK not in text_of(out), name
    assert hashes(folder_of(bot, OWNER)) == mine, name


@pytest.mark.parametrize("name", COMMANDS)
def test_other_users_commands_never_show_my_words(bot, name):
    """B 打每一個指令（不帶參數、或帶 A 的編號）：回覆裡沒有 A 的字；A 的資料夾不變。"""
    mine = hashes(folder_of(bot, OWNER))
    txn = ids_of(bot, OWNER)[0]
    for text in ("", str(txn)):
        reply = bot.command(name, OTHER, text)
        assert MARK not in reply, (name, text, reply)
    assert hashes(folder_of(bot, OWNER)) == mine, name


@pytest.mark.parametrize("name", ACTIONS)
def test_my_actions_never_touch_the_other_folder(bot, name):
    """A 做每一個動作（合理的值，失敗也沒關係）：B 的資料夾裡每個檔的雜湊都不變。"""
    theirs = hashes(folder_of(bot, OTHER))
    assert theirs                                                     # B 真的有一本帳
    call(bot.act, name, OWNER, generic_values(bot, name, OWNER))
    assert hashes(folder_of(bot, OTHER)) == theirs, name


def test_interleaved_users_keep_separate_books(bot):
    """交錯記帳、交錯讀：每個人只看到自己的。"""
    for i in range(5):
        bot.command("add", OWNER, f"{100 + i} 午餐 {MARK}")
        bot.act("add", OTHER, {"amount": str(10 + i), "desc": "範例乙店"})
        assert MARK not in text_of(bot.read("passbook", OTHER))
        assert "範例乙店" not in text_of(bot.read("passbook", OWNER))
    with bot.module.session.open_book(bot.pack, U(OWNER)) as book:
        assert book.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 7
    with bot.module.session.open_book(bot.pack, U(OTHER)) as book:
        rows = book.conn.execute("SELECT COUNT(*), SUM(amount) FROM transactions").fetchone()
        assert tuple(rows) == (5, 60)


def test_two_threads_two_hundred_entries_each(make_money):
    bot = make_money()
    add = cmd_of(bot, "add")
    errors = []

    def worker(uid, scale):
        try:
            for n in range(1, 201):
                with bot.module.session.open_book(bot.pack, U(uid)) as book:
                    add.run(book, {"amount": str(n * scale), "desc": f"範例店{n}", "category": "午餐"})
        except Exception as e:                                        # 交給主執行緒判
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(OWNER, 1)), threading.Thread(target=worker, args=(OTHER, 2))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    assert not errors, errors[:3]
    for uid, scale in ((OWNER, 1), (OTHER, 2)):
        with bot.module.session.open_book(bot.pack, U(uid)) as book:
            rows = book.conn.execute("SELECT COUNT(*), SUM(amount) FROM transactions").fetchone()
        assert tuple(rows) == (200, 20100 * scale), uid


def test_engine_without_a_folder_refuses():
    module = load_money()
    db = importlib.import_module(f"{module.__name__}.engine.db")
    errors = importlib.import_module(f"{module.__name__}.engine.errors")
    paths = importlib.import_module(f"{module.__name__}.engine.paths")
    assert paths.current_or_none() is None
    with pytest.raises(errors.NoHome):
        db.connect()


def test_open_book_needs_an_integer_user(make_money):
    bot = make_money()
    for who in (None, "100", U("100"), True):
        with pytest.raises(bot.module.session.NoUser):
            with bot.module.session.open_book(bot.pack, who):
                pass


def test_personal_mode_single_user_works(make_money):
    bot = make_money(per_user=False)
    assert "120" in bot.command("add", OWNER, "120 午餐 範例便當店")
    bot.act("add", OWNER, {"amount": "80", "desc": "範例飲料店"})
    items = bot.read("passbook", OWNER)["items"]
    assert {i["raw"] for i in items} >= {120, 80}
