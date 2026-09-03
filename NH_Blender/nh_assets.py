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

# nh_assets.py
# auto-split slice; cross-module refs resolved with in-function imports

def _iter_p3d_files_in_folder(folder_abs: str):
    from .nh_model_split import (_is_ignored_nh_objects_asset_path)
    out = []
    for root, dirs, files in os.walk(folder_abs):
        dirs[:] = [
            name for name in dirs
            if not _is_ignored_nh_objects_asset_path(os.path.join(root, name))
        ]
        if _is_ignored_nh_objects_asset_path(root):
            continue
        for fn in files:
            if fn.lower().endswith('.p3d'):
                out.append(os.path.join(root, fn))
    out.sort(key=lambda x: x.lower())
    return out

def _ensure_temp_asset_scene():
    from .nh_model_split import (_NH_TEMP_ASSET_SCENE_NAME)
    scenes = getattr(bpy.data, "scenes", None)
    if scenes is None:
        raise RuntimeError("Blender scenes are not available right now")
    scene = scenes.get(_NH_TEMP_ASSET_SCENE_NAME)
    if scene is None:
        scene = scenes.new(_NH_TEMP_ASSET_SCENE_NAME)
    return scene


def _ensure_temp_asset_library_root(context):
    from .nh_base import (_iter_safe_scenes)
    from .nh_model_split import (_NH_TEMP_ASSET_LIBRARY_NAME)
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
    from .nh_model_split import (_NH_TEMP_ASSET_LIBRARY_NAME, _NH_TEMP_ASSET_SCENE_NAME)
    from .nh_textures import (_iter_collection_tree)
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

def _load_custom_asset_preview_safe(id_data, filepath: str):
    if id_data is None or not filepath or not os.path.isfile(filepath):
        return False
    try:
        with bpy.context.temp_override(id=id_data):
            result = bpy.ops.ed.lib_id_load_custom_preview(filepath=filepath)
        return "FINISHED" in set(result or [])
    except Exception:
        try:
            override = bpy.context.copy()
            override["id"] = id_data
            result = bpy.ops.ed.lib_id_load_custom_preview(override, filepath=filepath)
            return "FINISHED" in set(result or [])
        except Exception:
            return False


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


def _mark_object_as_asset_safe(obj, catalog_id=None, preview_path=None):
    if obj is None:
        return
    try:
        obj.asset_mark()
    except Exception:
        pass
    if catalog_id:
        try:
            asset_data = getattr(obj, "asset_data", None)
            if asset_data is not None:
                asset_data.catalog_id = str(catalog_id)
            else:
                obj["catalog_id"] = str(catalog_id)
        except Exception:
            pass
    if not _load_custom_asset_preview_safe(obj, preview_path or ""):
        _generate_asset_preview_safe(obj)


def _asset_preview_filename(name: str) -> str:
    base = os.path.splitext(str(name or "asset"))[0]
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", base).strip(" .") or "asset"
    return base[:120] + ".png"


