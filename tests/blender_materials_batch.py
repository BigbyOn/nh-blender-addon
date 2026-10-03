"""Manual preview jobs: finite metadata-only browsing, progress and cancellation.

Run in a disposable background Blender 4.0.0 process. Only temporary source
copies and caches under dist are changed; source project textures stay intact.
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

checks = []
history = []
active_cache = None
cancel_after_first = False
original_atomic = browser.atomic_json
original_studio = worker.studio


def check(name, condition):
    assert condition, name
    checks.append(name)
    print('PASS:', name, flush=True)


def capture_atomic(path, value):
    global cancel_after_first
    original_atomic(path, value)
    if Path(path).name != 'status.json':
        return
    history.append(dict(value))
    if (cancel_after_first and value.get('phase') == 'RENDER'
            and value.get('ready', 0) >= 1):
        cancel_after_first = False
        request = json.loads((active_cache / 'request.json').read_text(encoding='utf8'))
        request['cancelled'] = True
        original_atomic(active_cache / 'request.json', request)


def disallow_studio(*args, **kwargs):
    raise AssertionError('Metadata/cached job attempted to create a render studio')


def run(cache, source, *, mode=None, no_render=False, cancelled=False):
    global active_cache
    cache.mkdir(parents=True, exist_ok=True)
    active_cache = cache
    history.clear()
    request = dict(root=str(source), cache=str(cache), job_id=os.urandom(8).hex(),
                   generation=time.time(), engine='CYCLES', folder='not-a-folder',
                   selected='', search='does-not-match', cancelled=cancelled)
    if mode is not None:
        request['mode'] = mode
    original_atomic(cache / 'request.json', request)
    worker.studio = disallow_studio if no_render else original_studio
    try:
        worker.main(str(cache), once=True)
    finally:
        worker.studio = original_studio
    status = json.loads((cache / 'status.json').read_text(encoding='utf8'))
    manifest = json.loads((cache / 'manifest.json').read_text(encoding='utf8'))
    check('Job status retains request identity: ' + status['job_id'],
          status['job_id'] == request['job_id'])
    for item in history:
        assert 0 <= item['progress'] <= 1, item
        assert 0 <= item['ready'] + item['failed'] <= item['total'], item
        assert item['processed'] == item['ready'] + item['failed'], item
    if status.get('mode') == 'BUILD':
        progress = [item['progress'] for item in history if item['total'] > 0]
        assert all(after >= before for before, after in zip(progress, progress[1:])), history
    return status, manifest


def ready_pngs(cache):
    return {path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in (cache / 'previews').glob('*.png')}


browser.atomic_json = capture_atomic
try:
    with tempfile.TemporaryDirectory(dir=ROOT / 'dist') as temporary:
        base = Path(temporary)
        source = base / 'source'
        shutil.copytree(ROOT / 'dist/material_worker_fixture', source)
        source_hashes = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in source.rglob('*') if path.is_file()}
        cache = base / 'complete'
        status, manifest = run(cache, source, no_render=True)
        check('Default INDEX finishes without a render studio',
              status['mode'] == 'INDEX' and status['phase'] == 'DONE' and not status['busy'])
        check('INDEX creates both catalogs but no material thumbnails',
              len(manifest['entries']) == 2 and len(list((cache / 'assets').glob('*.blend'))) == 2
              and not ready_pngs(cache))
        check('Pending metadata thumbnail needs no renderer', (cache / 'pending.png').is_file())

        status, manifest = run(cache, source, mode='BUILD')
        check('Explicit BUILD ignores browser folder and search filters',
              status['phase'] == 'DONE' and status['total'] == status['ready'] == 2)
        check('Completed progress reaches 100 percent',
              status['processed'] == 2 and status['failed'] == 0 and status['progress'] == 1)
        built = ready_pngs(cache)
        check('Both real material sphere previews were saved', len(built) == 2)

        status, manifest = run(cache, source, mode='BUILD', no_render=True)
        check('Cached BUILD skips all material renders',
              status['phase'] == 'DONE' and status['ready'] == 2 and ready_pngs(cache) == built)

        interrupted = base / 'cancelled'
        run(interrupted, source, no_render=True)
        cancel_after_first = True
        status, manifest = run(interrupted, source, mode='BUILD')
        first = ready_pngs(interrupted)
        check('Cancel is honored between materials',
              status['phase'] == 'CANCELLED' and status['ready'] == 1 and len(first) == 1)
        status, manifest = run(interrupted, source, mode='BUILD')
        resumed = ready_pngs(interrupted)
        check('A new BUILD resumes from completed cached previews',
              status['phase'] == 'DONE' and status['ready'] == 2
              and all(resumed[name] == previous for name, previous in first.items()))

        errors = source / 'errors'
        errors.mkdir()
        (errors / 'bad.rvmat').write_text(
            'PixelShaderID="Super"; class Stage1 {texture="missing-normal.paa";};',
            encoding='utf8')
        shutil.copy2(source / 'bar100/nonstop_co.paa', errors / 'bad_co.paa')
        status, manifest = run(cache, source, mode='BUILD')
        failed = next(entry for entry in manifest['entries'] if entry['name'] == 'bad')
        error = failed.get('error')
        check('A failed material does not stall the remaining job',
              status['phase'] == 'DONE' and status['total'] == status['processed'] == 3
              and status['ready'] == 2 and status['failed'] == 1 and status['progress'] == 1
              and bool(error))
        good = ready_pngs(cache)
        status, manifest = run(cache, source, mode='BUILD', no_render=True, cancelled=True)
        retained = next(entry for entry in manifest['entries'] if entry['name'] == 'bad')
        check('Cancel preserves earlier thumbnails and material errors',
              status['phase'] == 'CANCELLED' and retained.get('error') == error
              and ready_pngs(cache) == good)

        status, manifest = run(cache, source, mode='BUILD')
        check('Retrying a failed material still terminates with correct counts',
              status['phase'] == 'DONE' and status['failed'] == 1 and status['processed'] == 3)
        check('Build jobs leave original source files unchanged',
              all(hashlib.sha256((source / path).read_bytes()).hexdigest() == digest
                  for path, digest in source_hashes.items()))
finally:
    browser.atomic_json = original_atomic
    worker.studio = original_studio

result = dict(blender=bpy.app.version_string, passed=True, checks=len(checks), names=checks)
(ROOT / 'dist/materials_batch_result.json').write_text(json.dumps(result, indent=2), encoding='utf8')
print(json.dumps(result), flush=True)
