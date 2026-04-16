# NH Blender Plugin

Blender-аддон для пайплайна DayZ/Arma с интеграцией **Arma 3 Object Builder (A3OB)**.

Расположение в Blender: `3D Viewport -> N Panel -> NH Plugin`

Текущая релизная версия: **0.4.0**

Состояние ветки: помимо релиза `0.4.0`, в рабочем дереве уже есть новые изменения, описанные в [CHANGELOG.md](CHANGELOG.md) в секции `Unreleased`.

## Возможности

- Scatter clutter-прокси из DayZ-конфига: `CfgWorlds -> CAWorld -> Clutter` + `CfgSurfaceCharacters`
- `Snap Points (Memory LOD)` с ручным A/V workflow по 2 выбранным вершинам
- Collider LOD workflow для `Geometry` / `View Geometry` / `Fire Geometry`
- `Misc / Roadway` workflow для подготовки walkway-LOD мешей
- `Texture Replace` через A3OB material properties (`.paa` / `.rvmat`)
- Batch import/export `.p3d`
- `Import/Export planner` с быстрым добавлением моделей по имени из `NH_Objects`
- `Model Split` для part-моделей и named standalone-моделей
- Temporary `P3D Asset Library` и конвертация размещённых объектов в A3OB proxy
- `Fixes` для shading, иерархии, component-fix списков и чистки проблемной геометрии

## Основные панели

- `Geometry Collider`
- `Clutter Proxies (DayZ)`
- `Snap Points (Memory LOD)`
- `P3D Asset Library`
- `Fixes`
- `Import/Export planner`
- `Model Split`
- `Texture Replace`

## Snap Points

Панель `Snap Points (Memory LOD)` работает через ручной workflow:

1. На исходном меше войдите в `Edit Mode`.
2. Выделите ровно 2 вершины.
3. Нажмите `Create/Find Point clouds > Memory`, если memory-LOD ещё не создан.
4. Выберите `P3D Name`, `ID`, сторону `A/V` и `Snap Axis`.
5. Нажмите `Create Snap Points`.

Что важно:

- `P3D Name` автоматически очищается от пробелов, подчёркиваний, `.p3d` и лишних символов.
- `Point clouds > Memory` создаётся в нужной `.p3d`-ветке и не цепляет чужие `Memory` из других моделей.
- Plain-axis pivot инструменты находятся в этой же панели.

## Geometry Collider

Панель `Geometry Collider` рассчитана на workflow, близкий к Object Builder.

Что умеет:

- создавать или находить target LOD: `Geometry`, `View Geometry`, `Fire Geometry`
- автоматически обновлять A3OB LOD props и имя target-объекта при смене `Target LOD`
- складывать collider-меши в коллекцию `Geometries`
- красить collider-объекты в отдельный цвет для быстрого визуального отличия от `Resolution`
- поддерживать OB-style workflow через хоткеи и fallback-кнопки
- давать быстрые build-операции `Selection -> Hull`, `Selection -> Box`, `Object -> Bounds`

Основные хоткеи:

- `Ctrl+Shift+C` — `Copy Selected Verts To Geometry`
- `Mouse5` — `Select Isolated Verts`
- `Mouse4` — `Selection -> Hull`

### Misc / Roadway

В той же панели есть блок `Misc / Roadway`, который умеет:

- создавать или находить коллекцию `Misc`
- создавать или находить `Roadway` LOD внутри `Misc`
- копировать выделенные полигоны из визуала в `Roadway`
- назначать `Roadway Material` и путь к `.rvmat` / `.paa`
- выполнять `Weld Roadway` только по текущему выделению в `Edit Mode`

## Fixes

Панель `Fixes` теперь закрывает несколько разных задач.

### Shading / Hierarchy

- `Fix Shading`
- `Fix Mesh/Hierarchy`
- `Repair Invalid A3OB Selections`

`Fix Mesh/Hierarchy` рассчитан на большие сцены и умеет:

- работать от selected/active объекта
- join'ить меши батчами
- складывать результат в отдельную fix-коллекцию
- при необходимости центрировать результат в `(0, 0, 0)`

### Component fixes from `.txt`

