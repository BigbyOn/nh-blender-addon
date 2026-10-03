"""Finite P3D preview jobs: explicit pairs, shared cache and source invalidation.

Run with a disposable background Blender 4.0.0 process. Fixtures, texture
caches and output files stay under dist; project DayZ sources are read only.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))

import bpy
from NH_Blender import nh_materials as browser, nh_materials_worker as worker
from NH_Blender import nh_material_cache as material_cache, nh_textures
from NH_Blender.utilities import rvmat

checks = []
original_scan, original_studio = rvmat.scan, worker.studio
original_full_load = nh_textures._load_material_preview_image
original_atomic = browser.atomic_json
history = []
cancel_failed_cache = None


def capture_atomic(path, value):
    global cancel_failed_cache
    original_atomic(path, value)
    if Path(path).name != 'status.json':
        return
    history.append(dict(value))
    if cancel_failed_cache and value.get('phase') == 'RENDER' and value.get('failed'):
        request_path = cancel_failed_cache / 'request.json'
        request = json.loads(request_path.read_text(encoding='utf8'))
        request['cancelled'] = True
        original_atomic(request_path, request)
        cancel_failed_cache = None


def check(name, condition):
    assert condition, name
    checks.append(name)
    print('PASS:', name, flush=True)


def disallow_studio(*args, **kwargs):
    raise AssertionError('Cached/metadata import job attempted to render')


def disallow_scan(*args, **kwargs):
    raise AssertionError('Import preview job scanned the complete library')


def disallow_full_load(*args, **kwargs):
    raise AssertionError('Preview job decoded a full texture instead of its preview mip')


def run(cache, source, *, mode='IMPORT', entries=(), no_render=False, refresh=False,
        cancelled=False, paused=False):
    history.clear()
    cache.mkdir(parents=True, exist_ok=True)
    request = dict(root=str(source), cache=str(cache), job_id=os.urandom(8).hex(),
                   mode=mode, generation=time.time(), engine='CYCLES',
                   folder='', selected='', search='', entries=list(entries),
                   refresh=refresh, cancelled=cancelled, paused=paused,
                   pause_generation=17 if paused else None)
    browser.atomic_json(cache / 'request.json', request)
    rvmat.scan = disallow_scan if mode == 'IMPORT' else original_scan
    worker.studio = disallow_studio if no_render else original_studio
    nh_textures._load_material_preview_image = disallow_full_load
    try:
        worker.main(str(cache), once=True)
    finally:
        rvmat.scan, worker.studio = original_scan, original_studio
        nh_textures._load_material_preview_image = original_full_load
    status = json.loads((cache / 'status.json').read_text(encoding='utf8'))
    manifest = json.loads((cache / 'manifest.json').read_text(encoding='utf8'))
    assert status['job_id'] == request['job_id'], status
    assert status['processed'] == status['ready'] + status['failed'], status
    return status, manifest


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def entry_named(manifest, name):
    return next(entry for entry in manifest['entries'] if entry['name'] == name)


try:
    browser.atomic_json = capture_atomic
    with tempfile.TemporaryDirectory(dir=ROOT / 'dist') as temporary:
        base = Path(temporary)
        os.environ['LOCALAPPDATA'] = str(base / 'localappdata')
        source = base / 'source'
        shutil.copytree(ROOT / 'dist/material_worker_fixture', source)
        cache = base / 'library'
        status, manifest = run(cache, source, mode='INDEX', no_render=True)
        requested = dict(entry_named(manifest, 'nonstop'), root=str(source), imported=True)
        unrelated = dict(entry_named(manifest, 'briks_big_01'))
        unrelated_card = cache / 'assets' / unrelated['asset_file']
        unrelated_before = fingerprint(unrelated_card)
        unrelated_png = cache / 'previews' / (unrelated['id'] + '.png')

        status, manifest = run(cache, source, entries=[requested])
        rendered = entry_named(manifest, 'nonstop')
        png = cache / 'previews' / (requested['id'] + '.png')
        check('IMPORT renders only its explicit pair without scanning the library',
              status['mode'] == 'IMPORT' and status['phase'] == 'DONE'
              and status['total'] == status['ready'] == status['processed'] == 1
              and status['progress'] == 1 and png.is_file() and not unrelated_png.exists())
        check('An unrelated pending card and its group remain unchanged',
              len(manifest['entries']) == 2
              and entry_named(manifest, 'briks_big_01') == unrelated
              and fingerprint(unrelated_card) == unrelated_before)
        check('The imported entry retains original PAA and RVMAT source paths',
              os.path.normcase(os.path.realpath(rendered['color']))
              == os.path.normcase(os.path.realpath(requested['color']))
              and os.path.normcase(os.path.realpath(rendered['rvmat']))
              == os.path.normcase(os.path.realpath(requested['rvmat']))
              and rendered['color'].lower().endswith('.paa')
              and rendered['rvmat'].lower().endswith('.rvmat') and rendered['imported'])
        recipe_path = Path(browser.cache_root()) / 'material_data' / (rendered['id'] + '.json')
        recipe = json.loads(recipe_path.read_text(encoding='utf8'))
        image_sources = [node['image']['source'] for node in recipe['graph']['nodes'] if 'image' in node]
        check('Import preview calculation also caches the Super graph with original texture references',
              len(recipe['graph']['nodes']) > 20 and image_sources
              and all(path.lower().endswith('.paa') and os.path.isfile(path) for path in image_sources)
              and os.path.normcase(rendered['color']) in image_sources
              and not any('.png' in path.lower() for path in image_sources))
        first = fingerprint(png)
        status, manifest = run(cache, source, entries=[requested], no_render=True)
        check('A repeated IMPORT uses its cached sphere without creating a studio',
              status['ready'] == 1 and fingerprint(png) == first)

        with open(requested['rvmat'], 'a', encoding='utf8') as file:
            file.write('\n// changed imported material fingerprint\n')
        status, manifest = run(cache, source, entries=[requested])
        changed = fingerprint(png)
        check('A changed source invalidates only the requested preview',
              status['ready'] == 1 and changed[1] != first[1]
              and fingerprint(unrelated_card) == unrelated_before
              and entry_named(manifest, 'nonstop')['signature'] != requested['signature'])
        png.unlink()
        status, manifest = run(cache, source, entries=[requested])
        check('A missing sphere PNG is recalculated despite a stored ready signature',
              png.is_file() and status['ready'] == 1)

        outside = base / 'outside'
        shutil.copytree(source / 'bar100', outside)
        extra_root = base / 'import-roots'
        shared_normal = extra_root / 'import_shared' / 'normal.paa'
        shared_normal.parent.mkdir(parents=True)
        normal_source = rvmat.stage(rvmat.read(outside / 'nonstop.rvmat'), 1)['texture']
        shutil.copy2(normal_source, shared_normal)
        outside_rvmat = outside / 'nonstop.rvmat'
        outside_rvmat.write_text(outside_rvmat.read_text(encoding='utf8').replace(
            normal_source, 'import_shared\\normal.paa'), encoding='utf8')
        external = dict(id=rvmat.identity(str(outside / 'nonstop.rvmat'), str(outside / 'nonstop_co.paa')),
                        name='External pair', folder='Imported/Outside', root=str(source),
                        rvmat=str(outside / 'nonstop.rvmat'), color=str(outside / 'nonstop_co.paa'),
                        signature='intentionally-old', imported=True, search_roots=[str(extra_root)])
        status, manifest = run(cache, source, entries=[external])
        external_entry = entry_named(manifest, 'External pair')
        external_png = cache / 'previews' / (external_entry['id'] + '.png')
        external_card = cache / 'assets' / external_entry['asset_file']
        external_ready = fingerprint(external_png)
        check('A Super pair outside the configured NH root receives a library card and sphere',
              status['ready'] == 1 and len(manifest['entries']) == 3
              and external_png.is_file() and external_card.is_file()
              and external_entry['signature'] != 'intentionally-old'
              and 'Imported/Outside' in (cache / 'blender_assets.cats.txt').read_text(encoding='utf8'))
        status, manifest = run(cache, source, mode='INDEX', refresh=True, no_render=True)
        check('Refreshing the main library preserves a valid external imported pair and sphere',
              manifest['library_indexed'] and len(manifest['entries']) == 3
              and entry_named(manifest, 'External pair')['preview_signature']
              == external_entry['preview_signature']
              and fingerprint(external_png) == external_ready)

        normal_stat = shared_normal.stat()
        os.utime(shared_normal, ns=(normal_stat.st_atime_ns, normal_stat.st_mtime_ns + 1_000_000_000))
        status, manifest = run(cache, source, mode='INDEX', refresh=True, no_render=True)
        check('Refreshing fingerprints stage textures resolved through import search roots',
              entry_named(manifest, 'External pair')['signature'] != external_entry['signature']
              and not entry_named(manifest, 'External pair')['preview_signature']
              and fingerprint(external_png) == external_ready)
        status, manifest = run(cache, source, entries=[external], paused=True, no_render=True)
        check('IMPORT supports the same pause acknowledgement as explicit BUILD',
              status['phase'] == 'PAUSED' and status['paused'] and status['pause_generation'] == 17)
        status, manifest = run(cache, source, entries=[external], cancelled=True, no_render=True)
        check('A cancelled IMPORT retains already calculated unrelated previews',
              status['phase'] == 'CANCELLED' and png.is_file() and external_png.is_file())
        status, manifest = run(cache, source, entries=[external])
        check('A resumed import calculates the stale external preview',
              status['phase'] == 'DONE' and status['ready'] == 1
              and fingerprint(external_png)[1] != external_ready[1])

        status, manifest = run(cache, source, mode='BUILD')
        check('Full-library BUILD includes imported external entries and reuses completed spheres',
              status['total'] == status['ready'] == 3
              and entry_named(manifest, 'External pair')['imported'])
        external_png_before = fingerprint(external_png)
        status, manifest = run(cache, source, mode='BUILD', no_render=True)
        check('Repeated BUILD also reuses the external imported sphere',
              status['ready'] == 3 and fingerprint(external_png) == external_png_before)

        broken_rvmat = base / 'broken.rvmat'
        broken_rvmat.write_text('PixelShaderID="Super"; class Stage1 {texture="missing.paa";};',
                               encoding='utf8')
        broken = dict(id=rvmat.identity(str(broken_rvmat), external['color']),
                      name='Broken pair', folder='Imported/Errors', root=str(source),
                      rvmat=str(broken_rvmat), color=external['color'], imported=True)
        status, manifest = run(cache, source, entries=[external, broken])
        bad = entry_named(manifest, 'Broken pair')
        check('IMPORT reports each failed pair with its exact source paths and reason',
              status['phase'] == 'DONE' and status['total'] == status['processed'] == 2
              and status['ready'] == 1 and status['failed'] == 1 and status['progress'] == 1
              and status['failures'] == [dict(id=bad['id'], name=bad['name'], rvmat=bad['rvmat'],
                                             color=bad['color'], error=bad['error'])]
              and 'missing.paa' in bad['error'])
        check('A failed material status includes its reason instead of reporting it as prepared',
              any(item['message'].startswith('Failed: Broken pair')
                  and bad['error'] in item['message'] for item in history))
        cancel_failed_cache = cache
        status, manifest = run(cache, source, entries=[broken])
        check('Cancellation retains failure details accumulated during the import job',
              status['phase'] == 'CANCELLED' and status['failed'] == 1
              and status['failures'][0]['error'] == bad['error'])
        broken_rvmat.unlink()
        Path(external['rvmat']).unlink()
        status, manifest = run(cache, source, mode='INDEX', refresh=True, no_render=True)
        check('Refresh removes an external imported pair whose source has been deleted',
              len(manifest['entries']) == 2
              and all(entry['id'] != external_entry['id'] for entry in manifest['entries']))

        subset = base / 'subset'
        status, manifest = run(subset, source, entries=[requested])
        check('First IMPORT marks its manifest as a partial library index',
              not manifest['library_indexed'] and len(manifest['entries']) == 1)
        subset_png = subset / 'previews' / (requested['id'] + '.png')
        subset_preview = fingerprint(subset_png)
        status, manifest = run(subset, source, mode='INDEX', no_render=True)
        check('Opening a partial IMPORT cache subsequently indexes the complete library',
              manifest['library_indexed'] and len(manifest['entries']) == 2
              and fingerprint(subset_png) == subset_preview)

        foreign_root = Path(r'Q:\missing NH folder')
        cross_drive = base / 'cross-drive'
        status, manifest = run(cross_drive, foreign_root, entries=[requested])
        cross_entry = manifest['entries'][0]
        check('IMPORT accepts real sources on another drive when the configured NH root is unavailable',
              status['phase'] == 'DONE' and status['ready'] == status['total'] == 1
              and not manifest['library_indexed'] and manifest['root'] == str(foreign_root)
              and (cross_drive / 'assets' / cross_entry['asset_file']).is_file()
              and (cross_drive / 'previews' / (cross_entry['id'] + '.png')).is_file())
finally:
    rvmat.scan, worker.studio = original_scan, original_studio
    nh_textures._load_material_preview_image = original_full_load
    browser.atomic_json = original_atomic

result = dict(blender=bpy.app.version_string, passed=True, checks=len(checks), names=checks)
(ROOT / 'dist/materials_import_preview_result.json').write_text(json.dumps(result, indent=2), encoding='utf8')
print(json.dumps(result), flush=True)
