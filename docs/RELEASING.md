# 發佈：把 dist/ 放上 GitHub 發佈頁，下載連結永遠是最新版

## 流程（先看這裡）

```
(a) 加／改工具 ─→ (b) 打包 ─→ (c) 測試 ─→ (d) commit、合併到 main（＝發佈 commit）
   packs/<名字>/     build.py     pytest        │
   升 version                                   ▼
   CHANGELOG        (d2) 擁有者電腦：從發佈 commit 重新 build → sign_dist.py 簽＋重算 → --check
                                                │
                                                ▼
                    (e) 備份 index → 上傳檔 → 最後換 index.json → tag＋發佈頁（pre-release）→ 每個連結 200
                                                │
                                                ▼
                    (f) 重新發佈線上頁 ─→ (g) 標成 Latest 之後：LINK_MODE 切到 latest（只做一次）
```

| 步驟 | 做什麼 | 指令／文件 |
|---|---|---|
| (a) | 加或改工具；改了就升 `pack.json` 的 `version`、CHANGELOG 一行；要開新 tag 才改 `VERSION` | [CONTRIBUTING-TOOLS.md](CONTRIBUTING-TOOLS.md) |
| (b) | 全部打包：`dist/`＋入口頁重新產生＋每頁下載連結改成現在的版本＋重新蓋頁面版本 | `python scripts/build.py`（先 `python scripts/build.py --check`） |
| (c) | 測試（`test_links`、`test_site_pages`、`test_build_site`、`test_sign_dist` 一定要過） | `python -m pytest -q` |
| (d) | commit（`dist/` 不進 git）、合併到 main。合併出來的那個 commit 就是**發佈 commit**：tag 會指到它 | |
| (d2) | **只在擁有者自己的電腦**：checkout 發佈 commit、重新 `build.py`、`sign_dist.py` 簽每個 `.synoxpack` 並用**簽過的檔**重算 `index.json`／`SHA256SUMS`／tools zip、`sign_dist.py --check` | 下面「簽章」 |
| (e) | 備份發佈頁上的 `index.json` → 上傳 `.synoxpack`、zip、`SHA256SUMS` → **最後**才換 `index.json` → tag 指到發佈 commit、發佈頁標 pre-release → 每個連結 HEAD 200 | 下面「上傳到發佈頁」 |
| (f) | 列出改到的頁、用 Artifact 重新發佈到同一個網址、看頁面版本一樣 | `python scripts/build_site.py --list-republish --since <上一個 tag>`；[ARTIFACTS.md](ARTIFACTS.md) |
| (g) | 發佈頁標成 Latest 之後（只做一次）：`LINK_MODE = "latest"`、重新產生頁面、重新發佈全部線上頁 | 下面「標成 Latest」 |

檢查清單（照順序）：

- [ ] 改到的工具都升了 `version`，CHANGELOG 有寫；開新 tag 的話 `VERSION` 也改了
- [ ] `python scripts/build.py --check` ✅、`python scripts/build.py` 跑完（印出「公開頁產生」「下載連結更新」的頁），改到的頁一起 commit
- [ ] `python -m pytest -q` 全過
- [ ] 合併到 main（`git merge --no-ff`），記下**發佈 commit**：`git rev-parse HEAD`
- [ ] **tag 跟 main 的位置**（擁有者電腦）：`git rev-parse HEAD` ＝ 發佈 commit；重新 `build.py` 之後 `git status --short` 是空的
      （有東西變了＝發佈 commit 不是 build 完的樣子，先修、合併、換新的發佈 commit）；
      `git log --oneline <發佈 commit>..origin/main -- packs costumes catalog.json VERSION scripts` 是空的（發佈 commit 之後沒有會影響 `dist/` 的 commit）
