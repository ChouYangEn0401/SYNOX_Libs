"""每週哪幾天、重複到哪天（從一個本機行事曆程式移植；只做每週，不做每月幾號、不做「第二個星期三」）。"""

from __future__ import annotations

import calendar as _cal
import re
from datetime import date

from .errors import CalError

WEEKDAY_LABEL = "一二三四五六日"

_NO_REPEAT = {"", "-", "none", "no", "off", "never", "false", "不重複", "不重复", "不要重複", "取消重複", "單次", "一次"}
_GROUPS = (
    (("weekdays", "weekday", "workdays", "workday", "平日", "工作日", "上班日"), (1, 2, 3, 4, 5)),
    (("weekends", "weekend", "週末", "周末"), (6, 7)),
    (("everyday", "every day", "daily", "每天", "天天", "每日"), (1, 2, 3, 4, 5, 6, 7)),
)
_FILLER = ("每個禮拜", "每个礼拜", "每個星期", "每个星期", "每星期", "每禮拜", "每礼拜",
           "每週", "每周", "禮拜", "礼拜", "星期", "週", "周", "每", "和", "跟", "與", "与", "及")
_EN_FILLER = re.compile(r"\b(?:every|weekly|on|and)\b")
_TOKEN = re.compile(r"(mon|tue|wed|thu|fri|sat|sun)[a-z]*|[0-7]|[一二三四五六日天]|到|至|~|～|-|－|–|—")
_EN = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 7}
_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_SEP = " \t,，、/;；.。&+[]()（）"
_RANGE = {"到", "至", "~", "～", "-", "－", "–", "—"}
_HINT = "（可以寫 二三四、週二三四、每週二、三、四、tue,wed,thu、2,3,4、一到五、weekdays、週末）"


def parse_weekdays(value) -> list:
    """每週哪幾天 → ISO 編號（週一＝1 … 週日＝7）排好的清單。`[]`＝不重複。認不出來就講，不猜。"""
    if value is None:
        return []
    if isinstance(value, dict):
        return parse_weekdays(value.get("weekdays"))
    if isinstance(value, bool):
        raise CalError(f"看不懂星期幾：{value!r}{_HINT}")
    if isinstance(value, int):
        value = [value]
    if isinstance(value, (list, tuple, set)):
        picked = set()
        for v in value:
            if isinstance(v, bool):
                raise CalError(f"看不懂星期幾：{v!r}{_HINT}")
            if isinstance(v, int):
                v = 7 if v == 0 else v
                if not 1 <= v <= 7:
                    raise CalError(f"星期幾要是 1～7（週一＝1、週日＝7），收到 {v}")
                picked.add(v)
            else:
                picked.update(parse_weekdays(v))
        return sorted(picked)

    s = str(value).strip().lower()
    if s in _NO_REPEAT:
        return []
    out = set()
    for words, days in _GROUPS:
        for w in words:
            if w in s:
                out.update(days)
                s = s.replace(w, " ")
    for w in _FILLER:
        s = s.replace(w, " ")
    s = _EN_FILLER.sub(" ", s)

    seq, pos = [], 0
    for m in _TOKEN.finditer(s):
        if s[pos:m.start()].strip(_SEP):
            raise CalError(f"看不懂星期幾：{value!r}{_HINT}")
        tok = m.group(0)
        if tok in _RANGE:
            seq.append("~")
        elif tok[:3] in _EN:
            seq.append(_EN[tok[:3]])
        elif tok.isdigit():
            seq.append(7 if tok == "0" else int(tok))
        else:
            seq.append(_CN[tok])
        pos = m.end()
    if s[pos:].strip(_SEP):
        raise CalError(f"看不懂星期幾：{value!r}{_HINT}")

    i = 0
    while i < len(seq):
        a = seq[i]
        if a == "~":
            raise CalError(f"看不懂星期幾：{value!r}{_HINT}")
        if i + 1 < len(seq) and seq[i + 1] == "~":             # 一到五
            if i + 2 >= len(seq) or seq[i + 2] == "~" or seq[i + 2] < a:
                raise CalError(f"看不懂星期幾的範圍：{value!r}{_HINT}")
            out.update(range(a, seq[i + 2] + 1))
            i += 3
        else:
            out.add(a)
            i += 1
    if not out or any(not 1 <= d <= 7 for d in out):
        raise CalError(f"要說每週星期幾（週一＝1、週日＝7）：收到 {value!r}{_HINT}")
    return sorted(out)


_UNTIL_NONE = _NO_REPEAT | {"不限", "沒有", "一直", "永遠", "forever"}


def parse_until(value, start: date):
    """重複到哪天（含）。`None`＝一直重複。認得 2026-12-31／12/31／年底／月底（以第一次那天算）。"""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if s.lower() in _UNTIL_NONE:
        return None
    for pre in ("until", "直到", "到"):
        if s.lower().startswith(pre):
            s = s[len(pre):].strip()
            break
    if s in ("年底", "今年底", "今年年底", "年尾"):
        return date(start.year, 12, 31)
    if s in ("月底", "本月底", "這個月底", "这个月底", "這月底", "月尾"):
        return date(start.year, start.month, _cal.monthrange(start.year, start.month)[1])
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})", s)
        if not m:
            raise CalError(f"看不懂重複到哪天：{value!r}（可以寫 2026-12-31、12/31、年底、月底；none＝一直重複）")
        mo, d = map(int, m.groups())
        y = start.year
    try:
        out = date(y, mo, d)
    except ValueError:
        raise CalError(f"重複到哪天不是真的日期：{value!r}") from None
    if not m.re.pattern.startswith(r"(\d{4})") and out < start:      # 只寫月/日、比第一次早 → 明年
        out = date(y + 1, mo, d)
    return out


def repeat_text(rule) -> str:
    """{"weekdays": [2, 3, 4]} → 「每週二三四」。不是重複的回空字串。"""
    wd = sorted((rule or {}).get("weekdays") or [])
    if not wd:
        return ""
    if wd == [1, 2, 3, 4, 5, 6, 7]:
        return "每天"
    if wd == [1, 2, 3, 4, 5]:
        return "每週一到五"
    return "每週" + "".join(WEEKDAY_LABEL[d - 1] for d in wd)
