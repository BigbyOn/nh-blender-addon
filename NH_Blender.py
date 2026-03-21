bl_info = {
    "name": "NH Plugin for Blender",
    "author": "Daryl and Enisam",
    "version": (0, 1, 7),
    "blender": (4, 4, 3),
    "location": "3D Viewport > N-panel > NH Plugin",
    "description": "Scatter Arma3 Proxy objects using DayZ clutter config + Texture Replace (.paa/.rvmat) + Replace from DB via A3OB",
    "doc_url": "https://github.com/BigbyOn/nh-blender-addon",
    "tracker_url": "https://github.com/BigbyOn/nh-blender-addon/issues",
    "mclink": "https://github.com/BigbyOn/nh-blender-addon",
    "category": "Object",
}

import bpy
import bmesh
from bpy.types import Operator, Panel, PropertyGroup, UIList, OperatorFileListElement
from bpy.props import PointerProperty, StringProperty, FloatProperty, IntProperty, BoolProperty, EnumProperty, CollectionProperty
from mathutils import Vector, Matrix
import math
import random
import os
import re
import shutil
import importlib
from contextlib import contextmanager
import uuid

# ------------------------------------------------------------------------
#  Global config storage
# ------------------------------------------------------------------------

CONFIG_PATH = ""
CONFIG_SURFACES = {}
CONFIG_CLUTTER = {}
_PROXY_MESH_NAME = "DayZ_ClutterProxyMesh"
_ASSET_CATALOG_NAME = "Asset"
_ASSET_CATALOG_FALLBACK_ID = "7d6f3b1d-4d5f-4b1e-9f77-5d1e8dd5c001"


def _fmt_exc(e: Exception) -> str:
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__

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
    set_a3ob_proxy_properties(proxy_obj, model_path, proxy_index)
    return proxy_obj


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
    max_height_offset: FloatProperty(name="Height Offset", default=2.0, min=0.0)
    max_distance: FloatProperty(name="Max Distance", default=100.0, min=0.1)
    random_jitter: FloatProperty(name="Random Jitter", default=0.5, min=0.0, max=1.0)
    spawn_probability: FloatProperty(name="Spawn Probability", default=1.0, min=0.0, max=1.0)
    max_proxies: IntProperty(name="Max Proxies (0=unlimited)", default=0, min=0)
    seed: IntProperty(name="Random Seed", default=0)
    only_hit_source: BoolProperty(name="Only Hit Source", default=True)

