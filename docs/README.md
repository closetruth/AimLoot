# 📚 文档索引

面向用户与开发者的说明入口。用户手册与安装见根目录 [README.md](../README.md)；版本变更见 [RELEASE_NOTES.md](../RELEASE_NOTES.md)。

---

## 👤 给用户

| 文档 | 说明 |
|------|------|
| 🌲 [../README.md](../README.md) | 功能、安装、使用、隐私 |
| 📝 [../RELEASE_NOTES.md](../RELEASE_NOTES.md) | 各版本变更（当前 v1.0.8） |
| 🎬 [Adventure_min_show.mp4](Adventure_min_show.mp4) | 本地演示视频 |
| 🔊 [../README.md#音效可选](../README.md#音效可选) | 音效需自行放入 `assets/sounds/` |

---

## 📊 概率与经济

| 文档 | 说明 |
|------|------|
| 🎰 [probability-design.md](probability-design.md) | 开奖 / 开箱 / 缓动条概率总览（含 SVG 图） |
| 📦 [chest-opening-probabilities.md](chest-opening-probabilities.md) | 开箱解锁、加速/秒开、字母、伴生货币公式与调参 |
| 🧮 [gen_probability_charts.py](gen_probability_charts.py) | 改参数后重跑 `img/` 下分布图 |
| 🖼️ [img/](img/) | 概率分布 SVG |

### 📈 分布图一览

| 图 | 对应机制 |
|----|----------|
| 🎲 [roll_span_uniform.svg](img/roll_span_uniform.svg) | 开奖周期 6–14 操作 |
| 📉 [roll_chance_lognormal.svg](img/roll_chance_lognormal.svg) | 金 / 钻概率重抽 |
| 💥 [crit_mult_lognormal.svg](img/crit_mult_lognormal.svg) | 暴击倍率 |
| 💰 [currency_lognormal.svg](img/currency_lognormal.svg) | 开箱伴生货币金额 |
| 🔤 [letter_count_geometric.svg](img/letter_count_geometric.svg) | 开箱字母数量 |
| 💎 [letter_rarity_weights.svg](img/letter_rarity_weights.svg) | 字母稀有度权重 |
| ⏱️ [ease_span_distribution.svg](img/ease_span_distribution.svg) | 缓动条周期跨度 |
| 📍 [ease_checkpoints.svg](img/ease_checkpoints.svg) | 缓动条检查点 |
| 🎁 [ease_rarity_weights.svg](img/ease_rarity_weights.svg) | 缓动条宝箱稀有度 |

---

## 💡 产品与机制草案

| 文档 | 说明 |
|------|------|
| 💡 [engagement-mechanics.md](engagement-mechanics.md) | 留存 / 反馈机制想法（按难度排序）；部分已落地（如暴击） |

---

## 🧩 设计规格（superpowers/specs）

已实现或近期落地的功能规格：

| 文档 | 主题 |
|------|------|
| 🏷️ [2026-09-12-aimloot-rebrand-design.md](superpowers/specs/2026-09-12-aimloot-rebrand-design.md) | 产品从 Adventure 重命名为 AimLoot（存档迁移） |
| 📅 [2026-09-02-weekly-runtime-intervals-design.md](superpowers/specs/2026-09-02-weekly-runtime-intervals-design.md) | 目标管理「本周」运行时段周视图 |
| ⚔️ [2026-09-02-word-arena-front-clash-design.md](superpowers/specs/2026-09-02-word-arena-front-clash-design.md) | 词汇自走棋：前排对撞战斗 |
| 🌱 [2026-09-02-word-arena-synergy-growth-design.md](superpowers/specs/2026-09-02-word-arena-synergy-growth-design.md) | 词汇自走棋：站位配合与成长词 |
| 📦 [2026-08-21-eased-bar-segment-fill-percent-design.md](superpowers/specs/2026-08-21-eased-bar-segment-fill-percent-design.md) | 缓动进度条填充 |
| 🔢 [2026-08-22-topbar-currency-countup-design.md](superpowers/specs/2026-08-22-topbar-currency-countup-design.md) | 顶栏金币 / 钻石滚动显示 |

## 🗺️ 实现计划（superpowers/plans）

| 文档 | 说明 |
|------|------|
| 🏷️ [2026-09-12-aimloot-rebrand.md](superpowers/plans/2026-09-12-aimloot-rebrand.md) | AimLoot 重命名与存档迁移的分步实现计划 |
| 📅 [2026-09-02-weekly-runtime-intervals.md](superpowers/plans/2026-09-02-weekly-runtime-intervals.md) | 本周时段功能的分步实现计划 |

---

## 🤖 给协作者 / AI

| 文档 | 说明 |
|------|------|
| 🤖 [../CLAUDE.md](../CLAUDE.md) | Claude Code：命令、架构、模块表 |
| 🧭 [../AGENTS.md](../AGENTS.md) | Codex / Agent：同上（与 CLAUDE 对齐） |

---

## 🗂️ 仓库内其它（一般不对外）

`.superpowers/sdd/` 下的 brief / report / diff 是某次子任务驱动开发的过程产物，不是产品文档。
