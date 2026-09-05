import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

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

unittest.main()
