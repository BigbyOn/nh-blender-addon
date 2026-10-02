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
_SP_POINT_NAME_RE = re.compile(r"^\.sp_([A-Za-z0-9]+)_([AaVv])_([01])$")
_SNAP_TARGET_VERTEX_TOLERANCE_DEFAULT = 0.01

_P3D_IMPORT_CANDIDATES = (
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
)

_P3D_EXPORT_CANDIDATES = (
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
            "recalculate_components",
            "renumber_components",
            "translate_selections",
            "force_lowercase",
        ),
    ),
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
    if _op_handle("nh.import_p3d") is None:
        _ensure_p3d_bundle_registered()
    return any(_op_handle(op) is not None for op, _ in _P3D_IMPORT_CANDIDATES)

def _has_any_p3d_export_ops():
    if _op_handle("nh.export_p3d") is None:
        _ensure_p3d_bundle_registered()
    return any(_op_handle(op) is not None for op, _ in _P3D_EXPORT_CANDIDATES)

def _has_any_p3d_io_ops():
    if _op_handle("nh.import_p3d") is None or _op_handle("nh.export_p3d") is None:
        _ensure_p3d_bundle_registered()
    has_import = _has_any_p3d_import_ops()
    has_export = _has_any_p3d_export_ops()
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
    """Register the embedded NH P3D backend as the primary import/export path.

    Registers:
    - P3D object/material/scene property groups (classes renamed NHA3_* to avoid
      RNA identifier collisions if the original add-on is enabled later)
    - nh.import_p3d / nh.export_p3d wrapper operators that use the
      embedded codec, plus a .p3d file handler.
    """
    reg = _P3D_BUNDLE_REGISTRY
    if reg["registered"]:
        if _op_handle("nh.import_p3d") is not None and _op_handle("nh.export_p3d") is not None:
            return True
        _unregister_p3d_bundle()

    prop_obj = _import_bundled_p3d_module("NH_bundle.props.object")
    prop_mat = _import_bundled_p3d_module("NH_bundle.props.material")
    prop_scn = _import_bundled_p3d_module("NH_bundle.props.scene")
    ui_mod = _import_bundled_p3d_module("NH_bundle.ui.import_export_p3d")
    ui_mesh_mod = _import_bundled_p3d_module("NH_bundle.ui.props_object_mesh")
    ui_mat_mod = _import_bundled_p3d_module("NH_bundle.ui.props_material")
    if prop_obj is None or prop_mat is None or prop_scn is None or ui_mod is None:
        return False

    # During a live legacy-ZIP update Blender can reload NH_Blender without
    # calling the previous unregister(). nh_snap's registry is then fresh, but
    # the old NH_bundle RNA classes are still registered. Remove that orphaned
    # runtime before registering the same bundled modules again.
    if not reg["registered"] and (
        _op_handle("nh.import_p3d") is not None
        or _op_handle("nh.export_p3d") is not None
    ):
        cleaned_modules = 0
        for mod in (ui_mod, ui_mat_mod, ui_mesh_mod, prop_scn, prop_mat, prop_obj):
            if mod is None or not callable(getattr(mod, "unregister", None)):
                continue
            try:
                mod.unregister()
                cleaned_modules += 1
            except Exception as e:
                print(
                    f"[NH Plugin] stale bundled P3D cleanup warning for "
                    f"{getattr(mod, '__name__', '<module>')}: {_fmt_exc(e)}"
                )
        if cleaned_modules:
            print(
                f"[NH Plugin] Removed stale bundled P3D runtime from "
                f"{cleaned_modules} module(s) after live ZIP update"
            )

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
    print("[NH Plugin] Internal P3D backend registered (P3D codec + property groups + UI panels)")
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
    if op_candidates is _P3D_IMPORT_CANDIDATES or op_candidates is _P3D_EXPORT_CANDIDATES:
        _ensure_p3d_bundle_registered()
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


_GEOMETRY_LOD_DEFAULT_TOTAL_MASS = 1000.0
_GEOMETRY_HOUSE_NAMED_PROPERTIES = (("class", "house"), ("map", "building"))


def _object_pointer_key(obj):
    if obj is None:
        return None
    try:
        return obj.as_pointer()
    except Exception:
        return id(obj)


def _geometry_lod_display_name(root_obj):
    try:
        return str(root_obj.a3ob_properties_object.get_name())
    except Exception:
        return getattr(root_obj, "name", "<unknown>")


def _geometry_lod_root_objects(export_objects):
    from .nh_assets import (_is_p3d_proxy_object)
    from .nh_scatter import (_GEOMETRY_LOD_TOKEN)
    roots = []
    seen = set()
    for obj in export_objects or []:
        if not _is_p3d_lod_root_object(obj):
            continue
        if _is_p3d_proxy_object(obj):
            continue
        try:
            lod_token = str(getattr(obj.a3ob_properties_object, "lod", "") or "").strip()
        except Exception:
            continue
        if lod_token != _GEOMETRY_LOD_TOKEN:
            continue
        key = _object_pointer_key(obj)
        if key in seen:
            continue
        seen.add(key)
        roots.append(obj)
    return roots


def _read_mesh_mass_values(mesh_obj):
    if mesh_obj is None or getattr(mesh_obj, "type", None) != "MESH" or mesh_obj.data is None:
        return []
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh_obj.data)
        layer = bm.verts.layers.float.get("a3ob_mass")
        if layer is None:
            return [0.0 for _vert in bm.verts]
        return [float(vert[layer]) for vert in bm.verts]
    finally:
        bm.free()


def _write_mesh_mass_uniform(mesh_obj, value_per_vertex: float):
    if mesh_obj is None or getattr(mesh_obj, "type", None) != "MESH" or mesh_obj.data is None:
        return 0
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh_obj.data)
        bm.verts.ensure_lookup_table()
        layer = bm.verts.layers.float.get("a3ob_mass")
        if layer is None:
            layer = bm.verts.layers.float.new("a3ob_mass")
        for vert in bm.verts:
            vert[layer] = value_per_vertex
        changed = len(bm.verts)
        bm.to_mesh(mesh_obj.data)
        mesh_obj.data.update()
        return changed
    finally:
        bm.free()


def _prepare_geometry_lod_mass_for_export(export_objects):
    prepared = []
    for root_obj in _geometry_lod_root_objects(export_objects):
        meshes = list(_iter_p3d_export_meshes_for_lod_root(root_obj))
        if not meshes:
            continue

        values_by_obj = []
        total_verts = 0
        total_mass = 0.0
        has_missing_mass = False
        for mesh_obj in meshes:
            values = _read_mesh_mass_values(mesh_obj)
            if not values:
                continue
            values_by_obj.append((mesh_obj, values))
            total_verts += len(values)
            total_mass += math.fsum(values)
            if any(value <= 1e-7 for value in values):
                has_missing_mass = True

        if total_verts <= 0:
            continue

        record = {
            "root_object": root_obj,
            "total_verts": total_verts,
            "total_mass": total_mass,
            "mass_created": False,
            "mass_repaired": False,
            "unchanged": False,
            "changed_verts": 0,
        }

        if total_mass > 1e-7 and not has_missing_mass:
            record["unchanged"] = True
            prepared.append(record)
            continue

        target_total_mass = total_mass if total_mass > 1e-7 else _GEOMETRY_LOD_DEFAULT_TOTAL_MASS
        value_per_vertex = target_total_mass / total_verts
        changed_verts = 0
        for mesh_obj, _values in values_by_obj:
            changed_verts += _write_mesh_mass_uniform(mesh_obj, value_per_vertex)
        record["total_mass"] = target_total_mass
        record["mass_created"] = total_mass <= 1e-7
        record["mass_repaired"] = total_mass > 1e-7
        record["changed_verts"] = changed_verts
        prepared.append(record)

    return prepared


def _prepare_geometry_house_metadata_for_export(export_objects, original_named_properties=None):
    original_map = {}
    for obj, saved in original_named_properties or []:
        original_map[_object_pointer_key(obj)] = list(saved or [])

    try:
        mass_records = _prepare_geometry_lod_mass_for_export(export_objects)
    except Exception as e:
        from .nh_base import (_fmt_exc)
        print(f"WARNING: Geometry mass preparation failed: {_fmt_exc(e)}")
        mass_records = []
    mass_by_root = {_object_pointer_key(rec["root_object"]): rec for rec in mass_records}

    restore_items = []
    records = []
    for root_obj in _geometry_lod_root_objects(export_objects):
        props = getattr(root_obj, "a3ob_properties_object", None)
        if props is None:
            continue
        try:
            items = props.properties
            saved = [
                (str(getattr(item, "name", "") or ""), str(getattr(item, "value", "") or ""))
                for item in items
            ]
            items.clear()
            for prop_name, prop_value in _GEOMETRY_HOUSE_NAMED_PROPERTIES:
                item = items.add()
                item.name = prop_name
                item.value = prop_value
        except Exception as e:
            from .nh_base import (_fmt_exc)
            print(
                f"WARNING: Geometry house metadata prep failed for "
                f"{getattr(root_obj, 'name', '<unknown>')}: {_fmt_exc(e)}"
            )
            continue

        restore_items.append((root_obj, saved))

        original = dict(original_map.get(_object_pointer_key(root_obj), []))
        kept_originals = (
            original.get("class") == "house"
            and original.get("map") == "building"
        )
        records.append(
            {
                "lod_object_name": getattr(root_obj, "name", "<unknown>"),
                "lod_name": _geometry_lod_display_name(root_obj),
                "named_properties_label": (
                    "kept originals (class=house, map=building)"
                    if kept_originals
                    else "added (class=house, map=building)"
                ),
                "mass": mass_by_root.get(_object_pointer_key(root_obj)),
            }
        )

    return restore_items, records


def _report_geometry_metadata_prepared_in_console(model_name, records):
    if not records:
        return

    print("=== Batch Export Collections: Geometry metadata prepared ===")
    print(f"Model: {model_name}")
    for rec in records:
        mass_rec = rec.get("mass") or {}
        if mass_rec.get("unchanged"):
            mass_label = f"mass kept, total {float(mass_rec.get('total_mass', 0.0) or 0.0):.3f}"
        elif mass_rec.get("mass_created"):
            mass_label = f"mass created, default total {float(mass_rec.get('total_mass', 0.0) or 0.0):.3f}"
        elif mass_rec.get("mass_repaired"):
            mass_label = f"mass repaired, total {float(mass_rec.get('total_mass', 0.0) or 0.0):.3f}"
        else:
            mass_label = "mass not prepared"
        print(
            f" - Geometry LOD: {rec.get('lod_name', '<unknown>')} | "
            f"class/map: {rec.get('named_properties_label', '')} | "
            f"{mass_label} | vertices: {int(mass_rec.get('total_verts', 0) or 0)}"
        )


