// Презентация Geoscan Fleet Planner (ЛЦТ, задача 5)
const pptxgen = require("pptxgenjs");
const fs = require("fs");
const D = JSON.parse(fs.readFileSync(process.argv[2], "utf8")); // числа
const IMG = process.argv[3]; // каталог с картинками
const OUT = process.argv[4];

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
pres.title = "Geoscan Fleet Planner";

const C = { navy: "0E1C2B", ink: "1B2733", muted: "5F6B7A", teal: "1F8A7E", tealL: "D8EFEC", orange: "F08A24", light: "F3F6F8", white: "FFFFFF", red: "C0392B" };
const HF = "Cambria", BF = "Calibri";

function title(s, t, sub) {
  s.addText(t, { x: 0.6, y: 0.35, w: 12.1, h: 0.75, fontFace: HF, fontSize: 32, bold: true, color: C.ink, margin: 0, isTextBox: true });
  if (sub) s.addText(sub, { x: 0.6, y: 1.08, w: 12.1, h: 0.4, fontFace: BF, fontSize: 15, color: C.muted, margin: 0, isTextBox: true });
}
function num(s, n) { s.addText(String(n), { x: 12.3, y: 7.0, w: 0.6, h: 0.3, fontSize: 10, color: C.muted, align: "right", fontFace: BF, isTextBox: true }); }
function card(s, x, y, w, h, fill) { s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: fill || C.light }, line: { color: fill || C.light }, rectRadius: 0.08 }); }
function badge(s, x, y, txt, fill) {
  s.addShape(pres.shapes.OVAL, { x, y, w: 0.5, h: 0.5, fill: { color: fill || C.teal }, line: { color: fill || C.teal } });
  s.addText(txt, { x, y, w: 0.5, h: 0.5, align: "center", valign: "middle", fontSize: 14, bold: true, color: C.white, fontFace: BF, margin: 0, isTextBox: true });
}
function dots(s, items, x, y, w, rowH, color, size, dotColor) {
  items.forEach((t, i) => {
    const yy = y + i * rowH;
    s.addShape(pres.shapes.OVAL, { x, y: yy + 0.11, w: 0.12, h: 0.12, fill: { color: dotColor || C.teal }, line: { color: dotColor || C.teal } });
    s.addText(t, { x: x + 0.28, y: yy, w: w - 0.28, h: rowH, fontFace: BF, fontSize: size || 14, color: color || C.ink, margin: 0, isTextBox: true, valign: "top" });
  });
}
function stat(s, x, y, w, big, label, color, labelColor) {
  s.addText(big, { x, y, w, h: 0.8, fontFace: HF, fontSize: 40, bold: true, color: color || C.teal, margin: 0, isTextBox: true });
  s.addText(label, { x, y: y + 0.8, w, h: 0.55, fontFace: BF, fontSize: 13, color: labelColor || C.muted, margin: 0, isTextBox: true, valign: "top" });
}

// 1. Титул
{
  const s = pres.addSlide(); s.background = { color: C.navy };
  s.addImage({ path: `${IMG}/map_t.png`, x: 7.2, y: 0.9, w: 5.8, h: 4.35, transparency: 25 });
  s.addText("Geoscan Fleet Planner", { x: 0.7, y: 2.2, w: 6.5, h: 1.0, fontFace: HF, fontSize: 44, bold: true, color: C.white, margin: 0, isTextBox: true });
  s.addText("Сервис планирования и распределения авиационных работ группы БВС", { x: 0.7, y: 3.25, w: 6.3, h: 0.9, fontFace: BF, fontSize: 22, color: "CFE3EC", margin: 0, isTextBox: true });
  s.addText("ЛЦТ · Задача 5 · ГК «Геоскан»", { x: 0.7, y: 4.5, w: 7, h: 0.4, fontFace: BF, fontSize: 16, color: C.orange, bold: true, margin: 0, isTextBox: true });
  s.addText("Геоскан 201 · Gemini · 801  |  KML / GeoJSON / QGroundControl  |  crash-тест каждого плана", { x: 0.7, y: 6.6, w: 12, h: 0.4, fontFace: BF, fontSize: 13, color: "9FB3C2", margin: 0, isTextBox: true });
  s.addNotes("Представляем сервис, который по области съемки, запретным зонам и парку БВС Геоскан строит индивидуальные полетные задания, расписание и гарантирует отсутствие опасных сближений.");
}

