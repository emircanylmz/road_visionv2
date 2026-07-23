from __future__ import annotations

import hashlib
import threading
import unittest

import cv2
import numpy as np

from roadvision.media import (
    CaptureModel,
    EncodedImage,
    FrameEncoder,
    GateObservation,
    MediaRecorder,
    MediaSink,
    Snapshot,
    SnapshotGate,
    snapshot_signature,
)
from roadvision.models.detections import DetectedObject


def make_snapshot(capture_id: str, *model_ids: str) -> Snapshot:
    return Snapshot(
        capture_id=capture_id,
        timestamp=1_700_000_000.0,
        run_id=7,
        source_name="test.mp4",
        source_kind="video",
        frame_sequence=42,
        is_reprocess=False,
        models=tuple(
            CaptureModel(
                model_id=model_id,
                signature=("spatial-v1", (("pothole", "present"),)),
                object_count=1,
            )
            for model_id in model_ids
        ),
    )


class MemorySink(MediaSink):
    def __init__(self) -> None:
        self.prepare_count = 0
        self.release_count = 0
        self.records: list[tuple[EncodedImage, EncodedImage, Snapshot]] = []
        self._lock = threading.Lock()

    def prepare_sink(self) -> None:
        self.prepare_count += 1

    def store(
        self,
        original: EncodedImage,
        annotated: EncodedImage,
        snapshot: Snapshot,
    ) -> None:
        with self._lock:
            self.records.append((original, annotated, snapshot))

    def release_sink(self) -> None:
        self.release_count += 1


class ByteEncoder:
    """Test encoder that preserves exact input bytes instead of using JPEG."""

    def encode(self, frame: np.ndarray) -> EncodedImage:
        data = frame.tobytes()
        height, width = frame.shape[:2]
        return EncodedImage(
            data=data,
            width=width,
            height=height,
            sha256=hashlib.sha256(data).hexdigest(),
            mime="application/x-test-array",
        )


class BlockingFirstEncoder(ByteEncoder):
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.allow = threading.Event()
        self._calls = 0
        self._lock = threading.Lock()

    def encode(self, frame: np.ndarray) -> EncodedImage:
        with self._lock:
            self._calls += 1
            should_block = self._calls == 1
        if should_block:
            self.entered.set()
            if not self.allow.wait(2.0):
                raise TimeoutError("Test encoder serbest bırakılmadı.")
        return super().encode(frame)


class MemoryJournal:
    def __init__(self) -> None:
        self.records: list[tuple[object, str, dict[str, object]]] = []
        self._lock = threading.Lock()

    def app_event(self, level: object, message: str, **payload: object) -> None:
        with self._lock:
            self.records.append((level, message, payload))


class FrameEncoderTests(unittest.TestCase):
    def test_downsizes_long_edge_preserving_aspect_and_sha_is_stable(self) -> None:
        rows = np.arange(100, dtype=np.uint8)[:, None]
        columns = np.arange(200, dtype=np.uint8)[None, :]
        frame = np.dstack(
            (
                np.broadcast_to(columns, (100, 200)),
                np.broadcast_to(rows, (100, 200)),
                np.full((100, 200), 127, dtype=np.uint8),
            )
        )
        encoder = FrameEncoder(max_edge=80, jpeg_quality=82)

        first = encoder.encode(frame)
        second = encoder.encode(frame)
        decoded = cv2.imdecode(np.frombuffer(first.data, dtype=np.uint8), cv2.IMREAD_COLOR)

        self.assertEqual((first.width, first.height), (80, 40))
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded.shape[:2], (40, 80))
        self.assertEqual(first.mime, "image/jpeg")
        self.assertEqual(first.byte_size, len(first.data))
        self.assertEqual(first.sha256, hashlib.sha256(first.data).hexdigest())
        self.assertEqual(first.data, second.data)
        self.assertEqual(first.sha256, second.sha256)

    def test_accepts_grayscale_without_unnecessary_resize(self) -> None:
        frame = np.arange(30 * 20, dtype=np.uint8).reshape(30, 20)

        encoded = FrameEncoder(max_edge=64, jpeg_quality=80).encode(frame)
        decoded = cv2.imdecode(
            np.frombuffer(encoded.data, dtype=np.uint8),
            cv2.IMREAD_UNCHANGED,
        )

        self.assertEqual((encoded.width, encoded.height), (20, 30))
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded.shape, (30, 20))

    def test_rejects_invalid_frames_and_configuration(self) -> None:
        with self.assertRaises(ValueError):
            FrameEncoder(max_edge=0)
        with self.assertRaises(ValueError):
            FrameEncoder(jpeg_quality=101)
        with self.assertRaises(ValueError):
            FrameEncoder().encode(np.array([], dtype=np.uint8))
        with self.assertRaises(ValueError):
            FrameEncoder().encode(np.zeros((2, 2, 2, 2), dtype=np.uint8))


