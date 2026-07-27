from __future__ import annotations

import queue
import types
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from roadvision.archive import TypeCount
from roadvision.engine import EngineEvent
from roadvision.ui.app import RoadVisionApp
from roadvision.ui.archive_page import ArchivePage


class ArchiveAppIntegrationTests(unittest.TestCase):
    def make_app(self) -> RoadVisionApp:
        app = RoadVisionApp.__new__(RoadVisionApp)
        app.root = Mock()
        app.status_text = Mock()
        app.status_dot = Mock()
        app.start_button = Mock()
        app.performance_text = Mock()
        app._journal = Mock()
        app._events = queue.Queue()
        app._active_run_id = None
        app._closing = False
        app._closed = False
        app._archive_poll_error_reported = False
        app._archive_fetcher = None
        app._snapshot_fetcher = None
        app._snapshot_viewer = None
        app._snapshot_generation = 0
        app._snapshot_capture_id = None
        app._snapshot_capture_time = None
        app._snapshot_retry_attempted = False
        app._last_display_frame = None
        app._update_start_availability = Mock()  # type: ignore[method-assign]
        return app

    @patch("roadvision.ui.app.SnapshotViewerWindow")
    def test_archive_snapshot_controller_creates_viewer_and_preserves_time(
        self,
        viewer_class: Mock,
    ) -> None:
        app = self.make_app()
        app._snapshot_fetcher = Mock()
        app._snapshot_fetcher.request.return_value = 9
        viewer = viewer_class.return_value
        viewer.exists.return_value = True
        captured_at = datetime(
            2026,
            7,
            24,
            10,
            30,
            tzinfo=ZoneInfo("Europe/Istanbul"),
        )
        capture_id = "035de335-28d6-4c31-9d7d-54fc6ca076ff"

        app._open_snapshot_capture(capture_id, captured_at)

        viewer_class.assert_called_once()
        viewer.show_loading.assert_called_once_with(capture_id)
        app._snapshot_fetcher.request.assert_called_once_with(capture_id)
        self.assertEqual(app._snapshot_generation, 9)
        self.assertEqual(app._snapshot_capture_id, capture_id)
        self.assertEqual(app._snapshot_capture_time, captured_at.timestamp())
        self.assertFalse(app._snapshot_retry_attempted)

    def test_logbook_path_delegates_to_shared_snapshot_controller(self) -> None:
        app = self.make_app()
        app._snapshot_fetcher = Mock()
        app.log_tree = Mock()
        app.log_tree.selection.return_value = ("row-1",)
        app._log_capture_ids = {"row-1": "capture-1"}
        app._log_capture_times = {"row-1": 123.5}
        app._open_snapshot_capture = Mock()  # type: ignore[method-assign]

        app._open_selected_snapshot()

        app._open_snapshot_capture.assert_called_once_with("capture-1", 123.5)

    def test_archive_render_error_does_not_stop_poll_heartbeat(self) -> None:
        app = self.make_app()
        app.archive_page = Mock()
        app.archive_page.poll_results.side_effect = RuntimeError("render")
        app.root.winfo_exists.return_value = True

        app._poll_events()
        app._poll_events()

        self.assertEqual(app._journal.app_event.call_count, 1)
        self.assertEqual(app.root.after.call_count, 2)
        app.root.after.assert_called_with(33, app._poll_events)

    def test_close_is_two_phase_for_archive_and_snapshot_fetchers(self) -> None:
        app = self.make_app()
        app.archive_page = Mock()
        app._archive_fetcher = Mock()
        app._snapshot_fetcher = Mock()
        app.engine = Mock()
        app._discard_pending_events = Mock()  # type: ignore[method-assign]

        app._on_close()

        app.archive_page.begin_close.assert_called_once_with()
        app._archive_fetcher.close.assert_called_once_with(timeout=0.0)
        app._snapshot_fetcher.close.assert_called_once_with(timeout=0.0)
        app.engine.request_shutdown.assert_called_once_with()

        app._handle_event(EngineEvent(kind="shutdown_complete"))

        app._archive_fetcher.close.assert_called_with(timeout=0.25)
        app._snapshot_fetcher.close.assert_called_with(timeout=0.25)
        app._journal.release_journal.assert_called_once_with()
        app.root.destroy.assert_called_once_with()

    def test_tab_activation_and_terminal_event_mark_archive_dirty(self) -> None:
        app = self.make_app()
        page = Mock()
        page.__str__ = Mock(return_value=".archive")
        app.archive_page = page
        app.content_tabs = Mock()
        app.content_tabs.select.return_value = ".archive"

        app._on_content_tab_changed()
        app._mark_archive_dirty()

        page.activate.assert_called_once_with()
        page.mark_dirty.assert_called_once_with(delay_ms=750)

    def test_failed_persistence_settlement_refreshes_archive_without_active_run(
        self,
    ) -> None:
        app = self.make_app()
        app.archive_page = Mock()
        app.root.winfo_exists.return_value = True

        app._events.put(
            EngineEvent(
                kind="archive_ready",
                run_id=42,
                journal_persisted=True,
                media_persisted=False,
            )
        )
        app._poll_events()

        app.archive_page.mark_dirty.assert_called_once_with(delay_ms=0)
        app.root.after.assert_called_once_with(33, app._poll_events)

    def test_snapshot_poll_rejects_same_generation_for_other_capture(self) -> None:
        app = self.make_app()
        app._snapshot_generation = 7
        app._snapshot_capture_id = "wanted"
        app._snapshot_viewer = Mock()
        app._snapshot_viewer.exists.return_value = True
        app._snapshot_fetcher = Mock()
        app._snapshot_fetcher.drain.return_value = [
            types.SimpleNamespace(
                generation=7,
                capture_id="other",
                status="error",
                bundle=None,
                message="eski sonuç",
            )
        ]

        app._poll_snapshot_results()

        app._snapshot_viewer.show_error.assert_not_called()
        app._snapshot_viewer.show_bundle.assert_not_called()
        app._snapshot_viewer.show_not_found.assert_not_called()

    def test_archive_map_restores_lazy_refresh_after_window_restore(self) -> None:
        page = ArchivePage.__new__(ArchivePage)
        page._closing = False
        page.activate = Mock()  # type: ignore[method-assign]

        page._mapped(types.SimpleNamespace(widget=page))

        page.activate.assert_called_once_with()

    def test_archive_summary_counts_only_selected_type_facets(self) -> None:
        page = ArchivePage.__new__(ArchivePage)
        page.state = Mock()
        page.state.selection.selected_type_ids = frozenset({1, 3})
        page.state.counts = (
            TypeCount(1, 7),
            TypeCount(2, 99),
            TypeCount(3, 4),
        )

        self.assertEqual(page._selected_detection_count(), 11)


if __name__ == "__main__":
    unittest.main()
