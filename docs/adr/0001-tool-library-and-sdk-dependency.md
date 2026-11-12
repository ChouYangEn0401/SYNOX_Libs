# ADR-0001：工具庫獨立成 SYNOX_Libs，每個工具宣告依賴的 SYNOX SDK 版本

- 狀態：已採用（2026-11-12）
- 範圍：SYNOX_Libs（這個 repo）與 SYNOX 主程式之間的分工

## 背景

維護者的決定（2026-11-12）：host（SYNOX 主程式）出廠是空的；工具各自以套件安裝，每個工具宣告依賴的 SDK 版本；所有工具集中在這個工具庫管理。

在這之前：

- SYNOX 主程式出廠就帶 `packs/money`、`packs/notes`、`packs/remind`（寫死在 repo 裡、`loader.BUNDLED` 把它們當成「出廠＝信任」）。
- SYNOXPacks 是另一個 repo，放 `calendar`、`todo`、`bullet`，用 `packs.dirs` 指過去或自己打包。
- 工具跟 SYNOX 之間沒有「我是照哪一版介面寫的」這個宣告，只有 `requires.synox`（SYNOX 本體的版本）。

## 決定

### 1. 兩個 repo，一個是主程式、一個是工具庫

| | SYNOX（主程式、host） | SYNOX_Libs（工具庫，這裡） |
|---|---|---|
| 放什麼 | 載入器、安全管線（五關）、AI、網頁、保管箱、**SDK 正本** | 每個工具一個資料夾 `packs/<name>/`、打包腳本、工具自己的測試 |
| 出廠時有幾個工具 | **0 個**（`packs/` 是安裝的落點，裡面只有說明） | 全部 |
| 給使用者的東西 | 安裝檔／程式 | 一堆 `.synoxpack` ＋ `index.json`（目錄）；使用者自己挑要裝哪個 |

安裝一律走 SYNOX 已有的路（同一條五關管線）：網頁「設定 → 裝功能」上傳、點兩下 `.synoxpack`（`Install-Pack.bat`）、
`python -m synox install <檔>`，或 `config.json` 的 `packs.dirs` 指到一個資料夾（開發用）。工具庫不另做安裝程式。

### 2. 「SDK」＝ SYNOX 給工具的介面合約，有自己的版本號 `SDK_VERSION`

- **正本在 SYNOX**：`src/synox/sdk/author.py` 的 `SDK_VERSION`（semver）。理由：合約是 host 實作的，版本號要跟實作放在一起才不會漂。
- 合約涵蓋三塊：
  1. `pack.json` 的格式與各欄位的意思（`docs/pack-format.md`）；
  2. 子程序工具（kind=cli／mcp）拿到的環境變數與 JSON 信封——就是單檔 `synox_sdk.py`；
  3. kind=python 工具的介面：`install(app, pack)`、`pack.store`／`store.of(user)`／`pack.path_of`／`pack.secret`／`pack.web_route`、
     `synox.packs.kit`、`isdtelekit.core.registry` 的 `Field`／`READ`／`WRITE`／`DANGER`。
- 這個合約以外的 SYNOX 內部模組（例如 `remind` 用到的 `synox.schedule.commands`）**不算 SDK**：用到的工具另外用 `requires.synox` 釘住 SYNOX 版本。

### 3. 每個工具都要宣告 `requires.sdk`

```json
"requires": { "synox": ">=0.2", "sdk": ">=0.2,<0.3" }
```

- 寫法跟 `requires.synox` 一樣（`>=`、`<`、逗號接起來）；0.x 的 SDK 照全域版本規則寫到下一個 MINOR 為止（`>=0.2,<0.3`）。
- **SYNOX 端**：第 1 關檢查寫法；安裝時這台的 SDK 不在範圍內＝不裝、說要哪一版；載入時不合＝狀態「版本不合」、不載入（跟 `requires.synox` 同一個出口，doctor／`packs status` 都看得到）。
- **工具庫端**：`scripts/build.py` 打包前檢查每個工具都有 `requires.sdk`，而且範圍包含「拿來打包的那個 SYNOX」的 `SDK_VERSION`；沒有就不打包。

### 4. 工具怎麼「裝」SDK

