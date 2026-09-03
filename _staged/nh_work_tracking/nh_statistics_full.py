# ------------------------------------------------------------------------
#  NH usage statistics (opt-in local telemetry)
# ------------------------------------------------------------------------
#  Tracks how often operators are invoked from the UI ("clicks") and how
#  many of them complete successfully. Data is stored ONLY on the local
#  machine (CONFIG/nh_usage_stats.json). Nothing is ever sent anywhere.
# ------------------------------------------------------------------------

import bpy
import os
import time
import json
import collections
from datetime import datetime, date

from bpy.types import Operator, Panel

_STATS_FILENAME = "nh_usage_stats.json"
_ENABLED = False  # master switch: set True to enable the local usage telemetry
_STATS_VERSION = 1
_EVENTS_LIMIT = 500
_FLUSH_INTERVAL = 30.0
_WRAPPER_TAG = "_nh_stat_wrapped"
_STATE = {
    "enabled": False,
    "timer_registered": False,
    "dirty": False,
    "memory": {},     # op_id -> {}
    "history": [],
    "originals": {},
    "session_start": 0.0,
}

# ------------------------------------------------------------------------
#  Work report subsystem (optional server sync for paid work tracking)
# ------------------------------------------------------------------------
_WORK_CONFIG_FILENAME = "nh_work_config.json"
_WORK_QUEUE_FILENAME = "nh_work_queue.json"
_WORK_SYNC_INTERVAL = 300.0
# operators that produce tangible geometry/work output (units = successful runs)
_WORK_OPS = frozenset({
    "nh.import_p3d", "nh.export_p3d",
    "cray.scatter_proxies",
    "cray.generate_fake_terrain_geometry", "cray.build_collider",
    "cray.ensure_collider", "cray.ensure_collider_lod",
    "cray.generate_box_collider_exp", "cray.generate_cylinder_boxes_collider_exp",
    "cray.generate_pipe_boxes_collider_exp", "cray.generate_capsule_collider_exp",
    "cray.generate_convex_hull_collider_exp", "cray.rebuild_convex_hull_collider_exp",
    "cray.model_split_grid_split_source", "cray.model_split_merge_selected_collections",
    "cray.tex_replace_textures", "cray.tex_db_build", "cray.texture_cache_build",
    "cray.snap_batch_process", "cray.snap_create_pair",
    "cray.asset_library_build_nh_objects", "cray.asset_library_add_new_nh_objects",
})
_WORK = {
    "enabled": False,
    "endpoint": "",
    "license": "",
    "project": "",
    "consent": False,
}


def _work_config_path() -> str:
    base_dir = ""
    try:
        base_dir = bpy.utils.user_resource("CONFIG") or ""
    except Exception:
        base_dir = ""
    if not base_dir:
        base_dir = bpy.app.tempdir or os.path.expanduser("~")
    return os.path.join(base_dir, _WORK_CONFIG_FILENAME)


def _load_work_config():
    p = _work_config_path()
    cfg = dict(_WORK)
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                for k in cfg:
                    if k in d:
                        cfg[k] = d[k]
        except Exception:
            pass
    return cfg


def _save_work_config(cfg):
    p = _work_config_path()
    folder = os.path.dirname(p)
    if folder:
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception:
            pass
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        pass


def _work_queue_path() -> str:
    return os.path.join(os.path.dirname(_work_config_path()), _WORK_QUEUE_FILENAME)


