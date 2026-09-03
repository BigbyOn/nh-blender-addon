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

# nh_collider.py
# auto-split slice; cross-module refs resolved with in-function imports

def _set_collider_settings_object(context, attr_name, obj):
    from .nh_snap import (_tag_redraw_all_areas)
    cs = getattr(getattr(context, "scene", None), "cray_collider_settings", None)
    if cs is None or not hasattr(cs, attr_name):
        return

    current = getattr(cs, attr_name, None)
    try:
        if current == obj:
            setattr(cs, attr_name, None)
        else:
            setattr(cs, attr_name, obj)
    except Exception:
        pass

    try:
        context.view_layer.update()
    except Exception:
        pass
    _tag_redraw_all_areas(context)


def _flush_edit_mesh_normals_after_bmesh_write(context, mesh):
    from .nh_snap import (_tag_redraw_all_areas)
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
    try:
        mesh.update()
    except Exception:
        pass
    try:
        context.view_layer.update()
    except Exception:
        pass
    _tag_redraw_all_areas(context)


def _force_edit_mesh_view_refresh_exp(context, obj):
    from .nh_snap import (_select_object_in_view_layer, _tag_redraw_all_areas)
    if obj is None or getattr(obj, "type", None) != "MESH":
        return
    if getattr(obj, "mode", "") != "EDIT":
        return

    view_layer = getattr(context, "view_layer", None)
    try:
        if view_layer is not None:
            _select_object_in_view_layer(context, obj, active=True)
    except Exception:
        pass

    try:
        bpy.ops.object.mode_set(mode="OBJECT")
        try:
            obj.data.update(calc_edges=True)
        except Exception:
            pass
        try:
            if view_layer is not None:
                view_layer.update()
        except Exception:
            pass
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            bpy.ops.mesh.select_mode(type="FACE")
        except Exception:
            pass
    except Exception:
        try:
            bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
        except Exception:
            pass

    _tag_redraw_all_areas(context)


def _sync_material_selection(context, material_attr: str, items_fn, none_value: str, preferred_name=""):
    from .nh_scatter import (_MATERIAL_ADD_NEW)
    from .nh_snap import (_tag_redraw_all_areas)
    global _COLLIDER_MATERIAL_SELECTION_SYNCING

    cs = getattr(getattr(context, "scene", None), "cray_collider_settings", None)
    if cs is None:
        return

    items = items_fn(None, context)
    valid_values = [
        item[0]
        for item in items
        if item and item[0] not in {none_value, _MATERIAL_ADD_NEW}
    ]
    current = (getattr(cs, material_attr, "") or "").strip()
    preferred_name = (preferred_name or "").strip()

    if preferred_name and preferred_name in valid_values:
        chosen = preferred_name
    elif current and current in valid_values:
        chosen = current
    elif valid_values:
        chosen = valid_values[0]
    else:
        chosen = none_value

    _COLLIDER_MATERIAL_SELECTION_SYNCING = True
    try:
        try:
            setattr(cs, material_attr, chosen)
        except Exception:
            pass
    finally:
        _COLLIDER_MATERIAL_SELECTION_SYNCING = False

    _tag_redraw_all_areas(context)


def _sync_roadway_material_selection(context, preferred_name=""):
    from .nh_scatter import (_ROADWAY_MATERIAL_NONE, get_roadway_material_enum_items)
    _sync_material_selection(
        context,
        "roadway_material",
        get_roadway_material_enum_items,
        _ROADWAY_MATERIAL_NONE,
        preferred_name,
    )


def _sync_fire_geometry_material_selection(context, preferred_name=""):
    from .nh_scatter import (_FIRE_GEOMETRY_MATERIAL_NONE, get_fire_geometry_material_enum_items)
    _sync_material_selection(
        context,
        "fire_geometry_material",
        get_fire_geometry_material_enum_items,
        _FIRE_GEOMETRY_MATERIAL_NONE,
        preferred_name,
    )


def _get_selected_material_from_object(obj, selected_name: str, *, create_name: str = ""):
    if obj is None or obj.type != "MESH":
        return None

    selected_name = (selected_name or "").strip()
    fallback_mat = None

    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue
        if fallback_mat is None:
            fallback_mat = mat
        if selected_name and mat.name == selected_name:
            return mat

    if fallback_mat is not None or not create_name:
        return fallback_mat

    mat = bpy.data.materials.new(create_name)
    obj.data.materials.append(mat)
    return mat


def _get_selected_material_from_objects(objects, selected_name: str, *, create_name: str = ""):
    from .nh_scatter import (_FIRE_GEOMETRY_MATERIAL_NONE, _MATERIAL_ADD_NEW, _ROADWAY_MATERIAL_NONE)
    objects = [
        obj for obj in objects or []
        if obj is not None and getattr(obj, "type", None) == "MESH"
    ]
    selected_name = (selected_name or "").strip()

    if selected_name and selected_name not in {_MATERIAL_ADD_NEW, _ROADWAY_MATERIAL_NONE, _FIRE_GEOMETRY_MATERIAL_NONE}:
        for obj in objects:
            for slot in getattr(obj, "material_slots", []) or []:
                mat = getattr(slot, "material", None)
                if mat is not None and getattr(mat, "name", "") == selected_name:
                    return mat
        mat = bpy.data.materials.get(selected_name)
        if mat is not None:
            return mat

    fallback_mat = None
    for obj in objects:
        for slot in getattr(obj, "material_slots", []) or []:
            mat = getattr(slot, "material", None)
            if mat is not None:
                fallback_mat = mat
                break
        if fallback_mat is not None:
            break

    if fallback_mat is not None or not create_name:
        return fallback_mat

    target_obj = objects[0] if objects else None
    if target_obj is None:
        return None
    mat = bpy.data.materials.new(create_name)
    target_obj.data.materials.append(mat)
    return mat


def _get_selected_roadway_material(context):
    cs = getattr(getattr(context, "scene", None), "cray_collider_settings", None)
    if cs is None:
        return None

    roadway_obj = getattr(cs, "roadway_object", None)
    selected_name = getattr(cs, "roadway_material", "") or ""
    return _get_selected_material_from_object(roadway_obj, selected_name)


def _get_selected_fire_geometry_material(context, *, create_name: str = ""):
    from .nh_scatter import (_collider_material_selection_objects, _resolve_fire_geometry_object_for_material)
    cs = getattr(getattr(context, "scene", None), "cray_collider_settings", None)
    if cs is None:
        return None

    fire_obj = _resolve_fire_geometry_object_for_material(context)
    selected_name = getattr(cs, "fire_geometry_material", "") or ""
    objects = _collider_material_selection_objects(context, "fire_geometry_object", fire_obj) if fire_obj is not None else []
    if not objects and fire_obj is not None:
        objects = [fire_obj]
    return _get_selected_material_from_objects(objects, selected_name, create_name=create_name)


def _material_slot_indices_for_material(obj, material):
    if obj is None or getattr(obj, "type", None) != "MESH" or material is None:
        return set()
    target_name = (getattr(material, "name", "") or "").strip().lower()
    indices = set()
    for idx, slot in enumerate(getattr(obj, "material_slots", []) or []):
        mat = getattr(slot, "material", None)
        if mat is None:
            continue
        if mat == material or ((getattr(mat, "name", "") or "").strip().lower() == target_name and target_name):
            indices.add(idx)
    return indices


def _select_material_faces_in_objects(context, objects, material):
    from .nh_snap import (_deselect_all_in_view_layer, _ensure_object_selectable_in_view_layer, _select_object_in_view_layer, _tag_redraw_all_areas)
    objects = [
        obj for obj in objects or []
        if obj is not None and getattr(obj, "type", None) == "MESH" and getattr(obj, "data", None) is not None
    ]
    if not objects or material is None:
        return {"objects": 0, "faces": 0}

    objects = [
        obj for obj in objects
        if _ensure_object_selectable_in_view_layer(context, obj)
    ]
    if not objects:
        return {"objects": 0, "faces": 0}

    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    _deselect_all_in_view_layer(context)
    selected_objects = []
    selected_faces = 0
    slot_indices_by_object = {}
    for obj in objects:
        slot_indices = _material_slot_indices_for_material(obj, material)
        if not slot_indices:
            continue
        object_selected_faces = 0
        for poly in getattr(obj.data, "polygons", []) or []:
            if int(getattr(poly, "material_index", 0) or 0) in slot_indices:
                object_selected_faces += 1
        if object_selected_faces <= 0:
            continue
        for poly in getattr(obj.data, "polygons", []) or []:
            poly.select = False
        try:
            obj.data.update()
        except Exception:
            pass
        _select_object_in_view_layer(context, obj)
        selected_objects.append(obj)
        selected_faces += object_selected_faces
        try:
            slot_indices_by_object[obj.as_pointer()] = slot_indices
        except Exception:
            slot_indices_by_object[id(obj)] = slot_indices

    if selected_objects:
        _select_object_in_view_layer(context, selected_objects[0], active=True)
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            context.tool_settings.mesh_select_mode = (False, False, True)
        except Exception:
            pass
        try:
            bpy.ops.mesh.select_mode(type="FACE")
        except Exception:
            pass
        for obj in selected_objects:
            try:
                slot_indices = slot_indices_by_object.get(obj.as_pointer(), set())
            except Exception:
                slot_indices = slot_indices_by_object.get(id(obj), set())
            if not slot_indices:
                continue
            try:
                bm = bmesh.from_edit_mesh(obj.data)
                bm.faces.ensure_lookup_table()
                for vert in bm.verts:
                    vert.select_set(False)
                for edge in bm.edges:
                    edge.select_set(False)
                for face in bm.faces:
                    face.select_set(int(face.material_index) in slot_indices)
                bm.select_flush_mode()
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            except Exception:
                pass
    _tag_redraw_all_areas(context)
    return {"objects": len(selected_objects), "faces": selected_faces}


def _apply_collider_visual_style(target_obj):
    from .nh_scatter import (_COLLIDER_OBJECT_COLOR)
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


def _is_existing_roadway_target(obj) -> bool:
    from .nh_scatter import (_ROADWAY_LOD_TOKEN)
    from .nh_snap import (_collider_lod_name, _is_collider_lod_mesh_object, _logical_collection_name)
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    if _is_collider_lod_mesh_object(obj, lod_token=_ROADWAY_LOD_TOKEN):
        return True
    return _logical_collection_name(getattr(obj, "name", "") or "") == _logical_collection_name(_collider_lod_name(_ROADWAY_LOD_TOKEN))


