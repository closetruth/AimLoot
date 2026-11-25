"""开奖进度条控件。"""
from __future__ import annotations

import random
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QPolygonF, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

# 每段 ease-out 指数：越大越「前冲后磨」。
_EASE_EXPONENT = 2.6
# 独立于开奖的视觉周期：约 258～342 秒一轮（空闲时接近 5 分钟）。
_EASE_SPAN_MIN = 258
_EASE_SPAN_STEP = 6
_EASE_SPAN_RANGE = 15
_EASE_CYCLES_PER_BLOCK = 15
_BAR_H = 22
_TRACK_H = 12
_CHEST_SIZE = 13
# 普通 / 罕见 / 稀有 / 史诗 / 传奇
_RARITY_COMMON = 0
_RARITY_UNCOMMON = 1
_RARITY_RARE = 2
_RARITY_EPIC = 3
_RARITY_LEGEND = 4
# body, lid, glow
_RARITY_PALETTE = (
    ("#8a7a68", "#6e6254", "#c8c0b4"),
    ("#3d8f5a", "#2d6b44", "#7dcc96"),
    ("#3d6ec9", "#2a4f96", "#7aa2ff"),
    ("#7a4ad4", "#5a32a8", "#c9a0ff"),
    ("#d4a017", "#b8860b", "#ffd56a"),
)
# 单箱稀有度：沿用旧第 3 箱权重（5 分钟一只，质量略高）。
_RARITY_WEIGHTS = (22, 25, 25, 18, 10)
CHEST_RARITY_NAMES = ("普通", "罕见", "稀有", "史诗", "传奇")
CHEST_RARITY_COLORS = ("#c8c0b4", "#7dcc96", "#7aa2ff", "#c9a0ff", "#ffd56a")
# 填充暗档 / 亮档，跟箱子稀有度走
_FILL_FLASH_COLORS = (
    ("#5c5348", "#f0ebe4"),
    ("#1e4a2e", "#b8ffce"),
    ("#1a3a7a", "#dce8ff"),
    ("#3a1e78", "#f0e0ff"),
    ("#7a5a08", "#fff0b8"),
)


def _ease_span_for_cycle(cycle_id: int) -> int:
    # 7 与 15 互质，15 轮里 span 走遍 258、264、…、342。
    return _EASE_SPAN_MIN + ((max(0, cycle_id) * 7) % _EASE_SPAN_RANGE) * _EASE_SPAN_STEP


def _ease_block_ops() -> int:
    return sum(_ease_span_for_cycle(i) for i in range(_EASE_CYCLES_PER_BLOCK))


def _independent_cycle(units: int) -> Tuple[int, int, int]:
    """把运行中目标的 units（操作数+秒）映射到独立视觉周期。"""
    total_ops = max(0, int(units))
    block = _ease_block_ops()
    blocks, rem = divmod(total_ops, block)
    cycle_id = blocks * _EASE_CYCLES_PER_BLOCK
    for i in range(_EASE_CYCLES_PER_BLOCK):
        cid = cycle_id + i
        span = _ease_span_for_cycle(cid)
        if rem <= span:
            return rem, span, cid
        rem -= span
    nxt = cycle_id + _EASE_CYCLES_PER_BLOCK
    return 0, _ease_span_for_cycle(nxt), nxt


def resolve_held_cycle(
    units: int,
    *,
    freeze_at_end: bool,
    holding: bool,
    held_cycle_id: int,
    origin_units: int = 0,
    relative: bool = False,
) -> Tuple[int, int, int, bool]:
    """满格且本轮箱子未领时钳在 100%；relative 时 units 即本轮工作量。"""
    if relative or origin_units > 0:
        cid = int(held_cycle_id)
        span = _ease_span_for_cycle(cid)
        if relative:
            progress = max(0, int(units))
        else:
            progress = max(0, int(units) - int(origin_units))
        if freeze_at_end and (holding or (span > 0 and progress >= span)):
            return span, span, cid, True
        return min(progress, span), span, cid, False
    progress, span, cid = _independent_cycle(units)
    if not freeze_at_end:
        return progress, span, cid, False
    if holding:
        held_cid = int(held_cycle_id)
        held_span = _ease_span_for_cycle(held_cid)
        return held_span, held_span, held_cid, True
    if span > 0 and progress >= span:
        return span, span, cid, True
    return progress, span, cid, False


