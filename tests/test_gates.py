"""每個工具都要過 SYNOX 的第 1 關（pack.json 宣告檢查，嚴格版）與第 2 關（靜態掃描），宣告 requires.sdk（ADR-0001），
再加一層這個 repo 自己的規矩：
- 本機工具（notes、remind、todo、money、calendar、bullet、diary）：不連網、不開別的程式、不動態執行，
  碰檔案只准 calendar 的 _write_own／os.listdir／os.remove（pack.path_of 底下）；
  diary 另外讀自己 pack 資料夾的 content/（題組、版型）、讀使用者放在自己資料夾的 import.json。
  記帳（0.4.0 起是多檔工具、帳本是自己資料夾裡的 SQLite）宣告 filesystem:own，碰檔案與開資料庫只准在列出的幾個檔，
  其餘照隔離規則（不用共用資料夾、pack.store 只准 .of(user)、模組層級沒有快取）。
- 接外面服務的（notion、todoist、google-calendar、apple-reminders，2026-11-12 從 SYNOX connectors/ 搬來；google-sheets 2026-11-16 新寫）：
  只宣告、也只用得到 network（requests）；一樣不開別的程式、不動態執行、不碰檔案、不讀環境變數；只給擁有者（access: owner）。
- 多人契約（docs/adr/0006 第 2 節）：入口欄位都明寫（test_declares_scope）；M3 不用 chat_id 分人、M4 模組層級不放會變的狀態、
  M5 面板只打自己的 API、不用 innerHTML／localStorage（最後一段的 AST／文字檢查，連檢查本身也有壞樣子的測試）。"""

import ast
import os
import re

import pytest

from synox.packs import manifest as synox_manifest
from synox.packs import scan as synox_scan
from synox.sdk import author

from conftest import PACK_NAMES, PACKS, PLANNER

CONNECT = ("notion", "todoist", "google-calendar", "google-sheets", "apple-reminders")
# 叫這台電腦上另外裝好的程式（video-notes：使用者自己裝的 Video Notes）：只給擁有者、只經 SYNOX 的 JobRunner（app.jobs）開程式，
# 自己不連網、不動態執行；讀檔只讀那支程式產生的筆記。規則在 test_local_program_tools_only_use_app_jobs。
LOCAL_PROGRAM = ("video-notes",)
# pack 名稱 → 宣告的 capabilities（其他一律不准；每個都要明寫，沒有就寫 []——ADR-0006 C5）
ALLOWED_CAPS = {"calendar": ["filesystem:own"], "todo": [], "bullet": [], "money": ["filesystem:own"], "notes": [],
                "diary": ["filesystem:own"],
                "guitar": [],
                **{name: ["network"] for name in CONNECT},
                "video-notes": ["subprocess", "filesystem:any"]}
# 宣告得比掃描看到的多（誠實的超集合）：{工具: (掃描會看到的, 理由)}
SCAN_CAPS = {"video-notes": (["filesystem:own", "subprocess"],
                             "讀的筆記在 Video Notes 自己的輸出資料夾（SYNOX 外面），路徑是執行時才知道的變數，掃描只看得出 filesystem:own")}
ALLOWED_IMPORTS = ("re", "time", "datetime", "dataclasses", "collections", "os", "isdtelekit", "synox")
# 本機工具個別多准的（calendar：匯出檔名的亂數，ADR-0006 P1）
LOCAL_EXTRA_IMPORTS = {"calendar": ("secrets",)}
# 接外面服務的多准：requests、urllib.parse、編碼／雜湊／亂數、XML（CalDAV）、時區、型別、log
CONNECT_IMPORTS = ALLOWED_IMPORTS + ("requests", "urllib", "base64", "hashlib", "secrets", "uuid", "xml", "zoneinfo",
                                     "typing", "logging", "__future__")
FORBIDDEN_IMPORTS = ("requests", "urllib", "http", "socket", "ssl", "subprocess", "multiprocessing", "ctypes",
                     "importlib", "pickle", "marshal", "shutil", "pathlib", "asyncio", "threading")
