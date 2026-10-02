"""Persistent Super graph blueprints with original texture references.

Asset cards remain lightweight. A graph made with preview mips can be restored
with full resolution images without importing Blender data blocks from disk.
"""
import hashlib
import json
import os
import time
import uuid

import bpy

from . import nh_material_shader as shader
from .utilities import rvmat

SCHEMA = 1
NODE_PROPERTIES = ('operation', 'blend_type', 'mode', 'space', 'uv_map',
                   'distribution', 'use_clamp', 'interpolation', 'projection',
                   'projection_blend', 'extension', 'vector_type', 'target')


def _canonical(path):
    return os.path.normcase(os.path.realpath(path)) if path else ''


def _pair(rvmat_path, color_path, search_roots):
    root = shader.texture_root()
    path = rvmat.resolve_path(rvmat_path, root=root, extra_roots=search_roots)
    if not path:
        return None
    color = rvmat.resolve_path(color_path, path, root, search_roots) if color_path else ''
    if color_path and not color:
        return None
    return _canonical(path), _canonical(color), root


def _path(pair):
    from . import nh_materials as browser
    return os.path.join(browser.cache_root(), 'material_data', rvmat.identity(*pair[:2]) + '.json')


def _stat(path):
    try:
        info = os.stat(path)
        return [os.path.realpath(path), info.st_size, info.st_mtime_ns]
    except OSError:
        return [path, None, None]


def _source_paths(pair, references, search_roots):
    path, color, root = pair
    return [path, color] + [rvmat.resolve_path(raw, path, root, search_roots) or raw
                            for raw in references]


def _signature(stats):
    return hashlib.sha256(json.dumps([rvmat.SCHEMA, stats], sort_keys=True).encode()).hexdigest()


def _read(pair, search_roots):
    try:
        with open(_path(pair), encoding='utf8') as file:
            record = json.load(file)
        if (record.get('schema') != SCHEMA or record.get('blender') != list(bpy.app.version[:2])
                or record.get('pair') != list(pair[:2])):
            return None
        stats = [_stat(path) for path in _source_paths(pair, record['references'], search_roots)]
        if stats != record['stats'] or _signature(stats) != record['signature']:
            return None
        return record
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _description(pair, search_roots, data=None):
    path, color, root = pair
    data = rvmat.read(path) if data is None else data
    if str(data.get('pixelshaderid', '')).lower() != 'super':
        return None
    references = []
    for number in range(1, 8):
        raw = rvmat.stage(data, number).get('texture', '')
        if raw and not raw.startswith('#'):
            references.append(raw)
    stats = [_stat(source) for source in _source_paths(pair, references, search_roots)]
    return dict(schema=SCHEMA, blender=list(bpy.app.version[:2]), pair=list(pair[:2]),
                id=rvmat.identity(path, color), rvmat=path, color=color, root=root,
                signature=_signature(stats), references=references, stats=stats,
                search_roots=[_canonical(path) for path in search_roots])


def _write(record, pair):
    target = _path(pair)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    temporary = target + '.%d.%s.tmp' % (os.getpid(), uuid.uuid4().hex)
    try:
        with open(temporary, 'w', encoding='utf8') as file:
            json.dump(record, file, ensure_ascii=False)
        # Windows may briefly deny replacement while another producer commits
        # or a reader closes the previous file. Retry only that sharing race.
        for attempt in range(5):
            try:
                os.replace(temporary, target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.01 * (attempt + 1))
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass


def _entry(record, pair, search_roots):
    return dict(id=record['id'], rvmat=pair[0], color=pair[1], root=pair[2],
                signature=record['signature'],
                search_roots=[_canonical(path) for path in search_roots])


def describe(rvmat_path, color_path='', *, search_roots=()):
    """Describe a Super source without parsing an unchanged cached RVMAT."""
    pair = _pair(rvmat_path, color_path, search_roots)
    if pair is None:
        return None
    record = _read(pair, search_roots)
    if record is None:
        record = _description(pair, search_roots)
        if record is None:
            return None
        try:
            _write(record, pair)
        except OSError:
            pass
    return _entry(record, pair, search_roots)


def _plain(value):
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    return [_plain(item) for item in value]


def _sockets(sockets):
    return [[index, _plain(socket.default_value)] for index, socket in enumerate(sockets)
            if hasattr(socket, 'default_value')]


def _blueprint(material):
    tree = material.node_tree
    nodes = []
    for node in tree.nodes:
        item = dict(kind=node.bl_idname, name=node.name, label=node.label,
                    location=list(node.location), width=node.width, hide=node.hide,
                    properties={key: _plain(getattr(node, key)) for key in NODE_PROPERTIES
                                if hasattr(node, key)},
                    inputs=_sockets(node.inputs), outputs=_sockets(node.outputs))
        if node.type == 'TEX_IMAGE':
            source = node.get('nh_texture_source')
            if not source:
                raise ValueError('A Super texture node has no original source reference')
            item['image'] = dict(source=_canonical(source),
                                 space=node.get('nh_texture_space', 'SRGB'),
                                 base=node.name == 'DayZ Color')
        nodes.append(item)
    links = [[link.from_node.name, list(link.from_node.outputs).index(link.from_socket),
              link.to_node.name, list(link.to_node.inputs).index(link.to_socket)]
             for link in tree.links]
    return dict(nodes=nodes, links=links,
                has_alpha=tree.nodes.get('DayZ Color Alpha') is not None,
                metadata={key: _plain(material[key]) for key in material.keys()
                          if key.startswith('nh_super_')})


