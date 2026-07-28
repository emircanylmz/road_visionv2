"""Oturum Günlüğü: QTableView + seviye filtreleri + ayrıntı çekmecesi."""

from __future__ import annotations

import json
from datetime import datetime

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QRectF,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QGuiApplication, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import MODEL_SPECS
from ..logbook import LogRecord
from . import theme
from .widgets import (
    LevelBadge,
    SegmentedControl,
    StatusChip,
    mono_font,
    mono_label,
    muted_label,
)

MAX_VISIBLE_LOG_ROWS = 1000

_COLUMNS = ("SAAT", "◉", "SEVİYE", "KATEGORİ", "MODEL", "MESAJ", "SÜRE")
_COL_TIME, _COL_CAP, _COL_LEVEL, _COL_CATEGORY, _COL_MODEL, _COL_MESSAGE, _COL_DUR = (
    range(7)
)

_MODEL_NAMES = {spec.id: spec.short_name for spec in MODEL_SPECS}


def _duration_text(record: LogRecord) -> str:
    payload = record.payload or {}
    for key in ("elapsed_ms", "total_ms", "duration_ms"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return f"{value:.1f} ms"
    return "—"


class LogTableModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._records: list[LogRecord] = []

    # Qt modeli API'si ------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self._records)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(_COLUMNS)

    def headerData(self, section, orientation, role):  # noqa: N802
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return _COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role):
        if not index.isValid():
            return None
        record = self._records[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.UserRole:
            return record
        if role == Qt.ItemDataRole.DisplayRole:
            if column == _COL_TIME:
                return datetime.fromtimestamp(record.timestamp).strftime(
                    "%H:%M:%S.%f"
                )[:-3]
            if column == _COL_CAP:
                return "◉" if self.capture_id(record) else ""
            if column == _COL_LEVEL:
                return record.level.value.upper()
            if column == _COL_CATEGORY:
                return record.category.value
            if column == _COL_MODEL:
                if not record.model_id:
                    return "—"
                return _MODEL_NAMES.get(record.model_id, record.model_id)
            if column == _COL_MESSAGE:
                return record.message
            if column == _COL_DUR:
                return _duration_text(record)
        if role == Qt.ItemDataRole.ForegroundRole:
            level_color = theme.LEVEL_COLORS.get(record.level.value, theme.TEXT)
            if column == _COL_LEVEL:
                return QColor(level_color)
            if column == _COL_CAP:
                return QColor(theme.ACCENT if self.capture_id(record) else theme.DIM)
            if column in (_COL_CATEGORY, _COL_DUR):
                return QColor(theme.MUTED)
            if column == _COL_MODEL:
                return QColor(theme.TEXT_SOFT)
            if column == _COL_TIME:
                return QColor(theme.TEXT_SOFT)
            return QColor(theme.TEXT)
        if role == Qt.ItemDataRole.BackgroundRole:
            if record.level.value == "error":
                return QColor(theme.DANGER_BG)
            if record.level.value == "warning":
                return QColor("#191710")
        if role == Qt.ItemDataRole.FontRole and column in (
            _COL_TIME,
            _COL_LEVEL,
            _COL_CATEGORY,
            _COL_DUR,
        ):
            return mono_font(8, bold=column == _COL_LEVEL)
        return None

    # Yardımcılar ------------------------------------------------------------

    @staticmethod
    def capture_id(record: LogRecord) -> str:
        payload = record.payload or {}
        value = payload.get("capture_id")
        return str(value).strip() if value is not None else ""

    def record_at(self, row: int) -> LogRecord | None:
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def append_records(self, records: list[LogRecord]) -> int:
        """Kayıtları ekler, üst sınırı aşarsa en eskileri düşürür."""

        if not records:
            return 0
        first = len(self._records)
        self.beginInsertRows(QModelIndex(), first, first + len(records) - 1)
        self._records.extend(records)
        self.endInsertRows()
        excess = len(self._records) - MAX_VISIBLE_LOG_ROWS
        if excess > 0:
            self.beginRemoveRows(QModelIndex(), 0, excess - 1)
            del self._records[:excess]
            self.endRemoveRows()
        return len(records)

    def clear(self) -> None:
        self.beginResetModel()
        self._records.clear()
        self.endResetModel()

    def total(self) -> int:
        return len(self._records)


class LogFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._level: str | None = None
        self._needle = ""

    def set_level(self, level: str | None) -> None:
        self._level = level
        self.invalidateRowsFilter()

    def set_needle(self, needle: str) -> None:
        self._needle = needle.strip().casefold()
        self.invalidateRowsFilter()

    def filterAcceptsRow(self, source_row, source_parent) -> bool:  # noqa: N802
        model = self.sourceModel()
        record = model.record_at(source_row)
        if record is None:
            return False
        if self._level is not None and record.level.value != self._level:
            return False
        if self._needle:
            haystack = f"{record.message} {record.category.value} {record.model_id or ''}".casefold()
            if self._needle not in haystack:
                return False
        return True


class _LevelStripeDelegate(QStyledItemDelegate):
    """Satırın soluna seviye renginde 3px şerit çizer."""

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        if index.column() != _COL_TIME:
            return
        record = index.data(Qt.ItemDataRole.UserRole)
        if record is None:
            return
        color = theme.LEVEL_COLORS.get(record.level.value, theme.DIM)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        rect = option.rect
        painter.drawRoundedRect(
            QRectF(rect.left() + 4, rect.top() + 7, 3, rect.height() - 14), 1.5, 1.5
        )
        painter.restore()


class LogPage(QWidget):
    openCaptureRequested = pyqtSignal(str, float)
    clearRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._counts = {"all": 0, "info": 0, "warning": 0, "error": 0}
        self._auto_scroll = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        toolbar = QWidget(self)
        toolbar.setStyleSheet(
            f"border-bottom: 1px solid {theme.BORDER_FAINT}; background: transparent;"
        )
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(16, 12, 16, 12)
        toolbar_layout.setSpacing(9)
        self.level_segment = SegmentedControl(
            (
                ("all", "Tümü"),
                ("info", "Bilgi"),
                ("warning", "Uyarı"),
                ("error", "Hata"),
            ),
            toolbar,
        )
        self.level_segment.setStyleSheet("border: none;")
        self.level_segment.changed.connect(self._level_changed)
        toolbar_layout.addWidget(self.level_segment)
        toolbar_layout.addStretch(1)
        self.search_edit = QLineEdit(toolbar)
        self.search_edit.setPlaceholderText("⌕  mesaj ara…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(230)
        self.search_edit.setStyleSheet("border-radius: 8px;")
        self.search_edit.textChanged.connect(self._needle_changed)
        toolbar_layout.addWidget(self.search_edit)
        clear_button = QPushButton("Temizle", toolbar)
        clear_button.clicked.connect(self.clearRequested.emit)
        toolbar_layout.addWidget(clear_button)
        root.addWidget(toolbar)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        table_box = QVBoxLayout()
        table_box.setContentsMargins(0, 0, 0, 0)
        table_box.setSpacing(0)
        self.model = LogTableModel(self)
        self.proxy = LogFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView(self)
        self.table.setModel(self.proxy)
        self.table.setItemDelegate(_LevelStripeDelegate(self.table))
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(33)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        widths = (104, 30, 72, 96, 128, 0, 70)
        for column, width in enumerate(widths):
            if width:
                self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(
            _COL_MESSAGE, QHeaderView.ResizeMode.Stretch
        )
        self.table.doubleClicked.connect(self._double_clicked)
        self.table.selectionModel().currentRowChanged.connect(self._row_changed)
        self.model.rowsInserted.connect(self._rows_inserted)
        table_box.addWidget(self.table, 1)

        footer = QWidget(self)
        footer.setStyleSheet(
            f"border-top: 1px solid {theme.BORDER_FAINT}; background: transparent;"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 7, 16, 7)
        footer_layout.setSpacing(10)
        self.count_label = mono_label("0 kayıt", footer, size=8)
        footer_layout.addWidget(self.count_label)
        footer_layout.addStretch(1)
        from .widgets import ToggleSwitch

        self.autoscroll_toggle = ToggleSwitch(footer, checked=True)
        self.autoscroll_toggle.toggled.connect(self._autoscroll_toggled)
        footer_layout.addWidget(self.autoscroll_toggle)
        footer_layout.addWidget(muted_label("Otomatik kaydır", footer))
        table_box.addWidget(footer)
        content.addLayout(table_box, 1)

        content.addWidget(self._build_drawer())
        root.addLayout(content, 1)

    # ── ayrıntı çekmecesi ───────────────────────────────────────────────────

    def _build_drawer(self) -> QWidget:
        drawer = QFrame(self)
        drawer.setFixedWidth(340)
        drawer.setStyleSheet(
            f"QFrame {{ background: {theme.RAIL_BG};"
            f"border-left: 1px solid {theme.BORDER_FAINT}; }}"
        )
        layout = QVBoxLayout(drawer)
        layout.setContentsMargins(15, 14, 15, 14)
        layout.setSpacing(13)
        title_row = QHBoxLayout()
        title = QLabel("Kayıt ayrıntısı", drawer)
        title.setStyleSheet(
            "background: transparent; border: none; font-size: 12px;"
            "font-weight: 700;"
        )
        title_row.addWidget(title)
        title_row.addStretch(1)
        layout.addLayout(title_row)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)
        self.detail_badge = LevelBadge(drawer)
        badge_row.addWidget(self.detail_badge)
        self.detail_time = mono_label("", drawer, size=9, color=theme.TEXT_SOFT)
        self.detail_time.setStyleSheet(
            f"color: {theme.TEXT_SOFT}; background: transparent; border: none;"
        )
        badge_row.addWidget(self.detail_time)
        badge_row.addStretch(1)
        layout.addLayout(badge_row)

        self.detail_message = QLabel("Bir kayıt seçin.", drawer)
        self.detail_message.setWordWrap(True)
        self.detail_message.setStyleSheet(
            "background: transparent; border: none; font-size: 13px;"
            "font-weight: 700;"
        )
        layout.addWidget(self.detail_message)

        from .widgets import KeyValueList

        self.detail_rows = KeyValueList(drawer)
        layout.addWidget(self.detail_rows)

        self.detail_json = QLabel("", drawer)
        self.detail_json.setWordWrap(True)
        self.detail_json.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.detail_json.setFont(mono_font(8))
        self.detail_json.setStyleSheet(
            f"color: {theme.MUTED}; background: {theme.INPUT_BG};"
            f"border: 1px solid {theme.BORDER_FAINT}; border-radius: 9px;"
            "padding: 11px;"
        )
        self.detail_json.hide()
        layout.addWidget(self.detail_json)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(7)
        self.open_button = QPushButton("Görüntüyü Aç", drawer)
        self.open_button.setObjectName("Primary")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_capture)
        buttons.addWidget(self.open_button, 1)
        self.copy_button = QToolButton(drawer)
        self.copy_button.setObjectName("IconBtn")
        self.copy_button.setText("⧉")
        self.copy_button.setToolTip("Kaydı JSON olarak kopyala")
        self.copy_button.setFixedSize(44, 40)
        self.copy_button.clicked.connect(self._copy_record)
        buttons.addWidget(self.copy_button)
        layout.addLayout(buttons)
        self._selected_record: LogRecord | None = None
        return drawer

    # ── veri akışı ──────────────────────────────────────────────────────────

    def append_records(self, records: list[LogRecord]) -> None:
        self.model.append_records(records)
        for record in records:
            self._counts["all"] += 1
            if record.level.value in self._counts:
                self._counts[record.level.value] += 1
        self._update_counts()

    def clear(self) -> None:
        self.model.clear()
        self._counts = {key: 0 for key in self._counts}
        self._update_counts()
        self._show_record(None)

    def _update_counts(self) -> None:
        self.count_label.setText(
            f"{self._counts['all']} kayıt · görünür {self.proxy.rowCount()}"
        )

    def _rows_inserted(self, *_args) -> None:
        if self._auto_scroll:
            self.table.scrollToBottom()
        self._update_counts()

    # ── etkileşim ───────────────────────────────────────────────────────────

    def _level_changed(self, key: str) -> None:
        self.proxy.set_level(None if key == "all" else key)
        self._update_counts()

    def _needle_changed(self, text: str) -> None:
        self.proxy.set_needle(text)
        self._update_counts()

    def _autoscroll_toggled(self, checked: bool) -> None:
        self._auto_scroll = checked

    def _row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        record = current.data(Qt.ItemDataRole.UserRole) if current.isValid() else None
        self._show_record(record)

    def _double_clicked(self, index: QModelIndex) -> None:
        record = index.data(Qt.ItemDataRole.UserRole)
        if record is None:
            return
        capture_id = LogTableModel.capture_id(record)
        if capture_id:
            self.openCaptureRequested.emit(capture_id, record.timestamp)

    def _show_record(self, record: LogRecord | None) -> None:
        self._selected_record = record
        if record is None:
            self.detail_badge.set_level("info")
            self.detail_time.setText("")
            self.detail_message.setText("Bir kayıt seçin.")
            self.detail_rows.set_rows(())
            self.detail_json.hide()
            self.open_button.setEnabled(False)
            return
        self.detail_badge.set_level(record.level.value)
        self.detail_time.setText(
            datetime.fromtimestamp(record.timestamp).strftime("%H:%M:%S.%f")[:-3]
        )
        self.detail_message.setText(record.message)
        capture_id = LogTableModel.capture_id(record)
        rows: list[tuple[str, str]] = [("Kategori", record.category.value)]
        rows.append(
            ("Run", str(record.run_id) if record.run_id is not None else "—")
        )
        if record.model_id:
            rows.append(
                ("Model", _MODEL_NAMES.get(record.model_id, record.model_id))
            )
        if capture_id:
            rows.append(("Capture", capture_id[:12]))
        self.detail_rows.set_rows(rows)
        payload = record.payload or {}
        if payload:
            self.detail_json.setText(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            )
            self.detail_json.show()
        else:
            self.detail_json.hide()
        self.open_button.setEnabled(bool(capture_id))

    def _open_capture(self) -> None:
        record = self._selected_record
        if record is None:
            return
        capture_id = LogTableModel.capture_id(record)
        if capture_id:
            self.openCaptureRequested.emit(capture_id, record.timestamp)

    def _copy_record(self) -> None:
        record = self._selected_record
        if record is None:
            return
        payload = {
            "time": datetime.fromtimestamp(record.timestamp).isoformat(),
            "level": record.level.value,
            "category": record.category.value,
            "run_id": record.run_id,
            "model_id": record.model_id,
            "message": record.message,
            "payload": record.payload,
        }
        QGuiApplication.clipboard().setText(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        )
