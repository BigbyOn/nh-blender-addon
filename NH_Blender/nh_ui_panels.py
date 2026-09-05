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

# nh_ui_panels.py
# auto-split slice; cross-module refs resolved with in-function imports

from .nh_base import (_UI_PANEL_DEFAULT_ORDER)

_OBJECT_BUILDER_PANEL_PREFIX = "Object Builder"
_P3D_PANEL_ICON_PATCHED = False
_P3D_PANEL_ICON_PATCH_ATTEMPTS = 0


def _patch_a3ob_object_builder_panel_headers():
    from .nh_ui_icons import (icon_value)
    idv = icon_value()
    if idv == 0:
        return 0
    patched = 0
    seen = set()
    target_modules = ("bl_ext.user_default.Arma3ObjectBuilder", "NH_bundle")
    interesting = []
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        mod_name = getattr(mod, "__name__", "") or ""
        if not mod_name.startswith(target_modules):
            continue
        for attr_name in dir(mod):
            try:
                cls = getattr(mod, attr_name, None)
            except Exception:
                continue
            if not (isinstance(cls, type) and issubclass(cls, bpy.types.Panel)):
                continue
            label = str(getattr(cls, "bl_label", "") or "")
            if not label.startswith(_OBJECT_BUILDER_PANEL_PREFIX):
                continue
            key = id(cls)
            if key in seen:
                continue
            seen.add(key)
            interesting.append((cls, label))

    for cls, label in interesting:
        new_label = re.sub(
            r"^\s*Object Builder\s*:\s*", "", label, flags=re.IGNORECASE
        ) or label

        def _nh_panel_header(self, context, _orig=None):
            try:
                idv_local = icon_value()
                if idv_local:
                    self.layout.label(text="", icon_value=idv_local)
            except Exception:
                pass
            if callable(_orig):
                try:
                    _orig(self, context)
                except Exception:
                    pass

        try:
            orig_header = getattr(cls, "draw_header", None)
            if not callable(orig_header) or getattr(orig_header, "__name__", "").startswith("_nh_"):
                cls.draw_header = _nh_panel_header
        except Exception:
            pass

        if new_label != label:
            original_label = label
            try:
                cls.bl_label = new_label
                bpy.utils.unregister_class(cls)
                bpy.utils.register_class(cls)
            except Exception:
                try:
                    cls.bl_label = original_label
                    bpy.utils.unregister_class(cls)
                    bpy.utils.register_class(cls)
                except Exception as e:
                    print(f"[NH Plugin] Could not refresh A3OB panel label {original_label}: {e}")
        patched += 1
    return patched


def _ensure_p3d_panel_icon_patch_timer():
    global _P3D_PANEL_ICON_PATCHED, _P3D_PANEL_ICON_PATCH_ATTEMPTS
    if _P3D_PANEL_ICON_PATCHED:
        return None
    try:
        patched = _patch_a3ob_object_builder_panel_headers()
        if patched > 0:
            _P3D_PANEL_ICON_PATCHED = True
            print(f"[NH Plugin] A3OB panel headers: NH icon applied to {patched} 'Object Builder' panel(s)")
            return None
    except Exception:
        pass
    _P3D_PANEL_ICON_PATCH_ATTEMPTS += 1
    if _P3D_PANEL_ICON_PATCH_ATTEMPTS > 30:
        print("[NH Plugin] A3OB panel headers: could not find Object Builder panels to patch")
        return None
    return 2.0


