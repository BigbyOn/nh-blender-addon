# Журнал изменений

Все заметные изменения проекта фиксируются в этом файле.

## [0.5.3.12] - 2026-06-19

### Добавлено
- В `Model Split / Merge` добавлен workflow `Grid Cutter Split`: генерация сетки cutter-cube-ов с настраиваемыми размерами ячеек, количеством по осям, режимом origin и префиксом выходных `.p3d`-частей.
- Добавлены команды `Create Cutter Grid`, `Select Cutter Grid`, `Clear Cutter Grid` и `Split Source By Cutter Grid` для подготовки сетки, выбора cutter-объектов, очистки tagged cutter-ов и разрезания source object или `.p3d` root-коллекции.
- Добавлены настройки grid-split: выбор source object/root collection, cutter collection, фильтр только видимых cutter-ов, сохранение оригинала, скрытие cutter-ов после split, пропуск пустых частей по порогам vertices/faces и автодобавление результата в `Import/Export planner`.

### Изменено
- Версия аддона поднята до `0.5.3.12`.
- Результат `Grid Cutter Split` создается как набор отдельных `.p3d` root-коллекций внутри output container, с сохранением A3OB category layout для Resolution/Point Clouds/Geometry и поддержкой mesh, point-cloud и proxy pieces.
- Настройки `Grid Cutter Split` добавлены в persisted UI state, чтобы размеры сетки, counts, origin, output prefix и поведение split сохранялись между сессиями.

### Исправлено
- `Grid Cutter Split` удаляет пустые result roots, если cutter не создал валидных pieces, и пишет подробности failures в System Console вместо молчаливого создания пустых `.p3d`-частей.

## [0.5.3.11] - 2026-06-17

### Добавлено
- В `Texture Replace -> Export Missing Textures from Sources` добавлен список `Source Texture Roots`: можно держать несколько DDS-корней, добавлять и удалять их прямо из UI и сканировать все источники за один запуск.
- Добавлен modal-export недостающих текстур с прогрессом, отменой, workspace-status и отчетом последнего запуска.
- Добавлена библиотека `NH Objects - Custom`: поиск `.p3d` по имени через `Custom Search Root`, добавление/удаление ассетов по имени и очистка custom-кеша.
- Добавлена команда `Clean Source Cache Files` для удаления старых `_NH_AssetLibrary`-кешей и preview-папок из исходных `NH_Objects`-директорий.
- Добавлена опция `Duplicate to all Resolution LODs` при конвертации размещенных ассетов в A3OB proxies.
- Добавлен режим `Delete All Plain Axes + Save Z`, который удаляет Plain Axis helper-ы, возвращает модели по X/Y и сохраняет текущую world Z-высоту.
- В `Import/Export planner` добавлена кнопка `Refresh` для добавления текущих `.p3d` root-коллекций сцены, удаления отсутствующих после merge и перепривязки `Back to source` после rename.

### Изменено
- Версия аддона поднята до `0.5.3.11`.
- `Model Split` переименован в `Model Split / Merge`; selector `Source` в `Merge Collections` показывает `.p3d` root-коллекции первыми, затем остальные коллекции по алфавиту.
- Кнопки `Add` и `Remove` в `Import/Export planner` сделаны компактными icon-only.
- `Convert Selected Assets To Proxies` переработан под явные поля `Proxy Source Object` и `Target Resolution / LOD`: target теперь должен быть A3OB LOD mesh, а созданные proxies получают корректные `proxy_path` / `proxy_index`, wire-display и имя `proxy: ...`.
- Экспорт текстур теперь использует встроенный Python DDS backend по умолчанию, переиспользует уже существующие PNG/PAA, обновляет PNG-кеш после успешного PNG -> PAA и пишет подробные события по diffuse/NOHQ/SMDI/RVMAT.
- Поиск texture candidates стал строже и устойчивее: учитываются A3OB material paths, image nodes, имена изображений и материалов, Blender numeric suffixes и варианты base-name; placeholder-материалы и некорректные Windows-пути отбрасываются.
- README дополнен быстрым setup workflow, проверкой настройки, частыми проблемами и актуальными установочными требованиями для ZIP-архива.

### Исправлено
- Исправлен `Convert Selected Assets To Proxies`: теперь можно выбирать source отдельно от target, создавать настоящие A3OB proxy-объекты внутри нужного LOD и при необходимости дублировать их по всем Resolution LODs одного `.p3d` root.
- Исправлен merge `Point clouds > Memory`: Memory-точки пересобираются в координатах target-модели с сохранением world-позиций и vertex groups, чтобы после merge/export они не уезжали в Object Builder.
- Исправлены случаи, когда texture export мог принимать `P3D: no material`, `<no materials>`, пути из папки установки Blender или другие невалидные строки как реальные имена текстур.
- Улучшена обработка отсутствующего `ImageToPAA`: PNG-результат сохраняется как доступный fallback, а предупреждение попадает в отчет без срыва всего экспорта.
- Очистка Plain Axis теперь дополнительно ремонтирует `Memory LOD` constraints перед удалением helper-ов.

## [0.5.3.0] - 2026-05-31