// 2. Задача
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Что проверяет заказчик", "Из ТЗ и QA-сессии: главное — отсутствие нарушений, затем оптимальность и продуманность");
  const items = [
    ["1", "Crash-тест", "Нет опасных сближений бортов, заходов в запретные зоны и пролетов у высотных препятствий"],
    ["2", "Два критерия", "Минимум общего времени работ (с подготовкой, заменой АКБ, возвратом) и минимум суммарного налета"],
    ["3", "Реальная физика", "GSD → высота, перекрытия 70/50%, резерв АКБ 20%, ветер как порог и как выбор курса галсов"],
    ["4", "Масштаб", "~100 км², до 10 бортов, расчет до 30 мин; MultiPolygon с отверстиями, 3D+время зоны"],
    ["5", "Результат", "Полетные задания KML/GeoJSON: маршрут, высоты, скорости, этапы, расписание, модель БВС"],
    ["6", "Невыполнимость", "Понятная ошибка и рекомендация: площадка ближе, другой тип БВС"],
  ];
  items.forEach((it, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 0.6 + col * 6.2, y = 1.8 + row * 1.7;
    card(s, x, y, 5.9, 1.45);
    badge(s, x + 0.25, y + 0.3, it[0], i === 0 ? C.orange : C.teal);
    s.addText(it[1], { x: x + 0.95, y: y + 0.2, w: 4.8, h: 0.4, fontFace: BF, fontSize: 18, bold: true, color: C.ink, margin: 0, isTextBox: true });
    s.addText(it[2], { x: x + 0.95, y: y + 0.62, w: 4.8, h: 0.75, fontFace: BF, fontSize: 13, color: C.muted, margin: 0, isTextBox: true, valign: "top" });
  });
  num(s, 2);
}

// 3. Конвейер
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Как работает планировщик", "Декомпозиция NP-трудной задачи: точные подзадачи + поиск по параметрам верхнего уровня");
  const steps = [
    ["Вход", "GeoJSON / KML, WGS-84. Зоны 2D/3D/4D, препятствия, площадки, парк, ветер, дата"],
    ["Геометрия съемки", "H = GSD·f/p, полоса, шаг, базис; скорость по ТТХ, затвору и ветру"],
    ["Галсы", "Курс θ с учетом ветра, ячейки, сканлайн с отверстиями, полосы boustrophedon"],
    ["Вылеты", "Упаковка по энергии (80%) с учетом обхода зон — оценка = точная траектория ±3%; 4D-траектории с рельефом"],
    ["Расписание", "АКБ и зарядка, переезды, светлое время, окна временных зон"],
    ["Разведение и проверка", "Эшелоны, 4D-проверка 1 с, задержка старта; независимый crash-тест"],
  ];
  const w = 1.95, gap = 0.12;
  steps.forEach((st, i) => {
    const x = 0.6 + i * (w + gap);
    card(s, x, 1.9, w, 3.2, i === 5 ? "FDEBD8" : C.tealL);
    badge(s, x + 0.15, 2.05, String(i + 1), i === 5 ? C.orange : C.teal);
    s.addText(st[0], { x: x + 0.15, y: 2.7, w: w - 0.3, h: 0.7, fontFace: BF, fontSize: 16, bold: true, color: C.ink, margin: 0, isTextBox: true, valign: "top" });
    s.addText(st[1], { x: x + 0.15, y: 3.4, w: w - 0.3, h: 1.6, fontFace: BF, fontSize: 12, color: C.ink, margin: 0, isTextBox: true, valign: "top" });
  });
  card(s, 0.6, 5.45, 12.1, 1.35, C.light);
  s.addText([
    { text: "Поиск: ", options: { bold: true, color: C.teal } },
    { text: "доли площади между типами БВС (сетка на симплексе + локальное уточнение) × ширина колонок нарезки. Каждый вариант оценивается целиком: галсы → вылеты → расписание → критерий (~50–100 мс). Затем байесовская оптимизация (TPE): непрерывные доли, ширина нарезки и размер ячеек — лучше сетки в 10 из 28 задач (до −48%), хуже ни в одной. Лучшие варианты обоих методов проходят полное построение с разведением.", options: { color: C.ink } },
  ], { x: 0.85, y: 5.55, w: 11.6, h: 1.15, fontFace: BF, fontSize: 14, margin: 0, isTextBox: true, valign: "middle" });
  num(s, 3);
}

