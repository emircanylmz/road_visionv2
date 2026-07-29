"""RoadVision Web API giriş noktası (Faz 0 iskeleti).

Sözleşmeler (WEB_PLANI.md §3, §4.7):

* Açılışta webapp migration'ları advisory-lock altında bir kez uygulanır;
  ardından async bağlantı havuzu açılır. Migration senkron psycopg ile
  ``asyncio.to_thread`` içinde koşar — event loop bloklanmaz.
* Servis yalnız ``roadvision_web`` DSN'ini bilir; ``public`` şemasına tüm
  erişim salt-okunurdur.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .db import create_pool
from .migrations import CURRENT_VERSION, run_migrations

WEB_APP_VERSION = "0.1.0-faz0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.webapp_schema_version = await asyncio.to_thread(
        run_migrations, settings.dsn
    )
    pool = create_pool(settings)
    await pool.open(wait=True)
    app.state.pool = pool
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(
    title="RoadVision Web API",
    version=WEB_APP_VERSION,
    lifespan=lifespan,
)


@app.get("/")
async def root() -> dict:
    return {
        "service": "roadvision-web",
        "version": WEB_APP_VERSION,
        "plan": "WEB_PLANI.md",
    }


@app.get("/healthz")
async def healthz() -> dict:
    """DB erişimini ve iki şemanın sürümünü raporlar (Faz 0 kabul ucu)."""

    public_version: int | None = None
    async with app.state.pool.connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM public.schema_info"
                )
                row = await cur.fetchone()
                public_version = int(row[0]) if row else 0
            except Exception:
                # Masaüstü şeması henüz kurulmamış olabilir; sağlık ucu bu
                # durumda da ayakta kalır ve durumu null ile bildirir.
                await conn.rollback()
    return {
        "status": "ok",
        "webapp_schema_version": app.state.webapp_schema_version,
        "webapp_schema_expected": CURRENT_VERSION,
        "public_schema_version": public_version,
    }
