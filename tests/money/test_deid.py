"""去識別化的閘門：記帳工具（packs/money）與它的測試（tests/money）裡不能有個人的痕跡。

範圍：packs/money、tests/money 底下每個文字檔（.py .js .mjs .css .html .json .md .sql）。每一條預期都是 0 個結果。
要抓的「壞字串」一律在執行時拼出來（或只放雜湊），這個檔本身才不會被自己抓到，也不會把名字寫進 repo。

 1 本機路徑、人名與帳號、email、token：scripts/public_check.py 的 scan_text（這台的帳號、git 名字、public_check.local.txt）
 2 電話、Telegram 編號
 3 外部專案名（門口平台、聊天機器人、AI 登入工具）：只放 sha256 的前 24 碼，跟檔案裡每個英數字詞比
 4 券商、銀行、支付、卡片產品名；訂閱、AI、串流、飲料、超商、外送、量販的品牌名
 5 特定投資商品代號、個人用語（舊的用途代號、宗教用語）、捐贈的英文欄位名只當欄位名（畫面與文字一律寫「捐贈」）
 6 真實日期（202x 年）、來源的資料檔名、擁有者原話的標記、外部網址、來源專案的 ADR 編號、來源程式的名字（含舊的命令列寫法）、
   搬家時拿掉的套件（pydantic、fastapi、openpyxl）
已知誤報（明確白名單，見 ALLOW）：style.css 的 .env class（「每一格」的樣式）、dom.js 與 test_contract.py 的
SVG 命名空間網址（只是名字，不會連線）、引擎讀「帳本資料夾裡自己的 categories.json」與收據副檔名。
"""

import hashlib
import os
import re
import unicodedata

import pytest

import public_check as pc

from conftest import PACKS, ROOT

TEXT_EXT = (".py", ".js", ".mjs", ".css", ".html", ".json", ".md", ".sql", ".txt")
SCOPES = (os.path.join(PACKS, "money"), os.path.join(ROOT, "tests", "money"))


def _j(*parts):
    return "".join(parts)


def files():
    out = []
    for scope in SCOPES:
        for base, dirs, names in os.walk(scope):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache")]
            for n in names:
                full = os.path.join(base, n)
                out.append((os.path.relpath(full, ROOT).replace(os.sep, "/"), full))
    adr = os.path.join(ROOT, "docs", "adr")                       # 記帳 0.4.0 的架構決定也要乾淨
    out += [(f"docs/adr/{n}", os.path.join(adr, n)) for n in os.listdir(adr) if n.startswith("0004-")]
    return sorted(out)


def texts():
    for rel, full in files():
        if rel.endswith(TEXT_EXT):
            with open(full, "r", encoding="utf-8") as f:
                yield rel, f.read()


ME = os.path.relpath(os.path.abspath(__file__), ROOT).replace(os.sep, "/")

# ── 規則 ─────────────────────────────────────────────────────────────────────────

# 外部專案名：sha256(小寫).hexdigest()[:24]
PROJECT_HASHES = {"3acd8d4d9cc1f3c68efc8eca", "f46d7daebc91498bd0bf4ba7", "635badfd66c8bea0b2fc3ace"}

