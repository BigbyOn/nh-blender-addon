bl_info = {
    "name": "NH Plugin for Blender",
    "author": "Daryl and Enisam",
    "version": (0, 1, 0),
    "blender": (4, 4, 0),
    "location": "3D Viewport > N-panel > NH Plugin",
    "description": "Scatter Arma3 Proxy objects using DayZ clutter config + Texture Replace (.paa/.rvmat) + Replace from DB via A3OB",
    "doc_url": "https://github.com/Enisam/NH_Blender",
    "tracker_url": "https://github.com/Enisam/NH_Blender/issues",
    "mclink": "https://github.com/Enisam/NH_Blender",
    "category": "Object",
}

import bpy
import bmesh
from bpy.types import Operator, Panel, PropertyGroup, UIList
from bpy.props import PointerProperty, StringProperty, FloatProperty, IntProperty, BoolProperty, EnumProperty
from mathutils import Vector, Matrix
import math
import random
import os
import re

# ------------------------------------------------------------------------
#  Global config storage
# ------------------------------------------------------------------------

CONFIG_PATH = ""
CONFIG_SURFACES = {}
CONFIG_CLUTTER = {}
_PROXY_MESH_NAME = "DayZ_ClutterProxyMesh"


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

def _is_helper_object_name(name: str) -> bool:
    n = (name or "").strip().lower()
    return n.startswith(_HELPER_OBJ_PREFIXES)

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

def _ensure_target_collection(context, mesh_obj):
    scene_root = context.scene.collection

    target = scene_root.children.get(_ROOT_COLLECTION_NAME)
    if target is not None:
        return target

    target = bpy.data.collections.get(_ROOT_COLLECTION_NAME)
    if target is None:
        target = bpy.data.collections.new(_ROOT_COLLECTION_NAME)

    if scene_root.children.get(target.name) is None:
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

def _ensure_visual_hierarchy(context, mesh_obj):
    mesh_name = "Resolution 0"
    target_collection = _ensure_target_collection(context, mesh_obj)

    _move_object_to_collection(mesh_obj, target_collection)
    mesh_world = mesh_obj.matrix_world.copy()
    mesh_obj.parent = None
    mesh_obj.matrix_parent_inverse.identity()
    mesh_obj.matrix_world = mesh_world
    mesh_obj.name = mesh_name
    return target_collection, mesh_obj

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
            self.report({"ERROR"}, "No mesh object found in picked/active/selected scope")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass

        scope_objs, scope_src = _collect_fix_scope(context, target_obj)
        obj, target_src = _pick_primary_mesh(scope_objs, target_obj)
        if obj is None:
            self.report({"ERROR"}, "No mesh object in selected/root scope")
            return {"CANCELLED"}
        ts.picked_object = obj
        old_parent_names = []
        p = obj.parent
        while p is not None:
            old_parent_names.append(p.name)
            p = p.parent

        descendant_names = {o.name for o in _iter_descendants(obj)}
        delete_objs = []
        join_objs = []
        for ch in scope_objs:
            if ch == obj:
                continue
            if bpy.data.objects.get(ch.name) is None:
                continue

            if _is_helper_object_name(ch.name):
                delete_objs.append(ch)
                continue

            if ch.type == "MESH":
                poly_count = len(ch.data.polygons) if ch.data else 0
                if poly_count > 0:
                    join_objs.append(ch)
                else:
                    delete_objs.append(ch)
            else:
                if ch.name in descendant_names:
                    delete_objs.append(ch)

        deleted_count = 0
        for ch in sorted(delete_objs, key=_obj_depth, reverse=True):
            if bpy.data.objects.get(ch.name) is not None:
                bpy.data.objects.remove(ch, do_unlink=True)
                deleted_count += 1

        joined_count = 0
        if join_objs:
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            for ch in join_objs:
                if bpy.data.objects.get(ch.name) is not None:
                    try:
                        ch.hide_set(False)
                    except Exception:
                        pass
                    try:
                        ch.hide_viewport = False
                    except Exception:
                        pass
                    ch.select_set(True)
            context.view_layer.objects.active = obj
            selected_meshes = [o for o in context.selected_objects if o.type == "MESH"]
            if len(selected_meshes) > 1:
                bpy.ops.object.join()
                joined_count = len(selected_meshes) - 1

        mesh = obj.data
        before_v = len(mesh.vertices)
        before_e = len(mesh.edges)
        before_f = len(mesh.polygons)

        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=0.000001)
        loose_edges = [e for e in bm.edges if not e.link_faces]
        if loose_edges:
            bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")
        loose_verts = [v for v in bm.verts if not v.link_edges]
        if loose_verts:
            bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")
        bm.normal_update()
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()

        after_v = len(mesh.vertices)
        after_e = len(mesh.edges)
        after_f = len(mesh.polygons)
        target_collection, mesh_obj = _ensure_visual_hierarchy(context, obj)
        deleted_parents = 0
        for anc_name in old_parent_names:
            anc_live = bpy.data.objects.get(anc_name)
            if anc_live is None:
                continue
            if anc_live == mesh_obj:
                continue
            if anc_live.type != "EMPTY":
                continue
            if len(anc_live.children) != 0:
                continue
            bpy.data.objects.remove(anc_live, do_unlink=True)
            deleted_parents += 1

        deleted_helpers = 0
        for helper in list(bpy.data.objects):
            if helper.type != "EMPTY":
                continue
            if not _is_export_helper_empty_name(helper.name):
                continue
            if len(helper.children) != 0:
                continue
            bpy.data.objects.remove(helper, do_unlink=True)
            deleted_helpers += 1

        deleted_total = deleted_count + deleted_parents + deleted_helpers
        extras = [f"src: {src}", f"scope_objs: {len(scope_objs)}"]
        if scope_src != "target-descendants":
            extras.append(f"scope: {scope_src}")
        if target_src != "preferred":
            extras.append(f"target: {target_src}")
        suffix = "" if not extras else f", {', '.join(extras)}"
        self.report(
            {"INFO"},
            (
                f"Fixed '{obj.name}': deleted children {deleted_total}, joined {joined_count}, "
                f"verts {before_v}->{after_v}, edges {before_e}->{after_e}, faces {before_f}->{after_f}, "
                f"hierarchy: {target_collection.name}/{mesh_obj.name}{suffix}"
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
    CRAY_OT_LoadConfig,
    CRAY_OT_ScatterProxies,

    CRAY_PG_TexDBItem,
    CRAY_PG_ObjMatImagesItem,
    CRAY_PG_TexReplaceSettings,
    CRAY_UL_TexDB,
    CRAY_UL_ObjPreview,
    CRAY_OT_TexDBBuildFromFolder,
    CRAY_OT_UpdateObjectPreview,
    CRAY_OT_FixMeshHierarchy,
    CRAY_OT_ReplaceTexturesFromDB,

    CRAY_PT_ClutterProxiesPanel,
    CRAY_PT_TextureReplacePanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.cray_settings = PointerProperty(type=CRAY_PG_Settings)
    bpy.types.Scene.cray_texreplace_settings = PointerProperty(type=CRAY_PG_TexReplaceSettings)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.cray_settings
    del bpy.types.Scene.cray_texreplace_settings

if __name__ == "__main__":
    register()

