"""目标树共用函数：标题解析、签名、未领奖判断。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import AppState, Reward, Subtask, Task, TaskStatus
from src.paths import project_root
from src.ui.goal_shared import (
    completion_bonus_gold,
    goal_detail_actions_signature,
    parse_decompose_titles,
    subtree_has_unclaimed,
)


class ParseDecomposeTitlesTests(unittest.TestCase):
    def test_comma_and_chinese_comma(self):
        self.assertEqual(
            parse_decompose_titles("子项1, 子项2，子项3"),
            ["子项1", "子项2", "子项3"],
        )

    def test_empty_and_spaces(self):
        self.assertEqual(parse_decompose_titles("  , ， "), [])
        self.assertEqual(parse_decompose_titles("只一项"), ["只一项"])


class GoalTreeSharedLogicTests(unittest.TestCase):
    def test_completion_bonus_default(self):
        self.assertEqual(completion_bonus_gold(AppState()), 0.5)

    def test_subtree_has_unclaimed_pending(self):
        leaf = Subtask(title="叶", pending_rewards=[Reward(gold=1)])
        group = Subtask(title="组", children=[leaf])
        self.assertTrue(subtree_has_unclaimed(group))
        self.assertFalse(subtree_has_unclaimed(Subtask(title="空")))

    def test_goal_detail_signature_changes_with_status(self):
        task = Task(title="t", status=TaskStatus.PAUSED)
        paused = goal_detail_actions_signature(task, None)
        task.status = TaskStatus.ACTIVE
        active = goal_detail_actions_signature(task, None)
        self.assertNotEqual(paused, active)

    def test_project_root_contains_run_py(self):
        self.assertTrue((project_root() / "run.py").exists())


if __name__ == "__main__":
    unittest.main()
