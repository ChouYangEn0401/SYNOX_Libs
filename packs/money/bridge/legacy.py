"""舊版（0.3.1）的資料第一次自動轉進新帳本（W6）。

0.3.1 把每個人的帳存在 pack.store.of(user)（SYNOX 保管箱，一份 JSON）：

    第 2 版 {"schema": 2, "next_id": 下一個編號, "budget": 數字或 null,
            "entries": [{id, kind, amount, category, memo, date, at, account?}],
            "categories": [{name, group, kind, keywords, cap?}]}
    第 1 版（0.1.x）沒有 schema／id／kind／date：照 0.3.1 的 upgrade() 補（照順序給編號、kind=expense、date 從 at 算）。

什麼時候轉：bridge/session.py 每次開好帳本都叫 compat.on_open → 這裡的 maybe_convert。
這本帳**還沒有任何一批寫入**、而且這個人有舊資料，才轉（新帳本開好、第一次用的時候）。

怎麼轉（整段在**一批寫入**裡，可以整批撤銷）：
- 類別：名字、大類、種類、關鍵字照搬；已經有同名同種類的（起始類別）就只補關鍵字；同名不同種類的另取名「名字（舊）」。
  大類不在新帳本裡就建一個（花錢的放在生活費底下）。單類上限 → 細項的預設預算（每期）。
- 每筆帳：日期、種類、金額、類別、備註、付款方式都搬；舊的編號記在 source_ref（legacy-0.3:<編號>），所以同一筆不會轉兩次。
  「待分類」＝沒有類別；沒有類別的收入放「其他收入」。備註放在說明（太長的接到備註）。
- 付款方式（只是標籤）→ 帳戶：先找名字或別名一樣的，沒有就建一個；種類照名字猜（現金、銀行、信用卡、證券），
  猜不到就當電子錢包。
- 轉帳：0.3.1 只記了「用什麼付／存到哪」一個標籤，沒有轉去哪裡——從那個帳戶（沒寫就是預設帳戶）轉到一個
  「轉出（舊版）」帳戶（不算可以花的錢），之後可以自己改。
- 每月預算 → 這一期「生活費」那一格的金額（之後每期沿用）。
- 金額：舊版收兩位小數。帳本的小數位數是 0、而舊資料裡有小數，就把帳本改成 2 位小數（不四捨五入任何一筆）。
- 轉完在設定裡記一筆（legacy.v03），舊資料另存一份到 pack.store.of(user) 的 legacy-0.3 鍵，原本那一份換成
  一個「已經搬走」的記號（不刪資料；開新帳本也不會再轉一次）。

**全工具只有這個檔可以用 pack.store，而且一定是 pack.store.of(uid)**（隔離規則；CONTRACT.md）。
轉不過去的那幾筆（例如日期在未來）不擋整批，列在結果的 skipped 裡（舊資料另存的那一份裡都還在）。
"""

import logging
from datetime import date as Date, datetime
from decimal import Decimal, InvalidOperation

from ..engine import db
from ..engine.errors import Conflict, LedgerError, NotFound
from ..engine.services import plans, refdata, settings, transactions as tx

KEY = "legacy-0.3"                # 舊資料另存在 pack.store.of(uid) 的這個鍵
DONE = "legacy.v03"               # 轉過了：帳本設定裡記一筆
SOURCE = "legacy"                 # 轉進來的每筆帳的 source；source_ref＝legacy-0.3:<舊編號>
MOVED = "moved"                   # 原本那一份換成 {"schema": "moved", "moved_to": KEY}
UNSORTED = "待分類"
KINDS = ("expense", "income", "transfer")
KIND_LABELS = {"expense": "支出", "income": "收入", "transfer": "轉帳"}
DEFAULT_GROUP = {"expense": "其他", "income": "收入", "transfer": "轉帳"}
TRANSFER_OUT = "轉出（舊版）"
COMMAND = "money legacy-0.3"

log = logging.getLogger(__name__)


# ── 讀舊資料 ───────────────────────────────────────────────────────────────────

