import bpy
import bmesh
import math
import os
import re
import shutil
import subprocess
import importlib
import importlib.util
import json
import sys
import random
import uuid
import hashlib
import tempfile
from mathutils import Vector, Matrix
from bpy.props import PointerProperty, StringProperty, FloatProperty, IntProperty, BoolProperty, EnumProperty, CollectionProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList, OperatorFileListElement, Menu
from bpy.app.handlers import persistent
from contextlib import contextmanager

# nh_base.py
# auto-split slice; cross-module refs resolved with in-function imports

bl_info = {
    "name": "NH Plugin for Blender",
    "author": "Enisam",
    "version": (0, 5, 4, 11),
    "blender": (5, 1, 1),
    "location": "3D Viewport > N-panel > NH Plugin",
    "description": "All-in-one Blender toolkit for porting and preparing DayZ/Arma assets: fixes, textures, colliders, proxies, snap points, and P3D workflow helpers.",    
    "doc_url": "https://github.com/BigbyOn/nh-blender-addon",
    "tracker_url": "https://github.com/BigbyOn/nh-blender-addon/issues",
    "mclink": "https://github.com/BigbyOn/nh-blender-addon",
    "category": "Object",
}

import bpy
import bmesh
from bpy.app.handlers import persistent
from bpy.types import Operator, Panel, PropertyGroup, UIList, OperatorFileListElement, Menu
from bpy.props import PointerProperty, StringProperty, FloatProperty, IntProperty, BoolProperty, EnumProperty, CollectionProperty
from mathutils import Vector, Matrix
import math
import random
import os
import re
import shutil
import subprocess
import importlib
import importlib.util
import json
import sys
from contextlib import contextmanager
import uuid
import hashlib
import tempfile

# ------------------------------------------------------------------------
#  Global config storage
# ------------------------------------------------------------------------

