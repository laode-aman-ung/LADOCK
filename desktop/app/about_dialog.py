"""
LADOCK — About Dialog (app/about_dialog.py)
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QTabWidget, QWidget, QFrame
)
from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QFont, QDesktopServices, QPixmap
from PySide6.QtWidgets import QApplication


_ABOUT_HTML = """
<h2 style="color:#89b4fa;">LADOCK Desktop</h2>
<p style="color:#cdd6f4;">Version <b>2.0</b> &nbsp;·&nbsp; Python + PySide6</p>
<hr style="border-color:#45475a;"/>
<p style="color:#cdd6f4;">
An open-source molecular docking workstation built on the LADOCK pipeline.<br/>
Supports <b>AutoDock Vina</b>, <b>AutoDock 4</b>, <b>VinaGPU</b>, and <b>AutoDock-GPU</b>.
</p>
<p style="color:#a6e3a1;">
<b>Features:</b><br/>
• PySide6 GUI with dark Catppuccin theme<br/>
• Batch docking with parallel job scheduling<br/>
• Ligand library management (CSV / SDF / PDBQT)<br/>
• Interactive 3-D molecular viewer (3Dmol.js)<br/>
• Non-covalent interaction analysis (H-bond, π-stacking, hydrophobic…)<br/>
• Real-time job progress tracking<br/>
• Result explorer with sortable tables
</p>
<p style="color:#585b70; font-size:11px;">
Built on top of the original LADOCK docking pipeline.<br/>
RDKit · NumPy · SciPy · PySide6 · 3Dmol.js
</p>
"""

_CITATION_PLAIN = """\
[1] Aman LO, Ischak NI, Tuloli TS, Arfan A, Asnawi A. (2024).
    Multiple ligands simultaneous molecular docking and dynamics approach to
    study the synergetic inhibitory of curcumin analogs on ErbB4 tyrosine
    phosphorylation. Research in Pharmaceutical Sciences, 19(6), 754–765.
    https://doi.org/10.4103/RPS.RPS_191_23

[2] Aman LO, Sihaloho M, Arfan A. (2023).
    Pencarian inhibitor DYRK2 dari database bahan alam ZINC15: Analisis
    farmakofor, simulasi docking dan dinamika molekuler.
    Jurnal Sains Farmasi & Klinis, 10(1), 100–113.
    https://doi.org/10.25077/jsfk.10.1.100-113.2023

[3] Aman LO, Arfan A, Asnawi A. (2023).
    In silico study of the synergistic interaction of 5-fluorouracil and
    curcumin analogues as inhibitors of B-cell lymphoma 2 protein.
    International Journal of Applied Pharmaceutics, 15(Special Issue 2), 61–66.
    https://doi.org/10.22159/ijap.2023.v15s2.05

── Third-party tools ───────────────────────────────────────────────────────

AutoDock Vina:
  Eberhardt J., Santos-Martins D., Tillack A.F., Forli S. (2021)
  J. Chem. Inf. Model. 61(8):3891–3898.
  https://doi.org/10.1021/acs.jcim.1c00203

RDKit: Landrum G. https://www.rdkit.org

3Dmol.js: Rego N. & Koes D. (2015) Bioinformatics 31(8):1322–1324.
  https://doi.org/10.1093/bioinformatics/btu829
