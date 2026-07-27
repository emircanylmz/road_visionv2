from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

import numpy as np

from roadvision.models.manager import ModelManager


class FakeAdapter:
    def __init__(self, spec, barrier: threading.Barrier, thread_names: set[str], lock: threading.Lock) -> None:
        self.spec = spec
        self.device = "cpu"
        self._barrier = barrier
        self._thread_names = thread_names
        self._lock = lock

    def predict(self, frame):
        with self._lock:
            self._thread_names.add(threading.current_thread().name)
        self._barrier.wait(timeout=2.0)
        return frame

    def annotate(self, frame, _result):
        return frame, 0


class HiddenAnnotationAdapter:
    def __init__(self, spec) -> None:
        self.spec = spec
        self.device = "cpu"
        self.predict_calls = 0
        self.annotate_calls = 0

    def predict(self, frame):
        self.predict_calls += 1
        return frame

    def annotate(self, frame, _result):
        self.annotate_calls += 1
        frame[:, :, 0] = 255
        return frame, 3

    def count_detections(self, _result):
        return 3


class ModelParallelismTests(unittest.TestCase):
    def test_multiple_cpu_models_use_distinct_pool_workers(self) -> None:
        # Havuz genişliği `min(4, cpu // 3)` ile hesaplanır; 6'dan az
        # çekirdekli CI makinelerinde tek worker kalır ve Barrier(2) zaman
        # aşımına uğrardı. Çekirdek sayısını sabitlemek testi her ortamda
        # deterministik yapar (bloklanan barrier GIL'i bıraktığından iki
        # worker tek fiziksel çekirdekte de buluşabilir).
        with patch("roadvision.models.manager.os.cpu_count", return_value=12):
            manager = ModelManager(device="cpu")
        specs = manager.get_available_models()[:2]
        barrier = threading.Barrier(2)
        names: set[str] = set()
        lock = threading.Lock()
        adapters = {spec.id: FakeAdapter(spec, barrier, names, lock) for spec in specs}
        manager.prepare_model = lambda model_id: adapters[model_id]  # type: ignore[method-assign]

        result = manager.run_models(
            np.zeros((32, 32, 3), dtype=np.uint8),
            frozenset(adapters),
        )

        self.assertEqual(len(result.stats), 2)
        self.assertEqual(len(names), 2)
        self.assertTrue(all(name.startswith("roadvision-model") for name in names))
        manager.release_models()

    def test_hidden_annotation_still_runs_detection_without_drawing(self) -> None:
        manager = ModelManager(device="cpu")
        spec = manager.get_available_models()[0]
        adapter = HiddenAnnotationAdapter(spec)
        manager.prepare_model = lambda _model_id: adapter  # type: ignore[method-assign]
        manager.set_annotation_enabled(spec.id, False)
        frame = np.zeros((32, 32, 3), dtype=np.uint8)

        result = manager.run_models(frame, frozenset({spec.id}))

        self.assertEqual(adapter.predict_calls, 1)
        self.assertEqual(adapter.annotate_calls, 0)
        self.assertEqual(result.stats[0].object_count, 3)
        self.assertTrue(np.array_equal(result.frame, frame))
        manager.release_models()

    def test_media_canvas_is_annotated_even_when_ui_annotation_is_hidden(self) -> None:
        manager = ModelManager(device="cpu")
        spec = manager.get_available_models()[0]
        adapter = HiddenAnnotationAdapter(spec)
        manager.prepare_model = lambda _model_id: adapter  # type: ignore[method-assign]
        manager.set_annotation_enabled(spec.id, False)
        frame = np.zeros((32, 32, 3), dtype=np.uint8)

        result = manager.run_models(
            frame,
            frozenset({spec.id}),
            capture_annotations=True,
        )

        self.assertEqual(adapter.predict_calls, 1)
        self.assertEqual(adapter.annotate_calls, 1)
        self.assertTrue(np.array_equal(result.frame, frame))
        self.assertIsNotNone(result.annotated_frame)
        assert result.annotated_frame is not None
        self.assertTrue(np.all(result.annotated_frame[:, :, 0] == 255))
        manager.release_models()

    def test_model_confidence_only_updates_target_adapter(self) -> None:
        manager = ModelManager(device="cpu")
        first, second = manager.get_available_models()[:2]
        first_adapter = type("Adapter", (), {"confidence": 0.35})()
        second_adapter = type("Adapter", (), {"confidence": 0.35})()
        manager._models = {  # type: ignore[assignment]
            first.id: first_adapter,
            second.id: second_adapter,
        }

        manager.set_model_confidence(first.id, 0.72)

        self.assertEqual(first_adapter.confidence, 0.72)
        self.assertEqual(second_adapter.confidence, 0.35)
        manager._models.clear()
        manager.release_models()

    @patch("roadvision.models.manager.YoloModelAdapter")
    def test_model_confidence_is_used_when_adapter_loads(self, adapter_class) -> None:
        manager = ModelManager(device="cpu")
        spec = manager.get_available_models()[0]
        adapter_class.return_value.device = "cpu"
        manager.set_model_confidence(spec.id, 0.64)

        manager.prepare_model(spec.id)

        self.assertEqual(adapter_class.call_args.args[2], 0.64)
        manager.release_models()


if __name__ == "__main__":
    unittest.main()
