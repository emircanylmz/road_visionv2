"""Tespit Arşivi sekmesinin ince Tk adaptör katmanı."""

from __future__ import annotations

import tkinter as tk
from collections import deque
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from tkinter import ttk
from typing import Any

from ..archive import (
    DetectionRow,
    ModelNode,
    SortColumn,
    SortSpec,
    TypeCount,
)
from ..archive_fetcher import ArchiveFetcher, ArchiveResult
from ..archive_state import (
    ArchiveState,
    ArchiveViewState,
    FilterDraft,
    ISTANBUL_TZ,
    PAGE_SIZES,
    SelectionState,
    TimePreset,
    TypeSelectionModel,
)


_PANEL = "#121c27"
_PANEL_2 = "#172330"
_TEXT = "#edf3f8"
_MUTED = "#8fa2b5"
_ACCENT = "#38d996"
_WARNING = "#ffd166"
_DANGER = "#ff6577"
_CHECK = {
    SelectionState.NONE: "☐",
    SelectionState.PARTIAL: "◪",
    SelectionState.ALL: "☑",
}
_TIME_LABELS: tuple[tuple[str, TimePreset], ...] = (
    ("Son 1 saat", TimePreset.LAST_HOUR),
    ("Son 24 saat", TimePreset.LAST_24_HOURS),
    ("Son 7 gün", TimePreset.LAST_7_DAYS),
    ("Tümü", TimePreset.ALL),
    ("Özel aralık", TimePreset.CUSTOM),
)
_PRESET_BY_LABEL = dict(_TIME_LABELS)
_LABEL_BY_PRESET = {preset: label for label, preset in _TIME_LABELS}


