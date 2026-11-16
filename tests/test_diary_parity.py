# -*- coding: utf-8 -*-
"""packs/diary 跟原本的每日日記程式逐字一樣：純文字、四種版型的 HTML、卡片 SVG、挑題組、
命令列寫入的每一種合併情況。期望值是 scripts/dump_diary_parity.py 從原本的 Python 後端錄下來的（tests/fixtures/diary_parity.json）。"""

import hashlib
import json
import os
from datetime import datetime

import pytest

from conftest import PACKS, ROOT, load_module

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "diary_parity.json")
with open(FIXTURE, encoding="utf-8") as _f:
    FX = json.load(_f)

M = load_module("diary")
CONTENT = M.Content(os.path.join(PACKS, "diary", "content"))
ORIGINAL = FX["templates"]


class MemStore:
    def __init__(self):
        self.data = {}

    def load(self, default=None, key=None):
        raw = self.data.get(key)
        return default if raw is None else json.loads(raw)

    def save(self, data, key=None):
        self.data[key] = json.dumps(data, ensure_ascii=False)


def book():
    b = M.Book(MemStore(), CONTENT)
    # 只看原本那 21 個題組（之後加的新題組不影響比對）
    b.setup["hidden_templates"] = [t for t in CONTENT.templates() if t not in ORIGINAL]
    return b


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def entry_of(raw):
    return M.Entry.from_row({**raw, "created_at": raw["created_at"], "updated_at": raw["updated_at"]})


def test_original_templates_are_shipped_unchanged():
    assert set(ORIGINAL) <= set(CONTENT.templates())


@pytest.mark.parametrize("case", FX["renders"], ids=[c["name"] for c in FX["renders"]])
def test_renders_match_the_original(case):
    b = book()
    e = entry_of(case["entry"])
    t = b.get_template(case["template"]) if case["template"] else M.recovery_template(e)
    doc = M.build_document(e, t)
    assert doc.to_plain_text() == case["text"]
    assert doc.word_count == case["word_count"]
    for key, want in case["html"].items():
        layout, theme, gift, msg = key.split("|", 3)
        d = M.build_document(e.copy(layout=layout, theme=theme), t)
        page = b.render_html(d, gift_cover=gift == "1", gift_message=msg)
        full = FX["full"].get(f"{case['name']}|{key}")
        if full is not None:
            assert page == full
        assert sha(page) == want, key
    for key, want in case["svg"].items():
        paper, width = key.split("|")
        svg = M.render_document_svg(doc, width=int(width), paper=paper)
        full = FX["full"].get(f"{case['name']}|svg|{paper}|{width}")
        if full is not None:
            assert svg == full
        assert sha(svg) == want, key


def test_suggest_and_categories_match_the_original():
    b = book()
    rules = CONTENT.json("suggest.json", {})
    assert sha(json.dumps(rules, ensure_ascii=False, sort_keys=True)) == FX["rules_sha"]
    templates = b.all_templates()
    assert [t.id for t in templates] == ORIGINAL
    for case in FX["suggest"]:
        got = M.suggest(case["text"], templates, rules=rules, faith=case["faith"], default_id="three-lines", limit=5)
        assert got == case["result"], case["text"]
    assert M.categories(templates) == FX["categories"]


# 原本的訊息裡提到命令列的旗標；SYNOX 版改成指令的說法（其他一字不差）
WORDING = {"（要換要 --overwrite 點名）": "（要換要點名 overwrite）",
           "要換掉用 overwrite 點名": "要換掉用 /diary_overwrite 點名"}


def _norm(text):
    for a, b in WORDING.items():
        text = text.replace(a, b)
    return text


def test_writes_match_the_original_command_line():
    """同一串 entry put（純文字、fill／fields／append、點名換、AI 整理、略過的題目）：每一次的結果與最後存的內容都一樣。"""
    b = book()
    M.now = lambda: datetime(2026, 11, 2, 21, 0)
    d = M.Diary(b)
    for step in FX["cli"]:
        env, flags = step["envelope"], step["flags"]
        try:
            result, text = d.put(date_="2026-11-02", template=step["template"], payload_raw=step["payload"],
                                 merge=flags.get("merge", "fields"), fmt=flags.get("format", "plain"),
                                 overwrite=flags.get("overwrite", ""), author=flags.get("author", "user"))
        except M.DiaryError as e:
            assert not env["ok"], (step, str(e))
            continue
        assert env["ok"], (step, text)
        want = env["result"]
        for key in ("created", "author", "filled", "kept", "protected", "overwritten", "ignored", "plain_text_field"):
            assert result.get(key) == want.get(key), (key, step)
        assert text == _norm(want["message"]).replace(f"#{want['entry']['id']}", f"#{result['entry']['id']}")
        for key in ("date", "template", "template_name", "title", "layout", "theme", "chars", "ai_fields"):
            assert result["entry"][key] == want["entry"][key], (key, step)
    _, shown = d.show(date_="2026-11-02")
    got = {e["template"]: e for e in d.show(date_="2026-11-02")[0]["entries"]}
    for e in FX["show"]["result"]["entries"]:
        mine = got[e["template"]]
        assert mine["text"] == e["text"]
        assert mine["answers"] == e["answers"]
        assert mine["closing"] == e["closing"]
        strip = lambda p: {**p, "fields": {k: {kk: vv for kk, vv in v.items() if kk != "at"}  # noqa: E731
                                           for k, v in (p.get("fields") or {}).items()}}
        assert strip(mine["provenance"]) == strip(e["provenance"])
    assert len(got) == FX["show"]["result"]["count"]
    assert [(i["date"], i["template"], i["chars"]) for i in d.list_(30)[0]["entries"]] == \
        [(i["date"], i["template"], i["chars"]) for i in FX["list"]["result"]["entries"]]
