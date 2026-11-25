"""目标树 UI 共享常量、行组件与行尾操作。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ..models import Subtask, TaskStatus

TREE_INDENT_PX = 24

TREE_QSS = """
QWidget#TreeNodeRow,
QFrame#TreeNodeRow {
    background-color: transparent;
    border: none;
    border-radius: 3px;
}
QWidget#TreeNodeRow:hover,
QFrame#TreeNodeRow:hover {
    background-color: rgba(255, 255, 255, 0.08);
}
QWidget#TreeNodeRow[selected="true"],
QFrame#TreeNodeRow[selected="true"] {
    background-color: transparent;
}
QWidget#TreeNodeRow[current="true"],
QFrame#TreeNodeRow[current="true"] {
    background-color: transparent;
    border: none;
    border-radius: 3px;
}
QWidget#SubtaskBlock {
    background-color: transparent;
    border: none;
    border-radius: 4px;
    margin: 2px 0;
    padding: 3px 6px;
}
QWidget#SubtaskBlock[selected="true"] {
    border: 2px solid #7eb4ff;
    background-color: transparent;
}
QWidget#SubtaskBlock[focused="true"] {
    background-color: rgba(126, 180, 255, 0.06);
}
QWidget#GoalBlock[focused="true"] {
    background-color: rgba(126, 180, 255, 0.04);
}
QWidget#GoalBlock {
    background-color: transparent;
    border: none;
    border-radius: 6px;
    margin: 3px 0;
    padding: 4px 6px;
}
QWidget#GoalBlock[hovered="true"] {
    background-color: rgba(255, 255, 255, 0.06);
}
QWidget#GoalBlock[selected="true"] {
    border: 2px solid #7eb4ff;
    background-color: transparent;
}
QWidget#GoalRootRow,
QFrame#GoalRootRow {
    background-color: transparent;
    border: none;
    border-radius: 3px;
}
QWidget#GoalRootRow:hover,
QFrame#GoalRootRow:hover {
    background-color: rgba(255, 255, 255, 0.06);
}
QWidget#GoalRootRow[selected="true"],
QFrame#GoalRootRow[selected="true"] {
    background-color: transparent;
}
QWidget#GoalRootRow[current="true"],
QFrame#GoalRootRow[current="true"],
QWidget#GoalRootRow[paused="true"],
QFrame#GoalRootRow[paused="true"] {
    background-color: transparent;
    border: none;
}
QPushButton#TreeFoldBtn {
    background: transparent;
    border: none;
    color: #e8ecf4;
    font-size: 11px;
    font-weight: 700;
    min-width: 18px;
    max-width: 18px;
    min-height: 18px;
    padding: 0;
}
QWidget#TreeActions {
    background: transparent;
}
QPushButton#TreeActionBtn {
    background: transparent;
    border: none;
    color: #b8c0d4;
    font-size: 12px;
    font-weight: 700;
    min-width: 20px;
    max-width: 20px;
    min-height: 20px;
    max-height: 20px;
    padding: 0;
}
QPushButton#TreeActionBtn:hover {
    color: #e8eaf0;
    background-color: rgba(255, 255, 255, 0.08);
    border-radius: 4px;
}
QPushButton#TreeActionBtn[primary="true"] {
    color: #7eb4ff;
}
"""

GOAL_TREE_PANEL_QSS = """
QWidget#GoalDetailPanel {
    background-color: rgba(0, 0, 0, 0.2);
    border: 1px solid #4a4e5c;
    border-radius: 6px;
    margin: 4px 0;
}
QLabel#GoalDetailTitle { font-size: 13px; font-weight: 700; color: #ffffff; }
QLabel#GoalDetailStats { font-size: 12px; color: #e8ecf4; }
QLabel#SubGoalList {
    color: #ffffff;
    font-size: 13px;
    font-weight: 500;
    line-height: 1.35;
    background: transparent;
}
QLabel#SubGoalHint {
    color: #f0c060;
    font-size: 12px;
    font-weight: 600;
    background: transparent;
}
QLineEdit#SubGoalInput {
    background-color: #1a1b24;
    color: #d8dce8;
    border: 1px solid #3a3d4a;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 12px;
}
QSpinBox#SubtaskMinSpin {
    background-color: #1a1b24;
    color: #d8dce8;
    border: 1px solid #3a3d4a;
    border-radius: 6px;
    padding: 3px 4px;
    font-size: 11px;
    min-height: 22px;
}
QSpinBox#SubtaskMinSpin:focus { border-color: #4a6ad0; }
QPushButton#SubAddBtn {
    font-size: 12px;
    padding: 4px 10px;
    background-color: #252833;
    border: 1px solid #404558;
    color: #b8c8e8;
}
QPushButton#SubAddBtn:hover { background-color: #303448; }
QPushButton#GoalPauseBtn, QPushButton#GoalResumeBtn {
    background-color: #252833;
    border: 1px solid #404558;
    color: #a8c4ff;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 10px;
    min-height: 22px;
    border-radius: 5px;
}
QPushButton#GoalPauseBtn:hover, QPushButton#GoalResumeBtn:hover {
    background-color: #303448;
    border-color: #5a6a90;
}
"""


def _apply_widget_properties(widget: QWidget, **props) -> bool:
    """仅在 QSS 属性变化时 unpolish/polish，避免热路径把点击吃掉。"""
    changed = False
    for key, value in props.items():
        current = widget.property(key)
        if isinstance(value, bool):
            if current is None and not value:
                continue
            if bool(current) == value:
                continue
        elif current == value:
            continue
        widget.setProperty(key, value)
        changed = True
    if changed:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
    return changed


def apply_subtask_block_ui(
    block: QWidget,
    *,
    selected: bool,
    focused: bool,
) -> None:
    """设置子目标块选中/聚焦 QSS 属性。"""
    _apply_widget_properties(block, selected=selected, focused=focused)


def apply_goal_block_hover(block: QWidget, *, hovered: bool) -> None:
    """设置目标块悬停 QSS 属性。"""
    _apply_widget_properties(block, hovered=hovered)


def apply_goal_block_ui(
    block: QWidget,
    *,
    is_running: bool,
    selected: bool,
    focused: bool,
) -> None:
    """设置目标块运行/选中/子目标聚焦 QSS 属性。"""
    _apply_widget_properties(
        block,
        current=is_running,
        paused=not is_running,
        selected=selected,
        focused=focused,
    )


def apply_goal_root_row_state(row: QWidget, *, is_running: bool) -> None:
    """设置目标根行属性（块级高亮由 GoalBlock 承担，根行保持透明）。"""
    _apply_widget_properties(row, current=is_running, paused=not is_running)


@dataclass
class SubtaskActionCallbacks:
    """子目标行尾操作回调。"""

    on_focus: Optional[Callable[[], None]] = None
    on_pause: Optional[Callable[[], None]] = None
    on_complete: Optional[Callable[[], None]] = None
    on_decompose: Optional[Callable[[], None]] = None
    on_delete: Optional[Callable[[], None]] = None


def _connect_callback(
    btn: QPushButton,
    callback: Optional[Callable[[], None]],
) -> None:
    """连接无参回调；兼容 QPushButton.clicked 传入的 checked 参数。"""
    if callback is None:
        return
    btn.clicked.connect(lambda _checked=False, cb=callback: cb())


def _make_action_btn(
    text: str,
    *,
    tooltip: str = "",
    primary: bool = False,
    width: int = 20,
    parent: QWidget | None = None,
) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("TreeActionBtn")
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedSize(width, 20)
    if primary:
        btn.setProperty("primary", True)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def build_subtask_action_buttons(
    sub: Subtask,
    *,
    task_status: TaskStatus,
    current_id: Optional[str],
    callbacks: SubtaskActionCallbacks,
    parent: QWidget | None = None,
) -> QWidget:
    """构建 VS Code 风行尾操作区（默认隐藏，由 TreeRow hover/selected 控制）。"""
    wrap = QWidget(parent)
    wrap.setObjectName("TreeActions")
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    if task_status == TaskStatus.COMPLETED:
        wrap.hide()
        return wrap

    is_active = task_status == TaskStatus.ACTIVE
    can_start = task_status in (TaskStatus.ACTIVE, TaskStatus.PAUSED)

    is_current = is_active and sub.id == current_id and not sub.done

    if sub.is_leaf() and not sub.done:
        if is_current:
            if callbacks.on_pause is not None:
                btn = _make_action_btn("||", tooltip="暂停整个目标", parent=wrap)
                _connect_callback(btn, callbacks.on_pause)
                lay.addWidget(btn)
    if can_start and sub.is_leaf() and sub.can_finish() and callbacks.on_complete is not None:
        btn = _make_action_btn("✓", tooltip="完成并领取", parent=wrap)
        _connect_callback(btn, callbacks.on_complete)
        lay.addWidget(btn)

    wrap.setVisible(False)
    return wrap


def _make_detail_action_btn(
    text: str,
    *,
    object_name: str,
    tooltip: str = "",
    parent: QWidget | None = None,
) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName(object_name)
    btn.setCursor(Qt.PointingHandCursor)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def append_subtask_detail_actions(
    layout: QHBoxLayout,
    sub: Subtask,
    *,
    task_status: TaskStatus,
    current_id: Optional[str],
    callbacks: SubtaskActionCallbacks,
) -> None:
    """详情面板：子目标常显操作按钮（叶子 / 分组）。"""
    if task_status == TaskStatus.COMPLETED:
        return

    is_active = task_status == TaskStatus.ACTIVE
    can_start = task_status in (TaskStatus.ACTIVE, TaskStatus.PAUSED)
    can_modify = can_start

    if sub.is_container():
        if not (sub.done and sub.rewards_claimed) and callbacks.on_delete is not None:
            btn = _make_detail_action_btn(
                "删除",
                object_name="Danger",
                tooltip="删除此分组",
            )
            _connect_callback(btn, callbacks.on_delete)
            layout.addWidget(btn)
        return

    if not sub.is_leaf():
        return

    is_current = is_active and sub.id == current_id and not sub.done

    if not sub.done:
        if is_current:
            if callbacks.on_pause is not None:
                btn = _make_detail_action_btn(
                    "暂停",
                    object_name="GoalPauseBtn",
                    tooltip="暂停整个目标",
                )
                _connect_callback(btn, callbacks.on_pause)
                layout.addWidget(btn)
        elif can_start and callbacks.on_focus is not None:
            btn = _make_detail_action_btn(
                "开始运行",
                object_name="GoalResumeBtn",
                tooltip="开始运行此子目标",
            )
            _connect_callback(btn, callbacks.on_focus)
            layout.addWidget(btn)
        if can_modify and not sub.is_legacy_progress() and callbacks.on_decompose is not None:
            btn = _make_detail_action_btn(
                "分解",
                object_name="Ghost",
                tooltip="分解为多个子项",
            )
            _connect_callback(btn, callbacks.on_decompose)
            layout.addWidget(btn)

    show_complete = (
        can_start and sub.can_finish() and callbacks.on_complete is not None
    )
    if show_complete:
        btn = _make_detail_action_btn(
            "完成",
            object_name="Primary",
            tooltip="完成并领取奖励",
        )
        _connect_callback(btn, callbacks.on_complete)
        layout.addWidget(btn)

    if not (sub.done and sub.rewards_claimed) and callbacks.on_delete is not None:
        btn = _make_detail_action_btn(
            "删除",
            object_name="Danger",
            tooltip="删除此子目标",
        )
        _connect_callback(btn, callbacks.on_delete)
        layout.addWidget(btn)


class TreeRow(QWidget):
    """可单击选中的树行；悬停/选中时显示行尾操作。"""

    selected = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._actions_widget: QWidget | None = None
        self._selected: bool = False
        self.setAttribute(Qt.WA_Hover, True)

    def set_actions_widget(self, widget: QWidget) -> None:
        self._actions_widget = widget
        self._sync_actions_visible()

    def set_row_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self._sync_actions_visible()

    def set_row_focused(self, focused: bool) -> None:
        if bool(self.property("subcurrent")) == focused:
            return
        self.setProperty("subcurrent", focused)
        self.style().unpolish(self)
        self.style().polish(self)
        self._sync_actions_visible()

    def _sync_actions_visible(self) -> None:
        if self._actions_widget is None:
            return
        lay = self._actions_widget.layout()
        has_buttons = lay is not None and lay.count() > 0
        hovered = self.underMouse()
        focused = bool(self.property("subcurrent"))
        self._actions_widget.setVisible(
            has_buttons and (self._selected or hovered or focused)
        )

    def enterEvent(self, event) -> None:
        self._sync_actions_visible()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._sync_actions_visible()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            while child is not None and child is not self:
                if isinstance(child, QPushButton):
                    super().mousePressEvent(event)
                    return
                child = child.parentWidget()
            self.selected.emit()
        super().mousePressEvent(event)