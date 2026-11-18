# 交接：只拿到這個 repo，就能開發一個新工具並交回

給**第一次接手的人**：外部開發者、雲端的 Claude Code session（Linux 容器）、任何 AI agent。
讀完這一頁，你應該能在一台乾淨的 Linux 上 clone → 建環境 → 跑測試 → 寫一個新工具 → 自己驗 → 開 PR。
細節（每個欄位、每支腳本）在 [CONTRIBUTING-TOOLS.md](CONTRIBUTING-TOOLS.md)；這一頁是入口和規矩，不重複那裡的表。

**一句話**：你只做「改程式＋開 PR」。合併、打包發佈、簽章、更新線上頁都是維護者的事，你不做、也做不了。

---

## 1. 這個 repo 是什麼

| | SYNOX（主程式、host；另一個 repo，主線 `dev`） | SYNOX_Libs（工具庫，這裡；主線 `main`） |
|---|---|---|
| 放什麼 | 載入器、安全檢查（五關）、AI、網頁、保管箱、**SDK 正本** | 每個工具一個資料夾 `packs/<name>/`、打包腳本、工具自己的測試、公開頁 |
| 出廠有幾個工具 | **0 個** | 全部 |
| 給使用者的東西 | 安裝檔／程式 | 一堆 `.synoxpack`（每個工具一個 zip）＋目錄 `index.json` |

- 工具（pack）在 SYNOX 的程序裡跑，用的是 SYNOX 給的介面（**SDK**，版本號 `SDK_VERSION`，正本在 SYNOX 的 `src/synox/sdk/author.py`）。
- 所以**開發、測試、打包這裡的工具都需要一份 SYNOX 原始碼**（只 import，不改它）。工具庫不複製 SYNOX 的任何程式。
- 設計理由：[adr/0001](adr/0001-tool-library-and-sdk-dependency.md)。多人（伺服器版）的規矩：[adr/0006](adr/0006-personal-and-server-mode.md)。

## 2. 目錄地圖

```
SYNOX_Libs/
  AGENTS.md、CLAUDE.md     給 AI agent 的精簡說明（指到這一頁）
  README.md                工具一覽、穩定度（stage）、給開發者的指令、公開版檢查
  CHANGELOG.md、VERSION    工具庫本身的版本（每個工具的版本在自己的 pack.json）
  catalog.json             建議群組（給人挑的組合）
  packs/<name>/            一個工具一個資料夾（下面第 3 節）
  packs/_shared/           共用程式的正本（不是工具）；scripts/sync_shared.py 原樣複製進用到的 pack.py
  costumes/                SYNOX 的外觀（theme pack），由 scripts/build_costumes.py 產生，不要手改
  legacy/                  下架的工具（不打包、不測試）
  scripts/                 build.py（打包＋index.json）、build_site.py（公開頁）、links.py、site_stamp.py、
                           public_check.py（隱私檢查）、sync_shared.py、_synox.py（找 SYNOX）…
  tests/                   pytest；conftest.py 提供 make_bot／synox_bot／host_bot（第 3.5 節）
  docs/                    文件（索引 docs/README.md）；docs/adr/ 設計決定；docs/site/ 公開頁的本機正本
  dist/                    打包輸出（git 忽略，不 commit）
```

## 3. 一個工具由哪些檔組成

```
packs/<name>/
  pack.json      宣告（必要）
  pack.py        程式：install(app, pack)（必要；kind=python）
  SKILL.md       給 AI 看：什麼時候用哪個指令（必要）
  README.md      給人看：指令、設定、資料存哪、現在的狀態（必要）
  panel.html     選用：SYNOX 網頁工具頁的小面板（先看第 8 節的已知落差）
  app/           選用：完整的網頁畫面（web.app；money 的做法，ADR-0003）
  bridge/、engine/  選用：多檔工具的子模組（money 的做法，下面 3.4）
tests/test_<name>.py   測試（必要；放 repo 的 tests/，不放在工具資料夾裡）
```

