"""老虎机弹窗离屏回归测试：三同制结算 / 樱桃赔付 / 字母与试听券 / Esc 不丢奖。

历史 bug：Esc / 关闭窗口会绕过「收下」按钮，奖励 signal 未 emit 导致丢失。
机制：经典三轴，三同才中奖；最左轴「字」给 1 枚字母（樱桃式部分赔付）；
三同字 = 3 枚字母、三同♪ = 3 张试听券。任何关闭路径都经 _finalize
恰好 emit 一次（可为空 SlotPrizes），重复收下不重复结算。
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.models import Reward
from src.ui_slot_machine import (
    SlotMachineDialog,
    SlotPrizes,
    _match_of,
    _PHYSICAL_STRIP,
    _PHYSICAL_LEN,
    _VIRTUAL_STRIPS,
    _VIRTUAL_LENS,
    _SYM_COIN,
    _SYM_DIAMOND,
    _SYM_LETTER,
    _SYM_NOTE,
    compute_prizes,
    near_miss_symbol,
    spin_result,
    triple_probabilities,
    physical_strip_probabilities,
)

_app = QApplication.instance() or QApplication([])


class _StubRng:
    """uniform/randrange/choices 确定性返回，用于测试。"""

    def __init__(self, value: float = 1.0, letter_index: int = 0, rarity: int = 0) -> None:
        self.value = value
        self.letter_index = letter_index
        self.rarity = rarity

    def choice(self, seq):
        return seq[0]

    def uniform(self, lo: float, hi: float) -> float:
        return hi if self.value >= 1.0 else lo

    def randrange(self, n: int) -> int:
        return self.letter_index % n

    def choices(self, population, weights=None):
        return [self.rarity]

    def random(self) -> float:
        return self.value


def _pump(ms: int) -> None:
    for _ in range(ms // 20):
        _app.processEvents()
        QTest.qWait(20)
    _app.processEvents()


class _RecordingSfx:
    """记录调用的假 SfxPlayer，用 app.sfx 注入。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def play_slot_spin(self) -> None:
        self.calls.append("spin")

    def stop_slot_spin(self) -> None:
        self.calls.append("stop")

    def play_slot_reel_stop(self) -> None:
        self.calls.append("reel")

    def play_slot_win(self) -> None:
        self.calls.append("win")

    def play_slot_near_miss(self) -> None:
        self.calls.append("near")

    def play_slot_suspense(self) -> None:
        self.calls.append("suspense")


class _SfxInjection(unittest.TestCase):
    """基类：把 _RecordingSfx 挂到 app.sfx，测试结束还原。"""

    _sfx: _RecordingSfx

    def setUp(self) -> None:
        super().setUp()
        self._sfx = _RecordingSfx()
        had = hasattr(_app, "sfx")
        old = getattr(_app, "sfx", None)
        _app.sfx = self._sfx

        def _restore() -> None:
            if had:
                _app.sfx = old
            else:
                try:
                    del _app.sfx
                except AttributeError:
                    pass

        self.addCleanup(_restore)


def _make_dialog(base: Reward | None = None, result=None, rng=None) -> SlotMachineDialog:
    dlg = SlotMachineDialog(
        base or Reward(), task_title="测试目标", result=result, rng=rng
    )
    dlg.show()
    _app.processEvents()
    return dlg


def _wait_result(dlg: SlotMachineDialog, timeout_ms: int = 12000) -> None:
    waited = 0
    while not dlg._result_shown and waited < timeout_ms:
        _pump(100)
        waited += 100


def _stop_and_wait(dlg: SlotMachineDialog, timeout_ms: int = 12000) -> None:
    """按下停轴再等揭晓（默认不自动停，测试需要主动触发）。"""
    dlg._press_stop()
    _wait_result(dlg, timeout_ms)


