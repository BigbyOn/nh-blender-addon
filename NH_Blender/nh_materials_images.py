"""Small PAA mipmaps for disposable NH Materials preview workers.

The separate cache never replaces full resolution images used by Apply or P3D.
"""
import hashlib
import json
import os
import re
import time

import bpy

SCHEMA = 1
MAXIMUM_SIZE = 512


def _cache_paths(source, color_space):
    from .nh_textures import _nh_blender_shared_cache_base
    root = os.environ.get('NH_MATERIALS_PREVIEW_TEXTURE_CACHE')
    if not root:
        root = os.path.join(os.environ.get('NH_MATERIALS_CACHE_ROOT') or
                            os.path.join(_nh_blender_shared_cache_base(), 'Materials'),
                            'textures')
    normalized = os.path.normcase(os.path.abspath(source))
    key = hashlib.sha1(('%s\0%s' % (normalized, color_space)).encode('utf8')).hexdigest()
    name = re.sub(r'[<>:"/\\|?*]+', '_', os.path.splitext(os.path.basename(source))[0])
    path = os.path.join(root, key[:2], '%s__mip%d_%s.png' %
                        (name, MAXIMUM_SIZE, key[:16]))
    return path, path + '.json'


def _fingerprint(source, color_space):
    info = os.stat(source)
    return dict(schema=SCHEMA, maximum_size=MAXIMUM_SIZE,
                source=os.path.normcase(os.path.abspath(source)),
                mtime_ns=info.st_mtime_ns, size=info.st_size,
                color_space=color_space)


def _valid(path, metadata, fingerprint):
    if not os.path.isfile(path):
        return None
    try:
        with open(metadata, encoding='utf8') as file:
            cached = json.load(file)
            return cached if (cached.get('fingerprint') == fingerprint and
                              'has_alpha' in cached) else None
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def load(texture_path, keep_cache=True, color_space='SRGB'):
    """Return the existing image loader's five-tuple, using at most 512px PAA."""
    from .nh_textures import (
        _base_color_declared_has_alpha,
        _load_external_image, _load_material_preview_image,
        _remove_image_if_unused, _resolve_p3d_texture_path, _save_image_as_png,
    )
    source = _resolve_p3d_texture_path(texture_path)
    if not source:
        return None, False, '', 'missing', ''
    if os.path.splitext(source)[1].lower() != '.paa':
        return _load_material_preview_image(source, keep_cache, color_space)
    color_space = 'DATA' if color_space == 'DATA' else 'SRGB'
    path, metadata = _cache_paths(source, color_space)
    declared_alpha = _base_color_declared_has_alpha(source)
    try:
        fingerprint = _fingerprint(source, color_space)
    except OSError:
        return None, False, source, 'missing', path
    cached_metadata = _valid(path, metadata, fingerprint)
    if cached_metadata is not None:
        try:
            image = _load_external_image(path, color_space)
            has_alpha = bool(cached_metadata['has_alpha'])
            return image, has_alpha, source, 'cache_hit', path
        except (OSError, RuntimeError):
            pass

    from NH_bundle.io import data_paa, import_paa
    try:
        with open(source, 'rb') as file:
            texture = data_paa.PAA_File.read(file)
        available = [mip for mip in texture.mips
                     if max(mip.width, mip.height) <= MAXIMUM_SIZE]
        mip = (max(available, key=lambda item: item.width * item.height) if available
               else min(texture.mips, key=lambda item: item.width * item.height))
        # The existing DXT decoder, SWIZ handling and channel flattening are
        # retained; only the mip to decompress changes in this worker loader.
        texture.mips = [mip]
        image = import_paa.create_image_from_texture(source, texture, color_space)
        if image is None:
            return None, False, source, 'missing', path
        if max(image.size) > MAXIMUM_SIZE:
            factor = MAXIMUM_SIZE / max(image.size)
            image.scale(max(1, round(image.size[0] * factor)),
                        max(1, round(image.size[1] * factor)))
        has_alpha = texture.type == data_paa.PAA_Type.DXT5
        if declared_alpha is not None:
            has_alpha = declared_alpha
    except (OSError, ValueError, RuntimeError, data_paa.PAA_Error):
        return None, False, source, 'missing', path

    if keep_cache:
        temporary = path + '.tmp.png'
        try:
            _save_image_as_png(image, temporary)
            os.replace(temporary, path)
            with open(metadata + '.tmp', 'w', encoding='utf8') as file:
                json.dump(dict(fingerprint=fingerprint, has_alpha=has_alpha), file)
            os.replace(metadata + '.tmp', metadata)
            cached = _load_external_image(path, color_space)
            cached.reload()
            _remove_image_if_unused(image)
            return cached, has_alpha, source, 'cache_created', path
        except (OSError, RuntimeError):
            # A write failure still permits a preview from the decoded mip.
            pass
    return image, has_alpha, source, 'paa_runtime', path


def cache_full_images(rvmat_path, color_path=''):
    """Warm Apply's original image cache after a selected preview is ready."""
    from . import nh_material_shader as shader
    from .nh_textures import _load_material_preview_image, _remove_image_if_unused
    from .utilities import rvmat
    started = time.monotonic()
    root = shader.texture_root()
    path = rvmat.resolve_path(rvmat_path, root=root)
    if not path:
        return dict(images=0, seconds=time.monotonic() - started)
    data = rvmat.read(path)
    sources = [(color_path, 'SRGB')] if color_path else []
    for number in (1, 2, 3, 4, 5, 7):
        raw = rvmat.stage(data, number).get('texture', '')
        if raw and not raw.startswith('#'):
            sources.append((raw, 'SRGB' if number in (3, 7) else 'DATA'))
    count = 0
    cache_paths = []
    seen = set()
    for raw, space in sources:
        source = rvmat.resolve_path(raw, path, root)
        if not source or (source, space) in seen:
            continue
        seen.add((source, space))
        loaded = _load_material_preview_image(source, True, color_space=space)
        image = loaded[0]
        if image is None:
            raise ValueError('Cannot load texture: ' + source)
        count += 1
        if loaded[4]:
            cache_paths.append(loaded[4])
        _remove_image_if_unused(image)
    return dict(images=count, cache_paths=list(dict.fromkeys(cache_paths)), seconds=time.monotonic() - started)


def full_images_ready(rvmat_path, color_path=''):
    """Cheap readiness check; never decompresses textures in the UI thread."""
    from . import nh_material_shader as shader
    from .nh_textures import _paa_preview_cache_path, _texture_cache_is_valid
    from .utilities import rvmat
    root = shader.texture_root()
    path = rvmat.resolve_path(rvmat_path, root=root)
    if not path:
        raise ValueError('Cannot find RVMAT: ' + rvmat_path)
    data = rvmat.read(path)
    sources = [color_path] if color_path else []
    for number in (1, 2, 3, 4, 5, 7):
        raw = rvmat.stage(data, number).get('texture', '')
        if raw and not raw.startswith('#'):
            sources.append(raw)
    ready = True
    for raw in dict.fromkeys(sources):
        source = rvmat.resolve_path(raw, path, root)
        if not source:
            raise ValueError('Cannot find texture: ' + raw)
        if source.lower().endswith('.paa'):
            ready = _texture_cache_is_valid(source, _paa_preview_cache_path(source)) and ready
    return ready
