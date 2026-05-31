# NH Blender Plugin

Blender-аддон для пайплайна DayZ/Arma с интеграцией **Arma 3 Object Builder (A3OB)**.

Расположение в Blender: `3D Viewport -> N Panel -> NH Plugin`

Текущая релизная версия: **0.5.3.0**

Состояние ветки: актуальный релиз `0.5.3.0`; подробности изменений описаны в [CHANGELOG.md](CHANGELOG.md).

## Возможности

- Scatter clutter-прокси из DayZ-конфига: `CfgWorlds -> CAWorld -> Clutter` + `CfgSurfaceCharacters`
- `Snap Points (Memory LOD)` с ручным A/V workflow по 2 выбранным вершинам и стабильной нумерацией `0/1`
- Collider LOD workflow для `Geometry` / `View Geometry` / `Fire Geometry`
- `Misc / Roadway` workflow для подготовки walkway-LOD мешей
- `Texture Replace` через A3OB material properties (`.paa` / `.rvmat`)
- общий PNG-кеш превью для `.paa`, чтобы импорт, карточки материалов и asset icons не конвертировали одни и те же текстуры заново
- Batch import/export `.p3d`
- `Import/Export planner` с быстрым добавлением моделей по имени из `NH_Objects`
- `Model Split` для part-моделей и named standalone-моделей
- `P3D Asset Library`: temporary library, persistent `NH_Objects` asset libraries для `Common` / `Environment`, кастомные иконки Asset Browser и конвертация размещённых объектов в A3OB proxy
- `Cache Manager` для обновления кеша текстур, пересборки NH-библиотек и открытия папок кеша
- `Menu Settings` для включения/скрытия панелей `NH Plugin` и просмотра кастомных хоткеев
- `Fixes` для shading, иерархии, component-fix списков и чистки проблемной геометрии

## Основные панели

- `Collider`
- `Geometry LODs`
- `Clutter Proxies (DayZ)`
- `Snap Points (Memory LOD)`
- `P3D Asset Library`
- `Fixes`
- `Import/Export planner`
- `Model Split`
- `Cache Manager`
- `Texture Replace`
- `Menu Settings`

## Snap Points

Панель `Snap Points (Memory LOD)` работает через ручной workflow:

1. Выберите `A Target` и `V Target`: обычные mesh-объекты нужных моделей.
2. Нажмите `Create/Find Point clouds > Memory`, если хотите заранее подготовить memory-LOD.
3. На исходном меше войдите в `Edit Mode`.
4. Выделите ровно 2 вершины.
5. Выберите `P3D Name`, `ID` и `Snap Axis`.
6. Нажмите `Create Snap Points`.

Что важно:

- `P3D Name` автоматически очищается от пробелов, подчёркиваний, `.p3d` и лишних символов.
- `Create Snap Points` сам найдёт или создаст `Point clouds > Memory` внутри `.p3d`-веток выбранных `A Target` / `V Target`.
- `Point clouds > Memory` создаётся в нужной `.p3d`-ветке и не цепляет чужие `Memory` из других моделей.
- Точки `0/1` сортируются по мировым координатам: сначала выбранная `Snap Axis`, а если пара по ней не различается, то фактическая ось разлёта точек. Меньшая координата получает `0`, большая `1`.
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
- `Ctrl+Shift+X` — `Select Isolated Verts`
- `Mouse4` — `Selection -> Hull`
- `Mouse5` — `Selection -> Box`

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
- собирать persistent Blender asset libraries из `NH_Objects/Common` и `NH_Objects/Environment`
- регистрировать библиотеки `NH Objects - Common` и `NH Objects - Environment` в Asset Browser
- создавать быстрые geometry-preview и, при включенном `Use textured icons`, textured rendered previews по уже готовому кешу текстур
- пропускать уже актуальные библиотеки по manifest-файлу и пересобирать только изменившиеся папки
- конвертировать расставленные объекты в A3OB proxies

Для `NH_Objects` workflow укажите корни `Common` и `Environment`, затем нажмите `Build NH Libraries`. После сборки можно открыть Asset Browser кнопкой рядом с build-кнопкой или через `Cache Manager`.

## Cache Manager

Панель `Cache Manager` собирает операции с кешами в одном месте:

- `Cache NH Used` / `Rebuild NH Used` обновляют PNG-кеш только для текстур, реально использованных в NH-библиотеках
- `Update All Folder` / `Rebuild All (slow)` работают с выбранной папкой текстур целиком
- `Open Texture PNG Cache` и `Report` открывают папку кеша и последний отчет
- `Build / Update Libraries`, `Rebuild Icons` и `Open NH Library Cache` управляют кешируемыми `.blend`-библиотеками и их иконками

## Texture Replace

Панель `Texture Replace` умеет:

- собирать базу `.paa` / `.rvmat` из папки
- находить материалы объекта
- заменять texture/material paths через A3OB-compatible material properties
- обновлять material preview nodes и переиспользовать общий PNG-кеш `.paa -> .png`

## Menu Settings

Панель `Menu Settings` позволяет скрывать редко используемые блоки `NH Plugin`, оставляя в `N-panel` только нужный workflow.

В блоке `Custom Keybinds` можно быстро посмотреть актуальные привязки:

- `Ctrl+Shift+C` — `Copy Selected Verts To Geometry`
- `Ctrl+Shift+X` — `Select Isolated Verts`
- `Mouse4` — `Selection -> Hull`
- `Mouse5` — `Selection -> Box`
- `Ctrl+Shift+P` — `Create Plain Axis Pivot`, если хоткей свободен

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

- `0.5.3.0` (`2026-05-31`) — PNG-кеш превью текстур, persistent `NH_Objects` asset libraries для `Common` / `Environment`, кастомные иконки Asset Browser, `Cache Manager`, `Menu Settings`, обновленные хоткеи collider workflow и улучшения proxy/collider target selection
- `0.5.2.31` (`2026-05-29`) — drag-and-drop `.p3d` сразу добавляет файлы в `Import/Export planner`, список dropped-файлов сортируется натурально
- `0.5.2.29` (`2026-05-29`) — drag-and-drop `.p3d`, `Visual 0 Only` / `Show All`, merge workflow в `Model Split`, `Material Safe Merge`, `Plain Axis Pivot` и fixes для snap points
- `0.4.9.1` (`2026-05-01`) — безопасные `.bak` при batch-export, диагностика missing LOD, рабочий `Force export all LODs`, loose-vertex export checks, auto mass для `Geometry` LOD, `Fix Proxy Triangles` и улучшения `Fire Geometry` / `Roadway` UI
- `0.4.0` (`2026-04-12`) — ручной A/V workflow для `Snap Points`, автоматическое создание `Point clouds > Memory`, scatter по выделенным полигонам и `Slope Falloff`
- `0.3.1` (`2026-04-07`) — `Import/Export planner`, `Model Split`, кеш texture preview и batch-export фильтр для split-part коллекций

## Ссылки

- Репозиторий: <https://github.com/BigbyOn/nh-blender-addon>
- Issues: <https://github.com/BigbyOn/nh-blender-addon/issues>

## Лицензия

MIT License. См. [LICENSE](LICENSE).
