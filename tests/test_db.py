from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from roadvision.db import PostgresSink, ingest_key_for, record_from_json_line, write_batch
from roadvision.logbook import LogCategory, LogLevel, LogRecord
from scripts.backfill_jsonl import backfill


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self.conn = conn
        self._last_returning = None
        self.rowcount = -1

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        normalized = " ".join(sql.split())
        self.conn.attempted_statements.append((normalized, params))
        self._last_returning = None
        self.rowcount = 1

        if self.conn.fail_next_insert and "INSERT INTO log_records" in normalized:
            self.conn.fail_next_insert = False
            raise RuntimeError("bağlantı koptu")

        self.conn.statements.append((normalized, params))
        table = next(
            (
                name
                for name in ("log_records", "detection_events")
                if f"INSERT INTO {name}" in normalized
            ),
            None,
        )
        ingest_key = params[-1] if table and params else None
        if (
            table
            and "ON CONFLICT" in normalized
            and ingest_key is not None
        ):
            seen = self.conn.seen_ingest_keys[table]
            pending = self.conn.pending_ingest_keys[table]
            if ingest_key in seen or ingest_key in pending:
                self._last_returning = None
                self.rowcount = 0
                return
            pending.add(ingest_key)

        if "RETURNING id" in normalized:
            self.conn.next_ids[table] += 1
            self._last_returning = (self.conn.next_ids[table],)

    def executemany(self, sql: str, params_list) -> None:
        for params in params_list:
            self.conn.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._last_returning


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list = []
        self.attempted_statements: list = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.fail_next_insert = False
        self.next_ids = {"log_records": 0, "detection_events": 0}
        self.seen_ingest_keys = {
            "log_records": set(),
            "detection_events": set(),
        }
        self.pending_ingest_keys = {
            "log_records": set(),
            "detection_events": set(),
        }

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1
        for table, keys in self.pending_ingest_keys.items():
            self.seen_ingest_keys[table].update(keys)
            keys.clear()

    def rollback(self) -> None:
        self.rollbacks += 1
        for keys in self.pending_ingest_keys.values():
            keys.clear()

    def close(self) -> None:
        self.closed = True


def app_record(message: str = "olay") -> LogRecord:
    return LogRecord(100.0, LogLevel.INFO, LogCategory.APP, message, run_id=1)


def detection_record(objects=None, message: str = "Çukur: 2 tespit") -> LogRecord:
    payload = {
        "object_count": 2,
        "elapsed_ms": 12.5,
        "dedup": "changed",
        "repeated_frames": 1,
        "objects": objects
        if objects is not None
        else [
            {"class": "pothole", "confidence": 0.91, "bbox": [1.0, 2.0, 3.0, 4.0]},
            {"class": "manhole", "confidence": 0.66, "bbox": [5.0, 6.0, 7.0, 8.0]},
        ],
    }
    return LogRecord(100.0, LogLevel.INFO, LogCategory.DETECTION, message, run_id=1, model_id="pothole", payload=payload)


def wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def table_rows(conn: FakeConnection, table: str) -> list:
    return [s for s in conn.statements if f"INSERT INTO {table}" in s[0]]


def table_attempts(conn: FakeConnection, table: str) -> list:
    return [s for s in conn.attempted_statements if f"INSERT INTO {table}" in s[0]]


