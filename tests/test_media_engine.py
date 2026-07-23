from __future__ import annotations

import threading
import time
import unittest
import uuid
from collections.abc import Sequence
from typing import Any

import numpy as np

from roadvision.engine import EngineState, ProcessingEngine
from roadvision.media import GateDecision, GateObservation, Snapshot
from roadvision.models.base import ModelRunStat
from roadvision.models.detections import DetectedObject
from roadvision.models.manager import AnalysisResult
from roadvision.sources import MediaSource, SourceKind


def wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class StaticSource(MediaSource):
    kind = SourceKind.IMAGE
    display_name = "engine-media-test.jpg"

    def __init__(self) -> None:
        self.frame = np.zeros((24, 32, 3), dtype=np.uint8)
        self.release_count = 0

    @property
    def is_static(self) -> bool:
        return True

    def prepare_source(self) -> None:
        return None

    def get_stream(self, stop_event: threading.Event):
        if not stop_event.is_set():
            yield self.frame.copy()

    def release_source(self) -> None:
        self.release_count += 1


class FakeRegistry:
    def validate_models(self, model_ids) -> None:
        if not model_ids:
            raise ValueError("En az bir model gerekli.")


class FakeManager:
    def __init__(self, *, trace: list[str] | None = None) -> None:
        self.registry = FakeRegistry()
        self.device_label = "cpu"
        self.trace = trace
        self.calls: list[tuple[frozenset[str], bool]] = []
        self.confidence = 0.35

    def run_models(
        self,
        frame: np.ndarray,
        model_ids: frozenset[str],
        *,
        capture_annotations: bool = False,
    ) -> AnalysisResult:
        self.calls.append((model_ids, capture_annotations))
        stats = tuple(self._stat(model_id) for model_id in sorted(model_ids))
        visible = frame.copy()
        annotated = frame.copy()
        annotated[:, :, 1] = 255
        return AnalysisResult(
            frame=visible,
            stats=stats,
            annotated_frame=annotated if capture_annotations else None,
        )

    @staticmethod
    def _stat(model_id: str) -> ModelRunStat:
        detected = DetectedObject(
            class_name=model_id,
            confidence=0.9,
            bbox=(2.0, 3.0, 12.0, 13.0),
        )
        return ModelRunStat(
            model_id=model_id,
            display_name=model_id,
            object_count=1,
            elapsed_ms=4.0,
            objects=(detected,),
        )

    def set_confidence(self, value: float) -> None:
        self.confidence = value

    def set_model_confidence(self, model_id: str, value: float) -> None:
        self.confidence = value

    def set_annotation_enabled(self, model_id: str, enabled: bool) -> None:
        return None

    def set_performance_profile(self, profile) -> None:
        return None

    def release_models(self) -> None:
        if self.trace is not None:
            self.trace.append("manager.release")


class SpyJournal:
    def __init__(self) -> None:
        self.detections: list[dict[str, Any]] = []
        self.app_events: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.finished_runs: list[int] = []
        self._lock = threading.Lock()

    def detection(self, **record: Any) -> None:
        with self._lock:
            self.detections.append(record)

    def app_event(self, *args: Any, **payload: Any) -> None:
        with self._lock:
            self.app_events.append((args, payload))

    def run_finished(self, run_id: int) -> None:
        with self._lock:
            self.finished_runs.append(run_id)


class ScriptedGate:
    def __init__(self, *, accept: bool) -> None:
        self.accept = accept
        self.evaluations: list[tuple[int, tuple[GateObservation, ...], bool]] = []
        self.commits: list[tuple[int, tuple[GateObservation, ...]]] = []
        self.finished_runs: list[int] = []

    def evaluate(
        self,
        run_id: int,
        observations: Sequence[GateObservation],
        *,
        is_static: bool,
        now: float | None = None,
    ) -> GateDecision:
        captured = tuple(observations)
        self.evaluations.append((run_id, captured, is_static))
        if self.accept:
            return GateDecision(models=captured, reason="changed")
        return GateDecision(reason="same_signature")

    def commit(
        self,
        run_id: int,
        observations: Sequence[GateObservation],
        *,
        now: float | None = None,
    ) -> None:
        self.commits.append((run_id, tuple(observations)))

    def finish_run(self, run_id: int) -> None:
        self.finished_runs.append(run_id)


