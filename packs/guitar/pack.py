"""🎸 吉他和弦：查和弦、貼一份譜直接移調、Capo 建議、分散和弦 TAB、一個調裡有哪些和弦、一格一小節的小節譜。

指令（群組 chord）：
  /chord <和弦…>          查按法（Am7、D/F#、Bb，一次可以好幾個）
  /chord_tp <移法> <譜>    整份譜移調（+2、-3、G→A、到 A）；譜可以是「和弦在上、歌詞在下」或 [C] 夾在歌詞裡
  /chord_capo [N] [譜或調] 夾 Capo 用哪一組手型最好彈；給 N＝直接把譜改成夾第 N 格的手型
  /chord_arp <和弦…> [型態] 分散和弦（指彈）的 TAB 譜
  /chord_key <調>          這個調裡的和弦與常見進行
  /chord_chart [移法] <進行> 整理成一格一小節的小節譜（| C | G D/F# |、C G Am F、AI 看照片填的 JSON）

樂理與按法的資料從「吉他和弦全書」（羊ㄣ出品）搬來；組成音都由程式逐弦算過（tests/test_guitar.py）。
只記兩樣東西：最後一次貼的譜、最後一份小節譜（pack.store.of(user)，保管箱），讓「/chord_tp +1」不用再貼一次、
面板的「小節譜」分頁看得到。不連網、不碰檔案。
"""

import re
import time

from isdtelekit.core.registry import WRITE, Field

from synox.packs import kit

# ── 音名 ──────────────────────────────────────────────────────────────────────