// 4. Физика съемки и ветер
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Съемка и ветер — по ТТХ, а не «на глаз»", "RGB, GSD 3 см, перекрытия 70/50%, ветер 5 м/с у земли (≈7–8 м/с на рабочей высоте)");
  const rows = [["Тип / камера", "H, м", "Шаг, м", "Базис, м", "V съемки, м/с"]].concat(D.models);
  s.addTable(rows.map((r, i) => r.map((c) => ({ text: String(c), options: { bold: i === 0, color: i === 0 ? C.white : C.ink, fill: { color: i === 0 ? C.teal : (i % 2 ? C.white : C.light) } } }))),
    { x: 0.6, y: 1.8, w: 6.9, colW: [2.9, 0.9, 0.95, 1.05, 1.1], fontFace: BF, fontSize: 13, border: { type: "solid", color: "DDE3E8", pt: 0.5 }, rowH: 0.45 });
  const pts = [
    ["Порог выполнимости", "ветер пересчитывается на высоту (профиль Хеллмана) и сравнивается с ТТХ; борт или эшелон, где предел превышен, исключается с объяснением"],
    ["Курс галсов", "самолет: угол сноса α → полоса W·cosα − L·sinα, воздушная скорость 64–130 км/ч; коптер — минимум разворотов"],
    ["Методика QA по умолчанию", "автопилот держит путевую скорость: ветер не меняет скорость и расход АКБ. Физическая модель (треугольник скоростей) — переключателем"],
  ];
  pts.forEach((p, i) => {
    const y = 1.8 + i * 1.6;
    card(s, 7.9, y, 4.8, 1.4);
    s.addText(p[0], { x: 8.1, y: y + 0.12, w: 4.4, h: 0.35, fontFace: BF, fontSize: 15, bold: true, color: C.teal, margin: 0, isTextBox: true });
    s.addText(p[1], { x: 8.1, y: y + 0.5, w: 4.45, h: 0.85, fontFace: BF, fontSize: 12, color: C.ink, margin: 0, isTextBox: true, valign: "top" });
  });
  card(s, 0.6, 4.0, 6.9, 2.6, C.tealL);
  s.addText("Формулы кадровой съемки", { x: 0.85, y: 4.15, w: 6.4, h: 0.4, fontFace: BF, fontSize: 15, bold: true, color: C.teal, margin: 0, isTextBox: true });
  dots(s, ["H = GSD · f / p   (p — размер пикселя)", "полоса W = w·H/f;  шаг = W·(1 − поперечное)", "базис b = L·(1 − продольное);  v ≤ b / t_затвора,  v ≤ 0.5·GSD / t_выдержки", "высота над землей постоянна: огибание рельефа по ЦМР, шаг 25 м"], 0.85, 4.65, 6.4, 0.46, C.ink, 13);
  num(s, 4);
}

// 5. Демо — карта
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Демо: 100 км², 8 бортов, 3D/4D-зоны", "Самолеты берут дальнюю середину, коптеры — ячейки у своих площадок и кромки у запретных зон");
  s.addImage({ path: `${IMG}/map.png`, x: 0.6, y: 1.65, w: 7.2, h: 5.4 });
  const legend = [
    ["Постоянные зоны", "населенный пункт, охраняемый объект — облет с буфером маневра"],
    ["Временная зона 12:00–16:00", "вылеты, задевающие ее, поставлены вне окна действия"],
    ["Трасса выше 450 м", "съемка под ней разрешена, эшелоны перелета ≤ 420 м"],
    ["Препятствия", "вышка 120 м и труба 180 м — вырезаны; ЛЭП 35 м — пролет над"],
  ];
  legend.forEach((l, i) => {
    const y = 1.75 + i * 1.3;
    s.addText(l[0], { x: 8.2, y, w: 4.6, h: 0.38, fontFace: BF, fontSize: 15, bold: true, color: C.ink, margin: 0, isTextBox: true });
    s.addText(l[1], { x: 8.2, y: y + 0.4, w: 4.6, h: 0.75, fontFace: BF, fontSize: 13, color: C.muted, margin: 0, isTextBox: true, valign: "top" });
  });
  num(s, 5);
}

