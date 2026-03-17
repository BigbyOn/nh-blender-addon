# NH Blender Plugin

Аддон Blender для пайплайнов DayZ/Arma с интеграцией A3OB.

## Основные возможности

- Scatter clutter-прокси из DayZ-конфига (`CfgWorlds -> CAWorld -> Clutter` + `CfgSurfaceCharacters`)
- Сборка базы текстур из папки (`.paa` / `.rvmat`)
- Замена путей материалов из базы через A3OB-совместимые свойства
- Пакетный импорт/экспорт `.p3d`-коллекций (с сохранением source-path и опциональным `.bak`)
- Построение временной P3D Asset Library и конвертация расставленных ассетов в A3OB-прокси
- Панель быстрых фиксов:
- `Fix Shading` (merge выбранных mesh, clear split normals, recalc normals, shade smooth)
- `Fix Mesh/Hierarchy` (дублирующая кнопка быстрого доступа)

## Панели в UI

- `Clutter Proxies (DayZ)`
- `P3D Asset Library`
- `Fixes`
- `Import/Export planner`
- `Texture Replace`

## Примечания по экспорту

- `Force export all LODs (skip validation)` предусмотрен для проблемных файлов.
- По умолчанию параметр **OFF**.
- При частичном экспорте batch-экспорт делает post-check и пишет в System Console, какие LOD не попали в файл.

## Статус Snap Points

- Инструменты Snap Points пока оставлены в коде, но панель скрыта в UI.
- Это временно, пока дорабатывается пайплайн и логика работы.

## Требования

- Blender `4.4+`
- Включенный аддон: **Arma 3 Object Builder (A3OB)**
- Для пакетных `.p3d`-операций нужны доступные A3OB import/export операторы

## Установка

1. Скачайте репозиторий.
2. В Blender откройте `Edit -> Preferences -> Add-ons -> Install...`.
3. Выберите `NH_Blender.py`.
4. Включите аддон.

Расположение панели:
- `3D Viewport -> N panel -> NH Plugin`

## Последние изменения (0.1.2 -> 0.1.4)

- `0.1.2`: добавлены инструменты P3D Asset Library и workflow конвертации selected-объектов в proxy.
- `0.1.3`: добавлена панель `Fixes`, кнопка `Fix Shading`, скрыта панель Snap Points, добавлена диагностика LOD при batch-экспорте.
- `0.1.4`: `Force export all LODs` по умолчанию переключен в OFF.

## Ссылки проекта

- Репозиторий: <https://github.com/BigbyOn/nh-blender-addon>
- Issues: <https://github.com/BigbyOn/nh-blender-addon/issues>

## Структура репозитория

- `NH_Blender.py` - основной файл аддона
- `README.md` - описание проекта и настройка
- `LICENSE` - условия лицензии
- `.gitignore` - игнорируемые локальные/сборочные файлы

## Лицензия

MIT License. См. [LICENSE](LICENSE).
