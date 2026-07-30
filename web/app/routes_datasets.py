"""Dataset ve istatistik uçları (WEB_PLANI.md §6, Faz 5).

``/api/datasets/summary`` model × tür × karar kırılımını verir; export
işleri ``webapp.export_jobs``ta yaşar ve zip çıktısı da DB'de saklanır
(§2 "tek temas PostgreSQL" — konteynerler geçicidir). ``POST
/api/datasets/export`` işi kaydedip FastAPI arka plan görevinde üretir;
görev havuzdan **kendi** bağlantısını alır (istek bağlantısı yanıtla
birlikte havuza döner). Aynı model + kapsam için bekleyen/koşan iş varken
ikincisi 409 ile reddedilir. YOLO üretim kuralları saf ``exportbuild``
modülündedir; etiketler ``final_*``tan, koordinat ``final_bbox/frame``
bölümüyle normalize üretilir ve ``wrong`` kapsamı boş etiketli
hard-negative/background görüntüleri verir (§9 Faz 5 kabulü).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from psycopg import errors as psycopg_errors

from . import accounts
from .db import get_connection
from .deps import require_csrf, require_user
from .exportbuild import ExportSample, assemble_zip, build_class_map, build_yolo_entries
from .routes_archive import _require_archive_schema
from .routes_reviews import KNOWN_MODELS

router = APIRouter(prefix="/api", tags=["datasets"])

# Bellek emniyeti: tek işte zip'e girecek en çok görüntü. Aşımı iş 'failed'
# yapmaz; en eski reviewed_at öncelikli ilk N kare alınır ve manifest'e
# 'truncated' yazılır.
MAX_EXPORT_IMAGES = 5000


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


class ExportIn(BaseModel):
    model_id: Literal["roadline", "traffic_sign", "pothole", "marking_damage"]
    verdict: Literal["positive", "wrong"]


_SUMMARY_SQL = """
SELECT
    s.model_id,
    s.final_type_id,
    s.final_class_name,
    s.verdict,
    count(*) AS samples,
    count(s.original_sha) AS with_image
