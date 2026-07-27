from __future__ import annotations

import threading
import time
import unittest

import numpy as np

from roadvision.engine import EngineEvent, EngineState, ProcessingEngine
from roadvision.logbook import PersistenceCheckpoint
from roadvision.models.manager import AnalysisResult
from roadvision.models.registry import ModelRegistry
from roadvision.sources import MediaSource, SourceKind


class StaticSource(MediaSource):
    kind = SourceKind.IMAGE
    display_name = "test.jpg"

    def __init__(self) -> None:
        self.prepared = False
        self.release_count = 0

    @property
    def released(self) -> bool:
        return self.release_count > 0

    @property
    def is_static(self) -> bool:
        return True

    def prepare_source(self) -> None:
        self.prepared = True

    def get_stream(self, stop_event: threading.Event):
        if not stop_event.is_set():
            yield np.zeros((24, 32, 3), dtype=np.uint8)

    def release_source(self) -> None:
        self.release_count += 1


class BlockingSource(MediaSource):
    kind = SourceKind.VIDEO
    display_name = "blocked.mp4"

    def __init__(self) -> None:
        self.prepared = threading.Event()
        self.stream_entered = threading.Event()
        self.allow_exit = threading.Event()
        self.released = threading.Event()
        self.release_count = 0

    @property
    def is_static(self) -> bool:
        return False

    def prepare_source(self) -> None:
        self.prepared.set()

    def get_stream(self, stop_event: threading.Event):
        self.stream_entered.set()
        self.allow_exit.wait()
        if not stop_event.is_set():
            yield np.zeros((24, 32, 3), dtype=np.uint8)

    def release_source(self) -> None:
        self.release_count += 1
        self.released.set()


class BlockingPrepareSource(MediaSource):
    kind = SourceKind.VIDEO
    display_name = "preparing.mp4"

    def __init__(self) -> None:
        self.prepare_entered = threading.Event()
        self.allow_prepare = threading.Event()
        self.release_count = 0

    @property
    def is_static(self) -> bool:
        return False

    def prepare_source(self) -> None:
        self.prepare_entered.set()
        self.allow_prepare.wait()

    def get_stream(self, stop_event: threading.Event):
        raise AssertionError("Durdurulan kaynak stream okumaya geçmemeliydi.")
        yield  # pragma: no cover

    def release_source(self) -> None:
        self.release_count += 1


class FakeManager:
    def __init__(self) -> None:
        self.registry = ModelRegistry()
        self.device = "cpu"
        self.device_label = "cpu"
        self.calls: list[frozenset[str]] = []
        self.confidence = 0.35
        self.release_count = 0

    def run_models(self, frame, model_ids):
        self.calls.append(model_ids)
        output = frame.copy()
        output[:, :, 1] = len(model_ids)
        return AnalysisResult(output, ())

    def set_confidence(self, value: float) -> None:
        self.confidence = value

    def release_models(self) -> None:
        self.release_count += 1


class BlockingInferenceManager(FakeManager):
    def __init__(self) -> None:
        super().__init__()
        self.inference_started = threading.Event()
        self.allow_inference = threading.Event()
        self.inference_finished = threading.Event()

    def run_models(self, frame, model_ids):
        self.calls.append(model_ids)
        self.inference_started.set()
        self.allow_inference.wait()
        self.inference_finished.set()
        return AnalysisResult(frame.copy(), ())


