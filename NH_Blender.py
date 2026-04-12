bl_info = {
    "name": "NH Plugin for Blender",
    "author": "Daryl and Enisam",
    "version": (0, 4, 0),
    "blender": (5, 1, 0),
    "location": "3D Viewport > N-panel > NH Plugin",
    "description": "Scatter Arma3 Proxy objects using DayZ clutter config + Texture Replace (.paa/.rvmat) + Replace from DB via A3OB",
    "doc_url": "https://github.com/BigbyOn/nh-blender-addon",
    "tracker_url": "https://github.com/BigbyOn/nh-blender-addon/issues",
    "mclink": "https://github.com/BigbyOn/nh-blender-addon",
    "category": "Object",
}

import bpy
import bmesh
from bpy.app.handlers import persistent
from bpy.types import Operator, Panel, PropertyGroup, UIList, OperatorFileListElement
from bpy.props import PointerProperty, StringProperty, FloatProperty, IntProperty, BoolProperty, EnumProperty, CollectionProperty
from mathutils import Vector, Matrix
import math
import random
import os
import re
import shutil
import importlib
import json
from contextlib import contextmanager
import uuid

# ------------------------------------------------------------------------
#  Global config storage
# ------------------------------------------------------------------------

CONFIG_PATH = ""
CONFIG_SURFACES = {}
CONFIG_CLUTTER = {}
_PROXY_MESH_NAME = "DayZ_ClutterProxyMesh"
_SCATTER_PROXY_TAG_PROP = "cray_scatter_proxy"
_ASSET_CATALOG_NAME = "Asset"
_ASSET_CATALOG_FALLBACK_ID = "7d6f3b1d-4d5f-4b1e-9f77-5d1e8dd5c001"
_ADDON_KEYMAP_ITEMS = []
_PLAIN_AXIS_HELPER_PROP = "cray_plain_axis_helper"
_PLAIN_AXIS_ROOT_PROP = "cray_plain_axis_root"
_PLAIN_AXIS_SOURCE_OBJECT_PROP = "cray_plain_axis_source_object"
_PLAIN_AXIS_CONSTRAINT_NAME = "NH Plain Axis"
_PLAIN_AXIS_HOTKEY_REGISTERED = False
_MESH_KEYMAP_NAME = "Mesh"
_LINKED_PICK_CONFLICT_KEYMAPS = {
    "Mesh",
    "3D View",
    "3D View Generic",
}
_PERSISTED_UI_STATE_FILENAME = "nh_blender_ui_state.json"
_PERSISTED_UI_STATE_TIMER_INTERVAL = 1.0
_PERSISTED_UI_STATE_CACHE = None
_TRASH_TINY_ISLAND_MAX_VERTS = 5
_TRASH_TINY_ISLAND_MAX_FACES = 6
_TRASH_TINY_ISLAND_MAX_EDGES = 9
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
    ),
    "cray_collider_settings": (
        "target_lod",
        "box_thickness",
        "bounds_padding",
        "merge_distance",
        "recalc_normals",
        "show_hotkey_button_fallbacks",
        "show_advanced_build_buttons",
        "roadway_weld_distance",
    ),
    "cray_texreplace_settings": (
        "folder",
        "fix_mesh_join_batch",
        "fix_mesh_center_to_origin",
        "split_planar_ngon_vertex_count",
        "split_planar_ngon_angle_tolerance",
        "split_planar_ngon_plane_tolerance",
    ),
    "cray_ie_settings": (
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
        "delete_originals",
    ),
    "cray_asset_library_settings": (
        "folder",
        "import_first_lod_only",
        "clear_previous_temp_library",
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
    state = {}
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


def _property_has_default_value(settings, prop_name: str) -> bool:
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

    applied = 0
    for settings_name, prop_names in _PERSISTED_UI_SETTINGS.items():
        settings = getattr(scene, settings_name, None)
        saved_block = raw.get(settings_name)
        if settings is None or not isinstance(saved_block, dict):
            continue

        for prop_name in prop_names:
            if prop_name not in saved_block:
                continue
            if only_if_default and not _property_has_default_value(settings, prop_name):
                continue

            try:
                setattr(settings, prop_name, saved_block[prop_name])
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


@persistent
def _restore_persisted_ui_state_on_load(_dummy):
    global _PERSISTED_UI_STATE_CACHE
    _apply_persisted_ui_state_to_all_scenes(only_if_default=True)
    _PERSISTED_UI_STATE_CACHE = _collect_persisted_ui_state(getattr(bpy.context, "scene", None))


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
        event_type="BUTTON5MOUSE",
        value="PRESS",
    )
    if _mesh_shortcut_is_free(window_manager, event_type="BUTTON4MOUSE", value="PRESS"):
        _register_addon_keymap_item(
            keymap,
            "cray.build_collider",
            event_type="BUTTON4MOUSE",
            value="PRESS",
            properties={"build_mode": "SELECTION_HULL"},
        )
    else:
        print("[NH Plugin] Mouse4 is already in use, skipping Selection -> Hull shortcut.")

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

# ------------------------------------------------------------------------
#  Brace helpers
# ------------------------------------------------------------------------

def _extract_block(src: str, brace_index: int):
    depth = 1
    i = brace_index + 1
    n = len(src)
    while i < n and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    if depth != 0:
        raise RuntimeError("Unbalanced braces while parsing config")
    return src[brace_index + 1 : i - 1], i


def _find_class_block(src: str, class_name: str):
    m = re.search(r"class\s+" + re.escape(class_name) + r"\b[^{]*{", src)
    if not m:
        return None
    brace_index = src.find("{", m.start())
    if brace_index == -1:
        return None
    body, _ = _extract_block(src, brace_index)
    return body


def _iter_inner_classes(block: str):
    pos = 0
    n = len(block)
    while pos < n:
        m = re.search(r"class\s+(\w+)[^{]*{", block[pos:])
        if not m:
            break
        name = m.group(1)
        brace_index = pos + m.end() - 1
        body, new_pos = _extract_block(block, brace_index)
        yield name, body
        pos = new_pos


# ------------------------------------------------------------------------
#  Parsing DayZ .cpp
# ------------------------------------------------------------------------

def parse_dayz_config(path: str):
    global CONFIG_PATH, CONFIG_SURFACES, CONFIG_CLUTTER

    if not os.path.isfile(path):
        raise RuntimeError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    text = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

    surfaces = {}
    clutter = {}

    cfgworlds_block = _find_class_block(text, "CfgWorlds")
    if cfgworlds_block:
        caworld_block = _find_class_block(cfgworlds_block, "CAWorld")
        if caworld_block:
            clutter_block = _find_class_block(caworld_block, "Clutter")
            if clutter_block:
                for c_name, c_body in _iter_inner_classes(clutter_block):
                    m_model = re.search(r'model\s*=\s*"([^"]+)"', c_body)
                    if not m_model:
                        continue
                    model_path = m_model.group(1)
                    m_smin = re.search(r"scaleMin\s*=\s*([0-9.eE+-]+)", c_body)
                    m_smax = re.search(r"scaleMax\s*=\s*([0-9.eE+-]+)", c_body)
                    smin = float(m_smin.group(1)) if m_smin else 1.0
                    smax = float(m_smax.group(1)) if m_smax else 1.0
                    clutter[c_name] = {"model": model_path, "scaleMin": smin, "scaleMax": smax}

    cfgsurf_block = _find_class_block(text, "CfgSurfaceCharacters")
    if cfgsurf_block:
        for s_name, s_body in _iter_inner_classes(cfgsurf_block):
            m_prob = re.search(r"probability\s*\[\]\s*=\s*{([^}]*)}", s_body)
            m_names = re.search(r"names\s*\[\]\s*=\s*{([^}]*)}", s_body, re.S)
            if not (m_prob and m_names):
                continue

            probs_str = m_prob.group(1)
            names_str = m_names.group(1)

            probs = [p.strip() for p in probs_str.split(",") if p.strip()]
            names = [n.strip().strip('"') for n in names_str.split(",") if n.strip()]

            if len(probs) != len(names):
                continue

            probs_f = [float(p) for p in probs]
            surfaces[s_name] = {"names": names, "probs": probs_f}

    CONFIG_PATH = path
    CONFIG_SURFACES = surfaces
    CONFIG_CLUTTER = clutter


def build_clutter_distribution(surface_name: str):
    if surface_name not in CONFIG_SURFACES:
        raise RuntimeError(f"Surface '{surface_name}' not found in CfgSurfaceCharacters")

    s_def = CONFIG_SURFACES[surface_name]
    names = s_def["names"]
    probs = s_def["probs"]

    used_names, used_probs, used_defs = [], [], {}
    clutter_map_lc = {k.lower(): k for k in CONFIG_CLUTTER.keys()}

    for n, p in zip(names, probs):
        if p <= 0.0:
            continue
        key = clutter_map_lc.get(n.lower())
        if key is None:
            raise RuntimeError(
                f"Clutter class '{n}' is referenced by surface '{surface_name}' "
                f"but not found in CfgWorlds->CAWorld->Clutter"
            )
        c_def = CONFIG_CLUTTER[key]
        model_path = (c_def.get("model") or "").strip()
        if not model_path:
            raise RuntimeError(f"Clutter class '{key}' has no 'model' defined")
        used_names.append(n)
        used_probs.append(p)
        used_defs[n] = c_def

    if not used_names:
        raise RuntimeError(f"Surface '{surface_name}' has no clutter with non-zero probability")

    total = sum(used_probs)
    if total <= 0.0:
        raise RuntimeError(f"Surface '{surface_name}' probabilities sum to zero")

    norm_probs = [p / total for p in used_probs]
    return used_names, norm_probs, used_defs


def pick_weighted_random(names, probs, rng=None):
    rng = rng or random
    r = rng.random()
    acc = 0.0
    for n, p in zip(names, probs):
        acc += p
        if r <= acc:
            return n
    return names[-1]

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
    normal_matrix = world.to_3x3().inverted().transposed()
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
#  Proxy mesh & A3OB properties
# ------------------------------------------------------------------------

def get_proxy_mesh():
    mesh = bpy.data.meshes.get(_PROXY_MESH_NAME)
    if mesh is None:
        mesh = bpy.data.meshes.new(_PROXY_MESH_NAME)
        mesh.from_pydata([(0.0, 0.0, 0.0), (0.0, 0.0, 2.0), (0.0, 1.0, 0.0)], [], [(0, 1, 2)])
        mesh.update(calc_edges=True)
    return mesh


def make_pdrive_path(model_path: str) -> str:
    if not model_path:
        return model_path
    p = model_path.strip().replace("/", "\\")
    if p.lower().startswith("p:\\"):
        return p
    while p.startswith("\\"):
        p = p[1:]
    return "p:\\" + p


def set_a3ob_proxy_properties(proxy_obj, model_path: str, proxy_index: int):
    if not hasattr(proxy_obj, "a3ob_properties_object_proxy"):
        raise RuntimeError(
            "Object has no 'a3ob_properties_object_proxy'. "
            "Ensure addon 'Arma 3 Object Builder' is installed and enabled."
        )

    pg = proxy_obj.a3ob_properties_object_proxy

    name_to_id = {}
    for prop in pg.bl_rna.properties:
        if prop.identifier == "rna_type":
            continue
        name_to_id[prop.name] = prop.identifier

    is_id = name_to_id.get("Is P3D Proxy")
    if is_id and hasattr(pg, is_id):
        setattr(pg, is_id, True)
    elif hasattr(pg, "is_a3_proxy"):
        pg.is_a3_proxy = True

    arma_path = make_pdrive_path(model_path)

    path_id = name_to_id.get("Path")
    if path_id and hasattr(pg, path_id):
        setattr(pg, path_id, arma_path)
    elif hasattr(pg, "path"):
        pg.path = arma_path

    index_id = name_to_id.get("Index")
    if index_id and hasattr(pg, index_id):
        setattr(pg, index_id, proxy_index)
    elif hasattr(pg, "index"):
        pg.index = proxy_index


def create_proxy_object(context, collection, parent_obj, location: Vector, normal: Vector,
                        model_path: str, proxy_index: int, scale_min: float = 1.0, scale_max: float = 1.0,
                        rng=None):
    proxy_mesh = get_proxy_mesh()
    proxy_obj = bpy.data.objects.new(f"clutter_proxy_{proxy_index}", proxy_mesh)

    n = normal.normalized()
    up = Vector((0.0, 0.0, 1.0))
    if abs(n.dot(up)) > 0.999:
        up = Vector((0.0, 1.0, 0.0))

    x_axis = up.cross(n).normalized()
    y_axis = n.cross(x_axis).normalized()

    rot_mat = Matrix(((x_axis.x, y_axis.x, n.x),
                      (x_axis.y, y_axis.y, n.y),
                      (x_axis.z, y_axis.z, n.z)))

    proxy_obj.matrix_world = Matrix.Translation(location) @ rot_mat.to_4x4()
    rng = rng or random
    s = rng.uniform(scale_min, scale_max)
    proxy_obj.scale = (s, s, s)

    collection.objects.link(proxy_obj)
    proxy_obj.parent = parent_obj
    try:
        proxy_obj[_SCATTER_PROXY_TAG_PROP] = True
    except Exception:
        pass
    set_a3ob_proxy_properties(proxy_obj, model_path, proxy_index)
    return proxy_obj

def _is_generated_scatter_proxy(obj, parent_obj=None) -> bool:
    if obj is None:
        return False
    if parent_obj is not None and obj.parent != parent_obj:
        return False
    if obj.get(_SCATTER_PROXY_TAG_PROP, False):
        return True

    try:
        proxy_mesh = get_proxy_mesh()
    except Exception:
        proxy_mesh = None

    if proxy_mesh is not None and getattr(obj, "data", None) == proxy_mesh:
        if (obj.name or "").startswith("clutter_proxy_"):
            return True
    return False

def _clear_generated_scatter_proxies(parent_obj) -> int:
    if parent_obj is None:
        return 0

    to_remove = [obj for obj in bpy.data.objects if _is_generated_scatter_proxy(obj, parent_obj=parent_obj)]
    to_remove.sort(key=lambda item: _obj_depth(item), reverse=True)

    removed = 0
    for obj in to_remove:
        if bpy.data.objects.get(obj.name) is None:
            continue
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1
    return removed


# ------------------------------------------------------------------------
#  UI helpers
# ------------------------------------------------------------------------

def get_surface_enum_items(self, context):
    items = [("NONE", "<no surface>", "Surface is not selected")]
    if not CONFIG_SURFACES:
        return items
    for name in sorted(CONFIG_SURFACES.keys()):
        items.append((name, name, "Surface from CfgSurfaceCharacters"))
    return items


# ------------------------------------------------------------------------
#  Settings
# ------------------------------------------------------------------------

class CRAY_PG_Settings(PropertyGroup):
    source_object: PointerProperty(name="Source Object", type=bpy.types.Object)
    vertex_group: StringProperty(name="Vertex Group", default="")
    target_collection: PointerProperty(name="Target Collection", type=bpy.types.Collection)
    config_path: StringProperty(name="Config .cpp", default="", subtype="FILE_PATH")
    selected_surface: EnumProperty(name="Surface", items=get_surface_enum_items)
    grid_size: FloatProperty(name="Grid Size", default=1.0, min=0.01)
    density_scale: FloatProperty(name="Density Scale", default=1.0, min=0.01, soft_max=8.0)
    slope_falloff: FloatProperty(
        name="Slope Falloff",
        description="Reduce clutter density on steeper faces; 0 disables the reduction",
        default=2.0,
        min=0.0,
        soft_max=4.0,
    )
    max_height_offset: FloatProperty(name="Height Offset", default=2.0, min=0.0)
    max_distance: FloatProperty(name="Max Distance", default=100.0, min=0.1)
    random_jitter: FloatProperty(name="Random Jitter", default=0.5, min=0.0, max=1.0)
    spawn_probability: FloatProperty(name="Spawn Probability", default=1.0, min=0.0, max=1.0)
    max_proxies: IntProperty(name="Max Proxies (0=unlimited)", default=0, min=0)
    seed: IntProperty(name="Random Seed", default=0)
    only_hit_source: BoolProperty(name="Only Hit Source", default=True)

class CRAY_PG_SnapSettings(PropertyGroup):
    source_object: PointerProperty(
        name="Resolution LOD (A)",
        description="Resolution/source LOD for the first A target",
        type=bpy.types.Object,
    )
    memory_object: PointerProperty(
        name="Memory LOD (A)",
        description="Memory LOD object for the first A target",
        type=bpy.types.Object,
    )
    paired_object: PointerProperty(
        name="Resolution LOD (V)",
        description="Resolution/source LOD for the second V target",
        type=bpy.types.Object,
    )
    paired_memory_object: PointerProperty(
        name="Memory LOD (V)",
        description="Memory LOD object for the second V target",
        type=bpy.types.Object,
    )
    snap_group: StringProperty(name="Snap Group", default="SampleName")
    snap_p3d_name: StringProperty(
        name="P3D Name",
        description="Only letters and digits are kept; spaces, underscores, .p3d and other symbols are removed automatically",
        default="SampleName",
        update=_on_snap_p3d_name_changed,
    )
    snap_pair_code: StringProperty(name="ID", default="01", maxlen=3)
    snap_side: EnumProperty(
        name="Side",
        items=(
            ("a", "A", "Create A-side snap points"),
            ("v", "V", "Create V-side snap points"),
        ),
        default="a",
    )
    edge_axis: EnumProperty(
        name="Snap Axis",
        items=(
            ("X", "X", "Use X in the snap point name pattern"),
            ("Y", "Y", "Use Y in the snap point name pattern"),
            ("Z", "Z", "Use Z in the snap point name pattern"),
        ),
        default="X",
    )
    edge_side: EnumProperty(
        name="Edge Side",
        items=(
            ("NEG", "Min", "Use minimum edge value"),
            ("POS", "Max", "Use maximum edge value"),
        ),
        default="POS",
    )
    edge_span_axis: EnumProperty(
        name="Span Axis",
        items=(
            ("AUTO", "Auto", "Auto-pick span axis from Edge Axis"),
            ("X", "X", "Use X as span axis"),
            ("Y", "Y", "Use Y as span axis"),
            ("Z", "Z", "Use Z as span axis"),
        ),
        default="AUTO",
    )
    edge_tolerance: FloatProperty(
        name="Edge Tolerance",
        description="Band size near edge (fraction of model size along edge axis)",
        default=0.03,
        min=0.0,
        max=0.5,
    )
    show_auto_edge_fallback: BoolProperty(
        name="Show Auto Edge Fallback",
        description="Show fallback settings used only when no 2 vertices are selected in Edit Mode",
        default=False,
    )
    replace_existing: BoolProperty(name="Replace Existing Named Groups", default=True)
    batch_cleanup_imported: BoolProperty(name="Cleanup Imported Objects", default=True)
    batch_overwrite_bak: BoolProperty(name="Overwrite .bak", default=True)

class CRAY_PG_ModelSplitSettings(PropertyGroup):
    part_number: IntProperty(
        name="Part Number",
        description="Numeric suffix for the new split part collection",
        default=1,
        min=1,
        max=999,
    )


_COLLIDER_TARGET_LOD_ITEMS = (
    ("6", "Geometry", "Object collision geometry and occluders"),
    ("14", "View Geometry", "View occlusion for AI"),
    ("15", "Fire Geometry", "Hitbox geometry"),
)
_COLLIDER_LOD_NAMES = {
    "6": "Geometry",
    "8": "Geometry PhysX",
    "14": "View Geometry",
    "15": "Fire Geometry",
}
_COLLIDER_KNOWN_LOD_NAMES = {
    **_COLLIDER_LOD_NAMES,
    "0": "Resolution",
    "9": "Memory",
    "11": "Roadway",
}
_COLLIDER_COLLECTION_NAME = "Geometries"
_COLLIDER_COLLECTION_ALIASES = ("Geometry",)
_COLLIDER_COLLECTION_COLOR = "COLOR_03"
_COLLIDER_OBJECT_COLOR = (1.0, 0.93, 0.55, 1.0)
_MEMORY_COLLECTION_NAME = "Point clouds"
_MEMORY_COLLECTION_ALIASES = ("Memory",)
_MEMORY_COLLECTION_COLOR = "COLOR_05"
_MISC_COLLECTION_NAME = "Misc"
_MISC_COLLECTION_COLOR = "COLOR_04"
_ROADWAY_LOD_TOKEN = "11"
_ROADWAY_OBJECT_COLOR = (0.72, 0.88, 1.0, 1.0)
_ROADWAY_SURFACES_FOLDER = r"P:\DZ\surfaces\data\roadway"
_ROADWAY_MATERIAL_NONE = "__NONE__"
_ROADWAY_MATERIAL_ENUM_CACHE = [
    (_ROADWAY_MATERIAL_NONE, "<no materials>", "Roadway object has no assigned materials")
]


def get_roadway_material_enum_items(self, context):
    del self

    items = []
    cs = getattr(getattr(context, "scene", None), "cray_collider_settings", None)
    obj = getattr(cs, "roadway_object", None) if cs else None

    if obj is None or obj.type != "MESH":
        items = [(_ROADWAY_MATERIAL_NONE, "<no roadway object>", "Assign a Roadway Object to list its materials")]
    else:
        seen = set()
        for slot_idx, slot in enumerate(obj.material_slots, start=1):
            mat = slot.material
            if mat is None:
                continue

            key = mat.name.lower()
            if key in seen:
                continue
            seen.add(key)

            image_names = []
            if mat.use_nodes and mat.node_tree:
                for node in mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and getattr(node, "image", None):
                        image_names.append(node.image.name)

            desc = f"Roadway material from slot {slot_idx}"
            if image_names:
                uniq_images = sorted(set(image_names), key=lambda x: x.lower())
                desc = f"{desc} | Images: {', '.join(uniq_images[:3])}"
                if len(uniq_images) > 3:
                    desc += f" (+{len(uniq_images) - 3} more)"

            items.append((mat.name, mat.name, desc))

        if not items:
            items = [(_ROADWAY_MATERIAL_NONE, "<no materials>", "Selected Roadway Object has no assigned materials")]

    global _ROADWAY_MATERIAL_ENUM_CACHE
    _ROADWAY_MATERIAL_ENUM_CACHE = items
    return _ROADWAY_MATERIAL_ENUM_CACHE


def _on_collider_target_lod_changed(self, context):
    cs = self
    if context is None:
        return

    target_obj = getattr(cs, "geometry_object", None)
    if target_obj is None or target_obj.type != "MESH":
        return

    lod_token = str(getattr(cs, "target_lod", "") or "").strip()
    if lod_token not in _COLLIDER_LOD_NAMES:
        return

    try:
        _set_collider_lod_a3ob_props(target_obj, lod_token)
        _apply_collider_visual_style(target_obj)
        _enable_collider_object_color_preview(context)
        _set_collider_settings_object(context, "geometry_object", target_obj)
    except Exception:
        pass


class CRAY_PG_ColliderSettings(PropertyGroup):
    source_object: PointerProperty(
        name="Source Object",
        description="Visual/source object used to build colliders",
        type=bpy.types.Object,
    )
    geometry_object: PointerProperty(
        name="Target LOD Object",
        description="Geometry LOD mesh that receives generated colliders",
        type=bpy.types.Object,
    )
    target_lod: EnumProperty(
        name="Target LOD",
        description="A3OB LOD type for the generated collider object",
        items=_COLLIDER_TARGET_LOD_ITEMS,
        default="6",
        update=_on_collider_target_lod_changed,
    )
    box_thickness: FloatProperty(
        name="Thickness",
        description="Thickness used for wall-like selections and flat convex hull fallback",
        default=0.20,
        min=0.0,
        precision=4,
        unit="LENGTH",
    )
    bounds_padding: FloatProperty(
        name="Bounds Padding",
        description="Expand the object bounds before creating a box collider",
        default=0.0,
        min=0.0,
        precision=4,
        unit="LENGTH",
    )
    merge_distance: FloatProperty(
        name="Merge Distance",
        description="Optional weld distance applied to the new collider points before convex hull",
        default=0.0,
        min=0.0,
        precision=5,
        unit="LENGTH",
    )
    recalc_normals: BoolProperty(
        name="Recalculate Normals",
        description="Recalculate normals on the newly created collider faces",
        default=True,
    )
    show_hotkey_button_fallbacks: BoolProperty(
        name="Show Hotkey Buttons",
        description="Show clickable fallback buttons for the collider hotkeys",
        default=False,
    )
    show_advanced_build_buttons: BoolProperty(
        name="Show Extra Build Buttons",
        description="Show extra build buttons that are not on hotkeys",
        default=False,
    )
    roadway_object: PointerProperty(
        name="Roadway Object",
        description="Roadway LOD mesh stored in Misc collection",
        type=bpy.types.Object,
    )
    roadway_material: EnumProperty(
        name="Roadway Material",
        description="Current material on the Roadway object",
        items=get_roadway_material_enum_items,
    )
    roadway_weld_distance: FloatProperty(
        name="Roadway Weld Distance",
        description="Merge nearly coincident Roadway vertices so AI pathing stays fully connected",
        default=0.0001,
        min=0.0,
        precision=6,
        unit="LENGTH",
    )


# ------------------------------------------------------------------------
#  Operators (scatter)
# ------------------------------------------------------------------------

class CRAY_OT_LoadConfig(Operator):
    bl_idname = "cray.load_config"
    bl_label = "Load .cpp & Parse"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.cray_settings
        if not s.config_path:
            self.report({"ERROR"}, "Config .cpp path is empty")
            return {"CANCELLED"}

        config_abs = bpy.path.abspath(s.config_path)
        if not os.path.isfile(config_abs):
            self.report({"ERROR"}, f"Config file not found: {config_abs}")
            return {"CANCELLED"}

        try:
            parse_dayz_config(config_abs)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to parse config '{config_abs}': {_fmt_exc(e)}")
            return {"CANCELLED"}

        if not CONFIG_SURFACES:
            self.report({"WARNING"}, "No surfaces found in CfgSurfaceCharacters")
        else:
            self.report({"INFO"}, f"Loaded {len(CONFIG_SURFACES)} surfaces and {len(CONFIG_CLUTTER)} clutter classes")

        s.selected_surface = "NONE"
        return {"FINISHED"}


class CRAY_OT_ScatterProxies(Operator):
    bl_idname = "object.cray_scatter_proxies"
    bl_label = "Scatter Proxies (DayZ-style)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.cray_settings
        try:
            obj = _resolve_scatter_edit_mesh_object(context)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}
        if not s.config_path:
            self.report({"ERROR"}, "Config .cpp path is not set")
            return {"CANCELLED"}
        if s.selected_surface == "NONE":
            self.report({"ERROR"}, "Surface is not selected")
            return {"CANCELLED"}
        if not hasattr(obj, "a3ob_properties_object_proxy"):
            self.report({"ERROR"}, "Missing 'a3ob_properties_object_proxy' (check Arma 3 Object Builder).")
            return {"CANCELLED"}

        config_abs = bpy.path.abspath(s.config_path)
        if not os.path.isfile(config_abs):
            self.report({"ERROR"}, f"Config file not found: {config_abs}")
            return {"CANCELLED"}

        try:
            parse_dayz_config(config_abs)
            clutter_names, clutter_probs, clutter_defs = build_clutter_distribution(s.selected_surface)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        triangles = _collect_selected_face_triangles_world(obj)
        if not triangles:
            self.report({"ERROR"}, "Select polygons in Edit Mode first")
            return {"CANCELLED"}

        if s.density_scale <= 0.0:
            self.report({"ERROR"}, "Density scale must be > 0")
            return {"CANCELLED"}
        grid = s.grid_size / math.sqrt(s.density_scale)
        if grid <= 0.0:
            self.report({"ERROR"}, "Grid size must be > 0")
            return {"CANCELLED"}

        if obj.users_collection:
            target_coll = obj.users_collection[0]
        else:
            target_coll = context.scene.collection

        cell_area = grid * grid
        if cell_area <= 0.0:
            self.report({"ERROR"}, "Density parameters produced invalid sample area")
            return {"CANCELLED"}

        return_to_edit = (obj.mode == "EDIT")
        if return_to_edit:
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as e:
                self.report({"ERROR"}, f"Failed to switch to Object Mode: {_fmt_exc(e)}")
                return {"CANCELLED"}

        created_count = 0
        proxy_index = 0
        candidate_count = 0
        skipped_by_probability = 0
        limit_reached = False
        total_area = 0.0
        removed_count = 0
        try:
            removed_count = _clear_generated_scatter_proxies(obj)

            for tri_idx, (v0, v1, v2, tri_normal, tri_area) in enumerate(triangles, start=1):
                total_area += tri_area
                slope_factor = _scatter_slope_density_factor(tri_normal, s.slope_falloff)
                if slope_factor <= 1e-6:
                    continue

                expected = (tri_area / cell_area) * slope_factor
                tri_rng = random.Random((int(s.seed) ^ (tri_idx * 2654435761)) & 0xFFFFFFFFFFFFFFFF)
                samples = int(expected)
                if tri_rng.random() < max(0.0, expected - samples):
                    samples += 1

                for sample_idx in range(samples):
                    if s.max_proxies > 0 and created_count >= s.max_proxies:
                        limit_reached = True
                        break

                    candidate_count += 1
                    sample_rng = random.Random(
                        ((int(s.seed) & 0xFFFFFFFF) ^ (tri_idx * 73856093) ^ ((sample_idx + 1) * 19349663))
                        & 0xFFFFFFFFFFFFFFFF
                    )
                    if s.spawn_probability < 1.0 and sample_rng.random() > s.spawn_probability:
                        skipped_by_probability += 1
                        continue

                    hit_loc = _sample_point_on_triangle(v0, v1, v2, sample_rng)
                    clutter_class = pick_weighted_random(clutter_names, clutter_probs, rng=sample_rng)
                    c_def = clutter_defs[clutter_class]
                    proxy_index += 1

                    create_proxy_object(
                        context=context,
                        collection=target_coll,
                        parent_obj=obj,
                        location=hit_loc,
                        normal=tri_normal,
                        model_path=c_def["model"],
                        proxy_index=proxy_index,
                        scale_min=c_def.get("scaleMin", 1.0),
                        scale_max=c_def.get("scaleMax", 1.0),
                        rng=sample_rng,
                    )
                    created_count += 1
                if limit_reached:
                    break
        finally:
            if return_to_edit:
                try:
                    context.view_layer.objects.active = obj
                except Exception:
                    pass
                try:
                    bpy.ops.object.mode_set(mode="EDIT")
                except Exception:
                    pass

        limit_suffix = " (max limit reached)" if limit_reached else ""
        self.report(
            {"INFO"},
            (
                f"Removed {removed_count}, created {created_count} proxies from {len(triangles)} selected triangle(s)"
                f" | area: {total_area:.2f}, candidates: {candidate_count}, prob-skip: {skipped_by_probability}"
                f"{limit_suffix}"
            ),
        )
        return {"FINISHED"}


# ------------------------------------------------------------------------
#  Snap points (.sp_*) for Memory LOD
# ------------------------------------------------------------------------

_SP_GROUP_RE = re.compile(r"^[A-Za-z0-9_]+$")
_SP_P3D_NAME_RE = re.compile(r"^[A-Za-z0-9]+$")
_SP_PAIR_CODE_RE = re.compile(r"^[A-Za-z0-9]{1,3}$")

_A3OB_IMPORT_CANDIDATES = (
    (
        "a3ob.import_p3d",
        (
            "filepath",
            "first_lod_only",
            "absolute_paths",
            "enclose",
            "groupby",
            "additional_data_allowed",
            "additional_data",
            "validate_meshes",
            "proxy_action",
            "translate_selections",
            "cleanup_empty_selections",
            "load_textures",
        ),
    ),
    ("import_scene.a3ob_p3d", ("filepath",)),
    ("import_scene.a3ob_model", ("filepath",)),
    ("a3ob.import_model", ("filepath",)),
)

_A3OB_EXPORT_CANDIDATES = (
    (
        "a3ob.export_p3d",
        (
            "filepath",
            "use_selection",
            "visible_only",
            "relative_paths",
            "preserve_normals",
            "validate_meshes",
            "apply_transforms",
            "apply_modifiers",
            "sort_sections",
            "lod_collisions",
            "validate_lods",
            "validate_lods_warning_errors",
            "generate_components",
            "force_lowercase",
        ),
    ),
    ("export_scene.a3ob_p3d", ("filepath", "use_selection")),
    ("a3ob.export_model", ("filepath", "use_selection")),
)

_A3OB_IMPORT_READ_FILE_PATCHES = []
_A3OB_IMPORT_TRACKING_SUPPRESS_DEPTH = 0

def _op_handle(op_idname: str):
    try:
        mod, op = op_idname.split(".", 1)
    except ValueError:
        return None
    mod_obj = getattr(bpy.ops, mod, None)
    if mod_obj is None:
        return None
    return getattr(mod_obj, op, None)

def _has_any_a3ob_import_ops():
    return any(_op_handle(op) is not None for op, _ in _A3OB_IMPORT_CANDIDATES)

def _has_any_a3ob_io_ops():
    has_import = _has_any_a3ob_import_ops()
    has_export = any(_op_handle(op) is not None for op, _ in _A3OB_EXPORT_CANDIDATES)
    return has_import and has_export

def _call_first_available(op_candidates, **kwargs):
    last_err = None
    for op_idname, allowed_keys in op_candidates:
        fn = _op_handle(op_idname)
        if fn is None:
            continue
        payload = {k: v for k, v in kwargs.items() if k in allowed_keys}
        try:
            rna = fn.get_rna_type()
            valid_keys = {prop.identifier for prop in rna.properties if prop.identifier != "rna_type"}
            payload = {k: v for k, v in payload.items() if k in valid_keys}
        except Exception:
            pass
        try:
            result = fn(**payload)
            if isinstance(result, set) and "CANCELLED" in result:
                last_err = RuntimeError(f"{op_idname} returned CANCELLED")
                continue
            return result, op_idname, None
        except Exception as e:
            last_err = e
            continue
    return None, None, last_err


@contextmanager
def _suppress_a3ob_import_tracking():
    global _A3OB_IMPORT_TRACKING_SUPPRESS_DEPTH
    _A3OB_IMPORT_TRACKING_SUPPRESS_DEPTH += 1
    try:
        yield
    finally:
        _A3OB_IMPORT_TRACKING_SUPPRESS_DEPTH = max(0, _A3OB_IMPORT_TRACKING_SUPPRESS_DEPTH - 1)


def _import_first_available_module(module_names):
    for module_name in module_names:
        try:
            return importlib.import_module(module_name)
        except Exception:
            continue
    return None


@contextmanager
def _temporary_disable_a3ob_lod_validation(enabled: bool):
    if not enabled:
        yield False
        return

    export_mod = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.export_p3d",
            "Arma3ObjectBuilder.io.export_p3d",
        )
    )
    if export_mod is None:
        yield False
        return

    validator_cls = getattr(export_mod, "Validator", None)
    original_validate = getattr(validator_cls, "validate_lod", None) if validator_cls else None
    if validator_cls is None or not callable(original_validate):
        yield False
        return

    def _always_valid(self, obj, lod, lazy=False, warns_errs=True, relative_paths=False):
        return True

    validator_cls.validate_lod = _always_valid
    try:
        yield True
    finally:
        validator_cls.validate_lod = original_validate


