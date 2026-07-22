from __future__ import annotations

import threading
import time
import unittest

import numpy as np

from roadvision.engine import EngineEvent, EngineState, ProcessingEngine
from roadvision.models.manager import AnalysisResult
from roadvision.models.registry import ModelRegistry
from roadvision.sources import MediaSource, SourceKind


class StaticSource(MediaSource):
    kind = SourceKind.IMAGE
    display_name = "test.jpg"

    def __init__(self) -> None:
        self.prepared = False
        self.released = False

    @property
    def is_static(self) -> bool:
        return True

    def prepare_source(self) -> None:
        self.prepared = True

    def get_stream(self, stop_event: threading.Event):
        if not stop_event.is_set():
            yield np.zeros((24, 32, 3), dtype=np.uint8)

    def release_source(self) -> None:
        self.released = True


class FakeManager:
    def __init__(self) -> None:
        self.registry = ModelRegistry()
        self.device = "cpu"
        self.calls: list[frozenset[str]] = []
        self.confidence = 0.35

    def run_models(self, frame, model_ids):
        self.calls.append(model_ids)
        output = frame.copy()
        output[:, :, 1] = len(model_ids)
        return AnalysisResult(output, ())

    def set_confidence(self, value: float) -> None:
        self.confidence = value

    def release_models(self) -> None:
        pass


def wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class EngineTests(unittest.TestCase):
    def test_static_frame_is_reprocessed_after_model_change(self) -> None:
        events: list[EngineEvent] = []
        manager = FakeManager()
        source = StaticSource()
        engine = ProcessingEngine(events.append, model_manager=manager)  # type: ignore[arg-type]

        engine.start(source, {"pothole"})
        self.assertTrue(wait_until(lambda: len(manager.calls) >= 1))
        engine.update_models({"pothole", "traffic_sign"})
        self.assertTrue(wait_until(lambda: len(manager.calls) >= 2))

        self.assertEqual(manager.calls[0], frozenset({"pothole"}))
        self.assertEqual(manager.calls[-1], frozenset({"pothole", "traffic_sign"}))
        self.assertTrue(any(event.kind == "frame" for event in events))
        self.assertEqual(engine.state, EngineState.RUNNING)
        engine.stop()
        self.assertEqual(engine.state, EngineState.IDLE)
        self.assertTrue(source.released)

    def test_start_requires_at_least_one_model(self) -> None:
        engine = ProcessingEngine(lambda _event: None, model_manager=FakeManager())  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "en az bir model"):
            engine.start(StaticSource(), set())


if __name__ == "__main__":
    unittest.main()
