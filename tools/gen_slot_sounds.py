"""老虎机音效离线合成：加法合成（泛音配方）+ 软限幅 + 峰值归一化。

全部用 math/struct/wave 实现，不引入 numpy：离线跑一次，且不增加打包体积。
滚动声例外：它取自一段真实 tick-tock 录音（tools/ref），只做切片与重采样——
合成的机械循环缺"活物感"，反复调参也换不来真实录音里的细微不规则。
"""
import math
import os
import random
import struct
import wave

SR = 44100
PEAK = 0.891       # -1 dBFS：七个音统一响度，避免连续播放忽大忽小

_TICK_TOCK_RATE_HZ = 12.0  # 参照录音每秒 tick 数；tick-tock 成对，故周期约 2/rate
_SPIN_LOOP_SECONDS = 4.0   # 每轴循环长度目标：够长，噪声纹理不会几秒就重复一次

# 泛音配方：(频率倍率, 相对幅度)。非整数倍率产生金属/钟的失谐共鸣，
# 整数倍率产生明亮干净的"金币"音色。
_HARM = {
    "metal": ((1.00, 1.00), (2.01, 0.42), (3.49, 0.28), (5.20, 0.16)),
    "coin":  ((1.00, 1.00), (2.00, 0.50), (3.00, 0.28), (4.00, 0.14)),
    "bell":  ((1.00, 1.00), (2.00, 0.35), (3.00, 0.12), (4.70, 0.07)),
    "thump": ((1.00, 1.00), (2.00, 0.30), (0.50, 0.22)),
}


def _mix(dst: list, src: list, offset: float = 0.0, gain: float = 1.0) -> None:
    start = max(0, int(offset * SR))
    for i, v in enumerate(src):
        idx = start + i
        if idx >= len(dst):
            break
        dst[idx] += v * gain


def _soft_limit(v: float) -> float:
    """tanh 软限幅：超过 1.0 的部分被平滑压住，而不是削成平顶（平顶=刺耳）。"""
    return math.tanh(v * 1.1) / math.tanh(1.1)


def _to_bytes(samples: list, peak: float = PEAK) -> bytes:
    peak_val = max((abs(v) for v in samples), default=0.0)
    if peak_val <= 1e-9:
        return b"\x00\x00" * len(samples)
    scale = 1.0 / peak_val
    out = bytearray()
    for v in samples:
        s = _soft_limit(v * scale) * peak
        q = int(round(s * 32767 + random.uniform(-0.5, 0.5)))
        q = -32768 if q < -32768 else (32767 if q > 32767 else q)
        out += struct.pack("<h", q)
    return bytes(out)


def _write(path: str, samples: list, peak: float = PEAK) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(_to_bytes(samples, peak))


def _write_raw(path: str, samples: list, rms_dbfs: float = -24.0,
               knee: float = 0.85, ceiling: float = 0.92) -> None:
    """按 RMS 定响度，只对越过膝点的极少数尖峰做软膝压缩。

    真实录音的 crest 很高（稀疏尖峰 + 很低的底噪），三轴切到的段落 crest
    还各不相同（实测 24–28dB）。若按峰值归一化，三轴 RMS 会差到 4dB，
    叠播时听得出"某一轴更响"；而全程软限幅又会削掉 tick 的瞬态。
    所以这里先按 RMS 定标（三轴持续响度一致），再只把超过 knee 的尖峰
    用指数曲线压向 ceiling——实测受影响的样本不到 0.01%、能量损失 <0.2%，
    瞬态形状基本保留。
    """
    rms = math.sqrt(sum(v * v for v in samples) / len(samples)) if samples else 0.0
    if rms <= 1e-12:
        return
    k = (10.0 ** (rms_dbfs / 20.0)) / rms
    span = max(1e-6, ceiling - knee)

    def shape(s: float) -> float:
        a = abs(s)
        if a <= knee:
            return s
        return math.copysign(min(knee + span * (1.0 - math.exp(-(a - knee) / span)), ceiling), s)

    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(
            struct.pack("<h", int(round(shape(v * k) * 32767)))
            for v in samples
        ))


