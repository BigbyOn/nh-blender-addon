# Processing functions to import multiple LODs as meshes
# from the MLOD P3D format. The actual file handling is implemented
# in the data_p3d module.


import time
import os

import bpy
import bmesh
import mathutils

from . import data_p3d as p3d
from . import import_paa
from ..utilities import generic as utils
from ..utilities import lod as lodutils
from ..utilities import compat as computils
from ..utilities import proxy as proxyutils
from ..utilities import flags as flagutils
from ..utilities import structure as structutils
from ..utilities import data
from ..utilities.logger import ProcessLogger


def categorize_lods(operator, context, mlod):
    categories = {}
    lods = []

    root = context.scene.collection
    if operator.enclose:
        root = bpy.data.collections.new(name=os.path.basename(operator.filepath))
        bpy.context.scene.collection.children.link(root)
    
    if operator.groupby == 'NONE':
        categories["None"] = [0, root]
        lods = [[*lod.resolution.get(), 0] for lod in mlod.lods]

    else:
        for lod in mlod.lods:
            lod_index, lod_resolution = lod.resolution.get()
            group_dict = data.lod_groups[operator.groupby]
            group_name = group_dict[lod_index]

            if group_name not in categories:
                new_category = bpy.data.collections.new(name=group_name)
                root.children.link(new_category)
                categories[group_name] = [len(categories), new_category]
            
            lods.append([lod_index, lod_resolution, categories[group_name][0]])

    return [cat[1] for cat in categories.values()], lods


def enable_material_alpha(material):
    try:
        material.blend_method = 'HASHED'
    except Exception:
        pass

    try:
        material.shadow_method = 'HASHED'
    except Exception:
        pass


def resolve_texture_path(texture_path):
    texture_path = texture_path.strip()
    if texture_path == "":
        return ""

    candidates = []

    if os.path.isabs(texture_path) or texture_path.startswith("//"):
        candidate = utils.abspath(texture_path)
        candidates.append(candidate)

        if os.path.splitext(candidate)[1] == "":
            candidates.append(candidate + ".paa")

    candidates.append(utils.restore_absolute(texture_path))
    candidates.append(utils.restore_absolute(texture_path, ".paa"))

    checked = set()
    for candidate in candidates:
        if candidate == "":
            continue

        if not os.path.isabs(candidate) and not candidate.startswith("//"):
            continue

        candidate = os.path.abspath(bpy.path.abspath(candidate))
        key = candidate.lower()
        if key in checked:
            continue

        checked.add(key)
        if os.path.isfile(candidate):
            return candidate

    return ""


def load_texture_image(texture_path):
    resolved_path = resolve_texture_path(texture_path)
    if resolved_path == "":
        return None, False, ""

    if os.path.splitext(resolved_path)[1].lower() == ".paa":
        try:
            image, tex = import_paa.load_file(resolved_path, 'SRGB')
        except Exception:
            return None, False, resolved_path

        if image is None:
            return None, False, resolved_path

        has_alpha = tex.type == import_paa.paa.PAA_Type.DXT5 if tex is not None else image.alpha_mode != 'NONE'
        return image, has_alpha, resolved_path

    try:
        image = bpy.data.images.load(resolved_path, check_existing=True)
    except Exception:
        return None, False, resolved_path

    return image, image.alpha_mode != 'NONE', resolved_path


def setup_material_nodes(material, logger=None):
    props = material.a3ob_properties_material

    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    node_output = nodes.new("ShaderNodeOutputMaterial")
    node_output.location = (300, 0)

    node_shader = nodes.new("ShaderNodeBsdfPrincipled")
    node_shader.location = (0, 0)
    links.new(node_shader.outputs["BSDF"], node_output.inputs["Surface"])

    if props.texture_type == 'COLOR':
        node_color = nodes.new("ShaderNodeRGB")
        node_color.location = (-250, 0)
        node_color.outputs[0].default_value = (props.color_value[0], props.color_value[1], props.color_value[2], 1.0)
        links.new(node_color.outputs[0], node_shader.inputs["Base Color"])

        node_shader.inputs["Alpha"].default_value = props.color_value[3]
        if props.color_value[3] < 1.0:
            enable_material_alpha(material)

        return 'COLOR'

    if props.texture_type != 'TEX' or props.texture_path.strip() == "":
        return 'DEFAULT'

    image, has_alpha, resolved_path = load_texture_image(props.texture_path)
    if image is None:
        if logger is not None:
            logger.step(">> Material preview texture not found: %s" % props.texture_path)
        return 'MISSING'

    node_texture = nodes.new("ShaderNodeTexImage")
    node_texture.location = (-300, 0)
    node_texture.image = image
    node_texture.label = os.path.basename(resolved_path)
    links.new(node_texture.outputs["Color"], node_shader.inputs["Base Color"])

    if has_alpha:
        links.new(node_texture.outputs["Alpha"], node_shader.inputs["Alpha"])
        enable_material_alpha(material)

    return 'TEXTURE'


