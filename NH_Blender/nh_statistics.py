# ------------------------------------------------------------------------
#  NH usage statistics (opt-in local telemetry, DISABLED by default)
# ------------------------------------------------------------------------
#  Tracks how often operators are invoked from the UI ("clicks") and how
#  many of them complete successfully. Data is stored ONLY on the local
#  machine (CONFIG/nh_usage_stats.json). Nothing is ever sent anywhere.
#
#  NOTE: the server-side work tracking subsystem has been cut out for now
#  and lives in _staged/nh_work_tracking/ (nh_statistics_full.py +
#  nh_work_server.py). Return to it when needed.
# ------------------------------------------------------------------------

import bpy
import os
import time
import json
import collections
from datetime import datetime

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
    "paused": False,
}


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
        return {"version": _STATS_VERSION, "total": {}, "history": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"version": _STATS_VERSION, "total": {}, "history": []}
    if not isinstance(data, dict):
        return {"version": _STATS_VERSION, "total": {}, "history": []}
    data.setdefault("version", _STATS_VERSION)
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


def _record(context, op_id, stage, start, result):
    """stage: 'invoke' | 'execute'. result: 0 ok / 1 error / 2 cancelled."""
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
            if isinstance(result, (set, tuple, list)) and result:
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

    wrapped_invoke.__name__ = getattr(original_invoke, "__name__", "wrapped_invoke")
    return wrapped_invoke if original_has_invoke else None


def wrap(classes):
    """Wrap Operator methods BEFORE bpy registration (bpy.ops calls the bound
    methods captured at register time, so wrapping must happen beforehand)."""
    global _STATE
    if not _ENABLED:
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
    global _STATE
    if not _ENABLED:
        return
    if _STATE["enabled"]:
        return
    _STATE["enabled"] = True
    _STATE["paused"] = False
    _STATE["session_start"] = float(_STATE.get("session_start") or time.time())
    loaded = _load_file()
    _STATE["memory"] = dict(loaded.get("total", {}))
    _STATE["history"] = list(loaded.get("history", []))[-_EVENTS_LIMIT:]
    _STATE["dirty"] = False
    for cls in (NH_OT_UsageReport, NH_OT_UsageReset, NH_OT_UsageToggle, NH_PT_UsageStatsPanel):
        try:
            bpy.utils.register_class(cls)
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
    for cls in reversed((NH_PT_UsageStatsPanel, NH_OT_UsageToggle, NH_OT_UsageReset, NH_OT_UsageReport)):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


def _flush_timer():
    if _STATE["dirty"]:
        _save_file()
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
    except Exception:
        print(text)
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
