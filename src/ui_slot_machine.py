"""老虎机弹窗：完成目标 / 领取宝箱时弹出，转出额外奖励。

三轴转轮，符号池：金 / 钻 / ×。三同大奖、两同小奖、无匹配空手。
结果与奖励在构造时一次性生成（纯展示 + 提前结算）：任何路径关闭
（收下 / Esc / 关窗）都会经 _finalize 恰好结算一次，奖励不丢。
"""
from __future__ import annotations

import random
from functools import partial
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .branding import window_title
from .models import Reward
from .ui_styles import BG_CARD, BORDER, DARK_BASE_QSS, TEXT_MUTED
from .ui_text import format_amount, format_reward_gain

SLOT_QSS = DARK_BASE_QSS + f"""
QLabel#SlotTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#SlotSub {{ color: {TEXT_MUTED}; font-size: 12px; }}
QLabel#SlotResult {{ font-size: 14px; font-weight: 600; min-height: 40px; }}
"""

_SYM_COIN = 0
_SYM_DIAMOND = 1
_SYM_MISS = 2

# 每轴独立均匀取样；计数：金 6/12、钻 3/12、× 3/12
_REEL_STRIP = (0, 0, 0, 1, 0, 2, 1, 0, 2, 0, 1, 2)
_STRIP_LEN = len(_REEL_STRIP)

_SYM_CHAR = {_SYM_COIN: "金", _SYM_DIAMOND: "钻", _SYM_MISS: "×"}
_SYM_COLOR = {_SYM_COIN: "#f5c842", _SYM_DIAMOND: "#5ec8f2", _SYM_MISS: "#5a5e6e"}

# (匹配类型, 符号) -> (倍率下界, 倍率上界)，作用于 base 对应币种
_PAYOUTS = {
    ("triple", _SYM_COIN): (1.5, 4.0),
    ("triple", _SYM_DIAMOND): (0.3, 1.0),
    ("pair", _SYM_COIN): (0.3, 1.2),
    ("pair", _SYM_DIAMOND): (0.05, 0.3),
}
_FALLBACK_BASE = 1.0  # base 该币种为 0 时的兜底基准（宝箱场景也有实奖）

_TICK_MS = 55             # 每 tick 滚动 1 格
_STOP_START_MS = 600      # 第一轴开始停的时间
_STOP_STAGGER_MS = 200    # 相邻两轴开始停的间隔
_REEL_W = 76
_REEL_H = 108
_VISIBLE_ROWS = 3         # 每轴可见 3 格，中心行为准


def spin_result(rng: Optional[random.Random] = None) -> Tuple[int, int, int]:
    """三轴各自独立从 _REEL_STRIP 均匀取样。"""
    r = rng if rng is not None else random.Random()
    return (r.choice(_REEL_STRIP), r.choice(_REEL_STRIP), r.choice(_REEL_STRIP))


def _match_of(s0: int, s1: int, s2: int) -> Tuple[str, Optional[int]]:
    """返回 ("triple", sym) / ("pair", sym) / ("none", None)。

    全同且非 × → triple；某金/钻恰好出现 2 次 → pair；其余 → none。
    """
    if s0 == s1 == s2:
        return ("triple", s0) if s0 != _SYM_MISS else ("none", None)
    for sym in (_SYM_COIN, _SYM_DIAMOND):
        if (s0, s1, s2).count(sym) == 2:
            return ("pair", sym)
    return ("none", None)


def compute_bonus(
    s0: int,
    s1: int,
    s2: int,
    base_reward: Optional[Reward] = None,
    rng: Optional[random.Random] = None,
) -> Reward:
    """三同/两同 → 倍率表作用在 base 对应币种上；无匹配返回空 Reward。

    eff = base 该币种（为 0 时兜底 _FALLBACK_BASE），bonus = round(eff × 倍率, 2)。
    只产出与匹配符号同币种的奖励。
    """
    r = rng if rng is not None else random.Random()
    base = base_reward if base_reward is not None else Reward()
    kind, sym = _match_of(s0, s1, s2)
    if kind == "none":
        return Reward()
    lo, hi = _PAYOUTS[(kind, sym)]
    if sym == _SYM_COIN:
        eff = base.gold if base.gold > 0 else _FALLBACK_BASE
        return Reward(gold=round(eff * r.uniform(lo, hi), 2))
    eff = base.diamond if base.diamond > 0 else _FALLBACK_BASE
    return Reward(diamond=round(eff * r.uniform(lo, hi), 2))