- [ ] `sign_dist.py --key <私鑰路徑>` ✅（簽了幾個、驗了幾個都是 official）→ `sign_dist.py --check` ✅
- [ ] 備份 `index.json` → 上傳檔、zip、`SHA256SUMS` → 最後換 `index.json`
- [ ] tag `v<VERSION>` 指到發佈 commit（`git rev-parse v<VERSION>^{commit}` ＝ 發佈 commit）、發佈頁是 pre-release
- [ ] 每個連結 HEAD 都 200
- [ ] `--list-republish` 列出的每一頁都重新發佈了，線上頁尾的頁面版本＝本機
- [ ] 新工具第一次上線：網址登記進 `docs/artifacts.json`、`build_site.py`、再發佈一次（[ARTIFACTS.md](ARTIFACTS.md)「新增一頁」）

擁有者的規矩：**每個公開頁**（`docs/site/*.html`、衣櫃頁 `docs/site/wardrobe.html`、`README.md`）上的下載連結，
永遠指到最新版。做法是「版本只寫在一個地方」：

| 東西 | 唯一來源 |
|---|---|
| 工具／外觀的版本 | 各自的 `pack.json`（`packs/<name>/`、`costumes/<id>/`） |
| 工具庫版本、發佈 tag | `VERSION`（tag ＝ `v` ＋ VERSION，例 `v0.2.0`） |
| 網址怎麼組 | `scripts/links.py`（`LINK_MODE`、`asset_url`、`pack_url`、`tools_url`） |

頁面裡看得到的版本（`money · v0.3.1`、`money-0.3.1.synoxpack`、`tools-0.2.0.zip`、`發佈頁 v0.2.0`）都是
`scripts/links.py` 寫進去的，**不要手改**；衣櫃頁整頁由 `scripts/build_costumes.py page` 產生、入口頁 `docs/site/index.html`
整頁由 `scripts/build_site.py` 從 pack.json 產生（新工具自動出現，[CONTRIBUTING-TOOLS.md](CONTRIBUTING-TOOLS.md)），連結都走 `links.py`。
`tests/test_links.py` 會擋：頁面寫的版本跟 pack.json 不同、連到 build 不會產生的檔、連到別的 tag、
`tools-<VERSION>.zip` 過期、衣櫃頁不是產生器現在的輸出、index.json 少了工具或 26 套外觀。

## 發佈頁上的檔（＝`scripts/build.py` 全部打包時的 dist/）

| 檔 | 說明 |
|---|---|
| `<name>-<version>.synoxpack` | 每個工具（`packs/` 底下每個，現在 12 個）與每套外觀（26 套）帶版本的檔 |
| `<name>.synoxpack` | 同一份內容、不帶版本的別名（給 `releases/latest/download/` 用） |
| `tools-<VERSION>.zip`、`tools.zip` | 全部工具：`tools/*.synoxpack` ＋ 只列工具的 `tools/index.json`；`tools.zip` 是別名 |
| `index.json` | 目錄：每個工具與外觀的版本、檔名、sha256、工具卡片的一句話 `line`（外觀 `kind: "theme"`、`collections.wardrobe`） |
| `SHA256SUMS` | 上面每個檔的 sha256（`sha256sum -c SHA256SUMS` 可以驗） |

## 連結的兩種模式（`scripts/links.py` 的 `LINK_MODE`）

- **`"tag"`（現在）**：`https://github.com/ChouYangEn0401/SYNOX_Libs/releases/download/v0.2.0/money-0.3.1.synoxpack`
  ——帶版本的檔名、掛在現在的 tag。因為 v0.2.0 是 **pre-release**，GitHub 的 `releases/latest` 不會指到 pre-release
  （SYNOX 那邊也是釘 v0.2.0）。
- **`"latest"`**：`https://github.com/ChouYangEn0401/SYNOX_Libs/releases/latest/download/money.synoxpack`
  ——別名，網址永遠不用改。擁有者把發佈頁標成 Latest 之後才切（下面「標成 Latest」）。

兩種模式 build 產生的檔都一樣（帶版本＋別名都有），切換只改那一個常數、重新產生頁面。

## 每次發佈的步驟

在工具庫根目錄（Windows 把 `python` 換成 `<SYNOX>\.venv\Scripts\python.exe`）：