**工具資料夾裡的每個檔都會被打包**（`__pycache__` 除外），草稿、假資料、筆記不要放進去。
最簡單的範本是 `packs/notes/`（四個檔、一個 `pack.py`）；新工具建議從複製它開始。

### 3.1 `pack.json`

完整格式的正本是 SYNOX 的 `docs/pack-format.md`；每個欄位在這個 repo 的用法見 [CONTRIBUTING-TOOLS.md](CONTRIBUTING-TOOLS.md) 第 2 節。
這個 repo **比 SYNOX 嚴**：下面這些一定要明寫（`scripts/build.py --check` 與 `tests/test_gates.py` 擋），就算值跟 SYNOX 的預設一樣：

```json
{
  "name": "example",
  "title": "🧩 範例",
  "summary": "一句話說它做什麼",
  "version": "0.1.0-alpha.1",
  "stage": "test",
  "kind": "python",
  "group": "ex",
  "access": "member",
  "surfaces": ["dm"],
  "data": "user",
  "storage": "vault",
  "order": 100,
  "python": { "module": "pack" },
  "requires": { "pip": [], "config": [], "env": {}, "synox": ">=0.2", "sdk": ">=0.2,<0.3" },
  "capabilities": [],
  "skill": "SKILL.md"
}
```

| 欄位 | 這個 repo 的規定 |
|---|---|
| `name` | ＝資料夾名；小寫英數與 `-`。不准叫 `remind`（SYNOX 的系統指令） |
| `version`、`stage` | 見第 4 節 |
| `kind` | 現在只收 `python`（`test_gates.py` 擋）。想做 `cli`／`mcp`：先開 issue 問維護者，測試要一起改 |
| `access` | 不連網的本機工具 `member`；接外面服務的 `owner`（[adr/0006](adr/0006-personal-and-server-mode.md) 第 4 節） |
| `surfaces`、`data`、`storage` | 固定 `["dm"]`、`"user"`、`"vault"` |
| `capabilities` | 沒有也要寫 `[]`；本機寫檔 `["filesystem:own"]`；連網 `["network"]`。宣告什麼要跟靜態掃描結果一模一樣 |
| `requires.sdk` | 一定要寫，範圍要包含測試用的那份 SYNOX 的 `SDK_VERSION`（現在 0.2.x → `">=0.2,<0.3"`；用到 0.2.3 才有的 API 就寫 `">=0.2.3,<0.3"`） |
| `help`、`site` | 選填、建議寫：`/help` 卡片與入口頁卡片的字（CONTRIBUTING-TOOLS 2a、2b） |

### 3.2 `pack.py`（單檔工具）

```python
"""🧩 範例：/ex <文字>、/ex_list。資料經 pack.store.of(user)（保管箱，密文）。"""

import time

from isdtelekit.core.registry import WRITE, Field
from synox.packs import kit


def now():                      # 唯一碰時鐘的地方（測試會換掉）
    return time.time()


def empty():                    # 每次回新的空資料，不要拿模組層級的常數淺拷貝
    return {"items": []}


def install(app, pack):
    g = pack.group

    def add(api, chat_id, user, values):          # chat_id 只拿來回訊息；分人一律用 user
        text = values["text"].strip()
        n = pack.store.of(user).update(lambda d: (d.setdefault("items", []).append(text), len(d["items"]))[1], empty())
        api.send_message(chat_id, f"記下了（第 {n} 筆）")

    def list_(api, chat_id, user, values):
        items = pack.store.of(user).load(empty())["items"]
        api.send_message(chat_id, "\n".join(items) or "還沒有。")

    kit.command(g, "add", add, "記一筆", default=True, effect=WRITE,
                fields=[Field("text", "要記的內容", required=True)])
    kit.command(g, "list", list_, "列出全部")
```

