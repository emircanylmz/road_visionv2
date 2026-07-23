from __future__ import annotations

import unittest
from datetime import datetime, timezone

from roadvision.db import CaptureBundle, fetch_capture


def normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


class CaptureCursor:
    def __init__(self, conn: "CaptureConnection") -> None:
        self.conn = conn
        self._kind = ""

    def __enter__(self) -> "CaptureCursor":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params: object) -> None:
        normalized = normalize_sql(sql)
        self.conn.statements.append((normalized, params))
        self._kind = "models" if "FROM media_capture_models" in normalized else "capture"

    def fetchone(self):
        return self.conn.capture_row if self._kind == "capture" else None

    def fetchall(self):
        return self.conn.model_rows if self._kind == "models" else []


class CaptureConnection:
    def __init__(self, capture_row, model_rows=()) -> None:
        self.capture_row = capture_row
        self.model_rows = model_rows
        self.statements: list[tuple[str, object]] = []

    def cursor(self) -> CaptureCursor:
        return CaptureCursor(self)


class FetchCaptureTests(unittest.TestCase):
    capture_id = "035de335-28d6-4c31-9d7d-54fc6ca076ff"

    def test_found_returns_both_images_metadata_and_sorted_models(self) -> None:
        timestamp = datetime(2026, 7, 23, 8, 30, tzinfo=timezone.utc)
        conn = CaptureConnection(
            (
                timestamp,
                "cadde.mp4",
                "video",
                91,
                True,
                memoryview(b"original"),
                1280,
                720,
                b"annotated",
                640,
                360,
            ),
            (
                ("pothole", 2, {"kind": "spatial-v1"}),
                ("traffic_sign", 1, ["spatial-v1"]),
            ),
        )

        bundle = fetch_capture(conn, self.capture_id)

        self.assertIsInstance(bundle, CaptureBundle)
        assert bundle is not None
        self.assertEqual(bundle.capture_id, self.capture_id)
        self.assertEqual(bundle.ts, timestamp)
        self.assertEqual(bundle.source_name, "cadde.mp4")
        self.assertEqual(bundle.source_kind, "video")
        self.assertEqual(bundle.frame_sequence, 91)
        self.assertTrue(bundle.is_reprocess)
        self.assertEqual((bundle.original.data, bundle.original.width, bundle.original.height), (b"original", 1280, 720))
        self.assertEqual((bundle.annotated.data, bundle.annotated.width, bundle.annotated.height), (b"annotated", 640, 360))
        self.assertEqual(
            bundle.models,
            (
                ("pothole", 2, {"kind": "spatial-v1"}),
                ("traffic_sign", 1, ["spatial-v1"]),
            ),
        )

        self.assertEqual(len(conn.statements), 2)
        for sql, params in conn.statements:
            self.assertIn("%s::uuid", sql)
            self.assertEqual(params, (self.capture_id,))
        self.assertIn("ORDER BY model_id", conn.statements[1][0])

    def test_missing_capture_returns_none_without_model_query(self) -> None:
        conn = CaptureConnection(None)

        result = fetch_capture(conn, self.capture_id)

        self.assertIsNone(result)
        self.assertEqual(len(conn.statements), 1)
        self.assertIn("WHERE c.capture_id = %s::uuid", conn.statements[0][0])


if __name__ == "__main__":
    unittest.main()