def _pick_roadway_lod_object(context, source_obj):
    from .nh_scatter import (_MISC_COLLECTION_NAME, _ROADWAY_LOD_TOKEN)
    from .nh_snap import (_collider_lod_name, _find_named_child_collection, _preferred_collider_parent_collection)
    parent = _preferred_collider_parent_collection(context, source_obj)
    misc_collection = _find_named_child_collection(parent, _MISC_COLLECTION_NAME)
    if misc_collection is None:
        return None

    expected_name = _collider_lod_name(_ROADWAY_LOD_TOKEN)
    direct = misc_collection.objects.get(expected_name)
    if _is_existing_roadway_target(direct):
        return direct

    for obj in misc_collection.objects:
        if _is_existing_roadway_target(obj):
            return obj

    return None


def _ensure_roadway_lod_object(context, source_obj, preferred_obj=None):
    from .nh_scatter import (_ROADWAY_LOD_TOKEN, _ROADWAY_OBJECT_COLOR)
    from .nh_snap import (_collider_lod_name, _ensure_misc_collection, _set_collider_lod_p3d_props)
    from .nh_textures import (_collection_directly_contains_object)
    misc_collection = _ensure_misc_collection(context, source_obj)
    if misc_collection is None:
        misc_collection = context.scene.collection

    if (
        _is_existing_roadway_target(preferred_obj)
        and _collection_directly_contains_object(misc_collection, preferred_obj)
    ):
        target_obj = preferred_obj
    else:
        target_obj = _pick_roadway_lod_object(context, source_obj)

    if target_obj is None:
        obj_name = _collider_lod_name(_ROADWAY_LOD_TOKEN)
        mesh = bpy.data.meshes.new(obj_name)
        target_obj = bpy.data.objects.new(obj_name, mesh)
        misc_collection.objects.link(target_obj)
        if source_obj is not None:
            target_obj.matrix_world = source_obj.matrix_world.copy()

    _set_collider_lod_p3d_props(target_obj, _ROADWAY_LOD_TOKEN)
    _apply_object_visual_style(target_obj, _ROADWAY_OBJECT_COLOR)
    _enable_collider_object_color_preview(context)
    try:
        target_obj.show_all_edges = True
    except Exception:
        pass
    try:
        target_obj.show_name = True
    except Exception:
        pass
    return target_obj


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


def _is_existing_collider_target_for_lod(obj, lod_token) -> bool:
    from .nh_snap import (_collider_lod_name, _is_collider_lod_mesh_object, _logical_collection_name)
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    if _is_collider_lod_mesh_object(obj, lod_token=lod_token):
        return True
    expected_name = _logical_collection_name(_collider_lod_name(lod_token))
    return _logical_collection_name(getattr(obj, "name", "") or "") == expected_name


def _allow_collider_exp_in_place_target_exp(source_obj, lod_token) -> bool:
    from .nh_scatter import (_COLLIDER_COLLECTION_NAME)
    from .nh_snap import (_collider_lod_name, _is_collider_lod_mesh_object, _logical_collection_name, _object_in_logical_collection)
    if (
        source_obj is None
        or getattr(source_obj, "type", None) != "MESH"
        or getattr(source_obj, "mode", "") != "EDIT"
    ):
        return False
    if _is_collider_lod_mesh_object(source_obj, lod_token=lod_token):
        return True
    expected_name = _logical_collection_name(_collider_lod_name(lod_token))
    return (
        _object_in_logical_collection(source_obj, _COLLIDER_COLLECTION_NAME)
        and _logical_collection_name(getattr(source_obj, "name", "") or "") == expected_name
    )


def _ensure_collider_lod_object(
    context,
    source_obj,
    lod_token,
    preferred_obj=None,
    exclude_obj=None,
    allow_any_preferred_lod=False,
    preserve_existing_lod=False,
):
    from .nh_scatter import (_COLLIDER_LOD_NAMES, _actual_collider_lod_token_from_object, _collider_lod_token_from_object)
    from .nh_snap import (_collider_lod_name, _ensure_collider_collection, _pick_collider_lod_object, _set_collider_lod_p3d_props)
    from .nh_textures import (_collection_directly_contains_object)
    collider_collection = _ensure_collider_collection(context, source_obj)
    if collider_collection is None:
        collider_collection = context.scene.collection

    preferred_is_usable = (
        preferred_obj != exclude_obj
        and (
            _is_existing_collider_target_for_lod(preferred_obj, lod_token)
            or (
                allow_any_preferred_lod
                and getattr(preferred_obj, "type", None) == "MESH"
                and _collider_lod_token_from_object(preferred_obj, allow_name_fallback=True) in _COLLIDER_LOD_NAMES
            )
        )
        and _collection_directly_contains_object(collider_collection, preferred_obj)
    )
    if preferred_is_usable:
        target_obj = preferred_obj
    else:
        target_obj = _pick_collider_lod_object(context, source_obj, lod_token, exclude_obj=exclude_obj)

    if target_obj is None:
        obj_name = _collider_lod_name(lod_token)
        mesh = bpy.data.meshes.new(obj_name)
        target_obj = bpy.data.objects.new(obj_name, mesh)
        collider_collection.objects.link(target_obj)
        if source_obj is not None:
            target_obj.matrix_world = source_obj.matrix_world.copy()

    existing_lod = _actual_collider_lod_token_from_object(target_obj)
    if not (preserve_existing_lod and existing_lod in _COLLIDER_LOD_NAMES):
        _set_collider_lod_p3d_props(target_obj, lod_token)
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


def _selected_face_islands_for_reconvex(bm):
    allowed_faces = {face for face in bm.faces if face is not None and face.is_valid and not face.hide}
    touched_faces = set()
    for face in allowed_faces:
        if face.select:
            touched_faces.add(face)
            continue
        if any(edge.select for edge in face.edges if edge is not None and edge.is_valid):
            touched_faces.add(face)
            continue
        if any(vert.select for vert in face.verts if vert is not None and vert.is_valid):
            touched_faces.add(face)

    islands = []
    processed = set()
    for seed_face in sorted(touched_faces, key=lambda item: item.index):
        if seed_face in processed or seed_face not in allowed_faces:
            continue
        island = [
            face for face in _collect_connected_face_island(seed_face, allowed_faces)
            if face is not None and face.is_valid
        ]
        island_set = set(island)
        processed.update(island_set)
        if island_set.intersection(touched_faces):
            islands.append(island)
    return islands


