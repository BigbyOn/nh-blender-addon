"""Terrain Builder placements using the active NH P3D backend."""
import io
import importlib
import sys
import math
from pathlib import Path
from types import SimpleNamespace

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ImportHelper
from mathutils import Euler, Matrix, Vector

from .utilities.tb_txt import ImportProblem, bounds_center, has_autocenter_zero, read_placements, resolve_models


def dependency():
    from .nh_snap import _ensure_p3d_bundle_registered, _P3D_BUNDLE_REGISTRY

    _ensure_p3d_bundle_registered()
    if _P3D_BUNDLE_REGISTRY['registered']:
        backend = 'NH_bundle'
    else:
        candidates = [name for name in bpy.context.preferences.addons.keys()
                      if name.rsplit('.', 1)[-1] == 'Arma3ObjectBuilder']
        candidates.extend(('bl_ext.user_default.Arma3ObjectBuilder', 'Arma3ObjectBuilder'))
        backend = next((name for name in candidates if name in sys.modules), None)
    if not backend or not hasattr(bpy.types.Object, 'a3ob_properties_object'):
        raise ImportProblem('P3D backend is unavailable; re-enable NH Plugin and check its P3D tools')
    return (importlib.import_module(backend + '.io.data_p3d'),
            importlib.import_module(backend + '.io.import_p3d'))


def placement_matrix(record, center, origin):
    rotation = Euler(tuple(math.radians(a) for a in (record.pitch, record.roll, -record.yaw)), 'ZXY').to_matrix()
    linear = rotation @ Matrix.Diagonal(Vector((record.scale,) * 3))
    matrix = linear.to_4x4()
    position = (record.east-origin[0], record.north-origin[1], record.elevation-origin[2])
    matrix.translation = tuple(position[i] - sum(float(linear[i][j]) * center[j] for j in range(3)) for i in range(3))
    return matrix


def preflight(operator):
    p3d, importer = dependency()
    records = read_placements(bpy.path.abspath(operator.filepath))
    models = resolve_models(records, bpy.path.abspath(operator.models_directory), operator.recursive_search)
    plans = {}
    for path in dict.fromkeys(models.values()):
        try:
            with path.open('rb') as handle:
                if handle.read(4) == b'ODOL':
                    raise ImportProblem(f'{path.name}: binarized ODOL is not supported; use the source MLOD P3D')
            mlod = p3d.P3D_MLOD.read_file(str(path))
            visual = next((lod for lod in mlod.lods if lod.resolution.lod == 0), None)
            if visual is None:
                raise ImportProblem(f'{path.name}: no visual LOD')
            center = bounds_center(mlod)
            if operator.centering == 'ORIGIN' or (operator.centering == 'AUTO_XY' and has_autocenter_zero(mlod)):
                anchor = (0.0, 0.0, 0.0)
            elif operator.centering in ('AUTO_XY', 'BOUNDS_XY'):
                anchor = (center[0], center[1], 0.0)
            else:
                anchor = center
            single = p3d.P3D_MLOD()
            single.lods = [visual]
            stream = io.BytesIO()
            single.write(stream)
            plans[path] = (stream.getvalue(), anchor)
        except ImportProblem:
            raise
        except Exception as error:
            raise ImportProblem(f'{path.name}: {error}') from error
    return records, models, plans, importer


def _snapshot():
    return {name: set(getattr(bpy.data, name)) for name in ('objects', 'collections', 'meshes', 'materials', 'images')}


def _rollback(before):
    for obj in set(bpy.data.objects)-before['objects']:
        bpy.data.objects.remove(obj, do_unlink=True)
    for col in set(bpy.data.collections)-before['collections']:
        bpy.data.collections.remove(col)
    for name in ('meshes', 'materials', 'images'):
        data = getattr(bpy.data, name)
        for block in set(data)-before[name]:
            if block.users == 0:
                data.remove(block)


def _move_to_collection(objects, target):
    for obj in objects:
        for collection in tuple(obj.users_collection):
            collection.objects.unlink(obj)
        target.objects.link(obj)


def _clone_tree(root, target, linked):
    originals = [root, *root.children_recursive]
    copies = {}
    for original in originals:
        copied = original.copy()
        if copied.data is not None and not linked:
            copied.data = copied.data.copy()
        target.objects.link(copied)
        copies[original] = copied
    for original, copied in copies.items():
        if original.parent in copies:
            copied.parent = copies[original.parent]
            copied.matrix_parent_inverse = original.matrix_parent_inverse.copy()
            copied.matrix_basis = original.matrix_basis.copy()
    return copies[root]