# diary（每日日記的移植）多准：json、html（跳脫與 html.parser 清理 HTML）、xml.sax.saxutils（卡片 SVG 的跳脫）、
# hashlib（匯入版型時比對內建的指紋）、typing、__future__——都是標準函式庫、不連網、不開程式
DIARY_IMPORTS = ALLOWED_IMPORTS + ("json", "html", "xml", "hashlib", "typing", "__future__")
CONNECT_FORBIDDEN = tuple(m for m in FORBIDDEN_IMPORTS if m not in ("requests", "urllib"))
# 記帳（0.4.0 起是多檔工具：pack.py＋bridge/＋engine/，帳本是自己資料夾裡的 SQLite）：多准資料庫、CSV、雜湊、
# 小數、日期、型別這些標準庫；pathlib／shutil／tempfile／threading 准用，但只准在下面列的檔（見 MONEY_ONLY_IN）。
# 連網、開別的程式、動態載入（requests、socket、subprocess、importlib、pickle…）一樣不准。
MONEY_IMPORTS = ALLOWED_IMPORTS + ("__future__", "typing", "types", "json", "sqlite3", "csv", "io", "hashlib", "decimal",
                                   "math", "calendar", "contextlib", "contextvars", "threading", "functools", "itertools",
                                   "unicodedata", "difflib", "logging", "pathlib", "shutil", "tempfile", "html")
MONEY_FORBIDDEN = tuple(m for m in FORBIDDEN_IMPORTS if m not in ("pathlib", "shutil", "threading"))
# 碰檔案、開資料庫、開執行緒鎖：只准在這幾個檔（其餘的檔只拿 book.conn 與 session 給的路徑）
MONEY_ONLY_IN = {
    "os": {"bridge/session.py", "engine/ids.py"},                         # 建自己的帳本資料夾；ids 用 os.urandom
    "threading": {"bridge/session.py"},                                  # 每個人一把寫入鎖
    "shutil": {"engine/services/attachments.py", "engine/services/book.py"},
    "tempfile": {"engine/services/book.py"},
    "pathlib": {"engine/db.py", "engine/paths.py", "engine/seed.py", "engine/services/attachments.py",
                "engine/services/book.py", "engine/services/csvio.py",
                "engine/services/statements/files.py"},                # 對帳單匯入：只讀對帳單與格式描述檔
    "html": {"engine/services/statements/tables.py"},                 # html.parser 讀網頁存下來的對帳單表格（只解析字串，不開檔、不連網）
}
MONEY_FILE_CALLS = {                                                     # 呼叫 → 准用的檔
    "open": {"engine/services/book.py"},
    "sqlite3.connect": {"engine/db.py", "engine/services/book.py"},
    "os.makedirs": {"bridge/session.py"},
    "os.urandom": {"engine/ids.py"},
    "shutil.copyfile": {"engine/services/attachments.py", "engine/services/book.py"},
    "shutil.rmtree": {"engine/services/book.py"},
    "tempfile.mkdtemp": {"engine/services/book.py"},
}
MONEY_WRITES = re.compile(r"\.(write_text|write_bytes|unlink|rmdir|touch)\(")
MONEY_WRITERS = {"engine/db.py", "engine/services/book.py", "engine/services/csvio.py", "engine/services/attachments.py"}
FORBIDDEN_TEXT = (r"\bos\.system\b", r"\bos\.popen\b", r"\bsubprocess\b", r"\beval\(", r"\bexec\(", r"__import__",
                  r"\bos\.environ\b", r"\bgetenv\b", r"app\.jobs", r"\bapi\.post\(")


def folder(name):
    return os.path.join(PACKS, name)


def source(name):
    with open(os.path.join(folder(name), "pack.py"), "r", encoding="utf-8") as f:
        return f.read()


def test_every_tool_is_listed():
    assert set(PACK_NAMES) == set(ALLOWED_CAPS)


@pytest.mark.parametrize("name", PACK_NAMES)
def test_gate1_manifest_strict(name):
    m = synox_manifest.read(folder(name), strict=True)
    assert m.kind == "python" and m.access == ("owner" if name in CONNECT + LOCAL_PROGRAM else "member") and m.name == name
    assert os.path.isfile(m.skill_path) and os.path.isfile(os.path.join(folder(name), "README.md"))
    assert m.capabilities_declared and m.capabilities == ALLOWED_CAPS[name]
    if name in PLANNER:
        assert m.storage == "vault" and m.web["panel"] == "panel.html" and m.web["api"] == ["today"]


