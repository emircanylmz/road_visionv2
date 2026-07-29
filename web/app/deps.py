"""FastAPI bağımlılıkları: oturum çözümleme, yetki ve CSRF kapıları.

Katmanlama (WEB_PLANI.md §8):

* ``get_auth`` — çerezden oturumu çözer, geçerliyse last_seen'i tazeler.
* ``require_user`` — onaylı (approved) oturum ister; oturum açıldıktan
  sonra devre dışı bırakılan kullanıcı, oturum iptaline ek olarak burada
  da (savunma derinliği) reddedilir.
* ``require_admin`` — approved + admin rolü.
* ``require_csrf`` / ``require_admin_csrf`` — durum değiştiren uçlarda
  ``X-RoadVision-CSRF`` başlığını oturum kaydındaki belirteçle karşılaştırır.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Depends, HTTPException, Request

from . import accounts, security
from .db import get_connection


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


def _parse_session_cookie(request: Request) -> uuid.UUID | None:
    raw = request.cookies.get(security.SESSION_COOKIE)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def get_auth(
    request: Request, conn: Any = Depends(get_connection)
) -> accounts.AuthContext | None:
    session_id = _parse_session_cookie(request)
    if session_id is None:
        return None
    settings = request.app.state.settings
    ctx = await accounts.get_auth_context(
        conn, session_id, idle_seconds=settings.session_idle_minutes * 60
    )
    if ctx is None:
        return None
    # Havuz, bağlantıyı iade ederken açık transaction'ı geri alır; bu yüzden
    # last_seen tazelemesi kendi transaction'ında commit edilir.
    async with conn.transaction():
        await accounts.touch_session(conn, session_id)
    return ctx


async def require_user(
    auth: accounts.AuthContext | None = Depends(get_auth),
) -> accounts.AuthContext:
    if auth is None:
        raise _error(401, "unauthorized", "Oturum bulunamadı veya süresi doldu.")
    if auth.user.status != "approved":
        raise _error(403, "account_inactive", "Hesabınız erişime kapalı.")
    return auth


async def require_admin(
    auth: accounts.AuthContext = Depends(require_user),
) -> accounts.AuthContext:
    if auth.user.role != "admin":
        raise _error(403, "admin_required", "Bu işlem yönetici yetkisi gerektirir.")
    return auth


async def require_csrf(
    request: Request,
    auth: accounts.AuthContext = Depends(require_user),
) -> accounts.AuthContext:
    provided = request.headers.get(security.CSRF_HEADER)
    if not security.csrf_matches(auth.session.csrf_token, provided):
        raise _error(
            403,
            "csrf_mismatch",
            f"{security.CSRF_HEADER} başlığı eksik veya oturumla eşleşmiyor.",
        )
    return auth


async def require_admin_csrf(
    auth: accounts.AuthContext = Depends(require_csrf),
) -> accounts.AuthContext:
    if auth.user.role != "admin":
        raise _error(403, "admin_required", "Bu işlem yönetici yetkisi gerektirir.")
    return auth
