#!/usr/bin/env python3
"""İlk yöneticiyi oluşturur veya mevcut kullanıcıyı yöneticiye yükseltir.

Parola komut satırına YAZILMAZ (süreç listesi/kabuk geçmişi); etkileşimli
``getpass`` ile iki kez sorulur ya da otomasyon için
``ROADVISION_ADMIN_PASSWORD`` ortam değişkeninden okunur. Bağlantı,
``webapp`` şemasının sahibi olan ``roadvision_web`` rolünün DSN'ini
kullanır (``ROADVISION_WEB_DSN``).

Örnekler:

    ROADVISION_WEB_DSN=... python3 web/scripts/create_admin.py \
        admin@kurum.tr --full-name "Saha Yöneticisi"

    python3 web/scripts/create_admin.py mevcut@kurum.tr --promote
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from email_validator import EmailNotValidError, validate_email

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.migrations import ensure_webapp_schema  # noqa: E402
from app.security import hash_password, validate_new_password  # noqa: E402


def _read_password() -> str:
    from_env = os.environ.get("ROADVISION_ADMIN_PASSWORD")
    if from_env:
        password = from_env
    else:
        first = getpass.getpass("Yönetici parolası: ")
        second = getpass.getpass("Parola (tekrar): ")
        if first != second:
            raise SystemExit("Parolalar eşleşmedi.")
        password = first
    try:
        return validate_new_password(password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", help="Yönetici e-posta adresi")
    parser.add_argument(
        "--full-name",
        default="RoadVision Yöneticisi",
        help="Görünen ad (yeni kayıt için)",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="E-posta zaten kayıtlıysa hesabı admin+approved yap",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help=(
            "webapp sahibinin DSN'i. Parolalı DSN komut satırında süreç "
            "listesi ve kabuk geçmişinde görünür; ROADVISION_WEB_DSN ortam "
            "değişkeni önerilir."
        ),
    )
    args = parser.parse_args()

    dsn = args.dsn or os.environ.get("ROADVISION_WEB_DSN")
    if not dsn:
        print("ROADVISION_WEB_DSN tanımlı değil.", file=sys.stderr)
        return 1

    try:
        import psycopg
    except ImportError:
        print("psycopg kurulu değil: pip install -r web/requirements.txt")
        return 1

    try:
        email = validate_email(
            args.email, check_deliverability=False
        ).normalized
    except EmailNotValidError as exc:
        print(f"Geçersiz yönetici e-postası: {exc}", file=sys.stderr)
        return 1

    with psycopg.connect(dsn) as conn:
        ensure_webapp_schema(conn)
        with conn.transaction():
            cur = conn.execute(
                "SELECT user_id, role, status FROM webapp.users "
                "WHERE lower(email) = lower(%s)",
                (email,),
            )
            row = cur.fetchone()
            if row is not None:
                user_id, role, status = row
                if not args.promote:
                    print(
                        f"{email} zaten kayıtlı (role={role}, "
                        f"status={status}). Yükseltmek için --promote kullanın.",
                        file=sys.stderr,
                    )
                    return 1
                conn.execute(
                    """
                    UPDATE webapp.users
                    SET role = 'admin', status = 'approved',
                        approved_at = COALESCE(approved_at, now()),
                        approved_by = COALESCE(approved_by, user_id)
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                print(f"{email} yöneticiye yükseltildi (user_id={user_id}).")
                return 0

            password_hash = hash_password(_read_password())
            cur = conn.execute(
                """
                INSERT INTO webapp.users
                    (email, full_name, password_hash, role, status, approved_at)
                VALUES (%s, %s, %s, 'admin', 'approved', now())
                RETURNING user_id
                """,
                (email, args.full_name, password_hash),
            )
            user_id = cur.fetchone()[0]
            conn.execute(
                "UPDATE webapp.users SET approved_by = user_id WHERE user_id = %s",
                (user_id,),
            )
    print(f"Yönetici oluşturuldu: {email} (user_id={user_id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