def _call_export_with_optional_relaxed_validation(force_all_lods: bool, **kwargs):
    with _temporary_disable_a3ob_lod_validation(force_all_lods):
        return _call_first_available(_A3OB_EXPORT_CANDIDATES, **kwargs)


def _get_a3ob_data_p3d_module():
    return _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.data_p3d",
            "Arma3ObjectBuilder.io.data_p3d",
        )
    )


def _lod_signature_key(signature: float) -> str:
    return f"{float(signature):.6e}"


def _a3ob_lod_signature_from_props(props, p3d_mod):
    lod_res_cls = getattr(p3d_mod, "P3D_LOD_Resolution", None)
    if lod_res_cls is None:
        return None

    try:
        lod_idx = int(getattr(props, "lod", 0))
    except Exception:
        return None

    lod_unknown = int(getattr(lod_res_cls, "UNKNOWN", -1))
    try:
        if lod_idx == lod_unknown:
            resolution = float(getattr(props, "resolution_float", 0.0) or 0.0)
        else:
            resolution = float(getattr(props, "resolution", 0.0) or 0.0)
    except Exception:
        resolution = 0.0

    try:
        signature = lod_res_cls.encode(lod_idx, resolution)
    except Exception:
        return None
    if signature is None:
        return None
    return float(signature)


def _collect_expected_lod_entries(export_objects):
    p3d_mod = _get_a3ob_data_p3d_module()
    if p3d_mod is None:
        return {}

    expected = {}
    for obj in export_objects:
        if obj is None or obj.type != "MESH" or obj.parent is not None:
            continue
        if not hasattr(obj, "a3ob_properties_object"):
            continue

        props = obj.a3ob_properties_object
        if not bool(getattr(props, "is_a3_lod", False)):
            continue

        signature = _a3ob_lod_signature_from_props(props, p3d_mod)
        if signature is None:
            continue

        try:
            lod_name = str(props.get_name())
        except Exception:
            lod_name = obj.name

        key = _lod_signature_key(signature)
        rec = expected.get(key)
        if rec is None:
            expected[key] = {
                "signature": signature,
                "lod_name": lod_name,
                "objects": [obj.name],
            }
        else:
            if obj.name not in rec["objects"]:
                rec["objects"].append(obj.name)

    return expected


def _read_exported_lod_entries(filepath: str):
    p3d_mod = _get_a3ob_data_p3d_module()
    if p3d_mod is None:
        raise RuntimeError("A3OB data_p3d module is not available")

    mlod = p3d_mod.P3D_MLOD.read_file(filepath, first_lod_only=False)
    exported = {}
    for lod in getattr(mlod, "lods", []):
        try:
            signature = float(lod.resolution)
        except Exception:
            continue
        key = _lod_signature_key(signature)
        exported[key] = {"signature": signature}
    return exported


def _report_missing_lods_in_console(collection_name: str, filepath: str, expected_entries, exported_entries):
    expected_keys = set(expected_entries.keys())
    exported_keys = set(exported_entries.keys())
    missing_keys = sorted(
        expected_keys - exported_keys,
        key=lambda k: expected_entries[k]["signature"],
    )
    if not missing_keys:
        return []

    print("=== Batch Export Collections: Missing LODs ===")
    print(f"Collection: {collection_name}")
    print(f"File: {filepath}")
    print(
        "WARNING: Not all LODs were exported "
        f"(expected unique: {len(expected_keys)}, exported unique: {len(exported_keys)})"
    )
    for key in missing_keys:
        rec = expected_entries[key]
        objs = ", ".join(rec["objects"])
        print(f" - {rec['lod_name']} | signature: {rec['signature']:.6e} | object(s): {objs}")
    return missing_keys

def _is_memory_lod_mesh_object(obj) -> bool:
    return _MemoryLodManager.is_memory_lod_mesh_object(obj)

def _pick_memory_lod_object(context, source_obj):
    return _MemoryLodManager(context, source_obj).pick_existing_object()

def _set_memory_lod_a3ob_props(memory_obj):
    _MemoryLodManager.apply_a3ob_props(memory_obj)

def _ensure_memory_lod_object(context, source_obj, preferred_obj=None):
    return _MemoryLodManager(context, source_obj).ensure_object(preferred_obj=preferred_obj)

class _MemoryLodManager:
    OBJECT_NAME = "Memory"

    def __init__(self, context, source_obj=None, parent_collection=None):
        self.context = context
        self.source_obj = source_obj
        self.parent_collection = parent_collection

    @staticmethod
    def is_memory_lod_mesh_object(obj) -> bool:
        if obj is None or obj.type != "MESH":
            return False
        if obj.name == _MemoryLodManager.OBJECT_NAME:
            return True
        if not hasattr(obj, "a3ob_properties_object"):
            return False
        try:
            props = obj.a3ob_properties_object
            return str(getattr(props, "lod", "")) == "9"
        except Exception:
            return False

    def pick_existing_object(self):
        if self.parent_collection is not None:
            memory_collection = self.ensure_collection()
            if memory_collection is not None:
                direct = memory_collection.objects.get(self.OBJECT_NAME)
                if direct is not None and direct.type == "MESH":
                    return direct
                for obj in memory_collection.objects:
                    if self.is_memory_lod_mesh_object(obj):
                        return obj
            return None

        if self.source_obj is not None:
            memory_collection = self.ensure_collection()
            if memory_collection is not None:
                direct = memory_collection.objects.get(self.OBJECT_NAME)
                if direct is not None and direct.type == "MESH":
                    return direct
                for obj in memory_collection.objects:
                    if self.is_memory_lod_mesh_object(obj):
                        return obj

            for col in self.source_obj.users_collection:
                obj = col.objects.get(self.OBJECT_NAME)
                if obj is not None and obj.type == "MESH":
                    return obj
            return None

        obj = bpy.data.objects.get(self.OBJECT_NAME)
        if obj is not None and obj.type == "MESH":
            return obj

        for obj in self.context.scene.objects:
            if self.is_memory_lod_mesh_object(obj):
                return obj
        return None

    @staticmethod
    def apply_a3ob_props(memory_obj):
        if not hasattr(memory_obj, "a3ob_properties_object"):
            return
        try:
            props = memory_obj.a3ob_properties_object
            props.lod = "9"
            props.is_a3_lod = True
            _remove_a3ob_named_property(props, "autocenter")
        except Exception:
            pass

    def ensure_collection(self):
        if self.parent_collection is not None:
            return _ensure_named_child_collection(
                self.parent_collection,
                _MEMORY_COLLECTION_NAME,
                _MEMORY_COLLECTION_COLOR,
                aliases=_MEMORY_COLLECTION_ALIASES,
            )
        return _ensure_memory_collection(self.context, self.source_obj)

    def ensure_object(self, preferred_obj=None):
        if preferred_obj is not None and preferred_obj.type == "MESH":
            memory_obj = preferred_obj
        else:
            memory_obj = self.pick_existing_object()

        memory_collection = self.ensure_collection()
        if memory_obj is None:
            memory_mesh = bpy.data.meshes.new(self.OBJECT_NAME)
            memory_obj = bpy.data.objects.new(self.OBJECT_NAME, memory_mesh)
            if memory_collection is not None:
                memory_collection.objects.link(memory_obj)
            else:
                self.context.scene.collection.objects.link(memory_obj)
            if self.source_obj is not None:
                memory_obj.matrix_world = self.source_obj.matrix_world.copy()
        else:
            _move_object_to_collection(memory_obj, memory_collection)

        self.apply_a3ob_props(memory_obj)
        return memory_obj

def _sort_snap_pair_world_points(context, world_points):
    points = [p.copy() for p in world_points]
    if len(points) != 2:
        return points

    area = getattr(context, "area", None)
    space = getattr(context, "space_data", None)
    region_3d = getattr(space, "region_3d", None) if space is not None else None
    if area is not None and area.type == "VIEW_3D" and region_3d is not None:
        try:
            view_points = [(region_3d.view_matrix @ p.to_4d()).to_3d() for p in points]
            delta = view_points[1] - view_points[0]
            primary_axis = 0 if abs(delta[0]) >= abs(delta[1]) else 1
            secondary_axis = 1 - primary_axis
            if abs(delta[primary_axis]) > 1e-6 or abs(delta[secondary_axis]) > 1e-6:
                indexed = list(zip(points, view_points))
                indexed.sort(
                    key=lambda item: (
                        item[1][primary_axis],
                        item[1][secondary_axis],
                        item[0][2],
                        item[0][1],
                        item[0][0],
                    )
                )
                return [item[0] for item in indexed]
        except Exception:
            pass

    delta = points[1] - points[0]
    axis_order = sorted(range(3), key=lambda idx: abs(delta[idx]), reverse=True)
    points.sort(key=lambda point: tuple(point[idx] for idx in axis_order) + (point[0], point[1], point[2]))
    return points

def _create_snap_pair_in_memory(context, memory_obj, world_points, snap_group: str, snap_side: str, replace_existing: bool):
    mesh = memory_obj.data
    to_local = memory_obj.matrix_world.inverted()
    ordered_world_points = _sort_snap_pair_world_points(context, world_points)
    local_points = [to_local @ p for p in ordered_world_points]

    base_idx = len(mesh.vertices)
    mesh.vertices.add(2)
    mesh.vertices[base_idx + 0].co = local_points[0]
    mesh.vertices[base_idx + 1].co = local_points[1]
    mesh.update()

    created_names = []
    for i in range(2):
        vg_name = f".sp_{snap_group}_{snap_side}_{i}"
        if replace_existing:
            old = memory_obj.vertex_groups.get(vg_name)
            if old is not None:
                memory_obj.vertex_groups.remove(old)
        vg = memory_obj.vertex_groups.get(vg_name)
        if vg is None:
            vg = memory_obj.vertex_groups.new(name=vg_name)
        vg.add([base_idx + i], 1.0, "REPLACE")
        created_names.append(vg_name)
    return created_names

def _normalize_snap_p3d_name(value: str) -> str:
    return _sanitize_snap_p3d_name_value(value)

def _build_snap_name_base(p3d_name: str, pair_code: str, axis_token: str) -> str:
    axis = (axis_token or "X").strip().lower() or "x"
    return f"{p3d_name}{pair_code}{axis}"

def _build_snap_point_name(p3d_name: str, pair_code: str, axis_token: str, snap_side: str, point_index: int) -> str:
    base = _build_snap_name_base(p3d_name, pair_code, axis_token)
    return f".sp_{base}_{snap_side}_{point_index}"

def _create_named_snap_points_in_memory(memory_obj, world_points, point_names, replace_existing: bool):
    if memory_obj is None or memory_obj.type != "MESH":
        raise RuntimeError("Memory LOD Object must be a mesh")
    if len(world_points) != len(point_names):
        raise RuntimeError("Point and name count mismatch")

    mesh = memory_obj.data
    to_local = memory_obj.matrix_world.inverted()
    local_points = [(to_local @ point.copy()) for point in world_points]

    base_idx = len(mesh.vertices)
    mesh.vertices.add(len(local_points))
    for offset, local_point in enumerate(local_points):
        mesh.vertices[base_idx + offset].co = local_point
    mesh.update()

    created_names = []
    for offset, point_name in enumerate(point_names):
        if replace_existing:
            old = memory_obj.vertex_groups.get(point_name)
            if old is not None:
                memory_obj.vertex_groups.remove(old)

        vg = memory_obj.vertex_groups.get(point_name)
        if vg is None:
            vg = memory_obj.vertex_groups.new(name=point_name)
        vg.add([base_idx + offset], 1.0, "REPLACE")
        created_names.append(point_name)
    return created_names

class _SnapPointNamePattern:
    def __init__(self, p3d_name: str, pair_code: str, axis_token: str):
        self.p3d_name = p3d_name
        self.pair_code = pair_code
        self.axis_token = (axis_token or "X").strip().upper() or "X"

    @classmethod
    def from_settings(cls, settings):
        p3d_name = _normalize_snap_p3d_name(getattr(settings, "snap_p3d_name", "") or getattr(settings, "snap_group", ""))
        if not p3d_name:
            raise RuntimeError("P3D Name is empty")
        if not _SP_P3D_NAME_RE.fullmatch(p3d_name):
            raise RuntimeError("P3D Name must contain only letters and digits")

        pair_code = (getattr(settings, "snap_pair_code", "") or "").strip()
        if not pair_code:
            raise RuntimeError("ID is empty")
        if not _SP_PAIR_CODE_RE.fullmatch(pair_code):
            raise RuntimeError("ID must contain 1-3 letters or digits")
        return cls(p3d_name=p3d_name, pair_code=pair_code, axis_token=getattr(settings, "edge_axis", "X"))

    @classmethod
    def from_preview_settings(cls, settings):
        p3d_name = _normalize_snap_p3d_name(getattr(settings, "snap_p3d_name", "") or getattr(settings, "snap_group", "")) or "SampleName"
        if not _SP_P3D_NAME_RE.fullmatch(p3d_name):
            p3d_name = "SampleName"

        pair_code = (getattr(settings, "snap_pair_code", "") or "").strip() or "01"
        if not _SP_PAIR_CODE_RE.fullmatch(pair_code):
            pair_code = "01"
        return cls(p3d_name=p3d_name, pair_code=pair_code, axis_token=getattr(settings, "edge_axis", "X"))

    @property
    def preview_base(self) -> str:
        return _build_snap_name_base(self.p3d_name, self.pair_code, self.axis_token)

    def build_pair_names(self, snap_side: str):
        return [
            _build_snap_point_name(self.p3d_name, self.pair_code, self.axis_token, snap_side, point_index)
            for point_index in range(2)
        ]

class _SnapPointPairBuilder:
    _SIDE_LABELS = {
        "a": "Memory LOD (A)",
        "v": "Memory LOD (V)",
    }

    def __init__(self, context, settings):
        self.context = context
        self.settings = settings
        self.naming = _SnapPointNamePattern.from_settings(settings)

    def _require_mesh_object(self, obj, label: str):
        if obj is None or obj.type != "MESH" or obj.data is None:
            raise RuntimeError(f"{label} must be a mesh")
        return obj

    def resolve_memory_a(self):
        memory_obj = getattr(self.settings, "memory_object", None)
        if memory_obj is None:
            raise RuntimeError("Pick or create Memory LOD (A) first")
        return self._require_mesh_object(memory_obj, "Memory LOD (A)")

    def resolve_memory_v(self):
        memory_obj = getattr(self.settings, "paired_memory_object", None)
        if memory_obj is None:
            raise RuntimeError("Pick or create Memory LOD (V) first")
        return self._require_mesh_object(memory_obj, "Memory LOD (V)")

    def ensure_object_mode(self):
        if self.context.mode == "OBJECT":
            return
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception as e:
            raise RuntimeError(f"Failed to switch to Object Mode: {_fmt_exc(e)}")

    def collect_selected_points(self):
        edit_obj = getattr(self.context, "edit_object", None)
        if edit_obj is None or edit_obj.type != "MESH":
            raise RuntimeError("Select exactly 2 vertices in Edit Mode on any mesh")

        world_points = _collect_snap_pair_selected_world_points(edit_obj)
        if len(world_points) != 2:
            raise RuntimeError("Select exactly 2 vertices in Edit Mode")

        return _sort_snap_pair_world_points(self.context, world_points)

    def create_dual_model_set(self):
        world_points = self.collect_selected_points()
        memory_a = self.resolve_memory_a()
        memory_v = self.resolve_memory_v()
        if memory_a == memory_v:
            raise RuntimeError("Pick two different Memory LOD objects")

        self.ensure_object_mode()
        self.settings.snap_p3d_name = self.naming.p3d_name

        created_names = []
        targets = (
            ("a", memory_a),
            ("v", memory_v),
        )
        for side_token, memory_obj in targets:
            created_names.extend(
                _create_named_snap_points_in_memory(
                    memory_obj=memory_obj,
                    world_points=world_points,
                    point_names=self.naming.build_pair_names(side_token),
                    replace_existing=self.settings.replace_existing,
                )
            )
        return targets, created_names

def _collect_snap_pair_selected_world_points(source_obj):
    if source_obj is None or source_obj.type != "MESH" or source_obj.mode != "EDIT":
        return []

    bm = bmesh.from_edit_mesh(source_obj.data)
    selected = [source_obj.matrix_world @ vert.co for vert in bm.verts if vert.select]
    if not selected:
        return []
    return _dedupe_world_points(selected)

def _axis_index_from_token(token: str) -> int:
    t = (token or "").upper()
    if t == "X":
        return 0
    if t == "Y":
        return 1
    return 2

def _pick_span_axis_index(edge_axis_idx: int, span_token: str) -> int:
    t = (span_token or "AUTO").upper()
    if t == "AUTO":
        # For walls/segments AUTO uses horizontal perpendicular axis.
        return 1 if edge_axis_idx == 0 else 0
    idx = _axis_index_from_token(t)
    if idx == edge_axis_idx:
        return 2 if edge_axis_idx != 2 else 0
    return idx

def _auto_snap_points_from_model_edge(model_obj, edge_axis_token: str, edge_side_token: str,
                                      span_axis_token: str, edge_tolerance: float):
    if model_obj is None or model_obj.type != "MESH" or model_obj.data is None:
        raise RuntimeError("Model Object must be a mesh")
    if len(model_obj.data.vertices) < 2:
        raise RuntimeError("Model object must have at least 2 vertices")

    edge_axis = _axis_index_from_token(edge_axis_token)
    span_axis = _pick_span_axis_index(edge_axis, span_axis_token)
    verts_local = [v.co.copy() for v in model_obj.data.vertices]

    edge_values = [v[edge_axis] for v in verts_local]
    edge_min = min(edge_values)
    edge_max = max(edge_values)
    edge_range = edge_max - edge_min
    target_edge = edge_min if (edge_side_token or "POS").upper() == "NEG" else edge_max

    tol_abs = max(1e-6, edge_range * max(0.0, edge_tolerance))
    candidates = [v for v in verts_local if abs(v[edge_axis] - target_edge) <= tol_abs]
    if len(candidates) < 2:
        sorted_by_edge = sorted(verts_local, key=lambda v: abs(v[edge_axis] - target_edge))
        candidates = sorted_by_edge[:max(2, len(sorted_by_edge))]

    if len(candidates) < 2:
        raise RuntimeError("Could not detect enough vertices on selected edge")

    v0 = min(candidates, key=lambda v: v[span_axis])
    v1 = max(candidates, key=lambda v: v[span_axis])
    if (v0 - v1).length_squared < 1e-12:
        farthest = None
        best_d2 = -1.0
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                d2 = (candidates[i] - candidates[j]).length_squared
                if d2 > best_d2:
                    best_d2 = d2
                    farthest = (candidates[i], candidates[j])
        if farthest is None:
            raise RuntimeError("Failed to determine distinct edge points")
        v0, v1 = farthest

    return [model_obj.matrix_world @ v0, model_obj.matrix_world @ v1]

def _pick_model_mesh_from_objects(objs):
    meshes = [o for o in objs if o is not None and o.type == "MESH" and o.data is not None]
    if not meshes:
        return None

    for o in meshes:
        if (o.name or "").strip().lower() == "resolution 0":
            return o

    non_memory = [o for o in meshes if not _is_memory_lod_mesh_object(o)]
    if non_memory:
        return max(non_memory, key=lambda o: len(o.data.polygons) if o.data else 0)

    return max(meshes, key=lambda o: len(o.data.polygons) if o.data else 0)

def _pick_memory_mesh_from_objects(objs):
    for o in objs:
        if _is_memory_lod_mesh_object(o):
            return o
    return None

def _deselect_all_in_view_layer(context):
    for o in context.view_layer.objects:
        if o.select_get():
            o.select_set(False)

def _cleanup_imported_objects(imported_obj_names, pre_collection_ptrs):
    live = [bpy.data.objects.get(n) for n in imported_obj_names]
    live = [o for o in live if o is not None]
    live.sort(key=_obj_depth, reverse=True)
    for obj in live:
        if bpy.data.objects.get(obj.name) is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    for col in list(bpy.data.collections):
        if col.as_pointer() in pre_collection_ptrs:
            continue
        if len(col.objects) != 0 or len(col.children) != 0:
            continue
        try:
            bpy.data.collections.remove(col)
        except Exception:
            pass

def _is_p3d_root_collection_name(name: str) -> bool:
    base = _strip_blender_numeric_suffix((name or "").strip())
    return base.lower().endswith(".p3d")

def _iter_p3d_root_collections(scene):
    if scene is None or scene.collection is None:
        return []

    roots = []
    for col in _collect_collections_deep(scene.collection):
        if col is None or col == scene.collection:
            continue
        if _is_p3d_root_collection_name(col.name):
            roots.append(col)
    return roots

class CRAY_OT_EnsureMemoryLOD(Operator):
    bl_idname = "cray.ensure_memory_lod"
    bl_label = "Create/Find Memory LODs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ss = context.scene.cray_snap_settings
        root_collections = _iter_p3d_root_collections(context.scene)
        if not root_collections:
            self.report({"ERROR"}, "No .p3d collections found in the scene")
            return {"CANCELLED"}

        prepared = []
        for root_col in root_collections:
            memory_obj = _MemoryLodManager(context, parent_collection=root_col).ensure_object()
            prepared.append(f"{root_col.name} -> {memory_obj.name}")

        preview = ", ".join(prepared[:3])
        if len(prepared) > 3:
            preview = f"{preview}, ..."
        self.report({"INFO"}, f"Prepared {len(prepared)} Memory LODs: {preview}")
        return {"FINISHED"}

