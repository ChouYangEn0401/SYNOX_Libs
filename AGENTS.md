# AGENTS.md：給 AI agent 的精簡說明

這個 repo 是 **SYNOX_Libs**：SYNOX（另一個 repo）的工具庫。每個工具（pack）一個資料夾 `packs/<name>/`，
在 SYNOX 的程序裡跑；開發、測試、打包都要一份 SYNOX 原始碼（只 import，不改它）。這是**公開 repo**。

**先讀** [docs/HANDOFF.md](docs/HANDOFF.md)（從 clone 到開 PR 的完整步驟、規矩、雲端 session 注意事項），
細節查 [docs/CONTRIBUTING-TOOLS.md](docs/CONTRIBUTING-TOOLS.md)，文件索引在 [docs/README.md](docs/README.md)。

## 怎麼跑（Linux）

```bash
export SYNOX_HOME=<SYNOX 的 clone>          # 建議放在這個 repo 旁邊、叫 SYNOX（HANDOFF 第 5 節）
export SYNOX_SRC="$SYNOX_HOME/src" PYTHONUTF8=1
PY="$SYNOX_HOME/.venv/bin/python"           # 先 python3 -m venv "$SYNOX_HOME/.venv" && "$SYNOX_HOME/.venv/bin/pip" install -e "$SYNOX_HOME[dev]"
"$PY" -m pytest -q                          # 全部測試（不連網、不開埠、不叫 AI）；開工前先跑一次記基準
"$PY" -m synox.sdk check packs/<name>       # SYNOX 安裝時的第 1、2 關；結束碼 0＝可以裝
"$PY" scripts/build.py --check              # 這個 repo 的規定（requires.sdk、stage、入口欄位、公開頁）
"$PY" scripts/public_check.py               # 隱私檢查（本機路徑、名字、email、token）
```

## 一定要守的

- **只開 PR**：不合併、不發版、不簽章、不上傳發佈頁、不改 `VERSION`、不 commit `dist/`。
- **不改 SYNOX**（另一個 repo）；SYNOX 那邊要改的寫在 PR 說明。
- 工具只用 SDK：`synox.packs.kit`、`isdtelekit.core.registry`（`READ`／`WRITE`／`DANGER`／`Field`）、`install(app, pack)` 給的 `pack`；
  不 import 別的工具、不 import `synox` 的其他模組；不連網（接外面服務、`access: owner` 的除外）、不開程式、不動態執行。
- 個人資料只經 `pack.store.of(user)`、檔案只經 `pack.path_of(user, …)`；不用 `chat_id` 分人；模組層級不放會變的狀態。
- 改了工具就升 `version`；沒實機試過的版本帶 `-alpha.N`；CHANGELOG `[Unreleased]` 一行。
- 測試不連網、不開埠、不叫 AI；接外面服務用假 HTTP。
- **全部用假資料**：不寫真名、信箱、帳號、金額、本機路徑、token、私鑰。不需要也不准拿任何私人資料或金鑰。
- 對 SYNOX `dev` 的測試應該全綠；`synox_bot`／`host_bot` 會先替每個工具寫 `packs.lock`（SYNOX 不載入沒上鎖的工具），不要在測試裡繞過它，也不要為了變綠改既有測試的期望。
- `panel.html`（選用）是隔開的小頁：讀資料只寫相對網址 `fetch("api/<route>")`（前面不加 `/`、不帶 `credentials`、route 在 `web.api` 宣告過），字用 `textContent`；舊的 `/api/packs/<name>/…` 會 401，測試會擋。範例在 HANDOFF 3.6。
- 小步 commit，訊息寫做了什麼、為什麼。
