from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from roadvision.logbook import (
    ConsoleSink,
    DetectionSuppressor,
    EventJournal,
    JsonlFileSink,
    LogCategory,
    LogLevel,
    LogRecord,
    LogSink,
    NullJournal,
    SessionLogSink,
)


class MemorySink(LogSink):
    def __init__(self, min_level: LogLevel = LogLevel.DEBUG) -> None:
        self.min_level = min_level
        self.records: list[LogRecord] = []
        self.prepared = False
        self.released = False

    def prepare_sink(self) -> None:
        self.prepared = True

    def write_record(self, record: LogRecord) -> None:
        self.records.append(record)

    def release_sink(self) -> None:
        self.released = True


class FailingSink(MemorySink):
    def write_record(self, record: LogRecord) -> None:
        raise RuntimeError("sink patladı")


def wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class DetectionSuppressorTests(unittest.TestCase):
    def test_first_observation_is_logged(self) -> None:
        suppressor = DetectionSuppressor()
        decision = suppressor.observe(1, "pothole", 2, now=100.0)
        self.assertTrue(decision.should_log)
        self.assertEqual(decision.reason, "changed")

    def test_identical_consecutive_detections_are_suppressed(self) -> None:
        suppressor = DetectionSuppressor(heartbeat_seconds=30.0)
        suppressor.observe(1, "pothole", 2, now=100.0)
        for offset in range(1, 10):
            decision = suppressor.observe(1, "pothole", 2, now=100.0 + offset)
            self.assertFalse(decision.should_log)
        self.assertEqual(decision.repeated_frames, 10)

    def test_signature_change_logs_and_summarizes_previous_streak(self) -> None:
        suppressor = DetectionSuppressor()
        suppressor.observe(1, "pothole", 2, now=100.0)
        suppressor.observe(1, "pothole", 2, now=104.0)
        decision = suppressor.observe(1, "pothole", 3, now=105.0)
        self.assertTrue(decision.should_log)
        self.assertEqual(decision.previous_signature, 2)
        self.assertEqual(decision.previous_frames, 2)
        self.assertAlmostEqual(decision.previous_seconds, 5.0)

    def test_heartbeat_logs_long_steady_state(self) -> None:
        suppressor = DetectionSuppressor(heartbeat_seconds=30.0)
        suppressor.observe(1, "pothole", 2, now=100.0)
        self.assertFalse(suppressor.observe(1, "pothole", 2, now=110.0).should_log)
        decision = suppressor.observe(1, "pothole", 2, now=131.0)
        self.assertTrue(decision.should_log)
        self.assertEqual(decision.reason, "heartbeat")
        # Kalp atışından hemen sonra tekrar bastırılır.
        self.assertFalse(suppressor.observe(1, "pothole", 2, now=132.0).should_log)

    def test_heartbeat_can_be_disabled(self) -> None:
        suppressor = DetectionSuppressor(heartbeat_seconds=0)
        suppressor.observe(1, "pothole", 2, now=100.0)
        decision = suppressor.observe(1, "pothole", 2, now=100000.0)
        self.assertFalse(decision.should_log)

    def test_streaks_are_independent_per_model_and_run(self) -> None:
        suppressor = DetectionSuppressor()
        suppressor.observe(1, "pothole", 2, now=100.0)
        self.assertTrue(suppressor.observe(1, "sign", 2, now=100.0).should_log)
        self.assertTrue(suppressor.observe(2, "pothole", 2, now=100.0).should_log)

    def test_finish_run_emits_summary_and_forgets_streaks(self) -> None:
        suppressor = DetectionSuppressor()
        suppressor.observe(1, "pothole", 2, now=100.0)
        suppressor.observe(1, "pothole", 2, now=107.0)
        records = suppressor.finish_run(1, now=110.0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].model_id, "pothole")
        self.assertEqual(records[0].payload["frames"], 2)
        self.assertAlmostEqual(records[0].payload["seconds"], 10.0)
        # Yeni çalışma sıfırdan başlar.
        self.assertTrue(suppressor.observe(1, "pothole", 2, now=120.0).should_log)


