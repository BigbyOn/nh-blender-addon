"""Disposable preview worker: small texture mips and lightweight asset catalogs."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from NH_Blender import nh_materials as browser
from NH_Blender import nh_material_shader as shader
from NH_Blender.utilities import rvmat
ASSET_SCHEMA = 2
GROUP_SIZE = 64


def read_json(path, default):
    try:
        with open(path, encoding='utf8') as file:
            return json.load(file)
    except (OSError, ValueError):
        return default


def studio(engine='EEVEE'):
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = 2
    if engine == 'CYCLES':
        scene.render.engine = 'CYCLES'
        scene.cycles.device = 'CPU'
        scene.cycles.samples = 8
        scene.cycles.use_denoising = True
    else:
        scene.render.engine = 'BLENDER_EEVEE' if bpy.app.version < (4, 2, 0) else 'BLENDER_EEVEE_NEXT'
        if hasattr(scene, 'eevee') and hasattr(scene.eevee, 'taa_render_samples'):
            scene.eevee.taa_render_samples = 32
    scene.render.resolution_x = scene.render.resolution_y = 192
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.film_transparent = True
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.world.use_nodes = True
    scene.world.node_tree.nodes.get('Background').inputs[0].default_value = (0.18, 0.18, 0.18, 1)
    scene.world.node_tree.nodes.get('Background').inputs[1].default_value = 0.65
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    sphere = bpy.context.object
    for face in sphere.data.polygons:
        face.use_smooth = True
    shader.prepare_uvs(sphere)
    camera_data = bpy.data.cameras.new('NH Preview Camera')
    camera = bpy.data.objects.new('NH Preview Camera', camera_data)
    scene.collection.objects.link(camera)
    camera.location = (3.6, -5.0, 2.8)
    camera.rotation_euler = (-camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera_data.type = 'ORTHO'
    camera_data.ortho_scale = 2.45
    scene.camera = camera
    for name, position, energy, size in [('Key', (1,-3,4),450,3), ('Fill',(-3,-2,1),160,4), ('Rim',(2,2,3),300,2)]:
        light_data = bpy.data.lights.new('NH '+name, 'AREA')
        light_data.energy, light_data.size = energy, size
        light = bpy.data.objects.new('NH '+name, light_data)
        scene.collection.objects.link(light)
        light.location = position
        light.rotation_euler = (-light.location).to_track_quat('-Z','Y').to_euler()
    return sphere


def asset_groups(entries, preserve=False):
    folders, groups = {}, {}
    for entry in entries:
        folders.setdefault(entry['folder'], []).append(entry)
    for folder, items in folders.items():
        items.sort(key=lambda entry: (entry['name'].casefold(), entry['id']))
        names = set()
        if preserve:
            # Importing one pair must not reshuffle established library cards.
            # New entries fill an existing group or create another small file.
            for entry in items:
                filename = entry.get('asset_file', '')
                if filename:
                    groups.setdefault(filename, []).append(entry)
                    names.add(entry.get('asset_name', entry['name']))
            for entry in items:
                if entry.get('asset_file'):
                    continue
                name = entry['name']
                if len(name.encode('utf8')) > 54:
                    name = name.encode('utf8')[:43].decode('utf8', errors='ignore') + ' · ' + entry['id'][:8]
                if name in names:
                    name += ' · ' + entry['id'][:6]
                names.add(name)
                candidates = [filename for filename, members in groups.items()
                              if members[0]['folder'] == folder and len(members) < GROUP_SIZE]
                if candidates:
                    filename = sorted(candidates)[-1]
                else:
                    number = 0
                    while True:
                        key = folder + '\0' + str(number)
                        filename = 'folder_' + hashlib.sha256(key.encode('utf8')).hexdigest()[:24] + '.blend'
                        if filename not in groups:
                            break
                        number += 1
                    groups[filename] = []
                entry['asset_file'], entry['asset_name'] = filename, name
                groups[filename].append(entry)
            continue
        for number, entry in enumerate(items):
            key = folder+'\0'+str(number//GROUP_SIZE)
            filename = 'folder_'+hashlib.sha256(key.encode('utf8')).hexdigest()[:24]+'.blend'
            name = entry['name']
            if len(name.encode('utf8')) > 54:
                name = name.encode('utf8')[:43].decode('utf8', errors='ignore') + ' · ' + entry['id'][:8]
            if name in names:
                name += ' · '+entry['id'][:6]
            names.add(name)
            entry['asset_file'], entry['asset_name'] = filename, name
            groups.setdefault(filename, []).append(entry)
    return groups


def write_asset_group(cache, entries, filename, default_preview=''):
    """Native assets carry only metadata and thumbnails, never render dependencies."""
    materials = []
    try:
        for entry in entries:
            material = bpy.data.materials.new(entry.get('asset_name', entry['name']))
            materials.append(material)
            entry['asset_name'] = material.name
            material['nh_material_id'] = entry['id']
            material['nh_material_color'] = entry['color']
            material['nh_material_rvmat'] = entry['rvmat']
            material['nh_material_stub'] = True
            material['nh_material_asset_schema'] = ASSET_SCHEMA
            material.use_fake_user = True
            material.asset_mark()
            material.asset_data.catalog_id = browser.catalog_id(entry['folder'])
            ready = entry.get('preview_signature') == entry.get('signature') and bool(entry.get('preview_signature'))
            try:
                description = os.path.relpath(os.path.realpath(entry['rvmat']),
                                             os.path.realpath(entry['root']))
            except ValueError:
                # Sources can be on another drive or use a junction alias.
                description = entry['rvmat']
            material.asset_data.description = description
            if not ready:
                material.asset_data.description += '\nPreview pending'
            preview = os.path.join(cache,'previews',entry['id']+'.png') if ready else default_preview
            if preview and os.path.isfile(preview):
                with bpy.context.temp_override(id=material):
                    bpy.ops.ed.lib_id_load_custom_preview(filepath=preview)
            target = os.path.join(cache,'assets',filename)
        temporary = target+'.tmp'
        bpy.data.libraries.write(temporary,set(materials),fake_user=True,compress=True)
        os.replace(temporary,target)
    finally:
        for material in materials:
            bpy.data.materials.remove(material)


def write_asset(cache, entry, preview='', material=None):
    """Compatibility single-entry helper; its render material stays disposable."""
    entry = dict(entry)
    entry.setdefault('signature','')
    if preview:
        entry['preview_signature'] = entry['signature']
    write_asset_group(cache,[entry],entry['id']+'.blend',default_preview=preview)


def pending_preview(cache):
    """A neutral pending sphere requires no material shader or render engine."""
    path = os.path.join(cache, 'pending.png')
    if os.path.isfile(path):
        return path
    from math import sqrt
    from NH_Blender.nh_textures import _save_image_as_png
    size = 96
    image = bpy.data.images.new('NH Pending Sphere', size, size, alpha=True)
    pixels = []
    for y in range(size):
        for x in range(size):
            nx, ny = (x + .5 - size / 2) / (size * .44), (y + .5 - size / 2) / (size * .44)
            radius = nx * nx + ny * ny
            if radius < 1:
                shade = .17 + .43 * max(0, .36 * nx + .42 * ny + .82 * sqrt(1 - radius))
                pixels.extend((shade, shade, shade, 1.0))
            else:
                pixels.extend((0.0, 0.0, 0.0, 0.0))
    image.pixels.foreach_set(pixels)
    temporary = path + '.tmp.png'
    try:
        _save_image_as_png(image, temporary)
        os.replace(temporary, path)
    finally:
        bpy.data.images.remove(image)
    return path


def _main(cache, once=False):
    from NH_Blender import nh_materials_images as preview_images, nh_material_cache as material_cache
    cache = os.path.abspath(cache)
    os.makedirs(os.path.join(cache, 'assets'), exist_ok=True)
    os.makedirs(os.path.join(cache, 'previews'), exist_ok=True)
    os.environ['NH_MATERIALS_PREVIEW_TEXTURE_CACHE'] = os.path.join(cache, 'textures')
    request_path = os.path.join(cache, 'request.json')
    request = read_json(request_path, {})
    root = request['root']
    os.environ['NH_MATERIALS_TEXTURE_ROOT'] = root
    mode = str(request.get('mode', 'INDEX')).upper()
    job_id = request.get('job_id', '')
    revision = time.time_ns()
    entries = []
    requested_ids = set()
    job_failures = set()
    manifest = dict(entries=entries, errors=[])

    def ready(entry):
        return bool(entry.get('preview_signature')) and entry['preview_signature'] == entry['signature']

    def status(message, phase='INDEX', busy=True, **extra):
        nonlocal revision
        revision += 1
        progress_entries = [entry for entry in entries if entry['id'] in requested_ids] if mode == 'IMPORT' else entries
        total = len(progress_entries)
        complete = sum(ready(entry) for entry in progress_entries)
        failed = sum(bool(entry.get('error')) and not ready(entry) and
                     (mode not in ('BUILD', 'IMPORT') or entry['id'] in job_failures) for entry in progress_entries)
        processed = complete + failed
        failures = [dict(id=entry['id'], name=entry['name'], rvmat=entry['rvmat'],
                         color=entry.get('color', ''), error=entry['error'])
                    for entry in progress_entries
                    if entry['id'] in job_failures and entry.get('error') and not ready(entry)]
        result = dict(message=message, job_id=job_id, mode=mode, phase=phase,
                      revision=revision, busy=busy, paused=False, total=total,
                      ready=complete, failed=failed, processed=processed,
                      progress=(processed / total if total else (1.0 if phase == 'DONE' else 0.0)),
                      current='', errors=manifest.get('errors', []), failures=failures)
        result.update(extra)
        browser.atomic_json(os.path.join(cache, 'status.json'), result)

    def save_manifest():
        browser.atomic_json(os.path.join(cache, 'manifest.json'), manifest)

    def control(phase):
        current = read_json(request_path, request)
        while True:
            replaced = current.get('job_id', '') != job_id
            if current.get('cancelled') or replaced:
                save_manifest()
                status('Material preparation cancelled', phase='CANCELLED', busy=False)
                return False
            # INDEX is finite and preserves previous paused-index requests.
            # Explicit library and import jobs can share the same pause gate.
            if mode not in ('BUILD', 'IMPORT') or not current.get('paused'):
                return True
            status('Material preparation paused', phase='PAUSED', busy=False,
                   paused=True, pause_generation=current.get('pause_generation'))
            if once:
                save_manifest()
                return False
            time.sleep(.25)
            current = read_json(request_path, request)

    old = read_json(os.path.join(cache, 'manifest.json'), {})
    status('Indexing materials…')
    compatible = old.get('schema') == rvmat.SCHEMA and old.get('root') == root

    def search_roots(entry):
        return tuple(dict.fromkeys((root, entry.get('root', root), *entry.get('search_roots', ()))))

    def imported_entry(entry):
        """Validate an explicit pair without walking the texture library."""
        path, color = entry.get('rvmat', ''), entry.get('color', '')
        if not os.path.isfile(path) or (color and not os.path.isfile(color)):
            return None
        description = material_cache.describe(path, color, search_roots=search_roots(entry))
        if not description:
            return None
        result = dict(entry, **description)
        result.update(imported=True, root=root)
        result.setdefault('name', Path(path).stem)
        result.setdefault('folder', '')
        return result

    if mode == 'IMPORT':
        manifest = dict(old) if compatible else dict(schema=rvmat.SCHEMA, root=root, errors=[])
        manifest['library_indexed'] = old.get('library_indexed', True) if compatible else False
        merged = {entry['id']: dict(entry) for entry in manifest.get('entries', [])}
        for entry in request.get('entries', []):
            entry = imported_entry(entry)
            if entry:
                requested_ids.add(entry['id'])
                previous = merged.get(entry['id'], {})
                merged[entry['id']] = dict(previous, **entry)
        manifest['entries'] = list(merged.values())
    elif mode != 'BUILD' and compatible and old.get('library_indexed', True) and not request.get('refresh'):
        manifest = old
    else:
        manifest = rvmat.scan(root, read_json(os.path.join(cache, 'pairs.json'), {}))
        manifest['library_indexed'] = True
        scanned = {entry['id']: entry for entry in manifest['entries']}
        for previous in old.get('entries', []) if compatible else []:
            if not previous.get('imported'):
                continue
            entry = imported_entry(previous)
            if entry:
                if entry['id'] in scanned:
                    scanned[entry['id']].update(imported=True, signature=entry['signature'],
                        search_roots=entry.get('search_roots', ()))
                else:
                    manifest['entries'].append(entry)
                    scanned[entry['id']] = entry
    entries = manifest['entries']
    old_lookup = {entry['id']: dict(entry) for entry in old.get('entries', [])}
    old_groups = {}
    for entry in old_lookup.values():
        old_groups.setdefault(entry.get('asset_file', entry['id'] + '.blend'), set()).add(entry['id'])
    for entry in entries:
        entry['root'] = root
        previous = old_lookup.get(entry['id'], {})
        unchanged = previous.get('signature') == entry['signature']
        entry['preview_signature'] = previous.get('preview_signature', '') if unchanged else ''
        if not os.path.isfile(os.path.join(cache, 'previews', entry['id'] + '.png')):
            entry['preview_signature'] = ''
        entry.pop('error', None)
        if unchanged and previous.get('error'):
            entry['error'] = previous['error']
        if mode == 'IMPORT' and previous.get('asset_file'):
            entry['asset_file'] = previous['asset_file']
            entry['asset_name'] = previous.get('asset_name', entry['name'])
    groups = asset_groups(entries, preserve=mode == 'IMPORT')
    if not control('INDEX'):
        return
    folders = browser.catalog_folders(entries)
    catalog = 'VERSION 1\n\n' + '\n'.join('%s:%s:%s' %
        (browser.catalog_id(folder), folder or 'Root', folder.split('/')[-1] or 'Root')
        for folder in sorted(folders)) + '\n'
    catalog_path = os.path.join(cache, 'blender_assets.cats.txt')
    if not os.path.isfile(catalog_path) or Path(catalog_path).read_text(encoding='utf8') != catalog:
        Path(catalog_path + '.tmp').write_text(catalog, encoding='utf8')
        os.replace(catalog_path + '.tmp', catalog_path)
    placeholder = pending_preview(cache)
    preferred = request.get('folder', '')
    ordered = sorted(groups, key=lambda name: (not any(entry['folder'] == preferred for entry in groups[name]), name))
    migration = old.get('asset_schema') != ASSET_SCHEMA
    for number, filename in enumerate(ordered):
        if not control('INDEX'):
            return
        group = groups[filename]
        changed = (migration or old_groups.get(filename) != {entry['id'] for entry in group}
                   or not os.path.isfile(os.path.join(cache, 'assets', filename)) or any(
            old_lookup.get(entry['id'], {}).get('signature') != entry['signature']
            or old_lookup.get(entry['id'], {}).get('asset_file') != filename
            or old_lookup.get(entry['id'], {}).get('asset_name') != entry['asset_name']
            or old_lookup.get(entry['id'], {}).get('preview_signature', '') != entry['preview_signature']
            for entry in group))
        if changed:
            write_asset_group(cache, group, filename, placeholder)
        if number % 16 == 0:
            status('Indexing %d / %d catalogs' % (number + 1, len(groups)))
    assets = Path(cache, 'assets').resolve()
    for path in assets.glob('*.blend'):
        if path.name not in groups and path.parent.resolve() == assets:
            path.unlink()
    manifest['asset_schema'] = ASSET_SCHEMA
    save_manifest()
    if mode not in ('BUILD', 'IMPORT'):
        status('%d Super materials indexed' % len(entries), phase='DONE', busy=False)
        return

    sphere = None
    engine = request.get('engine', 'EEVEE')

    def render(preview):
        nonlocal engine
        scene = bpy.context.scene
        scene.render.filepath = preview
        try:
            bpy.ops.render.render(write_still=True)
        except RuntimeError:
            if engine == 'CYCLES':
                raise
            engine = 'CYCLES'
            scene.render.engine = 'CYCLES'
            scene.cycles.device = 'CPU'
            scene.cycles.samples = 8
            scene.cycles.use_denoising = True
            bpy.ops.render.render(write_still=True)

    render_entries = [entry for entry in entries if entry['id'] in requested_ids] if mode == 'IMPORT' else entries
    for pending in sorted(render_entries, key=lambda entry: (entry['name'].casefold(), entry['id'])):
        if not control('RENDER'):
            return
        if ready(pending):
            continue
        material = None
        started = time.monotonic()
        try:
            status('Preparing: ' + pending['name'], phase='RENDER', current=pending['name'])
            if not pending.get('color'):
                raise ValueError('Choose Color Texture for this material before preparing its preview')
            if sphere is None:
                sphere = studio(engine)
            material = bpy.data.materials.new('NH Render Material')
            if not material_cache.build(material, pending['rvmat'], pending['color'],
                    search_roots=search_roots(pending), image_loader=preview_images.load):
                raise ValueError('Not a readable Super material')
            sphere.data.materials.clear()
            sphere.data.materials.append(material)
            preview = os.path.join(cache, 'previews', pending['id'] + '.png')
            temporary = preview + '.rendering.png'
            try:
                render(temporary)
                os.replace(temporary, preview)
            finally:
                if os.path.isfile(temporary):
                    os.remove(temporary)
            pending['preview_signature'] = pending['signature']
            pending.pop('error', None)
            job_failures.discard(pending['id'])
            write_asset_group(cache, groups[pending['asset_file']], pending['asset_file'], placeholder)
            print('[NH Materials] Preview %s: %.3fs' %
                  (pending['name'], time.monotonic() - started), flush=True)
        except Exception as exc:
            pending['preview_signature'] = ''
            pending['error'] = str(exc)
            job_failures.add(pending['id'])
            print('[NH Materials]', pending['name'], exc, flush=True)
        finally:
            if sphere:
                sphere.data.materials.clear()
            if material:
                bpy.data.materials.remove(material)
            for image in list(bpy.data.images):
                if image.users == 0 and image.type != 'RENDER_RESULT':
                    bpy.data.images.remove(image)
        save_manifest()
        message = ('Prepared: ' + pending['name'] if ready(pending)
                   else 'Failed: ' + pending['name'] + ' — ' + pending.get('error', 'Unknown error'))
        status(message, phase='RENDER', current=pending['name'],
               selected_ready=pending['id'] if ready(pending) else '')
    current = read_json(request_path, request)
    if current.get('cancelled') or current.get('job_id', '') != job_id:
        status('Material preparation cancelled', phase='CANCELLED', busy=False)
        return
    failures = len(job_failures)
    status('Material preparation complete' + ('; %d failed' % failures if failures else ''),
           phase='DONE', busy=False)


def main(cache, once=False):
    """Keep a fatal worker failure visible to the UI after the process exits."""
    request = read_json(os.path.join(cache, 'request.json'), {})
    try:
        _main(cache, once=once)
    except Exception as exc:
        previous = read_json(os.path.join(cache, 'status.json'), {})
        if previous.get('job_id', '') != request.get('job_id', ''):
            previous = {}
        for key, value in dict(total=0, ready=0, failed=0, processed=0, progress=0.0,
                               errors=[], failures=[]).items():
            previous.setdefault(key, value)
        previous.update(job_id=request.get('job_id', ''), mode=request.get('mode', 'INDEX'),
                        phase='ERROR', message='Material preparation failed: ' + str(exc),
                        error=str(exc), revision=time.time_ns(), busy=False, paused=False,
                        current='')
        browser.atomic_json(os.path.join(cache, 'status.json'), previous)
        raise


if __name__ == '__main__':
    arguments = sys.argv[sys.argv.index('--')+1:]
    if arguments[0] == '--prepare-only':
        task = read_json(arguments[1], {})
        os.environ['NH_MATERIALS_TEXTURE_ROOT'] = task['root']
        from NH_Blender import nh_materials_images as images, nh_textures as textures
        cache_path = task['texture_cache_root']
        def texture_cache(create=False):
            if create:
                os.makedirs(cache_path, exist_ok=True)
            return cache_path
        textures._nh_texture_cache_root = texture_cache
        original_save = textures._save_image_as_png
        def save_atomic(image, path):
            temporary = path + '.preparing.png'
            try:
                original_save(image, temporary)
                os.replace(temporary, path)
            finally:
                if os.path.isfile(temporary):
                    os.remove(temporary)
        textures._save_image_as_png = save_atomic
        try:
            entry = task['entry']
            result = images.cache_full_images(entry['rvmat'], entry['color'])
            result['success'] = True
        except Exception as exc:
            result = dict(success=False, error=str(exc))
        browser.atomic_json(task['result'], result)
    else:
        main(arguments[0],once='--once' in arguments)
