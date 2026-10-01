"""DayZ Super preview shared by the browser and P3D import (Blender 4.0+).

Blender BSDFs approximate the game lighting. SMDI is specular/gloss, never
metallic/roughness. Stage6 uses conductor Fresnel; Stage7 is a sphere map.
"""
import os
import re

import bpy

from .utilities import rvmat


def texture_root():
    override = os.environ.get('NH_MATERIALS_TEXTURE_ROOT', '')
    if override:
        return os.path.abspath(override)
    addon = bpy.context.preferences.addons.get(__package__)
    value = getattr(getattr(addon, 'preferences', None), 'nh_textures_folder', '')
    return os.path.abspath(bpy.path.abspath(value or rvmat.DEFAULT_ROOT))


def _color(value, default):
    result = list(value or default)
    return tuple(result[:4] + list(default[len(result):]))


def build(material, rvmat_path, color_path='', *, base_image=None, has_alpha=False,
          search_roots=(), keep_cache=True, image_loader=None):
    from .nh_textures import _load_material_preview_image, _enable_preview_material_alpha, _disable_preview_material_alpha
    load_image = image_loader or _load_material_preview_image
    root = texture_root()
    path = rvmat.resolve_path(rvmat_path, root=root, extra_roots=search_roots)
    if not path:
        return False
    data = rvmat.read(path)
    if str(data.get('pixelshaderid', '')).lower() != 'super':
        return False

    # Decode required images before touching a user's existing shader.
    loaded = {}
    missing = []
    for number in (1, 2, 3, 4, 5, 7):
        raw = rvmat.stage(data, number).get('texture', '')
        if raw and not raw.startswith('#'):
            resolved = rvmat.resolve_path(raw, path, root, search_roots)
            image = None
            if resolved:
                image = load_image(resolved, keep_cache,
                    color_space='SRGB' if number in (3, 7) else 'DATA')[0]
            if image is None:
                missing.append(raw)
            loaded[number] = image
    if base_image is None and color_path:
        resolved = rvmat.resolve_path(color_path, path, root, search_roots)
        base_image, has_alpha, *_ = load_image(resolved or color_path, keep_cache)
        if base_image is None:
            raise ValueError('Cannot load color texture: ' + color_path)
    if missing:
        raise ValueError('Cannot load Super textures: ' + ', '.join(missing))

    material.use_nodes = True
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    nodes.clear()

    def node(kind, name):
        result = nodes.new(kind)
        result.name = result.label = name
        return result

    def feed(socket, value):
        if isinstance(value, bpy.types.NodeSocket):
            links.new(value, socket)
        else:
            socket.default_value = value

    def arithmetic(operation, a, b=0, name=''):
        result = node('ShaderNodeMath', name or operation)
        result.operation = operation
        feed(result.inputs[0], a)
        feed(result.inputs[1], b)
        return result.outputs[0]

    def mix(operation, a, b, factor=1.0, name=''):
        result = node('ShaderNodeMixRGB', name or operation)
        result.blend_type = operation
        feed(result.inputs[0], factor)
        feed(result.inputs[1], a)
        feed(result.inputs[2], b)
        return result.outputs[0]

    def separate(color, name):
        result = node('ShaderNodeSeparateColor', name)
        result.mode = 'RGB'
        feed(result.inputs[0], color)
        return result.outputs

    coordinates = node('ShaderNodeTexCoord', 'Coordinates')

    def mapping(number):
        description = rvmat.stage(data, number)
        source = description.get('uvsource', 'tex').lower()
        uv = coordinates.outputs['UV']
        if source == 'tex1':
            uv_node = node('ShaderNodeUVMap', 'UV set 1 (second layer)')
            uv_node.uv_map = 'NH_UV1'
            uv = uv_node.outputs[0]
        transform = description.get('uvtransform', {})
        if not transform:
            return uv
        components = separate(uv, 'Stage%d UV components' % number)
        aside = transform.get('aside', [1, 0, 0])
        up = transform.get('up', [0, 1, 0])
        offset = transform.get('pos', [0, 0, 0])
        combined = node('ShaderNodeCombineXYZ', 'Stage%d UV transform' % number)
        for axis in range(3):
            component = arithmetic('ADD', arithmetic('MULTIPLY', components[0], aside[axis]),
                                   arithmetic('MULTIPLY', components[1], up[axis]))
            feed(combined.inputs[axis], arithmetic('ADD', component, offset[axis]))
        return combined.outputs[0]

    def stage_color(number, default, vector=None):
        description = rvmat.stage(data, number)
        image = loaded.get(number)
        if image is None:
            color = node('ShaderNodeRGB', 'Stage%d constant' % number)
            color.outputs[0].default_value = rvmat.procedural_color(description.get('texture', ''), default)
            return color.outputs[0], color.outputs[0].default_value[3]
        texture = node('ShaderNodeTexImage', 'Stage%d %s' % (number, os.path.basename(image.filepath)))
        texture.image = image
        feed(texture.inputs['Vector'], vector if vector is not None else mapping(number))
        return texture.outputs['Color'], texture.outputs['Alpha']

    if base_image:
        texture = node('ShaderNodeTexImage', 'DayZ Color')
        texture.image = base_image
        feed(texture.inputs['Vector'], coordinates.outputs['UV'])
        base, alpha = texture.outputs['Color'], texture.outputs['Alpha']
    else:
        base, alpha = (0.5, 0.5, 0.5, 1), 1.0
    detail, _ = stage_color(2, (0.5, 0.5, 0.5, 1))
    base = mix('MULTIPLY', base, mix('MULTIPLY', detail, (2, 2, 2, 1)), name='Detail × 2')
    macro, macro_alpha = stage_color(3, (0, 0, 0, 0))
    base = mix('MULTIPLY', base, macro, macro_alpha, 'Macro')
    ambient_shadow, _ = stage_color(4, (1, 1, 1, 1))
    ao = separate(ambient_shadow, 'AmbientShadow G')[1]
    base = mix('MULTIPLY', base, ao, name='Ambient shadow approximation')
    lighting_tint = mix('MIX', _color(data.get('diffuse'), (1, 1, 1, 1)),
                        _color(data.get('ambient'), (1, 1, 1, 1)), 0.25,
                        'RVMAT Diffuse / Ambient approximation')
    base = mix('MULTIPLY', base, lighting_tint, name='RVMAT lighting color')

    normal_color, _ = stage_color(1, (0.5, 0.5, 1, 1))
    normal_channels = separate(normal_color, 'NOHQ channels')
    converted = node('ShaderNodeCombineColor', 'DayZ normal: invert Y')
    converted.mode = 'RGB'
    feed(converted.inputs[0], normal_channels[0])
    feed(converted.inputs[1], arithmetic('SUBTRACT', 1, normal_channels[1]))
    feed(converted.inputs[2], normal_channels[2])
    normal = node('ShaderNodeNormalMap', 'DayZ Normal')
    normal.space = 'TANGENT'
    feed(normal.inputs['Color'], converted.outputs[0])

    smdi, _ = stage_color(5, (1, 0, 0, 1))
    channels = separate(smdi, 'SMDI: G Spec / B Gloss')
    exponent = arithmetic('MAXIMUM', arithmetic('MULTIPLY', channels[2], max(1.0, data.get('specularpower', 30))), 1)
    roughness = arithmetic('SQRT', arithmetic('DIVIDE', 2, arithmetic('ADD', exponent, 2)), name='Gloss to GGX roughness')

    # Complex-IOR Fresnel (unpolarized conductor reflectance), N/K from Stage6.
    fresnel = re.search(r'fresnel\(\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)\s*\)',
                        rvmat.stage(data, 6).get('texture', ''), re.I)
    eta, k = (float(v) for v in fresnel.groups()) if fresnel else (1.5, 0.0)
    geometry = node('ShaderNodeNewGeometry', 'Fresnel view')
    dot = node('ShaderNodeVectorMath', 'N · view')
    dot.operation = 'DOT_PRODUCT'
    feed(dot.inputs[0], normal.outputs['Normal'])
    feed(dot.inputs[1], geometry.outputs['Incoming'])
    c = arithmetic('ABSOLUTE', dot.outputs['Value'])
    c2 = arithmetic('MULTIPLY', c, c)
    s2 = arithmetic('SUBTRACT', 1, c2)
    t0 = arithmetic('SUBTRACT', eta * eta - k * k, s2)
    ab = arithmetic('SQRT', arithmetic('ADD', arithmetic('MULTIPLY', t0, t0), 4 * eta * eta * k * k))
    a = arithmetic('SQRT', arithmetic('MULTIPLY', arithmetic('ADD', ab, t0), 0.5))
    t1 = arithmetic('ADD', ab, c2)
    t2 = arithmetic('MULTIPLY', arithmetic('MULTIPLY', a, c), 2)
    rs = arithmetic('DIVIDE', arithmetic('SUBTRACT', t1, t2), arithmetic('MAXIMUM', arithmetic('ADD', t1, t2), 1e-6))
    t3 = arithmetic('ADD', arithmetic('MULTIPLY', c2, ab), arithmetic('MULTIPLY', s2, s2))
    t4 = arithmetic('MULTIPLY', t2, s2)
    rp = arithmetic('MULTIPLY', rs, arithmetic('DIVIDE', arithmetic('SUBTRACT', t3, t4), arithmetic('MAXIMUM', arithmetic('ADD', t3, t4), 1e-6)))
    reflectance = arithmetic('MULTIPLY', arithmetic('ADD', rs, rp), 0.5, 'Stage6 Fresnel N/K')
    specular = mix('MULTIPLY', _color(data.get('specular'), (1, 1, 1, 1)),
                   arithmetic('MULTIPLY', channels[1], reflectance), name='DayZ Specular')

    diffuse = node('ShaderNodeBsdfDiffuse', 'DayZ Diffuse')
    feed(diffuse.inputs['Color'], base)
    feed(diffuse.inputs['Normal'], normal.outputs['Normal'])
    glossy = node('ShaderNodeBsdfGlossy', 'DayZ Specular BSDF')
    feed(glossy.inputs['Color'], specular)
    feed(glossy.inputs['Roughness'], roughness)
    feed(glossy.inputs['Normal'], normal.outputs['Normal'])
    added = node('ShaderNodeAddShader', 'Diffuse + Specular')
    feed(added.inputs[0], diffuse.outputs[0])
    feed(added.inputs[1], glossy.outputs[0])
    surface = added.outputs[0]

    # Static game sphere-map reflection, separate from Blender scene lighting.
    if loaded.get(7):
        reflection = coordinates.outputs['Reflection']
        shifted = node('ShaderNodeVectorMath', 'Sphere map reflection + Z')
        shifted.operation = 'ADD'
        feed(shifted.inputs[0], reflection)
        shifted.inputs[1].default_value = (0, 0, 1)
        length = node('ShaderNodeVectorMath', 'Sphere map length')
        length.operation = 'LENGTH'
        feed(length.inputs[0], shifted.outputs[0])
        scaled = node('ShaderNodeVectorMath', 'Sphere map UV')
        scaled.operation = 'SCALE'
        feed(scaled.inputs[0], reflection)
        feed(scaled.inputs['Scale'], arithmetic('DIVIDE', 0.5, arithmetic('MAXIMUM', length.outputs['Value'], 1e-6)))
        offset = node('ShaderNodeVectorMath', 'Sphere map center')
        offset.operation = 'ADD'
        feed(offset.inputs[0], scaled.outputs[0])
        offset.inputs[1].default_value = (0.5, 0.5, 0)
        environment, _ = stage_color(7, (0, 0, 0, 1), offset.outputs[0])
        emission = node('ShaderNodeEmission', 'Stage7 Environment')
        feed(emission.inputs['Color'], mix('MULTIPLY', environment, specular))
        added = node('ShaderNodeAddShader', 'Static environment reflection')
        feed(added.inputs[0], surface)
        feed(added.inputs[1], emission.outputs[0])
        surface = added.outputs[0]

    emissive = _color(data.get('emmisive', data.get('emissive')), (0, 0, 0, 1))
    forced = _color(data.get('forceddiffuse'), (0, 0, 0, 1))
    if max(emissive[:3] + forced[:3]) > 0:
        emission = node('ShaderNodeEmission', 'Emissive + ForcedDiffuse')
        feed(emission.inputs['Color'], mix('ADD', mix('MULTIPLY', base, emissive), mix('MULTIPLY', base, forced)))
        added = node('ShaderNodeAddShader', 'RVMAT Emission')
        feed(added.inputs[0], surface)
        feed(added.inputs[1], emission.outputs[0])
        surface = added.outputs[0]
    if has_alpha:
        transparent = node('ShaderNodeBsdfTransparent', 'Alpha transparency')
        mixed = node('ShaderNodeMixShader', 'DayZ Color Alpha')
        feed(mixed.inputs[0], alpha)
        feed(mixed.inputs[1], transparent.outputs[0])
        feed(mixed.inputs[2], surface)
        surface = mixed.outputs[0]
        _enable_preview_material_alpha(material)
    else:
        _disable_preview_material_alpha(material)
    output = node('ShaderNodeOutputMaterial', 'DayZ Super Output')
    feed(output.inputs['Surface'], surface)
    # Arrange a readable graph by dependency depth, retaining meaningful labels.
    depths = {}
    def depth(item):
        if item in depths:
            return depths[item]
        depths[item] = 0
        depths[item] = 1 + max((depth(link.from_node) for socket in item.inputs for link in socket.links), default=-1)
        return depths[item]
    rows = {}
    for item in nodes:
        column = depth(item)
        row = rows.get(column, 0)
        item.location = (column * 240, -row * 180)
        rows[column] = row + 1
    material['nh_super_rvmat'] = path
    material['nh_super_color'] = color_path
    material['nh_super_specular_power'] = data.get('specularpower', 30)
    material['nh_super_ambient'] = _color(data.get('ambient'), (1, 1, 1, 1))
    material['nh_super_preview'] = 'Blender approximation: GGX gloss, diffuse/ambient lighting, ambient shadow, static sphere-map reflection'
    return True


def prepare_uvs(obj):
    """Provide the named second UV layer without changing existing coordinates."""
    layers = getattr(getattr(obj, 'data', None), 'uv_layers', None)
    if layers is None or not len(layers) or layers.get('NH_UV1'):
        return
    if len(layers) > 1:
        source = layers[1]
    else:
        source = layers[0]
    values = [tuple(item.uv) for item in source.data]
    previous = layers.active_index
    target = layers.new(name='NH_UV1')
    for item, uv in zip(target.data, values):
        item.uv = uv
    layers.active_index = previous
