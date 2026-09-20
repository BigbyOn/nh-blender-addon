"""Persistent pipe guides: edit one object, then replace it with convex boxes."""
import json
import math
import uuid
from contextlib import ExitStack

import bpy
from bpy.props import FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup
from mathutils import Matrix, Vector

_BUSY = set()
_FIELDS = ('outer_diameter', 'inner_diameter', 'depth', 'segments', 'phase')
_VALID = 'nh_pipe_valid_parameters'
_UID = 'nh_pipe_editor_id'


def is_pipe_editor(obj):
    return bool(obj and obj.type == 'MESH' and obj.get(_UID))


def parameters(obj):
    return {name: getattr(obj.nh_pipe_editor, name) for name in _FIELDS}


def pipe_mesh(values):
    outer = float(values['outer_diameter']) / 2
    inner = float(values['inner_diameter']) / 2
    depth = float(values['depth'])
    count = int(values['segments'])
    if not 0 < inner < outer:
        raise RuntimeError('Inner Diameter must be greater than 0 and smaller than Outer Diameter')
    if depth <= 0 or count < 4:
        raise RuntimeError('Depth must be positive; use at least 4 segments')
    vertices, faces = [], []
    for i in range(count):
        angle = values['phase'] + i * 2 * math.pi / count
        x, y = math.cos(angle), math.sin(angle)
        vertices.extend(((outer*x, outer*y, -depth/2), (outer*x, outer*y, depth/2),
                         (inner*x, inner*y, -depth/2), (inner*x, inner*y, depth/2)))
    for i in range(count):
        a, b = 4*i, 4*((i+1) % count)
        faces.extend(((a, b, b+1, a+1), (b+2, a+2, a+3, b+3),
                      (a+1, b+1, b+3, a+3), (b, a, a+2, b+2)))
    return vertices, faces


def set_parameters(obj, values):
    """Build first, then swap the mesh. Object, transforms and selection survive."""
    if obj.mode != 'OBJECT':
        raise RuntimeError('Leave Edit Mode to edit pipe dimensions')
    vertices, faces = pipe_mesh(values)
    mesh = bpy.data.meshes.new('NH Pipe Preview')
    try:
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    previous = obj.data
    key = obj.as_pointer()
    _BUSY.add(key)
    try:
        for name in _FIELDS:
            setattr(obj.nh_pipe_editor, name, values[name])
        obj.data = mesh
        obj[_VALID] = json.dumps(values)
        obj.nh_pipe_editor.error = ''
    finally:
        _BUSY.discard(key)
    if previous and previous.users == 0:
        bpy.data.meshes.remove(previous)


def _on_dimensions_changed(settings, context):
    obj = settings.id_data
    if not is_pipe_editor(obj) or obj.as_pointer() in _BUSY:
        return
    try:
        set_parameters(obj, parameters(obj))
    except Exception as exc:
        # An invalid edit must not leave parameters inconsistent with the mesh.
        previous = json.loads(obj.get(_VALID, '{}'))
        key = obj.as_pointer()
        _BUSY.add(key)
        try:
            for name, value in previous.items():
                if name in _FIELDS:
                    setattr(settings, name, value)
            settings.error = str(exc)
        finally:
            _BUSY.discard(key)


class CRAY_PG_PipeEditor(PropertyGroup):
    outer_diameter: FloatProperty(name='Outer Diameter', default=2., min=.0001, unit='LENGTH', precision=4, update=_on_dimensions_changed)
    inner_diameter: FloatProperty(name='Inner Diameter', default=1., min=.0001, unit='LENGTH', precision=4, update=_on_dimensions_changed)
    depth: FloatProperty(name='Depth', default=.25, min=.0001, unit='LENGTH', precision=4, update=_on_dimensions_changed)
    segments: IntProperty(name='Box Segments', default=16, min=4, max=128, update=_on_dimensions_changed)
    phase: FloatProperty(name='Segment Rotation', default=0., subtype='ANGLE', update=_on_dimensions_changed)
    source: PointerProperty(type=bpy.types.Object)
    target: PointerProperty(type=bpy.types.Object)
    error: StringProperty(options={'SKIP_SAVE'})


def _guide_collection(context):
    for collection in context.scene.collection.children:
        if collection.get('nh_pipe_editor_collection'):
            return collection
    collection = bpy.data.collections.new('NH Pipe Guides')
    collection['nh_pipe_editor_collection'] = True
    context.scene.collection.children.link(collection)
    return collection


def _activate(context, objects):
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.hide_set(False)
        obj.select_set(True)
    context.view_layer.objects.active = objects[-1]