@pytest.mark.parametrize("name", PACK_NAMES)
def test_declares_sdk_range_that_this_synox_satisfies(name):
    """ADR-0001：每個工具都宣告 requires.sdk（與 requires.synox），而且拿來測試的這份 SYNOX 合。"""
    m = synox_manifest.read(folder(name), strict=True)
    assert m.requires.get("sdk") and m.requires.get("synox")
    assert synox_manifest.synox_problem(m) is None
    assert synox_manifest.sdk_problem(m, author.SDK_VERSION) is None


@pytest.mark.parametrize("name", PACK_NAMES)
def test_declares_stage(name):
    """每個工具都明寫穩定度（test／alpha／beta／stable；README「穩定度」），SYNOX 讀得到同一個值。"""
    m = synox_manifest.read(folder(name), strict=True)
    assert m.stage_declared and m.stage in synox_manifest.STAGES and m.raw["stage"] == m.stage


@pytest.mark.parametrize("name", PACK_NAMES)
def test_declares_scope(name):
    """ADR-0006 入口標準：只能私訊、資料每人一份、落地是密文；access、surfaces、data、storage、capabilities 都明寫
    （SYNOX 有預設值的也寫，預設值改了不會悄悄變）。"""
    m = synox_manifest.read(folder(name), strict=True)
    assert m.surfaces == ["dm"] and m.data == "user" and m.storage == "vault"
    assert m.surfaces_declared and m.data_declared and m.capabilities_declared
    assert all(key in m.raw for key in ("access", "surfaces", "data", "storage", "capabilities"))
    assert m.raw["surfaces"] == ["dm"] and m.raw["data"] == "user" and m.raw["storage"] == "vault"


@pytest.mark.parametrize("name", PACK_NAMES)
def test_gate2_static_scan_passes(name):
    m = synox_manifest.read(folder(name), strict=True)
    report = synox_scan.scan(m)
    assert report.problems(m) == [], report.format(m)
    assert report.required_capabilities == SCAN_CAPS.get(name, (ALLOWED_CAPS[name],))[0], report.format(m)


LOCAL_PROGRAM_IMPORTS = ("json", "os", "re", "threading", "time", "isdtelekit", "synox")


@pytest.mark.parametrize("name", LOCAL_PROGRAM)
def test_local_program_tools_only_use_app_jobs(name):
    """叫別的程式的工具：程式只經 app.jobs（JobRunner：argv 陣列、不經 shell、逾時與取消收整棵樹），
    不 import subprocess／連網／動態載入；不讀環境變數；open 只在兩個地方：讀筆記（read_note）與寫網址清單（launch）。"""
    text = source(name)
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        else:
            continue
        for mod in mods:
            assert mod.split(".")[0] in LOCAL_PROGRAM_IMPORTS, f"{name}: unexpected import {mod}"
            assert mod.split(".")[0] not in ("subprocess", "socket", "requests", "urllib", "http", "importlib", "ctypes")
    for pattern in FORBIDDEN_TEXT:
        if pattern != r"app\.jobs":
            assert not re.search(pattern, text), f"{name}: {pattern}"
    assert "shell=True" not in text and "os.system" not in text
    owners = {}                                       # 每個節點 → 最裡面那一層函式（ast.walk 先外後內，後寫的蓋掉）
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                owners[id(node)] = fn.name
    opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "open"]
    assert sorted(owners.get(id(n)) for n in opens) == ["launch", "read_note"]


