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

# nh_collider_exp.py
# auto-split slice; cross-module refs resolved with in-function imports

def _require_collider_exp_enabled_exp(op, context):
    from .nh_collider import (_collider_exp_settings_exp)
    settings = _collider_exp_settings_exp(context)
    if settings is None:
        op.report({"ERROR"}, "Experimental collider settings are unavailable")
        return None
    return settings


def _set_collider_exp_settings_object_exp(context, obj):
    from .nh_collider import (_collider_exp_settings_exp)
    from .nh_snap import (_tag_redraw_all_areas)
    es = _collider_exp_settings_exp(context)
    if es is None:
        return

    try:
        if getattr(es, "geometry_object", None) == obj:
            es.geometry_object = None
        else:
            es.geometry_object = obj
    except Exception:
        pass

    try:
        context.view_layer.update()
    except Exception:
        pass
    _tag_redraw_all_areas(context)


def _sanitize_collider_exp_geometry_object_exp(context, settings=None):
    from .nh_collider import (_collider_exp_settings_exp, _is_existing_collider_target_for_lod)
    from .nh_snap import (_tag_redraw_all_areas)
    settings = settings or _collider_exp_settings_exp(context)
    if settings is None:
        return None

    target_obj = getattr(settings, "geometry_object", None)
    target_lod = str(getattr(settings, "target_lod", "6") or "6")
    if target_obj is None or _is_existing_collider_target_for_lod(target_obj, target_lod):
        return target_obj

    try:
        settings.geometry_object = None
    except Exception:
        pass
    try:
        context.view_layer.update()
    except Exception:
        pass
    _tag_redraw_all_areas(context)
    return None


def _copy_collider_exp_settings_to_operator_exp(op, settings, prop_names=None):
    from .nh_collider import (_COLLIDER_EXP_COMMON_PROPS, _COLLIDER_EXP_PERSISTENT_OPERATOR_PROPS)
    if op is None or settings is None:
        return
    for prop_name in prop_names or _COLLIDER_EXP_COMMON_PROPS:
        if prop_name not in _COLLIDER_EXP_PERSISTENT_OPERATOR_PROPS:
            continue
        if not hasattr(op, prop_name) or not hasattr(settings, prop_name):
            continue
        try:
            setattr(op, prop_name, getattr(settings, prop_name))
        except Exception:
            pass


def _write_collider_exp_operator_to_settings_exp(op, settings, prop_names=None):
    from .nh_collider import (_COLLIDER_EXP_COMMON_PROPS, _COLLIDER_EXP_PERSISTENT_OPERATOR_PROPS)
    if op is None or settings is None:
        return
    for prop_name in prop_names or _COLLIDER_EXP_COMMON_PROPS:
        if prop_name not in _COLLIDER_EXP_PERSISTENT_OPERATOR_PROPS:
            continue
        if not hasattr(op, prop_name) or not hasattr(settings, prop_name):
            continue
        try:
            setattr(settings, prop_name, getattr(op, prop_name))
        except Exception:
            pass


def _assign_collider_exp_operator_props_exp(op, settings, prop_names=None):
    _copy_collider_exp_settings_to_operator_exp(op, settings, prop_names=prop_names)


def _collider_exp_operator_props_exp(extra_props=()):
    from .nh_collider import (_COLLIDER_EXP_COMMON_PROPS)
    return tuple(dict.fromkeys((*_COLLIDER_EXP_COMMON_PROPS, *extra_props)))


def _draw_collider_exp_vector_props_exp(layout, op, prop_names, labels):
    row = layout.row(align=True)
    for prop_name, label in zip(prop_names, labels):
        if hasattr(op, prop_name):
            row.prop(op, prop_name, text=label)


def _draw_collider_exp_common_operator_props_exp(layout, op):
    layout.use_property_split = True
    layout.use_property_decorate = False
    if hasattr(op, "target_lod"):
        layout.prop(op, "target_lod")

    box = layout.box()
    box.label(text="Common Transform", icon="EMPTY_ARROWS")
    box.label(text="Scale")
    _draw_collider_exp_vector_props_exp(
        box,
        op,
        ("scale_x", "scale_y", "scale_z"),
        ("X", "Y", "Z"),
    )
    if hasattr(op, "scale_multiplier"):
        box.prop(op, "scale_multiplier")
    box.label(text="Offset")
    _draw_collider_exp_vector_props_exp(
        box,
        op,
        ("offset_x", "offset_y", "offset_z"),
        ("X", "Y", "Z"),
    )
    if hasattr(op, "minimum_size"):
        box.prop(op, "minimum_size")
    if hasattr(op, "normal_minimum_size"):
        box.prop(op, "normal_minimum_size")
    if hasattr(op, "floor_contact"):
        box.prop(op, "floor_contact")
    if hasattr(op, "merge_distance"):
        box.prop(op, "merge_distance")
    if hasattr(op, "recalc_normals"):
        box.prop(op, "recalc_normals")


def _draw_collider_exp_operator_panel_exp(layout, op, extra_props=(), *, extra_label="Shape"):
    _draw_collider_exp_common_operator_props_exp(layout, op)
    visible_props = [prop_name for prop_name in extra_props if hasattr(op, prop_name)]
    if not visible_props:
        return
    box = layout.box()
    box.label(text=extra_label, icon="MOD_REMESH")
    for prop_name in visible_props:
        box.prop(op, prop_name)


def _draw_collider_exp_guide_conversion_panel_exp(layout, op):
    layout.use_property_split = True
    layout.use_property_decorate = False
    if hasattr(op, "target_lod"):
        layout.prop(op, "target_lod")
    box = layout.box()
    box.label(text="Guide Conversion", icon="MESH_CUBE")
    for prop_name in ("minimum_size", "merge_distance", "recalc_normals"):
        if hasattr(op, prop_name):
            box.prop(op, prop_name)


def _resolve_collider_exp_source_object_exp(context, preferred_obj=None):
    active = getattr(getattr(context, "view_layer", None), "objects", None)
    active_obj = getattr(active, "active", None) if active is not None else None

    def _is_valid_source(obj):
        return obj is not None and obj.type == "MESH"

    if _is_valid_source(active_obj) and active_obj.mode == "EDIT":
        return active_obj

    selected_meshes = [
        obj for obj in getattr(context, "selected_objects", [])
        if _is_valid_source(obj)
    ]
    if active_obj in selected_meshes:
        return active_obj
    if selected_meshes:
        return selected_meshes[0]

    if _is_valid_source(active_obj):
        return active_obj
    if _is_valid_source(preferred_obj):
        return preferred_obj
    return None


def _ensure_collider_exp_target_object_exp(context, settings, source_obj, op=None):
    from .nh_collider import (_allow_collider_exp_in_place_target_exp, _ensure_collider_lod_object, _is_existing_collider_target_for_lod)
    from .nh_scatter import (_COLLIDER_LOD_NAMES, _collider_lod_token_from_object)
    from .nh_snap import (_collider_target_validation_error)
    if settings is None:
        raise RuntimeError("Experimental collider settings are unavailable")

    lod_token = str(getattr(op, "target_lod", getattr(settings, "target_lod", "6")) or "6")
    preferred_obj = _sanitize_collider_exp_geometry_object_exp(context, settings)
    allow_in_place_source = _allow_collider_exp_in_place_target_exp(source_obj, lod_token)
    if preferred_obj == source_obj and not allow_in_place_source:
        preferred_obj = None
    preferred_any_lod = (
        getattr(preferred_obj, "type", None) == "MESH"
        and _collider_lod_token_from_object(preferred_obj, allow_name_fallback=True) in _COLLIDER_LOD_NAMES
    )
    if not preferred_any_lod and not _is_existing_collider_target_for_lod(preferred_obj, lod_token):
        preferred_obj = None
    err = _collider_target_validation_error(
        preferred_obj,
        lod_token,
        source_obj=source_obj,
        allow_same_source=allow_in_place_source,
        allow_any_collider_lod=preferred_any_lod,
    )
    if err:
        raise RuntimeError(err)

    target_obj = _ensure_collider_lod_object(
        context,
        source_obj,
        lod_token,
        preferred_obj=preferred_obj,
        exclude_obj=None if allow_in_place_source else source_obj,
        allow_any_preferred_lod=preferred_any_lod,
        preserve_existing_lod=preferred_any_lod,
    )
    _set_collider_exp_settings_object_exp(context, target_obj)
    try:
        settings.geometry_object = target_obj
    except Exception:
        pass
    try:
        if source_obj is not None:
            settings.source_object = source_obj
    except Exception:
        pass
    return target_obj


def _restore_collider_exp_source_context_exp(context, source_obj, restore_edit_mode=False):
    from .nh_snap import (_deselect_all_in_view_layer, _select_object_in_view_layer)
    if not _is_live_blender_object_exp(source_obj) or getattr(source_obj, "type", None) != "MESH":
        return
    try:
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    try:
        _deselect_all_in_view_layer(context)
        _select_object_in_view_layer(context, source_obj, active=True)
    except Exception:
        return
    if restore_edit_mode:
        try:
            bpy.ops.object.mode_set(mode="EDIT")
        except Exception:
            pass


def _collider_exp_guide_source_object_exp(context, guide_obj, settings=None):
    from .nh_collider import (_COLLIDER_EXP_GUIDE_SOURCE_PROP)
    if not _is_collider_exp_guide_object_exp(guide_obj):
        return guide_obj

    source_name = ""
    try:
        source_name = str(guide_obj.get(_COLLIDER_EXP_GUIDE_SOURCE_PROP, "") or "")
    except Exception:
        source_name = ""
    if source_name:
        obj = bpy.data.objects.get(source_name)
        if _is_live_blender_object_exp(obj) and not _is_collider_exp_guide_object_exp(obj):
            return obj

    preferred_obj = getattr(settings, "source_object", None) if settings is not None else None
    if _is_live_blender_object_exp(preferred_obj) and not _is_collider_exp_guide_object_exp(preferred_obj):
        return preferred_obj

    selected = [
        obj for obj in getattr(context, "selected_objects", [])
        if _is_live_blender_object_exp(obj)
        and getattr(obj, "type", None) == "MESH"
        and not _is_collider_exp_guide_object_exp(obj)
    ]
    return selected[0] if selected else None


def _resolve_collider_exp_guide_creation_source_exp(context, settings):
    source_obj = _resolve_collider_exp_source_object_exp(
        context,
        getattr(settings, "source_object", None) if settings is not None else None,
    )
    if _is_collider_exp_guide_object_exp(source_obj):
        source_obj = _collider_exp_guide_source_object_exp(context, source_obj, settings)
    return source_obj



def _collider_exp_vec_from_props_exp(op, prefix):
    return Vector((
        float(getattr(op, f"{prefix}_x", 0.0)),
        float(getattr(op, f"{prefix}_y", 0.0)),
        float(getattr(op, f"{prefix}_z", 0.0)),
    ))


def _collider_exp_scale_vec_exp(op):
    mult = max(float(getattr(op, "scale_multiplier", 1.0)), 0.001)
    return Vector((
        max(float(getattr(op, "scale_x", 1.0)), 0.001) * mult,
        max(float(getattr(op, "scale_y", 1.0)), 0.001) * mult,
        max(float(getattr(op, "scale_z", 1.0)), 0.001) * mult,
    ))


def _collider_exp_data_is_guide_exp(data, guide_type=""):
    return _is_collider_exp_guide_object_exp(data.get("source_obj"), guide_type)


def _collider_exp_shape_scale_vec_exp(data, op):
    if _collider_exp_data_is_guide_exp(data):
        return Vector((1.0, 1.0, 1.0))
    return _collider_exp_scale_vec_exp(op)


def _collider_exp_shape_offset_vec_exp(data, op):
    if _collider_exp_data_is_guide_exp(data):
        return Vector((0.0, 0.0, 0.0))
    return _collider_exp_vec_from_props_exp(op, "offset")


def _bounds_from_points_exp(points):
    if not points:
        raise RuntimeError("No points available")
    min_v = Vector((
        min(point.x for point in points),
        min(point.y for point in points),
        min(point.z for point in points),
    ))
    max_v = Vector((
        max(point.x for point in points),
        max(point.y for point in points),
        max(point.z for point in points),
    ))
    return min_v, max_v


def _safe_normalized_vector_exp(vec):
    if vec is None:
        return None
    try:
        if vec.length_squared <= 1e-12:
            return None
        out = vec.copy()
        out.normalize()
        return out
    except Exception:
        return None


def _average_vector_exp(points):
    if not points:
        return Vector((0.0, 0.0, 0.0))
    total = Vector((0.0, 0.0, 0.0))
    for point in points:
        total += point
    return total / len(points)


def _cluster_points_by_projection_exp(points, axis, span, diagonal):
    if not points:
        return []
    projected = sorted((float(point.dot(axis)), point) for point in points)
    tol = max(float(span) * 0.035, float(diagonal) * 1e-5, 1e-6)
    clusters = []
    current = [projected[0][1]]
    last_projection = projected[0][0]
    for projection, point in projected[1:]:
        if abs(projection - last_projection) <= tol:
            current.append(point)
        else:
            clusters.append(current)
            current = [point]
        last_projection = projection
    clusters.append(current)
    return clusters


def _candidate_ring_axis_score_exp(points, axis, diagonal):
    axis = _safe_normalized_vector_exp(axis)
    if axis is None:
        return None
    projections = [float(point.dot(axis)) for point in points]
    span = max(projections) - min(projections)
    if span <= max(diagonal * 1e-5, 1e-6):
        return None
    clusters = _cluster_points_by_projection_exp(points, axis, span, diagonal)
    if len(clusters) < 2:
        return None
    first = clusters[0]
    last = clusters[-1]
    if len(first) < 3 or len(last) < 3:
        return None
    center_first = _average_vector_exp(first)
    center_last = _average_vector_exp(last)
    depth_axis = _safe_normalized_vector_exp(center_last - center_first)
    if depth_axis is None:
        return None
    if depth_axis.dot(axis) < 0.0:
        depth_axis.negate()
    center = (center_first + center_last) * 0.5
    ring_points = list(first) + list(last)
    radial_vectors = [
        point - center - depth_axis * ((point - center).dot(depth_axis))
        for point in ring_points
    ]
    radial_vectors = [vec for vec in radial_vectors if vec.length_squared > 1e-12]
    if len(radial_vectors) < 3:
        return None
    axis_a = max(radial_vectors, key=lambda vec: vec.length_squared)
    axis_a = _safe_normalized_vector_exp(axis_a)
    if axis_a is None:
        return None
    axis_b = _safe_normalized_vector_exp(depth_axis.cross(axis_a))
    if axis_b is None:
        return None
    axis_a = _safe_normalized_vector_exp(axis_b.cross(depth_axis))
    if axis_a is None:
        return None
    radius_a = max(abs(vec.dot(axis_a)) for vec in radial_vectors)
    radius_b = max(abs(vec.dot(axis_b)) for vec in radial_vectors)
    if min(radius_a, radius_b) <= max(diagonal * 1e-5, 1e-6):
        return None
    depth = (center_last - center_first).length
    min_ring_count = min(len(first), len(last))
    middle_count = max(0, len(points) - len(first) - len(last))
    score = (
        min_ring_count,
        len(first) + len(last),
        depth,
        -middle_count,
    )
    return {
        "score": score,
        "center": center,
        "axis_a": axis_a,
        "axis_b": axis_b,
        "depth_axis": depth_axis,
        "radius_a": radius_a,
        "radius_b": radius_b,
        "depth": depth,
        "edge_count": min_ring_count,
        "ignored_between_rings": middle_count,
    }


def _add_unique_axis_candidate_exp(candidates, axis):
    axis = _safe_normalized_vector_exp(axis)
    if axis is None:
        return
    for existing in candidates:
        if abs(existing.dot(axis)) > 0.9975:
            return
    candidates.append(axis)


def _radial_direction_count_for_profile_exp(radial_vectors, axis_a, axis_b):
    keys = set()
    for vec in radial_vectors:
        u = float(vec.dot(axis_a))
        v = float(vec.dot(axis_b))
        if (u * u + v * v) <= 1e-12:
            continue
        angle = (math.atan2(v, u) + (2.0 * math.pi)) % (2.0 * math.pi)
        keys.add(int(round(angle / (2.0 * math.pi) * 4096.0)) % 4096)
    return len(keys)


