"""Parola özetleme ve CSRF yardımcıları.

argon2-cffi kurulu değilse özet testleri atlanır (torch gerektiren masaüstü
testlerine önerdiğimiz zarif skip yaklaşımının web karşılığı); belirteç ve
karşılaştırma testleri saf stdlib olduğundan her ortamda koşar.
"""

from __future__ import annotations

import importlib.util
import unittest

from app.security import csrf_matches, new_csrf_token, new_session_id
from app.security import validate_new_password

_ARGON2_VAR = importlib.util.find_spec("argon2") is not None


class TokenTests(unittest.TestCase):
    def test_csrf_belirteci_uzun_ve_tekil(self):
        tokens = {new_csrf_token() for _ in range(64)}
        self.assertEqual(len(tokens), 64)
        self.assertTrue(all(len(token) >= 32 for token in tokens))

    def test_session_id_uuid4(self):
        self.assertEqual(new_session_id().version, 4)

    def test_csrf_matches_sabit_kurallar(self):
        self.assertTrue(csrf_matches("abc", "abc"))
        self.assertFalse(csrf_matches("abc", "abd"))
        self.assertFalse(csrf_matches("abc", None))
        self.assertFalse(csrf_matches(None, "abc"))
        self.assertFalse(csrf_matches("", ""))

    def test_yeni_parola_uzunluk_siniri(self):
        self.assertEqual(validate_new_password("1234567890"), "1234567890")
        with self.assertRaises(ValueError):
            validate_new_password("123456789")
        with self.assertRaises(ValueError):
            validate_new_password("x" * 201)


@unittest.skipUnless(_ARGON2_VAR, "argon2-cffi kurulu değil")
class PasswordHashTests(unittest.TestCase):
    def test_hash_verify_dongusu(self):
        from app.security import hash_password, verify_password

        digest = hash_password("çokgizli-parola-123")
        self.assertTrue(digest.startswith("$argon2id$"))
        self.assertTrue(verify_password(digest, "çokgizli-parola-123"))
        self.assertFalse(verify_password(digest, "yanlış-parola"))

    def test_bozuk_ozet_false_doner(self):
        from app.security import verify_password

        self.assertFalse(verify_password("bozuk$veri", "x"))

    def test_taze_ozet_rehash_istemez(self):
        from app.security import hash_password, password_needs_rehash

        self.assertFalse(password_needs_rehash(hash_password("abcdefghij")))

    def test_dummy_verify_hata_firlatmaz(self):
        from app.security import dummy_verify

        dummy_verify("herhangi")  # Sessizce dönmeli.


if __name__ == "__main__":
    unittest.main()
