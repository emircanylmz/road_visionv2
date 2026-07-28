"""RoadVision PyQt6 ana penceresi ve motor kablolaması.

Tk sürümüyle aynı sözleşmeler geçerlidir:

* Motor olayları ``queue.Queue`` üzerinden gelir; 33 ms'lik tek QTimer
  kalp atışı motor olaylarını, oturum günlüğünü, snapshot ve arşiv
  sonuçlarını boşaltır. Kare olaylarından yalnız en sonuncusu çizilir.
* Widget'lara yalnız Qt ana thread'i dokunur; kamera taraması worker
  thread'inden sinyalle ana thread'e taşınır.
* Kapanış iki fazlıdır: ``closeEvent`` motoru asenkron kapatır, pencere
  ancak ``shutdown_complete`` olayı gelince yok edilir.

Torch/cv2 gerektiren modüller (engine, media, camera, sources) pencere
kurulurken tembel yüklenir; böylece sayfalar ve tema başsız testlerde
sahte motorla da çalışır.
"""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QFileDialog,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import APP_CONFIG, MODEL_SPECS, PerformanceProfile
from ..logbook import EventJournal, LogLevel, SessionLogSink, create_default_journal
from . import theme
from .archive_page import ArchivePage
from .live_page import LivePage
from .log_page import LogPage, LogTableModel
from .run_stats import RunStats
from .snapshot_dialog import SnapshotDialog
from .summary_page import SummaryPage
from .widgets import StatusChip, mono_label

_UNSET = object()

_PROFILES = {
    "Hızlı": PerformanceProfile.SPEED,
    "Dengeli": PerformanceProfile.BALANCED,
    "Kalite": PerformanceProfile.QUALITY,
}

# EngineState str-Enum olduğundan düz dizgelerle karşılaştırmak güvenlidir;
# böylece bu modül torch zinciri olmadan da import edilebilir.
_IDLE = "idle"
_STARTING = "starting"
_RUNNING = "running"
_STOPPING = "stopping"


