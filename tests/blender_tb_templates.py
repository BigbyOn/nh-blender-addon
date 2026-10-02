"""Real TXT import through configured TML aliases in a disposable Blender 4.0+."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_ADDON_TEST_ROOT', str(ROOT)))

import bpy
import addon_utils
import NH_Blender as addon
from NH_Blender import nh_tb_import as tb
from NH_bundle.io import data_p3d

checks = []


def check(name, condition):
    assert condition, name
    checks.append(name)
    print('PASS:', name, flush=True)


def write_model(path, width=6):
    path.parent.mkdir(parents=True, exist_ok=True)
    mlod = data_p3d.P3D_MLOD()
    lod = data_p3d.P3D_LOD()
    lod.resolution = data_p3d.P3D_LOD_Resolution(0, 0)
    lod.verts = [(2, 4, 3, 0), (2 + width, 4, 3, 0), (2, 10, 3, 0)]
    lod.normals = [(0, 0, 1)]
    lod.faces = [[[0, 1, 2], [0, 0, 0], [(0, 0), (1, 0), (0, 1)], '', '', 0]]
    mlod.lods.append(lod)
    service = data_p3d.P3D_LOD()
    service.resolution = data_p3d.P3D_LOD_Resolution(9, 0)
    service.verts = [(-2, -4, -1, 0)]
    mlod.lods.append(service)
    mlod.write_file(str(path))


def row(name, east):
    return f'"{name}";{east};100;0;0;0;1;7;\n'


errors = []
check('Enable add-on through restricted registration',
      addon_utils.enable('NH_Blender', default_set=True, handle_error=errors.append) is addon and not errors)
preferences = bpy.context.preferences.addons['NH_Blender'].preferences
check('Template folder has requested P drive default',
      preferences.tb_templates_folder == r'P:\NH_Objects\TemplateLibs')
check('Template preference is a folder picker',
      preferences.bl_rna.properties['tb_templates_folder'].subtype == 'DIR_PATH')

with tempfile.TemporaryDirectory(prefix='tb_templates_', dir=ROOT / 'dist') as folder:
    game = Path(folder)
    objects_root = game / 'NH_Objects'
    libraries = objects_root / 'TemplateLibs'
    libraries.mkdir(parents=True)
    actual = objects_root / 'common/furniture/cabinets/wood_shelf_small_b_flip.p3d'
    wrong = objects_root / 'wood_shelf_small_b.p3d'
    legacy = objects_root / 'loose/legacy_model.p3d'
    write_model(actual)
    write_model(wrong, width=99)
    write_model(legacy)
    library = libraries / 'furniture.tml'
    library.write_text('<Library><Template><Name>wood_shelf_small_b</Name>'
                       '<File>nh_objects\\common\\furniture\\cabinets\\wood_shelf_small_b_flip.p3d</File>'
                       '</Template></Library>', encoding='utf-8')
    txt = game / 'placements.txt'
    txt.write_text(row('wood_shelf_small_b', 200010) + row('WOOD_SHELF_SMALL_B', 200020)
                   + row('legacy_model', 200030), encoding='utf-8')
    preferences.tb_templates_folder = str(libraries)
    check('Importer reads the configured add-on folder',
          Path(tb.template_libraries_directory()).resolve() == libraries.resolve())
    settings = dict(filepath=str(txt), models_directory=str(objects_root), recursive_search=True,
                    origin_mode='CUSTOM', origin_east=200000, origin_north=0, origin_height=0,
                    centering='AUTO_XY', linked_copies=True, load_textures=False)
    planner = list(bpy.context.scene.cray_ie_settings.import_files)
    check('Native TXT operator imports aliases and fallback together',
          bpy.ops.cray.import_tb_txt(**settings) == {'FINISHED'})
    imported = sorted([obj for obj in bpy.data.objects if 'tb_txt_line' in obj],
                      key=lambda obj: obj['tb_txt_line'])
    check('All three placements imported', len(imported) == 3)
    check('TML path wins over wrong same-named P3D',
          all(Path(obj['tb_source_p3d']) == actual for obj in imported[:2]))
    check('Imported geometry belongs to mapped model', len(imported[0].data.vertices) == 3
          and abs(imported[0].data.vertices[1].co.x - 8) < 1e-6)
    check('Original template aliases retained',
          [obj['tb_model_name'] for obj in imported[:2]] == ['wood_shelf_small_b', 'WOOD_SHELF_SMALL_B'])
    check('Aliases sharing a model use linked geometry', imported[0].data is imported[1].data)
    check('Names without templates retain recursive P3D lookup', Path(imported[2]['tb_source_p3d']) == legacy)
    check('All-LOD centering remains unchanged', list(imported[0]['tb_anchor']) == [3, 3, 0]
          and abs(imported[0].location.x - 7) < 1e-6 and abs(imported[0].location.y - 97) < 1e-6)
    check('TML import does not modify planner queue', list(bpy.context.scene.cray_ie_settings.import_files) == planner)
    collection = imported[0].users_collection[0]
    check('Collection records the configured template libraries',
          Path(collection['tb_template_libraries']).resolve() == libraries.resolve())

    # An authoritative missing/ambiguous TML mapping must fail before scene mutation.
    selection, active = set(bpy.context.selected_objects), bpy.context.view_layer.objects.active
    before = tb._snapshot()
    library.write_text('<Library><Template><Name>wood_shelf_small_b</Name>'
                       '<File>NH_Objects/missing.p3d</File></Template></Library>', encoding='utf-8')
    try:
        tb.import_layout(type('Settings', (), settings)(), bpy.context)
    except tb.ImportProblem as error:
        check('Missing mapped target identifies the template and source file',
              'wood_shelf_small_b' in str(error) and 'missing.p3d' in str(error))
    else:
        raise AssertionError('Missing TML target silently used a same-named P3D')
    check('Failed preflight preserves scene and selection', before == tb._snapshot()
          and set(bpy.context.selected_objects) == selection and bpy.context.view_layer.objects.active is active)

    library.write_text('<Library><Template><Name>wood_shelf_small_b</Name>'
                       '<File>NH_Objects/wood_shelf_small_b.p3d</File></Template>'
                       '<Template><Name>wood_shelf_small_b</Name>'
                       '<File>NH_Objects/loose/legacy_model.p3d</File></Template></Library>', encoding='utf-8')
    try:
        tb.import_layout(type('Settings', (), settings)(), bpy.context)
    except tb.ImportProblem as error:
        check('Conflicting templates produce a clear error', 'wood_shelf_small_b' in str(error))
    else:
        raise AssertionError('Conflicting TML mapping accepted')
    check('Conflicting templates preserve scene', before == tb._snapshot())

    # Folder settings are used on every import; no restart or stale library index.
    other = game / 'custom_template_libraries'
    other.mkdir()
    (other / 'custom.tml').write_text('<Library><Template><Name>wood_shelf_small_b</Name>'
                                    f'<File>{legacy.as_posix()}</File></Template></Library>', encoding='utf-8')
    preferences.tb_templates_folder = str(other)
    records, resolved, plans, importer = tb.preflight(type('Settings', (), settings)(), bpy.context)
    check('Changed folder takes effect immediately', resolved[records[0].model] == legacy)
    preferences.tb_templates_folder = ''
    check('Empty preference restores the default folder', tb.template_libraries_directory() == r'P:\NH_Objects\TemplateLibs')

addon_utils.disable('NH_Blender', default_set=True)
check('Clean add-on unregister', not hasattr(bpy.types.Scene, 'cray_ie_settings'))
print('NH_TB_TEMPLATES_PASS', json.dumps(dict(blender=bpy.app.version_string,
      addon_version=addon.bl_info['version'], addon_file=addon.__file__, checks=len(checks), passed=checks)))
