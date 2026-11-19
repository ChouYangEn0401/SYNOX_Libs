"""🗂️ 工作區（packs/workspace，ADR-0009 v0）：固定菜單、只碰一個工作區資料夾、不執行任何程式。

- 功能：設定工作區、ls／read／tree／find、templates、new（確認卡）、mkdir、status 與稽核紀錄。
- 攻擊清單（ADR-0009 第 4 節，逐項）：每一個都要「被拒、而且工作區外（連同工作區本身）沒有任何檔案被建立或改動」。
- 組合攻擊：先有 junction 再寫入、確認卡發出後才把目標換成 junction、工作區本身被換成 junction、硬連結。
- 靜態：packs/workspace 底下的 .py 沒有開程式、動態執行、連網的東西；工具自己沒有建連結的能力。
全部用 make_bot（假 store）跑在 tmp_path；家目錄換成 tmp_path 底下的假家目錄，不碰真的使用者資料夾。"""

import ast
import os
import re
import stat
from datetime import timedelta

import pytest

from isdtelekit.core.registry import DANGER, READ, WRITE
from isdtelekit.testing import OWNER_ID

from synox.guard.testing import as_origin
from synox.packs import manifest as synox_manifest
from synox.packs import scan as synox_scan

from conftest import MONDAY, PACKS

FOLDER = os.path.join(PACKS, "workspace")
OUTSIDE_SECRET = "TOP-SECRET-OUTSIDE"


# ── 共用 ─────────────────────────────────────────────────────────────

def make_link(link, target):
    """在 link 建一個指向 target 的資料夾連結（Windows：junction，不用系統管理員；其他：symlink）。
    這是測試在模擬「別的程式／攻擊者」先建好的連結；工具自己沒有建連結的能力（test_cannot_create_links）。"""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(str(target), str(link), target_is_directory=True)


def snapshot(base, skip=()):
    """base 底下每個檔案與資料夾 → (是不是資料夾, 大小, 修改時間)。skip 裡的資料夾整個不看（不跟進連結）。"""
    skip = {os.path.normcase(str(s)) for s in skip}
    out = {}
    for folder, dirs, files in os.walk(str(base)):
        keep = []
        for d in dirs:
            path = os.path.join(folder, d)
            if os.path.normcase(path) in skip:
                continue
            st = os.lstat(path)
            out[os.path.relpath(path, str(base))] = (True, 0, st.st_mtime_ns)
            if not os.path.islink(path) and not _is_junction(path):
                keep.append(d)
        dirs[:] = keep
        for f in files:
            path = os.path.join(folder, f)
            st = os.lstat(path)
            out[os.path.relpath(path, str(base))] = (False, st.st_size, st.st_mtime_ns)
    return out


def _is_junction(path):
    if os.name != "nt":
        return False
    st = os.lstat(path)
    return bool(st.st_file_attributes & 0x400) and bool(st.st_reparse_tag & 0x20000000)


class World:
    """一個設好工作區的 bot：tmp/home/Documents/SYNOX_Workspace 是工作區，tmp/outside 是工作區外面（放著一個祕密檔）。"""

    def __init__(self, bot, tmp_path):
        self.bot = bot
        self.tmp = tmp_path
        self.mod = bot.modules["workspace"]
        self.home = tmp_path / "home"
        self.outside = tmp_path / "outside"
        (self.home / "Documents").mkdir(parents=True)
        self.outside.mkdir()
        (self.outside / "plans.txt").write_text(OUTSIDE_SECRET, encoding="utf-8")
        self.mod.home_dir = lambda: str(self.home)
        self.root = self.home / "Documents" / "SYNOX_Workspace"

    def say(self, text, kind="telegram"):
        """本人（預設：Telegram）打一句；回最後一則回覆。"""
        with as_origin(kind, OWNER_ID):
            self.bot.say(OWNER_ID, text)
        return self.bot.last(OWNER_ID)

    def press(self, data, kind="telegram"):
        with as_origin(kind, OWNER_ID):
            self.bot.press(OWNER_ID, data)
        return self.bot.last(OWNER_ID)

    def everything(self):
        """tmp 底下的全部（SDK 自己的 data 資料夾除外）：攻擊前後要一模一樣。"""
        return snapshot(self.tmp, skip=[self.tmp / "data"])


@pytest.fixture
def world(make_bot, tmp_path):
    w = World(make_bot("workspace"), tmp_path)
    assert "工作區設定好了" in w.say("/ws_root default")
    assert w.root.is_dir()
    return w


