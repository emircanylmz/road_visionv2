"""Design token'ları ve uygulama geneli QSS.

Renkler "RoadVision Arayüz v2 (PyQt6)" tasarımından gelir; model renkleri
``models.json`` içindeki ``color_bgr`` değerlerinden türetilir.
"""

from __future__ import annotations

from ..config import MODEL_SPECS, ModelSpec

# ── Yüzeyler ────────────────────────────────────────────────────────────────
WINDOW_BG = "#0b0f14"
DEEP_BG = "#05080b"
RAIL_BG = "#0e151d"
SIDEBAR_BG = "#0e151d"
CARD_BG = "#131b25"
PANEL_BG = "#111925"
INPUT_BG = "#0b1118"
HOVER_BG = "#18212c"
CHIP_BG = "#1a2635"
SEG_ACTIVE_TOP = "#243444"
SEG_ACTIVE_BOTTOM = "#1d2b39"
HEADER_TOP = "#141c26"
HEADER_BOTTOM = "#111925"

# ── Çizgiler ────────────────────────────────────────────────────────────────
BORDER = "#26323f"
BORDER_SOFT = "#212c3a"
BORDER_FAINT = "#1c2632"
ROW_BORDER = "#131c26"
HAIRLINE = "#17202b"
CHIP_BORDER = "#2b3a4b"

# ── Metin ───────────────────────────────────────────────────────────────────
TEXT = "#e7eef5"
TEXT_SOFT = "#c2d0dd"
MUTED = "#8a9cb0"
FAINT = "#5f7386"
DIM = "#3a4a5a"
DISABLED = "#4b5c6e"

# ── Vurgu ve durum renkleri ─────────────────────────────────────────────────
ACCENT = "#38d996"
ACCENT_HOVER = "#4ce5a8"
ACCENT_GRAD_TOP = "#4ce5a8"
ACCENT_GRAD_BOTTOM = "#2cc98b"
ACCENT_INK = "#052014"
ACCENT_DIM = "#1a7a57"
OK_BG = "#122019"
OK_BORDER = "#1e4d3b"
OK_TEXT = "#9ff0cd"

DANGER = "#e84d61"
DANGER_LIGHT = "#ff7183"
DANGER_INK = "#2a070d"
DANGER_BG = "#1a1014"

WARNING = "#ffd166"
WARNING_DIM = "#7a6427"
WARNING_BG = "#1a1710"
WARNING_BORDER = "#4a4020"

INFO = "#4d9fe0"
INFO_BG = "#101822"
INFO_BORDER = "#22344a"

LEVEL_COLORS = {
    "debug": FAINT,
    "info": INFO,
    "warning": WARNING,
    "error": DANGER_LIGHT,
}

MONO_FAMILY = '"SF Mono","JetBrains Mono","Menlo","Consolas","DejaVu Sans Mono",monospace'


def bgr_to_hex(color_bgr: tuple[int, int, int]) -> str:
    blue, green, red = color_bgr
    return f"#{red:02x}{green:02x}{blue:02x}"


def dim_hex(hex_color: str, factor: float = 0.5) -> str:
    value = hex_color.lstrip("#")
    red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"#{int(red * factor):02x}{int(green * factor):02x}{int(blue * factor):02x}"


def glow_rgba(hex_color: str, alpha: float = 0.45) -> str:
    value = hex_color.lstrip("#")
    red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({red},{green},{blue},{alpha:.2f})"


def model_color(spec: ModelSpec) -> str:
    return bgr_to_hex(spec.color_bgr)


MODEL_HEX: dict[str, str] = {spec.id: model_color(spec) for spec in MODEL_SPECS}
MODEL_DIM: dict[str, str] = {
    model_id: dim_hex(value) for model_id, value in MODEL_HEX.items()
}


def color_for_model(model_id: str | None) -> str:
    if model_id is None:
        return DIM
    return MODEL_HEX.get(model_id, MUTED)


