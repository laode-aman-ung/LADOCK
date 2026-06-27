"""
LADOCK License Dialog (app/license_dialog.py)

Shown on first launch (no license) or from Help → License.
Handles activation, status display, and deactivation.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QWidget, QSizePolicy, QApplication,
)
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QFont, QDesktopServices

from core.license_manager import (
    LicenseInfo, LicenseStatus, LicenseType,
    load_license, save_license, remove_license, validate_key,
    ACADEMIC_FREE_UNTIL,
)
from datetime import date


# ── Palette (Catppuccin Mocha) ───────────────────────────────────────────────
_QSS = """
QDialog            { background:#1e1e2e; color:#cdd6f4; }
QLabel             { color:#cdd6f4; }
QLineEdit          {
    background:#181825; color:#cdd6f4;
    border:1px solid #45475a; border-radius:6px;
    padding:8px 12px; font-size:13px;
    selection-background-color:#89b4fa;
}
QLineEdit:focus    { border-color:#89b4fa; }
QPushButton        {
    background:#313244; border:1px solid #45475a;
    border-radius:6px; color:#cdd6f4;
    padding:7px 18px; font-size:12px;
}
QPushButton:hover  { background:#45475a; }
QPushButton#btnActivate {
    background:#89b4fa; color:#1e1e2e; font-weight:bold;
    border:none;
}
QPushButton#btnActivate:hover { background:#b4befe; }
QPushButton#btnContact {
    background:transparent; color:#89b4fa;
    border:1px solid #89b4fa; border-radius:6px;
}
QPushButton#btnContact:hover { background:rgba(137,180,250,0.12); }
QFrame#divider     { background:#313244; }
"""

_TYPE_COLOR = {
    LicenseType.ACADEMIC_FREE:     "#a6e3a1",   # green
    LicenseType.ACADEMIC_DISCOUNT: "#cba6f7",   # mauve
    LicenseType.COMMERCIAL:        "#89b4fa",   # blue
    LicenseType.UNLICENSED:        "#f38ba8",   # red
}
_STATUS_COLOR = {
    LicenseStatus.VALID:   "#a6e3a1",
    LicenseStatus.EXPIRED: "#f9e2af",
    LicenseStatus.INVALID: "#f38ba8",
    LicenseStatus.MISSING: "#fab387",
}


class LicenseDialog(QDialog):

    def __init__(self, parent=None, *, require_valid: bool = False):
        """
        require_valid: if True, the dialog cannot be closed without a valid license
                       (used on startup when the free period has ended).
        """
        super().__init__(parent)
        self._require_valid = require_valid
        self._info = load_license()

        self.setWindowTitle("LADOCK — License")
        self.setMinimumWidth(520)
        self.setStyleSheet(_QSS)
        if require_valid:
            self.setWindowFlag(Qt.WindowCloseButtonHint, False)

        self._build_ui()
        self._refresh_status()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 28, 28, 20)
        lay.setSpacing(16)

        # Title
        title = QLabel("🔐  LADOCK License")
        title.setFont(QFont("Sans", 16, QFont.Bold))
        title.setStyleSheet("color:#89b4fa;")
        lay.addWidget(title)

        # Status card
        self._status_card = _StatusCard()
        lay.addWidget(self._status_card)

        # Divider
        div = QFrame()
        div.setObjectName("divider")
        div.setFixedHeight(1)
        lay.addWidget(div)

        # Activate section
        act_title = QLabel("Activate License Key")
        act_title.setFont(QFont("Sans", 11, QFont.Bold))
        act_title.setStyleSheet("color:#cdd6f4; margin-top:4px;")
        lay.addWidget(act_title)

        act_hint = QLabel(
            "Enter the license key you received via email from "
            "<b style='color:#89b4fa'>laode_aman@ung.ac.id</b>"
        )
        act_hint.setWordWrap(True)
        act_hint.setStyleSheet("color:#a6adc8; font-size:12px;")
        act_hint.setTextFormat(Qt.RichText)
        lay.addWidget(act_hint)

        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("LADOCK-xxxxxxxxxxxxxxxx.yyyyyyyyyyyyyyyy")
        self._key_input.returnPressed.connect(self._activate)
        lay.addWidget(self._key_input)

        self._msg_label = QLabel("")
        self._msg_label.setWordWrap(True)
        self._msg_label.setStyleSheet("font-size:11px;")
        lay.addWidget(self._msg_label)

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._btn_activate = QPushButton("Activate")
        self._btn_activate.setObjectName("btnActivate")
        self._btn_activate.clicked.connect(self._activate)

        self._btn_deactivate = QPushButton("Remove License")
        self._btn_deactivate.clicked.connect(self._deactivate)
        self._btn_deactivate.setVisible(False)

        btn_contact = QPushButton("📧  Request License")
        btn_contact.setObjectName("btnContact")
        btn_contact.clicked.connect(self._open_contact)

        btn_row.addWidget(self._btn_activate)
        btn_row.addWidget(self._btn_deactivate)
        btn_row.addStretch()
        btn_row.addWidget(btn_contact)
        lay.addLayout(btn_row)

        # Divider
        div2 = QFrame()
        div2.setObjectName("divider")
        div2.setFixedHeight(1)
        lay.addWidget(div2)

        # Footer
        footer = QLabel(
            "Academic users: send email to <b>laode_aman@ung.ac.id</b> "
            "with subject <i>LADOCK Academic License</i> and attach proof of "
            "institutional affiliation.<br/>"
            "Academic free period: until <b style='color:#a6e3a1'>December 31, 2027</b>."
        )
        footer.setWordWrap(True)
        footer.setTextFormat(Qt.RichText)
        footer.setStyleSheet("color:#6c7086; font-size:11px;")
        lay.addWidget(footer)

        # Close button (only when valid or not required)
        close_row = QHBoxLayout()
        close_row.addStretch()
        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.accept)
        close_row.addWidget(self._btn_close)
        lay.addLayout(close_row)

    # ── Logic ─────────────────────────────────────────────────────────────────

    def _refresh_status(self):
        self._status_card.update_info(self._info)
        has_key = self._info.status not in (
            LicenseStatus.MISSING, LicenseStatus.UNLICENSED
        ) or self._info.type == LicenseType.ACADEMIC_FREE

        self._btn_deactivate.setVisible(
            self._info.status == LicenseStatus.VALID
            and self._info.type != LicenseType.ACADEMIC_FREE
        )
        # Disable close if license is required but not valid
        can_close = not self._require_valid or self._info.is_valid
        self._btn_close.setEnabled(can_close)
        if not can_close:
            self._btn_close.setToolTip("A valid license is required to use LADOCK.")

    def _activate(self):
        key = self._key_input.text().strip()
        if not key:
            self._show_msg("Please enter a license key.", error=True)
            return

        info = validate_key(key)
        if info.status == LicenseStatus.VALID:
            save_license(key)
            self._info = info
            self._key_input.clear()
            self._show_msg("✅  License activated successfully!", error=False)
            self._refresh_status()
        elif info.status == LicenseStatus.EXPIRED:
            self._show_msg(f"❌  {info.message}", error=True)
        else:
            self._show_msg(f"❌  {info.message}", error=True)

    def _deactivate(self):
        remove_license()
        self._info = load_license()
        self._key_input.clear()
        self._show_msg("License removed.", error=False)
        self._refresh_status()

    def _show_msg(self, text: str, error: bool = False):
        color = "#f38ba8" if error else "#a6e3a1"
        self._msg_label.setStyleSheet(f"font-size:11px; color:{color};")
        self._msg_label.setText(text)
        QTimer.singleShot(5000, lambda: self._msg_label.setText(""))

    def _open_contact(self):
        QDesktopServices.openUrl(QUrl(
            "mailto:laode_aman@ung.ac.id"
            "?subject=LADOCK%20License%20Inquiry"
        ))


# ── Status Card widget ────────────────────────────────────────────────────────

class _StatusCard(QFrame):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame { background:#181825; border:1px solid #313244;
                     border-radius:10px; padding:4px; }
            QLabel { background:transparent; border:none; }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)

        row1 = QHBoxLayout()
        self._type_label  = QLabel("—")
        self._type_label.setFont(QFont("Sans", 13, QFont.Bold))
        self._status_badge = QLabel("—")
        self._status_badge.setStyleSheet(
            "border-radius:4px; padding:2px 10px; font-size:11px; font-weight:bold;"
        )
        row1.addWidget(self._type_label)
        row1.addStretch()
        row1.addWidget(self._status_badge)
        lay.addLayout(row1)

        self._name_label    = QLabel()
        self._name_label.setStyleSheet("color:#a6adc8; font-size:12px;")
        self._email_label   = QLabel()
        self._email_label.setStyleSheet("color:#74c7ec; font-size:11px;")
        self._expiry_label  = QLabel()
        self._expiry_label.setStyleSheet("color:#a6adc8; font-size:11px;")

        lay.addWidget(self._name_label)
        lay.addWidget(self._email_label)
        lay.addWidget(self._expiry_label)

    def update_info(self, info: LicenseInfo):
        type_color   = _TYPE_COLOR.get(info.type, "#cdd6f4")
        status_color = _STATUS_COLOR.get(info.status, "#cdd6f4")

        self._type_label.setText(info.type_label())
        self._type_label.setStyleSheet(f"color:{type_color}; font-size:13px; font-weight:bold;")

        self._status_badge.setText(info.status.value)
        self._status_badge.setStyleSheet(
            f"background:rgba(0,0,0,0.3); color:{status_color}; "
            f"border:1px solid {status_color}; border-radius:4px; "
            f"padding:2px 10px; font-size:11px; font-weight:bold;"
        )

        self._name_label.setText(f"👤 {info.name}" if info.name else "")
        self._email_label.setText(f"📧 {info.email}" if info.email else "")

        if info.type == LicenseType.ACADEMIC_FREE and not info.name:
            self._name_label.setText("All verified academic users")
            self._email_label.setText("Institutional email required (.ac.id / .edu / etc.)")

        if info.expires:
            days = info.days_remaining
            if days is not None and days >= 0:
                color = "#a6e3a1" if days > 90 else "#f9e2af" if days > 30 else "#f38ba8"
                self._expiry_label.setText(
                    f"⏳ Expires: {info.expires}  ({days} days remaining)"
                )
                self._expiry_label.setStyleSheet(f"color:{color}; font-size:11px;")
            elif days is not None and days < 0:
                self._expiry_label.setText(f"⛔ Expired: {info.expires}")
                self._expiry_label.setStyleSheet("color:#f38ba8; font-size:11px;")
        elif info.type == LicenseType.COMMERCIAL:
            self._expiry_label.setText("✅ Perpetual license — no expiry")
            self._expiry_label.setStyleSheet("color:#a6e3a1; font-size:11px;")
        else:
            self._expiry_label.setText(info.message)
            self._expiry_label.setStyleSheet("color:#6c7086; font-size:11px;")