def create_blender_materials(lookup, absolute, with_preview=False, logger=None):
    materials = []
    preview_loaded = 0
    preview_missing = 0
    
    for texture, material in lookup.keys():
        material_name = "P3D: %s :: %s" % (os.path.basename(texture), os.path.basename(material))
        if texture == "" and material == "":
            material_name = "P3D: no material"
            
        new_mat = bpy.data.materials.new(material_name)
        new_mat.a3ob_properties_material.from_p3d(texture.strip(), material.strip(), absolute)

        if with_preview:
            status = setup_material_nodes(new_mat, logger)
            if status == 'TEXTURE':
                preview_loaded += 1
            elif status == 'MISSING':
                preview_missing += 1

        materials.append(new_mat)
        
    return materials, preview_loaded, preview_missing


def process_normals(mesh, lod):
    loop_normals = lod.loop_normals()
    
    if len(mesh.loops) == len(loop_normals):
        mesh.normals_split_custom_set(loop_normals)
        return True

    return False


def process_sharps(bm, lod):
    data = None
    for tagg in lod.taggs:
        if tagg.name == "#SharpEdges#":
            data = tagg.data
            break
    
    if not data:
        return
    
    for edge in bm.edges:
        edge.smooth = True
    
    for item in data.edges:
        edge = bm.edges.get([bm.verts[item[0]], bm.verts[item[1]]])
        if edge:
            edge.smooth = False


def process_uvsets(bm, lod):
    uvsets = lod.uvsets()
        
    for idx in uvsets:
        uvs = uvsets[idx]
        layer_name = "UVSet %d" % idx
        layer = bm.loops.layers.uv.get(layer_name)
        if not layer:
            layer = bm.loops.layers.uv.new(layer_name) 
        
        for face in bm.faces:
            for loop in face.loops:
                loop[layer].uv = uvs[loop.index]
    
    return len(uvsets)


def process_selections(bm, lod):
    bm.verts.layers.deform.verify()
    layer = bm.verts.layers.deform.active
    
    selection_names = []
    count_selections = 0
    for tagg in lod.taggs:
        if tagg.name[0] == tagg.name[-1] == "#":
            continue
        
        weights = tagg.data.weight_verts
        for idx, weight in weights:
            bm.verts[idx][layer][count_selections] = weight

        count_selections += 1
        
        selection_names.append(tagg.name)
    
    return selection_names


def process_materials(operator, mesh, bm, lod, materials, materials_lookup):
    slot_indices = []
    material_indices = []
    if operator.sections == 'PRESERVE':
        slot_indices, material_indices = lod.get_sections(materials_lookup)
    elif operator.sections == 'MERGE':
        slot_indices, material_indices = lod.get_sections_merged(materials_lookup)
    
    for i, idx in enumerate(slot_indices):
        bm.faces[i].material_index = idx
    
    for idx in material_indices:
        mesh.materials.append(materials[idx])


def process_mass(bm, lod):
    data = None
    for tagg in lod.taggs:
        if tagg.name == "#Mass#":
            data = tagg.data
            break
    
    if not data:
        return
    
    layer = bm.verts.layers.float.new("a3ob_mass")
    for vert in bm.verts:
        vert[layer] = data.masses[vert.index]


def process_properties(obj, lod):
    for tagg in lod.taggs:
        if tagg.name != "#Property#":
            continue
        
        new_prop = obj.a3ob_properties_object.properties.add()
        new_prop.name = tagg.data.key
        new_prop.value = tagg.data.value


def process_flag_groups_vertex(obj, bm, lod):
    groups, values = lod.flag_groups_vertex()

    layer = flagutils.get_layer_flags_vertex(bm)
    for vert in bm.verts:
        vert[layer] = values[vert.index]
    
    for i, grp in enumerate(groups):
        new_group = obj.a3ob_properties_object_flags.vertex.add()
        new_group.name = ("Group %d" % i )
        new_group.set_flag(grp)


def process_flag_groups_face(obj, bm, lod):
    groups, values = lod.flag_groups_face()

    layer = flagutils.get_layer_flags_face(bm)
    for face in bm.faces:
        face[layer] = values[face.index]
    
    for i, grp in enumerate(groups):
        new_group = obj.a3ob_properties_object_flags.face.add()
        new_group.name = ("Group %d" % i)
        new_group.set_flag(grp)


