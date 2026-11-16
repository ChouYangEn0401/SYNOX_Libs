# -*- coding: utf-8 -*-
"""📔 日記（packs/diary）：從原本的每日日記程式 1:1 搬來的行為，與 SYNOX 這邊的事（每人一份、密文、檔案傳完就刪）。
跟原本逐字比對的在 tests/test_diary_parity.py。內容全是編出來的範例，不是誰真的日記。"""

import json
import os
import re
from datetime import datetime

import pytest

from conftest import PACKS, load_module

NOV2 = datetime(2026, 11, 2, 21, 0)
TEMPLATES = os.path.join(PACKS, "diary", "content", "templates")
# 2026-11-15 併進來的 20 個新題組裡，預設就出現的 8 個（其餘 12 個內建但預設不出現）
NEW_SHOWN = {"soap-quick", "prayer-acts", "intercession", "sermon-notes", "small-group", "week-with-god",
             "founder-quick", "evening-review"}
NEW_HIDDEN = {"sermon-quick", "serving-review", "soap-full", "oia-study", "lectio", "thanks-to-god", "repent-respond",
              "verse-memory", "experiment-log", "customer-note", "mood-quick", "goal-check"}
ORIGINAL = {"quick-checkin", "three-part", "walk-with-jesus", "scripture-fill", "free-bullets", "examen", "lament",
            "hard-day", "feelings", "worry-sort", "day-review", "become", "peaks-valleys", "capsule", "month-review",
            "founder-daily", "decision-log", "week-review", "jot", "three-lines", "gratitude"}
GENERIC = {"jot", "three-lines", "gratitude", "day-review", "feelings", "hard-day", "worry-sort", "week-review"}


def shipped():
    out = {}
    for name in sorted(os.listdir(TEMPLATES)):
        with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
            out[name[:-5]] = json.load(f)
    return out


@pytest.fixture
def diary(make_bot):
    bot = make_bot("diary", now=NOV2)
    bot.module = bot.modules["diary"]
    bot.grabbed = {}
    real = bot.api.send_document

    def grab(chat_id, path, caption=""):          # 讀下傳出去的檔（之後 pack 會把它刪掉）
        with open(path, encoding="utf-8") as f:
            bot.grabbed[os.path.basename(path)] = f.read()
        return real(chat_id, path, caption=caption)
    bot.api.send_document = grab
    return bot


def db(bot, owner, key=None):
    return bot.packs["diary"].store.of(owner).load({}, key=key)


def ids_of(bot, owner):
    bot.say(owner, "/diary_templates")
    return {m.group(1) for m in re.finditer(r"（([a-z0-9-]+)）約", bot.last(owner))}


# ── 內建題組 ─────────────────────────────────────────────────────────

def test_every_shipped_set_is_complete():
    """每個內建題組（原本的 21 個＋新的 20 個）：檔名＝id、分類合法、挑題組要的鍵都有、每一題都寫了怎麼填。"""
    m = load_module("diary")
    sets = shipped()
    assert set(sets) == ORIGINAL | NEW_SHOWN | NEW_HIDDEN and len(sets) == 41
    for tid, raw in sets.items():
        t = m.parse_template(raw)
        assert t.id == tid and t.name and t.when and t.minutes > 0 and t.group, tid
        assert t.group in ("與神同行", "心情與難處", "成為什麼樣的人", "工作與創業", "簡單記一下"), (tid, t.group)
        assert t.flow and t.structure in m.STRUCTURES and t.reflection in m.REFLECTIONS, tid
        ids = [f.id for f in t.field_map().values()]
        assert len(ids) == len(set(ids)), tid
        for f in t.field_map().values():
            assert f.ai_hint, (tid, f.id)
            if f.kind in ("choice", "scale", "multi_choice"):
                assert f.options, (tid, f.id)
            if f.kind == "fill":
                assert f.pattern and "{}" in f.pattern, (tid, f.id)
        assert bool(raw.get("default_hidden")) == (tid in NEW_HIDDEN), tid