### Добавлено
- Добавлен общий кеш PNG-превью для `.paa`-текстур: импорт и обновление материалов могут переиспользовать уже сконвертированные изображения вместо повторной конвертации.
- Добавлен `Cache Manager` в `N-panel -> NH Plugin` для управления кешем текстур, пересборки NH-библиотек, пересборки иконок и быстрого открытия папок кеша.
- Добавлена сборка persistent Blender asset libraries для `NH_Objects` из корней `Common` и `Environment`, с регистрацией библиотек `NH Objects - Common` и `NH Objects - Environment`.
- Добавлены manifest-файлы для persistent asset libraries, чтобы пропускать уже актуальные библиотеки и пересобирать их только при изменении набора `.p3d`, версии или режима превью.
- Добавлены кастомные иконки Asset Browser для импортированных `.p3d`: быстрые geometry-preview и опциональные textured rendered previews по уже готовому кешу текстур.
- Добавлены кнопки `Build NH Libraries`, `Open NH Asset Browser`, `Rebuild Icons`, `Open NH Library Cache`, `Open Texture PNG Cache` и `Report`.
- Добавлена панель `Menu Settings` для включения/скрытия основных блоков `NH Plugin` и просмотра текущих кастомных хоткеев.

### Изменено
- Версия аддона поднята до `0.5.3.0`.
- `P3D Asset Library` расширен под workflow `NH_Objects`: отдельные поля для `Common` и `Environment`, флаг `Use textured icons` и сборка кешируемых `.blend`-библиотек.
- Импорт материалов теперь пишет подробную статистику превью: найденные текстуры, cache hit/cache created, packed images, missing и warnings.
- `Texture Replace` и импорт через planner синхронизированы с новым кешем превью, чтобы карточки материалов и иконки ассетов показывали текстуры сразу после импорта.
- Хоткеи collider workflow обновлены: `Ctrl+Shift+X` для `Select Isolated Verts`, `Mouse4` для `Selection -> Hull`, `Mouse5` для `Selection -> Box`.
- Регистрация хоткеев стала аккуратнее: старые привязки операторов очищаются перед добавлением новых, чтобы не копились дубли после перезагрузки аддона.
- `Geometry Collider (exp)` теперь безопаснее выбирает target LOD и не использует source object как collider target.
- Панели `Collider` и `Geometry LODs` разведены по названиям и могут скрываться отдельно через `Menu Settings`.

### Исправлено
- Исправлены сценарии, где выбранный source object мог быть ошибочно переиспользован как target geometry object в experimental collider workflow.
- Исправлена сборка proxy из размещенных asset objects: теперь можно создавать unparented proxies, если target object не выбран, и корректно выбирать collection для результата.
- Исправлены потенциальные дубли/конфликты хоткеев после повторной регистрации аддона.
- Исправлены случаи, когда Asset Browser не переключался на нужную NH-библиотеку после сборки.

### Проверено
- `python -m py_compile NH_Blender.py NH_Blender\__init__.py` проходит без ошибок.
- Собран архив `dist/nh-blender-addon-v0.5.3.0.zip`.
- Обновлен `dist/nh-blender-addon-latest.zip`.

## [0.5.2.31] - 2026-05-29

### Изменено
- Drag-and-drop `.p3d` теперь сразу добавляет dropped-файлы в `Import/Export planner`, без промежуточного меню выбора действия.
- Список dropped `.p3d` сортируется натурально по папке и имени файла, чтобы `part2.p3d` шёл перед `part10.p3d`.
- Описание оператора `P3D Drop` уточнено под новый planner-only workflow.

## [0.5.2.29] - 2026-05-29

### Добавлено
- Добавлен workflow drag-and-drop для `.p3d`: dropped-файлы можно добавить в `Import/Export planner` или импортировать сразу через A3OB.
- В `Snap Points (Memory LOD)` добавлены кнопки `Visual 0 Only` и `Show All` для быстрого переключения видимости `.p3d`-веток.
- В `Model Split` добавлен merge workflow: список source-коллекций, `Add/Remove/Clear Merge Source` и `Merge Collections`.
- Добавлен оператор `Material Safe Merge` для merge-by-distance с сохранением материалов.
- Добавлен workflow `Plain Axis Pivot`: создание Plain Axis helper-а для сборки частей сцены и кнопка `Delete All Plain Axes` для удаления helper-ов после сборки.

### Изменено
- `Visual 0 Only` теперь оставляет видимыми `Point clouds > Memory`, чтобы snap points не пропадали во время работы.
- `Memory` для snap points ищется и создаётся строго внутри соответствующей `.p3d` root-коллекции.
- При создании `Memory` рядом с Plain Axis он привязывается через constraint выбранного target object, а не через случайный LOD.
- `Delete All Plain Axes` возвращает объекты из собранной сцены обратно в их обычные позиции и больше не bake-ит собранное положение.
- Перед удалением Plain Axis автоматически ремонтируются `Memory` constraints, чтобы свежесозданные snap points возвращались вместе со своим `Resolution 0`.
- Патч A3OB `.p3d` file handler стал безопаснее: class unregister/register выполняется аккуратнее, чтобы drop handler стабильно обновлялся.
- Управление видимостью коллекций стало надёжнее за счёт синхронизации `hide_viewport` и layer collection state.