@pytest.mark.parametrize("name", [n for n in PACK_NAMES if n != "money" and n not in LOCAL_PROGRAM])
def test_no_network_subprocess_exec_or_stray_files(name):
    text = source(name)
    tree = ast.parse(text)
    if name in CONNECT:
        forbidden, allowed = CONNECT_FORBIDDEN, CONNECT_IMPORTS
    else:
        forbidden = FORBIDDEN_IMPORTS
        allowed = (DIARY_IMPORTS if name == "diary" else ALLOWED_IMPORTS) + LOCAL_EXTRA_IMPORTS.get(name, ())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        else:
            continue
        for mod in mods:
            assert mod.split(".")[0] not in forbidden, f"{name}: import {mod}"
            assert mod.split(".")[0] in allowed, f"{name}: unexpected import {mod}"
            if name in CONNECT and mod.split(".")[0] == "urllib":
                assert mod == "urllib.parse", f"{name}: 連網只准用 requests（{mod}）"
    for pattern in FORBIDDEN_TEXT:
        assert not re.search(pattern, text), f"{name}: {pattern}"
    opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "open"]
    os_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and getattr(n.func.value, "id", None) == "os"]
    if name == "calendar":
        # 唯一的 open 在 _write_own 裡；os.* 只有：匯出前列出自己的資料夾、清掉舊的匯出檔、送完 .ics 之後刪掉；
        # 路徑都來自 pack.path_of(user, …)（ADR-0006 P1）
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_write_own")
        assert len(opens) == 1 and opens[0] in list(ast.walk(fn))
        assert sorted(n.func.attr for n in os_calls) == ["listdir", "remove", "remove"]
        assert all(ast.unparse(n.args[0]).startswith("pack.path_of(user") or ast.unparse(n.args[0]) == "path"
                   for n in os_calls)
        assert "path = pack.path_of(user, " in text and "_write_own(path," in text and "os.remove(path)" in text
        assert "pack.path(" not in text                 # data: user 不能用全部人共用的資料夾
    elif name == "diary":
        # open 只在三個地方：_write_own（寫要傳給使用者的檔，pack.path_of 底下）、_read_own（使用者放的 import.json）、
        # Content.text（讀自己 pack 資料夾的 content/）；os.* 只有 remove（傳完就刪）與 listdir（列 content/）
        owners = {}
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef):
                for n in ast.walk(fn):
                    owners.setdefault(id(n), fn.name)
        assert sorted(owners.get(id(n)) for n in opens) == ["_read_own", "_write_own", "text"]
        assert sorted({n.func.attr for n in os_calls}) == ["listdir", "remove"]
        assert "pack.path(" not in text and "pack.path_of(user, name)" in text and "pack.path_of(user, \"import.json\")" in text
    else:
        assert not opens and not os_calls, f"{name} must not touch files"
        assert "import os" not in text


def money_sources():
    """packs/money 底下每個 .py（不含 __pycache__）→ {相對路徑: 內容}。"""
    return python_sources("money")


def python_sources(name):
    """packs/<name> 底下每個 .py（不含 __pycache__）→ {相對路徑: 內容}。單檔工具只有 pack.py；money 是多檔工具。"""
    root = folder(name)
    out = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(base, f)
                with open(path, "r", encoding="utf-8") as fh:
                    out[os.path.relpath(path, root).replace(os.sep, "/")] = fh.read()
    return out


def _call_name(node):
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        return f"{fn.value.id}.{fn.attr}"
    return None


def test_money_no_network_subprocess_exec_and_files_only_where_allowed():
    """記帳是多檔工具：每個 .py 都看。不連網、不開別的程式、不動態執行、不讀環境變數；
    碰檔案／開資料庫／開鎖的模組與呼叫只准在 MONEY_ONLY_IN／MONEY_FILE_CALLS 列的檔（帳本的位置一律來自 session.py 的
    pack.path_of）；其他檔只拿 book.conn。"""
    sources = money_sources()
    assert {"pack.py", "bridge/session.py", "engine/db.py"} <= set(sources)
    for rel, text in sources.items():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:                                    # 自己資料夾裡的相對 import（from ..engine import db）
                    continue
                mods = [node.module or ""]
            elif isinstance(node, ast.Call):
                call = _call_name(node)
                if call in MONEY_FILE_CALLS:
                    assert rel in MONEY_FILE_CALLS[call], f"money/{rel}: {call}(…) 只准在 {sorted(MONEY_FILE_CALLS[call])}"
                continue
            else:
                continue
            for mod in mods:
                top = mod.split(".")[0]
                assert top not in MONEY_FORBIDDEN, f"money/{rel}: import {mod}"
                assert top in MONEY_IMPORTS, f"money/{rel}: unexpected import {mod}"
                if top in MONEY_ONLY_IN:
                    assert rel in MONEY_ONLY_IN[top], f"money/{rel}: import {mod} 只准在 {sorted(MONEY_ONLY_IN[top])}"
        for pattern in FORBIDDEN_TEXT:
            assert not re.search(pattern, text), f"money/{rel}: {pattern}"
        if MONEY_WRITES.search(text):
            assert rel in MONEY_WRITERS, f"money/{rel}: 寫檔／刪檔只准在 {sorted(MONEY_WRITERS)}"


