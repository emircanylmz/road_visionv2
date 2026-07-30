"""Tespit arşivi uçları (WEB_PLANI.md §6, Faz 3).

``/api/archive/*`` masaüstü Tespit Arşivi sözleşmesini web'e taşır;
``/api/captures/{id}`` ve ``/api/media/{id}`` görüntü katmanını sunar.
Medya yanıtı ``ETag: "sha256"`` taşır ve ``If-None-Match`` eşleşmesinde
gövdesiz 304 döner. ``Cache-Control: private, no-cache`` her kullanımda
oturumu yeniden doğrulatır; çıkış sonrası hassas görüntü yerel önbellekten
sunulmaz. Tüm uçlar onaylı oturum ister ve yalnız SELECT çalıştırır.
"""

from __future__ import annotations

import hashlib
import string
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from . import accounts
from .archivequery import (
    CAPTURE_MODELS_SQL,
    CAPTURE_SQL,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MEDIA_SQL,
    SCHEMA_CHECK_SQL,
    SCHEMA_VERSION_SQL,
    TYPE_COUNTS_SQL,
    TYPE_TREE_SQL,
    ArchiveFilters,
    build_list_query,
    decode_cursor,
    encode_cursor,
)
from .db import get_connection
from .deps import require_user

router = APIRouter(prefix="/api", tags=["archive"])