def _get_p3d_export_p3d_module():
    return _import_first_available_module(
        (
            "NH_bundle.io.export_p3d",
            "bl_ext.user_default.Arma3ObjectBuilder.io.export_p3d",
        )
    )


def _get_p3d_validator_module():
    return _import_first_available_module(
        (
            "NH_bundle.utilities.validator",
            "bl_ext.user_default.Arma3ObjectBuilder.utilities.validator",
        )
    )


def _get_p3d_data_p3d_module():
    return _import_first_available_module(
        (
            "NH_bundle.io.data_p3d",
            "bl_ext.user_default.Arma3ObjectBuilder.io.data_p3d",
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
    operator.recalculate_components = False
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
    ordered_world_points = _sort_snap_pair_world_points(context, world_points, preferred_axis_token=axis_token)
    point_names = [f".sp_{snap_group}_{snap_side}_{point_index}" for point_index in range(2)]
    return _create_named_snap_points_in_memory(
        memory_obj,
        ordered_world_points,
        point_names,
        replace_existing=replace_existing,
    )

def _normalize_snap_p3d_name(value: str) -> str:
    from .nh_base import (_sanitize_snap_p3d_name_value)
    return _sanitize_snap_p3d_name_value(value)

def _build_snap_name_base(p3d_name: str, pair_code: str, axis_token: str, include_axis: bool = False) -> str:
    axis = ((axis_token or "X").strip().lower() or "x") if include_axis else ""
    return f"{p3d_name}{pair_code}{axis}"

def _build_snap_point_name(
    p3d_name: str,
    pair_code: str,
    axis_token: str,
    snap_side: str,
    point_index: int,
    include_axis: bool = False,
) -> str:
    base = _build_snap_name_base(p3d_name, pair_code, axis_token, include_axis=include_axis)
    side = (snap_side or "").strip().upper()
    return f".sp_{base}_{side}_{point_index}"


def _find_vertex_group_case_insensitive(obj, group_name: str):
    wanted = (group_name or "").lower()
    for group in getattr(obj, "vertex_groups", ()):
        if (getattr(group, "name", "") or "").lower() == wanted:
            return group
    return None


def _vertex_group_member_indices(obj, group):
    if obj is None or group is None or getattr(obj, "data", None) is None:
        return []
    group_index = int(group.index)
    return [
        int(vertex.index)
        for vertex in obj.data.vertices
        if any(int(membership.group) == group_index for membership in vertex.groups)
    ]


def _remove_replaced_snap_point_vertices(memory_obj, point_names):
    """Remove old points owned only by the snap groups being replaced."""
    groups = [
        _find_vertex_group_case_insensitive(memory_obj, point_name)
        for point_name in point_names
    ]
    groups = [group for group in groups if group is not None]
    if not groups:
        return

    replaced_group_indices = {int(group.index) for group in groups}
    removable_indices = []
    mesh = memory_obj.data
    for vertex in mesh.vertices:
        memberships = {int(item.group) for item in vertex.groups}
        if memberships and memberships.issubset(replaced_group_indices):
            removable_indices.append(int(vertex.index))

    if removable_indices:
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.verts.ensure_lookup_table()
            removable = [bm.verts[index] for index in removable_indices if 0 <= index < len(bm.verts)]
            if removable:
                bmesh.ops.delete(bm, geom=removable, context="VERTS")
                bm.to_mesh(mesh)
                mesh.update()
        finally:
            bm.free()

    for group in groups:
        try:
            memory_obj.vertex_groups.remove(group)
        except Exception:
            pass

def _create_named_snap_points_in_memory(memory_obj, world_points, point_names, replace_existing: bool):
    if memory_obj is None or memory_obj.type != "MESH":
        raise RuntimeError("Memory LOD Object must be a mesh")
    if len(world_points) != len(point_names):
        raise RuntimeError("Point and name count mismatch")

    existing_groups = [
        _find_vertex_group_case_insensitive(memory_obj, point_name)
        for point_name in point_names
    ]
    if any(group is not None for group in existing_groups):
        if not replace_existing:
            existing_names = [group.name for group in existing_groups if group is not None]
            raise RuntimeError(f"Snap point group already exists: {', '.join(existing_names)}")
        _remove_replaced_snap_point_vertices(memory_obj, point_names)

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
        vg = _find_vertex_group_case_insensitive(memory_obj, point_name)
        if vg is None:
            vg = memory_obj.vertex_groups.new(name=point_name)
        vg.add([base_idx + offset], 1.0, "REPLACE")
        created_names.append(point_name)
    return created_names

class _SnapPointNamePattern:
    def __init__(self, p3d_name: str, pair_code: str, axis_token: str, include_axis: bool = False):
        self.p3d_name = p3d_name
        self.pair_code = pair_code
        self.axis_token = (axis_token or "X").strip().upper() or "X"
        self.include_axis = bool(include_axis)

    @classmethod
    def from_settings(cls, settings):
        p3d_name = _normalize_snap_p3d_name(getattr(settings, "snap_p3d_name", "") or getattr(settings, "snap_group", ""))
        if not p3d_name:
            raise RuntimeError("P3D Name is empty")
        if not _SP_P3D_NAME_RE.fullmatch(p3d_name):
            raise RuntimeError("P3D Name must contain only letters and digits")

        return cls(
            p3d_name=p3d_name,
            pair_code="01",
            axis_token=getattr(settings, "edge_axis", "X"),
            include_axis=bool(getattr(settings, "snap_include_axis", False)),
        )

    @classmethod
    def from_preview_settings(cls, settings):
        p3d_name = _normalize_snap_p3d_name(getattr(settings, "snap_p3d_name", "") or getattr(settings, "snap_group", "")) or "SampleName"
        if not _SP_P3D_NAME_RE.fullmatch(p3d_name):
            p3d_name = "SampleName"

        return cls(
            p3d_name=p3d_name,
            pair_code="01",
            axis_token=getattr(settings, "edge_axis", "X"),
            include_axis=bool(getattr(settings, "snap_include_axis", False)),
        )

    @property
    def preview_base(self) -> str:
        return _build_snap_name_base(
            self.p3d_name,
            self.pair_code,
            self.axis_token,
            include_axis=self.include_axis,
        )

    def with_pair_code(self, pair_code: str):
        return _SnapPointNamePattern(
            self.p3d_name,
            pair_code,
            self.axis_token,
            include_axis=self.include_axis,
        )

    def build_pair_names(self, snap_side: str):
        return [
            _build_snap_point_name(
                self.p3d_name,
                self.pair_code,
                self.axis_token,
                snap_side,
                point_index,
                include_axis=self.include_axis,
            )
            for point_index in range(2)
        ]


def _snap_pair_id_is_occupied(naming, side_memory_pairs) -> bool:
    checked_memory = set()
    for _side_token, memory_obj in side_memory_pairs:
        if memory_obj is None:
            continue
        try:
            memory_key = memory_obj.as_pointer()
        except Exception:
            memory_key = id(memory_obj)
        if memory_key in checked_memory:
            continue
        checked_memory.add(memory_key)

        # A middle model first owns V01, then becomes the A target for the next
        # connection. Either side therefore occupies the component ID.
        for existing_side in ("a", "v"):
            for point_name in naming.build_pair_names(existing_side):
                if _find_vertex_group_case_insensitive(memory_obj, point_name) is not None:
                    return True
    return False


def _next_available_snap_naming(naming, side_memory_pairs):
    pair_code = str(naming.pair_code or "")
    if not pair_code.isdigit():
        return naming

    width = len(pair_code)
    candidate = int(pair_code)
    maximum = 999
    while candidate <= maximum:
        candidate_naming = naming.with_pair_code(str(candidate).zfill(width))
        if not _snap_pair_id_is_occupied(candidate_naming, side_memory_pairs):
            return candidate_naming
        candidate += 1
    raise RuntimeError(f"No free {width}-digit snap ID remains after {pair_code}")


def _snap_scene_memory_pairs(context):
    """Return every unique Memory LOD in the scene for global ID allocation."""
    from .nh_textures import (_collect_collection_objects_recursive)

    pairs = []
    seen = set()
    scene = getattr(context, "scene", None)
    for root in _iter_p3d_root_collections(scene):
        for obj in _collect_collection_objects_recursive(root):
            if not _is_memory_lod_mesh_object(obj):
                continue
            try:
                key = obj.as_pointer()
            except Exception:
                key = id(obj)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(("scene", obj))

    # Also include valid Memory objects outside a conventional .p3d root. This
    # keeps ID allocation global while a model is still being prepared or its
    # collection hierarchy has not been normalized yet.
    for obj in getattr(scene, "objects", ()) if scene is not None else ():
        if not _is_memory_lod_mesh_object(obj):
            continue
        try:
            key = obj.as_pointer()
        except Exception:
            key = id(obj)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(("scene", obj))
    return pairs


def _snap_named_point_world(memory_obj, point_name: str):
    group = _find_vertex_group_case_insensitive(memory_obj, point_name)
    if group is None:
        raise RuntimeError(f"Created snap group is missing: {point_name}")
    member_indices = _vertex_group_member_indices(memory_obj, group)
    if len(member_indices) != 1:
        raise RuntimeError(f"Snap group must contain exactly one point: {point_name}")
    return memory_obj.matrix_world @ memory_obj.data.vertices[member_indices[0]].co


def _snap_pair_distance_tolerance(*distances) -> float:
    """P3D stores float coordinates, so allow sub-millimetre round-trip noise."""
    scale = max((abs(float(value)) for value in distances), default=1.0)
    return max(5e-5, max(1.0, scale) * 1e-6)


def _validate_created_snap_pair(memory_a, memory_v, naming):
    names_a = naming.build_pair_names("a")
    names_v = naming.build_pair_names("v")
    points_a = [_snap_named_point_world(memory_a, point_name) for point_name in names_a]
    points_v = [_snap_named_point_world(memory_v, point_name) for point_name in names_v]

    span_a = (points_a[1] - points_a[0]).length
    span_v = (points_v[1] - points_v[0]).length
    tolerance = _snap_pair_distance_tolerance(span_a, span_v)
    if span_a <= tolerance:
        raise RuntimeError("Snap points 0 and 1 must not occupy the same position")
    if abs(span_a - span_v) > tolerance:
        raise RuntimeError("A and V snap point distances do not match")
    if (points_a[0] - points_v[0]).length > tolerance:
        raise RuntimeError("A_0 and V_0 were not created at the same world position")
    if (points_a[1] - points_v[1]).length > tolerance:
        raise RuntimeError("A_1 and V_1 were not created at the same world position")
    return span_a


def _snap_check_target_scope(context, target_obj, side_label: str):
    from .nh_textures import (_collect_collection_objects_recursive, _find_p3d_root_collection_for_object)
    errors = []
    if target_obj is None or getattr(target_obj, "type", None) != "MESH":
        return None, [], [f"{side_label}: select a visual mesh target"]

    root_collection = _find_p3d_root_collection_for_object(context, target_obj)
    if root_collection is None:
        return None, [], [f"{side_label}: target '{target_obj.name}' is not inside a .p3d root collection"]

    memory_objects = [
        obj for obj in _collect_collection_objects_recursive(root_collection)
        if _is_memory_lod_mesh_object(obj)
    ]
    memory_objects.sort(key=lambda obj: (getattr(obj, "name", "").lower(), obj.as_pointer()))
    if not memory_objects:
        errors.append(f"{side_label}: Memory LOD is missing in '{root_collection.name}'")
    elif len(memory_objects) > 1:
        names = ", ".join(obj.name for obj in memory_objects)
        errors.append(f"{side_label}: duplicate Memory LOD objects found: {names}")
    return root_collection, memory_objects, errors


def _snap_check_audit_memory_objects(side_label: str, memory_objects):
    records = {}
    errors = []
    warnings = []
    vertex_usage = {}
    valid_records = []

    for memory_obj in memory_objects:
        snap_group_count = 0
        for group in getattr(memory_obj, "vertex_groups", ()):
            raw_name = (getattr(group, "name", "") or "").strip()
            if not raw_name.lower().startswith(".sp_"):
                continue
            snap_group_count += 1
            match = _SP_POINT_NAME_RE.fullmatch(raw_name)
            if match is None:
                errors.append(
                    f"{side_label}/{memory_obj.name}: invalid snap selection name '{raw_name}' "
                    "(expected .sp_<letters-and-digits>_<A-or-V>_<0-or-1>)"
                )
                continue

            base_name, snap_side, point_index = match.groups()
            logical_key = (base_name.lower(), snap_side.lower(), int(point_index))
            member_indices = _vertex_group_member_indices(memory_obj, group)
            record = {
                "memory": memory_obj,
                "group": group,
                "name": raw_name,
                "base": base_name,
                "side": snap_side.lower(),
                "index": int(point_index),
                "members": member_indices,
                "point": None,
            }
            records.setdefault(logical_key, []).append(record)

            if len(member_indices) != 1:
                errors.append(
                    f"{side_label}/{memory_obj.name}: '{raw_name}' contains {len(member_indices)} vertices; expected exactly 1"
                )
                continue

            vertex_key = (memory_obj.as_pointer(), member_indices[0])
            vertex_usage.setdefault(vertex_key, []).append(raw_name)
            record["point"] = memory_obj.matrix_world @ memory_obj.data.vertices[member_indices[0]].co
            valid_records.append(record)

        if snap_group_count == 0:
            errors.append(f"{side_label}/{memory_obj.name}: no .sp_ snap selections found")

    for logical_key, entries in records.items():
        if len(entries) <= 1:
            continue
        names = ", ".join(f"{entry['memory'].name}:{entry['name']}" for entry in entries)
        base_name, snap_side, point_index = logical_key
        errors.append(
            f"{side_label}: duplicate logical snap point '{base_name}_{snap_side}_{point_index}': {names}"
        )

    for used_names in vertex_usage.values():
        if len(used_names) > 1:
            warnings.append(
                f"{side_label}: one Memory vertex is used by multiple snap selections: {', '.join(used_names)}"
            )

    duplicate_position_pairs = set()
    for left_index, left in enumerate(valid_records):
        for right in valid_records[left_index + 1:]:
            if left["memory"] != right["memory"] or left["name"].lower() == right["name"].lower():
                continue
            if (left["point"] - right["point"]).length > 1e-6:
                continue
            pair_key = tuple(sorted((left["name"].lower(), right["name"].lower())))
            if pair_key in duplicate_position_pairs:
                continue
            duplicate_position_pairs.add(pair_key)
            warnings.append(
                f"{side_label}/{left['memory'].name}: snap selections '{left['name']}' and "
                f"'{right['name']}' occupy the same position"
            )

    return records, errors, warnings


def _snap_check_pair_from_records(records, base_name: str, snap_side: str):
    pair_entries = []
    for point_index in (0, 1):
        entries = records.get((base_name.lower(), snap_side.lower(), point_index), ())
        if len(entries) != 1 or entries[0]["point"] is None:
            return None, None
        pair_entries.append(entries[0])
    if pair_entries[0]["memory"] != pair_entries[1]["memory"]:
        return None, None
    return [entry["point"].copy() for entry in pair_entries], pair_entries[0]["memory"]


def _snap_check_complete_pair_bases(records, snap_side: str):
    side = (snap_side or "").lower()
    bases = {base_name for base_name, record_side, _point_index in records if record_side == side}
    complete = set()
    for base_name in bases:
        points, _memory = _snap_check_pair_from_records(records, base_name, side)
        if points is not None:
            complete.add(base_name)
    return complete


def _snap_check_naming_from_existing_base(settings, base_name: str):
    p3d_name = _normalize_snap_p3d_name(
        getattr(settings, "snap_p3d_name", "") or getattr(settings, "snap_group", "")
    )
    if not p3d_name or not _SP_P3D_NAME_RE.fullmatch(p3d_name):
        raise RuntimeError("P3D Name must contain only letters and digits")

    base = str(base_name or "")
    prefix = p3d_name.lower()
    if not base.lower().startswith(prefix):
        return None

    suffix = base[len(p3d_name):]
    include_axis = False
    axis_token = getattr(settings, "edge_axis", "X")
    pair_code = suffix
    if len(suffix) >= 2 and suffix[-1:].lower() in {"x", "y", "z"}:
        possible_pair_code = suffix[:-1]
        if _SP_PAIR_CODE_RE.fullmatch(possible_pair_code):
            include_axis = True
            axis_token = suffix[-1].upper()
            pair_code = possible_pair_code

    if not _SP_PAIR_CODE_RE.fullmatch(pair_code or ""):
        return None
    return _SnapPointNamePattern(
        p3d_name=p3d_name,
        pair_code=pair_code,
        axis_token=axis_token,
        include_axis=include_axis,
    )


def _snap_check_select_automatic_naming(settings, records_a, records_v):
    common_bases = (
        _snap_check_complete_pair_bases(records_a, "a")
        & _snap_check_complete_pair_bases(records_v, "v")
    )
    candidates = []
    for base_name in common_bases:
        naming = _snap_check_naming_from_existing_base(settings, base_name)
        if naming is None:
            continue
        pair_code = str(naming.pair_code or "")
        sort_key = (
            0 if pair_code.isdigit() else 1,
            int(pair_code) if pair_code.isdigit() else 0,
            pair_code.lower(),
            base_name.lower(),
        )
        candidates.append((sort_key, naming))

    if not candidates:
        p3d_name = _normalize_snap_p3d_name(
            getattr(settings, "snap_p3d_name", "") or getattr(settings, "snap_group", "")
        )
        raise RuntimeError(
            f"No complete matching A/V snap pair found automatically for P3D Name '{p3d_name}'"
        )

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1], len(candidates)


def _snap_check_append_cross_pair_errors(records_a, records_v, errors):
    bases_a = {base_name for base_name, snap_side, _index in records_a if snap_side == "a"}
    bases_v = {base_name for base_name, snap_side, _index in records_v if snap_side == "v"}
    for base_name in sorted(bases_a & bases_v):
        points_a, _memory_a = _snap_check_pair_from_records(records_a, base_name, "a")
        points_v, _memory_v = _snap_check_pair_from_records(records_v, base_name, "v")
        if points_a is None or points_v is None:
            continue
        span_a = (points_a[1] - points_a[0]).length
        span_v = (points_v[1] - points_v[0]).length
        tolerance = _snap_pair_distance_tolerance(span_a, span_v)
        if abs(span_a - span_v) > tolerance:
            errors.append(
                f"A/V pair '{base_name}' has different _0/_1 distances: A={span_a:.6f}, V={span_v:.6f}"
            )


def _snap_check_alignment_transform(points_a, points_v):
    vector_a = points_a[1] - points_a[0]
    vector_v = points_v[1] - points_v[0]
    span_a = vector_a.length
    span_v = vector_v.length
    tolerance = _snap_pair_distance_tolerance(span_a, span_v)
    if span_a <= tolerance or span_v <= tolerance:
        raise RuntimeError("Selected snap pair has coincident _0/_1 points")
    if abs(span_a - span_v) > tolerance:
        raise RuntimeError(f"Selected A/V distances differ: A={span_a:.6f}, V={span_v:.6f}")

    rotation = vector_v.normalized().rotation_difference(vector_a.normalized()).to_matrix().to_4x4()
    transform = Matrix.Translation(points_a[0]) @ rotation @ Matrix.Translation(-points_v[0])
    residual_0 = (transform @ points_v[0] - points_a[0]).length
    residual_1 = (transform @ points_v[1] - points_a[1]).length
    if max(residual_0, residual_1) > tolerance:
        raise RuntimeError(
            f"Could not align selected pair precisely; endpoint error={max(residual_0, residual_1):.6f}"
        )
    return transform, max(residual_0, residual_1)


def _snap_check_visual_lod_roots(root_collection):
    from .nh_textures import (_collect_collection_objects_recursive)
    candidates = [
        obj for obj in _collect_collection_objects_recursive(root_collection)
        if _is_p3d_resolution_lod_object(obj)
    ]
    candidate_ptrs = {obj.as_pointer() for obj in candidates}
    roots = []
    for obj in candidates:
        parent = getattr(obj, "parent", None)
        nested = False
        while parent is not None:
            if parent.as_pointer() in candidate_ptrs:
                nested = True
                break
            parent = getattr(parent, "parent", None)
        if not nested:
            roots.append(obj)
    roots.sort(key=lambda obj: getattr(obj, "name", "").lower())
    return roots


def _snap_check_has_plain_axis(root_collection):
    from .nh_textures import (_collect_collection_objects_recursive, _is_plain_axis_helper)
    return any(_is_plain_axis_helper(obj) for obj in _collect_collection_objects_recursive(root_collection))


def _snap_check_apply_visual_assembly(context, target_a, target_v, root_a, root_v, memory_a, memory_v, snap_transform):
    from .nh_textures import (_set_object_world_matrix_stable)
    visual_a = _snap_check_visual_lod_roots(root_a)
    visual_v = _snap_check_visual_lod_roots(root_v)
    if target_a not in visual_a:
        raise RuntimeError("A Target must be a top-level visual Resolution LOD")
    if target_v not in visual_v:
        raise RuntimeError("V Target must be a top-level visual Resolution LOD")
    if not visual_a or not visual_v:
        raise RuntimeError("Both model roots must contain visual Resolution LODs")

    desired_a_matrix = memory_a.matrix_world.copy()
    desired_v_matrix = snap_transform @ memory_v.matrix_world.copy()
    delta_a = desired_a_matrix @ target_a.matrix_world.inverted_safe()
    delta_v = desired_v_matrix @ target_v.matrix_world.inverted_safe()

    for obj in visual_a:
        _set_object_world_matrix_stable(obj, delta_a @ obj.matrix_world)
    for obj in visual_v:
        _set_object_world_matrix_stable(obj, delta_v @ obj.matrix_world)
    context.view_layer.update()
    return len(visual_a), len(visual_v)


def _print_snap_check_report(target_a, target_v, naming, root_a, root_v, memory_a, memory_v, errors, warnings, assembly=None):
    print("=== NH Snap Magnet Check ===")
    print(f"Pattern: .sp_{naming.preview_base}_a_0/1 <-> .sp_{naming.preview_base}_v_0/1")
    print(f"A: {getattr(target_a, 'name', '<none>')} | root: {getattr(root_a, 'name', '<none>')} | Memory: {getattr(memory_a, 'name', '<none>')}")
    print(f"V: {getattr(target_v, 'name', '<none>')} | root: {getattr(root_v, 'name', '<none>')} | Memory: {getattr(memory_v, 'name', '<none>')}")
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for issue in errors:
            print(f" - {issue}")
    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for issue in warnings:
            print(f" - {issue}")
    if assembly is not None:
        count_a, count_v, endpoint_error = assembly
        print(
            f"ASSEMBLED: visual LODs only (A={count_a}, V={count_v}); "
            f"Memory/Geometry unchanged; endpoint error={endpoint_error:.8f}"
        )
    elif not errors and not warnings:
        print("OK: no snap point issues found")
    print("=== End NH Snap Magnet Check ===")


def _snap_check_scene_inventory(context):
    """Audit every P3D root without changing transforms or viewport state."""
    from .nh_textures import (_collect_collection_objects_recursive)

    roots = sorted(
        _iter_p3d_root_collections(getattr(context, "scene", None)),
        key=lambda col: ((getattr(col, "name", "") or "").lower(), col.as_pointer()),
    )
    errors = []
    warnings = []
    inventory = []

    if not roots:
        return [], ["No .p3d root collections found in the scene"], []

    for root in roots:
        objects = _collect_collection_objects_recursive(root)
        has_snap_selections = any(
            (getattr(group, "name", "") or "").strip().lower().startswith(".sp_")
            for obj in objects
            if getattr(obj, "type", None) == "MESH"
            for group in getattr(obj, "vertex_groups", ())
        )
        # A scene may contain unrelated P3D models. Only roots which actually
        # declare at least one snap selection participate in this operation.
        if not has_snap_selections:
            continue

        memory_objects = sorted(
            (obj for obj in objects if _is_memory_lod_mesh_object(obj)),
            key=lambda obj: ((getattr(obj, "name", "") or "").lower(), obj.as_pointer()),
        )
        root_label = getattr(root, "name", "<unnamed root>")
        if not memory_objects:
            errors.append(f"{root_label}: Memory LOD is missing")
            records = {}
            memory_obj = None
        elif len(memory_objects) > 1:
            names = ", ".join(obj.name for obj in memory_objects)
            errors.append(f"{root_label}: duplicate Memory LOD objects found: {names}")
            records = {}
            memory_obj = None
        else:
            memory_obj = memory_objects[0]
            records, audit_errors, audit_warnings = _snap_check_audit_memory_objects(
                root_label,
                memory_objects,
            )
            errors.extend(audit_errors)
            # Coincident world positions and shared Memory vertices between
            # different snap selections are not errors: on corners, different
            # exact snap IDs may physically overlap. They stay warnings only.
            warnings.extend(audit_warnings)

        visuals = _snap_check_visual_lod_roots(root)
        resolution_zero = [obj for obj in visuals if _is_resolution0_visual_lod_object(obj)]
        resolution_zero.sort(key=lambda obj: ((getattr(obj, "name", "") or "").lower(), obj.as_pointer()))
        if records and not visuals:
            errors.append(f"{root_label}: no visual Resolution LOD objects found")
        if records and not resolution_zero:
            errors.append(f"{root_label}: Resolution 0 is missing; cannot locate snap points on the visual model")
        elif len(resolution_zero) > 1:
            names = ", ".join(obj.name for obj in resolution_zero)
            errors.append(f"{root_label}: duplicate Resolution 0 objects found: {names}")

        if records and _snap_check_has_plain_axis(root):
            errors.append(f"{root_label}: Plain Axis still exists; delete it before magnet assembly")

        inventory.append({
            "root": root,
            "memory": memory_obj,
            "records": records,
            "visuals": visuals,
            "reference": resolution_zero[0] if len(resolution_zero) == 1 else None,
        })

    return inventory, errors, warnings


def _snap_check_exact_id_side_endpoint(state, side):
    """Return (endpoint, fail_reason, duplicate_roots) for one exact ID side."""
    points_by_index = state["points"].get(side, {})
    if not points_by_index:
        return None, None, []

    roots_by_index = {}
    for index in (0, 1):
        entries = points_by_index.get(index, [])
        if not entries:
            return None, f"missing _{index}", []
        by_root = {}
        for info, record in entries:
            root = info.get("root")
            key = root.as_pointer() if root is not None else id(root)
            by_root.setdefault(key, (info, record))
        if len(by_root) > 1:
            names = sorted(
                getattr(item[0].get("root"), "name", "<unknown>")
                for item in by_root.values()
            )
            return None, None, names
        info, record = next(iter(by_root.values()))
        if record.get("point") is None:
            return None, f"_{index} selection does not contain exactly one vertex", []
        roots_by_index[index] = (info, record)

    info_0, record_0 = roots_by_index[0]
    info_1, record_1 = roots_by_index[1]
    if info_0.get("root") != info_1.get("root"):
        root_0 = getattr(info_0.get("root"), "name", "<unknown>")
        root_1 = getattr(info_1.get("root"), "name", "<unknown>")
        return None, f"_0 and _1 points are in different P3D roots: '{root_0}' vs '{root_1}'", []

    endpoint = {
        "info": info_0,
        "points": [record_0["point"].copy(), record_1["point"].copy()],
        "memory": record_0.get("memory"),
    }
    return endpoint, None, []


def _snap_check_evaluate_exact_id(display_base, state):
    """Validate one exact snap ID locally and return its per-ID result record."""
    result = {
        "base": display_base,
        "status": "PASS",
        "reasons": [],
        "a_root": "",
        "v_root": "",
        "span_a": None,
        "span_v": None,
        "delta": None,
        "edge": None,
    }
    endpoints = {}
    for side in ("a", "v"):
        endpoint, fail_reason, duplicate_roots = _snap_check_exact_id_side_endpoint(state, side)
        endpoints[side] = endpoint
        if duplicate_roots:
            result["reasons"].append(
                f"Duplicate logical snap pair '.sp_{display_base}_{side.upper()}_0/1' "
                f"exists in multiple roots: {', '.join(duplicate_roots)}"
            )
        elif fail_reason:
            result["reasons"].append(f"{side.upper()} pair is incomplete: {fail_reason}")

    if result["reasons"]:
        result["status"] = "FAIL"
        return result

    endpoint_a = endpoints["a"]
    endpoint_v = endpoints["v"]
    if endpoint_a is None and endpoint_v is None:
        result["status"] = "FAIL"
        result["reasons"].append("no A_0/A_1 or V_0/V_1 snap points found")
        return result

    if endpoint_a is None or endpoint_v is None:
        result["status"] = "WARN"
        if endpoint_a is not None:
            result["a_root"] = getattr(endpoint_a["info"].get("root"), "name", "")
            result["span_a"] = (endpoint_a["points"][1] - endpoint_a["points"][0]).length
            result["reasons"].append(
                "unmatched snap connector: A_0/A_1 is present, V_0/V_1 is not present in this scene"
            )
        else:
            result["v_root"] = getattr(endpoint_v["info"].get("root"), "name", "")
            result["span_v"] = (endpoint_v["points"][1] - endpoint_v["points"][0]).length
            result["reasons"].append(
                "unmatched snap connector: V_0/V_1 is present, A_0/A_1 is not present in this scene"
            )
        return result

    root_a = endpoint_a["info"].get("root")
    root_v = endpoint_v["info"].get("root")
    result["a_root"] = getattr(root_a, "name", "")
    result["v_root"] = getattr(root_v, "name", "")
    span_a = (endpoint_a["points"][1] - endpoint_a["points"][0]).length
    span_v = (endpoint_v["points"][1] - endpoint_v["points"][0]).length
    result["span_a"] = span_a
    result["span_v"] = span_v
    result["delta"] = abs(span_a - span_v)
    tolerance = _snap_pair_distance_tolerance(span_a, span_v)

    if root_a == root_v:
        result["status"] = "FAIL"
        result["reasons"].append(
            f"A_0/A_1 and V_0/V_1 are in the same P3D root '{result['a_root']}'"
        )
        return result
    if span_a <= tolerance:
        result["status"] = "FAIL"
        result["reasons"].append(
            f"A pair has coincident _0/_1 points (length {span_a:.6f}); pair length must be non-zero"
        )
        return result
    if span_v <= tolerance:
        result["status"] = "FAIL"
        result["reasons"].append(
            f"V pair has coincident _0/_1 points (length {span_v:.6f}); pair length must be non-zero"
        )
        return result
    if abs(span_a - span_v) > tolerance:
        result["status"] = "FAIL"
        result["reasons"].append(
            f"A/V distances differ: A={span_a:.6f}, V={span_v:.6f}, delta={result['delta']:.6f}"
        )
        return result

    result["edge"] = {
        "base": display_base,
        "base_key": state.get("key") or display_base.lower(),
        "span": span_a,
        "a": endpoint_a,
        "v": endpoint_v,
    }
    return result


def _snap_check_build_local_pairs(inventory, errors):
    """Validate every exact snap ID locally and return A-root -> V-root pair edges."""
    id_entries = {}
    for info in inventory:
        records = info.get("records") or {}
        for (base, side, index), entries in records.items():
            key = str(base or "").lower()
            state = id_entries.setdefault(key, {"key": key, "display": None, "points": {}})
            for record in entries:
                if state["display"] is None:
                    state["display"] = str(record.get("base") or base)
                state["points"].setdefault(str(side or "").lower(), {}).setdefault(
                    int(index), []
                ).append((info, record))

    results = []
    edges = []
    failed_ids = 0
    for key in sorted(id_entries):
        state = id_entries[key]
        result = _snap_check_evaluate_exact_id(state["display"] or key, state)
        results.append(result)
        if result["status"] == "PASS" and result.get("edge") is not None:
            edges.append(result["edge"])
        elif result["status"] == "FAIL":
            failed_ids += 1

    if failed_ids:
        errors.append(
            f"Exact snap ID validation failed for {failed_ids} ID(s); see per-ID log"
        )
    if not edges:
        errors.append("No complete matching A/V magnet IDs found")

    edges.sort(key=lambda edge: (edge.get("base", "") or "").lower())
    return results, edges
def _snap_check_virtual_pair(endpoint, planned_deltas):
    """Map fixed Memory points into the visual root's current/planned space."""
    info = endpoint["info"]
    root_ptr = info["root"].as_pointer()
    planned_delta = planned_deltas.get(root_ptr, Matrix.Identity(4))
    reference = info["reference"]
    memory = endpoint["memory"]
    memory_to_visual = planned_delta @ reference.matrix_world @ memory.matrix_world.inverted_safe()
    return [memory_to_visual @ point for point in endpoint["points"]]


def _snap_check_matrix_is_identity(matrix, tolerance=5e-5):
    for row in range(4):
        for col in range(4):
            expected = 1.0 if row == col else 0.0
            if abs(float(matrix[row][col]) - expected) > tolerance:
                return False
    return True


def _snap_check_matrix_applied(actual, expected, tolerance=1e-4):
    for row in range(4):
        for col in range(4):
            if abs(float(actual[row][col]) - float(expected[row][col])) > tolerance:
                return False
    return True


def _snap_check_plan_automatic_assembly(edges):
    """Solve the whole rigid assembly before changing any Blender object.

    Every validated exact ID is an undirected rigid constraint between its A and
    V roots. Each weakly connected constraint component is solved exactly once
    from a single fixed anchor root, so every root receives exactly one solved
    rigid transform. A root referenced by several IDs is therefore never moved
    twice by a later constraint, which used to silently break an ID that had
    already been aligned.
    """
    edge_solutions = []
    endpoint_errors = []
    for edge in edges:
        points_a = _snap_check_virtual_pair(edge["a"], {})
        points_v = _snap_check_virtual_pair(edge["v"], {})
        snap_transform, endpoint_error = _snap_check_alignment_transform(points_a, points_v)
        endpoint_errors.append(endpoint_error)
        edge_solutions.append((edge, snap_transform))

    adjacency = {}
    target_ptrs = set()
    seen_roots = {}
    for index, (edge, _snap_transform) in enumerate(edge_solutions):
        a_root = edge["a"]["info"]["root"]
        v_root = edge["v"]["info"]["root"]
        a_ptr = a_root.as_pointer()
        v_ptr = v_root.as_pointer()
        target_ptrs.add(v_ptr)
        seen_roots.setdefault(a_ptr, a_root)
        seen_roots.setdefault(v_ptr, v_root)
        adjacency.setdefault(a_ptr, []).append((v_ptr, index, False))
        adjacency.setdefault(v_ptr, []).append((a_ptr, index, True))

    def root_sort_key(ptr):
        root = seen_roots.get(ptr)
        return ((getattr(root, "name", "") or "").lower(), ptr)

    planned_deltas = {}
    ordered_edges = []

    def expand_from(seed_ptr):
        queue = [seed_ptr]
        while queue:
            current_ptr = queue.pop(0)
            current_delta = planned_deltas[current_ptr]
            for other_ptr, edge_index, backward in adjacency.get(current_ptr, ()):
                if other_ptr in planned_deltas:
                    continue
                edge, snap_transform = edge_solutions[edge_index]
                if backward:
                    child_delta = current_delta @ snap_transform.inverted_safe()
                else:
                    child_delta = current_delta @ snap_transform
                planned_deltas[other_ptr] = child_delta
                ordered_edges.append(edge)
                queue.append(other_ptr)

    # A connected constraint component can only keep ONE root fixed: pinning
    # two roots of the same component over-constrains the loop and silently
    # breaks an already aligned exact ID. Prefer a root which is never a V
    # target (its own connectors point outwards), otherwise pin the first root
    # of the component so every participating root still gets a solved delta.
    components = []
    unvisited = set(seen_roots)
    while unvisited:
        start_ptr = min(unvisited, key=root_sort_key)
        component = []
        stack = [start_ptr]
        unvisited.discard(start_ptr)
        while stack:
            current_ptr = stack.pop()
            component.append(current_ptr)
            for other_ptr, _edge_index, _backward in adjacency.get(current_ptr, ()):
                if other_ptr in unvisited:
                    unvisited.discard(other_ptr)
                    stack.append(other_ptr)
        components.append(component)

    anchor_ptrs = []
    for component in sorted(components, key=lambda item: root_sort_key(min(item, key=root_sort_key))):
        anchors = sorted((ptr for ptr in component if ptr not in target_ptrs), key=root_sort_key)
        anchor_ptr = anchors[0] if anchors else min(component, key=root_sort_key)
        planned_deltas[anchor_ptr] = Matrix.Identity(4)
        anchor_ptrs.append(anchor_ptr)
        expand_from(anchor_ptr)

    verification_errors = []
    for edge, _snap_transform in edge_solutions:
        points_a = _snap_check_virtual_pair(edge["a"], planned_deltas)
        points_v = _snap_check_virtual_pair(edge["v"], planned_deltas)
        residual = max(
            (points_v[0] - points_a[0]).length,
            (points_v[1] - points_a[1]).length,
        )
        endpoint_errors.append(residual)
        tolerance = _snap_pair_distance_tolerance(
            (points_a[1] - points_a[0]).length,
            (points_v[1] - points_v[0]).length,
        )
        if residual > tolerance:
            verification_errors.append(
                f"Solved assembly cannot satisfy '.sp_{edge['base']}' "
                f"(A={getattr(edge['a']['info']['root'], 'name', '<root>')}, "
                f"V={getattr(edge['v']['info']['root'], 'name', '<root>')}): "
                f"residual={residual:.6f} > tolerance={tolerance:.6f}"
            )
    if verification_errors:
        raise RuntimeError("; ".join(verification_errors))

    return planned_deltas, ordered_edges, max(endpoint_errors, default=0.0), anchor_ptrs


def _assembly_visual_transform_targets(info):
    """Resolve every visual object which needs an explicit solved transform.

    Resolution LOD roots carry their whole hierarchy through the parent link, so
    only top-level LOD objects are returned. Proxy preview objects which are not
    parented anywhere inside a P3D LOD subtree are added explicitly: they
    receive the same solved rigid transform exactly once, and their own children
    keep inheriting it from them. Proxies parented to a technical LOD (Memory,
    Geometry, View Geometry, Fire Geometry, Roadway) stay with that LOD and are
    never moved by the visual solve.
    """
    from .nh_textures import (_collect_collection_objects_recursive)
    from .nh_assets import (_is_p3d_proxy_object)

    targets = list(info.get("visuals") or ())
    target_ptrs = {obj.as_pointer() for obj in targets}
    inherited_ptrs = set()
    for obj in targets:
        for descendant in _iter_object_tree(obj):
            inherited_ptrs.add(descendant.as_pointer())

    proxy_candidates = []
    lod_root_ptrs = set()
    root_collection = info.get("root")
    if root_collection is not None:
        for obj in _collect_collection_objects_recursive(root_collection):
            ptr = obj.as_pointer()
            if _assembly_lod_root_info(obj) is not None:
                lod_root_ptrs.add(ptr)
            if ptr in target_ptrs or ptr in inherited_ptrs:
                continue
            if _is_p3d_proxy_object(obj):
                proxy_candidates.append(obj)
    candidate_ptrs = {obj.as_pointer() for obj in proxy_candidates}
    for obj in proxy_candidates:
        parent = getattr(obj, "parent", None)
        nested = False
        while parent is not None:
            parent_ptr = parent.as_pointer()
            if parent_ptr in lod_root_ptrs or parent_ptr in candidate_ptrs:
                nested = True
                break
            parent = getattr(parent, "parent", None)
        if nested:
            continue
        targets.append(obj)
        for descendant in _iter_object_tree(obj):
            inherited_ptrs.add(descendant.as_pointer())
    return targets


def _snap_check_apply_automatic_assembly(context, inventory, planned_deltas):
    from .nh_textures import (_set_object_world_matrix_stable)
    from .nh_assets import (_is_p3d_proxy_object)

    by_pointer = {info["root"].as_pointer(): info for info in inventory}
    records = []
    for root_ptr, delta in planned_deltas.items():
        info = by_pointer.get(root_ptr)
        if info is None:
            continue
        visual_ptrs = {obj.as_pointer() for obj in (info.get("visuals") or ())}
        targets = []
        for obj in _assembly_visual_transform_targets(info):
            members = []
            for member in _iter_object_tree(obj):
                members.append((
                    member,
                    member.matrix_world.copy(),
                    _is_p3d_proxy_object(member),
                ))
            targets.append({
                "object": obj,
                "is_proxy": obj.as_pointer() not in visual_ptrs,
                "members": members,
            })
        records.append({
            "root": info["root"],
            "delta": delta.copy(),
            "targets": targets,
        })

    changes = []
    try:
        for record in records:
            delta = record["delta"]
            if _snap_check_matrix_is_identity(delta):
                continue
            for target in record["targets"]:
                for obj, original, _is_proxy in target["members"]:
                    if obj is not target["object"]:
                        continue
                    if not _set_object_world_matrix_stable(obj, delta @ original):
                        raise RuntimeError(f"Could not move visual LOD '{obj.name}'")
                    changes.append((obj, original))
        context.view_layer.update()

        audit_records = []
        audit_errors = []
        moved_roots = 0
        moved_lods = 0
        for record in records:
            delta = record["delta"]
            delta_is_identity = _snap_check_matrix_is_identity(delta)
            if not delta_is_identity:
                moved_roots += 1
            visual_lines = []
            proxy_count = 0
            object_count = 0
            for target in record["targets"]:
                target_applied = True
                for obj, original, is_proxy in target["members"]:
                    object_count += 1
                    if is_proxy:
                        proxy_count += 1
                    if not _snap_check_matrix_applied(obj.matrix_world, delta @ original):
                        target_applied = False
                        audit_errors.append(
                            f"{getattr(record['root'], 'name', '<root>')}/"
                            f"{getattr(obj, 'name', '<object>')}"
                        )
                if not target["is_proxy"]:
                    if not delta_is_identity:
                        moved_lods += 1
                    visual_lines.append({
                        "name": getattr(target["object"], "name", "<unnamed>"),
                        "applied": target_applied,
                    })
            audit_records.append({
                "root": record["root"],
                "delta": delta,
                "visual_lines": visual_lines,
                "proxy_count": proxy_count,
                "object_count": object_count,
            })
        if audit_errors:
            raise RuntimeError(
                "solved root has visual objects that were not transformed: "
                + ", ".join(audit_errors)
            )
    except Exception:
        for obj, original in changes:
            _set_object_world_matrix_stable(obj, original)
        context.view_layer.update()
        raise
    return moved_roots, moved_lods, audit_records


def _snap_check_assembly_root_diagnostics(inventory, planned_deltas, audit_records, anchor_ptrs=None):
    anchor_set = set(anchor_ptrs or ())
    audit_by_ptr = {
        record["root"].as_pointer(): record for record in (audit_records or ())
    }
    diagnostics = []
    for info in inventory:
        root = info.get("root")
        root_ptr = root.as_pointer() if root is not None else None
        delta = planned_deltas.get(root_ptr)
        audit = audit_by_ptr.get(root_ptr)
        entry = {
            "root": root,
            "name": getattr(root, "name", "<unnamed root>"),
            "visual_lods": len(info.get("visuals") or ()),
            "transformed_objects": int((audit or {}).get("object_count", 0)),
        }
        if root_ptr in anchor_set:
            entry["status"] = "ANCHOR"
            entry["translation"] = (0.0, 0.0, 0.0)
            entry["rotation"] = (1.0, 0.0, 0.0, 0.0)
            entry["reason"] = "pinned anchor; solved component around its current position"
        elif delta is not None:
            location, rotation, _scale = delta.decompose()
            entry["status"] = (
                "IDENTITY" if _snap_check_matrix_is_identity(delta) else "MOVED"
            )
            entry["translation"] = tuple(float(value) for value in location)
            entry["rotation"] = tuple(float(value) for value in rotation)
            entry["reason"] = ""
        else:
            entry["status"] = "SKIPPED"
            entry["translation"] = (0.0, 0.0, 0.0)
            entry["rotation"] = (1.0, 0.0, 0.0, 0.0)
            entry["reason"] = "no complete exact A/V snap ID touches this root"
        diagnostics.append(entry)
    return diagnostics


_ASSEMBLY_LOD_NAME_TOKENS = {
    "memory": "9",
    "geometry physx": "8",
    "geometry": "6",
    "view geometry": "14",
    "fire geometry": "15",
    "roadway": "11",
    "land contact": "10",
    "paths": "12",
    "hit-points": "13",
    "hitpoints": "13",
    "view - pilot": "2",
    "view - gunner": "1",
    "view - cargo": "3",
    "view - commander": "18",
    "shadow volume": "4",
    "edit": "5",
    "sub parts": "25",
    "wreckage": "29",
    "underground (vbs)": "30",
    "groundlayer (vbs)": "31",
    "navigation (vbs)": "32",
}

_ASSEMBLY_LOD_HIDE_ORDER = {
    "9": 1,
    "6": 2,
    "15": 3,
    "14": 4,
    "11": 5,
}


def _assembly_lod_root_info(obj):
    """Resolve an object as a P3D LOD root: LOD property first, name only as fallback."""
    from .nh_textures import (_strip_blender_numeric_suffix)
    if obj is None or getattr(obj, "type", None) != "MESH":
        return None

    props = getattr(obj, "a3ob_properties_object", None)
    if props is not None:
        try:
            if bool(getattr(props, "is_a3_lod", False)):
                token = str(getattr(props, "lod", "") or "").strip()
                try:
                    resolution = int(getattr(props, "resolution", 0) or 0)
                except Exception:
                    resolution = 0
                try:
                    label = str(props.get_name())
                except Exception:
                    label = ""
                if not label:
                    label = f"{_collider_lod_name(token)} {resolution}".strip()
                return {
                    "token": token,
                    "resolution": resolution,
                    "label": label,
                    "visual0": _is_resolution0_visual_lod_object(obj),
                }
        except Exception:
            return None

    logical = _strip_blender_numeric_suffix(getattr(obj, "name", "") or "").strip()
    lowered = logical.lower()
    if lowered.startswith("resolution"):
        rest = logical[len("resolution"):].strip()
        try:
            resolution = int(rest) if rest else 0
        except Exception:
            resolution = 0
        return {
            "token": "0",
            "resolution": resolution,
            "label": f"Resolution {resolution}",
            "visual0": resolution == 0,
        }

    token = _ASSEMBLY_LOD_NAME_TOKENS.get(lowered, "")
    if token:
        return {
            "token": token,
            "resolution": 0,
            "label": _collider_lod_name(token),
            "visual0": False,
        }
    return None


def _assembly_lod_visibility_groups(root_collection):
    from .nh_textures import (_collect_collection_objects_recursive)
    candidates = []
    lod_ptrs = set()
    for obj in _collect_collection_objects_recursive(root_collection):
        info = _assembly_lod_root_info(obj)
        if info is None:
            continue
        candidates.append((obj, info))
        try:
            lod_ptrs.add(obj.as_pointer())
        except Exception:
            lod_ptrs.add(id(obj))

    groups = []
    for obj, info in candidates:
        parent = getattr(obj, "parent", None)
        nested = False
        while parent is not None:
            try:
                parent_ptr = parent.as_pointer()
            except Exception:
                parent_ptr = id(parent)
            if parent_ptr in lod_ptrs:
                nested = True
                break
            parent = getattr(parent, "parent", None)
        if nested:
            continue
        groups.append({"root": obj, "info": info, "objects": list(_iter_object_tree(obj))})
    return groups


def _assembly_lod_group_sort_key(group):
    info = group.get("info") or {}
    token = str(info.get("token", ""))
    if token == "0":
        try:
            return (0, int(info.get("resolution", 0) or 0), "")
        except Exception:
            return (0, 0, "")
    if token in _ASSEMBLY_LOD_HIDE_ORDER:
        return (1, _ASSEMBLY_LOD_HIDE_ORDER[token], "")
    try:
        return (2, int(token), "")
    except Exception:
        return (3, 0, str(info.get("label", "")))


def _apply_assembly_lod_visibility(context, inventory):
    """Show only visual Resolution 0 for each assembled P3D root, hide every other LOD."""
    print("=== Assembly LOD Visibility ===")
    updated_roots = 0
    missing_resolution0 = []

    for info in inventory or []:
        root_collection = info.get("root")
        root_label = getattr(root_collection, "name", "<unnamed root>")
        try:
            groups = _assembly_lod_visibility_groups(root_collection)
        except Exception as e:
            missing_resolution0.append(root_label)
            print(f"WARNING: root '{root_label}': LOD visibility scan failed: {e}")
            continue

        visible_groups = [group for group in groups if bool(group["info"].get("visual0"))]
        if not visible_groups:
            missing_resolution0.append(root_label)
            print(f"WARNING: root '{root_label}': Resolution 0 not found; LOD visibility unchanged")
            continue

        hidden_groups = [group for group in groups if not bool(group["info"].get("visual0"))]
        for group in groups:
            visible = bool(group["info"].get("visual0"))
            for obj in group["objects"]:
                _set_object_view_visible(obj, visible)

        visible_labels = []
        for group in sorted(visible_groups, key=_assembly_lod_group_sort_key):
            label = str(group["info"].get("label") or getattr(group["root"], "name", ""))
            if label not in visible_labels:
                visible_labels.append(label)
        hidden_labels = []
        for group in sorted(hidden_groups, key=_assembly_lod_group_sort_key):
            label = str(group["info"].get("label") or getattr(group["root"], "name", ""))
            if label not in hidden_labels:
                hidden_labels.append(label)

        print(f"root: {root_label}")
        print(f"visible: {', '.join(visible_labels)}")
        print(f"hidden: {', '.join(hidden_labels) if hidden_labels else '<none>'}")
        updated_roots += 1

    try:
        context.view_layer.update()
    except Exception:
        pass
    _tag_redraw_all_areas(context)
    return updated_roots, missing_resolution0


def _print_automatic_snap_check_report(
    inventory,
    pair_results,
    errors,
    warnings,
    assembly=None,
):
    print("=== NH Automatic Snap Magnet Check ===")
    print(f"Participating P3D roots (.sp_ found): {len(inventory)}")
    pass_count = sum(1 for rec in pair_results if rec.get("status") == "PASS")
    fail_count = sum(1 for rec in pair_results if rec.get("status") == "FAIL")
    warn_count = sum(1 for rec in pair_results if rec.get("status") == "WARN")
    print(
        f"Exact snap IDs: {len(pair_results)} "
        f"(PASS {pass_count}, FAIL {fail_count}, WARN {warn_count})"
    )
    for rec in pair_results:
        base_name = rec.get("base", "<unknown>")
        status = rec.get("status")
        if status == "PASS":
            print(
                f"PASS .sp_{base_name}: A-root={rec.get('a_root', '')}, "
                f"V-root={rec.get('v_root', '')}, "
                f"A_len={float(rec.get('span_a', 0.0) or 0.0):.6f}, "
                f"V_len={float(rec.get('span_v', 0.0) or 0.0):.6f}, "
                f"delta={float(rec.get('delta', 0.0) or 0.0):.6f}"
            )
        elif status == "FAIL":
            for reason in rec.get("reasons") or ("invalid snap pair",):
                print(f"FAIL .sp_{base_name}: {reason}")
        else:
            for reason in rec.get("reasons") or ("unmatched snap connector",):
                print(f"WARN .sp_{base_name}: {reason}")
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for issue in errors:
            print(f" - {issue}")
    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for issue in warnings:
            print(f" - {issue}")
    if assembly is not None:
        if isinstance(assembly, dict):
            moved_roots = int(assembly.get("moved_roots", 0) or 0)
            moved_lods = int(assembly.get("moved_lods", 0) or 0)
            endpoint_error = float(assembly.get("endpoint_error", 0.0) or 0.0)
            diagnostics = assembly.get("diagnostics") or []
            audit_records = assembly.get("audit") or []
        else:
            moved_roots, moved_lods, endpoint_error = assembly
            diagnostics = []
            audit_records = []
        print(
            f"ASSEMBLED: {moved_roots} model root(s), {moved_lods} visual Resolution LOD(s); "
            f"Memory/Geometry unchanged; endpoint error={endpoint_error:.8f}"
        )
        if diagnostics:
            print("=== Assembly Solve Diagnostics ===")
            for entry in diagnostics:
                translation = entry.get("translation") or (0.0, 0.0, 0.0)
                rotation = entry.get("rotation") or (1.0, 0.0, 0.0, 0.0)
                print(f"root={entry.get('name', '<unnamed root>')}")
                print(f"status={entry.get('status', 'UNKNOWN')}")
                print(
                    f"translation=({translation[0]:.6f},{translation[1]:.6f},{translation[2]:.6f})"
                )
                print(
                    f"rotation=({rotation[0]:.6f},{rotation[1]:.6f},"
                    f"{rotation[2]:.6f},{rotation[3]:.6f})"
                )
                print(f"visual_lods={int(entry.get('visual_lods', 0) or 0)}")
                print(f"transformed_objects={int(entry.get('transformed_objects', 0) or 0)}")
                reason = str(entry.get("reason") or "")
                if reason:
                    print(f"reason={reason}")
        if audit_records:
            print("=== Assembly Transform Audit ===")
            for record in audit_records:
                location, rotation, _scale = record["delta"].decompose()
                print(f"root: {getattr(record.get('root'), 'name', '<unnamed root>')}")
                print(
                    f"solved transform: translation=({location.x:.6f},{location.y:.6f},"
                    f"{location.z:.6f}) rotation=({rotation.w:.6f},{rotation.x:.6f},"
                    f"{rotation.y:.6f},{rotation.z:.6f})"
                )
                for line in record.get("visual_lines") or ():
                    status = "APPLIED" if line.get("applied") else "NOT TRANSFORMED"
                    print(f"{line.get('name', '<unnamed>')}: {status}")
                proxy_count = int(record.get("proxy_count", 0) or 0)
                if proxy_count:
                    print(f"proxy children: APPLIED ({proxy_count})")
    elif not errors:
        print("OK: automatic snap pairs are valid")
    print("=== End NH Automatic Snap Magnet Check ===")

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

        world_points = _collect_snap_pair_selected_world_points(self.context, edit_obj)
        if len(world_points) != 2:
            raise RuntimeError(
                f"Select exactly 2 vertices in Edit Mode (currently selected: {len(world_points)})"
            )

        if (world_points[1] - world_points[0]).length <= 1e-6:
            raise RuntimeError("The 2 selected vertices occupy the same world position")

        return _sort_snap_pair_world_points(self.context, world_points, preferred_axis_token=self.naming.axis_token)

    def create_dual_model_set(self):
        world_points = self.collect_selected_points()
        target_a = self.resolve_target_object("a")
        target_v = self.resolve_target_object("v")
        scope_a = _snap_target_memory_scope_key(self.context, target_a)
        scope_v = _snap_target_memory_scope_key(self.context, target_v)
        if target_a == target_v or (scope_a is not None and scope_a == scope_v):
            raise RuntimeError("Choose targets from two different model roots")

        _validate_snap_targets_near_selected_points(
            self.context,
            self.settings,
            world_points,
            (("A", target_a), ("V", target_v)),
        )

        memory_a = self._require_mesh_object(
            _ensure_memory_lod_for_snap_target(self.context, target_a),
            self._MEMORY_LABELS["a"],
        )
        memory_v = self._require_mesh_object(
            _ensure_memory_lod_for_snap_target(self.context, target_v),
            self._MEMORY_LABELS["v"],
        )
        _set_snap_memory_object(self.settings, "a", memory_a)
        _set_snap_memory_object(self.settings, "v", memory_v)
        if memory_a == memory_v:
            raise RuntimeError("Choose targets from two different model roots")

        scene_memory_pairs = _snap_scene_memory_pairs(self.context)
        if not scene_memory_pairs:
            scene_memory_pairs = [("a", memory_a), ("v", memory_v)]
        self.naming = _next_available_snap_naming(self.naming, scene_memory_pairs)

        self.ensure_object_mode()
        self.settings.snap_p3d_name = self.naming.p3d_name
        self.settings.snap_pair_code = self.naming.pair_code

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
        _validate_created_snap_pair(memory_a, memory_v, self.naming)
        created_naming = self.naming
        try:
            next_naming = _next_available_snap_naming(
                created_naming,
                _snap_scene_memory_pairs(self.context),
            )
            self.settings.snap_pair_code = next_naming.pair_code
        except RuntimeError:
            self.settings.snap_pair_code = created_naming.pair_code
        return targets, created_names, created_naming

def _snap_edit_mode_mesh_objects(context, source_obj):
    objects = [
        obj for obj in (getattr(context, "objects_in_mode_unique_data", None) or ())
        if obj is not None and obj.type == "MESH" and obj.mode == "EDIT"
    ]
    if source_obj is not None and source_obj.type == "MESH" and source_obj.mode == "EDIT":
        objects = [obj for obj in objects if obj != source_obj]
        objects.insert(0, source_obj)
    return objects


def _collect_snap_pair_selected_world_points(context, source_obj):
    if source_obj is None or source_obj.type != "MESH" or source_obj.mode != "EDIT":
        return []

    selected = []
    for obj in _snap_edit_mode_mesh_objects(context, source_obj):
        bm = bmesh.from_edit_mesh(obj.data)
        selected.extend(obj.matrix_world @ vert.co for vert in bm.verts if vert.select)
    return selected


def _snap_target_mesh_objects(target_obj):
    if target_obj is None:
        return []

    meshes = []
    seen = set()
    stack = [target_obj]
    while stack:
        obj = stack.pop()
        try:
            object_key = obj.as_pointer()
        except Exception:
            object_key = id(obj)
        if object_key in seen:
            continue
        seen.add(object_key)
        if obj.type == "MESH" and obj.data is not None:
            meshes.append(obj)
        stack.extend(reversed(list(getattr(obj, "children", ()))))
    return meshes


def _snap_target_nearest_vertices(target_obj, world_points):
    nearest = [None for _point in world_points]
    nearest_distances = [math.inf for _point in world_points]

    for obj in _snap_target_mesh_objects(target_obj):
        if obj.mode == "EDIT":
            vertices = bmesh.from_edit_mesh(obj.data).verts
        else:
            vertices = obj.data.vertices
        object_matrix = obj.matrix_world
        try:
            object_key = obj.as_pointer()
        except Exception:
            object_key = id(obj)

        for vertex in vertices:
            vertex_world = object_matrix @ vertex.co
            for point_index, world_point in enumerate(world_points):
                distance = (vertex_world - world_point).length
                if distance < nearest_distances[point_index]:
                    nearest_distances[point_index] = distance
                    nearest[point_index] = (object_key, int(vertex.index), obj.name)

    return list(zip(nearest, nearest_distances))


def _validate_snap_targets_near_selected_points(context, settings, world_points, labeled_targets):
    view_layer = getattr(context, "view_layer", None)
    if view_layer is not None:
        view_layer.update()

    tolerance = max(
        1e-6,
        float(
            getattr(
                settings,
                "snap_target_vertex_tolerance",
                _SNAP_TARGET_VERTEX_TOLERANCE_DEFAULT,
            )
            or _SNAP_TARGET_VERTEX_TOLERANCE_DEFAULT
        ),
    )
    issues = []

    for side_label, target_obj in labeled_targets:
        matches = _snap_target_nearest_vertices(target_obj, world_points)
        if not matches or any(match is None for match, _distance in matches):
            issues.append(f"Target ({side_label}) '{target_obj.name}' contains no usable vertices")
            continue

        for point_index, (_match, distance) in enumerate(matches):
            if distance > tolerance:
                issues.append(
                    f"Target ({side_label}) '{target_obj.name}' is not near selected point {point_index}: "
                    f"nearest vertex is {distance:.6f} m away (allowed {tolerance:.6f} m)"
                )

        if len(matches) == 2 and matches[0][0] == matches[1][0]:
            issues.append(
                f"Target ({side_label}) '{target_obj.name}' needs 2 distinct nearby vertices at the selected points"
            )

    if issues:
        raise RuntimeError(
            "Target proximity check failed. Check A/V Target: " + " | ".join(issues)
        )

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


_P3D_LOD_REINDEX_KNOWN_NAMES = None
_P3D_LOD_REINDEX_SCENE_PTR = None
_P3D_LOD_REINDEX_LAST_COUNT = -1


def _is_resolution_lod_object(obj) -> bool:
    if not _is_p3d_resolution_lod_object(obj):
        return False
    proxy = getattr(obj, "a3ob_properties_object_proxy", None)
    if proxy is not None and bool(getattr(proxy, "is_a3_proxy", False)):
        return False
    return True


def _resolution_lod_index(obj):
    props = getattr(obj, "a3ob_properties_object", None)
    if props is None:
        return None
    try:
        return int(getattr(props, "resolution", 0))
    except Exception:
        return None


def _p3d_lod_reindex_scope(context, obj):
    from .nh_textures import (_find_p3d_root_collection_for_object)
    if context is not None:
        root = _find_p3d_root_collection_for_object(context, obj)
        if root is not None:
            branch_key, _ = _actual_top_level_collection_key_under_root(root, obj)
            return root, branch_key
    collections = list(getattr(obj, "users_collection", []) or [])
    fallback = collections[0] if collections else None
    if fallback is None:
        return None, None
    return fallback, ""


def _looks_like_duplicated_object_name(name: str, known_names) -> bool:
    match = re.match(r"^(?P<base>.+)\.\d{3}$", (name or "").strip())
    if not match:
        return False
    return match.group("base") in (known_names or set())


def _build_resolution_lod_buckets(root):
    from .nh_textures import (_collect_collection_objects_recursive)
    buckets = {}
    for other in _collect_collection_objects_recursive(root):
        if not _is_resolution_lod_object(other):
            continue
        value = _resolution_lod_index(other)
        if value is None:
            continue
        branch_key, _ = _actual_top_level_collection_key_under_root(root, other)
        bucket = buckets.setdefault(branch_key, {})
        bucket[value] = bucket.get(value, 0) + 1
    return buckets


def _resolution_lod_scope_counts(root, branch_key, cache):
    from .nh_textures import (_collect_collection_objects_recursive)
    if branch_key:
        buckets = cache.get("__branches__")
        if buckets is None:
            buckets = _build_resolution_lod_buckets(root)
            cache["__branches__"] = buckets
        return buckets.get(branch_key)
    key = ("__fallback__", id(root))
    scope = cache.get(key)
    if scope is None:
        scope = {}
        for other in _collect_collection_objects_recursive(root):
            if not _is_resolution_lod_object(other):
                continue
            value = _resolution_lod_index(other)
            if value is None:
                continue
            scope[value] = scope.get(value, 0) + 1
        cache[key] = scope
    return scope


def _reindex_duplicated_resolution_lods(context, new_objects, known_names=None):
    changed = 0
    scope_cache = {}
    for obj in new_objects:
        if known_names is not None and not _looks_like_duplicated_object_name(obj.name, known_names):
            continue
        if not _is_resolution_lod_object(obj):
            continue
        own_index = _resolution_lod_index(obj)
        if own_index is None:
            continue
        root, branch_key = _p3d_lod_reindex_scope(context, obj)
        if root is None:
            continue
        scope = _resolution_lod_scope_counts(root, branch_key, scope_cache)
        if not scope or scope.get(own_index, 0) <= 1:
            continue
        new_index = max(scope) + 1
        props = getattr(obj, "a3ob_properties_object", None)
        if props is None:
            continue
        try:
            props.resolution = new_index
        except Exception:
            continue
        scope[new_index] = scope.get(new_index, 0) + 1
        scope[own_index] = max(0, scope.get(own_index, 0) - 1)
        changed += 1
    return changed


@persistent
def _p3d_lod_duplicate_reindex_handler(scene, depsgraph):
    global _P3D_LOD_REINDEX_KNOWN_NAMES, _P3D_LOD_REINDEX_SCENE_PTR, _P3D_LOD_REINDEX_LAST_COUNT
    try:
        scene_ptr = scene.as_pointer() if scene is not None else 0
        objects = bpy.data.objects
        count = len(objects)
        if _P3D_LOD_REINDEX_KNOWN_NAMES is None or scene_ptr != _P3D_LOD_REINDEX_SCENE_PTR:
            _P3D_LOD_REINDEX_KNOWN_NAMES = {obj.name for obj in objects}
            _P3D_LOD_REINDEX_SCENE_PTR = scene_ptr
            _P3D_LOD_REINDEX_LAST_COUNT = count
            return
        if count == _P3D_LOD_REINDEX_LAST_COUNT:
            return
        known_names = _P3D_LOD_REINDEX_KNOWN_NAMES
        new_objects = [obj for obj in objects if obj.name not in known_names]
        _P3D_LOD_REINDEX_KNOWN_NAMES = {obj.name for obj in objects}
        _P3D_LOD_REINDEX_LAST_COUNT = count
        if not new_objects:
            return
        _reindex_duplicated_resolution_lods(bpy.context, new_objects, known_names)
    except Exception:
        return


def _prime_p3d_lod_reindex_state():
    global _P3D_LOD_REINDEX_KNOWN_NAMES, _P3D_LOD_REINDEX_SCENE_PTR, _P3D_LOD_REINDEX_LAST_COUNT
    try:
        scene = getattr(bpy.context, "scene", None)
        _P3D_LOD_REINDEX_SCENE_PTR = scene.as_pointer() if scene is not None else 0
        _P3D_LOD_REINDEX_KNOWN_NAMES = {obj.name for obj in bpy.data.objects}
        _P3D_LOD_REINDEX_LAST_COUNT = len(bpy.data.objects)
    except Exception:
        _P3D_LOD_REINDEX_KNOWN_NAMES = None
        _P3D_LOD_REINDEX_SCENE_PTR = None
        _P3D_LOD_REINDEX_LAST_COUNT = -1


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


class CRAY_OT_ToggleSnapNameAxis(Operator):
    bl_idname = "cray.toggle_snap_name_axis"
    bl_label = "Toggle Snap Axis"
    bl_description = "Select the 0/1 sort axis and include it in names (blue); click again to omit it from names (gray). The last axis is kept for 0/1 ordering"
    bl_options = {"UNDO"}

    axis: EnumProperty(
        items=(("X", "X", "X axis"), ("Y", "Y", "Y axis"), ("Z", "Z", "Z axis")),
        default="X",
    )

    def execute(self, context):
        settings = context.scene.cray_snap_settings
        include_axis = not (settings.snap_include_axis and settings.edge_axis == self.axis)
        settings.edge_axis = self.axis
        settings.snap_include_axis = include_axis
        return {"FINISHED"}


class CRAY_OT_SnapSetP3DVisualsOnly(Operator):
    bl_idname = "cray.snap_set_p3d_visuals_only"
    bl_label = "Visual 0 Only"
    bl_description = "Показывает только Resolution 0 в Visuals и оставляет видимыми Point clouds внутри каждой .p3d коллекции"
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
    bl_description = "Показывает все ветки и объекты внутри каждой .p3d коллекции"
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
    bl_description = "Находит или создаёт Point clouds > Memory для выбранных A Target и V Target; если цели не выбраны, готовит Memory для всех .p3d коллекций сцены"
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
        "Копирует 2 выделенные вершины из Edit Mode в Point clouds > Memory выбранных A Target и V Target, создавая пары .sp_a/.sp_v по текущему шаблону имени"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        ss = context.scene.cray_snap_settings
        try:
            targets, created_names, created_naming = _SnapPointPairBuilder(context, ss).create_dual_model_set()
        except Exception as e:
            message = _fmt_exc(e)
            print("=== NH Snap Point Creation ===")
            print(f"ERROR: {message}")
            print("No snap points were created. Check the two selected vertices and A/V Target fields.")
            print("=== End NH Snap Point Creation ===")
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        target_names = ", ".join(
            f"{side.upper()}: {target_obj.name} -> {memory_obj.name}"
            for side, target_obj, memory_obj in targets
        )
        print("=== NH Snap Point Creation ===")
        for target_index, (side, target_obj, memory_obj) in enumerate(targets):
            name_offset = target_index * 2
            side_names = created_names[name_offset:name_offset + 2]
            print(
                f"{side.upper()} Target: {target_obj.name} -> {memory_obj.name}: "
                f"{', '.join(side_names)}"
            )
        print(f"Created connection ID: {created_naming.pair_code}")
        print("=== End NH Snap Point Creation ===")
        self.report(
            {"INFO"},
            (
                f"Created {len(created_names)} snap points in {target_names}: "
                f"{', '.join(created_names)}"
            ),
        )
        return {"FINISHED"}


class CRAY_OT_ValidateAssembleSnapPoints(Operator):
    bl_idname = "cray.validate_assemble_snap_points"
    bl_label = "Check & Assemble Visual LODs"
    bl_description = (
        "Automatically find every exact A/V snap ID in all P3D Memory LODs, "
        "validate each ID locally, and assemble only visual Resolution LODs"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider import (_try_restore_edit_mode)
        inventory = []
        pair_results = []
        edges = []
        errors = []
        warnings = []
        assembly = None
        visibility_roots = 0
        missing_resolution0 = []
        restore_edit_mode = (
            context.mode == "EDIT_MESH"
            and context.active_object is not None
            and context.active_object.type == "MESH"
        )
        active_before = context.active_object

        try:
            inventory, errors, warnings = _snap_check_scene_inventory(context)
            pair_results, edges = _snap_check_build_local_pairs(inventory, errors)

            # Planning uses virtual snap points and performs no Blender writes. This
            # makes every diagnostic visible before any model can be moved.
            planned_deltas = {}
            endpoint_error = 0.0
            anchor_ptrs = []
            if not errors:
                planned_deltas, _ordered_edges, endpoint_error, anchor_ptrs = (
                    _snap_check_plan_automatic_assembly(edges)
                )

            if not errors:
                if context.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                moved_roots, moved_lods, audit_records = _snap_check_apply_automatic_assembly(
                    context,
                    inventory,
                    planned_deltas,
                )
                diagnostics = _snap_check_assembly_root_diagnostics(
                    inventory,
                    planned_deltas,
                    audit_records,
                    anchor_ptrs=anchor_ptrs,
                )
                assembly = {
                    "moved_roots": moved_roots,
                    "moved_lods": moved_lods,
                    "endpoint_error": endpoint_error,
                    "diagnostics": diagnostics,
                    "audit": audit_records,
                }
                visibility_roots, missing_resolution0 = _apply_assembly_lod_visibility(context, inventory)
        except Exception as exc:
            errors.append(_fmt_exc(exc))
        finally:
            if restore_edit_mode and active_before is not None:
                _try_restore_edit_mode(context, active_before)

        _print_automatic_snap_check_report(
            inventory,
            pair_results,
            errors,
            warnings,
            assembly=assembly,
        )

        if assembly is None:
            self.report(
                {"ERROR"},
                f"Automatic snap failed: {len(errors)} error(s); nothing was moved; see System Console",
            )
            return {"CANCELLED"}

        moved_roots = int(assembly.get("moved_roots", 0) or 0)
        moved_lods = int(assembly.get("moved_lods", 0) or 0)
        endpoint_error = float(assembly.get("endpoint_error", 0.0) or 0.0)
        if warnings or missing_resolution0:
            self.report(
                {"WARNING"},
                (
                    f"Assembled {moved_roots} model(s), {moved_lods} visual LOD(s); "
                    f"LOD visibility updated for {visibility_roots} root(s); "
                    f"{len(warnings)} snap warning(s), {len(missing_resolution0)} root(s) without Resolution 0; "
                    "see System Console"
                ),
            )
        else:
            self.report(
                {"INFO"},
                (
                    f"Automatic snap OK: assembled {moved_roots} model(s), {moved_lods} visual LOD(s), "
                    f"LOD visibility updated for {visibility_roots} root(s), "
                    f"endpoint error {endpoint_error:.8f}"
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
                    recalculate_components=True,
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
