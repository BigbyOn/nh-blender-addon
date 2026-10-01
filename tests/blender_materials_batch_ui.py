"""Exercise passive browsing and manual job controls in a disposable UI window."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))
temporary = tempfile.TemporaryDirectory(dir=ROOT / 'dist')
base = Path(temporary.name)
os.environ['LOCALAPPDATA'] = str(base / 'local')
os.environ['NH_MATERIALS_TEXTURE_ROOT'] = str(ROOT / 'dist/material_worker_fixture')
os.environ['NH_MATERIALS_CACHE_ROOT'] = str(base / 'materials')

import bpy
import addon_utils
import NH_Blender as addon
from NH_Blender import nh_materials as browser

assert addon_utils.enable('NH_Blender', default_set=True) is addon
if os.environ.get('NH_ADDON_TEST_ROOT'):
    assert Path(addon.__file__).resolve() == (Path(os.environ['NH_ADDON_TEST_ROOT']) / 'NH_Blender/__init__.py').resolve()
print('NH batch UI addon:', addon.__file__, flush=True)
addon_preferences = bpy.context.preferences.addons['NH_Blender'].preferences
addon_preferences.preview_engine = 'CYCLES'
original_request = browser.request_previews
launches = []


def observe_request(*args, **kwargs):
    previous = browser._worker
    result = original_request(*args, **kwargs)
    if browser._worker is not None and browser._worker is not previous:
        launches.append(browser._worker_request['mode'])
    return result


browser.request_previews = observe_request
window = bpy.context.window_manager.windows[0]
area = max(window.screen.areas, key=lambda item: item.width * item.height)
area.type = 'FILE_BROWSER'
area.ui_type = 'ASSETS'
cube = bpy.context.view_layer.objects.active
cube.data.materials.clear()
cache = Path(browser.cache_root())
state = 'index'
started = time.monotonic()
stage_started = started
job_id = None
paused_seen = False
before_cancel = None
result = dict(blender=bpy.app.version_string, passed=False,
              addon_file=str(Path(addon.__file__).resolve()),
              addon_version=list(addon.bl_info['version']))


def pngs():
    return {path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in (cache / 'previews').glob('*.png')}


def finish(error=None):
    browser.request_previews = original_request
    result.update(passed=error is None, error=str(error) if error else None,
                  worker_launches=launches, seconds=time.monotonic() - started)
    addon_utils.disable('NH_Blender', default_set=True)
    temporary.cleanup()
    (ROOT / 'dist/materials_batch_ui_result.json').write_text(
        json.dumps(result, indent=2), encoding='utf8')
    properties = bpy.ops.wm.quit_blender.get_rna_type().properties
    kwargs = {'use_save_prompt': False} if 'use_save_prompt' in properties else {}
    bpy.ops.wm.quit_blender('EXEC_DEFAULT', **kwargs)


def verify():
    global state, stage_started, job_id, paused_seen, before_cancel
    now = time.monotonic()
    try:
        if now - started > 55:
            raise RuntimeError('Manual batch UI check timed out: ' + state + ' / ' + browser._status)
        if state == 'index':
            if browser._library_pending:
                return .25
            params = area.spaces.active.params
            if params is None:
                return .25
            if params.asset_library_reference != browser.LIBRARY_NAME:
                params.asset_library_reference = browser.LIBRARY_NAME
                return .25
            if not launches or browser._worker.poll() is None:
                return .25
            manifest = browser.read_manifest()
            assert len(manifest['entries']) == 2
            assert launches == ['INDEX'], launches
            assert not pngs(), 'Browsing rendered material thumbnails'
            params.catalog_id = browser.catalog_id('briks')
            params.filter_search = 'br'
            result['metadata_only_open'] = True
            state = 'select'
            return .25
        if state == 'select':
            region = next(item for item in area.regions if item.type == 'WINDOW')
            with bpy.context.temp_override(window=window, area=area, region=region):
                for x in (64, 128, 220, 320):
                    for y in (region.height-64, region.height-128, region.height-220):
                        bpy.ops.file.select(mouse_x=x, mouse_y=y)
                        entry = browser.selected_entry(bpy.context)
                        if entry:
                            result['native_card_selected'] = entry['name']
                            state = 'idle'
                            stage_started = now
                            return .25
            return .25
        if state == 'idle':
            assert launches == ['INDEX'] and not pngs(), 'Folder/search/selection launched preview rendering'
            if now - stage_started < 3:
                return .25
            result['passive_navigation_no_render'] = True
            region = next(item for item in area.regions if item.type == 'WINDOW')
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.nh_materials.calculate_previews() == {'FINISHED'}
                job_id = browser._batch_id
                assert browser._worker_request['mode'] == 'BUILD' and job_id
                assert bpy.ops.nh_materials.apply() == {'RUNNING_MODAL'}
            result['manual_button_started_build'] = True
            state = 'apply'
            return .25
        if state == 'apply':
            if browser._batch_status.get('phase') == 'PAUSED':
                paused_seen = True
            assert browser._batch_id == job_id, 'Apply discarded the explicit build job'
            if browser._apply_operator is not None:
                return .25
            assert cube.active_material and cube.active_material.get('nh_super_rvmat'), browser._status
            assert paused_seen, 'Build did not acknowledge the Apply pause'
            result['apply_pauses_and_preserves_build'] = True
            area.type = 'VIEW_3D'
            state = 'build'
            return .25
        if state == 'build':
            if browser._batch_active() or browser._batch_status.get('phase') != 'DONE':
                return .25
            status = browser._batch_status
            assert status['ready'] == status['processed'] == status['total'] == 2
            assert status['progress'] == 1 and bpy.context.window_manager.nh_materials_progress == 100
            assert len(pngs()) == 2
            assert launches == ['INDEX', 'BUILD'], launches
            result['build_continues_outside_browser'] = True
            result['visible_progress_reaches_100'] = True
            before_cancel = pngs()
            region = next(item for item in area.regions if item.type == 'WINDOW')
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.nh_materials.calculate_previews() == {'FINISHED'}
                assert bpy.ops.nh_materials.cancel_calculation() == {'FINISHED'}
            state = 'cancel'
            return .25
        if state == 'cancel':
            if browser._batch_active() or browser._batch_status.get('phase') != 'CANCELLED':
                return .25
            assert pngs() == before_cancel, 'Cancel rewrote completed previews'
            assert not browser._progress_started, 'Native progress remained active after Cancel'
            result['cancel_button_preserves_ready_previews'] = True
            result['cancel_progress_cleanup'] = True
            finish()
            return None
    except Exception as exc:
        finish(exc)
        return None


bpy.app.timers.register(verify, first_interval=.25)