def _candidate_cylinder_axis_profile_exp(points, axis, diagonal):
    axis = _safe_normalized_vector_exp(axis)
    if axis is None or len(points) < 4:
        return None

    projected = sorted((float(point.dot(axis)), point) for point in points)
    min_projection = projected[0][0]
    max_projection = projected[-1][0]
    span = max_projection - min_projection
    if span <= max(float(diagonal) * 1e-5, 1e-6):
        return None

    end_tolerance = max(span * 0.06, float(diagonal) * 1e-5, 1e-6)
    first = [point for projection, point in projected if projection <= min_projection + end_tolerance]
    last = [point for projection, point in projected if projection >= max_projection - end_tolerance]
    minimum_end_points = 2 if len(points) >= 6 else 1
    if len(first) < minimum_end_points or len(last) < minimum_end_points:
        pick_count = max(minimum_end_points, min(max(1, len(points) // 8), 12))
        first = [point for _projection, point in projected[:pick_count]]
        last = [point for _projection, point in projected[-pick_count:]]

    center_first = _average_vector_exp(first)
    center_last = _average_vector_exp(last)
    depth_axis = _safe_normalized_vector_exp(center_last - center_first)
    if depth_axis is None:
        center_all = _average_vector_exp(points)
        center_first = center_all + axis * (min_projection - float(center_all.dot(axis)))
        center_last = center_all + axis * (max_projection - float(center_all.dot(axis)))
        depth_axis = axis
    if depth_axis.dot(axis) < 0.0:
        depth_axis.negate()

    depth = (center_last - center_first).length
    if depth <= max(float(diagonal) * 1e-5, 1e-6):
        return None

    center = (center_first + center_last) * 0.5
    radial_vectors = []
    radial_lengths = []
    for point in points:
        offset = point - center
        radial = offset - depth_axis * float(offset.dot(depth_axis))
        length = radial.length
        if length <= max(float(diagonal) * 1e-6, 1e-8):
            continue
        radial_vectors.append(radial)
        radial_lengths.append(length)
    if len(radial_vectors) < 3:
        return None

    axis_a = _safe_normalized_vector_exp(max(radial_vectors, key=lambda vec: vec.length_squared))
    if axis_a is None:
        return None
    axis_b = _safe_normalized_vector_exp(depth_axis.cross(axis_a))
    if axis_b is None:
        return None
    axis_a = _safe_normalized_vector_exp(axis_b.cross(depth_axis))
    if axis_a is None:
        return None

    radius_a = max(abs(float(vec.dot(axis_a))) for vec in radial_vectors)
    radius_b = max(abs(float(vec.dot(axis_b))) for vec in radial_vectors)
    min_radius = max(float(diagonal) * 1e-5, 1e-6)
    if min(radius_a, radius_b) <= min_radius:
        return None

    avg_radius = sum(radial_lengths) / len(radial_lengths)
    max_radius = max(radial_lengths)
    min_radial = min(radial_lengths)
    radial_spread = (max_radius - min_radial) / max(avg_radius, min_radius)
    ellipse_imbalance = abs(radius_a - radius_b) / max((radius_a + radius_b) * 0.5, min_radius)
    edge_count = max(4, min(_radial_direction_count_for_profile_exp(radial_vectors, axis_a, axis_b), 128))
    min_end_count = min(len(first), len(last))
    min_reliable_end_count = max(3, min(12, int(math.ceil(edge_count * 0.35))))
    if min_end_count < min_reliable_end_count:
        return None
    score = (
        min_end_count,
        len(first) + len(last),
        -radial_spread,
        -ellipse_imbalance,
        depth / max(float(diagonal), 1e-6),
    )
    return {
        "score": score,
        "center": center,
        "axis_a": axis_a,
        "axis_b": axis_b,
        "depth_axis": depth_axis,
        "radius_a": radius_a,
        "radius_b": radius_b,
        "depth": depth,
        "edge_count": edge_count,
        "end_points_a": len(first),
        "end_points_b": len(last),
        "end_ring_ratio": min_end_count / max(float(edge_count), 1.0),
    }


def _inferred_cylinder_axis_profile_exp(data):
    points = [point.copy() for point in (data.get("local_points") or [])]
    if len(points) < 4:
        return None

    min_v, max_v = _bounds_from_points_exp(points)
    size = max_v - min_v
    diagonal = size.length
    if diagonal <= 1e-8:
        return None

    candidates = []
    try:
        _add_unique_axis_candidate_exp(candidates, _collider_exp_principal_axis_exp(points))
    except Exception:
        pass

    for axis_index in sorted(range(3), key=lambda idx: abs(size[idx]), reverse=True):
        _add_unique_axis_candidate_exp(candidates, _axis_vector_exp(axis_index))

    edge_vectors = sorted(
        [
            vec.copy() for vec in (data.get("edge_vectors_local") or [])
            if getattr(vec, "length_squared", 0.0) > 1e-12
        ],
        key=lambda vec: vec.length_squared,
        reverse=True,
    )
    for vec in edge_vectors[:24]:
        _add_unique_axis_candidate_exp(candidates, vec)

    best = None
    for candidate in candidates:
        scored = _candidate_cylinder_axis_profile_exp(points, candidate, diagonal)
        if scored is None:
            continue
        if best is None or scored["score"] > best["score"]:
            best = scored
    if best is None:
        return None
    best.pop("score", None)
    return best


def _selected_two_ring_profile_exp(source_obj):
    if source_obj is None or source_obj.type != "MESH" or source_obj.mode != "EDIT":
        return None
    bm = bmesh.from_edit_mesh(source_obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    selected_verts = {vert for vert in bm.verts if vert.is_valid and vert.select}
    for edge in bm.edges:
        if edge.is_valid and edge.select:
            selected_verts.update(vert for vert in edge.verts if vert.is_valid)
    for face in bm.faces:
        if face.is_valid and face.select:
            selected_verts.update(vert for vert in face.verts if vert.is_valid)

    points = [vert.co.copy() for vert in selected_verts]
    if len(points) < 6:
        return None
    min_v, max_v = _bounds_from_points_exp(points)
    diagonal = (max_v - min_v).length
    if diagonal <= 1e-8:
        return None

    candidates = [
        Vector((1.0, 0.0, 0.0)),
        Vector((0.0, 1.0, 0.0)),
        Vector((0.0, 0.0, 1.0)),
        max_v - min_v,
    ]
    try:
        candidates.append(_collider_exp_principal_axis_exp(points))
    except Exception:
        pass

    best = None
    for candidate in candidates:
        scored = _candidate_ring_axis_score_exp(points, candidate, diagonal)
        if scored is None:
            continue
        if best is None or scored["score"] > best["score"]:
            best = scored
    if best is None:
        return None
    best.pop("score", None)
    return best


def _selected_local_points_and_faces_exp(source_obj):
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
    if not selected_verts:
        raise RuntimeError("Select vertices, edges or faces on the source mesh")

    face_centers = []
    face_normals = []
    edge_vectors = []

    def _add_edge_vector(v0, v1):
        vec = v1.co - v0.co
        if vec.length_squared > 1e-12:
            edge_vectors.append(vec.copy())

    selected_vert_set = set(selected_verts)
    for edge in bm.edges:
        if not edge.is_valid or len(edge.verts) != 2:
            continue
        if all(vert.is_valid and vert in selected_vert_set for vert in edge.verts):
            _add_edge_vector(edge.verts[0], edge.verts[1])

    for edge in selected_edges:
        if edge.is_valid and len(edge.verts) == 2 and all(vert.is_valid for vert in edge.verts):
            _add_edge_vector(edge.verts[0], edge.verts[1])

    selected_topology_faces = []
    for face in bm.faces:
        if not face.is_valid or not face.verts:
            continue
        verts = [vert for vert in face.verts if vert.is_valid]
        if not verts:
            continue
        if face.select or all(vert in selected_vert_set for vert in verts):
            selected_topology_faces.append((face, verts))

    for face, verts in selected_topology_faces:
        face_centers.append(sum((vert.co for vert in verts), Vector((0.0, 0.0, 0.0))) / len(verts))
        if face.normal.length_squared > 1e-12:
            face_normals.append(face.normal.copy())
        for idx, vert in enumerate(verts):
            _add_edge_vector(vert, verts[(idx + 1) % len(verts)])

    return [vert.co.copy() for vert in selected_verts], face_centers, edge_vectors, face_normals


def _object_local_topology_exp(source_obj):
    mesh = source_obj.data
    centers = []
    edge_vectors = []
    face_normals = []
    vertices = mesh.vertices

    for edge in mesh.edges:
        a, b = edge.vertices
        if 0 <= a < len(vertices) and 0 <= b < len(vertices):
            vec = vertices[b].co - vertices[a].co
            if vec.length_squared > 1e-12:
                edge_vectors.append(vec.copy())

    for poly in mesh.polygons:
        if not poly.vertices:
            continue
        center = Vector((0.0, 0.0, 0.0))
        count = 0
        valid_vertices = []
        for idx in poly.vertices:
            if 0 <= idx < len(vertices):
                vertex = vertices[idx]
                center += vertex.co
                valid_vertices.append(vertex)
                count += 1
        if count:
            centers.append(center / count)
        if poly.normal.length_squared > 1e-12:
            face_normals.append(poly.normal.copy())
        if not mesh.edges and len(valid_vertices) >= 2:
            for idx, vertex in enumerate(valid_vertices):
                vec = valid_vertices[(idx + 1) % len(valid_vertices)].co - vertex.co
                if vec.length_squared > 1e-12:
                    edge_vectors.append(vec.copy())
    return centers, edge_vectors, face_normals



def _collect_collider_exp_input_data_exp(context, source_obj, *, bounds_only=False):
    from .nh_textures import (_source_edit_selection_material_counts, _source_object_material_counts)
    if source_obj is None or source_obj.type != "MESH":
        raise RuntimeError("Source Object must be a mesh")

    matrix_world = source_obj.matrix_world.copy()
    face_centers = []
    edge_vectors = []
    face_normals = []
    two_ring_profile = None
    if source_obj.mode == "EDIT":
        two_ring_profile = _selected_two_ring_profile_exp(source_obj)
        local_points, face_centers, edge_vectors, face_normals = _selected_local_points_and_faces_exp(source_obj)
        material_counts = _source_edit_selection_material_counts(source_obj)
    elif bounds_only:
        local_points = [Vector(corner) for corner in source_obj.bound_box]
        material_counts = _source_object_material_counts(source_obj)
    else:
        local_points = [vert.co.copy() for vert in source_obj.data.vertices]
        face_centers, edge_vectors, face_normals = _object_local_topology_exp(source_obj)
        if not local_points:
            local_points = [Vector(corner) for corner in source_obj.bound_box]
        material_counts = _source_object_material_counts(source_obj)

    if not local_points:
        raise RuntimeError("Source Object has no usable geometry")

    min_v, max_v = _bounds_from_points_exp(local_points)
    world_points = [matrix_world @ point for point in local_points]
    data = {
        "source_obj": source_obj,
        "matrix_world": matrix_world,
        "local_points": local_points,
        "world_points": [point.copy() for point in world_points],
        "face_centers_local": face_centers,
        "edge_vectors_local": [vec.copy() for vec in edge_vectors],
        "face_normals_local": [normal.copy() for normal in face_normals],
        "material_counts": dict(material_counts or {}),
        "min": min_v,
        "max": max_v,
        "center": (min_v + max_v) * 0.5,
        "size": max_v - min_v,
        "world_floor_z": min((point.z for point in world_points), default=0.0),
        "two_ring_profile": two_ring_profile,
    }
    data["cylinder_axis_profile"] = _inferred_cylinder_axis_profile_exp(data)
    return data


def _collider_exp_data_from_local_points_exp(source_obj, local_points, *, face_centers=None, material_counts=None):
    if source_obj is None or source_obj.type != "MESH":
        raise RuntimeError("Source Object must be a mesh")
    if not local_points:
        raise RuntimeError("Source Object has no usable geometry")

    matrix_world = source_obj.matrix_world.copy()
    min_v, max_v = _bounds_from_points_exp(local_points)
    world_points = [matrix_world @ point for point in local_points]
    data = {
        "source_obj": source_obj,
        "matrix_world": matrix_world,
        "local_points": [point.copy() for point in local_points],
        "world_points": [point.copy() for point in world_points],
        "face_centers_local": list(face_centers or []),
        "edge_vectors_local": [],
        "face_normals_local": [],
        "material_counts": dict(material_counts or {}),
        "min": min_v,
        "max": max_v,
        "center": (min_v + max_v) * 0.5,
        "size": max_v - min_v,
        "world_floor_z": min((point.z for point in world_points), default=0.0),
    }
    data["cylinder_axis_profile"] = _inferred_cylinder_axis_profile_exp(data)
    return data


def _collider_exp_all_object_data_exp(source_obj):
    from .nh_textures import (_source_object_material_counts)
    if source_obj is None or source_obj.type != "MESH":
        raise RuntimeError("Source Object must be a mesh")

    face_centers = []
    edge_vectors = []
    face_normals = []
    material_counts = {}
    if source_obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(source_obj.data)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        local_points = [vert.co.copy() for vert in bm.verts if vert.is_valid]
        for edge in bm.edges:
            if edge.is_valid and len(edge.verts) == 2 and all(vert.is_valid for vert in edge.verts):
                vec = edge.verts[1].co - edge.verts[0].co
                if vec.length_squared > 1e-12:
                    edge_vectors.append(vec.copy())
        for face in bm.faces:
            if face.is_valid and face.verts:
                material_index = int(getattr(face, "material_index", 0) or 0)
                material_counts[material_index] = material_counts.get(material_index, 0) + 1
                face_centers.append(sum((vert.co for vert in face.verts), Vector((0.0, 0.0, 0.0))) / len(face.verts))
                if face.normal.length_squared > 1e-12:
                    face_normals.append(face.normal.copy())
    else:
        local_points = [vert.co.copy() for vert in source_obj.data.vertices]
        face_centers, edge_vectors, face_normals = _object_local_topology_exp(source_obj)
        if not local_points:
            local_points = [Vector(corner) for corner in source_obj.bound_box]
        material_counts = _source_object_material_counts(source_obj)

    data = _collider_exp_data_from_local_points_exp(
        source_obj,
        local_points,
        face_centers=face_centers,
        material_counts=material_counts,
    )
    data["edge_vectors_local"] = [vec.copy() for vec in edge_vectors]
    data["face_normals_local"] = [normal.copy() for normal in face_normals]
    data["cylinder_axis_profile"] = _inferred_cylinder_axis_profile_exp(data)
    return data


def _collider_exp_selected_source_objects_exp(context, settings):
    from .nh_collider import (_allow_collider_exp_in_place_target_exp)
    target_obj = _sanitize_collider_exp_geometry_object_exp(context, settings)
    target_lod = str(getattr(settings, "target_lod", "6") or "6") if settings is not None else "6"
    allow_target_as_source = _allow_collider_exp_in_place_target_exp(target_obj, target_lod)
    active = getattr(getattr(context, "view_layer", None), "objects", None)
    active_obj = getattr(active, "active", None) if active is not None else None

    def _is_valid_source(obj):
        return (
            _is_live_blender_object_exp(obj)
            and getattr(obj, "type", None) == "MESH"
            and (obj != target_obj or allow_target_as_source)
            and not _is_collider_exp_guide_object_exp(obj)
        )

    selected = [
        obj for obj in getattr(context, "selected_objects", [])
        if _is_valid_source(obj)
    ]
    ordered = []
    if active_obj in selected:
        ordered.append(active_obj)
    ordered.extend(obj for obj in selected if obj not in ordered)

    preferred = getattr(settings, "source_object", None) if settings is not None else None
    if _is_valid_source(preferred) and preferred not in ordered:
        ordered.append(preferred)

    if not ordered:
        resolved = _resolve_collider_exp_guide_creation_source_exp(context, settings)
        if _is_valid_source(resolved):
            ordered.append(resolved)
    return ordered


def _collider_exp_selected_vertex_indices_exp(source_obj):
    if source_obj is None or source_obj.type != "MESH" or source_obj.mode != "EDIT":
        return set()

    bm = bmesh.from_edit_mesh(source_obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()

    selected = {vert.index for vert in bm.verts if vert.is_valid and vert.select}
    for edge in bm.edges:
        if edge.is_valid and edge.select:
            selected.update(vert.index for vert in edge.verts if vert.is_valid)
    for face in bm.faces:
        if face.is_valid and face.select:
            selected.update(vert.index for vert in face.verts if vert.is_valid)
    return selected


def _collider_exp_mesh_graph_exp(source_obj, allowed_indices=None):
    allowed = set(allowed_indices) if allowed_indices is not None else None
    coords = {}
    adjacency = {}

    def _allow(index):
        return allowed is None or index in allowed

    def _add_vertex(index, co):
        if not _allow(index):
            return
        coords[index] = co.copy()
        adjacency.setdefault(index, set())

    def _add_edge(a, b):
        if a == b or not _allow(a) or not _allow(b):
            return
        if a not in adjacency or b not in adjacency:
            return
        adjacency[a].add(b)
        adjacency[b].add(a)

    if source_obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(source_obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.verts.index_update()
        for vert in bm.verts:
            if vert.is_valid:
                _add_vertex(vert.index, vert.co)
        for edge in bm.edges:
            if edge.is_valid and all(vert.is_valid for vert in edge.verts):
                _add_edge(edge.verts[0].index, edge.verts[1].index)
        for face in bm.faces:
            verts = [vert for vert in face.verts if vert.is_valid]
            for idx, vert in enumerate(verts):
                _add_edge(vert.index, verts[(idx + 1) % len(verts)].index)
    else:
        mesh = source_obj.data
        for vert in mesh.vertices:
            _add_vertex(int(vert.index), vert.co)
        for edge in mesh.edges:
            a, b = edge.vertices
            _add_edge(int(a), int(b))
        for poly in mesh.polygons:
            verts = [int(idx) for idx in poly.vertices]
            for idx, vert_idx in enumerate(verts):
                _add_edge(vert_idx, verts[(idx + 1) % len(verts)])

    _collider_exp_connect_duplicate_graph_points_exp(coords, adjacency)
    return coords, adjacency


def _collider_exp_graph_weld_tolerance_exp(coords):
    if not coords:
        return 0.0
    try:
        min_v, max_v = _bounds_from_points_exp(list(coords.values()))
        diagonal = (max_v - min_v).length
    except Exception:
        diagonal = 0.0
    return max(1e-6, diagonal * 1e-7)


def _collider_exp_connect_duplicate_graph_points_exp(coords, adjacency, tolerance=None):
    if not coords:
        return
    tolerance = (
        _collider_exp_graph_weld_tolerance_exp(coords)
        if tolerance is None
        else max(float(tolerance), 0.0)
    )
    if tolerance <= 0.0:
        return

    inv = 1.0 / tolerance
    tolerance_sq = tolerance * tolerance
    buckets = {}
    offsets = (-1, 0, 1)

    def _bucket_key(point):
        return (
            math.floor(point.x * inv),
            math.floor(point.y * inv),
            math.floor(point.z * inv),
        )

    for index in sorted(coords.keys()):
        point = coords.get(index)
        if point is None or not all(math.isfinite(float(point[axis])) for axis in range(3)):
            continue
        key = _bucket_key(point)
        for dx in offsets:
            for dy in offsets:
                for dz in offsets:
                    neighbor_key = (key[0] + dx, key[1] + dy, key[2] + dz)
                    for other_index in buckets.get(neighbor_key, ()):
                        other_point = coords.get(other_index)
                        if other_point is None:
                            continue
                        if (point - other_point).length_squared > tolerance_sq:
                            continue
                        adjacency.setdefault(index, set()).add(other_index)
                        adjacency.setdefault(other_index, set()).add(index)
        buckets.setdefault(key, []).append(index)



def _collider_exp_connected_component_indices_exp(source_obj, seed_indices=None, *, selected_only=False):
    seeds = set(seed_indices or [])
    allowed = seeds if selected_only and seeds else None
    coords, adjacency = _collider_exp_mesh_graph_exp(source_obj, allowed_indices=allowed)
    if not coords:
        return []

    seed_filter = seeds if seeds else set(coords.keys())
    visited = set()
    components = []
    for start in sorted(coords.keys()):
        if start in visited:
            continue
        stack = [start]
        visited.add(start)
        component = set()
        while stack:
            current = stack.pop()
            component.add(current)
            for nxt in adjacency.get(current, ()):
                if nxt in visited:
                    continue
                visited.add(nxt)
                stack.append(nxt)
        if component.intersection(seed_filter):
            components.append(sorted(component))
    return components


def _collider_exp_component_data_from_indices_exp(source_obj, component_indices):
    indices = {int(idx) for idx in component_indices or []}
    if source_obj is None or source_obj.type != "MESH" or not indices:
        raise RuntimeError("Source Object has no usable component geometry")

    local_points_by_index = {}
    face_centers = []
    edge_vectors = []
    face_normals = []
    material_counts = {}

    if source_obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(source_obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.verts.index_update()

        for vert in bm.verts:
            if vert.is_valid and vert.index in indices:
                local_points_by_index[int(vert.index)] = vert.co.copy()

        for edge in bm.edges:
            if not edge.is_valid or len(edge.verts) != 2:
                continue
            if all(vert.is_valid and int(vert.index) in indices for vert in edge.verts):
                vec = edge.verts[1].co - edge.verts[0].co
                if vec.length_squared > 1e-12:
                    edge_vectors.append(vec.copy())

        for face in bm.faces:
            if not face.is_valid or not face.verts:
                continue
            verts = [vert for vert in face.verts if vert.is_valid]
            if not verts or not all(int(vert.index) in indices for vert in verts):
                continue
            material_index = int(getattr(face, "material_index", 0) or 0)
            material_counts[material_index] = material_counts.get(material_index, 0) + 1
            face_centers.append(sum((vert.co for vert in verts), Vector((0.0, 0.0, 0.0))) / len(verts))
            if face.normal.length_squared > 1e-12:
                face_normals.append(face.normal.copy())
            for idx, vert in enumerate(verts):
                vec = verts[(idx + 1) % len(verts)].co - vert.co
                if vec.length_squared > 1e-12:
                    edge_vectors.append(vec.copy())
    else:
        mesh = source_obj.data
        for vert in mesh.vertices:
            idx = int(vert.index)
            if idx in indices:
                local_points_by_index[idx] = vert.co.copy()

        for edge in mesh.edges:
            a, b = (int(edge.vertices[0]), int(edge.vertices[1]))
            if a in indices and b in indices and a in local_points_by_index and b in local_points_by_index:
                vec = local_points_by_index[b] - local_points_by_index[a]
                if vec.length_squared > 1e-12:
                    edge_vectors.append(vec.copy())

        for poly in mesh.polygons:
            verts = [int(idx) for idx in poly.vertices]
            if not verts or not all(idx in indices and idx in local_points_by_index for idx in verts):
                continue
            material_index = int(getattr(poly, "material_index", 0) or 0)
            material_counts[material_index] = material_counts.get(material_index, 0) + 1
            face_centers.append(sum((local_points_by_index[idx] for idx in verts), Vector((0.0, 0.0, 0.0))) / len(verts))
            if poly.normal.length_squared > 1e-12:
                face_normals.append(poly.normal.copy())
            for idx, vert_idx in enumerate(verts):
                vec = local_points_by_index[verts[(idx + 1) % len(verts)]] - local_points_by_index[vert_idx]
                if vec.length_squared > 1e-12:
                    edge_vectors.append(vec.copy())

    local_points = [local_points_by_index[idx] for idx in sorted(local_points_by_index.keys())]
    data = _collider_exp_data_from_local_points_exp(
        source_obj,
        local_points,
        face_centers=face_centers,
        material_counts=material_counts,
    )
    data["edge_vectors_local"] = [vec.copy() for vec in edge_vectors]
    data["face_normals_local"] = [normal.copy() for normal in face_normals]
    data["cylinder_axis_profile"] = _inferred_cylinder_axis_profile_exp(data)
    return data


def _collect_collider_exp_scope_input_data_exp(context, settings, *, bounds_only=False):
    mode = str(getattr(settings, "collider_scope", "FROM_SELECTED") or "FROM_SELECTED")
    if mode == "FROM_SELECTED":
        source_obj = _resolve_collider_exp_guide_creation_source_exp(context, settings)
        if source_obj is None:
            raise RuntimeError("Source Object must be a mesh")
        return [_collect_collider_exp_input_data_exp(context, source_obj, bounds_only=bounds_only)]

    sources = _collider_exp_selected_source_objects_exp(context, settings)
    if not sources:
        raise RuntimeError("Select at least one mesh source object")

    data_items = []
    if mode == "PER_OBJECTS":
        for source_obj in sources:
            data_items.append(_collider_exp_all_object_data_exp(source_obj))
    elif mode == "PER_SHELLS":
        for source_obj in sources:
            selected = _collider_exp_selected_vertex_indices_exp(source_obj)
            components = _collider_exp_connected_component_indices_exp(source_obj, selected if selected else None)
            for component_indices in components:
                data_items.append(_collider_exp_component_data_from_indices_exp(source_obj, component_indices))
    elif mode == "PER_OBJECT_COMPONENTS":
        for source_obj in sources:
            selected = _collider_exp_selected_vertex_indices_exp(source_obj)
            components = _collider_exp_connected_component_indices_exp(
                source_obj,
                selected if selected else None,
                selected_only=bool(selected),
            )
            for component_indices in components:
                data_items.append(_collider_exp_component_data_from_indices_exp(source_obj, component_indices))
    else:
        source_obj = _resolve_collider_exp_guide_creation_source_exp(context, settings)
        if source_obj is None:
            raise RuntimeError("Source Object must be a mesh")
        data_items.append(_collect_collider_exp_input_data_exp(context, source_obj, bounds_only=bounds_only))

    if not data_items:
        raise RuntimeError("No usable geometry found for the selected create mode")
    return data_items


def _prepare_collider_exp_scope_build_exp(context, settings, op, *, bounds_only=False):
    from .nh_collider import (_allow_collider_exp_in_place_target_exp)
    data_items = _collect_collider_exp_scope_input_data_exp(context, settings, bounds_only=bounds_only)
    source_obj = data_items[0]["source_obj"]
    target_obj = _ensure_collider_exp_target_object_exp(context, settings, source_obj, op=op)
    lod_token = str(getattr(op, "target_lod", getattr(settings, "target_lod", "6")) or "6")
    for data in data_items:
        data_source = data.get("source_obj")
        if target_obj == data_source and not _allow_collider_exp_in_place_target_exp(data_source, lod_token):
            raise RuntimeError("Target Geometry LOD must be separate from the Source Object")
    return target_obj, source_obj, data_items


def _prepare_collider_exp_direct_boxes_build_exp(context, settings, op, *, bounds_only=False):
    from .nh_collider import (_allow_collider_exp_in_place_target_exp)
    mode = str(getattr(settings, "collider_scope", "FROM_SELECTED") or "FROM_SELECTED")
    if mode == "FROM_SELECTED":
        sources = _collider_exp_selected_source_objects_exp(context, settings)
        if not sources:
            raise RuntimeError("Select a source mesh or set Source Object")
        source_obj = sources[0]
        data_items = [_collect_collider_exp_input_data_exp(context, source_obj, bounds_only=bounds_only)]
    else:
        data_items = _collect_collider_exp_scope_input_data_exp(context, settings, bounds_only=bounds_only)
        source_obj = data_items[0]["source_obj"]

    target_obj = _ensure_collider_exp_target_object_exp(context, settings, source_obj, op=op)
    lod_token = str(getattr(op, "target_lod", getattr(settings, "target_lod", "6")) or "6")
    for data in data_items:
        data_source = data.get("source_obj")
        if target_obj == data_source and not _allow_collider_exp_in_place_target_exp(data_source, lod_token):
            raise RuntimeError("Target Geometry LOD must be separate from the Source Object")
    return target_obj, source_obj, data_items


def _collider_exp_empty_stats_exp():
    return {
        "verts_added": 0,
        "faces_added": 0,
        "vertex_indices": [],
        "face_indices": [],
        "triangles": 0,
        "actual_detail": 0,
        "max_triangles": 0,
    }


def _merge_collider_exp_stats_exp(total, stats):
    total["verts_added"] += int(stats.get("verts_added", 0))
    total["faces_added"] += int(stats.get("faces_added", 0))
    total["vertex_indices"].extend(stats.get("vertex_indices", []) or [])
    total["face_indices"].extend(stats.get("face_indices", []) or [])
    total["triangles"] += int(stats.get("triangles", 0))
    total["actual_detail"] = max(int(total.get("actual_detail", 0)), int(stats.get("actual_detail", 0)))
    total["max_triangles"] = max(int(total.get("max_triangles", 0)), int(stats.get("max_triangles", 0)))
    return total


def _expanded_local_points_for_minimum_exp(local_points, minimum_size):
    minimum_size = max(float(minimum_size), 0.0)
    if minimum_size <= 0.0:
        return [point.copy() for point in local_points]

    min_v, max_v = _bounds_from_points_exp(local_points)
    center = (min_v + max_v) * 0.5
    deficient_axes = [
        axis for axis in range(3)
        if abs(max_v[axis] - min_v[axis]) < minimum_size
    ]
    if not deficient_axes:
        return [point.copy() for point in local_points]

    half = minimum_size * 0.5
    expanded = []
    for point in local_points:
        variants = [point.copy()]
        for axis in deficient_axes:
            next_variants = []
            for variant in variants:
                low = variant.copy()
                high = variant.copy()
                low[axis] = center[axis] - half
                high[axis] = center[axis] + half
                next_variants.extend((low, high))
            variants = next_variants
        expanded.extend(variants)
    return expanded


def _transform_collider_exp_local_points_exp(data, op):
    local_points = _expanded_local_points_for_minimum_exp(
        data["local_points"],
        float(getattr(op, "minimum_size", 0.0)),
    )
    min_v, max_v = _bounds_from_points_exp(local_points)
    center = (min_v + max_v) * 0.5
    scale_vec = _collider_exp_scale_vec_exp(op)
    offset_vec = _collider_exp_vec_from_props_exp(op, "offset")
    matrix_world = data["matrix_world"]

    world_points = []
    for point in local_points:
        relative = point - center
        scaled = Vector((
            relative.x * scale_vec.x,
            relative.y * scale_vec.y,
            relative.z * scale_vec.z,
        ))
        world_points.append(matrix_world @ (center + scaled + offset_vec))

    if bool(getattr(op, "floor_contact", False)) and world_points:
        current_floor_z = min(point.z for point in world_points)
        delta_z = float(data.get("world_floor_z", current_floor_z)) - current_floor_z
        world_points = [point + Vector((0.0, 0.0, delta_z)) for point in world_points]

    return world_points


def _collider_exp_data_world_points_exp(data):
    points = data.get("world_points") or []
    if points:
        return [point.copy() for point in points]
    matrix_world = data["matrix_world"]
    return [matrix_world @ point for point in data.get("local_points", [])]


def _collider_exp_safe_normalized_exp(vec):
    if vec is None:
        return None
    try:
        if vec.length_squared <= 1e-12:
            return None
        out = vec.copy()
        out.normalize()
        if all(math.isfinite(float(out[axis])) for axis in range(3)):
            return out
    except Exception:
        return None
    return None


def _collider_exp_orthogonal_axis_exp(axis):
    axis = _collider_exp_safe_normalized_exp(axis) or Vector((0.0, 0.0, 1.0))
    candidates = (
        Vector((1.0, 0.0, 0.0)),
        Vector((0.0, 1.0, 0.0)),
        Vector((0.0, 0.0, 1.0)),
    )
    seed = min(candidates, key=lambda candidate: abs(axis.dot(candidate)))
    ortho = seed - axis * seed.dot(axis)
    return _collider_exp_safe_normalized_exp(ortho) or Vector((1.0, 0.0, 0.0))


def _collider_exp_matrix_basis_world_exp(matrix_world):
    mat3 = matrix_world.to_3x3()
    axis_x = _collider_exp_safe_normalized_exp(mat3 @ Vector((1.0, 0.0, 0.0)))
    if axis_x is None:
        axis_x = Vector((1.0, 0.0, 0.0))

    raw_y = mat3 @ Vector((0.0, 1.0, 0.0))
    axis_y = _collider_exp_safe_normalized_exp(raw_y - axis_x * raw_y.dot(axis_x))
    if axis_y is None:
        axis_y = _collider_exp_orthogonal_axis_exp(axis_x)

    axis_z = _collider_exp_safe_normalized_exp(axis_x.cross(axis_y))
    if axis_z is None:
        axis_z = Vector((0.0, 0.0, 1.0))
        axis_y = _collider_exp_safe_normalized_exp(axis_z.cross(axis_x)) or axis_y

    raw_z = _collider_exp_safe_normalized_exp(mat3 @ Vector((0.0, 0.0, 1.0)))
    if raw_z is not None and axis_z.dot(raw_z) < 0.0:
        axis_y = -axis_y
        axis_z = -axis_z

    return axis_x, axis_y, axis_z


def _collider_exp_world_face_normal_from_data_exp(data):
    matrix_world = data["matrix_world"]
    try:
        normal_matrix = matrix_world.to_3x3().inverted_safe().transposed()
    except Exception:
        normal_matrix = matrix_world.to_3x3()

    normal = None
    for local_normal in data.get("face_normals_local", []) or []:
        world_normal = _collider_exp_safe_normalized_exp(normal_matrix @ local_normal)
        if world_normal is None:
            continue
        if normal is None:
            normal = world_normal.copy()
        elif normal.dot(world_normal) < 0.0:
            normal -= world_normal
        else:
            normal += world_normal

    normal = _collider_exp_safe_normalized_exp(normal)
    if normal is not None:
        return normal
    return None


def _collider_exp_world_normal_from_data_exp(data, world_points):
    from .nh_collider import (_estimate_world_points_normal)
    normal = _collider_exp_world_face_normal_from_data_exp(data)
    if normal is not None:
        return normal
    return _estimate_world_points_normal(world_points)


def _collider_exp_project_axis_on_plane_exp(axis, normal):
    projected = axis - normal * axis.dot(normal)
    return _collider_exp_safe_normalized_exp(projected)


def _collider_exp_add_parallel_unique_axis_exp(axes, axis):
    axis = _collider_exp_safe_normalized_exp(axis)
    if axis is None:
        return
    for existing in axes:
        if abs(existing.dot(axis)) > 0.999:
            return
    axes.append(axis)


def _collider_exp_plane_basis_exp(normal, fallback_basis):
    normal = _collider_exp_safe_normalized_exp(normal) or Vector((0.0, 0.0, 1.0))
    axis_u = None
    for fallback_axis in fallback_basis:
        axis_u = _collider_exp_project_axis_on_plane_exp(fallback_axis, normal)
        if axis_u is not None:
            break
    if axis_u is None:
        axis_u = _collider_exp_orthogonal_axis_exp(normal)
    axis_v = _collider_exp_safe_normalized_exp(normal.cross(axis_u))
    if axis_v is None:
        axis_v = _collider_exp_orthogonal_axis_exp(axis_u)
    return axis_u, axis_v


def _collider_exp_projected_unique_2d_points_exp(world_points, axis_u, axis_v, tolerance=1e-6):
    scale = 1.0 / max(float(tolerance), 1e-12)
    seen = set()
    points = []
    for point in world_points:
        u = float(point.dot(axis_u))
        v = float(point.dot(axis_v))
        key = (round(u * scale), round(v * scale))
        if key in seen:
            continue
        seen.add(key)
        points.append((u, v))
    points.sort()
    return points


def _collider_exp_cross_2d_exp(origin, a, b):
    return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])


def _collider_exp_convex_hull_2d_exp(points):
    if len(points) <= 1:
        return list(points)

    lower = []
    for point in points:
        while len(lower) >= 2 and _collider_exp_cross_2d_exp(lower[-2], lower[-1], point) <= 1e-12:
            lower.pop()
        lower.append(point)

    upper = []
    for point in reversed(points):
        while len(upper) >= 2 and _collider_exp_cross_2d_exp(upper[-2], upper[-1], point) <= 1e-12:
            upper.pop()
        upper.append(point)

    hull = lower[:-1] + upper[:-1]
    return hull if hull else list(points)


def _collider_exp_axis_from_2d_delta_exp(axis_u, axis_v, delta_u, delta_v):
    return _collider_exp_safe_normalized_exp(axis_u * float(delta_u) + axis_v * float(delta_v))


def _collider_exp_best_planar_box_axis_exp(data, world_points, normal, fallback_basis):
    del data
    basis_u, basis_v = _collider_exp_plane_basis_exp(normal, fallback_basis)
    points_2d = _collider_exp_projected_unique_2d_points_exp(world_points, basis_u, basis_v)
    if len(points_2d) <= 1:
        return basis_u

    hull = _collider_exp_convex_hull_2d_exp(points_2d)
    axes = []
    if len(hull) == 2:
        axis = _collider_exp_axis_from_2d_delta_exp(
            basis_u,
            basis_v,
            hull[1][0] - hull[0][0],
            hull[1][1] - hull[0][1],
        )
        _collider_exp_add_parallel_unique_axis_exp(axes, axis)
    else:
        for idx, point in enumerate(hull):
            nxt = hull[(idx + 1) % len(hull)]
            axis = _collider_exp_axis_from_2d_delta_exp(
                basis_u,
                basis_v,
                nxt[0] - point[0],
                nxt[1] - point[1],
            )
            _collider_exp_add_parallel_unique_axis_exp(axes, axis)

    best_axis = None
    best_score = None
    for axis_u in axes:
        axis_v = _collider_exp_safe_normalized_exp(normal.cross(axis_u))
        if axis_v is None:
            continue
        u_values = [point.dot(axis_u) for point in world_points]
        v_values = [point.dot(axis_v) for point in world_points]
        span_u = max(u_values) - min(u_values)
        span_v = max(v_values) - min(v_values)
        score = (span_u * span_v, span_u + span_v)
        if best_score is None or score < best_score:
            best_score = score
            best_axis = axis_u

    if best_axis is not None:
        return best_axis
    return _collider_exp_orthogonal_axis_exp(normal)


def _collider_exp_box_axis_candidates_from_data_exp(data, fallback_basis, max_axes=48):
    axes = []
    mat3 = data["matrix_world"].to_3x3()

    edge_vectors = []
    for local_edge in data.get("edge_vectors_local", []) or []:
        try:
            length_sq = float(local_edge.length_squared)
        except Exception:
            length_sq = 0.0
        if length_sq > 1e-12:
            edge_vectors.append((length_sq, local_edge))

    for _length_sq, local_edge in sorted(edge_vectors, key=lambda item: item[0], reverse=True):
        _collider_exp_add_parallel_unique_axis_exp(axes, mat3 @ local_edge)
        if len(axes) >= max_axes:
            break

    try:
        normal_matrix = mat3.inverted_safe().transposed()
    except Exception:
        normal_matrix = mat3
    for local_normal in data.get("face_normals_local", []) or []:
        _collider_exp_add_parallel_unique_axis_exp(axes, normal_matrix @ local_normal)
        if len(axes) >= max_axes:
            break

    for fallback_axis in fallback_basis:
        _collider_exp_add_parallel_unique_axis_exp(axes, fallback_axis)

    return axes


def _collider_exp_box_basis_score_exp(world_points, axis_u, axis_v, axis_w):
    u_values = [point.dot(axis_u) for point in world_points]
    v_values = [point.dot(axis_v) for point in world_points]
    w_values = [point.dot(axis_w) for point in world_points]
    span_u = max(u_values) - min(u_values)
    span_v = max(v_values) - min(v_values)
    span_w = max(w_values) - min(w_values)
    volume = max(span_u, 0.0) * max(span_v, 0.0) * max(span_w, 0.0)
    surface = (
        max(span_u, 0.0) * max(span_v, 0.0)
        + max(span_u, 0.0) * max(span_w, 0.0)
        + max(span_v, 0.0) * max(span_w, 0.0)
    )
    longest = max(span_u, span_v, span_w)
    return volume, surface, longest


def _collider_exp_best_oriented_box_frame_exp(data, world_points, fallback_basis):
    axes = _collider_exp_box_axis_candidates_from_data_exp(data, fallback_basis)
    if len(axes) < 2:
        return fallback_basis

    best_basis = None
    best_score = None
    for idx, axis_u in enumerate(axes):
        axis_u = _collider_exp_safe_normalized_exp(axis_u)
        if axis_u is None:
            continue
        for axis_candidate in axes[idx + 1:]:
            axis_v = axis_candidate - axis_u * axis_candidate.dot(axis_u)
            axis_v = _collider_exp_safe_normalized_exp(axis_v)
            if axis_v is None:
                continue
            axis_w = _collider_exp_safe_normalized_exp(axis_u.cross(axis_v))
            if axis_w is None:
                continue
            score = _collider_exp_box_basis_score_exp(world_points, axis_u, axis_v, axis_w)
            if best_score is None or score < best_score:
                best_score = score
                best_basis = (axis_u, axis_v, axis_w)

    return best_basis or fallback_basis


def _collider_exp_box_frame_from_data_exp(data):
    from .nh_collider import (_dedupe_world_points, _points_are_flat)
    world_points = _dedupe_world_points(_collider_exp_data_world_points_exp(data))
    if not world_points:
        raise RuntimeError("No source points available to build a box")

    axis_x, axis_y, axis_z = _collider_exp_matrix_basis_world_exp(data["matrix_world"])
    if len(world_points) == 1:
        return world_points, axis_x, axis_y, axis_z

    if len(world_points) == 2:
        axis_u = _collider_exp_safe_normalized_exp(world_points[1] - world_points[0])
        axis_v = None
        if axis_u is not None:
            for fallback_axis in (axis_x, axis_y, axis_z):
                axis_v = _collider_exp_safe_normalized_exp(
                    fallback_axis - axis_u * fallback_axis.dot(axis_u)
                )
                if axis_v is not None:
                    break
        if axis_u is not None and axis_v is not None:
            axis_w = _collider_exp_safe_normalized_exp(axis_u.cross(axis_v))
            if axis_w is not None:
                return world_points, axis_u, axis_v, axis_w

    normal = _collider_exp_world_normal_from_data_exp(data, world_points)
    if normal is not None:
        min_w, max_w = _bounds_from_points_exp(world_points)
        flat_epsilon = max(1e-5, (max_w - min_w).length * 1e-5)
        if len(world_points) <= 3 or _points_are_flat(world_points, normal, epsilon=flat_epsilon):
            axis_w = normal
            axis_u = _collider_exp_best_planar_box_axis_exp(
                data,
                world_points,
                axis_w,
                (axis_x, axis_y, axis_z),
            )
            axis_v = _collider_exp_safe_normalized_exp(axis_w.cross(axis_u))
            if axis_v is not None:
                return world_points, axis_u, axis_v, axis_w

    axis_u, axis_v, axis_w = _collider_exp_best_oriented_box_frame_exp(
        data,
        world_points,
        (axis_x, axis_y, axis_z),
    )
    return world_points, axis_u, axis_v, axis_w


def _collider_exp_box_axis_center_half_exp(
    min_value,
    max_value,
    axis,
    source_normal,
    minimum_size,
    scale,
    use_normal_minimum_size,
):
    span = max(float(max_value) - float(min_value), 0.0)
    half = max(span, float(minimum_size)) * float(scale) * 0.5
    center = (float(min_value) + float(max_value)) * 0.5
    if not bool(use_normal_minimum_size) or span >= float(minimum_size):
        return center, half

    axis = _collider_exp_safe_normalized_exp(axis)
    source_normal = _collider_exp_safe_normalized_exp(source_normal)
    if axis is None or source_normal is None:
        return center, half

    normal_alignment = float(axis.dot(source_normal))
    if abs(normal_alignment) < 0.75:
        return center, half

    if normal_alignment >= 0.0:
        center = float(max_value) - half
    else:
        center = float(min_value) + half
    return center, half


def _box_vertices_from_bounds_data_exp(data, op):
    world_points, axis_u, axis_v, axis_w = _collider_exp_box_frame_from_data_exp(data)
    u_values = [point.dot(axis_u) for point in world_points]
    v_values = [point.dot(axis_v) for point in world_points]
    w_values = [point.dot(axis_w) for point in world_points]
    min_u, max_u = min(u_values), max(u_values)
    min_v, max_v = min(v_values), max(v_values)
    min_w, max_w = min(w_values), max(w_values)

    minimum_size = max(float(getattr(op, "minimum_size", 0.0)), 1e-6)
    scale_vec = _collider_exp_scale_vec_exp(op)
    offset_vec = _collider_exp_vec_from_props_exp(op, "offset")
    source_normal = (
        _collider_exp_world_face_normal_from_data_exp(data)
        if bool(getattr(op, "normal_minimum_size", False))
        else None
    )
    use_normal_minimum_size = source_normal is not None
    center_u, half_u = _collider_exp_box_axis_center_half_exp(
        min_u,
        max_u,
        axis_u,
        source_normal,
        minimum_size,
        scale_vec.x,
        use_normal_minimum_size,
    )
    center_v, half_v = _collider_exp_box_axis_center_half_exp(
        min_v,
        max_v,
        axis_v,
        source_normal,
        minimum_size,
        scale_vec.y,
        use_normal_minimum_size,
    )
    center_w, half_w = _collider_exp_box_axis_center_half_exp(
        min_w,
        max_w,
        axis_w,
        source_normal,
        minimum_size,
        scale_vec.z,
        use_normal_minimum_size,
    )
    half = Vector((half_u, half_v, half_w))

    center = (
        axis_u * center_u
        + axis_v * center_v
        + axis_w * center_w
        + axis_u * offset_vec.x
        + axis_v * offset_vec.y
        + axis_w * offset_vec.z
    )
    world_verts = [
        center - axis_u * half.x - axis_v * half.y - axis_w * half.z,
        center + axis_u * half.x - axis_v * half.y - axis_w * half.z,
        center + axis_u * half.x + axis_v * half.y - axis_w * half.z,
        center - axis_u * half.x + axis_v * half.y - axis_w * half.z,
        center - axis_u * half.x - axis_v * half.y + axis_w * half.z,
        center + axis_u * half.x - axis_v * half.y + axis_w * half.z,
        center + axis_u * half.x + axis_v * half.y + axis_w * half.z,
        center - axis_u * half.x + axis_v * half.y + axis_w * half.z,
    ]
    if bool(getattr(op, "floor_contact", False)) and world_verts:
        current_floor_z = min(point.z for point in world_verts)
        delta_z = float(data.get("world_floor_z", current_floor_z)) - current_floor_z
        world_verts = [point + Vector((0.0, 0.0, delta_z)) for point in world_verts]
    return world_verts


def _append_collider_exp_mesh_to_bmesh_exp(
    bm,
    target_obj,
    world_vertices,
    faces,
    *,
    merge_distance=0.0,
    recalc_normals=True,
    material_index=None,
):
    before_vert_count = len(bm.verts)
    before_face_count = len(bm.faces)
    to_local = target_obj.matrix_world.inverted_safe()

    new_verts = [bm.verts.new(to_local @ point) for point in world_vertices]
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    if merge_distance > 0.0 and new_verts:
        bmesh.ops.remove_doubles(bm, verts=new_verts, dist=merge_distance)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        new_verts = [vert for vert in new_verts if vert.is_valid]

    new_faces = []
    for face_indices in faces:
        face_verts = [
            new_verts[idx]
            for idx in face_indices
            if 0 <= idx < len(new_verts) and new_verts[idx].is_valid
        ]
        if len(face_verts) < 3 or len(set(face_verts)) < 3:
            continue
        try:
            face = bm.faces.new(face_verts)
        except ValueError:
            continue
        if material_index is not None:
            try:
                face.material_index = max(0, int(material_index))
            except Exception:
                pass
        new_faces.append(face)

    if not new_faces:
        rollback_verts = [vert for vert in new_verts if vert.is_valid]
        if rollback_verts:
            bmesh.ops.delete(bm, geom=rollback_verts, context="VERTS")
        raise RuntimeError("Could not append collider faces")

    if recalc_normals:
        bmesh.ops.recalc_face_normals(bm, faces=new_faces)

    bm.normal_update()
    bm.verts.index_update()
    bm.faces.index_update()
    return {
        "verts_added": len(bm.verts) - before_vert_count,
        "faces_added": len(bm.faces) - before_face_count,
        "vertex_indices": [vert.index for vert in new_verts if vert.is_valid],
        "face_indices": [face.index for face in new_faces if face.is_valid],
    }


def _append_collider_exp_mesh_to_object_exp(
    target_obj,
    world_vertices,
    faces,
    *,
    merge_distance=0.0,
    recalc_normals=True,
    material_index=None,
):
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target Geometry LOD object must be a mesh")
    if not world_vertices or not faces:
        raise RuntimeError("No collider geometry to append")

    mesh = target_obj.data
    if target_obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(mesh)
        try:
            stats = _append_collider_exp_mesh_to_bmesh_exp(
                bm,
                target_obj,
                world_vertices,
                faces,
                merge_distance=merge_distance,
                recalc_normals=recalc_normals,
                material_index=material_index,
            )
        except Exception:
            bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
            raise
        bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
        try:
            mesh.update(calc_edges=True)
        except Exception:
            pass
        return stats

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        stats = _append_collider_exp_mesh_to_bmesh_exp(
            bm,
            target_obj,
            world_vertices,
            faces,
            merge_distance=merge_distance,
            recalc_normals=recalc_normals,
            material_index=material_index,
        )
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True)
        return stats
    finally:
        bm.free()


def _matrix_to_list_exp(matrix):
    return [[float(matrix[row][col]) for col in range(4)] for row in range(4)]


def _matrix_from_list_exp(rows):
    if not isinstance(rows, list) or len(rows) != 4:
        raise RuntimeError("Stored matrix is invalid")
    return Matrix(rows)


def _points_to_list_exp(points):
    return [[float(point.x), float(point.y), float(point.z)] for point in points]


def _points_from_list_exp(items):
    if not isinstance(items, list):
        raise RuntimeError("Stored point list is invalid")
    return [Vector((float(item[0]), float(item[1]), float(item[2]))) for item in items if len(item) >= 3]


def _collider_exp_params_json_exp(params):
    try:
        return json.dumps(params, ensure_ascii=False, sort_keys=True)
    except Exception:
        return "{}"


def _set_collider_exp_current_props_exp(target_obj, exp_type, source_name, params):
    from .nh_collider import (_COLLIDER_EXP_PARAMS_PROP, _COLLIDER_EXP_SOURCE_PROP, _COLLIDER_EXP_TYPE_PROP, _COLLIDER_EXP_UUID_PROP)
    if target_obj is None:
        return
    params = dict(params)
    exp_uuid = str(params.get("uuid") or uuid.uuid4().hex)
    params["uuid"] = exp_uuid

    target_obj[_COLLIDER_EXP_TYPE_PROP] = str(exp_type)
    target_obj[_COLLIDER_EXP_SOURCE_PROP] = str(source_name or "")
    target_obj[_COLLIDER_EXP_UUID_PROP] = exp_uuid
    target_obj[_COLLIDER_EXP_PARAMS_PROP] = _collider_exp_params_json_exp(params)


def _coerce_collider_exp_history_entry_exp(entry):
    if not isinstance(entry, dict):
        return None

    raw_params = entry.get("params")
    if isinstance(raw_params, dict):
        params = dict(raw_params)
        exp_type = str(entry.get("exp_type", "") or "")
        source_name = str(entry.get("source_name", "") or "")
        exp_uuid = str(entry.get("uuid", "") or params.get("uuid", "") or "")
    else:
        params = dict(entry)
        exp_type = str(params.pop("__exp_type", params.pop("exp_type", "")) or "")
        source_name = str(params.pop("__source_name", params.pop("source_name", "")) or "")
        exp_uuid = str(params.get("uuid", "") or "")

    if not params.get("vertex_indices"):
        return None
    if not exp_uuid:
        exp_uuid = uuid.uuid4().hex
    params["uuid"] = exp_uuid
    return {
        "uuid": exp_uuid,
        "exp_type": exp_type,
        "source_name": source_name,
        "params": params,
    }


def _get_collider_exp_history_entries_exp(target_obj):
    from .nh_collider import (_COLLIDER_EXP_HISTORY_LIMIT, _COLLIDER_EXP_HISTORY_PROP)
    if target_obj is None:
        return []
    try:
        raw = target_obj.get(_COLLIDER_EXP_HISTORY_PROP, "")
    except Exception:
        raw = ""
    if not raw:
        return []

    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception:
            return []
    elif isinstance(raw, (list, tuple)):
        data = list(raw)
    else:
        return []

    if isinstance(data, dict):
        data = data.get("items", [])
    if not isinstance(data, list):
        return []

    entries = []
    for item in data:
        entry = _coerce_collider_exp_history_entry_exp(item)
        if entry is not None:
            entries.append(entry)
    return entries[-_COLLIDER_EXP_HISTORY_LIMIT:]


def _write_collider_exp_history_entries_exp(target_obj, entries):
    from .nh_collider import (_COLLIDER_EXP_HISTORY_LIMIT, _COLLIDER_EXP_HISTORY_PROP)
    if target_obj is None:
        return []

    history = []
    for item in entries or []:
        entry = _coerce_collider_exp_history_entry_exp(item)
        if entry is not None:
            history.append(entry)
    history = history[-_COLLIDER_EXP_HISTORY_LIMIT:]

    if not history:
        _clear_collider_exp_history_exp(target_obj)
        return []

    try:
        target_obj[_COLLIDER_EXP_HISTORY_PROP] = json.dumps(history, ensure_ascii=False, sort_keys=True)
    except Exception:
        pass
    return history


def _append_collider_exp_history_entry_exp(target_obj, exp_type, source_name, params):
    if target_obj is None:
        return []
    params = dict(params)
    if not params.get("vertex_indices"):
        return _get_collider_exp_history_entries_exp(target_obj)

    exp_uuid = str(params.get("uuid") or uuid.uuid4().hex)
    params["uuid"] = exp_uuid
    entry = {
        "uuid": exp_uuid,
        "exp_type": str(exp_type),
        "source_name": str(source_name or ""),
        "params": params,
    }
    history = [
        item
        for item in _get_collider_exp_history_entries_exp(target_obj)
        if str(item.get("uuid", "") or "") != exp_uuid
    ]
    history.append(entry)
    return _write_collider_exp_history_entries_exp(target_obj, history)


def _seed_collider_exp_history_from_current_exp(target_obj):
    from .nh_collider import (_COLLIDER_EXP_SOURCE_PROP, _COLLIDER_EXP_TYPE_PROP)
    if target_obj is None or _get_collider_exp_history_entries_exp(target_obj):
        return
    try:
        params = _get_collider_exp_custom_params_exp(target_obj)
    except Exception:
        return
    if not params.get("vertex_indices"):
        return
    try:
        exp_type = str(target_obj.get(_COLLIDER_EXP_TYPE_PROP, "") or "")
        source_name = str(target_obj.get(_COLLIDER_EXP_SOURCE_PROP, "") or "")
    except Exception:
        exp_type = ""
        source_name = ""
    _append_collider_exp_history_entry_exp(target_obj, exp_type, source_name, params)


def _apply_collider_exp_current_from_history_exp(target_obj, history=None):
    if target_obj is None:
        return None
    if history is None:
        history = _get_collider_exp_history_entries_exp(target_obj)
    else:
        history = _write_collider_exp_history_entries_exp(target_obj, history)

    if not history:
        _clear_last_collider_exp_params_exp(target_obj)
        return None

    entry = history[-1]
    params = dict(entry.get("params", {}) or {})
    _set_collider_exp_current_props_exp(
        target_obj,
        entry.get("exp_type", ""),
        entry.get("source_name", ""),
        params,
    )
    return params


def _pop_last_collider_exp_history_entry_exp(target_obj):
    history = _get_collider_exp_history_entries_exp(target_obj)
    if history:
        history = history[:-1]
        _apply_collider_exp_current_from_history_exp(target_obj, history)
        return len(history)

    _clear_last_collider_exp_params_exp(target_obj)
    return 0


def _clear_collider_exp_history_exp(target_obj):
    from .nh_collider import (_COLLIDER_EXP_HISTORY_PROP)
    if target_obj is None:
        return
    try:
        if _COLLIDER_EXP_HISTORY_PROP in target_obj:
            del target_obj[_COLLIDER_EXP_HISTORY_PROP]
    except Exception:
        pass


def _set_collider_exp_custom_props_exp(target_obj, exp_type, source_obj, params):
    source_name = getattr(source_obj, "name", "") or ""
    params = dict(params)
    exp_uuid = str(params.get("uuid") or uuid.uuid4().hex)
    params["uuid"] = exp_uuid

    _seed_collider_exp_history_from_current_exp(target_obj)
    _set_collider_exp_current_props_exp(target_obj, exp_type, source_name, params)
    _append_collider_exp_history_entry_exp(target_obj, exp_type, source_name, params)


def _is_live_blender_object_exp(obj):
    if obj is None:
        return False
    try:
        obj_ptr = obj.as_pointer()
        name = obj.name
    except (ReferenceError, RuntimeError):
        return False
    except Exception:
        return False
    if not name or not obj_ptr:
        return False
    try:
        live_obj = bpy.data.objects.get(name)
        return live_obj is not None and live_obj.as_pointer() == obj_ptr
    except (ReferenceError, RuntimeError):
        return False
    except Exception:
        return False


def _is_collider_exp_convex_hull_object_exp(obj):
    from .nh_collider import (_COLLIDER_EXP_TYPE_PROP)
    if not _is_live_blender_object_exp(obj):
        return False
    if getattr(obj, "type", None) != "MESH":
        return False
    try:
        return str(obj.get(_COLLIDER_EXP_TYPE_PROP, "") or "") == "CONVEX_HULL"
    except Exception:
        return False


def _resolve_collider_exp_convex_hull_target_exp(context, settings):
    active = getattr(getattr(context, "view_layer", None), "objects", None)
    active_obj = getattr(active, "active", None) if active is not None else None
    if _is_collider_exp_convex_hull_object_exp(active_obj):
        return active_obj, "active"

    target_obj = None
    if settings is not None:
        try:
            target_obj = getattr(settings, "geometry_object", None)
        except ReferenceError:
            target_obj = None
        except Exception:
            target_obj = None
    if _is_collider_exp_convex_hull_object_exp(target_obj):
        return target_obj, "last"

    return None, ""


def _get_collider_exp_custom_params_exp(target_obj):
    from .nh_base import (_fmt_exc)
    from .nh_collider import (_COLLIDER_EXP_PARAMS_PROP)
    if target_obj is None:
        raise RuntimeError("Target LOD Object is missing")
    try:
        raw = target_obj.get(_COLLIDER_EXP_PARAMS_PROP, "")
    except Exception:
        raw = ""
    if not raw:
        raise RuntimeError("No experimental collider data found on the target LOD object")
    try:
        params = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"Experimental collider data is invalid: {_fmt_exc(e)}")
    if not isinstance(params, dict):
        raise RuntimeError("Experimental collider data is invalid")
    return params


def _get_collider_exp_hull_rebuild_data_exp(target_obj):
    try:
        params = _get_collider_exp_custom_params_exp(target_obj)
    except Exception:
        params = {}

    matrix_rows = params.get("matrix_world")
    local_items = params.get("local_points", [])
    if matrix_rows and local_items:
        try:
            matrix_world = _matrix_from_list_exp(matrix_rows)
            local_points = _points_from_list_exp(local_items)
            if len(local_points) >= 4:
                return params, matrix_world, local_points, False
        except Exception:
            pass

    if not _is_collider_exp_convex_hull_object_exp(target_obj):
        raise RuntimeError("Selected object is not an experimental convex hull")
    if target_obj.data is None or len(target_obj.data.vertices) < 4:
        raise RuntimeError("Selected convex hull has fewer than 4 vertices")

    local_points = [vert.co.copy() for vert in target_obj.data.vertices]
    return params, target_obj.matrix_world.copy(), local_points, True


def _delete_collider_exp_vertices_exp(target_obj, vertex_indices):
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target Geometry LOD object must be a mesh")
    if target_obj.mode == "EDIT":
        raise RuntimeError("Target Geometry LOD must not be in Edit Mode")
    if not vertex_indices:
        return {"verts_removed": 0}

    mesh = target_obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        wanted = {int(idx) for idx in vertex_indices if isinstance(idx, int) or str(idx).isdigit()}
        verts = [bm.verts[idx] for idx in wanted if 0 <= idx < len(bm.verts) and bm.verts[idx].is_valid]
        if not verts:
            raise RuntimeError("Stored experimental collider vertices are no longer available")
        bmesh.ops.delete(bm, geom=verts, context="VERTS")
        bm.normal_update()
        removed = len(verts)
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True)
        return {"verts_removed": removed}
    finally:
        bm.free()


def _delete_collider_exp_vertices_any_mode_exp(target_obj, vertex_indices):
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target Geometry LOD object must be a mesh")
    if not vertex_indices:
        return {"verts_removed": 0}

    wanted = {int(idx) for idx in vertex_indices if isinstance(idx, int) or str(idx).isdigit()}
    if not wanted:
        return {"verts_removed": 0}

    if target_obj.mode == "EDIT":
        mesh = target_obj.data
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        verts = [bm.verts[idx] for idx in wanted if 0 <= idx < len(bm.verts) and bm.verts[idx].is_valid]
        if not verts:
            raise RuntimeError("Stored experimental collider vertices are no longer available")
        bmesh.ops.delete(bm, geom=verts, context="VERTS")
        bm.normal_update()
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=True)
        return {"verts_removed": len(verts)}

    return _delete_collider_exp_vertices_exp(target_obj, list(wanted))


def _clear_last_collider_exp_params_exp(target_obj):
    from .nh_collider import (_COLLIDER_EXP_PARAMS_PROP)
    if target_obj is None:
        return
    try:
        if _COLLIDER_EXP_PARAMS_PROP in target_obj:
            del target_obj[_COLLIDER_EXP_PARAMS_PROP]
    except Exception:
        pass


def _resolve_last_collider_exp_target_exp(context, settings=None):
    candidates = []
    active_obj = getattr(getattr(context, "view_layer", None), "objects", None)
    active_obj = getattr(active_obj, "active", None) if active_obj is not None else None
    edit_obj = getattr(context, "edit_object", None)
    for obj in (edit_obj, active_obj):
        if obj is not None and obj not in candidates:
            candidates.append(obj)

    try:
        target_obj = getattr(settings, "geometry_object", None) if settings is not None else None
    except ReferenceError:
        target_obj = None
    except Exception:
        target_obj = None
    if target_obj is not None and target_obj not in candidates:
        candidates.append(target_obj)

    for obj in getattr(context, "selected_objects", []) or []:
        if obj is not None and obj not in candidates:
            candidates.append(obj)

    for obj in candidates:
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        history = _get_collider_exp_history_entries_exp(obj)
        if history:
            params = dict(history[-1].get("params", {}) or {})
            if params.get("vertex_indices"):
                return obj, params
        try:
            params = _get_collider_exp_custom_params_exp(obj)
        except Exception:
            continue
        if params.get("vertex_indices"):
            return obj, params
    return None, {}


def _delete_all_collider_exp_vertices_exp(target_obj):
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target Geometry LOD object must be a mesh")
    if target_obj.mode == "EDIT":
        raise RuntimeError("Target Geometry LOD must not be in Edit Mode")
    mesh = target_obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        verts = [vert for vert in bm.verts if vert.is_valid]
        if verts:
            bmesh.ops.delete(bm, geom=verts, context="VERTS")
        bm.normal_update()
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True)
        return {"verts_removed": len(verts)}
    finally:
        bm.free()