class WriteBatchTests(unittest.TestCase):
    def test_app_record_goes_only_to_log_records(self) -> None:
        conn = FakeConnection()
        write_batch(conn, [(app_record(), None)])
        self.assertEqual(len(table_rows(conn, "log_records")), 1)
        self.assertEqual(len(table_rows(conn, "detection_events")), 0)
        self.assertEqual(conn.commits, 1)

    def test_detection_fans_out_to_three_tables(self) -> None:
        conn = FakeConnection()
        write_batch(conn, [(detection_record(), None)])
        self.assertEqual(len(table_rows(conn, "log_records")), 1)
        self.assertEqual(len(table_rows(conn, "detection_events")), 1)
        objects = table_rows(conn, "detected_objects")
        self.assertEqual(len(objects), 2)
        # V3 trigger'ı legacy model/sınıf INSERT'inden type_id üretir; bu
        # parametre düzeni v2 uyumluluk geri dönüşünde de çalışmaya devam eder.
        self.assertIn("model_id, class_name", objects[0][0])
        self.assertNotIn("type_id", objects[0][0])
        # tür + doğruluk + zaman satırda mevcut
        event_id, ts, run_id, model_id, class_name, confidence, bbox, area = objects[0][1]
        self.assertEqual(class_name, "pothole")
        self.assertAlmostEqual(confidence, 0.91)
        self.assertEqual(ts, 100.0)
        self.assertEqual(bbox, [1.0, 2.0, 3.0, 4.0])

    def test_semantic_object_with_null_confidence(self) -> None:
        conn = FakeConnection()
        record = detection_record(objects=[{"class": "roadline", "area_ratio": 0.31}])
        write_batch(conn, [(record, None)])
        row = table_rows(conn, "detected_objects")[0][1]
        self.assertEqual(row[4], "roadline")
        self.assertIsNone(row[5])  # confidence
        self.assertAlmostEqual(row[7], 0.31)  # area_ratio

    def test_duplicate_ingest_key_skips_event_and_objects(self) -> None:
        conn = FakeConnection()
        key = ingest_key_for("f.jsonl", 1, "satır")
        first_inserted = write_batch(conn, [(detection_record(), key)])
        objects_before = len(table_rows(conn, "detected_objects"))
        second_inserted = write_batch(conn, [(detection_record(), key)])
        self.assertEqual(first_inserted, 1)
        self.assertEqual(second_inserted, 0)
        self.assertEqual(len(table_rows(conn, "detected_objects")), objects_before)


