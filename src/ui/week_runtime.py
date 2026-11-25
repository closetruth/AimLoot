from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..models import AppState
from ..runtime_intervals import (
    DaySlice,
    add_weeks,
    day_seconds_for_week,
    enrich_slices,
    filter_slices_by_task,
    identity_color,
    local_week_start,
    seconds_from_slices,
    slices_for_week,
    top_level_titles_from_slices,
)
from ..task_manager import TaskManager
from .qt import clear_layout
from .styles import BORDER, FONT_FAMILY, TEXT_MUTED, TEXT_PRIMARY
from ..text import format_duration

WEEKDAYS = "一二三四五六日"
GUTTER = 28
HEADER = 36
MIN_BLOCK_PX = 4
ZOOM_MIN_SPAN = 3.0
GRID_MIN_PX = 280
GRID_PX_PER_HOUR = 32
LEGEND_MAX_PX = 88
ALL_GOALS_LABEL = "全部目标"
# Match QTabWidget::pane in task_dialog; opaque so tall grid/legend clip.
WEEK_PANE_BG = "#1a1b24"
WEEK_SCROLL_QSS = f"""
QScrollArea#WeekGridScroll, QScrollArea#WeekLegendScroll {{
    background-color: {WEEK_PANE_BG};
    border: none;
}}
QScrollArea#WeekGridScroll > QWidget > QWidget,
QScrollArea#WeekLegendScroll > QWidget > QWidget {{
    background-color: {WEEK_PANE_BG};
}}
"""


def grid_min_height(view_lo: float, view_hi: float) -> int:
    span = max(0.5, float(view_hi) - float(view_lo))
    return max(GRID_MIN_PX, round(span * GRID_PX_PER_HOUR))


def _opaque_week_scroll(scroll: QScrollArea) -> None:
    """Dialog QSS makes QScrollArea transparent; force opaque clip so siblings don't stack."""
    scroll.setStyleSheet(WEEK_SCROLL_QSS)
    _paint_week_viewport_opaque(scroll.viewport())


def _paint_week_viewport_opaque(vp: QWidget) -> None:
    vp.setAutoFillBackground(True)
    vp.setAttribute(Qt.WA_OpaquePaintEvent, True)
    pal = vp.palette()
    pal.setColor(vp.backgroundRole(), QColor(WEEK_PANE_BG))
    vp.setPalette(pal)


def format_running_status(state: AppState) -> str:
    active = state.active_task()
    if active is None:
        return "当前没有运行中的目标"
    if active.subtasks:
        leaf = active.current_subtask()
        if leaf is None:
            return "当前没有运行中的目标"
        return f"正在运行  顶层「{active.title}」  ·  「{leaf.title}」"
    return f"正在运行  顶层「{active.title}」"


def format_clock_hours(hours: float) -> str:
    total = int(round(float(hours) * 3600))
    total = max(0, min(24 * 3600, total))
    h, rem = divmod(total, 3600)
    m = rem // 60
    if h >= 24:
        return "24:00"
    return f"{h:02d}:{m:02d}"


def format_slice_hover(sl: DaySlice) -> str:
    t0 = format_clock_hours(sl.t0)
    t1 = format_clock_hours(sl.t1)
    dur = format_duration((sl.t1 - sl.t0) * 3600)
    if sl.leaf_title:
        return f"{sl.title} · {sl.leaf_title}  {t0}–{t1}  （{dur}）"
    return f"{sl.title}  {t0}–{t1}  （{dur}）"


def format_legend_label(title: str, leaf_title: Optional[str], *, running: bool) -> str:
    base = f"{title} · {leaf_title}" if leaf_title else title
    if running:
        return f"{base}  运行中"
    return base


def legend_row_specs(
    slices: list[DaySlice],
    running_identity: Optional[tuple[str, Optional[str]]] = None,
) -> list[tuple[str, str]]:
    """Unique identities in first-seen order: (identity_color hex, legend label)."""
    seen: list[tuple[str, Optional[str]]] = []
    rows: list[tuple[str, str]] = []
    for sl in slices:
        key = sl.identity()
        if key in seen:
            continue
        seen.append(key)
        rows.append(
            (
                identity_color(sl.task_id, sl.leaf_id),
                format_legend_label(
                    sl.title, sl.leaf_title, running=running_identity == key
                ),
            )
        )
    return rows


