"""Tespit Arşivi sekmesinin Qt adaptörü.

Durum makinesi Tk sürümüyle birebir aynıdır: ``ArchiveState`` +
``TypeSelectionModel`` + ``PaginationState`` view-model'leri kullanılır,
fetcher sonuçları generation kontrolüyle uygulanır, debounce tek QTimer'la
kurulur. Fetcher'ın ömrü App'e aittir.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStyledItemDelegate,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..archive import DetectionRow, ModelNode, SortColumn, SortSpec, TypeCount
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
from . import theme
from .widgets import (
    Card,
    SegmentedControl,
    ToggleSwitch,
    mono_font,
    mono_label,
    muted_label,
    section_label,
)

_TIME_ITEMS: tuple[tuple[str, TimePreset], ...] = (
    ("1sa", TimePreset.LAST_HOUR),
    ("24sa", TimePreset.LAST_24_HOURS),
    ("7g", TimePreset.LAST_7_DAYS),
    ("Tümü", TimePreset.ALL),
    ("Özel", TimePreset.CUSTOM),
)
_PRESET_BY_KEY = dict(_TIME_ITEMS)
_KEY_BY_PRESET = {preset: key for key, preset in _TIME_ITEMS}


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


class FilterPanel(Card):
    """Filtre girdilerini ``FilterDraft``'a çeviren sol panel bölümü."""

    def __init__(
        self,
        parent: QWidget,
        *,
        on_change: Callable[[], None],
        on_refresh: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self._on_change = on_change
        self._on_refresh = on_refresh
        self._enabled = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 13, 13, 13)
        layout.setSpacing(11)
        layout.addWidget(section_label("Zaman Aralığı", self))
        self.time_segment = SegmentedControl(
            tuple((key, key) for key, _preset in _TIME_ITEMS),
            self,
            current=_KEY_BY_PRESET[TimePreset.LAST_24_HOURS],
        )
        self.time_segment.changed.connect(self._time_changed)
        layout.addWidget(self.time_segment)

        self.custom_row = QWidget(self)
        custom_layout = QHBoxLayout(self.custom_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(7)
        self.from_edit = QLineEdit(self.custom_row)
        self.from_edit.setPlaceholderText("YYYY-AA-GG SS:DD")
        self.from_edit.setFont(mono_font(8))
        self.from_edit.textEdited.connect(lambda _text: self._emit_change())
        custom_layout.addWidget(self.from_edit)
        self.to_edit = QLineEdit(self.custom_row)
        self.to_edit.setPlaceholderText("YYYY-AA-GG SS:DD")
        self.to_edit.setFont(mono_font(8))
        self.to_edit.textEdited.connect(lambda _text: self._emit_change())
        custom_layout.addWidget(self.to_edit)
        self.custom_row.hide()
        layout.addWidget(self.custom_row)

        confidence_header = QHBoxLayout()
        confidence_header.setSpacing(9)
        self.confidence_toggle = ToggleSwitch(self)
        self.confidence_toggle.toggled.connect(self._confidence_toggled)
        confidence_header.addWidget(self.confidence_toggle)
        confidence_header.addWidget(muted_label("Minimum güven filtresi", self))
        confidence_header.addStretch(1)
        self.confidence_value = mono_label(
            "0.50", self, size=11, color=theme.ACCENT, bold=True
        )
        confidence_header.addWidget(self.confidence_value)
        layout.addLayout(confidence_header)
        self.confidence_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.confidence_slider.setRange(0, 100)
        self.confidence_slider.setValue(50)
        self.confidence_slider.setEnabled(False)
        self.confidence_slider.valueChanged.connect(self._confidence_changed)
        layout.addWidget(self.confidence_slider)

        image_row = QHBoxLayout()
        image_row.setSpacing(9)
        self.image_toggle = ToggleSwitch(self)
        self.image_toggle.toggled.connect(lambda _checked: self._emit_change())
        image_row.addWidget(self.image_toggle)
        image_row.addWidget(muted_label("Yalnız görüntüsü olanlar", self))
        image_row.addStretch(1)
        layout.addLayout(image_row)

        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        run_row.addWidget(muted_label("Çalışma no (Run)", self))
        self.run_edit = QLineEdit(self)
        self.run_edit.setPlaceholderText("boş = tüm çalışmalar")
        self.run_edit.setFont(mono_font(8))
        self.run_edit.textEdited.connect(lambda _text: self._emit_change())
        run_row.addWidget(self.run_edit, 1)
        self.refresh_button = QPushButton("Yenile", self)
        self.refresh_button.clicked.connect(self._on_refresh)
        run_row.addWidget(self.refresh_button)
        layout.addLayout(run_row)

    def read_draft(self) -> FilterDraft:
        preset = _PRESET_BY_KEY.get(
            self.time_segment.current(), TimePreset.LAST_24_HOURS
        )
        return FilterDraft(
            time_preset=preset,
            custom_from=self.from_edit.text(),
            custom_to=self.to_edit.text(),
            min_confidence=(
                self.confidence_slider.value() / 100
                if self.confidence_toggle.isChecked()
                else None
            ),
            run_id_text=self.run_edit.text(),
            only_with_image=self.image_toggle.isChecked(),
        )

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        for widget in (
            self.time_segment,
            self.confidence_toggle,
            self.image_toggle,
            self.run_edit,
            self.refresh_button,
        ):
            widget.setEnabled(enabled)
        self.confidence_slider.setEnabled(
            enabled and self.confidence_toggle.isChecked()
        )
        custom = enabled and self.time_segment.current() == _KEY_BY_PRESET[
            TimePreset.CUSTOM
        ]
        self.from_edit.setEnabled(custom)
        self.to_edit.setEnabled(custom)

    def _time_changed(self, key: str) -> None:
        preset = _PRESET_BY_KEY.get(key)
        if preset == TimePreset.CUSTOM:
            if not self.from_edit.text() and not self.to_edit.text():
                current = datetime.now(ISTANBUL_TZ).replace(
                    second=0, microsecond=0
                )
                self.from_edit.setText(
                    (current - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
                )
                self.to_edit.setText(current.strftime("%Y-%m-%d %H:%M"))
            self.custom_row.show()
            if self._enabled:
                self.from_edit.setEnabled(True)
                self.to_edit.setEnabled(True)
        else:
            self.custom_row.hide()
        self._emit_change()

    def _confidence_toggled(self, checked: bool) -> None:
        self.confidence_slider.setEnabled(self._enabled and checked)
        self._emit_change()

    def _confidence_changed(self, value: int) -> None:
        self.confidence_value.setText(f"{value / 100:.2f}")
        self._emit_change()

    def _emit_change(self) -> None:
        if self._enabled:
            self._on_change()


class _TypeRow(QFrame):
    """Ağaçtaki tek satır: üç durumlu kutu + renk + ad + sayım."""

    clicked = pyqtSignal()

    def __init__(
        self,
        *,
        label: str,
        color: str,
        indent: bool,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(28 if indent else 8, 6, 8, 6)
        layout.setSpacing(9)
        self.check_label = QLabel("", self)
        self.check_label.setFixedSize(15, 15)
        self.check_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.check_label)
        dot = QLabel("●", self)
        dot.setStyleSheet(
            f"color: {color}; background: transparent; border: none; font-size: 7px;"
        )
        layout.addWidget(dot)
        self.name_label = QLabel(label, self)
        self.name_label.setStyleSheet(
            "background: transparent; border: none; font-size: 11px;"
            + ("font-weight: 700;" if not indent else "")
        )
        layout.addWidget(self.name_label, 1)
        self.count_label = mono_label("", self, size=8)
        self.count_label.setStyleSheet(
            f"color: {theme.MUTED}; background: transparent; border: none;"
        )
        layout.addWidget(self.count_label)
        self._indent = indent
        self.set_state(SelectionState.NONE, 0)

    def set_state(self, state: SelectionState, count: int) -> None:
        marks = {
            SelectionState.NONE: ("", "transparent", theme.CHIP_BORDER),
            SelectionState.PARTIAL: ("–", theme.ACCENT, theme.ACCENT),
            SelectionState.ALL: ("✓", theme.ACCENT, theme.ACCENT),
        }
        mark, bg, border = marks[state]
        self.check_label.setText(mark)
        self.check_label.setStyleSheet(
            f"background: {bg}; border: 1px solid {border}; border-radius: 4px;"
            f"color: {theme.ACCENT_INK}; font-size: 9px; font-weight: 700;"
        )
        self.count_label.setText(_format_count(count))
        base_bg = theme.HOVER_BG if not self._indent else "transparent"
        self.setStyleSheet(
            f"QFrame {{ background: {base_bg}; border: none; border-radius: 7px; }}"
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class TypeTreePanel(Card):
    """``TypeSelectionModel``'i üç durumlu satır listesi olarak çizer."""

    def __init__(self, parent: QWidget, selection: TypeSelectionModel) -> None:
        super().__init__(parent)
        self.selection = selection
        self._nodes: tuple[ModelNode, ...] = ()
        self._counts: dict[int, int] = {}
        self._model_rows: dict[str, _TypeRow] = {}
        self._type_rows: dict[int, _TypeRow] = {}
        self._enabled = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 13, 9, 11)
        layout.setSpacing(9)
        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 4, 0)
        header.addWidget(section_label("Tespit Türleri", self))
        header.addStretch(1)
        self.all_button = QToolButton(self)
        self.all_button.setText("TÜMÜ")
        self.all_button.setStyleSheet(
            f"QToolButton {{ color: {theme.ACCENT}; background: transparent;"
            "border: none; font-size: 10px; font-weight: 700; padding: 2px; }"
        )
        self.all_button.clicked.connect(self._select_all)
        header.addWidget(self.all_button)
        self.clear_button = QToolButton(self)
        self.clear_button.setText("TEMİZLE")
        self.clear_button.setStyleSheet(
            f"QToolButton {{ color: {theme.MUTED}; background: transparent;"
            "border: none; font-size: 10px; font-weight: 700; padding: 2px; }"
        )
        self.clear_button.clicked.connect(self._clear)
        header.addWidget(self.clear_button)
        layout.addLayout(header)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rows_widget = QWidget(scroll)
        self._rows_widget.setStyleSheet("background: transparent;")
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        self._rows_layout.addStretch(1)
        scroll.setWidget(self._rows_widget)
        layout.addWidget(scroll, 1)

    def render(
        self,
        nodes: Sequence[ModelNode],
        counts: Sequence[TypeCount] = (),
    ) -> None:
        self._nodes = tuple(nodes)
        self._counts = {item.type_id: item.count for item in counts}
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._model_rows.clear()
        self._type_rows.clear()
        insert_at = 0
        for model in self._nodes:
            color = theme.color_for_model(model.model_id)
            suffix = "" if model.active else " · pasif"
            model_row = _TypeRow(
                label=f"{model.display_name}{suffix}",
                color=color,
                indent=False,
                parent=self._rows_widget,
            )
            model_row.clicked.connect(
                lambda model_id=model.model_id: self._toggle_model(model_id)
            )
            self._rows_layout.insertWidget(insert_at, model_row)
            insert_at += 1
            self._model_rows[model.model_id] = model_row
            for type_node in model.types:
                type_suffix = "" if type_node.is_catalogued else " · katalog dışı"
                type_row = _TypeRow(
                    label=f"{type_node.display_name}{type_suffix}",
                    color=color,
                    indent=True,
                    parent=self._rows_widget,
                )
                type_row.clicked.connect(
                    lambda type_id=type_node.type_id: self._toggle_type(type_id)
                )
                self._rows_layout.insertWidget(insert_at, type_row)
                insert_at += 1
                self._type_rows[type_node.type_id] = type_row
        self._refresh_labels()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.all_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)

    def _refresh_labels(self) -> None:
        for model in self._nodes:
            row = self._model_rows.get(model.model_id)
            if row is not None:
                count = sum(
                    self._counts.get(item.type_id, 0) for item in model.types
                )
                row.set_state(self.selection.model_state(model.model_id), count)
            for type_node in model.types:
                type_row = self._type_rows.get(type_node.type_id)
                if type_row is not None:
                    state = (
                        SelectionState.ALL
                        if self.selection.type_selected(type_node.type_id)
                        else SelectionState.NONE
                    )
                    type_row.set_state(
                        state, self._counts.get(type_node.type_id, 0)
                    )

    def _toggle_model(self, model_id: str) -> None:
        if self._enabled:
            self.selection.toggle_model(model_id)
            self._refresh_labels()

    def _toggle_type(self, type_id: int) -> None:
        if self._enabled:
            self.selection.toggle_type(type_id)
            self._refresh_labels()

    def _select_all(self) -> None:
        if self._enabled:
            self.selection.select_all()
            self._refresh_labels()

    def _clear(self) -> None:
        if self._enabled:
            self.selection.clear()
            self._refresh_labels()


