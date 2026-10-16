"""老虎机弹窗：完成目标 / 领取宝箱时弹出，转出额外奖励。

三轴转轮，符号池：金 / 钻 / ×。三同大奖、两同小奖、无匹配空手。
结果与奖励在构造时一次性生成（纯展示 + 提前结算）：任何路径关闭
（收下 / Esc / 关窗）都会经 _finalize 恰好结算一次，奖励不丢。
"""
from __future__ import annotations

import random
from functools import partial
from typing import List, Optional, Tuple

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
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
_STOP_STAGGER_MS = 200    # 相邻两轴开始停的间隔（仅在需要时使用）
_REEL_W = 76
_REEL_H = 108
_VISIBLE_ROWS = 3         # 每轴可见 3 格，中心行为准

_SLOW_STEP_MS = 40        # 减速：每 tick 间隔增量
_SLOW_MAX_TICK_MS = 170   # 减速间隔上限
_FREEZE_MS = 420          # 最后一格前定格时长（悬念顶点）

_NEAR_MISS_DELAY_MS = 300  # 三轴停稳后静置节拍，再揭晓近失反馈
_NUDGE_H = 7               # 近失回弹位移（中心行向下再弹回）
_BLINK_MS = 400            # 近失标签闪烁周期
_BLINK_TOTAL = 5           # 闪烁次数（约 2 秒后定格）
_NEAR_MISS_COLOR = "#ff9f43"  # 与 ui_text._TOAST_CRIT 一致的橙


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


def third_has_suspense(result: Tuple[int, int, int]) -> bool:
    """第三轴是否值得减速停：前两轴同号（冲三同/近失），
    或恰有一个 ✕（第三轴决定救回两同还是差一点）。
    金钻混排看起来不沾边，快停快揭。"""
    a, b = result[0], result[1]
    if a == b:
        return True
    return (a == _SYM_MISS) != (b == _SYM_MISS)


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


