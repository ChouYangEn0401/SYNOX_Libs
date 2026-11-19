"""🎯 里程碑與習慣的入口：install() 把 bridge.py 的指令表掛到 SYNOX 上。

    指令      kit.command(group, cmd.name, …)         /habit …、/habit_list、/habit_ms_new …；人打字、AI、排程都走這裡
    畫面動作  pack.app_action(cmd.name, …)（cmd.app）   同名、同危險程度（pack.json web.app.actions）
    畫面讀    pack.app_read(name, …)                    pack.json web.app.reads
    小面板    pack.web_route("summary", …)

資料只有兩份文件，都在這個人的 pack.store（保管箱，密文）：routines（習慣與提醒）、milestones（旅程）。
不連網、不開檔案、不開別的程式、不呼叫別的工具；跟行事曆之類的連動只留 engine/links.py 的空掛鉤。
這個檔要以套件身分載入（pack.json python.package=true）：engine/ 與 bridge.py 用相對 import 取用。
"""
from synox.packs import kit

from . import bridge
from .engine import clock, milestones, routines


def install(app, pack):
    group = pack.group
    for cmd in bridge.COMMANDS:
        kit.command(group, cmd.name, _command(pack, cmd), cmd.summary, fields=cmd.fields,
                    positional=bridge.positional_of(cmd), effect=cmd.effect, default=cmd.default, ai=bool(cmd.ai))
    pack.web_route("summary", lambda user: _summary(pack, user))
    if not hasattr(pack, "app_read"):               # 舊版 SYNOX 沒有工具服務：照樣能用指令
        return
    for name, fn in bridge.READS.items():
        pack.app_read(name, _read(pack, fn))
    for cmd in bridge.COMMANDS:
        if cmd.app:
            pack.app_action(cmd.name, _action(pack, cmd), describe=_describe(pack, cmd) if cmd.describe else None)


def _command(pack, cmd):
    """指令那一層：run → text；kit.AppError 與引擎的「說不行」回「❌ …」，其他的錯交給 SYNOX。"""
    def handler(api, chat_id, user, values):
        try:
            with bridge.Session(pack, user):
                text, _data = cmd.run(values)
        except Exception as e:
            refused = bridge.as_app_error(e)
            if refused is None:
                raise
            api.send_message(chat_id, f"❌ {refused.message}")
            return
        api.send_message(chat_id, text or "✅ 好了")
    return handler


def _action(pack, cmd):
    """畫面動作那一層：run → data（dict）。"""
    def handler(user, values):
        return _guarded(pack, user, lambda: dict(cmd.run(values)[1] or {}))
    return handler


def _describe(pack, cmd):
    def handler(user, values):
        try:
            with bridge.Session(pack, user):
                return cmd.describe(values)
        except Exception:
            return cmd.summary
    return handler


def _read(pack, fn):
    def handler(user, params):
        return _guarded(pack, user, lambda: fn(params))
    return handler


def _guarded(pack, user, call):
    """引擎的「說不行」轉成 kit.AppError（訊息原樣給人看），其他的錯原樣丟給 SYNOX（訊息不進回應、log、稽核）。"""
    try:
        with bridge.Session(pack, user):
            return call()
    except Exception as e:
        refused = bridge.as_app_error(e)
        if refused is None:
            raise
        raise refused


def _summary(pack, user):
    """工具頁的小面板（GET /summary）：今天的習慣與進行中的旅程。"""
    with bridge.Session(pack, user):
        rows = routines.list_routines()
        due = [r for r in rows if r["due_today"] and not r["paused"] and not r["enough_now"]
               and not r["period_ended"] and not r["not_started"]]
        return {"today": clock.today().isoformat(),
                "due": [{"id": r["id"], "name": r["name"], "kind": r["kind"], "at": r["at"]} for r in due],
                "habits": sum(1 for r in rows if r["kind"] == "habit" and not r["period_ended"]),
                "milestones": [{"id": m["id"], "name": m["name"], "emoji": m["emoji"], "done": m["done"],
                                "total": m["total"], "current": m["current"]}
                               for m in milestones.list_all() if m["pct"] < 100]}