def _format_count(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _format_timestamp(value: datetime) -> str:
    try:
        localized = (
            value.replace(tzinfo=ISTANBUL_TZ)
            if value.tzinfo is None
            else value.astimezone(ISTANBUL_TZ)
        )
    except (OverflowError, ValueError):
        localized = value
    return localized.strftime("%d.%m.%Y %H:%M:%S")


class FilterBar(ttk.Frame):
    """Filtre girdilerini ``FilterDraft`` nesnesine çeviren widget grubu."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_change: Callable[[], None],
        on_refresh: Callable[[], None],
    ) -> None:
        super().__init__(parent, style="Panel.TFrame", padding=(10, 8))
        self._on_change = on_change
        self._on_refresh = on_refresh
        self._enabled = True

        self.time_label = tk.StringVar(
            value=_LABEL_BY_PRESET[TimePreset.LAST_24_HOURS]
        )
        self.custom_from = tk.StringVar(value="")
        self.custom_to = tk.StringVar(value="")
        self.confidence_enabled = tk.BooleanVar(value=False)
        self.confidence = tk.DoubleVar(value=0.50)
        self.confidence_text = tk.StringVar(value="%50")
        self.run_id = tk.StringVar(value="")
        self.only_with_image = tk.BooleanVar(value=False)

        ttk.Label(self, text="Zaman", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.time_combo = ttk.Combobox(
            self,
            textvariable=self.time_label,
            values=tuple(label for label, _preset in _TIME_LABELS),
            state="readonly",
            width=15,
        )
        self.time_combo.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.time_combo.bind("<<ComboboxSelected>>", self._time_changed)

        self.from_label = ttk.Label(
            self, text="Başlangıç", style="Panel.TLabel"
        )
        self.from_entry = ttk.Entry(
            self,
            textvariable=self.custom_from,
            width=18,
        )
        self.to_label = ttk.Label(self, text="Bitiş", style="Panel.TLabel")
        self.to_entry = ttk.Entry(self, textvariable=self.custom_to, width=18)
        self._custom_widgets = (
            (self.from_label, {"row": 2, "column": 0, "sticky": "w"}),
            (
                self.from_entry,
                {
                    "row": 3,
                    "column": 0,
                    "columnspan": 2,
                    "sticky": "ew",
                    "padx": (0, 8),
                },
            ),
            (self.to_label, {"row": 2, "column": 2, "sticky": "w"}),
            (
                self.to_entry,
                {
                    "row": 3,
                    "column": 2,
                    "columnspan": 2,
                    "sticky": "ew",
                    "padx": (0, 8),
                },
            ),
        )
        self.from_entry.bind("<KeyRelease>", self._entry_changed)
        self.to_entry.bind("<KeyRelease>", self._entry_changed)

        confidence_box = ttk.Frame(self, style="Panel.TFrame")
        confidence_box.grid(row=0, column=3, rowspan=2, sticky="ew", padx=(0, 8))
        confidence_box.columnconfigure(0, weight=1)
        self.confidence_check = ttk.Checkbutton(
            confidence_box,
            text="Minimum güven filtresi",
            variable=self.confidence_enabled,
            command=self._confidence_toggled,
        )
        self.confidence_check.grid(row=0, column=0, sticky="w")
        self.confidence_value_label = ttk.Label(
            confidence_box,
            textvariable=self.confidence_text,
            style="Stat.TLabel",
        )
        self.confidence_value_label.grid(row=0, column=1, sticky="e")
        self.confidence_scale = ttk.Scale(
            confidence_box,
            from_=0.0,
            to=1.0,
            variable=self.confidence,
            command=self._confidence_changed,
            state="disabled",
        )
        self.confidence_scale.grid(row=1, column=0, columnspan=2, sticky="ew")

        run_box = ttk.Frame(self, style="Panel.TFrame")
        run_box.grid(row=0, column=4, rowspan=2, sticky="ew", padx=(0, 8))
        ttk.Label(
            run_box,
            text="Çalışma no (Run)",
            style="Panel.TLabel",
        ).grid(
            row=0, column=0, sticky="w"
        )
        self.run_entry = ttk.Entry(run_box, textvariable=self.run_id, width=12)
        self.run_entry.grid(row=1, column=0, sticky="ew")
        self.run_entry.bind("<KeyRelease>", self._entry_changed)
        ttk.Label(
            run_box,
            text="Boş = tüm çalışmalar",
            style="Panel.TLabel",
        ).grid(row=2, column=0, sticky="w")

        self.image_check = ttk.Checkbutton(
            self,
            text="Yalnız görüntüsü olanlar",
            variable=self.only_with_image,
            command=self._emit_change,
        )
        self.image_check.grid(
            row=0, column=5, rowspan=2, sticky="w", padx=(0, 8)
        )
        self.refresh_button = ttk.Button(
            self,
            text="Yenile",
            command=self._on_refresh,
        )
        self.refresh_button.grid(row=0, column=6, rowspan=2, sticky="e")

        self.columnconfigure(3, weight=1, minsize=145)
        self._hide_custom_fields()

    def read_draft(self) -> FilterDraft:
        preset = _PRESET_BY_LABEL.get(
            self.time_label.get(),
            TimePreset.LAST_24_HOURS,
        )
        return FilterDraft(
            time_preset=preset,
            custom_from=self.custom_from.get(),
            custom_to=self.custom_to.get(),
            min_confidence=(
                float(self.confidence.get())
                if self.confidence_enabled.get()
                else None
            ),
            run_id_text=self.run_id.get(),
            only_with_image=bool(self.only_with_image.get()),
        )

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        normal = "normal" if enabled else "disabled"
        self.time_combo.configure(state="readonly" if enabled else "disabled")
        self.confidence_check.configure(state=normal)
        self._sync_confidence_state()
        self.run_entry.configure(state=normal)
        self.image_check.configure(state=normal)
        self.refresh_button.configure(state=normal)
        if enabled and self.read_draft().time_preset == TimePreset.CUSTOM:
            self.from_entry.configure(state="normal")
            self.to_entry.configure(state="normal")
        else:
            self.from_entry.configure(state="disabled")
            self.to_entry.configure(state="disabled")

    def _time_changed(self, _event: tk.Event | None = None) -> None:
        preset = _PRESET_BY_LABEL.get(self.time_label.get())
        if preset == TimePreset.CUSTOM:
            if not self.custom_from.get() and not self.custom_to.get():
                current = datetime.now(ISTANBUL_TZ).replace(
                    second=0,
                    microsecond=0,
                )
                self.custom_from.set(
                    (current - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
                )
                self.custom_to.set(current.strftime("%Y-%m-%d %H:%M"))
            self._show_custom_fields()
        else:
            self._hide_custom_fields()
        self._emit_change()

    def _show_custom_fields(self) -> None:
        for widget, options in self._custom_widgets:
            widget.grid(**options)
        if self._enabled:
            self.from_entry.configure(state="normal")
            self.to_entry.configure(state="normal")

    def _hide_custom_fields(self) -> None:
        for widget, _options in self._custom_widgets:
            widget.grid_remove()
        self.from_entry.configure(state="disabled")
        self.to_entry.configure(state="disabled")

    def _confidence_toggled(self) -> None:
        self._sync_confidence_state()
        self._confidence_changed(str(self.confidence.get()))

    def _sync_confidence_state(self) -> None:
        active = self._enabled and bool(self.confidence_enabled.get())
        self.confidence_scale.configure(
            state="normal" if active else "disabled"
        )

    def _confidence_changed(self, raw_value: str) -> None:
        try:
            value = float(raw_value)
        except ValueError:
            value = float(self.confidence.get())
        self.confidence_text.set(f"%{value * 100:.0f}")
        self._emit_change()

    def _entry_changed(self, _event: tk.Event | None = None) -> None:
        self._emit_change()

    def _emit_change(self) -> None:
        if self._enabled:
            self._on_change()


class TypeFilterTree(ttk.Frame):
    """Model/tür seçim modelini üç durumlu Treeview olarak çizer."""

    def __init__(
        self,
        parent: tk.Misc,
        selection: TypeSelectionModel,
    ) -> None:
        super().__init__(parent, style="Panel.TFrame", padding=(8, 8))
        self.selection = selection
        self._nodes: tuple[ModelNode, ...] = ()
        self._counts: dict[int, int] = {}
        self._model_items: dict[str, str] = {}
        self._type_items: dict[str, int] = {}
        self._enabled = True

        header = ttk.Frame(self, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(header, text="Tespit türleri", style="Section.TLabel").pack(
            side="left"
        )
        self.clear_button = ttk.Button(
            header,
            text="Temizle",
            command=self._clear,
        )
        self.clear_button.pack(side="right")
        self.all_button = ttk.Button(
            header,
            text="Tümü",
            command=self._select_all,
        )
        self.all_button.pack(side="right", padx=(0, 5))

        tree_box = ttk.Frame(self, style="Panel.TFrame")
        tree_box.grid(row=1, column=0, sticky="nsew")
        tree_box.columnconfigure(0, weight=1)
        tree_box.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            tree_box,
            show="tree",
            selectmode="none",
            style="Log.Treeview",
        )
        scrollbar = ttk.Scrollbar(
            tree_box,
            orient="vertical",
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Button-1>", self._clicked)

        self.columnconfigure(0, weight=1, minsize=235)
        self.rowconfigure(1, weight=1)

    def render(
        self,
        nodes: Sequence[ModelNode],
        counts: Sequence[TypeCount] = (),
    ) -> None:
        open_models = {
            model_id
            for model_id, item_id in self._model_items.items()
            if self.tree.exists(item_id) and bool(self.tree.item(item_id, "open"))
        }
        first_render = not self._model_items
        self._nodes = tuple(nodes)
        self._counts = {item.type_id: item.count for item in counts}
        children = self.tree.get_children("")
        if children:
            self.tree.delete(*children)
        self._model_items.clear()
        self._type_items.clear()

        for model_index, model in enumerate(self._nodes):
            model_item = f"model_{model_index}"
            self._model_items[model.model_id] = model_item
            self.tree.insert(
                "",
                "end",
                iid=model_item,
                text=self._model_text(model),
                open=first_render or model.model_id in open_models,
            )
            for type_index, type_node in enumerate(model.types):
                type_item = f"type_{model_index}_{type_index}"
                self._type_items[type_item] = type_node.type_id
                self.tree.insert(
                    model_item,
                    "end",
                    iid=type_item,
                    text=self._type_text(type_node),
                )

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        state = "normal" if enabled else "disabled"
        self.all_button.configure(state=state)
        self.clear_button.configure(state=state)

    def _model_text(self, model: ModelNode) -> str:
        state = self.selection.model_state(model.model_id)
        count = sum(self._counts.get(item.type_id, 0) for item in model.types)
        suffix = "" if model.active else " · pasif/katalog dışı"
        return (
            f"{_CHECK[state]} {model.display_name}{suffix} "
            f"({_format_count(count)})"
        )

    def _type_text(self, node: Any) -> str:
        state = (
            SelectionState.ALL
            if self.selection.type_selected(node.type_id)
            else SelectionState.NONE
        )
        suffix = "" if node.is_catalogued else " · katalog dışı"
        count = _format_count(self._counts.get(node.type_id, 0))
        return f"{_CHECK[state]} {node.display_name}{suffix} ({count})"

    def _refresh_labels(self) -> None:
        for model in self._nodes:
            model_item = self._model_items.get(model.model_id)
            if model_item and self.tree.exists(model_item):
                self.tree.item(model_item, text=self._model_text(model))
            for node in model.types:
                for item_id, type_id in self._type_items.items():
                    if type_id == node.type_id and self.tree.exists(item_id):
                        self.tree.item(item_id, text=self._type_text(node))
                        break

    def _clicked(self, event: tk.Event) -> str | None:
        if not self._enabled or self.tree.identify_region(event.x, event.y) != "tree":
            return None
        element = self.tree.identify_element(event.x, event.y)
        if "indicator" in element:
            return None
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return None
        if item_id in self._type_items:
            self.selection.toggle_type(self._type_items[item_id])
        else:
            for model_id, model_item in self._model_items.items():
                if item_id == model_item:
                    self.selection.toggle_model(model_id)
                    break
        self._refresh_labels()
        return "break"

    def _select_all(self) -> None:
        self.selection.select_all()
        self._refresh_labels()

    def _clear(self) -> None:
        self.selection.clear()
        self._refresh_labels()


class ResultTable(ttk.Frame):
    """Tekil tespit satırlarını sıralanabilir ve scroll edilebilir gösterir."""

    _COLUMNS = (
        "ts",
        "model",
        "class",
        "confidence",
        "area_ratio",
        "run",
        "capture",
    )
    _HEADINGS = {
        "ts": "Zaman",
        "model": "Model",
        "class": "Tür",
        "confidence": "Güven",
        "area_ratio": "Alan",
        "run": "Run",
        "capture": "Görüntü",
    }
    _SORT_COLUMNS = {
        "ts": SortColumn.TS,
        "model": SortColumn.MODEL,
        "class": SortColumn.CLASS,
        "confidence": SortColumn.CONFIDENCE,
        "area_ratio": SortColumn.AREA_RATIO,
    }

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_sort: Callable[[SortColumn], None],
        on_open: Callable[[DetectionRow], None],
        on_selection: Callable[[], None],
    ) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self._on_sort = on_sort
        self._on_open = on_open
        self._on_selection = on_selection
        self._rows: dict[str, DetectionRow] = {}
        self._sort = SortSpec()

        self.tree = ttk.Treeview(
            self,
            columns=self._COLUMNS,
            show="headings",
            selectmode="browse",
            style="Log.Treeview",
        )
        widths = {
            "ts": (150, False),
            "model": (150, True),
            "class": (180, True),
            "confidence": (80, False),
            "area_ratio": (75, False),
            "run": (70, False),
            "capture": (80, False),
        }
        for column in self._COLUMNS:
            sort_column = self._SORT_COLUMNS.get(column)
            heading_options: dict[str, Any] = {
                "text": self._HEADINGS[column],
            }
            if sort_column is not None:
                heading_options["command"] = (
                    lambda value=sort_column: self._on_sort(value)
                )
            self.tree.heading(column, **heading_options)
            width, stretch = widths[column]
            self.tree.column(
                column,
                width=width,
                minwidth=width,
                stretch=stretch,
                anchor=(
                    "center"
                    if column in {"confidence", "area_ratio", "run", "capture"}
                    else "w"
                ),
            )
        self.tree.tag_configure("uncatalogued", foreground=_WARNING)
        self.tree.tag_configure("no_image", foreground=_MUTED)

        vertical = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.tree.yview,
        )
        horizontal = ttk.Scrollbar(
            self,
            orient="horizontal",
            command=self.tree.xview,
        )
        self.tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        self.tree.bind("<Double-1>", self._double_clicked)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.update_sort(self._sort)

    def render(self, rows: Sequence[DetectionRow]) -> None:
        children = self.tree.get_children("")
        if children:
            self.tree.delete(*children)
        self._rows.clear()
        for row in rows:
            item_id = str(row.id)
            confidence = (
                "—" if row.confidence is None else f"%{row.confidence * 100:.1f}"
            )
            area = "—" if row.area_ratio is None else f"%{row.area_ratio * 100:.2f}"
            tags: list[str] = []
            if not row.is_catalogued:
                tags.append("uncatalogued")
            if not row.capture_id:
                tags.append("no_image")
            self._rows[item_id] = row
            self.tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    _format_timestamp(row.ts),
                    row.model_display_name,
                    row.display_name,
                    confidence,
                    area,
                    "—" if row.run_id is None else row.run_id,
                    "📷" if row.capture_id else "—",
                ),
                tags=tuple(tags),
            )
        self._on_selection()

    def clear(self) -> None:
        self.render(())

    def update_sort(self, sort: SortSpec) -> None:
        self._sort = sort
        for column in self._COLUMNS:
            title = self._HEADINGS[column]
            if self._SORT_COLUMNS.get(column) == sort.column:
                title = f"{title} {'▼' if sort.descending else '▲'}"
            self.tree.heading(column, text=title)

    def selected_row(self) -> DetectionRow | None:
        selection = self.tree.selection()
        return self._rows.get(selection[0]) if selection else None

    def _selected(self, _event: tk.Event | None = None) -> None:
        self._on_selection()

    def _double_clicked(self, event: tk.Event) -> None:
        item_id = self.tree.identify_row(event.y)
        row = self._rows.get(item_id)
        if row is None:
            return
        self.tree.selection_set(item_id)
        self._on_open(row)


class ArchivePage(ttk.Frame):
    """Tespit arşivini lazy yükleyen, fetcher ömrünü sahiplenmeyen sekme."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        fetcher: ArchiveFetcher | None,
        on_open_capture: Callable[[str, datetime], None],
    ) -> None:
        super().__init__(parent, style="Panel.TFrame", padding=8)
        self._fetcher = fetcher
        self._on_open_capture = on_open_capture
        self.state = ArchiveState(enabled=fetcher is not None)
        self.state.set_change_listener(self._query_changed)
        self._active = False
        self._closing = False
        self._tree_dirty = True
        self._tree_pending = False
        self._debounce_id: str | None = None
        self._debounce_tree = False
        # Fetcher zaten tür başına latest sonucu tutar; bu adaptör kuyruğu da
        # sıra dışı küçük max_items çağrılarında sınırsız büyüyemez.
        self._result_backlog: deque[ArchiveResult] = deque(maxlen=16)

        self.filter_bar = FilterBar(
            self,
            on_change=self._filter_changed,
            on_refresh=self._refresh_now,
        )
        self.filter_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        self.paned.grid(row=1, column=0, sticky="nsew")
        self.type_tree = TypeFilterTree(self.paned, self.state.selection)
        right = ttk.Frame(self.paned, style="Panel.TFrame", padding=(8, 0, 0, 0))
        self.paned.add(self.type_tree)
        self.paned.add(right, weight=1)

        self.status_text = tk.StringVar(value="Arşiv sekmesi açıldığında yüklenecek.")
        self.status_label = tk.Label(
            right,
            textvariable=self.status_text,
            bg=_PANEL,
            fg=_MUTED,
            anchor="w",
            padx=4,
            pady=6,
        )
        self.status_label.grid(row=0, column=0, sticky="ew", pady=(0, 5))

        self.result_table = ResultTable(
            right,
            on_sort=self._sort_changed,
            on_open=self._open_row,
            on_selection=self._update_controls,
        )
        self.result_table.grid(row=1, column=0, sticky="nsew")

        pager = ttk.Frame(right, style="Panel.TFrame")
        pager.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        self.previous_button = ttk.Button(
            pager,
            text="← Önceki",
            command=self._previous_page,
        )
        self.previous_button.pack(side="left")
        self.page_text = tk.StringVar(value="Sayfa 1")
        ttk.Label(
            pager,
            textvariable=self.page_text,
            style="Panel.TLabel",
        ).pack(side="left", padx=10)
        self.next_button = ttk.Button(
            pager,
            text="Sonraki →",
            command=self._next_page,
        )
        self.next_button.pack(side="left")

        self.open_button = ttk.Button(
            pager,
            text="Görüntüyü Aç",
            command=self._open_selected,
        )
        self.open_button.pack(side="right")
        self.page_size_value = tk.StringVar(value=str(self.state.page_size))
        self.page_size_combo = ttk.Combobox(
            pager,
            textvariable=self.page_size_value,
            values=tuple(str(value) for value in PAGE_SIZES),
            state="readonly",
            width=5,
        )
        self.page_size_combo.pack(side="right", padx=(6, 12))
        self.page_size_combo.bind(
            "<<ComboboxSelected>>",
            self._page_size_changed,
        )
        ttk.Label(pager, text="Satır", style="Panel.TLabel").pack(side="right")

        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.bind("<Map>", self._mapped, add="+")
        self.bind("<Unmap>", self._unmapped, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")

        if fetcher is None:
            self.state.disable(
                "PostgreSQL bağlantısı yapılandırılmadığı için arşiv devre dışı."
            )
        self._render_state()

    def activate(self) -> None:
        """Sekme görünür olduğunda lazy tree/refresh akışını başlatır."""

        if self._closing:
            return
        self._active = True
        if self._fetcher is None:
            self._render_state()
            return
        if not self.state.tree_loaded or self._tree_dirty:
            if not self._tree_pending or self._tree_dirty:
                self._request_tree()
        elif self.state.dirty:
            self._schedule_refresh(0)

    def poll_results(self, max_items: int = 8) -> int:
        """Fetcher sonucunu tek UI turunda sınırlı sayıda uygular."""

        if self._closing or self._fetcher is None or max_items <= 0:
            return 0
        self._result_backlog.extend(self._fetcher.poll())
        processed = 0
        while self._result_backlog and processed < max_items:
            result = self._result_backlog.popleft()
            self._apply_result(result)
            processed += 1
        return processed

    def mark_dirty(self, delay_ms: int = 750) -> None:
        """Yeni DB verisi olabileceğini bildirir; görünürse gecikmeli yeniler."""

        if self._closing or self._fetcher is None:
            return
        self.state.mark_dirty()
        self._tree_dirty = True
        self._render_state()
        if self._active and self._is_mapped():
            self._schedule_refresh(max(0, int(delay_ms)), refresh_tree=True)

    def begin_close(self) -> None:
        """UI callback'lerini keser; paylaşılan fetcher'ı App kapatır."""

        self._closing = True
        self._active = False
        self._cancel_debounce()
        self._result_backlog.clear()

    def _filter_changed(self) -> None:
        if self._closing:
            return
        self.state.set_draft(self.filter_bar.read_draft())

    def _query_changed(self) -> None:
        # Mevcut satırlar, sorgu başarıyla dönene kadar uygulanmış sıralamayı
        # temsil eder. Yeni ok işaretini erken göstermek hata halinde başlıkla
        # eski satırları çelişkili hale getirirdi.
        if not self.state.has_content:
            self.result_table.update_sort(self.state.sort)
        self._render_state()
        if self._active:
            self._schedule_refresh(400)

    def _refresh_now(self) -> None:
        if self._closing or self._fetcher is None:
            return
        self.state.set_draft(self.filter_bar.read_draft())
        # set_draft change listener'ı debounce kurmuş olabilir; manuel yenileme
        # tek ve hemen başlayan istek üretmelidir.
        self._cancel_debounce()
        self.state.mark_dirty()
        if not self.state.tree_loaded or self._tree_dirty:
            if not self._tree_pending or self._tree_dirty:
                self._request_tree()
        else:
            self._request_refresh(include_counts=True)

    def _sort_changed(self, column: SortColumn) -> None:
        if self._closing or self._fetcher is None:
            return
        self.state.set_sort_column(column)

    def _page_size_changed(self, _event: tk.Event | None = None) -> None:
        try:
            page_size = int(self.page_size_value.get())
            self.state.set_page_size(page_size)
        except ValueError as exc:
            self.state.set_local_error(str(exc))
            self._render_state()

    def _schedule_refresh(
        self,
        delay_ms: int,
        *,
        refresh_tree: bool = False,
    ) -> None:
        if self._closing or self._fetcher is None:
            return
        self._debounce_tree = self._debounce_tree or refresh_tree
        self._cancel_debounce(clear_tree=False)
        self._debounce_id = self.after(
            max(0, delay_ms),
            self._run_debounced_refresh,
        )

    def _run_debounced_refresh(self) -> None:
        self._debounce_id = None
        refresh_tree = self._debounce_tree
        self._debounce_tree = False
        if self._closing or not self._active:
            return
        if self._tree_pending and not refresh_tree and not self._tree_dirty:
            # Tree gelince son draft ile refresh akışı devam eder.
            return
        if refresh_tree or self._tree_dirty or not self.state.tree_loaded:
            self._request_tree()
        else:
            self._request_refresh(include_counts=True)

    def _cancel_debounce(self, *, clear_tree: bool = True) -> None:
        callback_id, self._debounce_id = self._debounce_id, None
        if callback_id is not None:
            try:
                self.after_cancel(callback_id)
            except tk.TclError:
                pass
        if clear_tree:
            self._debounce_tree = False

    def _request_tree(self) -> None:
        if self._closing or self._fetcher is None:
            return
        self._tree_dirty = False
        try:
            generation = self._fetcher.request_tree()
        except Exception as exc:
            self._tree_dirty = True
            self._tree_pending = False
            self.state.set_local_error(f"Tür ağacı istenemedi: {exc}")
        else:
            self._tree_pending = True
            self.state.begin_tree(generation)
        self._render_state()

    def _request_refresh(self, *, include_counts: bool) -> None:
        if self._closing or self._fetcher is None or not self.state.tree_loaded:
            return
        try:
            flt = self.state.build_filter()
            if not flt.type_ids and not self.state.selection.nodes:
                self.state.set_local_empty(
                    "Veritabanında tespit türü bulunamadı."
                )
                self.result_table.clear()
                self._render_state()
                return
            # Tür facet sayımları type_ids'i bilerek dışlar. Seçim boşken de
            # diğer filtreler değişmiş olabilir; sayımları güncel tut.
            if not flt.type_ids:
                include_counts = True
            # Tam yenileme yeni bir zaman snapshot'ı kurar; eski keyset cursor'ı
            # bu filtreyle karıştırılmaz. İleri/geri akışı ayrı metottadır.
            cursor = self.state.pagination.begin_first()
            generation = self._fetcher.request_refresh(
                flt,
                self.state.sort,
                cursor,
                self.state.page_size,
                include_counts=include_counts,
            )
        except (TypeError, ValueError) as exc:
            self.state.set_local_error(str(exc))
        except Exception as exc:
            self.state.set_local_error(f"Arşiv sorgusu başlatılamadı: {exc}")
        else:
            self.state.begin_refresh(
                generation,
                flt,
                sort=self.state.sort,
                include_counts=include_counts,
            )
        self._render_state()

    def _request_navigation(self, direction: str) -> None:
        if (
            self._closing
            or self._fetcher is None
            or self.state.is_refreshing
            or self._tree_pending
            or self.state.dirty
        ):
            return
        try:
            # Göreli "son 24 saat" aralığı dahil aynı sayfalama gezintisinde
            # birebir uygulanan immutable filtre korunur.
            flt = self.state.applied_filter
            if flt is None:
                raise ValueError("Gezinilecek uygulanmış bir arşiv sorgusu yok.")
            cursor = (
                self.state.pagination.begin_next()
                if direction == "next"
                else self.state.pagination.begin_previous()
            )
            generation = self._fetcher.request_refresh(
                flt,
                self.state.sort,
                cursor,
                self.state.page_size,
                include_counts=False,
            )
        except (TypeError, ValueError) as exc:
            self.state.set_local_error(str(exc))
        except Exception as exc:
            self.state.set_local_error(f"Sayfa sorgusu başlatılamadı: {exc}")
        else:
            self.state.begin_refresh(
                generation,
                flt,
                sort=self.state.sort,
                include_counts=False,
            )
        self._render_state()

    def _next_page(self) -> None:
        self._cancel_debounce()
        self._request_navigation("next")

    def _previous_page(self) -> None:
        self._cancel_debounce()
        self._request_navigation("previous")

    def _apply_result(self, result: ArchiveResult) -> None:
        if result.kind == "tree":
            self._apply_tree_result(result)
        elif result.kind == "refresh":
            self._apply_refresh_result(result)

    def _apply_tree_result(self, result: ArchiveResult) -> None:
        if not self.state.accepts_tree(result.generation):
            return
        self._tree_pending = False
        if result.error:
            self._tree_dirty = True
            self.state.apply_tree_error(result.generation, result.error)
            self._render_state()
            return
        if result.tree is None:
            self._tree_dirty = True
            self.state.apply_tree_error(
                result.generation,
                "Tür ağacı sonucu boş döndü.",
            )
            self._render_state()
            return
        if not self.state.apply_tree(result.generation, result.tree):
            return
        self.type_tree.render(result.tree, self.state.counts)
        self._render_state()
        if (
            self._active
            and not self._tree_dirty
            and self._debounce_id is None
        ):
            self._request_refresh(include_counts=True)

    def _apply_refresh_result(self, result: ArchiveResult) -> None:
        if not self.state.accepts_refresh(result.generation):
            return
        if result.error:
            self.state.apply_error(result.generation, result.error)
            self._render_state()
            return
        if result.page is None:
            self.state.apply_error(
                result.generation,
                "Arşiv sorgusu sonuç sayfası döndürmedi.",
            )
            self._render_state()
            return
        if not self.state.apply_refresh(
            result.generation,
            result.page,
            result.counts,
        ):
            return
        if not self.state.selection.selected_type_ids:
            self.state.message = "Hiçbir tespit türü seçili değil."
        self.result_table.render(result.page.rows)
        self.result_table.update_sort(
            self.state.applied_sort or self.state.sort
        )
        self.type_tree.render(self.state.selection.nodes, self.state.counts)
        self._render_state()

    def _render_state(self) -> None:
        view = self.state.view_state
        color = _MUTED
        if view == ArchiveViewState.DISABLED:
            text = self.state.message or "Tespit arşivi devre dışı."
        elif view == ArchiveViewState.IDLE:
            text = self.state.message or "Arşiv sekmesi açıldığında yüklenecek."
        elif view == ArchiveViewState.LOADING:
            text = self.state.message or "Yükleniyor…"
            color = _ACCENT
        elif view == ArchiveViewState.READY:
            row_count = len(self.state.page.rows) if self.state.page else 0
            total_count = self._selected_detection_count()
            text = (
                f"{row_count} satır gösteriliyor · "
                f"seçili filtrelerde {_format_count(total_count)} tespit · "
                f"Sayfa {self.state.pagination.page_number}"
            )
            color = _ACCENT
        elif view == ArchiveViewState.EMPTY:
            text = self.state.message or "Kayıt bulunamadı."
        elif view == ArchiveViewState.ERROR:
            text = f"Hata: {self.state.message}"
            color = _DANGER
        else:
            detail = self.state.message or "Filtre/veri değişti."
            text = f"{detail} Önceki sonuçlar gösteriliyor."
            color = _WARNING
        self.status_text.set(text)
        self.status_label.configure(fg=color)
        enabled = self._fetcher is not None and not self._closing
        self.filter_bar.set_enabled(enabled)
        self.type_tree.set_enabled(enabled)
        self.page_size_combo.configure(
            state="readonly" if enabled else "disabled"
        )
        self._update_controls()

    def _update_controls(self) -> None:
        pending = self.state.is_refreshing or self._tree_pending or self.state.dirty
        enabled = self._fetcher is not None and not self._closing
        self.previous_button.configure(
            state=(
                "normal"
                if enabled and not pending and self.state.pagination.can_previous
                else "disabled"
            )
        )
        self.next_button.configure(
            state=(
                "normal"
                if enabled and not pending and self.state.pagination.can_next
                else "disabled"
            )
        )
        selected = self.result_table.selected_row()
        self.open_button.configure(
            state=(
                "normal"
                if enabled and selected is not None and selected.capture_id
                else "disabled"
            )
        )
        self.page_text.set(f"Sayfa {self.state.pagination.page_number}")

    def _selected_detection_count(self) -> int:
        selected = self.state.selection.selected_type_ids
        return sum(
            item.count
            for item in self.state.counts
            if item.type_id in selected
        )

    def _open_selected(self) -> None:
        row = self.result_table.selected_row()
        if row is not None:
            self._open_row(row)

    def _open_row(self, row: DetectionRow) -> None:
        if not row.capture_id:
            self.status_text.set("Bu tespit için saklanmış görüntü yok.")
            self.status_label.configure(fg=_WARNING)
            return
        self._on_open_capture(str(row.capture_id), row.ts)

    def _unmapped(self, event: tk.Event) -> None:
        if event.widget is self:
            self._active = False
            self._cancel_debounce()

    def _mapped(self, event: tk.Event) -> None:
        if event.widget is self and not self._closing:
            # Notebook yalnız seçili child'ı map eder. Böylece pencere
            # minimize/restore sonrasında TabChanged olayı gerekmeksizin
            # lazy yenileme yeniden devreye girer.
            self.activate()

    def _destroyed(self, event: tk.Event) -> None:
        if event.widget is self:
            self.begin_close()

    def _is_mapped(self) -> bool:
        try:
            return bool(self.winfo_ismapped())
        except tk.TclError:
            return False
