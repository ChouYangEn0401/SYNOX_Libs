"""報表與檢討測試共用的假資料。"""
from money_engine import db
from money_engine.services import ingest, plans, settings

# 今天 2037-10-10（週六）：上一個完整的週是 2037-W40（09/28–10/04）；結束滿一天的期是 2037-09（09/05–10/04）。
EXPENSES = [
    ("2037-09-06", "12:10", 120, "排骨便當", "午餐"), ("2037-09-15", "19:00", 260, "麻油雞", "晚餐"),
    ("2037-09-22", "12:30", 130, "排骨便當", "午餐"), ("2037-09-23", "15:00", 60, "奶茶", "飲料"),
    ("2037-09-29", "12:20", 140, "排骨便當", "午餐"), ("2037-09-30", "20:00", 1200, "燒肉", "吃好"),
    ("2037-10-02", "15:10", 70, "奶茶", "飲料"), ("2037-10-03", "10:00", 2400, "耳機", "購物"),
]


def seed(conn):
    records = [{"type": "expense", "date": d, "time": t, "amount": a, "desc": desc, "category": c}
               for d, t, a, desc, c in EXPENSES]
    records.append({"type": "income", "date": "2037-09-05", "amount": 45000, "desc": "薪水", "category": "薪資"})
    records.append({"type": "day_note", "date": "2037-09-30", "note": "朋友生日，吃燒肉"})
    ingest.run(conn, {"records": records})
    with db.write(conn) as w:
        plans.set_plan(w, "2037-09", income_expected=45000,
                       buckets={"giving": 4500, "fixed": 12000, "saving": 8000, "invest": 5000, "debt": 3000},
                       group_budgets={"三餐": 7500, "吃好喝好": 2500, "購物": 2500})
        settings.put(w, "review.weekly", True)        # 這些測試要驗週報的規則
