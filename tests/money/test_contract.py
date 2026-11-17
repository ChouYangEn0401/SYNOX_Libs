"""記帳工具 0.4.0 的契約（packs/money/CONTRACT.md）：名字、掛載、危險程度、對照表、畫面的規矩、隔離的入口。

這一份不測任何功能（W0 的每個 run 都是空殼）；功能由各工作包自己的測試測。
"""

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from synox.packs import kit, manifest as synox_manifest
from synox.packs import scan as synox_scan

from .conftest import MONEY, OTHER, OWNER, TODAY, U, load_money, read_manifest

APP = os.path.join(MONEY, "app")
NODE = shutil.which("node")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SVG_NS = "http://www.w3.org/2000/svg"                  # SVG 的命名空間字串（不會連線；SYNOX 的畫面掃描也放行）
OLD_COMMANDS = ("add", "in", "transfer", "list", "undo", "rm", "edit", "month", "budget", "cap", "cats", "cat_add",
                "cat_rm", "template")


def app_decl():
    return read_manifest().web["app"]


def tables():
    module = load_money()
    return module, module.table.collect(module.DOMAINS)


def commands():
    return {c.name: c for c in tables()[1].commands}


def node_json(script):
    """用 node 跑一小段 ES module（不開瀏覽器、不開埠），回它印出來的 JSON。"""
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def file_url(path):
    return "file:///" + os.path.abspath(path).replace("\\", "/")


# ── pack.json ────────────────────────────────────────────────────────────────

def test_manifest_is_accepted_by_strict_install_check():
    m = read_manifest()
    assert m.version == "0.5.0-alpha.1" and m.stage == "alpha" and m.kind == "python" and m.data == "user"
    assert m.capabilities == ["filesystem:own"]
    assert m.python == {"module": "pack", "package": True}
    app = m.web["app"]
    assert app["remote"] is True and app["kit"] == 1
    assert len(app["actions"]) <= synox_manifest.APP_MAX_NAMES and len(app["reads"]) <= synox_manifest.APP_MAX_NAMES
    assert {n for n, e in app["actions"].items() if e == "danger"} == {"rm", "revert", "book_new"}


def test_summary_tells_the_truth_about_encryption():
    """決定：帳本第一版不加密——說明文字照實寫，不再說「加密」。"""
    m = read_manifest()
    assert "沒有加密" in m.summary
    assert "加密" not in m.summary.replace("沒有加密", "")


def test_static_scan_only_needs_own_files():
    report = synox_scan.scan(read_manifest())
    kinds = {f.kind for f in report.findings}
    assert kinds <= {"filesystem:own", "app_note"}, report.findings
    assert not [f for f in report.findings if f.kind in ("app", "exec", "network", "subprocess", "env")]


def test_callers_only_tighten_declared_commands():
    m = read_manifest()
    cmds = commands()
    callers = m.raw["callers"]
    assert callers, "畫面專用的設定類指令要寫 callers"
    for name, who in callers.items():
        assert name in cmds, name
        assert set(who) <= {"human", "remote"}, name
        if cmds[name].effect.upper() == "DANGER":
            assert who == ["human"], name
        assert not cmds[name].ai, f"{name} 只收人，不該給 AI"


def test_agent_block_points_at_skill_topics():
    agent = read_manifest().raw["agent"]
    assert agent["skill"]["file"] == "SKILL.md"
    for key, rel in agent["skill"]["topics"].items():
        assert NAME_RE.match(key) and re.fullmatch(r"skills/[a-z_]+\.md", rel)
    assert len(agent["triggers"]) <= 12 and all(len(t) <= 12 for t in agent["triggers"])
    assert not synox_manifest.scenario_problem(agent["index"]) and len(agent["index"]) <= 60


# ── 指令表 ───────────────────────────────────────────────────────────────────

def test_tables_follow_naming_and_limits():
    module, t = tables()
    assert [d.DOMAIN for d in module.DOMAINS] == list(module.table.DOMAINS)
    names = [c.name for c in t.commands]
    assert len(names) == len(set(names))
    for c in t.commands:
        assert NAME_RE.match(c.name) and c.effect.upper() in ("READ", "WRITE", "DANGER"), c.name
        assert c.summary.strip() and "\n" not in c.summary and len(c.summary) <= 200, c.name
        assert len(c.fields) <= 20, c.name
        fields = [f.name for f in c.fields]
        assert len(fields) == len(set(fields)) and all(NAME_RE.match(f) for f in fields), c.name
        assert c.ai is False or (isinstance(c.ai, dict) and set(c.ai) == {"scenario"}), c.name
    for name in list(t.reads) + list(t.exports) + list(t.web_routes):
        assert NAME_RE.match(name), name
    assert [c.name for c in t.commands if c.default] == ["add"]