def test_new_person_sees_the_default_sets(diary, owner):
    """沒跑 setup init：原本的 21 個＋新的 8 個；另外 12 個內建、預設不出現，/diary_setup 列得出來、拿得出來。"""
    assert ids_of(diary, owner) == ORIGINAL | NEW_SHOWN
    diary.say(owner, "/diary_setup")
    reply = diary.last(owner)
    assert "還沒拿出來的內建題組" in reply and all(f"（{t}）" in reply for t in NEW_HIDDEN)
    diary.say(owner, "/diary_tpl_restore lectio")
    assert "放回了內建的" in diary.last(owner)
    assert ids_of(diary, owner) == ORIGINAL | NEW_SHOWN | {"lectio"}


def test_setup_init_generic_then_devotional_only_adds(diary, owner):
    """原 setup init：generic 只裝不談信仰的八個；之後 devotional 不會把刻意沒裝的倒回來（只加不刪），要的話自己拿。"""
    diary.say(owner, "/diary_setup_init generic")
    assert "裝了 8 個題組" in diary.last(owner)
    assert ids_of(diary, owner) == GENERIC
    diary.say(owner, "/diary_settings suggest_faith")
    assert '"never"' in diary.last(owner)
    diary.say(owner, "/diary_setup_init devotional")
    assert ids_of(diary, owner) == GENERIC
    diary.say(owner, "/diary_tpl_restore lament")
    assert "lament" in ids_of(diary, owner)


def test_setup_init_devotional_on_a_new_person(diary, owner):
    diary.say(owner, "/diary_setup_init devotional")
    assert ids_of(diary, owner) == ORIGINAL | NEW_SHOWN
    diary.say(owner, "/diary_settings suggest_faith")
    assert '"prefer"' in diary.last(owner)


def test_template_and_filters(diary, owner):
    diary.say(owner, "/diary_template three-lines")
    assert "三行日記（three-lines）" in diary.last(owner)
    diary.say(owner, "/diary_templates reflection=none,reflection")
    reply = diary.last(owner)
    assert "（three-lines）" in reply and "（lament）" not in reply
    diary.say(owner, "/diary_templates structure=nope")
    assert diary.last(owner).startswith("❌ 沒有這種 structure")
    diary.say(owner, "/diary_categories")
    assert "structure：" in diary.last(owner) and "groups：" in diary.last(owner)
    diary.say(owner, "/diary_suggest 今天跟客戶開會被罵，很擔心案子")
    assert diary.last(owner).startswith("1. ") and "看到的線索" in diary.last(owner)


# ── 寫入 ─────────────────────────────────────────────────────────────

def test_jot_and_put(diary, owner):
    diary.say(owner, "/diary 今天很好")
    assert "記進「一則一則記下來」" in diary.last(owner)
    diary.say(owner, "/diary 晚上下雨")
    assert "現在 2 則" in diary.last(owner)
    rows = db(diary, owner)["entries"]
    assert len(rows) == 1 and rows[0]["answers"]["notes"] == ["今天很好", "晚上下雨"]
    diary.say(owner, "/diary_put yesterday jot 昨天忘了記的事")
    assert "2026-11-01" in diary.last(owner)
    diary.say(owner, '/diary_put 2026-11-02 three-lines answers={"answers": {"fact": "下雨", "nope": 1}, "title": "雨天"}')
    reply = diary.last(owner)
    assert "新增了 2026-11-02 的「三行日記」" in reply and "1 題略過" in reply
    diary.say(owner, "/diary_show")
    reply = diary.last(owner)
    assert "今天很好" in reply and "雨天" in reply and "下雨" in reply
    diary.say(owner, "/diary_list")
    assert diary.last(owner).count("2026-11-0") == 3


def test_put_ai_keeps_the_original_and_never_replaces_the_author(diary, owner):
    diary.say(owner, '/diary_put today day-review answers={"answers": {"happened": "作者自己寫的"}}')
    diary.say(owner, '/diary_put_ai today day-review answers={"answers": {"happened": "AI 版"}}')
    assert "一定要附作者原文" in diary.last(owner)
    payload = {"answers": {"happened": "AI 想蓋掉", "learned": "AI 整理的學到"}, "source_text": "作者的原話"}
    diary.say(owner, "/diary_put_ai today day-review answers=" + json.dumps(payload, ensure_ascii=False))
    reply = diary.last(owner)
    assert "（標為 AI 整理、附作者原文）" in reply and "1 題原本就有寫、沒有動" in reply
    diary.say(owner, "/diary_show today day-review")
    reply = diary.last(owner)
    assert "作者自己寫的" in reply and "（AI 整理）" in reply and "作者的原話" in reply and "AI 想蓋掉" not in reply


