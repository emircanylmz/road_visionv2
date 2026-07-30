"""Doğrulama uçları (WEB_PLANI.md §6, Faz 4).

``/api/verify/queue`` karar bekleyenleri (karar satırı olmayan tespitleri)
en eskiden yeniye listeler. ``/api/reviews`` karar yazar; her karar tek
transaction'dır ve üç adımı ya hep ya hiç yürütür (§4.4–4.5):

1. ``webapp.detection_reviews`` satırı (PK ihlali → 409 ``already_reviewed``),
2. copy-on-verify: orijinal + işaretli JPEG baytları ``webapp.dataset_media``ya
   (``ON CONFLICT DO NOTHING`` — aynı kareden ikinci tespit bedava),
3. ``webapp.dataset_samples`` örneği (karar × model bölümüne düşer).

Kararı değiştirme yetkisi kararı veren kullanıcı ile yöneticilerdedir;
``PATCH`` verdict'i günceller, örnek satırı yeni bölüme taşınır (verdict
partition key olduğundan UPDATE satırı otomatik taşır) ve ``admin_audit``e
yazılır. Kural doğrulamasının tamamı saf ``reviewrules`` modülündedir.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import accounts
from .archivequery import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ArchiveFilters,
    build_list_query,
    decode_cursor,
    encode_cursor,
)
from .db import get_connection
from .deps import require_csrf, require_user
from .reviewrules import (
    DetectionContext,
    NormalizedReview,
    ReviewRuleError,
    normalize_review,
)
from .routes_archive import _detection_row, _require_archive_schema

router = APIRouter(prefix="/api", tags=["reviews"])

# §4.5: bölümleme bilinen dört modeli açar; beşinci model bilinçli bir
# migration ister. API bunu partition hatasına düşmeden önce yakalar.
KNOWN_MODELS = ("roadline", "traffic_sign", "pothole", "marking_damage")


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


class ReviewIn(BaseModel):
    object_id: int = Field(ge=1)
    verdict: Literal["correct", "corrected", "wrong"]
    corrected_bbox: list[float] | None = Field(
        default=None, min_length=4, max_length=4
    )
    corrected_class: str | None = Field(default=None, min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


class ReviewPatchIn(BaseModel):
    verdict: Literal["correct", "corrected", "wrong"]
    corrected_bbox: list[float] | None = Field(
        default=None, min_length=4, max_length=4
    )
    corrected_class: str | None = Field(default=None, min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


_DETECTION_CTX_SQL = """
SELECT
    o.id,
    o.model_id,
    COALESCE(m.task, 'unknown') AS model_task,
    o.type_id,
    o.class_name,
    o.bbox,
    o.confidence,
    o.area_ratio,
    o.ts,
    o.run_id,
    e.capture_id,
    ob.sha256,
    ob.width,
    ob.height,
    ab.sha256
FROM public.detected_objects AS o
JOIN public.detection_events AS e
    ON e.id = o.event_id
LEFT JOIN public.roadvision_model_catalog AS m
    ON m.model_id = o.model_id
LEFT JOIN public.media_captures AS mc
    ON mc.capture_id = e.capture_id
LEFT JOIN public.media_blobs AS ob
    ON ob.id = mc.original_media_id
LEFT JOIN public.media_blobs AS ab
    ON ab.id = mc.annotated_media_id
WHERE o.id = %s
"""

# copy-on-verify: baytlar tek SQL ile web deposuna kopyalanır (§4.4).
_COPY_MEDIA_SQL = """
INSERT INTO webapp.dataset_media (sha256, bytes, width, height, byte_size)
SELECT b.sha256, b.data, b.width, b.height, b.byte_size
FROM public.media_blobs AS b
WHERE b.sha256 = ANY(%s)
ON CONFLICT (sha256) DO NOTHING
"""

_INSERT_REVIEW_SQL = """
INSERT INTO webapp.detection_reviews
    (object_id, verdict, corrected_bbox, corrected_type_id,
     reviewer_id, note)
