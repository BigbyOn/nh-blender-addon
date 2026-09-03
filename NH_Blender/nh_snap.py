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

# nh_snap.py
# auto-split slice; cross-module refs resolved with in-function imports

_SP_GROUP_RE = re.compile(r"^[A-Za-z0-9_]+$")
_SP_P3D_NAME_RE = re.compile(r"^[A-Za-z0-9]+$")
_SP_PAIR_CODE_RE = re.compile(r"^[A-Za-z0-9]{1,3}$")

_P3D_IMPORT_CANDIDATES = (
    (
        "a3ob.import_p3d",
        (
            "filepath",
            "first_lod_only",
            "absolute_paths",
            "enclose",
            "groupby",
            "additional_data_allowed",
            "additional_data",
            "validate_meshes",
            "proxy_action",
            "translate_selections",
            "cleanup_empty_selections",
            "load_textures",
        ),
    ),
    ("import_scene.a3ob_p3d", ("filepath",)),
    ("import_scene.a3ob_model", ("filepath",)),
    ("a3ob.import_model", ("filepath",)),
    (
        "nh.import_p3d",
        (
            "filepath",
            "first_lod_only",
            "absolute_paths",
            "enclose",
            "groupby",
            "additional_data_allowed",
            "additional_data",
            "validate_meshes",
            "proxy_action",
            "translate_selections",
            "cleanup_empty_selections",
            "load_textures",
        ),
    ),
)

_P3D_EXPORT_CANDIDATES = (
    (
        "a3ob.export_p3d",
        (
            "filepath",
            "use_selection",
            "visible_only",
            "relative_paths",
            "preserve_normals",
            "validate_meshes",
            "apply_transforms",
            "apply_modifiers",
            "sort_sections",
            "lod_collisions",
            "validate_lods",
            "validate_lods_warning_errors",
            "generate_components",
            "renumber_components",
            "translate_selections",
            "force_lowercase",
        ),
    ),
    ("export_scene.a3ob_p3d", ("filepath", "use_selection")),
    ("a3ob.export_model", ("filepath", "use_selection")),
    (
        "nh.export_p3d",
        (
            "filepath",
            "use_selection",
            "visible_only",
            "relative_paths",
            "preserve_normals",
            "validate_meshes",
            "apply_transforms",
            "apply_modifiers",
            "sort_sections",
            "lod_collisions",
            "validate_lods",
            "validate_lods_warning_errors",
            "generate_components",
            "renumber_components",
            "translate_selections",
            "force_lowercase",
        ),
    ),
)

_P3D_IMPORT_READ_FILE_PATCHES = []
_P3D_IMPORT_TRACKING_SUPPRESS_DEPTH = 0
_P3D_P3D_FILE_HANDLER_PATCHES = []
_P3D_DROP_PENDING_PATHS = []

def _op_handle(op_idname: str):
    try:
        mod, op = op_idname.split(".", 1)
    except ValueError:
        return None
    mod_obj = getattr(bpy.ops, mod, None)
    if mod_obj is None:
        return None
    fn = getattr(mod_obj, op, None)
    if fn is None:
        return None
    try:
        fn.get_rna_type()
    except Exception:
        return None
    return fn

def _has_any_p3d_import_ops():
    return any(_op_handle(op) is not None for op, _ in _P3D_IMPORT_CANDIDATES)

def _has_any_p3d_io_ops():
    has_import = _has_any_p3d_import_ops()
    has_export = any(_op_handle(op) is not None for op, _ in _P3D_EXPORT_CANDIDATES)
    return has_import and has_export


_P3D_BUNDLE_REGISTRY = {
    "registered": False,
    "props_modules": [],
    "ops_classes": [],
}


def _has_original_p3d_addon():
    if _op_handle("a3ob.import_p3d") is not None or _op_handle("a3ob.export_p3d") is not None:
        return True
    try:
        return "bl_ext.user_default.Arma3ObjectBuilder" in sys.modules
    except Exception:
        return False


def _import_bundled_p3d_module(module_name: str):
    from .nh_base import (_fmt_exc)
    try:
        return importlib.import_module(module_name)
    except Exception as e:
        print(f"[NH Plugin] bundled P3D module import failed: {module_name}: {_fmt_exc(e)}")
        return None


def _ensure_p3d_bundle_registered():
    from .nh_base import (_fmt_exc)
    """Register the embedded P3D codec fallback when the original add-on is absent.

    Registers:
    - P3D object/material/scene property groups (classes renamed NHA3_* to avoid
      RNA identifier collisions if the original add-on is enabled later)
    - nh.import_p3d / nh.export_p3d wrapper operators that use the
      embedded codec, plus a .p3d file handler.
    """
    reg = _P3D_BUNDLE_REGISTRY
    if reg["registered"]:
        return True
    if _has_original_p3d_addon():
        return False

    prop_obj = _import_bundled_p3d_module("NH_bundle.props.object")
    prop_mat = _import_bundled_p3d_module("NH_bundle.props.material")
    prop_scn = _import_bundled_p3d_module("NH_bundle.props.scene")
    ui_mod = _import_bundled_p3d_module("NH_bundle.ui.import_export_p3d")
    ui_mesh_mod = _import_bundled_p3d_module("NH_bundle.ui.props_object_mesh")
    ui_mat_mod = _import_bundled_p3d_module("NH_bundle.ui.props_material")
    if prop_obj is None or prop_mat is None or prop_scn is None or ui_mod is None:
        return False

    new_prop_modules = []
    new_ui_modules = []
    try:
        for mod, existing_attr in (
            (prop_obj, hasattr(bpy.types.Object, "a3ob_properties_object")),
            (prop_mat, hasattr(bpy.types.Material, "a3ob_properties_material")),
            (prop_scn, hasattr(bpy.types.Scene, "a3ob_outliner")),
        ):
            if existing_attr:
                continue
            mod.register()
            new_prop_modules.append(mod)
    except Exception as e:
        for mod in reversed(new_prop_modules):
            try:
                mod.unregister()
            except Exception:
                pass
        print(f"[NH Plugin] failed to register bundled P3D property groups: {_fmt_exc(e)}")
        return False

    try:
        for mod in (ui_mesh_mod, ui_mat_mod):
            if mod is None:
                continue
            mod.register()
            new_ui_modules.append(mod)
    except Exception as e:
        for mod in reversed(new_ui_modules):
            try:
                mod.unregister()
            except Exception:
                pass
        for mod in reversed(new_prop_modules):
            try:
                mod.unregister()
            except Exception:
                pass
        print(f"[NH Plugin] failed to register bundled P3D UI panels: {_fmt_exc(e)}")
        return False

    new_op_classes = []
    try:
        if ui_mod is not None and callable(getattr(ui_mod, "register", None)):
            from . import nh_statistics as _st
            if callable(getattr(_st, "wrap", None)):
                _st.wrap(getattr(ui_mod, "classes", ()) or ())
            ui_mod.register()
            new_ui_modules.append(ui_mod)
    except Exception as e:
        for mod in reversed(new_ui_modules):
            try:
                mod.unregister()
            except Exception:
                pass
        for mod in reversed(new_prop_modules):
            try:
                mod.unregister()
            except Exception:
                pass
        print(f"[NH Plugin] failed to register bundled P3D operators: {_fmt_exc(e)}")
        return False

    reg["registered"] = True
    reg["props_modules"] = new_prop_modules
    reg["ui_modules"] = new_ui_modules
    reg["ops_classes"] = new_op_classes
    print("[NH Plugin] P3D bundled fallback registered (P3D codec + property groups + UI panels)")
    return True


def _unregister_p3d_bundle():
    reg = _P3D_BUNDLE_REGISTRY
    if not reg["registered"]:
        return
    for mod in reversed(reg.get("ui_modules", [])):
        try:
            mod.unregister()
        except Exception:
            pass
    for mod in reversed(reg["props_modules"]):
        try:
            mod.unregister()
        except Exception:
            pass
    reg["props_modules"] = []
    reg["ui_modules"] = []
    reg["ops_classes"] = []
    reg["registered"] = False


