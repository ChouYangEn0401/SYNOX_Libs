"""依名稱修改一筆交易（CLI 的 txn edit 與網站的 PATCH /api/txn 共用）。

呼叫端給的是人看得懂的名字（類別名、帳戶名或別名），這裡負責解析成 id 再交給 transactions.update。
有人改過＝看過了：一律清掉 agent 的低把握度（confidence）。
"""
from __future__ import annotations

from typing import Any

from ..db import Writer
from ..errors import InvalidInput, NotFound
from . import classify, refdata, transactions as tx

CLEAR_CATEGORY = ("", "待分類", "none")


def _advance_changes(w: Writer, txn_id: str, on: bool) -> dict[str, Any]:
    """一筆花錢切成「代墊」（幫別人先付、要跟對方報帳），或切回花錢。

    代墊＝從原本的帳戶（常常是信用卡）轉到「代墊款」的借出：錢照樣刷在卡上、卡費照算，
    但不是你的花費（不進生活費、不讓日曆變紅），錢還在「代墊款」等對方還。切回花錢時類別重猜一次。"""
    cur = tx.get(w.conn, txn_id)
    lent = cur["kind"] == "transfer" and cur["purpose"] == "lend"
    if on:
        if lent:
            return {}
        if cur["kind"] != "expense":
            raise InvalidInput("只有花錢可以改成代墊", hint="收入、轉帳、存錢不是幫別人先付的錢", fields={"advance": "不是花錢"})
        if cur["financed"]:
            raise InvalidInput("分期的不能改成代墊", fields={"advance": "分期的"})
        lend = refdata.system_category(w.conn, "lend")
        return {"kind": "transfer", "to_account_id": refdata.receivable_account(w)["id"], "purpose": "lend",
                "category_id": lend["id"] if lend else None, "nature": None}
    if not lent:
        if cur["kind"] == "expense":
            return {}
        raise InvalidInput("這筆不是代墊", fields={"advance": "不是代墊"})
    cat, _ = classify.guess(w.conn, cur["desc"] or "", time=cur["time"], date=cur["date"], merchant=cur["merchant"], kind="expense")
    return {"kind": "expense", "to_account_id": None, "purpose": None, "counterparty": None,
            "category_id": cat["id"] if cat else None}


def edit(w: Writer, txn_id: str, *, amount: Any = None, desc: str | None = None, category: str | None = None,
         account: str | None = None, to: str | None = None, date: str | None = None, time: str | None = None,
         merchant: str | None = None, note: str | None = None, purpose: str | None = None,
         counterparty: str | None = None, items: list | None = None, for_period: str | None = None,
         nature: str | None = None, advance: bool | None = None) -> dict[str, Any]:
    changes: dict[str, Any] = {"confidence": None}
    if amount is not None:
        changes["amount"] = amount
    if desc is not None:
        changes["description"] = desc
    for key, value in (("date", date), ("time", time), ("merchant", merchant), ("note", note),
                       ("purpose", purpose), ("counterparty", counterparty)):
        if value is not None:
            changes[key] = (None if key == "counterparty" and value == "" else value)      # 清掉對象存成 NULL，不是空字串
    if category is not None:
        if category in CLEAR_CATEGORY:
            changes["category_id"] = None
        else:
            cat = refdata.find_category(w.conn, category)
            if cat is None:
                raise NotFound(f"找不到類別「{category}」", suggestions=refdata.category_suggestions(w.conn, category),
                               fields={"category": "找不到"})
            changes["category_id"] = cat["id"]
    if account is not None:
        changes["account_id"] = refdata.resolve_account(w.conn, account)["id"]
    if to is not None:
        changes["to_account_id"] = refdata.resolve_account(w.conn, to)["id"]
    if nature is not None:
        changes["nature"] = nature                           # 空字串＝改回日常
    if for_period is not None:
        changes["for_period"] = for_period                   # 空字串＝拿掉（照付款日算）
    if items is not None:
        changes["items"] = items
    asked = "category_id" in changes                          # 你自己選的類別才教它；切代墊／切回時系統猜的不算
    if advance is not None:
        for key, value in _advance_changes(w, txn_id, advance).items():
            if key != "category_id" or not asked:
                changes[key] = value
    t = tx.update(w, txn_id, **changes)
    if asked and changes["category_id"]:
        classify.learn(w, t)                                  # 改類別＝教它這家店歸哪裡
    return t
