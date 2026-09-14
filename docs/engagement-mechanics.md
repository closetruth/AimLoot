# 可做的成瘾 / 留存机制

面向 AimLoot 悬浮窗 + 落点连通开奖的扩展想法。按 **好做程度 × 上瘾程度** 排序，并标注建议改动的模块。

> 原则：机制应强化「真实工作时的正反馈」，避免让人为刷键而刷键。已有防护：需有活跃任务才记子任务奖励；关屏 / 空闲停表。

> **已落地（不必再做）**：开奖暴击（约 8% + 右偏倍率，见 `reward_system.py` / [probability-design.md](probability-design.md)）；缓动宝箱条；皇室战争式开箱 + 字母收集；本周运行时段视图。

文档索引：[README.md](README.md)

---

## 第一梯队：改动小、效果明显（优先）

| 机制 | 上瘾点 | 实现要点 | 主要模块 | 难度 |
|------|--------|----------|----------|------|
| **将满张力** | 簇差 1 个满（near miss）最想继续敲 | `cluster_size == span - 1` 时画布边框呼吸、落点略放大、轻 tick 音；不改概率 | `ui_roll_bar.py`, `sfx.py` | 低 |
| **Combo 连击** | 短间隔连续操作停不下来 | 距上次 op < 1.5s 则 combo+1，否则清零；画布角标 `x12`，高 combo 落点更亮 | `op_tracker.py`, `main.py`, `ui_roll_bar.py` | 低～中 |
| **开奖揭晓动画** | 老虎机式等待最上头 | 结果已算好，延迟 0.5～1s 闪动后再出 Toast | `widget.py`, `ui_roll_bar.py` | 中 |
| **每日首胜** | 每天第一次想先「拿首胜」 | 记 `last_daily_bonus_date`，当天首次开奖 gold_chance 临时 +10% 或保底少量金 | `reward_system.py`, `models.py` | 低 |

---

## 第二梯队：仍好做，上瘾略弱

| 机制 | 上瘾点 | 实现要点 | 主要模块 | 难度 |
|------|--------|----------|----------|------|
| **软 Streak** | 连续天数不想断 | 每天有 ops 或完成子目标则 +1，全局区显示；断签归零或减半（减半更温和） | `models.py`, `widget.py`, `ui_text.py` | 低 |
| **里程碑庆祝** | 整百/整千想再点几下 | `total_operations` 命中 100/500/1000 时 Toast + 音效 | `main.py`, `widget.py` | 低 |
| **稀有落点** | 像抽闪卡 | `add_roll_point` 小概率 `rare=True`，大一圈 + 金边；可选略增连通半径 | `models.py`, `reward_system.py`, `ui_roll_bar.py` | 低 |
| **数字跳动** | 看金币涨很爽 | Toast 或 global summary 金币从旧值滚动到新值 | `widget.py` | 中 |
| **簇内连线加强** | 「连起来了」更直观 | 将满时连线变亮、略粗；已部分实现，可加强动画 | `ui_roll_bar.py` | 低 |
| **since_roll 脉动** | 本周期掉落累积有盼头 | 上次开奖后 pending 金/钻变化时 chip 闪一下 | `widget.py` | 低 |

---

## 第三梯队：能做但工程量更大

| 机制 | 上瘾点 | 实现要点 | 主要模块 | 难度 |
|------|--------|----------|----------|------|
| **成就系统** | 收集癖、全成就 | `Achievement` 列表 + 条件检测 + 弹窗/历史 | 新模块 + `widget.py` | 中～高 |
| **全局等级 / 经验条** | 永远差一级升级 | `total_operations` 或有效 ops 换算 EXP，升级解锁称号/皮肤 | `models.py`, `widget.py` | 中 |
| **赛季 / Battle Pass** | 限时 FOMO | 赛季计数、等级轨道、过期重置 | 新模块 + 持久化 | 高 |
| **刮刮乐揭晓** | 手动刮开才揭晓 | 鼠标拖刮开层，底层是开奖结果 | 新 `QWidget` | 中～高 |
| **子游戏联动** | 宠物/格子战随 ops 成长 | 主 App ops 写入 session 加成 | `game_launcher.py`, games/ | 中 |
| **日终总结卡片** | 抖音式「刷完一条总结」良性版 | 退出或每日首次打开弹：今日簇、掉落、专注时长 | `widget.py`, `active_time.py` | 中 |

