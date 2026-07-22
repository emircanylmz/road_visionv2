from __future__ import annotations

import queue
import unittest
from unittest.mock import Mock

import numpy as np

from roadvision.engine import EngineEvent, EngineState
from roadvision.ui.app import PREVIEW_PLACEHOLDER, RoadVisionApp


class FakeEngine:
    def __init__(self, state: EngineState, run_id: int | None = 7) -> None:
        self.state = state
        self.device = "cpu"
        self.active_run_id = run_id if state != EngineState.IDLE else None
        self.request_stop_calls = 0
        self.stop_calls = 0
        self.request_shutdown_calls = 0
        self.shutdown_calls = 0

    def request_stop(self) -> int | None:
        self.request_stop_calls += 1
        if self.state == EngineState.IDLE:
            return None
        self.state = EngineState.STOPPING
        return self.active_run_id

    def stop(self, *_args, **_kwargs) -> bool:
        self.stop_calls += 1
        return True

    def request_shutdown(self) -> None:
        self.request_shutdown_calls += 1

    def shutdown(self, *_args, **_kwargs) -> bool:
        self.shutdown_calls += 1
        return True


def configured_value(widget: Mock, key: str):
    for call in reversed(widget.configure.call_args_list):
        if key in call.kwargs:
            return call.kwargs[key]
    raise AssertionError(f"{key!r} hiçbir configure çağrısında ayarlanmadı")