@pytest.fixture
def project(world):
    """工作區裡放一個小專案（直接用 Python 寫，不經工具）。"""
    r = world.root
    (r / "my-app" / "src").mkdir(parents=True)
    (r / "my-app" / "README.md").write_text("# my-app\n第二行\n", encoding="utf-8")
    (r / "my-app" / "src" / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    (r / "notes.txt").write_text("筆記\n", encoding="utf-8")
    return world


# ══════════════════════════════════════════════════════════════════════════
# 宣告與菜單
# ══════════════════════════════════════════════════════════════════════════

def test_manifest_declares_owner_only_filesystem_any_and_human_callers():
    m = synox_manifest.read(FOLDER, strict=True)
    assert m.access == "owner" and m.capabilities == ["filesystem:any"]
    assert m.version == "0.1.0-alpha.1" and m.stage == "alpha"
    assert m.callers == {"mkdir": ["human"], "new": ["human"], "root": ["human"]}
    for cap in ("subprocess", "exec", "network"):
        assert not m.allows(cap)


def test_menu_is_fixed_and_writes_are_human_only(make_bot):
    bot = make_bot("workspace")
    reg = bot.app.registry
    leaves = {n.name: n for n in reg.node("ws").walk() if not n.is_group}
    assert set(leaves) == {"ls", "read", "tree", "find", "templates", "status", "new", "mkdir", "root"}
    assert {n: leaves[n].effect for n in leaves} == {
        "ls": READ, "read": READ, "tree": READ, "find": READ, "templates": READ, "status": READ,
        "new": DANGER, "mkdir": WRITE, "root": DANGER}
    assert [n for n in leaves if not leaves[n].ai] == ["new", "mkdir", "root"]


def test_not_configured_says_how(make_bot, tmp_path):
    w = World(make_bot("workspace"), tmp_path)
    for text in ("/ws", "/ws_read a.txt", "/ws_tree", "/ws_find a", "/ws_mkdir a", "/ws_new empty a"):
        assert "還沒設定工作區" in w.say(text), text
    assert "還沒設定工作區" in w.say("/ws_status")


@pytest.mark.parametrize("kind", ["ai", "schedule", "remote", "unknown"])
def test_writes_refuse_non_human_callers(world, kind):
    before = world.everything()
    for text in ("/ws_root default", "/ws_mkdir made-by-ai", "/ws_new empty made-by-ai"):
        assert "要你本人" in world.say(text, kind=kind), (kind, text)
    assert world.everything() == before


# ══════════════════════════════════════════════════════════════════════════
# 設定工作區
# ══════════════════════════════════════════════════════════════════════════

def test_root_default_creates_an_empty_folder_and_status_shows_it(world):
    assert list(world.root.iterdir()) == []
    reply = world.say("/ws_status")
    assert str(world.root) in reply and "/ws_read" in reply
    assert "root" in reply and "（工作區設定）" in reply           # 稽核紀錄有設定這一筆


def test_status_for_ai_shows_only_the_folder_name(world):
    reply = world.say("/ws_status", kind="ai")
    assert "SYNOX_Workspace" in reply and str(world.home) not in reply


def test_root_custom_path(world):
    other = world.home / "Projects" / "ws"
    other.mkdir(parents=True)
    assert "工作區設定好了" in world.say(f"/ws_root {other}")
    (other / "a.txt").write_text("a", encoding="utf-8")
    assert "a.txt" in world.say("/ws")


def test_root_refuses_unsafe_folders(world):
    home = world.home
    (home / "AppData" / "Local" / "x").mkdir(parents=True)
    (home / ".ssh" / "keys").mkdir(parents=True)
    has_secrets = home / "Projects" / "with-secrets"
    (has_secrets / ".ssh").mkdir(parents=True)
    has_env = home / "Projects" / "with-env"
    has_env.mkdir(parents=True)
    (has_env / ".env").write_text("K=V", encoding="utf-8")
    a_file = home / "Projects" / "file.txt"
    a_file.write_text("x", encoding="utf-8")
    linked = home / "Projects" / "linked"
    make_link(linked, world.outside)
    drive_root = os.path.abspath(os.sep)
    cases = {
        "relative": "Projects",
        "dotdot": str(home / "Projects" / ".." / "Projects"),
        "tilde": "~/Projects",
        "percent": str(home / "Projects%2f..").replace("\\", "/"),
        "unc": r"\\server\share\ws",
        "device": "\\\\?\\" + str(home / "Projects"),
        "drive root": drive_root,
        "home": str(home),
        "ancestor of home": str(world.tmp),
        "appdata": str(home / "AppData" / "Local" / "x"),
        "ssh": str(home / ".ssh" / "keys"),
        "contains .ssh": str(has_secrets),
        "contains .env": str(has_env),
        "missing": str(home / "nope"),
        "a file": str(a_file),
        "junction": str(linked),
        "reserved": str(home / "Projects" / "CON"),
        "trailing dot": str(home / "Projects") + ".",
    }
    saved = world.say("/ws_status")
    for label, text in cases.items():
        reply = world.say(f"/ws_root {text}")
        assert reply.startswith("❌"), (label, reply)
    assert world.say("/ws_status").split("\n")[0] == saved.split("\n")[0]     # 工作區沒有被換掉


def test_root_place_rules(world):
    """工作區的位置（只看路徑文字，不碰真的系統資料夾）：系統、程式資料夾、別人的家目錄、禁區、太大的範圍不行。"""
    place = world.mod.place_problem
    if os.name == "nt":
        home = "C:\\Users\\me"
        cases = {"C:\\": "root_wide", "C:\\Users": "root_wide", home: "root_wide",
                 "C:\\Windows\\Temp\\ws": "root_system", "C:\\Program Files\\ws": "root_system",
                 "C:\\ProgramData\\ws": "root_system", "C:\\Users\\alice\\ws": "root_system",
                 "D:\\keys\\.ssh\\ws": "root_denied", home + "\\AppData\\Local\\ws": "root_denied",
                 home + "\\.ssh": "root_denied", home + "\\Documents\\Chrome\\ws": "root_denied",
                 home + "\\Documents\\SYNOX_Workspace": None, "D:\\Projects\\ws": None}
    else:
        home = "/home/me"
        cases = {"/": "root_wide", "/home": "root_wide", home: "root_wide", "/etc/ws": "root_system",
                 "/usr/local/ws": "root_system", "/home/alice/ws": "root_system", "/srv/.ssh/ws": "root_denied",
                 home + "/.ssh": "root_denied", home + "/.config/google-chrome/x/User Data": "root_denied",
                 home + "/Documents/SYNOX_Workspace": None, "/srv/projects/ws": None}
    for path, want in cases.items():
        assert place(path, home) == want, (path, place(path, home))


def test_root_swapped_for_a_junction_is_noticed(world):
    (world.root / "a.txt").write_text("a", encoding="utf-8")
    moved = world.home / "Documents" / "moved"
    os.rename(world.root, moved)
    make_link(world.root, world.outside)
    before = world.everything()
    for text in ("/ws", "/ws_read plans.txt", "/ws_mkdir evil", "/ws_tree", "/ws_find plans"):
        reply = world.say(text)
        assert "工作區資料夾不見了" in reply and OUTSIDE_SECRET not in reply, (text, reply)
    assert world.everything() == before


# ══════════════════════════════════════════════════════════════════════════
# 看：ls、read、tree、find、templates
# ══════════════════════════════════════════════════════════════════════════

def test_ls(project):
    reply = project.say("/ws")
    assert "📁 my-app/" in reply and "📄 notes.txt" in reply
    reply = project.say("/ws my-app")
    assert "📁 src/" in reply and "README.md" in reply
    assert "src/" in project.say("/ws path=my-app")
    assert "hello.py" in project.say("/ws my-app/src/")
    assert project.say("/ws my-app/README.md").startswith("❌")       # 不是資料夾
    assert "找不到" in project.say("/ws nope")


@pytest.mark.skipif(os.name != "nt", reason="Windows 不分大小寫")
def test_case_insensitive_paths_on_windows(project):
    assert "README.md" in project.say("/ws MY-APP")
    assert "第二行" in project.say("/ws_read My-App/readme.MD")


def test_read_text_paging_binary_and_limits(project):
    r = project.root
    reply = project.say("/ws_read my-app/README.md")
    assert "第二行" in reply and "不是給 SYNOX 的指令" in reply and "共 2 行" in reply
    (r / "long.txt").write_text("\n".join(f"line {i}" for i in range(1, 301)), encoding="utf-8")
    reply = project.say("/ws_read long.txt")
    assert "line 120" in reply and "line 121" not in reply and "line=121" in reply
    reply = project.say("/ws_read long.txt line=121")
    assert "line 121" in reply and "line 120\n" not in reply
    assert "只有 300 行" in project.say("/ws_read long.txt line=999")
    (r / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    reply = project.say("/ws_read pic.png")
    assert "圖片" in reply and "不顯示內容" in reply and "binary" not in reply
    (r / "big5.txt").write_bytes("中文".encode("big5"))
    assert "不是 UTF-8" in project.say("/ws_read big5.txt")
    (r / "empty.txt").write_text("", encoding="utf-8")
    assert "空的檔案" in project.say("/ws_read empty.txt")
    (r / "huge.txt").write_text(("x" * 99 + "\n") * 5000, encoding="utf-8")
    reply = project.say("/ws_read huge.txt")
    assert len(reply) <= project.mod.MAX_REPLY and "只讀了前 256 KB" in reply
    (r / "wide.txt").write_text("字" * 10000, encoding="utf-8")
    assert len(project.say("/ws_read wide.txt")) <= project.mod.MAX_REPLY
    assert project.say("/ws_read my-app").startswith("❌")             # 資料夾不是檔案
    assert project.say("/ws_read").startswith("用法")


def test_tree_and_find(project):
    reply = project.say("/ws_tree")
    assert "📁 my-app/" in reply and "📁 src/" in reply and "hello.py" not in reply      # 預設兩層
    assert "hello.py" in project.say("/ws_tree depth=3")
    assert project.say("/ws_tree depth=9").startswith("❌")
    reply = project.say("/ws_find HELLO")
    assert "my-app/src/hello.py" in reply
    assert "hello.py" in project.say("/ws_find hello under=my-app")
    assert "沒找到" in project.say("/ws_find nothing-like-this")
    for bad in ("*.py", "a|b", "../x", "x" * 41):
        assert project.say(f"/ws_find {bad}").startswith("❌"), bad


def test_ls_has_a_limit(world):
    for i in range(250):
        (world.root / f"f{i:03d}.txt").write_text("", encoding="utf-8")
    reply = world.say("/ws")
    assert "還有 50 個沒列出" in reply and len(reply) <= world.mod.MAX_REPLY


def test_templates(world):
    reply = world.say("/ws_templates")
    assert "empty" in reply and "python-cli" in reply and "pyproject.toml" in reply


# ══════════════════════════════════════════════════════════════════════════
# 建：new（確認卡）、mkdir、稽核
# ══════════════════════════════════════════════════════════════════════════

def test_new_shows_the_full_file_list_then_creates_exactly_that(world):
    reply = world.say("/ws_new python-cli my-tool")
    for rel in ("README.md", ".gitignore", "pyproject.toml", "src/my_tool/__init__.py", "src/my_tool/__main__.py",
                "src/my_tool/cli.py", "tests/test_cli.py"):
        assert f"my-tool/{rel}" in reply, rel
    assert not (world.root / "my-tool").exists()                      # 還沒按，什麼都沒建
    ok = next(b for b in world.bot.buttons(OWNER_ID) if b.endswith(":ok"))
    reply = world.press(ok)
    assert "建好了 my-tool/" in reply
    made = sorted(os.path.relpath(os.path.join(b, f), world.root).replace(os.sep, "/")
                  for b, _d, fs in os.walk(world.root) for f in fs)
    assert made == ["my-tool/.gitignore", "my-tool/README.md", "my-tool/pyproject.toml",
                    "my-tool/src/my_tool/__init__.py", "my-tool/src/my_tool/__main__.py",
                    "my-tool/src/my_tool/cli.py", "my-tool/tests/test_cli.py"]
    cli = (world.root / "my-tool" / "src" / "my_tool" / "cli.py").read_text(encoding="utf-8")
    assert "prog=\"my-tool\"" in cli and "{" not in cli.replace("{}", "")
    ast.parse(cli)
    ast.parse((world.root / "my-tool" / "tests" / "test_cli.py").read_text(encoding="utf-8"))
    world.press(ok)                                                    # 同一張卡不能按兩次
    assert world.bot.answers()[-1] == world.mod.SAY["card_gone"]
    status = world.say("/ws_status")
    assert "new my-tool ✅" in status and "print(" not in status       # 紀錄沒有檔案內容


def test_new_cancel_expire_and_replace(world):
    world.say("/ws_new empty one")
    no = next(b for b in world.bot.buttons(OWNER_ID) if b.endswith(":no"))
    assert "已取消" in world.press(no) and not (world.root / "one").exists()
    world.say("/ws_new empty two")
    ok = next(b for b in world.bot.buttons(OWNER_ID) if b.endswith(":ok"))
    world.mod.now = lambda: MONDAY + timedelta(minutes=11)
    assert "超過 10 分鐘" in world.press(ok) and not (world.root / "two").exists()
    world.mod.now = lambda: MONDAY
    world.say("/ws_new empty three")
    first = next(b for b in world.bot.buttons(OWNER_ID) if b.endswith(":ok"))
    world.say("/ws_new empty four")                                    # 新的卡取代舊的
    world.press(first)
    assert world.bot.answers()[-1] == world.mod.SAY["card_gone"] and not (world.root / "three").exists()
    for bad in ("wsnew:zz:ok", "wsnew:" + "a" * 16 + ":maybe", "wsnew:" + "é" * 16 + ":ok"):
        world.press(bad)
    assert not (world.root / "four").exists()


def test_new_refuses_bad_input(world):
    (world.root / "taken").mkdir()
    cases = ["/ws_new empty taken", "/ws_new nope my-app", "/ws_new empty My-App", "/ws_new empty -rf",
             "/ws_new empty a_b", "/ws_new empty con", "/ws_new empty nul", "/ws_new python-cli 1abc",
             "/ws_new empty " + "a" * 41, "/ws_new empty ../x", "/ws_new empty .git", "/ws_new empty a.bat"]
    before = world.everything()
    for text in cases:
        assert world.say(text).startswith("❌"), text
    assert world.bot.buttons(OWNER_ID) == []                           # 一張確認卡都沒發
    assert world.everything() == before


def test_press_from_non_human_is_refused(world):
    world.say("/ws_new empty proj")
    ok = next(b for b in world.bot.buttons(OWNER_ID) if b.endswith(":ok"))
    world.press(ok, kind="ai")
    assert not (world.root / "proj").exists()
    assert "要你本人" in world.bot.answers()[-1]


def test_mkdir(world):
    assert "建好資料夾：docs/" in world.say("/ws_mkdir docs")
    assert (world.root / "docs").is_dir()
    assert "已經有東西" in world.say("/ws_mkdir docs")
    assert "一次只建一層" in world.say("/ws_mkdir a/b/c")
    assert "可以直接執行" in world.say("/ws_mkdir run.bat")
    assert "工作區本身" in world.say("/ws_mkdir .")
    assert "mkdir docs ✅" in world.say("/ws_status")


def test_errors_do_not_echo_the_input(world):
    marker = "ZQX9MARK"
    for text in (f"/ws_read ../{marker}", f"/ws_mkdir {marker}:x", f"/ws_root {marker}", f"/ws_new empty {marker}",
                 f"/ws_read {marker}%2f", f"/ws_find {marker}*"):
        reply = world.say(text)
        assert reply.startswith("❌") and marker not in reply, (text, reply)


# ══════════════════════════════════════════════════════════════════════════
# ADR-0009 第 4 節：攻擊清單（逐項；被拒＋工作區內外都沒有任何改動）
# ══════════════════════════════════════════════════════════════════════════

ATTACKS = {
    # .. 與跳出工作區
    "dotdot backslash": "..\\x",
    "dotdot slash": "../x",
    "a/../../x": "a/../../x",
    "only dotdot": "..",
    "dotdot at end": "a/..",
    "dot inside": "a/./b",
    # 絕對路徑、磁碟機代號、網路路徑、裝置路徑
    "C:\\Windows": "C:\\Windows",
    "C:/Windows": "C:/Windows",
    "drive relative": "C:x",
    "posix absolute": "/etc/passwd",
    "rooted backslash": "\\Windows",
    "unc": "\\\\server\\share",
    "unc slashes": "//server/share",
    "device path": "\\\\?\\C:\\Windows",
    "dot device": "\\\\.\\C:\\x",
    # NTFS 資料流
    "x:stream": "x:stream",
    "::$DATA": "notes.txt::$DATA",
    # 保留名稱
    "CON": "CON",
    "nul.txt": "nul.txt",
    "aux lower": "aux",
    "com1.log": "com1.log",
    "LPT9": "LPT9",
    "con space ext": "con .txt",
    "CONIN$": "CONIN$",
    "superscript com": "COM\u00b9",
    # 結尾空白或點
    "name.": "name.",
    "name space": "name ",
    "dir dot": "dir./x",
    "dir space": "dir /x",
    "dots only": "...",
    # 網址編碼與雙重編碼
    "url dotdot": "%2e%2e/x",
    "url slash": "..%2fx",
    "url backslash": "%2e%2e%5cx",
    "url mixed": "a%2f..%2f..%2fx",
    "double encoded": "%252e%252e/x",
    "double encoded slash": "..%252fx",
    # Unicode：全形斜線與點、長得像的字
    "fullwidth dots slash": "\uff0e\uff0e\uff0fx",
    "fullwidth slash": "..\uff0fx",
    "fullwidth backslash": "a\uff3c..\uff3c..",
    "one dot leader": "\u2024\u2024/x",
    "two dot leader": "\u2025/x",
    "division slash": "..\u2215x",
    "fraction slash": "..\u2044x",
    "small full stop": "\ufe52\ufe52/x",
    "ideographic stop": "\u3002\u3002/x",
    # 空位元組、控制字元、看不到的字
    "null byte": "a\x00b",
    "null before ext": "notes.txt\x00.md",
    "tab": "a\tb",
    "escape": "\x1b[31mx",
    "zero width": "a\u200bb",
    "bidi override": "\u202ex.txt",
    "nbsp": "a\u00a0",
    # 超長
    "too deep": "/".join(["a"] * 30),
    "too long part": "x" * 300,
    "too long total": "/".join(["abcdefghij"] * 40),
    # 以 - 開頭（旗標）
    "dash": "-rf",
    "double dash flag": "--upload-pack=x",
    "dash inside": "a/-b",
    # ~（家目錄、8.3 短檔名）
    "tilde": "~",
    "tilde slash": "~/x",
    "short name": "PROGRA~1",
    "short git": "GIT~1/hooks",
    # 萬用字元與 shell 符號
    "star": "*",
    "question": "a?",
    "pipe": "a|b",
    "angle": "<x>",
    "quote": "\"x\"",
    # 空的一段
    "double slash": "a//b",
}


@pytest.mark.parametrize("attack", list(ATTACKS.values()), ids=list(ATTACKS))
def test_path_attacks_are_refused_and_change_nothing(project, attack):
    """ADR-0009 第 4 節：每個攻擊路徑對 ls／read／tree／find／mkdir 都被拒，tmp 底下（工作區內外）一個位元組都沒變。"""
    before = project.everything()
    with pytest.raises(project.mod.WsError):
        project.mod.resolve(str(project.root), attack)
    with pytest.raises(project.mod.WsError):
        project.mod.make_dir(str(project.root), attack)
    commands = [f"/ws {attack}", f"/ws_read {attack}", f"/ws_tree {attack}", f"/ws_find a under={attack}"]
    if " ".join(attack.split()) == attack:
        # 打字的那條路：SDK 先照空白切參數（tab、NBSP、結尾空白會變成一般空白或消失，到工具時已經是無害的名字），
        # 這種只在上面直接對 resolve／make_dir 測；其餘的連 mkdir 都從聊天打一次
        commands.append(f"/ws_mkdir {attack}")
    for text in commands:
        reply = project.say(text)
        assert reply.startswith("❌") or reply.startswith("用法"), (text, reply)
        assert OUTSIDE_SECRET not in reply
    assert project.everything() == before


def test_attacks_through_key_value_form_are_refused(project):
    before = project.everything()
    for text in ("/ws_read path=../outside/plans.txt", "/ws path=C:\\", "/ws_mkdir path=..\\evil",
                 "/ws_tree path=%2e%2e", "/ws_read path=\uff0e\uff0e\uff0fx"):
        assert project.say(text).startswith("❌"), text
    assert project.everything() == before


DENIED = [".git/hooks/pre-commit", ".git/config", ".GIT/HOOKS/pre-commit", ".Git/config", ".env", ".ENV",
          ".env.local", "prod.env", ".ssh/id_rsa", ".SSH/known_hosts", "id_ed25519", "_keys/a.secret.asc",
          "_KEYS/b.public.asc", "server.pem", "api.KEY", "my-secret.txt", "Passwords.txt", "credentials.json",
          "AppData/Local/x.txt", "appdata/Roaming/y.txt",
          "Google/Chrome/User Data/Default/Login Data", "Microsoft/Edge/User Data/Default/Cookies",
          "Mozilla/Firefox/Profiles/p/key4.db", "my-app/.git/hooks/post-checkout", "my-app/.env"]


@pytest.mark.parametrize("rel", DENIED)
def test_deny_list_inside_the_workspace(project, rel):
    """禁區：就算真的在工作區裡也不碰（大小寫變化一樣）；列表與搜尋看不到，讀、建都拒。"""
    path = project.root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("SENSITIVE-" + rel, encoding="utf-8")
    before = project.everything()
    reply = project.say(f"/ws_read {rel}")
    assert reply.startswith("❌") and "SENSITIVE" not in reply, reply
    assert project.say(f"/ws_mkdir {rel}/sub").startswith("❌")
    parts = rel.split("/")
    i = next(k for k, p in enumerate(parts) if project.mod.is_denied(p))   # 第一段禁區的名字
    parent = "/".join(parts[:i])
    listing = project.say(f"/ws {parent}" if parent else "/ws")
    assert f" {parts[i]}" not in listing and "不開放的項目" in listing, listing
    assert project.say(f"/ws {'/'.join(parts[:i + 1])}").startswith("❌")
    tree = project.say("/ws_tree depth=4")
    assert "SENSITIVE" not in tree and f" {parts[i]}" not in tree
    found = project.say(f"/ws_find {parts[-1][:12].strip('. ')}")
    assert rel not in found and f"/{parts[i]}" not in found and not found.startswith(f"{parts[i]}")
    assert project.everything() == before


def test_listing_counts_hidden_entries(project):
    (project.root / ".git").mkdir()
    (project.root / ".env").write_text("K=V", encoding="utf-8")
    reply = project.say("/ws")
    assert ".git" not in reply and ".env" not in reply and "另有 2 個不開放的項目" in reply


# ── 連結與組合攻擊 ───────────────────────────────────────────────────

def test_junction_inside_workspace_pointing_outside(project):
    """工作區裡有一個指到外面的 junction／symlink（別的程式建的）：看、讀、找、建都不跟進。"""
    make_link(project.root / "jx", project.outside)
    (project.root / "my-app" / "inner").mkdir()
    make_link(project.root / "my-app" / "inner" / "deep", project.outside)
    before = project.everything()
    assert "↪ jx（連結，不跟進）" in project.say("/ws")
    for text in ("/ws jx", "/ws_read jx/plans.txt", "/ws_tree jx", "/ws_find plans under=jx",
                 "/ws_read my-app/inner/deep/plans.txt", "/ws my-app/inner/deep"):
        reply = project.say(text)
        assert reply.startswith("❌") and OUTSIDE_SECRET not in reply, (text, reply)
    reply = project.say("/ws_tree depth=4")
    assert "plans.txt" not in reply and "↪ deep" in reply
    assert "沒找到" in project.say("/ws_find plans")
    assert project.everything() == before


def test_junction_then_write_through_it(project):
    """兩步組合：先有 junction（工具自己建不出來），再想用它寫入——mkdir 與 new 都被擋，外面沒多任何東西。"""
    make_link(project.root / "jx", project.outside)
    before = project.everything()
    for text in ("/ws_mkdir jx/evil", "/ws_mkdir jx", "/ws_new empty jx"):
        assert project.say(text).startswith("❌"), text
    assert project.everything() == before
    assert sorted(os.listdir(project.outside)) == ["plans.txt"]


def test_target_swapped_for_junction_after_the_card(project):
    """確認卡發出之後、按下去之前，目標名字被換成 junction：按了也不建，外面沒多任何東西。"""
    project.say("/ws_new python-cli proj")
    ok = next(b for b in project.bot.buttons(OWNER_ID) if b.endswith(":ok"))
    make_link(project.root / "proj", project.outside)
    before = project.everything()
    reply = project.press(ok)
    assert reply.startswith("❌"), reply
    assert project.everything() == before
    assert sorted(os.listdir(project.outside)) == ["plans.txt"]
    assert "new proj ❌" in project.say("/ws_status")


def test_symlinked_file_is_not_read(project):
    link = project.root / "link.txt"
    try:
        os.symlink(str(project.outside / "plans.txt"), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("這台電腦不給一般使用者建 symlink（Windows 沒開開發人員模式）；資料夾連結由 junction 的測試涵蓋")
    reply = project.say("/ws_read link.txt")
    assert reply.startswith("❌") and OUTSIDE_SECRET not in reply


def test_hard_linked_file_is_not_read(project):
    link = project.root / "hard.txt"
    try:
        os.link(str(project.outside / "plans.txt"), str(link))
    except OSError:
        pytest.skip("這個檔案系統不能建硬連結")
    reply = project.say("/ws_read hard.txt")
    assert "硬連結" in reply and OUTSIDE_SECRET not in reply


def test_created_project_has_no_executables_and_no_git(world):
    """組合：建好的專案沒有可執行的副檔名、沒有 .git（更沒有 .git/hooks）；每個範本都一樣。"""
    mod = world.mod
    for template in mod.TEMPLATES:
        name = f"t-{template}"
        world.say(f"/ws_new {template} {name}")
        ok = next(b for b in world.bot.buttons(OWNER_ID) if b.endswith(":ok"))
        assert "建好了" in world.press(ok)
        for base, dirs, files in os.walk(world.root / name):
            assert ".git" not in [d.casefold() for d in dirs]
            for f in dirs + files:
                assert not f.casefold().endswith(mod.EXEC_EXT), f
                assert not mod.is_denied(f), f
                assert not os.path.islink(os.path.join(base, f))
        for rel, body in mod.plan_of(template, name):
            assert "hooks" not in rel and ".git/" not in rel
            assert "{name}" not in body and "{pkg}" not in body


def test_templates_are_checked_against_the_rules():
    """範本本身也要過路徑規則（萬一之後有人加了 .bat 或 .git/hooks，plan_of 會擋）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ws_rules_probe", os.path.join(FOLDER, "pack.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for files in ((("run.bat", "x"),), ((".git/hooks/pre-commit", "x"),), (("a/../b.txt", "x"),), (("x.ps1", "x"),)):
        mod.TEMPLATES["probe"] = {"summary": "", "files": files}
        with pytest.raises(mod.WsError):
            mod.plan_of("probe", "demo")


# ══════════════════════════════════════════════════════════════════════════
# 靜態：菜單以外沒有路
# ══════════════════════════════════════════════════════════════════════════

FORBIDDEN_WORDS = (r"\bsubprocess\b", r"\bos\.system\b", r"\bos\.popen\b", r"\beval\b", r"\bexec\b", r"__import__",
                   r"\bctypes\b", r"\bsocket\b", r"\burllib\b", r"\brequests\b", r"\bos\.environ\b", r"\bgetenv\b",
                   r"\bimportlib\b", r"\bpickle\b", r"\bmarshal\b", r"\bshutil\b", r"\bos\.startfile\b",
                   r"\bos\.spawn", r"\bos\.exec", r"\bos\.symlink\b", r"\bCreateJunction\b", r"\b_winapi\b",
                   r"\bos\.link\b", r"\bos\.remove\b", r"\bos\.unlink\b", r"\bos\.rename\b", r"\bos\.replace\b",
                   r"\brmdir\b", r"\bwebbrowser\b", r"\bapp\.jobs\b")
ALLOWED_IMPORTS = {"__future__", "os", "re", "secrets", "stat", "unicodedata", "datetime", "typing", "isdtelekit",
                   "synox"}


def _py_files():
    out = {}
    for base, dirs, files in os.walk(FOLDER):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith((".py", ".pyw")):
                with open(os.path.join(base, f), encoding="utf-8") as fh:
                    out[f] = fh.read()
    return out


def test_static_no_process_exec_network_or_links():
    files = _py_files()
    assert set(files) == {"pack.py"}
    for name, text in files.items():
        for pattern in FORBIDDEN_WORDS:
            assert not re.search(pattern, text), f"{name}: {pattern}"
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            else:
                mods = []
            for mod in mods:
                assert mod.split(".")[0] in ALLOWED_IMPORTS, f"{name}: import {mod}"
            if isinstance(node, ast.Name):
                assert node.id not in ("eval", "exec", "compile", "getattr", "setattr", "globals", "vars",
                                       "__import__", "breakpoint"), f"{name}: {node.id}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ("system", "popen", "startfile", "symlink", "link", "remove", "unlink",
                                              "rename", "replace_file", "rmdir", "removedirs", "chmod"), \
                    f"{name}: .{node.func.attr}(…)"
    # 寫檔只有一種：open(…, "x")（只建新檔）；其餘的 open 都是 "rb"
    opens = [n for n in ast.walk(ast.parse(files["pack.py"])) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "open"]
    assert sorted(ast.literal_eval(n.args[1]) for n in opens) == ["rb", "x"]


def test_static_check_catches_bad_shapes():
    """上面那個檢查自己的測試：故意寫壞的樣子一定被抓到。"""
    bad = "import subprocess\nos.system('x')\neval('1')\nos.symlink(a, b)\n"
    assert all(re.search(p, bad) for p in (r"\bsubprocess\b", r"\bos\.system\b", r"\beval\b", r"\bos\.symlink\b"))


def test_synox_scan_passes_with_only_filesystem():
    m = synox_manifest.read(FOLDER, strict=True)
    report = synox_scan.scan(m)
    assert report.problems(m) == [], report.format(m)
    assert set(report.required_capabilities) <= {"filesystem:own", "filesystem:any"}
    assert not report.opaque and not report.external
    assert not [f for f in report.findings if f.kind in ("network", "subprocess", "exec", "binary", "env")]


def test_resolve_is_the_only_way_from_input_to_disk():
    """每個指令都經 resolve()：open／os.mkdir／os.listdir／os.lstat 拿到的路徑來自 resolve 的結果或由它推得；
    這裡只做粗的檢查：使用者給的 values[…] 一律先進 resolve／split_rel／plan_of／check_root_text，不直接碰檔案。"""
    text = _py_files()["pack.py"]
    tree = ast.parse(text)
    fs_calls = ("open", "os.mkdir", "os.listdir", "os.lstat", "os.path.realpath", "os.path.lexists")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func) in fs_calls:
            assert "values" not in ast.unparse(node), ast.unparse(node)
    assert text.count("def resolve(") == 1