def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def upgrade(data: dict) -> dict:
    """任何一版的舊資料 → 第 2 版的樣子（**新的一份**，不改傳進來的；跟 0.3.1 的 upgrade() 同一套規則）。"""
    out = {"schema": data.get("schema") or 1, "budget": data.get("budget"),
           "categories": [], "entries": []}
    for c in data.get("categories") or []:
        if not isinstance(c, dict) or not str(c.get("name") or "").strip():
            continue
        kind = c.get("kind") if c.get("kind") in KINDS else "expense"
        words = c.get("keywords") or []
        if isinstance(words, str):
            words = [w for w in words.replace("，", ",").replace("、", ",").split(",")]
        out["categories"].append({"name": str(c["name"]).strip(), "group": str(c.get("group") or c["name"]).strip(),
                                  "kind": kind, "keywords": [str(w).strip() for w in words if str(w).strip()],
                                  "cap": c.get("cap")})
    entries = [e for e in data.get("entries") or [] if isinstance(e, dict)]
    used = [e["id"] for e in entries if _is_id(e.get("id"))]
    next_id = max(int(data.get("next_id") or 1), max(used, default=0) + 1)
    for e in entries:
        e = dict(e)
        if not _is_id(e.get("id")):
            e["id"] = next_id
            next_id += 1
        if e.get("kind") not in KINDS:
            e["kind"] = "expense"
        if not e.get("date"):
            at = e.get("at")
            if not isinstance(at, (int, float)) or isinstance(at, bool):
                e["date"] = None                              # 沒有日期也沒有時間：轉不過去（列在 skipped）
            else:
                e["date"] = datetime.fromtimestamp(at).strftime("%Y-%m-%d")
        e.setdefault("category", UNSORTED)
        e.setdefault("memo", "")
        out["entries"].append(e)
    out["next_id"] = next_id
    return out


def has_content(data) -> bool:
    """這份舊資料有沒有東西可以轉（帳、類別或預算）；已經搬走的記號、比 0.3.1 新的格式都不算。"""
    if not isinstance(data, dict) or data.get("schema") == MOVED:
        return False
    schema = data.get("schema")
    if schema not in (None, 1, 2):
        return False
    return bool(data.get("entries") or data.get("categories") or data.get("budget"))


# ── 共用的小工具（compat.py 的舊指令也用）─────────────────────────────────────────────

def ensure_category(w, name: str, kind: str, *, group: str = "", keywords=(), purpose_hint: bool = True):
    """有這個名字、而且種類一樣的類別就用它（停用的打開、補上沒有的關鍵字）；沒有就建一個。回類別 dict。
    同名但種類不一樣：改用「名字（舊）」。大類不在就建一個（花錢的放生活費底下）。"""
    conn = w.conn
    name = name.strip()
    row = conn.execute("SELECT id, kind, active, keywords FROM categories WHERE name = ?", (name,)).fetchone()
    if row is not None and row["kind"] != kind:
        return ensure_category(w, f"{name}（舊）", kind, group=group, keywords=keywords, purpose_hint=purpose_hint)
    words = [k for k in keywords if k]
    if row is not None:
        have = [k for k in (row["keywords"] or "").split(",") if k]
        lower = {k.lower() for k in have}
        changes = {}
        extra = [k for k in words if k.lower() not in lower]
        if extra:
            changes["keywords"] = have + extra
        if not row["active"]:
            changes["active"] = 1
        if changes:
            refdata.update_category(w, row["id"], **changes)
        return refdata.category_by_id(conn, row["id"])
    g = _ensure_group(w, group or DEFAULT_GROUP[kind], kind)
    fields = {"keywords": words or None}
    if kind == "transfer" and purpose_hint:
        fields["transfer_purpose"] = transfer_purpose(name)
    return refdata.add_category(w, name=name, group=g["name"], kind=kind, **fields)


def _ensure_group(w, name: str, kind: str) -> dict:
    try:
        return refdata.group_by_name(w.conn, name)
    except NotFound:
        pass
    try:
        return refdata.add_group(w, name=name)
    except LedgerError:                                   # 名字撞到停用的、或不能建：放進那一種的預設大類
        fallback = DEFAULT_GROUP[kind]
        if fallback == name:
            raise
        return _ensure_group(w, fallback, kind)


