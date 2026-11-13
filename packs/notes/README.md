# 📒 notes（隨手記）

| 指令 | 做什麼 |
|---|---|
| `/note <文字>` | 記一筆 |
| `/note_list [關鍵字]` | 列出全部，或只列含關鍵字的 |
| `/note_rm <編號>` | 刪掉一筆 |
| `/note_clear` | 全部刪掉（⚠️ 危險；AI 要做會先送確認卡） |

資料：`data/notes.json.enc`（保管箱加密；金鑰用 Windows DPAPI 綁這個帳號）。

**個人版與伺服器版**：伺服器版每人一份（`data/users/<id>/notes.json.enc`，用每個人自己的金鑰加密），別人的指令、面板、AI 都看不到；但伺服器的管理者有主金鑰，**解得開每個人的資料**。個人版改成伺服器版時，舊資料不會自己搬（[ADR-0003](../../docs/adr/0003-personal-and-server-mode.md)）。
