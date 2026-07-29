"""Kimlik istek modellerinin sınır doğrulamaları."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.routes_auth import RegisterIn


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


if __name__ == "__main__":
    unittest.main()
