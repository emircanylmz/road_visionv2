"""Tasarımdaki tekrar eden yapı taşları: segment, toggle, kart, sparkline."""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme


def mono_font(point_size: int = 9, *, bold: bool = False) -> QFont:
    font = QFont("SF Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFamilies(
        ["SF Mono", "JetBrains Mono", "Menlo", "Consolas", "DejaVu Sans Mono"]
    )
    font.setPointSize(point_size)
    font.setBold(bold)
    return font


def section_label(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text.upper(), parent)
    label.setStyleSheet(
        f"color: {theme.MUTED}; font-size: 10px; font-weight: 700;"
        "letter-spacing: 1px; background: transparent;"
    )
    return label


def muted_label(text: str, parent: QWidget | None = None, size: int = 11) -> QLabel:
    label = QLabel(text, parent)
    label.setStyleSheet(
        f"color: {theme.MUTED}; font-size: {size}px; background: transparent;"
    )
    return label


def mono_label(
    text: str,
    parent: QWidget | None = None,
    *,
    size: int = 9,
    color: str = theme.MUTED,
    bold: bool = False,
) -> QLabel:
    label = QLabel(text, parent)
    label.setFont(mono_font(size, bold=bold))
    label.setStyleSheet(f"color: {color}; background: transparent;")
    return label


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")