# 品牌（拼開寫）：中文直接比對；英文看整個詞（大小寫不分、全形先轉半形）
BRANDS_CJK = [_j(*w.split("|")) for w in (
    # 銀行
    "國|泰", "富|邦", "玉|山", "中國|信託", "中|信銀", "台|新", "永|豐", "兆|豐", "第一|銀行", "合作|金庫", "合|庫", "彰化|銀行",
    "彰|銀", "華南|銀行", "土地|銀行", "土|銀", "臺灣|銀行", "台灣|銀行", "臺|銀", "星|展", "渣|打", "花|旗", "匯|豐", "滙|豐",
    "聯邦|銀行", "遠東|商銀", "新光|銀行", "上海|商銀", "王道|銀行", "將來|銀行", "連線|銀行",
    # 券商
    "元|大", "凱|基", "群|益", "復|華", "統一|證券", "康|和", "日|盛", "富|果", "第一|證券", "嘉|信", "盈|透", "富|途",
    # 支付、卡
    "街|口", "全|支付", "悠遊|付", "悠遊|卡", "一卡|通", "台灣|pay", "拍|錢包",
    # 超商、飲料、餐飲、外送、量販、網購
    "統一|超商", "小|七", "全|家便利", "萊爾|富", "OK|超商", "星|巴克", "路易|莎", "50|嵐", "清心|福全", "迷客|夏",
    "麻|古", "麥當|勞", "肯德|基", "摩斯|漢堡", "漢堡|王", "熊貓|外送", "蝦|皮", "博客|來", "全|聯", "家樂|福",
    "好市|多", "屈臣|氏", "康是|美", "寶|雅", "優|食",
)]
BRANDS_WORD = [_j(*w.split("|")) for w in (
    "line| pay", "line|pay", "line| bank", "apple| pay", "google| pay", "pay|pal", "jko|pay", "ipa|ss", "cu|be",
    "first|trade", "sch|wab", "interactive| brokers", "ib|kr", "robin|hood", "ameri|trade", "moo|moo",
    "net|flix", "spo|tify", "you|tube", "dis|ney", "kk|box", "chat|gpt", "open|ai", "clau|de", "gem|ini", "copi|lot",
    "mid|journey", "ado|be", "icl|oud", "drop|box", "not|ion", "7-|eleven", "seven|-eleven", "family|mart", "star|bucks",
    "mc|donald", "mcdonald|'s", "k|fc", "sub|way", "uber| eats", "uber|eats", "food|panda", "sho|pee", "mo|mo", "pc|home",
    "cost|co", "ik|ea", "watson|s",
)]
PERSONAL = [_j(*w.split("|")) for w in ("king|dom", "hed|ge", "奉|獻", "十一|奉獻")]

PATTERNS = [
    ("電話", re.compile(r"(?<!\d)09\d{8}(?!\d)|\+886")),
    ("Telegram 編號", re.compile(r"telegram:\d+|tg_id\s*=\s*\d{6,}")),
    ("本機資料夾", re.compile(r"Documents[\\/]|Users/user\b")),
    ("門口密碼檔", re.compile(r"\.env\b")),
    ("外部專案的啟動檔與埠", re.compile(r"run\.bat|(?<!\d)8810(?!\d)")),
    ("特定商品代號", re.compile(r"(?<![\w.\-:/])00\d{2}(?![\w\-])|\b\d{4}\.TW\b")),
    ("真實日期", re.compile(r"\b202[0-8]-\d\d-\d\d\b")),       # 測試的假日期是 2029～2030（引擎測試 2037）
    ("來源的資料檔", re.compile(r"categories\.json|trading_data|\.xlsx\b|inbox-images|\.jpe?g\b")),
    ("擁有者原話", re.compile(_j("擁有", "者：") + "|" + _j("原", "話"))),
    ("外部網址", re.compile(r"https?://")),
    ("來源專案的 ADR 編號", re.compile(r"ADR[ -]?(\d{4})")),
    ("來源程式的名字與舊的命令列寫法", re.compile(_j("money", "book"), re.I)),
    ("拿掉的套件", re.compile(r"\b(" + _j("pyd", "antic") + "|" + _j("fast", "api") + "|" + _j("open", "pyxl") + r")\b")),
    ("捐贈的英文欄位名出現在給人看的字裡", re.compile(r"[\"'`][^\"'`\n]*[一-鿿][^\"'`\n]*\btithe\b|\btithe\b[^\"'`\n]*[一-鿿][^\"'`\n]*[\"'`]",
                                              re.I)),
]

# 已知誤報：(檔名結尾, 規則名, 這一行一定要包含的字)
ALLOW = (
    ("packs/money/app/style.css", "門口密碼檔", ".env"),                       # 「每一格」的 CSS class
    ("packs/money/app/js/dom.js", "外部網址", "www.w3.org/2000/svg"),          # SVG 命名空間（不會連線）
    ("tests/money/test_contract.py", "外部網址", "www.w3.org/2000/svg"),
    ("packs/money/engine/seed.py", "來源的資料檔", "categories.json"),          # 帳本資料夾裡使用者自己的起始檔（不在 repo）
    ("packs/money/engine/db.py", "來源的資料檔", "categories.json"),
    ("packs/money/engine/services/starter.py", "來源的資料檔", "categories.json"),
    ("tests/money/engine/", "來源的資料檔", "categories.json"),                 # 引擎測試的假起始檔（seed_test.json）
    ("tests/money/engine/test_ingest.py", "來源的資料檔", ".jpg"),              # 假的收據檔名
    ("packs/money/CONTRACT.md", "門口密碼檔", "style.css"),                     # 契約裡說明這個誤報的那一行
    ("packs/money/engine/services/attachments.py", "來源的資料檔", ".jpg"),    # 收據可以是 .jpg
)


