"""📔 日記（維護者自己的每日日記程式的 SYNOX 版）：挑一個題組、多半用勾的就寫完，
輸出一頁 HTML 或一張像日記本內頁的卡片（SVG）。

從原專案 1:1 搬來（docs/sources.md）：題組、版型、分類與挑題組的規則、寫入的合併規則（fill／append／overwrite、
AI 整理一律標明並附原文）、歷史版本、垃圾桶、整份備份、匯出匯入、設定、起始組合、輸出（HTML 版型與卡片）、
畫圖用的 prompt（沒有 AI 的那一套）。為了 SYNOX 的 pack 契約改寫的地方：
- 原本的 SQLite／data/ 資料夾 → SYNOX 保管箱（pack.store.of(user)，密文、伺服器版每人一份）；
- 原本靠 pydantic／Jinja2 → 只用標準函式庫：題組照原本的 schema 自己檢查，版型用下面的「小 Jinja」（只收原本版型用到的語法）；
- 原本 FastAPI 的網址與命令列 → /diary_* 指令（AI 的工具 diary__*）；檔案寫在使用者自己的資料夾、傳給他之後就刪。
每個人的資料：
  diary（主檔）  {"schema", "entries", "revisions", "next_entry", "next_revision"}＝原本的 journal.sqlite3
  diary.setup     {"preferences", "templates", "layouts", "hidden_templates", "hidden_layouts", "seeded", "backups"}
  diary.backup-<槽> 整份備份（原本 data/backups/*.sqlite3）
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from isdtelekit.core.registry import DANGER, WRITE, Field

from synox.packs import kit

SCHEMA = 1


def now() -> datetime:
    """「現在」（測試會換掉它）。"""
    return datetime.now()


class DiaryError(Exception):
    """使用者改得了的事；訊息就是要回給他的那一句。"""


# ══════════════════════════════════════════════════════════════════════════
# 分類（原 backend/models.py）
# ══════════════════════════════════════════════════════════════════════════

STRUCTURES: dict[str, str] = {
    "one-part": "一段式",
    "three-part": "三段式",
    "n-part": "多段式",
}
REFLECTIONS: dict[str, str] = {
    "none": "只記錄",
    "reflection": "有反思（不談信仰）",
    "devotional": "有信仰的反思",
    "prayer": "有禱告／代禱",
}
FLOW_TOKENS: dict[str, str] = {
    "checkin": "與主同行的打卡",
    "events": "發生的事",
    "feelings": "感受",
    "worries": "擔心的事",
    "work": "工作與專案",
    "decision": "一個決定",
    "identity": "想成為的人",
    "review": "一段時間的回顧",
    "scripture": "經文",
    "reflection": "反思／學到什麼",
    "confession": "認罪／對不起",
    "gratitude": "感恩",
    "prayer": "禱告／代禱",
    "trust": "信靠",
    "people": "人",
    "quote": "別人說過的話",
    "plan": "下一步／明天",
}
#: 題型（原 FieldKind）：choice／multi_choice／scale 是「勾了就好」的；fill 是填空句（{} 是空格）。
KINDS = ("choice", "multi_choice", "scale", "short_text", "fill", "text", "list")
TICK_KINDS = ("choice", "scale", "multi_choice")
PAPERS = ("ruled", "grid", "plain", "dotted")
MERGES = ("fields", "append", "fill")
FORMATS = ("plain", "html")
EXPORTS = ("both", "html", "card")
AUTHORS = ("user", "ai")
PRESETS = ("generic", "devotional")
DAY_WORDS = {"today": 0, "今天": 0, "yesterday": 1, "昨天": 1}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass
class Option:
    id: str
    label: str


@dataclass
class TField:
    id: str
    label: str
    kind: str = "text"
    options: list = field(default_factory=list)
    pattern: str | None = None
    placeholder: str | None = None
    min_rows: int = 2
    color: str | None = None
    hint: str | None = None
    ai_hint: str | None = None


@dataclass
class Section:
    id: str
    title: str
    color: str = "#2f3437"
    icon: str | None = None
    fields: list = field(default_factory=list)


@dataclass
class Template:
    id: str
    name: str
    description: str = ""
    default_theme: str = "letterpress"
    default_layout: str = "letter"
    group: str = ""
    order: int = 1000
    when: str = ""
    minutes: int = 0
    moods: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    structure: str = ""
    reflection: str = "none"
    flow: list = field(default_factory=list)
    closing_label: str = "給主的話"
    closing_hint: str = "今天想對主說的一段話。也可以換色。"
    closing_placeholder: str = "親愛的主耶穌，今天…"
    sections: list = field(default_factory=list)
    #: 內建、但沒跑 setup init 時預設不出現（使用者用 /diary_tpl_restore 拿出來）。只影響第一次裝哪些題組。
    default_hidden: bool = False

    def field_map(self) -> dict:
        return {f.id: f for s in self.sections for f in s.fields}


def _s(raw: dict, key: str, default, what: str, optional: bool = False):
    value = raw.get(key, default)
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{what} 的 {key} 要是文字")
    return value


def _i(raw: dict, key: str, default: int, what: str) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value)
        raise ValueError(f"{what} 的 {key} 要是整數")
    return int(value)


def _strs(raw: dict, key: str, what: str) -> list:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{what} 的 {key} 要是文字的清單")
    return list(value)


def parse_template(raw: Any) -> Template:
    """一個題組檔（JSON 物件）→ Template；不合就丟 ValueError（一句人話）。規則照原本的 pydantic 模型。"""
    if not isinstance(raw, dict):
        raise ValueError("題組要是一個 JSON 物件")
    if not isinstance(raw.get("id"), str) or not raw.get("id"):
        raise ValueError("題組要有 id")
    if not isinstance(raw.get("name"), str):
        raise ValueError("題組要有 name")
    t = Template(
        id=raw["id"], name=raw["name"],
        description=_s(raw, "description", "", "題組"),
        default_theme=_s(raw, "default_theme", "letterpress", "題組"),
        default_layout=_s(raw, "default_layout", "letter", "題組"),
        group=_s(raw, "group", "", "題組"),
        order=_i(raw, "order", 1000, "題組"),
        when=_s(raw, "when", "", "題組"),
        minutes=_i(raw, "minutes", 0, "題組"),
        moods=_strs(raw, "moods", "題組"), tags=_strs(raw, "tags", "題組"),
        structure=_s(raw, "structure", "", "題組"),
        reflection=_s(raw, "reflection", "none", "題組", optional=True) or "none",
        flow=_strs(raw, "flow", "題組"),
        closing_label=_s(raw, "closing_label", "給主的話", "題組"),
        closing_hint=_s(raw, "closing_hint", "今天想對主說的一段話。也可以換色。", "題組"),
        closing_placeholder=_s(raw, "closing_placeholder", "親愛的主耶穌，今天…", "題組"),
        default_hidden=bool(raw.get("default_hidden", False)),
    )
    if t.structure and t.structure not in STRUCTURES:
        raise ValueError(f"structure 只能是 {'、'.join(STRUCTURES)}（收到：{t.structure}）")
    if t.reflection not in REFLECTIONS:
        raise ValueError(f"reflection 只能是 {'、'.join(REFLECTIONS)}（收到：{t.reflection}）")
    unknown = [x for x in t.flow if x not in FLOW_TOKENS]
    if unknown:
        raise ValueError(f"flow 裡有不認識的步驟：{'、'.join(unknown)}（可用：{'、'.join(FLOW_TOKENS)}）")
    t.flow = list(dict.fromkeys(t.flow))
    sections = raw.get("sections", [])
    if not isinstance(sections, list):
        raise ValueError("sections 要是清單")
    for rs in sections:
        if not isinstance(rs, dict) or not isinstance(rs.get("id"), str) or not isinstance(rs.get("title"), str):
            raise ValueError("每個段落要有 id 和 title")
        sec = Section(id=rs["id"], title=rs["title"], color=_s(rs, "color", "#2f3437", "段落"),
                      icon=_s(rs, "icon", None, "段落", optional=True))
        rfields = rs.get("fields", [])
        if not isinstance(rfields, list):
            raise ValueError(f"段落 {sec.id} 的 fields 要是清單")
        for rf in rfields:
            if not isinstance(rf, dict) or not isinstance(rf.get("id"), str) or not isinstance(rf.get("label"), str):
                raise ValueError(f"段落 {sec.id} 的每一題要有 id 和 label")
            kind = rf.get("kind", "text")
            if kind not in KINDS:
                raise ValueError(f"題目 {rf['id']} 的 kind 只能是 {'、'.join(KINDS)}（收到：{kind}）")
            options = []
            for o in rf.get("options", []) or []:
                if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not isinstance(o.get("label"), str):
                    raise ValueError(f"題目 {rf['id']} 的選項要有 id 和 label")
                options.append(Option(o["id"], o["label"]))
            sec.fields.append(TField(
                id=rf["id"], label=rf["label"], kind=kind, options=options,
                pattern=_s(rf, "pattern", None, "題目", optional=True),
                placeholder=_s(rf, "placeholder", None, "題目", optional=True),
                min_rows=_i(rf, "min_rows", 2, "題目"),
                color=_s(rf, "color", None, "題目", optional=True),
                hint=_s(rf, "hint", None, "題目", optional=True),
                ai_hint=_s(rf, "ai_hint", None, "題目", optional=True)))
        t.sections.append(sec)
    if not t.structure:
        n = len(t.sections)
        t.structure = "one-part" if n == 1 else "three-part" if n == 3 else "n-part"
    return t


def template_dump(t: Template) -> dict:
    """Template → 存檔用的 JSON（原本的 model_dump）。"""
    out = asdict(t)
    if not out.get("default_hidden"):
        out.pop("default_hidden", None)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 文字（原 backend/richtext.py，逐行照搬）
# ══════════════════════════════════════════════════════════════════════════

INLINE_TAGS = {"b", "strong", "i", "em", "u", "s", "strike", "span"}
BLOCK_TAGS = {"div", "p", "li"}
VOID_TAGS = {"br"}
DROP_CONTENT_TAGS = {"script", "style", "title", "textarea", "noscript", "iframe", "object", "embed",
                     "template", "head", "svg", "math"}

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB = re.compile(r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,[^)]*)?\)$")
_COLOR_DECL = re.compile(r"(?:^|;)\s*color\s*:\s*([^;]+)", re.IGNORECASE)


def normalize_color(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if _HEX.match(value):
        if len(value) == 4:
            return "#" + "".join(ch * 2 for ch in value[1:]).lower()
        return value.lower()
    m = _RGB.match(value)
    if m:
        r, g, b = (min(255, int(p)) for p in m.groups())
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _extract_color(style: str | None) -> str | None:
    if not style:
        return None
    m = _COLOR_DECL.search(style)
    return normalize_color(m.group(1)) if m else None


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list = []
        self._open: list = []
        self._muted = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            self._muted += 1
            return
        if self._muted:
            return
        if tag in VOID_TAGS:
            self.out.append("<br>")
            return
        if tag in BLOCK_TAGS:
            if self.out and self.out[-1] != "<br>":
                self.out.append("<br>")
            self._open.append("")
            return
        if tag not in INLINE_TAGS:
            return
        if tag == "span":
            color = _extract_color(dict(attrs).get("style"))
            if color is None:
                self._open.append("")
                return
            self.out.append(f'<span style="color:{color}">')
            self._open.append("span")
            return
        canonical = {"strong": "b", "em": "i", "strike": "s"}.get(tag, tag)
        self.out.append(f"<{canonical}>")
        self._open.append(canonical)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            self._muted = max(0, self._muted - 1)
            return
        if self._muted:
            return
        if tag in VOID_TAGS:
            return
        if tag not in INLINE_TAGS | BLOCK_TAGS:
            return
        if not self._open:
            return
        opened = self._open.pop()
        if opened:
            self.out.append(f"</{opened}>")

    def handle_startendtag(self, tag, attrs):
        if not self._muted and tag.lower() in VOID_TAGS:
            self.out.append("<br>")

    def handle_data(self, data):
        if self._muted:
            return
        self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        tail = "".join(f"</{t}>" for t in reversed(self._open) if t)
        text = "".join(self.out) + tail
        text = re.sub(r"^(?:<br>)+", "", text)
        text = re.sub(r"(?:<br>)+$", "", text)
        return text


def sanitize(raw: str | None) -> str:
    if not raw:
        return ""
    parser = _Sanitizer()
    parser.feed(raw)
    parser.close()
    return parser.result()


@dataclass
class Run:
    text: str
    color: str | None = None
    bold: bool = False
    italic: bool = False


class _RunParser(HTMLParser):
    def __init__(self, default_color):
        super().__init__(convert_charrefs=True)
        self.runs: list = []
        self._colors: list = []
        self._bold = 0
        self._italic = 0
        self._default = default_color

    @property
    def _color(self):
        return self._colors[-1] if self._colors else self._default

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "br":
            self.runs.append(Run("\n", self._color, bool(self._bold), bool(self._italic)))
        elif tag == "span":
            color = _extract_color(dict(attrs).get("style"))
            self._colors.append(color or (self._color or ""))
        elif tag in ("b", "strong"):
            self._bold += 1
        elif tag in ("i", "em"):
            self._italic += 1

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "br":
            self.runs.append(Run("\n", self._color, bool(self._bold), bool(self._italic)))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "span" and self._colors:
            self._colors.pop()
        elif tag in ("b", "strong"):
            self._bold = max(0, self._bold - 1)
        elif tag in ("i", "em"):
            self._italic = max(0, self._italic - 1)

    def handle_data(self, data):
        if data:
            self.runs.append(Run(data, self._color, bool(self._bold), bool(self._italic)))


def to_runs(raw: str | None, default_color: str | None = None) -> list:
    if not raw:
        return []
    parser = _RunParser(default_color)
    parser.feed(sanitize(raw))
    parser.close()
    merged: list = []
    for run in parser.runs:
        if (merged and merged[-1].color == run.color and merged[-1].bold == run.bold
                and merged[-1].italic == run.italic and "\n" not in merged[-1].text and "\n" not in run.text):
            merged[-1].text += run.text
        else:
            merged.append(run)
    return merged


def to_plain(raw: str | None) -> str:
    return "".join(r.text for r in to_runs(raw)).strip()


# ══════════════════════════════════════════════════════════════════════════
# 紙張顏色（原 backend/render/themes.py）
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    backdrop: str
    paper: str
    paper_edge: str
    ink: str
    ink_muted: str
    hairline: str
    accent: str
    rule: str
    margin_rule: str
    display_font: str
    body_font: str
    dark_backdrop: str = "#14161a"
    dark_paper: str = "#1c1f24"
    dark_ink: str = "#e9e6df"
    dark_ink_muted: str = "#9aa0a8"
    dark_hairline: str = "#2f343b"
    tags: tuple = ()


_SERIF = '"Noto Serif TC", "Songti TC", "Source Han Serif TC", "PingFang TC", Georgia, "Times New Roman", serif'
_SANS = '"Noto Sans TC", "PingFang TC", "Microsoft JhengHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
_HAND = '"LXGW WenKai TC", "Kaiti TC", "DFKai-SB", STKaiti, "Noto Serif TC", serif'

THEMES: dict = {
    "letterpress": Theme(id="letterpress", name="信箋 Letterpress", backdrop="#efeae1", paper="#fdfbf6",
                         paper_edge="#e6dfd2", ink="#23262b", ink_muted="#7b7468", hairline="#e2dacb",
                         accent="#b5623c", rule="#e8e0d0", margin_rule="#e2b9ab", display_font=_SERIF,
                         body_font=_SERIF, tags=("溫暖", "書信感")),
    "notebook": Theme(id="notebook", name="筆記本 Notebook", backdrop="#e8ebee", paper="#ffffff",
                      paper_edge="#dfe3e8", ink="#22252a", ink_muted="#767d86", hairline="#e3e7ec",
                      accent="#2456b8", rule="#d9e4f2", margin_rule="#efb1ac", display_font=_HAND,
                      body_font=_HAND, tags=("手寫", "橫線稿")),
    "linen": Theme(id="linen", name="亞麻 Linen", backdrop="#e7e3da", paper="#f7f4ec", paper_edge="#ddd7c9",
                   ink="#2b2b28", ink_muted="#7d7869", hairline="#e0dacb", accent="#5d7a5a", rule="#e4dfd0",
                   margin_rule="#c8bba0", display_font=_SERIF, body_font=_SANS, tags=("素雅", "自然")),
    "dawn": Theme(id="dawn", name="晨光 Dawn", backdrop="#f3ecec", paper="#fffaf7", paper_edge="#f0dfd8",
                  ink="#2a2124", ink_muted="#8a7076", hairline="#f0dfd8", accent="#c9705f", rule="#f4e2dc",
                  margin_rule="#e9b7a8", display_font=_SERIF, body_font=_SANS, tags=("柔和", "清晨")),
    "midnight": Theme(id="midnight", name="夜禱 Midnight", backdrop="#14161c", paper="#1e222b",
                      paper_edge="#2b3140", ink="#e8e6e1", ink_muted="#98a0ad", hairline="#2e3540",
                      accent="#c8a86b", rule="#2a313c", margin_rule="#3d4756", display_font=_SERIF,
                      body_font=_SANS, dark_backdrop="#0f1116", dark_paper="#1a1e26", dark_ink="#e8e6e1",
                      dark_ink_muted="#98a0ad", dark_hairline="#2a313c", tags=("深色", "安靜")),
}
DEFAULT_THEME = "letterpress"


def get_theme(theme_id: str | None) -> Theme:
    return THEMES.get(theme_id or DEFAULT_THEME, THEMES[DEFAULT_THEME])


# ══════════════════════════════════════════════════════════════════════════
# 日記（原 Entry）
# ══════════════════════════════════════════════════════════════════════════

ENTRY_KEYS = ("id", "entry_date", "title", "template_id", "theme", "layout", "answers", "closing",
              "field_colors", "provenance", "created_at", "updated_at")


@dataclass
class Entry:
    id: int
    entry_date: date
    template_id: str
    title: str = ""
    theme: str = "letterpress"
    layout: str = "letter"
    answers: dict = field(default_factory=dict)
    closing: str = ""
    field_colors: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    created_at: datetime = None
    updated_at: datetime = None

    @classmethod
    def from_row(cls, row: dict) -> "Entry":
        theme = row.get("theme") or "letterpress"
        return cls(id=int(row["id"]), entry_date=date.fromisoformat(row["entry_date"]),
                   template_id=row["template_id"], title=row.get("title") or "",
                   theme=theme if theme in THEMES else "letterpress", layout=row.get("layout") or "letter",
                   answers=dict(row.get("answers") or {}), closing=row.get("closing") or "",
                   field_colors=dict(row.get("field_colors") or {}), provenance=dict(row.get("provenance") or {}),
                   created_at=datetime.fromisoformat(row["created_at"]),
                   updated_at=datetime.fromisoformat(row["updated_at"]))

    def to_row(self) -> dict:
        return {"id": self.id, "entry_date": self.entry_date.isoformat(), "title": self.title,
                "template_id": self.template_id, "theme": self.theme, "layout": self.layout,
                "answers": self.answers, "closing": self.closing, "field_colors": self.field_colors,
                "provenance": self.provenance, "created_at": self.created_at.isoformat(timespec="seconds"),
                "updated_at": self.updated_at.isoformat(timespec="seconds")}

    def copy(self, **updates) -> "Entry":
        row = self.to_row()
        e = Entry.from_row(row)
        for k, v in updates.items():
            if k == "theme":
                e.theme = v if v in THEMES else "letterpress"
            elif k == "layout":
                e.layout = v
        return e


# ══════════════════════════════════════════════════════════════════════════
# 一篇日記 → 兩種輸出共用的結構（原 backend/render/document.py）
# ══════════════════════════════════════════════════════════════════════════

WEEKDAYS_ZH = ["一", "二", "三", "四", "五", "六", "日"]
AI_LABEL = "（AI 整理）"
SOURCE_LABEL = "作者原文"
SOURCE_COLOR = "#6b675e"


@dataclass
class Chip:
    label: str
    selected: bool


@dataclass
class RenderedField:
    id: str
    label: str
    kind: str
    color: str
    chips: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    numbered: bool = False
    plain: str = ""
    author: str = "user"

    @property
    def is_empty(self) -> bool:
        if self.chips:
            return not any(c.selected for c in self.chips)
        return not any(any(r.text.strip() for r in line) for line in self.lines)


@dataclass
class RenderedSection:
    id: str
    title: str
    color: str
    icon: str | None
    fields: list = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return all(f.is_empty for f in self.fields)


@dataclass
class RenderedDoc:
    entry_id: int
    title: str
    entry_date: date
    date_display: str
    weekday: str
    template_name: str
    theme: str
    layout: str
    sections: list
    closing_lines: list
    word_count: int
    palette: list
    closing_label: str = "給主的話"

    @property
    def has_closing(self) -> bool:
        return any(any(r.text.strip() for r in line) for line in self.closing_lines)

    def to_plain_text(self) -> str:
        parts = [f"{self.date_display}（週{self.weekday}）"]
        if self.title:
            parts.append(self.title)
        for section in self.sections:
            if section.is_empty:
                continue
            parts.append(f"\n## {section.title}")
            for f in section.fields:
                if f.is_empty:
                    continue
                if f.chips:
                    picked = "、".join(c.label for c in f.chips if c.selected)
                    parts.append(f"- {f.label}：{picked}")
                else:
                    parts.append(f"- {f.label}：")
                    for line in f.lines:
                        text = "".join(r.text for r in line).strip()
                        if text:
                            parts.append(f"    {text}")
        if self.has_closing:
            parts.append(f"\n## {self.closing_label}")
            for line in self.closing_lines:
                text = "".join(r.text for r in line).strip()
                if text:
                    parts.append(text)
        return "\n".join(parts)


def _split_lines(runs: list) -> list:
    lines: list = [[]]
    for run in runs:
        if "\n" not in run.text:
            lines[-1].append(run)
            continue
        chunks = run.text.split("\n")
        for i, chunk in enumerate(chunks):
            if i:
                lines.append([])
            if chunk:
                lines[-1].append(Run(chunk, run.color, run.bold, run.italic))
    return [ln for ln in lines if ln] or [[]]


def _fill_runs(pattern: str, values: list, color: str) -> list:
    runs: list = []
    parts = pattern.split("{}")
    for i, part in enumerate(parts):
        if part:
            runs.append(Run(part))
        if i < len(parts) - 1:
            value = values[i] if i < len(values) else ""
            runs.append(Run(value or "＿＿＿＿", color, bold=bool(value)))
    return runs


def _render_field(tf: TField, raw_value: object, color: str) -> RenderedField:
    rendered = RenderedField(id=tf.id, label=tf.label, kind=tf.kind, color=color)
    if tf.kind in ("choice", "scale"):
        picked = raw_value if isinstance(raw_value, str) else ""
        rendered.chips = [Chip(label=o.label, selected=o.id == picked) for o in tf.options]
        rendered.plain = next((o.label for o in tf.options if o.id == picked), "")
    elif tf.kind == "multi_choice":
        picked_ids = set(raw_value) if isinstance(raw_value, list) else set()
        rendered.chips = [Chip(label=o.label, selected=o.id in picked_ids) for o in tf.options]
        rendered.plain = "、".join(o.label for o in tf.options if o.id in picked_ids)
    elif tf.kind == "short_text":
        text = raw_value if isinstance(raw_value, str) else ""
        rendered.lines = [[Run(text, color)]] if text else []
        rendered.plain = text
    elif tf.kind == "fill":
        values = [str(v) for v in raw_value] if isinstance(raw_value, list) else []
        runs = _fill_runs(tf.pattern or "{}", values, color)
        rendered.lines = _split_lines(runs) if any(values) else []
        rendered.plain = "".join(r.text for r in runs) if any(values) else ""
    elif tf.kind == "list":
        items = raw_value if isinstance(raw_value, list) else []
        rendered.numbered = True
        for item in items:
            runs = to_runs(str(item), color)
            if any(r.text.strip() for r in runs):
                rendered.lines.append(runs)
        rendered.plain = "\n".join("".join(r.text for r in line) for line in rendered.lines)
    else:
        runs = to_runs(str(raw_value or ""), color)
        rendered.lines = [ln for ln in _split_lines(runs) if any(r.text.strip() for r in ln)]
        rendered.plain = to_plain(str(raw_value or ""))
    return rendered


def _source_field(field_id: str, text: str) -> RenderedField:
    rendered = RenderedField(id=f"{field_id}__source", label=SOURCE_LABEL, kind="text", color=SOURCE_COLOR)
    runs = [Run(text, SOURCE_COLOR)]
    rendered.lines = [ln for ln in _split_lines(runs) if any(r.text.strip() for r in ln)]
    rendered.plain = text
    return rendered


def build_document(entry: Entry, template: Template, *, drop_empty: bool = True) -> RenderedDoc:
    sections: list = []
    palette: list = []
    marks = entry.provenance.get("fields", {}) if isinstance(entry.provenance, dict) else {}
    marks = marks if isinstance(marks, dict) else {}
    ai_written = False
    for ts in template.sections:
        rs = RenderedSection(id=ts.id, title=ts.title, color=ts.color, icon=ts.icon)
        for tf in ts.fields:
            color = entry.field_colors.get(tf.id) or tf.color or ts.color
            rf = _render_field(tf, entry.answers.get(tf.id), color)
            if drop_empty and rf.is_empty:
                continue
            mark = marks.get(tf.id)
            if isinstance(mark, dict) and mark.get("author") == "ai":
                rf.label, rf.author, ai_written = rf.label + AI_LABEL, "ai", True
                rs.fields.append(rf)
                if str(mark.get("source") or "").strip():
                    rs.fields.append(_source_field(tf.id, str(mark["source"]).strip()))
            else:
                rs.fields.append(rf)
            if color not in palette:
                palette.append(color)
        if not (drop_empty and rs.is_empty):
            sections.append(rs)
        if ts.color not in palette:
            palette.append(ts.color)
    source_text = str(entry.provenance.get("source_text") or "").strip() if isinstance(entry.provenance, dict) else ""
    closing_mark = marks.get("closing")
    closing_ai = isinstance(closing_mark, dict) and closing_mark.get("author") == "ai"
    if source_text and (ai_written or closing_ai):
        sections.append(RenderedSection(id="source", title=SOURCE_LABEL, color=SOURCE_COLOR, icon="✎",
                                        fields=[_source_field("entry", source_text)]))
    closing_runs = to_runs(entry.closing, "#22252a")
    closing_lines = [ln for ln in _split_lines(closing_runs) if any(r.text.strip() for r in ln)]
    body = "\n".join(f.plain for s in sections for f in s.fields) + "\n" + \
        "\n".join("".join(r.text for r in ln) for ln in closing_lines)
    d = entry.entry_date
    return RenderedDoc(
        entry_id=entry.id, title=entry.title.strip(), entry_date=d,
        date_display=f"{d.year}．{d.month:02d}．{d.day:02d}", weekday=WEEKDAYS_ZH[d.weekday()],
        template_name=template.name, theme=entry.theme, layout=entry.layout, sections=sections,
        closing_lines=closing_lines, word_count=len([c for c in body if not c.isspace()]), palette=palette,
        closing_label=(template.closing_label or "給主的話") + (AI_LABEL if closing_ai else ""))


# ══════════════════════════════════════════════════════════════════════════
# 中英混排斷行（原 backend/render/textlayout.py）
# ══════════════════════════════════════════════════════════════════════════

CLOSING_PUNCT = frozenset("、。，．：；！？）】》〉」』〞”’%,.!?;:)]}…〜～ー–—")
OPENING_PUNCT = frozenset("（【《〈「『〝“‘([{#$@")
_NARROW = frozenset("iljtfIr.,;:'\"!|()[]{}`")
_WIDE_LATIN = frozenset("mwMW@%&")


def char_width(ch: str) -> float:
    o = ord(ch)
    if ch == "\t":
        return 2.0
    if ch == " ":
        return 0.30
    if (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF or 0xA960 <= o <= 0xA97F or 0xAC00 <= o <= 0xD7A3
            or 0xF900 <= o <= 0xFAFF or 0xFE10 <= o <= 0xFE19 or 0xFE30 <= o <= 0xFE6F
            or 0xFF00 <= o <= 0xFF60 or 0xFFE0 <= o <= 0xFFE6 or 0x20000 <= o <= 0x3FFFD):
        return 1.0
    if ch in _NARROW:
        return 0.30
    if ch in _WIDE_LATIN:
        return 0.72
    if ch.isupper():
        return 0.66
    if ch.isdigit():
        return 0.55
    return 0.52


def text_width(text: str) -> float:
    return sum(char_width(c) for c in text)


def is_wide(ch: str) -> bool:
    return char_width(ch) >= 0.95


@dataclass
class Piece:
    text: str
    color: str | None = None
    bold: bool = False
    italic: bool = False


@dataclass
class _Unit:
    text: str
    width: float
    color: str | None
    bold: bool
    italic: bool
    is_space: bool = False
    is_break: bool = False


def _tokenize(runs: list) -> list:
    units: list = []
    for run in runs:
        buf = ""
        for ch in run.text:
            if ch == "\n":
                if buf:
                    units.append(_Unit(buf, text_width(buf), run.color, run.bold, run.italic))
                    buf = ""
                units.append(_Unit("", 0.0, run.color, run.bold, run.italic, is_break=True))
                continue
            if ch == " " or is_wide(ch):
                if buf:
                    units.append(_Unit(buf, text_width(buf), run.color, run.bold, run.italic))
                    buf = ""
                units.append(_Unit(ch, char_width(ch), run.color, run.bold, run.italic, is_space=(ch == " ")))
                continue
            buf += ch
        if buf:
            units.append(_Unit(buf, text_width(buf), run.color, run.bold, run.italic))
    return units


def _to_pieces(units: list) -> list:
    pieces: list = []
    for u in units:
        if pieces and pieces[-1].color == u.color and pieces[-1].bold == u.bold and pieces[-1].italic == u.italic:
            pieces[-1].text += u.text
        else:
            pieces.append(Piece(u.text, u.color, u.bold, u.italic))
    return [p for p in pieces if p.text]


def wrap_runs(runs: list, max_width_em: float) -> list:
    if max_width_em <= 0:
        return []
    units = _tokenize(runs)
    lines: list = [[]]
    width = 0.0
    for i, unit in enumerate(units):
        if unit.is_break:
            lines.append([])
            width = 0.0
            continue
        if unit.is_space and not lines[-1]:
            continue
        overflow = width + unit.width > max_width_em + 1e-9
        hangs = len(unit.text) == 1 and unit.text in CLOSING_PUNCT
        if overflow and lines[-1] and not hangs:
            lines.append([])
            width = 0.0
            if unit.is_space:
                continue
        if (len(unit.text) == 1 and unit.text in OPENING_PUNCT and i + 1 < len(units)
                and width + unit.width + units[i + 1].width > max_width_em + 1e-9 and lines[-1]):
            lines.append([])
            width = 0.0
        lines[-1].append(unit)
        width += unit.width
    return [_to_pieces(line) for line in lines if line] or [[]]


def wrap_plain(text: str, max_width_em: float, color: str | None = None) -> list:
    return wrap_runs([Run(text, color)], max_width_em)


# ══════════════════════════════════════════════════════════════════════════
# 卡片 SVG（原 backend/render/card_svg.py）
# ══════════════════════════════════════════════════════════════════════════

def _fonts(stack: str) -> str:
    return stack.replace('"', "'")


@dataclass
class _Geom:
    s: float
    width: float
    margin: float
    pad_x: float
    rule_top: float
    lh: float

    @property
    def paper_x(self) -> float:
        return self.margin

    @property
    def paper_w(self) -> float:
        return self.width - 2 * self.margin

    @property
    def content_x(self) -> float:
        return self.paper_x + self.pad_x

    @property
    def content_w(self) -> float:
        return self.paper_w - 2 * self.pad_x


class _Builder:
    def __init__(self, g: _Geom, theme: Theme) -> None:
        self.g = g
        self.t = theme
        self.body: list = []
        self.row = 0.0

    def baseline(self, row=None) -> float:
        r = self.row if row is None else row
        return self.g.margin + self.g.rule_top + r * self.g.lh - 11 * self.g.s

    def rule_y(self, row: float) -> float:
        return self.g.margin + self.g.rule_top + row * self.g.lh

    def advance(self, rows: float = 1.0) -> None:
        self.row += rows

    def line(self, pieces, *, x, size, family, color, weight="400", letter_spacing=0.0, opacity=1.0,
             anchor="start", row=None) -> None:
        if not pieces:
            return
        spans = []
        for p in pieces:
            attrs = ""
            if p.color and p.color != color:
                attrs += f' fill="{p.color}"'
            if p.bold:
                attrs += ' font-weight="600"'
            if p.italic:
                attrs += ' font-style="italic"'
            spans.append(f"<tspan{attrs}>{xml_escape(p.text)}</tspan>")
        ls = f' letter-spacing="{letter_spacing:.2f}"' if letter_spacing else ""
        op = f' opacity="{opacity}"' if opacity != 1.0 else ""
        anc = f' text-anchor="{anchor}"' if anchor != "start" else ""
        self.body.append(
            f'<text x="{x:.1f}" y="{self.baseline(row):.1f}" fill="{color}" '
            f'font-family="{family}" font-size="{size:.1f}" '
            f'font-weight="{weight}"{ls}{op}{anc}>{"".join(spans)}</text>')

    def plain(self, text: str, **kw) -> None:
        if not text:
            return
        self.line([Piece(text)], **kw)

    def hairline(self, row: float, *, color: str, width: float = 1.0) -> None:
        y = self.rule_y(row) - self.g.lh * 0.55
        self.body.append(
            f'<line x1="{self.g.content_x:.1f}" y1="{y:.1f}" '
            f'x2="{self.g.content_x + self.g.content_w:.1f}" y2="{y:.1f}" '
            f'stroke="{color}" stroke-width="{width * self.g.s:.2f}"/>')

    def rect(self, x, y, w, h, *, fill, rx=0.0, opacity=1.0) -> None:
        op = f' opacity="{opacity}"' if opacity != 1.0 else ""
        self.body.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                         f'rx="{rx:.1f}" fill="{fill}"{op}/>')

    def circle(self, cx, cy, r, *, fill, opacity=1.0) -> None:
        op = f' opacity="{opacity}"' if opacity != 1.0 else ""
        self.body.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}"{op}/>')


def _render_chips(b: _Builder, labels: list, color: str) -> None:
    g, t = b.g, b.t
    body_font = _fonts(t.body_font)
    size = 25 * g.s
    pad = 20 * g.s
    gap = 11 * g.s
    h = 40 * g.s
    x = g.content_x
    top = b.rule_y(b.row) - g.lh + (g.lh - h) / 2 + 4 * g.s
    for label, selected in labels:
        w = text_width(label) * size + pad * 2
        if x + w > g.content_x + g.content_w and x > g.content_x:
            b.advance()
            x = g.content_x
            top = b.rule_y(b.row) - g.lh + (g.lh - h) / 2 + 4 * g.s
        if selected:
            b.rect(x, top, w, h, fill=color, rx=h / 2)
        else:
            b.rect(x, top, w, h, fill=t.ink, rx=h / 2, opacity=0.05)
        b.body.append(
            f'<text x="{x + w / 2:.1f}" y="{top + h / 2 + size * 0.36:.1f}" '
            f'text-anchor="middle" font-family="{body_font}" '
            f'font-size="{size:.1f}" font-weight="{"600" if selected else "400"}" '
            f'fill="{"#ffffff" if selected else t.ink_muted}">'
            f"{xml_escape(label)}</text>")
        x += w + gap
    b.advance()


def _card_field(b: _Builder, f, section_color: str) -> None:
    g, t = b.g, b.t
    body_font = _fonts(t.body_font)
    indent = 26 * g.s
    x = g.content_x + indent
    avail_em = (g.content_w - indent) / (30 * g.s)
    b.plain(f.label, x=x, size=21 * g.s, family=body_font, color=t.ink_muted, letter_spacing=1.2 * g.s)
    b.advance()
    if f.chips:
        _render_chips(b, [(c.label, c.selected) for c in f.chips], section_color)
        b.advance(0.35)
        return
    size = 30 * g.s
    if f.numbered:
        num_indent = 46 * g.s
        for i, runs in enumerate(f.lines, start=1):
            lines = wrap_runs(runs, avail_em - num_indent / size)
            cy = b.rule_y(b.row) - g.lh * 0.5 - 4 * g.s
            r = 15 * g.s
            b.circle(x + r, cy, r, fill=section_color, opacity=0.14)
            b.body.append(
                f'<text x="{x + r:.1f}" y="{cy + 7 * g.s:.1f}" text-anchor="middle" '
                f'font-family="{body_font}" font-size="{19 * g.s:.1f}" '
                f'font-weight="600" fill="{section_color}">{i}</text>')
            for j, pieces in enumerate(lines):
                b.line(pieces, x=x + num_indent, size=size, family=body_font, color=section_color,
                       row=b.row if j == 0 else b.row + j)
            b.advance(len(lines))
    else:
        for runs in f.lines:
            for pieces in wrap_runs(runs, avail_em):
                b.line(pieces, x=x, size=size, family=body_font, color=section_color)
                b.advance()
    b.advance(0.4)


def _card_body(b: _Builder, doc: RenderedDoc) -> None:
    g, t = b.g, b.t
    display = _fonts(t.display_font)
    body_font = _fonts(t.body_font)
    b.plain(doc.template_name, x=g.content_x, size=20 * g.s, family=body_font, color=t.ink_muted,
            letter_spacing=3.4 * g.s)
    b.advance(1.4)
    b.plain(doc.date_display, x=g.content_x, size=62 * g.s, family=display, color=t.ink, weight="600",
            letter_spacing=1.5 * g.s)
    b.advance(1.5)
    meta = f"週{doc.weekday}　·　{doc.word_count} 字"
    b.plain(meta, x=g.content_x, size=22 * g.s, family=body_font, color=t.ink_muted, letter_spacing=1.4 * g.s)
    b.advance()
    if doc.title:
        for pieces in wrap_plain(doc.title, g.content_w / (32 * g.s)):
            b.line(pieces, x=g.content_x, size=32 * g.s, family=display, color=t.ink, weight="500")
            b.advance()
    b.advance(0.35)
    b.hairline(b.row, color=t.hairline, width=1.2)
    b.advance(0.9)
    for section in doc.sections:
        icon_size = 34 * g.s
        top = b.rule_y(b.row) - g.lh + (g.lh - icon_size) / 2 + 4 * g.s
        if section.icon:
            b.rect(g.content_x, top, icon_size, icon_size, fill=section.color, rx=9 * g.s)
            b.body.append(
                f'<text x="{g.content_x + icon_size / 2:.1f}" '
                f'y="{top + icon_size / 2 + 8 * g.s:.1f}" text-anchor="middle" '
                f'font-family="{body_font}" font-size="{20 * g.s:.1f}" '
                f'fill="#ffffff">{xml_escape(section.icon)}</text>')
        title_x = g.content_x + (icon_size + 14 * g.s if section.icon else 0)
        b.plain(section.title, x=title_x, size=31 * g.s, family=display, color=t.ink, weight="600")
        b.advance(1.15)
        bar_top = b.rule_y(b.row) - g.lh
        for f in section.fields:
            _card_field(b, f, section.color)
        bar_bottom = b.rule_y(b.row) - g.lh * 0.9
        b.body.insert(
            0,
            f'<rect x="{g.content_x + 4 * g.s:.1f}" y="{bar_top:.1f}" '
            f'width="{2.5 * g.s:.1f}" height="{max(0.0, bar_bottom - bar_top):.1f}" '
            f'rx="{1.5 * g.s:.1f}" fill="{section.color}" opacity="0.35"/>')
        b.advance(0.6)
    if doc.has_closing:
        b.advance(0.4)
        start = b.rule_y(b.row) - g.lh
        b.plain(doc.closing_label, x=g.content_x + 26 * g.s, size=21 * g.s, family=body_font, color=t.accent,
                letter_spacing=1.2 * g.s)
        b.advance()
        size = 30 * g.s
        avail = (g.content_w - 26 * g.s) / size
        for runs in doc.closing_lines:
            for pieces in wrap_runs(runs, avail):
                b.line(pieces, x=g.content_x + 26 * g.s, size=size, family=display, color=t.ink)
                b.advance()
        end = b.rule_y(b.row) - g.lh * 0.9
        b.body.append(
            f'<rect x="{g.content_x:.1f}" y="{start:.1f}" width="{3.5 * g.s:.1f}" '
            f'height="{max(0.0, end - start):.1f}" rx="{2 * g.s:.1f}" '
            f'fill="{t.accent}"/>')
    b.advance(0.8)


def _paper_pattern(paper: str, g: _Geom, t: Theme, height: float) -> str:
    if paper == "plain":
        return ""
    out: list = []
    x1 = g.paper_x + g.pad_x * 0.45
    x2 = g.paper_x + g.paper_w - g.pad_x * 0.45
    sw = 1.1 * g.s
    if paper in ("ruled", "grid"):
        row = 0
        while True:
            y = g.margin + g.rule_top + row * g.lh
            if y > height - g.margin - g.lh * 0.4:
                break
            out.append(f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" '
                       f'stroke="{t.rule}" stroke-width="{sw:.2f}"/>')
            row += 1
    if paper == "grid":
        x = x1
        while x <= x2:
            out.append(f'<line x1="{x:.1f}" y1="{g.margin + g.rule_top - g.lh:.1f}" '
                       f'x2="{x:.1f}" y2="{height - g.margin - g.lh * 0.4:.1f}" '
                       f'stroke="{t.rule}" stroke-width="{sw:.2f}"/>')
            x += g.lh
    if paper == "dotted":
        row = 0
        while True:
            y = g.margin + g.rule_top + row * g.lh
            if y > height - g.margin - g.lh * 0.4:
                break
            x = x1
            while x <= x2:
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{1.6 * g.s:.2f}" fill="{t.rule}"/>')
                x += g.lh
            row += 1
    if paper in ("ruled", "grid"):
        mx = g.content_x - 24 * g.s
        out.append(f'<line x1="{mx:.1f}" y1="{g.margin + g.lh * 0.5:.1f}" x2="{mx:.1f}" '
                   f'y2="{height - g.margin - g.lh * 0.5:.1f}" '
                   f'stroke="{t.margin_rule}" stroke-width="{1.6 * g.s:.2f}"/>')
        for k in range(3):
            cy = g.margin + g.lh * (2.6 + k * 4.2)
            if cy < height - g.margin - g.lh:
                out.append(f'<circle cx="{g.paper_x + g.pad_x * 0.28:.1f}" cy="{cy:.1f}" '
                           f'r="{8 * g.s:.1f}" fill="{t.backdrop}" opacity="0.9"/>')
    return "".join(out)


def render_document_svg(doc: RenderedDoc, *, width: int = 1080, min_height: int | None = None,
                        paper: str = "ruled") -> str:
    theme = get_theme(doc.theme)
    s = width / 1080
    g = _Geom(s=s, width=float(width), margin=30 * s, pad_x=66 * s, rule_top=100 * s, lh=54 * s)
    b = _Builder(g, theme)
    _card_body(b, doc)
    height = g.margin * 2 + g.rule_top + b.row * g.lh + 30 * s
    if min_height:
        height = max(height, float(min_height))
    footer_y = height - g.margin - 26 * s
    footer = (
        f'<line x1="{g.content_x:.1f}" y1="{footer_y - 26 * s:.1f}" '
        f'x2="{g.content_x + g.content_w:.1f}" y2="{footer_y - 26 * s:.1f}" '
        f'stroke="{theme.hairline}" stroke-width="{1 * s:.2f}"/>'
        f'<text x="{g.content_x:.1f}" y="{footer_y:.1f}" '
        f'font-family="{_fonts(theme.body_font)}" font-size="{18 * s:.1f}" '
        f'fill="{theme.ink_muted}" letter-spacing="{2 * s:.2f}">'
        f"{xml_escape(doc.date_display)}</text>"
        f'<text x="{g.content_x + g.content_w:.1f}" y="{footer_y:.1f}" '
        f'text-anchor="end" font-family="{_fonts(theme.body_font)}" '
        f'font-size="{18 * s:.1f}" fill="{theme.ink_muted}" '
        f'letter-spacing="{2 * s:.2f}">'
        f"{xml_escape(doc.template_name)}</text>")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height:.0f}" viewBox="0 0 {width} {height:.0f}" '
        f'role="img" aria-label="{xml_escape(doc.date_display)} 日記卡片">'
        "<defs>"
        '<filter id="cardShadow" x="-20%" y="-20%" width="140%" height="140%">'
        f'<feDropShadow dx="0" dy="{12 * s:.1f}" stdDeviation="{16 * s:.1f}" '
        'flood-color="#000000" flood-opacity="0.18"/>'
        "</filter>"
        '<linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#ffffff" stop-opacity="0.55"/>'
        '<stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>'
        "</linearGradient>"
        "</defs>"
        f'<rect width="{width}" height="{height:.0f}" fill="{theme.backdrop}"/>'
        f'<rect x="{g.paper_x:.1f}" y="{g.margin:.1f}" width="{g.paper_w:.1f}" '
        f'height="{height - 2 * g.margin:.1f}" rx="{20 * s:.1f}" '
        f'fill="{theme.paper}" stroke="{theme.paper_edge}" '
        f'stroke-width="{1 * s:.2f}" filter="url(#cardShadow)"/>'
        f'<rect x="{g.paper_x:.1f}" y="{g.margin:.1f}" width="{g.paper_w:.1f}" '
        f'height="{min(160 * s, height / 3):.1f}" rx="{20 * s:.1f}" '
        'fill="url(#sheen)" opacity="0.5"/>'
        + _paper_pattern(paper, g, theme, height)
        + "".join(b.body)
        + footer
        + "</svg>")


# ══════════════════════════════════════════════════════════════════════════
# 小 Jinja：只收原本版型用到的語法（原本用 Jinja2；SYNOX 的環境沒有它，pack 也不能自己裝套件）
#   {{ 運算式 }}、{% if/elif/else/endif %}、{% for x in … %}…{% else %}…{% endfor %}、{% set x = … %}（含 ns.x）、
#   {% include "檔名" %}、{# 註解 #}；左右的 - 去空白；trim_blocks＋lstrip_blocks；自動跳脫（同 Jinja 的 autoescape）。
#   運算式：字串、數字、true/false/none、a.b、a[0]、a[:6]、not／and／or、x if c else y、== != < > <= >= in、+ - * / % ~、
#   濾器 runs、selectattr、rejectattr、map、join、list、format、length、default、upper、lower、first、last、safe、e、trim、string、int。
#   取值只查 dict 與 list（輸出用的資料先轉成 dict），不碰物件的屬性。
# ══════════════════════════════════════════════════════════════════════════

class TemplateError(Exception):
    """版型寫錯了（訊息給人看）。"""


class Markup(str):
    """已經跳脫過、可以原樣輸出的字（同 markupsafe.Markup 的用途）。"""


class _Undefined:
    def __bool__(self):
        return False

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __str__(self):
        return ""


UNDEF = _Undefined()


def escape(value) -> Markup:
    if isinstance(value, Markup):
        return value
    if value is UNDEF:
        return Markup("")
    text = str(value)
    return Markup(text.replace("&", "&amp;").replace(">", "&gt;").replace("<", "&lt;")
                  .replace("'", "&#39;").replace('"', "&#34;"))


_BEGIN = re.compile(r"\{\{|\{%|\{#")
_CLOSE = {"{{": "}}", "{%": "%}", "{#": "#}"}


def _find_close(src: str, i: int, close: str, where: str) -> int:
    quote = ""
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif close != "#}" and ch in "'\"":
            quote = ch
        elif src.startswith(close, i):
            return i
        i += 1
    raise TemplateError(f"{where}：{close} 沒有關上")


def _lex(src: str, where: str) -> list:
    src = "\n".join(re.split(r"\r\n|\r|\n", src))
    if src.endswith("\n"):
        src = src[:-1]                                      # keep_trailing_newline=False
    tokens: list = []
    pos, line_starting = 0, True
    while pos <= len(src):
        m = _BEGIN.search(src, pos)
        if not m:
            if pos < len(src):
                tokens.append(("text", src[pos:]))
            break
        kind = m.group()
        sign = src[m.end()] if m.end() < len(src) and src[m.end()] in "-+" else ""
        text = src[pos:m.start()]
        if sign == "-":
            text = text.rstrip()
        elif sign != "+" and kind != "{{":                 # lstrip_blocks
            l_pos = text.rfind("\n") + 1
            if (l_pos > 0 or line_starting) and not re.search(r"[^ \t]", text[l_pos:]):
                text = text[:l_pos]
        if text:
            tokens.append(("text", text))
        start = m.end() + (1 if sign else 0)
        end = _find_close(src, start, _CLOSE[kind], where)
        inner = src[start:end]
        after = end + 2
        consumed = ""
        stripped = inner.rstrip()
        if stripped.endswith("-") and kind != "{#" or kind == "{#" and inner.endswith("-"):
            inner = stripped[:-1]
            j = after
            while j < len(src) and src[j].isspace():
                j += 1
            consumed, after = src[after:j], j
        elif stripped.endswith("+") and kind != "{{":
            inner = stripped[:-1]
        elif kind != "{{" and src.startswith("\n", after):  # trim_blocks
            consumed, after = "\n", after + 1
        line_starting = consumed.endswith("\n")
        if kind == "{{":
            tokens.append(("var", inner.strip()))
        elif kind == "{%":
            tokens.append(("block", inner.strip()))
        pos = after
    return tokens


_EXPR_TOKEN = re.compile(
    r"\s*(?:(?P<str>'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")|(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>==|!=|<=|>=|//|[.\[\](),|=:<>+\-*/%~]))")


def _unquote(s: str) -> str:
    body = s[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)


class _Expr:
    """運算式的 parser（recursive descent，優先順序照 Jinja）。"""

    def __init__(self, src: str, where: str):
        self.where = where
        self.toks: list = []
        pos = 0
        src = src.rstrip()
        while pos < len(src):
            m = _EXPR_TOKEN.match(src, pos)
            if not m or m.end() == pos:
                raise TemplateError(f"{where}：看不懂「{src[pos:pos + 12]}」")
            if m.group("str") is not None:
                self.toks.append(("str", _unquote(m.group("str"))))
            elif m.group("num") is not None:
                n = m.group("num")
                self.toks.append(("num", float(n) if "." in n else int(n)))
            elif m.group("name") is not None:
                self.toks.append(("name", m.group("name")))
            else:
                self.toks.append(("op", m.group("op")))
            pos = m.end()
        self.i = 0

    def peek(self, kind=None, value=None):
        if self.i >= len(self.toks):
            return None
        tok = self.toks[self.i]
        if kind and tok[0] != kind or value is not None and tok[1] != value:
            return None
        return tok

    def take(self, kind=None, value=None):
        tok = self.peek(kind, value)
        if tok is None:
            got = self.toks[self.i][1] if self.i < len(self.toks) else "（結尾）"
            raise TemplateError(f"{self.where}：這裡要 {value or kind}，看到 {got}")
        self.i += 1
        return tok

    def done(self):
        if self.i < len(self.toks):
            raise TemplateError(f"{self.where}：多了「{self.toks[self.i][1]}」")

    def expression(self):
        node = self.or_()
        if self.peek("name", "if"):
            self.take()
            test = self.or_()
            other = None
            if self.peek("name", "else"):
                self.take()
                other = self.expression()
            node = ("cond", test, node, other)
        return node

    def or_(self):
        node = self.and_()
        while self.peek("name", "or"):
            self.take()
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.peek("name", "and"):
            self.take()
            node = ("and", node, self.not_())
        return node

    def not_(self):
        if self.peek("name", "not"):
            self.take()
            return ("not", self.not_())
        return self.compare()

    def compare(self):
        node = self.add()
        while True:
            tok = self.peek("op")
            if tok and tok[1] in ("==", "!=", "<", ">", "<=", ">="):
                self.take()
                node = ("cmp", tok[1], node, self.add())
            elif self.peek("name", "in"):
                self.take()
                node = ("cmp", "in", node, self.add())
            elif self.peek("name", "not") and self.i + 1 < len(self.toks) and self.toks[self.i + 1] == ("name", "in"):
                self.i += 2
                node = ("cmp", "not in", node, self.add())
            else:
                return node

    def add(self):
        node = self.mul()
        while True:
            tok = self.peek("op")
            if tok and tok[1] in ("+", "-", "~"):
                self.take()
                node = ("bin", tok[1], node, self.mul())
            else:
                return node

    def mul(self):
        node = self.unary()
        while True:
            tok = self.peek("op")
            if tok and tok[1] in ("*", "/", "//", "%"):
                self.take()
                node = ("bin", tok[1], node, self.unary())
            else:
                return node

    def unary(self):
        if self.peek("op", "-"):
            self.take()
            return ("neg", self.unary())
        node = self.postfix(self.primary())
        while self.peek("op", "|"):
            self.take()
            name = self.take("name")[1]
            args, kwargs = [], {}
            if self.peek("op", "("):
                args, kwargs = self.call_args()
            node = ("filter", name, node, args, kwargs)
        return node

    def primary(self):
        tok = self.take()
        if tok[0] in ("str", "num"):
            return ("const", tok[1])
        if tok[0] == "name":
            word = tok[1]
            if word in ("true", "True"):
                return ("const", True)
            if word in ("false", "False"):
                return ("const", False)
            if word in ("none", "None"):
                return ("const", None)
            return ("name", word)
        if tok == ("op", "("):
            node = self.expression()
            self.take("op", ")")
            return node
        if tok == ("op", "["):
            items = []
            while not self.peek("op", "]"):
                items.append(self.expression())
                if not self.peek("op", ","):
                    break
                self.take()
            self.take("op", "]")
            return ("list", items)
        raise TemplateError(f"{self.where}：看不懂「{tok[1]}」")

    def postfix(self, node):
        while True:
            if self.peek("op", "."):
                self.take()
                tok = self.take()
                node = ("item", node, ("const", tok[1]))
            elif self.peek("op", "["):
                self.take()
                if self.peek("op", ":"):
                    lo = None
                else:
                    lo = self.expression()
                if self.peek("op", ":"):
                    self.take()
                    hi = None if self.peek("op", "]") else self.expression()
                    node = ("slice", node, lo, hi)
                else:
                    node = ("item", node, lo)
                self.take("op", "]")
            elif self.peek("op", "("):
                args, kwargs = self.call_args()
                node = ("call", node, args, kwargs)
            else:
                return node

    def call_args(self):
        self.take("op", "(")
        args, kwargs = [], {}
        while not self.peek("op", ")"):
            if self.peek("name") and self.i + 1 < len(self.toks) and self.toks[self.i + 1] == ("op", "="):
                key = self.take()[1]
                self.take()
                kwargs[key] = self.expression()
            else:
                args.append(self.expression())
            if not self.peek("op", ","):
                break
            self.take()
        self.take("op", ")")
        return args, kwargs


def _parse_expr(src: str, where: str):
    p = _Expr(src, where)
    node = p.expression()
    p.done()
    return node


def _parse(tokens: list, where: str) -> list:
    """tokens → 敘述樹。"""
    root: list = []
    stack: list = [("root", root, None)]

    def body():
        return stack[-1][1]

    for kind, value in tokens:
        if kind == "text":
            body().append(("text", value))
        elif kind == "var":
            body().append(("out", _parse_expr(value, where)))
        else:
            word, _, rest = value.partition(" ")
            rest = rest.strip()
            if word == "if":
                node = ["if", [(_parse_expr(rest, where), [])], None]
                body().append(node)
                stack.append(("if", node[1][0][1], node))
            elif word in ("elif", "else") and stack[-1][0] == "if":
                node = stack.pop()[2]
                if word == "elif":
                    node[1].append((_parse_expr(rest, where), []))
                    stack.append(("if", node[1][-1][1], node))
                else:
                    node[2] = []
                    stack.append(("if-else", node[2], node))
            elif word == "endif":
                if stack[-1][0] not in ("if", "if-else"):
                    raise TemplateError(f"{where}：多了一個 endif")
                stack.pop()
            elif word == "for":
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)\s+in\s+(.+)$", rest)
                if not m:
                    raise TemplateError(f"{where}：for 要寫成 for x in …")
                names = [n.strip() for n in m.group(1).split(",")]
                node = ["for", names, _parse_expr(m.group(2), where), [], None]
                body().append(node)
                stack.append(("for", node[3], node))
            elif word == "else" and stack[-1][0] == "for":
                node = stack.pop()[2]
                node[4] = []
                stack.append(("for-else", node[4], node))
            elif word == "endfor":
                if stack[-1][0] not in ("for", "for-else"):
                    raise TemplateError(f"{where}：多了一個 endfor")
                stack.pop()
            elif word == "include":
                body().append(("include", _parse_expr(rest, where)))
            elif word == "set":
                target, eq, expr = rest.partition("=")
                target = target.strip()
                if not eq or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$", target):
                    raise TemplateError(f"{where}：set 要寫成 set x = …")
                body().append(("set", target.split("."), _parse_expr(expr, where)))
            else:
                raise TemplateError(f"{where}：不支援 {{% {word} %}}（這個版型引擎只收 if／for／set／include）")
    if len(stack) > 1:
        raise TemplateError(f"{where}：{stack[-1][0].split('-')[0]} 沒有結束（少了 end{stack[-1][0].split('-')[0]}）")
    return root


class _Renderer:
    def __init__(self, loader, filters):
        self.loader = loader          # name → 樹（找不到丟 TemplateError）
        self.filters = filters        # name → 函式（dict）
        self.depth = 0

    def lookup(self, scopes, name):
        for scope in reversed(scopes):
            if name in scope:
                return scope[name]
        return UNDEF

    def ev(self, node, scopes):
        op = node[0]
        if op == "const":
            return node[1]
        if op == "name":
            return self.lookup(scopes, node[1])
        if op == "item":
            obj, key = self.ev(node[1], scopes), self.ev(node[2], scopes)
            if isinstance(obj, dict):
                return obj.get(key, UNDEF)
            if isinstance(obj, (list, tuple, str)) and isinstance(key, int) and not isinstance(key, bool):
                try:
                    return obj[key]
                except IndexError:
                    return UNDEF
            return UNDEF
        if op == "slice":
            obj = self.ev(node[1], scopes)
            lo = None if node[2] is None else self.ev(node[2], scopes)
            hi = None if node[3] is None else self.ev(node[3], scopes)
            if isinstance(obj, (list, tuple, str)):
                return obj[lo:hi]
            return UNDEF
        if op == "not":
            return not self.ev(node[1], scopes)
        if op == "and":
            left = self.ev(node[1], scopes)
            return self.ev(node[2], scopes) if left else left
        if op == "or":
            left = self.ev(node[1], scopes)
            return left if left else self.ev(node[2], scopes)
        if op == "cond":
            if self.ev(node[1], scopes):
                return self.ev(node[2], scopes)
            return UNDEF if node[3] is None else self.ev(node[3], scopes)
        if op == "cmp":
            a, b = self.ev(node[2], scopes), self.ev(node[3], scopes)
            how = node[1]
            try:
                if how == "==":
                    return a == b
                if how == "!=":
                    return a != b
                if how == "<":
                    return a < b
                if how == ">":
                    return a > b
                if how == "<=":
                    return a <= b
                if how == ">=":
                    return a >= b
                if how == "in":
                    return a in b
                return a not in b
            except TypeError:
                return False
        if op == "bin":
            a, b = self.ev(node[2], scopes), self.ev(node[3], scopes)
            how = node[1]
            if how == "~":
                return str(a) + str(b)
            try:
                if how == "+":
                    return a + b
                if how == "-":
                    return a - b
                if how == "*":
                    return a * b
                if how == "/":
                    return a / b
                if how == "//":
                    return a // b
                return a % b
            except (TypeError, ZeroDivisionError, ValueError):
                return UNDEF
        if op == "neg":
            v = self.ev(node[1], scopes)
            return -v if isinstance(v, (int, float)) else UNDEF
        if op == "list":
            return [self.ev(n, scopes) for n in node[1]]
        if op == "call":
            target = node[1]
            kwargs = {k: self.ev(v, scopes) for k, v in node[3].items()}
            args = [self.ev(v, scopes) for v in node[2]]
            if target == ("name", "namespace"):
                return dict(*args, **kwargs)
            if target == ("name", "range"):
                ints = [int(a) for a in args]
                return list(range(*ints))
            return UNDEF
        if op == "filter":
            fn = self.filters.get(node[1])
            if fn is None:
                raise TemplateError(f"沒有這個濾器：{node[1]}")
            value = self.ev(node[2], scopes)
            args = [self.ev(v, scopes) for v in node[3]]
            kwargs = {k: self.ev(v, scopes) for k, v in node[4].items()}
            return fn(self, value, *args, **kwargs)
        raise TemplateError(f"看不懂的運算式 {op}")

    def run(self, tree, scopes, out):
        for node in tree:
            op = node[0]
            if op == "text":
                out.append(node[1])
            elif op == "out":
                value = self.ev(node[1], scopes)
                out.append(escape(value))
            elif op == "if":
                for test, body in node[1]:
                    if self.ev(test, scopes):
                        self.run(body, scopes, out)
                        break
                else:
                    if node[2] is not None:
                        self.run(node[2], scopes, out)
            elif op == "for":
                items = self.ev(node[2], scopes)
                if isinstance(items, dict):
                    items = list(items)
                items = list(items) if items is not UNDEF and items is not None else []
                if not items and node[4] is not None:
                    self.run(node[4], scopes, out)
                    continue
                n = len(items)
                for i, item in enumerate(items):
                    scope = {"loop": {"index": i + 1, "index0": i, "first": i == 0, "last": i == n - 1,
                                      "length": n, "revindex": n - i, "revindex0": n - i - 1}}
                    names = node[1]
                    if len(names) == 1:
                        scope[names[0]] = item
                    else:
                        values = list(item) if isinstance(item, (list, tuple)) else []
                        for k, name in enumerate(names):
                            scope[name] = values[k] if k < len(values) else UNDEF
                    self.run(node[3], scopes + [scope], out)
            elif op == "set":
                target, value = node[1], self.ev(node[2], scopes)
                if len(target) == 1:
                    scopes[-1][target[0]] = value
                else:
                    ns = self.lookup(scopes, target[0])
                    if not isinstance(ns, dict):
                        raise TemplateError(f"{target[0]} 不是 namespace()，不能 set {'.'.join(target)}")
                    ns[target[1]] = value
            elif op == "include":
                name = self.ev(node[1], scopes)
                if self.depth > 10:
                    raise TemplateError("include 太多層")
                self.depth += 1
                try:
                    self.run(self.loader(str(name)), scopes, out)
                finally:
                    self.depth -= 1


def _f_runs(r, value, *a, **k):
    return runs_to_html(value if isinstance(value, list) else [])


def _attr(item, name):
    if isinstance(item, dict):
        return item.get(name, UNDEF)
    return UNDEF


def _f_selectattr(r, value, name, *test):
    out = []
    for item in value or []:
        v = _attr(item, name)
        if test and test[0] in ("equalto", "eq", "==", "sameas"):
            ok = v == (test[1] if len(test) > 1 else None)
        else:
            ok = bool(v)
        if ok:
            out.append(item)
    return out


def _f_rejectattr(r, value, name, *test):
    keep = _f_selectattr(r, value, name, *test)
    return [item for item in value or [] if not any(item is k for k in keep)]


def _f_map(r, value, *args, attribute=None, default=None):
    if attribute is not None:
        out = []
        for item in value or []:
            v = _attr(item, attribute)
            out.append(default if v is UNDEF and default is not None else v)
        return out
    if args:
        fn = r.filters.get(args[0])
        if fn is None:
            raise TemplateError(f"沒有這個濾器：{args[0]}")
        return [fn(r, item, *args[1:]) for item in value or []]
    return list(value or [])


def _f_join(r, value, d="", attribute=None):
    items = list(value or [])
    if attribute is not None:
        items = [_attr(i, attribute) for i in items]
    return Markup(str(escape(d)).join(str(escape(i)) for i in items))


def _f_format(r, value, *args, **kwargs):
    return str(value) % (kwargs or tuple(args))


def _f_default(r, value, default="", boolean=False):
    if value is UNDEF or boolean and not value:
        return default
    return value


FILTERS = {
    "runs": _f_runs,
    "selectattr": _f_selectattr,
    "rejectattr": _f_rejectattr,
    "map": _f_map,
    "join": _f_join,
    "list": lambda r, v, *a: list(v) if v is not UNDEF and v is not None else [],
    "format": _f_format,
    "length": lambda r, v: len(v) if v is not UNDEF and v is not None else 0,
    "count": lambda r, v: len(v) if v is not UNDEF and v is not None else 0,
    "default": _f_default,
    "d": _f_default,
    "upper": lambda r, v: str(v).upper(),
    "lower": lambda r, v: str(v).lower(),
    "trim": lambda r, v: str(v).strip(),
    "first": lambda r, v: next(iter(v), UNDEF) if v else UNDEF,
    "last": lambda r, v: list(v)[-1] if v else UNDEF,
    "safe": lambda r, v: Markup(str(v)),
    "e": lambda r, v: escape(v),
    "escape": lambda r, v: escape(v),
    "string": lambda r, v: str(v),
    "int": lambda r, v, d=0: int(v) if str(v).strip().lstrip("-").isdigit() else d,
}


def runs_to_html(runs: list) -> Markup:
    """顏色 run → 行內標記（原 artifact_html.runs_to_html）：作者選的顏色也放進 --ink，深色模式的版型才調得亮。"""
    out: list = []
    for run in runs:
        text = html.escape(run.text).replace("  ", " &nbsp;")
        if not text:
            continue
        if run.bold:
            text = f"<strong>{text}</strong>"
        if run.italic:
            text = f"<em>{text}</em>"
        if run.color:
            ink = html.escape(run.color, quote=True)
            text = f'<span style="--ink:{ink};color:var(--ink)">{text}</span>'
        out.append(text)
    return Markup("".join(out) or "&nbsp;")


def render_layout(loader, name: str, context: dict) -> str:
    """版型 name（例 letter.html.j2）＋ context → 一頁 HTML。loader(name) 回原始文字（找不到回 None）。"""
    cache: dict = {}

    def tree(n: str):
        if n not in cache:
            src = loader(n)
            if src is None:
                raise TemplateError(f"找不到版型檔：{n}")
            cache[n] = _parse(_lex(src, n), n)
        return cache[n]

    out: list = []
    _Renderer(tree, FILTERS).run(tree(name), [dict(context)], out)
    return "".join(out)


class _DateView(dict):
    """版型裡的日期：{{ doc.entry_date.day }} 取得到、{{ doc.entry_date }} 印出 YYYY-MM-DD。"""

    def __str__(self):
        return "%04d-%02d-%02d" % (self["year"], self["month"], self["day"])


def doc_context(doc: RenderedDoc) -> dict:
    """RenderedDoc → 版型看到的 doc（只有 dict 與 list）。"""
    def fld(f: RenderedField) -> dict:
        return {"id": f.id, "label": f.label, "kind": f.kind, "color": f.color,
                "chips": [{"label": c.label, "selected": c.selected} for c in f.chips],
                "lines": f.lines, "numbered": f.numbered, "plain": f.plain, "author": f.author,
                "is_empty": f.is_empty}

    d = doc.entry_date
    return {
        "entry_id": doc.entry_id, "title": doc.title,
        "entry_date": _DateView(year=d.year, month=d.month, day=d.day),
        "date_display": doc.date_display, "weekday": doc.weekday, "template_name": doc.template_name,
        "theme": doc.theme, "layout": doc.layout,
        "sections": [{"id": s.id, "title": s.title, "color": s.color, "icon": s.icon,
                      "fields": [fld(f) for f in s.fields], "is_empty": s.is_empty} for s in doc.sections],
        "closing_lines": doc.closing_lines, "word_count": doc.word_count, "palette": list(doc.palette),
        "closing_label": doc.closing_label, "has_closing": doc.has_closing,
        "plain_text": doc.to_plain_text(),
    }


def theme_context(t: Theme) -> dict:
    out = asdict(t)
    out["tags"] = list(t.tags)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 挑題組、分類（原 backend/taxonomy.py）
# ══════════════════════════════════════════════════════════════════════════

FAITH_LEVELS = ("devotional", "prayer")


def categories(templates: list) -> dict:
    def bucket(vocab, pick):
        return [{"id": tid, "label": label, "templates": [t.id for t in templates if pick(t, tid)]}
                for tid, label in vocab.items()]

    tags: dict = {}
    groups: dict = {}
    for t in templates:
        for tag in t.tags:
            tags.setdefault(tag, []).append(t.id)
        groups.setdefault(t.group or "其他", []).append(t.id)
    return {
        "structure": bucket(STRUCTURES, lambda t, v: t.structure == v),
        "reflection": bucket(REFLECTIONS, lambda t, v: t.reflection == v),
        "flow": bucket(FLOW_TOKENS, lambda t, v: v in t.flow),
        "tags": [{"id": k, "label": k, "templates": v} for k, v in sorted(tags.items(), key=lambda kv: -len(kv[1]))],
        "groups": [{"id": k, "label": k, "templates": v} for k, v in groups.items()],
    }


def filter_templates(templates: list, *, structure="", reflection="", flow="", tag="") -> list:
    def wanted(raw):
        return {v.strip() for v in raw.split(",") if v.strip()}

    out = templates
    if structure:
        out = [t for t in out if t.structure in wanted(structure)]
    if reflection:
        out = [t for t in out if t.reflection in wanted(reflection)]
    if flow:
        out = [t for t in out if wanted(flow) <= set(t.flow)]
    if tag:
        out = [t for t in out if wanted(tag) & set(t.tags)]
    return out


def check_filter_values(*, structure="", reflection="", flow="") -> str | None:
    for raw, vocab, name in ((structure, STRUCTURES, "structure"), (reflection, REFLECTIONS, "reflection"),
                             (flow, FLOW_TOKENS, "flow")):
        for v in (x.strip() for x in raw.split(",") if x.strip()):
            if v not in vocab:
                return f"沒有這種 {name}：{v}（有：{'、'.join(vocab)}）"
    return None


def _writing_length(text: str) -> int:
    return len([c for c in text if not c.isspace()])


def _has_text_field(t: Template) -> bool:
    return any(f.kind in ("text", "list") for s in t.sections for f in s.fields)


def detect_signals(text: str, rules: dict) -> list:
    found = []
    for sid, rule in rules.get("signals", {}).items():
        if not isinstance(rule, dict):
            continue
        hits = [w for w in rule.get("words", []) if isinstance(w, str) and w and w in text]
        if hits:
            found.append({"id": sid, "label": rule.get("label", sid), "words": hits[:5], "rule": rule})
    return found


def suggest(text: str, templates: list, *, rules: dict, faith: str = "auto", default_id: str = "",
            limit: int = 5) -> dict:
    """照規則替一段文字排題組（不叫模型），每一分都寫成一句 why。"""
    text = text or ""
    length = _writing_length(text)
    bounds = rules.get("length", {}) if isinstance(rules.get("length"), dict) else {}
    short_max, long_min = int(bounds.get("short", 40)), int(bounds.get("long", 300))
    penalty = int(rules.get("faith_penalty", 4))
    signals = detect_signals(text, rules)
    faith_seen = any(s["id"] == "faith" for s in signals)
    scored: list = []
    for t in templates:
        score, why = 0.0, []
        if t.reflection in FAITH_LEVELS and not faith_seen:
            if faith == "never":
                continue
            if faith == "auto":
                score -= penalty
                why.append("沒提到信仰，先放後面")
        for s in signals:
            rule, word = s["rule"], "「" + s["words"][0] + "」"
            weight = float(rule.get("weight", 1))
            matched = []
            if set(rule.get("flow", [])) & set(t.flow):
                matched.append("、".join(FLOW_TOKENS.get(f, f) for f in rule.get("flow", []) if f in t.flow))
            if set(rule.get("moods", [])) & set(t.moods):
                matched.append("心情：" + "、".join(m for m in rule.get("moods", []) if m in t.moods))
            if set(rule.get("tags", [])) & set(t.tags):
                matched.append("標籤：" + "、".join(g for g in rule.get("tags", []) if g in t.tags))
            if t.id in rule.get("templates", []):
                score += weight * 1.5
                matched.append("這個題組就是為這種時候做的")
            if matched:
                score += weight * min(len(s["words"]), 3) + (len(matched) - 1)
                why.append(f"提到{word}→ {'；'.join(matched)}")
        if length and length <= short_max:
            if t.minutes and t.minutes <= 3:
                score += 2
                why.append(f"寫得很短（{length} 字）→ 快速的題組")
            elif t.minutes >= 15:
                score -= 2
        elif length >= long_min:
            if _has_text_field(t) and t.minutes >= 10:
                score += 2
                why.append(f"寫得很長（{length} 字）→ 有空間書寫的題組")
        if score > 0:
            scored.append((score, t.order, t, why))
    scored.sort(key=lambda row: (-row[0], row[1], row[2].id))
    out = [{"id": t.id, "name": t.name, "group": t.group or "其他", "minutes": t.minutes, "structure": t.structure,
            "reflection": t.reflection, "flow": t.flow, "score": round(score, 1), "why": why}
           for score, _, t, why in scored[:max(1, limit)]]
    if not out:
        by_id = {t.id: t for t in templates}
        for tid in dict.fromkeys([default_id, "jot", "three-lines"]):
            t = by_id.get(tid)
            if t is not None:
                out.append({"id": t.id, "name": t.name, "group": t.group or "其他", "minutes": t.minutes,
                            "structure": t.structure, "reflection": t.reflection, "flow": t.flow, "score": 0,
                            "why": ["沒有特別的線索：照原文記下來最穩"]})
    flows = list(dict.fromkeys(f for s in signals for f in s["rule"].get("flow", []) if f in FLOW_TOKENS))
    level = "prayer" if faith_seen and any("prayer" in s["rule"].get("flow", []) for s in signals) else \
        "devotional" if faith_seen else "reflection" if any(s["id"] in ("learn", "identity") for s in signals) else ""
    return {"suggestions": out, "signals": [{"id": s["id"], "label": s["label"], "words": s["words"]} for s in signals],
            "length": length, "categories": {"reflection": level, "flow": flows}}


# ══════════════════════════════════════════════════════════════════════════
# 畫圖用的 prompt（原 backend/ai/image_prompt.py 沒有 API key 的那一套；SYNOX 的 AI 可以自己寫得更好）
# ══════════════════════════════════════════════════════════════════════════

STYLES: dict = {
    "watercolor": ("水彩", "loose watercolour on cold-press paper, soft bleeds, visible paper grain"),
    "ink": ("水墨", "east-asian ink wash, restrained palette, generous negative space"),
    "gouache": ("不透明水彩", "matte gouache, flat shapes, muted mid-tones"),
    "pencil": ("色鉛筆", "coloured pencil on toned paper, gentle hatching"),
    "oil": ("油畫", "oil on canvas, visible brushwork, warm layered colour"),
    "risograph": ("孔版印刷", "two-colour risograph print, slight misregistration, grainy texture"),
    "storybook": ("繪本", "children's picture-book illustration, warm and simple shapes"),
    "photo": ("寫實", "natural-light photography, shallow depth of field, honest and unstyled"),
}


def image_prompt(doc: RenderedDoc, style: str) -> dict:
    style_zh, style_en = STYLES.get(style, STYLES["watercolor"])
    body_lines = [re.sub(r"^-\s*[^：]{0,12}：", "", line).strip()
                  for line in doc.to_plain_text().splitlines()[1:]
                  if line.strip() and not line.lstrip().startswith("#")]
    chunks = [c.strip(" 　·-–—") for c in re.split(r"[。！？；\n，、,.!?;]", "\n".join(body_lines))
              if 3 <= len(c.strip()) <= 18]
    keywords = chunks[:5] or ["這一天"]
    subject = "、".join(keywords[:4])
    return {
        "prompt_en": (f"A single scene from one ordinary day, drawn from what happened: {subject}. Keep the mood "
                      f"honest and unembellished — natural light, everyday surroundings, nothing staged. {style_en}."),
        "prompt_zh": f"畫出這一天真實的樣子，畫面裡有：{subject}。自然的光線、日常的環境，不用美化，忠實就好。{style_zh}風格。",
        "mood": "如實", "keywords": keywords,
        "negative_prompt": "garbled text, distorted hands, watermark, signature", "source": "fallback",
    }


# ══════════════════════════════════════════════════════════════════════════
# 設定（原 backend/preferences.py）
# ══════════════════════════════════════════════════════════════════════════

DEFAULT_PALETTE = [{"hex": "#22252a", "name": "墨黑"}, {"hex": "#c8322c", "name": "硃紅"},
                   {"hex": "#1f7a4d", "name": "松綠"}, {"hex": "#2456b8", "name": "靛藍"},
                   {"hex": "#8a5fbf", "name": "紫"}, {"hex": "#c98a1e", "name": "赭黃"}]
MAX_PALETTE = 24
PREF_DEFAULTS = {"palette": [], "template_id": None, "layout": None, "theme": None, "card_paper": None,
                 "card_width": 1080, "image_style": "watercolor", "use_as_default": True, "day_start_hour": 0,
                 "preset": "", "suggest_faith": "auto"}


def pref_defaults() -> dict:
    """PREF_DEFAULTS 的新複本（palette 是新的 list）：伺服器版每個人的設定不會共用到同一個 list。"""
    return {key: list(value) if isinstance(value, list) else value for key, value in PREF_DEFAULTS.items()}


#: {key: (是什麼, 怎麼寫)}。原本還有 export_dir（匯出到哪個資料夾）：SYNOX 版的檔案直接傳給你、傳完就刪，所以沒有這一項。
PREF_FIELDS: dict = {
    "palette": ("編輯器上方那一排墨水色（也是匯出時可以用的顏色）",
                "JSON 清單：[{\"hex\": \"#22252a\", \"name\": \"墨黑\"}, …]；default＝內建六色"),
    "template_id": ("預設題組：新的一天先用哪一個", "題組 id（/diary_templates 看全部），空字串＝不指定"),
    "layout": ("預設輸出版型", "版型 id（/diary_layouts 看全部），空字串＝不指定"),
    "theme": ("預設紙張顏色", "letterpress、notebook、linen、dawn、midnight，空字串＝不指定"),
    "card_paper": ("卡片的紙", "ruled、grid、plain、dotted，空字串＝跟版型"),
    "card_width": ("卡片寬度（px）", "480–2400 的整數"),
    "image_style": ("繪圖 prompt 的風格", "一個英文字，例 watercolor"),
    "use_as_default": ("新日記要不要套用上面的預設", "true／false"),
    "day_start_hour": ("幾點以前「今天」還算昨天（熬夜寫的人用）", "0–23 的整數，0＝午夜換日"),
    "preset": ("這份安裝用的起始題組（/diary_setup_init 寫的）", "generic、devotional，空字串＝沒跑過"),
    "suggest_faith": ("挑題組（/diary_suggest）怎麼對待信仰類題組",
                      "auto（字裡沒提信仰就放後面）、prefer（一視同仁）、never（不列）"),
}
_UNSET = ("", None, "null", "none")


def _check_prefs(data: dict) -> dict:
    """照原本 Preferences 模型的規則檢查；不合丟 ValueError（鍵名：原因）。"""
    out = pref_defaults()
    for key in PREF_DEFAULTS:
        if key not in data:
            continue
        value = data[key]
        if key == "palette":
            if not isinstance(value, list):
                raise ValueError("palette: 要是清單")
            pens = []
            for p in value:
                if not isinstance(p, dict) or not isinstance(p.get("hex"), str):
                    raise ValueError("palette: 每一色要有 hex")
                pens.append({"hex": p["hex"], "name": str(p.get("name", ""))})
            value = pens
        elif key in ("template_id", "layout", "theme", "card_paper"):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{key}: 要是文字")
        elif key in ("card_width", "day_start_hour"):
            if isinstance(value, str) and value.strip().isdigit():
                value = int(value)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key}: 要是整數")
            if key == "day_start_hour" and not 0 <= value <= 23:
                raise ValueError("day_start_hour 要在 0 到 23 之間")
        elif key == "use_as_default":
            if isinstance(value, str) and value.lower() in ("true", "yes", "1", "on", "false", "no", "0", "off"):
                value = value.lower() in ("true", "yes", "1", "on")
            if not isinstance(value, bool):
                raise ValueError("use_as_default: 要是 true 或 false")
        elif key == "image_style":
            if not isinstance(value, str):
                raise ValueError("image_style: 要是文字")
        elif key == "preset":
            if value not in ("", "generic", "devotional"):
                raise ValueError("preset 只能是 generic 或 devotional")
        elif key == "suggest_faith":
            if value not in ("auto", "prefer", "never"):
                raise ValueError("suggest_faith: 只能是 auto、prefer、never")
        out[key] = value
    return out


def clean_prefs(data: dict) -> dict:
    """存之前（原 preferences.save）：顏色正規化、去重、最多 24 色，空的放回內建六色。"""
    prefs = _check_prefs(data)
    clean, seen = [], set()
    for pen in prefs["palette"][:MAX_PALETTE]:
        hex_value = normalize_color(pen["hex"])
        if not hex_value or hex_value in seen:
            continue
        seen.add(hex_value)
        clean.append({"hex": hex_value, "name": pen["name"].strip()[:24]})
    prefs["palette"] = clean or [dict(p) for p in DEFAULT_PALETTE]
    return prefs


# ══════════════════════════════════════════════════════════════════════════
# 內建內容（pack 資料夾的 content/：題組、版型、起始組合、挑題組的規則）
# ══════════════════════════════════════════════════════════════════════════

SHIPPED_LAYOUT_IDS = ("letter", "ledger", "manuscript", "postcard")
FALLBACK_LAYOUT = "letter"


class Content:
    """pack 附的內建內容（唯讀，讀一次就快取）。"""

    def __init__(self, folder: str):
        self.folder = folder
        self._text: dict = {}
        self._templates = None

    def text(self, rel: str) -> str | None:
        if rel not in self._text:
            path = os.path.join(self.folder, *rel.split("/"))
            try:
                with open(path, "r", encoding="utf-8-sig", newline="") as f:
                    self._text[rel] = f.read()
            except OSError:
                self._text[rel] = None
        return self._text[rel]

    def listing(self, sub: str) -> list:
        try:
            return sorted(os.listdir(os.path.join(self.folder, sub)))
        except OSError:
            return []

    def templates(self) -> dict:
        """{id: 原始 dict}（內建題組；壞掉的跳過）。"""
        if self._templates is None:
            found = {}
            for name in self.listing("templates"):
                if not name.endswith(".json"):
                    continue
                try:
                    raw = json.loads(self.text(f"templates/{name}") or "")
                    raw.setdefault("id", name[:-5])
                    parse_template(raw)
                except ValueError:
                    continue
                found[raw["id"]] = raw
            self._templates = found
        return self._templates

    def layout_ids(self) -> list:
        return [n[:-len(".html.j2")] for n in self.listing("layouts")
                if n.endswith(".html.j2") and not n.startswith("_")]

    def json(self, rel: str, default):
        try:
            value = json.loads(self.text(rel) or "")
        except ValueError:
            return default
        return value


def fingerprint(text: str) -> str:
    """sha256（換行先統一成 LF；原 seed_manager.fingerprint）。"""
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════════════
# 一個人的日記本（原 storage.py、backup.py、template_registry.py、layout_registry.py、seed_manager.py）
# ══════════════════════════════════════════════════════════════════════════

MAX_EDIT_REVISIONS = 40
SNAPSHOT_INTERVAL_SECONDS = 180
KEEP_AUTOMATIC = 20
ROLLING_INTERVAL = timedelta(hours=6)
MAX_LIST_ROWS = 40
MAX_TEXT_CHARS = 20_000
EXPORT_FORMAT = "synox.diary.export"
EXPORT_VERSION = 1
#: 「匯出全部」的檔裡一定是 list 的鍵（至少要有一個；只放題組的短檔也算）
EXPORT_LISTS = ("entries", "templates", "layouts")


def is_export(document) -> bool:
    """匯入認得的檔：這一版匯出的（format＝EXPORT_FORMAT），或原本的每日日記程式匯出的舊檔——它的 format 是另一個
    「….export」名字，用結構認：format 以 .export 結尾、version 是 1、entries／templates／layouts 至少有一個是 list。"""
    if not isinstance(document, dict):
        return False
    fmt = document.get("format")
    if fmt == EXPORT_FORMAT:
        return True
    return (isinstance(fmt, str) and fmt.endswith(".export") and document.get("version") == EXPORT_VERSION
            and any(isinstance(document.get(key), list) for key in EXPORT_LISTS))


_SAFE_BACKUP = re.compile(r"^[A-Za-z0-9._-]+$")


def empty_db() -> dict:
    return {"schema": SCHEMA, "entries": [], "revisions": [], "next_entry": 1, "next_revision": 1}


def empty_setup() -> dict:
    return {"schema": SCHEMA, "preferences": {}, "templates": {}, "layouts": {}, "hidden_templates": [],
            "hidden_layouts": [], "seeded": [], "backups": []}


def _plain_length(answers: dict, closing: str) -> int:
    parts = [to_plain(closing)]
    for value in answers.values():
        if isinstance(value, str):
            parts.append(to_plain(value))
        elif isinstance(value, list):
            parts.extend(to_plain(str(v)) for v in value)
    return len([c for c in "".join(parts) if not c.isspace()])


def _stamp() -> str:
    return now().isoformat(timespec="seconds")


class Book:
    """一個人的日記本：保管箱裡的主檔＋設定。所有讀寫都經過它（不會碰到別人的 store）。"""

    def __init__(self, store, content: Content, *, seed: bool = True):
        self.store = store
        self.content = content
        db = store.load(None)
        self.db = db if isinstance(db, dict) else empty_db()
        for k, v in empty_db().items():
            self.db.setdefault(k, v)
        setup = store.load(None, key="setup")
        self.setup = setup if isinstance(setup, dict) else empty_setup()
        for k, v in empty_setup().items():
            self.setup.setdefault(k, v)
        self._db_dirty = self._setup_dirty = False
        if seed:
            self.install_seeds()

    # ── 存檔 ────────────────────────────────────────────────────────
    def touch_db(self):
        self._db_dirty = True

    def touch_setup(self):
        self._setup_dirty = True

    def commit(self):
        if self._db_dirty:
            self.store.save(self.db)
        if self._setup_dirty:
            self.store.save(self.setup, key="setup")
        self._db_dirty = self._setup_dirty = False

    # ── 設定 ────────────────────────────────────────────────────────
    def prefs(self) -> dict:
        try:
            prefs = _check_prefs(self.setup.get("preferences") or {})
        except ValueError:
            prefs = pref_defaults()
        if not prefs["palette"]:
            prefs["palette"] = [dict(p) for p in DEFAULT_PALETTE]
        return prefs

    def save_prefs(self, data: dict) -> dict:
        prefs = clean_prefs(data)
        self.setup["preferences"] = prefs
        self.touch_setup()
        return prefs

    # ── 內建題組：第一次用時「裝」哪些（原 install_seeds；只記哪些裝了、哪些刻意不裝） ──
    def preset_table(self) -> dict:
        raw = self.content.json("presets.json", {})
        return {k: v for k, v in raw.items() if isinstance(v, dict) and not k.startswith("_")} \
            if isinstance(raw, dict) else {}

    def _preset_templates(self, name: str):
        chosen = self.preset_table().get(name, {}).get("templates", "all")
        return None if chosen == "all" or not isinstance(chosen, list) else {str(t) for t in chosen}

    def install_seeds(self, keep=None) -> list:
        """還沒處理過的內建題組：照起始組合（沒跑過 setup init＝全部，但標了 default_hidden 的不裝）
        決定裝不裝；不裝的記進 hidden_templates，之後也不會自己跑出來（/diary_tpl_restore 拿回來）。"""
        seeded = set(self.setup["seeded"])
        hidden = list(self.setup["hidden_templates"])
        if keep is None:
            name = self.prefs()["preset"]
            keep = self._preset_templates(name) if name else None
        added = []
        for tid, raw in self.content.templates().items():
            if tid in seeded:
                continue
            seeded.add(tid)
            wanted = (tid in keep) if keep is not None else not raw.get("default_hidden")
            if wanted:
                if tid in hidden:
                    continue
                added.append(tid)
            elif tid not in hidden and tid not in self.setup["templates"]:
                hidden.append(tid)
        if seeded != set(self.setup["seeded"]) or hidden != self.setup["hidden_templates"]:
            self.setup["seeded"] = sorted(seeded)
            self.setup["hidden_templates"] = hidden
            self.touch_setup()
        return added

    # ── 題組 ────────────────────────────────────────────────────────
    def all_templates(self) -> list:
        raws: dict = {}
        hidden = set(self.setup["hidden_templates"])
        seeded = set(self.setup["seeded"])          # 還沒「裝」過的內建題組不算（原本：還沒複製進 data/templates/）
        for tid, raw in self.content.templates().items():
            if tid in seeded and tid not in hidden:
                raws[tid] = raw
        for tid, raw in (self.setup["templates"] or {}).items():
            raws[tid] = raw
        out = []
        for tid, raw in raws.items():
            try:
                t = parse_template({**raw, "id": raw.get("id") or tid})
            except ValueError:
                continue
            out.append((t.order, tid, t))
        out.sort(key=lambda x: (x[0], x[1]))
        return [t for _, _, t in out]

    def get_template(self, template_id) -> Template | None:
        return next((t for t in self.all_templates() if t.id == template_id), None)

    def save_template(self, t: Template) -> Template:
        self.setup["templates"][t.id] = template_dump(t)
        if t.id in self.setup["hidden_templates"]:
            self.setup["hidden_templates"].remove(t.id)
        self.touch_setup()
        return t

    def delete_template(self, template_id: str) -> bool:
        if self.get_template(template_id) is None:
            return False
        self.setup["templates"].pop(template_id, None)
        if template_id in self.content.templates() and template_id not in self.setup["hidden_templates"]:
            self.setup["hidden_templates"].append(template_id)
        self.touch_setup()
        return True

    def restore_template(self, template_id: str) -> bool:
        """把內建題組放回（或還原成）內建的版本；只動那一個題組，不碰日記。"""
        if template_id not in self.content.templates():
            return False
        self.setup["templates"].pop(template_id, None)
        if template_id in self.setup["hidden_templates"]:
            self.setup["hidden_templates"].remove(template_id)
        if template_id not in self.setup["seeded"]:
            self.setup["seeded"].append(template_id)
        self.touch_setup()
        return True

    def available_templates(self) -> list:
        """內建、但這個人現在沒有的題組（刪掉的、起始組合沒裝的、預設不出現的）。"""
        have = {t.id for t in self.all_templates()}
        out = []
        for tid, raw in self.content.templates().items():
            if tid not in have:
                t = parse_template(raw)
                out.append(t)
        out.sort(key=lambda t: (t.order, t.id))
        return out

    # ── 版型 ────────────────────────────────────────────────────────
    def layout_source(self, name: str) -> str | None:
        """版型檔（例 letter.html.j2、_tokens.css.j2）：自己的優先，再來是內建（原 FileSystemLoader 的順序）。"""
        if name.endswith(".html.j2"):
            own = (self.setup["layouts"] or {}).get(name[:-len(".html.j2")])
            if isinstance(own, dict) and isinstance(own.get("html"), str):
                return own["html"]
        if "/" in name or "\\" in name or ".." in name:
            return None
        return self.content.text(f"layouts/{name}")

    def all_layouts(self) -> list:
        metas: dict = {}
        hidden = set(self.setup["hidden_layouts"])
        for lid in self.content.layout_ids():
            if lid not in hidden:
                metas[lid] = self.content.json(f"layouts/{lid}.json", {})
        for lid, own in (self.setup["layouts"] or {}).items():
            if isinstance(own, dict):
                metas[lid] = own.get("meta") or {}
        out = []
        for lid, meta in metas.items():
            meta = dict(meta) if isinstance(meta, dict) else {}
            meta["id"] = lid
            meta.setdefault("name", lid)
            meta["origin"] = "shipped" if lid in SHIPPED_LAYOUT_IDS else "custom"
            if meta.get("card_paper", "ruled") not in PAPERS:
                continue
            out.append({"id": lid, "name": str(meta["name"]), "description": str(meta.get("description", "")),
                        "tags": list(meta.get("tags", [])), "default_theme": str(meta.get("default_theme", "letterpress")),
                        "card_paper": meta.get("card_paper", "ruled"), "origin": meta["origin"]})
        order = {name: i for i, name in enumerate(SHIPPED_LAYOUT_IDS)}
        out.sort(key=lambda x: (order.get(x["id"], 100), x["name"]))
        return out

    def get_layout(self, layout_id) -> dict | None:
        wanted = layout_id or FALLBACK_LAYOUT
        layouts = self.all_layouts()
        found = next((x for x in layouts if x["id"] == wanted), None)
        if found:
            return found
        return next((x for x in layouts if x["id"] == FALLBACK_LAYOUT), None) or (layouts[0] if layouts else None)

    def layout_exists(self, layout_id: str) -> bool:
        return any(x["id"] == layout_id for x in self.all_layouts())

    def delete_layout(self, layout_id: str) -> bool:
        if not self.layout_exists(layout_id):
            return False
        self.setup["layouts"].pop(layout_id, None)
        if layout_id in self.content.layout_ids() and layout_id not in self.setup["hidden_layouts"]:
            self.setup["hidden_layouts"].append(layout_id)
        self.touch_setup()
        return True

    def restore_layout(self, layout_id: str) -> bool:
        if layout_id not in self.content.layout_ids():
            return False
        self.setup["layouts"].pop(layout_id, None)
        if layout_id in self.setup["hidden_layouts"]:
            self.setup["hidden_layouts"].remove(layout_id)
        self.touch_setup()
        return True

    def render_html(self, doc: RenderedDoc, *, layout=None, gift_cover=False, gift_message="") -> str:
        chosen = self.get_layout(layout or doc.layout)
        layout_id = chosen["id"] if chosen else FALLBACK_LAYOUT
        ctx = {"doc": doc_context(doc), "t": theme_context(get_theme(doc.theme)), "layout": chosen,
               "gift_cover": bool(gift_cover), "gift_message": (gift_message or "").strip()}
        return render_layout(self.layout_source, f"{layout_id}.html.j2", ctx)

    # ── 日記（原 storage.py） ───────────────────────────────────────
    def _rows(self) -> list:
        return self.db["entries"]

    def _row(self, entry_id: int) -> dict | None:
        return next((r for r in self._rows() if r["id"] == entry_id), None)

    def get_entry(self, entry_id: int) -> Entry | None:
        row = self._row(entry_id)
        return Entry.from_row(row) if row else None

    def get_entry_for(self, day: date, template_id: str) -> Entry | None:
        rows = [r for r in self._rows() if r["entry_date"] == day.isoformat() and r["template_id"] == template_id]
        rows.sort(key=lambda r: (r["updated_at"], r["id"]), reverse=True)
        return Entry.from_row(rows[0]) if rows else None

    def get_entry_by_date(self, day: date) -> Entry | None:
        rows = [r for r in self._rows() if r["entry_date"] == day.isoformat()]
        rows.sort(key=lambda r: (r["updated_at"], r["id"]), reverse=True)
        return Entry.from_row(rows[0]) if rows else None

    def list_entries_on(self, day: date) -> list:
        rows = sorted((r for r in self._rows() if r["entry_date"] == day.isoformat()), key=lambda r: r["id"])
        return [Entry.from_row(r) for r in rows]

    def list_entries(self, limit: int = 100, offset: int = 0) -> list:
        rows = sorted(self._rows(), key=lambda r: (r["entry_date"], r["id"]), reverse=True)
        return [Entry.from_row(r) for r in rows[offset:offset + limit]]

    def create_entry(self, *, entry_date: date, template_id: str, title="", theme="letterpress", layout="letter",
                     answers=None, closing="", field_colors=None, provenance=None, created_at=None) -> Entry:
        stamp = _stamp()
        entry_id = int(self.db["next_entry"])
        self.db["next_entry"] = entry_id + 1
        row = {"id": entry_id, "entry_date": entry_date.isoformat(), "title": title, "template_id": template_id,
               "theme": theme if theme in THEMES else "letterpress", "layout": layout or "letter",
               "answers": answers or {}, "closing": closing or "", "field_colors": field_colors or {},
               "provenance": provenance or {}, "created_at": created_at or stamp, "updated_at": stamp}
        self._rows().append(row)
        self.touch_db()
        return Entry.from_row(row)

    def _snapshot(self, row: dict, reason: str) -> None:
        rev_id = int(self.db["next_revision"])
        self.db["next_revision"] = rev_id + 1
        self.db["revisions"].append({
            "id": rev_id, "entry_id": row["id"], "entry_date": row["entry_date"], "title": row["title"],
            "template_id": row["template_id"], "theme": row["theme"], "layout": row["layout"],
            "answers": row["answers"], "closing": row["closing"], "field_colors": row["field_colors"],
            "provenance": row.get("provenance") or {},
            "char_count": _plain_length(row["answers"], row["closing"]), "reason": reason, "created_at": _stamp()})
        edits = [r for r in self.db["revisions"] if r["reason"] == "edit" and r["entry_id"] == row["id"]]
        if len(edits) > MAX_EDIT_REVISIONS:
            drop = {r["id"] for r in sorted(edits, key=lambda r: r["id"], reverse=True)[MAX_EDIT_REVISIONS:]}
            self.db["revisions"] = [r for r in self.db["revisions"] if r["id"] not in drop]
        self.touch_db()

    def _should_snapshot(self, row: dict, new_len: int) -> bool:
        old_len = _plain_length(row["answers"], row["closing"])
        if old_len == 0:
            return False
        if new_len < old_len:
            return True
        mine = [r for r in self.db["revisions"] if r["entry_id"] == row["id"]]
        if not mine:
            return True
        last = max(mine, key=lambda r: r["id"])
        age = (now() - datetime.fromisoformat(last["created_at"])).total_seconds()
        return age >= SNAPSHOT_INTERVAL_SECONDS

    def update_entry(self, entry_id: int, updates: dict, expected_updated_at=None) -> Entry | None:
        """updates：只放要改的欄位（entry_date 是 date、其他照存檔格式）。比 expected_updated_at 新就不寫（原 StaleWriteError）。"""
        row = self._row(entry_id)
        if row is None:
            return None
        if expected_updated_at is not None and expected_updated_at.replace(microsecond=0).isoformat() != \
                datetime.fromisoformat(row["updated_at"]).replace(microsecond=0).isoformat():
            raise DiaryError("這篇日記在別的地方被改過了（可能是另一個分頁）。請重新載入，避免蓋掉比較新的內容。")
        clean = {k: v for k, v in updates.items() if v is not None}
        if not clean:
            return Entry.from_row(row)
        if "entry_date" in clean:
            clean["entry_date"] = clean["entry_date"].isoformat()
        new_len = _plain_length(clean.get("answers", row["answers"]), clean.get("closing", row["closing"]))
        if self._should_snapshot(row, new_len):
            self._snapshot(row, "edit")
        row.update(clean)
        row["updated_at"] = _stamp()
        self.touch_db()
        return Entry.from_row(row)

    def delete_entry(self, entry_id: int) -> bool:
        row = self._row(entry_id)
        if row is None:
            return False
        self._snapshot(row, "delete")
        self.db["entries"] = [r for r in self._rows() if r["id"] != entry_id]
        self.touch_db()
        return True

    def list_revisions(self, entry_id: int) -> list:
        keys = ("id", "entry_id", "entry_date", "title", "template_id", "char_count", "reason", "created_at")
        rows = sorted((r for r in self.db["revisions"] if r["entry_id"] == entry_id), key=lambda r: r["id"], reverse=True)
        return [{k: r[k] for k in keys} for r in rows]

    def list_trash(self) -> list:
        live = {r["id"] for r in self._rows()}
        latest: dict = {}
        for r in self.db["revisions"]:
            if r["reason"] == "delete" and (r["entry_id"] not in latest or r["id"] > latest[r["entry_id"]]["id"]):
                latest[r["entry_id"]] = r
        keys = ("id", "entry_id", "entry_date", "title", "template_id", "char_count", "created_at")
        rows = sorted((r for eid, r in latest.items() if eid not in live), key=lambda r: r["id"], reverse=True)
        return [{k: r[k] for k in keys} for r in rows]

    def restore_revision(self, revision_id: int) -> Entry | None:
        rev = next((r for r in self.db["revisions"] if r["id"] == revision_id), None)
        if rev is None:
            return None
        keys = ("entry_date", "title", "template_id", "theme", "layout", "answers", "closing", "field_colors",
                "provenance")
        current = self._row(rev["entry_id"])
        if current is not None:
            self._snapshot(current, "restore")
            for k in keys:
                current[k] = rev[k]
            current["updated_at"] = _stamp()
        else:
            row = {"id": rev["entry_id"], **{k: rev[k] for k in keys}, "created_at": rev["created_at"],
                   "updated_at": _stamp()}
            self._rows().append(row)
            self.db["next_entry"] = max(int(self.db["next_entry"]), rev["entry_id"] + 1)
        self.touch_db()
        return self.get_entry(rev["entry_id"])

    def undelete_entry(self, entry_id: int) -> Entry | None:
        revs = [r for r in self.db["revisions"] if r["entry_id"] == entry_id and r["reason"] == "delete"]
        if not revs or self._row(entry_id) is not None:
            return None
        return self.restore_revision(max(revs, key=lambda r: r["id"])["id"])

    def purge_trash(self, entry_id: int) -> bool:
        if self._row(entry_id) is not None:
            return False
        before = len(self.db["revisions"])
        self.db["revisions"] = [r for r in self.db["revisions"] if r["entry_id"] != entry_id]
        self.touch_db()
        return len(self.db["revisions"]) < before

    def stats(self) -> dict:
        return {"entries": len(self._rows()), "revisions": len(self.db["revisions"]), "trash": len(self.list_trash())}

    def entries_using_template(self, template_id: str) -> int:
        return sum(1 for r in self._rows() if r["template_id"] == template_id)

    # ── 整份備份（原 backup.py：data/backups/*.sqlite3 → 保管箱的 diary.backup-<槽>） ──
    def backups(self) -> list:
        return sorted(self.setup["backups"], key=lambda b: b["created_at"], reverse=True)

    def snapshot(self, reason: str = "auto", *, manual: bool = False) -> dict | None:
        if not self.db["entries"] and not self.db["revisions"]:
            return None                       # 原本：資料庫還不存在就不備份
        safe_reason = re.sub(r"[^a-z0-9-]+", "-", reason.lower()).strip("-") or "auto"
        stamp = now().strftime("%Y%m%d-%H%M%S")
        name = f"{'manual-' if manual else ''}journal-{stamp}-{safe_reason}"
        used = {b["slot"] for b in self.setup["backups"]}
        if manual:
            slot = next(f"m{i}" for i in range(10_000) if f"m{i}" not in used)
        else:
            autos = sorted((b for b in self.setup["backups"] if b["automatic"]), key=lambda b: b["created_at"])
            free = [f"a{i}" for i in range(KEEP_AUTOMATIC) if f"a{i}" not in used]
            if free:
                slot = free[0]
            else:
                slot = autos[0]["slot"]
                self.setup["backups"] = [b for b in self.setup["backups"] if b["slot"] != slot]
        while any(b["name"] == name for b in self.setup["backups"]):
            name += "-1"
        payload = json.loads(json.dumps(self.db, ensure_ascii=False))
        self.store.save(payload, key=f"backup-{slot}")
        info = {"name": name, "slot": slot, "created_at": now().isoformat(timespec="seconds"),
                "automatic": not manual, "size_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                "entries": len(payload["entries"])}
        self.setup["backups"].append(info)
        self.touch_setup()
        return info

    def snapshot_if_stale(self, reason: str = "rolling") -> dict | None:
        autos = [b for b in self.setup["backups"] if b["automatic"]]
        newest = max(autos, key=lambda b: b["created_at"], default=None)
        if newest and now() - datetime.fromisoformat(newest["created_at"]) < ROLLING_INTERVAL:
            return None
        return self.snapshot(reason)

    def _backup(self, name: str) -> dict:
        if not _SAFE_BACKUP.match(name or ""):
            raise DiaryError("備份名稱不合法。")
        found = next((b for b in self.setup["backups"] if b["name"] == name), None)
        if found is None:
            raise DiaryError("找不到這個備份（/diary_backups 看有哪些）。")
        return found

    def restore_backup(self, name: str) -> dict:
        info = self._backup(name)
        data = self.store.load(None, key=f"backup-{info['slot']}")
        if not isinstance(data, dict) or "entries" not in data:
            raise DiaryError("這個備份讀不到內容。")
        self.snapshot("before-restore")
        self.db = data
        for k, v in empty_db().items():
            self.db.setdefault(k, v)
        self.touch_db()
        return info

    def delete_backup(self, name: str) -> dict:
        info = self._backup(name)
        self.store.save({}, key=f"backup-{info['slot']}")
        self.setup["backups"] = [b for b in self.setup["backups"] if b["name"] != name]
        self.touch_setup()
        return info

    # ── 匯出全部／匯入（原 backup.export_all／import_all） ─────────────
    def export_all(self) -> dict:
        """「匯出全部」：format 是 EXPORT_FORMAT（舊程式匯出的檔，匯入時照 is_export 的規則也認得）。"""
        layouts = []
        for x in self.all_layouts():
            src = self.layout_source(f"{x['id']}.html.j2")
            meta = {k: x[k] for k in ("name", "description", "tags", "default_theme", "card_paper")}
            layouts.append({"id": x["id"], "html": src or "", "meta": meta})
        return {"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "exported_at": _stamp(),
                "entries": [e.to_row() for e in self.list_entries(limit=100_000)],
                "templates": [template_dump(t) for t in self.all_templates()],
                "layouts": layouts, "preferences": self.prefs()}

    def import_all(self, document: dict, *, replace: bool = False) -> dict:
        if not is_export(document):
            raise DiaryError(f"這不是每日日記「匯出全部」的檔案（format 要是 {EXPORT_FORMAT}）。")
        self.snapshot("before-import")
        counts = {"entries": 0, "templates": 0, "layouts": 0, "skipped_entries": 0}
        if replace:
            for entry in self.list_entries(limit=100_000):
                self.delete_entry(entry.id)
        existing = {(e.entry_date.isoformat(), e.title, e.template_id) for e in self.list_entries(limit=100_000)}
        for raw in document.get("entries", []) or []:
            if not isinstance(raw, dict):
                counts["skipped_entries"] += 1
                continue
            key = (raw.get("entry_date"), raw.get("title", ""), raw.get("template_id"))
            if key in existing:
                counts["skipped_entries"] += 1
                continue
            try:
                day = date.fromisoformat(str(raw["entry_date"]))
                if not isinstance(raw.get("template_id"), str) or not raw["template_id"]:
                    raise ValueError("template_id")
                for k in ("answers", "field_colors", "provenance"):
                    if raw.get(k) is not None and not isinstance(raw[k], dict):
                        raise ValueError(k)
                created = raw.get("created_at")
                created = datetime.fromisoformat(created).isoformat(timespec="seconds") if created else None
                self.create_entry(entry_date=day, template_id=raw["template_id"], title=str(raw.get("title") or ""),
                                  theme=str(raw.get("theme") or "letterpress"), layout=str(raw.get("layout") or "letter"),
                                  answers=raw.get("answers") or {}, closing=str(raw.get("closing") or ""),
                                  field_colors=raw.get("field_colors") or {}, provenance=raw.get("provenance") or {},
                                  created_at=created)
                counts["entries"] += 1
                existing.add(key)
            except (KeyError, ValueError, TypeError):
                counts["skipped_entries"] += 1
        for tpl in document.get("templates", []) or []:
            if not isinstance(tpl, dict) or not tpl.get("id") or not _ID_RE.match(str(tpl.get("id"))):
                continue
            try:
                self.save_template(parse_template(tpl))
            except ValueError:
                continue
            counts["templates"] += 1
        for layout in document.get("layouts", []) or []:
            lid = layout.get("id") if isinstance(layout, dict) else None
            if not lid or not _ID_RE.match(str(lid)) or not isinstance(layout.get("html"), str):
                continue
            meta = layout.get("meta") if isinstance(layout.get("meta"), dict) else {}
            if lid in SHIPPED_LAYOUT_IDS and fingerprint(layout["html"]) == fingerprint(
                    self.content.text(f"layouts/{lid}.html.j2") or ""):
                if lid in self.setup["hidden_layouts"]:
                    self.restore_layout(lid)
                counts["layouts"] += 1
                continue                       # 跟內建一樣的不另外存（內建更新時跟著更新）
            self.setup["layouts"][lid] = {"html": layout["html"], "meta": meta}
            self.touch_setup()
            counts["layouts"] += 1
        if isinstance(document.get("preferences"), dict):
            try:
                self.save_prefs({**self.prefs(), **{k: v for k, v in document["preferences"].items()
                                                    if k in PREF_DEFAULTS}})
            except ValueError:
                pass
        return counts


# ══════════════════════════════════════════════════════════════════════════
# 指令的本體（原 backend/cli.py 的 cmd_*：回 (result, 一句人話)）
# ══════════════════════════════════════════════════════════════════════════

def _rich(text: str) -> str:
    return html.escape(text.strip(), quote=False).replace("\r\n", "\n").replace("\n", "<br>")


def _lines(value) -> list:
    if isinstance(value, list):
        return [str(v) for v in value if v is not None and str(v).strip()]
    if value is None:
        return []
    return [ln for ln in str(value).replace("\r\n", "\n").split("\n") if ln.strip()]


def _option(f: TField, value) -> str | None:
    text = str(value or "").strip()
    for o in f.options:
        if text in (o.id, o.label):
            return o.id
    return None


def _shape(f: TField, value, fmt: str):
    kind = f.kind
    if kind in ("choice", "scale"):
        picked = _option(f, value)
        if picked is None:
            return None, ("不是這一題的選項" if str(value or "").strip() else "空的")
        return picked, None
    if kind == "multi_choice":
        raw = value if isinstance(value, list) else str(value or "").replace("，", "、").replace(",", "、").split("、")
        picked = [p for p in (_option(f, v) for v in raw) if p]
        unknown = [str(v) for v in raw if str(v).strip() and _option(f, v) is None]
        if not picked:
            return None, "不是這一題的選項" if unknown else "空的"
        return list(dict.fromkeys(picked)), ("有幾個不是選項，略過：" + "、".join(unknown)) if unknown else None
    if kind == "short_text":
        text = "、".join(_lines(value)) if isinstance(value, list) else str(value or "").strip()
        return (text, None) if text else (None, "空的")
    if kind == "fill":
        blanks = max((f.pattern or "").count("{}"), 1)
        items = [str(v).strip() for v in value] if isinstance(value, list) else [str(value or "").strip()]
        items = (items + [""] * blanks)[:blanks]
        return (items, None) if any(items) else (None, "空的")
    if kind == "list":
        items = _lines(value)
        if fmt == "plain":
            items = [_rich(i) for i in items]
        return (items, None) if items else (None, "空的")
    text = "\n".join(_lines(value)) if isinstance(value, list) else str(value or "")
    if not text.strip():
        return None, "空的"
    return (_rich(text) if fmt == "plain" else text), None


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, list):
        return not any(to_plain(str(v)).strip() for v in value)
    return not to_plain(str(value)).strip()


def _merge(f: TField, old, new, mode: str):
    if mode != "append" or _is_blank(old):
        return new
    if f.kind in ("list", "multi_choice"):
        return list(old) + [v for v in new if f.kind == "list" or v not in old]
    if f.kind == "text":
        return f"{old}<br><br>{new}"
    if f.kind == "short_text":
        return f"{old}；{new}"
    return new


class _Overwrite:
    def __init__(self, raw: str, known: set):
        self.all = raw.strip().lower() == "all"
        self.ids = set() if self.all else {v.strip() for v in raw.split(",") if v.strip()}
        unknown = sorted(self.ids - known - {"closing"})
        if unknown:
            raise DiaryError(f"要換掉的題目這個題組沒有：{'、'.join(unknown)}（/diary_template 看每一題的 id）")

    def __contains__(self, field_id: str) -> bool:
        return self.all or field_id in self.ids


def sanitize_answers(template: Template, answers: dict, *, keep_extra: bool = True) -> dict:
    """每個答案整理成那一題要的形狀、清掉不安全的 HTML；這個題組沒有的題目**留著**（原 services.sanitize_answers）。"""
    fields = template.field_map()
    clean: dict = {}
    for field_id, value in answers.items():
        tf = fields.get(field_id)
        if tf is None:
            if keep_extra:
                if isinstance(value, str):
                    cleaned = sanitize(value)[:MAX_TEXT_CHARS]
                    if cleaned:
                        clean[field_id] = cleaned
                elif isinstance(value, list):
                    items = [sanitize(str(v))[:MAX_TEXT_CHARS] for v in value[:MAX_LIST_ROWS]
                             if isinstance(v, (str, int, float))]
                    items = [i for i in items if i]
                    if items:
                        clean[field_id] = items
            continue
        if tf.kind in ("choice", "scale"):
            if isinstance(value, str) and value in {o.id for o in tf.options}:
                clean[field_id] = value
        elif tf.kind == "multi_choice":
            valid = {o.id for o in tf.options}
            if isinstance(value, list):
                clean[field_id] = [v for v in value if v in valid]
        elif tf.kind == "short_text":
            if isinstance(value, str):
                clean[field_id] = value.strip()[:300]
        elif tf.kind == "fill":
            blanks = (tf.pattern or "").count("{}")
            if isinstance(value, list):
                clean[field_id] = [str(v).strip()[:300] for v in value[:max(blanks, 1)]]
        elif tf.kind == "list":
            if isinstance(value, list):
                clean[field_id] = [sanitize(str(v))[:MAX_TEXT_CHARS] for v in value[:MAX_LIST_ROWS]]
        else:
            if isinstance(value, str):
                clean[field_id] = sanitize(value)[:MAX_TEXT_CHARS]
    return clean


def recovery_template(entry: Entry) -> Template:
    """題組被刪掉了：每一個答案照純文字放在同一段，日記照樣讀得到、輸出得了（原 services.recovery_template）。"""
    fields = [TField(id=fid, label=fid, kind="list" if isinstance(value, list) else "text")
              for fid, value in entry.answers.items()]
    return Template(id=entry.template_id, name=f"{entry.template_id}（題組已刪除）",
                    description="原本的題組已經不在了，以下是這篇保留下來的內容。", structure="one-part",
                    sections=[Section(id="recovered", title="保留下來的內容", color="#6b675e", fields=fields)])


def _template_json(t: Template, brief: bool = False) -> dict:
    out = {"id": t.id, "name": t.name, "description": t.description, "group": t.group or "其他", "order": t.order,
           "when": t.when, "minutes": t.minutes, "moods": t.moods, "tags": t.tags, "structure": t.structure,
           "reflection": t.reflection, "flow": t.flow, "default_layout": t.default_layout,
           "default_theme": t.default_theme, "closing_label": t.closing_label,
           "fields": sum(len(s.fields) for s in t.sections)}
    if brief:
        return out
    out["sections"] = [{"id": s.id, "title": s.title, "fields": [
        {"id": f.id, "label": f.label, "kind": f.kind,
         **({"options": [{"id": o.id, "label": o.label} for o in f.options]} if f.options else {}),
         **({"pattern": f.pattern, "blanks": (f.pattern or "").count("{}")} if f.pattern else {}),
         **({"hint": f.hint} if f.hint else {}),
         **({"ai_hint": f.ai_hint} if f.ai_hint else {})} for f in s.fields]} for s in t.sections]
    return out


def _ai_fields(entry: Entry) -> list:
    marks = entry.provenance.get("fields", {}) if isinstance(entry.provenance, dict) else {}
    if not isinstance(marks, dict):
        return []
    return sorted(k for k, v in marks.items() if isinstance(v, dict) and v.get("author") == "ai")


def _summary(entry: Entry, template: Template | None = None) -> dict:
    def count(value) -> int:
        if isinstance(value, list):
            return sum(len(to_plain(str(v)).strip()) for v in value)
        return len(to_plain(str(value or "")).strip())

    kinds = {f.id: f.kind for f in template.field_map().values()} if template else {}
    chars = count(entry.closing) + sum(count(v) for k, v in entry.answers.items() if kinds.get(k) not in TICK_KINDS)
    return {"id": entry.id, "date": entry.entry_date.isoformat(), "template": entry.template_id,
            "template_name": template.name if template else entry.template_id, "title": entry.title,
            "layout": entry.layout, "theme": entry.theme, "chars": chars, "ai_fields": _ai_fields(entry),
            "updated_at": entry.updated_at.isoformat(timespec="seconds")}


def entry_word_count(entry: Entry, template: Template | None) -> int:
    """編輯器清單用的字數（勾選題不算；原 routers/entries._entry_word_count）。"""
    kinds = {fid: f.kind for fid, f in template.field_map().items()} if template else {}
    parts = [to_plain(entry.closing)]
    for field_id, value in entry.answers.items():
        if kinds.get(field_id) in TICK_KINDS:
            continue
        if isinstance(value, str):
            parts.append(to_plain(value))
        elif isinstance(value, list):
            parts.extend(to_plain(str(v)) for v in value)
    return len([c for c in "".join(parts) if not c.isspace()])


def orphan_answers(template: Template, answers: dict) -> dict:
    known = set(template.field_map())
    return {k: v for k, v in answers.items() if k not in known}


def orphan_previews(template: Template, entry: Entry) -> list:
    out = []
    for field_id, value in orphan_answers(template, entry.answers).items():
        if isinstance(value, list):
            text = " / ".join(t for t in (to_plain(str(v)) for v in value) if t.strip())
        else:
            text = to_plain(str(value))
        text = text.strip()
        if text:
            out.append({"field_id": field_id, "preview": text[:80] + ("…" if len(text) > 80 else "")})
    return out


#: 純文字寫得進去的題型（一段字只放進第一個這種題目）
_PLAIN_KINDS = ("list", "text", "short_text")


class Diary:
    """一個人、一次指令：Book（他自己的資料）＋原本命令列的每個動作。"""

    def __init__(self, book: Book):
        self.book = book

    # ── 共用 ───────────────────────────────────────────────────────
    def day(self, raw) -> date:
        word = str(raw or "").strip().lower()
        if word in DAY_WORDS:
            t = now()
            if t.hour < self.book.prefs()["day_start_hour"]:
                t -= timedelta(days=1)
            return (t - timedelta(days=DAY_WORDS[word])).date()
        try:
            return date.fromisoformat(str(raw or "").strip())
        except ValueError:
            raise DiaryError(f"日期要寫成 YYYY-MM-DD、today 或 yesterday（收到：{raw}）") from None

    def template_or_fail(self, template_id) -> Template:
        t = self.book.get_template(str(template_id or "").strip())
        if t is None:
            raise DiaryError(f"找不到題組：{template_id}（/diary_templates 看全部）")
        return t

    def entry_and_template(self, entry: Entry):
        return entry, self.book.get_template(entry.template_id) or recovery_template(entry)

    def entry_by_id(self, raw) -> Entry:
        try:
            entry_id = int(str(raw).strip().lstrip("#"))
        except ValueError:
            raise DiaryError(f"編號要是數字（收到：{raw}）") from None
        entry = self.book.get_entry(entry_id)
        if entry is None:
            raise DiaryError(f"找不到日記 #{entry_id}（/diary_list 看編號）")
        return entry

    def find(self, *, id=None, date_=None, template=None) -> Entry:
        if id:
            return self.entry_by_id(id)
        if not (date_ and template):
            raise DiaryError("要給編號，或日期加題組")
        entry = self.book.get_entry_for(self.day(date_), str(template).strip())
        if entry is None:
            raise DiaryError(f"{date_} 還沒有用「{template}」寫的日記")
        return entry

    # ── 題組 ──────────────────────────────────────────────────────
    def templates(self, *, tid="", brief=True, structure="", reflection="", flow="", tag=""):
        if tid:
            t = self.template_or_fail(tid)
            data = _template_json(t, brief)
            lines = [f"{t.name}（{t.id}）：{data['fields']} 題"]
            if not brief:
                for s in t.sections:
                    for f in s.fields:
                        extra = "／".join(o.label for o in f.options) if f.options else (f.pattern or "")
                        lines.append(f"• {f.label}（{f.id}，{f.kind}）" + (f"：{extra}" if extra else ""))
            return {"template": data}, "\n".join(lines)
        problem = check_filter_values(structure=structure, reflection=reflection, flow=flow)
        if problem:
            raise DiaryError(problem)
        chosen = filter_templates(self.book.all_templates(), structure=structure, reflection=reflection, flow=flow,
                                  tag=tag)
        items = [_template_json(t, brief) for t in chosen]
        text = "\n".join(
            f"{t['group']}｜{t['name']}（{t['id']}）約 {t['minutes'] or '?'} 分鐘｜{STRUCTURES[t['structure']]}・"
            f"{REFLECTIONS[t['reflection']]}・{'→'.join(FLOW_TOKENS[f] for f in t['flow'])}：{t['when'] or t['description']}"
            for t in items)
        filters = {k: v for k, v in (("structure", structure), ("reflection", reflection), ("flow", flow),
                                     ("tag", tag)) if v}
        return {"templates": items, "count": len(items), **({"filters": filters} if filters else {})}, \
            text or "沒有符合的題組。"

    def suggest(self, text: str, limit: int = 5):
        if not str(text or "").strip():
            raise DiaryError("要給一段話：把想記的那段話放進來")
        prefs = self.book.prefs()
        rules = self.book.content.json("suggest.json", {})
        if not isinstance(rules, dict) or not isinstance(rules.get("signals"), dict):
            rules = {"signals": {}, "length": {"short": 40, "long": 300}, "faith_penalty": 4}
        result = suggest(text, self.book.all_templates(), rules=rules, faith=prefs["suggest_faith"],
                         default_id=prefs["template_id"] or "", limit=max(1, min(20, int(limit or 5))))
        lines = [f"{i + 1}. {s['name']}（{s['id']}）{s['score']} 分：" + "；".join(s["why"])
                 for i, s in enumerate(result["suggestions"])]
        if result["signals"]:
            lines.append("看到的線索：" + "、".join(f"{s['label']}（{'、'.join(s['words'])}）" for s in result["signals"]))
        return result, "\n".join(lines)

    def categories(self):
        cats = categories(self.book.all_templates())
        text = "\n".join(f"{name}：" + "、".join(f"{c['label']}（{len(c['templates'])}）" for c in cats[name] if c["templates"])
                         for name in ("structure", "reflection", "flow", "groups", "tags"))
        return cats, text

    def tpl_restore(self, tid: str):
        tid = str(tid or "").strip()
        if not tid.replace("-", "").replace("_", "").isalnum():
            raise DiaryError("題組 id 只能是英數字、- 和 _")
        if not self.book.restore_template(tid):
            raise DiaryError(f"{tid} 不是內建題組，沒有可以放回的版本")
        t = self.book.get_template(tid)
        return {"template": _template_json(t, True) if t else {"id": tid}}, f"放回了內建的「{t.name if t else tid}」（{tid}）"

    def tpl_save(self, raw_json: str):
        try:
            raw = json.loads(raw_json)
        except ValueError as e:
            raise DiaryError(f"題組要是一個 JSON 物件（{e}）") from None
        if not isinstance(raw, dict):
            raise DiaryError("題組要是一個 JSON 物件")
        tid = str(raw.get("id") or "")
        if not _ID_RE.match(tid):
            raise DiaryError("題組 id 只能使用英數字、- 與 _。")
        try:
            t = parse_template(raw)
        except ValueError as e:
            raise DiaryError(f"題組不合：{e}") from None
        existed = self.book.get_template(tid) is not None
        self.book.save_template(t)
        return {"template": _template_json(t, True), "created": not existed}, \
            f"{'改好了' if existed else '新增了'}題組「{t.name}」（{t.id}，{sum(len(s.fields) for s in t.sections)} 題）"

    def tpl_rm(self, tid: str, force: bool = False):
        tid = str(tid or "").strip()
        if self.book.get_template(tid) is None:
            raise DiaryError(f"找不到題組：{tid}")
        in_use = self.book.entries_using_template(tid)
        if in_use and not force:
            raise DiaryError(f"有 {in_use} 篇日記還在用這個題組。內容不會不見，但刪掉之後那些日記只會看到純文字。"
                             f"確定要刪就加 force=yes 再一次。")
        self.book.delete_template(tid)
        shipped = tid in self.book.content.templates()
        return {"deleted": tid, "entries_using": in_use}, \
            f"刪掉了題組 {tid}" + ("（內建的：/diary_tpl_restore 拿得回來）" if shipped else "")

    # ── 版型 ──────────────────────────────────────────────────────
    def layouts(self):
        items = self.book.all_layouts()
        keys = ("id", "name", "description", "tags", "default_theme", "card_paper")
        items = [{k: x[k] for k in keys} for x in items]
        return {"layouts": items}, "\n".join(f"{x['name']}（{x['id']}）：{x['description']}" for x in items)

    def themes(self):
        items = [{"id": t.id, "name": t.name, "tags": list(t.tags), "swatch": [t.paper, t.ink, t.accent, t.backdrop]}
                 for t in THEMES.values()]
        return {"themes": items}, "\n".join(f"{t['name']}（{t['id']}）：{'、'.join(t['tags'])}" for t in items)

    def layout_rm(self, lid: str):
        lid = str(lid or "").strip()
        if not self.book.delete_layout(lid):
            raise DiaryError(f"找不到版型：{lid}")
        return {"deleted": lid}, f"刪掉了版型 {lid}" + \
            ("（內建的：/diary_layout_restore 拿得回來）" if lid in SHIPPED_LAYOUT_IDS else "")

    def layout_restore(self, lid: str):
        lid = str(lid or "").strip()
        if not self.book.restore_layout(lid):
            raise DiaryError(f"{lid} 不是內建版型，沒有可以還原的版本。")
        return {"restored": lid}, f"放回了內建版型 {lid}"

    # ── 寫入（原 cmd_entry_put） ────────────────────────────────────
    def put(self, *, date_, template, payload_raw, merge="fill", fmt="plain", overwrite="", author="user",
            title=None):
        book = self.book
        day = self.day(date_)
        t = self.template_or_fail(template)
        ai = author == "ai"
        if merge not in MERGES:
            raise DiaryError(f"merge 只能是 {'、'.join(MERGES)}")
        if author not in AUTHORS:
            raise DiaryError("author 只能是 user 或 ai")
        if ai and merge == "append":
            raise DiaryError("AI 整理的版本不能接在作者的字後面（merge=append）：會混成一段。用 fill（只填空的）或點名要換的題目")
        payload = self._payload(payload_raw, t)
        plain_field = payload.pop("_plain_field", None)
        if plain_field and ai:
            raise DiaryError("AI 整理的版本要給 JSON（answers＋作者原文 source_text），不收純文字：純文字一律當作者自己的話")
        if title:
            payload["title"] = title
        fields = t.field_map()
        ow = _Overwrite(overwrite or "", set(fields))
        source_text = str(payload.get("source_text") or "").strip()
        sources = {str(k): str(v).strip() for k, v in (payload.get("sources") or {}).items() if str(v).strip()}
        if ai and not (source_text or sources):
            raise DiaryError("AI 整理的版本一定要附作者原文：JSON 裡的 source_text（整篇）或 sources（每一題）。"
                             "AI 整理的版本永遠跟原文一起存。")
        existing = book.get_entry_for(day, t.id)
        stored = dict(existing.answers) if existing else {}
        provenance = dict(existing.provenance) if existing and isinstance(existing.provenance, dict) else {}
        marks = dict(provenance.get("fields") or {})
        stamp = _stamp()
        changed: dict = {}
        filled, kept, protected, overwritten, ignored = [], [], [], [], []

        def decide(field_id, old) -> str:
            if _is_blank(old) or field_id in ow:
                return "replace"
            if field_id == plain_field and fields[field_id].kind == "list":
                return "append"
            if merge == "fill":
                return "keep"
            if ai:
                return "protect"
            return "append" if merge == "append" else "replace"

        def mark(field_id):
            if ai:
                marks[field_id] = {"author": "ai", "at": stamp, **({"source": sources[field_id]} if field_id in sources else {})}
            else:
                marks.pop(field_id, None)

        for field_id, value in payload.get("answers", {}).items():
            f = fields.get(str(field_id))
            if f is None:
                ignored.append({"field": str(field_id), "why": "這個題組沒有這一題"})
                continue
            shaped, why = _shape(f, value, fmt)
            if shaped is None:
                if why != "空的":
                    ignored.append({"field": f.id, "why": why})
                continue
            if why:
                ignored.append({"field": f.id, "why": why})
            verdict = decide(f.id, stored.get(f.id))
            if verdict == "keep":
                kept.append(f.id)
                continue
            if verdict == "protect":
                protected.append(f.id)
                continue
            if verdict == "replace" and not _is_blank(stored.get(f.id)):
                overwritten.append(f.id)
            changed[f.id] = _merge(f, stored.get(f.id), shaped, verdict)
            filled.append(f.id)
            mark(f.id)

        closing_in = str(payload.get("closing") or "")
        closing = None
        if closing_in.strip():
            new = _rich(closing_in) if fmt == "plain" else closing_in
            old = existing.closing if existing else ""
            verdict = decide("closing", old)
            if verdict == "keep":
                kept.append("closing")
            elif verdict == "protect":
                protected.append("closing")
            else:
                if verdict == "replace" and not _is_blank(old):
                    overwritten.append("closing")
                closing = sanitize(f"{old}<br><br>{new}" if verdict == "append" else new)
                filled.append("closing")
                mark("closing")
        title_in = str(payload.get("title") or "").strip()[:120]
        theme = str(payload.get("theme") or "") or None
        layout = str(payload.get("layout") or "") or None

        new_provenance = None
        if ai and filled:
            kept_text = str(provenance.get("source_text") or "").strip()
            if source_text and source_text not in kept_text:
                kept_text = f"{kept_text}\n\n{source_text}".strip()
            new_provenance = {**provenance, "fields": marks, **({"source_text": kept_text} if kept_text else {})}
        elif marks != (provenance.get("fields") or {}):
            new_provenance = {**provenance, "fields": marks}

        book.snapshot_if_stale("cli")
        if existing is None:
            prefs = book.prefs()
            use = prefs["use_as_default"]
            default_theme = (prefs["theme"] if use and prefs["theme"] in THEMES else None) or t.default_theme
            default_layout = (prefs["layout"] if use and prefs["layout"] and book.layout_exists(prefs["layout"])
                              else None) or t.default_layout
            entry = book.create_entry(entry_date=day, template_id=t.id, title=title_in,
                                      theme=theme if theme in THEMES else default_theme, layout=layout or default_layout,
                                      answers=sanitize_answers(t, changed), closing=closing or "",
                                      provenance=new_provenance or {})
            created = True
        else:
            entry = book.update_entry(existing.id, {
                "answers": sanitize_answers(t, {**stored, **changed}) if changed else None,
                "closing": closing,
                "title": title_in if title_in and (merge != "fill" or not existing.title) else None,
                "theme": theme if theme in THEMES else None,
                "layout": layout,
                "provenance": new_provenance}) or existing
            created = False

        result = {"entry": _summary(entry, t), "created": created, "author": author, "filled": filled, "kept": kept,
                  "protected": protected, "overwritten": overwritten, "ignored": ignored}
        if plain_field:
            result["plain_text_field"] = plain_field
        verb = "新增" if created else "更新"
        text = f"{verb}了 {entry.entry_date} 的「{t.name}」（#{entry.id}）：填了 {len(filled)} 題"
        if plain_field and plain_field in filled:
            text = f"{verb}了 {entry.entry_date} 的「{t.name}」（#{entry.id}）：記進「{fields[plain_field].label}」"
            if isinstance(changed.get(plain_field), list):
                text += f"（現在 {len(changed[plain_field])} 則）"
        elif plain_field and plain_field in kept:
            text = (f"沒有寫進去：{entry.entry_date} 的「{t.name}」的「{fields[plain_field].label}」已經有寫了"
                    f"（要換掉用 /diary_overwrite 點名 {plain_field}）")
            kept = []
        if ai and filled:
            text += "（標為 AI 整理、附作者原文）"
        if overwritten:
            text += f"，其中 {len(overwritten)} 題換掉了原本的答案"
        if kept:
            text += f"，{len(kept)} 題原本就有寫、沒有動"
        if protected:
            text += f"，{len(protected)} 題是作者自己寫的、沒有動（要換要點名 overwrite）"
        if ignored:
            text += f"，{len(ignored)} 題略過"
        return result, text

    def _payload(self, raw, template: Template) -> dict:
        raw = str(raw or "")
        if not raw.strip():
            raise DiaryError("內容是空的：直接給要記的話（寫進第一題），或一個 JSON 物件，例如 {\"answers\": {\"fact\": \"今天…\"}}")
        if not raw.lstrip().startswith("{"):
            target = next((f for f in template.field_map().values() if f.kind in _PLAIN_KINDS), None)
            if target is None:
                raise DiaryError(f"「{template.name}」沒有可以直接打字的題目：要給 JSON，例如 {{\"answers\": {{\"題目 id\": 答案}}}}")
            return {"answers": {target.id: raw.strip()}, "_plain_field": target.id}
        try:
            payload = json.loads(raw)
        except ValueError as e:
            raise DiaryError(f"內容不是 JSON：{e}") from None
        if not isinstance(payload, dict):
            raise DiaryError("內容要是一個 JSON 物件（{...}）")
        if not isinstance(payload.get("answers", {}), dict):
            raise DiaryError("answers 要是一個物件：{\"題目 id\": 答案}")
        sources = payload.get("sources", {})
        if sources and not isinstance(sources, dict):
            raise DiaryError("sources 要是一個物件：{\"題目 id\": \"這一題的原文\"}")
        return payload

    # ── 讀出 ──────────────────────────────────────────────────────
    def entry_json(self, entry: Entry) -> dict:
        entry, t = self.entry_and_template(entry)
        return {**_summary(entry, t), "answers": entry.answers, "closing": entry.closing,
                "provenance": entry.provenance, "text": build_document(entry, t).to_plain_text(),
                "hidden_answers": orphan_previews(t, entry)}

    def show(self, *, id=None, date_=None, template=None):
        if id:
            entries = [self.entry_by_id(id)]
            day = entries[0].entry_date.isoformat()
        else:
            d = self.day(date_ or "today")
            day = d.isoformat()
            if template:
                one = self.book.get_entry_for(d, str(template).strip())
                entries = [one] if one else []
            else:
                entries = self.book.list_entries_on(d)
        items = [self.entry_json(e) for e in entries]
        texts = []
        for i in items:
            extra = ""
            if i["hidden_answers"]:
                extra = f"\n（另外還留著 {len(i['hidden_answers'])} 題不屬於這個題組的內容，換回原本的題組就看得到）"
            texts.append(f"#{i['id']}　{i['template_name']}\n{i['text']}{extra}")
        return {"date": day, "entries": items, "count": len(items)}, "\n\n".join(texts) or f"{day} 還沒有日記。"

    def list_(self, limit: int = 30):
        limit = max(1, min(int(limit or 30), 500))
        items = []
        for e in self.book.list_entries(limit=limit):
            t = self.book.get_template(e.template_id)
            s = _summary(e, t)
            s["is_empty"] = entry_word_count(e, t) == 0 and not e.title.strip()
            items.append(s)
        text = "\n".join(f"{i['date']}　{i['template_name']}　{i['chars']} 字（#{i['id']}）" + ("　空白" if i["is_empty"] else "")
                         for i in items)
        return {"entries": items}, text or "還沒有任何日記。"

    # ── 改、刪、歷史、垃圾桶 ─────────────────────────────────────
    def set_title(self, entry_id, title: str):
        entry = self.entry_by_id(entry_id)
        title = str(title or "").strip()[:120]
        entry = self.book.update_entry(entry.id, {"title": title})
        return {"entry": _summary(*self.entry_and_template(entry))}, \
            f"#{entry.id} 的標題改成「{title}」" if title else f"#{entry.id} 的標題清掉了"

    def switch(self, entry_id, template_id):
        """換題組：答案一個都不刪；新題組沒有的題目留在這篇裡、只是暫時不顯示（原 PATCH template_id ＋ orphans）。"""
        entry = self.entry_by_id(entry_id)
        t = self.template_or_fail(template_id)
        merged = sanitize_answers(t, dict(entry.answers))
        entry = self.book.update_entry(entry.id, {"template_id": t.id, "answers": merged})
        hidden = orphan_previews(t, entry)
        text = f"#{entry.id} 換成「{t.name}」"
        if hidden:
            text += f"；原本有 {len(hidden)} 題這個題組沒有，內容還留著（換回原本的題組就會出現）"
        return {"entry": _summary(entry, t), "hidden_answers": hidden}, text

    def rm(self, entry_id):
        entry = self.entry_by_id(entry_id)
        entry, t = self.entry_and_template(entry)
        self.book.delete_entry(entry.id)
        return {"deleted": entry.id}, f"🗑 刪掉了 {entry.entry_date}「{t.name}」（#{entry.id}）。在垃圾桶裡：/diary_undelete {entry.id} 救得回來。"

    def tidy(self):
        gone = []
        for e in self.book.list_entries(limit=100_000):
            t = self.book.get_template(e.template_id)
            if entry_word_count(e, t) == 0 and not e.title.strip():
                self.book.delete_entry(e.id)
                gone.append(e.id)
        text = f"清掉了 {len(gone)} 筆完全空白的日記（在垃圾桶裡，救得回來）。" if gone else "沒有完全空白的日記。"
        return {"deleted": gone}, text

    def history(self, entry_id):
        entry = self.entry_by_id(entry_id)
        revs = self.book.list_revisions(entry.id)
        reasons = {"edit": "改之前", "delete": "刪之前", "restore": "還原之前", "import": "匯入之前"}
        text = "\n".join(f"版本 {r['id']}　{r['created_at'].replace('T', ' ')}　{reasons.get(r['reason'], r['reason'])}　"
                         f"{r['char_count']} 字" for r in revs)
        return {"entry_id": entry.id, "revisions": revs}, \
            (f"#{entry.id} 的歷史版本（/diary_restore 版本號 放回去）：\n" + text) if revs else f"#{entry.id} 還沒有歷史版本。"

    def restore(self, revision_id):
        try:
            rid = int(str(revision_id).strip())
        except ValueError:
            raise DiaryError("版本號要是數字（/diary_history 看）") from None
        entry = self.book.restore_revision(rid)
        if entry is None:
            raise DiaryError(f"找不到這個版本 #{rid}")
        return {"entry": _summary(*self.entry_and_template(entry))}, \
            f"#{entry.id} 放回了版本 {rid}（換掉的那一份也留了一個版本，按錯了也回得來）"

    def trash(self):
        items = self.book.list_trash()
        text = "\n".join(f"#{i['entry_id']}　{i['entry_date']}　{i['title'] or i['template_id']}　{i['char_count']} 字　"
                         f"刪於 {i['created_at'].replace('T', ' ')}" for i in items)
        return {"trash": items}, ("垃圾桶（/diary_undelete 編號 救回來）：\n" + text) if items else "垃圾桶是空的。"

    def undelete(self, entry_id):
        try:
            eid = int(str(entry_id).strip().lstrip("#"))
        except ValueError:
            raise DiaryError("編號要是數字（/diary_trash 看）") from None
        entry = self.book.undelete_entry(eid)
        if entry is None:
            raise DiaryError("垃圾桶裡沒有這一篇。")
        return {"entry": _summary(*self.entry_and_template(entry))}, f"救回來了：{entry.entry_date}（#{entry.id}）"

    def purge(self, entry_id):
        try:
            eid = int(str(entry_id).strip().lstrip("#"))
        except ValueError:
            raise DiaryError("編號要是數字（/diary_trash 看）") from None
        if not self.book.purge_trash(eid):
            raise DiaryError("垃圾桶裡沒有這一篇（還沒刪的日記不能永久刪除）。")
        return {"purged": eid}, f"#{eid} 永久刪除了（連歷史版本一起），救不回來。"

    # ── 備份 ──────────────────────────────────────────────────────
    def backups(self):
        st = self.book.stats()
        items = [{k: b[k] for k in ("name", "created_at", "automatic", "size_bytes", "entries") if k in b}
                 for b in self.book.backups()]
        lines = [f"日記 {st['entries']} 篇、歷史版本 {st['revisions']} 份、垃圾桶 {st['trash']} 篇、備份 {len(items)} 份"]
        lines += [f"{b['name']}　{b['created_at'].replace('T', ' ')}　{'自動' if b['automatic'] else '手動'}　{b.get('entries', '?')} 篇"
                  for b in items]
        return {"status": st, "backups": items}, "\n".join(lines)

    def backup(self):
        info = self.book.snapshot("manual", manual=True)
        if info is None:
            raise DiaryError("還沒有任何日記，沒有東西可以備份。")
        return {"backup": info}, f"備份好了：{info['name']}（手動的備份不會被自動清掉）"

    def backup_restore(self, name):
        info = self.book.restore_backup(str(name or "").strip())
        return {"restored": info["name"]}, f"整本日記還原成 {info['name']}（還原前的樣子也備份了一份）"

    def backup_rm(self, name):
        info = self.book.delete_backup(str(name or "").strip())
        return {"deleted": info["name"]}, f"刪掉了備份 {info['name']}"

    # ── 設定、起始組合 ───────────────────────────────────────────
    def describe_settings(self) -> list:
        prefs = self.book.prefs()
        blank = dict(pref_defaults(), palette=[dict(p) for p in DEFAULT_PALETTE])
        return [{"key": k, "value": prefs[k], "default": blank[k], "what": what, "how": how}
                for k, (what, how) in PREF_FIELDS.items()]

    @staticmethod
    def setting_row(row: dict) -> str:
        return f"{row['key']} = {json.dumps(row['value'], ensure_ascii=False)}　（{row['what']}；{row['how']}）"

    def settings(self, key=""):
        rows = self.describe_settings()
        if key:
            row = next((r for r in rows if r["key"] == key), None)
            if row is None:
                raise DiaryError(f"沒有這個設定：{key}（有：{'、'.join(PREF_FIELDS)}）")
            return row, self.setting_row(row)
        return {"settings": rows}, "\n".join(self.setting_row(r) for r in rows)

    def settings_set(self, key: str, value: str):
        key, value = str(key or "").strip(), str(value if value is not None else "")
        checks = {
            "template_id": (lambda v: self.book.get_template(v) is not None, "沒有這個題組（/diary_templates 看全部）"),
            "layout": (self.book.layout_exists, "沒有這個版型（/diary_layouts 看全部）"),
            "theme": (lambda v: v in THEMES, f"紙張顏色只有 {'、'.join(THEMES)}"),
            "card_paper": (lambda v: v in PAPERS, f"紙只有 {'、'.join(PAPERS)}"),
        }
        if key not in PREF_FIELDS:
            raise DiaryError(f"沒有這個設定：{key}（有：{'、'.join(PREF_FIELDS)}）")
        if key in checks and value not in ("", "default", "null", "none"):
            ok, why = checks[key]
            if not ok(value):
                raise DiaryError(f"{key} 的值不對：{why}（收到：{value}）")
        if value == "default":
            new = [dict(p) for p in DEFAULT_PALETTE] if key == "palette" else PREF_DEFAULTS[key]
        else:
            try:
                new = json.loads(value)
            except ValueError:
                new = value
            if key in ("template_id", "layout", "theme", "card_paper") and new in _UNSET:
                new = None
            if key in ("template_id", "layout", "theme", "card_paper", "image_style", "preset") and \
                    isinstance(new, (int, float)) and not isinstance(new, bool):
                new = value                  # 例如題組 id 是數字樣子：當成字
            if key == "card_width" and isinstance(new, int) and not 480 <= new <= 2400:
                raise DiaryError("card_width 要在 480 到 2400 之間")
        try:
            data = {**self.book.prefs(), key: new}
            _check_prefs(data)
        except ValueError as e:
            raise DiaryError(f"{key} 的值不對：{e}（{PREF_FIELDS[key][1]}）") from None
        if key == "palette" and not new:
            raise DiaryError("palette 不能是空的")
        prefs = self.book.save_prefs(data)
        row = next(r for r in self.describe_settings() if r["key"] == key)
        return {**row, "saved": True, "settings": prefs}, "改好了：" + self.setting_row(row)

    def setup_status(self):
        prefs = self.book.prefs()
        st = self.book.stats()
        tids = [t.id for t in self.book.all_templates()]
        more = self.book.available_templates()
        table = self.book.preset_table()
        result = {"preset": prefs["preset"], "presets": {k: v.get("label", k) for k, v in table.items()},
                  "templates": len(tids), "template_ids": tids, "entries": st["entries"], "preferences": prefs,
                  "available": [{"id": t.id, "name": t.name, "group": t.group or "其他", "when": t.when} for t in more]}
        text = (f"日記存在 SYNOX 保管箱（加密、只有你的）\n起始組合：{prefs['preset'] or '（沒跑過 /diary_setup_init）'}\n"
                f"題組：{len(tids)} 個；日記：{st['entries']} 篇")
        if more:
            text += "\n還沒拿出來的內建題組（/diary_tpl_restore <id>）：" + "、".join(f"{t.name}（{t.id}）" for t in more)
        return result, text

    def setup_init(self, preset: str):
        table = self.book.preset_table()
        if preset not in table:
            raise DiaryError(f"沒有這個起始組合：{preset}（有：{'、'.join(table) or '沒有'}）")
        before = sorted(t.id for t in self.book.all_templates())
        # 原 init_preset：只裝「還沒處理過」的內建題組（只加不刪；已經處理過、刻意沒裝的要 /diary_tpl_restore）
        added = self.book.install_seeds(keep=self.book._preset_templates(preset))
        prefs = self.book.prefs()
        changes = {"preset": preset}
        for key, value in (table[preset].get("settings") or {}).items():
            if key in PREF_FIELDS and prefs.get(key) == PREF_DEFAULTS.get(key):
                changes[key] = value
        prefs = self.book.save_prefs({**prefs, **changes})
        result = {"preset": preset, "label": table[preset].get("label", preset),
                  "templates_added": sorted(set(added) - set(before)),
                  "templates_now": sorted(t.id for t in self.book.all_templates()), "already_had": before,
                  "settings": {k: prefs[k] for k in changes}}
        text = (f"用「{result['label']}」設好了：裝了 {len(result['templates_added'])} 個題組"
                f"（原本就有 {len(before)} 個，一個都沒動），現在共 {len(result['templates_now'])} 個；"
                f"設定：{json.dumps(result['settings'], ensure_ascii=False)}")
        return result, text

    # ── 輸出 ──────────────────────────────────────────────────────
    def export(self, *, id=None, date_=None, template=None, fmt="both", layout=None, theme=None, width=None,
               paper=None, gift_cover=False, gift_message=""):
        """→ (result, text, files)；files＝[(檔名, 內容)]（原 cmd_export；寫檔與傳送由呼叫的人做）。"""
        if fmt not in EXPORTS:
            raise DiaryError(f"format 只能是 {'、'.join(EXPORTS)}")
        entry = self.find(id=id, date_=date_, template=template)
        entry, t = self.entry_and_template(entry)
        prefs = self.book.prefs()
        updates = {}
        if theme:
            if theme not in THEMES:
                raise DiaryError(f"沒有這種紙張顏色：{theme}（{'／'.join(THEMES)}）")
            updates["theme"] = theme
        if layout:
            if not self.book.layout_exists(layout):
                raise DiaryError(f"沒有這個版型：{layout}（/diary_layouts 看全部）")
            updates["layout"] = layout
        if paper and paper not in PAPERS:
            raise DiaryError(f"紙只有 {'、'.join(PAPERS)}")
        if updates:
            entry = entry.copy(**updates)
        doc = build_document(entry, t)
        stem = re.sub(r"[^0-9A-Za-z._-]+", "-", f"{entry.entry_date.isoformat()}-{entry.template_id}-{entry.id}")
        result = {"entry": _summary(entry, t), "layout": doc.layout, "theme": doc.theme}
        files = []
        if fmt in ("both", "html"):
            try:
                page = self.book.render_html(doc, gift_cover=gift_cover, gift_message=gift_message)
            except TemplateError as e:
                raise DiaryError(f"版型 {doc.layout} 有錯：{e}") from None
            files.append((f"{stem}.html", page))
            result["html"] = f"{stem}.html"
        if fmt in ("both", "card"):
            chosen = self.book.get_layout(doc.layout)
            paper = paper or prefs["card_paper"] or (chosen["card_paper"] if chosen else "ruled")
            try:
                w = int(width) if width not in (None, "") else int(prefs["card_width"] or 1080)
            except ValueError:
                raise DiaryError("寬度要是整數（480–2400）") from None
            w = max(480, min(2400, w))
            svg = render_document_svg(doc, width=w, paper=paper)
            m = re.search(r'height="(\d+)', svg)
            files.append((f"{stem}.svg", svg))
            result.update(svg=f"{stem}.svg", width=w, height=int(m.group(1)) if m else 0, paper=paper)
        names = "、".join(n for n, _ in files)
        return result, f"輸出了 {entry.entry_date} 的「{t.name}」：{names}", files

    def image_prompt(self, *, id=None, date_=None, template=None, style=None):
        entry = self.find(id=id, date_=date_, template=template)
        entry, t = self.entry_and_template(entry)
        style = style or self.book.prefs()["image_style"]
        result = image_prompt(build_document(entry, t), style)
        text = (f"🎨 {STYLES.get(style, STYLES['watercolor'])[0]}\n中文：{result['prompt_zh']}\n\nEnglish: {result['prompt_en']}\n\n"
                f"不要：{result['negative_prompt']}")
        return result, text


# ══════════════════════════════════════════════════════════════════════════
# SYNOX：每個人的日記本、指令
# ══════════════════════════════════════════════════════════════════════════

class _Held:
    def __init__(self, lock):
        self.lock = lock

    def __enter__(self):
        if self.lock is not None:
            self.lock.acquire()

    def __exit__(self, *exc):
        if self.lock is not None:
            self.lock.release()


def _write_own(path: str, data: str) -> None:
    """pack 唯一寫檔的地方：path 一定是 pack.path_of(user, ...) 給的（這個人自己的資料夾底下）。"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(data)