CONFIG_SURFACES = {}
CONFIG_CLUTTER = {}
_PROXY_MESH_NAME = "DayZ_ClutterProxyMesh"
_SCATTER_PROXY_TAG_PROP = "cray_scatter_proxy"
_ASSET_CATALOG_NAME = "Asset"
_ASSET_CATALOG_FALLBACK_ID = "7d6f3b1d-4d5f-4b1e-9f77-5d1e8dd5c001"
_TEXTURE_PREVIEW_CACHE_SCHEMA_VERSION = 2
_ADDON_KEYMAP_ITEMS = []
_PLAIN_AXIS_HELPER_PROP = "cray_plain_axis_helper"
_PLAIN_AXIS_ROOT_PROP = "cray_plain_axis_root"
_PLAIN_AXIS_SOURCE_OBJECT_PROP = "cray_plain_axis_source_object"
_PLAIN_AXIS_CONSTRAINT_NAME = "NH Plain Axis"
_PLAIN_AXIS_CONSTRAINT_AXES = (
    "use_location_x",
    "use_location_y",
    "use_location_z",
    "use_rotation_x",
    "use_rotation_y",
    "use_rotation_z",
    "use_scale_x",
    "use_scale_y",
    "use_scale_z",
)
_PLAIN_AXIS_HOTKEY_REGISTERED = False
_MESH_KEYMAP_NAME = "Mesh"
_LINKED_PICK_CONFLICT_KEYMAPS = {
    "Mesh",
    "3D View",
    "3D View Generic",
}
_PERSISTED_UI_STATE_FILENAME = "nh_blender_ui_state.json"
_PERSISTED_UI_STATE_VERSION = 4
_PERSISTED_UI_STATE_TIMER_INTERVAL = 1.0
_PERSISTED_UI_STATE_CACHE = None
_PERSISTED_UI_FORCE_RESTORE_SETTINGS = {"cray_texreplace_settings"}
_PERSISTED_UI_SKIP_EMPTY_DEFAULT_PROPS = {
    "cray_texreplace_settings": {
        "source_textures_folder",
        "target_textures_folder",
        "image_to_paa_path",
    },
}
_PERSISTED_UI_LEGACY_DEFAULTS_TO_IGNORE = {
    "cray_texreplace_settings": {
        "convert_png_to_paa": {False},
        "dds_backend": {"AUTO", "BUNDLED_EXE", "BUNDLED_NODE", "BLENDER", "EXTERNAL"},
        "folder": {r"P:\NH_ObjectTextures", "P:\\NH_ObjectTextures\\"},
        "target_textures_folder": {r"P:\NH_ObjectTextures", "P:\\NH_ObjectTextures\\"},
        "texture_cache_source_folder": {r"P:\NH_ObjectTextures", "P:\\NH_ObjectTextures\\"},
    },
}
_PERSISTED_UI_DYNAMIC_ENUM_DEFAULTS = {
    ("cray_settings", "selected_surface"): "NONE",
}
_TRASH_TINY_ISLAND_MAX_VERTS = 5
_TRASH_TINY_ISLAND_MAX_FACES = 5
_TRASH_TINY_ISLAND_MAX_EDGES = 8
_PERSISTED_UI_SETTINGS = {
    "cray_settings": (
        "vertex_group",
        "config_path",
        "selected_surface",
        "grid_size",
        "density_scale",
        "slope_falloff",
        "max_height_offset",
        "max_distance",
        "random_jitter",
        "spawn_probability",
        "max_proxies",
        "seed",
        "only_hit_source",
    ),
    "cray_snap_settings": (
        "snap_group",
        "snap_p3d_name",
        "snap_pair_code",
        "snap_side",
        "show_auto_edge_fallback",
        "edge_axis",
        "edge_side",
        "edge_span_axis",
        "edge_tolerance",
        "replace_existing",
        "batch_cleanup_imported",
        "batch_overwrite_bak",
    ),
    "cray_model_split_settings": (
        "part_number",
        "named_model_name",
        "named_export_mode",
        "named_export_directory",
        "grid_cell_size_x",
        "grid_cell_size_y",
        "grid_cell_size_z",
        "grid_count_x",
        "grid_count_y",
        "grid_count_z",
        "grid_origin_mode",
        "grid_manual_origin_x",
        "grid_manual_origin_y",
        "grid_manual_origin_z",
        "grid_output_prefix",
        "grid_use_visible_cutters_only",
        "grid_keep_original",
        "grid_hide_cutters_after_split",
        "grid_skip_empty_pieces",
        "grid_min_vertices",
        "grid_min_faces",
        "grid_add_result_to_export_planner",
    ),
    "cray_collider_settings": (
        "target_lod",
        "box_thickness",
        "bounds_padding",
        "merge_distance",
        "recalc_normals",
        "show_hotkey_button_fallbacks",
        "show_advanced_build_buttons",
        "show_fire_geometry_tools",
        "show_roadway_tools",
        "roadway_weld_distance",
    ),
    "cray_collider_exp_settings": (
        "enabled",
        "target_lod",
        "exp_mode",
        "scale_x",
        "scale_y",
        "scale_z",
        "scale_multiplier",
        "offset_x",
        "offset_y",
        "offset_z",
        "floor_contact",
        "minimum_size",
        "normal_minimum_size",
        "convex_detail",
        "convex_max_triangles",
        "cylinder_segments",
        "pipe_segments",
        "pipe_inner_radius",
        "pipe_outer_radius",
        "pipe_depth",
        "pipe_thickness",
        "sphere_segments",
        "capsule_radius",
        "capsule_height",
        "capsule_cap_size",
        "capsule_follow_source_angle",
        "capsule_vertical_align",
        "recalc_normals",
        "merge_distance",
    ),
    "cray_texreplace_settings": (
        "folder",
        "write_expected_missing_paths",
        "source_textures_folder",
        "target_textures_folder",
        "texture_cache_source_folder",
        "texture_cache_workers",
        "texture_tools_folder",
        "convert_dds_to_png",
        "dds_backend",
        "node_exe_path",
        "external_dds_converter_path",
        "convert_png_to_paa",
        "image_to_paa_path",
        "generate_rvmat",
        "export_only_missing",
        "export_overwrite_existing",
        "delete_png_after_paa",
        "fix_mesh_join_batch",
        "fix_mesh_center_to_origin",
        "material_safe_merge_distance",
        "show_component_fix_tools",
        "fix_list_path",
        "export_warn_loose_vertices",
        "split_planar_ngon_vertex_count",
        "split_planar_ngon_angle_tolerance",
        "split_planar_ngon_plane_tolerance",
    ),
    "cray_ie_settings": (
        "quick_add_p3d_name",
        "quick_add_search_root",
        "import_show_materials",
        "import_keep_converted_textures",
        "disable_collections_after_import",
        "disable_mode",
        "export_mode",
        "export_directory",
        "export_create_bak",
        "export_only_p3d_named",
        "export_only_split_parts",
        "export_force_all_lods",
    ),
    "cray_asset_proxy_settings": (
        "duplicate_to_all_resolution_lods",
        "proxy_duplicate_resolution",
        "proxy_duplicate_geometries",
        "proxy_duplicate_roadway",
        "proxy_duplicate_point_clouds",
    ),
    "cray_asset_library_settings": (
        "folder",
        "common_root",
        "environment_root",
        "custom_search_root",
        "custom_p3d_name",
        "import_first_lod_only",
        "clear_previous_temp_library",
        "rebuild_existing_libraries",
        "render_textured_previews",
    ),
    "cray_ui_panel_settings": (
        "order_collider",
        "order_geometry_lods",
        "order_asset_library",
        "order_snap_points",
        "order_import_export",
        "order_fixes",
        "order_model_split",
        "order_texture_replace",
        "order_cache_manager",
        "order_object_builder",
        "show_snap_points",
        "show_asset_library",
        "show_fixes",
        "show_import_export",
        "show_model_split",
        "show_texture_replace",
        "show_collider",
        "show_geometry_lods",
        "show_object_builder",
        "show_cache_manager",
        "show_custom_keybinds",
    ),
}


