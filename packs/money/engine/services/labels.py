"""一筆帳在畫面上怎麼寫：**大項目在上、細節在下**（一眼先看到花在哪一類，細節放第二行）。

- 標題（title）＝大項目＝類別：午餐、晚餐、捐贈、房租、訂閱……（轉帳沒有類別就用你寫的：ATM領、存錢）。
  還沒分類的支出就寫「待分類」——寧可誠實，也不要拿一串信用卡帳單原文當標題。
- 細節（detail）＝你寫的那段（店家、菜名、買了什麼：巷口自助餐、麻油雞、耳機）、帳單上的店名、備註。
  跟標題重複的（午餐／午餐）、銀行自己的字（網路轉、轉帳存）、帳單上同一家店的兩種寫法（某某小吃／支付前綴＋某某小吃）
  只留一個，不會一行裡講三遍。
- 銀行轉帳的字（網路轉、跨行轉帳…）不是「買了什麼」，是「怎麼付的」：進資料庫時搬到備註（ingest），
  畫面上也不會當標題或細節。

這個檔案是唯一的規則：網站的存摺、CLI 的 txn list、聊天助理的選項（quick items）都問這裡，三邊才會一致。
"""
from __future__ import annotations

import re
from typing import Any

# 銀行／轉帳工具自己的字：不是品項，是「怎麼付的」。只用在花錢與收入（轉帳本來就是你自己寫的說明，例：ATM領）。
MEMO_RE = re.compile(r"^(網路轉|網銀轉|行動轉|跨行轉|跨行|轉帳存|轉帳|匯款|匯入|ATM轉|轉入|轉出|存入|提款|扣款)(帳|存|入|出)?[\s\d]*$",
                     re.IGNORECASE)
UNSORTED = "待分類"
AUTO_TRANSFER = ("借出", "收回借款", "繳卡費", "帳戶互轉")        # 系統自動給轉帳的類別：標題已經講了，不再重複


def is_memo(text: str | None) -> bool:
    return bool(MEMO_RE.match((text or "").strip()))


def _norm(text: str | None) -> str:
    """比較用：小寫、只留文字與數字（拿掉空白、星號、標點）。"""
    return re.sub(r"[^0-9a-z㐀-鿿]", "", (text or "").lower())


def same(a: str | None, b: str | None) -> bool:
    """兩段字是不是在講同一件事：一邊包含另一邊（某某小吃／支付前綴＋某某小吃、大小寫或空白不同的同一個名字）。"""
    na, nb = _norm(a), _norm(b)
    return bool(na) and bool(nb) and (na in nb or nb in na)


def is_filler(desc: str | None, category: str | None) -> bool:
    """這段字不是「細節」：空的、銀行的字、或就是類別名字（午餐／午餐）。聊天助理的選項與標題判斷用。"""
    d = (desc or "").strip()
    return not d or is_memo(d) or (bool(category) and _norm(d) == _norm(category))


def tidy(desc: str | None, note: str | None, *, kind: str) -> tuple[str, str]:
    """進資料庫前：銀行的字（網路轉）搬到備註，品項留空——它是「怎麼付的」，不是「買了什麼」。"""
    d, n = (desc or "").strip(), (note or "").strip()
    if kind != "transfer" and is_memo(d):
        return "", (f"{d} {n}".strip() if n else d)
    return d, n


def detail_parts(desc: str | None, merchant: str | None, category: str | None, note: str | None, *, kind: str) -> list[str]:
    """細節：你寫的、店名、備註；重複的與銀行字不留。"""
    d = (desc or "").strip()
    m = (merchant or "").strip()
    n = (note or "").strip()
    if kind != "transfer" and is_memo(d):
        d = ""
    if d and category and same(d, category):
        d = ""
    parts: list[str] = []
    if d:
        parts.append(d)
    if m and not (d and same(d, m)) and not (category and same(m, category) and not d):
        parts.append(m)
    if n and not (kind != "transfer" and is_memo(n)) and not any(same(n, p) for p in parts):
        parts.append(n)
    return parts


def split(*, desc: str | None, merchant: str | None = None, category: str | None = None, note: str | None = None,
          kind: str = "expense", purpose: str | None = None) -> dict[str, Any]:
    """回 {title, detail(list)}。代墊與收回的大項目就叫「代墊」「收回」，你寫的（AI 訂閱、午餐）在細節。"""
    parts = detail_parts(desc, merchant, category, note, kind=kind)
    if kind == "transfer" and purpose in ("lend", "collect", "repay"):
        title = {"lend": "代墊", "collect": "收回", "repay": "還錢"}[purpose]
        return {"title": title, "detail": [p for p in parts if _norm(p) != _norm(title)]}      # 整段一樣才拿掉，「代墊午餐給小明」要留著
    if kind == "transfer":
        title = (desc or "").strip() or category or ("預付" if purpose == "prepay" else "轉帳")
        if category and category not in AUTO_TRANSFER and not same(title, category):
            parts.insert(0, category)
        parts = [p for p in parts if not same(p, title)]
        return {"title": title, "detail": parts}
    if category:
        title = category
    elif kind == "expense":
        title = UNSORTED
    else:
        title = (desc or merchant or "").strip() or "（沒寫）"
    return {"title": title, "detail": parts}
