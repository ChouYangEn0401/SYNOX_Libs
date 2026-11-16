# 代墊與欠款

四個方向一個格式：用 `money__ingest` 送一筆 type 是 iou 的紀錄。
**direction 與 who 一定要寫**；聽不出方向就先問使用者一句（「是你幫他付，還是他幫你付？」），不要猜。

| direction | 意思 | 帳本怎麼算 |
|---|---|---|
| lend | 你幫別人先付（別人欠你） | 錢從你的帳戶出去，記成「別人欠我」，不算你的花費 |
| owe | 別人幫你先付（你欠別人） | 算你的花費（照 desc 猜類別），錢包要等還的那天才少 |
| collect | 對方還你 | 「別人欠我」少一筆，錢進你的帳戶 |
| repay | 你還對方 | 「我欠別人」少一筆，錢從你的帳戶出去 |

欄位：who（對方名字，前後要寫得一樣才對得上）、amount、date（YYYY-MM-DD）、time、desc（買了什麼）、
category（只有 owe：那筆東西的細項）、account（lend／repay＝從哪個帳戶付，lend 可以是信用卡；collect＝收進哪個帳戶）、note。

## 例子

「我幫小明付了午餐 120」：

```json
{"records": [{"type": "iou", "direction": "lend", "who": "小明", "amount": 120, "date": "2030-01-15", "desc": "午餐"}]}
```

「小華幫我買了電影票 300，我還沒還」：

```json
{"records": [{"type": "iou", "direction": "owe", "who": "小華", "amount": 300, "date": "2030-01-15", "desc": "電影票", "category": "娛樂"}]}
```

「小明還我 120 了，轉到銀行」：

```json
{"records": [{"type": "iou", "direction": "collect", "who": "小明", "amount": 120, "date": "2030-01-16", "account": "銀行帳戶"}]}
```

「我把電影票的錢還給小華了」：

```json
{"records": [{"type": "iou", "direction": "repay", "who": "小華", "amount": 300, "date": "2030-01-17"}]}
```

送法：`money__ingest records=<上面那段 JSON> source_ref=<這則訊息的編號>`。

## 一起吃飯、一人付全部

「我們四個吃 800，我先付」：使用者自己那份是花費，其他三份是代墊。分兩筆，放同一批：

```json
{"records": [
  {"type": "expense", "amount": 200, "date": "2030-01-15", "time": "19:00", "desc": "聚餐（自己那份）", "category": "交際"},
  {"type": "iou", "direction": "lend", "who": "小明", "amount": 200, "date": "2030-01-15", "desc": "聚餐"},
  {"type": "iou", "direction": "lend", "who": "小華", "amount": 200, "date": "2030-01-15", "desc": "聚餐"},
  {"type": "iou", "direction": "lend", "who": "小美", "amount": 200, "date": "2030-01-15", "desc": "聚餐"}
]}
```

怎麼分（平分、誰點比較多）照使用者說的；他沒說就問。

## 查誰欠誰

AI 叫不到；請使用者打 `/money_lend`（每個人的淨額：誰欠我、我欠誰）。

## 注意

- 名字要跟之前寫的一樣（「小明」和「明明」會變兩個人）。不確定就問。
- 還錢的金額比欠的多或少：照實記，工具會提醒；不要自己湊。
- 已經記成一般支出、後來才說是幫別人付的：用 `money__edit id=<編號> advance=yes counterparty=<對方名字>` 改成代墊
  （advance=no 切回自己的花費）。
