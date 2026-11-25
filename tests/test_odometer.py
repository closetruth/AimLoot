"""计数器式金额：按位滚动偏移（不依赖窗口）。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ui.odometer import integer_digit_count, place_scroll


class PlaceScrollTests(unittest.TestCase):
    def test_tenths_of_10_2_is_exactly_two(self):
        digit, frac = place_scroll(10.2, 0.1)
        self.assertEqual(digit, 2)
        self.assertAlmostEqual(frac, 0.0, places=6)

    def test_resting_reel_aligns_to_tenths_grid(self):
        """开箱货币是 2 位小数（如 12.37）：静止刷新必须量化到 0.1 网格，
        十分位不能停在两数字中间。"""
        from src.ui.odometer import RollingAmount
        reel = RollingAmount("#f5c842")
        reel.set_amount(12.37)
        self.assertEqual(reel.amount(), 12.4)
        # 整数/整格值不动
        reel.set_amount(12.4)
        self.assertEqual(reel.amount(), 12.4)
        reel.set_amount(12.0)
        self.assertEqual(reel.amount(), 12.0)
        # 追赶动画的中间值保留小数（用于滚动）
        reel.set_amount_scrolling(12.34)
        self.assertEqual(reel.amount(), 12.34)

    def test_tenths_mid_roll_keeps_scroll(self):
        """追赶动画中的中间值仍应连续滚动（frac>0）。"""
        digit, frac = place_scroll(12.34, 0.1)
        self.assertEqual(digit, 3)
        self.assertGreater(frac, 0.0)

    def test_ones_of_10_2_rests_on_full_digit(self):
        digit, frac = place_scroll(10.2, 1.0)
        self.assertEqual(digit, 0)
        self.assertAlmostEqual(frac, 0.0, places=6)

    def test_tens_of_10_2_rests_on_full_digit(self):
        digit, frac = place_scroll(10.2, 10.0)
        self.assertEqual(digit, 1)
        self.assertAlmostEqual(frac, 0.0, places=6)

    def test_integer_digit_count(self):
        self.assertEqual(integer_digit_count(0.5), 1)
        self.assertEqual(integer_digit_count(10.2), 2)
        self.assertEqual(integer_digit_count(3058.0), 4)


if __name__ == "__main__":
    unittest.main()
