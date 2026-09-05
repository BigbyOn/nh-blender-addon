"""Blender operators and transient UI state for Geometry Audit."""

import hashlib
import struct

import bpy
import bmesh
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, PropertyGroup


_GEOMETRY_AUDIT_LODS = (
    ("6", "Geometry"),
    ("15", "Fire Geometry"),
    ("14", "View Geometry"),
)
_GEOMETRY_AUDIT_LOD_LABELS = dict(_GEOMETRY_AUDIT_LODS)
_GEOMETRY_AUDIT_LOD_TOKENS = set(_GEOMETRY_AUDIT_LOD_LABELS)
_GEOMETRY_AUDIT_CACHE = {}


class CRAY_PG_GeometryAuditDetail(PropertyGroup):
    text: StringProperty(default="", options={"SKIP_SAVE"})


class CRAY_PG_GeometryAuditLODResult(PropertyGroup):
    lod_token: StringProperty(default="", options={"SKIP_SAVE"})
    lod_name: StringProperty(default="", options={"SKIP_SAVE"})
    raw_components: IntProperty(default=0, options={"SKIP_SAVE"})
    effective_components: IntProperty(default=0, options={"SKIP_SAVE"})
    loose_vertices: IntProperty(default=0, options={"SKIP_SAVE"})
    faceless_islands: IntProperty(default=0, options={"SKIP_SAVE"})
    tiny_components: IntProperty(default=0, options={"SKIP_SAVE"})
    nested_suspicious: IntProperty(default=0, options={"SKIP_SAVE"})
    nested_strong: IntProperty(default=0, options={"SKIP_SAVE"})
    not_testable_pairs: IntProperty(default=0, options={"SKIP_SAVE"})
    show_details: BoolProperty(default=False, options={"SKIP_SAVE"})
    details: CollectionProperty(type=CRAY_PG_GeometryAuditDetail, options={"SKIP_SAVE"})


class CRAY_PG_GeometryAuditSettings(PropertyGroup):
    scope: EnumProperty(
        name="Scope",
        description="Choose all collision LODs in the active P3D or only selected relevant LODs",
        items=(
            ("ACTIVE_P3D", "Active P3D", "Scan Geometry, Fire Geometry and View Geometry in the active P3D"),
            ("SELECTED_LODS", "Selected LODs", "Scan selected Geometry, Fire Geometry and View Geometry LODs"),
        ),
        default="ACTIVE_P3D",
    )
    inside_threshold: FloatProperty(
        name="Inside Threshold",
        description="Minimum share of vertices and face centers that must be inside a larger closed component",
        default=0.80,
        min=0.50,
        max=0.95,
        subtype="FACTOR",
        precision=1,
    )
    has_results: BoolProperty(default=False, options={"SKIP_SAVE"})
    scope_label: StringProperty(default="", options={"SKIP_SAVE"})
    lod_results: CollectionProperty(type=CRAY_PG_GeometryAuditLODResult, options={"SKIP_SAVE"})


def _geometry_audit_scene_key(scene):
    if scene is None:
        return None
    try:
        return scene.as_pointer()
    except Exception:
        return id(scene)


def _clear_geometry_audit_cache(scene=None):
    if scene is None:
        _GEOMETRY_AUDIT_CACHE.clear()
        return
    _GEOMETRY_AUDIT_CACHE.pop(_geometry_audit_scene_key(scene), None)


def _geometry_audit_has_live_cache(context):
    scene = getattr(context, "scene", None)
    settings = getattr(scene, "cray_geometry_audit_settings", None) if scene is not None else None
    return bool(
        settings is not None
        and settings.has_results
        and _GEOMETRY_AUDIT_CACHE.get(_geometry_audit_scene_key(scene))
    )


def _geometry_audit_lod_root(obj):
    from .nh_scatter import _collider_lod_token_from_object

    current = obj
    while current is not None:
        token = _collider_lod_token_from_object(current, allow_name_fallback=True)
        if token in _GEOMETRY_AUDIT_LOD_TOKENS:
            return current, token
        current = getattr(current, "parent", None)
    return None, ""