def _persisted_ui_state_path() -> str:
    base_dir = ""
    try:
        base_dir = bpy.utils.user_resource("CONFIG") or ""
    except Exception:
        base_dir = ""
    if not base_dir:
        base_dir = bpy.app.tempdir or os.path.expanduser("~")
    return os.path.join(base_dir, _PERSISTED_UI_STATE_FILENAME)


def _read_persisted_ui_state():
    path = _persisted_ui_state_path()
    if not path or not os.path.isfile(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("=== NH Plugin: failed to read persisted UI state ===")
        print(f"{path} -> {_fmt_exc(e)}")
        return {}

    return data if isinstance(data, dict) else {}


def _write_persisted_ui_state(data):
    global _PERSISTED_UI_STATE_CACHE

    path = _persisted_ui_state_path()
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)

    _PERSISTED_UI_STATE_CACHE = data


def _collect_persisted_ui_state(scene):
    state = {"__version": _PERSISTED_UI_STATE_VERSION}
    if scene is None:
        return state

    for settings_name, prop_names in _PERSISTED_UI_SETTINGS.items():
        settings = getattr(scene, settings_name, None)
        if settings is None:
            continue

        block = {}
        for prop_name in prop_names:
            try:
                value = getattr(settings, prop_name)
            except Exception:
                continue

            if isinstance(value, bool):
                block[prop_name] = bool(value)
            elif isinstance(value, int):
                block[prop_name] = int(value)
            elif isinstance(value, float):
                block[prop_name] = float(value)
            elif isinstance(value, str):
                block[prop_name] = value

        if block:
            state[settings_name] = block

    return state


