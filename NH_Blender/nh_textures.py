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

# nh_textures.py
# auto-split slice; cross-module refs resolved with in-function imports

def _get_bundled_python_dds_converter_path(settings=None):
    from .nh_collider_exp import (_first_existing_texture_tool)
    return _first_existing_texture_tool("dds_python.py", settings=settings)

def _get_bundled_dds_converter_exe(settings=None):
    from .nh_collider_exp import (_first_existing_texture_tool)
    return _first_existing_texture_tool("nh_dds_converter.exe", settings=settings, include_bin=True)

def _dds_backend_display_name(backend):
    from .nh_collider_exp import (_TEX_EXPORT_DDS_BACKEND_LABELS)
    key = str(backend or "").upper()
    if key in _TEX_EXPORT_DDS_BACKEND_LABELS:
        return _TEX_EXPORT_DDS_BACKEND_LABELS[key]
    return key or "<unknown>"

def _find_node_exe(settings):
    from .nh_collider_exp import (_norm_path, _tex_export_resolve_path)
    configured = _tex_export_resolve_path(getattr(settings, "node_exe_path", ""))
    if configured and os.path.isfile(configured):
        return configured

    found = shutil.which("node")
    if found and os.path.isfile(found):
        return _norm_path(found)

    for candidate in (
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
    ):
        if os.path.isfile(candidate):
            return _norm_path(candidate)
    return ""

def _tex_export_subprocess_creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)

def _run_tex_export_converter_command(args, cwd=None, timeout=120):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=_tex_export_subprocess_creationflags(),
    )

def _tex_export_converter_error(prefix, result):
    detail = "\n".join(
        part.strip()
        for part in (getattr(result, "stdout", ""), getattr(result, "stderr", ""))
        if part and part.strip()
    )
    if detail:
        return f"{prefix}: {detail}"
    return f"{prefix}: exit code {getattr(result, 'returncode', '<unknown>')}"