def _most_common_material_index_from_faces(faces):
    counts = {}
    for face in faces or []:
        if face is None or not face.is_valid:
            continue
        idx = int(getattr(face, "material_index", 0) or 0)
        counts[idx] = counts.get(idx, 0) + 1
    if not counts:
        return 0
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _replace_face_islands_with_clean_hull_in_edit_object(
    context,
    target_obj,
    hull_data,
    island_faces,
    *,
    material_index=0,
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

    faces_to_delete = [face for face in island_faces or [] if face is not None and face.is_valid]
    if not faces_to_delete:
        raise RuntimeError("No selected component faces to replace")

    old_edges = {
        edge for face in faces_to_delete
        for edge in face.edges
        if edge is not None and edge.is_valid
    }
    old_verts = {
        vert for face in faces_to_delete
        for vert in face.verts
        if vert is not None and vert.is_valid
    }
    before_vert_count = len(bm.verts)
    before_face_count = len(bm.faces)

    bmesh.ops.delete(bm, geom=faces_to_delete, context="FACES")
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    loose_edges = [edge for edge in old_edges if edge.is_valid and len(edge.link_faces) == 0]
    if loose_edges:
        bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

    loose_verts = [
        vert for vert in old_verts
        if vert.is_valid and len(vert.link_edges) == 0 and len(vert.link_faces) == 0
    ]
    if loose_verts:
        bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

    new_verts = [bm.verts.new(point.copy()) for point in hull_data.get("verts", [])]
    bm.verts.ensure_lookup_table()

    material_index = max(0, int(material_index or 0))
    new_faces = []
    for face_indices in hull_data.get("faces", []):
        face_verts = [new_verts[idx] for idx in face_indices if 0 <= idx < len(new_verts)]
        if len(face_verts) < 3 or len(set(face_verts)) < 3:
            continue
        try:
            face = bm.faces.new(face_verts)
        except ValueError:
            continue
        try:
            face.material_index = material_index
        except Exception:
            pass
        new_faces.append(face)

    if not new_faces:
        raise RuntimeError("Could not write re-convex hull back to the mesh")

    if recalc_normals:
        bmesh.ops.recalc_face_normals(bm, faces=new_faces)

    _select_only_faces_in_bmesh(bm, new_faces)
    bm.normal_update()
    _flush_edit_mesh_normals_after_bmesh_write(context, mesh)
    if context.mode == "EDIT_MESH":
        try:
            bpy.ops.mesh.select_mode(type="FACE")
        except Exception:
            pass

    return {
        "verts_before": before_vert_count,
        "faces_before": before_face_count,
        "verts_after": len(bm.verts),
        "faces_after": len(bm.faces),
        "verts_added": len(new_verts),
        "faces_added": len(new_faces),
        "faces_removed": len(faces_to_delete),
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
        bm.verts.index_update()
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
    bm.verts.index_update()
    return [vert.index for vert in new_verts if vert.is_valid]


def _append_selected_faces_to_object(target_obj, source_obj, recalc_normals=True, weld_distance=0.0):
    from .nh_textures import (_ensure_roadway_material)
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

        if weld_distance > 0.0 and created_faces:
            weld_verts = list({vert for face in created_faces if face.is_valid for vert in face.verts})
            bmesh.ops.remove_doubles(bm, verts=weld_verts, dist=weld_distance)
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
    from .nh_snap import (_deselect_all_in_view_layer, _select_object_in_view_layer)
    if obj is None or obj.type != "MESH":
        raise RuntimeError("Target object must be a mesh")

    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    _deselect_all_in_view_layer(context)
    _select_object_in_view_layer(context, obj, active=True)
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
    from .nh_snap import (_select_object_in_view_layer)
    if obj is None or obj.type != "MESH":
        return
    if bpy.data.objects.get(obj.name) is None:
        return
    try:
        _select_object_in_view_layer(context, obj, active=True)
    except Exception:
        pass
    try:
        bpy.ops.object.mode_set(mode="EDIT")
    except Exception:
        pass


def _resolve_fake_terrain_source_object(context, settings):
    candidates = []
    for obj in (
        getattr(settings, "fake_terrain_source_object", None),
        getattr(settings, "source_object", None),
        getattr(getattr(context, "view_layer", None), "objects", None).active
        if getattr(context, "view_layer", None) is not None
        else None,
    ):
        if obj is not None and obj not in candidates:
            candidates.append(obj)

    for obj in getattr(context, "selected_objects", []) or []:
        if obj is not None and obj not in candidates:
            candidates.append(obj)

    target_obj = getattr(settings, "fake_terrain_target_object", None)
    for obj in candidates:
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        if obj == target_obj:
            continue
        return obj
    return None


def _resolve_fake_terrain_preferred_target(context, settings, lod_token=None):
    from .nh_scatter import (_COLLIDER_LOD_NAMES, _FAKE_TERRAIN_TARGET_NONE, _FIRE_GEOMETRY_LOD_TOKEN, _collider_lod_token_from_object, _fake_terrain_context_root_collection, _fake_terrain_target_candidates)
    from .nh_textures import (_object_is_directly_or_indirectly_in_collection)
    if settings is None:
        return None

    choice = str(getattr(settings, "fake_terrain_target_choice", "") or "")
    choice_target = bpy.data.objects.get(choice) if choice and choice != _FAKE_TERRAIN_TARGET_NONE else None
    candidates = []

    for obj in (
        getattr(settings, "fake_terrain_target_object", None),
        choice_target,
    ):
        if obj is not None and obj not in candidates:
            candidates.append(obj)

    for _candidate_lod, obj in _fake_terrain_target_candidates(context, settings):
        if obj is not None and obj not in candidates:
            candidates.append(obj)

    if str(lod_token or "") == _FIRE_GEOMETRY_LOD_TOKEN:
        obj = getattr(settings, "fire_geometry_object", None)
        if obj is not None and obj not in candidates:
            candidates.append(obj)
    obj = getattr(settings, "geometry_object", None)
    if obj is not None and obj not in candidates:
        candidates.append(obj)

    root = _fake_terrain_context_root_collection(context, settings)
    for obj in candidates:
        if getattr(obj, "type", None) != "MESH":
            continue
        if root is not None:
            try:
                if not _object_is_directly_or_indirectly_in_collection(root, obj):
                    continue
            except Exception:
                pass
        obj_lod = _collider_lod_token_from_object(obj, allow_name_fallback=True)
        if obj_lod not in _COLLIDER_LOD_NAMES:
            continue
        if lod_token is None or str(lod_token) == obj_lod:
            return obj
    return None


def _fake_terrain_selected_face_indices(source_obj):
    if source_obj is None or getattr(source_obj, "type", None) != "MESH":
        raise RuntimeError("Source Visual must be a mesh")
    if getattr(source_obj, "mode", "") != "EDIT":
        raise RuntimeError("Enter Edit Mode on Source Visual and select terrain faces")

    selected = _fake_terrain_selected_face_indices_if_edit(source_obj)
    if not selected:
        raise RuntimeError("Select terrain faces on Source Visual in Edit Mode")
    return selected


def _fake_terrain_selected_face_indices_if_edit(source_obj):
    if source_obj is None or getattr(source_obj, "type", None) != "MESH":
        return set()
    if getattr(source_obj, "mode", "") != "EDIT":
        return set()

    try:
        source_obj.update_from_editmode()
    except Exception:
        pass

    selected = {
        int(poly.index)
        for poly in getattr(source_obj.data, "polygons", []) or []
        if bool(getattr(poly, "select", False))
    }
    return selected


def _collect_fake_terrain_source_triangles(source_obj, selected_face_indices):
    if source_obj is None or getattr(source_obj, "type", None) != "MESH":
        raise RuntimeError("Source Visual must be a mesh")

    mesh = source_obj.data
    matrix_world = source_obj.matrix_world.copy()
    selected_face_indices = set(selected_face_indices or [])
    if not selected_face_indices:
        raise RuntimeError("Select terrain faces on Source Visual in Edit Mode")

    triangles = []
    matched_faces = 0

    for poly in mesh.polygons:
        if int(poly.index) not in selected_face_indices:
            continue

        verts = [matrix_world @ mesh.vertices[idx].co for idx in poly.vertices]
        if len(verts) < 3:
            continue

        matched_faces += 1
        material_index = int(getattr(poly, "material_index", 0) or 0)
        for idx in range(1, len(verts) - 1):
            tri = (verts[0].copy(), verts[idx].copy(), verts[idx + 1].copy())
            if (tri[1] - tri[0]).cross(tri[2] - tri[0]).length_squared <= 1e-12:
                continue
            center = (tri[0] + tri[1] + tri[2]) / 3.0
            triangles.append({
                "points": tri,
                "center": center,
                "source_obj": source_obj,
                "material_index": material_index,
            })

    return triangles, matched_faces


def _fake_terrain_source_candidates(context, primary_source_obj, target_obj=None):
    candidates = []

    def add_candidate(obj):
        if obj is None or getattr(obj, "type", None) != "MESH":
            return
        if target_obj is not None and obj == target_obj:
            return
        if obj not in candidates:
            candidates.append(obj)

    add_candidate(primary_source_obj)
    for attr_name in ("objects_in_mode_unique_data", "objects_in_mode"):
        for obj in getattr(context, attr_name, []) or []:
            add_candidate(obj)
    active_obj = (
        getattr(getattr(context, "view_layer", None), "objects", None).active
        if getattr(context, "view_layer", None) is not None
        else None
    )
    add_candidate(active_obj)
    for obj in getattr(context, "selected_objects", []) or []:
        add_candidate(obj)
    return candidates


def _collect_fake_terrain_edit_selection_triangles(context, primary_source_obj, target_obj=None):
    triangles = []
    matched_faces = 0
    source_objects = []

    for obj in _fake_terrain_source_candidates(context, primary_source_obj, target_obj=target_obj):
        selected_face_indices = _fake_terrain_selected_face_indices_if_edit(obj)
        if not selected_face_indices:
            continue
        obj_triangles, obj_matched_faces = _collect_fake_terrain_source_triangles(obj, selected_face_indices)
        if obj_matched_faces > 0:
            matched_faces += obj_matched_faces
            source_objects.append(obj)
        if obj_triangles:
            triangles.extend(obj_triangles)

    if source_objects:
        if not triangles:
            raise RuntimeError("Selected source faces did not contain valid triangles")
        return triangles, matched_faces, source_objects

    selected_face_indices = _fake_terrain_selected_face_indices(primary_source_obj)
    triangles, matched_faces = _collect_fake_terrain_source_triangles(primary_source_obj, selected_face_indices)
    if not triangles:
        raise RuntimeError("Selected source faces did not contain valid triangles")
    return triangles, matched_faces, [primary_source_obj]


def _fake_terrain_det3(rows):
    return (
        rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
        - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
        + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
    )


def _fake_terrain_solve3(matrix_rows, values):
    det = _fake_terrain_det3(matrix_rows)
    if abs(det) <= 1e-12:
        return None

    solved = []
    for col in range(3):
        rows = [list(row) for row in matrix_rows]
        for row_idx in range(3):
            rows[row_idx][col] = values[row_idx]
        solved.append(_fake_terrain_det3(rows) / det)
    return solved


def _fake_terrain_fit_plane(points):
    if not points:
        return (0.0, 0.0, 0.0, 0.0, 0.0)

    count = float(len(points))
    origin_x = sum(point.x for point in points) / count
    origin_y = sum(point.y for point in points) / count

    sx2 = sy2 = sxy = sx = sy = 0.0
    sxz = syz = sz = 0.0
    for point in points:
        x = point.x - origin_x
        y = point.y - origin_y
        sx2 += x * x
        sy2 += y * y
        sxy += x * y
        sx += x
        sy += y
        sxz += x * point.z
        syz += y * point.z
        sz += point.z

    rows = (
        (sx2, sxy, sx),
        (sxy, sy2, sy),
        (sx, sy, count),
    )
    solution = _fake_terrain_solve3(rows, (sxz, syz, sz))
    if solution is None:
        avg_z = sz / count
        return (origin_x, origin_y, 0.0, 0.0, avg_z)

    return (origin_x, origin_y, float(solution[0]), float(solution[1]), float(solution[2]))


def _fake_terrain_plane_z(plane, x, y) -> float:
    origin_x, origin_y, slope_x, slope_y, offset_z = plane
    return slope_x * (x - origin_x) + slope_y * (y - origin_y) + offset_z


def _fake_terrain_unique_xy(points):
    unique = {}
    for point in points:
        key = (round(float(point.x), 5), round(float(point.y), 5))
        unique[key] = key
    return sorted(unique.values())


def _fake_terrain_cross_2d(origin, a, b) -> float:
    return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])


def _fake_terrain_convex_hull_xy(points_xy):
    points = sorted(set(points_xy))
    if len(points) <= 1:
        return points

    lower = []
    for point in points:
        while len(lower) >= 2 and _fake_terrain_cross_2d(lower[-2], lower[-1], point) <= 1e-10:
            lower.pop()
        lower.append(point)

    upper = []
    for point in reversed(points):
        while len(upper) >= 2 and _fake_terrain_cross_2d(upper[-2], upper[-1], point) <= 1e-10:
            upper.pop()
        upper.append(point)

    return lower[:-1] + upper[:-1]


def _fake_terrain_polygon_area_xy(points_xy) -> float:
    if len(points_xy) < 3:
        return 0.0
    area = 0.0
    for idx, point in enumerate(points_xy):
        nxt = points_xy[(idx + 1) % len(points_xy)]
        area += point[0] * nxt[1] - nxt[0] * point[1]
    return area * 0.5


def _fake_terrain_polygon_area_from_vectors_xy(points) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for idx, point in enumerate(points):
        nxt = points[(idx + 1) % len(points)]
        area += point.x * nxt.y - nxt.x * point.y
    return area * 0.5


def _fake_terrain_bbox_from_points_xy(points):
    if not points:
        return None
    return (
        min(point.x for point in points),
        max(point.x for point in points),
        min(point.y for point in points),
        max(point.y for point in points),
    )


def _fake_terrain_bbox_from_xy(points_xy):
    if not points_xy:
        return None
    return (
        min(point[0] for point in points_xy),
        max(point[0] for point in points_xy),
        min(point[1] for point in points_xy),
        max(point[1] for point in points_xy),
    )


def _fake_terrain_bbox_overlaps_rect_xy(bbox, x0, x1, y0, y1, eps=1e-8) -> bool:
    if bbox is None:
        return False
    return (
        min(float(bbox[1]), float(x1)) - max(float(bbox[0]), float(x0)) > eps
        and min(float(bbox[3]), float(y1)) - max(float(bbox[2]), float(y0)) > eps
    )


def _fake_terrain_bbox_overlaps_bbox_xy(a, b, eps=1e-8) -> bool:
    if a is None or b is None:
        return False
    return (
        min(float(a[1]), float(b[1])) - max(float(a[0]), float(b[0])) > eps
        and min(float(a[3]), float(b[3])) - max(float(a[2]), float(b[2])) > eps
    )


