import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('tb_core', HERE.parent/'NH_Blender/utilities/tb_txt.py')
core=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=core
spec.loader.exec_module(core)
ROW='"model";204685.415117;9111.983547;200.651855;0;0;1.000001;14;'

class CoreTests(unittest.TestCase):
    def test_parse_and_encodings(self):
        rows=core.parse_text('\ufeff# comment\n\n'+ROW.replace('model',r'model\_a'))
        self.assertEqual(rows[0].model,'model_a')
        self.assertEqual(rows[0].line,3)
        self.assertEqual(rows[0].east,204685.415117)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.txt'
            for encoding in ('utf-8-sig','utf-16','cp1251'):
                path.write_bytes(ROW.replace('model','модель').encode(encoding))
                self.assertEqual(core.read_placements(path)[0].model,'модель')
    def test_invalid(self):
        for row in ('',ROW.replace(';14;',';NaN;'),ROW.replace(';14;',';inf;'),
                    ROW.replace(';1.000001;',';0;'),ROW.replace(';1.000001;',';-1;'),
                    ROW.replace(';14;',';'),ROW.replace('204685.415117','204685,415117')):
            with self.subTest(row=row), self.assertRaises(core.ImportProblem):
                core.parse_text(row)
    def test_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for sub in ('a','b'):
                (root/sub).mkdir()
                (root/sub/'model.p3d').touch()
            records=core.parse_text(ROW)
            with self.assertRaisesRegex(core.ImportProblem,'Ambiguous'):
                core.resolve_models(records,root)
            records=core.parse_text(ROW.replace('model',r'a\model.p3d'))
            self.assertEqual(core.resolve_models(records,root)[records[0].model],root/'a/model.p3d')
            with self.assertRaises(core.ImportProblem):
                core.resolve_models(core.parse_text(ROW.replace('model','../model')),root)
            with self.assertRaisesRegex(core.ImportProblem,'Not found'):
                core.resolve_models(core.parse_text(ROW),root,False)
    def test_all_lod_bounds_and_properties(self):
        def lod(kind,verts,properties=()):
            return NS(resolution=NS(lod=kind),verts=verts,taggs=[NS(data=NS(key=k,value=v)) for k,v in properties])
        mlod=NS(lods=[lod(0,[(0,0,0,0),(2,2,2,0)]),lod(6,[(-4,-6,-8,0)], [('autocenter','0')])])
        self.assertEqual(core.bounds_center(mlod),(-1,-2,-3))
        self.assertTrue(core.has_autocenter_zero(mlod))
        mlod.lods.pop()
        self.assertFalse(core.has_autocenter_zero(mlod))


class TemplateLookupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.game_root = Path(self.temporary.name) / 'game'
        self.models = self.game_root / 'NH_Objects' / 'Common' / 'Furniture' / 'Shelves'
        self.libraries = self.game_root / 'NH_Objects' / 'TemplateLibs'
        self.models.mkdir(parents=True)
        self.libraries.mkdir(parents=True)

    def model(self, relative):
        path = self.game_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path.resolve()

    def library(self, entries, filename='library.tml', encoding='utf-8'):
        path = self.libraries / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        document = ET.Element('Library')
        for name, model in entries:
            template = ET.SubElement(document, 'Template')
            ET.SubElement(template, 'Name').text = name
            if model is not None:
                ET.SubElement(template, 'File').text = str(model)
        if encoding == 'utf-8-sig':
            data = b'\xef\xbb\xbf' + ET.tostring(document, encoding='utf-8', xml_declaration=True)
        else:
            data = ET.tostring(document, encoding=encoding, xml_declaration=True)
        path.write_bytes(data)
        return path

    def lookup(self, name='model', **kwargs):
        records = core.parse_text(ROW.replace('model', name))
        return core.resolve_models(records, self.models, templates_directory=self.libraries,
                                   **kwargs)[records[0].model]

    def test_alias_uses_exact_file_before_same_named_model(self):
        self.model('NH_Objects/Common/Furniture/Shelves/wood_shelf_small_b.p3d')
        expected = self.model('NH_Objects/Common/Furniture/Cabinets/wood_shelf_small_b_flip.p3d')
        self.library([('wood_shelf_small_b',
                       r'NH_Objects\Common\Furniture\Cabinets\wood_shelf_small_b_flip.p3d')])
        self.assertEqual(self.lookup('wood_shelf_small_b'), expected)

    def test_nested_uppercase_library_and_normalized_template_name(self):
        expected = self.model('NH_Objects/Common/Furniture/Shelves/actual.p3d')
        self.library([(r'Alias\_Name', r'NH_Objects\Common\Furniture\Shelves\actual.p3d')],
                     filename='nested/library.TML')
        self.assertEqual(self.lookup(r'ALIAS\_NAME', recursive=False), expected)

    def test_game_root_path_can_be_outside_selected_model_folder(self):
        expected = self.model('dz/structures/house.p3d')
        self.library([('model', r'dz\structures\house.p3d')])
        self.assertEqual(self.lookup(), expected)

    def test_model_root_ancestors_can_resolve_game_root_path(self):
        expected = self.model('dz/structures/house.p3d')
        outside = Path(self.temporary.name) / 'other_templates'
        outside.mkdir()
        original_libraries = self.libraries
        self.libraries = outside
        self.library([('model', r'dz\structures\house.p3d')])
        self.assertEqual(self.lookup(), expected)
        self.libraries = original_libraries

    def test_library_root_ancestors_can_resolve_game_root_path(self):
        expected = self.model('dz/structures/house.p3d')
        self.library([('model', r'dz\structures\house.p3d')])
        self.models = Path(self.temporary.name) / 'unrelated_models'
        self.models.mkdir()
        self.assertEqual(self.lookup(), expected)

    def test_absolute_file_is_respected(self):
        expected = self.model('outside/model_actual.p3d')
        self.library([('model', str(expected))])
        self.assertEqual(self.lookup(), expected)

    def test_virtual_model_and_library_ancestors_are_preserved(self):
        self.library([('model', r'dz\structures\house.p3d')])
        virtual_root = Path(self.temporary.name) / 'virtual_game'
        virtual_root.mkdir()
        linked_objects = virtual_root / 'NH_Objects'
        target = self.game_root / 'NH_Objects'
        if os.name == 'nt':
            result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(linked_objects), str(target)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.addCleanup(linked_objects.rmdir)
        else:
            linked_objects.symlink_to(target, target_is_directory=True)
            self.addCleanup(linked_objects.unlink)
        expected = virtual_root / 'dz' / 'structures' / 'house.p3d'
        expected.parent.mkdir(parents=True)
        expected.touch()
        original_models, original_libraries = self.models, self.libraries
        with self.subTest(anchor='selected model folder'):
            self.models = linked_objects / original_models.relative_to(target)
            self.assertEqual(self.lookup(), expected)
        with self.subTest(anchor='selected library folder'):
            self.models = original_models
            self.libraries = linked_objects / original_libraries.relative_to(target)
            self.assertEqual(self.lookup(), expected)

    def test_duplicate_mapping_for_same_file_is_allowed(self):
        expected = self.model('dz/structures/house.p3d')
        self.library([('model', r'dz\structures\house.p3d')])
        self.library([('MODEL', 'dz/structures/house.p3d')], filename='second.tml')
        self.assertEqual(self.lookup(), expected)

    def test_conflicting_mapping_reports_both_libraries(self):
        first = self.library([('model', 'dz/structures/first.p3d')])
        second = self.library([('model', 'dz/structures/second.p3d')], filename='second.tml')
        with self.assertRaises(core.ImportProblem) as raised:
            self.lookup()
        self.assertIn('Conflicting template', str(raised.exception))
        self.assertIn(str(first), str(raised.exception))
        self.assertIn(str(second), str(raised.exception))

    def test_absolute_and_relative_mappings_for_same_file_are_allowed(self):
        expected = self.model('dz/structures/house.p3d')
        self.library([('model', r'dz\structures\house.p3d')])
        self.library([('model', str(expected))], filename='absolute.tml')
        self.assertEqual(self.lookup(), expected)

    def test_conflicting_unrequested_template_does_not_prevent_fallback(self):
        self.library([('other', 'first.p3d'), ('other', 'second.p3d')])
        expected = self.model('NH_Objects/Common/Furniture/Shelves/model.p3d')
        self.assertEqual(self.lookup(), expected)

    def test_missing_mapped_path_does_not_fall_back_to_basename(self):
        self.model('NH_Objects/Common/Furniture/Shelves/model.p3d')
        self.model('NH_Objects/Common/Furniture/Shelves/actual.p3d')
        library = self.library([('model', r'dz\missing\actual.p3d')])
        with self.assertRaises(core.ImportProblem) as raised:
            self.lookup()
        self.assertIn("template 'model'", str(raised.exception))
        self.assertIn(r'dz\missing\actual.p3d', str(raised.exception))
        self.assertIn(str(library), str(raised.exception))

    def test_missing_library_folder_preserves_legacy_lookup(self):
        self.libraries = self.libraries / 'missing'
        expected = self.model('NH_Objects/Common/Furniture/Shelves/model.p3d')
        self.assertEqual(self.lookup(), expected)

    def test_missing_library_folder_is_in_unresolved_diagnostic(self):
        self.libraries = self.libraries / 'missing'
        with self.assertRaises(core.ImportProblem) as raised:
            self.lookup()
        self.assertIn('Not found: model', str(raised.exception))
        self.assertIn(str(self.libraries), str(raised.exception))

    def test_malformed_xml_is_reported_even_when_legacy_file_exists(self):
        self.model('NH_Objects/Common/Furniture/Shelves/model.p3d')
        library = self.libraries / 'broken.tml'
        library.write_text('<Library><Template>', encoding='utf-8')
        with self.assertRaises(core.ImportProblem) as raised:
            self.lookup()
        self.assertIn(str(library), str(raised.exception))

    def test_xml_declared_encodings(self):
        expected = self.model('dz/structures/house.p3d')
        for encoding in ('utf-8', 'utf-8-sig', 'utf-16', 'cp1251'):
            with self.subTest(encoding=encoding):
                self.library([('модель', 'dz/structures/house.p3d')], encoding=encoding)
                self.assertEqual(self.lookup('модель'), expected)

    def test_namespace_and_direct_fields_ignore_nested_metadata(self):
        expected = self.model('dz/structures/house.p3d')
        (self.libraries / 'library.tml').write_text(
            '<Library xmlns="urn:terrain-builder"><Template>'
            '<Metadata><Name>wrong</Name><File>wrong.p3d</File></Metadata>'
            '<Name>model</Name><File>dz/structures/house.p3d</File>'
            '</Template></Library>', encoding='utf-8')
        self.assertEqual(self.lookup(), expected)

    def test_invalid_requested_mapping_is_reported(self):
        self.model('NH_Objects/Common/Furniture/Shelves/model.p3d')
        for filename in (None, '', '../actual.p3d', 'invalid\x00.p3d', 'actual.txt', 'P:actual.p3d'):
            with self.subTest(filename=filename):
                self.library([('model', filename)])
                with self.assertRaises(core.ImportProblem):
                    self.lookup()

    def test_invalid_unrequested_mapping_does_not_prevent_fallback(self):
        self.library([('other', None)])
        expected = self.model('NH_Objects/Common/Furniture/Shelves/model.p3d')
        self.assertEqual(self.lookup(), expected)

    def test_multiple_existing_exact_paths_are_ambiguous(self):
        filename = 'dz/structures/house.p3d'
        first = self.model(filename)
        second = self.models / filename
        second.parent.mkdir(parents=True)
        second.touch()
        self.library([('model', filename)])
        with self.assertRaises(core.ImportProblem) as raised:
            self.lookup()
        self.assertIn('Ambiguous template', str(raised.exception))
        self.assertIn(str(first), str(raised.exception))
        self.assertIn(str(second), str(raised.exception))

    def test_template_name_is_exact_instead_of_a_model_stem(self):
        expected = self.model('NH_Objects/Common/Furniture/Shelves/model.p3d')
        self.library([('folder/model', 'missing.p3d')])
        self.assertEqual(self.lookup(), expected)

if __name__ == '__main__':
    unittest.main()
