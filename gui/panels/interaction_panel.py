"""
LADOCK — Interaction Panel (PySide6)

Displays non-covalent interactions as:
  1. Summary badges (count per type)
  2. Sortable interaction table
  3. 2-D interaction diagram (custom QPainter canvas)
  4. Connector to MolecularViewerPanel (show H-bonds as 3-D sticks)

Signals emitted
---------------
hbonds_ready(list)   — list of {start:{x,y,z}, end:{x,y,z}} dicts
"""

from __future__ import annotations

import os
import math
import json
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSplitter,
    QScrollArea, QFrame, QFileDialog, QMessageBox,
    QSizePolicy, QComboBox, QCheckBox, QButtonGroup,
    QStackedWidget
)
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QSize, QPoint, QByteArray, QSettings
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
    QPainterPath, QLinearGradient, QRadialGradient, QPalette,
    QWheelEvent, QMouseEvent, QTransform, QImage
)
from PySide6.QtSvg import QSvgGenerator

from gui.widgets.common import SectionLabel
from gui import theme
from core.wsl_backend import prepare_subprocess, resolve_wsl_python, wsl_available
from engine.interaction_analyzer import (
    AnalysisResult, Interaction, analyze_from_files, analyze_from_strings
)


# ---------------------------------------------------------------------------
# Color map per interaction type
# ---------------------------------------------------------------------------
ITYPE_COLORS: dict[str, str] = {
    "H-Bond":       "#4fc3f7",   # sky blue
    "Hydrophobic":  "#ffb74d",   # amber
    "Pi-Stacking":  "#ce93d8",   # purple
    "Salt Bridge":  "#ef5350",   # red
    "Halogen Bond": "#80cbc4",   # teal
}
ITYPE_ICONS: dict[str, str] = {
    "H-Bond":       "💧",
    "Hydrophobic":  "🟡",
    "Pi-Stacking":  "⬡",
    "Salt Bridge":  "⚡",
    "Halogen Bond": "✕",
}


# ---------------------------------------------------------------------------
# Interaction Diagram (2-D painter widget)
# ---------------------------------------------------------------------------