def test_ai_tools_are_few_and_each_has_a_scenario():
    ai = [c for c in commands().values() if c.ai]
    assert 0 < len(ai) <= 15
    for c in ai:
        assert c.ai["scenario"] and not synox_manifest.scenario_problem(c.ai["scenario"]), c.name


def test_old_command_names_still_exist(make_money):
    bot = make_money()
    for name in OLD_COMMANDS:
        assert bot.node(name) is not None, name
    assert bot.node("add").default
    assert "summary" in bot.pack.web_routes


# ── 掛載：每個宣告過的名字都有函式、動作＝同名指令＋同樣的危險程度 ──────────────────────

def test_every_declared_read_action_export_is_hooked(make_money):
    bot = make_money()
    decl = app_decl()
    assert set(bot.pack.app_reads) == set(decl["reads"])
    assert set(bot.pack.app_actions) == set(decl["actions"])
    assert set(bot.pack.app_exports) == set(decl["exports"])
    assert set(bot.pack.web_routes) == {"summary"}


def test_every_action_has_a_same_named_command_with_the_same_effect(make_money):
    bot = make_money()
    for name, effect in app_decl()["actions"].items():
        node = bot.node(name)
        assert node is not None and not node.is_group, name
        assert str(node.effect).lower() == effect, name
        assert len(node.fields) <= 20


def test_commands_not_in_the_app_are_not_actions():
    decl = app_decl()
    for c in commands().values():
        assert c.app == (c.name in decl["actions"]), c.name


# ── 空殼：每一條路都走得到、回「還在做」──────────────────────────────────────────────

def still_todo():
    """還是空殼的名字（各工作包做完的就不在這裡）。"""
    _module, t = tables()
    return {"commands": {c.name for c in t.commands if hasattr(c.run, "todo") and not any(f.required for f in c.fields)},
            "reads": {n for n, fn in t.reads.items() if hasattr(fn, "todo")},
            "actions": {c.name for c in t.commands if c.app and hasattr(c.run, "todo")},
            "exports": {n for n, fn in t.exports.items() if hasattr(fn, "todo")}}


def test_shell_command_replies_todo(make_money):
    todo = sorted(still_todo()["commands"])
    if not todo:
        pytest.skip("每個指令都做好了")
    bot = make_money()
    reply = bot.command(todo[0], OWNER, "")
    assert reply.startswith("❌") and "還在做" in reply


@pytest.mark.parametrize("kind", ["reads", "actions", "exports"])
def test_shell_app_calls_raise_todo(make_money, kind):
    bot = make_money()
    hooks = {"reads": bot.pack.app_reads, "actions": bot.pack.app_actions, "exports": bot.pack.app_exports}[kind]
    todo = still_todo()[kind]
    for name, fn in hooks.items():
        if name not in todo:
            continue
        with pytest.raises(kit.AppError) as no:
            fn(U(OWNER), {})
        assert "還在做" in no.value.message, name


def test_engine_errors_become_plain_words():
    module = load_money()

    class Fake(Exception):
        code, message, hint, suggestions = "not_found", "找不到帳戶「範例銀行」", "用 /money_ref 看帳戶清單", ["現金"]

    assert module.table.engine_message(Fake()) == "找不到帳戶「範例銀行」　是不是：現金　用 /money_ref 看帳戶清單"
    assert module.table.as_app_error(ValueError("x")) is None              # 引擎以外的錯不轉（訊息不外流）
    same = kit.AppError("不行")
    assert module.table.as_app_error(same) is same


def test_reply_and_action_shapes():
    table = load_money().table
    r = table.Result(data={"batch_id": "b_1"}, text="💰 記了", warnings=["生活費快用完了"])
    assert table.reply_text(r) == "💰 記了\n⚠️ 生活費快用完了"
    assert table.action_data(r) == {"batch_id": "b_1", "warnings": ["生活費快用完了"]}
    assert table.action_data(table.Result()) == {}


# ── 隔離的入口：open_book ─────────────────────────────────────────────────────────

def test_open_book_gives_each_person_their_own_folder(make_money):
    bot = make_money(per_user=True)
    session = bot.module.session
    seen = {}
    for uid in (OWNER, OTHER):
        with session.open_book(bot.pack, U(uid)) as book:
            assert book.user_id == uid and book.today == TODAY.date() and book.actor.startswith("synox:")
            seen[uid] = book.path("x.csv")
    assert seen[OWNER] != seen[OTHER]
    for uid, path in seen.items():
        parts = os.path.normpath(path).split(os.sep)
        assert str(uid) in parts and "money" in parts and "files" in parts
        assert os.path.isdir(os.path.join(os.path.dirname(path), session.BOOK_DIR))


