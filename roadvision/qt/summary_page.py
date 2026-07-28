"""Çalışma Özeti sayfası: KPI'lar, yoğunluk grafiği, döküm, kalıcılık."""

from __future__ import annotations

import csv
import json
from datetime import datetime

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import MODEL_SPECS
from . import theme
from .run_stats import RunStats, TypeAggregate
from .widgets import (
    Panel,
    QuotaBar,
    KeyValueList,
    mono_font,
    mono_label,
    muted_label,
    section_label,
)


class DensityChart(QWidget):
    """30 sn'lik kovalarla model bazlı yığılmış çubuk grafik."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series: list[tuple[str, dict[str, int]]] = []
        self.setMinimumHeight(160)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

    def set_series(self, series: list[tuple[str, dict[str, int]]]) -> None:
        self._series = series
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        label_height = 18
        chart_height = self.height() - label_height
        if not self._series:
            painter.setPen(QColor(theme.DIM))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Henüz veri yok — bir çalışma başlatın.",
            )
            return
        peak = max(
            (sum(bucket.values()) for _label, bucket in self._series), default=0
        )
        if peak == 0:
            peak = 1
        gap = 7
        count = len(self._series)
        bar_width = max(6.0, (self.width() - gap * (count - 1)) / count)
        x = 0.0
        painter.setPen(Qt.PenStyle.NoPen)
        for label, bucket in self._series:
            y = float(chart_height)
            for spec in MODEL_SPECS:
                value = bucket.get(spec.id, 0)
                if value <= 0:
                    continue
                segment = chart_height * value / peak
                y -= segment
                painter.setBrush(QColor(theme.MODEL_HEX[spec.id]))
                painter.drawRect(QRectF(x, y, bar_width, segment))
            painter.setPen(QColor(theme.FAINT))
            painter.setFont(mono_font(7))
            painter.drawText(
                QRectF(x - gap, chart_height + 2, bar_width + gap * 2, label_height),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            x += bar_width + gap


class BreakdownRow(QWidget):
    def __init__(
        self, aggregate: TypeAggregate, peak: int, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        color = theme.color_for_model(aggregate.model_id)
        model_names = {spec.id: spec.display_name for spec in MODEL_SPECS}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 8, 15, 8)
        layout.setSpacing(12)
        strip = QFrame(self)
        strip.setFixedSize(3, 22)
        strip.setStyleSheet(f"background: {color}; border-radius: 2px;")
        layout.addWidget(strip)
        model_label = muted_label(
            model_names.get(aggregate.model_id, aggregate.model_id), self
        )
        model_label.setFixedWidth(170)
        layout.addWidget(model_label)
        type_label = QLabel(aggregate.class_name, self)
        type_label.setStyleSheet(
            "background: transparent; font-size: 12px; font-weight: 700;"
        )
        type_label.setFixedWidth(170)
        layout.addWidget(type_label)
        count_label = mono_label(str(aggregate.count), self, size=10, color=theme.TEXT)
        count_label.setFixedWidth(56)
        count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(count_label)
        bar = _MiniBar(color, aggregate.count / peak if peak else 0.0, self)
        layout.addWidget(bar, 1)
        mean = aggregate.mean_confidence
        conf_label = mono_label(
            "—" if mean is None else f"{mean:.2f}", self, size=9
        )
        conf_label.setFixedWidth(52)
        conf_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(conf_label)


class _MiniBar(QWidget):
    def __init__(self, color: str, ratio: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self._ratio = max(0.0, min(1.0, ratio))
        self.setFixedHeight(7)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.INPUT_BG))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), 7), 4, 4)
        if self._ratio > 0:
            painter.setBrush(QColor(self._color))
            painter.drawRoundedRect(
                QRectF(0, 0, max(7.0, self.width() * self._ratio), 7), 4, 4
            )


class WarningCard(QFrame):
    def __init__(self, level: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        styles = {
            "warn": (theme.WARNING_BG, theme.WARNING_BORDER, theme.WARNING, "▲ UYARI"),
            "info": (theme.INFO_BG, theme.INFO_BORDER, theme.INFO, "● BİLGİ"),
        }
        bg, border, color, title = styles.get(level, styles["info"])
        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 1px solid {border};"
            "border-radius: 9px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 10, 11, 10)
        layout.setSpacing(5)
        header = mono_label(title, self, size=8, color=color, bold=True)
        header.setStyleSheet(
            f"color: {color}; background: transparent; border: none;"
        )
        layout.addWidget(header)
        body = QLabel(text, self)
        body.setWordWrap(True)
        body.setStyleSheet(
            f"color: {theme.TEXT_SOFT}; background: transparent; border: none;"
            "font-size: 11px;"
        )
        layout.addWidget(body)


class SummaryPage(QWidget):
    """RunStats anlık görüntüsünden beslenen salt-okunur özet."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stats: RunStats | None = None
        self._media_quota: tuple[int, int, int] = (0, 200, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("ÇALIŞMA ÖZETİ", self)
        title.setStyleSheet(
            "background: transparent; font-size: 13px; font-weight: 700;"
            "letter-spacing: 2px;"
        )
        header.addWidget(title)
        self.run_badge = mono_label("run —", self, size=8)
        self.run_badge.setStyleSheet(
            f"color: {theme.MUTED}; background: {theme.HOVER_BG};"
            f"border: 1px solid {theme.BORDER}; border-radius: 5px;"
            "padding: 3px 7px;"
        )
        header.addWidget(self.run_badge)
        header.addStretch(1)
        csv_button = QPushButton("CSV", self)
        csv_button.clicked.connect(lambda: self._export("csv"))
        header.addWidget(csv_button)
        jsonl_button = QPushButton("JSONL", self)
        jsonl_button.clicked.connect(lambda: self._export("jsonl"))
        header.addWidget(jsonl_button)
        root.addLayout(header)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        body = QWidget(scroll)
        body.setStyleSheet("background: transparent;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(14)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self._kpis: list[tuple[QLabel, QLabel, QLabel]] = []
        for label, unit in (
            ("SÜRE", "dk"),
            ("İŞLENEN KARE", "kare"),
            ("ORTALAMA FPS", "kare/sn"),
            ("TESPİT", "nesne"),
            ("KAYDEDİLEN", "görüntü"),
        ):
            panel = Panel(body)
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(16, 14, 16, 12)
            panel_layout.setSpacing(5)
            panel_layout.addWidget(section_label(label, panel))
            value_row = QHBoxLayout()
            value_label = QLabel("—", panel)
            value_label.setFont(mono_font(21, bold=True))
            value_label.setStyleSheet(
                f"color: {theme.TEXT}; background: transparent;"
            )
            value_row.addWidget(value_label)
            unit_label = mono_label(unit, panel, size=9)
            value_row.addWidget(unit_label, 0, Qt.AlignmentFlag.AlignBaseline)
            value_row.addStretch(1)
            panel_layout.addLayout(value_row)
            sub_label = muted_label("", panel, size=10)
            panel_layout.addWidget(sub_label)
            kpi_row.addWidget(panel, 1)
            self._kpis.append((value_label, unit_label, sub_label))
        body_layout.addLayout(kpi_row)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)

        chart_panel = Panel(body)
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(15, 15, 15, 12)
        chart_layout.setSpacing(13)
        chart_header = QHBoxLayout()
        chart_title = QLabel("Tespit yoğunluğu", chart_panel)
        chart_title.setStyleSheet(
            "background: transparent; font-size: 12px; font-weight: 700;"
        )
        chart_header.addWidget(chart_title)
        chart_header.addWidget(muted_label("30 sn kova", chart_panel, size=10))
        chart_header.addStretch(1)
        for spec in MODEL_SPECS:
            legend = QHBoxLayout()
            legend_dot = QLabel("■", chart_panel)
            legend_dot.setStyleSheet(
                f"color: {theme.MODEL_HEX[spec.id]}; background: transparent;"
                "font-size: 8px;"
            )
            legend.addWidget(legend_dot)
            legend.addWidget(muted_label(spec.short_name, chart_panel, size=10))
            legend.setSpacing(5)
            chart_header.addLayout(legend)
            chart_header.addSpacing(8)
        chart_layout.addLayout(chart_header)
        self.chart = DensityChart(chart_panel)
        chart_layout.addWidget(self.chart)
        left.addWidget(chart_panel)

        breakdown_panel = Panel(body)
        breakdown_layout = QVBoxLayout(breakdown_panel)
        breakdown_layout.setContentsMargins(0, 0, 0, 6)
        breakdown_layout.setSpacing(0)
        breakdown_header = QWidget(breakdown_panel)
        breakdown_header.setStyleSheet(
            f"border-bottom: 1px solid {theme.BORDER_FAINT}; background: transparent;"
        )
        breakdown_header_layout = QHBoxLayout(breakdown_header)
        breakdown_header_layout.setContentsMargins(15, 13, 15, 13)
        breakdown_title = QLabel("Model başına döküm", breakdown_header)
        breakdown_title.setStyleSheet(
            "background: transparent; border: none; font-size: 12px;"
            "font-weight: 700;"
        )
        breakdown_header_layout.addWidget(breakdown_title)
        breakdown_header_layout.addStretch(1)
        breakdown_layout.addWidget(breakdown_header)
        self.breakdown_box = QVBoxLayout()
        self.breakdown_box.setContentsMargins(0, 0, 0, 0)
        self.breakdown_box.setSpacing(0)
        breakdown_layout.addLayout(self.breakdown_box)
        self._breakdown_placeholder = muted_label(
            "Henüz tespit yok.", breakdown_panel
        )
        self._breakdown_placeholder.setContentsMargins(15, 10, 15, 10)
        self.breakdown_box.addWidget(self._breakdown_placeholder)
        left.addWidget(breakdown_panel)
        left.addStretch(1)
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(14)
        info_panel = Panel(body)
        info_layout = QVBoxLayout(info_panel)
        info_layout.setContentsMargins(15, 15, 15, 12)
        info_layout.setSpacing(9)
        info_title = QLabel("Çalışma bilgisi", info_panel)
        info_title.setStyleSheet(
            "background: transparent; font-size: 12px; font-weight: 700;"
        )
        info_layout.addWidget(info_title)
        self.run_info = KeyValueList(info_panel)
        info_layout.addWidget(self.run_info)
        right.addWidget(info_panel)

        quota_panel = Panel(body)
        quota_layout = QVBoxLayout(quota_panel)
        quota_layout.setContentsMargins(15, 15, 15, 14)
        quota_layout.setSpacing(12)
        quota_title = QLabel("Kalıcılık", quota_panel)
        quota_title.setStyleSheet(
            "background: transparent; font-size: 12px; font-weight: 700;"
        )
        quota_layout.addWidget(quota_title)
        self.quota_run = QuotaBar("Medya kotası (çalışma)", quota_panel)
        quota_layout.addWidget(self.quota_run)
        self.quota_hour = QuotaBar("Medya kotası (saat)", quota_panel)
        quota_layout.addWidget(self.quota_hour)
        right.addWidget(quota_panel)

        warn_panel = Panel(body)
        warn_layout = QVBoxLayout(warn_panel)
        warn_layout.setContentsMargins(15, 15, 15, 14)
        warn_layout.setSpacing(11)
        warn_title = QLabel("Dikkat edilmesi gerekenler", warn_panel)
        warn_title.setStyleSheet(
            "background: transparent; font-size: 12px; font-weight: 700;"
        )
        warn_layout.addWidget(warn_title)
        self.warning_box = QVBoxLayout()
        self.warning_box.setSpacing(11)
        warn_layout.addLayout(self.warning_box)
        right.addWidget(warn_panel)
        right.addStretch(1)

        right_box = QWidget(body)
        right_box.setLayout(right)
        right_box.setFixedWidth(320)
        right_box.setStyleSheet("background: transparent;")
        columns.addWidget(right_box)
        body_layout.addLayout(columns)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    # ── veri yenileme ───────────────────────────────────────────────────────

    def set_media_quota(self, saved: int, per_run: int, per_hour: int) -> None:
        self._media_quota = (saved, per_run, per_hour)

    def refresh(self, stats: RunStats, *, device: str, journal_hint: str) -> None:
        self._stats = stats
        info = stats.info
        self.run_badge.setText(
            f"run {info.run_id}" if info.run_id is not None else "run —"
        )

        duration = stats.duration_seconds
        minutes, seconds = divmod(int(duration), 60)
        started = (
            datetime.fromtimestamp(info.started_at).strftime("%H:%M:%S")
            if info.started_at
            else "—"
        )
        ended = (
            datetime.fromtimestamp(info.ended_at).strftime("%H:%M:%S")
            if info.ended_at
            else "sürüyor"
        )
        fps_span = (
            f"en düşük {stats.fps_min:.1f} · en yüksek {stats.fps_max:.1f}"
            if stats.fps_min is not None and stats.fps_max is not None
            else ""
        )
        saved, per_run, per_hour = self._media_quota
        values = (
            (f"{minutes:02d}:{seconds:02d}", "dk", f"{started} → {ended}"),
            (f"{stats.frames:,}".replace(",", " "), "kare", ""),
            (f"{stats.mean_fps:.1f}", "kare/sn", fps_span),
            (
                str(stats.total_objects),
                "nesne",
                f"{len(stats.types)} tür · {len(stats.models)} model",
            ),
            (str(saved), "görüntü", f"kota {per_run}"),
        )
        for (value_label, unit_label, sub_label), (value, unit, sub) in zip(
            self._kpis, values
        ):
            value_label.setText(value)
            unit_label.setText(unit)
            sub_label.setText(sub)

        self.chart.set_series(stats.bucket_series())

        while self.breakdown_box.count():
            item = self.breakdown_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        rows = stats.breakdown_rows()
        if not rows:
            placeholder = muted_label("Henüz tespit yok.", self)
            placeholder.setContentsMargins(15, 10, 15, 10)
            self.breakdown_box.addWidget(placeholder)
        else:
            peak = max(row.count for row in rows)
            for row in rows[:12]:
                self.breakdown_box.addWidget(BreakdownRow(row, peak, self))

        profile = info.profile_label
        self.run_info.set_rows(
            (
                ("Run kimliği", str(info.run_id) if info.run_id is not None else "—"),
                ("Kaynak", info.source_name),
                ("Aygıt", device),
                ("Profil", profile),
                (
                    "Başlangıç",
                    datetime.fromtimestamp(info.started_at).strftime(
                        "%d.%m.%Y %H:%M:%S"
                    )
                    if info.started_at
                    else "—",
                ),
                ("Journal", journal_hint),
            )
        )

        self.quota_run.set_ratio(
            saved / per_run if per_run else 0.0, f"{saved} / {per_run}"
        )
        self.quota_hour.set_ratio(
            saved / per_hour if per_hour else 0.0, f"{saved} / {per_hour}"
        )

        while self.warning_box.count():
            item = self.warning_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        warnings: list[tuple[str, str]] = []
        if per_run and saved / per_run >= 0.7:
            warnings.append(
                (
                    "warn",
                    f"Çalışma medya kotasının %{saved * 100 // per_run}'i dolu;"
                    " kota dolunca yeni görüntü yazılmaz.",
                )
            )
        if "cpu(det)" in device.lower():
            warnings.append(
                (
                    "info",
                    "Yol çizgisi modeli MPS üzerinde; detection modelleri CPU"
                    " uyumluluk modunda çalışıyor.",
                )
            )
        if not warnings:
            warnings.append(("info", "Bu çalışma için uyarı yok."))
        for level, text in warnings:
            self.warning_box.addWidget(WarningCard(level, text, self))

    # ── dışa aktarma ────────────────────────────────────────────────────────

    def _export(self, fmt: str) -> None:
        stats = self._stats
        if stats is None or not stats.breakdown_rows():
            return
        run_id = stats.info.run_id if stats.info.run_id is not None else "x"
        suffix = "csv" if fmt == "csv" else "jsonl"
        target, _selected = QFileDialog.getSaveFileName(
            self,
            "Çalışma özetini dışa aktar",
            f"roadvision-run-{run_id}.{suffix}",
            f"{suffix.upper()} (*.{suffix})",
        )
        if not target:
            return
        rows = stats.breakdown_rows()
        model_names = {spec.id: spec.display_name for spec in MODEL_SPECS}
        try:
            if fmt == "csv":
                with open(target, "w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(
                        ("model_id", "model", "tur", "adet", "ortalama_guven")
                    )
                    for row in rows:
                        mean = row.mean_confidence
                        writer.writerow(
                            (
                                row.model_id,
                                model_names.get(row.model_id, row.model_id),
                                row.class_name,
                                row.count,
                                "" if mean is None else f"{mean:.4f}",
                            )
                        )
            else:
                with open(target, "w", encoding="utf-8") as stream:
                    for row in rows:
                        stream.write(
                            json.dumps(
                                {
                                    "model_id": row.model_id,
                                    "model": model_names.get(
                                        row.model_id, row.model_id
                                    ),
                                    "type": row.class_name,
                                    "count": row.count,
                                    "mean_confidence": row.mean_confidence,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
        except OSError:
            # Dosya yazılamadıysa sessizce geç; durum çubuğu ana pencerede.
            return