class _ReelWidget(QWidget):
    """单轴转轮：55ms/tick 滚动符号带，停稳在目标符号上。"""

    stopped = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(_REEL_W, _REEL_H)
        self._offset = random.randrange(_STRIP_LEN)
        self._target: Optional[int] = None
        self._stopped = True
        self._flash: Optional[str] = None
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)

    def start_spin(self) -> None:
        self._target = None
        self._stopped = False
        self._flash = None
        self._timer.start()

    def stop_at(self, symbol: int) -> None:
        """从当前 offset 往后找最近一个等于 symbol 的格子作为停点。"""
        if self._stopped or self._target is not None:
            return
        for step in range(1, _STRIP_LEN + 1):
            if _REEL_STRIP[(self._offset + step) % _STRIP_LEN] == symbol:
                self._target = self._offset + step
                return

    def is_stopped(self) -> bool:
        return self._stopped

    def center_symbol(self) -> int:
        return _REEL_STRIP[self._offset % _STRIP_LEN]

    def set_flash(self, color: Optional[str]) -> None:
        """中奖高亮：边框用给定颜色；None 熄灭。"""
        self._flash = color
        self.update()

    def stop_timer(self) -> None:
        self._timer.stop()

    def _on_tick(self) -> None:
        self._offset += 1
        self.update()
        if self._target is not None and self._offset >= self._target:
            self._halt()

    def _halt(self) -> None:
        self._offset = self._target
        self._timer.stop()
        self._stopped = True
        self.update()
        self.stopped.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        row_h = self.height() / _VISIBLE_ROWS
        for row in (-1, 0, 1):
            sym = _REEL_STRIP[(self._offset + row) % _STRIP_LEN]
            rect = self.rect().adjusted(4, 0, -4, 0)
            rect.moveTop(int((row + 1) * row_h) + 2)
            rect.setHeight(int(row_h) - 4)
            p.setPen(
                QPen(
                    QColor(self._flash if self._flash else BORDER),
                    2 if self._flash else 1,
                )
            )
            p.setBrush(QColor(BG_CARD))
            p.drawRoundedRect(rect, 6, 6)
            p.setFont(QFont("Microsoft YaHei UI", 26 if row == 0 else 15, QFont.Bold))
            p.setPen(QColor(_SYM_COLOR[sym]))
            p.drawText(rect, Qt.AlignCenter, _SYM_CHAR[sym])
        p.end()


