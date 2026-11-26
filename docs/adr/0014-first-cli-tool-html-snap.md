# ADR-0014：第一個 kind=cli 工具（html-snap 網頁轉長圖）——子程序＋自動建環境，以及工具庫要跟著改的地方

- 狀態：**提案（v0 已做）**（2026-11-26：`packs/html-snap` 0.1.0-alpha.1，工具本身在 Windows＋Edge 直接跑過，尚未在 SYNOX 實機驗證）。
- 範圍：新工具 `packs/html-snap`；工具庫的打包與測試基礎設施對 kind=cli 的最小支援。不改 SYNOX 核心。
- 讀者：之後接手開發的人與 Claude。

## 1. 背景

工具庫到這一版為止全部是 kind=python（在 SYNOX 的程序裡跑）。網頁轉長圖要用 Playwright（開瀏覽器）與 Pillow（拼圖），
這兩個都是有原生程式的第三方套件：放進 SYNOX 的程序裡跑，等於要 SYNOX 自己的環境裝它們，而且瀏覽器卡住會拖到 SYNOX。
SYNOX 的 kind=cli 正好解決這兩件事：每次呼叫開一個子程序、有逾時；`runtime.python: "auto"` 讓 SYNOX 在安裝時替這個工具
建一個獨立的 venv（`data/envs/html-snap/venv`），照 `requires.pip` 釘死的版本裝套件。

## 2. 決定

1. **kind=cli＋`runtime.python: "auto"`**：`cli.argv` 是 `["{python}", "./tool.py"]`，工具用附在旁邊的單檔 `synox_sdk.py`
   （SDK 0.2.5）的 `@tool` 寫，SYNOX 讀最後一行 JSON 信封（成功 `message`＋`data`；失敗是失敗信封 v1：`fail(code, reason, remedy=…)`）。
   pack.json 的 `cli.commands` 用 `synox-sdk init --from tool.py` 產生後手動補 `positional`、`timeout`、欄位的 `help`；
   測試比對兩邊一致（`test_pack_json_commands_match_the_tools`）。
2. **信任層級 T2**：自動建環境的工具一律是 T2，`publisher` 寫 `SYNOX_Libs`，安裝時要一字不差輸入。
   `capabilities` 照實宣告 `network`、`subprocess`、`filesystem:own`：掃描只看得到 `filesystem:own`、`network`（`urllib.parse`），
   開瀏覽器、連網是 Playwright 做的，掃不到，所以登記在 `tests/test_gates.py` 的 `DECLARED_BEYOND_SCAN`（usb-keys 的同一種情況）。
3. **`requires.pip` 釘版本、不附雜湊**：`playwright==1.65.2`、`greenlet==3.5.6`（playwright 唯一的相依；SYNOX 用 `--no-deps`）、`pillow==13.0.0`。
   SYNOX 的雜湊規則是「每一項都要有、一行最多 16 個」，而 pillow 13.0.0 在 PyPI 有 82 個 wheel、greenlet 有 78 個（每個 Python 版本×平台一個）：
   只挑幾個平台附雜湊，其他平台就裝不起來。所以這一版只釘版本，T2 安裝報告會紅字寫「版本釘選不等於來源可信」。之後若 SYNOX 放寬上限，再補。
4. **資料放 `{user_data_dir}/html-snap/`**：`settings.json`（設定）、`shots/`（截圖）。kind=cli 沒有 `pack.store`，
   所以 `storage` 寫 `"plain"`（照實：設定與圖都不加密）。工具從 SDK 的 `context().user_data_dir`（環境變數 `SYNOX_USER_DATA_DIR`）拿資料夾。
5. **渲染可以換**：`snapcore.py` 是純邏輯（設定、網址與路徑檢查、尺寸上限、切片、拼接、存檔、找瀏覽器），
   `playwright_render(job)` 是唯一開瀏覽器的地方；`tool.RENDERER` 測試時換成假的。playwright、PIL 都在函式裡才 import，
   沒裝也能載入（`status` 才說得出缺什麼；測試環境不用裝）。
6. **安全**：只收 http／https 與使用者資料夾裡的 .html；瀏覽器每個請求再過 `request_allowed`（截網址不准讀本機檔；
   截本機檔只准讀那個 .html 的資料夾）；高度、總像素、JPEG 邊長有上限；每片不超過 Chromium 單張上限；開網頁有逾時；
   不自動下載瀏覽器（找不到時回人話與那一行安裝指令）。`access: owner`。

## 3. 工具庫要跟著改的地方（kind=cli 的最小支援）

工具庫的共用測試原本假設每個工具都是 kind=python（有 `pack.py`、`install(app, pack)`）。這一版只做到「kind=cli 的工具也能過同一套關卡」，
不另外開一套平行的規則：

| 檔 | 改了什麼 |
|---|---|
| `tests/conftest.py` | `CLI_PACKS`（pack.json `kind` 是 cli 的工具，自動算）；`PYTHON_PACKS`＝其餘公開工具。`build_fake_envs`：替 `runtime.python: "auto"` 的工具放一份**只有標準函式庫**的 venv（整個測試程序建一次、複製過去）並用 SYNOX 的 `envs.write_state` 寫「建好了」的狀態檔，不連網、不裝套件；`approve_all`（synox_bot／host_bot 上鎖前）先叫它 |
| `tests/test_gates.py` | `pack.py` 的 import 白名單只看 `PYTHON_PACKS`；kind=cli 的 `storage` 要是 `plain`；M3（chat_id）、M4（模組層級狀態）照樣看 cli 工具自己的 .py（附的 `synox_sdk.py` 不算；html-snap 的測試掛勾 `RENDERER` 登記在 `M4_HOOKS`）；新的 `test_cli_tool_shape`：`{python} ./<腳本>.py`、沒有 `pack.py`、有 `publisher`、`requires.pip` 釘死、`health.check` 是唯讀免參數的指令、附的 `synox_sdk.py` 跟 SYNOX 的一字不差、自己的 .py 不開程式不動態執行。第 1、2 關、`requires.sdk`、`stage`、能力宣告（`ALLOWED_CAPS`、`DECLARED_BEYOND_SCAN`）照舊每個工具都跑 |
| `tests/test_build.py` | 空的 SYNOX 一個一個裝：報告是 T2 的照安裝頁帶 `trust_publisher`（publisher），裝好後 `build_fake_envs` 再重開；`SMOKE` 加 `/snap_status`（SYNOX 真的開子程序跑 `tool.py status`；假環境沒有 playwright，回覆照實說沒裝，不開瀏覽器、不連網） |
| `tests/test_access.py` | `OWNER_ONLY` 加 html-snap |

`scripts/build.py` 不用改：它本來就認得 cli 工具附的 `synox_sdk.py`（版本要在 `requires.sdk` 範圍內），打包走 SYNOX 的 `zip_pack`（第 1、2 關）。

## 4. 還沒做、要決定的

- **圖直接回到聊天**：SDK 0.2.5 的 `kit.send_image` 只給 kind=python。kind=cli 的信封沒有「附圖」的欄位，所以現在只回路徑。
  要做得 SYNOX 那邊加（例如成功信封帶 `images: [{path, alt}]`，路徑限在 `{user_data_dir}/<pack>/` 裡、走同一套圖片檢查）。
- **雜湊**：見第 2 節第 3 點。
- **資料版本**（`data_schema`）：設定檔只有一層鍵值，這一版不宣告；之後改格式要宣告並實作 `migrate` 子命令。
- **伺服器版的沙盒**：T2 工具在伺服器版一定要真的沙盒，瀏覽器在沙盒裡開不開得起來還沒試過。