| 工具的 kind | 執行時的 SDK 從哪來 | 開發／測試時 |
|---|---|---|
| `python`（這裡現在的 6 個都是） | SYNOX 程序本身（host 提供，不用另外裝） | 測試 import 一份 SYNOX：環境變數 `SYNOX_HOME`，沒設就找並排的 `../SYNOX` |
| `cli`／`mcp`（Python 寫的） | 工具資料夾裡的 `synox_sdk.py`（`synox-sdk init --from` 自動放一份） | `pip install <SYNOX>/sdk`（`synox-sdk` 套件，版本＝`SDK_VERSION`） |
| `cli`／`mcp`（別的語言） | 不需要（照 JSON 信封協定印就好） | — |

SDK 的發佈形式：SYNOX 的 `sdk/` 資料夾就是一個可以單獨安裝的套件（`sdk/pyproject.toml`，名字 `synox-sdk`，版本直接讀
`synox_sdk.SDK_VERSION`）。工具庫的打包腳本看到工具資料夾裡有 `synox_sdk.py`，會檢查它的 `SDK_VERSION` 也在那個工具的 `requires.sdk` 範圍內。

### 5. 打包與目錄

`python scripts/build.py` → `dist/<name>-<version>.synoxpack`（呼叫 SYNOX 的 `synox.sdk.zip_pack`：先跑安裝時的第 1、2 關，沒過不打包）
＋ `dist/index.json`：

```json
{
  "library": "SYNOX_Libs", "library_version": "0.1.0", "built_with": {"synox": "0.2.0", "sdk": "0.2.0"},
  "packs": [{"name": "money", "title": "💰 記帳", "version": "0.1.1", "kind": "python",
             "summary": "…", "requires": {"synox": ">=0.2", "sdk": ">=0.2,<0.3"},
             "bundles": ["starter"], "capabilities": [], "file": "money-0.1.1.synoxpack",
             "sha256": "…", "size": 12345}],
  "bundles": {"starter": {"title": "入門", "packs": ["notes", "remind", "todo"]}}
}
```

建議群組（`bundles`）寫在 `catalog.json`，只是給人挑的建議，裝的時候還是一個一個裝。

### 6. 版本

- 工具庫本身：根目錄 `VERSION`（目錄的版本，從 `0.1.0` 開始）＋ `CHANGELOG.md`。
- 每個工具：自己 `pack.json` 的 `version`（升級時 SYNOX 比這個）。
- SDK：SYNOX 的 `SDK_VERSION`。

## 考慮過但沒選的

| 做法 | 為什麼不選 |
|---|---|
| SDK 正本搬到工具庫 | 合約由 host 實作；正本離開 host，兩邊版本一定會漂（全域規則：同一件事只寫一處） |
| 工具用 pip／wheel 發佈 | 會繞過 SYNOX 的五關（宣告、靜態掃描、雜湊鎖、呼叫前驗參數、核准卡） |
| 工具庫自己寫一份打包／檢查 | 跟安裝時跑的檢查會不一樣；直接呼叫 SYNOX 的 `zip_pack` 才保證「打包過＝裝得進去」 |
| 繼續用 SYNOXPacks | 它只有 3 個、沒有 SDK 版本宣告、沒有打包與目錄；保留不動（只讀），內容搬進來 |

## 後果

- SYNOX 新裝好時 `/help` 只有基本指令，並提示「還沒裝任何工具、去哪裡裝」；要用記帳、筆記、提醒，得從工具庫裝。
- 從舊版 SYNOX 更新上來的人：三個內建工具會消失，但**資料還在**（`data/` 沒動），重新裝同名工具就回來。
- 工具庫的開發與打包需要一份 SYNOX（`SYNOX_HOME`）；工具庫不複製 SYNOX 的任何程式。
- SDK 合約改了不相容的地方 → `SDK_VERSION` 升 MINOR（0.x）或 MAJOR（1.x 以後），舊工具會被明確擋下並說原因，不會載入後壞掉。
- 還沒決定：SYNOX 的 4 個 connector（Notion、Todoist、Google 日曆、Apple 提醒事項）要不要也搬來（SYNOX 的 QuestionToUser.md）。
