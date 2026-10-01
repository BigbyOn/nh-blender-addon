"""NH Materials Asset Browser with explicit preview calculation and cached browsing."""
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import bpy
import bmesh
from bpy.props import EnumProperty, StringProperty, FloatProperty
from bpy.types import AddonPreferences, Operator, Panel

from . import nh_material_shader as shader
from .utilities import rvmat

LIBRARY_NAME = 'NH Materials'
_worker = None
_log = None
_request_key = None
_manifest = {}
_manifest_time = 0
_manifest_root = None
_entries_by_id = {}
_entries_by_asset = {}
_catalogs_by_id = {}
_catalog_folder_key = None
_cache_root_key = None
_cache_root_value = None
_status = ''
_folder = ''
_runtime_active = False
_library_pending = False
_worker_paused = False
_worker_request = None
_pause_generation = None
_refresh_pause = False
_material_busy = False
_prepare_job = None
_apply_operator = None
_stub_seen = {}
_stub_failures = {}
_worker_mode = None
_batch_id = None
_batch_status = {}
_progress_started = False
_index_checked_root = None

# A library refresh reads every asset file. Batch completed thumbnails instead
# of restarting Blender's asset scan after each individual sphere.
_BROWSER_REFRESH_INTERVAL = 5.0
_STUB_SETTLE_INTERVAL = 1.0
_STUB_RETRY_CHECK_INTERVAL = 5.0


def catalog_folders(entries):
    folders = {entry['folder'] for entry in entries}
    for folder in list(folders):
        parts = folder.split('/')
        folders.update('/'.join(parts[:i]) for i in range(1, len(parts)))
    return folders


def cache_root():
    global _cache_root_key, _cache_root_value
    base = os.environ.get('NH_MATERIALS_CACHE_ROOT')
    if not base:
        from .nh_textures import _nh_blender_shared_cache_base
        base = os.path.join(_nh_blender_shared_cache_base(), 'Materials')
    # Newer .blend compression/layouts are not readable by Blender 4.0.
    version = 'blender-%d.%d' % bpy.app.version[:2]
    root = shader.texture_root()
    key = (base, version, root)
    if key != _cache_root_key:
        _cache_root_value = os.path.join(base, version, rvmat.identity(root, ''))
        _cache_root_key = key
    return _cache_root_value


def catalog_id(folder):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'NH Materials/' + folder))


def atomic_json(path, value):
    temporary = str(path) + '.tmp'
    with open(temporary, 'w', encoding='utf8') as file:
        json.dump(value, file, ensure_ascii=False)
    os.replace(temporary, path)


def library_register():
    global _library_pending
    from .nh_assets import _ensure_blender_asset_library_registered
    root = cache_root()
    os.makedirs(root, exist_ok=True)
    if not _ensure_blender_asset_library_registered(LIBRARY_NAME, root):
        raise RuntimeError('Cannot register NH Materials asset library')
    library = bpy.context.preferences.filepaths.asset_libraries.get(LIBRARY_NAME)
    _library_pending = False
    return library


def stop_worker():
    global _worker, _log, _request_key, _worker_paused, _worker_request
    global _pause_generation, _refresh_pause
    global _worker_mode, _batch_id, _progress_started
    if _worker is not None and _worker.poll() is None:
        _worker.terminate()
        # Apply must not decode/upload maps while the disposable process still
        # owns an active preview render. Windows TerminateProcess is immediate;
        # wait for its resource teardown before continuing in the main scene.
        _worker.wait(timeout=2.0)
    _worker = None
    if _log:
        _log.close()
    _log = None
    _request_key = None
    _worker_paused = False
    _worker_request = None
    _pause_generation = None
    _refresh_pause = False
    _worker_mode = None
    _batch_id = None
    if _progress_started:
        bpy.context.window_manager.progress_end()
        _progress_started = False


def _dispose_prepare_job(*, terminate=False):
    global _prepare_job
    job = _prepare_job
    if job is None:
        return
    process = job['process']
    if terminate and process.poll() is None:
        process.terminate()
        process.wait(timeout=2.0)
    elif process.poll() is None:
        return
    job['log'].close()
    # The paths were created together in our source-specific cache. Never
    # remove source textures or derive a deletion path from material metadata.
    directory = Path(job['directory']).resolve()
    for name in ('request', 'result'):
        path = Path(job[name]).resolve()
        if path.parent == directory:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    _prepare_job = None


