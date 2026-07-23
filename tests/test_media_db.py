from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import call, patch

from roadvision.db import (
    MEDIA_ADVISORY_LOCK,
    SCHEMA_ADVISORY_LOCK,
    SCHEMA_VERSION,
    ensure_schema,
    write_batch,
)
from roadvision.logbook import LogCategory, LogLevel, LogRecord
from roadvision.media import CaptureModel, DbMediaSink, EncodedImage, Snapshot


def normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


def encoded_image(data: bytes, *, width: int = 8, height: int = 6) -> EncodedImage:
    return EncodedImage(
        data=data,
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def two_model_snapshot(capture_id: str = "035de335-28d6-4c31-9d7d-54fc6ca076ff") -> Snapshot:
    return Snapshot(
        capture_id=capture_id,
        timestamp=1_700_000_123.25,
        run_id=17,
        source_name="cadde.mp4",
        source_kind="video",
        frame_sequence=91,
        is_reprocess=False,
        models=(
            CaptureModel(
                model_id="pothole",
                signature=("spatial-v1", (("pothole", "bbox", 1, 2, 4, 6),)),
                object_count=1,
            ),
            CaptureModel(
                model_id="traffic_sign",
                signature=("spatial-v1", (("stop", "bbox", 8, 2, 10, 7),)),
                object_count=1,
            ),
        ),
    )


class MediaCursor:
    def __init__(self, conn: "MediaConnection") -> None:
        self.conn = conn
        self._result = None

    def __enter__(self) -> "MediaCursor":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        normalized = normalize_sql(sql)
        self.conn.attempts.append((normalized, params))
        self._result = None
        if (
            self.conn.fail_on is not None
            and self.conn.fail_on in normalized
            and not self.conn.failure_used
        ):
            self.conn.failure_used = True
            raise RuntimeError("geçici medya yazma hatası")

        if "INSERT INTO media_blobs" in normalized:
            sha256 = params[0]
            existing = self.conn.pending_blobs.get(sha256)
            if existing is None:
                existing = self.conn.blobs.get(sha256)
            if existing is None:
                self.conn.next_blob_id += 1
                existing = self.conn.next_blob_id
                self.conn.pending_blobs[sha256] = existing
            self._result = (existing,)
            return

        if "INSERT INTO media_captures" in normalized:
            capture_id = params[0]
            if capture_id not in self.conn.captures and capture_id not in self.conn.pending_captures:
                self.conn.pending_captures[capture_id] = params
            return

        if "INSERT INTO media_capture_models" in normalized:
            key = (params[0], params[1])
            if key not in self.conn.models and key not in self.conn.pending_models:
                self.conn.pending_models[key] = params

    def fetchone(self):
        return self._result


class MediaConnection:
    """Transaction-aware fake for DbMediaSink's three media tables."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.failure_used = False
        self.attempts: list[tuple[str, object]] = []
        self.blobs: dict[str, int] = {}
        self.captures: dict[str, tuple] = {}
        self.models: dict[tuple[str, str], tuple] = {}
        self.pending_blobs: dict[str, int] = {}
        self.pending_captures: dict[str, tuple] = {}
        self.pending_models: dict[tuple[str, str], tuple] = {}
        self.next_blob_id = 0
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> MediaCursor:
        return MediaCursor(self)

    def commit(self) -> None:
        self.commits += 1
        self.blobs.update(self.pending_blobs)
        self.captures.update(self.pending_captures)
        self.models.update(self.pending_models)
        self._clear_pending()

    def rollback(self) -> None:
        self.rollbacks += 1
        self._clear_pending()

    def close(self) -> None:
        self.closed = True

    def _clear_pending(self) -> None:
        self.pending_blobs.clear()
        self.pending_captures.clear()
        self.pending_models.clear()


class MigrationCursor:
    def __init__(self, conn: "MigrationConnection") -> None:
        self.conn = conn
        self._result = None

    def __enter__(self) -> "MigrationCursor":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        normalized = normalize_sql(sql)
        self.conn.statements.append((normalized, params))
        self._result = None
        if "SELECT COALESCE(MAX(version), 0) FROM schema_info" in normalized:
            current = max(self.conn.versions) if self.conn.versions else 0
            self._result = (current,)
        elif "INSERT INTO schema_info (version)" in normalized:
            self.conn.pending_versions.add(int(params[0]))

    def fetchone(self):
        return self._result


class MigrationConnection:
    def __init__(self, versions: set[int]) -> None:
        self.versions = set(versions)
        self.pending_versions: set[int] = set()
        self.statements: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> MigrationCursor:
        return MigrationCursor(self)

    def commit(self) -> None:
        self.commits += 1
        self.versions.update(self.pending_versions)
        self.pending_versions.clear()

    def rollback(self) -> None:
        self.rollbacks += 1
        self.pending_versions.clear()


class DetectionCursor:
    def __init__(self, conn: "DetectionConnection") -> None:
        self.conn = conn
        self._result = None

    def __enter__(self) -> "DetectionCursor":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        normalized = normalize_sql(sql)
        self.conn.statements.append((normalized, params))
        if "INSERT INTO log_records" in normalized:
            self._result = (1,)
        elif "INSERT INTO detection_events" in normalized:
            self._result = (2,)
        else:
            self._result = None

    def executemany(self, sql: str, params_list) -> None:
        normalized = normalize_sql(sql)
        self.conn.statements.extend((normalized, params) for params in params_list)

    def fetchone(self):
        return self._result


class DetectionConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, object]] = []
        self.commits = 0

    def cursor(self) -> DetectionCursor:
        return DetectionCursor(self)

    def commit(self) -> None:
        self.commits += 1


def statements_for(statements: list[tuple[str, object]], table: str) -> list[tuple[str, object]]:
    return [item for item in statements if f"INSERT INTO {table}" in item[0]]


class DbMediaSinkTests(unittest.TestCase):
    def test_store_is_idempotent_for_one_capture_with_two_models(self) -> None:
        conn = MediaConnection()
        original = encoded_image(b"original-jpeg")
        annotated = encoded_image(b"annotated-jpeg")
        snapshot = two_model_snapshot()
        sink = DbMediaSink(
            "postgresql://fake",
            retention_days=14,
            max_total_mb=64,
            connection_factory=lambda _dsn: conn,
            max_attempts=1,
        )

        with (
            patch("roadvision.media.ensure_schema") as schema,
            patch("roadvision.media.prune_media") as prune,
        ):
            sink.store(original, annotated, snapshot)
            sink.store(original, annotated, snapshot)

        self.assertEqual(len(conn.blobs), 2)
        self.assertEqual(len(conn.captures), 1)
        self.assertEqual(
            set(conn.models),
            {
                (snapshot.capture_id, "pothole"),
                (snapshot.capture_id, "traffic_sign"),
            },
        )
        self.assertEqual(conn.commits, 2)
        schema.assert_called_once_with(conn)
        self.assertEqual(prune.call_count, 2)
        prune.assert_has_calls(
            [
                call(conn, retention_days=14, max_total_bytes=64 * 1024 * 1024),
                call(conn, retention_days=14, max_total_bytes=64 * 1024 * 1024),
            ]
        )

        blob_sql = statements_for(conn.attempts, "media_blobs")[0][0]
        capture_sql = statements_for(conn.attempts, "media_captures")[0][0]
        model_sql = statements_for(conn.attempts, "media_capture_models")[0][0]
        self.assertIn("ON CONFLICT (sha256) DO UPDATE", blob_sql)
        self.assertIn("RETURNING id", blob_sql)
        self.assertIn("ON CONFLICT (capture_id) DO NOTHING", capture_sql)
        self.assertIn("ON CONFLICT (capture_id, model_id) DO NOTHING", model_sql)
        self.assertTrue(
            any(
                sql.startswith("SELECT pg_advisory_xact_lock")
                and params == (MEDIA_ADVISORY_LOCK,)
                for sql, params in conn.attempts
            )
        )

        capture_params = conn.captures[snapshot.capture_id]
        self.assertEqual(
            capture_params[:7],
            (
                snapshot.capture_id,
                snapshot.timestamp,
                snapshot.run_id,
                snapshot.source_name,
                snapshot.source_kind,
                snapshot.frame_sequence,
                snapshot.is_reprocess,
            ),
        )
        pothole_params = conn.models[(snapshot.capture_id, "pothole")]
        self.assertEqual(pothole_params[3], 1)
        self.assertEqual(
            json.loads(pothole_params[2]),
            ["spatial-v1", [["pothole", "bbox", 1, 2, 4, 6]]],
        )

    def test_same_original_and_annotated_hash_uses_one_blob_and_one_id(self) -> None:
        conn = MediaConnection()
        shared = encoded_image(b"same-jpeg")
        snapshot = two_model_snapshot()
        sink = DbMediaSink(
            "postgresql://fake",
            connection_factory=lambda _dsn: conn,
            max_attempts=1,
        )

        with (
            patch("roadvision.media.ensure_schema"),
            patch("roadvision.media.prune_media"),
        ):
            sink.store(shared, shared, snapshot)

        blob_inserts = statements_for(conn.attempts, "media_blobs")
        self.assertEqual(len(blob_inserts), 1)
        self.assertEqual(len(conn.blobs), 1)
        capture_params = conn.captures[snapshot.capture_id]
        self.assertEqual(capture_params[7], capture_params[8])

    def test_failed_transaction_reconnects_and_retries_whole_capture(self) -> None:
        first = MediaConnection(fail_on="INSERT INTO media_captures")
        second = MediaConnection()
        remaining = [first, second]
        factory_calls: list[str] = []
        delays: list[float] = []

        def factory(dsn: str) -> MediaConnection:
            factory_calls.append(dsn)
            return remaining.pop(0)

        sink = DbMediaSink(
            "postgresql://fake",
            connection_factory=factory,
            max_attempts=2,
            retry_delay=0.125,
            sleeper=delays.append,
        )
        with (
            patch("roadvision.media.ensure_schema") as schema,
            patch("roadvision.media.prune_media") as prune,
        ):
            sink.store(
                encoded_image(b"raw"),
                encoded_image(b"marked"),
                two_model_snapshot(),
            )

        self.assertEqual(factory_calls, ["postgresql://fake", "postgresql://fake"])
        self.assertEqual(delays, [0.125])
        self.assertEqual(schema.call_count, 2)
        self.assertEqual(prune.call_count, 1)
        self.assertEqual(first.rollbacks, 1)
        self.assertTrue(first.closed)
        self.assertEqual(first.blobs, {})
        self.assertEqual(first.captures, {})
        self.assertEqual(first.models, {})
        self.assertEqual(second.commits, 1)
        self.assertEqual(len(second.blobs), 2)
        self.assertEqual(len(second.captures), 1)
        self.assertEqual(len(second.models), 2)


class EnsureSchemaMigrationTests(unittest.TestCase):
    def test_v1_database_runs_only_v2_migration(self) -> None:
        conn = MigrationConnection({1})

        ensure_schema(conn)

        self.assertEqual(conn.versions, {1, 2})
        self.assertEqual((conn.commits, conn.rollbacks), (1, 0))
        migrations = [
            sql
            for sql, _ in conn.statements
            if "CREATE TABLE IF NOT EXISTS media_blobs" in sql
        ]
        self.assertEqual(len(migrations), 1)
        self.assertIn(
            "ALTER TABLE detection_events ADD COLUMN IF NOT EXISTS capture_id UUID",
            migrations[0],
        )
        self.assertFalse(
            any(
                "CREATE TABLE IF NOT EXISTS log_records" in sql
                for sql, _ in conn.statements
            ),
            "v1 şeması yükseltmede yeniden çalıştırıldı.",
        )
        self.assertTrue(
            any(
                sql.startswith("SELECT pg_advisory_xact_lock")
                and params == (SCHEMA_ADVISORY_LOCK,)
                for sql, params in conn.statements
            )
        )

    def test_current_schema_does_not_rerun_migrations(self) -> None:
        conn = MigrationConnection({1, 2})

        ensure_schema(conn)

        self.assertEqual(conn.versions, {1, 2})
        self.assertEqual((conn.commits, conn.rollbacks), (1, 0))
        self.assertFalse(
            any(
                "CREATE TABLE IF NOT EXISTS media_blobs" in sql
                or "CREATE TABLE IF NOT EXISTS log_records" in sql
                for sql, _ in conn.statements
            )
        )
        self.assertFalse(
            any("INSERT INTO schema_info (version)" in sql for sql, _ in conn.statements)
        )

    def test_future_schema_is_rejected_and_rolled_back(self) -> None:
        future = SCHEMA_VERSION + 1
        conn = MigrationConnection({future})

        with self.assertRaisesRegex(RuntimeError, rf"{future} > {SCHEMA_VERSION}"):
            ensure_schema(conn)

        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))
        self.assertEqual(conn.versions, {future})
        self.assertFalse(
            any(
                "CREATE TABLE IF NOT EXISTS media_blobs" in sql
                or "CREATE TABLE IF NOT EXISTS log_records" in sql
                for sql, _ in conn.statements
            )
        )


class DetectionCaptureIdTests(unittest.TestCase):
    @staticmethod
    def record(capture_id: str | None) -> LogRecord:
        payload = {
            "object_count": 1,
            "elapsed_ms": 8.5,
            "dedup": "changed",
            "repeated_frames": 1,
        }
        if capture_id is not None:
            payload["capture_id"] = capture_id
        return LogRecord(
            timestamp=1_700_000_123.25,
            level=LogLevel.INFO,
            category=LogCategory.DETECTION,
            message="Çukur: 1 tespit",
            run_id=17,
            model_id="pothole",
            payload=payload,
        )

    def test_detection_event_writes_capture_id_column_and_value(self) -> None:
        capture_id = "035de335-28d6-4c31-9d7d-54fc6ca076ff"
        conn = DetectionConnection()
        record = self.record(capture_id)

        inserted = write_batch(conn, [(record, record.ingest_key)])

        self.assertEqual(inserted, 1)
        rows = statements_for(conn.statements, "detection_events")
        self.assertEqual(len(rows), 1)
        sql, params = rows[0]
        self.assertIn("repeated_frames, capture_id, payload, ingest_key", sql)
        self.assertEqual(params[7], capture_id)
        self.assertEqual(json.loads(params[8])["capture_id"], capture_id)
        self.assertEqual(conn.commits, 1)

    def test_detection_without_media_writes_null_capture_id(self) -> None:
        conn = DetectionConnection()
        record = self.record(None)

        write_batch(conn, [(record, record.ingest_key)])

        _, params = statements_for(conn.statements, "detection_events")[0]
        self.assertIsNone(params[7])
        self.assertNotIn("capture_id", json.loads(params[8]))


if __name__ == "__main__":
    unittest.main()