class ShutdownTrackingManager(FakeManager):
    def __init__(self, source: BlockingSource) -> None:
        super().__init__()
        self.source = source
        self.models_released = threading.Event()
        self.released_after_source = False
        self.worker_threads: tuple[threading.Thread, ...] = ()
        self.released_after_workers = False

    def release_models(self) -> None:
        self.release_count += 1
        self.released_after_source = self.source.released.is_set()
        self.released_after_workers = bool(self.worker_threads) and all(
            not worker.is_alive() for worker in self.worker_threads
        )
        self.models_released.set()


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
        self.assertEqual(source.release_count, 1)

    def test_archive_ready_follows_terminal_event_after_persistence_checkpoints(self) -> None:
        events: list[EngineEvent] = []
        engine = ProcessingEngine(events.append, model_manager=FakeManager())  # type: ignore[arg-type]

        run_id = engine.start(StaticSource(), {"pothole"})
        self.assertTrue(
            wait_until(
                lambda: any(
                    event.kind == "frame" and event.run_id == run_id
                    for event in events
                )
            )
        )
        self.assertTrue(engine.stop(timeout=1.0))
        self.assertTrue(
            wait_until(
                lambda: any(
                    event.kind == "archive_ready" and event.run_id == run_id
                    for event in events
                )
            )
        )

        ordered = [
            event.kind
            for event in events
            if event.run_id == run_id
            and event.kind in {"stopped", "archive_ready"}
        ]
        self.assertEqual(ordered, ["stopped", "archive_ready"])
        ready = next(event for event in events if event.kind == "archive_ready")
        self.assertTrue(ready.journal_persisted)
        self.assertTrue(ready.media_persisted)

    def test_pending_archive_checkpoints_coalesce_to_latest_run_without_threads(self) -> None:
        events: list[EngineEvent] = []
        engine = ProcessingEngine(events.append, model_manager=FakeManager())  # type: ignore[arg-type]

        class CheckpointComponent:
            def __init__(self) -> None:
                self.requests: list[PersistenceCheckpoint] = []

            def request_checkpoint(self) -> PersistenceCheckpoint:
                checkpoint = PersistenceCheckpoint()
                self.requests.append(checkpoint)
                return checkpoint

        journal = CheckpointComponent()
        recorder = CheckpointComponent()
        engine._journal = journal  # type: ignore[assignment]
        engine._recorder = recorder  # type: ignore[assignment]

        engine._schedule_archive_checkpoint(1)
        engine._schedule_archive_checkpoint(2)

        self.assertEqual(len(journal.requests), 1)
        self.assertEqual(len(recorder.requests), 1)
        # Bayat run başarısız sonuçlansa da ayrı refresh olayı üretmemeli.
        journal.requests[0].resolve(False)
        recorder.requests[0].resolve(True)
        self.assertEqual(len(journal.requests), 2)
        self.assertEqual(len(recorder.requests), 2)
        self.assertFalse(any(event.kind == "archive_ready" for event in events))

        journal.requests[1].resolve(True)
        # En güncel run'da bir kalıcılık kolu başarısız olsa bile tamamlanma
        # sınırı arşivin mevcut DB durumuyla yenilenmesini tetiklemelidir.
        recorder.requests[1].resolve(False)

        ready = [event for event in events if event.kind == "archive_ready"]
        self.assertEqual([(event.kind, event.run_id) for event in ready], [("archive_ready", 2)])
        self.assertTrue(ready[0].journal_persisted)
        self.assertFalse(ready[0].media_persisted)

    def test_start_requires_at_least_one_model(self) -> None:
        engine = ProcessingEngine(lambda _event: None, model_manager=FakeManager())  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "en az bir model"):
            engine.start(StaticSource(), set())

    def test_request_stop_returns_without_waiting_and_keeps_run_stopping(self) -> None:
        events: list[EngineEvent] = []
        source = BlockingSource()
        engine = ProcessingEngine(events.append, model_manager=FakeManager())  # type: ignore[arg-type]
        self.addCleanup(lambda: engine.stop(timeout=1.0))
        self.addCleanup(source.allow_exit.set)

        run_id = engine.start(source, {"pothole"})
        self.assertTrue(source.stream_entered.wait(1.0))
        self.assertTrue(wait_until(lambda: engine.state == EngineState.RUNNING))

        returned = threading.Event()
        result: list[int | None] = []

        def request_stop() -> None:
            result.append(engine.request_stop())
            returned.set()

        caller = threading.Thread(target=request_stop, daemon=True)
        caller.start()
        self.assertTrue(returned.wait(0.5), "request_stop bloke worker'ı bekledi")
        caller.join(timeout=0.25)

        self.assertEqual(result, [run_id])
        self.assertEqual(engine.state, EngineState.STOPPING)
        self.assertEqual(engine.active_run_id, run_id)
        self.assertEqual(source.release_count, 0)
        self.assertFalse(any(event.kind == "stopped" for event in events))
        with self.assertRaisesRegex(RuntimeError, "tamamen durmadan"):
            engine.start(StaticSource(), {"pothole"})

        source.allow_exit.set()
        self.assertTrue(wait_until(lambda: engine.state == EngineState.IDLE))
        self.assertTrue(source.released.wait(1.0))
        self.assertEqual(source.release_count, 1)
        self.assertTrue(wait_until(lambda: any(event.kind == "stopped" for event in events)))
        stopped = [event for event in events if event.kind == "stopped"]
        self.assertEqual([event.run_id for event in stopped], [run_id])

    def test_repeated_stop_requests_release_once_and_emit_one_terminal_event(self) -> None:
        events: list[EngineEvent] = []
        source = BlockingSource()
        engine = ProcessingEngine(events.append, model_manager=FakeManager())  # type: ignore[arg-type]
        self.addCleanup(lambda: engine.stop(timeout=1.0))
        self.addCleanup(source.allow_exit.set)

        run_id = engine.start(source, {"pothole"})
        self.assertTrue(source.stream_entered.wait(1.0))

        self.assertEqual(engine.request_stop(), run_id)
        context = engine._active_run
        self.assertIsNotNone(context)
        assert context is not None
        first_reaper = context.reaper_thread
        self.assertIsNotNone(first_reaper)
        self.assertEqual(engine.request_stop(), run_id)
        self.assertIs(context.reaper_thread, first_reaper)
        self.assertFalse(engine.stop(timeout=0.0))
        self.assertEqual(engine.state, EngineState.STOPPING)

        source.allow_exit.set()
        self.assertTrue(wait_until(lambda: engine.state == EngineState.IDLE))
        self.assertEqual(source.release_count, 1)
        self.assertTrue(wait_until(lambda: any(event.kind == "stopped" for event in events)))
        terminal = [event for event in events if event.kind in {"stopped", "error"}]
        self.assertEqual([(event.kind, event.run_id) for event in terminal], [("stopped", run_id)])

    def test_stop_during_source_prepare_suppresses_late_run_events(self) -> None:
        events: list[EngineEvent] = []
        source = BlockingPrepareSource()
        engine = ProcessingEngine(events.append, model_manager=FakeManager())  # type: ignore[arg-type]
        self.addCleanup(lambda: engine.stop(timeout=1.0))
        self.addCleanup(source.allow_prepare.set)

        run_id = engine.start(source, {"pothole"})
        self.assertTrue(source.prepare_entered.wait(1.0))
        self.assertEqual(engine.request_stop(), run_id)
        self.assertEqual(engine.state, EngineState.STOPPING)

        source.allow_prepare.set()
        self.assertTrue(wait_until(lambda: engine.state == EngineState.IDLE))
        self.assertEqual(source.release_count, 1)
        self.assertTrue(wait_until(lambda: any(event.kind == "stopped" for event in events)))
        late_kinds = {
            event.kind
            for event in events
            if event.run_id == run_id
            and event.kind in {"started", "frame", "error", "source_ended"}
        }
        self.assertEqual(late_kinds, set())

    def test_frame_finishing_after_stop_is_not_emitted(self) -> None:
        events: list[EngineEvent] = []
        source = StaticSource()
        manager = BlockingInferenceManager()
        engine = ProcessingEngine(events.append, model_manager=manager)  # type: ignore[arg-type]
        self.addCleanup(lambda: engine.stop(timeout=1.0))
        self.addCleanup(manager.allow_inference.set)

        run_id = engine.start(source, {"pothole"})
        self.assertTrue(manager.inference_started.wait(1.0))

        self.assertEqual(engine.request_stop(), run_id)
        manager.allow_inference.set()

        self.assertTrue(manager.inference_finished.wait(1.0))
        self.assertTrue(wait_until(lambda: engine.state == EngineState.IDLE))
        self.assertFalse(
            any(event.kind == "frame" and event.run_id == run_id for event in events),
            "stop sırasında tamamlanan inference karesi yayımlandı",
        )
        self.assertEqual(source.release_count, 1)

    def test_request_stop_linearizes_with_inflight_frame_callback(self) -> None:
        events: list[EngineEvent] = []
        frame_callback_entered = threading.Event()
        allow_frame_callback = threading.Event()
        stop_returned = threading.Event()

        def callback(event: EngineEvent) -> None:
            if event.kind == "frame":
                frame_callback_entered.set()
                allow_frame_callback.wait()
            events.append(event)

        engine = ProcessingEngine(callback, model_manager=FakeManager())  # type: ignore[arg-type]
        self.addCleanup(lambda: engine.stop(timeout=1.0))
        self.addCleanup(allow_frame_callback.set)

        run_id = engine.start(StaticSource(), {"pothole"})
        self.assertTrue(frame_callback_entered.wait(1.0))

        caller = threading.Thread(
            target=lambda: (engine.request_stop(), stop_returned.set()),
            daemon=True,
        )
        caller.start()
        self.assertFalse(
            stop_returned.wait(0.10),
            "stop, yayımlanmakta olan frame callback'ini geçti",
        )

        allow_frame_callback.set()
        self.assertTrue(stop_returned.wait(1.0))
        caller.join(timeout=0.25)
        self.assertTrue(wait_until(lambda: engine.state == EngineState.IDLE))
        self.assertTrue(wait_until(lambda: any(event.kind == "stopped" for event in events)))
        run_events = [
            event.kind
            for event in events
            if event.run_id == run_id and event.kind in {"frame", "stopped"}
        ]
        self.assertEqual(run_events, ["frame", "stopped"])

    def test_run_ids_are_monotonic_and_stale_context_cannot_emit_into_new_run(self) -> None:
        events: list[EngineEvent] = []
        engine = ProcessingEngine(events.append, model_manager=FakeManager())  # type: ignore[arg-type]
        first_source = BlockingSource()
        second_source = BlockingSource()
        self.addCleanup(lambda: engine.stop(timeout=1.0))
        self.addCleanup(first_source.allow_exit.set)
        self.addCleanup(second_source.allow_exit.set)

        first_run_id = engine.start(first_source, {"pothole"})
        self.assertTrue(first_source.stream_entered.wait(1.0))
        first_context = engine._active_run
        self.assertIsNotNone(first_context)
        engine.request_stop()
        first_source.allow_exit.set()
        assert first_context is not None
        self.assertTrue(first_context.finished_event.wait(1.0))
        self.assertEqual(engine.state, EngineState.IDLE)

        second_run_id = engine.start(second_source, {"pothole"})
        self.assertTrue(second_source.stream_entered.wait(1.0))
        self.assertGreater(second_run_id, first_run_id)
        self.assertEqual(engine.active_run_id, second_run_id)

        engine._emit_for_context(
            first_context,
            EngineEvent(kind="status", message="stale-A", run_id=first_run_id),
        )
        self.assertFalse(any(event.message == "stale-A" for event in events))

        engine.request_stop()
        second_source.allow_exit.set()
        self.assertTrue(wait_until(lambda: engine.state == EngineState.IDLE))
        started_ids = [event.run_id for event in events if event.kind == "started"]
        self.assertEqual(started_ids, [first_run_id, second_run_id])

    def test_request_shutdown_is_nonblocking_and_releases_models_after_workers(self) -> None:
        events: list[EngineEvent] = []
        source = BlockingSource()
        manager = ShutdownTrackingManager(source)
        engine = ProcessingEngine(events.append, model_manager=manager)  # type: ignore[arg-type]
        self.addCleanup(lambda: engine.shutdown(timeout=1.0))
        self.addCleanup(source.allow_exit.set)

        run_id = engine.start(source, {"pothole"})
        self.assertTrue(source.stream_entered.wait(1.0))
        context = engine._active_run
        self.assertIsNotNone(context)
        assert context is not None
        assert context.capture_thread is not None
        assert context.inference_thread is not None
        manager.worker_threads = (context.capture_thread, context.inference_thread)

        returned = threading.Event()
        caller = threading.Thread(
            target=lambda: (engine.request_shutdown(), returned.set()),
            daemon=True,
        )
        caller.start()
        self.assertTrue(returned.wait(0.5), "request_shutdown worker'ı bekledi")
        caller.join(timeout=0.25)
        self.assertEqual(engine.state, EngineState.STOPPING)
        self.assertFalse(manager.models_released.is_set())
        self.assertEqual(source.release_count, 0)

        source.allow_exit.set()
        self.assertTrue(manager.models_released.wait(1.0))
        self.assertTrue(engine.shutdown(timeout=1.0))
        self.assertTrue(manager.released_after_source)
        self.assertTrue(manager.released_after_workers)
        self.assertEqual(manager.release_count, 1)
        self.assertEqual(source.release_count, 1)
        terminal = [event for event in events if event.kind in {"stopped", "shutdown_complete"}]
        self.assertEqual(
            [(event.kind, event.run_id) for event in terminal],
            [("stopped", run_id), ("shutdown_complete", 0)],
        )

    def test_shutdown_complete_cannot_overtake_inflight_stopped_callback(self) -> None:
        events: list[EngineEvent] = []
        stopped_callback_entered = threading.Event()
        allow_stopped_callback = threading.Event()
        shutdown_returned = threading.Event()

        def callback(event: EngineEvent) -> None:
            if event.kind == "stopped":
                stopped_callback_entered.set()
                allow_stopped_callback.wait()
            events.append(event)

        source = BlockingSource()
        engine = ProcessingEngine(callback, model_manager=FakeManager())  # type: ignore[arg-type]
        self.addCleanup(lambda: engine.shutdown(timeout=1.0))
        self.addCleanup(allow_stopped_callback.set)
        self.addCleanup(source.allow_exit.set)

        run_id = engine.start(source, {"pothole"})
        self.assertTrue(source.stream_entered.wait(1.0))
        self.assertEqual(engine.request_stop(), run_id)
        source.allow_exit.set()
        self.assertTrue(stopped_callback_entered.wait(1.0))

        caller = threading.Thread(
            target=lambda: (engine.request_shutdown(), shutdown_returned.set()),
            daemon=True,
        )
        caller.start()
        self.assertFalse(
            shutdown_returned.wait(0.10),
            "shutdown isteği terminal stopped callback'ini geçti",
        )

        allow_stopped_callback.set()
        self.assertTrue(shutdown_returned.wait(1.0))
        caller.join(timeout=0.25)
        self.assertTrue(engine.shutdown(timeout=1.0))
        terminal = [
            (event.kind, event.run_id)
            for event in events
            if event.kind in {"stopped", "shutdown_complete"}
        ]
        self.assertEqual(terminal, [("stopped", run_id), ("shutdown_complete", 0)])

    def test_reentrant_shutdown_from_stopped_callback_preserves_terminal_order(self) -> None:
        events: list[EngineEvent] = []
        stopped_callback_completed = threading.Event()
        source = BlockingSource()
        manager = FakeManager()
        engine: ProcessingEngine

        def callback(event: EngineEvent) -> None:
            if event.kind == "stopped":
                engine.request_shutdown()
            events.append(event)
            if event.kind == "stopped":
                stopped_callback_completed.set()

        engine = ProcessingEngine(callback, model_manager=manager)  # type: ignore[arg-type]
        self.addCleanup(lambda: engine.shutdown(timeout=1.0))
        self.addCleanup(source.allow_exit.set)

        run_id = engine.start(source, {"pothole"})
        self.assertTrue(source.stream_entered.wait(1.0))
        self.assertEqual(engine.request_stop(), run_id)
        source.allow_exit.set()

        self.assertTrue(stopped_callback_completed.wait(1.0))
        self.assertTrue(engine.shutdown(timeout=1.0))
        terminal = [
            (event.kind, event.run_id)
            for event in events
            if event.kind in {"stopped", "shutdown_complete"}
        ]
        self.assertEqual(terminal, [("stopped", run_id), ("shutdown_complete", 0)])
        self.assertEqual(manager.release_count, 1)


if __name__ == "__main__":
    unittest.main()
