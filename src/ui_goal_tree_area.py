"""目标树区域：滚动树、选中/悬停、详情面板、子目标操作。

从 FloatingWidget 拆出，独立管理树相关状态与几何同步。
对外协作：state_changed（请求存档+刷新）、subtask_claimed（完成领奖通知）。
"""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QRect, QTimer, Signal
from PySide6.QtGui import QCursor, QHelpEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyleOptionSlider,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .currency_display import CurrencyDisplay
from .goal_actions import try_complete_goal, try_delete_goal
from .models import AppState, Reward, Subtask, Task, TaskStatus
from .storage import save_state
from .task_manager import TaskManager
from .ui_confirm import ask_yes_no
from .ui_goal_tree_shared import (
    completion_bonus_gold,
    goal_detail_actions_signature,
    make_empty_subtasks_hint,
    make_goal_block,
    make_goal_root_row,
    make_tree_node_row,
    prompt_decompose_titles,
    subtree_has_unclaimed,
    visible_subtask_structure,
    wrap_indented_subtask_row,
)
from .ui_odometer import RollingAmount
from .ui_qt import (
    clear_layout,
    drain_layout_widgets,
    make_section_title,
    pin_html_label_width,
    set_label_html,
    set_label_text,
)
from .ui_task_tree import (
    SubtaskActionCallbacks,
    TreeRow,
    append_subtask_detail_actions,
    apply_goal_block_hover,
    apply_goal_block_ui,
    apply_subtask_block_ui,
)
from .ui_text import (
    format_goal_ops_html,
    format_goal_root_line_html,
    format_subgoals_focus_hint_html,
    format_tree_detail_html,
    format_tree_node_html,
)