def _fake_terrain_polygon_area_2d(points_xy) -> float:
    if len(points_xy) < 3:
        return 0.0
    area = 0.0
    for idx, point in enumerate(points_xy):
        nxt = points_xy[(idx + 1) % len(points_xy)]
        area += point[0] * nxt[1] - nxt[0] * point[1]
    return area * 0.5


def _fake_terrain_clean_xy_polygon(points_xy, eps=1e-8):
    cleaned = []
    for point in points_xy:
        item = (float(point[0]), float(point[1]))
        if cleaned and abs(item[0] - cleaned[-1][0]) <= eps and abs(item[1] - cleaned[-1][1]) <= eps:
            continue
        cleaned.append(item)
    if len(cleaned) > 1 and abs(cleaned[0][0] - cleaned[-1][0]) <= eps and abs(cleaned[0][1] - cleaned[-1][1]) <= eps:
        cleaned.pop()
    return cleaned



def _fake_terrain_line_intersection_2d(a, b, c, d, eps=1e-12):
    ax, ay = a
    bx, by = b
    cx, cy = c
    dx, dy = d
    sx = bx - ax
    sy = by - ay
    rx = dx - cx
    ry = dy - cy
    denom = sx * ry - sy * rx
    if abs(denom) <= eps:
        return b
    factor = ((cx - ax) * ry - (cy - ay) * rx) / denom
    factor = max(0.0, min(1.0, factor))
    return (ax + sx * factor, ay + sy * factor)


def _fake_terrain_clip_polygon_by_edge_2d(subject, edge_a, edge_b, eps=1e-8):
    if not subject:
        return []

    ax, ay = edge_a
    bx, by = edge_b

    def inside(point):
        return (bx - ax) * (point[1] - ay) - (by - ay) * (point[0] - ax) >= -eps

    clipped = []
    previous = subject[-1]
    previous_inside = inside(previous)
    for current in subject:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                clipped.append(_fake_terrain_line_intersection_2d(previous, current, edge_a, edge_b))
            clipped.append(current)
        elif previous_inside:
            clipped.append(_fake_terrain_line_intersection_2d(previous, current, edge_a, edge_b))
        previous = current
        previous_inside = current_inside

    return _fake_terrain_clean_xy_polygon(clipped, eps=eps)


def _fake_terrain_polygon_overlap_area_xy(subject_xy, clip_xy):
    subject = _fake_terrain_clean_xy_polygon(subject_xy)
    clip = _fake_terrain_clean_xy_polygon(clip_xy)
    if len(subject) < 3 or len(clip) < 3:
        return 0.0
    if _fake_terrain_polygon_area_2d(subject) < 0.0:
        subject = list(reversed(subject))
    if _fake_terrain_polygon_area_2d(clip) < 0.0:
        clip = list(reversed(clip))

    clipped = list(subject)
    for idx, edge_a in enumerate(clip):
        edge_b = clip[(idx + 1) % len(clip)]
        clipped = _fake_terrain_clip_polygon_by_edge_2d(clipped, edge_a, edge_b)
        if len(clipped) < 3:
            return 0.0
    return abs(_fake_terrain_polygon_area_2d(clipped))


def _fake_terrain_clean_polygon_points(points, eps=1e-8):
    cleaned = []
    eps_sq = eps * eps
    for point in points:
        if cleaned and (point - cleaned[-1]).length_squared <= eps_sq:
            continue
        cleaned.append(point)
    if len(cleaned) > 1 and (cleaned[0] - cleaned[-1]).length_squared <= eps_sq:
        cleaned.pop()
    return cleaned


def _fake_terrain_clip_polygon_axis_xy(points, axis, limit, keep_greater, eps=1e-8):
    if not points:
        return []

    limit = float(limit)

    def coord(point):
        return point.x if axis == 0 else point.y

    def inside(point):
        value = coord(point)
        return value >= limit - eps if keep_greater else value <= limit + eps

    def intersect(a, b):
        delta = coord(b) - coord(a)
        if abs(delta) <= eps:
            point = b.copy()
        else:
            factor = max(0.0, min(1.0, (limit - coord(a)) / delta))
            point = a.lerp(b, factor)
        if axis == 0:
            point.x = limit
        else:
            point.y = limit
        return point

    clipped = []
    previous = points[-1]
    previous_inside = inside(previous)
    for current in points:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                clipped.append(intersect(previous, current))
            clipped.append(current.copy())
        elif previous_inside:
            clipped.append(intersect(previous, current))
        previous = current
        previous_inside = current_inside

    return _fake_terrain_clean_polygon_points(clipped, eps=eps)


def _fake_terrain_clip_polygon_to_rect_xy(points, x0, x1, y0, y1):
    clipped = [point.copy() for point in points]
    clipped = _fake_terrain_clip_polygon_axis_xy(clipped, 0, x0, True)
    clipped = _fake_terrain_clip_polygon_axis_xy(clipped, 0, x1, False)
    clipped = _fake_terrain_clip_polygon_axis_xy(clipped, 1, y0, True)
    clipped = _fake_terrain_clip_polygon_axis_xy(clipped, 1, y1, False)
    if len(clipped) < 3 or abs(_fake_terrain_polygon_area_from_vectors_xy(clipped)) <= 1e-8:
        return []
    return clipped


def _fake_terrain_clipped_cell_points(cell_tris, x0, x1, y0, y1):
    points = []
    for tri in cell_tris:
        clipped = _fake_terrain_clip_polygon_to_rect_xy(tri["points"], x0, x1, y0, y1)
        if clipped:
            points.extend(clipped)
    return points



def _fake_terrain_hull_overlaps_occupied_bboxes(hull_xy, occupied_bboxes) -> bool:
    hull_bbox = _fake_terrain_bbox_from_xy(hull_xy)
    if hull_bbox is None:
        return False
    for item in occupied_bboxes:
        if isinstance(item, dict):
            bbox = item.get("bbox")
            poly = item.get("poly")
        else:
            bbox = item
            poly = None
        if _fake_terrain_bbox_overlaps_bbox_xy(hull_bbox, bbox):
            if poly is None:
                return True
            if _fake_terrain_polygon_overlap_area_xy(hull_xy, poly) > 1e-7:
                return True
    return False


def _append_fake_terrain_component(world_vertices, faces, hull_xy, plane, thickness, z_by_xy=None):
    if len(hull_xy) < 3:
        return False

    area = _fake_terrain_polygon_area_xy(hull_xy)
    if abs(area) <= 1e-8:
        return False
    if area < 0.0:
        hull_xy = list(reversed(hull_xy))

    top_indices = []
    bottom_indices = []
    for x, y in hull_xy:
        z = None
        if z_by_xy:
            z = z_by_xy.get((round(float(x), 5), round(float(y), 5)))
        if z is None:
            z = _fake_terrain_plane_z(plane, x, y)
        top_indices.append(len(world_vertices))
        world_vertices.append(Vector((x, y, z)))
    for x, y in hull_xy:
        z = None
        if z_by_xy:
            z = z_by_xy.get((round(float(x), 5), round(float(y), 5)))
        if z is None:
            z = _fake_terrain_plane_z(plane, x, y)
        z -= thickness
        bottom_indices.append(len(world_vertices))
        world_vertices.append(Vector((x, y, z)))

    center_x = sum(x for x, _y in hull_xy) / len(hull_xy)
    center_y = sum(y for _x, y in hull_xy) / len(hull_xy)
    center_z = _fake_terrain_plane_z(plane, center_x, center_y)
    top_center_idx = len(world_vertices)
    world_vertices.append(Vector((center_x, center_y, center_z)))
    bottom_center_idx = len(world_vertices)
    world_vertices.append(Vector((center_x, center_y, center_z - thickness)))

    count = len(hull_xy)
    for idx in range(count):
        nxt = (idx + 1) % count
        faces.append((top_center_idx, top_indices[idx], top_indices[nxt]))
        faces.append((bottom_center_idx, bottom_indices[nxt], bottom_indices[idx]))
        faces.append((top_indices[idx], bottom_indices[idx], bottom_indices[nxt], top_indices[nxt]))

    return True











