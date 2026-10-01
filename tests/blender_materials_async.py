"""Cold-cache Apply remains responsive, cancels safely, and preserves originals."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))
os.environ['NH_MATERIALS_TEXTURE_ROOT'] = str(ROOT/'dist/material_worker_fixture')
os.environ['NH_MATERIALS_CACHE_ROOT'] = str(ROOT/'dist/materials_async_test_cache')
import bpy
import addon_utils
import NH_Blender as addon
from NH_Blender import nh_materials as browser, nh_textures as textures
temporary = tempfile.TemporaryDirectory(dir=ROOT/'dist')
texture_cache = Path(temporary.name)/'originals'
def cache_root(create=False):
    if create:
        texture_cache.mkdir(parents=True, exist_ok=True)
    return str(texture_cache)
textures._nh_texture_cache_root = cache_root
entry = next(e for e in json.loads((ROOT/'dist/materials_fast_worker_fixture/manifest.json').read_text())['entries']
             if e['name'] == 'briks_big_01')
browser.selected_entry = lambda context, **kwargs: entry
assert addon_utils.enable('NH_Blender', default_set=True) is addon
# enable may reload already imported source modules.
browser.selected_entry = lambda context, **kwargs: entry
textures._nh_texture_cache_root = cache_root
window = bpy.context.window_manager.windows[0]
area = max(window.screen.areas, key=lambda item:item.width*item.height)
area.type = 'VIEW_3D'
region = next(r for r in area.regions if r.type == 'WINDOW')
cube = bpy.context.view_layer.objects.active
cube.data.materials.clear()
state = 'cancel'
started = time.monotonic()
last_tick = started
heartbeat = 0
maximum_gap = 0
result = dict(blender=bpy.app.version_string, passed=False)

def finish(error=None):
    result.update(passed=error is None, error=str(error) if error else None,
                  heartbeats=heartbeat, max_tick_gap=maximum_gap)
    addon_utils.disable('NH_Blender', default_set=True)
    temporary.cleanup()
    (ROOT/'dist/materials_async_result.json').write_text(json.dumps(result,indent=2), encoding='utf8')
    properties = bpy.ops.wm.quit_blender.get_rna_type().properties
    kwargs = {'use_save_prompt': False} if 'use_save_prompt' in properties else {}
    bpy.ops.wm.quit_blender('EXEC_DEFAULT', **kwargs)

def verify():
    global state, started, heartbeat, maximum_gap, last_tick
    now = time.monotonic()
    maximum_gap = max(maximum_gap, now-last_tick)
    last_tick = now
    try:
        with bpy.context.temp_override(window=window,area=area,region=region):
            if state == 'cancel':
                before = time.monotonic()
                assert bpy.ops.nh_materials.apply() == {'RUNNING_MODAL'}
                result['cold_start_seconds'] = time.monotonic()-before
                process = browser._prepare_job['process']
                browser._apply_operator.cancel(bpy.context)
                assert process.poll() is not None and browser._prepare_job is None and not browser._material_busy
                assert not cube.active_material
                result['cancel_cleanup'] = True
                state = 'selection'
                return 0.25
            if state == 'selection':
                assert bpy.ops.nh_materials.apply() == {'RUNNING_MODAL'}
                cube.select_set(False)
                state = 'changed'
                return 0.5
            if state == 'changed':
                assert browser._apply_operator is None and browser._prepare_job is None
                assert not cube.active_material
                result['changed_selection_cancelled'] = True
                cube.select_set(True)
                assert bpy.ops.nh_materials.apply() == {'RUNNING_MODAL'}
                started = time.monotonic()
                last_tick = started
                maximum_gap = 0
                state = 'waiting'
                return 0.05
            if state == 'waiting':
                heartbeat += 1
                if browser._apply_operator is not None:
                    assert not cube.active_material, 'Scene mutated before cache preparation completed'
                    if now-started > 45:
                        raise RuntimeError('Cold preparation timed out: '+browser._status)
                    return 0.05
                material = cube.active_material
                assert material and material.get('nh_material_id') == entry['id'], browser._status
                normal = next(n.image for n in material.node_tree.nodes if n.type=='TEX_IMAGE' and n.name.startswith('Stage1 '))
                assert max(normal.size) == 2048, normal.size[:]
                assert heartbeat >= 5 and result['cold_start_seconds'] < 2.0
                result.update(full_normal_size=list(normal.size), cold_apply_seconds=now-started,
                              async_apply=True, responsive_during_decode=True)
                finish()
                return None
    except Exception as exc:
        finish(exc)
        return None

bpy.app.timers.register(verify, first_interval=1.0)
