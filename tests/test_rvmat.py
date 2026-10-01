"""Library identity, filtering, decoding and cache invalidation without Blender."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('rvmat_core', Path(__file__).resolve().parents[1]/'NH_Blender/utilities/rvmat.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class LibraryTests(unittest.TestCase):
    def test_super_filter_variants_and_unsuffixed_colors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            nested = root/'one/two'
            nested.mkdir(parents=True)
            for name in ('wall_co.paa', 'wall_ca.paa', 'plain.paa'):
                (nested/name).write_bytes(b'fixture')
            for name, shader in [('wall', 'Super'), ('plain', 'Super'), ('tree', 'TreeAdv')]:
                (nested/(name+'.rvmat')).write_text('PixelShaderID="%s";' % shader)
            result = core.scan(folder)
            self.assertFalse(result['errors'])
            self.assertEqual(len(result['entries']), 3)
            self.assertEqual({entry['folder'] for entry in result['entries']}, {'one/two'})
            self.assertEqual(len({entry['id'] for entry in result['entries']}), 3)
            self.assertTrue(all(entry['color'] for entry in result['entries']))

    def test_map_change_and_missing_map_invalidate_signature(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            material, normal = root/'wall.rvmat', root/'wall_nohq.paa'
            material.write_text('PixelShaderID="Super";class Stage1 {texture="wall_nohq.paa";};')
            normal.write_bytes(b'first')
            first = core.signature(str(material), '', folder)
            normal.write_bytes(b'second longer')
            second = core.signature(str(material), '', folder)
            self.assertNotEqual(first, second)
            normal.unlink()
            self.assertNotEqual(second, core.signature(str(material), '', folder))

    def test_cp1251_paths_comments_and_manual_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root/'wall.rvmat'
            color = root/'different_co.paa'
            color.write_bytes(b'fixture')
            path.write_bytes(('/* PixelShaderID="Tree"; */\nPixelShaderID="Super";'
                              'class Stage1 {texture="NH_ObjectTextures\\стена_nohq.paa";};').encode('cp1251'))
            data = core.read(path)
            self.assertIn('стена', data['stage1']['texture'])
            result = core.scan(folder, {str(path).lower(): str(color)})
            # normcase is case folding on Windows; independent on other OSes.
            if not result['entries'][0]['color']:
                import os
                result = core.scan(folder, {os.path.normcase(str(path)): str(color)})
            self.assertEqual(result['entries'][0]['color'], str(color))


if __name__ == '__main__':
    unittest.main()
