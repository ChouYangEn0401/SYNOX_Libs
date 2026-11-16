# 週檢討、月檢討怎麼寫

帳本自己不叫 AI：由你讀數字、寫好、存回去。畫面的「檢討」分頁會顯示你存的檢討。

## 步驟

1. **拿數字與規則**：`money__report kind=month key=2030-01 review=yes`
   （週：`money__report kind=week key=2030-W03 review=yes`；不給 key＝現在該寫的那一份）。
   回來的字裡有：這段時間的數字（JSON）、寫的規則、金額的單位、回覆的格式（JSON Schema）。
2. **照規則寫**，只用資料裡的數字、品項、店名；不要用任何 emoji 或 ✅❌★ 這類符號。
3. **存回去**：`money__review_save kind=month key=2030-01 content=<JSON>`。
4. 把工具回的那段（等第、標題、重點）給使用者看；有「注意：…」的問題也要講。

只是問「這週花在哪」「這個月花多少」：`money__report kind=week`（不用 review=yes），把回來的字整理給他看就好，不用存。

## 內容的格式

```json
{"grade": "B", "title": "午餐穩定，耳機是大筆",
 "summary": "這期生活支出 1,770，比預算少；1/12 的耳機 1,200 是最大一筆。",
 "good": [{"text": "午餐都在 130 以內", "level": "key"}, {"text": "沒有亂買飲料", "level": "normal"}],
 "watch": [{"text": "耳機 1,200 一次吃掉一半的購物預算", "level": "key"}],
 "strategy": [{"text": "下個月飲料一週最多兩杯", "level": "normal"}]}
```

| 欄位 | 規則 |
|---|---|
| grade | A、A-、B+、B、B-、C+、C、C-、D 其中一個（A 很好、B 還可以、C 要注意、D 失控） |
| title | 一句話，20 字內 |
| summary | 200 字內 |
| good、watch、strategy | 各 1–4 點（多的只留前 4 點），每點 60 字內；level 寫 key（最該記住的，每組 1–2 點）或 normal |
| suggestion | 只有月檢討、而且規則裡有寫才要：下一期怎麼分（見下面） |

月檢討的 suggestion：

```json
{"buckets": {"fixed": 15000, "saving": 10000, "living": 20000},
 "group_budgets": {"食": 12000, "行": 3000, "樂": 5000},
 "saving_priority": ["銀行帳戶"], "reason": "購物先收一點，存錢照舊"}
```

- buckets 的格子照規則裡列的（每一格都要有），**加總要剛好等於**規則裡給的基準數字。
- group_budgets 的大類只能用規則列的，加總要等於 buckets 裡的生活費（living）。
- saving_priority 只能從規則列的帳戶選。
- 數字對不起來也會存，但會標「對不起來」，使用者按不了「照這個分」。存完看工具回的「注意」，有就修正再存一次（同一份會蓋掉）。

## 太長放不下時

一個欄位最多 2000 字。內容太長就分欄位送，同一次呼叫：
`money__review_save kind=week key=2030-W03 grade=B title=這週還可以 summary=… good=<JSON 清單> watch=<JSON 清單> strategy=<JSON 清單>`
（good、watch、strategy 也可以一行一點，開頭寫 ! 表示重要；suggestion 放 JSON 物件。）

## 語氣

像朋友：直接、具體、引用數字，不說教。額度提醒（規則裡標「一定要回應」的）一定要在 watch 或 strategy 講到原因和怎麼調。
「大概多少」的參考值只有很誇張（兩三倍）才講。

## 套用建議、回覆

- 使用者同意月檢討的分配建議：請他在「檢討」分頁按「照這個分」，或打 `/money_review_apply <檢討編號>`。不要自己套。
- 使用者對檢討有想法：畫面上可以回覆；他在聊天裡說的，照實轉述就好。

## 排程

每天跑一次 `/money_review_due`：回的字就是要給使用者的週報、檢討書（回了就算送出），
最後一段「該寫檢討了：…」是提醒你照上面的步驟寫。