def _simplify_collider_exp_points_exp(points, detail):
    from .nh_collider import (_dedupe_world_points, _vector_quantized_key)
    points = _dedupe_world_points(points)
    detail = max(4, min(int(detail), 128))
    max_points = max(8, detail * 4)
    if len(points) <= max_points:
        return points

    center = sum(points, Vector((0.0, 0.0, 0.0))) / len(points)
    directions = []
    for sx in (-1.0, 1.0):
        directions.append(Vector((sx, 0.0, 0.0)))
        directions.append(Vector((0.0, sx, 0.0)))
        directions.append(Vector((0.0, 0.0, sx)))
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                direction = Vector((sx, sy, sz))
                direction.normalize()
                directions.append(direction)

    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    for idx in range(detail * 2):
        z = 1.0 - (2.0 * idx + 1.0) / max(detail * 2, 1)
        radius = max(0.0, 1.0 - z * z) ** 0.5
        theta = golden_angle * idx
        directions.append(Vector((math.cos(theta) * radius, math.sin(theta) * radius, z)))

    selected = []
    selected_keys = set()
    for direction in directions:
        best = None
        best_dot = None
        for point in points:
            dot = (point - center).dot(direction)
            if best is None or dot > best_dot:
                best = point
                best_dot = dot
        if best is None:
            continue
        key = _vector_quantized_key(best)
        if key in selected_keys:
            continue
        selected_keys.add(key)
        selected.append(best.copy())
        if len(selected) >= max_points:
            break

    if len(selected) < 4:
        radial = sorted(points, key=lambda point: (point - center).length_squared, reverse=True)
        for point in radial:
            key = _vector_quantized_key(point)
            if key in selected_keys:
                continue
            selected_keys.add(key)
            selected.append(point.copy())
            if len(selected) >= 4:
                break

    return selected


def _count_collider_exp_hull_triangles_exp(face_indices):
    return sum(max(0, len(face) - 2) for face in face_indices or [] if len(face) >= 3)


def _collider_exp_hull_detail_candidates_exp(detail, max_triangles):
    detail = max(4, min(int(detail), 128))
    if max_triangles <= 0:
        return (detail,)

    candidates = {detail, 4}
    step = 1 if detail <= 24 else 4
    candidates.update(range(detail, 3, -step))
    candidates.update((96, 64, 48, 32, 24, 16, 12, 8, 6))
    return tuple(sorted((value for value in candidates if 4 <= value <= detail), reverse=True))


def _build_collider_exp_hull_data_for_budget_exp(target_obj, world_points, op):
    from .nh_base import (_fmt_exc)
    from .nh_collider import (_build_clean_hull_data_from_local_points, _dedupe_world_points)
    unique_points = _dedupe_world_points(world_points)
    if len(unique_points) < 4:
        raise RuntimeError("Need at least 4 unique points to build a collider")

    detail = max(4, min(int(getattr(op, "convex_detail", 16)), 128))
    max_triangles = max(0, int(getattr(op, "convex_max_triangles", 0)))
    merge_distance = float(getattr(op, "merge_distance", 0.0))
    recalc_normals = bool(getattr(op, "recalc_normals", True))
    target_to_local = target_obj.matrix_world.inverted_safe()
    best = None
    last_error = None

    for candidate_detail in _collider_exp_hull_detail_candidates_exp(detail, max_triangles):
        hull_points = _simplify_collider_exp_points_exp(unique_points, candidate_detail)
        try:
            hull_data = _build_clean_hull_data_from_local_points(
                [target_to_local @ point for point in hull_points],
                merge_distance=merge_distance,
                recalc_normals=recalc_normals,
            )
        except Exception as e:
            last_error = e
            continue
        triangle_count = _count_collider_exp_hull_triangles_exp(hull_data.get("faces", []))
        current = {
            "hull_data": hull_data,
            "hull_points": hull_points,
            "actual_detail": candidate_detail,
            "triangles": triangle_count,
            "max_triangles": max_triangles,
        }
        if max_triangles <= 0:
            return current
        if triangle_count <= max_triangles:
            return current
        if best is None or triangle_count < best["triangles"]:
            best = current

    if best is None:
        if last_error is not None:
            raise RuntimeError(_fmt_exc(last_error))
        raise RuntimeError("Could not simplify convex hull")
    return best


def _append_collider_exp_hull_data_to_bmesh_exp(bm, hull_data, recalc_normals=True, material_index=None):
    before_vert_count = len(bm.verts)
    before_face_count = len(bm.faces)

    new_verts = [bm.verts.new(point.copy()) for point in hull_data.get("verts", [])]
    bm.verts.ensure_lookup_table()

    new_faces = []
    for face_indices in hull_data.get("faces", []):
        face_verts = [new_verts[idx] for idx in face_indices if 0 <= idx < len(new_verts)]
        if len(face_verts) < 3 or len(set(face_verts)) < 3:
            continue
        try:
            face = bm.faces.new(face_verts)
        except ValueError:
            continue
        if material_index is not None:
            try:
                face.material_index = max(0, int(material_index))
            except Exception:
                pass
        new_faces.append(face)

    if not new_faces:
        rollback_verts = [vert for vert in new_verts if vert.is_valid]
        if rollback_verts:
            bmesh.ops.delete(bm, geom=rollback_verts, context="VERTS")
        raise RuntimeError("Could not append simplified convex hull to the target mesh")

    if recalc_normals:
        bmesh.ops.recalc_face_normals(bm, faces=new_faces)

    bm.normal_update()
    bm.verts.index_update()
    bm.faces.index_update()
    return {
        "verts_added": len(bm.verts) - before_vert_count,
        "faces_added": len(bm.faces) - before_face_count,
        "vertex_indices": [vert.index for vert in new_verts if vert.is_valid],
        "face_indices": [face.index for face in new_faces if face.is_valid],
    }


def _append_collider_exp_hull_data_to_object_exp(target_obj, hull_data, recalc_normals=True, material_index=None):
    if target_obj is None or target_obj.type != "MESH":
        raise RuntimeError("Target Geometry LOD object must be a mesh")

    mesh = target_obj.data
    if target_obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(mesh)
        try:
            stats = _append_collider_exp_hull_data_to_bmesh_exp(
                bm,
                hull_data,
                recalc_normals=recalc_normals,
                material_index=material_index,
            )
        except Exception:
            bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
            raise
        bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
        try:
            mesh.update(calc_edges=True)
        except Exception:
            pass
        return stats

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        stats = _append_collider_exp_hull_data_to_bmesh_exp(
            bm,
            hull_data,
            recalc_normals=recalc_normals,
            material_index=material_index,
        )
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True)
        return stats
    finally:
        bm.free()


def _apply_collider_exp_hull_build_stats_exp(stats, build):
    stats["used_verts"] = len(build.get("hull_points", []))
    stats["actual_detail"] = int(build.get("actual_detail", 0))
    stats["triangles"] = int(build.get("triangles", 0))
    stats["max_triangles"] = int(build.get("max_triangles", 0))
    return stats


def _append_collider_exp_hull_to_object_exp(target_obj, world_points, op, material_index=None):
    build = _build_collider_exp_hull_data_for_budget_exp(target_obj, world_points, op)
    stats = _append_collider_exp_hull_data_to_object_exp(
        target_obj,
        build["hull_data"],
        recalc_normals=bool(getattr(op, "recalc_normals", True)),
        material_index=material_index,
    )
    return _apply_collider_exp_hull_build_stats_exp(stats, build)


def _build_collider_exp_hull_from_selected_loose_verts_in_place_exp(context, target_obj, op, material_index=None):
    from .nh_collider import (_finalize_convex_hull_geometry, _select_only_faces_in_bmesh, _vector_quantized_key)
    from .nh_snap import (_tag_redraw_all_areas)
    if target_obj is None or getattr(target_obj, "type", None) != "MESH":
        raise RuntimeError("Target Geometry LOD object must be a mesh")
    if getattr(target_obj, "mode", "") != "EDIT":
        raise RuntimeError("Target Geometry LOD must be active in Edit Mode")

    mesh = target_obj.data
    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    before_vert_count = len(bm.verts)
    before_face_count = len(bm.faces)
    merge_distance = float(getattr(op, "merge_distance", 0.0) or 0.0)
    recalc_normals = bool(getattr(op, "recalc_normals", True))

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

    unique_point_keys = {_vector_quantized_key(vert.co) for vert in loose_verts if vert.is_valid}
    if len(unique_point_keys) < 4:
        raise RuntimeError("Selected loose vertices collapse below 4 unique points")

    try:
        hull = bmesh.ops.convex_hull(bm, input=loose_verts, use_existing_faces=False)
        final_faces = _finalize_convex_hull_geometry(
            bm,
            hull,
            loose_verts,
            recalc_normals=recalc_normals,
        )
        if material_index is not None:
            for face in final_faces:
                if not face.is_valid:
                    continue
                try:
                    face.material_index = max(0, int(material_index))
                except Exception:
                    pass
        _select_only_faces_in_bmesh(bm, final_faces)
        bm.normal_update()
        bm.verts.index_update()
        bm.faces.index_update()
        vertex_indices = sorted({
            int(vert.index)
            for face in final_faces
            if face is not None and face.is_valid
            for vert in face.verts
            if vert.is_valid
        })
        face_indices = [int(face.index) for face in final_faces if face is not None and face.is_valid]
        triangle_count = sum(max(0, len(face.verts) - 2) for face in final_faces if face is not None and face.is_valid)
    except Exception:
        bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
        raise

    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
    try:
        mesh.update(calc_edges=True)
    except Exception:
        pass
    try:
        bpy.ops.mesh.select_mode(type="FACE")
    except Exception:
        pass
    _tag_redraw_all_areas(context)

    return {
        "verts_added": max(0, len(bm.verts) - before_vert_count),
        "faces_added": len(bm.faces) - before_face_count,
        "vertex_indices": vertex_indices,
        "face_indices": face_indices,
        "used_verts": len(unique_point_keys),
        "actual_detail": int(getattr(op, "convex_detail", len(unique_point_keys)) or len(unique_point_keys)),
        "triangles": triangle_count,
        "max_triangles": max(0, int(getattr(op, "convex_max_triangles", 0) or 0)),
    }


def _collider_exp_collection_path_names_exp(context, obj):
    from .nh_textures import (_find_collection_path)
    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    names = []
    for collection in getattr(obj, "users_collection", []):
        path = None
        if scene_root is not None:
            try:
                path = _find_collection_path(scene_root, collection.as_pointer())
            except Exception:
                path = None
        for item in path or [collection]:
            name = getattr(item, "name", "") or ""
            if name:
                names.append(name)
    return names


def _is_collider_exp_object_in_lod_collection_exp(context, obj):
    from .nh_scatter import (_COLLIDER_COLLECTION_ALIASES, _COLLIDER_COLLECTION_NAME, _MISC_COLLECTION_NAME)
    from .nh_snap import (_logical_collection_name, _logical_collection_names)
    names = _collider_exp_collection_path_names_exp(context, obj)
    logical_names = {_logical_collection_name(name) for name in names}
    accepted = _logical_collection_names(
        _COLLIDER_COLLECTION_NAME,
        _COLLIDER_COLLECTION_ALIASES,
        "Geometry",
        "View Geometry",
        "Fire Geometry",
        _MISC_COLLECTION_NAME,
        "Roadway",
    )
    return bool(logical_names.intersection(accepted))


def _is_collider_exp_validation_candidate_exp(context, obj):
    from .nh_collider import (_COLLIDER_EXP_TYPE_PROP)
    from .nh_scatter import (_COLLIDER_LOD_NAMES, _ROADWAY_LOD_TOKEN, _collider_lod_token_from_object)
    if not _is_live_blender_object_exp(obj):
        return False
    if getattr(obj, "type", None) != "MESH":
        return False
    try:
        if obj.get(_COLLIDER_EXP_TYPE_PROP):
            return True
    except Exception:
        pass
    lod_token = _collider_lod_token_from_object(obj, allow_name_fallback=True)
    if lod_token in {*_COLLIDER_LOD_NAMES.keys(), _ROADWAY_LOD_TOKEN}:
        return True
    return _is_collider_exp_object_in_lod_collection_exp(context, obj)


def _resolve_collider_exp_validation_objects_exp(context, settings):
    selected = [
        obj for obj in getattr(context, "selected_objects", [])
        if _is_collider_exp_validation_candidate_exp(context, obj)
    ]
    if selected:
        return selected

    active = getattr(getattr(context, "view_layer", None), "objects", None)
    active_obj = getattr(active, "active", None) if active is not None else None
    if _is_collider_exp_validation_candidate_exp(context, active_obj):
        return [active_obj]

    target_obj = None
    if settings is not None:
        try:
            target_obj = getattr(settings, "geometry_object", None)
        except ReferenceError:
            target_obj = None
        except Exception:
            target_obj = None
    if _is_collider_exp_validation_candidate_exp(context, target_obj):
        return [target_obj]

    return []


def _collider_exp_face_islands_exp(bm):
    unvisited = {face for face in bm.faces if face.is_valid}
    islands = []
    while unvisited:
        first = unvisited.pop()
        island = []
        stack = [first]
        while stack:
            face = stack.pop()
            if face is None or not face.is_valid:
                continue
            island.append(face)
            for edge in face.edges:
                if edge is None or not edge.is_valid:
                    continue
                for linked_face in edge.link_faces:
                    if linked_face in unvisited:
                        unvisited.remove(linked_face)
                        stack.append(linked_face)
        islands.append(island)
    return islands


