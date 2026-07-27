"""PostgreSQL tespit arşivinin UI ve thread bağımsız sorgu katmanı.

Bu modül yalnız sorgu üretir ve verilen bağlantı üzerinde ``SELECT`` çalıştırır.
Transaction, retry ve bağlantı yaşam döngüsü çağıran servis katmanına aittir.
Sıralama ifadeleri kullanıcı girdisinden üretilmez; ``SortColumn`` değerleri
sabit SQL parçalarına eşlenir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


ARCHIVE_SCHEMA_VERSION = 3
ALLOWED_PAGE_SIZES = frozenset({25, 50, 100})


class ArchiveSchemaError(RuntimeError):
    """Bağlı veritabanı tespit arşivi için gereken şemayı sunmadığında."""


class SortColumn(str, Enum):
    TS = "ts"
    CONFIDENCE = "confidence"
    AREA_RATIO = "area_ratio"
    MODEL = "model"
    CLASS = "class"


@dataclass(frozen=True, slots=True)
class SortSpec:
    column: SortColumn = SortColumn.TS
    descending: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.column, SortColumn):
            object.__setattr__(self, "column", SortColumn(self.column))
        if not isinstance(self.descending, bool):
            raise TypeError("descending bool olmalıdır.")


@dataclass(frozen=True, slots=True)
class PageCursor:
    last_value: Any
    last_id: int
    last_is_null: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.last_id, bool) or not isinstance(self.last_id, int):
            raise TypeError("last_id tam sayı olmalıdır.")
        if self.last_id <= 0:
            raise ValueError("last_id pozitif olmalıdır.")
        if not isinstance(self.last_is_null, bool):
            raise TypeError("last_is_null bool olmalıdır.")
        if self.last_is_null and self.last_value is not None:
            raise ValueError("NULL imlecinde last_value None olmalıdır.")
        if not self.last_is_null and self.last_value is None:
            raise ValueError("Non-NULL imlecinde last_value dolu olmalıdır.")


@dataclass(frozen=True, slots=True)
class DetectionFilter:
    type_ids: frozenset[int] = field(default_factory=frozenset)
    ts_from: datetime | None = None
    ts_to: datetime | None = None
    min_confidence: float | None = None
    run_id: int | None = None
    only_with_image: bool = False

    def __post_init__(self) -> None:
        try:
            normalized_type_ids = frozenset(self.type_ids)
        except TypeError as exc:
            raise TypeError("type_ids yinelenebilir tam sayılardan oluşmalıdır.") from exc
        if any(
            isinstance(type_id, bool) or not isinstance(type_id, int) or type_id <= 0
            for type_id in normalized_type_ids
        ):
            raise ValueError("type_ids yalnız pozitif tam sayılar içermelidir.")
        object.__setattr__(self, "type_ids", normalized_type_ids)

        for name, value in (("ts_from", self.ts_from), ("ts_to", self.ts_to)):
            if value is not None and not _is_aware_datetime(value):
                raise ValueError(f"{name} timezone-aware datetime olmalıdır.")
        if (
            self.ts_from is not None
            and self.ts_to is not None
            and self.ts_from >= self.ts_to
        ):
            raise ValueError("ts_from, ts_to değerinden küçük olmalıdır.")

        if self.min_confidence is not None:
            confidence = float(self.min_confidence)
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise ValueError("min_confidence 0 ile 1 arasında olmalıdır.")
            object.__setattr__(self, "min_confidence", confidence)

        if self.run_id is not None:
            if isinstance(self.run_id, bool) or not isinstance(self.run_id, int):
                raise TypeError("run_id tam sayı olmalıdır.")
            if self.run_id < 0:
                raise ValueError("run_id negatif olamaz.")
        if not isinstance(self.only_with_image, bool):
            raise TypeError("only_with_image bool olmalıdır.")


@dataclass(frozen=True, slots=True)
class TypeNode:
    type_id: int
    model_id: str
    class_name: str
    display_name: str
    is_catalogued: bool


@dataclass(frozen=True, slots=True)
class ModelNode:
    model_id: str
    display_name: str
    task: str
    active: bool
    types: tuple[TypeNode, ...]


@dataclass(frozen=True, slots=True)
class DetectionRow:
    id: int
    ts: datetime
    run_id: int | None
    model_id: str
    model_display_name: str
    class_name: str
    display_name: str
    confidence: float | None
    area_ratio: float | None
    bbox: tuple[float, ...] | None
    capture_id: str | None
    is_catalogued: bool


@dataclass(frozen=True, slots=True)
class DetectionPage:
    rows: tuple[DetectionRow, ...]
    next_cursor: PageCursor | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class TypeCount:
    type_id: int
    count: int


@dataclass(frozen=True, slots=True)
class _SortSql:
    expression: str
    parameter_cast: str
    nullable: bool


_SORT_SQL: dict[SortColumn, _SortSql] = {
    SortColumn.TS: _SortSql("o.ts", "timestamptz", False),
    SortColumn.CONFIDENCE: _SortSql("o.confidence", "real", True),
    SortColumn.AREA_RATIO: _SortSql("o.area_ratio", "real", True),
    SortColumn.MODEL: _SortSql(
        "COALESCE(m.display_name, o.model_id)",
        "text",
        False,
    ),
    SortColumn.CLASS: _SortSql("t.display_name", "text", False),
}


_BASE_FROM_SQL = """
FROM detected_objects AS o
JOIN detection_events AS e
    ON e.id = o.event_id