class CRAY_PG_SnapSettings(PropertyGroup):
    source_object: PointerProperty(name="Model Object", type=bpy.types.Object)
    memory_object: PointerProperty(name="Memory LOD Object", type=bpy.types.Object)
    snap_group: StringProperty(name="Snap Group", default="StenaKamennaya")
    snap_side: EnumProperty(
        name="Side",
        items=(
            ("a", "A", "Create A-side snap points"),
            ("v", "V", "Create V-side snap points"),
        ),
        default="a",
    )
    edge_axis: EnumProperty(
        name="Edge Axis",
        items=(
            ("X", "X", "Use X min/max edge"),
            ("Y", "Y", "Use Y min/max edge"),
            ("Z", "Z", "Use Z min/max edge"),
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
    replace_existing: BoolProperty(name="Replace Existing Named Groups", default=True)
    batch_cleanup_imported: BoolProperty(name="Cleanup Imported Objects", default=True)
    batch_overwrite_bak: BoolProperty(name="Overwrite .bak", default=True)


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
        obj = s.source_object

        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Source object must be a mesh")
            return {"CANCELLED"}
        if not s.vertex_group:
            self.report({"ERROR"}, "Vertex group is not set")
            return {"CANCELLED"}
        vg = obj.vertex_groups.get(s.vertex_group)
        if vg is None:
            self.report({"ERROR"}, f"Vertex group '{s.vertex_group}' not found on object")
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

        mesh = obj.data
        vg_index = vg.index
        mw = obj.matrix_world

        group_verts_world = []
        for v in mesh.vertices:
            for g in v.groups:
                if g.group == vg_index and g.weight > 0.0:
                    group_verts_world.append(mw @ v.co)
                    break

        if not group_verts_world:
            self.report({"ERROR"}, "Vertex group is empty or has no weighted vertices")
            return {"CANCELLED"}

        xs = [v.x for v in group_verts_world]
        ys = [v.y for v in group_verts_world]
        zs = [v.z for v in group_verts_world]

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        start_z = max(zs) + s.max_height_offset
        if s.density_scale <= 0.0:
            self.report({"ERROR"}, "Density scale must be > 0")
            return {"CANCELLED"}
        grid = s.grid_size / math.sqrt(s.density_scale)
        if grid <= 0.0:
            self.report({"ERROR"}, "Grid size must be > 0")
            return {"CANCELLED"}

        if s.target_collection is not None:
            target_coll = s.target_collection
        elif obj.users_collection:
            target_coll = obj.users_collection[0]
        else:
            target_coll = context.scene.collection

        depsgraph = context.evaluated_depsgraph_get()
        scene = context.scene
        direction = Vector((0.0, 0.0, -1.0))
        jitter_radius = 0.5 * grid * s.random_jitter

        created_count = 0
        proxy_index = 0
        cells_total = 0
        skipped_by_probability = 0
        ray_miss_count = 0
        rejected_non_source = 0
        hit_count = 0
        limit_reached = False

        steps_x = max(1, int(math.ceil((max_x - min_x) / grid)) + 1)
        steps_y = max(1, int(math.ceil((max_y - min_y) / grid)) + 1)

        for ix in range(steps_x):
            x = min_x + ix * grid
            for iy in range(steps_y):
                if s.max_proxies > 0 and created_count >= s.max_proxies:
                    limit_reached = True
                    break

                y = min_y + iy * grid
                cells_total += 1

                cell_seed = (
                    (s.seed & 0xFFFFFFFF)
                    ^ ((ix + 1) * 73856093)
                    ^ ((iy + 1) * 19349663)
                ) & 0xFFFFFFFFFFFFFFFF
                cell_rng = random.Random(cell_seed)

                if s.spawn_probability < 1.0 and cell_rng.random() > s.spawn_probability:
                    skipped_by_probability += 1
                    continue

                jx = (cell_rng.random() * 2.0 - 1.0) * jitter_radius
                jy = (cell_rng.random() * 2.0 - 1.0) * jitter_radius
                origin = Vector((x + jx, y + jy, start_z))

                hit, hit_loc, hit_normal, _, hit_obj, _ = scene.ray_cast(
                    depsgraph, origin, direction, distance=s.max_distance
                )

                if not hit:
                    ray_miss_count += 1
                    continue

                if s.only_hit_source and getattr(hit_obj, "original", hit_obj) != obj:
                    rejected_non_source += 1
                    continue

                hit_count += 1
                clutter_class = pick_weighted_random(clutter_names, clutter_probs, rng=cell_rng)
                c_def = clutter_defs[clutter_class]
                proxy_index += 1

                create_proxy_object(
                    context=context,
                    collection=target_coll,
                    parent_obj=obj,
                    location=hit_loc,
                    normal=hit_normal,
                    model_path=c_def["model"],
                    proxy_index=proxy_index,
                    scale_min=c_def.get("scaleMin", 1.0),
                    scale_max=c_def.get("scaleMax", 1.0),
                    rng=cell_rng,
                )
                created_count += 1
            if limit_reached:
                break

        limit_suffix = " (max limit reached)" if limit_reached else ""
        self.report(
            {"INFO"},
            (
                f"Created {created_count} proxies from {cells_total} cells"
                f" | hits: {hit_count}, miss: {ray_miss_count},"
                f" prob-skip: {skipped_by_probability}, reject: {rejected_non_source}"
                f"{limit_suffix}"
            ),
        )
        return {"FINISHED"}


# ------------------------------------------------------------------------
#  Snap points (.sp_*) for Memory LOD
# ------------------------------------------------------------------------

_SP_GROUP_RE = re.compile(r"^[A-Za-z0-9]+$")

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
            result = fn(**payload)
            if isinstance(result, set) and "CANCELLED" in result:
                last_err = RuntimeError(f"{op_idname} returned CANCELLED")
                continue
            return result, op_idname, None
        except Exception as e:
            last_err = e
            continue
    return None, None, last_err


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
    if obj is None or obj.type != "MESH":
        return False
    if obj.name == "Memory":
        return True
    if not hasattr(obj, "a3ob_properties_object"):
        return False
    try:
        props = obj.a3ob_properties_object
        return str(getattr(props, "lod", "")) == "9"
    except Exception:
        return False

def _pick_memory_lod_object(context, source_obj):
    if source_obj is not None:
        for col in source_obj.users_collection:
            obj = col.objects.get("Memory")
            if obj is not None and obj.type == "MESH":
                return obj
    obj = bpy.data.objects.get("Memory")
    if obj is not None and obj.type == "MESH":
        return obj
    for obj in context.scene.objects:
        if _is_memory_lod_mesh_object(obj):
            return obj
    return None

def _set_memory_lod_a3ob_props(memory_obj):
    if not hasattr(memory_obj, "a3ob_properties_object"):
        return
    try:
        props = memory_obj.a3ob_properties_object
        props.lod = "9"
        props.is_a3_lod = True
        autocenter = None
        for p in props.properties:
            if p.name.lower() == "autocenter":
                autocenter = p
                break
        if autocenter is None:
            autocenter = props.properties.add()
            autocenter.name = "autocenter"
        autocenter.value = "0"
    except Exception:
        pass

def _ensure_memory_lod_object(context, source_obj, preferred_obj=None):
    if preferred_obj is not None and preferred_obj.type == "MESH":
        memory_obj = preferred_obj
    else:
        memory_obj = _pick_memory_lod_object(context, source_obj)

    if memory_obj is None:
        memory_mesh = bpy.data.meshes.new("Memory")
        memory_obj = bpy.data.objects.new("Memory", memory_mesh)
        if source_obj is not None and source_obj.users_collection:
            source_obj.users_collection[0].objects.link(memory_obj)
        else:
            context.scene.collection.objects.link(memory_obj)
        if source_obj is not None:
            memory_obj.matrix_world = source_obj.matrix_world.copy()

    _set_memory_lod_a3ob_props(memory_obj)
    return memory_obj

def _get_two_selected_vertex_world_positions(context):
    active = context.view_layer.objects.active
    if active is None or active.type != "MESH" or active.mode != "EDIT":
        raise RuntimeError("Active object must be a mesh in Edit Mode")

    bm = bmesh.from_edit_mesh(active.data)
    selected = [active.matrix_world @ v.co for v in bm.verts if v.select]
    if len(selected) != 2:
        raise RuntimeError(f"Select exactly 2 vertices in Edit Mode (selected: {len(selected)})")
    return active, selected

def _create_snap_pair_in_memory(memory_obj, world_points, snap_group: str, snap_side: str, replace_existing: bool):
    mesh = memory_obj.data
    to_local = memory_obj.matrix_world.inverted()
    local_points = [to_local @ p for p in world_points]

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

class CRAY_OT_EnsureMemoryLOD(Operator):
    bl_idname = "cray.ensure_memory_lod"
    bl_label = "Create/Find Memory LOD"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ss = context.scene.cray_snap_settings
        source_obj = ss.source_object
        if source_obj is None:
            active = context.view_layer.objects.active
            if active is not None and active.type == "MESH":
                source_obj = active

        if ss.memory_object is not None and ss.memory_object.type != "MESH":
            self.report({"ERROR"}, "Memory LOD Object must be a mesh")
            return {"CANCELLED"}

        memory_obj = _ensure_memory_lod_object(context, source_obj, preferred_obj=ss.memory_object)
        ss.memory_object = memory_obj
        self.report({"INFO"}, f"Memory LOD ready: {memory_obj.name}")
        return {"FINISHED"}

class CRAY_OT_CreateSnapPair(Operator):
    bl_idname = "cray.create_snap_pair"
    bl_label = "Create .sp Pair From Selected Vertices"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ss = context.scene.cray_snap_settings

        snap_group = (ss.snap_group or "").strip()
        if not snap_group:
            self.report({"ERROR"}, "Snap Group is empty")
            return {"CANCELLED"}
        if not _SP_GROUP_RE.fullmatch(snap_group):
            self.report({"ERROR"}, "Snap Group must contain only letters and digits")
            return {"CANCELLED"}

        try:
            active_obj, world_points = _get_two_selected_vertex_world_positions(context)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as e:
                self.report({"ERROR"}, f"Failed to switch to Object Mode: {_fmt_exc(e)}")
                return {"CANCELLED"}

        source_obj = ss.source_object if ss.source_object is not None else active_obj
        if ss.memory_object is not None and ss.memory_object.type != "MESH":
            self.report({"ERROR"}, "Memory LOD Object must be a mesh")
            return {"CANCELLED"}

        memory_obj = _ensure_memory_lod_object(context, source_obj, preferred_obj=ss.memory_object)
        ss.memory_object = memory_obj

        created_names = _create_snap_pair_in_memory(
            memory_obj=memory_obj,
            world_points=world_points,
            snap_group=snap_group,
            snap_side=ss.snap_side,
            replace_existing=ss.replace_existing,
        )

        self.report({"INFO"}, f"Created {len(created_names)} points in {memory_obj.name}: {', '.join(created_names)}")
        return {"FINISHED"}

class CRAY_OT_CreateSnapPairFromModelEdge(Operator):
    bl_idname = "cray.create_snap_pair_from_model_edge"
    bl_label = "Create .sp Pair From Model Edge"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ss = context.scene.cray_snap_settings

        snap_group = (ss.snap_group or "").strip()
        if not snap_group:
            self.report({"ERROR"}, "Snap Group is empty")
            return {"CANCELLED"}
        if not _SP_GROUP_RE.fullmatch(snap_group):
            self.report({"ERROR"}, "Snap Group must contain only letters and digits")
            return {"CANCELLED"}

        model_obj = ss.source_object
        if model_obj is None:
            active = context.view_layer.objects.active
            if active is not None and active.type == "MESH":
                model_obj = active
        if model_obj is None or model_obj.type != "MESH":
            self.report({"ERROR"}, "Model Object must be a mesh")
            return {"CANCELLED"}

        try:
            world_points = _auto_snap_points_from_model_edge(
                model_obj=model_obj,
                edge_axis_token=ss.edge_axis,
                edge_side_token=ss.edge_side,
                span_axis_token=ss.edge_span_axis,
                edge_tolerance=ss.edge_tolerance,
            )
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as e:
                self.report({"ERROR"}, f"Failed to switch to Object Mode: {_fmt_exc(e)}")
                return {"CANCELLED"}

        if ss.memory_object is not None and ss.memory_object.type != "MESH":
            self.report({"ERROR"}, "Memory LOD Object must be a mesh")
            return {"CANCELLED"}

        memory_obj = _ensure_memory_lod_object(context, model_obj, preferred_obj=ss.memory_object)
        ss.memory_object = memory_obj

        created_names = _create_snap_pair_in_memory(
            memory_obj=memory_obj,
            world_points=world_points,
            snap_group=snap_group,
            snap_side=ss.snap_side,
            replace_existing=ss.replace_existing,
        )
        self.report(
            {"INFO"},
            (
                f"Created {len(created_names)} edge points in {memory_obj.name}: "
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
            self.report({"ERROR"}, "Snap Group must contain only letters and digits")
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
    for scene in list(bpy.data.scenes):
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

class CRAY_PG_IEFileItem(PropertyGroup):
    path: StringProperty(name="File", default="", subtype="FILE_PATH")

class CRAY_PG_IEPlannerSettings(PropertyGroup):
    import_files: CollectionProperty(type=CRAY_PG_IEFileItem)
    import_active_index: IntProperty(default=0)
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
        dir_abs = bpy.path.abspath(self.directory) if self.directory else ""

        for f in self.files:
            fp = os.path.join(dir_abs, f.name) if dir_abs else f.name
            fp = bpy.path.abspath(fp)
            if not fp:
                continue
            it = st.import_files.add()
            it.path = _norm_path(fp)
            added += 1

        if added == 0:
            self.report({"WARNING"}, "No files added")
        else:
            self.report({"INFO"}, f"Added {added} file(s)")
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
            res, op_id, err = _call_first_available(_A3OB_IMPORT_CANDIDATES, filepath=fp)
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
    scene = bpy.data.scenes.get(_NH_TEMP_ASSET_SCENE_NAME)
    if scene is None:
        scene = bpy.data.scenes.new(_NH_TEMP_ASSET_SCENE_NAME)
    return scene


def _ensure_temp_asset_library_root(context):
    col = bpy.data.collections.get(_NH_TEMP_ASSET_LIBRARY_NAME)
    if col is None:
        col = bpy.data.collections.new(_NH_TEMP_ASSET_LIBRARY_NAME)

    asset_scene = _ensure_temp_asset_scene()

    for scene in bpy.data.scenes:
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
    asset_scene = bpy.data.scenes.get(_NH_TEMP_ASSET_SCENE_NAME)
    if asset_scene is not None:
        try:
            if len(asset_scene.collection.children) == 0:
                bpy.data.scenes.remove(asset_scene)
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

        col = layout.column(align=True)
        col.label(text="Source")
        col.prop(s, "source_object")
        if s.source_object and s.source_object.type == "MESH":
            col.prop_search(s, "vertex_group", s.source_object, "vertex_groups")
        col.prop(s, "target_collection")

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
        col.prop(s, "random_jitter")
        col.prop(s, "spawn_probability")
        col.prop(s, "max_proxies")
        col.prop(s, "seed")

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Raycast")
        col.prop(s, "max_height_offset")
        col.prop(s, "max_distance")
        col.prop(s, "only_hit_source")

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

        col = layout.column(align=True)
        col.label(text="Target")
        col.prop(ss, "source_object")
        col.prop(ss, "memory_object")
        col.operator("cray.ensure_memory_lod", icon="OUTLINER_OB_MESH")

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Name Pattern")
        col.prop(ss, "snap_group")
        col.prop(ss, "snap_side", expand=True)
        col.prop(ss, "replace_existing")

        layout.separator()
        box = layout.box()
        box.label(text="Manual: 2 selected vertices", icon="EDITMODE_HLT")
        box.label(text="Select exactly 2 vertices in Edit Mode", icon="INFO")
        box.operator("cray.create_snap_pair", icon="MESH_DATA")

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
        box.label(text="Select mesh object(s)", icon="INFO")
        box.label(text="If >1 selected: auto-join")
        box.operator("cray.fix_shading_by_pipeline", text="Fix Shading", icon="SHADING_RENDERED")
        box.separator()
        box.label(text="Hierarchy fix", icon="MOD_REMESH")
        box.prop(ts, "fix_mesh_join_batch")
        box.prop(ts, "fix_mesh_center_to_origin")
        box.label(text="Fix Mesh uses selected/active object first", icon="INFO")
        box.operator("cray.fix_mesh_hierarchy", text="Fix Mesh/Hierarchy", icon="MOD_REMESH")

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
        ibox.prop(st, "disable_collections_after_import")
        row2 = ibox.row()
        row2.enabled = bool(st.disable_collections_after_import)
        row2.prop(st, "disable_mode", text="")
        ibox.label(text="Import uses A3OB operators (fallback search).", icon="INFO")

        ebox = layout.box()
        ebox.label(text="Batch Export Collections (Arma 3 Object Builder)", icon="EXPORT")
        ebox.prop(st, "export_mode")
        row3 = ebox.row()
        row3.enabled = (st.export_mode == "CUSTOM_DIR")
        row3.prop(st, "export_directory")
        ebox.prop(st, "export_create_bak")
        ebox.prop(st, "export_only_p3d_named")
        ebox.prop(st, "export_force_all_lods")
        ebox.operator("cray.ie_export_collections_batch", icon="FILE_TICK")
        ebox.label(text="Each root collection exports separately.", icon="INFO")

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
    CRAY_OT_LoadConfig,
    CRAY_OT_ScatterProxies,
    CRAY_OT_EnsureMemoryLOD,
    CRAY_OT_CreateSnapPair,
    CRAY_OT_CreateSnapPairFromModelEdge,
    CRAY_OT_SnapBatchProcess,

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
    CRAY_OT_ConvertSelectedToProxies,
    CRAY_OT_FixShadingByPipeline,
    CRAY_OT_IE_ExportCollectionsBatch,

    CRAY_PT_ClutterProxiesPanel,
    CRAY_PT_SnapPointsPanel,
    CRAY_PT_AssetProxyPanel,
    CRAY_PT_FixesPanel,
    CRAY_PT_ImportExportPlannerPanel,
    CRAY_PT_TextureReplacePanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.cray_settings = PointerProperty(type=CRAY_PG_Settings)
    bpy.types.Scene.cray_snap_settings = PointerProperty(type=CRAY_PG_SnapSettings)
    bpy.types.Scene.cray_texreplace_settings = PointerProperty(type=CRAY_PG_TexReplaceSettings)
    bpy.types.Scene.cray_ie_settings = PointerProperty(type=CRAY_PG_IEPlannerSettings)
    bpy.types.Scene.cray_asset_library_settings = PointerProperty(type=CRAY_PG_AssetLibrarySettings)
    bpy.types.Scene.cray_asset_proxy_settings = PointerProperty(type=CRAY_PG_AssetProxySettings)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.cray_settings
    del bpy.types.Scene.cray_snap_settings
    del bpy.types.Scene.cray_texreplace_settings
    del bpy.types.Scene.cray_ie_settings
    del bpy.types.Scene.cray_asset_library_settings
    del bpy.types.Scene.cray_asset_proxy_settings

if __name__ == "__main__":
    register()