def _p3d_contains_preview_camera_selection(filepath: str) -> bool:
    needle = b"nh_cam"
    tail = b""
    try:
        with open(filepath, "rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    return False
                haystack = (tail + chunk).lower()
                if needle in haystack:
                    return True
                tail = haystack[-(len(needle) - 1):]
    except Exception:
        return False


def _p3d_preview_camera_hint(filepath: str):
    from .nh_base import (_fmt_exc)
    from .nh_model_split import (_NH_PREVIEW_CAMERA_SELECTION_RE)
    from .nh_snap import (_get_p3d_data_p3d_module)
    """Read nh_cam[_degrees] directly from the P3D Memory LOD."""
    if not filepath:
        return None
    try:
        filepath_abs = os.path.abspath(bpy.path.abspath(filepath))
    except Exception:
        filepath_abs = os.path.abspath(filepath)
    if not os.path.isfile(filepath_abs):
        return None
    if not _p3d_contains_preview_camera_selection(filepath_abs):
        return None

    p3d_mod = _get_p3d_data_p3d_module()
    if p3d_mod is None:
        return None

    try:
        mlod = p3d_mod.P3D_MLOD.read_file(filepath_abs, first_lod_only=False)
    except Exception as e:
        print(f"NH asset preview camera: could not read {filepath_abs}: {_fmt_exc(e)}")
        return None

    lod_resolution_cls = getattr(p3d_mod, "P3D_LOD_Resolution", None)
    memory_lod_id = int(getattr(lod_resolution_cls, "MEMORY", 9)) if lod_resolution_cls is not None else 9
    candidates = []

    for lod in getattr(mlod, "lods", []) or []:
        resolution = getattr(lod, "resolution", None)
        try:
            lod_id = int(getattr(resolution, "lod", -1))
        except Exception:
            lod_id = -1
        if lod_id != memory_lod_id:
            continue

        lod_vertices = list(getattr(lod, "verts", []) or [])
        for tagg_index, tagg in enumerate(getattr(lod, "taggs", []) or []):
            if not bool(getattr(tagg, "active", True)):
                continue
            selection_name = str(getattr(tagg, "name", "") or "").strip()
            match = _NH_PREVIEW_CAMERA_SELECTION_RE.fullmatch(selection_name)
            if match is None:
                continue

            points = []
            selection_data = getattr(tagg, "data", None)
            for vert_index, weight in getattr(selection_data, "weight_verts", []) or []:
                try:
                    if float(weight) <= 0.0:
                        continue
                    co = lod_vertices[int(vert_index)]
                    points.append(Vector((float(co[0]), float(co[1]), float(co[2]))))
                except Exception:
                    continue
            if not points:
                continue

            point = sum(points, Vector((0.0, 0.0, 0.0))) / len(points)
            try:
                yaw_degrees = float(match.group(1) or 0.0)
            except Exception:
                yaw_degrees = 0.0
            candidates.append({
                "location": point,
                "yaw_degrees": yaw_degrees,
                "selection": selection_name,
                # An explicit angle is more specific than a plain nh_cam if both exist.
                "priority": (int(match.group(1) is not None), -tagg_index),
            })

    if not candidates:
        return None
    best = max(candidates, key=lambda item: item["priority"])
    best.pop("priority", None)
    return best


def _first_image_from_collection_materials(collection):
    from .nh_textures import (_collect_collection_objects_recursive)
    if collection is None:
        return None
    for obj in _collect_collection_objects_recursive(collection):
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        for slot in getattr(obj, "material_slots", []) or []:
            mat = getattr(slot, "material", None)
            if mat is None or not getattr(mat, "use_nodes", False) or getattr(mat, "node_tree", None) is None:
                continue
            for node in getattr(mat.node_tree, "nodes", []) or []:
                if getattr(node, "type", None) != "TEX_IMAGE":
                    continue
                image = getattr(node, "image", None)
                if image is not None:
                    return image
    return None


def _collection_bounds_world(collection):
    from .nh_textures import (_collect_collection_objects_recursive)
    if collection is None:
        return None, None

    coords = []
    for obj in _collect_collection_objects_recursive(collection):
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        try:
            matrix_world = obj.matrix_world.copy()
            for corner in getattr(obj, "bound_box", []) or []:
                coords.append(matrix_world @ Vector(corner))
        except Exception:
            continue

    if not coords:
        return None, None

    min_v = Vector((
        min(v.x for v in coords),
        min(v.y for v in coords),
        min(v.z for v in coords),
    ))
    max_v = Vector((
        max(v.x for v in coords),
        max(v.y for v in coords),
        max(v.z for v in coords),
    ))
    return min_v, max_v


def _preview_view_direction(center, camera_hint=None):
    default_view_dir = Vector((1.7, -2.2, 1.35)).normalized()
    if not isinstance(camera_hint, dict):
        return default_view_dir

    try:
        camera_point = Vector(camera_hint.get("location"))
        offset = camera_point - Vector(center)
    except Exception:
        return default_view_dir
    if offset.length_squared <= 1e-12:
        return default_view_dir

    try:
        yaw_degrees = float(camera_hint.get("yaw_degrees", 0.0) or 0.0)
    except Exception:
        yaw_degrees = 0.0
    if abs(yaw_degrees) > 1e-9:
        try:
            offset = Matrix.Rotation(math.radians(yaw_degrees), 4, "Z") @ offset
        except Exception:
            pass
    if offset.length_squared <= 1e-12:
        return default_view_dir
    return offset.normalized()


def _preview_projection_axes(view_dir=None):
    try:
        view_dir = Vector(view_dir).normalized()
    except Exception:
        view_dir = Vector((1.7, -2.2, 1.35)).normalized()
    if view_dir.length_squared <= 1e-12:
        view_dir = Vector((1.7, -2.2, 1.35)).normalized()

    right = Vector((0.0, 0.0, 1.0)).cross(view_dir)
    if right.length_squared <= 1e-12:
        right = Vector((1.0, 0.0, 0.0))
    right.normalize()
    up = view_dir.cross(right)
    if up.length_squared <= 1e-12:
        up = Vector((0.0, 1.0, 0.0))
    up.normalize()
    return right, up, view_dir


def _preview_projected_span(min_v, max_v, view_dir) -> float:
    right, up, _view_dir = _preview_projection_axes(view_dir)
    corners = [
        Vector((x, y, z))
        for x in (float(min_v.x), float(max_v.x))
        for y in (float(min_v.y), float(max_v.y))
        for z in (float(min_v.z), float(max_v.z))
    ]
    right_values = [point.dot(right) for point in corners]
    up_values = [point.dot(up) for point in corners]
    return max(
        max(right_values) - min(right_values),
        max(up_values) - min(up_values),
        0.25,
    )


def _set_camera_look_at(camera_obj, target):
    if camera_obj is None:
        return
    try:
        direction = Vector(target) - camera_obj.location
        if direction.length_squared <= 1e-12:
            return
        camera_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    except Exception:
        pass


def _set_preview_render_engine_safe(scene):
    if scene is None:
        return
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scene.render.engine = engine
            break
        except Exception:
            continue

    try:
        scene.render.film_transparent = True
    except Exception:
        pass
    try:
        scene.view_settings.view_transform = "Filmic"
        scene.view_settings.look = "Medium High Contrast"
        scene.view_settings.exposure = 0
        scene.view_settings.gamma = 1
    except Exception:
        pass
    try:
        scene.eevee.taa_render_samples = 32
    except Exception:
        pass
    try:
        scene.eevee.taa_samples = 32
    except Exception:
        pass


def _render_scene_preview_safe(scene, filepath: str) -> bool:
    if scene is None or not filepath:
        return False
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
    except Exception:
        pass

    old_filepath = ""
    try:
        old_filepath = scene.render.filepath
    except Exception:
        old_filepath = ""

    old_window_scene = None
    window = getattr(bpy.context, "window", None)
    if window is not None:
        try:
            old_window_scene = window.scene
            window.scene = scene
        except Exception:
            old_window_scene = None

    try:
        scene.render.filepath = filepath
        try:
            result = bpy.ops.render.render(write_still=True, scene=scene.name)
        except TypeError:
            result = bpy.ops.render.render(write_still=True)
        return "FINISHED" in set(result or []) and os.path.isfile(filepath)
    except Exception:
        return False
    finally:
        try:
            scene.render.filepath = old_filepath
        except Exception:
            pass
        if window is not None and old_window_scene is not None:
            try:
                window.scene = old_window_scene
            except Exception:
                pass


def _asset_rendered_preview_path_for_collection(collection, preview_dir: str, size: int = 256, camera_hint=None):
    if collection is None or not preview_dir:
        return ""
    if _first_image_from_collection_materials(collection) is None:
        return ""

    min_v, max_v = _collection_bounds_world(collection)
    if min_v is None or max_v is None:
        return ""

    try:
        os.makedirs(preview_dir, exist_ok=True)
        preview_path = os.path.join(preview_dir, _asset_preview_filename(getattr(collection, "name", "") or "asset"))

        scene = bpy.data.scenes.new(f"NH Asset Preview {getattr(collection, 'name', 'asset')}")
        camera_data = bpy.data.cameras.new(f"NH Asset Preview Camera {getattr(collection, 'name', 'asset')}")
        camera_obj = bpy.data.objects.new(camera_data.name, camera_data)
        light_data = bpy.data.lights.new(f"NH Asset Preview Light {getattr(collection, 'name', 'asset')}", "AREA")
        light_obj = bpy.data.objects.new(light_data.name, light_data)

        try:
            scene.collection.children.link(collection)
        except Exception:
            pass
        try:
            scene.collection.objects.link(camera_obj)
        except Exception:
            pass
        try:
            scene.collection.objects.link(light_obj)
        except Exception:
            pass

        center = (min_v + max_v) * 0.5
        dims = max_v - min_v
        max_dim = max(float(dims.x), float(dims.y), float(dims.z), 0.25)
        view_dir = _preview_view_direction(center, camera_hint)
        projected_span = _preview_projected_span(min_v, max_v, view_dir)

        try:
            camera_data.type = "ORTHO"
            camera_data.ortho_scale = projected_span * 1.08
            camera_data.clip_start = 0.01
            camera_data.clip_end = max(max_dim * 20.0, 100.0)
        except Exception:
            pass
        camera_distance = max(max_dim * 2.2, 2.0)
        if isinstance(camera_hint, dict):
            try:
                hinted_distance = (Vector(camera_hint.get("location")) - center).length
                camera_distance = max(camera_distance, float(hinted_distance))
            except Exception:
                pass
        camera_obj.location = center + view_dir * camera_distance
        _set_camera_look_at(camera_obj, center)

        try:
            light_data.energy = 450.0
            light_data.size = max(max_dim * 2.2, 3.0)
        except Exception:
            pass
        light_obj.location = center + view_dir * max(max_dim * 1.2, 1.0) + Vector((0.0, 0.0, max(max_dim * 1.8, 2.0)))

        scene.camera = camera_obj
        scene.render.resolution_x = int(size)
        scene.render.resolution_y = int(size)
        scene.render.resolution_percentage = 100
        _set_preview_render_engine_safe(scene)

        ok = _render_scene_preview_safe(scene, preview_path)
        return preview_path if ok and os.path.isfile(preview_path) else ""
    except Exception:
        return ""
    finally:
        try:
            if scene is not None and any(ch == collection for ch in scene.collection.children):
                scene.collection.children.unlink(collection)
        except Exception:
            pass
        for obj in (locals().get("camera_obj"), locals().get("light_obj")):
            try:
                if obj is not None and bpy.data.objects.get(obj.name) is not None:
                    bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
        for datablock in (locals().get("camera_data"), locals().get("light_data")):
            try:
                if datablock is not None and datablock.users == 0:
                    if hasattr(bpy.data, "cameras") and getattr(datablock, "__class__", None).__name__ == "Camera":
                        bpy.data.cameras.remove(datablock)
                    elif hasattr(bpy.data, "lights"):
                        bpy.data.lights.remove(datablock)
            except Exception:
                pass
        try:
            if scene is not None and bpy.data.scenes.get(scene.name) is not None:
                bpy.data.scenes.remove(scene)
        except Exception:
            pass


def _asset_preview_path_for_collection(
    collection,
    preview_dir: str,
    render_textured_previews: bool = False,
    source_filepath: str = "",
):
    camera_hint = _p3d_preview_camera_hint(source_filepath)
    if camera_hint:
        print(
            "NH asset preview camera: "
            f"{os.path.basename(source_filepath)} uses {camera_hint.get('selection', 'nh_cam')}"
        )

    if render_textured_previews:
        rendered_preview = _asset_rendered_preview_path_for_collection(
            collection,
            preview_dir,
            camera_hint=camera_hint,
        )
        if rendered_preview:
            return rendered_preview

    geometry_preview = _asset_geometry_preview_path_for_collection(
        collection,
        preview_dir,
        camera_hint=camera_hint,
    )
    if geometry_preview:
        return geometry_preview
    return ""


def _preview_materialless_color(obj=None, material_index: int = 0):
    del obj, material_index
    return (0.66, 0.68, 0.64)


def _collect_collection_preview_geometry(collection, max_faces=900, max_edges=2600):
    from .nh_textures import (_collect_collection_objects_recursive)
    vertices = []
    triangles = []
    edges = []
    face_count = 0

    for obj in _collect_collection_objects_recursive(collection):
        if obj is None or getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
            continue
        mesh = obj.data
        try:
            matrix_world = obj.matrix_world.copy()
        except Exception:
            matrix_world = Matrix.Identity(4)
        try:
            world_vertices = [matrix_world @ vert.co for vert in mesh.vertices]
        except Exception:
            continue
        if not world_vertices:
            continue
        vertices.extend(world_vertices)

        polys = list(getattr(mesh, "polygons", []) or [])
        step = max(1, math.ceil(len(polys) / max(1, max_faces - face_count))) if face_count < max_faces else len(polys) + 1
        for poly in polys[::step]:
            if face_count >= max_faces:
                break
            indices = list(getattr(poly, "vertices", []) or [])
            if len(indices) < 3:
                continue
            color = _preview_materialless_color(obj, int(getattr(poly, "material_index", 0) or 0))
            for idx in range(1, len(indices) - 1):
                try:
                    p0 = world_vertices[indices[0]]
                    p1 = world_vertices[indices[idx]]
                    p2 = world_vertices[indices[idx + 1]]
                except Exception:
                    continue
                normal = (p1 - p0).cross(p2 - p0)
                if normal.length_squared <= 1e-14:
                    continue
                triangles.append((p0, p1, p2, normal.normalized(), color))
            face_count += 1

        mesh_edges = list(getattr(mesh, "edges", []) or [])
        edge_step = max(1, math.ceil(len(mesh_edges) / max(1, max_edges - len(edges)))) if len(edges) < max_edges else len(mesh_edges) + 1
        for edge in mesh_edges[::edge_step]:
            if len(edges) >= max_edges:
                break
            try:
                a, b = edge.vertices
                edges.append((world_vertices[a], world_vertices[b]))
            except Exception:
                continue

    return vertices, triangles, edges


def _set_preview_pixel(pixels, size, x, y, color, alpha=1.0):
    x = int(x)
    y = int(y)
    if x < 0 or y < 0 or x >= size or y >= size:
        return
    offset = (y * size + x) * 4
    alpha = max(0.0, min(1.0, float(alpha)))
    inv = 1.0 - alpha
    pixels[offset] = pixels[offset] * inv + color[0] * alpha
    pixels[offset + 1] = pixels[offset + 1] * inv + color[1] * alpha
    pixels[offset + 2] = pixels[offset + 2] * inv + color[2] * alpha
    pixels[offset + 3] = 1.0


def _draw_preview_line(pixels, size, a, b, color, alpha=0.75):
    x0, y0 = a
    x1, y1 = b
    steps = int(max(abs(x1 - x0), abs(y1 - y0), 1))
    for i in range(steps + 1):
        t = i / steps
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        _set_preview_pixel(pixels, size, round(x), round(y), color, alpha)
        if size >= 160:
            _set_preview_pixel(pixels, size, round(x) + 1, round(y), color, alpha * 0.45)
            _set_preview_pixel(pixels, size, round(x), round(y) + 1, color, alpha * 0.45)


def _fill_preview_triangle(pixels, size, p0, p1, p2, color):
    x0, y0 = p0
    x1, y1 = p1
    x2, y2 = p2
    min_x = max(0, int(math.floor(min(x0, x1, x2))))
    max_x = min(size - 1, int(math.ceil(max(x0, x1, x2))))
    min_y = max(0, int(math.floor(min(y0, y1, y2))))
    max_y = min(size - 1, int(math.ceil(max(y0, y1, y2))))
    denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(denom) <= 1e-8:
        return
    for y in range(min_y, max_y + 1):
        py = y + 0.5
        for x in range(min_x, max_x + 1):
            px = x + 0.5
            a = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / denom
            b = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / denom
            c = 1.0 - a - b
            if a >= -1e-5 and b >= -1e-5 and c >= -1e-5:
                _set_preview_pixel(pixels, size, x, y, color, 0.94)


def _asset_geometry_preview_path_for_collection(collection, preview_dir: str, size: int = 160, camera_hint=None):
    from .nh_textures import (_remove_image_if_unused, _save_image_as_png)
    if collection is None or not preview_dir:
        return ""
    vertices, triangles, edges = _collect_collection_preview_geometry(collection)
    if not vertices:
        return ""

    min_v = Vector((
        min(point.x for point in vertices),
        min(point.y for point in vertices),
        min(point.z for point in vertices),
    ))
    max_v = Vector((
        max(point.x for point in vertices),
        max(point.y for point in vertices),
        max(point.z for point in vertices),
    ))
    center = (min_v + max_v) * 0.5
    view_dir = _preview_view_direction(center, camera_hint)
    right, up, view_dir = _preview_projection_axes(view_dir)

    projected_vertices = [((v - center).dot(right), (v - center).dot(up)) for v in vertices]
    min_x = min(p[0] for p in projected_vertices)
    max_x = max(p[0] for p in projected_vertices)
    min_y = min(p[1] for p in projected_vertices)
    max_y = max(p[1] for p in projected_vertices)
    span = max(max_x - min_x, max_y - min_y, 1e-6)
    scale = (size * 0.90) / span
    offset_x = size * 0.5 - (min_x + max_x) * 0.5 * scale
    offset_y = size * 0.5 + (min_y + max_y) * 0.5 * scale

    def project(point):
        rel = point - center
        return (
            rel.dot(right) * scale + offset_x,
            -rel.dot(up) * scale + offset_y,
            rel.dot(view_dir),
        )

    pixels = [0.105, 0.112, 0.118, 1.0] * (size * size)
    light_dir = Vector((0.35, -0.45, 0.82)).normalized()
    projected_tris = []
    for p0, p1, p2, normal, base_color in triangles:
        shade = 0.45 + 0.55 * max(0.0, abs(normal.dot(light_dir)))
        color = tuple(max(0.0, min(1.0, c * shade)) for c in base_color)
        q0 = project(p0)
        q1 = project(p1)
        q2 = project(p2)
        depth = (q0[2] + q1[2] + q2[2]) / 3.0
        projected_tris.append((depth, q0[:2], q1[:2], q2[:2], color))

    for _depth, q0, q1, q2, color in sorted(projected_tris, key=lambda item: item[0]):
        _fill_preview_triangle(pixels, size, q0, q1, q2, color)

    wire_color = (0.02, 0.025, 0.025)
    for p0, p1 in edges:
        q0 = project(p0)
        q1 = project(p1)
        _draw_preview_line(pixels, size, q0[:2], q1[:2], wire_color, alpha=0.72)

    try:
        os.makedirs(preview_dir, exist_ok=True)
        preview_path = os.path.join(preview_dir, _asset_preview_filename(getattr(collection, "name", "") or "asset"))
        image_name = f"NH preview {getattr(collection, 'name', 'asset')}"
        try:
            image = bpy.data.images.new(image_name, size, size, alpha=True, float_buffer=False)
        except TypeError:
            image = bpy.data.images.new(image_name, size, size, alpha=True)
        try:
            image.pixels.foreach_set(pixels)
            try:
                image.update()
            except Exception:
                pass
            _save_image_as_png(image, preview_path)
        finally:
            _remove_image_if_unused(image)
        return preview_path if os.path.isfile(preview_path) else ""
    except Exception:
        return ""



def _clear_asset_mark_safe(id_data):
    if id_data is None:
        return
    try:
        if getattr(id_data, "asset_data", None) is not None:
            id_data.asset_clear()
    except Exception:
        pass


def _asset_instancer_name_for_collection(collection, filepath: str):
    base = os.path.splitext(os.path.basename(filepath or ""))[0]
    if not base:
        base = getattr(collection, "name", "") or "Asset"
    if not base.lower().endswith(".p3d"):
        base += ".p3d"
    return base


def _create_asset_instancer_for_collection(asset_root, collection, filepath: str, catalog_id=None, preview_dir=None, render_textured_previews=False, preview_paths_out=None):
    from .nh_collider_exp import (_norm_path)
    from .nh_textures import (_IE_SOURCE_PATH_KEY)
    if asset_root is None or collection is None:
        return None
    name = _asset_instancer_name_for_collection(collection, filepath)
    instancer = bpy.data.objects.new(name, None)
    try:
        instancer.empty_display_type = "CUBE"
        instancer.empty_display_size = 1.0
    except Exception:
        pass
    try:
        instancer.instance_type = "COLLECTION"
        instancer.instance_collection = collection
    except Exception:
        pass
    try:
        instancer[_IE_SOURCE_PATH_KEY] = _norm_path(bpy.path.abspath(filepath))
    except Exception:
        pass
    try:
        asset_root.objects.link(instancer)
    except Exception:
        pass
    preview_path = _asset_preview_path_for_collection(
        collection,
        preview_dir or "",
        render_textured_previews=render_textured_previews,
        source_filepath=filepath,
    )
    if preview_path and preview_paths_out is not None:
        try:
            preview_paths_out.append(preview_path)
        except Exception:
            pass
    _clear_asset_mark_safe(collection)
    _mark_object_as_asset_safe(instancer, catalog_id=catalog_id, preview_path=preview_path)
    return instancer


def _move_import_result_into_asset_library(context, filepath, pre_obj_ptrs, pre_col_ptrs, asset_root, catalog_id=None, preview_dir=None, render_textured_previews=False, preview_paths_out=None):
    from .nh_collider_exp import (_norm_path)
    from .nh_textures import (_IE_SOURCE_PATH_KEY, _collection_has_any_object_ptr, _find_collection_path, _tag_import_source_on_imported_data)
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
            _create_asset_instancer_for_collection(asset_root, col, filepath, catalog_id=catalog_id, preview_dir=preview_dir, render_textured_previews=render_textured_previews, preview_paths_out=preview_paths_out)
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
        _create_asset_instancer_for_collection(asset_root, col, filepath, catalog_id=catalog_id, preview_dir=preview_dir, render_textured_previews=render_textured_previews, preview_paths_out=preview_paths_out)
        moved = 1

    return moved, len(imported_objs)


from .nh_base import (_NH_OBJECTS_DEFAULT_COMMON_ROOT, _NH_OBJECTS_DEFAULT_CUSTOM_SEARCH_ROOT, _NH_OBJECTS_DEFAULT_ENVIRONMENT_ROOT)

class CRAY_PG_AssetLibrarySettings(PropertyGroup):
    folder: StringProperty(name="P3D Folder", default="", subtype="DIR_PATH")
    common_root: StringProperty(
        name="Common Folder",
        default=_NH_OBJECTS_DEFAULT_COMMON_ROOT,
        subtype="DIR_PATH",
        description="РџР°РїРєР° Common СЃ .p3d Р°СЃСЃРµС‚Р°РјРё; РїРѕРґРїР°РїРєР° Buildings РёРіРЅРѕСЂРёСЂСѓРµС‚СЃСЏ",
    )
    environment_root: StringProperty(
        name="Environment Folder",
        default=_NH_OBJECTS_DEFAULT_ENVIRONMENT_ROOT,
        subtype="DIR_PATH",
        description="РџР°РїРєР° Environment СЃ .p3d Р°СЃСЃРµС‚Р°РјРё",
    )
    custom_search_root: StringProperty(
        name="Custom Search Root",
        default=_NH_OBJECTS_DEFAULT_CUSTOM_SEARCH_ROOT,
        subtype="DIR_PATH",
        description="Root folder used to find a .p3d by name for the Custom asset library",
    )
    custom_p3d_name: StringProperty(
        name="Custom P3D Name",
        default="",
        description="Type a model name like pripyat_shoppingMall_sign, without .p3d, and add it to NH Objects - Custom",
    )
    import_first_lod_only: BoolProperty(
        name="Import first LOD only",
        default=True,
    )
    clear_previous_temp_library: BoolProperty(
        name="Clear previous temp library",
        default=True,
    )
    rebuild_existing_libraries: BoolProperty(
        name="Rebuild existing NH libraries",
        default=False,
        description="Р—Р°РЅРѕРІРѕ РёРјРїРѕСЂС‚РёСЂРѕРІР°С‚СЊ РїР°РїРєРё, РґР°Р¶Рµ РµСЃР»Рё РєРµС€РёСЂРѕРІР°РЅРЅР°СЏ _NH_AssetLibrary.blend Р±РёР±Р»РёРѕС‚РµРєР° СѓР¶Рµ Р°РєС‚СѓР°Р»СЊРЅР°",
    )
    render_textured_previews: BoolProperty(
        name="Textured rendered previews",
        default=False,
        description="Р РµРЅРґРµСЂРёС‚СЊ РёРєРѕРЅРєРё Asset Browser СЃ СѓР¶Рµ РєРµС€РёСЂРѕРІР°РЅРЅС‹РјРё С‚РµРєСЃС‚СѓСЂР°РјРё. Р•СЃР»Рё PNG РґР»СЏ .paa РµС‰Рµ РЅРµС‚ РІ РєРµС€Рµ, РёРєРѕРЅРєР° Р±С‹СЃС‚СЂРѕ СЃРѕР·РґР°РµС‚СЃСЏ РєР°Рє geometry preview Р±РµР· РєРѕРЅРІРµСЂС‚Р°С†РёРё С‚РµРєСЃС‚СѓСЂС‹",
    )
    asset_cut_name: StringProperty(
        name="Asset Name",
        default="new asset",
        description="Name of the new asset scene and its .p3d root collection when cutting a selection",
    )

class CRAY_OT_AssetLibraryBuildFromFolder(Operator):
    bl_idname = "cray.asset_library_build_folder"
    bl_label = "Build From Folder"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_snap import (_has_any_p3d_import_ops)
        st = context.scene.cray_asset_library_settings
        if not _has_any_p3d_import_ops():
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
        from .nh_snap import (_has_any_p3d_import_ops)
        if not _has_any_p3d_import_ops():
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


class CRAY_OT_AssetLibraryCleanSourceArtifacts(Operator):
    bl_idname = "cray.asset_library_clean_source_artifacts"
    bl_label = "Clean Source Cache Files"
    bl_description = (
        "Remove legacy _NH_AssetLibrary cache files from NH_Objects source folders; "
        "current libraries are stored outside the pack tree"
    )
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        from .nh_model_split import (_cleanup_legacy_nh_asset_library_artifacts)
        settings = context.scene.cray_asset_library_settings
        stats = _cleanup_legacy_nh_asset_library_artifacts(settings)
        failed = list(stats.get("failed", []) or [])
        if failed:
            print("=== NH Objects Asset Libraries: source cache cleanup failures ===")
            for item in failed:
                print(item)

        removed_files = int(stats.get("removed_files", 0) or 0)
        removed_dirs = int(stats.get("removed_dirs", 0) or 0)
        msg = f"Removed {removed_files} legacy cache file(s), {removed_dirs} preview folder(s)"
        if failed:
            self.report({"WARNING"}, msg + f", failed {len(failed)} (see System Console)")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


def _find_registered_asset_library_by_path(path_abs: str):
    libraries = getattr(getattr(bpy.context, "preferences", None), "filepaths", None)
    libraries = getattr(libraries, "asset_libraries", None)
    if libraries is None:
        return None, -1

    wanted = os.path.normcase(os.path.abspath(bpy.path.abspath(path_abs)))
    for idx, lib in enumerate(libraries):
        try:
            lib_path = os.path.normcase(os.path.abspath(bpy.path.abspath(getattr(lib, "path", "") or "")))
        except Exception:
            lib_path = ""
        if lib_path == wanted:
            return lib, idx
    return None, -1


def _find_registered_asset_library_by_name(name: str):
    libraries = getattr(getattr(bpy.context, "preferences", None), "filepaths", None)
    libraries = getattr(libraries, "asset_libraries", None)
    if libraries is None:
        return None, -1

    wanted = str(name or "").strip()
    if not wanted:
        return None, -1
    for idx, lib in enumerate(libraries):
        try:
            lib_name = str(getattr(lib, "name", "") or "").strip()
        except Exception:
            lib_name = ""
        if lib_name == wanted:
            return lib, idx
    return None, -1


def _ensure_blender_asset_library_registered(name: str, path_abs: str):
    if not path_abs or not os.path.isdir(path_abs):
        return False

    lib, _idx = _find_registered_asset_library_by_path(path_abs)
    if lib is None:
        lib, _idx = _find_registered_asset_library_by_name(name)
    if lib is None:
        try:
            result = bpy.ops.preferences.asset_library_add(directory=path_abs)
        except Exception:
            result = {"CANCELLED"}
        if "FINISHED" not in set(result or []):
            return False
        lib, _idx = _find_registered_asset_library_by_path(path_abs)

    if lib is None:
        return False

    try:
        lib.name = name
    except Exception:
        pass
    try:
        lib.path = path_abs
    except Exception:
        pass
    try:
        lib.enabled = True
    except Exception:
        pass
    try:
        lib.import_method = "APPEND_REUSE"
    except Exception:
        pass
    try:
        lib.use_relative_path = False
    except Exception:
        pass
    return True


def _register_nh_objects_blender_asset_libraries():
    from .nh_model_split import (_iter_nh_objects_asset_roots, _nh_asset_library_settings)
    registered = 0
    missing = []
    settings = _nh_asset_library_settings()
    for name, root_abs in _iter_nh_objects_asset_roots(settings):
        if _ensure_blender_asset_library_registered(name, root_abs):
            registered += 1
        else:
            missing.append(root_abs)
    try:
        _write_custom_asset_catalog_file()
    except Exception:
        pass
    try:
        bpy.ops.wm.save_userpref()
    except Exception:
        pass
    return registered, missing


def _p3d_file_manifest_entry(folder_abs: str, filepath: str):
    stat = os.stat(filepath)
    try:
        rel_path = os.path.relpath(filepath, folder_abs)
    except Exception:
        rel_path = os.path.basename(filepath)
    rel_path = rel_path.replace(os.sep, "/")
    return {
        "path": rel_path,
        "size": int(getattr(stat, "st_size", 0) or 0),
        "mtime_ns": int(getattr(stat, "st_mtime_ns", int(getattr(stat, "st_mtime", 0.0) * 1000000000))),
    }


def _p3d_folder_manifest(source_folder_abs: str, p3d_files, settings=None, preview_mode_override: str = ""):
    from .nh_collider_exp import (_norm_path)
    from .nh_model_split import (_NH_OBJECTS_ASSET_BLEND_NAME, _NH_OBJECTS_ASSET_MANIFEST_VERSION)
    entries = []
    for fp in sorted((p3d_files or []), key=lambda item: item.lower()):
        if not fp or not os.path.isfile(fp):
            continue
        try:
            entries.append(_p3d_file_manifest_entry(source_folder_abs, fp))
        except Exception:
            continue
    preview_mode = (
        str(preview_mode_override)
        if preview_mode_override
        else ("textured" if bool(getattr(settings, "render_textured_previews", False)) else "geometry")
    )
    return {
        "version": _NH_OBJECTS_ASSET_MANIFEST_VERSION,
        "asset_blend": _NH_OBJECTS_ASSET_BLEND_NAME,
        "source_folder": _norm_path(source_folder_abs),
        "preview_mode": preview_mode,
        "files": entries,
    }


def _cache_relative_file_manifest_entries(cache_folder_abs: str, filepaths):
    entries = []
    seen = set()
    cache_folder_abs = os.path.abspath(bpy.path.abspath(cache_folder_abs or ""))
    for filepath in filepaths or []:
        if not filepath:
            continue
        try:
            path_abs = os.path.abspath(bpy.path.abspath(filepath))
        except Exception:
            path_abs = os.path.abspath(filepath)
        key = os.path.normcase(path_abs)
        if key in seen or not os.path.isfile(path_abs):
            continue
        seen.add(key)
        try:
            rel_path = os.path.relpath(path_abs, cache_folder_abs)
        except Exception:
            rel_path = os.path.basename(path_abs)
        try:
            stat = os.stat(path_abs)
        except Exception:
            continue
        entries.append({
            "path": rel_path.replace(os.sep, "/"),
            "size": int(getattr(stat, "st_size", 0) or 0),
            "mtime_ns": int(getattr(stat, "st_mtime_ns", int(getattr(stat, "st_mtime", 0.0) * 1000000000))),
        })
    return entries


def _manifest_preview_files_are_ready(manifest_path: str, manifest: dict) -> bool:
    preview_files = manifest.get("preview_files", []) if isinstance(manifest, dict) else []
    if not preview_files:
        return False

    cache_folder = os.path.dirname(manifest_path or "")
    for item in preview_files:
        if not isinstance(item, dict):
            return False
        rel_or_abs = str(item.get("path", "") or "")
        if not rel_or_abs:
            return False
        path_abs = rel_or_abs if os.path.isabs(rel_or_abs) else os.path.join(cache_folder, rel_or_abs)
        try:
            path_abs = os.path.abspath(bpy.path.abspath(path_abs))
        except Exception:
            path_abs = os.path.abspath(path_abs)
        if not os.path.isfile(path_abs):
            return False
        try:
            if os.path.getsize(path_abs) <= 0:
                return False
        except Exception:
            return False

    stats = manifest.get("texture_preview_stats", {}) if isinstance(manifest, dict) else {}
    if not isinstance(stats, dict):
        return False
    textured_candidates = int(stats.get("textured_candidates", 0) or 0)
    previewed = int(stats.get("previewed", 0) or 0)
    if textured_candidates > 0 and previewed < textured_candidates:
        return False
    return True


def _read_json_file(filepath: str):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _library_preview_mode(cache_root_abs: str) -> str:
    """Detect the preview mode used by an existing asset-library cache.

    Returns 'textured' | 'geometry' | ''. 'textured' wins over 'geometry' so
    that a partly upgraded library (e.g. incremental entries) keeps textured
    icons.     Walks the whole cache folder including _NH_incremental manifests.
    """
    if not cache_root_abs:
        return ""
    try:
        cache_root_abs = os.path.abspath(bpy.path.abspath(cache_root_abs))
    except Exception:
        cache_root_abs = os.path.abspath(cache_root_abs)
    if not os.path.isdir(cache_root_abs):
        return ""
    textured = False
    geometry = False
    try:
        manifest_paths = _iter_nh_asset_manifest_paths(cache_root_abs) or ()
    except Exception:
        manifest_paths = ()
    for manifest_path in manifest_paths:
        manifest = _read_json_file(manifest_path)
        if not isinstance(manifest, dict):
            continue
        mode = str(manifest.get("preview_mode", "") or "")
        if mode == "textured":
            textured = True
        elif mode == "geometry":
            geometry = True
    if textured:
        return "textured"
    if geometry:
        return "geometry"
    return ""


def _wanted_preview_mode_for_source_folder(folder_abs: str, settings=None) -> str:
    """'textured' when the scene setting or the existing library cache is textured."""
    setting_textured = bool(getattr(settings, "render_textured_previews", False))
    if setting_textured:
        return "textured"
    from .nh_model_split import (_nh_asset_cache_folder_for_source_folder)
    try:
        cache_folder = _nh_asset_cache_folder_for_source_folder(folder_abs, settings, create=False)
    except Exception:
        cache_folder = ""
    library_mode = _library_preview_mode(cache_folder) if cache_folder else ""
    if library_mode == "textured":
        return "textured"
    return "geometry"


def _custom_asset_manifest_path() -> str:
    from .nh_model_split import (_nh_asset_manifest_path_for_folder, _nh_objects_custom_asset_cache_root)
    return _nh_asset_manifest_path_for_folder(_nh_objects_custom_asset_cache_root(create=False))


def _custom_asset_catalog_id() -> str:
    from .nh_model_split import (_NH_OBJECTS_CUSTOM_LABEL, _NH_OBJECTS_CUSTOM_LIBRARY_NAME, _nh_asset_catalog_id)
    return _nh_asset_catalog_id(_NH_OBJECTS_CUSTOM_LIBRARY_NAME, _NH_OBJECTS_CUSTOM_LABEL)


def _write_custom_asset_catalog_file():
    from .nh_model_split import (_NH_OBJECTS_CUSTOM_LABEL, _nh_objects_custom_asset_cache_root, _write_nh_asset_catalog_file)
    return _write_nh_asset_catalog_file(
        _nh_objects_custom_asset_cache_root(create=True),
        {_NH_OBJECTS_CUSTOM_LABEL: _custom_asset_catalog_id()},
    )


def _custom_p3d_file_manifest_entry(filepath: str):
    from .nh_collider_exp import (_norm_path)
    stat = os.stat(filepath)
    return {
        "path": _norm_path(os.path.abspath(bpy.path.abspath(filepath))),
        "size": int(getattr(stat, "st_size", 0) or 0),
        "mtime_ns": int(getattr(stat, "st_mtime_ns", int(getattr(stat, "st_mtime", 0.0) * 1000000000))),
    }


def _custom_asset_manifest(p3d_files, settings=None):
    from .nh_model_split import (_NH_OBJECTS_ASSET_BLEND_NAME, _NH_OBJECTS_ASSET_MANIFEST_VERSION)
    entries = []
    for fp in sorted((p3d_files or []), key=lambda item: item.lower()):
        if not fp or not os.path.isfile(fp):
            continue
        try:
            entries.append(_custom_p3d_file_manifest_entry(fp))
        except Exception:
            continue
    preview_mode = "textured" if bool(getattr(settings, "render_textured_previews", False)) else "geometry"
    return {
        "version": _NH_OBJECTS_ASSET_MANIFEST_VERSION,
        "asset_blend": _NH_OBJECTS_ASSET_BLEND_NAME,
        "source": "custom",
        "preview_mode": preview_mode,
        "files": entries,
    }


def _read_custom_asset_p3d_paths(include_missing=False):
    from .nh_collider_exp import (_norm_path)
    manifest = _read_json_file(_custom_asset_manifest_path())
    if not isinstance(manifest, dict):
        return []

    out = []
    seen = set()
    for item in manifest.get("files", []) or []:
        if not isinstance(item, dict):
            continue
        path = item.get("path", "")
        if not path:
            continue
        try:
            path_abs = os.path.abspath(bpy.path.abspath(path))
        except Exception:
            path_abs = os.path.abspath(path)
        key = os.path.normcase(path_abs)
        if key in seen:
            continue
        if include_missing or (os.path.isfile(path_abs) and path_abs.lower().endswith(".p3d")):
            seen.add(key)
            out.append(_norm_path(path_abs))
    return out


def _write_custom_asset_manifest(cache_folder_abs: str, p3d_files, blend_path: str, asset_entries: int, settings=None):
    from .nh_model_split import (_NH_OBJECTS_ASSET_BLEND_NAME, _nh_asset_manifest_path_for_folder)
    os.makedirs(cache_folder_abs, exist_ok=True)
    manifest = _custom_asset_manifest(p3d_files, settings=settings)
    manifest["asset_blend"] = os.path.basename(blend_path or _NH_OBJECTS_ASSET_BLEND_NAME)
    manifest["asset_entries"] = int(asset_entries or 0)
    manifest_path = _nh_asset_manifest_path_for_folder(cache_folder_abs)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    return manifest_path


def _custom_asset_library_is_current(p3d_files, settings=None):
    from .nh_model_split import (_nh_asset_blend_path_for_folder, _nh_asset_manifest_path_for_folder, _nh_objects_custom_asset_cache_root)
    cache_folder_abs = _nh_objects_custom_asset_cache_root(create=False)
    blend_path = _nh_asset_blend_path_for_folder(cache_folder_abs)
    if not os.path.isfile(blend_path):
        return False
    saved_manifest = _read_json_file(_nh_asset_manifest_path_for_folder(cache_folder_abs))
    if not isinstance(saved_manifest, dict):
        return False
    current_manifest = _custom_asset_manifest(p3d_files, settings=settings)
    if int(saved_manifest.get("version", 0) or 0) != int(current_manifest.get("version", 0) or 0):
        return False
    if str(saved_manifest.get("preview_mode", "") or "") != str(current_manifest.get("preview_mode", "") or ""):
        return False
    return list(saved_manifest.get("files", []) or []) == list(current_manifest.get("files", []) or [])


def _write_persistent_asset_library_manifest(
    cache_folder_abs: str,
    source_folder_abs: str,
    p3d_files,
    blend_path: str,
    asset_entries: int,
    settings=None,
    preview_mode_override: str = "",
    preview_files=None,
    texture_preview_stats=None,
):
    from .nh_model_split import (_NH_OBJECTS_ASSET_BLEND_NAME, _nh_asset_manifest_path_for_folder)
    os.makedirs(cache_folder_abs, exist_ok=True)
    manifest = _p3d_folder_manifest(source_folder_abs, p3d_files, settings=settings, preview_mode_override=preview_mode_override)
    manifest["asset_blend"] = os.path.basename(blend_path or _NH_OBJECTS_ASSET_BLEND_NAME)
    manifest["asset_entries"] = int(asset_entries or 0)
    manifest["preview_files"] = _cache_relative_file_manifest_entries(cache_folder_abs, preview_files or [])
    if texture_preview_stats is not None:
        manifest["texture_preview_stats"] = dict(texture_preview_stats)
    manifest_path = _nh_asset_manifest_path_for_folder(cache_folder_abs)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    return manifest_path


def _persistent_asset_library_is_current(source_folder_abs: str, p3d_files, settings=None):
    from .nh_model_split import (_nh_asset_blend_path_for_folder, _nh_asset_cache_folder_for_source_folder, _nh_asset_manifest_path_for_folder)
    cache_folder_abs = _nh_asset_cache_folder_for_source_folder(source_folder_abs, settings, create=False)
    blend_path = _nh_asset_blend_path_for_folder(cache_folder_abs)
    if not os.path.isfile(blend_path):
        return False
    manifest_path = _nh_asset_manifest_path_for_folder(cache_folder_abs)
    saved_manifest = _read_json_file(manifest_path)
    if not isinstance(saved_manifest, dict):
        return False
    current_manifest = _p3d_folder_manifest(source_folder_abs, p3d_files, settings=settings)
    if int(saved_manifest.get("version", 0) or 0) != int(current_manifest.get("version", 0) or 0):
        return False
    if str(saved_manifest.get("preview_mode", "") or "") != str(current_manifest.get("preview_mode", "") or ""):
        return False
    return list(saved_manifest.get("files", []) or []) == list(current_manifest.get("files", []) or [])


def _iter_nh_asset_manifest_paths(cache_root: str):
    from .nh_model_split import (_NH_OBJECTS_ASSET_MANIFEST_NAME)
    if not cache_root or not os.path.isdir(cache_root):
        return
    for current, dirs, files in os.walk(cache_root):
        dirs[:] = [name for name in dirs if name not in {"__pycache__"}]
        if _NH_OBJECTS_ASSET_MANIFEST_NAME in files:
            yield os.path.join(current, _NH_OBJECTS_ASSET_MANIFEST_NAME)


def _manifest_asset_blend_exists(manifest_path: str, manifest: dict) -> bool:
    from .nh_model_split import (_NH_OBJECTS_ASSET_BLEND_NAME)
    blend_name = str(manifest.get("asset_blend", "") or _NH_OBJECTS_ASSET_BLEND_NAME)
    blend_path = blend_name if os.path.isabs(blend_name) else os.path.join(os.path.dirname(manifest_path), blend_name)
    return os.path.isfile(blend_path)


def _cached_p3d_keys_from_asset_manifest(manifest_path: str):
    manifest = _read_json_file(manifest_path)
    if not isinstance(manifest, dict) or not _manifest_asset_blend_exists(manifest_path, manifest):
        return set()

    source_folder = str(manifest.get("source_folder", "") or "")
    out = set()
    for item in manifest.get("files", []) or []:
        if not isinstance(item, dict):
            continue
        rel_or_abs = str(item.get("path", "") or "")
        if not rel_or_abs:
            continue
        if os.path.isabs(rel_or_abs):
            path_abs = rel_or_abs
        elif source_folder:
            path_abs = os.path.join(source_folder, rel_or_abs)
        else:
            continue
        try:
            path_abs = os.path.abspath(bpy.path.abspath(path_abs))
        except Exception:
            path_abs = os.path.abspath(path_abs)
        if os.path.isfile(path_abs) and path_abs.lower().endswith(".p3d"):
            out.add(os.path.normcase(path_abs))
    return out


def _cached_nh_objects_p3d_keys(settings=None):
    from .nh_model_split import (_iter_nh_objects_source_roots, _nh_objects_asset_cache_root)
    keys = set()
    for label, _source_root in _iter_nh_objects_source_roots(settings):
        cache_root = _nh_objects_asset_cache_root(label, create=False)
        for manifest_path in _iter_nh_asset_manifest_paths(cache_root) or ():
            keys.update(_cached_p3d_keys_from_asset_manifest(manifest_path))
    return keys


def _p3d_paths_from_asset_manifest(manifest_path: str):
    manifest = _read_json_file(manifest_path)
    if not isinstance(manifest, dict) or not _manifest_asset_blend_exists(manifest_path, manifest):
        return []

    source_folder = str(manifest.get("source_folder", "") or "")
    out = []
    seen = set()
    for item in manifest.get("files", []) or []:
        if not isinstance(item, dict):
            continue
        rel_or_abs = str(item.get("path", "") or "")
        if not rel_or_abs:
            continue
        if os.path.isabs(rel_or_abs):
            path_abs = rel_or_abs
        elif source_folder:
            path_abs = os.path.join(source_folder, rel_or_abs)
        else:
            continue
        try:
            path_abs = os.path.abspath(bpy.path.abspath(path_abs))
        except Exception:
            path_abs = os.path.abspath(path_abs)
        key = os.path.normcase(path_abs)
        if key in seen:
            continue
        if os.path.isfile(path_abs) and path_abs.lower().endswith(".p3d"):
            seen.add(key)
            out.append(path_abs)
    return out


def _incremental_assets_needing_textured_previews(settings=None, wanted_mode="", wanted_mode_for_folder=None):
    from .nh_model_split import (_NH_OBJECTS_INCREMENTAL_CACHE_FOLDER_NAME, _iter_nh_objects_source_roots, _nh_objects_asset_cache_root, _path_is_under_or_equal)
    out = []
    seen = set()
    if not wanted_mode:
        wanted_mode = "textured" if bool(getattr(settings, "render_textured_previews", False)) else "geometry"
    marker = (os.path.sep + _NH_OBJECTS_INCREMENTAL_CACHE_FOLDER_NAME + os.path.sep).lower()
    for label, source_root in _iter_nh_objects_source_roots(settings):
        cache_root = _nh_objects_asset_cache_root(label, create=False)
        for manifest_path in _iter_nh_asset_manifest_paths(cache_root) or ():
            try:
                norm_path = manifest_path.lower().replace("/", os.path.sep)
            except Exception:
                norm_path = ""
            if not norm_path or marker not in norm_path:
                continue
            manifest = _read_json_file(manifest_path)
            if not isinstance(manifest, dict):
                continue
            source_folder_for_mode = str(manifest.get("source_folder", "") or "")
            folder_mode = (
                wanted_mode_for_folder(source_folder_for_mode)
                if callable(wanted_mode_for_folder)
                else wanted_mode
            )
            if (
                str(manifest.get("preview_mode", "") or "") == folder_mode
                and _manifest_preview_files_are_ready(manifest_path, manifest)
            ):
                continue
            p3d_paths = _p3d_paths_from_asset_manifest(manifest_path)
            if len(p3d_paths) != 1:
                continue
            fp = p3d_paths[0]
            key = os.path.normcase(fp)
            if key in seen:
                continue
            seen.add(key)
            source_folder = source_folder_for_mode or os.path.dirname(fp)
            try:
                source_folder = os.path.abspath(bpy.path.abspath(source_folder))
            except Exception:
                source_folder = os.path.abspath(source_folder)
            if not _path_is_under_or_equal(source_folder, source_root):
                source_folder = os.path.dirname(fp)
            out.append((source_folder, fp, os.path.dirname(manifest_path)))
    return out


def _nh_incremental_asset_cache_folder_for_p3d(source_folder_abs: str, p3d_path: str, settings=None, create=False) -> str:
    from .nh_collider_exp import (_norm_path)
    from .nh_model_split import (_NH_OBJECTS_INCREMENTAL_CACHE_FOLDER_NAME, _nh_asset_cache_folder_for_source_folder)
    base_cache = _nh_asset_cache_folder_for_source_folder(source_folder_abs, settings, create=create)
    stem = os.path.splitext(os.path.basename(p3d_path or ""))[0] or "asset"
    safe_stem = re.sub(r'[<>:"/\\|?*]+', "_", stem).strip(" .") or "asset"
    digest = hashlib.sha1(_norm_path(os.path.abspath(bpy.path.abspath(p3d_path))).encode("utf-8", "ignore")).hexdigest()[:12]
    cache_folder = os.path.join(base_cache, _NH_OBJECTS_INCREMENTAL_CACHE_FOLDER_NAME, f"{safe_stem}_{digest}")
    if create:
        os.makedirs(cache_folder, exist_ok=True)
    return cache_folder


def _find_new_nh_objects_p3d_files(settings=None):
    from .nh_model_split import (_iter_nh_objects_asset_source_folders, _iter_p3d_files_direct)
    cached_keys = _cached_nh_objects_p3d_keys(settings)
    new_items = []
    scanned = 0
    cached = 0
    for folder_abs in _iter_nh_objects_asset_source_folders(settings):
        for fp in _iter_p3d_files_direct(folder_abs, settings):
            scanned += 1
            key = os.path.normcase(os.path.abspath(bpy.path.abspath(fp)))
            if key in cached_keys:
                cached += 1
                continue
            new_items.append((folder_abs, fp))
    return new_items, scanned, cached, len(cached_keys)


def _write_persistent_asset_library_blend(folder_abs: str, asset_root):
    from .nh_model_split import (_nh_asset_blend_path_for_folder)
    asset_objects = [
        obj for obj in list(getattr(asset_root, "objects", []) or [])
        if obj is not None and getattr(obj, "asset_data", None) is not None
    ]
    if not asset_objects:
        raise RuntimeError("No imported asset objects to write")

    os.makedirs(folder_abs, exist_ok=True)
    blend_path = _nh_asset_blend_path_for_folder(folder_abs)
    datablocks = set(asset_objects)
    try:
        bpy.data.libraries.write(blend_path, datablocks, fake_user=True, compress=True)
    except TypeError:
        bpy.data.libraries.write(blend_path, datablocks, fake_user=True)
    return blend_path, len(asset_objects)


def _imported_lod_objects(imported_objs):
    out = []
    for obj in imported_objs or []:
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        if _is_a3_lod_object(obj):
            out.append(obj)
    return out


def _imported_lod_categories(imported_objs):
    from .nh_textures import (_model_split_category_for_object)
    cats = set()
    for obj in _imported_lod_objects(imported_objs):
        try:
            cats.add(_model_split_category_for_object(obj))
        except Exception:
            continue
    return cats


def _library_lod_import_is_visual_only(lod_categories) -> bool:
    return bool(lod_categories) and lod_categories <= {"RESOLUTION"}


def _visual_lod_object_sort_key(obj):
    from .nh_textures import (_model_split_category_for_object)
    lod_num = 9999
    res_value = 1
    try:
        props = getattr(obj, "a3ob_properties_object", None)
        if props is not None:
            try:
                lod_num = int(float(str(getattr(props, "lod", "") or "")))
            except Exception:
                lod_num = 9999
            try:
                res_value = int(getattr(props, "resolution", 1) or 1)
            except Exception:
                res_value = 1
    except Exception:
        pass
    try:
        cat = _model_split_category_for_object(obj)
    except Exception:
        cat = "RESOLUTION"
    if cat != "RESOLUTION":
        lod_num = 10000 + lod_num
    return (lod_num, res_value, getattr(obj, "name", "") or "")


def _keep_best_visual_lod_objects(imported_objs):
    """Keep only a single Resolution (visual) LOD for asset icons.

    Library assets should show exactly the best Resolution LOD (e.g. (0,0)):
    every other A3 LOD (Geometry/View-Geometry/Fire Geometry/Roadway/Memory)
    and duplicate/mirrored Resolution LODs are removed from the icon content.
    """
    from .nh_textures import (_model_split_category_for_object)
    keep = []
    resolution_candidates = []
    for obj in imported_objs or []:
        if obj is None:
            continue
        if not _is_a3_lod_object(obj):
            keep.append(obj)
            continue
        try:
            cat = _model_split_category_for_object(obj)
        except Exception:
            cat = "RESOLUTION"
        if cat == "RESOLUTION":
            resolution_candidates.append(obj)
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass
    if resolution_candidates:
        try:
            best = min(resolution_candidates, key=_visual_lod_object_sort_key)
        except Exception:
            best = resolution_candidates[0]
        keep.append(best)
        for obj in resolution_candidates:
            if obj is best:
                continue
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
    return keep


def _build_persistent_asset_library_for_folder(
    context,
    folder_abs: str,
    p3d_files,
    settings,
    *,
    cache_folder_abs: str = "",
    catalog_path: str = "",
    library_label: str = "",
    cache_missing_textures: bool = False,
    render_textured_previews=None,
    manifest_writer=None,
):
    from .nh_base import (_fmt_exc)
    from .nh_model_split import (_NH_OBJECTS_ASSET_PREVIEWS_FOLDER_NAME, _iter_nh_objects_asset_roots, _nh_asset_cache_folder_for_source_folder, _nh_asset_catalog_id, _nh_asset_catalog_path_for_source_folder, _path_is_under_or_equal)
    from .nh_snap import (_P3D_IMPORT_CANDIDATES, _call_first_available, _suppress_p3d_import_tracking)
    from .nh_textures import (_postprocess_imported_material_previews)
    _clear_temp_asset_library(context)
    asset_root = _ensure_temp_asset_library_root(context)
    cache_folder_abs = cache_folder_abs or _nh_asset_cache_folder_for_source_folder(folder_abs, settings, create=True)
    os.makedirs(cache_folder_abs, exist_ok=True)
    preview_dir = os.path.join(cache_folder_abs, _NH_OBJECTS_ASSET_PREVIEWS_FOLDER_NAME)
    catalog_path = catalog_path or _nh_asset_catalog_path_for_source_folder(folder_abs, settings)
    if not library_label:
        library_label = "NH Objects"
        for name, root_abs in _iter_nh_objects_asset_roots(settings):
            if _path_is_under_or_equal(cache_folder_abs, root_abs):
                library_label = name
                break
    catalog_id = _nh_asset_catalog_id(library_label, catalog_path)
    render_textured_previews = (
        bool(getattr(settings, "render_textured_previews", False))
        if render_textured_previews is None
        else bool(render_textured_previews)
    )

    imported = 0
    moved_collections = 0
    previewed = 0
    textured_candidates = 0
    missing_texture_previews = 0
    packed_preview_images = 0
    failed = []
    preview_errors = []
    preview_paths = []
    try:
        for fp in p3d_files:
            pre_obj_ptrs = {o.as_pointer() for o in bpy.data.objects}
            pre_col_ptrs = {c.as_pointer() for c in bpy.data.collections}
            with _suppress_p3d_import_tracking():
                res, _op_id, err = _call_first_available(
                    _P3D_IMPORT_CANDIDATES,
                    filepath=fp,
                    first_lod_only=True,
                    absolute_paths=True,
                    enclose=True,
                    groupby="TYPE",
                    additional_data_allowed=True,
                    additional_data={"PROPS", "SELECTIONS", "UV", "MATERIALS"},
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
            imported_objs = [o for o in bpy.data.objects if o.as_pointer() not in pre_obj_ptrs]

            first_cats = _imported_lod_categories(imported_objs)
            if first_cats and not _library_lod_import_is_visual_only(first_cats):
                print(
                    f"=== NH Asset Library: {os.path.basename(fp)} has no visual LOD first "
                    f"(got {sorted(first_cats)}); re-importing to keep visuals ==="
                )
                for obj in imported_objs:
                    try:
                        bpy.data.objects.remove(obj, do_unlink=True)
                    except Exception:
                        pass
                for col in [c for c in bpy.data.collections if c.as_pointer() not in pre_col_ptrs]:
                    try:
                        _remove_collection_tree(col)
                    except Exception:
                        pass
                with _suppress_p3d_import_tracking():
                    res2, _op_id2, err2 = _call_first_available(
                        _P3D_IMPORT_CANDIDATES,
                        filepath=fp,
                        first_lod_only=False,
                        absolute_paths=True,
                        enclose=True,
                        groupby="TYPE",
                        additional_data_allowed=True,
                        additional_data={"PROPS", "SELECTIONS", "UV", "MATERIALS"},
                        validate_meshes=False,
                        proxy_action="SEPARATE",
                        translate_selections=False,
                        cleanup_empty_selections=False,
                        load_textures=False,
                    )
                if res2 is None:
                    failed.append(f"{os.path.basename(fp)}: full re-import failed: {_fmt_exc(err2) if err2 else 'import failed'}")
                    continue
                from .nh_textures import (_model_split_category_for_object)
                all_objs = [o for o in bpy.data.objects if o.as_pointer() not in pre_obj_ptrs]
                keep = []
                for obj in all_objs:
                    if obj is None:
                        continue
                    if _is_a3_lod_object(obj):
                        try:
                            cat = _model_split_category_for_object(obj)
                        except Exception:
                            cat = "RESOLUTION"
                        if cat == "RESOLUTION":
                            keep.append(obj)
                            continue
                        try:
                            bpy.data.objects.remove(obj, do_unlink=True)
                        except Exception:
                            pass
                        continue
                    keep.append(obj)
                imported_objs = keep
                if not imported_objs:
                    failed.append(f"{os.path.basename(fp)}: no visual LOD objects after full re-import")
                    continue

            imported_objs = _keep_best_visual_lod_objects(imported_objs)
            if not imported_objs:
                failed.append(f"{os.path.basename(fp)}: no visual LOD objects for library icon")
                continue

            if render_textured_previews:
                try:
                    preview_stats = _postprocess_imported_material_previews(
                        context,
                        imported_objs,
                        show_materials=True,
                        keep_converted_textures=True,
                        pack_runtime_images=False,
                        cache_missing_textures=bool(cache_missing_textures),
                    )
                    previewed += int(preview_stats.get("previewed", 0) or 0)
                    textured_candidates += int(preview_stats.get("textured_candidates", 0) or 0)
                    missing_texture_previews += int(preview_stats.get("missing", 0) or 0)
                    packed_preview_images += int(preview_stats.get("packed", 0) or 0)
                    for item in preview_stats.get("errors", []) or []:
                        preview_errors.append(f"{os.path.basename(fp)}: {item}")
                except Exception as e:
                    preview_errors.append(f"{os.path.basename(fp)}: {_fmt_exc(e)}")
            moved, _obj_count = _move_import_result_into_asset_library(
                context,
                fp,
                pre_obj_ptrs,
                pre_col_ptrs,
                asset_root,
                catalog_id=catalog_id,
                preview_dir=preview_dir,
                render_textured_previews=render_textured_previews,
                preview_paths_out=preview_paths,
            )
            moved_collections += moved

        if imported <= 0:
            raise RuntimeError("No .p3d files imported")

        blend_path, asset_entries = _write_persistent_asset_library_blend(cache_folder_abs, asset_root)
        if callable(manifest_writer):
            manifest_path = manifest_writer(cache_folder_abs, p3d_files, blend_path, asset_entries, settings=settings)
        else:
            preview_mode = "textured" if render_textured_previews else "geometry"
            manifest_path = _write_persistent_asset_library_manifest(
                cache_folder_abs,
                folder_abs,
                p3d_files,
                blend_path,
                asset_entries,
                settings=settings,
                preview_mode_override=preview_mode,
                preview_files=preview_paths,
                texture_preview_stats={
                    "textured_candidates": int(textured_candidates),
                    "previewed": int(previewed),
                    "missing": int(missing_texture_previews),
                    "packed": int(packed_preview_images),
                    "errors": int(len(preview_errors)),
                },
            )
        return {
            "folder": folder_abs,
            "cache_folder": cache_folder_abs,
            "blend_path": blend_path,
            "manifest_path": manifest_path,
            "imported": imported,
            "asset_entries": asset_entries,
            "moved_collections": moved_collections,
            "previewed": previewed,
            "textured_candidates": textured_candidates,
            "missing_texture_previews": missing_texture_previews,
            "packed_preview_images": packed_preview_images,
            "failed": failed,
            "preview_errors": preview_errors,
        }
    finally:
        _clear_temp_asset_library(context)


def _normalize_custom_asset_p3d_paths(p3d_files):
    from .nh_collider_exp import (_norm_path)
    from .nh_textures import (_normalize_p3d_lookup_key)
    out = []
    seen = set()
    for fp in p3d_files or []:
        try:
            fp_abs = os.path.abspath(bpy.path.abspath(fp))
        except Exception:
            fp_abs = os.path.abspath(fp or "")
        if not fp_abs or not os.path.isfile(fp_abs) or not fp_abs.lower().endswith(".p3d"):
            continue
        key = os.path.normcase(fp_abs)
        if key in seen:
            continue
        seen.add(key)
        out.append(_norm_path(fp_abs))
    out.sort(key=lambda item: _normalize_p3d_lookup_key(item))
    return out


def _clear_custom_asset_library_cache():
    from .nh_model_split import (_NH_OBJECTS_ASSET_PREVIEWS_FOLDER_NAME, _nh_asset_blend_path_for_folder, _nh_asset_manifest_path_for_folder, _nh_objects_custom_asset_cache_root)
    cache_root = _nh_objects_custom_asset_cache_root(create=True)
    removed = 0
    for path in (
        _nh_asset_blend_path_for_folder(cache_root),
        _nh_asset_manifest_path_for_folder(cache_root),
    ):
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed += 1
        except Exception:
            pass
    preview_dir = os.path.join(cache_root, _NH_OBJECTS_ASSET_PREVIEWS_FOLDER_NAME)
    try:
        if os.path.isdir(preview_dir):
            shutil.rmtree(preview_dir, ignore_errors=True)
            removed += 1
    except Exception:
        pass
    try:
        _write_custom_asset_catalog_file()
    except Exception:
        pass
    return removed


def _remove_custom_preview_cache_for_model_key(model_key: str) -> int:
    from .nh_model_split import (_NH_OBJECTS_ASSET_PREVIEWS_FOLDER_NAME, _nh_objects_custom_asset_cache_root)
    from .nh_textures import (_normalize_p3d_lookup_key)
    wanted = _normalize_p3d_lookup_key(model_key)
    if not wanted:
        return 0
    preview_dir = os.path.join(_nh_objects_custom_asset_cache_root(create=False), _NH_OBJECTS_ASSET_PREVIEWS_FOLDER_NAME)
    if not os.path.isdir(preview_dir):
        return 0
    removed = 0
    try:
        for name in os.listdir(preview_dir):
            if not name.lower().endswith(".png"):
                continue
            if _normalize_p3d_lookup_key(name) != wanted:
                continue
            try:
                os.remove(os.path.join(preview_dir, name))
                removed += 1
            except Exception:
                pass
    except Exception:
        return removed
    return removed


def _build_custom_persistent_asset_library(op, context, p3d_files, *, open_browser=True, force_rebuild=True):
    from .nh_base import (_fmt_exc)
    from .nh_model_split import (_NH_OBJECTS_CUSTOM_LABEL, _NH_OBJECTS_CUSTOM_LIBRARY_NAME, _nh_objects_custom_asset_cache_root, _nh_objects_custom_search_root)
    from .nh_snap import (_has_any_p3d_import_ops)
    settings = context.scene.cray_asset_library_settings
    p3d_files = _normalize_custom_asset_p3d_paths(p3d_files)
    if not p3d_files:
        _register_nh_objects_blender_asset_libraries()
        _clear_custom_asset_library_cache()
        if open_browser:
            _open_nh_objects_asset_browser(context, settings, preferred_library_name=_NH_OBJECTS_CUSTOM_LIBRARY_NAME)
        op.report({"INFO"}, "Custom asset library cleared")
        return {"FINISHED"}
    if not _has_any_p3d_import_ops():
        op.report({"ERROR"}, "Arma 3 Object Builder import operators not found")
        return {"CANCELLED"}

    _register_nh_objects_blender_asset_libraries()
    _write_custom_asset_catalog_file()

    if (not force_rebuild) and _custom_asset_library_is_current(p3d_files, settings):
        if open_browser:
            _open_nh_objects_asset_browser(context, settings, preferred_library_name=_NH_OBJECTS_CUSTOM_LIBRARY_NAME)
        op.report({"INFO"}, f"Custom library already up to date: {len(p3d_files)} asset(s)")
        return {"FINISHED"}

    try:
        custom_cache_folder = _nh_objects_custom_asset_cache_root(create=False)
        custom_mode = _library_preview_mode(custom_cache_folder)
        if not custom_mode:
            custom_mode = (
                "textured" if bool(getattr(settings, "render_textured_previews", False)) else "geometry"
            )
        render_textured_previews = custom_mode == "textured"
        stats = _build_persistent_asset_library_for_folder(
            context,
            _nh_objects_custom_search_root(settings),
            p3d_files,
            settings,
            cache_folder_abs=_nh_objects_custom_asset_cache_root(create=True),
            catalog_path=_NH_OBJECTS_CUSTOM_LABEL,
            library_label=_NH_OBJECTS_CUSTOM_LIBRARY_NAME,
            cache_missing_textures=render_textured_previews,
            render_textured_previews=render_textured_previews,
            manifest_writer=_write_custom_asset_manifest,
        )
    except Exception as e:
        op.report({"ERROR"}, f"Could not build Custom library: {_fmt_exc(e)}")
        return {"CANCELLED"}

    failed = list(stats.get("failed", []) or [])
    preview_errors = list(stats.get("preview_errors", []) or [])
    if failed:
        print("=== NH Objects Custom Asset Library: Failures ===")
        for item in failed:
            print(item)
    if preview_errors:
        print("=== NH Objects Custom Asset Library: Material preview warnings ===")
        for item in preview_errors:
            print(item)

    if open_browser:
        _open_nh_objects_asset_browser(context, settings, preferred_library_name=_NH_OBJECTS_CUSTOM_LIBRARY_NAME)

    msg = (
        f"Custom library ready: imported {int(stats.get('imported', 0) or 0)}, "
        f"assets {int(stats.get('asset_entries', 0) or 0)}"
    )
    textured_candidates = int(stats.get("textured_candidates", 0) or 0)
    if textured_candidates > 0:
        msg += f", texture previews {int(stats.get('previewed', 0) or 0)}/{textured_candidates}"
    if failed:
        op.report({"WARNING"}, msg + f", failed {len(failed)} (see System Console)")
    elif preview_errors:
        op.report({"WARNING"}, msg + f", preview warnings {len(preview_errors)} (see System Console)")
    else:
        op.report({"INFO"}, msg)
    return {"FINISHED"}


def _find_custom_asset_source_by_name(settings, model_name: str):
    from .nh_model_split import (_nh_objects_common_root, _nh_objects_custom_search_root, _nh_objects_environment_root)
    from .nh_textures import (_find_p3d_paths_by_name, _normalize_p3d_lookup_key)
    model_key = _normalize_p3d_lookup_key(model_name)
    if not model_key:
        return "", []

    search_root = _nh_objects_custom_search_root(settings)
    matches = _find_p3d_paths_by_name(search_root, model_key, settings=settings, respect_ignore=False)
    if matches:
        return matches[0], matches

    extra_roots = (_nh_objects_common_root(settings), _nh_objects_environment_root(settings))
    all_matches = []
    seen = set()
    for root_abs in extra_roots:
        for fp in _find_p3d_paths_by_name(root_abs, model_key, settings=settings, respect_ignore=False):
            key = os.path.normcase(fp)
            if key in seen:
                continue
            seen.add(key)
            all_matches.append(fp)
    all_matches.sort(key=lambda item: (len(item), item.lower()))
    return (all_matches[0], all_matches) if all_matches else ("", [])


def _clear_nh_objects_asset_library_cache_roots(settings=None):
    from .nh_base import (_fmt_exc)
    from .nh_model_split import (_iter_nh_objects_source_roots, _nh_objects_asset_cache_base, _nh_objects_asset_cache_root, _path_is_under_or_equal)
    cache_base = os.path.abspath(_nh_objects_asset_cache_base(create=True))
    removed = []
    failed = []
    for label, _source_root in _iter_nh_objects_source_roots(settings):
        cache_root = os.path.abspath(_nh_objects_asset_cache_root(label, create=False))
        if not os.path.isdir(cache_root):
            continue
        if not _path_is_under_or_equal(cache_root, cache_base):
            failed.append(f"{label}: refused to delete outside NH cache: {cache_root}")
            continue
        try:
            shutil.rmtree(cache_root)
            removed.append(cache_root)
        except Exception as e:
            failed.append(f"{label}: {cache_root}: {_fmt_exc(e)}")
    return removed, failed


def _build_nh_objects_persistent_asset_libraries(op, context, cache_missing_textures: bool = False):
    from .nh_base import (_fmt_exc)
    from .nh_model_split import (_NH_OBJECTS_CUSTOM_LABEL, _NH_OBJECTS_CUSTOM_LIBRARY_NAME, _iter_nh_objects_asset_source_folders, _iter_p3d_files_direct, _nh_asset_catalog_paths_by_cache_root, _nh_objects_common_root, _nh_objects_custom_asset_cache_root, _nh_objects_custom_search_root, _nh_objects_environment_root, _write_nh_asset_catalog_file)
    from .nh_snap import (_has_any_p3d_import_ops)
    settings = context.scene.cray_asset_library_settings
    if not _has_any_p3d_import_ops():
        op.report({"ERROR"}, "Arma 3 Object Builder import operators not found")
        return {"CANCELLED"}

    registered, missing_roots = _register_nh_objects_blender_asset_libraries()
    configured_roots = [
        ("Common", _nh_objects_common_root(settings)),
        ("Environment", _nh_objects_environment_root(settings)),
    ]
    missing_configured = [f"{label}: {path}" for label, path in configured_roots if not os.path.isdir(path)]
    if missing_configured:
        op.report({"ERROR"}, "Set valid Common and Environment folders")
        print("=== NH Objects Asset Libraries: Missing configured roots ===")
        for item in missing_configured:
            print(item)
        return {"CANCELLED"}

    folders = list(_iter_nh_objects_asset_source_folders(settings))
    if not folders:
        op.report({"ERROR"}, "No .p3d folders found in NH_Objects Common/Environment")
        return {"CANCELLED"}
    for cache_root, catalog_paths in _nh_asset_catalog_paths_by_cache_root(folders, settings).items():
        try:
            _write_nh_asset_catalog_file(cache_root, catalog_paths)
        except Exception as e:
            print(f"NH Objects Asset Catalogs: {cache_root}: {_fmt_exc(e)}")

    built = 0
    skipped_current = 0
    imported_total = 0
    asset_entries_total = 0
    previewed_total = 0
    textured_candidates_total = 0
    packed_preview_images_total = 0
    failed = []
    preview_errors = []
    rebuild = bool(getattr(settings, "rebuild_existing_libraries", False))
    prev_import_first_lod_only = bool(getattr(settings, "import_first_lod_only", True))
    try:
        settings.import_first_lod_only = True
    except Exception:
        pass

    for folder_abs in folders:
        p3d_files = _iter_p3d_files_direct(folder_abs, settings)
        if not p3d_files:
            continue
        if not rebuild and _persistent_asset_library_is_current(folder_abs, p3d_files, settings):
            skipped_current += 1
            continue
        try:
            stats = _build_persistent_asset_library_for_folder(
                context,
                folder_abs,
                p3d_files,
                settings,
                cache_missing_textures=cache_missing_textures,
            )
        except Exception as e:
            failed.append(f"{folder_abs}: {_fmt_exc(e)}")
            continue
        built += 1
        imported_total += int(stats.get("imported", 0))
        asset_entries_total += int(stats.get("asset_entries", 0))
        previewed_total += int(stats.get("previewed", 0))
        textured_candidates_total += int(stats.get("textured_candidates", 0))
        packed_preview_images_total += int(stats.get("packed_preview_images", 0))
        for item in stats.get("failed", []) or []:
            failed.append(f"{folder_abs}: {item}")
        for item in stats.get("preview_errors", []) or []:
            preview_errors.append(f"{folder_abs}: {item}")

    custom_files = _read_custom_asset_p3d_paths()
    if custom_files:
        try:
            _write_custom_asset_catalog_file()
        except Exception as e:
            print(f"NH Objects Custom Asset Catalog: {_fmt_exc(e)}")
        if not rebuild and _custom_asset_library_is_current(custom_files, settings):
            skipped_current += 1
        else:
            try:
                stats = _build_persistent_asset_library_for_folder(
                    context,
                    _nh_objects_custom_search_root(settings),
                    custom_files,
                    settings,
                    cache_folder_abs=_nh_objects_custom_asset_cache_root(create=True),
                    catalog_path=_NH_OBJECTS_CUSTOM_LABEL,
                    library_label=_NH_OBJECTS_CUSTOM_LIBRARY_NAME,
                    cache_missing_textures=cache_missing_textures,
                    render_textured_previews=bool(getattr(settings, "render_textured_previews", False)),
                    manifest_writer=_write_custom_asset_manifest,
                )
            except Exception as e:
                failed.append(f"{_NH_OBJECTS_CUSTOM_LIBRARY_NAME}: {_fmt_exc(e)}")
            else:
                built += 1
                imported_total += int(stats.get("imported", 0))
                asset_entries_total += int(stats.get("asset_entries", 0))
                previewed_total += int(stats.get("previewed", 0))
                textured_candidates_total += int(stats.get("textured_candidates", 0))
                packed_preview_images_total += int(stats.get("packed_preview_images", 0))
                for item in stats.get("failed", []) or []:
                    failed.append(f"{_NH_OBJECTS_CUSTOM_LIBRARY_NAME}: {item}")
                for item in stats.get("preview_errors", []) or []:
                    preview_errors.append(f"{_NH_OBJECTS_CUSTOM_LIBRARY_NAME}: {item}")

    if failed:
        print("=== NH Objects Persistent Asset Libraries: Failures ===")
        for item in failed:
            print(item)
    if preview_errors:
        print("=== NH Objects Persistent Asset Libraries: Material preview warnings ===")
        for item in preview_errors:
            print(item)
    if missing_roots:
        print("=== NH Objects Asset Libraries: Missing roots ===")
        for item in missing_roots:
            print(item)

    if built == 0 and skipped_current == 0:
        try:
            settings.import_first_lod_only = prev_import_first_lod_only
        except Exception:
            pass
        op.report({"ERROR"}, "No NH asset libraries built (see System Console)")
        return {"CANCELLED"}

    _open_nh_objects_asset_browser(context, settings)

    msg = (
        f"NH libraries ready: registered {registered}, built {built}, "
        f"skipped up-to-date {skipped_current}, imported {imported_total}, assets {asset_entries_total}"
    )
    if textured_candidates_total > 0:
        msg += f", texture previews {previewed_total}/{textured_candidates_total}"
    if packed_preview_images_total > 0:
        msg += f", packed {packed_preview_images_total}"
    if failed:
        op.report({"WARNING"}, msg + f", failed {len(failed)} (see System Console)")
    elif preview_errors:
        op.report({"WARNING"}, msg + f", preview warnings {len(preview_errors)} (see System Console)")
    else:
        op.report({"INFO"}, msg)
    try:
        settings.import_first_lod_only = prev_import_first_lod_only
    except Exception:
        pass
    return {"FINISHED"}


def _add_new_nh_objects_assets_to_cache(op, context):
    from .nh_base import (_fmt_exc)
    from .nh_model_split import (_iter_nh_objects_asset_source_folders, _nh_asset_catalog_paths_by_cache_root, _nh_objects_common_root, _nh_objects_environment_root, _write_nh_asset_catalog_file)
    from .nh_snap import (_has_any_p3d_import_ops)
    settings = context.scene.cray_asset_library_settings
    if not _has_any_p3d_import_ops():
        op.report({"ERROR"}, "Arma 3 Object Builder import operators not found")
        return {"CANCELLED"}

    registered, missing_roots = _register_nh_objects_blender_asset_libraries()
    configured_roots = [
        ("Common", _nh_objects_common_root(settings)),
        ("Environment", _nh_objects_environment_root(settings)),
    ]
    missing_configured = [f"{label}: {path}" for label, path in configured_roots if not os.path.isdir(path)]
    if missing_configured:
        op.report({"ERROR"}, "Set valid Common and Environment folders")
        print("=== NH Objects Add New Assets: Missing configured roots ===")
        for item in missing_configured:
            print(item)
        return {"CANCELLED"}

    folders = list(_iter_nh_objects_asset_source_folders(settings))
    if not folders:
        op.report({"ERROR"}, "No .p3d folders found in NH_Objects Common/Environment")
        return {"CANCELLED"}
    for cache_root, catalog_paths in _nh_asset_catalog_paths_by_cache_root(folders, settings).items():
        try:
            _write_nh_asset_catalog_file(cache_root, catalog_paths)
        except Exception as e:
            print(f"NH Objects Asset Catalogs: {cache_root}: {_fmt_exc(e)}")

    new_items, scanned, cached_existing, cached_total = _find_new_nh_objects_p3d_files(settings)

    mode_cache = {}

    def _wanted_mode_for_folder(folder_abs):
        key = os.path.normcase(os.path.abspath(folder_abs or "") or "")
        if key not in mode_cache:
            mode_cache[key] = _wanted_preview_mode_for_source_folder(folder_abs, settings)
        return mode_cache[key]

    icon_update_items = _incremental_assets_needing_textured_previews(
        settings,
        wanted_mode_for_folder=_wanted_mode_for_folder,
    )
    mode_counts = {"textured": 0, "geometry": 0}
    for folder_abs, _fp in new_items:
        mode = _wanted_mode_for_folder(folder_abs)
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
    if mode_counts.get("textured"):
        print(
            "=== NH Objects Add New Assets: preview mode textured "
            f"(new items {mode_counts.get('textured')}; geometry {mode_counts.get('geometry')}) ==="
        )
    if scanned > 0 and cached_total <= 0:
        op.report({"ERROR"}, "No existing NH asset cache manifests found. Run Build NH Libraries once, then use Add New.")
        return {"CANCELLED"}
    if not new_items and not icon_update_items:
        _open_nh_objects_asset_browser(context, settings)
        op.report({"INFO"}, f"No new or outdated NH .p3d assets found: scanned {scanned}, cached {cached_existing}")
        return {"FINISHED"}

    prev_import_first_lod_only = bool(getattr(settings, "import_first_lod_only", True))
    try:
        settings.import_first_lod_only = True
    except Exception:
        pass

    built = 0
    imported_total = 0
    asset_entries_total = 0
    previewed_total = 0
    textured_candidates_total = 0
    packed_preview_images_total = 0
    failed = []
    preview_errors = []
    build_items = [
        (folder_abs, fp, _nh_incremental_asset_cache_folder_for_p3d(folder_abs, fp, settings, create=True), "new")
        for folder_abs, fp in new_items
    ]
    build_items.extend(
        (folder_abs, fp, cache_folder_abs, "icon")
        for folder_abs, fp, cache_folder_abs in icon_update_items
    )

    icon_updates = 0
    for folder_abs, fp, cache_folder_abs, item_kind in build_items:
        render_textured_previews = _wanted_mode_for_folder(folder_abs) == "textured"
        try:
            os.makedirs(cache_folder_abs, exist_ok=True)
            stats = _build_persistent_asset_library_for_folder(
                context,
                folder_abs,
                [fp],
                settings,
                cache_folder_abs=cache_folder_abs,
                cache_missing_textures=render_textured_previews,
                render_textured_previews=render_textured_previews,
            )
        except Exception as e:
            failed.append(f"{fp}: {_fmt_exc(e)}")
            continue
        built += 1
        if item_kind == "icon":
            icon_updates += 1
        imported_total += int(stats.get("imported", 0))
        asset_entries_total += int(stats.get("asset_entries", 0))
        previewed_total += int(stats.get("previewed", 0))
        textured_candidates_total += int(stats.get("textured_candidates", 0))
        packed_preview_images_total += int(stats.get("packed_preview_images", 0))
        for item in stats.get("failed", []) or []:
            failed.append(f"{fp}: {item}")
        for item in stats.get("preview_errors", []) or []:
            preview_errors.append(f"{fp}: {item}")

    if failed:
        print("=== NH Objects Add New Assets: Failures ===")
        for item in failed:
            print(item)
    if preview_errors:
        print("=== NH Objects Add New Assets: Material preview warnings ===")
        for item in preview_errors:
            print(item)
    if missing_roots:
        print("=== NH Objects Asset Libraries: Missing roots ===")
        for item in missing_roots:
            print(item)

    if built <= 0:
        try:
            settings.import_first_lod_only = prev_import_first_lod_only
        except Exception:
            pass
        op.report({"ERROR"}, f"No NH assets updated; found {len(new_items)} new and {len(icon_update_items)} icon candidate(s), failed {len(failed)}")
        return {"CANCELLED"}

    _open_nh_objects_asset_browser(context, settings)
    msg = (
        f"Added new NH assets: scanned {scanned}, already cached {cached_existing}, "
        f"new {len(new_items)}, updated {built}, imported {imported_total}, assets {asset_entries_total}"
    )
    if icon_updates:
        msg += f", icon updates {icon_updates}"
    if registered:
        msg += f", registered {registered}"
    if textured_candidates_total > 0:
        msg += f", texture previews {previewed_total}/{textured_candidates_total}"
    if packed_preview_images_total > 0:
        msg += f", packed {packed_preview_images_total}"
    if failed:
        op.report({"WARNING"}, msg + f", failed {len(failed)} (see System Console)")
    elif preview_errors:
        op.report({"WARNING"}, msg + f", preview warnings {len(preview_errors)} (see System Console)")
    else:
        op.report({"INFO"}, msg)
    try:
        settings.import_first_lod_only = prev_import_first_lod_only
    except Exception:
        pass
    return {"FINISHED"}


class CRAY_OT_AssetLibraryBuildNHObjects(Operator):
    bl_idname = "cray.asset_library_build_nh_objects"
    bl_label = "Build NH Libraries"
    bl_description = (
        "РЎРѕР·РґР°РµС‚ РёР»Рё РѕР±РЅРѕРІР»СЏРµС‚ Blender asset libraries РёР· РІС‹Р±СЂР°РЅРЅС‹С… РїР°РїРѕРє Common Рё Environment, "
        "РІРєР»СЋС‡Р°СЏ РїРѕРґРїР°РїРєРё Рё РёСЃРєР»СЋС‡Р°СЏ Common\\Buildings"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _build_nh_objects_persistent_asset_libraries(self, context)


class CRAY_OT_AssetLibraryAddNewNHObjects(Operator):
    bl_idname = "cray.asset_library_add_new_nh_objects"
    bl_label = "Add New NH Assets"
    bl_description = "Scan Common/Environment and cache only .p3d files that are not already present in NH asset library manifests"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _add_new_nh_objects_assets_to_cache(self, context)


class CRAY_OT_AssetLibraryOpenNHBrowser(Operator):
    bl_idname = "cray.asset_library_open_nh_browser"
    bl_label = "Open NH Asset Browser"
    bl_description = "Open the Asset Browser and show the registered NH Objects asset libraries"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = context.scene.cray_asset_library_settings
        _register_nh_objects_blender_asset_libraries()
        area = _open_nh_objects_asset_browser(context, settings)
        if area is None:
            self.report({"WARNING"}, "Open an Asset Browser and select an NH Objects library")
        else:
            self.report({"INFO"}, "NH Asset Browser opened")
        return {"FINISHED"}


class CRAY_OT_AssetLibraryAddCustomByName(Operator):
    bl_idname = "cray.asset_library_add_custom_by_name"
    bl_label = "Add Custom Asset"
    bl_description = "Find a .p3d by name and add it to NH Objects - Custom"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_model_split import (_nh_objects_custom_search_root)
        from .nh_textures import (_display_p3d_name, _normalize_p3d_lookup_key)
        settings = context.scene.cray_asset_library_settings
        model_name = (getattr(settings, "custom_p3d_name", "") or "").strip()
        model_key = _normalize_p3d_lookup_key(model_name)
        if not model_key:
            self.report({"ERROR"}, "Type a .p3d name like pripyat_shoppingMall_sign")
            return {"CANCELLED"}

        search_root = _nh_objects_custom_search_root(settings)
        if not search_root or not os.path.isdir(search_root):
            self.report({"ERROR"}, "Custom Search Root was not found")
            return {"CANCELLED"}

        chosen, matches = _find_custom_asset_source_by_name(settings, model_key)
        if not chosen:
            self.report({"ERROR"}, f"{_display_p3d_name(model_key)} was not found in {search_root}")
            return {"CANCELLED"}

        current = _read_custom_asset_p3d_paths()
        current = [
            fp for fp in current
            if _normalize_p3d_lookup_key(fp) != model_key
        ]
        current.append(chosen)
        settings.custom_p3d_name = model_key

        if len(matches) > 1:
            print("=== Custom Asset Library: multiple .p3d matches found ===")
            print(f"Requested: {model_key}")
            for path in matches:
                print(path)

        result = _build_custom_persistent_asset_library(self, context, current, open_browser=True, force_rebuild=True)
        if "FINISHED" in result and len(matches) > 1:
            self.report(
                {"WARNING"},
                (
                    f"Added {os.path.basename(chosen)} to Custom; found {len(matches)} matches, "
                    f"used the first one (see System Console)"
                ),
            )
        return result


class CRAY_OT_AssetLibraryRemoveCustomByName(Operator):
    bl_idname = "cray.asset_library_remove_custom_by_name"
    bl_label = "Remove Custom Asset"
    bl_description = "Remove the named .p3d from NH Objects - Custom"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_textures import (_display_p3d_name, _normalize_p3d_lookup_key)
        settings = context.scene.cray_asset_library_settings
        model_name = (getattr(settings, "custom_p3d_name", "") or "").strip()
        model_key = _normalize_p3d_lookup_key(model_name)
        if not model_key:
            self.report({"ERROR"}, "Type the custom .p3d name to remove")
            return {"CANCELLED"}

        current = _read_custom_asset_p3d_paths()
        remaining = [
            fp for fp in current
            if _normalize_p3d_lookup_key(fp) != model_key
        ]
        removed = len(current) - len(remaining)
        if removed <= 0:
            self.report({"WARNING"}, f"{_display_p3d_name(model_key)} is not in Custom")
            return {"CANCELLED"}

        settings.custom_p3d_name = model_key
        _remove_custom_preview_cache_for_model_key(model_key)
        result = _build_custom_persistent_asset_library(self, context, remaining, open_browser=True, force_rebuild=True)
        if "FINISHED" in result:
            self.report({"INFO"}, f"Removed {removed} custom asset(s)")
        return result


class CRAY_OT_AssetLibraryClearCustom(Operator):
    bl_idname = "cray.asset_library_clear_custom"
    bl_label = "Clear Custom Assets"
    bl_description = "Clear NH Objects - Custom cached assets and previews"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_model_split import (_NH_OBJECTS_CUSTOM_LIBRARY_NAME)
        _register_nh_objects_blender_asset_libraries()
        removed = _clear_custom_asset_library_cache()
        _open_nh_objects_asset_browser(context, context.scene.cray_asset_library_settings, preferred_library_name=_NH_OBJECTS_CUSTOM_LIBRARY_NAME)
        self.report({"INFO"}, f"Cleared Custom asset library ({removed} cache item(s))")
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


from .nh_base import (_ASSET_CATALOG_FALLBACK_ID, _ASSET_CATALOG_NAME)

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


def _switch_bottom_area_to_asset_browser(context, asset_library_reference="LOCAL"):
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
                library_ref = str(asset_library_reference or "LOCAL")
                library_refs = [library_ref]
                if library_ref not in {"LOCAL", "ESSENTIALS", "ALL"}:
                    library_refs.extend(["ALL", "LOCAL"])

                for attr_name, attr_values in (
                    ("asset_library_reference", library_refs),
                    ("asset_library_ref", library_refs),
                    ("catalog_id", [""]),
                    ("import_method", ["APPEND_REUSE"]),
                ):
                    try:
                        for attr_value in attr_values:
                            try:
                                setattr(params, attr_name, attr_value)
                                break
                            except Exception:
                                continue
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


def _first_nh_objects_asset_library_reference(settings=None, preferred_library_name: str = ""):
    from .nh_model_split import (_iter_nh_objects_asset_roots)
    preferred = str(preferred_library_name or "").strip()
    if preferred:
        for name, root_abs in _iter_nh_objects_asset_roots(settings):
            if name != preferred:
                continue
            lib, _idx = _find_registered_asset_library_by_path(root_abs)
            if lib is not None:
                try:
                    return str(getattr(lib, "name", "") or name)
                except Exception:
                    return name
            return name
    for name, root_abs in _iter_nh_objects_asset_roots(settings):
        lib, _idx = _find_registered_asset_library_by_path(root_abs)
        if lib is not None:
            try:
                return str(getattr(lib, "name", "") or name)
            except Exception:
                return name
    for name, _root_abs in _iter_nh_objects_asset_roots(settings):
        return name
    return "ALL"


def _open_nh_objects_asset_browser(context, settings=None, preferred_library_name: str = ""):
    library_ref = _first_nh_objects_asset_library_reference(settings, preferred_library_name=preferred_library_name)
    return _switch_bottom_area_to_asset_browser(context, asset_library_reference=library_ref)


def _build_temp_asset_library_from_paths(op, context, filepaths):
    from .nh_base import (_ASSET_CATALOG_NAME, _fmt_exc)
    from .nh_snap import (_P3D_IMPORT_CANDIDATES, _call_first_available, _suppress_p3d_import_tracking)
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
        with _suppress_p3d_import_tracking():
            res, op_id, err = _call_first_available(
                _P3D_IMPORT_CANDIDATES,
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
        from .nh_base import (_fmt_exc)
        from .nh_scatter import (_new_proxy_triangle_mesh, set_p3d_proxy_properties)
        from .nh_textures import (_build_proxy_from_object_instance, _model_split_is_p3d_lod_object, _model_split_target_category_label, _next_proxy_index_for_parent, _obj_depth, _pick_proxy_target_object, _pick_proxy_target_root_collection, _proxy_conversion_target_lods, _proxy_conversion_target_lods_for_categories, _proxy_explicit_source_map, _proxy_selected_asset_source_map, _proxy_selected_target_category_tokens, _proxy_target_collection_for_lod, _resolve_proxy_asset_source_p3d)
        st = context.scene.cray_asset_proxy_settings
        explicit_source = getattr(st, "source_object", None)
        explicit_target_collection = getattr(st, "target_collection", None)
        provisional_target_root = _pick_proxy_target_root_collection(
            context,
            explicit_collection=explicit_target_collection,
            target_obj=getattr(st, "target_object", None),
        )
        if explicit_source is not None:
            source_map, source_error = _proxy_explicit_source_map(context, explicit_source)
            if source_error:
                self.report({"ERROR"}, source_error)
                return {"CANCELLED"}
        else:
            source_map = _proxy_selected_asset_source_map(context, excluded_root=provisional_target_root)

        target_obj = _pick_proxy_target_object(context, st.target_object, source_map.keys())
        target_root = _pick_proxy_target_root_collection(
            context,
            explicit_collection=explicit_target_collection,
            target_obj=target_obj,
            source_objs=source_map.keys(),
        )
        if target_root is not None and explicit_source is None:
            source_map = _proxy_selected_asset_source_map(context, excluded_root=target_root)
            target_obj = _pick_proxy_target_object(context, st.target_object, source_map.keys())
            target_root = _pick_proxy_target_root_collection(
                context,
                explicit_collection=explicit_target_collection,
                target_obj=target_obj,
                source_objs=source_map.keys(),
            ) or target_root
        if target_obj is not None:
            try:
                st.target_object = target_obj
            except Exception:
                pass
        if target_root is not None:
            try:
                st.target_collection = target_root
            except Exception:
                pass
        if target_obj is None and target_root is None:
            self.report({"ERROR"}, "Pick Target P3D Collection, Target LOD mesh, or select an asset object parented under a LOD")
            return {"CANCELLED"}
        if target_obj is not None and not _model_split_is_p3d_lod_object(target_obj):
            self.report({"ERROR"}, "Target must be an P3D LOD mesh, for example Resolution 0")
            return {"CANCELLED"}

        category_tokens = _proxy_selected_target_category_tokens(st, target_obj=target_obj)
        target_lods = _proxy_conversion_target_lods_for_categories(context, target_root, target_obj, category_tokens)
        if not target_lods and target_obj is not None:
            target_lods = _proxy_conversion_target_lods(
                context,
                target_obj,
                bool(getattr(st, "duplicate_to_all_resolution_lods", False)),
            )
        if not target_lods:
            self.report({"ERROR"}, "Could not find or create target LOD object(s) in the selected target collection")
            return {"CANCELLED"}
        if target_obj is None:
            target_obj = target_lods[0]
            try:
                st.target_object = target_obj
            except Exception:
                pass

        if explicit_source is not None:
            selected = [explicit_source] if explicit_source != target_obj else []
        else:
            selected = []
            for obj in getattr(context, "selected_objects", []) or []:
                if obj not in target_lods and obj in source_map and obj not in selected:
                    selected.append(obj)
            for obj in source_map.keys():
                if obj not in target_lods and obj not in selected:
                    selected.append(obj)
        if not selected:
            self.report({"ERROR"}, "Pick Proxy Source Object or select placed asset object(s)")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as e:
                self.report({"ERROR"}, f"Failed to switch to Object Mode: {_fmt_exc(e)}")
                return {"CANCELLED"}

        target_collection = None
        if target_obj is not None and target_obj.users_collection:
            target_collection = target_obj.users_collection[0]
        elif selected and selected[0].users_collection:
            target_collection = selected[0].users_collection[0]
        else:
            target_collection = context.scene.collection

        created = 0
        removed = 0
        target_count = len(target_lods)
        skipped = []
        next_indices = {lod_obj: _next_proxy_index_for_parent(lod_obj) for lod_obj in target_lods}
        to_remove = []

        for obj in selected:
            src = source_map.get(obj) or _resolve_proxy_asset_source_p3d(obj, context=context)
            if not src:
                skipped.append(f"{obj.name}: no source .p3d path")
                continue

            created_for_source = 0
            for lod_obj in target_lods:
                proxy_index = int(next_indices.get(lod_obj, 1) or 1)
                proxy_mesh = _new_proxy_triangle_mesh(f"proxy_{obj.name}_mesh")
                proxy_obj = bpy.data.objects.new("proxy", proxy_mesh)
                proxy_collection = _proxy_target_collection_for_lod(lod_obj, target_collection)
                try:
                    proxy_collection.objects.link(proxy_obj)
                except Exception:
                    context.scene.collection.objects.link(proxy_obj)
                _build_proxy_from_object_instance(proxy_obj, obj, lod_obj, proxy_index, model_path=src)
                try:
                    set_p3d_proxy_properties(proxy_obj, src, proxy_index)
                except Exception as e:
                    try:
                        bpy.data.objects.remove(proxy_obj, do_unlink=True)
                    except Exception:
                        pass
                    skipped.append(f"{obj.name} -> {lod_obj.name}: {_fmt_exc(e)}")
                    continue

                created += 1
                created_for_source += 1
                next_indices[lod_obj] = proxy_index + 1

            if created_for_source > 0:
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

        if explicit_source is not None:
            try:
                if bpy.data.objects.get(explicit_source.name) is None:
                    st.source_object = None
            except Exception:
                pass

        msg = f"Created {created} proxy(s)"
        if target_count > 1:
            category_labels = ", ".join(
                _model_split_target_category_label(token)
                for token in category_tokens
            )
            msg += f" across {target_count} target LOD(s)"
            if category_labels:
                msg += f" ({category_labels})"
        else:
            msg += f" under '{target_obj.name}'"
        msg += f", removed {removed} original(s)"
        if skipped:
            msg += f", skipped {len(skipped)} (see System Console)"
            self.report({"WARNING"}, msg)
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


def _repair_invalid_p3d_selection_links(obj):
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


_FIX_LIST_LOD_KEY_ALIASES = {
    "geometry": "geometry",
    "viewgeometry": "geometryview",
    "geometryview": "geometryview",
    "firegeometry": "geometryfire",
    "geometryfire": "geometryfire",
}
_FIX_LIST_OBJECT_LOD_MAP = {
    "6": "geometry",
    "14": "geometryview",
    "15": "geometryfire",
}


def _normalize_fix_list_model_name(name: str) -> str:
    from .nh_textures import (_strip_blender_numeric_suffix)
    raw = (name or "").strip()
    if not raw:
        return ""
    raw = os.path.basename(raw.replace("\\", "/"))
    raw = _strip_blender_numeric_suffix(raw)
    return raw.lower()


def _normalize_fix_list_lod_key(name: str) -> str:
    key = re.sub(r"[^a-z]+", "", (name or "").strip().lower())
    return _FIX_LIST_LOD_KEY_ALIASES.get(key, "")


def _parse_fix_list_file(filepath: str):
    entries = {}
    current_model_key = ""

    with open(filepath, "r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue

            if re.search(r"\.p3d\s*$", line, flags=re.IGNORECASE):
                display_name = os.path.basename(line.replace("\\", "/")).strip()
                model_key = _normalize_fix_list_model_name(display_name or line)
                if not model_key:
                    current_model_key = ""
                    continue
                current_model_key = model_key
                rec = entries.setdefault(
                    current_model_key,
                    {
                        "display_name": display_name or model_key,
                        "lod_groups": {},
                    },
                )
                if display_name:
                    rec["display_name"] = display_name
                continue

            if not current_model_key or ":" not in line:
                continue

            lod_raw, groups_raw = line.split(":", 1)
            lod_key = _normalize_fix_list_lod_key(lod_raw)
            if not lod_key:
                continue

            group_names = []
            for item in groups_raw.split(","):
                token = item.strip().rstrip(";").strip()
                if token:
                    group_names.append(token)

            if not group_names:
                continue

            lod_groups = entries[current_model_key]["lod_groups"].setdefault(lod_key, set())
            lod_groups.update(group_names)

    return entries


def _detect_fix_list_lod_key(obj) -> str:
    from .nh_textures import (_strip_blender_numeric_suffix)
    if obj is None or obj.type != "MESH":
        return ""

    if hasattr(obj, "a3ob_properties_object"):
        try:
            lod_token = str(getattr(obj.a3ob_properties_object, "lod", "") or "").strip()
        except Exception:
            lod_token = ""
        if lod_token in _FIX_LIST_OBJECT_LOD_MAP:
            return _FIX_LIST_OBJECT_LOD_MAP[lod_token]

    return _normalize_fix_list_lod_key(_strip_blender_numeric_suffix(getattr(obj, "name", "") or ""))


def _select_fix_list_groups_on_active_lod(context, obj, group_names):
    from .nh_collider import (_activate_object_vertex_edit)
    from .nh_snap import (_tag_redraw_all_areas)
    if obj is None or obj.type != "MESH" or obj.data is None:
        raise RuntimeError("Active object must be a mesh")

    group_lookup = {
        (vg.name or "").strip().lower(): vg
        for vg in obj.vertex_groups
        if (vg.name or "").strip()
    }

    matched_groups = []
    missing_groups = []
    target_group_indices = set()
    for group_name in group_names:
        key = (group_name or "").strip().lower()
        vg = group_lookup.get(key)
        if vg is None:
            missing_groups.append(group_name)
            continue
        matched_groups.append(vg.name)
        target_group_indices.add(int(vg.index))

    if not target_group_indices:
        return {
            "matched_groups": [],
            "missing_groups": missing_groups,
            "selected_vert_count": 0,
        }

    _activate_object_vertex_edit(context, obj)
    context.tool_settings.mesh_select_mode = (True, False, False)

    try:
        bpy.ops.mesh.reveal(select=False)
    except Exception:
        pass
    try:
        bpy.ops.mesh.select_all(action="DESELECT")
    except Exception:
        pass

    used_vertex_group_ops = False
    lower_lookup = {
        (vg.name or "").strip().lower(): vg
        for vg in obj.vertex_groups
        if (vg.name or "").strip()
    }
    for group_name in matched_groups:
        vg = lower_lookup.get((group_name or "").strip().lower())
        if vg is None:
            continue
        try:
            obj.vertex_groups.active_index = int(vg.index)
            bpy.ops.object.vertex_group_select()
            used_vertex_group_ops = True
        except Exception:
            used_vertex_group_ops = False
            break

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    layer = bm.verts.layers.deform.verify()

    selected_vert_count = sum(1 for vert in bm.verts if vert.select)
    if not used_vertex_group_ops or selected_vert_count == 0:
        selected_vert_count = 0
        for vert in bm.verts:
            deform = vert[layer]
            is_selected = any(float(deform.get(group_index, 0.0)) > 0.0 for group_index in target_group_indices)
            vert.select = is_selected
            if is_selected:
                selected_vert_count += 1

        for edge in bm.edges:
            edge.select = False
        for face in bm.faces:
            face.select = False

        bm.select_flush_mode()

    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    try:
        obj.update_from_editmode()
    except Exception:
        pass
    try:
        obj.data.update()
    except Exception:
        pass
    _tag_redraw_all_areas(context)

    return {
        "matched_groups": sorted(set(matched_groups), key=str.lower),
        "missing_groups": missing_groups,
        "selected_vert_count": selected_vert_count,
    }


def _delete_selected_components_keep_vertices(context, mesh):
    if mesh is None:
        raise RuntimeError("Mesh data is not available")

    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    explicit_faces = {face for face in bm.faces if face.is_valid and face.select}
    explicit_edges = {edge for edge in bm.edges if edge.is_valid and edge.select}
    selected_verts = {vert for vert in bm.verts if vert.is_valid and vert.select}

    for edge in explicit_edges:
        selected_verts.update(vert for vert in edge.verts if vert.is_valid)
    for face in explicit_faces:
        selected_verts.update(vert for vert in face.verts if vert.is_valid)

    if not selected_verts and not explicit_edges and not explicit_faces:
        raise RuntimeError("Select at least one vertex, edge or face in Edit Mode")

    faces_to_delete = {face for face in explicit_faces if face.is_valid}
    faces_to_delete.update(
        face
        for face in bm.faces
        if face.is_valid and all(vert in selected_verts for vert in face.verts)
    )

    edges_to_delete = {edge for edge in explicit_edges if edge.is_valid}
    edges_to_delete.update(
        edge
        for edge in bm.edges
        if edge.is_valid and all(vert in selected_verts for vert in edge.verts)
    )

    deleted_face_count = len(faces_to_delete)
    if faces_to_delete:
        bmesh.ops.delete(bm, geom=list(faces_to_delete), context="FACES")

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    valid_edges_to_delete = [edge for edge in edges_to_delete if edge.is_valid]
    deleted_edge_count = len(valid_edges_to_delete)
    if valid_edges_to_delete:
        bmesh.ops.delete(bm, geom=valid_edges_to_delete, context="EDGES")

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    kept_verts = [vert for vert in selected_verts if vert.is_valid]
    context.tool_settings.mesh_select_mode = (True, False, False)
    for face in bm.faces:
        face.select = False
    for edge in bm.edges:
        edge.select = False
    for vert in bm.verts:
        vert.select = False
    for vert in kept_verts:
        vert.select = True

    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=True)

    return {
        "deleted_faces": deleted_face_count,
        "deleted_edges": deleted_edge_count,
        "kept_verts": len(kept_verts),
    }


class CRAY_OT_OpenFixListFile(Operator):
    bl_idname = "cray.open_fix_list_file"
    bl_label = "Choose Fix List"
    bl_description = "Pick a structured .txt file with bad component names"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(
        name="Fix List File",
        description="Choose a .txt file with bad component names per .p3d and LOD",
        subtype="FILE_PATH",
    )
    filter_glob: StringProperty(default="*.txt", options={"HIDDEN"})

    def invoke(self, context, event):
        del event

        ts = context.scene.cray_texreplace_settings
        current_path = bpy.path.abspath(getattr(ts, "fix_list_path", "") or "")
        if current_path and os.path.isfile(current_path):
            self.filepath = current_path
        else:
            blend_dir = bpy.path.abspath("//")
            if blend_dir and os.path.isdir(blend_dir):
                self.filepath = os.path.join(blend_dir, "")
            else:
                self.filepath = os.path.join(os.getcwd(), "")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        ts = context.scene.cray_texreplace_settings

        try:
            filepath = os.path.abspath(bpy.path.abspath(self.filepath or ""))
        except Exception as e:
            self.report({"ERROR"}, f"Could not resolve fix-list path: {_fmt_exc(e)}")
            return {"CANCELLED"}

        if not filepath or not os.path.isfile(filepath):
            self.report({"ERROR"}, "Choose an existing .txt file")
            return {"CANCELLED"}

        if os.path.splitext(filepath)[1].lower() != ".txt":
            self.report({"ERROR"}, "Choose a .txt file")
            return {"CANCELLED"}

        ts.fix_list_path = filepath
        self.report({"INFO"}, f"Fix list set: {os.path.basename(filepath)}")
        return {"FINISHED"}


class CRAY_OT_SelectFixListComponentsOnActiveLOD(Operator):
    bl_idname = "cray.select_fix_list_components_on_active_lod"
    bl_label = "Select Bad Components From List"
    bl_description = (
        "Read the structured fix-list .txt, match the active object's .p3d root collection name, "
        "and select bad component vertices for the active Geometry/View Geometry/Fire Geometry LOD"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_textures import (_find_p3d_root_collection_for_object)
        ts = context.scene.cray_texreplace_settings
        active_obj = context.view_layer.objects.active
        if active_obj is None or active_obj.type != "MESH" or active_obj.data is None:
            self.report({"ERROR"}, "Active object must be a mesh")
            return {"CANCELLED"}

        filepath = bpy.path.abspath(getattr(ts, "fix_list_path", "") or "")
        if not filepath:
            self.report({"ERROR"}, "Choose a fix-list .txt file first")
            return {"CANCELLED"}
        filepath = os.path.abspath(filepath)
        if not os.path.isfile(filepath):
            self.report({"ERROR"}, "Fix-list file does not exist")
            return {"CANCELLED"}

        root_collection = _find_p3d_root_collection_for_object(context, active_obj)
        if root_collection is None:
            self.report({"ERROR"}, "Active object is not inside a .p3d root collection")
            return {"CANCELLED"}

        lod_key = _detect_fix_list_lod_key(active_obj)
        if lod_key not in {"geometry", "geometryview", "geometryfire"}:
            self.report({"ERROR"}, "Active object must be Geometry, View Geometry or Fire Geometry")
            return {"CANCELLED"}

        model_key = _normalize_fix_list_model_name(getattr(root_collection, "name", "") or "")
        if not model_key:
            self.report({"ERROR"}, "Could not normalize the .p3d root collection name")
            return {"CANCELLED"}

        try:
            entries = _parse_fix_list_file(filepath)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to parse fix-list: {_fmt_exc(e)}")
            return {"CANCELLED"}

        entry = entries.get(model_key)
        if entry is None:
            self.report({"WARNING"}, f"No fix-list entry found for {root_collection.name}")
            return {"CANCELLED"}

        target_groups = sorted(entry.get("lod_groups", {}).get(lod_key, set()), key=str.lower)
        if not target_groups:
            lod_label = {
                "geometry": "Geometry",
                "geometryview": "View Geometry",
                "geometryfire": "Fire Geometry",
            }.get(lod_key, lod_key)
            self.report({"INFO"}, f"No bad components listed for {entry['display_name']} / {lod_label}")
            return {"FINISHED"}

        try:
            stats = _select_fix_list_groups_on_active_lod(context, active_obj, target_groups)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        matched_count = len(stats["matched_groups"])
        missing_count = len(stats["missing_groups"])
        selected_vert_count = int(stats["selected_vert_count"])

        if matched_count == 0:
            self.report({"WARNING"}, f"Listed groups were not found on {active_obj.name}")
            return {"CANCELLED"}

        msg = (
            f"{entry['display_name']} / {active_obj.name}: "
            f"matched {matched_count} group(s), selected {selected_vert_count} vertex/vertices"
        )
        if missing_count > 0:
            print("=== Fix List: Missing vertex groups ===")
            print(f"Model: {entry['display_name']}")
            print(f"LOD: {active_obj.name}")
            for group_name in stats["missing_groups"]:
                print(group_name)
            msg += f", missing {missing_count} group(s) (see System Console)"

        self.report({"INFO"}, msg)
        return {"FINISHED"}


class CRAY_OT_DeleteSelectedComponentsKeepVertices(Operator):
    bl_idname = "cray.delete_selected_components_keep_vertices"
    bl_label = "Delete Faces/Edges Keep Verts"
    bl_description = (
        "On the active mesh in Edit Mode, delete faces and edges belonging to the current selection "
        "while keeping the vertices"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return context.mode == "EDIT_MESH" and obj is not None and obj.type == "MESH"

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        obj = context.active_object
        if obj is None or obj.type != "MESH" or obj.data is None:
            self.report({"ERROR"}, "Active object must be a mesh in Edit Mode")
            return {"CANCELLED"}

        try:
            stats = _delete_selected_components_keep_vertices(context, obj.data)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"{obj.name}: deleted {stats['deleted_faces']} face(s) and "
                f"{stats['deleted_edges']} edge(s), kept {stats['kept_verts']} vertex/vertices"
            ),
        )
        return {"FINISHED"}


def _material_key_for_mesh_index(mesh, material_index):
    material_index = int(material_index)
    materials = getattr(mesh, "materials", None)
    if materials is not None and 0 <= material_index < len(materials):
        material = materials[material_index]
        if material is not None:
            try:
                return ("material", int(material.as_pointer()))
            except Exception:
                return ("material_name", str(getattr(material, "name", "")))
    return ("slot", material_index)


def _bmesh_vert_material_signature(mesh, vert):
    keys = {
        _material_key_for_mesh_index(mesh, face.material_index)
        for face in vert.link_faces
        if face.is_valid
    }
    return tuple(sorted(keys))


def _selected_bmesh_verts_for_material_safe_merge(bm, *, require_selection):
    if not require_selection:
        return [vert for vert in bm.verts if vert.is_valid]

    verts = []
    seen = set()

    def _add_vert(vert):
        if vert is None or not vert.is_valid:
            return
        key = vert.index
        if key in seen:
            return
        seen.add(key)
        verts.append(vert)

    for vert in bm.verts:
        if vert.select:
            _add_vert(vert)
    for edge in bm.edges:
        if edge.is_valid and edge.select:
            for vert in edge.verts:
                _add_vert(vert)
    for face in bm.faces:
        if face.is_valid and face.select:
            for vert in face.verts:
                _add_vert(vert)
    return verts


def _merge_bmesh_verts_by_distance_keep_materials(mesh, bm, verts, merge_distance):
    merge_distance = max(float(merge_distance), 0.0)
    if merge_distance <= 0.0:
        return {"removed_verts": 0, "input_verts": len(verts), "material_groups": 0}

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()

    before_vert_count = len(bm.verts)
    groups = {}
    seen = set()
    for vert in verts:
        if vert is None or not vert.is_valid:
            continue
        key = vert.index
        if key in seen:
            continue
        seen.add(key)
        signature = _bmesh_vert_material_signature(mesh, vert)
        groups.setdefault(signature, []).append(vert)

    for group_verts in groups.values():
        live_verts = [vert for vert in group_verts if vert.is_valid]
        if len(live_verts) < 2:
            continue
        bmesh.ops.remove_doubles(bm, verts=live_verts, dist=merge_distance)

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.normal_update()
    return {
        "removed_verts": max(0, before_vert_count - len(bm.verts)),
        "input_verts": len(seen),
        "material_groups": len(groups),
    }


def _merge_object_by_distance_keep_materials(obj, merge_distance):
    mesh = getattr(obj, "data", None)
    if obj is None or getattr(obj, "type", None) != "MESH" or mesh is None:
        raise RuntimeError("Object must be a mesh")

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        verts = _selected_bmesh_verts_for_material_safe_merge(bm, require_selection=False)
        stats = _merge_bmesh_verts_by_distance_keep_materials(mesh, bm, verts, merge_distance)
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True)
        return stats
    finally:
        bm.free()


def _merge_edit_mesh_by_distance_keep_materials(obj, merge_distance):
    mesh = getattr(obj, "data", None)
    if obj is None or getattr(obj, "type", None) != "MESH" or mesh is None:
        raise RuntimeError("Active object must be a mesh")

    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    verts = _selected_bmesh_verts_for_material_safe_merge(bm, require_selection=True)
    if not verts:
        raise RuntimeError("Select vertices, edges or faces to merge")

    stats = _merge_bmesh_verts_by_distance_keep_materials(mesh, bm, verts, merge_distance)
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=True)
    return stats


class CRAY_OT_MergeByDistanceKeepMaterials(Operator):
    bl_idname = "cray.merge_by_distance_keep_materials"
    bl_label = "Material Safe Merge"
    bl_description = (
        "Merge vertices by distance, but keep coincident vertices separate when their linked faces "
        "belong to different materials"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        active_obj = getattr(context, "active_object", None)
        if context.mode == "EDIT_MESH":
            return active_obj is not None and active_obj.type == "MESH"
        return any(
            obj is not None and obj.type == "MESH"
            for obj in getattr(context, "selected_objects", [])
        ) or (active_obj is not None and active_obj.type == "MESH")

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        ts = context.scene.cray_texreplace_settings
        merge_distance = float(getattr(ts, "material_safe_merge_distance", 0.0))
        if merge_distance <= 0.0:
            self.report({"ERROR"}, "Material Safe Merge Distance must be greater than zero")
            return {"CANCELLED"}

        total_removed = 0
        total_input = 0
        total_groups = 0
        object_count = 0

        try:
            if context.mode == "EDIT_MESH":
                obj = context.active_object
                stats = _merge_edit_mesh_by_distance_keep_materials(obj, merge_distance)
                total_removed += int(stats.get("removed_verts", 0))
                total_input += int(stats.get("input_verts", 0))
                total_groups += int(stats.get("material_groups", 0))
                object_count = 1
            else:
                objects = [
                    obj for obj in getattr(context, "selected_objects", [])
                    if obj is not None and obj.type == "MESH" and obj.data is not None
                ]
                active_obj = getattr(context, "active_object", None)
                if not objects and active_obj is not None and active_obj.type == "MESH":
                    objects = [active_obj]
                if not objects:
                    raise RuntimeError("Select at least one mesh object")

                for obj in objects:
                    stats = _merge_object_by_distance_keep_materials(obj, merge_distance)
                    total_removed += int(stats.get("removed_verts", 0))
                    total_input += int(stats.get("input_verts", 0))
                    total_groups += int(stats.get("material_groups", 0))
                object_count = len(objects)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Material Safe Merge: {object_count} object(s), "
                f"{total_removed} vertex/vertices removed from {total_input}, "
                f"{total_groups} material group(s)"
            ),
        )
        return {"FINISHED"}


def _is_proxy_object_name(name: str) -> bool:
    return (name or "").strip().lower().startswith("proxy:")


def _is_p3d_proxy_object(obj) -> bool:
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    if _is_proxy_object_name(getattr(obj, "name", "")):
        return True
    try:
        return bool(getattr(obj.a3ob_properties_object_proxy, "is_a3_proxy", False))
    except Exception:
        return False


# ------------------------------------------------------------------------
#  Cut selection to a new asset scene, then export it into an NH library
# ------------------------------------------------------------------------


def _asset_cut_sanitize_name(raw_name: str = ""):
    name = str(raw_name or "").strip() or "new asset"
    name = re.sub(r"\s+", " ", name).strip()
    safe = re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .") or "new asset"
    root_name = safe
    if not root_name.lower().endswith(".p3d"):
        root_name += ".p3d"
    return safe, root_name


def _is_a3_lod_object(obj) -> bool:
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    try:
        props = getattr(obj, "a3ob_properties_object", None)
        return bool(getattr(props, "is_a3_lod", False))
    except Exception:
        return False


def _asset_cut_canonical_lod_name(obj) -> str:
    try:
        props = getattr(obj, "a3ob_properties_object", None)
        if props is not None and bool(getattr(props, "is_a3_lod", False)):
            lod = str(getattr(props, "lod", "0") or "0")
            resolution = int(getattr(props, "resolution", 0) or 0)
            if lod == "0":
                return "Resolution %d" % resolution
    except Exception:
        pass
    return "Resolution 0"


def _asset_cut_ensure_object_lod_props(obj):
    try:
        props = getattr(obj, "a3ob_properties_object", None)
        if props is None:
            return
        if not bool(getattr(props, "is_a3_lod", False)):
            props.is_a3_lod = True
        if not (getattr(props, "lod", "") or ""):
            props.lod = "0"
        try:
            props.resolution = int(getattr(props, "resolution", 0) or 0)
        except Exception:
            pass
    except Exception:
        pass


def _asset_cut_unique_lod_name(obj) -> str:
    base = _asset_cut_canonical_lod_name(obj)
    candidate = base
    index = 1
    while bpy.data.objects.get(candidate) not in (None, obj):
        candidate = "%s.%03d" % (base, index)
        index += 1
    try:
        if getattr(obj, "data", None) is not None:
            obj.data.name = candidate
    except Exception:
        pass
    return candidate


def _asset_cut_move_object_into_collection(obj, target_collection) -> bool:
    if obj is None or target_collection is None:
        return False
    for parent in list(getattr(obj, "users_collection", []) or []):
        try:
            parent.objects.unlink(obj)
        except Exception:
            pass
    try:
        target_collection.objects.link(obj)
        return True
    except Exception:
        try:
            for parent in list(getattr(obj, "users_collection", []) or []):
                try:
                    target_collection.objects.link(obj)
                except Exception:
                    pass
            return True
        except Exception:
            return False


def _asset_cut_unhide_collection_tree(root_collection):
    from .nh_textures import (_iter_collection_tree)
    for col in _iter_collection_tree(root_collection):
        try:
            col.hide_viewport = False
        except Exception:
            pass
        try:
            col.hide_render = False
        except Exception:
            pass


def _asset_cut_center_bottom_to_origin(objects):
    from mathutils import Vector
    min_v = None
    max_v = None
    for obj in objects or []:
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        try:
            matrix = obj.matrix_world
            for corner in obj.bound_box:
                world = matrix @ Vector(corner)
                if min_v is None:
                    min_v = world.copy()
                    max_v = world.copy()
                else:
                    min_v.x = min(min_v.x, world.x)
                    min_v.y = min(min_v.y, world.y)
                    min_v.z = min(min_v.z, world.z)
                    max_v.x = max(max_v.x, world.x)
                    max_v.y = max(max_v.y, world.y)
                    max_v.z = max(max_v.z, world.z)
        except Exception:
            continue
    if min_v is None:
        return False
    delta = Vector(
        (
            -(min_v.x + max_v.x) / 2.0,
            -(min_v.y + max_v.y) / 2.0,
            -min_v.z,
        )
    )
    moved = 0
    for obj in objects or []:
        if obj is None or getattr(obj, "parent", None) is not None:
            continue
        try:
            obj.location = obj.location + delta
            moved += 1
        except Exception:
            pass
    return moved > 0


def _asset_cut_duplicate_object_for_asset(obj):
    try:
        dup = obj.copy()
    except Exception:
        return None
    try:
        dup.data = obj.data.copy()
    except Exception:
        pass
    return dup


def _cut_selection_to_asset_scene(op, context):
    from .nh_textures import (_ensure_model_split_target_category_collection, _model_split_category_for_object)
    settings = context.scene.cray_asset_library_settings
    selected = sorted(
        [o for o in getattr(context, "selected_objects", []) if o is not None and o.type == "MESH"],
        key=lambda o: o.name,
    )
    if not selected:
        op.report({"ERROR"}, "Select at least one mesh object in Edit/Object mode to cut")
        return {"CANCELLED"}

    scene_name, root_name = _asset_cut_sanitize_name(getattr(settings, "asset_cut_name", ""))

    try:
        new_scene = bpy.data.scenes.new(scene_name)
    except Exception as e:
        op.report({"ERROR"}, f"Could not create a new scene: {e}")
        return {"CANCELLED"}

    try:
        target_root = bpy.data.collections.new(root_name)
        try:
            new_scene.collection.children.link(target_root)
        except Exception:
            pass
        moved_meshes = []
        for obj in selected:
            dup = _asset_cut_duplicate_object_for_asset(obj)
            if dup is None:
                continue
            category = _model_split_category_for_object(dup)
            target_col = _ensure_model_split_target_category_collection(target_root, category)
            if not _asset_cut_move_object_into_collection(dup, target_col):
                _asset_cut_move_object_into_collection(dup, target_root)
            _asset_cut_ensure_object_lod_props(dup)
            if not _is_a3_lod_object(dup):
                try:
                    dup.name = _asset_cut_unique_lod_name(dup)
                except Exception:
                    pass
            moved_meshes.append(dup)
        _asset_cut_unhide_collection_tree(target_root)
    except Exception as e:
        op.report({"ERROR"}, f"Could not move the selection: {e}")
        return {"CANCELLED"}

    recentered = False
    try:
        recentered = _asset_cut_center_bottom_to_origin(moved_meshes)
    except Exception:
        recentered = False

    for obj in list(getattr(context, "selected_objects", []) or []):
        try:
            obj.select_set(False)
        except Exception:
            pass

    window = getattr(context, "window", None)
    if window is not None:
        try:
            window.scene = new_scene
        except Exception:
            pass

    op.report(
        {"INFO"},
        f"Copied {len(moved_meshes)} mesh(es) into new scene '{scene_name}' ({root_name})"
        + (", centered to XY=0 and bottom Z=0" if recentered else ""),
    )
    return {"FINISHED"}


def _collect_exportable_lod_objects(scene):
    from .nh_textures import (_iter_collection_tree)
    out = []
    seen = set()
    if scene is None:
        return out
    for col in _iter_collection_tree(scene.collection):
        for obj in col.objects:
            ptr = obj.as_pointer()
            if ptr in seen:
                continue
            seen.add(ptr)
            if not _is_a3_lod_object(obj):
                continue
            if obj.parent is not None:
                continue
            out.append(obj)
    return sorted(out, key=lambda o: o.name)


def _resolve_asset_library_for_exported_path(settings, filepath: str):
    from .nh_model_split import (_nh_objects_common_root, _nh_objects_custom_search_root, _nh_objects_environment_root, _path_is_under_or_equal)
    path_abs = os.path.abspath(bpy.path.abspath(filepath))
    common = _nh_objects_common_root(settings)
    environment = _nh_objects_environment_root(settings)
    custom = _nh_objects_custom_search_root(settings)
    if common and _path_is_under_or_equal(path_abs, common):
        return "Common", path_abs
    if environment and _path_is_under_or_equal(path_abs, environment):
        return "Environment", path_abs
    if custom and _path_is_under_or_equal(path_abs, custom):
        return "Custom", path_abs
    return "Custom", path_abs


def _nh_custom_incremental_cache_folder_for_p3d(p3d_path: str, create=False) -> str:
    from .nh_collider_exp import (_norm_path)
    from .nh_model_split import (_NH_OBJECTS_INCREMENTAL_CACHE_FOLDER_NAME, _nh_objects_custom_asset_cache_root)
    cache_root = _nh_objects_custom_asset_cache_root(create=create)
    stem = os.path.splitext(os.path.basename(p3d_path or ""))[0] or "asset"
    safe_stem = re.sub(r'[<>:"/\\|?*]+', "_", stem).strip(" .") or "asset"
    digest = hashlib.sha1(_norm_path(os.path.abspath(bpy.path.abspath(p3d_path))).encode("utf-8", "ignore")).hexdigest()[:12]
    cache_folder = os.path.join(cache_root, _NH_OBJECTS_INCREMENTAL_CACHE_FOLDER_NAME, f"{safe_stem}_{digest}")
    if create:
        os.makedirs(cache_folder, exist_ok=True)
    return cache_folder


def _build_asset_library_blend_from_scene_asset(
    context,
    root_collection,
    filepath: str,
    settings,
    *,
    cache_folder_abs: str,
    catalog_id: str,
    preview_dir: str,
    manifest_kind: str = "incremental",
    source_folder_abs: str = "",
    manifest_files=None,
):
    from .nh_base import (_fmt_exc)
    from .nh_model_split import (_NH_OBJECTS_ASSET_PREVIEWS_FOLDER_NAME)
    if root_collection is None:
        raise RuntimeError("No asset root collection to build the library blend from")
    _clear_temp_asset_library(context)
    asset_root = _ensure_temp_asset_library_root(context)
    preview_paths = []
    try:
        instancer = _create_asset_instancer_for_collection(
            asset_root,
            root_collection,
            filepath,
            catalog_id=catalog_id,
            preview_dir=preview_dir,
            render_textured_previews=True,
            preview_paths_out=preview_paths,
        )
        if instancer is None:
            raise RuntimeError("Could not create an asset instancer for the scene asset")

        blend_path, asset_entries = _write_persistent_asset_library_blend(cache_folder_abs, asset_root)
        if manifest_kind == "custom" or manifest_kind == "custom_manifest":
            manifest_path = _write_custom_asset_manifest(
                cache_folder_abs,
                list(manifest_files) if manifest_files else [filepath],
                blend_path,
                asset_entries,
                settings=settings,
            )
        else:
            manifest_path = _write_persistent_asset_library_manifest(
                cache_folder_abs,
                source_folder_abs or os.path.dirname(filepath),
                [filepath],
                blend_path,
                asset_entries,
                settings=settings,
                preview_mode_override="textured",
                preview_files=preview_paths,
                texture_preview_stats={
                    "textured_candidates": 0,
                    "previewed": 0,
                    "missing": 0,
                    "packed": 0,
                    "errors": 0,
                },
            )
        return {
            "blend_path": blend_path,
            "manifest_path": manifest_path,
            "asset_entries": asset_entries,
            "preview_paths": preview_paths,
        }
    finally:
        try:
            _clear_temp_asset_library(context)
        except Exception:
            pass


def _add_exported_asset_to_library(op, context, filepath: str, root_collection=None):
    from .nh_base import (_fmt_exc)
    from .nh_model_split import (_NH_OBJECTS_ASSET_PREVIEWS_FOLDER_NAME, _NH_OBJECTS_CUSTOM_LIBRARY_NAME, _iter_nh_objects_asset_source_folders, _nh_asset_catalog_id, _nh_asset_catalog_path_for_source_folder, _nh_asset_catalog_paths_by_cache_root, _write_nh_asset_catalog_file)
    settings = context.scene.cray_asset_library_settings
    label, path_abs = _resolve_asset_library_for_exported_path(settings, filepath)
    path_key = os.path.normcase(path_abs)
    old_render = bool(getattr(settings, "render_textured_previews", False))
    try:
        settings.render_textured_previews = True
        if root_collection is None:
            op.report({"ERROR"}, "No asset root collection was found to add to the library")
            return False, label
        if label == "Custom":
            _register_nh_objects_blender_asset_libraries()
            _write_custom_asset_catalog_file()
            cache_folder_abs = _nh_custom_incremental_cache_folder_for_p3d(path_abs, create=True)
            preview_dir = os.path.join(cache_folder_abs, _NH_OBJECTS_ASSET_PREVIEWS_FOLDER_NAME)
            stats = _build_asset_library_blend_from_scene_asset(
                context,
                root_collection,
                path_abs,
                settings,
                cache_folder_abs=cache_folder_abs,
                catalog_id=_custom_asset_catalog_id(),
                preview_dir=preview_dir,
                manifest_kind="custom",
                manifest_files=[path_abs],
            )
            preferred = _NH_OBJECTS_CUSTOM_LIBRARY_NAME
        else:
            folder_abs = os.path.dirname(path_abs)
            cache_folder = _nh_incremental_asset_cache_folder_for_p3d(folder_abs, path_abs, settings, create=True)
            catalog_paths = _nh_asset_catalog_paths_by_cache_root(list(_iter_nh_objects_asset_source_folders(settings)), settings)
            for cache_root, paths in catalog_paths.items():
                try:
                    _write_nh_asset_catalog_file(cache_root, paths)
                except Exception as e:
                    print(f"NH Objects Asset Catalogs: {cache_root}: {_fmt_exc(e)}")
            library_label = f"NH Objects - {label}"
            catalog_id = _nh_asset_catalog_id(library_label, _nh_asset_catalog_path_for_source_folder(folder_abs, settings))
            preview_dir = os.path.join(cache_folder, _NH_OBJECTS_ASSET_PREVIEWS_FOLDER_NAME)
            stats = _build_asset_library_blend_from_scene_asset(
                context,
                root_collection,
                path_abs,
                settings,
                cache_folder_abs=cache_folder,
                catalog_id=catalog_id,
                preview_dir=preview_dir,
                manifest_kind="incremental",
                source_folder_abs=folder_abs,
            )
            preferred = library_label
        try:
            _open_nh_objects_asset_browser(context, settings, preferred_library_name=preferred)
        except Exception:
            pass
        return True, label
    except Exception as e:
        op.report({"ERROR"}, f"Could not add the asset to the library: {_fmt_exc(e)}")
        return False, label
    finally:
        try:
            settings.render_textured_previews = old_render
        except Exception:
            pass


class CRAY_OT_AssetCutToScene(Operator):
    bl_idname = "cray.asset_cut_to_scene"
    bl_label = "Copy to New Scene"
    bl_description = (
        "COPY ONLY the selected mesh object(s) into a new scene with the standard P3D structure "
        "(Scene > <asset>.p3d > Visuals / Geometries / Point clouds), centered to XY=0 with bottom at Z=0; "
        "the original meshes stay untouched"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _cut_selection_to_asset_scene(self, context)


class CRAY_OT_AssetSaveToLibrary(Operator):
    bl_idname = "cray.asset_save_to_library"
    bl_label = "Save to Library"
    bl_description = (
        "Export the active scene as .p3d (pick the path in the file browser), then auto-add the asset to the "
        "matching NH Objects library (Common / Environment / Custom) with a browser icon and open the Asset Browser"
    )
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(name="Filepath", default="", subtype="FILE_PATH")
    filter_glob: StringProperty(name="Filter", default="*.p3d", options={"HIDDEN"})
    check_existing: BoolProperty(name="Check Existing", default=True)

    def invoke(self, context, event):
        from .nh_base import (_fmt_exc)
        from .nh_model_split import (_nh_objects_custom_search_root)
        settings = context.scene.cray_asset_library_settings
        asset_name, _root_name = _asset_cut_sanitize_name(getattr(settings, "asset_cut_name", ""))
        default_dir = _nh_objects_custom_search_root(settings)
        if not os.path.isdir(default_dir):
            try:
                default_dir = os.path.dirname(bpy.path.abspath(bpy.data.filepath)) if bpy.data.filepath else ""
            except Exception:
                default_dir = ""
        try:
            self.filepath = os.path.join(default_dir or "", asset_name + ".p3d")
        except Exception:
            self.filepath = asset_name + ".p3d"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_model_split import (_NH_TEMP_ASSET_SCENE_NAME)
        from .nh_snap import (_call_export_with_optional_relaxed_validation, _restore_collision_lod_materials_after_export, _restore_p3d_named_properties_after_export, _strip_collision_lod_materials_for_export, _strip_p3d_named_properties_for_export)
        scene = context.scene
        if scene is None:
            self.report({"ERROR"}, "No active scene")
            return {"CANCELLED"}
        if scene.name == _NH_TEMP_ASSET_SCENE_NAME:
            self.report({"ERROR"}, "Switch to the scene you cut the asset into before saving")
            return {"CANCELLED"}

        filepath = os.path.abspath(bpy.path.abspath(self.filepath or ""))
        if not filepath or not filepath.lower().endswith(".p3d"):
            filepath = (filepath or "asset") + ".p3d"

        export_objects = _collect_exportable_lod_objects(scene)
        if not export_objects:
            self.report({"ERROR"}, "No P3D LOD mesh objects found in the active scene")
            return {"CANCELLED"}

        from .nh_textures import (_find_p3d_root_collection_for_object)
        root_collection = None
        try:
            for obj in export_objects:
                root_collection = _find_p3d_root_collection_for_object(context, obj)
                if root_collection is not None:
                    break
            if root_collection is None:
                cols = [c for c in getattr(export_objects[0], "users_collection", []) if c is not None]
                root_collection = cols[0] if cols else None
        except Exception:
            root_collection = None
        if root_collection is None:
            self.report({"ERROR"}, "Could not find the asset's .p3d root collection in the scene")
            return {"CANCELLED"}

        try:
            for obj in context.view_layer.objects:
                try:
                    obj.select_set(False)
                except Exception:
                    pass
            for obj in export_objects:
                try:
                    obj.select_set(True)
                except Exception:
                    pass
            try:
                context.view_layer.objects.active = export_objects[0]
            except Exception:
                pass
        except Exception:
            pass

        material_restore = []
        named_property_restore = []
        try:
            material_restore = _strip_collision_lod_materials_for_export(export_objects)
            named_property_restore = _strip_p3d_named_properties_for_export(export_objects)
        except Exception:
            material_restore = []
            named_property_restore = []

        try:
            result, _op_id, err = _call_export_with_optional_relaxed_validation(
                force_all_lods=False,
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
                renumber_components=True,
                translate_selections=False,
                force_lowercase=True,
            )
        finally:
            try:
                _restore_p3d_named_properties_after_export(named_property_restore)
            except Exception:
                pass
            try:
                _restore_collision_lod_materials_after_export(material_restore)
            except Exception:
                pass
        if result is None:
            self.report({"ERROR"}, f"P3D export failed: {_fmt_exc(err) if err else 'no export operator found'}")
            return {"CANCELLED"}
        if not os.path.isfile(filepath):
            self.report({"ERROR"}, f"P3D export finished but no file was written: {filepath}")
            return {"CANCELLED"}

        added, library_label = _add_exported_asset_to_library(self, context, filepath, root_collection=root_collection)
        if not added:
            self.report(
                {"WARNING"},
                f"Exported {os.path.basename(filepath)}, but adding it to the asset library failed (see System Console)",
            )
            return {"FINISHED"}
        self.report({"INFO"}, f"Saved {os.path.basename(filepath)} and added it to the '{library_label}' library")
        return {"FINISHED"}

