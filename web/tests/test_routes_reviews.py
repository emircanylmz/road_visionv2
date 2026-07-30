"""Faz 4 review route transaction sözleşmesinin DB'siz testleri."""

from __future__ import annotations

import unittest
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.routes_reviews import (
    BulkReviewIn,
    ReviewIn,
    _create_review,
    create_reviews_bulk,
)


class _Cursor:
    def __init__(self, *, one=None, many=None):
        self.one = one
        self.many = many or []

    async def fetchone(self):
        return self.one

    async def fetchall(self):
        return self.many


class _ReviewConnection:
    """_create_review içindeki bütün SQL'in tek transaction'da olduğunu izler."""

    def __init__(self):
        self.active = 0
        self.transaction_count = 0
        self.sql: list[str] = []
        self.reviewed_at = datetime(2026, 7, 30, tzinfo=timezone.utc)

    @asynccontextmanager
    async def transaction(self):
        self.assert_idle()
        self.active = 1
        self.transaction_count += 1
        try:
            yield self
        finally:
            self.active = 0

    def assert_idle(self):
        if self.active:
            raise AssertionError("transaction iç içe açılmamalı")

    async def execute(self, sql, _params=None):
        if self.active != 1:
            raise AssertionError("review SQL'i atomik transaction dışında")
        normalized = " ".join(sql.split())
        self.sql.append(normalized)
        if "FROM public.detected_objects AS o" in normalized:
            return _Cursor(
                one=(
                    41,
                    "pothole",
                    "detect",
                    7,
                    "cukur",
                    [10.0, 20.0, 110.0, 220.0],
                    0.8,
                    0.1,
                    self.reviewed_at,
                    9,
                    uuid.uuid4(),
                    None,
                    1280,
                    720,
                    None,
                )
            )
        if "FROM public.detection_types" in normalized:
            return _Cursor(many=[("cukur", 7)])
        if "INSERT INTO webapp.detection_reviews" in normalized:
            return _Cursor(one=(self.reviewed_at,))
        return _Cursor()


class CreateReviewTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_baglami_ve_uclu_yazimi_tek_transactionda_yapar(self):
        conn = _ReviewConnection()

        review = await _create_review(
            conn,
            ReviewIn(object_id=41, verdict="correct"),
            reviewer_id=3,
        )

        self.assertEqual(review["verdict"], "correct")
        self.assertEqual(conn.transaction_count, 1)
        self.assertTrue(
            any("INSERT INTO webapp.detection_reviews" in sql for sql in conn.sql)
        )
        self.assertTrue(
            any("INSERT INTO webapp.dataset_samples" in sql for sql in conn.sql)
        )


class _BulkConnection:
    def __init__(self):
        self.committed = False

    async def commit(self):
        self.committed = True


class BulkTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_transactionini_kapatip_kismi_sonuc_dondurur(self):
        conn = _BulkConnection()
        payload = BulkReviewIn(
            items=[
                ReviewIn(object_id=1, verdict="correct"),
                ReviewIn(object_id=2, verdict="correct"),
            ]
        )
        user = SimpleNamespace(user=SimpleNamespace(user_id=8))

        async def fake_create(_conn, item, _reviewer_id):
            self.assertTrue(conn.committed)
            if item.object_id == 1:
                raise HTTPException(
                    409,
                    detail={"code": "already_reviewed", "message": "var"},
                )
            return {"verdict": "correct"}

        with (
            patch(
                "app.routes_reviews._require_archive_schema",
                return_value=None,
            ),
            patch(
                "app.routes_reviews._create_review",
                side_effect=fake_create,
            ),
        ):
            result = await create_reviews_bulk(
                payload,
                user=user,
                _csrf=None,
                conn=conn,
            )

        self.assertEqual(result["ok_count"], 1)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["results"][0]["code"], "already_reviewed")


if __name__ == "__main__":
    unittest.main()
