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

# nh_model_split.py
# auto-split slice; cross-module refs resolved with in-function imports

def _model_split_grid_config_values(settings):
    count_x = max(1, int(getattr(settings, "grid_count_x", 1) or 1))
    count_y = max(1, int(getattr(settings, "grid_count_y", 1) or 1))
    return count_x, count_y


def _model_split_grid_expanded_bounds(bounds, min_span: float = 0.01):
    if not bounds:
        return None
    min_v = bounds[0].copy()
    max_v = bounds[1].copy()
    for axis in range(3):
        span = float(max_v[axis] - min_v[axis])
        if abs(span) >= min_span:
            continue
        center = (float(min_v[axis]) + float(max_v[axis])) * 0.5
        half = min_span * 0.5
        min_v[axis] = center - half
        max_v[axis] = center + half
    return min_v, max_v


def _model_split_grid_auto_cut_positions(min_value: float, max_value: float, count: int):
    count = max(1, int(count or 1))
    if count <= 1:
        return []
    span = float(max_value) - float(min_value)
    if abs(span) <= 1.0e-9:
        return []
    step = span / float(count)
    return [float(min_value) + step * i for i in range(1, count)]


def _model_split_grid_unique_cut_positions(values, min_value: float, max_value: float):
    span = abs(float(max_value) - float(min_value))
    epsilon = max(span * 1.0e-5, 1.0e-5)
    result = []
    for value in sorted(float(v) for v in values):
        if value <= min_value + epsilon or value >= max_value - epsilon:
            continue
        if result and abs(value - result[-1]) <= epsilon:
            continue
        result.append(value)
    return result


def _model_split_grid_collect_guides(context, settings):
    from .nh_planner import (_model_split_grid_cutter_collection, _model_split_grid_is_enabled_cutter, _model_split_grid_is_guide, _model_split_grid_object_visible)
    from .nh_textures import (_collect_collection_objects_recursive)
    collection = _model_split_grid_cutter_collection(context, settings, create=False)
    if collection is None:
        return []

    guides = []
    for obj in _collect_collection_objects_recursive(collection):
        if not _model_split_grid_is_guide(obj):
            continue
        if not _model_split_grid_is_enabled_cutter(obj):
            continue
        if bool(getattr(settings, "grid_use_visible_cutters_only", True)) and not _model_split_grid_object_visible(context, obj):
            continue
        guides.append(obj)
    guides.sort(key=lambda obj: (str(obj.get("nh_grid_axis", "") or ""), getattr(obj, "name", "")))
    return guides


def _model_split_grid_guide_cut_positions(context, settings, bounds):
    positions = {"X": [], "Y": []}
    for guide in _model_split_grid_collect_guides(context, settings):
        try:
            axis = str(guide.get("nh_grid_axis", "") or "").strip().upper()
        except Exception:
            axis = ""
        if axis not in positions:
            continue
        try:
            loc = guide.matrix_world.translation
        except Exception:
            continue
        positions[axis].append(float(loc.x if axis == "X" else loc.y))

    min_v, max_v = bounds
    positions["X"] = _model_split_grid_unique_cut_positions(positions["X"], min_v.x, max_v.x)
    positions["Y"] = _model_split_grid_unique_cut_positions(positions["Y"], min_v.y, max_v.y)
    return positions


def _model_split_grid_cut_edges(context, settings, source_objects):
    from .nh_planner import (_model_split_grid_world_bounds_for_objects)
    bounds = _model_split_grid_expanded_bounds(_model_split_grid_world_bounds_for_objects(source_objects))
    if bounds is None:
        raise RuntimeError("Could not calculate source bounds for line grid split")
    min_v, max_v = bounds
    count_x = max(1, int(getattr(settings, "grid_count_x", 1) or 1))
    count_y = max(1, int(getattr(settings, "grid_count_y", 1) or 1))

    guide_positions = _model_split_grid_guide_cut_positions(context, settings, bounds)
    x_cuts = guide_positions["X"] or _model_split_grid_auto_cut_positions(min_v.x, max_v.x, count_x)
    y_cuts = guide_positions["Y"] or _model_split_grid_auto_cut_positions(min_v.y, max_v.y, count_y)
    x_cuts = _model_split_grid_unique_cut_positions(x_cuts, min_v.x, max_v.x)
    y_cuts = _model_split_grid_unique_cut_positions(y_cuts, min_v.y, max_v.y)

    x_edges = [float(min_v.x), *x_cuts, float(max_v.x)]
    y_edges = [float(min_v.y), *y_cuts, float(max_v.y)]
    used_guides = bool(guide_positions["X"] or guide_positions["Y"])
    return bounds, x_edges, y_edges, used_guides


def _model_split_grid_iter_cells(x_edges, y_edges):
    part_number = 1
    # Number top-to-bottom in top view, then left-to-right in each row.
    for iy in range(len(y_edges) - 2, -1, -1):
        for ix in range(len(x_edges) - 1):
            yield (
                part_number,
                ix,
                iy,
                float(x_edges[ix]),
                float(x_edges[ix + 1]),
                float(y_edges[iy]),
                float(y_edges[iy + 1]),
            )
            part_number += 1


def _model_split_grid_create_temp_collection(context, name: str):
    collection = bpy.data.collections.new(name)
    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    if scene_root is not None:
        scene_root.children.link(collection)
    return collection


def _model_split_grid_remove_temp_collection(collection):
    from .nh_textures import (_collect_collection_objects_recursive)
    if collection is None:
        return
    for obj in list(_collect_collection_objects_recursive(collection)):
        _model_split_grid_remove_object(obj)
    _model_split_grid_remove_collection_tree(collection)


def _model_split_grid_create_cell_cutter(context, temp_collection, name: str, bounds, x0: float, x1: float, y0: float, y1: float):
    from .nh_planner import (_model_split_grid_create_cube_mesh)
    from .nh_textures import (_ensure_collection_visible_in_view_layer, _link_object_to_collection)
    min_v, max_v = bounds
    span_z = max(float(max_v.z - min_v.z), 0.01)
    pad_z = max(span_z * 0.05, 0.01)
    size_x = max(float(x1 - x0), 0.001)
    size_y = max(float(y1 - y0), 0.001)
    size_z = span_z + pad_z * 2.0

    mesh = _model_split_grid_create_cube_mesh(name)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = (
        (float(x0) + float(x1)) * 0.5,
        (float(y0) + float(y1)) * 0.5,
        (float(min_v.z) + float(max_v.z)) * 0.5,
    )
    obj.scale = (size_x, size_y, size_z)
    obj["nh_grid_temp_cell_cutter"] = True
    try:
        obj.display_type = "WIRE"
    except Exception:
        pass
    _link_object_to_collection(obj, temp_collection)
    _ensure_collection_visible_in_view_layer(context, temp_collection)
    try:
        context.view_layer.update()
    except Exception:
        pass
    return obj


