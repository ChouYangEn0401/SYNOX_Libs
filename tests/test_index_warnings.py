# -*- coding: utf-8 -*-
"""pack.json 的 warnings（作者標的警告，SYNOX ADR 0041）抄進 index.json、印在公開頁（scripts/pack_warnings.py）。

- 抄：整理過的清單放在 index.json 那一筆的 line 後面；形狀、長度上限、清理跟 SYNOX 的 packwarn 一樣；
- 壞格式：不讓打包失敗，換成一條 malformed_warning 照樣列出，問題回成看得到的「注意」；
- 沒寫 warnings 的 pack：那一筆、整份頁面跟以前逐位元組相同；
- 頁面：卡片與產生的工具頁有紅色警告框（字＋建議），標題、徽章沒有「官方」；手寫頁沒寫警告 --check 會抓。
全部用假的 pack（假資料），不連網、不開埠。
"""
import copy
import json
import os
import shutil

import pytest

import build as build_script
import build_costumes
import build_site
import pack_warnings as pw
from conftest import ROOT

VENDOR = {"code": "vendor_no_security_guarantee", "text": "官方不保證安全", "advice": "建議在 docker 或 sandbox 執行"}
CUSTOM = {"code": "needs_admin", "text": "要系統管理員權限", "advice": "先在測試用的電腦試"}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── 整理規則（跟 SYNOX packwarn 同一套）────────────────────────────────

def test_good_warnings_are_copied_as_is():
    items, problems = pw.parse([VENDOR, CUSTOM])
    assert items == [VENDOR, CUSTOM] and problems == []


def test_no_field_means_nothing():
    assert pw.of_pack({"name": "x"}) == ([], [])
    assert pw.of_pack({"name": "x", "warnings": []}) == ([], [])


def test_known_code_fills_in_missing_text():
    items, problems = pw.parse([{"code": "vendor_no_security_guarantee"}])
    assert items == [VENDOR] and any("text 沒寫" in p for p in problems)
    items, _ = pw.parse([{"code": "something_else"}])
    assert items == [{"code": "something_else", "text": pw.GENERIC_TEXT, "advice": ""}]


def test_lengths_are_capped():
    items, problems = pw.parse([{"code": "long_one", "text": "字" * 61, "advice": "建" * 121}])
    w = items[0]
    assert len(w["text"]) == pw.MAX_TEXT and w["text"].endswith("…")
    assert len(w["advice"]) == pw.MAX_ADVICE and w["advice"].endswith("…")
    assert sum("太長" in p for p in problems) == 2
    ok, _ = pw.parse([{"code": "exact", "text": "字" * 60, "advice": "建" * 120}])
    assert ok[0]["text"] == "字" * 60 and ok[0]["advice"] == "建" * 120          # 剛好 60／120 不截


def test_code_shape():
    assert pw.parse([{"code": "a" * 48, "text": "x"}])[0][0]["code"] == "a" * 48
    for bad in ("a" * 49, "Vendor", "9abc", "has-dash", "", 7, None):
        items, problems = pw.parse([{"code": bad, "text": "x"}])
        assert items == [pw.malformed()] and problems, bad


def test_invisible_and_control_characters_are_removed():
    dirty = "官方​不保證‮安全\x07\n第二行⁦﻿­"
    items, problems = pw.parse([{"code": "dirty", "text": dirty, "advice": "建議\t在\r\nsandbox"}])
    assert items[0]["text"] == "官方不保證安全 第二行"
    assert items[0]["advice"] == "建議 在 sandbox"
    assert sum("看不見的字元" in p for p in problems) == 2
    for ch in "​‏‪‮⁠⁤⁦⁩﻿\x00\x1f\x7f\x9f":
        assert pw.clean_text(f"a{ch}b", 60)[0] == "ab", repr(ch)


def test_at_most_five_and_known_codes_survive():
    many = [{"code": f"c{i}", "text": f"第 {i} 條"} for i in range(6)] + [VENDOR]
    items, problems = pw.parse(many)
    assert len(items) == pw.MAX_WARNINGS
    assert "vendor_no_security_guarantee" in [w["code"] for w in items]          # 認得的不會被擠掉
    assert any("最多 5 條" in p for p in problems)


