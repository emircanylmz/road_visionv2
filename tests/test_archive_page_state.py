from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from roadvision.archive import (
    DetectionFilter,
    DetectionPage,
    DetectionRow,
    ModelNode,
    PageCursor,
    SortColumn,
    TypeNode,
)
from roadvision.archive_state import (
    ArchiveState,
    ArchiveViewState,
    FilterDraft,
    ISTANBUL_TZ,
    PaginationState,
    SelectionState,
    TimePreset,
    TypeSelectionModel,
    parse_local_datetime,
    resolve_time_range,
)


def load_archive_page_class():
    """UI package __init__'ini çalıştırmadan ince Tk modülünü yükler."""

    ui_dir = Path(__file__).resolve().parents[1] / "roadvision" / "ui"
    package_name = "roadvision.ui"
    module_name = "roadvision.ui._archive_page_headless_test"
    previous_package = sys.modules.get(package_name)
    previous_module = sys.modules.get(module_name)
    if previous_package is None:
        package = types.ModuleType(package_name)
        package.__package__ = package_name
        package.__path__ = [str(ui_dir)]  # type: ignore[attr-defined]
        sys.modules[package_name] = package

    spec = importlib.util.spec_from_file_location(
        module_name,
        ui_dir / "archive_page.py",
    )
    if spec is None or spec.loader is None:
        raise AssertionError("archive_page.py yükleme sözleşmesi kurulamadı")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module.ArchivePage, module.FilterBar
    finally:
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
        if previous_package is None:
            sys.modules.pop(package_name, None)


ArchivePage, FilterBar = load_archive_page_class()


def model_node(
    model_id: str,
    type_ids: tuple[int, ...],
    *,
    active: bool = True,
) -> ModelNode:
    return ModelNode(
        model_id=model_id,
        display_name=f"Model {model_id}",
        task="detect",
        active=active,
        types=tuple(
            TypeNode(
                type_id=type_id,
                model_id=model_id,
                class_name=f"class-{type_id}",
                display_name=f"Tür {type_id}",
                is_catalogued=True,
            )
            for type_id in type_ids
        ),
    )


def detection_row(row_id: int = 1) -> DetectionRow:
    return DetectionRow(
        id=row_id,
        ts=datetime(2026, 7, 24, 10, 0, tzinfo=ISTANBUL_TZ),
        run_id=7,
        model_id="pothole",
        model_display_name="Çukur",
        class_name="pothole",
        display_name="Çukur",
        confidence=0.91,
        area_ratio=0.04,
        bbox=(1.0, 2.0, 3.0, 4.0),
        capture_id="capture-1",
        is_catalogued=True,
    )


class TypeSelectionModelTests(unittest.TestCase):
    def test_parent_is_tri_state_and_parent_toggle_emits_one_callback(self) -> None:
        callbacks: list[frozenset[int]] = []
        selection = TypeSelectionModel(callbacks.append)
        selection.refresh((model_node("a", (1, 2, 3)),))

        self.assertEqual(selection.model_state("a"), SelectionState.ALL)
        selection.toggle_type(2)
        self.assertEqual(selection.model_state("a"), SelectionState.PARTIAL)
        self.assertEqual(callbacks, [frozenset({1, 3})])

        callbacks.clear()
        selection.toggle_model("a")
        self.assertEqual(selection.model_state("a"), SelectionState.ALL)
        self.assertEqual(callbacks, [frozenset({1, 2, 3})])

        callbacks.clear()
        selection.toggle_model("a")
        self.assertEqual(selection.model_state("a"), SelectionState.NONE)
        self.assertEqual(callbacks, [frozenset()])

    def test_refresh_preserves_selection_and_handles_new_types_by_parent_state(self) -> None:
        selection = TypeSelectionModel()
        selection.refresh((model_node("a", (1, 2)),))
        selection.toggle_type(2)

        selection.refresh(
            (
                model_node("a", (1, 2, 3)),
                model_node("new", (4,)),
            )
        )

        # Kısmi parent'a eklenen 3 seçilmez; tamamen yeni model seçili gelir.
        self.assertEqual(selection.selected_type_ids, frozenset({1, 4}))
        self.assertEqual(selection.model_state("a"), SelectionState.PARTIAL)
        self.assertEqual(selection.model_state("new"), SelectionState.ALL)

        selection.refresh((model_node("a", (2, 3)), model_node("new", (4,))))
        self.assertEqual(selection.selected_type_ids, frozenset({4}))
        self.assertEqual(selection.model_state("a"), SelectionState.NONE)

    def test_new_type_under_fully_selected_parent_is_selected(self) -> None:
        selection = TypeSelectionModel()
        selection.refresh((model_node("a", (1, 2)),))

        selection.refresh((model_node("a", (1, 2, 3)),))

        self.assertEqual(selection.selected_type_ids, frozenset({1, 2, 3}))


