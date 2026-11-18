"""🔑 USB 鑰匙：把很多支隨身碟登記成鑰匙，選一把來鎖訊息或檔案；要解開時那支碟一定要插著。

加密、驗證隨身碟全部交給 usbtether 函式庫（KeyRing）；這個工具只做指令、畫面、權限與白話訊息，不自己寫任何加密。

    AI 可以叫：/usb_keys（有哪些鑰匙、哪些插著）、/usb_drives（可以登記的隨身碟）、
               /usb_lock key=<名字> text=…｜file=…（上鎖）、/usb_unlock_check text=…｜file=…（要哪把鑰匙，不解開）
    只有你本人（ai=False＋pack.json callers human）：/usb_register、/usb_reregister、/usb_revoke、/usb_unlock、
               /usb_lock_pw、/usb_folders、/usb_folder_add、/usb_folder_rm

資料：鑰匙清單在 pack.path_of(user, "keyring")（usbtether 的 registry.json；「記住在這台」的解鎖資料在 secrets/，
Windows DPAPI 封住）；可以用的資料夾、每把鑰匙的保護方式、哪個 .utenc 檔是哪把鑰匙鎖的 → pack.store.of(user)（保管箱）。
密碼只從畫面（網頁）收，不收聊天裡打的；密碼與解開的內容不寫 log、不進 AI。
"""

import base64
import os
import re
import time

from isdtelekit.core.registry import DANGER, WRITE, Field
from synox.packs import kit

NOT_INSTALLED = ("🔑 USB 鑰匙要用的 usbtether 函式庫還沒裝在 SYNOX 的環境裡。"
                 "請照工具說明（README「安裝 usbtether」）裝好 0.3.0 版，再重新載入工具。")
NO_PASSWORD_IN_CHAT = ("🔐 密碼不要打在聊天裡：請到 SYNOX 網頁打開「🔑 USB 鑰匙」的畫面輸入。"
                       "剛剛那則訊息請自己刪掉。")
PROTECT = ("記住", "密碼", "不保護")
PROTECT_KEY = (("記住", "remember"), ("密碼", "password"), ("不保護", "none"))
PROTECT_TEXT = (("remember", "💻 記住在這台"), ("password", "🔒 要密碼"), ("none", "⚠️ 沒保護"))
OVERWRITE_WORD = "覆蓋"
SUFFIX = ".utenc"
MESSAGE_PREFIX = "utmsg1:"
MAX_FILE_INDEX = 500
MAX_LIST = 300
CHAT_KINDS = ("telegram", "discord", "console")
NOT_ENCRYPTED = "❌ 這不是 USB 鑰匙鎖過的東西（訊息要以 utmsg1: 開頭；檔案要是 .utenc）。"


def now():                       # 唯一碰時鐘的地方（測試會換掉）
    return time.time()


def empty():                     # 每次回新的空資料
    return {"schema": 1, "folders": [], "keys": {}, "files": {}}


def new_ring(home):
    """一個 usbtether KeyRing（鑰匙清單放 home）。測試換成用假隨身碟（InMemoryProbe）的版本。"""
    from usbtether import KeyRing
    return KeyRing(home=home)


class Problem(Exception):
    """要回給使用者的一句話（指令回訊息；畫面變成 kit.AppError）。"""

    def __init__(self, text):
        super().__init__(text)
        self.text = str(text)


def _lookup(table, key, fallback=""):
    for k, v in table:
        if k == key:
            return v
    return fallback


