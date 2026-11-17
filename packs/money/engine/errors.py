"""錯誤型別：每一種對應一個 CLI 結束碼與 JSON 的 error.code。

service 只丟這些錯；CLI 把它轉成 {"ok":false,"error":{…}} 與結束碼，網站轉成 HTTP 狀態碼。
訊息用中文、寫清楚錯在哪；hint 寫下一步怎麼做。
"""
from __future__ import annotations

from typing import Any


class LedgerError(Exception):
    exit_code = 1
    code = "internal"
    http_status = 500

    def __init__(self, message: str, *, hint: str = "", fields: dict[str, str] | None = None,
                 suggestions: list[str] | None = None, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.fields = fields or {}
        self.suggestions = suggestions or []
        self.extra = extra or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.fields:
            out["fields"] = self.fields
        if self.suggestions:
            out["suggestions"] = self.suggestions
        out.update(self.extra)
        return out


class InvalidInput(LedgerError):
    """輸入有誤：JSON 壞掉、欄位不對、金額不是整數、日期在未來、民國年。"""
    exit_code = 2
    code = "invalid"
    http_status = 400


class NotFound(LedgerError):
    """找不到帳戶、類別、id、期別；盡量附 suggestions。"""
    exit_code = 3
    code = "not_found"
    http_status = 404


class Conflict(LedgerError):
    """衝突：同一個 source_ref 內容不同、要撤銷的記錄之後被改過、資料庫比程式新。"""
    exit_code = 4
    code = "conflict"
    http_status = 409


class NoHome(LedgerError):
    """帳本資料夾沒設定（paths.use_home 沒進 context）：不開任何帳本、不退回共用位置。"""
    exit_code = 1
    code = "no_home"
    http_status = 500


class LlmUnavailable(LedgerError):
    """要 AI 寫檢討，但沒有接上任何「寫檢討的提供者」：什麼都不寫，其他功能照常。"""
    exit_code = 5
    code = "llm_unavailable"
    http_status = 503
