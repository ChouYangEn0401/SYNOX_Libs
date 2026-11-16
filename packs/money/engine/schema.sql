-- 記帳引擎的起始 schema（版本 1）。之後的改動一律在 db.py 的 MIGRATIONS 後面加新的一條，只加不改。
-- 每一句以分號結尾；字串裡不放分號（db._split_sql 用分號切句子，在同一個交易裡跑）。

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT INTO meta (key, value) VALUES ('write_seq', 0);

CREATE TABLE periods (key TEXT PRIMARY KEY, start_date TEXT NOT NULL UNIQUE, end_date TEXT NOT NULL);

-- 用途（格子）：核心六個 id 是 giving、fixed、saving、living、invest、debt；trading 是資金操盤（預設不開）；自己加的是 extraN
CREATE TABLE buckets (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, sort INTEGER NOT NULL, is_living INTEGER NOT NULL DEFAULT 0,
  label TEXT, locked INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
  rollover INTEGER NOT NULL DEFAULT 0, rollover_from TEXT, flex INTEGER NOT NULL DEFAULT 0);

CREATE TABLE category_groups (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, bucket_id TEXT REFERENCES buckets(id),
  sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, icon TEXT NOT NULL DEFAULT '');

-- system_key：程式用代號找類別（借出＝lend、繳卡費＝card_payment…，見 services/refdata.SYSTEM_KEYS），名字可以改
CREATE TABLE categories (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, group_id INTEGER NOT NULL REFERENCES category_groups(id),
  kind TEXT NOT NULL CHECK (kind IN ('expense','income','transfer')),
  keywords TEXT NOT NULL DEFAULT '', meal_slot TEXT CHECK (meal_slot IN ('breakfast','lunch','dinner')),
  default_budget INTEGER, transfer_purpose TEXT, sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
  cap_basis TEXT CHECK (cap_basis IN ('period','day')), icon TEXT NOT NULL DEFAULT '',
  cap_mode TEXT NOT NULL DEFAULT 'period', bucket_override TEXT REFERENCES buckets(id),
  system_key TEXT);
CREATE UNIQUE INDEX ux_category_system_key ON categories(system_key) WHERE system_key IS NOT NULL;

CREATE TABLE accounts (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('cash','bank','credit_card','securities','e_wallet','receivable','loan','pension')),
  purpose TEXT NOT NULL DEFAULT '', aliases TEXT NOT NULL DEFAULT '', liquid INTEGER NOT NULL DEFAULT 1,
  floor_amount INTEGER, target_amount INTEGER,
  card_closing_day INTEGER, card_due_day INTEGER, card_limit INTEGER,
  pay_from_account_id INTEGER REFERENCES accounts(id),
  sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '',
  reserve_group TEXT, asset_class TEXT NOT NULL DEFAULT '', bucket_id TEXT REFERENCES buckets(id));

CREATE TABLE batches (
  id TEXT PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, source TEXT NOT NULL, command TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', reverts TEXT, reverted_by TEXT);

CREATE TABLE transactions (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, time TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('expense','income','transfer')),
  amount INTEGER NOT NULL, currency TEXT, foreign_amount TEXT,
  category_id INTEGER REFERENCES categories(id),
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  to_account_id INTEGER REFERENCES accounts(id),
  purpose TEXT, counterparty TEXT,
  description TEXT NOT NULL DEFAULT '', merchant TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  confidence REAL, status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','deleted')),
  financed INTEGER NOT NULL DEFAULT 0, group_id TEXT, debt_id INTEGER, statement_line_id INTEGER,
  batch_id TEXT NOT NULL REFERENCES batches(id),
  source TEXT NOT NULL, source_ref TEXT, item_idx INTEGER NOT NULL DEFAULT 0, payload_hash TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  for_period TEXT, nature TEXT);
CREATE INDEX ix_txn_date ON transactions(date);
CREATE INDEX ix_txn_account ON transactions(account_id, date);
CREATE UNIQUE INDEX ux_txn_source ON transactions(source, source_ref, item_idx) WHERE source_ref IS NOT NULL;