class EventJournalTests(unittest.TestCase):
    def make_journal(self, sink: LogSink | None = None) -> tuple[EventJournal, MemorySink]:
        memory = sink or MemorySink()
        journal = EventJournal(sinks=[memory], suppressor=DetectionSuppressor(heartbeat_seconds=0))
        journal.prepare_journal()
        self.addCleanup(journal.release_journal)
        return journal, memory  # type: ignore[return-value]

    def test_app_event_reaches_sink(self) -> None:
        journal, memory = self.make_journal()
        journal.app_event(LogLevel.ERROR, "işlem hatası", run_id=3, detail="x")
        self.assertTrue(wait_until(lambda: any(r.message == "işlem hatası" for r in memory.records)))
        record = next(r for r in memory.records if r.message == "işlem hatası")
        self.assertEqual(record.category, LogCategory.APP)
        self.assertEqual(record.run_id, 3)
        self.assertEqual(record.payload["detail"], "x")

    def test_consecutive_identical_detections_write_single_record(self) -> None:
        journal, memory = self.make_journal()
        for _ in range(5):
            journal.detection(1, "pothole", "Çukur Tespiti", 2, 12.0)
        journal.detection(1, "pothole", "Çukur Tespiti", 3, 12.0)
        self.assertTrue(
            wait_until(
                lambda: len([r for r in memory.records if r.category == LogCategory.DETECTION]) == 2
            )
        )
        first, second = [r for r in memory.records if r.category == LogCategory.DETECTION]
        self.assertEqual(first.payload["object_count"], 2)
        self.assertEqual(second.payload["object_count"], 3)
        self.assertEqual(second.payload["previous"]["frames"], 5)

    def test_run_finished_closes_streaks(self) -> None:
        journal, memory = self.make_journal()
        journal.detection(1, "pothole", "Çukur Tespiti", 2, 12.0)
        journal.run_finished(1)
        self.assertTrue(
            wait_until(lambda: any(r.payload.get("closed_by") == "run_finished" for r in memory.records))
        )

    def test_min_level_filtering(self) -> None:
        memory = MemorySink(min_level=LogLevel.WARNING)
        journal, _ = self.make_journal(sink=memory)
        journal.app_event(LogLevel.DEBUG, "gizli")
        journal.app_event(LogLevel.WARNING, "görünür")
        self.assertTrue(wait_until(lambda: any(r.message == "görünür" for r in memory.records)))
        self.assertFalse(any(r.message == "gizli" for r in memory.records))

    def test_failing_sink_does_not_break_other_sinks(self) -> None:
        failing = FailingSink()
        healthy = MemorySink()
        journal = EventJournal(sinks=[failing, healthy], suppressor=DetectionSuppressor())
        journal.prepare_journal()
        self.addCleanup(journal.release_journal)
        journal.app_event(LogLevel.INFO, "mesaj")
        self.assertTrue(wait_until(lambda: any(r.message == "mesaj" for r in healthy.records)))

    def test_queue_overflow_drops_and_reports(self) -> None:
        memory = MemorySink()
        journal = EventJournal(sinks=[memory], queue_size=1)
        # Worker başlamadan doldur: ilk kayıt kuyruğa girer, ikincisi düşer.
        journal.app_event(LogLevel.INFO, "birinci")
        journal.app_event(LogLevel.INFO, "ikinci")
        self.assertEqual(journal.dropped_records, 1)
        journal.prepare_journal()  # worker başlar, kuyruk boşalır
        self.addCleanup(journal.release_journal)
        self.assertTrue(wait_until(lambda: len(memory.records) >= 1))
        dropped_note = [r for r in memory.records if "dropped_before_this" in r.payload]
        self.assertTrue(dropped_note)

    def test_add_sink_at_runtime(self) -> None:
        journal, _ = self.make_journal()
        late = MemorySink()
        journal.add_sink(late)
        journal.app_event(LogLevel.INFO, "sonradan")
        self.assertTrue(wait_until(lambda: any(r.message == "sonradan" for r in late.records)))
        self.assertTrue(late.prepared)

    def test_null_journal_is_inert(self) -> None:
        journal = NullJournal()
        journal.prepare_journal()
        journal.app_event(LogLevel.ERROR, "yok sayılır")
        journal.detection(1, "x", "X", 1, 1.0)
        journal.release_journal()
        self.assertEqual(journal.dropped_records, 0)


class JsonlFileSinkTests(unittest.TestCase):
    def test_records_are_written_as_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "logs" / "app.jsonl"
            sink = JsonlFileSink(path)
            sink.prepare_sink()
            sink.write_record(
                LogRecord(
                    timestamp=100.0,
                    level=LogLevel.INFO,
                    category=LogCategory.APP,
                    message="merhaba",
                    payload={"türkçe": "ççöö"},
                )
            )
            sink.release_sink()
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            data = json.loads(lines[0])
            self.assertEqual(data["message"], "merhaba")
            self.assertEqual(data["payload"]["türkçe"], "ççöö")

    def test_rotation_moves_file_to_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.jsonl"
            sink = JsonlFileSink(path, max_bytes=200)
            sink.prepare_sink()
            record = LogRecord(
                timestamp=100.0,
                level=LogLevel.INFO,
                category=LogCategory.APP,
                message="x" * 120,
            )
            sink.write_record(record)
            sink.write_record(record)
            sink.write_record(record)
            sink.release_sink()
            self.assertTrue(path.with_suffix(".jsonl.1").exists())
            self.assertTrue(path.exists())


class ConsoleSinkTests(unittest.TestCase):
    def test_accepts_respects_min_level(self) -> None:
        sink = ConsoleSink(min_level=LogLevel.WARNING)
        info = LogRecord(100.0, LogLevel.INFO, LogCategory.APP, "info")
        warning = LogRecord(100.0, LogLevel.WARNING, LogCategory.APP, "warn")
        self.assertFalse(sink.accepts(info))
        self.assertTrue(sink.accepts(warning))


class SessionLogSinkTests(unittest.TestCase):
    def test_full_queue_keeps_the_newest_records_in_order(self) -> None:
        sink = SessionLogSink(max_records=2)
        for message in ("bir", "iki", "üç"):
            sink.write_record(
                LogRecord(100.0, LogLevel.INFO, LogCategory.APP, message)
            )

        self.assertEqual([record.message for record in sink.drain()], ["iki", "üç"])
        self.assertEqual(sink.drain(), [])

    def test_max_records_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            SessionLogSink(max_records=0)


if __name__ == "__main__":
    unittest.main()
