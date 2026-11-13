# -*- coding: utf-8 -*-
"""💰 記帳（從 SYNOX tests/test_packs.py、tests/test_money_isolation.py 搬來；用真的 SYNOX 載入這裡的 packs/money）。"""
from isdtelekit.users.roles import BasicRoles


def test_money_budget_warning(synox_bot, owner):
    bot = synox_bot("money")
    bot.say(owner, "/money_budget 100")
    bot.say(owner, "/money 60 午餐")
    assert "預算還剩 40 元" in bot.last(owner)
    bot.say(owner, "/money 50 晚餐 和朋友")
    assert "這一筆讓這個月超過預算了" in bot.last(owner) and "（和朋友）" in bot.last(owner)
    bot.say(owner, "/money_month")
    assert "共 110 元" in bot.last(owner) and "超過 10 元" in bot.last(owner)
    bot.say(owner, "/money_list 1")
    assert "晚餐 50 元" in bot.last(owner)
    bot.say(owner, "/money abc 午餐")
    assert "要是數字" in bot.last(owner)


def test_first_entries_do_not_leak_between_users(synox_bot):
    """伺服器版：A 的第一筆帳絕對不能出現在 B 的帳裡（2026-11-10 的淺拷貝 bug 的回歸測試）。"""
    bot = synox_bot("money", mode="server")
    bot.make(77, BasicRoles.MEMBER, "a")
    bot.make(78, BasicRoles.MEMBER, "b")
    bot.say(77, "/money 50 咖啡 祕密備註A", chat_id=77)
    bot.say(78, "/money 20 茶", chat_id=78)
    bot.say(78, "/money_list", chat_id=78)
    b_view = "\n".join(bot.texts(chat_id=78))
    assert "咖啡" not in b_view and "祕密備註A" not in b_view and "茶" in b_view
    bot.say(77, "/money_list", chat_id=77)
    a_view = "\n".join(bot.texts(chat_id=77))
    assert "茶" not in a_view and "咖啡" in a_view


def test_money_data_is_encrypted_and_panel_summary(synox_bot, owner):
    bot = synox_bot("money")
    bot.say(owner, "/money 120 午餐 祕密")
    vault = bot.app.extras["vault"]
    assert "祕密".encode() not in open(vault.path("money"), "rb").read()
    pack = next(p for p in bot.app.extras["synox_packs"] if p.name == "money")
    assert "summary" in pack.web_routes


# ---- 0.2.0：資料第 2 版（ADR-0002） ----
from datetime import datetime  # noqa: E402


def _ts(y, m, d, h=12):
    return int(datetime(y, m, d, h).timestamp())


OLD = {"entries": [{"amount": 50.0, "category": "咖啡", "memo": "", "at": _ts(2026, 11, 2)},
                   {"amount": 80.0, "category": "午餐", "memo": "假資料", "at": _ts(2026, 11, 3)}],
       "budget": 1000}


def _data(bot, user=None):
    from conftest import OWNER_ID
    return bot.packs["money"].store.of(user or OWNER_ID).load(None)


def test_old_data_upgrades_on_read_and_saves_as_schema_2(make_bot, owner):
    bot = make_bot("money")
    bot.packs["money"].store.save({**OLD, "entries": [dict(e) for e in OLD["entries"]]})
    bot.say(owner, "/money_list")
    assert "#1 11/02  咖啡 50 元" in bot.last(owner) and "#2 11/03  午餐 80 元（假資料）" in bot.last(owner)
    assert "schema" not in _data(bot)                              # 只是讀：不寫回
    bot.say(owner, "/money 20 茶")
    assert "#3" in bot.last(owner)
    data = _data(bot)
    assert data["schema"] == 2 and data["next_id"] == 4 and data["budget"] == 1000
    first = data["entries"][0]
    assert first == {"amount": 50.0, "category": "咖啡", "memo": "", "at": OLD["entries"][0]["at"],
                     "id": 1, "kind": "expense", "date": "2026-11-02"}   # 舊欄位都留著（退回 0.1.1 也讀得懂）
    bot.say(owner, "/money_month")
    assert "共 150 元" in bot.last(owner)


def test_upgrade_is_idempotent_and_keeps_ids(make_bot):
    module = make_bot("money").modules["money"]
    once = module.upgrade({**OLD, "entries": [dict(e) for e in OLD["entries"]]})
    twice = module.upgrade({**once, "entries": [dict(e) for e in once["entries"]]})
    assert once == twice and [e["id"] for e in twice["entries"]] == [1, 2]
    newer = module.upgrade({"schema": 3, "entries": [], "next_id": 9})
    assert newer["schema"] == 3 and newer["next_id"] == 9                 # 比這版新的資料不降版


