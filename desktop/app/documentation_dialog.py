"""
LADOCK — Documentation Dialog (app/documentation_dialog.py)

In-app user guide, opened from Help → Documentation (F1). Content mirrors the
current application: native (Meeko/RDKit) preparation, the four workflow tabs,
the docking engines, and the cross-platform / WSL backend behaviour.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextBrowser, QTabWidget,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QDesktopServices


# --------------------------------------------------------------------------- #
# Shared style prepended to each section (Catppuccin, matching About/License)
# --------------------------------------------------------------------------- #
_CSS = """
<style>
  body   { color:#cdd6f4; font-family:sans-serif; font-size:12.5px; line-height:1.5; }
  h2     { color:#89b4fa; font-size:16px; margin:0 0 6px 0; }
  h3     { color:#f9e2af; font-size:13px; margin:14px 0 4px 0; }
  p      { margin:5px 0; }
  ul     { margin:4px 0 8px 0; padding-left:20px; }
  li     { margin:3px 0; }
  b      { color:#f5f5f5; }
  code   { background:#181825; color:#a6e3a1; padding:1px 5px; border-radius:3px;
           font-family:Consolas,Monospace; font-size:11.5px; }
  .tag   { color:#a6e3a1; }
  .warn  { color:#fab387; }
  .muted { color:#7f849c; font-size:11px; }
  .card  { background:#181825; border-left:3px solid #89b4fa;
           border-radius:4px; padding:6px 12px; margin:8px 0; }
  table  { border-collapse:collapse; margin:8px 0; width:100%; }
  th,td  { border:1px solid #313244; padding:5px 9px; text-align:left; font-size:11.5px; }
  th     { background:#313244; color:#89b4fa; }
  td.ok  { color:#a6e3a1; } td.no { color:#f38ba8; } td.opt { color:#fab387; }
</style>
"""

# --------------------------------------------------------------------------- #
# Section content
# --------------------------------------------------------------------------- #
_OVERVIEW = _CSS + """
<h2>LADOCK Desktop — Overview</h2>
<p>LADOCK is a molecular docking workstation. A run moves through four tabs on
the left, in order:</p>
<ul>
  <li><b>Preparation</b> — clean protein targets and export docking-ready receptors.</li>
  <li><b>Redocking</b> — dock a target's native (co-crystallised) ligand back into its site.</li>
  <li><b>Lig Test</b> — screen your own test ligands against prepared receptors.</li>
  <li><b>Jobs</b> / <b>Results</b> — watch running docking jobs and browse binding-energy tables.</li>
</ul>

<h3>Projects &amp; the job directory</h3>
<p>Everything lives inside a <b>job directory</b> (a project). Create one with
<b>File → New Project</b> or the <b>Generate Job Dir</b> button — you type the
folder name and choose where it is saved. It contains:</p>
<ul>
  <li><code>target_input/</code> — raw protein PDB files you import.</li>
  <li><code>ligand_input/</code> — test-ligand files (preview only; prepared automatically at docking time).</li>
  <li><code>receptor_ready/</code> — cleaned receptors produced by Preparation.</li>
  <li><code>results/</code> — docking outputs and result CSVs.</li>
  <li><code>logs/</code> — per-run logs.</li>
</ul>

<h3>Preparation of molecules is automatic</h3>
<p>You no longer add charges or convert to PDBQT by hand. Receptor and ligand
PDBQT files are generated <b>natively</b> by Meeko + RDKit at the moment they
are needed — the same on Windows, Linux and macOS, with no MGLTools or WSL
required for Vina/Vinardo.</p>
"""

_WORKFLOW = _CSS + """
<h2>Step-by-step workflow</h2>

<h3>1 · Preparation</h3>
<ul>
  <li>Click <b>Import Targets</b> to copy protein PDB files into <code>target_input/</code>.</li>
  <li>Select a target to see its <b>components</b> (chains, ligands, water, ions).
      Tick the components you want to <b>keep</b>. By default nothing is ticked —
      you must keep at least one, or a reminder pops up.</li>
  <li>Click <b>Save</b>: only the ticked components are written to a cleaned
      receptor in <code>receptor_ready/</code>. The 3-D preview updates as you switch files.</li>
</ul>

<h3>2 · Redocking</h3>
<ul>
  <li>The <b>RECEPTOR READY</b> list shows cleaned receptors. <b>Select</b> a receptor to
      view/edit its components and docking parameters; <b>tick</b> the ones to dock.
      Each ticked receptor keeps its own full parameter set.</li>
  <li>Set the grid <b>box</b> (center to the native ligand + size), pick one or more
      <b>scoring functions</b>, and choose <b>rigid</b> or <b>flexible</b> mode.</li>
  <li>Click <b>Run</b>. Progress streams to the <b>Jobs</b> tab; results are written as a
      CSV that opens in the <b>Results</b> tab.</li>
</ul>

<h3>3 · Lig Test</h3>
<ul>
  <li>The <b>LIGANDS</b> panel is a checklist — <b>tick the ligand files</b> you want to dock
      (Tick All / None). Only ticked ligands run. Supported inputs: PDBQT, PDB, SDF,
      MOL, MOL2, SMILES (<code>.smi/.smiles/.txt</code>), CSV/TSV and Excel with a SMILES column.</li>
  <li>Pick the receptor(s) from <b>RECEPTOR READY</b>, set the box + scoring, and Run.
      Multi-molecule libraries are expanded and prepared automatically.</li>
  <li><b>Simultaneous Ligands (MLSD)</b> docks several ligands together in one
      pocket. It is a Vina-only feature, so the control is enabled only when
      <b>Vina</b>/<b>Vinardo</b> is selected — see the <i>Engines</i> tab.</li>
</ul>

<h3>4 · Jobs &amp; Results</h3>
<ul>
  <li><b>Jobs</b> streams live log output and survives closing/reopening the app or project.</li>
  <li><b>Results</b> shows sortable binding-energy tables loaded from the run CSVs.</li>
</ul>
"""

_ENGINES = _CSS + """
<h2>Preparation &amp; docking engines</h2>

<h3>Native preparation (Meeko + RDKit)</h3>
<p>Receptor and ligand PDBQT files are built in-process:</p>
<ul>
  <li><b>Receptor</b>: cleaned protein PDB → PDBQT via Meeko <code>mk_prepare_receptor</code>.</li>
  <li><b>Ligand</b>: RDKit normalises the molecule (adds explicit H, embeds 3-D only when the
      input has no coordinates) → Meeko <code>mk_prepare_ligand</code>. Ligands that already
      carry a pose (redocking native ligands, SDF/MOL2) <b>keep their coordinates</b>.</li>
  <li>OpenBabel and legacy MGLTools are used only as automatic fallbacks.</li>
</ul>
<p class="muted">Because this runs in the app's own Python, it needs no external
binary — the same code path works on every OS.</p>

<h3>Scoring functions</h3>
<table>
  <tr><th>Function</th><th>Engine</th><th>Notes</th></tr>
  <tr><td>Vina</td><td>AutoDock Vina</td><td>Native on all platforms.</td></tr>
  <tr><td>Vinardo</td><td>AutoDock Vina</td><td>Native on all platforms.</td></tr>
  <tr><td>AD4</td><td>AutoGrid4 + AutoDock4</td><td>Needs the grid path (MGLTools GPF); Linux binaries.</td></tr>
  <tr><td>AD4-GPU</td><td>AutoDock-GPU</td><td>Linux binary; needs an NVIDIA/CUDA runtime.</td></tr>
</table>
<p>A single job may combine several scoring functions. The <b>AD4 / AD4-GPU</b> grid
path still relies on the bundled MGLTools (GPF / flex splitting) and therefore runs
only where those Linux binaries are available.</p>

<h3>Feature support per scoring function</h3>
<p>Two optional features are <b>not</b> supported by every engine, so LADOCK
enables their controls in the docking parameters only when a scoring function
that supports them is selected:</p>
<table>
  <tr><th>Feature</th><th>Vina</th><th>Vinardo</th><th>AD4</th><th>AD4-GPU</th></tr>
  <tr><td><b>Flexible residues</b><br/><span class="muted">flexible receptor side chains</span></td>
      <td class="ok">✔</td><td class="ok">✔</td><td class="ok">✔</td><td class="ok">✔</td></tr>
  <tr><td><b>MLSD</b><br/><span class="muted">Multiple-Ligand Simultaneous Docking</span></td>
      <td class="ok">✔</td><td class="ok">✔</td><td class="no">—</td><td class="no">—</td></tr>
</table>
<ul>
  <li><b>Flexible residues</b> — all engines support it, so the <i>Mode → Flexible</i>
      option (and its Flex Distance / Flexible Residues fields) is always available.</li>
  <li><b>MLSD</b> — docking several ligands together in one pocket is a native
      feature of the AutoDock&nbsp;Vina&nbsp;1.2 engine only. The <i>Simultaneous
      Ligands</i> control (Lig&nbsp;Test) is therefore enabled only when
      <b>Vina</b> or <b>Vinardo</b> is checked; if you select only AD4 / AD4-GPU
      it resets to 1 and greys out. AutoDock4 and AutoDock-GPU dock one ligand
      per run.</li>
</ul>
"""

_PLATFORMS = _CSS + """
<h2>Platforms &amp; supported scoring functions</h2>

<p>Which scoring functions are available depends on where LADOCK runs, because
AutoDock4, AutoGrid4, AutoDock-GPU and ADFR are distributed <b>only as Linux
binaries</b>. LADOCK detects the engines actually present on the current
platform and reflects this automatically: the <b>Tool Paths</b> status row shows
<span class="tag">✅</span>/<span class="warn">❌</span> per engine, and any
scoring function whose engine is missing is <b>greyed out</b> in the docking
parameters.</p>

<table>
  <tr><th>Where LADOCK runs</th><th>Supported scoring functions</th><th>Notes</th></tr>
  <tr><td><b>Windows</b> (native)</td><td class="ok">Vina, Vinardo</td>
      <td>Pure-native. No MGLTools, no WSL.</td></tr>
  <tr><td><b>Windows + Hybrid</b> (WSL)</td><td class="ok">Vina, Vinardo, AD4, AD4-GPU*</td>
      <td>GUI + prep stay native (embedded 3D works); AD4/AD-GPU run via WSL.</td></tr>
  <tr><td><b>Linux</b> (native)</td><td class="ok">Vina, Vinardo, AD4, AD4-GPU*</td>
      <td>Full engine set.</td></tr>
  <tr><td><b>macOS</b> (native)</td><td class="ok">Vina, Vinardo</td>
      <td>No Linux engines are shipped for macOS.</td></tr>
</table>
<p class="muted">* AutoDock-GPU additionally needs an NVIDIA driver and CUDA runtime. LADOCK
verifies the CUDA libraries actually resolve (inside WSL for hybrid); if they are
missing, AD4-GPU is greyed out even though the binary is present.</p>

<div class="card">
  <b>Hybrid mode (Windows).</b> Enable it in <b>Tools → Settings → Backend</b> to
  use the Linux-only engines from Windows: <b>Vina/Vinardo</b> and molecule
  preparation stay native (so the <b>embedded 3D preview keeps working</b>), while
  <b>AD4 / AD-GPU</b> and the AutoGrid4 / MGLTools grid path are dispatched to
  <b>WSL</b>. Requires WSL + an Ubuntu distro; AD-GPU also needs CUDA-on-WSL. With
  hybrid off, Windows stays pure-native (Vina/Vinardo only).
</div>

<h3>Bundled binaries</h3>
<p>Binaries ship per platform under <code>bin/&lt;platform&gt;/</code>
(<code>windows</code>, <code>linux</code>, <code>mac</code>). The Linux folder also holds
AutoDock4/AutoGrid4, AutoDock-GPU, ADFRsuite and MGLTools.</p>

<h3>Requirements</h3>
<p><code>PySide6</code>, <code>numpy</code>, <code>scipy</code>, <code>pandas</code>,
<code>rdkit</code>, and <code>meeko</code> (+ <code>gemmi</code>). Install with
<code>pip install -e .</code> from the <code>desktop/</code> folder.</p>
"""

_TROUBLE = _CSS + """
<h2>Troubleshooting</h2>

<h3>"Meeko Not Installed"</h3>
<p>Native preparation needs Meeko. Install it with <code>pip install meeko</code>
(it also pulls in <code>gemmi</code>), or configure an MGLTools path to use the
legacy pipeline.</p>

<h3>AD4 / AD4-GPU are greyed out</h3>
<p>On <b>Windows</b>, enable <b>Hybrid mode</b> (Tools → Settings → Backend) to run
them via WSL — Vina/Vinardo and the 3D preview stay native. On <b>macOS</b> they
are unavailable (no Linux binaries). On <b>Linux</b> they should be enabled; if not,
check the <b>Tool Paths</b> badges. AD-GPU also requires CUDA.</p>

<h3>"MGLTools Not Found" (Linux / inside WSL) for AD4 / AD4-GPU or Flexible mode</h3>
<p>These paths require the bundled MGLTools (grid/flex generation). Make sure the
Linux MGLTools bundle is present, or switch to <b>Vina/Vinardo</b>, which never
need MGLTools.</p>

<h3>Docking returns ~0.0 kcal/mol</h3>
<p>Almost always a <b>grid box that misses the binding site</b>. For redocking, center
the box on the native ligand; check the box size covers the pocket.</p>

<h3>3-D preview opens in a browser / is blank</h3>
<p>The embedded viewer needs Qt WebEngine. On some Linux packages it is a separate
install: <code>pip install PySide6-WebEngine</code>. File content still works without it.</p>

<h3>AD4-GPU is greyed out even on Linux</h3>
<p>The AutoDock-GPU binary is present but its CUDA runtime (e.g.
<code>libcurand</code>, <code>libcudart</code>) does not resolve — LADOCK checks
this and disables the engine. Install a matching NVIDIA driver + CUDA runtime
(inside WSL, the CUDA-on-WSL toolkit) and re-detect tools.</p>
"""

_SECTIONS = [
    ("Overview",      _OVERVIEW),
    ("Workflow",      _WORKFLOW),
    ("Engines",       _ENGINES),
    ("Platforms",     _PLATFORMS),
    ("Troubleshooting", _TROUBLE),
]


class DocumentationDialog(QDialog):
    """Tabbed, read-only user guide shown from Help → Documentation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LADOCK Documentation")
        self.setMinimumSize(720, 560)
        self.setStyleSheet("""
            QDialog { background:#1e1e2e; color:#cdd6f4; }
            QTabWidget::pane { border:1px solid #45475a; }
            QTabBar::tab {
                background:#313244; color:#cdd6f4; padding:6px 14px;
                border-radius:4px 4px 0 0;
            }
            QTabBar::tab:selected { background:#45475a; color:#89b4fa; }
            QTextBrowser { background:#1e1e2e; border:none; }
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
        logo = QLabel("📖")
        logo.setFont(QFont("Sans", 30))
        hdr.addWidget(logo)
        title_block = QVBoxLayout()
        title = QLabel("Documentation")
        title.setFont(QFont("Sans", 17, QFont.Bold))
        title.setStyleSheet("color:#89b4fa;")
        sub = QLabel("User guide — workflow, engines and platforms")
        sub.setStyleSheet("color:#585b70; font-size:11px;")
        title_block.addWidget(title)
        title_block.addWidget(sub)
        hdr.addLayout(title_block)
        hdr.addStretch()
        lay.addLayout(hdr)
        lay.addSpacing(8)

        # Tabs — one QTextBrowser per section (external links open in a browser)
        tabs = QTabWidget()
        for name, html in _SECTIONS:
            browser = QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.setHtml(html)
            tabs.addTab(browser, name)
        lay.addWidget(tabs)

        # Buttons
        btn_row = QHBoxLayout()
        web_btn = QPushButton("🌐 Project Website")
        web_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/laode-aman-ung/LADOCK"))
        )
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(web_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)