def _fit(data, op):
    from .nh_round_geometry import aligned_data, profile_for_axis
    aligned, frame = aligned_data(data, op)
    world = data['matrix_world']
    axis = world.to_3x3() @ frame.to_3x3().col[2]
    points = [world @ p for p in data['local_points']]
    profile = profile_for_axis(points, axis)
    if profile is None:
        raise RuntimeError('Cannot fit pipe to this selection')
    transform = Matrix.Identity(4)
    for i, name in enumerate(('axis_a', 'axis_b', 'depth_axis')):
        for j in range(3):
            transform[j][i] = profile[name][j]
    transform.translation = profile['center']
    radius = max(profile['radius_a'], profile['radius_b'])
    radial = []
    for point in points:
        offset = point-profile['center']
        distance = math.hypot(offset.dot(profile['axis_a'])/profile['radius_a'],
                              offset.dot(profile['axis_b'])/profile['radius_b'])
        if distance > .05:
            radial.append(distance)
    radial.sort()
    factor = .5
    if len(radial) >= 8:
        cut = max(range(3, len(radial)-3), key=lambda i: radial[i]-radial[i-1])
        if radial[cut]-radial[cut-1] > .08:
            factor = sum(radial[:cut])/cut / (sum(radial[cut:])/len(radial[cut:]))
    return dict(matrix=[list(row) for row in transform], source=data['source_obj'].name,
                values=dict(outer_diameter=radius*2, inner_diameter=radius*2*min(factor,.98),
                            depth=max(profile['depth'],.001), segments=int(op.pipe_segments), phase=0.))


def create_or_update(context, op):
    from .nh_collider_exp import _collect_collider_exp_scope_input_data_exp
    settings = context.scene.cray_collider_exp_settings
    active = context.active_object
    if not op.source_snapshot:
        if is_pipe_editor(active):
            if any(o != active and o.get(_UID) == active[_UID] for o in bpy.data.objects):
                active[_UID] = uuid.uuid4().hex
            spec = dict(matrix=[list(row) for row in active.matrix_world],
                        source=active.nh_pipe_editor.source.name if active.nh_pipe_editor.source else '',
                        values=parameters(active), uid=active[_UID])
            specs = [spec]
        else:
            specs = [_fit(data, op) for data in _collect_collider_exp_scope_input_data_exp(context, settings, bounds_only=False)]
            for spec in specs:
                spec['uid'] = uuid.uuid4().hex
        op.source_snapshot = json.dumps(specs)
        # Populate F9 with real dimensions rather than multiplicative factors.
        for prop, field in (('outer_diameter','outer_diameter'), ('inner_diameter','inner_diameter'),
                            ('pipe_depth','depth'), ('pipe_segments','segments'), ('phase','phase')):
            if not op.properties.is_property_set(prop) or (prop in ('outer_diameter','inner_diameter','pipe_depth') and getattr(op,prop) == 0):
                setattr(op, prop, specs[0]['values'][field])
    specs = json.loads(op.source_snapshot)
    # Validate the entire operation before creating/changing any object.
    values = dict(outer_diameter=op.outer_diameter, inner_diameter=op.inner_diameter,
                  depth=op.pipe_depth, segments=op.pipe_segments, phase=op.phase)
    per_object = []
    for spec in specs:
        fitted = dict(values)
        # Multi-shell creation preserves each shell's fitted size. F9 changes
        # scale the corresponding dimension across the fitted pipes.
        for field in ('outer_diameter', 'inner_diameter', 'depth'):
            fitted[field] *= spec['values'][field] / specs[0]['values'][field]
        pipe_mesh(fitted)
        per_object.append(fitted)
    objects, created, backups = [], [], []
    try:
        for spec, values in zip(specs, per_object):
            obj = next((o for o in bpy.data.objects if o.get(_UID) == spec['uid']), None)
            if obj is None:
                obj = bpy.data.objects.new('NH Pipe', bpy.data.meshes.new('NH Pipe Preview'))
                _guide_collection(context).objects.link(obj)
                obj[_UID] = spec['uid']
                obj['nh_collider_exp_guide_type'] = 'PIPE'
                obj['nh_collider_exp_guide_source'] = spec['source']
                obj.matrix_world = Matrix(spec['matrix'])
                obj.nh_pipe_editor.source = bpy.data.objects.get(spec['source'])
                obj.nh_pipe_editor.target = settings.geometry_object
                obj.display_type = 'WIRE'
                obj.show_in_front = True
                obj.hide_render = True
                obj.color = (1., .35, .04, 1.)
                created.append(obj)
            else:
                backups.append((obj, parameters(obj)))
            if obj.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            set_parameters(obj, values)
            objects.append(obj)
    except Exception:
        for obj, previous in backups:
            set_parameters(obj, previous)
        for obj in created:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise
    _activate(context, objects)
    return objects


