"""主悬浮小部件：常驻桌面，显示操作数 / 奖励 / 开奖进度。

目标树区域已拆到 ui_goal_tree_area.GoalTreeArea；本文件负责窗口框架、
顶栏拖动、全局区、右键菜单与开奖 UI。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QMouseEvent, QPainter, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .branding import APP_NAME
from .currency_display import CurrencyDisplay
from .models import AppState, Reward
from .op_tracker import OpRateTracker
from .reward_system import roll_progress
from .storage import save_state
from .task_manager import TaskManager
from .ui_goal_tree_area import GoalTreeArea
from .ui_odometer import RollingAmount
from .ui_qt import make_section_title, set_label_html
from .ui_roll_bar import (
    _CHEST_SIZE,
    _draw_chest,
    EasedProgressBar,
    SegmentedRollBar,
)
from .ui_text import (
    format_ops_summary_html,
    format_roll_history_lines_html,
)
from .ui_widget_qss import WIDGET_STYLESHEET
from .ui_window_drag import DragHandleBar, SystemMoveFilter
from .win_utils import (
    WM_ENTERSIZEMOVE,
    WM_EXITSIZEMOVE,
    is_windows,
    pin_window_to_all_desktops,
    prepare_overlay_hwnd,
    set_startup,
    unpin_window_from_all_desktops,
    win32_message_id,
)

logger = logging.getLogger(__name__)

_FLY_MS = 400
_FLY_SIZE = 22
_BADGE_H = 14
_BADGE_INSET = 3


class _ChestBadge(QWidget):
    """奖励背包按钮内侧右上角：蓝底白字「箱n」方块角标。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._count = 0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(_BADGE_H)
        self.hide()

    def _label(self) -> str:
        if self._count <= 0:
            return ""
        if self._count >= 100:
            return "箱99+"
        return f"箱{self._count}"

    def set_count(self, n: int) -> None:
        n = max(0, int(n))
        if n == self._count and ((n > 0) == self.isVisible()):
            if n > 0:
                self.raise_()
            return
        self._count = n
        if n <= 0:
            self.hide()
            return
        font = QFont("Microsoft YaHei UI", 7)
        font.setBold(True)
        fm_w = QFontMetrics(font).horizontalAdvance(self._label()) + 8
        self.setFixedWidth(max(_BADGE_H + 2, fm_w))
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event) -> None:
        if self._count <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont("Microsoft YaHei UI", 7)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#3a5cff"))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 3, 3)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(self.rect(), int(Qt.AlignCenter), self._label())
        painter.end()


class _FlyingChest(QWidget):
    """领取时飞向背包按钮的临时宝箱。"""

    def __init__(self, rarity: int, parent: QWidget):
        super().__init__(parent)
        self._rarity = int(rarity)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(_FLY_SIZE, _FLY_SIZE)
        self.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        _draw_chest(
            painter,
            self.width() / 2.0,
            self.height() / 2.0,
            float(_CHEST_SIZE + 2),
            reached=True,
            rarity=self._rarity,
            flash_on=True,
            opened=False,
        )
        painter.end()