def _iter_file_handler_subclasses(base_cls):
    seen = set()
    stack = list(base_cls.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        yield cls
        try:
            stack.extend(cls.__subclasses__())
        except Exception:
            pass


def _iter_p3d_p3d_file_handlers():
    file_handler_type = getattr(bpy.types, "FileHandler", None)
    if file_handler_type is None:
        return

    for cls in _iter_file_handler_subclasses(file_handler_type):
        try:
            extensions = str(getattr(cls, "bl_file_extensions", "") or "").lower()
            import_operator = str(getattr(cls, "bl_import_operator", "") or "")
        except Exception:
            continue
        if ".p3d" not in extensions:
            continue
        if (
            import_operator.startswith("a3ob.")
            or import_operator.startswith("cray.a3ob_")
            or import_operator.startswith("nh.")
            or cls.__name__ == "P3D_FH_import_p3d"
            or cls.__name__ == "NH_FH_import_p3d"
        ):
            yield cls


def _nh_p3d_file_handler_poll_drop(cls, context):
    del cls
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    return bool(area and (region is None or getattr(region, "type", None) == "WINDOW"))


def _is_registered_blender_class(cls):
    try:
        return getattr(bpy.types, cls.__name__, None) is cls
    except Exception:
        return False


def _patch_p3d_p3d_file_handler():
    from .nh_base import (_fmt_exc)
    patched = False
    for cls in _iter_p3d_p3d_file_handlers():
        if any(patched_cls is cls for patched_cls, _, _ in _P3D_P3D_FILE_HANDLER_PATCHES):
            patched = True
            continue
        original_import_operator = ""
        original_poll_drop = None
        was_registered = False
        try:
            original_import_operator = getattr(cls, "bl_import_operator", "")
            original_poll_drop = cls.__dict__.get("poll_drop", None)
            was_registered = _is_registered_blender_class(cls)
            if was_registered:
                bpy.utils.unregister_class(cls)
            cls.bl_import_operator = "cray.p3d_drop_menu"
            cls.poll_drop = classmethod(_nh_p3d_file_handler_poll_drop)
            if was_registered:
                bpy.utils.register_class(cls)
            _P3D_P3D_FILE_HANDLER_PATCHES.append((cls, original_import_operator, original_poll_drop))
            patched = True
        except Exception as e:
            try:
                cls.bl_import_operator = original_import_operator
                if original_poll_drop is not None:
                    cls.poll_drop = original_poll_drop
            except Exception:
                pass
            try:
                if was_registered and not _is_registered_blender_class(cls):
                    bpy.utils.register_class(cls)
            except Exception:
                pass
            print(f"[NH Plugin] Failed to patch P3D P3D file drop handler: {_fmt_exc(e)}")
    return patched


def _unpatch_p3d_p3d_file_handler():
    while _P3D_P3D_FILE_HANDLER_PATCHES:
        cls, original_import_operator, original_poll_drop = _P3D_P3D_FILE_HANDLER_PATCHES.pop()
        was_registered = _is_registered_blender_class(cls)
        try:
            if was_registered:
                bpy.utils.unregister_class(cls)
            cls.bl_import_operator = original_import_operator
            if original_poll_drop is not None:
                cls.poll_drop = original_poll_drop
            elif cls.__dict__.get("poll_drop", None) is not None:
                delattr(cls, "poll_drop")
            if was_registered:
                bpy.utils.register_class(cls)
        except Exception:
            pass

def _call_first_available(op_candidates, **kwargs):
    last_err = None
    for op_idname, allowed_keys in op_candidates:
        fn = _op_handle(op_idname)
        if fn is None:
            continue
        payload = {k: v for k, v in kwargs.items() if k in allowed_keys}
        try:
            rna = fn.get_rna_type()
            valid_keys = {prop.identifier for prop in rna.properties if prop.identifier != "rna_type"}
            payload = {k: v for k, v in payload.items() if k in valid_keys}
        except Exception:
            pass
        try:
            result = fn(**payload)
            if isinstance(result, set) and "CANCELLED" in result:
                last_err = RuntimeError(f"{op_idname} returned CANCELLED")
                continue
            return result, op_idname, None
        except Exception as e:
            last_err = e
            continue
    return None, None, last_err


@contextmanager
def _suppress_p3d_import_tracking():
    global _P3D_IMPORT_TRACKING_SUPPRESS_DEPTH
    _P3D_IMPORT_TRACKING_SUPPRESS_DEPTH += 1
    try:
        yield
    finally:
        _P3D_IMPORT_TRACKING_SUPPRESS_DEPTH = max(0, _P3D_IMPORT_TRACKING_SUPPRESS_DEPTH - 1)


def _import_first_available_module(module_names):
    for module_name in module_names:
        try:
            return importlib.import_module(module_name)
        except Exception:
            continue
    return None


@contextmanager
def _temporary_disable_p3d_lod_validation(enabled: bool):
    if not enabled:
        yield False
        return

    export_mod = _get_p3d_export_p3d_module()
    validator_mod = _get_p3d_validator_module()
    candidate_classes = []

    for mod in (export_mod, validator_mod):
        cls = getattr(mod, "Validator", None) if mod is not None else None
        if cls is not None:
            candidate_classes.append(cls)

    for module_name, mod in list(sys.modules.items()):
        if not module_name.endswith(".utilities.validator") and not module_name.endswith(".io.export_p3d"):
            continue
        cls = getattr(mod, "Validator", None)
        if cls is not None:
            candidate_classes.append(cls)

    patched_validators = []
    seen_classes = set()
    for validator_cls in candidate_classes:
        try:
            key = id(validator_cls)
        except Exception:
            key = None
        if key is not None and key in seen_classes:
            continue
        if key is not None:
            seen_classes.add(key)
        original_validate = getattr(validator_cls, "validate_lod", None)
        if callable(original_validate):
            patched_validators.append((validator_cls, original_validate))

    patched_proxy_checks = []
    for mod in (export_mod,):
        original_validate_proxies = getattr(mod, "validate_proxies", None) if mod is not None else None
        if callable(original_validate_proxies):
            patched_proxy_checks.append((mod, original_validate_proxies))

    if not patched_validators and not patched_proxy_checks:
        yield False
        return

    def _always_valid(self, obj, lod, lazy=False, warns_errs=True, relative_paths=False):
        return True

    def _always_valid_proxies(operator, proxy_objects):
        return True

    for validator_cls, _original_validate in patched_validators:
        validator_cls.validate_lod = _always_valid
    for mod, _original_validate_proxies in patched_proxy_checks:
        mod.validate_proxies = _always_valid_proxies

    try:
        yield True
    finally:
        for validator_cls, original_validate in patched_validators:
            validator_cls.validate_lod = original_validate
        for mod, original_validate_proxies in patched_proxy_checks:
            mod.validate_proxies = original_validate_proxies


def _call_export_with_optional_relaxed_validation(force_all_lods: bool, **kwargs):
    with _temporary_disable_p3d_lod_validation(force_all_lods) as relaxed:
        if force_all_lods:
            print("=== Batch Export Collections: Force export all LODs ===")
            print(
                "P3D validation/proxy guards bypassed: "
                f"{'yes' if relaxed else 'no (P3D modules were not found)'}"
            )
        return _call_first_available(_P3D_EXPORT_CANDIDATES, **kwargs)


def _collision_lod_material_export_keep_token(export_objects):
    from .nh_scatter import (_FIRE_GEOMETRY_LOD_TOKEN, _GEOMETRY_LOD_TOKEN, _collider_lod_token_from_object)
    present = set()
    for obj in export_objects or []:
        if getattr(obj, "type", None) != "MESH":
            continue
        lod_token = _collider_lod_token_from_object(obj, allow_name_fallback=True)
        if lod_token in (_FIRE_GEOMETRY_LOD_TOKEN, _GEOMETRY_LOD_TOKEN, "14"):
            present.add(lod_token)
    if _FIRE_GEOMETRY_LOD_TOKEN in present:
        return _FIRE_GEOMETRY_LOD_TOKEN
    if _GEOMETRY_LOD_TOKEN in present:
        return _GEOMETRY_LOD_TOKEN
    if "14" in present:
        return "14"
    return ""


def _strip_collision_lod_materials_for_export(export_objects):
    from .nh_scatter import (_GEOMETRY_LOD_TOKEN, _collider_lod_token_from_object)
    keep_token = _collision_lod_material_export_keep_token(export_objects)
    if not keep_token:
        return []

    restore_items = []
    seen_meshes = set()
    for obj in export_objects or []:
        if getattr(obj, "type", None) != "MESH":
            continue
        lod_token = _collider_lod_token_from_object(obj, allow_name_fallback=True)
        if lod_token not in (_GEOMETRY_LOD_TOKEN, "14"):
            continue
        if lod_token == keep_token:
            continue
        mesh = getattr(obj, "data", None)
        if mesh is None:
            continue
        try:
            mesh_key = mesh.as_pointer()
        except Exception:
            mesh_key = id(mesh)
        if mesh_key in seen_meshes:
            continue
        seen_meshes.add(mesh_key)
        materials = [mat for mat in mesh.materials]
        poly_indices = [int(poly.material_index) for poly in mesh.polygons]
        if not materials and not any(poly_indices):
            continue
        restore_items.append((mesh, materials, poly_indices))
        try:
            mesh.materials.clear()
            for poly in mesh.polygons:
                poly.material_index = 0
            mesh.update()
        except Exception:
            restore_items.pop()
    return restore_items


def _restore_collision_lod_materials_after_export(restore_items):
    for mesh, materials, poly_indices in restore_items or []:
        try:
            mesh.materials.clear()
            for mat in materials:
                mesh.materials.append(mat)
            for poly, material_index in zip(mesh.polygons, poly_indices):
                poly.material_index = int(material_index)
            mesh.update()
        except Exception:
            pass


def _strip_p3d_named_properties_for_export(export_objects):
    restore_items = []
    for obj in export_objects or []:
        if obj is None or not hasattr(obj, "a3ob_properties_object"):
            continue
        try:
            props = obj.a3ob_properties_object
            items = getattr(props, "properties", None)
        except Exception:
            continue
        if items is None or len(items) == 0:
            continue

        saved = []
        try:
            for item in items:
                saved.append((str(getattr(item, "name", "") or ""), str(getattr(item, "value", "") or "")))
            items.clear()
        except Exception:
            continue
        restore_items.append((obj, saved))
    return restore_items


def _restore_p3d_named_properties_after_export(restore_items):
    for obj, saved in restore_items or []:
        if obj is None or not hasattr(obj, "a3ob_properties_object"):
            continue
        try:
            items = obj.a3ob_properties_object.properties
            items.clear()
            for name, value in saved:
                item = items.add()
                item.name = name
                item.value = value
        except Exception:
            pass


def _get_p3d_export_p3d_module():
    return _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.export_p3d",
            "NH_bundle.io.export_p3d",
        )
    )


def _get_p3d_validator_module():
    return _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.utilities.validator",
            "NH_bundle.utilities.validator",
        )
    )


def _get_p3d_data_p3d_module():
    return _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.data_p3d",
            "NH_bundle.io.data_p3d",
        )
    )


def _lod_signature_key(signature: float) -> str:
    return f"{float(signature):.6e}"


def _p3d_lod_signature_from_props(props, p3d_mod):
    lod_res_cls = getattr(p3d_mod, "P3D_LOD_Resolution", None)
    if lod_res_cls is None:
        return None

    try:
        lod_idx = int(getattr(props, "lod", 0))
    except Exception:
        return None

    lod_unknown = int(getattr(lod_res_cls, "UNKNOWN", -1))
    try:
        if lod_idx == lod_unknown:
            resolution = float(getattr(props, "resolution_float", 0.0) or 0.0)
        else:
            resolution = float(getattr(props, "resolution", 0.0) or 0.0)
    except Exception:
        resolution = 0.0

    try:
        signature = lod_res_cls.encode(lod_idx, resolution)
    except Exception:
        return None
    if signature is None:
        return None
    return float(signature)


def _collect_expected_lod_entries(export_objects):
    p3d_mod = _get_p3d_data_p3d_module()
    if p3d_mod is None:
        return {}

    expected = {}
    for obj in export_objects:
        if obj is None or obj.type != "MESH" or obj.parent is not None:
            continue
        if not hasattr(obj, "a3ob_properties_object"):
            continue

        props = obj.a3ob_properties_object
        if not bool(getattr(props, "is_a3_lod", False)):
            continue

        signature = _p3d_lod_signature_from_props(props, p3d_mod)
        if signature is None:
            continue

        try:
            lod_name = str(props.get_name())
        except Exception:
            lod_name = obj.name

        key = _lod_signature_key(signature)
        rec = expected.get(key)
        if rec is None:
            expected[key] = {
                "signature": signature,
                "lod_name": lod_name,
                "objects": [obj.name],
            }
        else:
            if obj.name not in rec["objects"]:
                rec["objects"].append(obj.name)

    return expected

def _is_p3d_resolution_lod_object(obj) -> bool:
    if obj is None or obj.type != "MESH":
        return False
    if not hasattr(obj, "a3ob_properties_object"):
        return False

    try:
        props = obj.a3ob_properties_object
        if not bool(getattr(props, "is_a3_lod", False)):
            return False
        lod_value = str(getattr(props, "lod", "") or "").strip()
        if lod_value == "0":
            return True
        try:
            return int(lod_value) == 0
        except Exception:
            return False
    except Exception:
        return False

def _format_resolution_lod_index_value(value) -> str:
    try:
        num = float(value)
    except Exception:
        raw = str(value or "").strip()
        return raw or "0"

    if math.isfinite(num) and abs(num - round(num)) <= 1e-6:
        return str(int(round(num)))
    return f"{num:g}"

def _actual_top_level_collection_key_under_root(root_collection, obj):
    from .nh_textures import (_best_object_collection_path_under_root)
    source_path = _best_object_collection_path_under_root(root_collection, obj)
    if not source_path:
        return "<root>", ("<root>",)

    actual_parts = tuple(getattr(col, "name", "") or "" for col in list(source_path)[1:])
    if not actual_parts:
        return "<root>", ("<root>",)
    return actual_parts[0], actual_parts

def _collect_resolution_lod_index_conflicts(root_collection, export_objects):
    buckets = {}

    for obj in export_objects:
        if not _is_p3d_resolution_lod_object(obj):
            continue

        try:
            props = obj.a3ob_properties_object
            resolution_value = int(getattr(props, "resolution", 0) or 0)
        except Exception:
            resolution_value = 0

        top_level_key, actual_parts = _actual_top_level_collection_key_under_root(root_collection, obj)
        resolution_key = _format_resolution_lod_index_value(resolution_value)

        branch_bucket = buckets.setdefault(top_level_key, {})
        branch_bucket.setdefault(resolution_key, []).append(
            {
                "object_name": obj.name,
                "actual_branch": actual_parts,
            }
        )

    conflicts = []
    for branch_key, resolution_map in buckets.items():
        for resolution_key, items in resolution_map.items():
            if len(items) <= 1:
                continue
            conflicts.append(
                {
                    "branch_name": branch_key,
                    "resolution_index": resolution_key,
                    "items": items,
                }
            )

    conflicts.sort(
        key=lambda rec: (
            rec["branch_name"],
            rec["resolution_index"],
        )
    )
    return conflicts

def _report_resolution_lod_index_conflicts_in_console(collection_name: str, filepath: str, conflicts):
    if not conflicts:
        return

    print("=== Batch Export Collections: Duplicate Resolution LOD indices ===")
    print(f"Collection: {collection_name}")
    print(f"File: {filepath}")
    print(
        "WARNING: Duplicate Resolution LOD indices were found inside the same actual top-level collection branch."
    )
    for rec in conflicts:
        print(f"Top-level branch: {rec['branch_name']}")
        print(f"Resolution index: {rec['resolution_index']}")
        for item in rec["items"]:
            actual_branch = " > ".join(item["actual_branch"])
            print(f" - {item['object_name']} | actual branch: {actual_branch}")

def _is_p3d_lod_root_object(obj) -> bool:
    if obj is None or obj.type != "MESH" or obj.parent is not None:
        return False
    if not hasattr(obj, "a3ob_properties_object"):
        return False
    try:
        return bool(getattr(obj.a3ob_properties_object, "is_a3_lod", False))
    except Exception:
        return False

def _iter_p3d_export_meshes_for_lod_root(root_obj):
    from .nh_assets import (_is_p3d_proxy_object)
    if not _is_p3d_lod_root_object(root_obj):
        return []

    meshes = [root_obj]
    for child in getattr(root_obj, "children", []):
        if child is None or child.type != "MESH":
            continue
        if _is_p3d_proxy_object(child):
            continue
        meshes.append(child)
    return meshes


def _mesh_ngon_stats(mesh_obj):
    if mesh_obj is None or mesh_obj.type != "MESH" or mesh_obj.data is None:
        return 0, 0

    ngon_count = 0
    max_sides = 0
    for poly in getattr(mesh_obj.data, "polygons", []):
        side_count = len(getattr(poly, "vertices", ()))
        if side_count <= 4:
            continue
        ngon_count += 1
        if side_count > max_sides:
            max_sides = side_count
    return ngon_count, max_sides


def _mesh_isolated_vertex_indices(mesh_obj):
    if mesh_obj is None or mesh_obj.type != "MESH" or mesh_obj.data is None:
        return []

    mesh = mesh_obj.data
    if not getattr(mesh, "vertices", None):
        return []

    used_vertex_indices = set()
    for edge in getattr(mesh, "edges", []):
        try:
            used_vertex_indices.update(edge.vertices)
        except Exception:
            pass
    for poly in getattr(mesh, "polygons", []):
        try:
            used_vertex_indices.update(poly.vertices)
        except Exception:
            pass

    return [vert.index for vert in mesh.vertices if vert.index not in used_vertex_indices]


def _is_point_cloud_export_lod(root_obj, lod_token: str, lod_name: str) -> bool:
    from .nh_scatter import (_MEMORY_COLLECTION_NAME, _MODEL_SPLIT_POINT_CLOUD_LODS)
    if lod_token in _MODEL_SPLIT_POINT_CLOUD_LODS:
        return True

    logical_names = {
        _logical_collection_name(_MEMORY_COLLECTION_NAME),
        _logical_collection_name(_MemoryLodManager.OBJECT_NAME),
    }
    return _logical_collection_name(lod_name) in logical_names or _logical_collection_name(root_obj.name) in logical_names