def test_overwrite_only_the_named_question(diary, owner):
    diary.say(owner, '/diary_put today three-lines answers={"answers": {"fact": "一", "feel": "二"}}')
    diary.say(owner, '/diary_overwrite today three-lines feel user {"answers": {"fact": "不該換", "feel": "換了"}}')
    reply = diary.last(owner)
    assert "其中 1 題換掉了原本的答案" in reply and "1 題原本就有寫、沒有動" in reply
    answers = db(diary, owner)["entries"][0]["answers"]
    assert answers["fact"] == "一" and answers["feel"] == "換了"
    diary.say(owner, '/diary_overwrite today three-lines ghost user {"answers": {}}')
    assert "這個題組沒有：ghost" in diary.last(owner)


# ── 讀、改、刪、歷史、垃圾桶 ─────────────────────────────────────────

def test_switch_keeps_every_answer_and_title(diary, owner):
    diary.say(owner, '/diary_put today three-lines answers={"answers": {"fact": "一件事"}}')
    diary.say(owner, "/diary_switch 1 gratitude")
    assert "原本有 1 題這個題組沒有，內容還留著" in diary.last(owner)
    diary.say(owner, "/diary_show 1")
    assert "另外還留著 1 題" in diary.last(owner)
    diary.say(owner, "/diary_switch 1 three-lines")
    diary.say(owner, "/diary_show 1")
    assert "一件事" in diary.last(owner)
    diary.say(owner, "/diary_title 1 雨天的一件事")
    assert "標題改成「雨天的一件事」" in diary.last(owner)


def test_delete_goes_to_trash_and_comes_back(diary, owner):
    diary.say(owner, "/diary 會被刪掉的一則")
    diary.say(owner, "/diary_rm 1")
    assert "垃圾桶" in diary.last(owner) and db(diary, owner)["entries"] == []
    diary.say(owner, "/diary_trash")
    assert "#1" in diary.last(owner)
    diary.say(owner, "/diary_undelete 1")
    assert "救回來了" in diary.last(owner)
    diary.say(owner, "/diary_show 1")
    assert "會被刪掉的一則" in diary.last(owner)
    diary.say(owner, "/diary_purge 1")
    assert "還沒刪的日記不能永久刪除" in diary.last(owner)
    diary.say(owner, "/diary_rm 1")
    diary.say(owner, "/diary_purge 1")
    assert "永久刪除" in diary.last(owner) and db(diary, owner)["revisions"] == []
    diary.say(owner, "/diary_undelete 1")
    assert "垃圾桶裡沒有" in diary.last(owner)


def test_a_shrinking_save_keeps_the_old_version(diary, owner):
    diary.say(owner, '/diary_put today three-lines answers={"answers": {"fact": "很長的一段原本的字"}}')
    diary.say(owner, '/diary_overwrite today three-lines fact user {"answers": {"fact": "短"}}')
    diary.say(owner, "/diary_history 1")
    reply = diary.last(owner)
    assert "版本 1" in reply and "改之前" in reply
    diary.say(owner, "/diary_restore 1")
    assert "放回了版本 1" in diary.last(owner)
    assert db(diary, owner)["entries"][0]["answers"]["fact"] == "很長的一段原本的字"
    diary.say(owner, "/diary_history 1")
    assert "還原之前" in diary.last(owner)


def test_tidy_only_removes_blank_entries(diary, owner):
    diary.say(owner, "/diary 有字")
    book = db(diary, owner)
    book["entries"].append({**book["entries"][0], "id": 9, "answers": {}, "title": ""})
    diary.packs["diary"].store.of(owner).save(book)
    diary.say(owner, "/diary_list")
    assert "空白" in diary.last(owner)
    diary.say(owner, "/diary_tidy")
    assert "清掉了 1 筆" in diary.last(owner)
    assert [r["id"] for r in db(diary, owner)["entries"]] == [1]


# ── 備份 ─────────────────────────────────────────────────────────────

