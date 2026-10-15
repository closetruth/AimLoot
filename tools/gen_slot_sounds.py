import math
import random
import struct
import wave

SR = 44100
LOOP_CYCLE = 1764  # 40ms；44.1k 下整周期采样数，保证首尾相接无缝


def _env(t: float, attack: float, dur: float) -> float:
    """指数衰减包络（打击感）：快起音 + 短衰减。"""
    if t < 0 or t > dur:
        return 0.0
    a = min(1.0, t / attack) if attack > 0 else 1.0
    return a * math.exp(-t * 5.5)


def _tick(dur: float = 0.09, f0: float = 2400.0) -> bytes:
    """短促"咔哒"声：带通噪声脉冲。"""
    n = int(SR * dur)
    out = bytearray()
    band = [0.0, 0.0]  # 二阶带通状态
    center = f0 / SR
    q = 2.2
    for i in range(n):
        t = i / SR
        # 白噪声 + 低频"咔"的混合
        noise = random.uniform(-1.0, 1.0) * 0.7
        click = math.sin(2 * math.pi * 180 * t) * 0.5
        x = noise + click
        # 简单谐振带通
        band[0] = band[1]
        band[1] = band[1] + q * (x - band[1] - band[0] * 0.9)
        y = band[1]
        v = y * _env(t, 0.004, dur)
        out += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32000))
    return bytes(out)


def _spin(dur: float = 1.2) -> bytes:
    """转轮循环声：低频齿轮嗡鸣 + 周期性"哒哒"脉冲。

    周期固定 40ms（LOOP_CYCLE 采样），循环播放时首尾相位一致，无咔哒接缝。
    """
    n = int(SR * dur)
    out = bytearray()
    for i in range(n):
        t = i / SR
        phase = i % LOOP_CYCLE  # 周期内相位
        ph_norm = phase / LOOP_CYCLE
        # 低频震颤（两个近频正弦拍频，形成齿轮嗡嗡声）
        # 55/60Hz 在 1.2s 内均为整数周期 → 循环接缝无爆音
        hum = (
            math.sin(2 * math.pi * 55 * t) * 0.30
            + math.sin(2 * math.pi * 60 * t) * 0.24
        )
        # 周期脉冲：每 40ms 一滚过一格的"哒"（1102.5Hz 正弦 = 周期内 44 整周期）
        tick_env = math.exp(-ph_norm * 14.0)
        tick = math.sin(2 * math.pi * 44 * ph_norm) * tick_env * 0.16
        # 高频气流噪声（转轮摩擦），幅度随周期内相位起落（无缝）
        air = random.uniform(-1.0, 1.0)
        air *= 0.06 + 0.05 * (1.0 - ph_norm)
        v = hum + tick + air
        out += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 20000))
    return bytes(out)


def _win(dur: float = 0.55) -> bytes:
    """中奖短句：C5-E5-G5-C6 上行琶音，尾音带泛音铃感。"""
    notes = [523.25, 659.25, 783.99, 1046.50]
    n = int(SR * dur)
    out = bytearray()
    note_len = dur / len(notes)
    for i in range(n):
        t = i / SR
        idx = min(int(t / note_len), len(notes) - 1)
        note_t = t - idx * note_len
        f = notes[idx]
        v = math.sin(2 * math.pi * f * t) * 0.42
        v += math.sin(2 * math.pi * f * 2 * t) * 0.12
        v *= math.exp(-note_t * 4.5)
        out += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 26000))
    return bytes(out)


def _near(dur: float = 0.32) -> bytes:
    """"差一点"上行短句：半音上行的电子音，略带下滑。"""
    notes = [523.25, 659.25, 783.99]  # C5 E5 G5
    n = int(SR * dur)
    out = bytearray()
    note_len = dur / len(notes)
    for i in range(n):
        t = i / SR
        idx = min(int(t / note_len), len(notes) - 1)
        f = notes[idx]
        # 每个音符后部轻微下滑（叹息感）
        note_t = (t % note_len) / note_len
        f *= 1.0 - 0.03 * note_t
        v = math.sin(2 * math.pi * f * t) * 0.5
        v += math.sin(2 * math.pi * f * 2 * t) * 0.15
        # 全句淡入，末尾淡出
        a = _env(t, 0.02, dur)
        v *= a
        out += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 24000))
    return bytes(out)


def _write(path: str, samples: bytes) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples)


if __name__ == "__main__":
    import os
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sounds = os.path.join(root, "assets", "sounds")
    os.makedirs(sounds, exist_ok=True)
    _write(os.path.join(sounds, "reel_stop.wav"), _tick())
    _write(os.path.join(sounds, "near_miss.wav"), _near())
    _write(os.path.join(sounds, "slot_win.wav"), _win())
    _write(os.path.join(sounds, "slot_spin.wav"), _spin())
    print("written: reel_stop.wav, near_miss.wav, slot_win.wav, slot_spin.wav")
    sys.exit(0)