def _collect_export_loose_vertex_warnings(root_collection, export_objects):
    warnings = []
    seen_lod_roots = set()

    for obj in export_objects:
        if not _is_p3d_lod_root_object(obj):
            continue
        try:
            root_ptr = obj.as_pointer()
        except Exception:
            root_ptr = None
        if root_ptr in seen_lod_roots:
            continue
        if root_ptr is not None:
            seen_lod_roots.add(root_ptr)

        try:
            lod_token = str(getattr(obj.a3ob_properties_object, "lod", "") or "").strip()
        except Exception:
            lod_token = ""

        try:
            lod_name = str(obj.a3ob_properties_object.get_name())
        except Exception:
            lod_name = obj.name

        if _is_point_cloud_export_lod(obj, lod_token, lod_name):
            continue

        for mesh_obj in _iter_p3d_export_meshes_for_lod_root(obj):
            isolated_indices = _mesh_isolated_vertex_indices(mesh_obj)
            isolated_count = len(isolated_indices)
            if isolated_count <= 0:
                continue
            _, actual_parts = _actual_top_level_collection_key_under_root(root_collection, mesh_obj)
            warnings.append(
                {
                    "lod_object_name": obj.name,
                    "lod_name": lod_name,
                    "mesh_object_name": mesh_obj.name,
                    "mesh_object": mesh_obj,
                    "isolated_count": isolated_count,
                    "isolated_indices": isolated_indices,
                    "actual_branch": actual_parts,
                }
            )

    warnings.sort(
        key=lambda rec: (
            rec["lod_name"],
            rec["mesh_object_name"],
        )
    )
    return warnings


def _report_export_loose_vertex_warnings_in_console(collection_name: str, filepath: str, warnings):
    if not warnings:
        return

    print("=== Batch Export Collections: Loose vertices warning ===")
    print(f"Collection: {collection_name}")
    print(f"File: {filepath}")
    print(
        "WARNING: Export continued, but these LODs contain isolated vertices with no edges or faces. "
        "Only Point clouds > Memory is allowed to keep loose points."
    )
    for item in warnings:
        actual_branch = " > ".join(item["actual_branch"])
        indices = list(item.get("isolated_indices", []) or [])
        index_preview = ", ".join(str(idx) for idx in indices[:20])
        if len(indices) > 20:
            index_preview += ", ..."
        if not index_preview:
            index_preview = "<not available>"
        print(
            f" - LOD: {item['lod_name']} | root: {item['lod_object_name']} | "
            f"mesh: {item['mesh_object_name']} | loose vertices: {item['isolated_count']} | "
            f"branch: {actual_branch} | indices: {index_preview}"
        )


def _loose_vertices_outside_memory_root_collections(context):
    from .nh_textures import (_collection_has_any_mesh, _find_p3d_root_collection_for_object)
    scene = getattr(context, "scene", None)
    if scene is None or getattr(scene, "collection", None) is None:
        return []

    active_obj = getattr(context, "active_object", None)
    active_root = _find_p3d_root_collection_for_object(context, active_obj)
    if active_root is not None:
        return [active_root]

    roots = []
    seen = set()

    for obj in getattr(context, "selected_objects", []) or []:
        root = _find_p3d_root_collection_for_object(context, obj)
        if root is None:
            continue
        try:
            ptr = root.as_pointer()
        except Exception:
            ptr = id(root)
        if ptr in seen:
            continue
        seen.add(ptr)
        roots.append(root)

    if roots:
        return roots

    p3d_roots = list(_iter_p3d_root_collections(scene))
    if p3d_roots:
        return [root for root in p3d_roots if _collection_has_any_mesh(root)]

    return [col for col in scene.collection.children if _collection_has_any_mesh(col)]


def _collect_loose_vertices_outside_memory_records(context):
    from .nh_textures import (_collect_collection_objects_recursive)
    records = []
    seen = set()

    for root_collection in _loose_vertices_outside_memory_root_collections(context):
        objects = _collect_collection_objects_recursive(root_collection)
        warnings = _collect_export_loose_vertex_warnings(root_collection, objects)
        for item in warnings:
            mesh_obj = item.get("mesh_object")
            if mesh_obj is None:
                mesh_obj = bpy.data.objects.get(item.get("mesh_object_name", ""))
            if mesh_obj is None or mesh_obj.type != "MESH" or mesh_obj.data is None:
                continue

            isolated_indices = list(item.get("isolated_indices", []) or [])
            if not isolated_indices:
                isolated_indices = _mesh_isolated_vertex_indices(mesh_obj)
            if not isolated_indices:
                continue

            try:
                key = (root_collection.as_pointer(), mesh_obj.as_pointer())
            except Exception:
                key = (id(root_collection), id(mesh_obj))
            if key in seen:
                continue
            seen.add(key)

            rec = dict(item)
            rec["root_collection"] = root_collection
            rec["mesh_object"] = mesh_obj
            rec["isolated_indices"] = isolated_indices
            rec["isolated_count"] = len(isolated_indices)
            records.append(rec)

    records.sort(
        key=lambda rec: (
            getattr(rec.get("root_collection"), "name", ""),
            rec.get("lod_name", ""),
            rec.get("mesh_object_name", ""),
        )
    )
    return records


def _collect_export_ngon_issues(root_collection, export_objects):
    issues = []
    seen_lod_roots = set()

    for obj in export_objects:
        if not _is_p3d_lod_root_object(obj):
            continue
        try:
            root_ptr = obj.as_pointer()
        except Exception:
            root_ptr = None
        if root_ptr in seen_lod_roots:
            continue
        if root_ptr is not None:
            seen_lod_roots.add(root_ptr)

        try:
            lod_name = str(obj.a3ob_properties_object.get_name())
        except Exception:
            lod_name = obj.name

        for mesh_obj in _iter_p3d_export_meshes_for_lod_root(obj):
            ngon_count, max_sides = _mesh_ngon_stats(mesh_obj)
            if ngon_count <= 0:
                continue
            _, actual_parts = _actual_top_level_collection_key_under_root(root_collection, mesh_obj)
            display_path = _format_ngon_lod_display_path(getattr(root_collection, "name", ""), actual_parts, mesh_obj.name)
            issues.append(
                {
                    "lod_object_name": obj.name,
                    "lod_name": lod_name,
                    "mesh_object_name": mesh_obj.name,
                    "ngon_count": ngon_count,
                    "max_sides": max_sides,
                    "actual_branch": actual_parts,
                    "display_path": display_path,
                }
            )

    issues.sort(
        key=lambda rec: (
            rec["lod_name"],
            rec["mesh_object_name"],
        )
    )
    return issues


def _format_ngon_lod_display_path(root_name, branch_parts, object_name=""):
    parts = []
    root_name = (root_name or "").strip()
    if root_name:
        parts.append(root_name)

    for part in branch_parts or []:
        part = (str(part) or "").strip()
        if part and part not in parts:
            parts.append(part)

    object_name = (object_name or "").strip()
    if object_name and object_name not in parts:
        parts.append(object_name)

    return " > ".join(parts) if parts else object_name or "<unknown LOD>"


def _report_export_ngon_issues_in_console(collection_name: str, filepath: str, issues):
    if not issues:
        return

    print("=== Batch Export Collections: N-gons detected ===")
    print(f"Collection: {collection_name}")
    print(f"File: {filepath}")
    print(
        "WARNING: P3D validation will skip LODs that contain n-gons. "
        "Triangulate or remove faces with more than 4 vertices before export."
    )
    for item in issues:
        actual_branch = " > ".join(item["actual_branch"])
        display_path = item.get("display_path") or _format_ngon_lod_display_path(collection_name, item.get("actual_branch"), item.get("mesh_object_name", ""))
        print(
            f" - {display_path} has n-gons | LOD: {item['lod_name']} | root: {item['lod_object_name']} | "
            f"mesh: {item['mesh_object_name']} | n-gon faces: {item['ngon_count']} | "
            f"max verts on one face: {item['max_sides']} | branch: {actual_branch}"
        )


def _scene_collection_paths_for_object(context, obj):
    from .nh_textures import (_find_collection_path)
    scene = getattr(context, "scene", None)
    scene_root = getattr(scene, "collection", None) if scene is not None else None
    labels = []
    seen = set()

    for col in getattr(obj, "users_collection", []) or []:
        label = getattr(col, "name", "") or "<unnamed collection>"
        if scene_root is not None:
            try:
                path = _find_collection_path(scene_root, col.as_pointer())
            except Exception:
                path = None
            if path:
                names = [getattr(item, "name", "") or "<unnamed collection>" for item in path[1:]]
                label = " > ".join(names) if names else getattr(scene_root, "name", "Scene Collection")

        if label in seen:
            continue
        seen.add(label)
        labels.append(label)

    return labels or ["<not linked to scene collection>"]


def _scene_ngon_display_path(context, obj, collection_paths=None):
    from .nh_textures import (_best_object_collection_path_under_root, _find_p3d_root_collection_for_object)
    root = None
    try:
        root = _find_p3d_root_collection_for_object(context, obj)
    except Exception:
        root = None

    if root is not None:
        try:
            path = _best_object_collection_path_under_root(root, obj)
        except Exception:
            path = None
        if path:
            branch_parts = [getattr(col, "name", "") or "" for col in list(path)[1:]]
            return _format_ngon_lod_display_path(getattr(root, "name", ""), branch_parts, getattr(obj, "name", ""))

    collection_paths = list(collection_paths or _scene_collection_paths_for_object(context, obj))
    if collection_paths:
        return _format_ngon_lod_display_path("", [collection_paths[0]], getattr(obj, "name", ""))
    return _format_ngon_lod_display_path("", [], getattr(obj, "name", ""))


def _mesh_ngon_details(mesh_obj, *, use_edit_bmesh=False):
    if mesh_obj is None or mesh_obj.type != "MESH" or mesh_obj.data is None:
        return 0, 0, []

    face_indices = []
    max_sides = 0

    if use_edit_bmesh:
        try:
            bm = bmesh.from_edit_mesh(mesh_obj.data)
            bm.faces.ensure_lookup_table()
            bm.faces.index_update()
            for face in bm.faces:
                if face is None or not face.is_valid:
                    continue
                side_count = len(face.verts)
                if side_count <= 4:
                    continue
                face_indices.append(int(face.index))
                max_sides = max(max_sides, side_count)
            return len(face_indices), max_sides, face_indices
        except Exception:
            pass

    for poly in getattr(mesh_obj.data, "polygons", []) or []:
        side_count = int(getattr(poly, "loop_total", 0) or len(getattr(poly, "vertices", ()) or ()))
        if side_count <= 4:
            continue
        face_indices.append(int(getattr(poly, "index", len(face_indices))))
        max_sides = max(max_sides, side_count)

    return len(face_indices), max_sides, face_indices


def _collect_scene_ngon_mesh_records(context):
    scene = getattr(context, "scene", None)
    if scene is None:
        return []

    edit_object_ptrs = set()
    if getattr(context, "mode", "") == "EDIT_MESH":
        for obj in getattr(context, "objects_in_mode", []) or [getattr(context, "active_object", None)]:
            if obj is None:
                continue
            try:
                edit_object_ptrs.add(obj.as_pointer())
            except Exception:
                pass

    records = []
    for obj in getattr(scene, "objects", []) or []:
        if obj is None or obj.type != "MESH" or obj.data is None:
            continue

        try:
            use_edit_bmesh = obj.as_pointer() in edit_object_ptrs
        except Exception:
            use_edit_bmesh = False

        ngon_count, max_sides, face_indices = _mesh_ngon_details(obj, use_edit_bmesh=use_edit_bmesh)
        if ngon_count <= 0:
            continue

        collection_paths = _scene_collection_paths_for_object(context, obj)
        records.append(
            {
                "object_name": obj.name,
                "mesh_name": getattr(obj.data, "name", ""),
                "ngon_count": ngon_count,
                "max_sides": max_sides,
                "face_indices": face_indices,
                "collection_paths": collection_paths,
                "display_path": _scene_ngon_display_path(context, obj, collection_paths),
            }
        )

    records.sort(
        key=lambda rec: (
            " | ".join(rec["collection_paths"]),
            rec["object_name"].lower(),
            rec["mesh_name"].lower(),
        )
    )
    return records


def _report_scene_ngon_mesh_records_in_console(context, records):
    scene = getattr(context, "scene", None)
    scene_name = getattr(scene, "name", "<unknown>")

    print("")
    print("=== N-gon Mesh Scan ===")
    print(f"Scene: {scene_name}")

    if not records:
        print("No n-gons found in scene mesh objects.")
        return

    total_ngons = sum(int(rec["ngon_count"]) for rec in records)
    print(f"Found {total_ngons} n-gon face(s) in {len(records)} mesh object(s).")
    for rec in records:
        indices = list(rec.get("face_indices", []) or [])
        index_preview = ", ".join(str(idx) for idx in indices[:20])
        if len(indices) > 20:
            index_preview += ", ..."
        if not index_preview:
            index_preview = "<not available>"

        display_path = rec.get("display_path") or rec.get("object_name", "<unknown>")
        print(
            f" - {display_path} has n-gons | object: {rec['object_name']} | mesh data: {rec['mesh_name']} | "
            f"n-gons: {rec['ngon_count']} | max sides: {rec['max_sides']} | "
            f"face indices: {index_preview} | collections: {'; '.join(rec['collection_paths'])}"
        )