def _convert_dds_to_png_python(dds_path, output_png, mode, settings):
    converter_path = _get_bundled_python_dds_converter_path(settings)
    if not converter_path:
        raise RuntimeError("Built-in Python DDS converter not found")

    folder = os.path.dirname(output_png)
    if folder:
        os.makedirs(folder, exist_ok=True)

    module_name = "_nh_blender_dds_python_converter"
    spec = importlib.util.spec_from_file_location(module_name, os.path.abspath(converter_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load built-in Python DDS converter: {converter_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    convert = getattr(module, "convert_dds_to_png", None)
    if not callable(convert):
        raise RuntimeError("Built-in Python DDS converter does not expose convert_dds_to_png")

    result = convert(dds_path, output_png, mode)
    if not os.path.isfile(output_png):
        raise RuntimeError("Python DDS converter finished but PNG was not created")
    return result or output_png

def _convert_dds_to_png_bundled_exe(dds_path, output_png, mode, settings):
    exe = _get_bundled_dds_converter_exe(settings)
    if not exe:
        raise RuntimeError("Bundled DDS converter EXE not found")

    folder = os.path.dirname(output_png)
    if folder:
        os.makedirs(folder, exist_ok=True)

    result = _run_tex_export_converter_command(
        [
            exe,
            "--input", dds_path,
            "--output", output_png,
            "--mode", mode,
        ],
        cwd=os.path.dirname(exe) or None,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(_tex_export_converter_error("Bundled EXE DDS converter failed", result))

    if not os.path.isfile(output_png):
        raise RuntimeError("Bundled EXE finished but PNG was not created")
    return output_png

def _convert_dds_to_png_node(dds_path, output_png, mode, settings):
    from .nh_collider_exp import (_get_bundled_xray_converter_js)
    converter_js = _get_bundled_xray_converter_js(settings)
    node_exe = _find_node_exe(settings)

    if not node_exe:
        raise RuntimeError("Node.js not found")
    if not converter_js:
        raise RuntimeError("Bundled DDS converter not found")

    folder = os.path.dirname(output_png)
    if folder:
        os.makedirs(folder, exist_ok=True)

    result = _run_tex_export_converter_command(
        [
            node_exe,
            converter_js,
            "--input", dds_path,
            "--output", output_png,
            "--mode", mode,
        ],
        cwd=os.path.dirname(converter_js) or None,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(_tex_export_converter_error("Node DDS converter failed", result))

    if not os.path.isfile(output_png):
        raise RuntimeError("Node converter finished but PNG was not created")
    return output_png

def _convert_dds_to_png_external(dds_path, output_png, mode, settings):
    from .nh_collider_exp import (_tex_export_resolve_path)
    converter_path = _tex_export_resolve_path(getattr(settings, "external_dds_converter_path", ""))
    if not converter_path or not os.path.isfile(converter_path):
        raise RuntimeError("External DDS converter not found")

    folder = os.path.dirname(output_png)
    if folder:
        os.makedirs(folder, exist_ok=True)

    ext = os.path.splitext(converter_path)[1].lower()
    if ext == ".js":
        node_exe = _find_node_exe(settings)
        if not node_exe:
            raise RuntimeError("Node.js not found for external DDS converter")
        args = [
            node_exe,
            converter_path,
            "--input", dds_path,
            "--output", output_png,
            "--mode", mode,
        ]
        cwd = os.path.dirname(converter_path) or None
    else:
        args = [
            converter_path,
            "--input", dds_path,
            "--output", output_png,
            "--mode", mode,
        ]
        cwd = os.path.dirname(converter_path) or None

    result = _run_tex_export_converter_command(args, cwd=cwd, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(_tex_export_converter_error("External DDS converter failed", result))

    if not os.path.isfile(output_png):
        raise RuntimeError("External DDS converter finished but PNG was not created")
    return output_png

def _convert_dds_to_png_blender(dds_path: str, output_png: str, mode: str, events=None, label="", material_base=""):
    from .nh_base import (_fmt_exc)
    from .nh_collider_exp import (_save_rgba_pixels_to_png)
    _tex_export_log_event(
        events,
        "INFO",
        "DDS_LOAD_START",
        "Loading DDS with Blender image API",
        label=label,
        material_base=material_base,
        mode=mode,
        source_dds=dds_path,
    )
    try:
        src_img = bpy.data.images.load(dds_path, check_existing=False)
    except Exception as e:
        _tex_export_log_event(
            events,
            "ERROR",
            "DDS_LOAD_FAILED",
            "Blender image API failed to load DDS",
            label=label,
            material_base=material_base,
            mode=mode,
            source_dds=dds_path,
            exception=_fmt_exc(e),
        )
        raise RuntimeError(f"DDS conversion backend is not available: {_fmt_exc(e)}")

    try:
        width, height = int(src_img.size[0]), int(src_img.size[1])
        _tex_export_log_event(
            events,
            "INFO",
            "DDS_LOAD_OK",
            "Blender image API loaded DDS",
            label=label,
            material_base=material_base,
            mode=mode,
            source_dds=dds_path,
            width=width,
            height=height,
        )
        if width <= 0 or height <= 0:
            raise RuntimeError("DDS image has invalid dimensions")

        pixel_count = width * height * 4
        pixels = [0.0] * pixel_count
        try:
            src_img.pixels.foreach_get(pixels)
        except Exception as e:
            _tex_export_log_event(
                events,
                "ERROR",
                "DDS_PIXELS_FAILED",
                "Blender image API loaded DDS but pixel access failed",
                label=label,
                material_base=material_base,
                mode=mode,
                source_dds=dds_path,
                exception=_fmt_exc(e),
            )
            raise RuntimeError(f"DDS conversion backend is not available: {_fmt_exc(e)}")

        if len(pixels) != pixel_count:
            raise RuntimeError("DDS conversion backend returned an unexpected pixel buffer")

        if mode == "diffuse":
            out_pixels = pixels
        else:
            out_pixels = [0.0] * pixel_count
            for i in range(0, pixel_count, 4):
                r = pixels[i]
                g = pixels[i + 1]
                b = pixels[i + 2]
                a = pixels[i + 3]
                if mode == "nohq":
                    out_pixels[i] = a
                    out_pixels[i + 1] = b
                    out_pixels[i + 2] = g
                    out_pixels[i + 3] = 1.0
                elif mode == "smdi":
                    out_pixels[i] = 1.0
                    out_pixels[i + 1] = r
                    out_pixels[i + 2] = 0.0
                    out_pixels[i + 3] = 1.0
                else:
                    raise RuntimeError(f"Unsupported DDS conversion mode: {mode}")

        _tex_export_log_event(
            events,
            "INFO",
            "PNG_SAVE_START",
            "Saving converted PNG",
            label=label,
            material_base=material_base,
            mode=mode,
            target_png=output_png,
        )
        try:
            _save_rgba_pixels_to_png(width, height, out_pixels, output_png)
        except Exception as e:
            _tex_export_log_event(
                events,
                "ERROR",
                "PNG_SAVE_FAILED",
                "Blender loaded DDS but failed to save PNG",
                label=label,
                material_base=material_base,
                mode=mode,
                source_dds=dds_path,
                target_png=output_png,
                exception=_fmt_exc(e),
            )
            raise
        _tex_export_log_event(
            events,
            "INFO",
            "PNG_SAVE_OK",
            "PNG save call finished",
            label=label,
            material_base=material_base,
            mode=mode,
            target_png=output_png,
            exists=os.path.isfile(output_png),
            size=_tex_export_file_size(output_png),
        )
    finally:
        try:
            bpy.data.images.remove(src_img)
        except Exception:
            pass

    if not os.path.isfile(output_png):
        _tex_export_log_event(
            events,
            "ERROR",
            "PNG_SAVE_OUTPUT_MISSING",
            "PNG save call finished but output file is missing",
            label=label,
            material_base=material_base,
            mode=mode,
            target_png=output_png,
        )
        raise RuntimeError(f"PNG was not created: {output_png}")
    return output_png

def _convert_dds_to_png_export(dds_path, output_png, mode, settings, events=None, material_base=""):
    from .nh_base import (_fmt_exc)
    from .nh_collider_exp import (_TEX_EXPORT_DDS_BACKEND_LABELS, _get_bundled_xray_converter_js, _tex_export_resolve_path)
    requested = str(getattr(settings, "dds_backend", "BUILTIN_PYTHON") or "BUILTIN_PYTHON")
    label = f"{material_base}: {mode}" if material_base else mode
    errors = []

    backends = [requested] if requested in _TEX_EXPORT_DDS_BACKEND_LABELS else ["BUILTIN_PYTHON"]
    if backends == [requested] and requested == "AUTO":
        backends = ["BUILTIN_PYTHON"]

    _tex_export_log_event(
        events,
        "INFO",
        "DDS_BACKEND_SELECT",
        "DDS backend selected",
        label=label,
        material_base=material_base,
        requested_backend=_dds_backend_display_name(requested),
        requested_backend_id=requested,
        planned_backends=[_dds_backend_display_name(item) for item in backends],
        planned_backend_ids=backends,
        python_converter=_get_bundled_python_dds_converter_path(settings),
        bundled_exe=_get_bundled_dds_converter_exe(settings),
        converter_js=_get_bundled_xray_converter_js(settings),
        node_exe=_find_node_exe(settings),
        external_dds_converter=_tex_export_resolve_path(getattr(settings, "external_dds_converter_path", "")),
    )

    for backend in backends:
        backend_name = _dds_backend_display_name(backend)
        _tex_export_log_event(
            events,
            "INFO",
            "DDS_BACKEND_ATTEMPT",
            "Trying DDS conversion backend",
            label=label,
            material_base=material_base,
            backend=backend_name,
            backend_id=backend,
            source_dds=dds_path,
            target_png=output_png,
            mode=mode,
        )
        try:
            if backend == "BUILTIN_PYTHON":
                result = _convert_dds_to_png_python(dds_path, output_png, mode, settings)
            elif backend == "BUNDLED_EXE":
                result = _convert_dds_to_png_bundled_exe(dds_path, output_png, mode, settings)
            elif backend == "BUNDLED_NODE":
                result = _convert_dds_to_png_node(dds_path, output_png, mode, settings)
            elif backend == "EXTERNAL":
                result = _convert_dds_to_png_external(dds_path, output_png, mode, settings)
            elif backend == "BLENDER":
                result = _convert_dds_to_png_blender(
                    dds_path,
                    output_png,
                    mode,
                    events=events,
                    label=label,
                    material_base=material_base,
                )
            else:
                raise RuntimeError(f"Unsupported DDS backend: {backend}")

            _tex_export_log_event(
                events,
                "INFO",
                "DDS_BACKEND_OK",
                "DDS backend converted PNG",
                label=label,
                material_base=material_base,
                backend=backend_name,
                backend_id=backend,
                source_dds=dds_path,
                target_png=output_png,
                mode=mode,
                exists=os.path.isfile(output_png),
                size=_tex_export_file_size(output_png),
            )
            return result
        except Exception as e:
            error = f"{backend_name}: {_fmt_exc(e)}"
            errors.append(error)
            _tex_export_log_event(
                events,
                "WARNING" if requested == "AUTO" else "ERROR",
                "DDS_BACKEND_FAILED",
                "DDS backend failed",
                label=label,
                material_base=material_base,
                backend=backend_name,
                backend_id=backend,
                source_dds=dds_path,
                target_png=output_png,
                mode=mode,
                exception=_fmt_exc(e),
                auto_will_try_next=bool(requested == "AUTO" and backend != backends[-1]),
            )

    raise RuntimeError("DDS conversion failed with all selected backends: " + " | ".join(errors))

def _convert_png_to_paa_external(png_path: str, paa_path: str, exe_path: str, events=None, label="", material_base=""):
    if not exe_path or not os.path.isfile(exe_path):
        raise RuntimeError("ImageToPAA not found, PAA conversion skipped")

    folder = os.path.dirname(paa_path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    result = subprocess.run(
        [exe_path, png_path, paa_path],
        cwd=os.path.dirname(exe_path) or None,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            raise RuntimeError(f"PAA conversion failed: {detail}")
        raise RuntimeError(f"PAA conversion failed with code {result.returncode}")

    if not os.path.isfile(paa_path):
        alt_path = os.path.splitext(png_path)[0] + ".paa"
        if os.path.isfile(alt_path) and os.path.normcase(alt_path) != os.path.normcase(paa_path):
            _tex_export_log_event(
                events,
                "WARNING",
                "PAA_OUTPUT_PATH_MISMATCH",
                "ImageToPAA created a different output path; copying it to requested target",
                label=label,
                material_base=material_base,
                source_png=png_path,
                target_paa=paa_path,
                actual_paa=alt_path,
            )
            shutil.copy2(alt_path, paa_path)

    if not os.path.isfile(paa_path):
        raise RuntimeError("PAA conversion finished but output .paa was not created")
    return paa_path

def _tex_export_refresh_db(settings, folder_abs: str):
    entries = _walk_folder_build_db(folder_abs)
    settings.db_items.clear()
    for d in entries:
        it = settings.db_items.add()
        it.basename = d["basename"]
        it.abs_path = d["abs_path"]
        it.rel_path = d["rel_path"]
        it.is_problem = d["is_problem"]
        it.dup_count = d["dup_count"]
    return entries

def _tex_export_source_tried_lines(source_root, rel_dir: str, names):
    from .nh_collider_exp import (_norm_path, _sanitize_tex_export_base, _unique_ci)
    out = []
    roots = source_root if isinstance(source_root, (list, tuple, set)) else [source_root]
    for root in roots:
        if not root:
            continue
        for name in names:
            base = _sanitize_tex_export_base(name)
            if not base:
                continue
            if rel_dir:
                out.append(_norm_path(os.path.join(root, rel_dir, base + ".dds")))
            out.append(_norm_path(os.path.join(root, base + ".dds")))
    return _unique_ci(out)

def _tex_export_file_size(path: str):
    try:
        return os.path.getsize(path) if path and os.path.isfile(path) else None
    except Exception:
        return None

def _tex_export_log_event(events, level, code, message, **data):
    if events is None:
        return
    events.append({
        "level": str(level or "INFO").upper(),
        "code": str(code or "EVENT"),
        "message": str(message or ""),
        "data": data,
    })

def _tex_export_event_text(event) -> str:
    level = event.get("level", "INFO")
    code = event.get("code", "EVENT")
    message = event.get("message", "")
    data = event.get("data") or {}
    label = data.get("label") or data.get("material_base") or ""
    prefix = f"[{level}]"
    if label:
        prefix += f" {label}"
    lines = [f"{prefix} {code}: {message}".rstrip()]
    for key, value in data.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, (list, tuple)):
            lines.append(f"  {key}:")
            for item in value:
                lines.append(f"    {item}")
        else:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)

def _write_texture_export_logs(settings, target_root, events):
    from .nh_base import (_fmt_exc)
    from .nh_collider_exp import (_norm_path, _tex_export_resolve_path)
    del settings
    if not events:
        return [], []

    error_events = [event for event in events if event.get("level") == "ERROR"]
    if not error_events:
        return [], []

    txt_path = _norm_path(os.path.join(_tex_export_resolve_path(target_root), "_nh_texture_export_errors.txt"))
    try:
        folder = os.path.dirname(txt_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(txt_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("=== Texture Source Export Errors ===\n")
            for event in error_events:
                f.write(_tex_export_event_text(event))
                f.write("\n\n")
        return [txt_path], []
    except Exception as e:
        return [], [f"{txt_path}: {_fmt_exc(e)}"]

def _tex_export_relative_output(path: str, target_root: str) -> str:
    from .nh_collider_exp import (_to_dayz_relative_texture_path)
    return _to_dayz_relative_texture_path(path, target_root)

def _tex_export_result_item(material_base, kind, source, output, target_root, reason=""):
    from .nh_collider_exp import (_norm_path)
    return {
        "material_base": material_base or "",
        "kind": kind or "",
        "source": _norm_path(source or ""),
        "output": _norm_path(output or ""),
        "relative_output": _tex_export_relative_output(output, target_root) if output else "",
        "reason": reason or "",
    }


def _tex_export_item_output_path(item):
    if not isinstance(item, dict):
        return ""
    return item.get("output") or item.get("relative_output") or ""

def _tex_export_norm_output_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path or "")))

def _tex_export_is_png_path(path: str) -> bool:
    return str(path or "").lower().endswith(".png")

def _tex_export_is_paa_path(path: str) -> bool:
    return str(path or "").lower().endswith(".paa")

def _tex_export_is_rvmat_path(path: str) -> bool:
    return str(path or "").lower().endswith(".rvmat")

def _tex_export_append_unique(items, item):
    if not isinstance(item, dict):
        return False
    key = _tex_export_norm_output_key(_tex_export_item_output_path(item))
    if not key:
        return False
    for existing in items:
        if _tex_export_norm_output_key(_tex_export_item_output_path(existing)) == key:
            return False
    items.append(item)
    return True

def _tex_export_filter_unique_items(items, predicate):
    result = []
    for item in items or []:
        path = _tex_export_item_output_path(item)
        if predicate(path):
            _tex_export_append_unique(result, item)
    return result

def _tex_export_set_progress(context, ts, current, total, label="", action=""):
    from .nh_snap import (_tag_redraw_all_areas)
    try:
        ts.texture_export_is_running = True
        ts.texture_export_progress_current = int(current or 0)
        ts.texture_export_progress_total = int(total or 0)
        ts.texture_export_progress_label = str(label or "")
        ts.texture_export_progress_action = str(action or "")
        _tag_redraw_all_areas(context)
    except Exception:
        pass

def _tex_export_finish_progress(context, ts):
    from .nh_snap import (_tag_redraw_all_areas)
    try:
        ts.texture_export_is_running = False
        ts.texture_export_progress_action = ""
        _tag_redraw_all_areas(context)
    except Exception:
        pass


def _draw_texture_export_progress(layout, ts):
    current = int(getattr(ts, "texture_export_progress_current", 0) or 0)
    total = int(getattr(ts, "texture_export_progress_total", 0) or 0)
    factor = (float(current) / float(total)) if total > 0 else 0.0
    factor = max(0.0, min(1.0, factor))
    percent = factor * 100.0
    try:
        layout.progress(factor=factor, type="BAR", text=f"{current}/{total} ({percent:.1f}%)")
    except Exception:
        layout.label(text=f"Progress: {current}/{total} ({percent:.1f}%)")
    label = getattr(ts, "texture_export_progress_label", "") or ""
    action = getattr(ts, "texture_export_progress_action", "") or ""
    if label:
        layout.label(text=f"Current: {label}")
    if action:
        layout.label(text=f"Action: {action}")

def _tex_export_workspace_status(context, text):
    try:
        workspace = getattr(context, "workspace", None)
        if workspace and hasattr(workspace, "status_text_set"):
            workspace.status_text_set(text)
    except Exception:
        pass

def _print_texture_export_created_section(title, items, limit=100):
    print(f"{title}:")
    if not items:
        print("- <none>")
        return
    for item in items[:limit]:
        print(f"- {item.get('relative_output') or item.get('output') or '<unknown>'}")
    if len(items) > limit:
        print(f"... {len(items) - limit} more not shown")

def _write_texture_export_report_section(f, title, items, limit=None, more_message=None):
    f.write(f"\n=== {title} ===\n")
    if not items:
        f.write("<none>\n")
        return
    shown = items if limit is None else items[:limit]
    for item in shown:
        rel = item.get("relative_output") or item.get("output") or "<unknown>"
        f.write(f"- {rel}\n")
        source = item.get("source") or ""
        reason = item.get("reason") or ""
        tried = item.get("tried") or []
        if source:
            f.write(f"  source: {source}\n")
        if reason:
            f.write(f"  reason: {reason}\n")
        if tried:
            f.write("  source tried:\n")
            for tried_path in tried:
                f.write(f"    {tried_path}\n")
    if limit is not None and len(items) > limit:
        hidden = len(items) - limit
        if more_message:
            f.write(more_message.format(hidden=hidden) + "\n")
        else:
            f.write(f"... {hidden} more not shown. Full list is available in JSON report.\n")

def _write_texture_export_last_report(
    ts,
    summary,
    exported_diffuse,
    exported_nohq,
    exported_smdi,
    exported_paa,
    created_rvmat,
    skipped_existing,
    missing_sources,
    failed_items,
):
    from .nh_collider_exp import (_norm_path, _tex_export_resolve_path)
    target_root = _tex_export_resolve_path(summary.get("target_root") or getattr(ts, "target_textures_folder", ""), fallback=getattr(ts, "folder", ""))
    if not target_root:
        raise RuntimeError("Target Textures Folder is not set")
    os.makedirs(target_root, exist_ok=True)

    exported_diffuse = _tex_export_filter_unique_items(exported_diffuse, _tex_export_is_png_path)
    exported_nohq = _tex_export_filter_unique_items(exported_nohq, _tex_export_is_png_path)
    exported_smdi = _tex_export_filter_unique_items(exported_smdi, _tex_export_is_png_path)
    exported_paa = _tex_export_filter_unique_items(exported_paa, _tex_export_is_paa_path)
    created_rvmat = _tex_export_filter_unique_items(created_rvmat, _tex_export_is_rvmat_path)
    skipped_existing = _tex_export_filter_unique_items(skipped_existing, lambda path: bool(path))
    missing_sources = _tex_export_filter_unique_items(missing_sources, lambda path: bool(path))
    failed_items = _tex_export_filter_unique_items(failed_items, lambda path: bool(path))

    report_txt_path = _norm_path(os.path.join(target_root, "_nh_texture_export_last_report.txt"))
    report_json_path = _norm_path(os.path.join(target_root, "_nh_texture_export_last_report.json"))
    report_data = {
        "summary": summary,
        "exported_diffuse": exported_diffuse,
        "exported_nohq": exported_nohq,
        "exported_smdi": exported_smdi,
        "exported_paa": exported_paa,
        "created_rvmat": created_rvmat,
        "skipped_existing": skipped_existing,
        "missing_sources": missing_sources,
        "failed_items": failed_items,
    }

    with open(report_txt_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("=== NH Texture Export Report ===\n")
        labels = (
            ("Source root", "source_root"),
            ("Target root", "target_root"),
            ("DDS Backend", "dds_backend"),
            ("DDS scanned", "dds_scanned"),
            ("Missing requested", "missing_requested"),
            ("Diffuse converted", "diffuse_converted"),
            ("NOHQ converted", "nohq_converted"),
            ("SMDI converted", "smdi_converted"),
            ("PAA converted", "paa_converted"),
            ("RVMAT created", "rvmat_created"),
            ("Skipped existing", "skipped_existing"),
            ("Source not found", "source_not_found"),
            ("Failed", "failed"),
            ("DB rebuilt", "db_rebuilt"),
        )
        for label, key in labels:
            if key in summary and summary.get(key) is not None:
                f.write(f"{label}: {summary.get(key)}\n")
        _write_texture_export_report_section(f, "Created Diffuse", exported_diffuse)
        _write_texture_export_report_section(f, "Created NOHQ", exported_nohq)
        _write_texture_export_report_section(f, "Created SMDI", exported_smdi)
        _write_texture_export_report_section(f, "Created PAA", exported_paa)
        _write_texture_export_report_section(f, "Created RVMAT", created_rvmat)
        _write_texture_export_report_section(
            f,
            "Skipped Existing",
            skipped_existing,
            limit=100,
            more_message="... {hidden} more skipped existing not shown. Full list is available in JSON report.",
        )
        _write_texture_export_report_section(
            f,
            "Missing Sources",
            missing_sources,
            limit=100,
            more_message="... {hidden} more missing source(s) not shown. Full list is available in JSON report.",
        )
        _write_texture_export_report_section(
            f,
            "Failed",
            failed_items,
            limit=100,
            more_message="... {hidden} more failed item(s) not shown. Full list is available in JSON report.",
        )

    with open(report_json_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2, sort_keys=True)

    return report_txt_path

def _print_texture_export_backend_summary(events):
    ok_by_backend = {}
    failed_by_backend = {}
    failed_keys_by_backend = {}
    ok_keys = set()

    for event in events:
        data = event.get("data") or {}
        backend = data.get("backend")
        if not backend:
            continue
        key = (
            data.get("source_dds") or "",
            data.get("target_png") or "",
            data.get("mode") or "",
        )
        if event.get("code") == "DDS_BACKEND_OK":
            ok_by_backend[backend] = ok_by_backend.get(backend, 0) + 1
            ok_keys.add(key)
        elif event.get("code") == "DDS_BACKEND_FAILED":
            failed_by_backend[backend] = failed_by_backend.get(backend, 0) + 1
            failed_keys_by_backend.setdefault(backend, set()).add(key)

    if not ok_by_backend and not failed_by_backend:
        return

    if ok_by_backend:
        names = sorted(ok_by_backend, key=lambda name: (-ok_by_backend[name], name.lower()))
        if len(names) == 1:
            print(f"DDS backend used: {names[0]}")
        else:
            summary = ", ".join(f"{name}={ok_by_backend[name]}" for name in names)
            print(f"DDS backends used: {summary}")

    fallback_ok = sum(ok_by_backend.values())
    for backend in sorted(failed_by_backend, key=str.lower):
        failed_keys = failed_keys_by_backend.get(backend, set())
        recovered = sum(1 for key in failed_keys if key in ok_keys)
        if recovered:
            print(f"Warning: {backend} failed for {len(failed_keys)} file(s), fallback backend used for {recovered} file(s)")
        else:
            print(f"Warning: {backend} failed for {len(failed_keys)} file(s)")

def _collect_tex_source_export_requests(context, settings, target_root: str):
    from .nh_collider_exp import (_build_expected_texture_pair, _build_material_candidates, _is_placeholder_material_name, _is_valid_texture_candidate, _log_rejected_texture_candidate, _pick_best_db_match, _split_texture_candidate_base, _strip_tex_export_suffixes, _tex_export_rel_dir_from_path, _texture_category_folder_from_base, _unique_ci)
    obj, _ = _resolve_tex_target_object(context, settings.picked_object)
    if obj is None:
        return None, []

    db_map, _ = _build_db_map(settings)
    requests = {}
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue

        candidates = _build_material_candidates(mat)
        if not candidates:
            if _is_placeholder_material_name(getattr(mat, "name", "")):
                print(f"Skipped placeholder material: {mat.name}")
            continue

        match = _pick_best_db_match(candidates, db_map)
        paa_path, rvmat_path, used_base, _, _ = _build_expected_texture_pair(settings, candidates, match)
        texture_base, material_base = _split_texture_candidate_base(used_base)
        material_base = _strip_tex_export_suffixes(material_base or texture_base or used_base)
        if not material_base or not _is_valid_texture_candidate(material_base):
            _log_rejected_texture_candidate(material_base or used_base or getattr(mat, "name", ""))
            continue

        key = material_base.lower()
        rel_dir = (
            _tex_export_rel_dir_from_path(paa_path, target_root)
            or _tex_export_rel_dir_from_path(rvmat_path, target_root)
            or _texture_category_folder_from_base(material_base)
        )
        item = requests.setdefault(key, {
            "material_base": material_base,
            "material_names": [],
            "candidates": [],
            "expected_rel_dir": rel_dir,
        })
        item["material_names"].append(mat.name)
        item["candidates"].extend(candidates)
        if not item["expected_rel_dir"] and rel_dir:
            item["expected_rel_dir"] = rel_dir

    for item in requests.values():
        item["material_names"] = _unique_ci(item["material_names"])
        item["candidates"] = _unique_ci(item["candidates"])
    return obj, list(requests.values())

def _auto_select_object_base_color_paths(obj, settings) -> int:
    from .nh_base import (_fmt_exc)
    from .nh_collider_exp import (_build_expected_texture_pair, _build_material_candidates, _pick_best_db_match)
    """Apply the best available _ca/_co Base Color paths after a DB refresh."""
    if obj is None:
        return 0

    db_map, _ = _build_db_map(settings)
    changed = 0
    for slot in getattr(obj, "material_slots", []) or []:
        mat = getattr(slot, "material", None)
        if mat is None:
            continue

        candidates = _build_material_candidates(mat)
        match = _pick_best_db_match(candidates, db_map) if candidates else None
        if not match or not match.get("paa"):
            continue

        paa_path, rvmat_path, _, _, _ = _build_expected_texture_pair(settings, candidates, match)
        current_paa, current_rvmat = _get_p3d_material_paths(mat)
        paa_to_set = paa_path or current_paa
        rvmat_to_set = rvmat_path or current_rvmat
        if (paa_to_set or "").lower() == (current_paa or "").lower() and (
            (rvmat_to_set or "").lower() == (current_rvmat or "").lower()
        ):
            continue

        try:
            _set_p3d_material_paths(mat, paa_to_set, rvmat_to_set)
            changed += 1
        except Exception as e:
            print(f"Base Color auto-select failed for {mat.name}: {_fmt_exc(e)}")
    return changed

def _walk_folder_build_db(folder_abs: str):
    from .nh_collider_exp import (_ALLOWED_DB_EXTS, _is_texture_converter_test_output, _norm_path)
    if not os.path.isdir(folder_abs):
        raise RuntimeError(f"Folder not found: {folder_abs}")

    folder_abs = os.path.normpath(folder_abs)
    root_name = os.path.basename(folder_abs.rstrip("\\/")) or folder_abs

    buckets = {}
    for root, _, files in os.walk(folder_abs):
        for fn in files:
            full = os.path.join(root, fn)
            ext = os.path.splitext(full)[1].lower()
            if ext not in _ALLOWED_DB_EXTS:
                continue
            if _is_texture_converter_test_output(fn):
                continue
            base = os.path.basename(full)
            key = base.lower()
            buckets.setdefault(key, set()).add(os.path.normpath(full))

    entries = []
    for key, pathset in buckets.items():
        uniq = sorted(pathset)
        chosen = uniq[0]
        rel = os.path.relpath(chosen, folder_abs)
        entries.append({
            "basename": os.path.basename(chosen),
            "abs_path": _norm_path(chosen),
            "rel_path": _norm_path(os.path.join(root_name, rel)),
            "is_problem": (len(uniq) > 1),
            "dup_count": len(uniq),
        })

    entries.sort(key=lambda d: (d["basename"].lower(), d["rel_path"].lower()))
    return entries

def _collect_object_image_materials(obj, out_collection):
    out_collection.clear()
    if not obj or obj.type != "MESH":
        return 0

    mats_done = set()
    count = 0
    for slot in obj.material_slots:
        mat = slot.material
        if not mat or mat in mats_done:
            continue
        mats_done.add(mat)

        if not mat.use_nodes or not mat.node_tree:
            continue

        images = []
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and getattr(node, "image", None):
                images.append(node.image.name)

        if images:
            it = out_collection.add()
            it.mat_name = mat.name
            it.images_csv = ", ".join(sorted(set(images), key=lambda x: x.lower()))
            count += 1
    return count

def _build_db_map(settings):
    from .nh_collider_exp import (_is_texture_converter_test_output)
    db_map = {}
    dup_names = set()
    for it in settings.db_items:
        k = (it.basename or "").lower().strip()
        if not k:
            continue
        if _is_texture_converter_test_output(k):
            continue
        if it.is_problem:
            dup_names.add(k)
        db_map[k] = it.abs_path
    return db_map, dup_names

def _iter_descendants(root_obj):
    stack = list(root_obj.children)
    while stack:
        obj = stack.pop()
        yield obj
        stack.extend(obj.children)

def _obj_depth(obj):
    d = 0
    p = obj.parent
    while p is not None:
        d += 1
        p = p.parent
    return d

_HELPER_OBJ_PREFIXES = (
    "sector",
    "sectors",
    "selector",
    "selectors",
    "hierarchy",
    "hierarhy",
    "hierarrhy",
    "hierrarhy",
)

_ROOT_COLLECTION_NAME = "Collection"
_FIX_TARGET_COLLECTION_BASENAME = "NH_Fix_Result"

def _is_helper_object_name(name: str) -> bool:
    n = (name or "").strip().lower()
    return n.startswith(_HELPER_OBJ_PREFIXES) or n.startswith("hier")

def _link_object_to_collection(obj, collection):
    if obj is None or collection is None:
        return
    if not _collection_directly_contains_object(collection, obj):
        collection.objects.link(obj)

def _collection_directly_contains_object(collection, obj):
    if collection is None or obj is None:
        return False
    try:
        obj_ptr = obj.as_pointer()
    except Exception:
        obj_ptr = None
    for item in getattr(collection, "objects", []):
        if item == obj:
            return True
        if obj_ptr is not None:
            try:
                if item.as_pointer() == obj_ptr:
                    return True
            except Exception:
                pass
    return False

def _same_id_data(left, right):
    if left is None or right is None:
        return False
    if left == right:
        return True
    try:
        return left.as_pointer() == right.as_pointer()
    except Exception:
        return False

def _unlink_object_from_collection_tree(obj, root_collection, keep_collection=None):
    if obj is None or root_collection is None:
        return 0

    removed = 0
    for col in _iter_collection_tree(root_collection):
        if _same_id_data(col, keep_collection):
            continue
        if not _collection_directly_contains_object(col, obj):
            continue
        try:
            col.objects.unlink(obj)
            removed += 1
        except Exception:
            pass
    return removed

def _direct_object_collection_names_under_root(root_collection, obj):
    if root_collection is None or obj is None:
        return []
    names = []
    for col in _iter_collection_tree(root_collection):
        if _collection_directly_contains_object(col, obj):
            names.append(getattr(col, "name", "") or "<unnamed>")
    return names

def _move_object_to_collection(obj, target_collection, unlink_roots=None):
    if obj is None or target_collection is None:
        return
    _link_object_to_collection(obj, target_collection)

    unlink_cols = []
    seen = set()
    unlink_root_list = ()

    def _add_unlink_col(col):
        if col is None:
            return
        try:
            ptr = col.as_pointer()
        except Exception:
            ptr = id(col)
        if ptr in seen:
            return
        seen.add(ptr)
        unlink_cols.append(col)

    for col in list(getattr(obj, "users_collection", [])):
        _add_unlink_col(col)

    if unlink_roots is not None:
        unlink_root_list = unlink_roots if isinstance(unlink_roots, (tuple, list, set)) else (unlink_roots,)
        for root in unlink_root_list:
            if root is None:
                continue
            for col in _iter_collection_tree(root):
                if _collection_directly_contains_object(col, obj):
                    _add_unlink_col(col)

    try:
        target_ptr = target_collection.as_pointer()
    except Exception:
        target_ptr = None

    for col in unlink_cols:
        same_as_target = (col == target_collection)
        if not same_as_target and target_ptr is not None:
            try:
                same_as_target = (col.as_pointer() == target_ptr)
            except Exception:
                same_as_target = False
        if same_as_target:
            continue
        try:
            col.objects.unlink(obj)
        except Exception:
            pass

    for root in unlink_root_list:
        _unlink_object_from_collection_tree(obj, root, keep_collection=target_collection)

    if not _collection_directly_contains_object(target_collection, obj):
        _link_object_to_collection(obj, target_collection)

def _scene_fix_collection_name(scene):
    scene_name = (getattr(scene, "name", "") or "").strip()
    if not scene_name:
        return _FIX_TARGET_COLLECTION_BASENAME
    safe = re.sub(r"[\\/:*?\"<>|]+", "_", scene_name)
    return f"{_FIX_TARGET_COLLECTION_BASENAME}_{safe}"

def _ensure_target_collection(context, mesh_obj):
    scene_root = context.scene.collection
    target_name = _scene_fix_collection_name(context.scene)

    target = scene_root.children.get(target_name)
    if target is None:
        target = bpy.data.collections.new(target_name)
        scene_root.children.link(target)
    return target

def _ensure_default_scene_collection(context):
    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    if scene_root is None:
        return None

    target = scene_root.children.get(_ROOT_COLLECTION_NAME)
    if target is None:
        target = bpy.data.collections.new(_ROOT_COLLECTION_NAME)
        scene_root.children.link(target)
    return target

def _sanitize_repair_p3d_collection_name(value: str) -> str:
    raw = os.path.basename((value or "").replace("\\", "/")).strip()
    raw = _strip_blender_numeric_suffix(raw)
    if raw.lower().endswith(".blend"):
        raw = raw[:-6]
    raw = _INVALID_FILENAME_CHARS_RE.sub("_", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" .")
    if not raw:
        return ""
    if not raw.lower().endswith(".p3d"):
        raw += ".p3d"
    return raw

def _is_repair_generic_collection_name(name: str) -> bool:
    from .nh_scatter import (_COLLIDER_COLLECTION_NAME, _MEMORY_COLLECTION_NAME, _MISC_COLLECTION_NAME, _VISUALS_COLLECTION_NAME)
    from .nh_snap import (_logical_collection_name)
    logical = _logical_collection_name(_strip_blender_numeric_suffix(name or ""))
    if not logical:
        return True
    generic_names = {
        _logical_collection_name(_ROOT_COLLECTION_NAME),
        _logical_collection_name(_VISUALS_COLLECTION_NAME),
        _logical_collection_name(_COLLIDER_COLLECTION_NAME),
        _logical_collection_name(_MEMORY_COLLECTION_NAME),
        _logical_collection_name(_MISC_COLLECTION_NAME),
        "geometry",
        "memory",
    }
    return logical in generic_names or _is_helper_object_name(logical)

def _is_repair_lod_like_object_name(name: str) -> bool:
    from .nh_scatter import (_COLLIDER_KNOWN_LOD_NAMES)
    from .nh_snap import (_MemoryLodManager, _logical_collection_name)
    logical = _logical_collection_name(_strip_blender_numeric_suffix(name or ""))
    if not logical:
        return True
    if logical.startswith("resolution"):
        return True
    known = {_logical_collection_name(value) for value in _COLLIDER_KNOWN_LOD_NAMES.values()}
    known.add(_logical_collection_name(_MemoryLodManager.OBJECT_NAME))
    return logical in known

def _repair_scope_source_path(scope_objs):
    from .nh_collider_exp import (_norm_path)
    for obj in scope_objs or []:
        if obj is None:
            continue
        try:
            src = obj.get(_IE_SOURCE_PATH_KEY)
        except Exception:
            src = ""
        if isinstance(src, str) and src.strip():
            return _norm_path(bpy.path.abspath(src))

        for col in getattr(obj, "users_collection", []):
            try:
                src = col.get(_IE_SOURCE_PATH_KEY)
            except Exception:
                src = ""
            if isinstance(src, str) and src.strip():
                return _norm_path(bpy.path.abspath(src))
    return ""

def _repair_top_collection_candidate(context, scope_objs):
    from .nh_snap import (_logical_collection_name)
    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    if scene_root is None:
        return ""

    for obj in scope_objs or []:
        if obj is None:
            continue
        for col in getattr(obj, "users_collection", []):
            try:
                path = _find_collection_path(scene_root, col.as_pointer())
            except Exception:
                path = None
            if not path or len(path) < 2:
                continue

            candidate = None
            if _logical_collection_name(getattr(path[1], "name", "")) == _logical_collection_name(_ROOT_COLLECTION_NAME):
                if len(path) >= 3:
                    candidate = path[2]
            else:
                candidate = path[1]
            if candidate is None:
                continue

            name = getattr(candidate, "name", "") or ""
            if _looks_like_p3d_collection_name(name) or not _is_repair_generic_collection_name(name):
                return name
    return ""

def _derive_repair_p3d_collection_name(context, target_obj, scope_objs):
    source_path = _repair_scope_source_path(scope_objs)
    from_source = _sanitize_repair_p3d_collection_name(source_path)
    if from_source:
        return from_source, source_path

    from_collection = _sanitize_repair_p3d_collection_name(_repair_top_collection_candidate(context, scope_objs))
    if from_collection:
        return from_collection, ""

    blend_path = getattr(bpy.data, "filepath", "") or ""
    from_blend = _sanitize_repair_p3d_collection_name(blend_path)
    if from_blend and _normalize_p3d_lookup_key(from_blend) not in {"untitled", "startup"}:
        return from_blend, ""

    obj_name = getattr(target_obj, "name", "") or ""
    if obj_name and not _is_repair_lod_like_object_name(obj_name):
        from_object = _sanitize_repair_p3d_collection_name(obj_name)
        if from_object:
            return from_object, ""

    return "fixed_model.p3d", ""

def _ensure_repair_p3d_root_collection(context, target_obj, scope_objs):
    existing_root = _find_p3d_root_collection_for_object(context, target_obj)
    if existing_root is not None:
        return existing_root, _resolve_collection_source_path(existing_root)

    root_name, source_path = _derive_repair_p3d_collection_name(context, target_obj, scope_objs)
    parent = _ensure_default_scene_collection(context)
    if parent is None:
        raise RuntimeError("Scene collection is not available")

    target = parent.children.get(root_name)
    if target is None:
        target = bpy.data.collections.new(root_name)
        parent.children.link(target)

    if source_path:
        _set_ie_source_path_tag(target, source_path)
    return target, source_path

def _set_resolution0_p3d_lod_props(obj):
    from .nh_snap import (_remove_p3d_named_property)
    if obj is None or obj.type != "MESH":
        raise RuntimeError("Repair result must be a mesh")
    if not hasattr(obj, "a3ob_properties_object"):
        raise RuntimeError("P3D object properties are missing. Enable Arma 3 Object Builder first.")

    props = obj.a3ob_properties_object
    props.lod = "0"
    props.resolution = 0
    props.resolution_float = 0.0
    props.is_a3_lod = True
    _remove_p3d_named_property(props, "autocenter")

    try:
        lod_name = props.get_name()
    except Exception:
        lod_name = "Resolution 0"
    lod_name = lod_name or "Resolution 0"
    obj.name = lod_name
    if obj.data is not None:
        obj.data.name = lod_name
    return lod_name


def _apply_fix_mesh_resolution0_lod_props(obj):
    from .nh_base import (_fmt_exc)
    from .nh_snap import (_remove_p3d_named_property)
    """Set P3D LOD Properties to Is P3D LOD / Resolution / Index 0 for Fix Mesh output.

    This helper intentionally does not rename the object. Fix Mesh/Hierarchy should keep the
    repaired asset name while enabling the Object Builder LOD flags needed for P3D export.
    """
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False, "target is not a mesh"

    if not hasattr(obj, "a3ob_properties_object"):
        return False, "P3D object properties are missing"

    try:
        props = obj.a3ob_properties_object
        props.is_a3_lod = True
        props.lod = "0"
        props.resolution = 0
        props.resolution_float = 0.0
        _remove_p3d_named_property(props, "autocenter")
        return True, "Resolution 0"
    except Exception as e:
        return False, _fmt_exc(e)

def _remove_empty_subcollections(collection):
    if collection is None:
        return 0

    removed = 0
    for child in list(collection.children):
        removed += _remove_empty_subcollections(child)
        if len(child.objects) > 0 or len(child.children) > 0:
            continue
        try:
            collection.children.unlink(child)
        except Exception:
            continue
        if int(getattr(child, "users", 0) or 0) == 0:
            try:
                bpy.data.collections.remove(child)
            except Exception:
                pass
        removed += 1
    return removed

def _collect_fix_scope(context, target_obj):
    ordered = []
    seen = set()

    def _push(o):
        if o is None:
            return
        key = o.name
        if key in seen:
            return
        seen.add(key)
        ordered.append(o)

    selected = list(context.selected_objects)
    if selected:
        for o in selected:
            _push(o)
            for ch in _iter_descendants(o):
                _push(ch)
        return ordered, "selected"

    root = target_obj
    while root is not None and root.parent is not None:
        root = root.parent
    if root is None:
        return [target_obj], "target-only"

    _push(root)
    for ch in _iter_descendants(root):
        _push(ch)
    if root == target_obj:
        return ordered, "target-descendants"
    return ordered, "root-branch"

def _collect_repair_p3d_scope(context, target_obj):
    from .nh_snap import (_logical_collection_name)
    selected = [obj for obj in getattr(context, "selected_objects", []) if obj is not None]
    if len(selected) > 1:
        return _collect_fix_scope(context, target_obj)

    p3d_root = _find_p3d_root_collection_for_object(context, target_obj)
    if p3d_root is not None:
        objects = _collect_collection_objects_recursive(p3d_root)
        if objects:
            return objects, ".p3d root collection"

    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    for col in getattr(target_obj, "users_collection", []):
        if scene_root is not None:
            try:
                path = _find_collection_path(scene_root, col.as_pointer())
            except Exception:
                path = None
            if path and len(path) >= 2:
                candidate = col
                if (
                    _logical_collection_name(getattr(path[1], "name", ""))
                    == _logical_collection_name(_ROOT_COLLECTION_NAME)
                    and len(path) >= 3
                ):
                    candidate = path[2]
                elif len(path) >= 2:
                    candidate = path[1]

                objects = _collect_collection_objects_recursive(candidate)
                if objects:
                    return objects, f"collection: {candidate.name}"

        objects = _collect_collection_objects_recursive(col)
        if objects:
            return objects, f"collection: {col.name}"

    return _collect_fix_scope(context, target_obj)

def _largest_mesh(objs):
    meshes = [o for o in objs if o is not None and o.type == "MESH" and o.data is not None]
    if not meshes:
        return None
    return max(meshes, key=lambda o: len(o.data.polygons) if o.data else 0)

def _meshes_in_branch(seed_obj):
    if seed_obj is None:
        return []

    root = seed_obj
    while root.parent is not None:
        root = root.parent

    branch = [root]
    branch.extend(_iter_descendants(root))
    return [o for o in branch if o.type == "MESH" and o.data is not None]

def _resolve_fix_target_object(context, picked_obj):
    selected = list(context.selected_objects)

    if selected:
        active = context.view_layer.objects.active
        if active is not None:
            if active.type == "MESH":
                return active, "active"
            branch_meshes = _meshes_in_branch(active)
            mesh = _largest_mesh(branch_meshes)
            if mesh is not None:
                return mesh, "active-branch"

        selected_mesh = _largest_mesh(selected)
        if selected_mesh is not None:
            return selected_mesh, "selected"

        selected_branch_meshes = []
        for o in selected:
            selected_branch_meshes.extend(_meshes_in_branch(o))
        mesh = _largest_mesh(selected_branch_meshes)
        if mesh is not None:
            return mesh, "selected-branch"

    active = context.view_layer.objects.active
    if active is not None:
        if active.type == "MESH":
            return active, "active"
        branch_meshes = _meshes_in_branch(active)
        mesh = _largest_mesh(branch_meshes)
        if mesh is not None:
            return mesh, "active-branch"

    if picked_obj is not None:
        if picked_obj.type == "MESH":
            return picked_obj, "picked"
        branch_meshes = _meshes_in_branch(picked_obj)
        mesh = _largest_mesh(branch_meshes)
        if mesh is not None:
            return mesh, "picked-branch"

    return _resolve_tex_target_object(context, picked_obj)

def _collect_collections_deep(collection):
    if collection is None:
        return []

    out = []
    seen = set()
    stack = [collection]
    while stack:
        col = stack.pop()
        if col is None:
            continue
        key = col.as_pointer()
        if key in seen:
            continue
        seen.add(key)
        out.append(col)
        stack.extend(col.children)
    return out

def _resolve_tex_target_object(context, picked_obj):
    if picked_obj is not None and picked_obj.type == "MESH":
        return picked_obj, "picked"

    active = context.view_layer.objects.active
    if active is not None and active.type == "MESH":
        return active, "active"

    selected_meshes = [o for o in context.selected_objects if o.type == "MESH"]
    if len(selected_meshes) == 1:
        return selected_meshes[0], "selected"

    scene_meshes = [o for o in context.scene.objects if o.type == "MESH"]
    if not scene_meshes:
        return None, "none"
    if len(scene_meshes) == 1:
        return scene_meshes[0], "scene-single"

    best = max(scene_meshes, key=lambda o: len(o.data.polygons) if o.data else 0)
    return best, "scene-largest"

def _ensure_flat_collection_mesh(context, mesh_obj):
    target_collection = _ensure_target_collection(context, mesh_obj)
    mesh_world = mesh_obj.matrix_world.copy()

    _move_object_to_collection(mesh_obj, target_collection)
    mesh_obj.parent = None
    mesh_obj.matrix_world = mesh_world
    return target_collection, mesh_obj

def _purge_collection_tree(collection):
    deleted_objects = 0
    deleted_collections = 0

    for ch_idx, ch in enumerate(list(collection.children), start=1):
        child_deleted_objects, child_deleted_collections = _purge_collection_tree(ch)
        deleted_objects += child_deleted_objects
        deleted_collections += child_deleted_collections

        try:
            collection.children.unlink(ch)
        except Exception:
            pass
        else:
            if int(getattr(ch, "users", 0) or 0) == 0:
                try:
                    bpy.data.collections.remove(ch)
                except Exception:
                    pass
            deleted_collections += 1
        if ch_idx % 25 == 0:
            _ui_yield()

    for obj_idx, obj in enumerate(list(collection.objects), start=1):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            continue
        deleted_objects += 1
        if obj_idx % 50 == 0:
            _ui_yield()

    return deleted_objects, deleted_collections

def _cleanup_target_collection_keep_mesh(target_collection, keep_obj):
    deleted_objects = 0
    deleted_collections = 0

    for obj_idx, obj in enumerate(list(target_collection.objects), start=1):
        if obj == keep_obj:
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            continue
        deleted_objects += 1
        if obj_idx % 50 == 0:
            _ui_yield()

    for ch_idx, ch in enumerate(list(target_collection.children), start=1):
        child_deleted_objects, child_deleted_collections = _purge_collection_tree(ch)
        deleted_objects += child_deleted_objects
        deleted_collections += child_deleted_collections

        try:
            target_collection.children.unlink(ch)
        except Exception:
            continue

        if int(getattr(ch, "users", 0) or 0) == 0:
            try:
                bpy.data.collections.remove(ch)
            except Exception:
                pass
        deleted_collections += 1
        if ch_idx % 25 == 0:
            _ui_yield()

    return deleted_objects, deleted_collections

def _unlink_collection_from_scene_parents(scene, col):
    if scene is None or col is None:
        return
    scene_cols = _collect_collections_deep(scene.collection)
    for parent in scene_cols:
        if parent == col:
            continue
        try:
            if any(ch == col for ch in parent.children):
                parent.children.unlink(col)
        except Exception:
            pass

def _force_remove_object(obj, keep_obj=None, allowed_col_ptrs=None):
    if obj is None or obj == keep_obj:
        return False

    cols = list(getattr(obj, "users_collection", []))
    if allowed_col_ptrs is not None:
        for col in cols:
            if col.as_pointer() not in allowed_col_ptrs:
                # Object is shared with another scene/collection tree. Keep it safe.
                return False

    try:
        if keep_obj is not None and keep_obj.parent == obj:
            keep_obj.parent = None
        for ch in list(obj.children):
            if ch == keep_obj:
                ch.parent = None
        obj.parent = None
    except Exception:
        pass

    for col in cols:
        if allowed_col_ptrs is not None and col.as_pointer() not in allowed_col_ptrs:
            continue
        try:
            col.objects.unlink(obj)
        except Exception:
            pass

    try:
        bpy.data.objects.remove(obj, do_unlink=True)
        return True
    except Exception:
        return False

def _remove_helper_named_objects(scene=None, keep_obj=None, max_passes=8):
    if scene is None:
        scene = bpy.context.scene if bpy.context is not None else None
    if scene is None:
        return 0, 0, []

    scene_cols = _collect_collections_deep(scene.collection)
    scene_col_ptrs = {c.as_pointer() for c in scene_cols if c is not None}

    deleted_objects = 0
    deleted_collections = 0

    for pass_idx in range(max_passes):
        deleted_pass = 0

        helpers = [
            o for o in scene.objects
            if o is not None and o != keep_obj and _is_helper_object_name(o.name)
        ]
        helpers.sort(key=_obj_depth, reverse=True)
        for obj_idx, helper in enumerate(helpers, start=1):
            live = helper
            if live is None or live == keep_obj:
                continue
            try:
                live_name = live.name
            except ReferenceError:
                continue
            if not _is_helper_object_name(live_name):
                continue
            if _force_remove_object(live, keep_obj=keep_obj, allowed_col_ptrs=scene_col_ptrs):
                deleted_objects += 1
                deleted_pass += 1
            if obj_idx % 50 == 0:
                _ui_yield()

        helper_cols = [
            c for c in scene_cols
            if c is not None and c != scene.collection and _is_helper_object_name(c.name)
        ]
        # Remove deeper sub-collections first.
        helper_cols.sort(key=lambda c: len(getattr(c, "children_recursive", [])), reverse=True)
        for col_idx, col in enumerate(helper_cols, start=1):
            live_col = col
            if live_col is None:
                continue
            try:
                live_col_name = live_col.name
            except ReferenceError:
                continue
            if not _is_helper_object_name(live_col_name):
                continue

            for obj in list(live_col.objects):
                if _force_remove_object(obj, keep_obj=keep_obj, allowed_col_ptrs=scene_col_ptrs):
                    deleted_objects += 1
                    deleted_pass += 1

            for ch in list(live_col.children):
                try:
                    live_col.children.unlink(ch)
                except Exception:
                    pass
                if int(getattr(ch, "users", 0) or 0) == 0:
                    try:
                        bpy.data.collections.remove(ch)
                    except Exception:
                        pass

            _unlink_collection_from_scene_parents(scene, live_col)
            if int(getattr(live_col, "users", 0) or 0) == 0:
                try:
                    bpy.data.collections.remove(live_col)
                    deleted_collections += 1
                    deleted_pass += 1
                except Exception:
                    pass
            if col_idx % 25 == 0:
                _ui_yield()

        if deleted_pass == 0:
            break
        if pass_idx % 2 == 1:
            _ui_yield()

    remaining_helpers = [
        o.name for o in scene.objects
        if o is not None and o != keep_obj and _is_helper_object_name(o.name)
    ]
    return deleted_objects, deleted_collections, remaining_helpers

def _ui_yield():
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except Exception:
        pass

def _ensure_object_visible_for_ops(obj):
    if obj is None:
        return
    try:
        obj.hide_set(False)
    except Exception:
        pass
    try:
        obj.hide_viewport = False
    except Exception:
        pass

def _join_meshes_in_batches(context, anchor_obj, mesh_names, batch_size=24):
    if anchor_obj is None or anchor_obj.type != "MESH" or anchor_obj.data is None:
        raise RuntimeError("Join anchor must be a mesh object")

    batch_size = int(batch_size)
    batch_limit = None if batch_size <= 1 else max(2, batch_size)
    anchor_name = anchor_obj.name
    pending = [n for n in mesh_names if n and n != anchor_name]
    joined_count = 0
    join_passes = 0

    while pending:
        anchor = bpy.data.objects.get(anchor_name)
        if anchor is None or anchor.type != "MESH" or anchor.data is None:
            raise RuntimeError("Join failed: anchor mesh became unavailable")

        batch_names = []
        next_pending = []
        for nm in pending:
            live = bpy.data.objects.get(nm)
            if live is None or live == anchor:
                continue
            if live.type != "MESH" or live.data is None or len(live.data.polygons) == 0:
                continue
            if batch_limit is None or len(batch_names) < batch_limit:
                batch_names.append(nm)
            else:
                next_pending.append(nm)
        pending = next_pending

        if not batch_names:
            break

        bpy.ops.object.select_all(action="DESELECT")
        _ensure_object_visible_for_ops(anchor)
        anchor.select_set(True)
        selected_for_join = [anchor]

        for nm in batch_names:
            live = bpy.data.objects.get(nm)
            if live is None or live == anchor:
                continue
            _ensure_object_visible_for_ops(live)
            live.select_set(True)
            selected_for_join.append(live)

        if len(selected_for_join) <= 1:
            continue

        context.view_layer.objects.active = anchor
        bpy.ops.object.join()
        joined_count += len(selected_for_join) - 1
        join_passes += 1

        active_after = context.view_layer.objects.active
        if active_after is not None and active_after.type == "MESH":
            anchor_name = active_after.name

        _ui_yield()

    merged_obj = bpy.data.objects.get(anchor_name)
    if merged_obj is None or merged_obj.type != "MESH":
        raise RuntimeError("Join failed: no merged mesh after staged join")
    return merged_obj, joined_count, join_passes

def _center_object_bbox_to_world_origin(obj):
    if obj is None:
        return False, Vector((0.0, 0.0, 0.0))

    bbox_world = []
    try:
        for corner in obj.bound_box:
            bbox_world.append(obj.matrix_world @ Vector(corner))
    except Exception:
        bbox_world = []

    if bbox_world:
        center = Vector((0.0, 0.0, 0.0))
        for p in bbox_world:
            center += p
        center /= len(bbox_world)
    else:
        center = obj.matrix_world.translation.copy()

    if center.length <= 1e-7:
        return False, center

    mw = obj.matrix_world.copy()
    mw.translation = mw.translation - center
    obj.matrix_world = mw
    return True, center

def _set_object_origin_to_geometry(obj):
    if obj is None or getattr(obj, "type", None) != "MESH" or obj.data is None:
        return False, Vector((0.0, 0.0, 0.0))

    local_points = []
    try:
        local_points = [Vector(corner) for corner in obj.bound_box]
    except Exception:
        local_points = []

    if not local_points and getattr(obj.data, "vertices", None):
        local_points = [vert.co.copy() for vert in obj.data.vertices]

    if not local_points:
        return False, obj.matrix_world.translation.copy()

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
    local_center = (min_v + max_v) * 0.5
    world_center = obj.matrix_world @ local_center

    if local_center.length <= 1e-7:
        return False, world_center

    shift = Matrix.Translation(-local_center)
    try:
        obj.data.transform(shift, shape_keys=True)
    except TypeError:
        obj.data.transform(shift)
    obj.data.update()

    obj.matrix_world = obj.matrix_world @ Matrix.Translation(local_center)
    return True, world_center

# ---------- P3D material setter (FIXED) ----------

def _find_p3d_material_pg(mat: bpy.types.Material):
    if mat is None:
        return None
    for attr in dir(mat):
        if not attr.startswith("a3ob"):
            continue
        try:
            pg = getattr(mat, attr)
        except Exception:
            continue
        if hasattr(pg, "bl_rna"):
            return pg
    return None

def _p3d_props(mat_pg):
    props = []
    for p in mat_pg.bl_rna.properties:
        if p.identifier == "rna_type":
            continue
        # p.type is Blender RNA type label (STRING, ENUM, ...)
        props.append({
            "ui": p.name,
            "id": p.identifier,
            "type": p.type,
        })
    return props

def _pick_enum_id(props, keywords):
    for pr in props:
        if pr["type"] != "ENUM":
            continue
        ui_l = pr["ui"].lower()
        if all(k in ui_l for k in keywords):
            return pr["id"]
    return None

def _pick_string_id(props, keywords):
    for pr in props:
        if pr["type"] != "STRING":
            continue
        ui_l = pr["ui"].lower()
        if all(k in ui_l for k in keywords):
            return pr["id"]
    return None

def _get_p3d_material_paths(mat: bpy.types.Material):
    from .nh_collider_exp import (_norm_path)
    pg = _find_p3d_material_pg(mat)
    if pg is None:
        return None, None

    props = _p3d_props(pg)
    paa_id = (
        _pick_string_id(props, ["paa"])
        or _pick_string_id(props, ["texture", "paa"])
        or _pick_string_id(props, ["texture"])
        or _pick_string_id(props, ["file"])
        or _pick_string_id(props, ["path"])
    )
    rvmat_id = (
        _pick_string_id(props, ["rvmat"])
        or _pick_string_id(props, ["rvm"])
        or _pick_string_id(props, ["material", "path"])
        or _pick_string_id(props, ["material"])
    )

    paa_path = ""
    rvmat_path = ""
    if paa_id and hasattr(pg, paa_id):
        try:
            paa_path = _norm_path(str(getattr(pg, paa_id, "") or "").strip())
        except Exception:
            paa_path = ""
    if rvmat_id and hasattr(pg, rvmat_id):
        try:
            rvmat_path = _norm_path(str(getattr(pg, rvmat_id, "") or "").strip())
        except Exception:
            rvmat_path = ""

    return paa_path or None, rvmat_path or None

def _get_material_first_image_path(mat: bpy.types.Material):
    from .nh_collider_exp import (_basename_no_ext, _norm_path)
    if mat is None or not getattr(mat, "use_nodes", False) or not getattr(mat, "node_tree", None):
        return None

    for node in mat.node_tree.nodes:
        if node.type != "TEX_IMAGE":
            continue
        image = getattr(node, "image", None)
        if image is None:
            continue

        raw_path = getattr(image, "filepath_raw", "") or getattr(image, "filepath", "")
        if raw_path:
            try:
                return _norm_path(bpy.path.abspath(raw_path))
            except Exception:
                return _norm_path(str(raw_path))

        image_name = _basename_no_ext(getattr(image, "name", ""))
        if image_name:
            return image_name

    return None

def _material_identity_value_usable(value) -> bool:
    from .nh_collider_exp import (_is_blender_install_texture_path_invalid, _is_invalid_windows_filename_component, _is_placeholder_material_name)
    raw = str(value or "").strip()
    if not raw:
        return False
    if _is_placeholder_material_name(raw):
        return False
    if _is_invalid_windows_filename_component(raw) or _is_blender_install_texture_path_invalid(raw):
        return False
    return True


def _source_material_export_paths(src_mat: bpy.types.Material):
    if src_mat is None:
        return None, None

    paa_path, rvmat_path = _get_p3d_material_paths(src_mat)
    paa_path = paa_path if _material_identity_value_usable(paa_path) else None
    rvmat_path = rvmat_path if _material_identity_value_usable(rvmat_path) else None

    if not paa_path:
        image_path = _get_material_first_image_path(src_mat)
        if _material_identity_value_usable(image_path):
            paa_path = image_path

    return paa_path, rvmat_path


def _material_identity_quality(mat: bpy.types.Material) -> int:
    if mat is None:
        return -1
    paa_path, rvmat_path = _get_p3d_material_paths(mat)
    if _material_identity_value_usable(paa_path):
        return 40
    if _material_identity_value_usable(rvmat_path):
        return 30
    if _material_identity_value_usable(_get_material_first_image_path(mat)):
        return 20
    if _material_identity_value_usable(getattr(mat, "name", "")):
        return 10
    return 0


def _sync_collider_material_identity_from_source(target_mat: bpy.types.Material, src_mat: bpy.types.Material):
    if target_mat is None:
        return

    paa_path, rvmat_path = _source_material_export_paths(src_mat)
    try:
        _set_p3d_material_paths(
            target_mat,
            paa_path,
            rvmat_path,
            clear_paa=not bool(paa_path),
            clear_rvmat=not bool(rvmat_path),
        )
    except Exception:
        pass


def _derive_roadway_material_name(src_mat: bpy.types.Material):
    from .nh_collider_exp import (_basename_no_ext)
    if src_mat is None:
        return "RoadwayMaterial"

    paa_path, rvmat_path = _source_material_export_paths(src_mat)
    candidates = [
        paa_path,
        rvmat_path,
        _get_material_first_image_path(src_mat),
        getattr(src_mat, "name", ""),
    ]

    for candidate in candidates:
        base = _basename_no_ext(candidate)
        if base:
            return base

    return "RoadwayMaterial"

def _find_material_slot_index_by_name_ci(materials, material_name):
    target_name = (material_name or "").strip().lower()
    if not target_name:
        return None

    for slot_idx, existing_mat in enumerate(materials):
        if existing_mat is None:
            continue
        if existing_mat.name.strip().lower() == target_name:
            return slot_idx
    return None

def _ensure_roadway_material(target_materials, src_mat: bpy.types.Material):
    material_name = _derive_roadway_material_name(src_mat)
    existing_index = _find_material_slot_index_by_name_ci(target_materials, material_name)
    if existing_index is not None:
        existing_mat = target_materials[existing_index]
        _sync_collider_material_identity_from_source(existing_mat, src_mat)
        return existing_index, existing_mat.name

    if src_mat is not None:
        roadway_mat = src_mat.copy()
    else:
        roadway_mat = bpy.data.materials.new(name=material_name)

    roadway_mat.name = material_name

    _sync_collider_material_identity_from_source(roadway_mat, src_mat)

    target_materials.append(roadway_mat)
    return len(target_materials) - 1, roadway_mat.name


def _source_material_from_index(source_obj, material_index):
    if source_obj is None or getattr(source_obj, "type", None) != "MESH":
        return None
    mesh = getattr(source_obj, "data", None)
    if mesh is None:
        return None
    materials = list(getattr(mesh, "materials", []) or [])
    if not materials:
        return None
    try:
        idx = int(material_index)
    except Exception:
        return None
    if 0 <= idx < len(materials):
        return materials[idx]
    return None


def _source_object_material_counts(source_obj):
    if source_obj is None or getattr(source_obj, "type", None) != "MESH":
        return {}
    mesh = getattr(source_obj, "data", None)
    if mesh is None:
        return {}

    counts = {}
    for poly in getattr(mesh, "polygons", []) or []:
        idx = int(getattr(poly, "material_index", 0) or 0)
        counts[idx] = counts.get(idx, 0) + 1

    if counts:
        return counts

    try:
        active_idx = int(getattr(source_obj, "active_material_index", 0) or 0)
    except Exception:
        active_idx = 0
    if _source_material_from_index(source_obj, active_idx) is not None:
        return {active_idx: 1}
    return {}


def _source_edit_selection_material_counts(source_obj):
    if source_obj is None or getattr(source_obj, "type", None) != "MESH":
        return {}
    if getattr(source_obj, "mode", "") != "EDIT":
        return {}

    try:
        bm = bmesh.from_edit_mesh(source_obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
    except Exception:
        return {}

    selected_verts = {vert for vert in bm.verts if vert.is_valid and vert.select}
    selected_edges = {edge for edge in bm.edges if edge.is_valid and edge.select}
    counts = {}

    for face in bm.faces:
        if face is None or not face.is_valid:
            continue
        touched = bool(face.select)
        if not touched and selected_edges:
            touched = any(edge in selected_edges for edge in face.edges if edge is not None and edge.is_valid)
        if not touched and selected_verts:
            touched = any(vert in selected_verts for vert in face.verts if vert is not None and vert.is_valid)
        if not touched:
            continue
        idx = int(getattr(face, "material_index", 0) or 0)
        counts[idx] = counts.get(idx, 0) + 1

    return counts


def _normalize_material_counts(material_counts):
    counts = {}
    if isinstance(material_counts, dict):
        items = material_counts.items()
    else:
        items = material_counts or ()
    for raw_idx, raw_count in items:
        try:
            idx = int(raw_idx)
            count = int(raw_count)
        except Exception:
            continue
        if count <= 0:
            continue
        counts[idx] = counts.get(idx, 0) + count
    return counts


def _add_source_material_candidates(candidates, source_obj, material_counts, *, order_base=0):
    counts = _normalize_material_counts(material_counts)
    for order, material_index in enumerate(sorted(counts.keys())):
        mat = _source_material_from_index(source_obj, material_index)
        if mat is None:
            continue
        candidates.append(
            {
                "material": mat,
                "count": max(1, int(counts.get(material_index, 1) or 1)),
                "order": int(order_base) + order,
            }
        )


def _choose_best_source_material(candidates):
    merged = {}
    for item in candidates or []:
        mat = item.get("material") if isinstance(item, dict) else None
        if mat is None:
            continue
        try:
            key = ("ptr", int(mat.as_pointer()))
        except Exception:
            key = ("name", str(getattr(mat, "name", "") or ""))
        rec = merged.get(key)
        if rec is None:
            merged[key] = {
                "material": mat,
                "count": max(1, int(item.get("count", 1) or 1)),
                "order": int(item.get("order", 0) or 0),
            }
        else:
            rec["count"] += max(1, int(item.get("count", 1) or 1))
            rec["order"] = min(int(rec.get("order", 0) or 0), int(item.get("order", 0) or 0))

    best = None
    best_score = None
    for rec in merged.values():
        mat = rec["material"]
        score = (
            _material_identity_quality(mat),
            int(rec.get("count", 0) or 0),
            -int(rec.get("order", 0) or 0),
        )
        if best_score is None or score > best_score:
            best = mat
            best_score = score
    return best


def _selected_source_material_for_placeholder(source_obj, material_counts=None):
    if source_obj is None or getattr(source_obj, "type", None) != "MESH":
        return None

    candidates = []
    if material_counts:
        _add_source_material_candidates(candidates, source_obj, material_counts)
    if not candidates:
        _add_source_material_candidates(candidates, source_obj, _source_edit_selection_material_counts(source_obj))
    if not candidates:
        _add_source_material_candidates(candidates, source_obj, _source_object_material_counts(source_obj))
    if not candidates:
        try:
            active_idx = int(getattr(source_obj, "active_material_index", 0) or 0)
        except Exception:
            active_idx = 0
        _add_source_material_candidates(candidates, source_obj, {active_idx: 1})

    chosen = _choose_best_source_material(candidates)
    if chosen is not None:
        return chosen

    for mat in list(getattr(source_obj.data, "materials", []) or []):
        if mat is not None:
            return mat
    return None


def _selected_source_material_from_data_items(source_obj, *, source_objects=None, data_items=None):
    candidates = []
    order = 0
    for item in data_items or []:
        if not isinstance(item, dict):
            continue
        item_source = item.get("source_obj") or item.get("source_object") or source_obj
        if item_source is None or getattr(item_source, "type", None) != "MESH":
            continue
        if "material_counts" in item:
            _add_source_material_candidates(candidates, item_source, item.get("material_counts"), order_base=order)
        elif "material_index" in item:
            _add_source_material_candidates(candidates, item_source, {item.get("material_index"): 1}, order_base=order)
        order += 1000

    if candidates:
        return _choose_best_source_material(candidates)

    source_candidates = []
    for idx, obj in enumerate(source_objects or []):
        mat = _selected_source_material_for_placeholder(obj)
        if mat is not None:
            source_candidates.append({"material": mat, "count": 1, "order": idx})
    if source_candidates:
        return _choose_best_source_material(source_candidates)

    return _selected_source_material_for_placeholder(source_obj)


def _ensure_collider_placeholder_material(target_obj, source_obj, *, source_objects=None, data_items=None):
    if target_obj is None or getattr(target_obj, "type", None) != "MESH" or target_obj.data is None:
        return None, ""
    src_mat = _selected_source_material_from_data_items(
        source_obj,
        source_objects=source_objects,
        data_items=data_items,
    )
    material_name = _derive_roadway_material_name(src_mat)
    existing_index = _find_material_slot_index_by_name_ci(target_obj.data.materials, material_name)
    if existing_index is not None:
        existing_mat = target_obj.data.materials[existing_index]
        _sync_collider_material_identity_from_source(existing_mat, src_mat)
        return existing_index, getattr(existing_mat, "name", material_name)
    existing_global = bpy.data.materials.get(material_name)
    if existing_global is not None:
        _sync_collider_material_identity_from_source(existing_global, src_mat)
        target_obj.data.materials.append(existing_global)
        return len(target_obj.data.materials) - 1, existing_global.name
    try:
        return _ensure_roadway_material(target_obj.data.materials, src_mat)
    except Exception:
        if src_mat is not None:
            for idx, mat in enumerate(target_obj.data.materials):
                if mat == src_mat:
                    return idx, getattr(mat, "name", "")
            target_obj.data.materials.append(src_mat)
            return len(target_obj.data.materials) - 1, getattr(src_mat, "name", "")
    return None, ""


def _set_p3d_material_paths(
    mat: bpy.types.Material,
    paa_abs: str | None,
    rvmat_abs: str | None,
    *,
    clear_paa: bool = False,
    clear_rvmat: bool = False,
):
    from .nh_collider_exp import (_is_blender_install_texture_path_invalid, _is_invalid_windows_filename_component, _is_placeholder_material_name, _norm_path)
    def clean_path(value, label):
        from .nh_collider_exp import (_is_blender_install_texture_path_invalid, _is_invalid_windows_filename_component, _is_placeholder_material_name, _norm_path)
        if value is None:
            return None
        raw = _norm_path(str(value or "").strip())
        if not raw:
            return None
        if _is_placeholder_material_name(raw):
            raise RuntimeError(f"{label} path is a placeholder, not a texture path: {raw}")
        if _is_invalid_windows_filename_component(raw) or _is_blender_install_texture_path_invalid(raw):
            print(f"Skipped invalid texture candidate: {raw}")
            raise RuntimeError(f"{label} path has an invalid filename component: {raw}")
        return raw

    paa_abs = clean_path(paa_abs, "PAA")
    rvmat_abs = clean_path(rvmat_abs, "RVMAT")

    pg = _find_p3d_material_pg(mat)
    if pg is None:
        raise RuntimeError("P3D material property group not found")

    props = _p3d_props(pg)

    # 1) Ensure source enum -> File (TEX)
    # UI name in your screenshot: "Texture Source"
    src_id = _pick_enum_id(props, ["texture", "source"]) or _pick_enum_id(props, ["source"])
    if src_id and hasattr(pg, src_id):
        try:
            setattr(pg, src_id, "TEX")
        except Exception:
            # ignore if enum differs; not fatal
            pass

    # 2) Find string fields for PAA and RVMAT
    # We try multiple keyword combinations to survive different P3D versions
    paa_id = (
        _pick_string_id(props, ["paa"])
        or _pick_string_id(props, ["texture", "paa"])
        or _pick_string_id(props, ["texture"])
        or _pick_string_id(props, ["file"])
        or _pick_string_id(props, ["path"])
    )

    rvmat_id = (
        _pick_string_id(props, ["rvmat"])
        or _pick_string_id(props, ["rvm"])
        or _pick_string_id(props, ["material", "path"])
        or _pick_string_id(props, ["material"])
    )

    # Hard requirement: if we want to set a value, field must exist
    if clear_paa and paa_id and hasattr(pg, paa_id):
        setattr(pg, paa_id, "")
    if clear_rvmat and rvmat_id and hasattr(pg, rvmat_id):
        setattr(pg, rvmat_id, "")

    if paa_abs is not None:
        if not paa_id or not hasattr(pg, paa_id):
            raise RuntimeError("PAA path field not found in P3D Material Properties")
        setattr(pg, paa_id, paa_abs)

    if rvmat_abs is not None:
        if not rvmat_id or not hasattr(pg, rvmat_id):
            raise RuntimeError("RVMAT path field not found in P3D Material Properties")
        setattr(pg, rvmat_id, rvmat_abs)

def _iter_unique_materials_from_objects(objects):
    materials = []
    seen = set()
    for obj in objects or []:
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        for slot in getattr(obj, "material_slots", []):
            mat = getattr(slot, "material", None)
            if mat is None:
                continue
            ptr = mat.as_pointer()
            if ptr in seen:
                continue
            seen.add(ptr)
            materials.append(mat)
    return materials

def _enable_preview_material_alpha(material: bpy.types.Material):
    try:
        material.blend_method = "HASHED"
    except Exception:
        pass

    try:
        material.shadow_method = "HASHED"
    except Exception:
        pass

def _disable_preview_material_alpha(material: bpy.types.Material):
    try:
        material.blend_method = "OPAQUE"
    except Exception:
        pass

    try:
        material.shadow_method = "OPAQUE"
    except Exception:
        pass

def _apply_image_color_space(image, color_space: str):
    if image is None:
        return

    if color_space == "DATA":
        try:
            image.colorspace_settings.is_data = True
        except Exception:
            try:
                image.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
    else:
        try:
            image.colorspace_settings.is_data = False
        except Exception:
            pass

def _has_image_alpha(image) -> bool:
    if image is None:
        return False

    try:
        if getattr(image, "alpha_mode", "NONE") != "NONE":
            return True
    except Exception:
        pass

    try:
        return int(getattr(image, "channels", 0) or 0) >= 4
    except Exception:
        return False

def _base_color_declared_has_alpha(path_or_name: str):
    from .nh_collider_exp import (_base_color_suffix)
    suffix = _base_color_suffix(path_or_name)
    if suffix == "_ca":
        return True
    if suffix == "_co":
        return False
    return None

def _remove_image_if_unused(image):
    if image is None:
        return

    try:
        if image.users == 0:
            bpy.data.images.remove(image)
    except Exception:
        pass

def _find_existing_paa_preview_image(filepath: str, color_space: str):
    filepath = os.path.abspath(bpy.path.abspath(filepath)).lower()
    is_data = color_space == "DATA"

    for image in bpy.data.images:
        image_path = getattr(image, "filepath_raw", "") or getattr(image, "filepath", "")
        if not image_path:
            continue

        try:
            image_path = os.path.abspath(bpy.path.abspath(image_path)).lower()
        except Exception:
            continue

        if image_path != filepath:
            continue

        try:
            image_is_data = bool(image.colorspace_settings.is_data)
        except Exception:
            image_is_data = False

        if image_is_data == is_data:
            return image

    return None

def _flatten_image_channels(mip, width: int, height: int, channels: int = 4):
    """Convert a PAA mip buffer to a flat row-major interleaved float list for image.pixels.

    Accepts flat interleaved buffers, row-major rows of raw bytes/ints, and rows of pixel
    tuples (RGBA/RGB), normalizing 0-255 values to 0-1 floats. Falls back to simple
    type-shape detection so images are never transposed or channel-garbled.
    """
    data = getattr(mip, "data", None)
    if data is None:
        width, height = int(width or 1), int(height or 1)
        return [0.0] * (width * height * channels)
    try:
        total = width * height * channels
        values = []

        def _norm_num(v):
            try:
                f = float(v)
            except Exception:
                return 0.0
            return f / 255.0 if f > 1.5 else f

        if isinstance(data, (bytes, bytearray)):
            raw = data
        else:
            try:
                seq = list(data)
            except Exception:
                seq = []
            if seq and not isinstance(seq[0], (bytes, bytearray)) and not isinstance(seq[0], (list, tuple)):
                raw = seq
            elif seq and len(seq) == height:
                flat = []
                for row in seq:
                    if isinstance(row, (bytes, bytearray)) or (row and not isinstance(row[0], (list, tuple, bytes, bytearray))):
                        flat.extend(row)
                    elif row:
                        sub = row[0]
                        if isinstance(sub, (list, tuple)):
                            for pix in row:
                                flat.extend(pix if isinstance(pix, (list, tuple)) else [pix])
                        else:
                            flat.extend([sub])
                raw = flat
            else:
                raw = seq

        if len(raw) == total:
            values = [_norm_num(v) for v in raw]
        elif len(raw) == width * height * 4 and channels == 3:
            values = []
            for i in range(width * height):
                v = raw[i * 4:i * 4 + 3]
                values.extend(_norm_num(x) for x in v)
        elif len(raw) == width * height:
            rows = raw
            values = []
            for i in range(width * height):
                pix = rows[i]
                pix = pix if isinstance(pix, (list, tuple, bytes, bytearray)) else [pix]
                chs = list(pix[:channels]) if isinstance(pix, (list, tuple)) else list(pix)
                chs = chs + [0] * max(0, channels - len(chs))
                values.extend(_norm_num(x) for x in chs[:channels])
        else:
            values = [_norm_num(v) for v in raw][:total]
        if len(values) < total:
            values = values + [0.0] * (total - len(values))
        return values[:total]
    except Exception:
        width, height = int(width or 1), int(height or 1)
        return [0.0] * (width * height * channels)


def _create_blender_image_from_paa_texture(filepath: str, tex, color_space: str):
    from .nh_snap import (_import_first_available_module)
    paa_ns = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.data_paa",
            "NH_bundle.io.data_paa",
        )
    )
    if paa_ns is None:
        return None

    paa_type = getattr(paa_ns, "PAA_Type", None)
    if paa_type is None:
        return None

    dxt1 = getattr(paa_type, "DXT1", None)
    dxt5 = getattr(paa_type, "DXT5", None)
    if tex.type not in (dxt1, dxt5):
        return None

    mip = tex.mips[0]
    mip.decompress(tex.type)
    swiztagg = tex.get_tagg("SWIZ")
    if swiztagg is not None:
        mip.swizzle(swiztagg.data)

    alpha = tex.type == dxt5
    img = bpy.data.images.new(
        os.path.basename(filepath),
        mip.width,
        mip.height,
        alpha=alpha,
        is_data=color_space == "DATA",
    )
    img.filepath_raw = filepath
    if alpha:
        img.alpha_mode = "PREMUL"
    else:
        img.alpha_mode = "NONE"

    _apply_image_color_space(img, color_space)

    img.pixels = _flatten_image_channels(mip, mip.width, mip.height, channels=4 if alpha else 3)
    img.update()
    img.pack()
    return img

def _load_paa_image_with_original_p3d(filepath: str, color_space: str = "SRGB", check_existing: bool = True):
    from .nh_snap import (_import_first_available_module)
    filepath = os.path.abspath(bpy.path.abspath(filepath))
    if check_existing:
        existing = _find_existing_paa_preview_image(filepath, color_space)
        if existing is not None:
            return existing, None

    paa_mod = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.data_paa",
            "NH_bundle.io.data_paa",
        )
    )
    if paa_mod is None:
        return None, None

    try:
        with open(filepath, "rb") as file:
            tex = paa_mod.PAA_File.read(file)
    except Exception:
        return None, None

    return _create_blender_image_from_paa_texture(filepath, tex, color_space), tex

def _ensure_p3d_import_paa_helpers():
    from .nh_snap import (_import_first_available_module)
    import_paa_mod = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.import_paa",
            "NH_bundle.io.import_paa",
        )
    )
    if import_paa_mod is None:
        return None

    if not callable(getattr(import_paa_mod, "find_existing_image", None)):
        setattr(import_paa_mod, "find_existing_image", _find_existing_paa_preview_image)

    if not callable(getattr(import_paa_mod, "create_image_from_texture", None)):
        def _module_create_image_from_texture(filepath, tex, color_space):
            return _create_blender_image_from_paa_texture(filepath, tex, color_space)
        setattr(import_paa_mod, "create_image_from_texture", _module_create_image_from_texture)

    if not callable(getattr(import_paa_mod, "load_file", None)):
        def _module_load_file(filepath, color_space="SRGB", check_existing=True):
            return _load_paa_image_with_original_p3d(filepath, color_space=color_space, check_existing=check_existing)
        setattr(import_paa_mod, "load_file", _module_load_file)

    return import_paa_mod

def _normalize_drive_relative_path(path_value: str) -> str:
    from .nh_collider_exp import (_norm_path)
    raw = _norm_path(str(path_value or "").strip())
    match = re.match(r"^([A-Za-z]):(?![\\/])(.*)$", raw)
    if match:
        tail = match.group(2).lstrip("\\/")
        return f"{match.group(1)}:\\{tail}"
    return raw

def _iter_p3d_project_roots(context=None):
    from .nh_collider_exp import (_tex_export_resolve_path)
    try:
        scene = (context or bpy.context).scene
    except Exception:
        scene = None
    if scene is None:
        return

    for attr in dir(scene):
        if not attr.startswith("a3ob"):
            continue
        try:
            pg = getattr(scene, attr)
        except Exception:
            continue
        if not hasattr(pg, "bl_rna"):
            continue
        for prop in pg.bl_rna.properties:
            if prop.identifier == "rna_type" or prop.type != "STRING":
                continue
            label = f"{prop.identifier} {prop.name}".lower()
            if not any(token in label for token in ("project", "root", "folder")):
                continue
            try:
                value = str(getattr(pg, prop.identifier, "") or "").strip()
            except Exception:
                value = ""
            if not value:
                continue
            resolved = _tex_export_resolve_path(value)
            if resolved and os.path.isdir(resolved):
                yield resolved

def _texture_path_rel_variants(raw: str):
    from .nh_collider_exp import (_base_color_path_variants, _unique_ci)
    norm = _normalize_drive_relative_path(raw).strip().lstrip("\\/")
    if not norm:
        return []

    variants = _base_color_path_variants(norm)
    no_suffix = _strip_blender_numeric_suffix(norm)
    if no_suffix != norm:
        variants.extend(_base_color_path_variants(no_suffix))

    for item in list(variants):
        ext = os.path.splitext(item)[1].lower()
        if not ext:
            variants.append(item + ".paa")
            variants.append(item + ".dds")
        elif ext == ".paa":
            variants.append(os.path.splitext(item)[0] + ".dds")
        elif ext == ".dds":
            variants.append(os.path.splitext(item)[0] + ".paa")
    return _unique_ci(variants)

def _add_texture_resolution_candidate(candidates, value):
    from .nh_collider_exp import (_base_color_path_variants, _is_invalid_windows_filename_component, _is_placeholder_material_name)
    raw = _normalize_drive_relative_path(value)
    if not raw:
        return
    if _is_placeholder_material_name(raw):
        print(f"Skipped placeholder material: {raw}")
        return
    if _is_invalid_windows_filename_component(raw):
        print(f"Skipped invalid texture candidate: {raw}")
        return
    candidates.extend(_base_color_path_variants(raw))

def _resolve_p3d_texture_path(texture_path: str) -> str:
    from .nh_collider_exp import (_is_blender_install_texture_path_invalid, _is_invalid_windows_filename_component, _is_placeholder_material_name, _iter_texture_resolution_roots, _norm_path)
    from .nh_snap import (_import_first_available_module)
    raw = _normalize_drive_relative_path(texture_path)
    if not raw:
        return ""
    if _is_placeholder_material_name(raw):
        print(f"Skipped placeholder material: {raw}")
        return ""
    if _is_invalid_windows_filename_component(raw):
        print(f"Skipped invalid texture candidate: {raw}")
        return ""

    import_p3d_mod = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.import_p3d",
            "NH_bundle.io.import_p3d",
        )
    )
    resolver = getattr(import_p3d_mod, "resolve_texture_path", None) if import_p3d_mod is not None else None
    candidates = []

    if callable(resolver):
        try:
            resolved = resolver(raw)
            if resolved:
                _add_texture_resolution_candidate(candidates, resolved)
        except Exception:
            pass

    utils_mod = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.utilities.generic",
            "NH_bundle.utilities.generic",
        )
    )
    restore_absolute = getattr(utils_mod, "restore_absolute", None) if utils_mod is not None else None

    if callable(restore_absolute):
        for extension in ("", ".paa", ".dds"):
            try:
                candidate = restore_absolute(raw, extension)
            except TypeError:
                try:
                    candidate = restore_absolute(raw)
                except Exception:
                    candidate = ""
            except Exception:
                candidate = ""
            if candidate:
                _add_texture_resolution_candidate(candidates, candidate)

    _add_texture_resolution_candidate(candidates, raw)

    raw_is_abs = os.path.isabs(raw) or re.match(r"^[A-Za-z]:[\\/]", raw) is not None
    if not raw_is_abs:
        for root in _iter_texture_resolution_roots():
            if not root:
                continue
            for rel in _texture_path_rel_variants(raw):
                _add_texture_resolution_candidate(candidates, os.path.join(root, rel))

    try:
        blender_abs = os.path.abspath(bpy.path.abspath(raw))
        if not _is_blender_install_texture_path_invalid(blender_abs):
            _add_texture_resolution_candidate(candidates, blender_abs)
    except Exception:
        pass

    if os.path.splitext(_strip_blender_numeric_suffix(raw))[1] == "":
        try:
            blender_abs_paa = os.path.abspath(bpy.path.abspath(raw + ".paa"))
            if not _is_blender_install_texture_path_invalid(blender_abs_paa):
                _add_texture_resolution_candidate(candidates, blender_abs_paa)
        except Exception:
            pass

    checked = set()
    for candidate in candidates:
        if not candidate:
            continue
        try:
            candidate_abs = os.path.abspath(os.path.normpath(_normalize_drive_relative_path(candidate)))
        except Exception:
            continue
        if _is_blender_install_texture_path_invalid(candidate_abs):
            print(f"Skipped invalid texture candidate: {candidate_abs}")
            continue
        key = os.path.normcase(candidate_abs)
        if key in checked:
            continue
        checked.add(key)
        if os.path.isfile(candidate_abs):
            resolved = _norm_path(candidate_abs)
            if os.path.splitext(resolved)[1].lower() == ".dds":
                print(f"DDS source found: {resolved}")
            return resolved

    return ""