def _collider_exp_faces_bounds_exp(faces):
    verts = []
    seen = set()
    for face in faces:
        for vert in face.verts:
            if vert is None or not vert.is_valid:
                continue
            key = id(vert)
            if key in seen:
                continue
            seen.add(key)
            verts.append(vert)
    if not verts:
        return None, None, None
    min_v = Vector((
        min(vert.co.x for vert in verts),
        min(vert.co.y for vert in verts),
        min(vert.co.z for vert in verts),
    ))
    max_v = Vector((
        max(vert.co.x for vert in verts),
        max(vert.co.y for vert in verts),
        max(vert.co.z for vert in verts),
    ))
    return min_v, max_v, (min_v + max_v) * 0.5


def _validate_collider_exp_object_exp(context, obj, *, max_triangles=0, minimum_size=0.0):
    errors = []
    warnings = []
    details = []
    mesh = getattr(obj, "data", None)
    if obj is None or getattr(obj, "type", None) != "MESH" or mesh is None:
        return {"errors": ["Object must be a mesh"], "warnings": warnings, "details": details}

    triangle_count = sum(max(0, int(poly.loop_total) - 2) for poly in mesh.polygons)
    details.append(f"triangles={triangle_count}")
    max_triangles = max(0, int(max_triangles))
    if max_triangles > 0 and triangle_count > max_triangles:
        warnings.append(f"Triangle count {triangle_count} exceeds limit {max_triangles}")

    ngon_count = sum(1 for poly in mesh.polygons if int(poly.loop_total) > 4)
    if ngon_count:
        errors.append(f"N-gons: {ngon_count}")

    scale = getattr(obj, "scale", (1.0, 1.0, 1.0))
    bad_scale = [axis for axis in range(3) if abs(float(scale[axis]) - 1.0) > 1e-4]
    if bad_scale:
        warnings.append(f"Scale is not applied: ({scale[0]:.4g}, {scale[1]:.4g}, {scale[2]:.4g})")

    if not _is_collider_exp_object_in_lod_collection_exp(context, obj):
        errors.append("Object is not inside a collision/LOD collection")

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.normal_update()

        non_manifold_count = sum(
            1 for edge in bm.edges
            if edge.is_valid and len([face for face in edge.link_faces if face.is_valid]) != 2
        )
        if non_manifold_count:
            errors.append(f"Non-manifold edges: {non_manifold_count}")

        minimum_size = max(0.0, float(minimum_size))
        tiny_islands = 0
        flipped_faces = 0
        for island in _collider_exp_face_islands_exp(bm):
            min_v, max_v, center = _collider_exp_faces_bounds_exp(island)
            if min_v is None:
                continue
            size = max_v - min_v
            if minimum_size > 0.0 and max(abs(size.x), abs(size.y), abs(size.z)) < minimum_size:
                tiny_islands += 1
            for face in island:
                face_center = face.calc_center_median()
                direction = face_center - center
                if direction.length <= 1e-8:
                    continue
                if face.normal.dot(direction.normalized()) < -0.05:
                    flipped_faces += 1

        if tiny_islands:
            warnings.append(f"Too-small collision islands: {tiny_islands} below {minimum_size:g} m")
        if flipped_faces:
            warnings.append(f"Possible flipped normals: {flipped_faces} face(s)")
    finally:
        bm.free()

    return {"errors": errors, "warnings": warnings, "details": details}


def _print_collider_exp_validation_report_exp(results):
    print("=== NH Collision Validate ===")
    for obj, result in results:
        errors = result.get("errors", [])
        warnings = result.get("warnings", [])
        details = result.get("details", [])
        status = "OK" if not errors and not warnings else "CHECK"
        print(f"[{status}] {obj.name}")
        for detail in details:
            print(f"  - {detail}")
        for error in errors:
            print(f"  - ERROR: {error}")
        for warning in warnings:
            print(f"  - WARNING: {warning}")


def _collider_exp_self_test_cube_mesh_exp(name, size=2.0):
    half = float(size) * 0.5
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(
        [
            (-half, -half, -half),
            ( half, -half, -half),
            ( half,  half, -half),
            (-half,  half, -half),
            (-half, -half,  half),
            ( half, -half,  half),
            ( half,  half,  half),
            (-half,  half,  half),
        ],
        [],
        [
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
        ],
    )
    mesh.update(calc_edges=True)
    return mesh


def _collider_exp_mesh_signature_exp(obj):
    mesh = getattr(obj, "data", None)
    if obj is None or getattr(obj, "type", None) != "MESH" or mesh is None:
        return None
    return (
        tuple(tuple(round(float(coord), 6) for coord in vert.co) for vert in mesh.vertices),
        tuple(tuple(poly.vertices) for poly in mesh.polygons),
        tuple(round(float(value), 6) for row in obj.matrix_world for value in row),
    )


def _remove_collider_exp_self_test_data_exp(root_collection, meshes):
    from .nh_assets import (_remove_collection_tree)
    if root_collection is not None and bpy.data.collections.get(root_collection.name) is not None:
        _remove_collection_tree(root_collection)
    for mesh in list(meshes):
        try:
            if mesh is not None and mesh.users == 0 and bpy.data.meshes.get(mesh.name) is not None:
                bpy.data.meshes.remove(mesh)
        except (ReferenceError, RuntimeError):
            pass
        except Exception:
            pass


def _axis_vector_exp(axis_index):
    vec = Vector((0.0, 0.0, 0.0))
    vec[axis_index] = 1.0
    return vec


def _is_valid_cylinder_profile_exp(profile):
    if not isinstance(profile, dict):
        return False
    required = ("center", "axis_a", "axis_b", "depth_axis", "radius_a", "radius_b", "depth")
    if not all(key in profile for key in required):
        return False
    try:
        if float(profile.get("depth", 0.0)) <= 1e-8:
            return False
        if min(float(profile.get("radius_a", 0.0)), float(profile.get("radius_b", 0.0))) <= 1e-8:
            return False
    except Exception:
        return False
    return True


def _two_ring_profile_from_data_exp(data):
    profile = data.get("two_ring_profile") if isinstance(data, dict) else None
    if not _is_valid_cylinder_profile_exp(profile):
        return None
    return profile


def _cylinder_profile_from_data_exp(data):
    profile = _two_ring_profile_from_data_exp(data)
    if profile is not None:
        return profile
    if not isinstance(data, dict):
        return None
    profile = data.get("cylinder_axis_profile")
    if not _is_valid_cylinder_profile_exp(profile):
        profile = _inferred_cylinder_axis_profile_exp(data)
        if _is_valid_cylinder_profile_exp(profile):
            data["cylinder_axis_profile"] = profile
    return profile if _is_valid_cylinder_profile_exp(profile) else None


def _two_ring_uniform_scale_exp(op):
    try:
        return max(float(getattr(op, "scale_multiplier", 1.0)), 0.001)
    except Exception:
        return 1.0


def _two_ring_offset_center_exp(profile, op):
    return profile["center"] + _collider_exp_vec_from_props_exp(op, "offset")


def _two_ring_cylinder_mesh_exp(data, op):
    profile = _cylinder_profile_from_data_exp(data)
    if profile is None:
        return None
    scale = _two_ring_uniform_scale_exp(op)
    minimum_size = max(float(getattr(op, "minimum_size", 0.0)), 1e-6)
    center = _two_ring_offset_center_exp(profile, op)
    axis_a = profile["axis_a"]
    axis_b = profile["axis_b"]
    depth_axis = profile["depth_axis"]
    radius_a = max(float(profile["radius_a"]) * scale, minimum_size * 0.5)
    radius_b = max(float(profile["radius_b"]) * scale, minimum_size * 0.5)
    depth = max(float(profile["depth"]) * scale, minimum_size)
    segments = max(4, min(int(getattr(op, "cylinder_segments", profile.get("edge_count", 16)) or 16), 128))
    half_depth_vec = depth_axis * (depth * 0.5)

    vertices = []
    bottom = []
    top = []
    for idx in range(segments):
        angle = (2.0 * math.pi * idx) / segments
        ring_vec = axis_a * (math.cos(angle) * radius_a) + axis_b * (math.sin(angle) * radius_b)
        bottom.append(len(vertices))
        vertices.append(center + ring_vec - half_depth_vec)
        top.append(len(vertices))
        vertices.append(center + ring_vec + half_depth_vec)

    bottom_center = len(vertices)
    vertices.append(center - half_depth_vec)
    top_center = len(vertices)
    vertices.append(center + half_depth_vec)
    faces = []
    for idx in range(segments):
        nxt = (idx + 1) % segments
        faces.append((bottom[idx], bottom[nxt], top[nxt], top[idx]))
        faces.append((bottom_center, bottom[nxt], bottom[idx]))
        faces.append((top_center, top[idx], top[nxt]))
    return vertices, faces


def _two_ring_pipe_mesh_exp(data, op):
    profile = _two_ring_profile_from_data_exp(data)
    if profile is None:
        return None
    scale = _two_ring_uniform_scale_exp(op)
    minimum_size = max(float(getattr(op, "minimum_size", 0.0)), 1e-6)
    center = _two_ring_offset_center_exp(profile, op)
    axis_a = profile["axis_a"]
    axis_b = profile["axis_b"]
    depth_axis = profile["depth_axis"]
    outer_multiplier = max(float(getattr(op, "pipe_outer_radius", 1.0)), 0.001)
    radius_a = max(float(profile["radius_a"]) * outer_multiplier * scale, minimum_size * 0.5)
    radius_b = max(float(profile["radius_b"]) * outer_multiplier * scale, minimum_size * 0.5)
    depth = max(float(profile["depth"]) * scale, max(float(getattr(op, "pipe_depth", 0.25)), 0.001), minimum_size)
    inner_factor = max(0.0, min(float(getattr(op, "pipe_inner_radius", 0.5)), 0.98))
    thickness = max(float(getattr(op, "pipe_thickness", 0.0)), 0.0)
    if thickness > 0.0:
        avg_radius = max((radius_a + radius_b) * 0.5, minimum_size)
        inner_factor = min(inner_factor, max(0.0, 1.0 - thickness / avg_radius))
    inner_radius_a = max(radius_a * inner_factor, minimum_size * 0.05)
    inner_radius_b = max(radius_b * inner_factor, minimum_size * 0.05)
    segments = max(4, min(int(getattr(op, "pipe_segments", profile.get("edge_count", 24)) or 24), 128))
    half_depth_vec = depth_axis * (depth * 0.5)

    vertices = []
    outer_bottom = []
    outer_top = []
    inner_bottom = []
    inner_top = []
    for idx in range(segments):
        angle = (2.0 * math.pi * idx) / segments
        outer_vec = axis_a * (math.cos(angle) * radius_a) + axis_b * (math.sin(angle) * radius_b)
        inner_vec = axis_a * (math.cos(angle) * inner_radius_a) + axis_b * (math.sin(angle) * inner_radius_b)
        outer_bottom.append(len(vertices))
        vertices.append(center + outer_vec - half_depth_vec)
        outer_top.append(len(vertices))
        vertices.append(center + outer_vec + half_depth_vec)
        inner_bottom.append(len(vertices))
        vertices.append(center + inner_vec - half_depth_vec)
        inner_top.append(len(vertices))
        vertices.append(center + inner_vec + half_depth_vec)

    faces = []
    for idx in range(segments):
        nxt = (idx + 1) % segments
        faces.append((outer_bottom[idx], outer_bottom[nxt], outer_top[nxt], outer_top[idx]))
        faces.append((inner_bottom[nxt], inner_bottom[idx], inner_top[idx], inner_top[nxt]))
        faces.append((outer_top[idx], outer_top[nxt], inner_top[nxt], inner_top[idx]))
        faces.append((outer_bottom[nxt], outer_bottom[idx], inner_bottom[idx], inner_bottom[nxt]))
    return vertices, faces


def _two_ring_ellipse_vector_exp(axis_a, axis_b, radius_a, radius_b, angle):
    return axis_a * (math.cos(angle) * radius_a) + axis_b * (math.sin(angle) * radius_b)


def _two_ring_ellipse_radius_exp(radius_a, radius_b, angle):
    radius_a = max(float(radius_a), 1e-6)
    radius_b = max(float(radius_b), 1e-6)
    c = math.cos(angle)
    s = math.sin(angle)
    denom = ((c / radius_a) ** 2 + (s / radius_b) ** 2) ** 0.5
    if denom <= 1e-12:
        return min(radius_a, radius_b)
    return 1.0 / denom


def _two_ring_radial_axis_exp(axis_a, axis_b, angle):
    axis = axis_a * math.cos(angle) + axis_b * math.sin(angle)
    if axis.length_squared <= 1e-12:
        return axis_a
    return axis.normalized()


def _two_ring_tangent_axis_exp(axis_a, axis_b, radius_a, radius_b, angle):
    tangent = axis_a * (-math.sin(angle) * radius_a) + axis_b * (math.cos(angle) * radius_b)
    if tangent.length_squared <= 1e-12:
        tangent = axis_b
    if tangent.length_squared <= 1e-12:
        tangent = axis_a.cross(Vector((0.0, 0.0, 1.0)))
    if tangent.length_squared <= 1e-12:
        tangent = Vector((0.0, 1.0, 0.0))
    return tangent.normalized()


def _two_ring_cylinder_boxes_mesh_exp(data, op):
    profile = _cylinder_profile_from_data_exp(data)
    if profile is None:
        return None
    scale = _two_ring_uniform_scale_exp(op)
    minimum_size = max(float(getattr(op, "minimum_size", 0.0)), 1e-6)
    center = _two_ring_offset_center_exp(profile, op)
    axis_a = profile["axis_a"]
    axis_b = profile["axis_b"]
    depth_axis = profile["depth_axis"]
    radius_a = max(float(profile["radius_a"]) * scale, minimum_size * 0.5)
    radius_b = max(float(profile["radius_b"]) * scale, minimum_size * 0.5)
    depth = max(float(profile["depth"]) * scale, minimum_size)
    segments = max(2, min(int(getattr(op, "cylinder_segments", profile.get("edge_count", 16)) or 16), 128))
    step = math.pi / segments
    vertices = []
    faces = []
    for idx in range(segments):
        angle = idx * step
        a0 = angle - step * 0.5
        a1 = angle + step * 0.5
        radial_axis = _two_ring_radial_axis_exp(axis_a, axis_b, angle)
        tangent_axis = _two_ring_tangent_axis_exp(axis_a, axis_b, radius_a, radius_b, angle)
        edge0 = _two_ring_ellipse_vector_exp(axis_a, axis_b, radius_a, radius_b, a0)
        edge1 = _two_ring_ellipse_vector_exp(axis_a, axis_b, radius_a, radius_b, a1)
        radial_len = max(_two_ring_ellipse_radius_exp(radius_a, radius_b, angle) * 2.0, minimum_size)
        tangent_len = max(abs((edge1 - edge0).dot(tangent_axis)), minimum_size)
        box = _make_oriented_box_world_exp(
            data["matrix_world"],
            center,
            radial_axis,
            tangent_axis,
            depth_axis,
            radial_len,
            tangent_len,
            depth,
        )
        _append_box_data_exp(vertices, faces, box)
    return vertices, faces, 0.0


def _two_ring_pipe_boxes_mesh_exp(data, op):
    profile = _two_ring_profile_from_data_exp(data)
    if profile is None:
        return None
    scale = _two_ring_uniform_scale_exp(op)
    minimum_size = max(float(getattr(op, "minimum_size", 0.0)), 1e-6)
    center = _two_ring_offset_center_exp(profile, op)
    axis_a = profile["axis_a"]
    axis_b = profile["axis_b"]
    depth_axis = profile["depth_axis"]
    outer_multiplier = max(float(getattr(op, "pipe_outer_radius", 1.0)), 0.001)
    radius_a = max(float(profile["radius_a"]) * outer_multiplier * scale, minimum_size * 0.5)
    radius_b = max(float(profile["radius_b"]) * outer_multiplier * scale, minimum_size * 0.5)
    depth = max(float(profile["depth"]) * scale, max(float(getattr(op, "pipe_depth", 0.25)), 0.001), minimum_size)
    inner_factor = max(0.0, min(float(getattr(op, "pipe_inner_radius", 0.5)), 0.98))
    thickness = max(float(getattr(op, "pipe_thickness", 0.0)), 0.0)
    if thickness > 0.0:
        avg_radius = max((radius_a + radius_b) * 0.5, minimum_size)
        inner_factor = min(inner_factor, max(0.0, 1.0 - thickness / avg_radius))
    segments = max(4, min(int(getattr(op, "pipe_segments", profile.get("edge_count", 24)) or 24), 128))
    step = (2.0 * math.pi) / segments
    vertices = []
    faces = []
    for idx in range(segments):
        angle = (idx + 0.5) * step
        outer_vec = _two_ring_ellipse_vector_exp(axis_a, axis_b, radius_a, radius_b, angle)
        inner_vec = outer_vec * inner_factor
        radial_vec = outer_vec - inner_vec
        if radial_vec.length_squared <= 1e-12:
            radial_axis = _two_ring_radial_axis_exp(axis_a, axis_b, angle)
        else:
            radial_axis = radial_vec.normalized()
        tangent_axis = _two_ring_tangent_axis_exp(axis_a, axis_b, radius_a, radius_b, angle)
        center_vec = (outer_vec + inner_vec) * 0.5
        radial_len = max(radial_vec.length, minimum_size)
        tangent_len = max(outer_vec.length * math.tan(step * 0.5) * 2.08, minimum_size)
        box = _make_oriented_box_world_exp(
            data["matrix_world"],
            center + center_vec,
            radial_axis,
            tangent_axis,
            depth_axis,
            radial_len,
            tangent_len,
            depth,
        )
        _append_box_data_exp(vertices, faces, box)
    return vertices, faces


def _ring_axes_from_data_exp(data):
    size = data["size"]
    sizes = [abs(size.x), abs(size.y), abs(size.z)]
    sorted_axes = sorted(range(3), key=lambda axis: sizes[axis])
    smallest_axis, middle_axis, largest_axis = sorted_axes
    smallest = sizes[smallest_axis]
    middle = max(sizes[middle_axis], 1e-6)
    largest = sizes[largest_axis]

    if largest >= middle * 1.5:
        depth_axis = largest_axis
    elif smallest <= middle * 0.75:
        depth_axis = smallest_axis
    else:
        depth_axis = 2
    plane_axes = [axis for axis in range(3) if axis != depth_axis]
    return plane_axes[0], plane_axes[1], depth_axis


def _infer_inner_factor_from_source_exp(data, axis_a, axis_b):
    center = data["center"]
    size = data["size"]
    radius_a = max(abs(size[axis_a]) * 0.5, 1e-6)
    radius_b = max(abs(size[axis_b]) * 0.5, 1e-6)
    samples = []
    samples.extend(data.get("face_centers_local") or [])
    samples.extend(data.get("local_points") or [])
    distances = []
    for point in samples:
        da = (point[axis_a] - center[axis_a]) / radius_a
        db = (point[axis_b] - center[axis_b]) / radius_b
        distances.append((da * da + db * db) ** 0.5)
    inner_candidates = [
        distance for distance in distances
        if 0.35 <= distance <= 0.98
    ]
    if not inner_candidates:
        return 0.0
    inner = min(inner_candidates)
    return max(0.0, min(inner * 0.9, 0.85))


def _infer_inner_factor_exact_from_source_exp(data, axis_a, axis_b):
    center = data["center"]
    size = data["size"]
    radius_a = max(abs(size[axis_a]) * 0.5, 1e-6)
    radius_b = max(abs(size[axis_b]) * 0.5, 1e-6)
    distances = []
    for point in data.get("local_points") or []:
        da = (point[axis_a] - center[axis_a]) / radius_a
        db = (point[axis_b] - center[axis_b]) / radius_b
        distance = (da * da + db * db) ** 0.5
        if 0.05 <= distance <= 0.98:
            distances.append(distance)
    if not distances:
        return 0.0
    return max(0.0, min(min(distances), 0.98))


def _radial_direction_count_from_data_exp(data, axis_a, axis_b):
    center = data["center"]
    size = data["size"]
    radius = max(abs(size[axis_a]), abs(size[axis_b]), 1e-6)
    keys = set()
    for point in data.get("local_points") or []:
        da = point[axis_a] - center[axis_a]
        db = point[axis_b] - center[axis_b]
        if (da * da + db * db) ** 0.5 < radius * 0.05:
            continue
        angle = (math.atan2(db, da) + (2.0 * math.pi)) % (2.0 * math.pi)
        keys.add(int(round(angle / (2.0 * math.pi) * 4096.0)) % 4096)
    return len(keys)


def _ring_vectors_from_data_exp(data, axis_a, axis_b):
    center = data["center"]
    size = data["size"]
    radius = max(abs(size[axis_a]) * 0.5, abs(size[axis_b]) * 0.5, 1e-6)
    buckets = {}
    for point in data.get("local_points") or []:
        vec = Vector((0.0, 0.0, 0.0))
        vec[axis_a] = point[axis_a] - center[axis_a]
        vec[axis_b] = point[axis_b] - center[axis_b]
        length = vec.length
        if length < radius * 0.05:
            continue
        angle = (math.atan2(vec[axis_b], vec[axis_a]) + (2.0 * math.pi)) % (2.0 * math.pi)
        key = int(round(angle / (2.0 * math.pi) * 4096.0)) % 4096
        current = buckets.get(key)
        if current is None or length > current.length:
            buckets[key] = vec

    return sorted(
        buckets.values(),
        key=lambda vec: (math.atan2(vec[axis_b], vec[axis_a]) + (2.0 * math.pi)) % (2.0 * math.pi),
    )


def _ring_bounds_vectors_from_data_exp(data, axis_a, axis_b):
    center = data["center"]
    size = data["size"]
    radius = max(abs(size[axis_a]) * 0.5, abs(size[axis_b]) * 0.5, 1e-6)
    buckets = {}
    for point in data.get("local_points") or []:
        vec = Vector((0.0, 0.0, 0.0))
        vec[axis_a] = point[axis_a] - center[axis_a]
        vec[axis_b] = point[axis_b] - center[axis_b]
        length = vec.length
        if length < radius * 0.05:
            continue
        angle = (math.atan2(vec[axis_b], vec[axis_a]) + (2.0 * math.pi)) % (2.0 * math.pi)
        key = int(round(angle / (2.0 * math.pi) * 4096.0)) % 4096
        item = buckets.get(key)
        if item is None:
            buckets[key] = {"angle": angle, "inner": vec, "outer": vec}
            continue
        if length < item["inner"].length:
            item["inner"] = vec
        if length > item["outer"].length:
            item["outer"] = vec

    return [
        (item["inner"], item["outer"])
        for item in sorted(buckets.values(), key=lambda value: value["angle"])
    ]


def _ellipse_vector_from_angle_exp(axis_a, axis_b, radius_a, radius_b, angle):
    return (
        _axis_vector_exp(axis_a) * (math.cos(angle) * radius_a)
        + _axis_vector_exp(axis_b) * (math.sin(angle) * radius_b)
    )


def _ellipse_radius_in_direction_exp(radius_a, radius_b, angle):
    radius_a = max(float(radius_a), 1e-6)
    radius_b = max(float(radius_b), 1e-6)
    c = math.cos(angle)
    s = math.sin(angle)
    denom = ((c / radius_a) ** 2 + (s / radius_b) ** 2) ** 0.5
    if denom <= 1e-12:
        return min(radius_a, radius_b)
    return 1.0 / denom


def _ellipse_direction_axis_exp(axis_a, axis_b, angle):
    axis = _axis_vector_exp(axis_a) * math.cos(angle) + _axis_vector_exp(axis_b) * math.sin(angle)
    if axis.length_squared <= 1e-12:
        return _axis_vector_exp(axis_a)
    return axis


def _perpendicular_axis_in_plane_exp(axis_a, axis_b, axis):
    tangent = _axis_vector_exp(axis_a) * (-axis[axis_b]) + _axis_vector_exp(axis_b) * axis[axis_a]
    if tangent.length_squared <= 1e-12:
        return _axis_vector_exp(axis_b)
    return tangent


def _ellipse_tangent_axis_exp(axis_a, axis_b, radius_a, radius_b, angle):
    del radius_a, radius_b
    return _perpendicular_axis_in_plane_exp(
        axis_a,
        axis_b,
        _ellipse_direction_axis_exp(axis_a, axis_b, angle),
    )


def _make_oriented_box_world_exp(matrix_world, center_local, axis_u, axis_v, axis_w, size_u, size_v, size_w):
    u = axis_u.normalized() * (size_u * 0.5)
    v = axis_v.normalized() * (size_v * 0.5)
    w = axis_w.normalized() * (size_w * 0.5)
    local_verts = [
        center_local - u - v - w,
        center_local + u - v - w,
        center_local + u + v - w,
        center_local - u + v - w,
        center_local - u - v + w,
        center_local + u - v + w,
        center_local + u + v + w,
        center_local - u + v + w,
    ]
    return [matrix_world @ point for point in local_verts]


def _append_box_data_exp(vertices, faces, box_vertices):
    from .nh_collider import (_COLLIDER_EXP_BOX_FACES)
    offset = len(vertices)
    vertices.extend(box_vertices)
    faces.extend(tuple(offset + idx for idx in face) for face in _COLLIDER_EXP_BOX_FACES)


def _apply_floor_contact_to_vertices_exp(world_vertices, floor_z, enabled):
    if not enabled or not world_vertices:
        return world_vertices
    current_floor_z = min(point.z for point in world_vertices)
    delta_z = float(floor_z) - current_floor_z
    return [point + Vector((0.0, 0.0, delta_z)) for point in world_vertices]


def _is_collider_exp_guide_object_exp(obj, guide_type=""):
    from .nh_collider import (_COLLIDER_EXP_GUIDE_PROP)
    if not _is_live_blender_object_exp(obj):
        return False
    if getattr(obj, "type", None) != "MESH":
        return False
    try:
        value = str(obj.get(_COLLIDER_EXP_GUIDE_PROP, "") or "")
    except Exception:
        return False
    if not value:
        return False
    if guide_type:
        return value == str(guide_type)
    return True


def _axis_offset_exp(axis_index, amount):
    vec = Vector((0.0, 0.0, 0.0))
    vec[axis_index] = float(amount)
    return vec


def _cylinder_guide_mesh_from_data_exp(data, op):
    profile_mesh = _two_ring_cylinder_mesh_exp(data, op)
    if profile_mesh is not None:
        return profile_mesh

    axis_a, axis_b, depth_axis = _ring_axes_from_data_exp(data)
    center = data["center"] + _collider_exp_vec_from_props_exp(op, "offset")
    scale_vec = _collider_exp_scale_vec_exp(op)
    size = data["size"]
    minimum_size = max(float(getattr(op, "minimum_size", 0.0)), 1e-6)
    radius_a = max(abs(size[axis_a]) * 0.5 * scale_vec[axis_a], minimum_size * 0.5)
    radius_b = max(abs(size[axis_b]) * 0.5 * scale_vec[axis_b], minimum_size * 0.5)
    depth = max(abs(size[depth_axis]) * scale_vec[depth_axis], minimum_size)
    segments = max(4, min(int(getattr(op, "cylinder_segments", 16)), 128))
    half_depth_vec = _axis_offset_exp(depth_axis, depth * 0.5)

    vertices = []
    bottom = []
    top = []
    for idx in range(segments):
        angle = (2.0 * math.pi * idx) / segments
        ring_vec = _ellipse_vector_from_angle_exp(axis_a, axis_b, radius_a, radius_b, angle)
        bottom.append(len(vertices))
        vertices.append(center + ring_vec - half_depth_vec)
        top.append(len(vertices))
        vertices.append(center + ring_vec + half_depth_vec)

    bottom_center = len(vertices)
    vertices.append(center - half_depth_vec)
    top_center = len(vertices)
    vertices.append(center + half_depth_vec)

    faces = []
    for idx in range(segments):
        nxt = (idx + 1) % segments
        faces.append((bottom[idx], bottom[nxt], top[nxt], top[idx]))
        faces.append((bottom_center, bottom[nxt], bottom[idx]))
        faces.append((top_center, top[idx], top[nxt]))
    return vertices, faces


def _pipe_inner_factor_for_data_exp(data, axis_a, axis_b, radius_a, radius_b, op):
    minimum_size = max(float(getattr(op, "minimum_size", 0.0)), 1e-6)
    if _collider_exp_data_is_guide_exp(data, "PIPE"):
        inner_factor = _infer_inner_factor_exact_from_source_exp(data, axis_a, axis_b)
        return max(0.0, min(inner_factor, 0.98))

    configured_inner_raw = float(getattr(op, "pipe_inner_radius", 0.5))
    configured_inner = max(configured_inner_raw, 0.0)
    thickness = max(float(getattr(op, "pipe_thickness", 0.001)), minimum_size)
    avg_radius = max((radius_a + radius_b) * 0.5, minimum_size)
    inferred_inner = _infer_inner_factor_from_source_exp(data, axis_a, axis_b)

    if configured_inner_raw >= 0.0:
        # The UI value is an explicit override. Values <= 0.98 behave as an
        # outer-radius factor; larger values behave as an absolute world radius.
        # Source inference is only a fallback for legacy/invalid negative values.
        # This lets users make a pipe hole smaller than the source mesh hole.
        if configured_inner <= 0.98:
            inner_factor = configured_inner
        else:
            inner_factor = configured_inner / avg_radius
    elif inferred_inner > 0.0:
        inner_factor = inferred_inner
    else:
        inner_factor = 0.0
    inner_factor = max(0.0, min(inner_factor, 0.98))
    return min(inner_factor, max(0.0, 1.0 - (thickness / avg_radius)))