def _model_split_grid_create_guide_object(context, collection, axis: str, index: int, position: float, bounds):
    from .nh_planner import (_model_split_grid_create_cube_mesh)
    from .nh_textures import (_ensure_collection_visible_in_view_layer, _link_object_to_collection)
    axis = (axis or "X").upper()
    min_v, max_v = bounds
    span_x = max(float(max_v.x - min_v.x), 0.01)
    span_y = max(float(max_v.y - min_v.y), 0.01)
    span_z = max(float(max_v.z - min_v.z), 0.01)
    thickness = max(min(span_x, span_y) * 0.006, 0.02)
    pad_xy = max(max(span_x, span_y) * 0.02, 0.05)
    pad_z = max(span_z * 0.05, 0.05)

    name = f"CUT_{axis}{index:02d}"
    mesh = _model_split_grid_create_cube_mesh(name)
    obj = bpy.data.objects.new(name, mesh)
    if axis == "X":
        obj.location = (float(position), (min_v.y + max_v.y) * 0.5, (min_v.z + max_v.z) * 0.5)
        obj.scale = (thickness, span_y + pad_xy * 2.0, span_z + pad_z * 2.0)
    else:
        obj.location = ((min_v.x + max_v.x) * 0.5, float(position), (min_v.z + max_v.z) * 0.5)
        obj.scale = (span_x + pad_xy * 2.0, thickness, span_z + pad_z * 2.0)

    obj["nh_grid_guide"] = True
    obj["nh_grid_axis"] = axis
    obj["nh_grid_index"] = int(index)
    obj["nh_grid_enabled"] = True
    try:
        obj.display_type = "WIRE"
        obj.show_name = True
        obj.color = (0.0, 0.85, 1.0, 0.45) if axis == "X" else (1.0, 0.65, 0.0, 0.45)
    except Exception:
        pass
    _link_object_to_collection(obj, collection)
    _ensure_collection_visible_in_view_layer(context, collection)
    return obj


def _model_split_grid_category_for_object(obj) -> str:
    from .nh_snap import (_logical_collection_name)
    from .nh_textures import (_model_split_category_for_object)
    category = _model_split_category_for_object(obj)
    if obj is not None and hasattr(obj, "a3ob_properties_object"):
        try:
            if bool(getattr(obj.a3ob_properties_object, "is_a3_lod", False)):
                return category
        except Exception:
            pass

    text_parts = [getattr(obj, "name", "") or ""]
    for col in getattr(obj, "users_collection", []) or []:
        text_parts.append(getattr(col, "name", "") or "")
    text = " ".join(_logical_collection_name(part) for part in text_parts)
    if "roadway" in text:
        return "ROADWAY"
    if "memory" in text or "point cloud" in text or "pointcloud" in text:
        return "POINT_CLOUDS"
    if (
        "geometry" in text or
        "shadow" in text or
        "physx" in text or
        "view geometry" in text or
        "fire geometry" in text
    ):
        return "GEOMETRIES"
    if "resolution" in text or "visual" in text:
        return "RESOLUTION"
    return category


def _model_split_grid_copy_p3d_object_props(src_obj, dst_obj):
    if src_obj is None or dst_obj is None:
        return False
    if not hasattr(src_obj, "a3ob_properties_object") or not hasattr(dst_obj, "a3ob_properties_object"):
        return False
    try:
        src_props = src_obj.a3ob_properties_object
        dst_props = dst_obj.a3ob_properties_object
        for prop in getattr(src_props, "bl_rna", ()).properties:
            identifier = getattr(prop, "identifier", "")
            if not identifier or identifier == "rna_type" or getattr(prop, "is_readonly", False):
                continue
            try:
                setattr(dst_props, identifier, getattr(src_props, identifier))
            except Exception:
                pass
        return True
    except Exception:
        return False


def _model_split_grid_set_piece_names(obj, piece_name: str):
    if obj is None:
        return
    try:
        obj.name = piece_name
    except Exception:
        pass
    try:
        if obj.data is not None:
            obj.data.name = piece_name
    except Exception:
        pass


def _model_split_grid_prepare_piece_props(src_obj, dst_obj, category_token: str, piece_name: str):
    from .nh_textures import (_set_model_split_target_lod_p3d_props)
    copied = _model_split_grid_copy_p3d_object_props(src_obj, dst_obj)
    if not copied:
        _set_model_split_target_lod_p3d_props(dst_obj, category_token)
    _model_split_grid_set_piece_names(dst_obj, piece_name)


def _model_split_grid_has_faces(obj) -> bool:
    data = getattr(obj, "data", None)
    if data is None:
        return False
    try:
        return len(data.polygons) > 0
    except Exception:
        return False


def _model_split_grid_should_clip_as_points(obj, category_token: str) -> bool:
    return category_token == "POINT_CLOUDS" or not _model_split_grid_has_faces(obj)


def _model_split_grid_is_proxy_object(obj) -> bool:
    from .nh_assets import (_is_p3d_proxy_object)
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    try:
        if _is_p3d_proxy_object(obj):
            return True
    except Exception:
        pass
    if hasattr(obj, "a3ob_properties_object_proxy"):
        try:
            props = obj.a3ob_properties_object_proxy
            if bool(getattr(props, "is_a3_proxy", False)):
                return True
        except Exception:
            pass
    name = (getattr(obj, "name", "") or "").strip().lower()
    return name.startswith("proxy:")


def _model_split_grid_output_container(context, source_root, prefix: str):
    from .nh_planner import (_model_split_grid_collection_parent)
    parent = _model_split_grid_collection_parent(context, source_root)
    if parent is None:
        return None
    container_name = f"NH Grid Split - {prefix}"
    container = bpy.data.collections.new(container_name)
    parent.children.link(container)
    try:
        container.color_tag = "COLOR_04"
    except Exception:
        pass
    return container


def _model_split_grid_output_root_name(prefix: str, grid_id: str) -> str:
    from .nh_textures import (_INVALID_FILENAME_CHARS_RE)
    root_name = f"{prefix}_{grid_id}.p3d"
    root_name = _INVALID_FILENAME_CHARS_RE.sub("_", root_name)
    return root_name


def _model_split_grid_create_output_root(context, container, source_root, root_name: str):
    from .nh_textures import (_derive_split_export_source_path, _set_ie_source_path_tag)
    if container is None:
        return None
    target_root = bpy.data.collections.new(root_name)
    container.children.link(target_root)
    try:
        if source_root is not None and getattr(source_root, "color_tag", None):
            target_root.color_tag = source_root.color_tag
    except Exception:
        pass
    if source_root is not None:
        _set_ie_source_path_tag(target_root, _derive_split_export_source_path(source_root, root_name))
    return target_root


def _model_split_grid_remove_collection_tree(collection):
    if collection is None:
        return
    for child in list(getattr(collection, "children", []) or []):
        _model_split_grid_remove_collection_tree(child)
    for obj in list(getattr(collection, "objects", []) or []):
        try:
            collection.objects.unlink(obj)
        except Exception:
            pass
    try:
        bpy.data.collections.remove(collection)
    except Exception:
        pass


