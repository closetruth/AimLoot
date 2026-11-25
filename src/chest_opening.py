"""皇室战争式开宝箱:解锁倒计时 + 概率开出字母收藏与货币。

纯逻辑模块,不依赖 Qt。所有函数接受 state / chest 操作,或纯 RNG 生成结果
(可传 rng=random.Random(seed) 以便测试确定性)。
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .models import AppState, ChestItem

# 各稀有度解锁时长(秒):普通 30 分 / 罕见 1 小时 / 稀有 2 小时 / 史诗 4 小时 / 传奇 8 小时
UNLOCK_SPANS = (1800, 3600, 7200, 14400, 28800)
MAX_UNLOCK_SLOTS = 4

# 字母稀有度权重:行=宝箱稀有度,列=字母稀有度(普通..传奇)。
# 宝箱越稀有,开出高稀有度字母的分布越偏向高档。
LETTER_RARITY_WEIGHTS = (
    (70, 20, 7, 2, 1),   # 普通箱
    (50, 28, 15, 5, 2),  # 罕见箱
    (30, 30, 25, 12, 3), # 稀有箱
    (15, 25, 30, 22, 8), # 史诗箱
    (5, 12, 28, 30, 25), # 传奇箱
)

# 字母数量:几何分布 p=0.5,0-∞ 右偏(P(1)=50%, P(2)=25%, P(3)=12.5%…,均值 2)
GEOMETRIC_P = 0.5
LETTER_COUNT_MAX = 20  # 数量截断(21+ 概率 ~1e-6,可忽略)

# 伴生货币:命中概率 + 命中后的对数正态均值(0-∞ 右偏,无上限)
GOLD_HIT_P = 0.50
DIAMOND_HIT_P = 0.20
GOLD_MEAN = (2.0, 4.0, 7.0, 12.0, 18.0)      # 普通..传奇
DIAMOND_MEAN = (0.2, 0.4, 0.9, 2.0, 4.0)
CURRENCY_SIGMA = 0.6                          # 对数正态 σ(形状参数)


@dataclass
class OpenResult:
    """一次开箱的结果(已由概率分布生成,提交前可预览)。"""
    letters: List[Tuple[str, int]] = field(default_factory=list)  # (字母, 稀有度)
    gold: float = 0.0
    diamond: float = 0.0


def unlock_span_seconds(rarity: int) -> int:
    rarity = max(0, min(4, int(rarity)))
    return UNLOCK_SPANS[rarity]


def is_ready(chest: ChestItem, now: Optional[float] = None) -> bool:
    """解锁倒计时是否结束。"""
    if chest.unlock_started_at is None:
        return False
    now = time.time() if now is None else now
    return now - chest.unlock_started_at >= unlock_span_seconds(chest.rarity)


def remaining_seconds(chest: ChestItem, now: Optional[float] = None) -> int:
    """剩余解锁秒数(向上取整);未解锁按完整时长计,已就绪为 0。"""
    span = unlock_span_seconds(chest.rarity)
    if chest.unlock_started_at is None:
        return span
    now = time.time() if now is None else now
    return max(0, int(-(-(chest.unlock_started_at + span - now) // 1)))


def unlocking_chests(state: AppState, now: Optional[float] = None) -> List[ChestItem]:
    """解锁中(已开始、未就绪)的箱子。"""
    return [
        c for c in state.inventory.chests
        if c.unlock_started_at is not None and not is_ready(c, now=now)
    ]


def ready_chests(state: AppState, now: Optional[float] = None) -> List[ChestItem]:
    """倒计时结束、可开箱的箱子。"""
    return [c for c in state.inventory.chests if is_ready(c, now=now)]


def locked_chests(state: AppState) -> List[ChestItem]:
    """未开始解锁的箱子。"""
    return [c for c in state.inventory.chests if c.unlock_started_at is None]


def slots_available(state: AppState, now: Optional[float] = None) -> bool:
    """是否有空解锁槽位(最多 MAX_UNLOCK_SLOTS 个同时解锁)。"""
    return len(unlocking_chests(state, now=now)) < MAX_UNLOCK_SLOTS


def start_unlock(state: AppState, chest: ChestItem, now: Optional[float] = None) -> bool:
    """开始解锁一个未解锁的箱子;槽位已满或已在解锁/已就绪则失败。"""
    if chest not in state.inventory.chests:
        return False
    if chest.unlock_started_at is not None:
        return False
    if not slots_available(state, now=now):
        return False
    chest.unlock_started_at = time.time() if now is None else now
    return True


def _weighted_rarity(weights: Tuple[int, int, int, int, int], rng: random.Random) -> int:
    total = sum(weights)
    roll = rng.randrange(total)
    acc = 0
    for r, w in enumerate(weights):
        acc += w
        if roll < acc:
            return r
    return len(weights) - 1


def _unbounded_right_skewed(mean: float, rng: random.Random, sigma: float = CURRENCY_SIGMA) -> float:
    """对数正态右偏，0-∞ 无上限，期望≈mean。

    E[X] = e^(μ+σ²/2) = mean → μ = ln(mean) − σ²/2。
    众数 ≈ 0.7·mean，中位数 ≈ 0.84·mean；P(X > 10·mean) ≈ 0.02%（彩蛋尾值）。
    """
    mu = math.log(max(mean, 1e-9)) - sigma * sigma / 2
    return rng.lognormvariate(mu, sigma)


def generate_open_result(rarity: int, rng: Optional[random.Random] = None) -> OpenResult:
    """按概率分布生成一次开箱结果(纯 RNG,不修改任何状态)。

    - 字母数量:几何分布 p=0.5,0-∞ 右偏(1 个最常见)
    - 每个字母独立按权重表抽稀有度;本轮字母不重复(26 池内)
    - 伴生货币:金币/钻石各自独立按概率判断命中,命中后对数正态右偏抽金额(0-∞)
    """
    rng = rng if rng is not None else random.Random()
    rarity = max(0, min(4, int(rarity)))
    weights = LETTER_RARITY_WEIGHTS[rarity]

    count = 1
    while rng.random() >= GEOMETRIC_P and count < LETTER_COUNT_MAX:
        count += 1

    pool = [chr(ord("A") + i) for i in range(26)]
    rng.shuffle(pool)

    letters: List[Tuple[str, int]] = []
    for _ in range(count):
        rar = _weighted_rarity(weights, rng)
        letters.append((pool[len(letters)], rar))

    gold = round(_unbounded_right_skewed(GOLD_MEAN[rarity], rng), 2) if rng.random() < GOLD_HIT_P else 0.0
    diamond = round(_unbounded_right_skewed(DIAMOND_MEAN[rarity], rng), 2) if rng.random() < DIAMOND_HIT_P else 0.0

    return OpenResult(letters=letters, gold=gold, diamond=diamond)


def open_chest(state: AppState, chest: ChestItem, result: OpenResult) -> None:
    """提交开箱:移除宝箱、入账字母与货币。结果由调用方先 generate_open_result 生成。"""
    if chest in state.inventory.chests:
        state.inventory.chests.remove(chest)
    for letter, rar in result.letters:
        state.inventory.add_letter(letter, rar)
    state.inventory.gold += result.gold
    state.inventory.diamond += result.diamond


# 加速/秒开：约每 5 分钟 1 金币
SPEEDUP_SECONDS_PER_GOLD = 300


def speedup_gold_cost(remaining_seconds: int) -> int:
    """按剩余解锁秒数折算金币；已就绪为 0。"""
    rem = max(0, int(remaining_seconds))
    if rem <= 0:
        return 0
    return max(1, int(math.ceil(rem / float(SPEEDUP_SECONDS_PER_GOLD))))


def finish_unlock(chest: ChestItem, now: Optional[float] = None) -> bool:
    """将箱子标记为已就绪（回拨 unlock_started_at）。不检查背包归属。"""
    now = time.time() if now is None else now
    span = unlock_span_seconds(chest.rarity)
    chest.unlock_started_at = float(now) - float(span)
    return True


def try_speedup(
    state: AppState,
    chest: ChestItem,
    now: Optional[float] = None,
) -> Tuple[bool, str]:
    """花金币将未就绪箱子立刻变为可开箱。已就绪则失败。"""
    if chest not in state.inventory.chests:
        return False, "宝箱不存在"
    now = time.time() if now is None else now
    if is_ready(chest, now=now):
        return False, "宝箱已可开箱"
    rem = remaining_seconds(chest, now=now)
    cost = speedup_gold_cost(rem)
    if cost <= 0:
        return False, "宝箱已可开箱"
    if state.inventory.gold + 1e-9 < cost:
        return False, f"金币不足（需要 {cost}）"
    state.inventory.gold = round(state.inventory.gold - cost, 2)
    finish_unlock(chest, now=now)
    return True, f"已加速，花费 {cost} 金币"


def try_instant_open(
    state: AppState,
    chest: ChestItem,
    now: Optional[float] = None,
    rng: Optional[random.Random] = None,
) -> Tuple[bool, str, Optional[OpenResult]]:
    """花金币（若未就绪）立刻开箱入账；不占解锁槽。已就绪时费用为 0。"""
    ok, msg, opens = try_instant_open_n(
        state, chest.rarity, 1, now=now, rng=rng, chests=[chest],
    )
    if not ok:
        return False, msg, None
    if not opens:
        return False, msg or "开箱失败", None
    return True, msg, opens[0][1]


def not_ready_chests(
    state: AppState,
    rarity: int,
    now: Optional[float] = None,
) -> List[ChestItem]:
    """指定稀有度下尚未可开箱的箱子（待解锁或解锁中）。"""
    now = time.time() if now is None else now
    rarity = max(0, min(4, int(rarity)))
    return [
        c for c in state.inventory.chests
        if c.rarity == rarity and not is_ready(c, now=now)
    ]


def instant_open_total_cost(
    chests: List[ChestItem],
    now: Optional[float] = None,
) -> int:
    """多箱秒开总金币。"""
    now = time.time() if now is None else now
    return sum(
        speedup_gold_cost(remaining_seconds(c, now=now))
        for c in chests
    )


def try_instant_open_n(
    state: AppState,
    rarity: int,
    count: int,
    now: Optional[float] = None,
    rng: Optional[random.Random] = None,
    chests: Optional[List[ChestItem]] = None,
) -> Tuple[bool, str, List[Tuple[int, OpenResult]]]:
    """秒开指定稀有度下最多 count 只未就绪箱；先校验总费用再逐只开。

    chests 若传入则只开该列表（仍须属于本背包）；否则取该稀有度前 count 只未就绪。
    """
    now = time.time() if now is None else now
    count = max(0, int(count))
    if count <= 0:
        return False, "数量无效", []
    if chests is None:
        targets = not_ready_chests(state, rarity, now=now)[:count]
    else:
        targets = []
        for c in chests:
            if c not in state.inventory.chests:
                return False, "宝箱不存在", []
            if is_ready(c, now=now):
                continue
            targets.append(c)
            if len(targets) >= count:
                break
    if not targets:
        return False, "没有可秒开的宝箱", []
    total_cost = instant_open_total_cost(targets, now=now)
    if total_cost > 0 and state.inventory.gold + 1e-9 < total_cost:
        return False, f"金币不足（需要 {total_cost}）", []
    if total_cost > 0:
        state.inventory.gold = round(state.inventory.gold - total_cost, 2)
    opens: List[Tuple[int, OpenResult]] = []
    for chest in targets:
        rar = chest.rarity
        result = generate_open_result(rar, rng=rng)
        open_chest(state, chest, result)
        opens.append((rar, result))
    n = len(opens)
    if total_cost > 0:
        msg = f"秒开 {n} 个，花费 {total_cost} 金币"
    else:
        msg = f"开箱 {n} 个"
    return True, msg, opens



def open_all_ready(
    state: AppState,
    now: Optional[float] = None,
    rng: Optional[random.Random] = None,
) -> List[Tuple[int, OpenResult]]:
    """打开全部已就绪宝箱（无动画、无额外金币）。"""
    now = time.time() if now is None else now
    out: List[Tuple[int, OpenResult]] = []
    for chest in list(ready_chests(state, now=now)):
        rarity = chest.rarity
        result = generate_open_result(rarity, rng=rng)
        open_chest(state, chest, result)
        out.append((rarity, result))
    return out