### Исправлено
- Исправлен случай, когда snap points оставались в собранной сцене после удаления Plain Axis.
- Исправлен случай, когда `Memory` LOD возвращался по другой траектории из-за inverse matrix от неправильного LOD.
- Исправлено скрытие `Point clouds > Memory` в режиме `Visual 0 Only`.
- Исправлены случаи, где Blender мог держать старый A3OB drop handler.

### Проверено
- `python -m py_compile` проходит для:
  - `NH_Blender.py`;
  - `NH_Blender/__init__.py`.
- На `pripyat_sportCenter.blend` проверен workflow Plain Axis/Snap Points:
  - repaired `7` Memory LOD Plain Axis constraints;
  - removed `7` Plain Axis helper(s);
  - removed `63` `NH Plain Axis` constraints;
  - snap point delta относительно своего `Resolution 0`: `0.0`.
- Аддон задеплоен в Blender `5.1`.

## [0.5.2.10] - 2026-05-21

### Добавлено
- В `Geometry Collider (exp)` добавлен переключатель режима создания collider-геометрии над блоком `Create Collider`.
- Добавлены 4 режима ввода, расположенные в UI как 2 ряда по 2 кнопки:
  - `from selected` — прежнее поведение по текущему выделению;
  - `per shells` — выбранные вершины расширяются до connected mesh shells, затем создаётся отдельный collider на каждый shell;
  - `per obj comp` — выделенные группы вершин внутри объекта разбиваются на connected components, затем создаётся отдельный collider на каждую группу;
  - `per objects` — создаётся отдельный collider на каждый выбранный mesh object.

### Изменено
- `Box`, `Convex Hull`, `Sphere` и `Capsule` теперь используют общий scope pipeline и могут создавать сразу несколько collider parts за один запуск.
- В режимах `per shells`, `per obj comp` и `per objects` результат всё равно добавляется в выбранный target `Geometry`, сохраняя прежнюю LOD/collection бизнес-логику.
- В custom properties сгенерированной collider-геометрии теперь сохраняются `scope` и количество созданных `parts`.

### Проверено
- Blender smoke-test для новых режимов:
  - `per objects` на двух mesh objects создаёт 2 box-collider parts;
  - `per shells` на одном mesh с двумя disconnected shells создаёт 2 box-collider parts;
  - `per obj comp` на двух выделенных vertex groups создаёт 2 box-collider parts.
- `python -m py_compile` проходит для:
  - `NH_Blender.py`;
  - `NH_Blender/__init__.py`;
  - `NH_Blender/tools/xray_tex_converter/dds_python.py`.

## [0.5.2.9] - 2026-05-21

### Добавлено
- В `Geometry Collider (exp)` добавлен двухшаговый workflow для круглых коллизий:
  - `Create Cylinder` создаёт редактируемый cylinder guide;
  - `Boxes From Cylinder` генерирует box-collider из уже подогнанного guide;
  - `Create Pipe` создаёт редактируемый pipe guide;
  - `Boxes From Pipe` генерирует box-collider из уже подогнанного guide.
- Для guide-объектов добавлены служебные метки источника, чтобы результат создавался относительно исходного объекта, а не относительно временного guide.
- Добавлена автоматическая починка отображения mojibake-текста в material dropdown: повреждённые строки вида `РџСЂ...` показываются как нормальная кириллица, без переименования самих материалов.

### Изменено
- `Boxes From Cylinder` снова создаёт длинные прямые cuboid-боксы через весь диаметр цилиндра.
- Для 16-edge cylinder guide создаётся 8 прямых box-сегментов без перекошенных заломанных призм.
- `Boxes From Pipe` строит трапециевидные сегменты по реальным inner/outer ring вершинам guide, вместо прямоугольных боксов, вложенных в кольцо.
- При генерации из cylinder/pipe guide больше не создаётся отдельная коллекция `NH Collider Guides`.
- После `Boxes From Cylinder` и `Boxes From Pipe` временный guide удаляется, а активным объектом становится target `Geometry`.
- Результат генерации сохраняет прежнюю бизнес-логику LOD: создаётся или используется выбранный target, например `Geometries/Geometry`.
- Параметры shape-операторов в experimental collider tools больше не переносятся между новыми запусками меню; `Scale Multiplier`, scale, offset, segments, radii и depth стартуют с базовых значений. Между операторами сохраняется только `Target LOD`.
- При создании нового cylinder/pipe guide активный guide больше не становится source для будущего LOD target; source остаётся исходный mesh.

### Исправлено
- Исправлена проблема, когда сохранённый `Scale Multiplier` ломал реальные размеры следующих box-collider генераций.
- Исправлена проблема, когда генерация круглой коллизии могла не создавать прежнюю структуру `Geometries` collection и `Geometry` object.
- Исправлена геометрия cylinder boxes: убраны кривые, перекрученные и визуально сломанные сегменты.
- Исправлена геометрия pipe boxes: размеры берутся из подогнанного guide, а не из устаревших значений оператора.
- Исправлена логика сборки ZIP, когда build-script мог падать на уже выставленной версии.
- Чтение исходников в `build_addon_zip_v2.bat` переведено на UTF-8, чтобы не ломать кириллицу и metadata при сборке.
- Нормализованы line endings после шумного diff на десятки тысяч строк.
- `NH_Blender.py` и `NH_Blender/__init__.py` снова синхронизированы.

