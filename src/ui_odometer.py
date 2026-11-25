"""顶栏金币/钻石计数器：按位裁剪绘制 0–9 竖条。"""
from __future__ import annotations

import math

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


def place_scroll(amount: float, place: float) -> tuple[int, float]:
    """某一位的当前数字，以及滚向下一位的 0–1 进度。

    整数位不进位半格（否则 +0.2 后个位会永远停在两数字中间）。
    只有十分位连续翻。
    """
    if place <= 0:
        return 0, 0.0
    amount = max(0.0, float(amount))
    if place >= 1.0:
        rounded = round(amount, 1)
        return int(rounded / place) % 10, 0.0
    scaled = amount / float(place)
    # 10.2/0.1 在 IEEE 下常变成 101.999…，先推过整数边界
    whole = math.floor(scaled + 1e-8)
    frac = scaled - whole
    if frac < 0:
        frac = 0.0
    if frac > 1.0 - 1e-8:
        whole += 1
        frac = 0.0
    return int(whole) % 10, float(frac)


def integer_digit_count(amount: float) -> int:
    n = int(math.floor(max(0.0, float(amount))))
    if n <= 0:
        return 1
    return len(str(n))


class RollingAmount(QWidget):
    """固定「整数 + 小数点 + 十分位」，高度锁死以免撑高悬浮窗。"""

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RollingAmount")
        self._amount = 0.0
        self._rolling = False
        self._color = QColor(color)
        self._font = QFont("Microsoft YaHei UI", 11)
        self._font.setPixelSize(11)
        self._font.setWeight(QFont.Weight.Bold)
        self._digit_w = 8
        self._digit_h = 14
        self._recompute_metrics()
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._apply_fixed_size()

    def amount(self) -> float:
        return self._amount

    def set_amount(self, value: float) -> None:
        """静止刷新：对齐到 0.1 网格。开箱货币是 2 位小数（如 12.37），
        不量化十分位会停在两数字中间；量化后静止帧永远是整格。"""
        v = max(0.0, float(value))
        v = round(v * 10.0 + 1e-6) / 10.0
        if abs(v - self._amount) < 1e-9:
            return
        self._amount = v
        self._apply_fixed_size()
        self.update()

    def set_amount_scrolling(self, value: float) -> None:
        """追赶动画：保留中间值用于滚动（10.2 → 10.3 时十分位连续翻）。"""
        v = max(0.0, float(value))
        if abs(v - self._amount) < 1e-9:
            return
        self._amount = v
        self._apply_fixed_size()
        self.update()

    def _recompute_metrics(self) -> None:
        fm = QFontMetrics(self._font)
        self._digit_w = max(fm.horizontalAdvance(str(d)) for d in range(10))
        self._digit_h = max(fm.height(), 14)

    def _column_count(self) -> int:
        return integer_digit_count(self._amount)

    def _content_width(self) -> int:
        n = self._column_count()
        # 每列等宽：整数位 + 小数点（占一格）+ 十分位
        return (n + 2) * self._digit_w

    def _apply_fixed_size(self) -> None:
        w, h = self._content_width(), self._digit_h
        if self.width() == w and self.height() == h:
            return
        self.setFixedSize(w, h)

    def sizeHint(self) -> QSize:
        return QSize(self._content_width(), self._digit_h)

    def showEvent(self, event) -> None:
        self._recompute_metrics()
        self._apply_fixed_size()
        super().showEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(self._font)
        p.setPen(self._color)
        x = 0
        n = self._column_count()
        for i in range(n):
            place = 10 ** (n - 1 - i)
            self._paint_digit(p, x, float(place))
            x += self._digit_w
        # 小数点占与数字同宽的格子，在格内居中：与左右数字间距一致。
        p.setClipping(False)
        p.drawText(QRect(x, 0, self._digit_w, self._digit_h), Qt.AlignCenter, ".")
        x += self._digit_w
        self._paint_digit(p, x, 0.1)
        p.end()

    def _paint_digit(self, p: QPainter, x: int, place: float) -> None:
        digit, frac = place_scroll(self._amount, place)
        nxt = (digit + 1) % 10
        h = self._digit_h
        w = self._digit_w
        clip = QRect(x, 0, w, h)
        p.setClipRect(clip)
        y0 = int(round(-frac * h))
        p.drawText(QRect(x, y0, w, h), Qt.AlignCenter, str(digit))
        p.drawText(QRect(x, y0 + h, w, h), Qt.AlignCenter, str(nxt))