def _note(
    freq: float,
    dur: float,
    harm: tuple,
    *,
    attack: float = 0.004,
    decay: float = 5.5,
    f1: float | None = None,
    vibrato: float = 0.0,
    release: float = 0.02,
) -> list:
    """加法合成单音。相位按频率累积推进，频率变化时波形不断裂（无咔哒）。

    f1 给定时从 freq 线性滑到 f1（叹息/上行的关键）；vibrato 是 7Hz 微颤，
    让钟类音色有"活"的感觉。
    """
    n = max(1, int(SR * dur))
    total = sum(a for _, a in harm)
    phases = [random.random() * 2.0 * math.pi for _ in harm]
    out = [0.0] * n
    dt = 1.0 / SR
    for i in range(n):
        t = i * dt
        f = freq if f1 is None else freq + (f1 - freq) * (t / dur)
        if vibrato:
            f *= 1.0 + vibrato * math.sin(2.0 * math.pi * 7.0 * t)
        s = 0.0
        for j, (ratio, amp) in enumerate(harm):
            phases[j] += 2.0 * math.pi * f * ratio * dt
            s += math.sin(phases[j]) * amp
        env = min(1.0, t / attack) if attack > 0 else 1.0
        env *= math.exp(-t * decay)
        if release > 0:
            env *= min(1.0, (dur - t) / release)
        out[i] = s * env / total
    return out


def _noise_burst(dur: float, *, cutoff: float = 0.35, decay: float = 90.0) -> list:
    """带通噪声瞬态：cutoff 越小越"闷"，用作机械击发的音头。"""
    n = max(1, int(SR * dur))
    out = [0.0] * n
    y = 0.0
    for i in range(n):
        y += cutoff * (random.uniform(-1.0, 1.0) - y)
        out[i] = y * math.exp(-(i / SR) * decay)
    return out


def _reel_stop(freq: float = 1900.0, dur: float = 0.16) -> list:
    """停轴咔哒：噪声击发 + 金属共鸣尾 + 低频"咔"。"""
    out = [0.0] * int(SR * dur)
    _mix(out, _noise_burst(0.012, cutoff=0.35, decay=90.0), 0.0, 0.55)
    _mix(out, _note(freq, dur, _HARM["metal"], attack=0.002, decay=9.0, release=0.03), 0.0, 0.90)
    _mix(out, _note(180.0, 0.05, _HARM["thump"], attack=0.002, decay=22.0, release=0.01), 0.0, 0.32)
    return out


def _reel_tick(dur: float = 0.05) -> list:
    """减速段单格过卡榫：短、轻、余响极短。

    它只在相邻两声能分开时播放（每秒 ≤7 格），所以不能带长尾巴——
    一旦有 100ms 以上的共鸣，连续几声叠起来又会变回嗡鸣，等于白改。
    """
    out = [0.0] * int(SR * dur)
    _mix(out, _noise_burst(0.004, cutoff=0.5, decay=150.0), 0.0, 0.45)
    _mix(out, _note(1700.0, dur, _HARM["metal"], attack=0.001, decay=48.0, release=0.006), 0.0, 0.50)
    return out


def _win(dur: float = 0.55) -> list:
    """中奖短句：C5-E5-G5-C6 琶音，前三个用金币音色、尾音叠钟感。"""
    notes = (523.25, 659.25, 783.99, 1046.50)
    step = dur / len(notes)
    out = [0.0] * int(SR * (dur + 0.22))
    for k, f in enumerate(notes):
        harm = _HARM["coin"] if k < len(notes) - 1 else _HARM["bell"]
        tone = _note(
            f, dur - k * step + 0.18, harm,
            attack=0.004, decay=4.5, vibrato=0.004, release=0.06,
        )
        _mix(out, tone, k * step, 1.0 - 0.14 * k)
    return out