### Проверено
- `python -m py_compile` проходит для:
  - `NH_Blender.py`;
  - `NH_Blender/__init__.py`;
  - `NH_Blender/tools/xray_tex_converter/dds_python.py`.
- Проверена регистрация аддона в Steam Blender:
  - `D:\SteamLibrary\steamapps\common\Blender\blender.exe`;
  - Blender `5.1.2`.
- Проверена регистрация аддона напрямую из ZIP.
- Выполнен Blender smoke-test:
  - `Create Cylinder` -> `Boxes From Cylinder`;
  - `Create Pipe` -> `Boxes From Pipe`;
  - результат создаётся в `Geometries/Geometry`;
  - `NH Collider Guides` не создаётся;
  - cylinder guide с 16 edges даёт 8 box-сегментов;
  - pipe guide с 24 segments даёт 24 trapezoid-сегмента.
- Собран архив:
  - `dist/nh-blender-addon-v0.5.2.9.zip`;
  - `dist/nh-blender-addon-latest.zip`.

## [0.5.1.3] - 2026-05-18

### Добавлено
- `Export Missing Textures` переведён на modal/timer workflow, чтобы Blender мог обновлять UI во время экспорта.
- В `Texture Replace -> Export Missing Textures from Sources` добавлен видимый прогресс экспорта:
  - текущий номер операции;
  - общее количество операций;
  - процент выполнения;
  - текущая текстура;
  - текущее действие.
- Добавлена кнопка `Cancel Export` для безопасной остановки текущего экспорта текстур.
- Во время экспорта добавляется статус Blender workspace с текущим прогрессом `NH Texture Export`.
- В отчёты экспорта добавлен признак частичной отмены, если экспорт был остановлен пользователем.
- `Fix Mesh/Hierarchy` теперь после завершения автоматически выставляет итоговому mesh-объекту A3OB LOD Properties:
  - `Is P3D LOD = ON`;
  - `Type = Resolution`;
  - `Resolution / Index = 0`.
- После `Fix Mesh/Hierarchy` итоговый объект снова делается активным и выделенным, чтобы его LOD-свойства сразу были видны в `Object Data Properties`.

### Изменено
- `Export Missing Textures` больше не выполняет весь экспорт одним блокирующим `execute()`, а обрабатывает requests порциями через timer.
- Во время активного экспорта обычная кнопка `Export Missing Textures` скрывается или блокируется, чтобы не запускать второй экспорт поверх первого.
- `Texture Replace` сохраняет текущую логику Built-in Python DDS converter, RVMAT generation и TXT/JSON reports, но теперь даёт видимую обратную связь во время работы.
- TXT-отчёт экспорта стал короче:
  - `Skipped Existing` ограничен первыми 100 записями;
  - `Missing Sources` ограничен первыми 100 записями;
  - `Failed` ограничен первыми 100 записями;
  - полный список остаётся в JSON-отчёте.

### Исправлено
- Исправлен отчёт экспорта: `.paa` больше не должны попадать в секции `Created Diffuse`, `Created NOHQ` и `Created SMDI`.
- `Created Diffuse` теперь содержит только созданные diffuse `.png`.
- `Created NOHQ` теперь содержит только созданные `_nohq.png`.
- `Created SMDI` теперь содержит только созданные `_smdi.png`.
- `Created PAA` теперь содержит только созданные `.paa`.
- `Created RVMAT` теперь содержит только созданные `.rvmat`.
- Добавлена защитная фильтрация списков перед записью TXT/JSON-отчёта.
- Добавлен dedupe created-items по нормализованному output path.
- Runtime-поле `texture_export_cancel_requested` не сохраняется в persisted UI state.

### Проверено
- `python -m py_compile` проходит для:
  - `NH_Blender.py`;
  - `NH_Blender/__init__.py`;
  - `NH_Blender/tools/xray_tex_converter/dds_python.py`.
- ZIP собирается через `build_addon_zip_v2.bat`.
- Сохранён предыдущий фикс `Fix Mesh/Hierarchy` для A3OB LOD Properties.

## [0.5.1.2] - 2026-05-18

### Добавлено
- Package-версия аддона `NH_Blender/` стала основным форматом установки.
- В ZIP теперь включаются встроенные инструменты конвертации текстур:
  - `NH_Blender/tools/xray_tex_converter/dds_python.py`;
  - `NH_Blender/tools/xray_tex_converter/converter.js`.
- Добавлен встроенный Python DDS-конвертер без pip-зависимостей.
- Поддержаны DDS-форматы `DXT1`, `DXT3`, `DXT5`.
- Добавлена конвертация `DDS -> PNG` без Node.js.
- Добавлена генерация `_nohq` и `_smdi` из `_bump.dds`.
- Добавлена генерация `.rvmat` самим аддоном.
- Добавлены fallback `Stage1` / `Stage5` в RVMAT, если `_bump.dds` отсутствует или конвертация normal/specular не выполнена.
- Добавлен экспорт недостающих текстур из source-папки в target-папку с сохранением структуры подпапок.
- Добавлен итоговый отчёт экспорта:
  - `_nh_texture_export_last_report.txt`;
  - `_nh_texture_export_last_report.json`.
