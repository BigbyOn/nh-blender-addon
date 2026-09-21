"""Blender integration checks for DayZ ladder Memory creation and replacement."""
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('NH_TEST_PACKAGE_ROOT', str(ROOT)))
os.environ['BLENDER_USER_CONFIG'] = str(ROOT / 'dist/ladder_test_config')
import bpy
import bmesh
from mathutils import Matrix, Vector
import NH_Blender as nh
from NH_Blender import nh_ladder as ladder

nh.register()


def mesh(name, centre, width=.6):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(x*width/2, y*.025, z*.025)
                      for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], [], [])
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = centre
    bpy.context.view_layer.update()
    return obj


def groups(obj):
    result = {g.name: [] for g in obj.vertex_groups}
    for v in obj.data.vertices:
        for membership in v.groups:
            if membership.weight > 0:
                result[obj.vertex_groups[membership.group].name].append(tuple(obj.matrix_world @ v.co))
    return result


def active(*objects):
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[-1] if objects else None
    bpy.context.view_layer.update()


class LadderTests(unittest.TestCase):
    def setUp(self):
        active()
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.context.scene.property_unset('cray_ladder_settings')
        self.s = bpy.context.scene.cray_ladder_settings
        self.bottom = mesh('Bottom', (2, 3, 1))
        self.top = mesh('Top', (2, 3, 5))
        self.s.bottom, self.s.top = self.bottom, self.top
        active(self.bottom, self.top)

    def create(self):
        self.assertEqual(bpy.ops.cray.create_ladder_memory(), {'FINISHED'})
        return self.s.memory

    def test_front_names_coordinates_and_shared_vertices(self):
        obj = self.create()
        g = groups(obj)
        self.assertEqual(set(g), {'ladder1', 'ladder1_dir', 'ladder1_con', 'ladder1_con_dir',
                                  'ladder1_top_front', 'ladder1_bottom_front'})
        self.assertEqual(len(obj.data.vertices), 6)
        self.assertEqual(g['ladder1_bottom_front'], [(2, 3, 1)])
        self.assertEqual(g['ladder1_top_front'], [(2, 3, 5)])
        self.assertEqual(g['ladder1_dir'], [(2, 2.5, 1)])
        self.assertEqual(g['ladder1_con_dir'], [(2, 2.5, 1), (2, 3.5, 5)])
        self.assertEqual(len(self.bottom.data.vertices), 8)
        self.assertEqual(obj.a3ob_properties_object.lod, '9')
        view = self.s.view_geometry
        self.assertEqual(view.a3ob_properties_object.lod, '14')
        self.assertEqual(len(view.data.vertices), 8)
        self.assertEqual(len(view.data.polygons), 6)
        self.assertEqual(len(groups(view)['ladder1']), 8)
        self.assertEqual(groups(view)['ladder1'], groups(view)['Component01'])
        bm = bmesh.new()
        bm.from_mesh(view.data)
        self.assertTrue(all(e.is_manifold for e in bm.edges))
        self.assertGreater(bm.calc_volume(signed=True), 0)
        bm.free()

    def test_all_exit_buttons_and_no_duplicate_updates(self):
        obj = self.create()
        for token, sides in [('LEFT', ('left',)), ('RIGHT', ('right',)), ('BOTH', ('left', 'right')),
                             ('FRONT', ('front',))]:
            self.s.top_exit = token
            self.assertEqual(self.create(), obj)
            g = groups(obj)
            self.assertEqual({name for name in g if name.startswith('ladder1_top_')},
                             {'ladder1_top_' + side for side in sides})
            self.assertEqual(len(obj.data.vertices), 6 if token == 'FRONT' else 7)
            self.assertEqual(len(self.s.view_geometry.data.vertices), 8)
            self.assertEqual(len(self.s.view_geometry.data.polygons), 6)
            if token != 'FRONT':
                self.assertAlmostEqual(g['ladder1_top_' + sides[0]][0][2], 6.6, places=5)
                self.assertEqual(g['ladder1_con_dir'][-1], (2, 2.5, 5))

    def test_middle_variants_and_removal(self):
        self.s.middle = mesh('Middle', (2, 3, 3))
        self.s.ladder_type = 'MIDDLE'
        for token in ('LEFT', 'RIGHT', 'BOTH'):
            self.s.middle_exit = token
            obj = self.create()
            g = groups(obj)
            suffixes = ('left', 'right') if token == 'BOTH' else (token.lower(),)
            self.assertEqual({name for name in g if '_middle_' in name},
                             {'ladder1_middle_' + side for side in suffixes})
            self.assertEqual(len(obj.data.vertices), 9)
            for side in suffixes:
                self.assertEqual(g['ladder1_middle_' + side], [(2, 3, 3)])
            self.assertEqual(len(g['ladder1_con']), 3)
            self.assertEqual(len(g['ladder1_con_dir']), 3)
        self.s.ladder_type = 'NORMAL'
        self.create()
        self.assertEqual(len(obj.data.vertices), 6)
        self.assertFalse(any('_middle_' in name for name in groups(obj)))

    def test_rotation_scale_incline_and_flip(self):
        transform = Matrix.Translation((7, -4, 2)) @ Matrix.Rotation(.73, 4, 'Z')
        for obj in (self.bottom, self.top):
            obj.matrix_world = transform @ obj.matrix_world
            obj.scale = (1.7, .6, 2)
        self.top.location.x += .2
        bpy.context.view_layer.update()
        obj = self.create()
        g = groups(obj)
        bottom = Vector(g['ladder1_bottom_front'][0])
        direction = Vector(g['ladder1_dir'][0]) - bottom
        self.assertAlmostEqual(direction.length, .5, places=5)
        self.assertAlmostEqual(direction.dot(Vector((math_sin(.73), -math_cos(.73), 0))), .5, places=5)
        self.s.flip_front = True
        self.create()
        reverse = Vector(groups(obj)['ladder1_dir'][0]) - bottom
        self.assertLess((direction + reverse).length, 1e-5)

    def test_preserve_other_ladder_shared_mesh_and_selections(self):
        obj = self.create()
        unrelated = obj.vertex_groups.new(name='Unrelated')
        unrelated.add([0], 1.0, 'REPLACE')
        before = groups(obj)
        linked = obj.copy()
        bpy.context.scene.collection.objects.link(linked)
        bpy.ops.cray.new_ladder()
        self.create()
        self.assertEqual(self.s.ladder_id, 2)
        self.assertEqual(groups(linked), before)
        self.s.ladder_id = 1
        self.s.top_exit = 'LEFT'
        self.create()
        self.assertEqual(groups(obj)['Unrelated'], before['Unrelated'])
        self.assertEqual(groups(obj)['ladder2_con'], before['ladder1_con'])
        self.assertEqual(groups(linked), before)

    def test_invalid_middle_leaves_memory_untouched(self):
        obj = self.create()
        before, original = groups(obj), obj.data
        self.s.ladder_type = 'MIDDLE'
        self.s.middle = mesh('Bad Middle', (2, 3, 7))
        with self.assertRaises(RuntimeError):
            bpy.ops.cray.create_ladder_memory()
        self.assertEqual(obj.data, original)
        self.assertEqual(groups(obj), before)

    def test_capture_objects_sorts_by_height(self):
        self.s.bottom = self.s.top = None
        self.assertEqual(bpy.ops.cray.ladder_capture(), {'FINISHED'})
        self.assertEqual(self.s.bottom, self.bottom)
        self.assertEqual(self.s.top, self.top)
        self.create()

    def test_capture_edit_islands_and_restore_edit_mode(self):
        data = bpy.data.meshes.new('Two steps')
        vertices = [(x, y, z) for z in (1, 5) for x, y in ((-.3, 0), (.3, 0), (.3, .05), (-.3, .05))]
        data.from_pydata(vertices, [], [(0, 1, 2, 3), (4, 5, 6, 7)])
        obj = bpy.data.objects.new('Joined Steps', data)
        bpy.context.scene.collection.objects.link(obj)
        active(obj)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.cray.ladder_capture()
        self.assertEqual(self.s.bottom, obj)
        self.assertEqual(self.s.top, obj)
        self.assertEqual(json.loads(self.s.bottom_vertices), [0, 1, 2, 3])
        self.create()
        self.assertEqual(bpy.context.mode, 'EDIT_MESH')
        self.assertEqual(len(bmesh.from_edit_mesh(obj.data).verts), 8)

    def test_staged_failure_keeps_original(self):
        obj = self.create()
        before, original = groups(obj), obj.data
        with self.assertRaises(Exception):
            ladder._write_points(obj, 1, [(None, ('ladder1',))])
        self.assertEqual(obj.data, original)
        self.assertEqual(groups(obj), before)

    def test_failed_first_creation_leaves_no_empty_lod(self):
        objects, collections = set(bpy.data.objects), set(bpy.data.collections)
        original = ladder._write_points
        def fail(*args):
            raise RuntimeError('Injected write failure')
        ladder._write_points = fail
        try:
            with self.assertRaises(RuntimeError):
                bpy.ops.cray.create_ladder_memory()
        finally:
            ladder._write_points = original
        self.assertEqual(set(bpy.data.objects), objects)
        self.assertEqual(set(bpy.data.collections), collections)

    def test_box_failure_rolls_back_both_lods(self):
        obj = self.create()
        view = self.s.view_geometry
        before = (groups(obj), groups(view), obj.data, view.data)
        original = ladder._write_box
        def fail(*args):
            original(*args)
            raise RuntimeError('Injected box failure after write')
        ladder._write_box = fail
        self.s.top_exit = 'BOTH'
        try:
            with self.assertRaises(RuntimeError):
                bpy.ops.cray.create_ladder_memory()
        finally:
            ladder._write_box = original
        self.assertEqual((groups(obj), groups(view), obj.data, view.data), before)

    def test_manual_ids_multiple_boxes_and_view_only_number(self):
        self.s.ladder_id = 7
        self.create()
        view = self.s.view_geometry
        first = groups(view)['ladder7']
        linked = view.copy()
        bpy.context.scene.collection.objects.link(linked)
        self.s.ladder_id = 12
        self.top.location.z += 2
        bpy.context.view_layer.update()
        self.create()
        self.assertEqual(len(view.data.vertices), 16)
        self.assertEqual(len(view.data.polygons), 12)
        self.assertEqual(groups(view)['ladder7'], first)
        self.assertEqual(groups(linked)['ladder7'], first)
        self.assertNotIn('ladder12', groups(linked))
        view.vertex_groups.new(name='ladder1')
        bpy.ops.cray.new_ladder()
        self.create()
        self.assertEqual(self.s.ladder_id, 2)

    def test_box_contains_transformed_steps_and_side_exit(self):
        transform = Matrix.Translation((3, 4, 0)) @ Matrix.Rotation(1.2, 4, 'Z')
        for obj in (self.bottom, self.top):
            obj.matrix_world = transform @ obj.matrix_world
        self.top.location.x += .35
        self.s.top_exit = 'LEFT'
        bpy.context.view_layer.update()
        self.create()
        view = self.s.view_geometry
        bm = bmesh.new()
        bm.from_mesh(view.data)
        bm.transform(view.matrix_world)
        bm.normal_update()
        points = ladder._points(self.bottom) + ladder._points(self.top)
        points += [Vector(p) for p in groups(self.s.memory)['ladder1_top_left']]
        self.assertTrue(all((p-face.verts[0].co).dot(face.normal) < 1e-5
                            for face in bm.faces for p in points))
        bm.free()

    def test_sample_group_structure_and_free_id(self):
        with bpy.data.libraries.load(str(ROOT / 'laddernest.blend'), link=False) as (source, target):
            target.objects = ['Memory', 'View Geometry']
        sample = target.objects[0]
        bpy.context.scene.collection.objects.link(sample)
        sample_view = target.objects[1]
        bpy.context.scene.collection.objects.link(sample_view)
        original_view_groups = groups(sample_view)
        original_view_vertices = len(sample_view.data.vertices)
        original_view_faces = len(sample_view.data.polygons)
        sample_groups = groups(sample)
        self.assertEqual(ladder.next_id(sample), 12)
        for number in range(1, 12):
            prefix = f'ladder{number}'
            self.assertEqual(len(sample_groups[prefix + '_dir']), 1)
            self.assertEqual(len(sample_groups[prefix + '_con']), 3 if number == 5 else 2)
            self.assertEqual(len(sample_groups[prefix + '_con_dir']), 3 if number == 5 else 2)
        self.s.top_exit = 'BOTH'
        p = ladder.build_points(bpy.context, self.s, 3)
        self.assertEqual({n for _, names in p for n in names},
                         {n for n in sample_groups if ladder._belongs(n, 3)})
        self.s.top_exit = 'FRONT'
        self.s.ladder_type = 'MIDDLE'
        self.s.middle = mesh('Middle', (2, 3, 3))
        p = ladder.build_points(bpy.context, self.s, 5)
        self.assertEqual({n for _, names in p for n in names},
                         {n for n in sample_groups if ladder._belongs(n, 5)})
        self.s.ladder_type = 'NORMAL'
        self.s.memory = sample
        self.s.view_geometry = sample_view
        self.s.ladder_id = 0
        self.create()
        self.assertEqual(self.s.ladder_id, 12)
        for name, points in sample_groups.items():
            self.assertEqual(groups(sample)[name], points)
        self.assertEqual(len(sample_view.data.vertices), original_view_vertices + 8)
        self.assertEqual(len(sample_view.data.polygons), original_view_faces + 6)
        for name, points in original_view_groups.items():
            self.assertEqual(groups(sample_view)[name], points)
        self.s.ladder_id = 5
        self.create()
        self.assertEqual(len(sample_view.data.vertices), original_view_vertices + 8)
        self.assertEqual(len(groups(sample_view)['ladder5']), 8)
        self.assertEqual(groups(sample_view)['ladder6'], original_view_groups['ladder6'])

    def test_panel_order_and_persistence(self):
        from NH_Blender.nh_scatter import _sorted_ui_panel_layout_definitions
        keys = [item[0] for item in _sorted_ui_panel_layout_definitions(bpy.context.scene.cray_ui_panel_settings)]
        self.assertEqual(keys[keys.index('snap_points') + 1], 'ladder_points')
        obj = self.create()
        filepath = ROOT / 'dist/ladder_persistence_validation.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(filepath))
        bpy.ops.wm.open_mainfile(filepath=str(filepath))
        self.s = bpy.context.scene.cray_ladder_settings
        self.assertEqual(self.s.ladder_id, 1)
        self.s.top_exit = 'RIGHT'
        obj = self.create()
        self.assertEqual(len(obj.data.vertices), 7)
        self.assertIn('ladder1_top_right', groups(obj))


from math import sin as math_sin, cos as math_cos
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LadderTests))
(ROOT / 'reports/ladder_validation.json').write_text(json.dumps(dict(tests=result.testsRun,
    failures=len(result.failures), errors=len(result.errors)), indent=2), encoding='utf-8')
nh.unregister()
if not result.wasSuccessful():
    raise RuntimeError('Ladder validation failed')
