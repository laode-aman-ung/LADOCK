"""
LADOCK — Molecular Viewer Panel (PySide6 + QWebEngineView + 3Dmol.js)

Displays receptor (PDBQT) and ligand (PDBQT) in an interactive 3-D view.
Falls back gracefully when no display is available.

Public API
----------
load_receptor(path: str)
load_ligand(path: str)
load_pose(pdbqt_data: str)      ← raw PDBQT string (single pose)
highlight_pocket(cx, cy, cz, sx, sy, sz)
show_hbonds(bonds: list[dict])
clear()
"""

import os
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QSizePolicy, QLabel, QPushButton,
    QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt, QUrl, Signal, Slot, QTimer
from PySide6.QtGui import QDesktopServices, QColor

# Dark theme background used by the viewer HTML — also applied to the web page
# and widget so relayout/repaint never flashes the default white.
_VIEWER_BG = "#0f1117"

_IS_WSL = (
    sys.platform.startswith("linux")
    and (
        "microsoft" in platform.release().lower()
        or "wsl" in platform.release().lower()
        or "WSL_DISTRO_NAME" in os.environ
    )
)

_WEBENGINE_IMPORT_ERROR = None
if not _IS_WSL:
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebEngineCore import QWebEnginePage
    except Exception as exc:  # pragma: no cover - environment-dependent
        QWebEngineView = None
        QWebEnginePage = None
        _WEBENGINE_IMPORT_ERROR = exc
else:
    QWebEngineView = None
    QWebEnginePage = None
    _WEBENGINE_IMPORT_ERROR = RuntimeError(
        "Interactive 3D viewer is disabled for WSL sessions."
    )

# Embedded viewer vs. browser handoff — WSL is treated separately from native Linux:
#   • Windows / native Linux / macOS (real GPU, QtWebEngine present) → EMBEDDED viewer.
#   • WSL → Chromium/WebGL does not work under WSLg, so the 3D view is handed off to
#     the WINDOWS browser (self-contained HTML on a /mnt drive).
#   • Any platform where QtWebEngine is missing/broken → browser fallback too.
_USE_BROWSER_FALLBACK = _IS_WSL or QWebEngineView is None


# Path to the bundled HTML template
_VIEWER_HTML = Path(__file__).parent.parent / "assets" / "viewer.html"
_VIEWER_JS = Path(__file__).parent.parent / "assets" / "3Dmol-min.js"


# ---------------------------------------------------------------------------
# Custom page: suppress JS errors from reaching Qt's stderr
# ---------------------------------------------------------------------------
if QWebEnginePage is not None:
    class _SilentPage(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, msg, line, src):
            # Print warnings and errors to Python console; suppress info/debug noise
            lname = level.name if hasattr(level, 'name') else str(level)
            if 'Error' in lname or 'Warning' in lname:
                print(f"[3DViewer JS {lname}] {src}:{line}: {msg}")
else:
    _SilentPage = None


