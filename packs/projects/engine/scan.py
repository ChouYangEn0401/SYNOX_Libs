"""看資料夾「真的有沒有在動」：這是整個工具唯一會讀使用者自己指定的資料夾的地方。

- 只讀，不寫、不刪、不改名；不開別的程式（不呼叫 git：直接讀 .git/logs/HEAD 最後一行的時間）。
- 路徑只來自使用者自己填的專案位置；不給預設路徑、不猜路徑。`~` 會展開成這個人的家目錄；相對路徑不收（會變成「跟著程式從哪啟動」）。
- 永不丟錯：一個掃不到的專案（路徑在別台電腦、磁碟沒掛上、權限不足）只是回「沒活動」，不讓整批失敗。
"""

import os

# 這些資料夾的修改時間會被工具自己動來動去，拿來當「你有沒有在做這個專案」會失真
SKIP = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", ".vs",
                  "target", ".next", ".pytest_cache"})
MAX_CHILDREN = 5000        # 第一層最多看幾個項目：再多就當夠了，不拖慢
TAIL_BYTES = 4096


def resolve(path: str):
    """使用者填的位置 → (絕對路徑, 是不是一個現存的資料夾)。空的、相對的、壞的一律回 ("", False)。"""
    raw = str(path or "").strip()
    if not raw:
        return "", False
    full = os.path.expanduser(raw)
    if not os.path.isabs(full):
        return "", False
    try:
        return full, os.path.isdir(full)
    except OSError:
        return full, False


def last_commit(folder: str):
    """最後一次 HEAD 動作的時間（秒）。不是 git repo、讀不到、格式怪 → None（不是錯）。
    .git/logs/HEAD 每行長這樣：<舊> <新> <名字> <信箱> <秒> <時區>\\t<訊息>。只取時間那一欄。"""
    log = os.path.join(folder, ".git", "logs", "HEAD")
    try:
        with open(log, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    lines = [ln for ln in tail.splitlines() if ln.strip()]
    if not lines:
        return None
    head = lines[-1].split("\t", 1)[0]
    parts = head.rsplit(">", 1)
    if len(parts) != 2:
        return None
    fields = parts[1].split()
    if fields and fields[0].isdigit():
        return int(fields[0])
    return None


def newest_mtime(folder: str):
    """只看第一層（遞迴掃大專案會慢到沒人想按；改檔、存檔通常會動到某個第一層的東西）。
    刻意不看資料夾自己的修改時間：它會因為「裡面多了或少了一個項目」而更新（例如被重建的 .venv），跟你有沒有在做無關。
    只有真的沒有任何子項目時才退回看它自己。"""
    newest = 0
    try:
        with os.scandir(folder) as it:
            for n, entry in enumerate(it):
                if n >= MAX_CHILDREN:
                    break
                if entry.name in SKIP:
                    continue
                try:
                    newest = max(newest, int(entry.stat().st_mtime))
                except OSError:
                    continue
    except OSError:
        return None
    if newest:
        return newest
    try:
        return int(os.stat(folder).st_mtime)
    except OSError:
        return None


def scan_one(path: str) -> dict:
    """一個專案位置 → {last_active, last_commit, source}。source：git｜files｜missing（有填但找不到）｜nopath。"""
    if not str(path or "").strip():
        return {"last_active": 0, "last_commit": 0, "source": "nopath"}
    folder, exists = resolve(path)
    if not exists:
        return {"last_active": 0, "last_commit": 0, "source": "missing"}
    git_ts = last_commit(folder)
    mtime = newest_mtime(folder)
    best = max([t for t in (git_ts, mtime) if t] or [0])      # 取較晚的：有 commit 但之後又改了檔也算在動
    source = "git" if git_ts and git_ts >= (mtime or 0) else "files" if mtime else ""
    # 兩個都留著：最後一次 commit 本身就是使用者想看到的數字，而它幾乎永遠比工作目錄的修改時間舊
    return {"last_active": best, "last_commit": git_ts or 0, "source": source}