def test_money_isolation_static_rules():
    """隔離規則（packs/money/CONTRACT.md）：不用全部人共用的資料夾（pack.path(）、pack.store 只准在 legacy.py 而且一定是
    .of(user)；模組層級沒有會變的空容器（快取）與 lru_cache。"""
    for rel, text in money_sources().items():
        assert "pack.path(" not in text, rel
        for m in re.finditer(r"pack\.store\.(\w+)", text):
            assert rel == "bridge/legacy.py" and m.group(1) == "of", f"money/{rel}: pack.store.{m.group(1)}"
        assert not re.search(r"\b(lru_cache|functools\.cache)\b", text), rel
        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                v = node.value
                empty = ((isinstance(v, (ast.Dict, ast.List, ast.Set)) and not getattr(v, "keys", getattr(v, "elts", None)))
                         or (isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in ("dict", "list", "set")
                             and not v.args and not v.keywords))
                assert not empty, f"money/{rel}:{node.lineno} 模組層級的空容器（會被大家共用）"


def test_no_personal_data_in_repo():
    """repo 裡不放真的 Telegram id、token 或 email（範例一律佔位字）。哪些 email 算佔位，跟 scripts/public_check.py 同一份規則
    （完整的公開版檢查在 tests/test_public_check.py；這裡是不靠 git 的快速版，連 .gitignore 掉的檔也看）。"""
    import public_check
    root = os.path.dirname(PACKS)
    token = re.compile(r"\d{8,10}:[A-Za-z0-9_-]{30,}")
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", ".venv", "__pycache__", ".pytest_cache", "dist")]
        for f in files:
            if not f.endswith((".py", ".md", ".json", ".html")):
                continue
            with open(os.path.join(base, f), "r", encoding="utf-8") as fh:
                text = fh.read()
            assert not token.search(text), f"{f}: looks like a bot token"
            assert all(public_check.email_allowed(m.group(0)) for m in public_check.EMAIL.finditer(text)), \
                f"{f}: has an email address"


@pytest.mark.parametrize("name", PACK_NAMES)
def test_synox_sdk_check_and_zip(name, tmp_path):
    """synox-sdk check／zip（等於 SYNOX 的 scripts/pack_zip.py）：過第 1、2 關，打包出 .synoxpack。"""
    import zipfile

    from synox import sdk
    check = sdk.Pack.load(folder(name))
    assert [p for p in check.problems if not p.startswith("integrity")] == []
    path = sdk.zip_pack(folder(name), str(tmp_path))
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
    assert {"pack.json", "pack.py", "SKILL.md", "README.md"} <= names
    if os.path.isfile(os.path.join(folder(name), "panel.html")):
        assert "panel.html" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)


# ── 多人契約的靜態檢查（ADR-0006 第 2 節 M3、M4、M5） ───────────────────────────

CHAT_ID = re.compile(r"^chat_id\w*$")
PER_PERSON = ("of", "path_of", "secret")                 # pack.store.of(…)、pack.path_of(…)、pack.secret(…)
DICT_KEY_CALLS = ("get", "setdefault", "pop", "__getitem__", "__setitem__")
MUTATORS = ("append", "extend", "insert", "update", "setdefault", "pop", "popitem", "clear", "add", "discard",
            "remove", "sort", "reverse", "__setitem__", "__delitem__")
