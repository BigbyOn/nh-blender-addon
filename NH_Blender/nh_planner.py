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

# nh_planner.py
# auto-split slice; cross-module refs resolved with in-function imports

def _patch_p3d_import_read_file():
    from .nh_base import (_fmt_exc)
    from .nh_collider_exp import (_norm_path)
    from .nh_snap import (_P3D_IMPORT_READ_FILE_PATCHES, _P3D_IMPORT_TRACKING_SUPPRESS_DEPTH, _import_first_available_module)
    from .nh_textures import (_get_import_preview_settings, _log_import_preview_summary, _planner_add_import_file, _postprocess_imported_material_previews, _tag_import_source_on_imported_data)
    if _P3D_IMPORT_READ_FILE_PATCHES:
        return

    module_names = (
        "bl_ext.user_default.Arma3ObjectBuilder.io.import_p3d",
        "NH_bundle.io.import_p3d",
    )

    for module_name in module_names:
        mod = _import_first_available_module((module_name,))
        if mod is None:
            continue

        original_read_file = getattr(mod, "read_file", None)
        if not callable(original_read_file):
            continue
        if any(patched_mod is mod for patched_mod, _ in _P3D_IMPORT_READ_FILE_PATCHES):
            continue

        def _wrapped_read_file(operator, context, file, _original_read_file=original_read_file, _module_name=module_name):
            from .nh_base import (_fmt_exc)
            from .nh_collider_exp import (_norm_path)
            from .nh_snap import (_P3D_IMPORT_TRACKING_SUPPRESS_DEPTH)
            from .nh_textures import (_get_import_preview_settings, _log_import_preview_summary, _planner_add_import_file, _postprocess_imported_material_previews, _tag_import_source_on_imported_data)
            filepath = _norm_path(bpy.path.abspath(getattr(operator, "filepath", "")))
            pre_col_ptrs = {col.as_pointer() for col in bpy.data.collections}

            lod_objects = _original_read_file(operator, context, file)

            if _P3D_IMPORT_TRACKING_SUPPRESS_DEPTH > 0:
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
                print("=== Import/Export planner: failed to tag P3D import ===")
                print(f"{_module_name} -> {_fmt_exc(e)}")

            try:
                scene = getattr(context, "scene", None)
                settings = getattr(scene, "cray_ie_settings", None) if scene is not None else None
                _planner_add_import_file(settings, filepath)
            except Exception as e:
                print("=== Import/Export planner: failed to add P3D import to planner ===")
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
        _P3D_IMPORT_READ_FILE_PATCHES.append((mod, original_read_file))


def _unpatch_p3d_import_read_file():
    from .nh_snap import (_P3D_IMPORT_READ_FILE_PATCHES)
    while _P3D_IMPORT_READ_FILE_PATCHES:
        mod, original_read_file = _P3D_IMPORT_READ_FILE_PATCHES.pop()
        try:
            mod.read_file = original_read_file
        except Exception:
            pass


def _ensure_p3d_import_patch_timer():
    from .nh_snap import (_P3D_IMPORT_READ_FILE_PATCHES)
    if _P3D_IMPORT_READ_FILE_PATCHES:
        return None

    _patch_p3d_import_read_file()
    if _P3D_IMPORT_READ_FILE_PATCHES:
        return None

    return 2.0


def _ensure_p3d_p3d_file_handler_patch_timer():
    from .nh_snap import (_P3D_P3D_FILE_HANDLER_PATCHES, _patch_p3d_p3d_file_handler)
    if _P3D_P3D_FILE_HANDLER_PATCHES:
        return None

    if _patch_p3d_p3d_file_handler():
        return None

    return 2.0

class CRAY_PG_IEFileItem(PropertyGroup):
    path: StringProperty(name="File", default="", subtype="FILE_PATH")

class CRAY_PG_IEPlannerSettings(PropertyGroup):
    import_files: CollectionProperty(type=CRAY_PG_IEFileItem)
    import_active_index: IntProperty(default=0)
    quick_add_p3d_name: StringProperty(
        name="P3D Name",
        default="",
        description="Type a model name like darkvalley_brick_farm_a and add the matching .p3d from NH_Objects",
    )
    quick_add_search_root: StringProperty(
        name="NH_Objects Root",
        default=r"P:\NH_Objects",
        subtype="DIR_PATH",
        description="Root folder where the addon searches for .p3d files by name",
    )
    import_show_materials: BoolProperty(
        name="Show material textures after import",
        default=True,
        description="Create Image Texture preview nodes for imported P3D materials so textures are visible in Blender immediately",
    )
    import_keep_converted_textures: BoolProperty(
        name="Use shared .paa -> .png cache",
        default=True,
        description="Save/reuse Blender-friendly PNG copies in the shared NH texture preview cache instead of decoding .paa again",
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
            "Workaround for P3D exporter: temporarily bypass LOD validation "
            "during batch export to prevent Resolution LODs from being skipped"
        ),
    )