def _read_exported_lod_entries(filepath: str):
    p3d_mod = _get_p3d_data_p3d_module()
    if p3d_mod is None:
        raise RuntimeError("P3D data_p3d module is not available")

    mlod = p3d_mod.P3D_MLOD.read_file(filepath, first_lod_only=False)
    exported = {}
    for lod in getattr(mlod, "lods", []):
        try:
            signature = float(lod.resolution)
        except Exception:
            continue
        key = _lod_signature_key(signature)
        exported[key] = {"signature": signature}
    return exported


def _report_missing_lods_in_console(collection_name: str, filepath: str, expected_entries, exported_entries):
    expected_keys = set(expected_entries.keys())
    exported_keys = set(exported_entries.keys())
    missing_keys = sorted(
        expected_keys - exported_keys,
        key=lambda k: expected_entries[k]["signature"],
    )
    if not missing_keys:
        return []

    print("=== Batch Export Collections: Missing LODs ===")
    print(f"Collection: {collection_name}")
    print(f"File: {filepath}")
    print(
        "WARNING: Not all LODs were exported "
        f"(expected unique: {len(expected_keys)}, exported unique: {len(exported_keys)})"
    )
    for key in missing_keys:
        rec = expected_entries[key]
        objs = ", ".join(rec["objects"])
        print(f" - {rec['lod_name']} | signature: {rec['signature']:.6e} | object(s): {objs}")
    return missing_keys


def _format_lod_signature_preview(keys, entries, limit=8):
    if not keys:
        return "<none>"
    parts = []
    for key in list(keys)[:limit]:
        rec = entries.get(key, {})
        lod_name = str(rec.get("lod_name", "") or "").strip()
        signature = rec.get("signature", None)
        if signature is None:
            text = key
        else:
            text = f"{float(signature):.6e}"
        if lod_name:
            text = f"{lod_name} ({text})"
        parts.append(text)
    if len(keys) > limit:
        parts.append("...")
    return ", ".join(parts)


def _read_lod_entries_if_possible(filepath: str):
    from .nh_base import (_fmt_exc)
    try:
        return _read_exported_lod_entries(filepath), ""
    except Exception as e:
        return None, _fmt_exc(e)


def _pending_export_backup_path(filepath: str) -> str:
    return filepath + ".bak.pending"


def _stage_export_backup(filepath: str):
    from .nh_base import (_fmt_exc)
    if not os.path.isfile(filepath):
        return "", "target file does not exist"

    pending_path = _pending_export_backup_path(filepath)
    try:
        if os.path.exists(pending_path):
            os.remove(pending_path)
        shutil.copy2(filepath, pending_path)
    except Exception as e:
        return "", _fmt_exc(e)
    return pending_path, ""


def _lod_entries_missing_expected(entries, expected_entries):
    if not expected_entries:
        return []
    if entries is None:
        return list(expected_entries.keys())
    return sorted(
        set(expected_entries.keys()) - set(entries.keys()),
        key=lambda k: expected_entries[k]["signature"],
    )


def _promote_pending_export_backup(pending_path: str, backup_path: str):
    if not pending_path or not os.path.isfile(pending_path):
        raise RuntimeError("pending backup file is missing")
    shutil.copy2(pending_path, backup_path)
    try:
        os.remove(pending_path)
    except Exception:
        pass


def _discard_pending_export_backup(pending_path: str):
    if not pending_path:
        return
    try:
        if os.path.exists(pending_path):
            os.remove(pending_path)
    except Exception:
        pass


def _finalize_export_backup(filepath: str, pending_path: str, expected_entries, export_complete: bool):
    backup_path = filepath + ".bak"
    if not pending_path:
        return "none", "no backup was staged", []

    pending_entries, pending_err = _read_lod_entries_if_possible(pending_path)
    if pending_entries is None:
        _discard_pending_export_backup(pending_path)
        return "skipped", f"could not verify staged backup: {pending_err}", []

    pending_missing = _lod_entries_missing_expected(pending_entries, expected_entries)
    backup_entries = None
    backup_err = ""
    if os.path.isfile(backup_path):
        backup_entries, backup_err = _read_lod_entries_if_possible(backup_path)

    backup_has_more_lods = (
        backup_entries is not None
        and len(backup_entries) > len(pending_entries)
    )

    if export_complete:
        if backup_has_more_lods:
            _discard_pending_export_backup(pending_path)
            return (
                "preserved",
                (
                    "existing .bak has more LOD signatures "
                    f"({len(backup_entries)}) than pre-export target ({len(pending_entries)})"
                ),
                [],
            )
        _promote_pending_export_backup(pending_path, backup_path)
        if pending_missing:
            return (
                "updated",
                (
                    "export completed with more expected LODs than the pre-export target; "
                    "saved the replaced target as .bak"
                ),
                pending_missing,
            )
        return "updated", f"saved pre-export target with {len(pending_entries)} LOD signature(s)", []

    if pending_missing:
        _discard_pending_export_backup(pending_path)
        if backup_entries is not None:
            return (
                "preserved",
                (
                    "export was partial and the pre-export target was also missing "
                    f"{len(pending_missing)}/{len(expected_entries)} expected LOD signature(s); "
                    "kept existing .bak"
                ),
                pending_missing,
            )
        return (
            "skipped",
            (
                "export was partial and the pre-export target was also missing "
                f"{len(pending_missing)}/{len(expected_entries)} expected LOD signature(s)"
            ),
            pending_missing,
        )

    if backup_has_more_lods:
        _discard_pending_export_backup(pending_path)
        return (
            "preserved",
            (
                "export was partial; kept existing .bak because it has more LOD signatures "
                f"({len(backup_entries)}) than pre-export target ({len(pending_entries)})"
            ),
            [],
        )

    if backup_entries is None and backup_err:
        print("=== Batch Export Collections: Backup verification warning ===")
        print(f"Backup: {backup_path}")
        print(f"WARNING: Existing .bak could not be checked: {backup_err}")

    _promote_pending_export_backup(pending_path, backup_path)
    return (
        "updated",
        (
            "export was partial, but the pre-export target had all expected LOD signatures; "
            "saved it as .bak"
        ),
        [],
    )


def _report_export_backup_skipped_in_console(collection_name: str, filepath: str, reason: str, missing_keys, expected_entries):
    print("=== Batch Export Collections: Backup skipped ===")
    print(f"Collection: {collection_name}")
    print(f"File: {filepath}")
    print(f"Backup: {filepath}.bak")
    print(f"WARNING: Existing target was not copied to .bak: {reason}")
    if missing_keys:
        print(
            "Missing in existing target: "
            f"{_format_lod_signature_preview(missing_keys, expected_entries)}"
        )


def _report_export_backup_preserved_in_console(collection_name: str, filepath: str, reason: str, missing_keys, expected_entries):
    print("=== Batch Export Collections: Backup preserved ===")
    print(f"Collection: {collection_name}")
    print(f"File: {filepath}")
    print(f"Backup: {filepath}.bak")
    print(f"INFO: Existing .bak was kept: {reason}")
    if missing_keys:
        print(
            "Missing in pre-export target: "
            f"{_format_lod_signature_preview(missing_keys, expected_entries)}"
        )


def _report_export_backup_updated_in_console(collection_name: str, filepath: str, reason: str, missing_keys, expected_entries):
    if not reason or not missing_keys:
        return
    print("=== Batch Export Collections: Backup updated ===")
    print(f"Collection: {collection_name}")
    print(f"File: {filepath}")
    print(f"Backup: {filepath}.bak")
    print(f"INFO: {reason}")
    print(
        "Missing in pre-export target: "
        f"{_format_lod_signature_preview(missing_keys, expected_entries)}"
    )


class _P3DValidationCaptureLogger:
    def __init__(self, depth=0):
        self.depth = depth
        self.lines = []

    def start_subproc(self, message=""):
        if message:
            self.step(message)
        self.depth += 1

    def end_subproc(self, showtime=False):
        self.depth = max(0, self.depth - 1)

    def step(self, message):
        self.lines.append(f"{'  ' * self.depth}{message}")


def _is_ascii_text(value) -> bool:
    try:
        str(value or "").encode("ascii")
        return True
    except Exception:
        return False


def _collect_p3d_proxy_validation_diagnostics(operator, proxy_objects):
    from .nh_base import (_fmt_exc)
    lines = []
    for proxy in proxy_objects or []:
        original_name = ""
        try:
            original_name = str(proxy.get("a3ob_original_object", "") or "")
        except Exception:
            original_name = ""
        display_name = original_name or getattr(proxy, "name", "<unnamed proxy>")

        issues = []
        mesh = getattr(proxy, "data", None)
        poly_count = len(getattr(mesh, "polygons", []) or []) if mesh is not None else 0
        vert_count = len(getattr(mesh, "vertices", []) or []) if mesh is not None else 0
        first_face_verts = 0
        if mesh is not None and poly_count > 0:
            try:
                first_face_verts = len(mesh.polygons[0].vertices)
            except Exception:
                first_face_verts = 0
        if poly_count != 1 or first_face_verts != 3:
            issues.append(
                "geometry must be exactly one triangular face "
                f"(verts={vert_count}, faces={poly_count}, first_face_verts={first_face_verts})"
            )

        proxy_props = getattr(proxy, "a3ob_properties_object_proxy", None)
        if proxy_props is None:
            issues.append("missing P3D proxy properties")
        else:
            try:
                proxy_path, _proxy_selection = proxy_props.to_placeholder(operator.relative_paths)
            except Exception as e:
                issues.append(f"proxy path read failed: {_fmt_exc(e)}")
            else:
                if not _is_ascii_text(proxy_path):
                    issues.append(f"proxy path has non-ASCII characters: {proxy_path}")

        bad_groups = [
            group.name for group in getattr(proxy, "vertex_groups", [])
            if not _is_ascii_text(getattr(group, "name", ""))
        ]
        if bad_groups:
            preview = ", ".join(bad_groups[:5])
            if len(bad_groups) > 5:
                preview += ", ..."
            issues.append(f"vertex group name has non-ASCII characters: {preview}")

        for slot_idx, slot in enumerate(getattr(proxy, "material_slots", []) or []):
            mat = getattr(slot, "material", None)
            if mat is None:
                continue
            mat_props = getattr(mat, "a3ob_properties_material", None)
            if mat_props is None:
                issues.append(f"material slot {slot_idx} '{mat.name}' is missing P3D material properties")
                continue
            try:
                texture, material = mat_props.to_p3d(operator.relative_paths)
            except Exception as e:
                issues.append(f"material slot {slot_idx} '{mat.name}' read failed: {_fmt_exc(e)}")
                continue
            if not _is_ascii_text(texture) or not _is_ascii_text(material):
                issues.append(
                    f"material slot {slot_idx} '{mat.name}' has non-ASCII path: "
                    f"texture='{texture}', material='{material}'"
                )

        if issues:
            for issue in issues:
                lines.append(f"Proxy '{display_name}': {issue}")
        else:
            lines.append(f"Proxy '{display_name}': passed detailed proxy checks")

    return lines


def _make_p3d_export_diagnostic_operator(force_all_lods: bool):
    operator = type("_NH_P3DExportDiagnosticOperator", (), {})()
    operator.filepath = ""
    operator.use_selection = True
    operator.visible_only = False
    operator.relative_paths = True
    operator.preserve_normals = True
    operator.validate_meshes = False
    operator.apply_transforms = True
    operator.apply_modifiers = True
    operator.sort_sections = True
    operator.lod_collisions = "IGNORE" if force_all_lods else "SKIP"
    operator.validate_lods = False
    operator.validate_lods_warning_errors = False
    operator.generate_components = True
    operator.force_lowercase = True
    operator.renumber_components = True
    operator.translate_selections = False
    return operator