class SnapshotSignatureTests(unittest.TestCase):
    def test_bbox_signature_ignores_small_jitter_but_detects_real_motion(self) -> None:
        base = DetectedObject("pothole", 0.91, bbox=(20, 20, 60, 60))
        jittered = DetectedObject("pothole", 0.60, bbox=(21, 21, 61, 61))
        moved = DetectedObject("pothole", 0.91, bbox=(40, 20, 80, 60))

        base_signature = snapshot_signature((base,), 1, (100, 200, 3))

        self.assertEqual(
            base_signature,
            snapshot_signature((jittered,), 1, (100, 200, 3)),
        )
        self.assertNotEqual(
            base_signature,
            snapshot_signature((moved,), 1, (100, 200, 3)),
        )
        # Normalize edilmiş koordinatlar çözünürlükten bağımsız olmalıdır.
        self.assertEqual(
            base_signature,
            snapshot_signature(
                (DetectedObject("pothole", 0.91, bbox=(40, 40, 120, 120)),),
                1,
                (200, 400, 3),
            ),
        )

    def test_semantic_signature_quantizes_area_and_falls_back_to_count(self) -> None:
        base = DetectedObject("road_damage", None, area_ratio=0.20)
        jittered = DetectedObject("road_damage", None, area_ratio=0.205)
        changed = DetectedObject("road_damage", None, area_ratio=0.30)

        base_signature = snapshot_signature((base,), 1, (100, 100, 3))

        self.assertEqual(
            base_signature,
            snapshot_signature((jittered,), 1, (100, 100, 3)),
        )
        self.assertNotEqual(
            base_signature,
            snapshot_signature((changed,), 1, (100, 100, 3)),
        )
        self.assertEqual(
            snapshot_signature((), 3, (100, 100, 3)),
            ("spatial-v1", ("count", 3)),
        )

    def test_signature_is_independent_of_object_order(self) -> None:
        first = DetectedObject("pothole", 0.9, bbox=(10, 10, 30, 30))
        second = DetectedObject("manhole", 0.8, bbox=(50, 50, 80, 80))

        self.assertEqual(
            snapshot_signature((first, second), 2, (100, 100, 3)),
            snapshot_signature((second, first), 2, (100, 100, 3)),
        )