def _pipe_guide_mesh_from_data_exp(data, op):
    profile_mesh = _two_ring_pipe_mesh_exp(data, op)
    if profile_mesh is not None:
        return profile_mesh

    axis_a, axis_b, depth_axis = _ring_axes_from_data_exp(data)
    center = data["center"] + _collider_exp_vec_from_props_exp(op, "offset")
    scale_vec = _collider_exp_scale_vec_exp(op)
    size = data["size"]
    minimum_size = max(float(getattr(op, "minimum_size", 0.0)), 1e-6)
    outer_multiplier = max(float(getattr(op, "pipe_outer_radius", 1.0)), 0.001)
    configured_depth = max(float(getattr(op, "pipe_depth", 0.25)), 0.001)

    source_radius_a = max(abs(size[axis_a]) * 0.5, minimum_size * 0.5)
    source_radius_b = max(abs(size[axis_b]) * 0.5, minimum_size * 0.5)
    radius_a = max(source_radius_a * outer_multiplier * scale_vec[axis_a], minimum_size * 0.5)
    radius_b = max(source_radius_b * outer_multiplier * scale_vec[axis_b], minimum_size * 0.5)
    depth = max(abs(size[depth_axis]) * scale_vec[depth_axis], configured_depth * scale_vec[depth_axis], minimum_size)
    inner_factor = _pipe_inner_factor_for_data_exp(data, axis_a, axis_b, radius_a, radius_b, op)
    inner_min_radius = max(min(radius_a, radius_b) * 0.001, 1e-5)
    inner_radius_a = max(radius_a * inner_factor, inner_min_radius)
    inner_radius_b = max(radius_b * inner_factor, inner_min_radius)
    segments = max(4, min(int(getattr(op, "pipe_segments", 24)), 128))
    half_depth_vec = _axis_offset_exp(depth_axis, depth * 0.5)

    vertices = []
    outer_bottom = []
    outer_top = []
    inner_bottom = []
    inner_top = []
    for idx in range(segments):
        angle = (2.0 * math.pi * idx) / segments
        outer_vec = _ellipse_vector_from_angle_exp(axis_a, axis_b, radius_a, radius_b, angle)
        inner_vec = _ellipse_vector_from_angle_exp(axis_a, axis_b, inner_radius_a, inner_radius_b, angle)
        outer_bottom.append(len(vertices))
        vertices.append(center + outer_vec - half_depth_vec)
        outer_top.append(len(vertices))
        vertices.append(center + outer_vec + half_depth_vec)
        inner_bottom.append(len(vertices))
        vertices.append(center + inner_vec - half_depth_vec)
        inner_top.append(len(vertices))
        vertices.append(center + inner_vec + half_depth_vec)

    faces = []
    for idx in range(segments):
        nxt = (idx + 1) % segments
        faces.append((outer_bottom[idx], outer_bottom[nxt], outer_top[nxt], outer_top[idx]))
        faces.append((inner_bottom[nxt], inner_bottom[idx], inner_top[idx], inner_top[nxt]))
        faces.append((outer_top[idx], outer_top[nxt], inner_top[nxt], inner_top[idx]))
        faces.append((outer_bottom[nxt], outer_bottom[idx], inner_bottom[idx], inner_bottom[nxt]))
    return vertices, faces



def _collider_exp_local_vertices_to_world_exp(data, vertices, op):
    matrix_world = data["matrix_world"]
    world_vertices = [matrix_world @ vertex for vertex in vertices]
    return _apply_floor_contact_to_vertices_exp(
        world_vertices,
        data["world_floor_z"],
        bool(getattr(op, "floor_contact", False)),
    )


def _cylinder_guide_box_mesh_from_data_exp(data, axis_a, axis_b, depth_axis, center, depth, minimum_size):
    ring_vectors = _ring_vectors_from_data_exp(data, axis_a, axis_b)
    edge_count = len(ring_vectors)
    if edge_count < 4 or edge_count % 2 != 0:
        return None

    half_edges = edge_count // 2
    axis_depth = _axis_vector_exp(depth_axis)
    vertices = []
    faces = []
    for idx in range(half_edges):
        p0 = ring_vectors[idx]
        p1 = ring_vectors[(idx + 1) % edge_count]
        q0 = ring_vectors[(idx + half_edges) % edge_count]
        q1 = ring_vectors[(idx + 1 + half_edges) % edge_count]
        edge_mid = (p0 + p1) * 0.5
        opposite_mid = (q0 + q1) * 0.5
        radial_vec = edge_mid - opposite_mid
        tangent_vec = p1 - p0
        if radial_vec.length_squared <= 1e-12 or tangent_vec.length_squared <= 1e-12:
            continue
        radial_axis = radial_vec.normalized()
        tangent_axis = tangent_vec - radial_axis * tangent_vec.dot(radial_axis)
        if tangent_axis.length_squared <= 1e-12:
            tangent_axis = _perpendicular_axis_in_plane_exp(axis_a, axis_b, radial_axis)
        box_vertices = _make_oriented_box_world_exp(
            data["matrix_world"],
            center + (edge_mid + opposite_mid) * 0.5,
            radial_axis,
            tangent_axis,
            axis_depth,
            max(radial_vec.length, minimum_size),
            max(min(tangent_vec.length, (q1 - q0).length), minimum_size),
            depth,
        )
        _append_box_data_exp(vertices, faces, box_vertices)

    return vertices, faces



def _pipe_guide_box_mesh_from_data_exp(data, axis_a, axis_b, depth_axis, center, depth, minimum_size):
    ring_pairs = _ring_bounds_vectors_from_data_exp(data, axis_a, axis_b)
    segment_count = len(ring_pairs)
    if segment_count < 4:
        return None

    depth_axis_vector = _axis_vector_exp(depth_axis)
    depth_len = max(float(depth), float(minimum_size))
    vertices = []
    faces = []
    for idx in range(segment_count):
        inner0, outer0 = ring_pairs[idx]
        inner1, outer1 = ring_pairs[(idx + 1) % segment_count]
        inner_mid = (inner0 + inner1) * 0.5
        outer_mid = (outer0 + outer1) * 0.5
        segment_center_vec = (inner_mid + outer_mid) * 0.5
        radial_vec = outer_mid - inner_mid
        centerline0 = (inner0 + outer0) * 0.5
        centerline1 = (inner1 + outer1) * 0.5
        tangent_vec = centerline1 - centerline0
        if radial_vec.length_squared <= 1e-12 or tangent_vec.length_squared <= 1e-12:
            continue

        radial_axis = radial_vec.normalized()
        tangent_axis = tangent_vec - radial_axis * tangent_vec.dot(radial_axis)
        if tangent_axis.length_squared <= 1e-12:
            tangent_axis = _perpendicular_axis_in_plane_exp(axis_a, axis_b, radial_axis)
        if tangent_axis.length_squared <= 1e-12:
            continue

        box_vertices = _make_oriented_box_world_exp(
            data["matrix_world"],
            center + segment_center_vec,
            radial_axis,
            tangent_axis,
            depth_axis_vector,
            max(radial_vec.length, minimum_size),
            max(tangent_vec.length, minimum_size),
            depth_len,
        )
        _append_box_data_exp(vertices, faces, box_vertices)

    return vertices, faces


def _cylinder_box_mesh_from_data_exp(data, op):
    axis_a, axis_b, depth_axis = _ring_axes_from_data_exp(data)
    center = data["center"] + _collider_exp_shape_offset_vec_exp(data, op)
    scale_vec = _collider_exp_shape_scale_vec_exp(data, op)
    size = data["size"]
    minimum_size = max(float(getattr(op, "minimum_size", 0.0)), 1e-6)
    radius_a = max(abs(size[axis_a]) * 0.5 * scale_vec[axis_a], minimum_size * 0.5)
    radius_b = max(abs(size[axis_b]) * 0.5 * scale_vec[axis_b], minimum_size * 0.5)
    depth = max(abs(size[depth_axis]) * scale_vec[depth_axis], minimum_size)
    inner_factor = 0.0
    if _collider_exp_data_is_guide_exp(data, "CYLINDER"):
        guide_mesh = _cylinder_guide_box_mesh_from_data_exp(data, axis_a, axis_b, depth_axis, center, depth, minimum_size)
        if guide_mesh is not None:
            vertices, faces = guide_mesh
            vertices = _apply_floor_contact_to_vertices_exp(
                vertices,
                data["world_floor_z"],
                bool(getattr(op, "floor_contact", False)),
            )
            return vertices, faces, inner_factor

    profile_mesh = _two_ring_cylinder_boxes_mesh_exp(data, op)
    if profile_mesh is not None:
        vertices, faces, inner_factor = profile_mesh
        vertices = _apply_floor_contact_to_vertices_exp(
            vertices,
            data["world_floor_z"],
            bool(getattr(op, "floor_contact", False)),
        )
        return vertices, faces, inner_factor

    segments = max(2, min(int(getattr(op, "cylinder_segments", 16)), 128))
    step = math.pi / segments
    axis_depth = _axis_vector_exp(depth_axis)

    vertices = []
    faces = []
    for idx in range(segments):
        angle = idx * step
        a0 = angle - step * 0.5
        a1 = angle + step * 0.5
        radial_axis = _ellipse_direction_axis_exp(axis_a, axis_b, angle)
        tangent_axis = _ellipse_tangent_axis_exp(axis_a, axis_b, radius_a, radius_b, angle)
        edge0 = _ellipse_vector_from_angle_exp(axis_a, axis_b, radius_a, radius_b, a0)
        edge1 = _ellipse_vector_from_angle_exp(axis_a, axis_b, radius_a, radius_b, a1)
        radial_half = min(
            abs(edge0.dot(radial_axis)),
            abs(edge1.dot(radial_axis)),
            _ellipse_radius_in_direction_exp(radius_a, radius_b, angle),
        )
        radial_len = max(radial_half * 2.0, minimum_size)
        tangent_len = max(
            abs((edge1 - edge0).dot(tangent_axis)),
            minimum_size,
        )

        box = _make_oriented_box_world_exp(
            data["matrix_world"],
            center,
            radial_axis,
            tangent_axis,
            axis_depth,
            radial_len,
            tangent_len,
            depth,
        )
        _append_box_data_exp(vertices, faces, box)

    vertices = _apply_floor_contact_to_vertices_exp(
        vertices,
        data["world_floor_z"],
        bool(getattr(op, "floor_contact", False)),
    )
    return vertices, faces, inner_factor


def _remove_collider_exp_guide_after_conversion_exp(context, guide_obj):
    del context
    if not _is_collider_exp_guide_object_exp(guide_obj):
        return
    mesh = getattr(guide_obj, "data", None)
    try:
        bpy.data.objects.remove(guide_obj, do_unlink=True)
    except (ReferenceError, RuntimeError):
        return
    except Exception:
        return
    if mesh is not None:
        try:
            if mesh.users == 0 and bpy.data.meshes.get(mesh.name) is not None:
                bpy.data.meshes.remove(mesh)
        except Exception:
            pass


def _pipe_box_mesh_from_data_exp(data, op):
    axis_a, axis_b, depth_axis = _ring_axes_from_data_exp(data)
    center = data["center"] + _collider_exp_shape_offset_vec_exp(data, op)
    scale_vec = _collider_exp_shape_scale_vec_exp(data, op)
    size = data["size"]
    minimum_size = max(float(getattr(op, "minimum_size", 0.0)), 1e-6)
    outer_multiplier = 1.0 if _collider_exp_data_is_guide_exp(data, "PIPE") else max(float(getattr(op, "pipe_outer_radius", 1.0)), 0.001)
    configured_depth = max(float(getattr(op, "pipe_depth", 0.25)), 0.001)

    source_radius_a = max(abs(size[axis_a]) * 0.5, minimum_size * 0.5)
    source_radius_b = max(abs(size[axis_b]) * 0.5, minimum_size * 0.5)
    radius_a = max(source_radius_a * outer_multiplier * scale_vec[axis_a], minimum_size * 0.5)
    radius_b = max(source_radius_b * outer_multiplier * scale_vec[axis_b], minimum_size * 0.5)
    source_depth = abs(size[depth_axis]) * scale_vec[depth_axis]
    if _is_collider_exp_guide_object_exp(data.get("source_obj"), "PIPE"):
        depth = max(source_depth, minimum_size)
    else:
        depth = max(source_depth, configured_depth * scale_vec[depth_axis], minimum_size)

    inner_factor = _pipe_inner_factor_for_data_exp(data, axis_a, axis_b, radius_a, radius_b, op)
    if _collider_exp_data_is_guide_exp(data, "PIPE"):
        guide_mesh = _pipe_guide_box_mesh_from_data_exp(data, axis_a, axis_b, depth_axis, center, depth, minimum_size)
        if guide_mesh is not None:
            vertices, faces = guide_mesh
            vertices = _apply_floor_contact_to_vertices_exp(
                vertices,
                data["world_floor_z"],
                bool(getattr(op, "floor_contact", False)),
            )
            return vertices, faces

    profile_mesh = _two_ring_pipe_boxes_mesh_exp(data, op)
    if profile_mesh is not None:
        vertices, faces = profile_mesh
        vertices = _apply_floor_contact_to_vertices_exp(
            vertices,
            data["world_floor_z"],
            bool(getattr(op, "floor_contact", False)),
        )
        return vertices, faces

    guide_edge_count = _radial_direction_count_from_data_exp(data, axis_a, axis_b) if _collider_exp_data_is_guide_exp(data, "PIPE") else 0
    segments = max(4, min(int(getattr(op, "pipe_segments", 24)), 128))
    if guide_edge_count >= 4:
        segments = max(4, min(guide_edge_count, 128))
    step = (2.0 * math.pi) / segments
    axis_depth = _axis_vector_exp(depth_axis)

    vertices = []
    faces = []
    for idx in range(segments):
        angle = (idx + 0.5) * step
        c = math.cos(angle)
        s = math.sin(angle)
        outer_vec = _axis_vector_exp(axis_a) * (c * radius_a) + _axis_vector_exp(axis_b) * (s * radius_b)
        inner_vec = outer_vec * inner_factor
        center_vec = (outer_vec + inner_vec) * 0.5
        radial_axis = outer_vec.normalized() if outer_vec.length_squared > 1e-12 else _axis_vector_exp(axis_a)
        tangent_axis = _perpendicular_axis_in_plane_exp(axis_a, axis_b, radial_axis)

        a0 = angle - step * 0.5
        a1 = angle + step * 0.5
        radial_len = max((outer_vec - inner_vec).length, minimum_size)
        tangent_len = max(outer_vec.length * math.tan(step * 0.5) * 2.08, minimum_size)

        box = _make_oriented_box_world_exp(
            data["matrix_world"],
            center + center_vec,
            radial_axis,
            tangent_axis,
            axis_depth,
            radial_len,
            tangent_len,
            depth,
        )
        _append_box_data_exp(vertices, faces, box)

    vertices = _apply_floor_contact_to_vertices_exp(
        vertices,
        data["world_floor_z"],
        bool(getattr(op, "floor_contact", False)),
    )
    return vertices, faces


def _radial_rings_mesh_exp(rings, segments, radius_x, radius_y, center):
    vertices = []
    faces = []
    ring_indices = []
    for radius_factor, z_value in rings:
        if radius_factor <= 1e-8:
            ring_indices.append([len(vertices)])
            vertices.append(center + Vector((0.0, 0.0, z_value)))
            continue

        indices = []
        for idx in range(segments):
            angle = (2.0 * math.pi * idx) / segments
            indices.append(len(vertices))
            vertices.append(center + Vector((
                math.cos(angle) * radius_x * radius_factor,
                math.sin(angle) * radius_y * radius_factor,
                z_value,
            )))
        ring_indices.append(indices)

    for idx in range(len(ring_indices) - 1):
        current = ring_indices[idx]
        next_ring = ring_indices[idx + 1]
        if len(current) == 1 and len(next_ring) > 1:
            pole = current[0]
            for seg in range(segments):
                faces.append((pole, next_ring[seg], next_ring[(seg + 1) % segments]))
        elif len(next_ring) == 1 and len(current) > 1:
            pole = next_ring[0]
            for seg in range(segments):
                faces.append((current[seg], pole, current[(seg + 1) % segments]))
        else:
            for seg in range(segments):
                faces.append((
                    current[seg],
                    current[(seg + 1) % segments],
                    next_ring[(seg + 1) % segments],
                    next_ring[seg],
                ))

    return vertices, faces


def _sphere_mesh_from_data_exp(data, op):
    size = data["size"]
    center = data["center"] + _collider_exp_vec_from_props_exp(op, "offset")
    scale_vec = _collider_exp_scale_vec_exp(op)
    minimum_size = max(float(getattr(op, "minimum_size", 0.0)), 1e-6)
    base_radius = max(abs(size.x), abs(size.y), abs(size.z), minimum_size) * 0.5
    radius_x = max(base_radius * scale_vec.x, minimum_size * 0.5)
    radius_y = max(base_radius * scale_vec.y, minimum_size * 0.5)
    radius_z = max(base_radius * scale_vec.z, minimum_size * 0.5)
    segments = max(8, min(int(getattr(op, "sphere_segments", 16)), 64))
    lat_segments = max(4, segments // 2)

    rings = [(0.0, -radius_z)]
    for lat in range(1, lat_segments):
        phi = -math.pi * 0.5 + math.pi * (lat / lat_segments)
        rings.append((math.cos(phi), math.sin(phi) * radius_z))
    rings.append((0.0, radius_z))

    local_vertices, faces = _radial_rings_mesh_exp(rings, segments, radius_x, radius_y, center)
    world_vertices = [data["matrix_world"] @ point for point in local_vertices]
    world_vertices = _apply_floor_contact_to_vertices_exp(
        world_vertices,
        data["world_floor_z"],
        bool(getattr(op, "floor_contact", False)),
    )
    return world_vertices, faces


def _collider_exp_points_world_bounds_exp(points):
    min_v, max_v = _bounds_from_points_exp(points)
    center = (min_v + max_v) * 0.5
    return min_v, max_v, center


def _collider_exp_world_points_from_data_exp(data):
    local_points = data.get("local_points") or []
    if local_points:
        return [data["matrix_world"] @ point for point in local_points]
    min_v = data.get("min")
    max_v = data.get("max")
    if min_v is not None and max_v is not None:
        return [
            data["matrix_world"] @ Vector((x, y, z))
            for x in (min_v.x, max_v.x)
            for y in (min_v.y, max_v.y)
            for z in (min_v.z, max_v.z)
        ]
    return []


def _collider_exp_largest_bounds_axis_exp(size):
    axis_index = max(range(3), key=lambda axis: abs(size[axis]))
    axis = Vector((0.0, 0.0, 0.0))
    axis[axis_index] = 1.0
    return axis


def _collider_exp_principal_axis_exp(points):
    if len(points) < 2:
        return Vector((0.0, 0.0, 1.0))

    min_v, max_v, center = _collider_exp_points_world_bounds_exp(points)
    fallback = _collider_exp_largest_bounds_axis_exp(max_v - min_v)
    centered = [point - center for point in points]

    xx = sum(vec.x * vec.x for vec in centered)
    xy = sum(vec.x * vec.y for vec in centered)
    xz = sum(vec.x * vec.z for vec in centered)
    yy = sum(vec.y * vec.y for vec in centered)
    yz = sum(vec.y * vec.z for vec in centered)
    zz = sum(vec.z * vec.z for vec in centered)
    if max(xx, yy, zz) <= 1e-12:
        return fallback

    axis = fallback.normalized()
    for _idx in range(16):
        next_axis = Vector((
            xx * axis.x + xy * axis.y + xz * axis.z,
            xy * axis.x + yy * axis.y + yz * axis.z,
            xz * axis.x + yz * axis.y + zz * axis.z,
        ))
        if next_axis.length <= 1e-12:
            return fallback
        axis = next_axis.normalized()

    if axis.z < 0.0:
        axis.negate()
    return axis


def _collider_exp_basis_from_axis_exp(axis):
    axis_z = axis.normalized() if axis.length > 1e-12 else Vector((0.0, 0.0, 1.0))
    reference = Vector((0.0, 0.0, 1.0))
    if abs(axis_z.dot(reference)) > 0.92:
        reference = Vector((1.0, 0.0, 0.0))
    axis_x = reference.cross(axis_z)
    if axis_x.length <= 1e-12:
        axis_x = Vector((1.0, 0.0, 0.0))
    else:
        axis_x.normalize()
    axis_y = axis_z.cross(axis_x)
    if axis_y.length <= 1e-12:
        axis_y = Vector((0.0, 1.0, 0.0))
    else:
        axis_y.normalize()
    return axis_x, axis_y, axis_z


def _collider_exp_oriented_capsule_frame_exp(data):
    world_points = _collider_exp_world_points_from_data_exp(data)
    if not world_points:
        center = data["matrix_world"] @ data["center"]
        return center, Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0)), data["size"]

    axis = _collider_exp_principal_axis_exp(world_points)
    axis_x, axis_y, axis_z = _collider_exp_basis_from_axis_exp(axis)
    projections = []
    for point in world_points:
        projections.append((point.dot(axis_x), point.dot(axis_y), point.dot(axis_z)))

    min_x = min(item[0] for item in projections)
    max_x = max(item[0] for item in projections)
    min_y = min(item[1] for item in projections)
    max_y = max(item[1] for item in projections)
    min_z = min(item[2] for item in projections)
    max_z = max(item[2] for item in projections)
    center = (
        axis_x * ((min_x + max_x) * 0.5)
        + axis_y * ((min_y + max_y) * 0.5)
        + axis_z * ((min_z + max_z) * 0.5)
    )
    size = Vector((max_x - min_x, max_y - min_y, max_z - min_z))
    return center, axis_x, axis_y, axis_z, size


def _capsule_mesh_from_data_exp(data, op):
    follow_source_angle = bool(getattr(op, "capsule_follow_source_angle", False))
    if follow_source_angle:
        center, axis_x, axis_y, axis_z, size = _collider_exp_oriented_capsule_frame_exp(data)
        center = center + _collider_exp_vec_from_props_exp(op, "offset")
        matrix_world = None
    else:
        axis_x = axis_y = axis_z = None
        matrix_world = None

    vertical_align = bool(getattr(op, "capsule_vertical_align", True))
    if follow_source_angle:
        pass
    elif vertical_align:
        matrix_world = Matrix.Identity(4)
        local_points = data.get("local_points") or []
        if local_points:
            world_points = [data["matrix_world"] @ point for point in local_points]
            min_v, max_v = _bounds_from_points_exp(world_points)
            center = (min_v + max_v) * 0.5
            size = max_v - min_v
        else:
            center = data["matrix_world"] @ data["center"]
            size = data["size"]
    else:
        matrix_world = data["matrix_world"]
        center = data["center"]
        size = data["size"]

    if not follow_source_angle:
        center = center + _collider_exp_vec_from_props_exp(op, "offset")
    scale_vec = _collider_exp_scale_vec_exp(op)
    minimum_size = max(float(getattr(op, "minimum_size", 0.0)), 1e-6)
    radius_value = max(float(getattr(op, "capsule_radius", 0.5)), 0.001)
    height_value = max(float(getattr(op, "capsule_height", 2.0)), 0.001)
    cap_value = max(float(getattr(op, "capsule_cap_size", 0.5)), 0.001)

    radius_auto = abs(radius_value - 0.5) <= 1e-6
    height_auto = abs(height_value - 2.0) <= 1e-6
    cap_auto = abs(cap_value - 0.5) <= 1e-6

    if radius_auto:
        radius_x = max(abs(size.x) * 0.5 * scale_vec.x, minimum_size * 0.5)
        radius_y = max(abs(size.y) * 0.5 * scale_vec.y, minimum_size * 0.5)
    else:
        radius_x = max(radius_value * scale_vec.x, minimum_size * 0.5)
        radius_y = max(radius_value * scale_vec.y, minimum_size * 0.5)

    if height_auto:
        total_height = max(abs(size.z) * scale_vec.z, minimum_size)
    else:
        total_height = max(height_value * scale_vec.z, minimum_size)

    if cap_auto:
        cap_z = max(min(max(radius_x, radius_y), total_height * 0.5), minimum_size * 0.5)
    else:
        cap_z = min(max(cap_value * scale_vec.z, minimum_size * 0.5), total_height * 0.5)
    body_half = max(0.0, (total_height - cap_z * 2.0) * 0.5)
    segments = 16
    cap_rings = 4

    rings = [(0.0, -body_half - cap_z)]
    for idx in range(1, cap_rings + 1):
        phi = -math.pi * 0.5 + (math.pi * 0.5) * (idx / cap_rings)
        rings.append((max(math.cos(phi), 0.0), -body_half + math.sin(phi) * cap_z))
    rings.append((1.0, body_half))
    for idx in range(1, cap_rings + 1):
        phi = (math.pi * 0.5) * (idx / cap_rings)
        rings.append((max(math.cos(phi), 0.0), body_half + math.sin(phi) * cap_z))

    local_vertices, faces = _radial_rings_mesh_exp(rings, segments, radius_x, radius_y, center)
    if follow_source_angle:
        world_vertices = [
            center + axis_x * (point.x - center.x) + axis_y * (point.y - center.y) + axis_z * (point.z - center.z)
            for point in local_vertices
        ]
    else:
        world_vertices = [matrix_world @ point for point in local_vertices]
    world_vertices = _apply_floor_contact_to_vertices_exp(
        world_vertices,
        data["world_floor_z"],
        bool(getattr(op, "floor_contact", False)),
    )
    return world_vertices, faces


from .nh_base import (_COLLIDER_TARGET_LOD_ITEMS)

class CRAY_OT_EnsureColliderLODExp(Operator):
    """Create or find the experimental target Geometry LOD object"""

    bl_idname = "cray.ensure_collider_lod_exp"
    bl_label = "Create/Find Collider LOD (exp)"
    bl_options = {"REGISTER", "UNDO"}

    target_lod: EnumProperty(name="Target LOD", items=_COLLIDER_TARGET_LOD_ITEMS, default="6")

    def invoke(self, context, event):
        from .nh_collider import (_collider_exp_settings_exp)
        del event
        settings = _collider_exp_settings_exp(context)
        _copy_collider_exp_settings_to_operator_exp(
            self,
            settings,
            prop_names=("target_lod",),
        )
        return self.execute(context)

    def draw(self, context):
        del context
        self.layout.use_property_split = True
        self.layout.use_property_decorate = False
        self.layout.prop(self, "target_lod")

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        settings = _require_collider_exp_enabled_exp(self, context)
        if settings is None:
            return {"CANCELLED"}

        source_obj = _resolve_collider_exp_source_object_exp(context, getattr(settings, "source_object", None))
        if source_obj is None:
            self.report({"ERROR"}, "Select a mesh source object")
            return {"CANCELLED"}
        try:
            target_obj = _ensure_collider_exp_target_object_exp(context, settings, source_obj, op=self)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        _write_collider_exp_operator_to_settings_exp(
            self,
            settings,
            prop_names=("target_lod",),
        )
        self.report({"INFO"}, f"Experimental collider LOD ready: {target_obj.name}")
        return {"FINISHED"}


from .nh_base import (_COLLIDER_TARGET_LOD_ITEMS)

class CRAY_OT_GenerateBoxColliderExp(Operator):
    """Generate an experimental box collider into the target Geometry LOD"""

    bl_idname = "cray.generate_box_collider_exp"
    bl_label = "Generate Box"
    bl_options = {"REGISTER", "UNDO"}

    target_lod: EnumProperty(name="Target LOD", items=_COLLIDER_TARGET_LOD_ITEMS, default="6")
    scale_x: FloatProperty(name="Scale X", default=1.0, min=0.001)
    scale_y: FloatProperty(name="Scale Y", default=1.0, min=0.001)
    scale_z: FloatProperty(name="Scale Z", default=1.0, min=0.001)
    scale_multiplier: FloatProperty(name="Scale Multiplier", default=1.0, min=0.001)
    offset_x: FloatProperty(name="Offset X", default=0.0)
    offset_y: FloatProperty(name="Offset Y", default=0.0)
    offset_z: FloatProperty(name="Offset Z", default=0.0)
    floor_contact: BoolProperty(name="Floor Contact", default=False)
    minimum_size: FloatProperty(name="Minimum Size", default=0.05, min=0.0)
    normal_minimum_size: BoolProperty(
        name="Normal Min Size",
        description="For flat box sources, add missing Minimum Size thickness opposite to the averaged face normal instead of centering it",
        default=False,
    )
    merge_distance: FloatProperty(name="Merge Distance", default=0.0, min=0.0)
    recalc_normals: BoolProperty(name="Recalculate Normals", default=True)

    def invoke(self, context, event):
        from .nh_collider import (_collider_exp_settings_exp)
        del event
        _copy_collider_exp_settings_to_operator_exp(self, _collider_exp_settings_exp(context))
        return self.execute(context)

    def draw(self, context):
        del context
        _draw_collider_exp_operator_panel_exp(self.layout, self)

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider import (_COLLIDER_EXP_BOX_FACES)
        from .nh_textures import (_ensure_collider_placeholder_material)
        settings = _require_collider_exp_enabled_exp(self, context)
        if settings is None:
            return {"CANCELLED"}
        try:
            target_obj, source_obj, data_items = _prepare_collider_exp_scope_build_exp(
                context,
                settings,
                self,
                bounds_only=False,
            )
            material_index, material_name = _ensure_collider_placeholder_material(
                target_obj,
                source_obj,
                data_items=data_items,
            )
            stats = _collider_exp_empty_stats_exp()
            for data in data_items:
                vertices = _box_vertices_from_bounds_data_exp(data, self)
                part_stats = _append_collider_exp_mesh_to_object_exp(
                    target_obj,
                    vertices,
                    _COLLIDER_EXP_BOX_FACES,
                    merge_distance=self.merge_distance,
                    recalc_normals=bool(self.recalc_normals),
                    material_index=material_index,
                )
                _merge_collider_exp_stats_exp(stats, part_stats)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        _write_collider_exp_operator_to_settings_exp(self, settings)
        try:
            settings.exp_mode = "BOX"
        except Exception:
            pass
        _set_collider_exp_custom_props_exp(
            target_obj,
            "BOX",
            source_obj,
            {
                "vertex_indices": stats.get("vertex_indices", []),
                "face_indices": stats.get("face_indices", []),
                "material_name": material_name,
                "scope": str(getattr(settings, "collider_scope", "FROM_SELECTED")),
                "parts": len(data_items),
            },
        )
        self.report({"INFO"}, f"Generated {len(data_items)} box collider part(s) in {target_obj.name}: +{stats['verts_added']} verts, +{stats['faces_added']} faces")
        return {"FINISHED"}


from .nh_base import (_COLLIDER_TARGET_LOD_ITEMS)