class CRAY_OT_CreateSnapPairFromModelEdge(Operator):
    bl_idname = "cray.create_snap_pair_from_model_edge"
    bl_label = "Create Snap Points"
    bl_description = (
        "Copy 2 selected vertices from the active Edit Mode mesh into both chosen Memory LODs"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ss = context.scene.cray_snap_settings
        try:
            targets, created_names = _SnapPointPairBuilder(context, ss).create_dual_model_set()
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        target_names = ", ".join(f"{side.upper()}: {memory_obj.name}" for side, memory_obj in targets)
        self.report(
            {"INFO"},
            (
                f"Created {len(created_names)} snap points in {target_names}: "
                f"{', '.join(created_names)}"
            ),
        )
        return {"FINISHED"}

class CRAY_OT_SnapBatchProcess(Operator):
    bl_idname = "cray.snap_batch_process"
    bl_label = "Batch Process P3D (Backup + Snap)"
    bl_options = {"REGISTER"}

    filter_glob: StringProperty(default="*.p3d", options={"HIDDEN"})
    directory: StringProperty(subtype="DIR_PATH")
    files: CollectionProperty(type=OperatorFileListElement)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        ss = context.scene.cray_snap_settings

        if not _has_any_a3ob_io_ops():
            self.report({"ERROR"}, "A3OB import/export operators not found")
            return {"CANCELLED"}

        snap_group = (ss.snap_group or "").strip()
        if not snap_group:
            self.report({"ERROR"}, "Snap Group is empty")
            return {"CANCELLED"}
        if not _SP_GROUP_RE.fullmatch(snap_group):
            self.report({"ERROR"}, "Snap Group must contain only letters, digits and underscores")
            return {"CANCELLED"}

        paths = []
        for item in self.files:
            p = os.path.join(self.directory, item.name)
            paths.append(bpy.path.abspath(p))
        if not paths:
            self.report({"ERROR"}, "No files selected")
            return {"CANCELLED"}

        prev_selected_names = [o.name for o in context.selected_objects]
        prev_active_name = context.view_layer.objects.active.name if context.view_layer.objects.active else None

        ok_count = 0
        fail_count = 0
        backup_count = 0
        exported_count = 0
        failures = []

        for filepath in paths:
            if not os.path.isfile(filepath):
                fail_count += 1
                failures.append((filepath, "file-not-found"))
                continue

            bak_path = filepath + ".bak"
            try:
                if os.path.exists(bak_path) and not ss.batch_overwrite_bak:
                    bak_path = filepath + ".bak.prev"
                shutil.copy2(filepath, bak_path)
                backup_count += 1
            except Exception as e:
                fail_count += 1
                failures.append((filepath, f"backup-failed: {_fmt_exc(e)}"))
                continue

            pre_obj_ptrs = {o.as_pointer() for o in bpy.data.objects}
            pre_col_ptrs = {c.as_pointer() for c in bpy.data.collections}

            with _suppress_a3ob_import_tracking():
                _, used_import, import_err = _call_first_available(
                    _A3OB_IMPORT_CANDIDATES,
                    filepath=filepath,
                    first_lod_only=False,
                    absolute_paths=True,
                    enclose=True,
                    groupby="TYPE",
                    additional_data_allowed=True,
                    additional_data={"PROPS", "SELECTIONS"},
                    validate_meshes=False,
                    proxy_action="SEPARATE",
                    translate_selections=False,
                    cleanup_empty_selections=False,
                    load_textures=False,
                )
            if used_import is None:
                fail_count += 1
                failures.append((filepath, f"import-failed: {_fmt_exc(import_err) if import_err else 'no operator'}"))
                continue

            imported_objs = [o for o in bpy.data.objects if o.as_pointer() not in pre_obj_ptrs]
            imported_names = [o.name for o in imported_objs]
            if not imported_objs:
                fail_count += 1
                failures.append((filepath, "import-produced-no-objects"))
                continue

            model_obj = _pick_model_mesh_from_objects(imported_objs)
            if model_obj is None:
                fail_count += 1
                failures.append((filepath, "no-mesh-model-found"))
                if ss.batch_cleanup_imported:
                    _cleanup_imported_objects(imported_names, pre_col_ptrs)
                continue

            memory_obj = _pick_memory_mesh_from_objects(imported_objs)
            memory_obj = _ensure_memory_lod_object(context, model_obj, preferred_obj=memory_obj)

            try:
                world_points = _auto_snap_points_from_model_edge(
                    model_obj=model_obj,
                    edge_axis_token=ss.edge_axis,
                    edge_side_token=ss.edge_side,
                    span_axis_token=ss.edge_span_axis,
                    edge_tolerance=ss.edge_tolerance,
                )
                _create_snap_pair_in_memory(
                    context=context,
                    memory_obj=memory_obj,
                    world_points=world_points,
                    snap_group=snap_group,
                    snap_side=ss.snap_side,
                    replace_existing=ss.replace_existing,
                )
            except Exception as e:
                fail_count += 1
                failures.append((filepath, f"snap-failed: {_fmt_exc(e)}"))
                if ss.batch_cleanup_imported:
                    _cleanup_imported_objects(imported_names, pre_col_ptrs)
                continue

            _deselect_all_in_view_layer(context)
            for name in imported_names:
                live = bpy.data.objects.get(name)
                if live is None:
                    continue
                try:
                    live.hide_set(False)
                except Exception:
                    pass
                try:
                    live.hide_viewport = False
                except Exception:
                    pass
                live.select_set(True)
            if bpy.data.objects.get(model_obj.name) is not None:
                context.view_layer.objects.active = bpy.data.objects.get(model_obj.name)

            _, used_export, export_err = _call_first_available(
                _A3OB_EXPORT_CANDIDATES,
                filepath=filepath,
                use_selection=True,
                visible_only=True,
                relative_paths=True,
                preserve_normals=True,
                validate_meshes=False,
                apply_transforms=True,
                apply_modifiers=True,
                sort_sections=True,
                lod_collisions="SKIP",
                validate_lods=False,
                generate_components=True,
                force_lowercase=True,
            )
            if used_export is None:
                fail_count += 1
                failures.append((filepath, f"export-failed: {_fmt_exc(export_err) if export_err else 'no operator'}"))
            else:
                ok_count += 1
                exported_count += 1

            if ss.batch_cleanup_imported:
                _cleanup_imported_objects(imported_names, pre_col_ptrs)

        _deselect_all_in_view_layer(context)
        for name in prev_selected_names:
            o = bpy.data.objects.get(name)
            if o is not None:
                o.select_set(True)
        if prev_active_name and bpy.data.objects.get(prev_active_name) is not None:
            context.view_layer.objects.active = bpy.data.objects.get(prev_active_name)

        if failures:
            print("=== Batch Snap Process Failures ===")
            for path, reason in failures:
                print(f"{path} :: {reason}")

        msg = f"Batch done: ok {ok_count}/{len(paths)}, exported {exported_count}, backups {backup_count}, failed {fail_count}"
        if fail_count > 0:
            self.report({"WARNING"}, msg + " (see System Console)")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


# ------------------------------------------------------------------------
#  Collider helper tools for Geometry LOD
# ------------------------------------------------------------------------

def _collider_lod_name(lod_token: str) -> str:
    return _COLLIDER_KNOWN_LOD_NAMES.get(str(lod_token), f"LOD {lod_token}")


def _is_collider_lod_mesh_object(obj, lod_token=None) -> bool:
    if obj is None or obj.type != "MESH":
        return False

    expected = str(lod_token) if lod_token is not None else None
    if expected is not None and obj.name == _collider_lod_name(expected):
        return True
    if expected is None and obj.name in _COLLIDER_LOD_NAMES.values():
        return True

    if not hasattr(obj, "a3ob_properties_object"):
        return False

    try:
        value = str(getattr(obj.a3ob_properties_object, "lod", ""))
    except Exception:
        return False

    if expected is None:
        return value in _COLLIDER_LOD_NAMES
    return value == expected


def _object_in_logical_collection(obj, collection_name: str) -> bool:
    if obj is None:
        return False

    wanted_names = _logical_collection_names(collection_name)
    if collection_name == _COLLIDER_COLLECTION_NAME:
        wanted_names.update(_logical_collection_names(_COLLIDER_COLLECTION_ALIASES))
    for col in getattr(obj, "users_collection", []):
        if _logical_collection_name(getattr(col, "name", "")) in wanted_names:
            return True
    return False


def _is_auto_reusable_collider_target(obj, lod_token=None) -> bool:
    if obj is None or obj.type != "MESH":
        return False

    if _is_collider_lod_mesh_object(obj, lod_token=lod_token):
        return True

    return _object_in_logical_collection(obj, _COLLIDER_COLLECTION_NAME)


def _pick_collider_lod_object(context, source_obj, lod_token):
    expected_name = _collider_lod_name(lod_token)

    if source_obj is not None:
        for col in source_obj.users_collection:
            obj = col.objects.get(expected_name)
            if _is_auto_reusable_collider_target(obj, lod_token=lod_token):
                return obj

    obj = bpy.data.objects.get(expected_name)
    if _is_auto_reusable_collider_target(obj, lod_token=lod_token):
        return obj

    for obj in context.scene.objects:
        if _is_collider_lod_mesh_object(obj, lod_token=lod_token):
            return obj

    return None


def _find_parent_collection(root_collection, target_collection):
    if root_collection is None or target_collection is None:
        return None

    for child in root_collection.children:
        if child == target_collection:
            return root_collection
        found = _find_parent_collection(child, target_collection)
        if found is not None:
            return found
    return None


def _logical_collection_name(name: str) -> str:
    return re.sub(r"\.\d{3}$", "", (name or "").strip().lower())


def _logical_collection_names(*names) -> set:
    result = set()
    for name in names:
        if not name:
            continue
        if isinstance(name, (tuple, list, set)):
            result.update(_logical_collection_names(*name))
            continue
        result.add(_logical_collection_name(name))
    return result


def _preferred_collider_parent_collection(context, source_obj):
    source_col = None
    if source_obj is not None and source_obj.users_collection:
        source_col = source_obj.users_collection[0]
    if source_col is None:
        return context.scene.collection

    parent = _find_parent_collection(context.scene.collection, source_col)
    if parent is None:
        return source_col

    logical_group_names = {
        "visuals",
        "shadows",
        "geometry",
        "geometries",
        "point clouds",
        "misc",
    }
    if _logical_collection_name(source_col.name) in logical_group_names:
        return parent
    return source_col


def _ensure_collider_collection(context, source_obj):
    parent = _preferred_collider_parent_collection(context, source_obj)
    if parent is None:
        parent = context.scene.collection

    return _ensure_named_child_collection(
        parent,
        _COLLIDER_COLLECTION_NAME,
        _COLLIDER_COLLECTION_COLOR,
        aliases=_COLLIDER_COLLECTION_ALIASES,
    )


def _ensure_named_child_collection(parent_collection, collection_name, color_tag=None, aliases=()):
    if parent_collection is None:
        return None

    target = parent_collection.children.get(collection_name)
    logical_names = _logical_collection_names(collection_name, aliases)
    if target is None:
        for child in parent_collection.children:
            if _logical_collection_name(child.name) in logical_names:
                target = child
                break
    if target is None:
        target = bpy.data.collections.new(collection_name)
        parent_collection.children.link(target)
    elif _logical_collection_name(target.name) != _logical_collection_name(collection_name):
        try:
            target.name = collection_name
        except Exception:
            pass

    if color_tag:
        try:
            target.color_tag = color_tag
        except Exception:
            pass

    return target


def _ensure_memory_collection(context, source_obj):
    parent = _preferred_collider_parent_collection(context, source_obj)
    if parent is None:
        parent = context.scene.collection
    return _ensure_named_child_collection(
        parent,
        _MEMORY_COLLECTION_NAME,
        _MEMORY_COLLECTION_COLOR,
        aliases=_MEMORY_COLLECTION_ALIASES,
    )


def _ensure_misc_collection(context, source_obj):
    parent = _preferred_collider_parent_collection(context, source_obj)
    if parent is None:
        parent = context.scene.collection
    return _ensure_named_child_collection(parent, _MISC_COLLECTION_NAME, _MISC_COLLECTION_COLOR)


def _pick_named_lod_object(context, source_obj, lod_token, object_name):
    if source_obj is not None:
        for col in source_obj.users_collection:
            obj = col.objects.get(object_name)
            if obj is not None and obj.type == "MESH":
                return obj

    obj = bpy.data.objects.get(object_name)
    if obj is not None and obj.type == "MESH":
        return obj

    for obj in context.scene.objects:
        if _is_collider_lod_mesh_object(obj, lod_token=lod_token):
            return obj

    return None


def _remove_a3ob_named_property(props, name: str):
    items = getattr(props, "properties", None)
    if items is None:
        return

    remove_indices = []
    for idx, item in enumerate(items):
        if (getattr(item, "name", "") or "").strip().lower() == name.lower():
            remove_indices.append(idx)

    for idx in reversed(remove_indices):
        try:
            items.remove(idx)
        except Exception:
            pass


def _set_collider_lod_a3ob_props(target_obj, lod_token):
    if not hasattr(target_obj, "a3ob_properties_object"):
        return

    try:
        props = target_obj.a3ob_properties_object
        props.lod = str(lod_token)
        props.resolution = 1
        props.resolution_float = 1.0
        props.is_a3_lod = True
        _remove_a3ob_named_property(props, "autocenter")
        lod_name = props.get_name() if hasattr(props, "get_name") else _collider_lod_name(lod_token)
        target_obj.name = lod_name
        if target_obj.data is not None:
            target_obj.data.name = lod_name
    except Exception:
        pass


def _collider_target_validation_error(target_obj, lod_token, source_obj=None, allow_same_source=False):
    if target_obj is None:
        return None
    if target_obj.type != "MESH":
        return "Target LOD Object must be a mesh"
    if source_obj is not None and target_obj == source_obj and not allow_same_source:
        if not _is_collider_lod_mesh_object(target_obj, lod_token=lod_token):
            return "Target LOD Object must be separate from the Source Object"
    if not hasattr(target_obj, "a3ob_properties_object"):
        return None

    try:
        props = target_obj.a3ob_properties_object
        if not bool(getattr(props, "is_a3_lod", False)):
            return None
        current_lod = str(getattr(props, "lod", ""))
    except Exception:
        return None

    if current_lod and current_lod != str(lod_token):
        return (
            f"Target LOD Object '{target_obj.name}' is already "
            f"A3OB LOD '{_collider_lod_name(current_lod)}'"
        )
    return None


def _tag_redraw_all_areas(context):
    screen = getattr(getattr(context, "window", None), "screen", None) or getattr(context, "screen", None)
    if screen is None:
        return

    for area in getattr(screen, "areas", []):
        try:
            for region in area.regions:
                region.tag_redraw()
        except Exception:
            pass
        try:
            area.tag_redraw()
        except Exception:
            pass


def _set_collider_settings_object(context, attr_name, obj):
    cs = getattr(getattr(context, "scene", None), "cray_collider_settings", None)
    if cs is None or not hasattr(cs, attr_name):
        return

    current = getattr(cs, attr_name, None)
    try:
        if current == obj:
            setattr(cs, attr_name, None)
        setattr(cs, attr_name, obj)
    except Exception:
        pass

    try:
        context.view_layer.update()
    except Exception:
        pass
    _tag_redraw_all_areas(context)


def _sync_roadway_material_selection(context, preferred_name=""):
    cs = getattr(getattr(context, "scene", None), "cray_collider_settings", None)
    if cs is None:
        return

    items = get_roadway_material_enum_items(None, context)
    valid_values = [item[0] for item in items if item and item[0] != _ROADWAY_MATERIAL_NONE]
    current = (getattr(cs, "roadway_material", "") or "").strip()
    preferred_name = (preferred_name or "").strip()

    if preferred_name and preferred_name in valid_values:
        chosen = preferred_name
    elif current and current in valid_values:
        chosen = current
    elif valid_values:
        chosen = valid_values[0]
    else:
        chosen = _ROADWAY_MATERIAL_NONE

    try:
        cs.roadway_material = chosen
    except Exception:
        pass

    _tag_redraw_all_areas(context)


def _get_selected_roadway_material(context):
    cs = getattr(getattr(context, "scene", None), "cray_collider_settings", None)
    if cs is None:
        return None

    roadway_obj = getattr(cs, "roadway_object", None)
    if roadway_obj is None or roadway_obj.type != "MESH":
        return None

    selected_name = (getattr(cs, "roadway_material", "") or "").strip()
    fallback_mat = None

    for slot in roadway_obj.material_slots:
        mat = slot.material
        if mat is None:
            continue
        if fallback_mat is None:
            fallback_mat = mat
        if selected_name and mat.name == selected_name:
            return mat

    return fallback_mat


def _apply_collider_visual_style(target_obj):
    _apply_object_visual_style(target_obj, _COLLIDER_OBJECT_COLOR)


def _apply_object_visual_style(target_obj, color):
    if target_obj is None:
        return

    try:
        target_obj.color = color
    except Exception:
        pass
    try:
        target_obj.show_wire = True
    except Exception:
        pass


def _ensure_roadway_lod_object(context, source_obj, preferred_obj=None):
    if preferred_obj is not None and preferred_obj.type == "MESH":
        target_obj = preferred_obj
    else:
        target_obj = _pick_named_lod_object(
            context,
            source_obj,
            _ROADWAY_LOD_TOKEN,
            _collider_lod_name(_ROADWAY_LOD_TOKEN),
        )

    misc_collection = _ensure_misc_collection(context, source_obj)

    if target_obj is None:
        obj_name = _collider_lod_name(_ROADWAY_LOD_TOKEN)
        mesh = bpy.data.meshes.new(obj_name)
        target_obj = bpy.data.objects.new(obj_name, mesh)
        misc_collection.objects.link(target_obj)
        if source_obj is not None:
            target_obj.matrix_world = source_obj.matrix_world.copy()
    else:
        _move_object_to_collection(target_obj, misc_collection)

    _set_collider_lod_a3ob_props(target_obj, _ROADWAY_LOD_TOKEN)
    _apply_object_visual_style(target_obj, _ROADWAY_OBJECT_COLOR)
    _enable_collider_object_color_preview(context)
    return target_obj
    try:
        target_obj.show_all_edges = True
    except Exception:
        pass
    try:
        target_obj.show_name = True
    except Exception:
        pass


def _enable_collider_object_color_preview(context):
    area = getattr(context, "area", None)
    space = getattr(context, "space_data", None)
    if area is None or area.type != "VIEW_3D" or space is None:
        return

    shading = getattr(space, "shading", None)
    if shading is None:
        return

    try:
        shading.color_type = "OBJECT"
    except Exception:
        pass


def _ensure_collider_lod_object(context, source_obj, lod_token, preferred_obj=None):
    if preferred_obj is not None and preferred_obj.type == "MESH":
        target_obj = preferred_obj
    else:
        target_obj = _pick_collider_lod_object(context, source_obj, lod_token)

    collider_collection = _ensure_collider_collection(context, source_obj)

    if target_obj is None:
        obj_name = _collider_lod_name(lod_token)
        mesh = bpy.data.meshes.new(obj_name)
        target_obj = bpy.data.objects.new(obj_name, mesh)
        collider_collection.objects.link(target_obj)
        if source_obj is not None:
            target_obj.matrix_world = source_obj.matrix_world.copy()
    else:
        _move_object_to_collection(target_obj, collider_collection)

    _set_collider_lod_a3ob_props(target_obj, lod_token)
    _apply_collider_visual_style(target_obj)
    _enable_collider_object_color_preview(context)
    return target_obj


def _resolve_collider_source_object(context, preferred_obj=None):
    active = context.view_layer.objects.active
    if active is not None and active.type == "MESH":
        return active
    if preferred_obj is not None and preferred_obj.type == "MESH":
        return preferred_obj
    return None


def _resolve_collider_selection_source_object(context, preferred_obj=None):
    active = context.view_layer.objects.active
    if active is not None and active.type == "MESH" and active.mode == "EDIT":
        return active
    if preferred_obj is not None and preferred_obj.type == "MESH" and preferred_obj.mode == "EDIT":
        return preferred_obj
    return _resolve_collider_source_object(context, preferred_obj)


def _collect_selected_vertex_world_points(source_obj):
    if source_obj is None or source_obj.type != "MESH" or source_obj.mode != "EDIT":
        raise RuntimeError("Source object must be the active mesh in Edit Mode")

    bm = bmesh.from_edit_mesh(source_obj.data)
    selected = [source_obj.matrix_world @ vert.co for vert in bm.verts if vert.select]
    if not selected:
        raise RuntimeError("Select at least one vertex on the source mesh")
    return _dedupe_world_points(selected)


def _world_normal_from_selected_faces(source_obj, selected_faces):
    normal = Vector((0.0, 0.0, 0.0))
    for face in selected_faces:
        if len(face.verts) < 3:
            continue
        p0 = source_obj.matrix_world @ face.verts[0].co
        p1 = source_obj.matrix_world @ face.verts[1].co
        p2 = source_obj.matrix_world @ face.verts[2].co
        cross = (p1 - p0).cross(p2 - p0)
        if cross.length_squared > 1e-12:
            normal += cross

    if normal.length_squared <= 1e-12:
        return None

    normal.normalize()
    return normal


def _estimate_world_points_normal(points):
    if len(points) < 3:
        return None

    origin = points[0]
    farthest = None
    farthest_d2 = 0.0
    for point in points[1:]:
        d2 = (point - origin).length_squared
        if d2 > farthest_d2:
            farthest_d2 = d2
            farthest = point

    if farthest is None or farthest_d2 <= 1e-12:
        return None

    axis = farthest - origin
    best_normal = None
    best_d2 = 0.0
    for point in points[1:]:
        cross = axis.cross(point - origin)
        d2 = cross.length_squared
        if d2 > best_d2:
            best_d2 = d2
            best_normal = cross

    if best_normal is None or best_d2 <= 1e-12:
        return None

    best_normal.normalize()
    return best_normal


def _dedupe_world_points(points, tolerance=1e-6):
    if tolerance <= 0.0:
        return [p.copy() for p in points]

    scale = 1.0 / tolerance
    unique = []
    seen = set()
    for point in points:
        key = (
            round(point.x * scale),
            round(point.y * scale),
            round(point.z * scale),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(point.copy())
    return unique


def _vector_quantized_key(vec, tolerance=1e-6):
    tol = max(float(tolerance), 1e-12)
    scale = 1.0 / tol
    return (
        round(vec.x * scale),
        round(vec.y * scale),
        round(vec.z * scale),
    )


def _collect_selected_collider_input(source_obj, loose_only=False):
    if source_obj is None or source_obj.type != "MESH" or source_obj.mode != "EDIT":
        raise RuntimeError("Source object must be the active mesh in Edit Mode")

    bm = bmesh.from_edit_mesh(source_obj.data)
    selected_faces = [face for face in bm.faces if face.select]
    selected_edges = [edge for edge in bm.edges if edge.select]

    selected_verts = {vert for vert in bm.verts if vert.select}
    for edge in selected_edges:
        selected_verts.update(edge.verts)
    for face in selected_faces:
        selected_verts.update(face.verts)

    selected_verts = list(selected_verts)
    if loose_only:
        selected_verts = [
            vert for vert in selected_verts
            if len(vert.link_edges) == 0 and len(vert.link_faces) == 0
        ]

    if not selected_verts:
        if loose_only:
            raise RuntimeError("No isolated selected vertices found")
        raise RuntimeError("Select vertices, edges or faces on the source mesh")

    world_points = _dedupe_world_points([source_obj.matrix_world @ vert.co for vert in selected_verts])
    local_points = [vert.co.copy() for vert in selected_verts]

    normal = _world_normal_from_selected_faces(source_obj, selected_faces)
    if normal is None:
        normal = _estimate_world_points_normal(world_points)

    return {
        "world_points": world_points,
        "local_points": local_points,
        "normal": normal,
        "face_count": len(selected_faces),
        "vert_count": len(selected_verts),
    }


def _points_are_flat(points, normal, epsilon=1e-5):
    if len(points) < 4 or normal is None or normal.length_squared <= 1e-12:
        return False

    origin = points[0]
    max_dist = 0.0
    for point in points[1:]:
        max_dist = max(max_dist, abs((point - origin).dot(normal)))
    return max_dist <= epsilon


def _extrude_points_along_normal(points, normal, thickness):
    if thickness <= 0.0:
        raise RuntimeError("Thickness must be greater than zero")
    if normal is None or normal.length_squared <= 1e-12:
        raise RuntimeError("Could not determine a stable normal for the current selection")

    n = normal.normalized()
    half = thickness * 0.5
    out = []
    for point in points:
        out.append(point + n * half)
        out.append(point - n * half)
    return _dedupe_world_points(out)


def _world_corners_from_local_bounds(source_obj, local_points, padding=0.0, min_axis_size=0.0):
    if not local_points:
        raise RuntimeError("No points available to build bounds")

    min_v = Vector((
        min(point.x for point in local_points),
        min(point.y for point in local_points),
        min(point.z for point in local_points),
    ))
    max_v = Vector((
        max(point.x for point in local_points),
        max(point.y for point in local_points),
        max(point.z for point in local_points),
    ))

    if padding > 0.0:
        pad = Vector((padding, padding, padding))
        min_v -= pad
        max_v += pad

    if min_axis_size > 0.0:
        for axis in range(3):
            if abs(max_v[axis] - min_v[axis]) >= 1e-6:
                continue
            expand = min_axis_size * 0.5
            min_v[axis] -= expand
            max_v[axis] += expand

    corners = []
    for x in (min_v.x, max_v.x):
        for y in (min_v.y, max_v.y):
            for z in (min_v.z, max_v.z):
                corners.append(source_obj.matrix_world @ Vector((x, y, z)))
    return corners


def _delete_bmesh_geom(bm, geom_items):
    unique_items = []
    seen = set()
    for item in geom_items:
        key = id(item)
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(item)

    verts = [item for item in unique_items if isinstance(item, bmesh.types.BMVert) and item.is_valid]
    edges = [item for item in unique_items if isinstance(item, bmesh.types.BMEdge) and item.is_valid]
    faces = [item for item in unique_items if isinstance(item, bmesh.types.BMFace) and item.is_valid]

    if faces:
        bmesh.ops.delete(bm, geom=faces, context="FACES")
    if edges:
        bmesh.ops.delete(bm, geom=edges, context="EDGES")
    if verts:
        bmesh.ops.delete(bm, geom=verts, context="VERTS")


def _finalize_convex_hull_geometry(bm, hull_result, seed_verts, recalc_normals=True):
    created_faces = [
        item for item in hull_result.get("geom", [])
        if isinstance(item, bmesh.types.BMFace) and item.is_valid
    ]

    cleanup = []
    cleanup.extend(hull_result.get("geom_unused", []))
    cleanup.extend(hull_result.get("geom_interior", []))
    if cleanup:
        _delete_bmesh_geom(bm, cleanup)

    affected_verts = {vert for vert in seed_verts if vert.is_valid}
    for face in created_faces:
        if not face.is_valid:
            continue
        for vert in face.verts:
            if vert.is_valid:
                affected_verts.add(vert)

    created_face_set = {face for face in created_faces if face.is_valid}
    affected_edges = {
        edge
        for face in created_face_set
        for edge in face.edges
        if edge.is_valid
        and all(link_face in created_face_set for link_face in edge.link_faces if link_face.is_valid)
    }

    if affected_edges:
        bmesh.ops.dissolve_limit(
            bm,
            angle_limit=1e-5,
            use_dissolve_boundaries=True,
            verts=[vert for vert in affected_verts if vert.is_valid],
            edges=[edge for edge in affected_edges if edge.is_valid],
            delimit=set(),
        )

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    loose_edges = [edge for edge in affected_edges if edge.is_valid and len(edge.link_faces) == 0]
    if loose_edges:
        bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")

    loose_verts = [
        vert for vert in affected_verts
        if vert.is_valid and len(vert.link_edges) == 0 and len(vert.link_faces) == 0
    ]
    if loose_verts:
        bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")

    final_faces = {
        face
        for vert in affected_verts
        if vert.is_valid
        for face in vert.link_faces
        if face.is_valid
    }
    if not final_faces:
        raise RuntimeError("Convex hull did not create faces (selection may be too flat or degenerate)")

    final_faces = list(final_faces)
    if recalc_normals:
        bmesh.ops.recalc_face_normals(bm, faces=final_faces)

    return final_faces


def _select_only_faces_in_bmesh(bm, faces):
    face_set = {face for face in faces if face is not None and face.is_valid}
    for face in bm.faces:
        face.select = False
    for edge in bm.edges:
        edge.select = False
    for vert in bm.verts:
        vert.select = False
    for face in face_set:
        face.select = True
    bm.select_flush_mode()


def _build_clean_hull_data_from_local_points(local_points, merge_distance=0.0, recalc_normals=True):
    unique_points = []
    seen = set()
    for point in local_points:
        key = _vector_quantized_key(point)
        if key in seen:
            continue
        seen.add(key)
        unique_points.append(point.copy())

    if len(unique_points) < 4:
        raise RuntimeError("Selected vertices collapse below 4 unique points")

    bm = bmesh.new()
    try:
        seed_verts = [bm.verts.new(point) for point in unique_points]
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        if merge_distance > 0.0 and seed_verts:
            bmesh.ops.remove_doubles(bm, verts=seed_verts, dist=merge_distance)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
            seed_verts = [vert for vert in seed_verts if vert.is_valid]

        unique_point_keys = {_vector_quantized_key(vert.co) for vert in seed_verts if vert.is_valid}
        if len(unique_point_keys) < 4:
            raise RuntimeError("Selected vertices collapse below 4 unique points")

        hull = bmesh.ops.convex_hull(bm, input=seed_verts, use_existing_faces=False)
        final_faces = _finalize_convex_hull_geometry(
            bm,
            hull,
            seed_verts,
            recalc_normals=recalc_normals,
        )

        used_verts = []
        used_vert_ids = set()
        for face in final_faces:
            if face is None or not face.is_valid:
                continue
            for vert in face.verts:
                if vert is None or not vert.is_valid:
                    continue
                key = id(vert)
                if key in used_vert_ids:
                    continue
                used_vert_ids.add(key)
                used_verts.append(vert)

        if len(used_verts) < 4:
            raise RuntimeError("Convex hull did not keep enough vertices to build a clean result")

        vert_index_by_id = {id(vert): idx for idx, vert in enumerate(used_verts)}
        face_indices = []
        for face in final_faces:
            if face is None or not face.is_valid or len(face.verts) < 3:
                continue
            indices = [vert_index_by_id[id(vert)] for vert in face.verts if vert is not None and vert.is_valid]
            if len(indices) >= 3:
                face_indices.append(indices)

        if not face_indices:
            raise RuntimeError("Convex hull did not create faces (selection may be too flat or degenerate)")

        return {
            "verts": [vert.co.copy() for vert in used_verts],
            "faces": face_indices,
            "used_verts": len(unique_point_keys),
        }
    finally:
        bm.free()


def _replace_selection_with_clean_hull_in_edit_object(
    context,
    target_obj,
    hull_data,
    selected_geom,
    recalc_normals=True,
):
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target object must be a mesh")
    if target_obj.mode != "EDIT":
        raise RuntimeError("Target object must be in Edit Mode")

    mesh = target_obj.data
    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    geom_to_delete = []
    geom_to_delete.extend(selected_geom.get("faces", []))
    geom_to_delete.extend(selected_geom.get("edges", []))
    geom_to_delete.extend(selected_geom.get("verts", []))
    if geom_to_delete:
        _delete_bmesh_geom(bm, geom_to_delete)

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    new_verts = [bm.verts.new(point) for point in hull_data["verts"]]
    bm.verts.ensure_lookup_table()

    new_faces = []
    for face_indices in hull_data["faces"]:
        face_verts = [new_verts[idx] for idx in face_indices if 0 <= idx < len(new_verts)]
        if len(face_verts) < 3 or len(set(face_verts)) < 3:
            continue
        try:
            face = bm.faces.new(face_verts)
        except ValueError:
            continue
        new_faces.append(face)

    if not new_faces:
        raise RuntimeError("Could not write clean convex hull back to the mesh")

    if recalc_normals:
        bmesh.ops.recalc_face_normals(bm, faces=new_faces)

    _select_only_faces_in_bmesh(bm, new_faces)
    bm.normal_update()
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=True)
    if context.mode == "EDIT_MESH":
        try:
            bpy.ops.mesh.select_mode(type="FACE")
        except Exception:
            pass

    return {
        "verts_added": len(new_verts),
        "faces_added": len(new_faces),
    }


def _append_collider_hull_to_object(target_obj, world_points, merge_distance=0.0, recalc_normals=True):
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target Geometry LOD object must be a mesh")
    if target_obj.mode == "EDIT":
        raise RuntimeError("Target Geometry LOD must not be in Edit Mode")

    unique_points = _dedupe_world_points(world_points)
    if len(unique_points) < 4:
        raise RuntimeError("Need at least 4 unique points to build a collider")

    mesh = target_obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        before_vert_count = len(bm.verts)
        before_face_count = len(bm.faces)

        to_local = target_obj.matrix_world.inverted_safe()
        local_points = [to_local @ point for point in unique_points]
        hull_data = _build_clean_hull_data_from_local_points(
            local_points,
            merge_distance=merge_distance,
            recalc_normals=recalc_normals,
        )

        new_verts = [bm.verts.new(point) for point in hull_data["verts"]]
        bm.verts.ensure_lookup_table()

        new_faces = []
        for face_indices in hull_data["faces"]:
            face_verts = [new_verts[idx] for idx in face_indices if 0 <= idx < len(new_verts)]
            if len(face_verts) < 3 or len(set(face_verts)) < 3:
                continue
            try:
                face = bm.faces.new(face_verts)
            except ValueError:
                continue
            new_faces.append(face)

        if not new_faces:
            raise RuntimeError("Could not append clean convex hull to the target mesh")

        if recalc_normals:
            bmesh.ops.recalc_face_normals(bm, faces=new_faces)

        bm.normal_update()
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True)

        return {
            "verts_added": len(mesh.vertices) - before_vert_count,
            "faces_added": len(mesh.polygons) - before_face_count,
            "used_verts": hull_data["used_verts"],
        }
    finally:
        bm.free()


def _append_world_vertices_to_object(target_obj, world_points):
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target Geometry LOD object must be a mesh")
    if target_obj.mode == "EDIT":
        raise RuntimeError("Target Geometry LOD must not be in Edit Mode while copying vertices")

    unique_points = _dedupe_world_points(world_points)
    if not unique_points:
        raise RuntimeError("No vertices to append")

    mesh = target_obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        to_local = target_obj.matrix_world.inverted_safe()
        new_verts = []
        for point in unique_points:
            try:
                vert = bm.verts.new(to_local @ point)
                new_verts.append(vert)
            except ValueError:
                continue

        bm.verts.ensure_lookup_table()
        new_indices = [vert.index for vert in new_verts if vert.is_valid]
        if not new_indices:
            raise RuntimeError("Selected vertices already exist in Geometry")

        bm.to_mesh(mesh)
        mesh.update(calc_edges=True)
        return new_indices
    finally:
        bm.free()


def _duplicate_selected_verts_as_loose_points_in_edit_object(target_obj):
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target Geometry LOD object must be a mesh")
    if target_obj.mode != "EDIT":
        raise RuntimeError("Target Geometry LOD must be active in Edit Mode")

    mesh = target_obj.data
    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()

    selected_verts = [vert for vert in bm.verts if vert.is_valid and vert.select]
    if not selected_verts:
        raise RuntimeError("Select at least one vertex on the source mesh")

    dup = bmesh.ops.duplicate(bm, geom=selected_verts)
    new_verts = [
        item for item in dup.get("geom", [])
        if isinstance(item, bmesh.types.BMVert) and item.is_valid
    ]
    if not new_verts:
        raise RuntimeError("Could not duplicate selected vertices")

    for vert in bm.verts:
        vert.select = False
    for edge in bm.edges:
        edge.select = False
    for face in bm.faces:
        face.select = False
    for vert in new_verts:
        vert.select = True

    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=True)
    return [vert.index for vert in new_verts if vert.is_valid]


def _append_selected_faces_to_object(target_obj, source_obj, recalc_normals=True, weld_distance=0.0):
    if source_obj is None or source_obj.type != "MESH" or source_obj.mode != "EDIT":
        raise RuntimeError("Source object must be the active mesh in Edit Mode")
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target Roadway object must be a mesh")
    if target_obj.mode == "EDIT":
        raise RuntimeError("Target Roadway object must not be in Edit Mode while copying polygons")
    if target_obj == source_obj:
        raise RuntimeError("Target Roadway object must be separate from the edited source mesh")

    bm_src = bmesh.from_edit_mesh(source_obj.data)
    selected_faces = [face for face in bm_src.faces if face.select]
    if not selected_faces:
        raise RuntimeError("Select at least one polygon on the source mesh")

    material_slot_map = {}
    preferred_material_name = ""
    source_slots = list(getattr(source_obj, "material_slots", []))
    target_materials = target_obj.data.materials

    for src_material_index in sorted({face.material_index for face in selected_faces}):
        src_mat = source_slots[src_material_index].material if src_material_index < len(source_slots) else None

        if src_mat is not None:
            target_material_index, roadway_material_name = _ensure_roadway_material(target_materials, src_mat)
            if not preferred_material_name:
                preferred_material_name = roadway_material_name
        elif len(target_materials) > 0:
            target_material_index = 0
        else:
            target_material_index, roadway_material_name = _ensure_roadway_material(target_materials, None)
            if not preferred_material_name:
                preferred_material_name = roadway_material_name

        material_slot_map[src_material_index] = target_material_index

    mesh = target_obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        before_vert_count = len(bm.verts)
        before_face_count = len(bm.faces)

        to_target_local = target_obj.matrix_world.inverted_safe()
        source_to_world = source_obj.matrix_world
        vert_map = {}
        created_faces = []

        for src_face in selected_faces:
            face_verts = []
            for src_vert in src_face.verts:
                key = src_vert.index
                new_vert = vert_map.get(key)
                if new_vert is None or not new_vert.is_valid:
                    new_vert = bm.verts.new(to_target_local @ (source_to_world @ src_vert.co))
                    vert_map[key] = new_vert
                face_verts.append(new_vert)

            if len(face_verts) < 3 or len(set(face_verts)) < 3:
                continue

            try:
                new_face = bm.faces.new(face_verts)
            except ValueError:
                continue
            new_face.material_index = material_slot_map.get(src_face.material_index, 0)
            created_faces.append(new_face)

        if not created_faces:
            raise RuntimeError("Could not copy selected polygons to Roadway")

        if weld_distance > 0.0 and bm.verts:
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_distance)
            bm.verts.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
            created_faces = [face for face in created_faces if face.is_valid]

        if recalc_normals:
            bmesh.ops.recalc_face_normals(bm, faces=created_faces)

        for vert in bm.verts:
            vert.select = False
        for edge in bm.edges:
            edge.select = False
        for face in bm.faces:
            face.select = False
        for face in created_faces:
            if face.is_valid:
                face.select = True

        bm.normal_update()
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True)
        return {
            "verts_added": len(mesh.vertices) - before_vert_count,
            "faces_added": len(mesh.polygons) - before_face_count,
            "preferred_material_name": preferred_material_name,
        }
    finally:
        bm.free()


def _weld_mesh_vertices(target_obj, merge_distance):
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Roadway Object must be a mesh")
    if merge_distance <= 0.0:
        return {"removed_verts": 0}
    if target_obj.mode != "EDIT":
        raise RuntimeError("Roadway Object must be active in Edit Mode")

    mesh = target_obj.data
    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    before_vert_count = len(bm.verts)
    if before_vert_count == 0:
        return {"removed_verts": 0, "selected_verts": 0}

    selected_verts = []
    seen_indices = set()

    def _add_vert(vert):
        if vert is None or not vert.is_valid:
            return
        key = vert.index
        if key in seen_indices:
            return
        seen_indices.add(key)
        selected_verts.append(vert)

    for vert in bm.verts:
        if vert.select:
            _add_vert(vert)
    for edge in bm.edges:
        if edge.select:
            for vert in edge.verts:
                _add_vert(vert)
    for face in bm.faces:
        if face.select:
            for vert in face.verts:
                _add_vert(vert)

    if not selected_verts:
        raise RuntimeError("Select Roadway vertices, edges, or faces to weld")

    bmesh.ops.remove_doubles(bm, verts=selected_verts, dist=merge_distance)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.normal_update()
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=True)

    return {
        "removed_verts": max(0, before_vert_count - len(bm.verts)),
        "selected_verts": len(selected_verts),
    }


def _activate_object_vertex_edit(context, obj, selected_indices=None):
    if obj is None or obj.type != "MESH":
        raise RuntimeError("Target object must be a mesh")

    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    _deselect_all_in_view_layer(context)
    try:
        obj.hide_set(False)
    except Exception:
        pass
    try:
        obj.hide_viewport = False
    except Exception:
        pass
    obj.select_set(True)
    context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="VERT")

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for vert in bm.verts:
        vert.select = False
    if selected_indices:
        for idx in selected_indices:
            if 0 <= idx < len(bm.verts):
                bm.verts[idx].select = True
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def _activate_object_edit_mode(context, obj, select_mode="VERT"):
    if obj is None or obj.type != "MESH":
        raise RuntimeError("Target object must be a mesh")

    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    _deselect_all_in_view_layer(context)
    try:
        obj.hide_set(False)
    except Exception:
        pass
    try:
        obj.hide_viewport = False
    except Exception:
        pass
    obj.select_set(True)
    context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type=select_mode)


def _delete_loose_vertices_by_local_keys(target_obj, local_keys, tolerance=1e-6):
    if target_obj is None or target_obj.type != "MESH" or not local_keys:
        return 0

    mesh = target_obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        remove_verts = [
            vert for vert in bm.verts
            if vert.is_valid
            and len(vert.link_edges) == 0
            and len(vert.link_faces) == 0
            and _vector_quantized_key(vert.co, tolerance) in local_keys
        ]
        if not remove_verts:
            return 0

        removed_count = len(remove_verts)
        bmesh.ops.delete(bm, geom=remove_verts, context="VERTS")
        bm.normal_update()
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True)
        return removed_count
    finally:
        bm.free()


def _build_convex_hull_from_loose_geometry_verts(context, target_obj, merge_distance=0.0, recalc_normals=True):
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target Geometry LOD object must be a mesh")
    if target_obj.mode != "EDIT":
        raise RuntimeError("Geometry object must be active in Edit Mode while building hull")

    mesh = target_obj.data
    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    before_vert_count = len(bm.verts)
    before_face_count = len(bm.faces)

    loose_verts = [
        vert for vert in bm.verts
        if vert.is_valid and vert.select and len(vert.link_edges) == 0 and len(vert.link_faces) == 0
    ]

    if len(loose_verts) < 4:
        raise RuntimeError("Need at least 4 selected loose vertices in Geometry to build a collider")

    if merge_distance > 0.0:
        bmesh.ops.remove_doubles(bm, verts=loose_verts, dist=merge_distance)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        loose_verts = [
            vert for vert in bm.verts
            if vert.is_valid and vert.select and len(vert.link_edges) == 0 and len(vert.link_faces) == 0
        ]

    local_points = [vert.co.copy() for vert in loose_verts if vert is not None and vert.is_valid]
    unique_point_keys = {_vector_quantized_key(point) for point in local_points}
    if len(unique_point_keys) < 4:
        raise RuntimeError("Selected loose vertices collapse below 4 unique points")

    hull = bmesh.ops.convex_hull(bm, input=loose_verts, use_existing_faces=False)
    final_faces = _finalize_convex_hull_geometry(bm, hull, loose_verts, recalc_normals=recalc_normals)
    _select_only_faces_in_bmesh(bm, final_faces)
    bm.normal_update()
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
    try:
        bpy.ops.mesh.select_mode(type="FACE")
    except Exception:
        pass

    return {
        "verts_added": len(bm.verts) - before_vert_count,
        "faces_added": len(bm.faces) - before_face_count,
        "used_verts": len(unique_point_keys),
        "removed_source_verts": 0,
    }


def _build_convex_hull_from_current_selection_operator(context, target_obj, recalc_normals=True):
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target object must be a mesh")
    if context.mode != "EDIT_MESH" or target_obj.mode != "EDIT":
        raise RuntimeError("Convex hull requires the target object to be active in Edit Mode")

    mesh = target_obj.data
    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    selected_verts = {vert for vert in bm.verts if vert.select and vert.is_valid}
    for edge in bm.edges:
        if edge.select and edge.is_valid:
            for vert in edge.verts:
                if vert.is_valid:
                    selected_verts.add(vert)
    for face in bm.faces:
        if face.select and face.is_valid:
            for vert in face.verts:
                if vert.is_valid:
                    selected_verts.add(vert)

    unique_point_keys = {_vector_quantized_key(vert.co) for vert in selected_verts}
    if len(unique_point_keys) < 4:
        raise RuntimeError("Need at least 4 unique selected vertices to build a collider")

    before_vert_count = len(bm.verts)
    before_face_count = len(bm.faces)

    bpy.ops.mesh.select_mode(type="VERT")
    bpy.ops.mesh.convex_hull(
        delete_unused=True,
        use_existing_faces=False,
        make_holes=False,
        join_triangles=True,
        face_threshold=0.0001745329,
        shape_threshold=0.0001745329,
        uvs=False,
        vcols=False,
        seam=False,
        sharp=False,
        materials=False,
    )
    bpy.ops.mesh.select_mode(type="FACE")
    bpy.ops.mesh.tris_convert_to_quads(
        face_threshold=3.14159265,
        shape_threshold=3.14159265,
        uvs=False,
        vcols=False,
        seam=False,
        sharp=False,
        materials=False,
    )

    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    if recalc_normals:
        selected_faces = [face for face in bm.faces if face.select and face.is_valid]
        if selected_faces:
            bmesh.ops.recalc_face_normals(bm, faces=selected_faces)

    bm.normal_update()
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
    try:
        bpy.ops.mesh.select_mode(type="FACE")
    except Exception:
        pass

    return {
        "verts_added": len(bm.verts) - before_vert_count,
        "faces_added": len(bm.faces) - before_face_count,
        "used_verts": len(unique_point_keys),
        "removed_source_verts": 0,
    }


def _build_collider_hull_from_world_points_via_edit_target(
    context,
    target_obj,
    world_points,
    merge_distance=0.0,
    recalc_normals=True,
):
    before_vert_count = len(target_obj.data.vertices)
    before_face_count = len(target_obj.data.polygons)

    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    added_indices = _append_world_vertices_to_object(target_obj, world_points)
    _activate_object_vertex_edit(context, target_obj, added_indices)
    stats = _build_convex_hull_from_loose_geometry_verts(
        context,
        target_obj,
        merge_distance=merge_distance,
        recalc_normals=recalc_normals,
    )
    stats["verts_added"] = len(target_obj.data.vertices) - before_vert_count
    stats["faces_added"] = len(target_obj.data.polygons) - before_face_count
    return stats


def _build_selection_hull_via_target(
    context,
    source_obj,
    target_obj,
    *,
    merge_distance=0.0,
    recalc_normals=True,
    box_thickness=0.0,
    loose_only=False,
):
    if source_obj is None or source_obj.type != "MESH" or source_obj.mode != "EDIT":
        raise RuntimeError("Source object must be the active mesh in Edit Mode")
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target Geometry LOD object must be a mesh")

    bm = bmesh.from_edit_mesh(source_obj.data)
    selected_faces = [face for face in bm.faces if face.select and face.is_valid]
    selected_edges = [edge for edge in bm.edges if edge.select and edge.is_valid]
    selected_verts = {vert for vert in bm.verts if vert.select and vert.is_valid}
    for edge in selected_edges:
        for vert in edge.verts:
            if vert.is_valid:
                selected_verts.add(vert)
    for face in selected_faces:
        for vert in face.verts:
            if vert.is_valid:
                selected_verts.add(vert)

    selected_geom = {
        "verts": list(selected_verts),
        "edges": selected_edges,
        "faces": selected_faces,
    }

    selection = _collect_selected_collider_input(source_obj, loose_only=loose_only)
    world_points = selection["world_points"]
    normal = selection["normal"]
    auto_thickened = False

    flat_eps = max(1e-5, merge_distance * 2.0)
    if _points_are_flat(world_points, normal, epsilon=flat_eps):
        if box_thickness <= 0.0:
            raise RuntimeError("Flat selection detected. Increase Thickness or use a non-flat selection")
        world_points = _extrude_points_along_normal(world_points, normal, box_thickness)
        auto_thickened = True

    if target_obj == source_obj and target_obj.mode == "EDIT":
        stats = _build_convex_hull_from_current_selection_operator(
            context,
            target_obj,
            recalc_normals=recalc_normals,
        )
    else:
        stats = _build_collider_hull_from_world_points_via_edit_target(
            context,
            target_obj,
            world_points,
            merge_distance=merge_distance,
            recalc_normals=recalc_normals,
        )

    stats["auto_thickened"] = auto_thickened
    return stats


