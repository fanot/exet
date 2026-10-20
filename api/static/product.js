/* Mission overview and terminology help. */
"use strict";
(() => {
  let report = null;
  const emptyOverview = $("#overview").innerHTML;
  const glossary = {
    GSD: "Размер участка земли, который соответствует одному пикселю снимка. Например, GSD 3 см/пикс: один пиксель охватывает примерно 3 × 3 см поверхности.",
    AGL: "Высота над поверхностью земли под бортом. При учёте рельефа абсолютная высота меняется, чтобы сохранять высоту съёмки над землёй.",
    AMSL: "Абсолютная высота относительно среднего уровня моря. Её нельзя напрямую сравнивать с высотой AGL без отметки рельефа.",
    БВС: "Беспилотное воздушное судно. В парке сервиса доступны самолёт Геоскан 201 и коптеры Gemini и 801.",
    АКБ: "Аккумуляторная батарея. Резерв — доля заряда, которая должна оставаться на посадке; в этом планировщике минимум 20%.",
    ЦМР: "Цифровая модель рельефа — высоты поверхности земли. Используется для расчёта высот маршрута над землёй.",
    ТО: "Техническое обслуживание. Планировщик оценивает расход межсервисного ресурса по налёту или числу полётов.",
    RGB: "Обычная цветная фотосъёмка: красный, зелёный и синий каналы.",
    МУЛЬТИСПЕКТР: "Мультиспектральная съёмка: поверхность снимается в нескольких спектральных диапазонах, в том числе за пределами видимого света. Помогает оценивать состояние растительности и различать материалы по их отражению.",
    ГЕОФИЗИКА: "Измерение физических полей над местностью с помощью датчиков на БВС, например магнитного поля магнитометром. Позволяет находить аномалии и изучать геологическое строение территории.",
    ПЕРЕКРЫТИЕ: "Общая часть соседних снимков, на которой виден один и тот же участок земли. Нужна для совмещения кадров и построения карты или 3D-модели. Продольное перекрытие — вдоль полёта, поперечное — между соседними проходами.",
    ПРОДОЛЬНОЕ: "Перекрытие последовательных снимков вдоль одного прохода БВС. При 70% соседние кадры имеют примерно 70% общей длины по направлению полёта. Большее перекрытие требует снимать чаще и увеличивает число кадров.",
    ПОПЕРЕЧНОЕ: "Перекрытие снимков между соседними параллельными проходами БВС. При 50% соседние полосы съёмки перекрываются примерно на половину ширины. Большее перекрытие уменьшает шаг между проходами и обычно увеличивает налёт.",
    ИК: "Инфракрасная съёмка. Использует тепловизионную полезную нагрузку.",
    QA: "Здесь — методика из ответов организаторов: ветер проверяется по пределам БВС и учитывается при выборе курса галсов. Путевая скорость постоянна, расход АКБ от ветра не меняется.",
    QGC: "QGroundControl — наземная станция управления. Файл .plan содержит полётное задание для одного вылета.",
    ЛИДАР: "Лазерное сканирование: расстояния до поверхности измеряются лазером, чтобы получить облако точек.",
    ЭШЕЛОН: "Назначенная высота перелёта борта. Планировщик использует разные высоты и задержки старта для разведения маршрутов. В обзоре эшелоны указаны в метрах AGL.",
    ГАЛС: "Прямой рабочий проход над участком съёмки. Соседние галсы расположены с шагом, который обеспечивает заданное перекрытие.",
    НАЛЁТ: "Сумма времени в воздухе всех бортов. Если два борта летят одновременно по часу, пройдёт один час, а суммарный налёт составит два часа."
  };
  const termPattern = /(?<![a-zа-яё0-9])(GSD|AGL|AMSL|БВС|АКБ|ЦМР|ТО|RGB|ИК|QA|QGC|мультиспектр|геофизика|перекрытие снимков|лидар|эшелон[а-яё]*|галс[а-яё]*|нал[её]т[а-яё]*)(?![a-zа-яё0-9])/gi;
  function termKey(text) {
    const key = text.toUpperCase();
    return key === "ПЕРЕКРЫТИЕ СНИМКОВ" ? "ПЕРЕКРЫТИЕ" : key.startsWith("ЭШЕЛОН") ? "ЭШЕЛОН" : key.startsWith("ГАЛС") ? "ГАЛС" : /^НАЛ[ЕЁ]Т/.test(key) ? "НАЛЁТ" : key;
  }
  function decorateTerms(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.parentElement.closest("button,a,abbr,script,style,textarea,option,[data-term],.ico")) nodes.push(node);
    }
    for (const node of nodes) {
      const matches = [...node.textContent.matchAll(termPattern)];
      if (!matches.length) continue;
      const fragment = document.createDocumentFragment();
      let pos = 0;
      for (const match of matches) {
        fragment.append(node.textContent.slice(pos, match.index));
        const span = document.createElement("span");
        span.className = "term"; span.tabIndex = 0; span.dataset.term = termKey(match[0]);
        span.textContent = match[0];
        fragment.append(span); pos = match.index + match[0].length;
      }
      fragment.append(node.textContent.slice(pos)); node.replaceWith(fragment);
    }
  }
  let activeTerm = null;
  function hideTerm() {
    $("#term-tooltip").hidden = true;
    activeTerm?.removeAttribute("aria-describedby"); activeTerm = null;
  }
  function showTerm(el) {
    if (!glossary[el.dataset.term]) return;
    hideTerm(); activeTerm = el;
    const tooltip = $("#term-tooltip");
    tooltip.textContent = glossary[el.dataset.term]; tooltip.hidden = false;
    el.setAttribute("aria-describedby", "term-tooltip");
    const rect = el.getBoundingClientRect(), tip = tooltip.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - tip.width - 8))}px`;
    tooltip.style.top = `${Math.max(8, rect.bottom + tip.height + 14 < innerHeight ? rect.bottom + 8 : rect.top - tip.height - 8)}px`;
  }
  document.addEventListener("mouseover", e => { const el = e.target.closest("[data-term]"); if (el) showTerm(el); });
  document.addEventListener("mouseout", e => { if (e.target.closest("[data-term]")) hideTerm(); });
  document.addEventListener("focusin", e => { const el = e.target.closest("[data-term]"); if (el) showTerm(el); });
  document.addEventListener("focusout", hideTerm);
  document.addEventListener("scroll", () => {
    if (activeTerm && document.activeElement === activeTerm) showTerm(activeTerm);
    else hideTerm();
  }, true);
  document.addEventListener("keydown", e => { if (e.key === "Escape") hideTerm(); });
  let glossTimer;
  const observer = new MutationObserver(() => { clearTimeout(glossTimer); glossTimer = setTimeout(refreshTerms, 80); });
  function refreshTerms() {
    observer.disconnect();
    [$("#panel"), $("#inspector")].forEach(decorateTerms);
    [$("#panel"), $("#inspector")].forEach(el => observer.observe(el, {childList: true, subtree: true}));
  }

  const list = (items, fallback) => items.length ? `<ul class="report-list">${items.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="hint">${esc(fallback)}</p>`;
  function renderOverview(r) {
    $("#overview").innerHTML = `<div class="overview-head"><div class="eyebrow">ОБЗОР МИССИИ</div><h2>${esc(r.scenario)}</h2><span class="hint">${esc(r.objective)} · план ${esc(r.plan_id)}</span></div>
      <div class="report-status ${r.ready ? "ok" : "bad"}">${ico(r.ready ? "ok" : "alert")}<div><b>${esc(r.title)}</b><div>Проверки: ${r.checks_passed}/${r.checks.length}</div></div></div>
      <p class="report-notice">${esc(r.notice)}</p>
      ${r.issues.length ? `<section class="report-section report-attention"><h3>${ico("alert")}Требует внимания <span class="badge bad">${r.issues.length}</span></h3>${list(r.issues, "")}</section>` : ""}
      <section class="overview-summary"><h3>Итоги миссии</h3><div class="overview-metrics">${r.metrics.map(([k, v]) => `<div><strong>${esc(v)}</strong><span>${esc(k)}</span></div>`).join("")}</div></section>
      ${compareCard(S.plan)}
      <section class="overview-operations"><h3>Оперативное управление</h3><p>Перейдите к маршрутам, проверьте вылеты или подготовьте отчёт.</p><div class="overview-actions"><button class="btn btn-primary" data-navigate="result">${ico("route")}К полётам</button><button class="btn" data-ask="Объясни мой план">${ico("sparkle")}Объяснить план</button><a class="btn" href="/api/plans/${encodeURIComponent(r.plan_id)}/report/print" target="_blank" rel="noopener">${ico("printer")}Отчёт / PDF</a></div></section>
      <section class="report-section"><h3>Условия и высоты</h3><dl class="report-facts">${r.conditions.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl></section>
      <section class="report-section"><h3>Решения планировщика</h3>${list(r.decisions, "Нет данных.")}</section>
      <section class="report-section"><h3>Следующие действия</h3>${list(r.recommendations, "Откройте полёты: проверьте маршруты и задания каждого борта.")}<button class="btn btn-sm" data-navigate="result">К полётам и проверкам ${ico("chevR")}</button></section>
      <details class="report-section"><summary>Замечания расчёта · ${r.warnings.length}</summary>${list(r.warnings, "Замечаний нет.")}</details>`;
    $$(".cmp-show", $("#overview")).forEach(b => b.onclick = () => { const p = S.cmp?.find(x => x.id === b.dataset.id); if (p) loadPlan(p); });
  }
  async function loadReport(plan) {
    report = null; updateContext();
    $("#overview").innerHTML = '<div class="empty">Собираю обзор миссии…</div>';
    try {
      const r = await api(`/api/plans/${encodeURIComponent(plan.id)}/report`);
      if (S.plan?.id !== plan.id) return;
      report = r; renderOverview(r);
    } catch (e) {
      if (S.plan?.id !== plan.id) return;
      $("#overview").innerHTML = `<div class="empty"><b>Не удалось загрузить обзор</b><p>${esc(e.message)}</p><button class="btn" id="report-retry">Повторить</button></div>`;
      $("#report-retry").onclick = () => loadReport(plan);
    }
  }
  function clearReport() { report = null; $("#overview").innerHTML = emptyOverview; updateContext(); }
  function updateContext() { window.missionAssistant?.updateContext(); }
  window.product = {loadReport, clearReport, closeAssistant: () => window.missionAssistant?.close()};
  refreshTerms();
})();