def test_backdated_entry_lands_in_its_month(make_bot, owner):
    bot = make_bot("money")                                            # 現在＝2026-11-09
    bot.say(owner, "/money 30 早餐 date=昨天")
    assert "記在 2026-11-08" in bot.last(owner)
    bot.say(owner, "/money 40 晚餐 date=2026-10-31")
    bot.say(owner, "/money_month")
    assert "共 30 元" in bot.last(owner)
    bot.say(owner, "/money_month 2026-10")
    assert "2026-10 共 40 元" in bot.last(owner)
    bot.say(owner, "/money 40 晚餐 date=13/40")
    assert "看不懂" in bot.last(owner)


def test_old_data_in_real_vault_upgrades(synox_bot, owner):
    bot = synox_bot("money")
    pack = next(p for p in bot.app.extras["synox_packs"] if p.name == "money")
    pack.store.of(owner).save({**OLD, "entries": [dict(e) for e in OLD["entries"]]})
    bot.say(owner, "/money 20 茶")
    assert "#3" in bot.last(owner)
    assert pack.store.of(owner).load(None)["schema"] == 2


# ---- 0.2.0：類別是使用者自己的資料 ----

def test_categories_add_list_update_remove(make_bot, owner):
    bot = make_bot("money")
    bot.say(owner, "/money_cats")
    assert "還沒有自己的類別" in bot.last(owner)
    bot.say(owner, "/money_cat_add 飲料 食 keywords=咖啡,拿鐵")
    assert "新增了類別「飲料」" in bot.last(owner) and "咖啡、拿鐵" in bot.last(owner)
    bot.say(owner, "/money_cat_add 飲料 keywords=拿鐵、奶茶")               # 同名＝更新，不重複
    assert "更新了" in bot.last(owner)
    bot.say(owner, "/money_cat_add 交通")
    bot.say(owner, "/money_cats")
    assert "（2 個）" in bot.last(owner) and "食：飲料" in bot.last(owner) and "交通：交通" in bot.last(owner)
    bot.say(owner, "/money_cats 飲料")
    assert "咖啡、拿鐵、奶茶" in bot.last(owner)
    assert len(_data(bot)["categories"]) == 2
    bot.say(owner, "/money 60 飲料")
    bot.say(owner, "/money_cat_rm 飲料")
    assert "刪掉類別「飲料」" in bot.last(owner)
    bot.say(owner, "/money_cat_rm 飲料")
    assert "沒有「飲料」" in bot.last(owner)
    bot.say(owner, "/money_list")
    assert "飲料 60 元" in bot.last(owner)                                 # 已經記的帳不動


def test_free_text_category_still_records(make_bot, owner):
    bot = make_bot("money")
    bot.say(owner, "/money_cat_add Coffee")
    bot.say(owner, "/money 50 coffee")                                     # 不分大小寫，照存著的寫法
    assert "Coffee 50 元" in bot.last(owner)
    bot.say(owner, "/money 99 寵物 飼料")                                   # 不在清單裡也照記
    assert "寵物 99 元（飼料）" in bot.last(owner)


def test_categories_are_per_user_on_server(synox_bot):
    from isdtelekit.users.roles import BasicRoles as R
    bot = synox_bot("money", mode="server")
    bot.make(77, R.MEMBER, "a")
    bot.make(78, R.MEMBER, "b")
    bot.say(77, "/money_cat_add 祕密類別A", chat_id=77)
    bot.say(78, "/money_cats", chat_id=78)
    assert "祕密類別A" not in bot.last(78) and "還沒有自己的類別" in bot.last(78)


# ---- 0.2.0：範本（預設空，下指令才載入） ----

def test_templates_load_only_on_request_and_twice_adds_nothing(make_bot, owner):
    bot = make_bot("money")
    bot.say(owner, "/money 50 咖啡")
    assert "/money_template" in bot.last(owner)                            # 第一筆提示一次
    assert _data(bot)["categories"] == []                                  # 沒有自己載入
    bot.say(owner, "/money 60 茶")
    assert "/money_template" not in bot.last(owner)
    bot.say(owner, "/money_template")
    assert "基本" in bot.last(owner) and "捐贈" in bot.last(owner) and _data(bot)["categories"] == []
    bot.say(owner, "/money_template 基本")
    assert "新增 12 個類別" in bot.last(owner)
    count = len(_data(bot)["categories"])
    bot.say(owner, "/money_template basic")                               # 英文代號也行；第二次不會多
    assert "新增 0 個類別" in bot.last(owner) and "已經有的 12 個沒動" in bot.last(owner)
    assert len(_data(bot)["categories"]) == count
    bot.say(owner, "/money_template 不存在")
    assert "沒有「不存在」這個範本" in bot.last(owner)