def _ensure_full_images(entry, owner):
    """Prepare original PAA maps in one disposable CPU process, never in UI."""
    global _prepare_job, _status
    from .nh_materials_images import full_images_ready
    key = (entry['rvmat'], entry['color'])
    job = _prepare_job
    if job is not None and job['key'] == key and job['owner'] == owner:
        if job['process'].poll() is None:
            _status = 'Preparing material textures…'
            return False
        try:
            with open(job['result'], encoding='utf8') as file:
                result = json.load(file)
        except (OSError, ValueError):
            result = dict(success=False, error='Texture preparation failed; details: prepare.log')
        _dispose_prepare_job()
        if not result.get('success'):
            raise ValueError(result.get('error') or 'Texture preparation failed')
        if not full_images_ready(*key):
            raise ValueError('Original texture cache is incomplete')
        updated = {os.path.normcase(os.path.abspath(path)) for path in result.get('cache_paths', [])}
        for image in bpy.data.images:
            if image.filepath and os.path.normcase(os.path.abspath(bpy.path.abspath(image.filepath))) in updated:
                image.reload()
        return True
    if job is not None:
        if job['process'].poll() is None:
            return False
        _dispose_prepare_job()
    if full_images_ready(*key):
        return True
    from .nh_textures import _nh_texture_cache_root
    directory = os.path.join(cache_root(), 'prepare')
    os.makedirs(directory, exist_ok=True)
    token = uuid.uuid4().hex
    request_path = os.path.join(directory, token + '.request.json')
    result_path = os.path.join(directory, token + '.result.json')
    atomic_json(request_path, dict(entry=dict(rvmat=key[0], color=key[1]),
        root=shader.texture_root(), result=result_path,
        texture_cache_root=_nh_texture_cache_root(create=False)))
    log = open(os.path.join(directory, 'prepare.log'), 'w', encoding='utf8')
    environment = os.environ.copy()
    environment['NH_MATERIALS_TEXTURE_ROOT'] = shader.texture_root()
    environment['BLENDER_USER_CONFIG'] = os.path.join(cache_root(), 'worker_config')
    command = [bpy.app.binary_path, '--background', '--factory-startup', '-t', '2',
               '--python-exit-code', '1', '--python',
               str(Path(__file__).with_name('nh_materials_worker.py')),
               '--', '--prepare-only', request_path]
    try:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
            env=environment, creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                           | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)))
    except Exception:
        log.close()
        Path(request_path).unlink(missing_ok=True)
        raise
    _prepare_job = dict(process=process, key=key, owner=owner, directory=directory,
                        request=request_path, result=result_path, log=log)
    _status = 'Preparing material textures…'
    return False


def _preferences_changed(self, context):
    global _manifest, _manifest_time, _manifest_root, _library_pending
    global _entries_by_id, _entries_by_asset, _catalogs_by_id, _catalog_folder_key
    global _batch_status, _index_checked_root
    if _apply_operator is not None:
        _apply_operator.cancel(context)
    _dispose_prepare_job(terminate=True)
    stop_worker()
    _manifest, _manifest_time = {}, 0
    _manifest_root = None
    _entries_by_id, _entries_by_asset = {}, {}
    _catalogs_by_id, _catalog_folder_key = {}, None
    _library_pending = True
    _batch_status = {}
    _index_checked_root = None


class NH_MATERIALS_Preferences(AddonPreferences):
    bl_idname = __package__
    nh_textures_folder: StringProperty(name='NH Textures folder', subtype='DIR_PATH',
        description='Folder containing Super RVMAT materials and color textures. Empty: P:\\NH_ObjectTextures',
        default='', update=_preferences_changed)
    preview_engine: EnumProperty(name='Preview engine',
        description='Eevee is faster; CPU Cycles avoids a background GPU renderer',
        items=(('EEVEE', 'Eevee (Fast)', 'Fast material previews using Eevee'),
               ('CYCLES', 'Cycles (CPU)', 'CPU previews with two threads')),
        default='EEVEE', update=_preferences_changed)

    def draw(self, context):
        self.layout.prop(self, 'nh_textures_folder')
        self.layout.prop(self, 'preview_engine')
        if not self.nh_textures_folder:
            self.layout.label(text='Default: ' + rvmat.DEFAULT_ROOT)
        draw_calculation(self.layout, context)


def _preview_engine():
    addon = bpy.context.preferences.addons.get(__package__)
    return getattr(addon.preferences, 'preview_engine', 'EEVEE') if addon else 'EEVEE'


def read_manifest():
    global _manifest, _manifest_time, _manifest_root
    global _entries_by_id, _entries_by_asset, _catalogs_by_id, _catalog_folder_key
    global _revision, _pending_revision, _last_refresh
    root = cache_root()
    if root != _manifest_root:
        _manifest_root, _manifest, _manifest_time = root, {}, 0
        _entries_by_id, _entries_by_asset = {}, {}
        _catalogs_by_id, _catalog_folder_key = {}, None
        _revision, _pending_revision, _last_refresh = None, None, 0
    path = os.path.join(root, 'manifest.json')
    try:
        stamp = os.stat(path).st_mtime_ns
        if stamp != _manifest_time:
            with open(path, encoding='utf8') as file:
                manifest = json.load(file)
            entries = manifest.get('entries', [])
            _entries_by_id = {entry['id']: entry for entry in entries}
            _entries_by_asset = {
                (os.path.basename(entry.get('asset_file', entry['id'] + '.blend')),
                 entry.get('asset_name', entry['name'])): entry for entry in entries}
            # Preview/error updates change the manifest but not its catalogs.
            # Compute UUIDs only when the folder hierarchy actually changes.
            folders = frozenset(entry['folder'] for entry in entries)
            if folders != _catalog_folder_key:
                _catalogs_by_id = {catalog_id(folder): folder
                                  for folder in catalog_folders(entries)}
                _catalog_folder_key = folders
            _manifest = manifest
            _manifest_time = stamp
    except (OSError, ValueError):
        pass
    return _manifest


