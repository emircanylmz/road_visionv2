"""Tk'den bağımsız Tespit Arşivi seçim, filtre ve sayfalama durumu."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as fixed_timezone, tzinfo
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .archive import (
    DetectionFilter,
    DetectionPage,
    ModelNode,
    PageCursor,
    SortColumn,
    SortSpec,
    TypeCount,
)


try:
    ISTANBUL_TZ: tzinfo = ZoneInfo("Europe/Istanbul")
except ZoneInfoNotFoundError:
    # Windows'un yalın Python kurulumlarında IANA tzdata paketi bulunmayabilir.
    # Türkiye 2016'dan beri yıl boyunca UTC+3 kullandığından arşiv filtresi
    # import aşamasında düşmek yerine güvenli ve deterministik bir fallback
    # kullanır.
    ISTANBUL_TZ = fixed_timezone(timedelta(hours=3), name="Europe/Istanbul")
PAGE_SIZES = (25, 50, 100)


class SelectionState(str, Enum):
    NONE = "none"
    PARTIAL = "partial"
    ALL = "all"


class ArchiveViewState(str, Enum):
    DISABLED = "disabled"
    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    EMPTY = "empty"
    ERROR = "error"
    STALE = "stale"


class TimePreset(str, Enum):
    LAST_HOUR = "1h"
    LAST_24_HOURS = "24h"
    LAST_7_DAYS = "7d"
    ALL = "all"
    CUSTOM = "custom"


def parse_local_datetime(
    value: str,
    *,
    timezone: tzinfo = ISTANBUL_TZ,
) -> datetime:
    """UI tarih metnini timezone-aware yerel zamana çevirir."""

    normalized = value.strip()
    if not normalized:
        raise ValueError("Özel tarih alanları boş bırakılamaz.")

    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S"):
            try:
                parsed = datetime.strptime(normalized, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(
            "Tarih 'YYYY-AA-GG SS:DD' veya 'GG.AA.YYYY SS:DD' biçiminde olmalıdır."
        )
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def resolve_time_range(
    preset: TimePreset,
    *,
    custom_from: str = "",
    custom_to: str = "",
    now: datetime | None = None,
    timezone: tzinfo = ISTANBUL_TZ,
) -> tuple[datetime | None, datetime | None]:
    """Başlangıç dahil, bitiş hariç arşiv zaman aralığını döndürür."""

    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone)
    else:
        current = current.astimezone(timezone)

    if preset == TimePreset.ALL:
        return None, None
    if preset == TimePreset.LAST_HOUR:
        return current - timedelta(hours=1), current
    if preset == TimePreset.LAST_24_HOURS:
        return current - timedelta(hours=24), current
    if preset == TimePreset.LAST_7_DAYS:
        return current - timedelta(days=7), current
    if preset != TimePreset.CUSTOM:
        raise ValueError(f"Desteklenmeyen zaman ön ayarı: {preset!r}")

    start = parse_local_datetime(custom_from, timezone=timezone)
    end = parse_local_datetime(custom_to, timezone=timezone)
    if start >= end:
        raise ValueError("Özel başlangıç tarihi bitiş tarihinden önce olmalıdır.")
    return start, end


@dataclass(frozen=True, slots=True)
class FilterDraft:
    time_preset: TimePreset = TimePreset.LAST_24_HOURS
    custom_from: str = ""
    custom_to: str = ""
    min_confidence: float | None = None
    run_id_text: str = ""
    only_with_image: bool = False

    def to_filter(
        self,
        type_ids: frozenset[int],
        *,
        now: datetime | None = None,
    ) -> DetectionFilter:
        ts_from, ts_to = resolve_time_range(
            self.time_preset,
            custom_from=self.custom_from,
            custom_to=self.custom_to,
            now=now,
        )
        confidence = self.min_confidence
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("Minimum güven 0–1 aralığında olmalıdır.")

        raw_run = self.run_id_text.strip()
        run_id: int | None = None
        if raw_run:
            try:
                run_id = int(raw_run)
            except ValueError as exc:
                raise ValueError("Run kimliği tam sayı olmalıdır.") from exc
            if run_id < 0:
                raise ValueError("Run kimliği negatif olamaz.")

        return DetectionFilter(
            type_ids=type_ids,
            ts_from=ts_from,
            ts_to=ts_to,
            min_confidence=confidence,
            run_id=run_id,
            only_with_image=self.only_with_image,
        )


class TypeSelectionModel:
    """Model → tür ağacının üç durumlu, Tk'sız seçim modeli."""

    def __init__(
        self,
        on_change: Callable[[frozenset[int]], None] | None = None,
    ) -> None:
        self._nodes: tuple[ModelNode, ...] = ()
        self._types_by_model: dict[str, tuple[int, ...]] = {}
        self._selected: set[int] = set()
        self._on_change = on_change

    @property
    def nodes(self) -> tuple[ModelNode, ...]:
        return self._nodes

    @property
    def selected_type_ids(self) -> frozenset[int]:
        return frozenset(self._selected)

    def set_on_change(
        self,
        callback: Callable[[frozenset[int]], None] | None,
    ) -> None:
        self._on_change = callback

    def refresh(
        self,
        nodes: Sequence[ModelNode],
        *,
        notify: bool = False,
    ) -> bool:
        """Ağacı yeniler; mevcut seçimi ve yeni tür parent semantiğini korur.

        İlk yüklemede bütün türler seçilir. Daha sonraki yüklemelerde mevcut
        türlerin durumu korunur. Önceden tamamen seçili bir modele eklenen yeni
        tür seçili gelir; kısmi/boş modele eklenen tür seçilmez. Tamamen yeni
        modelin türleri varsayılan olarak seçilir.
        """

        previous_selected = set(self._selected)
        previous_models = dict(self._types_by_model)
        previous_full = {
            model_id: bool(type_ids) and set(type_ids).issubset(previous_selected)
            for model_id, type_ids in previous_models.items()
        }

        normalized_nodes = tuple(nodes)
        types_by_model = {
            node.model_id: tuple(item.type_id for item in node.types)
            for node in normalized_nodes
        }
        all_new_ids = {
            type_id for type_ids in types_by_model.values() for type_id in type_ids
        }

        if not previous_models:
            next_selected = set(all_new_ids)
        else:
            next_selected = previous_selected.intersection(all_new_ids)
            for model_id, type_ids in types_by_model.items():
                old_ids = set(previous_models.get(model_id, ()))
                added = set(type_ids).difference(old_ids)
                if model_id not in previous_models or previous_full.get(model_id, False):
                    next_selected.update(added)

        self._nodes = normalized_nodes
        self._types_by_model = types_by_model
        selection_changed = next_selected != self._selected
        self._selected = next_selected
        if selection_changed and notify:
            self._notify()
        return selection_changed

    def model_state(self, model_id: str) -> SelectionState:
        type_ids = set(self._types_by_model.get(model_id, ()))
        if not type_ids or not type_ids.intersection(self._selected):
            return SelectionState.NONE
        if type_ids.issubset(self._selected):
            return SelectionState.ALL
        return SelectionState.PARTIAL

    def type_selected(self, type_id: int) -> bool:
        return type_id in self._selected

    def toggle_model(self, model_id: str) -> bool:
        type_ids = set(self._types_by_model.get(model_id, ()))
        if not type_ids:
            return False
        if type_ids.issubset(self._selected):
            next_selected = self._selected.difference(type_ids)
        else:
            next_selected = self._selected.union(type_ids)
        return self._replace_selection(next_selected)

    def toggle_type(self, type_id: int) -> bool:
        if not any(type_id in values for values in self._types_by_model.values()):
            return False
        next_selected = set(self._selected)
        if type_id in next_selected:
            next_selected.remove(type_id)
        else:
            next_selected.add(type_id)
        return self._replace_selection(next_selected)

    def select_all(self) -> bool:
        return self._replace_selection(
            {
                type_id
                for type_ids in self._types_by_model.values()
                for type_id in type_ids
            }
        )

    def clear(self) -> bool:
        return self._replace_selection(set())

    def _replace_selection(self, selected: set[int]) -> bool:
        if selected == self._selected:
            return False
        self._selected = selected
        self._notify()
        return True

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change(self.selected_type_ids)