class Panel(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")


class SegmentedControl(QFrame):
    """Yatay segmentli seçim; ``changed(key)`` sinyali yayar."""

    changed = pyqtSignal(str)

    def __init__(
        self,
        items: Sequence[tuple[str, str]],
        parent: QWidget | None = None,
        *,
        current: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Segment")
        self._buttons: dict[str, QToolButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        for key, label in items:
            button = QToolButton(self)
            button.setObjectName("SegBtn")
            button.setText(label)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            button.clicked.connect(
                lambda _checked=False, value=key: self._on_clicked(value)
            )
            self._group.addButton(button)
            layout.addWidget(button, 1)
            self._buttons[key] = button
        first = current if current in self._buttons else next(iter(self._buttons), None)
        if first is not None:
            self._buttons[first].setChecked(True)
            self._current = first
        else:
            self._current = ""

    def _on_clicked(self, key: str) -> None:
        if key != self._current:
            self._current = key
            self.changed.emit(key)
        else:
            self._buttons[key].setChecked(True)

    def current(self) -> str:
        return self._current

    def set_current(self, key: str) -> None:
        button = self._buttons.get(key)
        if button is None:
            return
        self._current = key
        button.setChecked(True)


class ToggleSwitch(QAbstractButton):
    """30×17 px yeşil anahtar."""

    def __init__(self, parent: QWidget | None = None, *, checked: bool = False) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(30, 17)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        track = QColor(theme.ACCENT if on else theme.BORDER)
        if not self.isEnabled():
            track = QColor(theme.BORDER_FAINT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, 30, 17), 8.5, 8.5)
        knob = QColor(theme.ACCENT_INK if on else theme.MUTED)
        painter.setBrush(knob)
        x = 15.0 if on else 2.0
        painter.drawEllipse(QRectF(x, 2, 13, 13))


class Sparkline(QWidget):
    """14 örneklik mini çubuk grafik."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        color: str = theme.ACCENT_DIM,
        capacity: int = 14,
    ) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self._capacity = max(2, capacity)
        self._color = QColor(color)
        self.setFixedHeight(16)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def push(self, value: float) -> None:
        self._values.append(max(0.0, min(1.0, value)))
        if len(self._values) > self._capacity:
            del self._values[: len(self._values) - self._capacity]
        self.update()

    def clear(self) -> None:
        self._values.clear()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self._values:
            return
        painter = QPainter(self)
        width = self.width()
        height = self.height()
        count = self._capacity
        gap = 2
        bar_width = max(1.0, (width - gap * (count - 1)) / count)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        x = width - len(self._values) * (bar_width + gap) + gap
        for value in self._values:
            bar_height = max(2.0, value * height)
            painter.drawRect(
                QRectF(x, height - bar_height, bar_width, bar_height)
            )
            x += bar_width + gap


class StatCard(Panel):
    """Telemetri kartı: etiket, büyük mono değer, birim, sparkline."""

    def __init__(
        self,
        label: str,
        unit: str = "",
        parent: QWidget | None = None,
        *,
        spark_color: str = theme.ACCENT_DIM,
        value_color: str = theme.TEXT,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 11, 13, 9)
        layout.setSpacing(4)
        layout.addWidget(section_label(label, self))
        value_row = QHBoxLayout()
        value_row.setSpacing(6)
        self.value_label = QLabel("—", self)
        self.value_label.setFont(mono_font(19, bold=True))
        self.value_label.setStyleSheet(
            f"color: {value_color}; background: transparent;"
        )
        value_row.addWidget(self.value_label)
        self.unit_label = mono_label(unit, self, size=9)
        value_row.addWidget(
            self.unit_label, 0, Qt.AlignmentFlag.AlignBaseline
        )
        value_row.addStretch(1)
        layout.addLayout(value_row)
        self.spark = Sparkline(self, color=spark_color)
        layout.addWidget(self.spark)

    def set_value(self, value: str, unit: str | None = None) -> None:
        self.value_label.setText(value)
        if unit is not None:
            self.unit_label.setText(unit)


class CounterCard(Panel):
    """Sol renk şeritli sayaç kartı."""

    def __init__(
        self,
        label: str,
        color: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._strip = QColor(color)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 12, 9)
        layout.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(muted_label(label, self, size=10))
        top.addStretch(1)
        self.delta_label = mono_label("", self, size=8, color=theme.ACCENT)
        top.addWidget(self.delta_label)
        layout.addLayout(top)
        self.value_label = QLabel("—", self)
        self.value_label.setFont(mono_font(15, bold=True))
        self.value_label.setStyleSheet(
            f"color: {theme.TEXT}; background: transparent;"
        )
        layout.addWidget(self.value_label)
        self.sub_label = mono_label("", self, size=8)
        layout.addWidget(self.sub_label)

    def set_values(self, value: str, sub: str = "", delta: str = "") -> None:
        self.value_label.setText(value)
        self.sub_label.setText(sub)
        self.delta_label.setText(delta)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._strip)
        painter.drawRoundedRect(QRectF(1, 6, 3, self.height() - 12), 1.5, 1.5)


class StatusChip(QFrame):
    """Nokta + metin durum rozeti (ok / warn / danger / muted)."""

    _STYLES = {
        "ok": (theme.OK_BG, theme.OK_BORDER, theme.OK_TEXT, theme.ACCENT),
        "warn": (theme.WARNING_BG, theme.WARNING_BORDER, theme.WARNING, theme.WARNING),
        "danger": ("#2a0a10", "#6e2634", "#ffc3cc", theme.DANGER_LIGHT),
        "muted": (theme.HOVER_BG, theme.BORDER, theme.MUTED, theme.MUTED),
    }

    def __init__(
        self,
        text: str = "",
        variant: str = "muted",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 5, 11, 5)
        layout.setSpacing(7)
        self.dot = QLabel("●", self)
        self.dot.setStyleSheet("background: transparent; font-size: 8px;")
        layout.addWidget(self.dot)
        self.label = QLabel(text, self)
        self.label.setFont(mono_font(9))
        layout.addWidget(self.label)
        self.set_state(text, variant)

    def set_state(self, text: str, variant: str = "muted") -> None:
        bg, border, fg, dot = self._STYLES.get(variant, self._STYLES["muted"])
        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 1px solid {border};"
            "border-radius: 13px; }"
        )
        self.label.setText(text)
        self.label.setStyleSheet(
            f"color: {fg}; background: transparent; border: none;"
        )
        self.dot.setStyleSheet(
            f"color: {dot}; background: transparent; border: none; font-size: 8px;"
        )


class KeyValueList(QWidget):
    """Tasarımdaki k/v satır listesi; ``set_rows`` ile yeniden kurulur."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(7)

    def set_rows(self, rows: Sequence[tuple[str, str]]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for key, value in rows:
            row = QFrame(self)
            row.setStyleSheet(
                f"QFrame {{ border: none; border-bottom: 1px solid {theme.HAIRLINE};"
                "background: transparent; }"
            )
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 7)
            row_layout.setSpacing(10)
            row_layout.addWidget(muted_label(key, row))
            row_layout.addStretch(1)
            value_label = mono_label(value, row, size=8, color=theme.TEXT)
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            value_label.setWordWrap(True)
            row_layout.addWidget(value_label)
            self._layout.addWidget(row)


class QuotaBar(QWidget):
    """Etiket + değer + renkli oran çubuğu."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(muted_label(label, self))
        top.addStretch(1)
        self.value_label = mono_label("", self, size=9, color=theme.TEXT)
        top.addWidget(self.value_label)
        layout.addLayout(top)
        self._bar = _RatioBar(self)
        layout.addWidget(self._bar)

    def set_ratio(self, ratio: float, value_text: str, *, warn_at: float = 0.7) -> None:
        self.value_label.setText(value_text)
        color = theme.WARNING if ratio >= warn_at else theme.ACCENT
        dim = theme.WARNING_DIM if ratio >= warn_at else theme.ACCENT_DIM
        self._bar.set_ratio(ratio, color, dim)


class _RatioBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ratio = 0.0
        self._color = QColor(theme.ACCENT)
        self._dim = QColor(theme.ACCENT_DIM)
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_ratio(self, ratio: float, color: str, dim: str) -> None:
        self._ratio = max(0.0, min(1.0, ratio))
        self._color = QColor(color)
        self._dim = QColor(dim)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.INPUT_BG))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), 6), 3, 3)
        if self._ratio <= 0:
            return
        width = max(6.0, self.width() * self._ratio)
        from PyQt6.QtGui import QLinearGradient

        gradient = QLinearGradient(0, 0, width, 0)
        gradient.setColorAt(0.0, self._dim)
        gradient.setColorAt(1.0, self._color)
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(0, 0, width, 6), 3, 3)


class DotLabel(QWidget):
    """Renkli nokta + metin (model rozetleri için)."""

    def __init__(
        self,
        text: str,
        color: str,
        parent: QWidget | None = None,
        *,
        text_color: str = theme.MUTED,
        size: int = 10,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.dot = QLabel("●", self)
        self.dot.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 8px;"
        )
        layout.addWidget(self.dot)
        self.label = QLabel(text, self)
        self.label.setStyleSheet(
            f"color: {text_color}; background: transparent; font-size: {size}px;"
        )
        layout.addWidget(self.label)

    def set_text(self, text: str) -> None:
        self.label.setText(text)


class LevelBadge(QLabel):
    """INFO/WARN/ERROR rozetli mono etiket."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(mono_font(8, bold=True))
        self.set_level("info")

    def set_level(self, level: str) -> None:
        color = theme.LEVEL_COLORS.get(level, theme.MUTED)
        backgrounds = {
            "info": (theme.INFO_BG, theme.INFO_BORDER),
            "warning": (theme.WARNING_BG, theme.WARNING_BORDER),
            "error": (theme.DANGER_BG, "#6e2634"),
        }
        bg, border = backgrounds.get(level, (theme.HOVER_BG, theme.BORDER))
        self.setText(level.upper())
        self.setStyleSheet(
            f"color: {color}; background: {bg}; border: 1px solid {border};"
            "border-radius: 5px; padding: 3px 8px;"
        )