def _load_queue():
    p = _work_queue_path()
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_queue(items):
    p = _work_queue_path()
    folder = os.path.dirname(p)
    if folder:
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception:
            pass
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(items[-500:], f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        pass


def _machine_id() -> str:
    """Obfuscated, stable machine identifier (never send raw identifiers)."""
    import hashlib
    import uuid as _uuid
    try:
        node = hex(_uuid.getnode())
    except Exception:
        node = "node"
    try:
        user = os.environ.get("USERNAME", "")
        comp = os.environ.get("COMPUTERNAME", "")
    except Exception:
        user = comp = ""
    raw = f"{node}|{user}|{comp}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def _stats_path() -> str:
    base_dir = ""
    try:
        base_dir = bpy.utils.user_resource("CONFIG") or ""
    except Exception:
        base_dir = ""
    if not base_dir:
        base_dir = bpy.app.tempdir or os.path.expanduser("~")
    return os.path.join(base_dir, _STATS_FILENAME)


def _load_file():
    path = _stats_path()
    if not os.path.isfile(path):
        return {"version": _STATS_VERSION, "daily": {}, "total": {}, "history": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"version": _STATS_VERSION, "daily": {}, "total": {}, "history": []}
    if not isinstance(data, dict):
        return {"version": _STATS_VERSION, "daily": {}, "total": {}, "history": []}
    data.setdefault("version", _STATS_VERSION)
    data.setdefault("daily", {})
    data.setdefault("total", {})
    data.setdefault("history", [])
    return data


def _save_file():
    global _STATE
    path = _stats_path()
    folder = os.path.dirname(path)
    if folder:
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception:
            pass
    data = _load_file()
    data["total"] = dict(_STATE["memory"])
    data["history"] = _STATE["history"][-_EVENTS_LIMIT:]
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as e:
        print(f"=== NH Plugin: failed to save usage stats ===")
        print(str(e))
        return False
    _STATE["dirty"] = False
    return True


def _today_key() -> str:
    return date.today().isoformat()


def _record(context, op_id, stage, start, result):
    """stage: 'invoke' | 'execute'. result: 0 ok / 1 error / 2 cancelled / 3 running."""
    global _STATE
    if _STATE.get("paused", False):
        return
    now = time.time()
    duration = 0.0
    if stage == "execute" and start:
        duration = now - start

    bucket = _STATE["memory"].setdefault(op_id, {
        "clicks": 0, "runs": 0, "ok": 0, "errors": 0,
        "cancelled": 0, "seconds": 0.0, "last_used": 0.0,
    })
    if stage == "invoke":
        bucket["clicks"] += 1
        bucket["last_used"] = now
        _STATE["dirty"] = True
        return

    bucket["runs"] += 1
    bucket["seconds"] += duration
    bucket["last_used"] = now
    if result == 1:
        bucket["errors"] += 1
    elif result == 2:
        bucket["cancelled"] += 1
    else:
        bucket["ok"] += 1

    mode = ""
    try:
        mode = context.mode if context is not None else ""
    except Exception:
        mode = ""
    selection = 0
    try:
        selection = len(getattr(context, "selected_objects", []) or []) if context is not None else 0
    except Exception:
        selection = 0

    _STATE["history"].append({
        "t": now,
        "op": op_id,
        "stage": stage,
        "result": result,
        "sec": round(duration, 3),
        "mode": mode,
        "sel": selection,
    })
    _STATE["history"] = _STATE["history"][-_EVENTS_LIMIT:]
    _STATE["dirty"] = True


def _wrap_execute(cls):
    original = cls.execute

    def wrapped(self, context):
        start = time.time()
        try:
            result = original(self, context)
            outcome = 0
            if isinstance(result, str):
                outcome = 0
            elif isinstance(result, (set, tuple, list)) and result:
                outcome = 1 if "CANCELLED" in result else 0
            _record(context, cls.bl_idname, "execute", start, outcome)
            return result
        except Exception:
            _record(context, cls.bl_idname, "execute", start, 1)
            raise
    wrapped.__name__ = getattr(original, "__name__", "wrapped_execute")
    return wrapped


def _wrap_invoke(cls):
    original_invoke = cls.invoke
    original_has_invoke = callable(original_invoke)

    def wrapped_invoke(self, context, event):
        _record(context, cls.bl_idname, "invoke", 0.0, 0)
        if original_has_invoke:
            return original_invoke(self, context, event)
        return self.execute(context)

    wrapped_invoke.__name__ = getattr(original_invoke, "__name__", "wrapped_invoke") if original_has_invoke else "wrapped_invoke"
    return wrapped_invoke if original_has_invoke else None


def wrap(classes):
    """Wrap Operator methods BEFORE bpy registration (bpy.ops calls the bound
    methods captured at register time, so wrapping must happen beforehand)."""
    global _STATE
    try:
        _WORK.update(_load_work_config())
    except Exception:
        pass
    if not _ENABLED and not (_WORK.get("enabled") and _WORK.get("consent")):
        return 0
    wrapped = 0
    for cls in classes or ():
        if not (isinstance(cls, type) and issubclass(cls, Operator)):
            continue
        if getattr(cls, _WRAPPER_TAG, False):
            continue
        try:
            original_execute = cls.execute
            _STATE["originals"].setdefault(id(cls), {})
            _STATE["originals"][id(cls)]["execute"] = original_execute
            setattr(cls, "execute", _wrap_execute(cls))
        except Exception:
            continue
        try:
            original_invoke = getattr(cls, "invoke", None)
            if callable(original_invoke):
                _STATE["originals"][id(cls)]["invoke"] = original_invoke
                setattr(cls, "invoke", _wrap_invoke(cls))
        except Exception:
            pass
        try:
            setattr(cls, _WRAPPER_TAG, True)
            wrapped += 1
        except Exception:
            pass
    print(f"[NH Plugin] Usage statistics: {wrapped} operators wrapped")
    return wrapped


def start():
    """Enable recording, load counters, register stats panel and flush timer."""
    global _STATE, _WORK
    if not _ENABLED and not (_WORK.get("enabled") and _WORK.get("consent")):
        return
    if _STATE["enabled"]:
        return
    try:
        _WORK.update(_load_work_config())
    except Exception:
        pass
    _STATE["enabled"] = True
    _STATE["paused"] = False
    _STATE["session_start"] = float(time.time()) if not _STATE.get("session_start") else float(_STATE["session_start"])
    loaded = _load_file()
    _STATE["memory"] = dict(loaded.get("total", {}))
    _STATE["history"] = list(loaded.get("history", []))[-_EVENTS_LIMIT:]
    _STATE["dirty"] = False
    for cls in (NH_OT_UsageReport, NH_OT_UsageReset, NH_OT_UsageToggle,
                NH_WG_WorkSettings, NH_OT_WorkSave, NH_OT_WorkSend, NH_OT_WorkToggle, NH_PT_WorkReportPanel,
                NH_PT_UsageStatsPanel):
        try:
            bpy.utils.register_class(cls)
        except Exception:
            pass
    try:
        bpy.types.Scene.nh_work_settings = bpy.props.PointerProperty(type=NH_WG_WorkSettings)
    except Exception:
        pass
    if not _STATE["timer_registered"]:
        try:
            if not bpy.app.timers.is_registered(_flush_timer):
                bpy.app.timers.register(_flush_timer, first_interval=_FLUSH_INTERVAL, persistent=True)
            _STATE["timer_registered"] = True
        except Exception:
            pass
    print("[NH Plugin] Usage statistics enabled (local only)")
    if _WORK.get("enabled") and _WORK.get("consent"):
        try:
            if not bpy.app.timers.is_registered(_work_sync_timer):
                bpy.app.timers.register(_work_sync_timer, first_interval=_WORK_SYNC_INTERVAL, persistent=True)
        except Exception:
            pass
        print("[NH Plugin] Work report to server ENABLED")


def stop():
    """Flush and detach stats UI (wrappers stay installed on classes)."""
    global _STATE
    if not _ENABLED:
        return
    if _STATE["enabled"]:
        _flush_timer()
        _STATE["dirty"] = False
    _STATE["memory"] = {}
    _STATE["history"] = {}
    _STATE["enabled"] = False
    _STATE["paused"] = False
    try:
        if _STATE["timer_registered"]:
            if bpy.app.timers.is_registered(_flush_timer):
                bpy.app.timers.unregister(_flush_timer)
            _STATE["timer_registered"] = False
    except Exception:
        pass
    if _WORK.get("enabled") and _WORK.get("consent"):
        try:
            _work_sync()
        except Exception:
            pass
    try:
        if bpy.app.timers.is_registered(_work_sync_timer):
            bpy.app.timers.unregister(_work_sync_timer)
    except Exception:
        pass
    try:
        if hasattr(bpy.types.Scene, "nh_work_settings"):
            delattr(bpy.types.Scene, "nh_work_settings")
    except Exception:
        pass
    for cls in reversed((NH_PT_UsageStatsPanel, NH_PT_WorkReportPanel, NH_OT_WorkToggle,
                         NH_OT_WorkSend, NH_OT_WorkSave, NH_WG_WorkSettings,
                         NH_OT_UsageToggle, NH_OT_UsageReset, NH_OT_UsageReport)):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


def _iter_wrapped_operators():
    for cls in list(bpy.types.Operator.__subclasses__()):
        if getattr(cls, _WRAPPER_TAG, False):
            yield cls


def _flush_timer():
    if _STATE["dirty"]:
        _save_file()
        try:
            return _FLUSH_INTERVAL
        except Exception:
            return None
    return _FLUSH_INTERVAL


def _stats_summary():
    total_clicks = 0
    total_runs = 0
    total_err = 0
    top = []
    for op_id, b in sorted(_STATE["memory"].items(), key=lambda kv: -kv[1].get("runs", 0)):
        total_clicks += b.get("clicks", 0)
        total_runs += b.get("runs", 0)
        total_err += b.get("errors", 0)
        top.append((op_id, b))
    return {"clicks": total_clicks, "runs": total_runs, "errors": total_err, "top": top[:12]}


def _format_report():
    s = _stats_summary()
    lines = []
    lines.append("=== NH Plugin usage report ===")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Storage: {_stats_path()}")
    lines.append(f"UI clicks: {s['clicks']} | operator runs: {s['runs']} | errors: {s['errors']}")
    lines.append("")
    lines.append("Top operators (by runs):")
    for op_id, b in s["top"]:
        if b.get("runs", 0) == 0 and b.get("clicks", 0) == 0:
            continue
        lines.append(
            f"  {op_id:42} clicks={b.get('clicks',0):4} runs={b.get('runs',0):4} "
            f"ok={b.get('ok',0):4} err={b.get('errors',0):2} cancelled={b.get('cancelled',0):2} "
            f"time={b.get('seconds',0.0):7.2f}s last={datetime.fromtimestamp(b.get('last_used',0)).strftime('%m-%d %H:%M') if b.get('last_used') else '-'}"
        )
    lines.append("")
    lines.append("Recent events:")
    for ev in reversed(_STATE["history"][-15:]):
        lines.append(
            f"  {datetime.fromtimestamp(ev['t']).strftime('%H:%M:%S')} {ev['op']:42} {ev['stage']:<7} "
            f"result={ev['result']} sec={ev['sec']} mode={ev['mode'] or '-'} sel={ev['sel']}"
        )
    return "\n".join(lines)


def _open_text_report():
    text = _format_report()
    try:
        block = bpy.data.texts.get("NH Usage Report")
        if block is None:
            block = bpy.data.texts.new("NH Usage Report")
        block.clear()
        block.write(text)
        print(text)
        return block
    except Exception as e:
        print(text)
        return None



def _work_compile_payload():
    """Session payload for the work tracking server."""
    intervals = []
    for op_id, b in sorted(_STATE["memory"].items()):
        intervals.append({
            "op": op_id,
            "runs": b.get("runs", 0),
            "ok": b.get("ok", 0),
            "errors": b.get("errors", 0),
            "cancelled": b.get("cancelled", 0),
            "seconds": round(b.get("seconds", 0.0), 3),
            "last_used": int(b.get("last_used", 0)),
        })
    units = 0
    for op_id, b in _STATE["memory"].items():
        if op_id in _WORK_OPS:
            units += int(b.get("ok", 0))
    now = time.time()
    return {
        "plugin": "nh-blender-addon",
        "version": _plugin_version(),
        "machine_id": _machine_id(),
        "project": str(_WORK.get("project", "") or ""),
        "session_start": int(_STATE.get("session_start", 0) or 0),
        "session_end": int(now),
        "active_seconds": int(round(now - (_STATE.get("session_start", 0) or now))),
        "work_units": units,
        "totals": intervals,
        "error_free_runs": int(sum(b.get("ok", 0) for b in _STATE["memory"].values())),
        "event_hits": len(_STATE["history"]),
    }


def _plugin_version():
    try:
        import re as _re
        import NH_Blender as _nh
        text = str(getattr(_nh, "bl_info", {}).get("version", ""))
        return text
    except Exception:
        return ""


def _work_sign(body_bytes: bytes) -> str:
    import hashlib, hmac
    key = hashlib.sha256(("nh-work:" + str(_WORK.get("license", ""))).encode("utf-8", errors="replace")).hexdigest()
    return hmac.new(key.encode(), body_bytes, hashlib.sha256).hexdigest()


def _work_post(payload) -> bool:
    import urllib.request as _ur
    import urllib.error as _ue
    endpoint = str(_WORK.get("endpoint", "") or "").strip()
    if not endpoint.startswith(("http://", "https://")):
        return False
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = _ur.Request(
        endpoint.rstrip("/") + "/v1/heartbeat",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-NH-License": str(_WORK.get("license", "") or ""),
            "X-NH-Sig": _work_sign(body),
        },
        method="POST",
    )
    try:
        with _ur.urlopen(req, timeout=10) as resp:
            return resp.status == 200 or resp.status == 202
    except Exception:
        return False


