"""一筆帳怎麼寫：大項目（類別）在上、細節在下。"""
from money_engine.services import ingest, labels, quick, transactions as tx, wallet


def test_meal_is_the_title_and_the_place_goes_below():
    """午餐是大項目、店名在下面；晚餐取代銀行的「網路轉」、店名在下面。"""
    a = labels.split(desc="範例自助餐", category="午餐")
    assert a == {"title": "午餐", "detail": ["範例自助餐"]}
    b = labels.split(desc="網路轉", merchant="範例義大利麵", category="晚餐")
    assert b == {"title": "晚餐", "detail": ["範例義大利麵"]}                # 銀行的字不出現
    assert labels.split(desc="午餐", category="午餐") == {"title": "午餐", "detail": []}   # 不重複講


def test_bank_memo_never_becomes_a_title_or_detail():
    """捐款是大標題，不是「網路轉」；每個類別各自是自己的標題。"""
    assert labels.split(desc="網路轉", category="定期捐款") == {"title": "定期捐款", "detail": []}
    assert labels.split(desc="網路轉", category="公益捐款")["title"] == "公益捐款"
    assert labels.split(desc="轉帳存", category="薪資")["title"] == "薪資"


def test_transfer_keeps_what_you_wrote_and_the_route_is_the_detail():
    """ATM領＝大項目，生活帳戶 → 現金是細節（走向由存摺補上）。"""
    assert labels.split(desc="ATM領", kind="transfer") == {"title": "ATM領", "detail": []}
    assert labels.split(desc="存預備金", category="緊急預備金", kind="transfer") == \
        {"title": "存預備金", "detail": ["緊急預備金"]}


def test_statement_text_is_one_detail_and_unsorted_is_honest():
    """信用卡帳單原文：同一家店的兩種寫法只留一個；沒分類就老實寫待分類，不拿帳單原文當標題。"""
    r = labels.split(desc="範例廚房", merchant="FP一範例廚房", category=None)
    assert r == {"title": "待分類", "detail": ["範例廚房"]}
    r = labels.split(desc="SAMPLE APP", merchant="SAMPLE* APP SUB", category="訂閱")
    assert r == {"title": "訂閱", "detail": ["SAMPLE APP"]}
    assert labels.split(desc="耳機", merchant="某某3C", category="購物") == {"title": "購物", "detail": ["耳機", "某某3C"]}


def test_ingest_moves_the_bank_word_into_the_note(conn):
    r = ingest.run(conn, {"records": [{"type": "expense", "amount": 8000, "desc": "網路轉", "category": "房租",
                                       "date": "2037-10-09"}]})
    t = tx.get(conn, r["results"][0]["id"], include_deleted=True)
    assert t["desc"] == "" and t["note"] == "網路轉"                     # 它是「怎麼付的」，不是「買了什麼」
    assert (t["title"], t["detail"]) == ("房租", "")
    row = wallet.passbook(conn, "2037-10")["items"][0]
    assert (row["title"], row["detail"]) == ("房租", "")                 # 備註留著（資料不丟），畫面上不吵


def test_quick_buttons_do_not_offer_bank_words_or_the_category_name(conn):
    for desc in ("網路轉", "晚餐", "範例義大利麵", "麻油雞"):
        ingest.run(conn, {"records": [{"type": "expense", "amount": 200, "desc": desc, "category": "晚餐",
                                       "date": "2037-10-09"}]}, actor="t", source="t")
    names = [x["desc"] for x in quick.items(conn, "晚餐")["recent"]]
    assert "範例義大利麵" in names and "麻油雞" in names
    assert "網路轉" not in names and "晚餐" not in names


def test_advance_and_collect_are_the_big_item_and_what_you_wrote_goes_below():
    adv = labels.split(desc="雲端月費", merchant="SAMPLE CLOUD", category="借出", kind="transfer", purpose="lend")
    assert adv == {"title": "代墊", "detail": ["雲端月費", "SAMPLE CLOUD"]}
    assert labels.split(desc="代墊", category="借出", kind="transfer", purpose="lend") == {"title": "代墊", "detail": []}
    assert labels.split(desc="朋友還的", category="收回借款", kind="transfer", purpose="collect")["title"] == "收回"