def _model_split_grid_point_inside_cutter(cutter, world_point, epsilon: float = 1.0e-6) -> bool:
    if cutter is None:
        return False
    try:
        local = cutter.matrix_world.inverted_safe() @ world_point
    except Exception:
        return False

    bound_box = list(getattr(cutter, "bound_box", []) or [])
    if not bound_box and getattr(cutter, "data", None) is not None:
        try:
            bound_box = [vertex.co[:] for vertex in cutter.data.vertices]
        except Exception:
            bound_box = []
    if not bound_box:
        bound_box = [(-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)]

    min_v = Vector((
        min(Vector(corner).x for corner in bound_box),
        min(Vector(corner).y for corner in bound_box),
        min(Vector(corner).z for corner in bound_box),
    ))
    max_v = Vector((
        max(Vector(corner).x for corner in bound_box),
        max(Vector(corner).y for corner in bound_box),
        max(Vector(corner).z for corner in bound_box),
    ))
    return (
        min_v.x - epsilon <= local.x <= max_v.x + epsilon and
        min_v.y - epsilon <= local.y <= max_v.y + epsilon and
        min_v.z - epsilon <= local.z <= max_v.z + epsilon
    )


def _model_split_grid_apply_boolean_intersect(context, obj, cutter):
    if obj is None or cutter is None:
        raise RuntimeError("Missing object or cutter for boolean split")
    modifier = None
    old_mesh = getattr(obj, "data", None)
    try:
        modifier = obj.modifiers.new("NH Grid Cutter Intersect", "BOOLEAN")
        modifier.operation = "INTERSECT"
        modifier.object = cutter
        if hasattr(modifier, "solver"):
            modifier.solver = "EXACT"
        try:
            context.view_layer.update()
        except Exception:
            pass
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        try:
            new_mesh = bpy.data.meshes.new_from_object(
                evaluated,
                depsgraph=depsgraph,
                preserve_all_data_layers=True,
            )
        except TypeError:
            new_mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
        if new_mesh is None:
            raise RuntimeError("Boolean modifier produced no mesh")
        obj.modifiers.remove(modifier)
        modifier = None
        obj.data = new_mesh
        if old_mesh is not None and getattr(old_mesh, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(old_mesh)
            except Exception:
                pass
        return obj
    except Exception:
        if modifier is not None:
            try:
                obj.modifiers.remove(modifier)
            except Exception:
                pass
        raise


def _model_split_grid_cleanup_mesh(obj, *, recalc_normals: bool = False, remove_loose: bool = False):
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        if remove_loose:
            loose_verts = [vert for vert in bm.verts if not vert.link_edges and not vert.link_faces]
            if loose_verts:
                bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")
        if recalc_normals and bm.faces:
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()


def _model_split_grid_piece_is_empty(obj, settings, *, point_piece: bool = False) -> bool:
    data = getattr(obj, "data", None)
    if data is None:
        return True
    try:
        vertex_count = len(data.vertices)
    except Exception:
        vertex_count = 0
    try:
        face_count = len(data.polygons)
    except Exception:
        face_count = 0
    if vertex_count <= 0:
        return True
    min_vertices = max(0, int(getattr(settings, "grid_min_vertices", 0) or 0))
    min_faces = max(0, int(getattr(settings, "grid_min_faces", 0) or 0))
    if vertex_count < min_vertices:
        return True
    if not point_piece and bool(getattr(settings, "grid_skip_empty_pieces", True)) and face_count < min_faces:
        return True
    return False


def _model_split_grid_remove_object(obj):
    if obj is None:
        return
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:
        pass
    if data is not None:
        try:
            if getattr(data, "users", 0) == 0:
                bpy.data.meshes.remove(data)
        except Exception:
            pass


def _model_split_grid_make_face_piece(context, src_obj, cutter, dest_collection, settings, piece_name, category_token):
    from .nh_textures import (_duplicate_object_for_split, _ensure_collection_visible_in_view_layer, _link_object_to_collection)
    dup_obj = _duplicate_object_for_split(src_obj)
    if dup_obj is None:
        raise RuntimeError("Failed to duplicate source object")
    _model_split_grid_prepare_piece_props(src_obj, dup_obj, category_token, piece_name)
    _link_object_to_collection(dup_obj, dest_collection)
    _ensure_collection_visible_in_view_layer(context, dest_collection)
    try:
        _model_split_grid_apply_boolean_intersect(context, dup_obj, cutter)
        if category_token == "ROADWAY":
            _model_split_grid_cleanup_mesh(dup_obj, recalc_normals=True, remove_loose=True)
        if _model_split_grid_piece_is_empty(dup_obj, settings, point_piece=False):
            _model_split_grid_remove_object(dup_obj)
            return None
        return dup_obj
    except Exception:
        _model_split_grid_remove_object(dup_obj)
        raise


def _model_split_grid_copy_vertex_groups(src_obj, dst_obj, index_map):
    if src_obj is None or dst_obj is None or not index_map:
        return
    try:
        while dst_obj.vertex_groups:
            dst_obj.vertex_groups.remove(dst_obj.vertex_groups[0])
    except Exception:
        pass
    for src_group in getattr(src_obj, "vertex_groups", []) or []:
        try:
            dst_group = dst_obj.vertex_groups.new(name=src_group.name)
        except Exception:
            continue
        for old_index, new_index in index_map.items():
            try:
                weight = src_group.weight(old_index)
            except Exception:
                continue
            try:
                dst_group.add([new_index], weight, "ADD")
            except Exception:
                pass


def _model_split_grid_make_point_piece(src_obj, cutter, dest_collection, settings, piece_name, category_token):
    from .nh_textures import (_clear_ie_source_path_tag, _link_object_to_collection)
    source_mesh = getattr(src_obj, "data", None)
    if source_mesh is None:
        return None

    verts = []
    index_map = {}
    source_matrix = src_obj.matrix_world.copy()
    for vertex in source_mesh.vertices:
        try:
            world_point = source_matrix @ vertex.co
        except Exception:
            continue
        if not _model_split_grid_point_inside_cutter(cutter, world_point):
            continue
        index_map[int(vertex.index)] = len(verts)
        verts.append(tuple(vertex.co))

    if not verts:
        return None

    edges = []
    for edge in getattr(source_mesh, "edges", []) or []:
        try:
            v0, v1 = int(edge.vertices[0]), int(edge.vertices[1])
        except Exception:
            continue
        if v0 in index_map and v1 in index_map:
            edges.append((index_map[v0], index_map[v1]))

    mesh = bpy.data.meshes.new(piece_name)
    mesh.from_pydata(verts, edges, ())
    try:
        for material in getattr(source_mesh, "materials", []) or []:
            mesh.materials.append(material)
    except Exception:
        pass
    mesh.update()

    dup_obj = src_obj.copy()
    dup_obj.data = mesh
    try:
        dup_obj.parent = None
    except Exception:
        pass
    try:
        dup_obj.matrix_world = src_obj.matrix_world.copy()
    except Exception:
        pass
    try:
        for modifier in list(getattr(dup_obj, "modifiers", []) or []):
            dup_obj.modifiers.remove(modifier)
    except Exception:
        pass
    _clear_ie_source_path_tag(dup_obj)
    _model_split_grid_prepare_piece_props(src_obj, dup_obj, category_token, piece_name)
    _model_split_grid_copy_vertex_groups(src_obj, dup_obj, index_map)
    _link_object_to_collection(dup_obj, dest_collection)

    if _model_split_grid_piece_is_empty(dup_obj, settings, point_piece=True):
        _model_split_grid_remove_object(dup_obj)
        return None
    return dup_obj


def _model_split_grid_make_proxy_piece(src_obj, cutter, dest_collection, settings, piece_name, category_token):
    from .nh_textures import (_duplicate_object_for_split, _link_object_to_collection)
    try:
        origin = src_obj.matrix_world.translation.copy()
    except Exception:
        return None

    if not _model_split_grid_point_inside_cutter(cutter, origin):
        return None

    dup_obj = _duplicate_object_for_split(src_obj)
    if dup_obj is None:
        raise RuntimeError("Failed to duplicate proxy object")

    try:
        for modifier in list(getattr(dup_obj, "modifiers", []) or []):
            dup_obj.modifiers.remove(modifier)
    except Exception:
        pass

    _model_split_grid_prepare_piece_props(src_obj, dup_obj, category_token, piece_name)
    _link_object_to_collection(dup_obj, dest_collection)
    return dup_obj


def _model_split_grid_resolve_source(context, settings):
    from .nh_planner import (_model_split_grid_active_mesh_object, _model_split_grid_is_split_helper)
    from .nh_textures import (_collect_collection_objects_recursive, _find_p3d_root_collection_for_collection, _find_p3d_root_collection_for_object, _model_split_selected_p3d_root_collections)
    source_obj = getattr(settings, "grid_source_object", None)
    source_root = getattr(settings, "grid_source_root_collection", None)
    if source_obj is not None:
        if getattr(source_obj, "type", None) != "MESH":
            raise RuntimeError("Source Object must be a mesh")
        resolved_root = _find_p3d_root_collection_for_collection(context, source_root, require_p3d=False) if source_root is not None else None
        if resolved_root is None:
            resolved_root = _find_p3d_root_collection_for_object(context, source_obj)
        return resolved_root, [source_obj]

    if source_root is not None:
        resolved_root = _find_p3d_root_collection_for_collection(context, source_root, require_p3d=False) or source_root
        source_objects = [
            obj for obj in _collect_collection_objects_recursive(resolved_root)
            if getattr(obj, "type", None) == "MESH" and not _model_split_grid_is_split_helper(obj)
        ]
        if not source_objects:
            raise RuntimeError(f"Source Root Collection '{resolved_root.name}' contains no mesh objects")
        return resolved_root, source_objects

    selected_roots = _model_split_selected_p3d_root_collections(context)
    if len(selected_roots) == 1:
        root = selected_roots[0]
        source_objects = [
            obj for obj in _collect_collection_objects_recursive(root)
            if getattr(obj, "type", None) == "MESH" and not _model_split_grid_is_split_helper(obj)
        ]
        if source_objects:
            return root, source_objects

    active_obj = _model_split_grid_active_mesh_object(context)
    if active_obj is not None and not _model_split_grid_is_split_helper(active_obj):
        return _find_p3d_root_collection_for_object(context, active_obj), [active_obj]

    selected_meshes = [
        obj for obj in getattr(context, "selected_objects", []) or []
        if getattr(obj, "type", None) == "MESH" and not _model_split_grid_is_split_helper(obj)
    ]
    if selected_meshes:
        return _find_p3d_root_collection_for_object(context, selected_meshes[0]), selected_meshes

    raise RuntimeError("Choose Source Object, Source Root Collection, an active mesh, or one selected .p3d root collection")


def _model_split_grid_hide_sources(source_objects):
    for obj in source_objects or ():
        try:
            obj.hide_set(True)
        except Exception:
            pass
        try:
            obj.hide_viewport = True
        except Exception:
            pass
        try:
            obj.hide_render = True
        except Exception:
            pass


def _model_split_grid_hide_cutters(cutter_collection, cutters):
    if cutter_collection is not None:
        try:
            cutter_collection.hide_viewport = True
        except Exception:
            pass
    for cutter in cutters or ():
        try:
            cutter.hide_set(True)
        except Exception:
            pass
        try:
            cutter.hide_viewport = True
        except Exception:
            pass


class CRAY_OT_ModelSplitGridCreateCutters(Operator):
    bl_idname = "cray.model_split_grid_create_cutters"
    bl_label = "Create/Edit Cut Lines"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_planner import (_model_split_grid_cutter_collection, _model_split_grid_delete_tagged_cutters, _model_split_grid_source_display_name, _model_split_grid_world_bounds_for_objects)
        from .nh_snap import (_deselect_all_in_view_layer)
        from .nh_textures import (_ensure_collection_visible_in_view_layer)
        settings = context.scene.cray_model_split_settings
        try:
            source_root, source_objects = _model_split_grid_resolve_source(context, settings)
            bounds = _model_split_grid_expanded_bounds(_model_split_grid_world_bounds_for_objects(source_objects))
            if bounds is None:
                raise RuntimeError("Could not calculate source bounds for cut lines")

            count_x, count_y = _model_split_grid_config_values(settings)
            guide_collection = _model_split_grid_cutter_collection(context, settings, create=True)
            if guide_collection is None:
                raise RuntimeError("Could not create cut lines collection")

            removed = _model_split_grid_delete_tagged_cutters(guide_collection)
            created = []
            min_v, max_v = bounds
            for idx, position in enumerate(_model_split_grid_auto_cut_positions(min_v.x, max_v.x, count_x), start=1):
                created.append(_model_split_grid_create_guide_object(context, guide_collection, "X", idx, position, bounds))
            for idx, position in enumerate(_model_split_grid_auto_cut_positions(min_v.y, max_v.y, count_y), start=1):
                created.append(_model_split_grid_create_guide_object(context, guide_collection, "Y", idx, position, bounds))

            _ensure_collection_visible_in_view_layer(context, guide_collection)
            _deselect_all_in_view_layer(context)
            for obj in created:
                try:
                    obj.select_set(True)
                except Exception:
                    pass
            if created:
                try:
                    context.view_layer.objects.active = created[0]
                except Exception:
                    pass

            root_name = getattr(source_root, "name", "") if source_root is not None else _model_split_grid_source_display_name(context, settings)
            self.report({"INFO"}, f"Created {len(created)} cut line(s) for {count_x}x{count_y} split on {root_name}, removed {removed} old helper(s)")
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}