def transfer_purpose(name: str) -> str:
    """轉帳類別的名字 → 用途（照名字猜；猜不到是一般轉帳）。"""
    for words, purpose in ((("卡費",), "card_payment"), (("投資", "股票", "基金"), "investing"),
                           (("存錢", "儲蓄", "定存", "預備金"), "saving"), (("還款", "貸款", "房貸", "車貸", "學貸"), "debt")):
        if any(word in name for word in words):
            return purpose
    return "other"


def guess_account_kind(name: str) -> str:
    low = name.lower()
    if any(w in low for w in ("現金", "cash", "錢包")):
        return "cash"
    if any(w in low for w in ("信用卡", "credit")):
        return "credit_card"
    if any(w in low for w in ("證券", "股票", "券商")):
        return "securities"
    if any(w in low for w in ("銀行", "帳戶", "郵局", "活存", "薪轉", "bank")):
        return "bank"
    return "e_wallet"


def ensure_account(w, label: str) -> dict:
    """付款方式的標籤 → 帳戶（名字或別名一樣的；沒有就建一個）。"""
    try:
        return refdata.resolve_account(w.conn, label)
    except Conflict as e:                                    # 同時像好幾個：用第一個
        if e.suggestions:
            return refdata.resolve_account(w.conn, e.suggestions[0])
        raise
    except NotFound:
        pass
    return refdata.add_account(w, name=label, kind=guess_account_kind(label), purpose="從舊版的付款方式搬來的")


def default_account(conn) -> dict:
    name = settings.get(conn, "default_account") or "現金"
    try:
        return refdata.resolve_account(conn, name)
    except LedgerError:
        return refdata.accounts(conn)[0]


def _transfer_out(w) -> dict:
    try:
        return refdata.resolve_account(w.conn, TRANSFER_OUT)
    except NotFound:
        return refdata.add_account(w, name=TRANSFER_OUT, kind="bank", liquid=0,
                                   purpose="舊版的轉帳沒有記轉去哪裡，先放在這裡（可以改）")


# ── 金額 ───────────────────────────────────────────────────────────────────────

def _decimal(value):
    if isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _needs_cents(old: dict) -> bool:
    values = [e.get("amount") for e in old["entries"]] + [old.get("budget")] + [c.get("cap") for c in old["categories"]]
    for v in values:
        d = _decimal(v) if v is not None else None
        if d is not None and d != d.to_integral_value():
            return True
    return False


def _minor(value, decimals: int):
    """舊的金額（元，最多兩位小數）→ 帳本的最小單位整數；壞的回 None。"""
    d = _decimal(value)
    if d is None:
        return None
    return int((d * (10 ** decimals)).to_integral_value())


# ── 轉換 ───────────────────────────────────────────────────────────────────────

def _used(conn) -> bool:
    return conn.execute("SELECT 1 FROM batches LIMIT 1").fetchone() is not None


def maybe_convert(pack, uid: int, book):
    """開好帳本時叫（在這個人的寫入鎖裡）。帳本已經寫過東西 → 什麼都不做（不讀保管箱）。
    回轉換的結果（dict），沒有轉回 None。"""
    conn = book.conn
    if conn is None or _used(conn):
        return None
    store = pack.store.of(uid)
    raw = store.load(None)
    if not has_content(raw):
        return None
    result = convert(conn, raw, actor=book.actor, today=book.today)
    store.save(raw, key=KEY)                                  # 先另存一份，再把原本那一份換成記號（不刪資料）
    store.save({"schema": MOVED, "moved_to": KEY, "at": book.today.isoformat(), "version": "0.4.0"})
    return result