def _collect_p3d_lod_export_diagnostics(context, source_obj, force_all_lods: bool):
    from .nh_base import (_fmt_exc)
    from .nh_collider_exp import (_is_live_blender_object_exp)
    if source_obj is None or getattr(source_obj, "type", None) != "MESH":
        return ["ERROR: source LOD object is not a mesh"]

    export_mod = _get_p3d_export_p3d_module()
    validator_mod = _get_p3d_validator_module()
    validator_cls = getattr(export_mod, "Validator", None) if export_mod is not None else None
    if validator_cls is None and validator_mod is not None:
        validator_cls = getattr(validator_mod, "Validator", None)

    required_names = (
        "create_temp_collection",
        "cleanup_temp_collection",
        "duplicate_object",
        "get_sub_objects",
        "merge_sub_objects",
        "validate_proxies",
        "temporary_component",
    )
    if export_mod is None or validator_cls is None or any(getattr(export_mod, name, None) is None for name in required_names):
        return ["ERROR: P3D export diagnostics are unavailable (module API not found)"]

    temp_collection = None
    source_had_edit_mode = False
    lines = []
    operator = _make_p3d_export_diagnostic_operator(force_all_lods)
    try:
        temp_collection = export_mod.create_temp_collection(context)
        source_had_edit_mode = getattr(source_obj, "mode", "") == "EDIT"
        if source_had_edit_mode:
            try:
                _deselect_all_in_view_layer(context)
                _select_object_in_view_layer(context, source_obj, active=True)
            except Exception:
                pass
        if getattr(source_obj, "mode", "OBJECT") != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass

        main_obj = export_mod.duplicate_object(source_obj, temp_collection)
        sub_objects, proxy_objects = export_mod.get_sub_objects(source_obj, temp_collection)
        lines.append(
            "Preprocess: "
            f"children={len(getattr(source_obj, 'children', []) or [])}, "
            f"sub-objects={len(sub_objects)}, proxies={len(proxy_objects)}"
        )

        proxy_valid = bool(export_mod.validate_proxies(operator, proxy_objects))
        if not proxy_valid:
            lines.append(
                "ERROR: proxy validation failed "
                "(proxy must be one triangle and use ASCII paths/materials/groups)"
            )
            lines.extend(_collect_p3d_proxy_validation_diagnostics(operator, proxy_objects))

        export_mod.merge_sub_objects(operator, main_obj, sub_objects)
        mesh = getattr(main_obj, "data", None)
        if mesh is not None:
            lines.append(
                "Merged mesh: "
                f"verts={len(getattr(mesh, 'vertices', []))}, "
                f"edges={len(getattr(mesh, 'edges', []))}, "
                f"faces={len(getattr(mesh, 'polygons', []))}, "
                f"materials={len(getattr(main_obj, 'material_slots', []))}, "
                f"uv_layers={len(getattr(mesh, 'uv_layers', []))}"
            )

        logger = _P3DValidationCaptureLogger()
        validator = validator_cls(logger)
        lod_token = str(getattr(main_obj.a3ob_properties_object, "lod", "") or "")
        with export_mod.temporary_component(operator, main_obj):
            validation_valid = bool(
                validator.validate_lod(
                    main_obj,
                    lod_token,
                    False,
                    False,
                    True,
                )
            )

        interesting_lines = [
            line for line in logger.lines
            if "ERROR:" in line or "WARNING:" in line or "Validation " in line
        ]
        lines.extend(interesting_lines or logger.lines[-8:])

        if proxy_valid and validation_valid:
            lines.append(
                "P3D generic validation passed after merge; if this LOD is still missing, "
                "the skip likely happened after validation (duplicate signature or writer-side failure)."
            )
        return lines
    except Exception as e:
        return [f"ERROR: diagnostic failed: {_fmt_exc(e)}"]
    finally:
        if temp_collection is not None:
            try:
                export_mod.cleanup_temp_collection(temp_collection)
            except Exception:
                pass
        if source_had_edit_mode and _is_live_blender_object_exp(source_obj):
            try:
                if context.mode == "OBJECT":
                    _deselect_all_in_view_layer(context)
                    _select_object_in_view_layer(context, source_obj, active=True)
                    bpy.ops.object.mode_set(mode="EDIT")
            except Exception:
                pass


def _report_missing_lod_diagnostics_in_console(
    context,
    collection_name: str,
    filepath: str,
    missing_keys,
    expected_entries,
    export_objects,
    force_all_lods: bool,
):
    if not missing_keys:
        return

    by_name = {}
    for obj in export_objects or []:
        name = getattr(obj, "name", "")
        if name:
            by_name.setdefault(name, obj)

    print("=== Batch Export Collections: Missing LOD diagnostics ===")
    print(f"Collection: {collection_name}")
    print(f"File: {filepath}")
    print(f"Force export all LODs: {'ON' if force_all_lods else 'OFF'}")
    for key in missing_keys:
        rec = expected_entries.get(key, {})
        print(f"LOD: {rec.get('lod_name', key)} | signature: {rec.get('signature', 0.0):.6e}")
        for obj_name in rec.get("objects", []):
            obj = by_name.get(obj_name) or bpy.data.objects.get(obj_name)
            print(f"Object: {obj_name}")
            diagnostic_lines = _collect_p3d_lod_export_diagnostics(context, obj, force_all_lods)
            for line in diagnostic_lines[:40]:
                print(f" - {line}")
            if len(diagnostic_lines) > 40:
                print(f" - ... {len(diagnostic_lines) - 40} more diagnostic line(s)")


def _is_memory_lod_mesh_object(obj) -> bool:
    return _MemoryLodManager.is_memory_lod_mesh_object(obj)

def _ensure_memory_lod_object(context, source_obj, preferred_obj=None):
    return _MemoryLodManager(context, source_obj).ensure_object(preferred_obj=preferred_obj)

def _snap_target_prop_names(side_token: str):
    side = (side_token or "a").lower()
    if side == "v":
        return "paired_object", "paired_memory_object", "Target (V)", "Memory LOD (V)"
    return "source_object", "memory_object", "Target (A)", "Memory LOD (A)"

def _get_snap_target_object(settings, side_token: str, allow_memory_fallback: bool = False):
    target_prop, memory_prop, _target_label, _memory_label = _snap_target_prop_names(side_token)
    obj = getattr(settings, target_prop, None)
    if obj is None and allow_memory_fallback:
        obj = getattr(settings, memory_prop, None)
    return obj

def _set_snap_memory_object(settings, side_token: str, memory_obj):
    _target_prop, memory_prop, _target_label, _memory_label = _snap_target_prop_names(side_token)
    try:
        setattr(settings, memory_prop, memory_obj)
    except Exception:
        pass

def _snap_target_memory_scope_key(context, target_obj):
    from .nh_textures import (_find_p3d_root_collection_for_object)
    if target_obj is None:
        return None

    root_collection = _find_p3d_root_collection_for_object(context, target_obj)
    if root_collection is not None:
        try:
            return ("p3d", root_collection.as_pointer())
        except Exception:
            return ("p3d", root_collection.name)

    for col in getattr(target_obj, "users_collection", []):
        try:
            return ("collection", col.as_pointer())
        except Exception:
            return ("collection", col.name)

    try:
        return ("object", target_obj.as_pointer())
    except Exception:
        return ("object", target_obj.name)

def _ensure_memory_lod_for_snap_target(context, target_obj):
    from .nh_textures import (_find_p3d_root_collection_for_object)
    if target_obj is None or target_obj.type != "MESH" or target_obj.data is None:
        raise RuntimeError("Target Object must be a mesh")

    root_collection = _find_p3d_root_collection_for_object(context, target_obj)
    if root_collection is not None:
        return _MemoryLodManager(context, source_obj=target_obj, parent_collection=root_collection).ensure_object()

    return _MemoryLodManager(context, source_obj=target_obj).ensure_object()

class _MemoryLodManager:
    OBJECT_NAME = "Memory"

    def __init__(self, context, source_obj=None, parent_collection=None):
        self.context = context
        self.source_obj = source_obj
        self.parent_collection = parent_collection

    @staticmethod
    def is_memory_lod_mesh_object(obj) -> bool:
        if obj is None or obj.type != "MESH":
            return False
        if obj.name == _MemoryLodManager.OBJECT_NAME:
            return True
        if not hasattr(obj, "a3ob_properties_object"):
            return False
        try:
            props = obj.a3ob_properties_object
            return str(getattr(props, "lod", "")) == "9"
        except Exception:
            return False

    def pick_existing_object(self):
        if self.parent_collection is not None:
            memory_collection = self.ensure_collection()
            if memory_collection is not None:
                direct = memory_collection.objects.get(self.OBJECT_NAME)
                if direct is not None and direct.type == "MESH":
                    return direct
                for obj in memory_collection.objects:
                    if self.is_memory_lod_mesh_object(obj):
                        return obj
            return None

        if self.source_obj is not None:
            memory_collection = self.ensure_collection()
            if memory_collection is not None:
                direct = memory_collection.objects.get(self.OBJECT_NAME)
                if direct is not None and direct.type == "MESH":
                    return direct
                for obj in memory_collection.objects:
                    if self.is_memory_lod_mesh_object(obj):
                        return obj

            for col in self.source_obj.users_collection:
                obj = col.objects.get(self.OBJECT_NAME)
                if obj is not None and obj.type == "MESH":
                    return obj
            return None

        obj = bpy.data.objects.get(self.OBJECT_NAME)
        if obj is not None and obj.type == "MESH":
            return obj

        for obj in self.context.scene.objects:
            if self.is_memory_lod_mesh_object(obj):
                return obj
        return None

    @staticmethod
    def apply_p3d_props(memory_obj):
        if not hasattr(memory_obj, "a3ob_properties_object"):
            return
        try:
            props = memory_obj.a3ob_properties_object
            props.lod = "9"
            props.is_a3_lod = True
            _remove_p3d_named_property(props, "autocenter")
        except Exception:
            pass

    def ensure_collection(self):
        from .nh_scatter import (_MEMORY_COLLECTION_ALIASES, _MEMORY_COLLECTION_COLOR, _MEMORY_COLLECTION_NAME)
        if self.parent_collection is not None:
            return _ensure_named_child_collection(
                self.parent_collection,
                _MEMORY_COLLECTION_NAME,
                _MEMORY_COLLECTION_COLOR,
                aliases=_MEMORY_COLLECTION_ALIASES,
            )
        return _ensure_memory_collection(self.context, self.source_obj)

    def ensure_object(self, preferred_obj=None):
        from .nh_textures import (_ensure_plain_axis_constraint_for_new_object, _move_object_to_collection)
        if preferred_obj is not None and preferred_obj.type == "MESH":
            memory_obj = preferred_obj
        else:
            memory_obj = self.pick_existing_object()

        memory_collection = self.ensure_collection()
        if memory_obj is None:
            memory_mesh = bpy.data.meshes.new(self.OBJECT_NAME)
            memory_obj = bpy.data.objects.new(self.OBJECT_NAME, memory_mesh)
            if memory_collection is not None:
                memory_collection.objects.link(memory_obj)
            else:
                self.context.scene.collection.objects.link(memory_obj)
            if self.source_obj is not None:
                memory_obj.matrix_world = self.source_obj.matrix_world.copy()
        else:
            _move_object_to_collection(memory_obj, memory_collection)

        self.apply_p3d_props(memory_obj)
        if self.parent_collection is not None:
            _ensure_plain_axis_constraint_for_new_object(
                self.context,
                memory_obj,
                self.parent_collection,
                reference_obj=self.source_obj,
            )
        return memory_obj

def _snap_axis_index_or_none(axis_token: str):
    axis = (axis_token or "").strip().upper()
    if axis == "X":
        return 0
    if axis == "Y":
        return 1
    if axis == "Z":
        return 2
    return None

def _snap_pair_axis_order(points, preferred_axis_token: str = None):
    if len(points) != 2:
        return [0, 1, 2]

    delta = points[1] - points[0]
    axes_by_delta = sorted(range(3), key=lambda idx: (-abs(delta[idx]), idx))
    preferred_axis = _snap_axis_index_or_none(preferred_axis_token)
    max_delta = abs(delta[axes_by_delta[0]]) if axes_by_delta else 0.0
    axis_epsilon = max(1e-6, max_delta * 1e-4)
    if preferred_axis is not None and abs(delta[preferred_axis]) > axis_epsilon:
        return [preferred_axis] + [idx for idx in axes_by_delta if idx != preferred_axis]
    return axes_by_delta

def _sort_snap_pair_world_points(context, world_points, preferred_axis_token: str = None):
    points = [p.copy() for p in world_points]
    if len(points) != 2:
        return points

    axis_order = _snap_pair_axis_order(points, preferred_axis_token=preferred_axis_token)
    points.sort(key=lambda point: tuple(point[idx] for idx in axis_order) + (point[0], point[1], point[2]))
    return points

def _create_snap_pair_in_memory(context, memory_obj, world_points, snap_group: str, snap_side: str, replace_existing: bool, axis_token: str = None):
    mesh = memory_obj.data
    to_local = memory_obj.matrix_world.inverted()
    ordered_world_points = _sort_snap_pair_world_points(context, world_points, preferred_axis_token=axis_token)
    local_points = [to_local @ p for p in ordered_world_points]

    base_idx = len(mesh.vertices)
    mesh.vertices.add(2)
    mesh.vertices[base_idx + 0].co = local_points[0]
    mesh.vertices[base_idx + 1].co = local_points[1]
    mesh.update()

    created_names = []
    for i in range(2):
        vg_name = f".sp_{snap_group}_{snap_side}_{i}"
        if replace_existing:
            old = memory_obj.vertex_groups.get(vg_name)
            if old is not None:
                memory_obj.vertex_groups.remove(old)
        vg = memory_obj.vertex_groups.get(vg_name)
        if vg is None:
            vg = memory_obj.vertex_groups.new(name=vg_name)
        vg.add([base_idx + i], 1.0, "REPLACE")
        created_names.append(vg_name)
    return created_names

