"""📁 專案：每個專案「停在哪、為什麼停、回來先做什麼」。指令 /proj、/proj_list、/proj_pause…，另附完整的網頁畫面。

    指令      kit.command(group, name, …)            人打字、AI、排程都走這裡
    畫面動作  pack.app_action(name, …)               同名、同危險程度的指令；欄位檢查、權限、稽核照那個指令
    畫面讀    pack.app_read(name, …)

邏輯在 engine/model.py（不碰檔案）與 engine/scan.py（唯一會讀資料夾的地方，只讀）。
資料經 pack.store.of(user)（保管箱，密文）；每個人一份。這個檔要以套件身分載入（pack.json python.package=true）。

跟別的工具的連結：這個工具完全獨立，不 import、不呼叫任何其他工具。給別的工具讀的出口是 `due_back` 這個讀
（說好要回來看的專案與日期；pack.json provides: projects.due_back），有裝才用、沒裝什麼都不會發生。
"""

from isdtelekit.core.registry import DANGER, WRITE, Field

from synox.packs import kit

from .engine import model, scan

CLEAR = model.CLEAR
STATE_CHOICES = tuple(s["id"] for s in model.STATES)


def _yes_no(value: str):
    return None if str(value).strip().lower() in ("yes", "no", "true", "false", "1", "0", "是", "否", "-") else "要寫 yes 或 no"


def _iso_or_clear(value: str):
    return None if str(value).strip() == CLEAR else kit.checks.iso_date(value)


def _pid(value: str):
    return None if value and len(value) <= 32 and value.replace("-", "").replace("_", "").isalnum() else "專案編號是英數字（/proj_list 看）"


F_ID = Field("id", "專案編號（/proj_list 看）", required=True, check=_pid)
F_NAME = Field("name", "專案名稱（60 字內）", required=True, check=kit.checks.max_len(60))
F_PATH = Field("path", "資料夾位置（完整路徑；不寫＝不看資料夾）", check=kit.checks.max_len(400))
F_STATE = Field("state", "狀態", choices=STATE_CHOICES)
F_REASON = Field("reason", "為什麼停（編號；內建 api／detour／block／pivot，或自訂的）", check=kit.checks.max_len(16))
F_NOTE = Field("reason_note", "為什麼停：一句話補充", check=kit.checks.max_len(400))
F_REF = Field("reason_ref", "這次讓位給哪個專案（專案編號）", check=kit.checks.max_len(32))
F_RESUME = Field("resume_at", "什麼時候回來看（YYYY-MM-DD；-＝清掉）", check=_iso_or_clear)
F_WHY = Field("why_now", "為什麼現在做這個", check=kit.checks.max_len(400))
F_STATUS = Field("status", "現在做到哪", check=kit.checks.max_len(400))
F_NEXT = Field("next", "回來先做什麼", check=kit.checks.max_len(400))
F_TAGS = Field("tags", "標籤（逗號分開）", check=kit.checks.max_len(200))
F_PINNED = Field("pinned", "標記放前面 yes／no", check=_yes_no)

UPDATE_FIELDS = (F_ID, Field("name", "專案名稱", check=kit.checks.max_len(60)), F_PATH, F_STATE, F_REASON, F_NOTE,
                 F_REF, F_RESUME, F_WHY, F_STATUS, F_NEXT, F_TAGS, F_PINNED)
PAUSE_FIELDS = (F_ID, F_REASON, F_NOTE, F_REF, F_RESUME, F_NEXT)
VALUE_KEYS = tuple(k for k in model.UPDATABLE)


def _values(values: dict, keys=VALUE_KEYS) -> dict:
    """指令／畫面送來的字串 → 只留有給的欄位（沒給的不動；給 - 的由 model 清掉）。"""
    return {k: values[k] for k in keys if k in values and values[k] is not None and values[k] != ""}