def _collect_object_bounds_points(source_obj, padding=0.0, min_axis_size=0.0):
    if source_obj is None or source_obj.type != "MESH":
        raise RuntimeError("Source object must be a mesh")

    local_points = [Vector(corner) for corner in source_obj.bound_box]
    if not local_points:
        raise RuntimeError("Source object has no bounding box data")

    return _world_corners_from_local_bounds(
        source_obj,
        local_points,
        padding=padding,
        min_axis_size=min_axis_size,
    )

def _collect_single_selected_vertex_world_point(source_obj):
    if source_obj is None or source_obj.type != "MESH":
        raise RuntimeError("Source object must be a mesh")
    if source_obj.mode != "EDIT":
        raise RuntimeError("Source object must be the active mesh in Edit Mode")

    bm = bmesh.from_edit_mesh(source_obj.data)
    selected = [vert for vert in bm.verts if vert.select]
    if len(selected) != 1:
        raise RuntimeError("Select exactly one vertex on the active mesh")
    return source_obj.matrix_world @ selected[0].co.copy()

def _try_restore_edit_mode(context, obj):
    if obj is None or obj.type != "MESH":
        return
    if bpy.data.objects.get(obj.name) is None:
        return
    try:
        if context.view_layer.objects.active != obj:
            context.view_layer.objects.active = obj
        obj.select_set(True)
    except Exception:
        pass
    try:
        bpy.ops.object.mode_set(mode="EDIT")
    except Exception:
        pass


class CRAY_OT_CopySelectedVertsToGeometry(Operator):
    """Copy selected source vertices into the active Geometry LOD as loose points"""

    bl_idname = "cray.copy_selected_verts_to_geometry"
    bl_label = "Copy Selected Verts To Geometry"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        cs = context.scene.cray_collider_settings
        source_obj = _resolve_collider_selection_source_object(context, cs.source_object)
        if source_obj is None or source_obj.type != "MESH":
            self.report({"ERROR"}, "Source Object must be a mesh")
            return {"CANCELLED"}
        if source_obj.mode != "EDIT":
            self.report({"ERROR"}, "Copy requires the Source Object to be active in Edit Mode")
            return {"CANCELLED"}

        allow_same_source = (
            cs.geometry_object is not None
            and cs.geometry_object == source_obj
            and _is_collider_lod_mesh_object(source_obj, lod_token=cs.target_lod)
        )
        err = _collider_target_validation_error(
            cs.geometry_object,
            cs.target_lod,
            source_obj=source_obj,
            allow_same_source=allow_same_source,
        )
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        target_obj = _ensure_collider_lod_object(
            context,
            source_obj,
            cs.target_lod,
            preferred_obj=cs.geometry_object,
        )
        _set_collider_settings_object(context, "geometry_object", target_obj)

        try:
            if target_obj == source_obj and source_obj.mode == "EDIT":
                added_indices = _duplicate_selected_verts_as_loose_points_in_edit_object(target_obj)
                bpy.ops.mesh.select_mode(type="VERT")
            else:
                world_points = _collect_selected_vertex_world_points(source_obj)
                if context.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                added_indices = _append_world_vertices_to_object(target_obj, world_points)
                _activate_object_vertex_edit(context, target_obj, added_indices)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Copied {len(added_indices)} vertex/vertices to {target_obj.name}. You can now Shift+D in Geometry",
        )
        return {"FINISHED"}


class CRAY_OT_HullLooseGeometryVerts(Operator):
    """Build a convex hull from selected loose vertices in the Geometry LOD"""

    bl_idname = "cray.hull_loose_geometry_verts"
    bl_label = "Selected Loose Geometry Verts -> Hull"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        cs = context.scene.cray_collider_settings
        source_obj = cs.source_object if cs.source_object is not None and cs.source_object.type == "MESH" else None

        err = _collider_target_validation_error(cs.geometry_object, cs.target_lod)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        target_obj = _ensure_collider_lod_object(
            context,
            source_obj,
            cs.target_lod,
            preferred_obj=cs.geometry_object,
        )
        _set_collider_settings_object(context, "geometry_object", target_obj)

        try:
            if context.mode != "EDIT_MESH" or context.view_layer.objects.active != target_obj or target_obj.mode != "EDIT":
                self.report({"ERROR"}, "Select loose vertices on the Geometry LOD in Edit Mode")
                return {"CANCELLED"}
            stats = _build_convex_hull_from_loose_geometry_verts(
                context,
                target_obj,
                merge_distance=cs.merge_distance,
                recalc_normals=bool(cs.recalc_normals),
            )
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Built collider from {stats['used_verts']} selected loose Geometry verts in {target_obj.name}: "
                f"+{stats['faces_added']} faces"
            ),
        )
        return {"FINISHED"}


class CRAY_OT_ColliderHotkeysInfo(Operator):
    """Hover to see the NH collider hotkeys"""

    bl_idname = "cray.collider_hotkeys_info"
    bl_label = "Collider Hotkeys"
    bl_options = {"INTERNAL"}

    @classmethod
    def description(cls, context, properties):
        del context, properties
        return (
            "Ctrl+Shift+C: Copy Selected Verts To Geometry\n"
            "Mouse5: Select Isolated Verts\n"
            "Mouse4: Selection -> Hull"
        )

    def execute(self, context):
        del context
        return {"FINISHED"}


class CRAY_OT_EnsureRoadwayLOD(Operator):
    """Create or find the Roadway LOD mesh inside Misc collection"""

    bl_idname = "cray.ensure_roadway_lod"
    bl_label = "Create/Find Misc Roadway"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        cs = context.scene.cray_collider_settings
        source_obj = _resolve_collider_source_object(context, cs.source_object)

        err = _collider_target_validation_error(cs.roadway_object, _ROADWAY_LOD_TOKEN, source_obj=source_obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        try:
            roadway_obj = _ensure_roadway_lod_object(
                context,
                source_obj,
                preferred_obj=cs.roadway_object,
            )
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        _set_collider_settings_object(context, "roadway_object", roadway_obj)
        _sync_roadway_material_selection(context)
        self.report({"INFO"}, f"Roadway LOD ready: {roadway_obj.name}")
        return {"FINISHED"}


class CRAY_OT_CopySelectedFacesToRoadway(Operator):
    """Copy selected source polygons into the Roadway mesh in Misc collection"""

    bl_idname = "cray.copy_selected_faces_to_roadway"
    bl_label = "Copy Selected Faces To Roadway"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        cs = context.scene.cray_collider_settings
        source_obj = _resolve_collider_selection_source_object(context, cs.source_object)
        if source_obj is None or source_obj.type != "MESH":
            self.report({"ERROR"}, "Source Object must be a mesh")
            return {"CANCELLED"}
        if source_obj.mode != "EDIT":
            self.report({"ERROR"}, "Copy requires the Source Object to be active in Edit Mode")
            return {"CANCELLED"}

        err = _collider_target_validation_error(cs.roadway_object, _ROADWAY_LOD_TOKEN, source_obj=source_obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        try:
            roadway_obj = _ensure_roadway_lod_object(
                context,
                source_obj,
                preferred_obj=cs.roadway_object,
            )
            stats = _append_selected_faces_to_object(
                roadway_obj,
                source_obj,
                recalc_normals=bool(cs.recalc_normals),
                weld_distance=cs.roadway_weld_distance,
            )
            _activate_object_edit_mode(context, roadway_obj, select_mode="FACE")
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        _set_collider_settings_object(context, "roadway_object", roadway_obj)
        _sync_roadway_material_selection(context, stats.get("preferred_material_name", ""))
        self.report(
            {"INFO"},
            f"Copied source polygons to {roadway_obj.name}: +{stats['verts_added']} verts, +{stats['faces_added']} faces",
        )
        return {"FINISHED"}


class CRAY_OT_WeldRoadwayVertices(Operator):
    """Merge near-duplicate vertices only inside the current Roadway selection"""

    bl_idname = "cray.weld_roadway_vertices"
    bl_label = "Weld Roadway"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        cs = context.scene.cray_collider_settings
        target_obj = cs.roadway_object
        if target_obj is None or target_obj.type != "MESH":
            self.report({"ERROR"}, "Roadway Object must be a mesh")
            return {"CANCELLED"}
        if context.mode != "EDIT_MESH" or target_obj.mode != "EDIT":
            self.report({"ERROR"}, "Weld Roadway works only on the current Roadway selection in Edit Mode")
            return {"CANCELLED"}

        if cs.roadway_weld_distance <= 0.0:
            self.report({"ERROR"}, "Roadway Weld Distance must be greater than zero")
            return {"CANCELLED"}

        try:
            stats = _weld_mesh_vertices(target_obj, cs.roadway_weld_distance)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Welded selected Roadway elements in '{target_obj.name}': "
                f"removed {stats['removed_verts']} duplicate vert(s)"
            ),
        )
        return {"FINISHED"}


class CRAY_OT_OpenRoadwayMaterialFolder(Operator):
    """Pick a roadway material file inside Blender and assign it to the selected Roadway material"""

    bl_idname = "cray.open_roadway_material_folder"
    bl_label = "Choose Roadway Material Path"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(
        name="Material File",
        description="Choose an .rvmat or .paa file for the selected Roadway material",
        subtype="FILE_PATH",
    )
    filter_glob: StringProperty(default="*.rvmat;*.paa", options={"HIDDEN"})

    def invoke(self, context, event):
        del event

        mat = _get_selected_roadway_material(context)
        if mat is None:
            self.report({"ERROR"}, "Select or create a Roadway material first")
            return {"CANCELLED"}

        default_dir = _ROADWAY_SURFACES_FOLDER if os.path.isdir(_ROADWAY_SURFACES_FOLDER) else ""
        if not default_dir:
            blend_dir = bpy.path.abspath("//")
            if blend_dir and os.path.isdir(blend_dir):
                default_dir = blend_dir
            else:
                default_dir = os.getcwd()

        self.filepath = os.path.join(default_dir, "")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        mat = _get_selected_roadway_material(context)
        if mat is None:
            self.report({"ERROR"}, "Roadway material not found on the selected Roadway object")
            return {"CANCELLED"}

        try:
            filepath = os.path.abspath(bpy.path.abspath(self.filepath or ""))
        except Exception as e:
            self.report({"ERROR"}, f"Could not resolve material path: {_fmt_exc(e)}")
            return {"CANCELLED"}

        if not filepath or not os.path.isfile(filepath):
            self.report({"ERROR"}, "Choose an existing .rvmat or .paa file")
            return {"CANCELLED"}

        ext = os.path.splitext(filepath)[1].lower()
        try:
            if ext == ".rvmat":
                _set_a3ob_material_paths(mat, None, _norm_path(filepath))
            elif ext == ".paa":
                _set_a3ob_material_paths(mat, _norm_path(filepath), None)
            else:
                self.report({"ERROR"}, "Unsupported file type. Choose .rvmat or .paa")
                return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        new_name = _basename_no_ext(filepath)
        if new_name:
            try:
                mat.name = new_name
            except Exception:
                pass

        _sync_roadway_material_selection(context, mat.name)
        if ext == ".rvmat":
            self.report({"INFO"}, f"Assigned .rvmat to roadway material: {mat.name}")
        else:
            self.report({"INFO"}, f"Assigned .paa to roadway material: {mat.name}")
        return {"FINISHED"}


class CRAY_OT_SelectIsolatedVertices(Operator):
    """Select all isolated vertices that are not used by any edge or polygon"""

    bl_idname = "cray.select_isolated_vertices"
    bl_label = "Select Isolated Verts"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return context.mode == "EDIT_MESH" and obj is not None and obj.type == "MESH"

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Active object must be a mesh in Edit Mode")
            return {"CANCELLED"}

        mesh = obj.data
        bm = bmesh.from_edit_mesh(mesh)
        isolated_verts = [
            vert for vert in bm.verts
            if vert.is_valid and len(vert.link_edges) == 0 and len(vert.link_faces) == 0
        ]

        if not isolated_verts:
            self.report({"WARNING"}, "No isolated vertices found")
            return {"CANCELLED"}

        context.tool_settings.mesh_select_mode = (True, False, False)

        for face in bm.faces:
            face.select = False
        for edge in bm.edges:
            edge.select = False
        for vert in bm.verts:
            vert.select = False
        for vert in isolated_verts:
            vert.select = True

        bm.select_flush_mode()
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        self.report({"INFO"}, f"Selected {len(isolated_verts)} isolated vertex/vertices")
        return {"FINISHED"}


def _iter_split_planar_candidate_faces(bm):
    visible_faces = [face for face in bm.faces if face.is_valid and not face.hide]
    if not visible_faces:
        return [], "visible"

    selected_faces = [face for face in visible_faces if face.select]
    if selected_faces:
        return selected_faces, "selection"

    selected_verts = {vert for vert in bm.verts if vert.is_valid and not vert.hide and vert.select}
    selected_edges = {edge for edge in bm.edges if edge.is_valid and not edge.hide and edge.select}
    if not selected_verts and not selected_edges:
        return visible_faces, "visible"

    scoped_faces = []
    for face in visible_faces:
        if any(vert in selected_verts for vert in face.verts):
            scoped_faces.append(face)
            continue
        if any(edge in selected_edges for edge in face.edges):
            scoped_faces.append(face)

    if scoped_faces:
        return scoped_faces, "selection"
    return visible_faces, "visible"


def _split_planar_face_matches_seed_plane(face, plane_point, plane_normal, cos_limit, plane_tolerance):
    if face is None or not face.is_valid or face.hide:
        return False
    if plane_normal.length_squared <= 1e-12:
        return False
    if face.normal.length_squared <= 1e-12:
        return False

    face_normal = face.normal.copy()
    try:
        face_normal.normalize()
    except Exception:
        return False

    if abs(face_normal.dot(plane_normal)) < cos_limit:
        return False

    for vert in face.verts:
        if abs(plane_normal.dot(vert.co - plane_point)) > plane_tolerance:
            return False
    return True


def _collect_split_planar_region(seed_face, allowed_faces, cos_limit, plane_tolerance):
    plane_normal = seed_face.normal.copy()
    if plane_normal.length_squared <= 1e-12 or len(seed_face.verts) < 3:
        return []
    plane_normal.normalize()
    plane_point = seed_face.verts[0].co.copy()

    region_faces = []
    region_set = {seed_face}
    stack = [seed_face]

    while stack:
        face = stack.pop()
        region_faces.append(face)
        for edge in face.edges:
            for neighbor in edge.link_faces:
                if neighbor == face or neighbor not in allowed_faces or neighbor in region_set:
                    continue
                if _split_planar_face_matches_seed_plane(
                    neighbor,
                    plane_point,
                    plane_normal,
                    cos_limit,
                    plane_tolerance,
                ):
                    region_set.add(neighbor)
                    stack.append(neighbor)

    return region_faces


def _collect_connected_face_island(seed_face, allowed_faces):
    if seed_face is None or not seed_face.is_valid or seed_face.hide:
        return []

    island_faces = []
    island_set = {seed_face}
    stack = [seed_face]

    while stack:
        face = stack.pop()
        island_faces.append(face)
        for edge in face.edges:
            for neighbor in edge.link_faces:
                if neighbor == face or neighbor not in allowed_faces or neighbor in island_set:
                    continue
                if neighbor is None or not neighbor.is_valid or neighbor.hide:
                    continue
                island_set.add(neighbor)
                stack.append(neighbor)

    return island_faces


def _connected_face_island_counts(island_faces):
    face_set = {face for face in island_faces if face is not None and face.is_valid}
    vert_set = {vert for face in face_set for vert in face.verts if vert is not None and vert.is_valid}
    edge_set = {edge for face in face_set for edge in face.edges if edge is not None and edge.is_valid}
    return len(vert_set), len(face_set), len(edge_set)


def _connected_face_island_is_coplanar(island_faces, cos_limit, plane_tolerance):
    face_set = [face for face in island_faces if face is not None and face.is_valid and not face.hide]
    if not face_set:
        return False

    seed_face = face_set[0]
    if seed_face.normal.length_squared <= 1e-12 or len(seed_face.verts) < 3:
        return False

    plane_normal = seed_face.normal.copy()
    try:
        plane_normal.normalize()
    except Exception:
        return False
    plane_point = seed_face.verts[0].co.copy()

    for face in face_set:
        if not _split_planar_face_matches_seed_plane(
            face,
            plane_point,
            plane_normal,
            cos_limit,
            plane_tolerance,
        ):
            return False
    return True


def _split_planar_region_is_thin(region_faces, plane_point, plane_normal, cos_limit, plane_tolerance):
    region_set = {face for face in region_faces if face is not None and face.is_valid}
    if not region_set or plane_normal.length_squared <= 1e-12:
        return False

    region_verts = {vert for face in region_set for vert in face.verts if vert is not None and vert.is_valid}
    for vert in region_verts:
        for linked_face in vert.link_faces:
            if linked_face in region_set:
                continue
            if linked_face is None or not linked_face.is_valid or linked_face.hide:
                continue
            if not _split_planar_face_matches_seed_plane(
                linked_face,
                plane_point,
                plane_normal,
                cos_limit,
                plane_tolerance,
            ):
                return False
    return True


def _add_split_region_match(matches_by_signature, region_faces, boundary_edges, boundary_verts, match_kind):
    face_set = {face for face in region_faces if face is not None and face.is_valid}
    if not face_set:
        return False

    signature = frozenset(face_set)
    existing = matches_by_signature.get(signature)
    if existing is None:
        matches_by_signature[signature] = {
            "faces": list(face_set),
            "boundary_edges": [edge for edge in boundary_edges if edge is not None and edge.is_valid],
            "boundary_verts": [vert for vert in boundary_verts if vert is not None and vert.is_valid],
            "kind": match_kind,
        }
        return True

    if existing.get("kind") != "tiny" and match_kind == "tiny":
        existing["kind"] = "tiny"
    elif existing.get("kind") not in {"tiny", "coplanar"} and match_kind == "coplanar":
        existing["kind"] = "coplanar"
    return False


def _classify_split_planar_region_edges(region_faces):
    region_set = set(region_faces)
    boundary_edges = []
    seen_edges = set()
    non_manifold = False

    for face in region_faces:
        for edge in face.edges:
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            inside_count = sum(1 for link_face in edge.link_faces if link_face in region_set)
            if inside_count == 1:
                boundary_edges.append(edge)
            elif inside_count > 2:
                non_manifold = True

    boundary_verts = {vert for edge in boundary_edges for vert in edge.verts}
    return boundary_edges, boundary_verts, non_manifold


def _split_planar_boundary_is_single_loop(boundary_edges, boundary_verts):
    if not boundary_edges or not boundary_verts:
        return False

    vert_edges = {vert: [] for vert in boundary_verts}
    for edge in boundary_edges:
        if edge is None or not edge.is_valid:
            return False
        for vert in edge.verts:
            if vert not in vert_edges:
                return False
            vert_edges[vert].append(edge)

    if any(len(edges) != 2 for edges in vert_edges.values()):
        return False

    start_vert = next(iter(boundary_verts))
    seen_verts = set()
    stack = [start_vert]
    while stack:
        vert = stack.pop()
        if vert in seen_verts:
            continue
        seen_verts.add(vert)
        for edge in vert_edges[vert]:
            other_vert = edge.other_vert(vert)
            if other_vert not in seen_verts:
                stack.append(other_vert)

    return len(seen_verts) == len(boundary_verts)


def _find_split_planar_ngon_regions(bm, min_vertex_count, angle_tolerance_deg=0.1, plane_tolerance=0.0001):
    candidate_faces, scope_label = _iter_split_planar_candidate_faces(bm)
    if not candidate_faces:
        return [], scope_label

    allowed_faces = set(candidate_faces)
    matches_by_signature = {}
    cos_limit = math.cos(math.radians(max(0.0, min(180.0, float(angle_tolerance_deg)))))

    processed_islands = set()
    for seed_face in candidate_faces:
        if seed_face in processed_islands or not seed_face.is_valid:
            continue

        island_faces = _collect_connected_face_island(seed_face, allowed_faces)
        if not island_faces:
            processed_islands.add(seed_face)
            continue

        island_set = set(island_faces)
        processed_islands.update(island_set)

        island_vert_count, island_face_count, island_edge_count = _connected_face_island_counts(island_faces)
        boundary_edges, boundary_verts, non_manifold = _classify_split_planar_region_edges(island_faces)
        if (
            island_vert_count <= _TRASH_TINY_ISLAND_MAX_VERTS
            or island_face_count <= _TRASH_TINY_ISLAND_MAX_FACES
            or island_edge_count <= _TRASH_TINY_ISLAND_MAX_EDGES
        ):
            if not non_manifold:
                _add_split_region_match(
                    matches_by_signature,
                    island_faces,
                    boundary_edges,
                    boundary_verts,
                    "tiny",
                )

        if _connected_face_island_is_coplanar(
            island_faces,
            cos_limit,
            max(0.0, float(plane_tolerance)),
        ):
            _add_split_region_match(
                matches_by_signature,
                island_faces,
                boundary_edges,
                boundary_verts,
                "coplanar",
            )

    processed_faces = set()

    for seed_face in candidate_faces:
        if seed_face in processed_faces or not seed_face.is_valid:
            continue
        if seed_face.normal.length_squared <= 1e-12:
            processed_faces.add(seed_face)
            continue

        plane_normal = seed_face.normal.copy()
        plane_normal.normalize()
        plane_point = seed_face.verts[0].co.copy()

        region_faces = _collect_split_planar_region(
            seed_face,
            allowed_faces,
            cos_limit,
            max(0.0, float(plane_tolerance)),
        )
        if not region_faces:
            processed_faces.add(seed_face)
            continue

        region_set = set(region_faces)
        processed_faces.update(region_set)

        boundary_edges, boundary_verts, non_manifold = _classify_split_planar_region_edges(region_faces)
        if non_manifold:
            continue
        if len(boundary_verts) < int(min_vertex_count):
            continue
        if not _split_planar_boundary_is_single_loop(boundary_edges, boundary_verts):
            continue
        if not _split_planar_region_is_thin(
            region_faces,
            plane_point,
            plane_normal,
            cos_limit,
            max(0.0, float(plane_tolerance)),
        ):
            continue

        _add_split_region_match(
            matches_by_signature,
            region_faces,
            boundary_edges,
            boundary_verts,
            "flat",
        )

    return list(matches_by_signature.values()), scope_label


def _select_split_planar_ngon_regions(bm, matches):
    selected_faces = set()
    selected_edges = set()
    selected_verts = set()

    for item in matches:
        selected_faces.update(face for face in item.get("faces", []) if face is not None and face.is_valid)
        selected_edges.update(edge for edge in item.get("boundary_edges", []) if edge is not None and edge.is_valid)
        selected_verts.update(vert for vert in item.get("boundary_verts", []) if vert is not None and vert.is_valid)

    for face in bm.faces:
        face.select = False
    for edge in bm.edges:
        edge.select = False
    for vert in bm.verts:
        vert.select = False

    for face in selected_faces:
        face.select = True
    for edge in selected_edges:
        edge.select = True
    for vert in selected_verts:
        vert.select = True


class CRAY_OT_SelectSplitPlanarNgons(Operator):
    """Select flat thin face islands whose outer boundary has at least N vertices"""

    bl_idname = "cray.select_split_planar_ngons"
    bl_label = "Select Flat Thin N-gons"
    bl_description = (
        "In Edit Mode, find either flat thin face islands whose outer boundary has at least N vertices, "
        "or tiny connected face islands where verts <= 5, or faces <= 6, or edges <= 9, "
        "or any connected face island that lies in one plane. "
        "If faces, edges, or verts are already selected, "
        "only that local area is searched"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            context.mode == "EDIT_MESH"
            and obj is not None
            and obj.type == "MESH"
            and obj.mode == "EDIT"
        )

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH" or context.mode != "EDIT_MESH" or obj.mode != "EDIT":
            self.report({"ERROR"}, "Active object must be a mesh in Edit Mode")
            return {"CANCELLED"}

        ts = context.scene.cray_texreplace_settings
        mesh = obj.data
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        try:
            matches, scope_label = _find_split_planar_ngon_regions(
                bm,
                min_vertex_count=max(3, int(ts.split_planar_ngon_vertex_count)),
                angle_tolerance_deg=float(ts.split_planar_ngon_angle_tolerance),
                plane_tolerance=float(ts.split_planar_ngon_plane_tolerance),
            )
        except Exception as e:
            self.report({"ERROR"}, f"Failed to analyze split planar regions: {_fmt_exc(e)}")
            return {"CANCELLED"}

        if not matches:
            self.report(
                {"WARNING"},
                (
                    f"No flat thin N+ islands or tiny trash islands "
                    f"(verts <= 5 or faces <= 6 or edges <= 9) "
                    f"or fully coplanar islands found "
                    f"in {scope_label} scope"
                ),
            )
            return {"CANCELLED"}

        _select_split_planar_ngon_regions(bm, matches)
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

        face_total = sum(len(item["faces"]) for item in matches)
        edge_total = sum(len(item["boundary_edges"]) for item in matches)
        vert_total = sum(len(item["boundary_verts"]) for item in matches)
        tiny_total = sum(1 for item in matches if item.get("kind") == "tiny")
        coplanar_total = sum(1 for item in matches if item.get("kind") == "coplanar")
        flat_total = sum(1 for item in matches if item.get("kind") == "flat")
        self.report(
            {"INFO"},
            (
                f"Selected {len(matches)} island(s): flat N+ {flat_total}, "
                f"tiny by verts<=5/faces<=6/edges<=9 {tiny_total}, "
                f"fully coplanar {coplanar_total}; "
                f"{face_total} faces, {edge_total} boundary edges, {vert_total} boundary verts "
                f"in {scope_label} scope"
            ),
        )
        return {"FINISHED"}


