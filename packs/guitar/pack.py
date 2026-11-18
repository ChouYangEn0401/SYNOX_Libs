"""🎸 吉他和弦：查和弦、貼一份譜直接移調、Capo 建議、分散和弦 TAB、一個調裡有哪些和弦、一格一小節的小節譜。

指令（群組 chord）：
  /chord <和弦…> [說明]   和弦圖（圖片）＋一行按法（Am7、D/F#、Bb，一次可以好幾個）；單一個可以翻其他按法
  /chord_info <和弦…>     完整說明：每根手指、組成音、提醒、其他按法
  /chord_alt <和弦> [N]   第 N 種按法的和弦圖
  /chord_easy <和弦…>     最好按（不用大橫按）的按法
  /chord_tones <和弦…>    組成音與音程、每條弦彈到的音
  /chord_all <根音>       一個根音的常用和弦（C、Cm、C7…）排成一張圖
  /chord_basic            入門先練的 8 個開放和弦
  /chord_change <A> <B>   從 A 換到 B 每根手指怎麼動
  /chord_strum [刷法]      節奏刷法
  /chord_prog <調> [進行]  一個調的常見和弦進行（附和弦圖）
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


# 拼出來的寫法（F major 7、Fmajor7、F minor、C dominant 7、B diminished、"Fmaj 7"、"F M7"）→ 譜上的寫法。
# 照順序換：長的先。只換在根音後面的尾巴，不碰根音。
SPELLED = (
    (r"half[\s-]*diminished\s*7?(?:th)?", "m7b5"), (r"diminished\s*7(?:th)?", "dim7"), (r"diminished", "dim"),
    (r"augmented", "aug"), (r"suspended\s*([24])", r"sus\1"), (r"suspended", "sus4"),
    (r"dominant\s*(7|9|11|13)(?:th)?", r"\1"), (r"dominant", "7"),
    (r"major\s*(7|9|11|13)(?:th)?", r"maj\1"), (r"major", ""),
    (r"minor\s*major\s*7(?:th)?", "mmaj7"), (r"minor\s*(\d+)(?:th)?", r"m\1"), (r"minor", "m"),
    (r"(\d+)th", r"\1"),
)
SUFFIX_WORD = re.compile(r"^(?:major|minor|maj|min|mi|dominant|diminished|half-?diminished|augmented|suspended|sus[24]?|"
                         r"add\d*|dim7?|aug|m|M|7th|9th|11th|13th|\d{1,2}|\(?[b#♭♯]?\d{1,2}\)?|M7|m7|maj7|m7b5|ø|°|Δ7?)$",
                         re.IGNORECASE)
ROOT_HEAD = re.compile(r"^[A-G](?:[#♯b♭])?")


def normalize_chord_text(text):
    """全形（Ｆ、＃、７）→ 半形；♯♭ → # b；拼出來的種類（major 7、minor、dominant…）→ 譜上的寫法；根音和尾巴中間的空白拿掉。
    'F major 7' → 'Fmaj7'、'Ｃ＃ｍ７' → 'C#m7'、'B♭ minor' → 'Bbm'、'D/F＃' → 'D/F#'。"""
    import unicodedata
    t = unicodedata.normalize("NFKC", str(text or "")).strip()
    t = t.replace("♯", "#").replace("♭", "b")
    m = ROOT_HEAD.match(t)
    if not m:
        return t
    root, rest = m.group(0), t[m.end():]
    bass = ""
    slash = re.search(r"\s*/\s*([A-G][#b]?)\s*$", rest)
    if slash:
        bass, rest = "/" + slash.group(1), rest[:slash.start()]
    tail = rest.strip()
    low = tail.lower()
    for pat, rep in SPELLED:
        low2 = re.sub(pat, rep, low)
        if low2 != low:
            low = low2
    if low != tail.lower():
        tail = low
    tail = re.sub(r"\s+", "", tail)
    return root + tail + bass


def parse_chord_loose(text):
    """parse_chord 再加上拼出來的寫法、全形字（normalize_chord_text）。不是和弦回 None（絕對不猜成別的和弦）。"""
    ch = parse_chord(text)
    if ch:
        return ch
    norm = normalize_chord_text(text)
    return parse_chord(norm) if norm and norm != (text or "").strip() else None


def group_chord_words(text):
    """一串字 → 一個一個和弦的寫法：'F major 7 Bb' → ['F major 7', 'Bb']；'D/F# Bb F' → 三個。
    根音開頭的字開一個新和弦，後面接的「尾巴字」（major、7、minor、sus4、dim…）併進去。"""
    import unicodedata
    raw = unicodedata.normalize("NFKC", str(text or "")).replace("♯", "#").replace("♭", "b")
    words = [w for w in re.split(r"[\s,，、]+", raw) if w]
    out = []
    for w in words:
        if out and not ROOT_HEAD.match(w):
            out[-1] += " " + w          # 尾巴字（major、7…）；看不懂的字也併進去，整個說看不懂（不會只剩根音被當成大三和弦）
        elif out and w.startswith("/") and re.fullmatch(r"/[A-G][#b]?", w):
            out[-1] += w
        else:
            out.append(w)
    return out


def suggest_names(text, limit=3):
    """看不懂的寫法 → 幾個接近的和弦名稱（給「你是不是要找…」）。"""
    import difflib
    norm = normalize_chord_text(text)
    m = ROOT_HEAD.match(norm)
    roots = [m.group(0)] if m else list(NEUTRAL)
    names = [r + QUAL[q][0] for r in roots for q in QUAL_ORDER]
    return difflib.get_close_matches(norm, names, n=limit, cutoff=0.3)


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
    "B_major": ((None, None, 4, 4, 4, 2), (None, None, 2, 3, 4, 1),
                "中指、無名指、小指壓第4格，食指壓第1弦第2格；最低音是 F#（五度），不是根音 B。"),
    "A#_major": ((None, None, 3, 3, 3, 1), (None, None, 2, 3, 4, 1),
                 "跟簡易 B 同一個手型，往琴頭移一格；最低音是 F（五度），不是根音 Bb。"),
    "F#_minor": ((None, None, 4, 2, 2, 2), (None, None, 3, 1, 1, 1), "食指橫壓第1～3弦第2格，無名指壓第4弦第4格。"),
    "F_minor": ((None, None, 3, 1, 1, 1), (None, None, 3, 1, 1, 1), "跟簡易 F#m 同一個手型，往琴頭移一格。"),
    "G_minor": ((None, None, 5, 3, 3, 3), (None, None, 3, 1, 1, 1), "跟簡易 F#m 同一個手型，往琴身移一格。"),
    "C_minor": ((None, None, 5, 5, 4, 3), (None, None, 4, 3, 2, 1),
                "跟簡易 Bm 同一個手型，往琴身移一格；最低音是 G（五度），不是根音 C。"),
}
# 同一個和弦的第二種簡易按法（「吉他和弦全書」的簡易和弦表；最低音不是根音的，標籤寫低音）
EASY_MORE = {
    "B_minor": ((None, None, 0, 4, 3, 2), (None, None, 0, 3, 2, 1), "簡易・空弦版（低音 D）",
                "第4弦改用空弦，只要三根手指；代價是最低音變成 D。"),
    "G_major": ((None, None, 0, 0, 0, 3), (None, None, 0, 0, 0, 3), "一指簡易（低音 D）",
                "只要一根手指的 G，適合剛入門；第5、6弦不要彈，最低音變成 D。"),
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
    # add9／madd9 的 A 型（x 0 2 4 2 0）要跨 5 格，移到別的把位按不到：不收（開放的 Aadd9 在 OPEN；其他的交給 search()，
    # 一律四格以內）
    "9": ((None, 0, -1, 0, 0, 0), (None, 2, 1, 3, 3, 3)),
    "m9": ((None, 0, -2, 0, 0, 0), (None, 2, 1, 3, 3, 3)),
    "maj9": ((None, 0, -1, 1, 0, None), (None, 2, 1, 4, 3, None)),
    "mmaj7": ((None, 0, 2, 1, 1, 0), (None, 1, 4, 2, 3, 1)),
    "dim": ((None, 0, 1, 2, 1, None), (None, 1, 2, 4, 3, None)),
    # dim7：最常教的 x3424x 型（中指根音、無名指、食指、小指）；m7b5：x3434x 型的 1-3-2-4
    "dim7": ((None, 0, 1, -1, 1, None), (None, 2, 3, 1, 4, None)),
    "m7b5": ((None, 0, 1, 0, 1, None), (None, 1, 3, 2, 4, None)),
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


def _spread(order, low, first):
    """照格數由低到高給手指（從 first 號開始）：一根手指管一格（離最低那格 3 格＝小指）；排不下時容許相鄰手指差 2 格；
    還排不下（要五根手指、或兩指之間要撐 3 格以上）回 None。"""
    for relax in (0, 1):
        out, prev = [], first - 1
        for f in order:
            k = max(prev + 1, f - low + 1 - relax)
            out.append(k)
            prev = k
        if out and out[-1] <= 4:
            return out
    return None


def fresh_fingers(frets):
    """依格數由低到高給 1～4 指（手指跟著格數走：差 3 格的用小指，不用中指硬撐）；超過 4 個音時，最低那格用食指橫按。
    排不出來回 None。"""
    notes = sorted(((f, i) for i, f in enumerate(frets) if f is not None and f > 0))
    fingers = [None if f is None else 0 for f in frets]
    if not notes:
        return tuple(fingers)
    low = notes[0][0]
    if len(notes) <= 4:
        got = _spread([f for f, _ in notes], low, 1)
        plain = None
        if got is not None:
            plain = list(fingers)
            for k, (_, i) in zip(got, notes):
                plain[i] = k
            plain = tuple(plain)
            if not stretched(frets, plain):
                return plain
        barre = _barre_fingers(frets, notes, low, fingers)        # 撐太開：最低那格改食指橫按試試
        return barre if barre is not None and (plain is None or not stretched(frets, barre)) else plain
    return _barre_fingers(frets, notes, low, fingers)


def stretched(frets, fingers):
    """兩根手指之間要撐的格數比手指差多 2 以上（例如食指第 1 格、中指第 4 格）＝撐太開。"""
    at = {}
    for f, k in zip(frets, fingers):
        if f and k:
            at.setdefault(k, []).append(f)
    return any(max(at[b]) - min(at[a]) > (b - a) + 1 for a in at for b in at if a < b)


def _barre_fingers(frets, notes, low, fingers):
    fingers = list(fingers)
    lows = [i for f, i in notes if f == low]
    rest = sorted((i for f, i in notes if f > low), key=lambda i: (frets[i], i))
    if len(lows) < 2 or len(rest) > 3 or any(frets[i] in (0, None) or frets[i] < low
                                              for i in range(min(lows), max(lows) + 1)):
        return None
    got = _spread([frets[i] for i in rest], low, 2)
    if got is None:
        return None
    for i in lows:
        fingers[i] = 1
    for k, i in zip(got, rest):
        fingers[i] = k
    return tuple(fingers)


def span_of(frets):
    """按的格子從最低到最高跨幾格（1＝同一格）；沒按的回 0。"""
    pressed = [f for f in frets if f]
    return max(pressed) - min(pressed) + 1 if pressed else 0


STRETCH_TIP = "要撐開 4 格（手小的可以只彈上面幾條弦，或換別的按法）"


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
            if r == 0 or 0 in frets:                       # 滑到第 0 格：有空弦，不能照範本的橫按手指
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
    if k in EASY_MORE:
        frets, fingers, label, tip = EASY_MORE[k]
        out.append({"frets": frets, "fingers": fingers, "label": label, "kind": "easy", "tip": tip})
    if not out:                                     # 範本都按不到（例如 add9 的高把位）：在整個指板上找四格以內的
        for v in search(root, quality, limit=2):
            barre = sum(1 for f in v["fingers"] if f == 1) >= 2
            out.append(dict(v, label="自動排的按法", kind="barre" if barre else "open",
                            tip="照規則自動排出來的：音一定對，手指是建議。"))
    seen, uniq = set(), []
    for v in out:
        if span_of(v["frets"]) >= 4 and STRETCH_TIP not in (v.get("tip") or ""):
            v = dict(v, tip=((v.get("tip") or "") + ("；" if v.get("tip") else "") + STRETCH_TIP))
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
                        score = (1.5 if span_of(frets) >= 4 else 0) + between * 1.5 - frets.count(0) * 0.3 + n_fing + (2 if barre else 0) + (min(pressed) * 0.6 if pressed and min(pressed) > 3 else 0) \
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
    return f"{name}：{frets_text(v['frets'])}（{v['label']}）{extra}{approx_note(ch)}"


# ── 組成音與音程、最好按的按法、一個根音的全部、入門和弦、和弦進行 ─────────────────────────

INTERVAL_NAME = {0: "根音", 1: "小二度", 2: "大二度（九音）", 3: "小三度", 4: "大三度", 5: "完全四度", 6: "減五度",
                 7: "完全五度", 8: "增五度", 9: "大六度", 10: "小七度", 11: "大七度"}


def interval_name(quality, iv):
    """半音數 → 音程名稱；減七和弦的 9 個半音是「減七度」，不是大六度。"""
    if quality == "dim7" and iv == 9:
        return "減七度"
    return INTERVAL_NAME[iv % 12]


def tones_text(ch, key=None):
    """組成音＋音程，和第一個按法每條弦彈到的音（第6弦→第1弦）。"""
    root, q, bass = ch["root"], ch["quality"], ch["bass"]
    lines = [f"🎵 {own_name(ch)}（{QUAL[q][1]}）{approx_note(ch)}"]
    lines += [f"・{spell(root + iv, key)}：{interval_name(q, iv)}" for iv in QUAL[q][2]]
    if bass is not None and (bass - root) % 12 not in QUAL[q][2]:
        lines.append(f"・{spell(bass, key)}：低音（不在和弦裡，外加的）")
    elif bass is not None:
        lines.append(f"・低音彈 {spell(bass, key)}（和弦裡的音，轉位）")
    vs = voicings(root, q, bass)
    if vs:
        v = vs[0]
        per = [f"{STRING_NAME[i]}（{6 - i} 弦）" + ("不彈" if f is None else spell(STRING_PC[i] + f, key))
               for i, f in enumerate(v["frets"])]
        lines.append(f"按 {frets_text(v['frets'])} 時每條弦：" + "、".join(per))
    return "\n".join(lines)


def easiest(ch):
    """最好按的那個按法：開放 → 簡易 → 有空弦的斜線 → 第一個。"""
    vs = voicings(ch["root"], ch["quality"], ch["bass"])
    if not vs:
        return None
    for want in ("open", "easy"):
        got = next((v for v in vs if v["kind"] == want), None)
        if got:
            return got
    return next((v for v in vs if v["kind"] == "slash" and 0 in v["frets"]), vs[0])


ALL_QUALITIES = ("major", "minor", "7", "m7", "maj7", "sus2", "sus4", "add9")
BASIC = ("C", "G", "Am", "Em", "D", "A", "E", "Dm")      # 入門先練的開放和弦（「吉他和弦全書」入門和弦）


def find_progression(key, pick):
    """調＋（編號或名稱的一部分，空的＝第一個）→ (名稱, [和弦])；找不到回 None。"""
    tonic, minor = key
    progs = PROGS_MINOR if minor else PROGS_MAJOR
    pick = (pick or "").strip()
    i = 0
    if pick:
        if pick.isdigit() and 1 <= int(pick) <= len(progs):
            i = int(pick) - 1
        else:
            i = next((k for k, (name, _) in enumerate(progs) if pick.lower() in name.lower()), None)
            if i is None:
                return None
    name, seq = progs[i]
    chords = key_chords(key)
    out = []
    for d in seq:
        if d == 7:                                                   # 小調的 V：借和聲小調的大三和弦
            out.append({"root": (tonic + 7) % 12, "suffix": "", "quality": "major", "bass": None, "approx": False})
        else:
            out.append(chords[d][1])
    return name, out


def progression_list(key):
    progs = PROGS_MINOR if key[1] else PROGS_MAJOR
    return "\n".join(f"{i}. {name}" for i, (name, _) in enumerate(progs, 1))


# ── 和弦圖（SVG＋PNG，同一份圖元畫兩次） ───────────────────────────────────────
# 樣子照「吉他和弦全書」的 renderChordSVG：直的六條弦（左＝第6弦）、橫的格線、琴枕粗線或「5fr」、上面 × 不彈／○ 空弦、
# 圓點（裡面是手指編號）、橫按畫成一條圓頭的棒子、根音的點是綠色。資料一律來自 voicings()（跟文字的按法是同一份）。
# 不靠任何外部套件：SVG 是字串；PNG 用自己的小點陣字型＋zlib 壓縮（Telegram／Discord 不收 SVG 照片）。

FRETS_SHOWN = 5
CELL_W, CELL_H = 136, 200
GRID_X, GRID_Y, STRING_GAP, FRET_GAP = 33, 60, 17, 21
INK, INK_SOFT, LINE, DOT, ROOT_DOT, PAPER, CARD = "#26221B", "#736B5A", "#B7A98A", "#8A4A22", "#3C6358", "#FFFFFF", "#FBF8F1"
PNG_SCALE = 2
MAX_DIAGRAMS = 8


def find_barres(frets, fingers):
    """橫按：同一根手指（通常是食指）在同一格按了兩條以上的弦，而且中間的弦都有按、都不比那一格低。
    回 [(格, 從哪條弦 index, 到哪條弦 index, 手指)]（index：0＝第6弦）。沒給手指＝用 fresh_fingers 排。"""
    if not fingers or not any(fingers):
        fingers = fresh_fingers(frets) or [None] * 6
    out = []
    for k in (1, 2, 3, 4):
        idx = [i for i in range(6) if fingers[i] == k and frets[i]]
        if len(idx) < 2 or len({frets[i] for i in idx}) != 1:
            continue
        f, lo, hi = frets[idx[0]], min(idx), max(idx)
        if all(frets[j] is not None and frets[j] >= f for j in range(lo, hi + 1)):
            out.append((f, lo, hi, k))
    return out


def base_fret(frets):
    """0＝從琴枕開始畫；不然是圖上第一格的格數（標「5fr」）。按的最高那格在第 5 格以內就從琴枕開始。"""
    pressed = [f for f in frets if f]
    if not pressed or max(pressed) <= FRETS_SHOWN:
        return 0
    return min(pressed)


def diagram_shapes(name, frets, fingers, root=None, x0=0.0, y0=0.0, footer=None):
    """一張和弦圖的圖元（座標以 CELL_W×CELL_H 為一格，x0／y0 是這格的左上角）。
    ("rect", x, y, w, h, 顏色)、("seg", x1, y1, x2, y2, 線寬, 顏色)（圓頭線段）、("circle", cx, cy, r, 填色或 None, 邊色, 邊寬)、
    ("text", x, y（基線）, 字, 字級, 顏色, 對齊 start／middle／end, 粗體)。"""
    if not fingers or not any(fingers):
        fingers = fresh_fingers(frets) or [None] * 6
    base = base_fret(frets)
    gx, gy = x0 + GRID_X, y0 + GRID_Y
    sx = [gx + i * STRING_GAP for i in range(6)]
    bottom = gy + FRETS_SHOWN * FRET_GAP
    shapes = [("rect", x0 + 3, y0 + 3, CELL_W - 6, CELL_H - 6, CARD),
              ("text", x0 + CELL_W / 2, y0 + 27, name, 18, INK, "middle", True)]
    for i in range(6):
        shapes.append(("rect", sx[i] - 0.6, gy, 1.2, bottom - gy, LINE))
    for k in range(FRETS_SHOWN + 1):
        shapes.append(("rect", gx, gy + k * FRET_GAP - 0.5, sx[5] - gx, 1.0, LINE))
    if base == 0:
        shapes.append(("rect", gx - 0.6, gy - 4, sx[5] - gx + 1.2, 4.5, INK))
    else:
        shapes.append(("text", gx - 7, gy + FRET_GAP * 0.5 + 4, f"{base}fr", 11, INK_SOFT, "end", False))

    def row_y(f):
        r = f if base == 0 else f - base + 1
        return gy + (r - 0.5) * FRET_GAP

    def is_root(i, f):
        return root is not None and f is not None and (STRING_PC[i] + f) % 12 == root % 12

    barres = find_barres(frets, fingers)
    covered = set()
    for f, lo, hi, k in barres:
        y = row_y(f)
        shapes.append(("seg", sx[lo], y, sx[hi], y, 14, DOT))
        shapes.append(("text", (sx[lo] + sx[hi]) / 2, y + 3.6, str(k), 10, "#FFFFFF", "middle", True))
        covered |= {i for i in range(lo, hi + 1) if frets[i] == f and fingers[i] == k}
    mark_y = gy - 13
    for i in range(6):
        f = frets[i]
        if f is None:
            d = 3.6
            shapes.append(("seg", sx[i] - d, mark_y - d, sx[i] + d, mark_y + d, 1.6, INK_SOFT))
            shapes.append(("seg", sx[i] - d, mark_y + d, sx[i] + d, mark_y - d, 1.6, INK_SOFT))
        elif f == 0:
            if is_root(i, 0):
                shapes.append(("circle", sx[i], mark_y, 4.2, ROOT_DOT, ROOT_DOT, 1.4))
            else:
                shapes.append(("circle", sx[i], mark_y, 4.2, None, INK_SOFT, 1.4))
        elif i in covered:
            if is_root(i, f):
                shapes.append(("circle", sx[i], row_y(f), 7.6, None, ROOT_DOT, 2.2))
        else:
            y = row_y(f)
            shapes.append(("circle", sx[i], y, 7.4, ROOT_DOT if is_root(i, f) else DOT, None, 0))
            if fingers[i]:
                shapes.append(("text", sx[i], y + 3.6, str(fingers[i]), 10, "#FFFFFF", "middle", True))
    shapes.append(("text", x0 + CELL_W / 2, bottom + 22, footer if footer is not None else frets_text(frets), 12,
                   INK_SOFT, "middle", False))
    return shapes


def sheet_shapes(cells, per_row=6):
    """好幾張圖排成一張（一列最多 per_row 張）。cells：[{"name", "frets", "fingers", "root", "footer"}]。回 (寬, 高, 圖元)。"""
    cells = list(cells)[:MAX_DIAGRAMS * 2]
    cols = max(1, min(per_row, len(cells)))
    rows = max(1, -(-len(cells) // cols))
    w, h = cols * CELL_W, rows * CELL_H
    shapes = [("rect", 0, 0, w, h, PAPER)]
    for n, c in enumerate(cells):
        shapes += diagram_shapes(c["name"], c["frets"], c.get("fingers"), c.get("root"),
                                 (n % cols) * CELL_W, (n // cols) * CELL_H, c.get("footer"))
    return w, h, shapes


def _xml(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _n(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def to_svg(w, h, shapes, title=""):
    """圖元 → SVG（只用 SYNOX 白名單裡的元素與屬性：svg、title、rect、line、circle、text）。"""
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w * 2}" height="{h * 2}" '
           f'role="img" aria-label="{_xml(title)}">', f"<title>{_xml(title)}</title>"]
    for s in shapes:
        kind = s[0]
        if kind == "rect":
            _, x, y, rw, rh, col = s
            out.append(f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(rw)}" height="{_n(rh)}" fill="{col}"/>')
        elif kind == "seg":
            _, x1, y1, x2, y2, lw, col = s
            out.append(f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" stroke="{col}" '
                       f'stroke-width="{_n(lw)}" stroke-linecap="round"/>')
        elif kind == "circle":
            _, cx, cy, r, fill, stroke, sw = s
            out.append(f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}" fill="{fill or "none"}"'
                       + (f' stroke="{stroke}" stroke-width="{_n(sw)}"' if stroke and sw else "") + "/>")
        elif kind == "text":
            _, x, y, text, size, col, anchor, bold = s
            out.append(f'<text x="{_n(x)}" y="{_n(y)}" font-size="{_n(size)}" fill="{col}" text-anchor="{anchor}"'
                       f' font-family="Segoe UI, Noto Sans TC, Arial, sans-serif"'
                       + (' font-weight="700"' if bold else "") + f">{_xml(text)}</text>")
    out.append("</svg>")
    return "".join(out)


# 5×7 點陣字（PNG 用；SVG 用瀏覽器的字型）。和弦名稱、格數、手指編號用得到的字就好，沒有的畫成「?」。
FONT = {
    '0': ".###.|#...#|#..##|#.#.#|##..#|#...#|.###.", '1': "..#..|.##..|..#..|..#..|..#..|..#..|.###.",
    '2': ".###.|#...#|....#|...#.|..#..|.#...|#####", '3': "#####|...#.|..#..|...#.|....#|#...#|.###.",
    '4': "...#.|..##.|.#.#.|#..#.|#####|...#.|...#.", '5': "#####|#....|####.|....#|....#|#...#|.###.",
    '6': "..##.|.#...|#....|####.|#...#|#...#|.###.", '7': "#####|....#|...#.|..#..|.#...|.#...|.#...",
    '8': ".###.|#...#|#...#|.###.|#...#|#...#|.###.", '9': ".###.|#...#|#...#|.####|....#|...#.|.##..",
    'A': ".###.|#...#|#...#|#####|#...#|#...#|#...#", 'B': "####.|#...#|#...#|####.|#...#|#...#|####.",
    'C': ".###.|#...#|#....|#....|#....|#...#|.###.", 'D': "####.|#...#|#...#|#...#|#...#|#...#|####.",
    'E': "#####|#....|#....|####.|#....|#....|#####", 'F': "#####|#....|#....|####.|#....|#....|#....",
    'G': ".###.|#...#|#....|#.###|#...#|#...#|.####", 'M': "#...#|##.##|#.#.#|#.#.#|#...#|#...#|#...#",
    'a': ".....|.....|.###.|....#|.####|#...#|.####", 'b': "#....|#....|#.##.|##..#|#...#|#...#|####.",
    'd': "....#|....#|.##.#|#..##|#...#|#...#|.####", 'f': "..##.|.#..#|.#...|###..|.#...|.#...|.#...",
    'g': ".....|.####|#...#|#...#|.####|....#|.###.", 'i': "..#..|.....|.##..|..#..|..#..|..#..|.###.",
    'j': "...#.|.....|..##.|...#.|...#.|#..#.|.##..", 'm': ".....|.....|##.#.|#.#.#|#.#.#|#...#|#...#",
    'n': ".....|.....|#.##.|##..#|#...#|#...#|#...#", 'o': ".....|.....|.###.|#...#|#...#|#...#|.###.",
    'r': ".....|.....|#.##.|##..#|#....|#....|#....", 's': ".....|.....|.####|#....|.###.|....#|####.",
    'u': ".....|.....|#...#|#...#|#...#|#..##|.##.#", 'x': ".....|.....|#...#|.#.#.|..#..|.#.#.|#...#",
    '#': ".#.#.|.#.#.|#####|.#.#.|#####|.#.#.|.#.#.", '/': "....#|....#|...#.|..#..|.#...|#....|#....",
    '(': "...#.|..#..|.#...|.#...|.#...|..#..|...#.", ')': ".#...|..#..|...#.|...#.|...#.|..#..|.#...",
    '+': ".....|..#..|..#..|#####|..#..|..#..|.....", '-': ".....|.....|.....|#####|.....|.....|.....",
    '.': ".....|.....|.....|.....|.....|.##..|.##..", "\u00b0": ".##..|#..#.|#..#.|.##..|.....|.....|.....",
    "\u00f8": ".....|....#|.###.|#.#.#|#.#.#|.###.|#....", "\u0394": ".....|..#..|..#..|.#.#.|.#.#.|#...#|#####",
    '?': ".###.|#...#|....#|...#.|..#..|.....|..#..", ' ': ".....|.....|.....|.....|.....|.....|.....",
}


def _glyph(ch):
    return FONT.get(ch, FONT["?"]).replace("|", "")


class Canvas:
    """很小的點陣畫布（RGB）：長方形、圓頭線段、圓（實心或空心）、點陣字。邊緣用 2×2 取樣做反鋸齒。"""

    def __init__(self, w, h, bg=PAPER):
        self.w, self.h = int(w), int(h)
        self.px = bytearray(_rgb(bg) * (self.w * self.h))

    def _blend(self, x, y, rgb, a):
        if a <= 0 or not (0 <= x < self.w and 0 <= y < self.h):
            return
        i = (y * self.w + x) * 3
        if a >= 1:
            self.px[i:i + 3] = bytes(rgb)
            return
        for c in range(3):
            self.px[i + c] = int(self.px[i + c] * (1 - a) + rgb[c] * a + 0.5)

    def rect(self, x, y, w, h, col):
        rgb = _rgb(col)
        x1, y1 = x + w, y + h
        full_lo, full_hi = max(0, -int(-x)), min(self.w, int(x1))         # 整格都蓋到的那幾欄：一次整段填
        solid = bytes(rgb) * max(0, full_hi - full_lo)
        for py in range(max(0, int(y)), min(self.h, int(y1) + 1)):
            cy = max(0.0, min(py + 1, y1) - max(py, y))
            if cy <= 0:
                continue
            if cy >= 1 and solid:
                i = (py * self.w + full_lo) * 3
                self.px[i:i + len(solid)] = solid
                cols = [c for c in (int(x), full_hi) if c < full_lo or c >= full_hi]
            else:
                cols = range(max(0, int(x)), min(self.w, int(x1) + 1))
            for px in cols:
                if 0 <= px < self.w:
                    cx = max(0.0, min(px + 1, x1) - max(px, x))
                    self._blend(px, py, rgb, cx * cy)

    def _shape(self, x0, y0, x1, y1, inside, col):
        rgb = _rgb(col)
        subs = ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))
        for py in range(max(0, int(y0)), min(self.h, int(y1) + 2)):
            for px in range(max(0, int(x0)), min(self.w, int(x1) + 2)):
                n = sum(1 for dx, dy in subs if inside(px + dx, py + dy))
                if n:
                    self._blend(px, py, rgb, n / 4)

    def seg(self, x1, y1, x2, y2, lw, col):
        r = lw / 2
        dx, dy = x2 - x1, y2 - y1
        ll = dx * dx + dy * dy or 1e-9

        def inside(x, y):
            t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / ll))
            ex, ey = x - (x1 + t * dx), y - (y1 + t * dy)
            return ex * ex + ey * ey <= r * r
        self._shape(min(x1, x2) - r, min(y1, y2) - r, max(x1, x2) + r, max(y1, y2) + r, inside, col)

    def circle(self, cx, cy, r, fill, stroke, sw):
        if fill:
            self._shape(cx - r, cy - r, cx + r, cy + r,
                        lambda x, y: (x - cx) ** 2 + (y - cy) ** 2 <= r * r, fill)
        if stroke and sw:
            ro, ri = r + sw / 2, max(0.0, r - sw / 2)
            self._shape(cx - ro, cy - ro, cx + ro, cy + ro,
                        lambda x, y: ri * ri <= (x - cx) ** 2 + (y - cy) ** 2 <= ro * ro, stroke)

    def text(self, x, y, text, size, col, anchor, bold):
        """點陣字：字高約 size 的 0.75；基線在 y。"""
        unit = max(1, int(round(size * 0.75 / 7)))
        width = len(text) * 6 * unit - unit
        left = x - (width / 2 if anchor == "middle" else width if anchor == "end" else 0)
        top = y - 7 * unit
        rgb_col = col
        for n, ch in enumerate(text):
            bits = _glyph(ch)
            gx = left + n * 6 * unit
            for row in range(7):
                for c in range(5):
                    if bits[row * 5 + c] == "#":
                        self.rect(gx + c * unit, top + row * unit, unit + (unit * 0.25 if bold else 0), unit, rgb_col)

    def png(self):
        import struct
        import zlib
        raw = b"".join(b"\x00" + bytes(self.px[y * self.w * 3:(y + 1) * self.w * 3]) for y in range(self.h))

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _rgb(col):
    col = col.lstrip("#")
    return (int(col[0:2], 16), int(col[2:4], 16), int(col[4:6], 16))


def to_png(w, h, shapes, scale=PNG_SCALE):
    cv = Canvas(w * scale, h * scale)
    for s in shapes:
        kind = s[0]
        if kind == "rect":
            _, x, y, rw, rh, col = s
            cv.rect(x * scale, y * scale, rw * scale, rh * scale, col)
        elif kind == "seg":
            _, x1, y1, x2, y2, lw, col = s
            cv.seg(x1 * scale, y1 * scale, x2 * scale, y2 * scale, lw * scale, col)
        elif kind == "circle":
            _, cx, cy, r, fill, stroke, sw = s
            cv.circle(cx * scale, cy * scale, r * scale, fill, stroke, sw * scale)
        elif kind == "text":
            _, x, y, text, size, col, anchor, bold = s
            cv.text(x * scale, y * scale, text, size * scale, col, anchor, bold)
    return cv.png()


def diagram_cell(ch, v, footer=None):
    """一個和弦＋它的一個按法 → sheet_shapes 的一格。名稱照使用者寫的（own_name）。"""
    return {"name": own_name(ch), "frets": tuple(v["frets"]), "fingers": tuple(v["fingers"] or ()),
            "root": ch["root"], "footer": footer}


def render_cells(cells, title=""):
    """好幾格 → {"svg", "png", "alt"}（kit.send_image 的一張圖）。"""
    w, h, shapes = sheet_shapes(cells)
    return {"svg": to_svg(w, h, shapes, title), "png": to_png(w, h, shapes), "alt": title[:200]}


def _chunks_of(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


# ── 回覆：圖說、翻頁按鈕 ──────────────────────────────────────────────────────

CB_PREFIX = "gtr:"


def resolve_chords(text):
    """使用者打的一串 → (和弦清單, 看不懂的說明)。拼出來的寫法（F major 7）、全形字都收；看不懂的不會被猜成別的和弦，
    而是列在第二個清單：「看不懂「Fx7」（是不是：F7、Fm7？）」。"""
    chords, bad = [], []
    for word in group_chord_words(text):
        ch = parse_chord_loose(word)
        if ch:
            chords.append(ch)
        else:
            sug = suggest_names(word)
            bad.append(f"看不懂「{word}」" + (f"（是不是：{'、'.join(sug)}？）" if sug else ""))
    return chords, bad


def approx_note(ch):
    if not ch.get("approx"):
        return ""
    return f"（{ch['suffix']} 先用 {std_name(ch['root'], ch['quality'], ch['bass'])} 的按法；延伸音可以省略）"


def page_caption(ch, vs, i):
    """一個按法的圖說：名稱、第幾個／共幾個、標籤、一行按法、提醒。"""
    v = vs[i]
    lines = [f"🎸 {own_name(ch)}　{i + 1}／{len(vs)} {v['label']}",
             f"按法：{frets_text(v['frets'])}（第6弦→第1弦，x＝不彈）", approx_note(ch)]
    if v.get("tip"):
        lines.append("💡 " + v["tip"])
    return "\n".join(x for x in lines if x)


def pager_markup(ch, i, n):
    """‹ 上一個／下一個 › 換按法（同一則訊息換圖）＋📖 說明。"""
    name = own_name(ch)
    rows = []
    if n > 1:
        rows.append([{"text": "‹ 上一個", "callback_data": f"{CB_PREFIX}v:{(i - 1) % n}:{name}"},
                     {"text": f"{i + 1}／{n}", "callback_data": f"{CB_PREFIX}v:{i}:{name}"},
                     {"text": "下一個 ›", "callback_data": f"{CB_PREFIX}v:{(i + 1) % n}:{name}"}])
    rows.append([{"text": "📖 說明", "callback_data": f"{CB_PREFIX}i:{name}"}])
    return {"inline_keyboard": rows}


def multi_markup(chords):
    """好幾個和弦：每個有別的按法的，一顆「X 換按法」（另開一則可以翻頁的）；最後一顆「📖 說明」。"""
    buttons = []
    for ch in chords[:MAX_DIAGRAMS]:
        if len(voicings(ch["root"], ch["quality"], ch["bass"])) > 1:
            buttons.append({"text": f"{own_name(ch)} 換按法", "callback_data": f"{CB_PREFIX}o:{own_name(ch)}"})
    rows = _chunks_of(buttons, 3)
    names = " ".join(own_name(c) for c in chords[:MAX_DIAGRAMS])
    rows.append([{"text": "📖 說明", "callback_data": f"{CB_PREFIX}i:{names}"[:200]}])
    return {"inline_keyboard": rows}


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
    head = "    " + "".join(" " + n.ljust(w) for n, w in names)
    body = [f"{STRING_NAME[5 - row]}({row + 1})" + "".join(rows[row]) + "|" for row in range(6)]
    return "\n".join([head.rstrip()] + body)


ARP_LEGEND = ("T：拇指，彈根音（最低那條弦）", "1：食指，第 3 弦（G）", "2：中指，第 2 弦（B）", "3：無名指，第 1 弦（e）",
              "e（1 弦）＝最細的弦，E（6 弦）＝最粗的弦（TAB 左邊寫成 e(1)…E(6)）",
              "線上的數字＝左手按第幾格，0＝空弦")


def arp_reply(chords, pattern):
    """分散和弦的回覆：(Html, 純文字)。TAB 放在等寬區塊（richtext.Html 的 <pre>：Telegram 等寬、網頁畫成程式碼區塊），
    說明一行一個記號。"""
    from isdtelekit.core.richtext import Html, esc
    tab = arp_tab(chords, pattern, detect_key(chords))
    name = next((n for n, s in PATTERNS if s == pattern), "自訂")
    head = f"🎸 分散和弦：{name} {'-'.join(pattern)}"
    return (Html(f"<b>{esc(head)}</b>\n<pre>{esc(tab)}</pre>\n" + "\n".join(esc(x) for x in ARP_LEGEND)),
            head + "\n```\n" + tab + "\n```\n" + "\n".join(ARP_LEGEND))


# ── 換和弦（「吉他和弦全書」的換和弦分析器） ──────────────────────────────────

FINGER_NAME = ("", "食指", "中指", "無名指", "小指")


def _finger_positions(frets, fingers):
    out = {}
    for s, f in enumerate(frets):
        if f and fingers[s]:
            out.setdefault(fingers[s], []).append((s, f))
    return out


def _pos_text(lst):
    if len(lst) > 1:
        ss = [6 - s for s, _ in lst]
        return f"第{min(ss)}～{max(ss)}弦第{lst[0][1]}格（橫按）"
    return f"第{6 - lst[0][0]}弦第{lst[0][1]}格"


def analyze_change(a_name, a, b_name, b):
    """從 a 換到 b（a、b 是 voicings() 的按法）：每根手指不動／滑過去／移動／新放下／放開，整個手型平移，難度，低音先到位。"""
    fa = a["fingers"] or fresh_fingers(a["frets"])
    fb = b["fingers"] or fresh_fingers(b["frets"])
    pa, pb = _finger_positions(a["frets"], fa), _finger_positions(b["frets"], fb)
    rows = []
    for k in (1, 2, 3, 4):
        x, y = pa.get(k), pb.get(k)
        if not x and not y:
            continue
        if x and not y:
            rows.append(("放開", f"{FINGER_NAME[k]}放開（{b_name} 用不到）"))
        elif y and not x:
            rows.append(("新放下", f"{FINGER_NAME[k]}新放下：{_pos_text(y)}"))
        elif sorted(x) == sorted(y):
            rows.append(("不動", f"{FINGER_NAME[k]}不要動（{_pos_text(x)}，兩個和弦都要按）"))
        elif len(x) == 1 and len(y) == 1 and x[0][0] == y[0][0]:
            rows.append(("滑過去", f"{FINGER_NAME[k]}沿著第{6 - x[0][0]}弦從第{x[0][1]}格滑到第{y[0][1]}格，手指不要離開弦"))
        else:
            rows.append(("移動", f"{FINGER_NAME[k]}移動：{_pos_text(x)} → {_pos_text(y)}"))
    ea = [(s, f, fa[s]) for s, f in enumerate(a["frets"]) if f]
    eb = {(s, f, fb[s]) for s, f in enumerate(b["frets"]) if f}
    shift = ""
    if ea and len(ea) == len(eb):
        for d in (-2, -1, 1, 2):
            if all((s + d, f, k) in eb for s, f, k in ea):
                shift = f"整個手型不變，往{'細弦（第1弦）' if d > 0 else '粗弦（第6弦）'}方向平移 {abs(d)} 條弦"
                break
        if not shift:
            for t in [t for t in range(-7, 8) if t]:
                if all((s, f + t, k) in eb for s, f, k in ea):
                    shift = f"整個手型不變，往{'琴身' if t > 0 else '琴頭'}方向平移 {abs(t)} 格"
                    break
    count = {st: sum(1 for r in rows if r[0] == st) for st in ("不動", "滑過去", "移動", "新放下")}
    moving = count["移動"] + count["新放下"]
    level = "簡單" if moving <= 1 else "中等" if moving == 2 else "比較難"
    if any(len(v) > 1 for v in pb.values()) and moving >= 2:
        level = "比較難"
    if shift:
        level = "簡單（手型不變）"
    bi = next(i for i, f in enumerate(b["frets"]) if f is not None)
    if b["frets"][bi] == 0:
        bass = f"{b_name} 的最低音是第{6 - bi}弦空弦，換和弦時低音不用按，可以先彈出低音、爭取時間把其他手指放好。"
    else:
        who = FINGER_NAME[fb[bi]] if fb[bi] else "手指"
        bass = f"先放{who}到第{6 - bi}弦第{b['frets'][bi]}格——這是 {b_name} 的最低音，下一拍第一下就要彈到它。"
    return {"rows": rows, "shift": shift, "level": level, "bass": bass, "stay": count["不動"], "slide": count["滑過去"]}


def change_text(a_name, a, b_name, b):
    r = analyze_change(a_name, a, b_name, b)
    lines = [f"🎸 換和弦：{a_name} → {b_name}　難度：{r['level']}",
             f"{a_name} {frets_text(a['frets'])} → {b_name} {frets_text(b['frets'])}"]
    if r["shift"]:
        lines.append("✨ " + r["shift"] + "——只要記住一個手型，整隻手一起移。")
    lines += [f"・〔{st}〕{text}" for st, text in r["rows"]]
    lines.append("👉 " + r["bass"])
    lines.append("練習：只練這兩個和弦，每個刷 4 下就換，來回 1 分鐘；順了再改成每個 2 下、1 下。")
    return "\n".join(lines)


# ── 節奏刷法（「吉他和弦全書」的節奏資料） ─────────────────────────────────────
# 記號：D＝下刷 U＝上刷 B＝根音（最低音） b＝第二低的音 X＝切音 -＝空刷。spb＝每拍幾格。

RHYTHMS = (
    ("基本四拍", "4/4", 2, "D-D-D-D-", "最單純、最穩。", "剛開始練拍子；任何四拍子的歌都能先用它撐住。"),
    ("八分音符交替刷", "4/4", 2, "DUDUDUDU", "飽滿、有推進感。", "練右手「下上下上」的鐘擺；搖滾、流行副歌。"),
    ("進行曲／鄉村（碰恰碰恰）", "4/4", 2, "B-D-b-D-", "低音和刷弦輪流，一拍一下，很有精神。", "兒歌、進行曲、鄉村歌曲。"),
    ("Slow Soul（慢靈魂）", "4/4", 2, "D-DUD-DU", "穩定、柔和、沉著。", "抒情慢歌、情歌，華語流行抒情最常見。"),
    ("Slow Soul 低音版", "4/4", 2, "B-DUb-DU", "有低音在走，比較有層次。", "主歌、比較安靜的段落。"),
    ("Slow Soul 低音版 B", "4/4", 2, "B-DUD-DU", "只有第 1 拍彈低音，第 3 拍整個下刷，比低音版更飽滿。", "抒情歌的副歌。"),
    ("Folk Rock（民謠搖滾）", "4/4", 2, "D-DU-UDU", "輕快、往前推；第 3 拍空刷做出切分感。", "民謠、校園民歌、中速流行歌，最萬用的刷法。"),
    ("Folk Rock 低音版", "4/4", 2, "B-DU-UDU", "第 1 拍改彈根音，更有層次。", "主歌、想要有「貝斯感」的時候。"),
    ("Folk Rock 切音版", "4/4", 2, "D-XU-UXU", "跳躍，像有鼓在打。", "輕快的流行歌、副歌。"),
    ("16 Beat 基本練習", "4/4", 4, "DUDUDUDUDUDUDUDU", "密集、細膩。", "先把每拍 4 下練均勻，之後依歌曲拿掉其中幾下。"),
    ("16 Beat 進階 A", "4/4", 4, "B---D-DU-UD-D-DU", "第 2、4 拍是「恰 恰啦」，第 3 拍先空一下再「啦 恰」。", "中慢板的流行抒情、R&B。"),
    ("16 Beat 進階 B", "4/4", 4, "B---D--U-UD-D-DU", "跟 A 很像，但第 2 拍後半先空一下，聲音更有空間。", "想要比較輕、有呼吸感的時候。"),
    ("16 Beat 進階 C", "4/4", 4, "B---D-DU--U-D-DU", "第 3 拍是 8 分音符的「ㄇ 啦」，比 A 少一下。", "中慢板流行抒情。"),
    ("16 Beat 進階 D（連續型）", "4/4", 4, "B-DUD-DU-UDUD-DU", "幾乎每格都有刷，最考驗右手的穩定。", "R&B、節奏感強的抒情歌。"),
    ("16 Beat 切音型", "4/4", 4, "B-DUX-DU-UDUX-DU", "第 2、4 拍改成切音，像有鼓在打。", "節奏明顯的流行歌、R&B。"),
    ("Waltz 華爾滋（蹦恰恰）", "3/4", 2, "B-D-D-", "圓舞曲的搖擺，重音在第 1 拍。", "老歌、校園民謠、三拍子的抒情歌。"),
    ("Waltz 變化（蹦 恰 恰拉）", "3/4", 2, "B-D-DU", "第 3 拍多一個上刷，比蹦恰恰流動一點。", "三拍子的抒情歌。"),
    ("Waltz 流動版", "3/4", 2, "B-DUDU", "比蹦恰恰更流動、更滿。", "三拍子的歌想要聲音飽滿一點時。"),
    ("6/8 拍（低音型）", "6/8", 1, "BDDbDD", "搖籃般左右搖晃；重音在第 1、4 下。", "慢板抒情、聖歌、民謠。速度以八分音符計算。"),
    ("流行搖滾切音（恰 切拉）", "4/4", 2, "D-XUD-XU", "第 2、4 拍切音，像小鼓在打。", "流行搖滾、輕快的副歌。"),
    ("Slow Rock（慢搖滾）", "4/4（每拍三連音）", 3, "BDDDDDbDDDDD", "每拍分三下，有五、六○年代慢情歌的搖擺感。", "老式抒情搖滾、慢情歌。"),
    ("Shuffle（搖擺）", "4/4（三連音感）", 2, "D-DUDUDU", "「長－短、長－短」的跳躍感，像馬蹄聲。", "藍調、搖擺樂、鄉村。"),
)
STRUM_LEGEND = ("D：下刷（從第 6 弦往第 1 弦刷）", "U：上刷（從第 1 弦往第 6 弦刷）", "B：只彈根音（和弦最低那條弦）",
                "b：只彈第二低的音", "X：切音（刷下去馬上用手掌悶住）", "-：空刷（手照擺、不碰弦）",
                "count 那一行是數拍子：1 & 2 &…（16 Beat：1 e & a；三連音：1 la li）")
SUB_COUNTS = {1: [""], 2: ["", "&"], 3: ["", "la", "li"], 4: ["", "e", "&", "a"]}


def strum_count(spb, n):
    """每一格的數拍子：1 & 2 & …、1 e & a …、三連音 1 la li …；6/8（spb＝1）每格一拍。"""
    subs = SUB_COUNTS.get(spb, [""] * spb)
    return [str(i // spb + 1) if i % spb == 0 else subs[i % spb] for i in range(n)]


def find_rhythm(text):
    """'7'、'folk rock'、'華爾滋' → RHYTHMS 的 index；找不到回 None。"""
    t = (text or "").strip()
    if t.isdigit():
        return int(t) - 1 if 1 <= int(t) <= len(RHYTHMS) else None
    low = t.lower().replace(" ", "")
    for i, r in enumerate(RHYTHMS):
        if low and low in r[0].lower().replace(" ", ""):
            return i
    return None


def strum_reply(i):
    """一種刷法：(Html, 純文字)。格子放在等寬區塊。"""
    from isdtelekit.core.richtext import Html, esc
    name, meter, spb, seq, feel, use = RHYTHMS[i]
    counts = strum_count(spb, len(seq))
    width = max(2, max(len(c) for c in counts) + 1)
    grid = ("count " + "".join(c.ljust(width) for c in counts).rstrip() + "\n"
            + "strum " + "".join(t.ljust(width) for t in seq).rstrip())
    head = f"🎸 刷法 {i + 1}：{name}（{meter}）"
    tail = [f"感覺：{feel}", f"用在：{use}"] + list(STRUM_LEGEND)
    return (Html(f"<b>{esc(head)}</b>\n<pre>{esc(grid)}</pre>\n" + "\n".join(esc(x) for x in tail)),
            head + "\n```\n" + grid + "\n```\n" + "\n".join(tail))


def strum_list():
    return "🎸 刷法（/chord_strum 編號或名稱）\n" + "\n".join(
        f"{i + 1}. {r[0]}（{r[1]}）{r[3]}" for i, r in enumerate(RHYTHMS))


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

    send_image = getattr(kit, "send_image", None)          # SYNOX SDK 0.2.5 起；舊版 SYNOX 沒有＝只送文字
    edit_image = getattr(kit, "edit_image", None)

    def interactive(api):
        """按鈕翻頁只給人用：經 AI 呼叫（via_ai）的一律不出按鈕，回固定的預設按法＋文字。"""
        return not getattr(api, "via_ai", False)

    def picture(api, chat_id, cells, caption, title, markup=None):
        """送和弦圖（一張，好幾個和弦排一列）；送不了（舊版 SYNOX、channel 不收）回 False，呼叫的人改送文字。"""
        if send_image is None or not cells:
            return False
        try:
            img = render_cells(cells, title)
        except Exception:                                   # 畫圖壞掉不擋文字
            return False
        out = send_image(api, chat_id, [img], caption=caption, reply_markup=markup)
        return bool((out or {}).get("ok"))

    def picture_or_text(api, chat_id, cells, caption, title, markup=None, text=None):
        """圖＋圖說（使用者看到的）；經 AI 呼叫時不出按鈕，文字（text，沒給＝圖說）另外給 AI（SYNOX 會再補
        「已經傳了 N 張圖」）；圖送不出去就送文字。"""
        via_ai = getattr(api, "via_ai", False)
        ok = picture(api, chat_id, cells, caption, title, None if via_ai else markup)
        if via_ai or not ok:
            send(api, chat_id, text or caption)

    def unknown_reply(api, chat_id, raw, bad):
        api.send_message(chat_id, "❓ " + "；".join(bad or [f"看不懂「{raw}」是什麼和弦"])
                         + "\n例：/chord Am7、/chord D/F#、/chord Bb F Gm、/chord F major 7")

    def show_chords(api, chat_id, chords, bad=(), detail=False):
        """預設＝先給圖：每個和弦的第一個按法排成一張圖，字只有名稱＋一行按法。detail＝再附完整說明。"""
        names = [own_name(c) for c in chords]
        via_ai = getattr(api, "via_ai", False)
        if len(chords) == 1:
            ch = chords[0]
            vs = voicings(ch["root"], ch["quality"], ch["bass"])
            if not vs:
                api.send_message(chat_id, f"🎸 {names[0]}：找不到好按的按法。")
                return
            caption = page_caption(ch, vs, 0)
            markup = pager_markup(ch, 0, len(vs)) if interactive(api) else None
            cells = [diagram_cell(ch, vs[0])]
        else:
            key = detect_key(chords)
            caption = "🎸 " + " ".join(names) + "\n" + "\n".join(describe_short(c, key) for c in chords) + \
                "\n（按法：第6弦→第1弦，x＝不彈；說明：/chord_info）"
            markup = multi_markup(chords) if interactive(api) else None
            cells = []
            for c in chords[:MAX_DIAGRAMS]:
                vs = voicings(c["root"], c["quality"], c["bass"])
                if vs:
                    cells.append(diagram_cell(c, vs[0]))
        if bad:
            caption += "\n⚠️ " + "；".join(bad)
        picture_or_text(api, chat_id, cells, caption, " ".join(names) + " 和弦圖", markup)
        if detail:
            send(api, chat_id, "\n\n".join(describe(c) for c in chords))

    def parse_input(api, chat_id, raw):
        """一串和弦 → (和弦, 看不懂的)。都看不懂就回覆、回 None。"""
        chords, bad = resolve_chords(raw)
        if not chords:
            unknown_reply(api, chat_id, raw, bad)
            return None
        return chords, bad

    def show(api, chat_id, user, values):
        raw = values["text"].strip()
        detail = bool(re.search(r"(?:^|\s)(?:說明|詳細|detail|info)$", raw, re.I))
        if detail:
            raw = re.sub(r"\s*(?:說明|詳細|detail|info)$", "", raw, flags=re.I).strip()
        key = parse_key(raw)
        if key and raw.endswith(("調", "小調", "大調")):
            send(api, chat_id, describe_key(key))
            return
        got = parse_input(api, chat_id, raw)
        if got:
            show_chords(api, chat_id, got[0], got[1], detail)

    def info(api, chat_id, user, values):
        got = parse_input(api, chat_id, values["text"].strip())
        if not got:
            return
        chords, bad = got
        text = "\n\n".join(describe(c) for c in chords[:MAX_DIAGRAMS])
        if bad:
            text += "\n⚠️ " + "；".join(bad)
        cells = [diagram_cell(c, vs[0]) for c in chords[:MAX_DIAGRAMS]
                 for vs in [voicings(c["root"], c["quality"], c["bass"])] if vs]
        send(api, chat_id, text)
        picture(api, chat_id, cells, "", " ".join(own_name(c) for c in chords) + " 和弦圖")

    def alt(api, chat_id, user, values):
        raw = values["text"].strip()
        m = re.match(r"^(.*?)\s+(\d{1,2})$", raw)
        idx = int(m.group(2)) - 1 if m else 0
        got = parse_input(api, chat_id, m.group(1) if m else raw)
        if not got:
            return
        ch = got[0][0]
        vs = voicings(ch["root"], ch["quality"], ch["bass"])
        if not vs:
            api.send_message(chat_id, f"🎸 {own_name(ch)}：找不到好按的按法。")
            return
        if not 0 <= idx < len(vs):
            api.send_message(chat_id, f"❓ {own_name(ch)} 只有 {len(vs)} 種按法（1～{len(vs)}）。")
            return
        caption = page_caption(ch, vs, idx)
        others = [f"{i + 1}. {v['label']} {frets_text(v['frets'])}" for i, v in enumerate(vs) if i != idx]
        full = caption + ("\n其他按法：" + "；".join(others) if others else "")
        markup = pager_markup(ch, idx, len(vs)) if interactive(api) else None
        picture_or_text(api, chat_id, [diagram_cell(ch, vs[idx])], caption, f"{own_name(ch)} 和弦圖", markup, text=full)

    def page_to(api, chat_id, message_id, ch, i, edit):
        vs = voicings(ch["root"], ch["quality"], ch["bass"])
        if not vs:
            return
        i %= len(vs)
        caption, markup = page_caption(ch, vs, i), pager_markup(ch, i, len(vs))
        img = render_cells([diagram_cell(ch, vs[i])], f"{own_name(ch)} 和弦圖")
        if edit and edit_image is not None:
            edit_image(api, chat_id, message_id, [img], caption=caption, reply_markup=markup)
        elif send_image is not None:
            send_image(api, chat_id, [img], caption=caption, reply_markup=markup)
        else:
            api.send_message(chat_id, caption, reply_markup=markup)

    def on_button(api, cb):
        """和弦圖的按鈕：v:<第幾個>:<和弦>＝這一則換成那個按法；o:<和弦>＝另開一則翻頁；i:<和弦…>＝送說明。"""
        try:
            api.answer_callback_query(cb.cq_id)
        except Exception:
            pass
        parts = cb.data[len(CB_PREFIX):].split(":", 2)
        action = parts[0] if parts else ""
        if action == "v" and len(parts) == 3 and parts[1].isdigit():
            chords, _ = resolve_chords(parts[2])
            if chords:
                page_to(api, cb.chat_id, cb.message_id, chords[0], int(parts[1]), edit=True)
        elif action == "o" and len(parts) >= 2:
            chords, _ = resolve_chords(":".join(parts[1:]))
            if chords:
                ch = chords[0]
                page_to(api, cb.chat_id, 0, ch, 1 if len(voicings(ch["root"], ch["quality"], ch["bass"])) > 1 else 0,
                        edit=False)
        elif action == "i" and len(parts) >= 2:
            chords, _ = resolve_chords(":".join(parts[1:]))
            if chords:
                send(api, cb.chat_id, "\n\n".join(describe(c) for c in chords[:MAX_DIAGRAMS]))

    def tones(api, chat_id, user, values):
        got = parse_input(api, chat_id, values["text"].strip())
        if not got:
            return
        chords, bad = got
        key = detect_key(chords) if len(chords) > 1 else None
        text = "\n\n".join(tones_text(c, key) for c in chords[:MAX_DIAGRAMS])
        send(api, chat_id, text + ("\n⚠️ " + "；".join(bad) if bad else ""))

    def easy(api, chat_id, user, values):
        got = parse_input(api, chat_id, values["text"].strip())
        if not got:
            return
        chords, bad = got
        cells, lines = [], []
        for c in chords[:MAX_DIAGRAMS]:
            v = easiest(c)
            if v is None:
                lines.append(f"{own_name(c)}：找不到好按的按法")
                continue
            cells.append(diagram_cell(c, v))
            line = f"{own_name(c)}　{frets_text(v['frets'])}（{v['label']}）"
            if v["kind"] not in ("open", "easy"):
                line += "：沒有不用橫按的按法"
            if v.get("tip"):
                line += "\n　💡 " + v["tip"]
            lines.append(line)
        caption = "🎸 最好按的按法\n" + "\n".join(lines) + ("\n⚠️ " + "；".join(bad) if bad else "")
        picture_or_text(api, chat_id, cells, caption, " ".join(own_name(c) for c in chords) + " 簡易和弦圖")

    def all_(api, chat_id, user, values):
        import unicodedata
        raw = unicodedata.normalize("NFKC", values["text"].strip())
        raw = raw[:1].upper() + raw[1:]
        pc = note_pc(raw.replace(" ", ""))
        if pc is None:
            api.send_message(chat_id, f"❓ 「{raw}」不是根音。例：/chord_all C、/chord_all Bb、/chord_all F#")
            return
        name = raw.replace(" ", "")
        chords = [{"root": pc, "root_txt": name, "suffix": QUAL[q][0], "quality": q, "bass": None, "approx": False}
                  for q in ALL_QUALITIES]
        chords = [c for c in chords if voicings(pc, c["quality"])]
        cells = [diagram_cell(c, voicings(pc, c["quality"])[0]) for c in chords]
        caption = f"🎸 {name} 開頭的常用和弦\n" + "\n".join(describe_short(c) for c in chords) + \
            "\n（按法：第6弦→第1弦，x＝不彈；其他種類例如 /chord " + name + "dim7）"
        picture_or_text(api, chat_id, cells, caption, f"{name} 常用和弦圖")

    def basic(api, chat_id, user, values):
        chords = [parse_chord(n) for n in BASIC]
        cells = [diagram_cell(c, voicings(c["root"], c["quality"])[0]) for c in chords]
        caption = ("🎸 入門先練這 8 個（都不用橫按）\n" + "\n".join(describe_short(c) for c in chords)
                   + "\n💡 先練 C、G、Am、Em 四個就能彈很多歌（1564／6415 進行）；/chord_change C Am 看換法")
        picture_or_text(api, chat_id, cells, caption, "入門和弦圖")

    def prog(api, chat_id, user, values):
        import unicodedata
        raw = unicodedata.normalize("NFKC", (values.get("text") or "").strip())
        head, _, pick = raw.partition(" ")
        key = parse_key(head)
        if key is None:
            api.send_message(chat_id, "❓ 第一個字寫調：/chord_prog G、/chord_prog G 卡農、/chord_prog Am 2")
            return
        found = find_progression(key, pick)
        if found is None:
            api.send_message(chat_id, f"❓ {key_name(key)} 調沒有「{pick}」這個進行，可以選：\n" + progression_list(key))
            return
        name, chords = found
        names = [chord_name(c, 0, key) for c in chords]
        uniq = _uniq(chords)
        cells = [diagram_cell(dict(c, root_txt=spell(c["root"], key)), easiest(c)) for c in uniq if easiest(c)]
        caption = (f"🎸 {key_name(key)} 調・{name}\n" + " → ".join(names)
                   + "\n" + "\n".join(f"{chord_name(c, 0, key)}　{frets_text(easiest(c)['frets'])}" for c in uniq
                                      if easiest(c))
                   + "\n其他進行：" + "；".join(progression_list(key).split("\n")))
        picture_or_text(api, chat_id, cells, caption, f"{key_name(key)} 調 {name} 和弦圖")

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
        raw = values["text"].strip()
        words = [t for t in re.split(r"[\s,，、]+", raw) if t]
        pattern = PATTERNS[0][1]
        # 最後一個字是型態（1～5 或 T1213121）、而且不是和弦的尾巴（Am 7 的 7 不算型態：型態只認 1～5）
        if words and parse_pattern(words[-1]) and not parse_chord(words[-1]) \
                and (not words[-1].isdigit() or int(words[-1]) <= len(PATTERNS)):
            pattern = parse_pattern(words.pop())
        chords, bad = resolve_chords(" ".join(words))
        if not chords:
            api.send_message(chat_id, ("❓ " + "；".join(bad) + "\n" if bad else "❓ ")
                             + "例：/chord_arp Am F C G、/chord_arp C 2（第 2 種型態）、/chord_arp G T121\n"
                             + "\n".join(f"{i}. {n} {s}" for i, (n, s) in enumerate(PATTERNS, 1)))
            return
        html, text = arp_reply(chords, pattern)
        if bad:
            from isdtelekit.core.richtext import Html, esc
            warn = "⚠️ " + "；".join(bad)
            html, text = Html(html + "\n" + esc(warn)), text + "\n" + warn
        if len(text) > CHUNK:
            send(api, chat_id, text)
        else:
            api.send_message(chat_id, html)
        cells = [diagram_cell(c, vs[0]) for c in chords[:MAX_DIAGRAMS]
                 for vs in [voicings(c["root"], c["quality"], c["bass"])] if vs]
        picture(api, chat_id, cells, "左手按法（和 TAB 同一組）", " ".join(own_name(c) for c in chords) + " 和弦圖")

    def change(api, chat_id, user, values):
        chords, bad = resolve_chords(values["text"].strip())
        if len(chords) != 2:
            api.send_message(chat_id, ("❓ " + "；".join(bad) + "\n" if bad else "❓ ")
                             + "給我兩個和弦：/chord_change C Am（從 C 換到 Am）")
            return
        a, b = chords
        va = voicings(a["root"], a["quality"], a["bass"])
        vb = voicings(b["root"], b["quality"], b["bass"])
        if not va or not vb:
            api.send_message(chat_id, "🎸 其中一個和弦找不到好按的按法。")
            return
        text = change_text(own_name(a), va[0], own_name(b), vb[0])
        send(api, chat_id, text)
        picture(api, chat_id, [diagram_cell(a, va[0]), diagram_cell(b, vb[0])], f"{own_name(a)} → {own_name(b)}",
                f"{own_name(a)} 換到 {own_name(b)}")

    def strum(api, chat_id, user, values):
        raw = (values.get("text") or "").strip()
        if not raw:
            send(api, chat_id, strum_list())
            return
        i = find_rhythm(raw)
        if i is None:
            api.send_message(chat_id, f"❓ 找不到刷法「{raw}」。\n" + strum_list())
            return
        html, _text = strum_reply(i)
        api.send_message(chat_id, html)

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

    show_node = kit.command(
        g, "show", show, "查和弦：直接回和弦圖（圖片）＋一行按法；一次好幾個就排成一張圖。使用者要「圖」就用這個",
        default=True, positional=1, usage="<和弦…> [說明]（例：Am7、D/F# Bb F Gm Cm、F major 7；最後加「說明」看手指與組成音）",
        fields=[Field("text", "和弦名稱，可以一次好幾個（D/F# Bb F Gm Cm）；最後加「說明」附完整說明", required=True)])
    kit.command(g, "info", info, "和弦的完整說明：每根手指按哪裡、組成音、提醒、其他按法（附和弦圖）", positional=1,
                usage="<和弦…>（例：F、Bbmaj7）",
                fields=[Field("text", "和弦名稱，可以一次好幾個", required=True)])
    kit.command(g, "alt", alt, "一個和弦的第 N 種按法（和弦圖）；不寫 N＝第 1 種，回覆會列出共有幾種", positional=1,
                usage="<和弦> [第幾種]（例：F 2、G 3）",
                fields=[Field("text", "和弦，後面可以加第幾種按法", required=True)])
    kit.command(g, "change", change, "換和弦分析：從 A 換到 B 每根手指怎麼動、哪根不用動（附兩個和弦圖）", positional=1,
                usage="<和弦A> <和弦B>（例：C Am、G D/F#）",
                fields=[Field("text", "兩個和弦：從哪個換到哪個", required=True)])
    kit.command(g, "strum", strum, "節奏刷法：不寫＝列出 22 種，寫編號或名稱＝那一種的刷法格子", positional=1,
                usage="[編號或名稱]（例：7、folk rock、華爾滋）",
                fields=[Field("text", "刷法的編號或名稱（不寫＝列出全部）")])
    kit.command(g, "easy", easy, "最好按的按法：每個和弦挑不用大橫按的那個（和弦圖），說明最低音是不是根音", positional=1,
                usage="<和弦…>（例：F Bm B、Bb Cm）",
                fields=[Field("text", "和弦名稱，可以一次好幾個", required=True)])
    kit.command(g, "tones", tones, "和弦的組成音與音程（根音、大三度、完全五度…），和每條弦彈到的音", positional=1,
                usage="<和弦…>（例：Cmaj7、D/F#、Bdim7）",
                fields=[Field("text", "和弦名稱，可以一次好幾個", required=True)])
    kit.command(g, "all", all_, "一個根音的常用和弦（大三、小三、7、m7、maj7、sus2、sus4、add9）排成一張和弦圖", positional=1,
                usage="<根音>（例：C、Bb、F#）",
                fields=[Field("text", "根音（C、Bb、F#…）", required=True)])
    kit.command(g, "basic", basic, "入門先練的 8 個開放和弦（C G Am Em D A E Dm）的和弦圖", positional=0,
                usage="", fields=[])
    kit.command(g, "prog", prog, "一個調的常見和弦進行（1564、卡農…），附和弦圖；不寫進行＝第一個", positional=1,
                usage="<調> [進行的編號或名稱]（例：G、G 卡農、Am 2）",
                fields=[Field("text", "調，後面可以寫第幾個進行或名稱", required=True)])
    kit.command(g, "tp", tp, "貼一份譜直接移調", effect=WRITE, positional=1,
                usage="<移法> 換行貼譜（移法：+2、-3、G→A、到 A；不貼譜＝用上次那份）",
                fields=[Field("text", "移法，下一行起是譜", required=True)])
    kit.command(g, "capo", capo, "夾 Capo 用哪組手型最好彈", effect=WRITE, positional=1,
                usage="[格數] [譜或調]（例：Bb；3；不寫＝用上次貼的譜）",
                fields=[Field("text", "譜或調，可以先寫要夾第幾格")])
    kit.command(g, "arp", arp, "分散和弦的 TAB 譜（等寬）＋左手和弦圖", positional=1,
                usage="<和弦…> [型態 1～5 或 T1213121]",
                fields=[Field("text", "和弦，最後可以加型態", required=True)])
    kit.command(g, "key", key_, "一個調裡有哪些和弦", positional=1, usage="<調>（例：G、Am、Bb）",
                fields=[Field("text", "調，或一串和弦讓我猜調", required=True)])
    kit.command(g, "chart", chart_cmd, "整理成一格一小節的和弦譜", effect=WRITE, positional=1,
                usage="[移法] 換行貼進行、譜或小節譜 JSON（例：| C | G D/F# | Am | F |；不貼＝用上次的譜）",
                fields=[Field("text", "和弦進行、譜或小節譜 JSON，第一行可以先寫移法")])
    try:
        app.registry.bind_callback(CB_PREFIX, show_node.path, on_button)
    except ValueError:
        pass        # pack reload：舊的前綴還綁著（SYNOX 已知限制），沿用舊的那一個
    pack.web_route("data", data_route)


def _uniq(chords):
    out, seen = [], set()
    for ch in chords:
        k = (ch["root"], ch["quality"], ch["bass"])
        if k not in seen:
            seen.add(k)
            out.append(ch)
    return out