class _ExternalBrowserViewerPanel(QWidget):
    """WSL-safe 3D viewer fallback using the system browser."""

    screenshot_taken = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._receptor_path = ""
        self._ligand_path = ""
        self._bonds: list = []
        self._box: dict | None = None
        self._auto_open = True
        self._status = None
        self._browser_dir = self._resolve_browser_dir()
        self._html_path = self._browser_dir / "ladock_wsl_viewer.html"
        self._build_ui()

    def set_auto_open(self, value: bool):
        """When False, the view is written silently and only opened on demand
        (used by the live docking-parameter preview to avoid popup spam)."""
        self._auto_open = bool(value)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        if _IS_WSL:
            detail = ("The embedded 3D viewer can't run under WSL (WebGL is "
                      "unavailable there). Open it in your Windows browser:")
        else:
            detail = "Qt WebEngine is unavailable; the 3D view opens in your system browser."
        msg = QLabel(detail)
        msg.setAlignment(Qt.AlignCenter)
        msg.setWordWrap(True)
        msg.setStyleSheet("color:#cdd6f4;font-size:11px;")
        root.addWidget(msg)

        btn = QPushButton("Open / refresh 3D view in browser")
        btn.setFixedHeight(32)
        btn.setStyleSheet(
            "QPushButton{background:#89b4fa;color:#1e1e2e;border:none;"
            "border-radius:6px;font-weight:bold;padding:6px 10px;font-size:11px;}"
            "QPushButton:hover{background:#b4d0fa;}"
        )
        btn.clicked.connect(self.open_in_browser)
        root.addWidget(btn)

        # Inline text summary so there is still useful feedback without WebGL.
        self._status = QLabel("No structure loaded.")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            "color:#7d8590;background:#11151b;border:1px solid #22272e;"
            "border-radius:6px;padding:10px;font-size:11px;font-family:monospace;")
        root.addWidget(self._status)
        root.addStretch()

    def _refresh_status(self):
        if self._status is None:
            return
        lines = []
        lines.append("Protein: " + (os.path.basename(self._receptor_path)
                                     if self._receptor_path else "—"))
        lines.append("Ligand: " + (os.path.basename(self._ligand_path)
                                    if self._ligand_path else "—"))
        if self._box:
            b = self._box
            lines.append(f"Grid center: {b['cx']:.2f}, {b['cy']:.2f}, {b['cz']:.2f}")
            lines.append(f"Grid size:   {b['sx']:.0f} × {b['sy']:.0f} × {b['sz']:.0f} Å")
        else:
            lines.append("Grid box: not set")
        self._status.setText("\n".join(lines))

    def load_receptor(self, path: str):
        self._receptor_path = path
        self._write_html()
        self._refresh_status()

    def load_ligand(self, path: str):
        self._ligand_path = path
        self._write_html()
        self._refresh_status()
        if self._auto_open:
            self.open_in_browser()

    def load_pose(self, pdbqt_data: str):
        pose_path = Path(tempfile.gettempdir()) / "ladock_wsl_pose.pdbqt"
        pose_path.write_text(pdbqt_data, encoding="utf-8")
        self._ligand_path = str(pose_path)
        self._write_html()
        if self._auto_open:
            self.open_in_browser()

    def highlight_pocket(self, cx: float, cy: float, cz: float,
                         sx: float, sy: float, sz: float):
        self._box = {"cx": cx, "cy": cy, "cz": cz, "sx": sx, "sy": sy, "sz": sz}
        self._write_html()
        self._refresh_status()

    def show_hbonds(self, bonds: list):
        self._bonds = bonds or []
        self._write_html()

    def clear_bonds(self):
        self._bonds = []
        self._write_html()

    def show_bonds(self, bonds: list):
        self.show_hbonds(bonds)

    def set_interaction_view(self, bonds: list):
        self._bonds = bonds or []
        self._write_html()
        self.open_in_browser()

    def clear(self):
        self._receptor_path = ""
        self._ligand_path = ""
        self._bonds = []
        self._box = None
        self._write_html()
        self._refresh_status()

    def _write_html(self, box: dict | None = None):
        receptor_data = self._read_text(self._receptor_path)
        ligand_data = self._read_text(self._ligand_path)
        # Inline the 3Dmol library so the page is fully self-contained. A plain
        # Windows browser blocks <script src="file://…"> subresources, which
        # would otherwise leave the page blank.
        try:
            js_content = _VIEWER_JS.read_text(encoding="utf-8", errors="replace")
        except Exception:
            js_content = ""
        payload = {
            "receptor": receptor_data,
            "ligand": ligand_data,
            "recFmt": _mol_format(self._receptor_path) if self._receptor_path else "pdb",
            "ligFmt": _mol_format(self._ligand_path) if self._ligand_path else "pdb",
            "bonds": self._bonds,
            "box": box if box is not None else self._box,
        }
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>LADOCK WSL 3D Viewer</title>
<style>
html, body, #viewport {{ margin:0; width:100%; height:100%; background:#0f1117; overflow:hidden; }}
#status {{
  position:fixed; left:0; right:0; bottom:0; height:26px; line-height:26px;
  padding:0 10px; background:rgba(24,24,37,0.92); color:#a6adc8;
  font:12px monospace; border-top:1px solid #313244;
}}
</style>
</head>
<body>
<div id="viewport"></div>
<div id="status">Loading 3D viewer…</div>
<!--LADOCK_JS_PLACEHOLDER-->
<script>
const payload = {json.dumps(payload)};
const viewer = $3Dmol.createViewer(document.getElementById('viewport'), {{
  backgroundColor: '#0f1117',
  antialias: true,
  defaultcolors: $3Dmol.elementColors.rasmol
}});
function setStatus(text) {{
  document.getElementById('status').textContent = text;
}}
if (payload.receptor) {{
  viewer.addModel(payload.receptor, payload.recFmt || 'pdb');
  viewer.setStyle({{hetflag:false}}, {{cartoon:{{color:'spectrum', opacity:0.85}}}});
  viewer.setStyle({{hetflag:true}}, {{stick:{{radius:0.16, colorscheme:'Jmol'}}, sphere:{{radius:0.22, colorscheme:'Jmol'}}}});
}}
if (payload.ligand) {{
  viewer.addModel(payload.ligand, payload.ligFmt || 'pdb');
  const models = viewer.getModels();
  const ligModel = models[models.length - 1];
  if (ligModel) {{
    ligModel.setStyle({{}}, {{
      stick: {{radius:0.22, colorscheme:'orangeCarbon'}},
      sphere: {{radius:0.30, colorscheme:'orangeCarbon'}}
    }});
  }}
}}
if (payload.box) {{
  var _b = payload.box, _hx=_b.sx/2, _hy=_b.sy/2, _hz=_b.sz/2, _c=[];
  for (var _i=0; _i<8; _i++) {{
    _c.push({{x:_b.cx+((_i&1)?_hx:-_hx), y:_b.cy+((_i&2)?_hy:-_hy), z:_b.cz+((_i&4)?_hz:-_hz)}});
  }}
  [[0,1],[2,3],[4,5],[6,7],[0,2],[1,3],[4,6],[5,7],[0,4],[1,5],[2,6],[3,7]].forEach(function(e){{
    viewer.addCylinder({{start:_c[e[0]], end:_c[e[1]], radius:0.25, color:'#ffd23f', fromCap:2, toCap:2}});
  }});
  viewer.addBox({{
    center: {{x:_b.cx,y:_b.cy,z:_b.cz}}, dimensions: {{w:_b.sx,h:_b.sy,d:_b.sz}},
    color:'#ffd23f', opacity:0.10
  }});
}}
for (const b of (payload.bonds || [])) {{
  viewer.addCylinder({{
    start: b.start, end: b.end, radius: 0.06,
    color: b.color || '#44ff88',
    dashed: true, fromCap: 1, toCap: 1
  }});
}}
viewer.zoomTo();
viewer.render();
setStatus('3D view ready in browser.');
</script>
</body>
</html>
"""
        html = html.replace("<!--LADOCK_JS_PLACEHOLDER-->",
                             "<script>" + js_content + "</script>")
        self._browser_dir.mkdir(parents=True, exist_ok=True)
        self._html_path.write_text(html, encoding="utf-8")

    def open_in_browser(self):
        self._write_html()
        win_path = self._to_windows_path(self._html_path)

        # On WSL the HTML lives on a Windows-visible drive (/mnt/…), so it must be
        # opened by a WINDOWS launcher using the Windows path. xdg-open must NOT
        # be used here: it treats "D:\…" / "file:///D:/…" as a Linux path and fails.
        if _IS_WSL and win_path:
            for cmd in (
                ["cmd.exe", "/C", "start", "", win_path],
                ["powershell.exe", "-NoProfile", "-Command",
                 "Start-Process", "-FilePath", win_path],
                ["explorer.exe", win_path],
            ):
                try:
                    subprocess.run(
                        cmd, cwd="/mnt/c",
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=20)
                    return
                except Exception:
                    continue
            return

        # Native Linux / other: a normal local file URL works with xdg-open.
        browser_url = self._to_browser_file_url(self._html_path)
        if QDesktopServices.openUrl(QUrl(browser_url)):
            return
        for cmd in (["xdg-open", browser_url], ["wslview", browser_url]):
            try:
                subprocess.Popen(cmd)
                return
            except Exception:
                continue

    @staticmethod
    def _read_text(path: str) -> str:
        if not path or not os.path.isfile(path):
            return ""
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def _resolve_browser_dir(self) -> Path:
        project_root = Path(__file__).resolve().parents[2]
        mounted_root = self._to_windows_path(project_root)
        if mounted_root:
            cache_dir = project_root / ".ladock_wsl_viewer"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir
        temp_root = Path(tempfile.gettempdir()) / "ladock_wsl_viewer"
        temp_root.mkdir(parents=True, exist_ok=True)
        return temp_root

    def _ensure_browser_assets(self) -> str:
        self._browser_dir.mkdir(parents=True, exist_ok=True)
        js_copy = self._browser_dir / "3Dmol-min.js"
        try:
            src_mtime = _VIEWER_JS.stat().st_mtime
            dst_mtime = js_copy.stat().st_mtime if js_copy.exists() else -1
            if dst_mtime < src_mtime:
                shutil.copyfile(_VIEWER_JS, js_copy)
        except Exception:
            if not js_copy.exists():
                shutil.copyfile(_VIEWER_JS, js_copy)
        return self._to_browser_file_url(js_copy)

    @staticmethod
    def _to_windows_path(path: Path | str) -> str | None:
        raw = str(path).replace("\\", "/")
        if raw.startswith("/mnt/") and len(raw) > 6:
            drive = raw[5]
            if raw[6:7] == "/":
                tail = raw[7:].replace("/", "\\")
                return f"{drive.upper()}:\\{tail}"
        return None

    @classmethod
    def _to_browser_file_url(cls, path: Path | str) -> str:
        win_path = cls._to_windows_path(path)
        if win_path:
            return "file:///" + quote(win_path.replace("\\", "/"), safe=":/-._~")
        return Path(path).resolve().as_uri()


# ---------------------------------------------------------------------------
# Molecular Viewer Panel
# ---------------------------------------------------------------------------
class MolecularViewerPanel(QWidget):
    """
    Full 3-D viewer panel.  Embeds a QWebEngineView loading gui/assets/viewer.html.
    All 3Dmol.js calls are made via view.page().runJavaScript().
    """

    # Emitted after screenshot is captured: (png_data_uri: str)
    screenshot_taken = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._external = None
        if _USE_BROWSER_FALLBACK:
            self._external = _ExternalBrowserViewerPanel(self)
            self._external.screenshot_taken.connect(self.screenshot_taken)
        self._receptor_path: str = ""
        self._ligand_path:   str = ""
        self._ready: bool = False   # True once 3Dmol.js has initialised
        self._pending_calls: list[str] = []  # JS calls queued before ready
        self._shot_timer = None
        self._view = None
        self._status_label = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        if self._external is not None:
            root.addWidget(self._external)
            return

        if QWebEngineView is None:
            # _USE_BROWSER_FALLBACK already activates the external panel above;
            # this branch is unreachable in normal operation but kept as safety net.
            reason = (
                "Interactive 3D viewer could not be initialized.\n\n"
                f"{_WEBENGINE_IMPORT_ERROR or 'Qt WebEngine is not available.'}"
            )
            self._status_label = QLabel(reason)
            self._status_label.setAlignment(Qt.AlignCenter)
            self._status_label.setWordWrap(True)
            self._status_label.setStyleSheet(
                "color:#a6adc8;background:#11111b;padding:18px;font-size:12px;"
            )
            root.addWidget(self._status_label)
            return

        # 3D view (full area, no header bar)
        self._view = QWebEngineView()
        page = _SilentPage(self._view)
        self._view.setPage(page)
        # Paint the page/widget with the dark theme colour so switching files
        # (which relayouts this view) never flashes the default white background.
        page.setBackgroundColor(QColor(_VIEWER_BG))
        self._view.setStyleSheet(f"background:{_VIEWER_BG};")
        self._view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        html_url = QUrl.fromLocalFile(str(_VIEWER_HTML.resolve()))
        self._view.load(html_url)
        self._view.loadFinished.connect(self._on_load_finished)
        root.addWidget(self._view)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def set_auto_open(self, value: bool):
        """For the browser fallback: control whether loading a ligand auto-opens
        the browser. No-op for the embedded viewer."""
        if self._external is not None:
            self._external.set_auto_open(value)

    def load_receptor(self, path: str):
        """Load a receptor file into the viewer (auto-detects format)."""
        if self._external is not None:
            self._external.load_receptor(path)
            return
        if not os.path.isfile(path):
            self._js(f"setStatus('❌ Receptor not found: {os.path.basename(path)}')")
            return
        self._receptor_path = path
        data = Path(path).read_text(encoding="utf-8", errors="replace")
        fmt = _mol_format(path)
        self._js(f"loadReceptor({_js_str(data)}, '{fmt}')")

    def load_ligand(self, path: str):
        """Load a ligand / docked-pose file into the viewer (auto-detects format).

        For multi-pose PDBQT output (Vina/AD4) only the first MODEL is sent to
        the viewer so that the displayed pose matches the interaction analysis.
        """
        if self._external is not None:
            self._external.load_ligand(path)
            return
        if not os.path.isfile(path):
            self._js(f"setStatus('❌ Ligand not found: {os.path.basename(path)}')")
            return
        self._ligand_path = path
        data = Path(path).read_text(encoding="utf-8", errors="replace")
        fmt = _mol_format(path)
        # Keep only the first MODEL block (or all atoms if no MODEL records)
        if fmt in ("pdbqt", "pdb") and "ENDMDL" in data:
            first_pose_lines = []
            for line in data.splitlines(keepends=True):
                tag = line[:6].strip()
                if tag == "ENDMDL":
                    first_pose_lines.append(line)
                    break
                first_pose_lines.append(line)
            data = "".join(first_pose_lines)
        self._js(f"loadLigand({_js_str(data)}, '{fmt}')")

    def load_pose(self, pdbqt_data: str):
        """Load a raw PDBQT string as the ligand/pose."""
        if self._external is not None:
            self._external.load_pose(pdbqt_data)
            return
        self._js(f"loadLigand({_js_str(pdbqt_data)})")

    def highlight_pocket(self, cx: float, cy: float, cz: float,
                         sx: float, sy: float, sz: float):
        """Draw a wireframe box around the binding pocket."""
        if self._external is not None:
            self._external.highlight_pocket(cx, cy, cz, sx, sy, sz)
            return
        self._js(f"highlightPocket({cx},{cy},{cz},{sx},{sy},{sz})")

    def show_hbonds(self, bonds: list):
        if self._external is not None:
            self._external.show_hbonds(bonds)
            return
        self._js(f"showHBonds({json.dumps(bonds)})")

    def clear_bonds(self):
        """Remove all bond overlays (cylinders) from the scene."""
        if self._external is not None:
            self._external.clear_bonds()
            return
        self._js("clearBonds()")

    def show_bonds(self, bonds: list):
        """Show typed interaction bonds. bonds: [{start,end,itype,color}]"""
        if self._external is not None:
            self._external.show_bonds(bonds)
            return
        self._js(f"showHBonds({json.dumps(bonds)})")

    def set_interaction_view(self, bonds: list):
        """
        Discovery Studio-style 3D: fade protein, highlight pocket residues as sticks,
        show colored bond cylinders with distance labels, center on ligand.
        bonds: output of AnalysisResult.to_vectors()
        """
        if self._external is not None:
            self._external.set_interaction_view(bonds)
            return
        self._js(f"showInteractionHighlights({json.dumps(bonds)})")

    def clear(self):
        """Remove all molecules from the scene."""
        if self._external is not None:
            self._external.clear()
            return
        self._js("clearAll()")
        self._receptor_path = ""
        self._ligand_path   = ""

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _on_load_finished(self, ok: bool):
        if self._view is None:
            return
        if not ok:
            return
        self._ready = True
        for js in self._pending_calls:
            self._view.page().runJavaScript(js)
        self._pending_calls.clear()
        # Poll for screenshot requests from the JS toolbar button
        self._shot_timer = QTimer(self)
        self._shot_timer.setInterval(300)
        self._shot_timer.timeout.connect(self._check_screenshot_request)
        self._shot_timer.start()

    def _check_screenshot_request(self):
        if self._view is None:
            return
        self._view.page().runJavaScript(
            "var r = window._screenshotRequested; window._screenshotRequested=false; r;",
            lambda v: self._request_screenshot() if v else None
        )

    def _js(self, code: str):
        """Run JS, queuing if page not yet ready."""
        if self._view is None:
            return
        if self._ready:
            self._view.page().runJavaScript(code)
        else:
            self._pending_calls.append(code)

    def _request_screenshot(self, transparent: bool = False):
        """Capture PNG from 3Dmol viewer and save via file dialog."""
        if self._external is not None:
            return
        if self._view is None:
            return
        self._screenshot_transparent = transparent
        if transparent:
            # Temporarily remove background, capture, then restore
            js = """