def selected_entry(context, *, update_manifest=True):
    asset = getattr(context, 'asset', None)
    if asset is None:
        return None
    local = getattr(asset, 'local_id', None)
    key = local.get('nh_material_id', '') if local else ''
    if update_manifest:
        read_manifest()
    if key:
        return _entries_by_id.get(key)
    for path in (getattr(asset, 'full_library_path', ''),
                 getattr(asset, 'relative_path', '')):
        # full_library_path may include the ID path following the .blend file.
        normalized = path.replace('\\', '/')
        if '.blend' not in normalized:
            continue
        blend_part, suffix = normalized.split('.blend', 1)
        filename = blend_part.rsplit('/', 1)[-1] + '.blend'
        names = (suffix.rsplit('/', 1)[-1], getattr(asset, 'name', ''))
        for name in names:
            entry = _entries_by_asset.get((filename, name))
            if entry is not None:
                return entry
        # Older cache versions stored each material in <material id>.blend.
        entry = _entries_by_id.get(filename[:-6])
        if entry is not None:
            return entry
    return None


def request_previews(folder='', selected='', *, refresh=False, search='', mode='INDEX'):
    global _worker, _log, _request_key, _status, _worker_paused, _worker_request
    global _worker_mode, _batch_id, _batch_status, _progress_started, _index_checked_root
    if _refresh_pause:
        return
    root = shader.texture_root()
    if not os.path.isdir(root):
        _status = 'Textures folder not found: ' + root
        return
    cache = cache_root()
    os.makedirs(cache, exist_ok=True)
    active = _worker is not None and _worker.poll() is None
    if active:
        return
    if mode == 'INDEX' and not refresh:
        if _index_checked_root == root:
            return
        manifest = read_manifest()
        if manifest.get('schema') == rvmat.SCHEMA and manifest.get('root') == root and manifest.get('asset_schema') == 2:
            _index_checked_root = root
            return
        # One automatic metadata index per root; failures are retried by Refresh.
        _index_checked_root = root
    engine = _preview_engine()
    key = (root, mode, refresh, engine)
    if _worker is not None and _worker.poll() not in (None, 0) and key == _request_key:
        _status = 'Preview worker failed. Use Refresh Library; details: worker.log'
        return
    request = dict(root=root, cache=cache, folder=folder, selected=selected,
                   refresh=refresh, search=search, engine=engine, generation=time.time(),
                   mode=mode, job_id=uuid.uuid4().hex, paused=False, cancelled=False)
    atomic_json(os.path.join(cache, 'request.json'), request)
    _worker_request = request
    _request_key = key
    _worker_paused = False
    _worker_mode = mode
    if mode == 'BUILD':
        _batch_id = request['job_id']
        _batch_status = dict(job_id=_batch_id, phase='INDEX', total=0, ready=0, failed=0,
                             processed=0, progress=0, message='Indexing materials…')
        bpy.context.window_manager.nh_materials_progress = 0
        bpy.context.window_manager.progress_begin(0, 100)
        _progress_started = True
    else:
        _batch_id = None
    if not active:
        if _log:
            _log.close()
        _log = open(os.path.join(cache, 'worker.log'), 'w', encoding='utf8')
        environment = os.environ.copy()
        environment['NH_MATERIALS_TEXTURE_ROOT'] = root
        environment['BLENDER_USER_CONFIG'] = os.path.join(cache, 'worker_config')
        command = [bpy.app.binary_path, '--background', '--factory-startup', '-t', '2',
                   '--python-exit-code', '1', '--python',
                   str(Path(__file__).with_name('nh_materials_worker.py')), '--', cache]
        _worker = subprocess.Popen(command, stdout=_log, stderr=subprocess.STDOUT,
            env=environment, creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                           | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)))
        _status = 'Indexing materials…'


def _refresh_browser(area, window):
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region:
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.asset.library_refresh()
    area.tag_redraw()


_revision = None
_pending_revision = None
_last_refresh = 0


def _browsers():
    """Find open NH browsers before touching the material cache or source files."""
    result = []
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'FILE_BROWSER' or area.ui_type != 'ASSETS':
                continue
            params = area.spaces.active.params
            if params is not None and params.asset_library_reference == LIBRARY_NAME:
                result.append((window, area, params))
    return result