class SlotMachineDialog(QDialog):
    """老虎机弹窗（模态）。构造后自动开转，关闭时恰好结算一次奖励。"""

    reward_decided = Signal(object)  # 载荷 Reward；恰好 emit 一次（可为空）

    def __init__(
        self,
        base_reward: Reward,
        task_title: str = "",
        parent: QWidget | None = None,
        *,
        result: Optional[Tuple[int, int, int]] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        """base_reward 为基础奖励（仅展示与倍率基准）；老虎机产出额外 bonus。

        result/rng 仅供测试注入固定结果与固定倍率。
        """
        super().__init__(parent)
        self._base = base_reward if base_reward is not None else Reward()
        self._task_title = task_title
        self._result = result if result is not None else spin_result(rng)
        self._bonus = compute_bonus(*self._result, self._base, rng=rng)
        self._finalized = False
        self._result_shown = False
        self._stopped_count = 0
        self._stop_timers: List[QTimer] = []

        self.setWindowTitle(window_title("老虎机奖励"))
        self.setWindowFlags(
            Qt.Dialog
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTitleHint
            | Qt.MSWindowsFixedSizeDialogHint
        )
        self.setModal(True)
        self.setStyleSheet(SLOT_QSS)

        self._build_ui()
        self._start_spin()

    # ---- UI ----

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel(self._task_title or "奖励结算", self)
        title.setObjectName("SlotTitle")
        title.setAlignment(Qt.AlignCenter)
        title.setWordWrap(True)
        layout.addWidget(title)

        if not self._base.is_empty():
            sub_text = f"基础奖励 {format_reward_gain(self._base.gold, self._base.diamond)}"
        else:
            sub_text = "转动转轮，赢取额外奖励"
        sub = QLabel(sub_text, self)
        sub.setObjectName("SlotSub")
        sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(sub)

        reels_row = QHBoxLayout()
        reels_row.setSpacing(10)
        reels_row.addStretch(1)
        self._reels: List[_ReelWidget] = []
        for _ in range(3):
            reel = _ReelWidget(self)
            reel.stopped.connect(self._on_reel_stopped)
            self._reels.append(reel)
            reels_row.addWidget(reel)
        reels_row.addStretch(1)
        layout.addLayout(reels_row)

        self._result_label = QLabel("转轮转动中…", self)
        self._result_label.setObjectName("SlotResult")
        self._result_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._result_label)

        self._close_btn = QPushButton("收下", self)
        self._close_btn.setObjectName("Primary")
        self._close_btn.clicked.connect(self.accept)
        self._close_btn.setEnabled(False)
        layout.addWidget(self._close_btn, alignment=Qt.AlignCenter)

    # ---- 动画 ----

    def _start_spin(self) -> None:
        for reel in self._reels:
            reel.start_spin()
        for i in range(3):
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(partial(self._stop_reel, i))
            timer.start(_STOP_START_MS + i * _STOP_STAGGER_MS)
            self._stop_timers.append(timer)

    def _stop_reel(self, i: int) -> None:
        self._reels[i].stop_at(self._result[i])

    def _on_reel_stopped(self) -> None:
        if self._finalized:
            return
        self._stopped_count += 1
        if self._stopped_count >= 3 and not self._result_shown:
            self._on_all_stopped()

    def _on_all_stopped(self) -> None:
        if self._finalized or self._result_shown:
            return
        kind, sym = _match_of(*self._result)
        if kind == "none":
            text = "没有匹配，本次没有额外奖励"
            color = TEXT_MUTED
        else:
            for i in range(3):
                if self._result[i] == sym:
                    self._reels[i].set_flash(_SYM_COLOR[sym])
            amount = self._bonus.gold if sym == _SYM_COIN else self._bonus.diamond
            currency = "金币" if sym == _SYM_COIN else "钻石"
            match_name = "三同" if kind == "triple" else "两同"
            text = f"{match_name}{_SYM_CHAR[sym]}！额外 +{format_amount(amount)} {currency}"
            color = _SYM_COLOR[sym]
        self._result_label.setText(text)
        self._result_label.setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: 600;"
        )
        self._close_btn.setEnabled(True)
        self._close_btn.setFocus()
        self._result_shown = True

    # ---- 结算（幂等，唯一入口）----

    def _finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        self._stop_all_timers()
        self.reward_decided.emit(self._bonus)

    def _stop_all_timers(self) -> None:
        for timer in self._stop_timers:
            timer.stop()
        for reel in self._reels:
            reel.stop_timer()

    def bonus_reward(self) -> Reward:
        return self._bonus

    # ---- 关闭路径全部先结算 ----

    def accept(self) -> None:
        self._finalize()
        super().accept()

    def reject(self) -> None:
        self._finalize()
        super().reject()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self._finalize()
        super().closeEvent(event)

    def _center_on_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        anchor = parent.frameGeometry()
        self.move(
            anchor.center().x() - self.width() // 2,
            anchor.center().y() - self.height() // 2,
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._center_on_parent()