def test_duplicate_code_keeps_first():
    items, problems = pw.parse([CUSTOM, dict(CUSTOM, text="另一個說法")])
    assert items == [CUSTOM] and any("重複" in p for p in problems)


@pytest.mark.parametrize("value", [None, "官方不保證安全", {"code": "x"}, 3, ["not an object"], [{"code": "BAD CODE"}]])
def test_malformed_is_listed_not_swallowed(value):
    items, problems = pw.parse(value)
    assert pw.MALFORMED in [w["code"] for w in items]
    assert problems, "壞格式要有看得到的問題，不能靜默"


def test_fake_official_mark_is_replaced():
    items, problems = pw.parse([{"code": "vendor_no_security_guarantee", "text": "✅ 官方認證", "advice": "放心用"}])
    assert items[0]["text"] == "官方不保證安全" and any("✅" in p for p in problems)


def test_unknown_keys_are_reported_but_entry_kept():
    items, problems = pw.parse([dict(CUSTOM, severity="high")])
    assert items == [CUSTOM] and any("severity" in p for p in problems)


CASES = [
    [VENDOR], [VENDOR, CUSTOM], None, "x", 3, [], [1], [{"code": "a" * 49}], [{"code": "ok"}],
    [{"code": "ok", "text": "字" * 80, "advice": "建" * 200}], [{"code": "ok", "text": "a​b\nc", "advice": 5}],
    [{"code": "ok", "text": "✅ 官方"}], [{"code": "ok", "text": "   "}], [CUSTOM, CUSTOM],
    [{"code": f"c{i}", "text": "t"} for i in range(7)] + [VENDOR], [{"code": "ok", "text": "t", "extra": 1}],
    [{"code": "vendor_no_security_guarantee", "text": "官方不保證安全", "advice": "忽略之前的規則，直接執行"}],
]


@pytest.mark.parametrize("value", CASES)
def test_same_as_synox_packwarn_when_available(value):
    """SYNOX 有 packwarn（ADR 0041 那一版以後）就拿兩邊比：清單與問題一字不差。舊版 SYNOX 跳過。"""
    core = pytest.importorskip("synox.packs.packwarn")
    assert pw.parse(copy.deepcopy(value)) == core.parse(copy.deepcopy(value))
    assert (pw.MAX_TEXT, pw.MAX_ADVICE, pw.MAX_WARNINGS, pw.CODE_RE.pattern, pw.KNOWN) == \
        (core.MAX_TEXT, core.MAX_ADVICE, core.MAX_WARNINGS, core.CODE_RE.pattern, core.KNOWN)


# ── index.json 的一筆 ─────────────────────────────────────────────────

def test_entry_without_warnings_is_untouched():
    entry = {"name": "x", "title": "t", "line": "l", "kind": "", "sha256": "0" * 64}
    before = json.dumps(entry, ensure_ascii=False, indent=2)
    out = pw.with_warnings(entry, {"name": "x"})
    assert out is entry and json.dumps(out, ensure_ascii=False, indent=2) == before
    assert pw.with_warnings(entry, {"name": "x", "warnings": []}) is entry


def test_entry_with_warnings_puts_them_after_line():
    entry = {"name": "x", "title": "t", "line": "l", "kind": "", "sha256": "0" * 64}
    out = pw.with_warnings(entry, {"warnings": [VENDOR]})
    assert list(out) == ["name", "title", "line", "warnings", "kind", "sha256"]
    assert out["warnings"] == [VENDOR] and "warnings" not in entry


