"""Summarize the preserved before/after evidence for the Moscow bug fixes."""
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'out'
TARGETS = ['03_ignore_temporary', '07_wind_6_5_east', '09_wind_40_max_dir', '11_ir',
           '12_lidar', '22_gsd_200', '37_no_plan_vlos_0_3', '46_ir_high_gsd_fast', '48_combined']


def main():
    evidence = []
    for target in TARGETS:
        paths = [OUT / folder / target / 'result.json' for folder in ('qa_moscow_fixes_final', 'qa_moscow_fixes')]
        path = next((p for p in paths if p.exists()), None)
        if path is None:
            continue
        record = json.loads(path.read_text(encoding='utf-8'))
        plan_path = path.parent / 'plan.json.gz'
        result = json.loads(gzip.decompress(plan_path.read_bytes()))
        summary = result.get('summary', {})
        checks = {}
        if result.get('sorties'):
            layers = sorted({s['transit_alt_agl_m'] for s in result['sorties']})
            checks['reported_layers_match_used_drones'] = layers == summary['transit_layers_agl_m']
            actual_models = {s['model'] for s in result['sorties']}
            checks['shares_match_used_models'] = set(summary['shares']) == actual_models
            checks['shares_sum_to_one'] = abs(sum(summary['shares'].values()) - 1) < 1e-8
            checks['repeat_validation_agrees'] = record['validation'] == record['independent_validation']
            assert all(checks.values()), (target, checks)
        evidence.append({'case': target, 'path': str(path.relative_to(ROOT)), 'status': result['status'],
                         'error': result.get('error'), 'validation': (record.get('validation') or {}).get('passed'),
                         'coverage': summary.get('coverage_allowed_pct'), 'sorties': summary.get('sorties'),
                         'seconds': record['elapsed_s'], 'summary_checks': checks})
    artifacts = ['api/app.py', 'api/static/app.js', 'api/static/index.html', 'planner/routing.py',
                 'planner/sortie.py', 'planner/dispatch.py', 'planner/optimizer.py', 'planner/sites.py',
                 'planner/validate.py', 'tests/test_moscow_fixes.py', 'tests/ui_params_roundtrip.cjs']
    manifest = {'results': evidence, 'source_sha256': {
        f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in artifacts}}
    (OUT / 'qa_moscow_fixes' / 'verification.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Проверка исправлений московского задания, 29.09.2026', '',
             'Исправлены 14 подтвержденных дефектов M-01…M-14 из исходного аудита. Изменения находятся в локальном коде.', '',
             'Полный набор: **276 тестов пройдены**. После него добавлена и отдельно пройдена проверка выбора безопасного кандидата; '
             'все 111 тестов `tests/test_moscow_fixes.py` проходят. Всего 277 разных тестов. '
             'Есть одно предупреждение об устаревающем httpx, ошибок нет. `node --check` и `git diff --check` проходят.', '',
             'Реальные функции формы исполнены в Node с моделью DOM: восстановление всех 24 параметров в 56 наборах, '
             'UTC/отрицательное/получасовое смещение, секунды и доли секунд, граничные числа, каталожные перекрытия и кнопки количества/АКБ. '
             '99 запросов с NaN, Infinity и 1e309 проверены на трех API: все отвечают сериализуемым HTTP 422. '
             'Проверены отмена без сохранения результата, другие площадки, продолжение расписания после недоступного участка '
             'и выбор следующего кандидата после отказа независимого валидатора.', '',
             '## Повторные расчеты полного задания', '',
             '| Случай | Статус | Проверка траекторий | Доступная площадь | Вылеты | Время, с |',
             '|---|---|---|---:|---:|---:|']
    for row in evidence:
        check = 'пройдена' if row['validation'] else ('ошибка' if row['validation'] is False else 'нет траекторий')
        coverage = f"{row['coverage']:.2f}%" if row['coverage'] is not None else '—'
        lines.append(f"| {row['case']} | {row['status']} | {check} | {coverage} | {row['sorties'] or '—'} | {row['seconds']:.1f} |")
    lines += ['',
              '`46_ir_high_gsd_fast`: те же ИК-съемка, GSD 200 см, парк и геометрия, что в исходном случае 46; '
              'для проверки высот применен fast/grid вместо thorough/bayes. 150 проб TPE повторно не запускались. '
              '`48_combined`: сохранены все 24 исходных параметра; после завершенного повторного normal/bayes поиска '
              'окончательная проверка расписания выполнена на шести сохраненных кандидатах (`scripts/verify_moscow_combined.py`). '
              'Это повтор построения траекторий, а не второй повтор 60 проб TPE.', '',
              'По всем построенным планам повторно запущен неизмененный независимый валидатор. '
              'Сводка дополнительно сверена с реальными эшелонами использованных бортов и модельным составом вылетов. '
              'Первичные результаты сохранены в `out/qa_moscow_params`, повторные — в `out/qa_moscow_fixes` и `out/qa_moscow_fixes_final`. '
              'Окончательные результаты лидара и комбинированного задания берутся из второй папки: первый повтор выявил необходимость '
              'локально обрабатывать недоступные переходы. Хеш валидатора сохранен в verification.json, его код не изменялся.', '',
              '## Что остается обоснованным отказом', '',
              'Радиус прямой видимости 300 м на полной Москве дает безопасный частичный план, но не покрывает задание. '
              'Отсутствие нарушений полета не означает выполнение всей съемки. Рекомендации теперь указывают фактическое покрытие кругами и его недостаточность.', '',
              'На трубопроводе 35 км дополнительно проверены лидар без плана полета и ветер 9 м/с. '
              'Первый дает безопасный частичный план (6,13% доступной площади); второй корректно исключает коптеры по ветру на рабочей высоте '
              'и самолет по режиму без плана полета. История и общий результат должны показывать невыполнимость задания; 10/10 относится к безопасности рассчитанных вылетов.', '',
              '## Границы проверки', '',
              'Исходный аудит охватывал 52 расчета. Повторены целевые проблемные физические случаи; вся матрица 52 после изменений повторно не запускалась. '
              'Визуальная проверка браузером недоступна в этой сессии; проверено исполнение настоящих JS-функций и API. '
              'Старые сохраненные планы не переписываются: новые траектории появляются после повторного расчета. '
              'Отдельное профилирование скорости normal/bayes остается улучшением, а не закрытым дефектом.', '',
              'Для воспроизведения: `python -X utf8 scripts/qa_moscow_params.py --out out/qa_moscow_fixes_final --only 12_lidar,37_no_plan_vlos_0_3 --workers 2 --timeout 1800`. '
              'Сводка и хеши исходников: `out/qa_moscow_fixes/verification.json`; этот отчет: `python scripts/report_moscow_fixes.py`.', '']
    (ROOT / 'docs' / 'FIXES_VERIFIED_MOSCOW_2026-09-29.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == '__main__':
    main()
