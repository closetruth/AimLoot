"""皇室战争式开箱动画弹窗：宝箱发光晃动 → 字母卡逐个滑出 + 货币结算。

结果由调用方提前生成并已提交到状态（崩溃安全），本弹窗纯展示。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..chest_opening import OpenResult
from .qt import center_dialog_on_parent
from .roll_bar import CHEST_RARITY_COLORS, CHEST_RARITY_NAMES, _draw_chest

DIALOG_QSS = """
QDialog {
    background-color: #14151d;
}
QLabel {
    color: #e8eaf0;
    font-size: 13px;
}
QPushButton {
    background-color: #252833;
    color: #e8eaf0;
    border: 1px solid #404558;
    border-radius: 6px;
    padding: 6px 20px;
    min-width: 80px;
    min-height: 28px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #303448;
    border-color: #5a6a90;
}
"""

_CARD_W = 92
_CARD_H = 108
_PHASE1_MS = 1300     # 开箱发光晃动时长
_CARD_DELAY_MS = 260  # 字母卡逐个间隔
_CARD_SLIDE_MS = 280  # 单张滑出时长


class _ChestStage(QWidget):
    """第一阶段：绘制一个发光晃动的宝箱。"""

    def __init__(self, rarity: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rarity = max(0, min(4, int(rarity)))
        self._shake = 0.0
        self.setMinimumSize(280, 140)

    def get_shake(self) -> float:
        return self._shake

    def set_shake(self, value: float) -> None:
        self._shake = value
        self.update()

    shake = Property(float, get_shake, set_shake)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx = self.width() / 2 + self._shake * 6.0
        cy = self.height() * 0.58
        _draw_chest(
            p, cx, cy, 64.0,
            reached=True, rarity=self._rarity, flash_on=True,
        )
        p.end()


class _LetterCard(QWidget):
    """字母卡：未翻时背面显示「?」，点击翻面显示字母 + 稀有度 + 计数。"""

    def __init__(
        self, letter: str, rarity: int, count: int, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._letter = str(letter).upper()
        self._rarity = max(0, min(4, int(rarity)))
        self._count = count
        self._face_up = False
        self.setFixedSize(_CARD_W, _CARD_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: #1e2029;
                border: 2px solid {CHEST_RARITY_COLORS[self._rarity]};
                border-radius: 10px;
            }}
        """)

    def is_face_up(self) -> bool:
        return self._face_up

    def flip_up(self) -> None:
        self._face_up = True
        self.update()

    def mousePressEvent(self, event) -> None:
        if not self._face_up:
            # 交给弹窗处理翻面 + 出下一张
            self.parent().parent()._on_card_clicked(self)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(CHEST_RARITY_COLORS[self._rarity])

        if not self._face_up:
            # 背面：大「?」+ 稀有度名
            font = QFont("Arial", 34, QFont.Bold)
            p.setFont(font)
            p.setPen(color)
            p.drawText(
                QRectF(0, 4, self.width(), self.height() * 0.56),
                Qt.AlignCenter,
                "?",
            )
            font2 = QFont("Microsoft YaHei", 9)
            p.setFont(font2)
            p.setPen(QColor("#aeb6c8"))
            p.drawText(
                QRectF(0, self.height() * 0.6, self.width(), 20),
                Qt.AlignCenter,
                CHEST_RARITY_NAMES[self._rarity],
            )
            p.end()
            return

        font = QFont("Arial", 30, QFont.Bold)
        p.setFont(font)
        p.setPen(color)
        p.drawText(
            QRectF(0, 6, self.width(), self.height() * 0.52),
            Qt.AlignCenter,
            self._letter,
        )

        font2 = QFont("Microsoft YaHei", 9)
        p.setFont(font2)
        p.setPen(QColor("#aeb6c8"))
        p.drawText(
            QRectF(0, self.height() * 0.52, self.width(), 22),
            Qt.AlignCenter,
            CHEST_RARITY_NAMES[self._rarity],
        )

        if self._count > 1:
            p.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
            p.setPen(QColor("#e8eaf0"))
            p.drawText(
                QRectF(0, self.height() * 0.76, self.width(), 20),
                Qt.AlignCenter,
                f"x{self._count}",
            )
        p.end()