VALUES (%s, %s, %s, %s, %s, %s)
RETURNING reviewed_at
"""

_INSERT_SAMPLE_SQL = """
INSERT INTO webapp.dataset_samples
    (object_id, verdict, model_id,
     type_id, class_name, confidence, bbox, area_ratio,
     final_type_id, final_class_name, final_bbox,
     frame_w, frame_h, detected_at, run_id, capture_id,
     original_sha, annotated_sha, reviewed_at, reviewer_id)
VALUES (%s, %s, %s,
        %s, %s, %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s)
"""


class _DetectionCtxRow:
    __slots__ = (
        "ctx", "confidence", "area_ratio", "ts", "run_id",
        "capture_id", "original_sha", "annotated_sha",
    )

    def __init__(self, row: Any) -> None:
        self.ctx = DetectionContext(
            object_id=row[0],
            model_id=row[1],
            model_task=row[2],
            type_id=row[3],
            class_name=row[4],
            bbox=tuple(row[5]) if row[5] is not None else None,
            frame_w=row[12],
            frame_h=row[13],
        )
        self.confidence = row[6]
        self.area_ratio = row[7]
        self.ts = row[8]
        self.run_id = row[9]
        self.capture_id = row[10]
        self.original_sha = row[11]
        self.annotated_sha = row[14]


async def _fetch_detection(conn: Any, object_id: int) -> _DetectionCtxRow:
    cur = await conn.execute(_DETECTION_CTX_SQL, (object_id,))
    row = await cur.fetchone()
    if row is None:
        raise _error(404, "detection_not_found", "Tespit bulunamadı.")
    return _DetectionCtxRow(row)


async def _model_types(conn: Any, model_id: str) -> dict[str, int]:
    cur = await conn.execute(
        "SELECT class_name, type_id FROM public.detection_types "
        "WHERE model_id = %s",
        (model_id,),
    )
    return {class_name: type_id for class_name, type_id in await cur.fetchall()}


def _normalize_or_http(
    det: _DetectionCtxRow, payload: ReviewIn | ReviewPatchIn,
    model_types: dict[str, int],
) -> NormalizedReview:
    if det.ctx.model_id not in KNOWN_MODELS:
        raise _error(
            422,
            "unsupported_model",
            f"'{det.ctx.model_id}' modeli dataset bölümlemesinde tanımlı "
            "değil; beşinci model bilinçli bir migration ister (§4.5).",
        )
    try:
        return normalize_review(
            det.ctx,
            verdict=payload.verdict,
            corrected_bbox=payload.corrected_bbox,
            corrected_class=payload.corrected_class,
            model_types=model_types,
        )
    except ReviewRuleError as exc:
        raise _error(400, exc.code, exc.message)


async def _copy_media(conn: Any, det: _DetectionCtxRow) -> None:
    shas = [sha for sha in (det.original_sha, det.annotated_sha) if sha]
    if shas:
        await conn.execute(_COPY_MEDIA_SQL, (shas,))


async def _insert_sample(
    conn: Any,
    det: _DetectionCtxRow,
    normalized: NormalizedReview,
    reviewed_at: datetime,
    reviewer_id: int,
) -> None:
    await conn.execute(
        _INSERT_SAMPLE_SQL,
        (
            det.ctx.object_id,
            normalized.verdict,
            det.ctx.model_id,
            det.ctx.type_id,
            det.ctx.class_name,
            det.confidence,
            list(det.ctx.bbox) if det.ctx.bbox is not None else None,
            det.area_ratio,
            normalized.final_type_id,
            normalized.final_class_name,
            list(normalized.final_bbox)
            if normalized.final_bbox is not None
            else None,
            det.ctx.frame_w,
            det.ctx.frame_h,
            det.ts,
            det.run_id,
            det.capture_id,
            det.original_sha,
            det.annotated_sha,
            reviewed_at,
            reviewer_id,
        ),
    )


def _review_body(
    det: _DetectionCtxRow,
    normalized: NormalizedReview,
    reviewed_at: datetime,
    reviewer_id: int,
    note: str | None,
) -> dict:
    return {
        "object_id": det.ctx.object_id,
        "verdict": normalized.verdict,
        "model_id": det.ctx.model_id,
        "corrected_bbox": list(normalized.corrected_bbox)
        if normalized.corrected_bbox is not None
        else None,
        "corrected_type_id": normalized.corrected_type_id,
        "final_type_id": normalized.final_type_id,
        "final_class_name": normalized.final_class_name,
        "reviewer_id": reviewer_id,
        "reviewed_at": reviewed_at,
        "note": note,
        "has_image_copy": det.original_sha is not None,
    }


@router.get("/verify/queue")
async def verify_queue(
    model_id: list[str] | None = Query(default=None),
    type_id: list[int] | None = Query(default=None),
    run_id: int | None = Query(default=None, ge=1),
    ts_from: datetime | None = Query(default=None),
    ts_to: datetime | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    only_with_image: bool = Query(default=False),
    cursor: str | None = Query(default=None),
    order: Literal["asc", "desc"] = Query(default="asc"),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> dict:
    """Karar bekleyenler; varsayılan sıra en eskiden yeniye (FIFO adaleti)."""

    await _require_archive_schema(conn)
    for value in (ts_from, ts_to):
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise _error(
                400, "timezone_required", "Zaman filtreleri UTC ofseti içermeli."
            )
    decoded_cursor = None
    if cursor is not None:
        try:
            decoded_cursor = decode_cursor(cursor)
        except ValueError:
            raise _error(400, "invalid_cursor", "Sayfa imleci çözülemedi.")

    filters = ArchiveFilters(
        model_ids=tuple(item for item in (model_id or ()) if item),
        type_ids=tuple(type_id or ()),
        review_statuses=("unreviewed",),
        run_id=run_id,
        ts_from=ts_from,
        ts_to=ts_to,
        min_confidence=min_confidence,
        only_with_image=only_with_image,
    )
    sql, params = build_list_query(
        filters, cursor=decoded_cursor, limit=limit, order=order
    )
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    has_more = len(rows) > limit
    records = [_detection_row(row) for row in rows[:limit]]
    next_cursor = None
    if has_more:
        last = records[-1]
        next_cursor = encode_cursor(last["ts"], last["id"])
    return {"records": records, "next_cursor": next_cursor}


async def _create_review(
    conn: Any, payload: ReviewIn, reviewer_id: int
) -> dict:
    """Tek kararı tam doğrulayıp tek transaction'da yazar."""

    from psycopg import errors

    try:
        async with conn.transaction():
            det = await _fetch_detection(conn, payload.object_id)
            model_types = await _model_types(conn, det.ctx.model_id)
            normalized = _normalize_or_http(det, payload, model_types)
            cur = await conn.execute(
                _INSERT_REVIEW_SQL,
                (
                    det.ctx.object_id,
                    normalized.verdict,
                    list(normalized.corrected_bbox)
                    if normalized.corrected_bbox is not None
                    else None,
                    normalized.corrected_type_id,
                    reviewer_id,
                    payload.note,
                ),
            )
            reviewed_at = (await cur.fetchone())[0]
            await _copy_media(conn, det)
            await _insert_sample(conn, det, normalized, reviewed_at, reviewer_id)
    except errors.UniqueViolation:
        raise _error(
            409,
            "already_reviewed",
            "Bu tespit için karar zaten verilmiş; değişiklik için PATCH "
            "kullanılır.",
        )
    return _review_body(det, normalized, reviewed_at, reviewer_id, payload.note)