def build_qss() -> str:
    return f"""
QWidget {{
    color: {TEXT};
    font-size: 12px;
    selection-background-color: {SEG_ACTIVE_BOTTOM};
    selection-color: {TEXT};
}}
QMainWindow, QDialog, #Root {{ background: {WINDOW_BG}; }}
#Header {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {HEADER_TOP}, stop:1 {HEADER_BOTTOM});
    border-bottom: 1px solid #202b38;
}}
#Rail {{ background: {RAIL_BG}; border-right: 1px solid {BORDER_FAINT}; }}
#Sidebar {{ background: {SIDEBAR_BG}; }}

QFrame#Card {{
    background: {CARD_BG};
    border: 1px solid {BORDER_SOFT};
    border-radius: 10px;
}}
QFrame#Card:hover {{ border-color: #2e4054; }}
QFrame#Card[selected="true"] {{ border-color: {ACCENT}; }}
QFrame#Panel {{
    background: {PANEL_BG};
    border: 1px solid {BORDER_SOFT};
    border-radius: 10px;
}}
QFrame#Segment {{
    background: {INPUT_BG};
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
}}
QToolButton#SegBtn {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 11px;
    font-weight: 600;
    color: {MUTED};
}}
QToolButton#SegBtn:hover {{ color: {TEXT}; }}
QToolButton#SegBtn:checked {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {SEG_ACTIVE_TOP}, stop:1 {SEG_ACTIVE_BOTTOM});
    color: {TEXT};
    font-weight: 700;
}}
QToolButton#SegBtn:disabled {{ color: {DISABLED}; }}

QToolButton {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 6px 12px;
    font-weight: 600;
    color: {MUTED};
}}
QToolButton:hover {{ background: {HOVER_BG}; color: {TEXT}; }}
QToolButton:checked {{ background: {SEG_ACTIVE_TOP}; color: {TEXT}; }}
QToolButton:disabled {{ color: {DISABLED}; }}
QToolButton#IconBtn {{
    background: {HOVER_BG};
    border: 1px solid {BORDER};
    padding: 4px 8px;
    color: {MUTED};
}}
QToolButton#IconBtn:hover {{ color: {TEXT}; }}

QPushButton {{
    background: {HOVER_BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 600;
}}
QPushButton:hover {{ background: #1f2a37; }}
QPushButton:disabled {{ color: {DISABLED}; background: {PANEL_BG}; }}
QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {ACCENT_GRAD_TOP}, stop:1 {ACCENT_GRAD_BOTTOM});
    color: {ACCENT_INK};
    border: none;
    padding: 12px 18px;
    font-weight: 700;
    font-size: 13px;
}}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: #244238; color: #48705f; }}
QPushButton#Danger {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {DANGER_LIGHT}, stop:1 {DANGER});
    color: {DANGER_INK};
    border: none;
    padding: 12px 18px;
    font-weight: 700;
    font-size: 13px;
}}
QPushButton#Danger:disabled {{ background: #5c2833; color: #8f5560; }}

QLineEdit {{
    background: {INPUT_BG};
    border: 1px solid {BORDER_SOFT};
    border-radius: 7px;
    padding: 7px 10px;
    color: {TEXT};
}}
QLineEdit:focus {{ border-color: {ACCENT_DIM}; }}
QLineEdit:disabled {{ color: {DISABLED}; }}

QComboBox {{
    background: {INPUT_BG};
    border: 1px solid {BORDER_SOFT};
    border-radius: 7px;
    padding: 7px 10px;
    color: {TEXT};
}}
QComboBox:disabled {{ color: {DISABLED}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {CARD_BG};
    border: 1px solid {BORDER};
    selection-background-color: {SEG_ACTIVE_TOP};
    color: {TEXT};
}}

QSlider::groove:horizontal {{
    height: 5px;
    background: {INPUT_BG};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    width: 13px;
    height: 13px;
    margin: -4px 0;
    background: {TEXT};
    border-radius: 7px;
}}
QSlider:disabled::sub-page:horizontal {{ background: {BORDER}; }}

QTableView {{
    background: {PANEL_BG};
    alternate-background-color: {PANEL_BG};
    gridline-color: transparent;
    border: none;
    selection-background-color: {SEG_ACTIVE_BOTTOM};
    selection-color: {TEXT};
}}
QHeaderView::section {{
    background: {RAIL_BG};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {BORDER_FAINT};
    padding: 9px 10px;
    font-size: 10px;
    font-weight: 700;
}}
QTableCornerButton::section {{ background: {RAIL_BG}; border: none; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {WINDOW_BG}; }}
QSplitter::handle:hover {{ background: {BORDER_FAINT}; }}

QToolTip {{
    background: {CARD_BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 5px 8px;
}}
QMessageBox {{ background: {PANEL_BG}; }}
QMenu {{
    background: {CARD_BG};
    border: 1px solid {BORDER};
    padding: 4px;
}}
QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
QMenu::item:selected {{ background: {SEG_ACTIVE_TOP}; }}
"""


def apply_theme(app) -> None:
    """QApplication'a temayı ve temel fontu uygular."""

    from PyQt6.QtGui import QFont

    font = QFont()
    font.setPointSizeF(max(9.0, font.pointSizeF()))
    app.setFont(font)
    app.setStyleSheet(build_qss())
