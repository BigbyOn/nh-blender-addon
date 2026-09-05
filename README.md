# NH Blender Plugin

Форк [T3Z-ONE](https://github.com/T3Z-ONE/nh-blender-addon) на основе [BigbyOn/nh-blender-addon](https://github.com/BigbyOn/nh-blender-addon).

**NH Blender** — набор инструментов для Blender, предназначенный для подготовки, редактирования и переноса моделей **DayZ / Arma** с поддержкой `.p3d`, LOD, материалов, прокси, коллизий, snap points, библиотек ассетов и массового импорта/экспорта.

Аддон объединяет типовые операции вокруг P3D-пайплайна в одном интерфейсе и может работать как с установленным **Arma 3 Object Builder (A3OB)**, так и со встроенным P3D fallback.

- **Версия:** `0.6.2.8`
- **Blender:** `5.1.1+`
- **Интерфейс:** `3D Viewport -> N-panel -> NH Plugin`
- **Releases:** <https://github.com/T3Z-ONE/nh-blender-addon/releases>
- **Issues:** <https://github.com/T3Z-ONE/nh-blender-addon/issues>
- **История изменений:** [CHANGELOG.md](CHANGELOG.md)

---

## Что умеет NH Blender

Основные возможности:

- импорт и экспорт `.p3d`;
- импорт расстановки Terrain Builder из `.txt` с автоматической загрузкой P3D и поправкой центра всех LOD;
- встроенный P3D fallback на базе **Arma 3 Object Builder 2.5.1**;
- Drag & Drop `.p3d` в Blender;
- `Import/Export Planner` для массовой работы с моделями;
- автоматическое сохранение связи импортированной модели с исходным `.p3d` для `Back to source`;
- работа с `Resolution`, `Geometry`, `View Geometry`, `Fire Geometry`, `Roadway` и `Memory` LOD;
- отдельный генератор коллизий `Collider`;
- `Fake Terrain Geometry`;
- `Snap Points (Memory LOD)`;
- `P3D Asset Library` и интеграция с Blender Asset Browser;
- `Custom Assets`, `Cut to New Scene` и `Save to Library`;
- конвертация расставленных ассетов в P3D proxy;
- `Model Split / Merge`, `Part Transfer` и разделение модели по сетке/линиям;
- поиск и исправление проблемной геометрии;
- работа с `.paa`, `.rvmat`, PNG-preview и общим кешем текстур;
- текстурные и geometry-preview иконки для Asset Browser;
- scatter clutter-прокси из DayZ-конфигов;
- сохраняемые настройки интерфейса и настраиваемый порядок панелей;
- быстрый доступ к NH hotkeys и восстановление стандартных привязок.

---

# P3D и Arma 3 Object Builder

## Внешний A3OB больше не обязателен

NH Blender умеет использовать два варианта P3D backend.

### 1. Оригинальный Arma 3 Object Builder

Если оригинальный **Arma 3 Object Builder** установлен и активен, NH Blender использует его P3D import/export операторы.

Это предпочтительный вариант, если вам нужен полный функционал оригинального A3OB помимо возможностей NH Blender.

### 2. Встроенный P3D fallback

Если оригинальный A3OB отсутствует, NH Blender может зарегистрировать встроенный fallback из папки `NH_bundle`.

В bundle включена урезанная копия **Arma 3 Object Builder 2.5.1**, необходимая для:

- чтения `.p3d`;
- записи `.p3d`;
- P3D object/material properties;
- LOD properties;
- proxy и named properties;
- базовых P3D import/export UI и служебных структур.

Bundle специально изолирован от оригинального A3OB: его RNA-классы и operator idnames переименованы, чтобы минимизировать конфликты при одновременном наличии оригинального аддона.

Подробности: [NH_bundle/README.md](NH_bundle/README.md).

> Встроенный bundle не является полной заменой всех инструментов оригинального A3OB. Он предназначен прежде всего для обеспечения P3D-пайплайна внутри NH Blender.

---

## Импорт расстановки Terrain Builder (.txt)

Откройте **NH Plugin → Import/Export planner → Import Terrain Builder (.txt)**
или **File → Import → NH Terrain Builder (.txt)**.

Выберите TXT и папку исходных P3D. По умолчанию используется `NH_Objects Root`
из планировщика; последняя успешно использованная папка TXT-импорта запоминается отдельно.
Исходные P3D не изменяются. Каждая расстановка создаётся в новой коллекции `TB: имя_файла`.

Формат строки (восемь столбцов, углы в градусах, десятичная точка):

```text
"имя_модели";easting;northing;yaw;pitch;roll;scale;elevation;
"yantar_PipeTunnel_B_turn_r_01";204688.307990;8987.692831;175.758499;0;0;1.000002;14;
```

Для тоннеля Yantar: папка `P:\NH_Objects\Locations\Yantar`, **Map easting = 200000**,
**Map northing = 0**, **Height offset = 0**, **Model anchor = Terrain Builder XY**.
Пример восьми секций находится в `tests/fixtures/yantar_tunnel_tb.txt` в репозитории.

- **Terrain Builder XY** учитывает центр общих габаритов всех LOD по X/Y. При
  `autocenter=0` в Geometry LOD (либо первом LOD при отсутствии Geometry) используется ноль P3D.
  Есть отдельные режимы принудительной поправки XY, исходного нуля и центра XYZ.
- Поворот использует Euler `(pitch, roll, -yaw)` с порядком `ZXY`, масштаб берётся из TXT.
- **First object (XY)** вычитает координаты первой записи вместо заданного начала карты.
- **Linked copies** связывает mesh повторений одной модели; отключите для независимой геометрии.
- Поддерживаются UTF-8, UTF-16 с BOM и Windows-1251; имя может содержать расширение `.p3d`
  и относительный путь от выбранной папки. Неоднозначные и отсутствующие модели дают ошибку.

Импорт использует встроенный NH P3D backend либо активный A3OB, отдельный TXT-аддон не нужен.
Требуются исходные **MLOD P3D**; бинаризованные ODOL не поддерживаются. В сцену загружается
первый визуальный LOD, остальные читаются для расчёта габаритов. Внешние модели proxy
рекурсивно не загружаются. Высота `elevation` применяется буквально: рельеф не семплируется,
поэтому высоту над землёй при необходимости сначала нужно перевести в абсолютную.

Расстановка проверена на восьми секциях Yantar с нулевыми pitch/roll. Стыки наклонённых
моделей в Buldozer отдельно не проверялись. При ошибке загрузки незавершённая расстановка
удаляется, очередь P3D планировщика не изменяется.

# Требования

Минимально:

- **Blender 5.1.1 или новее**;
- установленный NH Blender Plugin.

Для работы с P3D:

- внешний A3OB **не обязателен**, так как имеется встроенный fallback;
- внешний A3OB можно установить отдельно, если нужен его полный оригинальный набор функций.

Для текстурного workflow:

- папки с `.paa` / `.rvmat`, если нужны material previews, Texture Replace или textured asset icons;
- **ImageToPAA / Pal2PacE из DayZ Tools** нужен только для конвертации PNG обратно в `.paa`;
- для обычного отображения `.paa` в Blender NH использует общий PNG-preview cache.

Для `P3D Asset Library`:

- доступ к папкам с исходными `.p3d`;
- при использовании стандартной структуры NH Objects — корни `Common` и `Environment`.

---

# Установка

## Обычная установка

1. Откройте страницу [Releases](https://github.com/T3Z-ONE/nh-blender-addon/releases).
2. Скачайте ZIP нужной версии NH Blender.
3. В Blender откройте:
   `Edit -> Preferences -> Add-ons`.
4. Установите ZIP-архив аддона.
5. Включите **NH Plugin for Blender**.
6. Откройте:
   `3D Viewport -> N-panel -> NH Plugin`.

## Development build

Текущая версия аддона находится в пакетной директории:

```text
NH_Blender/
    __init__.py
    nh_assets.py
    nh_base.py
    nh_collider.py
    nh_collider_exp.py
    nh_fixes.py
    nh_model_split.py
    nh_planner.py
    nh_scatter.py
    nh_snap.py
    nh_statistics.py
    nh_textures.py
    nh_ui_icons.py
    nh_ui_panels.py
    ...
```

Для сборки ZIP используется:

```text
build_addon_zip_v2.bat
```

Собранные архивы помещаются в `dist`.

Для локальной разработки также имеется:

```text
deploy_local_addon.ps1
```

После изменения Python-кода в Blender обычно достаточно:

```text
F3 -> Reload Scripts
```

Если Blender продолжает держать старые зарегистрированные классы, выключите/включите аддон или перезапустите Blender.

---

# Первичная настройка

После установки рекомендуется пройти следующие шаги.

1. Откройте `NH Plugin` в N-panel.
2. Проверьте импорт любого `.p3d`.
3. Если используете текстуры, укажите корневую папку с `.paa` / `.rvmat` в настройках texture/cache workflow.
4. Если нужен PNG -> PAA, укажите путь к `ImageToPAA.exe` / `Pal2PacE`.
5. В `P3D Asset Library` укажите корни библиотек моделей.
6. В `Cache Manager` подготовьте PNG-кеш используемых текстур.
7. Соберите или обновите NH Asset Libraries.
8. Откройте Asset Browser и проверьте иконки ассетов.
9. В `Import/Export Planner` оставьте включёнными material previews, если хотите видеть текстуры сразу после импорта.
10. В `Menu Settings` настройте порядок панелей и проверьте горячие клавиши.

---

# Панели NH Plugin

| Панель | Назначение |
| --- | --- |
| `Collider` | Генерация и проверка collision geometry |
| `Geometry LODs` | Fire Geometry, Fake Terrain Geometry, Roadway и вспомогательные LOD tools |
| `P3D Asset Library` | NH Objects libraries, Custom Assets, Asset Browser и proxy workflow |
| `Snap Points (Memory LOD)` | Создание snap-point selections и Memory LOD workflow |
| `Import/Export Planner` | Batch import/export, Back to source, Drag & Drop интеграция |
| `Fixes` | Repair, cleanup, geometry checks, component fixes |
| `Model Split / Merge` | Part Transfer, split, merge и grid/cut-line workflow |
| `Texture Replace` | Поиск/замена `.paa` и `.rvmat`, material preview и texture export |
| `Cache Manager` | Texture preview cache, asset cache и пересборка иконок |
| `Clutter Proxies (DayZ)` | Scatter clutter-прокси из DayZ config |
| `Menu Settings` | Видимость, порядок панелей и hotkeys |
| `Object Builder` | Панели оригинального или встроенного P3D backend, интегрированные в NH UI |

---

# Import / Export Planner

`Import/Export Planner` — центральный workflow для работы с большим количеством `.p3d`.

## Добавление моделей

Поддерживаются:

- `Add Files`;
- быстрое добавление модели по имени из configured NH Objects root;
- добавление уже существующих `.p3d` root collections из сцены;
- автоматическое добавление модели после обычного P3D import;
- Drag & Drop `.p3d`.

Planner хранит путь исходного файла и использует его для режима `Back to source`.

## Drag & Drop `.p3d`

`.p3d` можно перетащить непосредственно в Blender.

NH Blender перехватывает P3D drop handler и позволяет:

- добавить файл в `Import/Export Planner`;
- импортировать `.p3d` сразу.

При импорте NH также может:

- назначить source path импортированным данным;
- добавить файл в Planner;
- создать material preview nodes;
- использовать общий `.paa -> .png` cache.

## Batch Import

Доступны настройки:

- отображать material textures после импорта;
- использовать shared `.paa -> .png` cache;
- после batch-import автоматически скрывать коллекции;
- либо исключать их из текущего View Layer.

## Batch Export

Основные режимы:

- `Back to source` — экспорт обратно в исходный `.p3d`;
- `Custom folder` — экспорт в выбранную директорию.

Дополнительно можно:

- создавать `.bak` перед перезаписью;
- экспортировать только `.p3d`-подобные root collections;
- экспортировать только split parts;
- использовать `Force export all LODs`.

### Проверки перед экспортом

Перед записью NH Blender проверяет, среди прочего:

- дублирующиеся `Resolution LOD` index в одной логической ветке;
- N-gon полигоны;
- наличие ожидаемых LOD после экспорта.

При проблеме подробности выводятся в **System Console**.

### Force export all LODs

`Force export all LODs (skip validation)` предназначен как обходной режим для случаев, когда стандартная P3D LOD validation ошибочно пропускает часть LOD.

Используйте его только если понимаете причину ошибки: режим намеренно ослабляет часть проверок экспортера.

## Refresh после переименования

Если `.p3d` root collection была вручную переименована, например:

```text
model_p06.p3d -> model_p03.p3d
```

нажмите `Refresh` в Planner.

Это позволяет обновить internal source mapping и корректно использовать `Back to source` для нового имени.

---

# Collider

Новая панель `Collider` предназначена для быстрого создания collision geometry.

## Source scope

Коллайдер можно создавать:

- `from selected` — из текущего выделения;
- `per shells` — отдельно для connected shell;
- `per obj comp` — отдельно для connected components каждого объекта;
- `per objects` — отдельно для каждого выбранного объекта.

## Типы коллайдеров

Поддерживаются:

- `Box`;
- `Convex Hull`;
- `Simplify Hull`;
- `Re-Convex Selected Components`;
- `Sphere`;
- `Capsule`.

Для каждого режима доступны соответствующие настройки размеров, detail/triangle limits, offsets и прочие параметры.

## Round Box Collision

Для цилиндрических и трубчатых объектов имеется отдельный workflow:

- `Create Cylinder`;
- `Cylinder Boxes`;
- `Create Pipe`;
- `Pipe Boxes`.

Он создаёт набор box-сегментов вокруг цилиндрической или кольцевой формы, сохраняя открытое отверстие у pipe collider.

## Collision QA

Блок `Collision QA` включает:

- `Validate Collision`;
- контроль ограничений collision geometry;
- `NH Debug / Run Collision Tool Self Test`.

Также доступны быстрые операции:

- `Select Shell`;
- `Delete Last`.

---

# Geometry LODs

Панель `Geometry LODs` содержит инструменты для специальных P3D LOD.

## Geometries / Fire Geometry

Поддерживаются:

- выбор/поиск Fire Geometry target;
- быстрое назначение активного объекта;
- работа с Fire Geometry material;
- выбор faces по материалу;
- переход к папке `.rvmat`.

## Fake Terrain Geometry

`Fake Terrain Geometry` создаёт упрощённую collision representation поверхности.

Настраиваются:

- source object;
- target;
- patch size;
- minimum patch size;
- допустимая ошибка для depression;
- допустимая ошибка для hills;
- thickness.

## Misc / Roadway

Roadway workflow умеет:

- находить или создавать `Roadway` LOD;
- копировать выбранные faces из visual mesh;
- назначать texture/material;
- выбирать faces по roadway material;
- weld'ить выбранные вершины.

---

# Snap Points (Memory LOD)

Snap Points предназначены для создания согласованных named selections в `Point clouds -> Memory`.

Типовой workflow:

1. Укажите модели/targets.
2. Найдите или создайте `Point clouds -> Memory`.
3. Перейдите в `Edit Mode` исходного mesh.
4. Выделите две вершины.
5. Укажите P3D name, pair ID и нужные параметры.
6. Создайте snap pair.

Особенности:

- P3D name нормализуется автоматически;
- Memory LOD создаётся внутри правильной `.p3d` ветки;
- точки пары получают стабильную нумерацию `0/1`;
- при необходимости можно заменить существующие named groups;
- имеется fallback workflow для определения точки по грани/оси;
- доступны batch-операции и cleanup импортированных объектов;
- Plain Axis helpers находятся рядом с snap workflow.

---

# Model Split / Merge

Панель закрывает несколько сценариев работы с большими или составными P3D моделями.

## Part Transfer

Можно переносить выбранную геометрию между `.p3d` моделями в режиме:

- `Copy`;
- `Move`.

Целевая P3D-категория:

- `Resolution`;
- `Geometries`;
- `Point clouds`;
- `Roadway`.

## Named standalone model

Можно создать самостоятельную новую `.p3d` модель из выбранных объектов.

Поддерживаются:

- `Move` или `Copy`;
- экспорт рядом с исходной моделью;
- экспорт в custom directory;
- сохранение логической P3D category structure.

## Merge

Можно объединять несколько `.p3d` root collections в одну target model.

Selector сортирует `.p3d` roots выше обычных scene collections, чтобы крупные сцены было проще обслуживать.

После merge рекомендуется обновить Planner через `Refresh`.

## Grid / Cut Line Split

Для автоматического разбиения большой модели доступны editable cut guides.

Можно настроить:

- source object или source root collection;
- количество частей по X/Y;
- origin по bounds объекта, root collection, selection, 3D Cursor или вручную;
- output prefix;
- использование только видимых guides;
- сохранение оригинала;
- скрытие guides после split;
- пропуск пустых частей;
- минимальное число vertices/faces;
- автоматическое добавление результата в Planner.

---

# P3D Asset Library

`P3D Asset Library` создаёт persistent Blender Asset Libraries на основе `.p3d` моделей.

## NH Objects Libraries

Поддерживаются основные библиотеки:

- `Common`;
- `Environment`.

Основные операции:

- `Full Rebuild` — полная пересборка;
- `Add New` — добавление новых/изменённых объектов без полной пересборки;
- открытие NH Asset Browser.

Для ускорения обновления используются manifest/cache данные.

## Custom Assets

Можно поддерживать отдельную библиотеку `Custom`:

- найти P3D по имени;
- добавить конкретную модель;
- удалить конкретную модель;
- полностью очистить Custom cache/library.

## Cut / Save Asset

Новый workflow позволяет превратить часть текущей сцены в самостоятельный asset:

- `Cut to New Scene`;
- `Save to Library`.

Это удобно, когда из большой исходной модели нужно быстро получить переиспользуемый объект.

## Asset previews

Доступны два типа иконок:

- быстрый geometry preview;
- textured rendered preview.

При построении preview NH использует наиболее детальный `Resolution` LOD и исключает служебные Geometry / View Geometry / Fire Geometry / Roadway LOD из preview-модели.

### Ручной ракурс через `nh_cam`

Чтобы указать желаемый ракурс иконки:

1. Добавьте одну точку в Memory LOD.
2. Назначьте selection:
   `nh_cam`.

Для поворота камеры вокруг вертикальной оси поддерживаются:

```text
nh_cam_90
nh_cam_180
nh_cam_270
```

Ортографический масштаб рассчитывается автоматически так, чтобы модель помещалась в квадрат preview без обрезания.

## Placed Assets -> P3D Proxies

Размещённые объекты из Asset Browser можно конвертировать в P3D proxy.

Proxy можно создавать/дублировать в:

- `Resolution`;
- `Geometries`;
- `Roadway`;
- `Point Clouds`.

При необходимости target `.p3d` collection и target LOD можно указать вручную; иначе NH пытается определить их из текущего контекста.

---

# Cache Manager

`Cache Manager` объединяет операции с texture и asset cache.

## Texture cache

Используется общий PNG cache для `.paa`, чтобы разные части NH Blender не выполняли одну и ту же конвертацию повторно.

Кеш используется для:

- material preview после P3D import;
- Texture Replace;
- textured asset icons;
- NH Asset Libraries.

Операции включают:

- cache только реально используемых NH-текстур;
- пересборку используемых текстур;
- обновление всей выбранной папки;
- полную пересборку;
- открытие cache directory;
- просмотр последнего report.

## Asset cache

Можно:

- полностью пересобрать NH Libraries;
- добавить только новые P3D;
- пересобрать иконки;
- открыть NH library cache;
- выполнить `Force Rebuild All Icons + Textures`.

`Add New` и Custom workflow наследуют режим существующей библиотеки и не должны неожиданно переключать textured preview на geometry preview.

Если требуемая `.paa` ещё не имеет PNG-cache, NH может подготовить preview по мере необходимости.

---

# Texture Replace

Панель предназначена для массовой работы с material texture paths.

Она умеет:

- индексировать `.paa` и `.rvmat` в выбранных директориях;
- находить используемые материалы;
- заменять texture/material paths;
- работать с A3OB-compatible material properties;
- создавать/обновлять Image Texture preview nodes;
- переиспользовать shared PNG cache;
- экспортировать отсутствующие текстуры из source roots;
- при наличии DayZ Tools конвертировать подготовленные изображения обратно в `.paa`;
- создавать `.rvmat` в соответствующих workflow.

`ImageToPAA / Pal2PacE` требуется только там, где выполняется PNG -> PAA.

---

# Fixes

`Fixes` содержит операции для восстановления импортированных P3D и очистки проблемной геометрии.

## Repair Invalid P3D Selections

Repair workflow может:

- объединить повреждённые mesh fragments;
- восстановить нарушенные vertex-group / named-selection связи;
- собрать результат в корректный `Resolution 0`;
- вернуть результат в ожидаемую `.p3d` hierarchy.

## Material-safe merge

`Merge By Distance (Keep Materials)` предназначен для объединения близких вершин без нежелательного разрушения material boundaries.

## Proxy repair

`Fix Proxy Triangles` исправляет P3D proxy meshes, у которых ожидается один triangle, но topology повреждена.

## Component fixes from file

Можно загрузить список проблемных компонентов из `.txt`, сопоставить его с активным P3D/LOD и выделить соответствующие vertex groups.

После этого доступны операции удаления faces/edges с сохранением vertices.

## Geometry checks

В зависимости от режима доступны проверки и выбор:

- isolated/loose vertices;
- loose vertices вне Memory;
- N-gon meshes;
- маленькие мусорные connected islands;
- coplanar flat plates;
- проблемные planar N-gon cases.

Подробные результаты сложных проверок выводятся в System Console.

---

# Clutter Proxies (DayZ)

NH Blender умеет читать DayZ config и создавать scatter для clutter proxies.

Используются данные вида:

```text
CfgWorlds
  -> CAWorld
     -> Clutter

CfgSurfaceCharacters
```

Поддерживаются настройки плотности, grid, randomization, ограничений по высоте/дистанции, slope falloff и количества создаваемых proxy.

---

# Menu Settings и интерфейс

`Menu Settings` позволяет адаптировать N-panel под конкретный workflow.

Можно:

- скрывать неиспользуемые панели;
- менять порядок основных панелей NH Plugin;
- вернуть стандартный порядок;
- просматривать текущие NH keybinds;
- открыть Blender Keymap Preferences;
- восстановить стандартные NH hotkeys.

Настройки UI сохраняются между сессиями Blender.

## Основные горячие клавиши

Стандартные NH bindings включают:

- `Ctrl+Shift+C` — `Copy Selected Verts To Geometry`;
- `Ctrl+Shift+X` — `Select Isolated Verts`;
- `Mouse4` — collider `Selection -> Hull`;
- `Mouse5` — collider `Selection -> Box`;
- `Ctrl+Shift+P` — `Create Plain Axis Pivot`, если сочетание свободно.

Фактическую текущую привязку всегда можно проверить в:

```text
NH Plugin -> Menu Settings -> Custom Keybinds
```

Если shortcut занят другим аддоном или пользовательской настройкой Blender, NH показывает текущий статус в этом разделе.

---

# Object Builder UI

Если активен оригинальный A3OB или встроенный P3D bundle, его поддерживаемые P3D panels интегрируются в NH Plugin UI.

NH Blender:

- добавляет NH icon в заголовки Object Builder panels;
- убирает лишний префикс `Object Builder:` из названия там, где это возможно;
- сохраняет оригинальный P3D functionality backend.

---

# Сохранение настроек и локальные данные

NH Blender сохраняет часть пользовательских настроек между сессиями Blender, включая параметры основных workflow и порядок UI panels.

Persisted UI state хранится локально в Blender user config.

В проекте также присутствует модуль локальной usage statistics, но он **отключён по умолчанию**. В текущей конфигурации данные никуда не отправляются и, если модуль будет вручную включён разработчиком, сохраняются только локально.

Экспериментальный server-side work tracking вынесен в `_staged` и не является активной частью текущего аддона.

---

# Частые проблемы

## P3D import/export недоступен

1. Перезапустите Blender или выполните `F3 -> Reload Scripts`.
2. Проверьте, что NH Blender включён.
3. Если установлен оригинальный A3OB — убедитесь, что он корректно активен.
4. Если A3OB не установлен — NH должен зарегистрировать встроенный P3D fallback.
5. Посмотрите System Console на сообщения о `bundled P3D module import failed` или `failed to register bundled P3D`.

## Материалы импортированы, но текстуры не видны

- включите material preview после P3D import;
- проверьте texture source roots;
- убедитесь, что `.paa` доступны;
- обновите shared PNG cache через `Cache Manager`.

## `ImageToPAA not found`

Это не мешает обычному P3D import и просмотру уже существующих `.paa`.

`ImageToPAA / Pal2PacE` требуется только для PNG -> PAA. Укажите путь к инструменту из DayZ Tools в texture workflow.

## Asset Browser показывает geometry icons вместо textured icons

1. Проверьте наличие исходных `.paa`.
2. Обновите PNG cache.
3. Выполните `Rebuild Icons` или `Force Rebuild All Icons + Textures`.

## Новые P3D не появляются в библиотеке

Используйте `Add New`. Если cache/manifest повреждён или структура библиотек сильно изменилась — используйте `Full Rebuild`.

## `Back to source` указывает на старое имя

Если root collection была переименована вручную, выполните `Refresh` в Planner.

## Export остановлен из-за N-gon или duplicate Resolution LOD

Откройте **System Console**: NH выводит конкретную collection/object path и причину остановки.

Исправление исходной структуры предпочтительнее, чем использование `Force export all LODs`.

## Горячая клавиша не работает

Откройте:

```text
Menu Settings -> Custom Keybinds -> Open Keymap
```

Проверьте конфликт с Blender или другим add-on. При необходимости используйте `Restore Defaults`.

---

# Архитектура проекта

Текущий NH Blender разделён на доменные модули вместо одного большого Python-файла.

Основные части:

```text
NH_Blender/
├── __init__.py           # регистрация пакета и public surface
├── nh_base.py            # общие настройки, persistence, keymaps, helpers
├── nh_assets.py          # P3D Asset Library и proxy workflow
├── nh_collider.py        # Geometry/Roadway helpers
├── nh_collider_exp.py    # новый Collider и Collision QA
├── nh_fixes.py           # repair и geometry fixes
├── nh_model_split.py     # split / merge / transfer
├── nh_planner.py         # P3D Planner и Drag & Drop
├── nh_scatter.py         # settings, UI state, clutter/scatter
├── nh_snap.py            # Snap Points и P3D backend bridge
├── nh_textures.py        # texture replace/cache/export
├── nh_ui_icons.py        # NH UI icons
├── nh_ui_panels.py       # основные N-panel panels
└── nh_statistics.py      # отключённая по умолчанию локальная статистика
```

Дополнительно:

```text
NH_bundle/                # встроенный P3D/A3OB fallback
_source_monolith.py       # legacy/reference monolithic source
_staged/                  # неактивные экспериментальные компоненты
build_addon_zip_v2.bat    # сборка ZIP
Deploy_local_addon.ps1    # локальный development deploy
```

Модульная структура упрощает дальнейшее развитие и позволяет изменять отдельные части аддона без работы с монолитным файлом на десятки тысяч строк.

---

# Сборка релиза

Основной build script:

```bat
build_addon_zip_v2.bat
```

Он использует версию из `bl_info` активного package entrypoint и собирает distributable ZIP в `dist`.

Для release/development workflow доступны параметры самого build script; перед публикацией рекомендуется проверить:

- версию в `NH_Blender/__init__.py`;
- `CHANGELOG.md`;
- содержимое ZIP;
- наличие bundled texture tools;
- наличие `NH_bundle`, если релиз должен работать без внешнего A3OB;
- импорт и экспорт тестового `.p3d` на чистой установке Blender.

---

# История изменений

Полный changelog:

[CHANGELOG.md](CHANGELOG.md)

Текущая development-версия определяется `bl_info` в:

```text
NH_Blender/__init__.py
```

Для опубликованных сборок используйте страницу:

<https://github.com/T3Z-ONE/nh-blender-addon/releases>

---

# Лицензирование

Собственный код NH Blender сопровождается корневым файлом [LICENSE](LICENSE).

При этом `NH_bundle` содержит код **Arma 3 Object Builder 2.5.1**, распространяемый по **GNU GPL v3**. Bundle имеет собственные сведения о происхождении и лицензии:

- [NH_bundle/README.md](NH_bundle/README.md)
- [NH_bundle/LICENSE](NH_bundle/LICENSE)

Поэтому сборка NH Blender, которая распространяется вместе с `NH_bundle`, не должна описываться просто как «MIT-only»: при распространении необходимо учитывать условия GPLv3 для включённого A3OB-кода.

---

# Ссылки

- GitHub: <https://github.com/T3Z-ONE/nh-blender-addon>
- Releases: <https://github.com/T3Z-ONE/nh-blender-addon/releases>
- Issues: <https://github.com/T3Z-ONE/nh-blender-addon/issues>
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Bundled P3D backend: [NH_bundle/README.md](NH_bundle/README.md)