def _selected_geometry_audit_lods(context):
    found = {}
    for obj in getattr(context, "selected_objects", ()) or ():
        root, token = _geometry_audit_lod_root(obj)
        if root is None:
            continue
        try:
            key = root.as_pointer()
        except Exception:
            key = id(root)
        found[key] = (root, token)
    return sorted(found.values(), key=lambda item: (_GEOMETRY_AUDIT_LOD_LABELS[item[1]], item[0].name.lower()))


def _active_p3d_geometry_audit_lods(context):
    from .nh_textures import _collect_collection_objects_recursive, _find_p3d_root_collection_for_object

    anchors = []
    active = getattr(context, "active_object", None)
    if active is not None:
        anchors.append(active)
    for obj in getattr(context, "selected_objects", ()) or ():
        if obj is not None and obj not in anchors:
            anchors.append(obj)

    root_collection = None
    for anchor in anchors:
        root_collection = _find_p3d_root_collection_for_object(context, anchor)
        if root_collection is not None:
            break

    if root_collection is None:
        selected_lods = _selected_geometry_audit_lods(context)
        if selected_lods:
            return selected_lods, "Selected relevant LODs (no P3D root found)"
        raise RuntimeError("Select an object inside a .p3d collection or choose Selected LODs")

    found = {}
    for obj in _collect_collection_objects_recursive(root_collection):
        lod_root, token = _geometry_audit_lod_root(obj)
        if lod_root is None:
            continue
        try:
            key = lod_root.as_pointer()
        except Exception:
            key = id(lod_root)
        found[key] = (lod_root, token)

    lods = sorted(found.values(), key=lambda item: (_GEOMETRY_AUDIT_LOD_LABELS[item[1]], item[0].name.lower()))
    if not lods:
        raise RuntimeError(f"No Geometry, Fire Geometry or View Geometry LODs found in {root_collection.name}")
    return lods, root_collection.name


def _resolve_geometry_audit_scope(context, settings):
    if settings.scope == "SELECTED_LODS":
        lods = _selected_geometry_audit_lods(context)
        if not lods:
            raise RuntimeError("Select at least one Geometry, Fire Geometry or View Geometry LOD")
        return lods, f"Selected relevant LODs ({len(lods)})"
    return _active_p3d_geometry_audit_lods(context)


def _geometry_audit_lod_meshes(lod_root):
    from .nh_assets import _is_p3d_proxy_object
    from .nh_snap import _is_p3d_lod_root_object, _iter_p3d_export_meshes_for_lod_root
    from .nh_textures import _is_helper_object_name

    if _is_p3d_lod_root_object(lod_root):
        candidates = _iter_p3d_export_meshes_for_lod_root(lod_root)
    else:
        candidates = [lod_root]
        candidates.extend(
            child for child in getattr(lod_root, "children", ()) or ()
            if child is not None and getattr(child, "type", None) == "MESH"
        )

    meshes = []
    seen = set()
    for obj in candidates:
        if obj is None or getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
            continue
        if _is_p3d_proxy_object(obj) or _is_helper_object_name(getattr(obj, "name", "")):
            continue
        try:
            key = obj.as_pointer()
        except Exception:
            key = id(obj)
        if key in seen:
            continue
        seen.add(key)
        meshes.append(obj)
    return meshes


def _geometry_audit_object_signature(obj):
    mesh = getattr(obj, "data", None)
    if obj is None or mesh is None:
        return ""
    try:
        if getattr(obj, "mode", "OBJECT") == "EDIT":
            obj.update_from_editmode()
    except Exception:
        pass

    digest = hashlib.blake2b(digest_size=16)
    digest.update(struct.pack("<III", len(mesh.vertices), len(mesh.edges), len(mesh.polygons)))
    for vertex in mesh.vertices:
        digest.update(struct.pack("<ddd", float(vertex.co.x), float(vertex.co.y), float(vertex.co.z)))
    for edge in mesh.edges:
        digest.update(struct.pack("<II", int(edge.vertices[0]), int(edge.vertices[1])))
    for polygon in mesh.polygons:
        indices = tuple(int(index) for index in polygon.vertices)
        digest.update(struct.pack("<I", len(indices)))
        if indices:
            digest.update(struct.pack(f"<{len(indices)}I", *indices))
    try:
        matrix_values = tuple(float(value) for row in obj.matrix_world for value in row)
        digest.update(struct.pack("<16d", *matrix_values))
    except Exception:
        pass
    return digest.hexdigest()


