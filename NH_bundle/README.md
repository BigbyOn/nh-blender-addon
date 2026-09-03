Embedded Arma 3 Object Builder bundle
========================================

The `Arma3ObjectBuilder` package in this directory is a trimmed copy of the
"Arma 3 Object Builder" Blender add-on, used by the NH Blender plugin as a
fallback codec when the original add-on is not installed.

- Upstream: https://github.com/MrClock8163/Arma3ObjectBuilder
- License: GNU GPL v3 (see LICENSE in this directory)
- Version of the embedded code: 2.5.1

Included modules (verbatim, only `props/*.py` and `ui/*.py` RNA classes are
renamed with the `NH_` prefix and bl_idnames from `a3ob.` to `nh.` so the
bundle can never collide with the original add-on; `bl_parent_id` strings were
updated together with class renames):

- io: binary_handler, compression, data_p3d, data_paa, import_paa,
  import_p3d, export_p3d
- utilities: data, logger, compat, colors, flags, lod, masses, proxy,
  structure, generic, validator
- props: object, material, scene
- ui: import_export_p3d, props_object_mesh (LOD/Flag Groups/DTM/Proxy/
  Named Properties panels + flag utility operators), props_material
  (Material Properties panel)

The `__init__.py` files were replaced with minimal bootstraps (upstream
preferences/panels/tools are not part of this bundle). Because this package
contains GPL v3 code, any distributed build of the NH Blender add-on that
includes this folder is licensed under GPL v3 as a whole (see the add-on
root `LICENSE` file for the note).
