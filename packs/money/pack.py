"""💰 記帳 0.4.0 的入口：install() 只把 bridge/ 8 個領域檔的表掛到 SYNOX 上。

    指令      kit.command(group, cmd.name, …)            /money_<name>；人打字、AI、排程都走這裡
    畫面動作  pack.app_action(cmd.name, …)（cmd.app=True）  同名、同危險程度；欄位檢查、權限、稽核照那個指令
    畫面讀    pack.app_read(name, …)
    匯出      pack.app_export(name, …)
    小面板    pack.web_route(name, …)                     （pack.json web.api）

每一次呼叫都經 bridge/session.py 的 open_book(pack, user) 拿「這個人的帳本」，用完就關（隔離規則，CONTRACT.md）。
畫面那一層（讀、動作、匯出、確認框）開帳本時帶 screen=True：畫面送來的金額已經是最小單位，指令那一層是人打的字。
這個檔要以套件身分載入（pack.json python.package=true）：bridge/、engine/ 用相對 import 取用。
0.3.1 的單檔版本在 git 歷史裡（舊資料格式見那一版的 upgrade()）。
"""

from synox.packs import kit

from .bridge import compat, flow, home, ledger, piggy, plan, review, session, setup, table

DOMAINS = (ledger, home, plan, setup, flow, review, piggy, compat)


def install(app, pack):
    session.attach(pack)
    tables = table.collect(DOMAINS)
    group = pack.group
    for cmd in tables.commands:
        kit.command(group, cmd.name, _command(pack, cmd), cmd.summary, fields=cmd.fields,
                    positional=table.positional_of(cmd), effect=cmd.effect, default=cmd.default, ai=bool(cmd.ai))
    for name, fn in tables.web_routes.items():
        pack.web_route(name, _route(pack, fn))
    if not hasattr(pack, "app_read"):               # 舊版 SYNOX 沒有工具服務：照樣能用指令
        return
    for name, fn in tables.reads.items():
        pack.app_read(name, _guarded(pack, fn))
    for cmd in tables.commands:
        if cmd.app:
            pack.app_action(cmd.name, _action(pack, cmd), describe=_describe(pack, cmd) if cmd.describe else None)
    for name, fn in tables.exports.items():
        pack.app_export(name, _guarded(pack, fn))


def _command(pack, cmd):
    """指令那一層：run → text（＋提醒）；kit.AppError 與引擎的錯回「❌ …」，其他的錯交給 SYNOX。"""
    run = cmd.run

    def handler(api, chat_id, user, values):
        try:
            with session.open_book(pack, user) as book:
                result = run(book, values)
        except Exception as e:
            refused = table.as_app_error(e)
            if refused is None:
                raise
            api.send_message(chat_id, f"❌ {refused.message}")
            return
        api.send_message(chat_id, table.reply_text(result))
    return handler


def _action(pack, cmd):
    """畫面動作那一層：run → data（dict）。"""
    run = cmd.run

    def call(book, values):
        return table.action_data(run(book, values))
    return _guarded(pack, call)


def _guarded(pack, fn):
    """畫面的讀、動作、匯出：fn(book, params_or_values)；引擎的錯轉成 kit.AppError（訊息原樣給人看），
    其他的錯原樣丟給 SYNOX（SYNOX 不讓訊息出現在回應、log、稽核）。"""
    def handler(user, values):
        try:
            with session.open_book(pack, user, screen=True) as book:
                return fn(book, values)
        except Exception as e:
            refused = table.as_app_error(e)
            if refused is None:
                raise
            raise refused
    return handler


def _describe(pack, cmd):
    describe = cmd.describe

    def handler(user, values):
        with session.open_book(pack, user, screen=True) as book:
            return describe(book, values)
    return handler


def _route(pack, fn):
    def handler(user):
        with session.open_book(pack, user) as book:
            return fn(book)
    return handler
