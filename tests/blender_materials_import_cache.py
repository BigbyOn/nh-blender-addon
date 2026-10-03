"""Exercise NH Materials reuse through real P3D import/export operators.

Run in a disposable Blender 4.0.0: --background --factory-startup
--python-exit-code 1 --python tests/blender_materials_import_cache.py.
The MLOD models, valid DXT1 PAA sources and caches are created under dist.
"""
import contextlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))

import bpy
import addon_utils
import NH_Blender as addon
import NH_bundle
from NH_bundle.io import data_p3d, data_paa, import_p3d, import_paa
from NH_bundle.utilities import generic
from NH_Blender import nh_material_shader as shader, nh_materials as browser, nh_materials_worker as worker
from NH_Blender import nh_textures as textures, nh_snap
from NH_Blender.utilities import rvmat

checks = []
default_project_root = NH_bundle.get_prefs().project_root
assert default_project_root == 'P:\\', 'Embedded exporter must default to the game P drive'
calls = dict(shader=0, decode=0, backend_preview=0)
original_shader = shader.build
original_decode = data_paa.PAA_MIPMAP.decompress
original_backend_preview = import_p3d.setup_material_nodes
original_queue = None
queued = []


def check(name, condition):
    assert condition, name
    checks.append(name)
    print('PASS:', name, flush=True)


def count_shader(*args, **kwargs):
    calls['shader'] += 1
    return original_shader(*args, **kwargs)


def count_decode(*args, **kwargs):
    calls['decode'] += 1
    return original_decode(*args, **kwargs)


def count_backend_preview(*args, **kwargs):
    calls['backend_preview'] += 1
    return original_backend_preview(*args, **kwargs)


def queue_preview(material_path, color_path, *, search_roots=()):
    queued.append((material_path, color_path, tuple(search_roots)))
    return True


@contextlib.contextmanager
def forbid_rebuild():
    def forbidden_shader(*args, **kwargs):
        raise AssertionError('Ready import rebuilt the Super shader')
    def forbidden_decode(*args, **kwargs):
        raise AssertionError('Ready import decoded a PAA texture')
    old_shader, old_decode = shader.build, data_paa.PAA_MIPMAP.decompress
    shader.build, data_paa.PAA_MIPMAP.decompress = forbidden_shader, forbidden_decode
    try:
        yield
    finally:
        shader.build, data_paa.PAA_MIPMAP.decompress = old_shader, old_decode