@router.post("/reviews", status_code=201)
async def create_review(
    payload: ReviewIn,
    user: accounts.AuthContext = Depends(require_user),
    _csrf: None = Depends(require_csrf),
    conn: Any = Depends(get_connection),
) -> dict:
    await _require_archive_schema(conn)
    # Kimlik/CSRF bağımlılıklarının ve şema kontrolünün açtığı salt-okunur
    # transaction'ı kapat; _create_review kendi atomik transaction'ını açar.
    await conn.commit()
    return {"review": await _create_review(conn, payload, user.user.user_id)}


class BulkReviewIn(BaseModel):
    items: list[ReviewIn] = Field(min_length=1, max_length=100)


@router.post("/reviews/bulk")
async def create_reviews_bulk(
    payload: BulkReviewIn,
    user: accounts.AuthContext = Depends(require_user),
    _csrf: None = Depends(require_csrf),
    conn: Any = Depends(get_connection),
) -> dict:
    """Kısmi başarı raporlu toplu karar; her öğe kendi transaction'ıdır."""

    await _require_archive_schema(conn)
    # Her _create_review çağrısının üst-seviye ve birbirinden bağımsız bir
    # transaction olması gerekir; auth/şema okumalarının dış transaction'ını
    # önce kapatırız.
    await conn.commit()
    results = []
    ok_count = 0
    for item in payload.items:
        try:
            review = await _create_review(conn, item, user.user.user_id)
            results.append({"object_id": item.object_id, "status": "ok",
                            "verdict": review["verdict"]})
            ok_count += 1
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            results.append(
                {
                    "object_id": item.object_id,
                    "status": "error",
                    "code": detail.get("code", "error"),
                    "message": detail.get("message", ""),
                }
            )
    return {
        "results": results,
        "ok_count": ok_count,
        "error_count": len(results) - ok_count,
    }