class PostgresSinkTests(unittest.TestCase):
    def make_sink(self, factory) -> PostgresSink:
        sink = PostgresSink(
            "postgresql://test", connection_factory=factory,
            batch_size=10, flush_interval=0.05,
            error_reporter=lambda _message: None,
        )
        sink.prepare_sink()
        self.addCleanup(sink.release_sink)
        return sink

    def test_records_are_flushed_in_background(self) -> None:
        conn = FakeConnection()
        sink = self.make_sink(lambda dsn: conn)
        sink.write_record(app_record())
        sink.write_record(detection_record())
        self.assertTrue(wait_until(lambda: len(table_rows(conn, "log_records")) == 2))
        self.assertTrue(len(table_rows(conn, "detected_objects")) == 2)

    def test_connection_failure_retries_without_losing_records(self) -> None:
        attempts = []

        def flaky_factory(dsn: str):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("veritabanı kapalı")
            return FakeConnection()

        sink = PostgresSink(
            "postgresql://test", connection_factory=flaky_factory,
            batch_size=10, flush_interval=0.05,
            error_reporter=lambda _message: None,
        )
        sink._backoff = 0.01  # testte hızlı geri çekilme
        sink.prepare_sink()
        self.addCleanup(sink.release_sink)
        sink.write_record(app_record("dayanıklı"))
        checkpoint = sink.request_checkpoint()
        self.assertFalse(checkpoint.wait(0.001))
        self.assertTrue(wait_until(lambda: len(attempts) >= 3))
        conn = sink._conn
        self.assertTrue(wait_until(lambda: conn is not None and len(table_rows(conn, "log_records")) == 1))
        self.assertTrue(checkpoint.wait(1.0))

    def test_concurrent_checkpoints_keep_their_own_sequence_targets(self) -> None:
        sink = PostgresSink(
            "postgresql://test",
            connection_factory=lambda _dsn: FakeConnection(),
        )
        sink.write_record(app_record("ilk sınır"))
        first = sink.request_checkpoint()
        sink.write_record(app_record("ikinci sınır"))
        second = sink.request_checkpoint()

        self.assertIsNot(first, second)
        sink._settle_sequences((1,), success=True)
        self.assertTrue(first.wait(0.1))
        self.assertFalse(second.done)

        sink._settle_sequences((2,), success=True)
        self.assertTrue(second.wait(0.1))

    def test_write_failure_rolls_back_and_requeues_batch(self) -> None:
        first_conn = FakeConnection()
        first_conn.fail_next_insert = True
        second_conn = FakeConnection()
        connections = [first_conn, second_conn]
        sink = self.make_sink(lambda dsn: connections.pop(0))
        sink._backoff = 0.01
        sink.write_record(app_record("tekrar dene"))
        second = connections  # ikinci bağlantı pop'lanınca boşalır
        self.assertTrue(wait_until(lambda: not second))
        self.assertTrue(
            wait_until(lambda: len(table_rows(sink._conn, "log_records")) == 1)
        )
        first_key = table_attempts(first_conn, "log_records")[0][1][-1]
        retried_key = table_rows(second_conn, "log_records")[0][1][-1]
        self.assertIsNotNone(first_key)
        self.assertEqual(retried_key, first_key)
        self.assertEqual(first_conn.rollbacks, 1)
        self.assertTrue(first_conn.closed)

    def test_write_failure_preserves_queue_overflow_report_for_retry(self) -> None:
        first_conn = FakeConnection()
        first_conn.fail_next_insert = True
        second_conn = FakeConnection()
        connections = [first_conn, second_conn]
        sink = PostgresSink(
            "postgresql://test",
            connection_factory=lambda dsn: connections.pop(0),
            batch_size=1000,
            flush_interval=0.05,
            queue_size=2,
            error_reporter=lambda _message: None,
        )
        sink._backoff = 0.01
        for i in range(5):
            sink.write_record(app_record(f"kayıt {i}"))
        self.assertEqual(sink.dropped_records, 3)
        sink.prepare_sink()
        self.addCleanup(sink.release_sink)

        self.assertTrue(
            wait_until(
                lambda: any(
                    "db_dropped" in str(params)
                    for _, params in table_rows(second_conn, "log_records")
                )
            )
        )
        warnings = [
            params
            for _, params in table_rows(second_conn, "log_records")
            if "db_dropped" in str(params)
        ]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(json.loads(warnings[0][6])["db_dropped"], 3)

    def test_connection_errors_are_reported_without_retry_spam(self) -> None:
        attempts = []
        reports = []

        def unavailable_factory(dsn: str):
            attempts.append(1)
            raise RuntimeError("veritabanı kapalı")

        sink = PostgresSink(
            "postgresql://test",
            connection_factory=unavailable_factory,
            error_reporter=reports.append,
            batch_size=10,
            flush_interval=0.01,
        )
        sink._backoff = 0.01
        sink.prepare_sink()
        self.addCleanup(sink.release_sink)
        sink.write_record(app_record("raporlanmalı"))

        self.assertTrue(wait_until(lambda: len(attempts) >= 3))
        self.assertEqual(len(reports), 1)

    def test_queue_overflow_drops_oldest_and_reports(self) -> None:
        conn = FakeConnection()
        sink = PostgresSink(
            "postgresql://test", connection_factory=lambda dsn: conn,
            batch_size=1000, flush_interval=0.05, queue_size=5,
        )
        for i in range(9):  # flusher yokken doldur
            sink.write_record(app_record(f"kayıt {i}"))
        self.assertEqual(sink.dropped_records, 4)
        checkpoint = sink.request_checkpoint()
        sink.prepare_sink()
        self.addCleanup(sink.release_sink)
        self.assertTrue(
            wait_until(lambda: any("db_dropped" in str(s[1]) for s in table_rows(conn, "log_records")))
        )
        self.assertFalse(checkpoint.wait(1.0))
        self.assertTrue(checkpoint.done)

    def test_release_timeout_keeps_worker_owned_connection_open(self) -> None:
        conn = FakeConnection()
        sink = PostgresSink(
            "postgresql://test",
            connection_factory=lambda _dsn: conn,
        )
        flusher = Mock()
        flusher.is_alive.return_value = True
        sink._flusher = flusher
        sink._conn = conn

        sink.release_sink()

        flusher.join.assert_called_once_with(timeout=10.0)
        self.assertIs(sink._flusher, flusher)
        self.assertFalse(conn.closed)

        # Timeouttan sonra gerçek worker kendi döngüsünden çıktığında cleanup
        # release çağrısının ikinci kez yapılmasına bağlı kalmaz.
        sink._flusher_loop()
        self.assertTrue(conn.closed)
        self.assertIsNone(sink._conn)

    def test_release_flushes_remaining_records(self) -> None:
        conn = FakeConnection()
        sink = PostgresSink(
            "postgresql://test", connection_factory=lambda dsn: conn,
            batch_size=1000, flush_interval=60.0,  # zamanlayıcı devreye girmesin
        )
        sink.prepare_sink()
        sink.write_record(app_record("son kayıt"))
        sink.release_sink()
        self.assertEqual(len(table_rows(conn, "log_records")), 1)
        self.assertTrue(conn.closed)


