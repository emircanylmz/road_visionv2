"""Kimlik uçları: kayıt, giriş, çıkış, aktif kullanıcı (WEB_PLANI.md §6).

Kayıt herkese açıktır ve kullanıcıyı ``pending`` oluşturur; giriş yalnız
``approved`` durumunda kabul edilir. Yanlış parola her koşulda aynı 401'i
döndürür; hesabın var olup olmadığı ancak doğru kimlik bilgisiyle (403
``pending_approval``) ayırt edilebilir — hesap varlığı dışarı sızmaz.
Argon2 doğrulaması CPU-yoğun olduğundan event loop'u bekletmemek için
``asyncio.to_thread`` içinde koşar.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field, field_validator

from . import accounts, security
from .db import get_connection
from .deps import require_csrf, require_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _error(status_code: int, code: str, message: str, **kwargs: Any) -> HTTPException:
    return HTTPException(
        status_code, detail={"code": code, "message": message}, **kwargs
    )


class RegisterIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(
        min_length=security.MIN_PASSWORD_LENGTH,
        max_length=security.MAX_PASSWORD_LENGTH,
    )

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("Ad soyad en az 2 görünür karakter içermeli.")
        return cleaned


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


@router.post("/register", status_code=201)
async def register(
    payload: RegisterIn,
    request: Request,
    conn: Any = Depends(get_connection),
) -> dict:
    from psycopg import errors

    # Oran sınırı Argon2'den ÖNCE koşar: uç kimliksizdir ve her deneme
    # CPU-yoğun bir özet üretir; sınırsız bırakmak hem işlemci tüketimine
    # (DoS) hem sınırsız 'pending' kayıt üretimine açıktır. Anahtar yalnız
    # IP'dir; e-posta anahtara katılmaz ki benzersiz adres üretmek sınırı
    # seyreltmesin.
    client_ip = request.client.host if request.client else None
    wait = request.app.state.register_limiter.check(client_ip or "?")
    if wait > 0:
        raise _error(
            429,
            "rate_limited",
            "Çok fazla kayıt denemesi; kısa bir süre sonra yeniden deneyin.",
            headers={"Retry-After": str(max(1, int(wait + 0.999)))},
        )

    password_hash = await asyncio.to_thread(security.hash_password, payload.password)
    try:
        async with conn.transaction():
            user = await accounts.create_user(
                conn, payload.email, payload.full_name, password_hash
            )
    except errors.UniqueViolation:
        raise _error(409, "email_taken", "Bu e-posta ile bir kayıt zaten var.")
    return {
        "user": accounts.user_public(user),
        "message": "Kayıt alındı; hesabınız yönetici onayından sonra açılacak.",
    }


@router.post("/login")
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    conn: Any = Depends(get_connection),
) -> dict:
    settings = request.app.state.settings
    client_ip = request.client.host if request.client else None
    ip_key = client_ip or "?"
    # IP+e-posta kovası hesap brute-force'unu, bu kaba IP tavanı ise her
    # denemede benzersiz e-posta kullanarak Argon2 CPU maliyetini sınırsız
    # çalıştırma girişimini sınırlar. İkisi de DB/parola doğrulamasından önce.
    wait = request.app.state.login_ip_limiter.check(ip_key)
    if wait > 0:
        raise _error(
            429,
            "rate_limited",
            "Çok fazla giriş denemesi; kısa bir süre sonra yeniden deneyin.",
            headers={"Retry-After": str(max(1, int(wait + 0.999)))},
        )
    limiter_key = (ip_key, payload.email.lower())
    wait = request.app.state.login_limiter.check(limiter_key)
    if wait > 0:
        raise _error(
            429,
            "rate_limited",
            "Çok fazla giriş denemesi; kısa bir süre sonra yeniden deneyin.",
            headers={"Retry-After": str(max(1, int(wait + 0.999)))},
        )

    found = await accounts.get_user_credentials(conn, payload.email)
    if found is None:
        # Zamanlama eşitlemesi: kullanıcı yokken de bir doğrulama koş.
        await asyncio.to_thread(security.dummy_verify, payload.password)
        raise _error(401, "invalid_credentials", "E-posta veya parola hatalı.")
    user, stored_hash = found

    ok = await asyncio.to_thread(security.verify_password, stored_hash, payload.password)
    if not ok:
        raise _error(401, "invalid_credentials", "E-posta veya parola hatalı.")

    if user.status != "approved":
        # pending/rejected/disabled tek mesajda toplanır (WEB_PLANI.md §7).
        raise _error(
            403,
            "pending_approval",
            "Hesabınız yönetici onayı bekliyor veya erişime kapalı.",
        )

    if security.password_needs_rehash(stored_hash):
        new_hash = await asyncio.to_thread(security.hash_password, payload.password)
        async with conn.transaction():
            await accounts.update_password_hash(conn, user.user_id, new_hash)

    session_id = security.new_session_id()
    csrf_token = security.new_csrf_token()
    ttl_seconds = settings.session_ttl_hours * 3600
    async with conn.transaction():
        # Fırsatçı temizlik: süresi geçmiş oturumlar giriş anında silinir.
        await accounts.purge_expired_sessions(conn)
        session = await accounts.create_session(
            conn,
            user.user_id,
            session_id,
            csrf_token,
            ttl_seconds,
            client_ip,
            request.headers.get("user-agent"),
        )

    security.set_auth_cookies(
        response,
        session.session_id,
        csrf_token,
        secure=settings.cookie_secure,
        max_age_seconds=ttl_seconds,
    )
    return {
        "user": accounts.user_public(user),
        "session_expires_at": session.expires_at,
        "csrf_token": csrf_token,
    }


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    auth: accounts.AuthContext = Depends(require_csrf),
    conn: Any = Depends(get_connection),
) -> dict:
    async with conn.transaction():
        await accounts.delete_session(conn, auth.session.session_id)
    security.clear_auth_cookies(
        response, secure=request.app.state.settings.cookie_secure
    )
    return {"message": "Oturum kapatıldı."}


@router.get("/me")
async def me(auth: accounts.AuthContext = Depends(require_user)) -> dict:
    return {
        "user": accounts.user_public(auth.user),
        "session_expires_at": auth.session.expires_at,
    }
