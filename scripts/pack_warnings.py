"""pack.json 的標記警告（`warnings`，SYNOX ADR 0041）→ index.json 與公開頁用的整理過的清單。

    "warnings": [{"code": "vendor_no_security_guarantee", "text": "官方不保證安全", "advice": "建議在 docker 或 sandbox 執行"}]

為什麼要抄進 index.json：使用者在 SYNOX 的工具庫清單、公開頁的工具卡片上挑工具時，還沒安裝、看不到 pack.json，
警告要在「裝之前」就看得到。index 上的只是**預告**：pack.json 受簽章保護，真正擋人的是安裝時 SYNOX 用簽過的 pack.json 重新檢查、
要擁有者逐條確認的那一份。

規則跟 SYNOX 的 synox/packs/packwarn.py 一樣（形狀、長度、清理、壞格式的處理；tests/test_index_warnings.py 拿兩邊比對）：
- code：小寫英數字與 _，字母開頭，48 字內；text 60 字內；advice 120 字內（選填）；最多 5 條，同一個 code 只留第一條，
  超過時 SYNOX 認得的 code 一定留。
- text／advice 先清：換行與 tab 換成空白、拿掉控制字元、零寬、雙向文字控制字元，連續空白併成一個，太長截斷加「…」；
  含「✅」（冒充 SYNOX 的標章）或像在對 AI 下指令的字（SYNOX 的 risk_flags；import 得到 synox 才查）→ 換成內建的字。
- 壞掉的寫法**不讓打包掛掉，也不靜默**：看不懂的那一條換成一條 `malformed_warning`（照樣顯示、照樣要確認），
  每個問題回一句給人看的話（build.py 印出來）。
- pack.json 沒寫 `warnings`：什麼都不回（index.json 那一筆不多任何欄位，跟以前逐位元組相同）。
只用標準函式庫（risk_flags 是選用的）；不連網。
"""
from __future__ import annotations

import re

CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
MAX_WARNINGS = 5
MAX_TEXT = 60
MAX_ADVICE = 120
KEYS = ("code", "text", "advice")

VENDOR_NO_GUARANTEE = "vendor_no_security_guarantee"
MALFORMED = "malformed_warning"
KNOWN = {
    VENDOR_NO_GUARANTEE: ("官方不保證安全", "建議在 docker 或 sandbox 執行"),
    MALFORMED: ("這個工具的警告寫壞了，SYNOX 看不懂", "當作有風險的工具：先問作者，或在 docker 或 sandbox 執行"),
}
GENERIC_TEXT = "作者標了一個警告（沒有寫說明）"
OFFICIAL_WORD = "官方"          # 有警告的工具，標題、卡片上的標籤都不准有這兩個字（不能看起來像官方背書）

# 換行、控制字元（C0、DEL、C1）、軟連字號、零寬（U+200B–U+200F）、雙向文字控制（U+202A–U+202E、U+2066–U+2069）、
# word joiner 那一段（U+2060–U+2064）、BOM
_STRIP_RE = re.compile("[\\x00-\\x1f\\x7f-\\x9f\xad​-‏‪-‮⁠-⁤⁦-⁩﻿]")


def clean_text(value, limit: int):
    """給人看的字 → (清過的字, 有沒有拿掉字元, 有沒有截斷)。不是字串回 ("", False, False)。"""
    if not isinstance(value, str):
        return "", False, False
    spaced = re.sub(r"[\t\r\n\v\f]", " ", value)
    stripped = _STRIP_RE.sub("", spaced)
    removed = stripped != value
    text = " ".join(stripped.split())
    cut = len(text) > limit
    if cut:
        text = text[:limit - 1].rstrip() + "…"
    return text, removed, cut


def _risky(text: str) -> list:
    """像在對 AI 下指令、要跳過確認、夾分隔符的字 → 理由（SYNOX 的 risk_flags；import 不到 synox 就不查，SYNOX 讀清單時會再查一次）。"""
    if not text:
        return []
    try:
        from synox.packs.manifest import SCENARIO_RISKS
        from synox.runtime.ledger import risk_flags
    except ImportError:
        return []
    return [flag for flag in risk_flags(text) if flag in SCENARIO_RISKS]


def malformed() -> dict:
    text, advice = KNOWN[MALFORMED]
    return {"code": MALFORMED, "text": text, "advice": advice}


