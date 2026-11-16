"""給 AI 的手冊（SKILL.md＋skills/*.md）：每個範例指令都真的存在、欄位名對得上；JSON 範例真的寫得進去（試算）；
每份主題檔 5,000 字內；pack.json 的 agent 區塊指到的檔都在；範例全是假資料。"""

import importlib
import json
import os
import re
from datetime import datetime

import pytest

from synox.agent import skills as synox_skills

from .conftest import MONEY, OWNER, load_money, read_manifest

TOPIC_LIMIT = 5000
TOOL_RE = re.compile(r"`(money__|/money_)([a-z][a-z0-9_]*)([^`]*)`")
FIELD_RE = re.compile(r"(?:^|\s)([a-z_][a-z0-9_]*)=")
JSON_RE = re.compile(r"```json\n(.*?)```", re.S)
WEB = "http" + "://"                                     # 拼出來的：這個檔本身才不會被去識別化檢查（test_deid）抓到
OLD_NAME = "money" + "book"                              # 來源程式的名字（拼出來的，同上）
FORBIDDEN = (OLD_NAME, "ledger.db", "C:\\", WEB, WEB.replace("http", "https"), "--json", "--dry-run", "plan_apply", "plan apply")


def topics():
    return read_manifest().raw["agent"]["skill"]["topics"]


def doc_files():
    files = {"SKILL.md": os.path.join(MONEY, "SKILL.md")}
    files.update({rel: os.path.join(MONEY, *rel.split("/")) for rel in topics().values()})
    return files


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def commands():
    module = load_money()
    return {c.name: c for c in module.table.collect(module.DOMAINS).commands}


def test_skill_md_has_frontmatter_and_points_to_every_topic():
    skill = synox_skills.load(os.path.join(MONEY, "SKILL.md"))
    assert skill is not None and skill.name == "money" and len(skill.description) <= 120
    body = skill.body()
    for rel in topics().values():
        assert rel in body, rel
    assert read_manifest().raw["agent"]["skill"]["file"] == "SKILL.md"


@pytest.mark.parametrize("rel", sorted(set(doc_files())))
def test_each_file_exists_is_short_and_clean(rel):
    text = read(doc_files()[rel])
    assert text.strip() and len(text) <= TOPIC_LIMIT, (rel, len(text))
    for word in FORBIDDEN:
        assert word not in text, (rel, word)
    if rel != "SKILL.md":
        assert text.startswith("# ")


def test_every_tool_and_field_in_the_docs_exists():
    cmds = commands()
    seen = set()
    for rel, path in doc_files().items():
        for prefix, name, rest in TOOL_RE.findall(read(path)):
            assert name in cmds, f"{rel}: {prefix}{name} 不存在"
            cmd = cmds[name]
            if prefix == "money__":
                assert cmd.ai, f"{rel}: money__{name} 不是給 AI 的工具（請改寫成給人打的 /money_{name}）"
            fields = {f.name for f in cmd.fields}
            for field in FIELD_RE.findall(rest):
                assert field in fields, f"{rel}: {prefix}{name} 沒有欄位 {field}"
            seen.add(name)
    ai = {n for n, c in cmds.items() if c.ai}
    assert ai <= seen, f"手冊沒提到這些給 AI 的工具：{sorted(ai - seen)}"


def json_examples():
    out = []
    for rel, path in doc_files().items():
        for i, block in enumerate(JSON_RE.findall(read(path))):
            out.append((f"{rel}#{i + 1}", block))
    return out


@pytest.mark.parametrize("where, block", json_examples(), ids=[w for w, _ in json_examples()])
def test_json_examples_parse(where, block):
    assert isinstance(json.loads(block), dict), where


@pytest.fixture
def bot(make_money):
    b = make_money(now=datetime(2030, 1, 31, 12, 0))
    services = f"{b.module.__name__}.engine.services"
    refdata = importlib.import_module(f"{services}.refdata")
    db = importlib.import_module(f"{b.module.__name__}.engine.db")
    with b.module.session.open_book(b.pack, OWNER) as book:
        with db.write(book.conn) as w:
            refdata.add_account(w, name="範例卡", kind="credit_card", card_closing_day=10, card_due_day=25)
    return b


def test_record_examples_really_go_in(bot):
    """每一段 {"records": …} 範例：照手冊送給 money__ingest（試算），帳本要收得下。"""
    ingest = next(c for c in bot.module.review.COMMANDS if c.name == "ingest")
    tried = 0
    for where, block in json_examples():
        data = json.loads(block)
        if "records" not in data:
            continue
        reply = bot.command("ingest", OWNER, json.dumps(data, ensure_ascii=False) + " dry_run=yes")
        assert reply.startswith("試算"), (where, reply)
        assert len(json.dumps(data, ensure_ascii=False)) <= 2000, where
        tried += 1
    assert tried >= 8 and ingest.ai


def test_review_examples_really_save(bot):
    blocks = {where: json.loads(block) for where, block in json_examples() if where.startswith("skills/review.md")}
    content = next(b for b in blocks.values() if "grade" in b)
    suggestion = next(b for b in blocks.values() if "buckets" in b)
    bot.command("ingest", OWNER, json.dumps({"records": [
        {"type": "income", "date": "2030-01-05", "amount": 45000, "desc": "薪水", "category": "薪資"}]}, ensure_ascii=False))
    out = bot.act("review_save", OWNER, {"kind": "month", "key": "2030-01",
                                         "content": json.dumps({**content, "suggestion": suggestion}, ensure_ascii=False)})
    assert out["review"]["grade"] == content["grade"] and out["review"]["suggestion"]["valid"], out["warnings"]


def test_command_examples_parse_with_the_real_fields(bot):
    """`/money_xxx …` 的範例（給人打的）：照 SYNOX 的參數規則解析得動（不缺必填、沒有不認得的欄位）。"""
    from synox.packs import kit
    cmds = commands()
    for rel, path in doc_files().items():
        for prefix, name, rest in TOOL_RE.findall(read(path)):
            if prefix != "/money_" or "<" in rest or not rest.strip():
                continue
            cmd = cmds[name]
            values, missing, errors = kit.parse_args(cmd.fields, rest.split(), load_money().table.positional_of(cmd))
            assert not errors and not missing, (rel, name, rest, errors, [f.name for f in missing])