def _near(dur: float = 0.32) -> list:
    """差一点：C5-E5-G5 上行三音，钟感 + 每音轻微下滑。"""
    notes = (523.25, 659.25, 783.99)
    step = dur / len(notes)
    out = [0.0] * int(SR * (dur + 0.12))
    for k, f in enumerate(notes):
        tone = _note(
            f, step + 0.09, _HARM["bell"],
            attack=0.012, decay=7.0, f1=f * 0.97, release=0.04,
        )
        _mix(out, tone, k * step, 0.90)
    return out


def _near_high(dur: float = 0.45) -> list:
    """强近失：低频冲击 → 上行滑音 → 叹息下滑，三段情绪各占一段。"""
    out = [0.0] * int(SR * (dur + 0.15))
    _mix(out, _note(80.0, 0.10, _HARM["thump"], attack=0.003, decay=14.0, release=0.02), 0.0, 0.60)
    _mix(out, _note(523.25, 0.26, _HARM["bell"], attack=0.010, decay=5.0, f1=783.99, vibrato=0.006, release=0.05), 0.02, 0.75)
    _mix(out, _note(783.99, 0.22, _HARM["bell"], attack=0.020, decay=6.0, f1=587.33, release=0.08), 0.20, 0.55)
    return out


def _near_low(dur: float = 0.18) -> list:
    """弱近失：单个 E5 钟音，短而轻。"""
    return _note(659.25, dur, _HARM["bell"], attack=0.008, decay=13.0, release=0.05)


def _suspense_heartbeat(dur: float = 1.8) -> list:
    """悬念心跳：每个搏动是一对"咚-哒"，间隔逐次收窄（心跳加速）。"""
    out = [0.0] * int(SR * dur)
    beat = 0.0
    interval = 0.45
    while beat < dur - 0.20:
        for offset, amp in ((0.0, 1.0), (0.075, 0.55)):
            _mix(out, _note(55.0, 0.12, _HARM["thump"], attack=0.004, decay=16.0, release=0.03), beat + offset, 0.70 * amp)
            _mix(out, _note(110.0, 0.10, _HARM["thump"], attack=0.004, decay=20.0, release=0.03), beat + offset, 0.25 * amp)
        beat += interval
        interval *= 0.80
    return out


def _decode_source(path: str) -> list:
    """用 ffmpeg 把参照录音解成 44.1kHz 单声道浮点样本。"""
    import shutil
    import subprocess
    import tempfile

    exe = shutil.which("ffmpeg")
    if exe is None:
        raise RuntimeError("找不到 ffmpeg，无法解码参照录音")
    tmp = os.path.join(tempfile.gettempdir(), "aimloot_slot_src.wav")
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-i", path,
         "-ac", "1", "-ar", str(SR), "-y", tmp],
        check=True,
    )
    with wave.open(tmp, "rb") as w:
        n = w.getnframes()
        raw = struct.unpack("<%dh" % n, w.readframes(n))
    return [v / 32768.0 for v in raw]


