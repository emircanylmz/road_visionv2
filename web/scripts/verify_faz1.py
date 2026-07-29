#!/usr/bin/env python3
"""Faz 1 kabul kontrolü (WEB_PLANI.md §9): çalışan API'ye karşı uçtan uca.

Doğrulananlar:

1. Kayıt ``pending`` oluşturur; onaysız girişe 403 ``pending_approval`` döner.
2. Oturumsuz istek korumalı uçlara erişemez (401); üye, admin uçlarına
   erişemez (403).
3. Yönetici onayı sonrası giriş açılır; onay/ret ``admin_audit``e düşer.
4. CSRF başlığı olmayan durum-değiştiren istek 403 ile reddedilir.
5. Devre dışı bırakma, kullanıcının açık oturumunu anında geçersiz kılar.

Gerekli ortam: ``ROADVISION_WEB_ADMIN_EMAIL`` ve
``ROADVISION_WEB_ADMIN_PASSWORD`` (create_admin.py ile açılmış hesap).
İsteğe bağlı: ``ROADVISION_WEB_URL`` (varsayılan http://127.0.0.1:8800),
temizlik için ``ROADVISION_WEB_DSN``. Çıkış kodu 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
import uuid

BASE_URL = os.environ.get("ROADVISION_WEB_URL", "http://127.0.0.1:8800").rstrip("/")
CSRF_HEADER = "X-RoadVision-CSRF"

_FAILED = False


def _ok(message: str) -> None:
    print(f"  [OK]   {message}")


def _fail(message: str) -> None:
    global _FAILED
    _FAILED = True
    print(f"  [HATA] {message}")


class Client:
    """Çerez kavanozlu küçük HTTP istemcisi (stdlib)."""

    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(BASE_URL + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with self.opener.open(req, timeout=10) as resp:
                payload = resp.read()
                return resp.status, json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                return exc.code, json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                return exc.code, {"raw": payload.decode("utf-8", "replace")}

    def csrf(self) -> str | None:
        for cookie in self.jar:
            if cookie.name == "rv_csrf":
                return cookie.value
        return None


def _expect(status: int, expected: int, label: str, body: dict) -> bool:
    if status == expected:
        _ok(f"{label} → {status}")
        return True
    _fail(f"{label} → {status} (beklenen {expected}); gövde: {body}")
    return False


def main() -> int:
    admin_email = os.environ.get("ROADVISION_WEB_ADMIN_EMAIL")
    admin_password = os.environ.get("ROADVISION_WEB_ADMIN_PASSWORD")
    if not admin_email or not admin_password:
        print(
            "ROADVISION_WEB_ADMIN_EMAIL ve ROADVISION_WEB_ADMIN_PASSWORD "
            "tanımlanmalı (bkz. web/scripts/create_admin.py)."
        )
        return 1

    print(f"RoadVision Web — Faz 1 kabul doğrulaması ({BASE_URL})")
    test_email = f"rv-faz1-verify+{uuid.uuid4().hex[:10]}@example.com"
    test_password = "Dgr-" + secrets.token_urlsafe(12)

    member = Client()
    status, body = member.request(
        "POST",
        "/api/auth/register",
        {"email": test_email, "full_name": "Faz1 Doğrulama", "password": test_password},
    )
    _expect(status, 201, "kayıt (pending)", body)
    user_id = body.get("user", {}).get("user_id")

    status, body = member.request(
        "POST", "/api/auth/login", {"email": test_email, "password": test_password}
    )
    if _expect(status, 403, "onaysız giriş", body):
        if body.get("error", {}).get("code") == "pending_approval":
            _ok("onaysız giriş kodu: pending_approval")
        else:
            _fail(f"onaysız giriş beklenen kodu vermedi: {body}")

    status, body = member.request("GET", "/api/auth/me")
    _expect(status, 401, "oturumsuz /auth/me", body)
    status, body = member.request("GET", "/api/admin/users")
    _expect(status, 401, "oturumsuz /admin/users", body)

    admin = Client()
    status, body = admin.request(
        "POST", "/api/auth/login", {"email": admin_email, "password": admin_password}
    )
    if not _expect(status, 200, "yönetici girişi", body):
        print("SONUÇ: FAIL")
        return 1
    admin_csrf = admin.csrf() or body.get("csrf_token")

    status, body = admin.request("GET", "/api/admin/sessions")
    if _expect(status, 200, "aktif oturum listesi", body):
        emails = [session.get("email") for session in body.get("sessions", [])]
        if admin_email in emails:
            _ok("yönetici oturumu aktif listede bulundu")
        else:
            _fail("yönetici oturumu aktif listede bulunamadı")

    status, body = admin.request("POST", f"/api/admin/users/{user_id}/approve")
    if status == 403 and body.get("error", {}).get("code") == "csrf_mismatch":
        _ok("CSRF başlıksız onay reddedildi (csrf_mismatch)")
    else:
        _fail(f"CSRF'siz istek reddedilmedi: {status} {body}")

    status, body = admin.request(
        "POST",
        f"/api/admin/users/{user_id}/approve",
        headers={CSRF_HEADER: admin_csrf},
    )
    _expect(status, 200, "kullanıcı onayı", body)

    status, body = admin.request(
        "POST",
        f"/api/admin/users/{user_id}/approve",
        headers={CSRF_HEADER: admin_csrf},
    )
    if status == 409:
        _ok("ikinci onay 409 invalid_transition")
    else:
        _fail(f"ikinci onay 409 dönmedi: {status} {body}")

    status, body = admin.request("GET", "/api/admin/audit?limit=20")
    if _expect(status, 200, "audit listesi", body):
        targets = [entry.get("target") for entry in body.get("entries", [])]
        if f"user:{user_id}" in targets:
            _ok("onay kaydı audit'te bulundu")
        else:
            _fail("onay kaydı audit'te bulunamadı")

    status, body = member.request(
        "POST", "/api/auth/login", {"email": test_email, "password": test_password}
    )
    _expect(status, 200, "onay sonrası üye girişi", body)
    status, body = member.request("GET", "/api/auth/me")
    _expect(status, 200, "üye /auth/me", body)
    status, body = member.request("GET", "/api/admin/users")
    if status == 403:
        _ok("üye, admin ucuna erişemedi (403)")
    else:
        _fail(f"üye admin ucuna erişti: {status}")

    status, body = admin.request(
        "POST",
        f"/api/admin/users/{user_id}/disable",
        headers={CSRF_HEADER: admin_csrf},
    )
    _expect(status, 200, "kullanıcıyı devre dışı bırakma", body)
    status, body = member.request("GET", "/api/auth/me")
    if status == 401:
        _ok("devre dışı kullanıcının oturumu anında geçersiz")
    else:
        _fail(f"devre dışı kullanıcı hâlâ oturumlu: {status} {body}")

    status, body = admin.request(
        "POST", "/api/auth/logout", headers={CSRF_HEADER: admin_csrf}
    )
    _expect(status, 200, "yönetici çıkışı ve test oturumu temizliği", body)

    dsn = os.environ.get("ROADVISION_WEB_DSN")
    if dsn:
        try:
            import psycopg

            with psycopg.connect(dsn) as conn, conn.transaction():
                conn.execute(
                    "DELETE FROM webapp.admin_audit WHERE target = %s",
                    (f"user:{user_id}",),
                )
                conn.execute(
                    "DELETE FROM webapp.users "
                    "WHERE email LIKE 'rv-faz1-verify+%%@example.com'"
                )
            _ok("doğrulama kayıtları temizlendi")
        except Exception as exc:  # Temizlik başarısızlığı kabulü düşürmez.
            print(f"  [UYARI] temizlik atlandı: {exc}")

    print("SONUÇ: " + ("FAIL" if _FAILED else "PASS"))
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