def _get_sfx():
    """从主 Application 取 SfxPlayer（测试/无主程序时返回 None）。"""
    app = QApplication.instance()
    return getattr(app, "sfx", None)


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
        self._nudge = 0.0
        self._nudge_anim: Optional[QPropertyAnimation] = None
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        self._slowdown = False       # 减速停轴演出中
        self._freeze_timer: Optional[QTimer] = None

    def start_spin(self) -> None:
        self._target = None
        self._stopped = False
        self._flash = None
        self._slowdown = False
        self._timer.setInterval(_TICK_MS)
        self._timer.start()

    def stop_at(self, symbol: int, *, slowdown: bool = False) -> None:
        """从当前 offset 往后找最近一个等于 symbol 的格子作为停点。

        slowdown=True 时走减速悬念：每 tick 拉长间隔，且最后一格落定前定格。
        """
        if self._stopped or self._target is not None:
            return
        self._slowdown = slowdown
        for step in range(1, _STRIP_LEN + 1):
            if _REEL_STRIP[(self._offset + step) % _STRIP_LEN] == symbol:
                self._target = self._offset + step
                return

    def is_stopped(self) -> bool:
        return self._stopped

    def center_symbol(self) -> int:
        return _REEL_STRIP[self._offset % _STRIP_LEN]

    def _symbol_at(self, row: int) -> int:
        """符号带取样：row -1=上格 / 0=中心 / 1=下格。

        offset 递增时上格符号下一帧落到中心 → 整体自上而下流动（向下滚），
        与近失 nudge 的向下回弹、"掉金币"直觉一致。
        """
        return _REEL_STRIP[(self._offset - row) % _STRIP_LEN]

    def set_flash(self, color: Optional[str]) -> None:
        """中奖高亮：边框用给定颜色；None 熄灭。"""
        self._flash = color
        self.update()

    def nudge(self, times: int = 2) -> None:
        """近失"想再滚一格"：中心行向下轻推再弹回，重复 times 次。"""
        if self._nudge_anim is not None:
            self._nudge_anim.stop()
        anim = QPropertyAnimation(self, b"nudge", self)
        anim.setDuration(180)
        anim.setStartValue(0.0)
        anim.setKeyValueAt(0.35, 1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setLoopCount(times)
        anim.start()
        self._nudge_anim = anim

    def get_nudge(self) -> float:
        return self._nudge

    def set_nudge(self, value: float) -> None:
        self._nudge = float(value)
        self.update()

    nudgeProp = Property(float, get_nudge, set_nudge)

    def stop_nudge(self) -> None:
        if self._nudge_anim is not None:
            self._nudge_anim.stop()
            self._nudge_anim = None

    def stop_timer(self) -> None:
        self._timer.stop()
        if self._freeze_timer is not None:
            self._freeze_timer.stop()

    def _on_tick(self) -> None:
        self._offset += 1
        self.update()
        if self._target is None:
            return
        if self._offset >= self._target:
            self._halt()
            return
        if self._slowdown:
            self._apply_slowdown_step()

    def _apply_slowdown_step(self) -> None:
        """减速停轴：每 tick 拉长间隔；最后一格落定前定格制造悬念。"""
        if self._offset >= self._target - 1:
            # 定格：暂停在"差一格"画面，等一拍再落定
            self._timer.stop()
            self._freeze_timer = QTimer(self)
            self._freeze_timer.setSingleShot(True)
            self._freeze_timer.timeout.connect(self._release_freeze)
            self._freeze_timer.start(_FREEZE_MS)
            return
        interval = min(self._timer.interval() + _SLOW_STEP_MS, _SLOW_MAX_TICK_MS)
        self._timer.setInterval(interval)

    def _release_freeze(self) -> None:
        if self._stopped:
            return
        self._timer.start()

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
        p.translate(0, self._nudge * _NUDGE_H)
        for row in (-1, 0, 1):
            sym = self._symbol_at(row)
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
        self._near_miss_active = False
        self._stopped_count = 0
        self._stop_timers: List[QTimer] = []
        self._reveal_timer: Optional[QTimer] = None
        self._blink_timer: Optional[QTimer] = None
        self._blink_left = 0

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

        self._near_miss_label = QLabel("差一点！", self)
        self._near_miss_label.setAlignment(Qt.AlignCenter)
        self._near_miss_label.setStyleSheet(
            f"color: {_NEAR_MISS_COLOR}; font-size: 15px; font-weight: 700;"
        )
        self._near_miss_label.setVisible(False)
        layout.addWidget(self._near_miss_label)
        self._close_btn = QPushButton("收下", self)
        self._close_btn.setObjectName("Primary")
        self._close_btn.clicked.connect(self.accept)
        self._close_btn.setEnabled(False)
        layout.addWidget(self._close_btn, alignment=Qt.AlignCenter)

    # ---- 动画 ----

    def _start_spin(self) -> None:
        sfx = _get_sfx()
        if sfx is not None:
            sfx.play_slot_spin()
        for reel in self._reels:
            reel.start_spin()
        self._stopped_count = 0
        self._stop_timers.clear()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(partial(self._stop_reel, 0))
        timer.start(_STOP_START_MS)
        self._stop_timers.append(timer)

    def _stop_reel(self, i: int) -> None:
        # 第三轴：结果构成悬念时减速停（链式停轴下前两轴此刻必然已停，
        # 所以按结果符号判定，而不是按"还有轴在转"）
        slowdown = i == 2 and third_has_suspense(self._result)
        self._reels[i].stop_at(self._result[i], slowdown=slowdown)

    def _on_reel_stopped(self) -> None:
        if self._finalized:
            return
        sfx = _get_sfx()
        if sfx is not None:
            sfx.play_slot_reel_stop()
        self._stopped_count += 1
        if self._stopped_count >= 3:
            if sfx is not None:
                sfx.stop_slot_spin()
            if not self._result_shown:
                self._on_all_stopped()
            return
        # 链式停轴：上一轴停稳后隔一小段再让下一轴起停，保证左→右顺序
        next_index = self._stopped_count
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(partial(self._stop_reel, next_index))
        timer.start(_STOP_STAGGER_MS)
        self._stop_timers.append(timer)

    def _on_all_stopped(self) -> None:
        if self._finalized or self._result_shown:
            return
        kind, sym = _match_of(*self._result)
        sfx = _get_sfx()
        if kind == "none":
            text = "没有匹配，本次没有额外奖励"
            color = TEXT_MUTED
            near_miss = self._result.count(_SYM_MISS) < 3
            if near_miss:
                # 差一格（如 金××）：换掉一个 ✕ 就是两同。
                # 静置一拍再揭晓（悬念节拍），音画同步。
                self._near_miss_active = True
                self._reveal_timer = QTimer(self)
                self._reveal_timer.setSingleShot(True)
                self._reveal_timer.timeout.connect(self._reveal_near_miss)
                self._reveal_timer.start(_NEAR_MISS_DELAY_MS)
        else:
            for i in range(3):
                if self._result[i] == sym:
                    self._reels[i].set_flash(_SYM_COLOR[sym])
            amount = self._bonus.gold if sym == _SYM_COIN else self._bonus.diamond
            currency = "金币" if sym == _SYM_COIN else "钻石"
            match_name = "三同" if kind == "triple" else "两同"
            text = f"{match_name}{_SYM_CHAR[sym]}！额外 +{format_amount(amount)} {currency}"
            color = _SYM_COLOR[sym]
            if sfx is not None:
                sfx.play_slot_win()
        self._result_label.setText(text)
        self._result_label.setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: 600;"
        )
        self._close_btn.setEnabled(True)
        self._close_btn.setFocus()
        self._result_shown = True

    def _reveal_near_miss(self) -> None:
        """近失揭晓：两格 ✕ 弹一下 + "差一点！"标签闪烁 + 音效。"""
        if self._finalized or not self._near_miss_active:
            return
        sfx = _get_sfx()
        if sfx is not None:
            sfx.play_slot_near_miss()
        for i, s in enumerate(self._result):
            if s == _SYM_MISS:
                self._reels[i].nudge(times=2)
        self._near_miss_label.setVisible(True)
        self._blink_left = _BLINK_TOTAL
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._blink_near_miss)
        self._blink_timer.start(_BLINK_MS)

    def _blink_near_miss(self) -> None:
        self._blink_left -= 1
        if self._blink_left <= 0:
            if self._blink_timer is not None:
                self._blink_timer.stop()
            self._near_miss_label.setStyleSheet(
                f"color: {_NEAR_MISS_COLOR}; font-size: 15px; font-weight: 700;"
            )
            return
        # 奇偶交替明暗：亮 → 暗 → 亮 …，最后一次定格为亮
        if self._blink_left % 2:
            self._near_miss_label.setStyleSheet(
                f"color: {_NEAR_MISS_COLOR}; font-size: 15px; font-weight: 700;"
            )
        else:
            self._near_miss_label.setStyleSheet(
                "color: #5a5e6e; font-size: 15px; font-weight: 700;"
            )

    # ---- 结算（幂等，唯一入口）----

    def _finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        sfx = _get_sfx()
        if sfx is not None:
            sfx.stop_slot_spin()
        self._stop_all_timers()
        self.reward_decided.emit(self._bonus)

    def _stop_all_timers(self) -> None:
        for timer in self._stop_timers:
            timer.stop()
        if self._reveal_timer is not None:
            self._reveal_timer.stop()
        if self._blink_timer is not None:
            self._blink_timer.stop()
        for reel in self._reels:
            reel.stop_timer()
            reel.stop_nudge()

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
