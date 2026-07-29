"""Hesap katmanının saf kuralları: durum geçişleri ve alan sözleşmeleri."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.accounts import (
    InvalidTransition,
    UserRecord,
    transition_target,
    user_from_row,
    user_public,
)


class TransitionRuleTests(unittest.TestCase):
    def test_gecerli_gecisler(self):
        self.assertEqual(transition_target("approve", "pending"), "approved")
        self.assertEqual(transition_target("approve", "rejected"), "approved")
        self.assertEqual(transition_target("approve", "disabled"), "approved")
        self.assertEqual(transition_target("reject", "pending"), "rejected")
        self.assertEqual(transition_target("disable", "approved"), "disabled")

    def test_gecersiz_gecisler_reddedilir(self):
        gecersizler = [
            ("approve", "approved"),
            ("reject", "approved"),
            ("reject", "rejected"),
            ("reject", "disabled"),
            ("disable", "pending"),
            ("disable", "rejected"),
            ("disable", "disabled"),
        ]
        for action, current in gecersizler:
            with self.subTest(action=action, current=current):
                with self.assertRaises(InvalidTransition):
                    transition_target(action, current)

    def test_bilinmeyen_eylem(self):
        with self.assertRaises(InvalidTransition):
            transition_target("delete", "pending")


class UserRecordTests(unittest.TestCase):
    def _ornek(self) -> UserRecord:
        now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
        return user_from_row(
            (7, "a@b.c", "Ad Soyad", "member", "pending", now, None, None)
        )

    def test_user_from_row_alan_sirasi(self):
        user = self._ornek()
        self.assertEqual(user.user_id, 7)
        self.assertEqual(user.email, "a@b.c")
        self.assertEqual(user.role, "member")
        self.assertEqual(user.status, "pending")

    def test_user_public_parola_ozetini_sizdirmaz(self):
        alanlar = user_public(self._ornek())
        self.assertNotIn("password_hash", alanlar)
        self.assertEqual(
            set(alanlar),
            {
                "user_id",
                "email",
                "full_name",
                "role",
                "status",
                "created_at",
                "approved_at",
            },
        )


if __name__ == "__main__":
    unittest.main()
