"""数据模型 - Task / Reward / AppState。"""
from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    ACTIVE = "active"        # 进行中
    PAUSED = "paused"        # 已暂停
    COMPLETED = "completed"  # 已完成


@dataclass
class Reward:
    """单次奖励掉落记录。"""
    gold: float = 0.0
    diamond: float = 0.0
    # 触发时所属的操作计数，便于排序回顾
    op_at: int = 0
    gold_crit_mult: float = 1.0
    diamond_crit_mult: float = 1.0

    def is_empty(self) -> bool:
        return self.gold == 0 and self.diamond == 0

    def gold_is_crit(self) -> bool:
        return self.gold > 0 and self.gold_crit_mult > 1.0

    def diamond_is_crit(self) -> bool:
        return self.diamond > 0 and self.diamond_crit_mult > 1.0

    def has_crit(self) -> bool:
        return self.gold_is_crit() or self.diamond_is_crit()


LEGACY_TITLE_SUFFIX = "（原进度）"


@dataclass
class Subtask:
    """目标下的子项（可嵌套）：叶子时长达标后点完成，奖励同时进背包。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = ""
    target_seconds: float = 600.0
    active_seconds: float = 0.0
    operations: int = 0
    earned_gold: float = 0.0
    earned_diamond: float = 0.0
    pending_rewards: List[Reward] = field(default_factory=list)
    done: bool = False
    rewards_claimed: bool = False
    is_legacy: bool = False
    created_at: Optional[float] = field(default_factory=time.time)
    completed_at: Optional[float] = None
    children: List["Subtask"] = field(default_factory=list)

    def is_leaf(self) -> bool:
        return not self.children

    def is_container(self) -> bool:
        return bool(self.children)

    def iter_subtree(self) -> Iterator["Subtask"]:
        yield self
        for child in self.children:
            yield from child.iter_subtree()

    def find_by_id(self, subtask_id: str) -> Optional["Subtask"]:
        if self.id == subtask_id:
            return self
        for child in self.children:
            found = child.find_by_id(subtask_id)
            if found is not None:
                return found
        return None

    def pending_summary(self) -> Reward:
        total = Reward()
        for r in self.pending_rewards:
            total.gold += r.gold
            total.diamond += r.diamond
        return total

    def is_claimable(self) -> bool:
        return self.is_leaf() and self.done and not self.rewards_claimed

    def can_claim_pending(self) -> bool:
        """叶子上有 pending 且未领取。"""
        return (
            self.is_leaf()
            and bool(self.pending_rewards)
            and not self.rewards_claimed
        )

    def time_target_met(self) -> bool:
        return self.active_seconds >= self.target_seconds

    def is_legacy_progress(self) -> bool:
        return self.is_legacy or self.title.endswith(LEGACY_TITLE_SUFFIX)

    def can_finish(self) -> bool:
        """时长达标且尚未领完（含旧档已完成未领取）。"""
        if not self.is_leaf() or self.rewards_claimed:
            return False
        if self.done:
            return True
        return self.time_target_met()

    def rollup_operations(self) -> int:
        """文件夹式汇总：叶子返回自身，分组返回子孙叶子合计。"""
        if self.is_leaf():
            return self.operations
        return sum(c.rollup_operations() for c in self.children)

    def rollup_earned(self) -> tuple[float, float]:
        if self.is_leaf():
            return self.earned_gold, self.earned_diamond
        gold = diamond = 0.0
        for child in self.children:
            cg, cd = child.rollup_earned()
            gold += cg
            diamond += cd
        return gold, diamond

    def rollup_active_seconds(self) -> float:
        if self.is_leaf():
            return self.active_seconds
        return sum(c.rollup_active_seconds() for c in self.children)

    def rollup_pending_summary(self) -> Reward:
        if self.is_leaf():
            return self.pending_summary()
        total = Reward()
        for child in self.children:
            sub = child.rollup_pending_summary()
            total.gold += sub.gold
            total.diamond += sub.diamond
        return total

    @classmethod
    def from_dict(cls, data: Dict) -> "Subtask":
        pending = [Reward(**r) for r in data.get("pending_rewards", [])]
        if "target_seconds" in data:
            target_seconds = float(data["target_seconds"])
        elif "target_ops" in data:
            target_seconds = max(60.0, float(data["target_ops"]) * 60.0)
            logger.debug("Subtask.from_dict: 旧版 target_ops 迁移 → target_seconds=%.0f", target_seconds)
        else:
            target_seconds = 600.0
        children = [cls.from_dict(c) for c in data.get("children", [])]
        title = data.get("title", "")
        is_legacy = bool(data.get("is_legacy", False)) or str(title).endswith(
            LEGACY_TITLE_SUFFIX
        )
        return cls(
            id=data.get("id", uuid.uuid4().hex[:8]),
            title=title,
            target_seconds=max(1.0, target_seconds),
            active_seconds=float(data.get("active_seconds", 0)),
            operations=int(data.get("operations", 0)),
            earned_gold=float(data.get("earned_gold", 0)),
            earned_diamond=float(data.get("earned_diamond", 0)),
            pending_rewards=pending,
            done=bool(data.get("done", False)),
            rewards_claimed=bool(data.get("rewards_claimed", False)),
            is_legacy=is_legacy,
            created_at=data.get("created_at"),
            completed_at=data.get("completed_at"),
            children=children,
        )

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class Task:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    note: str = ""
    status: TaskStatus = TaskStatus.ACTIVE
    subtasks: List[Subtask] = field(default_factory=list)
    current_subtask_id: Optional[str] = None

    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    # 任务进行期间累计的操作数 (仅 active 时累加)
    operations: int = 0
    earned_gold: float = 0.0
    earned_diamond: float = 0.0

    # 仅「进行中」状态的累计时长（秒）；悬浮窗定时器每 1s 触发时 +1
    active_seconds: float = 0.0

    # 待领取奖励：完成任务后会进入用户背包
    pending_rewards: List[Reward] = field(default_factory=list)

    # 完成任务时领取的奖励合计（写入背包后保留记录）
    completed_reward_gold: float = 0.0
    completed_reward_diamond: float = 0.0

    def active_duration_seconds(self) -> float:
        """进行中累计秒数（暂停与关屏不计）；有子树时为子孙叶子合计。"""
        return self.rollup_active_seconds()

    def rollup_operations(self) -> int:
        """文件夹式汇总：无子树时返回自身，否则为根级子树合计。"""
        if not self.subtasks:
            return self.operations
        return sum(s.rollup_operations() for s in self.subtasks)

    def rollup_earned(self) -> tuple[float, float]:
        if not self.subtasks:
            return self.earned_gold, self.earned_diamond
        gold = diamond = 0.0
        for sub in self.subtasks:
            sg, sd = sub.rollup_earned()
            gold += sg
            diamond += sd
        return gold, diamond

    def rollup_active_seconds(self) -> float:
        if not self.subtasks:
            return self.active_seconds
        return sum(s.rollup_active_seconds() for s in self.subtasks)

    def pending_summary(self) -> Reward:
        total = Reward()
        for r in self.pending_rewards:
            total.gold += r.gold
            total.diamond += r.diamond
        return total

    def find_subtask(self, subtask_id: str) -> Optional[Subtask]:
        for root in self.subtasks:
            if root.id == subtask_id:
                return root
            found = root.find_by_id(subtask_id)
            if found is not None:
                return found
        return None

    def iter_subtasks(self) -> Iterator[Subtask]:
        for root in self.subtasks:
            yield from root.iter_subtree()

    def iter_leaves(self) -> Iterator[Subtask]:
        for sub in self.iter_subtasks():
            if sub.is_leaf():
                yield sub

    def flatten_subtasks(self) -> List[Tuple[int, Subtask]]:
        """先序遍历，返回 (深度, 子目标)。"""
        flat: List[Tuple[int, Subtask]] = []

        def walk(nodes: List[Subtask], depth: int) -> None:
            for node in nodes:
                flat.append((depth, node))
                walk(node.children, depth + 1)

        walk(self.subtasks, 0)
        return flat

    def subtask_path_ids(self, subtask_id: str) -> List[str]:
        """从根到目标的 id 链（含目标自身）。"""
        path: List[str] = []

        def walk(nodes: List[Subtask]) -> bool:
            for node in nodes:
                path.append(node.id)
                if node.id == subtask_id:
                    return True
                if walk(node.children):
                    return True
                path.pop()
            return False

        if walk(self.subtasks):
            return path
        return []

    def active_focus_path_ids(self) -> frozenset[str]:
        """进行中且已聚焦叶子时，从根到叶的整条路径 id（含分组与叶子）。"""
        if self.status != TaskStatus.ACTIVE or not self.current_subtask_id:
            return frozenset()
        path = self.subtask_path_ids(self.current_subtask_id)
        return frozenset(path) if path else frozenset()

    def iter_visible_subtasks(
        self,
        expanded_ids: set[str],
    ) -> Iterator[Tuple[int, Subtask]]:
        """按展开状态遍历可见子目标（顶层始终可见）。"""

        def walk(nodes: List[Subtask], depth: int) -> Iterator[Tuple[int, Subtask]]:
            for node in sorted(
                nodes, key=lambda s: float(s.created_at or 0), reverse=True
            ):
                yield depth, node
                if node.is_container() and node.id in expanded_ids:
                    yield from walk(node.children, depth + 1)

        yield from walk(self.subtasks, 0)

    def subtask_path_titles(self, subtask_id: str) -> List[str]:
        path: List[str] = []

        def walk(nodes: List[Subtask]) -> bool:
            for node in nodes:
                path.append(node.title)
                if node.id == subtask_id:
                    return True
                if walk(node.children):
                    return True
                path.pop()
            return False

        if walk(self.subtasks):
            return path
        return []

    def subtask_progress(self) -> tuple[int, int]:
        """返回叶子子目标的 (已完成数, 总数)。"""
        leaves = list(self.iter_leaves())
        total = len(leaves)
        done = sum(1 for s in leaves if s.done)
        return done, total

    def earned_totals(self) -> tuple[float, float]:
        """展示用累计奖励（文件夹式 rollup）。"""
        return self.rollup_earned()

    def current_subtask(self) -> Optional[Subtask]:
        """当前聚焦的叶子子目标（仅 current_subtask_id 指向的未完成叶子）。"""
        if not self.current_subtask_id:
            return None
        sub = self.find_subtask(self.current_subtask_id)
        if sub is None or sub.done or not sub.is_leaf():
            return None
        return sub

    def has_unclaimed_subtasks(self) -> bool:
        """叶子未完成，或仍有 pending / 未领取完成奖时，不可完成父目标。"""
        for s in self.iter_leaves():
            if not s.done:
                return True
            if s.rewards_claimed:
                continue
            if s.is_claimable() or s.pending_rewards:
                return True
        return False

    def current_subtask_pending(self) -> Reward:
        sub = self.current_subtask()
        if sub is None:
            return Reward()
        return sub.pending_summary()

    def can_complete_sub(self, sub: Subtask) -> bool:
        return sub.is_leaf() and sub.time_target_met()

    @classmethod
    def from_dict(cls, data: Dict) -> "Task":
        rewards = [Reward(**r) for r in data.get("pending_rewards", [])]
        subtasks = [Subtask.from_dict(s) for s in data.get("subtasks", [])]
        task = cls(
            id=data.get("id", uuid.uuid4().hex[:12]),
            title=data.get("title", ""),
            note=data.get("note", ""),
            status=TaskStatus(data.get("status", "active")),
            subtasks=subtasks,
            current_subtask_id=data.get("current_subtask_id"),
            created_at=data.get("created_at", time.time()),
            completed_at=data.get("completed_at"),
            operations=data.get("operations", 0),
            earned_gold=float(data.get("earned_gold", 0)),
            earned_diamond=float(data.get("earned_diamond", 0)),
            active_seconds=float(data.get("active_seconds", 0)),
            pending_rewards=rewards,
            completed_reward_gold=float(data.get("completed_reward_gold", 0)),
            completed_reward_diamond=float(data.get("completed_reward_diamond", 0)),
        )
        return task

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class ChestItem:
    """未开宝箱（缓动条领取进背包；解锁倒计时后开箱兑奖）。"""
    rarity: int = 0
    obtained_at: float = field(default_factory=time.time)
    unlock_started_at: Optional[float] = None  # 点「解锁」时的时间戳；None = 未解锁

    @classmethod
    def from_dict(cls, data: Dict) -> "ChestItem":
        unlock_started = data.get("unlock_started_at")
        return cls(
            rarity=max(0, min(4, int(data.get("rarity", 0)))),
            obtained_at=float(data.get("obtained_at", time.time())),
            unlock_started_at=float(unlock_started) if unlock_started is not None else None,
        )

    def to_dict(self) -> Dict:
        d = {"rarity": int(self.rarity), "obtained_at": float(self.obtained_at)}
        if self.unlock_started_at is not None:
            d["unlock_started_at"] = float(self.unlock_started_at)
        return d


@dataclass
class Inventory:
    """玩家全局背包。"""
    gold: float = 0.0
    diamond: float = 0.00
    chests: List[ChestItem] = field(default_factory=list)
    # 字母收集：{"A": [普通, 罕见, 稀有, 史诗, 传奇 的计数]}，键为大写 A-Z
    letters: Dict[str, List[int]] = field(default_factory=dict)

    def add(self, reward: Reward) -> None:
        self.gold += reward.gold
        self.diamond += reward.diamond

    def add_chest(self, rarity: int) -> ChestItem:
        item = ChestItem(rarity=max(0, min(4, int(rarity))))
        self.chests.append(item)
        return item

    def chest_counts_by_rarity(self) -> Tuple[int, int, int, int, int]:
        counts = [0, 0, 0, 0, 0]
        for c in self.chests:
            r = max(0, min(4, int(c.rarity)))
            counts[r] += 1
        return (counts[0], counts[1], counts[2], counts[3], counts[4])

    def add_letter(self, letter: str, rarity: int) -> None:
        """累加一枚字母收藏（大写 A-Z，稀有度 0-4）。"""
        letter = str(letter).upper()
        if not ("A" <= letter <= "Z"):
            return
        rarity = max(0, min(4, int(rarity)))
        counts = self.letters.get(letter)
        if counts is None:
            counts = [0, 0, 0, 0, 0]
            self.letters[letter] = counts
        counts[rarity] += 1

    def letter_total(self, letter: str) -> int:
        """某字母的全部稀有度总数。"""
        counts = self.letters.get(str(letter).upper())
        return sum(counts) if counts else 0

    def letters_collected_count(self) -> int:
        """已收集的 (字母, 稀有度) 组合数，上限 26×5=130。"""
        total = 0
        for counts in self.letters.values():
            total += sum(1 for c in counts if c > 0)
        return total

    def letter_common_count(self, letter: str) -> int:
        """某字母的普通稀有度(rarity=0)数量。"""
        counts = self.letters.get(str(letter).upper())
        return counts[0] if counts else 0

    def consume_letter_common(self, letter: str) -> bool:
        """消耗 1 个普通稀有度字母，成功返回 True。"""
        letter = str(letter).upper()
        if not ("A" <= letter <= "Z"):
            return False
        counts = self.letters.get(letter)
        if counts is None or counts[0] <= 0:
            return False
        counts[0] -= 1
        return True

    def can_afford_letters(self, letter_counts: Dict[str, int]) -> bool:
        """检查一组字母的普通稀有度库存是否足够。"""
        for letter, need in letter_counts.items():
            if self.letter_common_count(letter) < need:
                return False
        return True

    def spend_letters(self, letter_counts: Dict[str, int]) -> bool:
        """批量消耗普通稀有度字母，全部成功返回 True，否则不消耗。"""
        if not self.can_afford_letters(letter_counts):
            return False
        for letter, need in letter_counts.items():
            for _ in range(need):
                self.consume_letter_common(letter)
        return True

    @classmethod
    def from_dict(cls, data: Dict) -> "Inventory":
        raw_chests = data.get("chests", [])
        chests: List[ChestItem] = []
        if isinstance(raw_chests, list):
            for item in raw_chests:
                if isinstance(item, dict):
                    chests.append(ChestItem.from_dict(item))
        raw_letters = data.get("letters", {})
        letters: Dict[str, List[int]] = {}
        if isinstance(raw_letters, dict):
            for key, val in raw_letters.items():
                if not (isinstance(key, str) and len(key) == 1 and "A" <= key.upper() <= "Z"):
                    continue
                key = key.upper()
                if isinstance(val, list):
                    counts = [0, 0, 0, 0, 0]
                    for i, v in enumerate(val[:5]):
                        try:
                            counts[i] = max(0, int(v))
                        except (TypeError, ValueError):
                            counts[i] = 0
                    letters[key] = counts
        return cls(
            gold=float(data.get("gold", 0)),
            diamond=float(data.get("diamond", 0)),
            chests=chests,
            letters=letters,
        )

    def to_dict(self) -> Dict:
        return {
            "gold": float(self.gold),
            "diamond": float(self.diamond),
            "chests": [c.to_dict() for c in self.chests],
            "letters": {k: [int(v) for v in vals] for k, vals in self.letters.items()},
        }


@dataclass
class EaseChestsState:
    """当前缓动条周期内终点宝箱的领取状态（防重启重复领）。"""
    cycle_id: int = 0
    claimed: Tuple[bool, ...] = (False,)
    holding: bool = False  # 满格停住、等点本轮箱子
    work_units: int = 0  # 本轮已计入的工作量（切叶子只加增量，不搬绝对值）
    seen_units: int = 0  # 上次见到的当前叶子 units
    seen_key: str = ""  # 上次见到的任务:叶子
    origin_units: int = 0  # 旧存档字段，不再用于计进度

    @classmethod
    def from_dict(cls, data: Dict) -> "EaseChestsState":
        raw = data.get("claimed", [False])
        if not isinstance(raw, (list, tuple)):
            raw = [False]
        if len(raw) >= 3:
            claimed = (bool(raw[2]),)
        elif len(raw) >= 1:
            claimed = (bool(raw[0]),)
        else:
            claimed = (False,)
        return cls(
            cycle_id=int(data.get("cycle_id", 0)),
            claimed=claimed,
            holding=bool(data.get("holding", False)),
            work_units=max(0, int(data.get("work_units", 0))),
            seen_units=max(0, int(data.get("seen_units", 0))),
            seen_key=str(data.get("seen_key", "") or ""),
            origin_units=0,
        )

    def to_dict(self) -> Dict:
        return {
            "cycle_id": int(self.cycle_id),
            "claimed": [bool(self.claimed[0]) if self.claimed else False],
            "holding": bool(self.holding),
            "work_units": int(self.work_units),
            "seen_units": int(self.seen_units),
            "seen_key": str(self.seen_key),
            "origin_units": 0,
        }

    def reset_for_cycle(self, cycle_id: int) -> None:
        self.cycle_id = int(cycle_id)
        self.claimed = (False,)
        self.holding = False

    def begin_next_cycle(self) -> None:
        """点完箱子：丢掉满格等待时的积压，从 0 开下一轮。监视点保留以免把等待量灌进来。"""
        self.work_units = 0
        self.origin_units = 0
        self.cycle_id = int(self.cycle_id) + 1
        self.claimed = (False,)
        self.holding = False

    def note_running(self, units: int, goal_key: str) -> None:
        """把当前叶子的 units 增量累进本轮；换叶子不导入对方生涯总量。"""
        key = str(goal_key or "")
        cur = max(0, int(units))
        if key != self.seen_key:
            self.seen_key = key
            self.seen_units = cur
            return
        if cur < self.seen_units:
            self.seen_units = cur
            return
        delta = cur - self.seen_units
        self.seen_units = cur
        if self.holding:
            return
        self.work_units += delta

    def mark_claimed(self, index: int) -> bool:
        """标记领取；已领过返回 False。"""
        if index < 0 or index >= len(self.claimed):
            return False
        if self.claimed[index]:
            return False
        claimed = list(self.claimed)
        claimed[index] = True
        self.claimed = tuple(claimed)
        return True


@dataclass
class RollAccum:
    """自上次开奖检查点以来累计掉落到当前任务的奖励。"""
    gold: float = 0.0
    diamond: float = 0.0

    def is_empty(self) -> bool:
        return self.gold == 0.0 and self.diamond == 0.0


@dataclass
class RollHistoryEntry:
    """单次开奖结果（命中或未中）。"""
    op_at: int = 0
    at: float = field(default_factory=time.time)
    hit: bool = False
    gold: float = 0.0
    diamond: float = 0.0
    gold_crit_mult: float = 1.0
    diamond_crit_mult: float = 1.0
    task_title: str = ""

    @classmethod
    def from_dict(cls, data: Dict) -> "RollHistoryEntry":
        return cls(
            op_at=int(data.get("op_at", 0)),
            at=float(data.get("at", time.time())),
            hit=bool(data.get("hit", False)),
            gold=float(data.get("gold", 0)),
            diamond=float(data.get("diamond", 0)),
            gold_crit_mult=float(data.get("gold_crit_mult", 1.0)),
            diamond_crit_mult=float(data.get("diamond_crit_mult", 1.0)),
            task_title=str(data.get("task_title", "")),
        )

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class RollRuntime:
    """当前开奖周期与有效随机参数（内置机制，每 10 分钟重抽概率/范围）。"""
    next_roll_at: int = 10
    roll_span: int = 10
    segment_colors: List[str] = field(default_factory=list)
    gold_chance: float = 0.35          # 金币掉落概率（与钻石独立）
    diamond_chance: float = 0.06       # 钻石掉落概率（与金币独立）
    gold_min: float = 0.1
    gold_max: float = 1.0
    diamond_min: float = 0.01
    diamond_max: float = 0.1
    last_shuffle_at: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict) -> "RollRuntime":
        # 兼容旧存档：roll_chance → gold_chance
        if "gold_chance" in data:
            gold_chance = float(data["gold_chance"])
        else:
            gold_chance = float(data.get("roll_chance", 0.35))
        return cls(
            next_roll_at=int(data.get("next_roll_at", 10)),
            roll_span=max(1, int(data.get("roll_span", 10))),
            segment_colors=list(data.get("segment_colors", [])),
            gold_chance=gold_chance,
            diamond_chance=float(data.get("diamond_chance", 0.06)),
            gold_min=float(data.get("gold_min", 0.1)),
            gold_max=float(data.get("gold_max", 1.0)),
            diamond_min=float(data.get("diamond_min", 0.01)),
            diamond_max=float(data.get("diamond_max", 0.1)),
            last_shuffle_at=float(data.get("last_shuffle_at", 0.0)),
        )

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class AppState:
    """整体应用状态 (持久化对象)。"""
    inventory: Inventory = field(default_factory=Inventory)
    tasks: List[Task] = field(default_factory=list)
    total_operations: int = 0           # 全局历史操作数
    last_roll_at: int = 0               # 上一次开奖时所属的操作总数
    since_roll: RollAccum = field(default_factory=RollAccum)
    roll_history: List[RollHistoryEntry] = field(default_factory=list)
    roll_runtime: RollRuntime = field(default_factory=RollRuntime)
    ease_chests: EaseChestsState = field(default_factory=EaseChestsState)
    settings: Dict = field(default_factory=lambda: {
        "pin_all_desktops": True,
        "always_on_top": True,
        "sound_enabled": True,
        "sound_volume": 0.8,
        "sound_on_roll_hit": True,
        "sound_op_chance": 0.2,
        "sound_grid_ease_chance": 0.08,
        "roll_interval": 10,           # 每多少次操作触发一次开奖
        "roll_chance": 0.35,           # 旧字段：迁移用（现为 gold_chance）
        "gold_chance": 0.35,           # 金币掉落概率（与钻石独立）
        "gold_min": 0.1,
        "gold_max": 1.0,
        "diamond_chance": 0.06,        # 钻石掉落概率（与金币独立）
        "diamond_min": 0.01,
        "diamond_max": 0.1,
        "pet_best_round": 0,
        "subtask_default_target_minutes": 10,
        "subtask_completion_bonus_gold": 0.5,
        "idle_pause_minutes": 10,
        "bgm_volume": 0.5,
    })

    def active_task(self) -> Optional[Task]:
        """当前唯一进行中的任务 (若有)。"""
        for t in self.tasks:
            if t.status == TaskStatus.ACTIVE:
                return t
        return None

    def visible_gold_diamond(self) -> tuple[float, float]:
        """全局顶栏用：只算已放进奖励背包的金币 / 钻石。"""
        return float(self.inventory.gold), float(self.inventory.diamond)

    def to_dict(self) -> Dict:
        return {
            "inventory": self.inventory.to_dict(),
            "tasks": [t.to_dict() for t in self.tasks],
            "total_operations": self.total_operations,
            "last_roll_at": self.last_roll_at,
            "since_roll": asdict(self.since_roll),
            "roll_history": [e.to_dict() for e in self.roll_history],
            "roll_runtime": self.roll_runtime.to_dict(),
            "ease_chests": self.ease_chests.to_dict(),
            "settings": self.settings,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "AppState":
        inv_raw = data.get("inventory", {})
        inv = (
            Inventory.from_dict(inv_raw)
            if isinstance(inv_raw, dict)
            else Inventory()
        )
        tasks = [Task.from_dict(t) for t in data.get("tasks", [])]
        sr = data.get("since_roll", {})
        history = [RollHistoryEntry.from_dict(x) for x in data.get("roll_history", [])]
        ease_raw = data.get("ease_chests", {})
        s = cls(
            inventory=inv,
            tasks=tasks,
            total_operations=data.get("total_operations", 0),
            last_roll_at=data.get("last_roll_at", 0),
            since_roll=RollAccum(
                gold=float(sr.get("gold", 0)),
                diamond=float(sr.get("diamond", 0)),
            ),
            roll_history=history,
            roll_runtime=RollRuntime.from_dict(data.get("roll_runtime", {})),
            ease_chests=(
                EaseChestsState.from_dict(ease_raw)
                if isinstance(ease_raw, dict)
                else EaseChestsState()
            ),
        )
        s.settings.update(data.get("settings", {}))
        # 刚落地的旧默认尚未被用户调过；统一迁到现行默认
        if s.settings.get("sound_grid_ease_chance") == 0.25:
            s.settings["sound_grid_ease_chance"] = 0.08
        if s.settings.get("sound_op_chance") == 0.4:
            s.settings["sound_op_chance"] = 0.2
        return s


def validate_state_invariants(state: AppState) -> Optional[str]:
    """检查内存状态的业务不变量；返回错误描述，None 表示通过。"""
    if state.total_operations < 0:
        return "total_operations 为负"
    if state.last_roll_at < 0:
        return "last_roll_at 为负"
    if state.last_roll_at > state.total_operations:
        return "last_roll_at 超过 total_operations"

    inv = state.inventory
    for name, val in (("gold", inv.gold), ("diamond", inv.diamond)):
        if not math.isfinite(val) or val < 0:
            return f"inventory.{name} 非法"
    for i, chest in enumerate(inv.chests):
        if not isinstance(chest, ChestItem):
            return f"inventory.chests[{i}] 类型非法"
        if chest.rarity < 0 or chest.rarity > 4:
            return f"inventory.chests[{i}] rarity 非法"
        if not math.isfinite(chest.obtained_at):
            return f"inventory.chests[{i}] obtained_at 非法"
        if chest.unlock_started_at is not None and not math.isfinite(chest.unlock_started_at):
            return f"inventory.chests[{i}] unlock_started_at 非法"

    for letter, counts in inv.letters.items():
        if not (isinstance(letter, str) and len(letter) == 1 and "A" <= letter <= "Z"):
            return f"inventory.letters 键非法: {letter!r}"
        if not isinstance(counts, (list, tuple)) or len(counts) != 5:
            return f"inventory.letters[{letter}] 长度非法"
        for c in counts:
            if not (isinstance(c, int) and c >= 0):
                return f"inventory.letters[{letter}] 计数非法"

    ec = state.ease_chests
    if not isinstance(ec.claimed, tuple) or len(ec.claimed) != 1:
        return "ease_chests.claimed 非法"

    active_count = 0
    task_ids: set[str] = set()
    for t in state.tasks:
        if not t.id:
            return "存在空目标 id"
        if t.id in task_ids:
            return f"目标 id 重复: {t.id}"
        task_ids.add(t.id)

        if t.status == TaskStatus.ACTIVE:
            active_count += 1
        if t.operations < 0:
            return f"目标「{t.title}」operations 为负"
        if not math.isfinite(t.active_seconds) or t.active_seconds < 0:
            return f"目标「{t.title}」active_seconds 非法"

        sub_ids: set[str] = set()

        def check_subtree(nodes: List[Subtask]) -> Optional[str]:
            for s in nodes:
                if not s.id:
                    return "存在空子目标 id"
                if s.id in sub_ids:
                    return f"子目标 id 重复: {s.id}"
                sub_ids.add(s.id)
                if s.operations < 0:
                    return f"子目标「{s.title}」operations 为负"
                if not math.isfinite(s.target_seconds) or s.target_seconds <= 0:
                    return f"子目标「{s.title}」target_seconds 非法"
                if not math.isfinite(s.active_seconds) or s.active_seconds < 0:
                    return f"子目标「{s.title}」active_seconds 非法"
                err = check_subtree(s.children)
                if err:
                    return err
            return None

        err = check_subtree(t.subtasks)
        if err:
            return err

        if t.current_subtask_id is not None:
            sub = t.find_subtask(t.current_subtask_id)
            if sub is None:
                return f"目标「{t.title}」current_subtask_id 指向不存在的子目标"
            if sub.done:
                return f"目标「{t.title}」current_subtask_id 指向已完成的子目标"
            if not sub.is_leaf():
                return f"目标「{t.title}」current_subtask_id 指向非叶子子目标"

    if active_count > 1:
        return f"存在 {active_count} 个进行中目标（最多 1 个）"

    sr = state.since_roll
    if not math.isfinite(sr.gold) or sr.gold < 0:
        return "since_roll.gold 非法"
    if not math.isfinite(sr.diamond) or sr.diamond < 0:
        return "since_roll.diamond 非法"

    rt = state.roll_runtime
    if rt.roll_span < 1:
        return "roll_runtime.roll_span 非法"
    if rt.next_roll_at < state.last_roll_at:
        return "roll_runtime.next_roll_at 早于 last_roll_at"
    for name, val in (
        ("gold_chance", rt.gold_chance),
        ("diamond_chance", rt.diamond_chance),
        ("gold_min", rt.gold_min),
        ("gold_max", rt.gold_max),
        ("diamond_min", rt.diamond_min),
        ("diamond_max", rt.diamond_max),
    ):
        if not math.isfinite(val) or val < 0:
            return f"roll_runtime.{name} 非法"

    return None