- 指令一律 `kit.command(...)` 掛；`add`（或 `default=True`）＝`/<group>`，其他＝`/<group>_<名字>`。
- `effect`：讀的不寫（READ）、改資料 `WRITE`、救不回來的 `DANGER`（AI 呼叫一律先送確認卡）。設定密鑰之類的指令加 `ai=False`。
- 要「到時間提醒」：不要自己排程，在回覆裡附一行 `/remind_add …` 讓使用者自己決定（`calendar` 的做法）。

### 3.3 `SKILL.md`

開頭一定要有 YAML 標頭（SYNOX 讀 `name`、`description`；`synox-sdk check` 會說缺什麼）：

```markdown
---
name: example
description: 使用者想記下或列出範例項目時使用。
---

# 範例

## 什麼時候用
- 使用者說「記一下…」→ `ex__add`，`text` 放原話。
- 使用者問「我記了什麼」→ `ex__list`。
```

AI 看到的工具名是 `<group>__<指令>`（兩個底線）。只寫什麼時候用、參數怎麼填、什麼不要做；不要寫「忽略之前的指示」這類對 AI 下命令的句子。

### 3.4 多檔工具：`bridge/`、`engine/`（money 的做法）

程式一個檔放不下時：`pack.json` 寫 `"python": {"module": "pack", "package": true}`，子模組放子資料夾，`pack.py` 用**相對 import**
（`from .engine import db`）。SYNOX 不把工具資料夾放進 `sys.path`，`import engine` 這種絕對 import 找不到。
money 的分層（[packs/money/CONTRACT.md](../packs/money/CONTRACT.md)）：

- `engine/`：純邏輯（資料庫、計算），不認識 SYNOX；
- `bridge/`：SYNOX 的指令與畫面 ↔ engine，一個領域一個檔，指令表格式在 `bridge/table.py`；
- `pack.py`：很薄，`install()` 把 bridge 的表掛上去。

小工具不需要這樣分；真的要做多檔工具，先在 PR 或 issue 說明為什麼。

### 3.5 測試

`tests/conftest.py` 的三個 fixture（都不連網、不開埠、不連 Telegram、不叫 AI）：

| fixture | 是什麼 | 什麼時候用 |
|---|---|---|
| `make_bot` | SDK 的 BotHarness＋假的 `pack.store`（記憶體）＋可以換「現在」 | 只用 `pack.store` 的小工具；最快 |
| `synox_bot` | 真的 SYNOX `build()`：真的 loader、保管箱加密、權限、`mode="server"` 每人一份 | 雙人隔離、落地是密文、權限 |
| `host_bot` | 跟 SYNOX 自己測試同一個介面（可以接假 AI） | 從 SYNOX 搬來的測試、要假 AI 的 |

範例：`tests/test_notes.py`（`synox_bot`，十幾行）。接外面服務的工具一律用**假的 HTTP**（看 `tests/test_todoist.py` 怎麼換掉傳輸）。

新工具除了 `tests/test_<name>.py`，還要在這幾個既有測試各加一行（不加會紅）：

| 檔 | 加什麼 |
|---|---|
| `tests/test_gates.py` | `ALLOWED_CAPS` 加 `"<name>": [...]`（跟 pack.json 的 capabilities 一樣）；接外面服務的也加進 `CONNECT` |
| `tests/test_build.py` | `SMOKE` 加 `"<name>": ("/指令 參數", "回覆裡要有的字")`（裝進空的 SYNOX 後試一次） |
| `tests/test_access.py` | 接外面服務的才要：`CONNECT` 加名字 |
| `tests/test_isolation.py` | 本機工具建議加：`CASES` 一行（雙人隔離 S1／S2；[adr/0006](adr/0006-personal-and-server-mode.md) 第 6 節） |

`test_gates.py` 對單檔工具的 `pack.py` 有白名單：本機工具只准 import `re`、`time`、`datetime`、`dataclasses`、`collections`、
`isdtelekit`、`synox`（`os` 只有 calendar、diary 這種要寫檔的才准，而且只准特定呼叫）；`json`、`math`、`random` 這些也不在名單裡。
真的需要別的標準函式庫：在 `test_gates.py` 照 `LOCAL_EXTRA_IMPORTS`／`DIARY_IMPORTS` 的寫法加，**旁邊寫為什麼**，PR 說明裡也寫。

