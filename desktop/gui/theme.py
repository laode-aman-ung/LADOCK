"""
LADOCK Dark Professional Theme
Inspired by GitHub Dark + VS Code Dark+ for a clean, scientific look.
"""

# ---------------------------------------------------------------------------
# Color Palette
# ---------------------------------------------------------------------------

# Backgrounds (darkest → lightest)
BG_DEEP     = "#0d1117"   # Window / page background
BG_BASE     = "#161b22"   # Main content area
BG_SURFACE  = "#1c2128"   # Panels, cards, sidebar
BG_MUTED    = "#22272e"   # Inputs, dropdowns, hover rows
BG_HOVER    = "#2d333b"   # Button hover, selected row

# Canvas background — shared by 2D diagram and 3D viewer for visual consistency
BG_CANVAS   = "#0f1117"   # Near-black, molecular visualization canvas

# Borders
BORDER_DIM  = "#2d333b"   # Subtle / structural borders
BORDER      = "#373e47"   # Standard borders, dividers

# Text
TEXT        = "#e6edf3"   # Primary text
TEXT_DIM    = "#8b949e"   # Secondary / caption text
TEXT_MUTED  = "#545d68"   # Disabled / placeholder

# Accent (sky blue — neutral & scientific)
ACCENT      = "#58a6ff"   # Primary accent (links, active)
ACCENT_DARK = "#1f6feb"   # Pressed / active accent
ACCENT_LITE = "#79c0ff"   # Lighter accent (hover highlights)
ACCENT_BG   = "#112240"   # Accent-tinted background

# Semantic colors
SUCCESS     = "#3fb950"
SUCCESS_BG  = "#12261e"
WARNING     = "#d29922"
WARNING_BG  = "#2e1f04"
ERROR       = "#f85149"
ERROR_BG    = "#2a0b08"
INFO        = "#58a6ff"
INFO_BG     = "#071d3a"

# Component role colors (molecular table badges)
ROLE_PROTEIN  = "#4a90d9"
ROLE_LIGAND   = "#e07b39"
ROLE_METAL    = "#c9a227"
ROLE_WATER    = "#3aabcc"
ROLE_OTHER    = "#9b7ed6"

# ---------------------------------------------------------------------------
# Global Qt Stylesheet
# ---------------------------------------------------------------------------

