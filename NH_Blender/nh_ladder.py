"""DayZ ladder Memory selections, based on the supplied laddernest sample."""
import json
import math
import re

import bpy
import bmesh
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Vector

from .nh_base import _UI_PANEL_DEFAULT_ORDER


def _mesh_poll(self, obj):
    return obj.type == 'MESH'


def _reset_anchor(slot):
    def update(self, context):
        setattr(self, slot + '_vertices', '')
    return update


EXIT_ITEMS = (('FRONT', 'Front', 'Exit forward over the top'),
              ('LEFT', 'Left', 'Exit sideways to the left'),
              ('RIGHT', 'Right', 'Exit sideways to the right'),
              ('BOTH', 'Left + Right', 'Allow both sideways exits'))


class CRAY_PG_LadderSettings(PropertyGroup):
    ladder_type: EnumProperty(name='Ladder Type', default='NORMAL', items=(
        ('NORMAL', 'Standard', 'Bottom entry and top exit'),
        ('MIDDLE', 'Middle Exit', 'Bottom entry, intermediate side exit and top exit')))
    bottom: PointerProperty(name='Bottom Step', type=bpy.types.Object, poll=_mesh_poll,
                            description='Bottom entry step; pick a mesh or capture selected vertices', update=_reset_anchor('bottom'))
    top: PointerProperty(name='Top Step', type=bpy.types.Object, poll=_mesh_poll,
                         description='Top exit step at the landing level; for a side exit the hand-height point is added above this level', update=_reset_anchor('top'))
    middle: PointerProperty(name='Middle Step', type=bpy.types.Object, poll=_mesh_poll,
                            description='Optional intermediate landing step, between the bottom and top', update=_reset_anchor('middle'))
    bottom_vertices: StringProperty(options={'HIDDEN'})
    top_vertices: StringProperty(options={'HIDDEN'})
    middle_vertices: StringProperty(options={'HIDDEN'})
    memory: PointerProperty(name='Memory', type=bpy.types.Object, poll=_mesh_poll,
                            description='Optional existing Memory LOD; otherwise find or create it in the step model')
    view_geometry: PointerProperty(name='View Geometry', type=bpy.types.Object, poll=_mesh_poll,
                                   description='Optional View Geometry LOD in the same model; otherwise find or create it automatically')
    ladder_id: IntProperty(name='Ladder ID', default=0, min=0,
                           description='0 chooses a number unused in Memory and View Geometry; the created number is retained for updates. Use + for another ladder')
    top_exit: EnumProperty(name='Top Exit', items=EXIT_ITEMS, default='FRONT')
    middle_exit: EnumProperty(name='Middle Exit Side', items=EXIT_ITEMS[1:], default='BOTH')
    front_axis: EnumProperty(name='Approach Side', default='AUTO', items=(
        ('AUTO', 'Auto from Step', 'Infer a horizontal normal from the width of the bottom step; flip if needed'),
        ('X', '+X', 'Approach from world +X'), ('NX', '-X', 'Approach from world -X'),
        ('Y', '+Y', 'Approach from world +Y'), ('NY', '-Y', 'Approach from world -Y')))
    flip_front: BoolProperty(name='Flip Side', default=False, description='Reverse the approach side of the ladder')
    bottom_offset: FloatProperty(name='Bottom Height', default=0, unit='LENGTH',
                                 description='Vertical offset from the bottom step centre to the entry/floor level')
    top_offset: FloatProperty(name='Top Height', default=0, unit='LENGTH',
                              description='Vertical offset from the top step centre to the landing level')
    middle_offset: FloatProperty(name='Middle Height', default=0, unit='LENGTH',
                                 description='Vertical offset from the middle step centre to the intermediate landing level')
    direction_distance: FloatProperty(name='Direction Distance', default=.5, min=.01, unit='LENGTH',
                                      description='Distance from each floor-level connection to its approach-direction point')
    bottom_action_height: FloatProperty(name='Bottom Action Height', default=1.0, min=0, unit='LENGTH',
                                       description='Height of the bottom ladder interaction widget above the entry')
    top_action_height: FloatProperty(name='Top Action Height', default=.65, min=0, unit='LENGTH',
                                    description='Height of the top ladder interaction widget above the landing')
    middle_action_height: FloatProperty(name='Middle Action Height', default=1.0, min=0, unit='LENGTH',
                                       description='Height of the intermediate ladder interaction widget above its landing')
    side_exit_height: FloatProperty(name='Side Exit Height', default=1.6, min=.01, unit='LENGTH',
                                   description='Height above the top landing for side-exit points; the sample uses about five 0.32 m steps')
    box_depth: FloatProperty(name='Box Depth', default=.3, min=.01, unit='LENGTH',
                            description='Minimum depth of the View Geometry ladder box; grows to contain inclined steps')
    box_margin: FloatProperty(name='Box Margin', default=.05, min=0, unit='LENGTH',
                             description='Padding around step meshes and ladder action/exit points in View Geometry')
    show_offsets: BoolProperty(name='Offsets', default=False)