@router.patch("/reviews/{object_id}")
async def change_review(
    object_id: int,
    payload: ReviewPatchIn,
    user: accounts.AuthContext = Depends(require_user),
    _csrf: None = Depends(require_csrf),
    conn: Any = Depends(get_connection),
) -> dict:
    """Karar değişikliği; yetki kararı verende ve yöneticilerdedir (§4.5)."""

    await _require_archive_schema(conn)
    await conn.commit()
    actor = user.user

    async with conn.transaction():
        cur = await conn.execute(
            "SELECT verdict, reviewer_id FROM webapp.detection_reviews "
            "WHERE object_id = %s FOR UPDATE",
            (object_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise _error(404, "review_not_found", "Bu tespit için karar yok.")
        old_verdict, owner_id = row
        if actor.role != "admin" and actor.user_id != owner_id:
            raise _error(
                403,
                "not_review_owner",
                "Kararı yalnız veren kullanıcı veya yönetici değiştirebilir.",
            )

        det = await _fetch_detection(conn, object_id)
        model_types = await _model_types(conn, det.ctx.model_id)
        review_in = ReviewIn(object_id=object_id, **payload.model_dump())
        normalized = _normalize_or_http(det, review_in, model_types)
        cur = await conn.execute(
            """
            UPDATE webapp.detection_reviews
            SET verdict = %s,
                corrected_bbox = %s,
                corrected_type_id = %s,
                note = %s,
                reviewer_id = %s,
                reviewed_at = now()
            WHERE object_id = %s
            RETURNING reviewed_at
            """,
            (
                normalized.verdict,
                list(normalized.corrected_bbox)
                if normalized.corrected_bbox is not None
                else None,
                normalized.corrected_type_id,
                payload.note,
                actor.user_id,
                object_id,
            ),
        )
        reviewed_at = (await cur.fetchone())[0]
        await _copy_media(conn, det)
        # verdict partition key: UPDATE satırı yeni bölüme otomatik taşır.
        await conn.execute(
            """
            UPDATE webapp.dataset_samples
            SET verdict = %s,
                final_type_id = %s,
                final_class_name = %s,
                final_bbox = %s,
                reviewed_at = %s,
                reviewer_id = %s
            WHERE object_id = %s
            """,
            (
                normalized.verdict,
                normalized.final_type_id,
                normalized.final_class_name,
                list(normalized.final_bbox)
                if normalized.final_bbox is not None
                else None,
                reviewed_at,
                actor.user_id,
                object_id,
            ),
        )
        await accounts.write_audit(
            conn,
            actor.user_id,
            "change_review",
            f"object:{object_id}",
            {"from": old_verdict, "to": normalized.verdict},
        )
    return {
        "review": _review_body(
            det, normalized, reviewed_at, actor.user_id, payload.note
        ),
        "previous_verdict": old_verdict,
    }