SHARP = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
FLAT = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
NEUTRAL = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")       # C 調／A 小調：借來的和弦多半寫降記號
KEY_MAJOR = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
KEY_MINOR = ("Cm", "C#m", "Dm", "D#m", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm")
FLAT_KEYS = frozenset((1, 3, 5, 8, 10))            # Db Eb F Ab Bb（大調主音；小調看關係大調）
SHARP_KEYS = frozenset((2, 4, 6, 7, 9, 11))        # D E F# G A B
LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
STRING_PC = (4, 9, 2, 7, 11, 4)                    # 第6弦 E … 第1弦 E
STRING_MIDI = (40, 45, 50, 55, 59, 64)
STRING_NAME = ("E", "A", "D", "G", "B", "e")
FINGER = ("空弦", "食指", "中指", "無名指", "小指")


def note_pc(text):
    """'C#'、'Bb'、'E♭' → 0..11；不是音名回 None。"""
    if not text or text[0] not in LETTER_PC:
        return None
    pc = LETTER_PC[text[0]]
    for ch in text[1:]:
        if ch in "#♯":
            pc += 1
        elif ch in "b♭":
            pc -= 1
        else:
            return None
    return pc % 12


def spell(pc, key=None):
    """依調號寫音名：降調用 Db Eb…、升調用 C# D#…；沒給調用 C 調的寫法。"""
    pc %= 12
    if key is None:
        return NEUTRAL[pc]
    tonic, minor = key
    major = (tonic + 3) % 12 if minor else tonic
    if major in FLAT_KEYS:
        return FLAT[pc]
    if major in SHARP_KEYS:
        if pc == 10 and major not in (6, 11):      # A# 只有 B、F# 調才這樣寫，其他寫 Bb
            return "Bb"
        return SHARP[pc]
    return NEUTRAL[pc]


def key_name(key):
    tonic, minor = key
    return (KEY_MINOR if minor else KEY_MAJOR)[tonic % 12]


def parse_key(text):
    """'G'、'Bb'、'F#m'、'Am'、'C 大調'、'A小調' → (主音, 是不是小調)；不是調回 None。"""
    t = (text or "").strip().replace("大調", "").replace("調", "").strip()
    minor = False
    for tail in ("小", "minor", "min", "m"):
        if t.endswith(tail) and len(t) > len(tail):
            t, minor = t[: -len(tail)].strip(), True
            break
    if t.endswith("major"):
        t = t[:-5].strip()
    pc = note_pc(t)
    return None if pc is None else (pc, minor)


# ── 和弦種類 ──────────────────────────────────────────────────────────────────
# 種類 → (標準寫法的尾巴, 中文, 組成音（半音）)
QUAL = {
    "major": ("", "大三和弦", (0, 4, 7)),
    "minor": ("m", "小三和弦", (0, 3, 7)),
    "7": ("7", "屬七和弦", (0, 4, 7, 10)),
    "m7": ("m7", "小七和弦", (0, 3, 7, 10)),
    "maj7": ("maj7", "大七和弦", (0, 4, 7, 11)),
    "6": ("6", "大六和弦", (0, 4, 7, 9)),
    "m6": ("m6", "小六和弦", (0, 3, 7, 9)),
    "9": ("9", "屬九和弦", (0, 4, 7, 10, 2)),
    "m9": ("m9", "小九和弦", (0, 3, 7, 10, 2)),
    "maj9": ("maj9", "大九和弦", (0, 4, 7, 11, 2)),
    "add9": ("add9", "加九和弦", (0, 4, 7, 2)),
    "madd9": ("madd9", "小加九和弦", (0, 3, 7, 2)),
    "sus2": ("sus2", "掛留二和弦", (0, 2, 7)),
    "sus4": ("sus4", "掛留四和弦", (0, 5, 7)),
    "7sus4": ("7sus4", "屬七掛四和弦", (0, 5, 7, 10)),
    "dim": ("dim", "減三和弦", (0, 3, 6)),
    "dim7": ("dim7", "減七和弦", (0, 3, 6, 9)),
    "m7b5": ("m7b5", "半減七和弦", (0, 3, 6, 10)),
    "aug": ("aug", "增三和弦", (0, 4, 8)),
    "mmaj7": ("mM7", "小大七和弦", (0, 3, 7, 11)),
    "5": ("5", "強力和弦", (0, 7)),
}
QUAL_ORDER = ("major", "minor", "7", "m7", "maj7", "sus2", "sus4", "add9", "6", "m6", "9", "m9", "maj9", "7sus4",
              "madd9", "dim", "dim7", "m7b5", "aug", "mmaj7", "5")
TONE_NAME = {0: "根音", 2: "九音", 3: "三音", 4: "三音", 5: "四音", 6: "降五音", 7: "五音", 8: "升五音", 9: "六音",
             10: "七音", 11: "大七音"}
MINORISH = frozenset(("minor", "m7", "m6", "m9", "madd9", "mmaj7"))
DIMISH = frozenset(("dim", "dim7", "m7b5"))

# 譜上的寫法 → 種類（先把括號拿掉、符號換成字）。沒列到的延伸和弦用 _approx() 找最接近的
SUFFIX_EXACT = {
    "": "major", "maj": "major", "M": "major", "m": "minor", "min": "minor", "mi": "minor", "-": "minor",
    "7": "7", "dom7": "7", "m7": "m7", "min7": "m7", "mi7": "m7", "-7": "m7",
    "maj7": "maj7", "M7": "maj7", "Maj7": "maj7", "ma7": "maj7", "Δ": "maj7", "Δ7": "maj7", "j7": "maj7",
    "6": "6", "m6": "m6", "min6": "m6", "-6": "m6",
    "9": "9", "m9": "m9", "min9": "m9", "-9": "m9", "maj9": "maj9", "M9": "maj9", "Δ9": "maj9",
    "add9": "add9", "add2": "add9", "2": "sus2", "madd9": "madd9", "madd2": "madd9", "m2": "madd9", "-add9": "madd9",
    "sus2": "sus2", "sus4": "sus4", "sus": "sus4", "4": "sus4", "7sus4": "7sus4", "7sus": "7sus4", "9sus4": "7sus4",
    "dim": "dim", "°": "dim", "dim7": "dim7", "°7": "dim7",
    "m7b5": "m7b5", "m7-5": "m7b5", "ø": "m7b5", "ø7": "m7b5", "min7b5": "m7b5", "-7b5": "m7b5",
    "aug": "aug", "+": "aug", "+5": "aug", "#5": "aug", "aug5": "aug",
    "mmaj7": "mmaj7", "mM7": "mmaj7", "m(maj7)": "mmaj7", "minmaj7": "mmaj7", "-maj7": "mmaj7", "mΔ7": "mmaj7",
    "5": "5",
}
SUFFIX_OK = re.compile(r"^(?:maj|Maj|ma|min|mi|dom|sus|add|alt|aug|dim|omit|no|m|M|j|Δ|°|ø|-|\+|[0-9]|b|#|♭|♯|\(|\)|,)*$")
CHORD_RE = re.compile(r"^([A-G])([#♯b♭]?)(.*?)(?:/([A-G][#♯b♭]?))?$")


def _approx(sfx):
    """延伸和弦（11、13、7b9、69…）→ 吉他上最接近、最常拿來代替的種類。"""
    s = sfx.replace("(", "").replace(")", "").replace(",", "")
    if s.startswith(("maj", "M", "Δ", "ma")):
        return "maj7"
    if s.startswith(("m", "min", "-")):
        return "m7" if re.search(r"\d", s) else "minor"
    if s.startswith(("6", "69")):
        return "6"
    if s.startswith(("dim", "°")):
        return "dim7"
    if s.startswith(("aug", "+")):
        return "aug"
    if s.startswith(("add", "sus")):
        return "major"
    if re.match(r"^(7|9|11|13)", s):
        return "7"
    return None


def parse_chord(text):
    """'Am7'、'D/F#'、'Bbmaj7'、'C(add9)' → dict；不是和弦回 None。
    回：root（0..11）、root_txt、suffix（原樣保留）、quality、approx（是不是用近似的種類）、bass（0..11 或 None）。"""
    m = CHORD_RE.match((text or "").strip())
    if not m:
        return None
    letter, acc, sfx, bass = m.groups()
    if not SUFFIX_OK.match(sfx):
        return None
    norm = sfx.replace("♭", "b").replace("♯", "#")
    flat = norm.replace("(", "").replace(")", "")
    quality = SUFFIX_EXACT.get(norm) or SUFFIX_EXACT.get(flat)
    approx = False
    if quality is None:
        quality, approx = _approx(norm), True
        if quality is None:
            return None
    return {"root": note_pc(letter + acc), "root_txt": letter + acc, "suffix": sfx, "quality": quality,
            "approx": approx, "bass": note_pc(bass) if bass else None, "bass_txt": bass or ""}


def chord_name(ch, shift=0, key=None):
    """把和弦平移 shift 個半音、照 key 的調號寫出來；尾巴照原譜的寫法。"""
    root = spell(ch["root"] + shift, key)
    name = root + ch["suffix"]
    if ch["bass"] is not None:
        name += "/" + spell(ch["bass"] + shift, key)
    return name


def own_name(ch):
    """使用者自己寫的樣子（查和弦時不換升降記號：打 Ab 就顯示 Ab）。"""
    if "root_txt" not in ch:
        return chord_name(ch)
    return ch["root_txt"] + ch["suffix"] + ("/" + ch["bass_txt"] if ch.get("bass_txt") else "")


def std_name(root, quality, bass=None, key=None):
    name = spell(root, key) + QUAL[quality][0]
    return name + ("/" + spell(bass, key) if bass is not None and bass != root else "")


# ── 按法資料（從「吉他和弦全書」搬來；frets＝第6弦→第1弦，None＝不彈） ──────────────

OPEN = {
    "C_major": ((None, 3, 2, 0, 1, 0), (None, 3, 2, 0, 1, 0)),
    "D_major": ((None, None, 0, 2, 3, 2), (None, None, 0, 1, 3, 2)),
    "E_major": ((0, 2, 2, 1, 0, 0), (0, 2, 3, 1, 0, 0)),
    "G_major": ((3, 2, 0, 0, 0, 3), (2, 1, 0, 0, 0, 3)),
    "A_major": ((None, 0, 2, 2, 2, 0), (None, 0, 1, 2, 3, 0)),
    "E_minor": ((0, 2, 2, 0, 0, 0), (0, 2, 3, 0, 0, 0)),
    "A_minor": ((None, 0, 2, 2, 1, 0), (None, 0, 2, 3, 1, 0)),
    "D_minor": ((None, None, 0, 2, 3, 1), (None, None, 0, 2, 3, 1)),
    "A_7": ((None, 0, 2, 0, 2, 0), (None, 0, 1, 0, 2, 0)),
    "B_7": ((None, 2, 1, 2, 0, 2), (None, 2, 1, 3, 0, 4)),
    "C_7": ((None, 3, 2, 3, 1, 0), (None, 3, 2, 4, 1, 0)),
    "D_7": ((None, None, 0, 2, 1, 2), (None, None, 0, 2, 1, 3)),
    "E_7": ((0, 2, 0, 1, 0, 0), (0, 2, 0, 1, 0, 0)),
    "G_7": ((3, 2, 0, 0, 0, 1), (3, 2, 0, 0, 0, 1)),
    "A_m7": ((None, 0, 2, 0, 1, 0), (None, 0, 2, 0, 1, 0)),
    "E_m7": ((0, 2, 0, 0, 0, 0), (0, 2, 0, 0, 0, 0)),
    "D_m7": ((None, None, 0, 2, 1, 1), (None, None, 0, 2, 1, 1)),
    "B_m7": ((None, 2, 0, 2, 0, 2), (None, 1, 0, 2, 0, 3)),
    "C_maj7": ((None, 3, 2, 0, 0, 0), (None, 3, 2, 0, 0, 0)),
    "F_maj7": ((None, None, 3, 2, 1, 0), (None, None, 3, 2, 1, 0)),
    "D_maj7": ((None, None, 0, 2, 2, 2), (None, None, 0, 1, 1, 1)),
    "E_maj7": ((0, 2, 1, 1, 0, 0), (0, 2, 1, 1, 0, 0)),
    "G_maj7": ((3, 2, 0, 0, 0, 2), (3, 1, 0, 0, 0, 2)),
    "A_maj7": ((None, 0, 2, 1, 2, 0), (None, 0, 2, 1, 3, 0)),
    "C_sus2": ((None, 3, 0, 0, 3, 3), (None, 2, 0, 0, 3, 4)),
    "C_sus4": ((None, 3, 3, 0, 1, 1), (None, 2, 3, 0, 1, 1)),
    "C_add9": ((None, 3, 2, 0, 3, 0), (None, 3, 2, 0, 4, 0)),
    "D_sus2": ((None, None, 0, 2, 3, 0), (None, None, 0, 1, 2, 0)),
    "D_sus4": ((None, None, 0, 2, 3, 3), (None, None, 0, 1, 3, 3)),
    "D_add9": ((None, 5, 4, 2, 3, 0), (None, 4, 3, 1, 2, 0)),
    "E_sus4": ((0, 2, 2, 2, 0, 0), (0, 3, 3, 3, 0, 0)),
    "G_sus4": ((3, 3, 0, 0, 1, 3), (3, 3, 0, 0, 1, 4)),
    "G_add9": ((3, 0, 0, 2, 0, 3), (2, 0, 0, 1, 0, 3)),
    "A_sus2": ((None, 0, 2, 2, 0, 0), (None, 0, 1, 1, 0, 0)),
    "A_sus4": ((None, 0, 2, 2, 3, 0), (None, 0, 1, 1, 3, 0)),
    "A_add9": ((None, 0, 2, 4, 2, 0), (None, 0, 1, 4, 2, 0)),
    # 以下是這個工具多收的常見開放按法
    "A_6": ((None, 0, 2, 2, 2, 2), (None, 0, 1, 1, 1, 1)),
    "E_9": ((0, 2, 0, 1, 0, 2), (0, 2, 0, 1, 0, 3)),
    "C_9": ((None, 3, 2, 3, 3, 3), (None, 2, 1, 3, 3, 3)),
    "E_m9": ((0, 2, 0, 0, 0, 2), (0, 2, 0, 0, 0, 3)),
    "A_7sus4": ((None, 0, 2, 0, 3, 0), (None, 0, 2, 0, 3, 0)),
    "D_7sus4": ((None, None, 0, 2, 1, 3), (None, None, 0, 2, 1, 4)),
    "E_7sus4": ((0, 2, 0, 2, 0, 0), (0, 2, 0, 3, 0, 0)),
}
# 同一個和弦常見的幾種按法（第 0 個＝預設）
ALT = {
    "G_major": (
        ("2-1-3 指法（三指 G）", (3, 2, 0, 0, 0, 3), (2, 1, 0, 0, 0, 3), "手的位置跟 D 一樣，G↔D、G↔Em 換得最順。"),
        ("3-2-4 指法（三指 G）", (3, 2, 0, 0, 0, 3), (3, 2, 0, 0, 0, 4), "空出食指，G↔C 換得最快。"),
        ("四指 G（320033）", (3, 2, 0, 0, 3, 3), (2, 1, 0, 0, 3, 4), "聲音飽滿，常搭 Cadd9、Em7、D/F#。"),
    ),
    "C_major": (
        ("標準 C", (None, 3, 2, 0, 1, 0), (None, 3, 2, 0, 1, 0), "最常見的 C。"),
        ("C（小指加按第1弦第3格）", (None, 3, 2, 0, 1, 3), (None, 3, 2, 0, 1, 4), "第1弦多一個 G，跟四指 G 換得很順。"),
    ),
    "E_7": (
        ("標準 E7", (0, 2, 0, 1, 0, 0), (0, 2, 0, 1, 0, 0), "兩根手指就好。"),
        ("飽滿版 E7（022130）", (0, 2, 2, 1, 3, 0), (0, 2, 3, 1, 4, 0), "多按兩個音，聲音更厚。"),
    ),
    "F_major": (
        ("大橫按（完整）", (1, 3, 3, 2, 1, 1), (1, 3, 4, 2, 1, 1), "六條弦都響，最飽滿，但最難按。"),
        ("四弦 F（簡易）", (None, None, 3, 2, 1, 1), (None, None, 3, 2, 1, 1), "只彈第1～4弦，食指壓兩條弦，初學最常用。"),
    ),
    "B_minor": (
        ("大橫按（完整）", (None, 2, 4, 4, 3, 2), (None, 1, 3, 4, 2, 1), "五條弦都響，最飽滿。"),
        ("四弦 Bm（簡易）", (None, None, 4, 4, 3, 2), (None, None, 4, 3, 2, 1), "只彈第1～4弦，最低音變成 F#。"),
    ),
}
# 不用大橫按的簡易按法（每個都由 analyze() 檢查組成音）
EASY = {
    "F_major": ((None, None, 3, 2, 1, 1), (None, None, 3, 2, 1, 1), "食指同時壓第1、2弦第1格，比大橫按輕鬆很多。"),
    "B_minor": ((None, None, 4, 4, 3, 2), (None, None, 4, 3, 2, 1), "四根手指各按一格，像爬樓梯；第5、6弦不要彈。"),
    "B_major": ((None, None, 4, 4, 4, 2), (None, None, 2, 3, 4, 1), "中指、無名指、小指壓第4格，食指壓第1弦第2格。"),
    "A#_major": ((None, None, 3, 3, 3, 1), (None, None, 2, 3, 4, 1), "跟簡易 B 同一個手型，往琴頭移一格。"),
    "F#_minor": ((None, None, 4, 2, 2, 2), (None, None, 3, 1, 1, 1), "食指橫壓第1～3弦第2格，無名指壓第4弦第4格。"),
    "F_minor": ((None, None, 3, 1, 1, 1), (None, None, 3, 1, 1, 1), "跟簡易 F#m 同一個手型，往琴頭移一格。"),
    "G_minor": ((None, None, 5, 3, 3, 3), (None, None, 3, 1, 1, 1), "跟簡易 F#m 同一個手型，往琴身移一格。"),
    "C_minor": ((None, None, 5, 5, 4, 3), (None, None, 4, 3, 2, 1), "跟簡易 Bm 同一個手型，往琴身移一格。"),
}
# 可以移動的手型：根音在第6弦（E 型）／第5弦（A 型），數字是「離根音那一格」幾格（可以是負的）
E_SHAPE = {
    "major": ((0, 2, 2, 1, 0, 0), (1, 3, 4, 2, 1, 1)),
    "minor": ((0, 2, 2, 0, 0, 0), (1, 3, 4, 1, 1, 1)),
    "7": ((0, 2, 0, 1, 0, 0), (1, 3, 1, 2, 1, 1)),
    "m7": ((0, 2, 0, 0, 0, 0), (1, 3, 1, 1, 1, 1)),
    "maj7": ((0, 2, 1, 1, 0, 0), (1, 4, 2, 3, 1, 1)),
    "sus4": ((0, 2, 2, 2, 0, 0), (1, 2, 3, 4, 1, 1)),
    "7sus4": ((0, 2, 0, 2, 0, 0), (1, 3, 1, 4, 1, 1)),
    "9": ((0, 2, 0, 1, 0, 2), (1, 3, 1, 2, 1, 4)),
    "m9": ((0, 2, 0, 0, 0, 2), (1, 3, 1, 1, 1, 4)),
    "m6": ((0, 2, 2, 0, 2, 0), (1, 2, 3, 1, 4, 1)),
    "5": ((0, 2, 2, None, None, None), (1, 3, 4, None, None, None)),
    "dim7": ((0, None, -1, 0, -1, None), (2, None, 1, 3, 1, None)),
    "m7b5": ((0, None, 0, 0, -1, None), (2, None, 3, 4, 1, None)),
}
A_SHAPE = {
    "major": ((None, 0, 2, 2, 2, 0), (None, 1, 3, 3, 3, 1)),
    "minor": ((None, 0, 2, 2, 1, 0), (None, 1, 3, 4, 2, 1)),
    "7": ((None, 0, 2, 0, 2, 0), (None, 1, 3, 1, 4, 1)),
    "m7": ((None, 0, 2, 0, 1, 0), (None, 1, 3, 1, 2, 1)),
    "maj7": ((None, 0, 2, 1, 2, 0), (None, 1, 3, 2, 4, 1)),
    "6": ((None, 0, 2, 2, 2, 2), (None, 1, 3, 3, 3, 3)),
    "m6": ((None, 0, 2, 2, 1, 2), (None, 1, 3, 3, 2, 4)),
    "sus2": ((None, 0, 2, 2, 0, 0), (None, 1, 3, 4, 1, 1)),
    "sus4": ((None, 0, 2, 2, 3, 0), (None, 1, 2, 3, 4, 1)),
    "7sus4": ((None, 0, 2, 0, 3, 0), (None, 1, 3, 1, 4, 1)),
    "add9": ((None, 0, 2, 4, 2, 0), (None, 1, 2, 4, 3, 1)),
    "9": ((None, 0, -1, 0, 0, 0), (None, 2, 1, 3, 3, 3)),
    "m9": ((None, 0, -2, 0, 0, 0), (None, 2, 1, 3, 3, 3)),
    "maj9": ((None, 0, -1, 1, 0, None), (None, 2, 1, 4, 3, None)),
    "madd9": ((None, 0, 2, 4, 1, 0), (None, 1, 3, 4, 2, 1)),
    "mmaj7": ((None, 0, 2, 1, 1, 0), (None, 1, 4, 2, 3, 1)),
    "dim": ((None, 0, 1, 2, 1, None), (None, 1, 2, 4, 3, None)),
    "dim7": ((None, 0, 1, 2, 1, 2), (None, 1, 2, 4, 3, 4)),
    "m7b5": ((None, 0, 1, 0, 1, None), (None, 1, 2, 3, 4, None)),
    "aug": ((None, 0, 3, 2, 2, 1), (None, 1, 4, 3, 3, 2)),
    "5": ((None, 0, 2, 2, None, None), (None, 1, 3, 4, None, None)),
}
SLASH = (
    ("C/E", (0, 3, 2, 0, 1, 0), (0, 3, 2, 0, 1, 0), "第6弦空弦 E 一起刷，很順的過門和弦。"),
    ("C/G", (3, 3, 2, 0, 1, 0), (3, 4, 2, 0, 1, 0), "C 和弦、低音換成 G。"),
    ("G/B", (None, 2, 0, 0, 0, 3), (None, 1, 0, 0, 0, 3), "常接在 C 或 Em 前後，讓低音平順移動。"),
    ("G/D", (None, None, 0, 0, 0, 3), (None, None, 0, 0, 0, 3), "第6、5弦都不彈，最低音是 D。"),
    ("D/F#", (2, None, 0, 2, 3, 2), (1, None, 0, 2, 4, 3), "卡農進行裡 D 接 G 前常見的過渡和弦。"),
    ("F/A", (None, 0, 3, 2, 1, 1), (None, 0, 3, 2, 1, 1), "不用大橫按的 F，低音用 A。"),
    ("Am/G", (3, None, 2, 2, 1, 0), (4, None, 2, 3, 1, 0), "Am–Am/G–F 這種下行低音常用。"),
    ("Em/B", (None, 2, 2, 0, 0, 0), (None, 1, 1, 0, 0, 0), "Em 拿掉第6弦，B 當最低音。"),
    ("Em/G", (3, None, 2, 0, 0, 0), (3, None, 2, 0, 0, 0), "C → G/B → Am → Em/G → F 這種下行低音。"),
    ("Dm7/G", (3, None, 0, 2, 1, 1), (3, None, 0, 2, 1, 1), "聽起來像 G7sus4，常放在回到 C 之前。"),
    ("F/G", (3, None, 3, 2, 1, 1), (3, None, 4, 2, 1, 1), "比 G 柔和，常用在副歌前。"),
    ("Fmaj7/G", (3, None, 3, 2, 1, 0), (3, None, 4, 2, 1, 0), "第1弦空弦，聲音更透明。"),
    ("Am/F#", (2, None, 2, 2, 1, 0), (2, None, 3, 4, 1, 0), "Am → Am/G → Am/F# → F 的下行低音。"),
    ("E/G#", (4, None, 2, 1, 0, 0), (4, None, 2, 1, 0, 0), "常放在 Am 前面：G# 往上一格就到 A。"),
    ("A/C#", (None, 4, 2, 2, 2, None), (None, 4, 1, 1, 1, None), "常放在 D 前面；第1弦不要彈。"),
    ("D/C", (None, 3, 0, 2, 3, 2), (None, 4, 0, 1, 3, 2), "D → D/C → G/B 的下行低音。"),
    ("G/F#", (2, None, 0, 0, 0, 3), (2, None, 0, 0, 0, 3), "G → G/F# → Em 的下行低音。"),
    ("G/F", (1, None, 0, 0, 0, 3), (1, None, 0, 0, 0, 3), "常放在 C/E 前面（G → G/F → C/E）。"),
    ("C/B", (None, 2, 2, 0, 1, 0), (None, 2, 3, 0, 1, 0), "C → C/B → Am 的下行低音。"),
    ("Dm/C", (None, 3, 0, 2, 3, 1), (None, 4, 0, 2, 3, 1), "Dm → Dm/C → G/B 的下行低音。"),
    ("F/C", (None, 3, 3, 2, 1, 1), (None, 3, 4, 2, 1, 1), "接在 C 後面，低音不用動。"),
    ("D/A", (None, 0, 0, 2, 3, 2), (None, 0, 0, 1, 3, 2), "連第5弦空弦一起彈，聲音比較厚。"),
    ("Em/D", (None, None, 0, 0, 0, 0), (None, None, 0, 0, 0, 0), "只彈第1～4弦空弦，Em → Em/D → C。"),
    ("A/E", (0, 0, 2, 2, 2, 0), (0, 0, 1, 2, 3, 0), "連第6弦空弦一起刷，更飽滿。"),
)
SIMPLIFIABLE = frozenset(("major", "minor", "7", "m7", "maj7"))


def _key(root, quality):
    return f"{SHARP[root % 12]}_{quality}"


def tones_of(frets):
    """每條有彈的弦的音高（0..11），由低到高。"""
    return [(STRING_PC[i] + f) % 12 for i, f in enumerate(frets) if f is not None]


def analyze(root, quality, frets, bass=None):
    """這個按法彈出來是不是這個和弦：每個音都在和弦裡、根音和三音（掛留＝四音／二音）都在、最低音對。"""
    want = set((root + iv) % 12 for iv in QUAL[quality][2])
    got = tones_of(frets)
    if not got:
        return False
    must = _must(root, quality)
    lowest = got[0]
    low_ok = lowest == (bass if bass is not None else root) % 12
    if bass is not None:
        want.add(bass % 12)
    return set(got) <= want and must <= set(got) and low_ok


def fresh_fingers(frets):
    """依格數由低到高給 1～4 指；超過 4 個音時，最低那格用食指橫按。排不出來回 None。"""
    notes = sorted(((f, i) for i, f in enumerate(frets) if f is not None and f > 0))
    fingers = [None if f is None else 0 for f in frets]
    if not notes:
        return tuple(fingers)
    if len(notes) <= 4:
        for k, (_, i) in enumerate(notes):
            fingers[i] = k + 1
        return tuple(fingers)
    low = notes[0][0]
    lows = [i for f, i in notes if f == low]
    rest = [i for f, i in notes if f > low]
    if len(lows) < 2 or len(rest) > 3 or any(frets[i] == 0 for i in range(min(lows), max(lows) + 1)):
        return None
    for i in lows:
        fingers[i] = 1
    for k, i in enumerate(sorted(rest, key=lambda i: (frets[i], i))):
        fingers[i] = k + 2
    return tuple(fingers)


def _shape_voicings(root, quality):
    out = []
    for string, table, name in ((0, E_SHAPE, "E 型"), (1, A_SHAPE, "A 型")):
        if quality not in table:
            continue
        rel, fin = table[quality]
        base = (root - STRING_PC[string]) % 12
        for r in (base, base + 12):
            low = min(x for x in rel if x is not None)
            if r + low < 0 or r + max(x for x in rel if x is not None) > 15:
                continue
            frets = tuple(None if x is None else x + r for x in rel)
            if r == 0:
                fingers = fresh_fingers(frets)
                label = "開放把位"
            else:
                fingers = fin
                label = f"{name}・第 {r + low} 格" + ("橫按" if fin.count(1) >= 2 and low == 0 else "")
            if fingers is None:
                continue
            out.append({"frets": frets, "fingers": tuple(fingers), "label": label, "kind": "open" if r == 0 else "barre"})
            break
    return sorted(out, key=lambda v: min([f for f in v["frets"] if f] or [0]))


def voicings(root, quality, bass=None):
    """這個和弦的按法，最推薦的在前。每個 {frets, fingers, label, kind, tip}；kind：open／barre／easy／slash／approx。"""
    root %= 12
    if bass is not None and bass % 12 != root:
        return slash_voicings(root, quality, bass % 12)
    out = []
    k = _key(root, quality)
    if k in ALT:
        for label, frets, fingers, tip in ALT[k]:
            kind = "easy" if "簡易" in label else ("barre" if 1 in fingers and fingers.count(1) >= 2 and 0 not in frets
                                                    else "open")
            out.append({"frets": frets, "fingers": fingers, "label": label, "kind": kind, "tip": tip})
    elif OPEN.get(k):
        frets, fingers = OPEN[k]
        out.append({"frets": frets, "fingers": fingers, "label": "開放把位", "kind": "open", "tip": ""})
    for v in _shape_voicings(root, quality):
        v.setdefault("tip", "")
        out.append(v)
    if quality not in SIMPLIFIABLE and quality != "5" and not any(v["kind"] == "open" for v in out):
        for v in search(root, quality, windows=range(0, 3), limit=8):
            if max(f or 0 for f in v["frets"]) <= 5:
                barre = sum(1 for k in v["fingers"] if k == 1) >= 2
                out.insert(0, dict(v, label="開放把位（自動）" if 0 in v["frets"] else "低把位（自動）",
                                   kind="barre" if barre else "open", tip="照規則自動排出來的：音一定對，手指是建議。"))
                break
    has_open = any(v["kind"] == "open" for v in out)
    if not has_open and k in EASY:
        frets, fingers, tip = EASY[k]
        out.insert(1 if out else 0, {"frets": frets, "fingers": fingers, "label": "簡易（不用大橫按）", "kind": "easy",
                                     "tip": tip})
    elif not has_open and quality in SIMPLIFIABLE and out:
        easy = _auto_easy(root, quality, out[0])
        if easy:
            out.insert(1, easy)
    seen, uniq = set(), []
    for v in out:
        if v["frets"] in seen or not analyze(root, quality, v["frets"]) and v["kind"] != "easy":
            continue
        if v["kind"] == "easy" and not _easy_ok(root, quality, v["frets"]):
            continue
        seen.add(v["frets"])
        uniq.append(v)
    return uniq


def _easy_ok(root, quality, frets):
    """簡易按法可以讓最低音不是根音（例如四弦 Bm 最低是 F#），但和弦的音都要在、根音三音要在。"""
    want = set((root + iv) % 12 for iv in QUAL[quality][2])
    got = set(tones_of(frets))
    must = _must(root, quality)
    return got <= want and must <= got and len(tones_of(frets)) >= 3


def _auto_easy(root, quality, base):
    """橫按的和弦拿掉低音弦（只彈第1～4弦），看剩下的音還是不是同一個和弦。"""
    for drop in (2, 3):
        frets = tuple(None if i < drop else f for i, f in enumerate(base["frets"]))
        if sum(f is not None for f in frets) < 3 or frets == base["frets"]:
            continue
        if not _easy_ok(root, quality, frets):
            continue
        fingers = fresh_fingers(frets)
        if fingers is None:
            continue
        lowest = tones_of(frets)[0]
        tip = "只彈第1～4弦" if drop == 2 else "只彈第1～3弦"
        if lowest != root:
            tip += f"；最低音變成 {spell(lowest)}，聽起來比較輕"
        return {"frets": frets, "fingers": fingers, "label": "簡易（拿掉低音弦）", "kind": "easy", "tip": tip}
    return None


def slash_voicings(root, quality, bass):
    """斜線和弦 X/Y：先找收錄的，再自動組（把比低音粗的弦悶掉，在第6、5、4弦找低音），最後退回「彈 X、低音換 Y」。"""
    out = []
    for name, frets, fingers, tip in SLASH:
        ch = parse_chord(name)
        if ch["root"] == root and ch["quality"] == quality and ch["bass"] == bass:
            out.append({"frets": frets, "fingers": fingers, "label": "常用斜線和弦", "kind": "slash", "tip": tip})
    gen = _generate_slash(root, quality, bass)
    if gen and all(gen["frets"] != v["frets"] for v in out):
        out.append(gen)
    if not out:
        base = voicings(root, quality)
        if base:
            v = dict(base[0])
            v.update(kind="approx", label="先彈原本的和弦",
                     tip=f"這個組合不好按：先彈 {std_name(root, quality)}，最低音改彈 {spell(bass)}（或乾脆省略低音）。")
            out.append(v)
    return out


def _must(root, quality, bass=None):
    """一定要有的音：和弦的每個音，只有完全五度可以省（吉他上常省）；強力和弦就是根音＋五度；斜線和弦再加低音。"""
    ivs = QUAL[quality][2]
    must = {(root + iv) % 12 for iv in ivs if iv != 7 or len(ivs) <= 2}
    if bass is not None:
        must.add(bass % 12)
    return must


def search(root, quality, bass=None, windows=range(0, 10), limit=3):
    """在指板上找按法：最低的弦（第6、5、4弦）彈低音（沒給＝根音），其他弦只彈和弦裡的音、四格以內、
    最多一條中間的弦不彈、手指排得開。分數越低越好按（手指少、把位低、空弦多、響的弦多）。"""
    root %= 12
    allowed = {(root + iv) % 12 for iv in QUAL[quality][2]}
    low_pc = root if bass is None else bass % 12
    if bass is not None:
        allowed.add(low_pc)
    must = _must(root, quality, bass)
    small = len(QUAL[quality][2]) <= 3
    found = {}
    for w in windows:
        lo, hi = max(1, w), max(1, w) + 3
        for s in range(3):
            base_opts = [f for f in ([0] if w <= 2 else []) + list(range(lo, hi + 1))
                         if (STRING_PC[s] + f) % 12 == low_pc]
            for bf in base_opts:
                bass_midi = STRING_MIDI[s] + bf
                opts = []
                for i in range(s + 1, 6):
                    o = [f for f in ([0] if w <= 2 else []) + list(range(lo, hi + 1))
                         if (STRING_PC[i] + f) % 12 in allowed and STRING_MIDI[i] + f > bass_midi]
                    opts.append(o + [None])

                def walk(i, acc, muted):
                    if i == 6:
                        frets = tuple([None] * s + [bf] + acc)
                        sounding = [f for f in frets if f is not None]
                        if len(sounding) < (3 if small else 4) or not must <= set(tones_of(frets)):
                            return
                        if acc and acc[-1] is None and muted > 1:
                            return
                        fingers = fresh_fingers(frets)
                        if fingers is None:
                            return
                        pressed = [f for f in frets if f]
                        n_fing = len({k for k in fingers if k})
                        barre = sum(1 for k in fingers if k == 1) >= 2
                        sounding_idx = [k for k, f in enumerate(frets) if f is not None]
                        pressed_idx = [k for k, f in enumerate(frets) if f]
                        between = sum(1 for k in sounding_idx if frets[k] == 0 and pressed_idx
                                      and min(pressed_idx) < k < max(pressed_idx))
                        score = between * 1.5 - frets.count(0) * 0.3 + n_fing + (2 if barre else 0) + (min(pressed) * 0.6 if pressed and min(pressed) > 3 else 0) \
                            + (6 - len(sounding)) * 0.8 + muted * 1.5 + (0.5 if (root + 7) % 12 not in tones_of(frets)
                                                                        and 7 in QUAL[quality][2] else 0)
                        if frets not in found or score < found[frets][0]:
                            found[frets] = (score, fingers)
                        return
                    for f in opts[i - s - 1]:
                        inner = f is None and i < 5
                        if inner and muted >= 1:
                            continue
                        walk(i + 1, acc + [f], muted + (1 if f is None else 0))

                walk(s + 1, [], 0)
    best = sorted(found.items(), key=lambda kv: kv[1][0])[:limit]
    return [{"frets": frets, "fingers": fingers} for frets, (score, fingers) in best]


def _generate_slash(root, quality, bass):
    found = search(root, quality, bass, limit=1)
    if not found:
        return None
    return {"frets": found[0]["frets"], "fingers": found[0]["fingers"], "label": "自動組合", "kind": "slash",
            "tip": "照規則自動排出來的：音一定對，手指是建議。"}


def difficulty(ch, shift=0):
    """這個和弦（平移後）好不好按：0 開放、1 簡易／斜線、3 要橫按、4 沒有好按法。capo 建議用。"""
    vs = voicings(ch["root"] + shift, ch["quality"], None if ch["bass"] is None else ch["bass"] + shift)
    if not vs:
        return 4
    kinds = {v["kind"] for v in vs}
    if "open" in kinds:
        return 0
    if vs[0]["kind"] == "slash" and 0 in vs[0]["frets"]:
        return 0.5
    if "easy" in kinds or "slash" in kinds:
        return 1.5
    return 3


# ── 和弦圖（文字） ────────────────────────────────────────────────────────────

def frets_text(frets):
    return " ".join("x" if f is None else str(f) for f in frets)


def start_text(frets):
    i = next((i for i, f in enumerate(frets) if f is not None), None)
    if i is None:
        return ""
    muted = [str(6 - j) for j, f in enumerate(frets) if f is None]
    head = f"從第{6 - i}弦開始彈"
    inner = [m for m in muted if int(m) < 6 - i]
    return head + (f"（第{'、'.join(inner)}弦不要響）" if inner else "")


def finger_lines(frets, fingers):
    lines = []
    for i in range(6):
        f = frets[i]
        if f is None or f == 0:
            continue
        k = fingers[i] if fingers and fingers[i] is not None else 0
        lines.append(f"第{6 - i}弦 第{f}格（{FINGER[k] if k else '按'}）{spell(STRING_PC[i] + f)}")
    barre = [i for i in range(6) if fingers and fingers[i] == 1 and frets[i]]
    if len(barre) >= 2:
        lines.insert(0, f"食指橫按第{frets[barre[0]]}格（第{6 - barre[-1]}～{6 - barre[0]}弦）")
        lines = [ln for ln in lines if "（食指）" not in ln]
    return lines


def describe(ch, key=None, full=True):
    """一個和弦的說明：按法、從哪條弦開始、手指、組成音、其他按法。"""
    root, q, bass = ch["root"], ch["quality"], ch["bass"]
    name = own_name(ch)
    vs = voicings(root, q, bass)
    head = f"🎸 {name}（{QUAL[q][1]}{'／' + '低音 ' + spell(bass, key) if bass is not None else ''}）"
    if ch.get("approx"):
        head += f"\n（{ch['suffix']} 先用 {std_name(root, q, bass, key)} 的按法；延伸音可以省略）"
    if not vs:
        return head + "\n找不到好按的按法。"
    v = vs[0]
    lines = [head, f"按法：{frets_text(v['frets'])}（第6弦→第1弦）· {v['label']}", start_text(v["frets"])]
    if full:
        lines += ["・" + ln for ln in finger_lines(v["frets"], v["fingers"])]
        ivs = QUAL[q][2]
        lines.append("組成音：" + "、".join(f"{spell(root + iv, key)}（{TONE_NAME.get(iv, '')}）" for iv in ivs))
    if v.get("tip"):
        lines.append("💡 " + v["tip"])
    others = vs[1:4]
    if others:
        lines.append("其他按法：" + "；".join(f"{o['label']} {frets_text(o['frets'])}" for o in others))
    return "\n".join(x for x in lines if x)


def describe_short(ch, key=None):
    vs = voicings(ch["root"], ch["quality"], ch["bass"])
    name = own_name(ch)
    if not vs:
        return f"{name}：找不到好按的按法"
    v = vs[0]
    extra = ""
    easy = next((o for o in vs[1:] if o["kind"] == "easy"), None)
    if v["kind"] == "barre" and easy:
        extra = f" · 簡易：{frets_text(easy['frets'])}"
    return f"{name}：{frets_text(v['frets'])}（{v['label']}）{extra}"


# ── 譜：找和弦、移調 ─────────────────────────────────────────────────────────

TOKEN = re.compile(r"[^\s|:：]+")
INLINE = re.compile(r"\[([^\[\]\s]{1,16})\]")
DECOR = re.compile(r"^(?:[-–—/%.*~>…_=]+|[x×X]\d+|\(?[x×X]?\d+[x×X]?\)?|N\.?C\.?|\d+\.|[(){}\[\]]+|\|+|:?\|\|?:?)$")
SECTION = re.compile(r"^[\[(（【]?(?:intro|verse|pre-?chorus|chorus|bridge|outro|interlude|solo|ending|coda|riff|"
                     r"前奏|主歌|副歌|間奏|尾奏|橋段|導歌|過門|獨奏|結尾|[A-Z]\d?段?|[AB]'?|Capo.*|key.*|調.*)"
                     r"[\])）】]?\d*$", re.IGNORECASE)
WRAP = re.compile(r"^([(\[{（【]*)(.*?)([)\]}）】.,*!?;]*)$")


def _token_chord(tok):
    m = WRAP.match(tok)
    core = m.group(2) if m else tok
    ch = parse_chord(core)
    return (ch, m.group(1), m.group(3)) if ch else (None, "", "")


def chord_line(line):
    """這一行是不是「整行都是和弦」：至少一個和弦，其他只能是小節線、反覆記號、段落名稱（前奏：、[Chorus]）。
    回 [(start, end, 和弦, 前綴, 後綴), …]；不是和弦行回 None。"""
    found, other = [], 0
    for m in TOKEN.finditer(line):
        tok = m.group(0)
        ch, pre, post = _token_chord(tok)
        if ch:
            found.append((m.start(), m.end(), ch, pre, post))
        elif DECOR.match(tok) or SECTION.match(tok):
            continue
        else:
            other += 1
    return found if found and other == 0 else None


FLAT_NOTE = "ℹ️ 譜變成一行了（聊天指令會把換行併起來），像和弦的字都照和弦移；要保留排版請用網頁面板貼譜。"


def flattened(text):
    """聊天指令會把換行併成空白（指令樹照空白切參數），整份譜就變成一行。
    只有一行、又不是整行和弦時，分不出哪些是和弦行、哪些是歌詞，就改成「每個像和弦的字都算」。"""
    t = (text or "").strip()
    return bool(t) and "\n" not in t and chord_line(t) is None


def loose_line(line):
    """一行裡每個自己一個字、看起來是和弦的（其他字不管）。格式同 chord_line。"""
    found = []
    for m in TOKEN.finditer(line):
        ch, pre, post = _token_chord(m.group(0))
        if ch:
            found.append((m.start(), m.end(), ch, pre, post))
    return found


def _inline_chords(text):
    return [c for c in (parse_chord(m.group(1)) for m in INLINE.finditer(text)) if c]


def _inline_sub(text, fn):
    return INLINE.sub(lambda m: f"[{fn(parse_chord(m.group(1)))}]" if parse_chord(m.group(1)) else m.group(0), text)


def chords_in(text):
    """譜裡依序出現的每個和弦（和弦行裡的、和 [C] 夾在歌詞裡的）。"""
    if flattened(text):
        line, out, prev = text.strip(), [], 0
        for start, end, ch, _, _ in loose_line(line):
            out += _inline_chords(line[prev:start]) + [ch]
            prev = end
        return out + _inline_chords(line[prev:])
    out = []
    for line in (text or "").splitlines():
        found = chord_line(line)
        if found:
            out += [f[2] for f in found]
        else:
            out += _inline_chords(line)
    return out


def _rewrite_line(line, found, fn):
    """把和弦行裡每個和弦換成 fn(ch)，盡量讓每個和弦還在原本那一欄（歌詞對得上）。"""
    out, prev = "", 0
    for start, end, ch, pre, post in found:
        gap = line[prev:start]
        out += gap
        if gap and gap.endswith(" ") and len(out) > start:
            over = len(out) - start
            trimmed = out.rstrip(" ")
            spaces = len(out) - len(trimmed)
            out = trimmed + " " * max(1, spaces - over)
        out += pre + fn(ch) + post
        prev = end
    return out + line[prev:]


def rewrite(text, fn):
    if flattened(text):
        line, out, prev = text.strip(), "", 0
        for start, end, ch, pre, post in loose_line(line):
            out += _inline_sub(line[prev:start], fn) + pre + fn(ch) + post
            prev = end
        return out + _inline_sub(line[prev:], fn)
    lines = []
    for line in (text or "").splitlines():
        found = chord_line(line)
        if found:
            lines.append(_rewrite_line(line, found, fn))
        else:
            lines.append(_inline_sub(line, fn))
    return "\n".join(lines)


def detect_key(chords):
    """從和弦猜調：每個大調算「有幾個和弦是這個調的和弦」，第一個、最後一個和弦是主和弦再加分；
    關係小調的主和弦（vi）在頭尾、大調的 I 不在結尾 → 當成小調。沒有和弦回 None。"""
    if not chords:
        return None
    diatonic = {0: "M", 2: "m", 4: "m", 5: "M", 7: "M", 9: "m", 11: "d"}

    def kind(ch):
        q = ch["quality"]
        return "m" if q in MINORISH else "d" if q in DIMISH else "*" if q in ("sus2", "sus4", "5", "7sus4") else "M"

    best = None
    for t in range(12):
        score = 0.0
        for ch in chords:
            deg = (ch["root"] - t) % 12
            if deg in diatonic:
                score += 1.0 if kind(ch) in (diatonic[deg], "*") else 0.3
        first, last = chords[0], chords[-1]
        for ch, w in ((first, 1.0), (last, 1.5)):
            if ch["root"] == t and kind(ch) != "m":
                score += w
            elif ch["root"] == (t + 9) % 12 and kind(ch) == "m":
                score += w * 0.8
        if best is None or score > best[0] + 1e-9:
            best = (score, t)
    t = best[1]
    rel = (t + 9) % 12
    first, last = chords[0], chords[-1]
    starts_minor = first["root"] == rel and kind(first) == "m"
    starts_major = first["root"] == t and kind(first) != "m"
    ends_minor = last["root"] == rel and kind(last) == "m"
    return (rel, True) if starts_minor or (ends_minor and not starts_major) else (t, False)


SHIFT_RE = re.compile(r"^(?:([+\-−＋])\s*(\d{1,2})|(升|降|up|down)\s*(\d{1,2})\s*(?:個)?\s*(?:半音|key|格)?)$", re.I)
ARROW_RE = re.compile(r"^(\S*?)\s*(?:→|->|=>|>|到|to|轉|移到|移)\s*(\S+?)\s*(?:調)?$", re.I)


def parse_shift(spec, source_key):
    """'+2'、'-3'、'升2'、'降 1'、'G→A'、'G>A'、'G 到 A'、'到 A'、'→Bb' → (半音, 目標調) 或 None。"""
    s = (spec or "").strip()
    m = SHIFT_RE.match(s)
    if m:
        sign = m.group(1) or m.group(3)
        n = int(m.group(2) or m.group(4))
        if n > 11:
            return None
        n = -n if sign in ("-", "−", "降") or sign.lower() == "down" else n
        return n % 12, None
    m = ARROW_RE.match(s)
    if m:
        src = parse_key(m.group(1)) if m.group(1) else source_key
        dst = parse_key(m.group(2))
        if src is None or dst is None:
            return None
        n = (dst[0] - src[0]) % 12
        # 主音對主音、大小調照原曲（Em 歌「到 G」＝ Gm）；使用者明寫小調（到 Gm）就照他寫的
        target = dst if dst[1] else (dst[0], src[1])
        return n, target
    return None


def shift_label(n):
    n %= 12
    if n == 0:
        return "不移調"
    return f"升 {n} 個半音" if n <= 6 else f"降 {12 - n} 個半音"


def split_spec(text):
    """把「移法」和「譜」分開：第一行是移法（或第一行的第一個字）；沒有譜回 (移法, "")。"""
    text = (text or "").strip("\n")
    lines = text.split("\n")
    first = lines[0].strip()
    rest = "\n".join(lines[1:])
    if parse_shift(first, (0, False)) is not None:
        return first, rest
    # 移法和譜在同一行（聊天指令把換行併成空白）：移法最多三個字（+2、到 Eb、G 到 A），從短的試
    words = first.split()
    for n in range(1, min(3, len(words) - 1) + 1):
        spec = " ".join(words[:n])
        if parse_shift(spec, (0, False)) is not None:
            tail = first.split(None, n)[n]
            return spec, (tail + ("\n" + rest if rest else "")).strip("\n")
    return None, text


def capo_options(chords, key, limit=3):
    """夾第 0～7 格，每一種「手型的調」要按幾個橫按：越少越好，capo 越低越好。回 [(capo, 手型調, 分數, 橫按的和弦), …]。"""
    uniq = []
    for ch in chords:
        k = (ch["root"], ch["quality"], ch["bass"])
        if k not in [(c["root"], c["quality"], c["bass"]) for c in uniq]:
            uniq.append(ch)
    res = []
    for capo in range(8):
        shape_key = ((key[0] - capo) % 12, key[1]) if key else None
        cost, hard, easy = 0.0, [], []
        for ch in uniq:
            d = difficulty(ch, -capo)
            cost += d
            if d >= 3:
                hard.append(chord_name(ch, -capo, shape_key))
            elif d >= 1.5:
                easy.append(chord_name(ch, -capo, shape_key))
        res.append((capo, shape_key, cost + capo * 0.25, hard, easy))
    res.sort(key=lambda r: (r[2], r[0]))
    return res[:limit]


def unique_names(chords, shift=0, key=None):
    seen = []
    for ch in chords:
        n = chord_name(ch, shift, key)
        if n not in seen:
            seen.append(n)
    return seen


def capo_line(opt, chords):
    capo, shape_key, _, hard, easy = opt
    names = " ".join(unique_names(chords, -capo, shape_key))
    head = "不夾 capo" if capo == 0 else f"Capo {capo}"
    shape = f"，用 {key_name(shape_key)} 調手型" if shape_key and capo else ""
    notes = ([f"要橫按：{'、'.join(hard)}"] if hard else []) + ([f"用簡易按法：{'、'.join(easy)}"] if easy else [])
    return f"{head}{shape}：{names}（{'；'.join(notes) if notes else '全部是開放和弦'}）"


# ── 分散和弦 TAB ──────────────────────────────────────────────────────────────

PATTERNS = (
    ("常見型", "T1213121"),
    ("基本交替型", "T123T123"),
    ("綿密型", "T2123212"),
    ("三拍子", "T12321"),
    ("簡單型", "T123"),
)


def parse_pattern(text):
    t = (text or "").strip()
    if t.isdigit() and 1 <= int(t) <= len(PATTERNS):
        return PATTERNS[int(t) - 1][1]
    seq = re.sub(r"[\s\-,.、]", "", t.upper())
    return seq if seq and re.fullmatch(r"[T123]{2,16}", seq) else None


def arp_tab(chords, pattern, key=None):
    """每個和弦一小節；T＝最低的那條弦（和弦的根音或低音），1／2／3＝第3／2／1弦。"""
    rows = [[] for _ in range(6)]                                   # 由第1弦（e）到第6弦
    names = []
    for ch in chords:
        vs = voicings(ch["root"], ch["quality"], ch["bass"])
        if not vs:
            continue
        frets = vs[0]["frets"]
        low = next(i for i, f in enumerate(frets) if f is not None)
        width = max(2, max(len(str(f)) for f in frets if f is not None) + 1)
        names.append((own_name(ch), len(pattern) * width))
        for r in rows:
            r.append("|")
        for tok in pattern:
            s = low if tok == "T" else {"1": 3, "2": 4, "3": 5}[tok]
            f = frets[s]
            for row in range(6):
                cell = str(f) if (5 - row) == s and f is not None else ""
                rows[row].append(cell.ljust(width, "-"))
    if not names:
        return ""
    head = " " + "".join(" " + n.ljust(w) for n, w in names)
    body = [STRING_NAME[5 - row] + "".join(rows[row]) + "|" for row in range(6)]
    return "\n".join([head.rstrip()] + body)


# ── 一個調裡的和弦 ───────────────────────────────────────────────────────────

MAJOR_DEG = ((0, "major", "I"), (2, "minor", "ii"), (4, "minor", "iii"), (5, "major", "IV"), (7, "major", "V"),
             (9, "minor", "vi"), (11, "dim", "vii°"))
MINOR_DEG = ((0, "minor", "i"), (2, "dim", "ii°"), (3, "major", "III"), (5, "minor", "iv"), (7, "minor", "v"),
             (8, "major", "VI"), (10, "major", "VII"))
PROGS_MAJOR = (("1564（流行最常見）", (0, 4, 5, 3)), ("卡農", (0, 4, 5, 2, 3, 0, 3, 4)), ("1645（復古甜）", (0, 5, 3, 4)),
               ("6415", (5, 3, 0, 4)), ("ii–V–I", (1, 4, 0)))
PROGS_MINOR = (("i–VI–III–VII", (0, 5, 2, 6)), ("i–iv–v–i", (0, 3, 4, 0)), ("i–VII–VI–V（安達魯西亞）", (0, 6, 5, 7)))


def key_chords(key):
    tonic, minor = key
    degs = MINOR_DEG if minor else MAJOR_DEG
    return [(num, {"root": (tonic + off) % 12, "suffix": QUAL[q][0], "quality": q, "bass": None, "approx": False})
            for off, q, num in degs]


def describe_key(key):
    tonic, minor = key
    chords = key_chords(key)
    lines = [f"🎸 {key_name(key)}{'（小調）' if minor else ' 大調'}的和弦",
             "・".join(f"{num} {chord_name(ch, 0, key)}" for num, ch in chords)]
    main = [chords[i][1] for i in ((0, 3, 4, 5) if not minor else (0, 3, 6, 5))]
    lines.append("最常用四個：" + " ".join(chord_name(ch, 0, key) for ch in main))
    progs = PROGS_MINOR if minor else PROGS_MAJOR
    for name, seq in progs:
        names = []
        for i in seq:
            if i == 7:                                               # 小調的 V：借和聲小調的大三和弦（Am 調用 E）
                names.append(std_name((tonic + 7) % 12, "major", key=key))
            else:
                names.append(chord_name(chords[i][1], 0, key))
        lines.append(f"{name}：{' '.join(names)}")
    hard = [chord_name(ch, 0, key) for _, ch in chords[:6] if difficulty(ch) >= 3]
    if hard:
        lines.append("要橫按的：" + "、".join(hard))
    opts = capo_options([ch for _, ch in chords[:6]], key)
    best = opts[0]
    if best[0]:
        lines.append(f"💡 不想橫按：{capo_line(best, [ch for _, ch in chords[:6]])}")
    return "\n".join(lines)


# ── 小節譜（一格一小節） ─────────────────────────────────────────────────────
# 格式（README「小節譜的格式」）：{"title", "artist", "key", "capo", "time", "tempo", "bars_per_line",
#   "sections": [{"name", "repeat", "bars": [["C"], ["G", "D/F#"], ["Am:3", "G:1"], ["%"], ["N.C."]], "lyrics": [...]}],
#   "notes", "image": {"marks": [{"x": 0.12, "y": 0.30, "chord": "Am"}]}}
# 一格（一小節）是一串字：和弦、和弦:拍數、%（同上一小節）、N.C.（不彈）、?（看不清楚）。

MAX_BARS = 512
MAX_SECTIONS = 40
MAX_MARKS = 400
NC_RE = re.compile(r"^(?:N\.?\s?C\.?|NC|n\.c\.)$")
BEATS_RE = re.compile(r"^(.+?)[:：](\d{1,2})$")
HOLDS = frozenset(("/", ".", "-", "·", "–", "—"))
SAME = frozenset(("%", "％", "〃", "//."))
TIME_RE = re.compile(r"^([1-9]\d?)/(2|4|8|16)$")
BAR_MARK = re.compile(r"[|｜]")
REPEAT_SIGN = re.compile(r"[|｜]\s*[:：]|[:：]\s*[|｜]")
REPEAT_TAIL = re.compile(r"(?:^|\s+|(?<=[|｜]))[(（]?\s*(?:[x×X]\s*(\d{1,2})|(\d{1,2})\s*(?:[x×X]|次|遍))\s*[)）]?\s*$")
REPEAT_ONLY = re.compile(r"^(?:[x×X]\s*\d{1,2}|\d{1,2}\s*(?:[x×X]|次|遍)|[x×X])$")
LABEL_BOX = re.compile(r"^\s*[\[【]([^\[\]【】]{1,24})[\]】]\s*(.*)$")
LABEL_COLON = re.compile(r"^\s*([^|｜:：\[\]【】]{1,16}?)\s*[:：](?!\d)\s*(.*)$")
META = (
    ("title", re.compile(r"^(?:標題|歌名|曲名|title)\s*[:：]\s*(.+)$", re.I)),
    ("artist", re.compile(r"^(?:歌手|演唱|原唱|artist|by)\s*[:：]\s*(.+)$", re.I)),
    ("key", re.compile(r"^(?:key|原調|調)\s*[:：=]?\s*(\S+?)\s*(?:調)?$", re.I)),
    ("capo", re.compile(r"^(?:capo|夾)\s*[:：=]?\s*(\d{1,2})\s*(?:格)?$", re.I)),
    ("time", re.compile(r"^(?:拍號\s*[:：]?\s*)?(\d{1,2}/\d{1,2})$")),
    ("tempo", re.compile(r"^(?:(?:♩|bpm|tempo|速度)\s*[:：=]?\s*(\d{2,3})|(\d{2,3})\s*bpm)$", re.I)),
    ("bars_per_line", re.compile(r"^(?:每行|bars)\s*[:：=]?\s*(\d)\s*(?:小節|格)?$", re.I)),
)
WIDE = re.compile(r"[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]")
JSON_TOKEN = re.compile(r'\s*(?:([{}\[\]:,])|"((?:[^"\\]|\\.)*)"|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(true|false|null))',
                        re.S)
JSON_ESC = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def read_json(text):
    """小小的 JSON 讀取器（本機工具只准用幾個標準庫，所以不 import json）。多的逗號放過、```json 圍欄拿掉；看不懂回 None。"""
    s = re.sub(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$", "", text or "").strip()
    toks, pos = [], 0
    while pos < len(s):
        m = JSON_TOKEN.match(s, pos)
        if not m:
            return None
        toks.append(m)
        pos = m.end()
    at = [0]

    def unescape(raw):
        out = re.sub(r"\\(u[0-9a-fA-F]{4}|.)",
                     lambda e: chr(int(e.group(1)[1:], 16)) if e.group(1)[0] == "u" and len(e.group(1)) == 5
                     else JSON_ESC.get(e.group(1), e.group(1)), raw)
        return out.encode("utf-16", "surrogatepass").decode("utf-16", "replace")

    def value():
        if at[0] >= len(toks):
            raise ValueError
        m = toks[at[0]]
        at[0] += 1
        punct, string, number, word = m.groups()
        if string is not None:
            return unescape(string)
        if number is not None:
            return float(number) if re.search(r"[.eE]", number) else int(number)
        if word is not None:
            return {"true": True, "false": False, "null": None}[word]
        if punct == "[":
            return seq("]", lambda: value())
        if punct == "{":
            pairs = seq("}", pair)
            return {k: v for k, v in pairs}
        raise ValueError

    def pair():
        k = value()
        if not isinstance(k, str) or at[0] >= len(toks) or toks[at[0]].group(1) != ":":
            raise ValueError
        at[0] += 1
        return k, value()

    def seq(close, item):
        out = []
        while True:
            if at[0] >= len(toks):
                raise ValueError
            if toks[at[0]].group(1) == close:
                at[0] += 1
                return out
            out.append(item())
            if at[0] < len(toks) and toks[at[0]].group(1) == ",":
                at[0] += 1
            elif at[0] >= len(toks) or toks[at[0]].group(1) != close:
                raise ValueError

    try:
        result = value()
    except (ValueError, RecursionError):
        return None
    return result if at[0] == len(toks) else None


def _width(text):
    """等寬字型裡的寬度：中日韓的字算 2 格。"""
    return sum(2 if WIDE.match(c) else 1 for c in text)


def split_beats(tok):
    """'Am:3' → ('Am', 3)；'Am' → ('Am', None)。"""
    m = BEATS_RE.match(tok)
    return (m.group(1), int(m.group(2))) if m else (tok, None)


def cell_word(word):
    """小節裡的一個字 → 標準寫法：和弦（照原本寫法，可帶 :拍數）、'%'、'N.C.'、'?'；'/'、'.' 是「多一拍」回 '+'；看不懂回 None。"""
    t = (word or "").strip()
    if t in SAME:
        return "%"
    if t in ("?", "？"):
        return "?"
    if t in HOLDS:
        return "+"
    name, beats = split_beats(t)
    if NC_RE.match(name):
        return "N.C." + (f":{beats}" if beats else "")
    ch, _, _ = _token_chord(name)
    if not ch:
        return None
    return own_name(ch) + (f":{beats}" if beats else "")


def beats_of(time_sig):
    m = TIME_RE.match(time_sig or "")
    return int(m.group(1)) if m else 4


def make_bar(words, bpb=4, unknown=None):
    """一小節的字 → 一格（list）。'C . . G' → ['C:3', 'G:1']；平均分的拍子不寫數字（'G D/F#' → ['G', 'D/F#']）。"""
    items = []                                                      # [名字, 寫明的拍數, 多出來的拍數]
    for w in words:
        c = cell_word(w)
        if c is None:
            if unknown is not None:
                unknown.append(w)
            items.append(["?", None, 0])
        elif c == "+":
            if items:
                items[-1][2] += 1
        else:
            name, beats = split_beats(c)
            items.append([name, beats, 0])
    if any(it[2] for it in items):
        for it in items:
            it[1] = (it[1] or 1) + it[2]
    out = [[name, beats] for name, beats, _ in items]
    if out and all(b for _, b in out) and len({b for _, b in out}) == 1 and sum(b for _, b in out) == bpb:
        out = [[name, None] for name, _ in out]                     # 平均分＝不用寫拍數
    if len(out) == 1:
        out[0][1] = None
    return [name + (f":{beats}" if beats else "") for name, beats in out][:8]


def _stream_bars(items, bpb):
    """沒有小節線、但有 '/' 拍子記號的一行：照拍子排進一小節 bpb 拍（例 C / / / G / / / → | C | G |）。"""
    bars, cur, used = [], [], 0
    for name, beats in items:
        while beats > 0:
            take = min(beats, bpb - used)
            cur.append([name, take])
            used += take
            beats -= take
            if used == bpb:
                bars.append(cur)
                cur, used = [], 0
    if cur:
        bars.append(cur)
    return [make_bar([f"{n}:{b}" for n, b in bar], bpb) for bar in bars]


def _plain_line(line, offset, bpb):
    """沒有小節線的一行：整行都是和弦＝一個和弦一小節（有 '/' 拍子記號或 'C:2' 拍數就照拍子排，沒寫的算一拍）。
    回 (小節們, 每小節在這行的欄位)；不是和弦行（例如歌詞）回 (None, None)。"""
    words, cols, holds = [], [], False
    for m in re.finditer(r"\S+", line):
        w = m.group(0)
        c = cell_word(w)
        if c is None:
            if DECOR.match(w) or SECTION.match(w):
                continue
            return None, None
        holds = holds or c == "+" or split_beats(c)[1] is not None     # 有拍子記號或寫了拍數：照拍子排
        words.append(c)
        cols.append(_width(line[:m.start()]) + offset)
    if not words or all(w == "+" for w in words):
        return None, None
    if holds:
        items = []
        for w in words:
            if w == "+":
                if items:
                    items[-1][1] += 1
                continue
            name, beats = split_beats(w)
            items.append([name, beats or 1])
        return _stream_bars(items, bpb), []
    return [[w] for w in words], cols


def _bar_line(line, bpb, unknown):
    """有小節線的一行：| C | G D/F# | Am . . G | % |。中間空的格＝同上一小節。"""
    segs = BAR_MARK.split(re.sub(r"[|｜]{2,}", "|", REPEAT_SIGN.sub("|", line)))
    if segs and not segs[0].strip():
        segs = segs[1:]
    if segs and not segs[-1].strip():
        segs = segs[:-1]
    bars = []
    for seg in segs:
        words = [w for w in seg.split() if not re.fullmatch(r"[(（]?[x×X]?\d{0,2}[x×X]?[)）]?", w) or cell_word(w)]
        bars.append(make_bar(words, bpb, unknown) if words else ["%"])
    return [b for b in bars if b]


def _inline_line(line):
    """ChordPro：這是[G]一首[D]簡單的[Em]歌 → 每個和弦一小節，歌詞是它後面那段字。"""
    parts = INLINE.split(line)
    if len(parts) < 3:
        return None, None
    bars, lyrics, lead = [], [], parts[0].strip()
    for i in range(1, len(parts), 2):
        c = cell_word(parts[i])
        if c is None or c == "+":
            continue
        bars.append([c])
        lyrics.append(parts[i + 1].strip())
    if not bars:
        return None, None
    if lead:
        lyrics[0] = (lead + " " + lyrics[0]).strip()
    return bars, lyrics


def _lyric_slices(text, cols):
    """歌詞照和弦的欄位切開（中文字算 2 格寬）。"""
    out, pos, col = [""] * len(cols), 0, 0
    for ch in text:
        while pos + 1 < len(cols) and col >= cols[pos + 1] - 1:
            pos += 1
        out[pos] += ch
        col += _width(ch)
    return [x.strip() for x in out]


def _meta(line, chart):
    """「Key: G」「Capo 2」「4/4」「♩=90」「標題：…」這種資訊行（可以用兩個空白、｜、逗號分開寫在同一行）。"""
    parts = [p.strip() for p in re.split(r"\s{2,}|[｜,，;；]", line.strip()) if p.strip()]
    found = {}
    for p in parts:
        for field, rx in META:
            m = rx.match(p)
            if m:
                found[field] = next(g for g in m.groups() if g)
                break
        else:
            return False
    if "key" in found and parse_key(found["key"]) is None:
        return False
    for field, v in found.items():
        chart[field] = int(v) if field in ("capo", "tempo", "bars_per_line") else v.strip()
    return bool(found)


def _label(line):
    """段落名稱：[主歌]、【副歌】×2、前奏：、Verse 1:、單獨一行的 Chorus、【×2】（沒有名字、整段重複）。
    回 (名稱, 同一行剩下的, 整段重複幾次)；不是段落回 (None, line, 1)。"""
    for rx in (LABEL_BOX, LABEL_COLON):
        m = rx.match(line)
        inner = m.group(1).strip() if m else ""
        if not inner or cell_word(inner):
            continue
        if rx is LABEL_BOX and REPEAT_ONLY.match(inner):
            n = int(re.sub(r"\D", "", inner))
            return "", m.group(2), n if 2 <= n <= 16 else 1
        if REPEAT_ONLY.match(inner):
            continue
        reps, body = _repeat(m.group(2))
        return (inner, "", reps) if reps > 1 and not body.strip() else (inner, m.group(2), 1)
    t = line.strip()
    if SECTION.match(t) and not cell_word(t) and not DECOR.match(t):
        return t.strip("[]()（）【】"), "", 1
    return None, line, 1


def _repeat(line):
    """行尾的 (x2)、x3、2次，或 |: … :| → (次數, 拿掉之後的行)；沒有回 (1, line)。"""
    m = REPEAT_TAIL.search(line)
    if m and (m.group(1) or m.group(2)):
        n = int(m.group(1) or m.group(2))
        if 2 <= n <= 16:
            return n, line[:m.start()]
    if re.search(r"[:：]\s*[|｜]", line):
        return 2, line
    return 1, line


def parse_progression(text):
    """打的或貼的和弦進行 → 小節譜（dict，格式見 README）；回 (譜, 看不懂的字)。
    認得：| C | G D/F# | Am | F |、C G Am F（一個和弦一小節）、C / / / G / / /（照拍子）、[主歌]／前奏：段落、
    %、N.C.、(x2)、|: :|、Key／Capo／4/4／♩=90 資訊行、和弦在上歌詞在下（歌詞照欄位分到小節）、[C] 夾在歌詞裡。"""
    chart = {"title": "", "artist": "", "key": "", "capo": 0, "time": "4/4", "tempo": None, "bars_per_line": 4,
             "sections": [], "notes": ""}
    unknown, sec, last, prev_n, closed = [], None, None, 0, False  # last：上一行和弦的 (段落, 第一個小節, 欄位)

    def section(name):
        s = {"name": name, "bars": [], "lyrics": []}
        chart["sections"].append(s)
        return s

    def repeat(n, reps):
        """最後 n 小節重複 reps 次：剛好是整段＝整段重複；不是就把這幾小節拆成自己一段（沒有名字）。"""
        cur = chart["sections"][-1]
        if len(cur["bars"]) > n:
            moved_b, moved_l = cur["bars"][-n:], cur["lyrics"][-n:]
            del cur["bars"][-n:], cur["lyrics"][-n:]
            cur = section("")
            cur["bars"], cur["lyrics"] = moved_b, moved_l
        cur["repeat"] = reps
        return cur

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            last = None
            continue
        if not chart["sections"] and _meta(line, chart):
            continue
        bpb = beats_of(chart["time"])
        label, rest, hdr_reps = _label(line)
        if label is not None:
            sec, last, prev_n, closed = section(label), None, 0, False
            if hdr_reps > 1:
                sec["repeat"] = hdr_reps
            if not rest.strip():
                continue
        offset = _width(line[:len(line) - len(rest)]) if label is not None else 0
        reps, body = _repeat(rest)
        if not body.strip():                                        # 單獨一行的 (x2)：上一行重複
            if reps > 1 and prev_n:
                sec, last, closed = repeat(prev_n, reps), None, True
            continue
        lyrics, cols = None, None
        if BAR_MARK.search(body):
            bars = _bar_line(body, bpb, unknown)
        else:
            bars, cols = _plain_line(body, offset, bpb)
            if bars is None:
                bars, lyrics = _inline_line(body)
        if not bars:                                                # 歌詞行：照欄位分給上一行的和弦
            if last:
                s, start, c = last
                for i, lyr in enumerate(_lyric_slices(body, c)):
                    s["lyrics"][start + i] = lyr
                if reps > 1:                                        # 歌詞行尾的 (x2)：上面那行和弦重複
                    sec, closed = repeat(prev_n, reps), True
            last = None
            continue
        if sec is None or closed:
            sec, closed = section(""), False
        start = len(sec["bars"])
        sec["bars"] += bars
        sec["lyrics"] += lyrics or [""] * len(bars)
        last = (sec, start, cols) if cols and len(cols) == len(bars) else None
        prev_n = len(bars)
        if reps > 1:
            sec, last, closed = repeat(len(bars), reps), None, True
    return chart, unknown


def _num(v, lo, hi, default):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n if lo <= n <= hi else default


def _text(v, limit):
    return str(v).strip()[:limit] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def normalize_chart(obj):
    """AI 填的（或解析出來的）小節譜 → 整理好、檢查過的；回 (譜, 問題)。看不懂的和弦換成 '?' 並列在問題裡。"""
    problems, unknown = [], []
    if not isinstance(obj, dict):
        return None, ["不是小節譜（要是一個 {…}）"]
    time_sig = obj.get("time") if TIME_RE.match(str(obj.get("time") or "")) else "4/4"
    bpb = beats_of(time_sig)
    chart = {"title": _text(obj.get("title"), 80), "artist": _text(obj.get("artist"), 80), "key": "",
             "capo": _num(obj.get("capo"), 0, 11, 0), "time": time_sig, "tempo": _num(obj.get("tempo"), 20, 320, None),
             "bars_per_line": _num(obj.get("bars_per_line"), 1, 8, 4), "sections": [], "notes": _text(obj.get("notes"), 500)}
    key = _text(obj.get("key"), 12)
    if key and parse_key(key):
        chart["key"] = key_name(parse_key(key))
    elif key:
        problems.append(f"看不懂調「{key}」，改用程式猜的")
    total = 0
    for s in (obj.get("sections") or [])[:MAX_SECTIONS]:
        if not isinstance(s, dict):
            continue
        bars = []
        for b in (s.get("bars") or []):
            if total >= MAX_BARS:
                problems.append(f"太長了，只收前 {MAX_BARS} 小節")
                break
            words = b.split() if isinstance(b, str) else [w for x in b for w in str(x).split()] if isinstance(b, list) else []
            bars.append(make_bar(words, bpb, unknown) if words else ["%"])
            total += 1
        if not bars:
            continue
        sec = {"name": _text(s.get("name"), 24), "bars": bars}
        if _num(s.get("repeat"), 2, 16, None):
            sec["repeat"] = int(s["repeat"])
        lyrics = [_text(x, 80) for x in (s.get("lyrics") or [])][:len(bars)] if isinstance(s.get("lyrics"), list) else []
        if any(lyrics):
            sec["lyrics"] = lyrics + [""] * (len(bars) - len(lyrics))
        chart["sections"].append(sec)
    marks = []
    image = obj.get("image") if isinstance(obj.get("image"), dict) else {}
    for mk in (image.get("marks") or [])[:MAX_MARKS]:
        if not isinstance(mk, dict):
            continue
        c = cell_word(_text(mk.get("chord"), 16))
        try:
            x, y = float(mk.get("x")), float(mk.get("y"))
        except (TypeError, ValueError):
            continue
        if c and c != "+" and 0 <= x <= 1 and 0 <= y <= 1:
            marks.append({"x": round(x, 4), "y": round(y, 4), "chord": split_beats(c)[0]})
    if marks:
        chart["image"] = {"marks": marks}
    if unknown:
        problems.append("看不懂的和弦先標成 ?：" + "、".join(dict.fromkeys(unknown)))
    if not chart["key"]:
        found = chart_chords(chart)
        if found:
            chart["key"] = key_name(detect_key(found))
    return chart, problems


def chart_input(text):
    """/chord_chart 收到的字 → (譜, 問題)：{ 開頭當 JSON，其他當和弦進行或譜。"""
    t = (text or "").strip()
    if re.match(r"^(?:```[a-zA-Z]*\s*)?\{", t):
        obj = read_json(t)
        if obj is None:
            return None, ["JSON 格式不對（括號、引號、逗號）"]
        return normalize_chart(obj)
    chart, unknown = parse_progression(t)
    chart, problems = normalize_chart(chart)
    if unknown:
        problems = [p for p in problems if not p.startswith("看不懂的和弦")] + \
            ["看不懂的字標成 ?：" + "、".join(dict.fromkeys(unknown))]
    return chart, problems


def chart_chords(chart):
    """譜裡依序出現的每個和弦（parse_chord 的 dict）；%、N.C.、? 跳過。照片上的標記不算。"""
    out = []
    for s in chart.get("sections") or []:
        for bar in s["bars"]:
            for tok in bar:
                ch = parse_chord(split_beats(tok)[0])
                if ch:
                    out.append(ch)
    return out


def chart_names(chart):
    """譜裡用到的和弦，照譜上的寫法、不重複、照出現順序。"""
    out = []
    for s in chart.get("sections") or []:
        for bar in s["bars"]:
            for tok in bar:
                name = split_beats(tok)[0]
                if parse_chord(name) and name not in out:
                    out.append(name)
    return out


def _tp_token(tok, n, key):
    name, beats = split_beats(tok)
    ch = parse_chord(name)
    if not ch:
        return tok
    return chord_name(ch, n, key) + (f":{beats}" if beats else "")


def transpose_chart(chart, n, key):
    """整份小節譜（和照片標記）移 n 個半音、照 key 的調號寫；回新的一份，原本的不動。"""
    out = dict(chart, key=key_name(key) if key else chart.get("key", ""))
    out["sections"] = [dict(s, bars=[[_tp_token(t, n, key) for t in bar] for bar in s["bars"]])
                       for s in chart.get("sections") or []]
    if chart.get("image"):
        out["image"] = {"marks": [dict(m, chord=_tp_token(m["chord"], n, key)) for m in chart["image"]["marks"]]}
    return out


def bar_text(bar):
    """一格的字：平均分拍＝和弦之間空兩格；有寫拍數＝每多一拍一個「.」（Am . . G）。"""
    if any(split_beats(t)[1] for t in bar):
        parts = []
        for t in bar:
            name, beats = split_beats(t)
            parts.append(name + " ." * ((beats or 1) - 1))
        return " ".join(parts)
    return "  ".join(bar)


def chart_head(chart):
    bits = [f"{chart['key']} 調" if chart.get("key") else "", chart.get("time") or "",
            f"Capo {chart['capo']}" if chart.get("capo") else "", f"♩={chart['tempo']}" if chart.get("tempo") else ""]
    name = " — ".join(x for x in (chart.get("title"), chart.get("artist")) if x)
    return (name + "｜" if name else "") + "・".join(b for b in bits if b)


def chart_text(chart, per_line=None):
    """等寬字的小節格子：每段一個標題、每行 per_line 小節、每格一樣寬；有歌詞的行下面列一行 ♪。"""
    per = per_line or chart.get("bars_per_line") or 4
    cells = [bar_text(b) for s in chart["sections"] for b in s["bars"]]
    w = max([6] + [len(c) for c in cells])
    lines = []
    for s in chart["sections"]:
        rep = f"×{s['repeat']}" if s.get("repeat") else ""
        if s.get("name") or rep:
            lines.append(f"【{s['name']}】{rep}" if s.get("name") else f"【{rep}】")
        lyr = s.get("lyrics") or []
        for i in range(0, len(s["bars"]), per):
            row = s["bars"][i:i + per]
            lines.append("|" + "|".join(" " + bar_text(b).ljust(w) + " " for b in row) + "|")
            words = [x for x in lyr[i:i + per] if x]
            if words:
                lines.append("  ♪ " + " / ".join(words))
    return "\n".join(lines)


# ── 工具 ─────────────────────────────────────────────────────────────────────

MAX_SHEET = 12000
CHUNK = 3500
USAGE_TP = ("用法：/chord_tp <移法> 再貼譜（譜放下一行）\n"
            "移法：+2、-3、升2、降1、G→A、到 A\n"
            "譜：和弦在上一行、歌詞在下一行，或 [C] 夾在歌詞裡都可以。\n"
            "同一份譜再換一種移法：/chord_tp +1（不用再貼）")


def empty():
    return {"last": "", "at": 0, "chart": None, "chart_at": 0}


def chunks(text, size=CHUNK):
    """太長的訊息切成幾則（照行切）。"""
    out, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = ""
        cur = (cur + "\n" + line) if cur else line
    if cur:
        out.append(cur)
    return out or [""]


def library():
    """面板用：12 個根音 × 每種和弦的按法（跟聊天裡同一份資料）。"""
    lib = {}
    for r in range(12):
        for q in QUAL_ORDER:
            vs = voicings(r, q)
            if vs:
                lib[f"{SHARP[r]}_{q}"] = [{"frets": list(v["frets"]), "fingers": list(v["fingers"]),
                                            "label": v["label"], "kind": v["kind"], "tip": v.get("tip", "")}
                                           for v in vs[:4]]
    slash = [{"name": n, "frets": list(f), "fingers": list(g), "tip": t} for n, f, g, t in SLASH]
    quals = [{"id": q, "suffix": QUAL[q][0], "label": QUAL[q][1], "tones": list(QUAL[q][2])} for q in QUAL_ORDER]
    return {"lib": lib, "slash": slash, "qualities": quals, "suffix_map": {k: v for k, v in SUFFIX_EXACT.items()},
            "patterns": [{"name": n, "seq": s} for n, s in PATTERNS]}


USAGE_CHART = ("用法：/chord_chart 換行貼和弦進行（或譜、或小節譜 JSON）\n"
               "・| C | G D/F# | Am | F |（一格一小節；一格裡兩個和弦＝各兩拍，Am . . G＝Am 三拍、G 一拍）\n"
               "・C G Am F（沒有小節線＝一個和弦一小節）\n"
               "・[主歌]、前奏：寫段落；%＝同上一小節；N.C.＝不彈；(x2)＝這行重複\n"
               "・第一行可以先寫移法：/chord_chart +2；不貼＝用上次的譜")


def install(app, pack):
    g = pack.group

    def last_sheet(user):
        return (pack.store.of(user).load(empty()) or {}).get("last", "")

    def remember(user, sheet):
        sheet = sheet[:MAX_SHEET]

        def put(d):
            d["last"], d["at"] = sheet, int(time.time())
        pack.store.of(user).update(put, empty())

    def send(api, chat_id, text):
        for part in chunks(text):
            api.send_message(chat_id, part)

    def show(api, chat_id, user, values):
        raw = values["text"].strip()
        tokens = [t for t in re.split(r"[\s,，、]+", raw) if t]
        chords = [c for c in (parse_chord(t) for t in tokens) if c]
        bad = [t for t in tokens if not parse_chord(t)]
        if not chords:
            key = parse_key(raw)
            if key and raw.endswith(("調", "小調", "大調")):
                send(api, chat_id, describe_key(key))
                return
            api.send_message(chat_id, f"❓ 看不懂「{raw}」是什麼和弦。例：/chord Am7、/chord D/F#、/chord Bb F Gm")
            return
        if len(chords) == 1:
            text = describe(chords[0])
        else:
            key = detect_key(chords)
            text = "🎸 " + " ".join(own_name(c) for c in chords) + "\n" + \
                "\n".join(describe_short(c, key) for c in chords) + "\n（按法：第6弦→第1弦，x＝不彈；單查一個看手指）"
        if bad:
            text += f"\n（看不懂的略過：{'、'.join(bad)}）"
        send(api, chat_id, text)

    def tp(api, chat_id, user, values):
        spec, sheet = split_spec(values["text"])
        if spec is None:
            api.send_message(chat_id, "❓ 第一行要寫怎麼移（+2、-3、G→A、到 A）。\n" + USAGE_TP)
            return
        if not sheet.strip():
            sheet = last_sheet(user)
            if not sheet:
                api.send_message(chat_id, "📄 還沒有譜。" + USAGE_TP)
                return
        else:
            remember(user, sheet)
        chords = chords_in(sheet)
        if not chords:
            api.send_message(chat_id, "❓ 這份譜裡找不到和弦。和弦要自己一行（C  G  Am  F），或寫成 [C] 夾在歌詞裡。")
            return
        src = detect_key(chords)
        parsed = parse_shift(spec, src)
        if parsed is None:
            api.send_message(chat_id, f"❓ 看不懂移法「{spec}」。\n" + USAGE_TP)
            return
        n, target = parsed
        dst = target or ((src[0] + n) % 12, src[1])
        out = rewrite(sheet, lambda ch: chord_name(ch, n, dst))
        names = unique_names(chords, n, dst)
        shown_src = ((dst[0] - n) % 12, dst[1])
        head = [f"🎸 移調：{key_name(shown_src)} → {key_name(dst)}（{shift_label(n)}）",
                f"和弦：{' '.join(names)}"]
        hard = [chord_name(ch, n, dst) for ch in _uniq(chords) if difficulty(ch, n) >= 3]
        if hard:
            head.append(f"要橫按：{'、'.join(hard)}")
            moved = [dict(c, root=(c["root"] + n) % 12, bass=None if c["bass"] is None else (c["bass"] + n) % 12)
                     for c in chords]
            best = capo_options(moved, dst)[0]
            if best[0]:
                head.append(f"💡 少按橫按：{capo_line(best, moved)}（/chord_capo 看整份）")
        if flattened(sheet):
            head.append(FLAT_NOTE)
        send(api, chat_id, "\n".join(head) + "\n\n" + out)

    def capo(api, chat_id, user, values):
        raw = (values.get("text") or "").strip("\n")
        first, _, rest = raw.partition("\n")
        head, _, tail = first.strip().partition(" ")
        force = None
        if re.fullmatch(r"(?:capo\s*)?\d{1,2}", first.strip(), re.I):
            force, raw = int(re.sub(r"\D", "", first)), rest
        elif re.fullmatch(r"\d{1,2}", head) and tail:
            force, raw = int(head), (tail + ("\n" + rest if rest else ""))
        key_only = parse_key(raw.strip()) if raw.strip() and "\n" not in raw.strip() and " " not in raw.strip() else None
        if key_only:
            chords = [ch for _, ch in key_chords(key_only)[:6]]
            sheet = ""
            src = key_only
        else:
            sheet = raw if raw.strip() else last_sheet(user)
            if raw.strip():
                remember(user, raw)
            chords = chords_in(sheet)
            if not chords:
                api.send_message(chat_id, "📄 給我譜或調：/chord_capo Bb、/chord_capo（用上次貼的譜）、"
                                          "/chord_capo 3（直接改成夾第 3 格的手型）")
                return
            src = detect_key(chords)
        if force is not None and not 0 <= force <= 11:
            api.send_message(chat_id, "❓ Capo 只能夾第 0～11 格。")
            return
        lines = [f"🎸 Capo 建議（原調 {key_name(src)}；夾越低越好、橫按越少越好）"]
        opts = capo_options(chords, src)
        for i, opt in enumerate(opts, 1):
            lines.append(f"{i}. {capo_line(opt, chords)}{' ⭐' if i == 1 else ''}")
        use = force if force is not None else opts[0][0]
        shape_key = ((src[0] - use) % 12, src[1])
        if force is not None and all(o[0] != force for o in opts):
            cost = capo_options(chords, src, limit=12)
            mine = next(o for o in cost if o[0] == force)
            lines.append(f"你選的：{capo_line(mine, chords)}")
        if sheet:
            lines += ["", f"── 夾 Capo {use}、照 {key_name(shape_key)} 調手型彈的譜 ──" if use else "── 原譜 ──",
                      rewrite(sheet, lambda ch: chord_name(ch, -use, shape_key))]
            if flattened(sheet):
                lines.append(FLAT_NOTE)
        send(api, chat_id, "\n".join(lines))

    def arp(api, chat_id, user, values):
        tokens = [t for t in re.split(r"[\s,，、]+", values["text"].strip()) if t]
        pattern = PATTERNS[0][1]
        if tokens and parse_pattern(tokens[-1]) and not parse_chord(tokens[-1]):
            pattern = parse_pattern(tokens.pop())
        chords = [c for c in (parse_chord(t) for t in tokens) if c]
        if not chords:
            api.send_message(chat_id, "❓ 例：/chord_arp Am F C G、/chord_arp C 2（第 2 種型態）、/chord_arp G T121\n"
                                      + "\n".join(f"{i}. {n} {s}" for i, (n, s) in enumerate(PATTERNS, 1)))
            return
        key = detect_key(chords)
        tab = arp_tab(chords, pattern, key)
        name = next((n for n, s in PATTERNS if s == pattern), "自訂")
        send(api, chat_id, f"🎸 分散和弦：{name} {'-'.join(pattern)}\n"
                           "T＝拇指彈最低那條弦（根音），1／2／3＝食指／中指／無名指彈第3／2／1弦\n" + tab)

    def key_(api, chat_id, user, values):
        key = parse_key(values["text"])
        if key is None:
            chords = chords_in(values["text"]) or [c for c in (parse_chord(t) for t in values["text"].split()) if c]
            if not chords:
                api.send_message(chat_id, "❓ 例：/chord_key G、/chord_key Am、/chord_key Bb")
                return
            key = detect_key(chords)
            api.send_message(chat_id, f"🔎 這些和弦看起來是 {key_name(key)} 調。")
        send(api, chat_id, describe_key(key))

    def saved(user):
        return pack.store.of(user).load(empty()) or {}

    def keep_chart(user, chart):
        def put(d):
            d["chart"], d["chart_at"] = chart, int(time.time())
        pack.store.of(user).update(put, empty())

    def chart_cmd(api, chat_id, user, values):
        raw = (values.get("text") or "").strip("\n")
        spec, body = split_spec(raw)
        if spec is None:
            body = raw
        chart, problems = None, []
        if not body.strip():
            d = saved(user)
            if d.get("chart") and d.get("chart_at", 0) >= d.get("at", 0):
                chart = d["chart"]                                  # 上次的小節譜比上次貼的譜新
            elif d.get("last"):
                chart, problems = chart_input(d["last"])
            else:
                api.send_message(chat_id, "📄 還沒有譜。" + USAGE_CHART)
                return
        else:
            chart, problems = chart_input(body)
            if chart and not body.lstrip().startswith(("{", "```")):
                remember(user, body)
        if not chart or not chart["sections"]:
            api.send_message(chat_id, "❓ " + ("；".join(problems) + "\n" if problems else "這裡面找不到和弦。\n") + USAGE_CHART)
            return
        chords = chart_chords(chart)
        head = []
        if spec is not None:
            src = parse_key(chart["key"]) or detect_key(chords)
            parsed = parse_shift(spec, src) if src else None
            if parsed is None:
                api.send_message(chat_id, f"❓ 看不懂移法「{spec}」。\n" + USAGE_CHART)
                return
            n, target = parsed
            dst = target or ((src[0] + n) % 12, src[1])
            chart = transpose_chart(chart, n, dst)
            head.append(f"移調：{key_name(((dst[0] - n) % 12, dst[1]))} → {key_name(dst)}（{shift_label(n)}）")
        keep_chart(user, chart)
        nbars = sum(len(s["bars"]) for s in chart["sections"])
        lines = [f"🎸 小節譜：{chart_head(chart)}（{nbars} 小節）"] + head
        if chords:
            lines.append("和弦：" + " ".join(chart_names(chart)))
        if chart.get("capo") and chords:                        # 譜上的 capo：寫的和弦＝夾了之後按的手型
            src = parse_key(chart["key"]) or detect_key(chords)
            sound = ((src[0] + chart["capo"]) % 12, src[1])
            lines.append(f"譜上夾 Capo {chart['capo']}：照上面的和弦按，聽起來是 {key_name(sound)} 調"
                         "（面板可以換成不夾、或夾別格的手型）")
        lines += ["", chart_text(chart), ""]
        if chart.get("notes"):
            lines.append("📝 " + chart["notes"])
        lines += ["⚠️ " + p for p in problems]
        lines.append("📋 完整的小節譜（和弦圖、移調、Capo、照片標記、列印）在網頁面板的「小節譜」分頁。")
        send(api, chat_id, "\n".join(lines))

    def data_route(user):
        """面板（panel.html）：按法資料＋這個人上次貼的譜＋上次的小節譜。只回這個人自己的。"""
        out = library()
        d = saved(user)
        out["last"] = d.get("last", "")
        out["chart"] = d.get("chart")
        return out

    kit.command(g, "show", show, "查和弦的按法", default=True, positional=1,
                usage="<和弦…>（例：Am7、D/F#、Bb F Gm）",
                fields=[Field("text", "和弦名稱，可以一次好幾個", required=True)])
    kit.command(g, "tp", tp, "貼一份譜直接移調", effect=WRITE, positional=1,
                usage="<移法> 換行貼譜（移法：+2、-3、G→A、到 A；不貼譜＝用上次那份）",
                fields=[Field("text", "移法，下一行起是譜", required=True)])
    kit.command(g, "capo", capo, "夾 Capo 用哪組手型最好彈", effect=WRITE, positional=1,
                usage="[格數] [譜或調]（例：Bb；3；不寫＝用上次貼的譜）",
                fields=[Field("text", "譜或調，可以先寫要夾第幾格")])
    kit.command(g, "arp", arp, "分散和弦的 TAB 譜", positional=1,
                usage="<和弦…> [型態 1～5 或 T1213121]",
                fields=[Field("text", "和弦，最後可以加型態", required=True)])
    kit.command(g, "key", key_, "一個調裡有哪些和弦", positional=1, usage="<調>（例：G、Am、Bb）",
                fields=[Field("text", "調，或一串和弦讓我猜調", required=True)])
    kit.command(g, "chart", chart_cmd, "整理成一格一小節的和弦譜", effect=WRITE, positional=1,
                usage="[移法] 換行貼進行、譜或小節譜 JSON（例：| C | G D/F# | Am | F |；不貼＝用上次的譜）",
                fields=[Field("text", "和弦進行、譜或小節譜 JSON，第一行可以先寫移法")])
    pack.web_route("data", data_route)


def _uniq(chords):
    out, seen = [], set()
    for ch in chords:
        k = (ch["root"], ch["quality"], ch["bass"])
        if k not in seen:
            seen.add(k)
            out.append(ch)
    return out
