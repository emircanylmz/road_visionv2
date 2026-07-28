"""Canlı Önizleme sayfası: sidebar + telemetri + önizleme + sayaçlar."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import APP_CONFIG, MODEL_SPECS, ModelSpec
from . import theme
from .preview import PreviewView
from .widgets import (
    Card,
    DotLabel,
    CounterCard,
    SegmentedControl,
    StatCard,
    StatusChip,
    ToggleSwitch,
    mono_label,
    muted_label,
    section_label,
)

PROFILE_LABELS = ("Hızlı", "Dengeli", "Kalite")


class ModelCard(Card):
    """Model seçim kartı: seçim, renk, canlı istatistik, güven kaydırağı."""

    toggled = pyqtSignal(str, bool)
    confidenceCommitted = pyqtSignal(str, float)
    annotationToggled = pyqtSignal(str, bool)

    def __init__(self, spec: ModelSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        color = theme.MODEL_HEX[spec.id]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 11, 12, 11)
        layout.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(9)
        self.check = QToolButton(self)
        self.check.setCheckable(True)
        self.check.setFixedSize(18, 18)
        self.check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check.setStyleSheet(
            "QToolButton { background: transparent; border: 1px solid"
            f" {theme.CHIP_BORDER}; border-radius: 5px; padding: 0;"
            f" color: transparent; font-size: 10px; font-weight: 700; }}"
            f"QToolButton:checked {{ background: {theme.ACCENT};"
            f" border-color: {theme.ACCENT}; color: {theme.ACCENT_INK}; }}"
        )
        self.check.setText("✓")
        self.check.toggled.connect(self._on_toggled)
        top.addWidget(self.check)
        dot = QLabel("●", self)
        dot.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 9px;"
        )
        top.addWidget(dot)
        name = QLabel(spec.display_name, self)
        name.setWordWrap(True)
        name.setStyleSheet(
            "background: transparent; font-size: 12px; font-weight: 700;"
        )
        top.addWidget(name, 1)
        self.ms_badge = mono_label("—", self, size=8)
        self.ms_badge.setStyleSheet(
            f"color: {theme.MUTED}; background: {theme.INPUT_BG};"
            "border-radius: 5px; padding: 2px 6px;"
        )
        top.addWidget(self.ms_badge)
        layout.addLayout(top)

        middle = QHBoxLayout()
        middle.setSpacing(7)
        task_text = (
            f"semantic · {spec.input_size}"
            if spec.task == "semantic"
            else f"{spec.task} · {spec.input_size}"
        )
        task_pill = QLabel(task_text, self)
        task_pill.setStyleSheet(
            f"color: {theme.MUTED}; background: {theme.INPUT_BG};"
            "border-radius: 9px; padding: 3px 8px; font-size: 9px;"
        )
        middle.addWidget(task_pill)
        self.live_label = mono_label("", self, size=8, color=color)
        middle.addWidget(self.live_label)
        middle.addStretch(1)
        self._is_semantic = spec.task == "semantic"
        self.annotation_button = QToolButton(self)
        self.annotation_button.setCheckable(True)
        self.annotation_button.setChecked(True)
        self.annotation_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.annotation_button.setText("MSK" if self._is_semantic else "BOX")
        self.annotation_button.setToolTip(
            "Maske çizimini aç/kapat" if self._is_semantic else "Kutu çizimini aç/kapat"
        )
        self.annotation_button.setStyleSheet(
            "QToolButton { background:"
            f" {theme.INPUT_BG}; color: {theme.DISABLED}; border-radius: 5px;"
            " padding: 3px 7px; font-size: 9px; font-weight: 700; }"
            f"QToolButton:checked {{ background: {theme.SEG_ACTIVE_TOP};"
            f" color: {theme.TEXT}; }}"
        )
        self.annotation_button.toggled.connect(
            lambda checked: self.annotationToggled.emit(self.spec.id, checked)
        )
        middle.addWidget(self.annotation_button)
        layout.addLayout(middle)

        bottom = QHBoxLayout()
        bottom.setSpacing(9)
        bottom.addWidget(muted_label("Güven", self, size=10))
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(10, 90)
        self.slider.setValue(round(APP_CONFIG.confidence * 100))
        self.slider.setStyleSheet(
            f"QSlider::sub-page:horizontal {{ background: {color};"
            "border-radius: 3px; }"
        )
        self.slider.valueChanged.connect(self._on_slider_changed)
        self.slider.sliderReleased.connect(self._commit_confidence)
        bottom.addWidget(self.slider, 1)
        self.conf_label = mono_label(
            f"{APP_CONFIG.confidence:.2f}", self, size=9, color=theme.TEXT
        )
        self.conf_label.setFixedWidth(34)
        self.conf_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        bottom.addWidget(self.conf_label)
        layout.addLayout(bottom)

    def _on_toggled(self, checked: bool) -> None:
        self.setProperty("selected", "true" if checked else "false")
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.toggled.emit(self.spec.id, checked)

    def _on_slider_changed(self, value: int) -> None:
        self.conf_label.setText(f"{value / 100:.2f}")
        if not self.slider.isSliderDown():
            # Klavye/tık kaynaklı değişimlerde bırakma olayı gelmez.
            self._commit_confidence()

    def _commit_confidence(self) -> None:
        self.confidenceCommitted.emit(self.spec.id, self.confidence())

    def confidence(self) -> float:
        return self.slider.value() / 100

    def is_selected(self) -> bool:
        return self.check.isChecked()

    def set_selected(self, selected: bool) -> None:
        self.check.setChecked(selected)

    def annotation_enabled(self) -> bool:
        return self.annotation_button.isChecked()

    def set_runtime(self, ms_text: str, live_text: str) -> None:
        self.ms_badge.setText(ms_text)
        self.live_label.setText(live_text)

    def reset_runtime(self) -> None:
        self.set_runtime("—", "")


class LivePage(QWidget):
    sourceKindChanged = pyqtSignal(str)
    cameraSelected = pyqtSignal()
    refreshCamerasClicked = pyqtSignal()
    chooseFileClicked = pyqtSignal()
    modelToggled = pyqtSignal(str, bool)
    selectAllClicked = pyqtSignal()
    confidenceCommitted = pyqtSignal(str, float)
    annotationToggled = pyqtSignal(str, bool)
    profileChanged = pyqtSignal(str)
    startStopClicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model_cards: dict[str, ModelCard] = {}
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setHandleWidth(5)
        self.splitter.setChildrenCollapsible(False)
        root.addWidget(self.splitter)
        self.splitter.addWidget(self._build_sidebar())
        self.splitter.addWidget(self._build_main())
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([312, 1000])

    # ── sidebar ─────────────────────────────────────────────────────────────

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame(self)
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(272)
        sidebar.setMaximumWidth(420)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # Girdi kaynağı kartı
        source_card = Card(sidebar)
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(13, 13, 13, 13)
        source_layout.setSpacing(11)
        source_header = QHBoxLayout()
        source_header.addWidget(section_label("Girdi Kaynağı", source_card))
        source_header.addStretch(1)
        self.source_state_label = mono_label("", source_card, size=8, color=theme.ACCENT)
        source_header.addWidget(self.source_state_label)
        source_layout.addLayout(source_header)
        self.source_segment = SegmentedControl(
            (("camera", "Kamera"), ("image", "Fotoğraf"), ("video", "Video")),
            source_card,
        )
        self.source_segment.changed.connect(self.sourceKindChanged.emit)
        source_layout.addWidget(self.source_segment)

        self.camera_row = QWidget(source_card)
        camera_layout = QHBoxLayout(self.camera_row)
        camera_layout.setContentsMargins(0, 0, 0, 0)
        camera_layout.setSpacing(7)
        self.camera_combo = QComboBox(self.camera_row)
        self.camera_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.camera_combo.activated.connect(lambda _index: self.cameraSelected.emit())
        camera_layout.addWidget(self.camera_combo, 1)
        self.refresh_button = QToolButton(self.camera_row)
        self.refresh_button.setObjectName("IconBtn")
        self.refresh_button.setText("⟳")
        self.refresh_button.setToolTip("Kameraları yeniden tara")
        self.refresh_button.clicked.connect(self.refreshCamerasClicked.emit)
        camera_layout.addWidget(self.refresh_button)
        source_layout.addWidget(self.camera_row)

        self.file_row = QWidget(source_card)
        file_layout = QHBoxLayout(self.file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(7)
        self.file_label = QLabel("Henüz dosya seçilmedi", self.file_row)
        self.file_label.setStyleSheet(
            f"color: {theme.MUTED}; background: {theme.INPUT_BG};"
            f"border: 1px solid {theme.BORDER_SOFT}; border-radius: 7px;"
            "padding: 8px 10px; font-size: 11px;"
        )
        self.file_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        file_layout.addWidget(self.file_label, 1)
        choose_button = QPushButton("Dosya Seç", self.file_row)
        choose_button.clicked.connect(self.chooseFileClicked.emit)
        file_layout.addWidget(choose_button)
        source_layout.addWidget(self.file_row)
        layout.addWidget(source_card)

        # Model listesi
        models_header = QHBoxLayout()
        models_header.setContentsMargins(2, 0, 2, 0)
        models_header.addWidget(section_label("Modeller", sidebar))
        count_pill = mono_label(f"{len(MODEL_SPECS)} etkin", sidebar, size=8)
        count_pill.setStyleSheet(
            f"color: {theme.MUTED}; background: {theme.HOVER_BG};"
            "border-radius: 9px; padding: 2px 7px;"
        )
        models_header.addWidget(count_pill)
        models_header.addStretch(1)
        select_all = QToolButton(sidebar)
        select_all.setText("TÜMÜ")
        select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        select_all.setStyleSheet(
            f"QToolButton {{ color: {theme.ACCENT}; background: transparent;"
            "border: none; font-size: 10px; font-weight: 700; padding: 2px; }"
        )
        select_all.clicked.connect(self.selectAllClicked.emit)
        models_header.addWidget(select_all)
        layout.addLayout(models_header)

        scroll = QScrollArea(sidebar)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        models_box = QWidget(scroll)
        models_box.setStyleSheet("background: transparent;")
        models_layout = QVBoxLayout(models_box)
        models_layout.setContentsMargins(0, 0, 0, 0)
        models_layout.setSpacing(8)
        for spec in MODEL_SPECS:
            card = ModelCard(spec, models_box)
            card.toggled.connect(self.modelToggled.emit)
            card.confidenceCommitted.connect(self.confidenceCommitted.emit)
            card.annotationToggled.connect(self.annotationToggled.emit)
            models_layout.addWidget(card)
            self.model_cards[spec.id] = card
        models_layout.addStretch(1)
        scroll.setWidget(models_box)
        layout.addWidget(scroll, 1)

        # Performans profili
        profile_card = Card(sidebar)
        profile_layout = QVBoxLayout(profile_card)
        profile_layout.setContentsMargins(13, 13, 13, 13)
        profile_layout.setSpacing(10)
        profile_header = QHBoxLayout()
        profile_header.addWidget(section_label("Performans Profili", profile_card))
        profile_header.addStretch(1)
        profile_header.addWidget(mono_label("1024/768/640", profile_card, size=8))
        profile_layout.addLayout(profile_header)
        self.profile_segment = SegmentedControl(
            tuple((label, label) for label in PROFILE_LABELS),
            profile_card,
            current="Kalite",
        )
        self.profile_segment.changed.connect(self.profileChanged.emit)
        profile_layout.addWidget(self.profile_segment)
        layout.addWidget(profile_card)

        # Başlat / Durdur + medya alt bilgisi
        self.start_button = QPushButton("BAŞLAT", sidebar)
        self.start_button.setObjectName("Primary")
        self.start_button.setMinimumHeight(46)
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.clicked.connect(self.startStopClicked.emit)
        layout.addWidget(self.start_button)
        footer = QHBoxLayout()
        self.media_state_label = mono_label("", sidebar, size=8)
        footer.addWidget(self.media_state_label)
        footer.addStretch(1)
        self.media_quota_label = mono_label("", sidebar, size=8)
        footer.addWidget(self.media_quota_label)
        layout.addLayout(footer)
        return sidebar

    # ── ana alan ────────────────────────────────────────────────────────────

    def _build_main(self) -> QWidget:
        main = QWidget(self)
        layout = QVBoxLayout(main)
        layout.setContentsMargins(14, 14, 14, 8)
        layout.setSpacing(12)

        telemetry_row = QHBoxLayout()
        telemetry_row.setSpacing(10)
        self.stat_fps = StatCard("FPS", "kare/sn", main, spark_color="#2b7a5c")
        self.stat_ms = StatCard("Toplam Çıkarım", "ms", main, spark_color="#2a5b80")
        self.stat_frames = StatCard("İşlenen Kare", "seq", main, spark_color="#41546a")
        self.stat_objects = StatCard("Tespit", "nesne", main, spark_color="#7a6427")
        self.stat_media = StatCard("Kaydedilen", "görüntü", main, spark_color="#41546a")
        for card in (
            self.stat_fps,
            self.stat_ms,
            self.stat_frames,
            self.stat_objects,
            self.stat_media,
        ):
            telemetry_row.addWidget(card, 1)
        layout.addLayout(telemetry_row)

        self.preview = PreviewView(main)
        self.preview.setStyleSheet(
            f"QGraphicsView {{ border: 1px solid {theme.BORDER_FAINT};"
            "border-radius: 12px; }"
        )
        layout.addWidget(self.preview, 1)

        # Önizleme overlay'leri
        self.frame_chip = mono_label("", None, size=8, color=theme.TEXT)
        self.frame_chip.setStyleSheet(
            f"color: {theme.TEXT}; background: rgba(8,12,17,0.78);"
            f"border: 1px solid {theme.BORDER}; border-radius: 7px;"
            "padding: 6px 10px;"
        )
        self.frame_chip.hide()
        self.preview.add_overlay(self.frame_chip, "top-left")
        self.record_chip = StatusChip("KAYIT", "danger")
        self.record_chip.hide()
        self.preview.add_overlay(self.record_chip, "top-right")

        zoom_box = QWidget()
        zoom_box.setStyleSheet("background: transparent;")
        zoom_layout = QHBoxLayout(zoom_box)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        zoom_layout.setSpacing(5)
        for text, tooltip, slot in (
            ("＋", "Yakınlaştır", lambda: self.preview.zoom_in()),
            ("－", "Uzaklaştır", lambda: self.preview.zoom_out()),
            ("⤢", "Pencereye sığdır", lambda: self.preview.fit()),
        ):
            button = QToolButton(zoom_box)
            button.setObjectName("IconBtn")
            button.setText(text)
            button.setToolTip(tooltip)
            button.setFixedSize(30, 30)
            button.clicked.connect(slot)
            zoom_layout.addWidget(button)
        self.preview.add_overlay(zoom_box, "bottom-right")

        self.model_chip_box = QWidget()
        self.model_chip_box.setStyleSheet("background: transparent;")
        chip_layout = QHBoxLayout(self.model_chip_box)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        chip_layout.setSpacing(6)
        self.model_chips: dict[str, QFrame] = {}
        self.model_chip_stats: dict[str, QLabel] = {}
        for spec in MODEL_SPECS:
            chip = QFrame(self.model_chip_box)
            chip.setStyleSheet(
                "QFrame { background: rgba(8,12,17,0.8); border: 1px solid"
                f" {theme.BORDER}; border-radius: 13px; }}"
            )
            chip_inner = QHBoxLayout(chip)
            chip_inner.setContentsMargins(10, 5, 10, 5)
            chip_inner.setSpacing(7)
            chip_dot = QLabel("●", chip)
            chip_dot.setStyleSheet(
                f"color: {theme.MODEL_HEX[spec.id]}; background: transparent;"
                "border: none; font-size: 7px;"
            )
            chip_inner.addWidget(chip_dot)
            chip_name = QLabel(spec.short_name, chip)
            chip_name.setStyleSheet(
                f"color: {theme.TEXT}; background: transparent; border: none;"
                "font-size: 10px;"
            )
            chip_inner.addWidget(chip_name)
            chip_stat = mono_label("", chip, size=8)
            chip_stat.setStyleSheet(
                f"color: {theme.MUTED}; background: transparent; border: none;"
            )
            chip_inner.addWidget(chip_stat)
            chip_layout.addWidget(chip)
            chip.hide()
            self.model_chips[spec.id] = chip
            self.model_chip_stats[spec.id] = chip_stat
        self.preview.add_overlay(self.model_chip_box, "bottom-left")

        counters_row = QHBoxLayout()
        counters_row.setSpacing(10)
        self.counter_cards: dict[str, CounterCard] = {}
        for spec in MODEL_SPECS:
            card = CounterCard(spec.short_name, theme.MODEL_HEX[spec.id], main)
            counters_row.addWidget(card, 1)
            self.counter_cards[spec.id] = card
        self.saved_counter = CounterCard("Kaydedilen", theme.MUTED, main)
        counters_row.addWidget(self.saved_counter, 1)
        layout.addLayout(counters_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.status_chip = StatusChip("Hazır", "muted", main)
        status_row.addWidget(self.status_chip)
        self.status_label = muted_label(
            "Bir kaynak ve en az bir model seçin.", main
        )
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        status_row.addWidget(self.status_label, 1)
        self.status_detail_label = mono_label("", main, size=8)
        status_row.addWidget(self.status_detail_label)
        layout.addLayout(status_row)
        return main

    # ── App'in kullandığı yüzey ─────────────────────────────────────────────

    def source_kind(self) -> str:
        return self.source_segment.current()

    def show_source_controls(self, kind: str) -> None:
        is_camera = kind == "camera"
        self.camera_row.setVisible(is_camera)
        self.file_row.setVisible(not is_camera)

    def set_camera_values(self, values: list[str]) -> None:
        self.camera_combo.clear()
        self.camera_combo.addItems(values)
        if values:
            self.camera_combo.setCurrentIndex(0)

    def set_camera_placeholder(self, text: str) -> None:
        self.camera_combo.clear()
        self.camera_combo.addItem(text)
        self.camera_combo.setCurrentIndex(0)

    def camera_index(self) -> int:
        return self.camera_combo.currentIndex()

    def set_scan_running(self, running: bool) -> None:
        self.refresh_button.setEnabled(not running)
        self.camera_combo.setEnabled(not running)

    def set_file_text(self, text: str, *, chosen: bool) -> None:
        color = theme.TEXT if chosen else theme.MUTED
        self.file_label.setText(text)
        self.file_label.setStyleSheet(
            f"color: {color}; background: {theme.INPUT_BG};"
            f"border: 1px solid {theme.BORDER_SOFT}; border-radius: 7px;"
            "padding: 8px 10px; font-size: 11px;"
        )

    def selected_models(self) -> set[str]:
        return {
            model_id
            for model_id, card in self.model_cards.items()
            if card.is_selected()
        }

    def set_source_connected(self, text: str) -> None:
        self.source_state_label.setText(text)

    def set_status(self, text: str, variant: str = "muted", chip: str | None = None) -> None:
        chip_texts = {
            "ok": "Çalışıyor",
            "warn": "Bekliyor",
            "danger": "Hata",
            "muted": "Hazır",
        }
        self.status_chip.set_state(chip or chip_texts.get(variant, "Hazır"), variant)
        self.status_label.setText(text)

    def set_status_detail(self, text: str) -> None:
        self.status_detail_label.setText(text)

    def set_start_mode(self, mode: str) -> None:
        """mode: idle | running | stopping | disabled"""

        if mode == "running":
            self.start_button.setObjectName("Danger")
            self.start_button.setText("■  DURDUR")
            self.start_button.setEnabled(True)
        elif mode == "stopping":
            self.start_button.setObjectName("Danger")
            self.start_button.setText("DURDURULUYOR…")
            self.start_button.setEnabled(False)
        else:
            self.start_button.setObjectName("Primary")
            self.start_button.setText("▶  BAŞLAT")
            self.start_button.setEnabled(mode != "disabled")
        style = self.start_button.style()
        style.unpolish(self.start_button)
        style.polish(self.start_button)

    def set_media_footer(self, state: str, quota: str) -> None:
        self.media_state_label.setText(state)
        self.media_quota_label.setText(quota)

    def update_frame_chip(self, text: str) -> None:
        self.frame_chip.setText(text)
        self.frame_chip.show()
        self.preview.refresh_overlays()

    def set_record_flash(self, visible: bool) -> None:
        self.record_chip.setVisible(visible)
        if visible:
            self.preview.refresh_overlays()

    def update_model_chip(self, model_id: str, visible: bool, text: str = "") -> None:
        chip = self.model_chips.get(model_id)
        stat = self.model_chip_stats.get(model_id)
        if chip is None or stat is None:
            return
        stat.setText(text)
        chip.setVisible(visible)

    def refresh_chips(self) -> None:
        self.preview.refresh_overlays()

    def clear_preview(self) -> None:
        self.preview.clear()
        self.frame_chip.hide()
        self.record_chip.hide()
        for chip in self.model_chips.values():
            chip.hide()
        for card in self.model_cards.values():
            card.reset_runtime()