def own_adrs():
    folder = os.path.join(ROOT, "docs", "adr")
    return {n[:4] for n in os.listdir(folder) if re.match(r"\d{4}-", n)}


def allowed(rel, rule, line):
    return any((rel == f or (f.endswith("/") and rel.startswith(f))) and rule == r and must in line
               for f, r, must in ALLOW)


def findings_in(rel, text, adrs):
    out = []
    norm = unicodedata.normalize("NFKC", text)
    for no, line in enumerate(norm.splitlines(), 1):
        low = line.casefold()
        for name, pat in PATTERNS:
            for m in pat.finditer(line):
                if name == "來源專案的 ADR 編號" and m.group(1) in adrs:
                    continue
                if not allowed(rel, name, line):
                    out.append(f"{rel}:{no} {name}")
                break
        for w in BRANDS_CJK:
            if w.casefold() in low:
                out.append(f"{rel}:{no} 品牌名")
        for w in BRANDS_WORD:
            if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", low):
                out.append(f"{rel}:{no} 品牌名")
        for w in PERSONAL:
            if w in low:
                out.append(f"{rel}:{no} 個人用語")
        for token in re.findall(r"[a-z0-9]+", low):
            if hashlib.sha256(token.encode()).hexdigest()[:24] in PROJECT_HASHES:
                out.append(f"{rel}:{no} 外部專案名")
    return out


def test_scope_is_not_empty():
    rels = [r for r, _ in files()]
    for must in ("packs/money/pack.py", "packs/money/app/js/views/piggy.js", "packs/money/app/js/views/reconcile.js",
                 "packs/money/bridge/piggy.py", "packs/money/engine/services/books.py", "tests/money/test_piggy.py"):
        assert must in rels, must
    assert len(rels) > 150


def test_no_personal_traces():
    adrs = own_adrs()
    found = []
    for rel, text in texts():
        if rel == ME:
            continue
        found += findings_in(rel, text, adrs)
    assert not found, "\n".join(found[:80])


def test_public_check_rules_on_money():
    """scripts/public_check.py 的規則（本機路徑、這台的帳號與 git 名字、email、token、真實 id）也照樣過一次。"""
    words = pc.private_words(ROOT)
    found = []
    for rel, text in texts():
        found += [f"{rel}:{no} {kind}" for no, kind in pc.scan_text(text, words)]
    assert not found, "\n".join(found[:50])


def test_no_data_files_or_images():
    """repo 裡不該有資料檔或圖片（帳本、試算表、截圖）；畫面的圖示另外放在 app/ 才算。"""
    bad = [r for r, _ in files() if r.endswith((".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".csv", ".jpg", ".jpeg",
                                                 ".png", ".enc", ".key", ".pem"))
           and not r.startswith("packs/money/app/")]
    assert not bad, bad
    assert not [r for r, _ in files() if pc.sensitive_name(r)]


@pytest.mark.parametrize("line, rule", [
    ("帳戶：" + _j("國", "泰") + "世華", "品牌名"),
    ("desc = '" + _j("Net", "flix") + " 月費'", "品牌名"),
    ("bucket = '" + _j("king", "dom") + "'", "個人用語"),
    ("date = '" + _j("2025", "-03-04") + "'", "真實日期"),
    ("symbol = '" + _j("00", "50") + "'", "特定商品代號"),
    ("url = '" + _j("https", "://example.org") + "'", "外部網址"),
    ("見 ADR " + _j("00", "32"), "來源專案的 ADR 編號"),
    ("打 " + _j("money", "book") + " batch list", "舊的命令列寫法"),
    ("DB_NAME = \"" + _j("Money", "Book") + ".db\"", "來源程式的名字"),          # 檔名也算（0.4.0 起叫 ledger.db）
    ("h('b', null, '" + _j("tit", "he") + " 做完了')", "捐贈的英文欄位名出現在給人看的字裡"),
])
def test_rules_catch_their_kind(line, rule):
    assert any(rule in f for f in findings_in("x.py", line, own_adrs())), findings_in("x.py", line, own_adrs())


@pytest.mark.parametrize("line", [
    "tithe_rate = books.tithe(w, book_id, done=True)",                # 欄位名、函式名可以
    "h('span', null, '捐贈 10%')",
    "DB_NAME = \"ledger.db\"",
    "date = '2030-01-15'",
    "見 docs/adr/0002",
    "SamplePay-範例網購 120",
])
def test_fine_lines_pass(line):
    assert findings_in("x.py", line, own_adrs()) == []