class RailButton(QToolButton):
    def __init__(self, glyph: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(f"{glyph}\n{label}")
        self.setFixedSize(56, 52)
        self.setStyleSheet(
            "QToolButton { background: transparent; border: none;"
            f" border-left: 3px solid transparent; border-radius: 8px;"
            f" color: {theme.MUTED}; font-size: 10px; font-weight: 600; }}"
            f"QToolButton:hover {{ color: {theme.TEXT}; }}"
            f"QToolButton:checked {{ background: {theme.CHIP_BG};"
            f" border-left: 3px solid {theme.ACCENT}; color: {theme.TEXT}; }}"
        )
        self._badge = QLabel("", self)
        self._badge.setStyleSheet(
            f"background: {theme.ACCENT}; color: {theme.ACCENT_INK};"
            "border-radius: 8px; font-size: 9px; font-weight: 700;"
            "padding: 1px 5px;"
        )
        self._badge.move(34, 4)
        self._badge.hide()

    def set_badge(self, count: int) -> None:
        if count > 0:
            self._badge.setText(str(min(count, 99)))
            self._badge.adjustSize()
            self._badge.move(self.width() - self._badge.width() - 4, 4)
            self._badge.show()
        else:
            self._badge.hide()


class RoadVisionQtApp(QMainWindow):
    """PyQt6 ana penceresi; Tk ``RoadVisionApp`` ile aynı akışları uygular."""

    _camera_scan_finished = pyqtSignal(object, object)

    def __init__(
        self,
        journal: EventJournal | None = None,
        *,
        engine=None,
        recorder=_UNSET,
        snapshot_fetcher=_UNSET,
        archive_fetcher=_UNSET,
    ) -> None:
        super().__init__()
        self._journal = journal or create_default_journal()
        self._session_log_sink = SessionLogSink()
        self._journal.add_sink(self._session_log_sink)
        self._journal.prepare_journal()

        self._events: queue.Queue = queue.Queue()
        if recorder is _UNSET:
            from ..media import create_default_recorder

            recorder = create_default_recorder(self._journal)
        self._recorder = recorder
        if snapshot_fetcher is _UNSET:
            from ..media import create_default_snapshot_fetcher

            snapshot_fetcher = create_default_snapshot_fetcher()
        self._snapshot_fetcher = snapshot_fetcher
        if archive_fetcher is _UNSET:
            from ..archive_fetcher import create_default_archive_fetcher

            archive_fetcher = create_default_archive_fetcher()
        self._archive_fetcher = archive_fetcher
        if engine is None:
            from ..engine import ProcessingEngine

            engine = ProcessingEngine(
                self._events.put, journal=self._journal, recorder=self._recorder
            )
        self.engine = engine

        self._journal.app_event(
            LogLevel.INFO,
            "RoadVision başlatıldı.",
            build=APP_CONFIG.build,
            device=self.engine.device,
            ui="qt",
        )

        self._archive_poll_error_reported = False
        self._snapshot_dialog: SnapshotDialog | None = None
        self._snapshot_generation = 0
        self._snapshot_capture_id: str | None = None
        self._snapshot_capture_time: float | None = None
        self._snapshot_retry_attempted = False
        self._camera_infos: list = []
        self._camera_scan_running = False
        self._active_run_id: int | None = None
        self._closing = False
        self._closed = False
        self._source_path = ""
        self._last_spark_push = 0.0
        self._log_alert_count = 0
        self.run_stats = RunStats()
        try:
            from ..config import MediaConfig

            self._media_config = MediaConfig.from_env()
        except ValueError:
            self._media_config = None
        self._media_enabled = self._detect_media_enabled()

        self.setWindowTitle(APP_CONFIG.title)
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)
        self._build_ui()
        self._wire_signals()
        self._camera_scan_finished.connect(self._finish_camera_scan)

        self._show_source_controls()
        self._update_start_availability()
        self.refresh_cameras()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(33)
        self._poll_timer.timeout.connect(self._poll_events)
        self._poll_timer.start()

        self._record_flash_timer = QTimer(self)
        self._record_flash_timer.setSingleShot(True)
        self._record_flash_timer.timeout.connect(
            lambda: self.live.set_record_flash(False)
        )

    # ── kurulum ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("Root")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(central)
        header.setObjectName("Header")
        header.setFixedHeight(46)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 0, 14, 0)
        header_layout.setSpacing(12)
        logo = QLabel(header)
        logo.setFixedSize(18, 18)
        logo.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            f" stop:0 {theme.ACCENT}, stop:1 #1e9b6b); border-radius: 5px;"
        )
        header_layout.addWidget(logo)
        title = QLabel("ROADVISION", header)
        title.setStyleSheet(
            "background: transparent; font-size: 13px; font-weight: 700;"
            "letter-spacing: 2px;"
        )
        header_layout.addWidget(title)
        version_badge = mono_label(APP_CONFIG.build, header, size=8)
        version_badge.setStyleSheet(
            f"color: {theme.MUTED}; background: {theme.HOVER_BG};"
            f"border: 1px solid {theme.BORDER}; border-radius: 5px;"
            "padding: 3px 7px;"
        )
        header_layout.addWidget(version_badge)
        header_layout.addStretch(1)
        self.device_chip = StatusChip(self.engine.device.upper(), "ok", header)
        header_layout.addWidget(self.device_chip)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        rail = QFrame(central)
        rail.setObjectName("Rail")
        rail.setFixedWidth(72)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(8, 12, 8, 12)
        rail_layout.setSpacing(6)
        self._rail_buttons: list[RailButton] = []
        for index, (glyph, label) in enumerate(
            (("▶", "Canlı"), ("◔", "Özet"), ("≡", "Günlük"), ("▤", "Arşiv"))
        ):
            button = RailButton(glyph, label, rail)
            button.clicked.connect(lambda _checked=False, i=index: self._switch_page(i))
            rail_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            self._rail_buttons.append(button)
        rail_layout.addStretch(1)
        device_label = QLabel("◈\nAygıt", rail)
        device_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        device_label.setToolTip(f"Aygıt: {self.engine.device}")
        device_label.setStyleSheet(
            f"color: {theme.MUTED}; background: transparent; font-size: 10px;"
            "font-weight: 600;"
        )
        rail_layout.addWidget(device_label, 0, Qt.AlignmentFlag.AlignHCenter)
        body.addWidget(rail)

        self.stack = QStackedWidget(central)
        self.live = LivePage(self.stack)
        self.summary = SummaryPage(self.stack)
        self.log_page = LogPage(self.stack)
        self.archive_page = ArchivePage(self.stack, fetcher=self._archive_fetcher)
        for page in (self.live, self.summary, self.log_page, self.archive_page):
            self.stack.addWidget(page)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)
        self.setCentralWidget(central)
        self._rail_buttons[0].setChecked(True)

        # Kısayollar: Space başlat/durdur, Ctrl+F ara, Ctrl+1..4 sayfa.
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._toggle_processing)
        QShortcut(
            QKeySequence.StandardKey.Find,
            self,
            lambda: (self._switch_page(2), self.log_page.search_edit.setFocus()),
        )
        for index in range(4):
            QShortcut(
                QKeySequence(f"Ctrl+{index + 1}"),
                self,
                lambda i=index: self._switch_page(i),
            )

        self.live.set_media_footer(*self._media_footer_texts(0))

    def _wire_signals(self) -> None:
        live = self.live
        live.sourceKindChanged.connect(self._on_source_kind_change)
        live.cameraSelected.connect(
            lambda: self._reset_for_source_change(
                "Kamera seçildi. İşlemi başlatabilirsiniz."
            )
        )
        live.refreshCamerasClicked.connect(self.refresh_cameras)
        live.chooseFileClicked.connect(self._choose_file)
        live.modelToggled.connect(self._on_model_toggled)
        live.selectAllClicked.connect(self._select_all_models)
        live.confidenceCommitted.connect(self.engine.set_model_confidence)
        live.annotationToggled.connect(self._on_annotation_change)
        live.profileChanged.connect(self._on_performance_profile)
        live.startStopClicked.connect(self._toggle_processing)
        self.log_page.openCaptureRequested.connect(self._open_snapshot_capture)
        self.log_page.clearRequested.connect(self._clear_session_logs)
        self.archive_page.openCaptureRequested.connect(self._open_snapshot_capture)

    # ── sayfa geçişleri ─────────────────────────────────────────────────────

    def _switch_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self._rail_buttons):
            button.setChecked(i == index)
        if index == 1:
            self._refresh_summary()
        if index == 2:
            self._log_alert_count = 0
            self._rail_buttons[2].set_badge(0)
        # Arşiv sayfası kendi show/hide olaylarıyla activate/deactivate olur.

    def _refresh_summary(self) -> None:
        journal_hint = "roadvision.jsonl"
        saved = len(self.run_stats.captures)
        per_run = self._media_config.max_per_run if self._media_config else 200
        per_hour = self._media_config.max_per_hour if self._media_config else 500
        self.summary.set_media_quota(saved, per_run, per_hour)
        self.summary.refresh(
            self.run_stats, device=self.engine.device, journal_hint=journal_hint
        )

    # ── kaynak yönetimi (Tk portu) ─────────────────────────────────────────

    def _show_source_controls(self) -> None:
        self.live.show_source_controls(self.live.source_kind())
        self._update_start_availability()

    def _on_source_kind_change(self, _kind: str) -> None:
        self._source_path = ""
        self.live.set_file_text("Henüz dosya seçilmedi", chosen=False)
        self._show_source_controls()
        self._reset_for_source_change("Kaynak türü değişti. Yeni kaynağı seçin.")

    def _choose_file(self) -> None:
        if self.live.source_kind() == "image":
            filters = (
                "Fotoğraflar (*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff)"
                ";;Tüm dosyalar (*.*)"
            )
        else:
            filters = (
                "Videolar (*.mp4 *.avi *.mov *.mkv *.m4v *.webm)"
                ";;Tüm dosyalar (*.*)"
            )
        path, _selected = QFileDialog.getOpenFileName(
            self, "Kaynak dosyayı seçin", "", filters
        )
        if path:
            from pathlib import Path

            self._source_path = path
            name = Path(path).name
            self.live.set_file_text(name, chosen=True)
            self._reset_for_source_change(
                f"{name} seçildi. İşlemi başlatabilirsiniz."
            )
        else:
            self._update_start_availability()

    def _reset_for_source_change(self, message: str) -> None:
        self._active_run_id = None
        if self.engine.state != _IDLE:
            self.engine.request_stop()
        self._discard_pending_events()
        self.live.clear_preview()
        self.live.set_status(message, "muted")
        self.live.set_status_detail("")
        self._set_running_ui(False)

    def _discard_pending_events(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                break

    # ── kamera taraması ─────────────────────────────────────────────────────

    def refresh_cameras(self) -> None:
        if self._camera_scan_running:
            return
        if self.live.source_kind() == "camera":
            self._reset_for_source_change("Kameralar yeniden taranıyor…")
        self._camera_scan_running = True
        self._camera_infos = []
        self.live.set_camera_placeholder("Kameralar taranıyor…")
        self.live.set_scan_running(True)
        self._update_start_availability()

        def scan() -> None:
            try:
                from ..camera import Camera

                cameras = Camera.get_camera_indexes(APP_CONFIG.max_camera_index)
                self._camera_scan_finished.emit(cameras, None)
            except Exception as exc:  # pragma: no cover - donanım hatası
                self._camera_scan_finished.emit([], str(exc))

        threading.Thread(
            target=scan, name="roadvision-camera-scan", daemon=True
        ).start()

    def _finish_camera_scan(self, cameras, error) -> None:
        self._camera_scan_running = False
        self._camera_infos = list(cameras)
        self.live.set_scan_running(False)
        values = [str(camera) for camera in self._camera_infos]
        if values:
            self.live.set_camera_values(values)
            self.live.set_status(f"{len(values)} erişilebilir kamera bulundu.")
            self.live.set_source_connected("bağlı")
        else:
            self.live.set_camera_placeholder("Erişilebilir kamera bulunamadı")
            self.live.set_status(
                error or "Kamera bulunamadı; fotoğraf veya video seçebilirsiniz."
            )
            self.live.set_source_connected("")
        self._update_start_availability()

    # ── model seçimi ────────────────────────────────────────────────────────

    def _selected_models(self) -> set[str]:
        return self.live.selected_models()

    def _select_all_models(self) -> None:
        cards = self.live.model_cards.values()
        should_select = not all(card.is_selected() for card in cards)
        for card in cards:
            card.set_selected(should_select)
        # set_selected sinyalleri tetikler; motor güncellemesi orada yapılır.

    def _on_model_toggled(self, _model_id: str, _selected: bool) -> None:
        selected = self._selected_models()
        if self.engine.state in (_STARTING, _RUNNING):
            self.engine.update_models(selected)
        self._update_start_availability()

    def _on_annotation_change(self, model_id: str, enabled: bool) -> None:
        self.engine.set_annotation_enabled(model_id, enabled)
        spec = next(spec for spec in MODEL_SPECS if spec.id == model_id)
        if enabled:
            self.live.set_status(f"{spec.short_name} çizimi açıldı.")
        elif model_id in self._selected_models():
            self.live.set_status(
                f"{spec.short_name} çizimi gizlendi; tespit devam ediyor."
            )
        else:
            self.live.set_status(f"{spec.short_name} çizimi kapatıldı.")

    def _on_performance_profile(self, label: str) -> None:
        self.engine.set_performance_profile(_PROFILES[label])
        self.live.set_status(
            f"Performans profili: {label}. Sonraki kareye uygulanacak."
        )

    # ── başlat / durdur ─────────────────────────────────────────────────────

    def _source_is_ready(self) -> bool:
        if self.live.source_kind() == "camera":
            return bool(self._camera_infos and self.live.camera_index() >= 0)
        from pathlib import Path

        return bool(self._source_path and Path(self._source_path).is_file())

    def _update_start_availability(self) -> None:
        if self._closing or self._closed:
            self.live.set_start_mode("disabled")
            return
        state = self.engine.state
        if state in (_STARTING, _RUNNING):
            if self._active_run_id is not None:
                self.live.set_start_mode("running")
            else:
                self.live.set_start_mode("stopping")
            return
        if state != _IDLE:
            self.live.set_start_mode("stopping")
            return
        ready = self._source_is_ready() and bool(self._selected_models())
        self.live.set_start_mode("idle" if ready else "disabled")

    def _create_source(self):
        from ..sources import SourceFactory

        kind = self.live.source_kind()
        if kind == "camera":
            current = self.live.camera_index()
            if current < 0 or current >= len(self._camera_infos):
                raise ValueError("Erişilebilir bir kamera seçin.")
            return SourceFactory.create_camera(
                self._camera_infos[current].index,
                APP_CONFIG.camera_width,
                APP_CONFIG.camera_height,
                APP_CONFIG.camera_fps,
            )
        if kind == "image":
            return SourceFactory.create_image(self._source_path)
        return SourceFactory.create_video(self._source_path)

    def _toggle_processing(self) -> None:
        if self._closing or self._closed:
            return
        if self.engine.state in (_STARTING, _RUNNING):
            self.engine.request_stop()
            self.live.set_start_mode("stopping")
            self.live.set_status("İşlem durduruluyor…", "warn")
            return
        if self.engine.state != _IDLE:
            self._set_running_ui(False)
            return
        try:
            selected = self._selected_models()
            if not selected:
                raise ValueError("Başlatmak için en az bir model seçin.")
            source = self._create_source()
            for model_id in selected:
                card = self.live.model_cards[model_id]
                self.engine.set_model_confidence(model_id, card.confidence())
                self.engine.set_annotation_enabled(
                    model_id, card.annotation_enabled()
                )
            profile_label = self.live.profile_segment.current()
            self.engine.set_performance_profile(_PROFILES[profile_label])
            self._active_run_id = self.engine.start(source, selected)
            source_name = getattr(source, "name", None) or self.live.source_kind()
            self.run_stats.reset(
                run_id=self._active_run_id,
                source_name=str(source_name),
                device=self.engine.device,
                profile_label=profile_label,
            )
            self._set_running_ui(True)
            self.live.set_status("Kaynak hazırlanıyor…", "warn")
        except Exception as exc:
            self._active_run_id = None
            self._set_running_ui(False)
            QMessageBox.critical(self, "Başlatılamadı", str(exc))

    def _set_running_ui(self, running: bool) -> None:
        if running:
            self.live.set_start_mode("running")
        else:
            self._update_start_availability()

    # ── kalp atışı ──────────────────────────────────────────────────────────

    def _poll_events(self) -> None:
        self._poll_log_records()
        self._poll_snapshot_results()
        self._poll_archive_results()
        latest_frame = None
        try:
            while not self._closed:
                event = self._events.get_nowait()
                if event.kind in {"shutdown_complete", "archive_ready"}:
                    self._handle_event(event)
                    continue
                if not self._event_belongs_to_active_run(event):
                    continue
                if event.kind == "frame":
                    latest_frame = event
                else:
                    self._handle_event(event)
        except queue.Empty:
            pass
        if self._closed:
            return
        if latest_frame is not None:
            self._handle_event(latest_frame)
        self._update_start_availability()

    def _event_belongs_to_active_run(self, event) -> bool:
        return self._active_run_id is not None and event.run_id == self._active_run_id

    def _handle_event(self, event) -> None:
        if event.kind == "archive_ready":
            if not self._closing and not self._closed:
                self._mark_archive_dirty(delay_ms=0)
            return
        if event.kind == "shutdown_complete":
            if self._closing and not self._closed:
                self._finalize_close()
            return
        if not self._event_belongs_to_active_run(event):
            return
        if self.engine.state == _STOPPING and event.kind not in {"error", "stopped"}:
            return

        if event.kind == "frame" and event.frame is not None:
            self._handle_frame(event)
        elif event.kind == "started":
            self.live.set_status(
                f"{event.source_name} işleniyor. Model seçimini istediğiniz an"
                " değiştirebilirsiniz.",
                "ok",
            )
            self._set_running_ui(True)
        elif event.kind == "status":
            self.live.set_status(event.message, "ok")
        elif event.kind == "source_ended":
            self.live.set_status(
                event.message
                + " Son kare üzerinde model seçimini değiştirebilirsiniz.",
                "warn",
            )
            self.run_stats.finish()
            self._mark_archive_dirty()
        elif event.kind == "error":
            self._active_run_id = None
            self.live.set_status(event.message, "danger")
            self.run_stats.finish()
            self._set_running_ui(False)
            self._mark_archive_dirty()
            QMessageBox.critical(self, "İşlem hatası", event.message)
        elif event.kind == "stopped":
            self._active_run_id = None
            self.live.set_status(event.message, "muted")
            self.run_stats.finish()
            self._set_running_ui(False)
            self._mark_archive_dirty()

    def _handle_frame(self, event) -> None:
        stats = self.run_stats
        stats.note_frame(
            event.stats,
            inference_fps=event.inference_fps,
            total_ms=event.total_ms,
        )
        live = self.live
        live.preview.set_frame(event.frame)
        live.update_frame_chip(
            f"{datetime.now().strftime('%H:%M:%S')} · kare {stats.frames}"
        )

        live.stat_fps.set_value(f"{event.inference_fps:.1f}")
        live.stat_ms.set_value(f"{event.total_ms:.0f}")
        live.stat_frames.set_value(f"{stats.frames}")
        live.stat_objects.set_value(str(stats.total_objects))
        live.stat_media.set_value(str(len(stats.captures)))
        now = time.monotonic()
        if now - self._last_spark_push >= 0.5:
            self._last_spark_push = now
            live.stat_fps.spark.push(min(1.0, event.inference_fps / 30.0))
            live.stat_ms.spark.push(min(1.0, event.total_ms / 200.0))
            live.stat_frames.spark.push(1.0)
            live.stat_objects.spark.push(
                min(1.0, sum(stat.object_count for stat in event.stats) / 10.0)
            )
            live.stat_media.spark.push(min(1.0, len(stats.captures) / 50.0))

        selected = self._selected_models()
        for spec in MODEL_SPECS:
            aggregate = stats.models.get(spec.id)
            card = live.model_cards[spec.id]
            counter = live.counter_cards[spec.id]
            if aggregate is None or spec.id not in selected:
                live.update_model_chip(spec.id, False)
                continue
            live_text = (
                f"{aggregate.last_count} nesne"
                if spec.task != "semantic"
                else f"{aggregate.last_count} bölge"
            )
            card.set_runtime(f"{aggregate.last_elapsed_ms:.0f} ms", live_text)
            live.update_model_chip(spec.id, True, str(aggregate.last_count))
            counter.set_values(
                str(aggregate.last_count), f"toplam {aggregate.object_count}"
            )
        live.saved_counter.set_values(
            str(len(stats.captures)),
            f"kota {self._media_config.max_per_run if self._media_config else 200}",
        )
        live.refresh_chips()
        live.set_status_detail(
            f"{event.inference_fps:.1f} fps · {event.total_ms:.0f} ms"
        )
        live.set_media_footer(*self._media_footer_texts(len(stats.captures)))

    # ── günlük akışı ────────────────────────────────────────────────────────

    def _poll_log_records(self) -> None:
        sink = getattr(self, "_session_log_sink", None)
        page = getattr(self, "log_page", None)
        if sink is None or page is None:
            return
        records = sink.drain()
        if not records:
            return
        page.append_records(records)
        for record in records:
            capture_id = LogTableModel.capture_id(record)
            if capture_id:
                self.run_stats.note_capture(capture_id)
                self.live.set_record_flash(True)
                self._record_flash_timer.start(1500)
            if record.level.value in ("warning", "error") and (
                self.stack.currentIndex() != 2
            ):
                self._log_alert_count += 1
        self._rail_buttons[2].set_badge(self._log_alert_count)

    def _clear_session_logs(self) -> None:
        self._session_log_sink.drain()
        self.log_page.clear()
        self._log_alert_count = 0
        self._rail_buttons[2].set_badge(0)

    # ── snapshot görüntüleyici (Tk portu) ──────────────────────────────────

    def _open_snapshot_capture(self, capture_id: str, capture_time=None) -> None:
        if self._snapshot_fetcher is None:
            self.live.set_status(
                "Tespit görüntüleyici kapalı: ROADVISION_DB_DSN tanımlı değil."
            )
            return
        normalized = str(capture_id).strip()
        if not normalized:
            self.live.set_status("Seçili tespit satırında görüntü kimliği yok.")
            return
        dialog = self._snapshot_dialog
        if dialog is None:
            dialog = SnapshotDialog(self)
            dialog.refreshRequested.connect(self._manual_refresh_snapshot)
            dialog.closed.connect(self._snapshot_dialog_closed)
            self._snapshot_dialog = dialog
        self._snapshot_capture_id = normalized
        if isinstance(capture_time, datetime):
            self._snapshot_capture_time = capture_time.timestamp()
        elif capture_time is None:
            self._snapshot_capture_time = None
        else:
            self._snapshot_capture_time = float(capture_time)
        self._snapshot_retry_attempted = False
        dialog.show_loading(normalized)
        self._request_snapshot(normalized)

    def _request_snapshot(self, capture_id: str) -> None:
        fetcher = self._snapshot_fetcher
        dialog = self._snapshot_dialog
        if fetcher is None or dialog is None:
            return
        try:
            self._snapshot_generation = fetcher.request(capture_id)
        except Exception as exc:
            dialog.show_error(str(exc))

    def _manual_refresh_snapshot(self, capture_id: str) -> None:
        if capture_id != self._snapshot_capture_id:
            return
        dialog = self._snapshot_dialog
        if dialog is not None and dialog.exists():
            dialog.show_loading(capture_id, "Görüntü yeniden yükleniyor…")
        self._request_snapshot(capture_id)

    def _poll_snapshot_results(self) -> None:
        fetcher = self._snapshot_fetcher
        if fetcher is None:
            return
        for result in fetcher.drain():
            if (
                result.generation != self._snapshot_generation
                or result.capture_id != self._snapshot_capture_id
            ):
                continue
            dialog = self._snapshot_dialog
            if dialog is None or not dialog.exists():
                continue
            if result.status == "ok" and result.bundle is not None:
                dialog.show_bundle(result.bundle)
            elif result.status == "not_found":
                dialog.show_not_found(
                    "Görüntü henüz yazılmamış veya saklama süresi dolduğu için"
                    " silinmiş olabilir."
                )
                capture_time = self._snapshot_capture_time
                is_recent = (
                    capture_time is not None
                    and 0.0 <= time.time() - capture_time <= 15.0
                )
                if is_recent and not self._snapshot_retry_attempted:
                    self._snapshot_retry_attempted = True
                    dialog.set_status(
                        "Kayıt arka planda sürüyor olabilir; 1,5 saniye sonra"
                        " bir kez yeniden denenecek."
                    )
                    QTimer.singleShot(
                        1500,
                        lambda capture_id=result.capture_id,
                        generation=result.generation: self._auto_retry_snapshot(
                            capture_id, generation
                        ),
                    )
            else:
                dialog.show_error(result.message or "Bilinmeyen veritabanı hatası")

    def _auto_retry_snapshot(self, capture_id: str, generation: int) -> None:
        if (
            capture_id != self._snapshot_capture_id
            or generation != self._snapshot_generation
        ):
            return
        dialog = self._snapshot_dialog
        if dialog is None or not dialog.exists():
            return
        dialog.show_loading(capture_id, "Arka plan kaydı yeniden kontrol ediliyor…")
        self._request_snapshot(capture_id)

    def _snapshot_dialog_closed(self, dialog) -> None:
        if dialog is self._snapshot_dialog:
            self._snapshot_dialog = None

    # ── arşiv akışı ─────────────────────────────────────────────────────────

    def _poll_archive_results(self) -> None:
        page = getattr(self, "archive_page", None)
        if page is None:
            return
        try:
            page.poll_results(max_items=8)
            self._archive_poll_error_reported = False
        except Exception as exc:
            # Render/adaptör hatası 33 ms kalp atışını durdurmamalı.
            if self._archive_poll_error_reported:
                return
            self._archive_poll_error_reported = True
            self._journal.app_event(
                LogLevel.ERROR,
                "Tespit arşivi sonucu arayüze uygulanamadı.",
                archive_error=str(exc),
            )

    def _mark_archive_dirty(self, *, delay_ms: int = 750) -> None:
        page = getattr(self, "archive_page", None)
        if page is None or self._closing or self._closed:
            return
        page.mark_dirty(delay_ms=max(0, int(delay_ms)))

    # ── yardımcılar ─────────────────────────────────────────────────────────

    def _detect_media_enabled(self) -> bool:
        recorder = self._recorder
        if recorder is None:
            return False
        return type(recorder).__name__ != "NullRecorder"

    def _media_footer_texts(self, saved: int) -> tuple[str, str]:
        if not self._media_enabled:
            return ("medya kaydı kapalı", "")
        per_run = self._media_config.max_per_run if self._media_config else 200
        return ("medya kaydı açık", f"kota {saved} / {per_run}")

    # ── kapanış ─────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._closed:
            event.accept()
            return
        event.ignore()
        if self._closing:
            return
        self._closing = True
        self.archive_page.begin_close()
        if self._archive_fetcher is not None:
            self._archive_fetcher.close(timeout=0.0)
        if self._snapshot_fetcher is not None:
            self._snapshot_fetcher.close(timeout=0.0)
        dialog = self._snapshot_dialog
        if dialog is not None and dialog.exists():
            dialog.close()
        self._active_run_id = None
        self._discard_pending_events()
        self.live.set_start_mode("disabled")
        self.live.set_status("Uygulama kapatılıyor…", "warn")
        self.engine.request_shutdown()

    def _finalize_close(self) -> None:
        self._closed = True
        self._poll_timer.stop()
        if self._archive_fetcher is not None:
            self._archive_fetcher.close(timeout=0.25)
        if self._snapshot_fetcher is not None:
            self._snapshot_fetcher.close(timeout=0.25)
        self._journal.release_journal()
        self.close()


def run_app() -> None:
    import sys

    app = QApplication(sys.argv)
    app.setApplicationName("RoadVision")
    theme.apply_theme(app)
    window = RoadVisionQtApp()
    window.show()
    app.exec()
