"""Cache migration, missing thumbnail repair, and removed catalog member checks."""
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
from NH_Blender import nh_materials_worker as worker, nh_materials as browser
checks = []
def check(name, condition):
    assert condition, name
    checks.append(name)
    print('PASS:', name, flush=True)

with tempfile.TemporaryDirectory(dir=ROOT/'dist') as temporary:
    base = Path(temporary)
    source, cache = base/'source', base/'cache'
    source.mkdir()
    (cache/'previews').mkdir(parents=True)
    shutil.copy2(ROOT/'dist/materials_fast_worker_fixture/pending.png', cache/'pending.png')
    for name in ('one', 'two'):
        (source/(name+'.rvmat')).write_text('PixelShaderID="Super";', encoding='utf8')
    request = dict(root=str(source), cache=str(cache), generation=time.time(), paused=True, refresh=False)
    browser.atomic_json(cache/'request.json', request)
    worker.main(str(cache), once=True)
    manifest = json.loads((cache/'manifest.json').read_text())
    check('Two materials share one folder bundle', len({e['asset_file'] for e in manifest['entries']}) == 1)
    entry = manifest['entries'][0]
    entry['preview_signature'] = entry['signature']
    browser.atomic_json(cache/'manifest.json', manifest)
    # A ready marker without its PNG must not remain a permanently empty tile.
    worker.main(str(cache), once=True)
    manifest = json.loads((cache/'manifest.json').read_text())
    check('Missing PNG invalidates ready marker', not manifest['entries'][0]['preview_signature'])
    filename = manifest['entries'][0]['asset_file']
    before = (cache/'assets'/filename).stat().st_mtime_ns
    (source/'two.rvmat').unlink()
    request.update(refresh=True, generation=time.time())
    browser.atomic_json(cache/'request.json', request)
    worker.main(str(cache), once=True)
    manifest = json.loads((cache/'manifest.json').read_text())
    check('Refresh removes deleted RVMAT from manifest', len(manifest['entries']) == 1)
    check('Removed group member rewrites existing file', (cache/'assets'/filename).stat().st_mtime_ns != before)
    with bpy.data.libraries.load(str(cache/'assets'/filename), link=False) as (data, loaded):
        check('Native bundle no longer contains deleted card', len(data.materials) == 1)
        loaded.materials = data.materials
    check('Pending card contains a sphere thumbnail', len(loaded.materials[0].preview.image_pixels) > 0)
    for material in loaded.materials:
        bpy.data.materials.remove(material)
print(json.dumps(dict(blender=bpy.app.version_string, checks=len(checks), passed=checks)), flush=True)
