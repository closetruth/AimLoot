"""单目标树面板：与悬浮窗一致的树 + 添加栏 + 详情区。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..models import AppState, Subtask, Task, TaskStatus
from ..task_manager import TaskManager
from .goal_shared import (
    completion_bonus_gold,
    goal_detail_actions_signature,
    make_empty_subtasks_hint,
    make_goal_block,
    make_goal_root_row,
    make_tree_node_row,
    prompt_decompose_titles,
    visible_subtask_structure,
    wrap_indented_subtask_row,
)
from .qt import clear_layout, drain_layout_widgets, set_label_html, set_label_text
from .task_tree import (
    SubtaskActionCallbacks,
    TreeRow,
    append_subtask_detail_actions,
    apply_goal_block_ui,
    apply_subtask_block_ui,
)
from ..text import (
    format_amount,
    format_goal_root_line_html,
    format_subgoals_focus_hint_html,
    format_tree_detail_html,
    format_tree_node_html,
)


class GoalTreePanel(QWidget):
    """单个目标的树形 UI：GoalBlock 树、外置添加栏、详情统计面板。"""

    action = Signal(str, str, str)  # task_id, action_name, extra

    def __init__(
        self,
        task: Task,
        manager: TaskManager,
        state: AppState,
        *,
        editable: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task = task
        self.manager = manager
        self.state = state
        self._editable = editable

        self._selected_subtask_id = ""
        self._goal_expanded = True
        self._sub_add_parent_id: Optional[str] = None
        self._structure_sig: tuple | None = None
        self._goal_detail_actions_sig: tuple | None = None

        self._goal_root_row: TreeRow | None = None
        self._goal_root_line: QLabel | None = None
        self._goal_block: QWidget | None = None
        self._subgoal_line_labels: dict[str, QLabel] = {}
        self._tree_row_widgets: dict[str, TreeRow] = {}
        self._subtask_blocks: dict[str, QWidget] = {}

        self._build_ui()
        self.refresh(task)

    def selected_subtask_id(self) -> str:
        return self._selected_subtask_id

    def set_selected_subtask_id(self, subtask_id: str) -> None:
        self._selected_subtask_id = subtask_id
        self._sync_add_parent_from_selection()
        since = self.state.since_roll
        self._apply_selection_ui(
            since_gold=since.gold,
            since_diamond=since.diamond,
        )

    def refresh(
        self,
        task: Task | None = None,
        *,
        since_gold: float = 0.0,
        since_diamond: float = 0.0,
    ) -> None:
        if task is not None:
            self.task = task
        if since_gold == 0.0 and since_diamond == 0.0:
            since = self.state.since_roll
            since_gold = since.gold
            since_diamond = since.diamond

        sig = self._structure_signature()
        if sig != self._structure_sig:
            self._rebuild_tree(since_gold=since_gold, since_diamond=since_diamond)
        else:
            self._apply_selection_ui(
                since_gold=since_gold,
                since_diamond=since_diamond,
            )

        self._update_focus_hint()
        self._add_bar.setVisible(self._editable)
        if self._editable:
            self._update_add_context()
        self._refresh_detail_panel(since_gold=since_gold, since_diamond=since_diamond)

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._focus_hint = QLabel("")
        self._focus_hint.setObjectName("SubGoalHint")
        self._focus_hint.setWordWrap(True)
        self._focus_hint.hide()
        lay.addWidget(self._focus_hint)

        self._tree_host = QWidget()
        self._tree_layout = QVBoxLayout(self._tree_host)
        self._tree_layout.setContentsMargins(0, 0, 0, 0)
        self._tree_layout.setSpacing(0)
        lay.addWidget(self._tree_host)

        self._add_bar = QWidget()
        self._add_bar.setObjectName("SubGoalActions")
        self._add_bar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        add_outer = QVBoxLayout(self._add_bar)
        add_outer.setContentsMargins(0, 0, 0, 0)
        add_outer.setSpacing(4)

        self._add_title = QLabel("添加子目标")
        self._add_title.setObjectName("SectionTitle")
        self._add_title.setWordWrap(False)
        add_outer.addWidget(self._add_title)

        add_row = QHBoxLayout()
        add_row.setSpacing(4)
        self._subgoal_input = QLineEdit()
        self._subgoal_input.setObjectName("SubGoalInput")
        self._subgoal_input.setPlaceholderText("子目标标题…")
        self._subgoal_input.returnPressed.connect(self._on_add_subgoal)
        add_row.addWidget(self._subgoal_input, 1)

        default_min = max(
            1, int(self.state.settings.get("subtask_default_target_minutes", 10)),
        )
        self._subgoal_min_spin = QSpinBox()
        self._subgoal_min_spin.setObjectName("SubtaskMinSpin")
        self._subgoal_min_spin.setRange(1, 999)
        self._subgoal_min_spin.setValue(default_min)
        self._subgoal_min_spin.setPrefix("最少 ")
        self._subgoal_min_spin.setSuffix(" 分")
        self._subgoal_min_spin.setToolTip("新目标需运行的最短时间（达标后可完成领奖）")
        self._subgoal_min_spin.setFixedWidth(96)
        add_row.addWidget(self._subgoal_min_spin)

        self._sub_add_btn = QPushButton("添加")
        self._sub_add_btn.setObjectName("SubAddBtn")
        self._sub_add_btn.setCursor(Qt.PointingHandCursor)
        self._sub_add_btn.clicked.connect(self._on_add_subgoal)
        add_row.addWidget(self._sub_add_btn)
        add_outer.addLayout(add_row)

        lay.addWidget(self._add_bar)

        self._detail_panel = QWidget()
        self._detail_panel.setObjectName("GoalDetailPanel")
        detail_lay = QVBoxLayout(self._detail_panel)
        detail_lay.setContentsMargins(8, 6, 8, 6)
        detail_lay.setSpacing(4)

        self._detail_title = QLabel("")
        self._detail_title.setObjectName("GoalDetailTitle")
        self._detail_title.setWordWrap(True)
        detail_lay.addWidget(self._detail_title)

        self._detail_stats = QLabel("")
        self._detail_stats.setObjectName("GoalDetailStats")
        self._detail_stats.setWordWrap(True)
        self._detail_stats.setTextFormat(Qt.RichText)
        detail_lay.addWidget(self._detail_stats)

        self._detail_btn_row = QWidget()
        self._detail_btn_lay = QHBoxLayout(self._detail_btn_row)
        self._detail_btn_lay.setContentsMargins(0, 0, 0, 0)
        self._detail_btn_lay.setSpacing(6)
        detail_lay.addWidget(self._detail_btn_row)

        self._detail_panel.hide()
        lay.addWidget(self._detail_panel)

    def _structure_signature(self) -> tuple:
        expanded = frozenset(self.manager.expanded_subtask_ids(self.task.id))
        sub_part = (
            visible_subtask_structure(self.manager, self.task)
            if self._goal_expanded
            else ()
        )
        return (
            self.task.id,
            self.task.title,
            self.task.status,
            self._goal_expanded,
            expanded,
            sub_part,
        )

    def _clear_tree_layout(self) -> None:
        self._goal_root_row = None
        self._goal_root_line = None
        self._goal_block = None
        self._subgoal_line_labels.clear()
        self._tree_row_widgets.clear()
        self._subtask_blocks.clear()
        self._goal_detail_actions_sig = None
        drain_layout_widgets(self._tree_layout)

    def _rebuild_tree(
        self,
        *,
        since_gold: float = 0.0,
        since_diamond: float = 0.0,
    ) -> None:
        self._clear_tree_layout()
        self._structure_sig = self._structure_signature()

        task = self.task
        is_running = task.status == TaskStatus.ACTIVE
        editable = self._editable
        root_selected = not self._selected_subtask_id

        block, block_layout = make_goal_block()

        root_row, root_line = self._make_goal_root_row(selected=root_selected)
        self._goal_root_row = root_row
        self._goal_root_line = root_line
        block_layout.addWidget(root_row)

        if self._goal_expanded:
            current = task.current_subtask() if editable else None
            current_id = current.id if current is not None else None
            active_path = task.active_focus_path_ids() if editable else frozenset()

            if not task.subtasks and is_running:
                block_layout.addWidget(make_empty_subtasks_hint())
            else:
                for depth, sub in self.manager.iter_visible_subtasks(task):
                    key = sub.id
                    row, line = self._make_tree_node_row(
                        sub,
                        depth=depth + 1,
                        selected=sub.id == self._selected_subtask_id,
                        is_current=sub.id in active_path,
                        current_id=current_id,
                    )
                    self._subgoal_line_labels[key] = line
                    self._tree_row_widgets[key] = row
                    sub_block, indent_wrap = wrap_indented_subtask_row(
                        row,
                        depth=depth + 1,
                        selected=sub.id == self._selected_subtask_id,
                        focused=sub.id in active_path,
                    )
                    self._subtask_blocks[key] = sub_block
                    block_layout.addWidget(indent_wrap)

        self._goal_block = block
        apply_goal_block_ui(
            block,
            is_running=is_running,
            selected=root_selected,
            focused=bool(task.active_focus_path_ids()),
        )
        self._tree_layout.addWidget(block, 0, Qt.AlignLeft)
        self._apply_selection_ui(since_gold=since_gold, since_diamond=since_diamond)

    def _make_goal_root_row(self, *, selected: bool) -> tuple[TreeRow, QLabel]:
        return make_goal_root_row(
            self.task,
            selected=selected,
            expanded=self._goal_expanded,
            on_fold=self._on_goal_toggle_fold,
            on_select=lambda: self._on_tree_select(""),
        )

    def _make_tree_node_row(
        self,
        sub: Subtask,
        *,
        depth: int,
        selected: bool,
        is_current: bool,
        current_id: Optional[str],
    ) -> tuple[TreeRow, QLabel]:
        task = self.task
        return make_tree_node_row(
            self.manager,
            task,
            sub,
            depth=depth,
            selected=selected,
            is_current=is_current,
            current_id=current_id,
            callbacks=SubtaskActionCallbacks(
                on_focus=lambda: self.action.emit(task.id, "subtask_focus", sub.id),
                on_pause=lambda: self.action.emit(task.id, "subtask_pause", ""),
                on_complete=lambda: self.action.emit(
                    task.id, "subtask_confirm_done", sub.id,
                ),
                on_decompose=lambda: self._prompt_decompose(sub.id),
                on_delete=lambda: self.action.emit(task.id, "subtask_delete", sub.id),
            ),
            on_fold=lambda: self._emit_toggle_fold(sub.id),
            on_select=lambda: self._on_tree_select(sub.id),
        )

    def _on_goal_toggle_fold(self) -> None:
        self._goal_expanded = not self._goal_expanded
        since = self.state.since_roll
        self.refresh(since_gold=since.gold, since_diamond=since.diamond)

    def _emit_toggle_fold(self, subtask_id: str) -> None:
        self.action.emit(self.task.id, "subtask_toggle_fold", subtask_id)

    def _on_tree_select(self, subtask_id: str) -> None:
        self._selected_subtask_id = subtask_id
        self._sync_add_parent_from_selection()
        since = self.state.since_roll
        self._apply_selection_ui(
            since_gold=since.gold,
            since_diamond=since.diamond,
        )

    def _sync_add_parent_from_selection(self) -> None:
        """选中文件夹时表单挂到其下；选顶层/叶子则根级添加。"""
        parent_id: Optional[str] = None
        if self._selected_subtask_id:
            sub = self.task.find_subtask(self._selected_subtask_id)
            if sub is not None and sub.is_container():
                parent_id = sub.id
        self._sub_add_parent_id = parent_id
        self._update_add_context()

    def _apply_selection_ui(
        self,
        *,
        since_gold: float = 0.0,
        since_diamond: float = 0.0,
    ) -> None:
        self._apply_selection_chrome()
        self._refresh_tree_labels()
        self._refresh_detail_panel(since_gold=since_gold, since_diamond=since_diamond)

    def _apply_selection_chrome(self) -> None:
        if self._goal_root_row is not None:
            self._goal_root_row.set_row_selected(not self._selected_subtask_id)
        for sid, row in self._tree_row_widgets.items():
            row.set_row_selected(sid == self._selected_subtask_id)
            task = self.task
            active_path = (
                task.active_focus_path_ids()
                if task.status == TaskStatus.ACTIVE
                else frozenset()
            )
            row.set_row_focused(sid in active_path)
        for sid, sub_block in self._subtask_blocks.items():
            active_path = (
                self.task.active_focus_path_ids()
                if self.task.status == TaskStatus.ACTIVE
                else frozenset()
            )
            apply_subtask_block_ui(
                sub_block,
                selected=bool(self._selected_subtask_id)
                and sid == self._selected_subtask_id,
                focused=sid in active_path,
            )
        if self._goal_block is not None:
            apply_goal_block_ui(
                self._goal_block,
                is_running=self.task.status == TaskStatus.ACTIVE,
                selected=not self._selected_subtask_id,
                focused=bool(self.task.active_focus_path_ids()),
            )

    def _refresh_tree_labels(self, *, stats_only: bool = False) -> None:
        task = self.task
        editable = self._editable
        active_path = task.active_focus_path_ids() if editable else frozenset()

        if not stats_only and self._goal_root_line is not None:
            set_label_html(
                self._goal_root_line,
                format_goal_root_line_html(
                    task,
                    selected=not self._selected_subtask_id,
                    is_running=task.status == TaskStatus.ACTIVE,
                    muted=task.status == TaskStatus.PAUSED,
                ),
            )

        for sid, sub_line in self._subgoal_line_labels.items():
            sub = task.find_subtask(sid)
            if sub is None:
                continue
            is_selected = sid == self._selected_subtask_id
            is_current = sid in active_path
            if stats_only and not (is_selected or is_current):
                stale_dot = (not sub.done) and ("●" in sub_line.text())
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

    def _format_detail_stats_html(
        self,
        task: Task,
        sub: Subtask | None,
        *,
        since_gold: float,
        since_diamond: float,
    ) -> str:
        html = format_tree_detail_html(
            task,
            sub,
            since_roll_gold=since_gold,
            since_roll_diamond=since_diamond,
            completion_bonus=completion_bonus_gold(self.state),
        )
        if sub is None and task.status == TaskStatus.COMPLETED:
            settlement = (
                f'<span style="color:#8b93a8">结算 '
                f"金 {format_amount(task.completed_reward_gold)} "
                f"钻 {format_amount(task.completed_reward_diamond)}</span>"
            )
            html = f"{html}<br>{settlement}"
        return html

    def _refresh_detail_panel(
        self,
        *,
        since_gold: float = 0.0,
        since_diamond: float = 0.0,
    ) -> None:
        task = self.task
        self._detail_panel.show()

        sub: Subtask | None = None
        if self._selected_subtask_id:
            sub = task.find_subtask(self._selected_subtask_id)

        title_text = sub.title if sub is not None else task.title
        set_label_text(self._detail_title, title_text)
        set_label_html(
            self._detail_stats,
            self._format_detail_stats_html(
                task,
                sub,
                since_gold=since_gold,
                since_diamond=since_diamond,
            ),
        )

        actions_sig = goal_detail_actions_signature(task, sub)
        if actions_sig == self._goal_detail_actions_sig:
            return
        self._goal_detail_actions_sig = actions_sig
        clear_layout(self._detail_btn_lay)

        if sub is None:
            if task.status == TaskStatus.PAUSED:
                btn = QPushButton("开始运行")
                btn.setObjectName("GoalResumeBtn")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(
                    lambda _c=False: self.action.emit(task.id, "resume", "")
                )
                self._detail_btn_lay.addWidget(btn)
            elif task.status == TaskStatus.ACTIVE:
                btn = QPushButton("暂停")
                btn.setObjectName("GoalPauseBtn")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(
                    lambda _c=False: self.action.emit(task.id, "pause", "")
                )
                self._detail_btn_lay.addWidget(btn)
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
                        btn_start.clicked.connect(
                            lambda _c=False, sid=first_leaf.id: self.action.emit(
                                task.id, "subtask_focus", sid,
                            )
                        )
                        self._detail_btn_lay.addWidget(btn_start)

        elif (
            sub is not None
            and task.status != TaskStatus.COMPLETED
        ):
            sid = sub.id
            callbacks = SubtaskActionCallbacks(
                on_focus=lambda: self.action.emit(task.id, "subtask_focus", sid),
                on_pause=lambda: self.action.emit(task.id, "subtask_pause", ""),
                on_complete=lambda: self.action.emit(
                    task.id, "subtask_confirm_done", sid,
                ),
                on_decompose=lambda: self._prompt_decompose(sid),
                on_delete=lambda: self.action.emit(task.id, "subtask_delete", sid),
            )
            append_subtask_detail_actions(
                self._detail_btn_lay,
                sub,
                task_status=task.status,
                current_id=task.current_subtask_id,
                callbacks=callbacks,
            )

        self._detail_btn_lay.addStretch(1)

    def _update_focus_hint(self) -> None:
        if self.task.status != TaskStatus.ACTIVE:
            self._focus_hint.hide()
            return
        hint = format_subgoals_focus_hint_html(self.task)
        if hint:
            self._focus_hint.setTextFormat(Qt.RichText)
            self._focus_hint.setText(hint)
            self._focus_hint.show()
        else:
            self._focus_hint.hide()

    def _update_add_context(self) -> None:
        if self._sub_add_parent_id:
            parent = self.task.find_subtask(self._sub_add_parent_id)
            if parent is not None:
                title = f"正在向「{parent.title}」添加子目标"
                self._add_title.setText(title)
                self._add_title.setToolTip(title)
                self._subgoal_input.setPlaceholderText(f"添加到「{parent.title}」下…")
                return
            self._sub_add_parent_id = None
        title = f"正在向「{self.task.title}」添加子目标"
        self._add_title.setText(title)
        self._add_title.setToolTip(title)
        self._subgoal_input.setPlaceholderText(f"添加到「{self.task.title}」下…")

    def _prompt_decompose(self, subtask_id: str) -> None:
        titles = prompt_decompose_titles(self)
        if not titles:
            return
        self.action.emit(
            self.task.id,
            "subtask_decompose",
            f"{subtask_id}|{','.join(titles)}",
        )

    def _on_add_subgoal(self) -> None:
        title = self._subgoal_input.text().strip()
        if not title:
            return
        target_minutes = self._subgoal_min_spin.value()
        self.action.emit(
            self.task.id,
            "subtask_add",
            f"{title}|{target_minutes}|{self._sub_add_parent_id or ''}",
        )
        self._subgoal_input.clear()
        self._sync_add_parent_from_selection()