def _property_has_default_value(settings, prop_name: str, settings_name: str = "") -> bool:
    if settings is None or not prop_name:
        return True

    try:
        prop = settings.bl_rna.properties.get(prop_name)
    except Exception:
        prop = None
    if prop is None:
        return False

    try:
        current = getattr(settings, prop_name)
    except Exception:
        return False

    dynamic_enum_default = _PERSISTED_UI_DYNAMIC_ENUM_DEFAULTS.get((settings_name, prop_name))
    if dynamic_enum_default is not None:
        return current == dynamic_enum_default

    try:
        default = prop.default
    except Exception:
        return False

    if isinstance(current, float) or isinstance(default, float):
        try:
            return abs(float(current) - float(default)) <= 1e-9
        except Exception:
            return False

    return current == default


def _apply_persisted_ui_state_to_scene(scene, only_if_default: bool = True):
    raw = _read_persisted_ui_state()
    if scene is None or not raw:
        return 0

    try:
        state_version = int(raw.get("__version", 0) or 0)
    except Exception:
        state_version = 0

    applied = 0
    for settings_name, prop_names in _PERSISTED_UI_SETTINGS.items():
        settings = getattr(scene, settings_name, None)
        saved_block = raw.get(settings_name)
        if settings is None or not isinstance(saved_block, dict):
            continue

        force_restore = settings_name in _PERSISTED_UI_FORCE_RESTORE_SETTINGS
        for prop_name in prop_names:
            if prop_name not in saved_block:
                continue
            if only_if_default and not force_restore and not _property_has_default_value(settings, prop_name, settings_name):
                continue
            saved_value = saved_block[prop_name]
            if state_version < _PERSISTED_UI_STATE_VERSION:
                ignored_legacy_values = _PERSISTED_UI_LEGACY_DEFAULTS_TO_IGNORE.get(settings_name, {}).get(prop_name)
                if ignored_legacy_values is not None and saved_value in ignored_legacy_values:
                    continue
            if (
                isinstance(saved_value, str)
                and not saved_value
                and prop_name in _PERSISTED_UI_SKIP_EMPTY_DEFAULT_PROPS.get(settings_name, set())
            ):
                try:
                    prop = settings.bl_rna.properties.get(prop_name)
                    if isinstance(prop.default, str) and prop.default:
                        continue
                except Exception:
                    pass

            try:
                prop = settings.bl_rna.properties.get(prop_name)
            except Exception:
                prop = None
            if prop is not None:
                try:
                    if getattr(prop, "type", "") == "ENUM" and isinstance(saved_value, str):
                        valid_values = {item.identifier for item in prop.enum_items}
                        if valid_values and saved_value not in valid_values:
                            continue
                except Exception:
                    pass

            try:
                setattr(settings, prop_name, saved_value)
                applied += 1
            except Exception:
                pass

    return applied


def _iter_safe_scenes():
    scenes = getattr(bpy.data, "scenes", None)
    if scenes is None:
        return []
    try:
        return list(scenes)
    except Exception:
        return []


def _apply_persisted_ui_state_to_all_scenes(only_if_default: bool = True):
    applied = 0
    for scene in _iter_safe_scenes():
        applied += _apply_persisted_ui_state_to_scene(scene, only_if_default=only_if_default)
    return applied


def _save_current_persisted_ui_state(scene=None):
    global _PERSISTED_UI_STATE_CACHE

    if scene is None:
        scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return False

    data = _collect_persisted_ui_state(scene)
    if data == (_PERSISTED_UI_STATE_CACHE or {}):
        return False

    try:
        _write_persisted_ui_state(data)
    except Exception as e:
        print("=== NH Plugin: failed to write persisted UI state ===")
        print(f"{_persisted_ui_state_path()} -> {_fmt_exc(e)}")
        return False

    return True

def _save_texreplace_settings_now(context):
    scene = getattr(context, "scene", None) if context is not None else None
    return _save_current_persisted_ui_state(scene)