Новый workflow для исправления плохих компонентов:

1. Укажите `Fix List .txt`.
2. Активируйте нужный `Geometry` / `View Geometry` / `Fire Geometry` объект.
3. Нажмите `Select Bad Components From List`.
4. После выделения используйте `Delete Faces/Edges Keep Verts`, если нужно удалить проблемные faces/edges, но сохранить точки.

Аддон сопоставляет:

- имя `.p3d` root-коллекции
- активный LOD
- vertex groups, перечисленные в fix-list файле

Если каких-то групп не хватает, они выводятся в `System Console`.

### Поиск проблемной геометрии

В `Edit Mode` доступны два поиска:

- `Find Trash` — ищет маленькие connected face islands, которые похожи на мусор
- `Find Flat Plates` — ищет плоские coplanar-островки в одной плоскости

Это удобно перед экспортом, когда нужно быстро вычистить артефакты меша.

## Import/Export planner

Панель `Import/Export planner` поддерживает batch-import и batch-export `.p3d`.

Актуально сейчас:

- можно вручную собирать список файлов на импорт
- можно быстро добавить модель по имени через блок `Quick Add From NH_Objects`
- batch-export умеет работать с обычными root-коллекциями и `.p3d` root-ветками

Перед экспортом аддон дополнительно проверяет:

- дубли `Resolution LOD` индексов внутри одной логической ветки
- наличие `n-gon`-полигонов в экспортируемых LOD-мешах

Если такая проблема найдена, экспорт конкретной коллекции останавливается заранее, а детали пишутся в `System Console`.

## Model Split

Панель `Model Split` поддерживает два сценария:

- создание обычных split-part моделей с суффиксами вида `*_01.p3d`, `*_02.p3d`
- `Separate -> Named Standalone Model` для сборки новой самостоятельной модели из выбранных объектов

Для named standalone workflow доступны:

- `Move` или `Copy` выбранных объектов
- экспорт рядом с исходной моделью или в отдельную папку
- сохранение логических путей вроде `Visuals`, `Geometries`, `Misc`, `Point clouds`

Такой результат затем нормально работает с `Back to source` в `Import/Export planner`.

## P3D Asset Library

Панель `P3D Asset Library` умеет:

- временно импортировать набор `.p3d`
- собирать temporary asset library
- конвертировать расставленные объекты в A3OB proxies

## Texture Replace

Панель `Texture Replace` умеет:

- собирать базу `.paa` / `.rvmat` из папки
- находить материалы объекта
- заменять texture/material paths через A3OB-compatible material properties

## Требования

- Blender `5.1+`
- включенный аддон **Arma 3 Object Builder (A3OB)**

## Установка

1. Скачайте репозиторий.
2. В Blender откройте `Edit -> Preferences -> Add-ons -> Install...`
3. Выберите файл `NH_Blender.py`.
4. Включите аддон.

## Обновление во время разработки

Обычно хватает:

- `F3 -> Reload Scripts`

Если Blender держит старую UI-версию аддона:

- выключите и включите аддон в `Preferences -> Add-ons`
- или перезапустите Blender

## История изменений

Полная история изменений: [CHANGELOG.md](CHANGELOG.md)

Коротко по актуальному состоянию:

- `Unreleased` (`2026-04-16`) — component-fix `.txt` workflow, `Delete Faces/Edges Keep Verts`, поиск `Find Trash` / `Find Flat Plates`, quick-add импорта по имени из `NH_Objects`, named standalone model split и дополнительные export-проверки на duplicate `Resolution LOD` и `n-gon`
- `0.4.0` (`2026-04-12`) — ручной A/V workflow для `Snap Points`, автоматическое создание `Point clouds > Memory`, scatter по выделенным полигонам и `Slope Falloff`
- `0.3.1` (`2026-04-07`) — `Import/Export planner`, `Model Split`, кеш texture preview и batch-export фильтр для split-part коллекций

## Ссылки

- Репозиторий: <https://github.com/BigbyOn/nh-blender-addon>
- Issues: <https://github.com/BigbyOn/nh-blender-addon/issues>

## Лицензия

MIT License. См. [LICENSE](LICENSE).
