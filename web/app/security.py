"""Parola özetleme, oturum/CSRF belirteçleri ve çerez yardımcıları.

Sözleşmeler (WEB_PLANI.md §8):

* Parolalar Argon2id ile saklanır (argon2-cffi varsayılan parametreleri).
* Oturum çerezi HttpOnly + SameSite=Lax; CSRF çerezi JS tarafından okunup
  ``X-RoadVision-CSRF`` başlığında geri gönderilir (double-submit, oturum
  kaydındaki değere bağlı).
* ``argon2`` importu tembel tutulur; böylece bu paketi kurmayan test
  ortamları modülü yine import edebilir (migrations.py ile aynı desen).
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from functools import lru_cache
from typing import Any

SESSION_COOKIE = "rv_session"
CSRF_COOKIE = "rv_csrf"
CSRF_HEADER = "X-RoadVision-CSRF"
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200


def validate_new_password(password: str) -> str:
    """Kayıt ve yönetici oluşturma yollarında aynı parola sınırını uygular."""

    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Parola {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} karakter olmalı."
        )
    return password


@lru_cache(maxsize=1)
def _hasher() -> Any:
    from argon2 import PasswordHasher

    return PasswordHasher()


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    # Kullanıcı bulunamadığında da bir doğrulama koşarak yanıt süresini
    # gerçek doğrulamaya yaklaştırır (hesap varlığı zamanlamayla sızmasın).
    return _hasher().hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    from argon2.exceptions import InvalidHashError, VerifyMismatchError

    try:
        return bool(_hasher().verify(stored_hash, password))
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    from argon2.exceptions import InvalidHashError

    try:
        return bool(_hasher().check_needs_rehash(stored_hash))
    except InvalidHashError:
        return True


def dummy_verify(password: str) -> None:
    from argon2.exceptions import InvalidHashError, VerifyMismatchError

    try:
        _hasher().verify(_dummy_hash(), password)
    except (VerifyMismatchError, InvalidHashError):
        pass


def new_session_id() -> uuid.UUID:
    return uuid.uuid4()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(expected: str | None, provided: str | None) -> bool:
    """Sabit zamanlı karşılaştırma; eksik değerlerde False."""

    if not expected or not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def set_auth_cookies(
    response: Any,
    session_id: uuid.UUID,
    csrf_token: str,
    *,
    secure: bool,
    max_age_seconds: int,
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        str(session_id),
        max_age=max_age_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    # CSRF çerezi bilinçli olarak HttpOnly DEĞİLDİR: SPA değeri okuyup
    # durum değiştiren isteklerde başlıkta geri gönderir.
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age_seconds,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_auth_cookies(response: Any, *, secure: bool) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, samesite="lax")