- В отчёте появились отдельные секции `Created Diffuse`, `Created NOHQ`, `Created SMDI`, `Created PAA`, `Created RVMAT`, `Skipped Existing`, `Missing Sources`, `Failed`.
- Добавлен краткий итог последнего экспорта прямо в UI.
- Добавлена кнопка `Open Last Export Report`.
- Добавлено принудительное сохранение и восстановление настроек `Texture Replace` через `nh_blender_ui_state.json`.
- Добавлена миграция старых UI-настроек, чтобы старые значения `AUTO`, `_co`, `Convert PNG to PAA OFF` не перебивали новые дефолты.
- Добавлены новые дефолты Texture Export:
  - `Diffuse Suffix = none`;
  - `Convert DDS to PNG = ON`;
  - `Convert PNG to PAA = ON`;
  - `DDS Backend = Built-in Python`;
  - `Generate RVMAT = ON`;
  - `Delete PNG after PAA = OFF`;
  - `Only Missing = ON`;
  - `Overwrite Existing = OFF`.
- Добавлен package-aware поиск встроенных converter tools.
- Добавлен build-скрипт `build_addon_zip_v2.bat`, который собирает package ZIP и проверяет содержимое архива.
- `Component fixes from .txt` переведён в свернутый dropdown, закрытый по умолчанию.

### Изменено
- `Texture Replace` UI упрощён.
- Удалены из UI технические кнопки диагностики экспорта, `Object Preview` и `DB Preview`.
- `Replace Texture from DB` перенесён ближе к `Build From Folder`, потому что это один workflow.
- DDS backend в обычном UI оставлен как `Built-in Python`.
- Node.js больше не требуется для штатной конвертации DDS.
- `Dry Run`, `Verbose Export Log`, `Write Export Log`, `Export Log File`, `Show Advanced Converter Tools` убраны из UI.
- Экспорт текстур теперь всегда выполняет реальный экспорт согласно настройкам `Only Missing` / `Overwrite Existing`.
- DB build/replace теперь игнорирует тестовые артефакты `*_test`, `*_node_test`, `*_package_test`, `*_backend_test`, `*_selftest`.
- RVMAT generation больше не требует заранее существующий `.rvmat`.
- Virtual missing paths больше не стирают старые поля A3OB пустым значением.