class PaginationStateTests(unittest.TestCase):
    def test_previous_uses_page_request_cursor_not_previous_last_row(self) -> None:
        first_next = PageCursor(last_value=100, last_id=10)
        second_next = PageCursor(last_value=80, last_id=20)
        pagination = PaginationState()

        self.assertIsNone(pagination.begin_reload())
        pagination.commit(next_cursor=first_next, has_more=True)
        self.assertEqual(pagination.page_number, 1)

        self.assertEqual(pagination.begin_next(), first_next)
        pagination.commit(next_cursor=second_next, has_more=True)
        self.assertEqual(pagination.current_cursor, first_next)
        self.assertEqual(pagination.history, (None,))
        self.assertEqual(pagination.page_number, 2)

        # Birinci sayfayı getiren cursor None'dır; first_next değildir.
        self.assertIsNone(pagination.begin_previous())
        pagination.commit(next_cursor=first_next, has_more=True)
        self.assertIsNone(pagination.current_cursor)
        self.assertEqual(pagination.history, ())
        self.assertEqual(pagination.page_number, 1)

    def test_rejected_navigation_keeps_last_committed_page(self) -> None:
        first_next = PageCursor(last_value=100, last_id=10)
        pagination = PaginationState()
        pagination.begin_reload()
        pagination.commit(next_cursor=first_next, has_more=True)

        pagination.begin_next()
        pagination.reject()

        self.assertIsNone(pagination.current_cursor)
        self.assertEqual(pagination.history, ())
        self.assertTrue(pagination.can_next)

    def test_failed_first_page_refresh_preserves_committed_page_and_history(self) -> None:
        first_next = PageCursor(last_value=100, last_id=10)
        second_next = PageCursor(last_value=80, last_id=20)
        pagination = PaginationState()
        pagination.begin_reload()
        pagination.commit(next_cursor=first_next, has_more=True)
        pagination.begin_next()
        pagination.commit(next_cursor=second_next, has_more=True)

        pagination.begin_first()
        pagination.reject()

        self.assertEqual(pagination.current_cursor, first_next)
        self.assertEqual(pagination.history, (None,))
        self.assertEqual(pagination.page_number, 2)

        pagination.begin_first()
        pagination.commit(next_cursor=first_next, has_more=True)
        self.assertIsNone(pagination.current_cursor)
        self.assertEqual(pagination.history, ())
        self.assertEqual(pagination.page_number, 1)


