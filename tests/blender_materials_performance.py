"""Regression checks for lightweight catalogs and bounded idle UI work."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))
os.environ['NH_MATERIALS_CACHE_ROOT'] = str(ROOT/'dist/materials_performance_test_cache')
os.environ['NH_MATERIALS_TEXTURE_ROOT'] = str(ROOT/'dist/material_worker_fixture')
import bpy
import addon_utils
import NH_Blender as addon
from NH_Blender import nh_materials as browser, nh_materials_worker as worker

checks = []
def check(name, condition):
    assert condition, name
    checks.append(name)
    print('PASS:', name, flush=True)

assert addon_utils.enable('NH_Blender', default_set=True) is addon
browser.timer()
entries = json.loads((ROOT/'dist/materials_fast_worker_fixture/manifest.json').read_text(encoding='utf8'))['entries']
cache = Path(browser.cache_root())
(cache/'assets').mkdir(exist_ok=True)
browser.atomic_json(cache/'manifest.json', dict(entries=entries))
browser.read_manifest()
context = SimpleNamespace(asset=SimpleNamespace(local_id=None, name=entries[0]['asset_name'],
    full_library_path=str(cache/'assets'/entries[0]['asset_file'])+'/Material/'+entries[0]['asset_name']))
check('Remote grouped asset selection retains ID', browser.selected_entry(context)['id'] == entries[0]['id'])
catalog_calls = []
real_catalog = browser.catalog_id
browser.catalog_id = lambda folder: catalog_calls.append(folder) or real_catalog(folder)
browser.atomic_json(cache/'manifest.json', dict(entries=[dict(e, error='changed preview status') for e in entries]))
browser.read_manifest()
check('Preview status updates reuse catalog UUIDs', not catalog_calls)
browser.catalog_id = real_catalog

real_manifest = browser.read_manifest
browser.read_manifest = lambda: (_ for _ in ()).throw(AssertionError('Idle manifest read'))
check('Idle timer skips library JSON', browser.timer() == 2.0)
browser.read_manifest = real_manifest
stub = bpy.data.materials.new('Unused metadata')
stub.use_fake_user = True
stub['nh_material_stub'] = True
check('Fake user does not trigger image decode', not browser._build_one_stub(10.0) and stub.get('nh_material_stub'))
bpy.data.materials.remove(stub)

many = [dict(entries[0], id='%024d' % i, name='Material %d' % i, folder='one/two') for i in range(145)]
many[0]['name'] = 'Длинное имя ' * 15
groups = worker.asset_groups(many)
check('Folder bundles bounded to 64 cards', len(groups) == 3 and max(map(len, groups.values())) <= 64)
check('Native material names fit Blender limit', max(len(e['asset_name'].encode('utf8')) for e in many) <= 63)
worker.write_asset_group(str(cache), entries, 'metadata_test.blend')
before = set(image.as_pointer() for image in bpy.data.images)
with bpy.data.libraries.load(str(cache/'assets/metadata_test.blend'), link=False) as (data, loaded):
    loaded.materials = data.materials
check('Native append loads no texture dependencies', before == set(image.as_pointer() for image in bpy.data.images))
check('Catalog carries metadata without Super render graph', all(m.get('nh_material_stub') and
      (m.node_tree is None or len(m.node_tree.nodes) <= 2) for m in loaded.materials))
for material in loaded.materials:
    bpy.data.materials.remove(material)

# Disabling during an automatic index must not permanently mark an incomplete
# cache as checked. Simulate a still-running child without starting GPU work.
spawns = []
real_popen = browser.subprocess.Popen
class PendingProcess:
    def __init__(self):
        self.code = None
    def poll(self):
        return self.code
    def terminate(self):
        self.code = -1
    def wait(self, timeout=None):
        return self.code

def fake_popen(*args, **kwargs):
    process = PendingProcess()
    spawns.append(process)
    return process

browser.subprocess.Popen = fake_popen
try:
    browser.request_previews()
    check('Automatic index starts once for incomplete cache', len(spawns) == 1)
    addon_utils.disable('NH_Blender', default_set=True)
    check('Disable terminates interrupted index', spawns[0].poll() is not None)
    check('Disable clears checked root for incomplete index', browser._index_checked_root is None)
    check('Re-enable succeeds after interrupted index', addon_utils.enable('NH_Blender', default_set=True) is addon)
    browser.request_previews()
    check('Re-enable retries interrupted metadata index', len(spawns) == 2)

    browser.stop_worker()
    browser.request_previews(refresh=True, mode='BUILD')
    browser._batch_status = dict(job_id=browser._batch_id, phase='RENDER', total=100,
                                processed=25, progress=.25, message='Preparing material')
    bpy.context.window_manager.nh_materials_progress = 25
    addon_utils.disable('NH_Blender', default_set=True)
    check('Disable clears active calculation history', not browser._batch_status)
    check('Disable ends calculation progress', not browser._progress_started)
    check('Re-enable succeeds after interrupted calculation', addon_utils.enable('NH_Blender', default_set=True) is addon)
    check('Re-enabled UI has no stale calculation status', not browser._batch_status and browser._batch_id is None)
finally:
    browser.stop_worker()
    browser.subprocess.Popen = real_popen
addon_utils.disable('NH_Blender', default_set=True)
print(json.dumps(dict(blender=bpy.app.version_string, checks=len(checks), passed=checks)), flush=True)