```bash
# 1. 改版本：packs/<name>/pack.json（＋CHANGELOG）；要開新的 tag 才改 VERSION
# 2. 全部打包：清掉舊的 dist/、打出全部檔、SHA256SUMS，重新產生入口頁（scripts/build_site.py：新工具自動出現），
#    並把每個公開頁的下載連結改成現在的版本、重新蓋頁面版本
python scripts/build.py
python scripts/build_costumes.py page && python scripts/site_stamp.py wardrobe.html   # 衣櫃頁的版面有改才需要
# 3. 測試（tests/test_links.py、tests/test_site_pages.py、tests/test_build_site.py 一定要過）
python -m pytest -q
# 4. commit；頁面有改的話，線上版 Artifact 也要重新發佈同一個網址（docs/ARTIFACTS.md）：
python scripts/build_site.py --list-republish --since HEAD~1    # 例：跟上一個 commit 比有改的頁 → 線上網址
# 5. 合併到 main 之後，擁有者電腦：從發佈 commit 重新 build、簽章並重算（下面「簽章」），再上傳（下面「上傳到發佈頁」）
```

### 簽章（只在擁有者自己的電腦）

SYNOX 安裝時會驗 `.synoxpack` 裡的 `pack.sig.json`（Ed25519），驗過官方鑰匙才顯示「✅ 官方」；沒簽的顯示「⚠️ 不是官方」（照樣能裝）。
規格與步驟的正本在 SYNOX 的 `docs/signing.md`（第 4 節「每次發佈官方工具」）與 `docs/adr/0028-pack-signatures-official-badge.md`；這裡只寫跟工具庫有關的：

- **私鑰只在擁有者手上**：不進 repo、不進任何 git 資料夾、不給雲端 session 或任何代理。別台電腦（含雲端）只 build，不簽。
- **先 build、再簽、簽完不再動**：簽章會改 `.synoxpack` 的內容，SYNOX 的「工具庫（官方）」又會對每個檔的大小與 sha256 跟 `index.json` 比，
  所以 `index.json` 每一筆的 `sha256`／`size`、`SHA256SUMS`、`tools-<VERSION>.zip`（與 `tools.zip`）都要用**簽過的檔**重算、重包。
  這些都由 `scripts/sign_dist.py` 一次做完；簽完之後再跑 `build.py` 會打回沒簽的檔，要整批重簽。
- 用的 SYNOX 要是**已經放進官方公鑰的版本**（`src/synox/packs/signing.py` 的 `OFFICIAL_PACK_PUBLIC_KEYS`）：`synox-sdk verify` 比對的是跑它的那份程式，
  舊的 SYNOX 會把官方鑰匙簽的檔看成「✍️ 作者簽章」，`sign_dist.py` 就會停下來。
- 目錄 `index.json` 本身還沒有簽章（SYNOX ADR 0028 §6，之後的事）。

在擁有者電腦、工具庫根目錄的 PowerShell（`<SYNOX>` 換成 SYNOX 資料夾；`<私鑰路徑>` 換成官方工具私鑰，放在任何 git 資料夾以外）：

```powershell
git fetch; git checkout <發佈 commit>                   # 或 main 的最新，確認 git rev-parse HEAD ＝ 發佈 commit
<SYNOX>\.venv\Scripts\python.exe scripts\build.py --check
<SYNOX>\.venv\Scripts\python.exe scripts\build.py      # dist/ 從發佈 commit 重新打
git status --short                                      # 要是空的（不是空的＝發佈 commit 不是 build 完的樣子，先停）

# 密語只放在這個視窗的環境變數：用 Read-Host 輸入（不會進 PowerShell 歷史；不要寫成 $env:...="密語"）
$s = Read-Host "私鑰密語" -AsSecureString
$env:SYNOX_PACK_KEY_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
<SYNOX>\.venv\Scripts\python.exe scripts\sign_dist.py --key <私鑰路徑>
Remove-Item Env:SYNOX_PACK_KEY_PASSWORD; Remove-Variable s     # 用完馬上清掉

<SYNOX>\.venv\Scripts\python.exe scripts\sign_dist.py --check   # 上傳前再驗一次（不用密語）
```

`scripts/sign_dist.py` 做的事（細節在檔頭）：