// 6. Два критерия
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Два критерия — разные планы", "Тот же сценарий, тот же парк: оптимизатор меняет доли самолетов/коптеров и размер вылетов");
  const T = D.time, F = D.flight;
  s.addChart(pres.charts.BAR, [
    { name: "Минимум времени", labels: ["Время работ, ч", "Налет, ч", "Вылетов"], values: [T.makespan_h, T.flight_hours, T.sorties] },
    { name: "Минимум налета", labels: ["Время работ, ч", "Налет, ч", "Вылетов"], values: [F.makespan_h, F.flight_hours, F.sorties] },
  ], { x: 0.6, y: 1.7, w: 7.4, h: 5.2, barDir: "col", barGrouping: "clustered", chartColors: [C.teal, C.orange],
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 12, dataLabelColor: C.ink, showLegend: true, legendPos: "b", legendFontSize: 12,
    catAxisLabelColor: C.muted, valAxisLabelColor: C.muted, valGridLine: { color: "E5E9EE", size: 0.5 }, catGridLine: { style: "none" }, catAxisLabelFontSize: 12 });
  stat(s, 8.5, 1.9, 4.2, `−${D.flight_saving}%`, "налета при критерии «минимум налета»", C.orange);
  stat(s, 8.5, 3.5, 4.2, `+${D.time_penalty}%`, "ко времени работ — цена экономии ресурса");
  s.addText("Время работ считается от развертывания расчета до посадки последнего борта и выгрузки данных, с учетом зарядки АКБ и светлого времени (Солнце ≥ 10°).", { x: 8.5, y: 5.2, w: 4.2, h: 1.4, fontFace: BF, fontSize: 13, color: C.muted, margin: 0, isTextBox: true, valign: "top" });
  num(s, 6);
}

// 7. Безопасность
{
  const s = pres.addSlide(); s.background = { color: C.navy };
  s.addText("Безопасность: гарантия + независимая проверка", { x: 0.6, y: 0.35, w: 12, h: 0.75, fontFace: HF, fontSize: 32, bold: true, color: C.white, margin: 0, isTextBox: true });
  const layers = [
    ["Эшелоны", "каждому борту свой эшелон перелета выше всех съемочных высот (шаг 35 м > разведение 30 м); коптерам — нижние"],
    ["Пространство", "съемочные участки разных вылетов не пересекаются; буферы маневра самолета 200 м"],
    ["Время (4D)", "траектории с шагом 1 с; конфликт → задержка старта на земле — всегда разрешимо"],
  ];
  layers.forEach((l, i) => {
    const y = 1.5 + i * 1.25;
    badge(s, 0.6, y + 0.1, String(i + 1), C.orange);
    s.addText(l[0], { x: 1.3, y, w: 5.4, h: 0.4, fontFace: BF, fontSize: 17, bold: true, color: C.white, margin: 0, isTextBox: true });
    s.addText(l[1], { x: 1.3, y: y + 0.42, w: 5.4, h: 0.75, fontFace: BF, fontSize: 13, color: "C7D6E2", margin: 0, isTextBox: true, valign: "top" });
  });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 7.1, y: 1.45, w: 5.6, h: 3.9, fill: { color: "16293D" }, line: { color: "16293D" }, rectRadius: 0.08 });
  s.addText("Crash-тест каждого плана", { x: 7.35, y: 1.6, w: 5.2, h: 0.45, fontFace: BF, fontSize: 17, bold: true, color: C.orange, margin: 0, isTextBox: true });
  const checks = ["Опасные сближения бортов", "Запретные зоны: высоты + окна действия", "Высотные препятствия", "Коридор ≤ 700 м AGL, разрешенная область", "Энергия 80% + достижимость площадки из любой точки", "Ветер на рабочих высотах ≤ ТТХ", "Светлое время"];
  dots(s, checks, 7.35, 2.15, 5.2, 0.34, C.white, 14, C.orange);
  s.addText("Работает только по выходным точкам (WGS-84 + время), своя проекция, своя выборка ЦМР — не доверяет планировщику", { x: 7.35, y: 4.6, w: 5.2, h: 0.7, fontFace: BF, fontSize: 12, italic: true, color: "9FB3C2", margin: 0, isTextBox: true });
  stat(s, 0.6, 5.55, 3.8, `${D.stress_ok}/${D.stress_n}`, "случайных сценариев без единого нарушения (стресс-тест)", C.orange, "C7D6E2");
  s.addText(`MultiPolygon с отверстиями, 1–4 зоны (постоянные, временные, по высотам), препятствия 40–250 м, 1–3 площадки, 1–10 бортов, ветер 0–7 м/с, широты 45–62°`, { x: 4.6, y: 5.65, w: 8.1, h: 1.0, fontFace: BF, fontSize: 13, color: "C7D6E2", margin: 0, isTextBox: true, valign: "top" });
  num(s, 7);
}

