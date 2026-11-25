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
from typing import List, Optional, Tuple

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..branding import window_title
from ..models import Inventory, Reward
from .qt import center_dialog_on_parent
from .styles import DARK_BASE_QSS, TEXT_MUTED
from ..text import format_amount, format_reward_gain

SLOT_QSS = DARK_BASE_QSS + f"""
QLabel#SlotTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#SlotSub {{ color: {TEXT_MUTED}; font-size: 12px; }}
QLabel#SlotResult {{ font-size: 14px; font-weight: 600; min-height: 40px; }}
QPushButton#StopBtn {{
    background-color: #2b2f3d;
    border-color: #4a5068;
    color: #ffd56a;
}}
QPushButton#StopBtn:hover {{ background-color: #3a3f52; border-color: #6a7390; }}
QPushButton#StopBtn:disabled {{ color: #5a5e6e; border-color: #2a2d38; }}
"""

_SYM_COIN = 0
_SYM_DIAMOND = 1
_SYM_LETTER = 2   # 字：给字母
_SYM_NOTE = 3     # ♪：给试听券

# ── 物理带（22格，3轴共用，玩家看到的滚动画面）──
# 近失邻接：每个金/字两侧都是钻或♪（高频符号），擦线而过效果最大化。
# 金5(22.7%) 钻7(31.8%) 字3(13.6%) ♪7(31.8%)
_PHYSICAL_STRIP = (
    0, 1, 3, 2, 1, 0, 3, 1, 3, 0,
    1, 3, 0, 1, 2, 3, 1, 0, 3, 1,
    2, 3,
)
_PHYSICAL_LEN = len(_PHYSICAL_STRIP)

# ── 虚拟带（72格×3轴，RNG取样源，决定真实停轴概率）──
# 轴1: 金22 钻24 字6 ♪20  →  P(金)30.6% P(钻)33.3% P(字)8.3% P(♪)27.8%
# 轴2: 金16 钻22 字4 ♪30  →  P(金)22.2% P(钻)30.6% P(字)5.6% P(♪)41.7%
# 轴3: 金6  钻18 字2 ♪46  →  P(金)8.3%  P(钻)25.0% P(字)2.8% P(♪)63.9%
# 三同概率：金0.57% 钻2.55% 字0.013% ♪7.40%  合计~10.5%
# ♪♪♪最频繁→LDW持续小胜感；金/字极稀有→中了更兴奋
# 第三轴金仅8.3%→"差一格就三同金"的近失感更强（虚拟转轮欺骗性）
_VIRTUAL_STRIPS = (
    (0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
     1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,
     2,2,2,2,2,2,
     3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3),
    (0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
     1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,
     2,2,2,2,
     3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3),
    (0,0,0,0,0,0,
     1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,
     2,2,
     3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3),
)
_VIRTUAL_LENS = tuple(len(s) for s in _VIRTUAL_STRIPS)

_SYM_CHAR = {
    _SYM_COIN: "金",
    _SYM_DIAMOND: "钻",
    _SYM_LETTER: "字",
    _SYM_NOTE: "♪",
}
_SYM_COLOR = {
    _SYM_COIN: "#f5c842",
    _SYM_DIAMOND: "#5ec8f2",
    _SYM_LETTER: "#7dcc96",   # 与宝箱稀有度"罕见"同系的绿
    _SYM_NOTE: "#c9a0ff",     # 紫
}
_SYM_COLOR_Q = {k: QColor(v) for k, v in _SYM_COLOR.items()}

# 白鼓上的深色版符号色：亮色在白底对比度不足（<2.2:1），必须换深色。
_SYM_COLOR_ON_LIGHT = {
    _SYM_COIN: "#8a6400",
    _SYM_DIAMOND: "#0b6e96",
    _SYM_LETTER: "#1f7a45",
    _SYM_NOTE: "#6b3fc0",
}
_SYM_COLOR_LIGHT_Q = {k: QColor(v) for k, v in _SYM_COLOR_ON_LIGHT.items()}

# 转轮上直接显示的 emoji（键盘可直接输入）。
_SYM_EMOJI = {
    _SYM_COIN: "\U0001F4B0",            # 💰 金币（Unicode 6.0，Win7+ 均有）
    _SYM_DIAMOND: "\U0001F48E",         # 💎 钻石
    _SYM_LETTER: "\U0001F170\uFE0F",    # 🅰️ 字母 A
    _SYM_NOTE: "\U0001F3B5",            # 🎵 音符
}
_EMOJI_FAMILIES = ("Segoe UI Emoji", "Noto Color Emoji", "Apple Color Emoji")
_emoji_ok: dict[int, bool] = {}