class CRAY_PT_ColliderPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_collider"
    bl_label = "Geometry LODs"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_order = _UI_PANEL_DEFAULT_ORDER["geometry_lods"]
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from .nh_scatter import (_is_ui_panel_visible)
        return _is_ui_panel_visible(context, "geometry_lods")

    def draw(self, context):
        layout = self.layout
        cs = context.scene.cray_collider_settings

        fire = layout.box()
        row = fire.row(align=True)
        row.label(text="Geometries / Fire Geometry", icon="MESH_ICOSPHERE")
        row.prop(
            cs,
            "show_fire_geometry_tools",
            text="",
            emboss=False,
            icon="TRIA_DOWN" if cs.show_fire_geometry_tools else "TRIA_RIGHT",
        )
        if cs.show_fire_geometry_tools:
            row = fire.row(align=True)
            row.prop(cs, "fire_geometry_object", text="Fire Geometry")
            op = row.operator("cray.set_collider_target_from_active", text="", icon="EYEDROPPER")
            op.target_attr = "FIRE"
            row = fire.row(align=True)
            row.prop(cs, "fire_geometry_material", text="Material")
            row.operator("cray.open_fire_geometry_rvmat_folder", text="", icon="FILE_FOLDER")
            op = row.operator("cray.select_collider_material_faces", text="", icon="FACESEL")
            op.target_attr = "FIRE"

        layout.separator()

        fake = layout.box()
        row = fake.row(align=True)
        row.label(text="Fake Terrain Geometry", icon="MESH_GRID")
        row.prop(
            cs,
            "show_fake_terrain_tools",
            text="",
            emboss=False,
            icon="TRIA_DOWN" if cs.show_fake_terrain_tools else "TRIA_RIGHT",
        )
        if cs.show_fake_terrain_tools:
            fake.prop(cs, "fake_terrain_source_object")
            row = fake.row(align=True)
            row.prop(cs, "fake_terrain_target_choice", text="Target")
            row.operator("cray.set_fake_terrain_target_from_active", text="", icon="EYEDROPPER")
            row = fake.row(align=True)
            row.prop(cs, "fake_terrain_patch_size")
            row.prop(cs, "fake_terrain_min_patch_size")
            row = fake.row(align=True)
            row.prop(cs, "fake_terrain_depression_error")
            row.prop(cs, "fake_terrain_hill_error")
            fake.prop(cs, "fake_terrain_thickness")
            fake.operator("cray.generate_fake_terrain_geometry", icon="MOD_BUILD")

        layout.separator()

        roadway = layout.box()
        row = roadway.row(align=True)
        row.label(text="Misc / Roadway", icon="MESH_PLANE")
        row.prop(
            cs,
            "show_roadway_tools",
            text="",
            emboss=False,
            icon="TRIA_DOWN" if cs.show_roadway_tools else "TRIA_RIGHT",
        )
        if cs.show_roadway_tools:
            row = roadway.row(align=True)
            row.prop(cs, "roadway_object")
            op = row.operator("cray.set_collider_target_from_active", text="", icon="EYEDROPPER")
            op.target_attr = "ROADWAY"
            row = roadway.row(align=True)
            row.operator("cray.ensure_roadway_lod", icon="OUTLINER_OB_MESH")
            row.operator("cray.copy_selected_faces_to_roadway", icon="FACESEL")
            row = roadway.row(align=True)
            row.prop(cs, "roadway_material", text="Texture")
            row.operator("cray.open_roadway_material_folder", text="", icon="FILE_FOLDER")
            op = row.operator("cray.select_collider_material_faces", text="", icon="FACESEL")
            op.target_attr = "ROADWAY"
            roadway.prop(cs, "roadway_weld_distance")
            roadway.operator("cray.weld_roadway_vertices", icon="AUTOMERGE_ON")


from .nh_base import (_UI_PANEL_DEFAULT_ORDER)