def _nh_blender_shared_cache_base(create=False) -> str:
    base_dir = os.environ.get("LOCALAPPDATA") or ""
    if not base_dir:
        try:
            base_dir = bpy.utils.user_resource("CONFIG") or ""
        except Exception:
            base_dir = ""
    if not base_dir:
        base_dir = bpy.app.tempdir or os.path.expanduser("~")
    path = os.path.join(base_dir, "NH_Blender")
    if create:
        os.makedirs(path, exist_ok=True)
    return path

def _nh_texture_cache_root(create=False) -> str:
    from .nh_model_split import (_NH_TEXTURE_CACHE_FOLDER_NAME)
    path = os.path.join(_nh_blender_shared_cache_base(create=create), _NH_TEXTURE_CACHE_FOLDER_NAME)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _nh_texture_export_output_root(create=False) -> str:
    path = os.path.join(_nh_blender_shared_cache_base(create=create), "NH_ObjectTextures")
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _texture_cache_key_for_path(path_abs: str) -> str:
    from .nh_base import (_TEXTURE_PREVIEW_CACHE_SCHEMA_VERSION)
    try:
        normalized = os.path.normcase(os.path.abspath(bpy.path.abspath(path_abs))).replace("/", "\\")
    except Exception:
        normalized = os.path.normcase(os.path.abspath(path_abs or "")).replace("/", "\\")
    versioned_key = f"v{_TEXTURE_PREVIEW_CACHE_SCHEMA_VERSION}\0{normalized}"
    return hashlib.sha1(versioned_key.encode("utf-8", errors="replace")).hexdigest()

def _paa_preview_cache_path(paa_abs_path: str) -> str:
    from .nh_base import (_TEXTURE_PREVIEW_CACHE_SCHEMA_VERSION)
    key = _texture_cache_key_for_path(paa_abs_path)
    basename = os.path.splitext(os.path.basename(paa_abs_path or "texture"))[0] or "texture"
    safe_basename = re.sub(r'[<>:"/\\|?*]+', "_", basename).strip(" .") or "texture"
    folder = os.path.join(_nh_texture_cache_root(create=True), key[:2], key[2:4])
    return os.path.join(
        folder,
        f"{safe_basename}__v{_TEXTURE_PREVIEW_CACHE_SCHEMA_VERSION}_{key[:12]}.png",
    )

def _texture_cache_is_valid(source_abs_path: str, cache_path: str) -> bool:
    if not source_abs_path or not cache_path or not os.path.isfile(source_abs_path) or not os.path.isfile(cache_path):
        return False
    try:
        return os.path.getmtime(cache_path) >= os.path.getmtime(source_abs_path)
    except Exception:
        return False

def _iter_paa_files_recursive(root_abs: str):
    root_abs = os.path.abspath(bpy.path.abspath(root_abs or ""))
    if not root_abs or not os.path.isdir(root_abs):
        return
    cache_root = os.path.normcase(os.path.abspath(_nh_texture_cache_root(create=False)))
    for dirpath, dirnames, filenames in os.walk(root_abs):
        try:
            dirpath_abs = os.path.abspath(dirpath)
            if os.path.normcase(dirpath_abs).startswith(cache_root):
                dirnames[:] = []
                continue
        except Exception:
            pass
        dirnames[:] = [d for d in dirnames if d not in {"_NH_previews", "__pycache__"}]
        for filename in filenames:
            if os.path.splitext(filename)[1].lower() == ".paa":
                yield os.path.join(dirpath, filename)


def _texture_cache_worker_count(settings=None, item_count=0) -> int:
    try:
        configured = int(getattr(settings, "texture_cache_workers", 4) or 1)
    except Exception:
        configured = 4
    try:
        cpu_count = int(os.cpu_count() or configured or 1)
    except Exception:
        cpu_count = configured or 1
    item_count = max(1, int(item_count or 1))
    return max(1, min(configured, cpu_count, item_count, 8))


def _chunk_sequence_evenly(items, chunk_count: int):
    items = list(items or [])
    chunk_count = max(1, int(chunk_count or 1))
    return [items[idx::chunk_count] for idx in range(chunk_count) if items[idx::chunk_count]]


def _blender_binary_for_texture_workers() -> str:
    try:
        binary = getattr(bpy.app, "binary_path", "") or ""
        if binary and os.path.isfile(binary):
            return binary
    except Exception:
        pass
    return sys.executable


def _texture_cache_worker_script_source() -> str:
    return r'''
import importlib.util
import json
import os
import sys
import traceback

def _write_output(path, payload):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    addon_path, input_path, output_path = args[:3]
    spec = importlib.util.spec_from_file_location("nh_blender_texture_cache_worker_addon", addon_path)
    addon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(addon)
    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    files = list(payload.get("files", []) or [])
    force_rebuild = bool(payload.get("force_rebuild", False))
    stats = {"created": 0, "rebuilt": 0, "skipped": 0, "failed": [], "processed": 0}

    for paa_path in files:
        try:
            cache_path = addon._paa_preview_cache_path(paa_path)
            existed_before = os.path.isfile(cache_path)
            if (not force_rebuild) and addon._texture_cache_is_valid(paa_path, cache_path):
                stats["skipped"] += 1
                stats["processed"] += 1
                continue
            out_cache, _source_kind, _resolved = addon._ensure_texture_cache_for_paa(paa_path, force_rebuild=force_rebuild)
            if not out_cache:
                raise RuntimeError("cache PNG was not created")
            if existed_before:
                stats["rebuilt"] += 1
            else:
                stats["created"] += 1
            stats["processed"] += 1
        except Exception as exc:
            stats["failed"].append("{}: {}\n{}".format(paa_path, exc, traceback.format_exc(limit=4)))

    _write_output(output_path, stats)

if __name__ == "__main__":
    main()
'''


def _run_texture_cache_workers(paa_files, *, force_rebuild: bool = False, settings=None, context=None):
    from .nh_base import (_fmt_exc)
    unique_files = []
    seen = set()
    for fp in paa_files or []:
        try:
            fp_abs = os.path.abspath(bpy.path.abspath(fp))
        except Exception:
            fp_abs = os.path.abspath(fp or "")
        if not fp_abs or not os.path.isfile(fp_abs):
            continue
        key = os.path.normcase(fp_abs)
        if key in seen:
            continue
        seen.add(key)
        unique_files.append(fp_abs)

    stats = {"created": 0, "rebuilt": 0, "skipped": 0, "failed": [], "workers": 0, "processed": 0}
    if not unique_files:
        return stats

    worker_count = _texture_cache_worker_count(settings, len(unique_files))
    stats["workers"] = worker_count

    if worker_count <= 1:
        try:
            if context is not None:
                context.window_manager.progress_begin(0, len(unique_files))
        except Exception:
            pass
        try:
            for index, paa_path in enumerate(unique_files, start=1):
                try:
                    if context is not None:
                        context.window_manager.progress_update(index)
                except Exception:
                    pass
                try:
                    cache_path = _paa_preview_cache_path(paa_path)
                    existed_before = os.path.isfile(cache_path)
                    if (not force_rebuild) and _texture_cache_is_valid(paa_path, cache_path):
                        stats["skipped"] += 1
                        stats["processed"] += 1
                        continue
                    out_cache, _source_kind, _resolved = _ensure_texture_cache_for_paa(paa_path, force_rebuild=force_rebuild)
                    if not out_cache:
                        raise RuntimeError("cache PNG was not created")
                    if existed_before:
                        stats["rebuilt"] += 1
                    else:
                        stats["created"] += 1
                    stats["processed"] += 1
                except Exception as e:
                    stats["failed"].append(f"{paa_path}: {_fmt_exc(e)}")
        finally:
            try:
                if context is not None:
                    context.window_manager.progress_end()
            except Exception:
                pass
        return stats

    work_dir = tempfile.mkdtemp(prefix="nh_texture_cache_workers_")
    script_path = os.path.join(work_dir, "texture_cache_worker.py")
    addon_path = os.path.abspath(__file__)
    try:
        with open(script_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(_texture_cache_worker_script_source())

        blender_binary = _blender_binary_for_texture_workers()
        chunks = _chunk_sequence_evenly(unique_files, worker_count)
        processes = []
        for idx, chunk in enumerate(chunks, start=1):
            input_path = os.path.join(work_dir, f"worker_{idx:02d}.json")
            output_path = os.path.join(work_dir, f"worker_{idx:02d}.out.json")
            log_path = os.path.join(work_dir, f"worker_{idx:02d}.log")
            with open(input_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"files": chunk, "force_rebuild": bool(force_rebuild)}, f, ensure_ascii=False)
            log_file = open(log_path, "w", encoding="utf-8", newline="\n")
            try:
                cmd = [blender_binary, "--background", "--python", script_path, "--", addon_path, input_path, output_path]
                proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
            except Exception:
                try:
                    log_file.close()
                except Exception:
                    pass
                raise
            processes.append({
                "proc": proc,
                "log_file": log_file,
                "log_path": log_path,
                "output_path": output_path,
                "chunk_size": len(chunk),
                "done": False,
            })

        completed = 0
        import time
        deadline = time.monotonic() + max(120.0, min(3600.0, 5.0 * max(len(unique_files), 1)))
        try:
            if context is not None:
                context.window_manager.progress_begin(0, len(unique_files))
        except Exception:
            pass
        try:
            while any(not item["done"] for item in processes):
                if time.monotonic() > deadline:
                    for item in processes:
                        if item["done"]:
                            continue
                        item["done"] = True
                        proc = item["proc"]
                        item["log_file"] = None
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        io_item_log = item.get("log_path") or ""
                        if io_item_log:
                            stats["failed"].append(f"Worker timed out: {io_item_log}")
                    break
                for item in processes:
                    if item["done"]:
                        continue
                    proc = item["proc"]
                    if proc.poll() is None:
                        continue
                    item["done"] = True
                    completed += int(item["chunk_size"])
                    if item["log_file"] is not None:
                        try:
                            item["log_file"].close()
                        except Exception:
                            pass
                    try:
                        if context is not None:
                            context.window_manager.progress_update(min(completed, len(unique_files)))
                    except Exception:
                        pass
                if any(not item["done"] for item in processes):
                    time_sleep = getattr(bpy.app, "sleep", None)
                    if callable(time_sleep):
                        try:
                            time_sleep(0.1)
                            continue
                        except Exception:
                            pass
                    import time as _time  # noqa: PLC0415
                    _time.sleep(0.1)
        finally:
            try:
                if context is not None:
                    context.window_manager.progress_end()
            except Exception:
                pass

        for item in processes:
            proc = item["proc"]
            try:
                if not item["log_file"].closed:
                    item["log_file"].close()
            except Exception:
                pass
            if proc.returncode not in (0, None):
                log_tail = ""
                try:
                    with open(item["log_path"], "r", encoding="utf-8", errors="replace") as f:
                        log_tail = "".join(f.readlines()[-30:]).strip()
                except Exception:
                    pass
                stats["failed"].append(f"Worker failed with code {proc.returncode}: {log_tail}")
                continue
            if not os.path.isfile(item["output_path"]):
                stats["failed"].append("Worker finished without writing output")
                continue
            try:
                with open(item["output_path"], "r", encoding="utf-8") as f:
                    worker_stats = json.load(f)
                stats["created"] += int(worker_stats.get("created", 0) or 0)
                stats["rebuilt"] += int(worker_stats.get("rebuilt", 0) or 0)
                stats["skipped"] += int(worker_stats.get("skipped", 0) or 0)
                stats["processed"] += int(worker_stats.get("processed", 0) or 0)
                stats["failed"].extend(worker_stats.get("failed", []) or [])
            except Exception as e:
                stats["failed"].append(f"Could not read worker output: {_fmt_exc(e)}")
    finally:
        try:
            for item in processes:
                proc = item["proc"]
                try:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=30)
                except Exception:
                    pass
                try:
                    log_file_handle = item.get("log_file")
                    if log_file_handle is not None and not log_file_handle.closed:
                        log_file_handle.close()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass

    return stats


def _ensure_texture_cache_for_paa(paa_abs_path: str, force_rebuild: bool = False):
    image, _has_alpha, resolved_path, source_kind, cache_path = _load_material_preview_image(
        paa_abs_path,
        keep_converted_textures=True,
        color_space="SRGB",
        force_rebuild_cache=bool(force_rebuild),
    )
    try:
        _remove_image_if_unused(image)
    except Exception:
        pass
    return cache_path if cache_path and os.path.isfile(cache_path) else "", source_kind, resolved_path

def _save_image_as_png(image, filepath: str):
    folder = os.path.dirname(filepath)
    if folder:
        os.makedirs(folder, exist_ok=True)

    prev_filepath_raw = getattr(image, "filepath_raw", "")
    prev_filepath = getattr(image, "filepath", "")
    prev_file_format = getattr(image, "file_format", None)

    try:
        image.filepath_raw = filepath
        if prev_file_format is not None:
            image.file_format = "PNG"
        image.save()
    finally:
        try:
            image.filepath_raw = prev_filepath_raw
        except Exception:
            pass
        try:
            image.filepath = prev_filepath
        except Exception:
            pass
        if prev_file_format is not None:
            try:
                image.file_format = prev_file_format
            except Exception:
                pass

def _load_external_image(filepath: str, color_space: str = "SRGB"):
    image = bpy.data.images.load(filepath, check_existing=True)
    _apply_image_color_space(image, color_space)
    return image

def _image_filepath_value(image) -> str:
    if image is None:
        return ""
    return (getattr(image, "filepath_raw", "") or getattr(image, "filepath", "") or "").strip()

def _relative_texture_path_from_blender_install(path_value: str) -> str:
    from .nh_collider_exp import (_blender_install_dir_abs, _norm_path, _path_is_under_or_equal_safe)
    raw = _normalize_drive_relative_path(path_value)
    blender_dir = _blender_install_dir_abs()
    if not raw or not blender_dir:
        return ""
    try:
        raw_abs = os.path.abspath(os.path.normpath(raw))
        if not _path_is_under_or_equal_safe(raw_abs, blender_dir):
            return ""
        return _norm_path(os.path.relpath(raw_abs, blender_dir))
    except Exception:
        return ""

def _set_image_filepath_value(image, filepath: str):
    if image is None:
        return
    try:
        image.filepath_raw = filepath
    except Exception:
        pass
    try:
        image.filepath = filepath
    except Exception:
        pass

def _repair_preview_image_path(image) -> bool:
    from .nh_collider_exp import (_is_blender_install_texture_path_invalid, _unique_ci)
    raw = _image_filepath_value(image)
    if not raw:
        return False

    raw = _normalize_drive_relative_path(raw)
    rel_from_blender = _relative_texture_path_from_blender_install(raw)
    blender_resolved = ""
    try:
        blender_resolved = os.path.abspath(bpy.path.abspath(raw))
    except Exception:
        blender_resolved = ""
    if not rel_from_blender and blender_resolved:
        rel_from_blender = _relative_texture_path_from_blender_install(blender_resolved)

    if not rel_from_blender and not _is_blender_install_texture_path_invalid(raw):
        return False

    search_values = _unique_ci([
        rel_from_blender,
        raw if not _is_blender_install_texture_path_invalid(raw) else "",
        getattr(image, "name", "") or "",
    ])
    for candidate in search_values:
        if not candidate:
            continue
        resolved = _resolve_p3d_texture_path(candidate)
        if resolved and not _is_blender_install_texture_path_invalid(resolved):
            _set_image_filepath_value(image, resolved)
            return True

    print(f"Skipped invalid texture candidate: {raw}")
    _set_image_filepath_value(image, "")
    return True

def _repair_existing_preview_image_paths():
    from .nh_base import (_fmt_exc)
    repaired = 0
    for image in list(getattr(bpy.data, "images", []) or []):
        try:
            if _repair_preview_image_path(image):
                repaired += 1
        except Exception as e:
            print(f"Preview image path repair failed for {getattr(image, 'name', '<image>')}: {_fmt_exc(e)}")
    return repaired

def _selected_base_color_texture_path(original_path: str, resolved_path: str) -> str:
    from .nh_collider_exp import (_base_color_stem, _base_color_suffix, _norm_path)
    selected_suffix = _base_color_suffix(resolved_path)
    if not selected_suffix or selected_suffix == _base_color_suffix(original_path):
        return original_path

    original = _normalize_drive_relative_path(original_path)
    folder, leaf = os.path.split(original)
    original_base, original_ext = os.path.splitext(leaf)
    resolved_ext = os.path.splitext(resolved_path)[1]
    stem = _base_color_stem(original_base)
    if not stem:
        return original_path

    selected_leaf = stem + selected_suffix + (original_ext or resolved_ext or ".paa")
    return _norm_path(os.path.join(folder, selected_leaf) if folder else selected_leaf)

def _load_material_preview_image(
    texture_path: str,
    keep_converted_textures: bool,
    color_space: str = "SRGB",
    force_rebuild_cache: bool = False,
    cache_missing_textures: bool = True,
):
    from .nh_base import (_fmt_exc)
    from .nh_snap import (_import_first_available_module)
    resolved_path = _resolve_p3d_texture_path(texture_path)
    if not resolved_path:
        return None, False, "", "missing", ""

    declared_has_alpha = _base_color_declared_has_alpha(resolved_path)
    ext = os.path.splitext(resolved_path)[1].lower()
    if ext != ".paa":
        try:
            image = _load_external_image(resolved_path, color_space)
            has_alpha = _has_image_alpha(image) if declared_has_alpha is None else declared_has_alpha
            return image, has_alpha, resolved_path, "file", ""
        except Exception:
            return None, False, resolved_path, "missing", ""

    import_paa_mod = _import_first_available_module(
        (
            "bl_ext.user_default.Arma3ObjectBuilder.io.import_paa",
            "NH_bundle.io.import_paa",
        )
    )
    if import_paa_mod is None:
        import_paa_mod = _ensure_p3d_import_paa_helpers()
    else:
        import_paa_mod = _ensure_p3d_import_paa_helpers() or import_paa_mod
    if import_paa_mod is None:
        return None, False, resolved_path, "missing", ""

    load_file = getattr(import_paa_mod, "load_file", None)
    if not callable(load_file):
        def _fallback_load_file(filepath, color_space="SRGB", check_existing=True):
            return _load_paa_image_with_original_p3d(filepath, color_space=color_space, check_existing=check_existing)
        load_file = _fallback_load_file

    cache_path = _paa_preview_cache_path(resolved_path)
    if not force_rebuild_cache and os.path.isfile(cache_path):
        cache_valid = _texture_cache_is_valid(resolved_path, cache_path)
        if cache_valid:
            try:
                image = _load_external_image(cache_path, color_space)
                has_alpha = _has_image_alpha(image) if declared_has_alpha is None else declared_has_alpha
                return image, has_alpha, resolved_path, "cache_hit", cache_path
            except Exception:
                try:
                    os.remove(cache_path)
                except Exception:
                    pass

    if keep_converted_textures and not cache_missing_textures:
        return None, False, resolved_path, "cache_missing", cache_path

    try:
        image, tex = load_file(resolved_path, color_space)
    except Exception:
        return None, False, resolved_path, "missing", cache_path

    if image is None:
        return None, False, resolved_path, "missing", cache_path

    has_alpha = _has_image_alpha(image)
    try:
        paa_ns = getattr(import_paa_mod, "paa", None)
        paa_type = getattr(paa_ns, "PAA_Type", None) if paa_ns is not None else None
        dxt5 = getattr(paa_type, "DXT5", None) if paa_type is not None else None
        if tex is not None and dxt5 is not None:
            has_alpha = getattr(tex, "type", None) == dxt5
    except Exception:
        pass
    if declared_has_alpha is not None:
        has_alpha = declared_has_alpha

    if keep_converted_textures:
        try:
            _save_image_as_png(image, cache_path)
            cache_image = _load_external_image(cache_path, color_space)
            cache_has_alpha = (
                (_has_image_alpha(cache_image) or has_alpha)
                if declared_has_alpha is None
                else declared_has_alpha
            )
            _remove_image_if_unused(image)
            return cache_image, cache_has_alpha, resolved_path, "cache_created", cache_path
        except Exception as e:
            print("=== Import/Export planner: failed to write texture cache ===")
            print(f"{resolved_path} -> {_fmt_exc(e)}")

    return image, has_alpha, resolved_path, "paa_runtime", cache_path

