"""公共暗色主题样式常量。"""
from __future__ import annotations

# ---- 颜色 ----
BG_DIALOG = "#16161e"
BG_CARD = "#1e1f28"
BG_INPUT = "#12141a"
BG_HOVER = "#303448"
BORDER = "#2a2d38"
BORDER_HOVER = "#4a5068"
TEXT_PRIMARY = "#e8eaf0"
TEXT_MUTED = "#8b93a8"
ACCENT = "#3a5cff"
ACCENT_HOVER = "#4d6dff"
DANGER_BG = "#2a2222"
DANGER_BORDER = "#503838"
DANGER_TEXT = "#d09090"

FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"'

# ---- 公共对话框基础样式 ----
DARK_BASE_QSS = f"""
QDialog {{ background-color: {BG_DIALOG}; color: {TEXT_PRIMARY}; }}
QLabel {{ color: {TEXT_PRIMARY}; font-family: {FONT_FAMILY}; }}
QPushButton {{
    background-color: #252833;
    color: {TEXT_PRIMARY};
    border: 1px solid #3a3f52;
    border-radius: 8px;
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton:hover {{ background-color: {BG_HOVER}; border-color: {BORDER_HOVER}; }}
QPushButton#Primary {{ background-color: {ACCENT}; border-color: {ACCENT}; color: #ffffff; }}
QPushButton#Primary:hover {{ background-color: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#Ghost {{
    background-color: transparent;
    color: #b8bfd0;
    border: 1px solid #3a3f52;
}}
QPushButton#Ghost:hover {{ background-color: #252833; color: {TEXT_PRIMARY}; }}
QPushButton#Danger {{ color: {DANGER_TEXT}; border-color: {DANGER_BORDER}; background: {DANGER_BG}; }}
QPushButton#Danger:hover {{ background-color: #3a2828; border-color: #704040; color: #ffb0b0; }}
QLineEdit, QTextEdit, QSpinBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus {{ border-color: #4a6ad0; }}
QFrame#Card {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#Divider {{ background-color: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""
