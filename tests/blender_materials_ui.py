"""Exercise the real Asset Browser in a disposable hidden Blender window.

Requires the two-material worker fixture/cache created by the render check.
Produces dist/materials_ui_result.json and quits its own Blender process.
"""
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))
os.environ['NH_MATERIALS_TEXTURE_ROOT'] = str(ROOT/'dist/material_worker_fixture')
os.environ['NH_MATERIALS_CACHE_ROOT'] = str(ROOT/'dist/materials_ui_cache')

import bpy
import addon_utils
import NH_Blender as addon
from NH_Blender import nh_materials as browser

cache = Path(browser.cache_root())
assert cache.resolve().is_relative_to((ROOT/'dist').resolve())
for path in (cache/'assets').glob('*.blend'):
    if path.parent.resolve() == (cache/'assets').resolve():
        path.unlink()
source_cache = Path(os.environ.get('NH_MATERIALS_UI_TEST_CACHE',
                                  str(ROOT/'dist/materials_fast_worker_fixture')))
shutil.copytree(source_cache, cache, dirs_exist_ok=True)
# This test exercises immediate Apply after a native tile selection; the separate
# cold-cache test exercises the asynchronous preparation and cancellation paths.
from NH_Blender.nh_materials_images import cache_full_images
for entry in json.loads((cache/'manifest.json').read_text(encoding='utf8'))['entries']:
    cache_full_images(entry['rvmat'], entry['color'])
errors = []
assert addon_utils.enable('NH_Blender', default_set=True, handle_error=errors.append) is addon, errors
assert browser._library_pending, 'Library initialization was not deferred'
window = bpy.context.window_manager.windows[0]
area = max(window.screen.areas, key=lambda item: item.width * item.height)
area.type = 'FILE_BROWSER'
area.ui_type = 'ASSETS'
attempts = 0


def finish(result):
    (ROOT/'dist/materials_ui_result.json').write_text(json.dumps(result, indent=2), encoding='utf8')
    addon_utils.disable('NH_Blender', default_set=True, handle_error=errors.append)
    properties = bpy.ops.wm.quit_blender.get_rna_type().properties
    kwargs = {'use_save_prompt': False} if 'use_save_prompt' in properties else {}
    bpy.ops.wm.quit_blender('EXEC_DEFAULT', **kwargs)


def verify():
    global attempts
    attempts += 1
    try:
        if browser._library_pending:
            if attempts > 20:
                raise RuntimeError('Deferred library initialization failed: ' + browser._status)
            return 1.0
        params = area.spaces.active.params
        if params is None:
            if attempts > 20:
                raise RuntimeError('Asset Browser parameters never initialized')
            return 1.0
        if params.asset_library_reference != browser.LIBRARY_NAME:
            params.asset_library_reference = browser.LIBRARY_NAME
            return 1.0
        region = next(item for item in area.regions if item.type == 'WINDOW')
        with bpy.context.temp_override(window=window, area=area, region=region):
            # Use the actual native tile selection API, then the actual Apply operator.
            for x in (64, 128, 220, 320):
                for y in (region.height - 64, region.height - 128, region.height - 220):
                    bpy.ops.file.select(mouse_x=x, mouse_y=y)
                    entry = browser.selected_entry(bpy.context)
                    if entry:
                        assert entry['asset_file'] in bpy.context.asset.full_library_path, bpy.context.asset.full_library_path
                        cube = bpy.context.view_layer.objects.active
                        assert cube is not None, 'Active scene object missing in Asset Browser'
                        result = bpy.ops.nh_materials.apply()
                        assert result == {'FINISHED'}, result
                        assert cube.active_material.get('nh_super_rvmat'), 'No Super shader after Apply'
                        assert cube.active_material.get('nh_material_id') == entry['id']
                        finish(dict(blender=bpy.app.version_string, passed=True,
                            addon_utils_enable=True, deferred_library_registered=True,
                            native_asset_selection=True, apply_from_asset_browser=True,
                            selected_material=entry['name'], mode=bpy.context.mode))
                        return None
        if attempts > 20:
            raise RuntimeError('Native assets could not be selected')
        return 1.0
    except Exception as exc:
        finish(dict(blender=bpy.app.version_string, passed=False, error=str(exc)))
        return None


bpy.app.timers.register(verify, first_interval=1.0)