def _setup_import_preview_nodes(material: bpy.types.Material, image, texture_label: str, has_alpha: bool):
    if material is None or image is None:
        return False

    material.use_nodes = True
    node_tree = getattr(material, "node_tree", None)
    if node_tree is None:
        return False

    nodes = node_tree.nodes
    links = node_tree.links
    nodes.clear()

    node_output = nodes.new("ShaderNodeOutputMaterial")
    node_output.location = (300, 0)

    node_shader = nodes.new("ShaderNodeBsdfPrincipled")
    node_shader.location = (0, 0)
    links.new(node_shader.outputs["BSDF"], node_output.inputs["Surface"])

    node_texture = nodes.new("ShaderNodeTexImage")
    node_texture.location = (-320, 0)
    node_texture.image = image
    node_texture.label = os.path.basename(texture_label or getattr(image, "filepath", "") or image.name)
    links.new(node_texture.outputs["Color"], node_shader.inputs["Base Color"])

    if has_alpha:
        try:
            links.new(node_texture.outputs["Alpha"], node_shader.inputs["Alpha"])
            _enable_preview_material_alpha(material)
        except Exception:
            pass
    else:
        _disable_preview_material_alpha(material)

    return True

def _postprocess_imported_material_previews(
    context,
    imported_objs,
    *,
    show_materials: bool,
    keep_converted_textures: bool,
    pack_runtime_images: bool = False,
    force_rebuild_cache: bool = False,
    cache_missing_textures: bool = True,
):
    from .nh_base import (_fmt_exc)
    from .nh_collider_exp import (_is_placeholder_material_name, _is_valid_texture_candidate, _log_rejected_texture_candidate)
    result = {
        "materials_total": 0,
        "textured_candidates": 0,
        "previewed": 0,
        "missing": 0,
        "packed": 0,
        "cache_hits": 0,
        "cache_created": 0,
        "errors": [],
    }

    if not show_materials:
        return result

    _repair_existing_preview_image_paths()

    materials = _iter_unique_materials_from_objects(imported_objs)
    result["materials_total"] = len(materials)

    for mat in materials:
        paa_path, _ = _get_p3d_material_paths(mat)
        if not paa_path:
            continue
        if _is_placeholder_material_name(paa_path):
            print(f"Skipped placeholder material: {paa_path}")
            continue
        if not _is_valid_texture_candidate(paa_path):
            _log_rejected_texture_candidate(paa_path, material_name=getattr(mat, "name", ""))
            continue

        result["textured_candidates"] += 1
        image, has_alpha, resolved_path, source_kind, _cache_path = _load_material_preview_image(
            paa_path,
            keep_converted_textures,
            color_space="SRGB",
            force_rebuild_cache=force_rebuild_cache,
            cache_missing_textures=cache_missing_textures,
        )
        if image is None:
            result["missing"] += 1
            continue

        selected_paa_path = _selected_base_color_texture_path(paa_path, resolved_path)
        if selected_paa_path != paa_path:
            try:
                _, rvmat_path = _get_p3d_material_paths(mat)
                _set_p3d_material_paths(mat, selected_paa_path, rvmat_path)
                paa_path = selected_paa_path
            except Exception as e:
                print(f"Base Color auto-select failed for {mat.name}: {_fmt_exc(e)}")

        try:
            if _setup_import_preview_nodes(mat, image, resolved_path or paa_path, has_alpha):
                result["previewed"] += 1
                if source_kind == "cache_hit":
                    result["cache_hits"] += 1
                elif source_kind == "cache_created":
                    result["cache_created"] += 1
                elif pack_runtime_images and source_kind == "paa_runtime":
                    try:
                        if getattr(image, "packed_file", None) is None:
                            image.pack()
                        result["packed"] += 1
                    except Exception as e:
                        result["errors"].append(f"{mat.name}: pack preview image: {_fmt_exc(e)}")
        except Exception as e:
            result["errors"].append(f"{mat.name}: {_fmt_exc(e)}")

    scene = getattr(context, "scene", None)
    tex_settings = getattr(scene, "cray_texreplace_settings", None) if scene is not None else None
    preview_obj = getattr(tex_settings, "picked_object", None) if tex_settings is not None else None
    if preview_obj in imported_objs and tex_settings is not None:
        try:
            _collect_object_image_materials(preview_obj, tex_settings.obj_preview_items)
        except Exception:
            pass

    return result

def _get_import_preview_settings(context, operator=None):
    scene = getattr(context, "scene", None)
    settings = getattr(scene, "cray_ie_settings", None) if scene is not None else None

    show_materials = bool(getattr(settings, "import_show_materials", True))
    if operator is not None and hasattr(operator, "load_textures"):
        try:
            show_materials = bool(getattr(operator, "load_textures"))
        except Exception:
            pass

    keep_converted_textures = bool(getattr(settings, "import_keep_converted_textures", True))
    return show_materials, keep_converted_textures

def _log_import_preview_summary(filepath: str, stats):
    if not stats:
        return

    previewed = int(stats.get("previewed", 0) or 0)
    missing = int(stats.get("missing", 0) or 0)
    packed = int(stats.get("packed", 0) or 0)
    cache_hits = int(stats.get("cache_hits", 0) or 0)
    cache_created = int(stats.get("cache_created", 0) or 0)
    errors = list(stats.get("errors", []) or [])

    if previewed == 0 and missing == 0 and packed == 0 and cache_hits == 0 and cache_created == 0 and not errors:
        return

    print("=== Import/Export planner: material previews ===")
    print(f"{os.path.basename(filepath) or filepath}")
    print(
        "materials: {materials_total}, textured: {textured_candidates}, previewed: {previewed}, "
        "missing: {missing}, packed: {packed}, cache hits: {cache_hits}, cache created: {cache_created}".format(
            materials_total=int(stats.get("materials_total", 0) or 0),
            textured_candidates=int(stats.get("textured_candidates", 0) or 0),
            previewed=previewed,
            missing=missing,
            packed=packed,
            cache_hits=cache_hits,
            cache_created=cache_created,
        )
    )
    if errors:
        for item in errors[:20]:
            print(item)

# ---------- UI data ----------

class CRAY_PG_TexDBItem(PropertyGroup):
    basename: StringProperty()
    abs_path: StringProperty()
    rel_path: StringProperty()
    is_problem: BoolProperty(default=False)
    dup_count: IntProperty(default=0)

class CRAY_PG_ObjMatImagesItem(PropertyGroup):
    mat_name: StringProperty()
    images_csv: StringProperty()

class CRAY_PG_TexSourceRootItem(PropertyGroup):
    path: StringProperty(name="Root", subtype="DIR_PATH")

from .nh_base import (_TEX_EXPORT_DDS_BACKEND_ITEMS, _TEX_EXPORT_DEFAULT_SOURCE_ROOTS)

class CRAY_PG_TexReplaceSettings(PropertyGroup):
    folder: StringProperty(name="Folder", default=_nh_texture_export_output_root(create=False), subtype="DIR_PATH")
    picked_object: PointerProperty(name="Select Object", type=bpy.types.Object)
    write_expected_missing_paths: BoolProperty(
        name="Write Expected Missing Paths",
        description="Write expected .paa/.rvmat paths even when files are missing from DB",
        default=True,
    )
    source_textures_folder: StringProperty(
        name="Source Textures Folder",
        subtype="DIR_PATH",
        default=";".join(_TEX_EXPORT_DEFAULT_SOURCE_ROOTS),
        description="One or more DDS source roots; separate multiple folders with ; or new lines",
    )
    source_root_to_add: StringProperty(
        name="Add Source Root",
        subtype="DIR_PATH",
        default="",
        description="Pick or type a DDS source root, then press +",
    )
    source_texture_roots: CollectionProperty(type=CRAY_PG_TexSourceRootItem)
    source_texture_roots_index: IntProperty(default=0, options={"SKIP_SAVE"})
    target_textures_folder: StringProperty(
        name="Target Textures Folder",
        subtype="DIR_PATH",
        default=_nh_texture_export_output_root(create=False),
    )
    texture_cache_source_folder: StringProperty(
        name="Texture Cache Source",
        subtype="DIR_PATH",
        default=_nh_texture_export_output_root(create=False),
        description="РљРѕСЂРЅРµРІР°СЏ РїР°РїРєР° СЃ .paa С‚РµРєСЃС‚СѓСЂР°РјРё РґР»СЏ РѕР±С‰РµРіРѕ PNG-РєРµС€Р° Blender",
    )
    texture_cache_workers: IntProperty(
        name="Cache Workers",
        default=4,
        min=1,
        max=8,
        description="РљРѕР»РёС‡РµСЃС‚РІРѕ С„РѕРЅРѕРІС‹С… Blender worker-РїСЂРѕС†РµСЃСЃРѕРІ РґР»СЏ РїР°СЂР°Р»Р»РµР»СЊРЅРѕР№ СЃР±РѕСЂРєРё PNG-РєРµС€Р° С‚РµРєСЃС‚СѓСЂ",
    )
    texture_cache_last_summary: StringProperty(
        default="",
        options={"SKIP_SAVE"},
    )
    texture_cache_last_report_path: StringProperty(
        default="",
        options={"SKIP_SAVE"},
    )
    texture_tools_folder: StringProperty(
        name="Texture Tools Folder",
        subtype="DIR_PATH",
        default="",
    )
    convert_dds_to_png: BoolProperty(
        name="Convert DDS to PNG",
        default=True,
    )
    dds_backend: EnumProperty(
        name="DDS Backend",
        items=_TEX_EXPORT_DDS_BACKEND_ITEMS,
        default="BUILTIN_PYTHON",
    )
    node_exe_path: StringProperty(
        name="Node.exe",
        subtype="FILE_PATH",
        default="",
    )
    external_dds_converter_path: StringProperty(
        name="External DDS Converter",
        subtype="FILE_PATH",
        default="",
    )
    convert_png_to_paa: BoolProperty(
        name="Convert PNG to PAA",
        default=True,
    )
    image_to_paa_path: StringProperty(
        name="ImageToPAA / Pal2PacE",
        subtype="FILE_PATH",
        default="E:\\SteamLibrary\\steamapps\\common\\DayZ Tools\\Bin\\ImageToPAA\\ImageToPAA.exe",
    )
    generate_rvmat: BoolProperty(
        name="Generate RVMAT",
        default=True,
    )
    export_only_missing: BoolProperty(
        name="Only Missing",
        default=True,
    )
    export_overwrite_existing: BoolProperty(
        name="Overwrite Existing",
        default=False,
    )
    delete_png_after_paa: BoolProperty(
        name="Delete PNG after PAA",
        default=False,
    )
    texture_export_is_running: BoolProperty(
        default=False,
        options={"SKIP_SAVE"},
    )
    texture_export_cancel_requested: BoolProperty(
        default=False,
        options={"SKIP_SAVE"},
    )
    texture_export_progress_current: IntProperty(
        default=0,
        options={"SKIP_SAVE"},
    )
    texture_export_progress_total: IntProperty(
        default=0,
        options={"SKIP_SAVE"},
    )
    texture_export_progress_label: StringProperty(
        default="",
        options={"SKIP_SAVE"},
    )
    texture_export_progress_action: StringProperty(
        default="",
        options={"SKIP_SAVE"},
    )
    texture_export_last_summary: StringProperty(
        default="",
        options={"SKIP_SAVE"},
    )
    texture_export_last_report_path: StringProperty(
        default="",
        options={"SKIP_SAVE"},
    )
    show_component_fix_tools: BoolProperty(
        name="Component fixes from .txt",
        description="Show tools for selecting and cleaning components listed in a .txt file",
        default=False,
    )
    fix_list_path: StringProperty(
        name="Fix List .txt",
        description="Structured text file with bad component names per .p3d and LOD",
        default="",
        subtype="FILE_PATH",
    )
    fix_mesh_join_batch: IntProperty(
        name="Fix Mesh Join Batch",
        description=(
            "How many meshes to join in one pass. "
            "1 = try to join all at once (legacy behavior), "
            "higher values split work into stages"
        ),
        default=1,
        min=1,
        max=500,
    )
    fix_mesh_center_to_origin: BoolProperty(
        name="Center Fixed Mesh To (0,0,0)",
        description="After Fix Mesh, move merged object's bounds center to world origin",
        default=True,
    )
    material_safe_merge_distance: FloatProperty(
        name="Material Safe Merge Distance",
        description="Merge close vertices only when their linked faces use the same material set",
        default=0.0001,
        min=0.0,
        precision=6,
        unit="LENGTH",
    )
    export_warn_loose_vertices: BoolProperty(
        name="Warn Loose Vertices On Export",
        description=(
            "During batch export, warn if any LOD except Point clouds > Memory contains isolated "
            "vertices with no edges or faces. Export continues so the model is still written."
        ),
        default=True,
    )
    split_planar_ngon_vertex_count: IntProperty(
        name="Flat Min N",
        description="Find flat thin face islands whose outer boundary has at least this many vertices",
        default=4,
        min=3,
        max=128,
    )
    split_planar_ngon_angle_tolerance: FloatProperty(
        name="Angle Tol",
        description="Maximum normal deviation in degrees when grouping faces into one planar island",
        default=0.1,
        min=0.0,
        soft_max=5.0,
        precision=4,
    )
    split_planar_ngon_plane_tolerance: FloatProperty(
        name="Plane Tol",
        description="Maximum signed distance from the seed plane for faces in the same planar island",
        default=0.0001,
        min=0.0,
        soft_max=0.01,
        precision=6,
    )
    obj_preview_items: bpy.props.CollectionProperty(type=CRAY_PG_ObjMatImagesItem)
    obj_preview_active_index: IntProperty(default=0)
    db_items: bpy.props.CollectionProperty(type=CRAY_PG_TexDBItem)
    db_active_index: IntProperty(default=0)

class CRAY_UL_TexDB(UIList):
    bl_idname = "CRAY_UL_tex_db"
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.alert = bool(item.is_problem)
        row = layout.row(align=True)
        row.label(text=item.basename, icon="FILE")
        row.label(text=item.rel_path)
        if item.is_problem:
            row.label(text=f"DUP x{item.dup_count}", icon="ERROR")

class CRAY_UL_ObjPreview(UIList):
    bl_idname = "CRAY_UL_obj_preview"
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.mat_name, icon="MATERIAL")
        row.label(text=item.images_csv, icon="IMAGE_DATA")

class CRAY_OT_TexDBBuildFromFolder(Operator):
    bl_idname = "cray.tex_db_build_folder"
    bl_label = "Build From Folder"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc, _save_texreplace_settings_now)
        ts = context.scene.cray_texreplace_settings
        _save_texreplace_settings_now(context)
        if not ts.folder:
            self.report({"ERROR"}, "Folder is not set")
            return {"CANCELLED"}

        folder_abs = bpy.path.abspath(ts.folder)
        if not os.path.isdir(folder_abs):
            self.report({"ERROR"}, f"Folder not found: {folder_abs}")
            return {"CANCELLED"}

        try:
            entries = _walk_folder_build_db(folder_abs)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to build DB from '{folder_abs}': {_fmt_exc(e)}")
            return {"CANCELLED"}

        ts.db_items.clear()
        for d in entries:
            it = ts.db_items.add()
            it.basename = d["basename"]
            it.abs_path = d["abs_path"]
            it.rel_path = d["rel_path"]
            it.is_problem = d["is_problem"]
            it.dup_count = d["dup_count"]

        total = len(entries)
        problems = sum(1 for d in entries if d["is_problem"])
        if total == 0:
            self.report({"WARNING"}, "DB is empty: no .paa/.rvmat found")
        elif problems:
            self.report({"WARNING"}, f"DB built: {total}. Problematic duplicates: {problems} (red)")
        else:
            self.report({"INFO"}, f"DB built: {total} (.paa/.rvmat)")
        _save_texreplace_settings_now(context)
        return {"FINISHED"}

class CRAY_OT_TexSourceRootAdd(Operator):
    bl_idname = "cray.tex_source_root_add"
    bl_label = "Add Texture Source Root"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_save_texreplace_settings_now)
        from .nh_collider_exp import (_ensure_tex_source_roots_collection, _split_tex_source_roots_text, _sync_tex_source_roots_text, _tex_source_roots_from_collection)
        ts = context.scene.cray_texreplace_settings
        _ensure_tex_source_roots_collection(ts)

        raw = getattr(ts, "source_root_to_add", "") or ""
        roots_to_add = _split_tex_source_roots_text(raw)
        if not roots_to_add:
            self.report({"WARNING"}, "Pick or type a source texture folder first")
            return {"CANCELLED"}

        existing = _tex_source_roots_from_collection(ts)
        existing_keys = {os.path.normcase(os.path.normpath(path)) for path in existing}
        added = 0
        for root in roots_to_add:
            key = os.path.normcase(os.path.normpath(root))
            if key in existing_keys:
                continue
            item = ts.source_texture_roots.add()
            item.path = root
            existing_keys.add(key)
            added += 1

        _sync_tex_source_roots_text(ts, _tex_source_roots_from_collection(ts))
        try:
            ts.source_root_to_add = ""
        except Exception:
            pass
        _save_texreplace_settings_now(context)

        if added:
            self.report({"INFO"}, f"Added {added} texture source root(s)")
        else:
            self.report({"INFO"}, "Texture source root already exists")
        return {"FINISHED"}

class CRAY_OT_TexSourceRootRemove(Operator):
    bl_idname = "cray.tex_source_root_remove"
    bl_label = "Remove Texture Source Root"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1, options={"SKIP_SAVE"})

    def execute(self, context):
        from .nh_base import (_fmt_exc, _save_texreplace_settings_now)
        from .nh_collider_exp import (_ensure_tex_source_roots_collection, _sync_tex_source_roots_text, _tex_source_roots_from_collection)
        ts = context.scene.cray_texreplace_settings
        _ensure_tex_source_roots_collection(ts)

        try:
            index = int(self.index)
        except Exception:
            index = -1
        try:
            count = len(ts.source_texture_roots)
        except Exception:
            count = 0
        if index < 0 or index >= count:
            self.report({"WARNING"}, "Texture source root not found")
            return {"CANCELLED"}

        try:
            ts.source_texture_roots.remove(index)
        except Exception as e:
            self.report({"ERROR"}, f"Could not remove texture source root: {_fmt_exc(e)}")
            return {"CANCELLED"}

        remaining = _tex_source_roots_from_collection(ts)
        if remaining:
            _sync_tex_source_roots_text(ts, remaining)
        else:
            try:
                ts.source_textures_folder = ""
            except Exception:
                pass
        try:
            ts.source_texture_roots_index = max(0, min(index, len(ts.source_texture_roots) - 1))
        except Exception:
            pass
        _save_texreplace_settings_now(context)
        self.report({"INFO"}, "Removed texture source root")
        return {"FINISHED"}

class CRAY_OT_UpdateObjectPreview(Operator):
    bl_idname = "cray.update_object_preview"
    bl_label = "Update Object Preview"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ts = context.scene.cray_texreplace_settings
        obj, src = _resolve_tex_target_object(context, ts.picked_object)
        if obj is None:
            ts.obj_preview_items.clear()
            self.report({"ERROR"}, "No mesh object found (pick one or select one)")
            return {"CANCELLED"}
        ts.picked_object = obj

        n = _collect_object_image_materials(obj, ts.obj_preview_items)
        if n == 0:
            self.report({"WARNING"}, f"Object '{obj.name}' has no materials with Image Texture nodes")
        else:
            suffix = "" if src == "picked" else f" (auto: {src})"
            self.report({"INFO"}, f"Object '{obj.name}': {n} materials with Image Texture nodes{suffix}")
        return {"FINISHED"}

class CRAY_OT_FixMeshHierarchy(Operator):
    bl_idname = "cray.fix_mesh_hierarchy"
    bl_label = "Fix Mesh/Hierarchy"
    bl_description = (
        "Use the picked/selected/active mesh as the main target, join meshes in scope, clean helper leftovers, "
        "and move the result into the fix collection"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        ts = context.scene.cray_texreplace_settings
        target_obj, src = _resolve_fix_target_object(context, ts.picked_object)
        if target_obj is None:
            self.report({"ERROR"}, "No mesh object found (pick/select one)")
            return {"CANCELLED"}
        ts.picked_object = target_obj

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass

        scope_objs, scope_src = _collect_fix_scope(context, target_obj)

        scope_names = []
        for o in scope_objs:
            try:
                name = o.name
            except ReferenceError:
                continue
            if not name:
                continue
            if name not in scope_names:
                scope_names.append(name)

        mesh_candidates = [
            o for o in (bpy.data.objects.get(name) for name in scope_names)
            if o is not None and o.type == "MESH" and o.data is not None and len(o.data.polygons) > 0
        ]
        if not mesh_candidates:
            self.report({"ERROR"}, "No mesh object in selected scope")
            return {"CANCELLED"}

        anchor_mesh = None
        anchor_src = "largest-non-helper"
        if target_obj in mesh_candidates and not _is_helper_object_name(target_obj.name):
            anchor_mesh = target_obj
            anchor_src = "target"
        else:
            non_helper = [o for o in mesh_candidates if not _is_helper_object_name(o.name)]
            if non_helper:
                anchor_mesh = max(non_helper, key=lambda o: len(o.data.polygons) if o.data else 0)
            else:
                anchor_mesh = max(mesh_candidates, key=lambda o: len(o.data.polygons) if o.data else 0)
                anchor_src = "largest-mesh"
        if anchor_mesh is None:
            self.report({"ERROR"}, "No valid mesh anchor in selected scope")
            return {"CANCELLED"}
        active_mesh_name = anchor_mesh.name
        ts.picked_object = anchor_mesh

        try:
            merged_obj, joined_count, join_passes = _join_meshes_in_batches(
                context=context,
                anchor_obj=anchor_mesh,
                mesh_names=[o.name for o in mesh_candidates],
                batch_size=ts.fix_mesh_join_batch,
            )
        except Exception as e:
            self.report({"ERROR"}, f"Join failed: {_fmt_exc(e)}")
            return {"CANCELLED"}

        live_scope_names = []
        for name in scope_names:
            ch_live = bpy.data.objects.get(name)
            if ch_live is None or ch_live == merged_obj:
                continue
            live_scope_names.append(( _obj_depth(ch_live), name ))
        live_scope_names.sort(key=lambda it: it[0], reverse=True)

        deleted_scope = 0
        for idx, (_, name) in enumerate(live_scope_names, start=1):
            ch_live = bpy.data.objects.get(name)
            if ch_live is None:
                continue
            try:
                bpy.data.objects.remove(ch_live, do_unlink=True)
            except Exception:
                continue
            deleted_scope += 1
            if idx % 50 == 0:
                _ui_yield()

        target_collection, mesh_obj = _ensure_flat_collection_mesh(context, merged_obj)
        deleted_target_objs, deleted_target_cols = _cleanup_target_collection_keep_mesh(target_collection, mesh_obj)

        deleted_helpers, deleted_helper_cols, remaining_helpers = _remove_helper_named_objects(
            scene=context.scene,
            keep_obj=mesh_obj,
        )

        centered = False
        center_vec = Vector((0.0, 0.0, 0.0))
        if ts.fix_mesh_center_to_origin:
            try:
                context.view_layer.update()
            except Exception:
                pass
            centered, center_vec = _center_object_bbox_to_world_origin(mesh_obj)
            try:
                context.view_layer.update()
            except Exception:
                pass

        origin_set = False
        origin_vec = Vector((0.0, 0.0, 0.0))
        origin_error = ""
        try:
            origin_set, origin_vec = _set_object_origin_to_geometry(mesh_obj)
            try:
                context.view_layer.update()
            except Exception:
                pass
        except Exception as e:
            origin_error = _fmt_exc(e)

        lod_applied, lod_status = _apply_fix_mesh_resolution0_lod_props(mesh_obj)
        try:
            mesh_obj.select_set(True)
            context.view_layer.objects.active = mesh_obj
            context.view_layer.update()
        except Exception:
            pass

        deleted_total = deleted_scope + deleted_target_objs + deleted_helpers
        extras = [
            f"src: {src}",
            f"scope: {scope_src}",
            f"scene: {context.scene.name}",
            f"scope_objs: {len(scope_names)}",
            f"anchor: {active_mesh_name}",
            f"anchor_src: {anchor_src}",
            f"join_passes: {join_passes}",
            f"join_batch: {int(ts.fix_mesh_join_batch)}",
        ]
        if deleted_helper_cols:
            extras.append(f"helper_cols: {deleted_helper_cols}")
        if remaining_helpers:
            extras.append(f"remaining_helpers: {len(remaining_helpers)}")
        if ts.fix_mesh_center_to_origin:
            if centered:
                extras.append(
                    f"centered_to_origin: yes ({center_vec.x:.3f}, {center_vec.y:.3f}, {center_vec.z:.3f})"
                )
            else:
                extras.append("centered_to_origin: already")
        else:
            extras.append("centered_to_origin: off")
        if origin_error:
            extras.append(f"origin_to_geometry_after_center: failed ({origin_error})")
        elif origin_set:
            extras.append(
                f"origin_to_geometry_after_center: yes ({origin_vec.x:.3f}, {origin_vec.y:.3f}, {origin_vec.z:.3f})"
            )
        else:
            extras.append("origin_to_geometry_after_center: already")
        if lod_applied:
            extras.append("a3ob_lod: Is P3D LOD, Resolution, Index 0")
        else:
            extras.append(f"a3ob_lod: not applied ({lod_status})")
        suffix = "" if not extras else f", {', '.join(extras)}"
        self.report(
            {"INFO"},
            (
                f"Fixed '{mesh_obj.name}': joined {joined_count}, removed objects {deleted_total}, "
                f"removed subcollections {deleted_target_cols}, "
                f"hierarchy: {context.scene.collection.name}/{target_collection.name}/{mesh_obj.name}{suffix}"
            ),
        )
        return {"FINISHED"}

class CRAY_OT_ReplaceTexturesFromDB(Operator):
    bl_idname = "cray.replace_textures_from_db"
    bl_label = "Replace Texture from DB"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc, _save_texreplace_settings_now)
        from .nh_collider_exp import (_build_expected_texture_pair, _build_material_candidates, _is_placeholder_material_name, _is_valid_texture_candidate, _pick_best_db_match, _unique_ci)
        ts = context.scene.cray_texreplace_settings
        _save_texreplace_settings_now(context)
        obj, _ = _resolve_tex_target_object(context, ts.picked_object)

        if obj is None:
            self.report({"ERROR"}, "No mesh object found (pick one or select one)")
            return {"CANCELLED"}
        ts.picked_object = obj
        if len(ts.db_items) == 0 and not ts.write_expected_missing_paths:
            self.report({"ERROR"}, "DB is empty. Build From Folder first.")
            return {"CANCELLED"}

        db_map, dup_names = _build_db_map(ts)

        materials_checked = 0
        matched_total = 0
        matched_both = 0
        matched_paa_only = 0
        matched_rvmat_only = 0
        virtual_paa = 0
        virtual_rvmat = 0
        changed = 0
        missing = []
        failed = []
        virtual_missing = []
        skipped_placeholders = []
        skipped_invalid = []

        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                continue

            materials_checked += 1
            candidates = _build_material_candidates(mat)
            if not candidates:
                if _is_placeholder_material_name(getattr(mat, "name", "")):
                    msg = f"Skipped placeholder material: {mat.name}"
                    print(msg)
                    skipped_placeholders.append(mat.name)
                else:
                    missing.append(f"{mat.name} -> no valid texture candidates")
                continue
            match = _pick_best_db_match(candidates, db_map)

            paa_path, rvmat_path, used_base, is_virtual_paa, is_virtual_rvmat = _build_expected_texture_pair(ts, candidates, match)
            paa_to_set = paa_path if (paa_path or "").strip() else None
            rvmat_to_set = rvmat_path if (rvmat_path or "").strip() else None

            if not paa_to_set and not rvmat_to_set:
                if _is_placeholder_material_name(used_base) or _is_placeholder_material_name(getattr(mat, "name", "")):
                    msg = f"Skipped placeholder material: {mat.name}"
                    print(msg)
                    skipped_placeholders.append(mat.name)
                    continue
                if used_base and not _is_valid_texture_candidate(used_base):
                    msg = f"{mat.name} -> invalid texture candidate: {used_base}"
                    print(f"Skipped invalid texture candidate: {used_base}")
                    skipped_invalid.append(msg)
                    continue
                preview = ", ".join(candidates[:5]) if candidates else "<none>"
                missing.append(f"{mat.name} -> no .paa/.rvmat match (candidates: {preview})")
                continue

            if match:
                found_paa = match["paa"]
                found_rvmat = match["rvmat"]
                matched_total += 1
                if found_paa and found_rvmat:
                    matched_both += 1
                elif found_paa:
                    matched_paa_only += 1
                else:
                    matched_rvmat_only += 1

            try:
                _set_p3d_material_paths(mat, paa_to_set, rvmat_to_set)
                changed += 1
                if is_virtual_paa and paa_to_set:
                    virtual_paa += 1
                    virtual_missing.append(f"{mat.name}: virtual .paa -> {paa_to_set}")
                if is_virtual_rvmat and rvmat_to_set:
                    virtual_rvmat += 1
                    virtual_missing.append(f"{mat.name}: virtual .rvmat -> {rvmat_to_set}")
            except Exception as e:
                failed.append(f"{mat.name} (base: {used_base}): {_fmt_exc(e)}")

        print("=== Texture Replace: Summary ===")
        print(f"Object: {obj.name}")
        print(f"Materials checked: {materials_checked}")
        print(
            f"Matched: {matched_total} (both: {matched_both}, "
            f"paa-only: {matched_paa_only}, rvmat-only: {matched_rvmat_only})"
        )
        print(f"Virtual missing paths written: {len(virtual_missing)} (paa: {virtual_paa}, rvmat: {virtual_rvmat})")
        print(f"Updated: {changed}")
        print(f"Missing: {len(missing)}")
        print(f"Skipped placeholders: {len(skipped_placeholders)}")
        print(f"Skipped invalid: {len(skipped_invalid)}")
        print(f"Failed: {len(failed)}")

        if failed:
            self.report({"ERROR"}, f"Updated: {changed}, failed: {len(failed)} (see System Console)")
            print("=== Texture Replace: P3D set failed ===")
            for f in failed:
                print(f)
            if missing:
                print("=== Texture Replace: Missing entries ===")
                for m in missing:
                    print(m)
            if virtual_missing:
                print("=== Texture Replace: Virtual missing paths written ===")
                for v in virtual_missing:
                    print(v)
            if skipped_placeholders:
                print("=== Texture Replace: Skipped placeholders ===")
                for item in _unique_ci(skipped_placeholders):
                    print(f"Skipped placeholder material: {item}")
            if skipped_invalid:
                print("=== Texture Replace: Skipped invalid candidates ===")
                for item in skipped_invalid:
                    print(item)
            return {"CANCELLED"}

        if virtual_missing:
            print("=== Texture Replace: Virtual missing paths written ===")
            for v in virtual_missing:
                print(v)

        if skipped_placeholders:
            print("=== Texture Replace: Skipped placeholders ===")
            for item in _unique_ci(skipped_placeholders):
                print(f"Skipped placeholder material: {item}")

        if skipped_invalid:
            print("=== Texture Replace: Skipped invalid candidates ===")
            for item in skipped_invalid:
                print(item)

        if missing:
            details = f"Updated: {changed}, missing: {len(missing)}"
            if virtual_missing:
                details += f", virtual missing paths: {len(virtual_missing)}"
            self.report({"WARNING"}, f"{details} (see System Console)")
            print("=== Texture Replace: Missing entries ===")
            for m in missing:
                print(m)
        elif virtual_missing:
            self.report({"WARNING"}, f"Updated: {changed}, virtual missing paths: {len(virtual_missing)} (see System Console)")
        elif skipped_placeholders or skipped_invalid:
            self.report(
                {"WARNING"},
                f"Updated: {changed}, skipped placeholders: {len(skipped_placeholders)}, invalid: {len(skipped_invalid)} (see System Console)",
            )
        else:
            self.report({"INFO"}, f"Updated: {changed} materials (P3D updated)")

        if dup_names:
            print("=== Texture Replace: DB duplicates (picked first path) ===")
            for d in sorted(dup_names):
                print(d)

        try:
            preview_stats = _postprocess_imported_material_previews(
                context,
                [obj],
                show_materials=True,
                keep_converted_textures=True,
                pack_runtime_images=False,
            )
            _log_import_preview_summary(obj.name, preview_stats)
        except Exception as e:
            print("=== Texture Replace: material preview refresh failed ===")
            print(_fmt_exc(e))

        _save_texreplace_settings_now(context)
        return {"FINISHED"}

