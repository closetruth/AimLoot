# tools/

离线资源生成脚本，不参与打包，也不被 `src/` 运行时引用。

## gen_slot_sounds.py

老虎机音效离线合成：加法合成 + 软限幅 + 峰值归一化，只依赖标准库（`math` / `struct` / `wave`）。

```bat
python tools/gen_slot_sounds.py
```

依赖：**ffmpeg 在 PATH 上**（只有三轴滚动声需要，用来解码参照录音）。缺失时会报错退出，其余音效已先写出。

产物写入 `assets/sounds/`：

| 文件 | 用途 |
|------|------|
| `reel_stop.wav` | 停轴咔哒 |
| `reel_tick.wav` | 减速段单格过卡榫 |
| `near_miss.wav` / `near_miss_high.wav` / `near_miss_low.wav` | 差一点的三档反馈 |
| `suspense.wav` | 悬念心跳 |
| `slot_win.wav` | 中奖短句 |
| `slot_spin_a/b/c.wav` | 三轴滚动循环 |

`random.seed(20260101)` 固定，同一环境重跑结果一致。

## ref/

| 文件 | 说明 |
|------|------|
| `tick_tock_source.mp3` | 三轴滚动声的参照录音，**删掉就无法生成 `slot_spin_*.wav`** |

滚动声取自真实 tick-tock 录音的切片与重采样，而不是纯合成：合成的机械循环缺"活物感"，反复调参也换不来真实录音里的细微不规则。脚本会就地实测每个切片点的事件周期、在包络最低处起切、取整数个 tick-tock 对做交叉淡入，保证循环接缝安静且不错位。