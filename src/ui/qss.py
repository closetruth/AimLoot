"""悬浮窗样式。树节点样式见 ui_task_tree.TREE_QSS / GOAL_TREE_PANEL_QSS。"""
from __future__ import annotations

from .task_tree import GOAL_TREE_PANEL_QSS, TREE_QSS

WIDGET_STYLESHEET = """
QWidget#WidgetWindow {
    background-color: #1c1c26;
}
QWidget#WidgetRoot {
    background-color: #1c1c26;
    border-radius: 12px;
    border: 1px solid #3a3f52;
}
QLabel { color: #f5f5f7; font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"; }
QWidget#DragHandle { background: transparent; }
QLabel#Title { font-size: 15px; font-weight: 700; background: transparent; }
QLabel#Subtle { color: #d0d4e0; font-size: 12px; }
QLabel#SectionTitle {
    font-size: 12px; font-weight: 700; color: #b8c0d4;
    padding-bottom: 2px;
}
QLabel#GlobalSummary { font-size: 11px; font-weight: 500; }
QLabel#GlobalCap { font-size: 11px; font-weight: 500; background: transparent; }
QWidget#RollingAmount { background: transparent; }
QLabel#RollHistCap { color: #a8b0c4; font-size: 10px; }
QLabel#RollHist { color: #b8c0d4; font-size: 9px; line-height: 1.25; }
QLabel#TaskTitle { font-size: 14px; font-weight: 700; color: #ffffff; }
QPushButton#GoalAddBtn {
    font-size: 12px;
    padding: 4px 10px;
    background-color: #252833;
    border: 1px solid #404558;
    color: #b8c8e8;
}
QPushButton#GoalAddBtn:hover { background-color: #303448; }
QPushButton#Primary {
    background-color: #3a5cff;
    border: 1px solid #3a5cff;
    color: #ffffff;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 10px;
    min-height: 22px;
    border-radius: 5px;
}
QPushButton#Primary:hover { background-color: #4d6dff; border-color: #4d6dff; }
QPushButton#Ghost {
    background-color: transparent;
    color: #b8bfd0;
    border: 1px solid #404558;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 10px;
    min-height: 22px;
    border-radius: 5px;
}
QPushButton#Ghost:hover { background-color: #252833; color: #e8eaf0; }
QPushButton#Danger {
    color: #d09090;
    border: 1px solid #503838;
    background: #2a2222;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 10px;
    min-height: 22px;
    border-radius: 5px;
}
QPushButton#Danger:hover {
    background-color: #3a2828;
    border-color: #704040;
    color: #ffb0b0;
}
QScrollArea#SubGoalScroll { background-color: #1c1c26; border: none; }
QWidget#SubGoalViewport { background-color: #1c1c26; }
QWidget#SubGoalContainer { background-color: #1c1c26; }
QScrollBar#SubGoalHBar:horizontal {
    height: 10px;
    background-color: #1c1c26;
    border: none;
    margin: 4px 4px 0 4px;
}
QScrollBar#SubGoalHBar::groove:horizontal {
    background-color: #1c1c26;
    border: none;
    height: 10px;
    border-radius: 5px;
}
QScrollBar#SubGoalHBar::sub-page:horizontal,
QScrollBar#SubGoalHBar::add-page:horizontal {
    background-color: #1c1c26;
    border: none;
}
QScrollBar#SubGoalHBar::handle:horizontal {
    background-color: #e8e8e8;
    min-width: 64px;
    border-radius: 5px;
    margin: 0;
    border: none;
}
QScrollBar#SubGoalHBar::handle:horizontal:hover { background-color: #ffffff; }
QScrollBar#SubGoalHBar::handle:horizontal:disabled { background-color: #5a5a62; }
QScrollBar#SubGoalHBar::add-line:horizontal,
QScrollBar#SubGoalHBar::sub-line:horizontal {
    width: 0;
    height: 0;
    border: none;
    background: none;
}
QScrollArea#SubGoalScroll QScrollBar:vertical {
    width: 10px;
    background-color: #1c1c26;
    border: none;
    margin: 2px 2px 2px 0;
}
QScrollArea#SubGoalScroll QScrollBar::groove:vertical {
    background-color: #1c1c26;
    border: none;
    width: 10px;
    border-radius: 5px;
}
QScrollArea#SubGoalScroll QScrollBar::sub-page:vertical,
QScrollArea#SubGoalScroll QScrollBar::add-page:vertical {
    background-color: #1c1c26;
    border: none;
}
QScrollArea#SubGoalScroll QScrollBar::handle:vertical {
    background-color: #e8e8e8;
    min-height: 40px;
    border-radius: 5px;
    margin: 0;
    border: none;
}
QScrollArea#SubGoalScroll QScrollBar::handle:vertical:hover { background-color: #ffffff; }
QScrollArea#SubGoalScroll QScrollBar::handle:vertical:disabled { background-color: #5a5a62; }
QScrollArea#SubGoalScroll QScrollBar::add-line:vertical,
QScrollArea#SubGoalScroll QScrollBar::sub-line:vertical {
    width: 0;
    height: 0;
    border: none;
    background: none;
}
QWidget#SubGoalActions { background: transparent; }
QPushButton {
    background-color: #2a2d3a;
    color: #f5f5f7;
    border: 1px solid #404558;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}
QPushButton:hover { background-color: #343848; }
QPushButton:pressed { background-color: #222530; }
QPushButton#CloseBtn, QPushButton#MinBtn {
    background-color: transparent;
    border: none;
    padding: 0px 6px;
    font-size: 14px;
    color: #c0c4d0;
}
QPushButton#CloseBtn:hover { color: #ff7474; }
QFrame#Divider { background-color: #2a2d38; max-height: 1px; min-height: 1px; }
""" + TREE_QSS + GOAL_TREE_PANEL_QSS