JOIN detection_types AS t
    ON t.type_id = o.type_id
LEFT JOIN roadvision_model_catalog AS m
    ON m.model_id = o.model_id
LEFT JOIN media_captures AS mc
    ON mc.capture_id = e.capture_id
"""


def _is_aware_datetime(value: object) -> bool:
    if not isinstance(value, datetime):
        return False
    try:
        return value.utcoffset() is not None
    except (OverflowError, ValueError):
        return False


def check_archive_capability(conn: Any) -> int:
    """Şema sürümünü ve arşivin zorunlu tablolarını doğrular.

    Fonksiyon bağlantıda transaction açmaz veya kapatmaz. Başarılı olduğunda
    bulunan şema sürümünü döndürür.
    """

    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('schema_info') IS NOT NULL")
        row = cur.fetchone()
        if row is None or not bool(row[0]):
            raise ArchiveSchemaError("RoadVision schema_info tablosu bulunamadı.")

        cur.execute("SELECT COALESCE(MAX(version), 0) FROM schema_info")
        row = cur.fetchone()
        version = int(row[0]) if row is not None else 0
        if version < ARCHIVE_SCHEMA_VERSION:
            raise ArchiveSchemaError(
                "Tespit arşivi PostgreSQL şema sürümü 3 gerektirir; "
                f"bulunan sürüm: {version}."
            )

        cur.execute(
            """
            SELECT
                to_regclass('detected_objects') IS NOT NULL,
                to_regclass('detection_events') IS NOT NULL,
                to_regclass('detection_types') IS NOT NULL,
                to_regclass('roadvision_model_catalog') IS NOT NULL,
                to_regclass('media_captures') IS NOT NULL
            """
        )
        row = cur.fetchone()
        if row is None or len(row) != 5 or not all(bool(value) for value in row):
            raise ArchiveSchemaError(
                "PostgreSQL şema sürümü 3 görünüyor ancak arşiv tabloları eksik."
            )
    return version


def check_archive_schema(conn: Any) -> int:
    """Fetcher sözleşmesindeki şema kontrolü adı için açık yönlendirme."""

    return check_archive_capability(conn)


def build_where(
    flt: DetectionFilter,
    *,
    include_type_ids: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Liste ve facet sayımları için ortak, tamamen parametreli WHERE üretir."""

    clauses: list[str] = []
    params: dict[str, Any] = {}

    if include_type_ids:
        if flt.type_ids:
            clauses.append("o.type_id = ANY(%(type_ids)s::integer[])")
            # psycopg array adaptörü frozenset değil list bekler.
            params["type_ids"] = sorted(flt.type_ids)
        else:
            clauses.append("FALSE")

    if flt.ts_from is not None:
        clauses.append("o.ts >= %(ts_from)s::timestamptz")
        params["ts_from"] = flt.ts_from
    if flt.ts_to is not None:
        clauses.append("o.ts < %(ts_to)s::timestamptz")
        params["ts_to"] = flt.ts_to
    if flt.min_confidence is not None:
        clauses.append("o.confidence >= %(min_confidence)s::real")
        params["min_confidence"] = flt.min_confidence
    if flt.run_id is not None:
        clauses.append("o.run_id = %(run_id)s::integer")
        params["run_id"] = flt.run_id
    if flt.only_with_image:
        clauses.append("mc.capture_id IS NOT NULL")

    if not clauses:
        return "", params
    return "WHERE " + "\n  AND ".join(clauses), params