@persistent
def _restore_persisted_ui_state_on_load(_dummy):
    from .nh_scatter import (_apply_ui_panel_class_order, _ui_panel_settings_from_context)
    global _PERSISTED_UI_STATE_CACHE
    _apply_persisted_ui_state_to_all_scenes(only_if_default=True)
    _apply_ui_panel_class_order(_ui_panel_settings_from_context(bpy.context))
    _PERSISTED_UI_STATE_CACHE = _collect_persisted_ui_state(getattr(bpy.context, "scene", None))
    try:
        bpy.app.timers.register(_deferred_restore_persisted_ui_state, first_interval=0.2)
    except Exception:
        pass


def _deferred_restore_persisted_ui_state():
    from .nh_scatter import (_apply_ui_panel_class_order, _tag_ui_redraw, _ui_panel_settings_from_context)
    global _PERSISTED_UI_STATE_CACHE
    _apply_persisted_ui_state_to_all_scenes(only_if_default=True)
    _apply_ui_panel_class_order(_ui_panel_settings_from_context(bpy.context))
    _PERSISTED_UI_STATE_CACHE = _collect_persisted_ui_state(getattr(bpy.context, "scene", None))
    _tag_ui_redraw(bpy.context)
    return None


def _persisted_ui_state_timer():
    _save_current_persisted_ui_state(getattr(bpy.context, "scene", None))
    return _PERSISTED_UI_STATE_TIMER_INTERVAL


def _fmt_exc(e: Exception) -> str:
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def _iter_unique_keyconfigs(window_manager):
    keyconfigs = getattr(window_manager, "keyconfigs", None)
    if keyconfigs is None:
        return

    seen = set()
    for attr in ("active", "user", "addon", "default"):
        keyconfig = getattr(keyconfigs, attr, None)
        if keyconfig is None:
            continue
        marker = id(keyconfig)
        if marker in seen:
            continue
        seen.add(marker)
        yield keyconfig


def _keymap_item_matches_event(
    kmi,
    *,
    event_type,
    value="PRESS",
    shift=False,
    ctrl=False,
    alt=False,
    oskey=False,
):
    if not getattr(kmi, "active", True):
        return False
    if getattr(kmi, "type", None) != event_type:
        return False
    if getattr(kmi, "value", None) != value:
        return False
    if getattr(kmi, "any", False):
        return True
    if bool(getattr(kmi, "shift", False)) != bool(shift):
        return False
    if bool(getattr(kmi, "ctrl", False)) != bool(ctrl):
        return False
    if bool(getattr(kmi, "alt", False)) != bool(alt):
        return False
    if bool(getattr(kmi, "oskey", False)) != bool(oskey):
        return False

    key_modifier = getattr(kmi, "key_modifier", "NONE")
    if key_modifier not in {"NONE", "", None}:
        return False

    return True


def _mesh_shortcut_is_free(
    window_manager,
    *,
    event_type,
    value="PRESS",
    shift=False,
    ctrl=False,
    alt=False,
    oskey=False,
):
    for keyconfig in _iter_unique_keyconfigs(window_manager):
        for keymap in keyconfig.keymaps:
            if keymap.name not in _LINKED_PICK_CONFLICT_KEYMAPS:
                continue
            for kmi in keymap.keymap_items:
                if _keymap_item_matches_event(
                    kmi,
                    event_type=event_type,
                    value=value,
                    shift=shift,
                    ctrl=ctrl,
                    alt=alt,
                    oskey=oskey,
                ):
                    return False
    return True


def _register_addon_keymap_item(keymap, operator_idname, *, event_type, value="PRESS", properties=None, **mods):
    for existing in list(keymap.keymap_items):
        if getattr(existing, "idname", "") != operator_idname:
            continue
        if getattr(existing, "type", None) != event_type or getattr(existing, "value", None) != value:
            continue
        if any(bool(getattr(existing, key, False)) != bool(mods.get(key, False)) for key in ("shift", "ctrl", "alt", "oskey")):
            continue
        try:
            keymap.keymap_items.remove(existing)
        except Exception:
            pass

    kmi = keymap.keymap_items.new(operator_idname, type=event_type, value=value, **mods)
    if properties:
        for prop_name, prop_value in properties.items():
            setattr(kmi.properties, prop_name, prop_value)
    _ADDON_KEYMAP_ITEMS.append((keymap, kmi))
    return kmi


