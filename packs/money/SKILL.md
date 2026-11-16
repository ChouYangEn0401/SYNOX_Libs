---
name: money
description: 使用者提到花了多少錢、收入、轉帳存錢、代墊或欠款、分期與信用卡帳單、問這期還能花多少、或要寫週檢討月檢討時使用。
---

# 記帳

每個人有自己的一本帳。工具回的字（含 ⚠️ 開頭的提醒）照實轉告使用者，不要自己改數字。
金額只放數字；帳本的幣別與小數位數是使用者自己設的，工具會照設定換算。
下面是總表；每一列最後是主題檔（`skills/` 底下），要知道細節時再讀。

## 使用者說什麼 → 用哪個工具

| 使用者說 | 工具 | 主題檔 |
|---|---|---|
| 「午餐 120」「剛剛加油花了 1500」「刷卡買了鞋子 1800」 | `money__add` | skills/record.md |
| 「薪水進來 32000」「賣二手書收 300」 | `money__in` | skills/record.md |
| 「那筆其實是 150」「把昨天那筆改成交通」 | 先 `money__list` 找編號，再 `money__edit` | skills/record.md |
| 「剛剛那筆記錯了」 | `money__undo` | skills/record.md |
| 「最近記了什麼」「這期飲料記了幾筆」 | `money__list`、`money__show` | skills/record.md |
| 「這筆應該算什麼」「有哪些類別」 | 讀主題檔 | skills/categories.md |
| 「存了 5000」「繳卡費 12000」「從銀行領 3000」 | `money__transfer` | skills/transfer.md |
| 「我幫小明付了午餐 120」「小明還我錢了」 | `money__ingest`（type 寫 iou） | skills/iou.md |
| 「手機分 12 期」「這是信用卡帳單」 | `money__ingest`（分期購買、信用卡帳單） | skills/debt.md |
| 「這期還能花多少」「額度還好嗎」「這個月花多少」 | `money__summary`、`money__check`、`money__month` | skills/budget.md |
| 「這週花在哪」「幫我寫這個月的檢討」 | `money__report`，寫完 `money__review_save` | skills/review.md |
| 一張收據很多品項、一則訊息好幾筆帳 | `money__ingest` | skills/ingest.md |

## 一定要守

- 金額看不懂（「幾百塊」「差不多一千」）就先問清楚，不要猜。
- **刷卡買東西是支出**（`money__add`，account 寫那張卡）；**繳信用卡帳單才是轉帳**（`money__transfer`）。
- 代墊、欠款一定要知道方向（誰幫誰付）與對象；聽不出來就先問一句。
- 記完一筆，工具會回「這期還能花多少」或額度提醒：照實講給使用者聽。
- 使用者沒說要做的事不要做：不要自己套用預算建議、不要自己刪帳、不要替他改類別名字。
- 檢討只能照 `money__report` 給的數字寫，不要編造沒有的店名或金額。

## 只給人打的指令（AI 叫不到；需要時告訴使用者怎麼打）

| 想做的事 | 請使用者打 |
|---|---|
| 刪掉某一筆 | `/money_rm <編號>`（會先確認） |
| 誰欠我、我欠誰的淨額 | `/money_lend` |
| 信用卡這期要繳多少、對帳單 | `/money_card`、`/money_card_match`、`/money_card_pay` |
| 分期與負債的排程、還一期 | `/money_debt` |
| 核對帳戶餘額 | `/money_balance` |
| 水電瓦斯帳單 | `/money_bill` |
| 把收據檔掛到一筆帳上 | `/money_attach` |
| 改設定、預算、類別 | 記帳本的「規劃」「設定」分頁 |
