"""RoadVision Web API giriş noktası.

Sözleşmeler (WEB_PLANI.md §3, §4.7, §6):

* Açılışta webapp migration'ları advisory-lock altında bir kez uygulanır;
  ardından async bağlantı havuzu açılır. Migration senkron psycopg ile
  ``asyncio.to_thread`` içinde koşar — event loop bloklanmaz.
* Servis yalnız ``roadvision_web`` DSN'ini bilir; ``public`` şemasına tüm
  erişim salt-okunurdur.
* Hata gövdesi tek biçimdir: ``{"error": {"code", "message"}}``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import create_pool
from .migrations import CURRENT_VERSION, run_migrations
from .rate_limit import SlidingWindowLimiter
from .routes_admin import router as admin_router
from .routes_archive import router as archive_router
from .routes_auth import router as auth_router
from .routes_datasets import router as datasets_router
from .routes_logs import router as logs_router
from .routes_reviews import router as reviews_router

WEB_APP_VERSION = "0.6.0-faz5"

_HTTP_CODE_SLUGS = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.webapp_schema_version = await asyncio.to_thread(
        run_migrations, settings.dsn
    )
    app.state.login_limiter = SlidingWindowLimiter(
        max_events=settings.login_rate_per_minute, window_seconds=60.0
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
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(logs_router)
app.include_router(archive_router)
app.include_router(reviews_router)
app.include_router(datasets_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    """HTTPException'ı plan §6'daki hata gövdesine çevirir.

    Uçlar ``detail`` olarak ``{"code", "message"}`` sözlüğü verir; düz
    metin detaylar genel durum koduna eşlenir.
    """

    if isinstance(exc.detail, dict) and "code" in exc.detail:
        body = {"code": exc.detail["code"], "message": exc.detail.get("message", "")}
    else:
        body = {
            "code": _HTTP_CODE_SLUGS.get(exc.status_code, "error"),
            "message": str(exc.detail),
        }
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": body},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = first.get("msg", "Geçersiz istek gövdesi.")
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": f"{location}: {message}" if location else message,
            }
        },
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
