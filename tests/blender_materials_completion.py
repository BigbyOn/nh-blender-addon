"""Renderer completion, full failure diagnostics and idle progress regression."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))
os.environ['NH_MATERIALS_TEXTURE_ROOT'] = str(ROOT / 'dist/material_worker_fixture')
os.environ['NH_MATERIALS_CACHE_ROOT'] = str(ROOT / 'dist/materials_completion_cache')
import bpy
import addon_utils
import NH_Blender as addon
from NH_Blender import nh_materials as browser

checks = []

def check(name, condition):
    assert condition, name
    checks.append(name)
    print('PASS:', name, flush=True)

class Process:
    code = None
    def poll(self):
        return self.code
    def terminate(self):
        self.code = -1
    def wait(self, timeout=None):
        return self.code

assert addon_utils.enable('NH_Blender', default_set=True) is addon
browser.timer()
failure = dict(id='failed-id', name='Missing normal material',
               rvmat='NH_ObjectTextures/example/material.rvmat',
               color='NH_ObjectTextures/example/material_co.paa',
               error='Cannot load Super textures: NH_ObjectTextures/example/material_nohq.paa')
wm = bpy.context.window_manager
for phase in ('DONE', 'CANCELLED', 'ERROR'):
    browser._worker = Process()
    browser._worker_mode = 'BUILD'
    browser._batch_id = 'completion-' + phase
    browser._worker_request = dict(job_id=browser._batch_id)
    browser._progress_started = True
    wm.progress_begin(0, 100)
    status = dict(job_id=browser._batch_id, phase=phase, total=3, ready=2, failed=1,
                  processed=3, progress=1.0, message=phase, failures=[failure])
    browser._update_batch_progress(status)
    check(phase + ' keeps progress active while renderer is alive',
          browser._progress_started and browser._batch_active()
          and browser._batch_status['phase'] == 'FINALIZING')
    check(phase + ' shows renderer finalization instead of completion',
          'renderer' in browser._status and 'finishing' in browser.calculation_text())
    check(phase + ' keeps worker status data unchanged', status['phase'] == phase)
    browser._worker.code = 1 if phase == 'ERROR' else 0
    browser._update_batch_progress(status)
    check(phase + ' ends progress only after process exit',
          not browser._progress_started and not browser._batch_active()
          and browser._batch_status['phase'] == phase)
    check(phase + ' retains complete failure source details',
          browser.calculation_failures() == [failure])
    browser.stop_worker()

browser._worker = Process()
browser._worker.code = -1073741819
browser._batch_id = 'crashed-worker'
browser._batch_status = dict(phase='RENDER', progress=.5)
browser._progress_started = True
wm.progress_begin(0, 100)
browser._update_batch_progress({})
check('Unexpected exit includes exit code and worker log',
      browser._batch_status['phase'] == 'ERROR'
      and '-1073741819' in browser.calculation_failures()[0]['error']
      and 'worker.log' in browser.calculation_failures()[0]['error'])
browser.stop_worker()

# Reproduce the user's WinError 5 with a real open Windows JSON reader.
# Temporary paths are scoped to this test; user caches remain untouched.
with tempfile.TemporaryDirectory(dir=ROOT / 'dist') as directory:
    target = Path(directory) / 'manifest.json'
    browser.atomic_json(target, dict(old=True))
    opened = threading.Event()
    def reader():
        with target.open(encoding='utf8') as file:
            opened.set()
            time.sleep(.09)
            json.load(file)
    thread = threading.Thread(target=reader)
    thread.start()
    assert opened.wait(1)
    browser.atomic_json(target, dict(new=True))
    thread.join()
    check('Open Windows reader cannot terminate JSON publishing',
          json.loads(target.read_text()) == dict(new=True))
    check('Retried JSON publishing cleans temporary files', not list(Path(directory).glob('*.tmp')))

    original_replace = browser.os.replace
    def denied(*args):
        raise PermissionError('Permanent access denied')
    browser.os.replace = denied
    try:
        try:
            browser.atomic_json(target, dict(corrupt=True))
        except PermissionError:
            pass
        else:
            raise AssertionError('Permanent write failure silently ignored')
    finally:
        browser.os.replace = original_replace
    check('Permanent access failure preserves earlier JSON and cleans temps',
          json.loads(target.read_text()) == dict(new=True)
          and not list(Path(directory).glob('*.tmp')))

# Draw the registered error dialog through a recording layout. Long source
# paths must remain available in full instead of the former 60-char truncation.
labels = []
class Layout:
    def prop(self, *args, **kwargs):
        pass
    def label(self, *, text, **kwargs):
        labels.append(text)

browser.NH_MATERIALS_OT_Errors.draw(
    SimpleNamespace(layout=Layout(), index=1, _errors=[failure]), bpy.context)
check('Error dialog includes every complete source and reason',
      all(value in ' '.join(labels) for value in
          (failure['name'], failure['rvmat'], failure['color'], failure['error'])))
browser._batch_status = dict(failures=[failure], failed=1, ready=2, total=3,
                             processed=3, progress=1)
check('Failure summary distinguishes ready from processed',
      'ready: 2; failed: 1' in browser.calculation_text())

real_read = browser._read_status
browser._read_status = lambda: (_ for _ in ()).throw(AssertionError('Idle status read'))
try:
    check('Completed calculation stops polling status off browser', browser.timer() == 2.0)
finally:
    browser._read_status = real_read
addon_utils.disable('NH_Blender', default_set=True)
result = dict(blender=bpy.app.version_string, addon_file=addon.__file__,
              version=addon.bl_info['version'], passed=True, checks=len(checks), names=checks)
(ROOT / 'dist/materials_completion_result.json').write_text(json.dumps(result, indent=2), encoding='utf8')
print(json.dumps(result), flush=True)