def test_open_book_refuses_without_an_integer_user(make_money):
    bot = make_money()
    session = bot.module.session
    for bad in (U(None), U("100"), U(True), object()):
        with pytest.raises(session.NoUser):
            with session.open_book(bot.pack, bad):
                pass


def test_open_book_needs_attach_and_paths_stay_inside(make_money):
    bot = make_money()
    session = bot.module.session

    class Bare:
        pass

    with pytest.raises(RuntimeError):
        with session.open_book(Bare(), U(OWNER)):
            pass
    with session.open_book(bot.pack, U(OWNER)) as book:
        with pytest.raises(Exception):
            book.path("..", "..", "別人的.csv")


def test_one_write_lock_per_person(make_money):
    bot = make_money()
    locks = bot.pack.money_locks
    assert locks.of(OWNER) is locks.of(OWNER)
    assert locks.of(OWNER) is not locks.of(OTHER)


def test_static_isolation_rules():
    """隔離規則 3、4：engine/、bridge/ 裡沒有寫環境變數、模組層級的可變表、裸用 pack.store、pack.path()。"""
    bad = [re.compile(p) for p in (r"os\.environ\[", r"\bputenv\b", r"lru_cache", r"^[A-Za-z_][A-Za-z0-9_]*\s*=\s*(\{\}|\[\])",
                                   r"pack\.store\.(?!of\()", r"pack\.path\(")]
    hits = []
    for sub in ("engine", "bridge"):
        for root, _dirs, files in os.walk(os.path.join(MONEY, sub)):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as f:
                    for no, line in enumerate(f, 1):
                        if any(p.search(line) for p in bad):
                            hits.append(f"{os.path.relpath(path, MONEY)}:{no}: {line.strip()}")
    assert not hits, hits


# ── 畫面 ─────────────────────────────────────────────────────────────────────

def app_files(ext):
    for root, _dirs, files in os.walk(APP):
        for name in files:
            if name.endswith(ext):
                yield os.path.join(root, name)


def test_index_html_follows_synox_app_rules():
    with open(os.path.join(APP, "index.html"), encoding="utf-8") as f:
        html = f.read()
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S)
    assert scripts and 'src="/_synox/kit.js"' in scripts[0][0]
    assert all(not body.strip() for _attrs, body in scripts)                  # 沒有內嵌程式
    assert not re.search(r"\son[a-z]+\s*=", html) and "style=" not in html
    links = re.findall(r'<link rel="stylesheet" href="([^"]+)">', html)
    assert links[:2] == ["/_synox/kit.css", "style.css"]
    assert sorted(links[2:]) == sorted(f"css/{n}.css" for n in ("book", "home", "plan", "setup", "review", "piggy"))
    for rel in links[1:]:
        assert os.path.isfile(os.path.join(APP, rel)), rel


def test_app_has_no_own_network_polling_or_urls():
    for path in list(app_files(".js")) + list(app_files(".css")) + list(app_files(".html")):
        with open(path, encoding="utf-8") as f:
            text = f.read().replace(SVG_NS, "")
        rel = os.path.relpath(path, APP)
        assert "fetch(" not in text, rel
        assert "setInterval(" not in text, rel
        assert "http" not in text, rel
        assert "XMLHttpRequest" not in text and "WebSocket" not in text, rel