class CRAY_OT_ModelSplitGridSelectCutters(Operator):
    bl_idname = "cray.model_split_grid_select_cutters"
    bl_label = "Select Cut Lines"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_planner import (_model_split_grid_cutter_collection, _model_split_grid_is_guide)
        from .nh_snap import (_deselect_all_in_view_layer)
        from .nh_textures import (_collect_collection_objects_recursive, _ensure_collection_visible_in_view_layer)
        settings = context.scene.cray_model_split_settings
        guide_collection = _model_split_grid_cutter_collection(context, settings, create=False)
        if guide_collection is None:
            self.report({"ERROR"}, "Create cut lines first")
            return {"CANCELLED"}

        guides = [
            obj for obj in _collect_collection_objects_recursive(guide_collection)
            if getattr(obj, "type", None) == "MESH" and _model_split_grid_is_guide(obj)
        ]
        _ensure_collection_visible_in_view_layer(context, guide_collection)
        _deselect_all_in_view_layer(context)
        for obj in guides:
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
        if guides:
            try:
                context.view_layer.objects.active = guides[0]
            except Exception:
                pass
        self.report({"INFO"}, f"Selected {len(guides)} cut line(s)")
        return {"FINISHED"}


class CRAY_OT_ModelSplitGridClearCutters(Operator):
    bl_idname = "cray.model_split_grid_clear_cutters"
    bl_label = "Clear Cut Lines"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_planner import (_model_split_grid_cutter_collection, _model_split_grid_delete_tagged_cutters)
        settings = context.scene.cray_model_split_settings
        guide_collection = _model_split_grid_cutter_collection(context, settings, create=False)
        if guide_collection is None:
            self.report({"ERROR"}, "Create cut lines first")
            return {"CANCELLED"}
        removed = _model_split_grid_delete_tagged_cutters(guide_collection)
        self.report({"INFO"}, f"Removed {removed} cut line helper(s)")
        return {"FINISHED"}