def _entry(item, where: str, problems: list) -> dict:
    if not isinstance(item, dict):
        problems.append(f"{where} 要是 {{\"code\", \"text\", \"advice\"}} 物件（現在是 {type(item).__name__}）：當作「警告寫壞了」")
        return malformed()
    unknown = sorted(str(k) for k in item if k not in KEYS)
    if unknown:
        problems.append(f"{where} 不認得 {'、'.join(unknown)}（只收 {'、'.join(KEYS)}；那幾個欄位沒有用）")
    code = item.get("code")
    if not isinstance(code, str) or not CODE_RE.match(code):
        problems.append(f"{where}.code 要是小寫英數字與 _（字母開頭，48 字內），現在是 {str(code)[:60]!r}：當作「警告寫壞了」")
        return malformed()
    default_text, default_advice = KNOWN.get(code, (GENERIC_TEXT, ""))
    out = {"code": code}
    for key, limit, fallback in (("text", MAX_TEXT, default_text), ("advice", MAX_ADVICE, default_advice)):
        value = item.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if key == "text":
                problems.append(f"{where}.text 沒寫：用「{fallback}」")
            out[key] = fallback
            continue
        if not isinstance(value, str):
            problems.append(f"{where}.{key} 要是字串：用「{fallback or '（沒有）'}」")
            out[key] = fallback
            continue
        text, removed, cut = clean_text(value, limit)
        if removed:
            problems.append(f"{where}.{key} 有換行、控制字元或看不見的字元（零寬、雙向文字控制）：已經拿掉")
        if cut:
            problems.append(f"{where}.{key} 太長（最多 {limit} 字）：截斷了")
        risky = _risky(text)
        if "✅" in text:
            risky.append("有 ✅（看起來像 SYNOX 的標章）")
        if risky or not text:
            if risky:
                problems.append(f"{where}.{key} 不能{'、'.join(risky)}（這段字會給 AI 看）：用「{fallback or '（沒有）'}」")
            text = fallback
        out[key] = text
    return out


def parse(value, present: bool = True):
    """pack.json 的 warnings → (整理過的清單, 問題清單)。不丟例外。present=False（沒寫這個欄位）→ ([], [])。"""
    if not present:
        return [], []
    problems = []
    if value is None:
        problems.append("warnings 寫了 null：當作「警告寫壞了」（不要警告就整個拿掉這個欄位）")
        return [malformed()], problems
    if not isinstance(value, list):
        problems.append(f"warnings 要是清單（[{{\"code\", \"text\", \"advice\"}}…]），現在是 {type(value).__name__}：當作「警告寫壞了」")
        return [malformed()], problems
    out = []
    seen = set()
    for i, item in enumerate(value):
        entry = _entry(item, f"warnings[{i}]", problems)
        if entry["code"] in seen:
            if entry["code"] != MALFORMED:
                problems.append(f"warnings[{i}] 的 code {entry['code']} 重複了：只留第一條")
            continue
        seen.add(entry["code"])
        out.append(entry)
    if len(out) > MAX_WARNINGS:
        problems.append(f"warnings 最多 {MAX_WARNINGS} 條（現在 {len(out)} 條）：只留 SYNOX 認得的與前面幾條")
        known = [w for w in out if w["code"] in KNOWN]
        rest = [w for w in out if w["code"] not in KNOWN]
        keep = {w["code"] for w in (known + rest)[:MAX_WARNINGS]}
        out = [w for w in out if w["code"] in keep]
    return out, problems


def of_pack(raw: dict):
    """pack.json（dict）→ (整理過的清單, 問題清單)；沒寫 warnings → ([], [])。"""
    return parse(raw.get("warnings"), present="warnings" in raw)


def with_warnings(entry: dict, raw: dict, after: str = "line") -> dict:
    """index.json 的一筆 → pack.json 有 warnings 就把整理過的清單插在 after 那一欄後面；
    沒有（或整理完是空的）→ entry 原樣（那一筆逐位元組不變）。"""
    items, _problems = of_pack(raw)
    if not items:
        return entry
    out = {}
    for key, value in entry.items():
        out[key] = value
        if key == after:
            out["warnings"] = items
    out.setdefault("warnings", items)
    return out


def line(w: dict) -> str:
    """一條給人看的一行：「官方不保證安全——建議在 docker 或 sandbox 執行」。"""
    text = str(w.get("text") or GENERIC_TEXT)
    advice = str(w.get("advice") or "")
    return f"{text}——{advice}" if advice else text
