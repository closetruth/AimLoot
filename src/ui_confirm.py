"""置顶、样式独立的确认对话框。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from .ui_qt import center_dialog_on_parent

CONFIRM_QSS = """
QMessageBox {
    background-color: #1e1f28;
}
QLabel {
    color: #e8eaf0;
    font-size: 13px;
    min-width: 240px;
}
QPushButton {
    background-color: #252833;
    color: #e8eaf0;
    border: 1px solid #404558;
    border-radius: 6px;
    padding: 6px 16px;
    min-width: 72px;
    min-height: 28px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #303448;
    border-color: #5a6a90;
}
"""


def ask_yes_no(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    default_no: bool = True,
) -> bool:
    """显示置顶确认框；用户点「确定」返回 True。"""
    box = QMessageBox(
        QMessageBox.Question,
        title,
        text,
        QMessageBox.Yes | QMessageBox.No,
        parent,
    )
    box.setDefaultButton(
        QMessageBox.No if default_no else QMessageBox.Yes,
    )
    box.setWindowModality(Qt.ApplicationModal)
    box.setWindowFlags(
        Qt.Dialog
        | Qt.WindowStaysOnTopHint
        | Qt.WindowTitleHint
        | Qt.MSWindowsFixedSizeDialogHint
    )
    box.setStyleSheet(CONFIRM_QSS)
    yes_btn = box.button(QMessageBox.Yes)
    no_btn = box.button(QMessageBox.No)
    if yes_btn is not None:
        yes_btn.setText("确定")
    if no_btn is not None:
        no_btn.setText("取消")
    center_dialog_on_parent(box)
    box.raise_()
    box.activateWindow()
    return box.exec() == QMessageBox.Yes