class CRAY_OT_ModelSplitGridSplitSource(Operator):
    bl_idname = "cray.model_split_grid_split_source"
    bl_label = "Split Source To _p Parts"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_planner import (_model_split_grid_cutter_collection, _model_split_grid_is_split_helper, _model_split_grid_output_prefix, _model_split_grid_source_display_name)
        from .nh_textures import (_add_model_split_part_to_planner, _ensure_collection_visible_in_view_layer, _ensure_model_split_target_category_collection, _focus_created_split_objects)
        settings = context.scene.cray_model_split_settings
        created_roots = []
        created_objects = []
        failures = []
        skipped_empty = 0
        error_count = 0

        try:
            source_root, source_objects = _model_split_grid_resolve_source(context, settings)
            bounds, x_edges, y_edges, used_guides = _model_split_grid_cut_edges(context, settings, source_objects)
            cell_count_x = max(1, len(x_edges) - 1)
            cell_count_y = max(1, len(y_edges) - 1)
            prefix = _model_split_grid_output_prefix(
                settings,
                _model_split_grid_source_display_name(context, settings),
            )
            output_container = _model_split_grid_output_container(context, source_root, prefix)
            if output_container is None:
                raise RuntimeError("Could not create output collection")
            temp_collection = _model_split_grid_create_temp_collection(context, f"NH Grid Split Cells - {prefix}")
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        try:
            cells = list(_model_split_grid_iter_cells(x_edges, y_edges))
            for part_number, _ix, _iy, x0, x1, y0, y1 in cells:
                grid_id = f"p{part_number:02d}"
                cell_cutter = None
                try:
                    cell_cutter = _model_split_grid_create_cell_cutter(
                        context,
                        temp_collection,
                        f"CELL_{grid_id}",
                        bounds,
                        x0,
                        x1,
                        y0,
                        y1,
                    )
                except Exception as e:
                    error_count += 1
                    failures.append(f"{grid_id} -> failed to create temporary split cell: {_fmt_exc(e)}")
                    continue

                root_name = _model_split_grid_output_root_name(prefix, grid_id)
                target_root = _model_split_grid_create_output_root(context, output_container, source_root, root_name)
                if target_root is None:
                    error_count += 1
                    failures.append(f"{grid_id} -> failed to create output root")
                    _model_split_grid_remove_object(cell_cutter)
                    continue

                cell_created = []
                for src_obj in source_objects:
                    if src_obj is None or getattr(src_obj, "type", None) != "MESH":
                        continue
                    if _model_split_grid_is_split_helper(src_obj):
                        continue

                    try:
                        if _model_split_grid_is_proxy_object(src_obj):
                            category_token = "RESOLUTION"
                            dest_leaf = _ensure_model_split_target_category_collection(target_root, category_token)
                            if dest_leaf is None:
                                raise RuntimeError("Could not create P3D category collection")
                            piece_name = f"{getattr(src_obj, 'name', 'Object')}__{grid_id}"
                            piece = _model_split_grid_make_proxy_piece(
                                src_obj,
                                cell_cutter,
                                dest_leaf,
                                settings,
                                piece_name,
                                category_token,
                            )
                            if piece is None:
                                skipped_empty += 1
                                continue
                            cell_created.append(piece)
                            created_objects.append(piece)
                            continue

                        category_token = _model_split_grid_category_for_object(src_obj)
                        if _model_split_grid_should_clip_as_points(src_obj, category_token):
                            category_token = "POINT_CLOUDS"
                        dest_leaf = _ensure_model_split_target_category_collection(target_root, category_token)
                        if dest_leaf is None:
                            raise RuntimeError("Could not create P3D category collection")
                        piece_name = f"{getattr(src_obj, 'name', 'Object')}__{grid_id}"
                        if _model_split_grid_should_clip_as_points(src_obj, category_token):
                            piece = _model_split_grid_make_point_piece(
                                src_obj,
                                cell_cutter,
                                dest_leaf,
                                settings,
                                piece_name,
                                category_token,
                            )
                        else:
                            piece = _model_split_grid_make_face_piece(
                                context,
                                src_obj,
                                cell_cutter,
                                dest_leaf,
                                settings,
                                piece_name,
                                category_token,
                            )

                        if piece is None:
                            skipped_empty += 1
                            continue
                        cell_created.append(piece)
                        created_objects.append(piece)
                    except Exception as e:
                        error_count += 1
                        failures.append(
                            f"{getattr(src_obj, 'name', '<source>')} / {grid_id} -> {_fmt_exc(e)}"
                        )

                _model_split_grid_remove_object(cell_cutter)

                if cell_created:
                    created_roots.append(target_root)
                else:
                    _model_split_grid_remove_collection_tree(target_root)
        finally:
            _model_split_grid_remove_temp_collection(temp_collection)

        if not created_roots:
            _model_split_grid_remove_collection_tree(output_container)
        else:
            _ensure_collection_visible_in_view_layer(context, output_container)
            _focus_created_split_objects(context, output_container, created_objects)

        if created_roots and not bool(getattr(settings, "grid_keep_original", True)):
            _model_split_grid_hide_sources(source_objects)

        guide_collection = _model_split_grid_cutter_collection(context, settings, create=False)
        guides = _model_split_grid_collect_guides(context, settings)
        if bool(getattr(settings, "grid_hide_cutters_after_split", False)):
            _model_split_grid_hide_cutters(guide_collection, guides)

        planner_added = 0
        if created_roots and bool(getattr(settings, "grid_add_result_to_export_planner", True)):
            for root in created_roots:
                try:
                    added, _planner_path = _add_model_split_part_to_planner(context, root)
                    if added:
                        planner_added += 1
                except Exception as e:
                    error_count += 1
                    failures.append(f"{getattr(root, 'name', '<root>')} -> planner add failed: {_fmt_exc(e)}")

        if failures:
            print("=== Model Split Line Grid Split: Failures ===")
            for failure in failures:
                print(failure)

        expected_parts = cell_count_x * cell_count_y
        guide_text = "manual guides" if used_guides else "equal grid"
        msg = (
            f"Line grid split: {cell_count_x}x{cell_count_y} cells ({expected_parts}) from {guide_text}, .p3d parts {len(created_roots)}, "
            f"mesh pieces {len(created_objects)}, skipped empty {skipped_empty}, errors {error_count}"
        )
        if planner_added:
            msg += f", planner added {planner_added}"
        if not created_objects:
            self.report({"ERROR"}, msg + " (see System Console)")
            return {"CANCELLED"}
        if error_count:
            self.report({"WARNING"}, msg + " (see System Console)")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class CRAY_UL_ModelSplitMergeSources(UIList):
    bl_idname = "CRAY_UL_model_split_merge_sources"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        collection = getattr(item, "collection", None)
        name = getattr(collection, "name", "") or getattr(item, "name", "") or "<missing collection>"
        layout.label(text=name, icon="OUTLINER_COLLECTION")


