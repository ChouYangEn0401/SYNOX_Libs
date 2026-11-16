"""重新產生 tests/fixtures/diary_parity.json：用**原本的**每日日記程式跑同一批假日記，記下它的答案。

packs/diary 是原專案 Python 後端的移植（輸出、版型、卡片、挑題組、寫入的合併規則），移植只有在跟原本一致時才算數，
所以 tests/test_diary_parity.py 拿這份 fixture 逐字比對。這支只給維護者用，要原專案的 checkout 和它自己的 venv（有 pydantic、jinja2）：

    <原專案>\\.venv\\Scripts\\python.exe scripts\\dump_diary_parity.py <原專案資料夾>

原專案的資料一律不碰：JOURNAL_DATA_DIR 指到一個暫存資料夾，跑完就刪。輸入全是編出來的。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, datetime

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "diary_parity.json")
DAY = date(2026, 11, 2)
STAMP = datetime(2026, 11, 2, 21, 0, 0)

TEXT = "第一行今天很平安。<br>第<b>二</b>行  兩個空白 <span style=\"color:#c8322c\">紅字</span>，還有 English words & <i>斜體</i>。"
LONG = ("這是一段比較長的文字，用來看卡片的斷行：「括號」不能落在行尾，句號。不能在行首！" * 3).strip()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def auto_answers(t) -> dict:
    """每一題給一個看得出題型的假答案。"""
    out = {}
    for s in t.sections:
        for f in s.fields:
            k = f.kind.value
            if k == "choice":
                out[f.id] = f.options[0].id
            elif k == "scale":
                out[f.id] = f.options[-1].id
            elif k == "multi_choice":
                out[f.id] = [o.id for o in f.options[:2]]
            elif k == "short_text":
                out[f.id] = "短短一句 & <不是標籤>"
            elif k == "fill":
                out[f.id] = [f"空格{i + 1}" for i in range(max((f.pattern or "").count("{}"), 1))]
            elif k == "list":
                out[f.id] = ["第一則", "第二則  有兩個空白", LONG]
            else:
                out[f.id] = TEXT
    return out


def main(argv) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    root = os.path.abspath(argv[0])
    tmp = tempfile.mkdtemp(prefix="diary-parity-")
    os.environ["JOURNAL_DATA_DIR"] = os.path.join(tmp, "data")
    sys.path.insert(0, root)
    try:
        from backend import cli
        from backend.models import Entry
        from backend.render.artifact_html import render_document
        from backend.render.card_svg import render_document_svg
        from backend.render.document import build_document
        from backend.seed_manager import install_seeds
        from backend.services import recovery_template
        from backend.taxonomy import categories, load_rules, suggest
        from backend.template_registry import all_templates

        install_seeds()
        templates = all_templates()
        fixture = {"_note": "scripts/dump_diary_parity.py 產生的，不要手改：原本每日日記的 Python 後端的輸出，packs/diary 要逐字一樣。",
                   "templates": [t.id for t in templates], "renders": [], "full": {}, "suggest": [], "cli": []}

        def entry(tid, answers, **kw):
            return Entry(id=kw.pop("id", 7), entry_date=kw.pop("entry_date", DAY), template_id=tid, answers=answers,
                         created_at=STAMP, updated_at=STAMP, **kw)

        def record(name, e, t, *, layouts=None, svgs=(("ruled", 1080),), gifts=((False, ""),), themes=None, full=False):
            doc_e = e
            out = {"name": name, "entry": json.loads(e.model_dump_json()), "template": t.id if t else None}
            t = t or recovery_template(e)
            doc = build_document(doc_e, t)
            out["text"] = doc.to_plain_text()
            out["word_count"] = doc.word_count
            out["html"] = {}
            for layout in layouts or [e.layout]:
                for theme in themes or [e.theme]:
                    for gift, msg in gifts:
                        d = build_document(doc_e.model_copy(update={"layout": layout, "theme": theme}), t)
                        page = render_document(d, gift_cover=gift, gift_message=msg)
                        key = f"{layout}|{theme}|{int(gift)}|{msg}"
                        out["html"][key] = sha(page)
                        if full:
                            fixture["full"][f"{name}|{key}"] = page
            out["svg"] = {}
            for paper, width in svgs:
                svg = render_document_svg(doc, width=width, paper=paper)
                out["svg"][f"{paper}|{width}"] = sha(svg)
                if full:
                    fixture["full"][f"{name}|svg|{paper}|{width}"] = svg
            fixture["renders"].append(out)

        by_id = {t.id: t for t in templates}
        # 每個內建題組：每一題都填、用它預設的版型與紙張顏色
        for t in templates:
            e = entry(t.id, auto_answers(t), title="測試標題" if t.order % 2 else "", closing="親愛的主，<b>謝謝</b>。",
                      layout=t.default_layout, theme=t.default_theme)
            record(f"tpl:{t.id}", e, t, svgs=(("ruled", 1080), ("dotted", 720)))
        # 一個題組：四種版型 × 五種紙張顏色、封面、卡片的每一種紙與寬度、整頁原文
        t = by_id["day-review"]
        e = entry(t.id, auto_answers(t), title="一個有 <角括號> & \"引號\" 的標題", closing=TEXT,
                  field_colors={next(iter(t.field_map())): "#2456b8"}, layout="letter")
        record("matrix", e, t, layouts=["letter", "ledger", "manuscript", "postcard"],
               themes=["letterpress", "notebook", "linen", "dawn", "midnight"],
               svgs=[(p, w) for p in ("ruled", "grid", "plain", "dotted") for w in (480, 1080, 2400)],
               gifts=((False, ""), (True, ""), (True, "給你 <的> 一句話")), full=False)
        record("full", e, t, layouts=["letter", "ledger", "manuscript", "postcard"], gifts=((True, "送你"),), full=True)
        # 只有勾選題（ledger 的左欄）、什麼都沒寫、AI 整理、題組被刪掉
        t = by_id["feelings"]
        ticks = {f.id: v for f, v in ((by_id["feelings"].field_map()[k], v) for k, v in auto_answers(t).items())
                 if f.kind.value in ("choice", "scale", "multi_choice")}
        record("ticks-only", entry(t.id, ticks, layout="ledger"), t, layouts=["ledger", "letter"])
        record("empty", entry("three-lines", {}, layout="postcard"), by_id["three-lines"],
               layouts=["letter", "ledger", "manuscript", "postcard"], svgs=(("ruled", 1080), ("grid", 1080)))
        t = by_id["capsule"]
        a = auto_answers(t)
        first = list(t.field_map())[:2]
        prov = {"fields": {first[0]: {"author": "ai", "at": "2026-11-02T21:00:00", "source": "作者的原話 A"},
                           first[1]: {"author": "ai", "at": "2026-11-02T21:00:00"},
                           "closing": {"author": "ai", "at": "2026-11-02T21:00:00"}},
                "source_text": "整篇的原文\n第二行"}
        record("ai", entry(t.id, a, closing="AI 整理的禱告", provenance=prov), t,
               layouts=["letter", "ledger"], full=True)
        record("recovered", entry("gone-template", {"x": "留下來的字", "y": ["一", "二"]}), None,
               layouts=["letter", "manuscript"])

        # 挑題組：同一份規則、同一批題組
        rules = load_rules()
        fixture["rules_sha"] = sha(json.dumps(rules, ensure_ascii=False, sort_keys=True))
        for text, faith in (("今天跟客戶開會被罵，很擔心案子", "auto"), ("今天很累但很感謝", "auto"),
                            ("讀經讀到詩篇二十三篇，禱告的時候很平安", "auto"), ("很難過，想哭", "never"),
                            ("今天很累但很感謝主", "prefer"), ("嗯", "auto"), (LONG * 5, "auto"),
                            ("我想成為一個更有耐心的人，今天學到要先聽", "auto")):
            fixture["suggest"].append({"text": text, "faith": faith,
                                       "result": suggest(text, templates, faith=faith, default_id="three-lines",
                                                         limit=5, rules=rules)})
        fixture["categories"] = categories(templates)

        # 命令列的寫入：一連串 entry put，記下每一次的結果
        def run(args, stdin=b""):
            old = sys.stdin
            sys.stdin = io.TextIOWrapper(io.BytesIO(stdin), encoding="utf-8")
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    cli.main(args + ["--json"])
            finally:
                sys.stdin = old
            return json.loads(buf.getvalue().strip().splitlines()[-1])

        def put(tid, payload, **flags):
            args = ["entry", "put", "--date=2026-11-02", f"--template={tid}"] + [f"--{k.replace('_', '-')}={v}"
                                                                                 for k, v in flags.items()]
            data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            env = run(args, data.encode("utf-8"))
            fixture["cli"].append({"template": tid, "payload": data, "flags": flags, "envelope": env})

        tl = by_id["three-lines"]
        ids = list(tl.field_map())
        f0, f1 = tl.field_map()[ids[0]], tl.field_map()[ids[1]]
        put("jot", "今天很好", merge="fill")
        put("jot", "晚上下雨\n第二行", merge="fill")
        put("three-lines", {"answers": {ids[0]: "第一題", ids[1]: "第二題", "nope": "x"}, "title": "三行"}, merge="fill")
        put("three-lines", {"answers": {ids[0]: "換掉嗎", ids[1]: "不要"}, "title": "新標題"}, merge="fill")
        put("three-lines", {"answers": {ids[0]: "換掉了"}}, merge="fields")
        put("three-lines", {"answers": {ids[1]: "接上去"}}, merge="append")
        put("three-lines", {"answers": {ids[1]: "點名換"}, "closing": "結尾"}, merge="fill", overwrite=ids[1])
        put("capsule", {"answers": {list(by_id["capsule"].field_map())[0]: "AI 的版本"}}, merge="fill", author="ai")
        cap = list(by_id["capsule"].field_map())
        put("capsule", {"answers": {cap[0]: "AI 的版本", cap[1]: "第二題 AI"}, "source_text": "作者原文",
                        "sources": {cap[0]: "這一題的原文"}, "closing": "AI 的結尾"}, merge="fill", author="ai")
        put("capsule", {"answers": {cap[0]: "作者自己寫的"}}, merge="fill", overwrite=cap[0])
        put("capsule", {"answers": {cap[0]: "AI 想蓋掉", cap[2]: "AI 新的"}, "source_text": "又一段原文"},
            merge="fields", author="ai")
        put("capsule", {"answers": {cap[0]: "x"}, "source_text": "y"}, merge="append", author="ai")
        put("capsule", "純文字給 AI", merge="fill", author="ai")
        ch = next((t, f) for t in templates for f in t.field_map().values() if f.kind.value == "choice")
        mc = next((t, f) for t in templates for f in t.field_map().values() if f.kind.value == "multi_choice")
        fl = next((t, f) for t in templates for f in t.field_map().values() if f.kind.value == "fill")
        put(ch[0].id, {"answers": {ch[1].id: ch[1].options[-1].label}}, merge="fill")
        put(mc[0].id, {"answers": {mc[1].id: f"{mc[1].options[0].label}，不是選項,{mc[1].options[1].id}"}}, merge="fill")
        put(fl[0].id, {"answers": {fl[1].id: "只有一格"}}, merge="fill")
        put("three-lines", {"answers": {ids[0]: ["清單", "給單行"]}}, merge="fields", format="plain")
        put("jot", {"answers": {"notes": "一\n二\n\n三"}, "closing": "<b>粗</b>"}, merge="append", format="html")
        put("jot", "", merge="fill")
        put("jot", "[1, 2]", merge="fill")
        put("jot", "{\"answers\": []}", merge="fill")
        put("nope", "x", merge="fill")
        put("three-lines", {"answers": {}}, merge="fill", overwrite="ghost")
        fixture["show"] = run(["entry", "show", "--date=2026-11-02"])
        fixture["list"] = run(["entry", "list"])
        _ = f0, f1
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(fixture, ensure_ascii=False, indent=1) + "\n")
        print(OUT, len(fixture["renders"]), "renders,", len(fixture["cli"]), "writes")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