def _emoji_font(px: int) -> QFont:
    f = QFont()
    f.setFamilies(list(_EMOJI_FAMILIES))
    f.setPixelSize(px)
    return f


def _emoji_usable(sym: int) -> bool:
    """探测该 emoji 能否真正画出彩色像素；缺字/单色剪影则回退中文字形。"""
    cached = _emoji_ok.get(sym)
    if cached is not None:
        return cached
    img = QImage(64, 64, QImage.Format_RGB32)
    img.fill(QColor("#101018"))
    probe = QPainter(img)
    probe.setFont(_emoji_font(44))
    probe.drawText(img.rect(), Qt.AlignCenter, _SYM_EMOJI[sym])
    probe.end()
    # 只数饱和像素：豆腐块与单色剪影的色度≈0，据此判为不可用。
    colorful = 0
    for y in range(64):
        for x in range(64):
            c = img.pixelColor(x, y)
            if max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) > 50:
                colorful += 1
    ok = colorful > 40
    _emoji_ok[sym] = ok
    return ok

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
_AUTO_STOP_FALLBACK_MS = 30000  # 不按「停」时的兜底：30 秒自动停轴，防止模态卡死
_REEL_W = 76
_REEL_H = 108
_VISIBLE_ROWS = 3         # 每轴可见 3 格，中心行为准
_DRUM_SLOTS = 10          # 圆鼓一圈格数（同 Vue 每轴 10 格 → 36°/格）
# 以下四项直接取自参考实现 SlotMachine.vue / Gift.vue 的 .gift-style：
# 象牙白鼓面 + 深蓝字 + 仅横向 1px 分格线（无左右描边、无圆角）。
_DRUM_BG = "#f5f2e2"       # background-color: #f5f2e2
_DRUM_TEXT = "#09384e"     # color: #09384e
_DRUM_LINE = "#333333"     # border-top / border-bottom: 1px solid #333
_DRUM_HOUSING = "#0d0e14"  # 嵌框底色（比卡片更暗）→ 白鼓像从机身里挖出来
# 窗口内阴影：参考实现用 ::before/::after 的 inset box-shadow 叠出上下暗角，
# 纵深全靠这层渐隐黑，而不是给每格刷灰。
_DRUM_SHADOW_DEPTH = 0.45  # 暗角高度占格高比例（参考 15px/200px ≈ 0.075 起渐隐）
_DRUM_SHADOW_ALPHA = 110   # 暗角最深处的黑 alpha（0..255，参考 0.2+0.3 叠加）

# 停轴时长与 Vue 版一致：三轴同帧起刹，落定时长依次 4s / 5s / 6s。
# 距离由时长反推（见 _stop_min_dist），速度曲线见 _step_for_remaining。
_REEL_STOP_MS = (4000.0, 5000.0, 6000.0)
# 悬念轴（前两轴同号）：在第三轴时长基础上再拉长，营造"差一点"的拖拽感。
_SUSPENSE_STOP_SCALE = {
    _SYM_NOTE:    1.25,
    _SYM_DIAMOND: 1.25,
    _SYM_COIN:    1.15,
    _SYM_LETTER:  1.15,
}
_NORMAL_STEP = _SPIN_CELLS_PER_SEC * _TICK_MS / 1000.0  # 匀速每帧步长
# 减速段过格声的速度阈值（格/帧）：低于此值相邻两声才能分开听。
# 0.11 格/帧 ≈ 6.9 格/秒；高于它时 18 格/秒的敲击会糊成嗡鸣。
_TICK_SOUND_STEP = 0.11

_NEAR_MISS_DELAY_MS = 300
_NUDGE_CELLS = 0.72      # 过冲格数：0.72 格≈28px，上格压到赔付线边缘但不完全进线
_NUDGE_MS = 520          # 回弹总时长（含峰值停驻）
_NUDGE_HOLD = 0.24       # 峰值停驻比例≈125ms，让"快三个一起"能被看清
_BLINK_MS = 400
_BLINK_TOTAL = 5
_NEAR_MISS_COLOR = "#ff9f43"
_FLASH_RISE_MS = 150
_FLASH_GLOW = 0.22