class CRAY_OT_ModelSplitMergeAddSource(Operator):
    bl_idname = "cray.model_split_merge_add_source"
    bl_label = "Add Merge Source"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_textures import (_find_p3d_root_collection_for_collection, _model_split_add_unique_collection, _model_split_id_key, _model_split_merge_source_collection_from_settings, _model_split_selected_p3d_root_collections, _same_id_data, _sort_model_split_merge_sources)
        st = context.scene.cray_model_split_settings

        candidates = []
        seen = set()
        picked = _model_split_merge_source_collection_from_settings(context, st)
        if picked is not None:
            root = _find_p3d_root_collection_for_collection(context, picked, require_p3d=True)
            if root is None:
                self.report({"ERROR"}, "Merge Source must be a .p3d root collection or one of its child collections")
                return {"CANCELLED"}
            _model_split_add_unique_collection(candidates, seen, root)
        else:
            for root in _model_split_selected_p3d_root_collections(context):
                _model_split_add_unique_collection(candidates, seen, root)

        if not candidates:
            self.report({"ERROR"}, "Pick a Merge Source or select .p3d collection/object(s)")
            return {"CANCELLED"}

        target_root = None
        picked_target = getattr(st, "named_target_collection", None)
        if picked_target is not None:
            target_root = _find_p3d_root_collection_for_collection(context, picked_target, require_p3d=True)

        existing = {
            _model_split_id_key(getattr(item, "collection", None))
            for item in getattr(st, "merge_sources", []) or []
        }
        added = 0
        for root in candidates:
            if _same_id_data(root, target_root):
                continue
            key = _model_split_id_key(root)
            if key in existing:
                continue
            item = st.merge_sources.add()
            item.collection = root
            item.name = getattr(root, "name", "") or ""
            st.merge_sources_index = len(st.merge_sources) - 1
            existing.add(key)
            added += 1

        _sort_model_split_merge_sources(st)
        if added:
            self.report({"INFO"}, f"Added {added} merge source collection(s)")
        else:
            self.report({"INFO"}, "No new source collection added")
        return {"FINISHED"}


class CRAY_OT_ModelSplitMergeRemoveSource(Operator):
    bl_idname = "cray.model_split_merge_remove_source"
    bl_label = "Remove Merge Source"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = context.scene.cray_model_split_settings
        if len(st.merge_sources) == 0:
            self.report({"INFO"}, "Merge source list is empty")
            return {"FINISHED"}
        index = max(0, min(int(getattr(st, "merge_sources_index", 0) or 0), len(st.merge_sources) - 1))
        st.merge_sources.remove(index)
        st.merge_sources_index = max(0, min(index, len(st.merge_sources) - 1))
        self.report({"INFO"}, "Removed merge source")
        return {"FINISHED"}


class CRAY_OT_ModelSplitMergeClearSources(Operator):
    bl_idname = "cray.model_split_merge_clear_sources"
    bl_label = "Clear Merge Sources"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.cray_model_split_settings.merge_sources.clear()
        self.report({"INFO"}, "Cleared merge source list")
        return {"FINISHED"}


class CRAY_OT_ModelSplitMergeSelectedCollections(Operator):
    bl_idname = "cray.model_split_merge_selected_collections"
    bl_label = "Merge Collections"
    bl_description = "Merge selected/listed .p3d collections into Target Model and join duplicate P3D LOD roots"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_textures import (_add_model_split_part_to_planner, _collect_collection_objects_recursive, _ensure_collection_visible_in_view_layer, _focus_created_split_objects, _model_split_merge_duplicate_lods, _model_split_move_root_contents_to_merge_target, _model_split_object_ptr, _remove_empty_root_collection, _remove_empty_subcollections, _resolve_model_split_merge_roots)
        st = context.scene.cray_model_split_settings

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as e:
                self.report({"ERROR"}, f"Failed to switch to Object Mode: {_fmt_exc(e)}")
                return {"CANCELLED"}

        try:
            target_root, source_roots = _resolve_model_split_merge_roots(context, st)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        try:
            _ensure_collection_visible_in_view_layer(context, target_root)
        except Exception:
            pass

        moved_objects = 0
        removed_sources = 0
        failures = []
        source_name_list = []
        for source_root in source_roots:
            try:
                source_name_list.append(getattr(source_root, "name", "") or "<collection>")
            except ReferenceError:
                source_name_list.append("<collection>")
        source_names = ", ".join(source_name_list)
        source_count = len(source_roots)

        preferred_target_object_ptrs = {
            _model_split_object_ptr(obj)
            for obj in _collect_collection_objects_recursive(target_root)
        }
        preferred_target_object_ptrs.discard(None)

        all_roots = [target_root] + list(source_roots)
        for root in all_roots:
            try:
                moved_objects += _model_split_move_root_contents_to_merge_target(target_root, root)
            except Exception as e:
                failures.append(f"{getattr(root, 'name', '<collection>')} -> {_fmt_exc(e)}")

        try:
            st.merge_sources.clear()
        except Exception:
            pass

        for source_root, source_name in zip(source_roots, source_name_list):
            try:
                removed_sources += _remove_empty_root_collection(context, source_root)
            except Exception as e:
                failures.append(f"{source_name} cleanup -> {_fmt_exc(e)}")

        try:
            merge_stats = _model_split_merge_duplicate_lods(
                context,
                target_root,
                preferred_object_ptrs=preferred_target_object_ptrs,
            )
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        removed_empty = _remove_empty_subcollections(target_root)
        planner_added, planner_path = _add_model_split_part_to_planner(context, target_root)

        merged_objects = merge_stats.get("merged_objects", []) if merge_stats else []
        if merged_objects:
            _focus_created_split_objects(context, target_root, merged_objects)
        else:
            _ensure_collection_visible_in_view_layer(context, target_root)

        if failures:
            print("=== Model Split Merge: Failures ===")
            for item in failures:
                print(item)

        print("=== Model Split Merge ===")
        print(f"Target: {target_root.name}")
        print(f"Sources: {source_names}")
        print(f"Moved/canonicalized objects: {moved_objects}")
        print(f"Merged duplicate LOD groups: {merge_stats.get('merged_lod_groups', 0)}")
        print(f"Joined/removed duplicate LOD roots: {merge_stats.get('joined_roots', 0)}")
        print(f"Reparented children/proxies: {merge_stats.get('reparented', 0)}")
        print(f"Rewired refs: {merge_stats.get('rewired', 0)}")
        print(f"Removed empty collections: {removed_empty + removed_sources}")

        msg = (
            f"Merged {source_count} collection(s) into {target_root.name}; "
            f"LOD groups joined: {merge_stats.get('merged_lod_groups', 0)}"
        )
        if planner_path:
            msg += ", added to Import/Export list" if planner_added else ", already in Import/Export list"
        if failures:
            self.report({"WARNING"}, msg + f"; failed {len(failures)} item(s), see System Console")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}