def test_backups(diary, owner):
    diary.say(owner, "/diary 第一則")
    diary.say(owner, "/diary_backups")
    assert "備份 0 份" in diary.last(owner)
    diary.say(owner, "/diary 第二則")                       # 有日記之後寫一次＝滾動備份（每 6 小時一份）
    diary.say(owner, "/diary_backup")
    assert "備份好了：manual-journal-20261102-210000-manual" in diary.last(owner)
    diary.say(owner, "/diary_backups")
    assert "備份 2 份" in diary.last(owner)
    diary.say(owner, "/diary_rm 1")
    diary.say(owner, "/diary_backup_restore manual-journal-20261102-210000-manual")
    assert "還原成" in diary.last(owner)
    assert db(diary, owner)["entries"][0]["answers"]["notes"] == ["第一則", "第二則"]
    diary.say(owner, "/diary_backups")
    assert "before-restore" in diary.last(owner)
    diary.say(owner, "/diary_backup_rm manual-journal-20261102-210000-manual")
    assert "刪掉了備份" in diary.last(owner)
    diary.say(owner, "/diary_backup_restore ../x")
    assert diary.last(owner).startswith("❌")


def test_automatic_backups_are_a_ring_of_twenty(diary, owner):
    m = diary.module
    book = m.Book(diary.packs["diary"].store.of(owner), m.Content(os.path.join(PACKS, "diary", "content")))
    book.create_entry(entry_date=NOV2.date(), template_id="jot", answers={"notes": ["x"]})
    for i in range(25):
        m.now = lambda i=i: datetime(2026, 11, 2, 0, 0, i)
        book.snapshot("auto")
    autos = [b for b in book.setup["backups"] if b["automatic"]]
    assert len(autos) == 20 and len({b["slot"] for b in autos}) == 20
    assert min(b["created_at"] for b in autos) == "2026-11-02T00:00:05"


# ── 輸出 ─────────────────────────────────────────────────────────────

def test_export_sends_a_page_and_a_card_then_deletes_them(diary, owner):
    diary.say(owner, '/diary_put today three-lines answers={"answers": {"fact": "下雨 & <風>"}, "title": "雨天"}')
    diary.say(owner, "/diary_export today three-lines")
    sent = [f for f in diary.api.files if f[0] == "document"]
    assert [os.path.basename(f[2]) for f in sent] == ["2026-11-02-three-lines-1.html", "2026-11-02-three-lines-1.svg"]
    assert all(not os.path.exists(f[2]) for f in sent)                   # 傳完就刪（日記不留在硬碟上）
    page, card = diary.grabbed["2026-11-02-three-lines-1.html"], diary.grabbed["2026-11-02-three-lines-1.svg"]
    assert page.startswith("<!doctype html>") and "下雨 &amp; &lt;風&gt;" in page and "雨天" in page
    assert card.startswith('<svg xmlns="http://www.w3.org/2000/svg" width="1080"') and "下雨 &amp; &lt;風&gt;" in card
    assert "輸出了 2026-11-02 的「三行日記」" in diary.last(owner)
    diary.say(owner, "/diary_export id=1 format=html layout=ledger theme=midnight gift=yes gift_message=給你")
    page = diary.grabbed["2026-11-02-three-lines-1.html"]
    assert 'class="cover"' in page and "給你" in page and "--backdrop:#14161c" in page and 'class="cols"' in page
    diary.say(owner, "/diary_export id=1 format=card width=1500 paper=grid")
    assert 'width="1500"' in diary.grabbed["2026-11-02-three-lines-1.svg"]
    diary.say(owner, "/diary_export id=1 layout=nope")
    assert "沒有這個版型" in diary.last(owner)
    diary.say(owner, "/diary_export 2026-01-01 three-lines")
    assert "還沒有用「three-lines」寫的日記" in diary.last(owner)


def test_layouts_can_be_removed_and_put_back(diary, owner):
    diary.say(owner, "/diary_layouts")
    assert "信箋 Letter（letter）" in diary.last(owner) and "明信片 Postcard（postcard）" in diary.last(owner)
    diary.say(owner, "/diary_layout_rm postcard")
    diary.say(owner, "/diary_layouts")
    assert "postcard" not in diary.last(owner)
    diary.say(owner, '/diary_put today three-lines answers={"answers": {"fact": "x"}}')
    diary.say(owner, "/diary_export id=1 format=html")                   # three-lines 預設 postcard → 退回 letter
    assert 'class="paper"' in diary.grabbed["2026-11-02-three-lines-1.html"]
    diary.say(owner, "/diary_layout_restore postcard")
    diary.say(owner, "/diary_layouts")
    assert "postcard" in diary.last(owner)
    diary.say(owner, "/diary_themes")
    assert "夜禱 Midnight（midnight）" in diary.last(owner)


