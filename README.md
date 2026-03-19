# NH Blender Plugin

Аддон Blender для пайплайна DayZ/Arma с интеграцией Arma 3 Object Builder (A3OB).

## Возможности

- Scatter clutter-прокси из DayZ-конфига (`CfgWorlds -> CAWorld -> Clutter` + `CfgSurfaceCharacters`)
- Сборка базы текстур из папки (`.paa` / `.rvmat`)
- Замена путей материалов из базы через A3OB-совместимые свойства
- Пакетный import/export `.p3d` коллекций
- Временная P3D Asset Library и конвертация расставленных ассетов в A3OB proxy
- Панель быстрых фиксов геометрии/иерархии

## Панели UI

- `Clutter Proxies (DayZ)`
- `P3D Asset Library`
- `Fixes`
- `Import/Export planner`
- `Texture Replace`

Расположение: `3D Viewport -> N Panel -> NH Plugin`

## Требования

- Blender `4.4+`
- Включенный аддон: **Arma 3 Object Builder (A3OB)**

## Установка

1. Скачайте репозиторий.
2. В Blender откройте `Edit -> Preferences -> Add-ons -> Install...`.
3. Выберите `NH_Blender.py`.
4. Включите аддон.

## Последний релиз: 0.1.7

### Что изменилось в 0.1.7

- `Fix Mesh/Hierarchy` стал безопаснее для больших сцен:
- Поиск цели идет от selected/active объекта, а не случайно.
- Join может выполняться поэтапно батчами.
- Добавлены уступки UI (`redraw/yield`) в тяжелых местах.
- Добавлено центрирование результата в `(0,0,0)` по bbox.
- `Fix Mesh Join Batch`:
- `1` = попытка объединить все за один проход (legacy-поведение).
- `>=2` = объединение поэтапно батчами.
- Очистка helper-объектов и фиксовая коллекция ограничены активной сценой.
- Результат fix складывается в коллекцию вида `NH_Fix_Result_<SceneName>`.

Полная история изменений: [CHANGELOG.md](CHANGELOG.md)

## Ссылки

- Репозиторий: <https://github.com/BigbyOn/nh-blender-addon>
- Issues: <https://github.com/BigbyOn/nh-blender-addon/issues>

## Лицензия

MIT License. См. [LICENSE](LICENSE).
