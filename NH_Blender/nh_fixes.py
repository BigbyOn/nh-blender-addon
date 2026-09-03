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

# nh_fixes.py
# auto-split slice; cross-module refs resolved with in-function imports

def _fix_proxy_triangle_mesh(obj):
    if obj is None or getattr(obj, "type", None) != "MESH" or obj.data is None:
        return None, "not a mesh"

    old_mesh = obj.data
    verts_count = len(old_mesh.vertices)
    faces_count = len(old_mesh.polygons)
    if verts_count != 3:
        return None, "not 3 verts"
    if faces_count == 1 and len(old_mesh.polygons[0].vertices) == 3:
        return None, "already ok"

    local_coords = [old_mesh.vertices[i].co.copy() for i in range(3)]
    old_materials = [mat for mat in old_mesh.materials]
    material_index = 0
    use_smooth = False
    if faces_count > 0:
        try:
            material_index = int(old_mesh.polygons[0].material_index)
            use_smooth = bool(old_mesh.polygons[0].use_smooth)
        except Exception:
            material_index = 0

    new_mesh = bpy.data.meshes.new(old_mesh.name)
    new_mesh.from_pydata(local_coords, [], [(0, 1, 2)])
    new_mesh.update()
    for mat in old_materials:
        new_mesh.materials.append(mat)
    if new_mesh.polygons:
        new_mesh.polygons[0].material_index = max(0, min(material_index, max(0, len(old_materials) - 1)))
        new_mesh.polygons[0].use_smooth = use_smooth
    obj.data = new_mesh
    if getattr(old_mesh, "users", 0) == 0:
        try:
            bpy.data.meshes.remove(old_mesh)
        except Exception:
            pass
    return {
        "name": obj.name,
        "old_verts": verts_count,
        "old_faces": faces_count,
    }, ""