_ARCHIVE_COLUMNS = ("ZAMAN", "MODEL", "TÜR", "GÜVEN", "ALAN", "RUN", "CAPTURE")
_A_TS, _A_MODEL, _A_TYPE, _A_CONF, _A_AREA, _A_RUN, _A_CAP = range(7)
_SORT_BY_COLUMN = {
    _A_TS: SortColumn.TS,
    _A_MODEL: SortColumn.MODEL,
    _A_TYPE: SortColumn.CLASS,
    _A_CONF: SortColumn.CONFIDENCE,
    _A_AREA: SortColumn.AREA_RATIO,
}


class ArchiveTableModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[DetectionRow] = []
        self._sort = SortSpec()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(_ARCHIVE_COLUMNS)

    def headerData(self, section, orientation, role):  # noqa: N802
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            title = _ARCHIVE_COLUMNS[section]
            if _SORT_BY_COLUMN.get(section) == self._sort.column:
                arrow = "▼" if self._sort.descending else "▲"
                return f"{title} {arrow}"
            return title
        return None

    def data(self, index: QModelIndex, role):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role == Qt.ItemDataRole.DisplayRole:
            if column == _A_TS:
                return _format_timestamp(row.ts)
            if column == _A_MODEL:
                return row.model_display_name
            if column == _A_TYPE:
                return row.display_name
            if column == _A_CONF:
                return (
                    "—"
                    if row.confidence is None
                    else f"{row.confidence:.2f}"
                )
            if column == _A_AREA:
                return (
                    "—"
                    if row.area_ratio is None
                    else f"%{row.area_ratio * 100:.2f}"
                )
            if column == _A_RUN:
                return "—" if row.run_id is None else str(row.run_id)
            if column == _A_CAP:
                return "📷" if row.capture_id else "—"
        if role == Qt.ItemDataRole.ForegroundRole:
            if column == _A_TYPE:
                return QColor(
                    theme.WARNING if not row.is_catalogued else theme.TEXT
                )
            if column == _A_MODEL:
                return QColor(theme.MUTED)
            if column in (_A_AREA, _A_RUN):
                return QColor(theme.MUTED)
            if column == _A_CAP:
                return QColor(theme.TEXT if row.capture_id else theme.DIM)
            if column == _A_TS:
                return QColor(theme.TEXT_SOFT)
            return QColor(theme.TEXT)
        if role == Qt.ItemDataRole.FontRole:
            if column in (_A_TS, _A_CONF, _A_AREA, _A_RUN):
                return mono_font(8)
            if column == _A_TYPE:
                font = self._base_font()
                font.setBold(True)
                return font
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (
            _A_CONF,
            _A_AREA,
            _A_RUN,
            _A_CAP,
        ):
            return (
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        return None

    @staticmethod
    def _base_font():
        from PyQt6.QtGui import QFont

        return QFont()

    def set_rows(self, rows: Sequence[DetectionRow]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def set_sort(self, sort: SortSpec) -> None:
        self._sort = sort
        self.headerDataChanged.emit(
            Qt.Orientation.Horizontal, 0, len(_ARCHIVE_COLUMNS) - 1
        )

    def row_at(self, row: int) -> DetectionRow | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None


class _ArchiveRowDelegate(QStyledItemDelegate):
    """Zaman kolonunun soluna model renginde şerit çizer."""

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        if index.column() != _A_TS:
            return
        row = index.data(Qt.ItemDataRole.UserRole)
        if row is None:
            return
        color = theme.color_for_model(row.model_id)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        rect = option.rect
        painter.drawRoundedRect(
            QRectF(rect.left() + 4, rect.top() + 8, 3, rect.height() - 16), 1.5, 1.5
        )
        painter.restore()


class ArchivePage(QWidget):
    """Tespit arşivini lazy yükleyen sayfa; fetcher ömrünü sahiplenmez."""

    openCaptureRequested = pyqtSignal(str, object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        fetcher: ArchiveFetcher | None,
    ) -> None:
        super().__init__(parent)
        self._fetcher = fetcher
        self.state = ArchiveState(enabled=fetcher is not None)
        self.state.set_change_listener(self._query_changed)
        self._active = False
        self._closing = False
        self._tree_dirty = True
        self._tree_pending = False
        self._debounce_tree = False
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._run_debounced_refresh)
        # Fetcher tür başına yalnız son sonucu tutar; backlog da sınırlı kalır.
        self._result_backlog: deque[ArchiveResult] = deque(maxlen=16)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left = QFrame(self)
        left.setObjectName("Sidebar")
        left.setFixedWidth(300)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(12)
        self.filter_panel = FilterPanel(
            left, on_change=self._filter_changed, on_refresh=self._refresh_now
        )
        left_layout.addWidget(self.filter_panel)
        self.type_tree = TypeTreePanel(left, self.state.selection)
        left_layout.addWidget(self.type_tree, 1)
        root.addWidget(left)

        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 10)
        right_layout.setSpacing(12)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.total_label = QLabel("—", right)
        self.total_label.setFont(mono_font(12, bold=True))
        self.total_label.setStyleSheet(
            f"color: {theme.TEXT}; background: transparent;"
        )
        status_row.addWidget(self.total_label)
        self.status_label = muted_label(
            "Arşiv sekmesi açıldığında yüklenecek.", right
        )
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        status_row.addWidget(self.status_label, 1)
        right_layout.addLayout(status_row)

        self.table_model = ArchiveTableModel(self)
        self.table = QTableView(right)
        self.table.setModel(self.table_model)
        self.table.setItemDelegate(_ArchiveRowDelegate(self.table))
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._header_clicked)
        widths = (150, 150, 0, 80, 80, 60, 84)
        for column, width in enumerate(widths):
            if width:
                self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(_A_TYPE, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self._double_clicked)
        self.table.selectionModel().selectionChanged.connect(
            lambda *_args: self._update_controls()
        )
        right_layout.addWidget(self.table, 1)

        pager = QHBoxLayout()
        pager.setSpacing(9)
        self.previous_button = QPushButton("← Önceki", right)
        self.previous_button.clicked.connect(self._previous_page)
        pager.addWidget(self.previous_button)
        self.page_label = mono_label("sayfa 1 · keyset imleç", right, size=8)
        pager.addWidget(self.page_label)
        self.next_button = QPushButton("Sonraki →", right)
        self.next_button.clicked.connect(self._next_page)
        pager.addWidget(self.next_button)
        pager.addStretch(1)
        pager.addWidget(muted_label("Satır", right))
        self.page_size_combo = QComboBox(right)
        self.page_size_combo.addItems(tuple(str(value) for value in PAGE_SIZES))
        self.page_size_combo.setCurrentText(str(self.state.page_size))
        self.page_size_combo.activated.connect(self._page_size_changed)
        pager.addWidget(self.page_size_combo)
        self.open_button = QPushButton("Görüntüyü Aç", right)
        self.open_button.setObjectName("Primary")
        self.open_button.clicked.connect(self._open_selected)
        pager.addWidget(self.open_button)
        right_layout.addLayout(pager)
        root.addWidget(right, 1)

        if fetcher is None:
            self.state.disable(
                "PostgreSQL bağlantısı yapılandırılmadığı için arşiv devre dışı."
            )
        self._render_state()

    # ── yaşam döngüsü (Tk sürümüyle aynı akış) ─────────────────────────────

    def activate(self) -> None:
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

    def deactivate(self) -> None:
        self._active = False
        self._cancel_debounce()

    def poll_results(self, max_items: int = 8) -> int:
        if self._closing or self._fetcher is None or max_items <= 0:
            return 0
        self._result_backlog.extend(self._fetcher.poll())
        processed = 0
        while self._result_backlog and processed < max_items:
            self._apply_result(self._result_backlog.popleft())
            processed += 1
        return processed

    def mark_dirty(self, delay_ms: int = 750) -> None:
        if self._closing or self._fetcher is None:
            return
        self.state.mark_dirty()
        self._tree_dirty = True
        self._render_state()
        if self._active and self.isVisible():
            self._schedule_refresh(max(0, int(delay_ms)), refresh_tree=True)

    def begin_close(self) -> None:
        self._closing = True
        self._active = False
        self._cancel_debounce()
        self._result_backlog.clear()

    # ── etkileşim ───────────────────────────────────────────────────────────

    def _filter_changed(self) -> None:
        if self._closing:
            return
        self.state.set_draft(self.filter_panel.read_draft())

    def _query_changed(self) -> None:
        # Sıralama oku, sorgu başarıyla dönene kadar eski satırlarla çelişmesin.
        if not self.state.has_content:
            self.table_model.set_sort(self.state.sort)
        self._render_state()
        if self._active:
            self._schedule_refresh(400)

    def _refresh_now(self) -> None:
        if self._closing or self._fetcher is None:
            return
        self.state.set_draft(self.filter_panel.read_draft())
        self._cancel_debounce()
        self.state.mark_dirty()
        if not self.state.tree_loaded or self._tree_dirty:
            if not self._tree_pending or self._tree_dirty:
                self._request_tree()
        else:
            self._request_refresh(include_counts=True)

    def _header_clicked(self, section: int) -> None:
        if self._closing or self._fetcher is None:
            return
        sort_column = _SORT_BY_COLUMN.get(section)
        if sort_column is not None:
            self.state.set_sort_column(sort_column)

    def _page_size_changed(self, _index: int) -> None:
        try:
            self.state.set_page_size(int(self.page_size_combo.currentText()))
        except ValueError as exc:
            self.state.set_local_error(str(exc))
            self._render_state()

    # ── debounce ────────────────────────────────────────────────────────────

    def _schedule_refresh(self, delay_ms: int, *, refresh_tree: bool = False) -> None:
        if self._closing or self._fetcher is None:
            return
        self._debounce_tree = self._debounce_tree or refresh_tree
        self._debounce_timer.stop()
        self._debounce_timer.start(max(0, delay_ms))

    def _run_debounced_refresh(self) -> None:
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
        self._debounce_timer.stop()
        if clear_tree:
            self._debounce_tree = False

    # ── fetcher istekleri (Tk sürümünden port) ─────────────────────────────

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
                self.state.set_local_empty("Veritabanında tespit türü bulunamadı.")
                self.table_model.set_rows(())
                self._render_state()
                return
            if not flt.type_ids:
                include_counts = True
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

    # ── sonuç uygulama ──────────────────────────────────────────────────────

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
                result.generation, "Tür ağacı sonucu boş döndü."
            )
            self._render_state()
            return
        if not self.state.apply_tree(result.generation, result.tree):
            return
        self.type_tree.render(result.tree, self.state.counts)
        self._render_state()
        if self._active and not self._tree_dirty and not self._debounce_timer.isActive():
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
                result.generation, "Arşiv sorgusu sonuç sayfası döndürmedi."
            )
            self._render_state()
            return
        if not self.state.apply_refresh(
            result.generation, result.page, result.counts
        ):
            return
        if not self.state.selection.selected_type_ids:
            self.state.message = "Hiçbir tespit türü seçili değil."
        self.table_model.set_rows(result.page.rows)
        self.table_model.set_sort(self.state.applied_sort or self.state.sort)
        self.type_tree.render(self.state.selection.nodes, self.state.counts)
        self._render_state()

    # ── görünüm durumu ──────────────────────────────────────────────────────

    def _render_state(self) -> None:
        view = self.state.view_state
        color = theme.MUTED
        total = self._selected_detection_count()
        self.total_label.setText(_format_count(total) if total else "—")
        if view == ArchiveViewState.DISABLED:
            text = self.state.message or "Tespit arşivi devre dışı."
        elif view == ArchiveViewState.IDLE:
            text = self.state.message or "Arşiv sekmesi açıldığında yüklenecek."
        elif view == ArchiveViewState.LOADING:
            text = self.state.message or "Yükleniyor…"
            color = theme.ACCENT
        elif view == ArchiveViewState.READY:
            row_count = len(self.state.page.rows) if self.state.page else 0
            text = (
                f"{row_count} satır gösteriliyor · seçili filtrelerde"
                f" {_format_count(total)} tespit"
            )
            color = theme.ACCENT
        elif view == ArchiveViewState.EMPTY:
            text = self.state.message or "Kayıt bulunamadı."
        elif view == ArchiveViewState.ERROR:
            text = f"Hata: {self.state.message}"
            color = theme.DANGER_LIGHT
        else:
            detail = self.state.message or "Filtre/veri değişti."
            text = f"{detail} Önceki sonuçlar gösteriliyor."
            color = theme.WARNING
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 11px;"
        )
        enabled = self._fetcher is not None and not self._closing
        self.filter_panel.set_enabled(enabled)
        self.type_tree.set_enabled(enabled)
        self.page_size_combo.setEnabled(enabled)
        self._update_controls()

    def _update_controls(self) -> None:
        pending = self.state.is_refreshing or self._tree_pending or self.state.dirty
        enabled = self._fetcher is not None and not self._closing
        self.previous_button.setEnabled(
            enabled and not pending and self.state.pagination.can_previous
        )
        self.next_button.setEnabled(
            enabled and not pending and self.state.pagination.can_next
        )
        selected = self.selected_row()
        self.open_button.setEnabled(
            enabled and selected is not None and bool(selected.capture_id)
        )
        self.page_label.setText(
            f"sayfa {self.state.pagination.page_number} · keyset imleç"
        )

    def _selected_detection_count(self) -> int:
        selected = self.state.selection.selected_type_ids
        return sum(
            item.count for item in self.state.counts if item.type_id in selected
        )

    def selected_row(self) -> DetectionRow | None:
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return None
        return self.table_model.row_at(indexes[0].row())

    def _double_clicked(self, index: QModelIndex) -> None:
        row = self.table_model.row_at(index.row())
        if row is not None:
            self._open_row(row)

    def _open_selected(self) -> None:
        row = self.selected_row()
        if row is not None:
            self._open_row(row)

    def _open_row(self, row: DetectionRow) -> None:
        if not row.capture_id:
            self.status_label.setText("Bu tespit için saklanmış görüntü yok.")
            self.status_label.setStyleSheet(
                f"color: {theme.WARNING}; background: transparent; font-size: 11px;"
            )
            return
        self.openCaptureRequested.emit(str(row.capture_id), row.ts)

    # Görünürlük değişimleri Tk'daki Map/Unmap akışının karşılığıdır.
    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        if not self._closing:
            self.activate()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.deactivate()