CREATE TABLE txn_items (
  id INTEGER PRIMARY KEY, txn_id TEXT NOT NULL REFERENCES transactions(id), name TEXT NOT NULL,
  qty REAL, amount INTEGER);
CREATE INDEX ix_items_txn ON txn_items(txn_id);

CREATE TABLE day_notes (date TEXT PRIMARY KEY, note TEXT NOT NULL, updated_at TEXT NOT NULL, batch_id TEXT);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, batch_id TEXT NOT NULL REFERENCES batches(id), actor TEXT NOT NULL,
  action TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT NOT NULL, before TEXT, after TEXT);
CREATE INDEX ix_audit_entity ON audit_log(entity, entity_id);
CREATE INDEX ix_audit_batch ON audit_log(batch_id);

CREATE TABLE period_plans (
  period TEXT PRIMARY KEY, income_expected INTEGER, buckets TEXT NOT NULL DEFAULT '{}',
  group_budgets TEXT NOT NULL DEFAULT '{}', source TEXT NOT NULL DEFAULT 'manual', review_id TEXT,
  updated_at TEXT NOT NULL);

-- 去重與收據
CREATE TABLE dedup_pairs (
  id INTEGER PRIMARY KEY, a_id TEXT NOT NULL REFERENCES transactions(id), b_id TEXT NOT NULL REFERENCES transactions(id),
  score REAL NOT NULL, reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','kept_both','merged','deleted')),
  created_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT, UNIQUE (a_id, b_id));
CREATE INDEX ix_dedup_status ON dedup_pairs(status);
CREATE TABLE attachments (
  id INTEGER PRIMARY KEY, txn_id TEXT NOT NULL REFERENCES transactions(id), sha256 TEXT NOT NULL, path TEXT NOT NULL,
  mime TEXT, original_name TEXT, size INTEGER, created_at TEXT NOT NULL);
CREATE INDEX ix_attach_sha ON attachments(sha256);
CREATE INDEX ix_attach_txn ON attachments(txn_id);

-- 信用卡
CREATE TABLE card_statements (
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), period_start TEXT,
  closing_date TEXT NOT NULL, due_date TEXT, total INTEGER NOT NULL, min_payment INTEGER,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','balanced','paid')), paid_txn_id TEXT,
  source TEXT, source_ref TEXT, lines_hash TEXT, batch_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (account_id, closing_date));
CREATE TABLE statement_lines (
  id INTEGER PRIMARY KEY, statement_id INTEGER NOT NULL REFERENCES card_statements(id), seq INTEGER NOT NULL,
  post_date TEXT, txn_date TEXT NOT NULL, description TEXT NOT NULL, amount INTEGER NOT NULL,
  currency TEXT, foreign_amount TEXT,
  line_type TEXT NOT NULL CHECK (line_type IN ('purchase','installment','fee','refund','payment')),
  installment_no INTEGER, installment_total INTEGER, matched_txn_id TEXT REFERENCES transactions(id),
  debt_schedule_id INTEGER,
  status TEXT NOT NULL DEFAULT 'missing'
    CHECK (status IN ('matched','missing','diff','ignored','installment','fee','payment')),
  manual INTEGER NOT NULL DEFAULT 0, score REAL, note TEXT NOT NULL DEFAULT '');
CREATE INDEX ix_lines_statement ON statement_lines(statement_id);
CREATE INDEX ix_lines_txn ON statement_lines(matched_txn_id);
CREATE TABLE merchant_aliases (
  id INTEGER PRIMARY KEY, pattern TEXT NOT NULL UNIQUE, merchant TEXT NOT NULL,
  category_id INTEGER REFERENCES categories(id), created_at TEXT NOT NULL);

-- 負債
CREATE TABLE debts (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('card_installment','self_loan','credit_loan')),
  principal INTEGER NOT NULL, periods INTEGER NOT NULL, per_period INTEGER NOT NULL,
  nature TEXT NOT NULL DEFAULT 'positive' CHECK (nature IN ('positive','negative','wear')),
  account_id INTEGER REFERENCES accounts(id), lender_account_id INTEGER REFERENCES accounts(id),
  start_date TEXT NOT NULL, purchase_txn_id TEXT REFERENCES transactions(id), note TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, source TEXT, source_ref TEXT, created_at TEXT NOT NULL);
