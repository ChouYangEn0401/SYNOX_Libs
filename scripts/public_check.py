"""公開版檢查：工具庫要做成公開版（給別人用）之前，找擁有者的隱私。只用標準函式庫、不連網、不執行 repo 裡的任何程式。

    <SYNOX>\\.venv\\Scripts\\python.exe scripts\\public_check.py             目前的檔案（git 追蹤的＋沒被忽略的新檔）
    <SYNOX>\\.venv\\Scripts\\python.exe scripts\\public_check.py --history   另外看 git 歷史（舊版本的檔、commit 作者／提交者信箱）

找什麼（概念跟 SYNOX 的 scripts/prepublish_check.py 一樣，規則是工具庫自己的）：
  1. 本機絕對路徑（C:\\Users\\<名字>、/Users/<名字>、/home/<名字>；<你>、x、me 這種佔位不算）
  2. 使用者名稱：這台電腦的帳號名、git 的 user.name／user.email（執行時才讀，不寫進 repo），
     再加 public_check.local.txt（git 忽略，一行一個：真名、暱稱、Telegram 帳號…）；太短或太常見的字（user、admin…）跳過
  3. email（example.com／.org／.net、.invalid 這類保留網域、me@／noreply@ 這種佔位不算）
  4. token／金鑰：Telegram、OpenAI／Anthropic、Google、Slack、GitHub、AWS、私鑰檔頭、Notion、Discord，
     以及 token／secret／password／api_key = "很長的一串"（值看起來是假的才放行：fake、test、example、123、重複字…）
  5. 私人資料檔：.env、config.json、*.enc、packs.lock、*.key、*.pem、credentials*.json、token.json、client_secret*.json、
     data／userdata／var 資料夾裡的檔、*.sqlite／*.db
  6. 測試用的真實 id：owner_id／tg_id／chat_id… 後面接 8～12 位數字（Telegram id 的長度）、看起來是真的 UUID（字元變化多）、
     真的 Google OAuth client id

報告只寫「檔名:行號＋種類」，不印出內容。退出碼：0＝乾淨、1＝有發現、2＝沒辦法檢查。
確定是範例或假資料的行，在同一行加 `public-check: allow`（並寫為什麼）；SYNOX 的 `prepublish-check: allow` 也認。
"""

from __future__ import annotations

import argparse
import fnmatch
import getpass
import os
import re
import subprocess
import sys
from typing import Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW_MARKS = ("public-check: allow", "prepublish-check: allow")
LOCAL_WORDS = "public_check.local.txt"            # git 忽略；擁有者自己的名字、暱稱…（不能進 repo）
MAX_BYTES = 2_000_000