class CRAY_PT_ColliderExpPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_collider_exp"
    bl_label = "Collider"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_order = _UI_PANEL_DEFAULT_ORDER["collider"]
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from .nh_scatter import (_is_ui_panel_visible)
        return _is_ui_panel_visible(context, "collider")

    def draw(self, context):
        from .nh_collider_exp import (_assign_collider_exp_operator_props_exp, _collider_exp_operator_props_exp, _resolve_collider_exp_source_object_exp)
        layout = self.layout
        scene = getattr(context, "scene", None)
        es = getattr(scene, "cray_collider_exp_settings", None)
        if es is None:
            layout.label(text="Experimental collider settings are unavailable.", icon="ERROR")
            return

        target = layout.box()
        target.label(text="Target", icon="OUTLINER_OB_MESH")
        source_obj = _resolve_collider_exp_source_object_exp(context)
        source_name = source_obj.name if source_obj is not None else "<select mesh source>"
        target.label(text=f"Source: {source_name}", icon="MESH_DATA")
        target.prop(es, "target_lod")
        target.prop(es, "geometry_object")
        op = target.operator("cray.ensure_collider_lod_exp", icon="OUTLINER_OB_MESH")
        _assign_collider_exp_operator_props_exp(
            op,
            es,
            prop_names=("target_lod",),
        )

        create = layout.box()
        create.operator_context = "INVOKE_DEFAULT"
        row = create.row(align=True)
        row.prop_enum(es, "collider_scope", "FROM_SELECTED", text="from selected")
        row.prop_enum(es, "collider_scope", "PER_SHELLS", text="per shells")
        row = create.row(align=True)
        row.prop_enum(es, "collider_scope", "PER_OBJECT_COMPONENTS", text="per obj comp")
        row.prop_enum(es, "collider_scope", "PER_OBJECTS", text="per objects")
        create.prop(es, "minimum_size")
        create.prop(es, "normal_minimum_size")
        create.label(text="Create Collider", icon="MOD_REMESH")

        op = create.operator("cray.generate_box_collider_exp", text="Box", icon="MESH_CUBE")
        _assign_collider_exp_operator_props_exp(op, es)

        row = create.row(align=True)
        op = row.operator("cray.generate_convex_hull_collider_exp", text="Convex Hull", icon="MESH_ICOSPHERE")
        _assign_collider_exp_operator_props_exp(
            op,
            es,
            prop_names=_collider_exp_operator_props_exp(("convex_detail", "convex_max_triangles")),
        )
        op = row.operator("cray.rebuild_convex_hull_collider_exp", text="Simplify Hull", icon="MOD_DECIM")
        _assign_collider_exp_operator_props_exp(
            op,
            es,
            prop_names=_collider_exp_operator_props_exp(("convex_detail", "convex_max_triangles")),
        )
        op = create.operator("cray.reconvex_selected_components_exp", text="Re-Convex Selected Components", icon="MESH_ICOSPHERE")
        _assign_collider_exp_operator_props_exp(
            op,
            es,
            prop_names=("merge_distance", "recalc_normals", "convex_detail", "convex_max_triangles"),
        )
        row = create.row(align=True)
        row.operator("cray.select_connected_shell_from_selection_exp", text="Select Shell", icon="GROUP_VERTEX")
        row.operator("cray.delete_last_collider_exp", text="Delete Last", icon="TRASH")

        row = create.row(align=True)
        op = row.operator("cray.generate_sphere_collider_exp", text="Sphere", icon="MESH_UVSPHERE")
        _assign_collider_exp_operator_props_exp(
            op,
            es,
            prop_names=_collider_exp_operator_props_exp(("sphere_segments",)),
        )
        op = row.operator("cray.generate_capsule_collider_exp", text="Capsule", icon="MESH_UVSPHERE")
        _assign_collider_exp_operator_props_exp(
            op,
            es,
            prop_names=_collider_exp_operator_props_exp((
                "capsule_radius",
                "capsule_height",
                "capsule_cap_size",
                "capsule_follow_source_angle",
                "capsule_vertical_align",
            )),
        )

        round_box = layout.box()
        round_box.operator_context = "INVOKE_DEFAULT"
        round_box.label(text="Round Box Collision", icon="MESH_CYLINDER")

        row = round_box.row(align=True)
        op = row.operator("cray.create_cylinder_guide_collider_exp", text="Create Cylinder", icon="MESH_CYLINDER")
        _assign_collider_exp_operator_props_exp(
            op,
            es,
            prop_names=_collider_exp_operator_props_exp(("cylinder_segments",)),
        )
        op = row.operator("cray.generate_cylinder_boxes_collider_exp", text="Cylinder Boxes", icon="MESH_CUBE")
        _assign_collider_exp_operator_props_exp(
            op,
            es,
            prop_names=_collider_exp_operator_props_exp(("cylinder_segments",)),
        )
        row = round_box.row(align=True)
        op = row.operator("cray.create_pipe_guide_collider_exp", text="Create Pipe", icon="MESH_TORUS")
        _assign_collider_exp_operator_props_exp(
            op,
            es,
            prop_names=_collider_exp_operator_props_exp((
                "pipe_segments",
                "pipe_inner_radius",
                "pipe_outer_radius",
                "pipe_depth",
                "pipe_thickness",
            )),
        )
        op = row.operator("cray.generate_pipe_boxes_collider_exp", text="Pipe Boxes", icon="MESH_CUBE")
        _assign_collider_exp_operator_props_exp(
            op,
            es,
            prop_names=_collider_exp_operator_props_exp((
                "pipe_segments",
                "pipe_inner_radius",
                "pipe_outer_radius",
                "pipe_depth",
                "pipe_thickness",
            )),
        )

        validate = layout.box()
        validate.operator_context = "INVOKE_DEFAULT"
        validate.label(text="Collision QA", icon="CHECKMARK")
        op = validate.operator("cray.validate_collision_exp", text="Validate Collision", icon="ERROR")
        op.max_triangles = int(es.convex_max_triangles)
        op.minimum_size = float(es.minimum_size)
        validate.operator("cray.run_collision_tool_self_test_exp", text="NH Debug / Run Collision Tool Self Test", icon="CONSOLE")


from .nh_base import (_UI_PANEL_DEFAULT_ORDER)