def _normalize_snap_p3d_name(value: str) -> str:
    from .nh_base import (_sanitize_snap_p3d_name_value)
    return _sanitize_snap_p3d_name_value(value)

def _build_snap_name_base(p3d_name: str, pair_code: str, axis_token: str) -> str:
    axis = (axis_token or "X").strip().lower() or "x"
    return f"{p3d_name}{pair_code}{axis}"

def _build_snap_point_name(p3d_name: str, pair_code: str, axis_token: str, snap_side: str, point_index: int) -> str:
    base = _build_snap_name_base(p3d_name, pair_code, axis_token)
    return f".sp_{base}_{snap_side}_{point_index}"

def _create_named_snap_points_in_memory(memory_obj, world_points, point_names, replace_existing: bool):
    if memory_obj is None or memory_obj.type != "MESH":
        raise RuntimeError("Memory LOD Object must be a mesh")
    if len(world_points) != len(point_names):
        raise RuntimeError("Point and name count mismatch")

    mesh = memory_obj.data
    to_local = memory_obj.matrix_world.inverted()
    local_points = [(to_local @ point.copy()) for point in world_points]

    base_idx = len(mesh.vertices)
    mesh.vertices.add(len(local_points))
    for offset, local_point in enumerate(local_points):
        mesh.vertices[base_idx + offset].co = local_point
    mesh.update()

    created_names = []
    for offset, point_name in enumerate(point_names):
        if replace_existing:
            old = memory_obj.vertex_groups.get(point_name)
            if old is not None:
                memory_obj.vertex_groups.remove(old)

        vg = memory_obj.vertex_groups.get(point_name)
        if vg is None:
            vg = memory_obj.vertex_groups.new(name=point_name)
        vg.add([base_idx + offset], 1.0, "REPLACE")
        created_names.append(point_name)
    return created_names

class _SnapPointNamePattern:
    def __init__(self, p3d_name: str, pair_code: str, axis_token: str):
        self.p3d_name = p3d_name
        self.pair_code = pair_code
        self.axis_token = (axis_token or "X").strip().upper() or "X"

    @classmethod
    def from_settings(cls, settings):
        p3d_name = _normalize_snap_p3d_name(getattr(settings, "snap_p3d_name", "") or getattr(settings, "snap_group", ""))
        if not p3d_name:
            raise RuntimeError("P3D Name is empty")
        if not _SP_P3D_NAME_RE.fullmatch(p3d_name):
            raise RuntimeError("P3D Name must contain only letters and digits")

        pair_code = (getattr(settings, "snap_pair_code", "") or "").strip()
        if not pair_code:
            raise RuntimeError("ID is empty")
        if not _SP_PAIR_CODE_RE.fullmatch(pair_code):
            raise RuntimeError("ID must contain 1-3 letters or digits")
        return cls(p3d_name=p3d_name, pair_code=pair_code, axis_token=getattr(settings, "edge_axis", "X"))

    @classmethod
    def from_preview_settings(cls, settings):
        p3d_name = _normalize_snap_p3d_name(getattr(settings, "snap_p3d_name", "") or getattr(settings, "snap_group", "")) or "SampleName"
        if not _SP_P3D_NAME_RE.fullmatch(p3d_name):
            p3d_name = "SampleName"

        pair_code = (getattr(settings, "snap_pair_code", "") or "").strip() or "01"
        if not _SP_PAIR_CODE_RE.fullmatch(pair_code):
            pair_code = "01"
        return cls(p3d_name=p3d_name, pair_code=pair_code, axis_token=getattr(settings, "edge_axis", "X"))

    @property
    def preview_base(self) -> str:
        return _build_snap_name_base(self.p3d_name, self.pair_code, self.axis_token)

    def build_pair_names(self, snap_side: str):
        return [
            _build_snap_point_name(self.p3d_name, self.pair_code, self.axis_token, snap_side, point_index)
            for point_index in range(2)
        ]

class _SnapPointPairBuilder:
    _SIDE_LABELS = {
        "a": "Target (A)",
        "v": "Target (V)",
    }
    _MEMORY_LABELS = {
        "a": "Memory LOD (A)",
        "v": "Memory LOD (V)",
    }

    def __init__(self, context, settings):
        self.context = context
        self.settings = settings
        self.naming = _SnapPointNamePattern.from_settings(settings)

    def _require_mesh_object(self, obj, label: str):
        if obj is None or obj.type != "MESH" or obj.data is None:
            raise RuntimeError(f"{label} must be a mesh")
        return obj

    def resolve_target_object(self, side_token: str):
        side = (side_token or "a").lower()
        target_obj = _get_snap_target_object(self.settings, side, allow_memory_fallback=True)
        label = self._SIDE_LABELS.get(side, "Target")
        if target_obj is None:
            raise RuntimeError(f"Pick {label} first")
        return self._require_mesh_object(target_obj, label)

    def resolve_memory_object(self, side_token: str):
        side = (side_token or "a").lower()
        target_obj = self.resolve_target_object(side)
        memory_obj = _ensure_memory_lod_for_snap_target(self.context, target_obj)
        memory_label = self._MEMORY_LABELS.get(side, "Memory LOD")
        memory_obj = self._require_mesh_object(memory_obj, memory_label)
        _set_snap_memory_object(self.settings, side, memory_obj)
        return target_obj, memory_obj

    def ensure_object_mode(self):
        from .nh_base import (_fmt_exc)
        if self.context.mode == "OBJECT":
            return
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception as e:
            raise RuntimeError(f"Failed to switch to Object Mode: {_fmt_exc(e)}")

    def collect_selected_points(self):
        edit_obj = getattr(self.context, "edit_object", None)
        if edit_obj is None or edit_obj.type != "MESH":
            raise RuntimeError("Select exactly 2 vertices in Edit Mode on any mesh")

        world_points = _collect_snap_pair_selected_world_points(edit_obj)
        if len(world_points) != 2:
            raise RuntimeError("Select exactly 2 vertices in Edit Mode")

        return _sort_snap_pair_world_points(self.context, world_points, preferred_axis_token=self.naming.axis_token)

    def create_dual_model_set(self):
        world_points = self.collect_selected_points()
        target_a, memory_a = self.resolve_memory_object("a")
        target_v, memory_v = self.resolve_memory_object("v")
        if memory_a == memory_v:
            raise RuntimeError("Choose targets from two different model roots")

        self.ensure_object_mode()
        self.settings.snap_p3d_name = self.naming.p3d_name

        created_names = []
        targets = (
            ("a", target_a, memory_a),
            ("v", target_v, memory_v),
        )
        for side_token, _target_obj, memory_obj in targets:
            created_names.extend(
                _create_named_snap_points_in_memory(
                    memory_obj=memory_obj,
                    world_points=world_points,
                    point_names=self.naming.build_pair_names(side_token),
                    replace_existing=self.settings.replace_existing,
                )
            )
        return targets, created_names

def _collect_snap_pair_selected_world_points(source_obj):
    from .nh_collider import (_dedupe_world_points)
    if source_obj is None or source_obj.type != "MESH" or source_obj.mode != "EDIT":
        return []

    bm = bmesh.from_edit_mesh(source_obj.data)
    selected = [source_obj.matrix_world @ vert.co for vert in bm.verts if vert.select]
    if not selected:
        return []
    return _dedupe_world_points(selected)

def _axis_index_from_token(token: str) -> int:
    t = (token or "").upper()
    if t == "X":
        return 0
    if t == "Y":
        return 1
    return 2

def _pick_span_axis_index(edge_axis_idx: int, span_token: str) -> int:
    t = (span_token or "AUTO").upper()
    if t == "AUTO":
        # For walls/segments AUTO uses horizontal perpendicular axis.
        return 1 if edge_axis_idx == 0 else 0
    idx = _axis_index_from_token(t)
    if idx == edge_axis_idx:
        return 2 if edge_axis_idx != 2 else 0
    return idx

def _auto_snap_points_from_model_edge(model_obj, edge_axis_token: str, edge_side_token: str,
                                      span_axis_token: str, edge_tolerance: float):
    if model_obj is None or model_obj.type != "MESH" or model_obj.data is None:
        raise RuntimeError("Model Object must be a mesh")
    if len(model_obj.data.vertices) < 2:
        raise RuntimeError("Model object must have at least 2 vertices")

    edge_axis = _axis_index_from_token(edge_axis_token)
    span_axis = _pick_span_axis_index(edge_axis, span_axis_token)
    verts_local = [v.co.copy() for v in model_obj.data.vertices]

    edge_values = [v[edge_axis] for v in verts_local]
    edge_min = min(edge_values)
    edge_max = max(edge_values)
    edge_range = edge_max - edge_min
    target_edge = edge_min if (edge_side_token or "POS").upper() == "NEG" else edge_max

    tol_abs = max(1e-6, edge_range * max(0.0, edge_tolerance))
    candidates = [v for v in verts_local if abs(v[edge_axis] - target_edge) <= tol_abs]
    if len(candidates) < 2:
        sorted_by_edge = sorted(verts_local, key=lambda v: abs(v[edge_axis] - target_edge))
        candidates = sorted_by_edge[:max(2, len(sorted_by_edge))]

    if len(candidates) < 2:
        raise RuntimeError("Could not detect enough vertices on selected edge")

    v0 = min(candidates, key=lambda v: v[span_axis])
    v1 = max(candidates, key=lambda v: v[span_axis])
    if (v0 - v1).length_squared < 1e-12:
        farthest = None
        best_d2 = -1.0
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                d2 = (candidates[i] - candidates[j]).length_squared
                if d2 > best_d2:
                    best_d2 = d2
                    farthest = (candidates[i], candidates[j])
        if farthest is None:
            raise RuntimeError("Failed to determine distinct edge points")
        v0, v1 = farthest

    return [model_obj.matrix_world @ v0, model_obj.matrix_world @ v1]

def _pick_model_mesh_from_objects(objs):
    meshes = [o for o in objs if o is not None and o.type == "MESH" and o.data is not None]
    if not meshes:
        return None

    for o in meshes:
        if (o.name or "").strip().lower() == "resolution 0":
            return o

    non_memory = [o for o in meshes if not _is_memory_lod_mesh_object(o)]
    if non_memory:
        return max(non_memory, key=lambda o: len(o.data.polygons) if o.data else 0)

    return max(meshes, key=lambda o: len(o.data.polygons) if o.data else 0)

def _pick_memory_mesh_from_objects(objs):
    for o in objs:
        if _is_memory_lod_mesh_object(o):
            return o
    return None

def _deselect_all_in_view_layer(context):
    for o in context.view_layer.objects:
        if o.select_get():
            o.select_set(False)


def _object_is_in_view_layer(context, obj) -> bool:
    view_layer = getattr(context, "view_layer", None)
    if view_layer is None or obj is None:
        return False
    try:
        obj_ptr = obj.as_pointer()
    except Exception:
        return False

    try:
        found = view_layer.objects.get(obj.name)
        if found is not None and found.as_pointer() == obj_ptr:
            return True
    except Exception:
        pass

    try:
        for layer_obj in view_layer.objects:
            if layer_obj is not None and layer_obj.as_pointer() == obj_ptr:
                return True
    except Exception:
        pass
    return False


def _ensure_object_selectable_in_view_layer(context, obj) -> bool:
    from .nh_textures import (_ensure_collection_visible_in_view_layer)
    if obj is None or bpy.data.objects.get(getattr(obj, "name", "")) is None:
        return False

    for col in list(getattr(obj, "users_collection", []) or []):
        try:
            _ensure_collection_visible_in_view_layer(context, col)
        except Exception:
            pass
    try:
        _set_object_view_visible(obj, True)
    except Exception:
        pass
    try:
        obj.hide_select = False
    except Exception:
        pass
    try:
        context.view_layer.update()
    except Exception:
        pass
    return _object_is_in_view_layer(context, obj)


def _select_object_in_view_layer(context, obj, *, active=False):
    if not _ensure_object_selectable_in_view_layer(context, obj):
        name = getattr(obj, "name", "<unknown>")
        raise RuntimeError(f"Object '{name}' is not available in the current View Layer")

    obj.select_set(True)
    if active:
        context.view_layer.objects.active = obj
    return obj


def _cleanup_imported_objects(imported_obj_names, pre_collection_ptrs):
    from .nh_textures import (_obj_depth)
    live = [bpy.data.objects.get(n) for n in imported_obj_names]
    live = [o for o in live if o is not None]
    live.sort(key=_obj_depth, reverse=True)
    for obj in live:
        if bpy.data.objects.get(obj.name) is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    for col in list(bpy.data.collections):
        if col.as_pointer() in pre_collection_ptrs:
            continue
        if len(col.objects) != 0 or len(col.children) != 0:
            continue
        try:
            bpy.data.collections.remove(col)
        except Exception:
            pass

def _is_p3d_root_collection_name(name: str) -> bool:
    from .nh_textures import (_strip_blender_numeric_suffix)
    base = _strip_blender_numeric_suffix((name or "").strip())
    return base.lower().endswith(".p3d")

