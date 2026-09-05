"""Run in a disposable Blender process, with a folder containing the Yantar P3Ds.

blender --background --factory-startup --python-exit-code 1 --python tests/blender_tb_txt.py -- P:/NH_Objects/Locations/Yantar
Append --external to also exercise the legacy installed Arma3ObjectBuilder backend.
Set BLENDER_USER_CONFIG to a temporary folder to isolate NH's saved UI state.
"""
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
import addon_utils

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))
args = sys.argv[sys.argv.index('--') + 1:]
models_directory = args[0]
if '--external' in args:
    assert addon_utils.enable('Arma3ObjectBuilder', default_set=True) is not None

import NH_Blender as nh
nh.register()
from NH_Blender import nh_tb_import as addon
from NH_Blender import nh_snap

settings = dict(filepath=str(repo / 'tests/fixtures/yantar_tunnel_tb.txt'),
    models_directory=models_directory, recursive_search=True, origin_mode='CUSTOM',
    origin_east=200000.0, origin_north=0.0, origin_height=0.0,
    centering='AUTO_XY', linked_copies=True, load_textures=False)
original_objects = {o.name: [list(row) for row in o.matrix_world] for o in bpy.data.objects}
planner = list(bpy.context.scene.cray_ie_settings.import_files)
assert bpy.ops.cray.import_tb_txt(**settings) == {'FINISHED'}
records = addon.read_placements(settings['filepath'])
objects = sorted([o for o in bpy.data.objects if 'tb_txt_line' in o], key=lambda o:o['tb_txt_line'])
assert len(objects) == 8
assert objects[2].data == objects[4].data
assert objects[2].name == records[2].model
assert len({o.data for o in objects}) == 7
assert list(bpy.context.scene.cray_ie_settings.import_files) == planner
for name, matrix in original_objects.items():
    assert [list(row) for row in bpy.data.objects[name].matrix_world] == matrix
p3d, importer = addon.dependency()
assert p3d.__package__ == importer.__package__
assert ('Arma3ObjectBuilder' in p3d.__package__) == ('--external' in args)
for obj, record in zip(objects, records):
    mlod = p3d.P3D_MLOD.read_file(obj['tb_source_p3d'])
    raw = {tuple(round(v[i], 5) for i in range(3)) for v in mlod.lods[0].verts}
    loaded = {tuple(round(v.co[i], 5) for i in range(3)) for v in obj.data.vertices}
    assert raw == loaded, obj.name
    assert len(obj.data.uv_layers) > 0 and len(obj.data.materials) > 0
    vertices = [v for lod in mlod.lods for v in lod.verts]
    cx, cy = [(min(v[i] for v in vertices) + max(v[i] for v in vertices)) / 2 for i in (0, 1)]
    angle = math.radians(-record.yaw)
    c, s = math.cos(angle), math.sin(angle)
    expected = (record.east - 200000 - record.scale*(cx*c-cy*s),
                record.north - record.scale*(cx*s+cy*c), record.elevation)
    assert max(abs(a-b) for a,b in zip(obj.location, expected)) < 0.0005, obj.name
    for i, value in enumerate((c, s, 0)):
        assert abs(obj.matrix_world[i][0] - value*record.scale) < 1e-6

# Failed imports must leave geometry, selection and planner queue unchanged.
snapshot = addon._snapshot()
try:
    addon.import_layout(SimpleNamespace(**dict(settings, models_directory=str(repo/'missing_models'))), bpy.context)
except addon.ImportProblem:
    pass
else:
    raise AssertionError('Missing model directory accepted')
assert snapshot == addon._snapshot()
selection = set(bpy.context.selected_objects)
active = bpy.context.view_layer.objects.active
original_read = importer.read_file
calls = 0
def fail_on_second(*args, **kwargs):
    global calls
    calls += 1
    if calls == 2:
        raise RuntimeError('Simulated load failure')
    return original_read(*args, **kwargs)
importer.read_file = fail_on_second
try:
    try:
        addon.import_layout(SimpleNamespace(**settings), bpy.context)
    except RuntimeError as error:
        assert str(error) == 'Simulated load failure'
    else:
        raise AssertionError('Load error swallowed')
finally:
    importer.read_file = original_read
assert snapshot == addon._snapshot(), 'Rollback leaked datablocks'
assert set(bpy.context.selected_objects) == selection
assert bpy.context.view_layer.objects.active == active
assert list(bpy.context.scene.cray_ie_settings.import_files) == planner
assert nh_snap._P3D_IMPORT_TRACKING_SUPPRESS_DEPTH == 0

# Repeated imports create independent collections and honour unlinked mode.
collection, copies = addon.import_layout(SimpleNamespace(**dict(settings,
    linked_copies=False, origin_mode='FIRST', centering='ORIGIN')), bpy.context)
assert len(copies) == 8 and copies[2].data != copies[4].data
assert abs(copies[0].location.x) < 1e-6 and abs(copies[0].location.y) < 1e-6
assert len([c for c in bpy.data.collections if 'tb_txt_source' in c]) == 2
assert all(c not in objects for c in copies)
backend = p3d.__package__
nh.unregister()
nh.register()
assert bpy.ops.cray.import_tb_txt.get_rna_type()
nh.unregister()
print('NH_TB_TXT_PASS', json.dumps(dict(backend=backend, objects=8,
    rollback=True, linked_and_unlinked=True, registration_cycle=True)))
