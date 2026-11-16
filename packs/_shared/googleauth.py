"""
googleauth：Google 的 OAuth「裝置碼登入」（device flow）、換 access token、收回授權。給接 Google 的工具共用
（google-sheets 先用；之後的 gmail／drive 也用這一份）。

這裡是**正本**。SYNOX 的 loader 只執行 pack 資料夾裡的 pack.py（不把資料夾放進 sys.path），
pack 之間也不能互相 import，所以每個用到它的 pack.py 裡都有一份一模一樣的副本，夾在
`# >>> googleauth` 與 `# <<< googleauth` 兩行之間。改這裡之後跑 `python scripts/sync_shared.py`，
`tests/test_shared_sync.py` 會檢查每份副本都跟正本一樣。

端點與參數照 Google 的官方文件（2026-11-16 查）：
- 裝置碼登入：https://developers.google.com/identity/protocols/oauth2/limited-input-device
  POST https://oauth2.googleapis.com/device/code（client_id、scope）→ device_code、user_code、verification_url、
  expires_in、interval；POST https://oauth2.googleapis.com/token（client_id、client_secret、device_code、
  grant_type=urn:ietf:params:oauth:grant-type:device_code）。等待中＝428 authorization_pending、太快＝403 slow_down、
  不允許＝403 access_denied；碼過期（文件沒列錯誤碼，RFC 8628 是 expired_token）→ 從頭再來。
  這個流程只收少數幾個 scope（email／openid／profile、drive.appdata、drive.file、youtube…）。
- 換 token：同一個 token 端點，grant_type=refresh_token；收回：POST https://oauth2.googleapis.com/revoke（token）。

規矩：
- 不碰時鐘以外的東西：HTTP 由呼叫的人給（`http(method, url, **kw)`，測試換成假的）、存哪裡由呼叫的人決定（store）。
- 錯誤一律是寫給人看的一句話（GoogleAuthError）；**絕不帶 token、client secret、device code 或 Google 回應的原文**，
  只認幾個已知的錯誤碼（小寫英文字母與底線）。
"""

# >>> googleauth（正本 packs/_shared/googleauth.py；不要直接改這段，改正本再跑 scripts/sync_shared.py）
import re as _gre
import time as _gtime
from urllib.parse import urlparse as _gurlparse

import requests as _grequests

GOOGLE_DEVICE_URL = "https://oauth2.googleapis.com/device/code"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
GOOGLE_TIMEOUT = 20
GOOGLE_MIN_INTERVAL = 5                          # Google 沒給 interval 時，兩次詢問之間至少隔幾秒
_G_CLIENT_ID = _gre.compile(r"^[A-Za-z0-9._-]{10,200}\.apps\.googleusercontent\.com$")
_G_SECRETISH = _gre.compile(r"^[A-Za-z0-9._~/+=-]{8,2048}$")
_G_USER_CODE = _gre.compile(r"^[A-Za-z0-9-]{4,20}$")
_G_ERROR_CODE = _gre.compile(r"^[a-z_]{1,40}$")
_G_VERIFY_HOSTS = ("www.google.com", "google.com")


class GoogleAuthError(Exception):
    """要回給使用者的一句話（不含任何憑證、不含 Google 回應的原文）。"""


class GoogleNetError(GoogleAuthError):
    """連不上 Google（網路）：等一下再試就好，不用重新開始。"""


class GoogleAuthGone(GoogleAuthError):
    """授權沒了（被收回、過期、測試狀態 7 天到了）：要重新登入。"""


def google_check_client(client_id: str, client_secret: str):
    """client_id／client_secret 的樣子對不對。回錯誤的一句話或 None（不印出值）。"""
    if not _G_CLIENT_ID.match(client_id or ""):
        return "client_id 看起來不對（應該是 ….apps.googleusercontent.com 結尾）。"
    if not _G_SECRETISH.match(client_secret or ""):
        return "client_secret 看起來不對（不能有空白）。"
    return None


def google_error_code(body) -> str:
    """Google 回應裡的 error 欄位，只認「小寫英文＋底線」的錯誤碼；其他（含原文訊息）一律不拿。"""
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) and _G_ERROR_CODE.match(code) else ""


def _g_post(http, url: str, data: dict):
    """POST 表單 → (HTTP 狀態, JSON dict)。連不上丟 GoogleAuthError（不帶例外的原文：裡面可能有網址或 token）。"""
    try:
        r = http("POST", url, data=data, timeout=GOOGLE_TIMEOUT)
    except _grequests.RequestException:
        raise GoogleNetError("連不上 Google（網路有問題？等一下再試）。") from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, body if isinstance(body, dict) else {}


def _g_raise_for(status: int, code: str) -> None:
    if code == "invalid_client" or status == 401:
        raise GoogleAuthError("Google 說 client_id／client_secret 不對（或用戶端類型不是「電視和輸入受限裝置」）。")
    if code == "invalid_grant":
        raise GoogleAuthGone("Google 不再接受這個授權（被收回、過期，或用戶端還在「測試」狀態時 7 天就失效）。")
    if code == "admin_policy_enforced":
        raise GoogleAuthError("這個 Google 帳號的管理員不允許這個權限。")
    if code == "org_internal":
        raise GoogleAuthError("這個用戶端只給某個機構內部用（OAuth 同意畫面的使用者類型要選「外部」）。")
    if status == 429 or code in ("rate_limit_exceeded", "slow_down"):
        raise GoogleAuthError("Google 說太頻繁了，等一下再試。")
    raise GoogleAuthError(f"Google 拒絕了（{code or 'HTTP ' + str(status)}）。")


