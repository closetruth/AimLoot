"""反馈音效：Qt 播 wav/ogg/真 mp3；其它格式预热时 ffmpeg 转进 sfx_cache。"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWidgets import QApplication

from .models import Reward
from .paths import project_root

logger = logging.getLogger(__name__)

_SOUND_EXTS = (".wav", ".ogg", ".mp3", ".m4a", ".aac", ".mp4")
_NATIVE = {"mp3", "wav", "ogg"}
_GOLD_STEM = "roll_gold"
_DIAMOND_STEM = "roll_diamond"
_GRID_STEM = "grid_full"
_CHEST_STEM = "chest_get"
_RANDOM_FOLDERS = ("op", "ease", "aim")
_PREWARM_MS = 500
_MAX_VOICES = 8
_MAX_OP_VOICES = 3
_SHORT_LANES = frozenset({"op", "gold", "diamond", "grid", "chest"})
_NO_WINDOW = 0x08000000
_OP_VOLUME_SCALE = 0.4
_DEFAULT_OP_CHANCE = 0.2
_DEFAULT_GRID_EASE_CHANCE = 0.08


def _sounds_dir() -> Path:
    return project_root() / "assets" / "sounds"


def sniff_audio_kind(path: Path) -> str:
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return "unknown"
    if head.startswith(b"ID3") or (
        len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
    ):
        return "mp3"
    if head.startswith(b"RIFF"):
        return "wav"
    if head.startswith(b"OggS"):
        return "ogg"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "mp4"
    return "unknown"


def _iter_sound_dir(folder: Path) -> List[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        (
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in _SOUND_EXTS
        ),
        key=lambda p: p.name.lower(),
    )


def _stem_exists(stem: str) -> bool:
    return any((_sounds_dir() / f"{stem}{ext}").exists() for ext in _SOUND_EXTS)


def _find_stem(stem: str) -> Optional[Path]:
    for ext in _SOUND_EXTS:
        path = _sounds_dir() / f"{stem}{ext}"
        if path.exists():
            return path
    return None


def _probe_sound_files() -> bool:
    if (
        _stem_exists(_GOLD_STEM)
        or _stem_exists(_DIAMOND_STEM)
        or _stem_exists(_GRID_STEM)
        or _stem_exists(_CHEST_STEM)
    ):
        return True
    return any(_iter_sound_dir(_sounds_dir() / name) for name in _RANDOM_FOLDERS)


def _find_ffmpeg() -> Optional[str]:
    found = shutil.which("ffmpeg")
    if found:
        return found
    extra = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "ffmpeg-master-latest-win64-gpl-shared"
        / "bin"
        / "ffmpeg.exe"
    )
    return str(extra) if extra.is_file() else None


def _cache_dir() -> Path:
    from .storage import get_data_dir

    d = get_data_dir() / "sfx_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def qt_ready_path(src: Path) -> Path:
    """不能直接播的文件转成缓存 mp3；失败则退回原路径。"""
    if sniff_audio_kind(src) in _NATIVE:
        return src
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        logger.warning("未找到 ffmpeg，可能无法播放 %s", src.name)
        return src
    try:
        st = src.stat()
    except OSError:
        return src
    digest = hashlib.sha1(
        f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8", "replace")
    ).hexdigest()[:16]
    dst = _cache_dir() / f"{digest}.mp3"
    if dst.is_file() and dst.stat().st_size > 0:
        return dst
    tmp = dst.with_suffix(".tmp.mp3")
    cmd = [ffmpeg, "-y", "-i", str(src), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(tmp)]
    flags = _NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("转码失败 %s: %s", src.name, exc)
        return src
    if result.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 0:
        tmp.unlink(missing_ok=True)
        logger.warning("转码失败 %s", src.name)
        return src
    tmp.replace(dst)
    logger.info("已转码 %s", src.name)
    return dst


def _qt_types():
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    except ImportError:
        return None
    return QAudioOutput, QMediaPlayer


def _song_letter_counts(name: str) -> Dict[str, int]:
    """从歌曲文件名提取字母消耗表（重复字母计多次）。"""
    counts: Dict[str, int] = {}
    for ch in name:
        upper = ch.upper()
        if "A" <= upper <= "Z":
            counts[upper] = counts.get(upper, 0) + 1
    return counts


def bgm_song_list() -> List[Dict[str, object]]:
    """返回 aim/ 文件夹下的音乐列表，每项含 path / name / letter_counts。"""
    aim_dir = _sounds_dir() / "aim"
    files = _iter_sound_dir(aim_dir)
    result: List[Dict[str, object]] = []
    for f in files:
        stem = f.stem
        result.append({
            "path": f,
            "name": stem,
            "letter_counts": _song_letter_counts(stem),
        })
    return result


class _Voice:
    __slots__ = ("player", "output", "busy", "lane")

    def __init__(self, player: object, output: object) -> None:
        self.player = player
        self.output = output
        self.busy = False
        self.lane = ""


class SfxPlayer(QObject):
    """叠加池给开奖 / op / ease；aim/grid/chest 独占声道。无文件或关闭音效时 no-op。"""

    def __init__(self, settings: dict, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._has_files = _probe_sound_files()
        self._qt = _qt_types() if self._has_files else None
        self._pool: List[_Voice] = []
        self._busy_order: List[_Voice] = []
        self._exclusive: Dict[str, _Voice] = {}
        self._skip: set[str] = set()
        self._folder_cache: Dict[str, List[Path]] = {}
        self._prewarm_timer: Optional[QTimer] = None
        self._bgm_player: Optional[object] = None
        self._bgm_output: Optional[object] = None
        self._bgm_name: str = ""
        if QApplication.instance() is None:
            self._qt = None

    def capable(self) -> bool:
        return bool(
            self._has_files
            and self._qt is not None
            and QApplication.instance() is not None
            and self._settings.get("sound_enabled", True)
        )

    def _volume(self) -> float:
        try:
            return max(0.0, min(1.0, float(self._settings.get("sound_volume", 0.8))))
        except (TypeError, ValueError):
            return 0.8

    def _op_chance(self) -> float:
        try:
            return max(0.0, min(1.0, float(self._settings.get("sound_op_chance", _DEFAULT_OP_CHANCE))))
        except (TypeError, ValueError):
            return _DEFAULT_OP_CHANCE

    def _grid_ease_chance(self) -> float:
        try:
            return max(
                0.0,
                min(1.0, float(self._settings.get("sound_grid_ease_chance", _DEFAULT_GRID_EASE_CHANCE))),
            )
        except (TypeError, ValueError):
            return _DEFAULT_GRID_EASE_CHANCE

    def prewarm(self) -> None:
        if not self.capable():
            return
        if self._prewarm_timer is None:
            self._prewarm_timer = QTimer(self)
            self._prewarm_timer.setSingleShot(True)
            self._prewarm_timer.timeout.connect(self._prewarm_now)
        self._prewarm_timer.start(_PREWARM_MS)

    def invalidate(self) -> None:
        self._drop()
        self.stop_bgm()
        self._has_files = _probe_sound_files()
        if self._has_files and self._qt is None:
            self._qt = _qt_types()
        if self.capable():
            self.prewarm()

    def shutdown(self) -> None:
        if self._prewarm_timer is not None:
            self._prewarm_timer.stop()
        self.stop_bgm()
        self._drop()

    def _bgm_volume(self) -> float:
        try:
            return max(0.0, min(1.0, float(self._settings.get("bgm_volume", 0.5))))
        except (TypeError, ValueError):
            return 0.5

    def play_bgm(self, src: Path, name: str = "") -> None:
        """播放 BGM（循环），独占声道，与音效池互不干扰。"""
        self.stop_bgm()
        qt = _qt_types()
        if qt is None:
            return
        QAudioOutput, QMediaPlayer = qt
        ready = qt_ready_path(src)
        try:
            player = QMediaPlayer(self)
            output = QAudioOutput(self)
            player.setAudioOutput(output)
            output.setVolume(self._bgm_volume())
            player.setSource(QUrl.fromLocalFile(str(ready.resolve())))
            player.setLoops(1)
            player.play()
            player.mediaStatusChanged.connect(self._on_bgm_status)
            self._bgm_player = player
            self._bgm_output = output
            self._bgm_name = name or src.stem
            logger.info("BGM 播放: %s", self._bgm_name)
        except Exception as exc:
            logger.warning("BGM 播放失败 %s: %s", src.name, exc)
            self._bgm_player = None
            self._bgm_output = None
            self._bgm_name = ""

    def stop_bgm(self) -> None:
        """停止 BGM。"""
        if self._bgm_player is not None:
            try:
                self._bgm_player.mediaStatusChanged.disconnect(self._on_bgm_status)
            except Exception:
                pass
            try:
                self._bgm_player.stop()
                self._bgm_player.setSource(QUrl())
            except Exception:
                pass
            self._bgm_player = None
            self._bgm_output = None
            name = self._bgm_name
            self._bgm_name = ""
            if name:
                logger.info("BGM 停止: %s", name)

    def bgm_playing(self) -> bool:
        return self._bgm_player is not None

    def bgm_song_name(self) -> str:
        return self._bgm_name

    def _on_bgm_status(self, status: object) -> None:
        qt = _qt_types()
        if qt is None:
            return
        _, QMediaPlayer = qt
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            name = self._bgm_name
            self._bgm_player = None
            self._bgm_output = None
            self._bgm_name = ""
            if name:
                logger.info("BGM 播放结束: %s", name)
            self.bgm_finished.emit()

    bgm_finished = Signal()

    def play_roll_hit(self, reward: Reward) -> None:
        if not self.capable() or reward.is_empty():
            return
        if reward.gold > 0:
            gold = self._gold_path()
            if gold is not None:
                self._play("gold", gold)
        if reward.diamond > 0:
            diamond = self._diamond_path()
            if diamond is not None:
                self._play("diamond", diamond)

    def play_op_tick(self, rng: Optional[object] = None) -> None:
        if not self.capable():
            return
        picker = rng if rng is not None else random
        if picker.random() >= self._op_chance():
            return
        path = self._pick_from("op", picker)
        if path is None:
            return
        self._play("op", path, volume_scale=_OP_VOLUME_SCALE)

    def play_ease_full(self) -> None:
        if not self.capable():
            return
        path = self._pick_from("ease")
        if path is None:
            return
        self._play("ease", path)

    def play_grid_full(self, rng: Optional[object] = None) -> None:
        if not self.capable():
            return
        picker = rng if rng is not None else random
        want_ease = picker.random() < self._grid_ease_chance()
        ding = _find_stem(_GRID_STEM)
        if ding is not None:
            self._play_exclusive("grid", ding)
        if want_ease:
            self.play_ease_full()

    def play_chest_get(self) -> None:
        if not self.capable():
            return
        path = _find_stem(_CHEST_STEM)
        if path is None:
            return
        self._play_exclusive("chest", path)

    def play_goal_complete(self) -> None:
        if not self.capable():
            return
        path = self._pick_from("aim")
        if path is None:
            return
        self._play_exclusive("aim", path)

    def _gold_path(self) -> Optional[Path]:
        return _find_stem(_GOLD_STEM)

    def _diamond_path(self) -> Optional[Path]:
        return _find_stem(_DIAMOND_STEM)

    def _folder_paths(self, name: str) -> List[Path]:
        cached = self._folder_cache.get(name)
        if cached is None:
            cached = _iter_sound_dir(_sounds_dir() / name)
            self._folder_cache[name] = cached
        return cached

    def _pick_from(self, name: str, rng: Optional[object] = None) -> Optional[Path]:
        paths = [p for p in self._folder_paths(name) if str(p.resolve()) not in self._skip]
        if not paths:
            return None
        picker = rng if rng is not None else random
        return picker.choice(paths)  # type: ignore[union-attr]

    def _play(self, lane: str, src: Path, volume_scale: float = 1.0) -> None:
        voice = self._acquire(prefer_lane=lane)
        if voice is None:
            return
        self._start_voice(voice, lane, src, self._volume() * volume_scale, pooled=True)

    def _play_exclusive(self, lane: str, src: Path, volume_scale: float = 1.0) -> None:
        voice = self._exclusive.get(lane)
        if voice is None:
            voice = self._new_voice()
            if voice is None:
                return
            self._exclusive[lane] = voice
        else:
            self._stop_player(voice.player)
        self._start_voice(voice, lane, src, self._volume() * volume_scale, pooled=False)

    def _start_voice(
        self,
        voice: _Voice,
        lane: str,
        src: Path,
        volume: float,
        *,
        pooled: bool,
    ) -> None:
        ready = qt_ready_path(src)
        try:
            voice.output.setVolume(max(0.0, min(1.0, volume)))  # type: ignore[attr-defined]
            player = voice.player
            player.stop()  # type: ignore[attr-defined]
            player.setSource(QUrl())
            player.setSource(QUrl.fromLocalFile(str(ready.resolve())))
            player.setPosition(0)  # type: ignore[attr-defined]
            player.play()  # type: ignore[attr-defined]
            voice.lane = lane
            # stop/清 source 可能同步打出 EndOfMedia，把刚占用的声道放掉
            self._mark_busy(voice, pooled=pooled)
        except Exception as exc:
            logger.warning("播放失败(%s) %s: %s", lane, src.name, exc)
            self._skip.add(str(src.resolve()))
            self._release(voice)

    def _new_voice(self) -> Optional[_Voice]:
        if self._qt is None:
            return None
        QAudioOutput, QMediaPlayer = self._qt
        player = QMediaPlayer(self)
        output = QAudioOutput(self)
        player.setAudioOutput(output)
        voice = _Voice(player, output)
        player.mediaStatusChanged.connect(
            lambda status, v=voice: self._on_status(v, status)
        )
        player.errorOccurred.connect(
            lambda error, message, v=voice: self._on_error(v, error, message)
        )
        return voice

    def _on_status(self, voice: _Voice, status: object) -> None:
        if self._qt is None:
            return
        _, QMediaPlayer = self._qt
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._release(voice)

    def _on_error(self, voice: _Voice, error: object, message: object) -> None:
        logger.warning("播放失败: %s %s", error, message)
        self._release(voice)

    def _mark_busy(self, voice: _Voice, *, pooled: bool = True) -> None:
        voice.busy = True
        if not pooled:
            return
        if voice in self._busy_order:
            self._busy_order.remove(voice)
        self._busy_order.append(voice)

    def _busy_lane_count(self, lane: str) -> int:
        return sum(1 for voice in self._pool if voice.busy and voice.lane == lane)

    def _steal_from_busy(self, prefer_lane: str) -> Optional[_Voice]:
        """池满时：优先同 lane → 其它短音 → 最后 ease。"""
        for voice in self._busy_order:
            if voice.lane == prefer_lane:
                return voice
        for voice in self._busy_order:
            if voice.lane in _SHORT_LANES and voice.lane != prefer_lane:
                return voice
        for voice in self._busy_order:
            if voice.lane == "ease":
                return voice
        return self._busy_order[0] if self._busy_order else None

    def _acquire(self, prefer_lane: str = "") -> Optional[_Voice]:
        if prefer_lane == "op" and self._busy_lane_count("op") >= _MAX_OP_VOICES:
            stolen = self._steal_from_busy("op")
            if stolen is None:
                return None
            self._stop_player(stolen.player)
            self._mark_busy(stolen)
            return stolen
        for voice in self._pool:
            if not voice.busy:
                self._mark_busy(voice)
                return voice
        if len(self._pool) < _MAX_VOICES:
            voice = self._new_voice()
            if voice is None:
                return None
            self._pool.append(voice)
            self._mark_busy(voice)
            return voice
        stolen = self._steal_from_busy(prefer_lane) if prefer_lane else (
            self._busy_order[0] if self._busy_order else None
        )
        if stolen is None:
            return None
        self._stop_player(stolen.player)
        self._mark_busy(stolen)
        return stolen

    def _release(self, voice: _Voice) -> None:
        if not voice.busy:
            return
        voice.busy = False
        voice.lane = ""
        if voice in self._busy_order:
            self._busy_order.remove(voice)
        self._stop_player(voice.player)

    def _stop_player(self, player: object) -> None:
        try:
            player.stop()  # type: ignore[attr-defined]
            player.setSource(QUrl())
        except Exception:
            pass

    def _drop(self) -> None:
        voices: Sequence[_Voice] = (*self._pool, *self._exclusive.values())
        for voice in voices:
            voice.busy = False
            self._stop_player(voice.player)
        self._pool.clear()
        self._busy_order.clear()
        self._exclusive.clear()
        self._folder_cache.clear()

    def _prewarm_now(self) -> None:
        if not self.capable():
            return
        want = 0
        gold = self._gold_path()
        if gold is not None:
            qt_ready_path(gold)
            want = 1
        diamond = self._diamond_path()
        if diamond is not None:
            qt_ready_path(diamond)
            want = 2 if want else 1
        for stem in (_GRID_STEM, _CHEST_STEM):
            path = _find_stem(stem)
            if path is not None:
                qt_ready_path(path)
        for name in _RANDOM_FOLDERS:
            files = self._folder_paths(name)
            for path in files:
                qt_ready_path(path)
            if files:
                logger.info("%s 音效 %d 条", name, len(files))
        while len(self._pool) < want:
            voice = self._new_voice()
            if voice is None:
                break
            self._pool.append(voice)