class CRAY_PT_AssetProxyPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_asset_proxy"
    bl_label = "P3D Asset Library"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_order = _UI_PANEL_DEFAULT_ORDER["asset_library"]
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from .nh_scatter import (_is_ui_panel_visible)
        return _is_ui_panel_visible(context, "asset_library")

    def draw(self, context):
        layout = self.layout
        lib = context.scene.cray_asset_library_settings
        st = context.scene.cray_asset_proxy_settings

        box = layout.box()
        box.label(text="NH Objects Libraries", icon="ASSET_MANAGER")
        box.prop(lib, "common_root")
        box.prop(lib, "environment_root")
        row = box.row(align=True)
        row.operator("cray.asset_library_full_rebuild_from_zero", text="Full Rebuild", icon="FILE_REFRESH")
        row.operator("cray.asset_library_add_new_nh_objects", text="Add New", icon="ADD")
        row.operator("cray.asset_library_open_nh_browser", text="", icon="FILEBROWSER")
        box.separator()
        box.label(text="Custom", icon="BOOKMARKS")
        box.prop(lib, "custom_search_root")
        custom_row = box.row(align=True)
        custom_row.prop(lib, "custom_p3d_name", text="")
        custom_row.operator("cray.asset_library_add_custom_by_name", text="", icon="ADD")
        custom_row.operator("cray.asset_library_remove_custom_by_name", text="", icon="REMOVE")
        box.operator("cray.asset_library_clear_custom", text="Clear Custom", icon="TRASH")

        box = layout.box()
        box.label(text="Cut / Save Asset", icon="ASSET_MANAGER")
        box.prop(lib, "asset_cut_name", text="", icon="OBJECT_DATA")
        box.separator()
        cut_row = box.row(align=True)
        cut_row.operator("cray.asset_cut_to_scene", text="Cut to New Scene", icon="ADD")
        cut_row.operator("cray.asset_save_to_library", text="Save to Library", icon="EXPORT")

        box = layout.box()
        box.label(text="Placed Assets -> P3D Proxies", icon="CONSTRAINT")
        box.prop(st, "source_object", text="Proxy Source Object")
        box.prop(st, "target_object", text="Target Resolution / LOD")
        box.prop(st, "target_collection", text="Target P3D Collection")
        row = box.row(align=True)
        row.label(text="Duplicate proxy to:")
        row.prop(st, "proxy_duplicate_resolution", text="", icon="OUTLINER_OB_MESH", toggle=True)
        row.prop(st, "proxy_duplicate_geometries", text="", icon="MESH_ICOSPHERE", toggle=True)
        row.prop(st, "proxy_duplicate_roadway", text="", icon="MESH_PLANE", toggle=True)
        row.prop(st, "proxy_duplicate_point_clouds", text="", icon="EMPTY_AXIS", toggle=True)
        box.operator("cray.convert_selected_to_proxies", icon="CONSTRAINT")


from .nh_base import (_UI_PANEL_DEFAULT_ORDER)

