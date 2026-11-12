"""伺服器版：每個人的第一筆不能帶到別人的檔案裡（回歸測試：dict(EMPTY) 淺拷貝會共用 list）。"""

import pytest

CASES = {"calendar": ("/cal 明天 9:00 {}", "events", "title"),
         "todo": ("/todo {}", "items", "text"),
         "bullet": ("/bj • {}", "entries", "text")}


@pytest.mark.parametrize("name", sorted(CASES))
def test_first_write_of_each_person_stays_private(make_bot, name):
    bot = make_bot(name, per_user=True)
    cmd, key, field = CASES[name]
    store = bot.packs[name].store
    # 個人版的權限只給擁有者；這裡直接呼叫 handler 的路徑：兩個人各自第一次寫入
    node = bot.app.registry.node(f"{bot.packs[name].manifest.group}.add")

    class U:
        def __init__(self, tg_id):
            self.tg_id = tg_id

    node.fn(bot.api, 100, U(100), cmd.format("擁有者的").split()[1:])
    node.fn(bot.api, 555, U(555), cmd.format("別人的").split()[1:])
    mine = [x[field] for x in store.of(100).load({})[key]]
    theirs = [x[field] for x in store.of(555).load({})[key]]
    assert mine == ["擁有者的"] and theirs == ["別人的"]
