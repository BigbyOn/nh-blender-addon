"""Terrain Builder text parsing and model lookup; no Blender dependency."""
from dataclasses import dataclass
import csv
import math
import os
from pathlib import Path


class ImportProblem(ValueError):
    pass


@dataclass(frozen=True)
class Placement:
    line: int
    model: str
    east: float
    north: float
    yaw: float
    pitch: float
    roll: float
    scale: float
    elevation: float


def parse_text(text):
    records = []
    for number, line in enumerate(text.lstrip('\ufeff').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith(('#', '//')):
            continue
        try:
            fields = next(csv.reader([line], delimiter=';', skipinitialspace=True, strict=True))
        except csv.Error as error:
            raise ImportProblem(f'Line {number}: {error}') from error
        fields = [field.strip() for field in fields]
        while fields and not fields[-1]:
            fields.pop()
        if len(fields) != 8:
            raise ImportProblem(f'Line {number}: expected 8 columns, found {len(fields)}')
        model = fields[0].replace('\\_', '_')
        if not model or '\x00' in model:
            raise ImportProblem(f'Line {number}: empty or invalid model name')
        try:
            values = [float(value) for value in fields[1:]]
        except ValueError as error:
            raise ImportProblem(f'Line {number}: invalid number; use a decimal point') from error
        if not all(math.isfinite(value) for value in values):
            raise ImportProblem(f'Line {number}: NaN and infinity are not allowed')
        if values[5] <= 0:
            raise ImportProblem(f'Line {number}: scale must be greater than zero')
        records.append(Placement(number, model, *values))
    if not records:
        raise ImportProblem('The TXT file contains no object placements')
    return records


def read_placements(filepath):
    data = Path(filepath).read_bytes()
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        encodings = ('utf-16',)
    else:
        encodings = ('utf-8-sig', 'cp1251')
    for encoding in encodings:
        try:
            return parse_text(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise ImportProblem('Cannot decode TXT; save it as UTF-8 or UTF-16 with BOM')


def _key(name):
    name = name.replace('\\', '/').rsplit('/', 1)[-1]
    return (name[:-4] if name.lower().endswith('.p3d') else name).casefold()


def resolve_models(records, directory, recursive=True):
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ImportProblem(f'P3D folder does not exist: {root}')
    names = list(dict.fromkeys(record.model for record in records))
    resolved, unresolved = {}, []
    for name in names:
        normalized = name.replace('\\', '/')
        if '..' in normalized.split('/'):
            raise ImportProblem(f'Parent-directory references are not supported: {name}')
        normalized += '' if normalized.lower().endswith('.p3d') else '.p3d'
        candidate = Path(normalized)
        candidate = candidate if candidate.is_absolute() else root / candidate
        candidate = candidate.resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            resolved[name] = candidate
        else:
            unresolved.append(name)
    if unresolved:
        wanted = {_key(name) for name in unresolved}
        index = {key: [] for key in wanted}
        def on_walk_error(error):
            raise ImportProblem(f'Cannot read P3D folder: {error}')
        for current, directories, files in os.walk(root, onerror=on_walk_error, followlinks=False):
            if not recursive:
                directories[:] = []
            for filename in files:
                if filename.lower().endswith('.p3d') and _key(filename) in wanted:
                    path = (Path(current) / filename).resolve()
                    if path.is_relative_to(root):
                        index[_key(filename)].append(path)
        errors = []
        for name in unresolved:
            matches = sorted(set(index[_key(name)]))
            if not matches:
                errors.append(f'Not found: {name}')
            elif len(matches) != 1:
                errors.append(f'Ambiguous model {name}: ' + ', '.join(str(p.relative_to(root)) for p in matches))
            else:
                resolved[name] = matches[0]
        if errors:
            raise ImportProblem('\n'.join(errors))
    return resolved


def bounds_center(mlod):
    minimum = [math.inf] * 3
    maximum = [-math.inf] * 3
    count = 0
    for lod in mlod.lods:
        for vertex in lod.verts:
            for axis in range(3):
                value = vertex[axis]
                if not math.isfinite(value):
                    raise ImportProblem('P3D contains non-finite vertex coordinates')
                minimum[axis] = min(minimum[axis], value)
                maximum[axis] = max(maximum[axis], value)
            count += 1
    if not count:
        raise ImportProblem('P3D contains no vertices')
    return tuple((a + b) / 2 for a, b in zip(minimum, maximum))


def has_autocenter_zero(mlod):
    geometry = [lod for lod in mlod.lods if lod.resolution.lod == 6]
    for lod in geometry or mlod.lods[:1]:
        for tag in lod.taggs:
            data = tag.data
            if getattr(data, 'key', '').strip().lower() == 'autocenter':
                return str(data.value).strip().lower() in ('0', 'false')
    return False
