"""Faz 3 medya HTTP yardımcılarının güvenlik sözleşmesi testleri."""

from __future__ import annotations

import asyncio
import hashlib
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.routes_archive import (
    _etag_matches,
    _media_response_parts,
    _validated_media_meta,
    _verify_media_payload,
    get_media,
)


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

    def test_meta_dogrulama_bayt_gerektirmez(self):
        # 304 kısayolunun sözleşmesi: blob çekilmeden özet biçimi ve MIME
        # allowlist'i doğrulanır, başlıklar 200 yoluyla birebir aynıdır.
        digest_in = "A" * 64
        digest, media_type, headers = _validated_media_meta(digest_in, "IMAGE/PNG")
        self.assertEqual(digest, "a" * 64)
        self.assertEqual(media_type, "image/png")
        self.assertEqual(headers["ETag"], f'"{"a" * 64}"')
        self.assertEqual(headers["Cache-Control"], "private, no-cache")
        self.assertIn("sandbox", headers["Content-Security-Policy"])

    def test_meta_dogrulama_guvensiz_turu_304_yolunda_da_reddeder(self):
        with self.assertRaises(HTTPException) as caught:
            _validated_media_meta("a" * 64, "image/svg+xml")
        self.assertEqual(caught.exception.status_code, 415)
        with self.assertRaises(HTTPException) as caught:
            _validated_media_meta("kisa-ozet", "image/jpeg")
        self.assertEqual(caught.exception.status_code, 409)

    def test_bayt_dogrulama_ozet_ve_boyutu_esler(self):
        payload = b"roadvision-bytes"
        digest = hashlib.sha256(payload).hexdigest()
        self.assertEqual(
            _verify_media_payload(digest, len(payload), payload), payload
        )
        with self.assertRaises(HTTPException) as caught:
            _verify_media_payload(digest, len(payload), b"tampered")
        self.assertEqual(caught.exception.status_code, 409)

    def test_304_yolu_blob_sorgusunu_calistirmaz(self):
        payload = b"cached-image"
        digest = hashlib.sha256(payload).hexdigest()

        class Cursor:
            async def fetchone(self):
                return digest, "image/jpeg", len(payload)

        class Connection:
            def __init__(self):
                self.queries = []

            async def execute(self, sql, params):
                self.queries.append((sql, params))
                return Cursor()

        conn = Connection()
        with patch(
            "app.routes_archive._require_archive_schema",
            new=AsyncMock(),
        ):
            response = asyncio.run(
                get_media(7, f'"{digest}"', _user=None, conn=conn)
            )
        self.assertEqual(response.status_code, 304)
        self.assertEqual(len(conn.queries), 1)
        self.assertNotIn("SELECT data", conn.queries[0][0])

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
