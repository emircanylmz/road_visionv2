"""Faz 3 medya HTTP yardımcılarının güvenlik sözleşmesi testleri."""

from __future__ import annotations

import hashlib
import unittest

from fastapi import HTTPException

from app.routes_archive import _etag_matches, _media_response_parts


class EtagTests(unittest.TestCase):
    def test_guclu_zayif_liste_ve_yildiz_eslesir(self):
        digest = "a" * 64
        self.assertTrue(_etag_matches(f'"{digest}"', digest))
        self.assertTrue(_etag_matches(f'W/"{digest}"', digest))
        self.assertTrue(_etag_matches(f'"b", W/"{digest}"', digest))
        self.assertTrue(_etag_matches("*", digest))
        self.assertFalse(_etag_matches('"b"', digest))


class MediaResponseTests(unittest.TestCase):
    def test_guvenli_raster_butunluk_ve_onbellek_basliklari(self):
        payload = b"roadvision-jpeg"
        digest = hashlib.sha256(payload).hexdigest()
        body, mime, headers = _media_response_parts(
            digest, "image/jpeg", len(payload), payload
        )
        self.assertEqual(body, payload)
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(headers["ETag"], f'"{digest}"')
        self.assertEqual(headers["Cache-Control"], "private, no-cache")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", headers["Content-Security-Policy"])

    def test_guvenli_olmayan_mime_reddedilir(self):
        payload = b"<svg/>"
        digest = hashlib.sha256(payload).hexdigest()
        with self.assertRaises(HTTPException) as caught:
            _media_response_parts(digest, "image/svg+xml", len(payload), payload)
        self.assertEqual(caught.exception.status_code, 415)

    def test_boyut_ozet_ve_sha_tutarsizligi_reddedilir(self):
        payload = b"image"
        digest = hashlib.sha256(payload).hexdigest()
        for sha, size, data in (
            ("x", len(payload), payload),
            (digest, len(payload) + 1, payload),
            (digest, len(payload), b"other"),
        ):
            with self.subTest(sha=sha, size=size, data_len=len(data)):
                with self.assertRaises(HTTPException) as caught:
                    _media_response_parts(sha, "image/jpeg", size, data)
                self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