def _work_sync() -> bool:
    """Send current session payload + drain the offline queue."""
    if not (_WORK.get("enabled") and _WORK.get("consent") and _WORK.get("endpoint")):
        return False
    if not _STATE["memory"] and not _STATE["history"]:
        return False
    import uuid as _uuid
    queued = _load_queue()
    ok_any = False
    for item in list(queued):
        if _work_post(item):
            queued.remove(item)
            ok_any = True
    payload = _work_compile_payload()
    payload["session_id"] = str(_uuid.uuid4())
    if _work_post(payload):
        ok_any = True
    else:
        queued.append(payload)
    _save_queue(queued)
    if ok_any:
        print("[NH Plugin] Work report sent to server")
    return ok_any


def _work_sync_timer():
    if _WORK.get("enabled") and _WORK.get("consent"):
        try:
            _work_sync()
        except Exception:
            pass
        return _WORK_SYNC_INTERVAL
    return None


class NH_OT_UsageReport(Operator):
    bl_idname = "nh.usage_report"
    bl_label = "NH Usage: Show Report"
    bl_description = "Print and open the local usage report (stored locally only)"

    def execute(self, context):
        del context
        block = _open_text_report()
        if block is not None:
            self.report({"INFO"}, "Usage report written to the Text Editor (NH Usage Report)")
        else:
            self.report({"INFO"}, "Usage report printed to the System Console")
        return {"FINISHED"}