def _cycle_checkpoints(span: int, cycle_id: int = 0) -> Tuple[float, ...]:
    """每个视觉周期一个终点检查点（箱子画在 100%）。"""
    return (1.0,)


def _cycle_chest_rarities(span: int, cycle_id: int = 0) -> Tuple[int, ...]:
    """每个视觉周期一只箱子的稀有度，由 span+cycle 固定。"""
    rng = random.Random(f"chest:{max(1, span)}:{int(cycle_id)}")
    roll = rng.randrange(sum(_RARITY_WEIGHTS))
    acc = 0
    rarity = _RARITY_COMMON
    for idx, w in enumerate(_RARITY_WEIGHTS):
        acc += w
        if roll < acc:
            rarity = idx
            break
    return (rarity,)


def fill_flash_interval_ms(eased: float) -> Optional[int]:
    """填充闪烁间隔：前期不闪，越接近终点越快。"""
    u = max(0.0, min(1.0, eased)) ** 2
    if u < 0.04:
        return None
    return max(160, int(800 - 620 * u))


def fill_flash_hex(rarity: int, *, flash_on: bool) -> str:
    idx = max(0, min(_RARITY_LEGEND, int(rarity)))
    rest, hot = _FILL_FLASH_COLORS[idx]
    return hot if flash_on else rest


def _with_alpha(color: QColor, alpha: int) -> QColor:
    out = QColor(color)
    out.setAlpha(alpha)
    return out