# Align the object coordinate system with the proxy coordinates.
# https://mrcmodding.gitbook.io/home/documents/proxy-coordinates
def transform_proxy(obj):
    if len(obj.data.vertices) < 3:
        return
    
    rotation_matrix = proxyutils.get_transform_rotation(obj)
    obj.data.transform(rotation_matrix)
    obj.matrix_world @= rotation_matrix.inverted()
    
    translate = mathutils.Matrix.Translation(-proxyutils.find_axis_vertices(obj.data)[0].co)
    obj.data.transform(translate)
    obj.matrix_world @= translate.inverted()
    
    obj.data.update()


def process_proxies(operator, obj, proxy_lookup, empty_material):
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')

    for key in proxy_lookup:
        vgroup = obj.vertex_groups.get(key)
        if not vgroup:
            continue
            
        obj.vertex_groups.active = vgroup
        computils.call_operator_ctx(bpy.ops.object.vertex_group_select)

        try:
            bpy.ops.mesh.separate(type='SELECTED')
            obj.vertex_groups.remove(vgroup)
        except:
            pass

    bpy.ops.object.mode_set(mode='OBJECT')
    
    proxy_objects = [proxy for proxy in bpy.context.selected_objects if proxy != obj]
        
    bpy.ops.object.select_all(action='DESELECT')
    
    if operator.proxy_action == 'CLEAR':
        for obj in proxy_objects:
            bpy.data.meshes.remove(obj.data)

    elif operator.proxy_action == 'SEPARATE':
        for i, proxy_obj in enumerate(proxy_objects):
            proxy_obj.a3ob_properties_object.is_a3_lod = False

            transform_proxy(proxy_obj)
            structutils.cleanup_vertex_groups(proxy_obj) # need to remove the unused groups leftover from the separation

            vgroup = proxy_obj.vertex_groups.get("@proxy_%d" % i)
            if not vgroup:
                continue
            
            path, index = proxy_lookup[vgroup.name]
            proxy_obj.vertex_groups.remove(vgroup)
            proxy_obj.a3ob_properties_object_proxy.proxy_path = utils.restore_absolute(path, ".p3d") if operator.absolute_paths else path
            proxy_obj.a3ob_properties_object_proxy.proxy_index = index

            proxy_obj.a3ob_properties_object_flags.vertex.clear()
            proxy_obj.a3ob_properties_object_flags.face.clear()

            proxy_obj.display_type = 'WIRE'
            proxy_obj.show_name = True

            bm = bmesh.new()
            bm.from_mesh(proxy_obj.data)

            flagutils.clear_layer_flags_vertex(bm)
            flagutils.clear_layer_flags_face(bm)

            bm.to_mesh(proxy_obj.data)
            bm.free()

            proxy_obj.a3ob_properties_object_proxy.is_a3_proxy = True
            proxy_obj.data.materials.clear()
            if empty_material is not None:
                proxy_obj.data.materials.append(empty_material)
            proxy_obj.parent = obj
            name = "proxy: %s" % proxy_obj.a3ob_properties_object_proxy.get_name()
            proxy_obj.name = name
            proxy_obj.data.name = name

            utils.clear_uvs(proxy_obj)


def translate_selections(obj):
    for group in obj.vertex_groups:
        group.name = data.translations_czech_english.get(group.name.lower(), group.name)