class _DiagramCanvas(QWidget):
    """
    Internal paint-only widget for InteractionDiagram.
    Supports 3 layout styles:
      STYLE_BIPARTITE  — receptor left column ↔ ligand atom right column (default)
      STYLE_RADIAL     — residues in a ring around a central ligand box
      STYLE_LID        — Schrödinger-style: 2D ligand structure in centre,
                         residue badges floating around it
    """
    STYLE_BIPARTITE = 0
    STYLE_RADIAL    = 1
    STYLE_LID       = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: Optional[AnalysisResult] = None
        self._type_filter: set[str] = {"H-Bond"}
        self._style: int = self.STYLE_BIPARTITE
        self._mol_style: str = "line"   # 'line' | 'ball' | 'stick'
        self._bg_mode: str = "dark"     # 'dark' | 'white' | 'transparent'
        self._ligand_path: Optional[str] = None
        self._ligand_smiles: Optional[str] = None
        self._mol2d_error: str = ""
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(f"background:{theme.BG_CANVAS};")
        self._transparent_render: bool = False

        # ── Zoom & Pan ───────────────────────────────────────────────────
        self._zoom: float = 1.0
        self._pan:  QPointF = QPointF(0.0, 0.0)
        self._drag_start: Optional[QPoint] = None
        self._pan_start:  QPointF = QPointF(0.0, 0.0)

        # ── Node dragging ────────────────────────────────────────────────
        self._node_positions: dict[str, QPointF] = {}  # canvas coords, updated each paint
        self._node_radii:     dict[str, float]   = {}  # for hit-test
        self._node_overrides: dict[str, QPointF] = {}  # user-dragged positions
        self._drag_node:      Optional[str]      = None
        self._drag_offset:    QPointF            = QPointF(0.0, 0.0)

        self.setMouseTracking(True)  # needed for hover cursor
        self._capture_scale: int = 1   # >1 during high-res transparent render

    def reset_view(self):
        """Reset zoom, pan and all manually-moved node positions."""
        self._zoom = 1.0
        self._pan  = QPointF(0.0, 0.0)
        self._node_overrides.clear()
        self.update()

    def render_hires(self, scale: int = 3) -> "QImage":
        """
        Render transparent diagram at *scale*× screen resolution.
        Sets 300 DPI metadata so Word/DOCX treats the image at correct print size.
        """
        from PySide6.QtGui import QImage as _QImage
        W, H = self.width(), self.height()
        img = _QImage(W * scale, H * scale, _QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        # 300 DPI metadata (39.3701 px/in → dots per metre)
        dpm = int(300 * 39.3701)
        img.setDotsPerMeterX(dpm)
        img.setDotsPerMeterY(dpm)

        self._transparent_render = True
        self._capture_scale = scale
        try:
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            p.scale(scale, scale)
            if self._style == self.STYLE_RADIAL:
                self._draw_radial(p)
            elif self._style == self.STYLE_LID:
                self._draw_lid(p)
            else:
                self._draw_bipartite(p)
            p.end()
        finally:
            self._transparent_render = False
            self._capture_scale = 1
        return img

    def render_transparent_cropped(self) -> "QImage":
        """
        Render current diagram at screen size, auto-cropped to content bounding box.
        Works in any bg_mode; transparent mode gives ARGB32 with alpha.
        """
        MARGIN = 12
        W, H = self.width(), self.height()
        img  = self._render_full()
        if self._bg_mode != "transparent":
            return img   # no crop needed for opaque backgrounds
        try:
            import numpy as np
            alpha = self._img_to_alpha(img, W, H)
            rows  = np.any(alpha > 0, axis=1)
            cols  = np.any(alpha > 0, axis=0)
            if not rows.any():
                return img
            y1 = max(0, int(np.argmax(rows))           - MARGIN)
            y2 = min(H, int(H - np.argmax(rows[::-1])) + MARGIN)
            x1 = max(0, int(np.argmax(cols))           - MARGIN)
            x2 = min(W, int(W - np.argmax(cols[::-1])) + MARGIN)
            return img.copy(x1, y1, x2 - x1, y2 - y1)
        except Exception:
            return img

    def render_svg(self, path: str) -> bool:
        """Export diagram as SVG vector, auto-cropped to content."""
        try:
            W, H = self.width(), self.height()

            # Compute crop offset from transparent pre-render
            ox, oy, cw, ch = 0, 0, W, H
            if self._bg_mode == "transparent":
                try:
                    import numpy as np
                    pre   = self._render_full()
                    alpha = self._img_to_alpha(pre, W, H)
                    rows  = np.any(alpha > 0, axis=1)
                    cols  = np.any(alpha > 0, axis=0)
                    MARGIN = 12
                    if rows.any():
                        oy = max(0, int(np.argmax(rows)) - MARGIN)
                        ox = max(0, int(np.argmax(cols)) - MARGIN)
                        y2 = min(H, int(H - np.argmax(rows[::-1])) + MARGIN)
                        x2 = min(W, int(W - np.argmax(cols[::-1])) + MARGIN)
                        cw, ch = x2 - ox, y2 - oy
                except Exception:
                    pass

            gen = QSvgGenerator()
            gen.setFileName(path)
            gen.setSize(QSize(cw, ch))
            gen.setViewBox(QRectF(0, 0, cw, ch))
            gen.setTitle("LADOCK 2D Interaction Diagram")

            p = QPainter(gen)
            p.setRenderHint(QPainter.Antialiasing)
            if ox or oy:
                p.translate(-ox, -oy)
            if self._style == self.STYLE_RADIAL:
                self._draw_radial(p)
            elif self._style == self.STYLE_LID:
                self._draw_lid(p)
            else:
                self._draw_bipartite(p)
            p.end()
            return True
        except Exception:
            return False

    # ── Internal helpers ─────────────────────────────────────────────────────
    def _render_full(self) -> "QImage":
        """Render diagram at screen size using current _bg_mode."""
        from PySide6.QtGui import QImage as _QImage
        W, H = self.width(), self.height()
        fmt = _QImage.Format_ARGB32
        img = _QImage(W, H, fmt)
        img.fill(Qt.transparent if self._bg_mode == "transparent"
                 else (Qt.white if self._bg_mode == "white" else Qt.black))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        if self._style == self.STYLE_RADIAL:
            self._draw_radial(p)
        elif self._style == self.STYLE_LID:
            self._draw_lid(p)
        else:
            self._draw_bipartite(p)
        p.end()
        return img

    @staticmethod
    def _img_to_alpha(img, W, H):
        import ctypes, numpy as np
        ptr  = img.bits()
        buf  = (ctypes.c_uint8 * (W * H * 4)).from_address(int(ptr))
        arr  = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 4).copy()
        return arr[:, :, 3]


    def _fg_text(self) -> QColor:
        tr = self._bg_mode in ("transparent", "white")
        return QColor("#000000") if tr else QColor(theme.TEXT)

    def _fg_dim(self) -> QColor:
        tr = self._bg_mode in ("transparent", "white")
        return QColor("#000000") if tr else QColor(theme.TEXT_DIM)

    def _badge_bg(self) -> QColor:
        tr = self._bg_mode in ("transparent", "white")
        return QColor(0, 0, 0, 0) if tr else QColor(24, 24, 37, 210)

    def _node_fill(self, rim: QColor) -> QColor:
        tr = self._bg_mode in ("transparent", "white")
        if tr:
            return QColor(0, 0, 0, 0)
        return QColor(rim.red(), rim.green(), rim.blue(), 55)

    def _edge_color(self, itype: str) -> QColor:
        if self._bg_mode in ("transparent", "white"):
            return QColor("#000000")
        return QColor(ITYPE_COLORS.get(itype, "#ffffff"))

    def _pw(self, normal: float) -> float:
        return 0.8 if self._bg_mode in ("transparent", "white") else normal

    def _label_font(self) -> QFont:
        return QFont("Sans Serif", 10) if self._bg_mode in ("transparent", "white") else QFont("Monospace", 10)

    def set_result(self, result: AnalysisResult):
        self._result = result
        self._node_overrides.clear()
        self._node_positions.clear()
        self.update()

    def set_type_filter(self, types: set[str]):
        self._type_filter = types
        self.update()

    def set_style(self, style: int):
        self._style = style
        self._node_overrides.clear()
        self._node_positions.clear()
        self.update()

    def set_mol_style(self, mol_style: str):
        """Set 2D molecule rendering style: 'line', 'ball', 'stick'."""
        self._mol_style = mol_style
        self.update()

    def set_bg_mode(self, mode: str):
        """Set background mode: 'dark' | 'white' | 'transparent'."""
        self._bg_mode = mode
        bg = "transparent" if mode == "transparent" else (theme.BG_CANVAS if mode == "dark" else "#ffffff")
        self.setStyleSheet(f"background:{bg};")
        self.update()

    def set_ligand_path(self, path: Optional[str]):
        self._ligand_path = path
        self._mol2d_error = ""
        self.update()

    def set_ligand_smiles(self, smiles: Optional[str]):
        self._ligand_smiles = (smiles or "").strip() or None
        self._mol2d_error = ""
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._zoom != 1.0 or self._pan != QPointF(0, 0):
            t = QTransform()
            t.translate(self._pan.x(), self._pan.y())
            t.scale(self._zoom, self._zoom)
            p.setTransform(t)
        if self._style == self.STYLE_RADIAL:
            self._draw_radial(p)
        elif self._style == self.STYLE_LID:
            self._draw_lid(p)
        else:
            self._draw_bipartite(p)
        p.end()

    # ── Coordinate helpers ───────────────────────────────────────────────────
    def _to_canvas(self, pos: QPoint) -> QPointF:
        """Convert widget pixel position to canvas (drawing) coordinate."""
        return QPointF(
            (pos.x() - self._pan.x()) / self._zoom,
            (pos.y() - self._pan.y()) / self._zoom,
        )

    def _hit_node(self, canvas_pos: QPointF) -> Optional[str]:
        """Return key of node under canvas_pos, or None."""
        for key, npos in self._node_positions.items():
            r = self._node_radii.get(key, 20)
            dx = canvas_pos.x() - npos.x()
            dy = canvas_pos.y() - npos.y()
            if dx * dx + dy * dy <= r * r:
                return key
        return None

    # ── Mouse events ─────────────────────────────────────────────────────────
    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        factor = 1.12 if delta > 0 else (1.0 / 1.12)
        new_zoom = max(0.25, min(6.0, self._zoom * factor))
        pos = event.position() if hasattr(event, 'position') else QPointF(event.pos())
        self._pan = QPointF(
            pos.x() - new_zoom / self._zoom * (pos.x() - self._pan.x()),
            pos.y() - new_zoom / self._zoom * (pos.y() - self._pan.y()),
        )
        self._zoom = new_zoom
        self.update()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            cp = self._to_canvas(event.pos())
            hit = self._hit_node(cp)
            if hit:
                # Drag a residue node
                self._drag_node   = hit
                npos = self._node_positions[hit]
                self._drag_offset = QPointF(npos.x() - cp.x(), npos.y() - cp.y())
                self.setCursor(Qt.SizeAllCursor)
            else:
                # Pan the whole canvas
                self._drag_start = event.pos()
                self._pan_start  = QPointF(self._pan)
                self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_node:
            cp = self._to_canvas(event.pos())
            self._node_overrides[self._drag_node] = QPointF(
                cp.x() + self._drag_offset.x(),
                cp.y() + self._drag_offset.y(),
            )
            self.update()
        elif self._drag_start is not None:
            diff = event.pos() - self._drag_start
            self._pan = self._pan_start + QPointF(diff)
            self.update()
        else:
            # Hover: change cursor when over a node
            cp = self._to_canvas(event.pos())
            if self._hit_node(cp):
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_node  = None
            self._drag_start = None
            self.setCursor(Qt.ArrowCursor)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """Double-click on node resets that node; double-click on empty → reset all."""
        cp  = self._to_canvas(event.pos())
        hit = self._hit_node(cp)
        if hit:
            self._node_overrides.pop(hit, None)
            self.update()
        else:
            self.reset_view()

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #
    def _filtered(self):
        if not self._result:
            return []
        return [i for i in self._result.interactions if i.itype in self._type_filter]

    def _draw_empty(self, p, W, H):
        msg = ("No interactions to display.\nSelect interaction types above."
               if self._result else
               "Load receptor + ligand to see interactions.")
        # Use dark text on white bg styles, light text on dark bg (bipartite)
        text_col = theme.TEXT_MUTED if self._style != self.STYLE_BIPARTITE else theme.TEXT_MUTED
        p.setPen(QColor(text_col))
        p.setFont(QFont("Sans", 11))
        p.drawText(QRectF(0, 0, W, H), Qt.AlignCenter, msg)

    def _collect_rec(self, interactions):
        rec_keys, rec_map = [], {}
        for i in interactions:
            rk = f"{i.rec_atom.resname}{i.rec_atom.resseq}{i.rec_atom.chain}"
            if rk not in rec_map:
                rec_keys.append(rk)
                rec_map[rk] = []
            rec_map[rk].append(i)
        return rec_keys, rec_map

    def _draw_legend(self, p, interactions, W, H, pad_bot=36, pad_left=8,
                     text_color=None):
        """Draw a vertical legend in the bottom-right corner.
        Returns QRectF of the legend area so callers can track it for cropping."""
        if text_color is None:
            text_color = "#000000" if self._bg_mode in ("transparent","white") else theme.TEXT_DIM
        present = {i.itype for i in interactions}
        legend_items = [(t, c) for t, c in ITYPE_COLORS.items() if t in present]
        if not legend_items:
            return QRectF()

        ROW_H  = 18    # height per legend row
        LINE_W = 20    # width of the sample line
        GAP    = 5     # gap between line and text
        PAD    = 6     # outer padding

        p.setFont(QFont("Sans", 8))
        fm = p.fontMetrics()
        max_txt_w = max(fm.horizontalAdvance(f"{ITYPE_ICONS.get(t,'')} {t}") for t, _ in legend_items)
        box_w = LINE_W + GAP + max_txt_w + PAD * 2
        box_h = ROW_H * len(legend_items) + PAD * 2

        # Position: bottom-right with small margin
        bx = W - box_w - 6
        by = H - box_h - 6

        # Background box (invisible in transparent mode)
        if self._bg_mode == "dark":
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(0, 0, 0, 120)))
            p.drawRoundedRect(QRectF(bx, by, box_w, box_h), 5, 5)

        for idx, (itype, color) in enumerate(legend_items):
            row_y  = by + PAD + idx * ROW_H
            line_y = row_y + ROW_H / 2
            col    = QColor("#000000") if self._bg_mode in ("transparent","white") else QColor(color)
            pen    = QPen(col, 2)
            pen.setStyle(Qt.DashLine if itype in ("H-Bond", "Halogen Bond")
                         else Qt.SolidLine)
            p.setPen(pen)
            p.drawLine(QPointF(bx + PAD, line_y),
                       QPointF(bx + PAD + LINE_W, line_y))
            p.setPen(QColor(text_color))
            icon = ITYPE_ICONS.get(itype, "")
            p.drawText(QRectF(bx + PAD + LINE_W + GAP, row_y, max_txt_w, ROW_H),
                       Qt.AlignLeft | Qt.AlignVCenter, f"{icon} {itype}")

        return QRectF(bx, by, box_w, box_h)

    # ------------------------------------------------------------------ #
    # Style 0 — Bipartite (receptor left | ligand atoms right)
    # ------------------------------------------------------------------ #
    def _draw_bipartite(self, p: QPainter):
        W, H = self.width(), self.height()
        if self._bg_mode == "white":
            p.fillRect(0, 0, W, H, QColor("#ffffff"))
        elif self._bg_mode == "dark":
            p.fillRect(0, 0, W, H, QColor(theme.BG_CANVAS))
        interactions = self._filtered()
        if not interactions:
            self._draw_empty(p, W, H); return

        rec_keys, rec_map = self._collect_rec(interactions)
        lig_keys: list[str] = []
        lig_map: dict[str, list] = {}
        for i in interactions:
            lk = i.lig_atom.name
            if lk not in lig_map:
                lig_keys.append(lk)
                lig_map[lk] = []
            lig_map[lk].append(i)

        PAD_TOP, PAD_BOT, PAD_LEFT, PAD_RIGHT = 30, 36, 8, 8
        NODE_R = 18
        COL_W  = 90
        MID_X  = W / 2.0
        left_x  = PAD_LEFT + COL_W / 2
        right_x = W - PAD_RIGHT - COL_W / 2
        usable_h = H - PAD_TOP - PAD_BOT

        def _ny(idx, count):
            if count == 1: return PAD_TOP + usable_h / 2
            step = usable_h / count
            return PAD_TOP + step * idx + step / 2

        p.setFont(QFont("Sans", 10, QFont.Bold))
        p.setPen(self._fg_text() if self._bg_mode in ("transparent","white") else QColor(theme.ACCENT))
        p.drawText(QRectF(PAD_LEFT, 4, COL_W, 22),
                   Qt.AlignHCenter | Qt.AlignVCenter, "Receptor")
        p.drawText(QRectF(W - PAD_RIGHT - COL_W, 4, COL_W, 22),
                   Qt.AlignHCenter | Qt.AlignVCenter, "Ligand")

        rec_pos = {rk: (left_x, _ny(idx, len(rec_keys)))
                   for idx, rk in enumerate(rec_keys)}
        lig_pos = {lk: (right_x, _ny(idx, len(lig_keys)))
                   for idx, lk in enumerate(lig_keys)}

        # Apply user-dragged overrides
        for k in list(rec_pos):
            if k in self._node_overrides:
                ov = self._node_overrides[k]; rec_pos[k] = (ov.x(), ov.y())
        for k in list(lig_pos):
            if k in self._node_overrides:
                ov = self._node_overrides[k]; lig_pos[k] = (ov.x(), ov.y())
        # Cache positions for hit-testing
        self._node_positions = {k: QPointF(x, y) for k, (x, y) in {**rec_pos, **lig_pos}.items()}
        self._node_radii     = {k: float(NODE_R)  for k in self._node_positions}

        # Edges
        for i in interactions:
            rk = f"{i.rec_atom.resname}{i.rec_atom.resseq}{i.rec_atom.chain}"
            lk = i.lig_atom.name
            rx, ry = rec_pos[rk];  lx, ly = lig_pos[lk]
            color = self._edge_color(i.itype)
            x1, y1 = rx + NODE_R, ry
            x4, y4 = lx - NODE_R, ly
            path = QPainterPath()
            path.moveTo(x1, y1)
            path.cubicTo(MID_X, y1, MID_X, y4, x4, y4)
            pen = QPen(color, self._pw(1.8))
            pen.setStyle(Qt.DashLine if i.itype in ("H-Bond", "Halogen Bond") else Qt.SolidLine)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawPath(path)
            # Distance badge at bezier midpoint
            t = 0.5
            bx = ((1-t)**3*x1 + 3*(1-t)**2*t*MID_X + 3*(1-t)*t**2*MID_X + t**3*x4)
            by = ((1-t)**3*y1 + 3*(1-t)**2*t*y1    + 3*(1-t)*t**2*y4    + t**3*y4)
            dist_str = f"{i.distance:.1f}Å"
            p.setFont(self._label_font())
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(dist_str) + 4
            badge = QRectF(bx - tw/2, by - 7, tw, 13)
            p.setPen(Qt.NoPen); p.setBrush(QBrush(self._badge_bg()))
            p.drawRoundedRect(badge, 3, 3)
            p.setPen(self._fg_text()); p.drawText(badge, Qt.AlignCenter, dist_str)

        def _draw_node(cx, cy, label, fill, rim):
            if self._bg_mode in ("transparent", "white"):
                p.setPen(QPen(QColor("#000000"), self._pw(2)))
                p.setBrush(Qt.NoBrush)
            else:
                grad = QRadialGradient(cx, cy, NODE_R)
                grad.setColorAt(0, fill); grad.setColorAt(1, QColor(13, 17, 23, 240))
                p.setPen(QPen(rim, 2)); p.setBrush(QBrush(grad))
            p.drawEllipse(QPointF(cx, cy), NODE_R, NODE_R)
            p.setPen(self._fg_text()); p.setFont(QFont("Sans", 8, QFont.Bold))
            p.drawText(QRectF(cx-NODE_R-2, cy-9, (NODE_R+2)*2, 18), Qt.AlignCenter, label[:9])

        for rk, (rx, ry) in rec_pos.items():
            rim  = QColor(ITYPE_COLORS.get(rec_map[rk][0].itype, theme.ACCENT))
            fill = self._node_fill(rim)
            _draw_node(rx, ry, rk[:9], fill, rim)

        for lk, (lx, ly) in lig_pos.items():
            rim  = QColor(ITYPE_COLORS.get(lig_map[lk][0].itype, "#fab387"))
            fill = self._node_fill(rim)
            _draw_node(lx, ly, lk[:9], fill, rim)

        self._draw_legend(p, interactions, W, H)

    # ------------------------------------------------------------------ #
    # Style 1 — Radial (residues in ring, ligand box at centre)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _spread_angles(nat_angles: list[tuple], min_sep_rad: float) -> dict:
        """
        Given [(key, angle_rad), ...] sorted by angle, push adjacent angles
        apart until minimum angular separation (radians) is satisfied.
        Returns {key: final_angle_rad}.
        """
        n = len(nat_angles)
        if n == 0:
            return {}
        total = 2 * math.pi
        if min_sep_rad * n > total:
            min_sep_rad = total / n * 0.95
        keys   = [k for k, _ in nat_angles]
        angles = [a for _, a in nat_angles]
        for _ in range(600):
            moved = False
            for i in range(n):
                j = (i + 1) % n
                diff = (angles[j] - angles[i]) % total
                if diff < min_sep_rad:
                    push = (min_sep_rad - diff) / 2
                    angles[i] = (angles[i] - push) % total
                    angles[j] = (angles[j] + push) % total
                    moved = True
            if not moved:
                break
        return {keys[i]: angles[i] for i in range(n)}

    @staticmethod
    def _optimal_ring_r(mol_w: float, mol_h: float, n: int, item_diam: float,
                        W: float, H: float, margin: float = 16.0) -> float:
        """
        Compute ring radius so badges:
          1. Clear the central molecule bounding ellipse
          2. Don't physically overlap each other around the ring
          3. Fit within the canvas
        """
        if n == 0:
            return min(W, H) * 0.38
        # Half-diagonal of molecule bounding box
        mol_r = math.hypot(mol_w / 2, mol_h / 2)
        # Must clear the molecule
        r_from_mol = mol_r + item_diam / 2 + margin
        # Circumference must fit all items
        r_from_size = n * (item_diam + margin) / (2 * math.pi)
        ring_r = max(r_from_mol, r_from_size, min(W, H) * 0.30)
        # Never clip beyond canvas edges (account for item radius)
        ring_r_max = min(W / 2, H / 2) - item_diam / 2 - 4
        return min(ring_r, ring_r_max)

    def _draw_radial(self, p: QPainter):
        W, H = self.width(), self.height()
        if self._bg_mode == "white":
            p.fillRect(0, 0, W, H, QColor("#ffffff"))
        elif self._bg_mode == "dark":
            p.fillRect(0, 0, W, H, QColor(theme.BG_CANVAS))
        interactions = self._filtered()
        if not interactions:
            self._draw_empty(p, W, H); return

        rec_keys, res_map = self._collect_rec(interactions)
        n = len(rec_keys)
        cx, cy = W / 2.0, H / 2.0

        # Molecule region (centre) — use canvas dimensions proportionally so
        # the molecule fills ~55% of width or ~85% of height, whichever fits.
        mol_w = min(W * 0.55, H * 0.85)
        mol_h = mol_w

        # ── Node size: fit the longest residue label ─────────────────────
        # Try font sizes from 9 down to 6 until circle radius is acceptable
        font_size = 9
        node_r    = 28.0
        for fs in range(9, 5, -1):
            fm_test = QFontMetrics(QFont("Sans", fs, QFont.Bold))
            longest = max((fm_test.horizontalAdvance(k[:9]) for k in rec_keys), default=0)
            # node_r must contain the text: r >= half_diagonal of text rect
            th = fm_test.height()
            r_needed = math.hypot(longest / 2 + 3, th / 2 + 3)
            if r_needed <= 40.0:
                font_size = fs
                node_r    = max(r_needed, 18.0)
                break
        else:
            node_r = 28.0; font_size = 7

        node_font = QFont("Sans", font_size, QFont.Bold)
        item_diam = node_r * 2 + 6

        ring_r = self._optimal_ring_r(mol_w, mol_h, n, item_diam, W, H, margin=18)
        # Min angular gap = physical item size at ring_r
        min_sep_rad = (item_diam + 6) / ring_r

        # ── Draw 2D molecule (dark bg) ──────────────────────────────────
        lig_atom_pos: dict[str, tuple[float, float]] = {}
        mol_ok = self._draw_mol_2d(p, cx, cy, mol_w, mol_h, lig_atom_pos,
                                   white_bg=(self._bg_mode in ("white", "transparent")))

        if not mol_ok:
            LIG_W, LIG_H = mol_w * 0.7, mol_h * 0.35
            lig_rect = QRectF(cx-LIG_W/2, cy-LIG_H/2, LIG_W, LIG_H)
            is_light = self._bg_mode in ("white", "transparent")
            p.setPen(QPen(QColor("#000000" if is_light else theme.BORDER), 1.5))
            p.setBrush(Qt.NoBrush if is_light else QBrush(QColor(theme.BG_MUTED)))
            p.drawRoundedRect(lig_rect, 8, 8)
            p.drawRoundedRect(lig_rect, 8, 8)
            p.setPen(self._fg_text()); p.setFont(QFont("Sans", 10, QFont.Bold))
            p.drawText(lig_rect, Qt.AlignCenter, "2D unavailable")
            if self._mol2d_error:
                msg_rect = lig_rect.adjusted(8, 26, -8, -8)
                p.setPen(self._fg_dim())
                p.setFont(QFont("Sans", 8))
                p.drawText(msg_rect, Qt.AlignHCenter | Qt.TextWordWrap, self._mol2d_error)

        # ── Compute natural angle per residue = direction to atom centroid ──
        nat: list[tuple] = []
        for key in rec_keys:
            pts = [lig_atom_pos.get(i.lig_atom.name, (cx, cy)) for i in res_map[key]]
            ax = sum(x for x, _ in pts) / len(pts)
            ay = sum(y for _, y in pts) / len(pts)
            ang = math.atan2(ay - cy, ax - cx)
            nat.append((key, ang % (2 * math.pi)))
        nat.sort(key=lambda t: t[1])

        spread = self._spread_angles(nat, min_sep_rad=min_sep_rad)

        def _res_pos(key):
            if key in self._node_overrides:
                ov = self._node_overrides[key]; return ov.x(), ov.y()
            a = spread[key]
            return cx + ring_r * math.cos(a), cy + ring_r * math.sin(a)

        # Cache for hit-testing
        self._node_positions = {k: QPointF(*_res_pos(k)) for k in rec_keys}
        self._node_radii     = {k: node_r for k in rec_keys}

        # ── Edges residue node → ligand atom ────────────────────────────
        for key in rec_keys:
            rx, ry = _res_pos(key)
            dx, dy = cx - rx, cy - ry
            length = math.hypot(dx, dy) or 1.0
            ux, uy = dx/length, dy/length
            ex1, ey1 = rx + node_r*ux, ry + node_r*uy

            for i in res_map[key]:
                lname = i.lig_atom.name
                ax, ay = lig_atom_pos.get(lname, (cx, cy))
                ddx, ddy = ax - ex1, ay - ey1
                dlen = math.hypot(ddx, ddy) or 1.0
                atom_r = 5 if mol_ok else 0
                ex2 = ax - ddx/dlen * atom_r
                ey2 = ay - ddy/dlen * atom_r

                color = self._edge_color(i.itype)
                pen = QPen(color, self._pw(1.6))
                pen.setStyle(Qt.DashLine if i.itype in ("H-Bond","Halogen Bond")
                             else Qt.SolidLine)
                p.setPen(pen); p.setBrush(Qt.NoBrush)
                ctrl_x = (ex1 + ex2) / 2 + (ey2 - ey1) * 0.07
                ctrl_y = (ey1 + ey2) / 2 - (ex2 - ex1) * 0.07
                path = QPainterPath()
                path.moveTo(ex1, ey1); path.quadTo(ctrl_x, ctrl_y, ex2, ey2)
                p.drawPath(path)

                # Distance label at midpoint
                qx = 0.25*ex1 + 0.5*ctrl_x + 0.25*ex2
                qy = 0.25*ey1 + 0.5*ctrl_y + 0.25*ey2
                dist_str = f"{i.distance:.1f}A"
                p.setFont(self._label_font())
                fm = p.fontMetrics()
                tw = fm.horizontalAdvance(dist_str) + 4
                bg = QRectF(qx-tw/2, qy-7, tw, 13)
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(self._badge_bg()))
                p.drawRoundedRect(bg, 3, 3)
                p.setPen(self._fg_text()); p.drawText(bg, Qt.AlignCenter, dist_str)

        # ── Residue nodes (border coloured by dominant interaction type) ─
        for key in rec_keys:
            rx, ry = _res_pos(key)
            type_counts: dict[str, int] = {}
            for i in res_map[key]:
                type_counts[i.itype] = type_counts.get(i.itype, 0) + 1
            dominant = max(type_counts, key=type_counts.__getitem__)
            rim = QColor(ITYPE_COLORS.get(dominant, theme.ACCENT))
            if self._bg_mode in ("transparent", "white"):
                p.setPen(QPen(QColor("#000000"), self._pw(2)))
                p.setBrush(Qt.NoBrush)
            else:
                p.setPen(QPen(rim, 2))
                p.setBrush(QBrush(QColor(rim.red(), rim.green(), rim.blue(), 50)))
            p.drawEllipse(QPointF(rx, ry), node_r, node_r)
            p.setFont(node_font)
            p.setPen(self._fg_text())
            fm_node = p.fontMetrics()
            th = fm_node.height()
            p.drawText(QRectF(rx - node_r, ry - th / 2, node_r * 2, th),
                       Qt.AlignCenter, key[:9])

        self._draw_legend(p, interactions, W, H)

    # ------------------------------------------------------------------ #
    # Style 2 — LID (Schrödinger-style, 2D ligand structure at centre)
    # ------------------------------------------------------------------ #
    # Residue-type colour palette (like Maestro)
    _RES_COLORS = {
        'GLU': '#e57373', 'ASP': '#e57373',
        'ARG': '#64b5f6', 'LYS': '#64b5f6', 'HIS': '#4dd0e1',
        'GLY': '#aed581',
        'TRP': '#81c784', 'PHE': '#81c784', 'TYR': '#81c784',
        'LEU': '#a5d6a7', 'ILE': '#a5d6a7', 'VAL': '#a5d6a7',
        'ALA': '#c8e6c9', 'MET': '#c8e6c9', 'PRO': '#c8e6c9',
        'SER': '#80deea', 'THR': '#80deea',
        'ASN': '#b39ddb', 'GLN': '#b39ddb',
        'CYS': '#fff176',
    }
    _RES_DEFAULT = '#90a4ae'
    _ELEM_COLORS = {
        'O': '#f38ba8', 'N': '#89b4fa', 'S': '#f9e2af',
        'F': '#a6e3a1', 'Cl': '#a6e3a1', 'Br': '#fab387', 'P': '#fab387',
    }

    def _draw_lid(self, p: QPainter):
        W, H = self.width(), self.height()
        if self._bg_mode == "white":
            p.fillRect(0, 0, W, H, QColor("#ffffff"))
        elif self._bg_mode == "dark":
            p.fillRect(0, 0, W, H, QColor(theme.BG_CANVAS))
        interactions = self._filtered()
        if not interactions:
            self._draw_empty(p, W, H); return

        rec_keys, rec_map = self._collect_rec(interactions)
        n = len(rec_keys)
        MX, MY = W / 2.0, H / 2.0

        BADGE_W, BADGE_H = 62, 26
        item_diam = BADGE_W + 6   # physical width + small gap

        # Molecule region — proportional to canvas, not limited to min(W,H)
        MOL_W = min(W * 0.55, H * 0.85)
        MOL_H = MOL_W

        ring_r = self._optimal_ring_r(MOL_W, MOL_H, n, item_diam, W, H, margin=18)
        min_sep_rad = item_diam / ring_r

        # ── Hydrophobic background blob ─────────────────────────────────
        hydro = [rk for rk in rec_keys if rec_map[rk][0].itype == "Hydrophobic"]
        if hydro and self._bg_mode == "dark":
            path_blob = QPainterPath()
            path_blob.addEllipse(QPointF(MX, MY), MOL_W/2+22, MOL_H/2+22)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(180, 230, 140, 50)))
            p.drawPath(path_blob)

        # ── Draw 2D ligand structure (dark bg) ──────────────────────────
        lig_atom_pos: dict[str, tuple[float, float]] = {}
        mol_ok = self._draw_mol_2d(p, MX, MY, MOL_W, MOL_H, lig_atom_pos,
                                   white_bg=(self._bg_mode in ("white", "transparent")))
        if not mol_ok:
            LIG_W, LIG_H = MOL_W * 0.7, MOL_H * 0.35
            r = QRectF(MX - LIG_W / 2, MY - LIG_H / 2, LIG_W, LIG_H)
            p.setPen(QPen(QColor("#000000" if self._bg_mode in ("transparent", "white")
                                 else theme.BORDER), 1.5))
            p.setBrush(Qt.NoBrush if self._bg_mode in ("transparent","white") else QBrush(QColor(theme.BG_MUTED)))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(self._fg_dim()); p.setFont(QFont("Sans", 10, QFont.Bold))
            p.drawText(r, Qt.AlignCenter, "2D unavailable")
            if self._mol2d_error:
                msg_rect = r.adjusted(8, 26, -8, -8)
                p.setFont(QFont("Sans", 8))
                p.drawText(msg_rect, Qt.AlignHCenter | Qt.TextWordWrap, self._mol2d_error)

        # ── Compute natural angle per residue = direction to atom centroid ──
        nat: list[tuple] = []
        for rk in rec_keys:
            pts = [lig_atom_pos.get(i.lig_atom.name, (MX, MY)) for i in rec_map[rk]]
            ax = sum(x for x, _ in pts) / len(pts)
            ay = sum(y for _, y in pts) / len(pts)
            ang = math.atan2(ay - MY, ax - MX)
            nat.append((rk, ang % (2 * math.pi)))
        nat.sort(key=lambda t: t[1])

        spread = self._spread_angles(nat, min_sep_rad=min_sep_rad)

        badge_pos: dict[str, tuple[float, float]] = {}
        for rk in rec_keys:
            if rk in self._node_overrides:
                ov = self._node_overrides[rk]; badge_pos[rk] = (ov.x(), ov.y())
            else:
                badge_pos[rk] = (MX + ring_r * math.cos(spread[rk]),
                                  MY + ring_r * math.sin(spread[rk]))
        # Cache for hit-testing (use half-diagonal of badge rect as radius)
        _hr = math.hypot(BADGE_W / 2, BADGE_H / 2)
        self._node_positions = {rk: QPointF(bx, by) for rk, (bx, by) in badge_pos.items()}
        self._node_radii     = {rk: _hr for rk in badge_pos}

        # ── Edges badge → ligand atom (ALL interactions per residue) ────
        for rk, (bx, by) in badge_pos.items():
            dx, dy = MX - bx, MY - by
            length = math.hypot(dx, dy) or 1.0
            ux, uy = dx/length, dy/length
            badge_edge_x = bx + BADGE_W/2 * ux
            badge_edge_y = by + BADGE_H/2 * uy

            for i in rec_map[rk]:
                lname = i.lig_atom.name
                ax, ay = lig_atom_pos.get(lname, (MX, MY))
                color = self._edge_color(i.itype)
                pen = QPen(color, self._pw(1.5))
                pen.setStyle(Qt.DashLine if i.itype in ("H-Bond", "Halogen Bond")
                             else Qt.SolidLine)
                p.setPen(pen); p.setBrush(Qt.NoBrush)
                ctrl_x = (badge_edge_x + ax) / 2 + (ay - badge_edge_y) * 0.08
                ctrl_y = (badge_edge_y + ay) / 2 + (badge_edge_x - ax) * 0.08
                path = QPainterPath(); path.moveTo(badge_edge_x, badge_edge_y)
                path.quadTo(ctrl_x, ctrl_y, ax, ay)
                p.drawPath(path)
                # Distance at midpoint
                qx = 0.25*badge_edge_x + 0.5*ctrl_x + 0.25*ax
                qy = 0.25*badge_edge_y + 0.5*ctrl_y + 0.25*ay
                dist_str = f"{i.distance:.1f}A"
                p.setFont(self._label_font())
                fm = p.fontMetrics()
                tw = fm.horizontalAdvance(dist_str) + 4
                dr = QRectF(qx-tw/2, qy-7, tw, 13)
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(self._badge_bg()))
                p.drawRoundedRect(dr, 3, 3)
                p.setPen(self._fg_text()); p.drawText(dr, Qt.AlignCenter, dist_str)

        # ── Residue badges (border coloured by dominant interaction type) ─
        for rk, (bx, by) in badge_pos.items():
            # dominant type = most frequent among filtered interactions for this residue
            type_counts: dict[str, int] = {}
            for i in rec_map[rk]:
                type_counts[i.itype] = type_counts.get(i.itype, 0) + 1
            dominant = max(type_counts, key=type_counts.__getitem__)
            bc = QColor(ITYPE_COLORS.get(dominant, self._RES_DEFAULT))
            if self._bg_mode in ("transparent", "white"):
                p.setPen(QPen(QColor("#000000"), self._pw(2)))
                p.setBrush(Qt.NoBrush)
            else:
                p.setPen(QPen(bc, 2))
                p.setBrush(QBrush(QColor(bc.red(), bc.green(), bc.blue(), 50)))
            r = QRectF(bx-BADGE_W/2, by-BADGE_H/2, BADGE_W, BADGE_H)
            p.drawRoundedRect(r, 8, 8)
            p.setPen(self._fg_text()); p.setFont(QFont("Sans", 8, QFont.Bold))
            p.drawText(r, Qt.AlignCenter, rk[:9])

        self._draw_legend(p, interactions, W, H)

    def _draw_mol_2d(self, p: QPainter, cx: float, cy: float,
                     mol_w: float, mol_h: float,
                     atom_pos_out: dict, white_bg: bool = False) -> bool:
        """
        Draw 2D ligand structure as SVG vector via RDKit MolDraw2DSVG + QSvgRenderer.
        SVG is sharp at any zoom level and in all export formats.
        Fills atom_pos_out with atom_name -> (wx, wy) for edge attachment.
        white_bg=True uses white canvas background (for radial/LID on white).
        """
        self._mol2d_error = ""
        if self._ligand_smiles:
            if self._draw_mol_2d_from_smiles(
                p, cx, cy, mol_w, mol_h, atom_pos_out, white_bg=white_bg
            ):
                return True
            self._mol2d_error = "SMILES depiction failed."
            return False
        if not self._ligand_path or not os.path.isfile(self._ligand_path):
            self._mol2d_error = "No chemistry source available."
            return False
        self._mol2d_error = "No valid 2D chemistry source for this ligand."
        return False

    def _draw_mol_2d_from_smiles(self, p: QPainter, cx: float, cy: float,
                                 mol_w: float, mol_h: float,
                                 atom_pos_out: dict, white_bg: bool = False) -> bool:
        svg_text, anchor_map = self._render_smiles_depiction(
            self._ligand_smiles or "",
            width=max(int(mol_w), 150),
            height=max(int(mol_h), 150),
            white_bg=white_bg,
        )
        if not svg_text:
            return False
        from PySide6.QtSvg import QSvgRenderer

        w_base = max(int(mol_w), 150)
        h_base = max(int(mol_h), 150)
        px_off = cx - w_base / 2.0
        py_off = cy - h_base / 2.0
        renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
        if not renderer.isValid():
            return False
        renderer.render(p, QRectF(px_off, py_off, w_base, h_base))
        if anchor_map:
            for atom_name, coords in anchor_map.items():
                if not isinstance(coords, (list, tuple)) or len(coords) < 2:
                    continue
                atom_pos_out[str(atom_name)] = (px_off + float(coords[0]), py_off + float(coords[1]))
        else:
            self._fill_anchor_positions_from_ligand_geometry(
                atom_pos_out, cx, cy, mol_w, mol_h
            )
        return True

    def _fill_anchor_positions_from_ligand_geometry(self, atom_pos_out: dict,
                                                    cx: float, cy: float,
                                                    mol_w: float, mol_h: float):
        parsed = self._parse_ligand_graph(self._ligand_path)
        if not parsed:
            return
        atoms, _ = parsed
        coords_2d = self._project_atoms_to_2d(atoms)
        if not coords_2d:
            return
        pad = 14.0
        usable_w = max(mol_w - 2 * pad, 20.0)
        usable_h = max(mol_h - 2 * pad, 20.0)
        xs = [pt[0] for pt in coords_2d.values()]
        ys = [pt[1] for pt in coords_2d.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)
        scale = min(usable_w / span_x, usable_h / span_y)
        px_off = cx - mol_w / 2.0 + pad
        py_off = cy - mol_h / 2.0 + pad
        for atom in atoms:
            sx = px_off + (coords_2d[atom["serial"]][0] - min_x) * scale
            sy = py_off + (coords_2d[atom["serial"]][1] - min_y) * scale
            atom_pos_out[atom["name"]] = (sx, sy)

    def _render_smiles_depiction(self, smiles: str, width: int, height: int,
                                 white_bg: bool = False) -> tuple[str, dict[str, tuple[float, float]]]:
        if not smiles:
            return "", {}
        # On Windows: prefer WSL RDKit if available (avoids needing local RDKit install).
        if os.name == "nt" and wsl_available():
            svg_text, anchors = self._render_smiles_depiction_wsl(smiles, width, height, white_bg)
            if svg_text:
                return svg_text, anchors
        # Try local RDKit (works on both Windows and Linux).
        return self._render_smiles_depiction_local(smiles, width, height, white_bg)

    def _render_smiles_depiction_local(self, smiles: str, width: int, height: int,
                                       white_bg: bool = False) -> tuple[str, dict[str, tuple[float, float]]]:
        try:
            from core.render_smiles_svg import render_smiles_depiction

            theme_name = "light" if white_bg else "dark"
            svg_text, anchors = render_smiles_depiction(
                smiles,
                max(width, 120),
                max(height, 120),
                theme_name,
                source_path=self._ligand_path or "",
            )
            return svg_text, {
                str(name): (float(coords[0]), float(coords[1]))
                for name, coords in (anchors or {}).items()
                if isinstance(coords, (list, tuple)) and len(coords) >= 2
            }
        except Exception:
            return "", {}

    def _render_smiles_depiction_wsl(self, smiles: str, width: int, height: int,
                                     white_bg: bool = False) -> tuple[str, dict[str, tuple[float, float]]]:
        if os.name != "nt" or not wsl_available():
            return "", {}
        helper = Path(__file__).resolve().parents[2] / "core" / "render_smiles_svg.py"
        if not helper.is_file():
            return "", {}
        fd, out_path = tempfile.mkstemp(suffix=".svg", prefix="ladock_smiles_")
        os.close(fd)
        anchors_fd, anchors_path = tempfile.mkstemp(suffix=".json", prefix="ladock_smiles_anchors_")
        os.close(anchors_fd)
        try:
            settings = QSettings("LADEEP", "LADOCK")
            wsl_distro = str(settings.value("wsl_distro", "") or "").strip()
            wsl_python = resolve_wsl_python(wsl_distro)
            if not wsl_python:
                return "", {}
            theme = "light" if (white_bg or self._bg_mode in ("white", "transparent")) else "dark"
            cmd = [
                wsl_python,
                str(helper),
                "--smiles",
                smiles,
                "--output",
                out_path,
                "--source",
                self._ligand_path or "",
                "--anchors-output",
                anchors_path,
                "--width",
                str(max(width, 120)),
                "--height",
                str(max(height, 120)),
                "--theme",
                theme,
            ]
            exec_cmd, exec_cwd = prepare_subprocess(
                cmd,
                cwd=str(helper.parent.parent),
                use_wsl_backend=True,
                wsl_distro=wsl_distro,
            )
            result = subprocess.run(
                exec_cmd,
                cwd=exec_cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            if result.returncode != 0 or not os.path.isfile(out_path):
                return "", {}
            svg_text = Path(out_path).read_text(encoding="utf-8", errors="replace")
            anchors: dict[str, tuple[float, float]] = {}
            if os.path.isfile(anchors_path):
                try:
                    payload = json.loads(Path(anchors_path).read_text(encoding="utf-8", errors="replace"))
                    if isinstance(payload, dict):
                        anchors = {
                            str(name): (float(coords[0]), float(coords[1]))
                            for name, coords in payload.items()
                            if isinstance(coords, (list, tuple)) and len(coords) >= 2
                        }
                except Exception:
                    anchors = {}
            return svg_text, anchors
        except Exception:
            return "", {}
        finally:
            for cleanup_path in (out_path, anchors_path):
                if os.path.isfile(cleanup_path):
                    try:
                        os.remove(cleanup_path)
                    except OSError:
                        pass

    def _draw_mol_2d_fallback(self, p: QPainter, cx: float, cy: float,
                              mol_w: float, mol_h: float,
                              atom_pos_out: dict, white_bg: bool = False) -> bool:
        """Pure-text fallback when RDKit is unavailable or cannot parse the ligand."""
        parsed = self._parse_ligand_graph(self._ligand_path)
        if not parsed:
            return False
        atoms, bonds = parsed
        coords_2d = self._project_atoms_to_2d(atoms)
        if not coords_2d:
            return False

        pad = 14.0
        usable_w = max(mol_w - 2 * pad, 20.0)
        usable_h = max(mol_h - 2 * pad, 20.0)
        xs = [pt[0] for pt in coords_2d.values()]
        ys = [pt[1] for pt in coords_2d.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)
        scale = min(usable_w / span_x, usable_h / span_y)
        px_off = cx - mol_w / 2.0 + pad
        py_off = cy - mol_h / 2.0 + pad

        is_light = white_bg or self._bg_mode in ("white", "transparent")
        colors = {
            "C": QColor("#202020") if is_light else QColor("#d9dee7"),
            "N": QColor("#4f9cf3"),
            "O": QColor("#ef5350"),
            "S": QColor("#f9c74f"),
            "P": QColor("#f8961e"),
            "F": QColor("#90be6d"),
            "CL": QColor("#43aa8b"),
            "BR": QColor("#f9844a"),
            "I": QColor("#9c89b8"),
        }
        bond_color = QColor("#202020") if is_light else QColor("#dfe6f2")
        shadow_color = QColor(0, 0, 0, 30 if is_light else 110)
        carbon_fill = QColor("#ffffff") if is_light else QColor("#1e2230")
        carbon_stroke = QColor("#7f8ba3") if is_light else QColor("#96a4bd")

        screen_pos: dict[int, tuple[float, float]] = {}
        for atom in atoms:
            sx = px_off + (coords_2d[atom["serial"]][0] - min_x) * scale
            sy = py_off + (coords_2d[atom["serial"]][1] - min_y) * scale
            screen_pos[atom["serial"]] = (sx, sy)
            atom_pos_out[atom["name"]] = (sx, sy)

        degree: dict[int, int] = {atom["serial"]: 0 for atom in atoms}
        for a1, a2 in bonds:
            degree[a1] = degree.get(a1, 0) + 1
            degree[a2] = degree.get(a2, 0) + 1

        shadow_pen = QPen(shadow_color, 4.2 if self._mol_style == "stick" else 3.2)
        shadow_pen.setCapStyle(Qt.RoundCap)
        shadow_pen.setJoinStyle(Qt.RoundJoin)
        bond_pen = QPen(bond_color, 2.1 if self._mol_style == "stick" else 1.55)
        bond_pen.setCapStyle(Qt.RoundCap)
        bond_pen.setJoinStyle(Qt.RoundJoin)
        for a1, a2 in bonds:
            if a1 not in screen_pos or a2 not in screen_pos:
                continue
            x1, y1 = screen_pos[a1]
            x2, y2 = screen_pos[a2]
            p.setPen(shadow_pen)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            p.setPen(bond_pen)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        atom_radius = 5.2 if self._mol_style == "ball" else 3.6
        font = QFont("Sans", 8, QFont.Bold)
        p.setFont(font)
        for atom in atoms:
            x, y = screen_pos[atom["serial"]]
            element = atom["element"].upper()
            color = colors.get(element, colors.get("C"))
            if element == "C":
                continue
            bubble_w = 18 if len(atom["element"]) == 1 else 24
            bubble_h = 18
            bubble = QRectF(x - bubble_w / 2, y - bubble_h / 2, bubble_w, bubble_h)
            bubble_fill = QColor(color)
            bubble_fill.setAlpha(36 if is_light else 54)
            p.setPen(QPen(color, 1.1))
            p.setBrush(QBrush(bubble_fill))
            p.drawRoundedRect(bubble, 9, 9)
            p.setBrush(Qt.NoBrush)
            p.setPen(color)
            p.drawText(bubble, Qt.AlignCenter, atom["element"])
        return True

    @staticmethod
    def _parse_ligand_graph(path: Optional[str]) -> Optional[tuple[list[dict], list[tuple[int, int]]]]:
        if not path or not os.path.isfile(path):
            return None
        atoms: list[dict] = []
        bonds: list[tuple[int, int]] = []
        serial_map: dict[int, dict] = {}
        seen_bonds: set[tuple[int, int]] = set()
        with open(path, encoding="utf-8", errors="replace") as handle:
            in_first_model = False
            saw_model = False
            for line in handle:
                record = line[:6].strip()
                if record == "MODEL":
                    if saw_model:
                        break
                    saw_model = True
                    in_first_model = True
                    continue
                if record == "ENDMDL" and in_first_model:
                    break
                if record not in ("ATOM", "HETATM", "CONECT"):
                    continue
                if record in ("ATOM", "HETATM"):
                    atom = _DiagramCanvas._parse_pdbqt_atom_line(line)
                    if atom is None:
                        continue
                    atoms.append(atom)
                    serial_map[atom["serial"]] = atom
                    continue
                fields = line.split()
                if len(fields) < 3:
                    continue
                try:
                    src = int(fields[1])
                except ValueError:
                    continue
                for dst_field in fields[2:]:
                    try:
                        dst = int(dst_field)
                    except ValueError:
                        continue
                    pair = tuple(sorted((src, dst)))
                    if src == dst or pair in seen_bonds:
                        continue
                    seen_bonds.add(pair)
                    bonds.append(pair)
        if not atoms:
            return None
        if not bonds:
            bonds = _DiagramCanvas._infer_ligand_bonds(atoms)
        return atoms, bonds

    @staticmethod
    def _parse_pdbqt_atom_line(line: str) -> Optional[dict]:
        try:
            serial = int(line[6:11].strip())
            name = line[12:16].strip() or f"A{serial}"
            x = float(line[30:38].strip())
            y = float(line[38:46].strip())
            z = float(line[46:54].strip())
        except ValueError:
            return None
        raw_type = line[77:].strip() if len(line) > 77 else ""
        raw_element = line[76:78].strip() if len(line) > 76 else ""
        token = (raw_type or raw_element or "".join(ch for ch in name if ch.isalpha())[:2] or "C").strip()
        token_upper = token.upper()
        pdbqt_map = {
            "A": "C",
            "C": "C",
            "N": "N",
            "NA": "N",
            "NS": "N",
            "OA": "O",
            "O": "O",
            "SA": "S",
            "S": "S",
            "P": "P",
            "F": "F",
            "CL": "Cl",
            "BR": "Br",
            "I": "I",
            "HD": "H",
            "HS": "H",
            "MG": "Mg",
            "ZN": "Zn",
            "CA": "Ca",
            "MN": "Mn",
            "FE": "Fe",
        }
        element = pdbqt_map.get(token_upper)
        if not element:
            alpha = "".join(ch for ch in token if ch.isalpha()) or "C"
            if len(alpha) >= 2 and alpha[:2].upper() in ("CL", "BR", "NA", "MG", "ZN", "CA", "MN", "FE"):
                alpha = alpha[:2]
            else:
                alpha = alpha[:1]
            element = alpha[0].upper() + alpha[1:].lower()
        return {"serial": serial, "name": name, "x": x, "y": y, "z": z, "element": element}

    @staticmethod
    def _infer_ligand_bonds(atoms: list[dict]) -> list[tuple[int, int]]:
        radii = {
            "H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "F": 0.57,
            "P": 1.07, "S": 1.05, "Cl": 1.02, "Br": 1.20, "I": 1.39,
        }
        bonds: list[tuple[int, int]] = []
        for i, a1 in enumerate(atoms):
            r1 = radii.get(a1["element"], 0.77)
            for a2 in atoms[i + 1:]:
                r2 = radii.get(a2["element"], 0.77)
                dx = a1["x"] - a2["x"]
                dy = a1["y"] - a2["y"]
                dz = a1["z"] - a2["z"]
                dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                cutoff = max(0.9, min(2.0, r1 + r2 + 0.45))
                if 0.4 < dist <= cutoff:
                    bonds.append((a1["serial"], a2["serial"]))
        return bonds

    @staticmethod
    def _project_atoms_to_2d(atoms: list[dict]) -> dict[int, tuple[float, float]]:
        if not atoms:
            return {}
        if len(atoms) == 1:
            return {atoms[0]["serial"]: (0.0, 0.0)}
        try:
            import numpy as np
            coords = np.array([[a["x"], a["y"], a["z"]] for a in atoms], dtype=float)
            coords -= coords.mean(axis=0)
            _, _, vh = np.linalg.svd(coords, full_matrices=False)
            basis = vh[:2].T
            projected = coords @ basis
            return {
                atom["serial"]: (float(projected[idx, 0]), float(projected[idx, 1]))
                for idx, atom in enumerate(atoms)
            }
        except Exception:
            return {
                atom["serial"]: (float(atom["x"]), float(atom["y"]))
                for atom in atoms
            }


# ---------------------------------------------------------------------------
# InteractionDiagram — public wrapper with style-toggle toolbar
# ---------------------------------------------------------------------------

class InteractionDiagram(QWidget):
    """
    Public widget: style toolbar (Bipartite | Radial | LID) on top,
    _DiagramCanvas below.  Drop-in replacement for the old single-style widget.
    """

    STYLE_BIPARTITE = _DiagramCanvas.STYLE_BIPARTITE
    STYLE_RADIAL    = _DiagramCanvas.STYLE_RADIAL
    STYLE_LID       = _DiagramCanvas.STYLE_LID

    _BTN_STYLE = (
        f"QPushButton{{background:{theme.BG_HOVER};color:{theme.TEXT_DIM};"
        f"border:1px solid {theme.BORDER};border-radius:4px;padding:1px 8px;font-size:10px;}}"
        f"QPushButton:checked{{background:{theme.ACCENT_DARK};color:#ffffff;"
        f"border:1px solid {theme.ACCENT};}}"
        f"QPushButton:hover:!checked{{background:{theme.BORDER};color:{theme.TEXT};}}"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(f"background:{theme.BG_CANVAS};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── Style toolbar ───────────────────────────────────────────────
        tb_widget = QWidget()
        tb_widget.setStyleSheet(f"background:{theme.BG_CANVAS};")
        tb_layout = QHBoxLayout(tb_widget)
        tb_layout.setContentsMargins(6, 2, 6, 2)
        tb_layout.setSpacing(4)
        lbl = QLabel("Layout:")
        lbl.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:10px;")
        tb_layout.addWidget(lbl)
        self._style_btns: list[QPushButton] = []
        for label, sid in [("≡ Bipartite", 0), ("◎ Radial", 1), ("⚛ LID", 2)]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(sid == 0)
            btn.setFixedHeight(20)
            btn.setStyleSheet(self._BTN_STYLE)
            btn.clicked.connect(lambda _, s=sid: self._set_style(s))
            tb_layout.addWidget(btn)
            self._style_btns.append(btn)
        tb_layout.addStretch()
        # ── Background mode ─────────────────────────────────────────────
        sep2 = QLabel("|")
        sep2.setStyleSheet(f"color:{theme.BORDER}; font-size:10px;")
        tb_layout.addWidget(sep2)
        bg_lbl = QLabel("BG:")
        bg_lbl.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:10px;")
        tb_layout.addWidget(bg_lbl)
        self._bg_btns: list[QPushButton] = []
        for label, bm in [("⬛ Dark", "dark"), ("⬜ White", "white"), ("□ Transparent", "transparent")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(bm == "dark")
            btn.setFixedHeight(20)
            btn.setStyleSheet(self._BTN_STYLE)
            btn.clicked.connect(lambda _, m=bm: self._set_bg_mode(m))
            tb_layout.addWidget(btn)
            self._bg_btns.append(btn)
        # ── Mol style (shown for Radial/LID only) ──────────────────────
        sep = QLabel("|")
        sep.setStyleSheet(f"color:{theme.BORDER}; font-size:10px;")
        tb_layout.addWidget(sep)
        mol_lbl = QLabel("Mol:")
        mol_lbl.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:10px;")
        tb_layout.addWidget(mol_lbl)
        self._mol_btns: list[QPushButton] = []
        for label, ms in [("— Line", "line"), ("● Ball", "ball"), ("| Stick", "stick")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(ms == "line")
            btn.setFixedHeight(20)
            btn.setStyleSheet(self._BTN_STYLE)
            btn.clicked.connect(lambda _, m=ms: self._set_mol_style(m))
            tb_layout.addWidget(btn)
            self._mol_btns.append(btn)
        lay.addWidget(tb_widget)

        # ── Canvas ──────────────────────────────────────────────────────
        self._canvas = _DiagramCanvas()
        lay.addWidget(self._canvas, stretch=1)

    def _set_bg_mode(self, mode: str):
        bm_list = ["dark", "white", "transparent"]
        for i, btn in enumerate(self._bg_btns):
            btn.setChecked(bm_list[i] == mode)
        self._canvas.set_bg_mode(mode)

    def _set_style(self, style: int):
        for i, btn in enumerate(self._style_btns):
            btn.setChecked(i == style)
        self._canvas.set_style(style)

    def _set_mol_style(self, mol_style: str):
        styles = ["line", "ball", "stick"]
        for i, btn in enumerate(self._mol_btns):
            btn.setChecked(styles[i] == mol_style)
        self._canvas.set_mol_style(mol_style)

    # ── Public API (mirrors old InteractionDiagram) ─────────────────────
    def set_result(self, result: AnalysisResult):
        self._canvas.set_result(result)

    def set_type_filter(self, types: set[str]):
        self._canvas.set_type_filter(types)

    def set_style(self, style: int):
        self._set_style(style)

    def set_mol_style(self, mol_style: str):
        self._set_mol_style(mol_style)

    def set_bg_mode(self, mode: str):
        self._set_bg_mode(mode)

    def set_ligand_path(self, path: Optional[str]):
        self._canvas.set_ligand_path(path)

    def set_ligand_smiles(self, smiles: Optional[str]):
        self._canvas.set_ligand_smiles(smiles)


# ---------------------------------------------------------------------------
# Interaction Table
# ---------------------------------------------------------------------------

class InteractionTable(QTableWidget):
    """Sortable table of all interactions."""

    COLUMNS = ["Type", "Receptor Residue", "Ligand Atom", "Distance (Å)", "Subtype"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(self.COLUMNS))
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.setSortingEnabled(True)

    def load_result(self, result: AnalysisResult):
        self.setSortingEnabled(False)
        self.clearContents()
        self.setRowCount(len(result.interactions))
        for row, inter in enumerate(result.interactions):
            color = QColor(ITYPE_COLORS.get(inter.itype, "#ffffff"))

            def _item(text: str, clr: QColor = color) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setForeground(clr)
                return it

            icon = ITYPE_ICONS.get(inter.itype, "")
            self.setItem(row, 0, _item(f"{icon} {inter.itype}"))
            self.setItem(row, 1, _item(inter.rec_label(), QColor(theme.TEXT)))
            self.setItem(row, 2, _item(inter.lig_label(), QColor(theme.TEXT)))
            dist_item = QTableWidgetItem()
            dist_item.setData(Qt.DisplayRole, round(inter.distance, 2))
            dist_item.setForeground(QColor(theme.TEXT))
            self.setItem(row, 3, dist_item)
            self.setItem(row, 4, _item(inter.subtype or "-", QColor(theme.TEXT_DIM)))

        self.setSortingEnabled(True)
        self.sortByColumn(3, Qt.AscendingOrder)   # sort by distance

    def load_filtered(self, result: AnalysisResult, types: set[str]):
        """Load only interactions whose itype is in types."""
        filtered = [i for i in result.interactions if i.itype in types]
        self.setSortingEnabled(False)
        self.clearContents()
        self.setRowCount(len(filtered))
        for row, inter in enumerate(filtered):
            color = QColor(ITYPE_COLORS.get(inter.itype, "#ffffff"))

            def _item(text: str, clr: QColor = color) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setForeground(clr)
                return it

            icon = ITYPE_ICONS.get(inter.itype, "")
            self.setItem(row, 0, _item(f"{icon} {inter.itype}"))
            self.setItem(row, 1, _item(inter.rec_label(), QColor(theme.TEXT)))
            self.setItem(row, 2, _item(inter.lig_label(), QColor(theme.TEXT)))
            dist_item = QTableWidgetItem()
            dist_item.setData(Qt.DisplayRole, round(inter.distance, 2))
            dist_item.setForeground(QColor(theme.TEXT))
            self.setItem(row, 3, dist_item)
            self.setItem(row, 4, _item(inter.subtype or "-", QColor(theme.TEXT_DIM)))
        self.setSortingEnabled(True)
        self.sortByColumn(3, Qt.AscendingOrder)


# ---------------------------------------------------------------------------
# Summary badges
# ---------------------------------------------------------------------------

class SummaryBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(8)
        self._layout.addStretch()

    def update_counts(self, counts: dict[str, int]):
        # Remove all existing badges
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for itype, count in sorted(counts.items()):
            color = ITYPE_COLORS.get(itype, "#ffffff")
            icon  = ITYPE_ICONS.get(itype, "")
            badge = QLabel(f" {icon} {itype}: <b>{count}</b> ")
            badge.setStyleSheet(
                f"color:{color}; background:rgba(30,30,46,0.8);"
                f"border:1px solid {color}; border-radius:10px;"
                f"padding:2px 8px; font-size:12px;"
            )
            badge.setTextFormat(Qt.RichText)
            self._layout.insertWidget(self._layout.count() - 1, badge)


# ---------------------------------------------------------------------------
# Main Interaction Panel
# ---------------------------------------------------------------------------

class InteractionPanel(QWidget):
    """
    Full interaction analysis panel.
    Call analyze(receptor_path, ligand_path) or analyze_from_strings(rec, lig).
    """

    hbonds_ready = Signal(list)   # [{start,end,itype,color}] — emitted on "Show in 3D"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: Optional[AnalysisResult] = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # ── Header row ────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.addWidget(SectionLabel("⚗️  Interactions"))
        hdr.addStretch()
        self._load_btn   = QPushButton("📂 Load…")
        self._export_btn = QPushButton("📥 JSON")
        self._export_btn.setEnabled(False)
        self._load_btn.clicked.connect(self._load_files)
        self._export_btn.clicked.connect(self._export_json)
        for btn in (self._load_btn, self._export_btn):
            btn.setFixedHeight(24)
            hdr.addWidget(btn)
        layout.addLayout(hdr)

        # ── Interaction-type filter + action buttons ───────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        filter_lbl = QLabel("Show:")
        filter_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:11px;")
        filter_row.addWidget(filter_lbl)

        self._type_cbs: dict[str, QCheckBox] = {}
        for itype, color in ITYPE_COLORS.items():
            icon = ITYPE_ICONS.get(itype, "")
            cb = QCheckBox(f"{icon} {itype}")
            cb.setChecked(itype == "H-Bond")   # H-Bond on by default
            cb.setStyleSheet(
                f"QCheckBox{{color:{color}; font-size:10px;}}"
                f"QCheckBox::indicator{{width:12px;height:12px;}}"
            )
            cb.stateChanged.connect(self._on_filter_changed)
            self._type_cbs[itype] = cb
            filter_row.addWidget(cb)

        filter_row.addStretch()

        self._show3d_btn = QPushButton("🔬 3D")
        self._show3d_btn.setToolTip("Show selected interaction types as bonds in 3D viewer")
        self._show3d_btn.setFixedHeight(24)
        self._show3d_btn.setEnabled(False)
        self._show3d_btn.clicked.connect(self._emit_hbonds)

        self._show2d_btn = QPushButton("🗺 2D")
        self._show2d_btn.setToolTip("Toggle 2D interaction diagram")
        self._show2d_btn.setFixedHeight(24)
        self._show2d_btn.setCheckable(True)
        self._show2d_btn.setChecked(True)
        self._show2d_btn.clicked.connect(self._toggle_diagram)

        filter_row.addWidget(self._show3d_btn)
        filter_row.addWidget(self._show2d_btn)
        layout.addLayout(filter_row)

        # ── Summary badges ─────────────────────────────────────────────
        self._summary_bar = SummaryBar()
        layout.addWidget(self._summary_bar)

        # ── Splitter: diagram (top) | table (bottom) ───────────────────
        self._splitter = QSplitter(Qt.Vertical)

        self._diagram = InteractionDiagram()
        self._splitter.addWidget(self._diagram)

        self._table = InteractionTable()
        self._splitter.addWidget(self._table)

        self._splitter.setStretchFactor(0, 2)
        self._splitter.setStretchFactor(1, 3)
        layout.addWidget(self._splitter, stretch=1)

        # ── Status ─────────────────────────────────────────────────────
        self._status = QLabel("No analysis loaded.")
        self._status.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:11px;")
        layout.addWidget(self._status)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def analyze(self, receptor_path: str, ligand_path: str):
        """Analyze from two PDBQT files."""
        if not os.path.isfile(receptor_path):
            self._status.setText(f"❌ Receptor not found: {receptor_path}")
            return
        if not os.path.isfile(ligand_path):
            self._status.setText(f"❌ Ligand not found: {ligand_path}")
            return
        try:
            result = analyze_from_files(receptor_path, ligand_path)
            self._apply_result(result, label=os.path.basename(ligand_path))
        except Exception as e:
            self._status.setText(f"❌ Analysis error: {e}")

    def analyze_strings(self, receptor_text: str, ligand_text: str, label: str = ""):
        """Analyze from raw PDBQT strings."""
        try:
            result = analyze_from_strings(receptor_text, ligand_text)
            self._apply_result(result, label=label)
        except Exception as e:
            self._status.setText(f"❌ Analysis error: {e}")

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _apply_result(self, result: AnalysisResult, label: str = ""):
        self._result = result
        self._diagram.set_result(result)
        self._table.load_result(result)
        self._summary_bar.update_counts(result.summary)
        self._on_filter_changed()   # apply current checkbox state to diagram

        total = len(result.interactions)
        counts = "  ".join(f"{v}×{k}" for k, v in sorted(result.summary.items()))
        self._status.setText(
            f"{label}  |  {total} interactions  |  {counts}"
        )
        self._export_btn.setEnabled(True)
        # Enable 3D button if any checked type has results
        self._update_show3d_state()

    def _checked_types(self) -> list[str]:
        """Return list of currently checked interaction types."""
        return [t for t, cb in self._type_cbs.items() if cb.isChecked()]

    def _on_filter_changed(self):
        """Called when any type checkbox changes — update 2D diagram filter."""
        types = set(self._checked_types())
        self._diagram.set_type_filter(types)
        self._update_show3d_state()

    def _update_show3d_state(self):
        if not self._result:
            self._show3d_btn.setEnabled(False)
            return
        checked = self._checked_types()
        has_any = any(self._result.by_type(t) for t in checked)
        self._show3d_btn.setEnabled(has_any)

    def _toggle_diagram(self):
        visible = self._show2d_btn.isChecked()
        self._diagram.setVisible(visible)

    def _load_files(self):
        rec, _ = QFileDialog.getOpenFileName(
            self, "Select Receptor PDBQT", "",
            "PDBQT Files (*.pdbqt);;All (*)"
        )
        if not rec:
            return
        lig, _ = QFileDialog.getOpenFileName(
            self, "Select Ligand / Pose PDBQT", "",
            "PDBQT Files (*.pdbqt);;All (*)"
        )
        if not lig:
            return
        self.analyze(rec, lig)

    def _emit_hbonds(self):
        """Emit interaction vectors for all checked types → 3D viewer."""
        if self._result:
            types = self._checked_types()
            vectors = self._result.to_vectors(types if types else None)
            self.hbonds_ready.emit(vectors)

    def _export_json(self):
        if not self._result:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Interactions", "interactions.json",
            "JSON (*.json)"
        )
        if not path:
            return
        data = []
        for i in self._result.interactions:
            data.append({
                "type":    i.itype,
                "subtype": i.subtype,
                "receptor": {
                    "residue": i.rec_atom.resname,
                    "resseq":  i.rec_atom.resseq,
                    "chain":   i.rec_atom.chain,
                    "atom":    i.rec_atom.name,
                    "x": i.rec_atom.x, "y": i.rec_atom.y, "z": i.rec_atom.z,
                },
                "ligand": {
                    "atom":  i.lig_atom.name,
                    "type":  i.lig_atom.atom_type,
                    "x": i.lig_atom.x, "y": i.lig_atom.y, "z": i.lig_atom.z,
                },
                "distance_A": round(i.distance, 3),
                "angle_deg":  round(i.angle, 2) if i.angle else None,
            })
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            self._status.setText(f"Exported {len(data)} interactions → {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))