def _iter_p3d_root_collections(scene):
    from .nh_textures import (_collect_collections_deep)
    if scene is None or scene.collection is None:
        return []

    roots = []
    for col in _collect_collections_deep(scene.collection):
        if col is None or col == scene.collection:
            continue
        if _is_p3d_root_collection_name(col.name):
            roots.append(col)
    return roots


def _is_visuals_collection_name(name: str) -> bool:
    from .nh_scatter import (_VISUALS_COLLECTION_NAME)
    from .nh_textures import (_strip_blender_numeric_suffix)
    return _strip_blender_numeric_suffix(name).strip().lower() == _VISUALS_COLLECTION_NAME.lower()


def _is_point_clouds_collection_name(name: str) -> bool:
    from .nh_scatter import (_MEMORY_COLLECTION_ALIASES, _MEMORY_COLLECTION_NAME)
    from .nh_textures import (_strip_blender_numeric_suffix)
    logical_name = _strip_blender_numeric_suffix(name).strip().lower()
    allowed_names = (_MEMORY_COLLECTION_NAME, *_MEMORY_COLLECTION_ALIASES)
    return logical_name in {item.lower() for item in allowed_names}


def _is_resolution0_visual_lod_object(obj) -> bool:
    from .nh_textures import (_strip_blender_numeric_suffix)
    if obj is None:
        return False

    if _is_p3d_resolution_lod_object(obj):
        try:
            props = obj.a3ob_properties_object
            resolution = float(getattr(props, "resolution", getattr(props, "resolution_float", 0.0)) or 0.0)
            return abs(resolution) <= 1e-6
        except Exception:
            return False

    name = _strip_blender_numeric_suffix(getattr(obj, "name", "") or "").strip().lower()
    return name == "resolution 0" or name.startswith("resolution 0 ")


def _layer_collection_map(context):
    from .nh_textures import (_iter_layer_collections)
    layer_root = getattr(getattr(context, "view_layer", None), "layer_collection", None)
    if layer_root is None:
        return {}
    return {lc.collection.as_pointer(): lc for lc in _iter_layer_collections(layer_root)}


def _set_collection_view_visible(layer_map, collection, visible: bool):
    lc = layer_map.get(collection.as_pointer()) if layer_map else None
    if lc is not None:
        try:
            lc.exclude = False
        except Exception:
            pass
        try:
            lc.hide_viewport = not visible
        except Exception:
            pass

    try:
        collection.hide_viewport = not visible
    except Exception:
        pass


def _set_object_view_visible(obj, visible: bool):
    try:
        obj.hide_set(not visible)
    except Exception:
        pass
    try:
        obj.hide_viewport = not visible
    except Exception:
        pass


def _iter_object_tree(root_obj):
    stack = [root_obj]
    while stack:
        obj = stack.pop()
        if obj is None:
            continue
        yield obj
        stack.extend(reversed(list(getattr(obj, "children", ()))))


def _set_p3d_visual_collection_visibility(context, *, visuals_only: bool):
    from .nh_textures import (_collect_collection_objects_recursive, _ensure_collection_visible_in_view_layer, _iter_collection_tree)
    roots = list(_iter_p3d_root_collections(context.scene))
    if not roots:
        return 0, 0

    layer_map = _layer_collection_map(context)
    changed = 0

    for root_col in roots:
        _ensure_collection_visible_in_view_layer(context, root_col)
        if visuals_only:
            visual_ptrs = set()
            visual_obj_ptrs = set()
            point_cloud_ptrs = set()
            point_cloud_obj_ptrs = set()
            for child in root_col.children:
                if _is_visuals_collection_name(child.name):
                    for visual_col in _iter_collection_tree(child):
                        visual_ptrs.add(visual_col.as_pointer())
                        for obj in visual_col.objects:
                            if not _is_resolution0_visual_lod_object(obj):
                                continue
                            for visible_obj in _iter_object_tree(obj):
                                visual_obj_ptrs.add(visible_obj.as_pointer())
                    continue

                if _is_point_clouds_collection_name(child.name):
                    for point_col in _iter_collection_tree(child):
                        point_cloud_ptrs.add(point_col.as_pointer())
                        for obj in point_col.objects:
                            point_cloud_obj_ptrs.add(obj.as_pointer())

            if not visual_ptrs:
                continue

            for col in _iter_collection_tree(root_col):
                col_ptr = col.as_pointer()
                visible = (col is root_col) or (col_ptr in visual_ptrs) or (col_ptr in point_cloud_ptrs)
                _set_collection_view_visible(layer_map, col, visible)
                changed += 1

            for obj in _collect_collection_objects_recursive(root_col):
                obj_ptr = obj.as_pointer()
                _set_object_view_visible(obj, (obj_ptr in visual_obj_ptrs) or (obj_ptr in point_cloud_obj_ptrs))
                changed += 1
            continue

        for col in _iter_collection_tree(root_col):
            _set_collection_view_visible(layer_map, col, True)
            changed += 1
        for obj in _collect_collection_objects_recursive(root_col):
            _set_object_view_visible(obj, True)
            changed += 1

    return len(roots), changed


class CRAY_OT_SnapSetP3DVisualsOnly(Operator):
    bl_idname = "cray.snap_set_p3d_visuals_only"
    bl_label = "Visual 0 Only"
    bl_description = "РџРѕРєР°Р·С‹РІР°РµС‚ С‚РѕР»СЊРєРѕ Resolution 0 РІ Visuals Рё РѕСЃС‚Р°РІР»СЏРµС‚ РІРёРґРёРјС‹РјРё Point clouds РІРЅСѓС‚СЂРё РєР°Р¶РґРѕР№ .p3d РєРѕР»Р»РµРєС†РёРё"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        roots, changed = _set_p3d_visual_collection_visibility(context, visuals_only=True)
        if roots <= 0:
            self.report({"ERROR"}, "No .p3d collections found in the scene")
            return {"CANCELLED"}
        if changed <= 0:
            self.report({"WARNING"}, "No Visuals collections found inside .p3d roots")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Visual 0 only: updated {changed} item(s) in {roots} .p3d root(s)")
        return {"FINISHED"}


class CRAY_OT_SnapShowAllP3DCollections(Operator):
    bl_idname = "cray.snap_show_all_p3d_collections"
    bl_label = "Show All"
    bl_description = "РџРѕРєР°Р·С‹РІР°РµС‚ РІСЃРµ РІРµС‚РєРё Рё РѕР±СЉРµРєС‚С‹ РІРЅСѓС‚СЂРё РєР°Р¶РґРѕР№ .p3d РєРѕР»Р»РµРєС†РёРё"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        roots, changed = _set_p3d_visual_collection_visibility(context, visuals_only=False)
        if roots <= 0:
            self.report({"ERROR"}, "No .p3d collections found in the scene")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Show all: updated {changed} item(s) in {roots} .p3d root(s)")
        return {"FINISHED"}


class CRAY_OT_EnsureMemoryLOD(Operator):
    bl_idname = "cray.ensure_memory_lod"
    bl_label = "Create/Find Point clouds > Memory"
    bl_description = "РќР°С…РѕРґРёС‚ РёР»Рё СЃРѕР·РґР°С‘С‚ Point clouds > Memory РґР»СЏ РІС‹Р±СЂР°РЅРЅС‹С… A Target Рё V Target; РµСЃР»Рё С†РµР»Рё РЅРµ РІС‹Р±СЂР°РЅС‹, РіРѕС‚РѕРІРёС‚ Memory РґР»СЏ РІСЃРµС… .p3d РєРѕР»Р»РµРєС†РёР№ СЃС†РµРЅС‹"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ss = context.scene.cray_snap_settings
        prepared = []
        prepared_scopes = set()

        for side_token in ("a", "v"):
            target_obj = _get_snap_target_object(ss, side_token, allow_memory_fallback=False)
            if target_obj is None:
                continue

            _target_prop, _memory_prop, target_label, _memory_label = _snap_target_prop_names(side_token)
            if target_obj.type != "MESH" or target_obj.data is None:
                self.report({"ERROR"}, f"{target_label} must be a mesh")
                return {"CANCELLED"}

            scope_key = _snap_target_memory_scope_key(context, target_obj)
            if scope_key in prepared_scopes:
                continue

            memory_obj = _ensure_memory_lod_for_snap_target(context, target_obj)
            _set_snap_memory_object(ss, side_token, memory_obj)
            prepared_scopes.add(scope_key)
            prepared.append(f"{target_obj.name} -> {memory_obj.name}")

        if prepared:
            preview = ", ".join(prepared[:3])
            if len(prepared) > 3:
                preview = f"{preview}, ..."
            self.report({"INFO"}, f"Prepared {len(prepared)} target Memory LODs: {preview}")
            return {"FINISHED"}

        root_collections = _iter_p3d_root_collections(context.scene)
        if not root_collections:
            self.report({"ERROR"}, "No .p3d collections found in the scene")
            return {"CANCELLED"}

        prepared = []
        for root_col in root_collections:
            memory_obj = _MemoryLodManager(context, parent_collection=root_col).ensure_object()
            prepared.append(f"{root_col.name} -> {memory_obj.name}")

        preview = ", ".join(prepared[:3])
        if len(prepared) > 3:
            preview = f"{preview}, ..."
        self.report({"INFO"}, f"Prepared {len(prepared)} Memory LODs: {preview}")
        return {"FINISHED"}