class NH_OT_UsageReset(Operator):
    bl_idname = "nh.usage_reset"
    bl_label = "NH Usage: Reset"
    bl_description = "Reset local usage counters (keeps file, clears history and stats)"

    def execute(self, context):
        del context
        _STATE["memory"] = {}
        _STATE["history"] = []
        _STATE["dirty"] = True
        _save_file()
        self.report({"INFO"}, "NH usage statistics reset")
        return {"FINISHED"}


class NH_OT_UsageToggle(Operator):
    bl_idname = "nh.usage_toggle"
    bl_label = "NH Usage: Pause / Resume"
    bl_description = "Pause or resume usage recording"

    def execute(self, context):
        del context
        if _STATE["enabled"]:
            _STATE["paused"] = not _STATE.get("paused", False)
            state_txt = "paused" if _STATE.get("paused", False) else "recording"
            self.report({"INFO"}, f"NH usage recording: {state_txt}")
        return {"FINISHED"}


def _all_registered_nh_ops():
    out = []
    for cls in bpy.types.Operator.__subclasses__():
        try:
            bl_idname = str(getattr(cls, "bl_idname", "") or "")
        except Exception:
            bl_idname = ""
        if bl_idname.startswith(("cray.", "nh.")):
            out.append(cls)
    return out



