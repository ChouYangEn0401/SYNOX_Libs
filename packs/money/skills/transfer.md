# 轉帳與卡費

## 什麼是轉帳

錢從自己的一個帳戶搬到自己的另一個帳戶：**不算花費**，也不算收入。
例：存錢、繳信用卡帳單、從銀行領現金、轉到投資帳戶、還自己的借款。

## `money__transfer`

| 欄位 | 怎麼填 |
|---|---|
| amount | 金額；必填 |
| account | 從哪個帳戶出去（不填用預設帳戶） |
| to | 轉到哪個帳戶 |
| purpose | 用途（不填會照帳戶判斷）：card_payment 繳卡費、saving 存錢、investing 投資、prepay 預付、debt 還債、other 其他 |
| category | 例：繳卡費、帳戶互轉、緊急預備金 |
| date、time、desc、note | 同記帳 |
| for_period | 算進哪一期（預繳、補繳） |

例：
- 「存了 5000 到緊急預備金」→ `money__transfer amount=5000 account=銀行帳戶 to=緊急預備金 purpose=saving`
- 「從銀行領 3000」→ `money__transfer amount=3000 account=銀行帳戶 to=現金`
- 「繳範例卡的卡費 12000」→ `money__transfer amount=12000 account=銀行帳戶 to=範例卡 purpose=card_payment`

## 信用卡：最常搞錯的地方

| 情況 | 怎麼記 |
|---|---|
| 刷卡買東西 | **支出**：`money__add amount=1800 desc=鞋子 account=範例卡` |
| 繳信用卡帳單 | **轉帳**：`money__transfer … to=範例卡 purpose=card_payment` |
| 刷卡退款 | `money__add … account=範例卡 refund=yes` |
| 分期買東西 | 見 debt.md（用 `money__ingest` 的 installment_purchase） |

繳卡費**不要**記成支出：那些錢在刷卡當下已經算過了，再記一次會重複算。

使用者把整張信用卡帳單丟過來（截圖或文字）：用 `money__ingest` 的 card_statement 紀錄（debt.md），
帳本會逐列跟記帳對：哪些對上、哪些漏記、哪些金額不同。之後的處理（補記、繳費）請使用者打
`/money_card`、`/money_card_match`、`/money_card_pay`。

## 帳戶名字

account、to 寫帳戶的名字或別名（現金、銀行帳戶、範例卡…）。寫錯時工具會回「是不是：…」的建議：
照建議問使用者，不要自己挑一個。帳戶是使用者在「設定」分頁加的；沒有的帳戶請他去加。

## 注意

- 「轉給朋友 500」不是轉帳：那是代墊、還錢或送錢（iou.md、或 `money__add` 記成禮物、交際）。
- 換外幣、買基金股票：轉到投資帳戶是轉帳（purpose=investing）；投資的盈虧另外記，不在這裡。