def error_table(lib):
    """usbtether 的錯誤 → 白話。子類別放在父類別前面（照順序找第一個對得上的）。"""
    return (
        (lib.KeyNotPresentError, "🔌 這把鑰匙的隨身碟沒插著：插上再試一次。"),
        (lib.PasswordRequiredError, "🔒 這把鑰匙要密碼：請到「🔑 USB 鑰匙」的畫面輸入（密碼不要打在聊天裡）。"),
        (lib.WrongPasswordError, "❌ 密碼不對。"),
        (lib.UnknownKeyError, "❌ 沒有這把鑰匙（/usb_keys 看名字）。"),
        (lib.KeyRevokedError, "⛔ 這把鑰匙已經撤銷：用它鎖的東西打不開了。"),
        (lib.DriveVerificationError, "⛔ 這支隨身碟驗不過：可能是複製出來的鑰匙資料，或不是當初登記的那一支。"),
        (lib.WrongKeyError, "❌ 這份資料不是用這把鑰匙鎖的（/usb_unlock_check 看要哪一把）。"),
        (lib.TamperedError, "❌ 解不開：資料被改過，或用錯了鑰匙。"),
        (lib.NotEncryptedError, NOT_ENCRYPTED),
        (lib.AlreadyEnrolledError, "❌ 這支隨身碟已經登記過了。真的要重新登記用 /usb_reregister（舊鑰匙鎖的東西會永遠打不開）。"),
        (lib.UnsupportedDeviceError, "❌ 這支隨身碟沒有可靠的序號，不能當鑰匙（換一支試試）。"),
        (lib.AmbiguousDeviceError, "❌ 插著好幾把鑰匙：請用 key=<名字> 指定一把。"),
        (lib.DeviceNotFoundError, "❌ 找不到那支隨身碟：磁碟代號對嗎？（/usb_drives 看）"),
        (lib.UnsupportedPlatformError, "❌ 這台電腦的系統還不支援讀隨身碟。"),
        (lib.ProbeError, "❌ 讀不到隨身碟清單，等一下再試。"),
        (lib.SecretStoreError, "❌ 這台電腦存的解鎖資料讀不到（換了 Windows 帳號或電腦？）。"),
        (lib.TokenError, "❌ 隨身碟上的鑰匙資料有問題（已經有別的鑰匙資料，或讀寫失敗）。要覆蓋用 /usb_reregister。"),
        (lib.RegistryError, "❌ 鑰匙清單讀寫失敗。"),
    )


def explain(lib, error):
    """usbtether 的例外 → 白話（不帶例外原文：裡面可能有路徑）。不是 usbtether 的例外回 None。"""
    if not isinstance(error, lib.UsbTetherError):
        return None
    for cls, text in error_table(lib):
        if isinstance(error, cls):
            return text
    return f"❌ USB 鑰匙出了問題（{type(error).__name__}）。"


def short(key_id):
    return str(key_id)[:8]


def size_text(n):
    if not n:
        return ""
    for unit, step in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6)):
        if n >= step:
            return f"{n / step:.0f} {unit}"
    return f"{n} B"


def message_key_id(lib, text):
    """utmsg1: 訊息 → 鎖它的鑰匙 id（只讀開頭的標頭，不解開；可以只給訊息的前一段）。"""
    text = (text or "").strip()
    if not text.startswith(MESSAGE_PREFIX):
        raise Problem(NOT_ENCRYPTED)
    body = re.sub(r"[^A-Za-z0-9_-]", "", text[len(MESSAGE_PREFIX):])
    body = body[: len(body) - len(body) % 4]
    try:
        blob = base64.urlsafe_b64decode(body)
        return lib.KeyRing.key_id_of(blob)
    except (ValueError, lib.UsbTetherError):
        raise Problem(NOT_ENCRYPTED)


