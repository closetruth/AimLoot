"""开箱汇总（无动画）：秒开 / 批量开箱后的短结果面板。"""
from __future__ import annotations

from typing import Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from ..chest_opening import OpenResult
from .styles import BG_DIALOG, FONT_FAMILY, TEXT_PRIMARY
from ..text import format_amount

SUMMARY_QSS = f"""
QDialog {{
    background-color: {BG_DIALOG};
}}
QLabel {{
    color: {TEXT_PRIMARY};
    font-family: {FONT_FAMILY};
    font-size: 13px;
}}
QPushButton {{
    background-color: #252833;
    color: {TEXT_PRIMARY};
    border: 1px solid #404558;
    border-radius: 6px;
    padding: 6px 20px;
    min-width: 80px;
    min-height: 28px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: #303448;
    border-color: #5a6a90;
}}
"""


def format_chest_summary(
    opens: Sequence[Tuple[int, OpenResult]],
) -> str:
    """多箱汇总文案。"""
    n = len(opens)
    letters = sum(len(r.letters) for _, r in opens)
    gold = sum(r.gold for _, r in opens)
    diamond = sum(r.diamond for _, r in opens)
    lines = [
        f"开箱 {n} 个",
        f"字母 {letters} 个",
        f"金币 +{format_amount(gold)}",
        f"钻石 +{format_amount(diamond)}",
    ]
    return "\n".join(lines)


class ChestSummaryDialog(QDialog):
    """轻量汇总弹窗；点「收下」关闭。"""

    def __init__(
        self,
        opens: Sequence[Tuple[int, OpenResult]],
        parent: QWidget | None = None,
        *,
        title: str = "开箱结果",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.Dialog
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTitleHint
            | Qt.MSWindowsFixedSizeDialogHint
        )
        self.setModal(True)
        self.setStyleSheet(SUMMARY_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        head = QLabel(title, self)
        head.setAlignment(Qt.AlignCenter)
        head.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(head)

        body = QLabel(format_chest_summary(opens), self)
        body.setAlignment(Qt.AlignCenter)
        body.setWordWrap(True)
        layout.addWidget(body)

        btn = QPushButton("收下", self)
        btn.clicked.connect(self.accept)
        layout.addWidget(btn, alignment=Qt.AlignCenter)


def show_chest_summary(
    parent: QWidget | None,
    opens: Sequence[Tuple[int, OpenResult]],
    *,
    title: str = "开箱结果",
) -> None:
    if not opens:
        return
    dialog = ChestSummaryDialog(opens, parent=parent, title=title)
    dialog.exec()