class SpyRecorder:
    enabled = True

    def __init__(
        self,
        gate: ScriptedGate,
        *,
        submit_result: bool = True,
        trace: list[str] | None = None,
    ) -> None:
        self.gate = gate
        self.submit_result = submit_result
        self.trace = trace
        self.prepared = False
        self.submissions: list[tuple[np.ndarray, np.ndarray, Snapshot]] = []

    def prepare_recorder(self) -> None:
        self.prepared = True

    def submit(
        self,
        raw_frame: np.ndarray,
        annotated_frame: np.ndarray,
        snapshot: Snapshot,
    ) -> bool:
        self.submissions.append((raw_frame, annotated_frame, snapshot))
        return self.submit_result

    def release_recorder(self, timeout: float | None = None) -> bool:
        if self.trace is not None:
            self.trace.append("recorder.release")
        return True


class MediaEngineIntegrationTests(unittest.TestCase):
    def _make_engine(
        self,
        *,
        accept: bool,
        trace: list[str] | None = None,
    ) -> tuple[ProcessingEngine, FakeManager, SpyJournal, SpyRecorder, ScriptedGate]:
        gate = ScriptedGate(accept=accept)
        recorder = SpyRecorder(gate, trace=trace)
        journal = SpyJournal()
        manager = FakeManager(trace=trace)
        engine = ProcessingEngine(
            lambda _event: None,
            model_manager=manager,  # type: ignore[arg-type]
            journal=journal,  # type: ignore[arg-type]
            recorder=recorder,  # type: ignore[arg-type]
            gate=gate,  # type: ignore[arg-type]
        )
        self.addCleanup(lambda: engine.shutdown(timeout=2.0))
        return engine, manager, journal, recorder, gate

    def test_accepted_capture_id_matches_journal_correlation(self) -> None:
        engine, manager, journal, recorder, gate = self._make_engine(accept=True)

        engine.start(StaticSource(), {"pothole"})
        self.assertTrue(
            wait_until(lambda: len(recorder.submissions) == 1 and len(journal.detections) == 1)
        )

        snapshot = recorder.submissions[0][2]
        journal_record = journal.detections[0]
        self.assertEqual(journal_record["capture_id"], snapshot.capture_id)
        self.assertEqual(uuid.UUID(snapshot.capture_id).version, 4)
        self.assertEqual(len(gate.commits), 1)
        self.assertTrue(manager.calls[0][1], "Medya açıkken annotation capture istenmedi.")

    def test_rejected_capture_does_not_add_capture_id_to_journal(self) -> None:
        engine, _manager, journal, recorder, gate = self._make_engine(accept=False)

        engine.start(StaticSource(), {"pothole"})
        self.assertTrue(wait_until(lambda: len(journal.detections) == 1))

        self.assertNotIn("capture_id", journal.detections[0])
        self.assertEqual(recorder.submissions, [])
        self.assertEqual(gate.commits, [])

    def test_two_models_in_one_frame_share_one_capture_and_one_submit(self) -> None:
        engine, _manager, journal, recorder, gate = self._make_engine(accept=True)

        engine.start(StaticSource(), {"pothole", "traffic_sign"})
        self.assertTrue(
            wait_until(lambda: len(recorder.submissions) == 1 and len(journal.detections) == 2)
        )

        snapshot = recorder.submissions[0][2]
        capture_ids = {record.get("capture_id") for record in journal.detections}
        self.assertEqual(capture_ids, {snapshot.capture_id})
        self.assertEqual(
            {model.model_id for model in snapshot.models},
            {"pothole", "traffic_sign"},
        )
        self.assertEqual(len(gate.commits), 1)
        self.assertEqual(len(gate.commits[0][1]), 2)

    def test_reprocess_preserves_sequence_and_timestamp_and_marks_snapshot(self) -> None:
        engine, _manager, _journal, recorder, _gate = self._make_engine(accept=True)

        engine.start(StaticSource(), {"pothole"})
        self.assertTrue(wait_until(lambda: len(recorder.submissions) == 1))
        original = recorder.submissions[0][2]

        engine.set_confidence(0.60)
        self.assertTrue(wait_until(lambda: len(recorder.submissions) == 2))
        reprocessed = recorder.submissions[1][2]

        self.assertFalse(original.is_reprocess)
        self.assertTrue(reprocessed.is_reprocess)
        self.assertEqual(reprocessed.frame_sequence, original.frame_sequence)
        self.assertEqual(reprocessed.timestamp, original.timestamp)
        self.assertNotEqual(reprocessed.capture_id, original.capture_id)

    def test_recorder_is_released_before_models(self) -> None:
        trace: list[str] = []
        engine, _manager, _journal, _recorder, _gate = self._make_engine(
            accept=True,
            trace=trace,
        )

        self.assertTrue(engine.shutdown(timeout=2.0))

        self.assertEqual(trace, ["recorder.release", "manager.release"])
        self.assertEqual(engine.state, EngineState.IDLE)


if __name__ == "__main__":
    unittest.main()