def _draw_chest(
    painter: QPainter,
    cx: float,
    cy: float,
    size: float,
    *,
    reached: bool,
    rarity: int,
    flash_on: bool,
    opened: bool = False,
    body_hex: str | None = None,
    lid_hex: str | None = None,
    glow_hex: str | None = None,
    muted_alpha: int | None = None,
) -> None:
    """关盖宝箱。opened=True 表示已领进背包：熄灭无光。"""
    rarity = max(0, min(_RARITY_LEGEND, int(rarity)))
    p_body, p_lid, p_glow = _RARITY_PALETTE[rarity]
    body_hex = body_hex or p_body
    lid_hex = lid_hex or p_lid
    glow_hex = glow_hex or p_glow
    glow = QColor(glow_hex)
    tall = 0.96 if rarity == _RARITY_UNCOMMON else 0.88
    w = size * (1.06 if rarity == _RARITY_LEGEND else 1.0)
    h = size * tall
    round_body = 2.4 if rarity == _RARITY_RARE else 1.6
    round_lid = 2.6 if rarity == _RARITY_RARE else 1.8
    body = QRectF(cx - w * 0.48, cy - h * 0.08, w * 0.96, h * 0.62)
    lid = QRectF(cx - w * 0.5, cy - h * 0.42, w, h * 0.38)

    show_glow = reached and not opened
    if opened:
        body_c = _with_alpha(QColor(body_hex), 48)
        lid_c = _with_alpha(QColor(lid_hex), 56)
        rim = QColor(255, 255, 255, 24)
        accent = _with_alpha(QColor(glow_hex), 40)
    elif show_glow:
        glow_mul = 1.9 if rarity == _RARITY_LEGEND else 1.55
        radius = size * (glow_mul if flash_on else glow_mul - 0.35)
        grad = QRadialGradient(cx, cy, radius)
        glow.setAlpha(165 if flash_on else 80)
        fade = QColor(glow)
        fade.setAlpha(0)
        grad.setColorAt(0.0, glow)
        grad.setColorAt(1.0, fade)
        painter.setPen(Qt.NoPen)
        painter.setBrush(grad)
        painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))
        body_c = QColor(body_hex)
        lid_c = QColor(lid_hex)
        rim = _with_alpha(QColor(glow_hex), 230 if flash_on else 150)
        accent = QColor(glow_hex)
    else:
        ba = 96 if muted_alpha is None else max(0, min(255, muted_alpha))
        la = min(255, ba + 20)
        body_c = _with_alpha(QColor(body_hex), ba)
        lid_c = _with_alpha(QColor(lid_hex), la)
        rim = QColor(255, 255, 255, 36 if muted_alpha is None else min(255, ba // 2))
        accent = _with_alpha(QColor(glow_hex), 100)

    painter.setPen(QPen(rim, 1.5 if rarity == _RARITY_EPIC else 0.9))
    painter.setBrush(body_c)
    painter.drawRoundedRect(body, round_body, round_body)
    if rarity == _RARITY_EPIC:
        painter.setPen(QPen(_with_alpha(QColor(glow_hex), 160 if show_glow else 50), 0.7))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(body.adjusted(0.9, 0.9, -0.9, -0.9), 1.2, 1.2)
    painter.setPen(QPen(rim, 0.9))
    painter.setBrush(lid_c)
    painter.drawRoundedRect(lid, round_lid, round_lid)

    if rarity == _RARITY_UNCOMMON:
        band_w = max(1.0, w * 0.08)
        iron = _with_alpha(
            QColor("#c9d4c4") if show_glow else QColor("#9aaa9a"),
            200 if show_glow else 70,
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(iron)
        painter.drawRect(QRectF(body.left() + 1.4, body.top(), band_w, body.height()))
        painter.drawRect(QRectF(body.right() - 1.4 - band_w, body.top(), band_w, body.height()))

    if rarity == _RARITY_EPIC:
        ridge = QPolygonF(
            [
                QPointF(cx - w * 0.16, lid.top() + 0.6),
                QPointF(cx, lid.top() - h * 0.16),
                QPointF(cx + w * 0.16, lid.top() + 0.6),
            ]
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawPolygon(ridge)

    if rarity == _RARITY_LEGEND:
        crown = QPolygonF(
            [
                QPointF(cx - w * 0.18, lid.top() + 0.4),
                QPointF(cx - w * 0.06, lid.top() - h * 0.12),
                QPointF(cx, lid.top() + 0.2),
                QPointF(cx + w * 0.06, lid.top() - h * 0.18),
                QPointF(cx + w * 0.18, lid.top() + 0.4),
            ]
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawPolygon(crown)

    painter.setPen(Qt.NoPen)
    painter.setBrush(accent)
    if rarity == _RARITY_RARE:
        gw = max(1.4, w * 0.14)
        gh = max(1.6, h * 0.16)
        gem = QPolygonF(
            [
                QPointF(cx, cy - gh),
                QPointF(cx + gw, cy),
                QPointF(cx, cy + gh),
                QPointF(cx - gw, cy),
            ]
        )
        painter.drawPolygon(gem)
    else:
        latch_w = max(1.6, w * (0.22 if rarity == _RARITY_LEGEND else 0.16))
        latch_h = max(2.2, h * 0.28)
        painter.drawRoundedRect(
            QRectF(cx - latch_w / 2, cy - latch_h * 0.15, latch_w, latch_h),
            0.6,
            0.6,
        )


def _segment_eased(raw: float, points: Tuple[float, ...], exponent: float) -> float:
    """分段幂函数 ease-out：到每个点前都先快后慢。"""
    if raw <= 0.0:
        return 0.0
    if raw >= 1.0:
        return 1.0
    prev = 0.0
    for pt in points:
        if raw <= pt:
            width = pt - prev
            if width <= 1e-9:
                return pt
            local = (raw - prev) / width
            eased_local = 1.0 - pow(1.0 - local, exponent)
            return prev + eased_local * width
        prev = pt
    return 1.0


class EasedProgressBar(QWidget):
    """非格子的小型平滑进度条：终点一箱、整段先快后慢。"""

    point_reached = Signal()
    chest_claimed = Signal(int, int)  # index, rarity
    cycle_changed = Signal(int)  # cycle_id

    def __init__(self, parent=None, *, exponent: float = _EASE_EXPONENT):
        super().__init__(parent)
        self._progress = 0
        self._span = 10
        self._cycle_id = 0
        self._exponent = max(1.0, exponent)
        self._points = _cycle_checkpoints(self._span, self._cycle_id)
        self._rarities = _cycle_chest_rarities(self._span, self._cycle_id)
        self._opened = (False,)
        self._holding = False
        self._have_baseline = False
        self._flash_on = True
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(400)
        self._flash_timer.timeout.connect(self._toggle_dot_flash)
        self.setMinimumHeight(_BAR_H)
        self.setMaximumHeight(_BAR_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    @property
    def cycle_id(self) -> int:
        return self._cycle_id

    @property
    def holding(self) -> bool:
        return self._holding

    def apply_claimed(self, claimed: Tuple[bool, ...]) -> None:
        """用存档里的本轮领取状态覆盖绘制。"""
        nxt = (bool(claimed[0]) if len(claimed) > 0 else False,)
        if nxt == self._opened:
            return
        self._opened = nxt
        self._sync_flash_timer()
        self.update()

    def mark_claimed(self, index: int) -> None:
        if index < 0 or index > 0:
            return
        opened = list(self._opened)
        if index >= len(opened):
            return
        opened[index] = True
        self._opened = tuple(opened)
        self._sync_flash_timer()
        self.update()

    def _layout_inset(self) -> float:
        h = float(max(1, self.height()))
        track_h = min(_TRACK_H, h)
        radius = track_h / 2.0
        return max(radius, _CHEST_SIZE * 0.62)

    def chest_center_local(self, index: int) -> QPoint:
        """检查点中心（控件本地坐标）。"""
        w = float(max(1, self.width()))
        h = float(max(1, self.height()))
        inset = self._layout_inset()
        pt = self._points[index] if 0 <= index < len(self._points) else 0.0
        cx = inset + pt * (w - 2 * inset)
        half = _CHEST_SIZE * 0.55
        cx = max(half, min(w - half, cx))
        return QPoint(int(round(cx)), int(round(h / 2.0)))

    def _chest_hit_rect(self, index: int) -> QRect:
        c = self.chest_center_local(index)
        box_w = max(_CHEST_SIZE + 8, 18)
        box_h = max(int(self.height()), _BAR_H)
        x = c.x() - box_w // 2
        x = max(0, min(max(0, self.width() - box_w), x))
        return QRect(x, 0, box_w, box_h)

    def set_progress(
        self,
        units: int,
        *,
        freeze_at_end: bool = False,
        holding: bool = False,
        held_cycle_id: int = 0,
        origin_units: int = 0,
        relative: bool = False,
    ) -> bool:
        """按运行中目标的 units 刷新。满格且 freeze_at_end 时停住不换轮。"""
        progress, span, cycle_id, now_holding = resolve_held_cycle(
            units,
            freeze_at_end=freeze_at_end,
            holding=holding,
            held_cycle_id=held_cycle_id if (holding or origin_units > 0 or relative) else self._cycle_id,
            origin_units=origin_units,
            relative=relative,
        )
        cycle_changed = span != self._span or cycle_id != self._cycle_id
        if cycle_changed:
            points = _cycle_checkpoints(span, cycle_id)
            rarities = _cycle_chest_rarities(span, cycle_id)
            opened = (False,)
        else:
            points = self._points
            rarities = self._rarities
            opened = self._opened
        if (
            self._progress == progress
            and self._span == span
            and self._cycle_id == cycle_id
            and points == self._points
            and rarities == self._rarities
            and self._holding == now_holding
        ):
            return self._holding
        old_eased = self._eased_fraction()
        emit_chime = (
            self._have_baseline
            and not cycle_changed
            and self._newly_reached(old_eased, progress, span, points)
        )
        self._progress = progress
        self._span = span
        self._cycle_id = cycle_id
        self._points = points
        self._rarities = rarities
        self._opened = opened
        self._holding = now_holding
        self._have_baseline = True
        self._sync_flash_timer()
        self.update()
        if cycle_changed:
            self.cycle_changed.emit(cycle_id)
        if emit_chime:
            self.point_reached.emit()
        return self._holding

    def _newly_reached(
        self,
        old_eased: float,
        progress: int,
        span: int,
        points: Tuple[float, ...],
    ) -> bool:
        raw = progress / max(1, span)
        new_eased = _segment_eased(raw, points, self._exponent)
        return any(
            old_eased + 1e-6 < pt <= new_eased + 1e-6
            for pt in points
        )

    def _raw_fraction(self) -> float:
        return self._progress / max(1, self._span)

    def _eased_fraction(self) -> float:
        return _segment_eased(self._raw_fraction(), self._points, self._exponent)

    def _any_claimable(self) -> bool:
        eased = self._eased_fraction()
        for i, pt in enumerate(self._points):
            opened = self._opened[i] if i < len(self._opened) else False
            if eased + 1e-6 >= pt and not opened:
                return True
        return False

    def _sync_flash_timer(self) -> None:
        interval = fill_flash_interval_ms(self._eased_fraction())
        want = self._any_claimable() or interval is not None
        if want:
            self._flash_timer.setInterval(interval if interval is not None else 400)
            if not self._flash_timer.isActive():
                self._flash_on = True
                self._flash_timer.start()
        elif self._flash_timer.isActive():
            self._flash_timer.stop()
            self._flash_on = True

    def _toggle_dot_flash(self) -> None:
        self._flash_on = not self._flash_on
        self.update()

    def _hit_chest_index(self, pos: QPoint) -> int:
        eased = self._eased_fraction()
        for i, pt in enumerate(self._points):
            opened = self._opened[i] if i < len(self._opened) else False
            if opened or eased + 1e-6 < pt:
                continue
            if self._chest_hit_rect(i).contains(pos):
                return i
        return -1

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            idx = self._hit_chest_index(event.position().toPoint())
            if idx >= 0:
                rarity = self._rarities[idx] if idx < len(self._rarities) else _RARITY_COMMON
                self.chest_claimed.emit(idx, int(rarity))
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        track_h = min(_TRACK_H, h)
        track_y = (h - track_h) / 2.0
        radius = track_h / 2.0
        inset = max(radius, _CHEST_SIZE * 0.62)

        track = QRectF(0, track_y, w, track_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 22))
        painter.drawRoundedRect(track, radius, radius)

        eased = self._eased_fraction()
        fill_w = inset + eased * (w - 2 * inset) + inset if eased > 0 else 0.0
        fill_w = max(0.0, min(w, fill_w))
        if fill_w > 0:
            fill_flash = self._flash_timer.isActive() and self._flash_on
            rarity = self._rarities[0] if self._rarities else _RARITY_COMMON
            painter.setBrush(QColor(fill_flash_hex(rarity, flash_on=fill_flash)))
            painter.drawRoundedRect(QRectF(0, track_y, fill_w, track_h), radius, radius)

        # 已走过/当前段：本段完成度按 raw 先快后慢；稀有度字 + 深色描边；1 位小数
        usable = w - 2 * inset
        font = QFont("Microsoft YaHei UI", 9)
        font.setBold(True)
        painter.setFont(font)
        raw = self._raw_fraction()
        exp = self._exponent
        prev = 0.0
        for i, pt in enumerate(self._points):
            pt_f = float(pt)
            if eased <= prev + 1e-6:
                prev = pt_f
                continue
            width = pt_f - prev
            if width <= 1e-9 or eased >= pt_f - 1e-6 or raw >= pt_f - 1e-6:
                seg_pct = 100.0
            elif raw <= prev + 1e-6:
                seg_pct = 0.0
            else:
                local = max(0.0, min(1.0, (raw - prev) / width))
                eased_local = 1.0 - pow(1.0 - local, exp)
                seg_pct = eased_local * 100.0
            mid = (prev + pt_f) * 0.5
            tx = inset + mid * usable
            rarity = self._rarities[i] if i < len(self._rarities) else _RARITY_COMMON
            rarity = max(0, min(_RARITY_LEGEND, int(rarity)))
            glow_hex = _RARITY_PALETTE[rarity][2]
            label = f"{seg_pct:.1f}%"
            text_rect = QRectF(tx - 24.0, track_y, 48.0, track_h)
            painter.setPen(QColor(20, 22, 30, 220))
            for dx, dy in (
                (-1, 0),
                (1, 0),
                (0, -1),
                (0, 1),
            ):
                painter.drawText(
                    text_rect.translated(dx, dy),
                    int(Qt.AlignCenter),
                    label,
                )
            painter.setPen(QColor(glow_hex))
            painter.drawText(text_rect, int(Qt.AlignCenter), label)
            prev = pt_f

        for i, pt in enumerate(self._points):
            cx = inset + pt * (w - 2 * inset)
            reached = eased + 1e-6 >= pt
            rarity = self._rarities[i] if i < len(self._rarities) else _RARITY_COMMON
            opened = self._opened[i] if i < len(self._opened) else False
            _draw_chest(
                painter,
                cx,
                h / 2.0,
                _CHEST_SIZE,
                reached=reached,
                rarity=rarity,
                flash_on=self._flash_on and reached and not opened,
                opened=opened,
            )

        painter.end()


class SegmentedRollBar(QWidget):
    """每格随机颜色的分段进度条，中央显示进度与当前概率。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._progress = 0
        self._span = 10
        self._colors: List[str] = []
        self._chance_label = ""
        self._flash = False
        self._near_full_steps = 0
        self._op_flash = False
        self._op_flash_timer = QTimer(self)
        self._op_flash_timer.setSingleShot(True)
        self._op_flash_timer.timeout.connect(self._end_op_flash)
        self.setMinimumHeight(18)
        self.setMaximumHeight(18)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_cycle(
        self,
        progress: int,
        span: int,
        colors: List[str],
        chance_label: str = "",
        near_full_steps: int = 0,
    ) -> None:
        progress = max(0, min(progress, max(1, span)))
        span = max(1, span)
        norm_colors = (
            colors if len(colors) == span else (colors + ["#6c8cff"] * span)[:span]
        )
        changed = (
            self._progress != progress
            or self._span != span
            or self._colors != norm_colors
            or self._chance_label != chance_label
            or self._near_full_steps != near_full_steps
        )
        self._progress = progress
        self._span = span
        self._colors = norm_colors
        self._chance_label = chance_label
        self._near_full_steps = max(0, near_full_steps)
        if changed:
            self.update()

    def set_flash(self, active: bool) -> None:
        if self._flash != active:
            self._flash = active
            self.update()

    def pulse_operation(self) -> None:
        """按键闪光：用定时器收尾，禁止在 paintEvent 里再 update（半透明置顶窗会卡死）。"""
        if not self._op_flash:
            self._op_flash = True
            self.update()
        self._op_flash_timer.start(180)

    def _end_op_flash(self) -> None:
        if self._op_flash:
            self._op_flash = False
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        radius = h / 2
        op_flash = self._op_flash

        bg = QColor(255, 255, 255, 16)
        border = QColor(255, 255, 255, 28)
        if self._near_full_steps > 0:
            strength = 5 - min(4, self._near_full_steps)
            bg = QColor(255, 214, 102, 18 + strength * 6)
            border = QColor(255, 214, 102, 60 + strength * 30)
        if op_flash:
            bg = bg.lighter(120)
            border = border.lighter(120)
        painter.setPen(QPen(border, 1.2 if self._near_full_steps > 0 else 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(QRectF(0, 0, w, h), radius, radius)

        span = self._span
        gap = 2
        seg_w = (w - gap * (span - 1)) / span if span > 0 else w

        for i in range(span):
            x = i * (seg_w + gap)
            color_hex = self._colors[i] if i < len(self._colors) else "#6c8cff"
            base = QColor(color_hex)
            filled = i < self._progress
            dist_to_roll = span - i
            if filled:
                c = base
                if self._flash:
                    c = c.lighter(140)
                elif op_flash:
                    c = c.lighter(118)
            else:
                c = QColor(base)
                c.setAlpha(40)
                if (
                    0 < self._near_full_steps <= 4
                    and dist_to_roll <= self._near_full_steps
                ):
                    boost = 18 + (self._near_full_steps - dist_to_roll + 1) * 12
                    c.setAlpha(min(120, c.alpha() + boost))
                    if dist_to_roll == 1:
                        c = c.lighter(138)
                    elif dist_to_roll == 2:
                        c = c.lighter(125)
            painter.setPen(Qt.NoPen)
            painter.setBrush(c)
            painter.drawRoundedRect(QRectF(x, 0, seg_w, h), 3, 3)

        text_color = QColor("#cfd3e0")
        if self._near_full_steps > 0:
            text_color = QColor("#ffe08a")
        painter.setPen(QPen(text_color))
        font = QFont("Microsoft YaHei UI", 8)
        font.setBold(True)
        painter.setFont(font)
        main_text = f"{self._progress}/{self._span}"
        if self._chance_label:
            main_text = f"{main_text}  {self._chance_label}"
        if self._near_full_steps > 0:
            main_text = f"{main_text}  即将开奖"
        painter.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, main_text)

        painter.end()
