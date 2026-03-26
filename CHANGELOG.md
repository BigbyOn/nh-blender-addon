# Журнал изменений

Все заметные изменения проекта фиксируются в этом файле.

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
