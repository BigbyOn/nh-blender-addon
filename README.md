# NH Blender Plugin

Blender-аддон для пайплайна DayZ/Arma с интеграцией **Arma 3 Object Builder (A3OB)**.

Расположение в Blender: `3D Viewport -> N Panel -> NH Plugin`

Текущая версия: **0.1.8**

## Возможности

- Scatter clutter-прокси из DayZ-конфига: `CfgWorlds -> CAWorld -> Clutter` + `CfgSurfaceCharacters`
- Memory LOD / Snap Points workflow
- Geometry Collider workflow для создания коллайдеров прямо в Blender
- Misc / Roadway workflow для подготовки плоских walkway-LOD мешей
- Texture Replace через A3OB material properties (`.paa` / `.rvmat`)
- Batch import/export `.p3d`
- Temporary P3D Asset Library и конвертация размещенных объектов в A3OB proxy
- Fixes для геометрии, shading и иерархии

## Основные панели

- `Geometry Collider`
- `Clutter Proxies (DayZ)`
- `Snap Points (Memory LOD)`
- `P3D Asset Library`
- `Fixes`
- `Import/Export planner`
- `Texture Replace`

## Geometry Collider

Панель `Geometry Collider` добавлена внутрь `NH Plugin` и рассчитана на workflow, близкий к Object Builder.

Что умеет:

- создавать или находить отдельный `Geometry` LOD
- складывать collider-меши в коллекцию `Geometry`
- красить collider-объекты в светло-желтый цвет для быстрого визуального отличия от `Resolution`
- поддерживать OB-style workflow через хоткеи
- показывать fallback-кнопки через раскрывающийся блок `Hotkeys -> Buttons`
- показывать дополнительные редкие build-кнопки через `Extra Build`

### Collider workflow

Базовый сценарий:

1. На `Resolution` войдите в `Edit Mode`.
2. Выделите вершины.
3. Нажмите `Ctrl+Shift+C` для копирования вершин в `Geometry`.
4. В `Geometry` при необходимости используйте `Shift+D` и перемещение вершин.
5. Нажмите `Mouse5`, чтобы собрать convex hull из loose verts.

Дополнительно:

- `Mouse4` делает `Selection -> Hull` по текущему выделению вершин / ребер / полигонов
- `Alt+LMB` выбирает весь связанный mesh island под курсором
- `Ctrl+Shift+A` выбирает только изолированные вершины без ребер и полигонов

### Collider hotkeys

- `Ctrl+Shift+C` — `Copy Selected Verts To Geometry`
- `Ctrl+Shift+A` — `Select Isolated Verts`
- `Mouse4` — `Selection -> Hull`
- `Mouse5` — `Loose Geometry Verts -> Hull`
- `Alt+LMB` — `Pick Whole Mesh Island`

### Extra Build

В раскрывающемся блоке `Extra Build` оставлены редкие кнопки:

- `Selection -> Box`
- `Object -> Bounds`

Настройки:

- `Thickness`
- `Bounds Padding`
- `Merge Distance`
- `Recalculate Normals`

## Misc / Roadway

Панель `Geometry Collider` также содержит блок `Misc / Roadway`.

Что умеет:

- создавать или находить коллекцию `Misc`
- создавать или находить `Roadway` LOD внутри `Misc`
- копировать выделенные полигоны из визуала в `Roadway`
- сшивать почти совпадающие вершины в `Roadway` для более цельной nav/path геометрии

### Roadway workflow

1. На визуальном меше выделите нужные полигоны в `Edit Mode`.
2. Нажмите `Create/Find Misc Roadway`.
3. Нажмите `Copy Selected Faces To Roadway`.
4. При необходимости нажмите `Weld Roadway`.

### Roadway настройки

- `Roadway Weld Distance` — дистанция сшивания близких вершин в `Roadway`
- `Weld Roadway` — повторное merge-by-distance после ручных правок

Примечание:

- weld помогает убрать микро-зазоры между почти совпадающими вершинами
- weld не строит мост через реальные щели, если между кусками есть заметный зазор

## Texture Replace

Панель `Texture Replace` умеет:

- собирать базу `.paa` / `.rvmat` из папки
- находить материалы объекта
- заменять texture/material paths через A3OB-compatible material properties

## P3D Asset Library

Панель `P3D Asset Library` умеет:

- временно импортировать набор `.p3d`
- собрать temporary asset library
- конвертировать расставленные объекты в A3OB proxies

## Fixes

Панель `Fixes` содержит:

- `Fix Shading`
- `Fix Mesh/Hierarchy`

`Fix Mesh/Hierarchy` рассчитан на большие сцены и умеет:

- работать от selected/active объекта
- поэтапно join'ить меши батчами
- собирать результат в отдельную fix-коллекцию
- при необходимости центрировать результат в `(0, 0, 0)`

## Требования

- Blender `4.4+`
- включенный аддон **Arma 3 Object Builder (A3OB)**

## Установка

1. Скачайте репозиторий.
2. В Blender откройте `Edit -> Preferences -> Add-ons -> Install...`
3. Выберите файл `NH_Blender.py`
4. Включите аддон.

## Обновление во время разработки

Обычно хватает:

- `F3 -> Reload Scripts`

Если Blender держит старую UI-версию аддона:

- выключите и включите аддон в `Preferences -> Add-ons`
- или перезапустите Blender

## История изменений

Полная история изменений: [CHANGELOG.md](CHANGELOG.md)

## Ссылки

- Репозиторий: <https://github.com/BigbyOn/nh-blender-addon>
- Issues: <https://github.com/BigbyOn/nh-blender-addon/issues>

## Лицензия

MIT License. См. [LICENSE](LICENSE).
