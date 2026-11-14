# 新增（或修改）一個工具：從頭到尾

給人，也給之後接手的 Claude。每一步的指令都在這個 repo 跑過（`scripts/build.py`、`scripts/build_site.py`、`scripts/links.py`、
`scripts/site_stamp.py`）。發佈到 GitHub 與線上頁的步驟在 [RELEASING.md](RELEASING.md)、[ARTIFACTS.md](ARTIFACTS.md)。
Windows 把 `python` 換成 `<SYNOX>\.venv\Scripts\python.exe`（找 SYNOX 的順序見 [README「給開發者」](../README.md#給開發者)）。

## 一眼看完：加一個資料夾，會出現在哪

```
packs/<名字>/pack.json ──┬─ scripts/build.py ──→ dist/<名字>-<版本>.synoxpack、<名字>.synoxpack（別名）
（＋程式、SKILL.md、     │                     dist/index.json（目錄：版本、sha256、stage、line…）
  README.md、測試）       │                     dist/tools-<VERSION>.zip（全部工具）、SHA256SUMS
                         │
                         └─ scripts/build_site.py（build.py 全部打包時會自動跑）
                               ├→ docs/site/index.html：入口頁多一張卡片（不用改 HTML）
                               └→ docs/site/<名字>.html：還沒有手寫說明頁 → 產生一頁簡單的（標「說明頁還沒寫」）

GitHub 發佈頁（gh release upload）──→ SYNOX 網頁「功能與安裝 → 工具庫（官方）」列出它；裝了舊版的人看到「有新版」
線上頁（claude.ai Artifact）────────→ 入口頁重新發佈；新工具的頁第一次發佈後登記網址（docs/artifacts.json）
```

衣櫃（`costumes/`、`docs/site/wardrobe.html`）**不受影響**：工具跟外觀是兩條路，外觀見 README 的「衣櫃」。

## 1. 資料夾

```
packs/<名字>/                 名字：小寫英數與 -（例 google-calendar），＝pack.json 的 name、＝資料夾名
  pack.json                   宣告（下面第 2 節）
  pack.py                     程式（kind=python：install(app, pack)）；格式見 SYNOX 的 docs/pack-format.md、docs/tools.md
  SKILL.md                    給 AI 看的用法
  README.md                   給人看：指令、設定、資料存哪、現在的狀態（入口頁沒有手寫說明頁時連到這裡）
  panel.html、app/            選用：網頁面板／完整畫面（money 的做法，ADR-0003）
tests/test_<名字>.py          測試（第 4 節）
```

- 工具之間不能互相 import；共用程式放 `packs/_shared/`，用 `python scripts/sync_shared.py` 複製進去。
- 不要叫 `remind`、不要用 `remind` 群組（SYNOX 系統指令）。`_` 開頭的資料夾不是工具（`_shared`）。
- 資料夾裡的**每個檔都會打包**（`__pycache__` 除外）：草稿、假資料不要放進來。

## 2. `pack.json`

完整格式是 SYNOX 的 `docs/pack-format.md`（正本 `synox/packs/manifest.py` 的說明）。這個工具庫另外規定的標 ★。

| 欄位 | 例 | 說明 |
|---|---|---|
| `name` | `"notes"` | ＝資料夾名（`build.py` 擋） |
| `title` | `"📒 隨手記"` | 「emoji＋空白＋名字」：`/help`、SYNOX 卡片、入口頁卡片都從這裡拆 emoji 和名字 |
| `summary` | `"記一筆、找、刪（加密存在這台電腦）"` | 一句話；入口頁卡片沒寫 `line` 時用它 |
| `version` | `"0.1.1"` | semver。★ 改了工具就升（第 5 節） |
| `stage` | `"alpha"` | ★ 一定要寫：`test`／`alpha`／`beta`／`stable`（README「穩定度」）；新工具從 `test` 或 `alpha` 開始 |
| `kind` | `"python"` | `python`／`cli`／`mcp`／`connector` |
| `group` | `"note"` | 指令群組：`/note`、`/note_list`… |
| `access` | `"member"` | `owner`＝👤 單人用；`member`／`everyone`＝👥 伺服器版也能用（入口頁標籤照這個） |
| `surfaces`、`data`、`storage` | `["dm"]`、`"user"`、`"vault"` | 權限與資料（pack-format.md） |
| `order` | `10` | SYNOX `/help` 的順序；入口頁沒在 `docs/site-tools.json` 的 `order` 列到時也用它排 |
| `capabilities` | `["network"]` | 有 `network`＝入口頁放在「接外面的服務」、標「要上網」；沒有＝「存在你電腦裡的」、標「不用上網」 |
| `requires` | `{"pip": [], "synox": ">=0.2", "sdk": ">=0.2,<0.3"}` | ★ 一定要寫 `requires.sdk`，範圍要包含拿來打包的 SYNOX 的 SDK（ADR-0001） |
| `python` | `{"module": "pack"}` | kind=python |
| `skill` | `"SKILL.md"` | |
| `auth` | `{"type": "token", "fields": [...]}` | 接外面服務、要 token 的（notion 的寫法） |
| `help` | 下面 2a | 選填，強烈建議：`/help` 卡片的白話說明與範例 |
| `site` | 下面 2b | 選填：入口頁卡片上的字 |

### 2a. `help` 區塊（SYNOX `/help` 卡片、網頁「💡 範例」）

規則的正本是 SYNOX 的 `synox/helpui/examples.py`（安裝時嚴格檢查，不合＝不給裝；載入時壞的那條丟掉）：

```json
"help": {
  "featured": ["add", "list"],
  "commands": {
    "add":  {"plain": "記一筆", "examples": [{"text": "買牛奶", "note": "記下來"}, "明天 繳電費"], "alias_suggest": ["記"]},
    "list": {"plain": "列出全部，或只列含關鍵字的", "examples": ["牛奶"]}
  }
}
```

- 鍵＝指令名（葉節點）：主要動作通常是 `add`（`/note`），其他是 `/<group>_<指令>`（`list`＝`/note_list`）。
- `plain`：一句白話，**40 字內**；不要括號補充、不要 `=`、`<>`、`[]` 這種符號；不要術語（API、token、JSON、參數…，只提醒不擋）。
- `examples`：最多 3 個；`text` 只寫參數（不寫 `/note`，SYNOX 自己補），80 字內；`note` 60 字內；字串＝只有 `text`。
  範例一定要跑得動：SYNOX 載入後用那個指令真的參數規則解析一次，跑不動的丟掉、`doctor` 會報。
- `featured`：`/help` 卡片「最常用」，最多 5 個；`alias_suggest`：最多 2 個建議的自訂名字。
- 不准網址、信箱、像密鑰的字、像在對 AI 下指令的字；只放假的佔位資料。只給人看，不進 AI 的提示。
- 入口頁沒寫 `site.example` 時，拿 `featured` 第一個（再來是第一個有範例的指令）的第一個範例當卡片上的例子：
  `add` 或跟群組同名的指令＝`/<group> 參數`，其他＝`/<group>_<指令> 參數`（猜的，不對就寫 `site.example`）。

### 2b. 公開頁上的樣子：`site`（pack.json）與 `docs/site-tools.json`

入口頁卡片的每一格都可以不寫，產生器（`scripts/build_site.py`）會從 pack.json 猜。要指定時寫：

| 欄位 | 卡片上的哪裡 | 沒寫時 |
|---|---|---|
| `emoji` | 圖示 | `title` 開頭的 emoji（再沒有＝🧩） |
| `name` | 粗體名字 | `title` 去掉 emoji |
| `line` | 一句話（也會寫進 `index.json` 的 `line`，SYNOX 的工具卡片用） | `summary` |
| `example` | 灰底的指令例子（整句，含 `/`） | `help` 的第一個範例（上面）；都沒有就不顯示 |
| `status` | 第二個標籤，例 `"還沒真的連上去測過，只用假資料試過"` | 不用上網的工具標「不用上網」，要上網的標「要上網」 |
| `network` | `true`／`false`：放哪一組、標不標連網 | `capabilities` 有沒有 `network` |

寫在哪裡（後面的蓋過前面的）：

1. pack.json 的 `"site": {...}`：**新工具建議寫這裡**，跟著工具走。改它＝改了 pack.json＝要升 `version`。
2. `docs/site-tools.json` 的 `tools.<名字>`：不打包進 `.synoxpack`，**之後只想改公開頁的字、不想升版就改這裡**。
   同一個檔的 `order` 決定卡片順序（沒列到的照 pack.json 的 `order`、再照名字排在後面）。

`site` 只收上面 6 個欄位，寫錯（例 `colour`）`build.py --check` 就擋。SYNOX 安裝時不看 `site`（不認得的上層欄位不影響安裝）。

卡片連到哪：`docs/artifacts.json` 有這個工具的線上說明頁 → 連過去；沒有 → 連到 GitHub 上的 `packs/<名字>/README.md`，
並多一個「說明頁還沒寫」（只有產生的簡單頁）或「說明頁還沒上線」（有手寫頁、還沒登記網址）的標籤。

## 3. 說明頁

- **不寫也可以**：`scripts/build_site.py` 會用 `scripts/site_tool_template.html` 產生 `docs/site/<名字>.html`
  （名字、版本、標籤、`help` 裡的指令、下載連結、「說明頁還沒寫，先看 README」），頁尾寫「線上版：還沒發佈」。
- **要寫手寫頁**：照 `docs/site/notes.html` 的版面（同一套 CSS），頁尾那一行 `<p class="stamp" data-online="…">` 照抄、換網址與檔名。
  手寫頁沒有 `generated: scripts/build_site.py` 那行註解，產生器就**永遠不會動它**（原本的 9 頁都是手寫的）。
  下載連結直接寫完整網址也沒關係，`scripts/links.py` 會改成現在的版本。改完 `python scripts/site_stamp.py <名字>.html`。
- 第一次發佈到線上、登記網址：見 [ARTIFACTS.md](ARTIFACTS.md)「新增一頁」。

## 4. 測試

```bash
python -m pytest -q                                   # 全部（不連網、不開埠、不連 Telegram、不叫 AI）
python -m pytest -q tests/test_<名字>.py              # 只跑這個工具
```

新工具要動到的測試：

| 檔 | 要做什麼 |
|---|---|
| `tests/test_<名字>.py` | 新增：主要指令跑一輪。只用 `pack.store` 的用 `make_bot`（快，假 store）；要真的 SYNOX（保管箱、權限、伺服器版）用 `synox_bot`／`host_bot`（`tests/conftest.py`）。接外面服務的一律假 HTTP |
| `tests/test_gates.py` | `ALLOWED_CAPS` 加一行（宣告的 capabilities）；接外面服務的也加進 `CONNECT`（規矩：只用 network、`access: owner`） |
| `tests/test_build.py` | `SMOKE` 加一行：裝進空的 SYNOX 後跑的一個指令與回覆裡要有的字 |
| `catalog.json` | 選填：放進建議群組（只能列存在的工具，`build.py` 擋） |

這些會自動涵蓋新工具（不用改）：`tests/test_links.py`（index.json 有它、版本對、連結對）、`tests/test_build_site.py`（入口頁是產生器現在的輸出）、
`tests/test_site_pages.py`（每一頁的頁尾與頁面版本）、`tests/test_synox_integration.py`（全部工具一起載入）。

## 5. 版本規則

- **改了工具裡的任何檔（含 pack.json、README、SKILL.md）就升 `version`**，要比裝著的大，SYNOX 才當成升級、顯示「有新版」。
  semver：修 bug 升第三碼、加功能升第二碼、不相容（資料格式、指令名）升第一碼並寫怎麼搬。
- 換 `stage` 也是改了工具：一起升 `version`，[CHANGELOG.md](../CHANGELOG.md) 寫一行為什麼。
- 每次改版在 CHANGELOG 的 `[Unreleased]` 寫一行（標出工具與版本，例 `money 0.3.0 → 0.3.1`）。
- 只改 `docs/site-tools.json`、`docs/site/*.html`、測試：不用升工具的版本（它們不在 `.synoxpack` 裡）。
- `VERSION`（工具庫本身、發佈 tag）只有要開新的發佈頁時才改（[RELEASING.md](RELEASING.md)）。

## 6. 打包與檢查

```bash
python scripts/build.py --check          # 只檢查：pack.json（requires.sdk、stage、site）、catalog、下載連結、公開頁是不是最新
python scripts/build.py                  # 全部打包 → dist/；並重新產生入口頁（build_site.py）、改好下載連結（links.py）、蓋頁面版本
python scripts/build.py <名字>           # 只打這一個（試打包；不產生頁面、index.json 只列它）
python scripts/build_site.py             # 只重新產生公開頁（改了 docs/site-tools.json、樣板、artifacts.json 之後）
python scripts/build_site.py --check     # 只檢查公開頁
```

`build.py` 打包用的是 SYNOX 的 `zip_pack`（＝安裝時的第 1、2 關），沒過不打包。

## 7. 會出現在哪

| 地方 | 怎麼來的 | 要手動做的 |
|---|---|---|
| `dist/index.json` | `build.py`：name、title、summary、`line`、version、stage、access、capabilities、file、sha256… | 無 |
| `dist/tools-<VERSION>.zip` | `build.py`：全部工具＋只列工具的 index.json | 無 |
| 入口頁 `docs/site/index.html` | `build_site.py`：一張卡片（照第 2b 節） | 無；改字改 `docs/site-tools.json`，改版面改 `scripts/site_index_template.html` |
| 工具頁 `docs/site/<名字>.html` | 沒有手寫頁就產生簡單頁 | 選擇寫手寫頁（第 3 節） |
| 線上頁（Artifact） | 不會自己更新 | 重新發佈改到的頁、新頁登記網址（[ARTIFACTS.md](ARTIFACTS.md)） |
| GitHub 發佈頁 | 不會自己更新 | `gh release upload`（[RELEASING.md](RELEASING.md)） |
| `README.md` 的工具表 | 手寫 | 加一行（下載連結由 `links.py` 管） |
| 衣櫃 | 不受影響 | 無 |

## 8. SYNOX 怎麼拿到新工具

- **線上目錄**：SYNOX 網頁「設定 → 功能與安裝 → 工具庫（官方）」按「看有哪些工具」時，抓寫死在 SYNOX 的
  `synox/packs/catalog.py` 的 `OFFICIAL_INDEX_URL`（現在釘在 `releases/download/v0.2.0/index.json`）。
  所以**新工具上傳到發佈頁、`index.json` 換新的之後就會出現**；按「安裝」會下載那個檔、對大小與 sha256、跑一樣的安全檢查報告。
- 卡片上的字：emoji、名字來自 `index.json` 的 `title`，一句話來自 `line`（這裡的入口頁同一份）。
  SYNOX 那邊另外寫死了舊工具的例子（`TOOLS`）、說明頁網址（`HELP_PAGES`，沒有的連到入口頁）與「只用模擬測過」（`SIMULATED_ONLY`）；
  新工具要有自己的說明頁連結或那個標籤，要改 SYNOX 的 `catalog.py`（另一個 repo）。
- **有新版**：`index.json` 的版本比裝著的大（semver）→ 卡片標「有新版」、「全部更新」；`python -m synox tools check` 列出來、
  `python -m synox tools update <名字>` 下載 → 檢查報告 → 核准才換（舊版留在 `packs/_removed/`）；每週檢查（有勾的話）也會數進去。
  所以**沒升 `version` 的改動，已經裝的人永遠拿不到**。
- **離線／本機**：把 `tools-<VERSION>.zip` 解開放成 `<SYNOX>\tools\`，`python -m synox tools list`、`python -m synox tools install <名字>`。

## 9. 檢查清單（照順序）

1. `packs/<名字>/`：pack.json（name、title、summary、version、stage、requires.sdk、help、需要的話 site）、程式、SKILL.md、README.md
2. 測試：`tests/test_<名字>.py`、`tests/test_gates.py`（ALLOWED_CAPS／CONNECT）、`tests/test_build.py`（SMOKE）
3. 選填：`catalog.json` 群組、`docs/site-tools.json`（卡片的字與順序）、手寫說明頁
4. `CHANGELOG.md` 一行；README 的工具表一行
5. `python scripts/build.py --check` → `python scripts/build.py` → `python -m pytest -q`
6. commit、合併到 main，再照 [RELEASING.md](RELEASING.md) 上傳與重新發佈線上頁