class PureFunctionTests(unittest.TestCase):
    """纯函数：三同判定 / 奖励计算 / 取样。"""

    def test_match_only_triple(self):
        self.assertEqual(_match_of(0, 0, 0), _SYM_COIN)
        self.assertEqual(_match_of(1, 1, 1), _SYM_DIAMOND)
        self.assertEqual(_match_of(2, 2, 2), _SYM_LETTER)
        self.assertEqual(_match_of(3, 3, 3), _SYM_NOTE)
        self.assertIsNone(_match_of(0, 0, 2), "两同不中奖（经典三轴）")
        self.assertIsNone(_match_of(0, 1, 2))
        self.assertIsNone(_match_of(2, 0, 2))

    def test_near_miss_detection(self):
        """只认前两轴同号：第三轴减速慢停才有"差一点"的悬念过程。"""
        self.assertEqual(near_miss_symbol((0, 0, 1)), _SYM_COIN, "金金钻 差一格")
        self.assertEqual(near_miss_symbol((3, 3, 2)), _SYM_NOTE, "♪♪字 差一格")
        self.assertEqual(near_miss_symbol((1, 1, 0)), _SYM_DIAMOND, "钻钻金 差一格")
        self.assertIsNone(near_miss_symbol((0, 0, 0)), "三同不是近失")
        self.assertIsNone(near_miss_symbol((0, 1, 2)), "全不同不是近失")
        self.assertIsNone(near_miss_symbol((0, 1, 1)), "后两轴同号无悬念过程，不算")
        self.assertIsNone(near_miss_symbol((0, 1, 0)), "首尾同号无悬念过程，不算")

    def test_triple_coin_scaling_and_fallback(self):
        hi = _StubRng(1.0)
        lo = _StubRng(0.0)
        self.assertAlmostEqual(
            compute_prizes(0, 0, 0, Reward(gold=2.0), hi).reward.gold, 8.0
        )
        self.assertAlmostEqual(compute_prizes(0, 0, 0, Reward(), hi).reward.gold, 4.0)
        self.assertAlmostEqual(
            compute_prizes(0, 0, 0, Reward(gold=2.0), lo).reward.gold, 3.0
        )

    def test_triple_diamond_scaling(self):
        hi = _StubRng(1.0)
        prizes = compute_prizes(1, 1, 1, Reward(diamond=5.0), hi)
        self.assertAlmostEqual(prizes.reward.diamond, 5.0)
        self.assertEqual(prizes.reward.gold, 0.0)
        self.assertAlmostEqual(compute_prizes(1, 1, 1, Reward(), hi).reward.diamond, 1.0)

    def test_triple_letter_gives_three(self):
        prizes = compute_prizes(2, 2, 2, Reward(gold=2.0), _StubRng())
        self.assertEqual(len(prizes.letters), 3)
        self.assertTrue(prizes.reward.is_empty())
        for letter, rarity in prizes.letters:
            self.assertTrue("A" <= letter <= "Z")
            self.assertIn(rarity, range(5))

    def test_triple_note_gives_three_tokens(self):
        prizes = compute_prizes(3, 3, 3, Reward(gold=2.0), _StubRng())
        self.assertEqual(sum(prizes.song_tokens.values()), 3)
        self.assertTrue(prizes.reward.is_empty())

    def test_cherry_left_letter(self):
        """最左轴「字」非三同：樱桃式 1 枚字母。"""
        prizes = compute_prizes(2, 0, 1, Reward(), _StubRng())
        self.assertEqual(len(prizes.letters), 1)
        self.assertTrue(prizes.reward.is_empty())

    def test_no_left_letter_no_prize(self):
        """字不在左轴且无三同 → 空。"""
        prizes = compute_prizes(0, 2, 1, Reward(gold=5.0), _StubRng())
        self.assertTrue(prizes.is_empty())
        self.assertEqual(prizes.letters, [])
        self.assertEqual(prizes.song_tokens, {})

    def test_prizes_apply_to_inventory(self):
        from src.models import Inventory

        inv = Inventory()
        prizes = SlotPrizes(reward=Reward(gold=2.5, diamond=0.5))
        prizes.letters = [("D", 2), ("A", 0)]
        prizes.song_tokens = {"The Dawn": 2}
        prizes.apply(inv)
        self.assertAlmostEqual(inv.gold, 2.5)
        self.assertAlmostEqual(inv.diamond, 0.5)
        self.assertEqual(inv.letter_total("D"), 1)
        self.assertEqual(inv.letters["D"][2], 1)
        self.assertEqual(inv.letters["A"][0], 1)
        self.assertEqual(inv.song_token_count("The Dawn"), 2)

    def test_spin_result_membership(self):
        all_syms = set(_PHYSICAL_STRIP)
        for _ in range(200):
            result = spin_result()
            self.assertEqual(len(result), 3)
            self.assertTrue(all(s in all_syms for s in result))