def test_image_prompt(diary, owner):
    diary.say(owner, '/diary_put today three-lines answers={"answers": {"fact": "傍晚在河邊散步"}}')
    diary.say(owner, "/diary_image_prompt today three-lines style=ink")
    reply = diary.last(owner)
    assert "水墨" in reply and "傍晚在河邊散步" in reply and "east-asian ink wash" in reply


# ── 題組的增刪 ─────────────────────────────────────────────────────

MINE = {"id": "my-three", "name": "我的每日三問", "group": "簡單記一下", "order": 50,
        "sections": [{"id": "night", "title": "睡前", "color": "#2456b8", "icon": "☾", "fields": [
            {"id": "best", "label": "今天最好的一刻", "kind": "short_text"},
            {"id": "mood", "label": "今天整體", "kind": "scale",
             "options": [{"id": "good", "label": "好"}, {"id": "ok", "label": "普通"}]}]}]}


def test_save_delete_and_recover_a_template(diary, owner):
    diary.say(owner, "/diary_tpl_save " + json.dumps(MINE, ensure_ascii=False))
    assert "新增了題組「我的每日三問」（my-three，2 題）" in diary.last(owner)
    diary.say(owner, '/diary_put today my-three answers={"answers": {"best": "吃到熱湯", "mood": "好"}}')
    diary.say(owner, "/diary_tpl_rm my-three")
    assert "有 1 篇日記還在用這個題組" in diary.last(owner)
    diary.say(owner, "/diary_tpl_rm my-three force=yes")
    assert "刪掉了題組 my-three" in diary.last(owner)
    diary.say(owner, "/diary_show 1")
    reply = diary.last(owner)
    assert "（題組已刪除）" in reply and "吃到熱湯" in reply                    # 日記退成「保留下來的內容」，字都在
    diary.say(owner, '/diary_tpl_save {"id": "bad", "name": "壞", "reflection": "x"}')
    assert "reflection 只能是" in diary.last(owner)
    diary.say(owner, "/diary_tpl_rm jot force=yes")
    diary.say(owner, "/diary 還記得進去嗎")
    assert "找不到題組：jot" in diary.last(owner)
    diary.say(owner, "/diary_tpl_restore jot")
    diary.say(owner, "/diary 還記得進去嗎")
    assert "記進" in diary.last(owner)


# ── 設定 ─────────────────────────────────────────────────────────────

def test_settings(diary, owner):
    diary.say(owner, "/diary_settings")
    reply = diary.last(owner)
    assert all(k in reply for k in ("palette", "day_start_hour", "suggest_faith", "card_width"))
    assert "export_dir" not in reply
    diary.say(owner, "/diary_settings_set day_start_hour 25")
    assert "0 到 23" in diary.last(owner)
    diary.say(owner, "/diary_settings_set theme nope")
    assert "紙張顏色只有" in diary.last(owner)
    diary.say(owner, "/diary_settings_set day_start_hour 22")           # 晚上九點還算「昨天」
    assert "改好了" in diary.last(owner)
    diary.say(owner, "/diary 熬夜寫的")
    assert "2026-11-01" in diary.last(owner)
    diary.say(owner, "/diary_settings_set palette default")
    assert "#22252a" in diary.last(owner)
    diary.say(owner, '/diary_settings_set palette [{"hex": "#ABC", "name": "淺"}, {"hex": "nope"}]')
    assert db(diary, owner, "setup")["preferences"]["palette"] == [{"hex": "#aabbcc", "name": "淺"}]
    diary.say(owner, "/diary_settings_set layout ledger")
    diary.say(owner, "/diary_settings_set theme dawn")
    diary.say(owner, '/diary_put today three-lines answers={"answers": {"fact": "x"}}')
    row = next(r for r in db(diary, owner)["entries"] if r["template_id"] == "three-lines")
    assert (row["layout"], row["theme"]) == ("ledger", "dawn")            # 新日記套用預設（use_as_default）