GLOBAL_QSS = f"""
    QMainWindow, QDialog, QWidget {{
        background-color: {BG_BASE};
        color: {TEXT};
        font-family: "Segoe UI", "Inter", "SF Pro Display", Sans-Serif;
        font-size: 12px;
    }}

    /* ----- GroupBox ----- */
    QGroupBox {{
        border: 1px solid {BORDER_DIM};
        border-radius: 6px;
        margin-top: 8px;
        padding-top: 8px;
        color: {TEXT_DIM};
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 6px;
        color: {ACCENT};
        font-weight: 600;
    }}

    /* ----- Input widgets ----- */
    QLineEdit, QComboBox, QTextEdit, QPlainTextEdit {{
        background: {BG_MUTED};
        border: 1px solid {BORDER};
        border-radius: 5px;
        color: {TEXT};
        padding: 4px 8px;
        selection-background-color: {ACCENT_DARK};
    }}
    QSpinBox, QDoubleSpinBox {{
        background: {BG_MUTED};
        border: 1px solid {BORDER};
        border-radius: 5px;
        color: {TEXT};
        padding: 0 24px 0 6px;
        selection-background-color: {ACCENT_DARK};
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 18px;
        height: 15px;
        border-left: 1px solid {BORDER_DIM};
        border-bottom: 1px solid {BORDER_DIM};
        background: {BG_MUTED};
        border-top-right-radius: 5px;
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 18px;
        height: 15px;
        border-left: 1px solid {BORDER_DIM};
        background: {BG_MUTED};
        border-bottom-right-radius: 5px;
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
        background: {BG_HOVER};
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        width: 8px;
        height: 8px;
    }}
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid {ACCENT};
        background: {BG_HOVER};
    }}
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {ACCENT};
        background: {BG_HOVER};
    }}
    QLineEdit:disabled, QComboBox:disabled {{
        color: {TEXT_MUTED};
        border-color: {BORDER_DIM};
    }}
    QSpinBox:disabled, QDoubleSpinBox:disabled {{
        color: {TEXT_MUTED};
        border-color: {BORDER_DIM};
    }}

    /* ----- ComboBox dropdown ----- */
    QComboBox::drop-down {{
        border: none;
        width: 20px;
    }}
    QComboBox QAbstractItemView {{
        background: {BG_MUTED};
        border: 1px solid {BORDER};
        color: {TEXT};
        selection-background-color: {ACCENT_DARK};
        outline: none;
    }}

    /* ----- Buttons ----- */
    QPushButton {{
        background: {BG_HOVER};
        border: 1px solid {BORDER};
        border-radius: 5px;
        color: {TEXT};
        padding: 5px 14px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background: {BORDER};
        border-color: {ACCENT};
        color: {ACCENT_LITE};
    }}
    QPushButton:pressed {{
        background: {ACCENT_DARK};
        border-color: {ACCENT};
        color: #ffffff;
    }}
    QPushButton:disabled {{
        color: {TEXT_MUTED};
        border-color: {BORDER_DIM};
        background: {BG_MUTED};
    }}

    /* ----- Table ----- */
    QTableWidget {{
        background: {BG_DEEP};
        alternate-background-color: {BG_BASE};
        gridline-color: {BORDER_DIM};
        color: {TEXT};
        border: none;
        selection-background-color: {ACCENT_DARK};
    }}
    QTableWidget::item:selected {{
        background: {ACCENT_DARK};
        color: #ffffff;
    }}
    QHeaderView::section {{
        background: {BG_SURFACE};
        color: {ACCENT};
        border: none;
        border-bottom: 2px solid {ACCENT_DARK};
        padding: 5px 8px;
        font-weight: 700;
        font-size: 11px;
        letter-spacing: 0.5px;
    }}

    /* ----- Scrollbar ----- */
    QScrollBar:vertical {{
        background: {BG_BASE};
        width: 8px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER};
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {ACCENT};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{
        background: {BG_BASE};
        height: 8px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: {BORDER};
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {ACCENT};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* ----- CheckBox & RadioButton ----- */
    QCheckBox, QRadioButton {{ color: {TEXT}; spacing: 6px; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {BORDER};
        border-radius: 3px;
        background: {BG_MUTED};
    }}
    QCheckBox::indicator:checked {{
        background: {ACCENT};
        border-color: {ACCENT};
    }}
    QRadioButton::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {BORDER};
        border-radius: 7px;
        background: {BG_MUTED};
    }}
    QRadioButton::indicator:checked {{
        background: {ACCENT};
        border-color: {ACCENT};
    }}

    /* ----- Labels ----- */
    QLabel {{ color: {TEXT}; }}

    /* ----- Splitter ----- */
    QSplitter::handle {{
        background: {BORDER_DIM};
    }}
    QSplitter::handle:hover {{
        background: {ACCENT};
    }}

    /* ----- Status bar ----- */
    QStatusBar {{
        background: {BG_DEEP};
        color: {TEXT_MUTED};
        border-top: 1px solid {BORDER_DIM};
        font-size: 11px;
    }}

    /* ----- Progress bar ----- */
    QProgressBar {{
        background: {BG_MUTED};
        border: 1px solid {BORDER_DIM};
        border-radius: 5px;
        text-align: center;
        color: {TEXT};
        height: 16px;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {ACCENT_DARK}, stop:1 {ACCENT});
        border-radius: 4px;
    }}

    /* ----- Tab widget ----- */
    QTabWidget::pane {{
        border: 1px solid {BORDER_DIM};
        border-radius: 0 6px 6px 6px;
        background: {BG_BASE};
    }}
    QTabBar::tab {{
        background: {BG_MUTED};
        color: {TEXT_DIM};
        border: 1px solid {BORDER_DIM};
        border-bottom: none;
        border-radius: 5px 5px 0 0;
        padding: 5px 14px;
        margin-right: 2px;
        font-weight: 500;
    }}
    QTabBar::tab:selected {{
        background: {BG_BASE};
        color: {TEXT};
        border-color: {BORDER};
        border-bottom: 2px solid {ACCENT};
    }}
    QTabBar::tab:hover:!selected {{
        background: {BG_HOVER};
        color: {TEXT};
    }}

    /* ----- List widget ----- */
    QListWidget {{
        background: {BG_DEEP};
        color: {TEXT};
        border: 1px solid {BORDER_DIM};
        border-radius: 5px;
    }}
    QListWidget::item:selected {{
        background: {ACCENT_DARK};
        color: #ffffff;
    }}
    QListWidget::item:hover {{
        background: {BG_HOVER};
    }}

    /* ----- Tooltip ----- */
    QToolTip {{
        background: {BG_SURFACE};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 4px 8px;
    }}
"""

