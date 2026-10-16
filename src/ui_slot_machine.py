"""老虎机弹窗：完成目标 / 领取宝箱时弹出，转出额外奖励。

经典三轴机制：三同才中奖（金/钻/字母/音符四种面）。
「字」在最左轴额外给 1 枚字母（真机"樱桃"式部分赔付，总中奖率 ~25%）。
结果与奖励在构造时一次性生成（纯展示 + 提前结算）：任何路径关闭
（收下 / Esc / 关窗）都会经 _finalize 恰好结算一次，奖励不丢。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
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
from .models import Inventory, Reward
from .ui_styles import BG_CARD, BORDER, DARK_BASE_QSS, TEXT_MUTED
from .ui_text import format_amount, format_reward_gain

SLOT_QSS = DARK_BASE_QSS + f"""
QLabel#SlotTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#SlotSub {{ color: {TEXT_MUTED}; font-size: 12px; }}
QLabel#SlotResult {{ font-size: 14px; font-weight: 600; min-height: 40px; }}
"""

_SYM_COIN = 0
_SYM_DIAMOND = 1
_SYM_LETTER = 2   # 字：给字母
_SYM_NOTE = 3     # ♪：给试听券
_SYM_MISS = 4

# 每轴独立均匀取样；计数：金5 钻2 字2 ♪2 ×1（12 格）。
# 三同金 7.2% / 三同钻·字·♪ 各 0.46%；2 个 ✕ 时"字/♪/金"停在左轴再做近失演出。
_REEL_STRIP = (0, 0, 1, 2, 0, 3, 4, 0, 1, 2, 0, 3)
_STRIP_LEN = len(_REEL_STRIP)

_SYM_CHAR = {
    _SYM_COIN: "金",
    _SYM_DIAMOND: "钻",
    _SYM_LETTER: "字",
    _SYM_NOTE: "♪",
    _SYM_MISS: "×",
}
_SYM_COLOR = {
    _SYM_COIN: "#f5c842",
    _SYM_DIAMOND: "#5ec8f2",
    _SYM_LETTER: "#7dcc96",   # 与宝箱稀有度"罕见"同系的绿
    _SYM_NOTE: "#c9a0ff",     # 紫
    _SYM_MISS: "#5a5e6e",
}

# 三同倍率（金/钻作用于 base 对应币种）；字/♪ 三同给固定数量
_TRIPLE_PAYOUTS = {
    _SYM_COIN: (1.5, 4.0),
    _SYM_DIAMOND: (0.3, 1.0),
}
_TRIPLE_LETTER_COUNT = 3   # 三同字 → 3 枚字母（各自独立抽稀有度）
_TRIPLE_NOTE_COUNT = 3     # 三同♪ → 3 张试听券
_LEFT_LETTER_FLOOR = 1     # 最左轴"字"的樱桃赔付：1 枚字母
_FALLBACK_BASE = 1.0       # base 该币种为 0 时的兜底基准（宝箱场景也有实奖）

# 字母开出时的稀有度权重：普通/罕见/稀有/史诗/传奇
_LETTER_RARITY_WEIGHTS = (55, 25, 13, 5, 2)
_LETTER_RARITY_NAMES = ("普通", "罕见", "稀有", "史诗", "传奇")
_LETTER_RARITY_COLORS = ("#c8c0b4", "#7dcc96", "#7aa2ff", "#c9a0ff", "#ffd56a")

_TICK_MS = 16             # 60fps 连续滚动
_SPIN_CELLS_PER_SEC = 18.0  # 匀速滚动速度（格/秒）
_STOP_START_MS = 600      # 第一轴开始停的时间
_STOP_STAGGER_MS = 200    # 相邻两轴开始停的间隔（链式停轴，上一轴停稳后延迟）
_SLOW_LEAD_MS = 820       # 第三轴减速演出前多转一会儿（阻尼滑行区更从容）
_REEL_W = 76
_REEL_H = 108
_VISIBLE_ROWS = 3         # 每轴可见 3 格，中心行为准

# 减速停轴（阻尼式）：剩余距离从该值起开始减速，近端最慢步长
_SLOW_ZONE_CELLS = 7.0    # 剩余几格开始进入减速区
_SLOW_MIN_STEP = 0.06     # 减速近端每帧步长（≈260ms/格）
_SLOW_EASE_POW = 1.6      # 减速曲线指数（越大末尾越拖）
_NORMAL_STEP = _SPIN_CELLS_PER_SEC * _TICK_MS / 1000.0  # 匀速每帧步长

_NEAR_MISS_DELAY_MS = 300  # 三轴停稳后静置节拍，再揭晓近失反馈
_NUDGE_H = 7               # 近失回弹位移（中心行向下再弹回）
_BLINK_MS = 400            # 近失标签闪烁周期
_BLINK_TOTAL = 5           # 闪烁次数（约 2 秒后定格）
_NEAR_MISS_COLOR = "#ff9f43"  # 与 ui_text._TOAST_CRIT 一致的橙


def spin_result(rng: Optional[random.Random] = None) -> Tuple[int, int, int]:
    """三轴各自独立从 _REEL_STRIP 均匀取样。"""
    r = rng if rng is not None else random.Random()
    return (r.choice(_REEL_STRIP), r.choice(_REEL_STRIP), r.choice(_REEL_STRIP))


def _match_of(s0: int, s1: int, s2: int) -> Optional[int]:
    """经典三轴：只有三同（非 ×）才中奖，返回中奖符号；否则 None。"""
    if s0 == s1 == s2 and s0 != _SYM_MISS:
        return s0
    return None


def third_has_suspense(result: Tuple[int, int, int]) -> bool:
    """第三轴是否值得减速停：前两轴同号（且非 ✕）——第三轴真有机会凑三同。

    三同制下没有两同小奖，所以 金×_ / 金钻_ 都不构成悬念（必不中），快停快揭。
    """
    a, b = result[0], result[1]
    return a == b and a != _SYM_MISS


def hot_symbol_for(last_symbol: int, prev_symbols: Tuple[int, ...]) -> Optional[int]:
    """减速停轴时最后停的若是 ×，"热符号"应是前两轴里出现的非 × 符号。

    定格瞬间中心停在热符号上、上格悬着 ×，制造"差一点掉下来"的画面。
    没有热符号（如 ×× 开局）时返回 None。
    """
    if last_symbol != _SYM_MISS:
        return None
    for sym in (_SYM_COIN, _SYM_DIAMOND, _SYM_LETTER, _SYM_NOTE):
        if sym in prev_symbols:
            return sym
    return None


@dataclass
class SlotPrizes:
    """老虎机一次转动的全部产出：钱 + 字母 + 试听券。"""

    reward: Reward = field(default_factory=Reward)
    # [(字母, 稀有度 0-4), ...]
    letters: List[Tuple[str, int]] = field(default_factory=list)
    # {歌名: 张数}
    song_tokens: dict = field(default_factory=dict)

    def is_empty(self) -> bool:
        return self.reward.is_empty() and not self.letters and not self.song_tokens

    def apply(self, inventory: Inventory) -> None:
        """全部入账（空奖励天然无害）。"""
        if not self.reward.is_empty():
            inventory.add(self.reward)
        for letter, rarity in self.letters:
            inventory.add_letter(letter, rarity)
        for name, count in self.song_tokens.items():
            inventory.add_song_token(name, count)


def draw_letter_prize(rng: Optional[random.Random] = None) -> Tuple[str, int]:
    """开出一枚随机字母 + 加权稀有度。"""
    r = rng if rng is not None else random.Random()
    letter = chr(ord("A") + r.randrange(26))
    rarity = r.choices(range(5), weights=_LETTER_RARITY_WEIGHTS)[0]
    return letter, int(rarity)


def song_names() -> List[str]:
    """可开出试听券的歌曲名列表（aim/ 文件夹）。"""
    from .sfx import bgm_song_list

    return [str(s["name"]) for s in bgm_song_list()]


def compute_prizes(
    s0: int,
    s1: int,
    s2: int,
    base_reward: Optional[Reward] = None,
    rng: Optional[random.Random] = None,
) -> SlotPrizes:
    """按结果结算全部产出。

    - 三同金/钻：金/钻倍率（作用于 base 对应币种，空 base 兜底 1.0）
    - 三同字：3 枚字母；三同♪：3 张随机歌曲试听券
    - 最左轴是「字」（非三同）：1 枚字母（樱桃式部分赔付）
    """
    r = rng if rng is not None else random.Random()
    base = base_reward if base_reward is not None else Reward()
    prizes = SlotPrizes()
    triple = _match_of(s0, s1, s2)
    if triple == _SYM_COIN or triple == _SYM_DIAMOND:
        lo, hi = _TRIPLE_PAYOUTS[triple]
        if triple == _SYM_COIN:
            eff = base.gold if base.gold > 0 else _FALLBACK_BASE
            prizes.reward = Reward(gold=round(eff * r.uniform(lo, hi), 2))
        else:
            eff = base.diamond if base.diamond > 0 else _FALLBACK_BASE
            prizes.reward = Reward(diamond=round(eff * r.uniform(lo, hi), 2))
        return prizes
    if triple == _SYM_LETTER:
        prizes.letters = [draw_letter_prize(r) for _ in range(_TRIPLE_LETTER_COUNT)]
        return prizes
    if triple == _SYM_NOTE:
        names = song_names()
        if names:
            for _ in range(_TRIPLE_NOTE_COUNT):
                name = names[r.randrange(len(names))]
                prizes.song_tokens[name] = prizes.song_tokens.get(name, 0) + 1
        return prizes
    # 樱桃式：最左轴是「字」就小赔一枚字母
    if s0 == _SYM_LETTER:
        prizes.letters = [draw_letter_prize(r) for _ in range(_LEFT_LETTER_FLOOR)]
    return prizes


def preferred_miss_stop(
    strip: Tuple[int, ...],
    start: int,
    hot: Optional[int],
) -> Optional[int]:
    """在 strip 中找一颗 ×，使其**正上方**显示 hot 符号（自 start 向后最近的一颗）。

    屏幕上格显示 strip[idx+1]（下滚方向），所以条件是 strip[idx+1] == hot：
    停稳后中心是 ×、上格是热符号，玩家刚看到热符号经过、又被 × 顶掉。
    返回相对 start 的步数；找不到返回 None。
    """
    if hot is None:
        return None
    n = len(strip)
    for step in range(1, n + 1):
        idx = (start + step) % n
        if strip[idx] == _SYM_MISS and strip[(idx + 1) % n] == hot:
            return step
    return None


def _get_sfx():
    """从主 Application 取 SfxPlayer（测试/无主程序时返回 None）。"""
    app = QApplication.instance()
    return getattr(app, "sfx", None)


class _ReelWidget(QWidget):
    """单轴转轮：60fps 浮点连续滚动，阻尼式减速停稳在目标符号上。"""

    stopped = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(_REEL_W, _REEL_H)
        self._offset = float(random.randrange(_STRIP_LEN))
        self._target: Optional[float] = None
        self._stopped = True
        self._flash: Optional[str] = None
        self._nudge = 0.0
        self._nudge_anim: Optional[QPropertyAnimation] = None
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        self._slowdown = False       # 减速停轴演出中

    def start_spin(self) -> None:
        self._target = None
        self._stopped = False
        self._flash = None
        self._slowdown = False
        self._timer.setInterval(_TICK_MS)
        self._timer.start()

    def stop_at(
        self,
        symbol: int,
        *,
        slowdown: bool = False,
        hot: Optional[int] = None,
    ) -> None:
        """从当前 offset 往后找最近的 symbol 格作为停点（整格对齐）。

        slowdown=True 时阻尼减速滑行到停点。
        hot 给定时（如最后一轴停 ×），优先选"上格是 hot"的 ×，
        使定格画面是"热符号悬在线上一格、× 掉到线上"。
        """
        if self._stopped or self._target is not None:
            return
        self._slowdown = slowdown
        base = math.ceil(self._offset)
        if symbol == _SYM_MISS:
            step = preferred_miss_stop(_REEL_STRIP, base, hot)
            if step is not None:
                self._target = base + step
                return
        for step in range(1, _STRIP_LEN + 1):
            if _REEL_STRIP[(base + step) % _STRIP_LEN] == symbol:
                self._target = base + step
                return

    def is_stopped(self) -> bool:
        return self._stopped

    def center_symbol(self) -> int:
        return _REEL_STRIP[int(self._offset) % _STRIP_LEN]

    def _symbol_at(self, row: int) -> int:
        """符号带取样：row -1=上格 / 0=中心 / 1=下格。

        offset 递增时上格符号下一帧落到中心 → 整体自上而下流动（向下滚），
        与近失 nudge 的向下回弹、"掉金币"直觉一致。
        """
        return _REEL_STRIP[(int(self._offset) - row) % _STRIP_LEN]

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

    def get_scroll_frac(self) -> float:
        """当前格内进度 0..1（用于亚像素平移）。"""
        return self._offset - int(self._offset)

    def stop_nudge(self) -> None:
        if self._nudge_anim is not None:
            self._nudge_anim.stop()
            self._nudge_anim = None

    def stop_timer(self) -> None:
        self._timer.stop()

    def _on_tick(self) -> None:
        if self._target is None:
            self._offset += _NORMAL_STEP
            self.update()
            return
        remaining = self._target - self._offset
        step = self._step_for_remaining(remaining) if self._slowdown else _NORMAL_STEP
        # 取 min 精确落格：最后一帧不吸附、不跳像素
        step = min(step, remaining)
        self._offset += step
        if self._offset >= self._target - 1e-9:
            self._halt()
            return
        self.update()

    @staticmethod
    def _step_for_remaining(remaining: float) -> float:
        """阻尼减速：剩余越少步长越小（幂曲线），近端收敛到 _SLOW_MIN_STEP。"""
        if remaining >= _SLOW_ZONE_CELLS:
            return _NORMAL_STEP
        t = max(0.0, remaining / _SLOW_ZONE_CELLS)
        eased = t ** _SLOW_EASE_POW
        return max(_SLOW_MIN_STEP, _NORMAL_STEP * eased)

    def _halt(self) -> None:
        self._offset = float(self._target)
        self._timer.stop()
        self._stopped = True
        self.update()
        self.stopped.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        row_h = self.height() / _VISIBLE_ROWS
        # 亚像素平移：格内进度 × 行高，让滚动连续而非整格跳。
        # nudge 为"想再滚一格"的向下推（正方向与滚向一致）。
        frac = self._offset - int(self._offset)
        scroll_px = frac * row_h if not self._stopped else 0.0
        p.translate(0, scroll_px + self._nudge * _NUDGE_H)
        # 多画一行，保证平移后上下边缘不露空
        for row in (-2, -1, 0, 1):
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

    prize_decided = Signal(object)  # 载荷 SlotPrizes；恰好 emit 一次（可为空）

    def __init__(
        self,
        base_reward: Reward,
        task_title: str = "",
        parent: QWidget | None = None,
        *,
        result: Optional[Tuple[int, int, int]] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        """base_reward 为基础奖励（金/钻倍率的基准）；老虎机产出额外奖励。

        result/rng 仅供测试注入固定结果与固定随机数。
        """
        super().__init__(parent)
        self._base = base_reward if base_reward is not None else Reward()
        self._task_title = task_title
        self._result = result if result is not None else spin_result(rng)
        self._prizes = compute_prizes(*self._result, self._base, rng=rng)
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
        # 减速停时若停在 ×，优选"上格是热符号"的 ×，定格成"差一点掉下来"画面
        hot = None
        if slowdown:
            hot = hot_symbol_for(self._result[i], tuple(self._result[:i]))
        self._reels[i].stop_at(self._result[i], slowdown=slowdown, hot=hot)

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
        # 链式停轴：上一轴停稳后隔一小段再让下一轴起停，保证左→右顺序。
        # 第三轴走减速演出时留更长的自由转时间，阻尼滑行区更从容。
        next_index = self._stopped_count
        delay = _STOP_STAGGER_MS
        if next_index == 2 and third_has_suspense(self._result):
            delay = _SLOW_LEAD_MS
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(partial(self._stop_reel, next_index))
        timer.start(delay)
        self._stop_timers.append(timer)

    def _on_all_stopped(self) -> None:
        if self._finalized or self._result_shown:
            return
        triple = _match_of(*self._result)
        prizes = self._prizes
        sfx = _get_sfx()
        if triple is not None:
            for i in range(3):
                if self._result[i] == triple:
                    self._reels[i].set_flash(_SYM_COLOR[triple])
        if not prizes.is_empty():
            text = self._prize_text(triple, prizes)
            color = _SYM_COLOR[triple] if triple is not None else _SYM_COLOR[_SYM_LETTER]
            if sfx is not None:
                sfx.play_slot_win()
        else:
            text = "没有匹配，本次没有额外奖励"
            color = TEXT_MUTED
            if self._has_near_miss():
                # 差一格：静置一拍再揭晓（悬念节拍），音画同步
                self._near_miss_active = True
                self._reveal_timer = QTimer(self)
                self._reveal_timer.setSingleShot(True)
                self._reveal_timer.timeout.connect(self._reveal_near_miss)
                self._reveal_timer.start(_NEAR_MISS_DELAY_MS)
        self._result_label.setText(text)
        self._result_label.setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: 600;"
        )
        self._close_btn.setEnabled(True)
        self._close_btn.setFocus()
        self._result_shown = True

    def _has_near_miss(self) -> bool:
        """无奖但差一格：有 ✕ 且非全 ✕（换掉 ✕ 就能凑出奖）。"""
        return 0 < self._result.count(_SYM_MISS) < 3

    def _prize_text(self, triple: Optional[int], prizes: SlotPrizes) -> str:
        """多行结果文案：线奖 / 字母 / 试听券。"""
        lines: List[str] = []
        if triple in (_SYM_COIN, _SYM_DIAMOND) and not prizes.reward.is_empty():
            amount = (
                prizes.reward.gold if triple == _SYM_COIN else prizes.reward.diamond
            )
            currency = "金币" if triple == _SYM_COIN else "钻石"
            lines.append(f"三同{_SYM_CHAR[triple]}！额外 +{format_amount(amount)} {currency}")
        elif triple == _SYM_LETTER:
            lines.append(f"三同字！开出 {len(prizes.letters)} 枚字母")
        elif triple == _SYM_NOTE:
            total = sum(prizes.song_tokens.values())
            lines.append(f"三同♪！获得 {total} 张试听券")
        # 字母列表（含樱桃赔付）
        if prizes.letters:
            parts = [
                f"{letter}（{_LETTER_RARITY_NAMES[r]}）" for letter, r in prizes.letters
            ]
            prefix = "字母 " if triple != _SYM_LETTER else ""
            lines.append(prefix + "、".join(parts))
        # 试听券列表
        if prizes.song_tokens:
            parts = [f"{name}×{n}" for name, n in prizes.song_tokens.items()]
            lines.append("试听券：" + "、".join(parts))
        return "\n".join(lines)

    def _reveal_near_miss(self) -> None:
        """近失揭晓：未凑成的那格 ✕（及其余非奖格）弹一下 + "差一点！"标签闪烁 + 音效。"""
        if self._finalized or not self._near_miss_active:
            return
        sfx = _get_sfx()
        if sfx is not None:
            sfx.play_slot_near_miss()
        # 未中奖时，弹出"最碍事的那些格"：优先弹 ✕，其次弹散格
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
        self.prize_decided.emit(self._prizes)

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

    def prizes(self) -> SlotPrizes:
        return self._prizes

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