class OpenChestDialog(QDialog):
    """开箱动画弹窗（模态）。构造时传入已生成的结果，展示完点「收下」关闭。"""

    def __init__(
        self,
        result: OpenResult,
        rarity: int,
        parent: QWidget | None = None,
        letter_totals: Optional[Dict[Tuple[str, int], int]] = None,
    ) -> None:
        """letter_totals: {(字母, 稀有度): 该组合累计数量}，用于卡片显示 xN。"""
        super().__init__(parent)
        self._result = result
        self._rarity = max(0, min(4, int(rarity)))
        self._letter_totals = letter_totals or {}
        self._cards: List[_LetterCard] = []
        self._revealed = 0          # 已翻开的卡片数
        self._flipped = 0           # 已翻面显示字母的卡片数
        self._timers: List[QTimer] = []

        self.setWindowTitle("开箱")
        self.setWindowFlags(
            Qt.Dialog
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTitleHint
            | Qt.MSWindowsFixedSizeDialogHint
        )
        self.setModal(True)
        self.setStyleSheet(DIALOG_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        title = QLabel(f"开箱 · {CHEST_RARITY_NAMES[self._rarity]}宝箱", self)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        self._stage = _ChestStage(self._rarity, self)
        self._stage.setFixedHeight(140)
        layout.addWidget(self._stage, alignment=Qt.AlignCenter)

        # 字母卡行：HBox 用伸缩居中，卡片逐个出现
        self._cards_row = QWidget(self)
        self._cards_row.setFixedHeight(_CARD_H)
        cards_layout = QHBoxLayout(self._cards_row)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(10)
        cards_layout.addStretch(1)
        self._cards_layout = cards_layout
        cards_layout.addStretch(1)
        layout.addWidget(self._cards_row)

        self._currency_label = QLabel("", self)
        self._currency_label.setAlignment(Qt.AlignCenter)
        self._currency_label.setStyleSheet("color: #ffd56a; font-size: 13px;")
        layout.addWidget(self._currency_label)

        self._close_btn = QPushButton("收下", self)
        self._close_btn.clicked.connect(self.accept)
        self._close_btn.setEnabled(False)
        layout.addWidget(self._close_btn, alignment=Qt.AlignCenter)

        self._build_cards()
        self._start_animation()

    def _build_cards(self) -> None:
        """预建全部字母卡(隐藏)，逐张点击揭面。"""
        for letter, rar in self._result.letters:
            total = self._letter_totals.get((letter, rar), 0)
            card = _LetterCard(letter, rar, max(1, total), self._cards_row)
            self._cards.append(card)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            card.setVisible(False)

    # ---- 动画 ----

    def _start_animation(self) -> None:
        shake = QPropertyAnimation(self._stage, b"shake", self)
        shake.setDuration(_PHASE1_MS)
        shake.setStartValue(0.0)
        shake.setKeyValueAt(0.5, 1.0)
        shake.setEndValue(0.0)
        shake.setEasingCurve(QEasingCurve.InOutSine)
        shake.finished.connect(self._show_first_back)
        shake.start()

    def _show_first_back(self) -> None:
        """动画结束：翻出第一张盖着的卡片，等待点击。"""
        self._stage.setVisible(False)
        self._revealed = 1
        self._show_card(self._cards[0])
        self._currency_label.setText(self._currency_text(self._result))

    def _show_card(self, card: _LetterCard) -> None:
        card.setVisible(True)
        anim = QPropertyAnimation(card, b"pos", self)
        start_pos = card.pos()
        anim.setDuration(_CARD_SLIDE_MS)
        anim.setStartValue(QPointF(start_pos.x(), start_pos.y() + 40))
        anim.setEndValue(start_pos)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()

    def _on_card_clicked(self, card: _LetterCard) -> None:
        """点击一张盖着的卡片：翻面显示字母；下一张卡片滑入。"""
        if card.is_face_up():
            return
        card.flip_up()
        self._flipped += 1
        if self._flipped >= len(self._cards):
            self._close_btn.setEnabled(True)
            return
        # 翻出下一张盖着的卡片
        if self._revealed < len(self._cards):
            self._show_card(self._cards[self._revealed])
            self._revealed += 1

    def _currency_text(self, result: OpenResult) -> str:
        parts = []
        if result.gold > 0:
            parts.append(f"+{result.gold:g} 金币")
        if result.diamond > 0:
            parts.append(f"+{result.diamond:g} 钻石")
        return "  ".join(parts) if parts else "本箱未掉落货币"

    def showEvent(self, event) -> None:
        super().showEvent(event)
        center_dialog_on_parent(self)

    def closeEvent(self, event) -> None:
        for t in self._timers:
            t.stop()
        super().closeEvent(event)