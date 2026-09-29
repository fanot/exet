"""REST API сервиса планирования (FastAPI, OpenAPI/Swagger: /docs, ReDoc: /redoc).

Все координаты — WGS-84 (градусы), высоты — метры (AMSL или AGL, указано в имени поля),
скорости — м/с, время — ISO 8601 с часовым поясом или Unix-время (секунды).
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from planner import export
from planner.catalog import load_catalog
from planner.geo import ScenarioError, merge_scenarios, parse_geojson, parse_kml, parse_scenario_bytes
from planner.models import MissionParams
from planner.optimizer import Planner, PlanningError
from planner.validate import validate_plan

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
PLANS = DATA / "plans"
PLANS.mkdir(parents=True, exist_ok=True)
PLAN_SCENARIOS = PLANS / "scenarios"  # исходный сценарий каждого плана — чтобы план из истории открывался со своей картой
PLAN_SCENARIOS.mkdir(parents=True, exist_ok=True)
SCENARIOS = ROOT / "scenarios"
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Geoscan Fleet Planner — сервис планирования и распределения авиационных работ БВС",
    version="1.0.0",
    description=(
        "Рассчитывает индивидуальные полетные задания для группы БВС (Геоскан 201 / Gemini / 801): "
        "сетка галсов по GSD и перекрытиям, распределение площади между бортами, вылеты с учетом АКБ и резерва 20%, "
        "расписание, 4D-разведение бортов, экспорт KML/GeoJSON/QGroundControl.\n\n"
        "Единицы: координаты — градусы WGS-84; высоты — м (AMSL/AGL в имени поля); скорости — м/с; "
        "длительности — мин или с (в имени поля); расстояния — км; время — ISO 8601."
    ),
)


@app.exception_handler(RequestValidationError)
async def request_validation_error(request, exc):
    # Invalid JSON numbers may already be inf/nan when Pydantic rejects them.
    # The error response itself must still be serializable as strict JSON.
    detail = jsonable_encoder(exc.errors(), custom_encoder={
        float: lambda value: value if math.isfinite(value) else str(value),
        Exception: str,
    })
    return JSONResponse(status_code=422, content={"detail": detail})

JOBS: Dict[str, dict] = {}
_LOCK = threading.Lock()


class PlanRequest(BaseModel):
    scenario: Dict[str, Any] = Field(..., description="Сценарий: GeoJSON FeatureCollection. properties.kind ∈ "
                                                      "survey_area | no_fly | obstacle | airspace | site | reserve_site")
    params: MissionParams


STAGES = [("prepare", "Подготовка данных"), ("slicing", "Нарезка галсов"), ("allocation", "Распределение площади"),
          ("bayes", "Байесовская оптимизация"), ("dispatch", "Траектории и разведение"), ("validate", "Независимая проверка")]


class JobStatus(BaseModel):
    job_id: str = Field(..., description="Идентификатор задачи расчета")
    status: str = Field(..., description="queued | running | done | error | cancelled")
    progress: float = Field(..., description="Доля выполнения 0..1")
    message: str = Field("", description="Текущий этап (текст)")
    stage: str = Field("prepare", description="Код этапа: prepare | slicing | allocation | bayes | dispatch | validate")
    elapsed_s: float = Field(0.0, description="Время с начала расчета, с")
    evaluations: int = Field(0, description="Сколько вариантов плана оценено поиском")
    trace: List[List[float]] = Field(default_factory=list, description="Сходимость поиска: [№ оценки, лучшее время работ, ч, "
                                                                        "налет лучшего, ч, 1 — байесовская проба / 0 — сетка]")
    plan_id: Optional[str] = Field(None, description="Идентификатор плана (когда status=done)")
    advice: Optional[Dict[str, Any]] = Field(None, description="Результат подбора парка (задача /api/advise): кривая «время работ "
                                                              "от числа бортов», рекомендованный парк к сроку, пояснение")
    error: Optional[str] = None


class Cancelled(Exception):
    pass


def _stage(msg: str, cur: str) -> str:
    m = (msg or "").lower()
    for key, word in (("slicing", "нарезка"), ("allocation", "распределение"), ("bayes", "байес"),
                      ("dispatch", "построение"), ("validate", "покрыт"), ("validate", "готово")):
        if word in m:
            return key
    return cur


def _trace(log: List[dict], limit: int = 240) -> List[List[float]]:
    """Лучший на текущий момент вариант поиска (по критерию) после каждой оценки."""
    out, best = [], None
    for i, r in enumerate(log):
        if best is None or r["score"] < best["score"]:
            best = r
        out.append([i + 1, round(best["makespan"] / 3600, 3), round(best["flight"] / 3600, 3), 1.0 if r.get("method") == "bayes" else 0.0])
    if len(out) > limit:  # прореживание, последняя точка сохраняется
        step = len(out) / limit
        out = [out[int(k * step)] for k in range(limit)] + [out[-1]]
    return out


class ValidateRequest(BaseModel):
    scenario: Dict[str, Any] = Field(..., description="Сценарий (GeoJSON FeatureCollection)")
    plan: Dict[str, Any] = Field(..., description="План в формате ответа /api/plans/{id}")
    params: MissionParams


def _run_job(job_id: str, scenario, params: MissionParams, scenario_gj: dict):
    job = JOBS[job_id]
    job["status"] = "running"
    job["t_start"] = time.time()

    def prog(f, msg):
        if job.get("cancel"):
            raise Cancelled()
        job["progress"] = round(float(f), 3)
        job["message"] = msg
        job["stage"] = _stage(msg, job.get("stage", "prepare"))

    try:
        planner = Planner(scenario, params, prog)
        job["planner"] = planner
        res = planner.run()
        if job.get("cancel"):
            raise Cancelled()
        plan_json = json.dumps(res, ensure_ascii=False, allow_nan=False)
        scenario_json = json.dumps(scenario_gj, ensure_ascii=False, allow_nan=False)
        # Cancel and publication have one ordering: an accepted cancellation
        # cannot turn into a saved plan when run() returns through an early error.
        with _LOCK:
            if job.get("cancel"):
                raise Cancelled()
            (PLAN_SCENARIOS / f"{res['id']}.json").write_text(scenario_json, encoding="utf-8")
            (PLANS / f"{res['id']}.json").write_text(plan_json, encoding="utf-8")
            job.update(status="done", progress=1.0, plan_id=res["id"], message="Готово", stage="validate")
    except Cancelled:
        job.update(status="cancelled", message="Расчет отменен")
    except (ScenarioError, PlanningError, ValueError) as e:
        job.update(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        job.update(status="error", error=f"Внутренняя ошибка: {type(e).__name__}: {e}")
    finally:
        job["elapsed_final"] = time.time() - job["t_start"]
        pl = job.get("planner")
        if pl is not None:
            job["trace_final"] = _trace(pl.search_log)
            job["evaluations_final"] = len(pl.search_log)
            job.pop("planner", None)


def _run_advice(job_id: str, scenario, params: MissionParams):
    from planner.advisor import advise
    job = JOBS[job_id]
    job["status"] = "running"
    job["t_start"] = time.time()
    job["stage"] = "allocation"

    def prog(f, msg):
        if job.get("cancel"):
            raise Cancelled()
        job["progress"] = round(float(f), 3)
        job["message"] = msg

    try:
        result = advise(scenario, params, prog)
        with _LOCK:
            if job.get("cancel"):
                raise Cancelled()
            job.update(status="done", progress=1.0, message="Готово", advice=result, stage="validate")
    except Cancelled:
        job.update(status="cancelled", message="Подбор отменен")
    except (ScenarioError, PlanningError, ValueError) as e:
        job.update(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        job.update(status="error", error=f"Внутренняя ошибка: {type(e).__name__}: {e}")
    finally:
        job["elapsed_final"] = time.time() - job["t_start"]


def _status(job: dict) -> JobStatus:
    pl = job.get("planner")
    log = list(pl.search_log) if pl is not None else []
    el = job.get("elapsed_final", time.time() - job["t_start"] if "t_start" in job else 0.0)
    return JobStatus(**{k: job.get(k) for k in ("job_id", "status", "progress", "message", "plan_id", "error", "advice")},
                     stage=job.get("stage", "prepare"), elapsed_s=round(el, 1),
                     evaluations=job.get("evaluations_final", len(log)), trace=job.get("trace_final", _trace(log)))


def _start(scenario, params: MissionParams, scenario_gj: dict) -> JobStatus:
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"job_id": job_id, "status": "queued", "progress": 0.0, "message": "В очереди", "plan_id": None, "error": None}
    # сценарий сохраняется до расчета: планировщик дописывает в него добавленные площадки
    threading.Thread(target=_run_job, args=(job_id, scenario, params, scenario_gj), daemon=True).start()
    return _status(JOBS[job_id])


def _load(plan_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", plan_id):
        raise HTTPException(404, "План не найден")
    p = PLANS / f"{plan_id}.json"
    if not p.exists() or "/" in plan_id or ".." in plan_id:
        raise HTTPException(404, "План не найден")
    try:
        r = json.loads(p.read_text(encoding="utf-8"), parse_constant=lambda _: None)
    except (ValueError, OSError):
        raise HTTPException(404, "Файл плана поврежден")
    if not isinstance(r, dict) or r.get("id") != plan_id:
        raise HTTPException(404, "Файл плана поврежден")
    return r


@app.get("/api/health", tags=["Сервис"], summary="Проверка работоспособности")
def health():
    return {"status": "ok"}


@app.get("/api/catalog", tags=["Справочники"], summary="Каталог БВС, полезных нагрузок и типов съемки (ТТХ)")
def catalog():
    cat = load_catalog()
    from planner.energy import table_info
    return {"global": cat.glob, "survey_types": cat.survey_types, "sensors": cat.sensors, "energy_model": table_info(),
            "drones": {k: {**d.__dict__, "turn_radius_m": round(d.turn_radius_m, 1)} for k, d in cat.drones.items()}}


@app.get("/api/scenarios", tags=["Справочники"], summary="Демонстрационные сценарии")
def scenarios():
    out = []
    for f in sorted(SCENARIOS.glob("*.geojson"), key=lambda f: (0 if "toksovo" in f.stem else 1, f.stem)):
        data = json.loads(f.read_text(encoding="utf-8"))
        out.append({"id": f.stem, "name": data.get("name", f.stem)})
    return out


@app.get("/api/scenarios/{sid}", tags=["Справочники"], summary="Сценарий (GeoJSON)")
def scenario(sid: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        raise HTTPException(404, "Сценарий не найден")
    f = SCENARIOS / f"{sid}.geojson"
    if not f.exists() or "/" in sid:
        raise HTTPException(404, "Сценарий не найден")
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")))


@app.post("/api/plan", response_model=JobStatus, tags=["Планирование"],
          summary="Запустить расчет плана (сценарий GeoJSON + параметры миссии)")
def create_plan(req: PlanRequest):
    try:
        sc = parse_geojson(req.scenario)
    except ScenarioError as e:
        raise HTTPException(422, str(e))
    return _start(sc, req.params, req.scenario)


@app.post("/api/advise", response_model=JobStatus, tags=["Планирование"],
          summary="Подбор парка к сроку: сколько бортов нужно, чтобы уложиться в deadline_h; кривая «время работ от числа бортов»")
def create_advice(req: PlanRequest):
    """params.fleet — доступный парк (максимум бортов каждого типа), params.deadline_h — срок (необязателен).
    Результат — в поле advice статуса задачи."""
    try:
        sc = parse_geojson(req.scenario)
    except ScenarioError as e:
        raise HTTPException(422, str(e))
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"job_id": job_id, "status": "queued", "progress": 0.0, "message": "В очереди", "plan_id": None, "error": None}
    threading.Thread(target=_run_advice, args=(job_id, sc, req.params), daemon=True).start()
    return _status(JOBS[job_id])


async def _read_layers(files: List[UploadFile], partial: bool):
    parts = []
    if len(files) > 64:
        raise HTTPException(413, "Не более 64 файлов за одну загрузку")
    for f in files:
        content = await f.read(50 * 1024 * 1024 + 1)
        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(413, f"Файл «{f.filename}» больше 50 МБ")
        try:
            parts.append(parse_scenario_bytes(content, f.filename or "scenario.geojson", partial=True))
        except ScenarioError as e:
            raise HTTPException(422, f"{f.filename}: {e}")
    name = " + ".join(p.name for p in parts)
    try:
        sc = merge_scenarios(parts, name=name, partial=partial)
    except ScenarioError as e:
        raise HTTPException(422, str(e))
    sc.crop_to_work()  # зоны/препятствия регионального файла — только у района работ
    return sc


@app.post("/api/plan/upload", response_model=JobStatus, tags=["Планирование"],
          summary="Запустить расчет по файлам сценария (GeoJSON/KML; область, зоны, препятствия — можно отдельными файлами)")
async def create_plan_upload(files: List[UploadFile] = File(..., description="Один или несколько файлов .geojson/.json/.kml — слои сценария"),
                             params: str = Form(..., description="MissionParams в виде JSON-строки")):
    sc = await _read_layers(files, partial=False)
    try:
        p = MissionParams.model_validate_json(params)
    except ValueError as e:
        raise HTTPException(422, f"Параметры: {e}")
    return _start(sc, p, scenario_to_geojson(sc))


def scenario_to_geojson(sc) -> dict:
    from shapely.geometry import mapping
    feats = [{"type": "Feature", "properties": {"kind": "survey_area", "name": n}, "geometry": mapping(g)} for n, g in sc.areas]
    for z in sc.nofly:
        feats.append({"type": "Feature", "geometry": mapping(z.geom), "properties": {
            "kind": "no_fly", "name": z.name, "alt_min_m": z.alt_min_m, "alt_max_m": z.alt_max_m, "alt_ref": z.alt_ref,
            "category": z.category, "note": z.note or None, "time_unknown": z.time_unknown or None,
            "active_from": z.active_from.isoformat() if z.active_from else None,
            "active_to": z.active_to.isoformat() if z.active_to else None}})
    for o in sc.obstacles:
        feats.append({"type": "Feature", "geometry": mapping(o.geom), "properties": {
            "kind": "obstacle", "name": o.name, "height_m": round(o.height_m, 1), "radius_m": o.radius_m, "alt_ref": o.alt_ref}})
    if sc.airspace is not None:
        feats.append({"type": "Feature", "geometry": mapping(sc.airspace), "properties": {"kind": "airspace", "ceiling_m": sc.airspace_ceiling_m}})
    for s in sc.sites:
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [s.lon, s.lat]},
                      "properties": {"kind": "reserve_site" if s.reserve else "site", "name": s.name}})
    return {"type": "FeatureCollection", "name": sc.name, "features": feats, "notes": sc.notes}


@app.post("/api/scenario/parse", tags=["Планирование"],
          summary="Разобрать файлы сценария (KML/GeoJSON, один или несколько слоев) в GeoJSON для просмотра")
async def parse_file(files: List[UploadFile] = File(..., description="Файлы-слои: область съемки, запретные зоны, высотные препятствия, площадки"),
                     partial: bool = Form(True, description="Разрешить неполный набор (например, только слой препятствий)")):
    return scenario_to_geojson(await _read_layers(files, partial=partial))


@app.get("/api/jobs/{job_id}", response_model=JobStatus, tags=["Планирование"], summary="Статус расчета")
def job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "Задача не найдена")
    return _status(JOBS[job_id])


@app.post("/api/jobs/{job_id}/cancel", response_model=JobStatus, tags=["Планирование"], summary="Отменить расчет")
def cancel_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "Задача не найдена")
    with _LOCK:
        if JOBS[job_id]["status"] not in ("done", "error", "cancelled"):
            JOBS[job_id]["cancel"] = True
    return _status(JOBS[job_id])


@app.get("/api/plans", tags=["Планы"], summary="История расчетов (для сравнения вариантов)")
def plans():
    out = []
    for f in sorted(PLANS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            r = json.loads(f.read_text(encoding="utf-8"), parse_constant=lambda _: None)
            if not isinstance(r, dict) or r.get("id") != f.stem:
                continue
            if not isinstance(r.get("params", {}), dict) or not isinstance(r.get("validation", {}), dict):
                continue
        except Exception:  # noqa: BLE001
            continue
        out.append({"id": r["id"], "created": r.get("created"), "status": r.get("status"), "scenario_name": r.get("scenario_name"),
                    "objective": r.get("params", {}).get("objective"), "summary": r.get("summary"),
                    "fleet": r.get("params", {}).get("fleet"), "validation_passed": r.get("validation", {}).get("passed"),
                    "has_scenario": (PLAN_SCENARIOS / f"{r['id']}.json").exists()})
        if len(out) == 100:
            break
    return out


@app.get("/api/plans/{plan_id}", tags=["Планы"], summary="План целиком (вылеты, точки, расписание, проверки)")
def get_plan(plan_id: str):
    return JSONResponse(_load(plan_id))


@app.get("/api/plans/{plan_id}/scenario", tags=["Планы"], summary="Исходный сценарий плана (GeoJSON)")
def plan_scenario(plan_id: str):
    _load(plan_id)
    f = PLAN_SCENARIOS / f"{plan_id}.json"
    if not f.exists():
        raise HTTPException(404, "Сценарий этого плана не сохранен (план рассчитан до обновления)")
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")))


@app.delete("/api/plans/{plan_id}", tags=["Планы"], summary="Удалить план из истории")
def delete_plan(plan_id: str):
    _load(plan_id)
    (PLANS / f"{plan_id}.json").unlink()
    (PLAN_SCENARIOS / f"{plan_id}.json").unlink(missing_ok=True)
    return {"deleted": plan_id}


@app.get("/api/plans/{plan_id}/export/{fmt}", tags=["Экспорт"],
         summary="Экспорт: geojson | kml | csv | zip (всё + QGroundControl .plan по вылетам) | qgc (один вылет, нужен sortie)")
def export_plan(plan_id: str, fmt: str,
                drone: Optional[str] = Query(None, description="Только один борт (индивидуальное полетное задание), например GEM-1"),
                sortie: Optional[int] = Query(None, description="Номер вылета — для fmt=qgc")):
    r = _load(plan_id)
    if r.get("status") != "ok" or not r.get("validation", {}).get("passed") or not r.get("sorties"):
        raise HTTPException(409, "Экспорт доступен только для успешного плана, прошедшего проверку")
    name = f"plan_{plan_id}"
    if fmt == "qgc":
        s = next((x for x in r["sorties"] if x["id"] == sortie), None)
        if s is None:
            raise HTTPException(404, "Укажите существующий номер вылета: ?sortie=N")
        return Response(json.dumps(export.to_qgc_plan(r, s, load_catalog()), ensure_ascii=False), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{s["drone_id"]}_sortie{s["id"]:03d}.plan"'})
    if drone:
        try:
            r = export.subset(r, drone)
        except KeyError:
            raise HTTPException(404, f"Борт {drone} не найден в плане")
        name += f"_{drone}"
    if fmt == "geojson":
        return Response(json.dumps(export.to_geojson(r), ensure_ascii=False), media_type="application/geo+json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.geojson"'})
    if fmt == "kml":
        return Response(export.to_kml(r), media_type="application/vnd.google-earth.kml+xml",
                        headers={"Content-Disposition": f'attachment; filename="{name}.kml"'})
    if fmt == "csv":
        return Response("﻿" + export.to_csv(r), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}_schedule.csv"'})
    if fmt == "zip":
        return Response(export.to_zip(r, load_catalog()), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})
    raise HTTPException(400, "Формат: geojson | kml | csv | zip | qgc")


@app.get("/api/plans/{plan_id}/brief", response_class=HTMLResponse, tags=["Экспорт"],
         summary="Полетное задание для печати (HTML): маршрут, этапы, ключевые точки, профиль высот — по всем бортам или одному")
def brief(plan_id: str, drone: Optional[str] = Query(None, description="Борт, например GEM-1; без параметра — все борта")):
    from planner.brief import to_brief_html
    r = _load(plan_id)
    if r.get("status") != "ok" or not r.get("validation", {}).get("passed") or not r.get("sorties"):
        raise HTTPException(409, "Полетное задание доступно только для успешного плана, прошедшего проверку")
    if drone and not any(d["id"] == drone for d in r.get("drones", [])):
        raise HTTPException(404, f"Борт {drone} не найден в плане")
    return HTMLResponse(to_brief_html(r, drone))


@app.post("/api/validate", tags=["Проверка"],
          summary="Независимая проверка плана (сближения, запретные зоны, препятствия, энергия, высоты, ветер)")
def validate(req: ValidateRequest):
    try:
        sc = parse_geojson(req.scenario)
    except ScenarioError as e:
        raise HTTPException(422, str(e))
    return validate_plan(req.plan, sc, req.params)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
