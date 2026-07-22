from __future__ import annotations

import queue
import unittest
from unittest.mock import Mock

from roadvision.engine import EngineEvent, EngineState
from roadvision.ui.app import PREVIEW_PLACEHOLDER, RoadVisionApp


class FakeEngine:
    def __init__(self, state: EngineState) -> None:
        self.state = state
        self.device = "cpu"
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1
        self.state = EngineState.IDLE


class SourceResetTests(unittest.TestCase):
    def make_app(self, state: EngineState) -> RoadVisionApp:
        app = RoadVisionApp.__new__(RoadVisionApp)
        app.engine = FakeEngine(state)  # type: ignore[assignment]
        app._events = queue.Queue()
        app._events.put(EngineEvent(kind="frame"))
        app._events.put(EngineEvent(kind="stopped"))
        app._last_display_frame = object()
        app._photo = object()
        app.preview = Mock()
        app.performance_text = Mock()
        app.status_text = Mock()
        app._set_running_ui = Mock()  # type: ignore[method-assign]
        return app

    def test_source_change_stops_processing_and_resets_preview(self) -> None:
        app = self.make_app(EngineState.RUNNING)

        app._reset_for_source_change("Yeni kaynak seçildi.")

        self.assertEqual(app.engine.stop_calls, 1)  # type: ignore[attr-defined]
        self.assertTrue(app._events.empty())
        self.assertIsNone(app._last_display_frame)
        self.assertIsNone(app._photo)
        app.preview.configure.assert_called_once_with(image="", text=PREVIEW_PLACEHOLDER)
        app.performance_text.set.assert_called_once_with("Aygıt: CPU")
        app.status_text.set.assert_called_once_with("Yeni kaynak seçildi.")
        app._set_running_ui.assert_called_once_with(False)

    def test_source_change_while_idle_does_not_stop_again(self) -> None:
        app = self.make_app(EngineState.IDLE)

        app._reset_for_source_change("Yeni kaynak seçildi.")

        self.assertEqual(app.engine.stop_calls, 0)  # type: ignore[attr-defined]
        app._set_running_ui.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main()
