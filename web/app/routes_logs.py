"""Log görüntüleyici uçları (WEB_PLANI.md §6, Faz 2).

``/api/logs`` masaüstü Oturum Günlüğü'nün kalıcı karşılığını sunar:
seviye/kategori/model/run/zaman filtreli, ``(ts, id)`` keyset sayfalı,
varsayılan yeniden-eskiye liste. Liste yanıtı ``payload`` gövdesini
taşımaz; ayrıntı ``/api/logs/{id}`` ile alınır. ``/api/meta/models``,
şema v3'teki ``roadvision_model_catalog`` referansını okur ve arayüzdeki
model filtresini besler. Tüm uçlar onaylı oturum ister ve yalnız SELECT
çalıştırır.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from . import accounts
from .db import get_connection
from .deps import require_user
from .logquery import (
    DEFAULT_PAGE_SIZE,
    DETAIL_SQL,
    MAX_PAGE_SIZE,
    LogFilters,
    build_list_query,
    decode_cursor,
    encode_cursor,
)

router = APIRouter(prefix="/api", tags=["logs"])

_Level = Literal["debug", "info", "warning", "error"]
_Category = Literal["app", "detection"]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


def _record_row(row: Any) -> dict:
    return {
        "id": row[0],
        "ts": row[1],
        "level": row[2],
        "category": row[3],
        "message": row[4],
        "run_id": row[5],
        "model_id": row[6],
        "has_payload": bool(row[7]),
    }


@router.get("/logs")
async def list_logs(
    level: list[_Level] | None = Query(default=None),
    category: list[_Category] | None = Query(default=None),
    model_id: list[str] | None = Query(default=None),
    run_id: int | None = Query(default=None, ge=1),
    ts_from: datetime | None = Query(default=None),
    ts_to: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None),
    order: Literal["asc", "desc"] = Query(default="desc"),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> dict:
    for value in (ts_from, ts_to):
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise _error(
                400,
                "timezone_required",
                "Zaman filtreleri UTC ofseti içermeli.",
            )
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

    filters = LogFilters(
        levels=tuple(level or ()),
        categories=tuple(category or ()),
        model_ids=tuple(item for item in (model_id or ()) if item),
        run_id=run_id,
        ts_from=ts_from,
        ts_to=ts_to,
    )
    sql, params = build_list_query(
        filters, cursor=decoded_cursor, limit=limit, order=order
    )
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    has_more = len(rows) > limit
    records = [_record_row(row) for row in rows[:limit]]
    next_cursor = None
    if has_more:
        last = records[-1]
        next_cursor = encode_cursor(last["ts"], last["id"])
    return {"records": records, "next_cursor": next_cursor}


@router.get("/logs/{record_id}")
async def get_log(
    record_id: int,
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> dict:
    cur = await conn.execute(DETAIL_SQL, (record_id,))
    row = await cur.fetchone()
    if row is None:
        raise _error(404, "log_not_found", "Günlük kaydı bulunamadı.")
    return {
        "record": {
            "id": row[0],
            "ts": row[1],
            "level": row[2],
            "category": row[3],
            "message": row[4],
            "run_id": row[5],
            "model_id": row[6],
            "payload": row[7],
        }
    }


@router.get("/meta/models")
async def list_models(
    _user: accounts.AuthContext = Depends(require_user),
    conn: Any = Depends(get_connection),
) -> dict:
    """Şema v3 model kataloğu; tablo yoksa boş liste döner.

    Masaüstü henüz şema 3'e migrate olmamış bir veritabanında arayüz model
    filtresiz çalışmaya devam eder; uç hata üretmez.
    """

    exists_cur = await conn.execute(
        "SELECT to_regclass('public.roadvision_model_catalog')"
    )
    if (await exists_cur.fetchone())[0] is None:
        return {"models": []}
    cur = await conn.execute(
        """
        SELECT model_id, display_name, task, input_size, active
        FROM public.roadvision_model_catalog
        ORDER BY model_id
        """
    )
    rows = await cur.fetchall()
    return {
        "models": [
            {
                "model_id": row[0],
                "display_name": row[1],
                "task": row[2],
                "input_size": row[3],
                "active": row[4],
            }
            for row in rows
        ]
    }
