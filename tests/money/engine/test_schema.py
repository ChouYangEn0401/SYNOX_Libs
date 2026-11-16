"""起始 schema（engine/schema.sql）：每張表的欄位、索引、唯一鍵；版本從 1 開始；沒設定帳本資料夾就不開帳本。"""
import sqlite3
import threading

import pytest

from money_engine import db, paths, seed
from money_engine.errors import NoHome
from money_engine.services import refdata

# 每張表的欄位（照順序）。舊帳本一條一條 migration 加出來的最後樣子，合成一份；之後只加不改。
COLUMNS = {
    "settings": "key value",
    "meta": "key value",
    "periods": "key start_date end_date",
    "buckets": "id name sort is_living label locked active rollover rollover_from flex",
    "category_groups": "id name bucket_id sort active icon",
    "categories": "id name group_id kind keywords meal_slot default_budget transfer_purpose sort active cap_basis icon "
                  "cap_mode bucket_override system_key",
    "accounts": "id name kind purpose aliases liquid floor_amount target_amount card_closing_day card_due_day card_limit "
                "pay_from_account_id sort active note reserve_group asset_class bucket_id",
    "batches": "id at actor source command note reverts reverted_by",
    "transactions": "id date time kind amount currency foreign_amount category_id account_id to_account_id purpose "
                    "counterparty description merchant note confidence status financed group_id debt_id statement_line_id "
                    "batch_id source source_ref item_idx payload_hash created_at updated_at for_period nature",
    "txn_items": "id txn_id name qty amount",
    "day_notes": "date note updated_at batch_id",
    "audit_log": "id at batch_id actor action entity entity_id before after",
    "period_plans": "period income_expected buckets group_budgets source review_id updated_at",
    "dedup_pairs": "id a_id b_id score reason status created_at resolved_at resolved_by",
    "attachments": "id txn_id sha256 path mime original_name size created_at",
    "card_statements": "id account_id period_start closing_date due_date total min_payment status paid_txn_id source "
                       "source_ref lines_hash batch_id created_at updated_at",
    "statement_lines": "id statement_id seq post_date txn_date description amount currency foreign_amount line_type "
                       "installment_no installment_total matched_txn_id debt_schedule_id status manual score note",
    "merchant_aliases": "id pattern merchant category_id created_at",
    "debts": "id name kind principal periods per_period nature account_id lender_account_id start_date purchase_txn_id "
             "note active source source_ref created_at",
    "debt_schedule": "id debt_id no due_date amount paid_txn_id statement_line_id status note",
    "balance_snapshots": "id account_id as_of balance source note batch_id created_at",
    "recurring": "id name kind category_id account_id to_account_id amount day_of_month cadence template active note "
                 "created_at bucket_id",
    "bills": "id bill_type period_start period_end usage unit amount due_date txn_id summer note source source_ref created_at",
    "invest_monthly": "id month account_id pnl market_value net_deposit note source created_at updated_at",
    "holdings_snapshots": "id as_of account_id bucket name symbol market_value cost note created_at",
    "reviews": "id kind key start_date end_date status grade title summary good watch strategy suggestion problems metrics "
               "model cost_usd attempts error next_attempt_after started_at finished_at sent_at reply replied_at "
               "applied_at applied_period source created_at updated_at",
    "notifications": "id kind key title text review_id created_at sent_at",
    "bucket_setaside": "id period bucket_id amount created_at txn_id dest_account_id dest_label",
    "bucket_skips": "id period bucket_id created_at",
    "bucket_moves": "id period from_bucket to_bucket amount note created_at",
    "books": "id account_id name kind status start_date end_date capital max_loss per_trade_loss tithe_rate result "
             "tithe_amount tithe_done_at note closed_at archived_at batch_id created_at updated_at",
    "trades": "id date account_id symbol name action shares price gross fee tax net note source source_ref batch_id "
              "created_at book_id time day_trade stop_price violation instrument multiplier",
    "quotes": "id symbol price as_of source batch_id created_at",
    "cash_leftover": "period_key amount balance keep card_keep living_left recorded_on batch_id",
    "holdings_counts": "id account_id symbol as_of shares note source batch_id created_at",
}

INDEXES = {
    "ix_txn_date": ("transactions", False), "ix_txn_account": ("transactions", False),
    "ux_txn_source": ("transactions", True), "ix_items_txn": ("txn_items", False),
    "ix_audit_entity": ("audit_log", False), "ix_audit_batch": ("audit_log", False),
    "ix_dedup_status": ("dedup_pairs", False), "ix_attach_sha": ("attachments", False),
    "ix_attach_txn": ("attachments", False), "ix_lines_statement": ("statement_lines", False),
    "ix_lines_txn": ("statement_lines", False), "ix_schedule_due": ("debt_schedule", False),
    "ix_holdings_asof": ("holdings_snapshots", False), "idx_bucket_moves_period": ("bucket_moves", False),
    "ix_trade_date": ("trades", False), "ux_trade_source": ("trades", True), "ix_trade_book": ("trades", False),
    "ix_trade_account": ("trades", False), "ix_quote_symbol": ("quotes", False),
    "ux_category_system_key": ("categories", True),
}