class CRAY_PG_AssetProxySettings(PropertyGroup):
    source_object: PointerProperty(
        name="Proxy Source Object",
        description="Placed asset object that will be replaced by an P3D proxy; leave empty to use selected asset object(s)",
        type=bpy.types.Object,
    )
    target_object: PointerProperty(
        name="Target Resolution / LOD",
        description="P3D LOD mesh, for example Resolution 0, that will own the created proxy",
        type=bpy.types.Object,
    )
    target_collection: PointerProperty(
        name="Target P3D Collection",
        description="Target .p3d root collection that will receive generated proxies; leave empty to infer from selection",
        type=bpy.types.Collection,
    )
    duplicate_to_all_resolution_lods: BoolProperty(
        name="Duplicate to all Resolution LODs",
        default=False,
        description="After conversion, also create the same P3D proxy in every Resolution LOD under the same .p3d root",
    )
    proxy_duplicate_resolution: BoolProperty(
        name="Resolution",
        description="Create proxies under Resolution LODs",
        default=True,
    )
    proxy_duplicate_geometries: BoolProperty(
        name="Geometries",
        description="Create proxies under Geometry LOD",
        default=False,
    )
    proxy_duplicate_roadway: BoolProperty(
        name="Roadway",
        description="Create proxies under Roadway LOD",
        default=False,
    )
    proxy_duplicate_point_clouds: BoolProperty(
        name="Point clouds",
        description="Create proxies under Point clouds / Memory LOD",
        default=False,
    )


_NH_TEMP_ASSET_LIBRARY_NAME = "NH Temp Asset Library"
_NH_TEMP_ASSET_SCENE_NAME = "NH Asset Library Scene"
_NH_OBJECTS_CUSTOM_LABEL = "Custom"
_NH_OBJECTS_CUSTOM_LIBRARY_NAME = "NH Objects - Custom"
_NH_OBJECTS_ASSET_BLEND_NAME = "_NH_AssetLibrary.blend"
_NH_OBJECTS_ASSET_MANIFEST_NAME = "_NH_AssetLibrary.manifest.json"
_NH_OBJECTS_CACHE_FOLDER_NAME = "NH_Objects_AssetLibraries"
_NH_OBJECTS_ASSET_PREVIEWS_FOLDER_NAME = "_NH_previews"
_NH_OBJECTS_INCREMENTAL_CACHE_FOLDER_NAME = "_NH_incremental"
_NH_TEXTURE_CACHE_FOLDER_NAME = "NH_TexturePreviewCache"
_NH_OBJECTS_ASSET_CATALOG_FILE_NAME = "blender_assets.cats.txt"
_NH_PREVIEW_CAMERA_SELECTION_RE = re.compile(
    r"^nh_cam(?:_(-?(?:\d+(?:\.\d*)?|\.\d+)))?$",
    re.IGNORECASE,
)
_NH_OBJECTS_LEGACY_SOURCE_CACHE_FILENAMES = {
    _NH_OBJECTS_ASSET_BLEND_NAME,
    _NH_OBJECTS_ASSET_MANIFEST_NAME,
    _NH_OBJECTS_ASSET_CATALOG_FILE_NAME,
}
_NH_OBJECTS_ASSET_CATALOG_NAMESPACE = uuid.UUID("c91f1215-9261-4d7e-8df4-4bb81567b6a8")
_NH_OBJECTS_ASSET_MANIFEST_VERSION = 8

def _path_is_under_or_equal(path: str, root: str) -> bool:
    if not path or not root:
        return False
    try:
        path_abs = os.path.abspath(bpy.path.abspath(path))
        root_abs = os.path.abspath(bpy.path.abspath(root))
        return os.path.normcase(os.path.commonpath([path_abs, root_abs])) == os.path.normcase(root_abs)
    except Exception:
        return False

def _nh_asset_library_settings(context=None):
    scene = getattr(context, "scene", None) if context is not None else getattr(bpy.context, "scene", None)
    return getattr(scene, "cray_asset_library_settings", None) if scene is not None else None

def _nh_objects_common_root(settings=None) -> str:
    from .nh_base import (_NH_OBJECTS_DEFAULT_COMMON_ROOT)
    settings = settings or _nh_asset_library_settings()
    path = getattr(settings, "common_root", "") if settings is not None else ""
    return os.path.abspath(bpy.path.abspath(path or _NH_OBJECTS_DEFAULT_COMMON_ROOT))

def _nh_objects_environment_root(settings=None) -> str:
    from .nh_base import (_NH_OBJECTS_DEFAULT_ENVIRONMENT_ROOT)
    settings = settings or _nh_asset_library_settings()
    path = getattr(settings, "environment_root", "") if settings is not None else ""
    return os.path.abspath(bpy.path.abspath(path or _NH_OBJECTS_DEFAULT_ENVIRONMENT_ROOT))

def _nh_objects_custom_search_root(settings=None) -> str:
    from .nh_base import (_NH_OBJECTS_DEFAULT_CUSTOM_SEARCH_ROOT)
    settings = settings or _nh_asset_library_settings()
    path = getattr(settings, "custom_search_root", "") if settings is not None else ""
    return os.path.abspath(bpy.path.abspath(path or _NH_OBJECTS_DEFAULT_CUSTOM_SEARCH_ROOT))

def _is_ignored_nh_objects_asset_path(path: str, settings=None) -> bool:
    common_root = _nh_objects_common_root(settings)
    buildings_root = os.path.join(common_root, "Buildings")
    return _path_is_under_or_equal(path, buildings_root)

def _nh_objects_asset_cache_base(create=False) -> str:
    base_dir = os.environ.get("LOCALAPPDATA") or ""
    if not base_dir:
        try:
            base_dir = bpy.utils.user_resource("CONFIG") or ""
        except Exception:
            base_dir = ""
    if not base_dir:
        base_dir = bpy.app.tempdir or os.path.expanduser("~")
    path = os.path.join(base_dir, "NH_Blender", _NH_OBJECTS_CACHE_FOLDER_NAME)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _nh_objects_asset_cache_root(label: str, create=False) -> str:
    safe_label = re.sub(r'[<>:"/\\|?*]+', "_", str(label or "Library")).strip(" .") or "Library"
    path = os.path.join(_nh_objects_asset_cache_base(create=create), safe_label)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _nh_objects_custom_asset_cache_root(create=False) -> str:
    return _nh_objects_asset_cache_root(_NH_OBJECTS_CUSTOM_LABEL, create=create)


def _iter_nh_objects_source_roots(settings=None):
    roots = (
        ("Common", _nh_objects_common_root(settings)),
        ("Environment", _nh_objects_environment_root(settings)),
    )
    for label, path in roots:
        path_abs = os.path.abspath(bpy.path.abspath(path))
        if os.path.isdir(path_abs) and not _is_ignored_nh_objects_asset_path(path_abs, settings):
            yield label, path_abs