---

## 老虎机式（适合开奖环节）

- **多轴独立转轮**：金、钻、特殊各滚一格再停（结果可先定，动画后展示）
- **近失音效**：未中时「叮—叮—叮—唉」阶梯音
- **保底 (Pity)**：连续 N 次未中后提高概率或必掉（需持久化 `miss_streak`）
- **Jackpot 池**：未中累池、某次爆发（易诱发刷键，慎用）

当前已有：随机 `roll_span`、金/钻独立概率、右偏金额、落点画布、开奖 flash + Toast + 音效。

---

## 抖音 / 信息流式（适合任务流）

- **极短反馈**：每次 op 立即落点（已有）
- **未完成张力**：子目标 `(3/5)`、簇 `(4/6)` 停在差一点（已有骨架）
- **自动下一项**：完成子目标后自动高亮下一个，减少空档
- **个性化参数**：每 10 分钟重抽概率（已有 `reshuffle_roll_params`）
- **热度条**：1 分钟 ops 可视化（数据在 `OpRateTracker`，可画条）

---

## 进度 / 习惯类

- **番茄块奖励**：连续专注 N 分钟才给额外 roll 或 combo 加成
- **微习惯**：每天只需 1 次有效 op 即算「今日已开工」
- **预承诺对赌**：自愿押少量金币，完成子目标退还 + 奖（自律向）
- **结束仪式**：收工一键总结 + 领取 pending 动画

---

## 社交类（单机 App 优先级低）

- 本地「本周最高 ops」记录（无联网）
- 导出分享图（今日掉落截图）
- 排行榜 / 好友助力（需后端或伪数据，一般不推荐）

---

## 建议慎用

| 机制 | 原因 |
|------|------|
| 体力限制操作次数 | 妨碍真实工作 |
| 造假 near miss（结果已定还演差一格） | 挫败 + 操纵感 |
| 进度随时间不操作而回退 | 惩罚休息 |
| 强排行榜 | 易刷 `total_operations` |
| 付费抽卡循环 | 工具定位不符 |

---

## 推荐组合（按阶段）

**阶段 A（1～2 天）**

1. 将满张力  
2. 暴击掉落  
3. Combo 连击  

**阶段 B**

4. 开奖揭晓动画  
5. 每日首胜  
6. 软 Streak  

**阶段 C**

7. 成就 + 里程碑  
8. 日终总结  

---

## 与现有代码对照

| 已有能力 | 位置 |
|----------|------|
| 开奖周期 6～14 操作 | `reward_system.py`, `ui_roll_bar.py` |
| 缓动宝箱条（约 5 分钟、终点一箱、点领取入包） | `ui_roll_bar.py`, `EaseChestsState` |
| 随机间隔 6～14 | `INTERVAL_MIN/MAX` |
| 金/钻独立概率 | `maybe_roll` |
| 开奖 / 操作 / 满格 / 完成音效 | `sfx.py`, `sound_on_roll_hit`, `sound_op_chance` |
| 操作速率 | `op_tracker.py` |
| 子任务进度 | `Task.subtask_progress`, `ui_text.py` |
| 本周期掉落展示 | `since_roll`, `ui_text.py` |
| 持久化 | `storage.py` → `%APPDATA%\AimLoot\data.json` |

---

## 实现时注意

1. **新字段**走 `RollRuntime` / `AppState.settings`，保证 `from_dict` / `to_dict` 兼容旧存档。  
2. **缓动条不改 `maybe_roll`**，只发宝箱（点领取入背包）；金币/钻石仍只由开奖决定。  
3. **Combo / 里程碑**建议仅在「有活跃任务」时累计，与 `task_manager.record_operation` 对齐。  
4. 改核心逻辑后跑 `tests/`（`unittest discover`）；UI 回归用 `tests/test_widget_smoke.py`（offscreen）。