## 4. 命名與版本

| 東西 | 規則 |
|---|---|
| 工具名（`name`、資料夾） | 小寫英數與 `-`，例 `google-calendar`；不能跟現有的重複，不准 `remind` |
| 指令群組（`group`） | 小寫英數，16 字內，例 `note` → `/note`、`/note_list`；不能跟現有工具的 group 重複（`test_synox_integration.py` 擋） |
| 新工具的 `version` | 從 `0.1.0-alpha.1` 開始 |
| 新工具的 `stage` | `test`（只給開發者試）或 `alpha`；不准一開始就 `beta`／`stable`（升級條件見 README「穩定度」） |
| 改了工具裡任何檔 | 升 `version`（含 pack.json、README、SKILL.md）；**沒在真的 SYNOX／Telegram 上試過的版本一律帶 `-alpha.N`**，再改就 `-alpha.2`… |
| 已經發過正式版的號碼 | 不能再加 `-alpha`（`0.1.0` → `0.1.0-alpha.1` 會被 SYNOX 當成降級）；要往上升號再加，例 `0.1.1-alpha.1` |
| CHANGELOG | `[Unreleased]` 底下寫一行：工具名、版本、做了什麼、為什麼；寫「alpha，尚未實機驗證」 |
| `VERSION`（工具庫本身） | **不要動**：只有維護者要開新的發佈頁時才改 |
| 分支 | `feat/<工具名>`、`fix/<工具名>-<什麼>`、`docs/<什麼>`；雲端 session 自己指定的分支名也可以 |

詳細：[CONTRIBUTING-TOOLS.md](CONTRIBUTING-TOOLS.md) 第 5 節。

## 5. 從零到交件（Linux）

每一行都可以直接貼到 bash。Windows 的寫法在 5.8。需要 **Python 3.11 以上**、`git`；`node` 選用（沒有的話幾條 JS 測試會跳過）。

### 5.1 clone 兩個 repo（並排放）

```bash
python3 --version                       # 要 3.11 以上
mkdir -p ~/work && cd ~/work             # 已經 clone 好了（雲端 session 通常是）：改成 cd 到那個 clone 的上一層
git clone <這個 repo 的網址> SYNOX_Libs   # 已經 clone 好了就跳過；資料夾名不是 SYNOX_Libs 的話，下面的 SYNOX_Libs 都換成它
# SYNOX 跟這個 repo 在同一個 GitHub 帳號底下，repo 名字是 SYNOX：
SYNOX_URL=$(git -C SYNOX_Libs remote get-url origin | sed 's#SYNOX_Libs\(\.git\)\?$#SYNOX.git#')
git clone --branch dev "$SYNOX_URL" SYNOX
```

- **放在 `SYNOX_Libs` 旁邊、資料夾叫 `SYNOX`**：這樣就算忘了設 `SYNOX_HOME`，`scripts/_synox.py` 的後備位置與 `tests/test_costumes.py` 也找得到。
- 雲端 session 的 `origin` 可能是 git 代理的網址；clone 不下來就改用 GitHub 上公開的 SYNOX repo 網址（README 開頭的發佈頁連結看得到帳號）。
- SYNOX 用 `dev`（主線）。要的是 `SDK_VERSION` 0.2.3 以上的版本（money、google-sheets 宣告 `>=0.2.3`）；`main` 現在也是 0.2.4。
- **SYNOX 只讀**：不要在 SYNOX 改檔、開分支、commit。發現 SYNOX 那邊的問題，寫在 PR 說明或 issue 裡。

### 5.2 建環境

```bash
cd ~/work                                         # 5.1 的那個上一層資料夾
python3 -m venv SYNOX/.venv
SYNOX/.venv/bin/pip install -e "./SYNOX[dev]"     # 裝 SYNOX（requests、cryptography、websocket-client）＋pytest
export SYNOX_HOME="$PWD/SYNOX"                    # 明寫 SYNOX 在哪（找的順序見 README「找 SYNOX 的順序」）
export SYNOX_SRC="$SYNOX_HOME/src"                # tests/test_costumes.py 最後一條用它找 SYNOX 原始碼
export PYTHONUTF8=1
PY="$SYNOX_HOME/.venv/bin/python"
```