MUTABLE_LITERALS = (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)
MUTABLE_CALLS = ("dict", "list", "set", "defaultdict", "OrderedDict", "Counter", "deque")
# 模組層級准用的呼叫（回的東西不會變）：正規表示式、tuple、frozenset
CONSTANT_CALLS = ("re.compile", "_re.compile", "_gre.compile", "tuple", "frozenset")    # _gre＝googleauth 的 import re as _gre
# M4 個別工具暫時准的「X = None」測試掛勾：{工具: {名字: 理由}}（todoist、google-calendar 已經改成 def new_session()）。
M4_HOOKS = {
    "google-sheets": {"transport": "測試換成假的 Google（None＝requests）；只在測試裡被換、執行時不寫入，不放任何人的資料。"
                                   "跟 todoist、google-calendar 一樣改成 def new_session() 是 google-sheets 自己的下一步"},
}
# M4 個別工具多准的模組層級呼叫，或「拿常數表做的複本」（寫成 set(FIXED) 的樣子）：
# {工具: {呼叫: 為什麼不算「會變的共用狀態」}}。新增一條要寫理由。
M4_EXTRA_CALLS = {
    "google-sheets": {
        "set(FIXED)": "FIXED 是兩個欄位名（字串）的 list，只拿來比對有沒有撞名；set() 回新的集合，沒有共用到可變的東西",
    },
    "diary": {
        "_Undefined": "範本引擎的「沒有值」記號：類別沒有任何欄位，大家共用同一個記號才比得出 `is UNDEF`",
        "timedelta": "不可變的時間長度（6 小時）",
    },
    "money": {
        "frozen": "bridge/table.py 的 frozen() 回 MappingProxyType（唯讀的指令表）",
        "MappingProxyType": "唯讀的對照表",
        "timedelta": "不可變的時間長度",
        "object": "「沒給」的記號（sentinel），沒有任何欄位",
        "model": "engine/schemas.py 的 model() 建出來的是類別（紀錄的格式），不是資料",
        "_add_as": "回一個函式（記一筆支出／收入／轉帳／借貸的指令），沒有狀態",
        "str.maketrans": "全形數字→半形的對照表，只給 str.translate 讀，整個工具沒有地方改它",
        "ROWS_SQL.replace": "回一個字串（SQL）",
        "Path(__file__).with_name": "自己 pack 資料夾裡 schema 檔的路徑（Path 不可變）",
        "logging.getLogger": "Python 的 logger 本來就是整個程式共用的，不放任何人的資料",
        "contextvars.ContextVar": "engine/paths.py 用它記「這一次呼叫是誰的帳本」；ContextVar 每個執行情境各一份、"
                                  "session 進出時 set／reset（就是用來避免模組層級共用狀態的機制）",
    },
}


def _uses_chat_id(node) -> bool:
    return any(isinstance(n, ast.Name) and CHAT_ID.match(n.id) for n in ast.walk(node))


def chat_id_problems(tree) -> list:
    """M3：chat_id 不能當分人的鍵——不准出現在 store.of(…)、path_of(…)、secret(…) 的參數，也不准當 dict 的鍵。"""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            args = list(n.args) + [k.value for k in n.keywords]
            if n.func.attr in PER_PERSON and any(_uses_chat_id(a) for a in args):
                out.append(f"{n.lineno}: {n.func.attr}(…) 用了 chat_id（要用 user 或 cb.clicker_id）")
            if n.func.attr in DICT_KEY_CALLS and n.args and _uses_chat_id(n.args[0]):
                out.append(f"{n.lineno}: .{n.func.attr}(chat_id…) 把 chat_id 當鍵")
        elif isinstance(n, ast.Subscript) and _uses_chat_id(n.slice):
            out.append(f"{n.lineno}: [chat_id] 把 chat_id 當鍵")
        elif isinstance(n, ast.Dict) and any(k is not None and _uses_chat_id(k) for k in n.keys):
            out.append(f"{n.lineno}: {{chat_id: …}} 把 chat_id 當鍵")
    return out


def _constant_name(name: str) -> bool:
    return name.lstrip("_").isupper()


