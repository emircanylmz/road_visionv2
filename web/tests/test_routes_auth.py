"""Kimlik istek modellerinin sınır doğrulamaları."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Response
from pydantic import ValidationError

from app.routes_auth import LoginIn, RegisterIn, login, register


class _BlockedLimiter:
    def check(self, _key):
        return 1.2


class RegisterInputTests(unittest.TestCase):
    def test_ad_soyad_kenar_bosluklari_temizlenir(self):
        payload = RegisterIn(
            email="uye@example.com",
            full_name="  Ada Lovelace  ",
            password="guvenli-parola",
        )
        self.assertEqual(payload.full_name, "Ada Lovelace")

    def test_yalniz_bosluk_olan_ad_reddedilir(self):
        with self.assertRaises(ValidationError):
            RegisterIn(
                email="uye@example.com",
                full_name="   ",
                password="guvenli-parola",
            )


class AnonymousRateLimitTests(unittest.TestCase):
    def _request(self):
        state = SimpleNamespace(
            register_limiter=_BlockedLimiter(),
            login_ip_limiter=_BlockedLimiter(),
            login_limiter=_BlockedLimiter(),
            settings=SimpleNamespace(),
        )
        return SimpleNamespace(
            client=SimpleNamespace(host="192.0.2.10"),
            app=SimpleNamespace(state=state),
        )

    def test_kayit_siniri_argon2den_once_429_doner(self):
        payload = RegisterIn(
            email="uye@example.com",
            full_name="Ada Lovelace",
            password="guvenli-parola",
        )
        with patch("app.routes_auth.security.hash_password") as hash_password:
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(register(payload, self._request(), object()))
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.headers["Retry-After"], "2")
        hash_password.assert_not_called()

    def test_login_ip_siniri_db_ve_argon2den_once_429_doner(self):
        payload = LoginIn(email="benzersiz@example.com", password="yanlis")
        with patch("app.routes_auth.accounts.get_user_credentials") as lookup:
            with patch("app.routes_auth.security.dummy_verify") as dummy_verify:
                with self.assertRaises(HTTPException) as caught:
                    asyncio.run(
                        login(payload, self._request(), Response(), object())
                    )
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.headers["Retry-After"], "2")
        lookup.assert_not_called()
        dummy_verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