// 7b. ML там, где он полезен
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "ML — там, где он дает измеримый выигрыш", "Не в контуре безопасности: каждое решение все равно проходит независимый crash-тест");
  const colw = 5.9;
  [[0.6, "Байесовская оптимизация плана", "TPE (Optuna): доли площади между типами БВС, ширина нарезки, размер ячеек; теплый старт от сетки",
    [["10 / 28", "задач лучше сетки, хуже — 0"], ["−3.7%", "в среднем по критерию"], ["−48%", "максимальный выигрыш"]]],
   [6.8, "Модель расхода АКБ по телеметрии", "Градиентный бустинг на открытых данных (CC BY 4.0): 209 полетов коптера (CMU) и 102 круга самолета (IDF_DS); перенос на Геоскан через безразмерную кривую мощности",
    [["7.1 → 2.0%", "ошибка энергии полета коптера"], ["10.2 → 5.5%", "самолет: надбавка за набор и крен"], ["0%", "полетов с недооценкой > 10%"]]]].forEach(([x, h, sub, stats]) => {
    card(s, x, 1.75, colw, 4.55, C.light);
    s.addText(h, { x: x + 0.3, y: 1.95, w: colw - 0.6, h: 0.45, fontFace: BF, fontSize: 19, bold: true, color: C.teal, margin: 0, isTextBox: true });
    s.addText(sub, { x: x + 0.3, y: 2.45, w: colw - 0.6, h: 1.05, fontFace: BF, fontSize: 13, color: C.ink, margin: 0, isTextBox: true, valign: "top" });
    stats.forEach(([big, lbl], i) => {
      s.addText(big, { x: x + 0.3, y: 3.6 + i * 0.88, w: 2.3, h: 0.6, fontFace: HF, fontSize: 26, bold: true, color: i === 0 ? C.orange : C.teal, margin: 0, isTextBox: true, valign: "middle" });
      s.addText(lbl, { x: x + 2.65, y: 3.6 + i * 0.88, w: colw - 2.95, h: 0.6, fontFace: BF, fontSize: 13, color: C.muted, margin: 0, isTextBox: true, valign: "middle" });
    });
  });
  s.addText("Проверка честная: по фактическому результату после полного построения (оптимизация) и на отложенных полетах, которых модель не видела (энергия). Где данные не подтвердили эффект — например, зависимость мощности самолета от скорости — он не используется.", { x: 0.6, y: 6.45, w: 12.1, h: 0.7, fontFace: BF, fontSize: 12.5, italic: true, color: C.muted, margin: 0, isTextBox: true });
  num(s, 8);
}

// 8. Масштабирование
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Масштабирование и скорость расчета", "Режим «нормально» (сетка + байесовская оптимизация); ориентир заказчика — до 30 минут");
  const rows = [["Сценарий", "Парк", "Расчет, с", "Время работ, ч", "Налет, ч", "Crash-тест"]].concat(D.bench);
  s.addTable(rows.map((r, i) => r.map((c) => ({ text: String(c), options: { bold: i === 0, color: i === 0 ? C.white : C.ink, fill: { color: i === 0 ? C.teal : (i % 2 ? C.white : C.light) } } }))),
    { x: 0.6, y: 1.75, w: 8.1, colW: [1.9, 2.6, 0.9, 1.1, 0.8, 0.8], fontFace: BF, fontSize: 12, border: { type: "solid", color: "DDE3E8", pt: 0.5 }, rowH: 0.42 });
  stat(s, 9.2, 1.8, 3.5, `${D.demo_compute} с`, "расчет демо 100 км² / 8 бортов с 4D-разведением и проверкой");
  stat(s, 9.2, 3.5, 3.5, "−16%", "налета после упаковки с учетом обхода зон (8 и 10 бортов, та же модель ветра, что раньше)", C.orange);
  s.addText(`Задание организаторов 245 км², 570 участков: ${D.moscow.compute_min} мин расчета. Трубопровод 35 км: ${D.pipeline}.`, { x: 9.2, y: 5.2, w: 3.5, h: 1.3, fontFace: BF, fontSize: 13, color: C.muted, margin: 0, isTextBox: true, valign: "top" });
  num(s, 9);
}

// 9. Интерфейс и выход
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Интерфейс, расписание и экспорт", "Веб-интерфейс + REST API (Swagger с единицами у каждого поля) + CLI; Docker Compose");
  s.addImage({ path: `${IMG}/ui.png`, x: 0.6, y: 1.65, w: 7.9, h: 4.94 });
  const cols = [
    ["Ход расчета", "этапы, график сходимости оптимизатора, отмена"],
    ["Инспектор вылета", "этапы, профиль высот над рельефом; курсор по профилю двигает борт на карте"],
    ["Crash-тест на карте", "точка наибольшего сближения и нарушения — «показать на карте» с переходом ко времени"],
    ["Задания и экспорт", "KML/GeoJSON/CSV/.plan, по бортам, задание для печати; сравнение критериев, история"],
  ];
  cols.forEach((c, i) => {
    const y = 1.65 + i * 1.26;
    card(s, 8.8, y, 3.9, 1.14);
    s.addText(c[0], { x: 9.0, y: y + 0.1, w: 3.5, h: 0.36, fontFace: BF, fontSize: 14, bold: true, color: C.teal, margin: 0, isTextBox: true });
    s.addText(c[1], { x: 9.0, y: y + 0.46, w: 3.55, h: 0.64, fontFace: BF, fontSize: 11.5, color: C.ink, margin: 0, isTextBox: true, valign: "top" });
  });
  num(s, 10);
}

