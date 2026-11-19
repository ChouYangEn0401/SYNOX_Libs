"""畫面的檔案：用到的讀與動作名字都在 pack.json 宣告過、檔案都在、沒有內嵌程式或外部網址。"""
import glob
import json
import os
import re

from .hbot import HABITS


def _js():
    return {p: open(p, encoding="utf-8").read() for p in glob.glob(os.path.join(HABITS, "app", "js", "*.js"))}


def test_every_act_and_read_name_is_declared():
    app = json.load(open(os.path.join(HABITS, "pack.json"), encoding="utf-8"))["web"]["app"]
    text = "\n".join(_js().values())
    acts = set(re.findall(r'\bact\("(\w+)"', text))
    reads = set(re.findall(r'\bread\("(\w+)"', text))
    assert acts and reads
    assert acts <= set(app["actions"]), acts - set(app["actions"])
    assert reads <= set(app["reads"]), reads - set(app["reads"])


def test_page_has_no_inline_code_or_external_urls():
    html = open(os.path.join(HABITS, "app", "index.html"), encoding="utf-8").read()
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"\son\w+=", html)
    for text in list(_js().values()) + [html, open(os.path.join(HABITS, "app", "style.css"), encoding="utf-8").read()]:
        assert not re.search(r"https?://(?!www\.w3\.org)", text)


def test_imports_resolve():
    for path, text in _js().items():
        for rel in re.findall(r'from "\./(\w+)\.js"', text):
            assert os.path.exists(os.path.join(os.path.dirname(path), rel + ".js")), (path, rel)
