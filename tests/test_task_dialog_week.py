from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QScrollArea

from src.models import AppState
from src.power_monitor import PowerMonitor
from src.task_dialog import TaskDialog
from src.task_manager import TaskManager
from src.ui.week_runtime import WEEK_PANE_BG

_app = QApplication.instance() or QApplication([])


class TaskDialogWeekTabTests(unittest.TestCase):
    def test_has_week_tab_with_seven_columns(self):
        state = AppState()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        manager = TaskManager(state, PowerMonitor())
        manager._intervals_path = Path(tmp.name) / "runtime_intervals.json"
        dlg = TaskDialog(state, manager)
        self.assertEqual(dlg.tabs.count(), 4)
        self.assertEqual(dlg.tabs.tabText(3), "本周")
        self.assertEqual(dlg.week_panel.grid.column_count, 7)
        self.assertGreaterEqual(dlg.width(), 540)
        dlg.refresh_stats()
        dlg.close()

    def test_week_grid_scrolls_vertically(self):
        state = AppState()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        manager = TaskManager(state, PowerMonitor())
        manager._intervals_path = Path(tmp.name) / "runtime_intervals.json"
        dlg = TaskDialog(state, manager)
        panel = dlg.week_panel
        self.assertIsInstance(panel.grid_scroll, QScrollArea)
        self.assertIs(panel.grid_scroll.widget(), panel.grid)
        self.assertGreaterEqual(panel.grid.minimumHeight(), 280)
        self.assertLessEqual(panel.legend_scroll.maximumHeight(), 88)
        self.assertIn(WEEK_PANE_BG, panel.grid_scroll.styleSheet())
        self.assertIn(WEEK_PANE_BG, panel.legend_scroll.styleSheet())
        self.assertTrue(panel.grid_scroll.viewport().autoFillBackground())
        self.assertTrue(panel.legend_scroll.viewport().autoFillBackground())
        dlg.close()