// 10. Невыполнимость и допущения
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Когда план невыполним — сервис объясняет почему", "Пример: только коптеры Gemini, площадки на краях участка");
  card(s, 0.6, 1.7, 6.1, 2.4, "FDEBD8");
  s.addText(D.infeasible_example, { x: 0.85, y: 1.85, w: 5.65, h: 2.1, fontFace: BF, fontSize: 13, color: C.ink, margin: 0, isTextBox: true, valign: "top" });
  const other = ["GSD ниже минимальной безопасной высоты 201 → «увеличьте GSD до ≥ … см»", "ветер на рабочей высоте > ТТХ → тип исключен с расчетом ветра на высоте", "нет полезной нагрузки (ИК только у 801) → подсказка по составу парка"];
  s.addText("Другие причины и подсказки", { x: 0.6, y: 4.35, w: 6.1, h: 0.4, fontFace: BF, fontSize: 16, bold: true, color: C.teal, margin: 0, isTextBox: true });
  dots(s, other, 0.6, 4.85, 6.1, 0.62, C.ink, 14);
  s.addText("Допущения (в catalog.yaml, меняются без кода)", { x: 7.2, y: 1.7, w: 5.5, h: 0.4, fontFace: BF, fontSize: 16, bold: true, color: C.teal, margin: 0, isTextBox: true });
  const lim = ["Энергия: линейная (QA) или калиброванная по открытой телеметрии; резерв 20%", "Ветер — один замер на миссию, профиль по высоте", "Самолет: упрощенные развороты, спирали, парашют", "Изогнутые коридоры режутся на прямые участки (глубина ≤ 5)", "Временная зона — вылет целиком вне ее окна (консервативно)", "Лидар и магнитометр — типовые (в линейке задачи нет)"];
  card(s, 7.2, 2.2, 5.5, 4.4, C.light);
  dots(s, lim, 7.45, 2.45, 5.1, 0.66, C.ink, 14);
  num(s, 11);
}

// 11. Воздушное пространство: 700 м и 150 м
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Воздушное пространство: с планом полета и без", "Экспертный вопрос: ограничение 150 м (ФП ИВП, Пост. №138) — учтено отдельным режимом");
  const A = D.airspace;
  const rows = [["Режим", "Площадок", "Время работ", "Покрытие", "Crash-тест"]].concat(A.rows);
  s.addTable(rows.map((r, i) => r.map((c, j) => ({ text: String(c), options: { bold: i === 0 || (j === 0 && i > 0), color: i === 0 ? C.white : C.ink, fill: { color: i === 0 ? C.teal : (i % 2 ? C.white : C.light) } } }))),
    { x: 0.6, y: 1.75, w: 7.6, colW: [3.0, 1.05, 1.25, 1.05, 1.25], fontFace: BF, fontSize: 13, border: { type: "solid", color: "DDE3E8", pt: 0.5 }, rowH: 0.5 });
  s.addText("Демо «Токсово, 100 км²», 8 бортов, ветер 5 м/с", { x: 0.6, y: 3.55, w: 7.6, h: 0.3, fontFace: BF, fontSize: 11.5, italic: true, color: C.muted, margin: 0, isTextBox: true });
  s.addText("Режим «без плана полета»", { x: 0.6, y: 4.35, w: 7.6, h: 0.4, fontFace: BF, fontSize: 16, bold: true, color: C.teal, margin: 0, isTextBox: true });
  dots(s, ["потолок 150 м над землей: съемка ≤ 120 м, перелеты 145 м с огибанием рельефа и препятствий",
           "прямая видимость: коптер не дальше R от площадки (параметр, 2 км); самолет исключен",
           "площадки для покрытия кругами видимости — «добавить и пересчитать» в один клик",
           "crash-тест контролирует потолок режима и удаление от площадки"], 0.6, 4.85, 7.6, 0.52, C.ink, 13.5);
  card(s, 8.7, 1.75, 4.0, 4.85, C.light);
  s.addText(A.ratio, { x: 8.95, y: 1.95, w: 3.5, h: 0.9, fontFace: HF, fontSize: 44, bold: true, color: C.orange, margin: 0, isTextBox: true });
  s.addText("дольше работы без плана полета и +" + A.extra_sites + " площадок", { x: 8.95, y: 2.85, w: 3.5, h: 0.7, fontFace: BF, fontSize: 14, color: C.ink, margin: 0, isTextBox: true, valign: "top" });
  s.addText("Вывод для заказчика: на площадной съемке десятков км² оправдан план полета; режим 150 м — для небольших участков рядом с площадкой.", { x: 8.95, y: 3.75, w: 3.5, h: 1.5, fontFace: BF, fontSize: 13.5, color: C.ink, margin: 0, isTextBox: true, valign: "top" });
  s.addText("Аэродромы, населенные пункты и массовые мероприятия задаются слоем запретных зон.", { x: 8.95, y: 5.35, w: 3.5, h: 1.1, fontFace: BF, fontSize: 12, italic: true, color: C.muted, margin: 0, isTextBox: true, valign: "top" });
  num(s, 12);
}