class GoalTreeArea(QWidget):
    """目标树：顶层目标列表 + 子目标目录 + 详情面板 + 子目标操作。"""

    state_changed = Signal()
    subtask_claimed = Signal(str, object)  # (title, Reward)
    goal_completed = Signal()

    def __init__(
        self,
        state: AppState,
        manager: TaskManager,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.state = state
        self.manager = manager

        self._subgoal_structure_sig: tuple | None = None
        self._subgoal_line_labels: dict[str, QLabel] = {}
        self._tree_row_widgets: dict[str, TreeRow] = {}
        self._subtask_blocks: dict[str, QWidget] = {}
        self._goal_root_rows: dict[str, TreeRow] = {}
        self._goal_root_lines: dict[str, QLabel] = {}
        self._goal_blocks: dict[str, QWidget] = {}
        self._expanded_goal_ids: set[str] = set()
        self._sub_add_parent_id: Optional[str] = None
        self._selected_task_id: str = ""
        self._selected_subtask_id: str = ""
        self._goal_detail_actions_sig: tuple | None = None
        self._hovered_goal_id: str = ""
        self._state_sync_pending = False
        self._local_refresh_pending = False
        self._geometry_sync_pending = False
        self._geometry_syncing = False
        self._dbg_logged_c: set[str] = set()
        self._dbg_stale_dot: set[str] = set()
        self._detail_currency = CurrencyDisplay(0.0, 0.0)
        self._detail_focus_key = ""
        # 折叠/展开时的滚动锚定：记录被点行的视口内纵坐标，重建后恢复
        self._tree_scroll_anchor: Optional[tuple[str, int]] = None

        self._build_ui()
        self.refresh()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # 目标树（父目标 + 子目标目录）
        self.task_tree_section = QWidget()
        self.task_tree_section.setObjectName("TaskTreeSection")
        self.task_tree_section.setSizePolicy(
            QSizePolicy.Preferred,
            QSizePolicy.Expanding,
        )
        tree_lay = QVBoxLayout(self.task_tree_section)
        tree_lay.setContentsMargins(0, 0, 0, 0)
        tree_lay.setSpacing(6)
        tree_lay.addWidget(make_section_title("目标"))

        self.subgoals_scroll = QScrollArea()
        self.subgoals_scroll.setObjectName("SubGoalScroll")
        self.subgoals_scroll.setFrameShape(QFrame.NoFrame)
        self.subgoals_scroll.setWidgetResizable(False)
        # 最小高度保持在窗口 600 预算内，避免固定部分(顶栏+全局+按钮)把窗口顶高
        self.subgoals_scroll.setMinimumHeight(160)
        self.subgoals_scroll.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding,
        )
        self.subgoals_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.subgoals_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.subgoals_container = QWidget()
        self.subgoals_container.setObjectName("SubGoalContainer")
        self.subgoals_container.setAutoFillBackground(True)
        self.subgoals_scroll.viewport().setObjectName("SubGoalViewport")
        self.subgoals_scroll.viewport().setAutoFillBackground(True)
        self.subgoals_layout = QVBoxLayout(self.subgoals_container)
        self.subgoals_layout.setContentsMargins(0, 0, 0, 0)
        self.subgoals_layout.setSpacing(2)
        self.subgoals_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.subgoals_hbar = QScrollBar(Qt.Orientation.Horizontal)
        self.subgoals_hbar.setObjectName("SubGoalHBar")
        self.subgoals_hbar.setCursor(Qt.PointingHandCursor)
        self.subgoals_hbar.setAttribute(Qt.WA_Hover, True)
        self.subgoals_hbar.setAutoFillBackground(True)
        self.subgoals_hbar.installEventFilter(self)
        self.subgoals_hbar.hide()
        self.subgoals_vbar = self.subgoals_scroll.verticalScrollBar()
        self.subgoals_vbar.setAttribute(Qt.WA_Hover, True)
        self.subgoals_vbar.setAutoFillBackground(True)
        self.subgoals_vbar.installEventFilter(self)
        inner_hbar = self.subgoals_scroll.horizontalScrollBar()
        inner_hbar.rangeChanged.connect(self._sync_subgoals_hbar)
        inner_hbar.valueChanged.connect(self._on_subgoals_inner_hscroll)
        self.subgoals_hbar.valueChanged.connect(self._on_subgoals_outer_hscroll)

        self.subgoals_scroll.viewport().installEventFilter(self)
        self.subgoals_scroll.setWidget(self.subgoals_container)
        tree_lay.addWidget(self.subgoals_scroll, 1)
        tree_lay.addWidget(self.subgoals_hbar)

        self.subgoals_hint = QLabel("")
        self.subgoals_hint.setObjectName("SubGoalHint")
        self.subgoals_hint.setWordWrap(True)
        self.subgoals_hint.hide()
        tree_lay.addWidget(self.subgoals_hint)

        self.subgoals_empty = QLabel("还没有目标")
        self.subgoals_empty.setObjectName("SubGoalList")
        self.subgoals_empty.setWordWrap(True)
        tree_lay.addWidget(self.subgoals_empty)

        self.subgoal_actions = QWidget()
        self.subgoal_actions.setObjectName("SubGoalActions")
        self.subgoal_actions.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        actions_layout = QVBoxLayout(self.subgoal_actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)

        self.subgoal_add_title = make_section_title("添加子目标")
        self.subgoal_add_title.setWordWrap(False)
        actions_layout.addWidget(self.subgoal_add_title)

        add_sub_row = QHBoxLayout()
        add_sub_row.setSpacing(4)
        self.subgoal_input = QLineEdit()
        self.subgoal_input.setObjectName("SubGoalInput")
        self.subgoal_input.setPlaceholderText("子目标标题…")
        self.subgoal_input.returnPressed.connect(self._on_add_subgoal)
        add_sub_row.addWidget(self.subgoal_input, 1)
        default_min = max(
            1, int(self.state.settings.get("subtask_default_target_minutes", 10)),
        )
        self.subgoal_min_spin = QSpinBox()
        self.subgoal_min_spin.setObjectName("SubtaskMinSpin")
        self.subgoal_min_spin.setRange(1, 999)
        self.subgoal_min_spin.setValue(default_min)
        self.subgoal_min_spin.setPrefix("最少 ")
        self.subgoal_min_spin.setSuffix(" 分")
        self.subgoal_min_spin.setToolTip("新目标需运行的最短时间（达标后可完成领奖）")
        self.subgoal_min_spin.setFixedWidth(96)
        self.subgoal_min_spin.valueChanged.connect(self._on_subtask_min_changed)
        add_sub_row.addWidget(self.subgoal_min_spin)
        self.sub_add_btn = QPushButton("添加")
        self.sub_add_btn.setObjectName("SubAddBtn")
        self.sub_add_btn.setCursor(Qt.PointingHandCursor)
        self.sub_add_btn.clicked.connect(self._on_add_subgoal)
        add_sub_row.addWidget(self.sub_add_btn)
        self.add_sub_row = QWidget()
        self.add_sub_row.setLayout(add_sub_row)
        actions_layout.addWidget(self.add_sub_row)

        self.goal_detail_panel = QWidget()
        self.goal_detail_panel.setObjectName("GoalDetailPanel")
        # 面板内容随选中变化；忽略其垂直尺寸贡献，让外层布局分配面板高度，
        # 否则点击目标树后详情面板的最小高度会把整个悬浮窗顶高
        self.goal_detail_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        detail_lay = QVBoxLayout(self.goal_detail_panel)
        detail_lay.setContentsMargins(8, 6, 8, 6)
        detail_lay.setSpacing(4)

        self.goal_detail_title = QLabel("")
        self.goal_detail_title.setObjectName("GoalDetailTitle")
        self.goal_detail_title.setWordWrap(True)
        detail_lay.addWidget(self.goal_detail_title)

        currency_row = QHBoxLayout()
        currency_row.setContentsMargins(0, 0, 0, 0)
        currency_row.setSpacing(6)
        self.goal_detail_ops = QLabel("")
        self.goal_detail_ops.setObjectName("GoalDetailStats")
        self.goal_detail_ops.setTextFormat(Qt.RichText)
        gold_cap = QLabel("金")
        gold_cap.setObjectName("GlobalCap")
        gold_cap.setStyleSheet("color: #f5c842; font-weight: 700; font-size: 13px;")
        self.detail_gold_reel = RollingAmount("#f5c842")
        diam_cap = QLabel("钻")
        diam_cap.setObjectName("GlobalCap")
        diam_cap.setStyleSheet("color: #5ec8f2; font-weight: 700; font-size: 13px;")
        self.detail_diamond_reel = RollingAmount("#5ec8f2")
        currency_row.addWidget(self.goal_detail_ops)
        currency_row.addWidget(gold_cap)
        currency_row.addWidget(self.detail_gold_reel)
        currency_row.addWidget(diam_cap)
        currency_row.addWidget(self.detail_diamond_reel)
        currency_row.addStretch(1)
        self.goal_detail_currency_row = QWidget()
        self.goal_detail_currency_row.setLayout(currency_row)
        detail_lay.addWidget(self.goal_detail_currency_row)

        self.goal_detail_stats = QLabel("")
        self.goal_detail_stats.setObjectName("GoalDetailStats")
        self.goal_detail_stats.setWordWrap(True)
        self.goal_detail_stats.setTextFormat(Qt.RichText)
        detail_lay.addWidget(self.goal_detail_stats)

        self.goal_detail_btn_row = QWidget()
        self.goal_detail_btn_row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.goal_detail_btn_lay = QHBoxLayout(self.goal_detail_btn_row)
        self.goal_detail_btn_lay.setContentsMargins(0, 0, 0, 0)
        self.goal_detail_btn_lay.setSpacing(6)
        detail_lay.addWidget(self.goal_detail_btn_row)

        self.goal_detail_panel.hide()

        v.addWidget(self.task_tree_section, 1)

        self.subgoal_actions.hide()
        v.addWidget(self.subgoal_actions)
        v.addWidget(self.goal_detail_panel)

        self.goal_add_btn = QPushButton("新建顶层目标")
        self.goal_add_btn.setObjectName("GoalAddBtn")
        self.goal_add_btn.setCursor(Qt.PointingHandCursor)
        self.goal_add_btn.setToolTip("创建与当前列表并列的新目标（非子目标）")
        self.goal_add_btn.clicked.connect(self._on_add_goal)
        v.addWidget(self.goal_add_btn)

    def refresh(
        self,
        *,
        since_gold: float | None = None,
        since_diamond: float | None = None,
    ) -> None:
        """全量刷新（结构变化时）。"""
        since = self.state.since_roll
        self._apply_task_section(
            self.state.active_task(),
            since_gold=since.gold if since_gold is None else since_gold,
            since_diamond=since.diamond if since_diamond is None else since_diamond,
        )

    def refresh_stats(
        self,
        *,
        since_gold: float | None = None,
        since_diamond: float | None = None,
    ) -> None:
        """轻量刷新：只改统计文本，不重建树/按钮。"""
        since = self.state.since_roll
        self._refresh_task_ops_ui(
            since_gold=since.gold if since_gold is None else since_gold,
            since_diamond=since.diamond if since_diamond is None else since_diamond,
            allow_action_rebuild=False,
        )

    def _sync_subgoals_hbar(self, *_args) -> None:
        if not hasattr(self, "subgoals_hbar"):
            return
        inner = self.subgoals_scroll.horizontalScrollBar()
        need_hbar = (
            self.subgoals_scroll.isVisible() and inner.maximum() > 0
        )
        self.subgoals_hbar.blockSignals(True)
        self.subgoals_hbar.setRange(inner.minimum(), inner.maximum())
        self.subgoals_hbar.setPageStep(max(inner.pageStep(), 1))
        self.subgoals_hbar.setSingleStep(max(inner.singleStep(), 1))
        self.subgoals_hbar.setValue(inner.value())
        self.subgoals_hbar.blockSignals(False)
        # 仅在需要横向滚动时显示，避免显隐抖动触发视口 Resize 死循环
        if self.subgoals_hbar.isVisible() != need_hbar:
            self.subgoals_hbar.setVisible(need_hbar)
        self.subgoals_hbar.setEnabled(need_hbar)

    def _on_subgoals_inner_hscroll(self, value: int) -> None:
        if self.subgoals_hbar.value() != value:
            self.subgoals_hbar.blockSignals(True)
            self.subgoals_hbar.setValue(value)
            self.subgoals_hbar.blockSignals(False)

    def _on_subgoals_outer_hscroll(self, value: int) -> None:
        inner = self.subgoals_scroll.horizontalScrollBar()
        if inner.value() != value:
            inner.setValue(value)

    def _widget_goals(self) -> list[Task]:
        goals = [
            t
            for t in self.state.tasks
            if t.status in (TaskStatus.ACTIVE, TaskStatus.PAUSED)
        ]
        return sorted(goals, key=lambda t: float(t.created_at or 0), reverse=True)

    @staticmethod
    def _sub_line_key(task_id: str, sub_id: str) -> str:
        return f"{task_id}:{sub_id}"

    def _sync_expanded_goals(self, active: Task | None) -> None:
        valid = {t.id for t in self._widget_goals()}
        self._expanded_goal_ids &= valid

    def _is_goal_expanded(self, task_id: str) -> bool:
        return task_id in self._expanded_goal_ids

    def _reveal_subtask_path(self, task_id: str, subtask_id: str) -> None:
        """展开顶层目标并展开到子项的路径（不全树展开）。"""
        self._expanded_goal_ids.add(task_id)
        self.manager.expand_subtask_path(task_id, subtask_id)

    def _request_state_sync(self) -> None:
        """延迟到点击处理结束后再存档+刷新，避免销毁当前按钮导致卡死。"""
        self._goal_detail_actions_sig = None
        if self._state_sync_pending:
            return
        self._state_sync_pending = True
        QTimer.singleShot(0, self._flush_state_sync)

    def _flush_state_sync(self) -> None:
        self._state_sync_pending = False
        self.state_changed.emit()

    def _request_local_refresh(self) -> None:
        """仅刷新悬浮窗，不写盘。"""
        if self._local_refresh_pending:
            return
        self._local_refresh_pending = True
        QTimer.singleShot(0, self._flush_local_refresh)

    def _flush_local_refresh(self) -> None:
        self._local_refresh_pending = False
        self.refresh()

    def _measure_subgoals_content_size(self) -> tuple[int, int]:
        """按子控件 sizeHint 累加，避免布局未激活时 minimumSize 变成 1px 把树裁没。"""
        self.subgoals_layout.activate()
        spacing = max(self.subgoals_layout.spacing(), 0)
        total_w = 1
        total_h = 0
        counted = 0
        for i in range(self.subgoals_layout.count()):
            item = self.subgoals_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if widget is None or not widget.isVisible():
                continue
            hint = widget.minimumSizeHint()
            if hint.width() <= 0 or hint.height() <= 0:
                hint = widget.sizeHint()
            total_w = max(
                total_w,
                hint.width(),
                widget.minimumWidth(),
                1,
            )
            total_h += max(hint.height(), 22)
            counted += 1
        if counted:
            total_h += spacing * max(0, counted - 1)
        else:
            total_h = 1
        row_floor = 22 * max(len(self._goal_blocks) + len(self._tree_row_widgets), 0)
        return total_w, max(total_h, row_floor, 1)

    def _request_geometry_sync(self) -> None:
        """延迟几何同步，避免 Resize 事件重入卡死。"""
        if self._geometry_sync_pending:
            return
        self._geometry_sync_pending = True
        QTimer.singleShot(0, self._flush_geometry_sync)

    def _flush_geometry_sync(self) -> None:
        self._geometry_sync_pending = False
        self._sync_subgoals_container_geometry()

    def _sync_subgoals_container_geometry(self) -> None:
        """按内容实际宽度撑开容器，超出时出现横向/纵向滚动条。"""
        if self._geometry_syncing:
            return
        if not self.subgoals_scroll.isVisible():
            self.subgoals_hbar.hide()
            return
        self._geometry_syncing = True
        try:
            content_w, content_h = self._measure_subgoals_content_size()
            viewport = self.subgoals_scroll.viewport()
            vw = max(viewport.width(), 1)
            w = max(content_w, vw)
            h = max(content_h, 1)
            if (
                self.subgoals_container.width() != w
                or self.subgoals_container.height() != h
            ):
                self.subgoals_container.resize(w, h)
            self._sync_subgoals_hbar()
        finally:
            self._geometry_syncing = False

    def _goal_id_for_widget(self, widget: QWidget) -> str:
        for task_id, block in self._goal_blocks.items():
            if widget is block or block.isAncestorOf(widget):
                return task_id
        return ""

    def _apply_goal_hover_ui(self) -> None:
        for task_id, block in self._goal_blocks.items():
            apply_goal_block_hover(
                block,
                hovered=task_id == self._hovered_goal_id,
            )

    def _sync_goal_block_hover(self) -> None:
        pos = QCursor.pos()
        hovered_id = ""
        for task_id, block in self._goal_blocks.items():
            if not block.isVisible():
                continue
            if block.rect().contains(block.mapFromGlobal(pos)):
                hovered_id = task_id
                break
        if hovered_id != self._hovered_goal_id:
            self._hovered_goal_id = hovered_id
            self._apply_goal_hover_ui()

    def _install_goal_hover_filters(self, block: QWidget) -> None:
        # 只装在 GoalBlock 上，不要递归装到每个子控件（否则悬停事件风暴会卡死）
        block.setAttribute(Qt.WA_Hover, True)
        block.installEventFilter(self)

    @staticmethod
    def _scrollbar_handle_rect(bar: QScrollBar) -> QRect:
        opt = QStyleOptionSlider()
        bar.initStyleOption(opt)
        return bar.style().subControlRect(
            QStyle.ComplexControl.CC_ScrollBar,
            opt,
            QStyle.SubControl.SC_ScrollBarSlider,
            bar,
        )

    def _scrollbar_handle_tooltip(self, bar: QScrollBar) -> str:
        if bar is self.subgoals_hbar:
            return "左右拖动查看完整标题"
        return "上下滚动目标列表"

    def _show_scrollbar_handle_tooltip(self, bar: QScrollBar, event: QHelpEvent) -> bool:
        if not bar.isEnabled():
            QToolTip.hideText()
            return True
        handle = self._scrollbar_handle_rect(bar)
        if handle.contains(event.position().toPoint()):
            QToolTip.showText(
                event.globalPosition().toPoint(),
                self._scrollbar_handle_tooltip(bar),
                bar,
                handle,
            )
            return True
        QToolTip.hideText()
        return True

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if isinstance(obj, QWidget):
            task_id = self._goal_id_for_widget(obj)
            if task_id:
                if event.type() in (
                    QEvent.Type.Enter,
                    QEvent.Type.HoverEnter,
                ):
                    if self._hovered_goal_id != task_id:
                        self._hovered_goal_id = task_id
                        self._apply_goal_hover_ui()
                elif event.type() in (
                    QEvent.Type.Leave,
                    QEvent.Type.HoverLeave,
                ):
                    QTimer.singleShot(0, self._sync_goal_block_hover)
        hbar = getattr(self, "subgoals_hbar", None)
        vbar = getattr(self, "subgoals_vbar", None)
        if hbar is not None and vbar is not None and obj in (hbar, vbar):
            if event.type() == QEvent.Type.ToolTip:
                if isinstance(event, QHelpEvent):
                    return self._show_scrollbar_handle_tooltip(obj, event)
            elif event.type() == QEvent.Type.Leave:
                QToolTip.hideText()
        if (
            obj is self.subgoals_scroll.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._request_geometry_sync()
        return super().eventFilter(obj, event)

    def _apply_tree_selection_chrome(self) -> None:
        """选中/聚焦描边（不更新文本，开销低）。"""
        for task_id, row in self._goal_root_rows.items():
            row.set_row_selected(
                task_id == self._selected_task_id and not self._selected_subtask_id
            )
        for key, row in self._tree_row_widgets.items():
            task_id, sub_id = key.split(":", 1)
            row.set_row_selected(
                task_id == self._selected_task_id
                and sub_id == self._selected_subtask_id
            )
            task = self.manager.get(task_id)
            active_path: frozenset[str] = frozenset()
            if task is not None and task.status == TaskStatus.ACTIVE:
                active_path = task.active_focus_path_ids()
            was_focused = bool(row.property("subcurrent"))
            in_path = sub_id in active_path
            row.set_row_focused(in_path)
            # #region agent log
            if was_focused and not in_path:
                from .task_manager import _agent_dbg
                _agent_dbg(
                    "H",
                    "ui_goal_tree_area.py:_apply_tree_selection_chrome",
                    "cleared stale row focus chrome",
                    {
                        "task_id": task_id,
                        "sub_id": sub_id,
                        "was_focused": was_focused,
                        "in_active_path": in_path,
                    },
                )
            # #endregion

        for key, sub_block in self._subtask_blocks.items():
            task_id, sub_id = key.split(":", 1)
            task = self.manager.get(task_id)
            if task is None:
                continue
            active_path = task.active_focus_path_ids()
            is_sub_selected = (
                task_id == self._selected_task_id
                and sub_id == self._selected_subtask_id
                and bool(self._selected_subtask_id)
            )
            apply_subtask_block_ui(
                sub_block,
                selected=is_sub_selected,
                focused=sub_id in active_path,
            )

        for task in self._widget_goals():
            block = self._goal_blocks.get(task.id)
            if block is not None:
                goal_selected = (
                    task.id == self._selected_task_id
                    and not self._selected_subtask_id
                )
                apply_goal_block_ui(
                    block,
                    is_running=task.status == TaskStatus.ACTIVE,
                    selected=goal_selected,
                    focused=bool(task.active_focus_path_ids()),
                )
                apply_goal_block_hover(
                    block,
                    hovered=task.id == self._hovered_goal_id,
                )

    def _refresh_tree_labels(self, *, stats_only: bool = False) -> None:
        width_changed = False
        for task in self._widget_goals():
            editable = task.status == TaskStatus.ACTIVE
            active_path = task.active_focus_path_ids() if editable else frozenset()

            if not stats_only:
                line = self._goal_root_lines.get(task.id)
                if line is not None:
                    set_label_html(
                        line,
                        format_goal_root_line_html(
                            task,
                            selected=(
                                task.id == self._selected_task_id
                                and not self._selected_subtask_id
                            ),
                            is_running=task.status == TaskStatus.ACTIVE,
                            muted=task.status == TaskStatus.PAUSED,
                        ),
                    )
                    if pin_html_label_width(line):
                        width_changed = True

            for sub_key, sub_line in self._subgoal_line_labels.items():
                if not sub_key.startswith(f"{task.id}:"):
                    continue
                sid = sub_key.split(":", 1)[1]
                sub = task.find_subtask(sid)
                if sub is None:
                    continue
                is_selected = (
                    task.id == self._selected_task_id
                    and sid == self._selected_subtask_id
                )
                is_current = sid in active_path
                if stats_only and not (is_selected or is_current):
                    # #region agent log
                    html = sub_line.text()
                    stale_dot = (not sub.done) and ("●" in html)
                    if stale_dot:
                        stale_key = f"{task.id}:{sid}"
                        if stale_key not in self._dbg_stale_dot:
                            self._dbg_stale_dot.add(stale_key)
                            from .task_manager import _agent_dbg
                            _agent_dbg(
                                "F",
                                "ui_goal_tree_area.py:_refresh_tree_labels",
                                "stats_only skipped row still showing filled dot",
                                {
                                    "task_id": task.id,
                                    "sub_id": sid,
                                    "title": sub.title,
                                    "is_selected": is_selected,
                                    "is_current": is_current,
                                    "current_subtask_id": task.current_subtask_id,
                                    "in_active_path": sid in active_path,
                                },
                            )
                    # #endregion
                    if not stale_dot:
                        continue
                show_stats = is_selected or is_current
                set_label_html(
                    sub_line,
                    format_tree_node_html(
                        sub,
                        selected=is_selected,
                        is_current=is_current,
                        expanded=self.manager.is_subtask_expanded(task.id, sub.id),
                        show_stats=show_stats,
                    ),
                )
                if pin_html_label_width(sub_line):
                    width_changed = True

        if width_changed:
            self._sync_subgoals_container_geometry()

    def _detail_earned_targets(self) -> tuple[float, float]:
        task = self.manager.get(self._selected_task_id)
        if task is None:
            return 0.0, 0.0
        if not self._selected_subtask_id:
            return task.earned_totals()
        sub = task.find_subtask(self._selected_subtask_id)
        if sub is None:
            return 0.0, 0.0
        if sub.is_container():
            return sub.rollup_earned()
        return float(sub.earned_gold), float(sub.earned_diamond)

    def _paint_detail_ops_and_reels(self) -> None:
        task = self.manager.get(self._selected_task_id)
        ops = 0
        if task is not None:
            if not self._selected_subtask_id:
                ops = task.rollup_operations()
            else:
                sub = task.find_subtask(self._selected_subtask_id)
                if sub is not None:
                    ops = (
                        sub.rollup_operations()
                        if sub.is_container()
                        else sub.operations
                    )
        set_label_html(self.goal_detail_ops, format_goal_ops_html(ops))
        self.detail_gold_reel.set_amount(self._detail_currency.gold)
        self.detail_diamond_reel.set_amount(self._detail_currency.diamond)

    def kick_detail_currency(self) -> None:
        """开奖后追详情金/钻；换选中时由详情刷新 snap。"""
        if not self.goal_detail_panel.isVisible():
            return
        gold, diamond = self._detail_earned_targets()
        self._detail_currency.step(gold, diamond, 0.0)
        self._paint_detail_ops_and_reels()

    def detail_needs_tick(self) -> bool:
        if not self.goal_detail_panel.isVisible():
            return False
        gold, diamond = self._detail_earned_targets()
        return not self._detail_currency.caught_up(gold, diamond)

    def tick_detail_currency(self, dt: float) -> bool:
        if not self.goal_detail_panel.isVisible():
            return False
        gold, diamond = self._detail_earned_targets()
        still = self._detail_currency.step(gold, diamond, dt)
        # 追赶时不要每 50ms 重写 ops HTML
        self.detail_gold_reel.set_amount_scrolling(self._detail_currency.gold)
        self.detail_diamond_reel.set_amount_scrolling(self._detail_currency.diamond)
        return still

    def _refresh_goal_detail_stats_only(
        self,
        *,
        since_gold: float = 0.0,
        since_diamond: float = 0.0,
    ) -> None:
        if not self._selected_task_id or not self.goal_detail_panel.isVisible():
            return
        task = self.manager.get(self._selected_task_id)
        if task is None:
            return
        sub: Subtask | None = None
        if self._selected_subtask_id:
            sub = task.find_subtask(self._selected_subtask_id)
        self._paint_detail_ops_and_reels()
        set_label_html(
            self.goal_detail_stats,
            format_tree_detail_html(
                task,
                sub,
                since_roll_gold=since_gold,
                since_roll_diamond=since_diamond,
                completion_bonus=completion_bonus_gold(self.state),
                omit_compact_currency=True,
            ),
        )

    def _refresh_task_ops_ui(
        self,
        *,
        since_gold: float = 0.0,
        since_diamond: float = 0.0,
        allow_action_rebuild: bool = False,
    ) -> None:
        """按键/计时后仅刷新统计文本，默认不重建按钮。"""
        self._refresh_tree_labels(stats_only=not allow_action_rebuild)
        if not self._selected_task_id or not self.goal_detail_panel.isVisible():
            return
        task = self.manager.get(self._selected_task_id)
        if task is None:
            return
        sub: Subtask | None = None
        if self._selected_subtask_id:
            sub = task.find_subtask(self._selected_subtask_id)
        if (
            allow_action_rebuild
            and goal_detail_actions_signature(task, sub)
            != self._goal_detail_actions_sig
        ):
            self._refresh_goal_detail_panel(
                since_gold=since_gold,
                since_diamond=since_diamond,
            )
            return
        # #region agent log
        if (
            sub is not None
            and sub.is_leaf()
            and sub.time_target_met()
            and not sub.can_finish()
            and sub.id not in self._dbg_logged_c
        ):
            self._dbg_logged_c.add(sub.id)
            from .task_manager import _agent_dbg
            _agent_dbg(
                "C",
                "ui_goal_tree_area.py:_refresh_task_ops_ui",
                "time met but can_finish false during stats refresh",
                {
                    "allow_action_rebuild": allow_action_rebuild,
                    "sub_id": sub.id,
                    "title": sub.title,
                    "done": sub.done,
                    "claimed": sub.rewards_claimed,
                    "can_finish": sub.can_finish(),
                    "task_status": task.status.value,
                },
            )
        # #endregion
        self._refresh_goal_detail_stats_only(
            since_gold=since_gold,
            since_diamond=since_diamond,
        )

    def _on_goal_toggle_fold(self, task_id: str) -> None:
        if task_id in self._expanded_goal_ids:
            self._expanded_goal_ids.discard(task_id)
        else:
            self._expanded_goal_ids.add(task_id)
        self._remember_tree_scroll_anchor(task_id)
        self._request_local_refresh()

    def _remember_tree_scroll_anchor(self, task_id: str) -> None:
        """记录被折叠/展开行的视口内纵坐标，重建后按此恢复滚动位置。"""
        row = self._goal_root_rows.get(task_id)
        if row is None:
            self._tree_scroll_anchor = None
            return
        try:
            y = row.mapTo(self.subgoals_scroll.viewport(), QPoint(0, 0)).y()
        except RuntimeError:
            self._tree_scroll_anchor = None
            return
        self._tree_scroll_anchor = (task_id, y)

    def _restore_tree_scroll_anchor(self) -> None:
        """重建后把被点行放回原视口位置（补偿滚动条值），消除列表跳动。

        两段延迟：重建期间的几何尚未落定（容器 resize 在事件队列后段），
        同步设值会被钳到 0；延后一拍后重新读取行位置再补偿。
        """
        anchor = self._tree_scroll_anchor
        if anchor is None:
            return
        self._tree_scroll_anchor = None
        task_id, vp_y = anchor
        QTimer.singleShot(0, lambda: self._apply_tree_scroll_anchor(task_id, vp_y))

    def _apply_tree_scroll_anchor(self, task_id: str, vp_y: int) -> None:
        row = self._goal_root_rows.get(task_id)
        if row is None:
            return
        try:
            now_y = row.mapTo(self.subgoals_scroll.viewport(), QPoint(0, 0)).y()
        except RuntimeError:
            return
        delta = now_y - vp_y
        if delta == 0:
            return
        vbar = self.subgoals_vbar
        vbar.setValue(vbar.value() + delta)

    def _clear_tree_layout(self) -> None:
        self._goal_root_rows.clear()
        self._goal_root_lines.clear()
        self._subtask_blocks.clear()
        self._goal_blocks.clear()
        self._hovered_goal_id = ""
        self._goal_detail_actions_sig = None
        drain_layout_widgets(self.subgoals_layout)

    def _refresh_goal_detail_panel(
        self,
        *,
        since_gold: float = 0.0,
        since_diamond: float = 0.0,
    ) -> None:
        task_id = self._selected_task_id
        if not task_id:
            self.goal_detail_panel.hide()
            self._goal_detail_actions_sig = None
            return
        task = self.manager.get(task_id)
        if task is None:
            self.goal_detail_panel.hide()
            self._goal_detail_actions_sig = None
            return

        self.goal_detail_panel.show()
        sub: Subtask | None = None
        if self._selected_subtask_id:
            sub = task.find_subtask(self._selected_subtask_id)

        title_text = sub.title if sub is not None else task.title
        set_label_text(self.goal_detail_title, title_text)
        key = f"{task.id}:{self._selected_subtask_id}"
        gold, diamond = self._detail_earned_targets()
        if key != self._detail_focus_key:
            self._detail_currency.snap_to(gold, diamond)
            self._detail_focus_key = key
        else:
            self._detail_currency.step(gold, diamond, 0.0)
        self._paint_detail_ops_and_reels()
        set_label_html(
            self.goal_detail_stats,
            format_tree_detail_html(
                task,
                sub,
                since_roll_gold=since_gold,
                since_roll_diamond=since_diamond,
                completion_bonus=completion_bonus_gold(self.state),
                omit_compact_currency=True,
            ),
        )

        actions_sig = goal_detail_actions_signature(task, sub)
        if actions_sig == self._goal_detail_actions_sig:
            return
        self._goal_detail_actions_sig = actions_sig
        clear_layout(self.goal_detail_btn_lay)
        if sub is None and task.status != TaskStatus.COMPLETED:
            if task.status == TaskStatus.PAUSED:
                btn = QPushButton("开始运行")
                btn.setObjectName("GoalResumeBtn")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(
                    lambda _c=False, tid=task.id: self._on_goal_resume(tid)
                )
                self.goal_detail_btn_lay.addWidget(btn)
            elif task.status == TaskStatus.ACTIVE:
                btn = QPushButton("暂停")
                btn.setObjectName("GoalPauseBtn")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(
                    lambda _c=False, tid=task.id: self._on_goal_pause(tid)
                )
                self.goal_detail_btn_lay.addWidget(btn)
                if task.subtasks and task.current_subtask() is None:
                    first_leaf = next(
                        (leaf for leaf in task.iter_leaves() if not leaf.done),
                        None,
                    )
                    if first_leaf is not None:
                        btn_start = QPushButton("开始运行")
                        btn_start.setObjectName("GoalResumeBtn")
                        btn_start.setCursor(Qt.PointingHandCursor)
                        btn_start.setToolTip("聚焦第一个未完成的子目标")
                        leaf_tid = task.id
                        leaf_sid = first_leaf.id
                        btn_start.clicked.connect(
                            lambda _c=False, tid=leaf_tid, sid=leaf_sid: (
                                self._on_sub_focus(tid, sid)
                            )
                        )
                        self.goal_detail_btn_lay.addWidget(btn_start)

            complete_style = (
                "Primary" if task.status == TaskStatus.ACTIVE else "Ghost"
            )
            btn_complete = QPushButton("完成目标")
            btn_complete.setObjectName(complete_style)
            btn_complete.setCursor(Qt.PointingHandCursor)
            btn_complete.clicked.connect(
                lambda _c=False, tid=task.id: self._on_goal_complete(tid)
            )
            self.goal_detail_btn_lay.addWidget(btn_complete)

            btn_del = QPushButton("删除")
            btn_del.setObjectName("Danger")
            btn_del.setCursor(Qt.PointingHandCursor)
            btn_del.clicked.connect(
                lambda _c=False, tid=task.id: self._on_goal_delete(tid)
            )
            self.goal_detail_btn_lay.addWidget(btn_del)

        elif (
            sub is not None
            and task.status != TaskStatus.COMPLETED
        ):
            sid = sub.id
            callbacks = SubtaskActionCallbacks(
                on_claim=lambda tid=task.id, s=sid: self._on_sub_claim(tid, s),
                on_focus=lambda tid=task.id, s=sid: self._on_sub_focus(tid, s),
                on_pause=lambda tid=task.id: self._on_sub_pause(tid),
                on_complete=lambda tid=task.id, s=sid: self._on_sub_complete(tid, s),
                on_decompose=lambda tid=task.id, s=sid: self._on_decompose(tid, s),
                on_delete=lambda tid=task.id, s=sid: self._on_sub_delete(tid, s),
            )
            append_subtask_detail_actions(
                self.goal_detail_btn_lay,
                sub,
                task_status=task.status,
                current_id=task.current_subtask_id,
                callbacks=callbacks,
            )

        self.goal_detail_btn_lay.addStretch(1)

        # 统一按钮高度：不同选中状态按钮组高度不同时，行高变化会改变
        # 详情面板的最小高度，进而把悬浮窗顶高
        row_h = self._detail_btn_row_height()
        for i in range(self.goal_detail_btn_lay.count()):
            item = self.goal_detail_btn_lay.itemAt(i)
            if item is not None and isinstance(item.widget(), QPushButton):
                item.widget().setFixedHeight(row_h)

    def _detail_btn_row_height(self) -> int:
        """详情面板按钮固定高度（含 QSS padding 与边框）。"""
        return 30

    def _apply_tree_selection_ui(
        self,
        *,
        since_gold: float = 0.0,
        since_diamond: float = 0.0,
    ) -> None:
        self._apply_tree_selection_chrome()
        self._refresh_tree_labels(stats_only=False)
        self._refresh_goal_detail_panel(
            since_gold=since_gold,
            since_diamond=since_diamond,
        )

    def _on_tree_select(self, task_id: str, subtask_id: str = "") -> None:
        self._selected_task_id = task_id
        self._selected_subtask_id = subtask_id
        self._sync_add_parent_from_selection()
        since = self.state.since_roll
        self._apply_tree_selection_ui(
            since_gold=since.gold,
            since_diamond=since.diamond,
        )

    def _sync_add_parent_from_selection(self) -> None:
        """选中文件夹时表单挂到其下；选顶层/叶子则根级添加。"""
        parent_id: Optional[str] = None
        if self._selected_subtask_id:
            task = self.manager.get(self._selected_task_id)
            if task is not None:
                sub = task.find_subtask(self._selected_subtask_id)
                if sub is not None and sub.is_container():
                    parent_id = sub.id
        self._sub_add_parent_id = parent_id
        self._update_subgoal_add_hint()

    def _make_goal_root_row(
        self,
        task: Task,
        *,
        selected: bool = False,
    ) -> tuple[TreeRow, QLabel]:
        return make_goal_root_row(
            task,
            selected=selected,
            expanded=self._is_goal_expanded(task.id),
            on_fold=lambda: self._on_goal_toggle_fold(task.id),
            on_select=lambda: self._on_tree_select(task.id),
            pin_label=True,
        )

    def _make_tree_node_row(
        self,
        task: Task,
        sub: Subtask,
        *,
        depth: int,
        selected: bool,
        is_current: bool,
    ) -> tuple[TreeRow, QLabel]:
        return make_tree_node_row(
            self.manager,
            task,
            sub,
            depth=depth,
            selected=selected,
            is_current=is_current,
            current_id=task.current_subtask_id,
            callbacks=SubtaskActionCallbacks(
                on_claim=lambda: self._on_sub_claim(task.id, sub.id),
                on_focus=lambda: self._on_sub_focus(task.id, sub.id),
                on_pause=lambda: self._on_sub_pause(task.id),
                on_complete=lambda: self._on_sub_complete(task.id, sub.id),
                on_decompose=lambda: self._on_decompose(task.id, sub.id),
                on_delete=lambda: self._on_sub_delete(task.id, sub.id),
            ),
            on_fold=lambda: self._on_sub_toggle_fold(task.id, sub.id),
            on_select=lambda: self._on_tree_select(task.id, sub.id),
            pin_label=True,
        )

    def _task_tree_structure_signature(self) -> tuple:
        """仅布局/结构相关字段；选中、聚焦、统计变化不触发整树重建。"""
        parts: list[tuple] = []
        for task in self._widget_goals():
            expanded = frozenset(self.manager.expanded_subtask_ids(task.id))
            sub_part = (
                visible_subtask_structure(self.manager, task)
                if self._is_goal_expanded(task.id)
                else ()
            )
            parts.append((
                task.id,
                task.title,
                expanded,
                sub_part,
            ))
        return (frozenset(self._expanded_goal_ids), tuple(parts))

    def _refresh_task_tree_section(
        self,
        *,
        since_gold: float = 0.0,
        since_diamond: float = 0.0,
    ) -> None:
        sig = self._task_tree_structure_signature()
        rebuilt = sig != self._subgoal_structure_sig
        # #region agent log
        t0 = time.perf_counter()
        # #endregion
        if rebuilt:
            self._rebuild_task_tree(
                since_gold=since_gold,
                since_diamond=since_diamond,
            )
        else:
            self._update_task_tree_lines(
                since_gold=since_gold,
                since_diamond=since_diamond,
            )
        # #region agent log
        ms = (time.perf_counter() - t0) * 1000
        if rebuilt or ms >= 8.0:
            active = self.state.active_task()
            from .task_manager import _agent_dbg
            _agent_dbg(
                "G",
                "ui_goal_tree_area.py:_refresh_task_tree_section",
                "tree refresh path",
                {
                    "rebuilt": rebuilt,
                    "ms": round(ms, 2),
                    "active_id": None if active is None else active.id,
                    "current_subtask_id": None if active is None else active.current_subtask_id,
                    "selected_subtask_id": self._selected_subtask_id,
                },
            )
        # #endregion

    def _update_task_tree_lines(
        self,
        *,
        since_gold: float = 0.0,
        since_diamond: float = 0.0,
    ) -> None:
        active = self.state.active_task()
        focus_hint = ""
        if active is not None and active.status == TaskStatus.ACTIVE:
            focus_hint = format_subgoals_focus_hint_html(active)
        if focus_hint:
            self.subgoals_hint.setTextFormat(Qt.RichText)
            set_label_html(self.subgoals_hint, focus_hint)
            self.subgoals_hint.show()
        else:
            self.subgoals_hint.hide()

        self._apply_tree_selection_chrome()
        self._refresh_task_ops_ui(
            since_gold=since_gold,
            since_diamond=since_diamond,
            allow_action_rebuild=True,
        )
        self._sync_subgoals_container_geometry()

    def _rebuild_task_tree(
        self,
        *,
        since_gold: float = 0.0,
        since_diamond: float = 0.0,
    ) -> None:
        self._clear_tree_layout()
        self._subgoal_line_labels.clear()
        self._tree_row_widgets.clear()
        self._subgoal_structure_sig = None

        goals = self._widget_goals()
        if not goals:
            self.subgoals_scroll.hide()
            self.subgoals_hbar.hide()
            self.subgoals_hint.hide()
            self.subgoals_empty.setText("还没有目标")
            self.subgoals_empty.show()
            return

        self.subgoals_empty.hide()
        self.subgoals_scroll.show()

        if self._selected_task_id:
            if not any(t.id == self._selected_task_id for t in goals):
                self._selected_task_id = ""
                self._selected_subtask_id = ""
            elif self._selected_subtask_id:
                task = self.manager.get(self._selected_task_id)
                if task is None or task.find_subtask(self._selected_subtask_id) is None:
                    self._selected_subtask_id = ""

        active = self.state.active_task()
        focus_hint = ""
        if active is not None and active.status == TaskStatus.ACTIVE:
            focus_hint = format_subgoals_focus_hint_html(active)
        if focus_hint:
            self.subgoals_hint.setTextFormat(Qt.RichText)
            set_label_html(self.subgoals_hint, focus_hint)
            self.subgoals_hint.show()
        else:
            self.subgoals_hint.hide()

        for task in goals:
            is_running = task.status == TaskStatus.ACTIVE
            editable = is_running
            goal_expanded = self._is_goal_expanded(task.id)
            root_selected = (
                task.id == self._selected_task_id and not self._selected_subtask_id
            )

            block, block_layout = make_goal_block()

            root_row, root_line = self._make_goal_root_row(
                task,
                selected=root_selected,
            )
            self._goal_root_rows[task.id] = root_row
            self._goal_root_lines[task.id] = root_line
            block_layout.addWidget(root_row)

            if goal_expanded:
                active_path = task.active_focus_path_ids() if editable else frozenset()

                if not task.subtasks and is_running:
                    block_layout.addWidget(make_empty_subtasks_hint())
                else:
                    for depth, sub in self.manager.iter_visible_subtasks(task):
                        key = self._sub_line_key(task.id, sub.id)
                        row, line = self._make_tree_node_row(
                            task,
                            sub,
                            depth=depth + 1,
                            selected=(
                                task.id == self._selected_task_id
                                and sub.id == self._selected_subtask_id
                            ),
                            is_current=sub.id in active_path,
                        )
                        self._subgoal_line_labels[key] = line
                        self._tree_row_widgets[key] = row
                        sub_selected = (
                            task.id == self._selected_task_id
                            and sub.id == self._selected_subtask_id
                        )
                        sub_block, indent_wrap = wrap_indented_subtask_row(
                            row,
                            depth=depth + 1,
                            selected=sub_selected,
                            focused=sub.id in active_path,
                        )
                        self._subtask_blocks[key] = sub_block
                        block_layout.addWidget(indent_wrap)

            self._goal_blocks[task.id] = block
            self.subgoals_layout.addWidget(block, 0, Qt.AlignLeft)

        for task in goals:
            block = self._goal_blocks.get(task.id)
            if block is not None:
                goal_selected = (
                    task.id == self._selected_task_id
                    and not self._selected_subtask_id
                )
                apply_goal_block_ui(
                    block,
                    is_running=task.status == TaskStatus.ACTIVE,
                    selected=goal_selected,
                    focused=bool(task.active_focus_path_ids()),
                )

        for task_id, block in self._goal_blocks.items():
            self._install_goal_hover_filters(block)

        self.subgoals_layout.addStretch(1)
        self._subgoal_structure_sig = self._task_tree_structure_signature()
        self._apply_tree_selection_ui(
            since_gold=since_gold,
            since_diamond=since_diamond,
        )
        self._sync_subgoals_container_geometry()
        self._restore_tree_scroll_anchor()
        self._request_geometry_sync()

    def _on_decompose(self, task_id: str, subtask_id: str) -> None:
        titles = prompt_decompose_titles(self)
        if not titles:
            return
        if self.manager.decompose_subtask(task_id, subtask_id, titles):
            self._selected_task_id = task_id
            self._selected_subtask_id = subtask_id
            self._reveal_subtask_path(task_id, subtask_id)
            self._request_state_sync()

    def _confirm_subgoal_delete(self, sub: Subtask, *, has_rewards: bool) -> bool:
        if has_rewards:
            text = f"「{sub.title}」有未领取奖励，确定删除吗？"
        else:
            text = f"确定删除「{sub.title}」吗？"
        return ask_yes_no(self, "删除目标", text)

    def _update_action_visibility(self, active: Task | None) -> None:
        if not self._selected_task_id:
            if active is not None:
                self._selected_task_id = active.id
            else:
                for task in self._widget_goals():
                    if task.status != TaskStatus.COMPLETED:
                        self._selected_task_id = task.id
                        break

        show_add = False
        if active is not None and active.status != TaskStatus.COMPLETED:
            show_add = True
        elif self._selected_task_id:
            task = self.manager.get(self._selected_task_id)
            show_add = task is not None and task.status != TaskStatus.COMPLETED
        self.subgoal_actions.setVisible(show_add)
        if show_add:
            self._update_subgoal_add_hint()

        if not self._selected_task_id:
            self.goal_detail_panel.hide()
            return
        task = self.manager.get(self._selected_task_id)
        if task is None:
            self.goal_detail_panel.hide()
            return
        since = self.state.since_roll
        self._refresh_goal_detail_panel(
            since_gold=since.gold,
            since_diamond=since.diamond,
        )

    def _on_goal_pause(self, task_id: str) -> None:
        task = self.manager.get(task_id)
        if task is None or task.status != TaskStatus.ACTIVE:
            return
        self.manager.pause(task_id)
        self._request_state_sync()

    def _on_goal_resume(self, task_id: str) -> None:
        t = self.manager.resume(task_id)
        if t is None or t.status != TaskStatus.ACTIVE:
            return
        sub = t.current_subtask()
        if sub is not None:
            self._selected_task_id = task_id
            self._selected_subtask_id = sub.id
            self._reveal_subtask_path(task_id, sub.id)
        self._request_state_sync()

    def _on_goal_complete(self, task_id: str) -> None:
        if try_complete_goal(self, self.manager, task_id):
            self.goal_completed.emit()
            self._request_state_sync()

    def _on_goal_delete(self, task_id: str) -> None:
        if try_delete_goal(self, self.manager, task_id):
            if self._selected_task_id == task_id:
                self._selected_task_id = ""
                self._selected_subtask_id = ""
            self._request_state_sync()

    def _on_sub_toggle_fold(self, task_id: str, subtask_id: str) -> None:
        if self.manager.toggle_subtask_expand(task_id, subtask_id):
            # 子目标分组折叠后锚定用所属根行回正
            self._remember_tree_scroll_anchor(task_id)
            self._request_local_refresh()

    def _on_sub_focus(self, task_id: str, subtask_id: str) -> None:
        task = self.manager.get(task_id)
        prev_id = None if task is None else task.current_subtask_id
        ok = self.manager.start_subtask(task_id, subtask_id)
        # #region agent log
        from .task_manager import _agent_dbg
        after = self.manager.get(task_id)
        _agent_dbg(
            "I",
            "ui_goal_tree_area.py:_on_sub_focus",
            "switch running subtask",
            {
                "ok": ok,
                "task_id": task_id,
                "from_id": prev_id,
                "to_id": subtask_id,
                "after_id": None if after is None else after.current_subtask_id,
                "selected_subtask_id": self._selected_subtask_id,
            },
        )
        # #endregion
        if ok:
            self._reveal_subtask_path(task_id, subtask_id)
            self._request_state_sync()

    def _on_sub_pause(self, task_id: str) -> None:
        task = self.manager.get(task_id)
        if task is None or task.status != TaskStatus.ACTIVE:
            return
        self.manager.pause(task_id)
        self._request_state_sync()

    def _on_sub_complete(self, task_id: str, subtask_id: str) -> None:
        task = self.manager.get(task_id)
        if task is None or task.status == TaskStatus.COMPLETED:
            return
        sub = task.find_subtask(subtask_id)
        if sub is None:
            return
        was_done = sub.done
        reward = self.manager.complete_and_claim_subtask(task_id, subtask_id)
        if not was_done and sub.done:
            self.goal_completed.emit()
        if reward is not None:
            self.subtask_claimed.emit(sub.title, reward)
        self._request_state_sync()

    def _on_sub_claim(self, task_id: str, subtask_id: str) -> None:
        task = self.manager.get(task_id)
        if task is None or task.status == TaskStatus.COMPLETED:
            return
        sub = task.find_subtask(subtask_id)
        if sub is None:
            return
        was_done = sub.done
        reward = self.manager.complete_and_claim_subtask(task_id, subtask_id)
        if not was_done and sub.done:
            self.goal_completed.emit()
        if reward is not None:
            self.subtask_claimed.emit(sub.title, reward)
        self._request_state_sync()

    def _on_sub_delete(self, task_id: str, subtask_id: str) -> None:
        task = self.manager.get(task_id)
        if task is None or task.status == TaskStatus.COMPLETED:
            return
        sub = task.find_subtask(subtask_id)
        if sub is None:
            return
        if subtree_has_unclaimed(sub):
            if not self._confirm_subgoal_delete(sub, has_rewards=True):
                return
        elif not sub.done and (
            sub.pending_rewards
            or sub.rollup_operations() > 0
            or sub.children
        ):
            if not self._confirm_subgoal_delete(sub, has_rewards=False):
                return
        if not self.manager.delete_subtask(task_id, subtask_id):
            return
        if self._selected_subtask_id == subtask_id:
            self._selected_subtask_id = ""
        if self._sub_add_parent_id == subtask_id:
            self._sub_add_parent_id = None
            self._update_subgoal_add_hint()
        self._request_state_sync()

    def _update_subgoal_add_hint(self) -> None:
        self.sub_add_btn.setText("添加")
        if self._sub_add_parent_id:
            task = self._subgoal_target_task()
            parent = task.find_subtask(self._sub_add_parent_id) if task else None
            if parent is not None:
                title = f"正在向「{parent.title}」添加子目标"
                self.subgoal_add_title.setText(title)
                self.subgoal_add_title.setToolTip(title)
                self.subgoal_input.setPlaceholderText(f"添加到「{parent.title}」下…")
                return
            self._sub_add_parent_id = None
        task = self._subgoal_target_task()
        if task is not None:
            title = f"正在向「{task.title}」添加子目标"
            self.subgoal_add_title.setText(title)
            self.subgoal_add_title.setToolTip(title)
            self.subgoal_input.setPlaceholderText(f"添加到「{task.title}」下…")
            return
        self.subgoal_add_title.setText("添加子目标")
        self.subgoal_add_title.setToolTip("")
        self.subgoal_input.setPlaceholderText("子目标标题…")

    def _on_subtask_min_changed(self, value: int) -> None:
        self.state.settings["subtask_default_target_minutes"] = max(1, int(value))
        save_state(self.state)

    def _subgoal_target_task(self) -> Task | None:
        """添加子目标所作用的目标：优先当前选中，否则进行中目标。"""
        if self._selected_task_id:
            task = self.manager.get(self._selected_task_id)
            if task is not None and task.status != TaskStatus.COMPLETED:
                return task
        return self.state.active_task()

    def _on_add_subgoal(self) -> None:
        task = self._subgoal_target_task()
        if task is None:
            return
        title = self.subgoal_input.text().strip()
        if not title:
            return
        target_minutes = self.subgoal_min_spin.value()
        self.state.settings["subtask_default_target_minutes"] = target_minutes
        sub = self.manager.add_subtask(
            task.id,
            title,
            target_minutes=target_minutes,
            parent_subtask_id=self._sub_add_parent_id,
        )
        if sub is None:
            return
        self._selected_task_id = task.id
        self._selected_subtask_id = sub.id
        self._reveal_subtask_path(task.id, sub.id)
        self.subgoal_input.clear()
        self._sub_add_parent_id = None
        self._update_subgoal_add_hint()
        self._request_state_sync()

    def _on_add_goal(self) -> None:
        title, ok = QInputDialog.getText(self, "新建目标", "目标标题：")
        if not ok:
            return
        title = title.strip()
        if not title:
            return
        self.manager.create(title)
        self._request_state_sync()

    def _apply_task_section(
        self,
        active: Task | None,
        *,
        since_gold: float,
        since_diamond: float,
    ) -> None:
        self._sync_expanded_goals(active)
        self._refresh_task_tree_section(
            since_gold=since_gold,
            since_diamond=since_diamond,
        )
        self._update_action_visibility(active)
