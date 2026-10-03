"""Native UI import queue and preview worker integration in Blender 4.0.0."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))
temporary = tempfile.TemporaryDirectory(prefix='materials_import_ui_', dir=ROOT / 'dist')
project = Path(temporary.name)
source = project / 'NH_ObjectTextures'
models = project / 'NH_Objects'
source.mkdir()
models.mkdir()
os.environ['LOCALAPPDATA'] = str(project / 'local')
os.environ['NH_MATERIALS_TEXTURE_ROOT'] = str(source)
os.environ['NH_MATERIALS_CACHE_ROOT'] = str(project / 'materials')

import bpy
import addon_utils
import NH_Blender as addon
import NH_bundle
from NH_bundle.io import data_p3d
from NH_Blender import nh_materials as browser, nh_textures as textures


def paa(path, rgb565):
    encoded = struct.pack('<HHI', rgb565, 0, 0) * 16
    path.write_bytes(struct.pack('<HHHH', 0xff01, 0, 16, 16)
                     + len(encoded).to_bytes(3, 'little') + encoded + b'\0' * 6)


def fixture(name, color):
    folder = source / name
    folder.mkdir()
    paa(folder / (name + '_co.paa'), color)
    paa(folder / (name + '_nohq.paa'), 0x841f)
    paa(folder / (name + '_smdi.paa'), 0x19d9)
    rvmat = folder / (name + '.rvmat')
    rvmat.write_text('''PixelShaderID="Super"; specularPower=75; specular[]={.2,.3,.4,1};
class Stage1 {texture="NH_ObjectTextures\\%s\\%s_nohq.paa"; uvSource="tex1";};
class Stage5 {texture="NH_ObjectTextures\\%s\\%s_smdi.paa";};
class Stage6 {texture="#(ai,64,64,1)fresnel(0.4,0.2)";};''' % (name, name, name, name))
    mlod = data_p3d.P3D_MLOD()
    for resolution in (0, 1):
        lod = data_p3d.P3D_LOD()
        lod.resolution = data_p3d.P3D_LOD_Resolution(0, resolution)
        lod.verts = [(0, 0, 0, 0), (1, 0, 0, 0), (0, 1, 0, 0)]
        lod.normals = [(0, 0, 1)]
        lod.faces = [[[0, 1, 2], [0, 0, 0], [(0, 0), (1, 0), (0, 1)],
                      r'NH_ObjectTextures\%s\%s_co.paa' % (name, name),
                      r'NH_ObjectTextures\%s\%s.rvmat' % (name, name), 0]]
        mlod.lods.append(lod)
    model = models / (name + '.p3d')
    mlod.write_file(str(model))
    return model


models_imported = [fixture('alpha', 0xc208), fixture('beta', 0x0700)]
fixture('gamma', 0x001f)  # Available in the library, never requested by import.
assert addon_utils.enable('NH_Blender', default_set=True) is addon
if os.environ.get('NH_ADDON_TEST_ROOT'):
    assert Path(addon.__file__).resolve() == (Path(os.environ['NH_ADDON_TEST_ROOT']) / 'NH_Blender/__init__.py').resolve()
NH_bundle.get_prefs().project_root = str(project)
bpy.context.preferences.addons['NH_Blender'].preferences.preview_engine = 'CYCLES'
original_request, original_selected = browser.request_previews, browser.selected_entry
launches = []


def observe_request(*args, **kwargs):
    previous = browser._worker
    request_at = time.monotonic()
    value = original_request(*args, **kwargs)
    if browser._worker is not None and browser._worker is not previous:
        launches.append(dict(mode=browser._worker_request['mode'],
                             ids=[entry['id'] for entry in browser._worker_request.get('entries', [])],
                             launch_seconds=time.monotonic()-request_at))
    return value


browser.request_previews = observe_request
window = bpy.context.window_manager.windows[0]
area = max(window.screen.areas, key=lambda item: item.width * item.height)
area.type = 'VIEW_3D'
region = next(item for item in area.regions if item.type == 'WINDOW')
cache = Path(browser.cache_root())
result = dict(blender=bpy.app.version_string, passed=False,
              addon_file=str(Path(addon.__file__).resolve()), addon_version=list(addon.bl_info['version']))
state = 'initial'
started = time.monotonic()
last_tick = started
maximum_gap = 0
state_gaps = {}
heartbeat = 0
job_id = None
paused_seen = False
imported_entry = None
ready_before = None
idle_since = None


def pngs():
    return {path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in (cache / 'previews').glob('*.png')}


def import_models():
    for model in models_imported:
        assert bpy.ops.nh.import_p3d(filepath=str(model), load_textures=True,
            absolute_paths=False, proxy_action='NOTHING',
            additional_data={'NORMALS', 'UV', 'MATERIALS'}) == {'FINISHED'}


def finish(error=None):
    browser.request_previews, browser.selected_entry = original_request, original_selected
    result.update(passed=error is None, error=str(error) if error else None,
                  worker_launches=launches, seconds=time.monotonic()-started,
                  heartbeat=heartbeat, maximum_ui_tick_gap_seconds=maximum_gap,
                  maximum_ui_tick_gap_by_state_seconds=state_gaps)
    addon_utils.disable('NH_Blender', default_set=True)
    temporary.cleanup()
    (ROOT / 'dist/materials_import_preview_ui_result.json').write_text(
        json.dumps(result, indent=2), encoding='utf8')
    print(json.dumps(result), flush=True)
    properties = bpy.ops.wm.quit_blender.get_rna_type().properties
    kwargs = {'use_save_prompt': False} if 'use_save_prompt' in properties else {}
    bpy.ops.wm.quit_blender('EXEC_DEFAULT', **kwargs)


def verify():
    global state, last_tick, maximum_gap, heartbeat, job_id, paused_seen
    global imported_entry, ready_before, idle_since
    now = time.monotonic()
    gap = now-last_tick
    maximum_gap = max(maximum_gap, gap)
    state_gaps[state] = max(state_gaps.get(state, 0), gap)
    if gap > 1:
        print('UI tick gap: %.3fs, state %s' % (gap, state), flush=True)
    last_tick = now
    heartbeat += 1
    try:
        if now-started > 55:
            raise RuntimeError('Import preview UI check timed out: ' + state + ' / ' + browser._status)
        if state == 'initial':
            if browser._library_pending:
                return .1
            imported_at = time.monotonic()
            with bpy.context.temp_override(window=window, area=area, region=region):
                import_models()
            result['cold_p3d_import_seconds'] = time.monotonic()-imported_at
            result['native_import_viewport_shading'] = area.spaces.active.shading.type
            # Measure the background worker independently of Blender's first
            # material-preview viewport shader compilation after native import.
            area.spaces.active.shading.type = 'SOLID'
            assert len(browser._import_queue) == 2 and not launches
            imported_entry = dict(next(iter(browser._import_queue.values())))
            pending = dict(browser._import_queue)
            old_worker, old_mode = browser._worker, browser._worker_mode
            class ActiveBuild:
                def poll(self):
                    return None
            browser._worker, browser._worker_mode = ActiveBuild(), 'BUILD'
            try:
                browser._start_import_previews()
                assert browser._import_queue == pending and not launches
            finally:
                browser._worker, browser._worker_mode = old_worker, old_mode
            result['real_p3d_import_queues_unique_pairs'] = True
            result['queue_waits_for_active_build'] = True
            target = next(obj for obj in bpy.data.objects if obj.type == 'MESH'
                          and obj.a3ob_properties_object.is_a3_lod)
            for obj in bpy.context.selected_objects:
                obj.select_set(False)
            target.hide_set(False)
            target.select_set(True)
            bpy.context.view_layer.objects.active = target
            last_tick, maximum_gap, heartbeat = time.monotonic(), 0, 0
            state = 'worker'
            return .05
        if state == 'worker':
            if not launches:
                return .05
            assert launches[0]['mode'] == 'IMPORT' and len(launches[0]['ids']) == 2, launches
            assert browser._import_active() and not browser._import_queue
            assert not browser._browsers(), 'Test unexpectedly opened an NH browser'
            job_id = browser._worker_request['job_id']
            browser.selected_entry = lambda context: imported_entry
            apply_at = time.monotonic()
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.nh_materials.apply() == {'RUNNING_MODAL'}
            result['apply_operator_start_seconds'] = time.monotonic()-apply_at
            result['import_job_started_outside_browser'] = True
            state = 'apply'
            return .05
        if state == 'apply':
            status = browser._read_status()
            if status.get('phase') == 'PAUSED':
                paused_seen = True
            assert browser._worker_request['job_id'] == job_id
            assert browser._worker_mode == 'IMPORT'
            if browser._apply_operator is not None:
                return .05
            assert paused_seen, 'IMPORT did not acknowledge Apply pause'
            assert bpy.context.active_object.active_material.get('nh_material_id') == imported_entry['id']
            browser.selected_entry = original_selected
            result['apply_pauses_and_preserves_import_job'] = True
            state = 'complete'
            return .05
        if state == 'complete':
            if browser._worker.poll() is None:
                return .05
            status = browser._read_status()
            manifest = browser.read_manifest()
            assert browser._worker.poll() == 0, browser._status
            assert status['phase'] == 'DONE' and status['ready'] == status['total'] == 2, status
            assert len(manifest['entries']) == 2 and not manifest['library_indexed']
            ready_before = pngs()
            assert len(ready_before) == 2
            for entry in manifest['entries']:
                recipe = json.loads((cache / 'material_data' / (entry['id'] + '.json')).read_text(encoding='utf8'))
                assert recipe['graph']['nodes'] and entry['preview_signature'] == entry['signature']
            result['preview_worker_finishes_with_spheres_and_shared_graphs'] = True
            with bpy.context.temp_override(window=window, area=area, region=region):
                import_models()
            area.spaces.active.shading.type = 'SOLID'
            assert not browser._import_queue and len(launches) == 1
            assert pngs() == ready_before
            result['reimport_uses_cached_spheres_without_new_worker'] = True
            area.type = 'FILE_BROWSER'
            area.ui_type = 'ASSETS'
            state = 'open'
            return .1
        if state == 'open':
            params = area.spaces.active.params
            if params is None:
                return .1
            if params.asset_library_reference != browser.LIBRARY_NAME:
                params.asset_library_reference = browser.LIBRARY_NAME
                return .1
            if len(launches) < 2 or browser._worker.poll() is None:
                return .1
            assert [item['mode'] for item in launches] == ['IMPORT', 'INDEX'], launches
            status = browser._read_status()
            manifest = browser.read_manifest()
            assert browser._worker.poll() == 0 and status['phase'] == 'DONE', status
            assert manifest['library_indexed'] and len(manifest['entries']) == 3
            assert pngs() == ready_before
            assert not next(entry for entry in manifest['entries'] if entry['name'] == 'gamma')['preview_signature']
            result['opening_partial_import_cache_indexes_library_without_rendering'] = True
            idle_since = now
            state = 'idle'
            return .1
        if state == 'idle':
            assert pngs() == ready_before and len(launches) == 2
            if now-idle_since < 2:
                return .1
            background_gap = max(state_gaps.get('worker', 0), state_gaps.get('complete', 0))
            assert heartbeat > 20 and background_gap < 1.5, state_gaps
            result['maximum_background_import_tick_gap_seconds'] = background_gap
            result['ui_responsive_during_preview_work'] = True
            result['passive_library_view_keeps_ready_spheres'] = True
            finish()
            return None
    except Exception as exc:
        finish(exc)
        return None


bpy.app.timers.register(verify, first_interval=.25)
