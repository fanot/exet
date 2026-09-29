# REST API и форматы данных

Интерактивная спецификация OpenAPI/Swagger доступна по адресу `http://localhost:8000/docs`, ReDoc — `/redoc`. У каждого поля есть описание с единицами измерения.

Единицы: координаты — градусы WGS-84; высоты — м (`_amsl` над уровнем моря, `_agl` над землей); скорости — м/с; длительности — мин (`_min`) или с (`_s`); расстояния — км; время — ISO 8601 с часовым поясом (`*_iso`) или Unix-время в секундах (`t_*`).

## Методы

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/health` | Проверка работоспособности |
| GET | `/api/catalog` | ТТХ БВС, сенсоры, типы съемки, нормативы |
| GET | `/api/scenarios`, `/api/scenarios/{id}` | Демо-сценарии (GeoJSON) |
| POST | `/api/plan` | Запуск расчета: `{scenario: GeoJSON, params: MissionParams}` → `{job_id}` |
| POST | `/api/plan/upload` | То же для файлов (multipart: один или несколько `files` .geojson/.kml — слои сценария, `params` — JSON-строка) |
| POST | `/api/scenario/parse` | KML/GeoJSON (один или несколько слоев) → нормализованный GeoJSON; `partial=true` допускает неполный набор |
| GET | `/api/jobs/{job_id}` | Статус: `queued/running/done/error/cancelled`, `progress`, этап `stage`, `elapsed_s`, сходимость поиска `trace`, `plan_id` |
| POST | `/api/jobs/{job_id}/cancel` | Отменить расчет |
| POST | `/api/advise` | **Подбор парка к сроку**: `params.fleet` — доступный парк (максимум по типам), `params.deadline_h` — срок. Результат в `advice` статуса задачи: кривая «время работ от числа бортов», минимальный парк в срок (при нехватке — сколько бортов потребуется сверх доступных), пояснение |
| GET | `/api/plans` | История расчетов (сравнение вариантов) |
| GET/DELETE | `/api/plans/{id}` | План целиком / удаление |
| GET | `/api/plans/{id}/scenario` | Исходный сценарий плана (GeoJSON) — план из истории открывается со своей картой |
| GET | `/api/plans/{id}/export/{fmt}` | `kml`, `geojson`, `csv`, `zip` (всё вместе, плюс QGroundControl `.plan` по вылетам и файлы по бортам), `qgc` (один вылет, `?sortie=N`). Параметр `?drone=GEM-1` — индивидуальное задание одного борта |
| GET | `/api/plans/{id}/brief` | Полетное задание для печати (HTML, A4): маршрут, этапы, ключевые точки с координатами, профиль высот. `?drone=` — один борт |
| POST | `/api/validate` | Независимая проверка произвольного плана на сценарии |

**Ход расчета.** Пока задача выполняется, `GET /api/jobs/{job_id}` возвращает:
- `stage`: `prepare` → `slicing` → `allocation` → `bayes` → `dispatch` → `validate`;
- `evaluations`: число оцененных вариантов;
- `trace`: сходимость поиска, `[№ оценки, время работ лучшего варианта (ч), его налет (ч), 1 — проба TPE / 0 — сетка]`.

Интерфейс рисует по `trace` график сходимости.

Пример загрузки слоями (область отдельным файлом, препятствия организаторов — другим):

```bash
curl -s -X POST localhost:8000/api/plan/upload -F "files=@область.geojson" -F "files=@высотные препятствия Приморский край.kml" -F 'params={"fleet":[{"model":"geoscan_gemini","count":4}],"start_time":"2026-09-25T09:00:00+10:00"}'
```

Пример:

```bash
curl -s -X POST localhost:8000/api/plan -H 'Content-Type: application/json' -d @- <<'JSON'
{"scenario": <GeoJSON>, "params": {
  "survey_type": "rgb", "gsd_cm": 3, "objective": "time",
  "fleet": [{"model": "geoscan_201", "count": 2}, {"model": "geoscan_gemini", "count": 4}],
  "start_time": "2026-09-25T09:00:00+03:00", "wind_speed_ms": 5, "wind_dir_deg": 250}}
