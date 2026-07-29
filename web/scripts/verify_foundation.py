#!/usr/bin/env python3
"""Faz 0 kabul kontrolü (WEB_PLANI.md §9).

``ROADVISION_WEB_DSN`` ile bağlanıp şunları doğrular:

1. webapp migration runner'ı çalışır ve sürüm raporlar,
2. public şeması OKUNABİLİR,
3. public'e INSERT ve CREATE TABLE ``InsufficientPrivilege`` ile REDDEDİLİR,
4. webapp şemasına yazılabilir.

Çıkış kodu 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.migrations import ensure_webapp_schema  # noqa: E402

_FAILED = False


def _ok(message: str) -> None:
    print(f"  [OK]   {message}")


def _warn(message: str) -> None:
    print(f"  [UYARI] {message}")


def _fail(message: str) -> None:
    global _FAILED
    _FAILED = True
    print(f"  [HATA] {message}")


def _expect_denied(conn, errors, sql: str, label: str) -> None:
    """SQL'in InsufficientPrivilege ile reddedilmesini bekler."""

    try:
        with conn.transaction():
            conn.execute(sql)
    except errors.InsufficientPrivilege:
        _ok(f"{label}: izin doğru şekilde reddedildi")
        return
    except Exception as exc:  # Beklenmeyen hata sınıfı da rapor edilsin.
        _fail(f"{label}: beklenmeyen hata sınıfı: {type(exc).__name__}: {exc}")
        return
    _fail(f"{label}: sorgu ÇALIŞTI — roadvision_web public'e yazabiliyor!")


def main() -> int:
    dsn = os.environ.get("ROADVISION_WEB_DSN")
    if not dsn:
        print(
            "ROADVISION_WEB_DSN tanımlı değil. Örnek:\n"
            "  export ROADVISION_WEB_DSN="
            "postgresql://roadvision_web:...@127.0.0.1:5433/roadvision"
        )
        return 1

    try:
        import psycopg
        from psycopg import errors
    except ImportError:
        print("psycopg kurulu değil: pip install -r web/requirements.txt")
        return 1

    print("RoadVision Web — Faz 0 temel doğrulaması")
    with psycopg.connect(dsn) as conn:
        version = ensure_webapp_schema(conn)
        _ok(f"webapp şeması hazır, sürüm {version}")

        try:
            with conn.transaction():
                cur = conn.execute("SELECT count(*) FROM public.log_records")
                count = cur.fetchone()[0]
            _ok(f"public.log_records okunabildi ({count} satır)")
        except errors.UndefinedTable:
            _warn(
                "public.log_records yok — masaüstü şeması bu veritabanında "
                "henüz kurulmamış olabilir; okuma testi atlandı"
            )
        except errors.InsufficientPrivilege:
            _fail("public.log_records OKUNAMADI — SELECT yetkisi eksik")

        _expect_denied(
            conn,
            errors,
            "INSERT INTO public.log_records (ts, level, category, message) "
            "VALUES (now(), 'info', 'app', 'rv-web faz0 probe')",
            "public INSERT",
        )
        _expect_denied(
            conn,
            errors,
            "CREATE TABLE public._rv_web_probe (id integer)",
            "public CREATE TABLE",
        )

        with conn.transaction():
            conn.execute(
                "CREATE TABLE IF NOT EXISTS webapp._rv_probe (id integer)"
            )
            conn.execute("DROP TABLE webapp._rv_probe")
        _ok("webapp şemasına yazılabiliyor")

    print("SONUÇ: " + ("FAIL" if _FAILED else "PASS"))
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