def _measure_period(x: list, start: int, span: int,
                    lo_s: float, hi_s: float) -> float:
    """在指定区间内实测事件周期（样本数，浮点）。

    参照录音的转速并不恒定：整段平均与局部会差好几毫秒。若用全局平均
    周期去切整数个周期，几十个周期累计下来循环点两记事件就会撞在一起，
    听感就是"循环起来很奇怪"。所以每个切片点都必须就地实测周期。

    用包络自相关（帧长 64 样本）+ 抛物线插值，得到亚样本精度。
    lo_s/hi_s 是搜索的周期上下限（秒）：tick-tock 的对周期约 1/6 秒，
    所以下限放到 60ms（单个 tick 间隔），上限放到 800ms（防锁到多倍周期）。
    """
    hop = 64
    region = x[start:start + span]
    if len(region) < hop * 8:
        return SR * 2.0 / _TICK_TOCK_RATE_HZ
    env = []
    for i in range(0, len(region) - hop, hop):
        env.append(math.sqrt(sum(v * v for v in region[i:i + hop]) / hop))
    mean = sum(env) / len(env)
    env = [v - mean for v in env]

    def ac(lag: int) -> float:
        num = sum(env[i] * env[i + lag] for i in range(len(env) - lag))
        den = math.sqrt(sum(env[i] ** 2 for i in range(len(env) - lag)) *
                        sum(env[i + lag] ** 2 for i in range(len(env) - lag)))
        return num / den if den else 0.0

    lo = max(1, int(lo_s * SR / hop))
    hi = max(lo + 1, int(hi_s * SR / hop))
    best = (-2.0, lo)
    for lag in range(lo, min(hi, len(env) - 2)):
        r = ac(lag)
        if r > best[0]:
            best = (r, lag)
    lag = best[1]
    if 1 <= lag < len(env) - 1:
        a, b, c = ac(lag - 1), best[0], ac(lag + 1)
        d = a - 2.0 * b + c
        if d != 0.0:
            lag = lag + 0.5 * (a - c) / d
    return lag * hop


def _probe_pair_period(x: list) -> float:
    """实测 tick-tock 的**对周期**（tick+tock 一轮，样本数）。

    tick 与 tock 音色不同，所以包络自相关在单事件间隔处较弱、
    在一整对处最强——长区间搜索锁到的就是"对周期"。
    搜索 40–600ms 覆盖常见机械钟/转轮的速率，不依赖外部假设的 BPM。
    """
    return _measure_period(x, 0, min(len(x), int(2.5 * SR)), 0.040, 0.600)


def _quiet_start(x: list, start: int, period: float) -> int:
    """把循环起点挪到一个对周期内包络最低处（tick 与 tock 之间的间隙）。

    整数对周期仍可能有毫秒级误差，循环点若落在事件上就会听到"半声"；
    落在间隙里，即使相位略有偏差，接缝也是安静的。
    """
    hop = 64
    end = min(len(x), start + max(hop, int(round(period * 0.8))))
    best_i, best_e = start, None
    for i in range(start, end - hop + 1, hop):
        e = sum(v * v for v in x[i:i + hop])
        if best_e is None or e < best_e:
            best_e, best_i = e, i
    return best_i


def _seamless_loop(x: list, start: int, period: float, periods: int,
                   xfade: int) -> list:
    """从录音取整数个事件周期，把尾部交叉淡入头部做成无缝循环。

    循环长度必须取周期的整数倍：这样循环点两侧的事件相位完全对齐，
    交叉淡化时不会出现"两记事件错位叠成一记"的重影（听感上就是一顿一顿）。
    """
    L = int(round(period * periods))
    if start + L + xfade > len(x):
        raise RuntimeError("参照录音长度不足，无法取出 %d 个周期" % periods)
    seg = x[start:start + L + xfade]
    out = seg[:L]
    for i in range(xfade):
        w = i / xfade
        out[i] = seg[i] * w + seg[L + i] * (1.0 - w)
    return out


def _resample(x: list, ratio: float) -> list:
    """线性插值重采样。ratio>1 = 更慢更低（同一台机器走得慢一点）。"""
    n = int(len(x) / ratio)
    out = [0.0] * n
    for i in range(n):
        p = i * ratio
        j = int(p)
        f = p - j
        a = x[j]
        b = x[j + 1] if j + 1 < len(x) else x[0]
        out[i] = a + (b - a) * f
    return out