JSON
# → {"job_id": "…"}; далее GET /api/jobs/{job_id} до status=done, затем GET /api/plans/{plan_id}
```

## Входной сценарий

GeoJSON FeatureCollection в WGS-84. Тип объекта задается полем `properties.kind`:

| kind | Геометрия | Свойства |
|---|---|---|
| `survey_area` | Polygon / MultiPolygon (с отверстиями) | `name` |
| `no_fly` | Polygon / MultiPolygon | `name`; `category` (`P`/`R`/`TEMP`); `alt_min_m`, `alt_max_m`, `alt_ref` (`AGL`/`AMSL`) — 3D-зона; `active_from`, `active_to` (ISO 8601) — временная зона. Текстовые высоты организаторов (`Altitudes`: «От земли до 900 м AMSL», «От FL150 до FL980») и `Type` (`запретная_зона`, `пост_ограничение`, `врем_ограничение`) разбираются автоматически — см. [ZONE_FORMAT.md](ZONE_FORMAT.md) |
| `obstacle` | Point / LineString / Polygon | `height_m` — высота, м; `alt_ref` — `AGL` (высота объекта над землей, по умолчанию) или `AMSL` (абсолютная отметка верха); `radius_m` — для точек и линий (по умолчанию 10) |
| `airspace` | Polygon | разрешенное пространство; `ceiling_m` — потолок AGL |
| `site` | Point | основная площадка взлета и посадки. **Необязательна**: если площадок нет (как в задании организаторов «Границы полетов.kml»), сервис расставит их автоматически — круги досягаемости покрывают область, положение уточняется k-медианами вне запретных зон |
| `reserve_site` | Point | резервная площадка (только для аварийной посадки) |

Необязательный внешний член `planning_hint.start_time` задает дату и время по умолчанию для интерфейса.

KML: принимаются Placemark с Polygon (outer/inner), LineString, Point. Тип объекта определяется по порядку:
1. `ExtendedData kind`;
2. имя файла («область съемки», «запретные зоны», «высотные препятствия», «площадки»);
3. коды типов препятствий eTOD/ICAO в имени объекта (`BUILDING`, `OTHER:COMMUNICATION_TOWER`, `POWER_TRANSMISSION_PYLON`, `CHIMNEY`, `TREE`…);
4. название папки или объекта;
5. большинство объектов документа — безымянный объект с высотой в Z в файле препятствий тоже считается препятствием.

**Файлы второго пакета организаторов.**
- «Границы полетов.kml» — задание на съемку: безымянные полигоны с отверстиями без признаков слоя принимаются как области.
- «Московская зона.kml» — зоны: тип и высоты из атрибутов `Type`/`Altitudes`; файл на несколько областей обрезается по району работ.
- «obstacles_Московская область.kml» — 3D-примитивы: выдавленные полигоны, высота над землей — в Z.

Координаты с пробелами после запятых (`0.0, 0.0, 0.0`) и пустые или вырожденные объекты не отвергают файл: они пропускаются с пометкой в `notes`.

**Высотные препятствия в формате организаторов (eTOD):**
- высота берется из `ExtendedData height_m` или из Z-координат (максимум);
- привязка — из `altitudeMode`: `absolute` означает абсолютную отметку верха над уровнем моря, и высота над землей считается по ЦМР (консервативно, по минимуму рельефа под объектом); `relativeToGround` означает высоту над землей;
- файл может покрывать целый регион: препятствия автоматически обрезаются до района работ (+~25 км на перелеты).

Пример организаторов — 3201 препятствие по Приморскому краю, из них 2777 с абсолютными отметками — разбирается целиком.

**Слои отдельными файлами.** `POST /api/plan/upload` и `POST /api/scenario/parse` принимают несколько файлов `files` (область, запретные зоны, препятствия, площадки) и склеивают их в один сценарий. В интерфейсе можно выбрать несколько файлов или перетащить их на карту. Файл без области съемки добавляется слоем к текущему сценарию.

## Параметры миссии (MissionParams)

| Поле | Тип / единицы | По умолчанию |
|---|---|---|
| `survey_type` | `rgb`, `multispectral`, `ir`, `lidar`, `geophysics` | `rgb` |
| `gsd_cm` | см/пикс | по типу съемки (RGB 3, МС 8, ИК 15) |
| `front_overlap`, `side_overlap` | доля 0..1 | 0.70 / 0.50 |
| `fleet[]` | `{model, count, batteries}` | — |
| `objective` | `time` или `flight` | `time` |
| `deadline_h` | ч | нет |
| `takeoff_weight_h` | ч налета за один взлет (износ) | 0 |
| `start_time` | ISO 8601 | завтра, 09:00 МСК |
| `wind_speed_ms`, `wind_dir_deg` | м/с на 10 м; откуда дует, ° | 4; 270 |
| `cloud_base_m` | м AGL | нет |
| `respect_daylight`, `min_sun_elevation_deg` | bool, ° | true, 10 |
| `use_terrain` | bool | true |
| `reserve_pct` | % | 20 |
| `extra_sites[]` | `{lon, lat, name}` | [] |
| `search_effort` | `fast`, `normal`, `thorough` | `normal` |
| `temp_restrictions` | временные ограничения без времени действия: `active` — считать действующими весь период (консервативно), `ignore` — не учитывать | `active` |
| `wind_model` | `qa` — методика QA-сессии (ветер — порог по ТТХ и выбор курса; путевая скорость постоянна, на расход АКБ не влияет) или `physical` (треугольник скоростей, консервативно) | `qa` |
| `airspace_mode` | `flight_plan` (до 700 м AGL) или `no_flight_plan` (ФП ИВП: до 150 м, прямая видимость, без самолета) | `flight_plan` |
| `vlos_radius_km` | км — удаление коптера от площадки в режиме `no_flight_plan` | 2 |
| `optimizer` | `grid` (сетка) или `bayes` (сетка + байесовская оптимизация TPE) | `bayes` |
| `energy_model` | `linear` (расход ∝ времени, QA) или `calibrated` (по открытой телеметрии, см. ENERGY_MODEL.md) | `linear` |

## Выходные форматы

### План (JSON, `/api/plans/{id}`)

- `status`: `ok` — всё снимается; `infeasible` — есть недостижимые участки, план для остального прикладывается справочно вместе с `recommendations`.
- `summary`: `makespan_h`, `flight_hours`, `sorties`, `drones_used`, `work_days`, `coverage_pct`, `coverage_allowed_pct`, `photos`, `headings_deg`, `shares`, `transit_layers_agl_m` и другие.
- `models`: геометрия съемки по типам — высота, GSD, шаг, базис, скорость, радиус действия.
- `drones`: борт, эшелон, вылеты, налет, `color` — цвет борта (одинаковый в интерфейсе и файлах экспорта).
- `sorties[]`: `drone_id`, `site`, `takeoff_iso`, `landing_iso`, `duration_min`, `distance_km`, `battery_used_pct`, `survey_alt_agl_m`, `transit_alt_agl_m`, `phases[]`, `keypoints[]`, **`waypoints[]` = `[lon, lat, alt_amsl, alt_agl, t_unix, phase]`**.
- `validation`: результаты crash-теста. У проверок есть `points` — точки на карте `{lon, lat, t, label, sortie}`: координаты нарушений, а для разведения бортов еще и точка наибольшего сближения (`pair` — номера вылетов).
- `recommendations`, `warnings`, `search_log`.

### GeoJSON

Каждый объект содержит `properties.feature_type`:

| feature_type | Содержимое |
|---|---|
| `route` | LineString 3D `[lon, lat, alt_amsl]` — полный маршрут вылета. Атрибуты: борт, модель, площадки, время взлета и посадки, скорости, высоты; **`coord_times`** — ISO-время каждой вершины; `alt_agl_m` — массив высот над землей |
| `phase` | этап полета (`takeoff`, `transit_out`, `descent`, `survey`, `climb`, `transit_back`, `landing`) с `t_start`/`t_end` |
| `keypoint` | взлет, начало и конец съемки, заход на посадку, посадка — с `eta` |
| `site`, `reserve_site`, `uncovered` | площадки и непокрытые участки |

Во внешнем члене `mission` (допускается RFC 7946 §6.1) лежат сводка и расписание.

### KML

Структура: папка «Площадки» и по папке на борт, внутри — папка на каждый вылет. В каждом вылете:
- `LineString` с `altitudeMode=absolute` и `TimeSpan`;
- **`gx:Track`** с `<when>` на каждую точку — воспроизведение во времени в Google Earth;
- точки этапов с `TimeStamp`;
- `ExtendedData` со всеми атрибутами вылета.

Временные метки хранятся в `gx:Track`/`TimeSpan`; если приемник их не поддерживает, те же данные есть в `ExtendedData` и в CSV-расписании.

### QGroundControl `.plan` (в ZIP)

По одному файлу на вылет. Команды: `NAV_TAKEOFF` (22), `NAV_WAYPOINT` (16, высоты AMSL, `AltitudeMode=2`), `DO_SET_CAM_TRIGG_DIST` (206; базис на участке съемки, 0 — выключение), `NAV_LAND` (21).
