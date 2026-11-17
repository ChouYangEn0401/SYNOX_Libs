"""「寫檢討的提供者」從哪裡來（引擎那一邊的介面在 engine/services/llm.py）。

順序：
1. 有人把提供者掛到這個工具上（`register(pack, provider)`：測試用假的；日後 SYNOX 或別的工具接上來也走這裡）。
2. SYNOX 自己給工具用的 AI（`from_synox`）：**SYNOX 的 SDK（0.2.x）現在沒有讓 python 工具叫 AI 的入口**，所以回 None；
   SDK 加了之後只改 `from_synox` 這一個函式。
3. 都沒有 → None：`/money_review_run` 回 llm_unavailable（什麼都不寫），其他照常。
   SYNOX 的 AI 助理照舊可以自己寫：`/money_report review=yes` 讀數字與規則、寫好用 `/money_review_save` 存回來。

提供者掛在 pack 物件上（跟寫入鎖一樣；重新載入工具＝新的 pack、要重新掛）：模組層級不放任何會變的東西（隔離規則 4）。
這個工具不叫任何外部程式、不讀任何路徑、不碰任何密鑰。
"""

def register(pack, provider) -> None:
    """把一個寫檢討的提供者掛到這個工具上；給 None＝拿掉。provider 的形狀見 engine/services/llm.py。"""
    if provider is not None and not callable(provider):
        raise TypeError("money: review writer must be callable(prompt, schema, opts) -> dict")
    pack.money_review_writer = provider


def from_synox(pack):
    """SYNOX 給工具用的 AI。SDK 0.2.x 沒有這個入口（loader.Pack 只有 store、path_of、secret、web_route、app_* 這些），回 None。"""
    return None


def provider_of(pack):
    """這次呼叫要用的提供者；沒有回 None。"""
    found = getattr(pack, "money_review_writer", None)
    if callable(found):
        return found
    return from_synox(pack)