def test_template_does_not_overwrite_user_edits(make_bot, owner):
    bot = make_bot("money")
    bot.say(owner, "/money_cat_add 午餐 外食 keywords=自助餐")
    bot.say(owner, "/money_template 基本")
    assert "新增 11 個類別" in bot.last(owner) and "午餐" in bot.last(owner).split("沒動")[1]
    lunch = next(c for c in _data(bot)["categories"] if c["name"] == "午餐")
    assert lunch["group"] == "外食" and lunch["keywords"] == ["自助餐"]


def test_template_contents_are_valid_and_neutral(make_bot):
    import os
    from conftest import ROOT
    module = make_bot("money").modules["money"]
    names = [c[0] for t in module.TEMPLATES for c in t[3]]
    assert len(names) == len(set(names))                                  # 範本之間不撞名
    assert {t[0] for t in module.TEMPLATES} == {"basic", "bills", "saving", "giving", "income"}
    for key, title, about, items in module.TEMPLATES:
        for name, group, kind, words in items:
            assert kind in module.KIND_LABELS and name and group, (key, name)
    giving = {c[0] for c in module.find_template("捐贈")[3]}
    assert {"捐款", "十一奉獻"} <= giving                                 # 作者刻意保留，放在選配的「捐贈」
    saving = {c[0]: c[2] for c in module.find_template("存錢與投資")[3]}
    assert saving["存錢"] == saving["投資"] == saving["還款"] == saving["繳卡費"] == "transfer"
    assert "信用卡" not in names                                          # 信用卡是付款方式，不是類別
    text = " ".join(" ".join(c) for t in module.TEMPLATES for c in t[3])
    brands = ("7-11", "全家", "萊爾富", "星巴克", "路易莎", "麥當勞", "肯德基", "全聯", "家樂福", "好市多", "50嵐",
              "Netflix", "Spotify", "YouTube", "Uber", "foodpanda", "中華電信", "台灣大哥大", "遠傳", "悠遊卡", "一卡通")
    assert not [b for b in brands if b.lower() in text.lower()]
    local = os.path.join(ROOT, "public_check.local.txt")                   # 擁有者自己的名字（git 忽略；有才比）
    if os.path.isfile(local):
        with open(local, encoding="utf-8") as f:
            words = [w.strip() for w in f if len(w.strip()) >= 2 and not w.startswith("#")]
        assert not [w for w in words if w.lower() in text.lower()]


# ---- 0.2.0：關鍵字猜類別 ----

def test_guess_category_from_keywords(make_bot, owner):
    bot = make_bot("money")
    bot.say(owner, "/money_cat_add 飲料 食 keywords=咖啡,拿鐵")
    bot.say(owner, "/money_cat_add 咖啡豆 keywords=咖啡豆,濾掛")
    bot.say(owner, "/money 65 拿鐵 早上")
    assert "飲料 65 元（拿鐵 早上）" in bot.last(owner) and "從「拿鐵」猜是 飲料" in bot.last(owner)
    bot.say(owner, "/money 300 咖啡豆子")                                 # 最長的關鍵字優先（咖啡豆 > 咖啡）
    assert "咖啡豆 300 元" in bot.last(owner)
    bot.say(owner, "/money 80 外帶 冰拿鐵")                                # 備註裡的字也算
    assert "飲料 80 元（外帶 冰拿鐵）" in bot.last(owner)
    bot.say(owner, "/money 99 寵物")                                        # 猜不到：照打的字記
    assert "寵物 99 元" in bot.last(owner) and "猜" not in bot.last(owner)
    bot.say(owner, "/money 10")                                            # 沒給類別：待分類，不擋
    assert "待分類 10 元" in bot.last(owner) and "先放「待分類」" in bot.last(owner)


def test_guess_unit(make_bot):
    module = make_bot("money").modules["money"]
    cats = [{"name": "A", "keywords": ["ab"]}, {"name": "B", "keywords": ["abc"]}, {"name": "C", "keywords": ["xy"]}]
    assert module.guess(cats, "zzABCzz")[0]["name"] == "B"
    assert module.guess(cats, "xy ab")[0]["name"] == "A"                   # 一樣長：清單順序
    assert module.guess(cats, "nothing") is None and module.guess([], "ab") is None