def _build_fake_terrain_mesh_from_triangles(
    triangles,
    *,
    patch_size,
    min_patch_size,
    depression_error,
    hill_error,
    thickness,
):
    if not triangles:
        raise RuntimeError("No matching source faces found for fake terrain")

    patch_size = max(float(patch_size), 0.25)
    min_patch_size = max(0.05, min(float(min_patch_size), patch_size))
    depression_error = max(0.0, float(depression_error))
    hill_error = max(0.0, float(hill_error))
    thickness = max(0.05, float(thickness))

    all_points = [point for tri in triangles for point in tri["points"]]
    min_x = min(point.x for point in all_points)
    min_y = min(point.y for point in all_points)

    tiles = {}
    for tri in triangles:
        bbox = _fake_terrain_bbox_from_points_xy(tri["points"])
        if bbox is None:
            continue
        tri["bbox_xy"] = bbox
        ix0 = int(math.floor((bbox[0] - min_x) / patch_size))
        ix1 = int(math.floor((bbox[1] - min_x) / patch_size))
        iy0 = int(math.floor((bbox[2] - min_y) / patch_size))
        iy1 = int(math.floor((bbox[3] - min_y) / patch_size))
        for ix in range(ix0, ix1 + 1):
            x0 = min_x + ix * patch_size
            x1 = x0 + patch_size
            for iy in range(iy0, iy1 + 1):
                y0 = min_y + iy * patch_size
                y1 = y0 + patch_size
                if _fake_terrain_bbox_overlaps_rect_xy(bbox, x0, x1, y0, y1):
                    tiles.setdefault((ix, iy), []).append(tri)

    world_vertices = []
    faces = []
    occupied_bboxes = []
    stats = {
        "components": 0,
        "source_tris": len(triangles),
        "split_cells": 0,
        "max_depth": 0,
        "skipped_existing": 0,
        "build_mode": "GRID_PATCHES",
    }

    def build_cell(cell_tris, x0, x1, y0, y1, depth):
        if not cell_tris:
            return

        points = _fake_terrain_clipped_cell_points(cell_tris, x0, x1, y0, y1)
        unique_xy = _fake_terrain_unique_xy(points)
        if len(unique_xy) < 3:
            return
        z_values_by_xy = {}
        for point in points:
            key = (round(float(point.x), 5), round(float(point.y), 5))
            z_values_by_xy.setdefault(key, []).append(float(point.z))
        z_by_xy = {
            key: sum(values) / len(values)
            for key, values in z_values_by_xy.items()
            if values
        }

        plane = _fake_terrain_fit_plane(points)
        max_depression = 0.0
        max_hill = 0.0
        for point in points:
            patch_z = _fake_terrain_plane_z(plane, point.x, point.y)
            max_depression = max(max_depression, patch_z - point.z)
            max_hill = max(max_hill, point.z - patch_z)

        size_x = max(0.0, x1 - x0)
        size_y = max(0.0, y1 - y0)
        size = max(size_x, size_y)
        should_split = (
            len(cell_tris) > 1
            and size > min_patch_size * 1.01
            and (max_depression > depression_error or max_hill > hill_error)
            and depth < 16
        )

        if should_split:
            mx = (x0 + x1) * 0.5
            my = (y0 + y1) * 0.5
            children = [[], [], [], []]
            child_bounds = (
                (x0, mx, y0, my),
                (mx, x1, y0, my),
                (x0, mx, my, y1),
                (mx, x1, my, y1),
            )
            for tri in cell_tris:
                bbox = tri.get("bbox_xy") or _fake_terrain_bbox_from_points_xy(tri["points"])
                for child_idx, bounds in enumerate(child_bounds):
                    if _fake_terrain_bbox_overlaps_rect_xy(bbox, bounds[0], bounds[1], bounds[2], bounds[3]):
                        children[child_idx].append(tri)

            stats["split_cells"] += 1
            for child_tris, bounds in zip(children, child_bounds):
                if child_tris:
                    build_cell(child_tris, bounds[0], bounds[1], bounds[2], bounds[3], depth + 1)
            return

        hull_xy = _fake_terrain_convex_hull_xy(unique_xy)
        if _fake_terrain_hull_overlaps_occupied_bboxes(hull_xy, occupied_bboxes):
            stats["skipped_existing"] += 1
            return
        if _append_fake_terrain_component(world_vertices, faces, hull_xy, plane, thickness, z_by_xy=z_by_xy):
            stats["components"] += 1
            stats["max_depth"] = max(stats["max_depth"], depth)
            bbox = _fake_terrain_bbox_from_xy(hull_xy)
            if bbox is not None:
                occupied_bboxes.append({"bbox": bbox, "poly": list(hull_xy)})

    for (ix, iy), tile_tris in tiles.items():
        x0 = min_x + ix * patch_size
        y0 = min_y + iy * patch_size
        build_cell(tile_tris, x0, x0 + patch_size, y0, y0 + patch_size, 0)

    if not world_vertices or not faces or stats["components"] <= 0:
        if stats["skipped_existing"] > 0:
            stats["verts"] = 0
            stats["faces"] = 0
            return [], [], stats
        raise RuntimeError("Could not build fake terrain components from the matched faces")

    # Keep slab vertices separate across patch boundaries. Sharing identical
    # coordinates here would weld adjacent fake terrain slabs into one connected
    # component in Blender.
    if not world_vertices or not faces:
        raise RuntimeError("Could not build valid fake terrain faces")

    stats["verts"] = len(world_vertices)
    stats["faces"] = len(faces)
    return world_vertices, faces, stats


class CRAY_OT_GenerateFakeTerrainGeometry(Operator):
    """Generate closed fake terrain components from selected visual terrain faces"""

    bl_idname = "cray.generate_fake_terrain_geometry"
    bl_label = "Generate Fake Terrain Geometry"
    bl_description = "Build adaptive closed fake terrain slabs from selected Source Visual faces in Edit Mode"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider_exp import (_append_collider_exp_mesh_to_object_exp, _restore_collider_exp_source_context_exp, _set_collider_exp_custom_props_exp)
        from .nh_scatter import (_COLLIDER_LOD_NAMES, _FIRE_GEOMETRY_LOD_TOKEN, _collider_lod_token_from_object, _set_fake_terrain_target_object)
        from .nh_snap import (_collider_target_validation_error)
        from .nh_textures import (_ensure_collider_placeholder_material)
        cs = context.scene.cray_collider_settings
        source_obj = _resolve_fake_terrain_source_object(context, cs)
        if source_obj is None or source_obj.type != "MESH":
            self.report({"ERROR"}, "Set Source Visual to the terrain mesh")
            return {"CANCELLED"}

        preferred_target = _resolve_fake_terrain_preferred_target(context, cs)
        if preferred_target is not None:
            lod_token = _collider_lod_token_from_object(preferred_target, allow_name_fallback=True)
        else:
            lod_token = str(getattr(cs, "fake_terrain_target_lod", "") or _FIRE_GEOMETRY_LOD_TOKEN)
        if lod_token not in _COLLIDER_LOD_NAMES:
            self.report({"ERROR"}, "Target LOD must be Geometry, View Geometry, or Fire Geometry")
            return {"CANCELLED"}

        preferred_any_lod = (
            getattr(preferred_target, "type", None) == "MESH"
            and _collider_lod_token_from_object(preferred_target, allow_name_fallback=True) in _COLLIDER_LOD_NAMES
        )

        err = _collider_target_validation_error(
            preferred_target,
            lod_token,
            source_obj=source_obj,
            allow_any_collider_lod=preferred_any_lod,
        )
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        source_was_edit = False
        try:
            source_was_edit = getattr(source_obj, "mode", "") == "EDIT"
            source_tris, matched_faces, source_objects = _collect_fake_terrain_edit_selection_triangles(
                context,
                source_obj,
                target_obj=preferred_target,
            )
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            target_obj = _ensure_collider_lod_object(
                context,
                source_obj,
                lod_token,
                preferred_obj=preferred_target,
                exclude_obj=source_obj,
                allow_any_preferred_lod=preferred_any_lod,
                preserve_existing_lod=preferred_any_lod,
            )
            if target_obj == source_obj:
                raise RuntimeError("Target LOD Object must be separate from the Source Visual")
            actual_target_lod = _collider_lod_token_from_object(target_obj, allow_name_fallback=True) or lod_token

            _set_fake_terrain_target_object(context, cs, target_obj, sync_choice=True)
            _set_collider_settings_object(context, "geometry_object", target_obj)
            if actual_target_lod == _FIRE_GEOMETRY_LOD_TOKEN:
                _set_collider_settings_object(context, "fire_geometry_object", target_obj)
                _sync_fire_geometry_material_selection(context)

            exp_settings = getattr(context.scene, "cray_collider_exp_settings", None)
            if exp_settings is not None:
                try:
                    exp_settings.geometry_object = target_obj
                    exp_settings.target_lod = actual_target_lod
                except Exception:
                    pass

            material_index, material_name = _ensure_collider_placeholder_material(
                target_obj,
                source_obj,
                source_objects=source_objects,
                data_items=source_tris,
            )
            world_vertices, faces, build_stats = _build_fake_terrain_mesh_from_triangles(
                source_tris,
                patch_size=cs.fake_terrain_patch_size,
                min_patch_size=cs.fake_terrain_min_patch_size,
                depression_error=cs.fake_terrain_depression_error,
                hill_error=cs.fake_terrain_hill_error,
                thickness=cs.fake_terrain_thickness,
            )
            if not world_vertices or not faces:
                self.report(
                    {"INFO"},
                    (
                        f"No new fake terrain added to {target_obj.name}: "
                        f"{build_stats.get('skipped_existing', 0)} patch(es) already overlap target geometry"
                    ),
                )
                _restore_collider_exp_source_context_exp(context, source_obj, restore_edit_mode=source_was_edit)
                return {"FINISHED"}
            append_stats = _append_collider_exp_mesh_to_object_exp(
                target_obj,
                world_vertices,
                faces,
                merge_distance=0.0,
                recalc_normals=True,
                material_index=material_index,
            )
            source_obj_for_props = source_objects[0] if source_objects else source_obj
            _set_collider_exp_custom_props_exp(
                target_obj,
                "FAKE_TERRAIN",
                source_obj_for_props,
                {
                    "vertex_indices": append_stats.get("vertex_indices", []),
                    "face_indices": append_stats.get("face_indices", []),
                    "source_object": source_obj_for_props.name,
                    "source_objects": [obj.name for obj in source_objects],
                    "source_mode": "SELECTED_FACES",
                    "build_mode": build_stats.get("build_mode", "GRID_PATCHES"),
                    "selected_faces": matched_faces,
                    "target_lod": actual_target_lod,
                    "material_name": material_name,
                    "components": build_stats.get("components", 0),
                    "skipped_existing": build_stats.get("skipped_existing", 0),
                    "matched_faces": matched_faces,
                    "source_tris": build_stats.get("source_tris", 0),
                    "patch_size": float(cs.fake_terrain_patch_size),
                    "min_patch_size": float(cs.fake_terrain_min_patch_size),
                    "depression_error": float(cs.fake_terrain_depression_error),
                    "hill_error": float(cs.fake_terrain_hill_error),
                    "thickness": float(cs.fake_terrain_thickness),
                },
            )

            _restore_collider_exp_source_context_exp(context, source_obj, restore_edit_mode=source_was_edit)
        except Exception as e:
            try:
                _restore_collider_exp_source_context_exp(context, source_obj, restore_edit_mode=source_was_edit)
            except Exception:
                pass
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Fake terrain added to {target_obj.name}: "
                f"{build_stats.get('components', 0)} components, "
                f"+{append_stats.get('verts_added', 0)} verts, +{append_stats.get('faces_added', 0)} faces, "
                f"from {matched_faces} selected face(s), "
                f"skipped {build_stats.get('skipped_existing', 0)} occupied patch(es)"
            ),
        )
        return {"FINISHED"}


class CRAY_OT_CopySelectedVertsToGeometry(Operator):
    """Copy selected source vertices into the active Geometry LOD as loose points"""

    bl_idname = "cray.copy_selected_verts_to_geometry"
    bl_label = "Copy Selected Verts To Geometry"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_snap import (_collider_target_validation_error, _deselect_all_in_view_layer, _is_collider_lod_mesh_object, _select_object_in_view_layer)
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

        source_was_edit = context.mode == "EDIT"
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
            try:
                if source_was_edit and context.mode == "OBJECT":
                    _deselect_all_in_view_layer(context)
                    _select_object_in_view_layer(context, source_obj, active=True)
                    bpy.ops.object.mode_set(mode="EDIT")
            except Exception:
                pass
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
        from .nh_base import (_fmt_exc)
        from .nh_snap import (_collider_target_validation_error)
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
        from .nh_base import (_PLAIN_AXIS_HOTKEY_REGISTERED, _find_nh_keymap_item, _keymap_item_shortcut_label)
        from .nh_scatter import (_CUSTOM_KEYBIND_DEFINITIONS)
        del context, properties
        lines = []
        for operator_idname, action, default_shortcut, status_key in _CUSTOM_KEYBIND_DEFINITIONS:
            shortcut = _keymap_item_shortcut_label(
                _find_nh_keymap_item(operator_idname),
                default_shortcut,
            )
            if status_key == "plain_axis" and not _PLAIN_AXIS_HOTKEY_REGISTERED:
                shortcut = f"{shortcut} (busy)"
            lines.append(f"{shortcut}: {action}")
        return "\n".join(lines)

    def execute(self, context):
        del context
        return {"FINISHED"}