class PaginationState:
    """Keyset request-cursor geçmişini başarıyla uygulanan sayfalarda tutar."""

    def __init__(self) -> None:
        self.current_cursor: PageCursor | None = None
        self.next_cursor: PageCursor | None = None
        self.has_more = False
        self._history: list[PageCursor | None] = []
        self._pending_cursor: PageCursor | None = None
        self._pending_history: list[PageCursor | None] | None = None

    @property
    def page_number(self) -> int:
        return len(self._history) + 1

    @property
    def can_previous(self) -> bool:
        return bool(self._history)

    @property
    def can_next(self) -> bool:
        return self.has_more and self.next_cursor is not None

    @property
    def request_cursor(self) -> PageCursor | None:
        return (
            self._pending_cursor
            if self._pending_history is not None
            else self.current_cursor
        )

    @property
    def history(self) -> tuple[PageCursor | None, ...]:
        return tuple(self._history)

    def reset(self) -> None:
        self.current_cursor = None
        self.next_cursor = None
        self.has_more = False
        self._history.clear()
        self._pending_cursor = None
        self._pending_history = None

    def begin_reload(self) -> PageCursor | None:
        self._pending_cursor = self.current_cursor
        self._pending_history = list(self._history)
        return self._pending_cursor

    def begin_first(self) -> None:
        """İlk sayfaya geçişi başarıya kadar transactional olarak bekletir."""

        self._pending_cursor = None
        self._pending_history = []
        return None

    def begin_next(self) -> PageCursor:
        if not self.can_next:
            raise ValueError("Sonraki sayfa bulunmuyor.")
        assert self.next_cursor is not None
        # Son satır cursor'ı değil, mevcut sayfayı getiren request cursor'ı
        # geri dönüş geçmişine eklenir.
        self._pending_history = [*self._history, self.current_cursor]
        self._pending_cursor = self.next_cursor
        return self.next_cursor

    def begin_previous(self) -> PageCursor | None:
        if not self.can_previous:
            raise ValueError("Önceki sayfa bulunmuyor.")
        self._pending_cursor = self._history[-1]
        self._pending_history = self._history[:-1]
        return self._pending_cursor

    def commit(
        self,
        *,
        next_cursor: PageCursor | None,
        has_more: bool,
    ) -> None:
        if self._pending_history is not None:
            self.current_cursor = self._pending_cursor
            self._history = self._pending_history
        self.next_cursor = next_cursor
        self.has_more = bool(has_more)
        self._pending_cursor = None
        self._pending_history = None

    def reject(self) -> None:
        self._pending_cursor = None
        self._pending_history = None


