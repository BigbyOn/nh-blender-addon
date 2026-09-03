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

# nh_scatter.py
# auto-split slice; cross-module refs resolved with in-function imports

def get_proxy_mesh():
    from .nh_base import (_PROXY_MESH_NAME)
    mesh = bpy.data.meshes.get(_PROXY_MESH_NAME)
    if mesh is None or not _is_proxy_triangle_mesh(mesh):
        if mesh is not None and getattr(mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(mesh)
            except Exception:
                pass
        mesh = _new_proxy_triangle_mesh(_PROXY_MESH_NAME)
    return mesh


def _is_proxy_triangle_mesh(mesh) -> bool:
    if mesh is None:
        return False
    try:
        return (
            len(mesh.vertices) == 3
            and len(mesh.polygons) == 1
            and len(mesh.polygons[0].vertices) == 3
        )
    except Exception:
        return False


def _new_proxy_triangle_mesh(name: str):
    from .nh_base import (_PROXY_MESH_NAME)
    mesh = bpy.data.meshes.new(name or _PROXY_MESH_NAME)
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (0.0, 0.0, 2.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
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


def _set_p3d_pg_property(pg, identifiers, value, *, names=(), contains=(), prop_types=()):
    for identifier in identifiers:
        if hasattr(pg, identifier):
            setattr(pg, identifier, value)
            return identifier

    norm_names = {str(item or "").strip().lower() for item in names}
    contains = tuple(str(item or "").strip().lower() for item in contains if str(item or "").strip())
    prop_types = {str(item or "").strip().upper() for item in prop_types if str(item or "").strip()}
    try:
        props = list(pg.bl_rna.properties)
    except Exception:
        props = []

    for prop in props:
        identifier = getattr(prop, "identifier", "")
        if identifier == "rna_type" or not hasattr(pg, identifier):
            continue
        if prop_types and str(getattr(prop, "type", "") or "").strip().upper() not in prop_types:
            continue
        prop_name = str(getattr(prop, "name", "") or "").strip().lower()
        prop_id = str(identifier or "").strip().lower()
        if prop_name in norm_names or prop_id in norm_names:
            setattr(pg, identifier, value)
            return identifier
        if contains and all(token in prop_name or token in prop_id for token in contains):
            setattr(pg, identifier, value)
            return identifier
    return ""


def _proxy_name_from_p3d_props(pg, model_path: str, proxy_index: int) -> str:
    try:
        name = str(pg.get_name() or "").strip()
    except Exception:
        name = ""
    if not name:
        base = os.path.splitext(os.path.basename(model_path or ""))[0].strip() or "unknown"
        name = f"{base} {int(proxy_index or 0)}"
    return f"proxy: {name}"


def set_p3d_proxy_properties(proxy_obj, model_path: str, proxy_index: int):
    from .nh_base import (_PROXY_MESH_NAME)
    if not hasattr(proxy_obj, "a3ob_properties_object_proxy"):
        raise RuntimeError(
            "Object has no 'a3ob_properties_object_proxy'. "
            "Ensure addon 'Arma 3 Object Builder' is installed and enabled."
        )

    pg = proxy_obj.a3ob_properties_object_proxy
    arma_path = make_pdrive_path(model_path)

    path_id = _set_p3d_pg_property(
        pg,
        ("proxy_path", "path", "filepath", "file_path"),
        arma_path,
        names=("Path", "Proxy Path"),
        contains=("path",),
        prop_types=("STRING",),
    )
    if not path_id:
        raise RuntimeError("P3D proxy path property not found")

    index_id = _set_p3d_pg_property(
        pg,
        ("proxy_index", "index"),
        int(proxy_index),
        names=("Index", "Proxy Index"),
        contains=("index",),
        prop_types=("INT",),
    )
    if not index_id:
        raise RuntimeError("P3D proxy index property not found")

    is_id = _set_p3d_pg_property(
        pg,
        ("is_a3_proxy", "is_proxy", "proxy"),
        True,
        names=("Is P3D Proxy", "Arma 3 Model Proxy"),
        contains=("proxy",),
        prop_types=("BOOLEAN",),
    )
    if not is_id:
        raise RuntimeError("P3D proxy enable property not found")

    proxy_name = _proxy_name_from_p3d_props(pg, arma_path, proxy_index)
    proxy_obj.name = proxy_name
    if getattr(proxy_obj, "data", None) is not None:
        if str(getattr(proxy_obj.data, "name", "") or "") != _PROXY_MESH_NAME:
            proxy_obj.data.name = proxy_name
    try:
        proxy_obj.display_type = "WIRE"
        proxy_obj.show_name = True
    except Exception:
        pass
    try:
        proxy_obj.a3ob_properties_object.is_a3_lod = False
    except Exception:
        pass


def create_proxy_object(context, collection, parent_obj, location: Vector, normal: Vector,
                        model_path: str, proxy_index: int, scale_min: float = 1.0, scale_max: float = 1.0,
                        rng=None):
    from .nh_base import (_SCATTER_PROXY_TAG_PROP)
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
        proxy_obj.matrix_parent_inverse = parent_obj.matrix_world.inverted_safe()
    except Exception:
        try:
            proxy_obj.matrix_parent_inverse = Matrix.Identity(4)
        except Exception:
            pass
    try:
        proxy_obj[_SCATTER_PROXY_TAG_PROP] = True
    except Exception:
        pass
    set_p3d_proxy_properties(proxy_obj, model_path, proxy_index)
    return proxy_obj

def _is_generated_scatter_proxy(obj, parent_obj=None) -> bool:
    from .nh_base import (_SCATTER_PROXY_TAG_PROP)
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
    from .nh_textures import (_obj_depth)
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
    from .nh_base import (CONFIG_SURFACES)
    items = [("NONE", "<no surface>", "Surface is not selected", 0)]
    if not CONFIG_SURFACES:
        return items
    for index, name in enumerate(sorted(CONFIG_SURFACES.keys()), start=1):
        items.append((name, name, "Surface from CfgSurfaceCharacters", index))
    return items


def _repair_mojibake_text(text):
    original = str(text or "")
    if not original:
        return original

    def _score(value):
        marker_count = sum(value.count(marker) for marker in ("Р ", "РЎ", "Гђ", "Г‘", "пїЅ"))
        cyrillic_count = sum(1 for ch in value if "\u0400" <= ch <= "\u04ff")
        return marker_count * 4 - cyrillic_count

    best = original
    best_score = _score(original)
    for encoding in ("cp1251", "latin1"):
        try:
            candidate = original.encode(encoding).decode("utf-8")
        except Exception:
            continue
        candidate_score = _score(candidate)
        if candidate and candidate_score < best_score:
            best = candidate
            best_score = candidate_score
    return best


# ------------------------------------------------------------------------
#  Settings
# ------------------------------------------------------------------------

class CRAY_PG_Settings(PropertyGroup):
    source_object: PointerProperty(name="Source Object", type=bpy.types.Object)
    vertex_group: StringProperty(name="Vertex Group", default="")
    target_collection: PointerProperty(name="Target Collection", type=bpy.types.Collection)
    config_path: StringProperty(name="Config .cpp", default="", subtype="FILE_PATH")
    selected_surface: EnumProperty(name="Surface", items=get_surface_enum_items, default=0)
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

from .nh_base import (_on_snap_p3d_name_changed)

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
            ("X", "X", "Use X in the snap point name pattern and as the preferred 0/1 sort axis"),
            ("Y", "Y", "Use Y in the snap point name pattern and as the preferred 0/1 sort axis"),
            ("Z", "Z", "Use Z in the snap point name pattern and as the preferred 0/1 sort axis"),
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

_MODEL_SPLIT_TARGET_CATEGORY_ITEMS = (
    ("RESOLUTION", "Resolution", "P3D Resolution LOD stored in Visuals"),
    ("GEOMETRIES", "Geometries", "P3D Geometry LOD stored in Geometries"),
    ("POINT_CLOUDS", "Point clouds", "P3D Memory LOD stored in Point clouds"),
    ("ROADWAY", "Roadway", "P3D Roadway LOD stored in Misc"),
)

_MODEL_SPLIT_MERGE_COLLECTION_ENUM_CACHE = []


def _model_split_natural_name_key(name: str):
    text = (name or "").strip().lower()
    return [
        (1, int(part)) if part.isdigit() else (0, part)
        for part in re.split(r"(\d+)", text)
    ]


def _model_split_enum_collection_is_p3d_root(collection) -> bool:
    name = (getattr(collection, "name", "") or "").strip().lower()
    name = re.sub(r"\.\d{3}$", "", name)
    return name.endswith(".p3d")


def _model_split_merge_collection_sort_key(collection):
    name = getattr(collection, "name", "") or ""
    return (
        0 if _model_split_enum_collection_is_p3d_root(collection) else 1,
        _model_split_natural_name_key(name),
        name.lower(),
    )


def _iter_model_split_scene_collections(context):
    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    if scene_root is None:
        return []

    result = []
    seen = set()
    stack = list(getattr(scene_root, "children", []) or [])
    while stack:
        collection = stack.pop(0)
        if collection is None:
            continue
        try:
            ptr = collection.as_pointer()
        except Exception:
            ptr = id(collection)
        if ptr in seen:
            continue
        seen.add(ptr)
        result.append(collection)
        stack[0:0] = list(getattr(collection, "children", []) or [])
    return result


def _model_split_collection_from_enum_key(context, key: str):
    key = (key or "").strip()
    if not key.startswith("PTR_"):
        return None
    try:
        target_ptr = int(key[4:])
    except Exception:
        return None
    for collection in _iter_model_split_scene_collections(context):
        try:
            if collection.as_pointer() == target_ptr:
                return collection
        except Exception:
            continue
    return None


def _model_split_merge_collection_enum_items(self, context):
    del self
    collections = sorted(
        _iter_model_split_scene_collections(context),
        key=_model_split_merge_collection_sort_key,
    )
    items = [("__NONE__", "Select or use selection", "Use selected .p3d collection/object(s)")]
    for idx, collection in enumerate(collections, start=1):
        name = getattr(collection, "name", "") or "<collection>"
        desc = ".p3d root collection" if _model_split_enum_collection_is_p3d_root(collection) else "Scene collection"
        try:
            identifier = f"PTR_{collection.as_pointer()}"
        except Exception:
            continue
        items.append((identifier, name, desc, "OUTLINER_COLLECTION", idx))

    global _MODEL_SPLIT_MERGE_COLLECTION_ENUM_CACHE
    _MODEL_SPLIT_MERGE_COLLECTION_ENUM_CACHE = items
    return _MODEL_SPLIT_MERGE_COLLECTION_ENUM_CACHE


def _on_model_split_merge_source_key_changed(self, context):
    try:
        self.merge_source_collection = _model_split_collection_from_enum_key(
            context,
            getattr(self, "merge_source_collection_key", ""),
        )
    except Exception:
        pass


_MODEL_SPLIT_GRID_ORIGIN_MODE_ITEMS = (
    ("ACTIVE_OBJECT_BOUNDS", "Active Object Bounds", "Place the cutter grid at the active object's bounds center"),
    ("SOURCE_ROOT_BOUNDS", "Source Root Bounds", "Place the cutter grid at the source root collection bounds center"),
    ("SELECTION_BOUNDS", "Selection Bounds", "Place the cutter grid at the selected objects bounds center"),
    ("CURSOR", "Cursor", "Place the cutter grid at the 3D cursor"),
    ("MANUAL", "Manual", "Use the manual origin coordinates"),
)


def _poll_model_split_grid_source_object(self, obj):
    return obj is not None and getattr(obj, "type", None) == "MESH"


class CRAY_PG_ModelSplitMergeSourceItem(PropertyGroup):
    name: StringProperty(name="Name", default="")
    collection: PointerProperty(name="Collection", type=bpy.types.Collection)

class CRAY_PG_ModelSplitSettings(PropertyGroup):
    part_number: IntProperty(
        name="Part Number",
        description="Numeric suffix for the new split part collection",
        default=1,
        min=1,
        max=999,
    )
    named_model_name: StringProperty(
        name="New Model Name",
        default="",
        description="Name of the new standalone model; .p3d is added automatically",
    )
    named_transfer_mode: EnumProperty(
        name="Action",
        items=(
            ("MOVE", "Move", "Move selected objects out of the source model into the standalone model"),
            ("COPY", "Copy", "Copy selected objects into the standalone model and keep originals"),
        ),
        default="MOVE",
    )
    named_source_collection: PointerProperty(
        name="Source Collection",
        description="Pick the original .p3d root collection that selected meshes come from",
        type=bpy.types.Collection,
    )
    named_target_collection: PointerProperty(
        name="Target Collection",
        description="Pick a .p3d root collection or one of its child P3D category collections",
        type=bpy.types.Collection,
    )
    named_target_category: EnumProperty(
        name="Category",
        description="P3D category where selected mesh objects should be placed",
        items=_MODEL_SPLIT_TARGET_CATEGORY_ITEMS,
        default="RESOLUTION",
    )
    named_export_mode: EnumProperty(
        name="Export Target",
        items=(
            ("SOURCE_SIBLING", "Next to source", "Save Back to source path next to the original model"),
            ("CUSTOM_DIR", "Custom folder", "Save Back to source path into a selected folder"),
        ),
        default="SOURCE_SIBLING",
    )
    named_export_directory: StringProperty(
        name="Export Folder",
        default="",
        subtype="DIR_PATH",
        description="Folder used when Export Target is set to Custom folder",
    )
    merge_source_collection: PointerProperty(
        name="Merge Source",
        description="Source .p3d root collection to add to the merge list",
        type=bpy.types.Collection,
    )
    merge_source_collection_key: EnumProperty(
        name="Merge Source",
        description="Source collection sorted with .p3d roots first, then alphabetically",
        items=_model_split_merge_collection_enum_items,
        update=_on_model_split_merge_source_key_changed,
    )
    merge_sources: CollectionProperty(type=CRAY_PG_ModelSplitMergeSourceItem)
    merge_sources_index: IntProperty(default=0)
    grid_source_object: PointerProperty(
        name="Source Object",
        description="Single mesh object to split by the cutter grid",
        type=bpy.types.Object,
        poll=_poll_model_split_grid_source_object,
    )
    grid_source_root_collection: PointerProperty(
        name="Source Root Collection",
        description=".p3d root collection whose mesh objects will be split by the cutter grid",
        type=bpy.types.Collection,
    )
    grid_cutter_collection: PointerProperty(
        name="Cut Lines Collection",
        description="Collection containing editable grid cut guide lines",
        type=bpy.types.Collection,
    )
    grid_cell_size_x: FloatProperty(
        name="Cell Size X",
        description="Legacy cutter size along X; line split uses Parts X/Y count",
        default=10.0,
        min=0.001,
        soft_min=0.001,
    )
    grid_cell_size_y: FloatProperty(
        name="Cell Size Y",
        description="Legacy cutter size along Y; line split uses Parts X/Y count",
        default=10.0,
        min=0.001,
        soft_min=0.001,
    )
    grid_cell_size_z: FloatProperty(
        name="Cell Size Z",
        description="Legacy cutter size along Z; not used by line grid split",
        default=10.0,
        min=0.001,
        soft_min=0.001,
    )
    grid_count_x: IntProperty(
        name="Parts X",
        description="Number of output parts along X",
        default=3,
        min=1,
        max=999,
    )
    grid_count_y: IntProperty(
        name="Parts Y",
        description="Number of output parts along Y",
        default=3,
        min=1,
        max=999,
    )
    grid_count_z: IntProperty(
        name="Legacy Count Z",
        description="Legacy cutter-grid depth; line grid split uses X/Y parts",
        default=1,
        min=1,
        max=999,
    )
    grid_origin_mode: EnumProperty(
        name="Grid Origin Mode",
        description="How the starting center of the cutter grid is chosen",
        items=_MODEL_SPLIT_GRID_ORIGIN_MODE_ITEMS,
        default="ACTIVE_OBJECT_BOUNDS",
    )
    grid_manual_origin_x: FloatProperty(name="Manual Origin X", default=0.0)
    grid_manual_origin_y: FloatProperty(name="Manual Origin Y", default=0.0)
    grid_manual_origin_z: FloatProperty(name="Manual Origin Z", default=0.0)
    grid_output_prefix: StringProperty(
        name="Output Name Prefix",
        default="",
        description="Prefix for generated .p3d root collections; empty uses the source model name",
    )
    grid_use_visible_cutters_only: BoolProperty(
        name="Use Visible Guides Only",
        description="Ignore hidden guide lines during split",
        default=True,
    )
    grid_keep_original: BoolProperty(
        name="Keep Original",
        description="Keep source objects untouched after a successful grid split",
        default=True,
    )
    grid_hide_cutters_after_split: BoolProperty(
        name="Hide Guides After Split",
        description="Hide the guide line collection after splitting",
        default=False,
    )
    grid_skip_empty_pieces: BoolProperty(
        name="Skip Empty Pieces",
        description="Skip generated pieces below the minimum vertex or face thresholds",
        default=True,
    )
    grid_min_vertices: IntProperty(
        name="Min Vertices",
        description="Minimum vertex count for generated pieces",
        default=1,
        min=0,
        max=1000000,
    )
    grid_min_faces: IntProperty(
        name="Min Faces",
        description="Minimum face count for generated face pieces when Skip Empty Pieces is enabled",
        default=1,
        min=0,
        max=1000000,
    )
    grid_add_result_to_export_planner: BoolProperty(
        name="Add Result To Export Planner",
        description="Add generated .p3d root collections to the Import/Export planner",
        default=True,
    )


_COLLIDER_EXP_MODE_ITEMS = (
    ("BOX", "Box", "Generate a box collider from the current source bounds or selection"),
    ("CONVEX_HULL", "Convex Hull", "Generate a simplified convex hull from the current source"),
    ("CYLINDER_BOXES", "Cylinder Boxes", "Generate box segments around a cylindrical form"),
    ("PIPE_BOXES", "Pipe Boxes", "Generate box segments around a ring or pipe while leaving the hole open"),
    ("SPHERE", "Sphere", "Generate a low-poly spherical collider"),
    ("CAPSULE", "Capsule", "Generate a low-poly capsule collider"),
)
_COLLIDER_EXP_SCOPE_ITEMS = (
    ("FROM_SELECTED", "from selected", "Use the current selection exactly as before"),
    ("PER_SHELLS", "per shells", "Expand selected vertices to their connected mesh shells and create one collider per shell"),
    ("PER_OBJECT_COMPONENTS", "per obj comp", "Create one collider per connected component inside the selected vertices"),
    ("PER_OBJECTS", "per objects", "Create one collider for each selected mesh object"),
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
_VISUALS_COLLECTION_NAME = "Visuals"
_VISUALS_COLLECTION_COLOR = "COLOR_02"
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
_FIRE_GEOMETRY_LOD_TOKEN = "15"
_FIRE_GEOMETRY_RVMAT_FOLDER = r"P:\DZ\data\data\penetration"
_FIRE_GEOMETRY_MATERIAL_NONE = "__NONE__"
_FIRE_GEOMETRY_MATERIAL_ENUM_CACHE = [
    (_FIRE_GEOMETRY_MATERIAL_NONE, "<no materials>", "Selected Fire Geometry object has no assigned materials")
]
_MATERIAL_ADD_NEW = "__ADD_NEW__"
_COLLIDER_MATERIAL_SELECTION_SYNCING = False
_COLLIDER_OBJECT_TARGET_SYNCING = False
_FAKE_TERRAIN_TARGET_SYNCING = False
_FAKE_TERRAIN_TARGET_NONE = "__NONE__"
_COLLIDER_LOD_SYNCING_FROM_OBJECT = False
_COLLIDER_LOD_SYNCING_FROM_OBJECT_EXP = False
_GEOMETRY_LOD_TOKEN = "6"
_MODEL_SPLIT_TARGET_CATEGORY_SPECS = {
    "RESOLUTION": {
        "collection": _VISUALS_COLLECTION_NAME,
        "aliases": (),
        "color": _VISUALS_COLLECTION_COLOR,
        "lod": "0",
    },
    "GEOMETRIES": {
        "collection": _COLLIDER_COLLECTION_NAME,
        "aliases": _COLLIDER_COLLECTION_ALIASES,
        "color": _COLLIDER_COLLECTION_COLOR,
        "lod": "6",
    },
    "POINT_CLOUDS": {
        "collection": _MEMORY_COLLECTION_NAME,
        "aliases": _MEMORY_COLLECTION_ALIASES,
        "color": _MEMORY_COLLECTION_COLOR,
        "lod": "9",
    },
    "ROADWAY": {
        "collection": _MISC_COLLECTION_NAME,
        "aliases": (),
        "color": _MISC_COLLECTION_COLOR,
        "lod": _ROADWAY_LOD_TOKEN,
    },
}
_MODEL_SPLIT_GEOMETRY_LODS = {
    "6", "7", "8", "14", "15", "16", "17", "19", "20", "21", "22", "23", "24", "30"
}
_MODEL_SPLIT_POINT_CLOUD_LODS = {"9", "10", "13"}
_MODEL_SPLIT_ROADWAY_LODS = {"11"}


def _material_enum_items_for_objects(objects, *, none_value: str, missing_object_desc: str, missing_material_desc: str, slot_desc_prefix: str):
    items = []
    objects = [
        obj for obj in objects or []
        if obj is not None and getattr(obj, "type", None) == "MESH"
    ]

    if not objects:
        return [(none_value, missing_object_desc, missing_object_desc)]

    items = [(_MATERIAL_ADD_NEW, "Add New", f"Create and assign a new {slot_desc_prefix.lower()}")]
    material_count = 0

    seen = set()
    for obj in objects:
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

            label = _repair_mojibake_text(mat.name)
            desc = f"{slot_desc_prefix} from {obj.name} slot {slot_idx}"
            if image_names:
                uniq_images = sorted(set((_repair_mojibake_text(name) for name in image_names)), key=lambda x: x.lower())
                desc = f"{desc} | Images: {', '.join(uniq_images[:3])}"
                if len(uniq_images) > 3:
                    desc += f" (+{len(uniq_images) - 3} more)"

            items.append((mat.name, label, desc))
            material_count += 1

    if material_count == 0:
        items.append((none_value, "<no materials>", missing_material_desc))

    return items


def _material_enum_items_for_object(obj, *, none_value: str, missing_object_desc: str, missing_material_desc: str, slot_desc_prefix: str):
    return _material_enum_items_for_objects(
        [obj] if obj is not None else [],
        none_value=none_value,
        missing_object_desc=missing_object_desc,
        missing_material_desc=missing_material_desc,
        slot_desc_prefix=slot_desc_prefix,
    )


def get_roadway_material_enum_items(self, context):
    del self

    cs = getattr(getattr(context, "scene", None), "cray_collider_settings", None)
    obj = getattr(cs, "roadway_object", None) if cs else None
    items = _material_enum_items_for_object(
        obj,
        none_value=_ROADWAY_MATERIAL_NONE,
        missing_object_desc="<no roadway object>",
        missing_material_desc="Selected Roadway Object has no assigned materials",
        slot_desc_prefix="Roadway material",
    )

    global _ROADWAY_MATERIAL_ENUM_CACHE
    _ROADWAY_MATERIAL_ENUM_CACHE = items
    return _ROADWAY_MATERIAL_ENUM_CACHE


def get_fire_geometry_material_enum_items(self, context):
    del self

    obj = _resolve_fire_geometry_object_for_material(context)
    objects = _collider_material_selection_objects(context, "fire_geometry_object", obj) if obj is not None else []
    if not objects and obj is not None:
        objects = [obj]
    items = _material_enum_items_for_objects(
        objects,
        none_value=_FIRE_GEOMETRY_MATERIAL_NONE,
        missing_object_desc="<no fire geometry object>",
        missing_material_desc="Selected Fire Geometry object has no assigned materials",
        slot_desc_prefix="Fire Geometry material",
    )

    global _FIRE_GEOMETRY_MATERIAL_ENUM_CACHE
    _FIRE_GEOMETRY_MATERIAL_ENUM_CACHE = items
    return _FIRE_GEOMETRY_MATERIAL_ENUM_CACHE


def _on_collider_target_lod_changed(self, context):
    from .nh_collider import (_apply_collider_visual_style, _enable_collider_object_color_preview, _set_collider_settings_object, _sync_fire_geometry_material_selection)
    from .nh_snap import (_set_collider_lod_p3d_props)
    global _COLLIDER_LOD_SYNCING_FROM_OBJECT
    if _COLLIDER_LOD_SYNCING_FROM_OBJECT:
        return

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
        _set_collider_lod_p3d_props(target_obj, lod_token)
        _apply_collider_visual_style(target_obj)
        _enable_collider_object_color_preview(context)
        if lod_token == _FIRE_GEOMETRY_LOD_TOKEN:
            setattr(cs, "fire_geometry_object", target_obj)
            _sync_fire_geometry_material_selection(context)
        _set_collider_settings_object(context, "geometry_object", target_obj)
    except Exception:
        pass


def _unique_material_name(base_name: str) -> str:
    base = (base_name or "Material").strip() or "Material"
    if bpy.data.materials.get(base) is None:
        return base
    idx = 1
    while True:
        candidate = f"{base}.{idx:03d}"
        if bpy.data.materials.get(candidate) is None:
            return candidate
        idx += 1


def _ensure_material_slot(obj, mat):
    if obj is None or getattr(obj, "type", None) != "MESH" or mat is None or obj.data is None:
        return -1
    for idx, existing in enumerate(obj.data.materials):
        if existing == mat:
            return idx
    obj.data.materials.append(mat)
    return len(obj.data.materials) - 1


def _assign_material_to_target_selection(obj, mat) -> bool:
    slot_idx = _ensure_material_slot(obj, mat)
    if slot_idx < 0:
        return False

    assigned = False
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        selected_faces = [face for face in bm.faces if face.select]
        if selected_faces:
            for face in selected_faces:
                face.material_index = slot_idx
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            assigned = True

    if not assigned:
        for poly in obj.data.polygons:
            poly.material_index = slot_idx
        assigned = len(obj.data.polygons) > 0

    return assigned


def _material_assignment_object_predicate(object_attr: str):
    if object_attr == "fire_geometry_object":
        return lambda obj: _poll_fire_geometry_object(None, obj)
    if object_attr == "roadway_object":
        return lambda obj: _poll_roadway_object(None, obj)
    return lambda obj: obj is not None and getattr(obj, "type", None) == "MESH"


def _selected_material_assignment_objects(context, object_attr: str):
    predicate = _material_assignment_object_predicate(object_attr)
    objects = []
    seen = set()
    for obj in getattr(context, "selected_objects", []):
        if not predicate(obj):
            continue
        try:
            ptr = obj.as_pointer()
        except Exception:
            ptr = id(obj)
        if ptr in seen:
            continue
        seen.add(ptr)
        objects.append(obj)
    return objects


def _collider_material_selection_objects(context, object_attr: str, target_obj):
    from .nh_textures import (_collect_collection_objects_recursive)
    predicate = _material_assignment_object_predicate(object_attr)
    objects = []
    seen = set()

    def add(obj):
        if obj is None or not predicate(obj):
            return
        try:
            ptr = obj.as_pointer()
        except Exception:
            ptr = id(obj)
        if ptr in seen:
            return
        seen.add(ptr)
        objects.append(obj)

    for obj in _selected_material_assignment_objects(context, object_attr):
        add(obj)
    add(target_obj)

    for col in getattr(target_obj, "users_collection", []) or []:
        for obj in _collect_collection_objects_recursive(col):
            add(obj)

    return objects


def _create_and_assign_target_material(context, *, object_attr: str, material_attr: str, default_name: str, sync_fn):
    global _COLLIDER_MATERIAL_SELECTION_SYNCING

    cs = getattr(getattr(context, "scene", None), "cray_collider_settings", None)
    if cs is None:
        return None

    target_obj = getattr(cs, object_attr, None)
    if object_attr == "fire_geometry_object":
        target_obj = _resolve_fire_geometry_object_for_material(context)
    if target_obj is None or getattr(target_obj, "type", None) != "MESH":
        _COLLIDER_MATERIAL_SELECTION_SYNCING = True
        try:
            setattr(cs, material_attr, _FIRE_GEOMETRY_MATERIAL_NONE if object_attr == "fire_geometry_object" else _ROADWAY_MATERIAL_NONE)
        finally:
            _COLLIDER_MATERIAL_SELECTION_SYNCING = False
        return None

    mat = bpy.data.materials.new(_unique_material_name(default_name))
    assignment_objects = _selected_material_assignment_objects(context, object_attr)
    if not assignment_objects:
        assignment_objects = [target_obj]

    target_in_assignment = False
    for obj in assignment_objects:
        if obj == target_obj:
            target_in_assignment = True
        _assign_material_to_target_selection(obj, mat)
    if not target_in_assignment:
        _ensure_material_slot(target_obj, mat)

    sync_fn(context, mat.name)
    return mat


def _on_roadway_material_changed(self, context):
    from .nh_collider import (_sync_roadway_material_selection)
    if _COLLIDER_MATERIAL_SELECTION_SYNCING:
        return
    if (getattr(self, "roadway_material", "") or "") != _MATERIAL_ADD_NEW:
        return
    _create_and_assign_target_material(
        context,
        object_attr="roadway_object",
        material_attr="roadway_material",
        default_name="RoadwayMaterial",
        sync_fn=_sync_roadway_material_selection,
    )


def _on_fire_geometry_material_changed(self, context):
    from .nh_collider import (_sync_fire_geometry_material_selection)
    if _COLLIDER_MATERIAL_SELECTION_SYNCING:
        return
    if (getattr(self, "fire_geometry_material", "") or "") != _MATERIAL_ADD_NEW:
        return
    _create_and_assign_target_material(
        context,
        object_attr="fire_geometry_object",
        material_attr="fire_geometry_material",
        default_name="FireGeometryMaterial",
        sync_fn=_sync_fire_geometry_material_selection,
    )


def _actual_collider_lod_token_from_object(obj) -> str:
    if obj is None or getattr(obj, "type", None) != "MESH":
        return ""

    if not hasattr(obj, "a3ob_properties_object"):
        return ""

    try:
        props = obj.a3ob_properties_object
        if not bool(getattr(props, "is_a3_lod", False)):
            return ""
        return str(getattr(props, "lod", "") or "").strip()
    except Exception:
        return ""


def _collider_lod_token_from_object(obj, *, allow_name_fallback: bool = True) -> str:
    from .nh_snap import (_logical_collection_name)
    lod_token = _actual_collider_lod_token_from_object(obj)
    if lod_token in _COLLIDER_LOD_NAMES:
        return lod_token

    if not allow_name_fallback or obj is None or getattr(obj, "type", None) != "MESH":
        return ""

    logical_name = _logical_collection_name(getattr(obj, "name", "") or "")
    for lod_token, lod_name in _COLLIDER_LOD_NAMES.items():
        if logical_name == _logical_collection_name(lod_name):
            return str(lod_token)

    return ""


def _resolve_fire_geometry_object_for_material(context):
    cs = getattr(getattr(context, "scene", None), "cray_collider_settings", None)
    if cs is None:
        return None

    candidates = []

    active = getattr(getattr(context, "view_layer", None), "objects", None) if context is not None else None
    active = getattr(active, "active", None) if active is not None else None
    for obj in (active,):
        if obj is not None and obj not in candidates:
            candidates.append(obj)
    for obj in getattr(context, "selected_objects", []) or []:
        if obj is not None and obj not in candidates:
            candidates.append(obj)
    for obj in (
        getattr(cs, "fire_geometry_object", None),
        getattr(cs, "geometry_object", None),
    ):
        if obj is not None and obj not in candidates:
            candidates.append(obj)

    for obj in candidates:
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        if _collider_lod_token_from_object(obj, allow_name_fallback=True) == _FIRE_GEOMETRY_LOD_TOKEN:
            return obj

    return None


def _object_name_startswith_lod(obj, lod_token: str) -> bool:
    from .nh_snap import (_collider_lod_name, _logical_collection_name)
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False

    expected = _logical_collection_name(_collider_lod_name(lod_token))
    actual = _logical_collection_name(getattr(obj, "name", "") or "")
    return bool(expected and actual.startswith(expected))


def _poll_fire_geometry_object(self, obj):
    del self
    return _object_name_startswith_lod(obj, _FIRE_GEOMETRY_LOD_TOKEN)


def _poll_roadway_object(self, obj):
    del self
    return _object_name_startswith_lod(obj, _ROADWAY_LOD_TOKEN)


def _active_or_selected_mesh_object(context):
    active = getattr(getattr(context, "view_layer", None), "objects", None)
    active_obj = getattr(active, "active", None) if active is not None else None
    if active_obj is not None and getattr(active_obj, "type", None) == "MESH":
        return active_obj
    for obj in getattr(context, "selected_objects", []):
        if obj is not None and getattr(obj, "type", None) == "MESH":
            return obj
    return None


def _on_collider_geometry_object_changed(self, context):
    from .nh_collider import (_apply_collider_visual_style, _enable_collider_object_color_preview, _sync_fire_geometry_material_selection)
    from .nh_snap import (_ensure_object_selectable_in_view_layer)
    global _COLLIDER_OBJECT_TARGET_SYNCING
    if _COLLIDER_OBJECT_TARGET_SYNCING:
        return
    if context is None:
        return

    target_obj = getattr(self, "geometry_object", None)
    lod_token = _collider_lod_token_from_object(target_obj, allow_name_fallback=True)
    if lod_token not in _COLLIDER_LOD_NAMES:
        return

    try:
        _ensure_object_selectable_in_view_layer(context, target_obj)
        if str(getattr(self, "target_lod", "") or "") != lod_token:
            global _COLLIDER_LOD_SYNCING_FROM_OBJECT
            _COLLIDER_LOD_SYNCING_FROM_OBJECT = True
            try:
                self.target_lod = lod_token
            finally:
                _COLLIDER_LOD_SYNCING_FROM_OBJECT = False
        else:
            _apply_collider_visual_style(target_obj)
            _enable_collider_object_color_preview(context)
        if lod_token == _FIRE_GEOMETRY_LOD_TOKEN:
            _COLLIDER_OBJECT_TARGET_SYNCING = True
            try:
                self.fire_geometry_object = target_obj
            finally:
                _COLLIDER_OBJECT_TARGET_SYNCING = False
            _sync_fire_geometry_material_selection(context)
    except Exception:
        _COLLIDER_OBJECT_TARGET_SYNCING = False
        pass


def _on_fire_geometry_object_changed(self, context):
    from .nh_collider import (_sync_fire_geometry_material_selection)
    from .nh_snap import (_ensure_object_selectable_in_view_layer)
    global _COLLIDER_OBJECT_TARGET_SYNCING
    if _COLLIDER_OBJECT_TARGET_SYNCING:
        return
    if context is None:
        return

    target_obj = getattr(self, "fire_geometry_object", None)
    if not _poll_fire_geometry_object(None, target_obj):
        return

    try:
        _ensure_object_selectable_in_view_layer(context, target_obj)
    except Exception:
        pass
    try:
        if getattr(self, "geometry_object", None) != target_obj:
            _COLLIDER_OBJECT_TARGET_SYNCING = True
            try:
                self.geometry_object = target_obj
            finally:
                _COLLIDER_OBJECT_TARGET_SYNCING = False
    except Exception:
        _COLLIDER_OBJECT_TARGET_SYNCING = False
        pass
    try:
        if str(getattr(self, "target_lod", "") or "") != _FIRE_GEOMETRY_LOD_TOKEN:
            global _COLLIDER_LOD_SYNCING_FROM_OBJECT
            _COLLIDER_LOD_SYNCING_FROM_OBJECT = True
            try:
                self.target_lod = _FIRE_GEOMETRY_LOD_TOKEN
            finally:
                _COLLIDER_LOD_SYNCING_FROM_OBJECT = False
    except Exception:
        pass
    _sync_fire_geometry_material_selection(context)


def _on_roadway_object_changed(self, context):
    from .nh_collider import (_sync_roadway_material_selection)
    from .nh_snap import (_ensure_object_selectable_in_view_layer)
    if context is None:
        return

    target_obj = getattr(self, "roadway_object", None)
    if not _poll_roadway_object(None, target_obj):
        return

    try:
        _ensure_object_selectable_in_view_layer(context, target_obj)
    except Exception:
        pass
    _sync_roadway_material_selection(context)


def _on_collider_exp_target_lod_changed_exp(self, context):
    from .nh_collider import (_apply_collider_visual_style, _enable_collider_object_color_preview)
    from .nh_snap import (_set_collider_lod_p3d_props)
    global _COLLIDER_LOD_SYNCING_FROM_OBJECT_EXP
    if _COLLIDER_LOD_SYNCING_FROM_OBJECT_EXP:
        return
    if context is None:
        return

    target_obj = getattr(self, "geometry_object", None)
    if target_obj is None or target_obj.type != "MESH":
        return

    lod_token = str(getattr(self, "target_lod", "") or "").strip()
    if lod_token not in _COLLIDER_LOD_NAMES:
        return

    try:
        _set_collider_lod_p3d_props(target_obj, lod_token)
        _apply_collider_visual_style(target_obj)
        _enable_collider_object_color_preview(context)
    except Exception:
        pass


def _on_collider_exp_geometry_object_changed_exp(self, context):
    from .nh_collider import (_apply_collider_visual_style, _enable_collider_object_color_preview)
    from .nh_snap import (_ensure_object_selectable_in_view_layer)
    if context is None:
        return

    target_obj = getattr(self, "geometry_object", None)
    lod_token = _collider_lod_token_from_object(target_obj, allow_name_fallback=True)
    if lod_token not in _COLLIDER_LOD_NAMES:
        return

    try:
        _ensure_object_selectable_in_view_layer(context, target_obj)
        if str(getattr(self, "target_lod", "") or "") != lod_token:
            global _COLLIDER_LOD_SYNCING_FROM_OBJECT_EXP
            _COLLIDER_LOD_SYNCING_FROM_OBJECT_EXP = True
            try:
                self.target_lod = lod_token
            finally:
                _COLLIDER_LOD_SYNCING_FROM_OBJECT_EXP = False
        else:
            _apply_collider_visual_style(target_obj)
            _enable_collider_object_color_preview(context)
    except Exception:
        pass


def _fake_terrain_context_root_collection(context, settings):
    from .nh_textures import (_find_p3d_root_collection_for_object)
    candidates = []

    def add(obj):
        if obj is not None and obj not in candidates:
            candidates.append(obj)

    add(getattr(settings, "fake_terrain_source_object", None))
    add(getattr(settings, "source_object", None))
    add(getattr(settings, "fake_terrain_target_object", None))
    add(getattr(settings, "geometry_object", None))
    add(getattr(settings, "fire_geometry_object", None))

    active = getattr(getattr(context, "view_layer", None), "objects", None) if context is not None else None
    add(getattr(active, "active", None) if active is not None else None)
    for obj in getattr(context, "selected_objects", []) or []:
        add(obj)

    for obj in candidates:
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        try:
            root = _find_p3d_root_collection_for_object(context, obj)
        except Exception:
            root = None
        if root is not None:
            return root
    return None


def _fake_terrain_target_candidates(context, settings):
    from .nh_snap import (_collider_lod_name, _find_named_child_collection)
    root = _fake_terrain_context_root_collection(context, settings)
    if root is None:
        return []

    try:
        collider_collection = _find_named_child_collection(
            root,
            _COLLIDER_COLLECTION_NAME,
            aliases=_COLLIDER_COLLECTION_ALIASES,
        )
    except Exception:
        collider_collection = None
    if collider_collection is None:
        return []

    candidates = []
    seen = set()

    def add_for_lod(lod_token):
        from .nh_snap import (_collider_lod_name)
        expected_name = _collider_lod_name(lod_token)
        ordered = []
        direct = getattr(collider_collection, "objects", {}).get(expected_name)
        if direct is not None:
            ordered.append(direct)
        ordered.extend(list(getattr(collider_collection, "objects", []) or []))

        for obj in ordered:
            if obj is None or getattr(obj, "type", None) != "MESH":
                continue
            try:
                ptr = obj.as_pointer()
            except Exception:
                ptr = id(obj)
            if ptr in seen:
                continue
            if _collider_lod_token_from_object(obj, allow_name_fallback=True) != str(lod_token):
                continue
            seen.add(ptr)
            candidates.append((str(lod_token), obj))
            return

    for lod_token in ("6", "14", _FIRE_GEOMETRY_LOD_TOKEN):
        add_for_lod(lod_token)

    return candidates


def get_fake_terrain_target_enum_items(self, context):
    from .nh_snap import (_collider_lod_name)
    items = []
    for lod_token, obj in _fake_terrain_target_candidates(context, self):
        lod_name = _collider_lod_name(lod_token)
        label = f"{lod_name}: {obj.name}"
        items.append((obj.name, label, f"Create fake terrain in {lod_name} object '{obj.name}'"))
    if not items:
        items.append((_FAKE_TERRAIN_TARGET_NONE, "<no Geometry LODs in current model>", "Pick a Source Visual inside a .p3d model with Geometries"))
    return items


def _set_fake_terrain_target_object(context, settings, obj, *, sync_choice=True):
    from .nh_collider import (_set_collider_settings_object, _sync_fire_geometry_material_selection)
    from .nh_snap import (_ensure_object_selectable_in_view_layer)
    global _FAKE_TERRAIN_TARGET_SYNCING

    if obj is None or getattr(obj, "type", None) != "MESH":
        return False

    lod_token = _collider_lod_token_from_object(obj, allow_name_fallback=True)
    if lod_token not in _COLLIDER_LOD_NAMES:
        return False

    _FAKE_TERRAIN_TARGET_SYNCING = True
    try:
        settings.fake_terrain_target_object = obj
        settings.fake_terrain_target_lod = lod_token
        if sync_choice:
            try:
                settings.fake_terrain_target_choice = obj.name
            except Exception:
                pass
    finally:
        _FAKE_TERRAIN_TARGET_SYNCING = False

    try:
        _ensure_object_selectable_in_view_layer(context, obj)
    except Exception:
        pass
    try:
        _set_collider_settings_object(context, "geometry_object", obj)
        if lod_token == _FIRE_GEOMETRY_LOD_TOKEN:
            _set_collider_settings_object(context, "fire_geometry_object", obj)
            _sync_fire_geometry_material_selection(context)
    except Exception:
        pass
    return True


def _on_fake_terrain_target_choice_changed(self, context):
    if _FAKE_TERRAIN_TARGET_SYNCING:
        return

    choice = str(getattr(self, "fake_terrain_target_choice", "") or "")
    if not choice or choice == _FAKE_TERRAIN_TARGET_NONE:
        return
    obj = bpy.data.objects.get(choice)
    _set_fake_terrain_target_object(context, self, obj, sync_choice=False)


from .nh_base import (_COLLIDER_TARGET_LOD_ITEMS)

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
        update=_on_collider_geometry_object_changed,
    )
    target_lod: EnumProperty(
        name="Target LOD",
        description="P3D LOD type for the generated collider object",
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
    show_fire_geometry_tools: BoolProperty(
        name="Show Fire Geometry Tools",
        description="Show Fire Geometry material tools",
        default=True,
    )
    show_roadway_tools: BoolProperty(
        name="Show Roadway Tools",
        description="Show Roadway material and weld tools",
        default=True,
    )
    show_fake_terrain_tools: BoolProperty(
        name="Show Fake Terrain Tools",
        description="Show adaptive fake terrain geometry tools",
        default=True,
    )
    fire_geometry_object: PointerProperty(
        name="Fire Geometry Object",
        description="Fire Geometry LOD mesh stored in Geometries collection",
        type=bpy.types.Object,
        poll=_poll_fire_geometry_object,
        update=_on_fire_geometry_object_changed,
    )
    fire_geometry_material: EnumProperty(
        name="Fire Geometry Material",
        description="Current material on the Fire Geometry object",
        items=get_fire_geometry_material_enum_items,
        update=_on_fire_geometry_material_changed,
    )
    roadway_object: PointerProperty(
        name="Roadway Object",
        description="Roadway LOD mesh stored in Misc collection",
        type=bpy.types.Object,
        poll=_poll_roadway_object,
        update=_on_roadway_object_changed,
    )
    roadway_material: EnumProperty(
        name="Roadway Material",
        description="Current material on the Roadway object",
        items=get_roadway_material_enum_items,
        update=_on_roadway_material_changed,
    )
    roadway_weld_distance: FloatProperty(
        name="Roadway Weld Distance",
        description="Merge nearly coincident Roadway vertices so AI pathing stays fully connected",
        default=0.0001,
        min=0.0,
        precision=6,
        unit="LENGTH",
    )
    fake_terrain_source_object: PointerProperty(
        name="Source Visual",
        description="Visual terrain mesh whose selected Edit Mode faces are used to build fake terrain geometry",
        type=bpy.types.Object,
    )
    fake_terrain_target_object: PointerProperty(
        name="Target LOD Object",
        description="Geometry LOD mesh that receives generated fake terrain components",
        type=bpy.types.Object,
    )
    fake_terrain_target_choice: EnumProperty(
        name="Target",
        description="Geometry/View Geometry/Fire Geometry object in the current .p3d model that receives generated fake terrain",
        items=get_fake_terrain_target_enum_items,
        update=_on_fake_terrain_target_choice_changed,
    )
    fake_terrain_target_lod: EnumProperty(
        name="Target LOD",
        description="LOD that receives generated fake terrain components",
        items=_COLLIDER_TARGET_LOD_ITEMS,
        default=_FIRE_GEOMETRY_LOD_TOKEN,
    )
    fake_terrain_patch_size: FloatProperty(
        name="Patch Size",
        description="Starting XY size for fake terrain components; larger values make bigger, fewer slabs",
        default=8.0,
        min=0.25,
        precision=2,
        unit="LENGTH",
    )
    fake_terrain_min_patch_size: FloatProperty(
        name="Min Patch",
        description="Smallest XY size allowed when refining pits and uneven terrain",
        default=1.0,
        min=0.05,
        precision=2,
        unit="LENGTH",
    )
    fake_terrain_depression_error: FloatProperty(
        name="Pit Error",
        description="Maximum allowed vertical bridge over lower source points before a patch is split",
        default=0.15,
        min=0.0,
        precision=3,
        unit="LENGTH",
    )
    fake_terrain_hill_error: FloatProperty(
        name="Hill Error",
        description="Maximum allowed source height above the fitted patch before a patch is split",
        default=0.35,
        min=0.0,
        precision=3,
        unit="LENGTH",
    )
    fake_terrain_thickness: FloatProperty(
        name="Thickness",
        description="Vertical thickness of each closed fake terrain component",
        default=1.0,
        min=0.05,
        precision=2,
        unit="LENGTH",
    )


from .nh_base import (_COLLIDER_TARGET_LOD_ITEMS)

class CRAY_PG_ColliderExpSettings(PropertyGroup):
    enabled: BoolProperty(
        name="Enable Experimental Collider Tools",
        default=True,
    )
    source_object: PointerProperty(
        type=bpy.types.Object,
        name="Source Object",
        description="Source object used by the experimental collider generators",
    )
    target_lod: EnumProperty(
        name="Target LOD",
        description="P3D LOD type for experimental collider output",
        items=_COLLIDER_TARGET_LOD_ITEMS,
        default="6",
        update=_on_collider_exp_target_lod_changed_exp,
    )
    geometry_object: PointerProperty(
        type=bpy.types.Object,
        name="Target LOD Object",
        description="Geometry LOD mesh that receives experimental collider geometry",
        update=_on_collider_exp_geometry_object_changed_exp,
    )
    exp_mode: EnumProperty(
        name="Collider Type",
        items=_COLLIDER_EXP_MODE_ITEMS,
        default="BOX",
    )
    collider_scope: EnumProperty(
        name="Create Mode",
        items=_COLLIDER_EXP_SCOPE_ITEMS,
        default="FROM_SELECTED",
    )
    scale_x: FloatProperty(
        name="Scale X",
        default=1.0,
        min=0.001,
        precision=4,
    )
    scale_y: FloatProperty(
        name="Scale Y",
        default=1.0,
        min=0.001,
        precision=4,
    )
    scale_z: FloatProperty(
        name="Scale Z",
        default=1.0,
        min=0.001,
        precision=4,
    )
    scale_multiplier: FloatProperty(
        name="Scale Multiplier",
        default=1.0,
        min=0.001,
        precision=4,
    )
    offset_x: FloatProperty(
        name="Offset X",
        default=0.0,
        precision=4,
        unit="LENGTH",
    )
    offset_y: FloatProperty(
        name="Offset Y",
        default=0.0,
        precision=4,
        unit="LENGTH",
    )
    offset_z: FloatProperty(
        name="Offset Z",
        default=0.0,
        precision=4,
        unit="LENGTH",
    )
    floor_contact: BoolProperty(
        name="Floor Contact",
        default=False,
    )
    minimum_size: FloatProperty(
        name="Minimum Size",
        description="Smallest generated collider axis/thickness for boxes, rounded box segments, spheres, and capsules",
        default=0.05,
        min=0.0,
        precision=4,
        unit="LENGTH",
    )
    normal_minimum_size: BoolProperty(
        name="Normal Min Size",
        description="For flat box sources, add missing Minimum Size thickness opposite to the averaged face normal instead of centering it",
        default=False,
    )
    convex_detail: IntProperty(
        name="Hull Detail",
        description="Simplification/detail level for experimental convex hull",
        default=16,
        min=4,
        max=128,
    )
    convex_max_triangles: IntProperty(
        name="Max Hull Triangles",
        description="Triangle budget used when simplifying experimental convex hulls",
        default=64,
        min=4,
        max=2048,
    )
    cylinder_segments: IntProperty(
        name="Cylinder Segments",
        default=16,
        min=4,
        max=128,
    )
    pipe_segments: IntProperty(
        name="Pipe Segments",
        default=24,
        min=4,
        max=128,
    )
    pipe_inner_radius: FloatProperty(
        name="Pipe Inner Radius",
        default=0.5,
        min=0.0,
        precision=4,
        unit="LENGTH",
    )
    pipe_outer_radius: FloatProperty(
        name="Pipe Outer Radius",
        default=1.0,
        min=0.001,
        precision=4,
        unit="LENGTH",
    )
    pipe_depth: FloatProperty(
        name="Pipe Depth",
        default=0.25,
        min=0.001,
        precision=4,
        unit="LENGTH",
    )
    pipe_thickness: FloatProperty(
        name="Pipe Thickness",
        default=0.25,
        min=0.0,
        precision=4,
        unit="LENGTH",
    )
    sphere_segments: IntProperty(
        name="Sphere Segments",
        default=16,
        min=8,
        max=64,
    )
    capsule_radius: FloatProperty(
        name="Capsule Radius",
        default=0.5,
        min=0.001,
        precision=4,
        unit="LENGTH",
    )
    capsule_height: FloatProperty(
        name="Capsule Height",
        default=2.0,
        min=0.001,
        precision=4,
        unit="LENGTH",
    )
    capsule_cap_size: FloatProperty(
        name="Capsule Cap Size",
        default=0.5,
        min=0.001,
        precision=4,
        unit="LENGTH",
    )
    capsule_follow_source_angle: BoolProperty(
        name="Capsule Follow Source Angle",
        description="Align capsule top and bottom along the selected shell/object direction instead of world/local Z",
        default=False,
    )
    capsule_vertical_align: BoolProperty(
        name="Capsule Vertical Align",
        default=True,
    )
    recalc_normals: BoolProperty(
        name="Recalculate Normals",
        default=True,
    )
    merge_distance: FloatProperty(
        name="Merge Distance",
        default=0.0,
        min=0.0,
        precision=5,
        unit="LENGTH",
    )




_CUSTOM_KEYBIND_DEFINITIONS = (
    ("cray.copy_selected_verts_to_geometry", "Copy Selected Verts To Geometry", "Ctrl+Shift+C", None),
    ("cray.select_isolated_vertices", "Select Isolated Verts", "Ctrl+Shift+X", None),
    ("cray.generate_convex_hull_collider_exp", "Create Collider -> Convex Hull", "Mouse4", None),
    ("cray.generate_box_collider_exp", "Create Collider -> Box", "Mouse5", None),
    ("cray.delete_last_collider_exp", "Delete Last Created Collider", "Ctrl+Mouse4", None),
    ("cray.select_connected_shell_from_selection_exp", "Select Connected Shell", "Ctrl+Mouse5", None),
    ("cray.create_plain_axis_pivot", "Create Plain Axis Pivot", "Ctrl+Shift+P", "plain_axis"),
)


def _ui_panel_settings_from_context(context):
    scene = getattr(context, "scene", None) if context is not None else None
    return getattr(scene, "cray_ui_panel_settings", None) if scene is not None else None


def _is_ui_panel_visible(context, key: str) -> bool:
    settings = _ui_panel_settings_from_context(context)
    if settings is None:
        return True
    return bool(getattr(settings, f"show_{key}", True))


def _ui_panel_order_prop_name(key: str) -> str:
    return f"order_{key}"


def _ui_panel_default_order(key: str, fallback_index: int = 0) -> int:
    from .nh_base import (_UI_PANEL_DEFAULT_ORDER, _UI_PANEL_LAYOUT_ORDER_STEP)
    return int(_UI_PANEL_DEFAULT_ORDER.get(key, (fallback_index + 1) * _UI_PANEL_LAYOUT_ORDER_STEP))


def _ui_panel_order_value(settings, key: str, fallback_index: int = 0) -> int:
    if settings is None:
        return _ui_panel_default_order(key, fallback_index)
    prop_name = _ui_panel_order_prop_name(key)
    try:
        value = int(getattr(settings, prop_name))
    except Exception:
        value = 0
    return value if value > 0 else _ui_panel_default_order(key, fallback_index)


def _sorted_ui_panel_layout_definitions(settings=None):
    from .nh_base import (_UI_PANEL_LAYOUT_DEFINITIONS)
    indexed = list(enumerate(_UI_PANEL_LAYOUT_DEFINITIONS))
    indexed.sort(key=lambda item: (_ui_panel_order_value(settings, item[1][0], item[0]), item[0]))
    return [definition for _idx, definition in indexed]


def _apply_ui_panel_class_order(settings=None):
    from .nh_base import (_UI_PANEL_LAYOUT_DEFINITIONS)
    for fallback_index, (key, _label, class_name) in enumerate(_UI_PANEL_LAYOUT_DEFINITIONS):
        panel_cls = globals().get(class_name)
        if panel_cls is None:
            try:
                panel_cls = getattr(bpy.types, class_name, None)
            except Exception:
                panel_cls = None
        if panel_cls is None:
            continue
        try:
            panel_cls.bl_order = _ui_panel_order_value(settings, key, fallback_index)
        except Exception:
            pass


def _normalize_ui_panel_order(settings, ordered_keys):
    from .nh_base import (_UI_PANEL_LAYOUT_ORDER_STEP)
    if settings is None:
        return
    for idx, key in enumerate(ordered_keys):
        prop_name = _ui_panel_order_prop_name(key)
        if not hasattr(settings, prop_name):
            continue
        try:
            setattr(settings, prop_name, (idx + 1) * _UI_PANEL_LAYOUT_ORDER_STEP)
        except Exception:
            pass


def _tag_ui_redraw(context=None):
    try:
        wm = getattr(bpy.context, "window_manager", None)
        windows = getattr(wm, "windows", []) if wm is not None else []
        for window in windows:
            screen = getattr(window, "screen", None)
            for area in getattr(screen, "areas", []) or []:
                if getattr(area, "type", None) == "VIEW_3D":
                    area.tag_redraw()
    except Exception:
        pass
    try:
        area = getattr(context, "area", None) if context is not None else None
        if area is not None:
            area.tag_redraw()
    except Exception:
        pass


def _on_ui_panel_layout_setting_changed(self, context):
    _apply_ui_panel_class_order(self)
    _tag_ui_redraw(context)


from .nh_base import (_UI_PANEL_DEFAULT_ORDER)

class CRAY_PG_UIPanelSettings(PropertyGroup):
    order_collider: IntProperty(name="Order", default=_UI_PANEL_DEFAULT_ORDER["collider"], min=1, update=_on_ui_panel_layout_setting_changed)
    order_geometry_lods: IntProperty(name="Order", default=_UI_PANEL_DEFAULT_ORDER["geometry_lods"], min=1, update=_on_ui_panel_layout_setting_changed)
    order_asset_library: IntProperty(name="Order", default=_UI_PANEL_DEFAULT_ORDER["asset_library"], min=1, update=_on_ui_panel_layout_setting_changed)
    order_snap_points: IntProperty(name="Order", default=_UI_PANEL_DEFAULT_ORDER["snap_points"], min=1, update=_on_ui_panel_layout_setting_changed)
    order_import_export: IntProperty(name="Order", default=_UI_PANEL_DEFAULT_ORDER["import_export"], min=1, update=_on_ui_panel_layout_setting_changed)
    order_fixes: IntProperty(name="Order", default=_UI_PANEL_DEFAULT_ORDER["fixes"], min=1, update=_on_ui_panel_layout_setting_changed)
    order_model_split: IntProperty(name="Order", default=_UI_PANEL_DEFAULT_ORDER["model_split"], min=1, update=_on_ui_panel_layout_setting_changed)
    order_texture_replace: IntProperty(name="Order", default=_UI_PANEL_DEFAULT_ORDER["texture_replace"], min=1, update=_on_ui_panel_layout_setting_changed)
    order_cache_manager: IntProperty(name="Order", default=_UI_PANEL_DEFAULT_ORDER["cache_manager"], min=1, update=_on_ui_panel_layout_setting_changed)
    order_object_builder: IntProperty(name="Order", default=_UI_PANEL_DEFAULT_ORDER["object_builder"], min=1, update=_on_ui_panel_layout_setting_changed)
    show_snap_points: BoolProperty(name="Show", description="РџРѕРєР°Р·С‹РІР°С‚СЊ РёР»Рё СЃРєСЂС‹РІР°С‚СЊ СЌС‚Рѕ РјРµРЅСЋ РІ РїР°РЅРµР»Рё NH Plugin", default=True, update=_on_ui_panel_layout_setting_changed)
    show_asset_library: BoolProperty(name="Show", description="РџРѕРєР°Р·С‹РІР°С‚СЊ РёР»Рё СЃРєСЂС‹РІР°С‚СЊ СЌС‚Рѕ РјРµРЅСЋ РІ РїР°РЅРµР»Рё NH Plugin", default=True, update=_on_ui_panel_layout_setting_changed)
    show_fixes: BoolProperty(name="Show", description="РџРѕРєР°Р·С‹РІР°С‚СЊ РёР»Рё СЃРєСЂС‹РІР°С‚СЊ СЌС‚Рѕ РјРµРЅСЋ РІ РїР°РЅРµР»Рё NH Plugin", default=True, update=_on_ui_panel_layout_setting_changed)
    show_import_export: BoolProperty(name="Show", description="РџРѕРєР°Р·С‹РІР°С‚СЊ РёР»Рё СЃРєСЂС‹РІР°С‚СЊ СЌС‚Рѕ РјРµРЅСЋ РІ РїР°РЅРµР»Рё NH Plugin", default=True, update=_on_ui_panel_layout_setting_changed)
    show_model_split: BoolProperty(name="Show", description="РџРѕРєР°Р·С‹РІР°С‚СЊ РёР»Рё СЃРєСЂС‹РІР°С‚СЊ СЌС‚Рѕ РјРµРЅСЋ РІ РїР°РЅРµР»Рё NH Plugin", default=True, update=_on_ui_panel_layout_setting_changed)
    show_texture_replace: BoolProperty(name="Show", description="РџРѕРєР°Р·С‹РІР°С‚СЊ РёР»Рё СЃРєСЂС‹РІР°С‚СЊ СЌС‚Рѕ РјРµРЅСЋ РІ РїР°РЅРµР»Рё NH Plugin", default=True, update=_on_ui_panel_layout_setting_changed)
    show_collider: BoolProperty(name="Show", description="РџРѕРєР°Р·С‹РІР°С‚СЊ РёР»Рё СЃРєСЂС‹РІР°С‚СЊ СЌС‚Рѕ РјРµРЅСЋ РІ РїР°РЅРµР»Рё NH Plugin", default=True, update=_on_ui_panel_layout_setting_changed)
    show_geometry_lods: BoolProperty(name="Show", description="РџРѕРєР°Р·С‹РІР°С‚СЊ РёР»Рё СЃРєСЂС‹РІР°С‚СЊ СЌС‚Рѕ РјРµРЅСЋ РІ РїР°РЅРµР»Рё NH Plugin", default=True, update=_on_ui_panel_layout_setting_changed)
    show_object_builder: BoolProperty(name="Show", description="РџРѕРєР°Р·С‹РІР°С‚СЊ РёР»Рё СЃРєСЂС‹РІР°С‚СЊ СЌС‚Рѕ РјРµРЅСЋ РІ РїР°РЅРµР»Рё NH Plugin", default=False, update=_on_ui_panel_layout_setting_changed)
    show_cache_manager: BoolProperty(name="Show", description="РџРѕРєР°Р·С‹РІР°С‚СЊ РёР»Рё СЃРєСЂС‹РІР°С‚СЊ СЌС‚Рѕ РјРµРЅСЋ РІ РїР°РЅРµР»Рё NH Plugin", default=True, update=_on_ui_panel_layout_setting_changed)
    show_custom_keybinds: BoolProperty(name="Custom Keybinds", description="РџРѕРєР°Р·С‹РІР°С‚СЊ СЃРїРёСЃРѕРє РєР°СЃС‚РѕРјРЅС‹С… С…РѕС‚РєРµРµРІ Р°РґРґРѕРЅР°", default=False, update=_on_ui_panel_layout_setting_changed)


class CRAY_OT_MoveUIPanelLayoutItem(Operator):
    bl_idname = "cray.move_ui_panel_layout_item"
    bl_label = "Move Panel"
    bl_options = {"INTERNAL"}

    panel_key: StringProperty(default="")
    direction: IntProperty(default=0)

    def execute(self, context):
        from .nh_base import (_save_current_persisted_ui_state)
        settings = _ui_panel_settings_from_context(context)
        if settings is None:
            return {"CANCELLED"}

        ordered_keys = [key for key, _label, _class_name in _sorted_ui_panel_layout_definitions(settings)]
        if self.panel_key not in ordered_keys:
            return {"CANCELLED"}

        index = ordered_keys.index(self.panel_key)
        target = max(0, min(len(ordered_keys) - 1, index + int(self.direction)))
        if target == index:
            return {"CANCELLED"}

        ordered_keys.insert(target, ordered_keys.pop(index))
        _normalize_ui_panel_order(settings, ordered_keys)
        _apply_ui_panel_class_order(settings)
        _tag_ui_redraw(context)
        _save_current_persisted_ui_state(getattr(context, "scene", None))
        return {"FINISHED"}


class CRAY_OT_ResetUIPanelLayoutOrder(Operator):
    bl_idname = "cray.reset_ui_panel_layout_order"
    bl_label = "Reset Panel Order"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        from .nh_base import (_UI_PANEL_LAYOUT_DEFINITIONS, _save_current_persisted_ui_state)
        settings = _ui_panel_settings_from_context(context)
        if settings is None:
            return {"CANCELLED"}

        for key, _label, _class_name in _UI_PANEL_LAYOUT_DEFINITIONS:
            prop_name = _ui_panel_order_prop_name(key)
            if not hasattr(settings, prop_name):
                continue
            try:
                setattr(settings, prop_name, _ui_panel_default_order(key))
            except Exception:
                pass
        _apply_ui_panel_class_order(settings)
        _tag_ui_redraw(context)
        _save_current_persisted_ui_state(getattr(context, "scene", None))
        return {"FINISHED"}


# ------------------------------------------------------------------------
#  Operators (scatter)
# ------------------------------------------------------------------------

class CRAY_OT_LoadConfig(Operator):
    bl_idname = "cray.load_config"
    bl_label = "Load .cpp & Parse"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (CONFIG_CLUTTER, CONFIG_SURFACES, _fmt_exc)
        from .utilities.dayz_config import (parse_dayz_config)
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
        from .nh_base import (_collect_selected_face_triangles_world, _fmt_exc, _resolve_scatter_edit_mesh_object, _sample_point_on_triangle, _scatter_slope_density_factor)
        from .utilities.dayz_config import (build_clutter_distribution, parse_dayz_config, pick_weighted_random)
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