_ReviewStatus = Literal["unreviewed", "correct", "corrected", "wrong"]
_MEDIA_EXTENSIONS = {
    "image/avif": "avif",
    "image/gif": "gif",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


async def _require_archive_schema(conn: Any) -> None:
    """Masaüstü gibi şema v3 tablolarını şart koşar (archive.py sözleşmesi)."""

    cur = await conn.execute(SCHEMA_CHECK_SQL)
    row = await cur.fetchone()
    if row is None or not bool(row[0]):
        raise _error(
            409,
            "archive_unavailable",
            "Tespit arşivi PostgreSQL şema sürümü 3 gerektirir; masaüstü "
            "uygulama en az bir kez şema migration'ını çalıştırmış olmalı.",
        )
    cur = await conn.execute(SCHEMA_VERSION_SQL)
    row = await cur.fetchone()
    if row is None or int(row[0]) < 3:
        found = int(row[0]) if row is not None else 0
        raise _error(
            409,
            "archive_unavailable",
            "Tespit arşivi PostgreSQL şema sürümü 3 gerektirir; "
            f"bulunan sürüm: {found}.",
        )


def _require_aware(*values: datetime | None) -> None:
    for value in values:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise _error(
                400,
                "timezone_required",
                "Zaman filtreleri UTC ofseti içermeli.",
            )


def _detection_row(row: Any) -> dict:
    return {
        "id": row[0],
        "ts": row[1],
        "run_id": row[2],
        "model_id": row[3],
        "model_display_name": row[4],
        "type_id": row[5],
        "class_name": row[6],
        "type_display_name": row[7],
        "is_catalogued": bool(row[8]),
        "confidence": row[9],
        "area_ratio": row[10],
        "bbox": list(row[11]) if row[11] is not None else None,
        "capture_id": str(row[12]) if row[12] is not None else None,
        "original_media_id": row[13],
        "annotated_media_id": row[14],
        "review_status": row[15],
        "reviewed_at": row[16],
    }


@router.get("/archive/types")
async def archive_types(
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> dict:
    """Model → tür ağacı ve tür × doğrulama durumu sayımları."""

    await _require_archive_schema(conn)

    cur = await conn.execute(TYPE_COUNTS_SQL)
    counts: dict[int, dict[str, int]] = {}
    for type_id, review_status, count in await cur.fetchall():
        bucket = counts.setdefault(
            int(type_id),
            {
                "total": 0,
                "unreviewed": 0,
                "correct": 0,
                "corrected": 0,
                "wrong": 0,
            },
        )
        bucket[review_status] = int(count)
        bucket["total"] += int(count)

    cur = await conn.execute(TYPE_TREE_SQL)
    models: dict[str, dict] = {}
    for row in await cur.fetchall():
        model = models.setdefault(
            row[0],
            {
                "model_id": row[0],
                "display_name": row[1],
                "task": row[2],
                "active": row[3],
                "types": [],
            },
        )
        type_id = int(row[4])
        model["types"].append(
            {
                "type_id": type_id,
                "class_name": row[5],
                "display_name": row[6],
                "is_catalogued": bool(row[7]),
                "counts": counts.get(
                    type_id,
                    {
                        "total": 0,
                        "unreviewed": 0,
                        "correct": 0,
                        "corrected": 0,
                        "wrong": 0,
                    },
                ),
            }
        )
    return {"models": list(models.values())}


@router.get("/archive/detections")
async def archive_detections(
    model_id: list[str] | None = Query(default=None),
    type_id: list[int] | None = Query(default=None),
    review_status: list[_ReviewStatus] | None = Query(default=None),
    run_id: int | None = Query(default=None, ge=1),
    capture_id: uuid.UUID | None = Query(default=None),
    ts_from: datetime | None = Query(default=None),
    ts_to: datetime | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    only_with_image: bool = Query(default=False),
    cursor: str | None = Query(default=None),
    order: Literal["asc", "desc"] = Query(default="desc"),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> dict:
    await _require_archive_schema(conn)
    _require_aware(ts_from, ts_to)
    if ts_from is not None and ts_to is not None and ts_from >= ts_to:
        raise _error(
            400, "invalid_range", "Başlangıç zamanı bitişten önce olmalı."
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
        review_statuses=tuple(review_status or ()),
        run_id=run_id,
        capture_id=str(capture_id) if capture_id is not None else None,
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


@router.get("/captures/{capture_id}")
async def get_capture(
    capture_id: uuid.UUID,
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> dict:
    await _require_archive_schema(conn)
    cur = await conn.execute(CAPTURE_SQL, (str(capture_id),))
    row = await cur.fetchone()
    if row is None:
        raise _error(404, "capture_not_found", "Kare kaydı bulunamadı.")
    cur = await conn.execute(CAPTURE_MODELS_SQL, (str(capture_id),))
    model_rows = await cur.fetchall()
    return {
        "capture": {
            "capture_id": str(row[0]),
            "ts": row[1],
            "run_id": row[2],
            "source_name": row[3],
            "source_kind": row[4],
            "frame_sequence": row[5],
            "is_reprocess": bool(row[6]),
            "original": {
                "media_id": row[7],
                "mime": row[8],
                "width": row[9],
                "height": row[10],
                "byte_size": row[11],
            },
            "annotated": {
                "media_id": row[12],
                "mime": row[13],
                "width": row[14],
                "height": row[15],
                "byte_size": row[16],
            },
            "models": [
                {"model_id": model_id, "object_count": count}
                for model_id, count in model_rows
            ],
        }
    }


def _etag_matches(if_none_match: str, sha256: str) -> bool:
    if if_none_match.strip() == "*":
        return True
    for candidate in if_none_match.split(","):
        value = candidate.strip()
        if value.startswith("W/"):
            value = value[2:]
        if value.strip('"') == sha256:
            return True
    return False


def _media_response_parts(
    sha256: Any,
    mime: Any,
    byte_size: Any,
    data: Any,
) -> tuple[bytes, str, dict[str, str]]:
    """DB medya metadatasını güvenli bir HTTP yanıtına dönüştürür."""

    digest = str(sha256).lower()
    media_type = str(mime).lower()
    if (
        len(digest) != 64
        or any(character not in string.hexdigits for character in digest)
    ):
        raise _error(409, "media_corrupt", "Görüntü özeti geçersiz.")
    extension = _MEDIA_EXTENSIONS.get(media_type)
    if extension is None:
        raise _error(
            415,
            "media_type_unsupported",
            "Yalnız güvenli raster görüntü türleri sunulabilir.",
        )
    payload = bytes(data)
    size_matches = int(byte_size) == len(payload)
    digest_matches = hashlib.sha256(payload).hexdigest() == digest
    if not size_matches or not digest_matches:
        raise _error(409, "media_corrupt", "Görüntü bütünlük kontrolü başarısız.")
    headers = {
        "ETag": f'"{digest}"',
        # Hassas arşiv görüntüsü yerel önbellekte tutulabilir ancak her
        # kullanımda oturumla yeniden doğrulanır; çıkıştan sonra 304 alınamaz.
        "Cache-Control": "private, no-cache",
        "Content-Disposition": f'inline; filename="{digest[:16]}.{extension}"',
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
    }
    return payload, media_type, headers


@router.get("/media/{media_id}")
async def get_media(
    media_id: int,
    if_none_match: str | None = Header(default=None),
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> Response:
    await _require_archive_schema(conn)
    cur = await conn.execute(MEDIA_SQL, (media_id,))
    row = await cur.fetchone()
    if row is None:
        raise _error(404, "media_not_found", "Görüntü bulunamadı.")
    sha256, mime, byte_size, data = row
    payload, media_type, headers = _media_response_parts(
        sha256, mime, byte_size, data
    )
    response_digest = headers["ETag"].strip('"')
    if if_none_match is not None and _etag_matches(if_none_match, response_digest):
        return Response(status_code=304, headers=headers)
    headers["Content-Length"] = str(len(payload))
    return Response(content=payload, media_type=media_type, headers=headers)