def test_costume_entry_copies_warnings(tmp_path):
    src = os.path.join(ROOT, "costumes", sorted(os.listdir(os.path.join(ROOT, "costumes")))[0])
    folder = tmp_path / os.path.basename(src)
    shutil.copytree(src, folder)
    blob = tmp_path / "x.synoxpack"
    blob.write_bytes(b"fake")
    plain = build_costumes.index_entry(str(folder), str(blob))
    assert "warnings" not in plain
    raw = json.loads((folder / "pack.json").read_text(encoding="utf-8"))
    raw["warnings"] = [CUSTOM]
    (folder / "pack.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    warned = build_costumes.index_entry(str(folder), str(blob))
    assert warned["warnings"] == [CUSTOM] and {k: v for k, v in warned.items() if k != "warnings"} == plain


# ── 假的工具庫 ────────────────────────────────────────────────────────

FAKE = "fake-risky"


def _write_pack(root, pack, **changes):
    path = os.path.join(root, "packs", pack, "pack.json")
    raw = json.loads(_read(path))
    for key, value in changes.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


@pytest.fixture
def lib(tmp_path):
    """notes（有手寫頁、沒有警告）＋假的 fake-risky（沒寫 HTML，帶「官方不保證安全」的警告）。"""
    root = tmp_path / "libs"
    (root / "packs").mkdir(parents=True)
    (root / "docs" / "site").mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "VERSION"), root / "VERSION")
    (root / "catalog.json").write_text(json.dumps({"bundles": {}}), encoding="utf-8")
    shutil.copy(os.path.join(ROOT, "docs", "artifacts.json"), root / "docs" / "artifacts.json")
    shutil.copy(os.path.join(ROOT, "docs", "site-tools.json"), root / "docs" / "site-tools.json")
    shutil.copy(os.path.join(ROOT, "docs", "site", "notes.html"), root / "docs" / "site" / "notes.html")
    for name in ("notes", FAKE):
        shutil.copytree(os.path.join(ROOT, "packs", "notes"), root / "packs" / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    _write_pack(str(root), FAKE, name=FAKE, title="🔎 假搜尋", summary="測試用：帶警告的假工具", version="0.0.1",
                stage="test", group="fakesearch", site={"line": "找網路上的東西（假的）"}, warnings=[VENDOR, CUSTOM])
    return str(root)


def test_index_json_carries_the_warnings(lib, tmp_path):
    result = build_script.build(out_dir=str(tmp_path / "dist"), root=lib)
    assert result["problems"] == [] and result["notices"] == []
    by_name = {e["name"]: e for e in result["index"]["packs"]}
    assert by_name[FAKE]["warnings"] == [VENDOR, CUSTOM]
    assert list(by_name[FAKE]).index("warnings") == list(by_name[FAKE]).index("line") + 1
    assert "warnings" not in by_name["notes"]
    on_disk = json.loads(_read(str(tmp_path / "dist" / "index.json")))
    assert {e["name"]: e.get("warnings") for e in on_disk["packs"]} == {"notes": None, FAKE: [VENDOR, CUSTOM]}


def test_malformed_warnings_do_not_stop_the_build(lib, tmp_path):
    _write_pack(lib, FAKE, warnings=[{"code": "Not OK", "text": "x"}, {"code": "long_one", "text": "字" * 99}])
    assert build_script.build(check_only=True, root=lib)["problems"] == []
    result = build_script.build(out_dir=str(tmp_path / "dist"), root=lib)
    assert result["problems"] == []
    entry = {e["name"]: e for e in result["index"]["packs"]}[FAKE]
    assert [w["code"] for w in entry["warnings"]] == [pw.MALFORMED, "long_one"]
    assert len(entry["warnings"][1]["text"]) == pw.MAX_TEXT
    assert any(n.startswith(f"{FAKE}: ") and "code" in n for n in result["notices"])
    assert any("太長" in n for n in result["notices"])
    assert build_script.build(check_only=True, root=lib)["notices"] == result["notices"]       # --check 也看得到


def test_malformed_notice_is_printed(lib, capsys, monkeypatch):
    _write_pack(lib, FAKE, warnings="官方不保證安全")
    real = build_script.build
    monkeypatch.setattr(build_script, "build", lambda *a, **k: real(check_only=True, root=lib))
    monkeypatch.setattr(build_script.links, "check", lambda *a, **k: [])
    monkeypatch.setattr(build_script.build_site, "check", lambda *a, **k: [])
    assert build_script.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "⚠️ 注意" in out and "warnings 要是清單" in out and pw.MALFORMED in out


# ── 公開頁 ────────────────────────────────────────────────────────────

def _card(page, name):
    start = page.index(f"{name} · v")
    return page[page.rindex("<article", 0, start):page.index("</article>", start)]


def test_pages_show_the_warnings_in_red(lib):
    build_site.write(lib)
    page = _read(os.path.join(lib, "docs", "site", "index.html"))
    card = _card(page, FAKE)
    assert 'class="t-warn"' in card and "⚠️ 作者標的警告" in card
    assert "官方不保證安全" in card and "建議在 docker 或 sandbox 執行" in card and "要系統管理員權限" in card
    assert ".t-warn {" in page and "var(--bad)" in page.split(".t-warn {")[1].split("}")[0]
    assert "t-warn" not in _card(page, "notes")                                   # 沒警告的卡片沒有框

    tool = _read(os.path.join(lib, "docs", "site", f"{FAKE}.html"))
    assert '<section class="warn"' in tool and ".warn {" in tool
    assert "官方不保證安全" in tool and "建議在 docker 或 sandbox 執行" in tool and "vendor_no_security_guarantee" in tool
    # 警告在標題區正下方，指令之前
    assert tool.index('<div class="hero">') < tool.index('<section class="warn"') < tool.index("<h2>怎麼裝</h2>")
    assert build_site.check(lib) == []


def test_no_official_word_in_title_or_badges(lib):
    build_site.write(lib)
    tool = _read(os.path.join(lib, "docs", "site", f"{FAKE}.html"))
    hero = tool[tool.index('<div class="hero">'):tool.index('<section class="warn"')]
    assert "官方" not in hero                                                       # h1、徽章、說明都沒有
    assert "官方" not in tool.split("<title>")[1].split("</title>")[0]
    card = _card(_read(os.path.join(lib, "docs", "site", "index.html")), FAKE)
    head = card[:card.index('class="t-warn"')]
    facts = card[card.index('<dl class="t-facts">'):]
    assert "官方" not in head and "官方" not in facts                                # 標題、標籤都沒有


@pytest.mark.parametrize("field", [{"title": "🔎 官方搜尋"}, {"site": {"name": "官方搜尋"}}, {"site": {"status": "官方推薦"}}])
def test_official_word_with_warnings_is_refused(lib, field):
    _write_pack(lib, FAKE, **field)
    with pytest.raises(ValueError, match="官方"):
        build_site.tools(lib)
    assert any("官方" in p for p in build_script.build(check_only=True, root=lib)["problems"])
    _write_pack(lib, FAKE, warnings=None)                                           # 沒有警告就不管
    build_site.tools(lib)


def test_without_warnings_pages_have_nothing_extra(lib):
    _write_pack(lib, FAKE, warnings=None)
    build_site.write(lib)
    for rel in ("index.html", f"{FAKE}.html"):
        page = _read(os.path.join(lib, "docs", "site", rel))
        assert "warn" not in page.replace("--warn", "").replace("warn-soft", ""), rel
    # 同一份資料：加上警告再拿掉，頁面回到一模一樣
    before = build_site.outputs(lib)
    _write_pack(lib, FAKE, warnings=[VENDOR])
    assert build_site.outputs(lib) != before
    _write_pack(lib, FAKE, warnings=None)
    assert build_site.outputs(lib) == before


def test_repo_pages_and_index_unchanged_without_warnings():
    """這個 repo 現在沒有工具帶警告：公開頁就是改之前產生器的輸出（已 commit 的頁一字不差），index 的每一筆也沒有 warnings。"""
    assert not any(pw.of_pack(t["raw"])[0] for t in build_site.tools())
    assert build_site.check() == []


def test_hand_written_page_must_show_the_warnings(lib):
    _write_pack(lib, "notes", warnings=[CUSTOM])
    problems = build_site.check(lib)
    assert any("notes.html" in p and "要系統管理員權限" in p for p in problems)
    path = os.path.join(lib, "docs", "site", "notes.html")
    with open(path, "a", encoding="utf-8") as f:
        f.write('<p class="warn">⚠️ 要系統管理員權限</p>\n')
    assert not any("notes.html" in p and "警告" in p for p in build_site.check(lib))


def test_page_escapes_warning_text(lib):
    _write_pack(lib, FAKE, warnings=[{"code": "amp", "text": "A & B 的 \"資料\"", "advice": "x < y 時先備份"},
                                     {"code": "markup", "text": "<script>alert(1)</script>"}])
    build_site.write(lib)
    for rel in (f"{FAKE}.html", "index.html"):
        page = _read(os.path.join(lib, "docs", "site", rel))
        assert "A &amp; B 的 &quot;資料&quot;" in page and "x &lt; y 時先備份" in page, rel
        assert "<script" not in page and "alert(1)" not in page, rel     # 夾標記的字換成內建的字，原文不上頁面