class ReelDirectionTests(unittest.TestCase):
    """转轮方向回归：向下滚（上格符号下一帧落到中心）。"""

    def test_symbol_flows_downward(self):
        from src.ui_slot_machine import _ReelWidget

        reel = _ReelWidget()
        above = reel._symbol_at(-1)
        reel._offset += 1
        center_next = reel._symbol_at(0)
        self.assertEqual(above, center_next, "上格符号应落到中心（向下滚）")

    def test_stopping_at_symbol_lands_center(self):
        from src.ui_slot_machine import _ReelWidget

        reel = _ReelWidget()
        reel._offset = 0.0
        reel.start_spin()
        reel.stop_at(1)  # 钻
        for _ in range(2000):
            reel._on_tick()
            if reel.is_stopped():
                break
        self.assertTrue(reel.is_stopped())
        self.assertEqual(reel.center_symbol(), 1)


class SlowdownTests(unittest.TestCase):
    """减速停轴：所有轴都走阻尼减速，落定前明显变慢。"""

    def test_stop_reserves_braking_distance(self):
        """停点必须留出减速区宽度，否则来不及减速（观感仍是骤停）。"""
        from src.ui_slot_machine import _ReelWidget, _STOP_ZONE_CELLS

        for start in range(0, 22):
            for symbol in range(4):
                reel = _ReelWidget()
                reel._offset = float(start)
                reel.start_spin()
                reel.stop_at(symbol)
                self.assertGreaterEqual(
                    reel._target - reel._offset, _STOP_ZONE_CELLS,
                    f"起点{start} 停{symbol}：距离不足减速区",
                )

    def test_normal_stop_decelerates_before_halt(self):
        """普通停轴：末段步长显著小于匀速步长。"""
        from src.ui_slot_machine import _NORMAL_STEP, _ReelWidget

        reel = _ReelWidget()
        reel._offset = 0.0
        reel.start_spin()
        reel.stop_at(1)
        steps = []
        prev = reel._offset
        for _ in range(2000):
            reel._on_tick()
            steps.append(reel._offset - prev)
            prev = reel._offset
            if reel.is_stopped():
                break
        self.assertTrue(reel.is_stopped())
        self.assertEqual(reel.center_symbol(), 1)
        self.assertLess(min(steps[-6:]), _NORMAL_STEP * 0.5, "临近停点应显著减速")
        self.assertLess(min(steps[-6:]), max(steps[:6]))

    def test_suspense_stop_crawls_slower_than_normal(self):
        """悬念轴（前两轴同号）比普通轴更拖。"""
        from src.ui_slot_machine import _ReelWidget

        def frames(suspense_sym):
            reel = _ReelWidget()
            reel._offset = 0.0
            reel.start_spin()
            reel.stop_at(1, suspense_sym=suspense_sym)
            count = 0
            for _ in range(4000):
                reel._on_tick()
                count += 1
                if reel.is_stopped():
                    break
            self.assertEqual(reel.center_symbol(), 1)
            return count

        self.assertGreater(frames(1), frames(None), "悬念停轴应更慢")

    def test_third_suspense_detection(self):
        from src.ui_slot_machine import third_has_suspense

        self.assertEqual(third_has_suspense((0, 0, 9)), 0, "金金 冲三同")
        self.assertEqual(third_has_suspense((2, 2, 9)), 2, "字字 冲三同")
        self.assertEqual(third_has_suspense((3, 3, 9)), 3, "♪♪ 冲三同")
        self.assertIsNone(third_has_suspense((0, 2, 9)), "金字 必然无三同，无悬念")
        self.assertIsNone(third_has_suspense((0, 1, 9)), "金钻混排无悬念")

    def test_physical_strip_layout(self):
        """物理带22格，近失邻接：金/字两侧必须是钻或♪。"""
        self.assertEqual(_PHYSICAL_LEN, 22)
        from collections import Counter
        counts = Counter(_PHYSICAL_STRIP)
        self.assertEqual(counts[_SYM_COIN], 5)
        self.assertEqual(counts[_SYM_DIAMOND], 7)
        self.assertEqual(counts[_SYM_LETTER], 3)
        self.assertEqual(counts[_SYM_NOTE], 7)
        high_freq = {_SYM_DIAMOND, _SYM_NOTE}
        for i, sym in enumerate(_PHYSICAL_STRIP):
            if sym in (_SYM_COIN, _SYM_LETTER):
                above = _PHYSICAL_STRIP[(i - 1) % _PHYSICAL_LEN]
                below = _PHYSICAL_STRIP[(i + 1) % _PHYSICAL_LEN]
                self.assertIn(above, high_freq,
                              f"位置{i}的{sym}上方应为高频符号，实际{above}")
                self.assertIn(below, high_freq,
                              f"位置{i}的{sym}下方应为高频符号，实际{below}")

    def test_virtual_strip_symbol_consistency(self):
        """虚拟带中的符号必须是物理带中存在的符号。"""
        physical_syms = set(_PHYSICAL_STRIP)
        for reel_idx, vstrip in enumerate(_VIRTUAL_STRIPS):
            for sym in vstrip:
                self.assertIn(sym, physical_syms)

    def test_triple_probabilities_sum(self):
        """三同概率合计约10.5%。"""
        probs = triple_probabilities()
        total = sum(probs.values())
        self.assertAlmostEqual(total, 0.1052, places=2)

    def test_physical_vs_virtual_deception(self):
        """虚拟带第三轴金概率远低于物理带显示概率（欺骗性）。"""
        phys = physical_strip_probabilities()
        self.assertAlmostEqual(phys[_SYM_COIN], 5 / 22, places=2)
        v3_coin = sum(1 for s in _VIRTUAL_STRIPS[2] if s == _SYM_COIN) / len(_VIRTUAL_STRIPS[2])
        self.assertAlmostEqual(v3_coin, 6 / 72, places=2)
        self.assertLess(v3_coin, phys[_SYM_COIN],
                        "第三轴金概率应低于物理带显示概率")


