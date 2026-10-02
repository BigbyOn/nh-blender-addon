"""Blueprint integrity, concurrent writes and failure atomicity in Blender 4.0."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))
os.environ['NH_MATERIALS_TEXTURE_ROOT'] = str(ROOT / 'dist' / 'material_worker_fixture')
os.environ['NH_MATERIALS_CACHE_ROOT'] = tempfile.mkdtemp(prefix='blueprint_edges_', dir=ROOT / 'dist')

import bpy
from NH_Blender import nh_material_cache as cache, nh_material_shader as shader
from NH_Blender import nh_materials_images as images
from NH_Blender.utilities import rvmat

checks = []
def check(name, value):
    assert value, name
    checks.append(name)
    print('PASS:', name, flush=True)

entry = rvmat.scan(shader.texture_root())['entries'][0]
pair = cache._pair(entry['rvmat'], entry['color'], ())
source = bpy.data.materials.new('Blueprint cold')
check('Cold preview graph builds', cache.build(source, entry['rvmat'], entry['color'], image_loader=images.load))
description = cache.describe(entry['rvmat'], entry['color'])
check('Description signature matches source signature', description['signature'] == rvmat.signature(pair[0], pair[1], pair[2]))
target = Path(cache._path(pair))
record = json.loads(target.read_text(encoding='utf8'))
check('Blueprint stores original PAA references', all(item['image']['source'].lower().endswith('.paa')
      for item in record['graph']['nodes'] if 'image' in item))

real_builder, real_reader = shader.build, rvmat.read
shader.build = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Fresh graph builder on warm cache'))
rvmat.read = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('RVMAT parse on warm cache'))
try:
    warm = bpy.data.materials.new('Blueprint warm')
    warm['user_property'] = 'preserve'
    check('Warm cache restores without builder or parser', cache.build(warm, entry['rvmat'], entry['color'], image_loader=images.load))
    check('Warm restore preserves unrelated material data', warm['user_property'] == 'preserve')
    check('Warm description requires no source parse', cache.describe(entry['rvmat'], entry['color'])['signature'] == description['signature'])
finally:
    shader.build, rvmat.read = real_builder, real_reader

for corruption in ('node', 'enum'):
    broken = json.loads(json.dumps(record))
    if corruption == 'node':
        broken['graph']['nodes'][0]['kind'] = 'ShaderNodeUnknownNHType'
    else:
        item = next(item for item in broken['graph']['nodes'] if 'operation' in item['properties'])
        item['properties']['operation'] = 'UNKNOWN_NH_ENUM'
    cache._write(broken, pair)
    material = bpy.data.materials.new('Corrupt ' + corruption)
    check('Invalid %s falls back to a fresh graph' % corruption,
          cache.build(material, entry['rvmat'], entry['color'], image_loader=images.load)
          and not material['nh_material_cache_hit'])
    repaired = json.loads(target.read_text(encoding='utf8'))
    check('Invalid %s recipe is replaced' % corruption, repaired['graph'] == record['graph'])

cache._write(record, pair)
preserved = bpy.data.materials.new('Preserved user graph')
preserved.use_nodes = True
preserved['user_property'] = 'keep'
before = [(node.name, node.bl_idname) for node in preserved.node_tree.nodes]
try:
    cache.build(preserved, entry['rvmat'], entry['color'],
                image_loader=lambda *a, **kw: (None, False, '', 'missing', ''))
except ValueError:
    pass
else:
    raise AssertionError('Missing image should be reported')
check('Missing image preserves the existing graph', before == [(node.name, node.bl_idname) for node in preserved.node_tree.nodes])
check('Missing image preserves unrelated data', preserved['user_property'] == 'keep')

# The JSON write itself uses no Blender API. Redirect only its path before
# concurrently writing one material record from four independent producers.
real_path = cache._path
concurrent_target = target.parent / 'concurrent.json'
cache._path = lambda pair: str(concurrent_target)
def writer(number):
    for iteration in range(25):
        cache._write(dict(writer=number, iteration=iteration, payload='x' * 8000), pair)
try:
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(writer, range(4)))
    final = json.loads(concurrent_target.read_text(encoding='utf8'))
    check('Concurrent writes produce a complete valid record', final['writer'] in range(4) and final['iteration'] == 24 and len(final['payload']) == 8000)
    check('Concurrent writers leave no temporary files', not list(target.parent.glob('concurrent.json.*.tmp')))
    original = concurrent_target.read_bytes()
    real_replace = cache.os.replace
    def failed_replace(*args):
        raise OSError('Simulated cache commit failure')
    cache.os.replace = failed_replace
    try:
        cache._write(dict(damaged=True), pair)
    except OSError:
        pass
    else:
        raise AssertionError('Write failure should propagate to the graceful caller')
    finally:
        cache.os.replace = real_replace
    check('Failed commit preserves the previous record', concurrent_target.read_bytes() == original)
    check('Failed commit removes its unique temporary file', not list(target.parent.glob('concurrent.json.*.tmp')))
finally:
    cache._path = real_path

print(json.dumps(dict(blender=bpy.app.version_string, checks=len(checks), passed=checks)), flush=True)