class FilterStateTests(unittest.TestCase):
    def test_istanbul_timezone_is_available_without_platform_tzdata(self) -> None:
        value = datetime(2026, 7, 24, 12, 0, tzinfo=ISTANBUL_TZ)

        self.assertEqual(value.utcoffset(), timedelta(hours=3))

    def test_presets_are_timezone_aware_and_all_has_no_artificial_bounds(self) -> None:
        now = datetime(2026, 7, 24, 12, 0, tzinfo=ISTANBUL_TZ)

        start, end = resolve_time_range(TimePreset.LAST_24_HOURS, now=now)
        self.assertEqual(start, now - timedelta(hours=24))
        self.assertEqual(end, now)
        self.assertIsNotNone(start.utcoffset())
        self.assertEqual(
            resolve_time_range(TimePreset.ALL, now=now),
            (None, None),
        )

    def test_custom_local_formats_and_order_are_validated(self) -> None:
        parsed = parse_local_datetime("24.07.2026 09:30")
        self.assertEqual(parsed.hour, 9)
        self.assertEqual(parsed.tzinfo, ISTANBUL_TZ)

        start, end = resolve_time_range(
            TimePreset.CUSTOM,
            custom_from="2026-07-24 09:00",
            custom_to="2026-07-24 10:00",
        )
        self.assertLess(start, end)
        with self.assertRaisesRegex(ValueError, "önce"):
            resolve_time_range(
                TimePreset.CUSTOM,
                custom_from="2026-07-24 10:00",
                custom_to="2026-07-24 09:00",
            )

    def test_filter_draft_validates_confidence_and_run(self) -> None:
        draft = FilterDraft(
            time_preset=TimePreset.ALL,
            min_confidence=0.75,
            run_id_text="42",
            only_with_image=True,
        )
        flt = draft.to_filter(frozenset({1, 2}))
        self.assertEqual(flt.min_confidence, 0.75)
        self.assertEqual(flt.run_id, 42)
        self.assertTrue(flt.only_with_image)
        self.assertEqual(flt.type_ids, frozenset({1, 2}))

        with self.assertRaisesRegex(ValueError, "tam sayı"):
            FilterDraft(
                time_preset=TimePreset.ALL,
                run_id_text="x",
            ).to_filter(frozenset({1}))
        with self.assertRaisesRegex(ValueError, "0–1"):
            FilterDraft(
                time_preset=TimePreset.ALL,
                min_confidence=1.5,
            ).to_filter(frozenset({1}))