@dataclass(frozen=True, slots=True)
class _RefreshContext:
    generation: int
    revision: int
    flt: DetectionFilter
    sort: SortSpec
    include_counts: bool


class ArchiveState:
    """ArchivePage'in draft/applied sorgu ve görünüm durumu."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.selection = TypeSelectionModel(self._selection_changed)
        self.pagination = PaginationState()
        self.draft = FilterDraft()
        self.applied_filter: DetectionFilter | None = None
        self.sort = SortSpec()
        self.applied_sort: SortSpec | None = None
        self.page_size = 50
        self.page: DetectionPage | None = None
        self.counts: tuple[TypeCount, ...] = ()
        self.view_state = ArchiveViewState.IDLE if enabled else ArchiveViewState.DISABLED
        self.message = ""
        self.tree_loaded = False
        self.dirty = True
        self.revision = 0
        self.applied_revision = -1
        self.latest_tree_generation = 0
        self.latest_refresh_generation = 0
        self._refresh_context: _RefreshContext | None = None
        self._change_listener: Callable[[], None] | None = None

    @property
    def has_content(self) -> bool:
        return self.page is not None and bool(self.page.rows)

    @property
    def is_stale(self) -> bool:
        return self.has_content and (
            self.dirty
            or self.applied_revision != self.revision
            or self.view_state == ArchiveViewState.STALE
        )

    @property
    def is_refreshing(self) -> bool:
        return self._refresh_context is not None

    def set_change_listener(self, callback: Callable[[], None] | None) -> None:
        self._change_listener = callback

    def disable(self, message: str) -> None:
        self.view_state = ArchiveViewState.DISABLED
        self.message = message

    def set_draft(self, draft: FilterDraft) -> bool:
        if draft == self.draft:
            return False
        self.draft = draft
        self._query_changed()
        return True

    def set_sort_column(self, column: SortColumn) -> bool:
        descending = (
            not self.sort.descending
            if self.sort.column == column
            else column
            in {
                SortColumn.TS,
                SortColumn.CONFIDENCE,
                SortColumn.AREA_RATIO,
            }
        )
        next_sort = SortSpec(column=column, descending=descending)
        if next_sort == self.sort:
            return False
        self.sort = next_sort
        self._query_changed()
        return True

    def set_page_size(self, page_size: int) -> bool:
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or page_size not in PAGE_SIZES
        ):
            raise ValueError(f"Sayfa boyutu yalnız {PAGE_SIZES} olabilir.")
        if page_size == self.page_size:
            return False
        self.page_size = page_size
        self._query_changed()
        return True

    def mark_dirty(self) -> None:
        # Çalışan sorgunun external-write öncesi snapshot'ını uygulamasını
        # engellemek için salt görünüm bayrağı değil revision da ilerler.
        self.revision += 1
        self.dirty = True
        if self.has_content:
            self.view_state = ArchiveViewState.STALE

    def build_filter(self, *, now: datetime | None = None) -> DetectionFilter:
        return self.draft.to_filter(self.selection.selected_type_ids, now=now)

    def begin_tree(self, generation: int) -> None:
        self.latest_tree_generation = generation
        self.view_state = (
            ArchiveViewState.STALE if self.has_content else ArchiveViewState.LOADING
        )
        self.message = "Tespit türleri yükleniyor…"

    def accepts_tree(self, generation: int) -> bool:
        return generation == self.latest_tree_generation

    def apply_tree(self, generation: int, nodes: Sequence[ModelNode]) -> bool:
        if not self.accepts_tree(generation):
            return False
        selection_changed = self.selection.refresh(nodes, notify=False)
        if selection_changed:
            # Tree yenilemesi yeni/çıkarılmış türlerle etkin sorguyu
            # değiştirebilir. Callback üretmeden revision ve cursor'ı güncelle.
            self.revision += 1
            self.pagination.reset()
        self.tree_loaded = True
        self.dirty = True
        self.message = ""
        return True

    def begin_refresh(
        self,
        generation: int,
        flt: DetectionFilter,
        *,
        sort: SortSpec | None = None,
        include_counts: bool,
    ) -> None:
        self.latest_refresh_generation = generation
        self._refresh_context = _RefreshContext(
            generation=generation,
            revision=self.revision,
            flt=flt,
            sort=sort or self.sort,
            include_counts=include_counts,
        )
        self.view_state = (
            ArchiveViewState.STALE if self.has_content else ArchiveViewState.LOADING
        )
        self.message = "Sorgulanıyor…"

    def accepts_refresh(self, generation: int) -> bool:
        context = self._refresh_context
        return bool(
            context is not None
            and generation == self.latest_refresh_generation
            and generation == context.generation
            and context.revision == self.revision
        )

    def apply_refresh(
        self,
        generation: int,
        page: DetectionPage,
        counts: Sequence[TypeCount] | None,
    ) -> bool:
        if not self.accepts_refresh(generation):
            return False
        context = self._refresh_context
        assert context is not None
        self.pagination.commit(
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )
        self.page = page
        if counts is not None:
            self.counts = tuple(counts)
        self.applied_filter = context.flt
        self.applied_sort = context.sort
        self.applied_revision = context.revision
        self.dirty = False
        if page.rows:
            self.message = ""
        elif not context.flt.type_ids:
            self.message = "Hiçbir tespit türü seçili değil."
        else:
            self.message = "Seçilen filtrelerle kayıt yok."
        self.view_state = (
            ArchiveViewState.READY if page.rows else ArchiveViewState.EMPTY
        )
        self._refresh_context = None
        return True

    def apply_error(self, generation: int, message: str) -> bool:
        if not self.accepts_refresh(generation):
            return False
        self.pagination.reject()
        self.message = message
        self.view_state = (
            ArchiveViewState.STALE if self.has_content else ArchiveViewState.ERROR
        )
        self._refresh_context = None
        return True

    def apply_tree_error(self, generation: int, message: str) -> bool:
        if not self.accepts_tree(generation):
            return False
        self.message = message
        self.view_state = (
            ArchiveViewState.STALE if self.has_content else ArchiveViewState.ERROR
        )
        return True

    def set_local_error(self, message: str) -> None:
        """UI doğrulama/istek hatasını mevcut içeriği kaybetmeden gösterir."""

        self.pagination.reject()
        self._refresh_context = None
        self.message = message
        self.view_state = (
            ArchiveViewState.STALE if self.has_content else ArchiveViewState.ERROR
        )
        self.dirty = True

    def set_local_empty(self, message: str = "Hiçbir tespit türü seçili değil.") -> None:
        self.pagination.reject()
        self._refresh_context = None
        self.page = None
        self.message = message
        self.view_state = ArchiveViewState.EMPTY
        self.dirty = False

    def _selection_changed(self, _selected: frozenset[int]) -> None:
        self._query_changed()

    def _query_changed(self) -> None:
        self.revision += 1
        self.dirty = True
        self.pagination.reset()
        if self.has_content:
            self.view_state = ArchiveViewState.STALE
        if self._change_listener is not None:
            self._change_listener()
