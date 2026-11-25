"""界面文案格式化（避免 emoji 在 Windows 默认字体下显示为方框）。"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .models import RollHistoryEntry, Subtask, Task

from .models import TaskStatus


def format_duration(seconds: float) -> str:
    """进行中累计时长（秒 → 可读字符串）。"""
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# 子目标区：深色底 + 彩色数据（避免一片白）
_COLOR_OPS = "#6ee7a0"
_COLOR_OPS_1MIN = "#ff9f6b"
_COLOR_GOLD = "#f5c842"
_COLOR_DIAM = "#5ec8f2"
_TOAST_GOLD = "#ffd54f"
_TOAST_DIAM = "#7dd3fc"
_TOAST_CRIT = "#ff9f43"
_COLOR_TIME = "#b0b8cc"
_COLOR_TEXT = "#ffffff"
_COLOR_MUTED = "#e8ecf4"
_COLOR_PAUSED = "#f0f2f8"
_COLOR_TITLE_BRIGHT = "#ffffff"
_COLOR_CURRENT = "#9ec5ff"
_COLOR_MARKER = "#c0c8dc"
_COLOR_WARN = "#e6a830"
_COLOR_CLAIM = "#f0c040"

_BASE_FONT = "font-family:'Microsoft YaHei UI','Microsoft YaHei','Segoe UI',sans-serif;"


def _font(size: int) -> str:
    return f"font-size:{size}px;{_BASE_FONT}"


def _muted_sep() -> str:
    return f'<span style="color:{_COLOR_MUTED}"> · </span>'


def _crit_base(amount: float, mult: float) -> str:
    """由暴击后实得反推基数（一位小数）。"""
    if mult <= 0:
        return format_amount(amount)
    return format_amount(round(float(amount) / float(mult), 1))


def _html_span(color: str, text: str, *, bold: bool = True) -> str:
    weight = "font-weight:700;" if bold else ""
    return f'<span style="color:{color};{weight}">{text}</span>'


def _crit_formula_html(
    amount: float,
    mult: float,
    label: str,
    amount_color: str,
    formula_color: str,
    *,
    spaced: bool,
) -> str:
    """基数 × 倍率 → +实得标签。"""
    base = _crit_base(amount, mult)
    mult_txt = format_amount(mult)
    if spaced:
        left = _html_span(formula_color, f"{base} × {mult_txt}")
        arrow = _html_span(_COLOR_MUTED, " → ", bold=False)
        gained = f"+{format_amount(amount)} {label}"
    else:
        left = _html_span(formula_color, f"{base}×{mult_txt}")
        arrow = _html_span(_COLOR_MUTED, "→", bold=False)
        gained = f"+{format_amount(amount)}{label}"
    return left + arrow + _html_span(amount_color, gained)


def _plain_amount_html(
    amount: float,
    label: str,
    color: str,
    *,
    spaced: bool,
) -> str:
    text = (
        f"+{format_amount(amount)} {label}"
        if spaced
        else f"+{format_amount(amount)}{label}"
    )
    return _html_span(color, text)


def _crit_formula_plain(
    amount: float,
    mult: float,
    label: str,
    *,
    spaced: bool,
) -> str:
    """纯文本：未暴击只写实得；暴击写成 基数×倍率→实得。"""
    if mult <= 1.0:
        if spaced:
            return f"+{format_amount(amount)} {label}"
        return f"+{format_amount(amount)}{label}"
    base = _crit_base(amount, mult)
    mult_txt = format_amount(mult)
    if spaced:
        return f"{base} × {mult_txt} → +{format_amount(amount)} {label}"
    return f"{base}×{mult_txt}→+{format_amount(amount)}{label}"


def format_ops_summary_html(
    total_ops: int,
    *,
    ops_1min: int | None = None,
) -> str:
    """悬浮窗顶栏操作数字（RichText）。金币/钻石由 RollingAmount 绘制。"""
    parts: list[str] = []
    if ops_1min is not None:
        parts.append(
            f'<span style="color:{_COLOR_OPS_1MIN}">近1分 </span>'
            f'<span style="color:{_COLOR_OPS_1MIN};font-weight:700">{ops_1min}</span>'
        )
    parts.append(
        f'<span style="color:{_COLOR_OPS}">总操作 </span>'
        f'<span style="color:{_COLOR_OPS};font-weight:700">{total_ops:,}</span>'
    )
    return f'<span style="{_font(11)}">' + _muted_sep().join(parts) + "</span>"


def format_global_summary_html(
    total_ops: int,
    gold: float,
    diamond: float,
    *,
    ops_1min: int | None = None,
) -> str:
    """兼容旧调用：操作 + 金/钻文字（现顶栏金/钻已改滚轮）。"""
    base = format_ops_summary_html(total_ops, ops_1min=ops_1min)
    extra = (
        f'{_muted_sep()}'
        f'<span style="color:{_COLOR_GOLD}">金币 </span>'
        f'<span style="color:{_COLOR_GOLD};font-weight:700">{format_amount(gold)}</span>'
        f'{_muted_sep()}'
        f'<span style="color:{_COLOR_DIAM}">钻石 </span>'
        f'<span style="color:{_COLOR_DIAM};font-weight:700">'
        f"{format_amount(diamond)}</span>"
    )
    return base[:-7] + extra + "</span>" if base.endswith("</span>") else base + extra


def format_roll_history_line_html(
    entry: "RollHistoryEntry",
    *,
    compact: bool = True,
) -> str:
    """单条开奖历史（RichText）。"""
    op = f'<span style="color:{_COLOR_MUTED}">#{entry.op_at}</span>'
    if not entry.hit:
        miss = "-" if compact else "未中奖"
        return f'{op} <span style="color:{_COLOR_MUTED}">{miss}</span>'
    reward_parts: list[str] = []
    if entry.gold:
        gold_label = "金" if compact else "金币"
        if entry.gold_crit_mult > 1.0:
            reward_parts.append(
                _crit_formula_html(
                    entry.gold,
                    entry.gold_crit_mult,
                    gold_label,
                    _COLOR_GOLD,
                    _COLOR_WARN,
                    spaced=not compact,
                )
            )
        else:
            reward_parts.append(
                _plain_amount_html(
                    entry.gold, gold_label, _COLOR_GOLD, spaced=not compact
                )
            )
    if entry.diamond:
        diam_label = "钻" if compact else "钻石"
        if entry.diamond_crit_mult > 1.0:
            reward_parts.append(
                _crit_formula_html(
                    entry.diamond,
                    entry.diamond_crit_mult,
                    diam_label,
                    _COLOR_DIAM,
                    _COLOR_WARN,
                    spaced=not compact,
                )
            )
        else:
            reward_parts.append(
                _plain_amount_html(
                    entry.diamond, diam_label, _COLOR_DIAM, spaced=not compact
                )
            )
    gap = " " if compact else f' <span style="color:{_COLOR_MUTED}">·</span> '
    return f"{op} {gap.join(reward_parts)}"


def format_roll_history_lines_html(
    entries: Iterable["RollHistoryEntry"],
    *,
    limit: int | None = None,
    compact: bool = True,
) -> str:
    """悬浮窗开奖历史多行 HTML。"""
    items = list(entries)
    if limit is not None:
        items = items[:limit]
    if not items:
        return (
            f'<span style="{_font(10)}color:{_COLOR_MUTED}">'
            f"暂无开奖记录</span>"
        )
    lines = [
        format_roll_history_line_html(e, compact=compact) for e in items
    ]
    return f'<span style="{_font(10)}">' + "<br/>".join(lines) + "</span>"


def format_subgoal_runtime_html(sub: Subtask) -> str:
    """子目标运行时长（RichText）。"""
    runtime = format_duration(sub.active_seconds)
    if sub.done:
        return (
            f'<span style="color:{_COLOR_MUTED}">运行 </span>'
            f'<span style="color:{_COLOR_TIME};font-weight:700">{runtime}</span>'
        )
    target = format_duration(sub.target_seconds)
    runtime_color = _COLOR_OPS if sub.time_target_met() else _COLOR_TIME
    return (
        f'<span style="color:{_COLOR_MUTED}">运行 </span>'
        f'<span style="color:{runtime_color};font-weight:700">{runtime}</span>'
        f'<span style="color:{_COLOR_MUTED}"> / </span>'
        f'<span style="color:{_COLOR_MUTED}">{target}</span>'
    )


def format_widget_runtime_html(
    since_gold: float,
    since_diamond: float,
    duration: str = "",
    *,
    sub_duration: str = "",
) -> str:
    """悬浮窗目标区副行：上次获得 / 目标运行 / 子目标运行。"""
    parts: list[str] = []
    since_parts: list[str] = []
    if since_gold:
        since_parts.append(
            f'<span style="color:{_COLOR_GOLD};font-weight:700">'
            f"金币 {format_amount(since_gold)}</span>"
        )
    if since_diamond:
        since_parts.append(
            f'<span style="color:{_COLOR_DIAM};font-weight:700">'
            f"钻石 {format_amount(since_diamond)}</span>"
        )
    if since_parts:
        since_body = f'<span style="color:{_COLOR_MUTED}"> · </span>'.join(since_parts)
        parts.append(
            f'<span style="color:{_COLOR_MUTED}">上次 </span>{since_body}'
        )
    else:
        parts.append(
            f'<span style="color:{_COLOR_MUTED}">上次 </span>'
            f'<span style="color:{_COLOR_MUTED}">未获得</span>'
        )
    if duration:
        parts.append(
            f'<span style="color:{_COLOR_MUTED}">目标运行 </span>'
            f'<span style="color:{_COLOR_TIME};font-weight:700">'
            f"{_html_escape(duration)}</span>"
        )
    if sub_duration:
        parts.append(
            f'<span style="color:{_COLOR_MUTED}">目标 </span>'
            f'<span style="color:{_COLOR_TIME};font-weight:700">'
            f"{_html_escape(sub_duration)}</span>"
        )
    return f'<span style="{_font(12)}">' + _muted_sep().join(parts) + "</span>"


def format_timestamp_short(ts: float | None) -> str:
    """短日期时间（含年），用于详情条创建/完成。"""
    if ts is None:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _format_created_completed_html(
    created_at: float | None,
    completed_at: float | None,
) -> list[str]:
    """详情条可拼接的「创建 / 完成」片段（未完成则无完成项）。"""
    parts: list[str] = []
    created = format_timestamp_short(created_at)
    if created:
        parts.append(
            f'<span style="color:{_COLOR_MUTED}">创建 </span>'
            f'<span style="color:{_COLOR_TIME};font-weight:700">{created}</span>'
        )
    completed = format_timestamp_short(completed_at)
    if completed:
        parts.append(
            f'<span style="color:{_COLOR_MUTED}">完成 </span>'
            f'<span style="color:{_COLOR_TIME};font-weight:700">{completed}</span>'
        )
    return parts


def format_subgoals_focus_hint_html(active: Task) -> str:
    """有未完成叶子子目标但未聚焦时的提示。"""
    if not active.subtasks:
        return ""
    if active.current_subtask() is not None:
        return ""
    leaves = list(active.iter_leaves())
    if not leaves or all(s.done for s in leaves):
        return ""
    return (
        f'<span style="{_font(12)}color:{_COLOR_WARN};font-weight:600">'
        f"未聚焦目标，奖励暂停累计</span>"
    )


def format_goal_root_line_html(
    task: Task,
    *,
    suffix: str = "",
    selected: bool = False,
    is_running: bool = False,
    muted: bool = False,
) -> str:
    """目录树根节点标题行：目标标题 + 子项完成数量。"""
    title = _html_escape(task.title)
    if is_running:
        weight = "800"
        color = _COLOR_TITLE_BRIGHT
        prefix = f'<span style="color:{_COLOR_CURRENT};font-weight:700">● </span>'
    elif selected:
        weight = "800"
        color = _COLOR_TITLE_BRIGHT
        prefix = ""
    elif muted:
        weight = "600"
        color = _COLOR_PAUSED
        prefix = ""
    else:
        weight = "700"
        color = _COLOR_TEXT
        prefix = ""
    parts = [
        prefix
        + f'<span style="{_font(13)}color:{color};font-weight:{weight}">{title}</span>',
    ]
    if task.subtasks:
        done, total = task.subtask_progress()
        parts.append(
            f'<span style="color:{_COLOR_MUTED};font-weight:600">'
            f"({done}/{total})</span>"
        )
    if suffix:
        parts.append(
            f'<span style="color:{_COLOR_MUTED};font-weight:600">'
            f"{_html_escape(suffix)}</span>"
        )
    return "  ".join(parts)


def _format_inline_stats_html(operations: int, active_seconds: float) -> str:
    """树行标题后的紧凑统计：仅操作与运行时间。"""
    duration = format_duration(active_seconds)
    return (
        f'  <span style="color:{_COLOR_MUTED};font-weight:500">'
        f"操作 {operations} · 运行 {duration}</span>"
    )


def format_tree_node_html(
    sub: Subtask,
    *,
    selected: bool = False,
    is_current: bool = False,
    expanded: bool = True,
    show_stats: bool = False,
) -> str:
    """VS Code 风紧凑树行（标题 + 可选行内统计）。"""
    title = _html_escape(sub.title)
    if selected:
        title_color = _COLOR_TITLE_BRIGHT
        title_weight = "700"
    elif is_current and not sub.done:
        title_color = _COLOR_TITLE_BRIGHT
        title_weight = "600"
    elif sub.is_claimable() or sub.can_claim_pending():
        title_color = _COLOR_CLAIM
        title_weight = "600"
    else:
        title_color = _COLOR_TEXT
        title_weight = "500"

    if sub.is_container():
        leaves = [s for s in sub.iter_subtree() if s.is_leaf() and s.id != sub.id]
        done = sum(1 for s in leaves if s.done)
        total = len(leaves)
        ops = sub.rollup_operations()
        secs = sub.rollup_active_seconds()
        prefix = ""
        if is_current and not sub.done:
            prefix = f'<span style="color:{_COLOR_CURRENT};font-weight:700">● </span>'
        inner = (
            f"{prefix}"
            f'<span style="color:{title_color};font-weight:{title_weight};">{title}</span>  '
            f'<span style="color:{_COLOR_MUTED}">({done}/{total})</span>'
        )
        if show_stats or ops or secs:
            inner += _format_inline_stats_html(ops, secs)
        return f'<span style="{_font(13)}">{inner}</span>'

    if sub.done:
        marker = "●" if sub.is_claimable() else "✓"
    elif is_current:
        marker = "●"
    else:
        marker = "○"
    marker_color = _COLOR_CURRENT if is_current and not sub.done else (
        _COLOR_CLAIM if (sub.is_claimable() or sub.can_claim_pending()) else _COLOR_MARKER
    )
    inner = (
        f'<span style="color:{marker_color};font-weight:700">{marker}</span> '
        f'<span style="color:{title_color};font-weight:{title_weight}">{title}</span>'
    )
    has_stats = bool(sub.operations or sub.active_seconds)
    if show_stats or sub.done or has_stats:
        if has_stats or sub.done:
            inner += _format_inline_stats_html(sub.operations, sub.active_seconds)
    return f'<span style="{_font(13)}">{inner}</span>'


def format_tree_detail_html(
    task: Task,
    sub: Subtask | None,
    *,
    since_roll_gold: float = 0.0,
    since_roll_diamond: float = 0.0,
    completion_bonus: float = 0.5,
    omit_compact_currency: bool = False,
) -> str:
    """详情面板统计文案。"""
    if sub is None:
        gold, diamond = task.earned_totals()
        accum_parts = [
            f'<span style="color:{_COLOR_MUTED}">累计 '
            f'{format_duration(task.active_duration_seconds())}</span>',
        ]
        accum_parts.extend(
            _format_created_completed_html(task.created_at, task.completed_at)
        )
        if omit_compact_currency:
            lines = ["  ".join(accum_parts)]
        else:
            lines = [
                format_goal_compact_html(task.rollup_operations(), gold, diamond),
                "  ".join(accum_parts),
            ]
        if task.status == TaskStatus.ACTIVE:
            lines.append(
                format_widget_runtime_html(
                    since_roll_gold,
                    since_roll_diamond,
                    "",
                )
            )
        return "<br>".join(lines)

    if sub.is_container():
        ops = sub.rollup_operations()
        gold, diamond = sub.rollup_earned()
        pending = sub.rollup_pending_summary()
        parts = [f'<span style="color:{_COLOR_MUTED}">分组</span>']
        if ops or gold or pending.gold or pending.diamond:
            if not omit_compact_currency:
                parts.append(format_goal_compact_html(ops, gold, diamond))
            if pending.gold or pending.diamond:
                parts.append(
                    f'<span style="color:{_COLOR_CLAIM};font-weight:700">'
                    f"待领 金{format_amount(pending.gold)} 钻{format_amount(pending.diamond)}</span>"
                )
        return "  ".join(parts)

    if omit_compact_currency:
        parts = [format_subgoal_runtime_html(sub)]
    else:
        parts = [
            format_goal_compact_html(sub.operations, sub.earned_gold, sub.earned_diamond),
            format_subgoal_runtime_html(sub),
        ]
    parts.extend(_format_created_completed_html(sub.created_at, sub.completed_at))
    if sub.can_finish():
        pending = sub.pending_summary()
        parts.append(
            f'<span style="color:{_COLOR_CLAIM};font-weight:700">'
            f"完成可领 金{format_amount(pending.gold + completion_bonus)} "
            f"钻{format_amount(pending.diamond)}</span>"
        )
    elif sub.can_claim_pending():
        pending = sub.pending_summary()
        parts.append(
            f'<span style="color:{_COLOR_CLAIM};font-weight:700">'
            f"待领 金{format_amount(pending.gold)} 钻{format_amount(pending.diamond)}</span>"
        )
    return "  ".join(parts)


def format_goal_ops_html(operations: int) -> str:
    """详情操作数（金/钻由 RollingAmount 绘制）。"""
    return (
        f'<span style="{_font(13)}">'
        f'<span style="color:{_COLOR_OPS};font-weight:700">操作 {operations}</span>'
        f"</span>"
    )


def format_goal_compact_html(operations: int, gold: float, diamond: float) -> str:
    """悬浮窗父目标紧凑统计一行。"""
    parts = [
        f'<span style="color:{_COLOR_OPS};font-weight:700">操作 {operations}</span>',
        f'<span style="color:{_COLOR_GOLD};font-weight:700">金 {format_amount(gold)}</span>',
        f'<span style="color:{_COLOR_DIAM if diamond else _COLOR_MUTED};font-weight:700">'
        f"钻 {format_amount(diamond)}</span>",
    ]
    sep = f'<span style="color:{_COLOR_MUTED}"> · </span>'
    return f'<span style="{_font(13)}">' + sep.join(parts) + "</span>"


def format_amount(value: float) -> str:
    """金额显示：最多 1 位小数，整数不带 .0。"""
    v = round(float(value), 1)
    if abs(v - int(v)) < 1e-9:
        return str(int(v))
    return f"{v:.1f}"


def format_reward_gain(gold: float, diamond: float) -> str:
    """完成任务时的奖励说明。"""
    parts = []
    if gold:
        parts.append(f"{format_amount(gold)} 金币")
    if diamond:
        parts.append(f"{format_amount(diamond)} 钻石")
    if not parts:
        return "无奖励"
    return "、".join(parts)


def format_roll_history_line(
    entry: "RollHistoryEntry",
    *,
    include_time: bool = False,
    compact: bool = False,
) -> str:
    """单条开奖历史。"""
    op = f"#{entry.op_at}"
    if not entry.hit:
        text = f"{op} -" if compact else f"{op}  未中奖"
    elif compact:
        parts = []
        if entry.gold:
            parts.append(
                _crit_formula_plain(
                    entry.gold, entry.gold_crit_mult, "金", spaced=False
                )
            )
        if entry.diamond:
            parts.append(
                _crit_formula_plain(
                    entry.diamond, entry.diamond_crit_mult, "钻", spaced=False
                )
            )
        text = f"{op} {' '.join(parts)}"
    else:
        parts = []
        if entry.gold:
            parts.append(
                _crit_formula_plain(
                    entry.gold, entry.gold_crit_mult, "金币", spaced=True
                )
            )
        if entry.diamond:
            parts.append(
                _crit_formula_plain(
                    entry.diamond, entry.diamond_crit_mult, "钻石", spaced=True
                )
            )
        text = f"{op}  {' · '.join(parts)}"
    if include_time:
        ts = time.strftime("%m-%d %H:%M", time.localtime(entry.at))
        return f"{ts}  {text}"
    return text