def _remove_addon_keymap_items_for_operators(keymap, operator_ids):
    operator_ids = set(operator_ids or ())
    if not operator_ids:
        return
    for existing in list(keymap.keymap_items):
        if getattr(existing, "idname", "") not in operator_ids:
            continue
        try:
            keymap.keymap_items.remove(existing)
        except Exception:
            pass


def _nh_keymap_operator_ids():
    from .nh_scatter import (_CUSTOM_KEYBIND_DEFINITIONS)
    return {
        operator_idname
        for operator_idname, _action, _default_shortcut, _status_key in _CUSTOM_KEYBIND_DEFINITIONS
    } | {"cray.build_collider"}


def _register_collider_keymaps():
    global _PLAIN_AXIS_HOTKEY_REGISTERED
    _unregister_collider_keymaps()
    _PLAIN_AXIS_HOTKEY_REGISTERED = False

    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return

    addon_keyconfig = getattr(window_manager.keyconfigs, "addon", None)
    if addon_keyconfig is None:
        return

    keymap = addon_keyconfig.keymaps.get(_MESH_KEYMAP_NAME)
    if keymap is None:
        keymap = addon_keyconfig.keymaps.new(name=_MESH_KEYMAP_NAME, space_type="EMPTY", region_type="WINDOW")

    _remove_addon_keymap_items_for_operators(keymap, _nh_keymap_operator_ids())

    _register_addon_keymap_item(
        keymap,
        "cray.copy_selected_verts_to_geometry",
        event_type="C",
        value="PRESS",
        ctrl=True,
        shift=True,
    )
    _register_addon_keymap_item(
        keymap,
        "cray.select_isolated_vertices",
        event_type="X",
        value="PRESS",
        ctrl=True,
        shift=True,
    )
    _register_addon_keymap_item(
        keymap,
        "cray.generate_convex_hull_collider_exp",
        event_type="BUTTON4MOUSE",
        value="PRESS",
    )
    _register_addon_keymap_item(
        keymap,
        "cray.generate_box_collider_exp",
        event_type="BUTTON5MOUSE",
        value="PRESS",
    )
    _register_addon_keymap_item(
        keymap,
        "cray.delete_last_collider_exp",
        event_type="BUTTON4MOUSE",
        value="PRESS",
        ctrl=True,
    )
    _register_addon_keymap_item(
        keymap,
        "cray.select_connected_shell_from_selection_exp",
        event_type="BUTTON5MOUSE",
        value="PRESS",
        ctrl=True,
    )

    if _mesh_shortcut_is_free(window_manager, event_type="P", value="PRESS", ctrl=True, shift=True):
        _register_addon_keymap_item(
            keymap,
            "cray.create_plain_axis_pivot",
            event_type="P",
            value="PRESS",
            ctrl=True,
            shift=True,
        )
        _PLAIN_AXIS_HOTKEY_REGISTERED = True
    else:
        print("[NH Plugin] Ctrl+Shift+P is already in use, skipping Plain Axis Pivot shortcut.")


def _unregister_collider_keymaps():
    global _PLAIN_AXIS_HOTKEY_REGISTERED
    while _ADDON_KEYMAP_ITEMS:
        keymap, kmi = _ADDON_KEYMAP_ITEMS.pop()
        try:
            keymap.keymap_items.remove(kmi)
        except Exception:
            pass
    _PLAIN_AXIS_HOTKEY_REGISTERED = False