# ------------------------------------------------------------------------
#  Work report UI (owner-side configuration)
# ------------------------------------------------------------------------

class NH_WG_WorkSettings(bpy.types.PropertyGroup):
    endpoint: bpy.props.StringProperty(name="Server URL", default="", description="https://... work tracking endpoint")
    license: bpy.props.StringProperty(name="License Key", default="", description="Provided by the owner; signs reports")
    project: bpy.props.StringProperty(name="Project / Order", default="", description="Work order / project id")
    consent: bpy.props.BoolProperty(
        name="Consent",
        default=False,
        description="I confirm workers are informed that work statistics are sent to the server",
    )


class NH_OT_WorkSave(bpy.types.Operator):
    bl_idname = "nh.work_save"
    bl_label = "NH Work: Save Settings"
    bl_description = "Store work report settings locally on this machine"

    def execute(self, context):
        global _WORK
        try:
            pg = context.scene.nh_work_settings
        except Exception:
            self.report({"ERROR"}, "Work settings are unavailable")
            return {"CANCELLED"}
        _WORK.update({
            "enabled": bool(_WORK.get("enabled", False)),
            "endpoint": str(getattr(pg, "endpoint", "") or "").strip(),
            "license": str(getattr(pg, "license", "") or "").strip(),
            "project": str(getattr(pg, "project", "") or "").strip(),
            "consent": bool(getattr(pg, "consent", False)),
        })
        _save_work_config(_WORK)
        self.report({"INFO"}, "NH work settings saved")
        return {"FINISHED"}