class CRAY_OT_GenerateConvexHullColliderExp(Operator):
    """Generate an experimental convex hull into the target Geometry LOD"""

    bl_idname = "cray.generate_convex_hull_collider_exp"
    bl_label = "Generate Convex Hull"
    bl_options = {"REGISTER", "UNDO"}

    target_lod: EnumProperty(name="Target LOD", items=_COLLIDER_TARGET_LOD_ITEMS, default="6")
    scale_x: FloatProperty(name="Scale X", default=1.0, min=0.001)
    scale_y: FloatProperty(name="Scale Y", default=1.0, min=0.001)
    scale_z: FloatProperty(name="Scale Z", default=1.0, min=0.001)
    scale_multiplier: FloatProperty(name="Scale Multiplier", default=1.0, min=0.001)
    offset_x: FloatProperty(name="Offset X", default=0.0)
    offset_y: FloatProperty(name="Offset Y", default=0.0)
    offset_z: FloatProperty(name="Offset Z", default=0.0)
    floor_contact: BoolProperty(name="Floor Contact", default=False)
    minimum_size: FloatProperty(name="Minimum Size", default=0.05, min=0.0)
    merge_distance: FloatProperty(name="Merge Distance", default=0.0, min=0.0)
    recalc_normals: BoolProperty(name="Recalculate Normals", default=True)
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

    def invoke(self, context, event):
        from .nh_collider import (_collider_exp_settings_exp)
        del event
        props = _collider_exp_operator_props_exp(("convex_detail", "convex_max_triangles"))
        _copy_collider_exp_settings_to_operator_exp(self, _collider_exp_settings_exp(context), prop_names=props)
        return self.execute(context)

    def draw(self, context):
        del context
        _draw_collider_exp_operator_panel_exp(
            self.layout,
            self,
            ("convex_detail", "convex_max_triangles"),
            extra_label="Convex Hull",
        )

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider import (_allow_collider_exp_in_place_target_exp)
        from .nh_textures import (_ensure_collider_placeholder_material)
        settings = _require_collider_exp_enabled_exp(self, context)
        if settings is None:
            return {"CANCELLED"}
        try:
            target_obj, source_obj, data_items = _prepare_collider_exp_scope_build_exp(
                context,
                settings,
                self,
                bounds_only=False,
            )
            material_index, material_name = _ensure_collider_placeholder_material(
                target_obj,
                source_obj,
                data_items=data_items,
            )
            stats = _collider_exp_empty_stats_exp()
            if target_obj == source_obj and _allow_collider_exp_in_place_target_exp(
                target_obj,
                str(getattr(self, "target_lod", getattr(settings, "target_lod", "6")) or "6"),
            ):
                part_stats = _build_collider_exp_hull_from_selected_loose_verts_in_place_exp(
                    context,
                    target_obj,
                    self,
                    material_index=material_index,
                )
                _merge_collider_exp_stats_exp(stats, part_stats)
            else:
                for data in data_items:
                    world_points = _transform_collider_exp_local_points_exp(data, self)
                    part_stats = _append_collider_exp_hull_to_object_exp(
                        target_obj,
                        world_points,
                        self,
                        material_index=material_index,
                    )
                    _merge_collider_exp_stats_exp(stats, part_stats)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        props = _collider_exp_operator_props_exp(("convex_detail", "convex_max_triangles"))
        try:
            settings.geometry_object = target_obj
        except Exception:
            pass
        _write_collider_exp_operator_to_settings_exp(self, settings, prop_names=props)
        try:
            settings.exp_mode = "CONVEX_HULL"
        except Exception:
            pass
        _set_collider_exp_custom_props_exp(
            target_obj,
            "CONVEX_HULL",
            source_obj,
            {
                "vertex_indices": stats.get("vertex_indices", []),
                "face_indices": stats.get("face_indices", []),
                "material_name": material_name,
                "scope": str(getattr(settings, "collider_scope", "FROM_SELECTED")),
                "parts": len(data_items),
                "convex_detail": int(self.convex_detail),
                "convex_max_triangles": int(self.convex_max_triangles),
                "actual_detail": int(stats.get("actual_detail", self.convex_detail)),
                "triangles": int(stats.get("triangles", 0)),
                **(
                    {
                        "matrix_world": _matrix_to_list_exp(data_items[0]["matrix_world"]),
                        "local_points": _points_to_list_exp(data_items[0]["local_points"]),
                    }
                    if len(data_items) == 1
                    else {}
                ),
            },
        )
        report_level = {"WARNING"} if (
            int(stats.get("max_triangles", 0)) > 0
            and int(stats.get("triangles", 0)) > int(stats.get("max_triangles", 0))
        ) else {"INFO"}
        self.report(
            report_level,
            (
                f"Generated convex hull in {target_obj.name}: "
                f"{len(data_items)} part(s), +{stats['verts_added']} verts, +{stats['faces_added']} faces, {stats.get('triangles', 0)} tris"
            ),
        )
        return {"FINISHED"}


from .nh_base import (_COLLIDER_TARGET_LOD_ITEMS)

class CRAY_OT_RebuildConvexHullColliderExp(Operator):
    """Rebuild the last experimental convex hull with the current settings"""

    bl_idname = "cray.rebuild_convex_hull_collider_exp"
    bl_label = "Simplify/Rebuild Convex Hull"
    bl_options = {"REGISTER", "UNDO"}

    target_lod: EnumProperty(name="Target LOD", items=_COLLIDER_TARGET_LOD_ITEMS, default="6")
    scale_x: FloatProperty(name="Scale X", default=1.0, min=0.001)
    scale_y: FloatProperty(name="Scale Y", default=1.0, min=0.001)
    scale_z: FloatProperty(name="Scale Z", default=1.0, min=0.001)
    scale_multiplier: FloatProperty(name="Scale Multiplier", default=1.0, min=0.001)
    offset_x: FloatProperty(name="Offset X", default=0.0)
    offset_y: FloatProperty(name="Offset Y", default=0.0)
    offset_z: FloatProperty(name="Offset Z", default=0.0)
    floor_contact: BoolProperty(name="Floor Contact", default=False)
    minimum_size: FloatProperty(name="Minimum Size", default=0.05, min=0.0)
    merge_distance: FloatProperty(name="Merge Distance", default=0.0, min=0.0)
    recalc_normals: BoolProperty(name="Recalculate Normals", default=True)
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

    def invoke(self, context, event):
        from .nh_collider import (_collider_exp_settings_exp)
        del event
        props = _collider_exp_operator_props_exp(("convex_detail", "convex_max_triangles"))
        _copy_collider_exp_settings_to_operator_exp(self, _collider_exp_settings_exp(context), prop_names=props)
        return self.execute(context)

    def draw(self, context):
        del context
        _draw_collider_exp_operator_panel_exp(
            self.layout,
            self,
            ("convex_detail", "convex_max_triangles"),
            extra_label="Simplify Convex Hull",
        )

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider import (_COLLIDER_EXP_SOURCE_PROP, _COLLIDER_EXP_TYPE_PROP)
        from .nh_snap import (_deselect_all_in_view_layer, _select_object_in_view_layer)
        from .nh_textures import (_collection_directly_contains_object)
        settings = _require_collider_exp_enabled_exp(self, context)
        if settings is None:
            return {"CANCELLED"}
        target_obj, target_source = _resolve_collider_exp_convex_hull_target_exp(context, settings)
        if target_obj is None or target_obj.type != "MESH":
            self.report({"ERROR"}, "No convex hull collision found. Select a generated hull or create one first")
            return {"CANCELLED"}
        if target_obj.mode == "EDIT":
            self.report({"ERROR"}, "Leave Edit Mode before simplifying a convex hull")
            return {"CANCELLED"}

        try:
            exp_type = str(target_obj.get(_COLLIDER_EXP_TYPE_PROP, ""))
            if exp_type != "CONVEX_HULL":
                raise RuntimeError("Selected object is not an experimental convex hull")
            original_name = target_obj.name
            original_mesh_name = target_obj.data.name if target_obj.data is not None else ""
            original_collections = list(getattr(target_obj, "users_collection", []))
            params, matrix_world, local_points, replace_whole_object = _get_collider_exp_hull_rebuild_data_exp(target_obj)
            if len(local_points) < 4:
                raise RuntimeError("Stored convex hull source has fewer than 4 points")
            min_v, max_v = _bounds_from_points_exp(local_points)
            data = {
                "source_obj": None,
                "matrix_world": matrix_world,
                "local_points": local_points,
                "face_centers_local": [],
                "min": min_v,
                "max": max_v,
                "center": (min_v + max_v) * 0.5,
                "size": max_v - min_v,
                "world_floor_z": min((matrix_world @ point).z for point in local_points),
            }
            world_points = _transform_collider_exp_local_points_exp(data, self)
            build = _build_collider_exp_hull_data_for_budget_exp(target_obj, world_points, self)
            vertex_indices = params.get("vertex_indices", [])
            if replace_whole_object or not vertex_indices:
                _delete_all_collider_exp_vertices_exp(target_obj)
                _clear_collider_exp_history_exp(target_obj)
            else:
                _delete_collider_exp_vertices_exp(target_obj, vertex_indices)
            stats = _append_collider_exp_hull_data_to_object_exp(
                target_obj,
                build["hull_data"],
                recalc_normals=bool(getattr(self, "recalc_normals", True)),
            )
            stats = _apply_collider_exp_hull_build_stats_exp(stats, build)
            target_obj.name = original_name
            if target_obj.data is not None and original_mesh_name:
                target_obj.data.name = original_mesh_name
            for collection in original_collections:
                if collection is not None and not _collection_directly_contains_object(collection, target_obj):
                    collection.objects.link(target_obj)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        props = _collider_exp_operator_props_exp(("convex_detail", "convex_max_triangles"))
        try:
            settings.geometry_object = target_obj
        except Exception:
            pass
        _write_collider_exp_operator_to_settings_exp(self, settings, prop_names=props)
        try:
            settings.geometry_object = target_obj
            settings.exp_mode = "CONVEX_HULL"
        except Exception:
            pass
        source_name = str(target_obj.get(_COLLIDER_EXP_SOURCE_PROP, "") or "")
        source_obj = bpy.data.objects.get(source_name) if source_name else None
        _set_collider_exp_custom_props_exp(
            target_obj,
            "CONVEX_HULL",
            source_obj,
            {
                "matrix_world": _matrix_to_list_exp(matrix_world),
                "local_points": _points_to_list_exp(local_points),
                "vertex_indices": stats.get("vertex_indices", []),
                "face_indices": stats.get("face_indices", []),
                "convex_detail": int(self.convex_detail),
                "convex_max_triangles": int(self.convex_max_triangles),
                "actual_detail": int(stats.get("actual_detail", self.convex_detail)),
                "triangles": int(stats.get("triangles", 0)),
                "uuid": params.get("uuid"),
            },
        )
        try:
            _deselect_all_in_view_layer(context)
            _select_object_in_view_layer(context, target_obj, active=True)
        except Exception:
            pass
        report_level = {"WARNING"} if (
            int(stats.get("max_triangles", 0)) > 0
            and int(stats.get("triangles", 0)) > int(stats.get("max_triangles", 0))
        ) else {"INFO"}
        source_note = "active hull" if target_source == "active" else "last hull"
        self.report(
            report_level,
            (
                f"Rebuilt {source_note} in {target_obj.name}: "
                f"+{stats['verts_added']} verts, +{stats['faces_added']} faces, {stats.get('triangles', 0)} tris"
            ),
        )
        return {"FINISHED"}


class CRAY_OT_ReconvexSelectedComponentsExp(Operator):
    """Replace selected connected mesh components with one convex hull"""

    bl_idname = "cray.reconvex_selected_components_exp"
    bl_label = "Re-Convex Selected Components"
    bl_description = "In Edit Mode, expand the current selection to touched connected face islands and replace them with one convex hull"
    bl_options = {"REGISTER", "UNDO"}

    merge_distance: FloatProperty(name="Merge Distance", default=0.0, min=0.0)
    recalc_normals: BoolProperty(name="Recalculate Normals", default=True)
    convex_detail: IntProperty(
        name="Hull Detail",
        description="Simplification/detail level for the merged convex hull",
        default=16,
        min=4,
        max=128,
    )
    convex_max_triangles: IntProperty(
        name="Max Hull Triangles",
        description="Triangle budget used when simplifying the merged convex hull",
        default=64,
        min=4,
        max=2048,
    )

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "edit_object", None)
        return obj is not None and getattr(obj, "type", None) == "MESH" and getattr(obj, "mode", "") == "EDIT"

    def invoke(self, context, event):
        from .nh_collider import (_collider_exp_settings_exp)
        del event
        props = ("merge_distance", "recalc_normals", "convex_detail", "convex_max_triangles")
        _copy_collider_exp_settings_to_operator_exp(self, _collider_exp_settings_exp(context), prop_names=props)
        return self.execute(context)

    def draw(self, context):
        del context
        self.layout.prop(self, "convex_detail")
        self.layout.prop(self, "convex_max_triangles")
        self.layout.prop(self, "merge_distance")
        self.layout.prop(self, "recalc_normals")

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider import (_force_edit_mesh_view_refresh_exp, _most_common_material_index_from_faces, _replace_face_islands_with_clean_hull_in_edit_object, _selected_face_islands_for_reconvex, _vector_quantized_key)
        target_obj = getattr(context, "edit_object", None) or getattr(context, "active_object", None)
        if target_obj is None or getattr(target_obj, "type", None) != "MESH" or getattr(target_obj, "mode", "") != "EDIT":
            self.report({"ERROR"}, "Select component faces/verts on a mesh in Edit Mode")
            return {"CANCELLED"}

        try:
            mesh = target_obj.data
            bm = bmesh.from_edit_mesh(mesh)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.faces.ensure_lookup_table()

            islands = _selected_face_islands_for_reconvex(bm)
            if not islands:
                raise RuntimeError("Select at least one face/edge/vertex on the component(s) to re-convex")

            island_faces = []
            seen_faces = set()
            for island in islands:
                for face in island:
                    if face in seen_faces or face is None or not face.is_valid:
                        continue
                    seen_faces.add(face)
                    island_faces.append(face)

            local_points = []
            seen_points = set()
            for face in island_faces:
                for vert in face.verts:
                    if vert is None or not vert.is_valid:
                        continue
                    key = _vector_quantized_key(vert.co)
                    if key in seen_points:
                        continue
                    seen_points.add(key)
                    local_points.append(vert.co.copy())
            if len(local_points) < 4:
                raise RuntimeError("Selected component vertices collapse below 4 unique points")

            world_points = [target_obj.matrix_world @ point for point in local_points]
            build = _build_collider_exp_hull_data_for_budget_exp(target_obj, world_points, self)
            material_index = _most_common_material_index_from_faces(island_faces)
            stats = _replace_face_islands_with_clean_hull_in_edit_object(
                context,
                target_obj,
                build["hull_data"],
                island_faces,
                material_index=material_index,
                recalc_normals=bool(self.recalc_normals),
            )
            _apply_collider_exp_hull_build_stats_exp(stats, build)
            _force_edit_mesh_view_refresh_exp(context, target_obj)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        report_level = {"WARNING"} if (
            int(stats.get("max_triangles", 0)) > 0
            and int(stats.get("triangles", 0)) > int(stats.get("max_triangles", 0))
        ) else {"INFO"}
        self.report(
            report_level,
            (
                f"Re-convexed {len(islands)} component(s) in {target_obj.name}: "
                f"-{stats.get('faces_removed', 0)} faces, +{stats.get('faces_added', 0)} faces, "
                f"{stats.get('triangles', 0)} tris"
            ),
        )
        return {"FINISHED"}


class CRAY_OT_DeleteLastColliderExp(Operator):
    """Delete the most recently generated experimental collider geometry"""

    bl_idname = "cray.delete_last_collider_exp"
    bl_label = "Delete Last Created Collider"
    bl_description = "Deletes one collider geometry item from the last-created history, keeping up to 30 items"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider import (_collider_exp_settings_exp)
        settings = _collider_exp_settings_exp(context)
        target_obj, params = _resolve_last_collider_exp_target_exp(context, settings)
        if target_obj is None:
            self.report({"ERROR"}, "No last created collider geometry found")
            return {"CANCELLED"}

        try:
            stats = _delete_collider_exp_vertices_any_mode_exp(target_obj, params.get("vertex_indices", []))
            remaining = _pop_last_collider_exp_history_entry_exp(target_obj)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Deleted last created collider from {target_obj.name}: "
                f"-{stats.get('verts_removed', 0)} verts, {remaining} stored item(s) left"
            ),
        )
        return {"FINISHED"}


class CRAY_OT_SelectConnectedShellFromSelectionExp(Operator):
    """Select the full connected mesh shell from the current Edit Mode selection"""

    bl_idname = "cray.select_connected_shell_from_selection_exp"
    bl_label = "Select Connected Shell"
    bl_description = "In Edit Mode, selects the full connected shell from the currently selected face, edge, or vertex"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "edit_object", None)
        return obj is not None and getattr(obj, "type", None) == "MESH" and getattr(obj, "mode", "") == "EDIT"

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        obj = getattr(context, "edit_object", None)
        if obj is None or getattr(obj, "type", None) != "MESH":
            self.report({"ERROR"}, "Open a mesh in Edit Mode and select part of a shell")
            return {"CANCELLED"}

        try:
            bm = bmesh.from_edit_mesh(obj.data)
            if not any(
                (vert.is_valid and vert.select)
                for vert in bm.verts
            ) and not any(
                (edge.is_valid and edge.select)
                for edge in bm.edges
            ) and not any(
                (face.is_valid and face.select)
                for face in bm.faces
            ):
                raise RuntimeError("Select a face, edge, or vertex first")
            result = bpy.ops.mesh.select_linked(delimit=set())
        except TypeError:
            result = bpy.ops.mesh.select_linked()
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        if "FINISHED" not in set(result or []):
            self.report({"WARNING"}, "Could not select linked shell")
            return {"CANCELLED"}
        self.report({"INFO"}, "Selected connected shell")
        return {"FINISHED"}


from .nh_base import (_COLLIDER_TARGET_LOD_ITEMS)

class CRAY_OT_CreateCylinderGuideColliderExp(Operator):
    """Create a cylinder collider directly in the target Geometry LOD"""

    bl_idname = "cray.create_cylinder_guide_collider_exp"
    bl_label = "Create Cylinder"
    bl_options = {"REGISTER", "UNDO"}

    target_lod: EnumProperty(name="Target LOD", items=_COLLIDER_TARGET_LOD_ITEMS, default="6")
    scale_x: FloatProperty(name="Scale X", default=1.0, min=0.001)
    scale_y: FloatProperty(name="Scale Y", default=1.0, min=0.001)
    scale_z: FloatProperty(name="Scale Z", default=1.0, min=0.001)
    scale_multiplier: FloatProperty(name="Scale Multiplier", default=1.0, min=0.001)
    offset_x: FloatProperty(name="Offset X", default=0.0)
    offset_y: FloatProperty(name="Offset Y", default=0.0)
    offset_z: FloatProperty(name="Offset Z", default=0.0)
    floor_contact: BoolProperty(name="Floor Contact", default=False)
    minimum_size: FloatProperty(name="Minimum Size", default=0.05, min=0.0)
    merge_distance: FloatProperty(name="Merge Distance", default=0.0, min=0.0)
    recalc_normals: BoolProperty(name="Recalculate Normals", default=True)
    cylinder_segments: IntProperty(name="Cylinder Segments", default=16, min=4, max=128)

    def invoke(self, context, event):
        from .nh_collider import (_collider_exp_settings_exp)
        del event
        props = _collider_exp_operator_props_exp(("cylinder_segments",))
        _copy_collider_exp_settings_to_operator_exp(self, _collider_exp_settings_exp(context), prop_names=props)
        return self.execute(context)

    def draw(self, context):
        del context
        _draw_collider_exp_operator_panel_exp(
            self.layout,
            self,
            ("cylinder_segments",),
            extra_label="Cylinder",
        )

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_textures import (_ensure_collider_placeholder_material)
        settings = _require_collider_exp_enabled_exp(self, context)
        if settings is None:
            return {"CANCELLED"}
        source_was_edit = False
        source_obj = None
        try:
            target_obj, source_obj, data_items = _prepare_collider_exp_scope_build_exp(
                context,
                settings,
                self,
                bounds_only=False,
            )
            source_was_edit = getattr(source_obj, "mode", "") == "EDIT"
            material_index, material_name = _ensure_collider_placeholder_material(
                target_obj,
                source_obj,
                data_items=data_items,
            )
            if getattr(source_obj, "mode", "OBJECT") != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            stats = _collider_exp_empty_stats_exp()
            for data in data_items:
                vertices, faces = _cylinder_guide_mesh_from_data_exp(data, self)
                vertices = _collider_exp_local_vertices_to_world_exp(data, vertices, self)
                part_stats = _append_collider_exp_mesh_to_object_exp(
                    target_obj,
                    vertices,
                    faces,
                    merge_distance=self.merge_distance,
                    recalc_normals=bool(self.recalc_normals),
                    material_index=material_index,
                )
                _merge_collider_exp_stats_exp(stats, part_stats)
            _restore_collider_exp_source_context_exp(context, source_obj, restore_edit_mode=source_was_edit)
        except Exception as e:
            try:
                _restore_collider_exp_source_context_exp(context, source_obj, restore_edit_mode=source_was_edit)
            except Exception:
                pass
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        props = _collider_exp_operator_props_exp(("cylinder_segments",))
        _write_collider_exp_operator_to_settings_exp(self, settings, prop_names=props)
        try:
            settings.exp_mode = "CYLINDER_BOXES"
        except Exception:
            pass
        _set_collider_exp_custom_props_exp(
            target_obj,
            "CYLINDER",
            source_obj,
            {
                "vertex_indices": stats.get("vertex_indices", []),
                "face_indices": stats.get("face_indices", []),
                "material_name": material_name,
                "segments": int(self.cylinder_segments),
                "scope": str(getattr(settings, "collider_scope", "FROM_SELECTED")),
                "parts": len(data_items),
            },
        )
        self.report({"INFO"}, f"Created {len(data_items)} cylinder collider part(s) in {target_obj.name}")
        return {"FINISHED"}


from .nh_base import (_COLLIDER_TARGET_LOD_ITEMS)

class CRAY_OT_CreatePipeGuideColliderExp(Operator):
    """Create a pipe collider directly in the target Geometry LOD"""

    bl_idname = "cray.create_pipe_guide_collider_exp"
    bl_label = "Create Pipe"
    bl_options = {"REGISTER", "UNDO"}

    target_lod: EnumProperty(name="Target LOD", items=_COLLIDER_TARGET_LOD_ITEMS, default="6")
    scale_x: FloatProperty(name="Scale X", default=1.0, min=0.001)
    scale_y: FloatProperty(name="Scale Y", default=1.0, min=0.001)
    scale_z: FloatProperty(name="Scale Z", default=1.0, min=0.001)
    scale_multiplier: FloatProperty(name="Scale Multiplier", default=1.0, min=0.001)
    offset_x: FloatProperty(name="Offset X", default=0.0)
    offset_y: FloatProperty(name="Offset Y", default=0.0)
    offset_z: FloatProperty(name="Offset Z", default=0.0)
    floor_contact: BoolProperty(name="Floor Contact", default=False)
    minimum_size: FloatProperty(name="Minimum Size", default=0.05, min=0.0)
    merge_distance: FloatProperty(name="Merge Distance", default=0.0, min=0.0)
    recalc_normals: BoolProperty(name="Recalculate Normals", default=True)
    pipe_segments: IntProperty(name="Pipe Segments", default=24, min=4, max=128)
    pipe_inner_radius: FloatProperty(name="Pipe Inner Radius", default=0.5, min=0.0, precision=4, unit="LENGTH")
    pipe_outer_radius: FloatProperty(name="Pipe Outer Radius", default=1.0, min=0.001, precision=4, unit="LENGTH")
    pipe_depth: FloatProperty(name="Pipe Depth", default=0.25, min=0.001, precision=4, unit="LENGTH")
    pipe_thickness: FloatProperty(name="Pipe Thickness", default=0.25, min=0.0, precision=4, unit="LENGTH")

    def invoke(self, context, event):
        from .nh_collider import (_collider_exp_settings_exp)
        del event
        props = _collider_exp_operator_props_exp((
            "pipe_segments",
            "pipe_inner_radius",
            "pipe_outer_radius",
            "pipe_depth",
            "pipe_thickness",
        ))
        _copy_collider_exp_settings_to_operator_exp(self, _collider_exp_settings_exp(context), prop_names=props)
        return self.execute(context)

    def draw(self, context):
        del context
        _draw_collider_exp_operator_panel_exp(
            self.layout,
            self,
            (
                "pipe_segments",
                "pipe_inner_radius",
                "pipe_outer_radius",
                "pipe_thickness",
                "pipe_depth",
            ),
            extra_label="Pipe",
        )

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_textures import (_ensure_collider_placeholder_material)
        settings = _require_collider_exp_enabled_exp(self, context)
        if settings is None:
            return {"CANCELLED"}
        source_was_edit = False
        source_obj = None
        try:
            target_obj, source_obj, data_items = _prepare_collider_exp_scope_build_exp(
                context,
                settings,
                self,
                bounds_only=True,
            )
            source_was_edit = getattr(source_obj, "mode", "") == "EDIT"
            material_index, material_name = _ensure_collider_placeholder_material(
                target_obj,
                source_obj,
                data_items=data_items,
            )
            if getattr(source_obj, "mode", "OBJECT") != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            stats = _collider_exp_empty_stats_exp()
            for data in data_items:
                vertices, faces = _pipe_guide_mesh_from_data_exp(data, self)
                vertices = _collider_exp_local_vertices_to_world_exp(data, vertices, self)
                part_stats = _append_collider_exp_mesh_to_object_exp(
                    target_obj,
                    vertices,
                    faces,
                    merge_distance=self.merge_distance,
                    recalc_normals=bool(self.recalc_normals),
                    material_index=material_index,
                )
                _merge_collider_exp_stats_exp(stats, part_stats)
            _restore_collider_exp_source_context_exp(context, source_obj, restore_edit_mode=source_was_edit)
        except Exception as e:
            try:
                _restore_collider_exp_source_context_exp(context, source_obj, restore_edit_mode=source_was_edit)
            except Exception:
                pass
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        props = _collider_exp_operator_props_exp((
            "pipe_segments",
            "pipe_inner_radius",
            "pipe_outer_radius",
            "pipe_depth",
            "pipe_thickness",
        ))
        _write_collider_exp_operator_to_settings_exp(self, settings, prop_names=props)
        try:
            settings.exp_mode = "PIPE_BOXES"
        except Exception:
            pass
        _set_collider_exp_custom_props_exp(
            target_obj,
            "PIPE",
            source_obj,
            {
                "vertex_indices": stats.get("vertex_indices", []),
                "face_indices": stats.get("face_indices", []),
                "material_name": material_name,
                "segments": int(self.pipe_segments),
                "inner_radius": float(self.pipe_inner_radius),
                "outer_radius": float(self.pipe_outer_radius),
                "thickness": float(self.pipe_thickness),
                "depth": float(self.pipe_depth),
                "scope": str(getattr(settings, "collider_scope", "FROM_SELECTED")),
                "parts": len(data_items),
            },
        )
        self.report({"INFO"}, f"Created {len(data_items)} pipe collider part(s) in {target_obj.name}")
        return {"FINISHED"}


from .nh_base import (_COLLIDER_TARGET_LOD_ITEMS)

class CRAY_OT_GenerateCylinderBoxesColliderExp(Operator):
    """Generate experimental box segments around a cylindrical form"""

    bl_idname = "cray.generate_cylinder_boxes_collider_exp"
    bl_label = "Generate Cylinder Boxes"
    bl_options = {"REGISTER", "UNDO"}

    target_lod: EnumProperty(name="Target LOD", items=_COLLIDER_TARGET_LOD_ITEMS, default="6")
    scale_x: FloatProperty(name="Scale X", default=1.0, min=0.001)
    scale_y: FloatProperty(name="Scale Y", default=1.0, min=0.001)
    scale_z: FloatProperty(name="Scale Z", default=1.0, min=0.001)
    scale_multiplier: FloatProperty(name="Scale Multiplier", default=1.0, min=0.001)
    offset_x: FloatProperty(name="Offset X", default=0.0)
    offset_y: FloatProperty(name="Offset Y", default=0.0)
    offset_z: FloatProperty(name="Offset Z", default=0.0)
    floor_contact: BoolProperty(name="Floor Contact", default=False)
    minimum_size: FloatProperty(name="Minimum Size", default=0.05, min=0.0)
    merge_distance: FloatProperty(name="Merge Distance", default=0.0, min=0.0)
    recalc_normals: BoolProperty(name="Recalculate Normals", default=True)
    cylinder_segments: IntProperty(name="Cylinder Segments", default=16, min=4, max=128)

    def invoke(self, context, event):
        from .nh_collider import (_collider_exp_settings_exp)
        del event
        props = ("target_lod", "minimum_size", "merge_distance", "recalc_normals")
        _copy_collider_exp_settings_to_operator_exp(self, _collider_exp_settings_exp(context), prop_names=props)
        return self.execute(context)

    def draw(self, context):
        del context
        _draw_collider_exp_guide_conversion_panel_exp(self.layout, self)

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider import (_COLLIDER_EXP_BOX_FACES)
        from .nh_textures import (_ensure_collider_placeholder_material)
        settings = _require_collider_exp_enabled_exp(self, context)
        if settings is None:
            return {"CANCELLED"}
        guide_obj = _resolve_collider_exp_source_object_exp(
            context,
            getattr(settings, "source_object", None) if settings is not None else None,
        )
        source_obj = None
        target_source_obj = None
        guide_mode = _is_collider_exp_guide_object_exp(guide_obj, "CYLINDER")
        restore_obj = None
        restore_edit = False
        try:
            if guide_mode:
                source_obj = guide_obj
                target_source_obj = _collider_exp_guide_source_object_exp(context, source_obj, settings) or source_obj
                restore_obj = target_source_obj
                if getattr(source_obj, "mode", "OBJECT") != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                target_obj = _ensure_collider_exp_target_object_exp(context, settings, target_source_obj, op=self)
                if target_obj == source_obj:
                    raise RuntimeError("Target Geometry LOD must be separate from the Source Object")
                data_items = [_collect_collider_exp_input_data_exp(context, source_obj, bounds_only=False)]
            else:
                target_obj, target_source_obj, data_items = _prepare_collider_exp_direct_boxes_build_exp(
                    context,
                    settings,
                    self,
                    bounds_only=False,
                )
                restore_obj = target_source_obj
                restore_edit = getattr(target_source_obj, "mode", "") == "EDIT"
                if getattr(target_source_obj, "mode", "OBJECT") != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")

            material_index, material_name = _ensure_collider_placeholder_material(
                target_obj,
                target_source_obj,
                data_items=[] if guide_mode else data_items,
            )
            stats = _collider_exp_empty_stats_exp()
            inner_factor = 0.0
            actual_segments = 0
            for data in data_items:
                vertices, faces, part_inner_factor = _cylinder_box_mesh_from_data_exp(data, self)
                part_stats = _append_collider_exp_mesh_to_object_exp(
                    target_obj,
                    vertices,
                    faces,
                    merge_distance=self.merge_distance,
                    recalc_normals=bool(self.recalc_normals),
                    material_index=material_index,
                )
                _merge_collider_exp_stats_exp(stats, part_stats)
                inner_factor = float(part_inner_factor)
                actual_segments += len(faces) // len(_COLLIDER_EXP_BOX_FACES)
            if guide_mode:
                _remove_collider_exp_guide_after_conversion_exp(context, source_obj)
            _restore_collider_exp_source_context_exp(context, restore_obj, restore_edit_mode=restore_edit)
        except Exception as e:
            try:
                _restore_collider_exp_source_context_exp(context, restore_obj, restore_edit_mode=restore_edit)
            except Exception:
                pass
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        props = ("target_lod", "minimum_size", "merge_distance", "recalc_normals")
        _write_collider_exp_operator_to_settings_exp(self, settings, prop_names=props)
        try:
            settings.exp_mode = "CYLINDER_BOXES"
        except Exception:
            pass
        _set_collider_exp_custom_props_exp(
            target_obj,
            "CYLINDER_BOXES",
            target_source_obj,
            {
                "vertex_indices": stats.get("vertex_indices", []),
                "face_indices": stats.get("face_indices", []),
                "material_name": material_name,
                "inner_factor": float(inner_factor),
                "segments": int(actual_segments),
                "scope": str(getattr(settings, "collider_scope", "FROM_SELECTED")),
                "parts": len(data_items),
            },
        )
        self.report({"INFO"}, f"Generated {actual_segments} cylinder box segments in {target_obj.name}")
        return {"FINISHED"}


from .nh_base import (_COLLIDER_TARGET_LOD_ITEMS)

