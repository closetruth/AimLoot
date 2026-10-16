"""老虎机弹窗离屏回归测试：奖励结算 / 跳过 / Esc 各阶段不丢奖。

历史 bug：Esc / 关闭窗口会绕过「收下」按钮，奖励 signal 未 emit 导致丢失。
本文件按简单版 API 重写：结果在构造时一次性生成，任何关闭路径都经
_finalize 恰好 emit 一次（可为空 Reward），重复收下不重复结算。
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
    _REEL_STRIP,
    _match_of,
    compute_bonus,
    spin_result,
)

_app = QApplication.instance() or QApplication([])


class _StubRng:
    """uniform(lo, hi) 固定返回 hi 或 lo，用于确定性测试。"""

    def __init__(self, mode: str) -> None:
        assert mode in ("hi", "lo")
        self.mode = mode

    def choice(self, seq):
        return seq[0]

    def uniform(self, lo: float, hi: float) -> float:
        return hi if self.mode == "hi" else lo


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


def _make_dialog(base: Reward | None = None, result=None) -> SlotMachineDialog:
    dlg = SlotMachineDialog(base or Reward(), task_title="测试目标", result=result)
    dlg.show()
    _app.processEvents()
    return dlg


def _wait_result(dlg: SlotMachineDialog, timeout_ms: int = 8000) -> None:
    waited = 0
    while not dlg._result_shown and waited < timeout_ms:
        _pump(100)
        waited += 100


class PureFunctionTests(unittest.TestCase):
    """纯函数：匹配判定 / 奖励计算 / 取样。"""

    def test_match_classification(self):
        self.assertEqual(_match_of(0, 0, 0), ("triple", 0))
        self.assertEqual(_match_of(1, 1, 1), ("triple", 1))
        self.assertEqual(_match_of(2, 2, 2), ("none", None))
        self.assertEqual(_match_of(0, 0, 2), ("pair", 0))
        self.assertEqual(_match_of(1, 2, 1), ("pair", 1))
        self.assertEqual(_match_of(0, 1, 2), ("none", None))
        self.assertEqual(_match_of(2, 2, 0), ("none", None))

    def test_compute_bonus_scaling_and_fallback(self):
        hi = _StubRng("hi")
        lo = _StubRng("lo")
        # 三同金：base 2.0 → 2.0×4.0；空 base → 兜底 1.0×4.0
        self.assertAlmostEqual(
            compute_bonus(0, 0, 0, Reward(gold=2.0), hi).gold, 8.0
        )
        self.assertAlmostEqual(compute_bonus(0, 0, 0, Reward(), hi).gold, 4.0)
        # 两同金
        self.assertAlmostEqual(
            compute_bonus(0, 0, 2, Reward(gold=2.0), hi).gold, 2.4
        )
        self.assertAlmostEqual(compute_bonus(0, 0, 2, Reward(), hi).gold, 1.2)
        # 三同钻：base 5.0 → 5.0×1.0；空 base → 兜底 1.0×1.0
        self.assertAlmostEqual(
            compute_bonus(1, 1, 1, Reward(diamond=5.0), hi).diamond, 5.0
        )
        self.assertAlmostEqual(compute_bonus(1, 1, 1, Reward(), hi).diamond, 1.0)
        # 下界方向也按倍率表走
        self.assertAlmostEqual(
            compute_bonus(0, 0, 2, Reward(gold=2.0), lo).gold, 0.6
        )

    def test_compute_bonus_currency_routing(self):
        # 金奖不动钻：base 只有钻时两同金走金兜底
        bonus = compute_bonus(0, 0, 2, Reward(diamond=10.0), _StubRng("hi"))
        self.assertAlmostEqual(bonus.gold, 1.2)
        self.assertEqual(bonus.diamond, 0.0)
        # 无匹配 → 空奖励
        self.assertTrue(compute_bonus(0, 1, 2, Reward(gold=2.0)).is_empty())
        # 三同 ✕ → 空奖励
        self.assertTrue(compute_bonus(2, 2, 2, Reward(gold=2.0)).is_empty())

    def test_spin_result_membership(self):
        strip = set(_REEL_STRIP)
        for _ in range(200):
            result = spin_result()
            self.assertEqual(len(result), 3)
            self.assertTrue(all(s in strip for s in result))


class SlotMachineDialogTests(_SfxInjection):
    def test_accept_awards_bonus_once(self):
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 0))
        got: list[object] = []
        dlg.reward_decided.connect(got.append)
        _wait_result(dlg)
        self.assertTrue(dlg._result_shown, "转轮应在超时前揭晓结果")
        self.assertTrue(dlg._close_btn.isEnabled())
        dlg._close_btn.click()
        _app.processEvents()
        self.assertEqual(len(got), 1)
        self.assertTrue(1.5 <= got[0].gold <= 4.0)
        self.assertTrue(dlg._finalized)
        dlg._close_btn.click()  # 二次点击不得重复结算
        self.assertEqual(len(got), 1, "重复收下不得重复结算")
        dlg.close()

    def test_spin_sfx_plays_reel_ticks_and_win(self):
        """转动全程音效：开转 → 3 次停轴 → 中奖音，三轴停稳后循环声即停。"""
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 0))
        _wait_result(dlg)
        self.assertEqual(self._sfx.calls[0], "spin", "构造后应立即开转音")
        self.assertEqual(self._sfx.calls.count("reel"), 3, "每轴停稳各一声")
        self.assertIn("win", self._sfx.calls, "三同应有中奖音")
        self.assertIn("stop", self._sfx.calls, "三轴停稳后循环声应停")
        dlg.close()

    def test_near_miss_reveal_after_delay(self):
        """金××（差一格）：静置节拍后播"差一点"音 + 标签可见 + 两格 ✕ 回弹。"""
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 2, 2))
        _wait_result(dlg)
        self.assertTrue(dlg.bonus_reward().is_empty())
        self.assertNotIn("near", self._sfx.calls, "揭晓前不应立刻播近失音")
        self.assertFalse(dlg._near_miss_label.isVisible())
        _pump(500)  # 等过 300ms 静置节拍
        self.assertIn("near", self._sfx.calls, "静置后应播近失音")
        self.assertNotIn("win", self._sfx.calls)
        self.assertTrue(dlg._near_miss_label.isVisible(), "近失标签应可见")
        # 两个 ✕ 格各有回弹动画（_nudge_anim 存活期间非 None）
        self.assertIsNotNone(dlg._reels[1]._nudge_anim)
        self.assertIsNotNone(dlg._reels[2]._nudge_anim)
        self.assertIsNone(dlg._reels[0]._nudge_anim, "金格不应回弹")
        dlg.close()

    def test_near_miss_escape_before_reveal_is_safe(self):
        """静置节拍期间 Esc：不崩、结算一次、延迟揭晓被取消。"""
        dlg = _make_dialog(Reward(gold=1.0), result=(2, 2, 0))
        got: list[object] = []
        dlg.reward_decided.connect(got.append)
        _wait_result(dlg)
        self.assertNotIn("near", self._sfx.calls)
        QTest.keyClick(dlg, Qt.Key_Escape)  # 在 300ms 揭晓前关闭
        _app.processEvents()
        self.assertEqual(len(got), 1)
        self.assertTrue(dlg._finalized)
        _pump(500)
        self.assertNotIn("near", self._sfx.calls, "关闭后不得再播近失音")
        dlg.close()

    def test_all_miss_sfx_is_silent(self):
        """×××：离中奖两格，无近失音、无标签。"""
        dlg = _make_dialog(Reward(gold=1.0), result=(2, 2, 2))
        _wait_result(dlg)
        _pump(500)
        self.assertNotIn("near", self._sfx.calls)
        self.assertNotIn("win", self._sfx.calls)
        self.assertFalse(dlg._near_miss_label.isVisible())
        dlg.close()

    def test_escape_before_stop_still_awards(self):
        """转动中 Esc：跳过后仍必须结算一次（历史 bug：奖励丢失）。"""
        dlg = _make_dialog(Reward(), result=(1, 1, 1))
        got: list[object] = []
        dlg.reward_decided.connect(got.append)
        _pump(300)
        self.assertFalse(dlg._reels[0].is_stopped(), "测试前提：第一轴应仍在转")
        QTest.keyClick(dlg, Qt.Key_Escape)
        _app.processEvents()
        self.assertEqual(len(got), 1, "转动中 Esc 必须结算一次奖励")
        self.assertTrue(0.3 <= got[0].diamond <= 1.0)
        self.assertFalse(dlg.isVisible())
        self.assertIn("stop", self._sfx.calls, "Esc 结算也必须停循环声")
        dlg.close()

    def test_escape_immediately_awards(self):
        """构造后立刻 Esc：不经过任何转动也结算（结果构造时已定）。"""
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 0))
        got: list[object] = []
        dlg.reward_decided.connect(got.append)
        QTest.keyClick(dlg, Qt.Key_Escape)
        _app.processEvents()
        self.assertEqual(len(got), 1)
        self.assertFalse(dlg.isVisible())
        self.assertIn("stop", self._sfx.calls)
        dlg.close()

    def test_reject_after_reveal_awards_once(self):
        dlg = _make_dialog(Reward(gold=1.0), result=(0, 0, 2))
        got: list[object] = []
        dlg.reward_decided.connect(got.append)
        _wait_result(dlg)
        dlg.reject()
        self.assertEqual(len(got), 1, "揭晓后 Esc 必须结算奖励")
        self.assertEqual(got[0].gold, dlg.bonus_reward().gold)
        self.assertEqual(got[0].diamond, dlg.bonus_reward().diamond)
        dlg.close()

    def test_no_match_emits_empty_and_close_stops_timers(self):
        dlg = _make_dialog(Reward(), result=(0, 1, 2))
        got: list[object] = []
        dlg.reward_decided.connect(got.append)
        _wait_result(dlg)
        self.assertTrue(dlg.bonus_reward().is_empty())
        self.assertTrue(dlg._close_btn.isEnabled(), "空手也能顺利收下")
        self.assertIsNotNone(dlg._reveal_timer, "1 个 ✕ 应排入近失揭晓定时器")
        dlg.close()
        _app.processEvents()
        self.assertTrue(dlg._finalized)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].is_empty(), "无匹配应发空奖励")
        for reel in dlg._reels:
            self.assertFalse(reel._timer.isActive(), "轴计时器必须停")
        for timer in dlg._stop_timers:
            self.assertFalse(timer.isActive(), "停轴计时器必须停")
        self.assertFalse(dlg._reveal_timer.isActive(), "迟揭晓定时器必须停")
        self.assertIn("stop", self._sfx.calls)
        _pump(500)
        self.assertNotIn("near", self._sfx.calls, "关闭后不得再揭晓近失")

    def test_chest_claim_opens_slot_and_bonus_lands(self):
        """集成回归：领宝箱弹老虎机，bonus 信号入账。"""
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
        # 不要 pump：_show_slot_machine 尾部 deleteLater 会在下次事件循环销毁弹窗
        self.assertEqual(len(captured), 1, "领宝箱应弹一次老虎机")
        self.assertEqual(state.inventory.chest_counts_by_rarity()[4], 1)
        self.assertEqual(captured[0]._task_title, "传奇宝箱")
        self.assertTrue(captured[0]._base.is_empty())
        before_gold = state.inventory.gold
        captured[0].reward_decided.emit(Reward(gold=2.5))
        self.assertAlmostEqual(state.inventory.gold, before_gold + 2.5)
        widget.close()
        widget.deleteLater()
        _app.processEvents()

    def test_subtask_claim_opens_slot_and_bonus_lands(self):
        """集成回归：子目标「完成并领取」也弹老虎机，bonus 入账。"""
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

        # 用桩替代完整 Application（省去托盘/监听/单实例锁）
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
        self.assertEqual(len(captured), 1, "子目标领取应弹一次老虎机")
        self.assertIn("子目标完成", captured[0]._task_title)
        self.assertIn("阅读文档", captured[0]._task_title)
        self.assertAlmostEqual(captured[0]._base.gold, 2.0)
        self.assertEqual(stub.notified, ["阅读文档"], "托盘气泡通知保留")
        before_gold = state.inventory.gold
        captured[0].reward_decided.emit(Reward(gold=1.5))
        self.assertAlmostEqual(state.inventory.gold, before_gold + 1.5)
        self.assertEqual(stub.saved, 1, "关闭后应存档一次")
        widget.close()
        widget.deleteLater()
        _app.processEvents()


if __name__ == "__main__":
    unittest.main()