class CRAY_OT_EnsureColliderLOD(Operator):
    """Create or find the Geometry LOD object and move it into the Geometries collection"""

    bl_idname = "cray.ensure_collider_lod"
    bl_label = "Create/Find Collider LOD"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        cs = context.scene.cray_collider_settings
        source_obj = _resolve_collider_source_object(context, cs.source_object)
        previous_active = context.view_layer.objects.active if context.view_layer is not None else None

        err = _collider_target_validation_error(cs.geometry_object, cs.target_lod, source_obj=source_obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        target_obj = _ensure_collider_lod_object(
            context,
            source_obj,
            cs.target_lod,
            preferred_obj=cs.geometry_object,
        )
        _set_collider_settings_object(context, "geometry_object", target_obj)
        try:
            context.view_layer.update()
        except Exception:
            pass
        if context.mode == "OBJECT" and source_obj is None:
            _deselect_all_in_view_layer(context)
            target_obj.select_set(True)
            context.view_layer.objects.active = target_obj
        elif context.mode == "OBJECT" and previous_active is not None:
            try:
                context.view_layer.objects.active = previous_active
            except Exception:
                pass
        self.report({"INFO"}, f"Collider LOD ready: {target_obj.name}")
        return {"FINISHED"}


class CRAY_OT_BuildCollider(Operator):
    """Directly build collider geometry from the current source selection or object bounds"""

    bl_idname = "cray.build_collider"
    bl_label = "Build Collider"
    bl_options = {"REGISTER", "UNDO"}

    build_mode: EnumProperty(
        name="Build Mode",
        items=(
            ("SELECTION_HULL", "Selection -> Hull", "Selected vertices/faces to convex hull"),
            ("ISOLATED_VERTS_HULL", "Isolated Verts -> Hull", "Use only isolated selected vertices to convex hull"),
            ("SELECTION_BOX", "Selection -> Box", "Selected wall/plane to thickness box"),
            ("OBJECT_BOUNDS", "Object -> Bounds", "Whole object bounds to box collider"),
        ),
        default="SELECTION_HULL",
        options={"HIDDEN"},
    )

    @classmethod
    def description(cls, context, properties):
        mode = getattr(properties, "build_mode", "SELECTION_HULL")
        return {
            "SELECTION_HULL": "Build a convex hull directly from the current source selection",
            "ISOLATED_VERTS_HULL": "Build a convex hull from isolated source vertices only",
            "SELECTION_BOX": "Build a box-like collider from a flat source selection using Thickness",
            "OBJECT_BOUNDS": "Build a box collider from the source object's local bounds",
        }.get(mode, cls.__doc__ or "")

    def execute(self, context):
        cs = context.scene.cray_collider_settings
        if self.build_mode == "OBJECT_BOUNDS":
            source_obj = _resolve_collider_source_object(context, cs.source_object)
        else:
            source_obj = _resolve_collider_selection_source_object(context, cs.source_object)
        if source_obj is None or source_obj.type != "MESH":
            self.report({"ERROR"}, "Source Object must be a mesh")
            return {"CANCELLED"}
        allow_same_source = (
            self.build_mode == "SELECTION_HULL"
            and cs.geometry_object is not None
            and cs.geometry_object == source_obj
            and _is_collider_lod_mesh_object(source_obj, lod_token=cs.target_lod)
        )
        err = _collider_target_validation_error(
            cs.geometry_object,
            cs.target_lod,
            source_obj=source_obj,
            allow_same_source=allow_same_source,
        )
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        if self.build_mode != "OBJECT_BOUNDS":
            active = context.view_layer.objects.active
            if active != source_obj or source_obj.mode != "EDIT":
                self.report({"ERROR"}, "Selection modes require the Source Object to be active in Edit Mode")
                return {"CANCELLED"}

        target_obj = _ensure_collider_lod_object(
            context,
            source_obj,
            cs.target_lod,
            preferred_obj=cs.geometry_object,
        )
        _set_collider_settings_object(context, "geometry_object", target_obj)

        if self.build_mode == "SELECTION_HULL":
            try:
                stats = _build_selection_hull_via_target(
                    context,
                    source_obj,
                    target_obj,
                    merge_distance=cs.merge_distance,
                    recalc_normals=bool(cs.recalc_normals),
                    box_thickness=cs.box_thickness,
                    loose_only=False,
                )
            except Exception as e:
                self.report({"ERROR"}, _fmt_exc(e))
                return {"CANCELLED"}

            extras = []
            if stats.get("auto_thickened"):
                extras.append(f"auto-thickness {cs.box_thickness:g}")
            extra_suffix = "" if not extras else f" ({', '.join(extras)})"
            self.report(
                {"INFO"},
                (
                    f"Built selection hull in {target_obj.name}: "
                    f"+{stats['verts_added']} verts, +{stats['faces_added']} faces{extra_suffix}"
                ),
            )
            return {"FINISHED"}

        if target_obj == source_obj and source_obj.mode == "EDIT":
            self.report({"ERROR"}, "Target Geometry LOD must be separate from the edited source mesh")
            return {"CANCELLED"}

        auto_thickened = False
        try:
            if self.build_mode == "OBJECT_BOUNDS":
                world_points = _collect_object_bounds_points(
                    source_obj,
                    padding=cs.bounds_padding,
                    min_axis_size=cs.box_thickness,
                )
            else:
                selection = _collect_selected_collider_input(
                    source_obj,
                    loose_only=(self.build_mode == "ISOLATED_VERTS_HULL"),
                )
                world_points = selection["world_points"]
                normal = selection["normal"]

                if self.build_mode == "SELECTION_BOX":
                    world_points = _extrude_points_along_normal(world_points, normal, cs.box_thickness)
                else:
                    flat_eps = max(1e-5, cs.merge_distance * 2.0)
                    if _points_are_flat(world_points, normal, epsilon=flat_eps):
                        if cs.box_thickness <= 0.0:
                            raise RuntimeError(
                                "Flat selection detected. Increase Thickness or use a non-flat selection"
                            )
                        world_points = _extrude_points_along_normal(world_points, normal, cs.box_thickness)
                        auto_thickened = True

            stats = _append_collider_hull_to_object(
                target_obj,
                world_points,
                merge_distance=cs.merge_distance,
                recalc_normals=bool(cs.recalc_normals),
            )
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        mode_name = {
            "SELECTION_HULL": "selection hull",
            "ISOLATED_VERTS_HULL": "isolated verts hull",
            "SELECTION_BOX": "selection box",
            "OBJECT_BOUNDS": "object bounds",
        }.get(self.build_mode, self.build_mode.lower())

        extras = []
        if auto_thickened:
            extras.append(f"auto-thickness {cs.box_thickness:g}")
        extra_suffix = "" if not extras else f" ({', '.join(extras)})"
        self.report(
            {"INFO"},
            (
                f"Built {mode_name} collider in {target_obj.name}: "
                f"+{stats['verts_added']} verts, +{stats['faces_added']} faces{extra_suffix}"
            ),
        )
        return {"FINISHED"}


# ------------------------------------------------------------------------
#  Texture Replace (.paa/.rvmat) + Replace from DB via A3OB
# ------------------------------------------------------------------------

_ALLOWED_DB_EXTS = {".paa", ".rvmat"}
_TEXTURE_SUFFIX_RE = re.compile(
    r"([_-])(co|ca|as|nohq|no|n|smdi|spec|det|detail|em|ao|rough|metal|mask)$",
    re.IGNORECASE,
)
def _norm_path(p: str) -> str:
    return (p or "").replace("/", "\\")

def _basename_no_ext(name_or_path: str) -> str:
    s = (name_or_path or "").replace("/", "\\").strip()
    s = s.split("\\")[-1]
    s = os.path.splitext(s)[0]
    return s

def _unique_ci(values):
    out = []
    seen = set()
    for v in values:
        s = (v or "").strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out

def _expand_basename_variants(base: str):
    base = _basename_no_ext(base)
    if not base:
        return []

    variants = [base]

    no_dot_num = re.sub(r"\.\d{3,}$", "", base)
    if no_dot_num != base:
        variants.append(no_dot_num)

    no_sep_num = re.sub(r"[_-]\d{2,4}$", "", no_dot_num)
    if no_sep_num != no_dot_num:
        variants.append(no_sep_num)

    for v in list(variants):
        stripped = _TEXTURE_SUFFIX_RE.sub("", v)
        if stripped and stripped != v:
            variants.append(stripped)

    for v in list(variants):
        if " " in v:
            variants.append(v.replace(" ", "_"))
            variants.append(v.replace(" ", "-"))

    return _unique_ci(variants)

def _build_material_candidates(mat: bpy.types.Material):
    candidates = [_basename_no_ext(mat.name)]

    if mat.use_nodes and mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type != "TEX_IMAGE" or not getattr(node, "image", None):
                continue
            img = node.image
            fp = getattr(img, "filepath", "") or ""
            if fp.strip():
                candidates.append(_basename_no_ext(fp))
            candidates.append(_basename_no_ext(img.name))

    expanded = []
    for c in _unique_ci(candidates):
        expanded.extend(_expand_basename_variants(c))
    return _unique_ci(expanded)

def _pick_best_db_match(candidates, db_map):
    best = None
    for base in candidates:
        key = base.lower()
        paa_path = db_map.get(f"{key}.paa")
        rvmat_path = db_map.get(f"{key}.rvmat")
        score = int(bool(paa_path)) + int(bool(rvmat_path))
        if score == 0:
            continue
        if best is None or score > best["score"]:
            best = {
                "base": base,
                "paa": paa_path,
                "rvmat": rvmat_path,
                "score": score,
            }
            if score == 2:
                break
    return best

def _walk_folder_build_db(folder_abs: str):
    if not os.path.isdir(folder_abs):
        raise RuntimeError(f"Folder not found: {folder_abs}")

    folder_abs = os.path.normpath(folder_abs)
    root_name = os.path.basename(folder_abs.rstrip("\\/")) or folder_abs

    buckets = {}
    for root, _, files in os.walk(folder_abs):
        for fn in files:
            full = os.path.join(root, fn)
            ext = os.path.splitext(full)[1].lower()
            if ext not in _ALLOWED_DB_EXTS:
                continue
            base = os.path.basename(full)
            key = base.lower()
            buckets.setdefault(key, set()).add(os.path.normpath(full))

    entries = []
    for key, pathset in buckets.items():
        uniq = sorted(pathset)
        chosen = uniq[0]
        rel = os.path.relpath(chosen, folder_abs)
        entries.append({
            "basename": os.path.basename(chosen),
            "abs_path": _norm_path(chosen),
            "rel_path": _norm_path(os.path.join(root_name, rel)),
            "is_problem": (len(uniq) > 1),
            "dup_count": len(uniq),
        })

    entries.sort(key=lambda d: (d["basename"].lower(), d["rel_path"].lower()))
    return entries

def _collect_object_image_materials(obj, out_collection):
    out_collection.clear()
    if not obj or obj.type != "MESH":
        return 0

    mats_done = set()
    count = 0
    for slot in obj.material_slots:
        mat = slot.material
        if not mat or mat in mats_done:
            continue
        mats_done.add(mat)

        if not mat.use_nodes or not mat.node_tree:
            continue

        images = []
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and getattr(node, "image", None):
                images.append(node.image.name)

        if images:
            it = out_collection.add()
            it.mat_name = mat.name
            it.images_csv = ", ".join(sorted(set(images), key=lambda x: x.lower()))
            count += 1
    return count

def _build_db_map(settings):
    db_map = {}
    dup_names = set()
    for it in settings.db_items:
        k = (it.basename or "").lower().strip()
        if not k:
            continue
        if it.is_problem:
            dup_names.add(k)
        db_map[k] = it.abs_path
    return db_map, dup_names

def _iter_descendants(root_obj):
    stack = list(root_obj.children)
    while stack:
        obj = stack.pop()
        yield obj
        stack.extend(obj.children)

def _obj_depth(obj):
    d = 0
    p = obj.parent
    while p is not None:
        d += 1
        p = p.parent
    return d

_HELPER_OBJ_PREFIXES = (
    "sector",
    "sectors",
    "selector",
    "selectors",
    "hierarchy",
    "hierarhy",
    "hierarrhy",
    "hierrarhy",
)

_ROOT_COLLECTION_NAME = "Collection"
_FIX_TARGET_COLLECTION_BASENAME = "NH_Fix_Result"

def _is_helper_object_name(name: str) -> bool:
    n = (name or "").strip().lower()
    return n.startswith(_HELPER_OBJ_PREFIXES) or n.startswith("hier")

def _link_object_to_collection(obj, collection):
    if obj is None or collection is None:
        return
    if collection.objects.get(obj.name) is None:
        collection.objects.link(obj)

def _move_object_to_collection(obj, target_collection):
    if obj is None or target_collection is None:
        return
    _link_object_to_collection(obj, target_collection)
    for col in list(obj.users_collection):
        if col == target_collection:
            continue
        try:
            col.objects.unlink(obj)
        except Exception:
            pass

def _is_export_helper_empty_name(name: str) -> bool:
    n = (name or "").strip().lower()
    return n == "visuals" or n.endswith("_a.p3d")

def _scene_fix_collection_name(scene):
    scene_name = (getattr(scene, "name", "") or "").strip()
    if not scene_name:
        return _FIX_TARGET_COLLECTION_BASENAME
    safe = re.sub(r"[\\/:*?\"<>|]+", "_", scene_name)
    return f"{_FIX_TARGET_COLLECTION_BASENAME}_{safe}"

def _ensure_target_collection(context, mesh_obj):
    scene_root = context.scene.collection
    target_name = _scene_fix_collection_name(context.scene)

    target = scene_root.children.get(target_name)
    if target is None:
        target = bpy.data.collections.new(target_name)
        scene_root.children.link(target)
    return target

def _collect_fix_scope(context, target_obj):
    ordered = []
    seen = set()

    def _push(o):
        if o is None:
            return
        key = o.name
        if key in seen:
            return
        seen.add(key)
        ordered.append(o)

    selected = list(context.selected_objects)
    if selected:
        for o in selected:
            _push(o)
            for ch in _iter_descendants(o):
                _push(ch)
        return ordered, "selected"

    root = target_obj
    while root is not None and root.parent is not None:
        root = root.parent
    if root is None:
        return [target_obj], "target-only"

    _push(root)
    for ch in _iter_descendants(root):
        _push(ch)
    if root == target_obj:
        return ordered, "target-descendants"
    return ordered, "root-branch"

def _pick_primary_mesh(scope_objs, preferred_obj):
    meshes = [o for o in scope_objs if o.type == "MESH" and o.data is not None]
    if not meshes:
        return None, "none"

    if preferred_obj in meshes and not _is_helper_object_name(preferred_obj.name):
        return preferred_obj, "preferred"

    non_helper = [o for o in meshes if not _is_helper_object_name(o.name)]
    if non_helper:
        best = max(non_helper, key=lambda o: len(o.data.polygons) if o.data else 0)
        return best, "largest-non-helper"

    best = max(meshes, key=lambda o: len(o.data.polygons) if o.data else 0)
    return best, "largest-mesh"

def _largest_mesh(objs):
    meshes = [o for o in objs if o is not None and o.type == "MESH" and o.data is not None]
    if not meshes:
        return None
    return max(meshes, key=lambda o: len(o.data.polygons) if o.data else 0)

def _meshes_in_branch(seed_obj):
    if seed_obj is None:
        return []

    root = seed_obj
    while root.parent is not None:
        root = root.parent

    branch = [root]
    branch.extend(_iter_descendants(root))
    return [o for o in branch if o.type == "MESH" and o.data is not None]

def _resolve_fix_target_object(context, picked_obj):
    selected = list(context.selected_objects)

    if selected:
        active = context.view_layer.objects.active
        if active is not None:
            if active.type == "MESH":
                return active, "active"
            branch_meshes = _meshes_in_branch(active)
            mesh = _largest_mesh(branch_meshes)
            if mesh is not None:
                return mesh, "active-branch"

        selected_mesh = _largest_mesh(selected)
        if selected_mesh is not None:
            return selected_mesh, "selected"

        selected_branch_meshes = []
        for o in selected:
            selected_branch_meshes.extend(_meshes_in_branch(o))
        mesh = _largest_mesh(selected_branch_meshes)
        if mesh is not None:
            return mesh, "selected-branch"

    active = context.view_layer.objects.active
    if active is not None:
        if active.type == "MESH":
            return active, "active"
        branch_meshes = _meshes_in_branch(active)
        mesh = _largest_mesh(branch_meshes)
        if mesh is not None:
            return mesh, "active-branch"

    if picked_obj is not None:
        if picked_obj.type == "MESH":
            return picked_obj, "picked"
        branch_meshes = _meshes_in_branch(picked_obj)
        mesh = _largest_mesh(branch_meshes)
        if mesh is not None:
            return mesh, "picked-branch"

    return _resolve_tex_target_object(context, picked_obj)

def _collect_collection_objects_deep(collection):
    if collection is None:
        return []

    out = []
    seen_cols = set()
    seen_objs = set()
    stack = [collection]
    while stack:
        col = stack.pop()
        if col is None:
            continue
        col_key = col.as_pointer()
        if col_key in seen_cols:
            continue
        seen_cols.add(col_key)

        for obj in col.objects:
            obj_key = obj.as_pointer()
            if obj_key in seen_objs:
                continue
            seen_objs.add(obj_key)
            out.append(obj)
        stack.extend(col.children)
    return out

def _collect_collections_deep(collection):
    if collection is None:
        return []

    out = []
    seen = set()
    stack = [collection]
    while stack:
        col = stack.pop()
        if col is None:
            continue
        key = col.as_pointer()
        if key in seen:
            continue
        seen.add(key)
        out.append(col)
        stack.extend(col.children)
    return out

def _pick_random_scene_fix_mesh(context):
    root_col = context.scene.collection.children.get(_ROOT_COLLECTION_NAME)
    if root_col is not None:
        root_col_meshes = [
            o for o in _collect_collection_objects_deep(root_col)
            if o.type == "MESH" and o.data is not None and len(o.data.polygons) > 0
        ]
        if root_col_meshes:
            non_helper = [o for o in root_col_meshes if not _is_helper_object_name(o.name)]
            if non_helper:
                return random.choice(non_helper), "collection-random-non-helper"
            return random.choice(root_col_meshes), "collection-random"

    scene_meshes = [
        o for o in context.scene.objects
        if o.type == "MESH" and o.data is not None and len(o.data.polygons) > 0
    ]
    if not scene_meshes:
        return None, "none"

    non_helper = [o for o in scene_meshes if not _is_helper_object_name(o.name)]
    if non_helper:
        return random.choice(non_helper), "scene-random-non-helper"
    return random.choice(scene_meshes), "scene-random"

def _resolve_tex_target_object(context, picked_obj):
    if picked_obj is not None and picked_obj.type == "MESH":
        return picked_obj, "picked"

    active = context.view_layer.objects.active
    if active is not None and active.type == "MESH":
        return active, "active"

    selected_meshes = [o for o in context.selected_objects if o.type == "MESH"]
    if len(selected_meshes) == 1:
        return selected_meshes[0], "selected"

    scene_meshes = [o for o in context.scene.objects if o.type == "MESH"]
    if not scene_meshes:
        return None, "none"
    if len(scene_meshes) == 1:
        return scene_meshes[0], "scene-single"

    best = max(scene_meshes, key=lambda o: len(o.data.polygons) if o.data else 0)
    return best, "scene-largest"

def _ensure_flat_collection_mesh(context, mesh_obj):
    target_collection = _ensure_target_collection(context, mesh_obj)
    mesh_world = mesh_obj.matrix_world.copy()

    _move_object_to_collection(mesh_obj, target_collection)
    mesh_obj.parent = None
    mesh_obj.matrix_world = mesh_world
    return target_collection, mesh_obj

def _purge_collection_tree(collection):
    deleted_objects = 0
    deleted_collections = 0

    for ch_idx, ch in enumerate(list(collection.children), start=1):
        child_deleted_objects, child_deleted_collections = _purge_collection_tree(ch)
        deleted_objects += child_deleted_objects
        deleted_collections += child_deleted_collections

        try:
            collection.children.unlink(ch)
        except Exception:
            pass
        else:
            if len(ch.users) == 0:
                try:
                    bpy.data.collections.remove(ch)
                except Exception:
                    pass
            deleted_collections += 1
        if ch_idx % 25 == 0:
            _ui_yield()

    for obj_idx, obj in enumerate(list(collection.objects), start=1):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            continue
        deleted_objects += 1
        if obj_idx % 50 == 0:
            _ui_yield()

    return deleted_objects, deleted_collections

def _cleanup_target_collection_keep_mesh(target_collection, keep_obj):
    deleted_objects = 0
    deleted_collections = 0

    for obj_idx, obj in enumerate(list(target_collection.objects), start=1):
        if obj == keep_obj:
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            continue
        deleted_objects += 1
        if obj_idx % 50 == 0:
            _ui_yield()

    for ch_idx, ch in enumerate(list(target_collection.children), start=1):
        child_deleted_objects, child_deleted_collections = _purge_collection_tree(ch)
        deleted_objects += child_deleted_objects
        deleted_collections += child_deleted_collections

        try:
            target_collection.children.unlink(ch)
        except Exception:
            continue

        if len(ch.users) == 0:
            try:
                bpy.data.collections.remove(ch)
            except Exception:
                pass
        deleted_collections += 1
        if ch_idx % 25 == 0:
            _ui_yield()

    return deleted_objects, deleted_collections

def _unlink_collection_from_all_parents(col):
    if col is None:
        return
    for scene in _iter_safe_scenes():
        try:
            if scene.collection.children.get(col.name) is not None:
                scene.collection.children.unlink(col)
        except Exception:
            pass
    for parent in list(bpy.data.collections):
        if parent == col:
            continue
        try:
            if parent.children.get(col.name) is not None:
                parent.children.unlink(col)
        except Exception:
            pass

def _unlink_collection_from_scene_parents(scene, col):
    if scene is None or col is None:
        return
    scene_cols = _collect_collections_deep(scene.collection)
    for parent in scene_cols:
        if parent == col:
            continue
        try:
            if any(ch == col for ch in parent.children):
                parent.children.unlink(col)
        except Exception:
            pass

def _force_remove_object(obj, keep_obj=None, allowed_col_ptrs=None):
    if obj is None or obj == keep_obj:
        return False

    cols = list(getattr(obj, "users_collection", []))
    if allowed_col_ptrs is not None:
        for col in cols:
            if col.as_pointer() not in allowed_col_ptrs:
                # Object is shared with another scene/collection tree. Keep it safe.
                return False

    try:
        if keep_obj is not None and keep_obj.parent == obj:
            keep_obj.parent = None
        for ch in list(obj.children):
            if ch == keep_obj:
                ch.parent = None
        obj.parent = None
    except Exception:
        pass

    for col in cols:
        if allowed_col_ptrs is not None and col.as_pointer() not in allowed_col_ptrs:
            continue
        try:
            col.objects.unlink(obj)
        except Exception:
            pass

    try:
        bpy.data.objects.remove(obj, do_unlink=True)
        return True
    except Exception:
        return False

def _remove_helper_named_objects(scene=None, keep_obj=None, max_passes=8):
    if scene is None:
        scene = bpy.context.scene if bpy.context is not None else None
    if scene is None:
        return 0, 0, []

    scene_cols = _collect_collections_deep(scene.collection)
    scene_col_ptrs = {c.as_pointer() for c in scene_cols if c is not None}

    deleted_objects = 0
    deleted_collections = 0

    for pass_idx in range(max_passes):
        deleted_pass = 0

        helpers = [
            o for o in scene.objects
            if o is not None and o != keep_obj and _is_helper_object_name(o.name)
        ]
        helpers.sort(key=_obj_depth, reverse=True)
        for obj_idx, helper in enumerate(helpers, start=1):
            live = helper
            if live is None or live == keep_obj:
                continue
            try:
                live_name = live.name
            except ReferenceError:
                continue
            if not _is_helper_object_name(live_name):
                continue
            if _force_remove_object(live, keep_obj=keep_obj, allowed_col_ptrs=scene_col_ptrs):
                deleted_objects += 1
                deleted_pass += 1
            if obj_idx % 50 == 0:
                _ui_yield()

        helper_cols = [
            c for c in scene_cols
            if c is not None and c != scene.collection and _is_helper_object_name(c.name)
        ]
        # Remove deeper sub-collections first.
        helper_cols.sort(key=lambda c: len(getattr(c, "children_recursive", [])), reverse=True)
        for col_idx, col in enumerate(helper_cols, start=1):
            live_col = col
            if live_col is None:
                continue
            try:
                live_col_name = live_col.name
            except ReferenceError:
                continue
            if not _is_helper_object_name(live_col_name):
                continue

            for obj in list(live_col.objects):
                if _force_remove_object(obj, keep_obj=keep_obj, allowed_col_ptrs=scene_col_ptrs):
                    deleted_objects += 1
                    deleted_pass += 1

            for ch in list(live_col.children):
                try:
                    live_col.children.unlink(ch)
                except Exception:
                    pass
                if len(ch.users) == 0:
                    try:
                        bpy.data.collections.remove(ch)
                    except Exception:
                        pass

            _unlink_collection_from_scene_parents(scene, live_col)
            if len(live_col.users) == 0:
                try:
                    bpy.data.collections.remove(live_col)
                    deleted_collections += 1
                    deleted_pass += 1
                except Exception:
                    pass
            if col_idx % 25 == 0:
                _ui_yield()

        if deleted_pass == 0:
            break
        if pass_idx % 2 == 1:
            _ui_yield()

    remaining_helpers = [
        o.name for o in scene.objects
        if o is not None and o != keep_obj and _is_helper_object_name(o.name)
    ]
    return deleted_objects, deleted_collections, remaining_helpers

def _ui_yield():
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except Exception:
        pass

def _ensure_object_visible_for_ops(obj):
    if obj is None:
        return
    try:
        obj.hide_set(False)
    except Exception:
        pass
    try:
        obj.hide_viewport = False
    except Exception:
        pass

def _join_meshes_in_batches(context, anchor_obj, mesh_names, batch_size=24):
    if anchor_obj is None or anchor_obj.type != "MESH" or anchor_obj.data is None:
        raise RuntimeError("Join anchor must be a mesh object")

    batch_size = int(batch_size)
    batch_limit = None if batch_size <= 1 else max(2, batch_size)
    anchor_name = anchor_obj.name
    pending = [n for n in mesh_names if n and n != anchor_name]
    joined_count = 0
    join_passes = 0

    while pending:
        anchor = bpy.data.objects.get(anchor_name)
        if anchor is None or anchor.type != "MESH" or anchor.data is None:
            raise RuntimeError("Join failed: anchor mesh became unavailable")

        batch_names = []
        next_pending = []
        for nm in pending:
            live = bpy.data.objects.get(nm)
            if live is None or live == anchor:
                continue
            if live.type != "MESH" or live.data is None or len(live.data.polygons) == 0:
                continue
            if batch_limit is None or len(batch_names) < batch_limit:
                batch_names.append(nm)
            else:
                next_pending.append(nm)
        pending = next_pending

        if not batch_names:
            break

        bpy.ops.object.select_all(action="DESELECT")
        _ensure_object_visible_for_ops(anchor)
        anchor.select_set(True)
        selected_for_join = [anchor]

        for nm in batch_names:
            live = bpy.data.objects.get(nm)
            if live is None or live == anchor:
                continue
            _ensure_object_visible_for_ops(live)
            live.select_set(True)
            selected_for_join.append(live)

        if len(selected_for_join) <= 1:
            continue

        context.view_layer.objects.active = anchor
        bpy.ops.object.join()
        joined_count += len(selected_for_join) - 1
        join_passes += 1

        active_after = context.view_layer.objects.active
        if active_after is not None and active_after.type == "MESH":
            anchor_name = active_after.name

        _ui_yield()

    merged_obj = bpy.data.objects.get(anchor_name)
    if merged_obj is None or merged_obj.type != "MESH":
        raise RuntimeError("Join failed: no merged mesh after staged join")
    return merged_obj, joined_count, join_passes

def _center_object_bbox_to_world_origin(obj):
    if obj is None:
        return False, Vector((0.0, 0.0, 0.0))

    bbox_world = []
    try:
        for corner in obj.bound_box:
            bbox_world.append(obj.matrix_world @ Vector(corner))
    except Exception:
        bbox_world = []

    if bbox_world:
        center = Vector((0.0, 0.0, 0.0))
        for p in bbox_world:
            center += p
        center /= len(bbox_world)
    else:
        center = obj.matrix_world.translation.copy()

    if center.length <= 1e-7:
        return False, center

    mw = obj.matrix_world.copy()
    mw.translation = mw.translation - center
    obj.matrix_world = mw
    return True, center

# ---------- A3OB material setter (FIXED) ----------

def _find_a3ob_material_pg(mat: bpy.types.Material):
    if mat is None:
        return None
    for attr in dir(mat):
        if not attr.startswith("a3ob"):
            continue
        try:
            pg = getattr(mat, attr)
        except Exception:
            continue
        if hasattr(pg, "bl_rna"):
            return pg
    return None

def _a3ob_props(mat_pg):
    props = []
    for p in mat_pg.bl_rna.properties:
        if p.identifier == "rna_type":
            continue
        # p.type is Blender RNA type label (STRING, ENUM, ...)
        props.append({
            "ui": p.name,
            "id": p.identifier,
            "type": p.type,
        })
    return props

def _pick_enum_id(props, keywords):
    for pr in props:
        if pr["type"] != "ENUM":
            continue
        ui_l = pr["ui"].lower()
        if all(k in ui_l for k in keywords):
            return pr["id"]
    return None

def _pick_string_id(props, keywords):
    for pr in props:
        if pr["type"] != "STRING":
            continue
        ui_l = pr["ui"].lower()
        if all(k in ui_l for k in keywords):
            return pr["id"]
    return None

def _get_a3ob_material_paths(mat: bpy.types.Material):
    pg = _find_a3ob_material_pg(mat)
    if pg is None:
        return None, None

    props = _a3ob_props(pg)
    paa_id = (
        _pick_string_id(props, ["paa"])
        or _pick_string_id(props, ["texture", "paa"])
        or _pick_string_id(props, ["texture"])
        or _pick_string_id(props, ["file"])
        or _pick_string_id(props, ["path"])
    )
    rvmat_id = (
        _pick_string_id(props, ["rvmat"])
        or _pick_string_id(props, ["rvm"])
        or _pick_string_id(props, ["material", "path"])
        or _pick_string_id(props, ["material"])
    )

    paa_path = ""
    rvmat_path = ""
    if paa_id and hasattr(pg, paa_id):
        try:
            paa_path = _norm_path(str(getattr(pg, paa_id, "") or "").strip())
        except Exception:
            paa_path = ""
    if rvmat_id and hasattr(pg, rvmat_id):
        try:
            rvmat_path = _norm_path(str(getattr(pg, rvmat_id, "") or "").strip())
        except Exception:
            rvmat_path = ""

    return paa_path or None, rvmat_path or None

def _get_material_first_image_path(mat: bpy.types.Material):
    if mat is None or not getattr(mat, "use_nodes", False) or not getattr(mat, "node_tree", None):
        return None

    for node in mat.node_tree.nodes:
        if node.type != "TEX_IMAGE":
            continue
        image = getattr(node, "image", None)
        if image is None:
            continue

        raw_path = getattr(image, "filepath_raw", "") or getattr(image, "filepath", "")
        if raw_path:
            try:
                return _norm_path(bpy.path.abspath(raw_path))
            except Exception:
                return _norm_path(str(raw_path))

        image_name = _basename_no_ext(getattr(image, "name", ""))
        if image_name:
            return image_name

    return None

def _derive_roadway_material_name(src_mat: bpy.types.Material):
    if src_mat is None:
        return "RoadwayMaterial"

    paa_path, rvmat_path = _get_a3ob_material_paths(src_mat)
    candidates = [
        paa_path,
        rvmat_path,
        _get_material_first_image_path(src_mat),
        getattr(src_mat, "name", ""),
    ]

    for candidate in candidates:
        base = _basename_no_ext(candidate)
        if base:
            return base

    return "RoadwayMaterial"

def _find_material_slot_index_by_name_ci(materials, material_name):
    target_name = (material_name or "").strip().lower()
    if not target_name:
        return None

    for slot_idx, existing_mat in enumerate(materials):
        if existing_mat is None:
            continue
        if existing_mat.name.strip().lower() == target_name:
            return slot_idx
    return None

def _ensure_roadway_material(target_materials, src_mat: bpy.types.Material):
    material_name = _derive_roadway_material_name(src_mat)
    existing_index = _find_material_slot_index_by_name_ci(target_materials, material_name)
    if existing_index is not None:
        existing_mat = target_materials[existing_index]
        return existing_index, existing_mat.name

    if src_mat is not None:
        roadway_mat = src_mat.copy()
    else:
        roadway_mat = bpy.data.materials.new(name=material_name)

    roadway_mat.name = material_name

    paa_path, rvmat_path = _get_a3ob_material_paths(src_mat)
    try:
        if paa_path or rvmat_path:
            _set_a3ob_material_paths(roadway_mat, paa_path, rvmat_path)
    except Exception:
        pass

    target_materials.append(roadway_mat)
    return len(target_materials) - 1, roadway_mat.name

def _set_a3ob_material_paths(mat: bpy.types.Material, paa_abs: str | None, rvmat_abs: str | None):
    pg = _find_a3ob_material_pg(mat)
    if pg is None:
        raise RuntimeError("A3OB material property group not found")

    props = _a3ob_props(pg)

    # 1) Ensure source enum -> File (TEX)
    # UI name in your screenshot: "Texture Source"
    src_id = _pick_enum_id(props, ["texture", "source"]) or _pick_enum_id(props, ["source"])
    if src_id and hasattr(pg, src_id):
        try:
            setattr(pg, src_id, "TEX")
        except Exception:
            # ignore if enum differs; not fatal
            pass

    # 2) Find string fields for PAA and RVMAT
    # We try multiple keyword combinations to survive different A3OB versions
    paa_id = (
        _pick_string_id(props, ["paa"])
        or _pick_string_id(props, ["texture", "paa"])
        or _pick_string_id(props, ["texture"])
        or _pick_string_id(props, ["file"])
        or _pick_string_id(props, ["path"])
    )

    rvmat_id = (
        _pick_string_id(props, ["rvmat"])
        or _pick_string_id(props, ["rvm"])
        or _pick_string_id(props, ["material", "path"])
        or _pick_string_id(props, ["material"])
    )

    # Hard requirement: if we want to set a value, field must exist
    if paa_abs is not None:
        if not paa_id or not hasattr(pg, paa_id):
            raise RuntimeError("PAA path field not found in A3OB Material Properties")
        setattr(pg, paa_id, paa_abs)

    if rvmat_abs is not None:
        if not rvmat_id or not hasattr(pg, rvmat_id):
            raise RuntimeError("RVMAT path field not found in A3OB Material Properties")
        setattr(pg, rvmat_id, rvmat_abs)

def _iter_unique_materials_from_objects(objects):
    materials = []
    seen = set()
    for obj in objects or []:
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        for slot in getattr(obj, "material_slots", []):
            mat = getattr(slot, "material", None)
            if mat is None:
                continue
            ptr = mat.as_pointer()
            if ptr in seen:
                continue
            seen.add(ptr)
            materials.append(mat)
    return materials

def _enable_preview_material_alpha(material: bpy.types.Material):
    try:
        material.blend_method = "HASHED"
    except Exception:
        pass

    try:
        material.shadow_method = "HASHED"
    except Exception:
        pass

def _apply_image_color_space(image, color_space: str):
    if image is None:
        return

    if color_space == "DATA":
        try:
            image.colorspace_settings.is_data = True
        except Exception:
            try:
                image.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
    else:
        try:
            image.colorspace_settings.is_data = False
        except Exception:
            pass

def _has_image_alpha(image) -> bool:
    if image is None:
        return False

    try:
        if getattr(image, "alpha_mode", "NONE") != "NONE":
            return True
    except Exception:
        pass

    try:
        return int(getattr(image, "channels", 0) or 0) >= 4
    except Exception:
        return False

def _remove_image_if_unused(image):
    if image is None:
        return

    try:
        if image.users == 0:
            bpy.data.images.remove(image)
    except Exception:
        pass

def _find_existing_paa_preview_image(filepath: str, color_space: str):
    filepath = os.path.abspath(bpy.path.abspath(filepath)).lower()
    is_data = color_space == "DATA"

    for image in bpy.data.images:
        image_path = getattr(image, "filepath_raw", "") or getattr(image, "filepath", "")
        if not image_path:
            continue

        try:
            image_path = os.path.abspath(bpy.path.abspath(image_path)).lower()
        except Exception:
            continue

        if image_path != filepath:
            continue

        try:
            image_is_data = bool(image.colorspace_settings.is_data)
        except Exception:
            image_is_data = False

        if image_is_data == is_data:
            return image

    return None

def _create_blender_image_from_paa_texture(filepath: str, tex, color_space: str):
    paa_ns = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.data_paa",
            "Arma3ObjectBuilder.io.data_paa",
        )
    )
    if paa_ns is None:
        return None

    paa_type = getattr(paa_ns, "PAA_Type", None)
    if paa_type is None:
        return None

    dxt1 = getattr(paa_type, "DXT1", None)
    dxt5 = getattr(paa_type, "DXT5", None)
    if tex.type not in (dxt1, dxt5):
        return None

    mip = tex.mips[0]
    mip.decompress(tex.type)
    swiztagg = tex.get_tagg("SWIZ")
    if swiztagg is not None:
        mip.swizzle(swiztagg.data)

    alpha = tex.type == dxt5
    img = bpy.data.images.new(
        os.path.basename(filepath),
        mip.width,
        mip.height,
        alpha=alpha,
        is_data=color_space == "DATA",
    )
    img.filepath_raw = filepath
    if alpha:
        img.alpha_mode = "PREMUL"
    else:
        img.alpha_mode = "NONE"

    _apply_image_color_space(img, color_space)

    img.pixels = [value for c in zip(*mip.data) for value in c]
    img.update()
    img.pack()
    return img

def _load_paa_image_with_original_a3ob(filepath: str, color_space: str = "SRGB", check_existing: bool = True):
    filepath = os.path.abspath(bpy.path.abspath(filepath))
    if check_existing:
        existing = _find_existing_paa_preview_image(filepath, color_space)
        if existing is not None:
            return existing, None

    paa_mod = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.data_paa",
            "Arma3ObjectBuilder.io.data_paa",
        )
    )
    if paa_mod is None:
        return None, None

    try:
        with open(filepath, "rb") as file:
            tex = paa_mod.PAA_File.read(file)
    except Exception:
        return None, None

    return _create_blender_image_from_paa_texture(filepath, tex, color_space), tex

def _ensure_a3ob_import_paa_helpers():
    import_paa_mod = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.import_paa",
            "Arma3ObjectBuilder.io.import_paa",
        )
    )
    if import_paa_mod is None:
        return None

    if not callable(getattr(import_paa_mod, "find_existing_image", None)):
        setattr(import_paa_mod, "find_existing_image", _find_existing_paa_preview_image)

    if not callable(getattr(import_paa_mod, "create_image_from_texture", None)):
        def _module_create_image_from_texture(filepath, tex, color_space):
            return _create_blender_image_from_paa_texture(filepath, tex, color_space)
        setattr(import_paa_mod, "create_image_from_texture", _module_create_image_from_texture)

    if not callable(getattr(import_paa_mod, "load_file", None)):
        def _module_load_file(filepath, color_space="SRGB", check_existing=True):
            return _load_paa_image_with_original_a3ob(filepath, color_space=color_space, check_existing=check_existing)
        setattr(import_paa_mod, "load_file", _module_load_file)

    return import_paa_mod

def _resolve_a3ob_texture_path(texture_path: str) -> str:
    raw = (texture_path or "").strip()
    if not raw:
        return ""

    import_p3d_mod = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.import_p3d",
            "Arma3ObjectBuilder.io.import_p3d",
        )
    )
    resolver = getattr(import_p3d_mod, "resolve_texture_path", None) if import_p3d_mod is not None else None
    if callable(resolver):
        try:
            resolved = resolver(raw)
            if resolved:
                resolved = os.path.abspath(bpy.path.abspath(resolved))
                if os.path.isfile(resolved):
                    return _norm_path(resolved)
        except Exception:
            pass

    utils_mod = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.utilities.generic",
            "Arma3ObjectBuilder.utilities.generic",
        )
    )
    restore_absolute = getattr(utils_mod, "restore_absolute", None) if utils_mod is not None else None

    candidates = []
    try:
        candidates.append(os.path.abspath(bpy.path.abspath(raw)))
    except Exception:
        pass

    if callable(restore_absolute):
        for extension in ("", ".paa"):
            try:
                candidate = restore_absolute(raw, extension)
            except TypeError:
                try:
                    candidate = restore_absolute(raw)
                except Exception:
                    candidate = ""
            except Exception:
                candidate = ""
            if candidate:
                candidates.append(candidate)

    if os.path.splitext(raw)[1] == "":
        try:
            candidates.append(os.path.abspath(bpy.path.abspath(raw + ".paa")))
        except Exception:
            pass

    checked = set()
    for candidate in candidates:
        if not candidate:
            continue
        try:
            candidate_abs = os.path.abspath(bpy.path.abspath(candidate))
        except Exception:
            continue
        key = os.path.normcase(candidate_abs)
        if key in checked:
            continue
        checked.add(key)
        if os.path.isfile(candidate_abs):
            return _norm_path(candidate_abs)

    return ""

def _paa_preview_cache_path(paa_abs_path: str) -> str:
    root, _ = os.path.splitext(paa_abs_path)
    return root + ".png"

def _save_image_as_png(image, filepath: str):
    folder = os.path.dirname(filepath)
    if folder:
        os.makedirs(folder, exist_ok=True)

    prev_filepath_raw = getattr(image, "filepath_raw", "")
    prev_filepath = getattr(image, "filepath", "")
    prev_file_format = getattr(image, "file_format", None)

    try:
        image.filepath_raw = filepath
        image.filepath = filepath
        if prev_file_format is not None:
            image.file_format = "PNG"
        image.save()
    finally:
        try:
            image.filepath_raw = prev_filepath_raw
        except Exception:
            pass
        try:
            image.filepath = prev_filepath
        except Exception:
            pass
        if prev_file_format is not None:
            try:
                image.file_format = prev_file_format
            except Exception:
                pass

def _load_external_image(filepath: str, color_space: str = "SRGB"):
    image = bpy.data.images.load(filepath, check_existing=True)
    _apply_image_color_space(image, color_space)
    return image

def _load_material_preview_image(texture_path: str, keep_converted_textures: bool, color_space: str = "SRGB"):
    resolved_path = _resolve_a3ob_texture_path(texture_path)
    if not resolved_path:
        return None, False, "", "missing", ""

    ext = os.path.splitext(resolved_path)[1].lower()
    if ext != ".paa":
        try:
            image = _load_external_image(resolved_path, color_space)
            return image, _has_image_alpha(image), resolved_path, "file", ""
        except Exception:
            return None, False, resolved_path, "missing", ""

    import_paa_mod = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.import_paa",
            "Arma3ObjectBuilder.io.import_paa",
        )
    )
    if import_paa_mod is None:
        import_paa_mod = _ensure_a3ob_import_paa_helpers()
    else:
        import_paa_mod = _ensure_a3ob_import_paa_helpers() or import_paa_mod
    if import_paa_mod is None:
        return None, False, resolved_path, "missing", ""

    load_file = getattr(import_paa_mod, "load_file", None)
    if not callable(load_file):
        def _fallback_load_file(filepath, color_space="SRGB", check_existing=True):
            return _load_paa_image_with_original_a3ob(filepath, color_space=color_space, check_existing=check_existing)
        load_file = _fallback_load_file

    cache_path = _paa_preview_cache_path(resolved_path)
    if keep_converted_textures and os.path.isfile(cache_path):
        cache_valid = True
        try:
            cache_valid = os.path.getmtime(cache_path) >= os.path.getmtime(resolved_path)
        except Exception:
            cache_valid = True

        if cache_valid:
            try:
                image = _load_external_image(cache_path, color_space)
                return image, _has_image_alpha(image), resolved_path, "cache_hit", cache_path
            except Exception:
                try:
                    os.remove(cache_path)
                except Exception:
                    pass

    try:
        image, tex = load_file(resolved_path, color_space)
    except Exception:
        return None, False, resolved_path, "missing", cache_path

    if image is None:
        return None, False, resolved_path, "missing", cache_path

    has_alpha = _has_image_alpha(image)
    try:
        paa_ns = getattr(import_paa_mod, "paa", None)
        paa_type = getattr(paa_ns, "PAA_Type", None) if paa_ns is not None else None
        dxt5 = getattr(paa_type, "DXT5", None) if paa_type is not None else None
        if tex is not None and dxt5 is not None:
            has_alpha = getattr(tex, "type", None) == dxt5
    except Exception:
        pass

    if keep_converted_textures:
        try:
            _save_image_as_png(image, cache_path)
            cache_image = _load_external_image(cache_path, color_space)
            cache_has_alpha = _has_image_alpha(cache_image) or has_alpha
            _remove_image_if_unused(image)
            return cache_image, cache_has_alpha, resolved_path, "cache_created", cache_path
        except Exception as e:
            print("=== Import/Export planner: failed to write texture cache ===")
            print(f"{resolved_path} -> {_fmt_exc(e)}")

    return image, has_alpha, resolved_path, "paa_runtime", cache_path