def process_lod(operator, logger, lod, materials, materials_lookup, categories, lod_links):
    lod_index = lod_links[0]
    lod_resolution = lod_links[1]
    lod_name = lodutils.format_lod_name(lod_index, lod_resolution)

    logger.start_subproc("File report:")
    logger.step("Name: %s" % lod_name)
    logger.step("Signature: %d" % lod.resolution.source)
    logger.step("Type: P3DM")
    logger.step("Version: 28.256")
    logger.step("Vertices: %d" % len(lod.verts))
    logger.step("Normals: %d" % len(lod.normals))
    logger.step("Faces: %d" % len(lod.faces))
    logger.step("Taggs: %d" % (len(lod.taggs) + 1))
    logger.end_subproc()

    logger.start_subproc("Processing data:")
    
    mesh = bpy.data.meshes.new(lod_name)
    
    mesh.from_pydata(*lod.pydata())
    mesh.update(calc_edges=True)
    
    obj = bpy.data.objects.new(lod_name, mesh)

    logger.step("Created raw mesh")

    # Setup LOD properties
    object_props = obj.a3ob_properties_object
    object_props.lod = str(lod_index)
    
    if lod_index != data.lod_unknown:
        object_props.resolution = lod_resolution
    else:
        object_props.resolution_float = lod_resolution

    if lod_index not in data.lod_shadows:
        for face in mesh.polygons:
            face.use_smooth = True
        
        computils.mesh_auto_smooth(mesh)
    
    if 'NORMALS' in operator.additional_data and lod_index in data.lod_visuals:
        if process_normals(mesh, lod):
            logger.step("Applied split normals")
        else:
            logger.step("Could not apply split normals")
    
    # Process TAGGs
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    
    process_sharps(bm, lod)
    logger.step("Marked sharp edges")
    
    if 'UV' in operator.additional_data:
        count_uv = process_uvsets(bm, lod)
        logger.step("Added UV channels: %d" % count_uv)
    
    selection_names = []
    proxy_lookup = {}
    if 'SELECTIONS' in operator.additional_data:
        proxy_lookup = lod.proxies_to_placeholders()
        selection_names = process_selections(bm, lod)
        logger.step("Added vertex groups: %d" % (len(selection_names)))
    
    if 'MATERIALS' in operator.additional_data:
        process_materials(operator, mesh, bm, lod, materials, materials_lookup)
        logger.step("Assigned materials")
    
    if lod_index == p3d.P3D_LOD_Resolution.GEOMETRY and 'MASS' in operator.additional_data:
        process_mass(bm, lod)
        logger.step("Added vertex masses")
    
    process_properties(obj, lod)
    logger.step("Added named properties")

    if 'FLAGS' in operator.additional_data:
        process_flag_groups_vertex(obj, bm, lod)
        logger.step("Assigned vertex flag groups")

        process_flag_groups_face(obj, bm, lod)
        logger.step("Assigned face flag groups")
        
    for name in selection_names:
        obj.vertex_groups.new(name=name)

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    uv_layer = mesh.uv_layers.get("UVSet 0")
    if uv_layer is not None:
        uv_layer.active = True
        uv_layer.active_render = True

    collection = categories[lod_links[2]]
    collection.objects.link(obj)

    if operator.validate_meshes:
        mesh.validate(clean_customdata=False)
    
    if operator.translate_selections:
        translate_selections(obj)
        logger.step("Translated selections to english")
    
    if operator.cleanup_empty_selections:
        structutils.cleanup_vertex_groups(obj)
        logger.step("Cleaned up vertex groups")

    if operator.proxy_action != 'NOTHING' and 'SELECTIONS' in operator.additional_data:
        empty_material = materials[0] if materials is not None else None
        process_proxies(operator, obj, proxy_lookup, empty_material)
        logger.step("Processed proxies: %d" % len(proxy_lookup))

    object_props.is_a3_lod = True

    logger.end_subproc()

    return obj


def read_file(operator, context, file):
    # If something is left selected in the scene, the proxy separation trips up with the operators.
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
        
    context.view_layer.objects.active = None

    wm = context.window_manager
    wm.progress_begin(0, 1000)
    wm.progress_update(0)
    logger = ProcessLogger()
    logger.start_subproc("P3D import from %s" % operator.filepath)

    if operator.first_lod_only:
        logger.step("Importing 1st LOD only")
    
    time_read_start = time.time()
    mlod = p3d.P3D_MLOD.read(file, operator.first_lod_only)
    logger.step("File reading done in %f sec" % (time.time() - time_read_start))

    logger.step("File version: %d" % mlod.version)
    logger.step("Number of read LODs: %d" % len(mlod.lods))
    
    categories, lod_links = categorize_lods(operator, context, mlod)

    if not operator.additional_data_allowed:
        operator.additional_data = set()
    
    materials = None
    materials_lookup = None
    if 'MATERIALS' in operator.additional_data:
        materials_lookup = mlod.get_materials()
        load_textures = getattr(operator, "load_textures", True)
        materials, preview_loaded, preview_missing = create_blender_materials(materials_lookup, operator.absolute_paths, load_textures, logger)
        logger.step("Number of unique materials: %d" % len(materials))
        if load_textures:
            logger.step("Material previews loaded: %d" % preview_loaded)
            if preview_missing > 0:
                logger.step("Material previews missing: %d" % preview_missing)

    logger.start_subproc("Processing mesh data:")

    lod_objects = []
    for i, lod in enumerate(mlod.lods):
        logger.start_subproc("LOD %d" % (i + 1))

        lod_objects.append(process_lod(operator, logger, lod, materials, materials_lookup, categories, lod_links[i]))

        logger.end_subproc(True)
        wm.progress_update(i + 1)

    logger.end_subproc()
    logger.end_subproc()
    wm.progress_end()
    logger.step("P3D import finished in %f sec" % (time.time() - logger.times.pop()))
    
    return lod_objects