SECRET_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("Telegram bot token", re.compile(r"(?<![0-9A-Za-z])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")),
    ("OpenAI／Anthropic 金鑰（sk-…）", re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-|ant-[a-z0-9]+-)?[A-Za-z0-9_-]{20,}")),
    ("Google API 金鑰（AIza…）", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("Google OAuth client secret（GOCSPX-…）", re.compile(r"GOCSPX-[A-Za-z0-9_-]{20,}")),
    ("Google refresh／access token", re.compile(r"(?:1//0[A-Za-z0-9_-]{30,}|ya29\.[A-Za-z0-9_-]{30,})")),
    ("Slack token（xox…）", re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}")),
    ("GitHub token", re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("AWS access key（AKIA…）", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")),
    ("私鑰檔頭", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("Notion token", re.compile(r"(?:ntn_[A-Za-z0-9]{30,}|secret_[A-Za-z0-9]{43})")),
    ("Discord bot token", re.compile(r"(?<![A-Za-z0-9_-])[MNO][A-Za-z0-9_-]{23,27}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,40}(?![A-Za-z0-9_-])")),
]
# token = "…"、"password": "…"：值 16 字以上、看起來不是假的
ASSIGNED_SECRET = re.compile(r"(?i)\b(?:token|secret|password|passwd|api[_-]?key|client_secret|refresh_token|access_token)"
                             r"\b[\"']?\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']")
FAKE_WORDS = ("fake", "test", "example", "dummy", "placeholder", "sample", "secret", "xxx", "abc", "123", "000", "111",
              "aaa", "xyz", "your", "<", "{")

WIN_PATH = re.compile(r"[A-Za-z]:(?:\\\\|\\|/)+Users(?:\\\\|\\|/)+([^\\/\s\"'`<>|*?:;,)\]}]*)", re.I)
POSIX_PATH = re.compile(r"(?<![A-Za-z]:)(?<![\w.])/(?:Users|home)/([^/\s\"'`<>|*?:;,)\]}]*)")
PLACEHOLDER_NAMES = {"", "x", "xx", "xxx", "you", "your", "yourname", "your_name", "your-name", "name", "username",
                     "user_name", "me", "example", "public", "default", "all users", "someone", "foo", "bar", "alice",
                     "bob", "runner", "runneradmin", "ci", "test", "tester", "admin", "...", "…", "你", "名字", "使用者"}

EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![A-Za-z0-9-])")
ALLOWED_EMAILS = {"noreply@anthropic.com", "git@github.com", "noreply@github.com"}
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com")
RESERVED_TLDS = (".invalid", ".test", ".example", ".localhost", ".local")
PLACEHOLDER_LOCAL_PARTS = {"me", "you", "user", "test", "foo", "bar", "someone", "name", "example", "nobody",
                           "noreply", "no-reply"}

SENSITIVE_NAMES = [".env", ".env.*", "config.json", "config.local.*", "packs.lock", "*.enc", ".synox.key", "*.key", "*.pem",
                   "*.p12", "*.pfx", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials*.json", "token.json",
                   "client_secret*.json", "*.kdbx", "secrets.json", "*.sqlite", "*.sqlite3", "*.db", LOCAL_WORDS]
ALLOWED_NAMES = {"config.example.json", ".env.example", ".env.sample"}
DATA_DIRS = ("data", "userdata", "var")

# 真實 id：owner_id 後面接 8～12 位數字（Telegram id 的長度）
REAL_TG_ID = re.compile(r"(?i)\b(?:owner_id|tg_id|chat_id|user_id|telegram_id|admin_id|from_id)\b[\"']?\s*[:=(,]\s*[\"']?(\d{8,12})\b")
UUID = re.compile(r"\b[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\b", re.I)
GOOGLE_CLIENT_ID = re.compile(r"\b(\d{6,}-[0-9a-z]{6,})\.apps\.googleusercontent\.com\b", re.I)
GENERIC_WORDS = {"user", "users", "admin", "owner", "root", "default", "public", "guest", "test", "runner", "synox",
                 "github", "windows", "desktop"}


class Finding(NamedTuple):
    where: str      # worktree／history／commit
    path: str
    line: int       # 0＝整個檔
    kind: str
    note: str = ""  # 補充（絕不放比對到的內容）


# ── 比對 ─────────────────────────────────────────────────────────────────

def looks_fake(value: str) -> bool:
    """看起來是範例／假資料（放行）：有 fake、test、123、xyz… 這種字，或用到的字元很少（11111111、aaaa-bbbb）。"""
    low = value.lower()
    if any(w in low for w in FAKE_WORDS):
        return True
    chars = {c for c in low if c.isalnum()}
    return len(chars) <= 8


def _placeholder_name(name: str) -> bool:
    n = name.strip().lower()
    return (n in PLACEHOLDER_NAMES or n[:1] in "<{%$[(" or n.startswith(("%user", "$user", "${user", "$env"))
            or set(n) <= {"x", "*", "."})


def email_allowed(addr: str) -> bool:
    local, _, domain = addr.lower().rpartition("@")
    return (addr.lower() in ALLOWED_EMAILS or domain.endswith(RESERVED_TLDS)
            or any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS)
            or local in PLACEHOLDER_LOCAL_PARTS)


def private_words(repo: str) -> List[str]:
    """這台電腦的帳號名、git 的 user.name／user.email、public_check.local.txt 的每一行（執行時才讀；太短或太常見的跳過）。"""
    words: List[str] = []
    try:
        words.append(getpass.getuser())
    except Exception:                                    # 讀不到帳號名就算了
        pass
    for key in ("user.name", "user.email"):
        try:
            words.append(_git(repo, "config", "--get", key).decode("utf-8", "replace").strip())
        except RuntimeError:
            pass
    path = os.path.join(repo, LOCAL_WORDS)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8-sig") as f:
            words += [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    out = []
    for w in words:
        if len(w) >= 5 and w.lower() not in GENERIC_WORDS and w not in out:
            out.append(w)
    return out


def scan_text(text: str, words: Iterable[str] = ()) -> List[Tuple[int, str]]:
    """一個檔的內容 → [(行號, 種類)]。只回位置與種類，不回比對到的字。"""
    out: List[Tuple[int, str]] = []
    lowered = [(w, w.lower()) for w in words]
    for no, line in enumerate(text.splitlines(), 1):
        if any(mark in line for mark in ALLOW_MARKS):
            continue
        for kind, pat in SECRET_PATTERNS:
            if pat.search(line):
                out.append((no, "疑似 token／金鑰：" + kind))
        if any(not looks_fake(m.group(1)) for m in ASSIGNED_SECRET.finditer(line)):
            out.append((no, "疑似 token／金鑰：寫死的值"))
        if any(not _placeholder_name(m.group(1)) for m in WIN_PATH.finditer(line)) or \
           any(not _placeholder_name(m.group(1)) for m in POSIX_PATH.finditer(line)):
            out.append((no, "本機絕對路徑（含使用者名稱）"))
        if any(not email_allowed(m.group(0)) for m in EMAIL.finditer(line)):
            out.append((no, "email"))
        low = line.lower()
        if any(lw in low for _w, lw in lowered):
            out.append((no, "使用者名稱（這台的帳號／git 名字／public_check.local.txt）"))
        if REAL_TG_ID.search(line):
            out.append((no, "疑似真實 id（Telegram 長度的數字）"))
        if any(not looks_fake(m.group(0)) for m in UUID.finditer(line)):
            out.append((no, "疑似真實 id（UUID）"))
        if any(not looks_fake(m.group(1)) for m in GOOGLE_CLIENT_ID.finditer(line)):
            out.append((no, "疑似真實 id（Google OAuth client id）"))
    return out


def sensitive_name(path: str) -> str:
    """私人資料檔 → 原因；不是回 ""。"""
    parts = path.replace("\\", "/").split("/")
    base = parts[-1].lower()
    if base in ALLOWED_NAMES:
        return ""
    if any(fnmatch.fnmatch(base, pat.lower()) for pat in SENSITIVE_NAMES):
        return "私人資料檔（不該提交）"
    if any(p.lower() in DATA_DIRS for p in parts[:-1]):
        return "私人資料檔（data／userdata／var 資料夾裡的檔）"
    return ""


def _decode(data: bytes) -> Optional[str]:
    if b"\0" in data[:8000]:
        return None                                       # 二進位
    return data.decode("utf-8", errors="replace")


# ── git ──────────────────────────────────────────────────────────────────

def _git(repo: str, *args: str, data: Optional[bytes] = None) -> bytes:
    proc = subprocess.run(["git", "-C", repo, "-c", "core.quotepath=off", *args], input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} 失敗：{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def worktree_files(repo: str) -> List[str]:
    out = _git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted({p for p in out.decode("utf-8", "replace").split("\0") if p})


def scan_worktree(repo: str, words: List[str]) -> List[Finding]:
    found: List[Finding] = []
    for rel in worktree_files(repo):
        why = sensitive_name(rel)
        if why:
            found.append(Finding("worktree", rel, 0, why))
        full = os.path.join(repo, rel)
        try:
            if not os.path.isfile(full) or os.path.getsize(full) > MAX_BYTES:
                continue
            with open(full, "rb") as fh:
                text = _decode(fh.read())
        except OSError:
            continue
        if text is not None:
            found.extend(Finding("worktree", rel, no, kind) for no, kind in scan_text(text, words))
    return found


def _history_blobs(repo: str) -> Dict[str, str]:
    named: Dict[str, str] = {}
    for ln in _git(repo, "rev-list", "--all", "--objects").decode("utf-8", "replace").splitlines():
        sha, _, path = ln.partition(" ")
        if path:
            named.setdefault(sha, path)
    if not named:
        return {}
    rows = _git(repo, "cat-file", "--batch-check=%(objecttype) %(objectname) %(objectsize)",
                data=("\n".join(named) + "\n").encode()).decode().splitlines()
    return {p[1]: named[p[1]] for p in (r.split() for r in rows)
            if len(p) == 3 and p[0] == "blob" and int(p[2]) <= MAX_BYTES}


def _read_blobs(repo: str, shas: List[str]) -> Iterable[Tuple[str, bytes]]:
    if not shas:
        return
    raw = _git(repo, "cat-file", "--batch", data=("\n".join(shas) + "\n").encode())
    pos = 0
    while pos < len(raw):
        nl = raw.index(b"\n", pos)
        sha, _type, size = raw[pos:nl].decode().split()
        start = nl + 1
        yield sha, raw[start:start + int(size)]
        pos = start + int(size) + 1


def scan_history(repo: str, words: List[str]) -> List[Finding]:
    """舊版本的檔（內容跟現在一樣的不重複報）＋曾經提交過的私人資料檔名＋commit 作者／提交者信箱。"""
    found: List[Finding] = []
    current = {ln.split()[1] for ln in _git(repo, "ls-files", "-s").decode("utf-8", "replace").splitlines() if ln.strip()}
    tracked_now = set(worktree_files(repo))
    paths = _git(repo, "log", "--all", "--name-only", "--format=", "-z", "--no-renames").decode("utf-8", "replace")
    for path in sorted({p.strip("\n") for p in paths.split("\0") if p.strip("\n")}):
        if sensitive_name(path) and path not in tracked_now:
            found.append(Finding("history", path, 0, "私人資料檔（曾經提交過）"))
    blobs = _history_blobs(repo)
    for sha, data in _read_blobs(repo, sorted(s for s in blobs if s not in current)):
        text = _decode(data)
        if text is not None:
            found.extend(Finding("history", blobs[sha], no, kind) for no, kind in scan_text(text, words))
    authors = _git(repo, "log", "--all", "--format=%ae%n%ce").decode("utf-8", "replace").split()
    for addr in sorted(set(authors)):
        if not email_allowed(addr):
            masked = f"{addr.split('@')[0][:1]}…@{addr.rpartition('@')[2]}"
            found.append(Finding("commit", "（commit 作者／提交者）", 0, "email", masked))
    return found


# ── 報告 ─────────────────────────────────────────────────────────────────

def report(findings: List[Finding], history: bool) -> str:
    out = ["公開版檢查（SYNOX_Libs scripts/public_check.py）", ""]
    sections = [("worktree", "目前的檔案（公開前要修掉）"),
                ("history", "git 歷史（舊版本裡有；要不要改寫歷史由擁有者決定）"),
                ("commit", "commit 的作者／提交者（要不要改成 noreply 信箱由擁有者決定）")]
    for key, title in sections:
        if key != "worktree" and not history:
            continue
        rows = sorted(set(f for f in findings if f.where == key))
        if key == "history":                              # 同一個檔、同一種問題只寫一行
            groups: Dict[Tuple[str, str], Set[int]] = {}
            for f in rows:
                groups.setdefault((f.path, f.kind), set()).add(f.line)
            out.append(f"■ {title}：{'沒有發現' if not groups else f'{len(groups)} 項'}")
            for (path, kind), lines in sorted(groups.items()):
                nums = sorted(n for n in lines if n)
                out.append(f"  - {path}" + (":" + ",".join(map(str, nums[:5])) + ("…" if len(nums) > 5 else "") if nums else "")
                           + f"　{kind}")
            out.append("")
            continue
        out.append(f"■ {title}：{'沒有發現' if not rows else f'{len(rows)} 項'}")
        for f in rows:
            out.append(f"  - {f.path}" + (f":{f.line}" if f.line else "") + f"　{f.kind}" + (f"（{f.note}）" if f.note else ""))
        out.append("")
    if findings:
        out.append("結果：有要處理的項目（只列位置與種類，沒有印出內容）。")
        out.append(f"確定是範例或假資料的行，在同一行加 `{ALLOW_MARKS[0]}` 並寫為什麼。")
    else:
        out.append("結果：乾淨。")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="公開版檢查：本機路徑、使用者名稱、email、token、私人資料檔、真實 id")
    parser.add_argument("--repo", default=ROOT, help="要檢查的 git repo（預設：這個工具庫）")
    parser.add_argument("--history", action="store_true", help="另外看 git 歷史與 commit 作者信箱")
    args = parser.parse_args(argv)
    try:
        _git(args.repo, "rev-parse", "--git-dir")
        words = private_words(args.repo)
        findings = scan_worktree(args.repo, words)
        if args.history:
            findings += scan_history(args.repo, words)
    except (RuntimeError, OSError) as e:
        print(f"沒辦法檢查：{e}", file=sys.stderr)
        return 2
    text = report(findings, args.history)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(text)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
