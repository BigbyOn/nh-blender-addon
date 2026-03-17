# NH Blender Plugin

Blender add-on for DayZ/Arma-style workflow:
- scatter proxy objects by DayZ clutter config (`.cpp`)
- build texture database from folder (`.paa`/`.rvmat`)
- replace material paths through A3OB-compatible properties
- create snap points in `Memory LOD` (`.sp_*`) manually, automatically, or in batch for many `.p3d`

## Features

- Clutter scatter from `CfgWorlds -> CAWorld -> Clutter` and `CfgSurfaceCharacters`
- Surface-based weighted spawn
- Density controls (`grid size`, `density scale`, `spawn probability`, limits)
- Texture DB build from folder and quick object preview
- Material replace from DB by smart name matching
- Mesh hierarchy fix helper for export preparation
- Snap points panel for `Memory LOD`:
  - manual pair from 2 selected vertices
  - auto pair from model edge extremes (`X/Y/Z`, `Min/Max`, tolerance)
  - batch process for multiple `.p3d`: backup to `.bak`, import, create/update snap points, export back

## Snap Points Naming

Created names follow:
- `.sp_<Group>_<a|v>_<index>`

Example:
- `.sp_StenaKamennaya_a_0`
- `.sp_StenaKamennaya_v_0`

Use matching `<Group>` and `<index>` on opposite sides (`a` <-> `v`) for intended snapping pairs.

## Requirements

- Blender `4.4+`
- Add-on: **Arma 3 Object Builder (A3OB)** enabled (for proxy/material property integration)
- For batch snap processing: A3OB import/export operators must be available

## Installation

1. Download this repository.
2. In Blender: `Edit -> Preferences -> Add-ons -> Install...`
3. Select `NH_Blender.py`.
4. Enable the add-on.

Panel location:
- `3D Viewport -> N panel -> NH Plugin`

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