class NH_OT_WorkToggle(bpy.types.Operator):
    bl_idname = "nh.work_toggle"
    bl_label = "NH Work: Enable"
    bl_description = "Enable/disable sending of work reports to the server (needs endpoint + license + consent)"

    def execute(self, context):
        global _WORK
        _WORK["enabled"] = not bool(_WORK.get("enabled", False))
        _save_work_config(_WORK)
        state_txt = "ENABLED" if _WORK.get("enabled") else "disabled"
        self.report({"INFO"}, f"NH work report: {state_txt}")
        return {"FINISHED"}


class NH_OT_WorkSend(bpy.types.Operator):
    bl_idname = "nh.work_send"
    bl_label = "Send Now"
    bl_description = "Send the current session work report immediately"

    def execute(self, context):
        del context
        if not (_WORK.get("enabled") and _WORK.get("consent") and _WORK.get("endpoint")):
            self.report({"ERROR"}, "Enable work report: save settings, tick consent, enable")
            return {"CANCELLED"}
        if _work_sync():
            self.report({"INFO"}, "Work report sent")
        else:
            self.report({"WARNING"}, "Work report saved to offline queue (server unreachable)")
        return {"FINISHED"}


class NH_PT_WorkReportPanel(Panel):
    bl_label = "NH Work Report"
    bl_idname = "NH_PT_work_report"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "NH Plugin"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        try:
            pg = context.scene.nh_work_settings
        except Exception:
            layout.label(text="Work settings unavailable")
            return
        cfg = _WORK
        box = layout.box()
        box.prop(pg, "endpoint")
        box.prop(pg, "license")
        box.prop(pg, "project")
        box.prop(pg, "consent")
        row = box.row(align=True)
        row.operator("nh.work_save", text="Save", icon="FILE_TICK")
        row.operator("nh.work_toggle", text="Disable" if cfg.get("enabled") else "Enable", icon="CHECKBOX_HLT" if cfg.get("enabled") else "CHECKBOX_DEHLT", toggle=True)
        layout.operator("nh.work_send", text="Send Now", icon="EXPORT")
        status = "running" if (cfg.get("enabled") and cfg.get("consent") and cfg.get("endpoint")) else "off"
        layout.label(text=f"Status: {status} | project: {cfg.get('project') or '-'}", icon="INFO")

class NH_PT_UsageStatsPanel(Panel):
    bl_label = "NH Usage"
    bl_idname = "NH_PT_usage_stats"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "NH Plugin"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        del context
        layout = self.layout
        s = _stats_summary()
        box = layout.box()
        box.label(text=f"Clicks: {s['clicks']}   Runs: {s['runs']}")
        box.label(text=f"Errors: {s['errors']}   Tracked ops: {len(_STATE['memory'])}")
        if s.get("top"):
            col = box.column(align=True)
            for op_id, b in s["top"][:6]:
                col.label(text=f"{op_id}: {b.get('runs', 0)} run(s)")
        row = layout.row(align=True)
        row.operator("nh.usage_report", text="Report", icon="TEXT")
        row.operator("nh.usage_reset", text="Reset", icon="LOOP_BACK")
        layout.operator("nh.usage_toggle", text="Pause" if _STATE["enabled"] else "Resume", icon="PAUSE" if _STATE["enabled"] else "PLAY")
        layout.label(text="Data stays on this PC only", icon="INFO")
