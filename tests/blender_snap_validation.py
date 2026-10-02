"""Blender regression checks for local exact-ID NH Automatic Snap Magnet validation."""
import contextlib
import io
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get("NH_TEST_PACKAGE_ROOT", str(ROOT)))
os.environ["BLENDER_USER_CONFIG"] = str(ROOT / "dist/snap_validation_config")
import bpy
import NH_Blender as nh
from NH_Blender import nh_snap as snap

nh.register()


class SnapLocalValidationTests(unittest.TestCase):
    def setUp(self):
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for col in list(bpy.data.collections):
            bpy.data.collections.remove(col)
        for mesh in list(bpy.data.meshes):
            bpy.data.meshes.remove(mesh)

    def mesh_object(self, collection, name, vertices, groups, lod=None, resolution=0):
        mesh = bpy.data.meshes.new(name + " Data")
        mesh.from_pydata([tuple(vertex) for vertex in vertices], [], [])
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
        if lod is not None:
            obj.a3ob_properties_object.is_a3_lod = True
            obj.a3ob_properties_object.lod = str(lod)
            obj.a3ob_properties_object.resolution = int(resolution)
        for group_name, index in groups.items():
            group = obj.vertex_groups.new(name=group_name)
            group.add([int(index)], 1.0, "REPLACE")
        return obj

    def build_root(self, name, vertices, groups, location=(0.0, 0.0, 0.0)):
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
        memory = self.mesh_object(collection, "Memory", vertices, groups, "9")
        reference = self.mesh_object(collection, "Resolution 0", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], {}, "0")
        memory.location = location
        reference.location = location
        bpy.context.view_layer.update()
        return {"collection": collection, "memory": memory, "reference": reference}

    def validate(self):
        inventory, errors, warnings = snap._snap_check_scene_inventory(bpy.context)
        results, edges = snap._snap_check_build_local_pairs(inventory, errors)
        return inventory, results, edges, errors, warnings

    def result_for(self, results, base_name):
        for result in results:
            if result["base"] == base_name:
                return result
        self.fail(f"result for .sp_{base_name} not found")

    def test_multiple_exact_ids_x_and_y_on_shared_root_without_chain_errors(self):
        self.build_root(
            "alpha_p01.p3d",
            [(0, 0, 0), (1, 0, 0), (0, 0, 0), (0, 2, 0)],
            {
                ".sp_alpha01x_a_0": 0,
                ".sp_alpha01x_a_1": 1,
                ".sp_alpha05y_a_0": 2,
                ".sp_alpha05y_a_1": 3,
            },
        )
        self.build_root(
            "alpha_p02.p3d",
            [(0, 0, 0), (1, 0, 0), (0, 0, 0), (0, 2, 0)],
            {
                ".sp_alpha01x_v_0": 0,
                ".sp_alpha01x_v_1": 1,
                ".sp_alpha05y_v_0": 2,
                ".sp_alpha05y_v_1": 3,
            },
        )
        _inventory, results, edges, errors, _warnings = self.validate()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len(edges), 2)
        self.assertEqual(results[0]["status"], "PASS")
        self.assertEqual(results[1]["status"], "PASS")
        for result in results:
            self.assertIn(result["a_root"], ("alpha_p01.p3d",))
            self.assertIn(result["v_root"], ("alpha_p02.p3d",))
            self.assertAlmostEqual(result["delta"], 0.0, places=6)

    def test_coincident_positions_of_different_ids_are_not_errors(self):
        self.build_root(
            "coincident_p01.p3d",
            [(0, 0, 0), (1, 0, 0), (0, 0, 0), (0, 2, 0)],
            {
                ".sp_coincident01x_a_0": 0,
                ".sp_coincident01x_a_1": 1,
                ".sp_coincident01y_a_0": 2,
                ".sp_coincident01y_a_1": 3,
            },
        )
        self.build_root(
            "coincident_p02.p3d",
            [(0, 0, 0), (1, 0, 0), (0, 0, 0), (0, 2, 0)],
            {
                ".sp_coincident01x_v_0": 0,
                ".sp_coincident01x_v_1": 1,
                ".sp_coincident01y_v_0": 2,
                ".sp_coincident01y_v_1": 3,
            },
        )
        _inventory, results, _edges, errors, warnings = self.validate()
        self.assertEqual(errors, [])
        self.assertEqual([result["status"] for result in results], ["PASS", "PASS"])
        self.assertTrue(any("same position" in warning for warning in warnings), warnings)

    def test_shared_memory_vertex_is_warning_not_error(self):
        self.build_root(
            "shared_p01.p3d",
            [(0, 0, 0), (1, 0, 0), (0, 2, 0)],
            {
                ".sp_shared01x_a_0": 0,
                ".sp_shared01x_a_1": 1,
                ".sp_shared01y_a_0": 0,
                ".sp_shared01y_a_1": 2,
            },
        )
        self.build_root(
            "shared_p02.p3d",
            [(0, 0, 0), (1, 0, 0), (0, 2, 0)],
            {
                ".sp_shared01x_v_0": 0,
                ".sp_shared01x_v_1": 1,
                ".sp_shared01y_v_0": 0,
                ".sp_shared01y_v_1": 2,
            },
        )
        _inventory, results, _edges, errors, warnings = self.validate()
        self.assertEqual(errors, [])
        self.assertEqual([result["status"] for result in results], ["PASS", "PASS"])
        self.assertTrue(any("used by multiple snap selections" in warning for warning in warnings), warnings)

    def test_duplicate_exact_id_across_roots_is_real_error(self):
        self.build_root(
            "zaton_refinery_building_A_p01_01.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_zatonrefinerybuildinga06x_a_0": 0, ".sp_zatonrefinerybuildinga06x_a_1": 1},
        )
        self.build_root(
            "zaton_refinery_building_A_p04_01.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_zatonrefinerybuildinga06x_v_0": 0, ".sp_zatonrefinerybuildinga06x_v_1": 1},
        )
        self.build_root(
            "zaton_refinery_building_A_p07_01.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_zatonrefinerybuildinga06x_v_0": 0, ".sp_zatonrefinerybuildinga06x_v_1": 1},
        )
        _inventory, results, edges, errors, _warnings = self.validate()
        result = self.result_for(results, "zatonrefinerybuildinga06x")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(edges, [])
        self.assertTrue(errors)
        message = " ".join(result["reasons"])
        self.assertIn(
            "Duplicate logical snap pair '.sp_zatonrefinerybuildinga06x_V_0/1' exists in multiple roots",
            message,
        )
        self.assertIn("zaton_refinery_building_A_p04_01.p3d", message)
        self.assertIn("zaton_refinery_building_A_p07_01.p3d", message)

    def test_a_v_length_mismatch_is_local_error(self):
        self.build_root(
            "length_p01.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_length01x_a_0": 0, ".sp_length01x_a_1": 1},
        )
        self.build_root(
            "length_p02.p3d",
            [(0, 0, 0), (0, 1.5, 0)],
            {".sp_length01x_v_0": 0, ".sp_length01x_v_1": 1},
        )
        _inventory, results, _edges, errors, _warnings = self.validate()
        result = self.result_for(results, "length01x")
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(errors)
        message = " ".join(result["reasons"])
        self.assertIn("A/V distances differ", message)
        self.assertAlmostEqual(result["delta"], 0.5, places=6)

    def test_incomplete_pair_is_local_error(self):
        self.build_root(
            "broken_p01.p3d",
            [(0, 0, 0)],
            {".sp_broken01x_a_0": 0},
        )
        self.build_root(
            "broken_p02.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_broken01x_v_0": 0, ".sp_broken01x_v_1": 1},
        )
        _inventory, results, _edges, errors, _warnings = self.validate()
        result = self.result_for(results, "broken01x")
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(errors)
        message = " ".join(result["reasons"])
        self.assertIn("A pair is incomplete", message)
        self.assertIn("missing _1", message)

    def test_same_root_a_v_is_local_error(self):
        self.build_root(
            "sameroot_p01.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_sameroot01x_a_0": 0, ".sp_sameroot01x_a_1": 1, ".sp_sameroot01x_v_0": 0, ".sp_sameroot01x_v_1": 1},
        )
        _inventory, results, _edges, errors, _warnings = self.validate()
        result = self.result_for(results, "sameroot01x")
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(errors)
        self.assertIn("same P3D root", " ".join(result["reasons"]))

    def test_missing_counterpart_is_warning_not_error(self):
        self.build_root(
            "solo_p01.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_solo01x_a_0": 0, ".sp_solo01x_a_1": 1},
        )
        _inventory, results, edges, errors, _warnings = self.validate()
        self.assertEqual(edges, [])
        self.assertEqual(errors, ["No complete matching A/V magnet IDs found"])
        result = self.result_for(results, "solo01x")
        self.assertEqual(result["status"], "WARN")
        self.assertIn("unmatched snap connector", " ".join(result["reasons"]))

    def test_repeated_models_cycle_is_not_an_error(self):
        self.build_root(
            "cycle_p01.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {
                ".sp_cycle01x_a_0": 0,
                ".sp_cycle01x_a_1": 1,
                ".sp_cycle02x_v_0": 0,
                ".sp_cycle02x_v_1": 1,
            },
        )
        self.build_root(
            "cycle_p02.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {
                ".sp_cycle01x_v_0": 0,
                ".sp_cycle01x_v_1": 1,
                ".sp_cycle02x_a_0": 0,
                ".sp_cycle02x_a_1": 1,
            },
        )
        _inventory, results, edges, errors, _warnings = self.validate()
        self.assertEqual(errors, [])
        self.assertEqual([result["status"] for result in results], ["PASS", "PASS"])
        self.assertEqual(len(edges), 2)

    def test_report_is_per_id_without_chains(self):
        self.build_root(
            "report_p01.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_report01x_a_0": 0, ".sp_report01x_a_1": 1},
        )
        self.build_root(
            "report_p02.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_report01x_v_0": 0, ".sp_report01x_v_1": 1},
        )
        inventory, results, _edges, errors, warnings = self.validate()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            snap._print_automatic_snap_check_report(inventory, results, errors, warnings)
        text = buffer.getvalue()
        self.assertIn("PASS .sp_report01x:", text)
        self.assertIn("A-root=report_p01.p3d", text)
        self.assertIn("V-root=report_p02.p3d", text)
        self.assertIn("A_len=1.000000", text)
        self.assertIn("V_len=1.000000", text)
        self.assertIn("delta=0.000000", text)
        self.assertNotIn("CHAIN ", text)
        self.assertNotIn("Non-contiguous", text)
        self.assertNotIn("Broken chain", text)

    def test_operator_assembles_multiple_local_pairs(self):
        self.build_root(
            "assemble_p01.p3d",
            [(0, 0, 0), (1, 0, 0), (0, 0, 0), (0, 2, 0)],
            {
                ".sp_assemble01x_a_0": 0,
                ".sp_assemble01x_a_1": 1,
                ".sp_assemble02y_a_0": 2,
                ".sp_assemble02y_a_1": 3,
            },
        )
        root_v = self.build_root(
            "assemble_p02.p3d",
            [(0, 0, 0), (1, 0, 0), (0, 0, 0), (0, 2, 0)],
            {
                ".sp_assemble01x_v_0": 0,
                ".sp_assemble01x_v_1": 1,
                ".sp_assemble02y_v_0": 2,
                ".sp_assemble02y_v_1": 3,
            },
            location=(0.0, 3.0, 0.0),
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = bpy.ops.cray.validate_assemble_snap_points()
        self.assertEqual(result, {"FINISHED"})
        self.assertAlmostEqual(root_v["reference"].matrix_world.translation.x, 0.0, places=5)
        self.assertAlmostEqual(root_v["reference"].matrix_world.translation.y, 0.0, places=5)
        self.assertAlmostEqual(root_v["reference"].matrix_world.translation.z, 0.0, places=5)
        text = buffer.getvalue()
        self.assertIn("ASSEMBLED:", text)
        self.assertNotIn("ERRORS", text)

    def test_assembly_hides_all_technical_lods_except_resolution0(self):
        root_a = self.build_root(
            "visibility_p01.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_vis01x_a_0": 0, ".sp_vis01x_a_1": 1},
        )
        collection = root_a["collection"]
        extra = {}
        for label, token, resolution in (
            ("Resolution 1", "0", 1),
            ("Resolution 2", "0", 2),
            ("Geometry", "6", 0),
            ("Fire Geometry", "15", 0),
            ("View Geometry", "14", 0),
            ("Roadway", "11", 0),
        ):
            extra[label] = self.mesh_object(collection, label, [(0, 0, 0), (1, 0, 0)], {}, token, resolution)
        memory_lod = root_a["memory"]
        body_part = self.mesh_object(collection, "Body Part", [(0, 0, 0)], {})
        body_part.parent = root_a["reference"]
        geometry_part = self.mesh_object(collection, "Geo Part", [(0, 0, 0)], {})
        geometry_part.parent = extra["Geometry"]
        bpy.context.view_layer.update()

        root_v = self.build_root(
            "visibility_p02.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_vis01x_v_0": 0, ".sp_vis01x_v_1": 1},
            location=(0.0, 3.0, 0.0),
        )
        v_resolution1 = self.mesh_object(root_v["collection"], "Resolution 1", [(0, 0, 0), (1, 0, 0)], {}, "0", 1)
        bpy.context.view_layer.update()

        for obj in (root_a["reference"], root_v["reference"], memory_lod, *extra.values(), v_resolution1, body_part, geometry_part):
            self.assertFalse(obj.hide_get(), f"{obj.name} should start visible")

        objects_before = sorted(obj.name for obj in bpy.data.objects)
        lod_props_before = {
            obj.name: (bool(obj.a3ob_properties_object.is_a3_lod), str(obj.a3ob_properties_object.lod))
            for obj in bpy.data.objects
            if obj.type == "MESH"
        }

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = bpy.ops.cray.validate_assemble_snap_points()
        text = buffer.getvalue()
        self.assertEqual(result, {"FINISHED"})
        self.assertIn("=== Assembly LOD Visibility ===", text)
        self.assertIn("root: visibility_p01.p3d", text)
        self.assertIn("visible: Resolution 0", text)
        for label in ("Resolution 1", "Resolution 2", "Memory", "Geometry", "Fire Geometry", "View Geometry", "Roadway"):
            self.assertIn(label, text)

        self.assertFalse(root_a["reference"].hide_get())
        self.assertFalse(root_a["reference"].hide_viewport)
        self.assertFalse(root_v["reference"].hide_get())
        self.assertTrue(memory_lod.hide_get(), "Memory must be hidden")
        for label, obj in extra.items():
            self.assertTrue(obj.hide_get(), f"{label} must be hidden")
            self.assertTrue(obj.hide_viewport, f"{label} must be hidden in viewport")
        self.assertTrue(v_resolution1.hide_get(), "Resolution 1 of the V root must be hidden")
        self.assertTrue(geometry_part.hide_get(), "child of a hidden LOD must be hidden")
        self.assertFalse(body_part.hide_get(), "child of visible Resolution 0 must stay visible")

        self.assertEqual(sorted(obj.name for obj in bpy.data.objects), objects_before)
        lod_props_after = {
            obj.name: (bool(obj.a3ob_properties_object.is_a3_lod), str(obj.a3ob_properties_object.lod))
            for obj in bpy.data.objects
            if obj.type == "MESH"
        }
        self.assertEqual(lod_props_before, lod_props_after)

    def test_missing_resolution0_prints_warning_without_hiding(self):
        collection = bpy.data.collections.new("nores_p01.p3d")
        bpy.context.scene.collection.children.link(collection)
        memory = self.mesh_object(
            collection,
            "Memory",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_nores01x_a_0": 0, ".sp_nores01x_a_1": 1},
            "9",
        )
        geometry = self.mesh_object(collection, "Geometry", [(0, 0, 0), (1, 0, 0)], {}, "6")
        bpy.context.view_layer.update()

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            updated, missing = snap._apply_assembly_lod_visibility(bpy.context, [{"root": collection}])
        text = buffer.getvalue()
        self.assertEqual(updated, 0)
        self.assertEqual(missing, ["nores_p01.p3d"])
        self.assertIn("Resolution 0 not found", text)
        self.assertFalse(memory.hide_get())
        self.assertFalse(geometry.hide_get())

    def test_lod_type_property_wins_over_object_name(self):
        collection = bpy.data.collections.new("props_p01.p3d")
        bpy.context.scene.collection.children.link(collection)
        hidden = self.mesh_object(collection, "Resolution 0.001", [(0, 0, 0), (1, 0, 0)], {}, "0", 1)
        visible = self.mesh_object(collection, "Visual Root Copy", [(0, 0, 0), (1, 0, 0)], {}, "0", 0)
        fallback_memory = self.mesh_object(collection, "Memory", [(0, 0, 0), (1, 0, 0)], {})
        fallback_resolution0 = self.mesh_object(collection, "Resolution 0 Copy", [(0, 0, 0), (1, 0, 0)], {})
        bpy.context.view_layer.update()

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            updated, missing = snap._apply_assembly_lod_visibility(bpy.context, [{"root": collection}])
        text = buffer.getvalue()
        self.assertEqual(updated, 1)
        self.assertEqual(missing, [])
        self.assertTrue(hidden.hide_get(), "object named Resolution 0.001 but typed Resolution 1 must be hidden")
        self.assertFalse(visible.hide_get(), "object with unrelated name but typed Resolution 0 must stay visible")
        self.assertTrue(fallback_memory.hide_get(), "name fallback must hide Memory without LOD properties")
        self.assertFalse(fallback_resolution0.hide_get(), "name fallback must keep Resolution 0 visible")
        self.assertIn("visible: Resolution 0", text)
        self.assertIn("hidden: Resolution 1, Memory", text)

    def world_snapshot(self):
        return {
            obj.as_pointer(): obj.matrix_world.copy()
            for obj in bpy.data.objects
        }

    def assert_snapshot_equal(self, snapshot, places=5):
        for obj in bpy.data.objects:
            before = snapshot.get(obj.as_pointer())
            if before is None:
                continue
            for row in range(4):
                for col in range(4):
                    self.assertAlmostEqual(
                        obj.matrix_world[row][col],
                        before[row][col],
                        places=places,
                        msg=f"{obj.name} moved unexpectedly on idempotent rerun",
                    )

    def assert_edges_aligned(self):
        inventory, _results, edges, errors, _warnings = self.validate()
        self.assertEqual(errors, [])
        for edge in edges:
            points_a = snap._snap_check_virtual_pair(edge["a"], {})
            points_v = snap._snap_check_virtual_pair(edge["v"], {})
            self.assertLess((points_a[0] - points_v[0]).length, 1e-5, edge["base"])
            self.assertLess((points_a[1] - points_v[1]).length, 1e-5, edge["base"])
        return inventory, edges

    def test_assembly_applies_one_solved_transform_to_whole_visual_subtree(self):
        p01 = self.build_root(
            "subtree_p01.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_subtree01x_a_0": 0, ".sp_subtree01x_a_1": 1},
        )
        p02 = self.build_root(
            "subtree_p02.p3d",
            [(0, 3, 0), (1, 3, 0), (0, 0, 0), (0, 2, 0)],
            {
                ".sp_subtree01x_v_0": 0,
                ".sp_subtree01x_v_1": 1,
                ".sp_subtree02y_a_0": 2,
                ".sp_subtree02y_a_1": 3,
            },
        )
        p03 = self.build_root(
            "subtree_p03.p3d",
            [(0, 5, 0), (0, 7, 0)],
            {".sp_subtree02y_v_0": 0, ".sp_subtree02y_v_1": 1},
        )
        collection = p02["collection"]
        child_proxy = self.mesh_object(collection, "proxy: child preview", [(0, 0, 0), (1, 0, 0)], {})
        child_proxy.parent = p02["reference"]
        proxy_like = self.mesh_object(collection, "proxy: orphan preview", [(0, 0, 0), (1, 0, 0)], {})
        geometry = self.mesh_object(collection, "Geometry", [(0, 0, 0)], {}, "6")
        geometry_proxy = self.mesh_object(collection, "proxy: geometry preview", [(0, 0, 0)], {})
        geometry_proxy.parent = geometry
        bpy.context.view_layer.update()

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = bpy.ops.cray.validate_assemble_snap_points()
        text = buffer.getvalue()
        self.assertEqual(result, {"FINISHED"})
        self.assertNotIn("ERRORS", text)
        self.assertIn("=== Assembly Solve Diagnostics ===", text)
        self.assertIn("=== Assembly Transform Audit ===", text)
        self.assertIn("root=subtree_p01.p3d\nstatus=ANCHOR", text)
        self.assertIn("proxy children: APPLIED", text)

        self.assertAlmostEqual(p02["reference"].matrix_world.translation.y, -3.0, places=5)
        self.assertAlmostEqual(p03["reference"].matrix_world.translation.y, -8.0, places=5)
        self.assertAlmostEqual(p01["reference"].matrix_world.translation.y, 0.0, places=5)
        self.assertAlmostEqual(child_proxy.matrix_world.translation.y, -3.0, places=5)
        self.assertAlmostEqual(proxy_like.matrix_world.translation.y, -3.0, places=5)
        self.assertAlmostEqual(geometry_proxy.matrix_world.translation.y, 0.0, places=5)
        self.assertNotAlmostEqual(p02["reference"].matrix_world.translation.y, 0.0, places=5)
        self.assertNotAlmostEqual(p03["reference"].matrix_world.translation.y, 0.0, places=5)

        self.assert_edges_aligned()

        snapshot = self.world_snapshot()
        with contextlib.redirect_stdout(io.StringIO()):
            result = bpy.ops.cray.validate_assemble_snap_points()
        self.assertEqual(result, {"FINISHED"})
        self.assert_snapshot_equal(snapshot)

    def test_single_anchor_per_component_does_not_break_earlier_ids(self):
        p01 = self.build_root(
            "multi_p01.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_multi01x_a_0": 0, ".sp_multi01x_a_1": 1},
        )
        p02 = self.build_root(
            "multi_p02.p3d",
            [(0, 3, 0), (1, 3, 0), (0, 0, 0), (0, 2, 0), (5, 3, 0), (6, 3, 0)],
            {
                ".sp_multi01x_v_0": 0,
                ".sp_multi01x_v_1": 1,
                ".sp_multi02y_a_0": 2,
                ".sp_multi02y_a_1": 3,
                ".sp_multi03z_v_0": 4,
                ".sp_multi03z_v_1": 5,
            },
        )
        p03 = self.build_root(
            "multi_p03.p3d",
            [(0, 0, 0), (1, 0, 0)],
            {".sp_multi03z_a_0": 0, ".sp_multi03z_a_1": 1},
        )
        p04 = self.build_root(
            "multi_p04.p3d",
            [(0, 5, 0), (0, 7, 0)],
            {".sp_multi02y_v_0": 0, ".sp_multi02y_v_1": 1},
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = bpy.ops.cray.validate_assemble_snap_points()
        text = buffer.getvalue()
        self.assertEqual(result, {"FINISHED"})
        self.assertNotIn("ERRORS", text)

        self.assertAlmostEqual(p01["reference"].matrix_world.translation.x, 0.0, places=5)
        self.assertAlmostEqual(p02["reference"].matrix_world.translation.y, -3.0, places=5)
        self.assertAlmostEqual(p04["reference"].matrix_world.translation.y, -8.0, places=5)
        self.assertAlmostEqual(p03["reference"].matrix_world.translation.x, 5.0, places=5)
        self.assertIn("root=multi_p01.p3d\nstatus=ANCHOR", text)
        self.assertIn("root=multi_p03.p3d\nstatus=MOVED", text)

        self.assert_edges_aligned()

        snapshot = self.world_snapshot()
        with contextlib.redirect_stdout(io.StringIO()):
            result = bpy.ops.cray.validate_assemble_snap_points()
        self.assertEqual(result, {"FINISHED"})
        self.assert_snapshot_equal(snapshot)

    def test_non_closing_constraint_cycle_is_an_error_without_writes(self):
        self.build_root(
            "cycle_bad_p01.p3d",
            [(0, 0, 0), (1, 0, 0), (0, 0, 0), (1, 0, 0)],
            {
                ".sp_cyclebad01x_a_0": 0,
                ".sp_cyclebad01x_a_1": 1,
                ".sp_cyclebad02z_a_0": 2,
                ".sp_cyclebad02z_a_1": 3,
            },
        )
        self.build_root(
            "cycle_bad_p02.p3d",
            [(1, 0, 0), (2, 0, 0), (0, 0, 0), (0, 1, 0)],
            {
                ".sp_cyclebad01x_v_0": 0,
                ".sp_cyclebad01x_v_1": 1,
                ".sp_cyclebad02y_a_0": 2,
                ".sp_cyclebad02y_a_1": 3,
            },
        )
        self.build_root(
            "cycle_bad_p03.p3d",
            [(0, 1, 0), (0, 2, 0), (5, 0, 0), (6, 0, 0)],
            {
                ".sp_cyclebad02y_v_0": 0,
                ".sp_cyclebad02y_v_1": 1,
                ".sp_cyclebad02z_v_0": 2,
                ".sp_cyclebad02z_v_1": 3,
            },
        )
        snapshot = self.world_snapshot()
        inventory, errors, _warnings = snap._snap_check_scene_inventory(bpy.context)
        self.assertEqual(errors, [])
        _results, edges = snap._snap_check_build_local_pairs(inventory, errors)
        self.assertEqual(errors, [])
        with self.assertRaises(RuntimeError) as raised:
            snap._snap_check_plan_automatic_assembly(edges)
        self.assertIn("Solved assembly cannot satisfy", str(raised.exception))
        self.assert_snapshot_equal(snapshot, places=6)


result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(SnapLocalValidationTests)
)
nh.unregister()
if not result.wasSuccessful():
    raise RuntimeError("Snap local validation tests failed")