class SnapshotGateTests(unittest.TestCase):
    @staticmethod
    def observation(model_id: str, signature: str, count: int = 1) -> GateObservation:
        return GateObservation(model_id, signature, count)

    def test_same_signature_is_suppressed_after_commit(self) -> None:
        gate = SnapshotGate(
            min_interval_seconds=0,
            max_captures_per_run=10,
            max_captures_per_hour=10,
        )
        observation = self.observation("pothole", "A")

        first = gate.evaluate(1, (observation,), is_static=False, now=10.0)
        self.assertTrue(first.capture)
        gate.commit(1, first.models, now=10.0)

        repeated = gate.evaluate(1, (observation,), is_static=False, now=20.0)
        self.assertFalse(repeated.capture)
        self.assertEqual(repeated.reason, "same_signature")

    def test_stable_change_becomes_eligible_after_minimum_interval(self) -> None:
        gate = SnapshotGate(
            min_interval_seconds=2.0,
            max_captures_per_run=10,
            max_captures_per_hour=10,
        )
        original = self.observation("pothole", "A")
        changed = self.observation("pothole", "B")

        first = gate.evaluate(1, (original,), is_static=False, now=10.0)
        gate.commit(1, first.models, now=10.0)

        too_soon = gate.evaluate(1, (changed,), is_static=False, now=11.0)
        self.assertFalse(too_soon.capture)
        self.assertEqual(too_soon.reason, "min_interval")

        after_interval = gate.evaluate(1, (changed,), is_static=False, now=12.0)
        self.assertTrue(after_interval.capture)
        self.assertEqual(after_interval.models, (changed,))
        gate.commit(1, after_interval.models, now=12.0)
        self.assertEqual(
            gate.evaluate(1, (changed,), is_static=False, now=20.0).reason,
            "same_signature",
        )

    def test_static_source_does_not_wait_for_minimum_interval(self) -> None:
        gate = SnapshotGate(
            min_interval_seconds=30.0,
            max_captures_per_run=10,
            max_captures_per_hour=10,
        )
        original = self.observation("pothole", "A")
        changed = self.observation("pothole", "B")
        first = gate.evaluate(1, (original,), is_static=True, now=10.0)
        gate.commit(1, first.models, now=10.0)

        decision = gate.evaluate(1, (changed,), is_static=True, now=10.1)

        self.assertTrue(decision.capture)
        self.assertEqual(decision.models, (changed,))

    def test_run_limit_warns_once_and_finish_run_resets_run_state(self) -> None:
        gate = SnapshotGate(
            min_interval_seconds=0,
            max_captures_per_run=2,
            max_captures_per_hour=20,
        )
        for now, signature in ((1.0, "A"), (2.0, "B")):
            decision = gate.evaluate(
                1,
                (self.observation("pothole", signature),),
                is_static=False,
                now=now,
            )
            self.assertTrue(decision.capture)
            gate.commit(1, decision.models, now=now)

        limited = gate.evaluate(
            1,
            (self.observation("pothole", "C"),),
            is_static=False,
            now=3.0,
        )
        limited_again = gate.evaluate(
            1,
            (self.observation("pothole", "D"),),
            is_static=False,
            now=4.0,
        )
        self.assertEqual((limited.reason, limited.warning), ("run_limit", "run_limit"))
        self.assertEqual((limited_again.reason, limited_again.warning), ("run_limit", None))

        gate.finish_run(1)
        reset = gate.evaluate(
            1,
            (self.observation("pothole", "A"),),
            is_static=False,
            now=5.0,
        )
        self.assertTrue(reset.capture)

    def test_hour_limit_is_global_and_expires_after_one_hour(self) -> None:
        gate = SnapshotGate(
            min_interval_seconds=0,
            max_captures_per_run=10,
            max_captures_per_hour=2,
        )
        for run_id, now in ((1, 0.0), (2, 100.0)):
            decision = gate.evaluate(
                run_id,
                (self.observation("pothole", f"S{run_id}"),),
                is_static=False,
                now=now,
            )
            gate.commit(run_id, decision.models, now=now)

        limited = gate.evaluate(
            3,
            (self.observation("pothole", "S3"),),
            is_static=False,
            now=200.0,
        )
        limited_again = gate.evaluate(
            4,
            (self.observation("pothole", "S4"),),
            is_static=False,
            now=300.0,
        )
        self.assertEqual((limited.reason, limited.warning), ("hour_limit", "hour_limit"))
        self.assertEqual((limited_again.reason, limited_again.warning), ("hour_limit", None))

        expired = gate.evaluate(
            5,
            (self.observation("pothole", "S5"),),
            is_static=False,
            now=3600.0,
        )
        self.assertTrue(expired.capture)

    def test_multi_model_capture_consumes_one_physical_run_quota(self) -> None:
        gate = SnapshotGate(
            min_interval_seconds=0,
            max_captures_per_run=2,
            max_captures_per_hour=20,
        )
        first_batch = (
            self.observation("pothole", "A1"),
            self.observation("traffic_sign", "B1"),
        )
        second_batch = (
            self.observation("pothole", "A2"),
            self.observation("traffic_sign", "B2"),
        )

        first = gate.evaluate(1, first_batch, is_static=False, now=1.0)
        self.assertEqual(first.models, first_batch)
        gate.commit(1, first.models, now=1.0)
        second = gate.evaluate(1, second_batch, is_static=False, now=2.0)
        self.assertTrue(second.capture, "İki model ilk fiziksel kotayı iki kez tüketti.")
        gate.commit(1, second.models, now=2.0)

        third = gate.evaluate(
            1,
            (self.observation("pothole", "A3"),),
            is_static=False,
            now=3.0,
        )
        self.assertEqual(third.reason, "run_limit")

    def test_only_changed_models_are_returned_from_a_shared_frame(self) -> None:
        gate = SnapshotGate(
            min_interval_seconds=0,
            max_captures_per_run=10,
            max_captures_per_hour=10,
        )
        original = (
            self.observation("pothole", "A"),
            self.observation("traffic_sign", "B"),
        )
        first = gate.evaluate(1, original, is_static=False, now=1.0)
        gate.commit(1, first.models, now=1.0)
        changed_sign = self.observation("traffic_sign", "C")

        decision = gate.evaluate(
            1,
            (original[0], changed_sign),
            is_static=False,
            now=2.0,
        )

        self.assertEqual(decision.models, (changed_sign,))


