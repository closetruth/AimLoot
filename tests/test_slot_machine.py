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
    _REEL_STRIP,
    _SYM_COIN,
    _SYM_DIAMOND,
    _SYM_LETTER,
    _SYM_MISS,
    _SYM_NOTE,
    compute_prizes,
    spin_result,
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


class PureFunctionTests(unittest.TestCase):
    """纯函数：三同判定 / 奖励计算 / 取样。"""

    def test_match_only_triple(self):
        self.assertEqual(_match_of(0, 0, 0), _SYM_COIN)
        self.assertEqual(_match_of(1, 1, 1), _SYM_DIAMOND)
        self.assertEqual(_match_of(2, 2, 2), _SYM_LETTER)
        self.assertEqual(_match_of(3, 3, 3), _SYM_NOTE)
        self.assertIsNone(_match_of(4, 4, 4), "三 ✕ 不中奖")
        self.assertIsNone(_match_of(0, 0, 2), "两同不中奖（经典三轴）")
        self.assertIsNone(_match_of(0, 1, 2))
        self.assertIsNone(_match_of(2, 0, 2))

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
        prizes = compute_prizes(2, 0, 4, Reward(), _StubRng())
        self.assertEqual(len(prizes.letters), 1)
        self.assertTrue(prizes.reward.is_empty())

    def test_no_left_letter_no_prize(self):
        """字不在左轴且无三同 → 空。"""
        prizes = compute_prizes(0, 2, 4, Reward(gold=5.0), _StubRng())
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
        strip = set(_REEL_STRIP)
        for _ in range(200):
            result = spin_result()
            self.assertEqual(len(result), 3)
            self.assertTrue(all(s in strip for s in result))


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
    """减速停轴：阻尼式步长收缩（连续滑行）。"""

    def test_normal_stop_keeps_constant_step(self):
        from src.ui_slot_machine import _NORMAL_STEP, _ReelWidget

        reel = _ReelWidget()
        reel._offset = 0.0
        reel.start_spin()
        reel.stop_at(1)
        start = reel._offset
        reel._on_tick()
        self.assertAlmostEqual(reel._offset - start, _NORMAL_STEP)

    def test_slowdown_shrinks_step_near_target(self):
        from src.ui_slot_machine import _NORMAL_STEP, _ReelWidget

        reel = _ReelWidget()
        reel._offset = 0.0
        reel.start_spin()
        reel.stop_at(1, slowdown=True)
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

    def test_third_suspense_detection(self):
        from src.ui_slot_machine import third_has_suspense

        self.assertTrue(third_has_suspense((0, 0, 9)), "金金 冲三同")
        self.assertTrue(third_has_suspense((2, 2, 9)), "字字 冲三同")
        self.assertTrue(third_has_suspense((3, 3, 9)), "♪♪ 冲三同")
        self.assertFalse(third_has_suspense((0, 2, 9)), "金× 必不中，无悬念")
        self.assertFalse(third_has_suspense((4, 3, 9)), "×♪ 必不中，无悬念")
        self.assertFalse(third_has_suspense((4, 4, 9)), "×× 无悬念")
        self.assertFalse(third_has_suspense((0, 1, 9)), "金钻混排无悬念")

    def test_hot_symbol_covers_letter_note(self):
        from src.ui_slot_machine import hot_symbol_for

        self.assertEqual(hot_symbol_for(_SYM_MISS, (2, 4)), _SYM_LETTER, "字× → 热符号字")
        self.assertEqual(hot_symbol_for(_SYM_MISS, (4, 3)), _SYM_NOTE, "×♪ → 热符号♪")
        self.assertEqual(hot_symbol_for(_SYM_MISS, (0, 0)), _SYM_COIN)
        self.assertIsNone(hot_symbol_for(_SYM_MISS, (4, 4)), "×× 无热符号")
        self.assertIsNone(hot_symbol_for(0, (0, 4)), "停金不适用")

    def test_preferred_miss_stop_pure(self):
        from src.ui_slot_machine import preferred_miss_stop

        for start in range(len(_REEL_STRIP)):
            step = preferred_miss_stop(_REEL_STRIP, start, _SYM_COIN)
            self.assertIsNotNone(step)
            idx = (start + step) % len(_REEL_STRIP)
            self.assertEqual(_REEL_STRIP[idx], _SYM_MISS)
            self.assertEqual(_REEL_STRIP[(idx + 1) % len(_REEL_STRIP)], _SYM_COIN)
        self.assertIsNone(preferred_miss_stop(_REEL_STRIP, 0, None))


class SlotMachineDialogTests(_SfxInjection):
    def test_triple_coin_awards_once(self):
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 0))
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _wait_result(dlg)
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
        _wait_result(dlg)
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
        _wait_result(dlg)
        self.assertIn("三同♪", dlg._result_label.text())
        dlg._close_btn.click()
        _app.processEvents()
        self.assertEqual(sum(got[0].song_tokens.values()), 3)
        dlg.close()

    def test_cherry_letter_prize_shown(self):
        """金×× 不中奖；但 字××（最左轴字）有小奖。"""
        dlg = _make_dialog(Reward(), result=(2, 4, 4), rng=_StubRng())
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _wait_result(dlg)
        self.assertIn("字母", dlg._result_label.text())
        dlg._close_btn.click()
        _app.processEvents()
        self.assertEqual(len(got[0].letters), 1)
        self.assertNotIn("near", self._sfx.calls, "有奖不播近失音")
        dlg.close()

    def test_two_of_a_kind_no_prize_near_miss(self):
        """两同不中奖（经典三轴），触发近失演出。"""
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 4))
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _wait_result(dlg)
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
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 4))
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _wait_result(dlg)
        dlg.reject()
        self.assertEqual(len(got), 1, "揭晓后 Esc 必须结算奖励")
        self.assertAlmostEqual(got[0].reward.gold, dlg.prizes().reward.gold)
        dlg.close()

    def test_close_stops_timers(self):
        dlg = _make_dialog(Reward(), result=(0, 4, 0))
        got: list[SlotPrizes] = []
        dlg.prize_decided.connect(got.append)
        _wait_result(dlg)
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
        _wait_result(dlg)
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