// 12. Реальные данные организаторов
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Задание организаторов: Московская область", "«Границы полетов» + «Московская зона» + 3D-препятствия — загружаются как есть, без правки файлов");
  s.addImage({ path: `${IMG}/map_moscow.png`, x: 0.6, y: 1.65, w: 5.6, h: 4.2 });
  const M = D.moscow;
  const pts = [
    [`${M.polys} участков, ${M.area_km2} км², без площадок`, `площадки расставлены автоматически (${M.sites}): круги досягаемости покрывают задание, положение — k-медианы вне зон`],
    [`${M.zones_total} зон → ${M.zones_near} у района`, "высоты разобраны из текста («От земли до 900 м AMSL», «от FL150»); зоны выше коридора не мешают съемке"],
    ["Предложенный формат зон", "category, alt_min/max_m + alt_ref, active_from/to (ISO 8601) — ZONE_FORMAT.md; временные без времени: «действуют» или «не учитывать»"],
    [`${M.obst_total} препятствий → ${M.obst_near} у района`, "3D-примитивы с высотой в Z; пустые объекты «0.0, 0.0, 0.0» пропускаются, а не ломают загрузку"],
  ];
  pts.forEach((pt, i) => {
    const y = 1.65 + i * 1.0;
    s.addText(pt[0], { x: 6.5, y, w: 6.3, h: 0.34, fontFace: BF, fontSize: 14, bold: true, color: C.ink, margin: 0, isTextBox: true });
    s.addText(pt[1], { x: 6.5, y: y + 0.34, w: 6.3, h: 0.62, fontFace: BF, fontSize: 11.5, color: C.muted, margin: 0, isTextBox: true, valign: "top" });
  });
  const st = [[M.checks, "crash-тест"], [`${M.coverage}%`, "покрытие разрешенной площади"], [`${M.compute_min} мин`, "расчет, режим «быстро»"], [`${M.days} дн.`, `работ парком из ${M.fleet_n} бортов`]];
  st.forEach((x, i) => {
    s.addText(x[0], { x: 0.6 + i * 3.1, y: 6.0, w: 3.0, h: 0.55, fontFace: HF, fontSize: 26, bold: true, color: i === 0 ? C.orange : C.teal, margin: 0, isTextBox: true });
    s.addText(x[1], { x: 0.6 + i * 3.1, y: 6.52, w: 3.0, h: 0.4, fontFace: BF, fontSize: 12, color: C.muted, margin: 0, isTextBox: true });
  });
  num(s, 13);
}

