# 發佈：把 dist/ 放上 GitHub 發佈頁，下載連結永遠是最新版

擁有者的規矩：**每個公開頁**（`docs/site/*.html`、衣櫃頁 `docs/site/wardrobe.html`、`README.md`）上的下載連結，
永遠指到最新版。做法是「版本只寫在一個地方」：

| 東西 | 唯一來源 |
|---|---|
| 工具／外觀的版本 | 各自的 `pack.json`（`packs/<name>/`、`costumes/<id>/`） |
| 工具庫版本、發佈 tag | `VERSION`（tag ＝ `v` ＋ VERSION，例 `v0.2.0`） |
| 網址怎麼組 | `scripts/links.py`（`LINK_MODE`、`asset_url`、`pack_url`、`tools_url`） |

頁面裡看得到的版本（`money · v0.3.1`、`money-0.3.1.synoxpack`、`tools-0.2.0.zip`、`發佈頁 v0.2.0`）都是
`scripts/links.py` 寫進去的，**不要手改**；衣櫃頁整頁由 `scripts/build_costumes.py page` 產生，連結也走 `links.py`。
`tests/test_links.py` 會擋：頁面寫的版本跟 pack.json 不同、連到 build 不會產生的檔、連到別的 tag、
`tools-<VERSION>.zip` 過期、衣櫃頁不是產生器現在的輸出、index.json 少了工具或 26 套外觀。

## 發佈頁上的檔（＝`scripts/build.py` 全部打包時的 dist/）

| 檔 | 說明 |
|---|---|
| `<name>-<version>.synoxpack` | 每個工具（9 個）與每套外觀（26 套）帶版本的檔 |
| `<name>.synoxpack` | 同一份內容、不帶版本的別名（給 `releases/latest/download/` 用） |
| `tools-<VERSION>.zip`、`tools.zip` | 全部工具：`tools/*.synoxpack` ＋ 只列工具的 `tools/index.json`；`tools.zip` 是別名 |
| `index.json` | 目錄：每個工具與外觀的版本、檔名、sha256（外觀 `kind: "theme"`、`collections.wardrobe`） |
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
# 2. 全部打包：清掉舊的 dist/、打出全部檔、SHA256SUMS，並把每個公開頁的下載連結改成現在的版本、重新蓋頁面版本
python scripts/build.py
python scripts/build_costumes.py page && python scripts/site_stamp.py wardrobe.html   # 衣櫃頁的版面有改才需要
# 3. 測試（tests/test_links.py、tests/test_site_pages.py 一定要過）
python -m pytest -q
# 4. commit（頁面有改的話，線上版 Artifact 也要重新發佈同一個網址）
```

### 上傳到發佈頁（擁有者的 agent 跑）

`TAG` ＝ `v` ＋ VERSION。**index.json 一定先備份舊的，再覆蓋。**

```bash
REPO=ChouYangEn0401/SYNOX_Libs
TAG=v$(cat VERSION)
cd dist && sha256sum -c SHA256SUMS && cd ..                 # 上傳前先驗一次

# a. 備份發佈頁上現在的 index.json：本機留一份，發佈頁上也留一份帶日期的（index-backup-YYYYMMDD-HHMMSS.json）
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p release-backup
gh release download "$TAG" -R "$REPO" -p index.json -O "release-backup/index-backup-$STAMP.json"
gh release upload "$TAG" -R "$REPO" "release-backup/index-backup-$STAMP.json"

# b. 工具與外觀：帶版本的檔、別名、tools zip、SHA256SUMS（全部 --clobber：
#    同名的 0.1.1 檔重新打包後 zip 時間不同、sha256 會變，不覆蓋的話 index.json 的 sha256 對不上）
gh release upload "$TAG" -R "$REPO" --clobber dist/*.synoxpack dist/tools-*.zip dist/tools.zip dist/SHA256SUMS

# c. 最後才換 index.json（目錄指到的檔都已經在上面了）
gh release upload "$TAG" -R "$REPO" --clobber dist/index.json

# d. 驗：每個頁面連到的檔都下載得到
python - <<'EOF'
import sys, urllib.request
sys.path.insert(0, "scripts")
import links
for name in sorted(links.expected_assets()):
    url = links.asset_url(name)
    req = urllib.request.Request(url, method="HEAD")
    print(urllib.request.urlopen(req).status, url)
EOF
```

注意：

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
2. `python scripts/links.py`（改寫 README 與 `docs/site/*.html`、重新蓋頁面版本）；`python scripts/build_costumes.py page && python scripts/site_stamp.py wardrobe.html`；
3. `python -m pytest -q`，commit，重新發佈線上版的每一頁。

之後每次發佈只要上傳新的檔（別名也一起 `--clobber`），頁面不用再動——除非頁面上寫的版本字樣（`money · v0.3.1`）要跟著變，
那還是跑一次 `python scripts/build.py`（測試會提醒）。新的 tag 也要標成 Latest，`releases/latest` 才會跟過去。
