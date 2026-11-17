"""對帳單匯入裡**唯一碰檔案的地方**（只讀）：對帳單本身、內建的格式描述檔、使用者自己放的格式描述檔。

- 對帳單：只能在這本帳的檔案資料夾裡（`paths.confined`：擋 `..`、擋跑出去的絕對路徑）；錯誤訊息不帶資料夾位置。
- 內建的格式描述檔：工具自己資料夾裡的 `engine/formats/*.json`。
- 使用者的格式描述檔：自己的檔案資料夾底下的 `formats/*.json`（跟對帳單放同一個地方，不讀別的地方）。

只讀、不寫；檔案大小有上限（記憶體）。其他檔只拿這裡回的字串。
"""
from __future__ import annotations

from pathlib import Path

from ... import paths
from ...errors import InvalidInput, NotFound

BUILTIN = Path(__file__).parent.parent.parent / "formats"
USER_DIR = "formats"
MAX_STATEMENT = 5 * 1024 * 1024           # 對帳單最大 5MB
MAX_FORMAT = 256 * 1024                   # 描述檔最大 256KB


def statement(raw: str | Path | None) -> tuple[str, bytes]:
    """對帳單 → (檔名, 內容)。"""
    p = paths.confined(raw)
    if not p.is_file():
        raise NotFound(f"找不到檔案「{p.name}」", hint="檔案要放在自己的檔案資料夾裡，寫檔名就好")
    data = p.read_bytes()
    if len(data) > MAX_STATEMENT:
        raise InvalidInput("檔案太大（超過 5MB），請分成幾個檔")
    return p.name, data


def _json_files(folder: Path) -> list[tuple[str, str]]:
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.json")):
        if not p.is_file():
            continue
        data = p.read_bytes()
        if len(data) > MAX_FORMAT:
            out.append((p.name, ""))                 # 太大：當成壞的描述檔（formats 會列出原因）
            continue
        out.append((p.name, data.decode("utf-8-sig", errors="replace")))
    return out


def builtin_formats() -> list[tuple[str, str]]:
    """內建的描述檔 → [(檔名, 內容)]。"""
    return _json_files(BUILTIN)


def user_formats() -> list[tuple[str, str]]:
    """使用者自己資料夾裡 formats/ 的描述檔 → [(檔名, 內容)]（沒有這個資料夾＝沒有）。"""
    return _json_files(paths.confined(USER_DIR))