def fetch_type_tree(conn: Any) -> tuple[ModelNode, ...]:
    """Katalogdaki ve yalnız çalışma zamanında görülmüş modelleri döndürür."""

    with conn.cursor() as cur:
        cur.execute(
            """
            WITH archive_models AS (
                SELECT
                    m.model_id,
                    m.display_name,
                    m.task,
                    m.active
                FROM roadvision_model_catalog AS m

                UNION ALL

                SELECT DISTINCT
                    t.model_id,
                    t.model_id AS display_name,
                    'unknown'::text AS task,
                    FALSE AS active
                FROM detection_types AS t
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM roadvision_model_catalog AS m
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
            JOIN detection_types AS t
                ON t.model_id = am.model_id
            ORDER BY
                am.active DESC,
                am.display_name,
                am.model_id,
                t.class_index NULLS LAST,
                t.class_name,
                t.type_id
            """
        )
        rows = cur.fetchall()

    models: list[ModelNode] = []
    current_identity: tuple[str, str, str, bool] | None = None
    current_types: list[TypeNode] = []

    def append_current() -> None:
        if current_identity is None:
            return
        model_id, display_name, task, active = current_identity
        models.append(
            ModelNode(
                model_id=model_id,
                display_name=display_name,
                task=task,
                active=active,
                types=tuple(current_types),
            )
        )

    for row in rows:
        identity = (str(row[0]), str(row[1]), str(row[2]), bool(row[3]))
        if current_identity is not None and identity != current_identity:
            append_current()
            current_types = []
        current_identity = identity
        current_types.append(
            TypeNode(
                type_id=int(row[4]),
                model_id=identity[0],
                class_name=str(row[5]),
                display_name=str(row[6]),
                is_catalogued=bool(row[7]),
            )
        )
    append_current()
    return tuple(models)