MENUBAR_QSS = f"""
    QMenuBar {{
        background: {BG_DEEP};
        color: {TEXT};
        border-bottom: 1px solid {BORDER_DIM};
    }}
    QMenuBar::item {{
        padding: 4px 10px;
        border-radius: 4px;
    }}
    QMenuBar::item:selected {{
        background: {BG_HOVER};
        color: {ACCENT_LITE};
    }}
    QMenu {{
        background: {BG_SURFACE};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 4px 0;
    }}
    QMenu::item {{
        padding: 5px 24px 5px 14px;
    }}
    QMenu::item:selected {{
        background: {ACCENT_DARK};
        color: #ffffff;
    }}
    QMenu::separator {{
        height: 1px;
        background: {BORDER_DIM};
        margin: 3px 8px;
    }}
"""

NAV_BUTTON_QSS = f"""
    QPushButton {{
        background: transparent;
        border: none;
        color: {TEXT_DIM};
        border-radius: 8px;
        padding: 4px;
        font-size: 11px;
    }}
    QPushButton:hover {{
        background: {BG_MUTED};
        color: {TEXT};
    }}
    QPushButton:checked {{
        background: {ACCENT_BG};
        border-left: 3px solid {ACCENT};
        color: {ACCENT_LITE};
        font-weight: 600;
    }}
"""

SIDEBAR_QSS = f"background:{BG_SURFACE}; border-right:1px solid {BORDER_DIM};"

# Inline style fragments for panels
SPIN_STYLE = (
    f"QDoubleSpinBox,QSpinBox{{background:{BG_MUTED};color:{TEXT};"
    f"border:1px solid {BORDER};border-radius:4px;padding:0 24px 0 6px;}}"
    f"QDoubleSpinBox::up-button,QSpinBox::up-button{{subcontrol-origin:border;"
    f"subcontrol-position:top right;width:18px;height:15px;"
    f"border-left:1px solid {BORDER_DIM};border-bottom:1px solid {BORDER_DIM};"
    f"background:{BG_MUTED};border-top-right-radius:4px;}}"
    f"QDoubleSpinBox::down-button,QSpinBox::down-button{{subcontrol-origin:border;"
    f"subcontrol-position:bottom right;width:18px;height:15px;"
    f"border-left:1px solid {BORDER_DIM};background:{BG_MUTED};"
    f"border-bottom-right-radius:4px;}}"
)

COMBO_STYLE = (
    f"QComboBox{{background:{BG_MUTED};color:{TEXT};"
    f"border:1px solid {BORDER};border-radius:4px;padding:2px 6px;}}"
    f"QComboBox QAbstractItemView{{background:{BG_MUTED};color:{TEXT};}}"
)

GRP_STYLE_TMPL = (
    "QGroupBox{{color:{t};border:1px solid " + BORDER_DIM + ";border-radius:5px;"
    "margin-top:8px;padding-top:8px;font-weight:600;}}"
    "QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 5px;"
    "color:{t};}}"
)

LIST_STYLE = (
    f"QListWidget{{background:{BG_DEEP};color:{TEXT};"
    f"border:1px solid {BORDER_DIM};font-size:11px;}}"
    f"QListWidget::item:selected{{background:{ACCENT_DARK};}}"
)

TABLE_STYLE = (
    f"QTableWidget{{background:{BG_DEEP};color:{TEXT};"
    f"border:1px solid {BORDER_DIM};gridline-color:{BORDER_DIM};font-size:11px;}}"
    f"QTableWidget::item:selected{{background:{ACCENT_DARK};}}"
)

TABLE_HEADER_STYLE = (
    f"QHeaderView::section{{background:{BG_SURFACE};color:{ACCENT};"
    f"border:none;border-bottom:2px solid {ACCENT_DARK};"
    f"padding:4px;font-size:11px;font-weight:700;}}"
)

LOG_STYLE = f"background:{BG_DEEP}; color:{TEXT}; font-family:monospace;"

# Job row status background colors
JOB_ROW_COLORS = {
    "FAILED":   ERROR_BG,
    "QUEUED":   WARNING_BG,
    "RUNNING":  INFO_BG,
    "FINISHED": None,
}

# Status badge colors (flat, professional)
STATUS_BADGE_COLORS = {
    "queued":    "#6e7681",   # neutral gray
    "running":   "#1f6feb",   # blue
    "finished":  "#238636",   # green
    "failed":    "#da3633",   # red
    "cancelled": "#9a6700",   # amber
}