def _pause_worker():
    global _request_key, _worker_paused, _worker_request, _pause_generation
    if _worker_paused or _worker is None or _worker.poll() is not None:
        return
    if _worker_request is None:
        return
    try:
        request_path = os.path.join(cache_root(), 'request.json')
        request = dict(_worker_request)
        request['paused'] = True
        _pause_generation = time.time_ns()
        request['pause_generation'] = _pause_generation
        atomic_json(request_path, request)
        _worker_request = request
        # Keep the explicit job available for resuming after Apply or drop.
        _request_key = None
        _worker_paused = True
    except (OSError, ValueError):
        pass


def _read_status():
    try:
        with open(os.path.join(cache_root(), 'status.json'), encoding='utf8') as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}


def _batch_active():
    return (_worker_mode == 'BUILD' and _batch_id is not None and _worker_request is not None
            and _worker_request.get('job_id') == _batch_id
            and _worker is not None and _worker.poll() is None)


def _resume_worker():
    global _worker_paused, _worker_request, _pause_generation
    if not _worker_paused or _worker is None or _worker.poll() is not None or not _worker_request:
        return
    if _worker_request.get('cancelled'):
        return
    request = dict(_worker_request, paused=False, generation=time.time())
    atomic_json(os.path.join(cache_root(), 'request.json'), request)
    _worker_request = request
    _worker_paused = False
    _pause_generation = None


def _update_batch_progress(status):
    global _batch_status, _progress_started, _status
    if _batch_id is None:
        return
    if status.get('job_id') == _batch_id:
        _batch_status = status
    if _worker is not None and _worker.poll() is not None and _batch_status.get('phase') not in {'DONE', 'CANCELLED', 'ERROR'}:
        _batch_status = dict(_batch_status, phase='ERROR', message='Preview calculation stopped; see worker.log')
    progress = min(1.0, max(0.0, _batch_status.get('progress', 0.0)))
    bpy.context.window_manager.nh_materials_progress = progress * 100
    if _progress_started:
        bpy.context.window_manager.progress_update(progress * 100)
        if _batch_status.get('phase') in {'DONE', 'CANCELLED', 'ERROR'}:
            bpy.context.window_manager.progress_end()
            _progress_started = False
    if not _material_busy and _prepare_job is None:
        _status = _batch_status.get('message', _status)


def _worker_pause_acknowledged(status=None):
    if _worker is None or _worker.poll() is not None:
        return True
    if not _worker_paused:
        return False
    if status is None:
        status = _read_status()
    return (status.get('paused') is True
            and status.get('pause_generation') == _pause_generation)


def _stub_fingerprint(material):
    """Check failed stubs for repaired maps, without decoding any image."""
    rvmat_path = material.get('nh_material_rvmat', '')
    color = material.get('nh_material_color', '')
    try:
        return rvmat.signature(rvmat_path, color, shader.texture_root())
    except (OSError, ValueError, TypeError):
        stats = []
        for path in (rvmat_path, color):
            try:
                stat = os.stat(path)
                stats.append((path, stat.st_size, stat.st_mtime_ns))
            except OSError:
                stats.append((path, None, None))
        return tuple(stats)


def _prepare_material_users_uvs(material):
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or not any(item == material for item in obj.data.materials):
            continue
        if obj.mode == 'EDIT':
            mesh = bmesh.from_edit_mesh(obj.data)
            uv_layers = mesh.loops.layers.uv
            source = list(uv_layers.values())
            if source and not uv_layers.get('NH_UV1'):
                target = uv_layers.new('NH_UV1')
                for face in mesh.faces:
                    for loop in face.loops:
                        loop[target].uv = loop[source[min(1, len(source) - 1)]].uv
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        else:
            shader.prepare_uvs(obj)