def import_layout(operator, context):
    if context.mode != 'OBJECT':
        raise ImportProblem('Switch to Object Mode before importing')
    from .nh_snap import _suppress_p3d_import_tracking

    records, paths, plans, importer = preflight(operator)
    origin = (operator.origin_east, operator.origin_north, operator.origin_height)
    if operator.origin_mode == 'FIRST':
        origin = (records[0].east, records[0].north, operator.origin_height)
    before = _snapshot()
    old_selection = list(context.selected_objects)
    old_active = context.view_layer.objects.active
    imported = []
    templates = {}
    named_templates = set()
    try:
        collection = bpy.data.collections.new('TB: ' + Path(operator.filepath).stem)
        context.scene.collection.children.link(collection)
        collection['tb_txt_source'] = bpy.path.abspath(operator.filepath)
        collection['tb_origin'] = list(origin)
        for record in records:
            path = paths[record.model]
            data, anchor = plans[path]
            if path in templates:
                obj = _clone_tree(templates[path], collection, operator.linked_copies)
            else:
                settings = SimpleNamespace(
                    filepath=str(path), first_lod_only=True, absolute_paths=True,
                    enclose=False, groupby='NONE', additional_data_allowed=True,
                    additional_data={'NORMALS', 'PROPS', 'SELECTIONS', 'UV', 'MATERIALS'},
                    validate_meshes=False, proxy_action='SEPARATE', translate_selections=False,
                    cleanup_empty_selections=False, sections='PRESERVE', load_textures=operator.load_textures,
                )
                # A TXT assembly is not a batch of editable source P3Ds: avoid
                # planner queue/material side effects, especially on rollback.
                with _suppress_p3d_import_tracking():
                    created = importer.read_file(settings, context, io.BytesIO(data))
                if len(created) != 1:
                    raise ImportProblem(f'{path.name}: expected one visual LOD')
                obj = created[0]
                _move_to_collection([obj, *obj.children_recursive], collection)
                templates[path] = obj
            if path not in named_templates:
                obj.name = path.stem
                named_templates.add(path)
            obj.rotation_mode = 'ZXY'
            obj.matrix_world = placement_matrix(record, anchor, origin)
            obj['tb_source_p3d'] = str(path)
            obj['tb_txt_line'] = record.line
            obj['tb_model_name'] = record.model
            obj['tb_position'] = [record.east, record.north, record.elevation]
            obj['tb_angles_ypr'] = [record.yaw, record.pitch, record.roll]
            obj['tb_uniform_scale'] = record.scale
            obj['tb_anchor'] = list(anchor)
            imported.append(obj)
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in imported:
            obj.select_set(True)
        context.view_layer.objects.active = imported[0]
        context.view_layer.update()
        return collection, imported
    except Exception:
        _rollback(before)
        for obj in old_selection:
            obj.select_set(True)
        context.view_layer.objects.active = old_active
        raise
    finally:
        context.window_manager.progress_end()


class CRAY_OT_ImportTerrainBuilderTXT(bpy.types.Operator, ImportHelper):
    bl_idname = 'cray.import_tb_txt'
    bl_label = 'Import Terrain Builder TXT'
    bl_options = {'REGISTER', 'UNDO', 'PRESET'}
    filename_ext = '.txt'
    filter_glob: StringProperty(default='*.txt;*.csv', options={'HIDDEN'})
    models_directory: StringProperty(name='P3D models folder', subtype='DIR_PATH', default='P:\\NH_Objects')
    recursive_search: BoolProperty(name='Search subfolders', default=True)
    origin_mode: EnumProperty(name='Coordinate origin', default='CUSTOM', items=(
        ('CUSTOM', 'Map origin', 'Subtract the map origin from TXT positions'),
        ('FIRST', 'First object (XY)', 'Place the first object anchor at X=0, Y=0'),
    ))
    origin_east: FloatProperty(name='Map easting', default=200000.0, precision=3)
    origin_north: FloatProperty(name='Map northing', default=0.0, precision=3)
    origin_height: FloatProperty(name='Height offset', default=0.0, precision=3)
    centering: EnumProperty(name='Model anchor', default='AUTO_XY', items=(
        ('AUTO_XY', 'Terrain Builder XY', 'Center from bounds of every LOD; preserve model origin when autocenter=0'),
        ('BOUNDS_XY', 'Bounds center XY (force)', 'Use the XY center of all LODs even if autocenter=0'),
        ('ORIGIN', 'Original P3D origin', 'Use raw P3D coordinates without centering'),
        ('BOUNDS_XYZ', 'Bounds center XYZ', 'Use when exported height refers to the 3D bounds center'),
    ))
    linked_copies: BoolProperty(name='Linked copies', description='Repeated models share mesh data', default=True)
    load_textures: BoolProperty(name='Load PAA textures', default=True)

    def invoke(self, context, event):
        settings = context.scene.cray_ie_settings
        if not self.properties.is_property_set('models_directory'):
            self.models_directory = settings.tb_models_directory or settings.quick_add_search_root
        if not self.properties.is_property_set('load_textures'):
            self.load_textures = settings.import_show_materials
        return ImportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'models_directory')
        layout.prop(self, 'recursive_search')
        layout.separator()
        layout.prop(self, 'origin_mode')
        column = layout.column()
        column.enabled = self.origin_mode == 'CUSTOM'
        column.prop(self, 'origin_east')
        column.prop(self, 'origin_north')
        layout.prop(self, 'origin_height')
        layout.prop(self, 'centering')
        layout.separator()
        layout.prop(self, 'linked_copies')
        layout.prop(self, 'load_textures')

    def execute(self, context):
        try:
            collection, imported = import_layout(self, context)
        except Exception as error:
            message = str(error)
            print('Terrain Builder TXT import failed:', message)
            self.report({'ERROR'}, message[:900])
            return {'CANCELLED'}
        context.scene.cray_ie_settings.tb_models_directory = self.models_directory
        self.report({'INFO'}, f'Imported {len(imported)} objects into {collection.name}')
        return {'FINISHED'}


def menu_import(self, context):
    self.layout.operator(CRAY_OT_ImportTerrainBuilderTXT.bl_idname, text='NH Terrain Builder (.txt)')