(function(){
  var orig = viewer.getBackgroundColor ? viewer.getBackgroundColor() : '#0f1117';
  viewer.setBackgroundColor('transparent');
  viewer.render();
  var uri = viewer.pngURI();
  viewer.setBackgroundColor(orig || '#0f1117');
  viewer.render();
  return uri;
})()
"""
        else:
            js = "viewer ? viewer.pngURI() : null"

        def _cb(result):
            if result and isinstance(result, str) and result.startswith("data:image"):
                self._save_screenshot(result, transparent=transparent)
        self._view.page().runJavaScript(js, _cb)

    def _save_screenshot(self, data_uri: str, transparent: bool = False):
        default_name = "ladock_viewer_transparent.png" if transparent else "ladock_viewer.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Screenshot", default_name,
            "PNG Images (*.png)"
        )
        if not path:
            return
        import base64
        _, b64 = data_uri.split(",", 1)
        png_data = base64.b64decode(b64)

        if transparent:
            # Post-process: remove the background colour from the PNG
            # 3Dmol renders on a solid bg even with 'transparent' — use Qt to alpha-cut
            from PySide6.QtGui import QImage
            from PySide6.QtCore import Qt
            img = QImage.fromData(png_data, "PNG")
            if not img.isNull():
                img = img.convertToFormat(QImage.Format_ARGB32)
                # Sample background colour from corner pixel
                bg_color = img.pixel(0, 0)
                bg_r = (bg_color >> 16) & 0xFF
                bg_g = (bg_color >> 8)  & 0xFF
                bg_b =  bg_color        & 0xFF
                tolerance = 30
                for y in range(img.height()):
                    for x in range(img.width()):
                        c = img.pixel(x, y)
                        r = (c >> 16) & 0xFF
                        g = (c >> 8)  & 0xFF
                        b =  c        & 0xFF
                        if (abs(r - bg_r) < tolerance and
                                abs(g - bg_g) < tolerance and
                                abs(b - bg_b) < tolerance):
                            img.setPixel(x, y, 0x00000000)  # fully transparent
                img.save(path, "PNG")
            else:
                with open(path, "wb") as f:
                    f.write(png_data)
        else:
            with open(path, "wb") as f:
                f.write(png_data)

        self._js(f"setStatus('📷 Screenshot saved: {os.path.basename(path)}')")
        self.screenshot_taken.emit(data_uri)


# ---------------------------------------------------------------------------
# Helper: detect 3Dmol format string from file extension
# ---------------------------------------------------------------------------
def _mol_format(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {'.pdb': 'pdb', '.pdbqt': 'pdbqt',
            '.sdf': 'sdf', '.mol': 'sdf', '.mol2': 'mol2'}.get(ext, 'pdb')


# ---------------------------------------------------------------------------
# Helper: safely escape a Python string for embedding in JS
# ---------------------------------------------------------------------------
def _js_str(s: str) -> str:
    """Return a JS string literal (backtick template) for multi-line PDBQT data."""
    # Escape backticks and backslashes; wrap in backtick template literal
    s = s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    return f"`{s}`"