### Исправлено
- Исправлена ошибка регистрации experimental collider operators из-за `PointerProperty(type=bpy.types.Object)` внутри Operator.
- Исправлена ошибка `_tex_export_should_write() missing 1 required positional argument: 'settings'`.
- Исправлена проблема, когда Blender Image API не мог прочитать некоторые DDS, например `prop_14.dds`.
- Исправлена проблема упаковки, из-за которой папка `tools/` не попадала рядом с установленным аддоном.
- Исправлена логика записи expected missing paths: если текстуры нет, аддон пишет ожидаемый путь, а не стирает поле.
- Исправлена логика `.rvmat` basename: `_co`, `_ca`, `_nohq`, `_smdi` и похожие суффиксы удаляются перед подбором базового имени.
- Исправлена сборка ZIP: пути внутри архива записываются через `/`, а не через `\`.
- Исправлена ошибка `re.PatternError: bad character range ’-Р at position 12` в `_texture_category_folder_from_base`.

### Известные ограничения
- `PNG -> PAA` по-прежнему требует внешний `ImageToPAA.exe` или `Pal2PacE.exe`.
- `_nohq/_smdi` создаются только если найден исходный `_bump.dds`.
- Если `_bump.dds` отсутствует, RVMAT использует fallback `Stage1` / `Stage5`.

## [0.4.9.1] - 2026-05-01

### По сравнению с предыдущим changelog
- Предыдущий блок `Unreleased` от `2026-04-25` добавлял ранние export-стопы для duplicate `Resolution LOD` и `n-gon`, чтобы A3OB не молча пропускал LOD. В `0.4.9.1` batch-export получил следующий слой защиты: проверяет результат после записи, диагностирует причину пропуска LOD и не даёт `.bak` перезаписаться уже частично сломанной моделью.
- Предыдущий changelog описывал подготовку split/snap/fix workflows. В `0.4.9.1` основной фокус смещён на безопасный повторный экспорт, восстановление проблемных A3OB-моделей и удобство работы с `Geometry` / `Fire Geometry` / `Roadway`.

### Добавлено
- В batch-export добавлена проверка текущего `.p3d` перед созданием `.bak`: если существующий файл уже не содержит ожидаемые LOD-сигнатуры или имеет меньше LOD, чем текущий backup, новый `.bak` не создаётся и причина выводится в `System Console`.
- Добавлена подробная диагностика `Missing LOD diagnostics`: для пропавшего LOD выводятся preprocess-данные, proxy-check, параметры merged mesh и ошибки A3OB validation.
- Диагностика proxy-validation теперь показывает конкретный proxy-объект и причину отказа: не один треугольник, non-ASCII path/material/group или отсутствующие A3OB proxy/material properties.
- Добавлены предупреждения и инструмент поиска loose vertices вне `Point clouds > Memory`: `Fixes -> Export checks -> Loose vertices outside Memory`.
- В `Fixes` добавлена кнопка `Fix Proxy Triangles`, которая чинит A3OB proxy-меши с ровно 3 вершинами, но без одного корректного треугольного face; объект proxy не пересоздаётся, поэтому сохраняются parent, transform, A3OB proxy properties, custom properties, материалы и material index первого face.
- Batch-export теперь автоматически готовит vertex mass для `Geometry` LOD, если масса отсутствует или часть вершин не имеет веса.
- В `Geometry Collider` добавлены eyedropper-кнопки для быстрого назначения активного mesh как `Target LOD Object`, `Fire Geometry` или `Roadway`.
- В списки материалов `Fire Geometry` и `Roadway` добавлен пункт `Добавить новый`, создающий материал и назначающий его на выделенные faces или весь target mesh.
- Добавлены сворачиваемые секции `Geometries / Fire Geometry` и `Misc / Roadway`.

### Изменено
- Версия аддона поднята до `0.4.9.1`.
- `Force export all LODs (skip validation)` теперь реально обходит A3OB `Validator.validate_lod` и proxy-validation, а также экспортирует с `lod_collisions="IGNORE"`.
- При включённом `Force export all LODs` локальные pre-check предупреждения по duplicate `Resolution LOD` и `n-gon` больше не останавливают экспорт, а только пишутся в консоль.
- `Repair Invalid A3OB Selections` теперь собирает выбранный repair scope в одну `Resolution 0` модель, переносит её в `.p3d` root-коллекцию, чистит пустые коллекции/helper-объекты и затем чинит A3OB selection links.
- В `Fixes` блок export-проверок вынесен отдельно, а старый отдельный `Fix Shading` убран из панели.

### Исправлено
- Исправлена ситуация, когда `Force export all LODs` не помогал проблемному LOD, потому что patch применялся не к тому `Validator`, который реально использует A3OB exporter.
- Batch-export теперь создаёт временный `.bak.pending` до записи и финализирует `.bak` только после post-check: полный экспорт может обновить backup даже если восстановил недостающие LOD, а partial export не перезаписывает более полезный существующий `.bak`.
- Post-check missing LOD теперь показывает не только факт `Only exported X/Y`, но и конкретные объекты/сигнатуры вместе с диагностикой возможной причины.
- В списке файлов `Import/Export planner` снова работает выбор строки через ЛКМ; кнопка tooltip больше не перехватывает клик выбора.
- В `Import/Export planner` кнопки `Add` / `Remove` / `Clear` перенесены под quick-add строку, а `Add By Name` заменена компактной icon-only кнопкой с плюсом, чтобы поле имени модели было шире.

## [Unreleased] - 2026-04-25

### Добавлено
- В `Fixes` добавлен workflow для component-fix `.txt`: выбор файла, поиск записей по имени `.p3d`-модели и выделение проблемных vertex groups на активном `Geometry` / `View Geometry` / `Fire Geometry` LOD.
- В `Fixes` добавлен оператор `Delete Faces/Edges Keep Verts` для быстрого удаления выбранных полигонов и рёбер без удаления вершин.
- В `Fixes` добавлен раздельный поиск проблемной геометрии в `Edit Mode`: `Find Trash` для маленьких мусорных face-island и `Find Flat Plates` для плоских coplanar-пластин.
- В `Import/Export planner` добавлен блок `Quick Add From NH_Objects`: можно указать корень `NH_Objects`, ввести имя модели и быстро добавить соответствующий `.p3d` в import-список.
- В `Model Split` добавлен режим `Separate -> Named Standalone Model` для сборки новой самостоятельной модели из выбранных уже разделённых объектов с сохранением логических путей вроде `Visuals`, `Geometries`, `Misc`, `Point clouds`.

### Изменено
- Проверка мусорных островков стала строже: `Find Trash` теперь ищет connected face islands по порогам `verts < 5`, `edges < 8`, `faces < 5`.
- Batch-export теперь сканирует не только дочерние коллекции корня сцены, но и `.p3d` root-коллекции, найденные через внутренний pipeline.
- В named-standalone split workflow можно выбирать режим переноса `Move` / `Copy` и место будущего `Back to source`: рядом с исходником или в отдельную папку.
- `Snap Points (Memory LOD)` теперь выбирает `A Target` / `V Target` как обычные mesh-объекты моделей, а `Point clouds > Memory` для каждой стороны создаётся или находится автоматически внутри соответствующей `.p3d`-ветки.
- Кнопка `Create/Find Point clouds > Memory` в `Snap Points` сначала готовит memory-LOD для выбранных `A Target` / `V Target`; если targets не выбраны, сохраняется прежний fallback по всем `.p3d` root-коллекциям сцены.

### Исправлено
- Batch-export теперь заранее останавливает экспорт коллекции, если внутри одной логической ветки найдены дублирующиеся `Resolution LOD` индексы; подробности выводятся в `System Console`.
- Batch-export теперь заранее останавливает экспорт коллекции при обнаружении `n-gon`-полигонов в экспортируемых LOD-мешах, чтобы не упираться в поздний отказ A3OB-валидации.
- `Create Snap Points` больше не зависит от текущего viewport при выборе порядка `0/1`: пара сортируется по мировым координатам, используя `Snap Axis` как предпочтительную ось и фактическую ось разлёта как fallback.

## [0.4.0] - 2026-04-12

### Изменено
- Версия аддона поднята до `0.4.0`.
- `Snap Points (Memory LOD)` переведён на ручной workflow: оператор берёт ровно 2 выбранные вершины из активного `Edit Mode` и копирует их в два выбранных `Memory`-объекта как пары `A/V`.
- Кнопка создания `Point clouds > Memory` теперь сканирует все коллекции сцены, оканчивающиеся на `.p3d`, и создаёт недостающие `Point clouds > Memory` внутри каждой такой модели.
- Категория для memory-LOD переименована в `Point clouds` с сохранением совместимости со старыми сценами, где ещё используется имя `Memory`.
- Scatter травы/клаттера переведён на выделенные полигоны активного меша в `Edit Mode`; старые поля `Source Object`, `Vertex Group` и `Target Collection` убраны из панели.
- `Create Proxies` теперь пересобирает scatter заново: удаляет ранее созданные прокси текущего объекта и сразу создаёт новый набор по текущему выделению.

### Добавлено
- Автоочистка `P3D Name`: при вводе и вставке автоматически удаляются пробелы, подчёркивания, `.p3d` и прочие спецсимволы, остаются только буквы и цифры.
- Параметр `Slope Falloff` для уменьшения плотности травы на крутых полигонах.

### Исправлено
- `Snap Axis` в `Snap Points` больше влияет только на шаблон имени и не смещает точки по выбранной оси.
- Поиск `Memory` стал локальным для конкретной `.p3d`-ветки и больше не подхватывает чужой `Memory` из другой модели.
- Кнопки snap point workflow стали надёжнее для сценария с двумя выбранными `Memory`-объектами `A/V`.

## [0.3.1] - 2026-04-07

### Добавлено
- Панель `Import/Export planner` с batch-import/batch-export workflow для `Arma 3 Object Builder`.
- Пост-обработка импортированных A3OB-материалов: предпросмотр текстур сразу после импорта и совместимость с оригинальным `add-on-arma3objectbuilder-v2.5.1` без форка.
- Опциональный дисковый кеш `texture.png` рядом с исходным `texture.paa` для ускорения повторных импортов между сессиями Blender.
- Новая панель `Model Split` для создания отдельных part-моделей из уже разделённых объектов с именами вида `*_01.p3d`, `*_02.p3d`.
- Фильтр batch-export `Only split part collections (_01, _02, ...)` для экспорта только split-частей без перезаписи оригинального root.

### Изменено
- Версия аддона поднята до `0.3.1`.
- `Snap Points (Memory LOD)` переведён на auto-workflow от края модели; manual-вариант из панели убран.
- Значение `Snap Group` по умолчанию изменено с `StenaKamennaya` на `SampleName`.
- Новые split-коллекции получают собственный `Back to source` путь рядом с исходным `.p3d`, чтобы экспортироваться как отдельные модели.

### Удалено
- Лишние текстовые подсказки из меню `Import/Export planner`.

### Исправлено
- Более стабильная постановка span/snap points за счёт предсказуемой сортировки пары точек и улучшенного выбора крайних точек модели.
- Batch-import planner больше не конфликтует с форком A3OB из-за двойной загрузки material previews.

## [0.2.1] - 2026-04-03

### Исправлено
- Кнопки создания `Memory LOD` и collider LOD теперь гарантированно создают нужные коллекции и переносят объект в них.
- Коллекция для collider-LOD теперь создаётся как `Geometries`, при этом старая `Geometry` подхватывается как совместимая и может быть переименована автоматически.
- Для `span point` добавлена стабильная сортировка точек `0/1` по текущему вьюпорту, с fallback на мировые координаты вне `VIEW_3D`.

### Изменено
- Версия аддона поднята до `0.2.1`.

## [0.2.0] - 2026-03-30

### Изменено
- Целевая версия Blender обновлена до `5.1`.
- Версия аддона поднята до `0.2.0`.
- `Select Isolated Verts` переназначен с `Ctrl+Shift+A` на `Mouse5`.
- Collider convex hull теперь собирается из чистого набора точек и пишет обратно упрощенную геометрию без временного мусора.

## [0.1.9] - 2026-03-29

### Добавлено
- Переключатель `Target LOD` в панели `Geometry Collider` для `Geometry`, `View Geometry` и `Fire Geometry`.
- Выбор активного `Roadway Material` на объекте `Roadway`.
- Кнопка `Choose Roadway Material Path` для назначения `.rvmat` или `.paa` в A3OB material properties выбранного roadway-материала.

### Изменено
- `Selected Loose Geometry Verts -> Hull` теперь строит hull только по выделенным loose-вершинам в текущем target collider LOD и работает как отдельная UI-кнопка.
- Набор collider-хоткеев упрощен: сохранены `Ctrl+Shift+C`, `Ctrl+Shift+A` и `Mouse4`; действия, убранные из хоткеев, доступны через `Hotkeys -> Buttons`.
- `Weld Roadway` теперь работает только по текущему выделению в `Edit Mode`, а не по всему `Roadway`-мешу.
- Панель `Geometry Collider` синхронизирует target-объект, A3OB LOD props и визуальный стиль при смене `Target LOD`.
- `README.md` синхронизирован с версией `0.1.9` и актуальным workflow.

### Исправлено
- Повторное использование collider target-объектов стало надежнее для объектов из логической коллекции `Geometry`.
- Валидация source/target для collider и roadway-операторов стала строже и дает более понятные ошибки.
- Финализация convex hull и очистка временной геометрии стали безопаснее для edit-mode workflow.

## [0.1.8] - 2026-03-27

### Добавлено
- Панель `Geometry Collider` в `N-panel -> NH Plugin`.
- Workflow создания collider-LOD прямо в Blender с отдельной коллекцией `Geometry`.
- OB-style сценарий для геометрии:
- `Copy Selected Verts To Geometry`
- `Loose Geometry Verts -> Hull`
- Подсветка geometry/collider-объектов отдельным цветом для визуального отличия от `Resolution`.
- Хоткеи для collider workflow:
- `Ctrl+Shift+C` — копирование вершин в `Geometry`
- `Ctrl+Shift+A` — выбор изолированных вершин
- `Mouse4` — `Selection -> Hull`
- `Mouse5` — `Loose Geometry Verts -> Hull`
- `Alt+LMB` — выбор всего связанного mesh island под курсором
- Блок `Misc / Roadway` в панели `Geometry Collider`.
- Создание/поиск коллекции `Misc` и `Roadway` LOD.
- Оператор `Copy Selected Faces To Roadway`.
- Настройка `Roadway Weld Distance` и оператор `Weld Roadway` для сшивания почти совпадающих вершин в walkway/nav геометрии.

### Изменено
- Панель `Geometry Collider` упрощена под работу через хоткеи.
- Основные действия вынесены в раскрывающийся блок `Hotkeys -> Buttons`.
- Редкие build-инструменты вынесены в отдельный раскрывающийся блок `Extra Build`.
- `Create/Find Collider LOD` теперь создает или использует отдельный geometry-объект, а не заставляет работать в `Resolution`.
- Collider-объекты складываются в коллекцию `Geometry`, а roadway-объекты — в `Misc`.
- `README.md` обновлен под актуальный функционал версии `0.1.8`.

### Удалено
- Лишние текстовые подсказки из панели `Geometry Collider`; оставлены hover/tooltips и компактный UI.
- Поле `Roadway Texture` и кнопка `Apply Roadway Texture Path` из блока `Misc / Roadway`.

### Исправлено
- Защита от неправильного выбора target LOD-объекта.
- Более безопасный путь сборки convex hull для loose geometry workflow.
- Защита от дублирующихся `bmesh`-элементов при удалении временной геометрии после hull-операций.

## [0.1.7] - 2026-03-21

### Изменено
- Панель `Snap Points (Memory LOD)` снова отображается в UI (`N-panel -> NH Plugin`).
- В панели `Snap Points` оставлен только manual workflow: `Manual: 2 selected vertices`.
- Блоки `Auto: edge extremes from model` и `Batch P3D: import -> snap -> export` убраны из UI.

## [0.1.6] - 2026-03-19

### Добавлено
- Новая настройка `Fix Mesh`: `Fix Mesh Join Batch` для управления размером батча при объединении.
- Новая настройка `Fix Mesh`: `Center Fixed Mesh To (0,0,0)`.
- Автоцентрирование результата после merge (центр bounds переносится в мировой ноль, если включено).

### Изменено
- `Fix Mesh/Hierarchy` теперь в первую очередь берёт selected/active объект, а не случайный меш сцены.
- Объединение мешей выполняется поэтапно, чтобы снизить риск зависания на больших ветках.
- В тяжёлые циклы очистки добавлены `redraw/yield` для лучшей отзывчивости UI.
- Значение `Fix Mesh Join Batch` по умолчанию теперь `1`.
- `Fix Mesh Join Batch = 1` явно означает попытку объединить всё за один проход (legacy-поведение).
- Значения `>= 2` сохраняют поэтапный режим объединения.
- В отчёте fix-оператора теперь выводится реальное значение `join_batch` (включая `1`).

### Исправлено
- Очистка в `Fix Mesh/Hierarchy` теперь ограничена активной сценой и не трогает посторонние сцены.
- Коллекция результата fix теперь сценозависимая: `NH_Fix_Result_<SceneName>`.
- Очистка helper-объектов не удаляет объекты, разделяемые с коллекциями вне дерева активной сцены.

## [0.1.4]

### Изменено
- `Force export all LODs (skip validation)` по умолчанию переключён в OFF.

## [0.1.3]

### Добавлено
- Панель `Fixes`.
- Оператор `Fix Shading`.
- Дополнительная диагностика LOD при batch-экспорте.

### Изменено
- Панель Snap Points временно скрыта в UI на время доработки пайплайна/воркфлоу.

## [0.1.2]

### Добавлено
- Инструменты P3D Asset Library.
- Workflow конвертации выделенных размещённых ассетов в A3OB proxy.