_NEAR_MISS_WEIGHTS = {
    _SYM_NOTE:    3,
    _SYM_DIAMOND: 3,
    _SYM_COIN:    2,
    _SYM_LETTER:  1,
}
_NEAR_MISS_TEXTS = {
    _SYM_NOTE:    "就差一点！",
    _SYM_DIAMOND: "就差一点！",
    _SYM_COIN:    "差一点！",
    _SYM_LETTER:  "差一点",
}
_NEAR_MISS_COLORS = {
    _SYM_NOTE:    "#ff6b6b",
    _SYM_DIAMOND: "#ff6b6b",
    _SYM_COIN:    "#ff9f43",
    _SYM_LETTER:  "#5a5e6e",
}
_NEAR_MISS_SIZES = {
    _SYM_NOTE:    18,
    _SYM_DIAMOND: 18,
    _SYM_COIN:    15,
    _SYM_LETTER:  12,
}
_NEAR_MISS_BLINKS = {
    _SYM_NOTE:    7,
    _SYM_DIAMOND: 7,
    _SYM_COIN:    5,
    _SYM_LETTER:  3,
}

# 悬念轴（前两轴同号）：沿用同一套曲线，只是时长按符号再放大。
_SUSPENSE_HEARTBEAT = {
    _SYM_NOTE:    True,
    _SYM_DIAMOND: True,
    _SYM_COIN:    False,
    _SYM_LETTER:  False,
}


def _stop_duration_ms(reel_index: int, suspense_sym: Optional[int]) -> float:
    """该轴落定总时长（毫秒）：三轴 4s/5s/6s，悬念轴再按符号放大。"""
    base = _REEL_STOP_MS[min(reel_index, len(_REEL_STOP_MS) - 1)]
    if suspense_sym is None:
        return base
    return base * _SUSPENSE_STOP_SCALE.get(suspense_sym, 1.0)


def _stop_min_dist(duration_ms: float) -> float:
    """从匀速刹停所需的最小距离（格）：恒减速度下 D = v0 * T / 2。"""
    return _SPIN_CELLS_PER_SEC * (duration_ms / 1000.0) / 2.0


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    """按 t∈[0,1] 在 a→b 之间线性插值（渐亮边框 / 泛光底色）。"""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


def spin_result(rng: Optional[random.Random] = None) -> Tuple[int, int, int]:
    """三轴各自从对应的虚拟带均匀取样（虚拟转轮映射）。"""
    r = rng if rng is not None else random.Random()
    return (
        r.choice(_VIRTUAL_STRIPS[0]),
        r.choice(_VIRTUAL_STRIPS[1]),
        r.choice(_VIRTUAL_STRIPS[2]),
    )


def _match_of(s0: int, s1: int, s2: int) -> Optional[int]:
    """经典三轴：只有三同才中奖，返回中奖符号；否则 None。"""
    if s0 == s1 == s2:
        return s0
    return None


def third_has_suspense(result: Tuple[int, int, int]) -> Optional[int]:
    """第三轴是否值得减速停：前两轴同号时返回该符号，否则 None。"""
    a, b = result[0], result[1]
    if a == b:
        return a
    return None


def near_miss_symbol(result: Tuple[int, int, int]) -> Optional[int]:
    """近失：前两轴同号、第三轴不同（非三同），返回那对符号；否则 None。

    只认前两轴：第三轴减速慢停时观众已经为"差一格"绷紧，此时才揭晓近失
    才成立。后两轴/首尾同号没有这个悬念过程，不做近失演出
    （任意两同约 59%，只取前两轴约 20%，接近近失研究的最优区间）。
    """
    s0, s1, s2 = result
    if s0 == s1 and s2 != s0:
        return s0
    return None


def triple_probabilities() -> dict[int, float]:
    """各三同事件的真实概率（从虚拟带计算）。"""
    probs: dict[int, float] = {}
    for sym in range(4):
        p = 1.0
        for reel_idx in range(3):
            strip = _VIRTUAL_STRIPS[reel_idx]
            p *= sum(1 for s in strip if s == sym) / len(strip)
        probs[sym] = p
    return probs


def physical_strip_probabilities() -> dict[int, float]:
    """物理带各符号的显示概率（玩家直觉依据）。"""
    total = _PHYSICAL_LEN
    return {
        sym: sum(1 for s in _PHYSICAL_STRIP if s == sym) / total
        for sym in range(4)
    }


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
    from ..sfx import bgm_song_list

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


def _get_sfx():
    """从主 Application 取 SfxPlayer（测试/无主程序时返回 None）。"""
    app = QApplication.instance()
    return getattr(app, "sfx", None)


