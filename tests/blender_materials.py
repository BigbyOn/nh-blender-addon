"""NH Materials integration and 4.0 API checks in a disposable Blender.

blender --background --factory-startup --python-exit-code 1 --python tests/blender_materials.py
"""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))
os.environ['NH_MATERIALS_CACHE_ROOT'] = str(ROOT / 'dist/materials_test_cache')
os.environ['BLENDER_USER_CONFIG'] = str(ROOT / 'dist/materials_test_config')
os.environ['NH_MATERIALS_TEXTURE_ROOT'] = r'C:\NH_Project\DayZ P Drive\Stable\NH_ObjectTextures'

import bpy
import bmesh
import addon_utils
import NH_Blender as addon
from NH_Blender import nh_materials as browser, nh_material_shader as shader
from NH_Blender import nh_textures as textures
from NH_Blender.utilities import rvmat

# Keep every generated cache, including PAA conversion, inside the workspace.
def test_cache(create=False):
    path = ROOT / 'dist/materials_test_cache/shared'
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return str(path)
textures._nh_blender_shared_cache_base = test_cache

checks = []
def check(name, condition):
    assert condition, name
    checks.append(name)
    print('PASS:', name, flush=True)

errors = []
check('Enable through Blender restricted registration',
      addon_utils.enable('NH_Blender', default_set=True, handle_error=errors.append) is addon
      and not errors)
if os.environ.get('NH_ADDON_TEST_ROOT'):
    check('Loaded package extracted from release ZIP', Path(addon.__file__).parent.resolve()
          == (Path(os.environ['NH_ADDON_TEST_ROOT'])/'NH_Blender').resolve())
check('Minimum Blender 4.0.0', addon.bl_info['blender'] == (4, 0, 0))
check('Old restore operator removed', not hasattr(bpy.types, 'CRAY_OT_UpdateObjectPreview'))
check('Library initialization deferred', browser._library_pending)
# Background scripts do not pump UI timers; run its first tick after enable.
# A transient library failure must remain retryable without disabling the addon.
register_library = browser.library_register
def fail_library():
    raise RuntimeError('Simulated asset library failure')
browser.library_register = fail_library
check('Library failure retried without disabling addon', browser.timer() == 5.0
      and browser._library_pending and addon.__addon_enabled__
      and browser._status == 'Simulated asset library failure')
browser.library_register = register_library
browser.timer()
check('Library registered', bpy.context.preferences.filepaths.asset_libraries.get('NH Materials') is not None)
check('Library recovers on next timer tick', not browser._library_pending and not browser._status)
check('Browser timer registered', bpy.app.timers.is_registered(browser.timer))

# The preferences callback must also defer operators and update the same library.
source_root = os.environ.pop('NH_MATERIALS_TEXTURE_ROOT')
preferences = bpy.context.preferences.addons['NH_Blender'].preferences
preferences.nh_textures_folder = str(ROOT/'dist/material_worker_fixture')
changed_cache = browser.cache_root()
check('Folder preference schedules deferred update', browser._library_pending)
browser.timer()
check('Folder preference updates library path', not browser._library_pending
      and bpy.context.preferences.filepaths.asset_libraries.get(browser.LIBRARY_NAME).path == changed_cache)
preferences.nh_textures_folder = ''
check('Empty folder uses P drive default', shader.texture_root() == rvmat.DEFAULT_ROOT)
os.environ['NH_MATERIALS_TEXTURE_ROOT'] = source_root
browser.timer()

parsed = rvmat.parse('''// class Stage8 { fake = 1; }\n
PixelShaderID="Super"; specularPower=75;
class Stage1 { texture="NH_ObjectTextures\\test\\wall_nohq.paa";
 class uvTransform {aside[]={2,0,0}; up[]={0,3,0}; pos[]={.2,.4,0};};};
class Stage2 : Stage1 {texture="#(argb,8,8,3)color(0.5,0.5,0.5,1,DT)";};''')
check('Comments, Windows paths, inheritance, UV transforms',
      'stage8' not in parsed and parsed['stage1']['texture'].endswith('wall_nohq.paa')
      and parsed['stage2']['uvtransform']['aside'][0] == 2)

