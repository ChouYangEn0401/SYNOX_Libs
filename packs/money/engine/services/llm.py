"""「寫檢討的提供者」的介面：引擎不認得任何 AI、不叫任何程式、不碰任何路徑，只認一個呼叫端交進來的函式。

    provider(prompt: str, schema: dict, opts: dict) -> {"structured": dict, "model": str | None, "cost_usd": float | None}

- prompt：寫檢討的提示（reviews.prompt）；schema：回覆要照的 JSON Schema（reviews.schema）；
  opts：{"model": 設定 review.model（沒設＝None，提供者自己決定）, "timeout": 秒}。
- 額度用完、晚點可以再試 → 丟 ProviderQuota（resets＝提供者說的重置時間，沒有就空字串）：那一份標「延後」，不算失敗。
- 其他失敗 → 丟 ProviderFailed（訊息給人看，不要放路徑與密鑰）：那一份標「失敗」、附原因。
- 回的不是上面那個形狀 → 當成 ProviderFailed。

誰來給提供者：SYNOX 那一層（bridge/writer.py）。**沒有提供者時 require() 丟 LlmUnavailable**——什麼都不寫，
其他檢討功能（清單、全文、存檢討、回覆、套用）與整個記帳照常。測試用假的提供者。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..errors import LlmUnavailable

Provider = Callable[[str, dict, dict], dict]

UNAVAILABLE = "還沒有接上會寫檢討的 AI，這一步先不做；其他功能都不受影響"
HINT = "請 AI 助理照記帳手冊寫：先 /money_report review=yes，寫完用 /money_review_save 存回來；或自己用 /money_review_save 存一份"


class ProviderQuota(Exception):
    """提供者的額度用完：resets 是它說的重置時間原文（沒有就空字串）。"""

    def __init__(self, message: str = "", resets: str = ""):
        super().__init__(message)
        self.resets = resets


class ProviderFailed(Exception):
    """提供者這一次失敗（訊息給人看）。"""


def require(provider: Optional[Provider]) -> Provider:
    """要叫 AI 之前問一次：沒有提供者就丟 LlmUnavailable（什麼都不寫）。"""
    if provider is None or not callable(provider):
        raise LlmUnavailable(UNAVAILABLE, hint=HINT)
    return provider


def call(provider: Provider, prompt: str, schema: dict, opts: dict) -> dict[str, Any]:
    """叫提供者、檢查回的形狀。ProviderQuota 原樣往外丟；其他例外一律變成 ProviderFailed（訊息只留型別與一句話）。"""
    try:
        res = provider(prompt, schema, dict(opts))
    except (ProviderQuota, ProviderFailed):
        raise
    except Exception as e:                                   # 提供者自己的錯：只留一句，不把內部細節帶出去
        raise ProviderFailed(f"寫檢討的 AI 出錯了（{type(e).__name__}）") from None
    if not isinstance(res, dict) or not isinstance(res.get("structured"), dict):
        raise ProviderFailed("寫檢討的 AI 沒有照格式回答")
    model = res.get("model")
    cost = res.get("cost_usd")
    return {"structured": res["structured"], "model": str(model)[:60] if model else None,
            "cost_usd": float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None}