// 13. Подбор парка к сроку и ресурс ТО
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Сколько бортов нужно к сроку?", "Ответ организаторов: пользователь задает ограничение — срок или число бортов — и получает подсказку");
  const A = D.advice;
  s.addChart(pres.charts.LINE, [
    { name: "Время работ, ч", labels: A.points.map((x) => String(x[0])), values: A.points.map((x) => x[1]) },
    { name: `Срок ${A.deadline} ч`, labels: A.points.map((x) => String(x[0])), values: A.points.map(() => A.deadline) },
  ], { x: 0.6, y: 1.7, w: 6.9, h: 4.6, chartColors: [C.teal, C.red], lineSize: 2.5, lineDataSymbol: "circle", lineDataSymbolSize: 7,
    showLegend: true, legendPos: "b", legendFontSize: 12, catAxisTitle: "бортов в распоряжении", showCatAxisTitle: true, catAxisTitleFontSize: 11,
    valAxisLabelColor: C.muted, catAxisLabelColor: C.muted, valGridLine: { color: "E5E9EE", size: 0.5 }, catGridLine: { style: "none" } });
  card(s, 7.9, 1.7, 4.8, 2.5, C.tealL);
  s.addText("Подбор парка (маржинальный анализ)", { x: 8.1, y: 1.82, w: 4.4, h: 0.36, fontFace: BF, fontSize: 15, bold: true, color: C.teal, margin: 0, isTextBox: true });
  s.addText(A.text, { x: 8.1, y: 2.22, w: 4.45, h: 0.75, fontFace: BF, fontSize: 12, color: C.ink, margin: 0, isTextBox: true, valign: "top" });
  const pts = A.points, first = pts[0], best = pts.reduce((a, b) => (b[1] < a[1] ? b : a));
  const inTime = pts.find((x) => x[1] <= A.deadline);
  const plateau = pts.filter((x) => Math.abs(x[1] - first[1]) < 0.5 || (x[1] > A.deadline && x[0] <= (inTime ? inTime[0] : 99)));
  dots(s, [
    `${pts[0][0]}–${inTime ? inTime[0] - 1 : pts[pts.length - 1][0]} бортов: ≥ ${Math.round(Math.min(...plateau.map((x) => x[1])))} ч — работа не укладывается в световой день`,
    inTime ? `${inTime[0]} бортов — минимум к сроку ${A.deadline} ч (≈ ${inTime[1].toFixed(1).replace(".", ",")} ч)` : `срок ${A.deadline} ч недостижим`,
    `лучшее — ${best[0]} бортов: ≈ ${best[1].toFixed(1).replace(".", ",")} ч; дальше прибавки нет`,
  ], 8.1, 3.0, 4.45, 0.38, C.ink, 11.5);
  card(s, 7.9, 4.4, 4.8, 1.9);
  s.addText("Ресурс до ТО (ответы организаторов)", { x: 8.1, y: 4.52, w: 4.4, h: 0.36, fontFace: BF, fontSize: 15, bold: true, color: C.teal, margin: 0, isTextBox: true });
  dots(s, D.maintenance, 8.1, 4.95, 4.45, 0.42, C.ink, 12);
  s.addText("Если доступного парка мало, подбор продолжается сверх него и называет, сколько бортов потребуется. Кривая монотонна: лишние борта можно не поднимать.", { x: 0.6, y: 6.4, w: 12.1, h: 0.5, fontFace: BF, fontSize: 12, italic: true, color: C.muted, margin: 0, isTextBox: true });
  num(s, 14);
}

// 14. Итог
{
  const s = pres.addSlide(); s.background = { color: C.navy };
  s.addText("Итог", { x: 0.6, y: 0.5, w: 12, h: 0.8, fontFace: HF, fontSize: 36, bold: true, color: C.white, margin: 0, isTextBox: true });
  const st = [[`${D.time.makespan_h} ч`, "100 км² восемью бортами (1 рабочий день)"], [`${D.cov}%`, "покрытие разрешенной площади"], ["0", `нарушений в crash-тесте, ${D.stress_ok}/${D.stress_n} случайных сценариев`], [`${D.demo_compute} с`, "время расчета демо"]];
  st.forEach((x, i) => {
    s.addText(x[0], { x: 0.6 + i * 3.1, y: 1.7, w: 3.0, h: 0.9, fontFace: HF, fontSize: 40, bold: true, color: C.orange, margin: 0, isTextBox: true });
    s.addText(x[1], { x: 0.6 + i * 3.1, y: 2.6, w: 2.8, h: 0.8, fontFace: BF, fontSize: 15, color: "C7D6E2", margin: 0, isTextBox: true, valign: "top" });
  });
  const pts = ["Готовый сервис: docker compose up → http://localhost:8000 (UI) и /docs (API); файлы организаторов загружаются как есть",
    "Подбор парка к сроку, ресурс ТО, режимы с планом полета (700 м) и без (ФП ИВП, 150 м), печатные полетные задания по бортам",
    "Дальше: разные площадки взлета/посадки в одном вылете, прогноз ветра по времени, интеграция с Geoscan Planner и СППИ"];
  dots(s, pts, 0.6, 4.0, 12.1, 0.85, C.white, 16, C.orange);
  num(s, 15);
}

pres.writeFile({ fileName: OUT }).then(() => console.log("written", OUT));