class CRAY_PT_FixesPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_fixes"
    bl_label = "Fixes"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_order = _UI_PANEL_DEFAULT_ORDER["fixes"]
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from .nh_scatter import (_is_ui_panel_visible)
        return _is_ui_panel_visible(context, "fixes")

    def draw(self, context):
        layout = self.layout
        ts = context.scene.cray_texreplace_settings

        from .nh_geometry_audit_ops import _geometry_audit_has_live_cache

        audit_settings = context.scene.cray_geometry_audit_settings
        audit_box = layout.box()
        audit_box.label(text="Geometry Audit", icon="VIEWZOOM")
        audit_box.prop(audit_settings, "scope", text="Scope")
        audit_box.prop(audit_settings, "inside_threshold", text="Inside Threshold")
        audit_box.operator("cray.geometry_audit_scan", text="Scan Geometry", icon="VIEWZOOM")

        if audit_settings.has_results:
            audit_box.label(text=audit_settings.scope_label, icon="OUTLINER_COLLECTION")
            for lod_result in audit_settings.lod_results:
                result_box = audit_box.box()
                result_box.label(text=lod_result.lod_name, icon="MESH_ICOSPHERE")
                for label, property_name in (
                    ("Raw Components", "raw_components"),
                    ("Effective Components", "effective_components"),
                    ("Faceless vertices", "loose_vertices"),
                    ("Faceless islands", "faceless_islands"),
                    ("Tiny components", "tiny_components"),
                    ("Nested suspicious", "nested_suspicious"),
                    ("Nested strong", "nested_strong"),
                ):
                    row = result_box.row(align=True)
                    row.label(text=label)
                    row.label(text=str(getattr(lod_result, property_name)))
                if lod_result.not_testable_pairs:
                    warning = result_box.row()
                    warning.alert = True
                    warning.label(
                        text=f"Not testable pairs: {lod_result.not_testable_pairs}",
                        icon="INFO",
                    )
                if lod_result.details:
                    detail_row = result_box.row(align=True)
                    detail_row.prop(
                        lod_result,
                        "show_details",
                        text=f"Issue details ({len(lod_result.details)})",
                        emboss=False,
                        icon="TRIA_DOWN" if lod_result.show_details else "TRIA_RIGHT",
                    )
                    if lod_result.show_details:
                        details = result_box.column(align=True)
                        for detail in lod_result.details:
                            details.label(text=detail.text)

            actions = audit_box.column(align=True)
            actions.enabled = _geometry_audit_has_live_cache(context)
            row = actions.row(align=True)
            op = row.operator("cray.geometry_audit_select", text="Select Faceless", icon="VERTEXSEL")
            op.issue = "FACELESS"
            op = row.operator("cray.geometry_audit_select", text="Select Tiny", icon="VERTEXSEL")
            op.issue = "TINY"
            op = actions.operator("cray.geometry_audit_select", text="Select Nested", icon="GROUP_VERTEX")
            op.issue = "NESTED"
            actions.operator("cray.geometry_audit_clean_safe", text="Clean Safe Garbage", icon="TRASH")

        check_box = layout.box()
        check_box.label(text="Export checks", icon="ERROR")
        check_box.prop(ts, "export_warn_loose_vertices", text="Loose vertices outside Memory")
        check_box.operator("cray.select_loose_vertices_outside_memory", icon="VERTEXSEL")
        check_box.operator("cray.report_ngon_meshes", text="Report Meshes With N-gons", icon="FACESEL")

        box = layout.box()
        box.label(text="Shading/Geometry fixes", icon="MOD_SMOOTH")
        box.operator("cray.repair_p3d_selections", text="Repair Invalid P3D Selections", icon="GROUP_VERTEX")
        box.separator()
        box.label(text="Material safe merge", icon="AUTOMERGE_ON")
        box.prop(ts, "material_safe_merge_distance", text="Distance")
        box.operator("cray.merge_by_distance_keep_materials", text="Merge By Distance (Keep Materials)", icon="AUTOMERGE_ON")
        box.separator()
        box.label(text="Hierarchy fix", icon="MOD_REMESH")
        box.prop(ts, "fix_mesh_join_batch")
        box.prop(ts, "fix_mesh_center_to_origin")
        box.operator("cray.fix_mesh_hierarchy", text="Fix Mesh/Hierarchy", icon="MOD_REMESH")
        box.separator()
        row = box.row(align=True)
        row.prop(
            ts,
            "show_component_fix_tools",
            text="Component fixes from .txt",
            emboss=False,
            icon="TRIA_DOWN" if ts.show_component_fix_tools else "TRIA_RIGHT",
        )
        if ts.show_component_fix_tools:
            file_row = box.row(align=True)
            file_row.prop(ts, "fix_list_path", text="")
            file_row.operator("cray.open_fix_list_file", text="", icon="FILE_FOLDER")
            box.operator("cray.select_fix_list_components_on_active_lod", icon="GROUP_VERTEX")
            cleanup_col = box.column(align=True)
            cleanup_col.enabled = (
                context.mode == "EDIT_MESH"
                and context.active_object is not None
                and context.active_object.type == "MESH"
            )
            cleanup_col.operator("cray.delete_selected_components_keep_vertices", icon="MESH_DATA")
        box.operator("cray.fix_proxy_triangle_meshes", icon="CONSTRAINT")
        box.separator()
        box.label(text="Edit Mode planar search", icon="FACESEL")
        edit_col = box.column(align=True)
        edit_col.enabled = (
            context.mode == "EDIT_MESH"
            and context.active_object is not None
            and context.active_object.type == "MESH"
        )
        row = edit_col.row(align=True)
        row.operator("cray.select_split_planar_ngons", text="Find Trash", icon="TRASH")
        row.operator("cray.select_coplanar_plate_islands", text="Find Flat Plates", icon="MESH_GRID")
        row = edit_col.row(align=True)
        row.operator("cray.select_ngon_faces", text="Find N-gons", icon="FACESEL")
        row.operator("cray.triangulate_ngon_faces", text="Triangulate Found", icon="MOD_TRIANGULATE")
        tol_row = edit_col.row(align=True)
        tol_row.prop(ts, "split_planar_ngon_angle_tolerance", text="Angle")
        tol_row.prop(ts, "split_planar_ngon_plane_tolerance", text="Plane")

from .nh_base import (_UI_PANEL_DEFAULT_ORDER)