class CRAY_OT_OpenNHKeymapPreferences(Operator):
    """Open Blender preferences where NH Plugin keymaps can be edited"""

    bl_idname = "cray.open_nh_keymap_preferences"
    bl_label = "Open Blender Keymap"
    bl_description = "Open Edit > Preferences > Keymap; search for cray or NH actions to change these shortcuts"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        try:
            context.preferences.active_section = "KEYMAP"
        except Exception:
            pass
        try:
            bpy.ops.screen.userpref_show("INVOKE_DEFAULT")
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class CRAY_OT_RestoreNHDefaultKeymaps(Operator):
    """Restore NH Plugin collider keymaps to their bundled defaults"""

    bl_idname = "cray.restore_nh_default_keymaps"
    bl_label = "Restore NH Defaults"
    bl_description = "Restore NH Plugin default collider shortcuts in the Blender add-on keymap"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .nh_base import (_fmt_exc, _register_collider_keymaps, _remove_nh_keymap_user_overrides)
        del context
        try:
            removed = _remove_nh_keymap_user_overrides()
            _register_collider_keymaps()
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}
        suffix = f" ({removed} user override(s) cleared)" if removed else ""
        self.report({"INFO"}, f"NH default keymaps restored{suffix}")
        return {"FINISHED"}


class CRAY_OT_SetColliderTargetFromActive(Operator):
    """Use the active selected mesh as the current target object"""

    bl_idname = "cray.set_collider_target_from_active"
    bl_label = "Use Active Mesh"
    bl_options = {"REGISTER", "UNDO"}

    target_attr: EnumProperty(
        name="Target",
        items=(
            ("GEOMETRY", "Target LOD Object", "Set the main Geometry Collider target"),
            ("FIRE", "Fire Geometry", "Set the Fire Geometry target"),
            ("ROADWAY", "Roadway", "Set the Roadway target"),
        ),
        default="GEOMETRY",
        options={"HIDDEN"},
    )

    @classmethod
    def description(cls, context, properties):
        del context
        label = {
            "GEOMETRY": "Target LOD Object",
            "FIRE": "Fire Geometry",
            "ROADWAY": "Roadway",
        }.get(getattr(properties, "target_attr", "GEOMETRY"), "target")
        return f"Use the active selected mesh as {label}"

    def execute(self, context):
        from .nh_scatter import (_COLLIDER_LOD_NAMES, _FIRE_GEOMETRY_LOD_TOKEN, _active_or_selected_mesh_object, _collider_lod_token_from_object, _poll_fire_geometry_object, _poll_roadway_object)
        cs = context.scene.cray_collider_settings
        obj = _active_or_selected_mesh_object(context)
        if obj is None:
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        target_attr = str(getattr(self, "target_attr", "GEOMETRY") or "GEOMETRY").upper()
        if target_attr == "FIRE":
            if not _poll_fire_geometry_object(None, obj):
                self.report({"ERROR"}, "Active mesh name must start with 'Fire Geometry'")
                return {"CANCELLED"}
            _set_collider_settings_object(context, "fire_geometry_object", obj)
            _set_collider_settings_object(context, "geometry_object", obj)
            try:
                cs.target_lod = _FIRE_GEOMETRY_LOD_TOKEN
            except Exception:
                pass
            _sync_fire_geometry_material_selection(context)
        elif target_attr == "ROADWAY":
            if not _poll_roadway_object(None, obj):
                self.report({"ERROR"}, "Active mesh name must start with 'Roadway'")
                return {"CANCELLED"}
            _set_collider_settings_object(context, "roadway_object", obj)
            _sync_roadway_material_selection(context)
        else:
            _set_collider_settings_object(context, "geometry_object", obj)
            lod_token = _collider_lod_token_from_object(obj, allow_name_fallback=True)
            if lod_token in _COLLIDER_LOD_NAMES:
                try:
                    cs.target_lod = lod_token
                except Exception:
                    pass
            if lod_token == _FIRE_GEOMETRY_LOD_TOKEN:
                _set_collider_settings_object(context, "fire_geometry_object", obj)
                _sync_fire_geometry_material_selection(context)

        self.report({"INFO"}, f"Target set to {obj.name}")
        return {"FINISHED"}


class CRAY_OT_SetFakeTerrainTargetFromActive(Operator):
    """Use the active selected Geometry/View/Fire mesh as the Fake Terrain target"""

    bl_idname = "cray.set_fake_terrain_target_from_active"
    bl_label = "Use Active Fake Terrain Target"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_scatter import (_COLLIDER_LOD_NAMES, _collider_lod_token_from_object, _fake_terrain_context_root_collection, _set_fake_terrain_target_object)
        from .nh_snap import (_collider_lod_name)
        from .nh_textures import (_object_is_directly_or_indirectly_in_collection)
        cs = context.scene.cray_collider_settings
        objects = []

        active = getattr(getattr(context, "view_layer", None), "objects", None)
        active_obj = getattr(active, "active", None) if active is not None else None
        if active_obj is not None:
            objects.append(active_obj)
        for obj in getattr(context, "selected_objects", []) or []:
            if obj is not None and obj not in objects:
                objects.append(obj)

        target_obj = None
        for obj in objects:
            if obj is None or getattr(obj, "type", None) != "MESH":
                continue
            if _collider_lod_token_from_object(obj, allow_name_fallback=True) in _COLLIDER_LOD_NAMES:
                target_obj = obj
                break

        if target_obj is None:
            self.report({"ERROR"}, "Select a Geometry, View Geometry, or Fire Geometry mesh first")
            return {"CANCELLED"}

        root = _fake_terrain_context_root_collection(context, cs)
        if root is not None:
            try:
                if not _object_is_directly_or_indirectly_in_collection(root, target_obj):
                    self.report({"ERROR"}, f"Target must be inside current model collection '{root.name}'")
                    return {"CANCELLED"}
            except Exception:
                pass

        if not _set_fake_terrain_target_object(context, cs, target_obj, sync_choice=True):
            self.report({"ERROR"}, "Active mesh must be Geometry, View Geometry, or Fire Geometry")
            return {"CANCELLED"}

        lod_name = _collider_lod_name(_collider_lod_token_from_object(target_obj, allow_name_fallback=True))
        self.report({"INFO"}, f"Fake Terrain target set to {lod_name}: {target_obj.name}")
        return {"FINISHED"}


class CRAY_OT_EnsureRoadwayLOD(Operator):
    """Create or find the Roadway LOD mesh inside Misc collection"""

    bl_idname = "cray.ensure_roadway_lod"
    bl_label = "Create/Find Misc Roadway"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_scatter import (_ROADWAY_LOD_TOKEN)
        from .nh_snap import (_collider_target_validation_error)
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
        from .nh_base import (_fmt_exc)
        from .nh_scatter import (_ROADWAY_LOD_TOKEN)
        from .nh_snap import (_collider_target_validation_error)
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
        from .nh_base import (_fmt_exc)
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
    """Pick a roadway texture file inside Blender and assign it to the selected Roadway material"""

    bl_idname = "cray.open_roadway_material_folder"
    bl_label = "Choose Roadway Texture Path"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(
        name="Texture File",
        description="Choose a texture file for the selected Roadway material",
        subtype="FILE_PATH",
    )
    filter_glob: StringProperty(default="*.paa;*.dds", options={"HIDDEN"})

    def invoke(self, context, event):
        from .nh_scatter import (_ROADWAY_SURFACES_FOLDER)
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
        from .nh_base import (_fmt_exc)
        from .nh_collider_exp import (_basename_no_ext, _norm_path)
        from .nh_textures import (_set_p3d_material_paths)
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
            self.report({"ERROR"}, "Choose an existing .paa or .dds texture file")
            return {"CANCELLED"}

        ext = os.path.splitext(filepath)[1].lower()
        try:
            if ext in {".paa", ".dds"}:
                _set_p3d_material_paths(mat, _norm_path(filepath), None, clear_rvmat=True)
            else:
                self.report({"ERROR"}, "Unsupported file type. Choose .paa or .dds")
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
        self.report({"INFO"}, f"Assigned texture to Roadway material: {mat.name}")
        return {"FINISHED"}


class CRAY_OT_OpenFireGeometryRvmatFolder(Operator):
    """Pick a penetration .rvmat and assign it to the selected Fire Geometry material"""

    bl_idname = "cray.open_fire_geometry_rvmat_folder"
    bl_label = "Choose Fire Geometry RVMAT Path"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(
        name="RVMAT File",
        description="Choose an .rvmat file for the selected Fire Geometry material",
        subtype="FILE_PATH",
    )
    filter_glob: StringProperty(default="*.rvmat", options={"HIDDEN"})

    def invoke(self, context, event):
        from .nh_scatter import (_FIRE_GEOMETRY_RVMAT_FOLDER, _resolve_fire_geometry_object_for_material)
        del event

        fire_obj = _resolve_fire_geometry_object_for_material(context)
        if fire_obj is None or getattr(fire_obj, "type", None) != "MESH":
            self.report({"ERROR"}, "Create or assign a Fire Geometry object first")
            return {"CANCELLED"}

        default_dir = _FIRE_GEOMETRY_RVMAT_FOLDER if os.path.isdir(_FIRE_GEOMETRY_RVMAT_FOLDER) else ""
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
        from .nh_base import (_fmt_exc)
        from .nh_collider_exp import (_basename_no_ext, _norm_path)
        from .nh_scatter import (_resolve_fire_geometry_object_for_material)
        from .nh_textures import (_set_p3d_material_paths)
        fire_obj = _resolve_fire_geometry_object_for_material(context)
        if fire_obj is None or getattr(fire_obj, "type", None) != "MESH":
            self.report({"ERROR"}, "Fire Geometry Object must be a mesh")
            return {"CANCELLED"}

        try:
            filepath = os.path.abspath(bpy.path.abspath(self.filepath or ""))
        except Exception as e:
            self.report({"ERROR"}, f"Could not resolve RVMAT path: {_fmt_exc(e)}")
            return {"CANCELLED"}

        if not filepath or not os.path.isfile(filepath):
            self.report({"ERROR"}, "Choose an existing .rvmat file")
            return {"CANCELLED"}
        if os.path.splitext(filepath)[1].lower() != ".rvmat":
            self.report({"ERROR"}, "Unsupported file type. Choose .rvmat")
            return {"CANCELLED"}

        material_name = _basename_no_ext(filepath) or "FireGeometryMaterial"
        mat = _get_selected_fire_geometry_material(context, create_name=material_name)
        if mat is None:
            self.report({"ERROR"}, "Could not create or select a Fire Geometry material")
            return {"CANCELLED"}

        try:
            _set_p3d_material_paths(mat, None, _norm_path(filepath), clear_paa=True)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        try:
            mat.name = material_name
        except Exception:
            pass

        _sync_fire_geometry_material_selection(context, mat.name)
        self.report({"INFO"}, f"Assigned Fire Geometry .rvmat: {mat.name}")
        return {"FINISHED"}