1. 先確認 `dist/` 是 `build.py` 全部打包的完整輸出、而且互相對得上（`index.json` 列的每個檔＋別名、tools zip、`SHA256SUMS`）；不是就停。
2. 在 `dist/` 裡的暫存資料夾，對每個帶版本的 `.synoxpack`：已經是官方簽章的跳過，沒簽的 `synox-sdk sign`；
   別名直接複製簽過的那一份（兩份逐位元組相同）；帶版本與別名**每一個**都 `synox-sdk verify`，要是 `official`
   （`verify` 沒有「要求官方」的選項：看結束碼＋第一行括號裡的狀態，例 `✅ 官方（official）`）。
3. 用簽過的檔重算 `index.json` 每一筆的 `sha256`／`size`（其餘一個字不改）、重包 `tools-<VERSION>.zip` 與 `tools.zip`、重寫 `SHA256SUMS`。
4. 一致性檢查（`sha256sum -c` 等價、`index.json` 每筆＝實際檔、每個 `.synoxpack` 都有 `pack.sig.json`、tools zip 裡的檔＝`dist/` 的檔）全過，才把結果換進 `dist/`。
   **任何一個檔失敗就整批停下，`dist/` 原封不動**（不會半簽半不簽）；結束碼 2 並指出是哪個檔。
- 密語只從環境變數 `SYNOX_PACK_KEY_PASSWORD` 拿：沒設＝報錯、什麼都不做；不問、不印、不寫進任何檔。私鑰路徑只交給 `synox-sdk sign`，這支不讀它。
- 用自己的測試鑰匙試跑：加 `--expect author`（標章會是「✍️ 作者簽章」）；`--sdk <synox-sdk 的路徑>` 指定 SDK；`--dist` 換資料夾。
- 重跑是安全的：簽好的不會重簽，結果逐位元組一樣。

### 上傳到發佈頁（擁有者的 agent 跑）

`TAG` ＝ `v` ＋ VERSION。**index.json 一定先備份舊的，再覆蓋。** 簽章（上面）做完、`sign_dist.py --check` 過了才開始。

**新的 tag**（例如第一次發 `v0.3.1`）：先開一個**草稿**發佈頁、tag 指到發佈 commit（草稿還不會建立 tag，也還沒人看得到），
照下面 a～c 上傳，全部傳完才把草稿發出去（這時才建立 tag），再做 d 的檢查。
新 tag 的發佈頁上還沒有 `index.json`：a 備份的是**上一個 tag** 的 `index.json`（`gh release download <上一個 tag> …`）。

```bash
# 新 tag：草稿、pre-release、tag 指到發佈 commit（REPO、TAG 照下一段開頭兩行先設好）
RELEASE_COMMIT=<發佈 commit>
gh release create "$TAG" -R "$REPO" --draft --prerelease --target "$RELEASE_COMMIT" --title "$TAG" --notes-file <說明檔>
#   ……a～c 上傳……
gh release edit "$TAG" -R "$REPO" --draft=false            # 這時才建立 tag
git fetch --tags && test "$(git rev-parse "$TAG^{commit}")" = "$RELEASE_COMMIT" && echo "tag OK"   # tag 一定要指到產生這批檔的 commit
```