class CRAY_PT_ImportExportPlannerPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_ie_planner"
    bl_label = "Import/Export planner"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_order = _UI_PANEL_DEFAULT_ORDER["import_export"]
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from .nh_scatter import (_is_ui_panel_visible)
        return _is_ui_panel_visible(context, "import_export")

    def draw(self, context):
        layout = self.layout
        st = context.scene.cray_ie_settings

        ibox = layout.box()
        ibox.label(text="Batch Import (Arma 3 Object Builder)", icon="IMPORT")
        ibox.operator("cray.import_tb_txt", text="Import Terrain Builder (.txt)", icon="IMPORT")
        ibox.separator()
        ibox.label(text="Quick Add From NH_Objects", icon="VIEWZOOM")
        ibox.prop(st, "quick_add_search_root")
        quick_row = ibox.row(align=True)
        quick_row.prop(st, "quick_add_p3d_name", text="")
        quick_row.operator("cray.ie_add_by_name", text="", icon="ADD")
        row = ibox.row(align=True)
        row.operator("cray.ie_add_files", text="", icon="ADD")
        row.operator("cray.ie_remove_file", text="", icon="REMOVE")
        row.operator("cray.ie_refresh_files", text="Refresh", icon="FILE_REFRESH")
        row.operator("cray.ie_clear_files", icon="TRASH")
        add_hint = ibox.row(align=True)
        add_hint.enabled = False
        add_hint.alignment = "CENTER"
        add_hint.label(text="Use + to queue .p3d files", icon="IMPORT")
        ibox.template_list("CRAY_UL_ie_files", "", st, "import_files", st, "import_active_index", rows=6)
        ibox.operator("cray.ie_import_batch", icon="FILE_REFRESH")
        ibox.separator()
        ibox.prop(st, "import_show_materials")
        row_preview = ibox.row()
        row_preview.enabled = bool(st.import_show_materials)
        row_preview.prop(st, "import_keep_converted_textures")
        ibox.separator()
        ibox.prop(st, "disable_collections_after_import")
        row2 = ibox.row()
        row2.enabled = bool(st.disable_collections_after_import)
        row2.prop(st, "disable_mode", text="")

        ebox = layout.box()
        ebox.label(text="Batch Export Collections (Arma 3 Object Builder)", icon="EXPORT")
        ebox.prop(st, "export_mode")
        row3 = ebox.row()
        row3.enabled = (st.export_mode == "CUSTOM_DIR")
        row3.prop(st, "export_directory")
        ebox.prop(st, "export_create_bak")
        ebox.prop(st, "export_only_p3d_named")
        ebox.prop(st, "export_only_split_parts")
        ebox.prop(st, "export_force_all_lods")
        ebox.operator("cray.ie_export_collections_batch", icon="FILE_TICK")

from .nh_base import (_UI_PANEL_DEFAULT_ORDER)

class CRAY_PT_ModelSplitPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_model_split"
    bl_label = "Model Split / Merge"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_order = _UI_PANEL_DEFAULT_ORDER["model_split"]
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from .nh_scatter import (_is_ui_panel_visible)
        return _is_ui_panel_visible(context, "model_split")

    def draw(self, context):
        layout = self.layout
        st = context.scene.cray_model_split_settings

        box = layout.box()
        box.label(text="Part Transfer", icon="OUTLINER_COLLECTION")
        box.prop(st, "named_source_collection", text="Source Model")
        box.prop(st, "named_target_collection", text="Target Model")
        row = box.row(align=True)
        copy_op = row.operator("cray.model_split_transfer_to_target_category", text="Copy", icon="DUPLICATE")
        copy_op.transfer_mode = "COPY"
        move_op = row.operator("cray.model_split_transfer_to_target_category", text="Move", icon="EXPORT")
        move_op.transfer_mode = "MOVE"

        layout.separator()

        grid_box = layout.box()
        grid_box.label(text="Line Grid Split", icon="MOD_BOOLEAN")
        grid_box.prop(st, "grid_source_object", text="Source Object")
        grid_box.prop(st, "grid_source_root_collection", text="Source Collection")
        row = grid_box.row(align=True)
        row.prop(st, "grid_count_x", text="Parts X")
        row.prop(st, "grid_count_y", text="Parts Y")
        grid_box.prop(st, "grid_cutter_collection", text="Cut Lines")
        grid_box.prop(st, "grid_output_prefix", text="Name Prefix")
        grid_box.prop(st, "grid_keep_original")
        grid_box.prop(st, "grid_skip_empty_pieces")
        row = grid_box.row(align=True)
        row.prop(st, "grid_min_vertices", text="Min Verts")
        row.prop(st, "grid_min_faces", text="Min Faces")
        grid_box.prop(st, "grid_use_visible_cutters_only")
        grid_box.prop(st, "grid_hide_cutters_after_split")
        grid_box.prop(st, "grid_add_result_to_export_planner")
        row = grid_box.row(align=True)
        row.operator("cray.model_split_grid_create_cutters", text="Create/Edit Cut Lines", icon="MESH_GRID")
        row.operator("cray.model_split_grid_select_cutters", text="Select Lines", icon="RESTRICT_SELECT_OFF")
        row = grid_box.row(align=True)
        row.operator("cray.model_split_grid_clear_cutters", text="Clear Lines", icon="TRASH")
        row.operator("cray.model_split_grid_split_source", text="Split To _p Parts", icon="MOD_BOOLEAN")

        layout.separator()

        merge_box = layout.box()
        merge_box.label(text="Merge Collections", icon="OUTLINER_COLLECTION")
        merge_box.prop(st, "named_target_collection", text="Target Model")
        row = merge_box.row(align=True)
        row.prop(st, "merge_source_collection_key", text="Source")
        row.operator("cray.model_split_merge_add_source", text="", icon="ADD")
        row = merge_box.row()
        row.template_list(
            "CRAY_UL_model_split_merge_sources",
            "",
            st,
            "merge_sources",
            st,
            "merge_sources_index",
            rows=3,
        )
        col = row.column(align=True)
        col.operator("cray.model_split_merge_remove_source", text="", icon="REMOVE")
        col.operator("cray.model_split_merge_clear_sources", text="", icon="TRASH")
        merge_box.operator("cray.model_split_merge_selected_collections", text="Merge", icon="OUTLINER_COLLECTION")