class SlotMachineDialogTests(_SfxInjection):
    def test_manual_stop_stops_all_reels_and_keeps_result(self):
        """按一次「停」：三轴同时起刹、阻力递增，中心符号仍是预设结果。"""
        dlg = _make_dialog(Reward(), result=(1, 3, 1))
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _pump(200)
        self.assertFalse(dlg._reels[0].is_stopped(), "测试前提：尚在转动")
        dlg._stop_btn.click()
        _app.processEvents()
        self.assertTrue(
            dlg._reels[0].is_stopping(), "第一轴应立即开始停",
        )
        self.assertTrue(
            dlg._reels[2].is_stopping(), "第三轴也应同帧起刹（不再延迟）",
        )
        self.assertGreater(
            dlg._reels[2]._zone_override, dlg._reels[0]._zone_override,
            "第三轴减速区更宽 → 落定更晚",
        )
        _wait_result(dlg)
        self.assertEqual(
            [r.center_symbol() for r in dlg._reels], [1, 3, 1],
            "手动停轴不得改变结果",
        )
        dlg._close_btn.click()
        _app.processEvents()
        self.assertEqual(len(got), 1)
        dlg.close()

    def test_manual_stop_via_space_key(self):
        dlg = _make_dialog(Reward(), result=(0, 0, 0))
        _pump(200)
        QTest.keyClick(dlg, Qt.Key_Space)
        _app.processEvents()
        self.assertTrue(dlg._reels[0].is_stopping())
        _wait_result(dlg)
        self.assertEqual([r.center_symbol() for r in dlg._reels], [0, 0, 0])
        dlg.close()

    def test_manual_stop_disabled_after_reveal(self):
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 0))
        _stop_and_wait(dlg)
        self.assertFalse(dlg._stop_btn.isEnabled(), "揭晓后停轴按钮应禁用")
        dlg._press_stop()  # 直接调用也不应改变任何状态
        self.assertTrue(dlg._result_shown)
        dlg.close()

    def test_manual_stop_twice_is_safe(self):
        """连按两次不应报错或改变结果。"""
        dlg = _make_dialog(Reward(), result=(1, 1, 0))
        _pump(200)
        dlg._press_stop()
        dlg._press_stop()
        _app.processEvents()
        _stop_and_wait(dlg)
        self.assertEqual([r.center_symbol() for r in dlg._reels], [1, 1, 0])
        dlg.close()

    def test_no_auto_stop_without_press(self):
        """不按「停」就不揭晓（兜底 30 秒，测试只等 2.5 秒）。"""
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 0))
        _pump(2500)
        self.assertFalse(dlg._result_shown, "未按停时不应自动揭晓")
        self.assertFalse(dlg._close_btn.isEnabled())
        for reel in dlg._reels:
            self.assertFalse(reel.is_stopped(), "三轴应持续转动")
        _stop_and_wait(dlg)
        self.assertTrue(dlg._result_shown, "按下停轴后应揭晓")
        dlg.close()

    def test_fallback_timer_is_armed(self):
        """兜底 30 秒计时器已挂上，防止模态窗无限卡住。"""
        dlg = _make_dialog(Reward(), result=(0, 0, 0))
        self.assertTrue(dlg._stop_timers, "应有兜底计时器")
        self.assertTrue(any(t.isActive() for t in dlg._stop_timers))
        dlg.close()

    def test_near_miss_grazes_payline(self):
        """真机式近失：第三轴停稳时热符号挂在赔付线正上方（擦线而过）。"""
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 1))  # 金金钻
        _stop_and_wait(dlg)
        reel = dlg._reels[2]
        idx = int(reel._offset) % _PHYSICAL_LEN
        center = _PHYSICAL_STRIP[idx]
        above = _PHYSICAL_STRIP[(idx + 1) % _PHYSICAL_LEN]
        self.assertEqual(center, _SYM_DIAMOND, "第三轴停在预设结果")
        self.assertEqual(above, _SYM_COIN, "热符号（金）应挂在赔付线上方")
        dlg.close()

    def test_physical_strip_covers_most_graze_combos(self):
        """物理带排列应覆盖绝大多数"某符号上方是热符号"的近失画面。"""
        n = _PHYSICAL_LEN
        avail = {
            (sym, graze): any(
                _PHYSICAL_STRIP[i] == sym and _PHYSICAL_STRIP[(i + 1) % n] == graze
                for i in range(n)
            )
            for sym in (0, 1, 2, 3)
            for graze in (0, 1, 2, 3)
            if sym != graze
        }
        covered = sum(1 for v in avail.values() if v)
        self.assertGreaterEqual(
            covered, 10, f"擦线组合覆盖不足：{covered}/12"
        )

    def test_triple_coin_awards_once(self):
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 0))
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _stop_and_wait(dlg)
        self.assertTrue(dlg._result_shown)
        self.assertTrue(dlg._close_btn.isEnabled())
        dlg._close_btn.click()
        _app.processEvents()
        self.assertEqual(len(got), 1)
        self.assertTrue(1.5 <= got[0].reward.gold <= 4.0)
        self.assertTrue(dlg._finalized)
        dlg._close_btn.click()
        self.assertEqual(len(got), 1, "重复收下不得重复结算")
        dlg.close()

    def test_letter_triple_shows_and_awards(self):
        dlg = _make_dialog(Reward(), result=(2, 2, 2), rng=_StubRng())
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _stop_and_wait(dlg)
        self.assertIn("三同字", dlg._result_label.text())
        dlg._close_btn.click()
        _app.processEvents()
        self.assertEqual(len(got), 1)
        self.assertEqual(len(got[0].letters), 3)
        self.assertIn("win", self._sfx.calls)
        dlg.close()

    def test_note_triple_shows_and_awards(self):
        dlg = _make_dialog(Reward(), result=(3, 3, 3), rng=_StubRng())
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _stop_and_wait(dlg)
        self.assertIn("三同♪", dlg._result_label.text())
        dlg._close_btn.click()
        _app.processEvents()
        self.assertEqual(sum(got[0].song_tokens.values()), 3)
        dlg.close()

    def test_cherry_letter_prize_shown(self):
        """字钻金（最左轴字）不中三同，但樱桃赔付给 1 枚字母。"""
        dlg = _make_dialog(Reward(), result=(2, 1, 0), rng=_StubRng())
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _stop_and_wait(dlg)
        self.assertIn("字母", dlg._result_label.text())
        dlg._close_btn.click()
        _app.processEvents()
        self.assertEqual(len(got[0].letters), 1)
        dlg.close()

    def test_two_of_a_kind_no_prize_near_miss(self):
        """两同不中奖（经典三轴），触发近失演出。"""
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 1))
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _stop_and_wait(dlg)
        self.assertNotIn("near", self._sfx.calls, "揭晓前不应立刻播近失音")
        self.assertFalse(dlg._near_miss_label.isVisible())
        _pump(500)
        self.assertIn("near", self._sfx.calls)
        self.assertTrue(dlg._near_miss_label.isVisible())
        dlg._close_btn.click()
        _app.processEvents()
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].is_empty())
        dlg.close()

    def test_escape_during_spin_still_awards(self):
        """转动中 Esc：跳过后仍必须结算一次（历史 bug：奖励丢失）。"""
        dlg = _make_dialog(Reward(), result=(1, 1, 1))
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _pump(300)
        self.assertFalse(dlg._reels[0].is_stopped(), "测试前提：第一轴应仍在转")
        QTest.keyClick(dlg, Qt.Key_Escape)
        _app.processEvents()
        self.assertEqual(len(got), 1, "转动中 Esc 必须结算一次奖励")
        self.assertTrue(0.3 <= got[0].reward.diamond <= 1.0)
        self.assertFalse(dlg.isVisible())
        self.assertIn("stop", self._sfx.calls)
        dlg.close()

    def test_escape_immediately_awards(self):
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 0))
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        QTest.keyClick(dlg, Qt.Key_Escape)
        _app.processEvents()
        self.assertEqual(len(got), 1)
        self.assertFalse(dlg.isVisible())
        self.assertIn("stop", self._sfx.calls)
        dlg.close()

    def test_reject_after_reveal_awards_once(self):
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 1))
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _stop_and_wait(dlg)
        dlg.reject()
        self.assertEqual(len(got), 1, "揭晓后 Esc 必须结算奖励")
        self.assertAlmostEqual(got[0].reward.gold, dlg.prizes().reward.gold)
        dlg.close()

    def test_close_stops_timers(self):
        dlg = _make_dialog(Reward(), result=(0, 1, 0))
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _stop_and_wait(dlg)
        dlg.close()
        _app.processEvents()
        self.assertTrue(dlg._finalized)
        self.assertEqual(len(got), 1)
        for reel in dlg._reels:
            self.assertFalse(reel._timer.isActive(), "轴计时器必须停")
        for timer in dlg._stop_timers:
            self.assertFalse(timer.isActive(), "停轴计时器必须停")
        self.assertIn("stop", self._sfx.calls)

    def test_spin_sfx_plays_reel_ticks_and_win(self):
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 0))
        _stop_and_wait(dlg)
        self.assertEqual(self._sfx.calls[0], "spin")
        self.assertEqual(self._sfx.calls.count("reel"), 3)
        self.assertIn("win", self._sfx.calls)
        self.assertIn("stop", self._sfx.calls)
        dlg.close()

    def test_chest_claim_opens_slot_and_prizes_land(self):
        """集成回归：领宝箱弹老虎机，产出入账。"""
        import tempfile
        from pathlib import Path
        from unittest import mock

        from src.models import AppState
        from src.power_monitor import PowerMonitor
        from src.task_manager import TaskManager
        from src.widget import FloatingWidget

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = AppState()
        manager = TaskManager(state, PowerMonitor())
        manager._intervals_path = Path(tmp.name) / "runtime_intervals.json"
        widget = FloatingWidget(state, manager)
        widget.show()
        _app.processEvents()

        captured: list[SlotMachineDialog] = []

        def _fake_exec(self: SlotMachineDialog) -> None:
            captured.append(self)

        with mock.patch.object(SlotMachineDialog, "exec", new=_fake_exec):
            widget.roll_progress_bar.chest_claimed.emit(0, 4)
        self.assertEqual(len(captured), 1, "领宝箱应弹一次老虎机")
        self.assertEqual(state.inventory.chest_counts_by_rarity()[4], 1)
        self.assertEqual(captured[0]._task_title, "传奇宝箱")
        self.assertTrue(captured[0]._base.is_empty())
        before_gold = state.inventory.gold
        captured[0].prize_decided.emit(SlotPrizes(reward=Reward(gold=2.5)))
        self.assertAlmostEqual(state.inventory.gold, before_gold + 2.5)
        widget.close()
        widget.deleteLater()
        _app.processEvents()

    def test_subtask_claim_opens_slot_and_prizes_land(self):
        """集成回归：子目标「完成并领取」也弹老虎机，产出入账。"""
        import tempfile
        from pathlib import Path
        from unittest import mock

        from src.main import Application
        from src.models import AppState
        from src.power_monitor import PowerMonitor
        from src.task_manager import TaskManager
        from src.widget import FloatingWidget

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = AppState()
        manager = TaskManager(state, PowerMonitor())
        manager._intervals_path = Path(tmp.name) / "runtime_intervals.json"
        widget = FloatingWidget(state, manager)
        widget.show()
        _app.processEvents()

        class _Stub:
            def __init__(self) -> None:
                self.state = state
                self.widget = widget
                self.saved = 0
                self.notified: list[str] = []

            def _notify_subtask_claim(self, reward, *, title: str = "") -> None:
                self.notified.append(title)

            def _safe_save(self) -> None:
                self.saved += 1

        stub = _Stub()
        captured: list[SlotMachineDialog] = []

        def _fake_exec(self: SlotMachineDialog) -> None:
            captured.append(self)

        with mock.patch.object(SlotMachineDialog, "exec", new=_fake_exec):
            Application._on_subtask_claimed(
                stub, "阅读文档", Reward(gold=2.0, diamond=0.5)
            )
        self.assertEqual(len(captured), 1)
        self.assertIn("子目标完成", captured[0]._task_title)
        self.assertEqual(stub.notified, ["阅读文档"])
        before_gold = state.inventory.gold
        captured[0].prize_decided.emit(SlotPrizes(reward=Reward(gold=1.5)))
        self.assertAlmostEqual(state.inventory.gold, before_gold + 1.5)
        self.assertEqual(stub.saved, 1)
        widget.close()
        widget.deleteLater()
        _app.processEvents()