"""

_CITATION_HTML = """
<style>
  body  { background:#1e1e2e; color:#cdd6f4; font-family:sans-serif;
          font-size:12px; margin:0; padding:8px; }
  .hdr  { color:#f38ba8; font-size:11px; font-weight:bold;
          letter-spacing:1px; margin:0 0 8px 0; }
  .card { background:#181825; border-left:3px solid #89b4fa;
          border-radius:4px; padding:8px 12px; margin-bottom:8px; }
  .num  { color:#89b4fa; font-weight:bold; font-size:13px; }
  .auth { color:#cdd6f4; margin:2px 0; }
  .titl { color:#a6e3a1; font-style:italic; margin:2px 0; }
  .jour { color:#a6adc8; font-size:11px; margin:2px 0; }
  .doi  { color:#89dceb; font-size:10px; }
  .sep  { border:none; border-top:1px solid #313244; margin:10px 0; }
  .sec  { color:#fab387; font-size:10px; font-weight:bold;
          letter-spacing:1px; margin:6px 0 6px 0; }
  .tool { background:#181825; border-left:3px solid #fab387;
          border-radius:4px; padding:6px 10px; margin-bottom:6px; }
  .tn   { color:#fab387; font-weight:bold; }
  .td   { color:#a6adc8; font-size:10px; margin:1px 0; }
  .tl   { color:#89dceb; font-size:10px; }
</style>
<p class="hdr">📚 PRIMARY CITATIONS — Please cite if you use LADOCK</p>

<div class="card">
  <span class="num">[1]</span>
  <p class="auth">Aman LO, Ischak NI, Tuloli TS, Arfan A, Asnawi A. <span style="color:#585b70;">(2024)</span></p>
  <p class="titl">Multiple ligands simultaneous molecular docking and dynamics approach to study the synergetic inhibitory of curcumin analogs on ErbB4 tyrosine phosphorylation.</p>
  <p class="jour">Research in Pharmaceutical Sciences, 19(6), 754–765.</p>
  <p class="doi">🔗 https://doi.org/10.4103/RPS.RPS_191_23</p>
</div>

<div class="card">
  <span class="num">[2]</span>
  <p class="auth">Aman LO, Sihaloho M, Arfan A. <span style="color:#585b70;">(2023)</span></p>
  <p class="titl">Pencarian inhibitor DYRK2 dari database bahan alam ZINC15: Analisis farmakofor, simulasi docking dan dinamika molekuler.</p>
  <p class="jour">Jurnal Sains Farmasi &amp; Klinis, 10(1), 100–113.</p>
  <p class="doi">🔗 https://doi.org/10.25077/jsfk.10.1.100-113.2023</p>
</div>

<div class="card">
  <span class="num">[3]</span>
  <p class="auth">Aman LO, Arfan A, Asnawi A. <span style="color:#585b70;">(2023)</span></p>
  <p class="titl">In silico study of the synergistic interaction of 5-fluorouracil and curcumin analogues as inhibitors of B-cell lymphoma 2 protein.</p>
  <p class="jour">International Journal of Applied Pharmaceutics, 15(Special Issue 2), 61–66.</p>
  <p class="doi">🔗 https://doi.org/10.22159/ijap.2023.v15s2.05</p>
</div>

<hr class="sep"/>
<p class="sec">⚙ THIRD-PARTY TOOLS</p>

<div class="tool">
  <span class="tn">AutoDock Vina</span>
  <p class="td">Eberhardt J., Santos-Martins D., Tillack A.F., Forli S. (2021)
  J. Chem. Inf. Model. 61(8):3891–3898.</p>
  <p class="tl">🔗 https://doi.org/10.1021/acs.jcim.1c00203</p>
</div>
<div class="tool">
  <span class="tn">RDKit</span>
  <p class="td">Landrum G. RDKit: Open-source cheminformatics.</p>
  <p class="tl">🔗 https://www.rdkit.org</p>
</div>
<div class="tool">
  <span class="tn">3Dmol.js</span>
  <p class="td">Rego N. &amp; Koes D. (2015) Bioinformatics 31(8):1322–1324.</p>
  <p class="tl">🔗 https://doi.org/10.1093/bioinformatics/btu829</p>
</div>
"""

_LICENSE = """LADOCK Desktop Software License
Copyright (c) 2024 La Ode Aman — All rights reserved.

ACADEMIC FREE LICENSE (2024 – 2030)
  Free for everyone for non-commercial use — research, study,
  teaching, and evaluation.
  No registration, institutional email, or license key required.
  Valid until: December 31, 2030.

ACADEMIC DISCOUNT (Post-2030)
  Discounted institutional license after the free period.
  Contact: laode_aman@ung.ac.id

COMMERCIAL LICENSE
  Required for any for-profit use (pharma, CRO, biotech, etc.).
  Contact: laode_aman@ung.ac.id
  Subject: LADOCK Commercial License Inquiry

NO WARRANTY
  This software is provided "as is" without warranty of any kind.
  The author is not liable for any damages arising from its use.

For full license terms see the LICENSE file in the installation directory
or visit the LADOCK website.
"""


class AboutDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About LADOCK Desktop")
        self.setMinimumSize(500, 420)
        self.setStyleSheet("""
            QDialog { background:#1e1e2e; color:#cdd6f4; }
            QTabWidget::pane { border:1px solid #45475a; }
            QTabBar::tab {
                background:#313244; color:#cdd6f4; padding:6px 16px;
                border-radius:4px 4px 0 0;
            }
            QTabBar::tab:selected { background:#45475a; color:#89b4fa; }
            QTextEdit { background:#181825; color:#cdd6f4;
                        border:none; font-family:Monospace; font-size:11px; }
            QPushButton { background:#313244; border:1px solid #45475a;
                          border-radius:4px; color:#cdd6f4; padding:4px 14px; }
            QPushButton:hover { background:#45475a; }
        """)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 12)

        # Header
        hdr = QHBoxLayout()
        logo = QLabel("🧬")
        logo.setFont(QFont("Sans", 36))
        hdr.addWidget(logo)

        title_block = QVBoxLayout()
        title = QLabel("LADOCK Desktop")
        title.setFont(QFont("Sans", 18, QFont.Bold))
        title.setStyleSheet("color:#89b4fa;")
        ver = QLabel("Version 2.0  ·  Built with PySide6 + 3Dmol.js")
        ver.setStyleSheet("color:#585b70; font-size:11px;")
        title_block.addWidget(title)
        title_block.addWidget(ver)
        hdr.addLayout(title_block)
        hdr.addStretch()
        lay.addLayout(hdr)

        lay.addSpacing(8)

        # Tabs
        tabs = QTabWidget()

        # About tab
        about_w = QWidget()
        about_lay = QVBoxLayout(about_w)
        about_label = QLabel(_ABOUT_HTML)
        about_label.setWordWrap(True)
        about_label.setTextFormat(Qt.RichText)
        about_label.setOpenExternalLinks(True)
        about_lay.addWidget(about_label)
        about_lay.addStretch()
        tabs.addTab(about_w, "About")

        # Citation tab
        cite_w = QWidget()
        cite_lay = QVBoxLayout(cite_w)
        cite_lay.setContentsMargins(0, 0, 0, 4)
        cite_edit = QTextEdit()
        cite_edit.setReadOnly(True)
        cite_edit.setHtml(_CITATION_HTML)
        cite_edit.setStyleSheet(
            "QTextEdit{background:#1e1e2e;border:none;}")
        copy_btn = QPushButton("📋  Copy Citations")
        copy_btn.setFixedHeight(28)
        def _copy_citation():
            QApplication.clipboard().setText(_CITATION_PLAIN)
            copy_btn.setText("✅  Copied to clipboard!")
            QTimer.singleShot(2000, lambda: copy_btn.setText("📋  Copy Citations"))
        copy_btn.clicked.connect(_copy_citation)
        cite_lay.addWidget(cite_edit)
        cite_lay.addWidget(copy_btn)
        tabs.addTab(cite_w, "Citation")

        # License tab
        lic_edit = QTextEdit(_LICENSE)
        lic_edit.setReadOnly(True)
        tabs.addTab(lic_edit, "License")

        lay.addWidget(tabs)

        # Buttons
        btn_row = QHBoxLayout()
        gh_btn = QPushButton("🌐 GitHub")
        gh_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://github.com/"))
        )
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(gh_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)