from .nh_base import (_UI_PANEL_DEFAULT_ORDER)

class CRAY_PT_CacheManagerPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_cache_manager"
    bl_label = "Cache Manager"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_order = _UI_PANEL_DEFAULT_ORDER["cache_manager"]
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from .nh_scatter import (_is_ui_panel_visible)
        return _is_ui_panel_visible(context, "cache_manager")

    def draw(self, context):
        from .nh_model_split import (_nh_objects_asset_cache_base)
        layout = self.layout
        ts = context.scene.cray_texreplace_settings

        lbox = layout.box()
        lbox.label(text="NH Asset Library Cache", icon="ASSET_MANAGER")
        lbox.operator("cray.asset_library_full_rebuild_from_zero", text="Full Rebuild From Zero", icon="FILE_REFRESH")
        lbox.operator("cray.asset_library_add_new_nh_objects", text="Add New P3Ds + Icons", icon="ADD")
        lbox.operator(
            "cray.asset_library_force_rebuild_icons_textures",
            text="Force Rebuild All Icons + Textures",
            icon="RENDER_STILL",
        )
        lbox.prop(ts, "texture_cache_workers", text="Cache Workers")
        row = lbox.row(align=True)
        row.operator("cray.asset_library_open_nh_browser", text="Open Asset Browser", icon="FILEBROWSER")
        row.operator("cray.open_nh_asset_cache_folder", text="Open Cache", icon="FILE_FOLDER")
        try:
            asset_cache = _nh_objects_asset_cache_base(create=False)
            lbox.label(text=f"Cache: {asset_cache}")
        except Exception:
            pass


from .nh_base import (_UI_PANEL_DEFAULT_ORDER)

class CRAY_PT_TextureReplacePanel(Panel):
    bl_idname = "VIEW3D_PT_cray_texreplace"
    bl_label = "Texture Replace"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_order = _UI_PANEL_DEFAULT_ORDER["texture_replace"]
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from .nh_scatter import (_is_ui_panel_visible)
        return _is_ui_panel_visible(context, "texture_replace")

    def draw(self, context):
        from .nh_collider_exp import (_ensure_tex_source_roots_collection, _tex_export_resolve_path)
        from .nh_textures import (_draw_texture_export_progress)
        layout = self.layout
        ts = context.scene.cray_texreplace_settings

        box = layout.box()
        box.label(text="Build Database (.paa/.rvmat only)")
        box.prop(ts, "folder")
        box.operator("cray.tex_db_build_folder", icon="FILE_FOLDER")

        layout.separator()

        rbox = layout.box()
        rbox.label(text="Replace Texture from DB", icon="FILE_TICK")
        rbox.prop(ts, "picked_object", text="Select Object")
        rbox.operator("cray.replace_textures_from_db", icon="FILE_TICK")
        rbox.prop(ts, "write_expected_missing_paths")

        layout.separator()

        ebox = layout.box()
        ebox.label(text="Export Missing Textures from Sources", icon="EXPORT")
        roots = _ensure_tex_source_roots_collection(ts)
        roots_box = ebox.box()
        roots_box.label(text="Source Texture Roots", icon="FILE_FOLDER")
        if roots:
            for idx, item in enumerate(ts.source_texture_roots):
                row = roots_box.row(align=True)
                row.prop(item, "path", text=f"{idx + 1}")
                remove_op = row.operator("cray.tex_source_root_remove", text="", icon="TRASH")
                remove_op.index = idx
        else:
            roots_box.label(text="No source roots configured", icon="ERROR")
        add_row = roots_box.row(align=True)
        add_row.prop(ts, "source_root_to_add", text="")
        add_row.operator("cray.tex_source_root_add", text="", icon="ADD")
        ebox.prop(ts, "target_textures_folder")
        ebox.label(text="Base Color suffix: automatic _ca / _co", icon="IMAGE_DATA")
        row = ebox.row(align=True)
        row.prop(ts, "convert_dds_to_png")
        row.prop(ts, "convert_png_to_paa")
        ebox.prop(ts, "dds_backend")
        if ts.convert_png_to_paa:
            ebox.prop(ts, "image_to_paa_path")
        row = ebox.row(align=True)
        row.prop(ts, "generate_rvmat")
        if ts.convert_png_to_paa:
            row.prop(ts, "delete_png_after_paa")
        row = ebox.row(align=True)
        row.prop(ts, "export_only_missing")
        row.prop(ts, "export_overwrite_existing")
        if ts.texture_export_is_running:
            pbox = ebox.box()
            pbox.label(text="Exporting textures...")
            _draw_texture_export_progress(pbox, ts)
            pbox.operator("cray.cancel_texture_export", icon="CANCEL")
        else:
            if ts.texture_export_last_summary:
                pbox = ebox.box()
                pbox.label(text=f"Last export: {ts.texture_export_last_summary}")
                report_path = _tex_export_resolve_path(ts.texture_export_last_report_path)
                if report_path:
                    pbox.label(text=f"Report: {os.path.basename(report_path)}")
                    if os.path.isfile(report_path):
                        pbox.operator("cray.open_texture_export_last_report", icon="TEXT")
            ebox.operator("cray.export_missing_textures_from_sources", icon="FILE_TICK")