- 不需要 `ntu_easy_llm`、Telegram token、AI 金鑰：測試全部用假的。
- 不能 `pip install -e` 的環境：`pip install requests cryptography websocket-client pytest` 也可以，`scripts/_synox.py` 會把 `$SYNOX_HOME/src`、`$SYNOX_HOME/vendor` 加進 `sys.path`
  （但這時 `python -m synox.sdk` 要自己帶 `PYTHONPATH="$SYNOX_HOME/src:$SYNOX_HOME/vendor"`）。

### 5.3 跑一次測試，記下基準

```bash
cd ~/work/SYNOX_Libs                              # 這個 repo
"$PY" -m pytest -q 2>&1 | tail -5                 # 記下 passed／skipped／failed 的數字
```

開工前先跑，**把結果記下來**：之後才分得出哪些是本來就壞的、哪些是你弄壞的。本來就紅的不要順手修，寫進 PR 說明。

### 5.4 開分支

```bash
git fetch origin
git switch -c feat/<工具名> origin/main
```

### 5.5 做

```bash
cp -r packs/notes packs/<工具名>         # 從最簡單的範本開始；改 pack.json（name、title、group…）、pack.py、SKILL.md、README.md
"$EDITOR" tests/test_<工具名>.py          # 主要指令跑一輪（第 3.5 節）
```

- 小步 commit，訊息寫**做了什麼、為什麼**。
- 要共用 `packs/_shared/` 的程式（中文日期 `zhdate`、Google 登入 `googleauth`）：不要 import，改 `scripts/sync_shared.py` 的對照、跑它複製進來。
- 範例、測試資料一律假的（`me@example.com`、`<使用者>`、`0123…`），見第 6.2 節。

### 5.6 自己驗（PR 前全部要過）

```bash
"$PY" -m synox.sdk check packs/<工具名>          # SYNOX 安裝時的第 1 關（宣告）＋第 2 關（靜態掃描）；最後一行要是「✅ 可以裝」，結束碼 0
"$PY" -m pytest -q tests/test_<工具名>.py        # 你的測試
"$PY" scripts/build.py --check                   # 這個 repo 的規定：requires.sdk、stage、入口欄位、catalog、公開頁是不是最新
"$PY" scripts/build.py <工具名> --out /tmp/try   # 試打包這一個（用 SYNOX 的 zip_pack；index.json 只列它；不動 repo 的檔）
"$PY" scripts/build_site.py                      # 新工具：入口頁多一張卡片、產生 docs/site/<工具名>.html（要 commit）
"$PY" -m pytest -q                               # 全部；跟 5.3 的基準比，只能多、不能少
"$PY" scripts/public_check.py                    # 隱私檢查（第 6.2 節）；有發現＝結束碼 1
```

- `synox.sdk check` 的結束碼：0＝可以裝；2＝安裝一定會被擋（下面列原因）。
- `build.py --check` 說公開頁不是最新：跑 `"$PY" scripts/build_site.py`，再 commit `docs/site/index.html` 與 `docs/site/<工具名>.html`。
- 不要跑沒帶工具名、沒帶 `--out` 的 `scripts/build.py`：它會重寫 README 與每個公開頁的下載連結和頁面版本（那是發佈時維護者做的）。

### 5.7 交件：開 PR