def module_state_problems(tree, hooks=(), extra_calls=()) -> list:
    """M4：模組層級不放會變的狀態。
    - 空的 {}／[]／set()／dict()／list()（快取、「上一次的結果」的樣子）不准；
    - 有內容的 dict／list／set 只准當常數表：名字全大寫、整個模組裡沒有任何地方改它、也不拿它淺拷貝當資料（用 def empty()）；
    - 模組層級的呼叫只准 re.compile、tuple、frozenset（不准放共用的 HTTP session 之類的物件）；
    - X = None（測試換掉用的掛勾）不准，hooks 裡的除外；函式裡不准 global。"""
    out, tables = [], set()
    for n in tree.body:
        if not isinstance(n, (ast.Assign, ast.AnnAssign)) or n.value is None:
            continue
        targets = n.targets if isinstance(n, ast.Assign) else [n.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        v = n.value
        if isinstance(v, MUTABLE_LITERALS) or (isinstance(v, ast.Call) and ast.unparse(v.func) in MUTABLE_CALLS):
            empty = (isinstance(v, (ast.Dict,)) and not v.keys) or (isinstance(v, (ast.List, ast.Set)) and not v.elts) \
                or (isinstance(v, ast.Call) and not v.args and not v.keywords)
            tables.update(names)
            if empty:
                out.append(f"{n.lineno}: {'、'.join(names)} 是模組層級的空容器（會變的狀態；要嘛用函式回新的，要嘛存 pack.store.of(user)）")
            elif isinstance(v, ast.Call):
                out.append(f"{n.lineno}: {'、'.join(names)} 用 {ast.unparse(v.func)}(…) 建在模組層級（常數表請寫成字面值）")
            else:
                bad = [x for x in names if not _constant_name(x)]
                if bad:
                    out.append(f"{n.lineno}: {'、'.join(bad)} 是模組層級的 dict／list／set，但名字不是常數（全大寫）")
        elif isinstance(v, ast.Call):
            if ast.unparse(v.func) not in CONSTANT_CALLS + tuple(extra_calls):
                out.append(f"{n.lineno}: {'、'.join(names)} = {ast.unparse(v.func)}(…) 在模組層級（共用的物件；改成函式）")
        elif isinstance(v, ast.Constant) and v.value is None:
            extra = [x for x in names if x not in hooks]
            if extra:
                out.append(f"{n.lineno}: {'、'.join(extra)} = None 在模組層級（測試換掉用的掛勾改成函式，例如 def new_session()）")
    for n in ast.walk(tree):
        if isinstance(n, ast.Global):
            out.append(f"{n.lineno}: global {'、'.join(n.names)}（改模組層級的狀態）")
        target = None
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in MUTATORS:
            target = n.func.value
        elif isinstance(n, (ast.Assign, ast.AugAssign, ast.Delete)):
            for t in (n.targets if isinstance(n, (ast.Assign, ast.Delete)) else [n.target]):
                if isinstance(t, ast.Subscript):
                    target = t.value
        while isinstance(target, (ast.Subscript, ast.Attribute)):          # TABLE["x"].append(…) 也算改到 TABLE
            target = target.value
        if isinstance(target, ast.Name) and target.id in tables:
            out.append(f"{n.lineno}: 改了模組層級的常數表 {target.id}")
        if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id in tables \
                and ast.unparse(n.func) in ("dict", "list", "set", "copy.copy") \
                and f"{ast.unparse(n.func)}({n.args[0].id})" not in extra_calls:
            out.append(f"{n.lineno}: {ast.unparse(n.func)}({n.args[0].id}) 拿常數表淺拷貝（空資料用 def empty() 回新的）")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "copy" \
                and isinstance(n.func.value, ast.Name) and n.func.value.id in tables:
            out.append(f"{n.lineno}: {n.func.value.id}.copy() 拿常數表淺拷貝（空資料用 def empty() 回新的）")
    return out


def panel_problems(name: str, html: str) -> list:
    """M5：panel.html 不用 innerHTML、localStorage、sessionStorage、外部網址；fetch 只打 /api/packs/<自己的名字>/。"""
    out = [f"用了 {word}" for word in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "localStorage",
                                      "sessionStorage", "indexedDB", "http://", "https://", "XMLHttpRequest",
                                      "WebSocket", "EventSource", "sendBeacon") if word in html]
    calls = re.findall(r"\bfetch\s*\(\s*([^,)]*)", html)
    for arg in calls:
        found = re.fullmatch(r"""\s*(["'`])(/api/packs/([^/"'`]+)/[^"'`$]*)\1\s*""", arg)
        if not found:
            out.append(f"fetch({arg.strip()}) 的網址不是寫死的 /api/packs/{name}/…")
        elif found.group(3) != name:
            out.append(f"fetch 打到別的工具：{found.group(2)}")
    return out


@pytest.mark.parametrize("name", PACK_NAMES)
def test_m3_chat_id_is_never_a_per_person_key(name):
    """每個 .py 都看（money 是多檔工具：pack.py＋bridge/＋engine/）。"""
    found = {rel: chat_id_problems(ast.parse(text)) for rel, text in python_sources(name).items()}
    assert {rel: p for rel, p in found.items() if p} == {}