def _geometry_audit_snapshot(objects):
    vertices = []
    edges = []
    faces = []
    triangles = []
    vertex_map = []
    object_signatures = {}

    for obj in objects:
        try:
            if getattr(obj, "mode", "OBJECT") == "EDIT":
                obj.update_from_editmode()
        except Exception:
            pass
        mesh = obj.data
        offset = len(vertices)
        matrix = obj.matrix_world
        for vertex in mesh.vertices:
            world = matrix @ vertex.co
            vertices.append((float(world.x), float(world.y), float(world.z)))
            vertex_map.append((obj.name, int(vertex.index)))
        edges.extend(
            (offset + int(edge.vertices[0]), offset + int(edge.vertices[1]))
            for edge in mesh.edges
        )
        faces.extend(
            tuple(offset + int(index) for index in polygon.vertices)
            for polygon in mesh.polygons
        )
        try:
            mesh.calc_loop_triangles()
            triangles.extend(
                tuple(offset + int(index) for index in triangle.vertices)
                for triangle in mesh.loop_triangles
            )
        except Exception:
            pass
        object_signatures[obj.name] = _geometry_audit_object_signature(obj)

    return {
        "vertices": tuple(vertices),
        "edges": tuple(edges),
        "faces": tuple(faces),
        "triangles": tuple(triangles),
        "vertex_map": tuple(vertex_map),
        "object_signatures": object_signatures,
    }


def _populate_geometry_audit_ui(settings, cache):
    settings.lod_results.clear()
    settings.scope_label = cache["scope_label"]
    entries_by_token = {}
    for entry in cache["entries"]:
        entries_by_token.setdefault(entry["lod_token"], []).append(entry)

    for token, lod_name in _GEOMETRY_AUDIT_LODS:
        entries = entries_by_token.get(token, ())
        if not entries:
            continue
        item = settings.lod_results.add()
        item.lod_token = token
        item.lod_name = lod_name
        summaries = [entry["result"].summary() for entry in entries]
        for field in (
            "raw_components",
            "effective_components",
            "loose_vertices",
            "faceless_islands",
            "tiny_components",
            "nested_suspicious",
            "nested_strong",
            "not_testable_pairs",
        ):
            setattr(item, field, sum(summary[field] for summary in summaries))

        for entry in entries:
            result = entry["result"]
            prefix = f"{entry['lod_root_name']}: " if len(entries) > 1 else ""
            for island_index, vertex_indices in enumerate(result.faceless_islands, start=1):
                detail = item.details.add()
                detail.text = f"{prefix}Faceless #{island_index}: {len(vertex_indices)} verts"
            for component in result.tiny_components:
                detail = item.details.add()
                detail.text = (
                    f"{prefix}Tiny C{component.index}: "
                    f"{component.vertices_count} verts, {component.faces_count} faces"
                )
            for kind, matches in (("Suspicious", result.nested_suspicious), ("Strong", result.nested_strong)):
                for match in matches:
                    component = result.components[match.inner_component_index]
                    detail = item.details.add()
                    detail.text = (
                        f"{prefix}{kind} C{match.inner_component_index} in C{match.outer_component_index}: "
                        f"{match.inside_fraction:.1%}, {component.vertices_count} verts, "
                        f"{component.faces_count} faces"
                    )

    settings.has_results = True


def _run_geometry_audit(context, settings, resolved_scope=None):
    from .nh_geometry_audit import audit_mesh_geometry

    if resolved_scope is None:
        lods, scope_label = _resolve_geometry_audit_scope(context, settings)
    else:
        lods, scope_label = resolved_scope
    entries = []
    for lod_root, lod_token in lods:
        objects = _geometry_audit_lod_meshes(lod_root)
        snapshot = _geometry_audit_snapshot(objects)
        result = audit_mesh_geometry(
            snapshot["vertices"],
            edges=snapshot["edges"],
            faces=snapshot["faces"],
            triangles=snapshot["triangles"],
            inside_threshold=settings.inside_threshold,
        )
        entries.append(
            {
                "lod_token": lod_token,
                "lod_root_name": lod_root.name,
                "result": result,
                "vertex_map": snapshot["vertex_map"],
                "object_signatures": snapshot["object_signatures"],
            }
        )

    cache = {
        "scope_label": scope_label,
        "inside_threshold": float(settings.inside_threshold),
        "entries": entries,
    }
    _GEOMETRY_AUDIT_CACHE[_geometry_audit_scene_key(context.scene)] = cache
    _populate_geometry_audit_ui(settings, cache)
    return cache


