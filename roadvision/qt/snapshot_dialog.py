"""Tespit Görüntüsü diyaloğu: işaretli / orijinal / yan yana görünümler.

Tk ``SnapshotViewerWindow`` ile aynı durum yüzeyini sunar
(``show_loading`` / ``show_bundle`` / ``show_not_found`` / ``show_error``)
ki App'in asenkron SnapshotFetcher akışı değişmeden bağlanabilsin.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import MODEL_SPECS
from . import theme
from .widgets import (
    KeyValueList,
    SegmentedControl,
    mono_label,
    muted_label,
    section_label,
)

if TYPE_CHECKING:  # yalnız tip için; db modülü çalışma anında da güvenli
    from ..db import CaptureBundle, CaptureMedia

_MODEL_NAMES = {spec.id: spec.display_name for spec in MODEL_SPECS}


class _ImagePane(QLabel):
    """Orana sadık ölçeklenen tek görüntü alanı."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(240, 200)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setStyleSheet(
            f"background: {theme.DEEP_BG}; border: 1px solid {theme.BORDER_FAINT};"
            f"border-radius: 11px; color: {theme.DIM}; font-size: 11px;"
        )

    def set_image(self, data: bytes) -> bool:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self._pixmap = None
            self.setText("JPEG çözümlenemedi")
            return False
        self._pixmap = pixmap
        self._rescale()
        return True

    def set_message(self, text: str) -> None:
        self._pixmap = None
        self.setPixmap(QPixmap())
        self.setText(text)

    def _rescale(self) -> None:
        if self._pixmap is None:
            return
        self.setPixmap(
            self._pixmap.scaled(
                self.size() * self.devicePixelRatioF(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._rescale()


class SnapshotDialog(QDialog):
    refreshRequested = pyqtSignal(str)
    closed = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._capture_id: str | None = None
        self._bundle: CaptureBundle | None = None
        self.setWindowTitle("Tespit Görüntüsü")
        self.resize(1040, 720)
        self.setMinimumSize(640, 460)
        self.setModal(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(self)
        header.setObjectName("Header")
        header.setFixedHeight(52)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 0, 14, 0)
        header_layout.setSpacing(12)
        title = QLabel("Tespit Görüntüsü", header)
        title.setStyleSheet(
            "background: transparent; font-size: 13px; font-weight: 700;"
        )
        header_layout.addWidget(title)
        self.capture_badge = mono_label("—", header, size=8)
        self.capture_badge.setStyleSheet(
            f"color: {theme.MUTED}; background: {theme.HOVER_BG};"
            f"border: 1px solid {theme.BORDER}; border-radius: 5px;"
            "padding: 3px 7px;"
        )
        header_layout.addWidget(self.capture_badge)
        header_layout.addStretch(1)
        self.view_segment = SegmentedControl(
            (
                ("annotated", "İşaretli"),
                ("original", "Orijinal"),
                ("side", "Yan yana"),
            ),
            header,
        )
        self.view_segment.changed.connect(lambda _key: self._render_images())
        header_layout.addWidget(self.view_segment)
        refresh_button = QPushButton("Yenile", header)
        refresh_button.clicked.connect(self._refresh)
        header_layout.addWidget(refresh_button)
        save_button = QPushButton("⤓ Kaydet", header)
        save_button.clicked.connect(self._save)
        header_layout.addWidget(save_button)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        image_box = QVBoxLayout()
        image_box.setContentsMargins(14, 14, 14, 14)
        image_box.setSpacing(10)
        panes = QHBoxLayout()
        panes.setSpacing(10)
        self.primary_pane = _ImagePane(self)
        panes.addWidget(self.primary_pane, 1)
        self.secondary_pane = _ImagePane(self)
        self.secondary_pane.hide()
        panes.addWidget(self.secondary_pane, 1)
        image_box.addLayout(panes, 1)
        self.status_label = muted_label("", self)
        image_box.addWidget(self.status_label)
        body.addLayout(image_box, 1)

        side = QFrame(self)
        side.setFixedWidth(296)
        side.setStyleSheet(
            f"QFrame {{ background: {theme.RAIL_BG};"
            f"border-left: 1px solid {theme.BORDER_FAINT}; }}"
        )
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(14, 14, 14, 14)
        side_layout.setSpacing(10)
        side_layout.addWidget(section_label("Bu Karenin Tespitleri", side))
        self.detections_box = QVBoxLayout()
        self.detections_box.setSpacing(8)
        side_layout.addLayout(self.detections_box)
        side_layout.addSpacing(4)
        side_layout.addWidget(section_label("Kayıt Bilgisi", side))
        self.meta_list = KeyValueList(side)
        side_layout.addWidget(self.meta_list)
        side_layout.addStretch(1)
        body.addWidget(side)
        root.addLayout(body, 1)

    # ── Tk viewer ile aynı durum yüzeyi ────────────────────────────────────

    def exists(self) -> bool:
        return self.isVisible()

    def focus(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_loading(self, capture_id: str, message: str = "Görüntü yükleniyor…") -> None:
        self._capture_id = capture_id
        self._bundle = None
        self.setWindowTitle(f"Tespit Görüntüsü — {capture_id}")
        self.capture_badge.setText(capture_id[:12])
        self.status_label.setText(message)
        self.primary_pane.set_message(message)
        self.secondary_pane.set_message("")
        self._set_detections(())
        self.meta_list.set_rows(())
        self.show()
        self.focus()

    def show_bundle(self, bundle: CaptureBundle) -> None:
        self._capture_id = bundle.capture_id
        self._bundle = bundle
        timestamp = bundle.ts.astimezone().strftime("%d.%m.%Y %H:%M:%S")
        source = bundle.source_name or bundle.source_kind or "Bilinmeyen kaynak"
        badge = " · yeniden işleme" if bundle.is_reprocess else ""
        self.setWindowTitle(f"{timestamp} — {source}{badge}")
        self.status_label.setText("")
        rows: list[tuple[str, str]] = [
            ("Capture", bundle.capture_id[:16]),
            ("Zaman", timestamp),
            ("Kaynak", source),
        ]
        if bundle.run_id is not None:
            rows.append(("Run", str(bundle.run_id)))
        annotated_kb = len(bundle.annotated.data) // 1024 if bundle.annotated else 0
        original_kb = len(bundle.original.data) // 1024 if bundle.original else 0
        rows.append(("Boyut", f"{annotated_kb} KB işaretli · {original_kb} KB ham"))
        if bundle.is_reprocess:
            rows.append(("Not", "yeniden işleme kaydı"))
        self.meta_list.set_rows(rows)
        self._set_detections(bundle.models)
        self._render_images()

    def show_not_found(self, message: str) -> None:
        self._bundle = None
        self.status_label.setText(message)
        self.primary_pane.set_message(message)
        self.secondary_pane.set_message("")

    def show_error(self, message: str) -> None:
        self._bundle = None
        rendered = f"Veritabanı görüntüsü okunamadı: {message}"
        self.status_label.setText(rendered)
        self.primary_pane.set_message(rendered)
        self.secondary_pane.set_message("")

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

    # ── iç işleyiş ──────────────────────────────────────────────────────────

    def _set_detections(self, models) -> None:
        while self.detections_box.count():
            item = self.detections_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not models:
            self.detections_box.addWidget(
                muted_label("Model bilgisi yok.", self)
            )
            return
        for model_id, object_count, _signature in models:
            color = theme.color_for_model(model_id)
            card = QFrame(self)
            card.setStyleSheet(
                f"QFrame {{ background: {theme.CARD_BG}; border: 1px solid"
                f" {theme.BORDER_SOFT}; border-radius: 9px; }}"
            )
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(11, 10, 11, 10)
            card_layout.setSpacing(8)
            dot = QLabel("●", card)
            dot.setStyleSheet(
                f"color: {color}; background: transparent; border: none;"
                "font-size: 8px;"
            )
            card_layout.addWidget(dot)
            name = QLabel(_MODEL_NAMES.get(model_id, model_id), card)
            name.setStyleSheet(
                "background: transparent; border: none; font-size: 12px;"
                "font-weight: 700;"
            )
            card_layout.addWidget(name, 1)
            count = mono_label(f"{object_count} nesne", card, size=9, color=color)
            count.setStyleSheet(
                f"color: {color}; background: transparent; border: none;"
            )
            card_layout.addWidget(count)
            self.detections_box.addWidget(card)

    def _selected_media(self) -> "CaptureMedia | None":
        if self._bundle is None:
            return None
        if self.view_segment.current() == "original":
            return self._bundle.original
        return self._bundle.annotated

    def _render_images(self) -> None:
        if self._bundle is None:
            return
        mode = self.view_segment.current()
        if mode == "side":
            self.secondary_pane.show()
            if self._bundle.annotated is not None:
                self.primary_pane.set_image(self._bundle.annotated.data)
            else:
                self.primary_pane.set_message("İşaretli görüntü yok")
            if self._bundle.original is not None:
                self.secondary_pane.set_image(self._bundle.original.data)
            else:
                self.secondary_pane.set_message("Orijinal görüntü yok")
            return
        self.secondary_pane.hide()
        media = self._selected_media()
        if media is None:
            self.primary_pane.set_message("Bu görünüm için kayıtlı görüntü yok")
            return
        self.primary_pane.set_image(media.data)

    def _refresh(self) -> None:
        if self._capture_id:
            self.refreshRequested.emit(self._capture_id)

    def _save(self) -> None:
        media = self._selected_media()
        if media is None or self._capture_id is None:
            self.status_label.setText("Kaydedilecek görüntü henüz yüklenmedi.")
            return
        kind = (
            "original" if self.view_segment.current() == "original" else "annotated"
        )
        target, _selected = QFileDialog.getSaveFileName(
            self,
            "Tespit görüntüsünü kaydet",
            f"{self._capture_id}-{kind}.jpg",
            "JPEG görüntüsü (*.jpg);;Tüm dosyalar (*.*)",
        )
        if not target:
            return
        try:
            Path(target).write_bytes(media.data)
            self.status_label.setText(f"Kaydedildi: {target}")
        except OSError as exc:
            QMessageBox.critical(self, "Görüntü kaydedilemedi", str(exc))

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().closeEvent(event)
        self.closed.emit(self)