def _find_nh_keymap_item(operator_idname):
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None or not operator_idname:
        return None

    fallback = None
    for keyconfig in _iter_unique_keyconfigs(window_manager):
        keymap = keyconfig.keymaps.get(_MESH_KEYMAP_NAME)
        if keymap is None:
            continue
        for kmi in keymap.keymap_items:
            if getattr(kmi, "idname", "") != operator_idname:
                continue
            if getattr(kmi, "active", True):
                return kmi
            if fallback is None:
                fallback = kmi
    return fallback


def _remove_nh_keymap_user_overrides():
    window_manager = getattr(bpy.context, "window_manager", None)
    keyconfigs = getattr(window_manager, "keyconfigs", None) if window_manager is not None else None
    user_keyconfig = getattr(keyconfigs, "user", None) if keyconfigs is not None else None
    if user_keyconfig is None:
        return 0

    removed = 0
    operator_ids = _nh_keymap_operator_ids()
    for keymap_name in (_MESH_KEYMAP_NAME,):
        keymap = user_keyconfig.keymaps.get(keymap_name)
        if keymap is None:
            continue
        for kmi in list(keymap.keymap_items):
            if getattr(kmi, "idname", "") not in operator_ids:
                continue
            try:
                keymap.keymap_items.remove(kmi)
                removed += 1
            except Exception:
                pass
    return removed


def _keymap_event_type_label(event_type):
    labels = {
        "BUTTON4MOUSE": "Mouse4",
        "BUTTON5MOUSE": "Mouse5",
        "LEFTMOUSE": "LMB",
        "MIDDLEMOUSE": "MMB",
        "RIGHTMOUSE": "RMB",
        "WHEELUPMOUSE": "Wheel Up",
        "WHEELDOWNMOUSE": "Wheel Down",
        "RET": "Enter",
        "ESC": "Esc",
        "SPACE": "Space",
    }
    return labels.get(str(event_type), str(event_type).replace("_", " ").title())


def _keymap_item_shortcut_label(kmi, default_shortcut=""):
    if kmi is None:
        return default_shortcut or "Unassigned"

    try:
        text = kmi.to_string(compact=True)
        if text:
            return text
    except Exception:
        pass

    parts = []
    if bool(getattr(kmi, "any", False)):
        parts.append("Any")
    else:
        if bool(getattr(kmi, "ctrl", False)):
            parts.append("Ctrl")
        if bool(getattr(kmi, "shift", False)):
            parts.append("Shift")
        if bool(getattr(kmi, "alt", False)):
            parts.append("Alt")
        if bool(getattr(kmi, "oskey", False)):
            parts.append("Cmd")

    key_modifier = getattr(kmi, "key_modifier", "NONE")
    if key_modifier not in {"NONE", "", None}:
        parts.append(_keymap_event_type_label(key_modifier))

    parts.append(_keymap_event_type_label(getattr(kmi, "type", "")))
    return "+".join(part for part in parts if part) or default_shortcut or "Unassigned"

# ------------------------------------------------------------------------
# (config block moved to utilities/dayz_config.py)

def _resolve_scatter_edit_mesh_object(context):
    obj = getattr(context, "edit_object", None)
    if obj is None:
        obj = context.view_layer.objects.active
    if obj is None or obj.type != "MESH":
        raise RuntimeError("Active object must be a mesh")
    if obj.mode != "EDIT":
        raise RuntimeError("Enter Edit Mode and select polygons on the mesh")
    return obj

def _collect_selected_face_triangles_world(obj):
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        return []

    bm = bmesh.from_edit_mesh(obj.data)
    world = obj.matrix_world
    try:
        normal_matrix = world.to_3x3().inverted_safe().transposed()
    except Exception:
        normal_matrix = Matrix.Identity(3)
    triangles = []

    for face in bm.faces:
        if not face.select or len(face.verts) < 3:
            continue

        verts_world = [world @ vert.co for vert in face.verts]
        try:
            face_normal = (normal_matrix @ face.normal).normalized()
        except Exception:
            face_normal = Vector((0.0, 0.0, 1.0))

        v0 = verts_world[0]
        for idx in range(1, len(verts_world) - 1):
            v1 = verts_world[idx]
            v2 = verts_world[idx + 1]
            area = ((v1 - v0).cross(v2 - v0)).length * 0.5
            if area <= 1e-10:
                continue
            triangles.append((v0.copy(), v1.copy(), v2.copy(), face_normal.copy(), area))

    return triangles