def _geometry_audit_cache_or_error(context):
    cache = _GEOMETRY_AUDIT_CACHE.get(_geometry_audit_scene_key(context.scene))
    if not cache:
        raise RuntimeError("Run Scan Geometry first")
    for entry in cache["entries"]:
        for object_name, expected_signature in entry["object_signatures"].items():
            obj = bpy.data.objects.get(object_name)
            if obj is None or _geometry_audit_object_signature(obj) != expected_signature:
                raise RuntimeError(f"Geometry changed for '{object_name}'; run Scan Geometry again")
    return cache


def _geometry_audit_issue_indices(result, issue):
    if issue == "FACELESS":
        return set(result.loose_vertices)
    if issue == "TINY":
        return {
            vertex_index
            for component in result.tiny_components
            for vertex_index in component.vertex_indices
        }
    if issue == "NESTED":
        component_indices = {
            match.inner_component_index
            for match in (*result.nested_suspicious, *result.nested_strong)
        }
        return {
            vertex_index
            for component_index in component_indices
            for vertex_index in result.components[component_index].vertex_indices
        }
    if issue == "SAFE":
        return set(result.safe_vertex_indices)
    return set()


def _geometry_audit_issue_vertex_map(cache, issue):
    mapped = {}
    for entry in cache["entries"]:
        indices = _geometry_audit_issue_indices(entry["result"], issue)
        for global_index in indices:
            if global_index < 0 or global_index >= len(entry["vertex_map"]):
                continue
            object_name, local_index = entry["vertex_map"][global_index]
            mapped.setdefault(object_name, set()).add(local_index)
    return mapped


def _select_geometry_audit_vertices(context, vertex_map):
    if not vertex_map:
        return 0, 0
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    for obj in context.view_layer.objects:
        try:
            obj.select_set(False)
        except Exception:
            pass

    targets = []
    for object_name in sorted(vertex_map):
        obj = bpy.data.objects.get(object_name)
        if obj is None or obj.type != "MESH" or obj.data is None:
            continue
        try:
            if context.view_layer.objects.get(obj.name) is None:
                continue
        except Exception:
            pass
        try:
            obj.hide_select = False
            obj.hide_viewport = False
            obj.hide_set(False)
            obj.select_set(True)
            targets.append(obj)
        except Exception:
            continue

    if not targets:
        raise RuntimeError("Found geometry is not available in the active View Layer")

    context.view_layer.objects.active = targets[0]
    context.scene.tool_settings.mesh_select_mode = (True, False, False)
    bpy.ops.object.mode_set(mode="EDIT")

    indices_by_mesh = {}
    mesh_by_key = {}
    for obj in targets:
        try:
            key = obj.data.as_pointer()
        except Exception:
            key = id(obj.data)
        mesh_by_key[key] = obj.data
        indices_by_mesh.setdefault(key, set()).update(vertex_map.get(obj.name, ()))

    selected_count = 0
    for key, mesh in mesh_by_key.items():
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        for face in bm.faces:
            face.select_set(False)
        for edge in bm.edges:
            edge.select_set(False)
        for vertex in bm.verts:
            vertex.select_set(False)
        for index in sorted(indices_by_mesh[key]):
            if 0 <= index < len(bm.verts):
                bm.verts[index].select_set(True)
                selected_count += 1
        bm.select_flush_mode()
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

    try:
        from .nh_snap import _tag_redraw_all_areas
        _tag_redraw_all_areas()
    except Exception:
        pass
    return selected_count, len(targets)