1. `git push -u origin feat/<工具名>`，對 `main` 開 PR（雲端 session 照它自己的流程開 PR 即可）。
2. **不要自己合併、不要發版、不要簽章、不要上傳發佈頁、不要改 `VERSION`、不要推 `dist/`**。
3. PR 說明寫：
   - 這個工具做什麼、指令一覽（跟 README 同一份）；
   - `stage`、`version`，以及「alpha，尚未實機驗證」（你在雲端只能跑自動測試，沒在真的 SYNOX／Telegram 上試過）；
   - 5.3 的基準與 5.6 跑完的數字（passed／skipped／failed）、`synox.sdk check` 的最後幾行、`public_check.py` 的結果；
   - 改了哪些既有檔（`tests/test_gates.py` 加了什麼 import、為什麼…）；
   - 還不確定、要維護者決定的事。
4. 維護者會：看 diff、在自己的電腦跑測試、`git merge --no-ff` 合進 `main`、之後某次發佈時打包＋簽章＋上傳（[RELEASING.md](RELEASING.md)）。

### 5.8 Windows 的寫法

| Linux | Windows（cmd） |
|---|---|
| `SYNOX/.venv/bin/python` | `SYNOX\.venv\Scripts\python.exe` |
| `export SYNOX_HOME="$PWD/SYNOX"` | `set SYNOX_HOME=%CD%\SYNOX` |
| `export SYNOX_SRC="$SYNOX_HOME/src"` | `set SYNOX_SRC=%SYNOX_HOME%\src` |
| `export PYTHONUTF8=1` | `set PYTHONUTF8=1` 與 `set PYTHONIOENCODING=utf-8`（主控台是 cp950 時一定要，不然印中文／emoji 會當掉） |
| `cp -r packs/notes packs/x` | `xcopy /E /I packs\notes packs\x` |
| `/tmp/try` | `%TEMP%\try` |

SYNOX 在 Windows 上也可以雙擊 `Setup.bat` 建 `.venv`；之後的指令都一樣，`python` 換成 `<SYNOX>\.venv\Scripts\python.exe`。

## 6. 規則與閘門

### 6.1 工具可以 import 什麼

**SDK（合約）只有這些**，其餘 SYNOX 內部模組都不算、不准用（SYNOX 升級時會壞，而且違反「工具只靠合約」）：

| 可以 | 是什麼 |
|---|---|
| `from synox.packs import kit` | `kit.command`、`kit.positive_int`、`kit.amount`、`kit.redact_in_logs`… |
| `from isdtelekit.core.registry import READ, WRITE, DANGER, Field` | 效果等級與欄位 |
| `from isdtelekit.core.channel import buttons` | 按鈕（現有工具有一個在用） |
| `install(app, pack)` 拿到的 `pack` | `pack.store`／`pack.store.of(user)`、`pack.path_of(user, …)`、`pack.secret(name, user)`、`pack.web_route(…)`、`pack.group` |

- **不准**：import 別的工具（`synox_pack_<別人>` 一律拒裝）、import `synox` 的其他模組（`synox.app`、`synox.vault`、`synox.config`、`synox.schedule`…；測試可以用，工具不行）。
- 不准連網、開別的程式、動態執行（`requests` 只准接外面服務、`access: owner` 的工具用；`subprocess`、`socket`、`eval`、`exec`、
  `__import__`、`importlib`、`pickle`、`os.system`、`os.environ` 一律不准）。`test_gates.py` 的 `FORBIDDEN_IMPORTS`、`FORBIDDEN_TEXT` 是清單。
- 工具之間要共用程式：放 `packs/_shared/`，用 `scripts/sync_shared.py` 原樣複製進去。

### 6.2 安全、隱私、多人

- **多人契約**（[adr/0006](adr/0006-personal-and-server-mode.md) 第 2 節，測試會擋大部分）：
  - 個人資料只經 `pack.store.of(user)`；個人檔案只經 `pack.path_of(user, …)`；不准自己組路徑、不准系統暫存資料夾；
  - 分人的鍵只能是 `user`，**不准用 `chat_id`**；
  - 模組層級不放會變的狀態（沒有 dict／list 快取、沒有共用的 HTTP session）；
  - 工具不准自己判斷個人版／伺服器版、不准自己判斷「是不是擁有者」（交給 `pack.json` 的 `access`）；
  - 密鑰（token、密碼）不出現在回覆、AI 的工具結果、log；設定密鑰的指令 `ai=False`。