class HighlightTests(_SfxInjection):
    """中奖高亮：只亮中线格 + 渐亮强度动画 + 樱桃单格高亮。"""

    def test_flash_animates_strength(self):
        from src.ui_slot_machine import _SYM_COIN, _ReelWidget

        reel = _ReelWidget()
        reel.show()
        reel.set_flash(_SYM_COIN)
        self.assertIsNotNone(reel._flash_anim)
        _pump(300)
        self.assertGreater(reel._flash_strength, 0.9, "渐亮应在 150ms 内到 1.0")
        reel.set_flash(None)
        self.assertEqual(reel._flash_strength, 0.0)
        reel.close()

    def test_triple_highlights_all_three_reels(self):
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 0))
        _stop_and_wait(dlg)
        for reel in dlg._reels:
            self.assertIsNotNone(reel._flash, "三同格应设高亮色")
        dlg.close()

    def test_cherry_highlights_only_left_reel(self):
        """樱桃（最左轴字）：只有左轴亮，另外两轴不高亮。"""
        dlg = _make_dialog(Reward(), result=(2, 1, 0), rng=_StubRng())
        _stop_and_wait(dlg)
        self.assertIsNotNone(dlg._reels[0]._flash, "樱桃应亮左轴")
        self.assertIsNone(dlg._reels[1]._flash, "未中轴不应高亮")
        self.assertIsNone(dlg._reels[2]._flash)
        dlg.close()

    def test_no_prize_no_flash(self):
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 1))
        _stop_and_wait(dlg)
        for reel in dlg._reels:
            self.assertIsNone(reel._flash, "未中奖不应高亮")
        dlg.close()