def boxes_from_guide(guide):
    """Enclose each edited ring sector in a rectangular 8-vertex box."""
    from .nh_collider_exp import _make_oriented_box_world_exp, _append_box_data_exp
    count = guide.nh_pipe_editor.segments
    if len(guide.data.vertices) != count*4:
        raise RuntimeError('Pipe topology changed; update its dimensions before converting')
    points = [guide.matrix_world @ v.co for v in guide.data.vertices]
    vertices, faces = [], []
    for i in range(count):
        a, b = i*4, ((i+1) % count)*4
        depth = (points[a+1]-points[a]).normalized()
        tangent = points[b]-points[a]
        radial = tangent.cross(depth).normalized()
        tangent = depth.cross(radial).normalized()
        if min(depth.length, tangent.length, radial.length) < .5:
            raise RuntimeError('Pipe has a collapsed scale or segment')
        sector = [points[k] for k in (a,a+1,a+2,a+3,b,b+1,b+2,b+3)]
        axes = (radial,tangent,depth)
        bounds = [(min(p.dot(axis) for p in sector), max(p.dot(axis) for p in sector)) for axis in axes]
        center = sum((axis*((low+high)/2) for axis,(low,high) in zip(axes,bounds)),Vector())
        box = _make_oriented_box_world_exp(Matrix.Identity(4),center,*axes,*(high-low for low,high in bounds))
        _append_box_data_exp(vertices,faces,box)
    return vertices, faces


def convert_selected(context, op):
    from . import nh_collider_exp as e
    from .nh_textures import _ensure_collider_placeholder_material
    settings = context.scene.cray_collider_exp_settings
    guides = [o for o in context.selected_objects if is_pipe_editor(o)]
    if not guides:
        raise RuntimeError('Select an editable Pipe; use Create Pipe first')
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    # Compute all boxes before touching any Geometry LOD.
    prepared = [(guide, *boxes_from_guide(guide)) for guide in guides]
    targets, snapshots, jobs = {}, {}, []
    try:
        for guide, vertices, faces in prepared:
            source = guide.nh_pipe_editor.source
            if source is None:
                raise RuntimeError('Pipe source was deleted; assign Source in the pipe editor')
            if guide.nh_pipe_editor.target is not None:
                settings.geometry_object = guide.nh_pipe_editor.target
            target = e._ensure_collider_exp_target_object_exp(context, settings, source, op=op)
            if target == guide:
                raise RuntimeError('Pipe preview cannot be its own target LOD')
            targets[target.as_pointer()] = target
            jobs.append((guide,source,target,vertices,faces))
        with ExitStack() as stack:
            for key,target in targets.items():
                snapshots[key] = {k:target[k] for k in target.keys() if k.startswith('nh_collider_exp_')}
                stack.enter_context(e._collider_exp_mesh_transaction_exp(target))
            for guide,source,target,vertices,faces in jobs:
                material_index, material_name = _ensure_collider_placeholder_material(target,source)
                stats = e._append_collider_exp_mesh_to_object_exp(target,vertices,faces,
                    merge_distance=0.,recalc_normals=True,material_index=material_index)
                e._set_collider_exp_custom_props_exp(target,'PIPE_BOXES',source,dict(
                    vertex_indices=stats['vertex_indices'],face_indices=stats['face_indices'],
                    material_name=material_name,
                    **parameters(guide)))
    except Exception:
        for key, snapshot in snapshots.items():
            target = targets[key]
            for name in list(target.keys()):
                if name.startswith('nh_collider_exp_'):
                    del target[name]
            for name,value in snapshot.items():
                target[name] = value
        raise
    count = sum(guide.nh_pipe_editor.segments for guide in guides)
    for guide in guides:
        e._remove_collider_exp_guide_after_conversion_exp(context,guide)
    _activate(context,list(targets.values()))
    return count


def draw_editor(layout, context):
    obj = context.active_object
    if not is_pipe_editor(obj):
        return
    box = layout.box()
    box.label(text='Pipe Editor: '+obj.name,icon='MESH_TORUS')
    box.label(text='Changes update this pipe')
    controls = box.column()
    controls.enabled = obj.mode == 'OBJECT'
    for field in _FIELDS:
        controls.prop(obj.nh_pipe_editor,field)
    if obj.mode != 'OBJECT':
        box.label(text='Leave Edit Mode to edit dimensions',icon='INFO')
    if obj.nh_pipe_editor.error:
        box.label(text=obj.nh_pipe_editor.error,icon='ERROR')
    controls.prop(obj.nh_pipe_editor,'source')
    controls.prop(obj.nh_pipe_editor,'target',text='Target LOD Object')
    op = controls.operator('cray.generate_pipe_boxes_collider_exp',text='Convert Pipe to Boxes',icon='MESH_CUBE')
    op.target_lod = context.scene.cray_collider_exp_settings.target_lod
