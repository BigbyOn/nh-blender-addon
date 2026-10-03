"""Terrain Builder text parsing and model lookup; no Blender dependency."""
from dataclasses import dataclass
import csv
import math
import os
from pathlib import Path
import xml.etree.ElementTree as ET


DEFAULT_TEMPLATES_DIRECTORY = r'P:\NH_Objects\TemplateLibs'


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


def _template_key(name):
    return name.replace('\\_', '_').strip().casefold()


def _xml_tag(element):
    return element.tag.rsplit('}', 1)[-1]


def _read_templates(directory):
    """Index direct Name/File fields without treating nested metadata as fields."""
    templates = {}

    def on_walk_error(error):
        raise ImportProblem(f'Cannot read template library folder: {error}')

    for current, directories, files in os.walk(directory, onerror=on_walk_error, followlinks=False):
        directories.sort()
        for filename in sorted(files):
            if not filename.lower().endswith('.tml'):
                continue
            library = Path(current) / filename
            try:
                document = ET.fromstring(library.read_bytes())
            except (OSError, ET.ParseError, ValueError, LookupError) as error:
                raise ImportProblem(f'Cannot read template library {library}: {error}') from error
            for element in document.iter():
                if _xml_tag(element) != 'Template':
                    continue
                names = [child.text or '' for child in element if _xml_tag(child) == 'Name']
                model_files = [child.text or '' for child in element if _xml_tag(child) == 'File']
                for name in names:
                    key = _template_key(name)
                    if key:
                        templates.setdefault(key, []).append((name, model_files, library, len(names)))
    return templates


def _path_key(path):
    return os.path.normcase(str(path))


def _template_candidates(path, anchors):
    if path.is_absolute():
        candidates = [path]
    else:
        candidates = [anchor / path for anchor in anchors]
    canonical = {}
    for candidate in candidates:
        candidate = candidate.resolve()
        canonical[_path_key(candidate)] = candidate
    return canonical


def _template_model(name, entries, anchors):
    references = {}
    for template_name, files, library, name_count in entries:
        context = f'Template {template_name!r} in {library}'
        if name_count != 1 or len(files) != 1 or not files[0].strip():
            raise ImportProblem(f'{context}: expected one Name and one non-empty File')
        filename = files[0].strip().replace('\\', '/')
        path = Path(filename)
        if '\x00' in filename or path.drive and not path.is_absolute():
            raise ImportProblem(f'{context}: invalid model File {files[0]!r}')
        if path.suffix.lower() != '.p3d':
            raise ImportProblem(f'{context}: model File must end with .p3d: {files[0]}')
        if '..' in filename.split('/'):
            raise ImportProblem(f'{context}: parent-directory references in File are not supported: {files[0]}')
        signature = os.path.normcase(os.path.normpath(filename))
        references.setdefault(signature, (path, files[0].strip(), library))
    candidates_by_reference, matches_by_reference = [], []
    for path, filename, library in references.values():
        try:
            candidates = _template_candidates(path, anchors)
            matches = {key: candidate for key, candidate in candidates.items() if candidate.is_file()}
        except (OSError, ValueError) as error:
            raise ImportProblem(f'Template {name!r}, File "{filename}" in {library}: {error}') from error
        candidates_by_reference.append(set(candidates))
        matches_by_reference.append(matches)
    # Absolute and game-relative spellings can name the same canonical file.
    if any(matches_by_reference):
        same_target = all(set(matches) == set(matches_by_reference[0]) for matches in matches_by_reference)
    else:
        same_target = bool(set.intersection(*candidates_by_reference))
    if not same_target:
        details = '; '.join(f'{filename} ({library})' for _, filename, library in references.values())
        raise ImportProblem(f'Conflicting template {name!r}: {details}')
    _, filename, library = next(iter(references.values()))
    matches = matches_by_reference[0]
    if not matches:
        raise ImportProblem(f'Not found for template {name!r}: File "{filename}" in {library}')
    if len(matches) != 1:
        raise ImportProblem(f'Ambiguous template {name!r}, File "{filename}" in {library}: '
                            + ', '.join(str(candidate) for candidate in sorted(matches.values())))
    return next(iter(matches.values()))


def resolve_models(records, directory, recursive=True, *, templates_directory=''):
    selected_root = Path(directory).absolute()
    root = selected_root.resolve()
    if not root.is_dir():
        raise ImportProblem(f'P3D folder does not exist: {root}')
    names = list(dict.fromkeys(record.model for record in records))
    resolved, unresolved = {}, []
    templates_root = Path(templates_directory).absolute() if templates_directory else None
    missing_templates = templates_root is not None and not templates_root.is_dir()
    templates = _read_templates(templates_root) if templates_root is not None and not missing_templates else {}
    # Preserve virtual drive/junction ancestors as well as the physical paths.
    anchor_roots = (selected_root, root, templates_root, templates_root.resolve() if templates_root else None)
    anchors = dict.fromkeys(anchor for base in anchor_roots if base is not None for anchor in (base, *base.parents))
    for name in names:
        entries = templates.get(_template_key(name))
        if entries:
            resolved[name] = _template_model(name, entries, anchors)
            continue
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
            if missing_templates:
                errors.append(f'Template library folder does not exist: {templates_root}')
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