def _sample_point_on_triangle(v0: Vector, v1: Vector, v2: Vector, rng) -> Vector:
    r1 = math.sqrt(rng.random())
    r2 = rng.random()
    return ((1.0 - r1) * v0) + (r1 * (1.0 - r2) * v1) + (r1 * r2 * v2)

def _scatter_slope_density_factor(normal: Vector, falloff: float) -> float:
    try:
        up_factor = max(0.0, min(1.0, normal.normalized().z))
    except Exception:
        up_factor = 0.0

    if falloff <= 0.0:
        return 1.0
    return up_factor ** float(falloff)

def _sanitize_snap_p3d_name_value(value: str) -> str:
    name = (value or "").strip()
    if name.lower().endswith(".p3d"):
        name = name[:-4]
    return re.sub(r"[^A-Za-z0-9]+", "", name)

def _on_snap_p3d_name_changed(self, context):
    del context
    current = getattr(self, "snap_p3d_name", "")
    sanitized = _sanitize_snap_p3d_name_value(current)
    if sanitized != current:
        self.snap_p3d_name = sanitized


# ------------------------------------------------------------------------
#  Proxy mesh & P3D properties
# ------------------------------------------------------------------------


# ---- promoted shared constants ----

_COLLIDER_TARGET_LOD_ITEMS = (
    ("6", "Geometry", "Object collision geometry and occluders"),
    ("14", "View Geometry", "View occlusion for AI"),
    ("15", "Fire Geometry", "Hitbox geometry"),
)
_UI_PANEL_LAYOUT_ORDER_STEP = 10
_UI_PANEL_LAYOUT_DEFINITIONS = (
    ("collider", "Collider", "CRAY_PT_ColliderExpPanel"),
    ("geometry_lods", "Geometry LODs", "CRAY_PT_ColliderPanel"),
    ("asset_library", "P3D Asset Library", "CRAY_PT_AssetProxyPanel"),
    ("snap_points", "Snap Points (Memory LOD)", "CRAY_PT_SnapPointsPanel"),
    ("import_export", "Import/Export planner", "CRAY_PT_ImportExportPlannerPanel"),
    ("fixes", "Fixes", "CRAY_PT_FixesPanel"),
    ("model_split", "Model Split / Merge", "CRAY_PT_ModelSplitPanel"),
    ("texture_replace", "Texture Replace", "CRAY_PT_TextureReplacePanel"),
    ("cache_manager", "Cache Manager", "CRAY_PT_CacheManagerPanel"),
    ("object_builder", "Clutter Proxies (DayZ)", "CRAY_PT_ClutterProxiesPanel"),
)
_UI_PANEL_DEFAULT_ORDER = {
    key: (idx + 1) * _UI_PANEL_LAYOUT_ORDER_STEP
    for idx, (key, _label, _class_name) in enumerate(_UI_PANEL_LAYOUT_DEFINITIONS)
}
_TEX_EXPORT_DDS_BACKEND_ITEMS = (
    ("BUILTIN_PYTHON", "Built-in Python", "Use dependency-free Python DDS converter"),
)
_TEX_EXPORT_DEFAULT_SOURCE_ROOTS = (
    r"E:\Living_zone\textures12012025\textures",
    r"E:\stalker anomaly\textures",
)
_NH_OBJECTS_DEFAULT_COMMON_ROOT = r"P:\NH_Objects\Common"
_NH_OBJECTS_DEFAULT_ENVIRONMENT_ROOT = r"P:\NH_Objects\Environment"
_NH_OBJECTS_DEFAULT_CUSTOM_SEARCH_ROOT = r"P:\NH_Objects"