def _build_one_stub(now):
    """Hydrate a dropped material after the drop settles; one graph per tick."""
    global _material_busy
    pending = False
    worker_ready = None
    live = set()
    for material in bpy.data.materials:
        if (not material.get('nh_material_stub') or material.library is not None
                or material.users - int(material.use_fake_user) <= 0):
            continue
        pointer = material.as_pointer()
        live.add(pointer)
        identity = (material.get('nh_material_id', ''),
                    material.get('nh_material_rvmat', ''),
                    material.get('nh_material_color', ''))
        seen = _stub_seen.get(pointer)
        if seen is None or seen[0] != identity:
            _stub_seen[pointer] = (identity, now)
            _stub_failures.pop(pointer, None)
            pending = True
            _pause_worker()
            continue
        failure = _stub_failures.get(pointer)
        if failure is not None:
            if now < failure[1]:
                continue
            fingerprint = _stub_fingerprint(material)
            if fingerprint == failure[0]:
                _stub_failures[pointer] = (fingerprint, now + _STUB_RETRY_CHECK_INTERVAL)
                continue
            _stub_failures.pop(pointer, None)
            # A repaired material gets the same settling delay as a new drop.
            _stub_seen[pointer] = (identity, now)
            pending = True
            _pause_worker()
            continue
        pending = True
        _pause_worker()
        if now - seen[1] < _STUB_SETTLE_INTERVAL or _material_busy:
            continue
        if getattr(bpy.context.window_manager, 'is_interface_locked', False):
            continue
        if worker_ready is None:
            worker_ready = _worker_pause_acknowledged()
        if not worker_ready:
            continue
        entry = dict(id=identity[0], rvmat=identity[1], color=identity[2], name=material.name)
        try:
            if not _ensure_full_images(entry, ('drop', pointer)):
                return True
            _material_busy = True
            material_from_entry(entry, material=material, cache_only=True)
            _prepare_material_users_uvs(material)
            if 'nh_material_error' in material:
                del material['nh_material_error']
        except Exception as exc:
            material['nh_material_error'] = str(exc)
            _stub_failures[pointer] = (_stub_fingerprint(material),
                                       now + _STUB_RETRY_CHECK_INTERVAL)
        finally:
            _material_busy = False
        # Keep library reloads away from the native drag/drop operation and
        # from the tick which changed the appended material's graph.
        return True
    for pointer in set(_stub_seen) - live:
        _stub_seen.pop(pointer, None)
        _stub_failures.pop(pointer, None)
    if (_prepare_job is not None and _prepare_job['owner'][0] == 'drop'
            and _prepare_job['owner'][1] not in live):
        _dispose_prepare_job(terminate=True)
    return pending


def timer():
    global _status, _folder, _revision, _pending_revision, _last_refresh, _refresh_pause
    try:
        if _library_pending:
            library_register()
            _status = ''
        status = _read_status() if _progress_started or (_worker is not None and _worker.poll() is None) else None
        if status is not None:
            _update_batch_progress(status)
        if _material_busy:
            _pause_worker()
            return 0.75
        browsers = _browsers()
        now = time.monotonic()
        pending_stubs = _build_one_stub(now)
        if pending_stubs:
            _pause_worker()
            return 0.75
        _resume_worker()
        if not browsers:
            # An explicit batch continues outside Shading. Idle browsing never
            # launches material rendering or rereads the cache.
            return 0.75 if _batch_active() else 2.0
        previous_status = _status
        status = status if status is not None else _read_status()
        if _batch_id is None:
            _status = status.get('message', _status)
        read_manifest()
        for window, area, params in browsers:
            _folder = _catalogs_by_id.get(params.catalog_id, '')
            if previous_status != _status or _batch_active():
                area.tag_redraw()
        revision = status.get('revision')
        if revision is not None and revision != _revision:
            # Keep the newest revision pending until every open browser has
            # refreshed. The final thumbnail remains pending even after the
            # worker stops changing status.json.
            _pending_revision = revision
        worker_active = _worker is not None and _worker.poll() is None
        due = (_pending_revision is not None and not worker_active
               and now - _last_refresh >= _BROWSER_REFRESH_INTERVAL)
        if due:
            _last_refresh = now
            for window, area, _params in browsers:
                _refresh_browser(area, window)
            _revision, _pending_revision = _pending_revision, None
        if not worker_active:
            request_previews()  # metadata only, at most once per source root
        return 0.75
    except Exception as exc:
        _status = str(exc)
        return 5.0 if _library_pending else 1.0


def material_from_entry(entry, *, material=None, cache_only=False):
    from .nh_textures import _set_p3d_material_paths
    if not entry['color']:
        raise ValueError('Choose a color texture for this RVMAT first')
    created = material is None
    if created:
        material = bpy.data.materials.new(entry['name'])
    try:
        loader = None
        if cache_only:
            from .nh_textures import _load_material_preview_image
            def loader(path, keep_cache, color_space='SRGB'):
                return _load_material_preview_image(path, keep_cache, color_space,
                                                    cache_missing_textures=False)
        if not shader.build(material, entry['rvmat'], entry['color'], image_loader=loader):
            raise ValueError('Not a readable Super material')
        _set_p3d_material_paths(material, entry['color'], entry['rvmat'])
        material['nh_material_id'] = entry['id']
        material['nh_material_color'] = entry['color']
        material['nh_material_rvmat'] = entry['rvmat']
        material['nh_material_stub'] = False
        return material
    except Exception:
        if created:
            bpy.data.materials.remove(material)
        raise