def paa(path, rgb565, *, size=16):
    """One mip containing independent, valid solid-color DXT1 blocks."""
    block = struct.pack('<HHI', rgb565, 0, 0)
    encoded = block * (size * size // 16)
    path.write_bytes(struct.pack('<HHHH', 0xff01, 0, size, size)
                     + len(encoded).to_bytes(3, 'little') + encoded + b'\0' * 6)


def p3d(path, texture, material):
    mlod = data_p3d.P3D_MLOD()
    for resolution in (0, 1):
        lod = data_p3d.P3D_LOD()
        lod.resolution = data_p3d.P3D_LOD_Resolution(0, resolution)
        lod.verts = [(0, 0, 0, 0), (1, 0, 0, 0), (0, 1, 0, 0)]
        lod.normals = [(0, 0, 1)]
        lod.faces = [[[0, 1, 2], [0, 0, 0], [(0, 0), (1, 0), (0, 1)],
                      texture, material, 0]]
        uv = data_p3d.P3D_TAGG()
        uv.name = '#UVSet#'
        uv.data = data_p3d.P3D_TAGG_DataUVSet()
        uv.data.id = 1
        uv.data.uvs = [(.2, .3), (.8, .3), (.2, .9)]
        lod.taggs.append(uv)
        mlod.lods.append(lod)
    mlod.write_file(str(path))


def import_model(path, *, load=True, absolute=False):
    before = set(bpy.data.objects)
    check('Native P3D import finishes: ' + path.stem,
          bpy.ops.nh.import_p3d(filepath=str(path), load_textures=load,
              absolute_paths=absolute, proxy_action='NOTHING',
              additional_data={'NORMALS', 'UV', 'MATERIALS'}) == {'FINISHED'})
    objects = sorted(set(bpy.data.objects) - before, key=lambda obj: obj.name)
    check('Native importer creates two visual LODs: ' + path.stem,
          len(objects) == 2 and all(obj.a3ob_properties_object.is_a3_lod for obj in objects))
    return objects, objects[0].data.materials[0]


def engine_path(path):
    return str(path).replace('/', '\\').lower()


def export_model(objects, path, expected):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    check('Native P3D export finishes: ' + path.stem,
          bpy.ops.nh.export_p3d(filepath=str(path), use_selection=True,
              visible_only=False, relative_paths=True, validate_lods=False,
              force_lowercase=False, generate_components=False) == {'FINISHED'})
    exported = data_p3d.P3D_MLOD.read_file(str(path))
    paths = [(engine_path(face[3]), engine_path(face[4]))
             for lod in exported.lods for face in lod.faces]
    check('Exported face paths reference source PAA/RVMAT: ' + path.stem,
          len(exported.lods) == 2 and paths and all(pair == expected for pair in paths)
          and not any('.png' in value or '.blend' in value for pair in paths for value in pair))


def check_shader(objects, material, label):
    check('Super graph restored: ' + label, material.node_tree is not None
          and material.node_tree.nodes.get('DayZ Super Output') is not None
          and material.node_tree.nodes.get('DayZ Normal') is not None
          and material.node_tree.nodes.get('SMDI: G Spec / B Gloss') is not None)
    check('Full source image dimensions retained: ' + label,
          all(tuple(node.image.size) == (16, 16) for node in material.node_tree.nodes
              if getattr(node, 'image', None) is not None))
    check('UV1 alias copies original second UV set: ' + label,
          all(obj.data.uv_layers.active.name == 'UVSet 0'
              and obj.data.uv_layers.get('NH_UV1') is not None
              and all((a.uv-b.uv).length < 1e-6 for a, b in zip(
                  obj.data.uv_layers['UVSet 1'].data, obj.data.uv_layers['NH_UV1'].data))
              for obj in objects))


errors = []
with tempfile.TemporaryDirectory(prefix='materials_import_', dir=ROOT / 'dist') as temp:
    project = Path(temp)
    source = project / 'NH_ObjectTextures/cache_case'
    source.mkdir(parents=True)
    models = project / 'NH_Objects'
    models.mkdir()
    os.environ['NH_MATERIALS_TEXTURE_ROOT'] = str(project / 'NH_ObjectTextures')
    os.environ['NH_MATERIALS_CACHE_ROOT'] = str(project / 'materials')

    def shared_cache(create=False):
        path = project / 'shared'
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return str(path)
    textures._nh_blender_shared_cache_base = shared_cache
    NH_bundle.get_prefs().project_root = str(project)
    paa(source/'wall_co.paa', 0xc208)
    paa(source/'wall_nohq.paa', 0x841f)
    paa(source/'wall_smdi.paa', 0x19d9)
    content = '''PixelShaderID="Super"; specularPower=75; specular[]={.2,.3,.4,1};
class Stage1 {texture="NH_ObjectTextures\\cache_case\\wall_nohq.paa"; uvSource="tex1";};
class Stage5 {texture="NH_ObjectTextures\\cache_case\\wall_smdi.paa";};
class Stage6 {texture="#(ai,64,64,1)fresnel(0.4,0.2)";};'''
    (source/'wall.rvmat').write_text(content)
    (source/'cold.rvmat').write_text(content)
    raw_color = r'NH_ObjectTextures\cache_case\wall_co.paa'
    raw_rvmat = r'NH_ObjectTextures\cache_case\wall.rvmat'
    cold_rvmat = r'NH_ObjectTextures\cache_case\cold.rvmat'
    expected = (engine_path(raw_color), engine_path(raw_rvmat))
    p3d(models/'ready.p3d', raw_color, raw_rvmat)
    p3d(models/'cold.p3d', raw_color, cold_rvmat)
    p3d(models/'normal_slot.p3d', raw_color.replace('_co.', '_nohq.'), raw_rvmat)
    p3d(models/'no_preview.p3d', raw_color, raw_rvmat)
    p3d(models/'procedural.p3d', '#(argb,8,8,3)color(0.2,0.4,0.6,1,CO)', '')
    check('Enable real addon with import hook', addon_utils.enable('NH_Blender',
          default_set=True, handle_error=errors.append) is addon and not errors
          and bool(nh_snap._P3D_IMPORT_READ_FILE_PATCHES))
    # addon_utils may reload the package while enabling; apply fixture-specific
    # paths to the final module objects after that reload.
    textures._nh_blender_shared_cache_base = shared_cache
    NH_bundle.get_prefs().project_root = str(project)
    original_queue = browser.queue_import_preview
    browser.queue_import_preview = queue_preview
    shader.build = count_shader
    data_paa.PAA_MIPMAP.decompress = count_decode
    import_p3d.setup_material_nodes = count_backend_preview
    try:
        entry = dict(id=rvmat.identity(str(source/'wall.rvmat'), str(source/'wall_co.paa')),
                     name='Ready library material', color=str(source/'wall_co.paa'),
                     rvmat=str(source/'wall.rvmat'))
        library_material = browser.material_from_entry(entry)
        check('Library material seeds persistent Super cache', calls['shader'] == 1)
        check('Library material prepares original full PNG textures',
              len(list((project/'shared').rglob('*.png'))) == 3)
        before = dict(calls)
        with forbid_rebuild():
            ready_objects, ready_material = import_model(models/'ready.p3d')
        check_shader(ready_objects, ready_material, 'ready library')
        check('Warm ordinary import uses no decode/build/backend preview', calls == before)
        check('Original relative engine fields survive cache restore',
              tuple(map(engine_path, textures._get_p3d_material_paths(ready_material))) == expected)
        check('Material property export retains source paths',
              tuple(map(engine_path, ready_material.a3ob_properties_material.to_p3d(True))) == expected)
        export_model(ready_objects, models/'cached_export.p3d', expected)
        with forbid_rebuild():
            roundtrip_objects, roundtrip_material = import_model(models/'cached_export.p3d')
        check_shader(roundtrip_objects, roundtrip_material, 'exported P3D roundtrip')

        before = dict(calls)
        cold_objects, cold_material = import_model(models/'cold.p3d')
        check_shader(cold_objects, cold_material, 'cold import')
        check('Cold import builds one blueprint for shared LOD material',
              calls['shader'] == before['shader'] + 1 and calls['backend_preview'] == 0)
        # Remove the imported material to prove the second import comes from
        # persistent metadata rather than the existing scene datablock.
        for obj in cold_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.materials.remove(cold_material)
        with forbid_rebuild():
            cold_objects, cold_material = import_model(models/'cold.p3d')
        check_shader(cold_objects, cold_material, 'warm cold-import cache')
        check('Import requests a targeted library preview after successful Super',
              len(queued) >= 4 and all(Path(item[1]).suffix == '.paa' for item in queued))

        before = calls['shader']
        (source/'wall.rvmat').write_text(content.replace('75', '125'))
        changed_objects, changed_material = import_model(models/'ready.p3d')
        check('RVMAT source modification invalidates blueprint', calls['shader'] == before + 1)
        check_shader(changed_objects, changed_material, 'changed RVMAT')
        before = dict(calls)
        paa(source/'wall_nohq.paa', 0x07e0)
        changed_ns = time.time_ns()
        os.utime(source/'wall_nohq.paa', ns=(changed_ns, changed_ns))
        map_objects, map_material = import_model(models/'ready.p3d')
        check('Stage PAA modification invalidates blueprint and full image cache',
              calls['shader'] == before['shader'] + 1 and calls['decode'] > before['decode'])
        normal_image = next(node.image for node in map_material.node_tree.nodes
                            if node.type == 'TEX_IMAGE' and node.name.startswith('Stage1 '))
        pixel = tuple(normal_image.pixels[:3])
        check('Changed map pixels are reloaded in existing Image datablock',
              pixel[1] > .9 and pixel[0] < .1 and pixel[2] < .1)

        with forbid_rebuild():
            normal_objects, normal_material = import_model(models/'normal_slot.p3d')
        check('Normal slot auto-selects sibling color while retaining relative source path',
              tuple(map(engine_path, textures._get_p3d_material_paths(normal_material))) == expected)
        export_model(normal_objects, models/'color_selected_export.p3d', expected)

        before = dict(calls)
        bpy.context.scene.cray_ie_settings.import_keep_converted_textures = False
        with forbid_rebuild():
            no_keep_objects, no_keep_material = import_model(models/'ready.p3d')
        check_shader(no_keep_objects, no_keep_material, 'PNG cache setting disabled')
        check('Disabling PNG retention does not bypass ready Super cache', calls == before)
        bpy.context.scene.cray_ie_settings.import_keep_converted_textures = True
        before = dict(calls)
        unused_objects, unused_material = import_model(models/'no_preview.p3d', load=False)
        check('Explicit load_textures=False skips preview work', calls == before
              and not unused_material.get('nh_super_rvmat'))

        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        for obj in ready_objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = ready_objects[0]
        check('Apply library material to imported LODs',
              browser.apply_material(bpy.context, library_material) == 2)
        check('Apply material property export uses physical project root',
              tuple(map(engine_path, library_material.a3ob_properties_material.to_p3d(True))) == expected)
        export_model(ready_objects, models/'apply_export.p3d', expected)
        with forbid_rebuild():
            applied_objects, applied_material = import_model(models/'apply_export.p3d')
        check_shader(applied_objects, applied_material, 'Apply/export/import roundtrip')

        # Reproduce the user's P: junction without altering their actual drive.
        realpath = generic.os.path.realpath
        def project_junction(path, *args, **kwargs):
            value = os.fspath(path)
            if value.lower().startswith('p:\\'):
                value = str(project / value[3:].replace('\\', os.sep))
            return realpath(value, *args, **kwargs)
        generic.os.path.realpath = project_junction
        NH_bundle.get_prefs().project_root = default_project_root
        try:
            check('Junction project root maps physical Apply sources to engine paths',
                  tuple(map(engine_path, library_material.a3ob_properties_material.to_p3d(True))) == expected)
            check('Disabling Relative Paths retains absolute original source paths',
                  tuple(map(engine_path, library_material.a3ob_properties_material.to_p3d(False)))
                  == tuple(map(engine_path, textures._get_p3d_material_paths(library_material))))
            check('Relative engine paths survive junction export',
                  generic.make_relative(raw_color, 'P:\\') == raw_color)
            check('Project root matching respects directory boundaries',
                  not generic.make_relative(str(project)+'_other/wall.paa', str(project))
                  .replace('\\', '/').startswith('../'))
            export_model(ready_objects, models/'junction_apply_export.p3d', expected)
        finally:
            generic.os.path.realpath = realpath
            NH_bundle.get_prefs().project_root = str(project)

        # A thumbnail queue error is reported separately and leaves Super valid.
        def failed_queue(*args, **kwargs):
            raise RuntimeError('Simulated thumbnail queue failure')
        browser.queue_import_preview = failed_queue
        stats = textures._postprocess_imported_material_previews(bpy.context,
            applied_objects, show_materials=True, keep_converted_textures=True)
        check('Thumbnail failure preserves successful scene material',
              stats['previewed'] == 1 and not stats['errors']
              and len(stats['library_errors']) == 1)
        check_shader(applied_objects, applied_material, 'queue failure')
        browser.queue_import_preview = queue_preview

        base_png = Path(textures._paa_preview_cache_path(str(source/'wall_co.paa')))
        check('Full color PNG exists before cache-only guard', base_png.is_file())
        base_png.unlink()
        nodes_before = {node.as_pointer() for node in applied_material.node_tree.nodes}
        before = dict(calls)
        stats = textures._postprocess_imported_material_previews(bpy.context,
            applied_objects, show_materials=True, keep_converted_textures=True,
            cache_missing_textures=False)
        check('Cache-only missing texture avoids PAA decode and preserves original graph',
              stats['previewed'] == 0 and len(stats['errors']) == 1
              and calls == before and not base_png.exists()
              and nodes_before == {node.as_pointer() for node in applied_material.node_tree.nodes})

        before = calls['shader']
        stats = textures._postprocess_imported_material_previews(bpy.context,
            applied_objects, show_materials=True, keep_converted_textures=True,
            force_rebuild_cache=True)
        check('Force rebuild invalidates ready material blueprint',
              stats['previewed'] == 1 and not stats['errors'] and calls['shader'] == before + 1)
        before = dict(calls)
        procedural_objects, procedural_material = import_model(models/'procedural.p3d')
        rgb = next(node for node in procedural_material.node_tree.nodes if node.type == 'RGB')
        check('Deferred ordinary import retains procedural color preview',
              max(abs(a-b) for a, b in zip(rgb.outputs[0].default_value, (.2, .4, .6, 1))) < 1e-6
              and calls['shader'] == before['shader'] and calls['decode'] == before['decode'])
        before_backend = calls['backend_preview']
        before_queue = len(queued)
        planner_count = len(bpy.context.scene.cray_ie_settings.import_files)
        with nh_snap._suppress_p3d_import_tracking():
            suppressed_objects, suppressed_material = import_model(models/'ready.p3d')
        check('Suppressed TB-style import retains independent backend preview',
              not suppressed_material.get('nh_super_rvmat')
              and any(node.type == 'BSDF_PRINCIPLED' for node in suppressed_material.node_tree.nodes)
              and calls['backend_preview'] > before_backend and len(queued) == before_queue
              and len(bpy.context.scene.cray_ie_settings.import_files) == planner_count
              and nh_snap._P3D_IMPORT_TRACKING_SUPPRESS_DEPTH == 0)

        # Native drag appends a metadata-only asset before async hydration.
        # Export must already preserve its source pair during that interval.
        asset_cache = project/'native_stub'
        (asset_cache/'assets').mkdir(parents=True)
        asset_entry = dict(entry, folder='cache_case', root=str(project/'NH_ObjectTextures'))
        worker.write_asset(str(asset_cache), asset_entry)
        with bpy.data.libraries.load(str(asset_cache/'assets'/(entry['id']+'.blend'))) as (source_ids, target_ids):
            target_ids.materials = source_ids.materials
        stub = target_ids.materials[0]
        props = stub.a3ob_properties_material
        check('Native material stub has source metadata before hydration',
              stub.get('nh_material_stub') and not props.texture_path and not props.material_path
              and not stub.get('nh_super_rvmat') and (not stub.node_tree or len(stub.node_tree.nodes) <= 2))
        before = dict(calls)
        for obj in suppressed_objects:
            obj.data.materials[0] = stub
        with forbid_rebuild():
            export_model(suppressed_objects, models/'stub_before_hydration_export.p3d', expected)
        check('Metadata stub exports correct source pair without conversion',
              calls == before and stub.get('nh_material_stub')
              and tuple(map(engine_path, props.to_p3d(True))) == expected
              and not props.texture_path and not props.material_path)
        props.texture_path, props.material_path = raw_color, cold_rvmat
        check('Explicit P3D fields override material stub metadata',
              tuple(map(engine_path, props.to_p3d(True))) == (engine_path(raw_color), engine_path(cold_rvmat)))
        props.texture_path, props.material_path = '', ''
        stub['nh_material_stub'] = False
        check('Metadata fallback is restricted to unconverted material stubs', props.to_p3d(True) == ('', ''))
        stub['nh_material_stub'] = True
        stub['nh_material_color'], stub['nh_material_rvmat'] = 'preview.png', 'assets.blend'
        check('Stub export rejects thumbnail and Blender cache metadata', props.to_p3d(True) == ('', ''))
        stub['nh_material_color'], stub['nh_material_rvmat'] = entry['color'], entry['rvmat']
        props.texture_type = 'COLOR'
        props.color_value = (.2, .3, .4, 1)
        procedural_texture, procedural_rvmat = props.to_p3d(True)
        check('Explicit procedural source bypasses material stub fallback',
              procedural_texture.startswith('#(argb,8,8,3)color(') and not procedural_rvmat)
    finally:
        shader.build = original_shader
        data_paa.PAA_MIPMAP.decompress = original_decode
        import_p3d.setup_material_nodes = original_backend_preview
        if original_queue is not None:
            browser.queue_import_preview = original_queue
        addon_utils.disable('NH_Blender', default_set=True, handle_error=errors.append)

check('Import cache regression clean unregister', not errors)
result = dict(blender=bpy.app.version_string, addon_file=addon.__file__,
              addon_version=addon.bl_info['version'], passed=True, checks=checks,
              calls=calls, actual_p3d_operators=True)
(ROOT/'dist/materials_import_cache_result.json').write_text(
    json.dumps(result, indent=2), encoding='utf8')
print('NH_MATERIALS_IMPORT_CACHE_PASS', json.dumps(result), flush=True)