class _ReelWidget(QWidget):
    """单轴转轮：60fps 浮点连续滚动，阻尼式减速停稳在目标符号上。"""

    stopped = Signal()
    # 减速段过格：每秒 ≤7 格时才 emit，此时相邻两声能分开听（见 _TICK_SOUND_STEP）。
    cell_crossed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(_REEL_W, _REEL_H)
        self._offset = float(random.randrange(_PHYSICAL_LEN))
        self._target: Optional[float] = None
        self._stopped = True
        self._flash: Optional[str] = None
        self._flash_strength = 0.0   # 0..1 渐亮系数（QPropertyAnimation 驱动）
        self._flash_anim: Optional[QPropertyAnimation] = None
        self._nudge = 0.0
        self._nudge_anim: Optional[QPropertyAnimation] = None
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        self._suspense_sym: Optional[int] = None
        self._stop_ms: float = _REEL_STOP_MS[0]
        self._stop_dist: float = 0.0
        self._stop_pow: float = 0.5

    def start_spin(self) -> None:
        self._target = None
        self._stopped = False
        # 上一轮若停在过冲位（近失回弹），新的一轮必须从整格起步，
        # 否则残留的 _nudge 会和新的相位叠加成永久错位。
        self._nudge = 0.0
        self._offset = float(int(self._offset))
        self._flash = None
        self._flash_strength = 0.0
        self._suspense_sym = None
        self._stop_ms = _REEL_STOP_MS[0]
        self._stop_dist = 0.0
        self._stop_pow = 0.5
        self._timer.setInterval(_TICK_MS)
        self._timer.start()

    def stop_at(
        self,
        symbol: int,
        *,
        suspense_sym: Optional[int] = None,
        graze: Optional[int] = None,
        duration_ms: Optional[float] = None,
    ) -> None:
        """从当前 offset 往后找 symbol 格作为停点（整格对齐），并预留减速距离。

        duration_ms 决定整段落定耗时（默认按轴序号 4s/5s/6s，悬念轴再放大），
        停点距离由时长反推（恒减速度 D = v0*T/2），因此时长精确、速度连续。
        查找优先级：带 graze 且够远 → 带 graze（保近失画面）→ 够远 → 最近一格。
        graze 给定时优先选一格使 graze 显示在赔付线正上方
        （屏幕上格 = strip[offset+1]）——真机的"擦线而过"近失画面。
        """
        if self._stopped or self._target is not None:
            return
        self._suspense_sym = suspense_sym
        self._stop_ms = (
            duration_ms if duration_ms is not None
            else _stop_duration_ms(0, suspense_sym)
        )
        base = math.ceil(self._offset)
        min_dist = int(math.ceil(_stop_min_dist(self._stop_ms)))
        graze_want = graze if (graze is not None and graze != symbol) else None

        def find(want_graze: Optional[int], min_step: int) -> Optional[float]:
            for step in range(max(1, min_step), min_step + _PHYSICAL_LEN + 1):
                idx = (base + step) % _PHYSICAL_LEN
                if _PHYSICAL_STRIP[idx] != symbol:
                    continue
                if (
                    want_graze is not None
                    and _PHYSICAL_STRIP[(idx + 1) % _PHYSICAL_LEN] != want_graze
                ):
                    continue
                return float(base + step)
            return None

        target = find(graze_want, min_dist)
        if target is None and graze_want is not None:
            target = find(graze_want, 1)
        if target is None:
            target = find(None, min_dist)
        if target is None:
            target = find(None, 1)
        if target is None:
            return
        # 近失：落点再往前推 0.72 格，让鼓面停在"快三同"的过冲位上——赔付线
        # 上方那格（缺失符号）压到几乎进线但没进。随后由 nudge 把它弹回整格，
        # "多转一格再被弹回"才是"差一格"的物理本体，而不是停稳后再平移一下。
        if (
            graze_want is not None
            and _PHYSICAL_STRIP[(int(target) + 1) % _PHYSICAL_LEN] == graze_want
        ):
            target += _NUDGE_CELLS
        self._target = target
        # 由「实际距离 + 目标时长」反解速度曲线指数 p：
        # v(r) = v0 * (r/D)^p，落定耗时 T = D / (v0 * (1-p))，令其等于 _stop_ms。
        self._stop_dist = max(1e-6, target - self._offset)
        ratio = self._stop_dist / (_SPIN_CELLS_PER_SEC * self._stop_ms / 1000.0)
        self._stop_pow = max(0.05, min(0.75, 1.0 - ratio))

    def is_stopped(self) -> bool:
        return self._stopped

    def is_stopping(self) -> bool:
        """已设停点、正在滑行但尚未停稳。"""
        return (not self._stopped) and self._target is not None

    def center_symbol(self) -> int:
        return _PHYSICAL_STRIP[int(self._offset) % _PHYSICAL_LEN]

    def _symbol_at(self, row: int) -> int:
        """符号带取样：row -1=上格 / 0=中心 / 1=下格。

        offset 递增时上格符号下一帧落到中心 → 整体自上而下流动（向下滚），
        与近失 nudge 的向下回弹、"掉金币"直觉一致。
        """
        return _PHYSICAL_STRIP[(int(self._offset) - row) % _PHYSICAL_LEN]

    def set_flash(self, color: Optional[str]) -> None:
        """中奖高亮：中线格边框渐亮为给定颜色；None 熄灭。"""
        self._flash = color
        if self._flash_anim is not None:
            self._flash_anim.stop()
            self._flash_anim = None
        if color is None:
            self._flash_strength = 0.0
            self.update()
            return
        anim = QPropertyAnimation(self, b"flashStrengthProp", self)
        anim.setDuration(_FLASH_RISE_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._flash_anim = anim

    def get_flash_strength(self) -> float:
        return self._flash_strength

    def set_flash_strength(self, value: float) -> None:
        self._flash_strength = float(value)
        self.update()

    flashStrengthProp = Property(float, get_flash_strength, set_flash_strength)

    def stop_flash(self) -> None:
        if self._flash_anim is not None:
            self._flash_anim.stop()
            self._flash_anim = None

    def nudge(self) -> None:
        """近失回弹：鼓面已停在"快三同"的过冲位（上格几乎进赔付线），
        停驻一拍后被棘轮弹回整格，并小幅越过静止位再收住。"""
        if self._nudge_anim is not None:
            self._nudge_anim.stop()
            self._nudge_anim = None
        # 属性名必须与下方 Property 的变量名 nudgeProp 一致：Qt 找不到属性时
        # 不抛异常，只打一行 warning 然后空转，set_nudge 一次都不会被调用。
        anim = QPropertyAnimation(self, b"nudgeProp", self)
        anim.setDuration(_NUDGE_MS)
        anim.setStartValue(0.0)
        # 峰值停驻：0 → _NUDGE_HOLD 这一段值不变，等于"卡在那儿不动"
        anim.setKeyValueAt(_NUDGE_HOLD, 0.0)
        # 退回一格：慢起（棘轮释放）→ 加速 → 逼近整格时减速
        anim.setKeyValueAt(0.40, -0.30)
        anim.setKeyValueAt(0.56, -1.00)
        # 越过静止位再收住，这一下"过头"是"不甘心"的来源
        anim.setKeyValueAt(0.70, -1.14)
        anim.setEndValue(-1.0)
        anim.setEasingCurve(QEasingCurve.Linear)
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
        # 停稳时 offset 停在整格 + 过冲量的位置上，回弹被打断（直接关窗）
        # 必须把两者都归一，否则相位永久偏 0.72 格。
        if self._stopped:
            self._offset = float(int(self._offset))
        self._nudge = 0.0
        self.update()

    def stop_timer(self) -> None:
        self._timer.stop()
        self.stop_flash()

    def _on_tick(self) -> None:
        if self._target is None:
            self._offset += _NORMAL_STEP
            self.update()
            return
        remaining = self._target - self._offset
        step = self._step_for_remaining(remaining)
        # 取 min 精确落格：最后一帧不吸附、不跳像素
        step = min(step, remaining)
        prev_cell = int(self._offset)
        self._offset += step
        # 高速段的过格声会糊成 18Hz 嗡鸣（主流机台此时只有气流声），
        # 只在速度降到每秒几格、每格都数得清时才响，才是真机的"哒…哒…咔"。
        if step < _TICK_SOUND_STEP and int(self._offset) != prev_cell:
            self.cell_crossed.emit()
        if self._offset >= self._target - 1e-9:
            self._halt()
            return
        self.update()

    def _step_for_remaining(self, remaining: float) -> float:
        """轻尾减速曲线：v(r) = v0 * (r/D)^p，末端长时间爬行才落定。

        r=D 处速度恰为滚动速度（无缝衔接，不跳变），随后随剩余距离单调收敛；
        p 由「距离 + 目标时长」反解（见 stop_at），因此整段耗时精确等于时长。
        p=0.5 时等价恒减速度（v ∝ √r）；p 越小尾巴越长、越"绵"。
        """
        dist = self._stop_dist
        if dist <= 0.0 or remaining >= dist:
            return _NORMAL_STEP
        if remaining <= 0.0:
            return 0.0
        return _NORMAL_STEP * (remaining / dist) ** self._stop_pow

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
        # 圆鼓几何：格沿水平轴绕一圈排布，半径由「格高 + 每格夹角」反推，
        # 等价于 Vue 的 rotateX + translateZ（参考实现 perspective 近无穷 →
        # 正交投影，所以只压缩高度、不产生梯形畸变）。
        radius = row_h / 2.0 / math.tan(math.pi / _DRUM_SLOTS)
        step = 2.0 * math.pi / _DRUM_SLOTS
        center_y = self.height() / 2.0
        # 停稳后 offset 可能停在"整格 + 过冲量"上（近失回弹的起点），
        # 所以不能用 _stopped 直接把 frac 归零，否则回弹起点会整格跳一下。
        frac = self._offset - int(self._offset)
        has_flash = self._flash is not None and self._flash_strength > 0.0
        flash_color = QColor(self._flash) if has_flash else None
        bg = QColor(_DRUM_BG)
        line = QColor(_DRUM_LINE)
        text_color = QColor(_DRUM_TEXT)
        p.fillRect(self.rect(), QColor(_DRUM_HOUSING))
        p.save()
        p.setClipRect(self.rect())
        # 回弹走旋转而非整体平移：平移会把鼓面连同暗角一起推出机身窗口，
        # 整块画面同步位移、没有静止参照物，就完全看不出来。
        # _nudge 是"从过冲位退回的格数"（0 → -1），落定后 phase 回到整格。
        phase = frac + self._nudge * _NUDGE_CELLS
        # 由远及近绘制：侧面格先画，正面格压在最上层
        cells = []
        span = _DRUM_SLOTS // 2
        for row in range(-span, span + 1):
            cos_a = math.cos((row + phase) * step)
            if cos_a <= 0.02:
                continue
            cells.append((cos_a, row))
        cells.sort(key=lambda item: item[0])
        width = float(self.width())
        for cos_a, row in cells:
            sym = self._symbol_at(row)
            ang = (row + phase) * step
            h = max(1.0, row_h * cos_a)
            top = center_y + radius * math.sin(ang) - h / 2.0
            rect = QRectF(0.0, top, width, h)
            # 参考实现里格与格之间只有一条横向 1px 线，没有左右描边、没有圆角，
            # 整条鼓面因此是连续的一整片色带（而非一个个独立卡片）。
            if has_flash and row == 0 and flash_color is not None:
                cell_bg = _blend(bg, flash_color, self._flash_strength * _FLASH_GLOW)
                line_color = _blend(
                    line, _SYM_COLOR_LIGHT_Q[sym], self._flash_strength
                )
                pen_w = 2
            else:
                cell_bg = bg
                line_color = line
                pen_w = 1
            p.fillRect(rect, cell_bg)
            p.fillRect(QRectF(0.0, top, width, float(pen_w)), line_color)
            # 字色统一深蓝（同参考实现单一字色），字号随 cos 收缩避免挤出格
            p.setPen(text_color)
            size = (26 if row == 0 else 15) * cos_a
            if _emoji_usable(sym):
                p.setFont(_emoji_font(max(7, int(size))))
                p.drawText(rect, Qt.AlignCenter, _SYM_EMOJI[sym])
            else:
                p.setFont(
                    QFont("Microsoft YaHei UI", max(6, int(size)), QFont.Bold)
                )
                p.drawText(rect, Qt.AlignCenter, _SYM_CHAR[sym])
        # 上下暗角：参考实现的 ::before/::after inset 阴影，压在所有格之上，
        # 鼓面转到上下沿时自然沉进阴影里，这才是纵深的来源。
        depth = row_h * _DRUM_SHADOW_DEPTH
        top_grad = QLinearGradient(0.0, 0.0, 0.0, depth)
        top_grad.setColorAt(0.0, QColor(0, 0, 0, _DRUM_SHADOW_ALPHA))
        top_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0.0, 0.0, width, depth), top_grad)
        bottom_grad = QLinearGradient(
            0.0, self.height() - depth, 0.0, float(self.height())
        )
        bottom_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        bottom_grad.setColorAt(1.0, QColor(0, 0, 0, _DRUM_SHADOW_ALPHA))
        p.fillRect(
            QRectF(0.0, self.height() - depth, width, depth), bottom_grad
        )
        p.restore()
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
        for index in range(3):
            reel = _ReelWidget(self)
            reel.stopped.connect(lambda i=index: self._on_reel_stopped(i))
            reel.cell_crossed.connect(self._on_reel_cell_tick)
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

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch(1)
        self._stop_btn = QPushButton("停", self)
        self._stop_btn.setObjectName("StopBtn")
        self._stop_btn.setToolTip("手动停轴（空格）")
        self._stop_btn.setFixedWidth(72)
        self._stop_btn.setFocusPolicy(Qt.NoFocus)
        self._stop_btn.clicked.connect(self._press_stop)
        btn_row.addWidget(self._stop_btn)
        self._close_btn = QPushButton("收下", self)
        self._close_btn.setObjectName("Primary")
        self._close_btn.clicked.connect(self.accept)
        self._close_btn.setEnabled(False)
        btn_row.addWidget(self._close_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

    # ---- 手动停轴 ----

    def _press_stop(self) -> None:
        """玩家按下「停」：三轴同一帧起刹，阻力递增（减速区依次变宽），结果不变。

        同时起刹更像真机"三个刹车片一起压下"，减速区不同则落定时间自然错开；
        第三轴在悬念场景下走更宽的减速区，保留"慢慢停"的紧张感。
        """
        if self._finalized or self._result_shown:
            return
        for timer in self._stop_timers:
            timer.stop()
        self._stop_timers.clear()
        for i, reel in enumerate(self._reels):
            if reel.is_stopped() or reel.is_stopping():
                continue
            self._stop_one(i)

    def _stop_one(self, i: int) -> None:
        reel = self._reels[i]
        if self._finalized or self._result_shown or reel.is_stopped() or reel.is_stopping():
            return
        suspense_sym = None
        graze = None
        if i == 2:
            suspense_sym = third_has_suspense(self._result)
            graze = near_miss_symbol(self._result)
        if suspense_sym is not None and _SUSPENSE_HEARTBEAT.get(suspense_sym, False):
            sfx = _get_sfx()
            if sfx is not None:
                sfx.play_slot_suspense()
        reel.stop_at(
            self._result[i],
            suspense_sym=suspense_sym,
            graze=graze,
            duration_ms=_stop_duration_ms(i, suspense_sym),
        )

    # ---- 动画 ----

    def _start_spin(self) -> None:
        sfx = _get_sfx()
        for i in range(3):
            if sfx is not None:
                sfx.play_slot_spin(i)
        for reel in self._reels:
            reel.start_spin()
        self._stopped_count = 0
        self._stop_timers.clear()
        # 不自动停轴：等玩家按「停」/空格；超过兜底时长仍未按则自动停，
        # 防止模态弹窗无限卡住主界面。
        fallback = QTimer(self)
        fallback.setSingleShot(True)
        fallback.timeout.connect(self._press_stop)
        fallback.start(_AUTO_STOP_FALLBACK_MS)
        self._stop_timers.append(fallback)

    def _on_reel_cell_tick(self) -> None:
        """减速段每过一格一声轻卡榫：三轴同时减速，天然形成交错的"哒哒"。"""
        if self._finalized:
            return
        sfx = _get_sfx()
        if sfx is not None:
            sfx.play_slot_reel_tick()

    def _on_reel_stopped(self, index: int = -1) -> None:
        if self._finalized:
            return
        sfx = _get_sfx()
        if sfx is not None:
            sfx.play_slot_reel_stop()
            # 停一轴就停那一台的放映机：三轴的放映机声随之逐轴减少，
            # 全部停稳时自然静音，不需要再统一收尾。
            if index >= 0:
                sfx.stop_slot_spin(index)
        if index == 2 and self._has_near_miss():
            # 第三轴刚停在"快三同"的过冲位，回弹必须紧接落定，不能等揭晓：
            # 落定 → 停驻一拍 → 被棘轮弹回一格，是一段连续运动。
            self._reels[2].nudge()
        self._stopped_count += 1
        if self._stopped_count >= 3:
            if sfx is not None:
                sfx.stop_slot_spin()
            if not self._result_shown:
                self._on_all_stopped()

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
        elif prizes.letters:
            # 樱桃赔付（最左轴字）：只亮左轴一格，小奖的克制感
            self._reels[0].set_flash(_SYM_COLOR[_SYM_LETTER])
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
        self._stop_btn.setEnabled(False)
        self._close_btn.setEnabled(True)
        self._close_btn.setFocus()
        self._result_shown = True

    def _has_near_miss(self) -> bool:
        """无奖但差一格：恰好两轴同号（非三同）。"""
        return near_miss_symbol(self._result) is not None

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
        """近失揭晓：按符号权重分级音效/闪烁（回弹已在落定时触发）。"""
        if self._finalized or not self._near_miss_active:
            return
        pair_sym = near_miss_symbol(self._result)
        sfx = _get_sfx()
        if pair_sym is not None and sfx is not None:
            w = _NEAR_MISS_WEIGHTS.get(pair_sym, 2)
            if w >= 3:
                sfx.play_slot_near_miss_high()
            elif w <= 1:
                sfx.play_slot_near_miss_low()
            else:
                sfx.play_slot_near_miss()
        elif sfx is not None:
            sfx.play_slot_near_miss()
        if pair_sym is not None:
            text = _NEAR_MISS_TEXTS.get(pair_sym, "差一点！")
            self._near_miss_label.setText(text)
        self._near_miss_label.setVisible(True)
        blink_count = _NEAR_MISS_BLINKS.get(pair_sym, _BLINK_TOTAL) if pair_sym is not None else _BLINK_TOTAL
        self._blink_left = blink_count
        self._near_miss_sym = pair_sym
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._blink_near_miss)
        self._blink_timer.start(_BLINK_MS)

    def _blink_near_miss(self) -> None:
        self._blink_left -= 1
        sym = getattr(self, "_near_miss_sym", None)
        color = _NEAR_MISS_COLORS.get(sym, _NEAR_MISS_COLOR) if sym is not None else _NEAR_MISS_COLOR
        size = _NEAR_MISS_SIZES.get(sym, 15) if sym is not None else 15
        if self._blink_left <= 0:
            if self._blink_timer is not None:
                self._blink_timer.stop()
            self._near_miss_label.setStyleSheet(
                f"color: {color}; font-size: {size}px; font-weight: 700;"
            )
            return
        if self._blink_left % 2:
            self._near_miss_label.setStyleSheet(
                f"color: {color}; font-size: {size}px; font-weight: 700;"
            )
        else:
            self._near_miss_label.setStyleSheet(
                f"color: #5a5e6e; font-size: {size}px; font-weight: 700;"
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
        if event.key() == Qt.Key_Space:
            self._press_stop()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self._finalize()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        center_dialog_on_parent(self)


_SYM_FROM_CHAR = {v: k for k, v in _SYM_CHAR.items()}


def _parse_result(text: str) -> Tuple[int, int, int]:
    """解析调试结果串：000 / 0,0,0 / 金钻字♪。"""
    raw = text.replace(",", "").replace(" ", "")
    if len(raw) != 3:
        raise ValueError("结果必须是 3 个符号：000 / 0,0,0 / 金钻字♪")
    out: List[int] = []
    for ch in raw:
        if ch in _SYM_FROM_CHAR:
            out.append(_SYM_FROM_CHAR[ch])
        elif ch.isdigit() and int(ch) in _SYM_CHAR:
            out.append(int(ch))
        else:
            raise ValueError(f"未知符号：{ch}")
    return out[0], out[1], out[2]


def _describe_result(result: Tuple[int, int, int]) -> str:
    triple = _match_of(*result)
    if triple is not None:
        return f"三同{_SYM_CHAR[triple]}"
    pair = near_miss_symbol(result)
    if pair is not None:
        return f"近失（{_SYM_CHAR[pair]}{_SYM_CHAR[pair]}，第三轴差一格）"
    return "无奖"


def _describe_prizes(prizes: SlotPrizes) -> str:
    parts: List[str] = []
    if prizes.reward.gold:
        parts.append(f"金币 {prizes.reward.gold}")
    if prizes.reward.diamond:
        parts.append(f"钻石 {prizes.reward.diamond}")
    if prizes.letters:
        parts.append(
            "字母 " + "、".join(
                f"{c}（{_LETTER_RARITY_NAMES[r]}）" for c, r in prizes.letters
            )
        )
    if prizes.song_tokens:
        parts.append(
            "试听券 " + "、".join(f"{n}×{c}" for n, c in prizes.song_tokens.items())
        )
    return "，".join(parts) if parts else "空"


def _main(argv: Optional[List[str]] = None) -> int:
    """独立调试启动器：python -m src.ui.slot_machine [结果] [--gold N] [--seed N]。"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m src.ui.slot_machine",
        description="老虎机独立调试启动器（不加载主程序与存档）",
    )
    parser.add_argument("result", nargs="?", help="强制结果：000 / 0,0,0 / 金钻字♪；省略则随机")
    parser.add_argument("--gold", type=float, default=1.0, help="基础金币（三同金倍率基准）")
    parser.add_argument("--diamond", type=float, default=0.0, help="基础钻石（三同钻倍率基准）")
    parser.add_argument("--title", default="老虎机调试", help="弹窗标题")
    parser.add_argument("--seed", type=int, default=None, help="固定随机种子")
    args = parser.parse_args(argv)

    forced = _parse_result(args.result) if args.result else None
    rng = random.Random(args.seed) if args.seed is not None else None
    result = forced if forced is not None else spin_result(rng)

    app = QApplication(sys.argv[:1])
    from ..sfx import SfxPlayer

    sfx = SfxPlayer({})
    app.sfx = sfx

    print(f"[结果] {result} → {_describe_result(result)}")
    print(f"[音效] capable={sfx.capable()}")
    for sym, p in triple_probabilities().items():
        print(f"[概率] 三同{_SYM_CHAR[sym]} {p * 100:.2f}%")

    dlg = SlotMachineDialog(
        Reward(gold=args.gold, diamond=args.diamond),
        task_title=args.title,
        result=result,
        rng=rng,
    )
    dlg.prize_decided.connect(lambda prizes: print(f"[产出] {_describe_prizes(prizes)}"))
    dlg.exec()
    sfx.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())