class SongTokenTests(unittest.TestCase):
    """试听券：存取 / 耗券 / 持久化。"""

    def test_add_and_consume(self):
        from src.models import Inventory

        inv = Inventory()
        inv.add_song_token("The Dawn", 2)
        self.assertEqual(inv.song_token_count("The Dawn"), 2)
        self.assertTrue(inv.consume_song_token("The Dawn"))
        self.assertEqual(inv.song_token_count("The Dawn"), 1)
        self.assertTrue(inv.consume_song_token("The Dawn"))
        self.assertEqual(inv.song_token_count("The Dawn"), 0)
        self.assertFalse(inv.consume_song_token("The Dawn"), "没券时应返回 False")
        self.assertNotIn("The Dawn", inv.song_tokens, "零张应从字典移除")

    def test_roundtrip_serialization(self):
        from src.models import Inventory

        inv = Inventory()
        inv.add_song_token("Geordie", 3)
        data = inv.to_dict()
        self.assertEqual(data["song_tokens"], {"Geordie": 3})
        restored = Inventory.from_dict(data)
        self.assertEqual(restored.song_token_count("Geordie"), 3)

    def test_from_dict_ignores_bad_entries(self):
        from src.models import Inventory

        inv = Inventory.from_dict(
            {"song_tokens": {"Good": 2, "Zero": 0, "Bad": "x", "": 5}}
        )
        self.assertEqual(inv.song_token_count("Good"), 2)
        self.assertEqual(inv.song_token_count("Zero"), 0)
        self.assertEqual(inv.song_token_count("Bad"), 0)
        self.assertEqual(inv.song_tokens, {"Good": 2})


if __name__ == "__main__":
    unittest.main()