def apply_material(context, material):
    if context.mode == 'EDIT_MESH':
        objects = [obj for obj in context.objects_in_mode_unique_data if obj.type == 'MESH']
        targets = [(obj, bmesh.from_edit_mesh(obj.data)) for obj in objects]
        targets = [(obj, mesh) for obj, mesh in targets if any(face.select for face in mesh.faces)]
        if not targets:
            raise ValueError('Select faces in Edit Mode')
        for obj, mesh in targets:
            # Separate shared mesh data is handled by Blender's multi-object
            # edit selection; preserve every unselected face's material index.
            index = next((i for i, item in enumerate(obj.data.materials) if item == material), None)
            if index is None:
                index = len(obj.data.materials)
                obj.data.materials.append(material)
            uv_layers = mesh.loops.layers.uv
            source = list(uv_layers.values())
            if source and not uv_layers.get('NH_UV1'):
                target = uv_layers.new('NH_UV1')
                for face in mesh.faces:
                    for loop in face.loops:
                        loop[target].uv = loop[source[min(1, len(source) - 1)]].uv
            for face in mesh.faces:
                if face.select:
                    face.material_index = index
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        return len(targets)
    if context.mode != 'OBJECT':
        raise ValueError('Use Object Mode or Edit Mode')
    objects = [obj for obj in context.selected_objects if obj.type == 'MESH']
    if not objects:
        raise ValueError('Select mesh objects')
    for obj in objects:
        # Object Mode assigns the active slot; it does not erase other slots.
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        if obj.material_slots:
            obj.active_material = material
        else:
            obj.data.materials.append(material)
        shader.prepare_uvs(obj)
    return len(objects)


class NH_MATERIALS_OT_Open(Operator):
    bl_idname = 'nh_materials.open'
    bl_label = 'NH Materials'
    bl_description = 'Open the NH Super material library in the Shading Asset Browser'

    def execute(self, context):
        try:
            library_register()
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        area = context.area
        if area is None or area.type != 'FILE_BROWSER':
            candidates = [a for a in context.screen.areas if a.type == 'FILE_BROWSER']
            area = max(candidates, key=lambda a: a.y) if candidates else None
        if area is None:
            self.report({'ERROR'}, 'Open an Asset Browser area first')
            return {'CANCELLED'}
        area.ui_type = 'ASSETS'
        params = area.spaces.active.params
        if params:
            params.asset_library_reference = LIBRARY_NAME
        request_previews()
        area.tag_redraw()
        return {'FINISHED'}


