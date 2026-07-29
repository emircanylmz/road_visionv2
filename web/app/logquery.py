"""public.log_records için saf sorgu üreticisi ve keyset imleç kodeki.

Masaüstündeki ``roadvision.archive`` disiplininin web karşılığıdır: filtre
ve imleç SQL'i Tk'den/FastAPI'den bağımsız, DB'siz test edilebilir saf
fonksiyonlarda üretilir (WEB_PLANI.md §6). Sayfalama OFFSET değil,
``(ts, id)`` keyset imlecidir; sıralama allowlist'lidir.

Sorgular yalnız SELECT üretir ve mevcut ``idx_log_records_ts`` /
``idx_log_records_level_ts`` / ``idx_log_records_category_ts`` indekslerini
kullanır; ``public`` şemasına hiçbir yazım yoktur.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

ALLOWED_LEVELS = ("debug", "info", "warning", "error")
ALLOWED_CATEGORIES = ("app", "detection")
_ALLOWED_ORDERS = ("asc", "desc")

MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 100

# payload kolonu NOT NULL DEFAULT '{}' olduğundan "ayrıntı var mı" kontrolü
# boş nesneyle karşılaştırmadır; liste yanıtı payload gövdesini taşımaz.
_LIST_COLUMNS = (
    "id, ts, level, category, message, run_id, model_id, "
    "(payload <> '{}'::jsonb) AS has_payload"
)

DETAIL_SQL = (
    "SELECT id, ts, level, category, message, run_id, model_id, payload "
    "FROM public.log_records WHERE id = %s"
)


@dataclass(frozen=True, slots=True)
class LogFilters:
    """Uygulanacak filtre kümesi; boş alan filtre yok demektir."""

    levels: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    model_ids: tuple[str, ...] = ()
    run_id: int | None = None
    ts_from: datetime | None = None
    ts_to: datetime | None = None


def encode_cursor(ts: datetime, record_id: int) -> str:
    """(ts, id) çiftini URL-güvenli opak imlece çevirir."""

    raw = json.dumps({"ts": ts.isoformat(), "id": int(record_id)})
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    """Opak imleci çözer; her türlü bozuk girdide ``ValueError`` üretir."""

    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or type(data.get("id")) is not int:
            raise ValueError("geçersiz imleç alanları")
        ts = datetime.fromisoformat(data["ts"])
        record_id = data["id"]
        if ts.tzinfo is None or ts.utcoffset() is None or record_id < 1:
            raise ValueError("geçersiz imleç alanları")
    except (
        binascii.Error,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError("geçersiz imleç") from exc
    return ts, record_id


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_PAGE_SIZE))


def build_list_query(
    filters: LogFilters,
    *,
    cursor: tuple[datetime, int] | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    order: str = "desc",
) -> tuple[str, list[Any]]:
    """Liste SQL'i ve parametrelerini üretir.

    ``order`` allowlist dışındaysa ``ValueError``; imleç yön karşılaştırması
    sıralamayla tutarlıdır (desc → ``<``, asc → ``>``), böylece sayfalar
    tekrar veya atlama üretmez.
    """

    if order not in _ALLOWED_ORDERS:
        raise ValueError(f"geçersiz sıralama: {order!r}")

    where: list[str] = []
    params: list[Any] = []

    if filters.levels:
        where.append("level = ANY(%s)")
        params.append(list(filters.levels))
    if filters.categories:
        where.append("category = ANY(%s)")
        params.append(list(filters.categories))
    if filters.model_ids:
        where.append("model_id = ANY(%s)")
        params.append(list(filters.model_ids))
    if filters.run_id is not None:
        where.append("run_id = %s")
        params.append(filters.run_id)
    if filters.ts_from is not None:
        where.append("ts >= %s")
        params.append(filters.ts_from)
    if filters.ts_to is not None:
        where.append("ts < %s")
        params.append(filters.ts_to)
    if cursor is not None:
        comparator = "<" if order == "desc" else ">"
        where.append(f"(ts, id) {comparator} (%s, %s)")
        params.extend(cursor)

    direction = "DESC" if order == "desc" else "ASC"
    sql = f"SELECT {_LIST_COLUMNS} FROM public.log_records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY ts {direction}, id {direction} LIMIT %s"
    # Bir fazla satır, tam dolu son sayfada sahte next_cursor üretmemeyi
    # sağlar. Route yalnız istenen sayıda kaydı döndürür.
    params.append(_clamp_limit(limit) + 1)
    return sql, params
