"""記帳工具的 bridge：SYNOX 的指令／畫面 ↔ engine（邏輯層）。一個領域一個檔；格式見 table.py，規則見 ../CONTRACT.md。

開資料庫只准經 session.open_book(pack, user)。這一層不准有模組層級的可變狀態、不准寫環境變數、
不准裸用 pack 的共用存放區（唯一例外：W6 的 legacy.py 用 store.of(uid) 讀舊資料），也不准用全部人共用的資料夾。
"""