class CRAY_OT_ExportMissingTexturesFromSources(Operator):
    bl_idname = "cray.export_missing_textures_from_sources"
    bl_label = "Export Missing Textures"
    bl_options = {"REGISTER", "UNDO"}

    _timer = None

    def _init_runtime(self):
        self.events = []
        self.source_root = ""
        self.source_roots = []
        self.target_root = ""
        self.dds_map = {}
        self.dds_scanned = 0
        self.obj = None
        self.requests = []
        self.index = 0
        self.stats = {
            "dds_scanned": 0,
            "missing_requested": 0,
            "diffuse_converted": 0,
            "nohq_converted": 0,
            "smdi_converted": 0,
            "paa_converted": 0,
            "rvmat_created": 0,
            "skipped_existing": 0,
            "source_not_found": 0,
            "failed": 0,
        }
        self.failed = []
        self.missing_sources = []
        self.failed_items = []
        self.exported_diffuse = []
        self.exported_nohq = []
        self.exported_smdi = []
        self.exported_paa = []
        self.created_rvmat = []
        self.skipped_existing = []
        self.warnings = []
        self.paa_warning_emitted = False
        self.dds_disabled_warned = False
        self.rebuilt_count = None
        self.cancelled = False

    def execute(self, context):
        from .nh_base import (_fmt_exc, _save_texreplace_settings_now)
        from .nh_collider_exp import (_scan_source_dds_files_from_roots, _tex_export_resolve_path, _tex_export_source_roots_from_settings, _texture_tools_folder_from_settings)
        from .nh_snap import (_tag_redraw_all_areas)
        ts = context.scene.cray_texreplace_settings
        if bool(getattr(ts, "texture_export_is_running", False)):
            self.report({"WARNING"}, "Texture export is already running")
            return {"CANCELLED"}

        self._init_runtime()
        self.dds_backend = str(getattr(ts, "dds_backend", "BUILTIN_PYTHON") or "BUILTIN_PYTHON")
        try:
            ts.texture_export_cancel_requested = False
            ts.texture_export_last_summary = ""
            ts.texture_export_last_report_path = ""
        except Exception:
            pass
        _save_texreplace_settings_now(context)

        self.source_roots = _tex_export_source_roots_from_settings(ts)
        self.source_root = "; ".join(self.source_roots)
        self.target_root = _tex_export_resolve_path(ts.target_textures_folder, fallback=ts.folder)

        _tex_export_log_event(
            self.events,
            "INFO",
            "EXPORT_START",
            "Texture source export started",
            source_root=self.source_root,
            source_roots=self.source_roots,
            target_root=self.target_root,
            texture_tools_folder=_texture_tools_folder_from_settings(ts),
            convert_dds_to_png=bool(ts.convert_dds_to_png),
            dds_backend=str(getattr(ts, "dds_backend", "BUILTIN_PYTHON") or "BUILTIN_PYTHON"),
            python_converter_path=_get_bundled_python_dds_converter_path(ts),
            convert_png_to_paa=bool(ts.convert_png_to_paa),
            image_to_paa_path=_tex_export_resolve_path(ts.image_to_paa_path),
            generate_rvmat=bool(ts.generate_rvmat),
            export_only_missing=bool(ts.export_only_missing),
            export_overwrite_existing=bool(ts.export_overwrite_existing),
            delete_png_after_paa=bool(ts.delete_png_after_paa),
            output_diffuse_suffix="AUTO (_ca / _co)",
        )

        if not self.source_roots or not any(os.path.isdir(root) for root in self.source_roots):
            self._finish_export_logging(ts)
            self.report({"ERROR"}, "Source Textures Folder is not set or does not exist")
            return {"CANCELLED"}
        if not self.target_root:
            self._finish_export_logging(ts)
            self.report({"ERROR"}, "Target Textures Folder is not set")
            return {"CANCELLED"}
        try:
            os.makedirs(self.target_root, exist_ok=True)
        except Exception as e:
            self.report({"ERROR"}, f"Could not create target folder: {_fmt_exc(e)}")
            return {"CANCELLED"}

        self.obj, self.requests = _collect_tex_source_export_requests(context, ts, self.target_root)
        if self.obj is None:
            self._finish_export_logging(ts)
            self.report({"ERROR"}, "No mesh object found (pick one or select one)")
            return {"CANCELLED"}
        if not self.requests:
            self._finish_export_logging(ts)
            self.report({"WARNING"}, f"Object '{self.obj.name}' has no exportable texture candidates")
            return {"CANCELLED"}
        try:
            ts.picked_object = self.obj
        except Exception:
            pass

        _tex_export_set_progress(context, ts, 0, len(self.requests), "", "Scanning source DDS")
        self.dds_map, self.dds_scanned = _scan_source_dds_files_from_roots(self.source_roots)
        self.stats["dds_scanned"] = self.dds_scanned
        _tex_export_log_event(self.events, "INFO", "SOURCE_DDS_SCAN_DONE", "Source DDS scan finished", source_root=self.source_root, source_roots=self.source_roots, dds_scanned=self.dds_scanned)
        _tex_export_set_progress(context, ts, 0, len(self.requests), "", "Starting...")

        try:
            context.window_manager.progress_begin(0, len(self.requests))
        except Exception:
            pass
        try:
            self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
            context.window_manager.modal_handler_add(self)
        except Exception as e:
            self._cleanup_modal(context, ts)
            self.report({"ERROR"}, f"Could not start modal export: {_fmt_exc(e)}")
            return {"CANCELLED"}
        _tag_redraw_all_areas(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        from .nh_base import (_fmt_exc)
        from .nh_snap import (_tag_redraw_all_areas)
        ts = context.scene.cray_texreplace_settings
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if bool(getattr(ts, "texture_export_cancel_requested", False)):
            return self._finish_modal_export(context, cancelled=True)
        try:
            if self.index < len(self.requests):
                self.index += 1
                req = self.requests[self.index - 1]
                material_base = req.get("material_base", "")
                _tex_export_set_progress(context, ts, self.index, len(self.requests), material_base, "Resolving sources")
                _tex_export_workspace_status(context, f"NH Texture Export: {self.index}/{len(self.requests)} {material_base} - resolving sources")
                self._process_request(context, ts, req, self.index)
                try:
                    context.window_manager.progress_update(self.index)
                except Exception:
                    pass
                _tag_redraw_all_areas(context)
                return {"RUNNING_MODAL"}
            return self._finish_modal_export(context)
        except Exception as e:
            self._add_failed("export", "Texture export", "", self.target_root, _fmt_exc(e))
            self.stats["failed"] += 1
            _tex_export_log_event(self.events, "ERROR", "EXPORT_MODAL_FAILED", "Texture export modal step failed", exception=_fmt_exc(e))
            return self._finish_modal_export(context, error=e)

    def _cleanup_modal(self, context, ts):
        from .nh_snap import (_tag_redraw_all_areas)
        try:
            if self._timer is not None:
                context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass
        self._timer = None
        try:
            context.window_manager.progress_end()
        except Exception:
            pass
        _tex_export_workspace_status(context, None)
        _tex_export_finish_progress(context, ts)
        try:
            ts.texture_export_cancel_requested = False
        except Exception:
            pass
        _tag_redraw_all_areas(context)

    def _finish_export_logging(self, ts):
        written, log_errors = _write_texture_export_logs(ts, self.target_root, self.events)
        for path in written:
            _tex_export_log_event(self.events, "INFO", "LOG_FILE_WRITTEN", "Texture export log file written", path=path)
        for err in log_errors:
            _tex_export_log_event(self.events, "WARNING", "LOG_FILE_WRITE_FAILED", "Texture export log file was not written", error=err)
            print(f"Texture export error log was not written: {err}")
        for path in written:
            print(f"Texture export error log: {path}")

    def _expected_out_path(self, ts, png_path, paa_path):
        return paa_path if ts.convert_png_to_paa else png_path

    def _note_missing_output(self, path):
        if path and not os.path.exists(path):
            self.stats["missing_requested"] += 1

    def _add_created(self, kind, material_base, source, output):
        item = _tex_export_result_item(material_base, kind, source, output, self.target_root)
        if kind == "diffuse" and _tex_export_is_png_path(output):
            _tex_export_append_unique(self.exported_diffuse, item)
        elif kind == "nohq" and _tex_export_is_png_path(output):
            _tex_export_append_unique(self.exported_nohq, item)
        elif kind == "smdi" and _tex_export_is_png_path(output):
            _tex_export_append_unique(self.exported_smdi, item)
        elif kind == "paa" and _tex_export_is_paa_path(output):
            _tex_export_append_unique(self.exported_paa, item)
        elif kind == "rvmat" and _tex_export_is_rvmat_path(output):
            _tex_export_append_unique(self.created_rvmat, item)
        return item

    def _add_skipped(self, kind, material_base, output, reason):
        _tex_export_append_unique(self.skipped_existing, _tex_export_result_item(material_base, kind, "", output, self.target_root, reason=reason))

    def _add_failed(self, kind, material_base, source, output, reason):
        item = _tex_export_result_item(material_base, kind, source, output, self.target_root, reason=reason)
        _tex_export_append_unique(self.failed_items, item)
        self.failed.append(f"{material_base}: {kind}: {reason}")
        return item

    def _note_missing_source(self, expected_path, rel_dir, tried_names, material_base="", kind=""):
        from .nh_collider_exp import (_to_dayz_relative_texture_path)
        tried = _tex_export_source_tried_lines(self.source_roots or self.source_root, rel_dir, tried_names)
        item = _tex_export_result_item(material_base, kind, "", expected_path, self.target_root, reason="source DDS not found")
        item["expected"] = _to_dayz_relative_texture_path(expected_path, self.target_root)
        item["tried"] = tried
        _tex_export_append_unique(self.missing_sources, item)
        self.stats["source_not_found"] += 1

    def _convert_channel(self, context, ts, label, mode, source_item, png_path, paa_path, rel_dir, tried_names, material_base):
        from .nh_base import (_fmt_exc)
        from .nh_collider_exp import (_tex_export_existing_preferred, _tex_export_resolve_path, _tex_export_should_write)
        mode_label = {"diffuse": "diffuse", "nohq": "NOHQ", "smdi": "SMDI"}.get(mode, mode)
        final_path = _tex_export_existing_preferred(png_path, paa_path, bool(ts.convert_png_to_paa))
        if final_path:
            _tex_export_log_event(self.events, "INFO", "CHANNEL_EXISTING_FOUND", "Existing output can satisfy this channel", label=label, material_base=material_base, mode=mode, final_path=final_path)

        if not source_item:
            if final_path and not bool(ts.export_overwrite_existing) and (not ts.convert_png_to_paa or os.path.isfile(paa_path)):
                self.stats["skipped_existing"] += 1
                self._add_skipped(mode, material_base, final_path, "Existing output can satisfy channel")
                return final_path
            if ts.convert_png_to_paa and os.path.isfile(png_path) and _tex_export_should_write(paa_path, ts):
                exe_path = _tex_export_resolve_path(ts.image_to_paa_path)
                if not exe_path or not os.path.isfile(exe_path):
                    if not self.paa_warning_emitted:
                        self.warnings.append("ImageToPAA not found, PAA conversion skipped")
                        self.paa_warning_emitted = True
                    return final_path
                try:
                    _tex_export_set_progress(context, ts, self.index, len(self.requests), material_base, f"PNG -> PAA {mode_label}")
                    _tex_export_workspace_status(context, f"NH Texture Export: {self.index}/{len(self.requests)} {material_base} - PNG -> PAA {mode_label}")
                    _convert_png_to_paa_external(png_path, paa_path, exe_path, events=self.events, label=label, material_base=material_base)
                    try:
                        _ensure_texture_cache_for_paa(paa_path, force_rebuild=True)
                    except Exception as cache_e:
                        self.warnings.append(f"Could not update texture preview cache: {paa_path}: {_fmt_exc(cache_e)}")
                    self.stats["paa_converted"] += 1
                    self._add_created("paa", material_base, png_path, paa_path)
                    return paa_path
                except Exception as e:
                    self.warnings.append(_fmt_exc(e))
                    self._add_failed("paa", material_base, png_path, paa_path, _fmt_exc(e))
                    self.stats["failed"] += 1
                    return final_path
            self._note_missing_source(self._expected_out_path(ts, png_path, paa_path), rel_dir, tried_names, material_base=material_base, kind=mode)
            return final_path

        if not ts.convert_dds_to_png:
            if not self.dds_disabled_warned:
                self.warnings.append("DDS conversion backend is not available: Convert DDS to PNG is disabled")
                self.dds_disabled_warned = True
            return final_path

        png_written_or_present = os.path.isfile(png_path)
        if _tex_export_should_write(png_path, ts):
            try:
                _tex_export_set_progress(context, ts, self.index, len(self.requests), material_base, f"DDS -> PNG {mode_label}")
                _tex_export_workspace_status(context, f"NH Texture Export: {self.index}/{len(self.requests)} {material_base} - DDS -> PNG {mode_label}")
                _convert_dds_to_png_export(source_item["path"], png_path, mode, ts, events=self.events, material_base=material_base)
                png_written_or_present = True
                if mode == "diffuse":
                    self.stats["diffuse_converted"] += 1
                elif mode == "nohq":
                    self.stats["nohq_converted"] += 1
                elif mode == "smdi":
                    self.stats["smdi_converted"] += 1
                if os.path.isfile(png_path):
                    self._add_created(mode, material_base, source_item["path"], png_path)
            except Exception as e:
                self._add_failed(mode, material_base, source_item["path"], png_path, _fmt_exc(e))
                self.stats["failed"] += 1
                _tex_export_log_event(self.events, "ERROR", "DDS_CONVERT_FAILED", "DDS conversion failed", label=label, material_base=material_base, mode=mode, source_dds=source_item["path"], target_png=png_path, exception=_fmt_exc(e), backend="BUILTIN_PYTHON")
        else:
            self.stats["skipped_existing"] += 1
            self._add_skipped(mode, material_base, png_path, "PNG exists and overwrite is disabled")

        if os.path.isfile(png_path) or png_written_or_present:
            final_path = png_path
        if not ts.convert_png_to_paa:
            return final_path if os.path.exists(final_path or "") else None

        exe_path = _tex_export_resolve_path(ts.image_to_paa_path)
        if not exe_path or not os.path.isfile(exe_path):
            if not self.paa_warning_emitted:
                self.warnings.append("ImageToPAA not found, PAA conversion skipped")
                self.paa_warning_emitted = True
            return final_path if os.path.exists(final_path or "") else None
        if not (os.path.isfile(png_path) or png_written_or_present):
            self._add_failed("paa", material_base, png_path, paa_path, f"PAA conversion skipped because PNG input is missing: {png_path}")
            self.stats["failed"] += 1
            return final_path
        if _tex_export_should_write(paa_path, ts):
            try:
                _tex_export_set_progress(context, ts, self.index, len(self.requests), material_base, f"PNG -> PAA {mode_label}")
                _tex_export_workspace_status(context, f"NH Texture Export: {self.index}/{len(self.requests)} {material_base} - PNG -> PAA {mode_label}")
                _convert_png_to_paa_external(png_path, paa_path, exe_path, events=self.events, label=label, material_base=material_base)
                try:
                    _ensure_texture_cache_for_paa(paa_path, force_rebuild=True)
                except Exception as cache_e:
                    self.warnings.append(f"Could not update texture preview cache: {paa_path}: {_fmt_exc(cache_e)}")
                self.stats["paa_converted"] += 1
                final_path = paa_path
                self._add_created("paa", material_base, png_path, paa_path)
                if ts.delete_png_after_paa and os.path.isfile(png_path):
                    try:
                        os.remove(png_path)
                    except Exception as e:
                        self.warnings.append(f"Could not delete PNG after PAA: {png_path}: {_fmt_exc(e)}")
            except Exception as e:
                self.warnings.append(_fmt_exc(e))
                self._add_failed("paa", material_base, png_path, paa_path, _fmt_exc(e))
                self.stats["failed"] += 1
                final_path = png_path if os.path.isfile(png_path) else None
        else:
            self.stats["skipped_existing"] += 1
            self._add_skipped("paa", material_base, paa_path, "PAA exists and overwrite is disabled")
            if os.path.isfile(paa_path):
                final_path = paa_path
        return final_path if os.path.exists(final_path or "") else None

    def _process_request(self, context, ts, req, idx):
        from .nh_base import (_fmt_exc)
        from .nh_collider_exp import (_base_color_stem, _find_tex_export_base_color_dds, _find_tex_export_dds, _generate_dayz_super_rvmat, _norm_path, _sanitize_tex_export_base, _strip_tex_export_suffixes, _tex_export_base_color_suffix, _tex_export_base_color_tried_names, _tex_export_should_write)
        material_base = req["material_base"]
        nohq_base = _sanitize_tex_export_base(material_base + "_nohq")
        smdi_base = _sanitize_tex_export_base(material_base + "_smdi")
        rvmat_base = _strip_tex_export_suffixes(material_base)
        preferred_rel_dir = req.get("expected_rel_dir") or ""
        diffuse_src = _find_tex_export_base_color_dds(
            self.dds_map,
            material_base,
            preferred_rel_dir=preferred_rel_dir,
        )
        bump_src = _find_tex_export_dds(self.dds_map, material_base + "_bump", preferred_rel_dir=preferred_rel_dir)
        if diffuse_src:
            print(f"DDS source found: {diffuse_src.get('path')}")
        if bump_src and (not diffuse_src or bump_src.get("path") != diffuse_src.get("path")):
            print(f"DDS source found: {bump_src.get('path')}")
        rel_dir = req.get("expected_rel_dir") or (diffuse_src or {}).get("rel_dir") or (bump_src or {}).get("rel_dir") or ""
        target_dir = os.path.normpath(os.path.join(self.target_root, rel_dir)) if rel_dir else self.target_root
        diffuse_suffix = _tex_export_base_color_suffix(diffuse_src, target_dir, material_base)
        diffuse_base = _sanitize_tex_export_base(_base_color_stem(material_base) + diffuse_suffix)
        print(f"Base Color selected: {diffuse_base} ({'alpha' if diffuse_suffix == '_ca' else 'opaque'})")
        diffuse_png = _norm_path(os.path.join(target_dir, diffuse_base + ".png"))
        diffuse_paa = _norm_path(os.path.join(target_dir, diffuse_base + ".paa"))
        nohq_png = _norm_path(os.path.join(target_dir, nohq_base + ".png"))
        nohq_paa = _norm_path(os.path.join(target_dir, nohq_base + ".paa"))
        smdi_png = _norm_path(os.path.join(target_dir, smdi_base + ".png"))
        smdi_paa = _norm_path(os.path.join(target_dir, smdi_base + ".paa"))
        rvmat_path = _norm_path(os.path.join(target_dir, rvmat_base + ".rvmat"))
        self._note_missing_output(self._expected_out_path(ts, diffuse_png, diffuse_paa))
        self._note_missing_output(self._expected_out_path(ts, nohq_png, nohq_paa))
        self._note_missing_output(self._expected_out_path(ts, smdi_png, smdi_paa))
        if ts.generate_rvmat:
            self._note_missing_output(rvmat_path)
        diffuse_path = self._convert_channel(
            context,
            ts,
            f"{material_base}: diffuse",
            "diffuse",
            diffuse_src,
            diffuse_png,
            diffuse_paa,
            rel_dir,
            _tex_export_base_color_tried_names(material_base),
            material_base,
        )
        nohq_path = self._convert_channel(context, ts, f"{material_base}: NOHQ", "nohq", bump_src, nohq_png, nohq_paa, rel_dir, (material_base + "_bump",), material_base)
        smdi_path = self._convert_channel(context, ts, f"{material_base}: SMDI", "smdi", bump_src, smdi_png, smdi_paa, rel_dir, (material_base + "_bump",), material_base)
        if ts.generate_rvmat:
            if _tex_export_should_write(rvmat_path, ts):
                try:
                    _tex_export_set_progress(context, ts, idx, len(self.requests), material_base, "Creating RVMAT")
                    _tex_export_workspace_status(context, f"NH Texture Export: {idx}/{len(self.requests)} {material_base} - creating RVMAT")
                    _generate_dayz_super_rvmat(rvmat_path, co_path=diffuse_path, nohq_path=nohq_path if nohq_path and os.path.exists(nohq_path) else None, smdi_path=smdi_path if smdi_path and os.path.exists(smdi_path) else None, target_root=self.target_root, warnings=self.warnings)
                    self.stats["rvmat_created"] += 1
                    self._add_created("rvmat", material_base, "", rvmat_path)
                except Exception as e:
                    self._add_failed("rvmat", material_base, "", rvmat_path, _fmt_exc(e))
                    self.stats["failed"] += 1
            else:
                self.stats["skipped_existing"] += 1
                self._add_skipped("rvmat", material_base, rvmat_path, "RVMAT exists and overwrite is disabled")

    def _finalize_lists(self):
        self.exported_diffuse = _tex_export_filter_unique_items(self.exported_diffuse, _tex_export_is_png_path)
        self.exported_nohq = _tex_export_filter_unique_items(self.exported_nohq, _tex_export_is_png_path)
        self.exported_smdi = _tex_export_filter_unique_items(self.exported_smdi, _tex_export_is_png_path)
        self.exported_paa = _tex_export_filter_unique_items(self.exported_paa, _tex_export_is_paa_path)
        self.created_rvmat = _tex_export_filter_unique_items(self.created_rvmat, _tex_export_is_rvmat_path)
        self.skipped_existing = _tex_export_filter_unique_items(self.skipped_existing, lambda path: bool(path))
        self.missing_sources = _tex_export_filter_unique_items(self.missing_sources, lambda path: bool(path))
        self.failed_items = _tex_export_filter_unique_items(self.failed_items, lambda path: bool(path))

    def _finish_modal_export(self, context, cancelled=False, error=None):
        from .nh_base import (_fmt_exc, _save_texreplace_settings_now)
        from .nh_collider_exp import (_unique_ci)
        ts = context.scene.cray_texreplace_settings
        self.cancelled = bool(cancelled)
        try:
            if self.target_root:
                ts.folder = self.target_root
                self.rebuilt_count = len(_tex_export_refresh_db(ts, self.target_root))
                auto_updated = _auto_select_object_base_color_paths(self.obj, ts)
                if auto_updated:
                    print(f"Base Color paths auto-selected after export: {auto_updated}")
                    try:
                        preview_stats = _postprocess_imported_material_previews(
                            context,
                            [self.obj],
                            show_materials=True,
                            keep_converted_textures=True,
                            pack_runtime_images=False,
                        )
                        _log_import_preview_summary(self.obj.name, preview_stats)
                    except Exception as preview_e:
                        self.warnings.append(f"Material preview refresh failed: {_fmt_exc(preview_e)}")
        except Exception as e:
            self._add_failed("db", "DB rebuild", "", self.target_root, _fmt_exc(e))
            self.stats["failed"] += 1
        self._finalize_lists()
        summary = {"source_root": self.source_root, "target_root": self.target_root, "dds_backend": _dds_backend_display_name(getattr(self, "dds_backend", "BUILTIN_PYTHON")), "dds_scanned": self.stats["dds_scanned"], "missing_requested": self.stats["missing_requested"], "diffuse_converted": self.stats["diffuse_converted"], "nohq_converted": self.stats["nohq_converted"], "smdi_converted": self.stats["smdi_converted"], "paa_converted": self.stats["paa_converted"], "rvmat_created": self.stats["rvmat_created"], "skipped_existing": self.stats["skipped_existing"], "source_not_found": self.stats["source_not_found"], "failed": self.stats["failed"], "db_rebuilt": self.rebuilt_count, "cancelled": self.cancelled}
        print("=== Texture Source Export Summary ===")
        for key, label in (("source_root", "Source root"), ("target_root", "Target root"), ("dds_backend", "DDS Backend"), ("dds_scanned", "DDS scanned"), ("missing_requested", "Missing requested"), ("diffuse_converted", "Diffuse converted"), ("nohq_converted", "NOHQ converted"), ("smdi_converted", "SMDI converted"), ("paa_converted", "PAA converted"), ("rvmat_created", "RVMAT created"), ("skipped_existing", "Skipped existing"), ("source_not_found", "Source not found"), ("failed", "Failed"), ("db_rebuilt", "DB rebuilt"), ("cancelled", "Cancelled")):
            if summary.get(key) is not None:
                print(f"{label}: {summary.get(key)}")
        _print_texture_export_backend_summary(self.events)
        print("=== Texture Source Export Created Files ===")
        _print_texture_export_created_section("Diffuse", self.exported_diffuse)
        _print_texture_export_created_section("NOHQ", self.exported_nohq)
        _print_texture_export_created_section("SMDI", self.exported_smdi)
        _print_texture_export_created_section("PAA", self.exported_paa)
        _print_texture_export_created_section("RVMAT", self.created_rvmat)
        if self.warnings:
            print("=== Texture Source Export Warnings ===")
            for item in _unique_ci(self.warnings):
                print(item)
        if self.skipped_existing:
            print("=== Texture Source Export Skipped Existing ===")
            for item in self.skipped_existing[:50]:
                print(f"- {item.get('relative_output') or item.get('output') or '<unknown>'}")
            if len(self.skipped_existing) > 50:
                print(f"... {len(self.skipped_existing) - 50} more skipped existing not shown")
        if self.missing_sources:
            print("=== Texture Source Export Missing Sources ===")
            for item in self.missing_sources[:50]:
                print(f"expected: {item.get('expected') or item.get('relative_output')}")
                print("source tried:")
                for tried in item.get("tried", []):
                    print(tried)
            if len(self.missing_sources) > 50:
                print(f"... {len(self.missing_sources) - 50} more missing source(s) not shown")
        if self.failed_items:
            print("=== Texture Source Export Failed ===")
            for item in self.failed_items[:100]:
                print(f"- {item.get('relative_output') or item.get('output') or item.get('material_base')}: {item.get('reason')}")
            if len(self.failed_items) > 100:
                print(f"... {len(self.failed_items) - 100} more failure(s) not shown")
        try:
            report_txt_path = _write_texture_export_last_report(ts, summary, self.exported_diffuse, self.exported_nohq, self.exported_smdi, self.exported_paa, self.created_rvmat, self.skipped_existing, self.missing_sources, self.failed_items)
            ts.texture_export_last_report_path = report_txt_path
            cancelled_prefix = "Cancelled, " if self.cancelled else ""
            ts.texture_export_last_summary = f"{cancelled_prefix}Diffuse {summary['diffuse_converted']}, NOHQ {summary['nohq_converted']}, SMDI {summary['smdi_converted']}, PAA {summary['paa_converted']}, RVMAT {summary['rvmat_created']}, Failed {summary['failed']}"
            print(f"Texture export report: {report_txt_path}")
        except Exception as e:
            print(f"Could not write last export report: {_fmt_exc(e)}")
        self._finish_export_logging(ts)
        self._cleanup_modal(context, ts)
        _save_texreplace_settings_now(context)
        if error is not None:
            self.report({"ERROR"}, f"Texture export failed: {_fmt_exc(error)}")
            return {"CANCELLED"}
        if self.cancelled:
            self.report({"WARNING"}, "Texture export cancelled. Partial report was written.")
            return {"CANCELLED"}
        if self.stats["failed"]:
            self.report({"WARNING"}, f"Texture export finished with {self.stats['failed']} failure(s). See System Console.")
        elif self.missing_sources or self.warnings:
            self.report({"WARNING"}, "Texture export finished with warnings. See System Console.")
        else:
            self.report({"INFO"}, "Texture export finished")
        return {"FINISHED"}

class CRAY_OT_CancelTextureExport(Operator):
    bl_idname = "cray.cancel_texture_export"
    bl_label = "Cancel Export"
    bl_options = {"REGISTER"}

    def execute(self, context):
        ts = context.scene.cray_texreplace_settings
        try:
            ts.texture_export_cancel_requested = True
        except Exception:
            pass
        self.report({"INFO"}, "Texture export cancellation requested")
        return {"FINISHED"}

class CRAY_OT_PrintTextureExportDiagnostics(Operator):
    bl_idname = "cray.print_texture_export_diagnostics"
    bl_label = "Print Export Diagnostics"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .nh_base import (_save_texreplace_settings_now)
        from .nh_collider_exp import (_base_color_stem, _find_tex_export_base_color_dds, _find_tex_export_dds, _norm_path, _sanitize_tex_export_base, _scan_source_dds_files_from_roots, _strip_tex_export_suffixes, _tex_export_base_color_suffix, _tex_export_base_color_tried_names, _tex_export_resolve_path, _tex_export_source_roots_from_settings)
        ts = context.scene.cray_texreplace_settings
        _save_texreplace_settings_now(context)
        source_roots = _tex_export_source_roots_from_settings(ts)
        source_root = "; ".join(source_roots)
        target_root = _tex_export_resolve_path(ts.target_textures_folder, fallback=ts.folder)
        obj, requests = _collect_tex_source_export_requests(context, ts, target_root)

        print("=== Texture Source Export Quick Diagnostics ===")
        print(f"Source root: {source_root}")
        print(f"Target root: {target_root}")
        print(f"Selected object: {obj.name if obj else '<none>'}")
        print(f"Export requests: {len(requests)}")

        if not obj:
            self.report({"ERROR"}, "No mesh object found (pick one or select one)")
            return {"CANCELLED"}

        if source_roots and any(os.path.isdir(root) for root in source_roots):
            dds_map, dds_scanned = _scan_source_dds_files_from_roots(source_roots)
            print(f"DDS scanned: {dds_scanned}")
        else:
            dds_map = {}
            print("DDS scanned: 0 (source root is missing)")

        for idx, req in enumerate(requests[:20], start=1):
            material_base = req["material_base"]
            nohq_base = _sanitize_tex_export_base(material_base + "_nohq")
            smdi_base = _sanitize_tex_export_base(material_base + "_smdi")
            rvmat_base = _strip_tex_export_suffixes(material_base)
            preferred_rel_dir = req.get("expected_rel_dir") or ""
            diffuse_src = _find_tex_export_base_color_dds(
                dds_map,
                material_base,
                preferred_rel_dir=preferred_rel_dir,
            )
            bump_src = _find_tex_export_dds(dds_map, material_base + "_bump", preferred_rel_dir=preferred_rel_dir)
            if diffuse_src:
                print(f"DDS source found: {diffuse_src.get('path')}")
            if bump_src and (not diffuse_src or bump_src.get("path") != diffuse_src.get("path")):
                print(f"DDS source found: {bump_src.get('path')}")
            rel_dir = (
                req.get("expected_rel_dir")
                or (diffuse_src or {}).get("rel_dir")
                or (bump_src or {}).get("rel_dir")
                or ""
            )
            target_dir = os.path.normpath(os.path.join(target_root, rel_dir)) if rel_dir else target_root
            diffuse_suffix = _tex_export_base_color_suffix(diffuse_src, target_dir, material_base)
            diffuse_base = _sanitize_tex_export_base(_base_color_stem(material_base) + diffuse_suffix)
            diffuse_png = _norm_path(os.path.join(target_dir, diffuse_base + ".png"))
            diffuse_paa = _norm_path(os.path.join(target_dir, diffuse_base + ".paa"))
            nohq_png = _norm_path(os.path.join(target_dir, nohq_base + ".png"))
            nohq_paa = _norm_path(os.path.join(target_dir, nohq_base + ".paa"))
            smdi_png = _norm_path(os.path.join(target_dir, smdi_base + ".png"))
            smdi_paa = _norm_path(os.path.join(target_dir, smdi_base + ".paa"))
            rvmat_path = _norm_path(os.path.join(target_dir, rvmat_base + ".rvmat"))

            print(f"[{idx}] material_base: {material_base}")
            print(f"  expected_rel_dir: {preferred_rel_dir}")
            print(f"  diffuse source: {(diffuse_src or {}).get('path') or '<not found>'}")
            if not diffuse_src:
                print("  diffuse tried:")
                for tried in _tex_export_source_tried_lines(
                    source_roots,
                    preferred_rel_dir,
                    _tex_export_base_color_tried_names(material_base),
                ):
                    print(f"    {tried}")
            print(f"  Base Color suffix: {diffuse_suffix} ({'alpha' if diffuse_suffix == '_ca' else 'opaque'})")
            print(f"  bump source: {(bump_src or {}).get('path') or '<not found>'}")
            if not bump_src:
                print("  bump tried:")
                for tried in _tex_export_source_tried_lines(source_roots, preferred_rel_dir, (material_base + '_bump',)):
                    print(f"    {tried}")
            print(f"  diffuse png: {diffuse_png}")
            print(f"  diffuse paa: {diffuse_paa}")
            print(f"  nohq png: {nohq_png}")
            print(f"  nohq paa: {nohq_paa}")
            print(f"  smdi png: {smdi_png}")
            print(f"  smdi paa: {smdi_paa}")
            print(f"  rvmat: {rvmat_path}")

        if len(requests) > 20:
            print(f"... {len(requests) - 20} more request(s) not shown")

        self.report({"INFO"}, f"Printed {min(len(requests), 20)} texture export request(s) to System Console")
        return {"FINISHED"}

class CRAY_OT_PrintTextureConverterDiagnostics(Operator):
    bl_idname = "cray.print_texture_converter_diagnostics"
    bl_label = "Print Converter Diagnostics"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .nh_base import (_save_texreplace_settings_now)
        from .nh_collider_exp import (_get_addon_dir, _get_bundled_xray_converter_js, _get_expected_python_dds_converter_paths, _tex_export_resolve_path, _texture_tools_folder_from_settings)
        ts = context.scene.cray_texreplace_settings
        _save_texreplace_settings_now(context)

        print("=== Texture Converter Diagnostics ===")
        print(f"addon_dir: {_get_addon_dir()}")
        print(f"texture_tools_folder: {_texture_tools_folder_from_settings(ts) or '<empty>'}")
        print("expected_python_converter_paths:")
        for path in _get_expected_python_dds_converter_paths(ts):
            print(f"  exists={bool(os.path.isfile(path))}: {path}")
        print(f"python_converter_path: {_get_bundled_python_dds_converter_path(ts) or '<missing>'}")
        print(f"bundled_exe_path: {_get_bundled_dds_converter_exe(ts) or '<missing>'}")
        print(f"converter_js_path: {_get_bundled_xray_converter_js(ts) or '<missing>'}")
        print(f"node_exe_found: {_find_node_exe(ts) or '<missing>'}")
        print(f"external_converter_path: {_tex_export_resolve_path(ts.external_dds_converter_path) or '<empty>'}")
        print(f"dds_backend: {_dds_backend_display_name(getattr(ts, 'dds_backend', 'BUILTIN_PYTHON'))}")
        print(f"image_to_paa_path: {_tex_export_resolve_path(ts.image_to_paa_path) or '<empty>'}")

        self.report({"INFO"}, "Printed converter diagnostics to System Console")
        return {"FINISHED"}

def _open_folder_in_system(path: str):
    if hasattr(os, "startfile"):
        os.startfile(path)
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
        return
    subprocess.Popen(["xdg-open", path])

class CRAY_OT_OpenTextureExportLastReport(Operator):
    bl_idname = "cray.open_texture_export_last_report"
    bl_label = "Open Last Export Report"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider_exp import (_tex_export_resolve_path)
        ts = context.scene.cray_texreplace_settings
        path = _tex_export_resolve_path(getattr(ts, "texture_export_last_report_path", ""))
        if not path or not os.path.isfile(path):
            self.report({"WARNING"}, "Last export report file was not found")
            return {"CANCELLED"}
        try:
            _open_folder_in_system(path)
        except Exception as e:
            self.report({"ERROR"}, f"Could not open export report: {_fmt_exc(e)}")
            return {"CANCELLED"}
        self.report({"INFO"}, "Opened last export report")
        return {"FINISHED"}

class CRAY_OT_OpenExpectedTextureToolsFolder(Operator):
    bl_idname = "cray.open_expected_texture_tools_folder"
    bl_label = "Open Expected Tools Folder"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .nh_base import (_fmt_exc, _save_texreplace_settings_now)
        from .nh_collider_exp import (_expected_texture_tools_folder)
        ts = context.scene.cray_texreplace_settings
        _save_texreplace_settings_now(context)
        folder = _expected_texture_tools_folder(ts)
        try:
            os.makedirs(folder, exist_ok=True)
            _open_folder_in_system(folder)
        except Exception as e:
            self.report({"ERROR"}, f"Could not open tools folder: {_fmt_exc(e)}")
            return {"CANCELLED"}

        print("=== Expected Texture Tools Folder ===")
        print(f"folder: {folder}")
        print("Put converters here as:")
        print(f"  {os.path.join(folder, 'xray_tex_converter', 'dds_python.py')}")
        print(f"  {os.path.join(folder, 'xray_tex_converter', 'converter.js')}")
        self.report({"INFO"}, "Opened expected texture tools folder")
        return {"FINISHED"}

class CRAY_OT_CleanTextureConverterTestOutputs(Operator):
    bl_idname = "cray.clean_texture_converter_test_outputs"
    bl_label = "Clean Test Converter Outputs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_base import (_fmt_exc, _save_texreplace_settings_now)
        from .nh_collider_exp import (_is_texture_converter_test_output, _norm_path, _tex_export_resolve_path)
        ts = context.scene.cray_texreplace_settings
        _save_texreplace_settings_now(context)
        target_root = _tex_export_resolve_path(ts.target_textures_folder, fallback=ts.folder)
        if not target_root or not os.path.isdir(target_root):
            self.report({"ERROR"}, "Target Textures Folder is not set or does not exist")
            return {"CANCELLED"}

        removed = []
        failed = []
        for root, _, files in os.walk(target_root):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in {".png", ".paa"}:
                    continue
                if not _is_texture_converter_test_output(fn):
                    continue
                path = os.path.join(root, fn)
                try:
                    os.remove(path)
                    removed.append(_norm_path(path))
                except Exception as e:
                    failed.append(f"{_norm_path(path)}: {_fmt_exc(e)}")

        print("=== Texture Converter Test Output Cleanup ===")
        print(f"Target root: {target_root}")
        print(f"Removed: {len(removed)}")
        print(f"Failed: {len(failed)}")
        if removed:
            for path in removed[:100]:
                print(path)
            if len(removed) > 100:
                print(f"... {len(removed) - 100} more removed file(s) not shown")
        if failed:
            print("=== Cleanup Failed ===")
            for item in failed[:100]:
                print(item)
            if len(failed) > 100:
                print(f"... {len(failed) - 100} more failure(s) not shown")
            self.report({"WARNING"}, f"Removed {len(removed)} test output(s), failed {len(failed)}. See System Console.")
        else:
            self.report({"INFO"}, f"Removed {len(removed)} test output(s)")
        return {"FINISHED"}

class CRAY_OT_TextureCacheBuild(Operator):
    bl_idname = "cray.texture_cache_build"
    bl_label = "Build Texture PNG Cache"
    bl_description = "РЎРєР°РЅРёСЂСѓРµС‚ РІС‹Р±СЂР°РЅРЅСѓСЋ РїР°РїРєСѓ С‚РµРєСЃС‚СѓСЂ. Update РґРѕР±Р°РІР»СЏРµС‚ С‚РѕР»СЊРєРѕ РѕС‚СЃСѓС‚СЃС‚РІСѓСЋС‰РёРµ РёР»Рё СѓСЃС‚Р°СЂРµРІС€РёРµ PNG, Rebuild All РїРµСЂРµСЃРѕР·РґР°РµС‚ РІРµСЃСЊ РєРµС€"
    bl_options = {"REGISTER", "UNDO"}

    missing_only: BoolProperty(
        name="РўРѕР»СЊРєРѕ РЅРѕРІС‹Рµ/СѓСЃС‚Р°СЂРµРІС€РёРµ",
        default=False,
        description="РџСЂРѕРїСѓСЃРєР°С‚СЊ СѓР¶Рµ РІР°Р»РёРґРЅС‹Рµ PNG Рё РєРѕРЅРІРµСЂС‚РёСЂРѕРІР°С‚СЊ С‚РѕР»СЊРєРѕ РѕС‚СЃСѓС‚СЃС‚РІСѓСЋС‰РёРµ РёР»Рё СѓСЃС‚Р°СЂРµРІС€РёРµ .paa",
    )

    def execute(self, context):
        from .nh_base import (_fmt_exc, _save_texreplace_settings_now)
        from .nh_collider_exp import (_tex_export_resolve_path)
        ts = context.scene.cray_texreplace_settings
        _save_texreplace_settings_now(context)
        root = _tex_export_resolve_path(getattr(ts, "texture_cache_source_folder", ""), fallback=getattr(ts, "folder", ""))
        if not root or not os.path.isdir(root):
            self.report({"ERROR"}, "Texture cache source folder is not set or does not exist")
            return {"CANCELLED"}

        paa_files = list(_iter_paa_files_recursive(root))
        if not paa_files:
            ts.texture_cache_last_summary = "No .paa files found"
            self.report({"WARNING"}, "No .paa files found in texture cache source")
            return {"CANCELLED"}

        root_abs = os.path.abspath(bpy.path.abspath(root))
        force_rebuild = not bool(self.missing_only)

        try:
            cache_stats = _run_texture_cache_workers(
                paa_files,
                force_rebuild=force_rebuild,
                settings=ts,
                context=context,
            )
        except Exception as e:
            print("=== NH Texture PNG Cache: failed to start/update cache ===")
            print(_fmt_exc(e))
            self.report({"ERROR"}, f"Texture PNG cache failed: {_fmt_exc(e)}")
            return {"CANCELLED"}
        created = int(cache_stats.get("created", 0) or 0)
        rebuilt = int(cache_stats.get("rebuilt", 0) or 0)
        skipped = int(cache_stats.get("skipped", 0) or 0)
        failed = list(cache_stats.get("failed", []) or [])
        workers_used = int(cache_stats.get("workers", 1) or 1)

        cache_root = _nh_texture_cache_root(create=True)
        summary = f"Created {created}, rebuilt {rebuilt}, skipped {skipped}, failed {len(failed)}, workers {workers_used}"
        ts.texture_cache_last_summary = summary
        report_path = os.path.join(cache_root, "_nh_texture_cache_last_report.txt")
        try:
            with open(report_path, "w", encoding="utf-8", newline="\n") as f:
                f.write("=== NH Texture PNG Cache Report ===\n")
                f.write(f"Source root: {root_abs}\n")
                f.write(f"Cache root: {cache_root}\n")
                f.write(f"Mode: {'add new/outdated only' if self.missing_only else 'full rebuild'}\n")
                f.write(f"Workers: {workers_used}\n")
                f.write(f"Scanned .paa: {len(paa_files)}\n")
                f.write(f"Created: {created}\n")
                f.write(f"Rebuilt: {rebuilt}\n")
                f.write(f"Skipped valid: {skipped}\n")
                f.write(f"Failed: {len(failed)}\n")
                if failed:
                    f.write("\n=== Failed ===\n")
                    for item in failed:
                        f.write(item + "\n")
            ts.texture_cache_last_report_path = report_path
        except Exception as e:
            print(f"Could not write texture cache report: {_fmt_exc(e)}")

        print("=== NH Texture PNG Cache ===")
        print(f"Source root: {root_abs}")
        print(f"Cache root: {cache_root}")
        print(summary)
        if failed:
            print("=== Texture cache failed ===")
            for item in failed[:100]:
                print(item)
            if len(failed) > 100:
                print(f"... {len(failed) - 100} more failure(s) not shown")
            self.report({"WARNING"}, summary + " (see System Console)")
        else:
            self.report({"INFO"}, summary)
        _save_texreplace_settings_now(context)
        return {"FINISHED"}

def _cleanup_imported_data_since(pre_obj_ptrs, pre_col_ptrs, pre_mat_ptrs=None, pre_img_ptrs=None):
    from .nh_assets import (_remove_collection_tree)
    pre_obj_ptrs = set(pre_obj_ptrs or set())
    pre_col_ptrs = set(pre_col_ptrs or set())
    pre_mat_ptrs = set(pre_mat_ptrs or set())
    pre_img_ptrs = set(pre_img_ptrs or set())

    new_collection_names = [
        col.name for col in list(bpy.data.collections)
        if col.as_pointer() not in pre_col_ptrs
    ]
    for name in reversed(new_collection_names):
        col = bpy.data.collections.get(name)
        if col is None:
            continue
        try:
            _remove_collection_tree(col)
        except Exception:
            pass

    for obj in list(bpy.data.objects):
        try:
            if obj.as_pointer() not in pre_obj_ptrs:
                bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass

    for mat in list(bpy.data.materials):
        try:
            if mat.as_pointer() not in pre_mat_ptrs and int(getattr(mat, "users", 0) or 0) == 0:
                bpy.data.materials.remove(mat)
        except Exception:
            pass

    for image in list(bpy.data.images):
        try:
            if image.as_pointer() not in pre_img_ptrs and int(getattr(image, "users", 0) or 0) == 0:
                bpy.data.images.remove(image)
        except Exception:
            pass


def _write_texture_cache_report(source_label: str, mode_label: str, scanned: int, created: int, rebuilt: int, skipped: int, failed):
    from .nh_base import (_fmt_exc)
    cache_root = _nh_texture_cache_root(create=True)
    summary = f"Created {created}, rebuilt {rebuilt}, skipped {skipped}, failed {len(failed or [])}"
    report_path = os.path.join(cache_root, "_nh_texture_cache_last_report.txt")
    try:
        with open(report_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("=== NH Texture PNG Cache Report ===\n")
            f.write(f"Source: {source_label}\n")
            f.write(f"Cache root: {cache_root}\n")
            f.write(f"Mode: {mode_label}\n")
            f.write(f"Scanned .p3d/.paa: {scanned}\n")
            f.write(f"Created: {created}\n")
            f.write(f"Rebuilt: {rebuilt}\n")
            f.write(f"Skipped valid: {skipped}\n")
            f.write(f"Failed: {len(failed or [])}\n")
            if failed:
                f.write("\n=== Failed ===\n")
                for item in failed:
                    f.write(str(item) + "\n")
    except Exception as e:
        print(f"Could not write texture cache report: {_fmt_exc(e)}")
        report_path = ""
    return summary, report_path


def _cache_nh_library_used_textures(op, context, *, force_rebuild: bool = False):
    from .nh_assets import (_read_custom_asset_p3d_paths)
    from .nh_base import (_fmt_exc, _save_texreplace_settings_now)
    from .nh_model_split import (_iter_nh_objects_asset_source_folders, _iter_p3d_files_direct, _nh_objects_common_root, _nh_objects_environment_root)
    from .nh_snap import (_P3D_IMPORT_CANDIDATES, _call_first_available, _has_any_p3d_import_ops, _suppress_p3d_import_tracking)
    settings = context.scene.cray_asset_library_settings
    ts = context.scene.cray_texreplace_settings
    if not _has_any_p3d_import_ops():
        op.report({"ERROR"}, "Arma 3 Object Builder import operators not found")
        return {"CANCELLED"}

    configured_roots = [
        ("Common", _nh_objects_common_root(settings)),
        ("Environment", _nh_objects_environment_root(settings)),
    ]
    missing_configured = [f"{label}: {path}" for label, path in configured_roots if not os.path.isdir(path)]
    if missing_configured:
        op.report({"ERROR"}, "Set valid Common and Environment folders")
        print("=== NH Library Texture Cache: Missing configured roots ===")
        for item in missing_configured:
            print(item)
        return {"CANCELLED"}

    folders = list(_iter_nh_objects_asset_source_folders(settings))
    p3d_files = []
    for folder_abs in folders:
        p3d_files.extend(_iter_p3d_files_direct(folder_abs, settings))
    p3d_files.extend(_read_custom_asset_p3d_paths())
    p3d_files = sorted({os.path.normcase(fp): fp for fp in p3d_files if fp and os.path.isfile(fp)}.values(), key=lambda item: item.lower())
    if not p3d_files:
        op.report({"ERROR"}, "No .p3d files found in NH_Objects Common/Environment")
        return {"CANCELLED"}

    used_paa_paths = set()
    textured_candidates = 0
    failed = []
    mode_label = "rebuild used NH library textures" if force_rebuild else "cache missing/outdated NH library textures"

    try:
        context.window_manager.progress_begin(0, len(p3d_files))
    except Exception:
        pass
    try:
        for index, fp in enumerate(p3d_files, start=1):
            try:
                context.window_manager.progress_update(index)
            except Exception:
                pass
            pre_obj_ptrs = {o.as_pointer() for o in bpy.data.objects}
            pre_col_ptrs = {c.as_pointer() for c in bpy.data.collections}
            pre_mat_ptrs = {m.as_pointer() for m in bpy.data.materials}
            pre_img_ptrs = {i.as_pointer() for i in bpy.data.images}
            try:
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
                    failed.append(f"{fp}: {_fmt_exc(err) if err else 'import failed'}")
                    continue
                imported_objs = [o for o in bpy.data.objects if o.as_pointer() not in pre_obj_ptrs]
                for mat in _iter_unique_materials_from_objects(imported_objs):
                    paa_path, _rvmat_path = _get_p3d_material_paths(mat)
                    if not paa_path:
                        continue
                    textured_candidates += 1
                    resolved_path = _resolve_p3d_texture_path(paa_path)
                    if not resolved_path or os.path.splitext(resolved_path)[1].lower() != ".paa":
                        failed.append(f"{fp}: could not resolve .paa texture: {paa_path}")
                        continue
                    used_paa_paths.add(resolved_path)
            except Exception as e:
                failed.append(f"{fp}: {_fmt_exc(e)}")
            finally:
                _cleanup_imported_data_since(pre_obj_ptrs, pre_col_ptrs, pre_mat_ptrs, pre_img_ptrs)
    finally:
        try:
            context.window_manager.progress_end()
        except Exception:
            pass

    cache_stats = _run_texture_cache_workers(
        sorted(used_paa_paths, key=lambda item: item.lower()),
        force_rebuild=force_rebuild,
        settings=ts,
        context=context,
    )
    created = int(cache_stats.get("created", 0) or 0)
    rebuilt = int(cache_stats.get("rebuilt", 0) or 0)
    skipped = int(cache_stats.get("skipped", 0) or 0)
    workers_used = int(cache_stats.get("workers", 1) or 1)
    failed.extend(cache_stats.get("failed", []) or [])

    summary, report_path = _write_texture_cache_report(
        "NH Objects Common/Environment/Custom libraries",
        f"{mode_label}; workers {workers_used}",
        len(used_paa_paths),
        created,
        rebuilt,
        skipped,
        failed,
    )
    ts.texture_cache_last_summary = f"{summary}, used textures {len(used_paa_paths)}/{textured_candidates}, workers {workers_used}"
    ts.texture_cache_last_report_path = report_path
    _save_texreplace_settings_now(context)

    print("=== NH Library Used Texture Cache ===")
    print(f"Scanned .p3d: {len(p3d_files)}")
    print(f"Used textures: {len(used_paa_paths)}/{textured_candidates}")
    print(f"Workers: {workers_used}")
    print(summary)
    if failed:
        print("=== NH Library Used Texture Cache: Failures ===")
        for item in failed[:100]:
            print(item)
        if len(failed) > 100:
            print(f"... {len(failed) - 100} more failure(s) not shown")
        op.report({"WARNING"}, f"{summary}, used textures {len(used_paa_paths)}/{textured_candidates} (see System Console)")
    else:
        op.report({"INFO"}, f"{summary}, used textures {len(used_paa_paths)}/{textured_candidates}")
    return {"FINISHED"}


class CRAY_OT_TextureCacheBuildNHLibraryUsed(Operator):
    bl_idname = "cray.texture_cache_build_nh_library_used"
    bl_label = "Cache NH Library Textures"
    bl_description = "РРјРїРѕСЂС‚РёСЂСѓРµС‚ .p3d РёР· NH Р±РёР±Р»РёРѕС‚РµРє Рё РєРµС€РёСЂСѓРµС‚ С‚РѕР»СЊРєРѕ С‚Рµ .paa С‚РµРєСЃС‚СѓСЂС‹, РєРѕС‚РѕСЂС‹Рµ СЂРµР°Р»СЊРЅРѕ РёСЃРїРѕР»СЊР·СѓСЋС‚СЃСЏ СЌС‚РёРјРё Р°СЃСЃРµС‚Р°РјРё"
    bl_options = {"REGISTER", "UNDO"}

    force_rebuild: BoolProperty(
        name="РџРµСЂРµСЃРѕР·РґР°С‚СЊ СЃСѓС‰РµСЃС‚РІСѓСЋС‰РёРµ PNG",
        default=False,
        description="РџРµСЂРµСЃРѕР·РґР°С‚СЊ PNG РґР»СЏ РёСЃРїРѕР»СЊР·СѓРµРјС‹С… С‚РµРєСЃС‚СѓСЂ NH Р±РёР±Р»РёРѕС‚РµРєРё РІРјРµСЃС‚Рѕ РёСЃРїРѕР»СЊР·РѕРІР°РЅРёСЏ РІР°Р»РёРґРЅРѕРіРѕ РєРµС€Р°",
    )

    def execute(self, context):
        return _cache_nh_library_used_textures(self, context, force_rebuild=bool(self.force_rebuild))


class CRAY_OT_OpenTexturePreviewCacheFolder(Operator):
    bl_idname = "cray.open_texture_preview_cache_folder"
    bl_label = "Open Texture PNG Cache"
    bl_description = "РћС‚РєСЂС‹РІР°РµС‚ РїР°РїРєСѓ РѕР±С‰РµРіРѕ PNG-РєРµС€Р°, РєСѓРґР° СЃРєР»Р°РґС‹РІР°СЋС‚СЃСЏ РїСЂРµРІСЊСЋ РґР»СЏ .paa С‚РµРєСЃС‚СѓСЂ"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        folder = _nh_texture_cache_root(create=True)
        try:
            _open_folder_in_system(folder)
        except Exception as e:
            self.report({"ERROR"}, f"Could not open texture cache folder: {_fmt_exc(e)}")
            return {"CANCELLED"}
        self.report({"INFO"}, "Opened texture cache folder")
        return {"FINISHED"}

class CRAY_OT_OpenTextureCacheLastReport(Operator):
    bl_idname = "cray.open_texture_cache_last_report"
    bl_label = "Open Texture Cache Report"
    bl_description = "РћС‚РєСЂС‹РІР°РµС‚ РїРѕСЃР»РµРґРЅРёР№ РѕС‚С‡РµС‚ РїРѕ СЃР±РѕСЂРєРµ PNG-РєРµС€Р° С‚РµРєСЃС‚СѓСЂ"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_collider_exp import (_tex_export_resolve_path)
        ts = context.scene.cray_texreplace_settings
        path = _tex_export_resolve_path(getattr(ts, "texture_cache_last_report_path", ""))
        if not path or not os.path.isfile(path):
            self.report({"WARNING"}, "Texture cache report was not found")
            return {"CANCELLED"}
        try:
            _open_folder_in_system(path)
        except Exception as e:
            self.report({"ERROR"}, f"Could not open texture cache report: {_fmt_exc(e)}")
            return {"CANCELLED"}
        self.report({"INFO"}, "Opened texture cache report")
        return {"FINISHED"}

class CRAY_OT_OpenNHAssetCacheFolder(Operator):
    bl_idname = "cray.open_nh_asset_cache_folder"
    bl_label = "Open NH Library Cache"
    bl_description = "РћС‚РєСЂС‹РІР°РµС‚ РїР°РїРєСѓ, РіРґРµ Р»РµР¶Р°С‚ РєРµС€РёСЂРѕРІР°РЅРЅС‹Рµ .blend asset libraries Рё РёС… РїСЂРµРІСЊСЋ"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .nh_base import (_fmt_exc)
        from .nh_model_split import (_nh_objects_asset_cache_base)
        folder = _nh_objects_asset_cache_base(create=True)
        try:
            _open_folder_in_system(folder)
        except Exception as e:
            self.report({"ERROR"}, f"Could not open NH library cache folder: {_fmt_exc(e)}")
            return {"CANCELLED"}
        self.report({"INFO"}, "Opened NH library cache folder")
        return {"FINISHED"}

class CRAY_OT_AssetLibraryRebuildIconCache(Operator):
    bl_idname = "cray.asset_library_rebuild_icon_cache"
    bl_label = "Rebuild Library Icons"
    bl_description = "РџРµСЂРµСЃРѕР±РёСЂР°РµС‚ NH asset libraries Рё Р·Р°РЅРѕРІРѕ СЃРѕР·РґР°РµС‚ РёРєРѕРЅРєРё Р°СЃСЃРµС‚РѕРІ СЃ С‚РµРєСѓС‰РёРјРё РЅР°СЃС‚СЂРѕР№РєР°РјРё РїСЂРµРІСЊСЋ"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_assets import (_build_nh_objects_persistent_asset_libraries)
        settings = context.scene.cray_asset_library_settings
        old_rebuild = bool(getattr(settings, "rebuild_existing_libraries", False))
        old_render = bool(getattr(settings, "render_textured_previews", False))
        try:
            settings.rebuild_existing_libraries = True
            settings.render_textured_previews = False
            return _build_nh_objects_persistent_asset_libraries(self, context, cache_missing_textures=False)
        finally:
            try:
                settings.rebuild_existing_libraries = old_rebuild
                settings.render_textured_previews = old_render
            except Exception:
                pass


class CRAY_OT_AssetLibraryFullRebuildFromZero(Operator):
    bl_idname = "cray.asset_library_full_rebuild_from_zero"
    bl_label = "Full Rebuild From Zero"
    bl_description = "Deletes NH Common/Environment asset-library cache and rebuilds libraries with materialless clay icons"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_assets import (_build_nh_objects_persistent_asset_libraries, _clear_nh_objects_asset_library_cache_roots)
        settings = context.scene.cray_asset_library_settings
        old_rebuild = bool(getattr(settings, "rebuild_existing_libraries", False))
        old_render = bool(getattr(settings, "render_textured_previews", False))
        try:
            removed, failed = _clear_nh_objects_asset_library_cache_roots(settings)
            if failed:
                print("=== NH Objects Full Rebuild: Cache cleanup warnings ===")
                for item in failed:
                    print(item)
            settings.rebuild_existing_libraries = True
            settings.render_textured_previews = False
            result = _build_nh_objects_persistent_asset_libraries(self, context, cache_missing_textures=False)
            if result == {"FINISHED"} and removed:
                self.report({"INFO"}, f"Full NH rebuild complete: cleared {len(removed)} cache folder(s)")
            return result
        finally:
            try:
                settings.rebuild_existing_libraries = old_rebuild
                settings.render_textured_previews = old_render
            except Exception:
                pass


class CRAY_OT_AssetLibraryForceRebuildIconsTextures(Operator):
    bl_idname = "cray.asset_library_force_rebuild_icons_textures"
    bl_label = "Force Rebuild All Icons + Textures"
    bl_description = (
        "Force-rebuild the PNG cache for textures used by all NH assets, clear the "
        "Common/Environment asset cache, and render every asset icon with textures"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nh_assets import (_build_nh_objects_persistent_asset_libraries, _clear_nh_objects_asset_library_cache_roots)
        settings = context.scene.cray_asset_library_settings
        old_rebuild = bool(getattr(settings, "rebuild_existing_libraries", False))

        # Keep textured mode enabled after this workflow so later Add New operations
        # produce matching textured icons instead of reverting them to geometry icons.
        settings.render_textured_previews = True
        try:
            texture_result = _cache_nh_library_used_textures(self, context, force_rebuild=True)
            if texture_result != {"FINISHED"}:
                return texture_result

            removed, cleanup_failed = _clear_nh_objects_asset_library_cache_roots(settings)
            if cleanup_failed:
                print("=== NH Objects Textured Rebuild: Cache cleanup warnings ===")
                for item in cleanup_failed:
                    print(item)

            settings.rebuild_existing_libraries = True
            result = _build_nh_objects_persistent_asset_libraries(
                self,
                context,
                cache_missing_textures=True,
            )
            if result == {"FINISHED"}:
                message = (
                    "Textured NH rebuild complete: texture cache refreshed, "
                    f"cleared {len(removed)} asset cache folder(s)"
                )
                if cleanup_failed:
                    self.report({"WARNING"}, message + f", cleanup warnings {len(cleanup_failed)}")
                else:
                    self.report({"INFO"}, message)
            return result
        finally:
            try:
                settings.rebuild_existing_libraries = old_rebuild
            except Exception:
                pass


class CRAY_OT_TextureCacheRebuildNHLibraryUsed(Operator):
    bl_idname = "cray.texture_cache_rebuild_nh_library_used"
    bl_label = "Rebuild NH Library Texture Cache"
    bl_description = "РџРµСЂРµСЃРѕР·РґР°РµС‚ PNG-РєРµС€ С‚РѕР»СЊРєРѕ РґР»СЏ С‚РµРєСЃС‚СѓСЂ, РєРѕС‚РѕСЂС‹Рµ РёСЃРїРѕР»СЊР·СѓСЋС‚СЃСЏ Р°СЃСЃРµС‚Р°РјРё NH Р±РёР±Р»РёРѕС‚РµРє"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _cache_nh_library_used_textures(self, context, force_rebuild=True)

# ------------------------------------------------------------------------
#  Batch Import (P3D)
# ------------------------------------------------------------------------

def _iter_layer_collections(layer_collection):
    stack = [layer_collection]
    while stack:
        lc = stack.pop()
        yield lc
        for ch in reversed(list(lc.children)):
            stack.append(ch)

def _disable_all_collections_in_view_layer(context, mode: str):
    vl = context.view_layer
    root_lc = vl.layer_collection
    for lc in _iter_layer_collections(root_lc):
        if lc is root_lc:
            continue
        if mode == "EXCLUDE":
            lc.exclude = True
        else:
            try:
                lc.collection.hide_viewport = True
                lc.collection.hide_render = True
            except Exception:
                pass

_IE_SOURCE_PATH_KEY = "cray_source_p3d"
_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*]')

def _iter_collection_tree(collection):
    stack = [collection]
    while stack:
        col = stack.pop()
        yield col
        for ch in reversed(list(col.children)):
            stack.append(ch)

def _collect_collection_objects_recursive(collection):
    objects = []
    seen = set()
    for col in _iter_collection_tree(collection):
        for obj in col.objects:
            ptr = obj.as_pointer()
            if ptr in seen:
                continue
            seen.add(ptr)
            objects.append(obj)
    return objects

def _collection_has_any_mesh(collection):
    for obj in _collect_collection_objects_recursive(collection):
        if obj.type == "MESH":
            return True
    return False

def _collection_has_any_object_ptr(collection, object_ptrs):
    if not object_ptrs:
        return False
    for obj in _collect_collection_objects_recursive(collection):
        if obj.as_pointer() in object_ptrs:
            return True
    return False

def _find_collection_path(root_collection, target_ptr):
    if root_collection.as_pointer() == target_ptr:
        return [root_collection]

    for child in root_collection.children:
        path = _find_collection_path(child, target_ptr)
        if path:
            return [root_collection] + path
    return None

def _ensure_collection_visible_in_view_layer(context, target_collection):
    root = context.scene.collection
    path = _find_collection_path(root, target_collection.as_pointer())
    if not path:
        return

    layer_map = {lc.collection.as_pointer(): lc for lc in _iter_layer_collections(context.view_layer.layer_collection)}
    to_show = []
    to_show.extend(path)
    to_show.extend(list(_iter_collection_tree(target_collection)))

    seen = set()
    for col in to_show:
        ptr = col.as_pointer()
        if ptr in seen:
            continue
        seen.add(ptr)

        lc = layer_map.get(ptr)
        if lc is not None:
            try:
                lc.exclude = False
            except Exception:
                pass
            try:
                lc.hide_viewport = False
            except Exception:
                pass

        try:
            col.hide_viewport = False
        except Exception:
            pass
        try:
            col.hide_render = False
        except Exception:
            pass

def _strip_blender_numeric_suffix(name: str) -> str:
    n = (name or "").strip()
    m = re.match(r"^(.*)\.(\d{3})$", n)
    if m:
        return m.group(1)
    return n

def _looks_like_p3d_collection_name(name: str) -> bool:
    n = (name or "").strip().lower()
    return ".p3d" in n

def _looks_like_split_part_collection_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    return re.search(r"_(?:p)?\d+\.p3d$", n, flags=re.IGNORECASE) is not None

def _build_ie_import_basename_map(settings):
    from .nh_collider_exp import (_norm_path)
    mapping = {}
    for item in settings.import_files:
        fp = bpy.path.abspath(item.path)
        if not fp:
            continue
        base = os.path.basename(fp).lower()
        if not base:
            continue
        if base not in mapping:
            mapping[base] = _norm_path(fp)
    return mapping


def _planner_add_import_file(settings, filepath: str) -> bool:
    from .nh_collider_exp import (_norm_path)
    if settings is None:
        return False

    fp = _norm_path(bpy.path.abspath(filepath)) if filepath else ""
    if not fp:
        return False

    for idx, item in enumerate(settings.import_files):
        existing = _norm_path(bpy.path.abspath(item.path)) if item.path else ""
        if existing == fp:
            settings.import_active_index = idx
            return False

    item = settings.import_files.add()
    item.path = fp
    settings.import_active_index = len(settings.import_files) - 1
    return True


def _is_p3d_filepath(filepath: str) -> bool:
    return bool(filepath) and os.path.splitext(str(filepath))[1].lower() == ".p3d"


def _iter_ie_operator_filepaths(directory: str = "", files=None, filepath: str = ""):
    dir_abs = bpy.path.abspath(directory) if directory else ""
    dir_abs = os.path.abspath(dir_abs) if dir_abs else ""
    yielded = False

    for file_item in files or ():
        name = getattr(file_item, "name", "") or ""
        if not name:
            continue
        yielded = True
        yield bpy.path.abspath(os.path.join(dir_abs, name) if dir_abs else name)

    if not yielded and filepath:
        yield bpy.path.abspath(filepath)


def _planner_add_import_files_from_operator(settings, *, directory: str = "", files=None, filepath: str = ""):
    from .nh_collider_exp import (_norm_path)
    added = 0
    skipped_duplicate = 0
    skipped_non_p3d = 0

    for fp in _iter_ie_operator_filepaths(directory=directory, files=files, filepath=filepath):
        fp = _norm_path(bpy.path.abspath(fp)) if fp else ""
        if not fp:
            continue
        if not _is_p3d_filepath(fp):
            skipped_non_p3d += 1
            continue
        if _planner_add_import_file(settings, fp):
            added += 1
        else:
            skipped_duplicate += 1

    return added, skipped_duplicate, skipped_non_p3d


def _p3d_drop_natural_sort_key(filepath: str):
    from .nh_collider_exp import (_norm_path)
    path = _norm_path(bpy.path.abspath(filepath)) if filepath else ""
    folder = os.path.dirname(path).lower()
    basename = os.path.basename(path).lower()
    name_parts = [
        (1, int(part)) if part.isdigit() else (0, part)
        for part in re.split(r"(\d+)", basename)
    ]
    return folder, name_parts, basename


def _collect_p3d_filepaths_from_operator(*, directory: str = "", files=None, filepath: str = ""):
    from .nh_collider_exp import (_norm_path)
    paths = []
    seen = set()
    for fp in _iter_ie_operator_filepaths(directory=directory, files=files, filepath=filepath):
        fp = _norm_path(bpy.path.abspath(fp)) if fp else ""
        if not fp or not _is_p3d_filepath(fp):
            continue
        key = os.path.normcase(fp)
        if key in seen:
            continue
        seen.add(key)
        paths.append(fp)
    return sorted(paths, key=_p3d_drop_natural_sort_key)


def _set_pending_p3d_drop_paths(paths):
    from .nh_snap import (_P3D_DROP_PENDING_PATHS)
    _P3D_DROP_PENDING_PATHS.clear()
    _P3D_DROP_PENDING_PATHS.extend(paths)


def _pending_p3d_drop_label():
    from .nh_snap import (_P3D_DROP_PENDING_PATHS)
    count = len(_P3D_DROP_PENDING_PATHS)
    if count <= 0:
        return "No .p3d files"
    if count == 1:
        return os.path.basename(_P3D_DROP_PENDING_PATHS[0])
    return f"{count} .p3d files"


def _import_p3d_paths_now(operator, context, paths):
    from .nh_base import (_fmt_exc)
    from .nh_collider_exp import (_norm_path)
    from .nh_snap import (_P3D_IMPORT_CANDIDATES, _call_first_available, _has_any_p3d_import_ops, _suppress_p3d_import_tracking)
    st = context.scene.cray_ie_settings
    if not paths:
        operator.report({"ERROR"}, "No .p3d files to import")
        return {"CANCELLED"}
    if not _has_any_p3d_import_ops():
        operator.report({"ERROR"}, "Arma 3 Object Builder import operators not found")
        return {"CANCELLED"}

    imported = 0
    skipped_existing = []
    failed = []
    used_op = None

    for fp in paths:
        fp = _norm_path(bpy.path.abspath(fp)) if fp else ""
        if not fp or not _is_p3d_filepath(fp):
            failed.append(f"{fp or '<empty>'} -> not a .p3d file")
            continue

        existing_root = _find_existing_scene_p3d_root(context.scene, fp)
        if existing_root is not None:
            skipped_existing.append(f"{os.path.basename(fp)} -> already imported as {existing_root.name}")
            continue
        if not os.path.isfile(fp):
            failed.append(f"{fp} -> file not found")
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
            continue

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
        print("=== P3D Drop Import: Skipped already imported ===")
        for item in skipped_existing:
            print(item)

    if failed:
        print("=== P3D Drop Import: Failures ===")
        for item in failed:
            print(item)
        operator.report(
            {"WARNING"},
            f"Imported {imported}, skipped existing {len(skipped_existing)}, failed {len(failed)} (see System Console)",
        )
        return {"FINISHED"}

    operator.report(
        {"INFO"},
        f"Imported {imported} file(s), skipped existing {len(skipped_existing)}" + (f" via {used_op}" if used_op else ""),
    )
    return {"FINISHED"}


def _normalize_p3d_lookup_key(value: str) -> str:
    raw = (value or "").replace("/", "\\").strip()
    if not raw:
        return ""
    raw = raw.split("\\")[-1]
    raw = _strip_blender_numeric_suffix(raw)
    if raw.lower().endswith(".p3d") or raw.lower().endswith(".png"):
        raw = raw[:-4]
    return raw.strip().lower()


def _display_p3d_name(value: str) -> str:
    key = _normalize_p3d_lookup_key(value)
    if not key:
        return ""
    return key + ".p3d"


def _find_existing_scene_p3d_root(scene, model_name_or_path: str):
    from .nh_snap import (_iter_p3d_root_collections)
    wanted = _normalize_p3d_lookup_key(model_name_or_path)
    if not wanted:
        return None

    for col in _iter_p3d_root_collections(scene):
        if _normalize_p3d_lookup_key(getattr(col, "name", "") or "") == wanted:
            return col
    return None


def _find_p3d_paths_by_name(search_root: str, model_name: str, settings=None, respect_ignore: bool = True):
    from .nh_collider_exp import (_norm_path)
    from .nh_model_split import (_is_ignored_nh_objects_asset_path)
    root_abs = bpy.path.abspath(search_root) if search_root else ""
    root_abs = os.path.abspath(root_abs) if root_abs else ""
    wanted = _normalize_p3d_lookup_key(model_name)
    if not root_abs or not os.path.isdir(root_abs) or not wanted:
        return []

    matches = []
    for root, dirs, files in os.walk(root_abs):
        if respect_ignore:
            dirs[:] = [
                name for name in dirs
                    if not _is_ignored_nh_objects_asset_path(os.path.join(root, name), settings)
                ]
            if _is_ignored_nh_objects_asset_path(root, settings):
                continue
        for fn in files:
            if not fn.lower().endswith(".p3d"):
                continue
            if _normalize_p3d_lookup_key(fn) != wanted:
                continue
            matches.append(_norm_path(os.path.join(root, fn)))

    matches.sort(key=lambda item: (len(item), item.lower()))
    return matches

def _resolve_collection_source_path(collection, import_basename_map=None):
    from .nh_collider_exp import (_norm_path)
    src = collection.get(_IE_SOURCE_PATH_KEY)
    if isinstance(src, str) and src.strip():
        return _norm_path(bpy.path.abspath(src))

    for obj in _collect_collection_objects_recursive(collection):
        src = obj.get(_IE_SOURCE_PATH_KEY)
        if isinstance(src, str) and src.strip():
            return _norm_path(bpy.path.abspath(src))

    if import_basename_map:
        names = []
        raw = (collection.name or "").strip()
        if raw:
            names.append(raw)
            names.append(_strip_blender_numeric_suffix(raw))
        for name in names:
            n = name.strip()
            if not n:
                continue
            if ".p3d" not in n.lower():
                n = n + ".p3d"
            key = n.lower()
            if key in import_basename_map:
                return import_basename_map[key]

    return ""

def _export_filename_for_collection(collection, source_path: str):
    if source_path:
        base = os.path.basename(source_path)
        if base:
            return base

    base = _strip_blender_numeric_suffix(collection.name)
    if not base:
        base = "export"
    if ".p3d" not in base.lower():
        base = base + ".p3d"
    return _INVALID_FILENAME_CHARS_RE.sub("_", base)

def _clear_ie_source_path_tag(id_data):
    if id_data is None:
        return
    try:
        if _IE_SOURCE_PATH_KEY in id_data:
            del id_data[_IE_SOURCE_PATH_KEY]
    except Exception:
        pass

def _set_ie_source_path_tag(id_data, source_path: str):
    from .nh_collider_exp import (_norm_path)
    if id_data is None:
        return
    src = _norm_path(bpy.path.abspath(source_path)) if source_path else ""
    if not src:
        _clear_ie_source_path_tag(id_data)
        return
    try:
        id_data[_IE_SOURCE_PATH_KEY] = src
    except Exception:
        pass

def _derive_split_export_source_path(source_root, split_root_name: str) -> str:
    from .nh_collider_exp import (_norm_path)
    source_path = _resolve_collection_source_path(source_root)
    if not source_path:
        return ""

    source_dir = os.path.dirname(source_path)
    if not source_dir:
        return ""

    base = _strip_blender_numeric_suffix((split_root_name or "").strip())
    if not base:
        base = "export"
    if ".p3d" not in base.lower():
        base = base + ".p3d"
    base = _INVALID_FILENAME_CHARS_RE.sub("_", base)
    return _norm_path(os.path.join(source_dir, base))

def _find_p3d_root_collection_for_object(context, obj):
    if obj is None:
        return None

    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    if scene_root is None:
        return None

    best = None
    best_depth = -1
    for col in getattr(obj, "users_collection", []):
        path = _find_collection_path(scene_root, col.as_pointer())
        if not path:
            continue
        for depth, item in enumerate(path):
            if _looks_like_p3d_collection_name(item.name) and depth >= best_depth:
                best = item
                best_depth = depth

    return best

def _plain_axis_helper_name(root_collection) -> str:
    root_name = _strip_blender_numeric_suffix((getattr(root_collection, "name", "") or "").strip())
    if not root_name:
        root_name = "Model"
    root_name = re.sub(r"\s+", " ", root_name).strip()
    return f"Plain Axis {root_name}"

def _is_plain_axis_helper(obj) -> bool:
    from .nh_base import (_PLAIN_AXIS_HELPER_PROP)
    if obj is None or obj.type != "EMPTY":
        return False
    try:
        if bool(obj.get(_PLAIN_AXIS_HELPER_PROP, False)):
            return True
    except Exception:
        pass
    return False

def _pick_plain_axis_root_collection(context, source_obj):
    root = _find_p3d_root_collection_for_object(context, source_obj)
    if root is not None:
        return root, True
    if source_obj is not None and source_obj.users_collection:
        return source_obj.users_collection[0], False
    return getattr(getattr(context, "scene", None), "collection", None), False

def _collect_plain_axis_target_objects(root_collection, helper_obj=None):
    objects = _collect_collection_objects_recursive(root_collection) if root_collection is not None else []
    if not objects:
        return []

    object_ptrs = {obj.as_pointer() for obj in objects}
    targets = []
    for obj in objects:
        if helper_obj is not None and obj == helper_obj:
            continue
        if _is_plain_axis_helper(obj):
            continue
        if obj.parent is not None and obj.parent.as_pointer() in object_ptrs:
            continue
        targets.append(obj)
    return targets

def _iter_plain_axis_constraints(obj, helper_ptrs=None):
    from .nh_base import (_PLAIN_AXIS_CONSTRAINT_NAME)
    if obj is None:
        return
    for con in getattr(obj, "constraints", []):
        if getattr(con, "type", "") != "CHILD_OF":
            continue
        target = getattr(con, "target", None)
        target_ptr = target.as_pointer() if target is not None else None
        if helper_ptrs and target_ptr in helper_ptrs:
            yield con
            continue
        if getattr(con, "name", "") == _PLAIN_AXIS_CONSTRAINT_NAME and target is not None and _is_plain_axis_helper(target):
            yield con

def _remove_plain_axis_constraints_from_objects(objects, helper_ptrs=None, *, context=None, keep_world_transform=False):
    entries = []
    seen = set()
    for obj in list(objects):
        if obj is None:
            continue
        try:
            ptr = obj.as_pointer()
        except Exception:
            ptr = id(obj)
        if ptr in seen:
            continue
        seen.add(ptr)

        constraints = list(_iter_plain_axis_constraints(obj, helper_ptrs=helper_ptrs))
        if not constraints:
            continue

        world_matrix = None
        if keep_world_transform:
            try:
                world_matrix = obj.matrix_world.copy()
            except Exception:
                world_matrix = None

        entries.append((obj, constraints, world_matrix))

    removed = 0
    for obj, constraints, _world_matrix in entries:
        if obj is None or bpy.data.objects.get(obj.name) is not obj:
            continue
        for con in constraints:
            try:
                obj.constraints.remove(con)
                removed += 1
            except Exception:
                pass

    if keep_world_transform:
        try:
            if context is not None:
                context.view_layer.update()
        except Exception:
            pass

        for obj, _constraints, world_matrix in sorted(entries, key=lambda item: _obj_depth(item[0])):
            if world_matrix is None or obj is None or bpy.data.objects.get(obj.name) is not obj:
                continue
            try:
                obj.matrix_world = world_matrix
            except Exception:
                pass

        try:
            if context is not None:
                context.view_layer.update()
        except Exception:
            pass

    return removed


def _remove_plain_axis_constraints_from_objects_keep_world_z(objects, helper_ptrs=None, *, context=None):
    entries = []
    seen = set()
    try:
        if context is not None:
            context.view_layer.update()
    except Exception:
        pass

    for obj in list(objects):
        if obj is None:
            continue
        try:
            ptr = obj.as_pointer()
        except Exception:
            ptr = id(obj)
        if ptr in seen:
            continue
        seen.add(ptr)

        constraints = list(_iter_plain_axis_constraints(obj, helper_ptrs=helper_ptrs))
        if not constraints:
            continue

        world_z = None
        try:
            world_z = float(obj.matrix_world.translation.z)
        except Exception:
            world_z = None

        entries.append((obj, constraints, world_z))

    removed = 0
    for obj, constraints, _world_z in entries:
        if obj is None or bpy.data.objects.get(obj.name) is not obj:
            continue
        for con in constraints:
            try:
                obj.constraints.remove(con)
                removed += 1
            except Exception:
                pass

    try:
        if context is not None:
            context.view_layer.update()
    except Exception:
        pass

    for obj, _constraints, world_z in sorted(entries, key=lambda item: _obj_depth(item[0])):
        if world_z is None or obj is None or bpy.data.objects.get(obj.name) is not obj:
            continue
        try:
            matrix = obj.matrix_world.copy()
            matrix.translation.z = world_z
            obj.matrix_world = matrix
        except Exception:
            pass

    try:
        if context is not None:
            context.view_layer.update()
    except Exception:
        pass

    return removed


def _plain_axis_object_world_center(obj, *, prefer_vertices=False):
    if obj is None:
        return Vector((0.0, 0.0, 0.0))

    if prefer_vertices and getattr(obj, "type", None) == "MESH" and getattr(obj, "data", None) is not None:
        vertices = getattr(obj.data, "vertices", ())
        count = len(vertices)
        if count > 0:
            total = Vector((0.0, 0.0, 0.0))
            for vert in vertices:
                total += obj.matrix_world @ vert.co
            return total / count

    points = []
    try:
        points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    except Exception:
        points = []

    if points:
        total = Vector((0.0, 0.0, 0.0))
        for point in points:
            total += point
        return total / len(points)

    try:
        return obj.matrix_world.translation.copy()
    except Exception:
        return Vector((0.0, 0.0, 0.0))


def _object_has_ancestor_in_pointer_set(obj, object_ptrs):
    parent = getattr(obj, "parent", None)
    while parent is not None:
        try:
            if parent.as_pointer() in object_ptrs:
                return True
        except Exception:
            pass
        parent = getattr(parent, "parent", None)
    return False


def _plain_axis_helper_root_collections(context, helper_obj):
    from .nh_base import (_PLAIN_AXIS_ROOT_PROP)
    roots = []
    seen = set()
    for col in getattr(helper_obj, "users_collection", []):
        try:
            ptr = col.as_pointer()
        except Exception:
            ptr = None
        if ptr is not None and ptr in seen:
            continue
        if ptr is not None:
            seen.add(ptr)
        roots.append(col)

    stored_name = ""
    try:
        stored_name = helper_obj.get(_PLAIN_AXIS_ROOT_PROP, "") or ""
    except Exception:
        stored_name = ""
    stored_col = bpy.data.collections.get(stored_name) if stored_name else None
    if stored_col is not None:
        try:
            ptr = stored_col.as_pointer()
        except Exception:
            ptr = None
        if ptr is None or ptr not in seen:
            roots.append(stored_col)

    return roots


def _snapshot_plain_axis_memory_restore_state(context, live_helpers):
    from .nh_snap import (_is_memory_lod_mesh_object)
    try:
        context.view_layer.update()
    except Exception:
        pass

    states = []
    for helper_obj in live_helpers:
        try:
            helper_ptr = helper_obj.as_pointer()
        except Exception:
            continue

        constrained = []
        constrained_ptrs = set()
        for obj in bpy.data.objects:
            constraints = list(_iter_plain_axis_constraints(obj, helper_ptrs={helper_ptr}))
            if not constraints:
                continue
            try:
                constrained_ptrs.add(obj.as_pointer())
            except Exception:
                pass
            constrained.append({
                "object": obj,
                "before_matrix": obj.matrix_world.copy(),
                "before_center": _plain_axis_object_world_center(obj),
            })

        if not constrained:
            continue

        memory_items = []
        memory_seen = set()
        for root_col in _plain_axis_helper_root_collections(context, helper_obj):
            for obj in _collect_collection_objects_recursive(root_col):
                if not _is_memory_lod_mesh_object(obj):
                    continue
                try:
                    ptr = obj.as_pointer()
                except Exception:
                    continue
                if ptr in memory_seen or ptr in constrained_ptrs:
                    continue
                if _object_has_ancestor_in_pointer_set(obj, constrained_ptrs):
                    continue
                if list(_iter_plain_axis_constraints(obj)):
                    continue
                memory_seen.add(ptr)
                memory_items.append({
                    "object": obj,
                    "before_matrix": obj.matrix_world.copy(),
                    "before_center": _plain_axis_object_world_center(obj, prefer_vertices=True),
                })

        if memory_items:
            states.append({
                "constrained": constrained,
                "memory": memory_items,
            })

    return states


def _restore_unconstrained_plain_axis_memory_objects(context, restore_states):
    if not restore_states:
        return 0

    try:
        context.view_layer.update()
    except Exception:
        pass

    adjusted = 0
    for state in restore_states:
        reference = None
        for item in state.get("constrained", ()):
            obj = item.get("object")
            if obj is not None and bpy.data.objects.get(obj.name) is obj:
                reference = item
                break
        if reference is None:
            continue

        ref_obj = reference["object"]
        ref_before_matrix = reference["before_matrix"]
        ref_after_matrix = ref_obj.matrix_world.copy()
        correction = ref_after_matrix @ ref_before_matrix.inverted_safe()

        for item in state.get("memory", ()):
            obj = item.get("object")
            if obj is None or bpy.data.objects.get(obj.name) is not obj:
                continue
            try:
                obj.matrix_world = correction @ obj.matrix_world
                adjusted += 1
            except Exception:
                pass

    if adjusted:
        try:
            context.view_layer.update()
        except Exception:
            pass
    return adjusted


def _apply_child_of_inverse_with_fallback(context, obj, constraint):
    if obj is None or constraint is None:
        return

    try:
        with context.temp_override(
            object=obj,
            active_object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
        ):
            bpy.ops.constraint.childof_set_inverse(constraint=constraint.name, owner="OBJECT")
        return
    except Exception:
        pass

    target = getattr(constraint, "target", None)
    if target is None:
        return
    try:
        constraint.inverse_matrix = target.matrix_world.inverted_safe()
    except Exception:
        pass


def _set_plain_axis_constraint_axes(constraint):
    from .nh_base import (_PLAIN_AXIS_CONSTRAINT_AXES)
    for attr in _PLAIN_AXIS_CONSTRAINT_AXES:
        try:
            setattr(constraint, attr, True)
        except Exception:
            pass


def _find_plain_axis_helper_in_collection(root_collection):
    if root_collection is None:
        return None
    helpers = [obj for obj in _collect_collection_objects_recursive(root_collection) if _is_plain_axis_helper(obj)]
    if not helpers:
        return None
    helpers.sort(key=lambda obj: getattr(obj, "name", ""))
    return helpers[-1]


def _plain_axis_reference_priority(obj):
    from .nh_snap import (_is_memory_lod_mesh_object)
    name = _strip_blender_numeric_suffix(getattr(obj, "name", "") or "").strip().lower()
    if name == "resolution 0":
        return 0
    if name.startswith("resolution 0"):
        return 1
    if name.startswith("resolution"):
        return 2
    if not _is_memory_lod_mesh_object(obj):
        return 3
    return 4


def _find_plain_axis_reference_constraint(helper_obj, exclude_obj=None, reference_obj=None, root_collection=None):
    if helper_obj is None:
        return None
    helper_ptr = helper_obj.as_pointer()
    exclude_ptr = exclude_obj.as_pointer() if exclude_obj is not None else None

    def constraint_from_object(obj):
        if obj is None:
            return None
        if exclude_ptr is not None and obj.as_pointer() == exclude_ptr:
            return None
        for con in _iter_plain_axis_constraints(obj, helper_ptrs={helper_ptr}):
            return con
        return None

    con = constraint_from_object(reference_obj)
    if con is not None:
        return con

    candidates = []
    if root_collection is not None:
        for obj in _collect_collection_objects_recursive(root_collection):
            if _is_plain_axis_helper(obj):
                continue
            con = constraint_from_object(obj)
            if con is None:
                continue
            candidates.append((obj, con))
        candidates.sort(key=lambda item: (_plain_axis_reference_priority(item[0]), getattr(item[0], "name", "")))
        if candidates:
            return candidates[0][1]

    for obj in bpy.data.objects:
        con = constraint_from_object(obj)
        if con is not None:
            return con
    return None


def _ensure_plain_axis_constraint_for_new_object(context, obj, root_collection, reference_obj=None):
    from .nh_base import (_PLAIN_AXIS_CONSTRAINT_NAME, _fmt_exc)
    if obj is None or root_collection is None:
        return False

    helper_obj = _find_plain_axis_helper_in_collection(root_collection)
    if helper_obj is None:
        return False

    helper_ptr = helper_obj.as_pointer()
    existing_constraints = list(_iter_plain_axis_constraints(obj, helper_ptrs={helper_ptr}))

    reference_constraint = _find_plain_axis_reference_constraint(
        helper_obj,
        exclude_obj=obj,
        reference_obj=reference_obj,
        root_collection=root_collection,
    )
    if reference_constraint is None:
        return False

    try:
        desired_world = obj.matrix_world.copy()
        for existing_constraint in existing_constraints:
            try:
                obj.constraints.remove(existing_constraint)
            except Exception:
                pass
        context.view_layer.update()

        helper_delta = helper_obj.matrix_world @ reference_constraint.inverse_matrix
        obj.matrix_world = helper_delta.inverted_safe() @ desired_world
        con = obj.constraints.new(type="CHILD_OF")
        con.name = _PLAIN_AXIS_CONSTRAINT_NAME
        con.target = helper_obj
        _set_plain_axis_constraint_axes(con)
        con.inverse_matrix = reference_constraint.inverse_matrix.copy()
        context.view_layer.update()
        return True
    except Exception as e:
        print(f"[NH Plugin] Failed to attach new object to Plain Axis: {getattr(obj, 'name', '<object>')}: {_fmt_exc(e)}")
        return False


def _repair_plain_axis_memory_constraints(context, helper_objects):
    from .nh_snap import (_is_memory_lod_mesh_object)
    repaired = 0
    for helper_obj in helper_objects:
        for root_collection in _plain_axis_helper_root_collections(context, helper_obj):
            for obj in _collect_collection_objects_recursive(root_collection):
                if not _is_memory_lod_mesh_object(obj):
                    continue
                if _ensure_plain_axis_constraint_for_new_object(context, obj, root_collection):
                    repaired += 1
    return repaired


def _create_plain_axis_helper(context, root_collection, source_obj, world_location):
    from .nh_base import (_PLAIN_AXIS_HELPER_PROP, _PLAIN_AXIS_ROOT_PROP, _PLAIN_AXIS_SOURCE_OBJECT_PROP)
    helper_name = _plain_axis_helper_name(root_collection)
    helper_obj = bpy.data.objects.new(helper_name, None)
    helper_obj.empty_display_type = "PLAIN_AXES"
    try:
        max_dim = max(abs(float(v)) for v in getattr(source_obj, "dimensions", (0.0, 0.0, 0.0)))
    except Exception:
        max_dim = 0.0
    helper_obj.empty_display_size = max(0.05, max_dim * 0.08)
    helper_obj.matrix_world = Matrix.Translation(world_location)
    helper_obj[_PLAIN_AXIS_HELPER_PROP] = True
    helper_obj[_PLAIN_AXIS_ROOT_PROP] = getattr(root_collection, "name", "")
    helper_obj[_PLAIN_AXIS_SOURCE_OBJECT_PROP] = getattr(source_obj, "name", "")
    _link_object_to_collection(helper_obj, root_collection)
    _ensure_collection_visible_in_view_layer(context, root_collection)
    return helper_obj

def _clear_plain_axis_helpers(context, helper_objects):
    live_helpers = []
    seen = set()
    for obj in helper_objects:
        if obj is None:
            continue
        try:
            ptr = obj.as_pointer()
        except Exception:
            continue
        if ptr in seen:
            continue
        seen.add(ptr)
        if bpy.data.objects.get(obj.name) is None:
            continue
        live_helpers.append(obj)

    if not live_helpers:
        return 0, 0

    repaired_memory = _repair_plain_axis_memory_constraints(context, live_helpers)
    memory_restore_states = _snapshot_plain_axis_memory_restore_state(context, live_helpers)
    helper_ptrs = {obj.as_pointer() for obj in live_helpers}
    removed_constraints = _remove_plain_axis_constraints_from_objects(
        bpy.data.objects,
        helper_ptrs=helper_ptrs,
        context=context,
        keep_world_transform=False,
    )
    restored_memory = _restore_unconstrained_plain_axis_memory_objects(context, memory_restore_states)

    removed_helpers = 0
    for obj in live_helpers:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed_helpers += 1
        except Exception:
            pass

    if repaired_memory:
        print(f"[NH Plugin] Repaired {repaired_memory} Memory LOD Plain Axis constraint(s) before deleting Plain Axes")
    if restored_memory:
        print(f"[NH Plugin] Restored {restored_memory} unconstrained Memory LOD object(s) after deleting Plain Axes")
    return removed_helpers, removed_constraints

def _clear_plain_axis_helpers_keep_world_z(context, helper_objects):
    live_helpers = []
    seen = set()
    for obj in helper_objects:
        if obj is None:
            continue
        try:
            ptr = obj.as_pointer()
        except Exception:
            continue
        if ptr in seen:
            continue
        seen.add(ptr)
        if bpy.data.objects.get(obj.name) is None:
            continue
        live_helpers.append(obj)

    if not live_helpers:
        return 0, 0

    repaired_memory = _repair_plain_axis_memory_constraints(context, live_helpers)
    memory_restore_states = _snapshot_plain_axis_memory_restore_state(context, live_helpers)
    helper_ptrs = {obj.as_pointer() for obj in live_helpers}
    removed_constraints = _remove_plain_axis_constraints_from_objects_keep_world_z(
        bpy.data.objects,
        helper_ptrs=helper_ptrs,
        context=context,
    )
    restored_memory = _restore_unconstrained_plain_axis_memory_objects(context, memory_restore_states)

    removed_helpers = 0
    for obj in live_helpers:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed_helpers += 1
        except Exception:
            pass

    if repaired_memory:
        print(f"[NH Plugin] Repaired {repaired_memory} Memory LOD Plain Axis constraint(s) before deleting Plain Axes with saved Z")
    if restored_memory:
        print(f"[NH Plugin] Restored {restored_memory} unconstrained Memory LOD object(s) after deleting Plain Axes with saved Z")
    return removed_helpers, removed_constraints

def _clear_plain_axis_helpers_in_collection(context, root_collection):
    if root_collection is None:
        return 0, 0
    helpers = [obj for obj in _collect_collection_objects_recursive(root_collection) if _is_plain_axis_helper(obj)]
    return _clear_plain_axis_helpers(context, helpers)

def _best_object_collection_path_under_root(root_collection, obj):
    if root_collection is None or obj is None:
        return None

    best = None
    for col in getattr(obj, "users_collection", []):
        path = _find_collection_path(root_collection, col.as_pointer())
        if not path:
            continue
        if best is None or len(path) > len(best):
            best = path

    return best

def _format_split_part_collection_name(source_root_name: str, part_number: int) -> str:
    base_name = _strip_blender_numeric_suffix((source_root_name or "").strip())
    if not base_name:
        base_name = "part"

    suffix = f"_p{int(part_number):02d}"
    stem, ext = os.path.splitext(base_name)
    if ext.lower() == ".p3d":
        return f"{stem}{suffix}{ext}"
    return f"{base_name}{suffix}.p3d"

def _split_part_collection_number(source_root_name: str, collection_name: str):
    base_name = _strip_blender_numeric_suffix((source_root_name or "").strip())
    if not base_name:
        return None

    stem, ext = os.path.splitext(base_name)
    base_stem = stem if ext.lower() == ".p3d" else base_name
    if not base_stem:
        return None

    pattern = r"^" + re.escape(base_stem) + r"_p(\d+)\.p3d$"
    match = re.match(pattern, _strip_blender_numeric_suffix(collection_name or ""), flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None

def _next_split_part_collection_number(source_root) -> int:
    highest = 0
    if source_root is not None:
        for child in getattr(source_root, "children", []):
            number = _split_part_collection_number(getattr(source_root, "name", ""), getattr(child, "name", ""))
            if number is not None:
                highest = max(highest, number)
    return highest + 1

def _ensure_split_part_root_collection(context, source_root, part_number: int):
    parent = source_root
    if parent is None:
        return None

    part_name = _format_split_part_collection_name(getattr(source_root, "name", ""), part_number)
    target = parent.children.get(part_name)
    if target is None:
        target = bpy.data.collections.new(part_name)
        parent.children.link(target)

    try:
        source_color = getattr(source_root, "color_tag", None)
        if source_color:
            target.color_tag = source_color
    except Exception:
        pass

    split_source_path = _derive_split_export_source_path(source_root, part_name)
    _set_ie_source_path_tag(target, split_source_path)
    return target

def _find_p3d_root_collection_for_collection(context, collection, *, require_p3d: bool = False):
    if collection is None:
        return None

    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    if scene_root is None:
        if require_p3d and not _looks_like_p3d_collection_name(getattr(collection, "name", "") or ""):
            return None
        return collection

    try:
        path = _find_collection_path(scene_root, collection.as_pointer())
    except Exception:
        path = None

    if not path:
        if require_p3d and not _looks_like_p3d_collection_name(getattr(collection, "name", "") or ""):
            return None
        return collection

    best = None
    for col in path:
        if _looks_like_p3d_collection_name(getattr(col, "name", "") or ""):
            best = col
    if best is not None:
        return best
    return None if require_p3d else collection

def _object_is_directly_or_indirectly_in_collection(root_collection, obj) -> bool:
    if root_collection is None or obj is None:
        return False
    for col in getattr(obj, "users_collection", []):
        try:
            if _find_collection_path(root_collection, col.as_pointer()):
                return True
        except Exception:
            pass
    return False

def _model_split_target_category_spec(category_token: str):
    from .nh_scatter import (_MODEL_SPLIT_TARGET_CATEGORY_SPECS)
    token = (category_token or "RESOLUTION").strip().upper()
    return _MODEL_SPLIT_TARGET_CATEGORY_SPECS.get(token) or _MODEL_SPLIT_TARGET_CATEGORY_SPECS["RESOLUTION"]

def _model_split_target_category_label(category_token: str) -> str:
    from .nh_scatter import (_MODEL_SPLIT_TARGET_CATEGORY_ITEMS)
    token = (category_token or "RESOLUTION").strip().upper()
    for item in _MODEL_SPLIT_TARGET_CATEGORY_ITEMS:
        if item[0] == token:
            return item[1]
    return "Resolution"

def _model_split_category_for_lod_token(lod_token: str) -> str:
    from .nh_scatter import (_MODEL_SPLIT_GEOMETRY_LODS, _MODEL_SPLIT_POINT_CLOUD_LODS, _MODEL_SPLIT_ROADWAY_LODS)
    token = str(lod_token or "").strip()
    if token in _MODEL_SPLIT_GEOMETRY_LODS:
        return "GEOMETRIES"
    if token in _MODEL_SPLIT_POINT_CLOUD_LODS:
        return "POINT_CLOUDS"
    if token in _MODEL_SPLIT_ROADWAY_LODS:
        return "ROADWAY"
    return "RESOLUTION"

def _model_split_category_for_object(obj) -> str:
    from .nh_snap import (_logical_collection_name)
    if obj is not None and hasattr(obj, "a3ob_properties_object"):
        try:
            props = obj.a3ob_properties_object
            if bool(getattr(props, "is_a3_lod", False)):
                return _model_split_category_for_lod_token(getattr(props, "lod", ""))
        except Exception:
            pass

    name = _logical_collection_name(getattr(obj, "name", "") if obj is not None else "")
    if "roadway" in name:
        return "ROADWAY"
    if "memory" in name or "point" in name:
        return "POINT_CLOUDS"
    if "geometry" in name:
        return "GEOMETRIES"
    return "RESOLUTION"

def _ensure_model_split_target_category_collection(target_root, category_token: str):
    from .nh_snap import (_ensure_named_child_collection)
    if target_root is None:
        return None

    spec = _model_split_target_category_spec(category_token)
    collection = _ensure_named_child_collection(
        target_root,
        spec["collection"],
        color_tag=spec.get("color"),
        aliases=spec.get("aliases", ()),
    )
    _clear_ie_source_path_tag(collection)
    return collection

def _set_model_split_target_lod_p3d_props(obj, category_token: str):
    from .nh_collider import (_apply_collider_visual_style, _apply_object_visual_style)
    from .nh_scatter import (_ROADWAY_LOD_TOKEN, _ROADWAY_OBJECT_COLOR)
    from .nh_snap import (_collider_lod_name, _remove_p3d_named_property)
    if obj is None or obj.type != "MESH" or not hasattr(obj, "a3ob_properties_object"):
        return

    spec = _model_split_target_category_spec(category_token)
    lod_token = str(spec.get("lod", "0"))
    try:
        props = obj.a3ob_properties_object
        current_is_lod = bool(getattr(props, "is_a3_lod", False))
        current_lod = str(getattr(props, "lod", "") or "")
        current_resolution = getattr(props, "resolution", 1)
        lod_token = current_lod if current_is_lod and current_lod else str(spec.get("lod", "0"))
        props.lod = lod_token
        if current_is_lod:
            pass
        elif lod_token == "0" and current_lod == "0":
            try:
                props.resolution = max(0, int(current_resolution))
            except Exception:
                props.resolution = 1
        else:
            props.resolution = 1
        if not current_is_lod:
            props.resolution_float = float(getattr(props, "resolution", 1) or 1)
        props.is_a3_lod = True
        _remove_p3d_named_property(props, "autocenter")
        lod_name = props.get_name() if hasattr(props, "get_name") else _collider_lod_name(lod_token)
        if lod_name:
            obj.name = lod_name
            if obj.data is not None:
                obj.data.name = lod_name
    except Exception:
        pass

    if lod_token == "6":
        _apply_collider_visual_style(obj)
    elif lod_token == _ROADWAY_LOD_TOKEN:
        _apply_object_visual_style(obj, _ROADWAY_OBJECT_COLOR)

def _model_split_id_key(id_data):
    if id_data is None:
        return None
    try:
        return id_data.as_pointer()
    except Exception:
        return id(id_data)

def _model_split_add_unique_collection(collections, seen, collection):
    key = _model_split_id_key(collection)
    if key is None or key in seen:
        return False
    seen.add(key)
    collections.append(collection)
    return True

def _model_split_selected_p3d_root_collections(context):
    roots = []
    seen = set()

    for item in getattr(context, "selected_ids", []) or []:
        if not isinstance(item, bpy.types.Collection):
            continue
        root = _find_p3d_root_collection_for_collection(context, item, require_p3d=True)
        if root is not None:
            _model_split_add_unique_collection(roots, seen, root)

    for obj in getattr(context, "selected_objects", []) or []:
        root = _find_p3d_root_collection_for_object(context, obj)
        if root is not None:
            _model_split_add_unique_collection(roots, seen, root)

    return roots

def _model_split_merge_source_roots_from_settings(context, settings):
    roots = []
    seen = set()
    for item in getattr(settings, "merge_sources", []) or []:
        collection = getattr(item, "collection", None)
        root = _find_p3d_root_collection_for_collection(context, collection, require_p3d=True)
        if root is not None:
            _model_split_add_unique_collection(roots, seen, root)
    return roots

def _model_split_merge_source_collection_from_settings(context, settings):
    from .nh_scatter import (_model_split_collection_from_enum_key)
    selected = _model_split_collection_from_enum_key(
        context,
        getattr(settings, "merge_source_collection_key", ""),
    )
    if selected is not None:
        return selected
    return getattr(settings, "merge_source_collection", None)

def _sort_model_split_merge_sources(settings):
    from .nh_scatter import (_model_split_merge_collection_sort_key)
    entries = []
    seen = set()
    for item in getattr(settings, "merge_sources", []) or []:
        collection = getattr(item, "collection", None)
        if collection is None:
            continue
        key = _model_split_id_key(collection)
        if key is None or key in seen:
            continue
        seen.add(key)
        entries.append((collection, getattr(collection, "name", "") or getattr(item, "name", "") or ""))

    entries.sort(key=lambda entry: _model_split_merge_collection_sort_key(entry[0]))
    settings.merge_sources.clear()
    for collection, name in entries:
        item = settings.merge_sources.add()
        item.collection = collection
        item.name = name
    settings.merge_sources_index = max(0, min(int(getattr(settings, "merge_sources_index", 0) or 0), len(settings.merge_sources) - 1))

def _model_split_category_for_collection_name(name: str):
    from .nh_scatter import (_COLLIDER_COLLECTION_ALIASES, _COLLIDER_COLLECTION_NAME, _MEMORY_COLLECTION_ALIASES, _MEMORY_COLLECTION_NAME, _MISC_COLLECTION_NAME, _VISUALS_COLLECTION_NAME)
    from .nh_snap import (_logical_collection_name, _logical_collection_names)
    logical = _logical_collection_name(_strip_blender_numeric_suffix(name or ""))
    if logical in _logical_collection_names(_VISUALS_COLLECTION_NAME):
        return "RESOLUTION"
    if logical in _logical_collection_names(_COLLIDER_COLLECTION_NAME, _COLLIDER_COLLECTION_ALIASES):
        return "GEOMETRIES"
    if logical in _logical_collection_names(_MEMORY_COLLECTION_NAME, _MEMORY_COLLECTION_ALIASES):
        return "POINT_CLOUDS"
    if logical in _logical_collection_names(_MISC_COLLECTION_NAME):
        return "ROADWAY"
    return None

def _model_split_canonical_collection_name(name: str) -> str:
    clean = _strip_blender_numeric_suffix((name or "").strip())
    return clean or (name or "Collection")

def _model_split_is_p3d_lod_object(obj) -> bool:
    from .nh_assets import (_is_p3d_proxy_object)
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    if _is_p3d_proxy_object(obj):
        return False
    if not hasattr(obj, "a3ob_properties_object"):
        return False
    try:
        return bool(getattr(obj.a3ob_properties_object, "is_a3_lod", False))
    except Exception:
        return False

def _model_split_is_merge_lod_root(obj) -> bool:
    return _model_split_is_p3d_lod_object(obj) and getattr(obj, "parent", None) is None

def _model_split_lod_merge_category(obj) -> str:
    if _model_split_is_p3d_lod_object(obj):
        try:
            return _model_split_category_for_lod_token(getattr(obj.a3ob_properties_object, "lod", ""))
        except Exception:
            return "RESOLUTION"
    return _model_split_category_for_object(obj)

def _model_split_merge_collection_path(target_root, source_root, obj):
    from .nh_snap import (_ensure_named_child_collection)
    path = _best_object_collection_path_under_root(source_root, obj)
    if not path or len(path) <= 1:
        return target_root

    current = target_root
    for idx, source_col in enumerate(list(path)[1:]):
        if idx == 0:
            category = _model_split_category_for_collection_name(getattr(source_col, "name", "") or "")
            if category:
                current = _ensure_model_split_target_category_collection(target_root, category)
                if current is None:
                    return target_root
                continue

        name = _model_split_canonical_collection_name(getattr(source_col, "name", "") or "")
        color_tag = None
        try:
            color_tag = getattr(source_col, "color_tag", None)
        except Exception:
            color_tag = None
        current = _ensure_named_child_collection(current, name, color_tag=color_tag)
        if current is None:
            return target_root
        _clear_ie_source_path_tag(current)

    return current

def _model_split_merge_destination_collection(target_root, source_root, obj):
    lod_obj = obj if _model_split_is_p3d_lod_object(obj) else None
    if lod_obj is None:
        parent = getattr(obj, "parent", None)
        if _model_split_is_p3d_lod_object(parent):
            lod_obj = parent

    if lod_obj is not None:
        return _ensure_model_split_target_category_collection(
            target_root,
            _model_split_lod_merge_category(lod_obj),
        ) or target_root

    return _model_split_merge_collection_path(target_root, source_root, obj)

def _move_object_from_root_to_collection(obj, target_collection, source_root):
    if obj is None or target_collection is None:
        return
    _link_object_to_collection(obj, target_collection)
    if source_root is not None:
        _unlink_object_from_collection_tree(obj, source_root, keep_collection=target_collection)
    if not _collection_directly_contains_object(target_collection, obj):
        _link_object_to_collection(obj, target_collection)
    _clear_ie_source_path_tag(obj)

def _model_split_move_root_contents_to_merge_target(target_root, source_root):
    moved = 0
    objects = list(_collect_collection_objects_recursive(source_root))
    for obj in objects:
        dest = _model_split_merge_destination_collection(target_root, source_root, obj)
        before_in_target = _object_is_directly_or_indirectly_in_collection(target_root, obj)
        _move_object_from_root_to_collection(obj, dest, source_root)
        if not before_in_target or not _same_id_data(source_root, target_root):
            moved += 1
    return moved

def _model_split_lod_merge_key(obj):
    from .nh_snap import (_p3d_lod_signature_from_props, _format_resolution_lod_index_value, _get_p3d_data_p3d_module, _lod_signature_key)
    if not _model_split_is_merge_lod_root(obj):
        return None

    props = obj.a3ob_properties_object
    p3d_mod = _get_p3d_data_p3d_module()
    if p3d_mod is not None:
        signature = _p3d_lod_signature_from_props(props, p3d_mod)
        if signature is not None:
            return ("SIG", _lod_signature_key(signature))

    try:
        lod_token = str(getattr(props, "lod", "") or "").strip()
    except Exception:
        lod_token = ""

    resolution_key = ""
    if lod_token == "0":
        try:
            resolution_key = _format_resolution_lod_index_value(getattr(props, "resolution", 0))
        except Exception:
            resolution_key = "0"
    elif lod_token == "":
        try:
            resolution_key = _format_resolution_lod_index_value(getattr(props, "resolution_float", 0.0))
        except Exception:
            resolution_key = "0"

    return ("LOD", lod_token, resolution_key)

def _model_split_lod_merge_size(obj):
    data = getattr(obj, "data", None)
    if data is None:
        return 0
    try:
        return len(data.polygons) * 1000000 + len(data.vertices) * 1000 + len(data.edges)
    except Exception:
        return 0

def _model_split_mesh_has_any_data(obj) -> bool:
    data = getattr(obj, "data", None)
    if data is None:
        return False
    try:
        return len(data.vertices) > 0 or len(data.edges) > 0 or len(data.polygons) > 0
    except Exception:
        return False

def _model_split_object_ptr(obj):
    if obj is None:
        return None
    try:
        return obj.as_pointer()
    except Exception:
        return None

def _model_split_choose_lod_merge_anchor(objects, preferred_object_ptrs=None):
    live = [obj for obj in objects if obj is not None and getattr(obj, "type", None) == "MESH"]
    if not live:
        return None
    preferred_object_ptrs = set(preferred_object_ptrs or ())
    preferred = [obj for obj in live if _model_split_object_ptr(obj) in preferred_object_ptrs]
    return max(preferred or live, key=_model_split_lod_merge_size)

def _model_split_rewire_object_refs(objects, object_map):
    if not object_map:
        return 0

    rewired = 0
    for obj in objects or []:
        if obj is None:
            continue
        for modifier in getattr(obj, "modifiers", []) or []:
            try:
                target = getattr(modifier, "object", None)
            except Exception:
                target = None
            if target in object_map:
                try:
                    modifier.object = object_map[target]
                    rewired += 1
                except Exception:
                    pass

        for constraint in getattr(obj, "constraints", []) or []:
            try:
                target = getattr(constraint, "target", None)
            except Exception:
                target = None
            if target in object_map:
                try:
                    constraint.target = object_map[target]
                    rewired += 1
                except Exception:
                    pass
    return rewired

def _model_split_reparent_children_for_lod_merge(lod_roots, anchor_obj):
    if anchor_obj is None:
        return 0

    root_set = {obj for obj in lod_roots if obj is not None}
    reparented = 0
    for root in list(root_set):
        for child in list(getattr(root, "children", []) or []):
            if child in root_set:
                continue
            try:
                world_matrix = child.matrix_world.copy()
            except Exception:
                world_matrix = None
            try:
                child.parent = anchor_obj
                if world_matrix is not None:
                    child.matrix_world = world_matrix
                reparented += 1
            except Exception:
                pass
    return reparented

def _model_split_refresh_lod_object_name(obj):
    from .nh_snap import (_remove_p3d_named_property)
    if obj is None or getattr(obj, "type", None) != "MESH" or not hasattr(obj, "a3ob_properties_object"):
        return
    try:
        props = obj.a3ob_properties_object
        props.is_a3_lod = True
        _remove_p3d_named_property(props, "autocenter")
        lod_name = props.get_name() if hasattr(props, "get_name") else ""
        if lod_name:
            obj.name = lod_name
            if obj.data is not None:
                obj.data.name = lod_name
    except Exception:
        pass

def _set_p3d_proxy_index_safe(proxy_obj, proxy_index: int):
    if proxy_obj is None or not hasattr(proxy_obj, "a3ob_properties_object_proxy"):
        return False
    try:
        props = proxy_obj.a3ob_properties_object_proxy
    except Exception:
        return False

    changed = False
    if hasattr(props, "index"):
        try:
            props.index = int(proxy_index)
            changed = True
        except Exception:
            pass

    try:
        for prop in props.bl_rna.properties:
            if prop.identifier == "rna_type":
                continue
            if prop.name != "Index":
                continue
            try:
                setattr(props, prop.identifier, int(proxy_index))
                changed = True
            except Exception:
                pass
            break
    except Exception:
        pass
    return changed

def _renumber_p3d_proxy_children(parent_obj):
    from .nh_assets import (_is_p3d_proxy_object)
    if parent_obj is None:
        return 0
    proxies = [
        child for child in getattr(parent_obj, "children", []) or []
        if _is_p3d_proxy_object(child)
    ]
    proxies.sort(key=lambda obj: (getattr(obj, "name", "") or "").lower())
    changed = 0
    for idx, proxy_obj in enumerate(proxies, start=1):
        if _set_p3d_proxy_index_safe(proxy_obj, idx):
            changed += 1
    return changed

def _model_split_is_point_cloud_lod_root(obj) -> bool:
    if not _model_split_is_p3d_lod_object(obj):
        return False
    try:
        return _model_split_category_for_lod_token(getattr(obj.a3ob_properties_object, "lod", "")) == "POINT_CLOUDS"
    except Exception:
        return False

def _model_split_reference_priority_for_memory(obj, preferred_object_ptrs):
    ptr = _model_split_object_ptr(obj)
    preferred = 0 if ptr in set(preferred_object_ptrs or ()) else 1
    category = _model_split_lod_merge_category(obj)
    category_priority = {
        "RESOLUTION": 0,
        "GEOMETRIES": 1,
        "ROADWAY": 2,
        "POINT_CLOUDS": 9,
    }.get(category, 8)

    lod_priority = 5
    try:
        props = obj.a3ob_properties_object
        lod_token = str(getattr(props, "lod", "") or "").strip()
        if lod_token == "0":
            resolution = float(getattr(props, "resolution", 0.0) or 0.0)
            lod_priority = 0 if abs(resolution) <= 1e-6 else 1
    except Exception:
        pass

    return (
        preferred,
        category_priority,
        lod_priority,
        -_model_split_lod_merge_size(obj),
        (getattr(obj, "name", "") or "").lower(),
    )

def _model_split_reference_matrix_for_memory(target_root, preferred_object_ptrs, fallback_matrix):
    candidates = []
    for obj in _collect_collection_objects_recursive(target_root):
        if not _model_split_is_p3d_lod_object(obj):
            continue
        if _model_split_is_point_cloud_lod_root(obj):
            continue
        candidates.append(obj)

    if candidates:
        candidates.sort(key=lambda obj: _model_split_reference_priority_for_memory(obj, preferred_object_ptrs))
        try:
            return candidates[0].matrix_world.copy()
        except Exception:
            pass

    return fallback_matrix.copy() if fallback_matrix is not None else Matrix.Identity(4)

def _model_split_vertex_group_names_for_vertex(obj, vertex):
    names = []
    groups = getattr(obj, "vertex_groups", None)
    if groups is None:
        return names
    for item in getattr(vertex, "groups", []) or []:
        try:
            if float(getattr(item, "weight", 0.0) or 0.0) <= 0.0:
                continue
            group = groups[int(item.group)]
            name = getattr(group, "name", "") or ""
            if name:
                names.append(name)
        except Exception:
            continue
    return names

def _model_split_replace_mesh_with_points(obj, reference_matrix, point_entries):
    from .nh_snap import (_MemoryLodManager)
    old_mesh = getattr(obj, "data", None)
    mesh = bpy.data.meshes.new(_MemoryLodManager.OBJECT_NAME)
    obj.data = mesh
    obj.matrix_world = reference_matrix.copy()

    inv = reference_matrix.inverted_safe()
    mesh.vertices.add(len(point_entries))
    for idx, (world_point, _group_names) in enumerate(point_entries):
        mesh.vertices[idx].co = inv @ world_point
    mesh.update()

    try:
        for group in reversed(list(obj.vertex_groups)):
            obj.vertex_groups.remove(group)
    except Exception:
        pass

    group_indices = {}
    for idx, (_world_point, group_names) in enumerate(point_entries):
        for name in group_names:
            if name not in group_indices:
                group_indices[name] = obj.vertex_groups.new(name=name)
            try:
                group_indices[name].add([idx], 1.0, "ADD")
            except Exception:
                pass

    try:
        if old_mesh is not None and old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
    except Exception:
        pass

def _model_split_join_point_cloud_lod_group(context, target_root, lod_roots, preferred_object_ptrs=None):
    from .nh_snap import (_MemoryLodManager)
    lod_roots = [obj for obj in lod_roots if obj is not None and getattr(obj, "type", None) == "MESH"]
    if not lod_roots:
        return None, 0, 0, 0

    _remove_plain_axis_constraints_from_objects(lod_roots, context=context, keep_world_transform=True)
    try:
        context.view_layer.update()
    except Exception:
        pass

    anchor_obj = _model_split_choose_lod_merge_anchor(lod_roots, preferred_object_ptrs=preferred_object_ptrs)
    if anchor_obj is None:
        return None, 0, 0, 0

    try:
        fallback_matrix = anchor_obj.matrix_world.copy()
    except Exception:
        fallback_matrix = Matrix.Identity(4)
    reference_matrix = _model_split_reference_matrix_for_memory(target_root, preferred_object_ptrs, fallback_matrix)

    point_entries = []
    for obj in lod_roots:
        mesh = getattr(obj, "data", None)
        if mesh is None:
            continue
        matrix_world = obj.matrix_world.copy()
        for vertex in getattr(mesh, "vertices", []) or []:
            point_entries.append((
                matrix_world @ vertex.co,
                _model_split_vertex_group_names_for_vertex(obj, vertex),
            ))

    _model_split_replace_mesh_with_points(anchor_obj, reference_matrix, point_entries)
    _MemoryLodManager.apply_p3d_props(anchor_obj)
    _model_split_refresh_lod_object_name(anchor_obj)

    removed = 0
    for obj in list(lod_roots):
        if obj == anchor_obj:
            continue
        try:
            if bpy.data.objects.get(obj.name) == obj:
                bpy.data.objects.remove(obj, do_unlink=True)
                removed += 1
        except Exception:
            pass

    return anchor_obj, removed, 0, 0

def _model_split_join_lod_root_group(context, target_root, lod_roots, preferred_object_ptrs=None):
    from .nh_assets import (_repair_invalid_p3d_selection_links)
    from .nh_base import (_fmt_exc)
    lod_roots = [obj for obj in lod_roots if obj is not None and getattr(obj, "type", None) == "MESH"]
    if len(lod_roots) <= 1:
        return lod_roots[0] if lod_roots else None, 0, 0, 0
    if all(_model_split_is_point_cloud_lod_root(obj) for obj in lod_roots):
        return _model_split_join_point_cloud_lod_group(
            context,
            target_root,
            lod_roots,
            preferred_object_ptrs=preferred_object_ptrs,
        )

    anchor_obj = _model_split_choose_lod_merge_anchor(lod_roots, preferred_object_ptrs=preferred_object_ptrs)
    if anchor_obj is None:
        return None, 0, 0, 0

    object_map = {obj: anchor_obj for obj in lod_roots if obj is not anchor_obj}
    all_target_objects = _collect_collection_objects_recursive(target_root)
    rewired = _model_split_rewire_object_refs(all_target_objects, object_map)
    reparented = _model_split_reparent_children_for_lod_merge(lod_roots, anchor_obj)

    joinable = [
        obj for obj in lod_roots
        if obj is not None and getattr(obj, "type", None) == "MESH" and _model_split_mesh_has_any_data(obj)
    ]
    joined_count = 0
    if len(joinable) > 1:
        try:
            bpy.ops.object.select_all(action="DESELECT")
        except Exception:
            pass

        for obj in joinable:
            _ensure_object_visible_for_ops(obj)
            try:
                obj.select_set(True)
            except Exception:
                pass
        try:
            context.view_layer.objects.active = anchor_obj
            bpy.ops.object.join()
            active_after = getattr(context.view_layer.objects, "active", None)
            if active_after is not None and getattr(active_after, "type", None) == "MESH":
                anchor_obj = active_after
            joined_count = max(0, len(joinable) - 1)
        except Exception as e:
            raise RuntimeError(f"Join duplicate LOD roots failed: {_fmt_exc(e)}")

    removed_empty = 0
    for obj in list(lod_roots):
        try:
            if obj == anchor_obj:
                continue
            if bpy.data.objects.get(obj.name) == obj:
                bpy.data.objects.remove(obj, do_unlink=True)
                removed_empty += 1
        except ReferenceError:
            pass
        except Exception:
            pass

    _model_split_refresh_lod_object_name(anchor_obj)
    try:
        _repair_invalid_p3d_selection_links(anchor_obj)
    except Exception as e:
        print(f"Model Split merge: selection repair skipped for {anchor_obj.name}: {_fmt_exc(e)}")

    _renumber_p3d_proxy_children(anchor_obj)
    return anchor_obj, joined_count + removed_empty, reparented, rewired

def _model_split_merge_duplicate_lods(context, target_root, preferred_object_ptrs=None):
    buckets = {}
    for obj in _collect_collection_objects_recursive(target_root):
        key = _model_split_lod_merge_key(obj)
        if key is None:
            continue
        buckets.setdefault(key, []).append(obj)

    merged_objects = []
    merged_lod_groups = 0
    joined_roots = 0
    reparented = 0
    rewired = 0

    for _key, objects in buckets.items():
        if objects and all(_model_split_is_point_cloud_lod_root(obj) for obj in objects):
            merged_obj, joined, child_count, ref_count = _model_split_join_point_cloud_lod_group(
                context,
                target_root,
                objects,
                preferred_object_ptrs=preferred_object_ptrs,
            )
            if merged_obj is not None:
                if len(objects) > 1:
                    merged_lod_groups += 1
                joined_roots += joined
                reparented += child_count
                rewired += ref_count
                merged_objects.append(merged_obj)
            continue
        if len(objects) <= 1:
            _renumber_p3d_proxy_children(objects[0])
            continue
        merged_obj, joined, child_count, ref_count = _model_split_join_lod_root_group(
            context,
            target_root,
            objects,
            preferred_object_ptrs=preferred_object_ptrs,
        )
        if merged_obj is None:
            continue
        merged_lod_groups += 1
        joined_roots += joined
        reparented += child_count
        rewired += ref_count
        merged_objects.append(merged_obj)

    return {
        "merged_objects": merged_objects,
        "merged_lod_groups": merged_lod_groups,
        "joined_roots": joined_roots,
        "reparented": reparented,
        "rewired": rewired,
    }

def _remove_empty_root_collection(context, collection):
    from .nh_snap import (_find_parent_collection)
    if collection is None:
        return 0
    _remove_empty_subcollections(collection)
    if len(collection.objects) > 0 or len(collection.children) > 0:
        return 0

    parent = None
    scene_root = getattr(getattr(context, "scene", None), "collection", None)
    if scene_root is not None:
        parent = _find_parent_collection(scene_root, collection)
    if parent is not None:
        try:
            parent.children.unlink(collection)
        except Exception:
            pass

    try:
        if int(getattr(collection, "users", 0) or 0) == 0 and bpy.data.collections.get(collection.name) == collection:
            bpy.data.collections.remove(collection)
            return 1
    except Exception:
        pass
    return 0

def _resolve_model_split_merge_roots(context, settings):
    target_root = None
    picked_target = getattr(settings, "named_target_collection", None)
    if picked_target is not None:
        target_root = _find_p3d_root_collection_for_collection(context, picked_target, require_p3d=True)
        if target_root is None:
            raise RuntimeError("Target Model must be a .p3d root collection or one of its child collections")

    listed_roots = _model_split_merge_source_roots_from_settings(context, settings)
    selected_roots = _model_split_selected_p3d_root_collections(context)
    candidate_roots = listed_roots if listed_roots else selected_roots

    if target_root is None:
        if len(candidate_roots) < 2:
            raise RuntimeError("Pick Target Model or select/add at least two .p3d collections to merge")
        target_root = candidate_roots[0]

    source_roots = []
    seen = {_model_split_id_key(target_root)}
    for root in candidate_roots:
        if root is None:
            continue
        _model_split_add_unique_collection(source_roots, seen, root)

    if not source_roots:
        raise RuntimeError("Add or select at least one source .p3d collection different from Target Model")

    try:
        settings.named_target_collection = target_root
    except Exception:
        pass

    return target_root, source_roots

def _collect_model_split_selected_mesh_objects(context):
    selected = [
        obj for obj in getattr(context, "selected_objects", [])
        if obj is not None and getattr(obj, "type", None) == "MESH"
    ]
    if selected:
        return selected

    active_objects = getattr(getattr(context, "view_layer", None), "objects", None)
    active_obj = getattr(active_objects, "active", None) if active_objects is not None else None
    if active_obj is not None and getattr(active_obj, "type", None) == "MESH":
        return [active_obj]

    return []

def _model_split_single_source_root_for_objects(context, objects):
    roots = {}
    missing = []
    for obj in objects or []:
        root = _find_p3d_root_collection_for_object(context, obj)
        if root is None:
            missing.append(getattr(obj, "name", "<unnamed>"))
            continue
        roots[root.as_pointer()] = root

    if missing:
        preview = ", ".join(missing[:5])
        if len(missing) > 5:
            preview += ", ..."
        raise RuntimeError(f"Selected objects are not inside a .p3d root collection: {preview}")
    if not roots:
        raise RuntimeError("Select mesh objects inside one .p3d root collection")
    if len(roots) != 1:
        root_names = ", ".join(sorted({root.name for root in roots.values()}, key=lambda x: x.lower()))
        raise RuntimeError(f"Select mesh objects from exactly one .p3d root collection (found: {root_names})")
    return next(iter(roots.values()))

def _resolve_model_split_source_root(context, settings, objects):
    picked = getattr(settings, "named_source_collection", None)
    source_root = _find_p3d_root_collection_for_collection(context, picked, require_p3d=True) if picked is not None else None
    if picked is not None and source_root is None:
        raise RuntimeError("Source Model must be a .p3d root collection or one of its child collections")
    if source_root is None:
        source_root = _model_split_single_source_root_for_objects(context, objects)

    outside = [
        getattr(obj, "name", "<unnamed>")
        for obj in objects or []
        if not _object_is_directly_or_indirectly_in_collection(source_root, obj)
    ]
    if outside:
        preview = ", ".join(outside[:5])
        if len(outside) > 5:
            preview += ", ..."
        raise RuntimeError(f"Selected objects are not inside source collection {source_root.name}: {preview}")

    try:
        settings.named_source_collection = source_root
    except Exception:
        pass
    return source_root

def _separate_edit_selection_for_model_split(context, settings, copy_selection: bool = False):
    from .nh_base import (_fmt_exc)
    edit_obj = getattr(context, "edit_object", None)
    if edit_obj is None or getattr(edit_obj, "type", None) != "MESH":
        raise RuntimeError("Enter Edit Mode on a mesh and select the part to separate")

    source_root = _resolve_model_split_source_root(context, settings, [edit_obj])

    try:
        before_ptrs = {obj.as_pointer() for obj in bpy.data.objects}
    except Exception:
        before_ptrs = set()

    if copy_selection:
        try:
            bpy.ops.mesh.duplicate()
        except Exception as e:
            raise RuntimeError(f"Duplicate selected mesh part failed: {_fmt_exc(e)}")

    try:
        bpy.ops.mesh.separate(type="SELECTED")
    except Exception as e:
        raise RuntimeError(f"Separate selected mesh part failed: {_fmt_exc(e)}")

    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass

    created = []
    for obj in bpy.data.objects:
        if getattr(obj, "type", None) != "MESH":
            continue
        try:
            if obj.as_pointer() in before_ptrs:
                continue
        except Exception:
            continue
        created.append(obj)

    if not created:
        created = [
            obj for obj in getattr(context, "selected_objects", [])
            if obj is not None and getattr(obj, "type", None) == "MESH" and obj != edit_obj
        ]

    if not created:
        raise RuntimeError("Select faces/edges/vertices before separating")

    return source_root, created, True

def _model_split_source_and_objects_for_transfer(context, settings, copy_selection: bool = False):
    mode = str(getattr(context, "mode", "") or "").upper()
    if mode in {"EDIT_MESH", "EDIT"}:
        return _separate_edit_selection_for_model_split(context, settings, copy_selection=copy_selection)

    selected = _collect_model_split_selected_mesh_objects(context)
    if not selected:
        raise RuntimeError("Select a mesh object or selected geometry in Edit Mode")

    source_root = _resolve_model_split_source_root(context, settings, selected)
    return source_root, selected, False

def _resolve_model_split_target_part_root(context, settings, source_root, *, force_new: bool = False):
    from .nh_snap import (_find_parent_collection)
    target_root = None
    target_container = None
    picked = getattr(settings, "named_target_collection", None)
    if picked is not None:
        target_container = _find_p3d_root_collection_for_collection(context, picked, require_p3d=True)
        if target_container is None:
            raise RuntimeError("Target Model must be a .p3d root collection or one of its child collections")
    if target_container is None:
        target_container = source_root

    if force_new and target_container is not None and _looks_like_split_part_collection_name(getattr(target_container, "name", "") or ""):
        scene_root = getattr(getattr(context, "scene", None), "collection", None)
        parent = _find_parent_collection(scene_root, target_container) if scene_root is not None else None
        if parent is not None and _looks_like_p3d_collection_name(getattr(parent, "name", "") or ""):
            target_container = parent

    use_existing_part = bool(
        target_container is not None and
        _looks_like_split_part_collection_name(getattr(target_container, "name", "") or "")
    )

    if force_new or not use_existing_part:
        part_number = _next_split_part_collection_number(target_container)
        target_root = _ensure_split_part_root_collection(context, target_container, part_number)
        if target_root is None:
            raise RuntimeError("Could not create destination part collection")
        try:
            settings.part_number = part_number
        except Exception:
            pass
    else:
        target_root = target_container

    try:
        settings.named_target_collection = target_container
    except Exception:
        pass

    return target_root

def _add_model_split_part_to_planner(context, target_root):
    from .nh_snap import (_find_parent_collection)
    planner_path = _resolve_collection_source_path(target_root)
    if not planner_path and _looks_like_split_part_collection_name(getattr(target_root, "name", "") or ""):
        scene_root = getattr(getattr(context, "scene", None), "collection", None)
        parent = _find_parent_collection(scene_root, target_root) if scene_root is not None else None
        if parent is not None:
            planner_path = _derive_split_export_source_path(parent, getattr(target_root, "name", "") or "")
            if planner_path:
                _set_ie_source_path_tag(target_root, planner_path)
    if not planner_path:
        return False, ""
    try:
        added = _planner_add_import_file(context.scene.cray_ie_settings, planner_path)
    except Exception:
        added = False
    return added, planner_path

def _focus_created_split_objects(context, dest_root, created):
    from .nh_snap import (_deselect_all_in_view_layer)
    _ensure_collection_visible_in_view_layer(context, dest_root)
    if not created:
        return

    _deselect_all_in_view_layer(context)
    for obj in created:
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
    try:
        context.view_layer.objects.active = created[0]
    except Exception:
        pass

def _prepare_moved_objects_for_named_split(objects):
    moved_set = {obj for obj in objects if obj is not None}
    if not moved_set:
        return

    for obj in list(moved_set):
        if obj is None:
            continue

        world_matrix = None
        try:
            world_matrix = obj.matrix_world.copy()
        except Exception:
            pass

        try:
            parent_obj = obj.parent
        except Exception:
            parent_obj = None

        if parent_obj not in moved_set:
            try:
                obj.parent = None
            except Exception:
                pass
            if world_matrix is not None:
                try:
                    obj.matrix_world = world_matrix
                except Exception:
                    pass

        _clear_ie_source_path_tag(obj)

def _duplicate_object_for_split(obj):
    if obj is None:
        return None

    new_obj = obj.copy()
    data = getattr(obj, "data", None)
    if data is not None:
        try:
            new_obj.data = data.copy()
        except Exception:
            pass

    try:
        new_obj.parent = None
    except Exception:
        pass

    try:
        new_obj.matrix_world = obj.matrix_world.copy()
    except Exception:
        pass

    _clear_ie_source_path_tag(new_obj)
    return new_obj

def _rewire_split_copy_object_refs(copies_by_source):
    source_to_copy = {src: dup for src, dup in copies_by_source.items() if src is not None and dup is not None}
    if not source_to_copy:
        return

    for source_obj, dup_obj in list(source_to_copy.items()):
        if dup_obj is None:
            continue

        try:
            parent_src = source_obj.parent
        except Exception:
            parent_src = None

        world_matrix = None
        try:
            world_matrix = source_obj.matrix_world.copy()
        except Exception:
            world_matrix = None

        if parent_src in source_to_copy:
            dup_parent = source_to_copy[parent_src]
            try:
                dup_obj.parent = dup_parent
                dup_obj.matrix_parent_inverse = dup_parent.matrix_world.inverted()
            except Exception:
                pass
            if world_matrix is not None:
                try:
                    dup_obj.matrix_world = world_matrix
                except Exception:
                    pass
        else:
            try:
                dup_obj.parent = None
            except Exception:
                pass
            if world_matrix is not None:
                try:
                    dup_obj.matrix_world = world_matrix
                except Exception:
                    pass

        for modifier in getattr(dup_obj, "modifiers", []):
            try:
                target_obj = getattr(modifier, "object", None)
            except Exception:
                target_obj = None
            if target_obj in source_to_copy:
                try:
                    modifier.object = source_to_copy[target_obj]
                except Exception:
                    pass

        for constraint in getattr(dup_obj, "constraints", []):
            try:
                target_obj = getattr(constraint, "target", None)
            except Exception:
                target_obj = None
            if target_obj in source_to_copy:
                try:
                    constraint.target = source_to_copy[target_obj]
                except Exception:
                    pass



def _iter_object_asset_source_name_candidates(obj):
    seen = set()

    def _add(value):
        value = (value or "").strip()
        if not value:
            return
        value = _strip_blender_numeric_suffix(value)
        key = _normalize_p3d_lookup_key(value)
        if not key or key in seen:
            return
        seen.add(key)
        yield value

    for item in (obj, getattr(obj, "instance_collection", None)):
        if item is not None:
            yield from _add(getattr(item, "name", "") or "")

    parent = getattr(obj, "parent", None)
    while parent is not None:
        yield from _add(getattr(parent, "name", "") or "")
        inst = getattr(parent, "instance_collection", None)
        if inst is not None:
            yield from _add(getattr(inst, "name", "") or "")
        parent = getattr(parent, "parent", None)

    for col in getattr(obj, "users_collection", []) or []:
        yield from _add(getattr(col, "name", "") or "")


def _find_nh_objects_p3d_path_by_name(model_name: str):
    from .nh_assets import (_read_custom_asset_p3d_paths)
    from .nh_model_split import (_iter_nh_objects_source_roots, _nh_asset_library_settings)
    settings = _nh_asset_library_settings()
    for _name, root_abs in _iter_nh_objects_source_roots(settings):
        matches = _find_p3d_paths_by_name(root_abs, model_name, settings=settings)
        if matches:
            return matches[0]
    wanted = _normalize_p3d_lookup_key(model_name)
    for fp in _read_custom_asset_p3d_paths():
        if _normalize_p3d_lookup_key(fp) == wanted:
            return fp
    return ""


def _next_proxy_index_for_parent(parent_obj) -> int:
    max_index = 0
    for obj in bpy.data.objects:
        if obj.parent != parent_obj:
            continue
        if not hasattr(obj, "a3ob_properties_object_proxy"):
            continue
        try:
            pg = obj.a3ob_properties_object_proxy
        except Exception:
            continue
        for attr in ("proxy_index", "index"):
            if hasattr(pg, attr):
                try:
                    max_index = max(max_index, int(getattr(pg, attr) or 0))
                except Exception:
                    pass
        try:
            for prop in pg.bl_rna.properties:
                if prop.identifier == "rna_type":
                    continue
                if prop.name == "Index":
                    try:
                        max_index = max(max_index, int(getattr(pg, prop.identifier) or 0))
                    except Exception:
                        pass
                    break
        except Exception:
            pass
    return max_index + 1


def _source_path_from_id_data(id_data):
    from .nh_collider_exp import (_norm_path)
    if id_data is None:
        return ""
    try:
        src = id_data.get(_IE_SOURCE_PATH_KEY)
    except Exception:
        src = None
    if isinstance(src, str) and src.strip():
        return _norm_path(bpy.path.abspath(src))
    return ""


def _resolve_proxy_asset_source_p3d(obj, context=None):
    from .nh_assets import (_is_p3d_proxy_object)
    from .nh_collider_exp import (_norm_path)
    if obj is None:
        return ""
    if _is_p3d_proxy_object(obj):
        return ""

    src = _source_path_from_id_data(obj)
    if src:
        return src

    inst = getattr(obj, "instance_collection", None)
    src = _source_path_from_id_data(inst)
    if src:
        return src

    parent = getattr(obj, "parent", None)
    while parent is not None:
        src = _source_path_from_id_data(parent)
        if src:
            return src
        inst = getattr(parent, "instance_collection", None)
        src = _source_path_from_id_data(inst)
        if src:
            return src
        parent = getattr(parent, "parent", None)

    if _model_split_is_p3d_lod_object(obj):
        return ""

    for name in _iter_object_asset_source_name_candidates(obj):
        src = _find_nh_objects_p3d_path_by_name(name)
        if src:
            return src

    root_source = ""
    if context is not None:
        root = _find_p3d_root_collection_for_object(context, obj)
        if root is not None:
            root_source = _resolve_collection_source_path(root)
            root_source = _norm_path(bpy.path.abspath(root_source)) if root_source else ""

    for col in getattr(obj, "users_collection", []) or []:
        src = _resolve_collection_source_path(col)
        if src:
            src = _norm_path(bpy.path.abspath(src))
            if root_source and _norm_path(src).lower() == root_source.lower():
                continue
            return src

    return ""


def _build_proxy_from_object_instance(proxy_obj, source_obj, parent_obj, proxy_index: int, model_path: str = ""):
    source_matrix = source_obj.matrix_world.copy()
    proxy_obj.matrix_world = source_matrix
    if parent_obj is not None:
        proxy_obj.parent = parent_obj
        try:
            proxy_obj.matrix_parent_inverse = parent_obj.matrix_world.inverted_safe()
        except Exception:
            pass
        try:
            proxy_obj.matrix_world = source_matrix
        except Exception:
            pass
    if model_path:
        base = os.path.splitext(os.path.basename(model_path))[0].strip() or source_obj.name
    else:
        base = source_obj.name
    proxy_obj.name = f"proxy: {base} {int(proxy_index or 0)}"
    if getattr(proxy_obj, "data", None) is not None:
        proxy_obj.data.name = proxy_obj.name
    try:
        proxy_obj["a3ob_original_object"] = source_obj.name
    except Exception:
        pass


def _proxy_selected_collection_ids(context):
    collections = []
    seen = set()
    for item in getattr(context, "selected_ids", []) or []:
        if not isinstance(item, bpy.types.Collection):
            continue
        key = _model_split_id_key(item)
        if key in seen:
            continue
        seen.add(key)
        collections.append(item)
    return collections


def _proxy_add_source_object(sources, obj, context=None, excluded_root=None):
    from .nh_assets import (_is_p3d_proxy_object)
    if obj is None or obj in sources:
        return
    if excluded_root is not None and _object_is_directly_or_indirectly_in_collection(excluded_root, obj):
        return
    if _model_split_is_p3d_lod_object(obj) or _is_p3d_proxy_object(obj):
        return
    src = _resolve_proxy_asset_source_p3d(obj, context=context)
    if src:
        sources[obj] = src


def _proxy_selected_asset_source_map(context, excluded_root=None):
    sources = {}
    for obj in getattr(context, "selected_objects", []) or []:
        _proxy_add_source_object(sources, obj, context=context, excluded_root=excluded_root)

    selected_collections = _proxy_selected_collection_ids(context)
    if selected_collections:
        for obj in bpy.data.objects:
            inst = getattr(obj, "instance_collection", None)
            if inst in selected_collections:
                _proxy_add_source_object(sources, obj, context=context, excluded_root=excluded_root)

        for collection in selected_collections:
            root = _find_p3d_root_collection_for_collection(context, collection, require_p3d=True)
            if excluded_root is not None and (collection == excluded_root or root == excluded_root):
                continue
            for obj in _collect_collection_objects_recursive(collection):
                _proxy_add_source_object(sources, obj, context=context, excluded_root=excluded_root)
    return sources


def _proxy_explicit_source_map(context, source_obj):
    if source_obj is None:
        return {}, ""
    if getattr(source_obj, "type", None) != "MESH" and getattr(source_obj, "instance_collection", None) is None:
        return {}, "Proxy Source Object must be a mesh or collection instance"
    src = _resolve_proxy_asset_source_p3d(source_obj, context=context)
    if not src:
        return {}, f"{source_obj.name}: no source .p3d path"
    return {source_obj: src}, ""


def _proxy_lod_parent_candidate(obj, source_objs):
    parent = getattr(obj, "parent", None)
    while parent is not None:
        if parent not in source_objs and _model_split_is_p3d_lod_object(parent):
            return parent
        parent = getattr(parent, "parent", None)
    return None


def _proxy_source_context_category(context, obj, root):
    if root is None or obj is None:
        return ""
    for col in getattr(obj, "users_collection", []) or []:
        try:
            path = _find_collection_path(root, col.as_pointer())
        except Exception:
            path = None
        if not path or len(path) < 2:
            continue
        for item in list(path)[1:]:
            category = _model_split_category_for_collection_name(getattr(item, "name", "") or "")
            if category:
                return category
    return ""


def _proxy_target_lod_sort_key(obj, preferred_category: str = ""):
    from .nh_scatter import (_MODEL_SPLIT_GEOMETRY_LODS, _MODEL_SPLIT_POINT_CLOUD_LODS, _MODEL_SPLIT_ROADWAY_LODS)
    category = _model_split_lod_merge_category(obj)
    category_score = 0 if preferred_category and category == preferred_category else 1
    lod_score = 5
    try:
        props = obj.a3ob_properties_object
        lod_token = str(getattr(props, "lod", "") or "").strip()
        if lod_token == "0":
            resolution = float(getattr(props, "resolution", 0.0) or 0.0)
            lod_score = 0 if abs(resolution) <= 1e-6 else 1
        elif lod_token in _MODEL_SPLIT_GEOMETRY_LODS:
            lod_score = 2
        elif lod_token in _MODEL_SPLIT_ROADWAY_LODS:
            lod_score = 3
        elif lod_token in _MODEL_SPLIT_POINT_CLOUD_LODS:
            lod_score = 9
    except Exception:
        pass
    return (
        category_score,
        lod_score,
        -_model_split_lod_merge_size(obj),
        (getattr(obj, "name", "") or "").lower(),
    )


def _proxy_target_from_source_context(context, source_objs):
    ordered = []
    active = getattr(getattr(context, "view_layer", None), "objects", None)
    active_obj = getattr(active, "active", None)
    if active_obj in source_objs:
        ordered.append(active_obj)
    for obj in getattr(context, "selected_objects", []) or []:
        if obj in source_objs and obj not in ordered:
            ordered.append(obj)

    for obj in ordered:
        parent = _proxy_lod_parent_candidate(obj, source_objs)
        if parent is not None:
            return parent

    for obj in ordered:
        for col in getattr(obj, "users_collection", []) or []:
            candidates = [
                candidate for candidate in getattr(col, "objects", []) or []
                if candidate not in source_objs and _model_split_is_p3d_lod_object(candidate)
            ]
            if candidates:
                category = _model_split_category_for_collection_name(getattr(col, "name", "") or "")
                candidates.sort(key=lambda candidate: _proxy_target_lod_sort_key(candidate, category))
                return candidates[0]

    for obj in ordered:
        root = _find_p3d_root_collection_for_object(context, obj)
        if root is None:
            continue
        preferred_category = _proxy_source_context_category(context, obj, root)
        candidates = [
            candidate for candidate in _collect_collection_objects_recursive(root)
            if candidate not in source_objs and _model_split_is_p3d_lod_object(candidate)
        ]
        if candidates:
            candidates.sort(key=lambda candidate: _proxy_target_lod_sort_key(candidate, preferred_category))
            return candidates[0]
    return None


def _pick_proxy_target_object(context, explicit_obj=None, source_objs=None):
    if explicit_obj is not None and explicit_obj.type == "MESH":
        return explicit_obj

    if source_objs is None:
        source_objs = set(_proxy_selected_asset_source_map(context))
    else:
        source_objs = set(source_objs or ())

    active = context.view_layer.objects.active
    if active is not None and active.type == "MESH" and active not in source_objs:
        return active
    for obj in context.selected_objects:
        if obj.type == "MESH" and obj not in source_objs:
            return obj

    return _proxy_target_from_source_context(context, source_objs)


def _pick_proxy_target_root_collection(context, explicit_collection=None, target_obj=None, source_objs=None):
    if explicit_collection is not None:
        root = _find_p3d_root_collection_for_collection(context, explicit_collection, require_p3d=True)
        if root is not None:
            return root

    if target_obj is not None:
        root = _find_p3d_root_collection_for_object(context, target_obj)
        if root is not None:
            return root

    source_objs = set(source_objs or ())
    for collection in _proxy_selected_collection_ids(context):
        root = _find_p3d_root_collection_for_collection(context, collection, require_p3d=True)
        if root is not None:
            return root

    active = getattr(getattr(context, "view_layer", None), "objects", None)
    active_obj = getattr(active, "active", None) if active is not None else None
    ordered = []
    if active_obj is not None:
        ordered.append(active_obj)
    for obj in getattr(context, "selected_objects", []) or []:
        if obj not in ordered:
            ordered.append(obj)
    for obj in ordered:
        if obj in source_objs:
            continue
        root = _find_p3d_root_collection_for_object(context, obj)
        if root is not None:
            return root
    return None


def _proxy_is_resolution_lod_object(obj) -> bool:
    if not _model_split_is_p3d_lod_object(obj):
        return False
    try:
        return _model_split_lod_merge_category(obj) == "RESOLUTION"
    except Exception:
        return False


def _proxy_resolution_lod_sort_key(obj):
    resolution = 0.0
    try:
        props = obj.a3ob_properties_object
        resolution = float(getattr(props, "resolution", getattr(props, "resolution_float", 0.0)) or 0.0)
    except Exception:
        pass
    return (resolution, (getattr(obj, "name", "") or "").lower())


def _proxy_selected_target_category_tokens(settings, target_obj=None):
    items = (
        ("RESOLUTION", "proxy_duplicate_resolution"),
        ("GEOMETRIES", "proxy_duplicate_geometries"),
        ("ROADWAY", "proxy_duplicate_roadway"),
        ("POINT_CLOUDS", "proxy_duplicate_point_clouds"),
    )
    tokens = [
        token for token, prop_name in items
        if bool(getattr(settings, prop_name, False))
    ]
    if tokens:
        return tokens
    if target_obj is not None:
        return [_model_split_lod_merge_category(target_obj)]
    return ["RESOLUTION"]


def _proxy_category_lod_sort_key(obj, category_token: str):
    if category_token == "RESOLUTION":
        return _proxy_resolution_lod_sort_key(obj)
    return _proxy_target_lod_sort_key(obj, category_token)


def _ensure_proxy_category_lod_object(context, target_root, category_token: str):
    from .nh_snap import (_collider_lod_name)
    if target_root is None:
        return None

    dest_collection = _ensure_model_split_target_category_collection(target_root, category_token)
    if dest_collection is None:
        return None

    spec = _model_split_target_category_spec(category_token)
    lod_token = str(spec.get("lod", "0") or "0")
    lod_name = _collider_lod_name(lod_token) if lod_token != "0" else "Resolution 0"
    mesh = bpy.data.meshes.new(lod_name)
    obj = bpy.data.objects.new(lod_name, mesh)
    dest_collection.objects.link(obj)

    if category_token == "RESOLUTION":
        try:
            _set_resolution0_p3d_lod_props(obj)
        except Exception:
            _set_model_split_target_lod_p3d_props(obj, category_token)
    else:
        _set_model_split_target_lod_p3d_props(obj, category_token)

    try:
        context.view_layer.update()
    except Exception:
        pass
    return obj


def _proxy_lod_objects_for_category(context, target_root, category_token: str, preferred_obj=None):
    if target_root is None:
        return []

    candidates = [
        obj for obj in _collect_collection_objects_recursive(target_root)
        if _model_split_is_p3d_lod_object(obj)
        and _model_split_lod_merge_category(obj) == category_token
    ]
    if preferred_obj is not None and _model_split_lod_merge_category(preferred_obj) == category_token:
        if preferred_obj not in candidates:
            candidates.append(preferred_obj)

    if not candidates:
        created = _ensure_proxy_category_lod_object(context, target_root, category_token)
        if created is not None:
            candidates.append(created)

    seen = set()
    ordered = []
    for obj in sorted(candidates, key=lambda item: _proxy_category_lod_sort_key(item, category_token)):
        ptr = _model_split_object_ptr(obj)
        if ptr in seen:
            continue
        seen.add(ptr)
        ordered.append(obj)

    if preferred_obj in ordered:
        ordered.remove(preferred_obj)
        ordered.insert(0, preferred_obj)
    return ordered


def _proxy_conversion_target_lods_for_categories(context, target_root, target_obj, category_tokens):
    target_lods = []
    seen = set()
    for category_token in category_tokens or ():
        for lod_obj in _proxy_lod_objects_for_category(context, target_root, category_token, preferred_obj=target_obj):
            ptr = _model_split_object_ptr(lod_obj)
            if ptr in seen:
                continue
            seen.add(ptr)
            target_lods.append(lod_obj)
    return target_lods


def _proxy_conversion_target_lods(context, target_obj, duplicate_to_all_resolution_lods: bool):
    if target_obj is None:
        return []
    if not duplicate_to_all_resolution_lods:
        return [target_obj]

    if not _proxy_is_resolution_lod_object(target_obj):
        return []

    root = _find_p3d_root_collection_for_object(context, target_obj)
    if root is None:
        return [target_obj]

    candidates = [
        obj for obj in _collect_collection_objects_recursive(root)
        if _proxy_is_resolution_lod_object(obj)
    ]
    if target_obj not in candidates:
        candidates.append(target_obj)

    seen = set()
    ordered = []
    for obj in sorted(candidates, key=_proxy_resolution_lod_sort_key):
        ptr = _model_split_object_ptr(obj)
        if ptr in seen:
            continue
        seen.add(ptr)
        ordered.append(obj)

    if target_obj in ordered:
        ordered.remove(target_obj)
    return [target_obj] + ordered


def _proxy_target_collection_for_lod(lod_obj, fallback_collection):
    if lod_obj is not None and getattr(lod_obj, "users_collection", None):
        try:
            return lod_obj.users_collection[0]
        except Exception:
            pass
    return fallback_collection


def _tag_import_source_on_imported_data(context, filepath, imported_objs, pre_collection_ptrs):
    from .nh_collider_exp import (_norm_path)
    src = _norm_path(bpy.path.abspath(filepath))
    if not src:
        return

    imported_ptrs = set()
    for obj in imported_objs:
        if obj is None:
            continue
        imported_ptrs.add(obj.as_pointer())
        try:
            obj[_IE_SOURCE_PATH_KEY] = src
        except Exception:
            pass

    if not imported_ptrs:
        return

    scene_root = context.scene.collection
    root_children = list(scene_root.children)
    new_collections = [c for c in bpy.data.collections if c.as_pointer() not in pre_collection_ptrs]

    for col in new_collections:
        if not _collection_has_any_object_ptr(col, imported_ptrs):
            continue
        try:
            col[_IE_SOURCE_PATH_KEY] = src
        except Exception:
            pass

    for col in new_collections:
        if not any(ch == col for ch in root_children):
            continue
        if not _collection_has_any_object_ptr(col, imported_ptrs):
            continue
        for nested in _iter_collection_tree(col):
            try:
                nested[_IE_SOURCE_PATH_KEY] = src
            except Exception:
                pass

