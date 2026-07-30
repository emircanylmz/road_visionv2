"""public tespit arşivi için saf sorgu üreticisi (WEB_PLANI.md §6, Faz 3).

Masaüstü ``roadvision/archive.py`` sözleşmesinin web karşılığıdır:

* Aynı FROM zinciri — ``detected_objects → detection_events →
  detection_types`` + katalog ve capture LEFT JOIN'leri — web tarafında tek
  ekle: ``webapp.detection_reviews`` LEFT JOIN'i. Satır yokluğu =
  **doğrulanmadı** (§4.3); ``review_status`` böylece SQL'de
  ``COALESCE(r.verdict, 'unreviewed')`` olarak türetilir.
* Filtreler tamamen parametreli; sıralama allowlist'lidir; sayfalama
  ``(o.ts, o.id)`` keyset imlecidir (imleç kodeki ``logquery`` ile ortak).
* Üretici LIMIT'e bir fazla satır ekler (Faz 2 sözleşmesi): tam dolu son
  sayfada sahte ``next_cursor`` üretilmez, route fazlalığı keser.

Masaüstünden bilinçli fark: masaüstü arayüzü tür seçimi boşken ``FALSE``
üretir (ağaçta hiçbir kutu işaretli değilse liste boştur); API'de ise
``type_id`` parametresinin yokluğu "tür filtresi yok" demektir.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .logquery import decode_cursor, encode_cursor  # ortak imleç kodeki

__all__ = [
    "ArchiveFilters",
    "REVIEW_STATUSES",
    "MAX_PAGE_SIZE",
    "DEFAULT_PAGE_SIZE",
    "SCHEMA_CHECK_SQL",
    "SCHEMA_VERSION_SQL",
    "TYPE_TREE_SQL",
    "TYPE_COUNTS_SQL",
    "CAPTURE_SQL",
    "CAPTURE_MODELS_SQL",
    "MEDIA_META_SQL",
    "MEDIA_DATA_SQL",
    "build_list_query",
    "decode_cursor",
    "encode_cursor",
]

REVIEW_STATUSES = ("unreviewed", "correct", "corrected", "wrong")
_ALLOWED_ORDERS = ("asc", "desc")

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 60

# Arşiv, masaüstü gibi şema v3 tablolarını ve type_id backfill'ini ister
# (db/roadvision_schema_v1_2_1.sql). Yetenek kontrolü UndefinedTable
# yakalamak yerine to_regclass kullanır ki transaction bozulmasın; ardından
# SCHEMA_VERSION_SQL gerçek sürüm kapısını doğrular.
SCHEMA_CHECK_SQL = """
SELECT to_regclass('public.schema_info') IS NOT NULL
   AND to_regclass('public.detected_objects') IS NOT NULL
   AND to_regclass('public.detection_events') IS NOT NULL
   AND to_regclass('public.detection_types') IS NOT NULL
   AND to_regclass('public.roadvision_model_catalog') IS NOT NULL
   AND to_regclass('public.media_captures') IS NOT NULL
   AND to_regclass('public.media_blobs') IS NOT NULL
   AND to_regclass('public.media_capture_models') IS NOT NULL
   AND EXISTS (
       SELECT 1 FROM information_schema.columns
       WHERE table_schema = 'public'
         AND table_name = 'detected_objects'
         AND column_name = 'type_id'
   )
"""

SCHEMA_VERSION_SQL = (
    "SELECT COALESCE(MAX(version), 0) FROM public.schema_info"
)

# Masaüstü _BASE_FROM_SQL + web'in doğrulama LEFT JOIN'i.
_BASE_FROM_SQL = """
FROM public.detected_objects AS o
JOIN public.detection_events AS e
    ON e.id = o.event_id
JOIN public.detection_types AS t
    ON t.type_id = o.type_id
LEFT JOIN public.roadvision_model_catalog AS m
    ON m.model_id = o.model_id
LEFT JOIN public.media_captures AS mc
    ON mc.capture_id = e.capture_id
LEFT JOIN webapp.detection_reviews AS r
    ON r.object_id = o.id
"""

_LIST_COLUMNS = """
    o.id,
    o.ts,
    o.run_id,
    o.model_id,
    COALESCE(m.display_name, o.model_id) AS model_display_name,
    o.type_id,
    o.class_name,
    t.display_name AS type_display_name,
    t.is_catalogued,
    o.confidence,
    o.area_ratio,
    o.bbox,
    mc.capture_id,
    mc.original_media_id,
    mc.annotated_media_id,
    COALESCE(r.verdict, 'unreviewed') AS review_status,
    r.reviewed_at
"""

# Model → tür ağacı: katalogdaki modeller + yalnız çalışma zamanında
# görülmüş modeller (masaüstü fetch_type_tree sözleşmesi).
TYPE_TREE_SQL = """
WITH archive_models AS (
    SELECT m.model_id, m.display_name, m.task, m.active
    FROM public.roadvision_model_catalog AS m

    UNION ALL

    SELECT DISTINCT
        t.model_id,
        t.model_id,
        'unknown'::text,
        FALSE
    FROM public.detection_types AS t
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.roadvision_model_catalog AS m
        WHERE m.model_id = t.model_id
    )
)
SELECT
    am.model_id,
    am.display_name,
    am.task,
    am.active,
    t.type_id,
    t.class_name,
    t.display_name,
    t.is_catalogued