def _setup_import_preview_nodes(material: bpy.types.Material, image, texture_label: str, has_alpha: bool):
    if material is None or image is None:
        return False

    material.use_nodes = True
    node_tree = getattr(material, "node_tree", None)
    if node_tree is None:
        return False

    nodes = node_tree.nodes
    links = node_tree.links
    nodes.clear()

    node_output = nodes.new("ShaderNodeOutputMaterial")
    node_output.location = (300, 0)

    node_shader = nodes.new("ShaderNodeBsdfPrincipled")
    node_shader.location = (0, 0)
    links.new(node_shader.outputs["BSDF"], node_output.inputs["Surface"])

    node_texture = nodes.new("ShaderNodeTexImage")
    node_texture.location = (-320, 0)
    node_texture.image = image
    node_texture.label = os.path.basename(texture_label or getattr(image, "filepath", "") or image.name)
    links.new(node_texture.outputs["Color"], node_shader.inputs["Base Color"])

    if has_alpha:
        try:
            links.new(node_texture.outputs["Alpha"], node_shader.inputs["Alpha"])
            _enable_preview_material_alpha(material)
        except Exception:
            pass

    return True

def _postprocess_imported_material_previews(context, imported_objs, *, show_materials: bool, keep_converted_textures: bool):
    result = {
        "materials_total": 0,
        "textured_candidates": 0,
        "previewed": 0,
        "missing": 0,
        "cache_hits": 0,
        "cache_created": 0,
        "errors": [],
    }

    if not show_materials:
        return result

    materials = _iter_unique_materials_from_objects(imported_objs)
    result["materials_total"] = len(materials)

    for mat in materials:
        paa_path, _ = _get_a3ob_material_paths(mat)
        if not paa_path:
            continue

        result["textured_candidates"] += 1
        image, has_alpha, resolved_path, source_kind, _cache_path = _load_material_preview_image(
            paa_path,
            keep_converted_textures,
            color_space="SRGB",
        )
        if image is None:
            result["missing"] += 1
            continue

        try:
            if _setup_import_preview_nodes(mat, image, resolved_path or paa_path, has_alpha):
                result["previewed"] += 1
                if source_kind == "cache_hit":
                    result["cache_hits"] += 1
                elif source_kind == "cache_created":
                    result["cache_created"] += 1
        except Exception as e:
            result["errors"].append(f"{mat.name}: {_fmt_exc(e)}")

    scene = getattr(context, "scene", None)
    tex_settings = getattr(scene, "cray_texreplace_settings", None) if scene is not None else None
    preview_obj = getattr(tex_settings, "picked_object", None) if tex_settings is not None else None
    if preview_obj in imported_objs and tex_settings is not None:
        try:
            _collect_object_image_materials(preview_obj, tex_settings.obj_preview_items)
        except Exception:
            pass

    return result

def _get_import_preview_settings(context, operator=None):
    scene = getattr(context, "scene", None)
    settings = getattr(scene, "cray_ie_settings", None) if scene is not None else None

    show_materials = bool(getattr(settings, "import_show_materials", True))
    if operator is not None and hasattr(operator, "load_textures"):
        try:
            show_materials = bool(getattr(operator, "load_textures"))
        except Exception:
            pass

    keep_converted_textures = bool(getattr(settings, "import_keep_converted_textures", False))
    return show_materials, keep_converted_textures

def _log_import_preview_summary(filepath: str, stats):
    if not stats:
        return

    previewed = int(stats.get("previewed", 0) or 0)
    missing = int(stats.get("missing", 0) or 0)
    cache_hits = int(stats.get("cache_hits", 0) or 0)
    cache_created = int(stats.get("cache_created", 0) or 0)
    errors = list(stats.get("errors", []) or [])

    if previewed == 0 and missing == 0 and cache_hits == 0 and cache_created == 0 and not errors:
        return

    print("=== Import/Export planner: material previews ===")
    print(f"{os.path.basename(filepath) or filepath}")
    print(
        "materials: {materials_total}, textured: {textured_candidates}, previewed: {previewed}, "
        "missing: {missing}, cache hits: {cache_hits}, cache created: {cache_created}".format(
            materials_total=int(stats.get("materials_total", 0) or 0),
            textured_candidates=int(stats.get("textured_candidates", 0) or 0),
            previewed=previewed,
            missing=missing,
            cache_hits=cache_hits,
            cache_created=cache_created,
        )
    )
    if errors:
        for item in errors[:20]:
            print(item)

# ---------- UI data ----------

class CRAY_PG_TexDBItem(PropertyGroup):
    basename: StringProperty()
    abs_path: StringProperty()
    rel_path: StringProperty()
    is_problem: BoolProperty(default=False)
    dup_count: IntProperty(default=0)

class CRAY_PG_ObjMatImagesItem(PropertyGroup):
    mat_name: StringProperty()
    images_csv: StringProperty()

class CRAY_PG_TexReplaceSettings(PropertyGroup):
    folder: StringProperty(name="Folder", default="P:\\NH_ObjectTextures", subtype="DIR_PATH")
    picked_object: PointerProperty(name="Select Object", type=bpy.types.Object)
    fix_mesh_join_batch: IntProperty(
        name="Fix Mesh Join Batch",
        description=(
            "How many meshes to join in one pass. "
            "1 = try to join all at once (legacy behavior), "
            "higher values split work into stages"
        ),
        default=1,
        min=1,
        max=500,
    )
    fix_mesh_center_to_origin: BoolProperty(
        name="Center Fixed Mesh To (0,0,0)",
        description="After Fix Mesh, move merged object's bounds center to world origin",
        default=True,
    )
    split_planar_ngon_vertex_count: IntProperty(
        name="Flat Min N",
        description="Find flat thin face islands whose outer boundary has at least this many vertices",
        default=4,
        min=3,
        max=128,
    )
    split_planar_ngon_angle_tolerance: FloatProperty(
        name="Angle Tol",
        description="Maximum normal deviation in degrees when grouping faces into one planar island",
        default=0.1,
        min=0.0,
        soft_max=5.0,
        precision=4,
    )
    split_planar_ngon_plane_tolerance: FloatProperty(
        name="Plane Tol",
        description="Maximum signed distance from the seed plane for faces in the same planar island",
        default=0.0001,
        min=0.0,
        soft_max=0.01,
        precision=6,
    )
    obj_preview_items: bpy.props.CollectionProperty(type=CRAY_PG_ObjMatImagesItem)
    obj_preview_active_index: IntProperty(default=0)
    db_items: bpy.props.CollectionProperty(type=CRAY_PG_TexDBItem)
    db_active_index: IntProperty(default=0)

class CRAY_UL_TexDB(UIList):
    bl_idname = "CRAY_UL_tex_db"
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.alert = bool(item.is_problem)
        row = layout.row(align=True)
        row.label(text=item.basename, icon="FILE")
        row.label(text=item.rel_path)
        if item.is_problem:
            row.label(text=f"DUP x{item.dup_count}", icon="ERROR")

class CRAY_UL_ObjPreview(UIList):
    bl_idname = "CRAY_UL_obj_preview"
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.mat_name, icon="MATERIAL")
        row.label(text=item.images_csv, icon="IMAGE_DATA")

class CRAY_OT_TexDBBuildFromFolder(Operator):
    bl_idname = "cray.tex_db_build_folder"
    bl_label = "Build From Folder"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ts = context.scene.cray_texreplace_settings
        if not ts.folder:
            self.report({"ERROR"}, "Folder is not set")
            return {"CANCELLED"}

        folder_abs = bpy.path.abspath(ts.folder)
        if not os.path.isdir(folder_abs):
            self.report({"ERROR"}, f"Folder not found: {folder_abs}")
            return {"CANCELLED"}

        try:
            entries = _walk_folder_build_db(folder_abs)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to build DB from '{folder_abs}': {_fmt_exc(e)}")
            return {"CANCELLED"}

        ts.db_items.clear()
        for d in entries:
            it = ts.db_items.add()
            it.basename = d["basename"]
            it.abs_path = d["abs_path"]
            it.rel_path = d["rel_path"]
            it.is_problem = d["is_problem"]
            it.dup_count = d["dup_count"]

        total = len(entries)
        problems = sum(1 for d in entries if d["is_problem"])
        if total == 0:
            self.report({"WARNING"}, "DB is empty: no .paa/.rvmat found")
        elif problems:
            self.report({"WARNING"}, f"DB built: {total}. Problematic duplicates: {problems} (red)")
        else:
            self.report({"INFO"}, f"DB built: {total} (.paa/.rvmat)")
        return {"FINISHED"}

class CRAY_OT_UpdateObjectPreview(Operator):
    bl_idname = "cray.update_object_preview"
    bl_label = "Update Object Preview"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ts = context.scene.cray_texreplace_settings
        obj, src = _resolve_tex_target_object(context, ts.picked_object)
        if obj is None:
            ts.obj_preview_items.clear()
            self.report({"ERROR"}, "No mesh object found (pick one or select one)")
            return {"CANCELLED"}
        ts.picked_object = obj

        n = _collect_object_image_materials(obj, ts.obj_preview_items)
        if n == 0:
            self.report({"WARNING"}, f"Object '{obj.name}' has no materials with Image Texture nodes")
        else:
            suffix = "" if src == "picked" else f" (auto: {src})"
            self.report({"INFO"}, f"Object '{obj.name}': {n} materials with Image Texture nodes{suffix}")
        return {"FINISHED"}

class CRAY_OT_FixMeshHierarchy(Operator):
    bl_idname = "cray.fix_mesh_hierarchy"
    bl_label = "Fix Mesh/Hierarchy"
    bl_description = (
        "Use the picked/selected/active mesh as the main target, join meshes in scope, clean helper leftovers, "
        "and move the result into the fix collection"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ts = context.scene.cray_texreplace_settings
        target_obj, src = _resolve_fix_target_object(context, ts.picked_object)
        if target_obj is None:
            self.report({"ERROR"}, "No mesh object found (pick/select one)")
            return {"CANCELLED"}
        ts.picked_object = target_obj

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass

        scope_objs, scope_src = _collect_fix_scope(context, target_obj)

        scope_names = []
        for o in scope_objs:
            try:
                name = o.name
            except ReferenceError:
                continue
            if not name:
                continue
            if name not in scope_names:
                scope_names.append(name)

        mesh_candidates = [
            o for o in (bpy.data.objects.get(name) for name in scope_names)
            if o is not None and o.type == "MESH" and o.data is not None and len(o.data.polygons) > 0
        ]
        if not mesh_candidates:
            self.report({"ERROR"}, "No mesh object in selected scope")
            return {"CANCELLED"}

        anchor_mesh = None
        anchor_src = "largest-non-helper"
        if target_obj in mesh_candidates and not _is_helper_object_name(target_obj.name):
            anchor_mesh = target_obj
            anchor_src = "target"
        else:
            non_helper = [o for o in mesh_candidates if not _is_helper_object_name(o.name)]
            if non_helper:
                anchor_mesh = max(non_helper, key=lambda o: len(o.data.polygons) if o.data else 0)
            else:
                anchor_mesh = max(mesh_candidates, key=lambda o: len(o.data.polygons) if o.data else 0)
                anchor_src = "largest-mesh"
        if anchor_mesh is None:
            self.report({"ERROR"}, "No valid mesh anchor in selected scope")
            return {"CANCELLED"}
        active_mesh_name = anchor_mesh.name
        ts.picked_object = anchor_mesh

        try:
            merged_obj, joined_count, join_passes = _join_meshes_in_batches(
                context=context,
                anchor_obj=anchor_mesh,
                mesh_names=[o.name for o in mesh_candidates],
                batch_size=ts.fix_mesh_join_batch,
            )
        except Exception as e:
            self.report({"ERROR"}, f"Join failed: {_fmt_exc(e)}")
            return {"CANCELLED"}

        live_scope_names = []
        for name in scope_names:
            ch_live = bpy.data.objects.get(name)
            if ch_live is None or ch_live == merged_obj:
                continue
            live_scope_names.append(( _obj_depth(ch_live), name ))
        live_scope_names.sort(key=lambda it: it[0], reverse=True)

        deleted_scope = 0
        for idx, (_, name) in enumerate(live_scope_names, start=1):
            ch_live = bpy.data.objects.get(name)
            if ch_live is None:
                continue
            try:
                bpy.data.objects.remove(ch_live, do_unlink=True)
            except Exception:
                continue
            deleted_scope += 1
            if idx % 50 == 0:
                _ui_yield()

        target_collection, mesh_obj = _ensure_flat_collection_mesh(context, merged_obj)
        deleted_target_objs, deleted_target_cols = _cleanup_target_collection_keep_mesh(target_collection, mesh_obj)

        deleted_helpers, deleted_helper_cols, remaining_helpers = _remove_helper_named_objects(
            scene=context.scene,
            keep_obj=mesh_obj,
        )

        centered = False
        center_vec = Vector((0.0, 0.0, 0.0))
        if ts.fix_mesh_center_to_origin:
            centered, center_vec = _center_object_bbox_to_world_origin(mesh_obj)

        deleted_total = deleted_scope + deleted_target_objs + deleted_helpers
        extras = [
            f"src: {src}",
            f"scope: {scope_src}",
            f"scene: {context.scene.name}",
            f"scope_objs: {len(scope_names)}",
            f"anchor: {active_mesh_name}",
            f"anchor_src: {anchor_src}",
            f"join_passes: {join_passes}",
            f"join_batch: {int(ts.fix_mesh_join_batch)}",
        ]
        if deleted_helper_cols:
            extras.append(f"helper_cols: {deleted_helper_cols}")
        if remaining_helpers:
            extras.append(f"remaining_helpers: {len(remaining_helpers)}")
        if ts.fix_mesh_center_to_origin:
            if centered:
                extras.append(
                    f"centered_to_origin: yes ({center_vec.x:.3f}, {center_vec.y:.3f}, {center_vec.z:.3f})"
                )
            else:
                extras.append("centered_to_origin: already")
        else:
            extras.append("centered_to_origin: off")
        suffix = "" if not extras else f", {', '.join(extras)}"
        self.report(
            {"INFO"},
            (
                f"Fixed '{mesh_obj.name}': joined {joined_count}, removed objects {deleted_total}, "
                f"removed subcollections {deleted_target_cols}, "
                f"hierarchy: {context.scene.collection.name}/{target_collection.name}/{mesh_obj.name}{suffix}"
            ),
        )
        return {"FINISHED"}

class CRAY_OT_ReplaceTexturesFromDB(Operator):
    bl_idname = "cray.replace_textures_from_db"
    bl_label = "Replace Texture from DB"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ts = context.scene.cray_texreplace_settings
        obj, _ = _resolve_tex_target_object(context, ts.picked_object)

        if obj is None:
            self.report({"ERROR"}, "No mesh object found (pick one or select one)")
            return {"CANCELLED"}
        ts.picked_object = obj
        if len(ts.db_items) == 0:
            self.report({"ERROR"}, "DB is empty. Build From Folder first.")
            return {"CANCELLED"}

        db_map, dup_names = _build_db_map(ts)

        materials_checked = 0
        matched_total = 0
        matched_both = 0
        matched_paa_only = 0
        matched_rvmat_only = 0
        changed = 0
        missing = []
        failed = []

        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                continue

            materials_checked += 1
            candidates = _build_material_candidates(mat)
            match = _pick_best_db_match(candidates, db_map)

            if not match:
                preview = ", ".join(candidates[:5]) if candidates else "<none>"
                missing.append(f"{mat.name} -> no .paa/.rvmat match (candidates: {preview})")
                continue

            used_base = match["base"]
            found_paa = match["paa"]
            found_rvmat = match["rvmat"]
            matched_total += 1
            if found_paa and found_rvmat:
                matched_both += 1
            elif found_paa:
                matched_paa_only += 1
            else:
                matched_rvmat_only += 1

            try:
                _set_a3ob_material_paths(mat, found_paa, found_rvmat)
                changed += 1
            except Exception as e:
                failed.append(f"{mat.name} (base: {used_base}): {_fmt_exc(e)}")

        print("=== Texture Replace: Summary ===")
        print(f"Object: {obj.name}")
        print(f"Materials checked: {materials_checked}")
        print(
            f"Matched: {matched_total} (both: {matched_both}, "
            f"paa-only: {matched_paa_only}, rvmat-only: {matched_rvmat_only})"
        )
        print(f"Updated: {changed}")
        print(f"Missing: {len(missing)}")
        print(f"Failed: {len(failed)}")

        if failed:
            self.report({"ERROR"}, f"Updated: {changed}, failed: {len(failed)} (see System Console)")
            print("=== Texture Replace: A3OB set failed ===")
            for f in failed:
                print(f)
            if missing:
                print("=== Texture Replace: Missing entries ===")
                for m in missing:
                    print(m)
            return {"CANCELLED"}

        if missing:
            self.report({"WARNING"}, f"Updated: {changed}, missing: {len(missing)} (see System Console)")
            print("=== Texture Replace: Missing entries ===")
            for m in missing:
                print(m)
        else:
            self.report({"INFO"}, f"Updated: {changed} materials (A3OB updated)")

        if dup_names:
            print("=== Texture Replace: DB duplicates (picked first path) ===")
            for d in sorted(dup_names):
                print(d)

        return {"FINISHED"}

# ------------------------------------------------------------------------
#  Batch Import (A3OB)
# ------------------------------------------------------------------------

def _iter_layer_collections(layer_collection):
    stack = [layer_collection]
    while stack:
        lc = stack.pop()
        yield lc
        for ch in reversed(list(lc.children)):
            stack.append(ch)

def _disable_all_collections_in_view_layer(context, mode: str):
    vl = context.view_layer
    root_lc = vl.layer_collection
    for lc in _iter_layer_collections(root_lc):
        if lc is root_lc:
            continue
        if mode == "EXCLUDE":
            lc.exclude = True
        else:
            try:
                lc.collection.hide_viewport = True
                lc.collection.hide_render = True
            except Exception:
                pass

_IE_SOURCE_PATH_KEY = "cray_source_p3d"
_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*]')

def _iter_collection_tree(collection):
    stack = [collection]
    while stack:
        col = stack.pop()
        yield col
        for ch in reversed(list(col.children)):
            stack.append(ch)

def _collect_collection_objects_recursive(collection):
    objects = []
    seen = set()
    for col in _iter_collection_tree(collection):
        for obj in col.objects:
            ptr = obj.as_pointer()
            if ptr in seen:
                continue
            seen.add(ptr)
            objects.append(obj)
    return objects

def _collection_has_any_mesh(collection):
    for obj in _collect_collection_objects_recursive(collection):
        if obj.type == "MESH":
            return True
    return False

def _collection_has_any_object_ptr(collection, object_ptrs):
    if not object_ptrs:
        return False
    for obj in _collect_collection_objects_recursive(collection):
        if obj.as_pointer() in object_ptrs:
            return True
    return False

def _find_collection_path(root_collection, target_ptr):
    if root_collection.as_pointer() == target_ptr:
        return [root_collection]

    for child in root_collection.children:
        path = _find_collection_path(child, target_ptr)
        if path:
            return [root_collection] + path
    return None

def _ensure_collection_visible_in_view_layer(context, target_collection):
    root = context.scene.collection
    path = _find_collection_path(root, target_collection.as_pointer())
    if not path:
        return

    layer_map = {lc.collection.as_pointer(): lc for lc in _iter_layer_collections(context.view_layer.layer_collection)}
    to_show = []
    to_show.extend(path)
    to_show.extend(list(_iter_collection_tree(target_collection)))

    seen = set()
    for col in to_show:
        ptr = col.as_pointer()
        if ptr in seen:
            continue
        seen.add(ptr)

        lc = layer_map.get(ptr)
        if lc is not None:
            try:
                lc.exclude = False
            except Exception:
                pass

        try:
            col.hide_viewport = False
        except Exception:
            pass
        try:
            col.hide_render = False
        except Exception:
            pass

def _strip_blender_numeric_suffix(name: str) -> str:
    n = (name or "").strip()
    m = re.match(r"^(.*)\.(\d{3})$", n)
    if m:
        return m.group(1)
    return n

def _looks_like_p3d_collection_name(name: str) -> bool:
    n = (name or "").strip().lower()
    return ".p3d" in n

def _looks_like_split_part_collection_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    return re.search(r"_\d+\.p3d$", n, flags=re.IGNORECASE) is not None

def _build_ie_import_basename_map(settings):
    mapping = {}
    for item in settings.import_files:
        fp = bpy.path.abspath(item.path)
        if not fp:
            continue
        base = os.path.basename(fp).lower()
        if not base:
            continue
        if base not in mapping:
            mapping[base] = _norm_path(fp)
    return mapping


def _planner_add_import_file(settings, filepath: str) -> bool:
    if settings is None:
        return False

    fp = _norm_path(bpy.path.abspath(filepath)) if filepath else ""
    if not fp:
        return False

    for idx, item in enumerate(settings.import_files):
        existing = _norm_path(bpy.path.abspath(item.path)) if item.path else ""
        if existing == fp:
            settings.import_active_index = idx
            return False

    item = settings.import_files.add()
    item.path = fp
    settings.import_active_index = len(settings.import_files) - 1
    return True

def _resolve_collection_source_path(collection, import_basename_map=None):
    src = collection.get(_IE_SOURCE_PATH_KEY)
    if isinstance(src, str) and src.strip():
        return _norm_path(bpy.path.abspath(src))

    for obj in _collect_collection_objects_recursive(collection):
        src = obj.get(_IE_SOURCE_PATH_KEY)
        if isinstance(src, str) and src.strip():
            return _norm_path(bpy.path.abspath(src))

    if import_basename_map:
        names = []
        raw = (collection.name or "").strip()
        if raw:
            names.append(raw)
            names.append(_strip_blender_numeric_suffix(raw))
        for name in names:
            n = name.strip()
            if not n:
                continue
            if ".p3d" not in n.lower():
                n = n + ".p3d"
            key = n.lower()
            if key in import_basename_map:
                return import_basename_map[key]

    return ""

def _export_filename_for_collection(collection, source_path: str):
    if source_path:
        base = os.path.basename(source_path)
        if base:
            return base

    base = _strip_blender_numeric_suffix(collection.name)
    if not base:
        base = "export"
    if ".p3d" not in base.lower():
        base = base + ".p3d"
    return _INVALID_FILENAME_CHARS_RE.sub("_", base)

def _clear_ie_source_path_tag(id_data):
    if id_data is None:
        return
    try:
        if _IE_SOURCE_PATH_KEY in id_data:
            del id_data[_IE_SOURCE_PATH_KEY]
    except Exception:
        pass

def _set_ie_source_path_tag(id_data, source_path: str):
    if id_data is None:
        return
    src = _norm_path(bpy.path.abspath(source_path)) if source_path else ""
    if not src:
        _clear_ie_source_path_tag(id_data)
        return
    try:
        id_data[_IE_SOURCE_PATH_KEY] = src
    except Exception:
        pass

def _derive_split_export_source_path(source_root, split_root_name: str) -> str:
    source_path = _resolve_collection_source_path(source_root)
    if not source_path:
        return ""

    source_dir = os.path.dirname(source_path)
    if not source_dir:
        return ""

    base = _strip_blender_numeric_suffix((split_root_name or "").strip())
    if not base:
        base = "export"
    if ".p3d" not in base.lower():
        base = base + ".p3d"
    base = _INVALID_FILENAME_CHARS_RE.sub("_", base)
    return _norm_path(os.path.join(source_dir, base))

def _find_p3d_root_collection_for_object(context, obj):
    if obj is None:
        return None

    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    if scene_root is None:
        return None

    best = None
    best_depth = -1
    for col in getattr(obj, "users_collection", []):
        path = _find_collection_path(scene_root, col.as_pointer())
        if not path:
            continue
        for depth, item in enumerate(path):
            if _looks_like_p3d_collection_name(item.name) and depth >= best_depth:
                best = item
                best_depth = depth

    return best

def _plain_axis_helper_name(root_collection) -> str:
    root_name = _strip_blender_numeric_suffix((getattr(root_collection, "name", "") or "").strip())
    if not root_name:
        root_name = "Model"
    root_name = re.sub(r"\s+", " ", root_name).strip()
    return f"Plain Axis {root_name}"

def _is_plain_axis_helper(obj) -> bool:
    if obj is None or obj.type != "EMPTY":
        return False
    try:
        if bool(obj.get(_PLAIN_AXIS_HELPER_PROP, False)):
            return True
    except Exception:
        pass
    return False

def _pick_plain_axis_root_collection(context, source_obj):
    root = _find_p3d_root_collection_for_object(context, source_obj)
    if root is not None:
        return root, True
    if source_obj is not None and source_obj.users_collection:
        return source_obj.users_collection[0], False
    return getattr(getattr(context, "scene", None), "collection", None), False

def _collect_plain_axis_target_objects(root_collection, helper_obj=None):
    objects = _collect_collection_objects_recursive(root_collection) if root_collection is not None else []
    if not objects:
        return []

    object_ptrs = {obj.as_pointer() for obj in objects}
    targets = []
    for obj in objects:
        if helper_obj is not None and obj == helper_obj:
            continue
        if _is_plain_axis_helper(obj):
            continue
        if obj.parent is not None and obj.parent.as_pointer() in object_ptrs:
            continue
        targets.append(obj)
    return targets

def _iter_plain_axis_constraints(obj, helper_ptrs=None):
    if obj is None:
        return
    for con in getattr(obj, "constraints", []):
        if getattr(con, "type", "") != "CHILD_OF":
            continue
        target = getattr(con, "target", None)
        target_ptr = target.as_pointer() if target is not None else None
        if helper_ptrs and target_ptr in helper_ptrs:
            yield con
            continue
        if getattr(con, "name", "") == _PLAIN_AXIS_CONSTRAINT_NAME and target is not None and _is_plain_axis_helper(target):
            yield con

def _remove_plain_axis_constraints_from_objects(objects, helper_ptrs=None):
    removed = 0
    for obj in objects:
        constraints = list(_iter_plain_axis_constraints(obj, helper_ptrs=helper_ptrs))
        for con in constraints:
            try:
                obj.constraints.remove(con)
                removed += 1
            except Exception:
                pass
    return removed

def _apply_child_of_inverse_with_fallback(context, obj, constraint):
    if obj is None or constraint is None:
        return

    try:
        with context.temp_override(
            object=obj,
            active_object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
        ):
            bpy.ops.constraint.childof_set_inverse(constraint=constraint.name, owner="OBJECT")
        return
    except Exception:
        pass

    target = getattr(constraint, "target", None)
    if target is None:
        return
    try:
        constraint.inverse_matrix = target.matrix_world.inverted_safe()
    except Exception:
        pass

def _create_plain_axis_helper(context, root_collection, source_obj, world_location):
    helper_name = _plain_axis_helper_name(root_collection)
    helper_obj = bpy.data.objects.new(helper_name, None)
    helper_obj.empty_display_type = "PLAIN_AXES"
    try:
        max_dim = max(abs(float(v)) for v in getattr(source_obj, "dimensions", (0.0, 0.0, 0.0)))
    except Exception:
        max_dim = 0.0
    helper_obj.empty_display_size = max(0.05, max_dim * 0.08)
    helper_obj.matrix_world = Matrix.Translation(world_location)
    helper_obj[_PLAIN_AXIS_HELPER_PROP] = True
    helper_obj[_PLAIN_AXIS_ROOT_PROP] = getattr(root_collection, "name", "")
    helper_obj[_PLAIN_AXIS_SOURCE_OBJECT_PROP] = getattr(source_obj, "name", "")
    _link_object_to_collection(helper_obj, root_collection)
    _ensure_collection_visible_in_view_layer(context, root_collection)
    return helper_obj

def _clear_plain_axis_helpers(context, helper_objects):
    live_helpers = []
    seen = set()
    for obj in helper_objects:
        if obj is None:
            continue
        try:
            ptr = obj.as_pointer()
        except Exception:
            continue
        if ptr in seen:
            continue
        seen.add(ptr)
        if bpy.data.objects.get(obj.name) is None:
            continue
        live_helpers.append(obj)

    if not live_helpers:
        return 0, 0

    helper_ptrs = {obj.as_pointer() for obj in live_helpers}
    removed_constraints = _remove_plain_axis_constraints_from_objects(bpy.data.objects, helper_ptrs=helper_ptrs)

    removed_helpers = 0
    for obj in live_helpers:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed_helpers += 1
        except Exception:
            pass

    return removed_helpers, removed_constraints

def _clear_plain_axis_helpers_in_collection(context, root_collection):
    if root_collection is None:
        return 0, 0
    helpers = [obj for obj in _collect_collection_objects_recursive(root_collection) if _is_plain_axis_helper(obj)]
    return _clear_plain_axis_helpers(context, helpers)

def _best_object_collection_path_under_root(root_collection, obj):
    if root_collection is None or obj is None:
        return None

    best = None
    for col in getattr(obj, "users_collection", []):
        path = _find_collection_path(root_collection, col.as_pointer())
        if not path:
            continue
        if best is None or len(path) > len(best):
            best = path

    return best

def _format_split_part_collection_name(source_root_name: str, part_number: int) -> str:
    base_name = _strip_blender_numeric_suffix((source_root_name or "").strip())
    if not base_name:
        base_name = "part"

    suffix = f"_{int(part_number):02d}"
    stem, ext = os.path.splitext(base_name)
    if ext.lower() == ".p3d":
        return f"{stem}{suffix}{ext}"
    return f"{base_name}{suffix}.p3d"

def _ensure_split_part_root_collection(context, source_root, part_number: int):
    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    parent = _find_parent_collection(scene_root, source_root) if scene_root is not None else None
    if parent is None:
        parent = scene_root if scene_root is not None else source_root
    if parent is None:
        return None

    part_name = _format_split_part_collection_name(getattr(source_root, "name", ""), part_number)
    target = parent.children.get(part_name)
    if target is None:
        existing = bpy.data.collections.get(part_name)
        if existing is not None:
            target = existing
            try:
                if all(ch != target for ch in parent.children):
                    parent.children.link(target)
            except Exception:
                pass
        else:
            target = bpy.data.collections.new(part_name)
            parent.children.link(target)

    try:
        source_color = getattr(source_root, "color_tag", None)
        if source_color:
            target.color_tag = source_color
    except Exception:
        pass

    split_source_path = _derive_split_export_source_path(source_root, part_name)
    _set_ie_source_path_tag(target, split_source_path)
    return target

def _ensure_split_collection_path(dest_root, source_path):
    current = dest_root
    for source_col in list(source_path or [])[1:]:
        color_tag = None
        try:
            color_tag = getattr(source_col, "color_tag", None)
        except Exception:
            color_tag = None
        current = _ensure_named_child_collection(current, source_col.name, color_tag=color_tag)
        _clear_ie_source_path_tag(current)
    return current

def _duplicate_object_for_split(obj):
    if obj is None:
        return None

    new_obj = obj.copy()
    data = getattr(obj, "data", None)
    if data is not None:
        try:
            new_obj.data = data.copy()
        except Exception:
            pass

    try:
        new_obj.parent = None
    except Exception:
        pass

    try:
        new_obj.matrix_world = obj.matrix_world.copy()
    except Exception:
        pass

    _clear_ie_source_path_tag(new_obj)
    return new_obj

def _rewire_split_copy_object_refs(copies_by_source):
    source_to_copy = {src: dup for src, dup in copies_by_source.items() if src is not None and dup is not None}
    if not source_to_copy:
        return

    for source_obj, dup_obj in list(source_to_copy.items()):
        if dup_obj is None:
            continue

        try:
            parent_src = source_obj.parent
        except Exception:
            parent_src = None

        world_matrix = None
        try:
            world_matrix = source_obj.matrix_world.copy()
        except Exception:
            world_matrix = None

        if parent_src in source_to_copy:
            dup_parent = source_to_copy[parent_src]
            try:
                dup_obj.parent = dup_parent
                dup_obj.matrix_parent_inverse = dup_parent.matrix_world.inverted()
            except Exception:
                pass
            if world_matrix is not None:
                try:
                    dup_obj.matrix_world = world_matrix
                except Exception:
                    pass
        else:
            try:
                dup_obj.parent = None
            except Exception:
                pass
            if world_matrix is not None:
                try:
                    dup_obj.matrix_world = world_matrix
                except Exception:
                    pass

        for modifier in getattr(dup_obj, "modifiers", []):
            try:
                target_obj = getattr(modifier, "object", None)
            except Exception:
                target_obj = None
            if target_obj in source_to_copy:
                try:
                    modifier.object = source_to_copy[target_obj]
                except Exception:
                    pass

        for constraint in getattr(dup_obj, "constraints", []):
            try:
                target_obj = getattr(constraint, "target", None)
            except Exception:
                target_obj = None
            if target_obj in source_to_copy:
                try:
                    constraint.target = source_to_copy[target_obj]
                except Exception:
                    pass


def _resolve_object_source_p3d(obj):
    if obj is None:
        return ""

    candidates = [obj]
    if getattr(obj, "instance_collection", None) is not None:
        candidates.append(obj.instance_collection)

    parent = obj.parent
    while parent is not None:
        candidates.append(parent)
        if getattr(parent, "instance_collection", None) is not None:
            candidates.append(parent.instance_collection)
        parent = parent.parent

    for item in candidates:
        try:
            src = item.get(_IE_SOURCE_PATH_KEY)
        except Exception:
            src = None
        if isinstance(src, str) and src.strip():
            return _norm_path(bpy.path.abspath(src))

    for col in getattr(obj, "users_collection", []):
        src = _resolve_collection_source_path(col)
        if src:
            return src

    return ""


def _next_proxy_index_for_parent(parent_obj) -> int:
    max_index = 0
    for obj in bpy.data.objects:
        if obj.parent != parent_obj:
            continue
        if not hasattr(obj, "a3ob_properties_object_proxy"):
            continue
        try:
            pg = obj.a3ob_properties_object_proxy
        except Exception:
            continue
        for attr in ("index",):
            if hasattr(pg, attr):
                try:
                    max_index = max(max_index, int(getattr(pg, attr) or 0))
                except Exception:
                    pass
        try:
            for prop in pg.bl_rna.properties:
                if prop.identifier == "rna_type":
                    continue
                if prop.name == "Index":
                    try:
                        max_index = max(max_index, int(getattr(pg, prop.identifier) or 0))
                    except Exception:
                        pass
                    break
        except Exception:
            pass
    return max_index + 1


def _build_proxy_from_object_instance(proxy_obj, source_obj, parent_obj, proxy_index: int):
    proxy_obj.matrix_world = source_obj.matrix_world.copy()
    proxy_obj.parent = parent_obj
    try:
        proxy_obj.matrix_parent_inverse = parent_obj.matrix_world.inverted()
    except Exception:
        pass
    proxy_obj.name = f"proxy_{source_obj.name}_{proxy_index}"


