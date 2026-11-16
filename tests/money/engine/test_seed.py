"""起始資料：用途、大類、細項、帳戶、設定照起始檔建；每個生活大類都屬於生活支出、轉帳細項都有用途、金額不寫在程式裡。"""
import json
import os

SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_test.json")


def test_seed_installs_buckets_groups_categories_accounts_settings(conn):
    with open(SEED, encoding="utf-8") as f:
        data = json.load(f)
    buckets = [r["name"] for r in conn.execute("SELECT name FROM buckets WHERE active = 1 ORDER BY sort")]
    assert buckets == [b["name"] for b in data["buckets"]]
    assert buckets == ["捐贈", "固定支出", "存錢儲蓄", "生活支出", "資產投資", "負債償還"]
    # 「資金操盤」先建好但不開：要在規劃頁打開才會出現
    assert conn.execute("SELECT active FROM buckets WHERE id = 'trading'").fetchone()[0] == 0
    living = conn.execute("SELECT id FROM buckets WHERE is_living=1").fetchall()
    assert [r["id"] for r in living] == ["living"]
    cats = {r["name"]: r for r in conn.execute(
        "SELECT c.*, g.name AS g, g.bucket_id FROM categories c JOIN category_groups g ON g.id=c.group_id")}
    for name in ["早餐", "午餐", "晚餐", "吃好", "飲料", "點心宵夜", "大眾運輸", "購物", "雜支", "定期捐款"]:
        assert name in cats, name
    assert cats["晚餐"]["meal_slot"] == "dinner" and cats["晚餐"]["bucket_id"] == "living"
    assert cats["定期捐款"]["bucket_id"] == "giving"
    assert cats["薪資"]["kind"] == "income" and cats["薪資"]["bucket_id"] is None
    for r in cats.values():
        if r["kind"] == "transfer":
            assert r["transfer_purpose"], r["name"]
        assert r["default_budget"] is None          # 金額不寫在程式裡
    accounts = [r["name"] for r in conn.execute("SELECT name FROM accounts ORDER BY sort")]
    assert accounts == [a["name"] for a in data["accounts"]]
    assert conn.execute("SELECT value FROM settings WHERE key='period_start_day'").fetchone()[0] == "5"