class ArchiveStateTests(unittest.TestCase):
    def make_loaded_state(self) -> ArchiveState:
        state = ArchiveState()
        state.begin_tree(1)
        self.assertTrue(state.apply_tree(1, (model_node("a", (1, 2)),)))
        return state

    def test_query_change_resets_cursor_and_rejects_non_integer_page_size(self) -> None:
        state = self.make_loaded_state()
        next_cursor = PageCursor(last_value=100, last_id=10)
        state.pagination.begin_reload()
        state.pagination.commit(next_cursor=next_cursor, has_more=True)
        state.pagination.begin_next()
        state.pagination.commit(next_cursor=None, has_more=False)
        old_revision = state.revision

        state.set_sort_column(SortColumn.CONFIDENCE)

        self.assertGreater(state.revision, old_revision)
        self.assertEqual(state.pagination.page_number, 1)
        self.assertIsNone(state.pagination.current_cursor)
        for invalid in (50.0, True, "50"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    state.set_page_size(invalid)  # type: ignore[arg-type]

    def test_refresh_has_draft_applied_boundary_and_explicit_states(self) -> None:
        state = self.make_loaded_state()
        state.set_sort_column(SortColumn.CONFIDENCE)
        requested_sort = state.sort
        flt = state.build_filter(
            now=datetime(2026, 7, 24, 12, 0, tzinfo=ISTANBUL_TZ)
        )
        page = DetectionPage(
            rows=(detection_row(),),
            next_cursor=None,
            has_more=False,
        )
        state.pagination.begin_reload()
        state.begin_refresh(1, flt, include_counts=True)
        self.assertEqual(state.view_state, ArchiveViewState.LOADING)

        self.assertTrue(state.apply_refresh(1, page, ()))
        self.assertIs(state.applied_filter, flt)
        self.assertEqual(state.applied_sort, requested_sort)
        self.assertEqual(state.view_state, ArchiveViewState.READY)
        self.assertFalse(state.dirty)

        state.set_sort_column(SortColumn.AREA_RATIO)
        state.mark_dirty()
        self.assertEqual(state.view_state, ArchiveViewState.STALE)
        self.assertTrue(state.is_stale)

        state.pagination.begin_reload()
        state.begin_refresh(2, flt, include_counts=False)
        self.assertTrue(state.apply_error(2, "DB kapalı"))
        self.assertEqual(state.view_state, ArchiveViewState.STALE)
        self.assertEqual(state.message, "DB kapalı")
        self.assertEqual(state.applied_sort, requested_sort)

    def test_stale_revision_and_generation_results_are_ignored(self) -> None:
        state = self.make_loaded_state()
        now = datetime(2026, 7, 24, 12, 0, tzinfo=ISTANBUL_TZ)
        flt = state.build_filter(now=now)
        result_page = DetectionPage(rows=(detection_row(),), next_cursor=None, has_more=False)

        state.pagination.begin_reload()
        state.begin_refresh(3, flt, include_counts=True)
        state.set_draft(FilterDraft(time_preset=TimePreset.ALL))

        self.assertFalse(state.apply_refresh(3, result_page, ()))
        self.assertFalse(state.apply_error(2, "eski hata"))
        self.assertIsNone(state.page)

    def test_tree_and_refresh_generations_do_not_invalidate_each_other(self) -> None:
        state = self.make_loaded_state()
        flt = state.build_filter(
            now=datetime(2026, 7, 24, 12, 0, tzinfo=ISTANBUL_TZ)
        )
        state.pagination.begin_reload()
        state.begin_refresh(9, flt, include_counts=True)

        state.begin_tree(4)

        self.assertTrue(state.accepts_refresh(9))
        self.assertTrue(state.accepts_tree(4))
        self.assertFalse(state.accepts_tree(3))

    def test_tree_refresh_changes_revision_without_per_child_callbacks(self) -> None:
        state = self.make_loaded_state()
        callbacks = Mock()
        state.set_change_listener(callbacks)
        old_revision = state.revision

        state.begin_tree(2)
        state.apply_tree(2, (model_node("a", (1, 2, 3)),))

        self.assertEqual(state.revision, old_revision + 1)
        self.assertIn(3, state.selection.selected_type_ids)
        callbacks.assert_not_called()


class ArchivePageHeadlessWiringTests(unittest.TestCase):
    def test_confidence_scale_is_enabled_only_when_filter_is_checked(self) -> None:
        filter_bar = FilterBar.__new__(FilterBar)
        filter_bar._enabled = True
        filter_bar.confidence_enabled = Mock()
        filter_bar.confidence_scale = Mock()

        filter_bar.confidence_enabled.get.return_value = False
        FilterBar._sync_confidence_state(filter_bar)
        filter_bar.confidence_scale.configure.assert_called_with(
            state="disabled"
        )

        filter_bar.confidence_enabled.get.return_value = True
        FilterBar._sync_confidence_state(filter_bar)
        filter_bar.confidence_scale.configure.assert_called_with(state="normal")

        filter_bar._enabled = False
        FilterBar._sync_confidence_state(filter_bar)
        filter_bar.confidence_scale.configure.assert_called_with(
            state="disabled"
        )

    def test_public_lifecycle_surface_exists(self) -> None:
        for method_name in (
            "activate",
            "poll_results",
            "mark_dirty",
            "begin_close",
        ):
            self.assertTrue(callable(getattr(ArchivePage, method_name, None)))

    def test_navigation_reuses_applied_filter_snapshot(self) -> None:
        page = ArchivePage.__new__(ArchivePage)
        page._closing = False
        page._tree_pending = False
        page._fetcher = Mock()
        page._fetcher.request_refresh.return_value = 11
        page._render_state = Mock()
        page.state = ArchiveState()
        page.state.begin_tree(1)
        page.state.apply_tree(1, (model_node("a", (1,)),))

        applied = DetectionFilter(
            type_ids=frozenset({1}),
            ts_from=datetime(2026, 7, 23, 12, 0, tzinfo=ISTANBUL_TZ),
            ts_to=datetime(2026, 7, 24, 12, 0, tzinfo=ISTANBUL_TZ),
        )
        next_cursor = PageCursor(
            last_value=datetime(2026, 7, 24, 10, 0, tzinfo=ISTANBUL_TZ),
            last_id=1,
        )
        page.state.pagination.begin_reload()
        page.state.begin_refresh(1, applied, include_counts=True)
        page.state.apply_refresh(
            1,
            DetectionPage(
                rows=(detection_row(),),
                next_cursor=next_cursor,
                has_more=True,
            ),
            (),
        )
        # Draft'ın göreli zamanı yeni bir "şimdi" üretebilirdi; navigation
        # bunun yerine uygulanan immutable filtreyi göndermelidir.
        page.state.draft = FilterDraft(time_preset=TimePreset.LAST_HOUR)

        ArchivePage._request_navigation(page, "next")

        request_args = page._fetcher.request_refresh.call_args
        self.assertIs(request_args.args[0], applied)
        self.assertEqual(request_args.args[2], next_cursor)
        self.assertFalse(request_args.kwargs["include_counts"])

    def test_debounce_really_removes_the_previous_scheduled_callback(self) -> None:
        page = ArchivePage.__new__(ArchivePage)
        page._closing = False
        page._fetcher = object()
        page._debounce_id = None
        page._debounce_tree = False
        page._run_debounced_refresh = Mock()
        scheduled: dict[str, object] = {}
        serial = 0

        def after(_delay: int, callback):
            nonlocal serial
            serial += 1
            callback_id = f"after-{serial}"
            scheduled[callback_id] = callback
            return callback_id

        def after_cancel(callback_id: str) -> None:
            scheduled.pop(callback_id)

        page.after = after
        page.after_cancel = after_cancel

        ArchivePage._schedule_refresh(page, 400)
        first_id = page._debounce_id
        ArchivePage._schedule_refresh(page, 400)

        self.assertNotIn(first_id, scheduled)
        self.assertEqual(tuple(scheduled), (page._debounce_id,))
        next(iter(scheduled.values()))()
        page._run_debounced_refresh.assert_called_once_with()

    def test_tree_result_waits_for_pending_filter_debounce(self) -> None:
        page = ArchivePage.__new__(ArchivePage)
        page.state = Mock()
        page.state.accepts_tree.return_value = True
        page.state.apply_tree.return_value = True
        page.state.counts = ()
        page.type_tree = Mock()
        page._render_state = Mock()
        page._request_refresh = Mock()
        page._active = True
        page._tree_dirty = False
        page._tree_pending = True
        page._debounce_id = "after-1"
        tree = (model_node("a", (1,)),)
        result = types.SimpleNamespace(generation=1, error="", tree=tree)

        ArchivePage._apply_tree_result(page, result)

        page.type_tree.render.assert_called_once_with(tree, ())
        page._request_refresh.assert_not_called()
        self.assertFalse(page._tree_pending)

    def test_manual_refresh_cancels_debounce_created_by_set_draft(self) -> None:
        events: list[str] = []
        page = ArchivePage.__new__(ArchivePage)
        page._closing = False
        page._fetcher = object()
        page._tree_dirty = False
        page.filter_bar = Mock()
        page.filter_bar.read_draft.return_value = FilterDraft(
            time_preset=TimePreset.ALL
        )
        page._cancel_debounce = Mock(side_effect=lambda: events.append("cancel"))
        page._request_refresh = Mock(side_effect=lambda **_kw: events.append("request"))

        class FakeState:
            tree_loaded = True

            def set_draft(self, _draft: FilterDraft) -> None:
                events.append("set_draft")
                events.append("listener_debounce")

            def mark_dirty(self) -> None:
                events.append("dirty")

        page.state = FakeState()

        ArchivePage._refresh_now(page)

        self.assertEqual(
            events,
            [
                "set_draft",
                "listener_debounce",
                "cancel",
                "dirty",
                "request",
            ],
        )


if __name__ == "__main__":
    unittest.main()