def _iter_legacy_nh_asset_library_artifacts(settings=None):
    for _label, root_abs in _iter_nh_objects_source_roots(settings):
        for current, dirs, files in os.walk(root_abs):
            dirs[:] = [
                name for name in dirs
                if not _is_ignored_nh_objects_asset_path(os.path.join(current, name), settings)
            ]
            if _is_ignored_nh_objects_asset_path(current, settings):
                continue

            for dirname in list(dirs):
                if dirname != _NH_OBJECTS_ASSET_PREVIEWS_FOLDER_NAME:
                    continue
                path_abs = os.path.abspath(os.path.join(current, dirname))
                if _path_is_under_or_equal(path_abs, root_abs):
                    yield "dir", path_abs
                dirs.remove(dirname)

            for filename in files:
                if filename not in _NH_OBJECTS_LEGACY_SOURCE_CACHE_FILENAMES:
                    continue
                path_abs = os.path.abspath(os.path.join(current, filename))
                if _path_is_under_or_equal(path_abs, root_abs):
                    yield "file", path_abs


def _cleanup_legacy_nh_asset_library_artifacts(settings=None):
    from .nh_base import (_fmt_exc)
    artifacts = sorted(
        set(_iter_legacy_nh_asset_library_artifacts(settings)),
        key=lambda item: (item[0] != "dir", item[1].lower()),
    )
    removed_files = 0
    removed_dirs = 0
    failed = []
    for kind, path_abs in artifacts:
        try:
            if kind == "dir":
                if os.path.isdir(path_abs):
                    shutil.rmtree(path_abs, ignore_errors=False)
                    removed_dirs += 1
            elif os.path.isfile(path_abs):
                os.remove(path_abs)
                removed_files += 1
        except Exception as e:
            failed.append(f"{path_abs}: {_fmt_exc(e)}")
    return {
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
        "failed": failed,
    }


def _iter_nh_objects_asset_roots(settings=None):
    for label, _source_abs in _iter_nh_objects_source_roots(settings):
        yield f"NH Objects - {label}", _nh_objects_asset_cache_root(label, create=True)
    yield _NH_OBJECTS_CUSTOM_LIBRARY_NAME, _nh_objects_custom_asset_cache_root(create=True)


def _safe_cache_relpath(path_abs: str, root_abs: str) -> list[str]:
    try:
        rel = os.path.relpath(path_abs, root_abs)
    except Exception:
        rel = os.path.basename(path_abs)
    rel = "" if rel in {"", "."} else rel
    parts = []
    for part in re.split(r"[\\/]+", rel):
        if not part or part in {".", ".."}:
            continue
        safe = re.sub(r'[<>:"/\\|?*]+', "_", part).strip(" .")
        if safe:
            parts.append(safe)
    return parts


def _nh_asset_cache_folder_for_source_folder(source_folder_abs: str, settings=None, create=False) -> str:
    source_folder_abs = os.path.abspath(bpy.path.abspath(source_folder_abs))
    for label, root_abs in _iter_nh_objects_source_roots(settings):
        if _path_is_under_or_equal(source_folder_abs, root_abs):
            root = _nh_objects_asset_cache_root(label, create=create)
            cache_folder = os.path.join(root, *_safe_cache_relpath(source_folder_abs, root_abs))
            if create:
                os.makedirs(cache_folder, exist_ok=True)
            return cache_folder

    fallback_root = _nh_objects_asset_cache_root("Other", create=create)
    cache_folder = os.path.join(fallback_root, re.sub(r'[<>:"/\\|?*]+', "_", source_folder_abs).strip(" ."))
    if create:
        os.makedirs(cache_folder, exist_ok=True)
    return cache_folder


def _nh_asset_catalog_path_for_source_folder(source_folder_abs: str, settings=None) -> str:
    source_folder_abs = os.path.abspath(bpy.path.abspath(source_folder_abs))
    for _label, root_abs in _iter_nh_objects_source_roots(settings):
        if _path_is_under_or_equal(source_folder_abs, root_abs):
            parts = _safe_cache_relpath(source_folder_abs, root_abs)
            return "/".join(parts) if parts else "Root"
    return "Other"


def _nh_asset_catalog_id(library_name: str, catalog_path: str) -> str:
    return str(uuid.uuid5(_NH_OBJECTS_ASSET_CATALOG_NAMESPACE, f"{library_name}:{catalog_path}"))


def _nh_asset_catalog_paths_by_cache_root(source_folders, settings=None):
    out = {}
    for folder_abs in source_folders or []:
        cache_root = None
        library_name = None
        folder_abs = os.path.abspath(bpy.path.abspath(folder_abs))
        for label, root_abs in _iter_nh_objects_source_roots(settings):
            if _path_is_under_or_equal(folder_abs, root_abs):
                library_name = f"NH Objects - {label}"
                cache_root = _nh_objects_asset_cache_root(label, create=True)
                break
        if not cache_root:
            library_name = "NH Objects - Other"
            cache_root = _nh_objects_asset_cache_root("Other", create=True)
        catalog_path = _nh_asset_catalog_path_for_source_folder(folder_abs, settings)
        out.setdefault(cache_root, {})[catalog_path] = _nh_asset_catalog_id(library_name, catalog_path)
    return out


def _write_nh_asset_catalog_file(cache_root: str, catalog_paths_to_ids):
    if not cache_root or not catalog_paths_to_ids:
        return ""
    os.makedirs(cache_root, exist_ok=True)
    catalog_file = os.path.join(cache_root, _NH_OBJECTS_ASSET_CATALOG_FILE_NAME)
    lines = [
        "# This is an Asset Catalog Definition file for Blender.",
        "#",
        "# Empty lines and lines starting with `#` will be ignored.",
        "# The first non-ignored line should be the version indicator.",
        "# Other lines are of the format \"UUID:catalog/path/for/assets:simple catalog name\".",
        "",
        "VERSION 1",
        "",
    ]
    for catalog_path, catalog_id in sorted((catalog_paths_to_ids or {}).items(), key=lambda item: item[0].lower()):
        simple_name = catalog_path.split("/")[-1] if catalog_path else "Root"
        lines.append(f"{catalog_id}:{catalog_path}:{simple_name}")
    with open(catalog_file, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return catalog_file


def _nh_asset_blend_path_for_folder(folder_abs: str) -> str:
    return os.path.join(folder_abs, _NH_OBJECTS_ASSET_BLEND_NAME)

def _nh_asset_manifest_path_for_folder(folder_abs: str) -> str:
    return os.path.join(folder_abs, _NH_OBJECTS_ASSET_MANIFEST_NAME)

def _iter_p3d_files_direct(folder_abs: str, settings=None):
    if not folder_abs or not os.path.isdir(folder_abs) or _is_ignored_nh_objects_asset_path(folder_abs, settings):
        return []
    out = []
    try:
        for fn in os.listdir(folder_abs):
            fp = os.path.join(folder_abs, fn)
            if os.path.isfile(fp) and fn.lower().endswith(".p3d"):
                out.append(fp)
    except Exception:
        return []
    out.sort(key=lambda x: x.lower())
    return out

def _iter_nh_objects_asset_source_folders(settings=None):
    seen = set()
    for _label, root_abs in _iter_nh_objects_source_roots(settings):
        for current, dirs, _files in os.walk(root_abs):
            dirs[:] = [
                name for name in dirs
                if not _is_ignored_nh_objects_asset_path(os.path.join(current, name), settings)
            ]
            if _is_ignored_nh_objects_asset_path(current, settings):
                continue
            key = os.path.normcase(os.path.abspath(current))
            if key in seen:
                continue
            seen.add(key)
            if _iter_p3d_files_direct(current, settings):
                yield current