def fetch_detections(
    conn: Any,
    flt: DetectionFilter,
    sort: SortSpec,
    cursor: PageCursor | None,
    page_size: int,
) -> DetectionPage:
    """Filtrelenmiş tekil tespitlerin bir keyset sayfasını döndürür."""

    _validate_page_size(page_size)
    if not flt.type_ids:
        return DetectionPage(rows=(), next_cursor=None, has_more=False)

    where_sql, params = build_where(flt)
    sort_sql = _SORT_SQL[sort.column]
    if cursor is not None:
        cursor_sql, cursor_params = _build_cursor_clause(sort_sql, sort, cursor)
        where_sql += "\n  AND " + cursor_sql
        params.update(cursor_params)

    direction = "DESC" if sort.descending else "ASC"
    null_order = " NULLS LAST" if sort_sql.nullable else ""
    params["page_limit"] = page_size + 1

    sql = f"""
        SELECT
            o.id,
            o.ts,
            o.run_id,
            o.model_id,
            COALESCE(m.display_name, o.model_id) AS model_display_name,
            o.class_name,
            t.display_name,
            o.confidence,
            o.area_ratio,
            o.bbox,
            mc.capture_id,
            t.is_catalogued
        {_BASE_FROM_SQL}
        {where_sql}
        ORDER BY
            {sort_sql.expression} {direction}{null_order},
            o.id {direction}
        LIMIT %(page_limit)s::integer
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)
        raw_rows = cur.fetchall()

    has_more = len(raw_rows) > page_size
    visible_rows = raw_rows[:page_size]
    rows = tuple(_detection_row(row) for row in visible_rows)
    next_cursor = (
        _cursor_for_row(rows[-1], sort)
        if has_more and rows
        else None
    )
    return DetectionPage(rows=rows, next_cursor=next_cursor, has_more=has_more)


def fetch_type_counts(
    conn: Any,
    flt: DetectionFilter,
) -> tuple[TypeCount, ...]:
    """Tür facet'i dışındaki filtreler için tür bazında kesin sayımları döndürür."""

    where_sql, params = build_where(flt, include_type_ids=False)
    sql = f"""
        SELECT
            o.type_id,
            count(*)::bigint AS detection_count
        {_BASE_FROM_SQL}
        {where_sql}
        GROUP BY o.type_id
        ORDER BY o.type_id
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return tuple(TypeCount(type_id=int(row[0]), count=int(row[1])) for row in rows)


def _build_cursor_clause(
    sort_sql: _SortSql,
    sort: SortSpec,
    cursor: PageCursor,
) -> tuple[str, dict[str, Any]]:
    operator = "<" if sort.descending else ">"
    params: dict[str, Any] = {"cursor_id": cursor.last_id}
    expression = sort_sql.expression

    if cursor.last_is_null:
        if not sort_sql.nullable:
            raise ValueError(f"{sort.column.value} sıralaması NULL imleci kabul etmez.")
        return (
            f"{expression} IS NULL "
            f"AND o.id {operator} %(cursor_id)s::bigint",
            params,
        )

    params["cursor_value"] = cursor.last_value
    value = f"%(cursor_value)s::{sort_sql.parameter_cast}"
    non_null_comparison = (
        f"{expression} {operator} {value} "
        f"OR ({expression} = {value} "
        f"AND o.id {operator} %(cursor_id)s::bigint)"
    )
    if not sort_sql.nullable:
        return f"({non_null_comparison})", params
    return (
        f"(({expression} IS NOT NULL AND ({non_null_comparison})) "
        f"OR {expression} IS NULL)",
        params,
    )


def _detection_row(row: Any) -> DetectionRow:
    bbox = row[9]
    return DetectionRow(
        id=int(row[0]),
        ts=row[1],
        run_id=int(row[2]) if row[2] is not None else None,
        model_id=str(row[3]),
        model_display_name=str(row[4]),
        class_name=str(row[5]),
        display_name=str(row[6]),
        confidence=float(row[7]) if row[7] is not None else None,
        area_ratio=float(row[8]) if row[8] is not None else None,
        bbox=tuple(float(value) for value in bbox) if bbox is not None else None,
        capture_id=str(row[10]) if row[10] is not None else None,
        is_catalogued=bool(row[11]),
    )


def _cursor_for_row(row: DetectionRow, sort: SortSpec) -> PageCursor:
    values: dict[SortColumn, Any] = {
        SortColumn.TS: row.ts,
        SortColumn.CONFIDENCE: row.confidence,
        SortColumn.AREA_RATIO: row.area_ratio,
        SortColumn.MODEL: row.model_display_name,
        SortColumn.CLASS: row.display_name,
    }
    value = values[sort.column]
    return PageCursor(
        last_value=value,
        last_id=row.id,
        last_is_null=value is None,
    )


def _validate_page_size(page_size: int) -> None:
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or page_size not in ALLOWED_PAGE_SIZES
    ):
        allowed = ", ".join(str(value) for value in sorted(ALLOWED_PAGE_SIZES))
        raise ValueError(f"page_size yalnız {allowed} olabilir.")