def _clean_geometry_audit_safe_vertices(context, vertex_map):
    if not vertex_map:
        return 0, 0
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    indices_by_mesh = {}
    mesh_by_key = {}
    for object_name, indices in vertex_map.items():
        obj = bpy.data.objects.get(object_name)
        if obj is None or obj.type != "MESH" or obj.data is None:
            continue
        try:
            key = obj.data.as_pointer()
        except Exception:
            key = id(obj.data)
        mesh_by_key[key] = obj.data
        indices_by_mesh.setdefault(key, set()).update(indices)

    deleted_vertices = 0
    changed_meshes = 0
    for key, mesh in mesh_by_key.items():
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.verts.ensure_lookup_table()
            targets = [bm.verts[index] for index in sorted(indices_by_mesh[key]) if 0 <= index < len(bm.verts)]
            if not targets:
                continue
            deleted_vertices += len(targets)
            bmesh.ops.delete(bm, geom=targets, context="VERTS")
            bm.to_mesh(mesh)
            mesh.update()
            changed_meshes += 1
        finally:
            bm.free()
    return deleted_vertices, changed_meshes


class CRAY_OT_GeometryAuditScan(Operator):
    bl_idname = "cray.geometry_audit_scan"
    bl_label = "Scan Geometry"
    bl_description = "Audit real topology in Geometry, Fire Geometry and View Geometry without changing it"

    def execute(self, context):
        settings = context.scene.cray_geometry_audit_settings
        _clear_geometry_audit_cache(context.scene)
        settings.has_results = False
        settings.lod_results.clear()
        try:
            cache = _run_geometry_audit(context, settings)
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        safe_count = sum(len(entry["result"].safe_vertex_indices) for entry in cache["entries"])
        nested_count = sum(
            len(entry["result"].nested_suspicious) + len(entry["result"].nested_strong)
            for entry in cache["entries"]
        )
        self.report(
            {"INFO"},
            f"Geometry Audit: {len(cache['entries'])} LOD(s), {safe_count} safe-garbage verts, {nested_count} nested candidate(s)",
        )
        return {"FINISHED"}


class CRAY_OT_GeometryAuditSelect(Operator):
    bl_idname = "cray.geometry_audit_select"
    bl_label = "Select Geometry Audit Result"
    bl_description = "Enter mesh Edit Mode and select geometry found by the last audit"
    bl_options = {"REGISTER"}

    issue: EnumProperty(
        items=(
            ("FACELESS", "Faceless", "Select vertices that do not belong to any polygon"),
            ("TINY", "Tiny", "Select connected topology components with at most three vertices"),
            ("NESTED", "Nested", "Select suspicious and strong nested component candidates"),
        ),
        default="FACELESS",
        options={"SKIP_SAVE"},
    )

    def execute(self, context):
        try:
            cache = _geometry_audit_cache_or_error(context)
            vertex_map = _geometry_audit_issue_vertex_map(cache, self.issue)
            if not vertex_map:
                self.report({"INFO"}, f"No {self.issue.lower()} geometry found")
                return {"FINISHED"}
            selected, object_count = _select_geometry_audit_vertices(context, vertex_map)
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Selected {selected} vertices in {object_count} mesh object(s)")
        return {"FINISHED"}


class CRAY_OT_GeometryAuditCleanSafe(Operator):
    bl_idname = "cray.geometry_audit_clean_safe"
    bl_label = "Clean Safe Garbage"
    bl_description = "Delete only faceless vertices and topology islands with at most three vertices; nested candidates are kept"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.cray_geometry_audit_settings
        try:
            cache = _geometry_audit_cache_or_error(context)
            vertex_map = _geometry_audit_issue_vertex_map(cache, "SAFE")
            if not vertex_map:
                self.report({"INFO"}, "No safe geometry garbage found; nothing changed")
                return {"FINISHED"}
            rescan_lods = []
            for entry in cache["entries"]:
                lod_root = bpy.data.objects.get(entry["lod_root_name"])
                if lod_root is None:
                    raise RuntimeError("Audit scope changed; run Scan Geometry again")
                rescan_lods.append((lod_root, entry["lod_token"]))
            deleted_vertices, changed_meshes = _clean_geometry_audit_safe_vertices(context, vertex_map)
            _clear_geometry_audit_cache(context.scene)
            _run_geometry_audit(
                context,
                settings,
                resolved_scope=(rescan_lods, cache["scope_label"]),
            )
        except Exception as error:
            settings.has_results = False
            settings.lod_results.clear()
            _clear_geometry_audit_cache(context.scene)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Cleaned {deleted_vertices} safe-garbage vertices in {changed_meshes} mesh(es); nested candidates were kept",
        )
        return {"FINISHED"}