def _pick_proxy_target_object(context, explicit_obj=None):
    if explicit_obj is not None and explicit_obj.type == "MESH":
        return explicit_obj
    active = context.view_layer.objects.active
    if active is not None and active.type == "MESH":
        return active
    for obj in context.selected_objects:
        if obj.type == "MESH":
            return obj
    return None


def _tag_import_source_on_imported_data(context, filepath, imported_objs, pre_collection_ptrs):
    src = _norm_path(bpy.path.abspath(filepath))
    if not src:
        return

    imported_ptrs = set()
    for obj in imported_objs:
        if obj is None:
            continue
        imported_ptrs.add(obj.as_pointer())
        try:
            obj[_IE_SOURCE_PATH_KEY] = src
        except Exception:
            pass

    if not imported_ptrs:
        return

    scene_root = context.scene.collection
    root_children = list(scene_root.children)
    new_collections = [c for c in bpy.data.collections if c.as_pointer() not in pre_collection_ptrs]

    for col in new_collections:
        if not _collection_has_any_object_ptr(col, imported_ptrs):
            continue
        try:
            col[_IE_SOURCE_PATH_KEY] = src
        except Exception:
            pass

    for col in new_collections:
        if not any(ch == col for ch in root_children):
            continue
        if not _collection_has_any_object_ptr(col, imported_ptrs):
            continue
        for nested in _iter_collection_tree(col):
            try:
                nested[_IE_SOURCE_PATH_KEY] = src
            except Exception:
                pass


def _patch_a3ob_import_read_file():
    if _A3OB_IMPORT_READ_FILE_PATCHES:
        return

    module_names = (
        "bl_ext.user_default.Arma3ObjectBuilder.io.import_p3d",
        "Arma3ObjectBuilder.io.import_p3d",
    )

    for module_name in module_names:
        mod = _import_first_available_module((module_name,))
        if mod is None:
            continue

        original_read_file = getattr(mod, "read_file", None)
        if not callable(original_read_file):
            continue
        if any(patched_mod is mod for patched_mod, _ in _A3OB_IMPORT_READ_FILE_PATCHES):
            continue

        def _wrapped_read_file(operator, context, file, _original_read_file=original_read_file, _module_name=module_name):
            filepath = _norm_path(bpy.path.abspath(getattr(operator, "filepath", "")))
            pre_col_ptrs = {col.as_pointer() for col in bpy.data.collections}

            lod_objects = _original_read_file(operator, context, file)

            if _A3OB_IMPORT_TRACKING_SUPPRESS_DEPTH > 0:
                return lod_objects

            if not filepath or not os.path.isfile(filepath):
                return lod_objects

            try:
                _tag_import_source_on_imported_data(
                    context=context,
                    filepath=filepath,
                    imported_objs=lod_objects or [],
                    pre_collection_ptrs=pre_col_ptrs,
                )
            except Exception as e:
                print("=== Import/Export planner: failed to tag A3OB import ===")
                print(f"{_module_name} -> {_fmt_exc(e)}")

            try:
                scene = getattr(context, "scene", None)
                settings = getattr(scene, "cray_ie_settings", None) if scene is not None else None
                _planner_add_import_file(settings, filepath)
            except Exception as e:
                print("=== Import/Export planner: failed to add A3OB import to planner ===")
                print(f"{_module_name} -> {_fmt_exc(e)}")

            try:
                show_materials, keep_converted = _get_import_preview_settings(context, operator)
                operator_loads_textures = False
                if hasattr(operator, "load_textures"):
                    try:
                        operator_loads_textures = bool(getattr(operator, "load_textures"))
                    except Exception:
                        operator_loads_textures = False
                if operator_loads_textures and not keep_converted:
                    return lod_objects
                stats = _postprocess_imported_material_previews(
                    context,
                    lod_objects or [],
                    show_materials=show_materials,
                    keep_converted_textures=keep_converted,
                )
                _log_import_preview_summary(filepath, stats)
            except Exception as e:
                print("=== Import/Export planner: failed to build material previews ===")
                print(f"{_module_name} -> {_fmt_exc(e)}")

            return lod_objects

        mod.read_file = _wrapped_read_file
        _A3OB_IMPORT_READ_FILE_PATCHES.append((mod, original_read_file))


def _unpatch_a3ob_import_read_file():
    while _A3OB_IMPORT_READ_FILE_PATCHES:
        mod, original_read_file = _A3OB_IMPORT_READ_FILE_PATCHES.pop()
        try:
            mod.read_file = original_read_file
        except Exception:
            pass


def _ensure_a3ob_import_patch_timer():
    if _A3OB_IMPORT_READ_FILE_PATCHES:
        return None

    _patch_a3ob_import_read_file()
    if _A3OB_IMPORT_READ_FILE_PATCHES:
        return None

    return 2.0

class CRAY_PG_IEFileItem(PropertyGroup):
    path: StringProperty(name="File", default="", subtype="FILE_PATH")

class CRAY_PG_IEPlannerSettings(PropertyGroup):
    import_files: CollectionProperty(type=CRAY_PG_IEFileItem)
    import_active_index: IntProperty(default=0)
    import_show_materials: BoolProperty(
        name="Show material textures after import",
        default=True,
        description="Create Image Texture preview nodes for imported A3OB materials so textures are visible in Blender immediately",
    )
    import_keep_converted_textures: BoolProperty(
        name="Keep converted .png next to source .paa",
        default=False,
        description="Save Blender-friendly .png copies near imported .paa files and reuse them on later imports instead of converting again",
    )
    disable_collections_after_import: BoolProperty(
        name="Disable all collections after import",
        default=False,
        description="After batch import finishes, disable all collections in current View Layer",
    )
    disable_mode: EnumProperty(
        name="Disable mode",
        items=(
            ("HIDE", "Hide viewport", "Set Collection.hide_viewport and hide_render"),
            ("EXCLUDE", "Exclude from View Layer", "Set LayerCollection.exclude"),
        ),
        default="HIDE",
    )
    export_mode: EnumProperty(
        name="Export Target",
        items=(
            ("SOURCE", "Back to source", "Export each collection back to its imported .p3d path"),
            ("CUSTOM_DIR", "Custom folder", "Export each collection to a selected folder"),
        ),
        default="SOURCE",
    )
    export_directory: StringProperty(
        name="Export Folder",
        default="",
        subtype="DIR_PATH",
    )
    export_create_bak: BoolProperty(
        name="Create .bak before export",
        default=True,
    )
    export_only_p3d_named: BoolProperty(
        name="Only .p3d-like root collections",
        default=True,
        description="Skip root collections that do not look like imported .p3d collections",
    )
    export_only_split_parts: BoolProperty(
        name="Only split part collections (_01, _02, ...)",
        default=False,
        description="Export only root collections whose names end with a numeric split suffix like _01.p3d",
    )
    export_force_all_lods: BoolProperty(
        name="Force export all LODs (skip validation)",
        default=False,
        description=(
            "Workaround for A3OB exporter: temporarily bypass LOD validation "
            "during batch export to prevent Resolution LODs from being skipped"
        ),
    )

class CRAY_UL_IEFiles(UIList):
    bl_idname = "CRAY_UL_ie_files"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=os.path.basename(item.path) or "<empty>", icon="FILE")
        row.label(text=_norm_path(item.path))

class CRAY_OT_IE_AddFiles(Operator):
    bl_idname = "cray.ie_add_files"
    bl_label = "Add Files"
    bl_options = {"REGISTER", "UNDO"}

    files: CollectionProperty(type=OperatorFileListElement)
    directory: StringProperty(subtype="DIR_PATH")
    filter_glob: StringProperty(default="*.p3d", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        st = context.scene.cray_ie_settings
        added = 0
        skipped = 0
        dir_abs = bpy.path.abspath(self.directory) if self.directory else ""

        for f in self.files:
            fp = os.path.join(dir_abs, f.name) if dir_abs else f.name
            fp = bpy.path.abspath(fp)
            if not fp:
                continue
            if _planner_add_import_file(st, fp):
                added += 1
            else:
                skipped += 1

        if added == 0:
            if skipped > 0:
                self.report({"WARNING"}, "All selected files are already in the planner")
            else:
                self.report({"WARNING"}, "No files added")
        else:
            msg = f"Added {added} file(s)"
            if skipped > 0:
                msg += f", skipped {skipped} duplicate(s)"
            self.report({"INFO"}, msg)
        return {"FINISHED"}

class CRAY_OT_IE_RemoveFile(Operator):
    bl_idname = "cray.ie_remove_file"
    bl_label = "Remove"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = context.scene.cray_ie_settings
        i = st.import_active_index
        if i < 0 or i >= len(st.import_files):
            self.report({"WARNING"}, "Nothing to remove")
            return {"CANCELLED"}
        st.import_files.remove(i)
        st.import_active_index = max(0, min(i, len(st.import_files) - 1))
        return {"FINISHED"}

class CRAY_OT_IE_ClearFiles(Operator):
    bl_idname = "cray.ie_clear_files"
    bl_label = "Clear"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = context.scene.cray_ie_settings
        n = len(st.import_files)
        st.import_files.clear()
        st.import_active_index = 0
        self.report({"INFO"}, f"Cleared {n} file(s)")
        return {"FINISHED"}

class CRAY_OT_IE_ImportBatch(Operator):
    bl_idname = "cray.ie_import_batch"
    bl_label = "Batch Import (A3OB)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = context.scene.cray_ie_settings
        if len(st.import_files) == 0:
            self.report({"ERROR"}, "Import list is empty")
            return {"CANCELLED"}
        if not _has_any_a3ob_import_ops():
            self.report({"ERROR"}, "Arma 3 Object Builder import operators not found")
            return {"CANCELLED"}

        imported = 0
        failed = []
        used_op = None

        for it in st.import_files:
            fp = bpy.path.abspath(it.path)
            if not fp or not os.path.isfile(fp):
                failed.append(f"{it.path} -> file not found")
                continue

            pre_obj_ptrs = {o.as_pointer() for o in bpy.data.objects}
            pre_col_ptrs = {c.as_pointer() for c in bpy.data.collections}
            with _suppress_a3ob_import_tracking():
                res, op_id, err = _call_first_available(
                    _A3OB_IMPORT_CANDIDATES,
                    filepath=fp,
                    load_textures=False,
                )
            if op_id:
                used_op = op_id
            if res is None:
                failed.append(f"{fp} -> {_fmt_exc(err) if err else 'unknown error'}")
            else:
                imported += 1
                imported_objs = [o for o in bpy.data.objects if o.as_pointer() not in pre_obj_ptrs]
                _tag_import_source_on_imported_data(
                    context=context,
                    filepath=fp,
                    imported_objs=imported_objs,
                    pre_collection_ptrs=pre_col_ptrs,
                )
                stats = _postprocess_imported_material_previews(
                    context,
                    imported_objs,
                    show_materials=st.import_show_materials,
                    keep_converted_textures=st.import_keep_converted_textures,
                )
                _log_import_preview_summary(fp, stats)

        if st.disable_collections_after_import:
            _disable_all_collections_in_view_layer(context, st.disable_mode)

        if failed:
            print("=== Batch Import: Failures ===")
            for f in failed:
                print(f)
            self.report({"WARNING"}, f"Imported {imported}, failed {len(failed)} (see System Console)")
        else:
            self.report({"INFO"}, f"Imported {imported} file(s){' via ' + used_op if used_op else ''}")
        return {"FINISHED"}

class CRAY_OT_ModelSplitDuplicateToPart(Operator):
    bl_idname = "cray.model_split_duplicate_to_part"
    bl_label = "Create Part Collection From Selected"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = context.scene.cray_model_split_settings
        selected = [obj for obj in context.selected_objects if obj is not None]
        if not selected:
            self.report({"ERROR"}, "Select the separated objects you want to turn into a new model part")
            return {"CANCELLED"}

        missing_root = []
        roots = {}
        for obj in selected:
            root = _find_p3d_root_collection_for_object(context, obj)
            if root is None:
                missing_root.append(obj.name)
                continue
            roots[root.as_pointer()] = root

        if missing_root:
            preview = ", ".join(missing_root[:5])
            if len(missing_root) > 5:
                preview += ", ..."
            self.report({"ERROR"}, f"Selected objects are not inside a .p3d root collection: {preview}")
            return {"CANCELLED"}

        if len(roots) != 1:
            root_names = ", ".join(sorted({root.name for root in roots.values()}, key=lambda x: x.lower()))
            self.report({"ERROR"}, f"Select objects from exactly one .p3d root collection (found: {root_names})")
            return {"CANCELLED"}

        source_root = next(iter(roots.values()))
        dest_root = _ensure_split_part_root_collection(context, source_root, st.part_number)
        if dest_root is None:
            self.report({"ERROR"}, "Could not create destination part collection")
            return {"CANCELLED"}

        copies_by_source = {}
        created = []
        failed = []

        for src_obj in selected:
            source_path = _best_object_collection_path_under_root(source_root, src_obj)
            if not source_path:
                failed.append(f"{src_obj.name} -> collection path under {source_root.name} not found")
                continue

            dest_leaf = _ensure_split_collection_path(dest_root, source_path)
            if dest_leaf is None:
                failed.append(f"{src_obj.name} -> failed to create destination collection path")
                continue

            dup_obj = _duplicate_object_for_split(src_obj)
            if dup_obj is None:
                failed.append(f"{src_obj.name} -> failed to duplicate object")
                continue

            try:
                dest_leaf.objects.link(dup_obj)
                copies_by_source[src_obj] = dup_obj
                created.append(dup_obj)
            except Exception as e:
                failed.append(f"{src_obj.name} -> {_fmt_exc(e)}")
                try:
                    if bpy.data.objects.get(dup_obj.name) is not None and dup_obj.users == 0:
                        bpy.data.objects.remove(dup_obj)
                except Exception:
                    pass

        _rewire_split_copy_object_refs(copies_by_source)
        _ensure_collection_visible_in_view_layer(context, dest_root)

        if created:
            _deselect_all_in_view_layer(context)
            for obj in created:
                try:
                    obj.hide_set(False)
                except Exception:
                    pass
                try:
                    obj.hide_viewport = False
                except Exception:
                    pass
                try:
                    obj.select_set(True)
                except Exception:
                    pass
            try:
                context.view_layer.objects.active = created[0]
            except Exception:
                pass

        if failed:
            print("=== Model Split: Failures ===")
            for item in failed:
                print(item)

        if not created:
            self.report({"ERROR"}, "No part objects were created")
            return {"CANCELLED"}

        msg = f"Created {len(created)} object copy/copies in {dest_root.name}"
        if failed:
            self.report({"WARNING"}, msg + f", failed {len(failed)} (see System Console)")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}

class CRAY_PG_AssetProxySettings(PropertyGroup):
    target_object: PointerProperty(name="Target Object", type=bpy.types.Object)
    delete_originals: BoolProperty(
        name="Delete originals after convert",
        default=True,
        description="Remove selected placed objects after proxy creation",
    )


_NH_TEMP_ASSET_LIBRARY_NAME = "NH Temp Asset Library"
_NH_TEMP_ASSET_SCENE_NAME = "NH Asset Library Scene"

def _iter_p3d_files_in_folder(folder_abs: str):
    out = []
    for root, _, files in os.walk(folder_abs):
        for fn in files:
            if fn.lower().endswith('.p3d'):
                out.append(os.path.join(root, fn))
    out.sort(key=lambda x: x.lower())
    return out

def _ensure_temp_asset_scene():
    scenes = getattr(bpy.data, "scenes", None)
    if scenes is None:
        raise RuntimeError("Blender scenes are not available right now")
    scene = scenes.get(_NH_TEMP_ASSET_SCENE_NAME)
    if scene is None:
        scene = scenes.new(_NH_TEMP_ASSET_SCENE_NAME)
    return scene


def _ensure_temp_asset_library_root(context):
    col = bpy.data.collections.get(_NH_TEMP_ASSET_LIBRARY_NAME)
    if col is None:
        col = bpy.data.collections.new(_NH_TEMP_ASSET_LIBRARY_NAME)

    asset_scene = _ensure_temp_asset_scene()

    for scene in _iter_safe_scenes():
        try:
            if scene != asset_scene and any(ch == col for ch in scene.collection.children):
                scene.collection.children.unlink(col)
        except Exception:
            pass

    try:
        if all(ch != col for ch in asset_scene.collection.children):
            asset_scene.collection.children.link(col)
    except Exception:
        pass

    try:
        col.hide_viewport = True
        col.hide_render = True
    except Exception:
        pass
    return col

def _safe_unlink_collection_from_parents(col):
    scene = bpy.context.scene
    if scene is not None:
        for parent in [scene.collection] + list(bpy.data.collections):
            try:
                if any(ch == col for ch in parent.children):
                    parent.children.unlink(col)
            except Exception:
                pass

def _remove_collection_tree(col):
    for child in list(col.children):
        _remove_collection_tree(child)
    for obj in list(col.objects):
        try:
            col.objects.unlink(obj)
        except Exception:
            pass
        if bpy.data.objects.get(obj.name) is not None and obj.users == 0:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
    _safe_unlink_collection_from_parents(col)
    if bpy.data.collections.get(col.name) is not None:
        try:
            bpy.data.collections.remove(col)
        except Exception:
            pass

def _clear_temp_asset_library(context):
    col = bpy.data.collections.get(_NH_TEMP_ASSET_LIBRARY_NAME)
    if col is None:
        return 0
    child_count = len(list(_iter_collection_tree(col)))
    _remove_collection_tree(col)
    scenes = getattr(bpy.data, "scenes", None)
    asset_scene = scenes.get(_NH_TEMP_ASSET_SCENE_NAME) if scenes is not None else None
    if asset_scene is not None:
        try:
            if len(asset_scene.collection.children) == 0:
                scenes.remove(asset_scene)
        except Exception:
            pass
    return child_count

def _generate_asset_preview_safe(id_data):
    if id_data is None:
        return
    try:
        gen = getattr(id_data, "asset_generate_preview", None)
        if callable(gen):
            gen()
            return
    except Exception:
        pass
    try:
        with bpy.context.temp_override(id=id_data):
            bpy.ops.ed.lib_id_generate_preview()
    except Exception:
        try:
            override = bpy.context.copy()
            override["id"] = id_data
            bpy.ops.ed.lib_id_generate_preview(override)
        except Exception:
            pass


def _mark_object_as_asset_safe(obj):
    if obj is None:
        return
    try:
        obj.asset_mark()
    except Exception:
        pass
    _generate_asset_preview_safe(obj)


def _mark_collection_as_asset_safe(collection, catalog_id=None):
    if collection is None:
        return
    try:
        collection.asset_mark()
    except Exception:
        pass
    if catalog_id:
        try:
            asset_data = getattr(collection, "asset_data", None)
            if asset_data is not None:
                asset_data.catalog_id = str(catalog_id)
            else:
                collection["catalog_id"] = str(catalog_id)
        except Exception:
            pass
    _generate_asset_preview_safe(collection)


def _mark_objects_in_collection_as_assets_safe(collection):
    if collection is None:
        return
    seen = set()
    for obj in _collect_collection_objects_recursive(collection):
        if obj is None:
            continue
        ptr = obj.as_pointer()
        if ptr in seen:
            continue
        seen.add(ptr)
        if obj.type not in {"MESH", "EMPTY", "CURVE", "ARMATURE"}:
            continue
        _mark_object_as_asset_safe(obj)

def _move_import_result_into_asset_library(context, filepath, pre_obj_ptrs, pre_col_ptrs, asset_root, catalog_id=None):
    imported_objs = [o for o in bpy.data.objects if o.as_pointer() not in pre_obj_ptrs]
    _tag_import_source_on_imported_data(
        context=context,
        filepath=filepath,
        imported_objs=imported_objs,
        pre_collection_ptrs=pre_col_ptrs,
    )

    new_cols = [c for c in bpy.data.collections if c.as_pointer() not in pre_col_ptrs]
    candidate_cols = []
    for col in new_cols:
        if col == asset_root:
            continue
        if not _collection_has_any_object_ptr(col, {o.as_pointer() for o in imported_objs}):
            continue
        candidate_cols.append(col)

    moved = 0
    if candidate_cols:
        root_candidates = []
        candidate_ptrs = {c.as_pointer() for c in candidate_cols}
        for col in candidate_cols:
            is_child = False
            for other in candidate_cols:
                if other == col:
                    continue
                if _find_collection_path(other, col.as_pointer()):
                    is_child = True
                    break
            if not is_child:
                root_candidates.append(col)
        for col in root_candidates:
            try:
                _safe_unlink_collection_from_parents(col)
            except Exception:
                pass
            try:
                if all(ch != col for ch in asset_root.children):
                    asset_root.children.link(col)
            except Exception:
                pass
            try:
                col[_IE_SOURCE_PATH_KEY] = _norm_path(bpy.path.abspath(filepath))
            except Exception:
                pass
            _mark_collection_as_asset_safe(col, catalog_id=catalog_id)
            moved += 1
    else:
        name = os.path.splitext(os.path.basename(filepath))[0]
        col = bpy.data.collections.new(name)
        asset_root.children.link(col)
        for obj in imported_objs:
            for parent in list(obj.users_collection):
                try:
                    parent.objects.unlink(obj)
                except Exception:
                    pass
            try:
                col.objects.link(obj)
            except Exception:
                pass
        try:
            col[_IE_SOURCE_PATH_KEY] = _norm_path(bpy.path.abspath(filepath))
        except Exception:
            pass
        _mark_collection_as_asset_safe(col, catalog_id=catalog_id)
        moved = 1

    return moved, len(imported_objs)


class CRAY_PG_AssetLibrarySettings(PropertyGroup):
    folder: StringProperty(name="P3D Folder", default="", subtype="DIR_PATH")
    import_first_lod_only: BoolProperty(
        name="Import first LOD only",
        default=True,
    )
    clear_previous_temp_library: BoolProperty(
        name="Clear previous temp library",
        default=True,
    )

class CRAY_OT_AssetLibraryBuildFromFolder(Operator):
    bl_idname = "cray.asset_library_build_folder"
    bl_label = "Build From Folder"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = context.scene.cray_asset_library_settings
        if not _has_any_a3ob_import_ops():
            self.report({"ERROR"}, "Arma 3 Object Builder import operators not found")
            return {"CANCELLED"}
        folder_abs = bpy.path.abspath(st.folder)
        if not folder_abs or not os.path.isdir(folder_abs):
            self.report({"ERROR"}, "P3D folder not found")
            return {"CANCELLED"}

        files = _iter_p3d_files_in_folder(folder_abs)
        if not files:
            self.report({"ERROR"}, "No .p3d files found in folder")
            return {"CANCELLED"}

        return _build_temp_asset_library_from_paths(self, context, files)

class CRAY_OT_AssetLibraryBuildFromFiles(Operator):
    bl_idname = "cray.asset_library_build_files"
    bl_label = "Build From Files"
    bl_options = {"REGISTER", "UNDO"}

    files: CollectionProperty(type=OperatorFileListElement)
    directory: StringProperty(subtype="DIR_PATH")
    filter_glob: StringProperty(default="*.p3d", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not _has_any_a3ob_import_ops():
            self.report({"ERROR"}, "Arma 3 Object Builder import operators not found")
            return {"CANCELLED"}
        dir_abs = bpy.path.abspath(self.directory) if self.directory else ""
        files = []
        seen = set()
        for item in self.files:
            fp = os.path.join(dir_abs, item.name) if dir_abs else item.name
            fp = os.path.abspath(bpy.path.abspath(fp))
            fp_key = os.path.normcase(fp)
            if fp and os.path.isfile(fp) and fp.lower().endswith('.p3d') and fp_key not in seen:
                seen.add(fp_key)
                files.append(fp)
        if not files:
            self.report({"ERROR"}, "No .p3d files selected")
            return {"CANCELLED"}
        return _build_temp_asset_library_from_paths(self, context, files)

class CRAY_OT_AssetLibraryClear(Operator):
    bl_idname = "cray.asset_library_clear"
    bl_label = "Clear Temp Asset Library"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = _clear_temp_asset_library(context)
        self.report({"INFO"}, f"Cleared temp asset library ({removed} collection(s))")
        return {"FINISHED"}



def _get_asset_browser_space_from_area(area):
    if area is None:
        return None
    try:
        for space in area.spaces:
            if getattr(space, "type", None) == "FILE_BROWSER":
                return space
    except Exception:
        pass
    return None


def _ensure_asset_catalog_and_activate(context, area, catalog_name=_ASSET_CATALOG_NAME, preferred_catalog_id=None):
    if area is None:
        return None
    space = _get_asset_browser_space_from_area(area)
    if space is None:
        return None

    params = getattr(space, "params", None)
    if params is not None:
        try:
            params.asset_library_reference = "LOCAL"
        except Exception:
            pass

    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None) if window else None
    region = next((r for r in getattr(area, "regions", []) if getattr(r, "type", None) == "WINDOW"), None)

    try:
        override = context.copy()
        override["window"] = window
        override["screen"] = screen
        override["area"] = area
        if region is not None:
            override["region"] = region
        with context.temp_override(**{k: v for k, v in override.items() if v is not None}):
            for kwargs in ({}, {"parent_path": catalog_name}):
                try:
                    bpy.ops.asset.catalog_new(**kwargs)
                    break
                except Exception:
                    pass
            try:
                bpy.ops.asset.catalogs_save()
            except Exception:
                pass
    except Exception:
        pass

    catalog_id = str(preferred_catalog_id or "")
    if not catalog_id and params is not None:
        try:
            current_catalog_id = str(getattr(params, "catalog_id", "") or "")
            if current_catalog_id:
                catalog_id = current_catalog_id
        except Exception:
            pass

    if not catalog_id:
        catalog_id = _ASSET_CATALOG_FALLBACK_ID

    if params is not None:
        for attr_name, attr_value in (
            ("catalog_id", str(catalog_id)),
            ("catalog_path", str(catalog_name)),
            ("display_type", "THUMBNAIL"),
            ("asset_library_reference", "LOCAL"),
            ("import_method", "APPEND_REUSE"),
        ):
            try:
                setattr(params, attr_name, attr_value)
            except Exception:
                pass

    try:
        area.tag_redraw()
    except Exception:
        pass
    return str(catalog_id)


def _switch_bottom_area_to_asset_browser(context):
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None) if window else None
    if screen is None:
        return False

    ignore_types = {"TOPBAR", "STATUSBAR", "PREFERENCES"}
    areas = [a for a in screen.areas if getattr(a, "type", None) not in ignore_types]
    if not areas:
        return False

    def _priority(area):
        tp = getattr(area, "type", "")
        if tp == "TIMELINE":
            return 0
        if tp in {"DOPESHEET_EDITOR", "NLA_EDITOR", "GRAPH_EDITOR"}:
            return 1
        if tp == "FILE_BROWSER":
            return 2
        return 3

    # Pick the lowest area. For equal y, prefer timeline/animation editors.
    area = sorted(areas, key=lambda a: (a.y, _priority(a), -a.width, -a.height))[0]

    try:
        area.type = "FILE_BROWSER"
    except Exception:
        # Sometimes direct area-type switching does not work on the first try.
        try:
            override = context.copy()
            override["window"] = window
            override["screen"] = screen
            override["area"] = area
            override["region"] = next((r for r in area.regions if r.type == "WINDOW"), None)
            with context.temp_override(**{k: v for k, v in override.items() if v is not None}):
                bpy.ops.screen.space_type_set_or_cycle(space_type="FILE_BROWSER")
        except Exception:
            return False

    ok = False
    try:
        area.ui_type = "ASSETS"
        ok = True
    except Exception:
        pass

    for space in area.spaces:
        try:
            if getattr(space, "type", None) != "FILE_BROWSER":
                continue
            try:
                space.browse_mode = "ASSETS"
            except Exception:
                pass
            params = getattr(space, "params", None)
            if params is not None:
                try:
                    params.display_type = "THUMBNAIL"
                except Exception:
                    pass
                for attr_name, attr_value in (
                    ("asset_library_reference", "LOCAL"),
                    ("catalog_id", ""),
                    ("import_method", "APPEND_REUSE"),
                ):
                    try:
                        setattr(params, attr_name, attr_value)
                    except Exception:
                        pass
            ok = True
        except Exception:
            pass

    try:
        for region in area.regions:
            region.tag_redraw()
        area.tag_redraw()
    except Exception:
        pass
    return area if ok else None

def _build_temp_asset_library_from_paths(op, context, filepaths):
    st = context.scene.cray_asset_library_settings

    unique_filepaths = []
    seen_paths = set()
    for fp in filepaths:
        fp_abs = os.path.abspath(bpy.path.abspath(fp))
        fp_key = os.path.normcase(fp_abs)
        if fp_key in seen_paths:
            continue
        seen_paths.add(fp_key)
        unique_filepaths.append(fp_abs)
    filepaths = unique_filepaths

    if st.clear_previous_temp_library:
        _clear_temp_asset_library(context)

    asset_root = _ensure_temp_asset_library_root(context)
    asset_browser_area = _switch_bottom_area_to_asset_browser(context)
    asset_catalog_id = _ensure_asset_catalog_and_activate(context, asset_browser_area, _ASSET_CATALOG_NAME)
    imported = 0
    moved_collections = 0
    failed = []

    for fp in filepaths:
        pre_obj_ptrs = {o.as_pointer() for o in bpy.data.objects}
        pre_col_ptrs = {c.as_pointer() for c in bpy.data.collections}
        with _suppress_a3ob_import_tracking():
            res, op_id, err = _call_first_available(
                _A3OB_IMPORT_CANDIDATES,
                filepath=fp,
                first_lod_only=st.import_first_lod_only,
                absolute_paths=True,
                enclose=True,
                groupby="TYPE",
                additional_data_allowed=True,
                additional_data={"PROPS", "SELECTIONS"},
                validate_meshes=False,
                proxy_action="SEPARATE",
                translate_selections=False,
                cleanup_empty_selections=False,
                load_textures=False,
            )
        if res is None:
            failed.append(f"{os.path.basename(fp)}: {_fmt_exc(err) if err else 'import failed'}")
            continue
        imported += 1
        moved, _ = _move_import_result_into_asset_library(context, fp, pre_obj_ptrs, pre_col_ptrs, asset_root, catalog_id=asset_catalog_id)
        moved_collections += moved

    if failed:
        print('=== P3D Asset Library: Failures ===')
        for item in failed:
            print(item)

    if imported == 0:
        op.report({"ERROR"}, "No assets imported (see System Console)")
        return {"CANCELLED"}

    switched_to_assets = asset_browser_area is not None
    if asset_browser_area is not None:
        _ensure_asset_catalog_and_activate(context, asset_browser_area, _ASSET_CATALOG_NAME, preferred_catalog_id=asset_catalog_id)

    msg = f"Imported {imported} file(s), asset entries: {moved_collections}"
    if switched_to_assets:
        msg += ", Asset Browser opened below"

    if failed:
        op.report({"WARNING"}, msg + f", failed: {len(failed)}")
    else:
        op.report({"INFO"}, msg)
    return {"FINISHED"}

class CRAY_OT_ConvertSelectedToProxies(Operator):
    bl_idname = "cray.convert_selected_to_proxies"
    bl_label = "Convert Selected Assets To Proxies"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = context.scene.cray_asset_proxy_settings
        target_obj = _pick_proxy_target_object(context, st.target_object)
        if target_obj is None or target_obj.type != "MESH":
            self.report({"ERROR"}, "Target object must be a mesh")
            return {"CANCELLED"}

        selected = [o for o in context.selected_objects if o != target_obj]
        if not selected:
            self.report({"ERROR"}, "Select placed asset objects together with target object")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as e:
                self.report({"ERROR"}, f"Failed to switch to Object Mode: {_fmt_exc(e)}")
                return {"CANCELLED"}

        target_collection = None
        if target_obj.users_collection:
            target_collection = target_obj.users_collection[0]
        else:
            target_collection = context.scene.collection

        created = 0
        removed = 0
        skipped = []
        next_index = _next_proxy_index_for_parent(target_obj)
        to_remove = []

        for obj in selected:
            src = _resolve_object_source_p3d(obj)
            if not src:
                skipped.append(f"{obj.name}: no source .p3d path")
                continue

            proxy_mesh = get_proxy_mesh()
            proxy_obj = bpy.data.objects.new(f"proxy_{obj.name}", proxy_mesh)
            target_collection.objects.link(proxy_obj)
            _build_proxy_from_object_instance(proxy_obj, obj, target_obj, next_index)
            try:
                set_a3ob_proxy_properties(proxy_obj, src, next_index)
            except Exception as e:
                try:
                    bpy.data.objects.remove(proxy_obj, do_unlink=True)
                except Exception:
                    pass
                skipped.append(f"{obj.name}: {_fmt_exc(e)}")
                continue

            created += 1
            next_index += 1
            if st.delete_originals:
                to_remove.append(obj)

        for obj in sorted(to_remove, key=_obj_depth, reverse=True):
            if bpy.data.objects.get(obj.name) is None:
                continue
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
                removed += 1
            except Exception as e:
                skipped.append(f"{obj.name}: delete failed: {_fmt_exc(e)}")

        if skipped:
            print("=== Convert Selected To Proxies: Skipped ===")
            for item in skipped:
                print(item)

        if created == 0:
            self.report({"ERROR"}, "No proxies created (see System Console)")
            return {"CANCELLED"}

        msg = f"Created {created} proxy(s) for '{target_obj.name}'"
        if st.delete_originals:
            msg += f", removed {removed} original(s)"
        if skipped:
            msg += f", skipped {len(skipped)} (see System Console)"
            self.report({"WARNING"}, msg)
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class CRAY_OT_FixShadingByPipeline(Operator):
    bl_idname = "cray.fix_shading_by_pipeline"
    bl_label = "Fix Shading (Merge + Smooth)"
    bl_description = (
        "Merge the selected mesh objects if needed, clear split normals, recalculate normals, "
        "and apply Shade Smooth"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        active_before = context.view_layer.objects.active
        selected_meshes = [obj for obj in context.selected_objects if obj and obj.type == "MESH"]
        if active_before is not None and active_before.type == "MESH" and active_before not in selected_meshes:
            selected_meshes.insert(0, active_before)

        if not selected_meshes:
            self.report({"ERROR"}, "Select at least one mesh object")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as e:
                self.report({"ERROR"}, f"Failed to switch to Object Mode: {_fmt_exc(e)}")
                return {"CANCELLED"}

        active_mesh = active_before if active_before in selected_meshes else selected_meshes[0]
        _deselect_all_in_view_layer(context)
        for obj in selected_meshes:
            try:
                obj.hide_set(False)
            except Exception:
                pass
            try:
                obj.hide_viewport = False
            except Exception:
                pass
            try:
                obj.select_set(True)
            except Exception:
                pass
        try:
            context.view_layer.objects.active = active_mesh
        except Exception:
            pass

        joined_count = len(selected_meshes)
        if joined_count > 1:
            try:
                bpy.ops.object.join()
            except Exception as e:
                self.report({"ERROR"}, f"Failed to join selected meshes: {_fmt_exc(e)}")
                return {"CANCELLED"}
            active_mesh = context.view_layer.objects.active
        else:
            active_mesh = active_mesh or context.view_layer.objects.active

        if active_mesh is None or active_mesh.type != "MESH":
            self.report({"ERROR"}, "Active object must be a mesh after merge")
            return {"CANCELLED"}

        try:
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
        except Exception:
            pass

        try:
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_mode(type="FACE")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.normals_make_consistent(inside=False)
            bpy.ops.mesh.faces_shade_smooth()
        except Exception as e:
            self.report({"ERROR"}, f"Failed to run base shading pipeline: {_fmt_exc(e)}")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Shading fix done on '{active_mesh.name}': joined {joined_count}, Shade Smooth applied",
        )
        return {"FINISHED"}


