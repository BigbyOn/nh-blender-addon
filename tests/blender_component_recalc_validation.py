"""Blender regression checks for geometry component recalculation during P3D export."""
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get("NH_TEST_PACKAGE_ROOT", str(ROOT)))
os.environ["BLENDER_USER_CONFIG"] = str(ROOT / "dist/component_recalc_validation_config")
import bmesh
import bpy
import NH_Blender as nh
from NH_bundle.io import data_p3d as p3d
from NH_bundle.io import export_p3d

nh.register()

CUBE_VERTS = (
    (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
    (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
)
CUBE_FACES = (
    (0, 1, 2, 3),
    (4, 7, 6, 5),
    (0, 4, 5, 1),
    (1, 5, 6, 2),
    (2, 6, 7, 3),
    (3, 7, 4, 0),
)


def build_two_cubes_mesh(name):
    vertices = []
    faces = []
    for offset in ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)):
        base = len(vertices)
        vertices.extend(
            (x + offset[0], y + offset[1], z + offset[2]) for x, y, z in CUBE_VERTS
        )
        faces.extend(tuple(index + base for index in face) for face in CUBE_FACES)

    mesh = bpy.data.meshes.new(name + " Data")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()

    bm = bmesh.new()
    bm.from_mesh(mesh)
    mass_layer = bm.verts.layers.float.new("a3ob_mass")
    for vert in bm.verts:
        vert[mass_layer] = 1.0
    bm.to_mesh(mesh)
    bm.free()
    return mesh


class ComponentRecalcTests(unittest.TestCase):
    def setUp(self):
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for col in list(bpy.data.collections):
            bpy.data.collections.remove(col)
        for mesh in list(bpy.data.meshes):
            bpy.data.meshes.remove(mesh)

    def make_geometry_object(self, name="Geometry"):
        collection = bpy.data.collections.new("recalc_test_p01.p3d")
        bpy.context.scene.collection.children.link(collection)
        obj = bpy.data.objects.new(name, build_two_cubes_mesh(name))
        collection.objects.link(obj)
        obj.a3ob_properties_object.is_a3_lod = True
        obj.a3ob_properties_object.lod = "6"
        obj.a3ob_properties_object.resolution = 0
        return obj

    def add_stale_components(self, obj):
        group = obj.vertex_groups.new(name="component01")
        group.add(list(range(8)), 1.0, "REPLACE")
        group = obj.vertex_groups.new(name="component02")
        group.add(list(range(8, 12)), 1.0, "REPLACE")

    def fake_operator(self, filepath, recalculate):
        operator = type("_ComponentRecalcTestOperator", (), {})()
        operator.filepath = filepath
        operator.use_selection = True
        operator.visible_only = False
        operator.relative_paths = True
        operator.preserve_normals = True
        operator.validate_meshes = False
        operator.apply_transforms = True
        operator.apply_modifiers = False
        operator.sort_sections = True
        operator.lod_collisions = "SKIP"
        operator.validate_lods = False
        operator.validate_lods_warning_errors = False
        operator.generate_components = True
        operator.recalculate_components = recalculate
        operator.renumber_components = True
        operator.translate_selections = False
        operator.force_lowercase = True
        return operator

    def export_object(self, obj, recalculate):
        for other in bpy.context.selected_objects:
            other.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        filepath = os.path.join(tempfile.mkdtemp(), "component_recalc.p3d")
        operator = self.fake_operator(filepath, recalculate)
        context = bpy.context
        temp_collection = export_p3d.create_temp_collection(context)
        try:
            with open(filepath, "wb") as file:
                export_p3d.write_file(operator, context, file, temp_collection)
        finally:
            export_p3d.cleanup_temp_collection(temp_collection)
        return p3d.P3D_MLOD.read_file(filepath)

    def geometry_lod(self, mlod):
        for lod in mlod.lods:
            if lod.resolution.get() == (6, 0):
                return lod
        self.fail("Geometry LOD is missing from the exported file")

    def component_selections(self, lod):
        return [
            tagg for tagg in lod.taggs
            if re.fullmatch(r"component\d+", tagg.name, re.IGNORECASE)
        ]

    def test_recalculate_replaces_stale_components_with_closed_parts(self):
        obj = self.make_geometry_object()
        self.add_stale_components(obj)

        geometry = self.geometry_lod(self.export_object(obj, recalculate=True))
        components = self.component_selections(geometry)
        self.assertEqual(len(components), 2)
        self.assertEqual(
            sorted(tagg.name for tagg in components),
            ["Component01", "Component02"],
        )
        covered = sorted(index for tagg in components for index, _ in tagg.data.weight_verts)
        self.assertEqual(covered, list(range(16)))
        self.assertEqual(sorted(len(tagg.data.weight_verts) for tagg in components), [8, 8])

    def test_recalculate_disabled_keeps_existing_components(self):
        obj = self.make_geometry_object()
        self.add_stale_components(obj)

        geometry = self.geometry_lod(self.export_object(obj, recalculate=False))
        components = self.component_selections(geometry)
        self.assertEqual(len(components), 2)
        self.assertEqual(
            sorted(tagg.name for tagg in components),
            ["Component01", "Component02"],
        )
        covered = sorted(index for tagg in components for index, _ in tagg.data.weight_verts)
        self.assertEqual(covered, list(range(12)))
        self.assertEqual(sorted(len(tagg.data.weight_verts) for tagg in components), [4, 8])

    def test_generate_components_without_stale_components(self):
        obj = self.make_geometry_object()

        geometry = self.geometry_lod(self.export_object(obj, recalculate=True))
        components = self.component_selections(geometry)
        self.assertEqual(len(components), 2)
        covered = sorted(index for tagg in components for index, _ in tagg.data.weight_verts)
        self.assertEqual(covered, list(range(16)))

    def test_export_operator_exposes_recalculate_option(self):
        rna = bpy.ops.nh.export_p3d.get_rna_type()
        identifiers = {prop.identifier for prop in rna.properties}
        self.assertIn("generate_components", identifiers)
        self.assertIn("recalculate_components", identifiers)

    def test_batch_setting_defaults_to_recalculate(self):
        settings = bpy.context.scene.cray_ie_settings
        self.assertTrue(bool(settings.export_recalculate_components))

    def test_batch_export_recalculates_components(self):
        obj = self.make_geometry_object()
        self.add_stale_components(obj)
        settings = bpy.context.scene.cray_ie_settings
        export_dir = tempfile.mkdtemp()
        settings.export_mode = "CUSTOM_DIR"
        settings.export_directory = export_dir
        settings.export_create_bak = False
        settings.export_force_all_lods = False
        settings.export_geometry_house_metadata = False
        settings.export_recalculate_components = True
        settings.export_only_p3d_named = True
        settings.export_only_split_parts = False

        result = bpy.ops.cray.ie_export_collections_batch()
        self.assertIn("FINISHED", result)

        filepath = os.path.join(export_dir, "recalc_test_p01.p3d")
        self.assertTrue(os.path.isfile(filepath))
        geometry = self.geometry_lod(p3d.P3D_MLOD.read_file(filepath))
        components = self.component_selections(geometry)
        self.assertEqual(len(components), 2)
        self.assertEqual(
            sorted(tagg.name for tagg in components),
            ["Component01", "Component02"],
        )
        covered = sorted(index for tagg in components for index, _ in tagg.data.weight_verts)
        self.assertEqual(covered, list(range(16)))


result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(ComponentRecalcTests)
)
nh.unregister()
if not result.wasSuccessful():
    raise RuntimeError("Component recalculation validation tests failed")