def install(app, pack):
    g = pack.group
    tasks = getattr(app, "tasks", None)

    # ── 資料 ─────────────────────────────────────────────────────────

    def lib():
        try:
            import usbtether
        except ImportError:
            raise Problem(NOT_INSTALLED)
        return usbtether

    def ring(user):
        lib()
        return new_ring(pack.path_of(user, "keyring"))

    def data(user):
        d = pack.store.of(user).load(empty()) or empty()
        for k, v in empty().items():
            d.setdefault(k, v)
        return d

    def update(user, fn):
        def apply(d):
            for k, v in empty().items():
                d.setdefault(k, v)
            return fn(d)
        return pack.store.of(user).update(apply, empty())

    def attempt(work):
        """跑 work()；usbtether 的例外 → Problem（白話）。"""
        try:
            return work()
        except Problem:
            raise
        except Exception as e:
            text = explain(lib(), e)
            if text is None:
                raise
            raise Problem(text)

    # ── 鑰匙 ─────────────────────────────────────────────────────────

    def key_rows(user, with_presence=True):
        r = ring(user)
        keys = r.keys()
        present = {k.key_id for k in r.present_keys()} if with_presence and keys else set()
        meta = data(user)["keys"]
        return [{"id": k.key_id, "short": short(k.key_id), "label": k.label or short(k.key_id),
                 "product": k.product or f"{k.vendor_id}:{k.product_id}",
                 "registered": (k.registered_at or "")[:10],
                 "protect": (meta.get(k.key_id) or {}).get("protect", ""),
                 "present": k.key_id in present} for k in keys]

    def find_key(user, name):
        name = (name or "").strip()
        if not name:
            raise Problem("❌ 要指定一把鑰匙：key=<名字>（/usb_keys 看名字）。")
        keys = ring(user).keys()
        hit = [k for k in keys if (k.label or "").strip().lower() == name.lower()]
        if not hit and re.fullmatch(r"[0-9a-fA-F-]{8,36}", name):
            hit = [k for k in keys if k.key_id.lower().startswith(name.lower())]
        if len(hit) != 1:
            raise Problem(f"❌ 沒有叫「{name}」的鑰匙（/usb_keys 看名字）。" if not hit
                          else f"❌ 有好幾把符合「{name}」：請用完整的名字。")
        return hit[0]

    def label_of(user, key_id):
        try:
            k = ring(user).get(key_id)
        except Exception:
            return None
        return k.label or short(k.key_id)

    def drive_rows(user):
        out = []
        r = ring(user)
        labels = {k.key_id: (k.label or short(k.key_id)) for k in r.keys()}
        for d in r.drives():
            if d.registered:
                state, can = f"🔑 已經是「{labels.get(d.key_id, short(d.key_id))}」", False
            elif not d.bindable:
                state, can = "⚠️ 沒有可靠的序號，不能當鑰匙", False
            elif not d.mount_point:
                state, can = "⚠️ 沒有磁碟代號（先讓 Windows 掛上）", False
            elif d.key_id:
                state, can = "⚠️ 上面有別的鑰匙資料：要用就「重新登記」（會蓋掉）", False
            else:
                state, can = "✅ 可以登記", True
            out.append({"mount": d.mount_point or "", "product": d.product or f"{d.vendor_id}:{d.product_id}",
                        "volume": d.volume_label or "", "size": size_text(d.capacity), "state": state,
                        "can_register": can, "has_key": bool(d.key_id)})
        return out

    def password_from(values):
        """密碼只收畫面（網頁）來的；聊天裡打的一律拒（而且不碰那個值）。"""
        pw = values.get("password") or ""
        if pw and kit.caller().kind != "web":
            raise Problem(NO_PASSWORD_IN_CHAT)
        return pw or None

    def register_core(user, values, overwrite):
        label = (values.get("label") or "").strip()
        mount = (values.get("mount") or "").strip()
        protect = _lookup(PROTECT_KEY, (values.get("protect") or "記住").strip(), "remember")
        password = password_from(values)
        if protect == "password" and not password:
            raise Problem("🔒 選了「密碼」保護，要在畫面上輸入密碼（聊天裡不收密碼）。")
        if protect != "password" and password:
            raise Problem("❌ 只有選「密碼」保護時才填密碼。")
        if overwrite and (values.get("confirm") or "").strip() != OVERWRITE_WORD:
            raise Problem(f"⚠️ 重新登記會讓這支碟原本的鑰匙永遠失效：用它鎖過的東西都打不開。"
                          f"真的要做，在 confirm 填「{OVERWRITE_WORD}」。")
        r = ring(user)
        if any((k.label or "").strip().lower() == label.lower() for k in r.keys()):
            raise Problem(f"❌ 已經有一把叫「{label}」的鑰匙了，換一個名字。")
        key = r.register(mount, label=label, password=password, remember=(protect == "remember"),
                         overwrite=overwrite)

        def remember_meta(d):
            d["keys"][key.key_id] = {"protect": protect, "at": int(now())}
        update(user, remember_meta)
        return {"id": key.key_id, "label": key.label or label, "protect": protect}

    def revoke_core(user, values):
        key = find_key(user, values.get("key"))
        label = key.label or short(key.key_id)
        if (values.get("confirm") or "").strip() != label:
            raise Problem(f"⚠️ 撤銷之後，用「{label}」鎖過的訊息和檔案永遠打不開，沒有辦法救回來。"
                          f"真的要撤銷，在 confirm 再打一次鑰匙的名字「{label}」。")
        ring(user).revoke(key.key_id)

        def forget(d):
            d["keys"].pop(key.key_id, None)
        update(user, forget)
        return {"id": key.key_id, "label": label}

    # ── 資料夾與檔案 ───────────────────────────────────────────────────

    def folders(user):
        return list(data(user)["folders"])

    def inside(base, path):
        base, path = os.path.normcase(os.path.realpath(base)), os.path.normcase(os.path.realpath(path))
        try:
            return path != base and os.path.commonpath([base, path]) == base
        except ValueError:                       # 不同磁碟
            return False

    def resolve(user, raw, *, must_exist=True):
        """file 欄位 → 實際路徑；一定要在「可以用的資料夾」裡面。寫法：<資料夾編號>/<相對路徑>，或完整路徑。"""
        allowed = folders(user)
        if not allowed:
            raise Problem("📁 還沒有可以用的資料夾：先在畫面或用 /usb_folder_add <資料夾> 加一個。")
        raw = (raw or "").strip().strip('"')
        m = re.match(r"^(\d+)[/\\](.+)$", raw)
        if m:
            n = int(m.group(1))
            if not 0 < n <= len(allowed):
                raise Problem(f"❌ 沒有第 {n} 個資料夾（/usb_folders 看編號）。")
            path = os.path.join(allowed[n - 1], m.group(2))
        elif os.path.isabs(raw):
            path = raw
        else:
            raise Problem("❌ 檔案要寫成「資料夾編號/檔名」（例如 1/報告.pdf），或完整路徑。")
        real = os.path.realpath(path)
        if not any(inside(f, real) for f in allowed):
            raise Problem("⛔ 這個檔不在你設定的資料夾裡：只能鎖、解開那些資料夾裡的檔（/usb_folders 看）。")
        if must_exist and not os.path.isfile(real):
            raise Problem("❌ 找不到這個檔。")
        return real

    def lock_core(user, values, password=None):
        key = find_key(user, values.get("key"))
        text, file = values.get("text") or "", values.get("file") or ""
        if bool(text) == bool(file):
            raise Problem("❌ text（訊息）和 file（檔案）要填一個，而且只填一個。")
        r = ring(user)
        label = key.label or short(key.key_id)
        if text:
            return {"kind": "message", "label": label, "message": r.encrypt_message(text, key.key_id, password=password)}
        path = resolve(user, file)
        if path.endswith(SUFFIX):
            raise Problem("❌ 這個檔已經是鎖好的（.utenc）。")
        if os.path.exists(path + SUFFIX):
            raise Problem(f"❌ 旁邊已經有 {os.path.basename(path)}{SUFFIX}：先把它移走或改名，免得蓋掉。")
        out = str(r.encrypt_file(path, key.key_id, keep=True, password=password))

        def index(d):
            files = d["files"]
            files[os.path.normcase(out)] = key.key_id
            while len(files) > MAX_FILE_INDEX:
                files.pop(next(iter(files)))
        update(user, index)
        return {"kind": "file", "label": label, "name": os.path.basename(path), "out": os.path.basename(out)}

    def file_key_id(user, path):
        return data(user)["files"].get(os.path.normcase(path))

    def check_core(user, values):
        text, file = values.get("text") or "", values.get("file") or ""
        if bool(text) == bool(file):
            raise Problem("❌ text（訊息）和 file（檔案）要填一個，而且只填一個。")
        if text:
            key_id = message_key_id(lib(), text)
        else:
            path = resolve(user, file)
            if not path.endswith(SUFFIX):
                raise Problem(NOT_ENCRYPTED)
            key_id = file_key_id(user, path)
            if key_id is None:
                return {"known": False, "label": "", "id": "",
                        "text": "🤔 .utenc 檔本身不記是哪把鑰匙，這個檔也不是在這裡鎖的：插著的鑰匙只有一把時，解開會直接用那一把。"}
        label = label_of(user, key_id)
        if label is None:
            return {"known": False, "label": "", "id": key_id,
                    "text": f"🤔 要用的鑰匙（id {short(key_id)}…）不在你的鑰匙清單裡（別的電腦登記的，或已經撤銷）。"}
        present = ring(user).is_present(key_id)
        return {"known": True, "label": label, "id": key_id, "present": present,
                "text": f"🔑 要用「{label}」解開" + ("（現在插著）。" if present else "（現在沒插著）。")}

    def unlock_core(user, values):
        text, file = values.get("text") or "", values.get("file") or ""
        if bool(text) == bool(file):
            raise Problem("❌ text（訊息）和 file（檔案）要填一個，而且只填一個。")
        password = password_from(values)
        r = ring(user)
        key_id = find_key(user, values["key"]).key_id if (values.get("key") or "").strip() else None
        if text:
            return {"kind": "message", "text": r.decrypt_message(text, key_id=key_id, password=password)}
        path = resolve(user, file)
        if not path.endswith(SUFFIX):
            raise Problem(NOT_ENCRYPTED)
        target = path[: -len(SUFFIX)]
        if os.path.exists(target):
            raise Problem(f"❌ 旁邊已經有 {os.path.basename(target)}：先把它移走或改名，免得蓋掉。")
        out = str(r.decrypt_file(path, key_id or file_key_id(user, path), keep=True, password=password))
        return {"kind": "file", "name": os.path.basename(path), "out": os.path.basename(out)}

    def folder_add_core(user, values):
        raw = (values.get("path") or "").strip().strip('"')
        if not raw or not os.path.isabs(raw):
            raise Problem("❌ 要給完整的資料夾路徑。")
        real = os.path.realpath(raw)
        if not os.path.isdir(real):
            raise Problem("❌ 找不到這個資料夾。")
        if os.path.dirname(real) == real:
            raise Problem("⛔ 不能加整個磁碟，請選一個資料夾。")

        def add(d):
            have = [os.path.normcase(f) for f in d["folders"]]
            if os.path.normcase(real) not in have:
                d["folders"].append(real)
            return len(d["folders"])
        n = update(user, add)
        return {"n": n, "name": os.path.basename(real) or real}

    def folder_rm_core(user, values):
        n = int(values["n"])

        def drop(d):
            return d["folders"].pop(n - 1) if 0 < n <= len(d["folders"]) else None
        gone = update(user, drop)
        if gone is None:
            raise Problem(f"❌ 沒有第 {n} 個資料夾（/usb_folders 看編號）。")
        return {"name": os.path.basename(gone) or gone}

    def browse(user, params):
        allowed = folders(user)
        try:
            n = int(params.get("folder") or "1")
        except ValueError:
            n = 0
        if not 0 < n <= len(allowed):
            return {"folder": 0, "sub": "", "dirs": [], "files": []}
        base = allowed[n - 1]
        sub = (params.get("sub") or "").strip().strip("/\\")
        here = os.path.realpath(os.path.join(base, sub)) if sub else os.path.realpath(base)
        if sub and not inside(base, here):
            raise Problem("⛔ 只能看你設定的資料夾裡面。")
        dirs, files = [], []
        try:
            names = sorted(os.listdir(here))
        except OSError:
            raise Problem("❌ 打不開這個資料夾。")
        for name in names[:MAX_LIST]:
            if name.startswith("."):
                continue
            full = os.path.join(here, name)
            if os.path.isdir(full):
                dirs.append(name)
            elif os.path.isfile(full):
                files.append({"name": name, "locked": name.endswith(SUFFIX)})
        return {"folder": n, "sub": sub.replace("\\", "/"), "dirs": dirs, "files": files}

    # ── 指令 ─────────────────────────────────────────────────────────

    def say(api, chat_id, work, fmt):
        """跑 work()；usbtether 的錯誤變白話。人在聊天打字的：丟到背景跑（列隨身碟要 2 秒左右，不卡住收訊）。"""
        def go():
            try:
                text = fmt(attempt(work))
            except Problem as p:
                text = p.text
            api.send_message(chat_id, text)
        if tasks is not None and kit.caller().kind in CHAT_KINDS:
            tasks.run(go, name=f"pack-{pack.name}")
        else:
            go()

    def keys_text(rows):
        if not rows:
            return "🔑 還沒有登記任何 USB 鑰匙。插上隨身碟，到「🔑 USB 鑰匙」的畫面登記（/usb_drives 看插著哪些）。"
        on = sum(1 for k in rows if k["present"])
        lines = [f"🔑 USB 鑰匙（{len(rows)} 把；插著 {on} 把）"]
        for k in rows:
            lines.append(f"{'🟢' if k['present'] else '⚪'} {k['label']} — {k['product']}"
                         f" · {_lookup(PROTECT_TEXT, k['protect'], '保護方式不明')} · 登記 {k['registered']} · id {k['short']}")
        return "\n".join(lines)

    def drives_text(rows):
        if not rows:
            return "💽 沒有插著的隨身碟。"
        lines = [f"💽 插著的隨身碟（{len(rows)} 支）"]
        for d in rows:
            extra = "、".join(x for x in (f"標籤 {d['volume']}" if d["volume"] else "", d["size"]) if x)
            lines.append(f"• {d['mount'] or '（沒有磁碟代號）'} {d['product']}" + (f"（{extra}）" if extra else "")
                         + f" — {d['state']}")
        return "\n".join(lines)

    def lock_text(out):
        if out["kind"] == "message":
            return (f"🔒 用「{out['label']}」鎖好了（解開時這支碟要插著）：\n{out['message']}")
        return (f"🔒 用「{out['label']}」鎖好了：{out['name']} → {out['out']}\n"
                f"原檔還在：確認鎖好的檔解得開之後，再自己決定要不要刪原檔。")

    def unlock_text(out):
        if out["kind"] == "message":
            return "🔓 解開了（只給你看）：\n" + out["text"]
        return f"🔓 解開了：{out['name']} → {out['out']}（.utenc 檔還在）"

    def folders_text(rows):
        if not rows:
            return "📁 還沒有可以用的資料夾：/usb_folder_add <完整的資料夾路徑>"
        return "\n".join(["📁 可以鎖、解開檔案的資料夾："] + [f"{i}. {f}" for i, f in enumerate(rows, 1)])

    def keys_cmd(api, chat_id, user, values):
        say(api, chat_id, lambda: key_rows(user), keys_text)

    def drives_cmd(api, chat_id, user, values):
        say(api, chat_id, lambda: drive_rows(user), drives_text)

    def lock_cmd(api, chat_id, user, values):
        say(api, chat_id, lambda: lock_core(user, values), lock_text)

    def lock_pw_cmd(api, chat_id, user, values):
        say(api, chat_id, lambda: lock_core(user, values, password_from(values)), lock_text)

    def check_cmd(api, chat_id, user, values):
        say(api, chat_id, lambda: check_core(user, values), lambda out: out["text"])

    def unlock_cmd(api, chat_id, user, values):
        kit.require_human()
        say(api, chat_id, lambda: unlock_core(user, values), unlock_text)

    def register_cmd(api, chat_id, user, values):
        kit.require_human()
        say(api, chat_id, lambda: register_core(user, values, False),
            lambda out: f"🔑 登記好了：「{out['label']}」（{_lookup(PROTECT_TEXT, out['protect'])}）。"
                        + ("\n⚠️ 沒有保護：撿到這支碟的人也能用它解開。" if out["protect"] == "none" else ""))

    def reregister_cmd(api, chat_id, user, values):
        kit.require_human()
        say(api, chat_id, lambda: register_core(user, values, True),
            lambda out: f"🔑 重新登記好了：「{out['label']}」。這支碟原本的鑰匙已經失效。")

    def revoke_cmd(api, chat_id, user, values):
        kit.require_human()
        say(api, chat_id, lambda: revoke_core(user, values),
            lambda out: f"⛔ 撤銷了「{out['label']}」：用它鎖過的東西再也打不開。")

    def folders_cmd(api, chat_id, user, values):
        api.send_message(chat_id, folders_text(folders(user)))

    def folder_add_cmd(api, chat_id, user, values):
        kit.require_human()
        say(api, chat_id, lambda: folder_add_core(user, values),
            lambda out: f"📁 加好了：第 {out['n']} 個資料夾「{out['name']}」。")

    def folder_rm_cmd(api, chat_id, user, values):
        kit.require_human()
        say(api, chat_id, lambda: folder_rm_core(user, values),
            lambda out: f"📁 拿掉了「{out['name']}」（資料夾裡的檔不會動）。")

    key_field = Field("key", "鑰匙的名字", required=True, check=kit.max_len(40))
    text_field = Field("text", "訊息")
    file_field = Field("file", "檔案（資料夾編號/檔名，例如 1/報告.pdf）")
    password_field = Field("password", "密碼（只在畫面輸入）")
    confirm_field = Field("confirm", "確認")
    reg_fields = [Field("mount", "隨身碟的磁碟代號（例如 E:）", required=True, check=kit.max_len(260)),
                  Field("label", "鑰匙的名字", required=True, check=kit.max_len(40)),
                  Field("protect", "保護方式", choices=PROTECT, default="記住"),
                  password_field]

    kit.command(g, "keys", keys_cmd, "列出 USB 鑰匙，哪些插著")
    kit.command(g, "drives", drives_cmd, "列出插著的隨身碟，哪些可以登記")
    kit.command(g, "lock", lock_cmd, "用一把鑰匙鎖訊息或檔案", effect=WRITE, positional=2,
                fields=[key_field, text_field, file_field])
    kit.command(g, "unlock_check", check_cmd, "看一段鎖住的訊息或檔案要哪把鑰匙（不解開）", positional=1,
                fields=[text_field, file_field])
    kit.command(g, "register", register_cmd, "把一支隨身碟登記成鑰匙（只有你本人）", effect=WRITE, ai=False,
                positional=2, fields=reg_fields)
    kit.command(g, "reregister", reregister_cmd, "重新登記一支隨身碟：舊鑰匙永遠失效（只有你本人）", effect=DANGER,
                ai=False, positional=2, fields=reg_fields + [confirm_field])
    kit.command(g, "revoke", revoke_cmd, "撤銷一把鑰匙：用它鎖的東西永遠打不開（只有你本人）", effect=DANGER,
                ai=False, positional=1, fields=[key_field, confirm_field])
    kit.command(g, "unlock", unlock_cmd, "解開鎖住的訊息或檔案（只有你本人）", effect=WRITE, ai=False, positional=1,
                fields=[text_field, file_field, Field("key", "鑰匙的名字（選填）", check=kit.max_len(40)),
                        password_field])
    kit.command(g, "lock_pw", lock_pw_cmd, "用要密碼的鑰匙上鎖（密碼只在畫面輸入；只有你本人）", effect=WRITE,
                ai=False, positional=2, fields=[key_field, text_field, file_field, password_field])
    kit.command(g, "folders", folders_cmd, "列出可以鎖、解開檔案的資料夾（只有你本人）", ai=False)
    kit.command(g, "folder_add", folder_add_cmd, "加一個可以鎖、解開檔案的資料夾（只有你本人）", effect=WRITE,
                ai=False, fields=[Field("path", "完整的資料夾路徑", required=True, check=kit.max_len(260))])
    kit.command(g, "folder_rm", folder_rm_cmd, "拿掉一個資料夾（只有你本人）", effect=WRITE, ai=False,
                fields=[Field("n", "編號", required=True, check=kit.positive_int)])
    for name in ("register", "reregister", "lock_pw", "unlock"):     # 可能帶密碼的指令：後面的字不進 log
        kit.redact_in_logs(f"/{pack.manifest.group}_{name}")

    # ── 畫面（web.app）：讀＝現在的樣子；動作＝同名的指令（權限、欄位檢查照指令） ─────────────

    if not hasattr(pack, "app_read"):                 # 舊版 SYNOX 沒有工具畫面：照樣能用指令
        return

    def app_call(work):
        try:
            return attempt(work)
        except Problem as p:
            raise kit.AppError(p.text)

    def ready():
        try:
            lib()
            return {"ready": True, "message": ""}
        except Problem as p:
            return {"ready": False, "message": p.text}

    def read_keys(user, params):
        state = ready()
        if not state["ready"]:
            return {**state, "keys": []}
        return {**state, "keys": app_call(lambda: key_rows(user))}

    def read_drives(user, params):
        state = ready()
        if not state["ready"]:
            return {**state, "drives": []}
        return {**state, "drives": app_call(lambda: drive_rows(user))}

    pack.app_read("keys", read_keys)
    pack.app_read("drives", read_drives)
    pack.app_read("folders", lambda user, params: {"folders": [{"n": i, "path": f} for i, f in
                                                               enumerate(folders(user), 1)]})
    pack.app_read("browse", lambda user, params: app_call(lambda: browse(user, params)))
    pack.app_read("check", lambda user, params: app_call(lambda: check_core(user, params)))

    def describe_reregister(user, values):
        return (f"重新登記 {values.get('mount', '')} 會讓這支碟原本的鑰匙永遠失效：用它鎖過的訊息和檔案都打不開，"
                f"沒有辦法救回來。")

    def describe_revoke(user, values):
        return (f"撤銷「{values.get('key', '')}」之後，用它鎖過的訊息和檔案永遠打不開，沒有辦法救回來。"
                f"隨身碟本身不會被清掉。")

    pack.app_action("register", lambda user, values: app_call(lambda: register_core(user, values, False)))
    pack.app_action("reregister", lambda user, values: app_call(lambda: register_core(user, values, True)),
                    describe=describe_reregister)
    pack.app_action("revoke", lambda user, values: app_call(lambda: revoke_core(user, values)),
                    describe=describe_revoke)
    pack.app_action("lock_pw", lambda user, values: app_call(lambda: lock_core(user, values, password_from(values))))
    pack.app_action("unlock", lambda user, values: app_call(lambda: unlock_core(user, values)))
    pack.app_action("folder_add", lambda user, values: app_call(lambda: folder_add_core(user, values)))
    pack.app_action("folder_rm", lambda user, values: app_call(lambda: folder_rm_core(user, values)))