def install(app, pack):
    group = pack.group

    def load(user) -> dict:
        return model.normalize(pack.store.of(user).load(None) or model.empty())

    def change(user, fn):
        """讀－改－存（同一個保管箱交易）；fn(data) 的回傳值原樣交回。"""
        def apply(data):
            model.normalize(data)
            return fn(data)
        return pack.store.of(user).update(apply, model.empty())

    def one(data, pid):
        p = model._find(data, pid)
        resolved, exists = scan.resolve(p["path"])
        return model.view(p, resolved, exists)

    # ---- 核心：指令與畫面共用，回 dict（畫面）；指令再把它排成一句話 ----

    def core_add(user, values):
        def go(data):
            rec = model.add(data, values.get("name", ""), values.get("path", ""))
            return rec["id"]
        pid = change(user, go)
        return {"ok": True, "project": one(load(user), pid)}

    def core_update(user, values):
        pid = values["id"]
        fields = _values(values)
        change(user, lambda data: model.update(data, pid, **fields))
        return {"ok": True, "project": one(load(user), pid)}

    def core_pause(user, values):
        pid = values["id"]
        fields = _values(values, ("reason", "reason_note", "reason_ref", "resume_at", "next"))
        change(user, lambda data: model.update(data, pid, state="paused", **fields))
        return {"ok": True, "project": one(load(user), pid)}

    def core_resume(user, values):
        pid = values["id"]
        # 回來做：回訪日清掉（約定完成了），原因留著——「上次是被誰插隊的」三個月後還要看得到
        change(user, lambda data: model.update(data, pid, state="active", resume_at=CLEAR))
        return {"ok": True, "project": one(load(user), pid)}

    def core_rm(user, values):
        rec = change(user, lambda data: model.delete(data, values["id"]))
        if rec is None:
            raise kit.AppError(f"找不到專案：{values['id']}", code="not_found", status=404)
        return {"ok": True, "id": rec["id"], "name": rec["name"]}

    def core_restore(user, values):
        rec = change(user, lambda data: model.restore(data, values["id"]))
        if rec is None:
            raise kit.AppError("回收區裡沒有這個專案（只留最近 10 筆）", code="not_found", status=404)
        return {"ok": True, "id": rec["id"], "name": rec["name"]}

    def core_reorder(user, values):
        ids = [x.strip() for x in values["ids"].split(",") if x.strip()]
        return {"ok": bool(change(user, lambda data: model.reorder(data, ids)))}

    def core_scan(user, values):
        """掃資料夾算「真的多久沒動」。掃描（讀磁碟）在保管箱交易外面做，最後只把結果寫回去。"""
        pid = values.get("id", "")
        data = load(user)
        targets = [p for p in data["projects"] if not pid or p["id"] == pid]
        if pid and not targets:
            raise kit.AppError(f"找不到專案：{pid}", code="not_found", status=404)
        results = {p["id"]: scan.scan_one(p["path"]) for p in targets}
        change(user, lambda d: model.apply_scan(d, results))
        return {"ok": True, "scanned": len(targets)}

    def core_reason_add(user, values):
        rec = change(user, lambda data: model.add_reason(data, values["label"], values.get("icon", ""), values.get("ask", "")))
        return {"ok": True, "reason": rec}

    def core_reason_retire(user, values):
        return {"ok": True, "reason": change(user, lambda data: model.retire_reason(data, values["id"]))}

    def guard(fn):
        """引擎丟的 ValueError（人看得懂的一句話）→ kit.AppError；其他的錯原樣交給 SYNOX。"""
        def run(*args):
            try:
                return fn(*args)
            except ValueError as e:
                raise kit.AppError(str(e), code="invalid", status=400)
        return run

    # ---- 指令（人打字）：把核心的結果排成一句話 ----

    def say(core, text_of):
        guarded = guard(core)

        def handler(api, chat_id, user, values):
            try:
                result = guarded(user, values)
            except kit.AppError as e:
                api.send_message(chat_id, f"❌ {e.message}")
                return
            api.send_message(chat_id, text_of(result, values))
        return handler

    def line(p) -> str:
        st = next((s for s in model.STATES if s["id"] == p["state"]), {"icon": "•"})
        bits = [f"{st['icon']} [{p['id']}] {p['name']}"]
        if p.get("next"):
            bits.append("▸ " + p["next"])
        elif p.get("status"):
            bits.append("· " + p["status"])
        if p.get("is_cold"):
            bits.append(f"🧊 {p['idle_days']} 天沒動")
        if p.get("resume_days") is not None:
            bits.append("🔖 " + (f"{p['resume_days']} 天後回來看" if p["resume_days"] > 0 else "說好今天回來看" if p["resume_days"] == 0 else f"說好 {-p['resume_days']} 天前回來看"))
        return "　".join(bits)

    def reason_text(data, p) -> str:
        found = next((r for r in model.meta(data)["reasons"] + model.meta(data)["legacy_reasons"] if r["id"] == p.get("reason")), None)
        if not found:
            return ""
        ref = next((x["name"] for x in data["projects"] if x["id"] == p.get("reason_ref")), "")
        return f"{found['icon']} {found['label']}" + (f" → {ref}" if ref else "")

    kit.command(group, "add", say(core_add, lambda r, v: f"✅ 開始追蹤：{line(r['project'])}"), "追蹤一個新專案",
                default=True, effect=WRITE, positional=1, fields=[F_NAME, F_PATH],
                usage="<名稱> [path=資料夾位置]")

    def list_(api, chat_id, user, values):
        data = load(user)
        which = values.get("filter", "").strip()
        shown = model.list_all(data, scan.resolve)["projects"]
        if which == "cold":
            shown = [p for p in shown if p["is_cold"]]
        elif which == "due":
            shown = [p for p in shown if p["resume_due"]]
        elif which in STATE_CHOICES:
            shown = [p for p in shown if p["state"] == which]
        else:
            shown = [p for p in shown if p["state"] != "archived"]      # 放下的不天天出現在眼前
        if not shown:
            api.send_message(chat_id, "📁 沒有符合的專案。用 /proj 名稱 追蹤一個。")
            return
        api.send_message(chat_id, "\n".join([f"📁 專案（{len(shown)} 個）"] + [line(p) for p in shown]))

    kit.command(group, "list", list_, "看專案（不寫＝全部，放下的不列；也可以寫狀態、cold 冷掉的、due 說好回來看的）",
                fields=[Field("filter", "狀態（idea／active／done／paused／archived／mirror）或 cold／due",
                              check=lambda v: None if v in STATE_CHOICES + ("cold", "due") else "看不懂這個篩選")])

    def show(api, chat_id, user, values):
        data = load(user)
        try:
            p = one(data, values["id"])
        except ValueError as e:
            api.send_message(chat_id, f"❌ {e}")
            return
        rows = [line(p)]
        for label, key in (("現在做到哪", "status"), ("回來先做什麼", "next"), ("為什麼現在做", "why_now"),
                           ("為什麼停", "reason_note")):
            if p.get(key):
                rows.append(f"{label}：{p[key]}")
        if reason_text(data, p):
            rows.append("原因：" + reason_text(data, p))
        if p["path"]:
            rows.append(f"位置：{p['path']}" + ("" if p["path_exists"] else "（找不到這個資料夾）"))
        api.send_message(chat_id, "\n".join(rows))

    kit.command(group, "show", show, "看一個專案的細節", fields=[F_ID])
    kit.command(group, "update", say(core_update, lambda r, v: f"✅ 改好了：{line(r['project'])}"),
                "改一個專案的欄位（名稱、位置、狀態、做到哪、下一步…；給 - ＝清掉）", effect=WRITE, positional=1,
                fields=UPDATE_FIELDS, usage="<編號> [name=…] [state=…] [status=…] [next=…] [path=…] [tags=…]")
    kit.command(group, "pause", say(core_pause, lambda r, v: f"⏸ 先停一下：{line(r['project'])}"),
                "暫停一個專案：記為什麼停、讓位給誰、什麼時候回來看", effect=WRITE, positional=1, fields=PAUSE_FIELDS,
                usage="<編號> [reason=…] [reason_note=…] [reason_ref=…] [resume_at=YYYY-MM-DD] [next=…]")
    kit.command(group, "resume", say(core_resume, lambda r, v: f"▶ 回來做了：{line(r['project'])}"),
                "從暫停回到進行中（回訪日清掉，原因留著）", effect=WRITE, fields=[F_ID])
    kit.command(group, "scan", say(core_scan, lambda r, v: f"⟳ 掃描了 {r['scanned']} 個專案，「幾天沒動」現在看的是資料夾裡真的有沒有動"),
                "掃資料夾，算出真正多久沒動（看 git 最後一次紀錄與檔案修改時間；只讀）", effect=WRITE, positional=1,
                fields=[Field("id", "專案編號（不寫＝全部）", check=lambda v: None if v == "" else _pid(v))])
    kit.command(group, "rm", say(core_rm, lambda r, v: f"🗑 停止追蹤「{r['name']}」（資料夾與檔案完全不會動；/proj_restore {r['id']} 可以復原）"),
                "停止追蹤一個專案（只刪這裡的紀錄；先放進回收區，可以復原）", effect=DANGER, fields=[F_ID])
    kit.command(group, "restore", say(core_restore, lambda r, v: f"↩ 復原了「{r['name']}」"),
                "把剛停止追蹤的專案放回來（回收區留最近 10 筆）", effect=WRITE, fields=[F_ID])
    kit.command(group, "reorder", say(core_reorder, lambda r, v: "✅ 排好了"), "照給的編號順序排專案", effect=WRITE,
                fields=[Field("ids", "專案編號，逗號分開，由前到後", required=True, check=kit.checks.max_len(800))])
    kit.command(group, "reason_add", say(core_reason_add, lambda r, v: f"✅ 多了一個原因：{r['reason']['icon']} {r['reason']['label']}"),
                "加一個自訂的暫停原因（之後暫停時可以選）", effect=WRITE, positional=1,
                fields=[Field("label", "原因（12 字內）", required=True, check=kit.checks.max_len(12)),
                        Field("icon", "圖示（一個 emoji）", check=kit.checks.max_len(4)),
                        Field("ask", "按下後追問的一句", check=kit.checks.max_len(40))])
    kit.command(group, "reason_retire", say(core_reason_retire, lambda r, v: f"✅ 退休了「{r['reason']['label']}」（舊紀錄照樣看得到）"),
                "退休一個自訂原因（不再出現在選單，不刪紀錄）", effect=WRITE, fields=[Field("id", "原因編號", required=True, check=kit.checks.max_len(16))])

    # ---- 畫面 ----

    if not hasattr(pack, "app_read"):               # 舊版 SYNOX 沒有工具服務：照樣能用指令
        return

    def read_projects(user, params):
        return model.list_all(load(user), scan.resolve)

    def read_path_check(user, params):
        resolved, exists = scan.resolve(params.get("path", ""))
        return {"resolved": resolved, "exists": exists, "relative": bool(params.get("path", "").strip()) and not resolved}

    def read_due_back(user, params):
        """給別的工具（例如行事曆）選用：說好要回來看的專案與日期。這個工具不知道、也不需要誰在讀。"""
        rows = model.list_all(load(user))["projects"]
        return {"due": [{"id": p["id"], "name": p["name"], "resume_at": p["resume_at"], "days": p["resume_days"]}
                        for p in rows if p["resume_at"] and p["state"] not in model.RESTING_STATES]}

    pack.app_read("projects", read_projects)
    pack.app_read("path_check", read_path_check)
    pack.app_read("due_back", read_due_back)
    for name, core in (("add", core_add), ("update", core_update), ("pause", core_pause), ("resume", core_resume),
                       ("scan", core_scan), ("restore", core_restore), ("reorder", core_reorder),
                       ("reason_add", core_reason_add), ("reason_retire", core_reason_retire)):
        pack.app_action(name, guard(core))
    pack.app_action("rm", guard(core_rm), describe=lambda user, values: _describe(load(user), values))


def _describe(data, values) -> str:
    hit = next((p for p in data["projects"] if p["id"] == values.get("id")), None)
    name = hit["name"] if hit else values.get("id", "")
    return f"停止追蹤「{name}」？只刪這裡的紀錄，資料夾與檔案完全不會動（可以在畫面上復原）。"