CREATE TABLE debt_schedule (
  id INTEGER PRIMARY KEY, debt_id INTEGER NOT NULL REFERENCES debts(id), no INTEGER NOT NULL, due_date TEXT NOT NULL,
  amount INTEGER NOT NULL, paid_txn_id TEXT REFERENCES transactions(id), statement_line_id INTEGER,
  status TEXT NOT NULL DEFAULT 'due' CHECK (status IN ('due','paid','skipped')), note TEXT NOT NULL DEFAULT '',
  UNIQUE (debt_id, no));
CREATE INDEX ix_schedule_due ON debt_schedule(due_date);

-- 餘額核對、固定項目、帳單
CREATE TABLE balance_snapshots (
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), as_of TEXT NOT NULL,
  balance INTEGER NOT NULL, source TEXT, note TEXT NOT NULL DEFAULT '', batch_id TEXT, created_at TEXT NOT NULL,
  UNIQUE (account_id, as_of));
CREATE TABLE recurring (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK (kind IN ('expense','income','transfer')),
  category_id INTEGER REFERENCES categories(id), account_id INTEGER REFERENCES accounts(id),
  to_account_id INTEGER REFERENCES accounts(id), amount INTEGER, day_of_month INTEGER NOT NULL DEFAULT 5,
  cadence TEXT NOT NULL DEFAULT 'monthly' CHECK (cadence IN ('monthly','bimonthly')),
  template TEXT NOT NULL DEFAULT '{}', active INTEGER NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, bucket_id TEXT REFERENCES buckets(id));
CREATE TABLE bills (
  id INTEGER PRIMARY KEY, bill_type TEXT NOT NULL, period_start TEXT, period_end TEXT NOT NULL, usage REAL, unit TEXT,
  amount INTEGER NOT NULL, due_date TEXT, txn_id TEXT REFERENCES transactions(id), summer INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '', source TEXT, source_ref TEXT, created_at TEXT NOT NULL,
  UNIQUE (bill_type, period_end));

-- 投資（每月損益、部位快照）
CREATE TABLE invest_monthly (
  id INTEGER PRIMARY KEY, month TEXT NOT NULL, account_id INTEGER NOT NULL REFERENCES accounts(id), pnl INTEGER NOT NULL,
  market_value INTEGER, net_deposit INTEGER, note TEXT NOT NULL DEFAULT '', source TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, UNIQUE (month, account_id));
CREATE TABLE holdings_snapshots (
  id INTEGER PRIMARY KEY, as_of TEXT NOT NULL, account_id INTEGER NOT NULL REFERENCES accounts(id), bucket TEXT NOT NULL,
  name TEXT NOT NULL, symbol TEXT, market_value INTEGER NOT NULL, cost INTEGER, note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL);
CREATE INDEX ix_holdings_asof ON holdings_snapshots(account_id, as_of);

-- 檢討與要送的通知
CREATE TABLE reviews (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('week','month')), key TEXT NOT NULL,
  start_date TEXT NOT NULL, end_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running','done','failed','deferred','skipped')),
  grade TEXT, title TEXT, summary TEXT, good TEXT, watch TEXT, strategy TEXT, suggestion TEXT, problems TEXT,
  metrics TEXT, model TEXT, cost_usd REAL, attempts INTEGER NOT NULL DEFAULT 0, error TEXT, next_attempt_after TEXT,
  started_at TEXT, finished_at TEXT, sent_at TEXT, reply TEXT, replied_at TEXT, applied_at TEXT, applied_period TEXT,
  source TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (kind, key));
CREATE TABLE notifications (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,
  review_id INTEGER REFERENCES reviews(id), created_at TEXT NOT NULL, sent_at TEXT,
  UNIQUE (kind, key));