```bash
REPO=$(python -c "import sys; sys.path.insert(0, 'scripts'); import links; print(links.REPO_URL.split('github.com/')[1])")   # <帳號>/SYNOX_Libs，跟 links.py 同一份
TAG=v$(cat VERSION)
cd dist && sha256sum -c SHA256SUMS && cd ..                 # 上傳前先驗一次（＝python scripts/sign_dist.py --check 的一部分）

# a. 備份發佈頁上現在的 index.json：本機留一份，發佈頁上也留一份帶日期的（index-backup-YYYYMMDD-HHMMSS.json）
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p release-backup
gh release download "$TAG" -R "$REPO" -p index.json -O "release-backup/index-backup-$STAMP.json"   # 新 tag：$TAG 換成上一個 tag
gh release upload "$TAG" -R "$REPO" "release-backup/index-backup-$STAMP.json"

# b. 工具與外觀：帶版本的檔、別名、tools zip、SHA256SUMS（全部 --clobber：
#    同名的 0.1.1 檔重新打包後 zip 時間不同、sha256 會變，不覆蓋的話 index.json 的 sha256 對不上）
gh release upload "$TAG" -R "$REPO" --clobber dist/*.synoxpack dist/tools-*.zip dist/tools.zip dist/SHA256SUMS

# c. 最後才換 index.json（目錄指到的檔都已經在上面了）
gh release upload "$TAG" -R "$REPO" --clobber dist/index.json
#    新 tag：這時才把草稿發出去、檢查 tag 指到發佈 commit（上一段最後兩行）

# d. 驗：每個頁面連到的檔都下載得到（草稿發出去之後才看得到）
python - <<'EOF'
import sys, urllib.request
sys.path.insert(0, "scripts")
import links
for name in sorted(links.expected_assets()):
    url = links.asset_url(name)
    req = urllib.request.Request(url, method="HEAD")
    print(urllib.request.urlopen(req).status, url)
EOF

# e. 重新發佈改到的線上頁（docs/ARTIFACTS.md）
python scripts/build_site.py --list-republish --since <上一個 tag>
```

注意：

- **tag 一定指到「產生這批檔的那個 commit」**（發佈 commit）：`dist/` 是從它 build、簽的。發佈 commit 之後、tag 建立之前，
  不要再合併會影響 `dist/` 的 commit（`packs/`、`costumes/`、`catalog.json`、`VERSION`、`scripts/`）；只改文件的可以。
  有的話：要嘛 tag 照樣指發佈 commit（之後的改動等下一版），要嘛從新的 main 重新 build、重新簽、重新上傳，tag 改指新的 commit。

- 舊版本的檔（例 `money-0.2.0.synoxpack`）**留著**，舊的連結與已經裝了的人不會壞；`index.json` 只列最新版。
  舊的 `SHA256SUMS-costumes.txt` 已經被 `SHA256SUMS` 取代，可以刪：`gh release delete-asset "$TAG" SHA256SUMS-costumes.txt -R "$REPO" -y`。
- 只有 `gh api`（REST）可用的環境：先 `gh api repos/$REPO/releases/tags/$TAG -q '.assets[]|{name,id}'` 找到同名資產的 id、
  `gh api -X DELETE repos/$REPO/releases/assets/<id>` 刪掉，再用
  `curl -H "Authorization: Bearer $(gh auth token)" -H "Content-Type: application/octet-stream" --data-binary @<檔> "https://uploads.github.com/repos/$REPO/releases/<release id>/assets?name=<檔名>"` 上傳
  （這就是 `--clobber` 做的事）。index.json 一樣：先下載備份、最後才換。

## 標成 Latest（之後切到 `LINK_MODE = "latest"`）

```bash
gh release edit "$TAG" -R "$REPO" --prerelease=false --latest
# REST： gh api -X PATCH repos/$REPO/releases/<release id> -F prerelease=false -f make_latest=true
curl -sI https://github.com/ChouYangEn0401/SYNOX_Libs/releases/latest/download/money.synoxpack | head -1   # 302＝好了
```

確認 `releases/latest/download/<name>.synoxpack` 會轉到檔案之後：

1. `scripts/links.py`：`LINK_MODE = "latest"`；
2. `python scripts/links.py`（改寫 README 與 `docs/site/*.html`、重新蓋頁面版本）；`python scripts/build_site.py`（產生的頁也照新模式，通常已經一樣）；
   `python scripts/build_costumes.py page && python scripts/site_stamp.py wardrobe.html`；
3. `python -m pytest -q`，commit，重新發佈線上版的每一頁（`python scripts/build_site.py --list-republish` 全部列出來）；
4. SYNOX 那邊（另一個 repo）：`synox/packs/catalog.py` 的 `OFFICIAL_INDEX_URL` 改回 `releases/latest/download/index.json`。

之後每次發佈只要上傳新的檔（別名也一起 `--clobber`），頁面不用再動——除非頁面上寫的版本字樣（`money · v0.3.1`）要跟著變，
那還是跑一次 `python scripts/build.py`（測試會提醒）。新的 tag 也要標成 Latest，`releases/latest` 才會跟過去。