def _tick_tock_loops(src: str) -> list:
    """从真实 tick-tock 录音切出三轴循环。

    三轴各取录音里**不同位置**的片段：若三轴共用同一段再变调叠加，
    两份相同噪声在不同音高下会形成梳状干涉（缓慢的"哇哇"起伏），
    听起来像同一个声音在颤动，而不是三个转轮。换成互不相关的片段后
    只剩节奏的轻微错位，才是"三轴在转"的听感。

    变调比例只取 1.000/1.010/1.020：差值再大，相邻事件之间会以
    0.5Hz 以上的速度相互干涉，形成明显拍频，同样会听出"怪"。

    tick 与 tock 音色不同、单个事件并不等价，所以循环单位必须是
    **整数个 tick-tock 对**；周期数再取偶数，万一段落自相关锁到的是
    单个 tick，偶数也能保住 tick-tock 的交替顺序。
    """
    x = _decode_source(src)
    dur = len(x) / SR
    pair = _probe_pair_period(x)                         # 三轴共用同一对周期基准
    loop = min(_SPIN_LOOP_SECONDS, max(1.0, dur - 1.6))  # 录音偏短就缩短循环
    span = max(0.0, dur - loop - 0.5)                    # 三个起点可用的余量
    # 起点只错开 35%：录音本身不到 4s，错太开会让末轴剩不下足够的整对周期
    # （实测第三轴被挤到只有 4 对 ≈1.7s，循环点重复得太密）。
    plans = ((0.0, 1.000), (0.35, 1.010), (0.70, 1.020))
    loops = []
    for frac, ratio in plans:
        start = int(span * frac * SR)
        region = max(0, min(len(x) - start, int(2.5 * SR)))
        period = _measure_period(
            x, start, region, 0.7 * pair / SR, 1.4 * pair / SR)
        if not 0.85 * pair <= period <= 1.15 * pair:
            period = pair                                # 局部异常就退回基准
        start = _quiet_start(x, start, period)
        xfade = min(int(round(period)), int(0.20 * SR))
        # 目标对数是偶数（保住 tick-tock 交替）；但还要装得下录音剩余的
        # 整数对——装不下就退到能装下的最大偶数对，而不是硬减到只剩 2 对。
        want = 2 * int(round(loop / (2.0 * period / SR)))
        fit = 2 * int((len(x) - start - xfade) // period)
        pairs = max(2, min(want, fit))
        if pairs < 2:
            raise RuntimeError("参照录音太短，切不出三轴循环")
        loops.append(_resample(
            _seamless_loop(x, start, period, pairs, xfade), ratio))
    return loops


if __name__ == "__main__":
    import sys

    random.seed(20260101)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sounds = os.path.join(root, "assets", "sounds")
    os.makedirs(sounds, exist_ok=True)
    _write(os.path.join(sounds, "reel_stop.wav"), _reel_stop())
    _write(os.path.join(sounds, "reel_tick.wav"), _reel_tick())
    _write(os.path.join(sounds, "near_miss.wav"), _near())
    _write(os.path.join(sounds, "near_miss_high.wav"), _near_high())
    _write(os.path.join(sounds, "near_miss_low.wav"), _near_low())
    _write(os.path.join(sounds, "suspense.wav"), _suspense_heartbeat())
    _write(os.path.join(sounds, "slot_win.wav"), _win())

    src = os.path.join(root, "tools", "ref", "tick_tock_source.mp3")
    if not os.path.exists(src):
        print("缺少参照录音，无法生成三轴滚动声：%s" % src, file=sys.stderr)
        sys.exit(1)
    for name, loop in zip(("slot_spin_a.wav", "slot_spin_b.wav", "slot_spin_c.wav"),
                          _tick_tock_loops(src)):
        _write_raw(os.path.join(sounds, name), loop, rms_dbfs=-24.0)

    print("written: reel_stop.wav, reel_tick.wav, near_miss.wav, near_miss_high.wav, "
          "near_miss_low.wav, suspense.wav, slot_win.wav, slot_spin_a/b/c.wav")
    sys.exit(0)