def _points(obj, indices='', context=None):
    if obj is None or obj.type != 'MESH':
        raise ValueError('Choose the step mesh first')
    if indices:
        ids = json.loads(indices)
        if obj.mode == 'EDIT':
            verts = bmesh.from_edit_mesh(obj.data).verts
            verts.ensure_lookup_table()
        else:
            verts = obj.data.vertices
        if not ids or max(ids) >= len(verts) or min(ids) < 0:
            raise ValueError('The captured step changed topology; capture its vertices again')
        return [obj.matrix_world @ verts[i].co for i in ids]
    if obj.mode == 'EDIT':
        return [obj.matrix_world @ v.co for v in bmesh.from_edit_mesh(obj.data).verts]
    evaluated = obj.evaluated_get((context or bpy.context).evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return [obj.matrix_world @ v.co for v in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def _centre(points):
    if not points:
        raise ValueError('The step mesh has no vertices')
    return Vector([(min(p[i] for p in points) + max(p[i] for p in points)) * .5 for i in range(3)])


def _anchor(settings, slot, context):
    return _points(getattr(settings, slot), getattr(settings, slot + '_vertices'), context)


def _front(points, settings):
    axes = {'X': (1, 0, 0), 'NX': (-1, 0, 0), 'Y': (0, 1, 0), 'NY': (0, -1, 0)}
    if settings.front_axis in axes:
        direction = Vector(axes[settings.front_axis])
    else:
        centre = sum(points, Vector()) / len(points)
        xx = sum((p.x-centre.x)**2 for p in points)
        yy = sum((p.y-centre.y)**2 for p in points)
        xy = sum((p.x-centre.x)*(p.y-centre.y) for p in points)
        if xx + yy < 1e-12 or math.hypot(xx-yy, 2*xy) < (xx+yy)*.05:
            raise ValueError('The step width is ambiguous; choose an Approach Side axis')
        angle = .5 * math.atan2(2*xy, xx-yy)
        direction = Vector((math.sin(angle), -math.cos(angle), 0))
    return -direction if settings.flip_front else direction


def build_points(context, settings, number):
    """One physical vertex may belong to several selections, as in laddernest."""
    bottom_points = _anchor(settings, 'bottom', context)
    bottom, top = _centre(bottom_points), _centre(_anchor(settings, 'top', context))
    bottom.z += settings.bottom_offset
    top.z += settings.top_offset
    if top.z - bottom.z < .05:
        raise ValueError('Top Step must be above Bottom Step')
    front = _front(bottom_points, settings)
    prefix = f'ladder{number}'
    points = []
    up = Vector((0, 0, 1))

    def add(point, *suffixes):
        points.append((point, tuple(prefix + suffix for suffix in suffixes)))

    add(bottom, '_con', '_bottom_front')
    add(bottom + front * settings.direction_distance, '_con_dir', '_dir')
    add(bottom + up * settings.bottom_action_height, '')
    if settings.top_exit == 'FRONT':
        add(top, '_con', '_top_front')
        top_direction = -front
    else:
        add(top, '_con')
        suffixes = tuple('_top_' + side for side in ('left', 'right')
                         if settings.top_exit in (side.upper(), 'BOTH'))
        add(top + up * settings.side_exit_height, *suffixes)
        top_direction = front
    add(top + top_direction * settings.direction_distance, '_con_dir')
    add(top + up * settings.top_action_height, '')
    if settings.ladder_type == 'MIDDLE':
        middle = _centre(_anchor(settings, 'middle', context))
        middle.z += settings.middle_offset
        if not bottom.z < middle.z < top.z:
            raise ValueError('Middle Step must lie between the bottom and top landing heights')
        suffixes = tuple('_middle_' + side for side in ('left', 'right')
                         if settings.middle_exit in (side.upper(), 'BOTH'))
        add(middle, '_con', *suffixes)
        add(middle + front * settings.direction_distance, '_con_dir')
        add(middle + up * settings.middle_action_height, '')
    return points


def _selected_parts(context):
    parts = []
    if context.mode == 'EDIT_MESH':
        for obj in context.objects_in_mode:
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            bm.verts.index_update()
            remaining = {v for v in bm.verts if v.select and not v.hide}
            while remaining:
                stack = [remaining.pop()]
                component = []
                while stack:
                    vert = stack.pop()
                    component.append(vert.index)
                    for edge in vert.link_edges:
                        other = edge.other_vert(vert)
                        if other in remaining:
                            remaining.remove(other)
                            stack.append(other)
                parts.append((obj, json.dumps(sorted(component))))
    else:
        parts = [(obj, '') for obj in context.selected_objects if obj.type == 'MESH']
    return parts


class CRAY_OT_LadderCapture(Operator):
    bl_idname = 'cray.ladder_capture'
    bl_label = 'Use Selected Steps'
    bl_description = 'Capture two selected step meshes, or two disconnected selected vertex sets in Edit Mode; assign the lower and upper automatically'
    bl_options = {'UNDO'}
    slot: EnumProperty(items=(('PAIR', 'Two Steps', ''), ('bottom', 'Bottom', ''),
                              ('top', 'Top', ''), ('middle', 'Middle', '')), default='PAIR')

    @classmethod
    def description(cls, context, properties):
        if properties.slot == 'PAIR':
            return cls.bl_description
        return 'Use the active mesh or its selected vertices as the ' + properties.slot + ' step'

    def execute(self, context):
        settings = context.scene.cray_ladder_settings
        try:
            if self.slot == 'PAIR':
                parts = _selected_parts(context)
                if len(parts) != 2:
                    raise ValueError('Select exactly two step meshes or two disconnected vertex sets')
                parts.sort(key=lambda part: _centre(_points(*part, context)).z)
                assignments = list(zip(('bottom', 'top'), parts))
            else:
                obj = context.edit_object or context.active_object
                if obj is None or obj.type != 'MESH':
                    raise ValueError('Select a step mesh')
                indices = ''
                if obj.mode == 'EDIT':
                    bm = bmesh.from_edit_mesh(obj.data)
                    bm.verts.index_update()
                    ids = [v.index for v in bm.verts if v.select and not v.hide]
                    if not ids:
                        raise ValueError('Select the step vertices')
                    indices = json.dumps(ids)
                assignments = [(self.slot, (obj, indices))]
            for slot, (obj, indices) in assignments:
                setattr(settings, slot, obj)
                setattr(settings, slot + '_vertices', indices)
            return {'FINISHED'}
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


def next_id(*objects):
    used = {int(m.group(1)) for obj in objects if obj is not None for g in obj.vertex_groups
            if (m := re.match(r'^ladder(\d+)(?:_|$)', g.name, re.I))}
    number = 1
    while number in used:
        number += 1
    return number


def _belongs(name, number):
    return bool(re.match(r'^ladder' + str(number) + r'(?:_|$)', name, re.I))


def _install_staged(target, staged):
    original = target.data
    names = [(g.name, g.lock_weight) for g in target.vertex_groups]
    empty = bpy.data.meshes.new('Ladder group transfer')
    target.data = empty
    try:
        target.vertex_groups.clear()
        for group in staged.vertex_groups:
            target.vertex_groups.new(name=group.name).lock_weight = group.lock_weight
        target.data = staged.data
    except Exception:
        target.vertex_groups.clear()
        for name, lock_weight in names:
            target.vertex_groups.new(name=name).lock_weight = lock_weight
        target.data = original
        raise
    finally:
        bpy.data.meshes.remove(empty)


def build_box(context, settings, points):
    samples = []
    for slot in ('bottom', 'top', 'middle') if settings.ladder_type == 'MIDDLE' else ('bottom', 'top'):
        samples.extend(_anchor(settings, slot, context))
    # Direction markers describe interaction cones, not the ladder volume.
    samples.extend(point for point, names in points if not any(name.endswith('_dir') for name in names))
    front = _front(_anchor(settings, 'bottom', context), settings)
    axes = (Vector((front.y, -front.x, 0)), front, Vector((0, 0, 1)))
    bounds = [[min(p.dot(axis) for p in samples) - settings.box_margin,
               max(p.dot(axis) for p in samples) + settings.box_margin] for axis in axes]
    centre = sum(bounds[1]) * .5
    half_depth = max((bounds[1][1] - bounds[1][0]) * .5, settings.box_depth * .5)
    bounds[1] = [centre - half_depth, centre + half_depth]
    if any(hi-lo < 1e-6 for lo, hi in bounds):
        raise ValueError('Ladder box has zero size; increase Box Margin')
    vertices = [sum((axes[i] * bounds[i][bit] for i, bit in enumerate(bits)), Vector())
                for bits in ((0,0,0), (1,0,0), (1,1,0), (0,1,0),
                             (0,0,1), (1,0,1), (1,1,1), (0,1,1))]
    faces = ((0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7))
    return vertices, faces


def _write_box(target, number, geometry):
    original = target.data
    staged = target.copy()
    staged.data = original.copy()
    mesh = staged.data
    committed = False
    prefix = f'ladder{number}'
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        deform = bm.verts.layers.deform.verify()
        previous = next((g for g in staged.vertex_groups if g.name.lower() == prefix), None)
        removed_components = []
        if previous is not None:
            doomed = {v for v in bm.verts if v[deform].get(previous.index, 0) > 0}
            other_ladders = {g.index for g in staged.vertex_groups
                             if re.match(r'^ladder\d+$', g.name, re.I) and g != previous}
            if any({i for i, weight in v[deform].items() if weight > 0} & other_ladders for v in doomed):
                raise ValueError('The ladder box shares vertices with another ladder; separate the components first')
            if any(any(v not in doomed for v in face.verts) for vert in doomed for face in vert.link_faces):
                raise ValueError('The ladder selection is joined to other geometry; separate the component first')
            for group in staged.vertex_groups:
                if re.match(r'^Component\d+$', group.name, re.I):
                    members = {v for v in bm.verts if v[deform].get(group.index, 0) > 0}
                    if members and members <= doomed:
                        removed_components.append(group.name)
            bmesh.ops.delete(bm, geom=list(doomed), context='VERTS')
            bm.to_mesh(mesh)
            for name in [previous.name, *removed_components]:
                staged.vertex_groups.remove(staged.vertex_groups[name])
            bm.clear()
            bm.from_mesh(mesh)
        inverse = target.matrix_world.inverted()
        verts, faces = geometry
        additions = [bm.verts.new(inverse @ point) for point in verts]
        new_faces = [bm.faces.new([additions[i] for i in face]) for face in faces]
        bmesh.ops.recalc_face_normals(bm, faces=new_faces)
        bm.verts.index_update()
        indices = [v.index for v in additions]
        bm.to_mesh(mesh)
        staged.vertex_groups.new(name=prefix).add(indices, 1.0, 'REPLACE')
        component = 1
        existing_names = {g.name.lower() for g in staged.vertex_groups}
        while f'component{component:02}' in existing_names:
            component += 1
        staged.vertex_groups.new(name=f'Component{component:02}').add(indices, 1.0, 'REPLACE')
        mesh.update()
        _install_staged(target, staged)
        committed = True
    finally:
        bm.free()
        bpy.data.objects.remove(staged, do_unlink=True)
        if not committed and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    if original.users == 0:
        bpy.data.meshes.remove(original)


def _write_points(memory, number, points):
    """Stage on a private object/mesh; preserve all unrelated selections and users."""
    original_mesh = memory.data
    staged = memory.copy()
    staged.data = original_mesh.copy()
    staged_mesh = staged.data
    committed = False
    try:
        doomed = {g.index for g in staged.vertex_groups if _belongs(g.name, number)}
        bm = bmesh.new()
        try:
            bm.from_mesh(staged_mesh)
            deform = bm.verts.layers.deform.active
            if deform is not None:
                remove = []
                for vert in bm.verts:
                    memberships = {i for i, weight in vert[deform].items() if weight > 0}
                    if memberships & doomed and not memberships - doomed and not vert.link_edges:
                        remove.append(vert)
                bmesh.ops.delete(bm, geom=remove, context='VERTS')
            bm.to_mesh(staged_mesh)
        finally:
            bm.free()
        for index in sorted(doomed, reverse=True):
            staged.vertex_groups.remove(staged.vertex_groups[index])
        inverse = memory.matrix_world.inverted()
        start = len(staged_mesh.vertices)
        staged_mesh.vertices.add(len(points))
        for offset, (world, names) in enumerate(points):
            staged_mesh.vertices[start + offset].co = inverse @ world
            for name in names:
                group = staged.vertex_groups.get(name) or staged.vertex_groups.new(name=name)
                group.add([start + offset], 1.0, 'REPLACE')
        staged_mesh.update()
        _install_staged(memory, staged)
        committed = True
    finally:
        bpy.data.objects.remove(staged, do_unlink=True)
        if not committed and staged_mesh.users == 0:
            bpy.data.meshes.remove(staged_mesh)
    if original_mesh.users == 0:
        bpy.data.meshes.remove(original_mesh)


class CRAY_OT_CreateLadderMemory(Operator):
    bl_idname = 'cray.create_ladder_memory'
    bl_label = 'Create / Update Ladder'
    bl_description = 'Create or update Memory points and a View Geometry box named ladderN; missing LODs are created automatically. Reusing an ID replaces that ladder only'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .nh_snap import _ensure_memory_lod_for_snap_target, _is_memory_lod_mesh_object
        from .nh_collider import _ensure_collider_lod_object, _is_existing_collider_target_for_lod
        from .nh_textures import _find_p3d_root_collection_for_object
        settings = context.scene.cray_ladder_settings
        restore_edit = context.mode == 'EDIT_MESH'
        objects_before = set(bpy.data.objects)
        collections_before = set(bpy.data.collections)
        memory = None
        backups = []
        try:
            # Complete geometry validation before creating any LOD.
            preliminary = build_points(context, settings, settings.ladder_id or 1)
            box = build_box(context, settings, preliminary)
            roots = {_find_p3d_root_collection_for_object(context, obj)
                     for obj in (settings.bottom, settings.top, settings.middle if settings.ladder_type == 'MIDDLE' else None)
                     if obj is not None}
            if len(roots) > 1:
                raise ValueError('All steps must belong to the same model')
            memory = settings.memory
            if memory is not None:
                if not _is_memory_lod_mesh_object(memory):
                    raise ValueError('The selected Memory object is not a Memory LOD')
                if memory in (settings.bottom, settings.top, settings.middle):
                    raise ValueError('Memory must be separate from the step meshes')
                memory_root = _find_p3d_root_collection_for_object(context, memory)
                if roots and memory_root not in roots:
                    raise ValueError('Memory must belong to the same model as the steps')
            view = settings.view_geometry
            if view is not None:
                if not _is_existing_collider_target_for_lod(view, '14'):
                    raise ValueError('Choose a View Geometry LOD')
                if view in (memory, settings.bottom, settings.top, settings.middle):
                    raise ValueError('View Geometry must be separate from Memory and the step meshes')
                if roots and _find_p3d_root_collection_for_object(context, view) not in roots:
                    raise ValueError('View Geometry must belong to the same model as the steps')
            if restore_edit:
                bpy.ops.object.mode_set(mode='OBJECT')
            if memory is None:
                memory = _ensure_memory_lod_for_snap_target(context, settings.bottom)
            if view is None:
                view = _ensure_collider_lod_object(context, settings.bottom, '14', exclude_obj=settings.bottom)
            if view in (memory, settings.bottom, settings.top, settings.middle):
                raise ValueError('View Geometry must be separate from Memory and the step meshes')
            number = settings.ladder_id or next_id(memory, view)
            points = build_points(context, settings, number)
            backups = [(obj, obj.copy()) for obj in (memory, view)]
            _write_points(memory, number, points)
            _write_box(view, number, box)
            settings.memory = memory
            settings.view_geometry = view
            settings.ladder_id = number
        except Exception as exc:
            for obj, backup in backups:
                changed_mesh = obj.data
                _install_staged(obj, backup)
                if changed_mesh.users == 0:
                    bpy.data.meshes.remove(changed_mesh)
            # A failed first creation must not leave an empty Memory LOD behind.
            for obj in set(bpy.data.objects) - objects_before - {backup for _, backup in backups}:
                if obj.type == 'MESH':
                    mesh = obj.data
                    bpy.data.objects.remove(obj, do_unlink=True)
                    if mesh.users == 0:
                        bpy.data.meshes.remove(mesh)
            for collection in set(bpy.data.collections) - collections_before:
                if not collection.objects and not collection.children:
                    bpy.data.collections.remove(collection)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            for _, backup in backups:
                mesh = backup.data
                bpy.data.objects.remove(backup, do_unlink=True)
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            if restore_edit and context.active_object is not None and context.mode != 'EDIT_MESH':
                bpy.ops.object.mode_set(mode='EDIT')
        self.report({'INFO'}, f'ladder{number}: {len(points)} Memory points and View Geometry box')
        return {'FINISHED'}


class CRAY_OT_NewLadder(Operator):
    bl_idname = 'cray.new_ladder'
    bl_label = 'New Ladder'
    bl_description = 'Use the next free ladder ID on the next creation; keep the current settings'
    bl_options = {'UNDO'}

    def execute(self, context):
        context.scene.cray_ladder_settings.ladder_id = 0
        return {'FINISHED'}


class CRAY_PT_LadderPointsPanel(Panel):
    bl_idname = 'VIEW3D_PT_cray_ladder_points'
    bl_label = 'Ladder Points (Memory LOD)'
    bl_category = 'NH Plugin'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_order = _UI_PANEL_DEFAULT_ORDER['ladder_points']
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        from .nh_scatter import _is_ui_panel_visible
        return _is_ui_panel_visible(context, 'ladder_points')

    def draw(self, context):
        s = context.scene.cray_ladder_settings
        layout = self.layout
        layout.prop(s, 'ladder_type', expand=True)
        layout.operator('cray.ladder_capture', text='Use Two Selected Steps', icon='RESTRICT_SELECT_OFF')
        for slot in ('bottom', 'top'):
            row = layout.row(align=True)
            row.prop(s, slot)
            row.operator('cray.ladder_capture', text='', icon='RESTRICT_SELECT_OFF').slot = slot
        if s.ladder_type == 'MIDDLE':
            row = layout.row(align=True)
            row.prop(s, 'middle')
            row.operator('cray.ladder_capture', text='', icon='RESTRICT_SELECT_OFF').slot = 'middle'
        layout.prop(s, 'memory')
        layout.prop(s, 'view_geometry')
        row = layout.row(align=True)
        row.prop(s, 'ladder_id')
        row.operator('cray.new_ladder', text='', icon='ADD')
        row = layout.row(align=True)
        row.prop(s, 'front_axis')
        row.prop(s, 'flip_front', text='', icon='ARROW_LEFTRIGHT')
        layout.label(text='Top Exit')
        row = layout.row(align=True)
        for token, label in (('FRONT', 'Front'), ('LEFT', 'Left'), ('RIGHT', 'Right'), ('BOTH', 'Both')):
            row.prop_enum(s, 'top_exit', token, text=label)
        if s.ladder_type == 'MIDDLE':
            layout.label(text='Middle Exit')
            row = layout.row(align=True)
            for token, label in (('LEFT', 'Left'), ('RIGHT', 'Right'), ('BOTH', 'Both')):
                row.prop_enum(s, 'middle_exit', token, text=label)
        layout.prop(s, 'show_offsets', icon='TRIA_DOWN' if s.show_offsets else 'TRIA_RIGHT', emboss=False)
        if s.show_offsets:
            col = layout.column(align=True)
            for name in ('bottom_offset', 'top_offset', 'direction_distance', 'bottom_action_height', 'top_action_height', 'box_depth', 'box_margin'):
                col.prop(s, name)
            if s.top_exit != 'FRONT':
                col.prop(s, 'side_exit_height')
            if s.ladder_type == 'MIDDLE':
                col.prop(s, 'middle_offset')
                col.prop(s, 'middle_action_height')
        layout.operator('cray.create_ladder_memory', icon='OUTLINER_DATA_MESH')