class CRAY_OT_CreateSnapPairFromModelEdge(Operator):
    bl_idname = "cray.create_snap_pair_from_model_edge"
    bl_label = "Create Snap Points"
    bl_description = (
        "РљРѕРїРёСЂСѓРµС‚ 2 РІС‹РґРµР»РµРЅРЅС‹Рµ РІРµСЂС€РёРЅС‹ РёР· Edit Mode РІ Point clouds > Memory РІС‹Р±СЂР°РЅРЅС‹С… A Target Рё V Target, СЃРѕР·РґР°РІР°СЏ РїР°СЂС‹ .sp_a/.sp_v РїРѕ С‚РµРєСѓС‰РµРјСѓ С€Р°Р±Р»РѕРЅСѓ РёРјРµРЅРё"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        ss = context.scene.cray_snap_settings
        try:
            targets, created_names = _SnapPointPairBuilder(context, ss).create_dual_model_set()
        except Exception as e:
            self.report({"ERROR"}, _fmt_exc(e))
            return {"CANCELLED"}

        target_names = ", ".join(
            f"{side.upper()}: {target_obj.name} -> {memory_obj.name}"
            for side, target_obj, memory_obj in targets
        )
        self.report(
            {"INFO"},
            (
                f"Created {len(created_names)} snap points in {target_names}: "
                f"{', '.join(created_names)}"
            ),
        )
        return {"FINISHED"}

class CRAY_OT_SnapBatchProcess(Operator):
    bl_idname = "cray.snap_batch_process"
    bl_label = "Batch Process P3D (Backup + Snap)"
    bl_options = {"REGISTER"}

    filter_glob: StringProperty(default="*.p3d", options={"HIDDEN"})
    directory: StringProperty(subtype="DIR_PATH")
    files: CollectionProperty(type=OperatorFileListElement)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        ss = context.scene.cray_snap_settings

        if not _has_any_p3d_io_ops():
            self.report({"ERROR"}, "P3D import/export operators not found")
            return {"CANCELLED"}

        snap_group = (ss.snap_group or "").strip()
        if not snap_group:
            self.report({"ERROR"}, "Snap Group is empty")
            return {"CANCELLED"}
        if not _SP_GROUP_RE.fullmatch(snap_group):
            self.report({"ERROR"}, "Snap Group must contain only letters, digits and underscores")
            return {"CANCELLED"}

        paths = []
        for item in self.files:
            p = os.path.join(self.directory, item.name)
            paths.append(bpy.path.abspath(p))
        if not paths:
            self.report({"ERROR"}, "No files selected")
            return {"CANCELLED"}

        prev_selected_names = [o.name for o in context.selected_objects]
        prev_active_name = context.view_layer.objects.active.name if context.view_layer.objects.active else None

        ok_count = 0
        fail_count = 0
        backup_count = 0
        exported_count = 0
        failures = []

        for filepath in paths:
            if not os.path.isfile(filepath):
                fail_count += 1
                failures.append((filepath, "file-not-found"))
                continue

            bak_path = filepath + ".bak"
            try:
                if os.path.exists(bak_path) and not ss.batch_overwrite_bak:
                    bak_path = filepath + ".bak.prev"
                    if os.path.exists(bak_path):
                        prev2_path = filepath + ".bak.prev2"
                        if os.path.exists(prev2_path):
                            os.remove(prev2_path)
                        shutil.move(bak_path, prev2_path)
                shutil.copy2(filepath, bak_path)
                backup_count += 1
            except Exception as e:
                fail_count += 1
                failures.append((filepath, f"backup-failed: {_fmt_exc(e)}"))
                continue

            pre_obj_ptrs = {o.as_pointer() for o in bpy.data.objects}
            pre_col_ptrs = {c.as_pointer() for c in bpy.data.collections}

            with _suppress_p3d_import_tracking():
                _, used_import, import_err = _call_first_available(
                    _P3D_IMPORT_CANDIDATES,
                    filepath=filepath,
                    first_lod_only=False,
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
            if used_import is None:
                fail_count += 1
                failures.append((filepath, f"import-failed: {_fmt_exc(import_err) if import_err else 'no operator'}"))
                continue

            imported_objs = [o for o in bpy.data.objects if o.as_pointer() not in pre_obj_ptrs]
            imported_names = [o.name for o in imported_objs]
            if not imported_objs:
                fail_count += 1
                failures.append((filepath, "import-produced-no-objects"))
                continue

            model_obj = _pick_model_mesh_from_objects(imported_objs)
            if model_obj is None:
                fail_count += 1
                failures.append((filepath, "no-mesh-model-found"))
                if ss.batch_cleanup_imported:
                    _cleanup_imported_objects(imported_names, pre_col_ptrs)
                continue

            memory_obj = _pick_memory_mesh_from_objects(imported_objs)
            memory_obj = _ensure_memory_lod_object(context, model_obj, preferred_obj=memory_obj)

            try:
                world_points = _auto_snap_points_from_model_edge(
                    model_obj=model_obj,
                    edge_axis_token=ss.edge_axis,
                    edge_side_token=ss.edge_side,
                    span_axis_token=ss.edge_span_axis,
                    edge_tolerance=ss.edge_tolerance,
                )
                _create_snap_pair_in_memory(
                    context=context,
                    memory_obj=memory_obj,
                    world_points=world_points,
                    snap_group=snap_group,
                    snap_side=ss.snap_side,
                    replace_existing=ss.replace_existing,
                    axis_token=ss.edge_axis,
                )
            except Exception as e:
                fail_count += 1
                failures.append((filepath, f"snap-failed: {_fmt_exc(e)}"))
                if ss.batch_cleanup_imported:
                    _cleanup_imported_objects(imported_names, pre_col_ptrs)
                continue

            _deselect_all_in_view_layer(context)
            for name in imported_names:
                live = bpy.data.objects.get(name)
                if live is None:
                    continue
                try:
                    live.hide_set(False)
                except Exception:
                    pass
                try:
                    live.hide_viewport = False
                except Exception:
                    pass
                live.select_set(True)
            if bpy.data.objects.get(model_obj.name) is not None:
                context.view_layer.objects.active = bpy.data.objects.get(model_obj.name)

            named_property_restore = _strip_p3d_named_properties_for_export(
                [bpy.data.objects.get(name) for name in imported_names]
            )
            try:
                _, used_export, export_err = _call_first_available(
                    _P3D_EXPORT_CANDIDATES,
                    filepath=filepath,
                    use_selection=True,
                    visible_only=True,
                    relative_paths=True,
                    preserve_normals=True,
                    validate_meshes=False,
                    apply_transforms=True,
                    apply_modifiers=True,
                    sort_sections=True,
                    lod_collisions="SKIP",
                    validate_lods=False,
                    generate_components=True,
                    renumber_components=True,
                    translate_selections=False,
                    force_lowercase=True,
                )
            finally:
                _restore_p3d_named_properties_after_export(named_property_restore)
            if used_export is None:
                fail_count += 1
                failures.append((filepath, f"export-failed: {_fmt_exc(export_err) if export_err else 'no operator'}"))
            else:
                ok_count += 1
                exported_count += 1

            if ss.batch_cleanup_imported:
                _cleanup_imported_objects(imported_names, pre_col_ptrs)

        _deselect_all_in_view_layer(context)
        for name in prev_selected_names:
            o = bpy.data.objects.get(name)
            if o is not None:
                o.select_set(True)
        if prev_active_name and bpy.data.objects.get(prev_active_name) is not None:
            context.view_layer.objects.active = bpy.data.objects.get(prev_active_name)

        if failures:
            print("=== Batch Snap Process Failures ===")
            for path, reason in failures:
                print(f"{path} :: {reason}")

        msg = f"Batch done: ok {ok_count}/{len(paths)}, exported {exported_count}, backups {backup_count}, failed {fail_count}"
        if fail_count > 0:
            self.report({"WARNING"}, msg + " (see System Console)")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


# ------------------------------------------------------------------------
#  Collider helper tools for Geometry LOD
# ------------------------------------------------------------------------

def _collider_lod_name(lod_token: str) -> str:
    from .nh_scatter import (_COLLIDER_KNOWN_LOD_NAMES)
    return _COLLIDER_KNOWN_LOD_NAMES.get(str(lod_token), f"LOD {lod_token}")


def _is_collider_lod_mesh_object(obj, lod_token=None) -> bool:
    from .nh_scatter import (_COLLIDER_KNOWN_LOD_NAMES, _actual_collider_lod_token_from_object)
    if obj is None or obj.type != "MESH":
        return False

    expected = str(lod_token) if lod_token is not None else None
    value = _actual_collider_lod_token_from_object(obj)
    if not value:
        return False

    if expected is None:
        return value in _COLLIDER_KNOWN_LOD_NAMES
    return value == expected


def _object_in_logical_collection(obj, collection_name: str) -> bool:
    from .nh_scatter import (_COLLIDER_COLLECTION_ALIASES, _COLLIDER_COLLECTION_NAME)
    if obj is None:
        return False

    wanted_names = _logical_collection_names(collection_name)
    if collection_name == _COLLIDER_COLLECTION_NAME:
        wanted_names.update(_logical_collection_names(_COLLIDER_COLLECTION_ALIASES))
    for col in getattr(obj, "users_collection", []):
        if _logical_collection_name(getattr(col, "name", "")) in wanted_names:
            return True
    return False


def _is_auto_reusable_collider_target(obj, lod_token=None) -> bool:
    from .nh_scatter import (_COLLIDER_COLLECTION_NAME)
    if obj is None or obj.type != "MESH":
        return False

    if _is_collider_lod_mesh_object(obj, lod_token=lod_token):
        return True

    if lod_token is None:
        return False

    expected_name = _logical_collection_name(_collider_lod_name(lod_token))
    return (
        _object_in_logical_collection(obj, _COLLIDER_COLLECTION_NAME)
        and _logical_collection_name(getattr(obj, "name", "") or "") == expected_name
    )


def _pick_collider_lod_object(context, source_obj, lod_token, exclude_obj=None):
    from .nh_scatter import (_COLLIDER_COLLECTION_ALIASES, _COLLIDER_COLLECTION_NAME)
    expected_name = _collider_lod_name(lod_token)

    parent = _preferred_collider_parent_collection(context, source_obj)
    collider_collection = _find_named_child_collection(
        parent,
        _COLLIDER_COLLECTION_NAME,
        aliases=_COLLIDER_COLLECTION_ALIASES,
    )
    if collider_collection is None:
        return None

    direct = collider_collection.objects.get(expected_name)
    if direct != exclude_obj and _is_auto_reusable_collider_target(direct, lod_token=lod_token):
        return direct

    for obj in collider_collection.objects:
        if obj == exclude_obj:
            continue
        if _is_auto_reusable_collider_target(obj, lod_token=lod_token):
            return obj

    return None


def _find_parent_collection(root_collection, target_collection):
    if root_collection is None or target_collection is None:
        return None

    for child in root_collection.children:
        if child == target_collection:
            return root_collection
        found = _find_parent_collection(child, target_collection)
        if found is not None:
            return found
    return None


def _logical_collection_name(name: str) -> str:
    return re.sub(r"\.\d{3}$", "", (name or "").strip().lower())


def _logical_collection_names(*names) -> set:
    result = set()
    for name in names:
        if not name:
            continue
        if isinstance(name, (tuple, list, set)):
            result.update(_logical_collection_names(*name))
            continue
        result.add(_logical_collection_name(name))
    return result


def _preferred_collider_parent_collection(context, source_obj):
    source_col = None
    if source_obj is not None and source_obj.users_collection:
        source_col = source_obj.users_collection[0]
    if source_col is None:
        return context.scene.collection

    parent = _find_parent_collection(context.scene.collection, source_col)
    if parent is None:
        return source_col

    logical_group_names = {
        "visuals",
        "shadows",
        "geometry",
        "geometries",
        "point clouds",
        "misc",
    }
    if _logical_collection_name(source_col.name) in logical_group_names:
        return parent
    return source_col


def _ensure_collider_collection(context, source_obj):
    from .nh_scatter import (_COLLIDER_COLLECTION_ALIASES, _COLLIDER_COLLECTION_COLOR, _COLLIDER_COLLECTION_NAME)
    parent = _preferred_collider_parent_collection(context, source_obj)
    if parent is None:
        parent = context.scene.collection

    return _ensure_named_child_collection(
        parent,
        _COLLIDER_COLLECTION_NAME,
        _COLLIDER_COLLECTION_COLOR,
        aliases=_COLLIDER_COLLECTION_ALIASES,
    )


def _ensure_named_child_collection(parent_collection, collection_name, color_tag=None, aliases=()):
    if parent_collection is None:
        return None

    target = _find_named_child_collection(parent_collection, collection_name, aliases=aliases)
    if target is None:
        target = bpy.data.collections.new(collection_name)
        parent_collection.children.link(target)
    elif _logical_collection_name(target.name) != _logical_collection_name(collection_name):
        try:
            target.name = collection_name
        except Exception:
            pass

    if color_tag:
        try:
            target.color_tag = color_tag
        except Exception:
            pass

    return target


def _find_named_child_collection(parent_collection, collection_name, aliases=()):
    if parent_collection is None:
        return None

    target = parent_collection.children.get(collection_name)
    logical_names = _logical_collection_names(collection_name, aliases)
    if target is None:
        for child in parent_collection.children:
            if _logical_collection_name(child.name) in logical_names:
                target = child
                break
    return target


def _ensure_memory_collection(context, source_obj):
    from .nh_scatter import (_MEMORY_COLLECTION_ALIASES, _MEMORY_COLLECTION_COLOR, _MEMORY_COLLECTION_NAME)
    parent = _preferred_collider_parent_collection(context, source_obj)
    if parent is None:
        parent = context.scene.collection
    return _ensure_named_child_collection(
        parent,
        _MEMORY_COLLECTION_NAME,
        _MEMORY_COLLECTION_COLOR,
        aliases=_MEMORY_COLLECTION_ALIASES,
    )


def _ensure_misc_collection(context, source_obj):
    from .nh_scatter import (_MISC_COLLECTION_COLOR, _MISC_COLLECTION_NAME)
    parent = _preferred_collider_parent_collection(context, source_obj)
    if parent is None:
        parent = context.scene.collection
    return _ensure_named_child_collection(parent, _MISC_COLLECTION_NAME, _MISC_COLLECTION_COLOR)


def _remove_p3d_named_property(props, name: str):
    items = getattr(props, "properties", None)
    if items is None:
        return

    remove_indices = []
    for idx, item in enumerate(items):
        if (getattr(item, "name", "") or "").strip().lower() == name.lower():
            remove_indices.append(idx)

    for idx in reversed(remove_indices):
        try:
            items.remove(idx)
        except Exception:
            pass


def _set_collider_lod_p3d_props(target_obj, lod_token):
    if not hasattr(target_obj, "a3ob_properties_object"):
        return

    try:
        props = target_obj.a3ob_properties_object
        props.lod = str(lod_token)
        props.resolution = 1
        props.resolution_float = 1.0
        props.is_a3_lod = True
        _remove_p3d_named_property(props, "autocenter")
        lod_name = props.get_name() if hasattr(props, "get_name") else _collider_lod_name(lod_token)
        target_obj.name = lod_name
        if target_obj.data is not None:
            target_obj.data.name = lod_name
    except Exception:
        pass


def _collider_target_validation_error(
    target_obj,
    lod_token,
    source_obj=None,
    allow_same_source=False,
    allow_any_collider_lod=False,
):
    from .nh_scatter import (_COLLIDER_LOD_NAMES)
    if target_obj is None:
        return None
    if target_obj.type != "MESH":
        return "Target LOD Object must be a mesh"
    if source_obj is not None and target_obj == source_obj and not allow_same_source:
        if not _is_collider_lod_mesh_object(target_obj, lod_token=lod_token):
            return "Target LOD Object must be separate from the Source Object"
    if not hasattr(target_obj, "a3ob_properties_object"):
        return None

    try:
        props = target_obj.a3ob_properties_object
        if not bool(getattr(props, "is_a3_lod", False)):
            return None
        current_lod = str(getattr(props, "lod", ""))
    except Exception:
        return None

    if current_lod and current_lod != str(lod_token):
        if allow_any_collider_lod and current_lod in _COLLIDER_LOD_NAMES:
            return None
        return (
            f"Target LOD Object '{target_obj.name}' is already "
            f"P3D LOD '{_collider_lod_name(current_lod)}'"
        )
    return None


def _tag_redraw_all_areas(context):
    screen = getattr(getattr(context, "window", None), "screen", None) or getattr(context, "screen", None)
    if screen is None:
        return

    for area in getattr(screen, "areas", []):
        try:
            for region in area.regions:
                region.tag_redraw()
        except Exception:
            pass
        try:
            area.tag_redraw()
        except Exception:
            pass