def clip_hours(
    t0: float, t1: float, view_lo: float, view_hi: float
) -> Optional[tuple[float, float]]:
    a = max(t0, view_lo)
    b = min(t1, view_hi)
    if b <= a:
        return None
    return a, b


def hour_to_y(hour: float, view_lo: float, view_hi: float, top: int, height: int) -> int:
    span = max(1e-6, view_hi - view_lo)
    t = (hour - view_lo) / span
    t = max(0.0, min(1.0, t))
    return top + int(height * t)


def view_hour_ticks(view_lo: float, view_hi: float) -> list[float]:
    """Label hours inside the visible window (inclusive ends when on integers)."""
    span = view_hi - view_lo
    if span <= 6:
        step = 1.0
    elif span <= 12:
        step = 2.0
    else:
        step = 6.0
    ticks: list[float] = []
    # Start at first multiple of step at or above view_lo
    start = int(view_lo // step) * step
    if start < view_lo - 1e-9:
        start += step
    h = start
    while h <= view_hi + 1e-9:
        ticks.append(float(h))
        h += step
    if not ticks or abs(ticks[0] - view_lo) > 1e-6:
        ticks.insert(0, view_lo)
    if abs(ticks[-1] - view_hi) > 1e-6:
        ticks.append(view_hi)
    # de-dupe near equals
    out: list[float] = []
    for x in ticks:
        if not out or abs(out[-1] - x) > 1e-6:
            out.append(x)
    return out


def zoom_window(
    view_lo: float,
    view_hi: float,
    *,
    center: float,
    factor: float,
    min_span: float = ZOOM_MIN_SPAN,
) -> tuple[float, float]:
    """Shrink (factor<1) or grow (factor>1) the hour window around center, clamped to [0,24]."""
    span = max(min_span, (view_hi - view_lo) * factor)
    span = min(24.0, span)
    lo = center - span / 2.0
    hi = center + span / 2.0
    if lo < 0.0:
        hi -= lo
        lo = 0.0
    if hi > 24.0:
        lo -= hi - 24.0
        hi = 24.0
    lo = max(0.0, lo)
    hi = min(24.0, hi)
    if hi - lo < min_span:
        hi = min(24.0, lo + min_span)
        lo = max(0.0, hi - min_span)
    return lo, hi


class WeekGrid(QWidget):
    column_count = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slices: list[DaySlice] = []
        self._week_start = 0.0
        self._now = 0.0
        self._open_identity = None
        self._view_lo = 0.0
        self._view_hi = 24.0
        self._day_seconds: list[float] = [0.0] * 7
        self._col_rects: list[QRect] = []
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:
        return QSize(400, grid_min_height(self._view_lo, self._view_hi))

    def minimumSizeHint(self) -> QSize:
        return QSize(200, grid_min_height(self._view_lo, self._view_hi))

    def set_view(self, view_lo: float, view_hi: float) -> None:
        self._view_lo = max(0.0, min(24.0, float(view_lo)))
        self._view_hi = max(self._view_lo + 0.5, min(24.0, float(view_hi)))
        self.updateGeometry()
        self.update()

    def set_slices(
        self,
        slices,
        *,
        week_start,
        now,
        open_identity,
        view_lo: float | None = None,
        view_hi: float | None = None,
        day_seconds: list[float] | None = None,
    ):
        self._slices = slices
        self._week_start = week_start
        self._now = now
        self._open_identity = open_identity
        if day_seconds is None:
            self._day_seconds = [0.0] * 7
        else:
            secs = list(day_seconds[:7])
            while len(secs) < 7:
                secs.append(0.0)
            self._day_seconds = secs
        if view_lo is not None and view_hi is not None:
            self.set_view(view_lo, view_hi)
        else:
            self.update()

    def _body_rect(self) -> QRect:
        return QRect(
            GUTTER,
            HEADER,
            max(1, self.width() - GUTTER),
            max(1, self.height() - HEADER),
        )

    def _block_rect(self, col: QRect, t0: float, t1: float) -> Optional[QRect]:
        clipped = clip_hours(t0, t1, self._view_lo, self._view_hi)
        if clipped is None:
            return None
        a, b = clipped
        y0 = hour_to_y(a, self._view_lo, self._view_hi, col.y(), col.height())
        y1 = hour_to_y(b, self._view_lo, self._view_hi, col.y(), col.height())
        h = max(MIN_BLOCK_PX, y1 - y0)
        return QRect(col.x() + 2, y0, col.width() - 4, h)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setFont(QFont("Microsoft YaHei UI", 8))
        p.fillRect(self.rect(), QColor(WEEK_PANE_BG))
        body = self._body_rect()
        col_w = body.width() / 7.0
        self._col_rects = []
        for i in range(7):
            x = int(body.x() + i * col_w)
            w = int(body.x() + (i + 1) * col_w) - x
            self._col_rects.append(QRect(x, body.y(), w, body.height()))
        p.setPen(QColor(TEXT_MUTED))
        for hour in view_hour_ticks(self._view_lo, self._view_hi):
            y = hour_to_y(hour, self._view_lo, self._view_hi, body.y(), body.height())
            label = str(int(round(hour))) if abs(hour - round(hour)) < 1e-6 else f"{hour:.1f}"
            p.drawText(
                0,
                y - 6,
                GUTTER - 2,
                12,
                int(Qt.AlignRight | Qt.AlignVCenter),
                label,
            )
        mon = datetime.fromtimestamp(self._week_start)
        today = datetime.fromtimestamp(self._now).date()
        for i, rect in enumerate(self._col_rects):
            day = mon + timedelta(days=i)
            p.setPen(QColor(TEXT_PRIMARY))
            p.drawText(
                rect.x(),
                0,
                rect.width(),
                18,
                int(Qt.AlignCenter | Qt.AlignBottom),
                f"{WEEKDAYS[i]} {day.day:02d}",
            )
            p.setPen(QColor(TEXT_MUTED))
            sec = self._day_seconds[i] if i < len(self._day_seconds) else 0.0
            p.drawText(
                rect.x(),
                16,
                rect.width(),
                18,
                int(Qt.AlignCenter | Qt.AlignTop),
                format_duration(sec),
            )
            p.setPen(QColor("#2a2d38"))
            p.drawRect(rect.adjusted(0, 0, -1, -1))
        for sl in self._slices:
            idx = (datetime.strptime(sl.date, "%Y-%m-%d").date() - mon.date()).days
            if idx < 0 or idx > 6:
                continue
            block = self._block_rect(self._col_rects[idx], sl.t0, sl.t1)
            if block is None:
                continue
            color = QColor(identity_color(sl.task_id, sl.leaf_id))
            p.fillRect(block, color)
            if (
                self._open_identity == sl.identity()
                and datetime.strptime(sl.date, "%Y-%m-%d").date() == today
            ):
                p.setPen(QPen(color.lighter(140), 2))
                p.drawRect(block.adjusted(0, 0, -1, -1))
        now_dt = datetime.fromtimestamp(self._now)
        if mon.date() <= now_dt.date() <= (mon + timedelta(days=6)).date():
            idx = (now_dt.date() - mon.date()).days
            rect = self._col_rects[idx]
            hour = now_dt.hour + now_dt.minute / 60.0 + now_dt.second / 3600.0
            if self._view_lo <= hour <= self._view_hi:
                y = hour_to_y(
                    hour, self._view_lo, self._view_hi, rect.y(), rect.height()
                )
                p.setPen(QPen(QColor("#e8eaf0"), 1))
                p.drawLine(rect.left(), y, rect.right(), y)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        mon = datetime.fromtimestamp(self._week_start)
        for sl in self._slices:
            idx = (datetime.strptime(sl.date, "%Y-%m-%d").date() - mon.date()).days
            if idx < 0 or idx > 6 or idx >= len(self._col_rects):
                continue
            block = self._block_rect(self._col_rects[idx], sl.t0, sl.t1)
            if block is not None and block.contains(pos):
                QToolTip.showText(
                    event.globalPosition().toPoint(), format_slice_hover(sl), self
                )
                return
        QToolTip.hideText()


class WeekRuntimePanel(QWidget):
    def __init__(self, state: AppState, manager: TaskManager, parent=None):
        super().__init__(parent)
        self.state = state
        self.manager = manager
        self._week_start = local_week_start(time.time())
        self._view_lo = 0.0
        self._view_hi = 24.0
        self._filter_task_id: Optional[str] = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        nav = QHBoxLayout()
        self.btn_prev = QPushButton("←")
        self.btn_next = QPushButton("→")
        self.lbl_range = QLabel("")
        self.lbl_range.setAlignment(Qt.AlignCenter)
        self.btn_prev.clicked.connect(self._prev_week)
        self.btn_next.clicked.connect(self._next_week)
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.lbl_range, 1)
        nav.addWidget(self.btn_next)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("顶层目标"))
        self.combo_goal = QComboBox()
        self.combo_goal.setMinimumWidth(160)
        self.combo_goal.currentIndexChanged.connect(self._on_goal_filter_changed)
        filter_row.addWidget(self.combo_goal, 1)

        zoom = QHBoxLayout()
        self.btn_full = QPushButton("全日")
        self.btn_day = QPushButton("白天")
        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_out = QPushButton("-")
        self.btn_full.setToolTip("显示 0–24 点")
        self.btn_day.setToolTip("放大到 8–24 点")
        self.btn_zoom_in.setToolTip("放大（更细）")
        self.btn_zoom_out.setToolTip("缩小")
        self.btn_full.clicked.connect(self._view_full)
        self.btn_day.clicked.connect(self._view_daytime)
        self.btn_zoom_in.clicked.connect(self._zoom_in)
        self.btn_zoom_out.clicked.connect(self._zoom_out)
        for b in (self.btn_full, self.btn_day, self.btn_zoom_in, self.btn_zoom_out):
            zoom.addWidget(b)
        zoom.addStretch(1)

        self.lbl_status = QLabel("")
        self.lbl_week_total = QLabel("")
        self.lbl_week_total.setStyleSheet(f"color: {TEXT_PRIMARY}; font-family: {FONT_FAMILY};")
        self.lbl_reset = QLabel("运行记录已重置")
        self.lbl_reset.setStyleSheet(f"color: {TEXT_MUTED};")
        self.lbl_reset.hide()
        self.grid = WeekGrid(self)
        self.grid.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setObjectName("WeekGridScroll")
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setFrameShape(QFrame.NoFrame)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.grid_scroll.setWidget(self.grid)
        _opaque_week_scroll(self.grid_scroll)
        self.legend = QWidget()
        self.legend_layout = QVBoxLayout(self.legend)
        self.legend_layout.setContentsMargins(0, 4, 0, 0)
        self.legend_layout.setSpacing(4)
        self.legend_scroll = QScrollArea()
        self.legend_scroll.setObjectName("WeekLegendScroll")
        self.legend_scroll.setWidgetResizable(True)
        self.legend_scroll.setFrameShape(QFrame.NoFrame)
        self.legend_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.legend_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.legend_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.legend_scroll.setMaximumHeight(LEGEND_MAX_PX)
        self.legend_scroll.setWidget(self.legend)
        _opaque_week_scroll(self.legend_scroll)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(nav)
        lay.addLayout(filter_row)
        lay.addLayout(zoom)
        lay.addWidget(self.lbl_status)
        lay.addWidget(self.lbl_week_total)
        lay.addWidget(self.lbl_reset)
        lay.addWidget(self.grid_scroll, 1)
        lay.addWidget(self.legend_scroll)
        self.grid_scroll.viewport().installEventFilter(self)
        self.legend_scroll.viewport().installEventFilter(self)
        self.refresh()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Polish, QEvent.Type.StyleChange):
            if obj is self.grid_scroll.viewport() or obj is self.legend_scroll.viewport():
                _paint_week_viewport_opaque(obj)
        return super().eventFilter(obj, event)

    def event(self, event):
        result = super().event(event)
        # Dialog stylesheet polish clears viewport autoFillBackground after addTab.
        if event.type() in (QEvent.Type.Polish, QEvent.Type.StyleChange):
            if hasattr(self, "grid_scroll"):
                _paint_week_viewport_opaque(self.grid_scroll.viewport())
                _paint_week_viewport_opaque(self.legend_scroll.viewport())
        return result

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._timer.start()
        self.refresh()
        QTimer.singleShot(0, self._reopaque_scrolls)

    def _reopaque_scrolls(self) -> None:
        _opaque_week_scroll(self.grid_scroll)
        _opaque_week_scroll(self.legend_scroll)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _prev_week(self) -> None:
        self._week_start = add_weeks(self._week_start, -1)
        self.refresh()

    def _next_week(self) -> None:
        nxt = add_weeks(self._week_start, 1)
        if nxt <= local_week_start(time.time()):
            self._week_start = nxt
            self.refresh()

    def _view_full(self) -> None:
        self._view_lo, self._view_hi = 0.0, 24.0
        self.refresh()

    def _view_daytime(self) -> None:
        self._view_lo, self._view_hi = 8.0, 24.0
        self.refresh()

    def _zoom_center(self) -> float:
        now = time.time()
        mon = datetime.fromtimestamp(self._week_start).date()
        today = datetime.fromtimestamp(now).date()
        if mon <= today <= mon + timedelta(days=6):
            dt = datetime.fromtimestamp(now)
            return dt.hour + dt.minute / 60.0 + dt.second / 3600.0
        return (self._view_lo + self._view_hi) / 2.0

    def _zoom_in(self) -> None:
        self._view_lo, self._view_hi = zoom_window(
            self._view_lo,
            self._view_hi,
            center=self._zoom_center(),
            factor=0.5,
        )
        self.refresh()

    def _zoom_out(self) -> None:
        self._view_lo, self._view_hi = zoom_window(
            self._view_lo,
            self._view_hi,
            center=self._zoom_center(),
            factor=2.0,
        )
        self.refresh()

    def _on_goal_filter_changed(self, index: int) -> None:
        task_id = self.combo_goal.itemData(index)
        self._filter_task_id = None if not task_id else str(task_id)
        self.refresh()

    def _sync_goal_combo(self, options: list[tuple[str, str]]) -> None:
        """Rebuild combo while preserving selection when still present."""
        wanted = self._filter_task_id
        self.combo_goal.blockSignals(True)
        self.combo_goal.clear()
        self.combo_goal.addItem(ALL_GOALS_LABEL, None)
        for task_id, title in options:
            self.combo_goal.addItem(title, task_id)
        idx = 0
        if wanted is not None:
            found = self.combo_goal.findData(wanted)
            if found >= 0:
                idx = found
            else:
                self._filter_task_id = None
        self.combo_goal.setCurrentIndex(idx)
        self.combo_goal.blockSignals(False)

    def refresh(self) -> None:
        now = time.time()
        current = local_week_start(now)
        self.btn_next.setEnabled(add_weeks(self._week_start, 1) <= current)
        mon = datetime.fromtimestamp(self._week_start)
        sun = mon + timedelta(days=6)
        self.lbl_range.setText(
            f"{mon.strftime('%Y-%m-%d')} ~ {sun.strftime('%Y-%m-%d')}"
        )
        self.lbl_status.setText(format_running_status(self.state))
        self.lbl_reset.setVisible(self.manager.runtime_log.load_reset)
        raw = slices_for_week(self.manager.runtime_log, self._week_start, now)
        all_slices = enrich_slices(self.state, raw)
        self._sync_goal_combo(top_level_titles_from_slices(all_slices))
        slices = filter_slices_by_task(all_slices, self._filter_task_id)
        day_secs = day_seconds_for_week(slices, self._week_start)
        week_secs = seconds_from_slices(slices)
        self.lbl_week_total.setText(f"本周合计  {format_duration(week_secs)}")
        ident = None
        rec, task_id, _t, leaf_id, _lt = self.manager.recording_identity()
        if rec and task_id:
            ident = (task_id, leaf_id)
        self.grid.set_slices(
            slices,
            week_start=self._week_start,
            now=now,
            open_identity=ident,
            view_lo=self._view_lo,
            view_hi=self._view_hi,
            day_seconds=day_secs,
        )
        self._sync_grid_height()
        self._fill_legend(legend_row_specs(slices, ident))

    def _sync_grid_height(self) -> None:
        h = grid_min_height(self._view_lo, self._view_hi)
        self.grid.setMinimumHeight(h)
        self.grid.updateGeometry()

    def _fill_legend(self, rows: list[tuple[str, str]]) -> None:
        clear_layout(self.legend_layout)
        for color, text in rows:
            row = QWidget(self.legend)
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(8)
            swatch = QLabel(row)
            swatch.setFixedSize(12, 12)
            swatch.setStyleSheet(
                f"background-color: {color}; border: 1px solid {BORDER};"
            )
            lbl = QLabel(text, row)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; font-family: {FONT_FAMILY};")
            row_lay.addWidget(swatch, 0, Qt.AlignTop)
            row_lay.addWidget(lbl, 1)
            self.legend_layout.addWidget(row)