def convert(conn, raw: dict, *, actor: str, today: Date) -> dict:
    """把一份舊資料轉進這本帳（一批寫入）。回 {"entries", "categories", "accounts", "budget", "skipped", "batch_id"}。"""
    old = upgrade(raw)
    skipped = []
    made_accounts = []
    with db.write(conn, actor=actor, source=SOURCE, command=COMMAND, note="0.3 版的舊資料轉進來") as w:
        decimals = int(settings.value(conn, "currency.decimals") or 0)
        if decimals == 0 and _needs_cents(old):
            settings.put(w, "currency.decimals", 2)
            decimals = 2
        before_accounts = {a["id"] for a in refdata.accounts(conn, include_inactive=True)}
        cats = {}
        for c in old["categories"]:
            cat = ensure_category(w, c["name"], c["kind"], group=c["group"], keywords=c["keywords"])
            cats[(c["name"].lower(), c["kind"])] = cat
            cap = _minor(c.get("cap"), decimals) if c.get("cap") not in (None, "", 0) else None
            if cap and cap > 0 and cat["kind"] == "expense":
                refdata.update_category(w, cat["id"], default_budget=cap, cap_basis="period")
        fallback = default_account(conn)
        count = 0
        for e in sorted(old["entries"], key=lambda x: x["id"]):
            why = _one(w, e, cats, fallback, decimals)
            if why:
                skipped.append({"id": e["id"], "why": why})
            else:
                count += 1
        budget = _minor(old.get("budget"), decimals) if old.get("budget") not in (None, "", 0) else None
        if budget and budget > 0:
            plans.set_plan(w, None, buckets={"living": budget})
        made_accounts = [a["name"] for a in refdata.accounts(conn, include_inactive=True) if a["id"] not in before_accounts]
        settings.put(w, DONE, {"from": "0.3", "entries": count, "skipped": len(skipped), "on": today.isoformat()})
        batch_id = w.batch_id
    return {"entries": count, "categories": len(old["categories"]), "accounts": made_accounts,
            "budget": budget, "skipped": skipped, "batch_id": batch_id, "decimals": decimals}


def _one(w, e: dict, cats: dict, fallback: dict, decimals: int):
    """一筆舊帳 → 新帳本；轉不過去回原因（一句話），轉好了回 None。"""
    kind = e["kind"]
    amount = _minor(e.get("amount"), decimals)
    if amount is None or amount <= 0:
        return "金額不對"
    if not e.get("date"):
        return "沒有日期"
    name = str(e.get("category") or "").strip()
    cat = None
    if name and name != UNSORTED:
        cat = cats.get((name.lower(), kind))
        if cat is None:
            try:
                cat = ensure_category(w, name, kind)
            except LedgerError as err:
                return err.message
            cats[(name.lower(), kind)] = cat
    if kind == "income" and cat is None:
        cat = refdata.system_category(w.conn, "income.other") or next(
            (c for c in refdata.categories(w.conn) if c["kind"] == "income"), None)
        if cat is None:
            return "沒有收入類別"
    label = str(e.get("account") or "").strip()
    try:
        account = ensure_account(w, label) if label else fallback
    except LedgerError as err:
        return err.message
    to_id = None
    if kind == "transfer":
        target = _transfer_out(w)
        if account["kind"] == "credit_card" or account["id"] == target["id"]:
            account = fallback
        to_id = target["id"]
    memo = str(e.get("memo") or "")
    try:
        tx.create(w, kind=kind, amount=amount, date=e["date"], account_id=account["id"],
                  category_id=cat["id"] if cat else None, to_account_id=to_id,
                  description=memo[:tx.MAX_DESC], note=memo[tx.MAX_DESC:tx.MAX_DESC + tx.MAX_NOTE],
                  source=SOURCE, source_ref=f"{KEY}:{e['id']}")
    except LedgerError as err:
        return err.message
    return None


def on_open(pack, uid, book):
    """compat.on_open 叫這裡。轉換失敗（不是使用者能處理的）不擋這次的指令：帳本照常打開，下次再試；
    舊資料原封不動（寫入整批退回）。"""
    try:
        return maybe_convert(pack, uid, book)
    except Exception as e:                                    # 訊息可能帶資料，不寫進 log
        log.warning("money: converting 0.3 data failed (%s); will retry next time", type(e).__name__)
        return None