FROM archive_models AS am
JOIN public.detection_types AS t
    ON t.model_id = am.model_id
ORDER BY
    am.active DESC,
    am.display_name,
    am.model_id,
    t.class_index NULLS LAST,
    t.class_name,
    t.type_id
"""

# Tür × karar sayımları; 'unreviewed' türetimi liste sorgusuyla aynıdır.
TYPE_COUNTS_SQL = """
SELECT
    o.type_id,
    COALESCE(r.verdict, 'unreviewed') AS review_status,
    count(*)::bigint
FROM public.detected_objects AS o
LEFT JOIN webapp.detection_reviews AS r
    ON r.object_id = o.id
GROUP BY 1, 2
"""

CAPTURE_SQL = """
SELECT
    mc.capture_id,
    mc.ts,
    mc.run_id,
    mc.source_name,
    mc.source_kind,
    mc.frame_sequence,
    mc.is_reprocess,
    ob.id, ob.mime, ob.width, ob.height, ob.byte_size,
    ab.id, ab.mime, ab.width, ab.height, ab.byte_size
FROM public.media_captures AS mc
JOIN public.media_blobs AS ob ON ob.id = mc.original_media_id
JOIN public.media_blobs AS ab ON ab.id = mc.annotated_media_id
WHERE mc.capture_id = %s::uuid
"""

CAPTURE_MODELS_SQL = """
SELECT model_id, object_count
FROM public.media_capture_models
WHERE capture_id = %s::uuid
ORDER BY model_id
"""

# Meta ve baytlar ayrı çekilir: ETag saklanan sha256 kolonudur ve panel her
# görüntüyü no-cache ile yeniden doğrulattığından 304 sıcak yoldur — blob
# baytlarını yalnız 200 dönecek istek DB'den taşır (routes_archive.get_media).
MEDIA_META_SQL = (
    "SELECT sha256, mime, byte_size "
    "FROM public.media_blobs WHERE id = %s"
)
MEDIA_DATA_SQL = "SELECT data FROM public.media_blobs WHERE id = %s"


@dataclass(frozen=True, slots=True)
class ArchiveFilters:
    """Uygulanacak filtre kümesi; boş alan filtre yok demektir."""

    model_ids: tuple[str, ...] = ()
    type_ids: tuple[int, ...] = ()
    review_statuses: tuple[str, ...] = ()
    run_id: int | None = None
    capture_id: str | None = None
    ts_from: datetime | None = None
    ts_to: datetime | None = None
    min_confidence: float | None = None
    only_with_image: bool = False


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_PAGE_SIZE))


def build_list_query(
    filters: ArchiveFilters,
    *,
    cursor: tuple[datetime, int] | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    order: str = "desc",
) -> tuple[str, list[Any]]:
    """Arşiv liste SQL'i ve parametrelerini üretir.

    ``review_statuses`` çok-seçimlidir ve OR ile bağlanır: ``unreviewed``
    "karar satırı yok" (``r.object_id IS NULL``), diğerleri ``r.verdict``
    eşleşmesidir. Allowlist dışı değer ``ValueError`` üretir.
    """

    if order not in _ALLOWED_ORDERS:
        raise ValueError(f"geçersiz sıralama: {order!r}")

    where: list[str] = []
    params: list[Any] = []

    if filters.model_ids:
        where.append("o.model_id = ANY(%s)")
        params.append(list(filters.model_ids))
    if filters.type_ids:
        where.append("o.type_id = ANY(%s)")
        params.append(list(filters.type_ids))
    if filters.review_statuses:
        parts: list[str] = []
        verdicts = []
        for status in filters.review_statuses:
            if status not in REVIEW_STATUSES:
                raise ValueError(f"geçersiz doğrulama durumu: {status!r}")
            if status == "unreviewed":
                parts.append("r.object_id IS NULL")
            else:
                verdicts.append(status)
        if verdicts:
            parts.append("r.verdict = ANY(%s)")
            params.append(verdicts)
        where.append("(" + " OR ".join(parts) + ")")
    if filters.run_id is not None:
        where.append("o.run_id = %s")
        params.append(filters.run_id)
    if filters.capture_id is not None:
        where.append("e.capture_id = %s::uuid")
        params.append(filters.capture_id)
    if filters.ts_from is not None:
        where.append("o.ts >= %s")
        params.append(filters.ts_from)
    if filters.ts_to is not None:
        where.append("o.ts < %s")
        params.append(filters.ts_to)
    if filters.min_confidence is not None:
        where.append("o.confidence >= %s")
        params.append(filters.min_confidence)
    if filters.only_with_image:
        where.append("mc.capture_id IS NOT NULL")
    if cursor is not None:
        comparator = "<" if order == "desc" else ">"
        where.append(f"(o.ts, o.id) {comparator} (%s, %s)")
        params.extend(cursor)

    direction = "DESC" if order == "desc" else "ASC"
    sql = f"SELECT {_LIST_COLUMNS} {_BASE_FROM_SQL}"
    if where:
        sql += " WHERE " + "\n  AND ".join(where)
    sql += f" ORDER BY o.ts {direction}, o.id {direction} LIMIT %s"
    # Faz 2 sözleşmesi: bir fazla satır iste, route fazlalığı kessin.
    params.append(_clamp_limit(limit) + 1)
    return sql, params
