"""目标级操作（完成 / 删除）共享 UI 逻辑。"""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from .models import Reward
from .task_manager import TaskManager
from .ui_confirm import ask_yes_no
from .ui_slot_machine import SlotMachineDialog


def _apply_slot_bonus(manager: TaskManager, bonus: Reward) -> None:
    """老虎机额外奖励入账（空奖励跳过）。"""
    if not bonus.is_empty():
        manager.state.inventory.add(bonus)


def try_complete_goal(parent: QWidget, manager: TaskManager, task_id: str) -> bool:
    """尝试完成目标；成功返回 True。弹出老虎机判定额外奖励。"""
    task = manager.get(task_id)
    if task is None:
        return False
    if not manager.can_complete_task(task_id):
        # #region agent log
        from .task_manager import _agent_dbg
        _agent_dbg(
            "D",
            "goal_actions.py:try_complete_goal",
            "can_complete_task false",
            {
                "task_id": task_id,
                "title": task.title,
                "status": task.status.value,
                "has_unclaimed": task.has_unclaimed_subtasks(),
                "leaves": [
                    {
                        "id": s.id,
                        "title": s.title,
                        "done": s.done,
                        "claimed": s.rewards_claimed,
                        "can_finish": s.can_finish(),
                        "time_met": s.time_target_met(),
                    }
                    for s in task.iter_leaves()
                ],
            },
        )
        # #endregion
        QMessageBox.information(
            parent,
            "提示",
            "请先完成所有子目标，再完成此目标。",
        )
        return False
    reward = manager.complete(task_id)
    base = reward if reward is not None else Reward()
    dialog = SlotMachineDialog(base, task_title=f"目标完成 · {task.title}", parent=parent)
    dialog.reward_decided.connect(lambda bonus: _apply_slot_bonus(manager, bonus))
    dialog.exec()
    dialog.deleteLater()
    return True


def try_delete_goal(parent: QWidget, manager: TaskManager, task_id: str) -> bool:
    """尝试删除目标；用户确认并删除后返回 True。"""
    task = manager.get(task_id)
    if task is None:
        return False
    if not ask_yes_no(
        parent,
        "删除目标",
        f"确定要删除「{task.title}」吗？\n未领取的奖励将一并丢失。",
    ):
        return False
    manager.delete(task_id)
    return True
