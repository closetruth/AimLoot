"""音效：文件夹随机、钻石单文件、声道池叠加、独占声道。"""
from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.models import Reward
from src.sfx import (
    SfxPlayer,
    _MAX_OP_VOICES,
    _MAX_VOICES,
    _iter_sound_dir,
    _probe_sound_files,
    _qt_types,
    qt_ready_path,
    sniff_audio_kind,
)

_app = QApplication.instance() or QApplication([])


def _write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"\x80" * 8000
    path.write_bytes(
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def _busy_count(sfx: SfxPlayer) -> int:
    return sum(1 for voice in sfx._pool if voice.busy)


def _forced_player() -> SfxPlayer:
    qt = _qt_types()
    if qt is None or QApplication.instance() is None:
        raise unittest.SkipTest("QtMultimedia 不可用")
    sfx = SfxPlayer({
        "sound_enabled": True,
        "sound_op_chance": 0.2,
        "sound_grid_ease_chance": 0.08,
    })
    sfx._has_files = True
    sfx._qt = qt
    return sfx


def _install_sounds(test: unittest.TestCase, rel_paths: list[str]) -> Path:
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    test.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    for rel in rel_paths:
        _write_tiny_wav(root / rel)
    patcher = mock.patch("src.sfx._sounds_dir", return_value=root)
    patcher.start()
    test.addCleanup(patcher.stop)
    return root


class _ChanceRng:
    def __init__(self, value: float, pick_index: int = 0) -> None:
        self.value = value
        self.pick_index = pick_index

    def random(self) -> float:
        return self.value

    def choice(self, seq):
        return seq[self.pick_index]


class SfxSniffTests(unittest.TestCase):
    def test_sniff_mp3_id3(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            handle.write(b"ID3" + b"\x00" * 20)
            path = Path(handle.name)
        try:
            self.assertEqual(sniff_audio_kind(path), "mp3")
        finally:
            path.unlink(missing_ok=True)

    def test_sniff_mp4_ftyp(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            handle.write(b"\x00\x00\x00\x18ftypisom")
            path = Path(handle.name)
        try:
            self.assertEqual(sniff_audio_kind(path), "mp4")
        finally:
            path.unlink(missing_ok=True)


class SfxAssetProbeTests(unittest.TestCase):
    def test_iter_sound_dir_sorted(self) -> None:
        root = _install_sounds(self, ["op/b.wav", "op/a.wav"])
        names = [p.name.lower() for p in _iter_sound_dir(root / "op")]
        self.assertEqual(names, ["a.wav", "b.wav"])

    def test_probe_true_when_only_op_folder(self) -> None:
        _install_sounds(self, ["op/tick.wav"])
        self.assertTrue(_probe_sound_files())

    def test_probe_true_when_roll_diamond(self) -> None:
        _install_sounds(self, ["roll_diamond.wav"])
        self.assertTrue(_probe_sound_files())

    def test_probe_ignores_diamond_folder(self) -> None:
        _install_sounds(self, ["diamond/other.wav"])
        self.assertFalse(_probe_sound_files())


class SfxSkipFilterTests(unittest.TestCase):
    def test_skip_filters_bad_folder_file(self) -> None:
        player = SfxPlayer({"sound_enabled": True})
        bad = Path("bad.mp3")
        good = Path("good.mp3")
        player._folder_cache = {"op": [bad, good]}
        player._skip.add(str(bad.resolve()))
        remain = [
            p for p in player._folder_paths("op") if str(p.resolve()) not in player._skip
        ]
        self.assertEqual(remain, [good])


class SfxQtPlayerTests(unittest.TestCase):
    def test_gold_play_prepares_lane(self) -> None:
        player = SfxPlayer({"sound_enabled": True})
        if player._gold_path() is None:
            self.skipTest("没有 roll_gold 音效文件")
        if not player.capable():
            self.skipTest("QtMultimedia 不可用")
        player.play_roll_hit(Reward(gold=0.1))
        self.assertGreaterEqual(len(player._pool), 1)
        self.assertGreaterEqual(_busy_count(player), 1)
        player.shutdown()

    def test_diamond_uses_roll_diamond_not_folder(self) -> None:
        _install_sounds(self, ["roll_diamond.wav", "diamond/other.wav"])
        player = _forced_player()
        self.addCleanup(player.shutdown)
        player.play_roll_hit(Reward(diamond=0.1))
        self.assertGreaterEqual(len(player._pool), 1)
        src = Path(player._pool[0].player.source().toLocalFile()).name
        self.assertEqual(src, "roll_diamond.wav")


class SfxOpTickChanceTests(unittest.TestCase):
    def setUp(self) -> None:
        _install_sounds(self, ["op/a.wav", "op/b.wav"])
        self.sfx = _forced_player()

    def tearDown(self) -> None:
        self.sfx.shutdown()

    def test_op_tick_plays_when_roll_below_chance(self) -> None:
        self.sfx.play_op_tick(rng=_ChanceRng(0.19))
        self.assertEqual(_busy_count(self.sfx), 1)
        self.assertEqual(self.sfx._pool[0].lane, "op")
        self.assertEqual(self.sfx._exclusive, {})

    def test_op_tick_skips_when_roll_at_chance(self) -> None:
        self.sfx.play_op_tick(rng=_ChanceRng(0.2))
        self.assertEqual(self.sfx._exclusive, {})
        self.assertEqual(self.sfx._pool, [])

    def test_op_tick_picks_folder_file(self) -> None:
        self.sfx.play_op_tick(rng=_ChanceRng(0.0, pick_index=1))
        src = Path(self.sfx._pool[0].player.source().toLocalFile()).name
        self.assertEqual(src, "b.wav")

    def test_op_tick_overlays_voices(self) -> None:
        rng = _ChanceRng(0.0)
        self.sfx.play_op_tick(rng=rng)
        self.sfx.play_op_tick(rng=rng)
        self.assertEqual(_busy_count(self.sfx), 2)
        self.assertEqual(len(self.sfx._pool), 2)
        self.assertTrue(all(v.lane == "op" for v in self.sfx._pool if v.busy))

    def test_op_tick_caps_at_max_op_voices(self) -> None:
        rng = _ChanceRng(0.0)
        for _ in range(_MAX_OP_VOICES + 2):
            self.sfx.play_op_tick(rng=rng)
        op_busy = [v for v in self.sfx._pool if v.busy and v.lane == "op"]
        self.assertEqual(len(op_busy), _MAX_OP_VOICES)

    def test_op_tick_does_not_steal_ease(self) -> None:
        _install_sounds(self, ["op/a.wav", "ease/e.wav"])
        sfx = _forced_player()
        self.addCleanup(sfx.shutdown)
        sfx.play_ease_full()
        ease_voices = [v for v in sfx._pool if v.busy and v.lane == "ease"]
        self.assertEqual(len(ease_voices), 1)
        ease_player = ease_voices[0].player
        rng = _ChanceRng(0.0)
        for _ in range(_MAX_OP_VOICES + 2):
            sfx.play_op_tick(rng=rng)
        still = [v for v in sfx._pool if v.busy and v.lane == "ease"]
        self.assertEqual(len(still), 1)
        self.assertIs(still[0].player, ease_player)
        self.assertEqual(
            sum(1 for v in sfx._pool if v.busy and v.lane == "op"),
            _MAX_OP_VOICES,
        )


class SfxExclusiveFolderTests(unittest.TestCase):
    def test_ease_overlays_aim_stays_exclusive(self) -> None:
        _install_sounds(self, ["ease/e.wav", "aim/g.wav"])
        sfx = _forced_player()
        self.addCleanup(sfx.shutdown)
        sfx.play_ease_full()
        sfx.play_ease_full()
        sfx.play_goal_complete()
        sfx.play_goal_complete()
        self.assertEqual(_busy_count(sfx), 2)
        self.assertEqual(len(sfx._exclusive), 1)
        self.assertTrue(sfx._exclusive["aim"].busy)
        self.assertEqual(len(sfx._pool), 2)


class SfxGridFullTests(unittest.TestCase):
    def test_ding_overlays_ease(self) -> None:
        _install_sounds(self, ["grid_full.wav", "ease/e.wav"])
        sfx = _forced_player()
        self.addCleanup(sfx.shutdown)
        sfx.play_grid_full(rng=_ChanceRng(0.0))
        self.assertIn("grid", sfx._exclusive)
        self.assertTrue(sfx._exclusive["grid"].busy)
        self.assertEqual(_busy_count(sfx), 1)
        src = Path(sfx._pool[0].player.source().toLocalFile()).name
        self.assertEqual(src, "e.wav")

    def test_skips_ease_when_roll_at_chance(self) -> None:
        _install_sounds(self, ["grid_full.wav", "ease/e.wav"])
        sfx = _forced_player()
        self.addCleanup(sfx.shutdown)
        sfx.play_grid_full(rng=_ChanceRng(0.08))
        self.assertIn("grid", sfx._exclusive)
        self.assertEqual(sfx._pool, [])

    def test_no_ding_plays_ease_immediately(self) -> None:
        _install_sounds(self, ["ease/e.wav"])
        sfx = _forced_player()
        self.addCleanup(sfx.shutdown)
        sfx.play_grid_full(rng=_ChanceRng(0.0))
        self.assertNotIn("grid", sfx._exclusive)
        self.assertEqual(_busy_count(sfx), 1)

    def test_chest_get_uses_stem(self) -> None:
        _install_sounds(self, ["chest_get.wav"])
        sfx = _forced_player()
        self.addCleanup(sfx.shutdown)
        sfx.play_chest_get()
        self.assertIn("chest", sfx._exclusive)
        src = Path(sfx._exclusive["chest"].player.source().toLocalFile()).name
        self.assertEqual(src, "chest_get.wav")


class SfxSlotTests(unittest.TestCase):
    """老虎机音效：循环转轮声可停、停轴咔哒、中奖/近失短音。"""

    def _player_with_slot_sounds(self) -> SfxPlayer:
        _install_sounds(
            self,
            [
                "slot_spin.wav",
                "reel_stop.wav",
                "slot_win.wav",
                "near_miss.wav",
            ],
        )
        return _forced_player()

    def test_spin_loops_on_slot_lane_and_stops(self) -> None:
        sfx = self._player_with_slot_sounds()
        self.addCleanup(sfx.shutdown)
        sfx.play_slot_spin()
        voice = sfx._exclusive.get("slot")
        self.assertIsNotNone(voice, "转轮声应占 slot 独占声道")
        self.assertEqual(voice.loops, -1, "转轮声应循环播放")
        src = Path(voice.player.source().toLocalFile()).name
        self.assertEqual(src, "slot_spin.wav")
        sfx.stop_slot_spin()
        self.assertFalse(voice.busy, "stop 后声道应空闲")
        self.assertFalse(sfx._slot_active)

    def test_stop_without_spin_is_noop(self) -> None:
        sfx = self._player_with_slot_sounds()
        self.addCleanup(sfx.shutdown)
        sfx.stop_slot_spin()  # 不应抛异常
        self.assertEqual(sfx._exclusive, {})

    def test_spin_stops_when_sound_disabled(self) -> None:
        """capable() 为假（没文件）时不应建声道。"""
        _install_sounds(self, ["op/a.wav"])  # 无 slot 文件
        sfx = _forced_player()
        self.addCleanup(sfx.shutdown)
        sfx.play_slot_spin()
        self.assertEqual(sfx._exclusive, {})

    def test_reel_stop_and_win_and_near_miss(self) -> None:
        sfx = self._player_with_slot_sounds()
        self.addCleanup(sfx.shutdown)
        sfx.play_slot_reel_stop()
        sfx.play_slot_win()
        sfx.play_slot_near_miss()
        lanes = [v.lane for v in sfx._pool if v.busy]
        self.assertIn("reel", lanes)
        self.assertIn("win", lanes)
        self.assertIn("near", lanes)


class SfxVoicePoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sfx = _forced_player()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.wav = Path(self._tmp.name) / "beep.wav"
        _write_tiny_wav(self.wav)

    def tearDown(self) -> None:
        self.sfx.shutdown()
        self._tmp.cleanup()

    def test_overlap_uses_two_voices(self) -> None:
        self.sfx._play("gold", self.wav)
        self.sfx._play("gold", self.wav)
        self.assertEqual(_busy_count(self.sfx), 2)
        self.assertEqual(len(self.sfx._pool), 2)
        self.assertIsNot(self.sfx._pool[0].player, self.sfx._pool[1].player)

    def test_reuse_idle_voice_after_end(self) -> None:
        self.sfx._play("gold", self.wav)
        self.assertEqual(len(self.sfx._pool), 1)
        self.sfx._release(self.sfx._pool[0])
        self.assertEqual(_busy_count(self.sfx), 0)
        self.sfx._play("gold", self.wav)
        self.assertEqual(_busy_count(self.sfx), 1)
        self.assertEqual(len(self.sfx._pool), 1)

    def test_end_of_media_releases_voice(self) -> None:
        self.sfx._play("gold", self.wav)
        voice = self.sfx._pool[0]
        _, QMediaPlayer = self.sfx._qt
        voice.player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertFalse(voice.busy)
        self.assertEqual(_busy_count(self.sfx), 0)

    def test_shutdown_clears_pool(self) -> None:
        self.sfx._play("gold", self.wav)
        self.sfx.shutdown()
        self.assertEqual(self.sfx._pool, [])
        self.assertEqual(self.sfx._exclusive, {})

    def test_pool_caps_at_max(self) -> None:
        for _ in range(_MAX_VOICES + 2):
            self.sfx._play("gold", self.wav)
        self.assertEqual(len(self.sfx._pool), _MAX_VOICES)
        self.assertEqual(_busy_count(self.sfx), _MAX_VOICES)


if __name__ == "__main__":
    unittest.main()