class CRAY_PT_MenuSettingsPanel(Panel):
    bl_idname = "VIEW3D_PT_cray_menu_settings"
    bl_label = "Menu Settings"
    bl_category = "NH Plugin"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_order = 10000
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        from .nh_base import (_PLAIN_AXIS_HOTKEY_REGISTERED, _find_nh_keymap_item, _keymap_item_shortcut_label)
        from .nh_scatter import (_CUSTOM_KEYBIND_DEFINITIONS, _sorted_ui_panel_layout_definitions)
        layout = self.layout
        settings = context.scene.cray_ui_panel_settings

        box = layout.box()
        box.label(text="Panel Visibility", icon="PREFERENCES")
        box.label(text="Use arrows to reorder; order is saved on exit", icon="INFO")

        ordered_definitions = _sorted_ui_panel_layout_definitions(settings)
        for _idx, (key, label, _class_name) in enumerate(ordered_definitions):
            row = box.row(align=True)
            up = row.operator("cray.move_ui_panel_layout_item", text="", icon="TRIA_UP")
            up.panel_key = key
            up.direction = -1
            down = row.operator("cray.move_ui_panel_layout_item", text="", icon="TRIA_DOWN")
            down.panel_key = key
            down.direction = 1
            row.prop(settings, f"show_{key}", text="")
            row.label(text=label)

        box.operator("cray.reset_ui_panel_layout_order", text="Reset Order", icon="FILE_REFRESH")

        layout.separator()
        keybind_box = layout.box()
        row = keybind_box.row(align=True)
        row.prop(
            settings,
            "show_custom_keybinds",
            text="Custom Keybinds",
            icon="TRIA_DOWN" if settings.show_custom_keybinds else "TRIA_RIGHT",
            emboss=False,
        )

        if settings.show_custom_keybinds:
            row = keybind_box.row(align=True)
            row.operator("cray.open_nh_keymap_preferences", text="Open Keymap", icon="PREFERENCES")
            row.operator("cray.restore_nh_default_keymaps", text="Restore Defaults", icon="FILE_REFRESH")

            for operator_idname, action, default_shortcut, status_key in _CUSTOM_KEYBIND_DEFINITIONS:
                kmi = _find_nh_keymap_item(operator_idname)
                shortcut = _keymap_item_shortcut_label(kmi, default_shortcut)
                action_text = action
                enabled = True
                if kmi is not None and not bool(getattr(kmi, "active", True)):
                    action_text = f"{action} (disabled)"
                    enabled = False
                elif status_key == "plain_axis" and not _PLAIN_AXIS_HOTKEY_REGISTERED and kmi is None:
                    action_text = f"{action} (default busy)"
                    enabled = False

                row = keybind_box.row(align=True)
                row.enabled = enabled
                split = row.split(factor=0.36, align=True)
                split.label(text=shortcut)
                split.label(text=action_text)


# ------------------------------------------------------------------------
#  Registration
# ------------------------------------------------------------------------