with tempfile.TemporaryDirectory(dir=ROOT / 'dist') as temporary:
    temporary = Path(temporary)
    # Known flat normal, independent specular and gloss values.
    for name, color in [('test_co', (.6, .2, .1, 1)), ('test_nohq', (.5, .75, 1, 1)),
                        ('test_smdi', (1, .3, .8, 1))]:
        image = bpy.data.images.new(name, 8, 8, is_data=name != 'test_co')
        image.pixels[:] = list(color) * 64
        image.filepath_raw = str(temporary / (name + '.png'))
        image.file_format = 'PNG'
        image.save()
        bpy.data.images.remove(image)
    path = temporary / 'test.rvmat'
    path.write_text('''PixelShaderID="Super"; specular[]={.2,.4,.6,1}; specularPower=100;
class Stage1 {texture="test_nohq.png"; uvSource="tex";};
class Stage5 {texture="test_smdi.png"; uvSource="tex";};
class Stage6 {texture="#(ai,64,64,1)fresnel(0.4,0.2)";};''')
    material = bpy.data.materials.new('Test Super')
    check('Build Super shader', shader.build(material, str(path), str(temporary/'test_co.png')))
    nodes = material.node_tree.nodes
    check('Normal map + DirectX Y correction', nodes.get('DayZ Normal') is not None
          and nodes.get('DayZ normal: invert Y') is not None)
    check('Separate Spec/Gloss and Fresnel', nodes.get('SMDI: G Spec / B Gloss') is not None
          and nodes.get('Gloss to GGX roughness') is not None and nodes.get('Stage6 Fresnel N/K') is not None)
    check('No incorrect metallic mapping', not any(n.bl_idname == 'ShaderNodeBsdfPrincipled' for n in nodes))
    # A failed decode preserves the existing user's graph.
    before = {n.as_pointer() for n in nodes}
    path.write_text(path.read_text().replace('test_nohq.png', 'missing.png'))
    try:
        shader.build(material, str(path), str(temporary/'test_co.png'))
    except ValueError:
        pass
    else:
        raise AssertionError('Missing map accepted')
    check('Missing texture preserves existing shader', before == {n.as_pointer() for n in nodes})
    path.write_text(path.read_text().replace('missing.png', 'test_nohq.png'))
    old_signature = rvmat.signature(str(path), str(temporary/'test_co.png'), str(temporary))
    path.write_text(path.read_text().replace('100', '50'))
    check('RVMAT changes invalidate cache', old_signature != rvmat.signature(str(path), str(temporary/'test_co.png'), str(temporary)))

    for obj in list(bpy.context.selected_objects):
        obj.select_set(False)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.object
    cube.data.materials.append(bpy.data.materials.new('Original'))
    untouched = cube.copy()
    untouched.data = cube.data
    bpy.context.scene.collection.objects.link(untouched)
    untouched.select_set(False)
    check('Apply Object Mode', browser.apply_material(bpy.context, material) == 1)
    check('Shared unselected geometry unchanged', untouched.data != cube.data
          and untouched.data.materials[0] != material)
    original = bpy.data.materials.new('Original Edit')
    cube.data.materials[0] = original
    bpy.ops.object.mode_set(mode='EDIT')
    mesh = bmesh.from_edit_mesh(cube.data)
    for face in mesh.faces:
        face.select_set(False)
    mesh.faces.ensure_lookup_table()
    mesh.faces[0].select_set(True)
    check('Apply selected faces', browser.apply_material(bpy.context, material) == 1)
    check('Unselected faces preserved', mesh.faces[0].material_index == 1
          and all(face.material_index == 0 for face in list(mesh.faces)[1:]))
    bpy.ops.object.mode_set(mode='OBJECT')

real_root = shader.texture_root()
path = str(Path(real_root)/'bar100/nonstop.rvmat')
if os.path.isfile(path):
    color = str(Path(real_root)/'bar100/nonstop_co.paa')
    material = browser.material_from_entry(dict(id=rvmat.identity(path, color),
        name='Real Nonstop', rvmat=path, color=color))
    check('Real PAA decode and Super stages', material.get('nh_super_rvmat') == path)
    check('P3D paths retained', textures._get_p3d_material_paths(material) == (color, path))
    source = bpy.data.objects.new('Source P3D', bpy.data.meshes.new('Source P3D Mesh'))
    source.data.materials.append(material)
    source.data.uv_layers.new(name='UVMap')
    stats = textures._postprocess_imported_material_previews(bpy.context, [source],
        show_materials=True, keep_converted_textures=True)
    check('Ordinary import builds Super automatically', stats['previewed'] == 1
          and not stats['errors'] and material.node_tree.nodes.get('DayZ Super Output') is not None)
    check('UV1 alias prepared', source.data.uv_layers.get('NH_UV1') is not None)

# Native metadata asset, catalog and custom preview writing API.
from NH_Blender import nh_materials_worker as worker
cache = Path(browser.cache_root())
(cache/'assets').mkdir(parents=True, exist_ok=True)
entry = dict(id='test_asset', name='Test Native Asset', color=color, rvmat=path,
             root=real_root, folder='bar100')
worker.write_asset(str(cache), entry)
with bpy.data.libraries.load(str(cache/'assets/test_asset.blend'), link=False) as (data, loaded):
    loaded.materials = data.materials
stub = loaded.materials[0]
check('Native asset metadata and folder catalog', stub.asset_data.catalog_id == browser.catalog_id('bar100')
      and stub['nh_material_rvmat'] == path)
bpy.data.materials.remove(stub)

# Parent folder catalogs must remain navigable when only descendants contain assets.
check('Nested folder navigation', {'one', 'one/two', 'one/two/three'} <=
      browser.catalog_folders([dict(folder='one/two/three')]))
check('Existing collider runtime self-test', bpy.ops.cray.run_collision_tool_self_test_exp() == {'FINISHED'})

# Live source reload must unregister the old timer and preferences class.
addon = importlib.reload(addon)
addon.register()
browser = addon._materials
check('Hot reload removes old runtime', bpy.app.timers.is_registered(browser.timer))

addon_utils.disable('NH_Blender', default_set=True, handle_error=errors.append)
check('Clean unregister', not bpy.app.timers.is_registered(browser.timer))
check('Enable again after disable', addon_utils.enable('NH_Blender', default_set=True,
      handle_error=errors.append) is addon and not errors)
browser.timer()
check('Re-enable retains single NH Materials library',
      len([library for library in bpy.context.preferences.filepaths.asset_libraries
           if library.name == browser.LIBRARY_NAME]) == 1)
addon_utils.disable('NH_Blender', default_set=True, handle_error=errors.append)
check('Disable removes runtime and preferences', not errors
      and not bpy.app.timers.is_registered(browser.timer) and not browser._library_pending
      and 'NH_Blender' not in bpy.context.preferences.addons)
print(json.dumps(dict(blender=bpy.app.version_string, checks=len(checks), passed=checks), ensure_ascii=False), flush=True)