- **測試不連網、不開埠、不連 Telegram、不叫 AI**。接外面服務的一律假 HTTP；需要時間的換 `now()`。
- **假資料**：範例、測試、文件、commit 訊息一律假的。不要放真的人名、信箱、電話、帳號、金額、地址、本機路徑（`/home/<你>`、Windows 的使用者資料夾）、token。
  `scripts/public_check.py` 會掃（本機絕對路徑、使用者名稱、email、像 token 的字串、私人資料檔），`tests/test_public_check.py` 每次跑測試都檢查；
  真的需要留的範例行加 `public-check: allow` 並寫為什麼。
- **公開 repo**：這個 repo 是公開的，你 push 的每一行任何人都看得到。

### 6.3 簽章（`pack.sig.json`）

- 官方工具的 `.synoxpack` 由**維護者**在自己的電腦用 Ed25519 私鑰簽（`synox-sdk sign`，zip 裡多一個 `pack.sig.json`）；
  SYNOX 安裝時驗出「✅ 官方」。規格：SYNOX 的 `docs/adr/0028-pack-signatures-official-badge.md`、`docs/signing.md`；
  工具庫這邊的發佈步驟：[RELEASING.md](RELEASING.md)「簽章」（寫這段時 SYNOX 還沒放官方公鑰，所以現在所有工具都顯示「⚠️ 不是官方」）。
- **私鑰只有維護者有，不會給任何人、任何雲端 session**。你不簽、也不需要簽。
- 交件時：**不要**把 `pack.sig.json` 放進 `packs/<name>/`（`zip_pack` 會丟掉它，簽完再改任何檔簽章都會失效）；
  **不要**用自己的鑰匙簽官方工具；**不要**在 repo 裡放任何 `.pem` 或私鑰。
- 你想先給別人試你的工具：可以自己 `"$PY" -m synox.sdk zip packs/<name> --out /tmp/try` 打包給對方，對方會看到「⚠️ 不是官方」（沒簽）——這是正常的。

### 6.4 誰做什麼

| 事 | 誰 |
|---|---|
| 寫工具、寫測試、開 PR | 你 |
| 看 PR、在自己的電腦跑測試、`--no-ff` 合進 `main` | 維護者 |
| `stage` 往上升（`alpha` → `beta` → `stable`） | 維護者（要真的環境用過，README「穩定度」） |
| `scripts/build.py` 全部打包、`dist/index.json`、`SHA256SUMS` | 維護者（發佈時） |
| 簽章 | 維護者（只在他自己的電腦） |
| 上傳 GitHub 發佈頁、改 `VERSION`、改 `scripts/links.py` 的 `LINK_MODE` | 維護者（[RELEASING.md](RELEASING.md)） |
| 線上頁（claude.ai Artifact）重新發佈、登記 `docs/artifacts.json` | 維護者（[ARTIFACTS.md](ARTIFACTS.md)） |
| SYNOX 那邊的改動（`catalog.py` 的說明頁連結等） | SYNOX 的維護者；你在 PR 裡寫「需要 SYNOX 改什麼」 |

## 7. 雲端 session（Linux 容器）要注意的

- **沒有 Windows 的東西**：`.bat`、`Setup.bat`、PowerShell、Windows 的 DPAPI 都用不到。測試用的保管箱是假金鑰（`Vault(b"k" * 32, …)`），跟作業系統無關。
  文件裡寫 `<SYNOX>\.venv\Scripts\python.exe` 的地方，你一律換成 `$SYNOX_HOME/.venv/bin/python`。
- **會跳過的測試**（不是失敗）：
  - 沒有 `node`：`tests/money/` 裡幾條跑網頁 JS 的測試（`skipif(NODE is None)`）；
  - 找不到 SYNOX 原始碼：`tests/test_costumes.py` 的最後一條（設 `SYNOX_SRC`，或把 SYNOX 放在旁邊的 `../SYNOX`）；
  - SYNOX 太舊（沒有多檔工具）：`tests/money/` 的兩條。照 5.1 用 `dev` 就不會跳。
  跳過的數字寫進 PR 說明。