class BackfillHelpersTests(unittest.TestCase):
    def test_record_roundtrip_from_jsonl(self) -> None:
        record = detection_record()
        parsed = record_from_json_line(record.to_json())
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.category, LogCategory.DETECTION)
        self.assertEqual(parsed.model_id, "pothole")
        self.assertEqual(parsed.payload["objects"][0]["class"], "pothole")
        self.assertAlmostEqual(parsed.timestamp, record.timestamp)
        self.assertEqual(parsed.ingest_key, record.ingest_key)

    def test_malformed_line_returns_none(self) -> None:
        self.assertIsNone(record_from_json_line("{bozuk"))
        self.assertIsNone(record_from_json_line('{"time": "yok"}'))

    def test_ingest_key_is_stable_and_distinct(self) -> None:
        a = ingest_key_for("f.jsonl", 1, "satır")
        self.assertEqual(a, ingest_key_for("f.jsonl", 1, "satır"))
        self.assertNotEqual(a, ingest_key_for("f.jsonl", 2, "satır"))

    def test_backfill_reports_zero_imports_when_all_keys_already_exist(self) -> None:
        conn = FakeConnection()
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "roadvision.jsonl"
            path.write_text(app_record("tekil").to_json() + "\n", encoding="utf-8")

            first_imported, first_skipped = backfill(path, conn)
            second_imported, second_skipped = backfill(path, conn)

        self.assertEqual((first_imported, first_skipped), (1, 0))
        self.assertEqual((second_imported, second_skipped), (0, 0))

    def test_backfill_skips_record_already_written_by_live_sink(self) -> None:
        conn = FakeConnection()
        record = app_record("canlı ve jsonl")
        self.assertIsNotNone(record.ingest_key)
        self.assertEqual(write_batch(conn, [(record, record.ingest_key)]), 1)

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "roadvision.jsonl"
            path.write_text(record.to_json() + "\n", encoding="utf-8")
            imported, skipped = backfill(path, conn)

        self.assertEqual((imported, skipped), (0, 0))

    def test_legacy_jsonl_without_ingest_key_remains_idempotent(self) -> None:
        conn = FakeConnection()
        data = app_record("eski kayıt").to_dict()
        data.pop("ingest_key")

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "roadvision.jsonl"
            path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
            first_imported, first_skipped = backfill(path, conn)
            second_imported, second_skipped = backfill(path, conn)

        self.assertEqual((first_imported, first_skipped), (1, 0))
        self.assertEqual((second_imported, second_skipped), (0, 0))


if __name__ == "__main__":
    unittest.main()
