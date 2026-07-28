"""QGraphicsView tabanlı canlı önizleme.

BGR numpy kare, renk dönüşümü ve PIL olmadan doğrudan ``Format_BGR888``
``QImage`` olarak sarılıp kopyalanır; ölçekleme GPU dostu view dönüşümüyle
yapılır. Kare Tk sürümündeki gibi salt-okunur kabul edilir.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from . import theme

PREVIEW_PLACEHOLDER = "Kaynak seçildikten sonra çıktı burada görünecek"

_MIN_SCALE = 0.05
_MAX_SCALE = 12.0


class PreviewView(QGraphicsView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = QGraphicsPixmapItem()
        self._pixmap_item.setTransformationMode(
            Qt.TransformationMode.SmoothTransformation
        )
        self._scene.addItem(self._pixmap_item)
        self._has_frame = False
        self._user_zoomed = False
        self._placeholder = PREVIEW_PLACEHOLDER
        # Köşe overlay'leri: (widget, köşe) — viewport çocuğu olarak konumlanır.
        self._overlays: list[tuple[QWidget, str]] = []

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setBackgroundBrush(QColor(theme.DEEP_BG))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    # ── kare akışı ──────────────────────────────────────────────────────────

    def set_frame(self, frame) -> None:
        height, width = frame.shape[:2]
        image = QImage(
            frame.data,
            width,
            height,
            frame.strides[0],
            QImage.Format.Format_BGR888,
        ).copy()  # paylaşılan motor buffer'ı; kopya şart
        first = not self._has_frame
        size_changed = self._pixmap_item.pixmap().size() != image.size()
        self._pixmap_item.setPixmap(QPixmap.fromImage(image))
        if first or size_changed:
            self._scene.setSceneRect(QRectF(0, 0, width, height))
            self._has_frame = True
            self.fit()
        self.viewport().update()

    def clear(self) -> None:
        self._has_frame = False
        self._user_zoomed = False
        self._pixmap_item.setPixmap(QPixmap())
        self.resetTransform()
        self.viewport().update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        if not self._has_frame:
            self.viewport().update()

    # ── zoom / pan ──────────────────────────────────────────────────────────

    def fit(self) -> None:
        if not self._has_frame:
            return
        self._user_zoomed = False
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom_in(self) -> None:
        self._zoom_by(1.2)

    def zoom_out(self) -> None:
        self._zoom_by(1 / 1.2)

    def _zoom_by(self, factor: float) -> None:
        if not self._has_frame:
            return
        current = self.transform().m11()
        target = current * factor
        if not _MIN_SCALE <= target <= _MAX_SCALE:
            return
        self._user_zoomed = True
        self.scale(factor, factor)

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt API)
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self._zoom_by(1.15 if delta > 0 else 1 / 1.15)
        event.accept()

    # ── overlay yönetimi ────────────────────────────────────────────────────

    def add_overlay(self, widget: QWidget, corner: str) -> None:
        widget.setParent(self.viewport())
        widget.show()
        self._overlays.append((widget, corner))
        self._place_overlays()

    def _place_overlays(self) -> None:
        margin = 14
        area = self.viewport().rect()
        for widget, corner in self._overlays:
            hint = widget.sizeHint()
            if corner == "top-left":
                widget.move(margin, margin)
            elif corner == "top-right":
                widget.move(area.width() - hint.width() - margin, margin)
            elif corner == "bottom-left":
                widget.move(margin, area.height() - hint.height() - margin)
            else:
                widget.move(
                    area.width() - hint.width() - margin,
                    area.height() - hint.height() - margin,
                )

    def refresh_overlays(self) -> None:
        for widget, _corner in self._overlays:
            widget.adjustSize()
        self._place_overlays()

    # ── Qt olayları ─────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._has_frame and not self._user_zoomed:
            self.fitInView(
                self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio
            )
        self._place_overlays()

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        super().drawForeground(painter, rect)
        if self._has_frame:
            return
        painter.save()
        painter.resetTransform()
        painter.setPen(QColor(theme.DIM))
        font = painter.font()
        font.setPointSize(10)
        font.setLetterSpacing(font.SpacingType.PercentageSpacing, 108)
        painter.setFont(font)
        painter.drawText(
            self.viewport().rect(),
            Qt.AlignmentFlag.AlignCenter,
            self._placeholder,
        )
        painter.restore()
