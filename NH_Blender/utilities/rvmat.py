"""Super material descriptions and library indexing; no Blender dependency.

Stage semantics: https://community.bistudio.com/wiki/Super_shader
Paths in RVMAT strings are engine paths, not Python escape sequences.
"""
import hashlib
import json
import os
from pathlib import Path
import re

SCHEMA = 1
DEFAULT_ROOT = r"P:\NH_ObjectTextures"
_TOKENS = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:[^"\\]|\\.)*"|[A-Za-z_][\w]*|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?|[^\s]', re.S)


def parse(text):
    """Read nested classes, arrays and scalars, including local class inheritance."""
    text = re.sub(r'^\s*#[^\n]*', '', text, flags=re.M)
    tokens = [m.group() for m in _TOKENS.finditer(text)
              if not m.group().startswith(('//', '/*'))]
    position = 0

    def value(token):
        if token.startswith('"'):
            return token[1:-1].replace(r'\"', '"')
        try:
            return float(token)
        except ValueError:
            return token

    def block():
        nonlocal position
        result = {}
        while position < len(tokens):
            token = tokens[position]
            position += 1
            if token == '}':
                return result
            if token == ';':
                continue
            if token.lower() == 'class':
                name = tokens[position].lower()
                position += 1
                parent = {}
                if tokens[position] == ':':
                    position += 1
                    parent = result.get(tokens[position].lower(), {})
                    position += 1
                if tokens[position] != '{':
                    raise ValueError('Expected class body: ' + name)
                position += 1
                result[name] = dict(parent, **block())
                continue
            key = token.lower()
            if position < len(tokens) and tokens[position] == '[':
                position += 2
            if position >= len(tokens) or tokens[position] != '=':
                continue
            position += 1
            if tokens[position] == '{':
                position += 1
                values = []
                while position < len(tokens) and tokens[position] != '}':
                    if tokens[position] != ',':
                        values.append(value(tokens[position]))
                    position += 1
                position += 1
                result[key] = values
            else:
                result[key] = value(tokens[position])
                position += 1
        return result

    return block()


def read(path):
    raw = Path(path).read_bytes()
    for encoding in ('utf-8-sig', 'cp1251'):
        try:
            return parse(raw.decode(encoding))
        except UnicodeError:
            continue
    raise ValueError('Cannot read RVMAT: ' + str(path))


def resolve_path(raw, rvmat_path='', root='', extra_roots=()):
    if not raw or raw.startswith('#'):
        return ''
    raw = raw.replace('\\', os.sep).replace('/', os.sep)
    if os.path.isfile(raw):
        return os.path.abspath(raw)
    relative = raw[3:] if re.match(r'^[a-zA-Z]:[/\\]', raw) else raw.lstrip('/\\')
    roots = list(extra_roots)
    if root:
        roots += [root, os.path.dirname(root)]
    if rvmat_path:
        roots += [os.path.dirname(rvmat_path)]
        # Locate the drive root from material paths under NH_ObjectTextures/DZ.
        ancestors = list(Path(rvmat_path).parents)
        for ancestor in ancestors:
            if ancestor.name.lower() in ('nh_objecttextures', 'dz'):
                roots.insert(0, str(ancestor.parent))
    for directory in dict.fromkeys(roots):
        candidate = os.path.normpath(os.path.join(directory, relative))
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return ''


def stage(data, number):
    result = dict(data.get('stage' + str(number), {}))
    texgen = data.get('texgen' + str(int(float(result.get('texgen', 0)))), {})
    if texgen:
        result = dict(texgen, **result)
    return result


def procedural_color(raw, default):
    match = re.search(r'color\(([^)]*)\)', raw or '', re.I)
    if not match:
        return tuple(default)
    parts = match.group(1).split(',')[:4]
    try:
        values = tuple(float(x) for x in parts)
        return values + tuple(default[len(values):])
    except ValueError:
        return tuple(default)


def identity(rvmat, color):
    text = '\0'.join(os.path.normcase(os.path.realpath(p)) if p else ''
                     for p in (rvmat, color))
    return hashlib.sha256(text.encode('utf8')).hexdigest()[:24]


def signature(rvmat, color, root, *, data=None, stats_cache=None, search_roots=()):
    data = read(rvmat) if data is None else data
    paths = [rvmat, color]
    for number in range(1, 8):
        raw = stage(data, number).get('texture', '')
        if raw and not raw.startswith('#'):
            paths.append(resolve_path(raw, rvmat, root, search_roots) or raw)
    stats = []
    for path in paths:
        if stats_cache is not None and path in stats_cache:
            stats.append(stats_cache[path])
            continue
        try:
            stat = os.stat(path)
            value = (os.path.realpath(path), stat.st_size, stat.st_mtime_ns)
        except OSError:
            value = (path, None, None)
        stats.append(value)
        if stats_cache is not None:
            stats_cache[path] = value
    return hashlib.sha256(json.dumps([SCHEMA, stats], sort_keys=True).encode()).hexdigest()


def scan(root, overrides=None):
    overrides = overrides or {}
    entries, errors = [], []
    # Shared stage textures need one filesystem fingerprint per scan. This
    # cache lives only during this scan, so subsequent refreshes see changes.
    stats_cache = {}
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(f for f in folders if not f.startswith('.') and f != '__pycache__')
        lookup = {name.lower(): os.path.join(directory, name) for name in files}
        for name in sorted(files):
            if not name.lower().endswith('.rvmat'):
                continue
            path = os.path.join(directory, name)
            try:
                data = read(path)
                if str(data.get('pixelshaderid', '')).lower() != 'super':
                    continue
                stem = os.path.splitext(name)[0]
                bases = [stem]
                normal = stage(data, 1).get('texture', '')
                if normal and not normal.startswith('#'):
                    normal_stem = os.path.splitext(normal.replace('\\', '/').split('/')[-1])[0]
                    bases.append(re.sub(r'_(nohq|no)$', '', normal_stem, flags=re.I))
                candidates = []
                for base in dict.fromkeys(bases):
                    for suffix in ('_co.paa', '_ca.paa', '.paa'):
                        candidate = lookup.get((base + suffix).lower())
                        if candidate and candidate not in candidates:
                            candidates.append(candidate)
                    if candidates:
                        break
                custom = overrides.get(os.path.normcase(os.path.abspath(path)))
                if custom:
                    candidates = [custom]
                folder = os.path.relpath(directory, root).replace('\\', '/')
                folder = '' if folder == '.' else folder
                for color in candidates or ['']:
                    label = stem if len(candidates) <= 1 else stem + ' · ' + Path(color).stem
                    entries.append(dict(id=identity(path, color), name=label, folder=folder,
                                        rvmat=path, color=color, signature=signature(path, color, root,
                                            data=data, stats_cache=stats_cache)))
            except (OSError, ValueError, IndexError, TypeError) as exc:
                errors.append(name + ': ' + str(exc))
    return dict(schema=SCHEMA, root=root, entries=entries, errors=errors)
