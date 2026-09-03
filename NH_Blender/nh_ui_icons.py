# ------------------------------------------------------------------------
#  NH UI icons: custom "NH" monogram used on plugin panels and buttons.
# ------------------------------------------------------------------------
#  The icon is embedded as base64 PNG (generated from the NH logo concept),
#  extracted once to the user CONFIG folder and registered via bpy.utils.previews.
# ------------------------------------------------------------------------

import base64
import os

import bpy

_ICON_FILENAME = "nh_icon.png"


_ICON_B64 = (
    "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"
    "RU5ErkJggg=="
)


_previews = None


def _config_dir() -> str:
    base = ""
    try:
        base = bpy.utils.user_resource("CONFIG") or ""
    except Exception:
        base = ""
    if not base:
        base = bpy.app.tempdir or os.path.expanduser("~")
    return base


def _icon_path() -> str:
    return os.path.join(_config_dir(), _ICON_FILENAME)


def _ensure_png() -> str:
    path = _icon_path()
    try:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        raw = base64.b64decode("".join(_ICON_B64))
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "wb") as f:
            f.write(raw)
    except Exception:
        return ""
    return path


def _previews_module():
    try:
        import importlib
        return importlib.import_module("bpy.utils.previews")
    except Exception:
        return None


def ensure_previews() -> bool:
    """Lazy-load the NH icon previews; returns True when ready."""
    global _previews
    try:
        if _previews is not None:
            return True
        path = _ensure_png()
        if not path or not os.path.isfile(path):
            return False
        pm = _previews_module()
        if pm is None:
            return False
        pcoll = pm.new()
        pcoll.load("nh", path, "IMAGE")
        _previews = pcoll
        return True
    except Exception:
        return False


def icon_value() -> int:
    """Icon id for layout.operator(icon_value=...) or 0 if unavailable."""
    try:
        if ensure_previews() and _previews is not None:
            return int(_previews["nh"].icon_id)
    except Exception:
        pass
    return 0


def apply_to_panels(classes):
    """Attach the NH icon to panel headers (Panel.bl_icon_value)."""
    idv = icon_value()
    if not idv:
        return 0
    patched = 0
    for cls in classes or ():
        if not (isinstance(cls, type) and issubclass(cls, bpy.types.Panel)):
            continue
        try:
            cls.bl_icon_value = idv
            patched += 1
        except Exception:
            pass
    if patched:
        print(f"[NH Plugin] NH icon applied to {patched} panel(s)")
    return patched


def dispose():
    global _previews
    try:
        pm = _previews_module()
        if _previews is not None and pm is not None:
            pm.remove(_previews)
    except Exception:
        pass
    _previews = None