class MediaRecorderTests(unittest.TestCase):
    def test_submit_takes_ownership_by_copying_before_async_encode(self) -> None:
        sink = MemorySink()
        encoder = BlockingFirstEncoder()
        recorder = MediaRecorder(
            sink,
            encoder=encoder,  # type: ignore[arg-type]
            queue_size=2,
            queue_max_bytes=1024,
        )
        recorder.prepare_recorder()
        self.addCleanup(lambda: recorder.release_recorder(timeout=2.0))
        self.addCleanup(encoder.allow.set)
        raw = np.full((3, 4, 3), 7, dtype=np.uint8)
        annotated = np.full((3, 4, 3), 9, dtype=np.uint8)

        self.assertTrue(recorder.submit(raw, annotated, make_snapshot("owned", "pothole")))
        self.assertTrue(encoder.entered.wait(1.0))
        raw.fill(70)
        annotated.fill(90)
        encoder.allow.set()
        self.assertTrue(recorder.release_recorder(timeout=2.0))

        self.assertEqual(len(sink.records), 1)
        original, marked, _ = sink.records[0]
        self.assertEqual(original.data, np.full((3, 4, 3), 7, dtype=np.uint8).tobytes())
        self.assertEqual(marked.data, np.full((3, 4, 3), 9, dtype=np.uint8).tobytes())

    def test_full_queue_drops_without_blocking_and_reports_count(self) -> None:
        sink = MemorySink()
        journal = MemoryJournal()
        encoder = BlockingFirstEncoder()
        recorder = MediaRecorder(
            sink,
            encoder=encoder,  # type: ignore[arg-type]
            journal=journal,  # type: ignore[arg-type]
            queue_size=1,
            queue_max_bytes=4096,
        )
        recorder.prepare_recorder()
        self.addCleanup(lambda: recorder.release_recorder(timeout=2.0))
        self.addCleanup(encoder.allow.set)
        frame = np.zeros((4, 4, 3), dtype=np.uint8)

        self.assertTrue(recorder.submit(frame, frame, make_snapshot("first", "pothole")))
        self.assertTrue(encoder.entered.wait(1.0))
        self.assertTrue(recorder.submit(frame, frame, make_snapshot("second", "pothole")))
        self.assertFalse(recorder.submit(frame, frame, make_snapshot("dropped", "pothole")))
        encoder.allow.set()
        self.assertTrue(recorder.release_recorder(timeout=2.0))

        self.assertEqual([record[2].capture_id for record in sink.records], ["first", "second"])
        drop_counts = [
            payload["media_dropped"]
            for _, _, payload in journal.records
            if "media_dropped" in payload
        ]
        self.assertEqual(drop_counts, [1])
        self.assertEqual(recorder.pending_bytes, 0)

    def test_release_drains_jobs_releases_sink_once_and_stops_accepting(self) -> None:
        sink = MemorySink()
        recorder = MediaRecorder(
            sink,
            encoder=ByteEncoder(),  # type: ignore[arg-type]
            queue_size=4,
            queue_max_bytes=4096,
        )
        recorder.prepare_recorder()
        frame = np.ones((4, 4, 3), dtype=np.uint8)
        for index in range(3):
            self.assertTrue(
                recorder.submit(
                    frame,
                    frame,
                    make_snapshot(f"capture-{index}", "pothole"),
                )
            )

        self.assertTrue(recorder.release_recorder(timeout=2.0))

        self.assertEqual(
            [record[2].capture_id for record in sink.records],
            ["capture-0", "capture-1", "capture-2"],
        )
        self.assertEqual(sink.prepare_count, 1)
        self.assertEqual(sink.release_count, 1)
        self.assertEqual(recorder.pending_bytes, 0)
        self.assertFalse(recorder.submit(frame, frame, make_snapshot("late", "pothole")))
        self.assertTrue(recorder.release_recorder(timeout=0.0))
        self.assertEqual(sink.release_count, 1)

    def test_memory_limit_rejects_job_without_enqueuing_it(self) -> None:
        sink = MemorySink()
        recorder = MediaRecorder(
            sink,
            encoder=ByteEncoder(),  # type: ignore[arg-type]
            queue_size=2,
            queue_max_bytes=10,
        )
        recorder.prepare_recorder()
        self.addCleanup(lambda: recorder.release_recorder(timeout=2.0))
        frame = np.zeros((2, 2, 3), dtype=np.uint8)  # İki kare toplam 24 bayt.

        self.assertFalse(recorder.submit(frame, frame, make_snapshot("large", "pothole")))
        self.assertTrue(recorder.release_recorder(timeout=2.0))

        self.assertEqual(sink.records, [])
        self.assertEqual(recorder.pending_bytes, 0)


if __name__ == "__main__":
    unittest.main()
