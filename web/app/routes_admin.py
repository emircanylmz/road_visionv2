"""Yönetici uçları: üyelik onay akışı, oturum yönetimi ve denetim listesi.

Faz 1 kabulü (WEB_PLANI.md §9): onay/ret/devre dışı bırakma işlemlerinin
tamamı ``webapp.admin_audit``e düşer ve durum değişikliği + oturum iptali +
audit kaydı tek transaction'da atomiktir. Durum geçişi WHERE koşulunda da
doğrulandığından iki yöneticinin yarışında yalnız biri kazanır; diğeri 409
``invalid_transition`` alır.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import accounts
from .accounts import InvalidTransition, transition_target
from .db import get_connection
from .deps import require_admin, require_admin_csrf

router = APIRouter(prefix="/api/admin", tags=["admin"])

_ACTION_AUDIT = {
    "approve": "approve_user",
    "reject": "reject_user",
    "disable": "disable_user",
}


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


@router.get("/users")
async def list_users(
    status: Literal["pending", "approved", "rejected", "disabled"] | None = None,
    _admin: accounts.AuthContext = Depends(require_admin),
    conn: Any = Depends(get_connection),
) -> dict:
    users = await accounts.list_users(conn, status)
    return {"users": [accounts.user_public(user) for user in users]}


async def _apply_action(
    action: Literal["approve", "reject", "disable"],
    user_id: int,
    admin: accounts.AuthContext,
    conn: Any,
) -> dict:
    target_user = await accounts.get_user(conn, user_id)
    if target_user is None:
        raise _error(404, "user_not_found", "Kullanıcı bulunamadı.")
    if action == "disable" and target_user.user_id == admin.user.user_id:
        raise _error(
            400, "self_disable", "Yönetici kendi hesabını devre dışı bırakamaz."
        )
    try:
        transition_target(action, target_user.status)
    except InvalidTransition as exc:
        raise _error(409, "invalid_transition", str(exc))

    async with conn.transaction():
        updated = await accounts.apply_user_action(
            conn, user_id, action, actor_id=admin.user.user_id
        )
        if updated is None:
            # Yarış: kontrol ile güncelleme arasında durum değişti.
            raise _error(
                409,
                "invalid_transition",
                "Kullanıcının durumu bu sırada değişti; listeyi yenileyin.",
            )
        detail: dict = {"email": updated.email, "new_status": updated.status}
        if action == "disable":
            detail["revoked_sessions"] = await accounts.revoke_user_sessions(
                conn, user_id
            )
        await accounts.write_audit(
            conn,
            actor_id=admin.user.user_id,
            action=_ACTION_AUDIT[action],
            target=f"user:{user_id}",
            detail=detail,
        )
    return {"user": accounts.user_public(updated)}


@router.post("/users/{user_id}/approve")
async def approve_user(
    user_id: int,
    admin: accounts.AuthContext = Depends(require_admin_csrf),
    conn: Any = Depends(get_connection),
) -> dict:
    return await _apply_action("approve", user_id, admin, conn)


@router.post("/users/{user_id}/reject")
async def reject_user(
    user_id: int,
    admin: accounts.AuthContext = Depends(require_admin_csrf),
    conn: Any = Depends(get_connection),
) -> dict:
    return await _apply_action("reject", user_id, admin, conn)


@router.post("/users/{user_id}/disable")
async def disable_user(
    user_id: int,
    admin: accounts.AuthContext = Depends(require_admin_csrf),
    conn: Any = Depends(get_connection),
) -> dict:
    return await _apply_action("disable", user_id, admin, conn)


@router.get("/sessions")
async def list_sessions(
    request: Request,
    _admin: accounts.AuthContext = Depends(require_admin),
    conn: Any = Depends(get_connection),
) -> dict:
    idle_seconds = request.app.state.settings.session_idle_minutes * 60
    return {
        "sessions": await accounts.list_active_sessions(
            conn, idle_seconds=idle_seconds
        )
    }


@router.delete("/sessions/{session_id}")
async def revoke_session(
    session_id: uuid.UUID,
    admin: accounts.AuthContext = Depends(require_admin_csrf),
    conn: Any = Depends(get_connection),
) -> dict:
    async with conn.transaction():
        deleted = await accounts.delete_session(conn, session_id)
        if not deleted:
            raise _error(404, "session_not_found", "Oturum bulunamadı.")
        await accounts.write_audit(
            conn,
            actor_id=admin.user.user_id,
            action="revoke_session",
            target=f"session:{session_id}",
        )
    return {"message": "Oturum iptal edildi."}


@router.get("/audit")
async def list_audit(
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
    _admin: accounts.AuthContext = Depends(require_admin),
    conn: Any = Depends(get_connection),
) -> dict:
    entries = await accounts.list_audit(conn, before_id=before_id, limit=limit)
    next_cursor = entries[-1]["audit_id"] if len(entries) == limit else None
    return {"entries": entries, "next_before_id": next_cursor}