class CRAY_OT_FixProxyTriangleMeshes(Operator):
    bl_idname = "cray.fix_proxy_triangle_meshes"
    bl_label = "Fix Proxy Triangles"
    bl_description = (
        "Fix P3D proxy mesh objects that have exactly 3 vertices but not exactly one triangular face"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_assets import (_is_p3d_proxy_object)
        del context
        fixed = []
        skipped = []

        for obj in bpy.data.objects:
            if not _is_p3d_proxy_object(obj):
                continue
            result, reason = _fix_proxy_triangle_mesh(obj)
            if result is not None:
                fixed.append(result)
            else:
                mesh = getattr(obj, "data", None)
                verts_count = len(getattr(mesh, "vertices", []) or []) if mesh is not None else 0
                faces_count = len(getattr(mesh, "polygons", []) or []) if mesh is not None else 0
                skipped.append(
                    {
                        "name": obj.name,
                        "verts": verts_count,
                        "faces": faces_count,
                        "reason": reason,
                    }
                )

        print("")
        print("=== Proxy triangle fixer ===")
        print(f"Fixed: {len(fixed)}")
        for rec in fixed:
            print(
                f"FIXED: {rec['name']} | old verts={rec['old_verts']}, "
                f"old faces={rec['old_faces']} -> new verts=3, new faces=1"
            )

        print("")
        print(f"Skipped: {len(skipped)}")
        for rec in skipped:
            print(
                f"SKIP: {rec['name']} | verts={rec['verts']}, "
                f"faces={rec['faces']} | {rec['reason']}"
            )

        if fixed:
            self.report({"INFO"}, f"Fixed {len(fixed)} proxy triangle mesh(es) (see System Console)")
        else:
            self.report({"INFO"}, "No proxy triangle meshes needed fixing")
        return {"FINISHED"}


class CRAY_OT_RepairP3DSelections(Operator):
    bl_idname = "cray.repair_p3d_selections"
    bl_label = "Repair Invalid P3D Selections"
    bl_description = (
        "Join the selected/broken P3D meshes into one Resolution 0 LOD, rebuild broken vertex-group links, "
        "and place the result under Collection > model.p3d"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_assets import (_repair_invalid_p3d_selection_links)
        from .nh_base import (_fmt_exc)
        from .nh_snap import (_deselect_all_in_view_layer)
        from .nh_textures import (_collect_repair_p3d_scope, _ensure_collection_visible_in_view_layer, _ensure_repair_p3d_root_collection, _is_helper_object_name, _join_meshes_in_batches, _move_object_to_collection, _obj_depth, _remove_empty_subcollections, _remove_helper_named_objects, _resolve_fix_target_object, _set_resolution0_p3d_lod_props, _ui_yield)
        ts = context.scene.cray_texreplace_settings
        target_obj, src = _resolve_fix_target_object(context, ts.picked_object)
        if target_obj is None:
            self.report({"ERROR"}, "Select at least one mesh object")
            return {"CANCELLED"}
        ts.picked_object = target_obj

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as e:
                self.report({"ERROR"}, f"Failed to switch to Object Mode: {_fmt_exc(e)}")
                return {"CANCELLED"}

        scope_objs, scope_src = _collect_repair_p3d_scope(context, target_obj)
        if not scope_objs:
            scope_objs = [target_obj]

        try:
            target_root, source_path = _ensure_repair_p3d_root_collection(context, target_obj, scope_objs)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to prepare .p3d collection: {_fmt_exc(e)}")
            return {"CANCELLED"}

        scope_names = []
        for obj in scope_objs:
            try:
                name = obj.name
            except ReferenceError:
                continue
            if name and name not in scope_names:
                scope_names.append(name)

        all_mesh_candidates = [
            obj for obj in (bpy.data.objects.get(name) for name in scope_names)
            if obj is not None and obj.type == "MESH" and obj.data is not None and len(obj.data.vertices) > 0
        ]
        mesh_candidates = [obj for obj in all_mesh_candidates if not _is_helper_object_name(obj.name)]
        if not mesh_candidates:
            mesh_candidates = all_mesh_candidates
        if not mesh_candidates:
            self.report({"ERROR"}, "No mesh object in selected repair scope")
            return {"CANCELLED"}

        def _mesh_repair_size(obj):
            if obj is None or obj.data is None:
                return 0
            return len(obj.data.polygons) * 1000 + len(obj.data.vertices)

        if target_obj in mesh_candidates and not _is_helper_object_name(target_obj.name):
            anchor_mesh = target_obj
            anchor_src = "target"
        else:
            anchor_mesh = max(mesh_candidates, key=_mesh_repair_size)
            anchor_src = "largest-mesh"
        active_mesh_name = anchor_mesh.name

        try:
            merged_obj, joined_count, join_passes = _join_meshes_in_batches(
                context=context,
                anchor_obj=anchor_mesh,
                mesh_names=[obj.name for obj in mesh_candidates],
                batch_size=ts.fix_mesh_join_batch,
            )
        except Exception as e:
            self.report({"ERROR"}, f"Join failed: {_fmt_exc(e)}")
            return {"CANCELLED"}

        live_scope_names = []
        for name in scope_names:
            live = bpy.data.objects.get(name)
            if live is None or live == merged_obj:
                continue
            live_scope_names.append((_obj_depth(live), name))
        live_scope_names.sort(key=lambda item: item[0], reverse=True)

        deleted_scope = 0
        for idx, (_, name) in enumerate(live_scope_names, start=1):
            live = bpy.data.objects.get(name)
            if live is None or live == merged_obj:
                continue
            try:
                bpy.data.objects.remove(live, do_unlink=True)
                deleted_scope += 1
            except Exception:
                pass
            if idx % 50 == 0:
                _ui_yield()

        try:
            lod_name = _set_resolution0_p3d_lod_props(merged_obj)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        try:
            mesh_world = merged_obj.matrix_world.copy()
            merged_obj.parent = None
            merged_obj.matrix_world = mesh_world
        except Exception:
            pass

        _move_object_to_collection(merged_obj, target_root, unlink_roots=context.scene.collection)
        removed_empty_cols = _remove_empty_subcollections(target_root)

        deleted_helpers, deleted_helper_cols, remaining_helpers = _remove_helper_named_objects(
            scene=context.scene,
            keep_obj=merged_obj,
        )

        try:
            stats = _repair_invalid_p3d_selection_links(merged_obj)
        except Exception as e:
            self.report({"ERROR"}, f"P3D selection repair failed: {_fmt_exc(e)}")
            return {"CANCELLED"}

        _ensure_collection_visible_in_view_layer(context, target_root)
        _deselect_all_in_view_layer(context)
        try:
            merged_obj.select_set(True)
            context.view_layer.objects.active = merged_obj
        except Exception:
            pass

        msg = (
            f"Repaired '{target_root.name}/{lod_name}': joined {joined_count}, "
            f"removed objects {deleted_scope + deleted_helpers}, "
            f"removed empty collections {removed_empty_cols + deleted_helper_cols}, "
            f"invalid refs {stats['invalid_refs_removed']}, zero refs {stats['zero_refs_removed']}, "
            f"rebuilt groups {stats['groups_rebuilt']}"
        )
        extras = [
            f"src: {src}",
            f"scope: {scope_src}",
            f"anchor: {active_mesh_name}",
            f"anchor_src: {anchor_src}",
            f"join_passes: {join_passes}",
        ]
        if source_path:
            extras.append(f"source_path: {source_path}")
        if remaining_helpers:
            extras.append(f"remaining_helpers: {len(remaining_helpers)}")
        self.report({"INFO"}, msg + f", {', '.join(extras)}")
        return {"FINISHED"}


class CRAY_OT_CreatePlainAxisPivot(Operator):
    bl_idname = "cray.create_plain_axis_pivot"
    bl_label = "Create Plain Axis Pivot"
    bl_description = (
        "Р’ Edit Mode Р±РµСЂС‘С‚ РѕРґРЅСѓ РІС‹РґРµР»РµРЅРЅСѓСЋ РІРµСЂС€РёРЅСѓ РєР°Рє pivot, СЃРѕР·РґР°С‘С‚ Plain Axes helper Рё РґРѕР±Р°РІР»СЏРµС‚ Child Of constraints, С‡С‚РѕР±С‹ helper РґРІРёРіР°Р» РІСЃСЋ РёРјРїРѕСЂС‚РёСЂРѕРІР°РЅРЅСѓСЋ РјРѕРґРµР»СЊ"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_PLAIN_AXIS_CONSTRAINT_NAME, _fmt_exc)
        from .nh_collider import (_collect_single_selected_vertex_world_point, _try_restore_edit_mode)
        from .nh_textures import (_apply_child_of_inverse_with_fallback, _clear_plain_axis_helpers, _clear_plain_axis_helpers_in_collection, _collect_plain_axis_target_objects, _create_plain_axis_helper, _pick_plain_axis_root_collection, _set_plain_axis_constraint_axes)
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

            for obj in target_objects:
                try:
                    con = obj.constraints.new(type="CHILD_OF")
                    con.name = _PLAIN_AXIS_CONSTRAINT_NAME
                    con.target = helper_obj
                    _set_plain_axis_constraint_axes(con)
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
    bl_description = "РЈРґР°Р»СЏРµС‚ РІСЃРµ Plain Axes helper-С‹, СЃРѕР·РґР°РЅРЅС‹Рµ СЌС‚РѕР№ РєРЅРѕРїРєРѕР№, Рё СЃРЅРёРјР°РµС‚ РёС… Child Of constraints"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider import (_try_restore_edit_mode)
        from .nh_textures import (_clear_plain_axis_helpers, _is_plain_axis_helper)
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


class CRAY_OT_ClearPlainAxisPivotsKeepZ(Operator):
    bl_idname = "cray.clear_plain_axis_pivots_keep_z"
    bl_label = "Delete All Plain Axes + Save Z"
    bl_description = (
        "РЈРґР°Р»СЏРµС‚ РІСЃРµ Plain Axes helper-С‹, РІРѕР·РІСЂР°С‰Р°РµС‚ РјРѕРґРµР»Рё РїРѕ X/Y РєР°Рє РїСЂРё РѕР±С‹С‡РЅРѕРј СѓРґР°Р»РµРЅРёРё, "
        "РЅРѕ СЃРѕС…СЂР°РЅСЏРµС‚ С‚РµРєСѓС‰СѓСЋ РјРёСЂРѕРІСѓСЋ РІС‹СЃРѕС‚Сѓ Z РґР»СЏ РѕР±СЉРµРєС‚РѕРІ РєРѕР»Р»РµРєС†РёР№"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider import (_try_restore_edit_mode)
        from .nh_textures import (_clear_plain_axis_helpers_keep_world_z, _is_plain_axis_helper)
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

        removed_helpers, removed_constraints = _clear_plain_axis_helpers_keep_world_z(context, helpers)

        if restore_edit_mode:
            _try_restore_edit_mode(context, active_before)

        self.report(
            {"INFO"},
            (
                f"Deleted {removed_helpers} Plain Axis helper(s), removed {removed_constraints} "
                "Child Of constraint(s), and saved current world Z"
            ),
        )
        return {"FINISHED"}


class CRAY_OT_IE_ExportCollectionsBatch(Operator):
    bl_idname = "cray.ie_export_collections_batch"
    bl_label = "Batch Export Collections (P3D)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_snap import (_P3D_EXPORT_CANDIDATES, _call_export_with_optional_relaxed_validation, _collect_expected_lod_entries, _collect_export_loose_vertex_warnings, _collect_export_ngon_issues, _collect_resolution_lod_index_conflicts, _deselect_all_in_view_layer, _discard_pending_export_backup, _finalize_export_backup, _iter_p3d_root_collections, _op_handle, _read_exported_lod_entries, _report_export_backup_preserved_in_console, _report_export_backup_skipped_in_console, _report_export_backup_updated_in_console, _report_export_loose_vertex_warnings_in_console, _report_export_ngon_issues_in_console, _report_missing_lod_diagnostics_in_console, _report_missing_lods_in_console, _report_resolution_lod_index_conflicts_in_console, _restore_p3d_named_properties_after_export, _restore_collision_lod_materials_after_export, _stage_export_backup, _strip_p3d_named_properties_for_export, _strip_collision_lod_materials_for_export)
        from .nh_textures import (_build_ie_import_basename_map, _collect_collection_objects_recursive, _collection_has_any_mesh, _ensure_collection_visible_in_view_layer, _export_filename_for_collection, _looks_like_p3d_collection_name, _looks_like_split_part_collection_name, _resolve_collection_source_path)
        st = context.scene.cray_ie_settings
        tex_settings = context.scene.cray_texreplace_settings
        warn_loose_vertices = bool(getattr(tex_settings, "export_warn_loose_vertices", True))
        has_export = any(_op_handle(op) is not None for op, _ in _P3D_EXPORT_CANDIDATES)
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

        candidate_roots = []
        seen_candidate_ptrs = set()
        for col in list(context.scene.collection.children) + list(_iter_p3d_root_collections(context.scene)):
            if col is None:
                continue
            try:
                ptr = col.as_pointer()
            except Exception:
                ptr = None
            if ptr in seen_candidate_ptrs:
                continue
            if ptr is not None:
                seen_candidate_ptrs.add(ptr)
            candidate_roots.append(col)

        candidates = []
        for col in candidate_roots:
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
        loose_vertex_warnings = []
        backup_skipped = []
        backup_preserved = []
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

            resolution_conflicts = _collect_resolution_lod_index_conflicts(col, objects)
            if resolution_conflicts:
                _report_resolution_lod_index_conflicts_in_console(col.name, filepath, resolution_conflicts)
                if not bool(st.export_force_all_lods):
                    failed.append(
                        f"{col.name} -> duplicate Resolution LOD index in one logical collection path "
                        f"(see System Console)"
                    )
                    continue
                print(
                    "WARNING: Force export all LODs is ON, continuing despite duplicate "
                    "Resolution LOD index conflict(s)."
                )

            ngon_issues = _collect_export_ngon_issues(col, objects)
            if ngon_issues:
                _report_export_ngon_issues_in_console(col.name, filepath, ngon_issues)
                if not bool(st.export_force_all_lods):
                    first_path = ngon_issues[0].get("display_path") or ngon_issues[0].get("mesh_object_name", "<unknown>")
                    failed.append(f"{first_path} has n-gons (see System Console)")
                    continue
                print("WARNING: Force export all LODs is ON, continuing despite n-gon issue(s).")

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

            if warn_loose_vertices:
                loose_warnings = _collect_export_loose_vertex_warnings(col, selectable)
                if loose_warnings:
                    _report_export_loose_vertex_warnings_in_console(col.name, filepath, loose_warnings)
                    loose_vertex_warnings.append((col.name, filepath, len(loose_warnings)))

            expected_lod_entries = _collect_expected_lod_entries(selectable)

            pending_backup_path = ""
            if st.export_create_bak and os.path.isfile(filepath):
                pending_backup_path, backup_stage_err = _stage_export_backup(filepath)
                if backup_stage_err:
                    failed.append(f"{col.name} -> backup stage failed: {backup_stage_err}")
                    continue

            material_restore = {}
            named_property_restore = {}
            try:
                material_restore = _strip_collision_lod_materials_for_export(selectable)
                named_property_restore = _strip_p3d_named_properties_for_export(selectable)
            except Exception as e:
                if pending_backup_path:
                    try:
                        _discard_pending_export_backup(pending_backup_path)
                    except Exception:
                        pass
                failed.append(f"{col.name} -> prep failed: {_fmt_exc(e)}")
                continue
            try:
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
                    lod_collisions="IGNORE" if bool(st.export_force_all_lods) else "SKIP",
                    validate_lods=False,
                    validate_lods_warning_errors=False,
                    generate_components=True,
                    renumber_components=True,
                    translate_selections=False,
                    force_lowercase=True,
                )
            finally:
                _restore_p3d_named_properties_after_export(named_property_restore)
                _restore_collision_lod_materials_after_export(material_restore)
            export_missing_keys = []
            lod_post_check_failed = False
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
                            export_missing_keys = list(missing_keys)
                            _report_missing_lod_diagnostics_in_console(
                                context=context,
                                collection_name=col.name,
                                filepath=filepath,
                                missing_keys=missing_keys,
                                expected_entries=expected_lod_entries,
                                export_objects=selectable,
                                force_all_lods=bool(st.export_force_all_lods),
                            )
                            partial_lod_exports.append((col.name, filepath, len(missing_keys), len(expected_lod_entries)))
                    except Exception as e:
                        lod_post_check_failed = True
                        print("=== Batch Export Collections: LOD post-check failed ===")
                        print(f"{col.name} -> {_fmt_exc(e)}")
            else:
                failed.append(f"{col.name} -> {_fmt_exc(err) if err else 'export failed'}")

            if pending_backup_path:
                export_complete = bool(op_id) and not export_missing_keys and not lod_post_check_failed
                try:
                    backup_status, backup_reason, backup_missing_keys = _finalize_export_backup(
                        filepath,
                        pending_backup_path,
                        expected_lod_entries,
                        export_complete,
                    )
                except Exception as e:
                    backup_status = "skipped"
                    backup_reason = f"backup finalize failed: {_fmt_exc(e)}"
                    backup_missing_keys = []
                    _discard_pending_export_backup(pending_backup_path)

                if backup_status == "updated":
                    backups += 1
                    _report_export_backup_updated_in_console(
                        col.name,
                        filepath,
                        backup_reason,
                        backup_missing_keys,
                        expected_lod_entries,
                    )
                elif backup_status == "preserved":
                    backup_preserved.append((col.name, filepath, backup_reason))
                    _report_export_backup_preserved_in_console(
                        col.name,
                        filepath,
                        backup_reason,
                        backup_missing_keys,
                        expected_lod_entries,
                    )
                elif backup_status == "skipped":
                    backup_skipped.append((col.name, filepath, backup_reason))
                    _report_export_backup_skipped_in_console(
                        col.name,
                        filepath,
                        backup_reason,
                        backup_missing_keys,
                        expected_lod_entries,
                    )

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
        if loose_vertex_warnings:
            msg += f", loose vertex warnings {len(loose_vertex_warnings)}"
        if backup_skipped:
            msg += f", backups skipped {len(backup_skipped)}"
        if backup_preserved:
            msg += f", backups preserved {len(backup_preserved)}"

        if failed or partial_lod_exports or loose_vertex_warnings or backup_skipped:
            self.report({"WARNING"}, msg + " (see System Console)")
        else:
            suffix = f" via {used_op}" if used_op else ""
            self.report({"INFO"}, msg + suffix)
        return {"FINISHED"}


# ------------------------------------------------------------------------
#  Panels (separate blocks)
# ------------------------------------------------------------------------

from .nh_base import (_UI_PANEL_DEFAULT_ORDER)

class CRAY_PT_ClutterProxiesPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_panel"
    bl_label = "Clutter Proxies (DayZ)"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_order = _UI_PANEL_DEFAULT_ORDER["object_builder"]
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from .nh_scatter import (_is_ui_panel_visible)
        return _is_ui_panel_visible(context, "object_builder")

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

from .nh_base import (_UI_PANEL_DEFAULT_ORDER)

class CRAY_PT_SnapPointsPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_snap_points"
    bl_label = "Snap Points (Memory LOD)"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_order = _UI_PANEL_DEFAULT_ORDER["snap_points"]
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from .nh_scatter import (_is_ui_panel_visible)
        return _is_ui_panel_visible(context, "snap_points")

    def draw(self, context):
        from .nh_base import (_PLAIN_AXIS_HOTKEY_REGISTERED)
        from .nh_snap import (_SnapPointNamePattern, _get_snap_target_object, _snap_target_memory_scope_key)
        layout = self.layout
        ss = context.scene.cray_snap_settings
        preview_pattern = _SnapPointNamePattern.from_preview_settings(ss)
        target_a = _get_snap_target_object(ss, "a", allow_memory_fallback=False)
        target_v = _get_snap_target_object(ss, "v", allow_memory_fallback=False)
        targets_are_meshes = bool(
            target_a is not None and
            target_v is not None and
            target_a.type == "MESH" and
            target_v.type == "MESH" and
            target_a.data is not None and
            target_v.data is not None
        )
        target_scope_a = _snap_target_memory_scope_key(context, target_a)
        target_scope_v = _snap_target_memory_scope_key(context, target_v)
        same_target_scope = bool(target_scope_a is not None and target_scope_a == target_scope_v)
        can_create = bool(
            targets_are_meshes and
            not same_target_scope
        )

        col = layout.column(align=True)
        col.label(text="Target")
        col.prop(ss, "source_object", text="A Target")
        col.separator()
        col.prop(ss, "paired_object", text="V Target")
        col.operator("cray.ensure_memory_lod", text="Create/Find Point clouds > Memory", icon="OUTLINER_OB_MESH")
        visibility_row = col.row(align=True)
        visibility_row.operator("cray.snap_set_p3d_visuals_only", text="Visual 0 Only", icon="HIDE_ON")
        visibility_row.operator("cray.snap_show_all_p3d_collections", text="Show All", icon="HIDE_OFF")

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
        create_row = layout.row()
        create_row.enabled = can_create
        create_row.operator("cray.create_snap_pair_from_model_edge", text="Create Snap Points", icon="MESH_DATA")

        layout.separator()
        pbox = layout.box()
        pbox.label(text="Plain Axes pivot", icon="EMPTY_AXIS")
        create_label = "Create Plain Axis Pivot  [Ctrl+Shift+P]" if _PLAIN_AXIS_HOTKEY_REGISTERED else "Create Plain Axis Pivot"
        pbox.operator("cray.create_plain_axis_pivot", text=create_label, icon="EMPTY_AXIS")
        pbox.operator("cray.clear_plain_axis_pivots", text="Delete All Plain Axes", icon="TRASH")
        pbox.operator("cray.clear_plain_axis_pivots_keep_z", text="Delete All Plain Axes + Save Z", icon="TRASH")