class CRAY_OT_SelectColliderMaterialFaces(Operator):
    """Select faces that use the currently chosen collider material"""

    bl_idname = "cray.select_collider_material_faces"
    bl_label = "Select Material Faces"
    bl_options = {"REGISTER", "UNDO"}

    target_attr: EnumProperty(
        name="Target",
        items=(
            ("FIRE", "Fire Geometry", "Select Fire Geometry faces with the chosen material"),
            ("ROADWAY", "Roadway", "Select Roadway faces with the chosen material"),
        ),
        default="FIRE",
    )

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_scatter import (_collider_material_selection_objects, _resolve_fire_geometry_object_for_material)
        target_attr = str(getattr(self, "target_attr", "FIRE") or "FIRE")
        cs = getattr(getattr(context, "scene", None), "cray_collider_settings", None)
        if target_attr == "ROADWAY":
            target_obj = getattr(cs, "roadway_object", None) if cs is not None else None
            material = _get_selected_roadway_material(context)
            object_attr = "roadway_object"
            label = "Roadway"
        else:
            target_obj = _resolve_fire_geometry_object_for_material(context)
            material = _get_selected_fire_geometry_material(context)
            object_attr = "fire_geometry_object"
            label = "Fire Geometry"

        if target_obj is None or getattr(target_obj, "type", None) != "MESH":
            self.report({"ERROR"}, f"{label} object must be a mesh")
            return {"CANCELLED"}
        if material is None:
            self.report({"ERROR"}, f"Select or create a {label} material first")
            return {"CANCELLED"}

        objects = _collider_material_selection_objects(context, object_attr, target_obj)

        try:
            stats = _select_material_faces_in_objects(context, objects, material)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        if int(stats.get("faces", 0)) <= 0:
            self.report({"WARNING"}, f"No faces use material '{material.name}'")
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Selected {stats['faces']} face(s) using '{material.name}' on {stats['objects']} object(s)",
        )
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