@pytest.mark.parametrize("name", PACK_NAMES)
def test_m4_no_mutable_module_state(name):
    """每個 .py 都看；個別工具多准的呼叫列在 M4_EXTRA_CALLS（各有理由）。"""
    extra = M4_EXTRA_CALLS.get(name, {})
    hooks = M4_HOOKS.get(name, {})
    found = {rel: module_state_problems(ast.parse(text), hooks=hooks, extra_calls=extra)
             for rel, text in python_sources(name).items()}
    assert {rel: p for rel, p in found.items() if p} == {}


def test_m4_extra_calls_are_still_used():
    """M4_EXTRA_CALLS 每一條都還真的用得到（工具改掉了就把例外拿掉，例外不會越積越多）。"""
    for name, calls in M4_EXTRA_CALLS.items():
        trees = [ast.parse(text) for text in python_sources(name).values()]
        used = {ast.unparse(n.value.func) for tree in trees for n in tree.body
                if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(n.value, ast.Call)}
        used |= {f"{ast.unparse(n.func)}({n.args[0].id})" for tree in trees for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Name)}
        assert set(calls) <= used, f"{name}: 用不到的例外 {sorted(set(calls) - used)}"
    for name, hooks in M4_HOOKS.items():
        top = {t.id for text in python_sources(name).values() for n in ast.parse(text).body
               if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and n.value.value is None
               for t in n.targets if isinstance(t, ast.Name)}
        assert set(hooks) <= top, f"{name}: 用不到的掛勾例外 {sorted(set(hooks) - top)}"


@pytest.mark.parametrize("name", [n for n in PACK_NAMES if os.path.isfile(os.path.join(PACKS, n, "panel.html"))])
def test_m5_panel_only_talks_to_its_own_api(name):
    with open(os.path.join(folder(name), "panel.html"), "r", encoding="utf-8") as f:
        html = f.read()
    m = synox_manifest.read(folder(name), strict=True)
    assert panel_problems(name, html) == []
    assert "fetch(" in html and "textContent" in html
    routes = set(re.findall(r"""fetch\(\s*["'`]/api/packs/[^/]+/([^"'`?]+)""", html))
    assert routes and routes <= set(m.web.get("api", [])), routes      # 只打自己在 pack.json 宣告的 route


BAD_CHAT_ID = """
def install(app, pack):
    seen = {}
    def handler(api, chat_id, user, values):
        pack.store.of(chat_id).load()
        pack.path_of(chat_id, "x")
        pack.secret("token", chat_id)
        seen[chat_id] = 1
        seen.get(chat_id)
        x = {chat_id: 1}
"""
BAD_STATE = """
import requests
CACHE = {}
seen = []
EMPTY = {"items": []}
lower = {"a": 1}
SESSION = requests.Session()
transport = None
def f():
    global seen
    CACHE["x"] = 1
    EMPTY["items"].append(1)
    data = dict(EMPTY)
    other = lower.copy()
"""
BAD_PANEL = """
<script>
fetch("/api/packs/other/today");
fetch(url);
fetch("https://evil.example/x");
el.innerHTML = x; localStorage.setItem("a", 1);
</script>
"""


def test_static_checks_catch_the_bad_shapes():
    """上面三個檢查自己的測試：故意寫錯的樣子一定要被抓到（檢查壞掉不會悄悄變成全部通過）。"""
    assert len(chat_id_problems(ast.parse(BAD_CHAT_ID))) == 6
    found = "\n".join(module_state_problems(ast.parse(BAD_STATE)))
    for needle in ("CACHE 是模組層級的空容器", "seen 是模組層級的空容器", "lower 是模組層級的 dict", "SESSION = requests.Session",
                   "transport = None", "global seen", "改了模組層級的常數表 CACHE", "改了模組層級的常數表 EMPTY",
                   "dict(EMPTY)", "lower.copy()"):
        assert needle in found, needle
    assert module_state_problems(ast.parse("transport = None\n"), {"transport"}) == []
    found = panel_problems("todo", BAD_PANEL)
    assert any("別的工具" in p for p in found) and any("fetch(url)" in p for p in found)
    assert any("innerHTML" in p for p in found) and any("localStorage" in p for p in found)
    assert any("https://" in p for p in found)
