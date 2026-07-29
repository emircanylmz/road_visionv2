"""webapp kimlik tablolarına erişim ve durum-geçiş kuralları.

Masaüstündeki ``roadvision/db.py`` yaklaşımıyla aynıdır: fonksiyonlar açık
bir bağlantı alır ve **transaction yönetmez** — yazan uçlar transaction'ı
route katmanında ``async with conn.transaction():`` ile açar; böylece çok
adımlı işlemler (ör. devre dışı bırakma + oturum iptali + audit) atomik
kalır. Saf kurallar (durum geçişleri) DB'siz test edilebilsin diye ayrı
tutulur (WEB_PLANI.md Faz 1 kabulü).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

USER_STATUSES = ("pending", "approved", "rejected", "disabled")

# action → izin verilen mevcut durumlar. approve, reddedilmiş/devre dışı
# hesabı yeniden açmak için de kullanılır; reject yalnız bekleyen kayıtta,
# disable yalnız onaylı hesapta anlamlıdır.
_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "approve": ("pending", "rejected", "disabled"),
    "reject": ("pending",),
    "disable": ("approved",),
}
_ACTION_RESULT = {"approve": "approved", "reject": "rejected", "disable": "disabled"}


class InvalidTransition(ValueError):
    """Geçersiz durum geçişi (ör. onaylı kaydı reddetmek)."""


def transition_target(action: str, current_status: str) -> str:
    """Eylemin hedef durumunu döndürür; geçersizse InvalidTransition."""

    allowed = _TRANSITIONS.get(action)
    if allowed is None:
        raise InvalidTransition(f"bilinmeyen eylem: {action}")
    if current_status not in allowed:
        raise InvalidTransition(
            f"'{current_status}' durumundaki kullanıcıya '{action}' uygulanamaz"
        )
    return _ACTION_RESULT[action]


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: int
    email: str
    full_name: str
    role: str
    status: str
    created_at: datetime
    approved_at: datetime | None
    approved_by: int | None


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: uuid.UUID
    user_id: int
    csrf_token: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: UserRecord
    session: SessionRecord


_USER_COLS = (
    "user_id, email, full_name, role, status, created_at, approved_at, approved_by"
)


def user_from_row(row: Any) -> UserRecord:
    return UserRecord(*row)


def user_public(user: UserRecord) -> dict:
    """API yanıtlarında dönen alan kümesi (parola özeti asla dışarı çıkmaz)."""

    return {
        "user_id": user.user_id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "status": user.status,
        "created_at": user.created_at,
        "approved_at": user.approved_at,
    }


# --- kullanıcılar ---------------------------------------------------------


async def create_user(
    conn: Any, email: str, full_name: str, password_hash: str
) -> UserRecord:
    cur = await conn.execute(
        f"""
        INSERT INTO webapp.users (email, full_name, password_hash)
        VALUES (%s, %s, %s)
        RETURNING {_USER_COLS}
        """,
        (email, full_name, password_hash),
    )
    return user_from_row(await cur.fetchone())


async def get_user_credentials(
    conn: Any, email: str
) -> tuple[UserRecord, str] | None:
    cur = await conn.execute(
        f"""
        SELECT {_USER_COLS}, password_hash
        FROM webapp.users
        WHERE lower(email) = lower(%s)
        """,
        (email,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return user_from_row(row[:-1]), row[-1]


async def get_user(conn: Any, user_id: int) -> UserRecord | None:
    cur = await conn.execute(
        f"SELECT {_USER_COLS} FROM webapp.users WHERE user_id = %s",
        (user_id,),
    )
    row = await cur.fetchone()
    return user_from_row(row) if row is not None else None


async def list_users(conn: Any, status: str | None = None) -> list[UserRecord]:
    if status is None:
        cur = await conn.execute(
            f"SELECT {_USER_COLS} FROM webapp.users ORDER BY created_at, user_id"
        )
    else:
        cur = await conn.execute(
            f"""
            SELECT {_USER_COLS} FROM webapp.users
            WHERE status = %s ORDER BY created_at, user_id
            """,
            (status,),
        )
    return [user_from_row(row) for row in await cur.fetchall()]


async def apply_user_action(
    conn: Any, user_id: int, action: str, actor_id: int
) -> UserRecord | None:
    """Durum geçişini uygular; satır uygun durumda değilse None döner.

    WHERE koşulundaki durum listesi geçiş kuralını veritabanında da
    doğrular; iki yöneticinin aynı anda işlem yapması yarışında yalnız
    biri satırı günceller.
    """

    target = _ACTION_RESULT[action]
    allowed = list(_TRANSITIONS[action])
    if action == "approve":
        cur = await conn.execute(
            f"""
            UPDATE webapp.users
            SET status = %s, approved_at = now(), approved_by = %s
            WHERE user_id = %s AND status = ANY(%s)
            RETURNING {_USER_COLS}
            """,
            (target, actor_id, user_id, allowed),
        )
    else:
        cur = await conn.execute(
            f"""
            UPDATE webapp.users
            SET status = %s
            WHERE user_id = %s AND status = ANY(%s)
            RETURNING {_USER_COLS}
            """,
            (target, user_id, allowed),
        )
    row = await cur.fetchone()
    return user_from_row(row) if row is not None else None


async def update_password_hash(conn: Any, user_id: int, new_hash: str) -> None:
    await conn.execute(
        "UPDATE webapp.users SET password_hash = %s WHERE user_id = %s",
        (new_hash, user_id),
    )


# --- oturumlar ------------------------------------------------------------


async def create_session(
    conn: Any,
    user_id: int,
    session_id: uuid.UUID,
    csrf_token: str,
    ttl_seconds: int,
    ip: str | None,
    user_agent: str | None,
) -> SessionRecord:
    cur = await conn.execute(
        """
        INSERT INTO webapp.sessions
            (session_id, user_id, csrf_token, expires_at, ip, user_agent)
        VALUES (%s, %s, %s, now() + make_interval(secs => %s), %s::inet, %s)
        RETURNING session_id, user_id, csrf_token,
                  created_at, expires_at, last_seen_at
        """,
        (session_id, user_id, csrf_token, ttl_seconds, ip, user_agent),
    )
    return SessionRecord(*await cur.fetchone())


async def get_auth_context(
    conn: Any, session_id: uuid.UUID, idle_seconds: int
) -> AuthContext | None:
    """Süresi (mutlak + hareketsizlik) geçmemiş oturumu kullanıcısıyla verir."""

    cur = await conn.execute(
        f"""
        SELECT s.session_id, s.user_id, s.csrf_token,
               s.created_at, s.expires_at, s.last_seen_at,
               {", ".join("u." + col.strip() for col in _USER_COLS.split(","))}
        FROM webapp.sessions AS s
        JOIN webapp.users AS u USING (user_id)
        WHERE s.session_id = %s
          AND s.expires_at > now()
          AND s.last_seen_at > now() - make_interval(secs => %s)
        """,
        (session_id, idle_seconds),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return AuthContext(session=SessionRecord(*row[:6]), user=user_from_row(row[6:]))


async def touch_session(
    conn: Any, session_id: uuid.UUID, min_gap_seconds: int = 60
) -> None:
    """last_seen_at'i tazeler; yazım maliyetini sınırlamak için en fazla
    ``min_gap_seconds``te bir gerçek UPDATE üretir."""

    await conn.execute(
        """
        UPDATE webapp.sessions
        SET last_seen_at = now()
        WHERE session_id = %s
          AND last_seen_at < now() - make_interval(secs => %s)
        """,
        (session_id, min_gap_seconds),
    )


async def delete_session(conn: Any, session_id: uuid.UUID) -> bool:
    cur = await conn.execute(
        "DELETE FROM webapp.sessions WHERE session_id = %s RETURNING session_id",
        (session_id,),
    )
    return await cur.fetchone() is not None


async def revoke_user_sessions(conn: Any, user_id: int) -> int:
    cur = await conn.execute(
        "DELETE FROM webapp.sessions WHERE user_id = %s RETURNING session_id",
        (user_id,),
    )
    return len(await cur.fetchall())


async def purge_expired_sessions(conn: Any) -> int:
    cur = await conn.execute(
        "DELETE FROM webapp.sessions WHERE expires_at <= now() RETURNING session_id"
    )
    return len(await cur.fetchall())


async def list_active_sessions(
    conn: Any, idle_seconds: int, limit: int = 200
) -> list[dict]:
    cur = await conn.execute(
        """
        SELECT s.session_id, s.user_id, u.email, s.created_at,
               s.expires_at, s.last_seen_at, s.ip, s.user_agent
        FROM webapp.sessions AS s
        JOIN webapp.users AS u USING (user_id)
        WHERE s.expires_at > now()
          AND s.last_seen_at > now() - make_interval(secs => %s)
        ORDER BY s.created_at DESC
        LIMIT %s
        """,
        (idle_seconds, limit),
    )
    rows = await cur.fetchall()
    return [
        {
            "session_id": row[0],
            "user_id": row[1],
            "email": row[2],
            "created_at": row[3],
            "expires_at": row[4],
            "last_seen_at": row[5],
            "ip": str(row[6]) if row[6] is not None else None,
            "user_agent": row[7],
        }
        for row in rows
    ]


# --- denetim ---------------------------------------------------------------


async def write_audit(
    conn: Any, actor_id: int, action: str, target: str, detail: dict | None = None
) -> None:
    from psycopg.types.json import Jsonb

    await conn.execute(
        """
        INSERT INTO webapp.admin_audit (actor_id, action, target, detail)
        VALUES (%s, %s, %s, %s)
        """,
        (actor_id, action, target, Jsonb(detail) if detail is not None else None),
    )


async def list_audit(
    conn: Any, before_id: int | None = None, limit: int = 100
) -> list[dict]:
    """audit_id üzerinden keyset sayfalı, yeniden eskiye denetim listesi."""

    limit = max(1, min(int(limit), 500))
    if before_id is None:
        cur = await conn.execute(
            """
            SELECT a.audit_id, a.actor_id, u.email, a.action, a.target,
                   a.detail, a.created_at
            FROM webapp.admin_audit AS a
            JOIN webapp.users AS u ON u.user_id = a.actor_id
            ORDER BY a.audit_id DESC
            LIMIT %s
            """,
            (limit,),
        )
    else:
        cur = await conn.execute(
            """
            SELECT a.audit_id, a.actor_id, u.email, a.action, a.target,
                   a.detail, a.created_at
            FROM webapp.admin_audit AS a
            JOIN webapp.users AS u ON u.user_id = a.actor_id
            WHERE a.audit_id < %s
            ORDER BY a.audit_id DESC
            LIMIT %s
            """,
            (before_id, limit),
        )
    rows = await cur.fetchall()
    return [
        {
            "audit_id": row[0],
            "actor_id": row[1],
            "actor_email": row[2],
            "action": row[3],
            "target": row[4],
            "detail": row[5],
            "created_at": row[6],
        }
        for row in rows
    ]