-- 格子每期的處理：撥額度、跳過、挪用
CREATE TABLE bucket_setaside (
  id INTEGER PRIMARY KEY, period TEXT NOT NULL, bucket_id TEXT NOT NULL REFERENCES buckets(id),
  amount INTEGER NOT NULL, created_at TEXT NOT NULL,
  txn_id TEXT, dest_account_id INTEGER REFERENCES accounts(id), dest_label TEXT,
  UNIQUE (period, bucket_id));
CREATE TABLE bucket_skips (
  id INTEGER PRIMARY KEY, period TEXT NOT NULL, bucket_id TEXT NOT NULL REFERENCES buckets(id),
  created_at TEXT NOT NULL, UNIQUE (period, bucket_id));
CREATE TABLE bucket_moves (
  id INTEGER PRIMARY KEY, period TEXT NOT NULL,
  from_bucket TEXT NOT NULL REFERENCES buckets(id), to_bucket TEXT NOT NULL REFERENCES buckets(id),
  amount INTEGER NOT NULL CHECK (amount > 0), note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE INDEX idx_bucket_moves_period ON bucket_moves (period);

-- 撲滿：倉、交易明細、塞進來的價格、現金層每期剩下的、對過的股數
CREATE TABLE books (
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'real' CHECK (kind IN ('real','sim','history')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed','archived')),
  start_date TEXT NOT NULL, end_date TEXT,
  capital INTEGER NOT NULL DEFAULT 0, max_loss INTEGER, per_trade_loss INTEGER, tithe_rate REAL,
  result INTEGER, tithe_amount INTEGER, tithe_done_at TEXT, note TEXT NOT NULL DEFAULT '',
  closed_at TEXT, archived_at TEXT, batch_id TEXT NOT NULL REFERENCES batches(id),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (account_id, name));
CREATE TABLE trades (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  symbol TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL CHECK (action IN ('buy','sell','dividend','fee','other')),
  shares INTEGER NOT NULL DEFAULT 0, price TEXT,
  gross INTEGER NOT NULL DEFAULT 0, fee INTEGER NOT NULL DEFAULT 0, tax INTEGER NOT NULL DEFAULT 0,
  net INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual', source_ref TEXT,
  batch_id TEXT NOT NULL REFERENCES batches(id), created_at TEXT NOT NULL,
  book_id INTEGER REFERENCES books(id), time TEXT, day_trade INTEGER NOT NULL DEFAULT 0, stop_price TEXT, violation TEXT,
  instrument TEXT NOT NULL DEFAULT 'stock' CHECK (instrument IN ('stock','future','option')),
  multiplier INTEGER NOT NULL DEFAULT 1 CHECK (multiplier >= 1));
CREATE INDEX ix_trade_date ON trades(date);
CREATE UNIQUE INDEX ux_trade_source ON trades(source, source_ref) WHERE source_ref IS NOT NULL;
CREATE INDEX ix_trade_book ON trades(book_id, date);
CREATE INDEX ix_trade_account ON trades(account_id, date);
CREATE TABLE quotes (
  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, price TEXT NOT NULL, as_of TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'cli', batch_id TEXT NOT NULL REFERENCES batches(id), created_at TEXT NOT NULL);
CREATE INDEX ix_quote_symbol ON quotes(symbol, as_of);
CREATE TABLE cash_leftover (
  period_key TEXT PRIMARY KEY, amount INTEGER NOT NULL, balance INTEGER NOT NULL, keep INTEGER NOT NULL,
  card_keep INTEGER NOT NULL, living_left INTEGER NOT NULL, recorded_on TEXT NOT NULL,
  batch_id TEXT NOT NULL REFERENCES batches(id));
CREATE TABLE holdings_counts (
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), symbol TEXT NOT NULL,
  as_of TEXT NOT NULL, shares INTEGER NOT NULL CHECK (shares >= 0), note TEXT NOT NULL DEFAULT '',
  source TEXT, batch_id TEXT, created_at TEXT NOT NULL, UNIQUE (account_id, symbol, as_of));