def _load_images(graph, pair, search_roots, loader, keep_cache, base_image):
    images = {}
    for item in graph['nodes']:
        reference = item.get('image')
        if reference is None:
            continue
        if reference.get('base') and base_image is not None:
            image = base_image
        else:
            source = rvmat.resolve_path(reference['source'], pair[0], pair[2], search_roots)
            if not source:
                raise ValueError('Cannot find Super texture: ' + reference['source'])
            key = (source, reference['space'])
            if key not in images:
                loaded = loader(source, keep_cache, color_space=reference['space'])
                if loaded[0] is None:
                    raise ValueError('Cannot load Super texture: ' + source)
                images[key] = loaded[0]
            image = images[key]
        images[item['name']] = image
    return images


def _install(material, graph, images):
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    nodes = {}
    for item in graph['nodes']:
        node = tree.nodes.new(item['kind'])
        node.name, node.label = item['name'], item['label']
        node.location, node.width, node.hide = item['location'], item['width'], item['hide']
        for key, value in item['properties'].items():
            setattr(node, key, value)
        for direction in ('inputs', 'outputs'):
            sockets = getattr(node, direction)
            for index, value in item[direction]:
                sockets[index].default_value = value
        if 'image' in item:
            node.image = images[item['name']]
            node['nh_texture_source'] = item['image']['source']
            node['nh_texture_space'] = item['image']['space']
        nodes[item['name']] = node
    for source, output, target, input_index in graph['links']:
        tree.links.new(nodes[source].outputs[output], nodes[target].inputs[input_index])
    from .nh_textures import _enable_preview_material_alpha, _disable_preview_material_alpha
    if graph['has_alpha']:
        _enable_preview_material_alpha(material)
    else:
        _disable_preview_material_alpha(material)
    for key, value in graph['metadata'].items():
        material[key] = value


def restore(material, rvmat_path, color_path='', *, base_image=None, has_alpha=False,
            search_roots=(), keep_cache=True, image_loader=None, force_rebuild=False):
    """Restore a validated cached blueprint; return None for a cache miss."""
    if force_rebuild:
        return None
    pair = _pair(rvmat_path, color_path, search_roots)
    if pair is None:
        return None
    record = _read(pair, search_roots)
    graph = record.get('graph') if record else None
    if not graph or (base_image is not None and bool(has_alpha) != graph['has_alpha']):
        return None
    from .nh_textures import _load_material_preview_image
    loader = image_loader or _load_material_preview_image
    # Load every dependency and validate the graph in a disposable material
    # before changing a material already used by imported geometry.
    images = _load_images(graph, pair, search_roots, loader, keep_cache, base_image)
    temporary = bpy.data.materials.new('NH Blueprint Validation')
    try:
        try:
            _install(temporary, graph, images)
        except (RuntimeError, ValueError):
            # Blender rejects an unknown node type or enum in a damaged JSON
            # recipe. The user's material still has its original graph here.
            return None
        _install(material, graph, images)
    finally:
        bpy.data.materials.remove(temporary)
    material['nh_material_cache_hit'] = True
    return True


def build(material, rvmat_path, color_path='', *, base_image=None, has_alpha=False,
          search_roots=(), keep_cache=True, image_loader=None, force_rebuild=False):
    """Restore or create a Super graph while preserving unrelated material data."""
    arguments = dict(base_image=base_image, has_alpha=has_alpha,
                     search_roots=search_roots, keep_cache=keep_cache,
                     image_loader=image_loader, force_rebuild=force_rebuild)
    try:
        if restore(material, rvmat_path, color_path, **arguments):
            return True
    except (KeyError, TypeError, IndexError, AttributeError):
        # A damaged recipe is a cache miss, never a reason to break an import.
        pass
    result = shader.build(material, rvmat_path, color_path, base_image=base_image,
                          has_alpha=has_alpha, search_roots=search_roots,
                          keep_cache=keep_cache, image_loader=image_loader)
    if not result:
        return False
    # Converted PNGs may already have a datablock loaded in this scene. Source
    # invalidation must also replace that old pixel buffer after cache rewrites.
    for node in material.node_tree.nodes:
        if (node.type == 'TEX_IMAGE' and node.image and node.image.packed_file is None
                and node.image.filepath.lower().endswith('.png')):
            node.image.reload()
    material['nh_material_cache_hit'] = False
    pair = _pair(rvmat_path, color_path, search_roots)
    if pair is not None:
        record = _description(pair, search_roots)
        if record is not None:
            try:
                record['graph'] = _blueprint(material)
            except ValueError:
                # An explicit base_image without an original source path may
                # build successfully, but cannot produce a reusable recipe.
                return True
            try:
                _write(record, pair)
            except OSError:
                pass
    return True
