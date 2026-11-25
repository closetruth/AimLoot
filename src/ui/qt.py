"""通用 Qt 小工具：标签、标题、布局清理。"""
from __future__ import annotations

from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QWidget


def center_dialog_on_parent(dialog: QDialog) -> None:
    """把弹窗居中到父窗口（无父窗口时不动）。"""
    parent = dialog.parentWidget()
    if parent is None:
        return
    dialog.adjustSize()
    anchor = parent.frameGeometry()
    dialog.move(
        anchor.center().x() - dialog.width() // 2,
        anchor.center().y() - dialog.height() // 2,
    )


def make_section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("SectionTitle")
    return lbl


def make_divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


def set_label_text(label: QLabel, text: str) -> None:
    if label.text() != text:
        label.setText(text)


def set_label_html(label: QLabel, html: str) -> None:
    if label.text() != html:
        label.setText(html)


def html_label_width(label: QLabel) -> int:
    doc = QTextDocument()
    doc.setDefaultFont(label.font())
    doc.setHtml(label.text())
    return max(int(doc.idealWidth()) + 8, 0)


def pin_html_label_width(label: QLabel) -> bool:
    width = html_label_width(label)
    if label.minimumWidth() != width:
        label.setMinimumWidth(width)
        return True
    return False


def hide_and_delete(widget: QWidget) -> None:
    """先 hide 再 deleteLater。

    不要 setParent(None)：无父控件会变成顶层窗，挡住悬浮窗点击。
    """
    widget.hide()
    widget.deleteLater()


def clear_layout(layout) -> None:
    """递归拆掉 layout 里的控件和子 layout。"""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            hide_and_delete(widget)
        elif item.layout() is not None:
            clear_layout(item.layout())


def drain_layout_widgets(layout) -> None:
    """只拆顶层控件，用于整棵树宿主。"""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            hide_and_delete(widget)