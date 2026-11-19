"""簽整批發佈檔：dist/ 裡每個 .synoxpack 加上 pack.sig.json，再用**簽過的檔**重算 index.json、SHA256SUMS、tools zip。

    <SYNOX>\\.venv\\Scripts\\python.exe scripts\\sign_dist.py --key <私鑰.pem>             簽 dist/、重算、檢查
    <SYNOX>\\.venv\\Scripts\\python.exe scripts\\sign_dist.py --check                       只檢查（上傳前再跑一次；不簽、不用密語）
    選項：--dist <資料夾>（預設 dist/）、--sdk <synox-sdk 的路徑>（預設：跑這支的 Python 旁邊的 synox-sdk，
          沒有就 python -m synox.sdk）、--expect official|author（預設 official）

為什麼要這支（docs/RELEASING.md「簽章」；正本在 SYNOX 的 docs/signing.md 第 4 節、ADR 0028）：
簽章會改 .synoxpack 的內容，SYNOX 的「工具庫（官方）」又要每個檔的大小與 sha256 跟 index.json 一樣，
所以簽完 index.json 每一筆的 sha256／size、SHA256SUMS、tools-<VERSION>.zip（與 tools.zip）都要用簽過的檔重算、重包。

規矩：
- 只在擁有者自己的電腦跑（私鑰只在那裡）。私鑰路徑只當參數交給 `synox-sdk sign`，這支不開、不讀那個檔。
- 密語只從環境變數 SYNOX_PACK_KEY_PASSWORD 拿（沒設＝報錯、什麼都不做）；不問、不印、不寫進任何檔。
  呼叫 SDK 時 stdin 接空的，SDK 不會停下來問；SDK 印的字萬一含密語也會被遮掉。
- dist/ 必須是 scripts/build.py 全部打包的完整輸出（index.json 列的每個檔＋別名、tools zip、SHA256SUMS 都在、而且互相對得上）。
- 全有或全無：先在 dist/ 裡的暫存資料夾簽、驗、重算、檢查，全部過了才換進 dist/；任何一個檔失敗就停，dist/ 原封不動。
- 已經是預期標章的檔不重簽（用 `synox-sdk verify` 判斷）；別名不另外簽，直接用帶版本那一份簽過的檔複製（兩份逐位元組相同），再各驗一次。
- 驗：`synox-sdk verify` 沒有「要求某種標章」的選項，這裡看它的結束碼（0＝有效簽章）＋第一行括號裡的狀態
  （`✅ 官方（official）`、`✍️ 作者簽章（author）`、`⚠️ 不是官方（none）`、`⛔ 被改過（invalid）`），要等於 --expect。
  official 要用「已經放進官方公鑰」的 SYNOX 跑（verify 比對的是那份程式裡的 OFFICIAL_PACK_PUBLIC_KEYS）。
- index.json 只改每一筆的 sha256 與 size，其餘一個字都不動（寫法跟 build.py 同一份：build.index_text）。
結束碼：0＝好了；2＝有問題（印出是哪一個檔、哪裡不對）。只用標準函式庫＋呼叫 synox-sdk。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _synox  # noqa: E402
import build  # noqa: E402  （只用它的寫法：index_text、write_tools_zips、write_sums；不 import SYNOX）
import links  # noqa: E402

PASSWORD_ENV = "SYNOX_PACK_KEY_PASSWORD"
SIG_FILE = "pack.sig.json"
STATUSES = ("official", "author", "none", "invalid")
STATUS_RE = re.compile(r"（(official|author|none|invalid)）")
STAGING_PREFIX = ".sign-staging-"


class SignError(Exception):
    """停下來的原因（給人看的一句話）。"""


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 找 synox-sdk、叫它 ─────────────────────────────────────────────────

def sdk_command(sdk: str = "") -> list:
    """synox-sdk 怎麼叫：--sdk 給了就用它（.py 用這個 Python 跑）；沒給就找這個 Python 旁邊的 synox-sdk，再沒有就 -m synox.sdk。"""
    if sdk:
        return [sys.executable, sdk] if sdk.lower().endswith(".py") else [sdk]
    here = os.path.dirname(sys.executable)
    for name in ("synox-sdk.exe", "synox-sdk"):
        for folder in (here, os.path.join(here, "Scripts"), os.path.join(here, "bin")):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return [path]
    return [sys.executable, "-m", "synox.sdk"]


def _sdk_env() -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    home = _synox.home()                       # -m synox.sdk 時讓它 import 得到（pip install -e 過的不需要）
    if home:
        extra = [os.path.join(home, "src"), os.path.join(home, "vendor")]
        env["PYTHONPATH"] = os.pathsep.join(extra + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def _redact(text: str) -> str:
    secret = os.environ.get(PASSWORD_ENV) or ""
    return text.replace(secret, "***") if len(secret) >= 1 else text


def run_sdk(cmd: list, args: list) -> tuple:
    """→ (結束碼, 印出的字)。stdin 接空的（SDK 不會停下來問密語）；輸出遮掉密語。"""
    try:
        done = subprocess.run(cmd + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              env=_sdk_env(), timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        raise SignError(f"叫不動 synox-sdk（{' '.join(cmd)}）：{type(e).__name__}。用 --sdk 指定它的路徑")
    return done.returncode, _redact(done.stdout.decode("utf-8", errors="replace"))


def verify_status(cmd: list, path: str) -> tuple:
    """synox-sdk verify → (狀態 official／author／none／invalid／""（看不懂）, 印出的字)。
    結束碼與第一行括號裡的狀態要一致（0＝official／author、1＝none、2＝invalid），不一致當看不懂。"""
    code, out = run_sdk(cmd, ["verify", path])
    first = next((line for line in out.splitlines() if line.strip()), "")
    found = STATUS_RE.search(first)
    status = found.group(1) if found else ""
    expected_code = {"official": 0, "author": 0, "none": 1, "invalid": 2}.get(status)
    if status and expected_code != code:
        status = ""
    return status, out


# ── 讀 dist/、檢查一致 ──────────────────────────────────────────────────

def _load_index(dist: str) -> tuple:
    path = os.path.join(dist, links.INDEX_FILE)
    if not os.path.isfile(path):
        raise SignError(f"{dist} 裡沒有 index.json：先跑 python scripts/build.py（全部打包）")
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    try:
        index = json.loads(text)
    except ValueError as e:
        raise SignError(f"index.json 看不懂：{e}")
    if not isinstance(index, dict) or not isinstance(index.get("packs"), list) or not index["packs"]:
        raise SignError("index.json 沒有 packs 清單：不是 build.py 的輸出")
    if build.index_text(index) != text:
        raise SignError("index.json 的寫法跟 build.py 寫的不一樣（被手改過？）：重新跑 python scripts/build.py")
    return index, text


def tools_zip_name(index: dict) -> str:
    return f"tools-{index.get('library_version')}.zip"


def release_files(index: dict) -> list:
    """發佈頁上該有的全部檔（＝SHA256SUMS 該列的；build.release_extras 寫的那一組）。"""
    names = {e["file"] for e in index["packs"]} | {links.alias_file(e["name"]) for e in index["packs"]}
    return sorted(names | {tools_zip_name(index), links.TOOLS_ALIAS, links.INDEX_FILE})


def read_sums(dist: str) -> dict:
    path = os.path.join(dist, links.SUMS_FILE)
    if not os.path.isfile(path):
        raise SignError(f"{dist} 裡沒有 SHA256SUMS：先跑 python scripts/build.py（全部打包）")
    out = {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\r\n")
            if not line:
                continue
            found = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
            if not found:
                raise SignError(f"SHA256SUMS 第 {n} 行看不懂：{line[:80]}")
            out[found.group(2)] = found.group(1).lower()
    return out


def has_signature(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return any(n == SIG_FILE or n.endswith("/" + SIG_FILE) for n in z.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def problems(dist: str, signed: bool = True) -> list:
    """dist/ 一致嗎（不叫 SDK、不改檔）→ 每一條不一致一句話（空的＝都對）：
    - sha256sum -c SHA256SUMS 等價：列的每個檔都在、sha256 對；發佈檔每個都有列；
    - index.json 每一筆的 sha256／size＝實際檔；別名＝帶版本那一份（逐位元組）；
    - dist/ 最上層沒有 index.json 不認得的 .synoxpack；
    - tools-<VERSION>.zip＝tools.zip；裡面每個 tools/*.synoxpack＝dist 的同名檔、tools/index.json＝從 index.json 算出來的那份；
    - signed=True：每個 .synoxpack 裡都有 pack.sig.json。"""
    try:
        index, _text = _load_index(dist)
        sums = read_sums(dist)
    except SignError as e:
        return [str(e)]
    out = []
    files = release_files(index)
    for name in files:
        path = os.path.join(dist, name)
        if not os.path.isfile(path):
            out.append(f"{name}：dist 裡沒有這個檔（不是 build.py 的完整輸出）")
        elif name not in sums:
            out.append(f"{name}：SHA256SUMS 沒有列")
    for name, digest in sorted(sums.items()):
        path = os.path.join(dist, name)
        if not os.path.isfile(path):
            out.append(f"SHA256SUMS 列了 {name}，但 dist 裡沒有這個檔")
        elif sha256_of(path) != digest:
            out.append(f"{name}：sha256 跟 SHA256SUMS 寫的不一樣")
        elif name not in files:
            out.append(f"SHA256SUMS 列了發佈檔以外的 {name}")
    known = {n for n in files if n.endswith(links.PACK_EXT)}
    for name in sorted(os.listdir(dist)):
        if name.endswith(links.PACK_EXT) and os.path.isfile(os.path.join(dist, name)) and name not in known:
            out.append(f"{name}：index.json 沒有列（舊的檔？重新跑 python scripts/build.py）")
    for e in index["packs"]:
        path = os.path.join(dist, e["file"])
        alias = os.path.join(dist, links.alias_file(e["name"]))
        if not os.path.isfile(path):
            continue
        if e.get("sha256") != sha256_of(path) or e.get("size") != os.path.getsize(path):
            out.append(f"{e['file']}：index.json 的 sha256／size 跟實際的檔不一樣")
        if os.path.isfile(alias) and sha256_of(alias) != sha256_of(path):
            out.append(f"{links.alias_file(e['name'])}：別名跟 {e['file']} 內容不一樣")
        if signed:
            for p in (path, alias):
                if os.path.isfile(p) and not has_signature(p):
                    out.append(f"{os.path.basename(p)}：裡面沒有 {SIG_FILE}（還沒簽）")
    out += _zip_problems(dist, index)
    return out


def _zip_problems(dist: str, index: dict) -> list:
    out = []
    zip_name = tools_zip_name(index)
    zpath, apath = os.path.join(dist, zip_name), os.path.join(dist, links.TOOLS_ALIAS)
    if not (os.path.isfile(zpath) and os.path.isfile(apath)):
        return out                                  # 少檔已經在上面報了
    if sha256_of(zpath) != sha256_of(apath):
        out.append(f"{links.TOOLS_ALIAS}：跟 {zip_name} 內容不一樣")
    tools = [e for e in index["packs"] if e.get("kind") != "theme"]
    tools_index = {k: v for k, v in index.items() if k != "collections"}
    tools_index["packs"] = tools
    want = {"tools/" + e["file"]: os.path.join(dist, e["file"]) for e in tools}
    try:
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
            for n in names:
                data = z.read(n)
                if n == "tools/index.json":
                    if data != build.index_text(tools_index).encode("utf-8"):
                        out.append(f"{zip_name} 的 tools/index.json 跟 index.json 對不上")
                elif n in want:
                    if not os.path.isfile(want[n]) or hashlib.sha256(data).hexdigest() != sha256_of(want[n]):
                        out.append(f"{zip_name} 的 {n} 跟 dist 裡的 {os.path.basename(want[n])} 不一樣")
                else:
                    out.append(f"{zip_name} 裡有 index.json 沒列的 {n}")
    except (OSError, zipfile.BadZipFile) as e:
        return out + [f"{zip_name} 打不開：{e}"]
    for n in sorted(set(want) - set(names)):
        out.append(f"{zip_name} 裡少了 {n}")
    if "tools/index.json" not in names:
        out.append(f"{zip_name} 裡少了 tools/index.json")
    return out


# ── 簽 ─────────────────────────────────────────────────────────────────

def sign_dist(dist: str, key: str, cmd: list, expect: str = "official", say=print) -> dict:
    """簽、驗、重算、檢查，全部過了才換進 dist/。→ {"signed", "skipped", "verified", "recomputed"}；失敗丟 SignError（dist/ 不動）。"""
    if not os.environ.get(PASSWORD_ENV):
        raise SignError(f"沒有設環境變數 {PASSWORD_ENV}（私鑰的密語）：先在這個視窗設好再跑（docs/RELEASING.md「簽章」）。"
                        "這支不會問、也不會印密語")
    if not key or not os.path.isfile(key):
        raise SignError("--key 要給私鑰檔的路徑（檔案不存在）")
    dist = os.path.abspath(dist)
    if not os.path.isdir(dist):
        raise SignError(f"沒有 {dist}：先跑 python scripts/build.py（全部打包）")
    before = problems(dist, signed=False)
    if before:
        raise SignError("dist/ 不是一份完整、對得上的 build.py 輸出（先重新跑 python scripts/build.py）：\n  - "
                        + "\n  - ".join(before))
    index, _text = _load_index(dist)
    for old in os.listdir(dist):                       # 上一次被中斷留下的暫存（只認這支自己的前綴）
        if old.startswith(STAGING_PREFIX) and os.path.isdir(os.path.join(dist, old)):
            shutil.rmtree(os.path.join(dist, old), ignore_errors=True)
    stage = tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=dist)
    try:
        report = _sign_in(stage, dist, index, key, cmd, expect, say)
        _swap_in(stage, dist, index)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    after = problems(dist, signed=True)
    if after:                                          # 換進去之後再對一次（理論上不會發生）
        raise SignError("換進 dist/ 之後檢查沒過：\n  - " + "\n  - ".join(after))
    return report


def _sign_in(stage: str, dist: str, index: dict, key: str, cmd: list, expect: str, say) -> dict:
    signed, skipped, verified = [], [], 0
    for e in index["packs"]:
        target = os.path.join(stage, e["file"])
        shutil.copyfile(os.path.join(dist, e["file"]), target)
        status, out = verify_status(cmd, target)
        if status == expect:
            skipped.append(e["file"])
        elif status == "none":
            code, sign_out = run_sdk(cmd, ["sign", target, "--key", key])
            if code != 0:
                raise SignError(f"{e['file']}：簽章失敗（synox-sdk sign 結束碼 {code}），全部停下、dist/ 沒有改：\n"
                                + _indent(sign_out))
            signed.append(e["file"])
        else:
            what = {"official": "已經是官方簽章", "author": "已經有別把鑰匙的簽章（✍️ 作者簽章）",
                    "invalid": "簽章對不上（被改過）", "": "verify 的輸出看不懂"}[status]
            raise SignError(f"{e['file']}：{what}，預期 {expect}；全部停下、dist/ 沒有改。"
                            "重新跑 python scripts/build.py 再簽，或確認用的是對的鑰匙與 SYNOX：\n" + _indent(out))
        alias = os.path.join(stage, links.alias_file(e["name"]))
        shutil.copyfile(target, alias)                 # 別名＝簽過的那一份（逐位元組相同）
        for path in (target, alias):
            status, out = verify_status(cmd, path)
            if status != expect:
                hint = ("（verify 比對的是跑它的那份 SYNOX 裡的官方公鑰：用已經放進官方公鑰的 SYNOX，"
                        "或確認 --key 是官方鑰匙）" if expect == "official" and status == "author" else "")
                raise SignError(f"{os.path.basename(path)}：簽完驗出來是 {status or '看不懂'}，預期 {expect}{hint}；"
                                "全部停下、dist/ 沒有改：\n" + _indent(out))
            verified += 1
        e["sha256"] = sha256_of(target)                # 只改這兩個欄位
        e["size"] = os.path.getsize(target)
    say(f"簽了 {len(signed)} 個、本來就簽好的 {len(skipped)} 個；驗了 {verified} 個檔（帶版本＋別名），都是 {expect}")
    with open(os.path.join(stage, links.INDEX_FILE), "w", encoding="utf-8", newline="\n") as f:
        f.write(build.index_text(index))
    zips = build.write_tools_zips(index, stage, tools_zip_name(index))
    build.write_sums(stage, release_files(index))
    left = problems(stage, signed=True)
    if left:
        raise SignError("重算之後檢查沒過，dist/ 沒有改：\n  - " + "\n  - ".join(left))
    return {"signed": signed, "skipped": skipped, "verified": verified,
            "recomputed": [links.INDEX_FILE + "（每筆 sha256／size）", *zips, links.SUMS_FILE]}


def _swap_in(stage: str, dist: str, index: dict) -> None:
    """暫存 → dist/：先 .synoxpack、zip、SHA256SUMS，最後 index.json（跟上傳同一個順序）。"""
    names = [n for n in release_files(index) if n != links.INDEX_FILE] + [links.SUMS_FILE, links.INDEX_FILE]
    for n in names:
        os.replace(os.path.join(stage, n), os.path.join(dist, n))


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in text.strip().splitlines()) or "    （沒有印任何字）"


def check_dist(dist: str, cmd: list, expect: str = "official") -> list:
    """--check：一致性＋每個 .synoxpack 都 verify 是預期的標章。"""
    dist = os.path.abspath(dist)
    out = problems(dist, signed=True)
    if out:
        return out
    index, _text = _load_index(dist)
    for e in index["packs"]:
        for name in (e["file"], links.alias_file(e["name"])):
            status, _o = verify_status(cmd, os.path.join(dist, name))
            if status != expect:
                out.append(f"{name}：verify 是 {status or '看不懂'}，預期 {expect}")
    return out


NEXT_STEPS = """下一步（docs/RELEASING.md「上傳到發佈頁」；index.json 一定最後換）：
  1. cd dist && sha256sum -c SHA256SUMS（或 python scripts/sign_dist.py --check）
  2. 備份發佈頁上現在的 index.json（本機 release-backup/ 一份、發佈頁上 index-backup-<時間>.json 一份）
  3. 上傳 *.synoxpack、tools-<VERSION>.zip、tools.zip、SHA256SUMS（--clobber）
  4. 最後才上傳 index.json（--clobber）
  5. 每個連結 HEAD 都 200；之後重新發佈改到的線上頁"""


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="簽 dist/ 的每個 .synoxpack，再用簽過的檔重算 index.json、SHA256SUMS、tools zip")
    parser.add_argument("--key", default="", help="私鑰檔（synox-sdk keygen 產生的 .pem；這支不讀它，只交給 synox-sdk sign）")
    parser.add_argument("--dist", default=os.path.join(_synox.LIBS_ROOT, "dist"), help="build.py 的輸出（預設 dist/）")
    parser.add_argument("--sdk", default="", help="synox-sdk 的路徑（預設：這個 Python 旁邊的 synox-sdk，或 python -m synox.sdk）")
    parser.add_argument("--expect", choices=("official", "author"), default="official",
                        help="簽完 verify 要看到的標章（官方發佈＝official；拿自己的鑰匙試＝author）")
    parser.add_argument("--check", action="store_true", help="只檢查：一致性＋每個檔 verify（不簽、不用密語）")
    args = parser.parse_args(argv)
    cmd = sdk_command(args.sdk)
    if args.check:
        found = check_dist(args.dist, cmd, args.expect)
        for p in found:
            print("  - " + p)
        print(("❌ dist/ 有對不上的地方" if found else f"✅ dist/ 一致，每個 .synoxpack 都是 {args.expect}"))
        return 2 if found else 0
    try:
        report = sign_dist(args.dist, args.key, cmd, args.expect)
    except SignError as e:
        print(f"❌ {e}")
        return 2
    print("重算：" + "、".join(report["recomputed"]))
    print(f"✅ 檢查都過了：SHA256SUMS 每一行對、index.json 每筆＝實際檔、別名＝帶版本那一份、每個 .synoxpack 都有 {SIG_FILE}、"
          f"tools zip 裡的檔＝dist 裡的檔")
    print(NEXT_STEPS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
