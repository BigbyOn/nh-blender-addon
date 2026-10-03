"""Append NH material assets and assign them in a disposable UI Blender process.

This exercises the native .blend dependency load and the add-on's deferred mesh
conversion while Material Preview redraws. It does not synthesize mouse dragging.
NH_MATERIALS_DROP_TEST_CACHE points to the worker-generated fixture cache.
With NH_MATERIALS_NATIVE_DROP_TEST=1 and --enable-event-simulate, a pointer event
and Blender's native drop operator assign each appended material to the mesh.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))
os.environ['NH_MATERIALS_TEXTURE_ROOT'] = str(ROOT / 'dist/material_worker_fixture')
os.environ['NH_MATERIALS_CACHE_ROOT'] = str(ROOT / 'dist/materials_drop_test_cache')

import bpy
import addon_utils
import NH_Blender as addon
from NH_Blender import nh_materials as browser

native_drop = os.environ.get('NH_MATERIALS_NATIVE_DROP_TEST') == '1'

source_cache = Path(os.environ.get('NH_MATERIALS_DROP_TEST_CACHE',
                                  str(ROOT / 'dist/materials_fast_worker_fixture')))
cache = Path(browser.cache_root())
shutil.copytree(source_cache, cache, dirs_exist_ok=True)
manifest = json.loads((cache / 'manifest.json').read_text(encoding='utf8'))
entries = [entry for entry in manifest['entries'] if entry.get('color')]
assert entries, 'No paired worker fixture materials'
errors = []
assert addon_utils.enable('NH_Blender', default_set=True,
                         handle_error=errors.append) is addon, errors
window = bpy.context.window_manager.windows[0]
area = max(window.screen.areas, key=lambda item: item.width * item.height)
area.type = 'VIEW_3D'
area.spaces.active.shading.type = 'MATERIAL'
cube = bpy.context.view_layer.objects.active
assert cube is not None and cube.type == 'MESH'
cube.data.materials.clear()

result = dict(blender=bpy.app.version_string, addon_utils_enable=True,
              native_blend_append=True, material_preview_redraw=True,
              mouse_drag_synthesized=False, native_drop_operator=native_drop, cases=[])
stage = 'load'
index = 0
material = None
stage_start = time.monotonic()


def finish(error=None):
    result['passed'] = error is None
    if error:
        result['error'] = str(error)
    result_file = 'materials_native_drop_result.json' if native_drop else 'materials_drop_result.json'
    (ROOT / 'dist' / result_file).write_text(
        json.dumps(result, indent=2), encoding='utf8')
    addon_utils.disable('NH_Blender', default_set=True, handle_error=errors.append)
    properties = bpy.ops.wm.quit_blender.get_rna_type().properties
    kwargs = {'use_save_prompt': False} if 'use_save_prompt' in properties else {}
    bpy.ops.wm.quit_blender('EXEC_DEFAULT', **kwargs)


def verify():
    global stage, index, material, stage_start
    try:
        if browser._library_pending:
            if time.monotonic() - stage_start > 20:
                raise RuntimeError('Deferred library initialization failed: ' + browser._status)
            return 0.25
        entry = entries[index]
        if stage == 'load':
            file_name = entry.get('asset_file', entry['id'] + '.blend')
            path = Path(file_name)
            if not path.is_absolute():
                path = cache / 'assets' / file_name
            before_images = set(bpy.data.images)
            load_start = time.monotonic()
            with bpy.data.libraries.load(str(path)) as (data_from, data_to):
                name = entry.get('asset_name', entry['name'])
                assert name in data_from.materials, (name, data_from.materials)
                data_to.materials = [name]
            material = data_to.materials[0]
            assert material is not None and material.get('nh_material_stub'), 'Expected metadata asset'
            assert set(bpy.data.images) == before_images, 'Asset appended image dependencies'
            assert not material.node_tree or len(material.node_tree.nodes) <= 2, 'Asset appended full shader'
            material.use_fake_user = True
            assert material.users == 1, ('Expected fake-user-only material', material.users)
            result['cases'].append(dict(name=entry['name'], append_seconds=time.monotonic()-load_start,
                no_image_dependencies=True, lightweight_nodes=True))
            stage_start = time.monotonic()
            stage = 'unused'
            return 0.25
        if stage == 'unused':
            assert material.get('nh_material_stub'), 'Unused fake-user asset was converted'
            if time.monotonic() - stage_start < 2:
                return 0.25
            result['cases'][-1]['fake_user_ignored'] = True
            cube.data.materials.clear()
            if native_drop:
                from bpy_extras.view3d_utils import location_3d_to_region_2d
                region = next(item for item in area.regions if item.type == 'WINDOW')
                position = location_3d_to_region_2d(region, area.spaces.active.region_3d,
                                                  cube.matrix_world.translation)
                assert position is not None, 'Cube center was not in viewport'
                window.event_simulate(type='MOUSEMOVE', value='NOTHING',
                    x=region.x+round(position.x), y=region.y+round(position.y))
                stage = 'drop'
                return 0.25
            cube.data.materials.append(material)
            stage_start = time.monotonic()
            stage = 'assigned'
            return 0.1
        if stage == 'drop':
            region = next(item for item in area.regions if item.type == 'WINDOW')
            with bpy.context.temp_override(window=window, area=area, region=region):
                dropped = bpy.ops.object.drop_named_material('INVOKE_DEFAULT', name=material.name)
            assert dropped == {'FINISHED'}, ('Native drop operator did not finish', dropped)
            assert cube.active_material == material, 'Native drop did not assign to cube'
            result['cases'][-1]['native_drop_finished'] = True
            stage_start = time.monotonic()
            stage = 'assigned'
            return 0.1
        if stage == 'assigned':
            if material.get('nh_material_stub'):
                if time.monotonic() - stage_start > 40:
                    raise RuntimeError('Assigned asset was not converted: ' + str(material.get('nh_material_error', '')))
                return 0.1
            elapsed = time.monotonic() - stage_start
            assert elapsed >= 0.5, ('Shader rebuilt without deferral', elapsed)
            assert material.get('nh_super_rvmat'), 'Assigned material has no Super graph'
            assert material.get('nh_material_id') == entry['id'], 'Wrong metadata after conversion'
            assert cube.data.uv_layers.get('NH_UV1'), 'No UV1 prepared for appended material'
            result['cases'][-1].update(deferred_conversion=True,
                conversion_seconds=elapsed, super_shader=True, uv1_ready=True)
            stage_start = time.monotonic()
            stage = 'draw'
            return 0.25
        if stage == 'draw':
            area.tag_redraw()
            if time.monotonic() - stage_start < 3:
                return 0.25
            index += 1
            if index >= min(2, len(entries)):
                finish()
                return None
            stage = 'load'
            return 0.25
    except Exception as exc:
        finish(exc)
        return None


bpy.app.timers.register(verify, first_interval=0.25)
