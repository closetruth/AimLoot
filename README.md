# AimLoot

<p align="center">
  <strong>目标奖励管理工具</strong><br>
  桌面悬浮的「目标树 + 键鼠奖励」小部件<br>
  Windows 10 / 11 · 本地存档 · 三个小游戏
</p>

<p align="center">
  <a href="https://github.com/closetruth/AimLoot/releases/latest"><img src="https://img.shields.io/github/v/release/closetruth/AimLoot?label=release&color=2f6fed" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--2.0-blue" alt="License"></a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-111827" alt="Platform">
  <img src="https://img.shields.io/badge/python-3.12%20%7C%203.13-3776ab" alt="Python">
</p>

敲键盘、点鼠标、移动光标会累计操作并周期性开奖；奖励先挂在当前目标上，领进背包后才能花。目标像文件夹一样层层拆解，工作时顺手收集金币、钻石、宝箱和字母。

**当前正式版：[v1.0.8](https://github.com/closetruth/AimLoot/releases/tag/v1.0.8)** · 变更说明见 [RELEASE_NOTES.md](RELEASE_NOTES.md)

---

<h2 id="目录">📑 目录</h2>

- [🎬 演示](#演示)
- [✨ 亮点](#亮点)
- [🧩 功能说明](#功能说明)
- [🚀 快速开始](#快速开始)
- [🛠️ 开发](#开发)
- [🖱️ 使用说明](#使用说明)
- [🔒 数据与隐私](#数据与隐私)
- [📚 文档](#文档)
- [📁 项目结构](#项目结构)
- [⚖️ 许可](#许可)

---

<h2 id="演示">🎬 演示</h2>

https://github.com/user-attachments/assets/009c0b74-6018-4a5d-9810-1adb240dbb8c

B站地址：https://www.bilibili.com/video/BV1wfbJ6iExF/?spm_id_from=0.0.upload.video_card.click&vd_source=68ce22f01f3c3394fe4f021266842422

---

<h2 id="亮点">✨ 亮点</h2>

| | |
|---|---|
| 🌲 **目标树** | 大目标 / 文件夹 / 文件，像资源管理器一样展开；聚焦叶子才计时与开奖 |
| 📅 **本周视图** | 目标管理里看周一到周日真实运行时段（顶层 + 叶子） |
| 🎰 **键鼠开奖** | 每 6～14 次操作开一轮；金币 / 钻石独立判定；约 8% 暴击 |
| 📦 **缓动宝箱条** | 约 5 分钟一轮，满格停住点领；五档稀有度 |
| 💌 **开箱收集** | 解锁倒计时 + A–Z × 5 稀有度字母（130 种） |
| 🎮 **小游戏** | 小动物竞技场 · 像素格子战场 · 词汇自走棋 |

---

<h2 id="功能说明">🧩 功能说明</h2>

### 📁 目标（文件夹 / 文件）

| 比喻 | 含义 |
|------|------|
| 📂 **文件夹** | 可继续建子项；本身不直接计时，显示子项完成数与加总进度 |
| 📄 **文件** | 叶子节点，有目标分钟；做满才能完成 |
| 📊 **文件夹大小** | 子树操作数、时长、金币等自动 rollup |

- 同一时间只能有一个「进行中」的大目标
- 有子树时必须选中**文件**并点「开始运行」才会累计；路径会高亮
- **分解**：文件 → 新文件夹 + 多个新文件（旧进度可单独保留）
- 暂停 / 关屏 / 休眠 / 约 10 分钟无操作 → 不计时

### 📅 本周运行时段

目标管理 → **本周**：七列（周一～日）× 0–24 点。

- 可按顶层目标筛选（或全部）；列头显示当日合计，并给出本周合计
- 换顶层或换叶子会切段；跨午夜按自然日切开
- 图例按身份配色，对照格子颜色
- 独立日志：`%APPDATA%\AimLoot\runtime_intervals.json`

设计细节：[周视图规格](docs/superpowers/specs/2026-09-02-weekly-runtime-intervals-design.md)

### 💰 奖励与悬浮窗

- **操作**：按键 / 鼠标按下计 1 次（长按不重复）；移动约每 80 像素计 1 次；本窗口与虚拟机内也计入
- **开奖**：6～14 操作一轮；金 / 钻独立；命中后约 8% 暴击（倍率右偏）；每 10 分钟重抽概率与金额范围
- **挂账**：有子树时奖励记在当前叶子；点领取才进背包；顶栏只显示已进背包的金 / 钻
- **缓动条**：独立于开奖格；`活跃秒 + 操作÷10`；258～342 秒一轮；满格点领宝箱后再解锁倒计时
- **分段条**：彩色格 + `距下次开奖 x/y` + 有效概率；最近 3 条历史

概率总览与分布图：[docs/probability-design.md](docs/probability-design.md)

### 🎒 奖励背包与开箱

- 资产、统计、完整开奖历史、三个游戏入口
- 宝箱解锁：普通 30 分 → 传奇 8 小时；最多 4 个同时解锁（真实时间，关应用也走）
- 金币加速 / 秒开：未就绪箱可按剩余时间花金币立刻可开或秒开（跳过动画）；同稀有度可一次秒开多只。约每 5 分钟剩余 1 金（普通满时约 6 金，传奇满时约 96 金）
- 批量开箱：已就绪箱可一键批量开并跳过逐箱动画，结果以短汇总展示
- 开箱：字母数量几何分布（均值约 2）；稀有度按箱子加权；伴生货币右偏

公式与调参：[docs/chest-opening-probabilities.md](docs/chest-opening-probabilities.md)

### 🎮 小游戏

| 游戏 | 入场 | 玩法一句话 |
|------|------|------------|
| 🐾 小动物竞技场 | 10 金 | 5 槽布阵，5 vs 5 对位 |
| 👾 像素格子战场 | 12 金 | 6×4 格子对战 |
| 🔤 词汇自走棋 | 10 金 | 计算机英语词棋；铜币商店；前排对撞；站位配合与成长词 |

`ESC` 结算回背包。主程序用子进程 + JSON 会话通信。点开始词汇自走棋后目标悬浮窗仍置顶，输入继续计入当前目标。

相关设计：[前排对撞](docs/superpowers/specs/2026-09-02-word-arena-front-clash-design.md) · [站位与成长](docs/superpowers/specs/2026-09-02-word-arena-synergy-growth-design.md)

---

<h2 id="快速开始">🚀 快速开始</h2>

### 💿 正式版（推荐）

1. 🔗 打开 [Releases](https://github.com/closetruth/AimLoot/releases/latest)
2. 📦 下载 `AimLoot-vX.Y.Z.zip`（如 `AimLoot-v1.0.8.zip`）并解压
3. ▶️ 双击 `AimLoot.exe`

> SmartScreen：点「更多信息」→「仍要运行」（exe 未签名）。首次启动解压内置依赖会稍慢。
>
> 音效文件**不会随仓库或安装包附带**。把文件放到 `assets/sounds/` 下对应位置（正式版 exe 则放 `_internal/assets/sounds/`）。右键菜单可开关音效。

### 🧬 从源码运行

> 推荐 **Python 3.12 / 3.13**。Python **3.14** 必须用 `pygame-ce`（`requirements.txt` 已指定）。

```bat
install.bat
run.bat
```

小游戏起不来时可再跑 `fix_game.bat`。右键菜单可勾选开机自启。

手动：

```bat
.venv\Scripts\pythonw.exe run.py
```

---

<h2 id="开发">🛠️ 开发</h2>

### 💻 环境

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

### 🎮 游戏子进程（调试）

```bat
python run.py --game pet  <session_in.json>
python run.py --game grid <session_in.json>
python run.py --game word <session_in.json>
```

### 🧪 测试

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`tests/test_widget_smoke.py` 使用 `QT_QPA_PLATFORM=offscreen` 做 UI 回归。

### 📦 打包

```bat
build.bat
```

产物：`dist\AimLoot\AimLoot.exe`（需根目录 `AimLoot.spec`）。

> 非 Windows 无「固定到所有虚拟桌面」；macOS 上 pynput 需辅助功能权限。

---

<h2 id="使用说明">🖱️ 使用说明</h2>

| 操作 | 说明 |
|------|------|
| 🖐️ 左键拖动 | 移动悬浮窗 |
| 📋 右键 | 置顶 / 全桌面 / 开机自启 / 退出 |
| 👆 点选一行 | 选中大目标 / 文件夹 / 文件 |
| 📂 `>` / `v` | 收起 / 展开 |
| ▶️ 开始运行 | 聚焦选中的**文件**并开始累计 |
| ⏸️ 暂停 | 暂停整个大目标 |
| ✂️ 分解 | 文件 → 文件夹 + 多个新文件 |
| ➕ 添加子目标 | 选中文件夹后用底部表单新建；未选文件夹则在根下 |
| 🗂️ 任务管理 | 目标 CRUD + **本周**时段 |
| 🎒 奖励背包 | 资产 / 开箱 / 历史 / 游戏 |
| 📌 托盘单击 | 重新显示悬浮窗 |

---

<h2 id="数据与隐私">🔒 数据与隐私</h2>

| 路径 | 用途 |
|------|------|
| 💾 `%APPDATA%\AimLoot\data.json` | 主存档（目标、背包、开奖…） |
| 📅 `%APPDATA%\AimLoot\runtime_intervals.json` | 本周时段日志 |
| 🎮 `%APPDATA%\AimLoot\game_sessions\` | 小游戏临时会话 |
| 🔊 `%APPDATA%\AimLoot\sfx_cache\` | 非原生音效转码缓存 |

- 约每 **15 秒**自动保存，退出时再存一次
- 损坏时尝试 `.bak*` / `.anchor` / `.snap.*` 恢复，并备份为 `data.broken.*.json`
- 旧版 `%APPDATA%\Adventure` 会**复制**迁到 `%APPDATA%\AimLoot`（失败则继续用旧目录并提示）
- **只计次数**，不记录按键内容、坐标或前台应用名；数据仅本机，不上传

`data.json` 主要字段：`inventory`、`tasks[]`、`total_operations`、`roll_runtime`、`ease_chests`、`roll_history[]`、`settings`。实际开奖以 `roll_runtime` 为准；`settings` 里旧 roll 字段仅供迁移。常量见 `src/reward_system.py`。

### 🔊 音效（可选）

仓库**不附带音效文件**，要自己加才会出声。

操作 tick、开奖格子满格、缓动条满格只在当前目标正在计数时播放（有进行中目标；有子树时须已聚焦未完成叶子）。领取进包、完成目标不受这条限制。右键菜单可总开关音效。

| 放哪里 | 何时播放 |
|--------|----------|
| `assets/sounds/roll_gold.*` | 金币开奖 |
| `assets/sounds/roll_diamond.*` | 钻石开奖 |
| `assets/sounds/op/` 下若干短音 | 记数操作，每次 20% 随机抽一条（可叠播） |
| `assets/sounds/grid_full.*` | 离散开奖格子满格（先播） |
| `assets/sounds/ease/` 下若干音乐 | 连续进度条满格必播；离散格子满格后 8% 叠播一条 |
| `assets/sounds/chest_get.*` | 点箱子领取进背包 |
| `assets/sounds/aim/` 下若干音乐 | 完成子目标或根目标，随机抽一条 |

正式版 exe 把同样路径放到解压目录的 `_internal/assets/sounds/`。支持 wav / ogg / mp3 等；部分格式需本机 ffmpeg。

---

<h2 id="文档">📚 文档</h2>

完整目录见 **[docs/README.md](docs/README.md)**（概率图、设计规格、实现计划、协作者速查）。

| 常用入口 | 内容 |
|----------|------|
| 📝 [RELEASE_NOTES.md](RELEASE_NOTES.md) | 版本变更（当前 v1.0.8） |
| 📊 [docs/probability-design.md](docs/probability-design.md) | 开奖 / 开箱 / 缓动条概率总览 |
| 📦 [docs/chest-opening-probabilities.md](docs/chest-opening-probabilities.md) | 开箱公式与调参 |
| 💡 [docs/engagement-mechanics.md](docs/engagement-mechanics.md) | 留存 / 反馈机制草案 |
| 🤖 [CLAUDE.md](CLAUDE.md) · [AGENTS.md](AGENTS.md) | 架构与命令（给协作者 / AI） |

---

<h2 id="项目结构">📁 项目结构</h2>

```
AimLoot/
├── run.py / run.bat / install.bat / build.bat / fix_game.bat
├── AimLoot.spec · requirements.txt · LICENSE
├── assets/sounds/          # roll_gold.* / roll_diamond.* / grid_full.* / chest_get.* ；op/ ease/ aim/ 需自备
├── docs/                   # 概率、开箱、设计规格、demo
├── games/
│   ├── pet_arena.py        # 小动物竞技场
│   ├── pixel_tactics.py    # 像素格子战场
│   └── word_arena.py       # 词汇自走棋
├── tests/                  # unittest
└── src/
    ├── main.py · widget.py · models.py · storage.py
    ├── task_manager.py · task_dialog.py · inventory_dialog.py
    ├── reward_system.py · chest_opening.py · ui_roll_bar.py
    ├── runtime_intervals.py · ui_week_runtime.py
    ├── input_monitor.py · game_launcher.py · game_protocol.py
    └── …
```

可选：`install.ps1`。`run_game.bat` 仅提示用，正常从背包进游戏。

### ⚡ 性能

平时只开悬浮窗时负载很低（Windows 上定时器轮询键鼠，不装系统钩子）。开着「任务管理」狂打字可能因重建卡片略顿。小游戏在独立子进程，不拖慢主窗。

---

<h2 id="许可">⚖️ 许可</h2>

[GNU General Public License v2.0](LICENSE)