class CRAY_OT_SelectLooseVerticesOutsideMemory(Operator):
    """Find non-Memory LOD loose vertices and select them on the first matching mesh"""

    bl_idname = "cray.select_loose_vertices_outside_memory"
    bl_label = "Select Loose Vertices Outside Memory"
    bl_description = (
        "Find isolated vertices in exportable LODs outside Point clouds > Memory, "
        "then select the first matching mesh in Edit Mode"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_snap import (_collect_loose_vertices_outside_memory_records, _tag_redraw_all_areas)
        from .nh_textures import (_ensure_collection_visible_in_view_layer)
        try:
            records = _collect_loose_vertices_outside_memory_records(context)
        except Exception as e:
            self.report({"ERROR"}, f"Loose vertex scan failed: {_fmt_exc(e)}")
            return {"CANCELLED"}

        if not records:
            self.report({"INFO"}, "No loose vertices outside Memory found")
            return {"FINISHED"}

        total_vertices = sum(int(rec.get("isolated_count", 0) or 0) for rec in records)
        print("=== Select Loose Vertices Outside Memory ===")
        print(f"Found {total_vertices} loose vertex/vertices in {len(records)} mesh object(s).")
        for rec in records:
            root_collection = rec.get("root_collection")
            root_name = getattr(root_collection, "name", "<unknown>")
            actual_branch = " > ".join(rec.get("actual_branch", ()) or ())
            indices = list(rec.get("isolated_indices", []) or [])
            index_preview = ", ".join(str(idx) for idx in indices[:20])
            if len(indices) > 20:
                index_preview += ", ..."
            print(
                f" - {root_name} | LOD: {rec.get('lod_name', '')} | "
                f"mesh: {rec.get('mesh_object_name', '')} | loose vertices: {len(indices)} | "
                f"branch: {actual_branch} | indices: {index_preview}"
            )

        target = records[0]
        target_obj = target.get("mesh_object")
        target_root = target.get("root_collection")
        target_indices = list(target.get("isolated_indices", []) or [])
        if target_obj is None or target_obj.type != "MESH" or target_obj.data is None or not target_indices:
            self.report({"ERROR"}, "Loose vertex target is no longer available")
            return {"CANCELLED"}

        try:
            if target_root is not None:
                _ensure_collection_visible_in_view_layer(context, target_root)
            _activate_object_vertex_edit(context, target_obj)
            try:
                bpy.ops.mesh.reveal(select=False)
            except Exception:
                pass

            context.tool_settings.mesh_select_mode = (True, False, False)
            bm = bmesh.from_edit_mesh(target_obj.data)
            bm.verts.ensure_lookup_table()
            target_index_set = set(target_indices)
            selected_count = 0

            for face in bm.faces:
                face.select = False
            for edge in bm.edges:
                edge.select = False
            for vert in bm.verts:
                is_target = vert.index in target_index_set
                vert.select = is_target
                if is_target:
                    selected_count += 1

            bm.select_flush_mode()
            bmesh.update_edit_mesh(target_obj.data, loop_triangles=False, destructive=False)
            _tag_redraw_all_areas(context)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to select loose vertices: {_fmt_exc(e)}")
            return {"CANCELLED"}

        msg = f"Selected {selected_count} loose vertex/vertices on {target_obj.name}"
        if len(records) > 1:
            msg += f"; {len(records) - 1} more mesh object(s) listed in System Console"
            self.report({"WARNING"}, msg)
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class CRAY_OT_ReportNgonMeshes(Operator):
    """Print all scene mesh objects that contain n-gons"""

    bl_idname = "cray.report_ngon_meshes"
    bl_label = "Report N-gon Meshes"
    bl_description = "Print scene mesh objects that contain faces with more than 4 vertices"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_snap import (_collect_scene_ngon_mesh_records, _report_scene_ngon_mesh_records_in_console)
        try:
            records = _collect_scene_ngon_mesh_records(context)
        except Exception as e:
            self.report({"ERROR"}, f"N-gon scan failed: {_fmt_exc(e)}")
            return {"CANCELLED"}

        _report_scene_ngon_mesh_records_in_console(context, records)

        if not records:
            self.report({"INFO"}, "No n-gons found in scene mesh objects")
            return {"FINISHED"}

        total_ngons = sum(int(rec.get("ngon_count", 0) or 0) for rec in records)
        first_path = records[0].get("display_path") or records[0].get("object_name", "<unknown>")
        self.report(
            {"WARNING"},
            f"Found {total_ngons} n-gon face(s) in {len(records)} mesh object(s); first: {first_path} has n-gons (see System Console)",
        )
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


def _collect_candidate_face_islands(bm):
    candidate_faces, scope_label = _iter_split_planar_candidate_faces(bm)
    if not candidate_faces:
        return [], scope_label

    allowed_faces = set(candidate_faces)
    islands = []
    processed = set()

    for seed_face in candidate_faces:
        if seed_face in processed or not seed_face.is_valid:
            continue

        island_faces = _collect_connected_face_island(seed_face, allowed_faces)
        if not island_faces:
            processed.add(seed_face)
            continue

        island_faces = [face for face in island_faces if face is not None and face.is_valid]
        if not island_faces:
            continue

        processed.update(island_faces)
        islands.append(island_faces)

    return islands, scope_label


def _build_face_island_match(island_faces, kind, counts=None):
    face_set = {face for face in island_faces if face is not None and face.is_valid}
    edge_set = {edge for face in face_set for edge in face.edges if edge is not None and edge.is_valid}
    vert_set = {vert for face in face_set for vert in face.verts if vert is not None and vert.is_valid}
    return {
        "faces": list(face_set),
        "edges": list(edge_set),
        "verts": list(vert_set),
        "kind": kind,
        "counts": counts or {},
    }


def _select_face_island_matches(bm, matches):
    selected_faces = set()
    selected_edges = set()
    selected_verts = set()

    for item in matches:
        selected_faces.update(face for face in item.get("faces", []) if face is not None and face.is_valid)
        selected_edges.update(edge for edge in item.get("edges", []) if edge is not None and edge.is_valid)
        selected_verts.update(vert for vert in item.get("verts", []) if vert is not None and vert.is_valid)

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

    bm.select_flush_mode()


def _ngon_faces_from_bmesh(bm, *, selected_only=False):
    faces = []
    for face in bm.faces:
        if face is None or not face.is_valid or len(face.verts) <= 4:
            continue
        if selected_only and not face.select:
            continue
        faces.append(face)
    return faces


def _find_small_trash_face_islands(bm):
    from .nh_base import (_TRASH_TINY_ISLAND_MAX_EDGES, _TRASH_TINY_ISLAND_MAX_FACES, _TRASH_TINY_ISLAND_MAX_VERTS)
    islands, scope_label = _collect_candidate_face_islands(bm)
    matches = []

    for island_faces in islands:
        vert_count, face_count, edge_count = _connected_face_island_counts(island_faces)
        if (
            vert_count < _TRASH_TINY_ISLAND_MAX_VERTS
            or edge_count < _TRASH_TINY_ISLAND_MAX_EDGES
            or face_count < _TRASH_TINY_ISLAND_MAX_FACES
        ):
            matches.append(
                _build_face_island_match(
                    island_faces,
                    "trash",
                    counts={
                        "verts": vert_count,
                        "edges": edge_count,
                        "faces": face_count,
                    },
                )
            )

    return matches, scope_label


def _find_coplanar_plate_face_islands(bm, angle_tolerance_deg=0.1, plane_tolerance=0.0001):
    islands, scope_label = _collect_candidate_face_islands(bm)
    matches = []
    cos_limit = math.cos(math.radians(max(0.0, min(180.0, float(angle_tolerance_deg)))))
    plane_tolerance = max(0.0, float(plane_tolerance))

    for island_faces in islands:
        if not _connected_face_island_is_coplanar(island_faces, cos_limit, plane_tolerance):
            continue

        seed_face = next((face for face in island_faces if face is not None and face.is_valid), None)
        if seed_face is None or seed_face.normal.length_squared <= 1e-12 or len(seed_face.verts) < 3:
            continue

        plane_normal = seed_face.normal.copy()
        plane_normal.normalize()
        plane_point = seed_face.verts[0].co.copy()

        boundary_edges, boundary_verts, non_manifold = _classify_split_planar_region_edges(island_faces)
        if non_manifold:
            continue
        if not _split_planar_boundary_is_single_loop(boundary_edges, boundary_verts):
            continue
        if not _split_planar_region_is_thin(
            island_faces,
            plane_point,
            plane_normal,
            cos_limit,
            plane_tolerance,
        ):
            continue

        vert_count, face_count, edge_count = _connected_face_island_counts(island_faces)
        matches.append(
            _build_face_island_match(
                island_faces,
                "plate",
                counts={
                    "verts": vert_count,
                    "edges": edge_count,
                    "faces": face_count,
                },
            )
        )

    return matches, scope_label


class CRAY_OT_SelectSplitPlanarNgons(Operator):
    """Select tiny connected face islands treated as trash"""

    bl_idname = "cray.select_split_planar_ngons"
    bl_label = "Select Trash Islands"
    bl_description = (
        "In Edit Mode, find connected face islands treated as trash: "
        "verts < 5 or edges < 8 or faces < 5. "
        "If faces, edges, or verts are already selected, only that local area is searched"
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
        from .nh_base import (_TRASH_TINY_ISLAND_MAX_EDGES, _TRASH_TINY_ISLAND_MAX_FACES, _TRASH_TINY_ISLAND_MAX_VERTS, _fmt_exc)
        obj = context.active_object
        if obj is None or obj.type != "MESH" or context.mode != "EDIT_MESH" or obj.mode != "EDIT":
            self.report({"ERROR"}, "Active object must be a mesh in Edit Mode")
            return {"CANCELLED"}

        mesh = obj.data
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        try:
            matches, scope_label = _find_small_trash_face_islands(bm)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to analyze trash islands: {_fmt_exc(e)}")
            return {"CANCELLED"}

        if not matches:
            self.report(
                {"WARNING"},
                (
                    f"No trash islands found "
                    f"(verts < {_TRASH_TINY_ISLAND_MAX_VERTS} or "
                    f"edges < {_TRASH_TINY_ISLAND_MAX_EDGES} or "
                    f"faces < {_TRASH_TINY_ISLAND_MAX_FACES}) "
                    f"in {scope_label} scope"
                ),
            )
            return {"CANCELLED"}

        context.tool_settings.mesh_select_mode = (False, False, True)
        _select_face_island_matches(bm, matches)
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

        face_total = sum(len(item["faces"]) for item in matches)
        edge_total = sum(len(item["edges"]) for item in matches)
        vert_total = sum(len(item["verts"]) for item in matches)
        self.report(
            {"INFO"},
            (
                f"Selected {len(matches)} trash island(s): "
                f"{face_total} faces, {edge_total} edges, {vert_total} verts "
                f"in {scope_label} scope"
            ),
        )
        return {"FINISHED"}


class CRAY_OT_SelectCoplanarPlateIslands(Operator):
    """Select connected face islands that form a flat plate in one plane"""

    bl_idname = "cray.select_coplanar_plate_islands"
    bl_label = "Select Flat Plates"
    bl_description = (
        "In Edit Mode, find connected face islands that lie in one plane like a flat plate. "
        "If faces, edges, or verts are already selected, only that local area is searched"
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
        from .nh_base import (_fmt_exc)
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
            matches, scope_label = _find_coplanar_plate_face_islands(
                bm,
                angle_tolerance_deg=float(ts.split_planar_ngon_angle_tolerance),
                plane_tolerance=float(ts.split_planar_ngon_plane_tolerance),
            )
        except Exception as e:
            self.report({"ERROR"}, f"Failed to analyze flat plates: {_fmt_exc(e)}")
            return {"CANCELLED"}

        if not matches:
            self.report({"WARNING"}, f"No flat plates found in {scope_label} scope")
            return {"CANCELLED"}

        context.tool_settings.mesh_select_mode = (False, False, True)
        _select_face_island_matches(bm, matches)
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

        face_total = sum(len(item["faces"]) for item in matches)
        edge_total = sum(len(item["edges"]) for item in matches)
        vert_total = sum(len(item["verts"]) for item in matches)
        self.report(
            {"INFO"},
            (
                f"Selected {len(matches)} flat plate island(s): "
                f"{face_total} faces, {edge_total} edges, {vert_total} verts "
                f"in {scope_label} scope"
            ),
        )
        return {"FINISHED"}


class CRAY_OT_SelectNgonFaces(Operator):
    """Select all n-gon faces on the active edit mesh"""

    bl_idname = "cray.select_ngon_faces"
    bl_label = "Find N-gons"
    bl_description = "In Edit Mode, select all faces with more than 4 vertices on the active mesh"
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

        mesh = obj.data
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        ngons = _ngon_faces_from_bmesh(bm)
        if not ngons:
            self.report({"WARNING"}, "No n-gons found on active mesh")
            return {"CANCELLED"}

        context.tool_settings.mesh_select_mode = (False, False, True)
        for face in bm.faces:
            face.select_set(False)
        for edge in bm.edges:
            edge.select_set(False)
        for vert in bm.verts:
            vert.select_set(False)
        for face in ngons:
            face.select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

        max_sides = max(len(face.verts) for face in ngons)
        self.report({"INFO"}, f"Selected {len(ngons)} n-gon face(s), max sides: {max_sides}")
        return {"FINISHED"}


class CRAY_OT_TriangulateNgonFaces(Operator):
    """Triangulate selected n-gons, or all n-gons if none are selected"""

    bl_idname = "cray.triangulate_ngon_faces"
    bl_label = "Triangulate N-gons"
    bl_description = (
        "In Edit Mode, triangulate selected n-gons. "
        "If no selected n-gons exist, triangulate all n-gons on the active mesh"
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
        from .nh_base import (_fmt_exc)
        obj = context.active_object
        if obj is None or obj.type != "MESH" or context.mode != "EDIT_MESH" or obj.mode != "EDIT":
            self.report({"ERROR"}, "Active object must be a mesh in Edit Mode")
            return {"CANCELLED"}

        mesh = obj.data
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        selected_ngons = _ngon_faces_from_bmesh(bm, selected_only=True)
        if selected_ngons:
            ngons = selected_ngons
            scope_label = "selected"
        else:
            ngons = _ngon_faces_from_bmesh(bm)
            scope_label = "all"

        if not ngons:
            self.report({"WARNING"}, "No n-gons found to triangulate")
            return {"CANCELLED"}

        affected_vert_sets = [set(face.verts) for face in ngons if face.is_valid]

        try:
            bmesh.ops.triangulate(
                bm,
                faces=ngons,
                quad_method="BEAUTY",
                ngon_method="BEAUTY",
            )
        except Exception as e:
            self.report({"ERROR"}, f"Failed to triangulate n-gons: {_fmt_exc(e)}")
            return {"CANCELLED"}

        bm.faces.ensure_lookup_table()
        for face in bm.faces:
            face.select_set(False)
        selected_triangles = 0
        for face in bm.faces:
            if not face.is_valid or len(face.verts) != 3:
                continue
            face_verts = set(face.verts)
            if any(face_verts.issubset(source_verts) for source_verts in affected_vert_sets):
                face.select_set(True)
                selected_triangles += 1

        context.tool_settings.mesh_select_mode = (False, False, True)
        bm.select_flush_mode()
        bm.normal_update()
        bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)

        self.report(
            {"INFO"},
            f"Triangulated {len(ngons)} {scope_label} n-gon face(s) into {selected_triangles} triangle(s)",
        )
        return {"FINISHED"}


class CRAY_OT_EnsureColliderLOD(Operator):
    """Create or find the Geometry LOD object and move it into the Geometries collection"""

    bl_idname = "cray.ensure_collider_lod"
    bl_label = "Create/Find Collider LOD"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_scatter import (_FIRE_GEOMETRY_LOD_TOKEN)
        from .nh_snap import (_collider_target_validation_error, _deselect_all_in_view_layer, _select_object_in_view_layer)
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
        if str(getattr(cs, "target_lod", "") or "") == _FIRE_GEOMETRY_LOD_TOKEN:
            _set_collider_settings_object(context, "fire_geometry_object", target_obj)
            _sync_fire_geometry_material_selection(context)
        try:
            context.view_layer.update()
        except Exception:
            pass
        if context.mode == "OBJECT" and source_obj is None:
            _deselect_all_in_view_layer(context)
            _select_object_in_view_layer(context, target_obj, active=True)
        elif context.mode == "OBJECT" and previous_active is not None:
            try:
                _select_object_in_view_layer(context, previous_active, active=True)
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
        from .nh_base import (_fmt_exc)
        from .nh_snap import (_collider_target_validation_error, _is_collider_lod_mesh_object)
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
#  Experimental Geometry Collider tools
# ------------------------------------------------------------------------

_COLLIDER_EXP_TYPE_PROP = "nh_collider_exp_type"
_COLLIDER_EXP_SOURCE_PROP = "nh_collider_exp_source"
_COLLIDER_EXP_UUID_PROP = "nh_collider_exp_uuid"
_COLLIDER_EXP_PARAMS_PROP = "nh_collider_exp_params"
_COLLIDER_EXP_HISTORY_PROP = "nh_collider_exp_history"
_COLLIDER_EXP_HISTORY_LIMIT = 30
_COLLIDER_EXP_GUIDE_PROP = "nh_collider_exp_guide_type"
_COLLIDER_EXP_GUIDE_SOURCE_PROP = "nh_collider_exp_guide_source"
_COLLIDER_EXP_COMMON_PROPS = (
    "target_lod",
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
    "merge_distance",
    "recalc_normals",
)
_COLLIDER_EXP_PERSISTENT_OPERATOR_PROPS = {
    "target_lod",
    "minimum_size",
    "normal_minimum_size",
}
_COLLIDER_EXP_BOX_FACES = (
    (0, 3, 2, 1),
    (4, 5, 6, 7),
    (0, 1, 5, 4),
    (1, 2, 6, 5),
    (2, 3, 7, 6),
    (3, 0, 4, 7),
)


def _collider_exp_settings_exp(context):
    return getattr(getattr(context, "scene", None), "cray_collider_exp_settings", None)