def _read_own(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DiaryError("匯入的檔案要是 UTF-8 的 JSON。") from None


def _cleanup(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _yes(value) -> bool:
    return str(value or "").strip().lower() in ("1", "yes", "y", "true", "on", "是", "要", "好")


def install(app, pack):
    g = pack.group
    content = Content(os.path.join(pack.manifest.folder, "content"))

    def locked(user):
        """這個人的日記本整段持鎖（讀 → 改 → 存主檔與設定兩份；SYNOX 的 PackStore 每人一把 RLock）。"""
        return _Held(getattr(pack.store.of(user), "lock", None))

    def book_of(user, *, seed=True) -> Book:
        return Book(pack.store.of(user), content, seed=seed)

    def run(api, chat_id, user, fn, *, seed=True):
        """拿這個人的日記本 → 做 → 存 → 回一句話。DiaryError 回那一句、什麼都不存。"""
        with locked(user):
            book = book_of(user, seed=seed)
            try:
                out = fn(Diary(book))
            except DiaryError as e:
                api.send_message(chat_id, "❌ " + str(e))
                return None
            book.commit()
        api.send_message(chat_id, out[1])
        return out

    def simple(method, *names, seed=True):
        """把 values 裡這幾個欄位照順序交給 Diary 的那個動作。"""
        def handler(api, chat_id, user, values):
            def go(d):
                fn = method
                return fn(d, *[values.get(n, "") for n in names])
            run(api, chat_id, user, go, seed=seed)
        return handler

    def send_files(api, chat_id, user, files, caption):
        for name, text in files:
            path = pack.path_of(user, name)       # data: user：每個人自己的資料夾
            _write_own(path, text)
            try:
                api.send_document(chat_id, path, caption=caption)
            finally:
                _cleanup(path)

    # ── 寫 ───────────────────────────────────────────────────────
    def jot(api, chat_id, user, values):
        run(api, chat_id, user, lambda d: d.put(date_="today", template="jot", payload_raw=values["text"], merge="fill"))

    def put(api, chat_id, user, values):
        run(api, chat_id, user, lambda d: d.put(
            date_=values["date"], template=values["template"], payload_raw=values["answers"],
            merge=values.get("merge") or "fill", fmt=values.get("format") or "plain"))

    def put_ai(api, chat_id, user, values):
        run(api, chat_id, user, lambda d: d.put(
            date_=values["date"], template=values["template"], payload_raw=values["answers"], merge="fill",
            author="ai"))

    def overwrite(api, chat_id, user, values):
        run(api, chat_id, user, lambda d: d.put(
            date_=values["date"], template=values["template"], payload_raw=values["answers"], merge="fill",
            overwrite=values["fields"], author=values.get("author") or "user"))

    # ── 讀 ───────────────────────────────────────────────────────
    def show(api, chat_id, user, values):
        def go(d):
            day = values.get("date", "")
            if day.strip().lstrip("#").isdigit() and not values.get("id") and len(day.strip().lstrip("#")) < 6:
                return d.show(id=day)            # /diary_show 12 ＝ 看 #12
            return d.show(id=values.get("id"), date_=day or "today", template=values.get("template"))
        run(api, chat_id, user, go)

    def templates(api, chat_id, user, values):
        run(api, chat_id, user, lambda d: d.templates(
            structure=values.get("structure", ""), reflection=values.get("reflection", ""),
            flow=values.get("flow", ""), tag=values.get("tag", "")))

    def template(api, chat_id, user, values):
        run(api, chat_id, user, lambda d: d.templates(tid=values["template"], brief=False))

    def suggest_(api, chat_id, user, values):
        run(api, chat_id, user, lambda d: d.suggest(values["text"], int(values.get("limit") or 5)))

    def list_(api, chat_id, user, values):
        run(api, chat_id, user, lambda d: d.list_(int(values.get("n") or 14)))

    def settings(api, chat_id, user, values):
        run(api, chat_id, user, lambda d: d.settings(values.get("key", "")))

    # ── 輸出 ─────────────────────────────────────────────────────
    def export(api, chat_id, user, values):
        with locked(user):
            book = book_of(user)
            try:
                result, text, files = Diary(book).export(
                    id=values.get("id"), date_=values.get("date"), template=values.get("template"),
                    fmt=values.get("format") or "both", layout=values.get("layout"), theme=values.get("theme"),
                    width=values.get("width"), paper=values.get("paper"), gift_cover=_yes(values.get("gift")),
                    gift_message=values.get("gift_message", ""))
            except DiaryError as e:
                api.send_message(chat_id, "❌ " + str(e))
                return
            book.commit()
        send_files(api, chat_id, user, files, "📔 " + text)
        hint = ("頁面用瀏覽器打開（可以列印成 PDF）；卡片是 SVG（要 PNG：瀏覽器打開後截圖，或用 SYNOX 網頁存成圖片）。"
                if len(files) > 1 or files and files[0][0].endswith(".svg") else "用瀏覽器打開，可以列印成 PDF。")
        api.send_message(chat_id, f"📔 {text}\n{hint}")

    def export_all(api, chat_id, user, values):
        kit.require_human()                     # 整本日記：只給本人，不給 AI、排程
        with locked(user):
            book = book_of(user)
            doc = book.export_all()
            book.commit()
        stamp = now().strftime("%Y%m%d-%H%M%S")
        send_files(api, chat_id, user, [(f"journal-export-{stamp}.json", json.dumps(doc, ensure_ascii=False, indent=2))],
                   "📔 整本日記（日記＋題組＋版型＋設定）")
        api.send_message(chat_id, f"📔 匯出了 {len(doc['entries'])} 篇日記、{len(doc['templates'])} 個題組。"
                                  f"這個檔沒有加密：存到你自己的地方，不要轉給別人。")

    def importer(replace):
        def handler(api, chat_id, user, values):
            kit.require_human()
            path = pack.path_of(user, "import.json")

            def go(d):
                text = _read_own(path)
                if text is None:
                    raise DiaryError("找不到要匯入的檔：把「匯出全部」的 JSON 放在你自己的日記資料夾，檔名叫 import.json，再打一次。"
                                     "（/diary_setup 會說資料夾在哪）")
                try:
                    doc = json.loads(text)
                except ValueError:
                    raise DiaryError("import.json 不是 JSON。") from None
                counts = d.book.import_all(doc, replace=replace)
                return counts, (f"匯入了 {counts['entries']} 篇日記（跳過 {counts['skipped_entries']} 篇已經有的或讀不懂的）、"
                                f"{counts['templates']} 個題組、{counts['layouts']} 個版型。匯入前的樣子備份了一份。")
            if run(api, chat_id, user, go) is not None:
                _cleanup(path)
        return handler

    def setup(api, chat_id, user, values):
        def go(d):
            result, text = d.setup_status()
            folder = pack.path_of(user)
            result["files"] = folder
            return result, text + f"\n匯入用的資料夾：{folder}"
        run(api, chat_id, user, go)

    def tpl_rm(api, chat_id, user, values):
        run(api, chat_id, user, lambda d: d.tpl_rm(values["template"], _yes(values.get("force"))))

    def image_prompt_(api, chat_id, user, values):
        run(api, chat_id, user, lambda d: d.image_prompt(
            id=values.get("id"), date_=values.get("date"), template=values.get("template"), style=values.get("style")))

    date_field = Field("date", "哪一天（YYYY-MM-DD、today、yesterday）", required=True)
    tpl_field = Field("template", "題組 id（/diary_templates 看）", required=True)

    kit.command(g, "jot", jot, "隨手記一則：直接打字，記進今天的「隨手記」（接在之前那幾則後面，不蓋掉）",
                default=True, effect=WRITE, fields=[Field("text", "要記的話", required=True)])
    kit.command(g, "put", put, "把作者自己的話寫進某一天、某個題組（只填空著的題目；直接打字＝寫進第一題）",
                effect=WRITE, positional=3, fields=[
                    date_field, tpl_field,
                    Field("answers", "要記的話（直接打字＝寫進第一題）或 JSON：{\"answers\": {\"題目 id\": 答案}, \"title\": \"\", \"closing\": \"\"}",
                          required=True),
                    Field("merge", "已經有答案的題目：fill＝不動（預設）、fields＝換成新的、append＝接在後面", choices=MERGES),
                    Field("format", "文字是純文字（plain，預設）還是編輯器的 HTML（html）", choices=FORMATS)])
    kit.command(g, "put_ai", put_ai, "AI 替作者整理的版本：標「AI 整理」、附作者原文，不蓋作者寫的",
                effect=WRITE, positional=3, fields=[
                    date_field, tpl_field,
                    Field("answers", "JSON：{\"answers\": {…}, \"source_text\": \"作者原文（必填）\", \"sources\": {\"題目 id\": \"原文\"}}",
                          required=True)])
    kit.command(g, "overwrite", overwrite, "換掉已經寫過的幾題（只動點名的題目）", effect=DANGER, positional=5, fields=[
        date_field, tpl_field,
        Field("fields", "要換掉的題目 id，逗號分開（或 all）", required=True),
        Field("author", "user（作者的話）或 ai（AI 整理，JSON 要附 source_text）", required=True, choices=AUTHORS),
        Field("answers", "JSON：{\"answers\": {\"題目 id\": 新答案}}", required=True)])
    kit.command(g, "show", show, "某一天寫了什麼（每一篇的純文字；給編號＝看那一篇）", positional=2, fields=[
        Field("date", "哪一天（YYYY-MM-DD、today、yesterday；或日記編號）", default="today"),
        Field("template", "只看這個題組的那一篇"), Field("id", "日記編號")])
    kit.command(g, "list", list_, "最近寫過的日記（只有日期、題組、字數、編號）",
                fields=[Field("n", "幾篇（預設 14）", check=kit.positive_int)])
    kit.command(g, "templates", templates, "有哪些題組（名稱、什麼時候用、幾分鐘、分類；可以篩）", positional=0, fields=[
        Field("structure", "結構：one-part、three-part、n-part（逗號分開＝其中之一）"),
        Field("reflection", "反思層級：none、reflection、devotional、prayer（逗號分開＝其中之一）"),
        Field("flow", "走過這些步驟：events、scripture、reflection、prayer…（逗號分開＝都要有）"),
        Field("tag", "標籤（逗號分開＝其中之一）")])
    kit.command(g, "template", template, "一個題組的每一題（題型、選項、填空格數、每一題怎麼填）", fields=[tpl_field])
    kit.command(g, "suggest", suggest_, "替一段話挑題組（照規則、說出為什麼；不呼叫模型）", positional=1, fields=[
        Field("text", "使用者寫的那段話", required=True), Field("limit", "最多幾個（1–20）", check=kit.positive_int)])
    kit.command(g, "categories", simple(Diary.categories),
                "分類：結構、反思層級、步驟、分組、標籤各有哪些題組（照分類挑用）")
    kit.command(g, "layouts", simple(Diary.layouts), "輸出版型有哪些")
    kit.command(g, "themes", simple(Diary.themes), "紙張顏色有哪些")
    kit.command(g, "export", export, "做成一頁 HTML ＋ 一張卡片 SVG，傳給你（照現在存的內容重新畫）",
                positional=2, fields=[
                    Field("date", "哪一天（YYYY-MM-DD、today、yesterday）"), Field("template", "題組 id"),
                    Field("id", "日記編號（給了就不用日期、題組）"),
                    Field("format", "both＝HTML＋卡片（預設）、html、card", choices=EXPORTS),
                    Field("layout", "版型（/diary_layouts）"), Field("theme", "紙張顏色（/diary_themes）"),
                    Field("width", "卡片寬度 480–2400", check=kit.positive_int),
                    Field("paper", "卡片的紙：ruled、grid、plain、dotted", choices=PAPERS),
                    Field("gift", "加上可翻開的封面（yes／no）"), Field("gift_message", "封面上的一句話")])
    kit.command(g, "image_prompt", image_prompt_, "從那一篇做一段畫圖用的 prompt（中英各一段；照原文、不呼叫模型）",
                positional=2, fields=[Field("date", "哪一天"), Field("template", "題組 id"), Field("id", "日記編號"),
                                      Field("style", "畫風：" + "、".join(STYLES), choices=tuple(STYLES))])
    kit.command(g, "title", simple(Diary.set_title, "id", "title"), "替一篇取標題（空的＝清掉）", effect=WRITE,
                positional=2, fields=[Field("id", "日記編號", required=True, check=kit.positive_int), Field("title", "標題")])
    kit.command(g, "switch", simple(Diary.switch, "id", "template"),
                "替一篇換題組（答案一個都不刪；新題組沒有的題目先藏起來，換回去就出現）", effect=WRITE,
                fields=[Field("id", "日記編號", required=True, check=kit.positive_int), tpl_field])
    kit.command(g, "rm", simple(Diary.rm, "id"), "刪掉一篇（進垃圾桶，救得回來）", effect=DANGER,
                fields=[Field("id", "日記編號（/diary_list 看）", required=True, check=kit.positive_int)])
    kit.command(g, "tidy", simple(Diary.tidy), "清掉完全空白的日記（進垃圾桶）", effect=WRITE)
    kit.command(g, "history", simple(Diary.history, "id"), "一篇的歷史版本（存壞了可以放回去）",
                fields=[Field("id", "日記編號", required=True, check=kit.positive_int)])
    kit.command(g, "restore", simple(Diary.restore, "revision"), "把一篇放回某個歷史版本（換掉的那一份也會留一版）",
                effect=WRITE, fields=[Field("revision", "版本號（/diary_history 看）", required=True, check=kit.positive_int)])
    kit.command(g, "trash", simple(Diary.trash), "垃圾桶：刪掉的日記")
    kit.command(g, "undelete", simple(Diary.undelete, "id"), "從垃圾桶救回一篇", effect=WRITE,
                fields=[Field("id", "日記編號（/diary_trash 看）", required=True, check=kit.positive_int)])
    kit.command(g, "purge", simple(Diary.purge, "id"), "從垃圾桶永久刪除一篇（連歷史版本，救不回來）", effect=DANGER,
                fields=[Field("id", "日記編號（/diary_trash 看）", required=True, check=kit.positive_int)])
    kit.command(g, "backups", simple(Diary.backups), "整本日記的備份與狀態（幾篇、幾份歷史、垃圾桶幾篇）")
    kit.command(g, "backup", simple(Diary.backup), "現在備份一份整本日記（手動的不會被自動清掉）", effect=WRITE)
    kit.command(g, "backup_restore", simple(Diary.backup_restore, "name"), "整本日記還原成某一份備份（還原前先備份一份）",
                effect=DANGER, fields=[Field("name", "備份名稱（/diary_backups 看）", required=True)])
    kit.command(g, "backup_rm", simple(Diary.backup_rm, "name"), "刪掉一份備份", effect=DANGER,
                fields=[Field("name", "備份名稱（/diary_backups 看）", required=True)])
    kit.command(g, "export_all", export_all, "匯出整本日記（日記＋題組＋版型＋設定）成一個 JSON 檔，傳給你", ai=False)
    kit.command(g, "import", importer(False), "匯入「匯出全部」的 JSON（合併：只會增加，不會蓋掉）", effect=WRITE, ai=False)
    kit.command(g, "import_replace", importer(True), "匯入並取代：現有的日記先進垃圾桶，再放進匯入的", effect=DANGER, ai=False)
    kit.command(g, "tpl_save", simple(Diary.tpl_save, "json"), "新增或修改一個題組（給整個題組的 JSON）", effect=WRITE,
                fields=[Field("json", "題組 JSON（格式見 /diary_template 與 README）", required=True)])
    kit.command(g, "tpl_rm", tpl_rm, "刪掉一個題組（還有日記在用要加 force=yes；日記不會不見）", effect=DANGER,
                fields=[tpl_field, Field("force", "還有日記在用也刪（yes）")])
    kit.command(g, "tpl_restore", simple(Diary.tpl_restore, "template"),
                "把一個內建題組放回來（或還原成內建的版本；預設沒出現的也從這裡拿）", effect=WRITE, fields=[tpl_field])
    kit.command(g, "layout_rm", simple(Diary.layout_rm, "layout"), "刪掉一個版型", effect=DANGER,
                fields=[Field("layout", "版型 id", required=True)])
    kit.command(g, "layout_restore", simple(Diary.layout_restore, "layout"), "把一個內建版型放回來", effect=WRITE,
                fields=[Field("layout", "版型 id", required=True)])
    kit.command(g, "settings", settings, "每一個設定：現在的值、預設、是什麼、怎麼寫",
                fields=[Field("key", "只看這一個設定")])
    kit.command(g, "settings_set", simple(Diary.settings_set, "key", "value"),
                "改一個設定（預設題組、版型、幾點換日、顏色…；default＝放回內建）", effect=WRITE, fields=[
                    Field("key", "設定名（/diary_settings 看全部）", required=True),
                    Field("value", "新的值（default＝放回內建）", required=True)])
    kit.command(g, "setup", setup, "這份日記的狀態：起始組合、幾個題組、幾篇、還沒拿出來的內建題組")
    kit.command(g, "setup_init", simple(Diary.setup_init, "preset", seed=False),
                "挑起始題組：generic＝不談信仰的幾個、devotional＝靈修（只加不刪）", effect=WRITE,
                fields=[Field("preset", "generic 或 devotional", required=True, choices=PRESETS)])