class SourceResetTests(unittest.TestCase):
    def make_app(self, state: EngineState, run_id: int | None = 7) -> RoadVisionApp:
        app = RoadVisionApp.__new__(RoadVisionApp)
        app.engine = FakeEngine(state, run_id)  # type: ignore[assignment]
        app._active_run_id = app.engine.active_run_id  # type: ignore[attr-defined]
        app._closing = False
        app._closed = False
        app._events = queue.Queue()
        app._events.put(EngineEvent(kind="frame", run_id=run_id or 0))
        app._events.put(EngineEvent(kind="stopped", run_id=run_id or 0))
        app._last_display_frame = object()
        app._photo = object()
        app.preview = Mock()
        app.performance_text = Mock()
        app.status_text = Mock()
        app.status_dot = Mock()
        app.start_button = Mock()
        app.root = Mock()
        app.root.winfo_exists.return_value = False
        app._display_frame = Mock()  # type: ignore[method-assign]
        app._source_is_ready = Mock(return_value=True)  # type: ignore[method-assign]
        app._selected_models = Mock(return_value={"pothole"})  # type: ignore[method-assign]
        return app

    def test_source_change_requests_stop_and_immediately_resets_visible_state(self) -> None:
        app = self.make_app(EngineState.RUNNING, run_id=7)

        app._reset_for_source_change("Yeni kaynak seçildi.")

        self.assertEqual(app.engine.request_stop_calls, 1)  # type: ignore[attr-defined]
        self.assertEqual(app.engine.stop_calls, 0)  # type: ignore[attr-defined]
        self.assertEqual(app.engine.state, EngineState.STOPPING)
        self.assertTrue(app._events.empty())
        self.assertIsNone(app._last_display_frame)
        self.assertIsNone(app._photo)
        app.preview.configure.assert_called_once_with(image="", text=PREVIEW_PLACEHOLDER)
        app.performance_text.set.assert_called_once_with("Aygıt: CPU")
        app.status_text.set.assert_called_once_with("Yeni kaynak seçildi.")
        self.assertEqual(configured_value(app.start_button, "text"), "Başlat")
        self.assertEqual(configured_value(app.start_button, "style"), "Accent.TButton")
        self.assertEqual(configured_value(app.start_button, "state"), "disabled")

        # Stop tamamlanana kadar eski run'dan geç gelen kare/terminal olayı yeni
        # kaynak mesajını ve temiz önizlemeyi geri alamaz.
        app.status_text.reset_mock()
        app.performance_text.reset_mock()
        app._display_frame.reset_mock()  # type: ignore[union-attr]
        app._handle_event(
            EngineEvent(
                kind="frame",
                frame=np.ones((4, 4, 3), dtype=np.uint8),
                run_id=7,
            )
        )
        app._handle_event(EngineEvent(kind="stopped", message="eski işlem", run_id=7))
        self.assertIsNone(app._last_display_frame)
        app._display_frame.assert_not_called()  # type: ignore[union-attr]
        app.status_text.set.assert_not_called()
        app.performance_text.set.assert_not_called()

    def test_source_change_while_idle_does_not_request_stop(self) -> None:
        app = self.make_app(EngineState.IDLE, run_id=None)

        app._reset_for_source_change("Yeni kaynak seçildi.")

        self.assertEqual(app.engine.request_stop_calls, 0)  # type: ignore[attr-defined]
        self.assertEqual(app.engine.stop_calls, 0)  # type: ignore[attr-defined]
        self.assertEqual(configured_value(app.start_button, "text"), "Başlat")

    def test_current_run_frame_wins_when_a_stale_frame_arrives_later_in_same_poll(self) -> None:
        app = self.make_app(EngineState.RUNNING, run_id=2)
        app._events = queue.Queue()
        current_frame = np.full((4, 4, 3), 2, dtype=np.uint8)
        stale_frame = np.full((4, 4, 3), 1, dtype=np.uint8)
        app._events.put(
            EngineEvent(
                kind="frame",
                frame=current_frame,
                inference_fps=20.0,
                total_ms=50.0,
                run_id=2,
            )
        )
        app._events.put(
            EngineEvent(
                kind="frame",
                frame=stale_frame,
                inference_fps=99.0,
                total_ms=1.0,
                run_id=1,
            )
        )

        app._poll_events()

        self.assertIs(app._last_display_frame, current_frame)
        app._display_frame.assert_called_once_with(current_frame)  # type: ignore[union-attr]
        app.performance_text.set.assert_called_once()
        self.assertIn("20.0 FPS", app.performance_text.set.call_args.args[0])

    def test_stale_terminal_event_cannot_change_active_run_ui(self) -> None:
        app = self.make_app(EngineState.RUNNING, run_id=2)
        app._set_running_ui = Mock()  # type: ignore[method-assign]

        app._handle_event(EngineEvent(kind="stopped", message="A durdu", run_id=1))

        app.status_text.set.assert_not_called()
        app.performance_text.set.assert_not_called()
        app._set_running_ui.assert_not_called()  # type: ignore[union-attr]

    def test_manual_stop_keeps_run_id_until_terminal_event(self) -> None:
        app = self.make_app(EngineState.RUNNING, run_id=3)

        app._toggle_processing()

        self.assertEqual(app.engine.request_stop_calls, 1)  # type: ignore[attr-defined]
        self.assertEqual(app.engine.stop_calls, 0)  # type: ignore[attr-defined]
        self.assertEqual(app._active_run_id, 3)
        self.assertEqual(configured_value(app.start_button, "text"), "Durduruluyor…")
        self.assertEqual(configured_value(app.start_button, "style"), "Danger.TButton")
        self.assertEqual(configured_value(app.start_button, "state"), "disabled")
        app.status_text.set.assert_called_with("İşlem durduruluyor…")

    def test_close_requests_async_shutdown_without_calling_blocking_wrapper(self) -> None:
        app = self.make_app(EngineState.RUNNING, run_id=3)

        app._on_close()

        self.assertEqual(app.engine.request_shutdown_calls, 1)  # type: ignore[attr-defined]
        self.assertEqual(app.engine.shutdown_calls, 0)  # type: ignore[attr-defined]

    def test_poll_heartbeat_continues_while_async_shutdown_is_pending(self) -> None:
        app = self.make_app(EngineState.STOPPING, run_id=3)
        app._closing = True
        app._active_run_id = None
        app._events = queue.Queue()
        app.root.winfo_exists.return_value = True

        app._poll_events()

        app.root.after.assert_called_once_with(33, app._poll_events)
        app.root.destroy.assert_not_called()

    def test_shutdown_complete_destroys_window_once(self) -> None:
        app = self.make_app(EngineState.STOPPING, run_id=3)
        app._closing = True
        app._active_run_id = None

        app._handle_event(EngineEvent(kind="shutdown_complete"))
        app._handle_event(EngineEvent(kind="shutdown_complete"))

        self.assertTrue(app._closed)
        app.root.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