class CRAY_OT_GeneratePipeBoxesColliderExp(Operator):
    """Generate experimental box segments around a ring or pipe"""

    bl_idname = "cray.generate_pipe_boxes_collider_exp"
    bl_label = "Generate Pipe Boxes"
    bl_options = {"REGISTER", "UNDO"}

    target_lod: EnumProperty(name="Target LOD", items=_COLLIDER_TARGET_LOD_ITEMS, default="6")
    scale_x: FloatProperty(name="Scale X", default=1.0, min=0.001)
    scale_y: FloatProperty(name="Scale Y", default=1.0, min=0.001)
    scale_z: FloatProperty(name="Scale Z", default=1.0, min=0.001)
    scale_multiplier: FloatProperty(name="Scale Multiplier", default=1.0, min=0.001)
    offset_x: FloatProperty(name="Offset X", default=0.0)
    offset_y: FloatProperty(name="Offset Y", default=0.0)
    offset_z: FloatProperty(name="Offset Z", default=0.0)
    floor_contact: BoolProperty(name="Floor Contact", default=False)
    minimum_size: FloatProperty(name="Minimum Size", default=0.05, min=0.0)
    merge_distance: FloatProperty(name="Merge Distance", default=0.0, min=0.0)
    recalc_normals: BoolProperty(name="Recalculate Normals", default=True)
    pipe_segments: IntProperty(name="Pipe Segments", default=24, min=4, max=128)
    pipe_inner_radius: FloatProperty(name="Pipe Inner Radius", default=0.5, min=0.0, precision=4, unit="LENGTH")
    pipe_outer_radius: FloatProperty(name="Pipe Outer Radius", default=1.0, min=0.001, precision=4, unit="LENGTH")
    pipe_depth: FloatProperty(name="Pipe Depth", default=0.25, min=0.001, precision=4, unit="LENGTH")
    pipe_thickness: FloatProperty(name="Pipe Thickness", default=0.25, min=0.0, precision=4, unit="LENGTH")

    def invoke(self, context, event):
        from .nh_collider import (_collider_exp_settings_exp)
        del event
        props = ("target_lod", "minimum_size", "merge_distance", "recalc_normals")
        _copy_collider_exp_settings_to_operator_exp(self, _collider_exp_settings_exp(context), prop_names=props)
        return self.execute(context)

    def draw(self, context):
        del context
        _draw_collider_exp_guide_conversion_panel_exp(self.layout, self)

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider import (_COLLIDER_EXP_BOX_FACES)
        from .nh_textures import (_ensure_collider_placeholder_material)
        settings = _require_collider_exp_enabled_exp(self, context)
        if settings is None:
            return {"CANCELLED"}
        guide_obj = _resolve_collider_exp_source_object_exp(
            context,
            getattr(settings, "source_object", None) if settings is not None else None,
        )
        source_obj = None
        target_source_obj = None
        guide_mode = _is_collider_exp_guide_object_exp(guide_obj, "PIPE")
        restore_obj = None
        restore_edit = False
        try:
            if guide_mode:
                source_obj = guide_obj
                target_source_obj = _collider_exp_guide_source_object_exp(context, source_obj, settings) or source_obj
                restore_obj = target_source_obj
                if getattr(source_obj, "mode", "OBJECT") != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                target_obj = _ensure_collider_exp_target_object_exp(context, settings, target_source_obj, op=self)
                if target_obj == source_obj:
                    raise RuntimeError("Target Geometry LOD must be separate from the Source Object")
                data_items = [_collect_collider_exp_input_data_exp(context, source_obj, bounds_only=False)]
            else:
                target_obj, target_source_obj, data_items = _prepare_collider_exp_direct_boxes_build_exp(
                    context,
                    settings,
                    self,
                    bounds_only=False,
                )
                restore_obj = target_source_obj
                restore_edit = getattr(target_source_obj, "mode", "") == "EDIT"
                if getattr(target_source_obj, "mode", "OBJECT") != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")

            material_index, material_name = _ensure_collider_placeholder_material(
                target_obj,
                target_source_obj,
                data_items=[] if guide_mode else data_items,
            )
            stats = _collider_exp_empty_stats_exp()
            actual_segments = 0
            for data in data_items:
                vertices, faces = _pipe_box_mesh_from_data_exp(data, self)
                part_stats = _append_collider_exp_mesh_to_object_exp(
                    target_obj,
                    vertices,
                    faces,
                    merge_distance=self.merge_distance,
                    recalc_normals=bool(self.recalc_normals),
                    material_index=material_index,
                )
                _merge_collider_exp_stats_exp(stats, part_stats)
                actual_segments += len(faces) // len(_COLLIDER_EXP_BOX_FACES)
            if guide_mode:
                _remove_collider_exp_guide_after_conversion_exp(context, source_obj)
            _restore_collider_exp_source_context_exp(context, restore_obj, restore_edit_mode=restore_edit)
        except Exception as e:
            try:
                _restore_collider_exp_source_context_exp(context, restore_obj, restore_edit_mode=restore_edit)
            except Exception:
                pass
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        props = ("target_lod", "minimum_size", "merge_distance", "recalc_normals")
        _write_collider_exp_operator_to_settings_exp(self, settings, prop_names=props)
        try:
            settings.exp_mode = "PIPE_BOXES"
        except Exception:
            pass
        _set_collider_exp_custom_props_exp(
            target_obj,
            "PIPE_BOXES",
            target_source_obj,
            {
                "vertex_indices": stats.get("vertex_indices", []),
                "face_indices": stats.get("face_indices", []),
                "material_name": material_name,
                "segments": int(actual_segments),
                "inner_radius": float(self.pipe_inner_radius),
                "outer_radius": float(self.pipe_outer_radius),
                "thickness": float(self.pipe_thickness),
                "depth": float(self.pipe_depth),
                "scope": str(getattr(settings, "collider_scope", "FROM_SELECTED")),
                "parts": len(data_items),
            },
        )
        self.report({"INFO"}, f"Generated {actual_segments} pipe box segments in {target_obj.name}")
        return {"FINISHED"}


from .nh_base import (_COLLIDER_TARGET_LOD_ITEMS)

class CRAY_OT_GenerateSphereColliderExp(Operator):
    """Generate an experimental low-poly sphere into the target Geometry LOD"""

    bl_idname = "cray.generate_sphere_collider_exp"
    bl_label = "Generate Sphere"
    bl_options = {"REGISTER", "UNDO"}

    target_lod: EnumProperty(name="Target LOD", items=_COLLIDER_TARGET_LOD_ITEMS, default="6")
    scale_x: FloatProperty(name="Scale X", default=1.0, min=0.001)
    scale_y: FloatProperty(name="Scale Y", default=1.0, min=0.001)
    scale_z: FloatProperty(name="Scale Z", default=1.0, min=0.001)
    scale_multiplier: FloatProperty(name="Scale Multiplier", default=1.0, min=0.001)
    offset_x: FloatProperty(name="Offset X", default=0.0)
    offset_y: FloatProperty(name="Offset Y", default=0.0)
    offset_z: FloatProperty(name="Offset Z", default=0.0)
    floor_contact: BoolProperty(name="Floor Contact", default=False)
    minimum_size: FloatProperty(name="Minimum Size", default=0.05, min=0.0)
    merge_distance: FloatProperty(name="Merge Distance", default=0.0, min=0.0)
    recalc_normals: BoolProperty(name="Recalculate Normals", default=True)
    sphere_segments: IntProperty(name="Sphere Segments", default=16, min=8, max=64)

    def invoke(self, context, event):
        from .nh_collider import (_collider_exp_settings_exp)
        del event
        props = _collider_exp_operator_props_exp(("sphere_segments",))
        _copy_collider_exp_settings_to_operator_exp(self, _collider_exp_settings_exp(context), prop_names=props)
        return self.execute(context)

    def draw(self, context):
        del context
        _draw_collider_exp_operator_panel_exp(
            self.layout,
            self,
            ("sphere_segments",),
            extra_label="Sphere",
        )

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_textures import (_ensure_collider_placeholder_material)
        settings = _require_collider_exp_enabled_exp(self, context)
        if settings is None:
            return {"CANCELLED"}
        try:
            target_obj, source_obj, data_items = _prepare_collider_exp_scope_build_exp(
                context,
                settings,
                self,
                bounds_only=True,
            )
            material_index, material_name = _ensure_collider_placeholder_material(
                target_obj,
                source_obj,
                data_items=data_items,
            )
            stats = _collider_exp_empty_stats_exp()
            for data in data_items:
                vertices, faces = _sphere_mesh_from_data_exp(data, self)
                part_stats = _append_collider_exp_mesh_to_object_exp(
                    target_obj,
                    vertices,
                    faces,
                    merge_distance=self.merge_distance,
                    recalc_normals=bool(self.recalc_normals),
                    material_index=material_index,
                )
                _merge_collider_exp_stats_exp(stats, part_stats)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        props = _collider_exp_operator_props_exp(("sphere_segments",))
        _write_collider_exp_operator_to_settings_exp(self, settings, prop_names=props)
        try:
            settings.exp_mode = "SPHERE"
        except Exception:
            pass
        _set_collider_exp_custom_props_exp(
            target_obj,
            "SPHERE",
            source_obj,
            {
                "vertex_indices": stats.get("vertex_indices", []),
                "face_indices": stats.get("face_indices", []),
                "material_name": material_name,
                "scope": str(getattr(settings, "collider_scope", "FROM_SELECTED")),
                "parts": len(data_items),
            },
        )
        self.report({"INFO"}, f"Generated {len(data_items)} sphere collider part(s) in {target_obj.name}: +{stats['verts_added']} verts, +{stats['faces_added']} faces")
        return {"FINISHED"}


from .nh_base import (_COLLIDER_TARGET_LOD_ITEMS)

class CRAY_OT_GenerateCapsuleColliderExp(Operator):
    """Generate an experimental low-poly capsule into the target Geometry LOD"""

    bl_idname = "cray.generate_capsule_collider_exp"
    bl_label = "Generate Capsule"
    bl_options = {"REGISTER", "UNDO"}

    target_lod: EnumProperty(name="Target LOD", items=_COLLIDER_TARGET_LOD_ITEMS, default="6")
    scale_x: FloatProperty(name="Scale X", default=1.0, min=0.001)
    scale_y: FloatProperty(name="Scale Y", default=1.0, min=0.001)
    scale_z: FloatProperty(name="Scale Z", default=1.0, min=0.001)
    scale_multiplier: FloatProperty(name="Scale Multiplier", default=1.0, min=0.001)
    offset_x: FloatProperty(name="Offset X", default=0.0)
    offset_y: FloatProperty(name="Offset Y", default=0.0)
    offset_z: FloatProperty(name="Offset Z", default=0.0)
    floor_contact: BoolProperty(name="Floor Contact", default=False)
    minimum_size: FloatProperty(name="Minimum Size", default=0.05, min=0.0)
    merge_distance: FloatProperty(name="Merge Distance", default=0.0, min=0.0)
    recalc_normals: BoolProperty(name="Recalculate Normals", default=True)
    capsule_radius: FloatProperty(name="Capsule Radius", default=0.5, min=0.001)
    capsule_height: FloatProperty(name="Capsule Height", default=2.0, min=0.001)
    capsule_cap_size: FloatProperty(name="Capsule Cap Size", default=0.5, min=0.001)
    capsule_follow_source_angle: BoolProperty(
        name="Capsule Follow Source Angle",
        description="Align capsule top and bottom along the selected shell/object direction",
        default=False,
    )
    capsule_vertical_align: BoolProperty(name="Capsule Vertical Align", default=True)

    def invoke(self, context, event):
        from .nh_collider import (_collider_exp_settings_exp)
        del event
        props = _collider_exp_operator_props_exp((
            "capsule_radius",
            "capsule_height",
            "capsule_cap_size",
            "capsule_follow_source_angle",
            "capsule_vertical_align",
        ))
        _copy_collider_exp_settings_to_operator_exp(self, _collider_exp_settings_exp(context), prop_names=props)
        return self.execute(context)

    def draw(self, context):
        del context
        _draw_collider_exp_operator_panel_exp(
            self.layout,
            self,
            (
                "capsule_radius",
                "capsule_height",
                "capsule_cap_size",
                "capsule_follow_source_angle",
                "capsule_vertical_align",
            ),
            extra_label="Capsule",
        )

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_textures import (_ensure_collider_placeholder_material)
        settings = _require_collider_exp_enabled_exp(self, context)
        if settings is None:
            return {"CANCELLED"}
        try:
            target_obj, source_obj, data_items = _prepare_collider_exp_scope_build_exp(
                context,
                settings,
                self,
                bounds_only=True,
            )
            material_index, material_name = _ensure_collider_placeholder_material(
                target_obj,
                source_obj,
                data_items=data_items,
            )
            stats = _collider_exp_empty_stats_exp()
            for data in data_items:
                vertices, faces = _capsule_mesh_from_data_exp(data, self)
                part_stats = _append_collider_exp_mesh_to_object_exp(
                    target_obj,
                    vertices,
                    faces,
                    merge_distance=self.merge_distance,
                    recalc_normals=bool(self.recalc_normals),
                    material_index=material_index,
                )
                _merge_collider_exp_stats_exp(stats, part_stats)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        props = _collider_exp_operator_props_exp((
            "capsule_radius",
            "capsule_height",
            "capsule_cap_size",
            "capsule_follow_source_angle",
            "capsule_vertical_align",
        ))
        _write_collider_exp_operator_to_settings_exp(self, settings, prop_names=props)
        try:
            settings.exp_mode = "CAPSULE"
        except Exception:
            pass
        _set_collider_exp_custom_props_exp(
            target_obj,
            "CAPSULE",
            source_obj,
            {
                "vertex_indices": stats.get("vertex_indices", []),
                "face_indices": stats.get("face_indices", []),
                "material_name": material_name,
                "scope": str(getattr(settings, "collider_scope", "FROM_SELECTED")),
                "parts": len(data_items),
                "capsule_follow_source_angle": bool(self.capsule_follow_source_angle),
            },
        )
        self.report({"INFO"}, f"Generated {len(data_items)} capsule collider part(s) in {target_obj.name}: +{stats['verts_added']} verts, +{stats['faces_added']} faces")
        return {"FINISHED"}


class CRAY_OT_ValidateCollisionExp(Operator):
    """Validate generated collision objects for common DayZ/P3D collision issues"""

    bl_idname = "cray.validate_collision_exp"
    bl_label = "Validate Collision"
    bl_options = {"REGISTER", "UNDO"}

    max_triangles: IntProperty(
        name="Max Triangles",
        description="Warn when a collision object exceeds this triangle budget; set to 0 to disable",
        default=256,
        min=0,
        max=100000,
    )
    minimum_size: FloatProperty(
        name="Minimum Size",
        description="Warn when a disconnected collision island is smaller than this size",
        default=0.05,
        min=0.0,
        precision=4,
        unit="LENGTH",
    )

    def invoke(self, context, event):
        from .nh_collider import (_collider_exp_settings_exp)
        del event
        settings = _collider_exp_settings_exp(context)
        if settings is not None:
            try:
                self.max_triangles = int(getattr(settings, "convex_max_triangles", self.max_triangles))
            except Exception:
                pass
            try:
                self.minimum_size = float(getattr(settings, "minimum_size", self.minimum_size))
            except Exception:
                pass
        return self.execute(context)

    def draw(self, context):
        del context
        self.layout.use_property_split = True
        self.layout.use_property_decorate = False
        self.layout.prop(self, "max_triangles")
        self.layout.prop(self, "minimum_size")

    def execute(self, context):
        from .nh_collider import (_collider_exp_settings_exp)
        settings = _collider_exp_settings_exp(context)
        objects = _resolve_collider_exp_validation_objects_exp(context, settings)
        if not objects:
            self.report({"ERROR"}, "No collision objects found. Select a generated collision object or set a target LOD")
            return {"CANCELLED"}

        results = []
        for obj in objects:
            results.append((
                obj,
                _validate_collider_exp_object_exp(
                    context,
                    obj,
                    max_triangles=int(self.max_triangles),
                    minimum_size=float(self.minimum_size),
                ),
            ))

        _print_collider_exp_validation_report_exp(results)
        error_count = sum(len(result.get("errors", [])) for _obj, result in results)
        warning_count = sum(len(result.get("warnings", [])) for _obj, result in results)
        object_count = len(results)

        if error_count or warning_count:
            self.report(
                {"WARNING"},
                (
                    f"Validated {object_count} collision object(s): "
                    f"{error_count} error(s), {warning_count} warning(s). See System Console"
                ),
            )
        else:
            self.report({"INFO"}, f"Validated {object_count} collision object(s): no issues found")
        return {"FINISHED"}


class CRAY_OT_RunCollisionToolSelfTestExp(Operator):
    """Run a small runtime self-test for the experimental collision tool"""

    bl_idname = "cray.run_collision_tool_self_test_exp"
    bl_label = "NH Debug / Run Collision Tool Self Test"
    bl_options = {"REGISTER", "UNDO"}

    def _run_step(self, label, log, callback, *, expect_finished=True, expected_error=None):
        from .nh_base import (_fmt_exc)
        try:
            result = callback()
        except Exception as e:
            message = _fmt_exc(e)
            if not expect_finished:
                if expected_error and expected_error not in message:
                    raise RuntimeError(f"{label}: expected error containing {expected_error!r}, got {message}")
                log.append(f"{label}: ['CANCELLED'] ({message})")
                return {"CANCELLED"}
            raise RuntimeError(f"{label}: traceback: {_fmt_exc(e)}")

        if isinstance(result, set):
            result_set = result
        elif isinstance(result, (tuple, list)):
            result_set = set(result)
        else:
            result_set = {str(result)}

        log.append(f"{label}: {sorted(result_set)}")
        if expect_finished and "FINISHED" not in result_set:
            raise RuntimeError(f"{label}: expected FINISHED, got {sorted(result_set)}")
        if not expect_finished and "FINISHED" in result_set:
            raise RuntimeError(f"{label}: expected non-FINISHED, got {sorted(result_set)}")
        return result_set

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider import (_collider_exp_settings_exp)
        from .nh_snap import (_deselect_all_in_view_layer)
        scene = getattr(context, "scene", None)
        view_layer = getattr(context, "view_layer", None)
        settings = _collider_exp_settings_exp(context)
        if scene is None or view_layer is None:
            self.report({"ERROR"}, "Self Test requires an active scene and view layer")
            return {"CANCELLED"}
        if settings is None:
            self.report({"ERROR"}, "Experimental collider settings are unavailable")
            return {"CANCELLED"}

        log = []
        root_collection = None
        visual_obj = None
        empty_obj = None
        collision_obj = None
        created_meshes = []
        saved_selection = []
        try:
            saved_selection = [
                obj.name for obj in getattr(context, "selected_objects", [])
                if _is_live_blender_object_exp(obj)
            ]
        except Exception:
            saved_selection = []
        try:
            active_obj = getattr(view_layer.objects, "active", None)
            saved_active_name = active_obj.name if _is_live_blender_object_exp(active_obj) else ""
        except Exception:
            saved_active_name = ""

        setting_names = (
            "enabled",
            "source_object",
            "target_lod",
            "geometry_object",
            "convex_detail",
            "convex_max_triangles",
            "minimum_size",
            "normal_minimum_size",
            "merge_distance",
            "recalc_normals",
        )
        saved_settings = {}
        for name in setting_names:
            try:
                saved_settings[name] = getattr(settings, name)
            except (ReferenceError, RuntimeError):
                saved_settings[name] = None
            except Exception:
                pass

        success = False
        try:
            suffix = uuid.uuid4().hex[:8]
            root_collection = bpy.data.collections.new(f"NH_ColliderSelfTest_{suffix}.p3d")
            scene.collection.children.link(root_collection)
            visuals_collection = bpy.data.collections.new("Visuals")
            root_collection.children.link(visuals_collection)
            log.append(f"Created temp collection: {root_collection.name}")

            visual_mesh = _collider_exp_self_test_cube_mesh_exp(f"NH_ColliderSelfTest_VisualMesh_{suffix}", size=2.0)
            created_meshes.append(visual_mesh)
            visual_obj = bpy.data.objects.new(f"NH_ColliderSelfTest_Visual_{suffix}", visual_mesh)
            visuals_collection.objects.link(visual_obj)
            visual_signature = _collider_exp_mesh_signature_exp(visual_obj)
            if visual_signature is None:
                raise RuntimeError("Could not capture visual object signature")

            empty_obj = bpy.data.objects.new(f"NH_ColliderSelfTest_Empty_{suffix}", None)
            root_collection.objects.link(empty_obj)

            settings.enabled = True
            settings.source_object = visual_obj
            settings.geometry_object = None
            settings.target_lod = "6"
            settings.convex_detail = 8
            settings.convex_max_triangles = 32
            settings.minimum_size = 0.05
            settings.normal_minimum_size = False
            settings.merge_distance = 0.0
            settings.recalc_normals = True

            _deselect_all_in_view_layer(context)
            visual_obj.select_set(True)
            view_layer.objects.active = visual_obj

            self._run_step(
                "Generate Box",
                log,
                lambda: bpy.ops.cray.generate_box_collider_exp(
                    "EXEC_DEFAULT",
                    target_lod="6",
                    minimum_size=0.05,
                    normal_minimum_size=False,
                    recalc_normals=True,
                ),
            )
            collision_obj = getattr(settings, "geometry_object", None)
            if not _is_live_blender_object_exp(collision_obj):
                raise RuntimeError("Generate Box did not create a live collision target")
            if collision_obj == visual_obj:
                raise RuntimeError("Collision target reused the visual object")
            if collision_obj.data is not None:
                created_meshes.append(collision_obj.data)
            if not _is_collider_exp_object_in_lod_collection_exp(context, collision_obj):
                raise RuntimeError("Box collision target is not inside a collision/LOD collection")

            _deselect_all_in_view_layer(context)
            visual_obj.select_set(True)
            view_layer.objects.active = visual_obj
            self._run_step(
                "Generate Convex Hull",
                log,
                lambda: bpy.ops.cray.generate_convex_hull_collider_exp(
                    "EXEC_DEFAULT",
                    target_lod="6",
                    convex_detail=8,
                    convex_max_triangles=32,
                    minimum_size=0.05,
                    recalc_normals=True,
                ),
            )
            collision_obj = getattr(settings, "geometry_object", None)
            if not _is_live_blender_object_exp(collision_obj):
                raise RuntimeError("Generate Convex Hull lost the collision target")
            if not _is_collider_exp_convex_hull_object_exp(collision_obj):
                raise RuntimeError("Generated collision target was not tagged as CONVEX_HULL")
            if not _is_collider_exp_object_in_lod_collection_exp(context, collision_obj):
                raise RuntimeError("Convex hull target is not inside a collision/LOD collection")

            _deselect_all_in_view_layer(context)
            collision_obj.select_set(True)
            view_layer.objects.active = collision_obj
            self._run_step(
                "Simplify Hull",
                log,
                lambda: bpy.ops.cray.rebuild_convex_hull_collider_exp(
                    "EXEC_DEFAULT",
                    target_lod="6",
                    convex_detail=6,
                    convex_max_triangles=24,
                    minimum_size=0.05,
                    recalc_normals=True,
                ),
            )
            if not _is_live_blender_object_exp(collision_obj):
                raise RuntimeError("Simplify Hull removed the collision object")
            if not _is_collider_exp_object_in_lod_collection_exp(context, collision_obj):
                raise RuntimeError("Simplified hull left the collision/LOD collection")

            self._run_step(
                "Validate Collision",
                log,
                lambda: bpy.ops.cray.validate_collision_exp(
                    "EXEC_DEFAULT",
                    max_triangles=64,
                    minimum_size=0.01,
                ),
            )

            settings.geometry_object = None
            _deselect_all_in_view_layer(context)
            view_layer.objects.active = None
            self._run_step(
                "Validate Empty Scene State",
                log,
                lambda: bpy.ops.cray.validate_collision_exp(
                    "EXEC_DEFAULT",
                    max_triangles=64,
                    minimum_size=0.01,
                ),
                expect_finished=False,
                expected_error="No collision objects found",
            )

            _deselect_all_in_view_layer(context)
            empty_obj.select_set(True)
            view_layer.objects.active = empty_obj
            self._run_step(
                "Validate Non-Mesh Active",
                log,
                lambda: bpy.ops.cray.validate_collision_exp(
                    "EXEC_DEFAULT",
                    max_triangles=64,
                    minimum_size=0.01,
                ),
                expect_finished=False,
                expected_error="No collision objects found",
            )

            settings.geometry_object = collision_obj
            if _collider_exp_mesh_signature_exp(visual_obj) != visual_signature:
                raise RuntimeError("Visual object changed during collision self-test")

            success = True
            print("=== NH Experimental Collider Self Test ===")
            for item in log:
                print(item)
            self.report({"INFO"}, "Collision Tool Self Test passed")
            return {"FINISHED"}
        except Exception as e:
            print("=== NH Experimental Collider Self Test FAILED ===")
            for item in log:
                print(item)
            print(_fmt_exc(e))
            self.report({"ERROR"}, f"Collision Tool Self Test failed: {_fmt_exc(e)}")
            return {"CANCELLED"}
        finally:
            try:
                _deselect_all_in_view_layer(context)
            except Exception:
                pass
            _remove_collider_exp_self_test_data_exp(root_collection, created_meshes)
            for name, value in saved_settings.items():
                try:
                    if name in {"source_object", "geometry_object"} and not _is_live_blender_object_exp(value):
                        setattr(settings, name, None)
                    else:
                        setattr(settings, name, value)
                except Exception:
                    pass
            for name in saved_selection:
                obj = bpy.data.objects.get(name)
                if obj is not None:
                    try:
                        obj.select_set(True)
                    except Exception:
                        pass
            if saved_active_name:
                active = bpy.data.objects.get(saved_active_name)
                if active is not None:
                    try:
                        view_layer.objects.active = active
                    except Exception:
                        pass
            if success:
                log.append("Cleanup complete")


# ------------------------------------------------------------------------
#  Texture Replace (.paa/.rvmat) + Replace from DB via P3D
# ------------------------------------------------------------------------

_ALLOWED_DB_EXTS = {".paa", ".rvmat"}
_TEXTURE_SUFFIX_RE = re.compile(
    r"([_-])(co|ca|as|nohq|no|n|smdi|spec|det|detail|em|ao|rough|metal|mask)$",
    re.IGNORECASE,
)
_TEX_EXPORT_TEXTURE_SUFFIX_RE = re.compile(
    r"([_-])(co|ca|nohq|smdi|bump|dt|as|mc|mask|rough|metal|ao|spec|det|detail|em|no|n)$",
    re.IGNORECASE,
)
_TEX_EXPORT_DDS_BACKEND_LABELS = {
    "AUTO": "Auto",
    "BUILTIN_PYTHON": "Built-in Python",
    "BUNDLED_EXE": "Bundled EXE",
    "BUNDLED_NODE": "Bundled Node",
    "BLENDER": "Blender",
    "EXTERNAL": "External",
}
_TEX_EXPORT_NOHQ_FALLBACK = "#(argb,8,8,3)color(0.5,0.5,1,1,NOHQ)"
_TEX_EXPORT_SMDI_FALLBACK = "#(argb,8,8,3)color(0,0.012,0.63,1,SMDI)"
from .nh_base import (_TEX_EXPORT_DEFAULT_SOURCE_ROOTS)

_TEX_EXPORT_LEGACY_DEFAULT_SOURCE_ROOT = _TEX_EXPORT_DEFAULT_SOURCE_ROOTS[0]
_TEXTURE_CONVERTER_TEST_SUFFIXES = (
    "_test",
    "_node_test",
    "_package_test",
    "_backend_test",
    "_selftest",
)
_WINDOWS_INVALID_FILENAME_CHARS = set('<>:"|?*')
_TEXTURE_CANDIDATE_EXTS = {
    ".paa",
    ".rvmat",
    ".dds",
    ".png",
    ".tga",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tif",
    ".tiff",
}
_PLACEHOLDER_MATERIAL_KEYS = {
    "<no materials>",
    "__none__",
    "p3d: no material",
    "p3d no material",
    "p3d:_no_material",
    "p3d no_material",
    "no material",
    "no_material",
}

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

def _candidate_base_no_ext(name_or_path: str) -> str:
    from .nh_textures import (_strip_blender_numeric_suffix)
    s = _strip_blender_numeric_suffix(_norm_path(str(name_or_path or "")))
    s = _basename_no_ext(s)
    return _strip_blender_numeric_suffix(s)

def _placeholder_name_keys(value: str):
    raw = str(value or "").strip()
    if not raw:
        return [""]

    norm = _norm_path(raw)
    leaf = norm.split("\\")[-1].strip()
    base = _candidate_base_no_ext(norm)
    values = [raw, norm, leaf, _candidate_base_no_ext(leaf), base]
    keys = []
    for item in values:
        s = str(item or "").strip().strip("'\"")
        if not s:
            keys.append("")
            continue
        lower = s.lower()
        keys.append(lower)
        keys.append(re.sub(r"\s+", " ", lower.replace("_", " ")).strip())
        keys.append(re.sub(r"[^a-z0-9]+", " ", lower).strip())
    return _unique_ci(keys)

def _is_placeholder_material_name(value) -> bool:
    for key in _placeholder_name_keys(value):
        if key == "" or key in _PLACEHOLDER_MATERIAL_KEYS:
            return True
    return False

def _is_invalid_windows_filename_component(value) -> bool:
    raw = str(value or "").strip().strip("'\"")
    if not raw:
        return True

    norm = _norm_path(raw)
    parts = [part.strip() for part in re.split(r"[\\/]+", norm) if part.strip()]
    if not parts:
        return True

    for idx, part in enumerate(parts):
        if idx == 0 and re.fullmatch(r"[A-Za-z]:", part):
            continue
        if any(ch in _WINDOWS_INVALID_FILENAME_CHARS for ch in part):
            return True
    return False

def _blender_install_dir_abs() -> str:
    try:
        binary = getattr(bpy.app, "binary_path", "") or ""
        if binary:
            return os.path.abspath(os.path.dirname(binary))
    except Exception:
        pass
    return ""

def _path_is_under_or_equal_safe(path: str, root: str) -> bool:
    try:
        path_abs = os.path.abspath(os.path.normpath(path))
        root_abs = os.path.abspath(os.path.normpath(root))
        return os.path.normcase(os.path.commonpath([path_abs, root_abs])) == os.path.normcase(root_abs)
    except Exception:
        return False

def _iter_texture_resolution_roots(settings=None):
    from .nh_textures import (_iter_p3d_project_roots)
    try:
        ts = _get_texreplace_settings_safe(settings)
    except Exception:
        ts = settings

    if ts is not None:
        for attr in ("target_textures_folder", "folder", "texture_cache_source_folder"):
            raw = getattr(ts, attr, "") if hasattr(ts, attr) else ""
            path = _tex_export_resolve_path(raw) if raw else ""
            if path:
                yield path
        try:
            for root in _tex_export_source_roots_from_settings(ts):
                if root:
                    yield root
        except Exception:
            pass

    for root in _iter_p3d_project_roots():
        if root:
            yield root

