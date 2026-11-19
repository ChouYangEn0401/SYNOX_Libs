"""每個有 web.app 的官方工具都要守的規則（ADR-0003 第 1 節第 3 點、§3 第 13 項）。之後每個新 app 自動被它檢查。

SYNOX 對一般工具只把「畫面程式自己連線」列成提醒；這個工具庫把它當成不准：官方工具的畫面一律經 SYNOX 的共用元件（SynoxApp.*），
所以確認框、匯出前的提醒、「人還在」的訊號都是同一份程式。"""
import json
import os
import re

import pytest

from conftest import PACKS, _synox

FORBIDDEN_JS = ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "setInterval", "innerHTML", "/api/", "cookie",
                "sessionStorage", "localStorage", "serviceWorker", "location.href", "location.assign", "location.replace",
                "window.open", "127.0.0.")
APP_KEYS = {"entry", "root", "title", "kit", "reads", "actions", "exports", "remote"}
# 記帳 0.4.0 的畫面從原本的記帳畫面原樣搬來：每個分頁還是叫 api("/api/…")，由 app/js/api.js（轉接器）
# 照 app/js/routes.js 的對照表換成 SynoxApp.read／act／download。這裡的 "/api/…" 只是對照表的鍵、不是網址；
# 自己連線（fetch(、XMLHttpRequest…）照樣擋（packs/money/CONTRACT.md「畫面」）。
ALLOWED_JS = {"money": ("/api/",)}


def app_tools():
    out = []
    for folder in _synox.pack_folders(private=False):        # 私人工具不在工具庫的公開規矩裡（自己的測試另外管）
        with open(os.path.join(folder, "pack.json"), encoding="utf-8") as f:
            raw = json.load(f)
        app = (raw.get("web") or {}).get("app")
        if app:
            out.append((os.path.basename(folder), folder, raw, app))
    return out


TOOLS = app_tools()


def test_there_is_at_least_one_app():
    assert [t[0] for t in TOOLS] == ["calendar", "habits", "money", "projects", "usb-keys", "video-notes"]


@pytest.mark.parametrize("name,folder,raw,app", TOOLS, ids=[t[0] for t in TOOLS])
def test_page_rules(name, folder, raw, app):
    root = os.path.join(folder, app["root"])
    for here, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".js"):
                text = open(os.path.join(here, f), encoding="utf-8").read()
                bad = [w for w in FORBIDDEN_JS if w in text and w not in ALLOWED_JS.get(name, ())]
                assert not bad, f"{name}/{f}: {bad}"
    entry = open(os.path.join(folder, app["entry"]), encoding="utf-8").read()
    scripts = re.findall(r"<script\b[^>]*>", entry)
    assert scripts and 'src="/_synox/kit.js"' in scripts[0]
    assert 'href="/_synox/kit.css"' in entry
    assert app["kit"] == 1 and set(app) <= APP_KEYS


@pytest.mark.parametrize("name,folder,raw,app", TOOLS, ids=[t[0] for t in TOOLS])
def test_docs_do_not_print_the_address(name, folder, raw, app):
    for doc in ("README.md", "SKILL.md"):
        text = open(os.path.join(folder, doc), encoding="utf-8").read()
        assert "127.0.0.2" not in text and "書籤" not in text, doc


@pytest.mark.parametrize("name,folder,raw,app", TOOLS, ids=[t[0] for t in TOOLS])
def test_every_action_has_its_command(synox_bot, name, folder, raw, app):
    bot = synox_bot(name)
    group = raw.get("group") or name
    for action, effect in app["actions"].items():
        node = bot.app.registry.node(f"{group}.{action}")
        assert node is not None and str(node.effect).upper() == effect.upper(), action
    from synox.web import apps
    assert apps.of(bot.app).problems() == {}
