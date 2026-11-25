"""悬浮窗与目标管理对话框共用的目标树构建。"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..models import AppState, Subtask, Task, TaskStatus
from ..task_manager import TaskManager
from .qt import pin_html_label_width
from .task_tree import (
    TREE_INDENT_PX,
    SubtaskActionCallbacks,
    TreeRow,
    apply_goal_root_row_state,
    apply_subtask_block_ui,
    build_subtask_action_buttons,
)
from ..text import format_goal_root_line_html, format_tree_node_html

SUBGOAL_INDENT_PX = TREE_INDENT_PX


def completion_bonus_gold(state: AppState) -> float:
    return float(state.settings.get("subtask_completion_bonus_gold", 0.5))


def subtree_has_unclaimed(sub: Subtask) -> bool:
    for node in sub.iter_subtree():
        if node.rewards_claimed:
            continue
        if node.is_claimable() or node.pending_rewards:
            return True
    return False


def goal_detail_actions_signature(task: Task, sub: Subtask | None) -> tuple:
    if sub is None:
        has_unfocused_subs = bool(
            task.subtasks
            and task.status == TaskStatus.ACTIVE
            and task.current_subtask() is None
        )
        return ("goal", task.id, task.status, has_unfocused_subs)
    return (
        "sub",
        task.id,
        sub.id,
        sub.done,
        sub.rewards_claimed,
        sub.is_leaf(),
        sub.is_container(),
        sub.is_claimable(),
        sub.can_claim_pending(),
        sub.can_finish(),
        sub.is_legacy_progress(),
        sub.time_target_met(),
        task.current_subtask_id,
        task.status,
    )


def visible_subtask_structure(manager: TaskManager, task: Task) -> tuple:
    return tuple(
        (
            s.id,
            s.title,
            s.done,
            s.rewards_claimed,
            s.is_container(),
        )
        for _, s in manager.iter_visible_subtasks(task)
    )


def parse_decompose_titles(text: str) -> list[str]:
    return [p.strip() for p in text.replace("，", ",").split(",") if p.strip()]


def prompt_decompose_titles(parent: QWidget) -> list[str] | None:
    text, ok = QInputDialog.getText(
        parent,
        "分解目标",
        "子项名称（多个用逗号分隔）：",
        text="子项1, 子项2",
    )
    if not ok:
        return None
    titles = parse_decompose_titles(text)
    return titles or None


def make_fold_button(*, expanded: bool, on_click: Callable[[], None]) -> QPushButton:
    btn = QPushButton("v" if expanded else ">")
    btn.setObjectName("TreeFoldBtn")
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedSize(18, 18)
    btn.clicked.connect(lambda _checked=False: on_click())
    return btn


def make_empty_subtasks_hint() -> QLabel:
    hint = QLabel("添加目标后开始累计奖励")
    hint.setObjectName("SubGoalList")
    hint.setWordWrap(True)
    hint.setContentsMargins(22, 0, 4, 0)
    return hint


def make_goal_block() -> tuple[QWidget, QVBoxLayout]:
    block = QWidget()
    block.setObjectName("GoalBlock")
    block.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
    layout = QVBoxLayout(block)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    return block, layout


def wrap_indented_subtask_row(
    row: TreeRow,
    *,
    depth: int,
    selected: bool,
    focused: bool,
) -> tuple[QWidget, QWidget]:
    """返回 (sub_block, indent_wrap)。"""
    sub_block = QWidget()
    sub_block.setObjectName("SubtaskBlock")
    sub_block.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
    apply_subtask_block_ui(sub_block, selected=selected, focused=focused)
    sub_lay = QVBoxLayout(sub_block)
    sub_lay.setContentsMargins(0, 0, 0, 0)
    sub_lay.setSpacing(0)
    sub_lay.addWidget(row)

    indent_wrap = QWidget()
    indent_wrap.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
    indent_lay = QHBoxLayout(indent_wrap)
    indent_lay.setContentsMargins(depth * SUBGOAL_INDENT_PX, 0, 0, 0)
    indent_lay.setSpacing(0)
    indent_lay.addWidget(sub_block)
    return sub_block, indent_wrap


def make_goal_root_row(
    task: Task,
    *,
    selected: bool,
    expanded: bool,
    on_fold: Callable[[], None],
    on_select: Callable[[], None],
    pin_label: bool = False,
) -> tuple[TreeRow, QLabel]:
    is_running = task.status == TaskStatus.ACTIVE
    row = TreeRow()
    row.setObjectName("GoalRootRow")
    row.setCursor(Qt.PointingHandCursor)
    row.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
    apply_goal_root_row_state(row, is_running=is_running)
    row.set_row_selected(selected)

    row_lay = QHBoxLayout(row)
    row_lay.setContentsMargins(4, 3, 4, 3)
    row_lay.setSpacing(4)

    if bool(task.subtasks) or is_running:
        row_lay.addWidget(
            make_fold_button(expanded=expanded, on_click=on_fold),
            0,
            Qt.AlignVCenter,
        )

    line = QLabel(
        format_goal_root_line_html(
            task,
            selected=selected,
            is_running=is_running,
            muted=task.status == TaskStatus.PAUSED,
        )
    )
    line.setObjectName("TaskTitle")
    line.setWordWrap(False)
    line.setTextFormat(Qt.RichText)
    line.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
    row_lay.addWidget(line, 0)
    if pin_label:
        pin_html_label_width(line)

    row.selected.connect(on_select)
    row.style().unpolish(row)
    row.style().polish(row)
    return row, line


def make_tree_node_row(
    manager: TaskManager,
    task: Task,
    sub: Subtask,
    *,
    depth: int,
    selected: bool,
    is_current: bool,
    current_id: Optional[str],
    callbacks: SubtaskActionCallbacks,
    on_fold: Callable[[], None],
    on_select: Callable[[], None],
    pin_label: bool = False,
) -> tuple[TreeRow, QLabel]:
    row = TreeRow()
    row.setObjectName("TreeNodeRow")
    row.setCursor(Qt.PointingHandCursor)
    row.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
    if depth > 0:
        row.setProperty("nested", True)
    row.set_row_selected(selected)

    row_lay = QHBoxLayout(row)
    row_lay.setContentsMargins(4, 3, 4, 3)
    row_lay.setSpacing(4)

    if sub.is_container():
        expanded = manager.is_subtask_expanded(task.id, sub.id)
        row_lay.addWidget(
            make_fold_button(expanded=expanded, on_click=on_fold),
            0,
            Qt.AlignVCenter,
        )

    line = QLabel(
        format_tree_node_html(
            sub,
            selected=selected,
            is_current=is_current,
            expanded=manager.is_subtask_expanded(task.id, sub.id),
            show_stats=selected or is_current,
        )
    )
    line.setObjectName("SubGoalList")
    line.setWordWrap(False)
    line.setTextFormat(Qt.RichText)
    line.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
    row_lay.addWidget(line, 0)
    if pin_label:
        pin_html_label_width(line)

    actions = build_subtask_action_buttons(
        sub,
        task_status=task.status,
        current_id=current_id,
        callbacks=callbacks,
        parent=row,
    )
    row.set_actions_widget(actions)
    row_lay.addWidget(actions, 0, Qt.AlignVCenter)
    row.set_row_focused(is_current)

    row.selected.connect(on_select)
    row.style().unpolish(row)
    row.style().polish(row)
    return row, line