# 表格自己宣告的唯一鍵（PRIMARY KEY 以外；sqlite 會建 sqlite_autoindex_*）
UNIQUE = {
    "periods": [("start_date",)], "buckets": [("name",)], "category_groups": [("name",)], "categories": [("name",)],
    "accounts": [("name",)], "dedup_pairs": [("a_id", "b_id")], "card_statements": [("account_id", "closing_date")],
    "merchant_aliases": [("pattern",)], "debt_schedule": [("debt_id", "no")], "balance_snapshots": [("account_id", "as_of")],
    "recurring": [("name",)], "bills": [("bill_type", "period_end")], "invest_monthly": [("month", "account_id")],
    "reviews": [("kind", "key")], "notifications": [("kind", "key")], "bucket_setaside": [("period", "bucket_id")],
    "bucket_skips": [("period", "bucket_id")], "books": [("account_id", "name")],
    "holdings_counts": [("account_id", "symbol", "as_of")],
}


def test_every_table_and_column(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == set(COLUMNS)
    for table, cols in COLUMNS.items():
        assert [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")] == cols.split(), table


def test_indexes_and_unique_keys(conn):
    named = {r["name"]: (r["tbl_name"], r["sql"]) for r in conn.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
    assert set(named) == set(INDEXES)
    for name, (table, unique) in INDEXES.items():
        assert named[name][0] == table
        assert named[name][1].upper().startswith("CREATE UNIQUE") == unique, name
    for table in COLUMNS:
        found = sorted(tuple(c["name"] for c in conn.execute(f"PRAGMA index_info({r['name']})"))
                       for r in conn.execute(f"PRAGMA index_list({table})") if r["origin"] == "u")
        assert found == sorted(UNIQUE.get(table, [])), table


def test_version_starts_at_one_and_newer_db_refuses_writes(conn):
    assert db.schema_version(conn) == len(db.MIGRATIONS) == 1
    conn.execute("PRAGMA user_version = 2")
    conn.close()
    again = db.connect()
    assert again.newer_than_code
    with pytest.raises(Exception) as e:
        with db.write(again):
            pass
    assert e.value.code == "conflict"
    again.close()


def test_no_home_means_no_book(tmp_path):
    """沒有 use_home 的 context：connect() 一定丟錯，不會退回任何共用的資料夾。"""
    errors = []

    def outside():                                   # 新的執行緒從空的 context 開始
        try:
            db.connect()
        except NoHome as e:
            errors.append(e)

    t = threading.Thread(target=outside)
    t.start()
    t.join()
    assert len(errors) == 1 and errors[0].code == "no_home"
    assert "Users" not in errors[0].message and str(tmp_path) not in errors[0].message


def test_each_context_sees_its_own_home(tmp_path):
    seen = {}

    def run(name):
        with paths.use_home(tmp_path / name):
            c = db.connect()
            seen[name] = db.main_db_file(c)
            c.close()

    threads = [threading.Thread(target=run, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen["a"].parent.name == "a" and seen["b"].parent.name == "b"


def test_generic_seed(generic_home):
    c = db.connect()
    buckets = {r["id"]: dict(r) for r in c.execute("SELECT * FROM buckets")}
    assert set(buckets) == {"giving", "fixed", "saving", "living", "invest", "debt", "trading"}
    assert buckets["giving"]["label"] == "捐贈" and not buckets["giving"]["active"]
    assert buckets["fixed"]["locked"] and buckets["living"]["is_living"] and not buckets["trading"]["active"]
    keys = {r["system_key"]: r["name"] for r in c.execute("SELECT system_key, name FROM categories WHERE system_key IS NOT NULL")}
    assert keys["card_payment"] == "繳卡費" and keys["lend"] == "借出" and keys["meal.lunch"] == "午餐"
    assert set(keys) <= set(refdata.SYSTEM_KEYS)
    settings = {r["key"] for r in c.execute("SELECT key FROM settings")}
    assert not {"review.model", "import.invest_account", "import.trading_account"} & settings
    assert {"currency.code", "currency.symbol", "currency.decimals"} <= settings
    assert c.execute("SELECT asset_class FROM accounts WHERE name = '現金'").fetchone()[0] == "cash"
    assert "giving" in seed.optional() and "tithe" not in seed.optional()
    c.close()


def test_seed_param_beats_folder_file(home, tmp_path):
    other = tmp_path / "other"
    with paths.use_home(other):
        c = db.connect(seed="generic")
        assert c.execute("SELECT 1 FROM category_groups WHERE name = '食'").fetchone()
        c.close()
    c = db.connect()                                  # 這個資料夾有自己的 categories.json（測試用的假資料）
    assert c.execute("SELECT 1 FROM category_groups WHERE name = '三餐'").fetchone()
    c.close()


def test_system_key_can_rename_but_not_disable(conn):
    lunch = refdata.system_category(conn, "meal.lunch")
    with db.write(conn) as w:
        refdata.update_category(w, lunch["id"], name="中飯")
    assert refdata.system_category(conn, "meal.lunch")["name"] == "中飯"
    assert refdata.system_name(conn, "meal.lunch") == "中飯"
    from money_engine.errors import InvalidInput
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            refdata.update_category(w, lunch["id"], active=0)
    pay = refdata.system_category(conn, "card_payment")
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            refdata.update_category(w, pay["id"], kind="expense")
    with db.write(conn) as w:                         # 代號本身不能被改掉
        refdata.update_category(w, pay["id"], system_key="other")
    assert refdata.system_category(conn, "card_payment")["id"] == pay["id"]


def test_sqlite_is_plain_file(home, conn):
    """帳本是這個資料夾裡的一個 SQLite 檔（沒有加密；備份與封存也在這裡）。"""
    f = db.main_db_file(conn)
    assert f.parent == home and f.name == db.DB_NAME
    assert sqlite3.connect(str(f)).execute("SELECT COUNT(*) FROM accounts").fetchone()[0] >= 1
