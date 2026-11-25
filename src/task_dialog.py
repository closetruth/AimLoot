"""目标管理对话框：创建 / 暂停 / 恢复 / 完成 / 删除目标。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .branding import window_title
from .goal_actions import try_complete_goal, try_delete_goal
from .models import AppState, Task, TaskStatus
from .task_manager import TaskManager
from .ui.confirm import ask_yes_no
from .ui.goal_panel import GoalTreePanel
from .ui.qt import make_divider, make_section_title
from .ui.styles import DARK_BASE_QSS
from .ui.task_tree import GOAL_TREE_PANEL_QSS, TREE_QSS
from .text import format_duration
from .ui.week_runtime import WeekRuntimePanel

DIALOG_STYLESHEET = DARK_BASE_QSS + """
QTabWidget::pane {
    background: #1a1b24;
    border: 1px solid #2a2d38;
    border-radius: 10px;
    top: -1px;
    padding: 8px;
}
QTabBar::tab {
    background: transparent;
    color: #8b93a8;
    padding: 8px 16px;
    border-radius: 8px;
    margin-right: 6px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background: #252833;
    color: #e8eaf0;
    border: 1px solid #3a3f52;
}
QTabBar::tab:hover:!selected { color: #c8ceda; background: #1e1f28; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QFrame#Card {
    background-color: #1e1f28;
    border: 1px solid #2a2d38;
    border-radius: 12px;
}
QFrame#Card[active="true"] {
    border: 1px solid #3a5080;
    background-color: #1a2030;
}
QFrame#CreateCard {
    background-color: #1a1b24;
    border: 1px solid #2a2d38;
    border-radius: 12px;
}
QFrame#Divider { background-color: #2a2d38; max-height: 1px; min-height: 1px; border: none; }
QLabel#TaskTitle { font-size: 16px; font-weight: 700; color: #f0f2f8; }
QLabel#SectionTitle {
    font-size: 12px; font-weight: 700; color: #8b93a8;
    padding-bottom: 2px;
}
QLabel#Note { color: #9aa0b4; font-size: 12px; line-height: 1.4; }
QLabel#EmptyHint { color: #6e7588; font-size: 13px; padding: 40px 20px; }
""" + TREE_QSS + GOAL_TREE_PANEL_QSS


class TaskCard(QFrame):
    """单个目标卡片。"""

    action = Signal(str, str, str)  # (task_id, action_name, extra)

    def __init__(
        self,
        task: Task,
        *,
        manager: TaskManager,
        state: AppState,
        selected_subtask_id: str = "",
    ):
        super().__init__()
        self.task = task
        self.manager = manager
        self.state = state
        self.setObjectName("Card")
        self.setProperty("active", task.status == TaskStatus.ACTIVE)
        self._tree_panel: Optional[GoalTreePanel] = None
        self._build()
        if selected_subtask_id:
            self.set_selected_subtask_id(selected_subtask_id)

    def selected_subtask_id(self) -> str:
        if self._tree_panel is None:
            return ""
        return self._tree_panel.selected_subtask_id()

    def set_selected_subtask_id(self, subtask_id: str) -> None:
        if self._tree_panel is not None:
            self._tree_panel.set_selected_subtask_id(subtask_id)

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)

        if self.task.note:
            note = QLabel(self.task.note)
            note.setObjectName("Note")
            note.setWordWrap(True)
            v.addWidget(note)

        self._tree_panel = GoalTreePanel(
            self.task,
            self.manager,
            self.state,
            editable=self.task.status == TaskStatus.ACTIVE,
        )
        self._tree_panel.action.connect(self.action.emit)
        v.addWidget(self._tree_panel)

        v.addWidget(make_divider())
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if self.task.status == TaskStatus.ACTIVE:
            b_complete = QPushButton("完成目标")
            b_complete.setObjectName("Primary")
            b_complete.clicked.connect(
                lambda _c=False: self.action.emit(self.task.id, "complete", "")
            )
            btn_row.addWidget(b_complete)
        elif self.task.status == TaskStatus.PAUSED:
            b_complete = QPushButton("完成目标")
            b_complete.setObjectName("Ghost")
            b_complete.clicked.connect(
                lambda _c=False: self.action.emit(self.task.id, "complete", "")
            )
            btn_row.addWidget(b_complete)
        else:
            btn_row.addStretch(1)

        btn_row.addStretch(1)
        b_del = QPushButton("删除")
        b_del.setObjectName("Danger")
        b_del.clicked.connect(
            lambda _c=False: self.action.emit(self.task.id, "delete", "")
        )
        btn_row.addWidget(b_del)
        v.addLayout(btn_row)

    def update_stats(self) -> None:
        task = self.manager.get(self.task.id)
        if task is None or self._tree_panel is None:
            return
        self.task = task
        since = self.state.since_roll
        self._tree_panel.refresh(
            task,
            since_gold=since.gold,
            since_diamond=since.diamond,
        )


class TaskDialog(QDialog):
    """目标管理主对话框。"""

    state_changed = Signal()
    subtask_claimed = Signal(str, object)  # (title, Reward)
    goal_completed = Signal()

    def __init__(self, state: AppState, manager: TaskManager, parent=None):
        super().__init__(parent)
        self.state = state
        self.manager = manager
        self._card_selection: dict[str, str] = {}
        self._refreshing = False
        self._state_change_pending = False

        self.setWindowTitle(window_title("目标管理"))
        self.resize(640, 640)
        self.setStyleSheet(DIALOG_STYLESHEET)
        self._build()
        self.refresh()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        create_box = QFrame()
        create_box.setObjectName("CreateCard")
        cl = QVBoxLayout(create_box)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)
        cl.addWidget(make_section_title("新建目标"))
        self.input_title = QLineEdit()
        self.input_title.setPlaceholderText("目标标题，例如：写完文档第 3 章")
        self.input_note = QTextEdit()
        self.input_note.setPlaceholderText("备注（可选）")
        self.input_note.setFixedHeight(56)
        cl.addWidget(self.input_title)
        cl.addWidget(self.input_note)
        bt = QHBoxLayout()
        bt.addStretch(1)
        self.btn_create = QPushButton("创建目标")
        self.btn_create.setObjectName("Primary")
        self.btn_create.clicked.connect(self._on_create)
        bt.addWidget(self.btn_create)
        cl.addLayout(bt)
        v.addWidget(create_box)

        self.tabs = QTabWidget()
        self.tab_active = self._make_scroll_tab()
        self.tab_paused = self._make_scroll_tab()
        self.tab_done = self._make_scroll_tab()
        self.tabs.addTab(self.tab_active["widget"], "进行中")
        self.tabs.addTab(self.tab_paused["widget"], "已暂停")
        self.tabs.addTab(self.tab_done["widget"], "已完成")
        self.week_panel = WeekRuntimePanel(self.state, self.manager)
        self.tabs.addTab(self.week_panel, "本周")
        v.addWidget(self.tabs, 1)

    def _make_scroll_tab(self) -> dict:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setObjectName("TabInner")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(2, 2, 2, 8)
        layout.setSpacing(10)
        layout.addStretch(1)
        scroll.setWidget(inner)
        return {"widget": scroll, "inner": inner, "layout": layout}

    def _on_create(self) -> None:
        title = self.input_title.text().strip()
        note = self.input_note.toPlainText().strip()
        if not title:
            QMessageBox.information(self, "提示", "请输入目标标题")
            return
        self.manager.create(title, note)
        self.input_title.clear()
        self.input_note.clear()
        self.state_changed.emit()
        self.refresh()

    def _emit_state_changed(self) -> None:
        """延迟发出，避免在按钮回调里同步重建卡片导致卡死。"""
        if self._state_change_pending:
            return
        self._state_change_pending = True
        QTimer.singleShot(0, self._flush_state_change)

    def _flush_state_change(self) -> None:
        self._state_change_pending = False
        self.state_changed.emit()

    def _on_card_action(self, task_id: str, action: str, extra: str = "") -> None:
        if action == "pause":
            self.manager.pause(task_id)
        elif action == "resume":
            self.manager.resume(task_id)
        elif action == "subtask_toggle_fold":
            if self.manager.toggle_subtask_expand(task_id, extra):
                self._emit_state_changed()
            return
        elif action == "subtask_focus":
            if self.manager.start_subtask(task_id, extra):
                self._emit_state_changed()
            return
        elif action == "subtask_pause":
            task = self.manager.get(task_id)
            if task is not None and task.status == TaskStatus.ACTIVE:
                self.manager.pause(task_id)
                self._emit_state_changed()
            return
        elif action == "subtask_decompose":
            parts = extra.split("|", 1)
            if len(parts) < 2:
                return
            subtask_id, titles_raw = parts[0], parts[1]
            titles = [t.strip() for t in titles_raw.split(",") if t.strip()]
            if not titles:
                return
            if self.manager.decompose_subtask(task_id, subtask_id, titles):
                self._emit_state_changed()
            return
        elif action == "subtask_add":
            parts = extra.split("|")
            title = parts[0] if parts else ""
            target_minutes = None
            parent_subtask_id = None
            if len(parts) >= 2 and parts[1]:
                target_minutes = max(1, int(parts[1]))
            if len(parts) >= 3 and parts[2]:
                parent_subtask_id = parts[2]
            sub = self.manager.add_subtask(
                task_id,
                title,
                target_minutes=target_minutes,
                parent_subtask_id=parent_subtask_id,
            )
            if sub is None:
                return
            self._card_selection[task_id] = sub.id
            self._emit_state_changed()
            return
        elif action == "subtask_confirm_done":
            task = self.manager.get(task_id)
            if task is None:
                return
            sub = task.find_subtask(extra)
            if sub is None:
                self.refresh()
                return
            if not sub.can_finish():
                if not sub.done:
                    QMessageBox.information(
                        self,
                        "提示",
                        f"目标「{sub.title}」时长未达标（"
                        f"{format_duration(sub.active_seconds)}/"
                        f"{format_duration(sub.target_seconds)}），暂不能完成。",
                    )
                self.refresh()
                return
            ret = QMessageBox.question(
                self,
                "完成目标",
                f"完成目标「{sub.title}」并领取奖励？",
            )
            if ret != QMessageBox.Yes:
                self.refresh()
                return
            was_done = sub.done
            reward = self.manager.complete_and_claim_subtask(task_id, extra)
            if not was_done and sub.done:
                self.goal_completed.emit()
            if reward is not None:
                self.subtask_claimed.emit(sub.title, reward)
            self._emit_state_changed()
            return
        elif action == "subtask_delete":
            task = self.manager.get(task_id)
            if task is None:
                return
            sub = task.find_subtask(extra)
            if sub is None:
                return
            if not sub.rewards_claimed:
                p = sub.pending_summary()
                if sub.done or p.gold or p.diamond:
                    if not ask_yes_no(
                        self,
                        "删除目标",
                        f"「{sub.title}」有未领取奖励，确定删除吗？",
                    ):
                        return
            self.manager.delete_subtask(task_id, extra)
        elif action == "delete":
            if try_delete_goal(self, self.manager, task_id):
                self._emit_state_changed()
            return
        elif action == "complete":
            if try_complete_goal(self, self.manager, task_id):
                self.goal_completed.emit()
                self._emit_state_changed()
            return
        # 由主程序统一 refresh 对话框，避免与按钮回调同步重建冲突
        self._emit_state_changed()

    def _capture_card_selections(self) -> None:
        for tab in (self.tab_active, self.tab_paused, self.tab_done):
            for card in tab["inner"].findChildren(TaskCard):
                self._card_selection[card.task.id] = card.selected_subtask_id()

    def refresh_stats(self) -> None:
        """轻量刷新：只更新各卡片上的操作数/奖励，不重建列表。"""
        for tab in (self.tab_active, self.tab_paused, self.tab_done):
            for card in tab["inner"].findChildren(TaskCard):
                card.update_stats()
        self.week_panel.refresh()

    def refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self._capture_card_selections()
            self._fill_tab(self.tab_active, self.manager.by_status(TaskStatus.ACTIVE))
            self._fill_tab(self.tab_paused, self.manager.by_status(TaskStatus.PAUSED))
            done = sorted(
                self.manager.by_status(TaskStatus.COMPLETED),
                key=lambda t: t.completed_at or 0,
                reverse=True,
            )
            self._fill_tab(self.tab_done, done)

            self.tabs.setTabText(0, f"进行中 ({len(self.manager.by_status(TaskStatus.ACTIVE))})")
            self.tabs.setTabText(1, f"已暂停 ({len(self.manager.by_status(TaskStatus.PAUSED))})")
            self.tabs.setTabText(2, f"已完成 ({len(self.manager.by_status(TaskStatus.COMPLETED))})")
            self.week_panel.refresh()
        finally:
            self._refreshing = False

    def _clear_tab_layout(self, layout: QVBoxLayout) -> None:
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def _fill_tab(self, tab: dict, tasks) -> None:
        layout: QVBoxLayout = tab["layout"]
        self._clear_tab_layout(layout)
        if not tasks:
            empty = QLabel("暂无目标")
            empty.setObjectName("EmptyHint")
            empty.setAlignment(Qt.AlignCenter)
            layout.addWidget(empty)
        else:
            for t in tasks:
                card = TaskCard(
                    t,
                    manager=self.manager,
                    state=self.state,
                    selected_subtask_id=self._card_selection.get(t.id, ""),
                )
                card.action.connect(self._on_card_action)
                layout.addWidget(card)
        layout.addStretch(1)