def google_device_start(http, client_id: str, scope: str, now: float) -> dict:
    """第一步：跟 Google 要一組裝置碼。回 {device_code, user_code, verification_url, expires_at, interval, next_poll}。"""
    status, body = _g_post(http, GOOGLE_DEVICE_URL, {"client_id": client_id, "scope": scope})
    if status != 200:
        _g_raise_for(status, google_error_code(body))
    device_code, user_code = body.get("device_code"), body.get("user_code")
    url = body.get("verification_url") or body.get("verification_uri")
    if not (isinstance(device_code, str) and _G_SECRETISH.match(device_code)
            and isinstance(user_code, str) and _G_USER_CODE.match(user_code) and isinstance(url, str)):
        raise GoogleAuthError("Google 的回應看不懂（沒有裝置碼）。")
    parsed = _gurlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _G_VERIFY_HOSTS:
        raise GoogleAuthError("Google 給的登入網址看起來不對，沒有用它。")
    try:
        expires_in = max(60, min(int(body.get("expires_in") or 1800), 3600))
        interval = max(GOOGLE_MIN_INTERVAL, min(int(body.get("interval") or GOOGLE_MIN_INTERVAL), 60))
    except (TypeError, ValueError):
        expires_in, interval = 1800, GOOGLE_MIN_INTERVAL
    return {"device_code": device_code, "user_code": user_code, "verification_url": url,
            "expires_at": now + expires_in, "interval": interval, "next_poll": now + interval}


def google_device_poll(http, client_id: str, client_secret: str, device: dict, now: float):
    """第二步：問一次 Google「使用者按允許了沒」（只問一次，不在這裡等）。
    回 ("ok", token 回應)、("pending", 還要等幾秒)、("slow_down", 還要等幾秒)；不允許／過期丟 GoogleAuthError。
    device 會被改（interval、next_poll）：呼叫的人要存回去。"""
    if now >= float(device.get("expires_at") or 0):
        raise GoogleAuthError("登入碼已經過期了，請重新開始。")
    wait = float(device.get("next_poll") or 0) - now
    if wait > 0:
        return "pending", int(wait) + 1                 # 太快：不去問 Google（不然會被說 slow_down）
    status, body = _g_post(http, GOOGLE_TOKEN_URL, {
        "client_id": client_id, "client_secret": client_secret,
        "device_code": device.get("device_code", ""), "grant_type": GOOGLE_DEVICE_GRANT})
    interval = int(device.get("interval") or GOOGLE_MIN_INTERVAL)
    if status == 200:
        if not body.get("access_token"):
            raise GoogleAuthError("Google 的回應看不懂（沒有 access_token）。")
        return "ok", body
    code = google_error_code(body)
    if code == "authorization_pending":
        device["next_poll"] = now + interval
        return "pending", interval
    if code == "slow_down":
        interval = min(interval + 5, 60)                # RFC 8628：每次被說 slow_down 就多等 5 秒
        device["interval"], device["next_poll"] = interval, now + interval
        return "slow_down", interval
    if code == "access_denied":
        raise GoogleAuthError("你在 Google 那邊按了不允許（或關掉了），沒有連上。要連的話重新開始。")
    if code in ("expired_token", "invalid_grant"):
        raise GoogleAuthError("登入碼已經過期或用過了，請重新開始。")
    _g_raise_for(status, code)
    return "pending", interval                          # 不會到這裡（_g_raise_for 一定丟）


def google_refresh(http, client_id: str, client_secret: str, refresh_token: str) -> dict:
    """用 refresh token 換新的 access token。授權沒了丟 GoogleAuthGone。"""
    status, body = _g_post(http, GOOGLE_TOKEN_URL, {
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token"})
    if status != 200:
        _g_raise_for(status, google_error_code(body))
    if not body.get("access_token"):
        raise GoogleAuthError("Google 的回應看不懂（沒有 access_token）。")
    return body


def google_revoke(http, token: str) -> bool:
    """請 Google 收回（refresh token 或 access token；收回 access token 也會收回對應的 refresh token）。"""
    try:
        return http("POST", GOOGLE_REVOKE_URL, data={"token": token}, timeout=GOOGLE_TIMEOUT).status_code == 200
    except _grequests.RequestException:
        return False


def google_access_token(store, http, force: bool = False) -> str:
    """store（pack.store.of(user)）裡的 access token；快過期（或 force）就用 refresh token 換新的並存回去。
    store 的欄位：client_id、client_secret、refresh_token、access_token、expires_at。
    沒有 refresh token 丟 GoogleAuthGone；換 token 時 Google 說授權沒了，把 token 從 store 拿掉再丟。"""
    d = store.load(None) or {}
    if not d.get("refresh_token"):
        raise GoogleAuthGone("還沒連上 Google。")
    if not force and d.get("access_token") and float(d.get("expires_at") or 0) > _gtime.time() + 60:
        return d["access_token"]
    try:
        body = google_refresh(http, d.get("client_id", ""), d.get("client_secret", ""), d["refresh_token"])
    except GoogleAuthGone:
        google_forget_tokens(store)
        raise

    def put(x):
        x["access_token"] = body["access_token"]
        x["expires_at"] = _gtime.time() + int(body.get("expires_in") or 3600)
        if body.get("refresh_token"):
            x["refresh_token"] = body["refresh_token"]
    store.update(put, {})
    return body["access_token"]


def google_forget_tokens(store) -> None:
    """拿掉 token（client_id／client_secret 留著，重新登入不用再填）。"""
    def drop(x):
        for k in ("refresh_token", "access_token", "expires_at", "device"):
            x.pop(k, None)
    store.update(drop, {})
# <<< googleauth