class NH_MATERIALS_OT_Apply(Operator):
    bl_idname = 'nh_materials.apply'
    bl_label = 'Apply Material'
    bl_options = {'REGISTER', 'UNDO'}

    _event_timer = None
    _wm = None
    _entry = None
    _selection = None
    _owner = None
    _next_poll = 0

    @staticmethod
    def _capture_selection(context):
        if context.mode == 'EDIT_MESH':
            objects = [obj for obj in context.objects_in_mode_unique_data if obj.type == 'MESH']
            snapshot = []
            for obj in objects:
                mesh = bmesh.from_edit_mesh(obj.data)
                mesh.faces.index_update()
                faces = tuple(face.index for face in mesh.faces if face.select)
                snapshot.append((obj.as_pointer(), obj.data.as_pointer(), len(mesh.faces), faces))
            if not any(item[-1] for item in snapshot):
                raise ValueError('Select faces in Edit Mode')
        elif context.mode == 'OBJECT':
            snapshot = [(obj.as_pointer(), obj.data.as_pointer(), obj.active_material_index)
                        for obj in context.selected_objects if obj.type == 'MESH']
            if not snapshot:
                raise ValueError('Select mesh objects')
        else:
            raise ValueError('Use Object Mode or Edit Mode')
        return context.mode, shader.texture_root(), tuple(sorted(snapshot))

    def _finish(self):
        global _material_busy, _apply_operator
        if self._event_timer is not None and self._wm is not None:
            self._wm.event_timer_remove(self._event_timer)
        self._event_timer, self._wm = None, None
        _material_busy = False
        if _apply_operator is self:
            _apply_operator = None
        _resume_worker()

    def _apply_ready(self, context):
        material = None
        try:
            if self._capture_selection(context) != self._selection:
                raise ValueError('Selection or mode changed; apply the material again')
            material = material_from_entry(self._entry, cache_only=True)
            count = apply_material(context, material)
            self.report({'INFO'}, 'Applied to %d object(s)' % count)
            return {'FINISHED'}
        except Exception as exc:
            if material and material.users == 0:
                bpy.data.materials.remove(material)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            self._finish()

    def execute(self, context):
        global _material_busy, _apply_operator
        if _material_busy:
            self.report({'INFO'}, 'Material preparation is already in progress')
            return {'CANCELLED'}
        entry = selected_entry(context)
        if not entry:
            self.report({'ERROR'}, 'Select an NH material')
            return {'CANCELLED'}
        try:
            self._selection = self._capture_selection(context)
            self._entry = dict(entry)
            self._owner = ('apply', self.as_pointer())
            if _batch_active():
                _pause_worker()
            else:
                stop_worker()
            _dispose_prepare_job(terminate=True)
            _material_busy = True
            if _ensure_full_images(self._entry, self._owner) and _worker_pause_acknowledged():
                return self._apply_ready(context)
            self._wm = context.window_manager
            self._event_timer = self._wm.event_timer_add(0.25, window=context.window)
            self._next_poll = time.monotonic() + 0.25
            self._wm.modal_handler_add(self)
            _apply_operator = self
            return {'RUNNING_MODAL'}
        except Exception as exc:
            _dispose_prepare_job(terminate=True)
            self.report({'ERROR'}, str(exc))
            self._finish()
            return {'CANCELLED'}

    def modal(self, context, event):
        if self._event_timer is None:
            return {'CANCELLED'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self.cancel(context)
            self.report({'INFO'}, 'Material preparation cancelled')
            return {'CANCELLED'}
        # Blender 4.0 Event exposes the type, but has no Event.timer member.
        if event.type != 'TIMER' or time.monotonic() < self._next_poll:
            return {'PASS_THROUGH'}
        self._next_poll = time.monotonic() + 0.25
        try:
            if self._capture_selection(context) != self._selection:
                self.cancel(context)
                self.report({'WARNING'}, 'Selection or mode changed; apply the material again')
                return {'CANCELLED'}
            if not _ensure_full_images(self._entry, self._owner):
                return {'RUNNING_MODAL'}
            if not _worker_pause_acknowledged():
                return {'RUNNING_MODAL'}
            return self._apply_ready(context)
        except Exception as exc:
            self.cancel(context)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def cancel(self, context):
        if _prepare_job is not None and _prepare_job['owner'] == self._owner:
            _dispose_prepare_job(terminate=True)
        self._finish()


class NH_MATERIALS_OT_Calculate(Operator):
    bl_idname = 'nh_materials.calculate_previews'
    bl_label = 'Calculate Previews'
    bl_description = 'Calculate missing and changed previews for the entire library; reuse ready previews'

    @classmethod
    def poll(cls, context):
        return not _batch_active() and not _material_busy and _prepare_job is None

    def execute(self, context):
        if not os.path.isdir(shader.texture_root()):
            self.report({'ERROR'}, 'NH Textures folder not found')
            return {'CANCELLED'}
        try:
            library_register()
            stop_worker()
            request_previews(refresh=True, mode='BUILD')
            self.report({'INFO'}, 'Preview calculation started for the entire library')
            return {'FINISHED'}
        except Exception as exc:
            stop_worker()
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class NH_MATERIALS_OT_CancelCalculation(Operator):
    bl_idname = 'nh_materials.cancel_calculation'
    bl_label = 'Cancel Calculation'
    bl_description = 'Stop after the current material; keep completed previews for the next calculation'

    @classmethod
    def poll(cls, context):
        return _batch_active() and not _worker_request.get('cancelled', False)

    def execute(self, context):
        global _worker_request, _worker_paused, _status
        request = dict(_worker_request, cancelled=True, paused=False)
        atomic_json(os.path.join(cache_root(), 'request.json'), request)
        _worker_request = request
        _worker_paused = False
        _status = 'Cancelling calculation; saving the current preview…'
        return {'FINISHED'}


class NH_MATERIALS_OT_Refresh(Operator):
    bl_idname = 'nh_materials.refresh'
    bl_label = 'Refresh Library'
    bl_description = 'Rescan material files without rendering previews'

    @classmethod
    def poll(cls, context):
        return not _batch_active()

    def execute(self, context):
        stop_worker()
        request_previews(_folder, refresh=True)
        return {'FINISHED'}


class NH_MATERIALS_OT_Pair(Operator):
    bl_idname = 'nh_materials.choose_color'
    bl_label = 'Choose Color Texture'
    bl_description = 'Choose the color texture when the RVMAT name has no unambiguous pair'
    filepath: StringProperty(subtype='FILE_PATH')
    rvmat_path: StringProperty(options={'HIDDEN'})
    filter_glob: StringProperty(default='*.paa', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return not _batch_active()

    def invoke(self, context, event):
        entry = selected_entry(context)
        if not entry:
            self.report({'ERROR'}, 'Select an NH material')
            return {'CANCELLED'}
        self.rvmat_path = entry['rvmat']
        self.filepath = entry['color'] or os.path.dirname(entry['rvmat']) + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.rvmat_path or not os.path.isfile(self.filepath) or not self.filepath.lower().endswith('.paa'):
            self.report({'ERROR'}, 'Choose an existing PAA color texture')
            return {'CANCELLED'}
        path = os.path.join(cache_root(), 'pairs.json')
        try:
            with open(path, encoding='utf8') as file:
                pairs = json.load(file)
        except (OSError, ValueError):
            pairs = {}
        pairs[os.path.normcase(os.path.abspath(self.rvmat_path))] = os.path.abspath(self.filepath)
        atomic_json(path, pairs)
        stop_worker()
        request_previews(_folder, refresh=True)
        return {'FINISHED'}


def calculation_text():
    status = _batch_status
    if not status:
        return ''
    processed, total = status.get('processed', 0), status.get('total', 0)
    if not total:
        return status.get('message', 'Indexing materials…')
    text = '%d / %d (%.0f%%)' % (processed, total, status.get('progress', 0) * 100)
    if status.get('failed'):
        text += '; failed: %d' % status['failed']
    if status.get('phase') == 'CANCELLED':
        text += ' — cancelled'
    return text


def draw_calculation(layout, context):
    row = layout.row(align=True)
    row.operator('nh_materials.calculate_previews', icon='RENDER_STILL')
    if _batch_active():
        row.operator('nh_materials.cancel_calculation', text='', icon='CANCEL')
    if _batch_status:
        row = layout.row()
        row.enabled = False
        row.prop(context.window_manager, 'nh_materials_progress', text='Previews', slider=True)
        layout.label(text=calculation_text())
        if _batch_active() and _batch_status.get('current'):
            layout.label(text=_batch_status['current'])


class NH_MATERIALS_PT_Details(Panel):
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_label = 'NH Materials'

    @classmethod
    def poll(cls, context):
        return (context.space_data and context.space_data.type == 'FILE_BROWSER'
                and context.space_data.params
                and getattr(context.space_data.params, 'asset_library_reference', '') == LIBRARY_NAME)

    def draw(self, context):
        layout = self.layout
        layout.operator('nh_materials.apply', icon='MATERIAL')
        layout.operator('nh_materials.choose_color', icon='FILE_IMAGE')
        layout.operator('nh_materials.refresh', icon='FILE_REFRESH')
        draw_calculation(layout, context)
        entry = selected_entry(context)
        if entry:
            layout.label(text=entry['name'])
            if entry.get('preview_signature') != entry.get('signature'):
                layout.label(text='Preview pending', icon='TIME')
            if not entry['color']:
                layout.label(text='Choose a color texture', icon='INFO')
            if entry.get('error'):
                layout.label(text=entry['error'][:60], icon='ERROR')
        if _status:
            # Native sidebar can wrap long status messages.
            for line in [_status[i:i + 42] for i in range(0, len(_status), 42)]:
                layout.label(text=line)


def draw_header(self, context):
    if context.area and context.area.ui_type == 'ASSETS':
        params = context.space_data.params
        row = self.layout.row(align=True)
        if params and params.asset_library_reference == LIBRARY_NAME:
            row.operator('nh_materials.apply', text='Apply Material', icon='MATERIAL')
            row.operator('nh_materials.choose_color', text='', icon='FILE_IMAGE')
            row.operator('nh_materials.refresh', text='', icon='FILE_REFRESH')
            row.operator('nh_materials.calculate_previews', icon='RENDER_STILL')
            if _batch_active():
                row.operator('nh_materials.cancel_calculation', text='', icon='CANCEL')
                row.label(text=calculation_text())
        else:
            row.operator('nh_materials.open', text='NH Materials', icon='MATERIAL')


classes = (NH_MATERIALS_Preferences, NH_MATERIALS_OT_Open, NH_MATERIALS_OT_Apply,
           NH_MATERIALS_OT_Calculate, NH_MATERIALS_OT_CancelCalculation,
           NH_MATERIALS_OT_Refresh, NH_MATERIALS_OT_Pair, NH_MATERIALS_PT_Details)


def register_runtime():
    global _runtime_active, _library_pending
    # addon_utils.enable runs register() inside RestrictBlend: operators cannot
    # access view_layer there. Create the library after normal context returns.
    _library_pending = True
    bpy.types.WindowManager.nh_materials_progress = FloatProperty(name='Preview progress',
        subtype='PERCENTAGE', min=0, max=100, default=0, options={'SKIP_SAVE'})
    if not _runtime_active:
        bpy.types.FILEBROWSER_HT_header.append(draw_header)
    if not bpy.app.timers.is_registered(timer):
        bpy.app.timers.register(timer, first_interval=1, persistent=True)
    _runtime_active = True


def unregister_runtime():
    global _runtime_active, _library_pending, _index_checked_root, _batch_status
    _library_pending = False
    stop_worker()
    if _apply_operator is not None:
        _apply_operator.cancel(bpy.context)
    _dispose_prepare_job(terminate=True)
    if bpy.app.timers.is_registered(timer):
        bpy.app.timers.unregister(timer)
    if _runtime_active:
        bpy.types.FILEBROWSER_HT_header.remove(draw_header)
    _stub_seen.clear()
    _stub_failures.clear()
    _index_checked_root = None
    _batch_status = {}
    _runtime_active = False
    if hasattr(bpy.types.WindowManager, 'nh_materials_progress'):
        del bpy.types.WindowManager.nh_materials_progress
