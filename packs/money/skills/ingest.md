# 收據與一批寫入

一則訊息裡有好幾筆帳、或一張收據有很多品項：用 `money__ingest` 一次送，**整批要嘛全部記好、要嘛全部不記**
（任何一筆有錯，工具會說第幾筆哪裡錯，一筆都不會寫進去）。

## 怎麼送

`money__ingest records=<JSON> source_ref=<這則訊息的編號>`

- records：`{"records": [ … ]}`（也可以直接給一個清單）。每一筆都要有 type。
- source_ref：同一則訊息永遠給同一個值（例 chat:12:345）；重送不會多記。
- dry_run=yes：先試算（大量截圖時先跑一次，回「新的幾筆、可能重複幾筆、之前記過幾筆」給使用者確認）。
- **一次最多 2000 字**：超過就分幾批，每批用不同的 source_ref（例 chat:12:345:1、chat:12:345:2）。
- JSON 裡的金額是**整數**、單位是帳本的最小單位（幣別沒有小數時就是一般金額）；日期一律 YYYY-MM-DD。

## 紀錄的種類

| type | 用途 | 主要欄位 |
|---|---|---|
| expense | 支出 | amount、date、time、desc、category、account、merchant、items、refund、nature、for_period |
| income | 收入 | amount、date、desc、category、account、deductions（薪水扣繳） |
| transfer | 轉帳 | amount、date、from、to、purpose、category |
| iou | 代墊、欠款 | direction、who、amount、date、desc（iou.md） |
| day_note | 那天的小日記 | date、note |
| installment_purchase | 分期購買 | name、card、total、periods、date（debt.md） |
| card_statement | 信用卡帳單 | card、closing_date、total、lines（debt.md） |
| balance | 帳戶餘額核對 | account、balance、as_of |
| bill | 水電瓦斯帳單 | bill、amount、period_end、usage、due_date、paid |

欄位名拼錯會整批擋下來，照工具說的改。

## 一張收據、很多品項

記成**一筆**支出，品項放 items（總額是 amount）：

```json
{"records": [{"type": "expense", "amount": 285, "date": "2030-01-15", "time": "18:40", "desc": "範例超市採買",
  "merchant": "範例超市", "category": "食材",
  "items": [{"name": "雞蛋", "amount": 85}, {"name": "青菜", "amount": 60}, {"name": "牛奶", "amount": 140}]}]}
```

品項分屬不同類別（食材＋衛生紙）而使用者想分開算：拆成兩筆 expense。

## 一則訊息好幾筆

「早餐 60、午餐 120、晚上搭計程車 250」：

```json
{"records": [
  {"type": "expense", "amount": 60, "date": "2030-01-15", "time": "08:00", "desc": "早餐", "category": "早餐"},
  {"type": "expense", "amount": 120, "date": "2030-01-15", "time": "12:10", "desc": "午餐", "category": "午餐"},
  {"type": "expense", "amount": 250, "date": "2030-01-15", "time": "22:30", "desc": "計程車", "category": "計程車"}
]}
```

## 薪水

```json
{"records": [{"type": "income", "amount": 36000, "date": "2030-01-05", "desc": "一月薪水", "category": "薪資",
  "account": "銀行帳戶"}]}
```

薪資單上有扣繳（勞保、健保…）而使用者想分開記：加 `"deductions": {"<類別名>": 金額}`，amount 改寫扣繳前的總額；
扣繳的名字一定要是帳本裡有的類別（沒有的話工具會擋，請使用者先到「設定」分頁加）。

## 收據圖檔

使用者把收據圖存在自己的檔案資料夾：在 expense 加 `"attach": "receipt.png"`（從自己的檔案資料夾算起的相對路徑）。
已經記好的帳要補收據：請使用者打 `/money_attach <帳的編號> <檔名>`。只能用自己資料夾裡的檔。

## 回來的字

第一行是總數（新記幾筆、可能重複、之前記過、支出合計），接著每一筆一行，最後是 ⚠️ 提醒。
「可能重複」的要問使用者：兩筆都先算了，要刪哪一筆請他說（刪是 `/money_rm <編號>`）。
