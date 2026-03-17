# NH Blender Plugin

Blender add-on for DayZ/Arma-style workflows with A3OB integration.

## Core Features

- Scatter clutter proxies from DayZ config (`CfgWorlds -> CAWorld -> Clutter` + `CfgSurfaceCharacters`)
- Build texture DB from folder (`.paa` / `.rvmat`)
- Replace material paths from DB using A3OB-compatible properties
- Batch import/export `.p3d` collections (with source-path tracking and optional `.bak`)
- Build temporary P3D Asset Library and convert placed assets to A3OB proxies
- Quick fixes panel:
- `Fix Shading` (merge selected meshes, clear split normals, recalc normals, shade smooth)
- `Fix Mesh/Hierarchy` duplicate shortcut button

## UI Panels

- `Clutter Proxies (DayZ)`
- `P3D Asset Library`
- `Fixes`
- `Import/Export planner`
- `Texture Replace`

## Export Notes

- `Force export all LODs (skip validation)` exists for problematic files.
- Default is **OFF**.
- Batch export performs post-check and writes missing LOD info to System Console when export is partial.

## Snap Points Status

- Snap Points tools are currently kept in code but panel visibility is disabled in UI.
- This is temporary while pipeline/behavior is being reworked.

## Requirements

- Blender `4.4+`
- Add-on: **Arma 3 Object Builder (A3OB)** enabled
- A3OB import/export operators are required for batch `.p3d` workflows

## Installation

1. Download this repository.
2. In Blender open `Edit -> Preferences -> Add-ons -> Install...`.
3. Select `NH_Blender.py`.
4. Enable the add-on.

Panel location:
- `3D Viewport -> N panel -> NH Plugin`

## Recent Changes (0.1.2 -> 0.1.4)

- `0.1.2`: P3D Asset Library tools and convert-selected-to-proxies workflow added.
- `0.1.3`: New `Fixes` panel, `Fix Shading`, hidden Snap Points panel, LOD export diagnostics.
- `0.1.4`: `Force export all LODs` default switched to OFF.

## Project Links

- Repository: <https://github.com/BigbyOn/nh-blender-addon>
- Issues: <https://github.com/BigbyOn/nh-blender-addon/issues>

## Repository Structure

- `NH_Blender.py` - main add-on file
- `README.md` - project description and setup
- `LICENSE` - license terms
- `.gitignore` - ignored local/build files

## License

MIT License. See [LICENSE](LICENSE).
