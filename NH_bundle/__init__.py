# ------------------------------------------------------------------------
#  Embedded Arma 3 Object Builder compatibility bundle (GPL v3)
# ------------------------------------------------------------------------
#  This package is a trimmed copy of the "Arma 3 Object Builder" Blender
#  add-on (https://github.com/MrClock8163/Arma3ObjectBuilder), GPLv3.
#  Copyright (C) MrClock, Hans-Joerg "Alwarren" Frieden and contributors.
#  See the LICENSE file in this directory for the full license text.
#
#  This bundle is used by the NH Blender plugin as a fallback ONLY when the
#  original "Arma 3 Object Builder" extension is not installed. It contains
#  the modules needed for P3D import/export, PAA texture decoding and the
#  A3OB object/material/scene property groups referenced by NH.
#
#  Upstream __init__.py, UI tool panels and preferences were intentionally
#  replaced by a minimal bootstrap because the bundle is not a standalone
#  add-on; only the codec/API parts are embedded.


import os

import bpy


addon_prefs = None
addon_dir = os.path.abspath(os.path.dirname(os.path.realpath(__file__)))
addon_icons = {}


class _NHFallbackPreferences(object):
    """Lightweight stand-in for the upstream A3OB add-on preferences.

    Provides sane defaults for the fields used by the embedded modules
    (import/export paths and vertex/face flag defaults).
    """
    icon_theme = "none"
    show_info_links = False
    project_root = ""
    custom_data = ""
    a3_tools = ""
    flag_vertex = 0x02000000
    flag_face = 0
    preserve_preprocessed_lods = False
    create_backups = False
    preserve_faulty_output = False


addon_prefs = _NHFallbackPreferences()


def get_icon(name):
    try:
        return addon_icons[addon_prefs.icon_theme.lower()][name].icon_id
    except Exception:
        return 0


def get_prefs():
    return addon_prefs


def register():
    pass


def unregister():
    pass