# ── SYNOX：權限、每人一份、密文 ─────────────────────────────────────

def test_dangerous_commands_and_ai_tools(synox_bot, owner):
    from isdtelekit.core.registry import DANGER, READ, WRITE
    bot = synox_bot("diary")
    reg = bot.app.registry
    effects = {n: reg.node(f"diary.{n}").effect for n in (
        "jot", "put", "put_ai", "overwrite", "rm", "purge", "backup_restore", "backup_rm", "tpl_rm", "layout_rm",
        "import_replace", "show", "list", "export")}
    assert effects == {"jot": WRITE, "put": WRITE, "put_ai": WRITE, "overwrite": DANGER, "rm": DANGER,
                       "purge": DANGER, "backup_restore": DANGER, "backup_rm": DANGER, "tpl_rm": DANGER,
                       "layout_rm": DANGER, "import_replace": DANGER, "show": READ, "list": READ, "export": READ}
    for name in ("export_all", "import", "import_replace"):
        assert reg.node(f"diary.{name}").ai is False                       # 整本日記：AI 碰不到


def test_data_is_encrypted_and_export_all_import_round_trip(synox_bot, owner):
    bot = synox_bot("diary")
    got = {}
    real = bot.api.send_document

    def grab(chat_id, path, caption=""):
        with open(path, encoding="utf-8") as f:
            got[os.path.basename(path)] = f.read()
        return real(chat_id, path, caption=caption)
    bot.api.send_document = grab
    bot.say(owner, "/diary 祕密的一則日記")
    bot.say(owner, "/diary_tpl_save " + json.dumps(MINE, ensure_ascii=False))
    pack = next(p for p in bot.app.extras["synox_packs"] if p.name == "diary")
    for key in (None, "setup"):
        with open(pack.store.of(owner).file(key), "rb") as f:
            raw = f.read()
        assert "祕密".encode() not in raw and "我的每日三問".encode() not in raw
    bot.say(owner, "/diary_export_all")
    name = next(n for n in got if n.startswith("journal-export-"))
    doc = json.loads(got[name])
    assert doc["format"] == "synox.diary.export" and doc["version"] == 1 and len(doc["entries"]) == 1
    assert any(t["id"] == "my-three" for t in doc["templates"]) and len(doc["layouts"]) == 4
    bot.say(owner, "/diary_import")
    assert "找不到要匯入的檔" in bot.last(owner)
    doc["entries"].append({**doc["entries"][0], "entry_date": "2026-10-01", "id": 99})
    with open(pack.path_of(owner, "import.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False))
    bot.say(owner, "/diary_import")
    assert "匯入了 1 篇日記（跳過 1 篇" in bot.last(owner)
    assert not os.path.exists(pack.path_of(owner, "import.json"))         # 匯入完就刪
    bot.say(owner, "/diary_list")
    assert "2026-10-01" in bot.last(owner)


# 原本的每日日記程式匯出的檔用的 format 名字（拆開寫：repo 裡不出現原程式的名字）
OLD_FORMAT = "fast" + "daily" + "journal" + "writer" + ".export"


def test_export_format_is_neutral_and_old_files_are_recognised_by_shape():
    """匯出的 format 是 synox.diary.export；原本程式的舊檔用結構認（….export、version 1、至少一個 list），其他的不收。"""
    m = load_module("diary")
    assert m.EXPORT_FORMAT == "synox.diary.export"
    assert m.is_export({"format": "synox.diary.export", "version": 1, "entries": []})
    assert m.is_export({"format": OLD_FORMAT, "version": 1, "entries": [], "templates": []})
    assert m.is_export({"format": OLD_FORMAT, "version": 1, "templates": [{"id": "x"}]})       # 只放題組的短檔
    for bad in (None, [], {"format": "something.else", "version": 1, "entries": []},
                {"format": OLD_FORMAT, "version": 2, "entries": []},                          # 不認得的版本
                {"format": OLD_FORMAT, "version": 1},                                          # 沒有任何內容
                {"format": OLD_FORMAT, "version": 1, "entries": "not a list"}):
        assert not m.is_export(bad), bad


def test_import_takes_an_old_export_file_and_the_error_names_only_the_new_format(synox_bot, owner):
    """舊程式匯出的檔照樣匯得進來（差別只有 format 的名字）；不認得的檔，錯誤訊息只寫新的 format 名字。"""
    bot = synox_bot("diary")
    got = {}
    real = bot.api.send_document

    def grab(chat_id, path, caption=""):
        with open(path, encoding="utf-8") as f:
            got[os.path.basename(path)] = f.read()
        return real(chat_id, path, caption=caption)
    bot.api.send_document = grab
    bot.say(owner, "/diary 一則日記")
    bot.say(owner, "/diary_export_all")
    doc = json.loads(got[next(n for n in got if n.startswith("journal-export-"))])
    pack = next(p for p in bot.app.extras["synox_packs"] if p.name == "diary")
    old = {**doc, "format": OLD_FORMAT, "entries": [{**doc["entries"][0], "entry_date": "2026-09-30", "id": 7}]}
    with open(pack.path_of(owner, "import.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(old, ensure_ascii=False))
    bot.say(owner, "/diary_import")
    assert "匯入了 1 篇日記" in bot.last(owner), bot.last(owner)
    bot.say(owner, "/diary_list")
    assert "2026-09-30" in bot.last(owner)
    with open(pack.path_of(owner, "import.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"format": "something.else", "version": 1, "entries": []}))
    bot.say(owner, "/diary_import")
    reply = bot.last(owner)
    assert "synox.diary.export" in reply and "writer" not in reply.lower(), reply


def test_each_person_has_their_own_diary(synox_bot):
    """伺服器版：A 的日記、題組、設定、備份、垃圾桶、輸出的檔，B 從任何指令都看不到；落地檔是密文。"""
    from isdtelekit.users.roles import BasicRoles as R
    bot = synox_bot("diary", mode="server")
    bot.make(77, R.MEMBER, "a")
    bot.make(78, R.MEMBER, "b")
    files = []
    real = bot.api.send_document

    def grab(chat_id, path, caption=""):
        with open(path, encoding="utf-8") as f:
            files.append((chat_id, f.read()))
        return real(chat_id, path, caption=caption)
    bot.api.send_document = grab
    mine = dict(MINE, id="secret-set", name="祕密題組A")
    bot.say(77, "/diary 祕密日記A", chat_id=77)
    bot.say(77, '/diary_put today three-lines answers={"answers": {"fact": "祕密事件A"}, "title": "祕密標題A"}', chat_id=77)
    bot.say(77, "/diary_tpl_save " + json.dumps(mine, ensure_ascii=False), chat_id=77)
    bot.say(77, "/diary_backup", chat_id=77)
    bot.say(77, "/diary_rm 2", chat_id=77)
    bot.say(77, "/diary_export today jot", chat_id=77)
    bot.say(77, "/diary_settings_set day_start_hour 23", chat_id=77)
    assert any("祕密日記A" in text for chat, text in files if chat == 77)
    for text in ("/diary 我的", "/diary_list", "/diary_show today", "/diary_show 1", "/diary_show 2", "/diary_trash",
                 "/diary_backups", "/diary_history 1", "/diary_templates", "/diary_template secret-set",
                 "/diary_settings day_start_hour", "/diary_export today jot", "/diary_export id=2",
                 "/diary_undelete 2", "/diary_setup"):
        bot.say(78, text, chat_id=78)
    b_view = "\n".join(bot.texts(chat_id=78)) + "".join(t for chat, t in files if chat == 78)
    assert not [s for s in ("祕密日記A", "祕密事件A", "祕密標題A", "祕密題組A") if s in b_view], b_view
    assert '"day_start_hour": 23' not in b_view and "day_start_hour = 23" not in b_view
    pack = next(p for p in bot.app.extras["synox_packs"] if p.name == "diary")
    for key in (None, "setup"):
        with open(pack.store.of(77).file(key), "rb") as f:
            raw = f.read()
        assert "祕密".encode() not in raw
    assert pack.store.of(78).load({})["entries"][0]["answers"]["notes"] == ["我的"]
    assert os.path.dirname(pack.store.of(77).file()) != os.path.dirname(pack.store.of(78).file())