- **不需要、也不准拿**任何私人資料或金鑰：沒有 Telegram token、AI 金鑰、簽章私鑰、使用者的資料檔（`data/`、`*.enc`、`config.json`）。
  測試要的東西全部是假的；如果某件事看起來非要真的帳號才能做，停下來寫進 PR 說明，不要自己去找。
- **不要在容器裡跑真的 SYNOX 伺服器、開對外的埠、連真的外部服務**。要看工具在 SYNOX 裡的樣子，用 `synox_bot` 測試就好。
- **私人的計畫與設計稿不在這個 repo**：如果你的任務有那種文件，維護者會另外經加密分支提供（解密方法寫在那條分支自己的 README）；
  解出來的內容只用來工作，**不准寫進這個 repo 的檔案、commit 訊息、PR 說明**，工作完刪掉明文。
- 網路：`pip install` 與 `git clone` 要連網；測試本身不連網（連網的程式碼一律被假傳輸換掉）。

## 8. 已知落差（文件或測試跟現在的 SYNOX 對不上，還沒修）

| 落差 | 影響 | 現在怎麼辦 |
|---|---|---|
| **面板的 `fetch` 網址**：SYNOX 從 ADR 0028 §5（2026-11-16）起把 `panel.html` 隔開，面板只能打**相對網址** `fetch("api/<route>")`，舊的 `fetch("/api/packs/<name>/<route>", {credentials: …})` 會被擋（401）。但這個 repo 的 5 個面板（todo、money、guitar、calendar、bullet）還是舊寫法，`tests/test_gates.py` 的 `panel_problems` 也**只收舊寫法** | 照現在的測試寫的面板，在新的 SYNOX 上讀不到資料 | 新工具**先不要做 `panel.html`**；真的需要，在 PR 說明提出，等維護者決定怎麼改測試與舊面板 |
| ADR-0006 第 2、3 節的 M5（面板只打 `/api/packs/<自己>/…`、帶 `credentials`） | 同上，是舊的規則 | 以 SYNOX 的 `docs/pack-format.md`「`web.panel`」為準 |
| `tests/test_costumes.py` 找 SYNOX 原始碼只看 `SYNOX_SRC` 與 `../SYNOX`，不看 `SYNOX_HOME` | SYNOX 不放旁邊又沒設 `SYNOX_SRC` 時，那一條會跳過 | 照 5.2 設 `SYNOX_SRC` |

## 9. 交件前檢查清單

- [ ] `packs/<name>/`：`pack.json`（第 3.1 節每個欄位）、`pack.py`、`SKILL.md`（有 YAML 標頭）、`README.md`；資料夾裡沒有草稿與真資料
- [ ] `version` 是 `0.1.0-alpha.1` 之類（帶 `-alpha.N`）、`stage` 是 `test` 或 `alpha`、`requires.sdk` 有寫
- [ ] `tests/test_<name>.py`；`test_gates.py`（`ALLOWED_CAPS`）、`test_build.py`（`SMOKE`）各一行；接外面服務的加進 `CONNECT`
- [ ] `CHANGELOG.md` 的 `[Unreleased]` 一行；`README.md` 的工具表一行（說明頁那格寫「（還沒有說明頁）」）
- [ ] `"$PY" scripts/build_site.py` 跑過、`docs/site/index.html` 與 `docs/site/<name>.html` 一起 commit
- [ ] 5.6 的每一條都過；全部測試跟基準比沒有變少
- [ ] `public_check.py` 沒有發現；diff 裡沒有本機路徑、真名、信箱、token、`.pem`
- [ ] 沒動 `VERSION`、`dist/`、`scripts/links.py` 的 `LINK_MODE`、別的工具的檔（除非 PR 說明寫了為什麼）
- [ ] 開 PR、說明照 5.7 第 3 點；不合併、不發版、不簽章