@pytest.mark.skipif(NODE is None, reason="這台沒有 node")
def test_every_js_parses():
    for path in app_files(".js"):
        out = subprocess.run([NODE, "--check", path], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, (path, out.stderr)


@pytest.mark.skipif(NODE is None, reason="這台沒有 node")
def test_views_export_what_other_views_import():
    views = os.path.join(APP, "js", "views")
    script = ("const out = {};\n" + "".join(
        f"out[{json.dumps(n)}] = Object.keys(await import({json.dumps(file_url(os.path.join(views, n + '.js')))}));\n"
        for n in ("home", "book", "piggy", "reviews", "plan", "setup", "reconcile", "onboard")) +
        "console.log(JSON.stringify(out));")
    got = node_json(script)
    need = {"home": ["render"], "book": ["render", "recordForm", "payKey", "KINDS"], "reviews": ["render"],
            "piggy": ["render", "pendingList", "titheDone", "titheRate"], "plan": ["render", "postRecurring"],
            "setup": ["render"], "reconcile": ["reconcileCard"], "onboard": ["start"]}
    for name, exports in need.items():
        assert set(exports) <= set(got[name]), name


# ── routes.js：每一列都指到宣告過的名字，路徑參數與固定欄位都是那個動作的欄位 ───────────────

@pytest.fixture(scope="module")
def routes():
    if NODE is None:
        pytest.skip("這台沒有 node")
    url = file_url(os.path.join(APP, "js", "routes.js"))
    return node_json(f"const m = await import({json.dumps(url)});\n"
                     "console.log(JSON.stringify({routes: m.ROUTES, names: m.names()}));")


def test_routes_point_to_declared_names(routes):
    decl = app_decl()
    names = routes["names"]
    assert set(names["reads"]) <= set(decl["reads"])
    assert set(names["actions"]) <= set(decl["actions"])
    assert set(names["exports"]) <= set(decl["exports"])
    # 每個讀、動作、匯出都有一條路走得到（畫面用得到的才宣告）
    assert set(names["reads"]) == set(decl["reads"])
    assert set(names["actions"]) == set(decl["actions"])
    assert set(names["exports"]) == set(decl["exports"])


def test_route_params_are_fields_of_their_action(routes):
    cmds = commands()
    for r in routes["routes"]:
        assert r["method"] in ("GET", "POST", "PATCH", "PUT", "DELETE"), r
        assert sum(1 for k in ("read", "act", "download", "none") if k in r) == 1, r
        if "act" not in r:
            continue
        acts = r["act"] if isinstance(r["act"], list) else [r["act"]]
        assert ("pick" in r) == (len(acts) > 1), r
        given = set(re.findall(r":([a-z_]+)", r["path"])) | set(r.get("fixed", {})) | set(r.get("rename", {}).values())
        for a in acts:
            fields = {f.name for f in cmds[a].fields}
            assert given <= fields, (r["path"], a, given - fields)


def test_routes_match_in_order(routes):
    """同方法、會同時對上的兩條，寫在前面的要是比較具體的那條（例：/api/dedup/scan 在 /api/dedup/:id 前面）。"""
    seen = set()
    for r in routes["routes"]:
        key = (r["method"], r["path"])
        assert key not in seen, key
        seen.add(key)
    order = [(r["method"], r["path"]) for r in routes["routes"]]
    assert order.index(("POST", "/api/dedup/scan")) < order.index(("POST", "/api/dedup/:id"))


@pytest.mark.skipif(NODE is None, reason="這台沒有 node")
def test_adapter_turns_bodies_into_string_fields():
    url = file_url(os.path.join(APP, "js", "api.js"))
    got = node_json(
        f"const a = await import({json.dumps(url)});\n"
        "const r = { act: 'pbook', rename: { rate: 'tithe_rate' }, fixed: {} };\n"
        "console.log(JSON.stringify({\n"
        "  patch: a.toValues(r, { id: '3' }, { note: null, name: '', rate: 0.1, done: true, keywords: ['a', 'b'] }, {}, 'PATCH'),\n"
        "  post: a.toValues(r, {}, { note: null, name: '', capital: 1000, ok: false, buckets: { fixed: 12000 } }, { period: '2030-01' }, 'POST'),\n"
        "  params: a.toParams({ a: 1, b: false, c: '', d: null, e: true }),\n"
        "}));")
    assert got["patch"] == {"id": "3", "note": "-", "name": "-", "tithe_rate": "0.1", "done": "yes", "keywords": "a,b"}
    assert got["post"] == {"period": "2030-01", "capital": "1000", "ok": "no", "buckets": '{"fixed":12000}'}
    assert got["params"] == {"a": "1", "e": "true"}


# ── CONTRACT.md 的名字清單跟 pack.json 一樣 ─────────────────────────────────────────

def contract_section(title):
    with open(os.path.join(MONEY, "CONTRACT.md"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^### " + re.escape(title) + r".*?$(.*?)(?=^#{2,3} )", text, re.S | re.M)
    assert m, title
    return re.findall(r"^\| `([a-z0-9_]+)` \|", m.group(1), re.M)


def test_contract_md_lists_the_same_names():
    decl = app_decl()
    assert contract_section("讀") == list(decl["reads"])
    assert contract_section("動作") == list(decl["actions"])
    assert contract_section("匯出") == list(decl["exports"])
    only = {c.name for c in commands().values() if not c.app}
    assert set(contract_section("只有指令")) == only


# ── SYNOX 本體真的載得起來（要等 W1：python.package）──────────────────────────────────

def _synox_loads_packages():
    import importlib.util
    return importlib.util.find_spec("synox.packs.pkgload") is not None


@pytest.mark.xfail(not _synox_loads_packages(), strict=True,
                   reason="這份 SYNOX 還沒有多檔工具（python.package，W1／SYNOX 的 feat/pack-package）：loader 只會把 pack.py "
                          "當單一個檔載入，pack.py 的相對 import（from .bridge import …）會失敗。換成有 W1 的 SYNOX 就會過。")
def test_real_synox_loader_loads_money_as_a_package(synox_bot):
    from synox.web.apps.spec import specs_of
    bot = synox_bot("money")
    assert bot.app.registry.node("money.add") is not None
    specs, problems = specs_of(bot.app)
    assert "money" in specs and "money" not in problems