def _repair_invalid_a3ob_selection_links(obj):
    if obj is None or obj.type != "MESH" or obj.data is None:
        raise RuntimeError("Target object must be a mesh")

    group_specs = sorted(
        [(int(vg.index), vg.name) for vg in obj.vertex_groups],
        key=lambda item: item[0],
    )
    if not group_specs:
        return {
            "invalid_refs_removed": 0,
            "zero_refs_removed": 0,
            "verts_touched": 0,
            "groups_rebuilt": 0,
            "reindexed_groups": False,
        }

    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        layer = bm.verts.layers.deform.verify()

        old_to_new = {old_idx: new_idx for new_idx, (old_idx, _name) in enumerate(group_specs)}
        valid_old_indices = set(old_to_new.keys())
        reindexed_groups = any(old_idx != new_idx for old_idx, new_idx in old_to_new.items())

        invalid_refs_removed = 0
        zero_refs_removed = 0
        verts_touched = 0
        dense_weights_by_vert = {}

        for vert in bm.verts:
            deform = vert[layer]
            items = list(deform.items())
            if not items:
                continue

            filtered = {}
            changed = False
            for idx, weight in items:
                idx = int(idx)
                weight = float(weight)
                if idx not in valid_old_indices:
                    invalid_refs_removed += 1
                    changed = True
                    continue
                if weight <= 0.0:
                    zero_refs_removed += 1
                    changed = True
                    continue
                new_idx = old_to_new[idx]
                prev = filtered.get(new_idx)
                if prev is None or weight > prev:
                    filtered[new_idx] = weight
                if new_idx != idx:
                    changed = True

            if filtered:
                dense_weights_by_vert[vert.index] = sorted(filtered.items())

            if not changed and not reindexed_groups:
                continue

            verts_touched += 1

        if invalid_refs_removed == 0 and zero_refs_removed == 0 and not reindexed_groups:
            return {
                "invalid_refs_removed": 0,
                "zero_refs_removed": 0,
                "verts_touched": 0,
                "groups_rebuilt": 0,
                "reindexed_groups": False,
            }

        existing_groups = list(obj.vertex_groups)
        for vg in existing_groups:
            obj.vertex_groups.remove(vg)

        rebuilt_groups = []
        for _old_idx, group_name in group_specs:
            rebuilt_groups.append(obj.vertex_groups.new(name=group_name))

        for vert_idx, weights in dense_weights_by_vert.items():
            for dense_idx, weight in weights:
                rebuilt_groups[dense_idx].add([vert_idx], weight, "REPLACE")

        return {
            "invalid_refs_removed": invalid_refs_removed,
            "zero_refs_removed": zero_refs_removed,
            "verts_touched": verts_touched,
            "groups_rebuilt": len(rebuilt_groups),
            "reindexed_groups": reindexed_groups,
        }
    finally:
        bm.free()


class CRAY_OT_RepairA3OBSelections(Operator):
    bl_idname = "cray.repair_a3ob_selections"
    bl_label = "Repair Invalid A3OB Selections"
    bl_description = (
        "Scan selected mesh objects and rebuild broken A3OB vertex-group links, removing invalid or zero-weight references"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        active_before = context.view_layer.objects.active
        selected_meshes = [
            obj for obj in context.selected_objects
            if obj is not None and obj.type == "MESH" and obj.data is not None
        ]
        if active_before is not None and active_before.type == "MESH" and active_before not in selected_meshes:
            selected_meshes.insert(0, active_before)

        if not selected_meshes:
            self.report({"ERROR"}, "Select at least one mesh object")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as e:
                self.report({"ERROR"}, f"Failed to switch to Object Mode: {_fmt_exc(e)}")
                return {"CANCELLED"}

        total_invalid = 0
        total_zero = 0
        total_verts = 0
        touched_objects = 0
        rebuilt_groups = 0
        reindexed_objects = 0
        failed = []

        for obj in selected_meshes:
            try:
                stats = _repair_invalid_a3ob_selection_links(obj)
            except Exception as e:
                failed.append(f"{obj.name}: {_fmt_exc(e)}")
                continue

            total_invalid += stats["invalid_refs_removed"]
            total_zero += stats["zero_refs_removed"]
            total_verts += stats["verts_touched"]
            rebuilt_groups += stats["groups_rebuilt"]
            if stats["reindexed_groups"]:
                reindexed_objects += 1
            if stats["verts_touched"] > 0:
                touched_objects += 1

        if failed:
            print("=== Repair Invalid A3OB Selections: Failed ===")
            for item in failed:
                print(item)

        if total_invalid == 0 and total_zero == 0 and rebuilt_groups == 0:
            msg = f"No invalid A3OB selection links found on {len(selected_meshes)} mesh object(s)"
            if failed:
                self.report({"WARNING"}, msg + f", failed: {len(failed)} (see System Console)")
            else:
                self.report({"INFO"}, msg)
            return {"FINISHED"}

        msg = (
            f"Repaired A3OB selections on {touched_objects} object(s): "
            f"removed {total_invalid} invalid refs and {total_zero} zero-weight refs "
            f"across {total_verts} vertex/vertices, rebuilt {rebuilt_groups} groups"
        )
        if reindexed_objects > 0:
            msg += f", reindexed groups on {reindexed_objects} object(s)"
        if failed:
            self.report({"WARNING"}, msg + f", failed: {len(failed)} (see System Console)")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class CRAY_OT_CreatePlainAxisPivot(Operator):
    bl_idname = "cray.create_plain_axis_pivot"
    bl_label = "Create Plain Axis Pivot"
    bl_description = (
        "In Edit Mode, use the selected vertex as a pivot, create a Plain Axes helper, "
        "and add Child Of constraints so moving the helper moves the whole imported model"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        source_obj = context.view_layer.objects.active
        if source_obj is None or source_obj.type != "MESH" or context.mode != "EDIT_MESH" or source_obj.mode != "EDIT":
            self.report({"ERROR"}, "Active object must be a mesh in Edit Mode")
            return {"CANCELLED"}

        try:
            world_location = _collect_single_selected_vertex_world_point(source_obj)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        root_collection, used_p3d_root = _pick_plain_axis_root_collection(context, source_obj)
        if root_collection is None:
            self.report({"ERROR"}, "Could not determine a target collection for Plain Axis")
            return {"CANCELLED"}

        helper_obj = None
        constrained = 0
        failed = []
        replaced_helpers = 0
        restored_edit_mode = False

        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception as e:
            self.report({"ERROR"}, f"Failed to switch to Object Mode: {_fmt_exc(e)}")
            return {"CANCELLED"}

        try:
            replaced_helpers, _removed_constraints = _clear_plain_axis_helpers_in_collection(context, root_collection)
            helper_obj = _create_plain_axis_helper(context, root_collection, source_obj, world_location)
            target_objects = _collect_plain_axis_target_objects(root_collection, helper_obj=helper_obj)
            if not target_objects:
                raise RuntimeError(f"No movable root objects found in collection {root_collection.name}")

            context.view_layer.update()

            enabled_axes = (
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

            for obj in target_objects:
                try:
                    con = obj.constraints.new(type="CHILD_OF")
                    con.name = _PLAIN_AXIS_CONSTRAINT_NAME
                    con.target = helper_obj
                    for attr in enabled_axes:
                        try:
                            setattr(con, attr, True)
                        except Exception:
                            pass
                    context.view_layer.update()
                    _apply_child_of_inverse_with_fallback(context, obj, con)
                    constrained += 1
                except Exception as e:
                    failed.append(f"{obj.name}: {_fmt_exc(e)}")

            if constrained == 0:
                raise RuntimeError("Failed to add Child Of constraints to target objects")
        except Exception as e:
            if helper_obj is not None:
                _clear_plain_axis_helpers(context, [helper_obj])
            try:
                _try_restore_edit_mode(context, source_obj)
                restored_edit_mode = True
            except Exception:
                pass
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        if not restored_edit_mode:
            _try_restore_edit_mode(context, source_obj)

        if failed:
            print("=== Plain Axis Pivot: Failed Objects ===")
            for item in failed:
                print(item)

        scope_label = ".p3d root collection" if used_p3d_root else "active object collection"
        msg = (
            f"Created Plain Axis in {root_collection.name}: constrained {constrained} root object(s) "
            f"using {scope_label}"
        )
        if replaced_helpers > 0:
            msg += f", replaced {replaced_helpers} existing helper(s)"
        if failed:
            self.report({"WARNING"}, msg + f", failed {len(failed)} object(s) (see System Console)")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class CRAY_OT_ClearPlainAxisPivots(Operator):
    bl_idname = "cray.clear_plain_axis_pivots"
    bl_label = "Delete All Plain Axes"
    bl_description = "Delete all Plain Axes helpers created by this tool and remove their Child Of constraints"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        active_before = context.view_layer.objects.active
        restore_edit_mode = (
            context.mode == "EDIT_MESH"
            and active_before is not None
            and active_before.type == "MESH"
        )

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as e:
                self.report({"ERROR"}, f"Failed to switch to Object Mode: {_fmt_exc(e)}")
                return {"CANCELLED"}

        helpers = [obj for obj in bpy.data.objects if _is_plain_axis_helper(obj)]
        if not helpers:
            if restore_edit_mode:
                _try_restore_edit_mode(context, active_before)
            self.report({"INFO"}, "No Plain Axis helpers found in the scene")
            return {"FINISHED"}

        removed_helpers, removed_constraints = _clear_plain_axis_helpers(context, helpers)

        if restore_edit_mode:
            _try_restore_edit_mode(context, active_before)

        self.report(
            {"INFO"},
            f"Deleted {removed_helpers} Plain Axis helper(s) and removed {removed_constraints} Child Of constraint(s)",
        )
        return {"FINISHED"}


class CRAY_OT_IE_ExportCollectionsBatch(Operator):
    bl_idname = "cray.ie_export_collections_batch"
    bl_label = "Batch Export Collections (A3OB)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = context.scene.cray_ie_settings
        has_export = any(_op_handle(op) is not None for op, _ in _A3OB_EXPORT_CANDIDATES)
        if not has_export:
            self.report({"ERROR"}, "Arma 3 Object Builder export operators not found")
            return {"CANCELLED"}

        export_dir = ""
        if st.export_mode == "CUSTOM_DIR":
            export_dir = bpy.path.abspath(st.export_directory)
            if not export_dir:
                self.report({"ERROR"}, "Export folder is empty")
                return {"CANCELLED"}
            try:
                os.makedirs(export_dir, exist_ok=True)
            except Exception as e:
                self.report({"ERROR"}, f"Failed to create export folder: {_fmt_exc(e)}")
                return {"CANCELLED"}

        import_basename_map = _build_ie_import_basename_map(st)

        candidates = []
        for col in context.scene.collection.children:
            if not _collection_has_any_mesh(col):
                continue
            source_hint = _resolve_collection_source_path(col, import_basename_map)
            if st.export_only_p3d_named and not source_hint and not _looks_like_p3d_collection_name(col.name):
                continue
            if st.export_only_split_parts and not _looks_like_split_part_collection_name(col.name):
                continue
            candidates.append((col, source_hint))

        if not candidates:
            self.report({"ERROR"}, "No exportable root collections found")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as e:
                self.report({"ERROR"}, f"Failed to switch to Object Mode: {_fmt_exc(e)}")
                return {"CANCELLED"}

        prev_selected_names = [o.name for o in context.selected_objects]
        prev_active_name = context.view_layer.objects.active.name if context.view_layer.objects.active else None

        exported = 0
        backups = 0
        failed = []
        partial_lod_exports = []
        used_op = None
        used_targets = set()

        for col, source_hint in candidates:
            objects = _collect_collection_objects_recursive(col)
            if not objects:
                failed.append(f"{col.name} -> no objects")
                continue

            source_path = _resolve_collection_source_path(col, import_basename_map) or source_hint
            if st.export_mode == "SOURCE":
                if not source_path:
                    failed.append(f"{col.name} -> missing source path (import with this addon first)")
                    continue
                filepath = bpy.path.abspath(source_path)
                source_dir = os.path.dirname(filepath)
                if source_dir and not os.path.isdir(source_dir):
                    failed.append(f"{col.name} -> source folder not found: {source_dir}")
                    continue
            else:
                filename = _export_filename_for_collection(col, source_path)
                filepath = bpy.path.abspath(os.path.join(export_dir, filename))

            target_key = os.path.normcase(os.path.normpath(filepath))
            if st.export_mode == "SOURCE":
                if target_key in used_targets:
                    failed.append(f"{col.name} -> duplicate source path in batch: {filepath}")
                    continue
            else:
                if target_key in used_targets:
                    base, ext = os.path.splitext(filepath)
                    idx = 1
                    while target_key in used_targets:
                        filepath = f"{base}_{idx:03d}{ext}"
                        target_key = os.path.normcase(os.path.normpath(filepath))
                        idx += 1
            used_targets.add(target_key)

            if st.export_create_bak and os.path.isfile(filepath):
                try:
                    shutil.copy2(filepath, filepath + ".bak")
                    backups += 1
                except Exception as e:
                    failed.append(f"{col.name} -> backup failed: {_fmt_exc(e)}")
                    continue

            _ensure_collection_visible_in_view_layer(context, col)
            _deselect_all_in_view_layer(context)

            selectable = []
            for obj in objects:
                try:
                    obj.hide_set(False)
                except Exception:
                    pass
                try:
                    obj.hide_viewport = False
                except Exception:
                    pass
                try:
                    obj.select_set(True)
                    selectable.append(obj)
                except Exception:
                    continue

            if not selectable:
                failed.append(f"{col.name} -> no selectable objects in current View Layer")
                continue

            active_obj = None
            for obj in selectable:
                if obj.type == "MESH":
                    active_obj = obj
                    break
            if active_obj is None:
                active_obj = selectable[0]

            try:
                context.view_layer.objects.active = active_obj
            except Exception:
                pass

            expected_lod_entries = _collect_expected_lod_entries(selectable)

            _, op_id, err = _call_export_with_optional_relaxed_validation(
                force_all_lods=bool(st.export_force_all_lods),
                filepath=filepath,
                use_selection=True,
                visible_only=False,
                relative_paths=True,
                preserve_normals=True,
                validate_meshes=False,
                apply_transforms=True,
                apply_modifiers=True,
                sort_sections=True,
                lod_collisions="SKIP",
                validate_lods=False,
                validate_lods_warning_errors=False,
                generate_components=True,
                force_lowercase=True,
            )
            if op_id:
                used_op = op_id
                exported += 1
                if expected_lod_entries:
                    try:
                        exported_lod_entries = _read_exported_lod_entries(filepath)
                        missing_keys = _report_missing_lods_in_console(
                            collection_name=col.name,
                            filepath=filepath,
                            expected_entries=expected_lod_entries,
                            exported_entries=exported_lod_entries,
                        )
                        if missing_keys:
                            partial_lod_exports.append((col.name, filepath, len(missing_keys), len(expected_lod_entries)))
                    except Exception as e:
                        print("=== Batch Export Collections: LOD post-check failed ===")
                        print(f"{col.name} -> {_fmt_exc(e)}")
            else:
                failed.append(f"{col.name} -> {_fmt_exc(err) if err else 'export failed'}")

        _deselect_all_in_view_layer(context)
        for name in prev_selected_names:
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            try:
                obj.select_set(True)
            except Exception:
                pass

        if prev_active_name:
            prev_active = bpy.data.objects.get(prev_active_name)
            if prev_active is not None:
                try:
                    context.view_layer.objects.active = prev_active
                except Exception:
                    pass

        if failed:
            print("=== Batch Export Collections: Failures ===")
            for f in failed:
                print(f)

        if partial_lod_exports:
            print("=== Batch Export Collections: Partial LOD exports ===")
            for col_name, fp, miss_count, expected_count in partial_lod_exports:
                print(
                    f"{col_name} -> missing {miss_count}/{expected_count} expected unique LOD signatures "
                    f"({fp})"
                )

        msg = (
            f"Exported {exported}/{len(candidates)} collections, "
            f"backups {backups}, failed {len(failed)}"
        )
        if partial_lod_exports:
            msg += f", partial LOD exports {len(partial_lod_exports)}"

        if failed or partial_lod_exports:
            self.report({"WARNING"}, msg + " (see System Console)")
        else:
            suffix = f" via {used_op}" if used_op else ""
            self.report({"INFO"}, msg + suffix)
        return {"FINISHED"}


# ------------------------------------------------------------------------
#  Panels (separate blocks)
# ------------------------------------------------------------------------

class CRAY_PT_ClutterProxiesPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_panel"
    bl_label = "Clutter Proxies (DayZ)"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        s = context.scene.cray_settings
        edit_obj = getattr(context, "edit_object", None)
        edit_name = edit_obj.name if edit_obj is not None and edit_obj.type == "MESH" else "<enter Edit Mode on mesh>"

        col = layout.column(align=True)
        col.label(text="Selection")
        col.label(text=edit_name, icon="MESH_DATA")
        col.label(text="Selected polygons in Edit Mode are used", icon="INFO")

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Config .cpp")
        col.prop(s, "config_path")
        col.operator("cray.load_config", icon="FILE_FOLDER")
        col.prop(s, "selected_surface")

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Density (DayZ-style)")
        col.prop(s, "grid_size")
        col.prop(s, "density_scale")
        col.prop(s, "slope_falloff")
        col.prop(s, "spawn_probability")
        col.prop(s, "max_proxies")
        col.prop(s, "seed")

        layout.separator()
        layout.operator("object.cray_scatter_proxies", icon="PARTICLES")

class CRAY_PT_SnapPointsPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_snap_points"
    bl_label = "Snap Points (Memory LOD)"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return True

    def draw(self, context):
        layout = self.layout
        ss = context.scene.cray_snap_settings
        preview_pattern = _SnapPointNamePattern.from_preview_settings(ss)
        can_create = bool(
            ss.memory_object is not None and
            ss.paired_memory_object is not None and
            ss.memory_object != ss.paired_memory_object
        )

        col = layout.column(align=True)
        col.label(text="Target")
        col.prop(ss, "memory_object", text="")
        col.separator()
        col.prop(ss, "paired_memory_object", text="")
        col.operator("cray.ensure_memory_lod", text="Create/Find Point clouds > Memory", icon="OUTLINER_OB_MESH")

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Name Pattern")
        col.prop(ss, "snap_p3d_name")
        col.prop(ss, "snap_pair_code")
        col.label(text="Snap Axis")
        axis_row = col.row(align=True)
        axis_row.prop(ss, "edge_axis", expand=True)
        col.label(text=f".sp_{preview_pattern.preview_base}_a_0 / .sp_{preview_pattern.preview_base}_v_1", icon="INFO")
        col.prop(ss, "replace_existing")

        layout.separator()
        info = layout.box()
        info.label(text="Select exactly 2 vertices in Edit Mode on any LOD", icon="INFO")
        create_row = info.row()
        create_row.enabled = can_create
        create_row.operator("cray.create_snap_pair_from_model_edge", text="Create Snap Points", icon="MESH_DATA")
        if ss.memory_object is None or ss.paired_memory_object is None:
            info.label(text="Pick or create both Memory LODs first", icon="INFO")
        elif ss.memory_object == ss.paired_memory_object:
            info.label(text="Choose two different Memory LODs", icon="ERROR")

        layout.separator()
        pbox = layout.box()
        pbox.label(text="Plain Axes pivot", icon="EMPTY_AXIS")
        create_label = "Create Plain Axis Pivot  [Ctrl+Shift+P]" if _PLAIN_AXIS_HOTKEY_REGISTERED else "Create Plain Axis Pivot"
        pbox.operator("cray.create_plain_axis_pivot", text=create_label, icon="EMPTY_AXIS")
        pbox.operator("cray.clear_plain_axis_pivots", text="Delete All Plain Axes", icon="TRASH")

class CRAY_PT_ColliderPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_collider"
    bl_label = "Geometry Collider"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return True

    def draw(self, context):
        layout = self.layout
        cs = context.scene.cray_collider_settings
        active_obj = context.view_layer.objects.active
        active_source_name = active_obj.name if active_obj is not None and active_obj.type == "MESH" else "<active mesh>"

        col = layout.column(align=True)
        col.label(text="Target")
        col.label(text=f"Source: {active_source_name}", icon="MESH_DATA")
        col.prop(cs, "target_lod")
        col.prop(cs, "geometry_object")
        col.operator("cray.ensure_collider_lod", icon="OUTLINER_OB_MESH")
        col.prop(cs, "merge_distance")
        col.prop(cs, "recalc_normals")

        layout.separator()

        hotkeys = layout.box()
        row = hotkeys.row(align=True)
        row.label(text="Hotkeys", icon="EVENT_K")
        row.operator("cray.collider_hotkeys_info", text="", icon="INFO")
        row.prop(
            cs,
            "show_hotkey_button_fallbacks",
            text="Buttons",
            emboss=False,
            icon="TRIA_DOWN" if cs.show_hotkey_button_fallbacks else "TRIA_RIGHT",
        )

        if cs.show_hotkey_button_fallbacks:
            box = hotkeys.box()
            box.operator(
                "cray.copy_selected_verts_to_geometry",
                text="Copy Selected Verts To Geometry  [Ctrl+Shift+C]",
                icon="VERTEXSEL",
            )
            box.operator(
                "cray.select_isolated_vertices",
                text="Select Isolated Verts  [Mouse5]",
                icon="VERTEXSEL",
            )
            op = box.operator(
                "cray.build_collider",
                text="Selection -> Hull  [Mouse4]",
                icon="MESH_ICOSPHERE",
            )
            op.build_mode = "SELECTION_HULL"
            box.operator(
                "cray.hull_loose_geometry_verts",
                text="Selected Loose Geometry Verts -> Hull",
                icon="MESH_ICOSPHERE",
            )

        layout.separator()

        adv = layout.box()
        row = adv.row(align=True)
        row.label(text="Extra Build", icon="MOD_REMESH")
        row.prop(
            cs,
            "show_advanced_build_buttons",
            text="Open",
            emboss=False,
            icon="TRIA_DOWN" if cs.show_advanced_build_buttons else "TRIA_RIGHT",
        )

        if cs.show_advanced_build_buttons:
            adv.prop(cs, "box_thickness")
            adv.prop(cs, "bounds_padding")

            row = adv.row(align=True)
            op = row.operator("cray.build_collider", text="Selection -> Box", icon="MESH_CUBE")
            op.build_mode = "SELECTION_BOX"
            op = row.operator("cray.build_collider", text="Object -> Bounds", icon="CUBE")
            op.build_mode = "OBJECT_BOUNDS"

        layout.separator()

        roadway = layout.box()
        roadway.label(text="Misc / Roadway", icon="MESH_PLANE")
        roadway.prop(cs, "roadway_object")
        row = roadway.row(align=True)
        row.operator("cray.ensure_roadway_lod", icon="OUTLINER_OB_MESH")
        row.operator("cray.copy_selected_faces_to_roadway", icon="FACESEL")
        row = roadway.row(align=True)
        row.prop(cs, "roadway_material", text="Material")
        row.operator("cray.open_roadway_material_folder", text="", icon="FILE_FOLDER")
        roadway.prop(cs, "roadway_weld_distance")
        roadway.operator("cray.weld_roadway_vertices", icon="AUTOMERGE_ON")

class CRAY_PT_AssetProxyPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_asset_proxy"
    bl_label = "P3D Asset Library"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        lib = context.scene.cray_asset_library_settings
        st = context.scene.cray_asset_proxy_settings

        box = layout.box()
        box.label(text="Temporary Asset Library", icon="ASSET_MANAGER")
        box.prop(lib, "folder")
        box.prop(lib, "import_first_lod_only")
        box.prop(lib, "clear_previous_temp_library")
        row = box.row(align=True)
        row.operator("cray.asset_library_build_folder", icon="FILE_FOLDER")
        row.operator("cray.asset_library_build_files", icon="FILEBROWSER")
        box.operator("cray.asset_library_clear", icon="TRASH")

        box = layout.box()
        box.label(text="Placed Assets -> A3OB Proxies", icon="CONSTRAINT")
        box.prop(st, "target_object", text="Target House / Main Object")
        box.prop(st, "delete_originals")
        box.operator("cray.convert_selected_to_proxies", icon="CONSTRAINT")


class CRAY_PT_FixesPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_fixes"
    bl_label = "Fixes"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        ts = context.scene.cray_texreplace_settings

        box = layout.box()
        box.label(text="Shading/Geometry fixes", icon="MOD_SMOOTH")
        box.operator("cray.fix_shading_by_pipeline", text="Fix Shading", icon="SHADING_RENDERED")
        box.operator("cray.repair_a3ob_selections", text="Repair Invalid A3OB Selections", icon="GROUP_VERTEX")
        box.separator()
        box.label(text="Hierarchy fix", icon="MOD_REMESH")
        box.prop(ts, "fix_mesh_join_batch")
        box.prop(ts, "fix_mesh_center_to_origin")
        box.operator("cray.fix_mesh_hierarchy", text="Fix Mesh/Hierarchy", icon="MOD_REMESH")
        box.separator()
        box.label(text="Edit Mode planar search", icon="FACESEL")
        edit_col = box.column(align=True)
        edit_col.enabled = (
            context.mode == "EDIT_MESH"
            and context.active_object is not None
            and context.active_object.type == "MESH"
        )
        row = edit_col.row(align=True)
        row.prop(ts, "split_planar_ngon_vertex_count", text="N")
        row.operator("cray.select_split_planar_ngons", text="Find Flat N+", icon="VIEWZOOM")
        tol_row = edit_col.row(align=True)
        tol_row.prop(ts, "split_planar_ngon_angle_tolerance", text="Angle")
        tol_row.prop(ts, "split_planar_ngon_plane_tolerance", text="Plane")

class CRAY_PT_ImportExportPlannerPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_ie_planner"
    bl_label = "Import/Export planner"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        st = context.scene.cray_ie_settings

        ibox = layout.box()
        ibox.label(text="Batch Import (Arma 3 Object Builder)", icon="IMPORT")
        row = ibox.row(align=True)
        row.operator("cray.ie_add_files", icon="ADD")
        row.operator("cray.ie_remove_file", icon="REMOVE")
        row.operator("cray.ie_clear_files", icon="TRASH")
        ibox.template_list("CRAY_UL_ie_files", "", st, "import_files", st, "import_active_index", rows=6)
        ibox.operator("cray.ie_import_batch", icon="FILE_REFRESH")
        ibox.separator()
        ibox.prop(st, "import_show_materials")
        row_preview = ibox.row()
        row_preview.enabled = bool(st.import_show_materials)
        row_preview.prop(st, "import_keep_converted_textures")
        ibox.separator()
        ibox.prop(st, "disable_collections_after_import")
        row2 = ibox.row()
        row2.enabled = bool(st.disable_collections_after_import)
        row2.prop(st, "disable_mode", text="")

        ebox = layout.box()
        ebox.label(text="Batch Export Collections (Arma 3 Object Builder)", icon="EXPORT")
        ebox.prop(st, "export_mode")
        row3 = ebox.row()
        row3.enabled = (st.export_mode == "CUSTOM_DIR")
        row3.prop(st, "export_directory")
        ebox.prop(st, "export_create_bak")
        ebox.prop(st, "export_only_p3d_named")
        ebox.prop(st, "export_only_split_parts")
        ebox.prop(st, "export_force_all_lods")
        ebox.operator("cray.ie_export_collections_batch", icon="FILE_TICK")

class CRAY_PT_ModelSplitPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_model_split"
    bl_label = "Model Split"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        st = context.scene.cray_model_split_settings

        box = layout.box()
        box.label(text="Separate -> Part Model", icon="OUTLINER_COLLECTION")
        box.prop(st, "part_number")
        box.label(text="Select separated objects from one .p3d root", icon="INFO")
        box.label(text="Original collection stays unchanged", icon="INFO")
        box.operator("cray.model_split_duplicate_to_part", icon="DUPLICATE")

class CRAY_PT_TextureReplacePanel(Panel):
    bl_idname = "VIEW3D_PT_cray_texreplace"
    bl_label = "Texture Replace"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        ts = context.scene.cray_texreplace_settings

        box = layout.box()
        box.label(text="Build Database (.paa/.rvmat only)")
        box.prop(ts, "folder")
        box.operator("cray.tex_db_build_folder", icon="FILE_FOLDER")

        layout.separator()

        obox = layout.box()
        obox.label(text="Object Preview (Image Texture materials)")
        row = obox.row(align=True)
        row.prop(ts, "picked_object", text="Select Object")
        row.operator("cray.update_object_preview", text="", icon="FILE_REFRESH")
        row.operator("cray.fix_mesh_hierarchy", text="", icon="MOD_REMESH")
        obox.operator("cray.replace_textures_from_db", icon="FILE_TICK")
        obox.label(text="Preview/Replace auto-detects mesh if picker is empty", icon="INFO")

        obj = ts.picked_object
        if obj is None:
            obox.label(text="No object selected", icon="INFO")
        else:
            obox.label(text=f"Object: {obj.name}", icon="OBJECT_DATA")
            obox.template_list("CRAY_UL_obj_preview", "", ts, "obj_preview_items", ts, "obj_preview_active_index", rows=5)

        layout.separator()
        layout.label(text="DB Preview (problematic duplicates = red)")
        layout.template_list("CRAY_UL_tex_db", "", ts, "db_items", ts, "db_active_index", rows=10)


# ------------------------------------------------------------------------
#  Registration
# ------------------------------------------------------------------------

classes = (
    CRAY_PG_Settings,
    CRAY_PG_SnapSettings,
    CRAY_PG_ColliderSettings,
    CRAY_OT_LoadConfig,
    CRAY_OT_ScatterProxies,
    CRAY_OT_EnsureMemoryLOD,
    CRAY_OT_CreateSnapPairFromModelEdge,
    CRAY_OT_SnapBatchProcess,
    CRAY_OT_CopySelectedVertsToGeometry,
    CRAY_OT_HullLooseGeometryVerts,
    CRAY_OT_ColliderHotkeysInfo,
    CRAY_OT_EnsureRoadwayLOD,
    CRAY_OT_CopySelectedFacesToRoadway,
    CRAY_OT_WeldRoadwayVertices,
    CRAY_OT_OpenRoadwayMaterialFolder,
    CRAY_OT_SelectIsolatedVertices,
    CRAY_OT_SelectSplitPlanarNgons,
    CRAY_OT_EnsureColliderLOD,
    CRAY_OT_BuildCollider,

    CRAY_PG_TexDBItem,
    CRAY_PG_ObjMatImagesItem,
    CRAY_PG_TexReplaceSettings,
    CRAY_UL_TexDB,
    CRAY_UL_ObjPreview,
    CRAY_OT_TexDBBuildFromFolder,
    CRAY_OT_UpdateObjectPreview,
    CRAY_OT_FixMeshHierarchy,
    CRAY_OT_ReplaceTexturesFromDB,

    CRAY_PG_IEFileItem,
    CRAY_PG_IEPlannerSettings,
    CRAY_PG_ModelSplitSettings,
    CRAY_PG_AssetLibrarySettings,
    CRAY_PG_AssetProxySettings,
    CRAY_UL_IEFiles,
    CRAY_OT_AssetLibraryBuildFromFolder,
    CRAY_OT_AssetLibraryBuildFromFiles,
    CRAY_OT_AssetLibraryClear,
    CRAY_OT_IE_AddFiles,
    CRAY_OT_IE_RemoveFile,
    CRAY_OT_IE_ClearFiles,
    CRAY_OT_IE_ImportBatch,
    CRAY_OT_ModelSplitDuplicateToPart,
    CRAY_OT_ConvertSelectedToProxies,
    CRAY_OT_FixShadingByPipeline,
    CRAY_OT_RepairA3OBSelections,
    CRAY_OT_CreatePlainAxisPivot,
    CRAY_OT_ClearPlainAxisPivots,
    CRAY_OT_IE_ExportCollectionsBatch,

    CRAY_PT_ColliderPanel,
    CRAY_PT_ClutterProxiesPanel,
    CRAY_PT_SnapPointsPanel,
    CRAY_PT_AssetProxyPanel,
    CRAY_PT_FixesPanel,
    CRAY_PT_ImportExportPlannerPanel,
    CRAY_PT_ModelSplitPanel,
    CRAY_PT_TextureReplacePanel,
)

def register():
    global _PERSISTED_UI_STATE_CACHE

    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.cray_settings = PointerProperty(type=CRAY_PG_Settings)
    bpy.types.Scene.cray_snap_settings = PointerProperty(type=CRAY_PG_SnapSettings)
    bpy.types.Scene.cray_collider_settings = PointerProperty(type=CRAY_PG_ColliderSettings)
    bpy.types.Scene.cray_texreplace_settings = PointerProperty(type=CRAY_PG_TexReplaceSettings)
    bpy.types.Scene.cray_ie_settings = PointerProperty(type=CRAY_PG_IEPlannerSettings)
    bpy.types.Scene.cray_model_split_settings = PointerProperty(type=CRAY_PG_ModelSplitSettings)
    bpy.types.Scene.cray_asset_library_settings = PointerProperty(type=CRAY_PG_AssetLibrarySettings)
    bpy.types.Scene.cray_asset_proxy_settings = PointerProperty(type=CRAY_PG_AssetProxySettings)
    _PERSISTED_UI_STATE_CACHE = _read_persisted_ui_state()
    _apply_persisted_ui_state_to_all_scenes(only_if_default=True)
    _PERSISTED_UI_STATE_CACHE = _collect_persisted_ui_state(getattr(bpy.context, "scene", None))
    if _restore_persisted_ui_state_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_restore_persisted_ui_state_on_load)
    if not bpy.app.timers.is_registered(_persisted_ui_state_timer):
        bpy.app.timers.register(_persisted_ui_state_timer, first_interval=_PERSISTED_UI_STATE_TIMER_INTERVAL, persistent=True)
    _patch_a3ob_import_read_file()
    if not bpy.app.timers.is_registered(_ensure_a3ob_import_patch_timer):
        bpy.app.timers.register(_ensure_a3ob_import_patch_timer, first_interval=1.0, persistent=True)
    _register_collider_keymaps()

def unregister():
    _unregister_collider_keymaps()
    _save_current_persisted_ui_state(getattr(bpy.context, "scene", None))
    if bpy.app.timers.is_registered(_persisted_ui_state_timer):
        bpy.app.timers.unregister(_persisted_ui_state_timer)
    if _restore_persisted_ui_state_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_restore_persisted_ui_state_on_load)
    if bpy.app.timers.is_registered(_ensure_a3ob_import_patch_timer):
        bpy.app.timers.unregister(_ensure_a3ob_import_patch_timer)
    _unpatch_a3ob_import_read_file()
    del bpy.types.Scene.cray_asset_proxy_settings
    del bpy.types.Scene.cray_asset_library_settings
    del bpy.types.Scene.cray_model_split_settings
    del bpy.types.Scene.cray_ie_settings
    del bpy.types.Scene.cray_texreplace_settings
    del bpy.types.Scene.cray_collider_settings
    del bpy.types.Scene.cray_snap_settings
    del bpy.types.Scene.cray_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
