# 記一筆、改、撤銷、查

## 記一筆支出：`money__add`

| 欄位 | 怎麼填 |
|---|---|
| amount | 只放數字（不要「元」）；必填 |
| desc | 買了什麼（例：雞腿便當） |
| category | 細項（午餐、飲料、計程車…）；不知道就不填，帳本會照內容與時間猜 |
| account | 怎麼付的（現金、銀行帳戶、某張卡）；不填用預設帳戶 |
| date | 哪一天；可以寫「今天」「昨天」「1/15」「2030-01-15」；不填＝今天 |
| time | 幾點（HH:MM）；使用者說了就填，「午餐」「晚餐」的猜類別會用到 |
| merchant | 店家（例：範例便當店） |
| note | 備註 |
| refund | 退款寫 yes（amount 還是寫正數，會存成負的支出） |
| for_period | 算進哪一期（預繳、補繳；例 2030-02） |
| nature | 長期用的東西（囤貨、3C、一年份）寫 stock；平常不要填 |

例：
- 「午餐 120」→ `money__add amount=120 category=午餐`
- 「昨天在範例便當店吃晚餐 150，刷範例卡」→ `money__add amount=150 desc=晚餐 merchant=範例便當店 date=昨天 account=範例卡`
- 「鞋子退貨退了 800」→ `money__add amount=800 desc=鞋子退貨 refund=yes`

## 記一筆收入：`money__in`

欄位跟 `money__add` 一樣（amount、desc、category、account、date…）。
- 「薪水進來 32000 到銀行」→ `money__in amount=32000 category=薪資 account=銀行帳戶`
- 薪水有扣勞健保要分開記：用 `money__ingest` 的 income 紀錄加 deductions（見 ingest.md）。

## 改一筆：`money__edit`

先用 `money__list` 找到那一筆的編號（id），只給要改的欄位。清掉一個欄位寫 `-`。
- 「剛剛那筆其實是 150」→ `money__list n=3` 找到編號 → `money__edit id=<編號> amount=150`
- 「把昨天那筆改成交通」→ `money__edit id=<編號> category=計程車`
- 「備註拿掉」→ `money__edit id=<編號> note=-`

可以改：amount、desc、category、account、to（轉帳的轉入帳戶）、date、time、merchant、note、for_period、nature、
counterparty（對象）、advance（yes＝這筆其實是幫別人先付的代墊，配 counterparty 寫對方名字；no＝切回自己的花費）。

## 撤銷：`money__undo`

撤銷**最近一次**寫入的整批（一則訊息記了三筆，就三筆一起撤）。
- 「剛剛那筆記錯了」→ `money__undo`，再照使用者說的重記。
- 要刪的不是最近那一筆：請使用者打 `/money_rm <編號>`（會先確認）。

## 查：`money__list`、`money__show`

`money__list` 的篩選：n（幾筆）、period（哪一期，例 2030-01）、date、category、group（大類）、account、
keyword 或 q（關鍵字）、month（YYYY-MM）、kind（支出、收入、轉帳）。
- 「最近記了什麼」→ `money__list n=10`
- 「這期飲料記了幾筆」→ `money__list category=飲料`
- 「上個月在範例便當店花了多少」→ `money__list keyword=範例便當店 month=2029-12`

`money__show id=<編號>`：一筆的全部內容（品項、備註、收據、改過的紀錄）。

## 注意

- 一則訊息裡有好幾筆、或一張收據有很多品項：用 `money__ingest` 一次記（ingest.md），不要一筆一筆叫。
- 同一筆重複說兩次（「剛剛那筆午餐 120」）不要再記一次：先 `money__list` 看有沒有記過。
- 記完工具回的「可能重複」「猜成某類別」提醒，照實問使用者要不要改。
