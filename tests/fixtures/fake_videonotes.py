"""假的 Video Notes 命令列（只給 tests/test_video_notes.py 用；只用標準函式庫，不連網、不下載、不叫 AI、不開埠）。

照真的 VideoNotes 的 `--json` 合約（它 README 的「給程式呼叫」）：stdout 一份 indent=2 的 {"ok", "data"|"error"}，
進度印在 stderr（「[標題] 下載…」「[標題] 下載：完成」），退出碼 0／2／3／4／1。每次被叫都把 argv 記到 <out>/_argv.jsonl。

網址裡有 `fail` → 網路錯誤（退出碼 4）；有 `slow` → 印完前兩個階段就等 60 秒（給取消、看進度的測試）；
有 `garbage` → 不印 JSON；其他 → 做一份假筆記 notes.general.md。
"""
import argparse
import json
import os
import sys
import time

STAGES = ("影片資訊", "下載", "抽音訊", "語音辨識", "AI 筆記")


def emit(payload, code=0):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.flush()
    return code


def fail(code, reason, action="", exit_code=1):
    return emit({"ok": False, "error": {"code": code, "reason": reason, "retryable": exit_code == 4,
                                        "remedy": {"kind": "none", "action": action, "url": ""}, "detail": "",
                                        "source": "fake"}}, exit_code)


def progress(label, text):
    print(f"[{label}] {text}", file=sys.stderr, flush=True)


def video(root, url, n):
    vid = url.rstrip("/").split("=")[-1].split("/")[-1][:20] or f"v{n}"
    title = f"假影片 {vid}"
    folder = os.path.join(root, f"{title} [video-{vid}]")
    os.makedirs(folder, exist_ok=True)
    label = title[:40]
    for i, stage in enumerate(STAGES):
        progress(label, f"{stage}…")
        if "slow" in url and i == 2:
            time.sleep(60)
        progress(label, f"{stage}：完成")
    notes = f"# {title}\n\n> 🔗 {url}\n\n## 一句話結論\n\n這是假的筆記（{vid}）。\n"
    path = os.path.join(folder, "notes.general.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(notes)
    with open(os.path.join(folder, "info.json"), "w", encoding="utf-8") as f:
        json.dump({"url": url, "video": {"title": title}}, f, ensure_ascii=False)
    return {"url": url, "title": title, "folder": folder, "notes": notes, "notes_file": path, "profile": "general",
            "status": "done", "error": None}


def library(root):
    videos, cols = [], []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        folder = os.path.join(root, name)
        if os.path.isfile(os.path.join(folder, "info.json")):
            with open(os.path.join(folder, "info.json"), encoding="utf-8") as f:
                info = json.load(f)
            notes = sorted(n[len("notes."):-len(".md")] for n in os.listdir(folder) if n.startswith("notes."))
            videos.append({"type": "video", "folder": folder, "name": name, "title": info["video"]["title"],
                           "url": info["url"], "notes": notes, "analyses": [], "outputs": [], "transcript": True,
                           "updated": os.path.getmtime(os.path.join(folder, "info.json"))})
    base = os.path.join(root, "collections")
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        folder = os.path.join(base, name)
        notes = sorted(n[len("notes."):-len(".md")] for n in os.listdir(folder) if n.startswith("notes."))
        cols.append({"type": "collection", "folder": folder, "name": name, "title": name, "url": "", "notes": notes,
                     "members": [], "updated": os.path.getmtime(folder)})
    return {"videos": videos, "collections": cols}


def main(argv):
    p = argparse.ArgumentParser(prog="VideoNotes")
    p.add_argument("urls", nargs="*")
    p.add_argument("-f", "--file")
    p.add_argument("-o", "--out")
    p.add_argument("-p", "--profile")
    p.add_argument("-w", "--with", dest="modifiers", default="")
    p.add_argument("--preset", default="")
    p.add_argument("--focus", default="")
    p.add_argument("--outputs", default="")
    p.add_argument("--reuse", action="store_true")
    p.add_argument("--redo", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--library", action="store_true")
    p.add_argument("--combine", action="store_true")
    p.add_argument("--folders", default="")
    p.add_argument("--title", default="")
    p.add_argument("-y", "--yes", action="store_true")
    args = p.parse_args(argv)
    if args.file:                                   # 跟真的一樣：一行一個網址，# 開頭的略過
        with open(args.file, encoding="utf-8-sig") as f:
            args.urls += [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    root = args.out or os.path.join(os.getcwd(), "fake-out")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "_argv.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "urls": args.urls}, ensure_ascii=False) + "\n")
    if not args.json:
        return fail("bad_input", "測試裡一定要 --json", exit_code=2)
    if args.check:
        return emit({"ok": True, "data": {"checks": [{"name": "ffmpeg", "level": "ok", "message": "找到了"},
                                                     {"name": "yt-dlp", "level": "warn", "message": "已經 40 天沒更新"}],
                                          "summary": {"total": 2, "error": 0, "warn": 1}}})
    if args.library:
        return emit({"ok": True, "data": library(root)})
    if args.combine:
        folders = [f for f in args.folders.split(",") if f]
        if len(folders) > 2 and not args.yes:
            return fail("bad_input", f"合併 {len(folders)} 支影片，規模較大需要確認", "確定要做的話加上 --yes", exit_code=2)
        progress("合併", "AI 合併中…")
        folder = os.path.join(root, "collections", f"20300101-000000 {args.title or '合併'}")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "notes.general.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {args.title or '合併'}\n\n合併了 {len(folders)} 支。\n")
        return emit({"ok": True, "data": {"videos": [], "collection": {"title": args.title or "合併", "folder": folder,
                                                                         "notes_file": path, "notes": "…"}}})
    if not args.urls:
        return fail("bad_input", "沒有網址", exit_code=2)
    if any("garbage" in u for u in args.urls):
        print("Traceback (most recent call last):\n  boom", file=sys.stderr)
        return 1
    entries, errors = [], []
    for n, url in enumerate(args.urls, 1):
        if "fail" in url:
            err = {"code": "network_unreachable", "reason": "連不上影片網站", "retryable": True,
                   "remedy": {"kind": "none", "action": "檢查網路後再試一次", "url": ""}, "detail": "", "source": "yt-dlp"}
            errors.append(err)
            entries.append({"url": url, "title": "", "folder": "", "status": "failed", "error": err})
            continue
        entries.append(video(root, url, n))
    data = entries[0] if len(args.urls) == 1 else entries
    if errors:
        return emit({"ok": False, "error": errors[0], "data": data}, 4)
    return emit({"ok": True, "data": data})


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
