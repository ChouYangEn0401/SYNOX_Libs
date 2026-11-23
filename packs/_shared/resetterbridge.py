"""
resetterbridge：SYNOX 的工具要叫「這台電腦上另外裝好的 ClaudeTimerResetter」時共用的那一小段
（claude-resetter、claude-relay 兩個工具都用它，所以兩邊只維護這一份）。

這裡是**正本**。SYNOX 的 loader 只執行 pack 資料夾裡的 pack.py（不把資料夾放進 sys.path），pack 之間也不能互相 import，
所以每個用到它的 pack.py 裡都有一份一模一樣的副本，夾在 `# >>> resetterbridge` 與 `# <<< resetterbridge` 兩行之間。
改這裡之後跑 `python scripts/sync_shared.py`，tests 會檢查每份副本都跟正本一樣。

它做的事只有三件，而且都不碰網路、不讀 ~/.claude、不讀任何金鑰：
- 找到使用者設的 resetter 資料夾與它旁邊 .venv 的 Python（`resolve_resetter`）；
- 組出「python -m <resetter 的模組> …」的 argv 陣列（`module_argv`；只認 MODULES 裡的三個入口，不能叫別的）；
- 從程式輸出裡讀出最後一份 JSON（`last_json`；resetter 的 `--json` 印一份 JSON，進度與雜訊在前面）。

怎麼開程式不在這裡：由各工具用 SYNOX 的 JobRunner（app.jobs）開（argv 陣列、不開視窗、逾時、取消整棵程序樹）。
只有「從原始碼跑」的 resetter 有命令列入口；只裝了 ClaudeTimerResetter.exe（視窗版）的不能用——要 clone 專案、跑一次 scripts\\setup.bat。
"""

import json
import os

# >>> resetterbridge（正本 packs/_shared/resetterbridge.py；不要直接改這段，改正本再跑 scripts/sync_shared.py）
RESETTER_MODULES = {"usage": "claude_timer.core.usage",     # 看額度（不花錢，純 HTTPS GET）
                    "ping": "claude_timer.core.ping",       # 該刷新就刷新（花約 $0.00025）
                    "cli": "claude_timer.cli"}              # 接續／新對話（relay 用）


class BridgeProblem(Exception):
    """回使用者一句話（指令回訊息、畫面顯示），不算工具壞掉。"""

    def __init__(self, text):
        super().__init__(text)
        self.text = str(text)


def resolve_resetter(settings, setup_command):
    """設定 → (python 路徑, resetter 資料夾)。找不到就丟 BridgeProblem（白話：哪裡不對、怎麼改）。
    settings["home"]＝ClaudeTimerResetter 的 clone 資料夾；settings["python"]（可留空）＝要用的 Python，留空就用 home\\.venv 的。"""
    home = (settings.get("home") or "").strip().strip('"')
    if not home:
        raise BridgeProblem(f"還沒告訴我 ClaudeTimerResetter 在哪裡。請你本人打：{setup_command} home=<ClaudeTimerResetter 的資料夾>\n"
                            "（要是從原始碼跑的那份：資料夾裡有 claude_timer\\，跑過一次 scripts\\setup.bat 建好 .venv）")
    folder = os.path.abspath(os.path.expanduser(home))
    if not os.path.isfile(os.path.join(folder, "claude_timer", "__init__.py")):
        raise BridgeProblem(f"這個資料夾裡找不到 claude_timer（不是 ClaudeTimerResetter 的原始碼資料夾？）：{folder}\n"
                            f"用 {setup_command} home=… 重新設定。只有 exe 版的不能用，要 clone 專案。")
    python = (settings.get("python") or "").strip().strip('"')
    if not python:
        for rel in ((".venv", "Scripts", "python.exe"), (".venv", "bin", "python")):
            if os.path.isfile(os.path.join(folder, *rel)):
                python = os.path.join(folder, *rel)
                break
    if not python:
        raise BridgeProblem("ClaudeTimerResetter 旁邊沒有 .venv（它自己的 Python 環境）。先在它的資料夾跑一次 scripts\\setup.bat，"
                            f"或用 {setup_command} python=<python.exe> 指定。")
    if not os.path.isfile(python):
        raise BridgeProblem(f"找不到設定的 Python：{python}\n用 {setup_command} python=… 改（留空＝用 .venv 的）。")
    return python, folder


def module_argv(python, entry, tail):
    """[python, -m, <模組>, *tail]。entry 只能是 RESETTER_MODULES 的鍵；tail 每一項都得是字串，不准空位元組。"""
    module = RESETTER_MODULES.get(entry)
    if module is None:
        raise BridgeProblem(f"內部錯誤：不認得的入口 {entry!r}")
    items = [str(t) for t in tail]
    if any("\x00" in t for t in items):
        raise BridgeProblem("參數裡有不能用的字元。")
    return [python, "-m", module] + items


def last_json(text):
    """輸出裡最後一份 JSON 物件（可以印好幾行；前面是進度與雜訊）。沒有回 None。"""
    text = text or ""
    lines = text.splitlines(keepends=True)
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)
    decoder = json.JSONDecoder()
    for i in range(len(lines) - 1, -1, -1):
        if not lines[i].startswith("{"):
            continue
        try:
            data, _end = decoder.raw_decode(text[offsets[i]:])
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None
# <<< resetterbridge
