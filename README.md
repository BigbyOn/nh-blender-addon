# NH Blender Plugin

Blender-аддон для пайплайна DayZ/Arma с интеграцией **Arma 3 Object Builder (A3OB)**.

Расположение в Blender: `3D Viewport -> N Panel -> NH Plugin`

Текущая версия: **0.2.0**

## Возможности

- Scatter clutter-прокси из DayZ-конфига: `CfgWorlds -> CAWorld -> Clutter` + `CfgSurfaceCharacters`
- Memory LOD / Snap Points workflow
- Collider LOD workflow для `Geometry` / `View Geometry` / `Fire Geometry`
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

- создавать или находить отдельный target LOD: `Geometry`, `View Geometry` или `Fire Geometry`
- автоматически обновлять A3OB LOD props и имя target-объекта при смене `Target LOD`
- складывать collider-меши в коллекцию `Geometry`
- красить collider-объекты в светло-желтый цвет для быстрого визуального отличия от `Resolution`
- поддерживать OB-style workflow через хоткеи
- показывать fallback-кнопки через раскрывающийся блок `Hotkeys -> Buttons`
- давать отдельную кнопку `Selected Loose Geometry Verts -> Hull` для работы по выделенным loose verts
- показывать дополнительные редкие build-кнопки через `Extra Build`

### Collider workflow

Базовый сценарий:

1. На исходном визуальном меше войдите в `Edit Mode`.
2. В блоке `Target` выберите нужный LOD: `Geometry`, `View Geometry` или `Fire Geometry`.
3. Нажмите `Create/Find Collider LOD`.
4. Выделите вершины и нажмите `Ctrl+Shift+C` для копирования в target LOD.
5. В target LOD при необходимости используйте `Shift+D` и перемещение вершин.
6. Выделите loose verts и нажмите `Selected Loose Geometry Verts -> Hull`.

Дополнительно:

- `Mouse4` делает `Selection -> Hull` по текущему выделению вершин / ребер / полигонов
- `Mouse5` выбирает только изолированные вершины без ребер и полигонов
- `Selected Loose Geometry Verts -> Hull` работает по выделению внутри target LOD в `Edit Mode`

### Collider hotkeys

- `Ctrl+Shift+C` — `Copy Selected Verts To Geometry`
- `Mouse5` — `Select Isolated Verts`
- `Mouse4` — `Selection -> Hull`

Примечание:

- `Mouse5` назначен на `Select Isolated Verts`
- `Selected Loose Geometry Verts -> Hull` доступен через блок `Hotkeys -> Buttons`

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
- выбирать активный материал у `Roadway`-объекта
- назначать `.rvmat` / `.paa` через файловый выбор прямо в выбранный `Roadway Material`
- сшивать почти совпадающие вершины только в текущем выделении `Roadway` для более цельной nav/path геометрии

### Roadway workflow

1. На визуальном меше выделите нужные полигоны в `Edit Mode`.
2. Нажмите `Create/Find Misc Roadway`.
3. Нажмите `Copy Selected Faces To Roadway`.
4. При необходимости выберите `Roadway Material` и назначьте `.rvmat` или `.paa` через кнопку с иконкой папки.
5. Перейдите в `Roadway` и при необходимости нажмите `Weld Roadway` по текущему выделению.

### Roadway настройки

- `Material` — выбор текущего материала на `Roadway`-объекте
- Кнопка с иконкой папки — выбор `.rvmat` / `.paa` для выбранного `Roadway Material`
- `Roadway Weld Distance` — дистанция сшивания близких вершин в `Roadway`
- `Weld Roadway` — merge-by-distance только по текущему выделению в `Edit Mode`

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

Последнее обновление:

- `0.2.0` (`2026-03-30`) — целевая версия Blender обновлена до `5.1`, релизная версия аддона поднята до `0.2.0`
- `0.1.9` (`2026-03-29`) — target LOD selector для collider workflow, `Roadway Material` picker и более точный selection-based `Weld Roadway`

## Ссылки

- Репозиторий: <https://github.com/BigbyOn/nh-blender-addon>
- Issues: <https://github.com/BigbyOn/nh-blender-addon/issues>

## Лицензия

MIT License. См. [LICENSE](LICENSE).