class CRAY_OT_IEFilePathTooltip(Operator):
    bl_idname = "cray.ie_file_path_tooltip"
    bl_label = "Import File"
    bl_options = {"INTERNAL"}

    filepath: StringProperty(options={"HIDDEN"})

    @classmethod
    def description(cls, context, properties):
        from .nh_collider_exp import (_norm_path)
        del context
        return _norm_path(getattr(properties, "filepath", "") or "")

    def execute(self, context):
        del context
        return {"FINISHED"}


class CRAY_UL_IEFiles(UIList):
    bl_idname = "CRAY_UL_ie_files"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        from .nh_collider_exp import (_norm_path)
        del context, data, icon, active_data, active_propname, index
        filepath = _norm_path(item.path)
        label = os.path.basename(filepath) or "<empty>"
        layout.label(text=label, icon="FILE")

class CRAY_OT_IE_AddFiles(Operator):
    bl_idname = "cray.ie_add_files"
    bl_label = "Add Files"
    bl_options = {"REGISTER", "UNDO"}

    files: CollectionProperty(type=OperatorFileListElement)
    directory: StringProperty(subtype="DIR_PATH")
    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN"})
    filter_glob: StringProperty(default="*.p3d", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from .nh_textures import (_planner_add_import_files_from_operator)
        st = context.scene.cray_ie_settings
        added, skipped, skipped_non_p3d = _planner_add_import_files_from_operator(
            st,
            directory=getattr(self, "directory", ""),
            files=getattr(self, "files", ()),
            filepath=getattr(self, "filepath", ""),
        )

        if added == 0:
            if skipped > 0 and skipped_non_p3d == 0:
                self.report({"WARNING"}, "All selected files are already in the planner")
            elif skipped_non_p3d > 0:
                self.report({"WARNING"}, "No .p3d files added")
            else:
                self.report({"WARNING"}, "No files added")
        else:
            msg = f"Added {added} file(s)"
            if skipped > 0:
                msg += f", skipped {skipped} duplicate(s)"
            if skipped_non_p3d > 0:
                msg += f", skipped {skipped_non_p3d} non-.p3d file(s)"
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class CRAY_OT_P3DDropMenu(Operator):
    bl_idname = "cray.p3d_drop_menu"
    bl_label = "P3D Drop"
    bl_description = "Add dropped .p3d files to the Import/Export planner"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(subtype="DIR_PATH", options={"SKIP_SAVE", "HIDDEN"})
    files: CollectionProperty(type=OperatorFileListElement, options={"SKIP_SAVE", "HIDDEN"})
    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE", "HIDDEN"})

    def invoke(self, context, event):
        del event
        return self.execute(context)

    def execute(self, context):
        from .nh_textures import (_collect_p3d_filepaths_from_operator, _planner_add_import_file, _set_pending_p3d_drop_paths)
        paths = _collect_p3d_filepaths_from_operator(
            directory=getattr(self, "directory", ""),
            files=getattr(self, "files", ()),
            filepath=getattr(self, "filepath", ""),
        )
        if not paths:
            self.report({"WARNING"}, "No .p3d files dropped")
            return {"CANCELLED"}

        st = getattr(context.scene, "cray_ie_settings", None)
        if st is None:
            self.report({"ERROR"}, "Import/Export planner settings are not available")
            return {"CANCELLED"}

        added = 0
        skipped = 0
        for fp in paths:
            if _planner_add_import_file(st, fp):
                added += 1
            else:
                skipped += 1
        _set_pending_p3d_drop_paths([])
        self.report({"INFO"}, f"Added {added} dropped .p3d file(s), skipped {skipped} duplicate(s)")
        return {"FINISHED"}


class CRAY_MT_P3DDropMenu(Menu):
    bl_idname = "CRAY_MT_p3d_drop_menu"
    bl_label = "P3D Drop"

    def draw(self, context):
        from .nh_textures import (_pending_p3d_drop_label)
        del context
        layout = self.layout
        layout.label(text=_pending_p3d_drop_label(), icon="FILE")
        layout.separator()
        layout.operator("cray.p3d_drop_add_to_planner", text="Add to Import/Export planner", icon="ADD")
        layout.operator("cray.p3d_drop_import_now", text="Import now (P3D)", icon="IMPORT")


class CRAY_OT_P3DDropAddToPlanner(Operator):
    bl_idname = "cray.p3d_drop_add_to_planner"
    bl_label = "Add Dropped P3D To Planner"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_snap import (_P3D_DROP_PENDING_PATHS)
        from .nh_textures import (_planner_add_import_file, _set_pending_p3d_drop_paths)
        paths = list(_P3D_DROP_PENDING_PATHS)
        if not paths:
            self.report({"ERROR"}, "No dropped .p3d files are pending")
            return {"CANCELLED"}

        st = context.scene.cray_ie_settings
        added = 0
        skipped = 0
        for fp in paths:
            if _planner_add_import_file(st, fp):
                added += 1
            else:
                skipped += 1
        _set_pending_p3d_drop_paths([])
        self.report({"INFO"}, f"Added {added} .p3d file(s) to planner, skipped {skipped} duplicate(s)")
        return {"FINISHED"}


class CRAY_OT_P3DDropImportNow(Operator):
    bl_idname = "cray.p3d_drop_import_now"
    bl_label = "Import Dropped P3D Now"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_snap import (_P3D_DROP_PENDING_PATHS)
        from .nh_textures import (_import_p3d_paths_now, _set_pending_p3d_drop_paths)
        paths = list(_P3D_DROP_PENDING_PATHS)
        if not paths:
            self.report({"ERROR"}, "No dropped .p3d files are pending")
            return {"CANCELLED"}
        _set_pending_p3d_drop_paths([])
        return _import_p3d_paths_now(self, context, paths)


class CRAY_OT_IE_AddByName(Operator):
    bl_idname = "cray.ie_add_by_name"
    bl_label = "Add By Name"
    bl_description = "Find a .p3d in NH_Objects by model name and add it to the import list"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_textures import (_display_p3d_name, _find_existing_scene_p3d_root, _find_p3d_paths_by_name, _normalize_p3d_lookup_key, _planner_add_import_file)
        st = context.scene.cray_ie_settings

        model_name = (getattr(st, "quick_add_p3d_name", "") or "").strip()
        model_key = _normalize_p3d_lookup_key(model_name)
        if not model_key:
            self.report({"ERROR"}, "Type a .p3d name like darkvalley_brick_farm_a")
            return {"CANCELLED"}

        search_root = bpy.path.abspath(getattr(st, "quick_add_search_root", "") or "")
        search_root = os.path.abspath(search_root) if search_root else ""
        if not search_root or not os.path.isdir(search_root):
            self.report({"ERROR"}, "NH_Objects root folder was not found")
            return {"CANCELLED"}

        existing_root = _find_existing_scene_p3d_root(context.scene, model_key)
        if existing_root is not None:
            self.report({"WARNING"}, f"{existing_root.name} is already imported in the scene")
            return {"CANCELLED"}

        matches = _find_p3d_paths_by_name(search_root, model_key)
        if not matches:
            self.report({"ERROR"}, f"{_display_p3d_name(model_key)} was not found in {search_root}")
            return {"CANCELLED"}

        chosen = matches[0]
        added = _planner_add_import_file(st, chosen)
        st.quick_add_p3d_name = model_key

        if not added:
            self.report({"WARNING"}, f"{os.path.basename(chosen)} is already in the import list")
            return {"CANCELLED"}

        if len(matches) > 1:
            print("=== Batch Import: multiple .p3d matches found ===")
            print(f"Requested: {model_key}")
            for path in matches:
                print(path)
            self.report(
                {"WARNING"},
                (
                    f"Added {os.path.basename(chosen)}; found {len(matches)} matches, "
                    f"used the first one (see System Console)"
                ),
            )
            return {"FINISHED"}

        self.report({"INFO"}, f"Added {os.path.basename(chosen)} to the import list")
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

def _planner_path_for_collection_name(collection, import_basename_map):
    from .nh_textures import (_display_p3d_name, _normalize_p3d_lookup_key)
    if collection is None or not import_basename_map:
        return ""
    key = _display_p3d_name(_normalize_p3d_lookup_key(getattr(collection, "name", "") or "")).lower()
    return import_basename_map.get(key, "")

def _set_ie_source_path_tag_recursive(root_collection, source_path: str):
    from .nh_textures import (_IE_SOURCE_PATH_KEY, _collect_collection_objects_recursive, _iter_collection_tree, _set_ie_source_path_tag)
    if root_collection is None or not source_path:
        return
    for collection in _iter_collection_tree(root_collection):
        try:
            if collection == root_collection or _IE_SOURCE_PATH_KEY in collection:
                _set_ie_source_path_tag(collection, source_path)
        except Exception:
            pass
    for obj in _collect_collection_objects_recursive(root_collection):
        try:
            if _IE_SOURCE_PATH_KEY in obj:
                _set_ie_source_path_tag(obj, source_path)
        except Exception:
            pass

def _refresh_ie_scene_source_tags_from_collection_names(context, settings):
    from .nh_collider_exp import (_norm_path)
    from .nh_snap import (_iter_p3d_root_collections)
    from .nh_textures import (_build_ie_import_basename_map, _resolve_collection_source_path)
    import_basename_map = _build_ie_import_basename_map(settings)
    retagged = 0
    for root in _iter_p3d_root_collections(getattr(context, "scene", None)):
        wanted_path = _planner_path_for_collection_name(root, import_basename_map)
        if not wanted_path:
            continue
        current_path = _resolve_collection_source_path(root)
        if _norm_path(current_path) == _norm_path(wanted_path):
            continue
        _set_ie_source_path_tag_recursive(root, wanted_path)
        retagged += 1
    return retagged

def _current_scene_p3d_planner_keys(context, settings):
    from .nh_collider_exp import (_norm_path)
    from .nh_snap import (_iter_p3d_root_collections)
    from .nh_textures import (_build_ie_import_basename_map, _normalize_p3d_lookup_key, _resolve_collection_source_path)
    import_basename_map = _build_ie_import_basename_map(settings)
    scene_paths = set()
    scene_names = set()
    roots = list(_iter_p3d_root_collections(getattr(context, "scene", None)))
    for root in roots:
        name_key = _normalize_p3d_lookup_key(getattr(root, "name", "") or "")
        if name_key:
            scene_names.add(name_key)
        source_path = _resolve_collection_source_path(root, import_basename_map)
        source_path = _norm_path(bpy.path.abspath(source_path)) if source_path else ""
        if source_path:
            scene_paths.add(source_path)
            source_key = _normalize_p3d_lookup_key(source_path)
            if source_key:
                scene_names.add(source_key)
    return roots, scene_paths, scene_names

def _ie_add_unique_p3d_root(roots, seen, context, collection):
    from .nh_textures import (_find_p3d_root_collection_for_collection, _model_split_add_unique_collection)
    root = _find_p3d_root_collection_for_collection(context, collection, require_p3d=True)
    if root is None:
        return False
    return _model_split_add_unique_collection(roots, seen, root)

def _ie_selected_p3d_root_collections(context):
    from .nh_scatter import (_model_split_merge_collection_sort_key)
    from .nh_textures import (_find_p3d_root_collection_for_object, _model_split_add_unique_collection)
    roots = []
    seen = set()

    for item in getattr(context, "selected_ids", []) or []:
        if isinstance(item, bpy.types.Collection):
            _ie_add_unique_p3d_root(roots, seen, context, item)

    for obj in list(getattr(context, "selected_objects", []) or []):
        root = _find_p3d_root_collection_for_object(context, obj)
        if root is not None:
            _model_split_add_unique_collection(roots, seen, root)

    active_obj = getattr(getattr(context, "view_layer", None), "objects", None)
    active_obj = getattr(active_obj, "active", None) if active_obj is not None else None
    if active_obj is not None:
        root = _find_p3d_root_collection_for_object(context, active_obj)
        if root is not None:
            _model_split_add_unique_collection(roots, seen, root)

    active_layer = getattr(getattr(context, "view_layer", None), "active_layer_collection", None)
    active_collection = getattr(active_layer, "collection", None) if active_layer is not None else None
    if active_collection is not None:
        _ie_add_unique_p3d_root(roots, seen, context, active_collection)

    context_collection = getattr(context, "collection", None)
    if context_collection is not None:
        _ie_add_unique_p3d_root(roots, seen, context, context_collection)

    roots.sort(key=_model_split_merge_collection_sort_key)
    return roots

def _ie_resolve_export_path_for_collection(context, settings, collection):
    from .nh_collider_exp import (_norm_path)
    from .nh_textures import (_build_ie_import_basename_map, _display_p3d_name, _find_p3d_paths_by_name, _normalize_p3d_lookup_key, _resolve_collection_source_path)
    name_key = _normalize_p3d_lookup_key(getattr(collection, "name", "") or "")
    filename = _display_p3d_name(name_key)
    if not filename:
        return "", "collection name is not a .p3d name"

    import_basename_map = _build_ie_import_basename_map(settings)
    mapped = _planner_path_for_collection_name(collection, import_basename_map)
    if mapped:
        return mapped, ""

    search_root = bpy.path.abspath(getattr(settings, "quick_add_search_root", "") or "")
    matches = _find_p3d_paths_by_name(search_root, name_key) if search_root else []
    if matches:
        return matches[0], ""

    current_source = _resolve_collection_source_path(collection)
    current_source = _norm_path(bpy.path.abspath(current_source)) if current_source else ""
    if current_source:
        source_dir = os.path.dirname(current_source)
        if source_dir:
            return _norm_path(os.path.join(source_dir, filename)), ""

    if getattr(settings, "export_mode", "") == "CUSTOM_DIR":
        export_dir = bpy.path.abspath(getattr(settings, "export_directory", "") or "")
        if export_dir:
            return _norm_path(os.path.join(export_dir, filename)), ""

    return "", "no source path found; import once, use + to select a .p3d file, or set Custom folder"

def _ie_sync_p3d_root_collection_to_planner(context, settings, root):
    from .nh_collider_exp import (_norm_path)
    from .nh_textures import (_planner_add_import_file, _resolve_collection_source_path)
    path, reason = _ie_resolve_export_path_for_collection(context, settings, root)
    if not path:
        return False, False, reason

    previous = _resolve_collection_source_path(root)
    _set_ie_source_path_tag_recursive(root, path)
    added = _planner_add_import_file(settings, path)
    retargeted = _norm_path(previous) != _norm_path(path)
    return added, retargeted, ""

class CRAY_OT_IE_AddSelectedCollections(Operator):
    bl_idname = "cray.ie_add_selected_collections"
    bl_label = "Add Selected Collection"
    bl_description = "Add selected .p3d root collection(s) to the planner and retarget Back to source by collection name"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = context.scene.cray_ie_settings
        roots = _ie_selected_p3d_root_collections(context)
        if not roots:
            self.report({"ERROR"}, "Select a .p3d root collection in Outliner or an object inside it")
            return {"CANCELLED"}

        added = 0
        retargeted = 0
        failed = []
        for root in roots:
            was_added, was_retargeted, reason = _ie_sync_p3d_root_collection_to_planner(context, st, root)
            if reason:
                failed.append(f"{getattr(root, 'name', '<collection>')} -> {reason}")
                continue
            if was_added:
                added += 1
            if was_retargeted:
                retargeted += 1

        if failed:
            print("=== Import/Export planner: Add Selected Collection failures ===")
            for item in failed:
                print(item)

        if added or retargeted:
            msg = f"Added {added}, retargeted {retargeted} selected .p3d collection(s)"
            if failed:
                self.report({"WARNING"}, msg + f", failed {len(failed)} (see System Console)")
            else:
                self.report({"INFO"}, msg)
            return {"FINISHED"}

        if failed:
            self.report({"ERROR"}, f"Failed to add selected collection(s), see System Console")
            return {"CANCELLED"}

        self.report({"INFO"}, "Selected collection(s) already in planner")
        return {"FINISHED"}

class CRAY_OT_IE_RefreshFiles(Operator):
    bl_idname = "cray.ie_refresh_files"
    bl_label = "Refresh"
    bl_description = (
        "Remove planner entries whose .p3d root collection is no longer in the scene "
        "and retarget renamed .p3d collections by name"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_collider_exp import (_norm_path)
        from .nh_scatter import (_model_split_merge_collection_sort_key)
        from .nh_snap import (_iter_p3d_root_collections)
        from .nh_textures import (_normalize_p3d_lookup_key)
        st = context.scene.cray_ie_settings
        roots = list(_iter_p3d_root_collections(getattr(context, "scene", None)))
        if not roots:
            self.report({"WARNING"}, "No .p3d root collections in the scene; planner list unchanged")
            return {"CANCELLED"}

        roots.sort(key=_model_split_merge_collection_sort_key)
        added = 0
        retargeted = 0
        failed = []
        for root in roots:
            was_added, was_retargeted, reason = _ie_sync_p3d_root_collection_to_planner(context, st, root)
            if reason:
                failed.append(f"{getattr(root, 'name', '<collection>')} -> {reason}")
                continue
            if was_added:
                added += 1
            if was_retargeted:
                retargeted += 1

        _refresh_ie_scene_source_tags_from_collection_names(context, st)
        roots, scene_paths, scene_names = _current_scene_p3d_planner_keys(context, st)

        removed = 0
        for idx in range(len(st.import_files) - 1, -1, -1):
            item = st.import_files[idx]
            fp = _norm_path(bpy.path.abspath(item.path)) if item.path else ""
            name_key = _normalize_p3d_lookup_key(fp)
            if fp in scene_paths or name_key in scene_names:
                continue
            st.import_files.remove(idx)
            removed += 1

        st.import_active_index = max(0, min(int(getattr(st, "import_active_index", 0) or 0), len(st.import_files) - 1))
        if failed:
            print("=== Import/Export planner: Refresh skipped collections ===")
            for item in failed:
                print(item)
        msg = f"Refreshed planner: added {added}, removed {removed}, retargeted {retargeted}"
        if failed:
            self.report({"WARNING"}, msg + f", skipped {len(failed)} (see System Console)")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}

class CRAY_OT_IE_ImportBatch(Operator):
    bl_idname = "cray.ie_import_batch"
    bl_label = "Batch Import (P3D)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_snap import (_P3D_IMPORT_CANDIDATES, _call_first_available, _has_any_p3d_import_ops, _suppress_p3d_import_tracking)
        from .nh_textures import (_disable_all_collections_in_view_layer, _find_existing_scene_p3d_root, _log_import_preview_summary, _postprocess_imported_material_previews, _tag_import_source_on_imported_data)
        st = context.scene.cray_ie_settings
        if len(st.import_files) == 0:
            self.report({"ERROR"}, "Import list is empty")
            return {"CANCELLED"}
        if not _has_any_p3d_import_ops():
            self.report({"ERROR"}, "Arma 3 Object Builder import operators not found")
            return {"CANCELLED"}

        imported = 0
        skipped_existing = []
        failed = []
        used_op = None

        for it in st.import_files:
            fp = bpy.path.abspath(it.path)
            existing_root = _find_existing_scene_p3d_root(context.scene, fp)
            if existing_root is not None:
                skipped_existing.append(f"{os.path.basename(fp)} -> already imported as {existing_root.name}")
                continue
            if not fp or not os.path.isfile(fp):
                failed.append(f"{it.path} -> file not found")
                continue

            pre_obj_ptrs = {o.as_pointer() for o in bpy.data.objects}
            pre_col_ptrs = {c.as_pointer() for c in bpy.data.collections}
            with _suppress_p3d_import_tracking():
                res, op_id, err = _call_first_available(
                    _P3D_IMPORT_CANDIDATES,
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

        if skipped_existing:
            print("=== Batch Import: Skipped already imported ===")
            for item in skipped_existing:
                print(item)

        if failed:
            print("=== Batch Import: Failures ===")
            for f in failed:
                print(f)
            msg = f"Imported {imported}, skipped existing {len(skipped_existing)}, failed {len(failed)}"
            self.report({"WARNING"}, msg + " (see System Console)")
        else:
            msg = f"Imported {imported} file(s), skipped existing {len(skipped_existing)}"
            self.report({"INFO"}, msg + (f" via {used_op}" if used_op else ""))
        return {"FINISHED"}

class CRAY_OT_ModelSplitTransferSelectedToTargetCategory(Operator):
    bl_idname = "cray.model_split_transfer_to_target_category"
    bl_label = "Move/Copy Selected To Target Category"
    bl_options = {"REGISTER", "UNDO"}

    transfer_mode: EnumProperty(
        name="Action",
        items=(
            ("MOVE", "Move", "Move selected mesh objects into the target part model"),
            ("COPY", "Copy", "Copy selected mesh objects into the target part model"),
        ),
        default="MOVE",
    )

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_textures import (_add_model_split_part_to_planner, _direct_object_collection_names_under_root, _duplicate_object_for_split, _ensure_model_split_target_category_collection, _focus_created_split_objects, _model_split_category_for_object, _model_split_source_and_objects_for_transfer, _model_split_target_category_label, _move_object_to_collection, _prepare_moved_objects_for_named_split, _resolve_model_split_target_part_root, _rewire_split_copy_object_refs, _same_id_data, _set_model_split_target_lod_p3d_props)
        st = context.scene.cray_model_split_settings
        requested_mode = str(getattr(self, "transfer_mode", "MOVE") or "MOVE").upper()
        if requested_mode != "COPY":
            requested_mode = "MOVE"

        try:
            source_root, selected, separated_from_edit = _model_split_source_and_objects_for_transfer(
                context,
                st,
                copy_selection=(requested_mode == "COPY"),
            )
            target_root = _resolve_model_split_target_part_root(context, st, source_root, force_new=False)
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        effective_mode = requested_mode
        if separated_from_edit:
            effective_mode = "MOVE"

        if effective_mode == "MOVE":
            _prepare_moved_objects_for_named_split(selected)

        created = []
        failed = []
        copies_by_source = {}
        used_categories = set()

        for src_obj in selected:
            dup_obj = None
            try:
                category_token = _model_split_category_for_object(src_obj)
                dest_leaf = _ensure_model_split_target_category_collection(target_root, category_token)
                if dest_leaf is None:
                    failed.append(f"{src_obj.name} -> failed to create P3D category collection")
                    continue

                if effective_mode == "COPY":
                    dup_obj = _duplicate_object_for_split(src_obj)
                    if dup_obj is None:
                        failed.append(f"{src_obj.name} -> failed to duplicate object")
                        continue
                    _set_model_split_target_lod_p3d_props(dup_obj, category_token)
                    dest_leaf.objects.link(dup_obj)
                    copies_by_source[src_obj] = dup_obj
                    used_categories.add(_model_split_target_category_label(category_token))
                    created.append(dup_obj)
                else:
                    _move_object_to_collection(src_obj, dest_leaf, unlink_roots=source_root)
                    _set_model_split_target_lod_p3d_props(src_obj, category_token)
                    used_categories.add(_model_split_target_category_label(category_token))
                    created.append(src_obj)
                    if not _same_id_data(source_root, target_root):
                        lingering = _direct_object_collection_names_under_root(source_root, src_obj)
                        if lingering:
                            preview = ", ".join(lingering[:5])
                            if len(lingering) > 5:
                                preview += ", ..."
                            failed.append(f"{src_obj.name} -> moved but still linked in source collection(s): {preview}")
            except Exception as e:
                failed.append(f"{src_obj.name} -> {_fmt_exc(e)}")
                if effective_mode == "COPY":
                    try:
                        if dup_obj is not None and bpy.data.objects.get(dup_obj.name) is not None and dup_obj.users == 0:
                            bpy.data.objects.remove(dup_obj)
                    except Exception:
                        pass

        if effective_mode == "COPY":
            _rewire_split_copy_object_refs(copies_by_source)

        _focus_created_split_objects(context, target_root, created)
        planner_added, planner_path = _add_model_split_part_to_planner(context, target_root)

        if failed:
            print("=== Model Split Target Category: Failures ===")
            for item in failed:
                print(item)

        if not created:
            self.report({"ERROR"}, "No mesh objects were moved or copied")
            return {"CANCELLED"}

        action_word = "Copied" if requested_mode == "COPY" else "Moved"
        if separated_from_edit and requested_mode == "MOVE":
            action_word = "Separated and moved"
        elif separated_from_edit and requested_mode == "COPY":
            action_word = "Copied selected geometry"
        category_label = ", ".join(sorted(used_categories, key=str.lower)) if used_categories else "P3D category"
        msg = f"{action_word} {len(created)} object(s) to {target_root.name} > {category_label}"
        if planner_path:
            msg += ", added to Import/Export list" if planner_added else ", already in Import/Export list"
        if failed:
            self.report({"WARNING"}, msg + f", failed {len(failed)} (see System Console)")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


def _model_split_grid_active_mesh_object(context):
    active_objects = getattr(getattr(context, "view_layer", None), "objects", None)
    active_obj = getattr(active_objects, "active", None) if active_objects is not None else None
    if active_obj is not None and getattr(active_obj, "type", None) == "MESH":
        return active_obj
    return None


def _model_split_grid_is_cutter(obj) -> bool:
    if obj is None:
        return False
    try:
        return bool(obj.get("nh_grid_cutter", False))
    except Exception:
        return False


def _model_split_grid_is_guide(obj) -> bool:
    if obj is None:
        return False
    try:
        return bool(obj.get("nh_grid_guide", False))
    except Exception:
        return False


def _model_split_grid_is_split_helper(obj) -> bool:
    return _model_split_grid_is_cutter(obj) or _model_split_grid_is_guide(obj)


def _model_split_grid_is_enabled_cutter(obj) -> bool:
    if not (_model_split_grid_is_cutter(obj) or _model_split_grid_is_guide(obj)):
        return False
    try:
        return bool(obj.get("nh_grid_enabled", True))
    except Exception:
        return True


def _model_split_grid_object_visible(context, obj) -> bool:
    if obj is None:
        return False
    try:
        return bool(obj.visible_get(view_layer=context.view_layer))
    except TypeError:
        try:
            return bool(obj.visible_get())
        except Exception:
            pass
    except Exception:
        pass
    try:
        if obj.hide_get():
            return False
    except Exception:
        pass
    try:
        if bool(getattr(obj, "hide_viewport", False)):
            return False
    except Exception:
        pass
    return True




def _model_split_grid_source_display_name(context, settings) -> str:
    from .nh_textures import (_model_split_selected_p3d_root_collections, _strip_blender_numeric_suffix)
    source_obj = getattr(settings, "grid_source_object", None)
    if source_obj is not None:
        return _strip_blender_numeric_suffix(getattr(source_obj, "name", "") or "Source")
    source_root = getattr(settings, "grid_source_root_collection", None)
    if source_root is not None:
        return _strip_blender_numeric_suffix(getattr(source_root, "name", "") or "Source")
    active_obj = _model_split_grid_active_mesh_object(context)
    if active_obj is not None and not _model_split_grid_is_split_helper(active_obj):
        return _strip_blender_numeric_suffix(getattr(active_obj, "name", "") or "Source")
    roots = _model_split_selected_p3d_root_collections(context)
    if roots:
        return _strip_blender_numeric_suffix(getattr(roots[0], "name", "") or "Source")
    return "Scene"


def _model_split_grid_safe_name(value: str, fallback: str = "split") -> str:
    from .nh_textures import (_INVALID_FILENAME_CHARS_RE, _strip_blender_numeric_suffix)
    name = _strip_blender_numeric_suffix((value or "").strip())
    if name.lower().endswith(".p3d"):
        name = os.path.splitext(name)[0]
    name = _INVALID_FILENAME_CHARS_RE.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or fallback


def _model_split_grid_output_prefix(settings, source_name: str = "") -> str:
    raw = (getattr(settings, "grid_output_prefix", "") or "").strip()
    source_prefix = _model_split_grid_safe_name(source_name or "split", fallback="split")
    if not raw or raw.lower() == "split":
        raw = source_prefix
    return _model_split_grid_safe_name(
        raw,
        fallback="split",
    )


def _model_split_grid_collection_parent(context, source_root=None):
    from .nh_snap import (_find_parent_collection)
    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    if scene_root is None:
        return source_root
    if source_root is not None:
        parent = _find_parent_collection(scene_root, source_root)
        if parent is not None:
            return parent
    return scene_root


def _model_split_grid_cutter_collection(context, settings, *, create: bool = False):
    from .nh_snap import (_find_parent_collection)
    collection = getattr(settings, "grid_cutter_collection", None)
    if collection is not None:
        return collection
    if not create:
        return None

    source_name = _model_split_grid_source_display_name(context, settings)
    collection_name = f"NH Grid Cut Lines - {source_name}"
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        collection = bpy.data.collections.new(collection_name)

    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    if scene_root is not None:
        try:
            if _find_parent_collection(scene_root, collection) is None and scene_root != collection:
                scene_root.children.link(collection)
        except Exception:
            pass

    try:
        settings.grid_cutter_collection = collection
    except Exception:
        pass
    return collection



def _model_split_grid_delete_tagged_cutters(collection):
    from .nh_base import (_fmt_exc)
    from .nh_textures import (_collect_collection_objects_recursive)
    if collection is None:
        return 0
    removed = 0
    for obj in list(_collect_collection_objects_recursive(collection)):
        if not _model_split_grid_is_split_helper(obj):
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
        except Exception as e:
            print(f"[NH Plugin] Line Grid Split: failed to remove helper {getattr(obj, 'name', '<object>')}: {_fmt_exc(e)}")
    return removed


def _model_split_grid_world_bounds_for_objects(objects):
    points = []
    for obj in objects or ():
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        try:
            matrix = obj.matrix_world.copy()
            bound_box = list(getattr(obj, "bound_box", []) or [])
        except Exception:
            bound_box = []
        if bound_box:
            points.extend(matrix @ Vector(corner) for corner in bound_box)
            continue
        data = getattr(obj, "data", None)
        if data is None:
            continue
        for vertex in getattr(data, "vertices", []) or []:
            try:
                points.append(matrix @ vertex.co)
            except Exception:
                pass
    if not points:
        return None

    min_v = Vector((
        min(p.x for p in points),
        min(p.y for p in points),
        min(p.z for p in points),
    ))
    max_v = Vector((
        max(p.x for p in points),
        max(p.y for p in points),
        max(p.z for p in points),
    ))
    return min_v, max_v






def _model_split_grid_create_cube_mesh(name: str):
    mesh = bpy.data.meshes.new(name)
    verts = (
        (-0.5, -0.5, -0.5),
        (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5),
        (-0.5, 0.5, -0.5),
        (-0.5, -0.5, 0.5),
        (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5),
        (-0.5, 0.5, 0.5),
    )
    faces = (
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    )
    mesh.from_pydata(verts, (), faces)
    mesh.update()
    return mesh

