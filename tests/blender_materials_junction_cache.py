"""Apply restores canonical Super blueprints from legacy junction PNG caches."""
import contextlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))

import bpy
import addon_utils
import NH_Blender as addon
from NH_Blender import nh_materials as browser, nh_material_cache as blueprints
from NH_Blender import nh_materials_images as images, nh_material_shader as shader, nh_textures as textures
from NH_bundle.io import data_paa, import_paa

checks = []


def check(name, condition):
    assert condition, name
    checks.append(name)
    print('PASS:', name, flush=True)


def paa(path, rgb565):
    size = 1024
    encoded = struct.pack('<HHI', rgb565, 0, 0) * (size * size // 16)
    path.write_bytes(struct.pack('<HHHH', 0xff01, 0, size, size)
                     + len(encoded).to_bytes(3, 'little') + encoded + b'\0' * 6)


@contextlib.contextmanager
def forbid_decode():
    original = data_paa.PAA_MIPMAP.decompress
    def forbidden(*args, **kwargs):
        raise AssertionError('Apply migrated cache must not decompress original PAA')
    data_paa.PAA_MIPMAP.decompress = forbidden
    try:
        yield
    finally:
        data_paa.PAA_MIPMAP.decompress = original


assert addon_utils.enable('NH_Blender', default_set=True) is addon
with tempfile.TemporaryDirectory(prefix='apply_junction_', dir=ROOT / 'dist') as temporary:
    workspace = Path(temporary)
    physical = workspace / 'physical/NH_ObjectTextures'
    physical.mkdir(parents=True)
    alias = workspace / 'NH_ObjectTextures'
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(physical)],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
    else:
        alias.symlink_to(physical, target_is_directory=True)
    try:
        os.environ['NH_MATERIALS_TEXTURE_ROOT'] = str(alias)
        os.environ['NH_MATERIALS_CACHE_ROOT'] = str(workspace / 'materials')
        def texture_cache(create=False):
            path = workspace / 'full_png'
            if create:
                path.mkdir(parents=True, exist_ok=True)
            return str(path)
        textures._nh_texture_cache_root = texture_cache
        color = alias / 'door_wood_01_co.paa'
        normal = alias / 'door_wood_01_nohq.paa'
        rvmat = alias / 'door_wood_01.rvmat'
        paa(color, 0xf800)
        paa(normal, 0x841f)
        rvmat.write_text('PixelShaderID="Super"; VertexShaderID="Super"; '
                         'class Stage1 { texture="NH_ObjectTextures/door_wood_01_nohq.paa"; };', encoding='utf-8')
        check('Real junction points to the same original texture', color.resolve() == physical / color.name)

        # Saved preview blueprint uses canonical physical sources, as real Calculate Previews does.
        preview = bpy.data.materials.new('Preview seed')
        check('Preview creates reusable Super blueprint',
              blueprints.build(preview, str(rvmat), str(color), image_loader=images.load))
        check('Preview does not warm full-resolution Apply PNG',
              not Path(textures._paa_preview_cache_path(str(color))).exists())
        for source, space in ((color, 'SRGB'), (normal, 'DATA')):
            image, _ = import_paa.load_file(str(source), space, check_existing=False)
            legacy = Path(textures._paa_preview_cache_path(str(source), canonical=False))
            textures._save_image_as_png(image, str(legacy))
            bpy.data.images.remove(image)
            check('Legacy PNG exists for ' + source.name, legacy.is_file())
        check('Alias and physical paths share the new cache key',
              textures._paa_preview_cache_path(str(color)) == textures._paa_preview_cache_path(str(color.resolve())))
        check('Legacy keys reproduce the old alias mismatch',
              textures._paa_preview_cache_path(str(color), canonical=False)
              != textures._paa_preview_cache_path(str(color.resolve()), canonical=False))
        check('Legacy-only cache requests preparation before Apply',
              not images.full_images_ready(str(rvmat), str(color)))
        with forbid_decode():
            prepared = images.cache_full_images(str(rvmat), str(color))
        check('Preparation reuses legacy PNG without PAA decode', prepared['images'] == 2)
        check('Canonical PNGs are ready for blueprint restoration', images.full_images_ready(str(rvmat), str(color)))
        canonical = Path(textures._paa_preview_cache_path(str(color)))
        old = Path(textures._paa_preview_cache_path(str(color), canonical=False))
        check('Migration retains legacy PNG and copies exact cached bytes', old.exists()
              and canonical.read_bytes() == old.read_bytes())
        before = canonical.stat().st_mtime_ns
        entry = dict(id='junction-door', name='Door', rvmat=str(rvmat), color=str(color))
        with forbid_decode():
            material = browser.material_from_entry(entry, cache_only=True)
        check('Cache-only Apply restores an existing blueprint', material.get('nh_material_cache_hit') is True)
        check('Apply keeps full original resolution', tuple(material.node_tree.nodes['DayZ Color'].image.size) == (1024, 1024))
        check('Warm Apply does not rewrite canonical PNG', canonical.stat().st_mtime_ns == before)
        with forbid_decode():
            batch = textures._run_texture_cache_workers([str(color), str(color.resolve())],
                settings=SimpleNamespace(texture_cache_workers=1))
        check('Batch caches each physical source once across aliases',
              batch['processed'] == 1 and batch['skipped'] == 1 and not batch['failed'])
        obj = bpy.context.view_layer.objects.active
        check('Ready material applies to the selected mesh', browser.apply_material(bpy.context, material) == 1
              and obj.active_material is material)
        check('Export fields retain the original source pair',
              textures._get_p3d_material_paths(material) == (str(color), str(rvmat)))
        check('Migration leaves no temporary files', not list((workspace / 'full_png').rglob('*.tmp*')))
    finally:
        # Remove only this junction; all generated targets remain inside our disposable workspace.
        assert alias.parent.resolve() == workspace.resolve()
        if alias.exists():
            alias.rmdir() if os.name == 'nt' else alias.unlink()
addon_utils.disable('NH_Blender', default_set=True)
print('NH_APPLY_JUNCTION_CACHE_PASS', json.dumps(dict(blender=bpy.app.version_string,
      addon_file=addon.__file__, version=addon.bl_info['version'], checks=len(checks), passed=checks)))
