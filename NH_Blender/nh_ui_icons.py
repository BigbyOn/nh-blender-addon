# ------------------------------------------------------------------------
#  NH UI icons: custom "NH" monogram used on plugin panels and buttons.
# ------------------------------------------------------------------------
#  The icon is embedded as base64 PNG (generated from the NH logo concept),
#  extracted once to the user CONFIG folder and registered via bpy.utils.previews.
# ------------------------------------------------------------------------

import base64
import os

import bpy

_ICON_FILENAME = "nh_icon_nh.png"


_ICON_B64 = (
    "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"
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


_installed_panel_headers = {}


def _make_panel_icon_header(original):
    def _nh_panel_icon_header(self, context):
        try:
            idv = icon_value()
            if idv:
                self.layout.label(text="", icon_value=idv)
        except Exception:
            pass
        if callable(original):
            try:
                original(self, context)
            except Exception:
                pass

    _nh_panel_icon_header._nh_panel_icon_wrapper = True
    return _nh_panel_icon_header


def install_panel_header_icon(cls) -> bool:
    """Draw the NH icon in a panel header (same approach as the X-Ray addon)."""
    if not (isinstance(cls, type) and issubclass(cls, bpy.types.Panel)):
        return False
    try:
        if getattr(cls.__dict__.get("draw_header"), "_nh_panel_icon_wrapper", False):
            return False
    except Exception:
        pass

    original = getattr(cls, "draw_header", None)
    try:
        cls.draw_header = _make_panel_icon_header(original)
    except Exception:
        return False
    _installed_panel_headers[cls] = original
    return True


def restore_panel_header_icons():
    """Remove NH icon headers and restore the original draw_header methods."""
    while _installed_panel_headers:
        cls, original = _installed_panel_headers.popitem()
        try:
            if original is None:
                if "draw_header" in cls.__dict__:
                    delattr(cls, "draw_header")
            else:
                cls.draw_header = original
        except Exception:
            pass


def apply_to_panels(classes):
    """Attach the NH icon to panel headers in the NH Plugin N-panel."""
    ensure_previews()
    patched = 0
    for cls in classes or ():
        if install_panel_header_icon(cls):
            patched += 1
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