def _is_under_configured_texture_root(path: str, settings=None) -> bool:
    if not path:
        return False
    for root in _iter_texture_resolution_roots(settings):
        if root and _path_is_under_or_equal_safe(path, root):
            return True
    return False

def _is_blender_install_texture_path_invalid(path_value: str, settings=None) -> bool:
    from .nh_textures import (_normalize_drive_relative_path)
    raw = _normalize_drive_relative_path(str(path_value or "").strip())
    if not raw:
        return False

    raw_is_abs = os.path.isabs(raw) or re.match(r"^[A-Za-z]:[\\/]", raw) is not None
    if not raw_is_abs:
        return False

    try:
        abs_path = os.path.abspath(bpy.path.abspath(raw))
    except Exception:
        try:
            abs_path = os.path.abspath(raw)
        except Exception:
            return False

    blender_dir = _blender_install_dir_abs()
    if not blender_dir or not _path_is_under_or_equal_safe(abs_path, blender_dir):
        return False
    return not _is_under_configured_texture_root(abs_path, settings)

def _looks_like_texture_candidate(value: str) -> bool:
    from .nh_textures import (_strip_blender_numeric_suffix)
    raw = str(value or "").strip()
    if not raw or raw.startswith("#"):
        return False

    norm = _norm_path(raw)
    stripped = _strip_blender_numeric_suffix(norm)
    ext = os.path.splitext(stripped)[1].lower()
    base = _candidate_base_no_ext(norm)
    if not base or not re.search(r"[A-Za-z0-9]", base):
        return False

    if ext in _TEXTURE_CANDIDATE_EXTS:
        return True
    if "\\" in norm or "/" in raw:
        return True
    if "_" in base or "-" in base:
        return True
    if re.search(r"\d", base):
        return True
    return False

def _is_valid_texture_candidate(value) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    if _is_placeholder_material_name(raw):
        return False
    if _is_invalid_windows_filename_component(raw):
        return False
    if _is_blender_install_texture_path_invalid(raw):
        return False
    return _looks_like_texture_candidate(raw)

def _log_rejected_texture_candidate(value, *, material_name: str = ""):
    raw = str(value or "").strip()
    if not raw:
        return
    if _is_placeholder_material_name(raw):
        print(f"Skipped placeholder material: {raw}")
        return
    if _is_invalid_windows_filename_component(raw) or _is_blender_install_texture_path_invalid(raw):
        print(f"Skipped invalid texture candidate: {raw}")

def _expand_basename_variants(base: str):
    base = _candidate_base_no_ext(base)
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
    from .nh_textures import (_get_p3d_material_paths)
    candidates = []

    def add_candidate(value, *, source=""):
        raw = str(value or "").strip()
        if not raw:
            return
        if not _is_valid_texture_candidate(raw):
            _log_rejected_texture_candidate(raw, material_name=getattr(mat, "name", ""))
            return
        base = _sanitize_tex_export_base(raw)
        if not base:
            _log_rejected_texture_candidate(raw, material_name=getattr(mat, "name", ""))
            return
        candidates.append(base)

    paa_path, rvmat_path = _get_p3d_material_paths(mat)
    add_candidate(paa_path, source="a3ob-paa")
    add_candidate(rvmat_path, source="a3ob-rvmat")

    if mat.use_nodes and mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type != "TEX_IMAGE" or not getattr(node, "image", None):
                continue
            img = node.image
            fp = (getattr(img, "filepath_raw", "") or getattr(img, "filepath", "") or "").strip()
            if fp:
                add_candidate(fp, source="image-filepath")
            image_name = getattr(img, "name", "") or ""
            add_candidate(image_name, source="image-name")

    add_candidate(getattr(mat, "name", ""), source="material-name")

    expanded = []
    for c in _unique_ci(candidates):
        expanded.extend(_expand_basename_variants(c))
    return _unique_ci(c for c in expanded if _is_valid_texture_candidate(c))

def _make_expected_texture_path_from_base(folder_abs: str, base: str, ext: str) -> str:
    ext = (ext or "").lower().strip()
    if ext not in _ALLOWED_DB_EXTS:
        raise ValueError(f"Unsupported texture extension: {ext}")

    base = _sanitize_tex_export_base(base)
    if not base or not _is_valid_texture_candidate(base):
        return ""

    folder_abs = folder_abs or ""
    folder_norm = os.path.normpath(folder_abs) if folder_abs else ""
    category = _texture_category_folder_from_base(base)
    if category and folder_norm:
        current_leaf = os.path.basename(folder_norm.rstrip("\\/"))
        if current_leaf.lower() != category.lower():
            folder_norm = os.path.join(folder_norm, category)
    path = os.path.join(folder_norm, f"{base}{ext}") if folder_norm else f"{base}{ext}"
    return _norm_path(os.path.normpath(path))

def _split_texture_candidate_base(base: str):
    texture_base = _sanitize_tex_export_base(base)
    if not texture_base or not _is_valid_texture_candidate(texture_base):
        return "", ""

    material_base = _TEXTURE_SUFFIX_RE.sub("", texture_base) or texture_base
    return texture_base, material_base

def _base_color_suffix(base: str) -> str:
    cleaned = _sanitize_tex_export_base(base)
    match = re.search(r"(?:_|-)(ca|co)$", cleaned, re.IGNORECASE)
    return f"_{match.group(1).lower()}" if match else ""

def _base_color_stem(base: str) -> str:
    cleaned = _sanitize_tex_export_base(base)
    if not cleaned:
        return ""
    return re.sub(r"(?:_|-)(?:ca|co)$", "", cleaned, flags=re.IGNORECASE) or cleaned

def _base_color_variant_bases(base: str, *, include_legacy: bool = True):
    """Return Base Color names in selection order: alpha, opaque, legacy."""
    cleaned = _sanitize_tex_export_base(base)
    if not cleaned:
        return []

    suffix_match = _TEX_EXPORT_TEXTURE_SUFFIX_RE.search(cleaned)
    if suffix_match and suffix_match.group(2).lower() not in {"ca", "co"}:
        return [cleaned]

    stem = _base_color_stem(cleaned)
    variants = [stem + "_ca", stem + "_co"]
    if include_legacy:
        variants.append(stem)
    return _unique_ci(variants)

def _expected_base_color_base(base: str, has_alpha=None) -> str:
    cleaned = _sanitize_tex_export_base(base)
    if not cleaned:
        return ""

    suffix_match = _TEX_EXPORT_TEXTURE_SUFFIX_RE.search(cleaned)
    if suffix_match and suffix_match.group(2).lower() not in {"ca", "co"}:
        return cleaned

    explicit_suffix = _base_color_suffix(cleaned)
    if has_alpha is None and explicit_suffix:
        return _base_color_stem(cleaned) + explicit_suffix

    suffix = "_ca" if bool(has_alpha) else "_co"
    return _base_color_stem(cleaned) + suffix

def _base_color_suffix_priority(base: str) -> int:
    suffix = _base_color_suffix(base)
    if suffix == "_ca":
        return 2
    if suffix == "_co":
        return 1
    return 0

def _base_color_path_variants(path_value: str):
    from .nh_textures import (_normalize_drive_relative_path)
    """Return sibling Base Color paths with _ca before _co."""
    raw = _normalize_drive_relative_path(path_value)
    if not raw:
        return []

    folder, leaf = os.path.split(raw)
    base, ext = os.path.splitext(leaf)
    cleaned = _sanitize_tex_export_base(base)
    if not cleaned or ext.lower() == ".rvmat":
        return [raw]

    suffix_match = _TEX_EXPORT_TEXTURE_SUFFIX_RE.search(cleaned)
    if suffix_match and suffix_match.group(2).lower() not in {"ca", "co"}:
        return [raw]

    variants = []
    for variant_base in _base_color_variant_bases(cleaned, include_legacy=True):
        variants.append(os.path.join(folder, variant_base + ext) if folder else variant_base + ext)
    return _unique_ci(variants)

def _texture_category_folder_from_base(base: str) -> str:
    cleaned = os.path.basename(str(base or "")).strip()
    cleaned = os.path.splitext(cleaned)[0]
    if not cleaned:
        return ""

    for sep in ("_", "-"):
        if sep in cleaned:
            prefix = cleaned.split(sep, 1)[0].strip()
            if prefix and all((ch.isalpha() or ch.isdigit()) for ch in prefix):
                return prefix

    prefix_chars = []
    for ch in cleaned:
        if ch == "_" or ch == "-":
            break
        if ch.isalpha() or ch.isdigit():
            prefix_chars.append(ch)
        else:
            break

    return "".join(prefix_chars).strip()

def _texreplace_folder_abs(settings) -> str:
    folder_abs = getattr(settings, "folder", "") or ""
    try:
        folder_abs = bpy.path.abspath(folder_abs)
    except Exception:
        pass
    try:
        folder_abs = os.path.abspath(folder_abs)
    except Exception:
        pass
    return _norm_path(folder_abs)

def _first_valid_texture_candidate(candidates) -> str:
    for candidate in candidates or []:
        if not _is_valid_texture_candidate(candidate):
            _log_rejected_texture_candidate(candidate)
            continue
        base = _sanitize_tex_export_base(candidate)
        if base:
            return base
    return ""

def _pick_best_db_match(candidates, db_map):
    best = None
    for candidate_index, base in enumerate(candidates):
        if not _is_valid_texture_candidate(base):
            _log_rejected_texture_candidate(base)
            continue
        texture_base, material_base = _split_texture_candidate_base(base)
        if not texture_base and not material_base:
            continue

        paa_path = None
        matched_texture_base = ""
        for variant_base in _base_color_variant_bases(texture_base, include_legacy=True):
            variant_path = db_map.get(f"{variant_base.lower()}.paa")
            if variant_path:
                paa_path = variant_path
                matched_texture_base = variant_base
                break

        rvmat_path = db_map.get(f"{material_base.lower()}.rvmat") if material_base else None
        if not rvmat_path and texture_base and texture_base.lower() != material_base.lower():
            rvmat_path = db_map.get(f"{texture_base.lower()}.rvmat")

        selected_texture_base = matched_texture_base or _expected_base_color_base(texture_base)
        score = int(bool(paa_path)) + int(bool(rvmat_path))
        if score == 0:
            continue

        rank = (
            score,
            int(bool(paa_path)),
            _base_color_suffix_priority(selected_texture_base),
            -candidate_index,
        )
        if best is None or rank > best["rank"]:
            best = {
                "base": selected_texture_base,
                "material_base": material_base,
                "paa": paa_path,
                "rvmat": rvmat_path,
                "score": score,
                "rank": rank,
            }
    return best

def _build_expected_texture_pair(settings, candidates, match):
    write_expected = bool(getattr(settings, "write_expected_missing_paths", True))
    folder_abs = _texreplace_folder_abs(settings)

    if match:
        found_paa = match.get("paa")
        found_rvmat = match.get("rvmat")
        used_base = match.get("base") or _first_valid_texture_candidate(candidates)
        texture_base, material_base = _split_texture_candidate_base(used_base)
        if not texture_base and not material_base:
            return None, None, used_base, False, False

        paa_path = found_paa or None
        rvmat_path = found_rvmat or None
        is_virtual_paa = False
        is_virtual_rvmat = False

        if write_expected:
            if not paa_path and texture_base:
                paa_dir = os.path.dirname(found_rvmat) if found_rvmat else folder_abs
                paa_path = _make_expected_texture_path_from_base(paa_dir, texture_base, ".paa")
                is_virtual_paa = bool(paa_path)
            if not rvmat_path and material_base:
                rvmat_dir = os.path.dirname(found_paa) if found_paa else folder_abs
                rvmat_path = _make_expected_texture_path_from_base(rvmat_dir, material_base, ".rvmat")
                is_virtual_rvmat = bool(rvmat_path)

        return paa_path, rvmat_path, used_base, is_virtual_paa, is_virtual_rvmat

    used_base = _first_valid_texture_candidate(candidates)
    if not write_expected or not used_base or not _is_valid_texture_candidate(used_base):
        return None, None, used_base, False, False

    texture_base, material_base = _split_texture_candidate_base(used_base)
    if not texture_base and not material_base:
        return None, None, used_base, False, False
    texture_base = _expected_base_color_base(texture_base)
    paa_path = _make_expected_texture_path_from_base(folder_abs, texture_base, ".paa") if texture_base else None
    rvmat_path = _make_expected_texture_path_from_base(folder_abs, material_base, ".rvmat") if material_base else None
    return paa_path, rvmat_path, used_base, bool(paa_path), bool(rvmat_path)

def _tex_export_resolve_path(path_value: str, fallback: str = "") -> str:
    path_value = path_value or fallback or ""
    if not path_value:
        return ""
    try:
        path_value = bpy.path.abspath(path_value)
    except Exception:
        pass
    try:
        path_value = os.path.abspath(path_value)
    except Exception:
        pass
    return _norm_path(os.path.normpath(path_value))

def _split_tex_source_roots_text(raw) -> list[str]:
    roots = []
    for part in re.split(r"[;\r\n]+", str(raw or "")):
        part = part.strip()
        if not part:
            continue
        resolved = _tex_export_resolve_path(part)
        if not resolved:
            continue
        roots.append(resolved)
    return _unique_ci(roots)

def _tex_source_roots_with_defaults(roots) -> list[str]:
    from .nh_base import (_TEX_EXPORT_DEFAULT_SOURCE_ROOTS)
    roots = _unique_ci(roots or [])
    if not roots:
        return list(_TEX_EXPORT_DEFAULT_SOURCE_ROOTS)

    if (
        len(roots) == 1
        and os.path.normcase(os.path.normpath(roots[0])) == os.path.normcase(os.path.normpath(_TEX_EXPORT_LEGACY_DEFAULT_SOURCE_ROOT))
    ):
        for default_root in _TEX_EXPORT_DEFAULT_SOURCE_ROOTS:
            if default_root not in roots:
                roots.append(default_root)
    return _unique_ci(roots)

def _tex_source_roots_from_collection(settings) -> list[str]:
    if settings is None or not hasattr(settings, "source_texture_roots"):
        return []
    roots = []
    try:
        items = list(getattr(settings, "source_texture_roots", []) or [])
    except Exception:
        items = []
    for item in items:
        path = getattr(item, "path", "") or ""
        resolved = _tex_export_resolve_path(path)
        if resolved:
            roots.append(resolved)
    return _unique_ci(roots)

def _sync_tex_source_roots_text(settings, roots=None):
    if settings is None or not hasattr(settings, "source_textures_folder"):
        return []
    roots = _unique_ci(roots if roots is not None else _tex_source_roots_from_collection(settings))
    try:
        settings.source_textures_folder = ";".join(roots)
    except Exception:
        pass
    return roots

def _ensure_tex_source_roots_collection(settings):
    if settings is None or not hasattr(settings, "source_texture_roots"):
        return _tex_source_roots_with_defaults(
            _split_tex_source_roots_text(getattr(settings, "source_textures_folder", ""))
        )

    collection_roots = _tex_source_roots_from_collection(settings)
    if collection_roots:
        return _sync_tex_source_roots_text(settings, collection_roots)

    roots = _tex_source_roots_with_defaults(
        _split_tex_source_roots_text(getattr(settings, "source_textures_folder", ""))
    )
    try:
        settings.source_texture_roots.clear()
        for root in roots:
            item = settings.source_texture_roots.add()
            item.path = root
    except Exception:
        pass
    return _sync_tex_source_roots_text(settings, roots)

def _tex_export_source_roots_from_settings(settings) -> list[str]:
    from .nh_base import (_TEX_EXPORT_DEFAULT_SOURCE_ROOTS)
    if settings is None:
        return list(_TEX_EXPORT_DEFAULT_SOURCE_ROOTS)
    return _ensure_tex_source_roots_collection(settings)

def _find_tex_export_base_color_dds(dds_map, material_base: str, preferred_rel_dir=""):
    source_names = _base_color_variant_bases(material_base, include_legacy=True)
    return _find_tex_export_dds(dds_map, *source_names, preferred_rel_dir=preferred_rel_dir)

def _dds_file_has_alpha_channel(dds_path: str):
    """Read just the DDS header and return True/False, or None for an unknown format."""
    try:
        with open(dds_path, "rb") as stream:
            header = stream.read(148)
    except OSError:
        return None

    if len(header) < 128 or header[:4] != b"DDS ":
        return None

    pixel_flags = int.from_bytes(header[80:84], "little")
    fourcc = header[84:88].decode("ascii", errors="ignore").replace("\x00", "").strip().upper()
    alpha_mask = int.from_bytes(header[104:108], "little")

    if fourcc == "DX10":
        if len(header) < 148:
            return None
        dxgi_format = int.from_bytes(header[128:132], "little")
        if dxgi_format in {74, 75, 77, 78}:  # BC2 / BC3
            return True
        if dxgi_format in {71, 72}:  # BC1
            return bool(pixel_flags & 0x1)
        return None

    if fourcc in {"DXT2", "DXT3", "DXT4", "DXT5", "BC2", "BC3"}:
        return True
    if fourcc in {"DXT1", "BC1"}:
        return bool(pixel_flags & 0x1)
    if not fourcc:
        return bool(alpha_mask or (pixel_flags & 0x1))
    return None

def _tex_export_base_color_suffix(source_item=None, target_dir: str = "", material_base: str = "") -> str:
    if source_item:
        declared = _base_color_suffix(source_item.get("basename") or source_item.get("path") or "")
        if declared:
            return declared
        detected = _dds_file_has_alpha_channel(source_item.get("path") or "")
        if detected is not None:
            return "_ca" if detected else "_co"

    stem = _base_color_stem(material_base)
    if target_dir and stem:
        for suffix in ("_ca", "_co"):
            for ext in (".paa", ".png"):
                if os.path.isfile(os.path.join(target_dir, stem + suffix + ext)):
                    return suffix
    return "_co"

def _tex_export_base_color_tried_names(material_base: str):
    return tuple(_base_color_variant_bases(material_base, include_legacy=True))

def _sanitize_tex_export_base(base: str) -> str:
    from .nh_textures import (_strip_blender_numeric_suffix)
    raw = _norm_path(str(base or "")).strip().strip("'\"")
    if not raw or _is_placeholder_material_name(raw):
        return ""

    leaf = raw.split("\\")[-1].strip()
    leaf = _strip_blender_numeric_suffix(leaf)
    leaf = os.path.splitext(leaf)[0]
    leaf = _strip_blender_numeric_suffix(leaf)
    if not leaf or _is_placeholder_material_name(leaf):
        return ""

    leaf = re.sub(r"\s+", "_", leaf)
    leaf = re.sub(r'[<>:"/\\|?*]+', "_", leaf)
    leaf = re.sub(r"_+", "_", leaf).strip(" _.")
    if not leaf or _is_placeholder_material_name(leaf):
        return ""
    return leaf

def _is_texture_converter_test_output(name_or_path: str) -> bool:
    base = _sanitize_tex_export_base(name_or_path).lower()
    return any(base.endswith(suffix) for suffix in _TEXTURE_CONVERTER_TEST_SUFFIXES)

def _strip_tex_export_suffixes(base: str) -> str:
    cleaned = _sanitize_tex_export_base(base)
    if not cleaned:
        return ""

    previous = None
    while cleaned and previous != cleaned:
        previous = cleaned
        cleaned = _TEX_EXPORT_TEXTURE_SUFFIX_RE.sub("", cleaned)
    return cleaned or _sanitize_tex_export_base(base)

def _tex_export_source_key(base: str) -> str:
    return _sanitize_tex_export_base(base).lower()

def _scan_source_dds_files(source_root: str):
    dds_map = {}
    scanned = 0
    if not os.path.isdir(source_root):
        return dds_map, scanned

    source_root = os.path.normpath(source_root)
    for root, _, files in os.walk(source_root):
        for fn in files:
            if os.path.splitext(fn)[1].lower() != ".dds":
                continue
            scanned += 1
            full = os.path.normpath(os.path.join(root, fn))
            base = _sanitize_tex_export_base(fn)
            if not base:
                continue
            try:
                rel_dir = os.path.relpath(root, source_root)
            except Exception:
                rel_dir = ""
            if rel_dir == ".":
                rel_dir = ""
            item = {
                "path": _norm_path(full),
                "rel_dir": _norm_path(rel_dir),
                "basename": base,
            }
            dds_map.setdefault(base.lower(), []).append(item)
    return dds_map, scanned

def _scan_source_dds_files_from_roots(source_roots):
    dds_map = {}
    scanned_total = 0
    for source_root in source_roots or []:
        print(f"Texture source root: {source_root}")
        current_map, scanned = _scan_source_dds_files(source_root)
        scanned_total += scanned
        for key, items in current_map.items():
            dds_map.setdefault(key, []).extend(items)
    return dds_map, scanned_total

def _find_tex_export_dds(dds_map, *base_names, preferred_rel_dir=""):
    preferred_rel_dir = _norm_path(preferred_rel_dir or "").lower()
    for base in base_names:
        key = _tex_export_source_key(base)
        if not key:
            continue
        items = dds_map.get(key)
        if not items:
            continue
        if isinstance(items, dict):
            items = [items]
        if preferred_rel_dir:
            for item in items:
                if _norm_path(item.get("rel_dir", "")).lower() == preferred_rel_dir:
                    return item
        return items[0]
    return None

def _tex_export_rel_dir_from_path(abs_path: str, target_root: str) -> str:
    if not abs_path or not target_root:
        return ""
    try:
        abs_norm = os.path.abspath(os.path.normpath(abs_path))
        root_norm = os.path.abspath(os.path.normpath(target_root))
        common = os.path.commonpath([abs_norm, root_norm])
        if os.path.normcase(common) != os.path.normcase(root_norm):
            return ""
        rel = os.path.relpath(os.path.dirname(abs_norm), root_norm)
        if rel == ".":
            return ""
        return _norm_path(rel)
    except Exception:
        return ""

def _to_dayz_relative_texture_path(abs_path, target_root, warnings=None):
    path_norm = _norm_path(abs_path or "").strip()
    if not path_norm:
        return ""

    target_root_norm = _norm_path(target_root or "").strip()
    target_name = os.path.basename(target_root_norm.rstrip("\\/")) if target_root_norm else ""
    if target_name:
        lower = path_norm.lower().lstrip("\\/")
        if lower == target_name.lower() or lower.startswith(target_name.lower() + "\\"):
            return path_norm

    try:
        abs_norm = os.path.abspath(os.path.normpath(path_norm))
        target_abs = os.path.abspath(os.path.normpath(target_root_norm))
        parent = os.path.dirname(target_abs)
        common = os.path.commonpath([abs_norm, parent])
        if os.path.normcase(common) == os.path.normcase(parent):
            return _norm_path(os.path.relpath(abs_norm, parent))
    except Exception:
        pass

    drive, tail = os.path.splitdrive(path_norm)
    if drive and tail:
        fallback = _norm_path(tail.lstrip("\\/"))
        if warnings is not None:
            warnings.append(f"Could not make DayZ-relative path, stripped drive: {path_norm} -> {fallback}")
        return fallback

    if warnings is not None:
        warnings.append(f"Could not make DayZ-relative path: {path_norm}")
    return path_norm

def _build_dayz_super_rvmat_text(nohq_texture: str, smdi_texture: str) -> str:
    return f"""ambient[] = {{0.6,0.6,0.6,1}};
diffuse[] = {{1,1,1,1}};
forcedDiffuse[] = {{0,0,0,1}};
emmisive[] = {{0,0,0,1}};
specular[] = {{0.16,0.16,0.16,1}};
specularPower = 100.0;
PixelShaderID = "Super";
VertexShaderID = "Super";

class Stage1
{{
	texture = "{nohq_texture}";
	uvSource = "tex";
	class uvTransform
	{{
		aside[] = {{1.0,0.0,0.0}};
		up[] = {{0.0,1.0,0.0}};
		dir[] = {{0.0,0.0,0.0}};
		pos[] = {{0.0,0.0,0.0}};
	}};
}};

class Stage2
{{
	texture = "#(argb,8,8,3)color(0.5,0.5,0.5,1,DT)";
	uvSource = "tex1";
	class uvTransform
	{{
		aside[] = {{1.0,0.0,0.0}};
		up[] = {{0.0,1.0,0.0}};
		dir[] = {{0.0,0.0,0.0}};
		pos[] = {{0.0,0.0,0.0}};
	}};
}};

class Stage3
{{
	texture = "#(argb,8,8,3)color(0,0,0,0,MC)";
	uvSource = "tex1";
	class uvTransform
	{{
		aside[] = {{1.0,0.0,0.0}};
		up[] = {{0.0,1.0,0.0}};
		dir[] = {{0.0,0.0,0.0}};
		pos[] = {{0.0,0.0,0.0}};
	}};
}};

class Stage4
{{
	texture = "#(argb,8,8,3)color(1,1,1,1,AS)";
	uvSource = "tex1";
	class uvTransform
	{{
		aside[] = {{1.0,0.0,0.0}};
		up[] = {{0.0,1.0,0.0}};
		dir[] = {{0.0,0.0,0.0}};
		pos[] = {{0.0,0.0,0.0}};
	}};
}};

class Stage5
{{
	texture = "{smdi_texture}";
	uvSource = "tex";
	class uvTransform
	{{
		aside[] = {{1.0,0.0,0.0}};
		up[] = {{0.0,1.0,0.0}};
		dir[] = {{0.0,0.0,0.0}};
		pos[] = {{0.0,0.0,0.0}};
	}};
}};

class Stage6
{{
	texture = "#(ai,64,64,1)fresnel(0.4,0.2)";
	uvSource = "tex";
	class uvTransform
	{{
		aside[] = {{1.0,0.0,0.0}};
		up[] = {{0.0,1.0,0.0}};
		dir[] = {{0.0,0.0,0.0}};
		pos[] = {{0.0,0.0,0.0}};
	}};
}};

class Stage7
{{
	texture = "dz\\data\\data\\env_land_co.paa";
	uvSource = "tex";
	class uvTransform
	{{
		aside[] = {{1.0,0.0,0.0}};
		up[] = {{0.0,1.0,0.0}};
		dir[] = {{0.0,0.0,0.0}};
		pos[] = {{0.0,0.0,0.0}};
	}};
}};
"""

def _generate_dayz_super_rvmat(base_path, co_path=None, nohq_path=None, smdi_path=None, target_root=None, warnings=None):
    target_root = target_root or os.path.dirname(base_path)
    nohq_texture = (
        _to_dayz_relative_texture_path(nohq_path, target_root, warnings=warnings)
        if nohq_path else (
            _to_dayz_relative_texture_path(co_path, target_root, warnings=warnings)
            if co_path else _TEX_EXPORT_NOHQ_FALLBACK
        )
    )
    smdi_texture = (
        _to_dayz_relative_texture_path(smdi_path, target_root, warnings=warnings)
        if smdi_path else _TEX_EXPORT_SMDI_FALLBACK
    )
    folder = os.path.dirname(base_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(base_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_build_dayz_super_rvmat_text(nohq_texture, smdi_texture))
    return base_path

def _tex_export_should_write(path: str, settings) -> bool:
    if bool(getattr(settings, "export_overwrite_existing", False)):
        return True
    if bool(getattr(settings, "export_only_missing", True)):
        return not os.path.exists(path)
    return True

def _tex_export_existing_preferred(png_path: str, paa_path: str, prefer_paa: bool):
    if prefer_paa and os.path.isfile(paa_path):
        return paa_path
    if os.path.isfile(png_path):
        return png_path
    if os.path.isfile(paa_path):
        return paa_path
    return None

def _save_rgba_pixels_to_png(width: int, height: int, pixels, output_png: str):
    folder = os.path.dirname(output_png)
    if folder:
        os.makedirs(folder, exist_ok=True)

    image_name = os.path.basename(output_png) or "NH_TextureExport"
    out_img = bpy.data.images.new(image_name, width=width, height=height, alpha=True, float_buffer=False)
    try:
        out_img.pixels.foreach_set(pixels)
        try:
            out_img.update()
        except Exception:
            pass
        out_img.filepath_raw = output_png
        out_img.file_format = "PNG"
        out_img.save()
    finally:
        try:
            bpy.data.images.remove(out_img)
        except Exception:
            pass

def _get_addon_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return os.getcwd()

def _get_texreplace_settings_safe(settings=None):
    if settings is not None:
        return settings
    try:
        return bpy.context.scene.cray_texreplace_settings
    except Exception:
        return None

def _path_is_scripts_addons_dir(path: str) -> bool:
    parts = os.path.normpath(path or "").replace("/", "\\").lower().split("\\")
    return len(parts) >= 2 and parts[-2:] == ["scripts", "addons"]

def _texture_tools_folder_from_settings(settings=None) -> str:
    settings = _get_texreplace_settings_safe(settings)
    return _tex_export_resolve_path(getattr(settings, "texture_tools_folder", "")) if settings is not None else ""

def _texture_tool_candidates(filename: str, settings=None, include_bin=False):
    addon_dir = _get_addon_dir()
    bundled_tools_dir = "_nh_blender_tools"
    candidates = []
    configured = _texture_tools_folder_from_settings(settings)
    if configured:
        candidates.append(os.path.join(configured, "xray_tex_converter", filename))
        if include_bin:
            candidates.append(os.path.join(configured, "xray_tex_converter", "bin", filename))
            candidates.append(os.path.join(configured, "bin", filename))
        candidates.append(os.path.join(configured, filename))

    candidates.append(os.path.join(addon_dir, bundled_tools_dir, "xray_tex_converter", filename))
    if include_bin:
        candidates.append(os.path.join(addon_dir, bundled_tools_dir, "xray_tex_converter", "bin", filename))
    candidates.append(os.path.join(addon_dir, "tools", "xray_tex_converter", filename))
    if include_bin:
        candidates.append(os.path.join(addon_dir, "tools", "xray_tex_converter", "bin", filename))

    if _path_is_scripts_addons_dir(addon_dir):
        candidates.append(os.path.join(addon_dir, bundled_tools_dir, "xray_tex_converter", filename))
        if include_bin:
            candidates.append(os.path.join(addon_dir, bundled_tools_dir, "xray_tex_converter", "bin", filename))
        candidates.append(os.path.join(addon_dir, "tools", "xray_tex_converter", filename))
        if include_bin:
            candidates.append(os.path.join(addon_dir, "tools", "xray_tex_converter", "bin", filename))

    return _unique_ci(_norm_path(path) for path in candidates)

def _first_existing_texture_tool(filename: str, settings=None, include_bin=False) -> str:
    for path in _texture_tool_candidates(filename, settings=settings, include_bin=include_bin):
        if os.path.isfile(path):
            return _norm_path(path)
    return ""

def _expected_texture_tools_folder(settings=None) -> str:
    configured = _texture_tools_folder_from_settings(settings)
    if configured:
        return _norm_path(configured)

    addon_dir = _get_addon_dir()
    if _path_is_scripts_addons_dir(addon_dir):
        return _norm_path(os.path.join(addon_dir, "_nh_blender_tools"))
    return _norm_path(os.path.join(addon_dir, "tools"))

def _get_expected_python_dds_converter_paths(settings=None):
    return _texture_tool_candidates("dds_python.py", settings=settings)

def _get_bundled_xray_converter_js(settings=None):
    return _first_existing_texture_tool("converter.js", settings=settings)