FROM webapp.dataset_samples AS s
GROUP BY s.model_id, s.final_type_id, s.final_class_name, s.verdict
ORDER BY s.model_id, s.final_class_name, s.verdict
"""

_JOB_COLS = (
    "job_id, requested_by, model_id, verdict_scope, status, created_at, "
    "started_at, finished_at, sample_count, image_count, skipped_no_image, "
    "skipped_no_bbox, byte_size, error"
)


def _job_row(row: Any) -> dict:
    keys = (
        "job_id", "requested_by", "model_id", "verdict_scope", "status",
        "created_at", "started_at", "finished_at", "sample_count",
        "image_count", "skipped_no_image", "skipped_no_bbox", "byte_size",
        "error",
    )
    return dict(zip(keys, row))


@router.get("/datasets/summary")
async def dataset_summary(
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> dict:
    """Model × tür × karar kırılımı (doğrulanmış örnekler üzerinden)."""

    cur = await conn.execute(_SUMMARY_SQL)
    rows = await cur.fetchall()
    models: dict[str, dict] = {}
    for model_id, type_id, class_name, verdict, samples, with_image in rows:
        model = models.setdefault(
            model_id, {"model_id": model_id, "types": {}, "totals": {}}
        )
        node = model["types"].setdefault(
            type_id,
            {
                "final_type_id": type_id,
                "final_class_name": class_name,
                "counts": {"correct": 0, "corrected": 0, "wrong": 0},
                "with_image": 0,
            },
        )
        node["counts"][verdict] = samples
        node["with_image"] += with_image
        model["totals"][verdict] = model["totals"].get(verdict, 0) + samples
    return {
        "models": [
            {
                "model_id": model["model_id"],
                "totals": model["totals"],
                "types": sorted(
                    model["types"].values(),
                    key=lambda item: item["final_class_name"],
                ),
            }
            for model in models.values()
        ]
    }


@router.get("/stats/overview")
async def stats_overview(
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> dict:
    """Panel kartları: tespit hacmi, doğrulama ilerlemesi, model dağılımı."""

    await _require_archive_schema(conn)
    cur = await conn.execute(
        """
        SELECT
            (SELECT count(*) FROM public.detected_objects),
            (SELECT count(*) FROM public.detected_objects
              WHERE ts >= now() - interval '24 hours'),
            (SELECT count(*) FROM webapp.detection_reviews),
            (SELECT count(*) FROM webapp.detection_reviews
              WHERE reviewed_at >= now() - interval '24 hours'),
            (SELECT count(*) FROM webapp.dataset_samples
              WHERE original_sha IS NOT NULL),
            (SELECT count(*) FROM webapp.export_jobs
              WHERE status IN ('pending', 'running'))
        """
    )
    (total, last24, reviewed, reviewed24, with_image, active_jobs) = (
        await cur.fetchone()
    )
    cur = await conn.execute(
        """
        SELECT verdict, count(*) FROM webapp.detection_reviews
        GROUP BY verdict
        """
    )
    verdicts = {verdict: count for verdict, count in await cur.fetchall()}
    cur = await conn.execute(
        """
        SELECT o.model_id, count(*),
               count(r.object_id)
        FROM public.detected_objects AS o
        LEFT JOIN webapp.detection_reviews AS r ON r.object_id = o.id
        GROUP BY o.model_id
        ORDER BY o.model_id
        """
    )
    models = [
        {"model_id": model_id, "detections": detections, "reviewed": reviewed_n}
        for model_id, detections, reviewed_n in await cur.fetchall()
    ]
    return {
        "detections": {"total": total, "last_24h": last24},
        "reviews": {
            "total": reviewed,
            "last_24h": reviewed24,
            "verdicts": verdicts,
            "coverage": (reviewed / total) if total else 0.0,
        },
        "dataset": {"samples_with_image": with_image},
        "export_jobs_active": active_jobs,
        "models": models,
    }


async def _run_export(pool: Any, job_id: int) -> None:
    """Arka plan görevi: işi koşar, zip'i DB'ye yazar; hata işi 'failed' yapar."""

    async with pool.connection() as conn:
        try:
            cur = await conn.execute(
                """
                UPDATE webapp.export_jobs
                SET status = 'running', started_at = now()
                WHERE job_id = %s AND status = 'pending'
                RETURNING model_id, verdict_scope
                """,
                (job_id,),
            )
            row = await cur.fetchone()
            if row is None:  # yarışta başkası aldıysa
                return
            model_id, verdict_scope = row
            await conn.commit()

            cur = await conn.execute(
                "SELECT type_id, class_index, class_name "
                "FROM public.detection_types WHERE model_id = %s",
                (model_id,),
            )
            names, type_to_index = build_class_map(list(await cur.fetchall()))

            verdicts = (
                ("correct", "corrected")
                if verdict_scope == "positive"
                else ("wrong",)
            )
            cur = await conn.execute(
                """
                SELECT object_id, verdict, final_type_id, final_bbox,
                       frame_w, frame_h, original_sha
                FROM webapp.dataset_samples
                WHERE model_id = %s AND verdict = ANY(%s)
                ORDER BY reviewed_at, object_id
                """,
                (model_id, list(verdicts)),
            )
            samples = [
                ExportSample(
                    object_id=object_id,
                    verdict=verdict,
                    final_type_id=final_type_id,
                    final_bbox=tuple(final_bbox)
                    if final_bbox is not None
                    else None,
                    frame_w=frame_w,
                    frame_h=frame_h,
                    original_sha=original_sha,
                )
                for (
                    object_id, verdict, final_type_id, final_bbox,
                    frame_w, frame_h, original_sha,
                ) in await cur.fetchall()
            ]
            labels, counters = build_yolo_entries(
                samples, type_to_index, verdict_scope
            )
            truncated = False
            # Sorgu zaten reviewed_at ASC, object_id ASC sırasındadır.
            # Ekleme sırasını korumak, sınır aşıldığında en eski incelenmiş
            # karelerin seçilmesini sağlar. Zip yazıcısı kendi içinde sıralar.
            shas = list(labels)
            if len(shas) > MAX_EXPORT_IMAGES:
                shas = shas[:MAX_EXPORT_IMAGES]
                labels = {sha: labels[sha] for sha in shas}
                counters["image_count"] = len(shas)
                truncated = True

            images: dict[str, bytes] = {}
            for sha in shas:
                cur = await conn.execute(
                    "SELECT bytes FROM webapp.dataset_media WHERE sha256 = %s",
                    (sha,),
                )
                media = await cur.fetchone()
                if media is None:  # FK gereği beklenmez; savunmacı
                    labels.pop(sha, None)
                    counters["skipped_no_image"] += 1
                    counters["image_count"] -= 1
                    continue
                images[sha] = bytes(media[0])
            if truncated:
                counters["truncated_at"] = MAX_EXPORT_IMAGES

            # Zip kurulumu senkron ve CPU-yoğundur (etiket/manifest deflate,
            # binlerce arşiv girdisi); arka plan görevi event loop'ta
            # koştuğundan doğrudan çağrı tüm HTTP isteklerini bloklar.
            # Login'deki Argon2 ile aynı disiplin: worker thread'e taşınır.
            zip_bytes = await asyncio.to_thread(
                assemble_zip,
                model_id=model_id,
                verdict_scope=verdict_scope,
                names=names,
                labels=labels,
                images=images,
                counters=counters,
            )
            await conn.execute(
                """
                UPDATE webapp.export_jobs
                SET status = 'done', finished_at = now(),
                    sample_count = %s, image_count = %s,
                    skipped_no_image = %s, skipped_no_bbox = %s,
                    byte_size = %s, zip_bytes = %s, error = NULL
                WHERE job_id = %s
                """,
                (
                    counters["sample_count"],
                    counters["image_count"],
                    counters["skipped_no_image"],
                    counters["skipped_no_bbox"],
                    len(zip_bytes),
                    zip_bytes,
                    job_id,
                ),
            )
            await conn.commit()
        except Exception as exc:  # işi asılı bırakma
            await conn.rollback()
            await conn.execute(
                """
                UPDATE webapp.export_jobs
                SET status = 'failed', finished_at = now(), error = %s
                WHERE job_id = %s
                """,
                (f"{type(exc).__name__}: {exc}"[:2000], job_id),
            )
            await conn.commit()


@router.post("/datasets/export", status_code=202)
async def start_export(
    payload: ExportIn,
    background: BackgroundTasks,
    request: Request,
    user: accounts.AuthContext = Depends(require_user),
    _csrf: None = Depends(require_csrf),
    conn: Any = Depends(get_connection),
) -> dict:
    await _require_archive_schema(conn)
    if payload.model_id not in KNOWN_MODELS:  # Literal zaten korur; savunmacı
        raise _error(422, "unsupported_model", "Model bölümlemede tanımlı değil.")
    await conn.commit()
    try:
        async with conn.transaction():
            cur = await conn.execute(
                """
                SELECT job_id FROM webapp.export_jobs
                WHERE model_id = %s AND verdict_scope = %s
                  AND status IN ('pending', 'running')
                LIMIT 1
                """,
                (payload.model_id, payload.verdict),
            )
            if await cur.fetchone() is not None:
                raise _error(
                    409,
                    "export_in_progress",
                    "Aynı model ve kapsam için bekleyen/koşan bir export işi var.",
                )
            cur = await conn.execute(
                f"""
                INSERT INTO webapp.export_jobs (requested_by, model_id, verdict_scope)
                VALUES (%s, %s, %s)
                RETURNING {_JOB_COLS}
                """,
                (user.user.user_id, payload.model_id, payload.verdict),
            )
            job = _job_row(await cur.fetchone())
    except psycopg_errors.UniqueViolation as exc:
        # SELECT + INSERT arası yarışta DB'deki partial unique indeks son
        # savunma hattıdır; istemci sözleşmesi yine aynı 409'u görür.
        raise _error(
            409,
            "export_in_progress",
            "Aynı model ve kapsam için bekleyen/koşan bir export işi var.",
        ) from exc
    background.add_task(_run_export, request.app.state.pool, job["job_id"])
    return {"job": job}


@router.get("/datasets/exports")
async def list_exports(
    limit: int = Query(default=20, ge=1, le=100),
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> dict:
    cur = await conn.execute(
        f"""
        SELECT {_JOB_COLS} FROM webapp.export_jobs
        ORDER BY job_id DESC
        LIMIT %s
        """,
        (limit,),
    )
    return {"jobs": [_job_row(row) for row in await cur.fetchall()]}


@router.get("/datasets/exports/{job_id}")
async def get_export(
    job_id: int,
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> dict:
    cur = await conn.execute(
        f"SELECT {_JOB_COLS} FROM webapp.export_jobs WHERE job_id = %s",
        (job_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise _error(404, "export_not_found", "Export işi bulunamadı.")
    return {"job": _job_row(row)}


@router.get("/datasets/exports/{job_id}/download")
async def download_export(
    job_id: int,
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> Response:
    cur = await conn.execute(
        "SELECT model_id, verdict_scope, status, zip_bytes "
        "FROM webapp.export_jobs WHERE job_id = %s",
        (job_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise _error(404, "export_not_found", "Export işi bulunamadı.")
    model_id, verdict_scope, status, zip_bytes = row
    if status != "done" or zip_bytes is None:
        raise _error(
            409, "export_not_ready", f"İş '{status}' durumunda; indirilemez."
        )
    filename = f"roadvision_{model_id}_{verdict_scope}_{job_id}.zip"
    return Response(
        content=bytes(zip_bytes),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