class FloatingWidget(QWidget):
    """常驻桌面的悬浮小部件。"""

    request_task_dialog = Signal()
    request_inventory_dialog = Signal()
    request_quit = Signal()
    request_stop_bgm = Signal()
    subtask_claimed = Signal(str, object)  # (title, Reward)
    state_changed = Signal()
    ease_point_reached = Signal()
    goal_completed = Signal()
    chest_bagged = Signal()  # 宝箱已写入背包

    def __init__(self, state: AppState, manager: TaskManager):
        super().__init__()
        self.state = state
        self.manager = manager

        self.setWindowTitle(APP_NAME)
        self.setObjectName("WidgetWindow")
        # 不透明顶层窗：避免 WS_EX_LAYERED 点穿。圆角只是视觉，HWND 仍是矩形。
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#1c1c26"))
        self.setPalette(pal)
        flags = (
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
        )
        self.setWindowFlags(flags)
        self.setFixedWidth(308)
        self.setMinimumHeight(600)
        # 高度上限 = 初始高度：任何布局重算都不得把窗口撑过 680
        self.setMaximumHeight(680)
        self.resize(308, 680)

        self._op_tracker = OpRateTracker(window_sec=60.0)
        self._refreshing = False
        self._window_dragging = False
        self._drag_end_timer = QTimer(self)
        self._drag_end_timer.setSingleShot(True)
        self._drag_end_timer.timeout.connect(self.end_user_move)
        vis_g, vis_d = state.visible_gold_diamond()
        self._currency = CurrencyDisplay(vis_g, vis_d)
        self._currency_last_ts: float | None = None
        self._currency_timer = QTimer(self)
        self._currency_timer.setInterval(50)
        self._currency_timer.timeout.connect(self._tick_currency_display)

        self._build_ui()
        # 控件默认是 0/10；立刻用存档进度画一次，否则要等第一次按键才刷新
        self._paint_global_stats()
        self._paint_roll_history()
        self._update_roll_bar()

        # 自动刷新 (用于 active 任务的计时显示)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._refresh_runtime)
        self._tick.start()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        self.setStyleSheet(WIDGET_STYLESHEET)
        root = QWidget(self)
        root.setObjectName("WidgetRoot")
        root.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        v = QVBoxLayout(root)
        v.setContentsMargins(14, 10, 14, 12)
        v.setSpacing(8)

        # 顶栏（拖动手柄 + 窗口按钮）
        top = QHBoxLayout()
        top.setSpacing(4)
        drag_handle = DragHandleBar(self)
        drag_lay = QHBoxLayout(drag_handle)
        drag_lay.setContentsMargins(0, 0, 0, 0)
        drag_lay.setSpacing(4)
        title = QLabel(APP_NAME)
        title.setObjectName("Title")
        drag_lay.addWidget(title)
        drag_lay.addStretch(1)
        top.addWidget(drag_handle, 1)
        self.min_btn = QPushButton("—")
        self.min_btn.setObjectName("MinBtn")
        self.min_btn.setFixedSize(22, 22)
        self.min_btn.setCursor(Qt.PointingHandCursor)
        self.min_btn.clicked.connect(self.showMinimized)
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("CloseBtn")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.request_quit)
        top.addWidget(self.min_btn)
        top.addWidget(self.close_btn)
        v.addLayout(top)

        # --- 全局区 ---
        self.global_section = QWidget()
        self.global_section.setObjectName("GlobalSection")
        global_lay = QVBoxLayout(self.global_section)
        global_lay.setContentsMargins(0, 0, 0, 0)
        global_lay.setSpacing(6)
        global_lay.addWidget(make_section_title("全局"))

        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(6)
        self.global_summary = QLabel("")
        self.global_summary.setObjectName("GlobalSummary")
        self.global_summary.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.global_summary.setTextFormat(Qt.RichText)
        gold_cap = QLabel("金币")
        gold_cap.setObjectName("GlobalCap")
        gold_cap.setStyleSheet("color: #f5c842; font-weight: 500;")
        self.gold_reel = RollingAmount("#f5c842")
        diam_cap = QLabel("钻石")
        diam_cap.setObjectName("GlobalCap")
        diam_cap.setStyleSheet("color: #5ec8f2; font-weight: 500;")
        self.diamond_reel = RollingAmount("#5ec8f2")
        summary_row.addWidget(self.global_summary, 1)
        summary_row.addWidget(gold_cap)
        summary_row.addWidget(self.gold_reel)
        summary_row.addWidget(diam_cap)
        summary_row.addWidget(self.diamond_reel)
        global_lay.addLayout(summary_row)

        bar_row = QVBoxLayout()
        bar_row.setSpacing(3)
        self.roll_progress_bar = EasedProgressBar()
        self.roll_progress_bar.point_reached.connect(self.ease_point_reached)
        self.roll_progress_bar.chest_claimed.connect(self._on_chest_claimed)
        cap = QLabel("距下次开奖")
        cap.setObjectName("Subtle")
        self.roll_bar = SegmentedRollBar()
        bar_row.addWidget(self.roll_progress_bar)
        bar_row.addWidget(cap)
        bar_row.addWidget(self.roll_bar)
        global_lay.addLayout(bar_row)

        hist_row = QVBoxLayout()
        hist_row.setSpacing(1)
        hist_cap = QLabel("开奖历史")
        hist_cap.setObjectName("RollHistCap")
        self.roll_history_lbl = QLabel("暂无开奖记录")
        self.roll_history_lbl.setObjectName("RollHist")
        self.roll_history_lbl.setWordWrap(True)
        self.roll_history_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.roll_history_lbl.setTextFormat(Qt.RichText)
        hist_row.addWidget(hist_cap)
        hist_row.addWidget(self.roll_history_lbl)
        global_lay.addLayout(hist_row)

        v.addWidget(self.global_section)
        self._drag_helper = SystemMoveFilter(self, self)
        self._drag_helper.attach(self.global_section)

        divider = QFrame()
        divider.setObjectName("Divider")
        v.addWidget(divider)

        # --- 目标树区域（独立类） ---
        self.goal_tree = GoalTreeArea(self.state, self.manager, parent=root)
        self.goal_tree.subtask_claimed.connect(self.subtask_claimed)
        self.goal_tree.goal_completed.connect(self.goal_completed)
        self.goal_tree.state_changed.connect(self.state_changed)
        v.addWidget(self.goal_tree, 1)

        # 按钮
        btns = QHBoxLayout()
        btns.setSpacing(6)
        self.task_btn = QPushButton("目标管理")
        self.task_btn.setCursor(Qt.PointingHandCursor)
        self.task_btn.clicked.connect(self.request_task_dialog)
        self.inv_btn = QPushButton("奖励背包")
        self.inv_btn.setCursor(Qt.PointingHandCursor)
        self.inv_btn.clicked.connect(self.request_inventory_dialog)
        btns.addWidget(self.task_btn)
        btns.addWidget(self.inv_btn)
        v.addLayout(btns)

        # 角标画在按钮内侧；点击穿透
        self.inv_chest_badge = _ChestBadge(self.inv_btn)

    def note_operation(self) -> None:
        """记录一次全局操作（用于近1分钟计数）。"""
        self._op_tracker.record()
        self.roll_bar.pulse_operation()

    # ---------- 右键菜单（拖动见 ui_window_drag）----------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.RightButton:
            self._show_context_menu()
            event.accept()
        else:
            super().mousePressEvent(event)

    def _show_context_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #25262e; color: #f0f0f6; border: 1px solid #404252; }"
            "QMenu::item:selected { background-color: #3a5cff; }"
        )

        s = self.state.settings

        act_top = QAction("窗口置顶", self, checkable=True)
        act_top.setChecked(bool(s.get("always_on_top", True)))
        act_top.toggled.connect(self._toggle_top)
        menu.addAction(act_top)

        act_pin = QAction("固定到所有虚拟桌面", self, checkable=True)
        act_pin.setChecked(bool(s.get("pin_all_desktops", True)))
        act_pin.setEnabled(is_windows())
        act_pin.toggled.connect(self._toggle_pin_all)
        menu.addAction(act_pin)

        menu.addSeparator()

        act_startup = QAction("开机自启", self, checkable=True)
        act_startup.setChecked(bool(s.get("startup", False)))
        act_startup.setEnabled(is_windows())
        act_startup.toggled.connect(self._toggle_startup)
        menu.addAction(act_startup)

        act_sound = QAction("音效", self, checkable=True)
        act_sound.setChecked(bool(s.get("sound_enabled", True)))
        act_sound.toggled.connect(self._toggle_sound)
        menu.addAction(act_sound)

        act_stop_bgm = QAction("停止背景音乐", self)
        act_stop_bgm.triggered.connect(self.request_stop_bgm.emit)
        menu.addAction(act_stop_bgm)

        menu.addSeparator()
        act_exit = QAction(f"退出 {APP_NAME}", self)
        act_exit.triggered.connect(self.request_quit)
        menu.addAction(act_exit)

        menu.exec(QCursor.pos())

    def _toggle_top(self, checked: bool) -> None:
        self.state.settings["always_on_top"] = checked
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        prepare_overlay_hwnd(int(self.winId()))
        save_state(self.state)
        logger.info("窗口置顶: %s", checked)

    def _toggle_pin_all(self, checked: bool) -> None:
        self.state.settings["pin_all_desktops"] = checked
        hwnd = int(self.winId())
        if checked:
            pin_window_to_all_desktops(hwnd)
        else:
            unpin_window_from_all_desktops(hwnd)
        prepare_overlay_hwnd(hwnd)
        save_state(self.state)
        logger.info("固定所有桌面: %s", checked)

    def _toggle_startup(self, checked: bool) -> None:
        self.state.settings["startup"] = checked
        set_startup(checked)
        save_state(self.state)
        logger.info("开机自启: %s", checked)

    def _toggle_sound(self, checked: bool) -> None:
        self.state.settings["sound_enabled"] = checked
        save_state(self.state)
        logger.info("音效: %s", checked)

    def _paint_ops_and_reels(self, ops_1min: int) -> None:
        set_label_html(
            self.global_summary,
            format_ops_summary_html(
                self.state.total_operations,
                ops_1min=ops_1min,
            ),
        )
        self._paint_currency_reels()

    def _paint_currency_reels(self) -> None:
        self.gold_reel.set_amount(self._currency.gold)
        self.diamond_reel.set_amount(self._currency.diamond)

    def _paint_currency_reels_scrolling(self) -> None:
        self.gold_reel.set_amount_scrolling(self._currency.gold)
        self.diamond_reel.set_amount_scrolling(self._currency.diamond)

    def kick_currency_display(self) -> None:
        """背包已变时立刻开追（不要等鼠标松开后的 UI flush）。"""
        # #region agent log
        t0 = time.perf_counter()
        # #endregion
        self._sync_currency_lanes()
        self.goal_tree.kick_detail_currency()
        if not hasattr(self, "global_summary"):
            return
        self._paint_ops_and_reels(self._op_tracker.count_recent())
        self._ensure_currency_timer()
        # #region agent log
        self._dbg_kick_n = getattr(self, "_dbg_kick_n", 0) + 1
        ms = (time.perf_counter() - t0) * 1000
        if self._dbg_kick_n <= 3 or self._dbg_kick_n % 25 == 0 or ms >= 4.0:
            from .task_manager import _agent_dbg
            g, d = self._currency_targets()
            _agent_dbg(
                "A",
                "widget.py:kick_currency_display",
                "kick cost",
                {
                    "n": self._dbg_kick_n,
                    "ms": round(ms, 2),
                    "timer": self._currency_timer.isActive(),
                    "need": not self._currency.caught_up(g, d)
                    or self.goal_tree.detail_needs_tick(),
                    "vis_g": round(g, 3),
                    "disp_g": round(self._currency.gold, 3),
                },
            )
        # #endregion

    def _currency_targets(self) -> tuple[float, float]:
        return self.state.visible_gold_diamond()

    def _ensure_currency_timer(self) -> None:
        gold, diamond = self._currency_targets()
        need = (
            not self._currency.caught_up(gold, diamond)
            or self.goal_tree.detail_needs_tick()
        )
        if need:
            if not self._currency_timer.isActive():
                self._currency_last_ts = time.monotonic()
                self._currency_timer.start()
            return
        self._currency_timer.stop()
        self._currency_last_ts = None

    def _sync_currency_lanes(self) -> None:
        """变少立刻对齐；变多则启动 50ms 追赶定时器。"""
        gold, diamond = self._currency_targets()
        self._currency.step(gold, diamond, 0.0)
        if self._currency.caught_up(gold, diamond):
            self._currency.snap_to(gold, diamond)

    def _tick_currency_display(self) -> None:
        now = time.monotonic()
        if self.is_user_moving():
            self._currency_last_ts = now
            return
        # #region agent log
        t0 = time.perf_counter()
        t_vis = time.perf_counter()
        # #endregion
        dt = 0.05
        if self._currency_last_ts is not None:
            dt = min(max(now - self._currency_last_ts, 0.0), 0.1)
        self._currency_last_ts = now
        gold, diamond = self._currency_targets()
        # #region agent log
        vis_ms = (time.perf_counter() - t_vis) * 1000
        # #endregion
        still_global = self._currency.step(gold, diamond, dt)
        still_detail = self.goal_tree.tick_detail_currency(dt)
        self._paint_currency_reels_scrolling()
        if not still_global and not still_detail:
            self._currency_timer.stop()
            self._currency_last_ts = None
        # #region agent log
        self._dbg_tick_n = getattr(self, "_dbg_tick_n", 0) + 1
        ms = (time.perf_counter() - t0) * 1000
        if self._dbg_tick_n <= 3 or self._dbg_tick_n % 20 == 0 or ms >= 4.0:
            from .task_manager import _agent_dbg
            _agent_dbg(
                "B",
                "widget.py:_tick_currency_display",
                "tick cost",
                {
                    "n": self._dbg_tick_n,
                    "ms": round(ms, 2),
                    "vis_ms": round(vis_ms, 2),
                    "dt": round(dt, 3),
                    "still_g": still_global,
                    "still_d": still_detail,
                },
            )
        # #endregion

    def _paint_global_stats(self) -> None:
        self._sync_currency_lanes()
        self._paint_ops_and_reels(self._op_tracker.count_recent())
        self._ensure_currency_timer()
        self._refresh_inv_badge()

    def _refresh_inv_badge(self) -> None:
        n = len(self.state.inventory.chests)
        self.inv_chest_badge.set_count(n)
        self._place_inv_badge()

    def _place_inv_badge(self) -> None:
        badge = self.inv_chest_badge
        if not badge.isVisible():
            return
        # 按钮内部右上角，不探出边框
        x = max(0, self.inv_btn.width() - badge.width() - _BADGE_INSET)
        y = _BADGE_INSET
        badge.move(x, y)
        badge.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "inv_chest_badge"):
            self._place_inv_badge()

    def _paint_roll_history(self) -> None:
        set_label_html(
            self.roll_history_lbl,
            format_roll_history_lines_html(
                self.state.roll_history, limit=3, compact=True,
            ),
        )

    def _running_goal_units(self) -> Optional[int]:
        """正在运行的目标：1 秒 = 1，约 10 次操作 = 1。无 ACTIVE 时返回 None。"""
        active = self.state.active_task()
        if active is None:
            return None
        if active.subtasks:
            sub = active.current_subtask()
            if sub is None:
                return None
            return int(sub.active_seconds) + int(sub.operations) // 10
        return int(active.active_seconds) + int(active.operations) // 10

    def _ease_goal_key(self) -> Optional[str]:
        """当前计入缓动条的任务/叶子，用于切目标时只加增量。"""
        active = self.state.active_task()
        if active is None:
            return None
        if active.subtasks:
            sub = active.current_subtask()
            if sub is None:
                return None
            return f"{active.id}:{sub.id}"
        return f"{active.id}:"

    def _sync_ease_chests_claimed(self) -> None:
        """条上周期与存档 ease_chests 对齐，避免重启重复领。"""
        bar = self.roll_progress_bar
        ec = self.state.ease_chests
        ec.cycle_id = bar.cycle_id
        ec.holding = bar.holding
        bar.apply_claimed(ec.claimed)

    def _on_chest_claimed(self, index: int, rarity: int) -> None:
        ec = self.state.ease_chests
        if not ec.mark_claimed(index):
            return
        self.state.inventory.add_chest(rarity)
        self.roll_progress_bar.mark_claimed(index)
        self._paint_global_stats()
        self._fly_chest_to_bag(index, rarity)
        if index == 0:
            ec.begin_next_cycle()
            self.roll_progress_bar.set_progress(
                ec.work_units,
                freeze_at_end=True,
                holding=False,
                held_cycle_id=ec.cycle_id,
                relative=True,
            )
            self.roll_progress_bar.apply_claimed(ec.claimed)
        self.chest_bagged.emit()

    def _update_roll_bar(self) -> None:
        rt = self.state.roll_runtime
        progress, span = roll_progress(self.state)
        remaining = max(0, span - progress)
        near_full_steps = remaining if 0 < remaining <= 4 else 0
        units = self._running_goal_units()
        key = self._ease_goal_key()
        if units is not None and key is not None:
            ec = self.state.ease_chests
            ec.note_running(units, key)
            self.roll_progress_bar.set_progress(
                ec.work_units,
                freeze_at_end=not ec.claimed[0],
                holding=ec.holding,
                held_cycle_id=ec.cycle_id,
                relative=True,
            )
            self._sync_ease_chests_claimed()
        chance_label = (
            f"金 {rt.gold_chance:.0%}  钻 {rt.diamond_chance:.0%}"
        )
        self.roll_bar.set_cycle(
            progress,
            span,
            rt.segment_colors,
            chance_label=chance_label,
            near_full_steps=near_full_steps,
        )

    def _fly_chest_to_bag(self, index: int, rarity: int) -> None:
        bar = self.roll_progress_bar
        start_global = bar.mapTo(self, bar.chest_center_local(index))
        badge = self.inv_chest_badge
        if badge.isVisible():
            end_global = badge.mapTo(
                self,
                QPoint(badge.width() // 2, badge.height() // 2),
            )
        else:
            end_global = self.inv_btn.mapTo(
                self,
                QPoint(self.inv_btn.width() // 2, self.inv_btn.height() // 2),
            )
        flyer = _FlyingChest(rarity, self)
        half = _FLY_SIZE // 2
        start = QPoint(start_global.x() - half, start_global.y() - half)
        end = QPoint(end_global.x() - half, end_global.y() - half)
        flyer.move(start)
        flyer.show()
        flyer.raise_()
        anim = QPropertyAnimation(flyer, b"pos", flyer)
        anim.setDuration(_FLY_MS)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _cleanup() -> None:
            flyer.hide()
            flyer.deleteLater()

        anim.finished.connect(_cleanup)
        flyer._anim = anim  # type: ignore[attr-defined]
        anim.start()

    def refresh_roll_meta(self) -> None:
        """仅更新进度条概率/颜色元数据（10 分钟重抽后调用）。"""
        self._update_roll_bar()

    def _flash_roll_bar(self) -> None:
        self.roll_bar.set_flash(True)

        def _off() -> None:
            self.roll_bar.set_flash(False)

        QTimer.singleShot(300, _off)
        QTimer.singleShot(600, lambda: self.roll_bar.set_flash(True))
        QTimer.singleShot(900, _off)

    def refresh_stats(self, *, roll_changed: bool = False, reward: Optional[Reward] = None) -> None:
        """按键后的轻量刷新：只改数字，不重建目标树。"""
        self._paint_global_stats()
        self._update_roll_bar()

        if roll_changed:
            self._paint_roll_history()
            if reward is not None:
                self._flash_roll_bar()

        since = self.state.since_roll
        self.goal_tree.refresh_stats(
            since_gold=since.gold,
            since_diamond=since.diamond,
        )

    # ---------- 刷新 ----------
    def refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self._refresh()
        finally:
            self._refreshing = False

    def _refresh(self) -> None:
        self._paint_global_stats()
        self._update_roll_bar()
        self._paint_roll_history()
        since = self.state.since_roll
        self.goal_tree.refresh(
            since_gold=since.gold,
            since_diamond=since.diamond,
        )

    def is_user_moving(self) -> bool:
        return self._window_dragging

    def begin_user_move(self) -> None:
        """交给系统拖动；Windows 上不用 QWidget.move()。"""
        self._window_dragging = True
        self._drag_end_timer.start(2000)
        handle = self.windowHandle()
        if handle is None:
            return
        try:
            handle.startSystemMove()
        except Exception:
            logger.debug("startSystemMove 失败", exc_info=True)

    def end_user_move(self) -> None:
        self._drag_end_timer.stop()
        grabber = QWidget.mouseGrabber()
        if grabber is not None:
            grabber.releaseMouse()
        self._window_dragging = False

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if self._window_dragging:
            self._drag_end_timer.start(400)

    def _refresh_runtime(self) -> None:
        """仅刷新与时间相关的字段，避免整窗口频繁重绘。"""
        self.manager.tick_active_time()
        if self.is_user_moving():
            return
        if QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton:
            return
        self._tick_count = getattr(self, "_tick_count", 0) + 1
        if self._tick_count % 60 == 0:
            logger.debug("运行中 (ops=%d)", self.state.total_operations)
        self._paint_global_stats()
        self._update_roll_bar()

        if self.state.active_task() is None:
            return

        since = self.state.since_roll
        self.goal_tree.refresh_stats(
            since_gold=since.gold,
            since_diamond=since.diamond,
        )

    def nativeEvent(self, eventType, message):
        self.manager.power_monitor.handle_native_event(eventType, message)
        msg_id = win32_message_id(eventType, message)
        if msg_id == WM_ENTERSIZEMOVE:
            self._window_dragging = True
            self._drag_end_timer.start(2000)
        elif msg_id == WM_EXITSIZEMOVE:
            self.end_user_move()
        return super().nativeEvent(eventType, message)

    # ---------- 显示时初始化窗口属性 ----------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.manager.power_monitor.install_on(self)
        hwnd = int(self.winId())
        if self.state.settings.get("pin_all_desktops", True):
            pin_window_to_all_desktops(hwnd)
        prepare_overlay_hwnd(hwnd)
        if hasattr(self, "inv_chest_badge"):
            QTimer.singleShot(0, self._place_inv_badge)