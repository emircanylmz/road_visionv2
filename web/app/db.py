"""psycopg3 async bağlantı havuzu ve FastAPI bağımlılığı."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from psycopg_pool import AsyncConnectionPool

from .config import Settings


def create_pool(settings: Settings) -> AsyncConnectionPool:
    """Havuzu kapalı oluşturur; ``lifespan`` açar ve kapatır.

    Havuzdaki bağlantılar ``roadvision_web`` rolüyle açılır: ``public``
    şeması salt-okunur, ``webapp`` şeması yazılabilirdir (Faz 0 kabulü
    ``web/scripts/verify_foundation.py`` ile doğrulanır).
    """

    return AsyncConnectionPool(
        settings.dsn,
        min_size=settings.pool_min,
        max_size=settings.pool_max,
        open=False,
        name="roadvision-web",
    )


async def get_connection(request: Request) -> AsyncIterator[Any]:
    """İstek başına havuzdan bağlantı veren FastAPI bağımlılığı."""

    pool: AsyncConnectionPool = request.app.state.pool
    async with pool.connection() as conn:
        yield conn
