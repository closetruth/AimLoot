"""无边框窗拖动：一律交给系统 startSystemMove，不用 QWidget.move()。"""
from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QPushButton, QWidget

from .roll_bar import EasedProgressBar


class SystemMovable(Protocol):
    def begin_user_move(self) -> None: ...


class DragHandleBar(QWidget):
    """顶栏拖动手柄。"""

    def __init__(self, host: SystemMovable, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        self.setObjectName("DragHandle")
        self.setCursor(Qt.SizeAllCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._host.begin_user_move()
            event.accept()
            return
        super().mousePressEvent(event)


class SystemMoveFilter(QObject):
    """给一块区域（如全局统计）安装左键系统拖动。"""

    def __init__(self, host: SystemMovable, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._host = host

    def attach(self, root: QWidget) -> None:
        root.installEventFilter(self)
        for child in root.findChildren(QWidget):
            if isinstance(child, (QPushButton, EasedProgressBar)):
                continue
            child.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if isinstance(obj, EasedProgressBar):
            return False
        if event.type() == QEvent.Type.MouseButtonPress:
            me = event
            if isinstance(me, QMouseEvent) and me.button() == Qt.LeftButton:
                self._host.begin_user_move()
                return True
        return False
