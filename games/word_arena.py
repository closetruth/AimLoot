"""计算机词汇自走棋（Super Auto Pets 式轻量版）。

用英语计算机词汇当棋子：每种词汇自带一个与本义强关联的触发技能。
每回合商店固定 10 铜币，买词汇/食物、喂食、三合升星、调整队伍顺序；
金币可在商店花 5 金买 1 点生命。自动回合制战斗（左右对垒：我方在左、
敌方在右，每拍双方当前前排互打同时出手，回放动画），赢拿奖杯、输掉生命，
集满 10 奖杯通关。你用过的阵型会存下来当以后的敌方。

操作：
  全部用鼠标点击（商店/队伍/开战/继续）；ESC 退出并结算
  dummy 环境（SDL_VIDEODRIVER=dummy）自动模拟完整一局，便于无头验证
"""
from __future__ import annotations

import array
import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    import pygame
except ImportError:
    print("请先安装 pygame-ce（Python 3.14 必须用 ce 版）:")
    print("  pip install pygame-ce")
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from games.font_util import load_font  # noqa: E402
from src.game_protocol import GameResult, GameSession  # noqa: E402

W, H = 1100, 700
FPS = 60
ENTRY_FEE = 10
ROUND_COPPER = 10
LIFE_GOLD_COST = 5
MAX_TEAM = 5
SHOP_SLOTS = 3
FOOD_SLOTS = 2
TIER_COUNT = 5
WIN_TROPHIES = 10
MAX_LIVES = 10

# 战斗舞台：左右对垒（我方在左、敌方在右），单排 5 格，VS 居中
STAGE_X, STAGE_Y, STAGE_W, STAGE_H = 16, 70, 1068, 430
BATTLE_W, BATTLE_H, BATTLE_GAP = 96, 108, 8
BATTLE_STEP = BATTLE_W + BATTLE_GAP
BATTLE_ROW_Y = 190          # 两 lane 共用单行带
P_X0, E_X0 = 16, 572        # 我方左区起点、敌方右区起点
CENTER_X = 550              # 中线（VS 位置）
CLASH_APPROACH = 0.32       # 先走到中间
CLASH_LUNGE_OUT = 0.12
CLASH_HOLD = 0.50
CLASH_LUNGE_BACK = 0.22
SNIPE_SKILLS = frozenset({"sql", "mybatis"})

# 颜色
COL_BG = (18, 20, 30)
COL_PANEL = (33, 37, 56)
COL_CARD = (45, 50, 74)
COL_BORDER = (80, 90, 130)
COL_TEXT = (240, 242, 250)
COL_MUTED = (158, 166, 188)
COL_GOLD = (255, 213, 79)
COL_COPPER = (196, 122, 58)
COL_DIAM = (125, 211, 252)
COL_HP = (232, 86, 86)
COL_EXP = (120, 226, 255)
COL_ENEMY = (255, 120, 120)
COL_ACCENT = (110, 140, 255)
COL_STAR = (255, 205, 90)
COL_FOOD = (160, 230, 150)

TIER_NAMES = ["入门", "基础", "网络", "架构", "高级"]
TIER_COLORS = [
    (168, 176, 196),
    (108, 140, 255),
    (170, 90, 255),
    (255, 138, 128),
    (255, 213, 79),
]

# ---------- 程序合成音效 ----------
# 用波形合成（不依赖任何素材文件）；mixer 初始化失败时全部静音，游戏照常跑。
_SND: Dict[str, "pygame.mixer.Sound"] = {}
_SFX_RATE = 22050
_SFX_CHANNELS = 1


def _expand_channels(samples: array.array) -> array.array:
    """单声道样本扩展到 mixer 实际声道数（真实设备常见 2 声道）。"""
    if _SFX_CHANNELS == 1:
        return samples
    out = array.array("h")
    for v in samples:
        for _ in range(_SFX_CHANNELS):
            out.append(v)
    return out


def _synth_tone(freq: float, dur: float, shape: str = "sine", vol: float = 0.5) -> array.array:
    """合成一个短音：正弦/方波/三角 + 指数衰减包络。"""
    n = max(1, int(_SFX_RATE * dur))
    out = array.array("h")
    for i in range(n):
        t = i / _SFX_RATE
        env = math.exp(-3.2 * t / max(dur, 0.01))
        if shape == "sine":
            v = math.sin(2 * math.pi * freq * t)
        elif shape == "square":
            v = 1.0 if math.sin(2 * math.pi * freq * t) >= 0 else -1.0
        else:  # triangle
            p = (freq * t) % 1.0
            v = 4.0 * abs(p - 0.5) - 1.0
        out.append(int(32000 * vol * v * env))
    return out


def _synth_noise(dur: float, vol: float = 0.5) -> array.array:
    """白噪声（受击等冲击声）。"""
    n = max(1, int(_SFX_RATE * dur))
    out = array.array("h")
    rnd = random.Random(7)  # 固定种子，音效稳定
    for i in range(n):
        t = i / _SFX_RATE
        env = math.exp(-5.0 * t / max(dur, 0.01))
        out.append(int(32000 * vol * (rnd.random() * 2 - 1) * env))
    return out


def _synth_glide(f0: float, f1: float, dur: float, shape: str = "sine", vol: float = 0.5) -> array.array:
    """频率滑音（f0→f1）。"""
    n = max(1, int(_SFX_RATE * dur))
    out = array.array("h")
    for i in range(n):
        t = i / _SFX_RATE
        k = min(1.0, t / max(dur, 0.01))
        freq = f0 + (f1 - f0) * k
        env = math.exp(-2.5 * k)
        if shape == "sine":
            v = math.sin(2 * math.pi * freq * t)
        else:
            v = 1.0 if math.sin(2 * math.pi * freq * t) >= 0 else -1.0
        out.append(int(32000 * vol * v * env))
    return out


def _synth_arpeggio(freqs: List[float], dur: float, vol: float = 0.45) -> array.array:
    """琶音序列（胜利等）。"""
    out = array.array("h")
    step = int(_SFX_RATE * dur)
    rnd = random.Random(7)
    for freq in freqs:
        for i in range(step):
            t = i / _SFX_RATE
            env = math.exp(-3.0 * i / step)
            v = math.sin(2 * math.pi * freq * t)
            out.append(int(32000 * vol * v * env))
    return out


def _synth_cat(dur: float, vol: float = 0.5, f0: float = 880.0) -> array.array:
    """猫叫：两个快滑音（买命等俏皮音）。"""
    out = array.array("h")
    seg = int(_SFX_RATE * dur)
    rnd = random.Random(7)
    for i in range(seg * 2):
        t = i / _SFX_RATE
        k = min(1.0, (i % seg) / max(seg, 1))
        freq = f0 * (1.0 + 0.5 * (1 - k)) * (1.0 if i < seg else 0.7)
        env = math.exp(-4.0 * (i % seg) / max(seg, 1)) * (0.8 if i < seg else 0.6)
        v = math.sin(2 * math.pi * freq * t)
        out.append(int(32000 * vol * v * env))
    return out


def _init_sfx() -> None:
    """预合成全部音效。任何一步失败 → 静默禁用（_play 变成 no-op）。"""
    global _SND, _SFX_CHANNELS
    try:
        if pygame.mixer.get_init() is None:
            pygame.mixer.init(_SFX_RATE, -16, 1)
        cfg = pygame.mixer.get_init()
        if cfg is not None:
            _SFX_RATE, _fmt, _SFX_CHANNELS = cfg
        _SND = {
            "click": pygame.mixer.Sound(buffer=_expand_channels(_synth_tone(880, 0.05, "square", 0.25)).tobytes()),
            "buy": pygame.mixer.Sound(buffer=_expand_channels(_synth_glide(500, 880, 0.10, "sine", 0.35)).tobytes()),
            "sell": pygame.mixer.Sound(buffer=_expand_channels(_synth_glide(880, 330, 0.10, "sine", 0.30)).tobytes()),
            "refresh": pygame.mixer.Sound(buffer=_expand_channels(_synth_tone(1320, 0.05, "triangle", 0.28)).tobytes()),
            "freeze": pygame.mixer.Sound(buffer=_expand_channels(_synth_tone(660, 0.06, "square", 0.25)).tobytes()),
            "levelup": pygame.mixer.Sound(buffer=_expand_channels(_synth_arpeggio([523, 659, 784], 0.09, 0.4)).tobytes()),
            "life": pygame.mixer.Sound(buffer=_expand_channels(_synth_cat(0.07, 0.35)).tobytes()),
            "error": pygame.mixer.Sound(buffer=_expand_channels(_synth_tone(160, 0.12, "square", 0.3)).tobytes()),
            # 战斗
            "hit": pygame.mixer.Sound(buffer=_expand_channels(_synth_noise(0.06, 0.35)).tobytes()),
            "crit": pygame.mixer.Sound(buffer=_expand_channels(_synth_noise(0.07, 0.5)).tobytes()),
            "block": pygame.mixer.Sound(buffer=_expand_channels(_synth_tone(220, 0.08, "square", 0.3)).tobytes()),
            "faint": pygame.mixer.Sound(buffer=_expand_channels(_synth_glide(400, 120, 0.22, "sine", 0.35)).tobytes()),
            "skill": pygame.mixer.Sound(buffer=_expand_channels(_synth_tone(1568, 0.08, "triangle", 0.3)).tobytes()),
            "win": pygame.mixer.Sound(buffer=_expand_channels(_synth_arpeggio([523, 659, 784, 1047], 0.12, 0.4)).tobytes()),
            "lose": pygame.mixer.Sound(buffer=_expand_channels(_synth_arpeggio([392, 330, 262], 0.14, 0.4)).tobytes()),
        }
    except Exception:
        _SND = {}
# 商店出现概率：行=当前开放层数，列=Tier1..5（经典层级权重）
TIER_ODDS = [
    (100, 0, 0, 0, 0),
    (70, 30, 0, 0, 0),
    (60, 30, 10, 0, 0),
    (50, 32, 15, 3, 0),
    (40, 33, 20, 6, 1),
]

# 星级
STAR_BONUS = {1: 0, 2: 2, 3: 6}   # 每星额外攻/血


@dataclass
class WordSpec:
    """词库中的一种词汇（棋子原型）。"""

    word: str          # 英文单词（技能名/展示名）
    cn: str            # 中文释义
    tier: int          # 1-5
    cost: int          # 购买费用
    base_atk: int
    base_hp: int
    skill: str         # 技能 key，对应 _cast_* 方法
    skill_cn: str      # 技能中文说明
    color: Tuple[int, int, int]


# ---------- 词库：纯计算机词汇，技能=单词本义 ----------
WORDS: List[WordSpec] = []


def _w(word, cn, tier, cost, atk, hp, skill, skill_cn, color):
    WORDS.append(WordSpec(word, cn, tier, cost, atk, hp, skill, skill_cn, color))


# T1 入门
_w("bug", "缺陷/虫子", 1, 3, 2, 2, "bug", "死亡后留下一只 1/1 小虫子", (255, 140, 120))
_w("java", "Java 语言", 1, 3, 2, 3, "java", "编译执行：攻击后连击 1 次", (255, 183, 77))
_w("api", "接口", 1, 3, 2, 3, "api", "调用：开局给随机队友 +2 攻", (129, 199, 132))
_w("sql", "查询语言", 1, 3, 3, 2, "sql", "查询：攻击优先打血最少的敌人", (144, 202, 249))
_w("stack", "堆栈", 1, 2, 1, 4, "router", "压栈：受击时把 1 点伤害转给最前排", (190, 170, 255))
_w("git", "版本控制", 1, 3, 2, 3, "git", "提交：每有队友死亡 +2 攻", (255, 213, 79))
_w("shell", "外壳", 1, 2, 1, 4, "shell", "反弹：受击后对攻击者造成 1 点伤害", (178, 235, 242))
_w("loop", "循环", 1, 3, 3, 2, "loop", "重复：每回合首次攻击连打 2 次", (255, 200, 230))
_w("test", "测试", 1, 3, 2, 3, "cache", "校验：攻击满血敌人伤害×2", (255, 230, 180))
_w("input", "输入", 1, 3, 2, 3, "java", "输入流：攻击后连击 1 次", (255, 220, 200))
_w("data", "数据", 1, 2, 2, 3, "memory", "存储：开局全队 +2 血", (210, 240, 220))
_w("uid", "唯一标识", 1, 2, 1, 4, "proxy", "映射：受击时 50% 转移给随机队友", (190, 190, 240))
# T2 基础
_w("python", "Python 语言", 2, 4, 3, 3, "python", "解释执行：攻击 30% 概率伤害×2", (255, 213, 79))
_w("cache", "缓存", 2, 3, 2, 3, "cache", "命中：攻击满血敌人伤害×2", (255, 170, 120))
_w("heap", "堆内存", 2, 3, 2, 3, "heap", "分配：受击时随机队友 +1 攻", (150, 230, 170))
_w("thread", "线程", 2, 4, 3, 2, "thread", "并发：攻击 25% 概率多打一次", (255, 180, 220))
_w("class", "类", 2, 4, 2, 3, "class", "继承：继承我方最前排单位的攻击", (200, 190, 255))
_w("kernel", "内核", 2, 4, 3, 4, "kernel", "核心：开局自身 +4 攻", (255, 150, 90))
_w("array", "数组", 2, 3, 3, 2, "java", "遍历：攻击后连击 1 次", (255, 200, 140))
_w("memory", "内存", 2, 3, 2, 4, "memory", "存储：开局全队 +2 血", (190, 220, 255))
_w("hash", "哈希", 2, 3, 2, 3, "python", "散列：攻击 30% 概率伤害×2", (255, 190, 130))
_w("url", "网址", 2, 3, 2, 3, "socket", "请求：受击时全队回 1 血", (170, 210, 255))
_w("tcp", "传输协议", 2, 4, 3, 3, "router", "重传：受击时把 1 点伤害转给最前排", (255, 200, 160))
_w("file", "文件", 2, 3, 2, 4, "cookie", "存档：死亡后留下一份饼干", (200, 190, 220))
# T3 网络/运行时
_w("docker", "容器", 3, 5, 3, 3, "docker", "镜像：开局复制一份自己", (140, 210, 255))
_w("router", "路由器", 3, 4, 2, 4, "router", "转发：受击时把 1 点伤害转给最前排", (255, 190, 120))
_w("node", "节点", 3, 4, 3, 3, "node", "连接：开局给相邻队友 +1 攻", (200, 230, 150))
_w("server", "服务器", 3, 4, 1, 6, "server", "服务：开局全队 +1 血", (255, 220, 220))
_w("queue", "队列", 3, 4, 3, 3, "queue", "排队：每拍本方全体额外出手 1 次", (220, 190, 255))
_w("cookie", "会话饼干", 3, 3, 2, 3, "cookie", "会话：死亡后留下一块饼干（下次喂食效果×2）", (255, 210, 170))
_w("socket", "套接字", 3, 4, 3, 3, "socket", "连接：受击时全队回 1 血", (190, 190, 255))
_w("virus", "病毒", 3, 4, 4, 2, "virus", "感染：攻击后把 1 点伤害传染给另一个敌人", (255, 140, 200))
_w("cloud", "云端", 3, 5, 3, 4, "server", "服务：开局全队 +1 血", (160, 220, 255))
_w("script", "脚本", 3, 4, 3, 3, "pipeline", "流水：攻击后队友也攻击 1 次", (220, 245, 170))
_w("backup", "备份", 3, 4, 2, 4, "socket", "冗余：受击时全队回 1 血", (170, 200, 255))
_w("deploy", "部署", 3, 4, 3, 3, "git", "发布：每有队友死亡 +2 攻", (255, 190, 120))
_w("springboot", "Spring Boot 框架", 3, 4, 3, 3, "springboot", "自动装配：开局全队 +1 攻", (255, 205, 130))
_w("mybatis", "MyBatis 持久层", 3, 4, 3, 3, "mybatis", "映射：攻击血最少的敌人，伤害+2", (205, 235, 190))
_w("redis", "Redis 缓存", 3, 4, 2, 5, "redis", "缓存：受击时自身回 2 血", (210, 120, 120))
_w("mq", "消息队列", 3, 4, 3, 3, "mq", "异步：攻击后 50% 概率再打 1 次", (180, 215, 255))
_w("alibaba", "阿里巴巴技术栈", 3, 5, 4, 3, "alibaba", "全家桶：攻击后连击 1 次", (255, 90, 90))
# T4 架构
_w("cluster", "集群", 4, 5, 3, 4, "cluster", "分布式：开局把自身血量分摊给全队", (160, 230, 180))
_w("backend", "后端", 4, 5, 4, 4, "node", "处理：开局给相邻队友 +1 攻", (255, 170, 170))
_w("database", "数据库", 4, 5, 5, 3, "database", "查询：攻击时读取数据库，伤害+2", (180, 210, 255))
_w("firewall", "防火墙", 4, 5, 2, 6, "firewall", "拦截：开局挡掉前 2 次攻击", (255, 160, 110))
_w("proxy", "代理", 4, 5, 3, 3, "proxy", "代理：受击时 50% 转移给随机队友", (210, 190, 255))
_w("monitor", "监控", 4, 5, 3, 4, "monitor", "监控：死亡时全队回 2 血", (255, 220, 140))
_w("pipeline", "流水线", 4, 5, 4, 3, "pipeline", "流水：攻击后队友也攻击 1 次", (200, 240, 220))
_w("encrypt", "加密", 4, 5, 3, 5, "shell", "加密：受击后对攻击者造成 1 点伤害", (255, 200, 190))
_w("concurrency", "并发", 4, 5, 3, 3, "thread", "并发：攻击 25% 概率多打一次", (255, 180, 220))
_w("search", "检索", 4, 5, 4, 3, "sql", "查询：攻击优先打血最少的敌人", (210, 230, 255))
_w("balance", "负载均衡", 4, 5, 3, 4, "heap", "调度：受击时随机队友 +1 攻", (240, 220, 160))
_w("log", "日志", 4, 5, 2, 5, "monitor", "记录：死亡时全队回 2 血", (200, 230, 200))
_w("nginx", "Nginx 反向代理", 4, 5, 2, 6, "nginx", "反向代理：受击时 50% 把伤害转给最前排队友", (200, 225, 255))
_w("dubbo", "Dubbo 远程调用", 4, 5, 4, 3, "dubbo", "远程调用：攻击后 50% 概率再打 1 次", (230, 180, 255))
_w("sentinel", "Sentinel 限流", 4, 5, 2, 6, "sentinel", "限流：开局挡掉前 2 次攻击", (255, 165, 145))
_w("ruoyi", "若依框架", 4, 5, 3, 4, "ruoyi", "脚手架：死亡时全队回 2 血", (240, 210, 170))
_w("jvm", "Java 虚拟机", 4, 5, 3, 5, "jvm", "垃圾回收：开局全队 +1 血", (250, 190, 190))
_w("tomcat", "Tomcat 容器", 4, 5, 2, 6, "tomcat", "启动：开局复制一份自己", (255, 200, 150))
# T5 高级
_w("kubernetes", "容器编排", 5, 7, 4, 4, "kubernetes", "编排：开局把自身攻击分摊给全队", (255, 190, 80))
_w("crypto", "密码学", 5, 7, 4, 4, "crypto", "加密：开局隐形 1 回合", (200, 170, 255))
_w("micro", "微服务", 5, 7, 5, 3, "micro", "微服务：死亡时分裂两只半属性小服务", (170, 240, 255))
_w("quantum", "量子计算", 5, 7, 5, 3, "quantum", "量子叠加：攻击 50% 概率打 2 次", (255, 160, 255))
_w("blockchain", "区块链", 5, 7, 4, 5, "blockchain", "链式：每回合 +1 攻 +1 血（永久累计）", (255, 220, 110))
_w("botnet", "僵尸网络", 5, 7, 4, 3, "botnet", "僵尸网络：死亡时全队 +1 攻", (180, 220, 150))
_w("latency", "延迟", 5, 7, 3, 4, "crypto", "缓冲：开局隐形 1 回合", (190, 190, 250))
_w("sharding", "分片", 5, 7, 5, 3, "micro", "分片：死亡时分裂两只半属性小服务", (200, 240, 255))
_w("gpu", "图形处理器", 5, 7, 4, 4, "class", "加速：继承我方最前排单位的攻击", (200, 170, 255))
_w("replica", "副本", 5, 7, 3, 5, "server", "冗余：开局全队 +1 血", (170, 220, 255))
_w("scheduler", "调度器", 5, 7, 5, 3, "pipeline", "编排：攻击后队友也攻击 1 次", (230, 220, 160))
_w("metrics", "指标", 5, 7, 3, 4, "proxy", "采集：受击时 50% 转移给随机队友", (190, 230, 200))
_w("threadpool", "线程池", 5, 6, 5, 3, "threadpool", "复用：每次攻击连打 2 次", (200, 220, 255))
_w("volatile", "volatile 关键字", 5, 6, 4, 4, "volatile", "内存可见：开局隐形 1 回合", (200, 200, 255))
_w("hashmap", "HashMap 集合", 5, 7, 5, 5, "hashmap", "扩容：死亡时全队 +1 攻", (255, 200, 120))
# 站位配合 / 成长
_w("buffer", "缓冲区", 1, 2, 1, 3, "buffer", "开局：正后方 +2 血", (160, 210, 190))
_w("pointer", "指针", 1, 2, 2, 2, "pointer", "开局：正后方 +2 攻", (180, 200, 255))
_w("wrapper", "包装器", 1, 3, 1, 4, "wrapper", "开局：正后方护甲 1（受击减 1）", (200, 190, 170))
_w("shuffle", "洗牌", 2, 3, 2, 3, "shuffle", "开局：随机一名己方 +3 血", (220, 180, 230))
_w("callback", "回调", 2, 3, 2, 3, "callback", "开局：正前方 +2 攻", (255, 200, 140))
_w("cron", "定时任务", 2, 3, 1, 2, "cron", "每回合商店开始：自身永久 +1 攻 +1 血", (180, 220, 160))
_w("restore", "还原", 3, 4, 2, 4, "restore", "正前方第一次阵亡时，以 1 血复活", (150, 230, 200))
_w("mutex", "互斥锁", 3, 4, 2, 5, "mutex", "自身第一次将死时锁在 1 血", (200, 170, 140))
_w("watch", "监视", 3, 4, 2, 3, "watch", "刷新词汇商店：随机己方永久 +1 血", (255, 220, 150))
_w("sigkill", "强制终止", 4, 5, 4, 3, "sigkill", "第一次打中后，目标剩余血≤3 则击杀", (255, 110, 110))


WORD_BY_KEY = {w.word: w for w in WORDS}


@dataclass
class FoodSpec:
    name: str
    cn: str
    cost: int
    effect: str       # apple|honey|garlic|chocolate
    desc: str
    color: Tuple[int, int, int]


FOODS: List[FoodSpec] = [
    FoodSpec("Apple", "苹果", 3, "apple", "喂食：+1 攻 +1 血", (235, 120, 110)),
    FoodSpec("Honey", "蜂蜜", 3, "honey", "喂食：死亡时召唤 1/1 小蜜蜂", (255, 200, 100)),
    FoodSpec("Garlic", "大蒜", 3, "garlic", "喂食：受击减伤 1", (240, 240, 230)),
    FoodSpec("Chocolate", "巧克力", 4, "chocolate", "喂食：当 1 份同名升星材料", (160, 120, 90)),
]


@dataclass
class Unit:
    """一个已上场的词汇单位（战斗中可复制的战斗单位）。"""

    word: str
    cn: str
    tier: int
    atk: int
    hp: int
    max_hp: int
    skill: str
    skill_cn: str
    star: int = 1           # 1-3
    honey: bool = False     # 死亡召唤蜜蜂
    garlic: bool = False    # 受击减伤 1
    cookie: bool = False    # 死亡留饼干（下次喂食效果×2）
    buffed_atk: int = 0     # 战斗中临时攻击加成（cloned/micro 用）
    cloned: bool = False    # docker 镜像标记（镜像不带镜像）
    summon: bool = False    # 召唤物标记（蜜蜂/虫子/分裂体，不再触发产子类技能）
    dead_atk: int = 0       # git：本局死亡的队友数
    chain_atk: int = 0      # blockchain：每回合累计
    battle_extra_atk: int = 0  # database 本回合读取加成

    @classmethod
    def from_spec(cls, spec: WordSpec, star: int = 1) -> "Unit":
        b = STAR_BONUS.get(star, 0)
        atk = spec.base_atk + b
        hp = spec.base_hp + b
        return cls(
            word=spec.word, cn=spec.cn, tier=spec.tier,
            atk=atk, hp=hp, max_hp=hp,
            skill=spec.skill, skill_cn=spec.skill_cn, star=star,
        )

    @property
    def total_atk(self) -> int:
        return self.atk + self.buffed_atk + self.dead_atk + self.chain_atk + self.battle_extra_atk

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def display(self) -> str:
        return f"{self.word}({self.cn})"

    def copy_for_battle(self) -> "Unit":
        return Unit(
            word=self.word, cn=self.cn, tier=self.tier,
            atk=self.atk, hp=self.hp, max_hp=self.max_hp,
            skill=self.skill, skill_cn=self.skill_cn, star=self.star,
            honey=self.honey, garlic=self.garlic, cookie=self.cookie,
            buffed_atk=self.buffed_atk, cloned=self.cloned,
            summon=self.summon,
            dead_atk=self.dead_atk, chain_atk=self.chain_atk,
        )


def front_index(board: List[Optional[Unit]]) -> Optional[int]:
    """从槽 0 起第一个存活单位（当前前排）。"""
    for i, u in enumerate(board):
        if u is not None and u.alive:
            return i
    return None


def occupied_behind(board: List[Optional[Unit]], idx: int) -> Optional[Unit]:
    """正后方：更大槽号里下一个存活单位（跳过空槽）。"""
    for j in range(idx + 1, len(board)):
        u = board[j]
        if u is not None and u.alive:
            return u
    return None


def occupied_ahead(board: List[Optional[Unit]], idx: int) -> Optional[Unit]:
    """正前方：更小槽号里下一个存活单位（跳过空槽）。"""
    for j in range(idx - 1, -1, -1):
        u = board[j]
        if u is not None and u.alive:
            return u
    return None


def printed_atk(u: Unit) -> int:
    """商店白板攻击：词库基础 + 星级。召唤物用当前 atk。"""
    if u.summon:
        return max(0, u.atk)
    spec = WORD_BY_KEY.get(u.word)
    if spec is None:
        return max(0, u.atk)
    return spec.base_atk + STAR_BONUS.get(u.star, 0)


def printed_hp(u: Unit) -> int:
    """商店白板血量：词库基础 + 星级。召唤物用当前 max_hp。"""
    if u.summon:
        return max(1, u.max_hp)
    spec = WORD_BY_KEY.get(u.word)
    if spec is None:
        return max(1, u.max_hp)
    return spec.base_hp + STAR_BONUS.get(u.star, 0)


def bonus_atk(u: Unit) -> int:
    if u.summon:
        return max(0, u.buffed_atk + u.dead_atk + u.battle_extra_atk)
    return max(0, u.total_atk - printed_atk(u))


def bonus_hp(u: Unit) -> int:
    if u.summon:
        return 0
    return max(0, u.max_hp - printed_hp(u))


def grant_battle_hp(u: Unit, n: int) -> None:
    """本场加血：同时抬上限，满血也能加上。"""
    if n <= 0:
        return
    u.max_hp += n
    u.hp += n


def is_snipe(skill: str) -> bool:
    return skill in SNIPE_SKILLS


def extra_clash_count(unit: Unit, rng: Optional[random.Random] = None) -> int:
    """第一次互打之后，该单位再触发多少次完整互打。"""
    roll = rng.random if rng is not None else random.random
    extra = 0
    if unit.skill in ("loop", "threadpool"):
        extra += 1
    if unit.skill == "quantum" and roll() < 0.50:
        extra += 1
    if unit.skill in ("dubbo", "mq") and roll() < 0.50:
        extra += 1
    if unit.skill in ("java", "alibaba"):
        extra += 1
    if unit.skill == "thread" and roll() < 0.25:
        extra += 1
    return extra


def battle_draw_col(side: str, slot: int, max_team: int = MAX_TEAM) -> int:
    """我方槽 0 画在右侧贴中线；敌方槽 0 画在左侧贴中线。"""
    if side == "p":
        return max_team - 1 - slot
    return slot


def unit_to_dict(u: Optional[Unit]) -> Optional[dict]:
    if u is None:
        return None
    return {
        "word": u.word,
        "star": int(u.star),
        "atk": int(u.atk),
        "hp": int(u.hp),
        "max_hp": int(u.max_hp),
        "honey": bool(u.honey),
        "garlic": bool(u.garlic),
        "cookie": bool(u.cookie),
        "chain_atk": int(u.chain_atk),
    }


def unit_from_dict(data: Optional[dict]) -> Optional[Unit]:
    if not data:
        return None
    spec = WORD_BY_KEY.get(str(data.get("word", "")))
    if spec is None:
        return None
    star = max(1, min(3, int(data.get("star", 1))))
    u = Unit.from_spec(spec, star)
    u.atk = max(0, int(data.get("atk", u.atk)))
    u.max_hp = max(1, int(data.get("max_hp", u.max_hp)))
    u.hp = max(1, min(u.max_hp, int(data.get("hp", u.max_hp))))
    u.honey = bool(data.get("honey", False))
    u.garlic = bool(data.get("garlic", False))
    u.cookie = bool(data.get("cookie", False))
    u.chain_atk = max(0, int(data.get("chain_atk", 0)))
    return u


def lineup_to_dict(round_no: int, team: List[Optional[Unit]]) -> dict:
    padded = list(team) + [None] * MAX_TEAM
    return {
        "round": int(round_no),
        "units": [unit_to_dict(u) for u in padded[:MAX_TEAM]],
    }


def lineup_from_dict(data: dict) -> List[Unit]:
    raw = data.get("units") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out: List[Unit] = []
    for item in raw:
        u = unit_from_dict(item if isinstance(item, dict) else None)
        if u is not None:
            out.append(u)
    return out


@dataclass
class ShopSlot:
    kind: str = ""          # "word" | "food" | ""
    spec: object = None     # WordSpec 或 FoodSpec


class WordArenaGame:
    def __init__(self, session: GameSession):
        self.session = session
        self.initial_gold = session.gold
        self.initial_diamond = session.diamond
        self.gold = session.gold
        self.diamond = session.diamond
        self.copper = 0
        self.past_lineups: List[dict] = list(getattr(session, "word_lineups", None) or [])
        self.new_lineups: List[dict] = []

        self.phase = "title"  # title | shop | battle | battle_res | over
        self.round_no = 0
        self.trophies = 0
        self.lives = MAX_LIVES
        self.wins = 0
        self.losses = 0
        self.log = ""
        self.log_t = 0.0
        self.over_msg = ""
        self.entry_paid = False
        self.available_tiers = 1
        self.letters_awarded: List[Tuple[str, int]] = []
        self.used_letters: List[str] = []

        # 商店：词汇与食物分开刷新
        self.shop: List[ShopSlot] = [ShopSlot() for _ in range(SHOP_SLOTS)]
        self.frozen: List[bool] = [False] * SHOP_SLOTS
        self.food_shop: List[ShopSlot] = [ShopSlot() for _ in range(FOOD_SLOTS)]
        self.food_frozen: List[bool] = [False] * FOOD_SLOTS
        self.shop_initial_rolled = False

        # 队伍
        self.team: List[Optional[Unit]] = [None] * MAX_TEAM
        self.selected_slot = 0

        # 战斗
        self.battle_players: List[Optional[Unit]] = []
        self.battle_enemies: List[Optional[Unit]] = []
        self.battle_events: List[dict] = []
        self.battle_event_idx = 0
        self.battle_event_cooldown = 0.0
        self.float_texts: List[dict] = []
        self.battle_over = False
        self.battle_result_msg = ""
        self.hit_flash = {"p": 0.0, "e": 0.0}
        self.battle_focus_p = 0
        self.battle_focus_e = 0
        self._combat_p: List[Optional[Unit]] = []
        self._combat_e: List[Optional[Unit]] = []
        self._pending_faints: List[Unit] = []  # 拍内登记的死亡，拍末统一结算
        self._clash: Optional[dict] = None     # 正在模拟的互打（命中收入 clash 事件）

        # 输入
        self._click_zones: List[Tuple[pygame.Rect, str, tuple]] = []
        self._hover_zones: List[Tuple[pygame.Rect, List[str]]] = []
        self._swap_pick: Optional[int] = None
        self.over_wait = 0.0
        self.auto_dummy = "SDL_VIDEODRIVER" in __import__("os").environ and \
            __import__("os").environ.get("SDL_VIDEODRIVER") == "dummy"
        self.dummy_t = 0.0
        # cookie 技能留下的饼干（{槽位}）：下回合喂食该槽位效果×2
        self._food_cookies = set()

        # 战斗动画 fx（纯表现层，不影响战斗模拟）
        self._lunges: List[dict] = []        # 最多两人同时冲锋
        self._clash_phase: Optional[str] = None  # None | approach | lunge | hold | after
        self._clash_t: float = 0.0
        self._active_clash: Optional[dict] = None
        self.battle_inspect: Optional[Tuple[str, int]] = None
        self._faint_fx: List[dict] = []      # 死亡下沉淡出残影
        self._spawn_fx: List[dict] = []      # 召唤弹入
        self._block_fx: List[dict] = []      # 拦截/隐形/减伤浮标

        # 音效采样率/声道需在 pygame.init() 之前声明，否则 mixer 默认 44100 立体声，
        # 与合成波形格式不匹配，Sound(buffer=...) 会失败
        pygame.mixer.pre_init(_SFX_RATE, -16, 1)
        pygame.init()
        pygame.display.set_caption("AimLoot - 计算机词汇自走棋")
        self.screen = pygame.display.set_mode((W, H))
        self.clock = pygame.time.Clock()
        self.font = load_font(22)
        self.font_sm = load_font(17)
        self.font_xs = load_font(14)
        self.font_lg = load_font(30, bold=True)
        self.font_lg2 = load_font(40, bold=True)
        _init_sfx()

    def _play(self, name: str) -> None:
        """播放一个预合成音效；音频不可用时静默跳过。"""
        snd = _SND.get(name)
        if snd is not None:
            try:
                snd.play()
            except Exception:
                pass

    # ---------- 主循环 ----------
    def run(self) -> None:
        running = True
        while running:
            dt = self.clock.tick(FPS) / 1000.0
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    running = False
                elif e.type == pygame.MOUSEBUTTONDOWN:
                    if e.button == 1:
                        self._on_mouse_down(e.pos)
                elif e.type == pygame.KEYDOWN:
                    if e.key == pygame.K_ESCAPE:
                        running = False
                    elif e.key == pygame.K_SPACE:
                        self._on_space()
            if self.phase == "battle":
                self._update_battle(dt)
                self._update_float_texts(dt)
                self._update_battle_fx(dt)
            if self.log_t > 0:
                self.log_t -= dt
            if self.auto_dummy:
                self._dummy_step(dt)
            self._draw()
            if self.phase == "over" and not self.auto_dummy:
                self.over_wait += dt
                if self.over_wait >= 0.4:
                    running = False
            elif self.phase == "over" and self.auto_dummy:
                running = False
        self._write_result()

    def _on_space(self) -> None:
        if self.phase == "title":
            self._click_start_game()
        elif self.phase == "shop":
            self._click_start_battle()
        elif self.phase == "battle_res":
            self._next_round_or_end()

    def _on_mouse_down(self, pos) -> None:
        # 后注册的小按钮（冻、卖）优先于大卡片
        for rect, action, args in reversed(self._click_zones):
            if rect.collidepoint(pos):
                self._play("click")
                getattr(self, f"_click_{action}")(*args)
                return

    def _click_start_game(self) -> None:
        if self.phase != "title":
            return
        if not self.entry_paid:
            self._pay_entry()
            if not self.entry_paid:
                return
        self._start_run()

    # ---------- 阶段流转 ----------
    def _pay_entry(self) -> None:
        # 入场费由主程序启动时从背包扣除，局内不再扣一次
        self.entry_paid = True
        self._set_log(f"入场费 {ENTRY_FEE} 金币已从背包扣除")

    def _start_run(self) -> None:
        self.phase = "shop"
        self.round_no = 1
        self.available_tiers = 1
        self.copper += ROUND_COPPER
        self._roll_shop(initial=True)
        self._build_enemy_team()
        self._set_log("第 1 回合：点击商店买棋子，点「开战」")

    def _next_round_or_end(self) -> None:
        if self.trophies >= WIN_TROPHIES:
            self.phase = "over"
            self.over_msg = f"通关！{self.trophies} 奖杯"
            self._play("win")
            self._award_letters(win=True)
            return
        if self.lives <= 0:
            self.phase = "over"
            self.over_msg = f"生命耗尽：{self.trophies} 奖杯"
            self._play("lose")
            self._award_letters(win=False)
            return
        # 仁慈机制：第 3 回合起，若前两回合都输，回 1 命
        if self.round_no == 3 and self.losses >= 2:
            self.lives = min(MAX_LIVES, self.lives + 1)
            self._set_log("仁慈机制：你连输两场，回复 1 点生命")
        self.round_no += 1
        self.available_tiers = min(TIER_COUNT, 1 + (self.round_no - 1) // 2)
        self.copper += ROUND_COPPER
        self._grow_cron()
        self._roll_shop(initial=True)
        self._build_enemy_team()
        self.phase = "shop"
        self._set_log(f"第 {self.round_no} 回合：商店（解锁 {TIER_NAMES[self.available_tiers-1]} 层词汇）")

    # ---------- 商店 ----------
    def _roll_shop(self, initial: bool = False) -> bool:
        """开局/新回合：词汇和食物各刷一次，不扣铜币。"""
        self._roll_word_shop(charge=False)
        self._roll_food_shop(charge=False)
        return True

    def _roll_word_shop(self, charge: bool = True) -> bool:
        if charge:
            if self.copper <= 0:
                self._set_log("铜币不足，无法刷新词汇")
                return False
            self.copper -= 1
        odds = TIER_ODDS[self.available_tiers - 1]
        for i in range(SHOP_SLOTS):
            if self.frozen[i] and self.shop[i].spec is not None:
                continue
            tier = random.choices(range(1, TIER_COUNT + 1), weights=odds, k=1)[0]
            pool = [w for w in WORDS if w.tier == tier]
            spec = random.choice(pool) if pool else random.choice(WORDS)
            self.shop[i] = ShopSlot(kind="word", spec=spec)
            self.frozen[i] = False
        if charge:
            self._grow_watch()
        return True

    def _grow_cron(self) -> None:
        """进入下一回合商店：cron 永久 +1/+1。"""
        for u in self.team:
            if u is not None and u.skill == "cron":
                u.atk += 1
                u.max_hp += 1
                u.hp += 1

    def _grow_watch(self) -> None:
        """花铜币刷新词汇：场上有 watch 时，随机己方永久 +1 血。"""
        if not any(u is not None and u.skill == "watch" for u in self.team):
            return
        allies = [u for u in self.team if u is not None]
        pick = random.choice(allies)
        pick.max_hp += 1
        pick.hp += 1
        self._set_log(f"watch 监视：{pick.word} +1 血")

    def _roll_food_shop(self, charge: bool = True) -> bool:
        if charge:
            if self.copper <= 0:
                self._set_log("铜币不足，无法刷新食物")
                return False
            self.copper -= 1
        for i in range(FOOD_SLOTS):
            if self.food_frozen[i] and self.food_shop[i].spec is not None:
                continue
            self.food_shop[i] = ShopSlot(kind="food", spec=random.choice(FOODS))
            self.food_frozen[i] = False
        return True

    def _click_buy_shop(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.shop):
            return
        slot = self.shop[idx]
        if slot.spec is None:
            return
        if self.copper < slot.spec.cost:
            self._play("error")
            self._set_log(f"铜币不足，需要 {slot.spec.cost}")
            return
        ok = self._buy_word(slot.spec) if slot.kind == "word" else self._buy_food(slot.spec)
        if ok:
            self.shop[idx] = ShopSlot()
            self.frozen[idx] = False

    def _click_buy_food(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.food_shop):
            return
        slot = self.food_shop[idx]
        if slot.spec is None:
            return
        if self.copper < slot.spec.cost:
            self._play("error")
            self._set_log(f"铜币不足，需要 {slot.spec.cost}")
            return
        if self._buy_food(slot.spec):
            self.food_shop[idx] = ShopSlot()
            self.food_frozen[idx] = False

    def _buy_word(self, spec: WordSpec) -> bool:
        unit = Unit.from_spec(spec)
        # 先尝试同名合成
        for i, u in enumerate(self.team):
            if u is not None and u.word == spec.word and u.star < 3:
                self.copper -= spec.cost
                u.star += 1
                b = STAR_BONUS.get(u.star, 0)
                u.atk = spec.base_atk + b
                u.hp = spec.base_hp + b
                u.max_hp = u.hp
                self._play("levelup")
                self._set_log(f"升星！{spec.word} → {u.star}★（{spec.cn}）")
                return True
        # 放入空槽
        for i, u in enumerate(self.team):
            if u is None:
                self.copper -= spec.cost
                self.team[i] = unit
                self._play("buy")
                self._set_log(f"获得词汇 {spec.word}（{spec.cn}）")
                return True
        self._play("error")
        self._set_log("队伍已满，无法购买")
        return False

    def _buy_food(self, spec: FoodSpec) -> bool:
        unit = self.team[self.selected_slot]
        if unit is None:
            self._set_log("先选择要喂食的队伍槽位")
            return False
        if self.copper < spec.cost:
            self._set_log(f"铜币不足，需要 {spec.cost}")
            return False
        self.copper -= spec.cost
        # 饼干（cookie 死亡留下的）：本次喂食效果×2
        doubled = getattr(self, "_food_cookies", None) is not None and \
            self.selected_slot in self._food_cookies
        if doubled:
            self._food_cookies.discard(self.selected_slot)
            self._play("levelup")
            self._set_log("饼干：本次喂食效果×2")
        if spec.effect == "apple":
            gain = 2 if doubled else 1
            unit.atk += gain
            unit.max_hp += gain
            unit.hp += gain
        elif spec.effect == "honey":
            unit.honey = True
        elif spec.effect == "garlic":
            unit.garlic = True
        elif spec.effect == "chocolate":
            # 当 1 份同名升星材料
            if unit.star < 3:
                unit.star += 1
                b = STAR_BONUS.get(unit.star, 0)
                spec0 = WORD_BY_KEY[unit.word]
                unit.atk = spec0.base_atk + b
                unit.hp = spec0.base_hp + b
                unit.max_hp = unit.hp
                self._set_log(f"巧克力：{unit.word} → {unit.star}★")
            else:
                gain = 4 if doubled else 2
                unit.atk += gain
                unit.max_hp += gain
                unit.hp += gain
                self._set_log(f"巧克力：{unit.word} 已满星，改为 +{gain} 攻血")
        if not doubled:
            self._play("buy")
        self._set_log(f"喂食 {spec.name} 给 {unit.word}")
        return True

    def _click_sell(self, idx: int) -> None:
        if self.phase != "shop":
            return
        if idx < 0 or idx >= len(self.team):
            return
        u = self.team[idx]
        if u is None:
            self._play("error")
            self._set_log("空槽不能出售")
            return
        refund = max(1, (u.star - 1) + WORD_BY_KEY[u.word].cost // 3)
        self.copper += refund
        self.team[idx] = None
        if self.selected_slot == idx:
            self._swap_pick = None
        self._play("sell")
        self._set_log(f"出售 {u.word}，返还 {refund} 铜币")

    def _click_buy_life(self) -> None:
        if self.phase != "shop":
            return
        if self.lives >= MAX_LIVES:
            self._play("error")
            self._set_log("生命已满")
            return
        if self.gold < LIFE_GOLD_COST:
            self._play("error")
            self._set_log(f"金币不足，买命需要 {LIFE_GOLD_COST}")
            return
        self.gold -= LIFE_GOLD_COST
        self.lives += 1
        self._play("life")
        self._set_log(f"买命：-{LIFE_GOLD_COST} 金币，生命 {self.lives}")

    def _click_team_slot(self, idx: int) -> None:
        if self.phase != "shop":
            return
        if self._swap_pick is not None and self._swap_pick != idx:
            self.team[self._swap_pick], self.team[idx] = self.team[idx], self.team[self._swap_pick]
            self._swap_pick = None
            self.selected_slot = idx
            self._set_log("已交换两格位置")
            return
        self._swap_pick = idx
        self.selected_slot = idx
        self._set_log(f"选中槽位 {idx + 1}，再点另一格可换位")

    def _click_refresh(self) -> None:
        if self.phase != "shop":
            return
        if self._roll_word_shop(charge=True):
            self._play("refresh")

    def _click_refresh_food(self) -> None:
        if self.phase != "shop":
            return
        if self._roll_food_shop(charge=True):
            self._play("refresh")

    def _click_freeze(self, idx: int) -> None:
        if self.phase != "shop" or idx < 0 or idx >= len(self.shop) or self.shop[idx].spec is None:
            return
        self.frozen[idx] = not self.frozen[idx]
        self._play("freeze")
        self._set_log("已冻结" if self.frozen[idx] else "已解冻")

    def _click_freeze_food(self, idx: int) -> None:
        if self.phase != "shop" or idx < 0 or idx >= len(self.food_shop) or self.food_shop[idx].spec is None:
            return
        self.food_frozen[idx] = not self.food_frozen[idx]
        self._play("freeze")
        self._set_log("已冻结食物" if self.food_frozen[idx] else "已解冻食物")

    def _click_inspect_unit(self, side: str, slot: int) -> None:
        if self.phase not in ("battle", "battle_res"):
            return
        board = self._visual_board(side)
        if slot < 0 or slot >= len(board) or board[slot] is None:
            return
        self.battle_inspect = (side, slot)
        u = board[slot]
        self._set_log(f"{u.word}（{u.cn}）{u.skill_cn}")

    def _click_start_battle(self) -> None:
        if self.phase != "shop":
            return
        if not any(u is not None for u in self.team):
            self._set_log("至少上阵一个词汇单位")
            return
        self._start_battle()

    def _start_battle(self) -> None:
        self.new_lineups.append(lineup_to_dict(self.round_no, self.team))
        self.phase = "battle"
        self.battle_inspect = None
        self.battle_players = [u.copy_for_battle() if u else None for u in self.team]
        self.battle_enemies = [None] * MAX_TEAM
        for i, u in enumerate(self.enemy_team[:MAX_TEAM]):
            self.battle_enemies[i] = u.copy_for_battle()
        self._combat_p = [u.copy_for_battle() if u else None for u in self.battle_players]
        self._combat_e = [u.copy_for_battle() if u else None for u in self.battle_enemies]
        self.battle_events = []
        self.battle_event_idx = 0
        self.battle_event_cooldown = 0.0
        self.float_texts = []
        self.battle_over = False
        self.battle_result_msg = ""
        self.hit_flash = {"p": 0.0, "e": 0.0}
        self.battle_focus_p = 0
        self.battle_focus_e = 0
        self._build_battle_events()

    # ---------- 敌方 ----------
    def _pick_ghost_lineup(self) -> Optional[dict]:
        pool = [L for L in (self.past_lineups + self.new_lineups) if isinstance(L, dict)]
        viable = [L for L in pool if lineup_from_dict(L)]
        if not viable:
            return None
        near = [
            L for L in viable
            if abs(int(L.get("round", 1)) - self.round_no) <= 1
        ]
        return random.choice(near or viable)

    def _build_enemy_team(self) -> None:
        ghost = self._pick_ghost_lineup()
        if ghost is not None:
            self.enemy_team = lineup_from_dict(ghost)
            return
        cnt = min(3 + self.round_no // 2, MAX_TEAM)
        avail = min(TIER_COUNT, 1 + (self.round_no - 1) // 2)
        team: List[Unit] = []
        for _ in range(cnt):
            tier = random.randint(1, avail)
            pool = [w for w in WORDS if w.tier == tier]
            spec = random.choice(pool) if pool else random.choice(WORDS)
            star = 1 if self.round_no < 6 else random.choice([1, 1, 2])
            u = Unit.from_spec(spec, star)
            u.atk += self.round_no // 2
            u.max_hp += self.round_no // 2
            u.hp = u.max_hp
            team.append(u)
        self.enemy_team = team

    # ---------- 战斗（先全量模拟成事件队列，再回放） ----------
    def _alive(self, team: List[Optional[Unit]]) -> List[Unit]:
        return [u for u in team if u is not None and u.alive]

    def _build_battle_events(self) -> None:
        self.battle_events = [{"type": "start", "msg": f"第 {self.round_no} 回合战斗开始"}]
        # 开局技能（双方全员）
        for u in self._alive(self._combat_p):
            self._cast_skill(u, self._combat_p, self._combat_e)
        for u in self._alive(self._combat_e):
            self._cast_skill(u, self._combat_e, self._combat_p)

        # 前排对撞：直到一方全灭（60 次互打上限）
        guard = 0
        while self._alive(self._combat_p) and self._alive(self._combat_e) and guard < 60:
            guard += 1
            n_before = len(self.battle_events)
            self._one_combat_tick()
            if len(self.battle_events) == n_before:
                break

        if self._alive(self._combat_p) and not self._alive(self._combat_e):
            self.battle_events.append({"type": "end", "win": True, "msg": "胜利！"})
        elif self._alive(self._combat_e) and not self._alive(self._combat_p):
            self.battle_events.append({"type": "end", "win": False, "msg": "失败…"})
        else:
            self.battle_events.append({"type": "end", "win": False, "msg": "平局"})

    def _pick_target(self, attacker: Unit, foes_board: List[Optional[Unit]]) -> Optional[Unit]:
        """默认打对方当前前排；sql/mybatis 打血最少。"""
        live = self._alive(foes_board)
        if not live:
            return None
        if is_snipe(attacker.skill):
            return min(live, key=lambda t: t.hp)
        fi = front_index(foes_board)
        if fi is None:
            return None
        return foes_board[fi]

    def _mark_faint(self, u: Unit) -> None:
        """拍内登记死亡（幂等），拍末 _settle_deaths 统一结算。"""
        if u.hp > 0 or u in self._pending_faints:
            return
        self._pending_faints.append(u)

    def _settle_deaths(self) -> None:
        """拍末统一处理登记死亡的单位：faint 事件 + 清槽 + 死亡技能。"""
        pending, self._pending_faints = self._pending_faints, []
        for u in pending:
            if u.hp > 0:
                continue
            if self._try_restore(u):
                continue
            self._on_faint(u)

    def _try_restore(self, fainted: Unit) -> bool:
        """restore：正前方第一次阵亡时以 1 血复活。"""
        board = self._board_of(fainted)
        idx = next((i for i, x in enumerate(board) if x is fainted), None)
        if idx is None:
            return False
        for j in range(idx + 1, len(board)):
            r = board[j]
            if r is None or r.skill != "restore" or not r.alive:
                continue
            if getattr(r, "_restore_used", False):
                continue
            ahead = None
            for k in range(j - 1, -1, -1):
                if board[k] is not None:
                    ahead = board[k]
                    break
            if ahead is fainted:
                r._restore_used = True
                fainted.hp = 1
                self._emit({
                    "type": "skill",
                    "msg": f"{r.word} 还原：{fainted.word} 以 1 血复活",
                })
                return True
        return False

    def _snapshot_board_stats(self) -> List[dict]:
        out: List[dict] = []
        for side, board in (("p", self._combat_p), ("e", self._combat_e)):
            for i, u in enumerate(board):
                if u is None:
                    continue
                out.append({
                    "side": side,
                    "slot": i,
                    "atk": int(u.atk),
                    "hp": int(u.hp),
                    "max_hp": int(u.max_hp),
                    "buffed_atk": int(u.buffed_atk),
                    "dead_atk": int(u.dead_atk),
                    "chain_atk": int(u.chain_atk),
                    "battle_extra_atk": int(u.battle_extra_atk),
                    "armor": int(getattr(u, "_armor", 0)),
                })
        return out

    def _emit(self, ev: dict) -> None:
        """战斗事件：互打进行中则收进当前 clash，否则进全局队列。"""
        if "stats" not in ev:
            ev["stats"] = self._snapshot_board_stats()
        if self._clash is not None:
            kind = ev.get("type")
            if kind == "hit":
                self._clash["hits"].append(ev)
            elif kind == "block":
                self._clash["blocks"].append(ev)
            else:
                self._clash["after"].append(ev)
            return
        self.battle_events.append(ev)

    def _front_unit(self, board: List[Optional[Unit]]) -> Optional[Unit]:
        idx = front_index(board)
        if idx is None:
            return None
        return board[idx]

    def _begin_clash(self, p_u: Unit, e_u: Unit, p_tgt: Unit, e_tgt: Unit) -> None:
        p_side, p_slot = self._side_slot(p_u)
        e_side, e_slot = self._side_slot(e_u)
        pt_side, pt_slot = self._side_slot(p_tgt)
        et_side, et_slot = self._side_slot(e_tgt)
        self._clash = {
            "type": "clash",
            "msg": f"{p_u.word} ↔ {e_u.word}",
            "lunges": [
                {"side": p_side, "slot": p_slot, "target_side": pt_side, "target_slot": pt_slot},
                {"side": e_side, "slot": e_slot, "target_side": et_side, "target_slot": et_slot},
            ],
            "hits": [],
            "blocks": [],
            "after": [],
        }

    def _end_clash(self) -> None:
        self._settle_deaths()
        clash = self._clash
        self._clash = None
        if clash is not None:
            hit_msgs = [h.get("msg", "") for h in clash.get("hits") or [] if h.get("msg")]
            block_msgs = [b.get("msg", "") for b in clash.get("blocks") or [] if b.get("msg")]
            parts = [m for m in hit_msgs + block_msgs if m]
            if parts:
                clash["msg"] = "  ".join(parts)
            clash["stats"] = self._snapshot_board_stats()
            self.battle_events.append(clash)

    def _mutual_clash(self, p_u: Unit, e_u: Unit, *, allow_pipeline: bool = True) -> None:
        """一次完整互打：两边同时打出，再统一结算死亡。"""
        if p_u is None or e_u is None:
            return
        p_tgt = self._pick_target(p_u, self._combat_e) if is_snipe(p_u.skill) else e_u
        e_tgt = self._pick_target(e_u, self._combat_p) if is_snipe(e_u.skill) else p_u
        if p_tgt is None or e_tgt is None:
            return
        self._begin_clash(p_u, e_u, p_tgt, e_tgt)
        self._swing(p_u, p_tgt)
        self._swing(e_u, e_tgt)
        self._end_clash()
        if not allow_pipeline:
            return
        if p_u.skill == "pipeline":
            self._pipeline_bonus("p", p_u)
        if e_u.skill == "pipeline":
            self._pipeline_bonus("e", e_u)

    def _pipeline_bonus(self, side: str, attacker: Unit) -> None:
        """pipeline 触发的互打不再连锁 pipeline。"""
        board = self._combat_p if side == "p" else self._combat_e
        foes = self._combat_e if side == "p" else self._combat_p
        if not self._alive(board) or not self._alive(foes):
            return
        allies = [a for a in self._alive(board) if a is not attacker]
        enemy_front = self._front_unit(foes)
        if not allies or enemy_front is None:
            return
        partner = random.choice(allies)
        if side == "p":
            self._mutual_clash(partner, enemy_front, allow_pipeline=False)
        else:
            self._mutual_clash(enemy_front, partner, allow_pipeline=False)

    def _extra_clashes_from(self, side: str, fighter: Unit) -> None:
        n = extra_clash_count(fighter)
        for _ in range(n):
            if not fighter.alive:
                break
            p_u = self._front_unit(self._combat_p)
            e_u = self._front_unit(self._combat_e)
            if p_u is None or e_u is None:
                break
            if side == "p" and p_u is not fighter:
                break
            if side == "e" and e_u is not fighter:
                break
            self._mutual_clash(p_u, e_u)

    def _queue_wave(self, side: str) -> None:
        board = self._combat_p if side == "p" else self._combat_e
        if not self._has_skill(board, "queue"):
            return
        order = list(board)
        for u in order:
            if u is None or not u.alive:
                continue
            if not self._alive(self._combat_p) or not self._alive(self._combat_e):
                return
            p_u = u if side == "p" else self._front_unit(self._combat_p)
            e_u = u if side == "e" else self._front_unit(self._combat_e)
            if p_u is None or e_u is None:
                return
            self._mutual_clash(p_u, e_u)

    def _one_combat_tick(self) -> None:
        """前排互打 → 连击互打 → pipeline → queue 全员与对方前排互打。"""
        p_u = self._front_unit(self._combat_p)
        e_u = self._front_unit(self._combat_e)
        if p_u is None or e_u is None:
            return
        self._mutual_clash(p_u, e_u)
        if p_u.alive:
            self._extra_clashes_from("p", p_u)
        if e_u.alive:
            self._extra_clashes_from("e", e_u)
        if self._alive(self._combat_p) and self._alive(self._combat_e):
            self._queue_wave("p")
        if self._alive(self._combat_p) and self._alive(self._combat_e):
            self._queue_wave("e")

    def _has_skill(self, board: List[Optional[Unit]], skill: str) -> bool:
        return any(u is not None and u.skill == skill for u in board)

    def _board_of(self, u: Unit) -> List[Optional[Unit]]:
        if any(x is u for x in self._combat_p):
            return self._combat_p
        return self._combat_e

    def _swing(self, attacker: Unit, target: Unit) -> None:
        """一次出手的伤害（不含连击；连击是另一次互打）。"""
        dmg = max(1, attacker.total_atk)
        if attacker.skill == "mybatis":
            dmg += 2
        dmg_this = dmg
        if attacker.skill == "cache" and target.hp >= target.max_hp:
            dmg_this *= 2
        if attacker.skill == "python" and random.random() < 0.30:
            dmg_this *= 2
        if attacker.skill == "database":
            attacker.battle_extra_atk += 2
            dmg_this += 2
        self._deal_damage(attacker, target, dmg_this)
        if (
            attacker.skill == "sigkill"
            and not getattr(attacker, "_sigkill_used", False)
            and target.hp > 0
        ):
            attacker._sigkill_used = True
            if target.hp <= 3:
                leftover = target.hp
                target.hp = 0
                t_side, t_slot = self._side_slot(target)
                a_side, a_slot = self._side_slot(attacker)
                self._emit({
                    "type": "hit",
                    "msg": f"{attacker.word} 强制终止 {target.word}",
                    "target_side": t_side,
                    "target_slot": t_slot,
                    "attacker_side": a_side,
                    "attacker_slot": a_slot,
                    "damage": leftover,
                    "target_hp": 0,
                })
                self._mark_faint(target)
        if attacker.skill == "virus":
            board = self._board_of(target)
            others = [t for t in self._alive(board) if t is not target]
            if others:
                self._deal_damage(attacker, random.choice(others), 1)

    def _deal_damage(self, attacker: Unit, target: Unit, dmg: int) -> None:
        if target.hp <= 0:
            return
        t_side0, t_slot0 = self._side_slot(target)
        # 防火墙/Sentinel：拦截前 2 次攻击
        if (target.skill == "firewall" or target.skill == "sentinel") and getattr(target, "_shield", 0) > 0:
            target._shield -= 1
            self._emit({"type": "block", "msg": f"{target.word} 拦截",
                                       "target_side": t_side0, "target_slot": t_slot0})
            return
        # 隐身（crypto 开局 1 回合）
        if getattr(target, "_invisible", False):
            self._emit({"type": "block", "msg": f"{target.word} 隐形中",
                                       "target_side": t_side0, "target_slot": t_slot0})
            return
        # 大蒜 / 护甲减伤
        dmg = max(0, dmg - (1 if target.garlic else 0) - int(getattr(target, "_armor", 0)))
        if dmg <= 0:
            self._emit({"type": "block", "msg": f"{target.word} 减伤",
                                       "target_side": t_side0, "target_slot": t_slot0})
            return
        target.hp -= dmg
        if target.hp <= 0 and getattr(target, "_hp_lock", 0) > 0:
            target._hp_lock = 0
            target.hp = 1
            self._emit({
                "type": "block",
                "msg": f"{target.word} 锁血",
                "target_side": t_side0,
                "target_slot": t_slot0,
            })
        t_side, t_slot = self._side_slot(target)
        a_side, a_slot = self._side_slot(attacker)
        # socket / heap / redis 先结算，再发 hit，快照里带上 +攻/+血
        if target.alive and target.skill == "socket":
            for a in self._alive(self._board_of(target)):
                a.hp = min(a.max_hp, a.hp + 1)
            self._emit({"type": "skill", "msg": f"{target.word} 套接字：全队回 1 血"})
        if target.alive and target.skill == "heap":
            allies = [a for a in self._alive(self._board_of(target)) if a is not target]
            if allies:
                random.choice(allies).buffed_atk += 1
        if target.alive and target.skill == "redis":
            target.hp = min(target.max_hp, target.hp + 2)
            self._emit({"type": "skill", "msg": f"{target.word} 缓存命中：自身回 2 血"})
        self._emit({
            "type": "hit",
            "msg": f"{attacker.word} → {target.word} -{dmg}",
            "target_side": t_side,
            "target_slot": t_slot,
            "attacker_side": a_side,
            "attacker_slot": a_slot,
            "damage": dmg,
            "target_hp": max(0, target.hp),
        })
        # shell 反弹
        if target.alive and target.skill == "shell" and attacker.alive:
            attacker.hp -= 1
            ra_side, ra_slot = self._side_slot(attacker)
            rs_side, rs_slot = self._side_slot(target)
            self._emit({
                "type": "hit",
                "msg": f"{target.word} 反弹 -1",
                "target_side": ra_side,
                "target_slot": ra_slot,
                "attacker_side": rs_side,
                "attacker_slot": rs_slot,
                "damage": 1,
                "target_hp": max(0, attacker.hp),
            })
            if attacker.hp <= 0:
                self._mark_faint(attacker)
        # router：受击时把 1 点伤害转给最前排
        if target.alive and target.skill == "router" and attacker.alive:
            board = self._board_of(target)
            front = [a for a in self._alive(board) if a is not target]
            if front:
                front[0].hp -= 1
                f_side, f_slot = self._side_slot(front[0])
                self._emit({
                    "type": "hit",
                    "msg": f"{target.word} 路由转移",
                    "target_side": f_side,
                    "target_slot": f_slot,
                    "attacker_side": t_side,
                    "attacker_slot": t_slot,
                    "damage": 1,
                    "target_hp": max(0, front[0].hp),
                })
                if front[0].hp <= 0:
                    self._mark_faint(front[0])
        # nginx：50% 把伤害转给最前排队友
        if target.alive and target.skill == "nginx" and attacker.alive and random.random() < 0.50:
            board = self._board_of(target)
            front = [a for a in self._alive(board) if a is not target]
            if front:
                front[0].hp -= max(0, dmg)
                f_side, f_slot = self._side_slot(front[0])
                self._emit({
                    "type": "hit",
                    "msg": f"{target.word} 反向代理转移",
                    "target_side": f_side,
                    "target_slot": f_slot,
                    "attacker_side": t_side,
                    "attacker_slot": t_slot,
                    "damage": max(0, dmg),
                    "target_hp": max(0, front[0].hp),
                })
                if front[0].hp <= 0:
                    self._mark_faint(front[0])
        # proxy：50% 转移给随机队友
        if target.alive and target.skill == "proxy" and random.random() < 0.50:
            allies = [a for a in self._alive(self._board_of(target)) if a is not target]
            if allies:
                proxy = random.choice(allies)
                proxy.hp -= max(0, dmg)
                p_side, p_slot = self._side_slot(proxy)
                self._emit({
                    "type": "hit",
                    "msg": f"{target.word} 代理转移",
                    "target_side": p_side,
                    "target_slot": p_slot,
                    "attacker_side": t_side,
                    "attacker_slot": t_slot,
                    "damage": max(0, dmg),
                    "target_hp": max(0, proxy.hp),
                })
                if proxy.hp <= 0:
                    self._mark_faint(proxy)
        # 死亡检查
        if not target.alive:
            self._mark_faint(target)

    def _side_slot(self, u: Unit) -> Tuple[str, int]:
        for i, x in enumerate(self._combat_p):
            if x is u:
                return "p", i
        for i, x in enumerate(self._combat_e):
            if x is u:
                return "e", i
        return "p", 0

    def _spawn_into_board(self, board: List[Optional[Unit]], unit: Unit) -> Optional[int]:
        """把新单位放进棋盘的第一个空槽，返回槽位。"""
        for i, x in enumerate(board):
            if x is None:
                board[i] = unit
                return i
        return None

    def _unit_idx(self, u: Unit) -> int:
        for i, x in enumerate(self._combat_p):
            if x is u:
                return i
        for i, x in enumerate(self._combat_e):
            if x is u:
                return i + 100
        return 0

    def _on_faint(self, u: Unit) -> None:
        team = self._board_of(u)
        side, slot = self._side_slot(u)
        self._emit({
            "type": "faint", "msg": f"{u.word} 倒下了", "side": side, "slot": slot,
        })
        try:
            team[team.index(u)] = None
        except ValueError:
            pass
        # bug：死亡后留下一只 1/1 小虫子（本体技能，与蜂蜜召唤逻辑一致）
        # 注意：召唤物不再触发产子，防止虫海无限增殖
        if u.skill == "bug" and not u.summon:
            bee = Unit.from_spec(WORD_BY_KEY["bug"])
            bee.atk, bee.hp, bee.max_hp = 1, 1, 1
            bee.summon = True
            idx = self._spawn_into_board(team, bee)
            if idx is not None:
                self._emit({
                    "type": "spawn",
                    "msg": f"{u.word} 留下小虫子 1/1",
                    "side": side,
                    "slot": idx,
                    "unit": unit_to_dict(bee),
                })
        # cookie：死亡后留下一块饼干（下次喂食效果×2）——饼干不占槽位，
        # 记录死亡时的槽位，下回合喂食该槽位时消耗 cookie
        if u.skill == "cookie":
            self._food_cookies.add(slot)
            self._emit({"type": "skill", "msg": f"{u.word} 留下一块饼干（下次喂食效果×2）"})
        if u.honey:
            bee = Unit.from_spec(WORD_BY_KEY["bug"])
            bee.atk, bee.hp, bee.max_hp = 1, 1, 1
            bee.summon = True
            idx = self._spawn_into_board(team, bee)
            if idx is not None:
                self._emit({
                    "type": "spawn",
                    "msg": "蜂蜜：蜜蜂 1/1 出现",
                    "side": side,
                    "slot": idx,
                    "unit": unit_to_dict(bee),
                })
        if u.cookie:
            self._emit({"type": "spawn", "msg": "会话饼干已留下"})
        if u.skill == "monitor":
            for a in self._alive(team):
                a.hp = min(a.max_hp, a.hp + 2)
            self._emit({"type": "spawn", "msg": "监控：全队回 2 血"})
        if u.skill == "botnet":
            for a in self._alive(team):
                a.buffed_atk += 1
            self._emit({"type": "spawn", "msg": "僵尸网络：全队 +1 攻"})
        if u.skill == "ruoyi":
            for a in self._alive(team):
                a.hp = min(a.max_hp, a.hp + 2)
            self._emit({"type": "spawn", "msg": "若依脚手架：全队回 2 血"})
        if u.skill == "hashmap":
            for a in self._alive(team):
                a.buffed_atk += 1
            self._emit({"type": "spawn", "msg": "HashMap 扩容：全队 +1 攻"})
        if u.skill == "micro":
            for _ in range(2):
                m = Unit.from_spec(WORD_BY_KEY["java"])
                m.atk = max(1, u.atk // 2)
                m.hp = max(1, u.max_hp // 2)
                m.max_hp = m.hp
                m.summon = True
                idx = self._spawn_into_board(team, m)
                if idx is not None:
                    self._emit({
                        "type": "spawn",
                        "msg": "微服务分裂",
                        "side": side,
                        "slot": idx,
                        "unit": unit_to_dict(m),
                    })
        if team is self._combat_p:
            for a in self._alive(team):
                if a.skill == "git" and a is not u:
                    a.dead_atk += 2

    def _cast_skill(self, u: Unit, own: List[Optional[Unit]], foes: List[Optional[Unit]]) -> None:
        """开局技能。"""
        own_i = next((i for i, x in enumerate(own) if x is u), None)
        if u.skill == "api":
            allies = [a for a in self._alive(own) if a is not u]
            if allies:
                random.choice(allies).buffed_atk += 2
                self._emit({"type": "skill", "msg": f"{u.word} 调用接口：队友 +2 攻"})
        elif u.skill == "kernel":
            u.buffed_atk += 4
            self._emit({"type": "skill", "msg": f"{u.word} 核心强化 +4 攻"})
        elif u.skill == "memory":
            for a in self._alive(own):
                grant_battle_hp(a, 2)
            self._emit({"type": "skill", "msg": f"{u.word}：全队 +2 血"})
        elif u.skill == "docker":
            if not u.cloned:
                clone = u.copy_for_battle()
                clone.cloned = True
                clone.summon = True
                idx = self._spawn_into_board(own, clone)
                if idx is not None:
                    side = "p" if own is self._combat_p else "e"
                    self._emit({
                        "type": "spawn",
                        "msg": f"{u.word} 镜像复制",
                        "side": side,
                        "slot": idx,
                        "unit": unit_to_dict(clone),
                    })
        elif u.skill == "node":
            if own_i is not None:
                for j in (own_i - 1, own_i + 1):
                    if 0 <= j < len(own) and own[j] is not None and own[j] is not u:
                        own[j].buffed_atk += 1
                self._emit({"type": "skill", "msg": f"{u.word} 节点连接：相邻 +1 攻"})
        elif u.skill == "server":
            for a in self._alive(own):
                grant_battle_hp(a, 1)
            self._emit({"type": "skill", "msg": f"{u.word}：全队 +1 血"})
        elif u.skill == "class":
            front = self._alive(own)
            if front:
                u.buffed_atk = max(u.buffed_atk, front[0].total_atk)
                self._emit({"type": "skill", "msg": f"{u.word} 继承前排攻击"})
        elif u.skill == "cluster":
            allies = [a for a in self._alive(own) if a is not u]
            if allies:
                share = u.hp // 4
                for a in allies:
                    grant_battle_hp(a, share)
                self._emit({"type": "skill", "msg": f"{u.word} 集群：血量分摊"})
        elif u.skill == "kubernetes":
            allies = [a for a in self._alive(own) if a is not u]
            if allies:
                share = u.total_atk // 4
                for a in allies:
                    a.buffed_atk += share
                self._emit({"type": "skill", "msg": f"{u.word} 编排：攻击分摊"})
        elif u.skill == "crypto":
            u._invisible = True
            self._emit({"type": "skill", "msg": f"{u.word} 加密：隐形 1 回合"})
        elif u.skill == "firewall":
            u._shield = 2
            self._emit({"type": "skill", "msg": f"{u.word} 防火墙就绪"})
        elif u.skill == "blockchain":
            u.chain_atk += 1
            grant_battle_hp(u, 1)
            self._emit({"type": "skill", "msg": f"{u.word} 链式增长 +1/+1"})
        elif u.skill == "springboot":
            for a in self._alive(own):
                a.buffed_atk += 1
            self._emit({"type": "skill", "msg": f"{u.word} 自动装配：全队 +1 攻"})
        elif u.skill == "jvm":
            for a in self._alive(own):
                grant_battle_hp(a, 1)
            self._emit({"type": "skill", "msg": f"{u.word} 垃圾回收：全队 +1 血"})
        elif u.skill == "volatile":
            u._invisible = True
            self._emit({"type": "skill", "msg": f"{u.word} 内存可见：隐形 1 回合"})
        elif u.skill == "sentinel":
            u._shield = 2
            self._emit({"type": "skill", "msg": f"{u.word} 限流就绪"})
        elif u.skill == "tomcat":
            if not u.cloned:
                clone = u.copy_for_battle()
                clone.cloned = True
                clone.summon = True
                idx = self._spawn_into_board(own, clone)
                if idx is not None:
                    side = "p" if own is self._combat_p else "e"
                    self._emit({
                        "type": "spawn",
                        "msg": f"{u.word} 容器启动：复制",
                        "side": side,
                        "slot": idx,
                        "unit": unit_to_dict(clone),
                    })
        elif u.skill == "buffer":
            if own_i is not None:
                behind = occupied_behind(own, own_i)
                if behind is not None:
                    grant_battle_hp(behind, 2)
                    self._emit({"type": "skill", "msg": f"{u.word} 缓冲：{behind.word} +2 血"})
        elif u.skill == "pointer":
            if own_i is not None:
                behind = occupied_behind(own, own_i)
                if behind is not None:
                    behind.buffed_atk += 2
                    self._emit({"type": "skill", "msg": f"{u.word} 指针：{behind.word} +2 攻"})
        elif u.skill == "wrapper":
            if own_i is not None:
                behind = occupied_behind(own, own_i)
                if behind is not None:
                    behind._armor = int(getattr(behind, "_armor", 0)) + 1
                    self._emit({"type": "skill", "msg": f"{u.word} 包装：{behind.word} 护甲+1"})
        elif u.skill == "shuffle":
            allies = self._alive(own)
            if allies:
                pick = random.choice(allies)
                grant_battle_hp(pick, 3)
                self._emit({"type": "skill", "msg": f"{u.word} 洗牌：{pick.word} +3 血"})
        elif u.skill == "callback":
            if own_i is not None:
                ahead = occupied_ahead(own, own_i)
                if ahead is not None:
                    ahead.buffed_atk += 2
                    self._emit({"type": "skill", "msg": f"{u.word} 回调：{ahead.word} +2 攻"})
        elif u.skill == "mutex":
            u._hp_lock = 1
            self._emit({"type": "skill", "msg": f"{u.word} 互斥锁就绪"})
        elif u.skill == "restore":
            self._emit({"type": "skill", "msg": f"{u.word} 还原待命"})
        elif u.skill == "sigkill":
            u._sigkill_used = False
            self._emit({"type": "skill", "msg": f"{u.word} 强制终止就绪"})

    # ---------- 战斗播放 ----------
    def _visual_board(self, side: str) -> List[Optional[Unit]]:
        return self.battle_players if side == "p" else self.battle_enemies

    def _apply_visual(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "hit":
            side = ev.get("target_side", "e")
            slot = int(ev.get("target_slot", 0))
            board = self._visual_board(side)
            if 0 <= slot < len(board) and board[slot] is not None:
                board[slot].hp = int(ev.get("target_hp", board[slot].hp))
            self.hit_flash[side] = 1.0
            if ev.get("attacker_side") == "p":
                self.battle_focus_p = int(ev.get("attacker_slot", 0))
            elif ev.get("attacker_side") == "e":
                self.battle_focus_e = int(ev.get("attacker_slot", 0))
            self._spawn_float_damage(ev)
            self._start_lunge(ev)
            self._play("crit" if ev.get("damage", 1) >= 4 else "hit")
        elif kind == "faint":
            side = ev.get("side", "p")
            slot = int(ev.get("slot", 0))
            board = self._visual_board(side)
            if 0 <= slot < len(board):
                self._start_faint_fx(side, slot, board[slot])
                board[slot] = None
            self._play("faint")
        elif kind == "block":
            side = ev.get("target_side", ev.get("side", "e"))
            slot = int(ev.get("target_slot", ev.get("slot", 0)))
            self._start_block_fx(side, slot, ev.get("msg", ""))
            self._play("block")
        elif kind == "skill":
            self._play("skill")
        elif kind == "spawn" and ev.get("unit"):
            side = ev.get("side", "p")
            slot = int(ev.get("slot", 0))
            board = self._visual_board(side)
            spawned = unit_from_dict(ev.get("unit"))
            if spawned is not None and 0 <= slot < len(board):
                board[slot] = spawned  # 修复：此前召唤物只播动画未真正入板
                self._start_spawn_fx(side, slot)
                self._play("levelup")
        self._apply_stat_snapshot(ev)

    def _apply_stat_snapshot(self, ev: dict) -> None:
        """把模拟盘快照写回可视棋子；攻/血变高时飘绿字。"""
        for s in ev.get("stats") or []:
            side = str(s.get("side", "p"))
            slot = int(s.get("slot", 0))
            board = self._visual_board(side)
            if slot < 0 or slot >= len(board) or board[slot] is None:
                continue
            u = board[slot]
            old_atk = u.total_atk
            old_hp = u.hp
            u.atk = int(s.get("atk", u.atk))
            u.buffed_atk = int(s.get("buffed_atk", u.buffed_atk))
            u.dead_atk = int(s.get("dead_atk", u.dead_atk))
            u.chain_atk = int(s.get("chain_atk", u.chain_atk))
            u.battle_extra_atk = int(s.get("battle_extra_atk", u.battle_extra_atk))
            u.max_hp = max(1, int(s.get("max_hp", u.max_hp)))
            u.hp = int(s.get("hp", u.hp))
            u._armor = int(s.get("armor", getattr(u, "_armor", 0)))
            d_atk = u.total_atk - old_atk
            d_hp = u.hp - old_hp
            x, y = self._battle_slot_center(side, slot)
            if d_atk > 0:
                self._spawn_float(x - 10, y - 10, f"+{d_atk}攻", COL_GOLD, ttl=1.2, big=False)
            if d_hp > 0:
                self._spawn_float(x + 14, y - 10, f"+{d_hp}血", COL_FOOD, ttl=1.2, big=False)

    def _start_clash_replay(self, ev: dict) -> None:
        self._active_clash = ev
        self._clash_phase = "approach"
        self._clash_t = 0.0
        self._set_log(ev.get("msg", ""))
        self._lunges = []
        for L in (ev.get("lunges") or [])[:2]:
            a_side = str(L.get("side", "p"))
            a_slot = int(L.get("slot", 0))
            meet = self._meet_slot_rect(a_side)
            self._lunges.append({
                "side": a_side, "slot": a_slot,
                "meet_x": meet.x, "meet_y": meet.y,
                "ux": 1.0 if a_side == "p" else -1.0,
                "uy": 0.0,
                "bump": 16.0,
            })
        if len(self._lunges) == 2:
            a, b = self._lunges[0], self._lunges[1]
            dx, dy = b["meet_x"] - a["meet_x"], b["meet_y"] - a["meet_y"]
            dist = max(1.0, math.hypot(dx, dy))
            a["ux"], a["uy"] = dx / dist, dy / dist
            b["ux"], b["uy"] = -dx / dist, -dy / dist
        lunges = ev.get("lunges") or []
        if lunges:
            self.battle_inspect = (str(lunges[0].get("side", "p")), int(lunges[0].get("slot", 0)))

    def _meet_slot_rect(self, side: str) -> "pygame.Rect":
        """对撞集合点：贴在 VS 两侧。"""
        if side == "p":
            return pygame.Rect(CENTER_X - BATTLE_W - 12, BATTLE_ROW_Y, BATTLE_W, BATTLE_H)
        return pygame.Rect(CENTER_X + 12, BATTLE_ROW_Y, BATTLE_W, BATTLE_H)

    def _apply_clash_impact(self, ev: dict) -> None:
        if not ev:
            return
        self._set_log(ev.get("msg", ""))
        for b in ev.get("blocks") or []:
            self._start_block_fx(
                str(b.get("target_side", "e")),
                int(b.get("target_slot", 0)),
                str(b.get("msg", "")),
            )
            self._play("block")
        for h in ev.get("hits") or []:
            side = str(h.get("target_side", "e"))
            slot = int(h.get("target_slot", 0))
            board = self._visual_board(side)
            if 0 <= slot < len(board) and board[slot] is not None:
                board[slot].hp = int(h.get("target_hp", board[slot].hp))
            self.hit_flash[side] = 1.0
            if h.get("attacker_side") == "p":
                self.battle_focus_p = int(h.get("attacker_slot", 0))
            elif h.get("attacker_side") == "e":
                self.battle_focus_e = int(h.get("attacker_slot", 0))
            self._spawn_float_damage(h)
            self._play("crit" if h.get("damage", 1) >= 4 else "hit")
            self._apply_stat_snapshot(h)
        for b in ev.get("blocks") or []:
            self._apply_stat_snapshot(b)

    def _apply_clash_after(self, ev: dict) -> None:
        if not ev:
            return
        for a in ev.get("after") or []:
            self._apply_visual(a)
            if a.get("msg"):
                self._set_log(a["msg"])
        self._apply_stat_snapshot(ev)

    def _finish_active_clash(self) -> None:
        if self._clash_phase in ("approach", "lunge"):
            self._apply_clash_impact(self._active_clash)
        if self._clash_phase in ("approach", "lunge", "hold"):
            self._apply_clash_after(self._active_clash)
        self._clash_phase = None
        self._active_clash = None
        self._lunges = []

    def _update_battle(self, dt: float) -> None:
        self.hit_flash["p"] = max(0.0, self.hit_flash["p"] - dt * 2.5)
        self.hit_flash["e"] = max(0.0, self.hit_flash["e"] - dt * 2.5)
        if self._clash_phase is not None:
            if self.auto_dummy:
                self._finish_active_clash()
                return
            self._clash_t += dt
            if self._clash_phase == "approach" and self._clash_t >= CLASH_APPROACH:
                self._clash_phase = "lunge"
                self._clash_t = 0.0
            elif self._clash_phase == "lunge" and self._clash_t >= CLASH_LUNGE_OUT:
                self._apply_clash_impact(self._active_clash)
                self._clash_phase = "hold"
                self._clash_t = 0.0
            elif self._clash_phase == "hold" and self._clash_t >= CLASH_HOLD:
                self._apply_clash_after(self._active_clash)
                self._clash_phase = "after"
                self._clash_t = 0.0
            elif self._clash_phase == "after" and self._clash_t >= CLASH_LUNGE_BACK:
                self._clash_phase = None
                self._active_clash = None
                self._lunges = []
            return
        self.battle_event_cooldown -= dt
        if self.battle_event_cooldown > 0 and not self.auto_dummy:
            return
        remaining = len(self.battle_events) - self.battle_event_idx
        self.battle_event_cooldown = 0.28 if remaining > 30 else 0.42
        if self.battle_event_idx >= len(self.battle_events):
            if not self.battle_over:
                self._on_battle_end(self.battle_events[-1])
            return
        ev = self.battle_events[self.battle_event_idx]
        self.battle_event_idx += 1
        if ev["type"] == "end":
            self._on_battle_end(ev)
            return
        if ev["type"] == "clash":
            if self.auto_dummy:
                self._apply_clash_impact(ev)
                self._apply_clash_after(ev)
                self._lunges = []
                return
            self._start_clash_replay(ev)
            return
        self._apply_visual(ev)
        self._set_log(ev.get("msg", ""))

    def _home_slot_rect(self, side: str, slot: int) -> "pygame.Rect":
        x0 = P_X0 if side == "p" else E_X0
        col = battle_draw_col(side, slot)
        return pygame.Rect(x0 + col * BATTLE_STEP, BATTLE_ROW_Y, BATTLE_W, BATTLE_H)

    def _battle_slot_rect(self, side: str, slot: int) -> "pygame.Rect":
        rect = self._home_slot_rect(side, slot)
        phase = self._clash_phase
        t = self._clash_t
        for l in self._lunges:
            if l["side"] != side or l["slot"] != slot:
                continue
            hx, hy = rect.x, rect.y
            mx, my = int(l.get("meet_x", hx)), int(l.get("meet_y", hy))
            bump = float(l.get("bump", 16.0))
            ux, uy = float(l.get("ux", 0.0)), float(l.get("uy", 0.0))
            if phase == "approach":
                k = min(1.0, t / max(0.001, CLASH_APPROACH))
                k = k * k
                rect.x = int(hx + (mx - hx) * k)
                rect.y = int(hy + (my - hy) * k)
            elif phase == "lunge":
                k = min(1.0, t / max(0.001, CLASH_LUNGE_OUT))
                off = bump * (k * k)
                rect.x = int(mx + ux * off)
                rect.y = int(my + uy * off)
            elif phase == "hold":
                rect.x = int(mx + ux * bump)
                rect.y = int(my + uy * bump)
            elif phase == "after":
                k = min(1.0, t / max(0.001, CLASH_LUNGE_BACK))
                k = k * k
                sx, sy = mx + ux * bump, my + uy * bump
                rect.x = int(sx + (hx - sx) * k)
                rect.y = int(sy + (hy - sy) * k)
            break
        return rect

    def _battle_slot_center(self, side: str, slot: int) -> Tuple[int, int]:
        r = self._battle_slot_rect(side, slot)
        return r.centerx, r.y + 8

    # ---------- 战斗动画 fx ----------
    def _start_lunge(self, ev: dict) -> None:
        """兼容单条 hit 的冲锋；对撞回放走 _start_clash_replay。"""
        a_side = str(ev.get("attacker_side", "e"))
        a_slot = int(ev.get("attacker_slot", 0))
        t_side = str(ev.get("target_side", "e"))
        t_slot = int(ev.get("target_slot", 0))
        a0 = self._battle_slot_center(a_side, a_slot)
        a1 = self._battle_slot_center(t_side, t_slot)
        dx, dy = a1[0] - a0[0], a1[1] - a0[1]
        dist = max(1.0, math.hypot(dx, dy))
        self._lunges = [{
            "side": a_side, "slot": a_slot,
            "ux": dx / dist, "uy": dy / dist,
            "reach": 0.78 * dist,
        }]
        self._clash_phase = "lunge"
        self._clash_t = 0.0

    def _start_faint_fx(self, side: str, slot: int, unit: Optional[Unit]) -> None:
        """死亡残影：单位下坠 + 淡出（0.4s）。"""
        r = self._battle_slot_rect(side, slot)
        self._faint_fx.append({
            "side": side, "slot": slot,
            "x": r.x, "y": r.y, "w": r.w, "h": r.h,
            "unit": unit, "t": 0.0, "total": 0.4,
        })

    def _start_spawn_fx(self, side: str, slot: int) -> None:
        """召唤弹入：从格子上方下落回弹（0.4s）。"""
        self._spawn_fx.append({"side": side, "slot": slot, "t": 0.0, "total": 0.4})

    def _block_label(self, msg: str) -> str:
        if "拦截" in msg:
            return "拦截"
        if "隐形" in msg:
            return "隐形"
        if "大蒜" in msg or "减伤" in msg:
            return "减伤"
        elif "锁血" in msg:
            return "锁血"
        return "阻挡"

    def _start_block_fx(self, side: str, slot: int, msg: str) -> None:
        """拦截浮标：在被挡格子上方显示盾形标记 0.6s。"""
        label = self._block_label(msg)
        self._block_fx.append({
            "side": side, "slot": slot, "label": label,
            "t": 0.0, "total": 0.8,
        })
        x, y = self._battle_slot_center(side, slot)
        self._spawn_float(x, y - 12, label, COL_EXP, ttl=1.2, big=False)

    def _update_battle_fx(self, dt: float) -> None:
        """推进全部战斗动画 fx。冲锋进度由 _clash_t 驱动。"""
        for lst in (self._faint_fx, self._spawn_fx, self._block_fx):
            alive = []
            for f in lst:
                f["t"] += dt
                if f["t"] < f["total"]:
                    alive.append(f)
            lst[:] = alive

    def _spawn_float(
        self,
        x: float,
        y: float,
        text: str,
        color: Tuple[int, int, int],
        *,
        ttl: float = 1.4,
        big: bool = True,
    ) -> None:
        n = sum(1 for t in self.float_texts if abs(float(t["x"]) - x) < 52)
        self.float_texts.append({
            "x": x + n * 28,
            "y": y - n * 20,
            "ttl": ttl,
            "max_ttl": ttl,
            "text": text,
            "color": color,
            "big": big,
        })

    def _spawn_float_damage(self, ev: dict) -> None:
        side = str(ev.get("target_side", "e"))
        slot = int(ev.get("target_slot", 0))
        x, y = self._battle_slot_center(side, slot)
        dmg = int(ev.get("damage", 1))
        self._spawn_float(x, y - 18, f"-{dmg}", COL_HP, ttl=1.45, big=True)

    def _update_float_texts(self, dt: float) -> None:
        alive: List[dict] = []
        for t in self.float_texts:
            t["ttl"] -= dt
            t["y"] -= dt * 52
            if t["ttl"] > 0:
                alive.append(t)
        self.float_texts = alive

    def _on_battle_end(self, ev: dict) -> None:
        self.battle_over = True
        win = ev.get("win", False)
        if win:
            self.wins += 1
            self.trophies += 1
            gain = round(random.uniform(0.3, 1.2), 1)
            self.gold += gain
            if self.trophies % 3 == 0:
                d = round(random.uniform(0.1, 0.6), 1)
                self.diamond += d
                self.battle_result_msg = f"胜利！+{gain:.1f} 金币，+{d:.1f} 钻石"
            else:
                self.battle_result_msg = f"胜利！+{gain:.1f} 金币"
        else:
            self.losses += 1
            self.lives -= 1
            self.battle_result_msg = f"失败，-1 生命（剩余 {self.lives}）"
        self.phase = "battle_res"
        self._play("win" if win else "lose")

    # ---------- 奖励 ----------
    def _award_letters(self, win: bool) -> None:
        """按表现给 1-2 个字母（稀有度与表现挂钩）。"""
        rar = 0 if not win else (2 if self.round_no >= 12 else 1)
        if win and self.round_no >= 14:
            rar = 3
        for _ in range(2 if win and self.trophies >= WIN_TROPHIES else 1):
            letter = random.choice([c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in self.used_letters])
            self.used_letters.append(letter)
            self.letters_awarded.append((letter, rar))

    def _set_log(self, msg: str) -> None:
        self.log = msg
        self.log_t = 3.0

    # ---------- 绘制 ----------
    def _draw(self) -> None:
        self._click_zones = []
        self._hover_zones = []
        self.screen.fill(COL_BG)
        self._draw_header()
        if self.phase == "title":
            self._draw_title()
        elif self.phase in ("shop", "battle", "battle_res"):
            self._draw_shop_or_battle()
        elif self.phase == "over":
            self._draw_center([self.over_msg, "", "正在结算..."])
        self._draw_footer()
        self._draw_hover_tooltip()
        pygame.display.flip()

    def _draw_header(self) -> None:
        pygame.draw.rect(self.screen, COL_PANEL, (0, 0, W, 54))
        self.screen.blit(self.font.render(f"铜币 {int(self.copper)}", True, COL_COPPER), (16, 10))
        self.screen.blit(self.font.render(f"金币 {self.gold:.1f}", True, COL_GOLD), (160, 10))
        self.screen.blit(self.font.render(f"钻石 {self.diamond:.1f}", True, COL_DIAM), (310, 10))
        self.screen.blit(self.font.render(f"回合 {max(1, self.round_no)}", True, COL_TEXT), (460, 10))
        self.screen.blit(self.font_sm.render(f"层级 {TIER_NAMES[self.available_tiers-1]}", True, COL_MUTED), (600, 16))
        self.screen.blit(self.font_sm.render(f"奖杯 {self.trophies}/{WIN_TROPHIES}", True, COL_STAR), (730, 16))
        for i in range(self.lives):
            pygame.draw.circle(self.screen, COL_HP, (W - 40 - i * 22, 26), 7)
        self.screen.blit(self.font_sm.render(f"胜 {self.wins} 负 {self.losses}", True, COL_MUTED), (W - 280, 16))

    def _draw_title(self) -> None:
        lines = [
            ("计算机词汇自走棋", self.font_lg2, COL_TEXT),
            ("用英语计算机词汇当棋子，边玩边学", self.font_sm, COL_MUTED),
            ("点击词汇商店 / 食物商店 → 三合升星 → 自动战斗 → 10 奖杯通关", self.font_sm, COL_MUTED),
            ("", self.font_sm, COL_MUTED),
            (f"入场费已从背包扣除 {ENTRY_FEE} 金币", self.font_sm, COL_GOLD),
            (f"局内用铜币买棋；商店可花 {LIFE_GOLD_COST} 金币买 1 命", self.font_sm, COL_COPPER),
        ]
        y = 88
        for text, f, c in lines:
            s = f.render(text, True, c)
            self.screen.blit(s, (W // 2 - s.get_width() // 2, y))
            y += s.get_height() + 12
        btn = pygame.Rect(W // 2 - 130, y + 4, 260, 52)
        pygame.draw.rect(self.screen, COL_ACCENT, btn, border_radius=12)
        lab = self.font_lg.render("开始", True, COL_TEXT)
        self.screen.blit(lab, (btn.centerx - lab.get_width() // 2, btn.centery - 16))
        self._register_click(btn, "start_game")
        hint = self.font_sm.render("ESC 退出并结算", True, COL_MUTED)
        self.screen.blit(hint, (W // 2 - hint.get_width() // 2, btn.bottom + 10))
        self._draw_word_grid()

    def _draw_word_grid(self) -> None:
        x0, y0 = 60, 360
        page = WORDS[:12]
        for i, w in enumerate(page):
            x = x0 + (i % 4) * 250
            y = y0 + (i // 4) * 110
            pygame.draw.rect(self.screen, COL_CARD, (x, y, 230, 90), border_radius=8)
            pygame.draw.rect(self.screen, TIER_COLORS[w.tier - 1], (x, y, 230, 90), 1, border_radius=8)
            self.screen.blit(self.font_sm.render(f"{w.word}", True, TIER_COLORS[w.tier - 1]), (x + 10, y + 8))
            self.screen.blit(self.font_sm.render(w.cn, True, COL_TEXT), (x + 10, y + 30))
            self.screen.blit(self.font_sm.render(f"T{w.tier} {w.skill_cn}", True, COL_MUTED), (x + 10, y + 56))
        more = self.font_sm.render(f"共 {len(WORDS)} 个词汇 · 全部可在商店中按层级解锁", True, COL_MUTED)
        self.screen.blit(more, (W // 2 - more.get_width() // 2, y0 + 3 * 110 + 6))

    def _blit_fit(self, text: str, font, color, x: int, y: int, max_w: int) -> None:
        if font.size(text)[0] <= max_w:
            self.screen.blit(font.render(text, True, color), (x, y))
            return
        for n in range(len(text), 0, -1):
            s = text[:n] + ".."
            if font.size(s)[0] <= max_w:
                self.screen.blit(font.render(s, True, color), (x, y))
                return

    def _atk_hp_colors(self, u: Unit) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
        atk_c = COL_GOLD if bonus_atk(u) > 0 else COL_TEXT
        if u.hp < u.max_hp and u.hp * 2 <= u.max_hp:
            hp_c = COL_HP
        elif bonus_hp(u) > 0:
            hp_c = COL_GOLD
        else:
            hp_c = COL_TEXT
        return atk_c, hp_c

    def _blit_atk_hp(self, u: Unit, x: int, y: int, max_w: int, font=None) -> None:
        font = font or self.font_xs
        atk_c, hp_c = self._atk_hp_colors(u)
        atk_s = str(u.total_atk)
        hp_s = str(max(0, u.hp))
        atk = font.render(atk_s, True, atk_c)
        slash = font.render("/", True, COL_MUTED)
        hp = font.render(hp_s, True, hp_c)
        total_w = atk.get_width() + slash.get_width() + hp.get_width()
        if total_w > max_w:
            self._blit_fit(f"{atk_s}/{hp_s}", font, atk_c, x, y, max_w)
            return
        self.screen.blit(atk, (x, y))
        self.screen.blit(slash, (x + atk.get_width(), y))
        self.screen.blit(hp, (x + atk.get_width() + slash.get_width(), y))

    def _draw_shop_or_battle(self) -> None:
        shop_live = self.phase == "shop"
        # 左：词汇商店
        pygame.draw.rect(self.screen, COL_PANEL, (16, 70, 420, 430), border_radius=12)
        self.screen.blit(self.font.render("词汇商店", True, COL_TEXT), (30, 86))
        if shop_live:
            self._register_click(pygame.Rect(300, 82, 120, 32), "refresh")
        pygame.draw.rect(self.screen, COL_CARD, (300, 82, 120, 32), border_radius=8)
        self.screen.blit(self.font_sm.render("刷新词汇 -1", True, COL_COPPER), (308, 90))
        for i, slot in enumerate(self.shop):
            x, y = 30, 130 + i * 120
            pygame.draw.rect(self.screen, COL_CARD, (x, y, 390, 105), border_radius=10)
            if self.frozen[i]:
                pygame.draw.rect(self.screen, COL_ACCENT, (x, y, 390, 105), 2, border_radius=10)
            if slot.spec is None or slot.kind != "word":
                self.screen.blit(self.font_sm.render("已售出", True, COL_MUTED), (x + 20, y + 30))
                continue
            if shop_live:
                self._register_click(pygame.Rect(x, y, 390, 105), "buy_shop", i)
                self._register_click(pygame.Rect(x + 350, y + 8, 34, 24), "freeze", i)
            freeze_c = COL_ACCENT if self.frozen[i] else COL_MUTED
            self.screen.blit(self.font_sm.render("冻", True, freeze_c), (x + 360, y + 12))
            spec = slot.spec
            c = TIER_COLORS[spec.tier - 1]
            self.screen.blit(self.font.render(f"{spec.word}", True, c), (x + 20, y + 12))
            self.screen.blit(self.font_sm.render(f"T{spec.tier} {spec.cn}", True, COL_TEXT), (x + 20, y + 42))
            self.screen.blit(self.font_sm.render(f"攻{spec.base_atk} 血{spec.base_hp}", True, COL_MUTED), (x + 150, y + 12))
            self._blit_fit(spec.skill_cn, self.font_sm, COL_MUTED, x + 20, y + 66, 300)
            self.screen.blit(self.font_sm.render(f"{spec.cost}铜", True, COL_COPPER), (x + 328, y + 70))
            if shop_live:
                self._register_hover(pygame.Rect(x, y, 390, 105), self._word_hover_lines(spec))
        # 队伍：列号与战斗我方相同（槽 0 前排在右，朝向敌方）
        pygame.draw.rect(self.screen, COL_PANEL, (16, 520, 420, 165), border_radius=12)
        self.screen.blit(self.font.render("我的队伍", True, COL_TEXT), (30, 536))
        self.screen.blit(self.font_xs.render("右=前排 · 悬停看技能", True, COL_MUTED), (148, 542))
        for i, u in enumerate(self.team):
            x, y = 30 + battle_draw_col("p", i) * 78, 568
            rr = pygame.Rect(x, y, 72, 72)
            if self.selected_slot == i and shop_live:
                pygame.draw.rect(self.screen, COL_ACCENT, rr.inflate(4, 4), 2, border_radius=10)
            if shop_live:
                self._register_click(rr, "team_slot", i)
            if u is None:
                pygame.draw.rect(self.screen, COL_CARD, rr, border_radius=10)
                self.screen.blit(self.font_sm.render("空", True, COL_MUTED), (x + 24, y + 26))
                continue
            pygame.draw.rect(self.screen, COL_CARD, rr, border_radius=10)
            c = TIER_COLORS[u.tier - 1]
            self._blit_fit(u.word, self.font_xs, c, x + 6, y + 4, 60)
            self._blit_atk_hp(u, x + 6, y + 22, 60)
            self.screen.blit(self.font_xs.render("★" * u.star, True, COL_STAR), (x + 6, y + 38))
            self._blit_fit(u.skill_cn, self.font_xs, COL_MUTED, x + 6, y + 54, 60)
            self._register_hover(rr, self._unit_hover_lines(u, tag="我方"))
            sell_r = pygame.Rect(x, y + 74, 72, 22)
            pygame.draw.rect(self.screen, (90, 40, 48), sell_r, border_radius=6)
            self.screen.blit(self.font_xs.render("出售", True, COL_HP), (x + 18, y + 76))
            if shop_live:
                self._register_click(sell_r, "sell", i)

        # 右：开战按钮 / 战斗
        if self.phase == "shop":
            self._register_click(pygame.Rect(560, 520, 200, 50), "start_battle")
            pygame.draw.rect(self.screen, COL_ACCENT, (560, 520, 200, 50), border_radius=12)
            self.screen.blit(self.font.render("开战", True, COL_TEXT), (620, 532))
            life_btn = pygame.Rect(780, 520, 220, 50)
            pygame.draw.rect(self.screen, COL_CARD, life_btn, border_radius=12)
            pygame.draw.rect(self.screen, COL_GOLD, life_btn, 1, border_radius=12)
            self._register_click(life_btn, "buy_life")
            self.screen.blit(
                self.font_sm.render(f"买命 -{LIFE_GOLD_COST} 金", True, COL_GOLD),
                (800, 534),
            )
            pygame.draw.rect(self.screen, COL_PANEL, (520, 70, 560, 430), border_radius=12)
            self.screen.blit(self.font.render("敌方（预览）", True, COL_ENEMY), (540, 86))
            self.screen.blit(self.font_xs.render("左=前排  悬停看技能", True, COL_MUTED), (700, 92))
            for i, u in enumerate(self.enemy_team[:MAX_TEAM]):
                x = 540 + battle_draw_col("e", i) * 100
                y = 118
                er = pygame.Rect(x, y, 90, 118)
                pygame.draw.rect(self.screen, COL_CARD, er, border_radius=10)
                c = TIER_COLORS[u.tier - 1]
                self._blit_fit(u.word, self.font_sm, c, x + 6, y + 6, 78)
                self._blit_atk_hp(u, x + 6, y + 30, 78)
                self.screen.blit(self.font_xs.render("★" * u.star, True, COL_STAR), (x + 6, y + 48))
                self._blit_fit(u.skill_cn, self.font_xs, COL_MUTED, x + 6, y + 70, 78)
                self._register_hover(er, self._unit_hover_lines(u, tag="敌方"))
            # 食物商店：独立刷新
            self.screen.blit(self.font.render("食物商店", True, COL_FOOD), (540, 250))
            if shop_live:
                self._register_click(pygame.Rect(900, 248, 150, 32), "refresh_food")
            pygame.draw.rect(self.screen, COL_CARD, (900, 248, 150, 32), border_radius=8)
            self.screen.blit(self.font_sm.render("刷新食物 -1", True, COL_COPPER), (912, 256))
            for i, slot in enumerate(self.food_shop):
                x, y = 540 + i * 260, 292
                pygame.draw.rect(self.screen, COL_CARD, (x, y, 248, 180), border_radius=10)
                if self.food_frozen[i]:
                    pygame.draw.rect(self.screen, COL_ACCENT, (x, y, 248, 180), 2, border_radius=10)
                if slot.spec is None:
                    self.screen.blit(self.font_sm.render("已售出", True, COL_MUTED), (x + 16, y + 70))
                    continue
                if shop_live:
                    self._register_click(pygame.Rect(x, y, 248, 180), "buy_food", i)
                    self._register_click(pygame.Rect(x + 208, y + 8, 32, 24), "freeze_food", i)
                freeze_c = COL_ACCENT if self.food_frozen[i] else COL_MUTED
                self.screen.blit(self.font_sm.render("冻", True, freeze_c), (x + 214, y + 10))
                f = slot.spec
                self.screen.blit(self.font.render(f.name, True, COL_FOOD), (x + 16, y + 16))
                self.screen.blit(self.font_sm.render(f.cn, True, COL_TEXT), (x + 16, y + 52))
                self._blit_fit(f.desc, self.font_sm, COL_MUTED, x + 16, y + 86, 216)
                self.screen.blit(self.font_sm.render(f"{f.cost}铜", True, COL_COPPER), (x + 16, y + 140))
                if shop_live:
                    self._register_hover(pygame.Rect(x, y, 248, 180), self._food_hover_lines(f))
        else:
            pygame.draw.rect(self.screen, COL_PANEL, (STAGE_X, STAGE_Y, STAGE_W, STAGE_H), border_radius=12)
            pygame.draw.rect(self.screen, (46, 51, 76), (STAGE_X, STAGE_Y, STAGE_W, STAGE_H), 1, border_radius=12)
            pygame.draw.line(self.screen, (58, 64, 94), (CENTER_X, STAGE_Y + 40), (CENTER_X, STAGE_Y + STAGE_H - 12), 1)
            self.screen.blit(self.font.render("战斗", True, COL_ENEMY), (30, 88))
            lab_p = self.font_sm.render("我方（前排靠中线）", True, COL_TEXT)
            lab_e = self.font_sm.render("敌方（前排靠中线）", True, COL_ENEMY)
            self.screen.blit(lab_p, (P_X0, BATTLE_ROW_Y - 32))
            self.screen.blit(lab_e, (E_X0 + MAX_TEAM * BATTLE_STEP - BATTLE_GAP - lab_e.get_width(), BATTLE_ROW_Y - 32))
            self._draw_battle_units()
            self._draw_clash_damage_labels()
            self._draw_skill_banner()
            self._draw_float_texts()
            if self.phase == "battle_res":
                self._register_click(pygame.Rect(820, 580, 240, 52), "next_round")
                pygame.draw.rect(self.screen, COL_ACCENT, (820, 580, 240, 52), border_radius=12)
                self.screen.blit(self.font_sm.render(f"继续  {self.battle_result_msg}", True, COL_TEXT), (836, 594))

    def _click_next_round(self) -> None:
        if self.phase == "battle_res":
            self._next_round_or_end()

    def _draw_battle_units(self) -> None:
        vs = self.font_lg.render("VS", True, COL_ACCENT)
        self.screen.blit(vs, vs.get_rect(center=(CENTER_X, BATTLE_ROW_Y + BATTLE_H // 2)))
        for side, board in (("e", self.battle_enemies), ("p", self.battle_players)):
            for i, u in enumerate(board):
                rect = self._battle_slot_rect(side, i)
                if u is None:
                    pygame.draw.rect(self.screen, COL_CARD, rect, border_radius=10)
                    continue
                self._register_click(rect, "inspect_unit", side, i)
                flash = self.hit_flash.get(side, 0.0)
                focus = self.battle_focus_e if side == "e" else self.battle_focus_p
                bg = COL_CARD
                if i == focus and flash > 0:
                    bg = (92, 44, 44) if side == "e" else (42, 80, 46)
                    pygame.draw.rect(self.screen, COL_ACCENT, rect.inflate(4, 4), 2, border_radius=10)
                if self.battle_inspect == (side, i):
                    pygame.draw.rect(self.screen, COL_STAR, rect.inflate(4, 4), 2, border_radius=10)
                pygame.draw.rect(self.screen, bg, rect, border_radius=10)
                c = TIER_COLORS[u.tier - 1] if side == "p" else COL_ENEMY
                if not u.alive:
                    c = COL_MUTED
                self._blit_fit(u.word, self.font_sm, c, rect.x + 6, rect.y + 6, rect.w - 12)
                self._blit_atk_hp(u, rect.x + 6, rect.y + 26, rect.w - 12)
                self.screen.blit(self.font_xs.render("★" * u.star, True, COL_STAR), (rect.x + 6, rect.y + 44))
                self._blit_fit(u.skill_cn, self.font_xs, COL_MUTED, rect.x + 6, rect.y + 62, rect.w - 12)
                self._register_hover(rect, self._unit_hover_lines(u, tag="我方" if side == "p" else "敌方"))
                bar_w = int((rect.w - 8) * max(0.0, u.hp / max(1, u.max_hp)))
                pygame.draw.rect(self.screen, (70, 76, 96), (rect.x + 4, rect.bottom - 10, rect.w - 8, 5), border_radius=2)
                if bar_w > 0:
                    bar_c = (220, 100, 100) if side == "e" else (120, 220, 140)
                    pygame.draw.rect(self.screen, bar_c, (rect.x + 4, rect.bottom - 10, bar_w, 5), border_radius=2)
        self._draw_faint_fx()
        self._draw_spawn_fx()
        self._draw_block_fx()

    def _draw_skill_banner(self) -> None:
        """战斗底部：当前对撞或点选棋子的完整技能。"""
        shown: List[Tuple[str, Unit]] = []
        seen = set()
        if self._active_clash:
            for L in self._active_clash.get("lunges") or []:
                side, slot = str(L.get("side", "p")), int(L.get("slot", 0))
                board = self._visual_board(side)
                if 0 <= slot < len(board) and board[slot] is not None and (side, slot) not in seen:
                    seen.add((side, slot))
                    shown.append((side, board[slot]))
        if self.battle_inspect is not None:
            side, slot = self.battle_inspect
            board = self._visual_board(side)
            if 0 <= slot < len(board) and board[slot] is not None and (side, slot) not in seen:
                shown.append((side, board[slot]))
        box = pygame.Rect(STAGE_X + 12, BATTLE_ROW_Y + BATTLE_H + 18, STAGE_W - 24, 96)
        pygame.draw.rect(self.screen, COL_CARD, box, border_radius=10)
        if not shown:
            self.screen.blit(
                self.font_sm.render("悬停棋子查看技能；对撞时显示双方技能和伤害", True, COL_MUTED),
                (box.x + 16, box.y + 36),
            )
            return
        col_w = box.w // max(1, len(shown))
        for i, (side, u) in enumerate(shown[:2]):
            x = box.x + 16 + i * col_w
            tag = "我方" if side == "p" else "敌方"
            c = COL_TEXT if side == "p" else COL_ENEMY
            title = f"{tag} {u.word}（{u.cn}）"
            self.screen.blit(self.font_sm.render(title, True, c), (x, box.y + 10))
            self._blit_atk_hp(
                u,
                x + self.font_sm.size(title)[0] + 12,
                box.y + 10,
                max(40, col_w - 28 - self.font_sm.size(title)[0]),
                font=self.font_sm,
            )
            self._blit_fit(u.skill_cn, self.font_sm, COL_MUTED, x, box.y + 40, col_w - 28)
            extras = []
            if u.honey:
                extras.append("蜂蜜")
            if u.garlic:
                extras.append("大蒜")
            if extras:
                self.screen.blit(self.font_xs.render(" ".join(extras), True, COL_FOOD), (x, box.y + 68))

    def _draw_faint_fx(self) -> None:
        """死亡残影：单位下坠 + 淡出。"""
        for f in self._faint_fx:
            u = f.get("unit")
            if u is None:
                continue
            k = min(1.0, f["t"] / f["total"])
            rect = pygame.Rect(f["x"], f["y"] + int(14 * k * k), f["w"], f["h"])
            alpha = max(0, min(255, int(255 * (1 - k))))
            # 深色底 + 灰化文字，随下落淡出
            surf = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
            pygame.draw.rect(surf, (*COL_CARD, alpha), surf.get_rect(), border_radius=10)
            self.screen.blit(surf, rect)
            c = TIER_COLORS[u.tier - 1]
            txt = self.font_sm.render(u.word, True, c).copy()
            txt.set_alpha(alpha)
            self.screen.blit(txt, (rect.x + 8, rect.y + 8))
            txt2 = self.font_sm.render(f"{u.total_atk}/{u.hp}", True, COL_TEXT).copy()
            txt2.set_alpha(alpha)
            self.screen.blit(txt2, (rect.x + 8, rect.y + 34))

    def _draw_spawn_fx(self) -> None:
        """召唤弹入：从格子上方下落回弹。"""
        for f in self._spawn_fx:
            k = min(1.0, f["t"] / f["total"])
            rect = self._battle_slot_rect(f["side"], f["slot"])
            # 前 0.5 段下落 + 回弹，整体从 0 高度压扁展开
            scale = 1.0
            drop = 0
            if k < 0.5:
                scale = 0.3 + 0.7 * (k / 0.5)
            else:
                scale = 1.0
                drop = int(10 * (1 - k) * (1 - k))
            w = max(4, int(rect.w * scale))
            h = max(4, int(rect.h * scale))
            x = rect.centerx - w // 2
            y = rect.y - drop
            pygame.draw.rect(self.screen, (120, 220, 160), (x, y, w, h), 2, border_radius=10)

    def _draw_block_fx(self) -> None:
        """拦截/隐形/减伤浮标：被挡格子上方显示盾形标记。"""
        for f in self._block_fx:
            k = min(1.0, f["t"] / f["total"])
            alpha = max(0, min(255, int(255 * (1 - k))))
            rect = self._battle_slot_rect(f["side"], f["slot"])
            # 盾形圆角标记
            shield = pygame.Rect(rect.centerx - 22, rect.y - 26, 44, 24)
            s = pygame.Surface((44, 24), pygame.SRCALPHA)
            pygame.draw.rect(s, (120, 200, 255, alpha), s.get_rect(), border_radius=8)
            self.screen.blit(s, shield)
            lab = self.font_sm.render(f["label"], True, COL_TEXT).copy()
            lab.set_alpha(alpha)
            self.screen.blit(lab, (shield.x + 2, shield.y + 2))

    def _draw_float_texts(self) -> None:
        for f in self.float_texts:
            max_ttl = float(f.get("max_ttl", 1.4) or 1.4)
            alpha = max(0, min(255, int(255 * (f["ttl"] / max(0.001, max_ttl)))))
            font = self.font_lg if f.get("big") else self.font_sm
            color = tuple(f.get("color") or COL_HP)
            s = font.render(str(f["text"]), True, color).copy()
            s.set_alpha(alpha)
            self.screen.blit(s, (int(f["x"]) - s.get_width() // 2, int(f["y"])))

    def _draw_clash_damage_labels(self) -> None:
        """对撞命中后，伤害数字钉在棋子上方，停顿期间一直能看见。"""
        ev = self._active_clash
        if not ev or self._clash_phase not in ("hold", "after"):
            return
        hits = ev.get("hits") or []
        seen: Dict[Tuple[str, int], int] = {}
        for h in hits:
            side = str(h.get("target_side", "e"))
            slot = int(h.get("target_slot", 0))
            key = (side, slot)
            n = seen.get(key, 0)
            seen[key] = n + 1
            rect = self._battle_slot_rect(side, slot)
            dmg = int(h.get("damage", 1))
            txt = self.font_lg.render(f"-{dmg}", True, COL_HP)
            self.screen.blit(
                txt,
                (rect.centerx - txt.get_width() // 2 + n * 18, rect.y - 34 - n * 6),
            )
        for b in ev.get("blocks") or []:
            side = str(b.get("target_side", "e"))
            slot = int(b.get("target_slot", 0))
            rect = self._battle_slot_rect(side, slot)
            lab = self.font_sm.render(self._block_label(str(b.get("msg", ""))), True, COL_EXP)
            self.screen.blit(lab, (rect.centerx - lab.get_width() // 2, rect.y - 54))
        line = "   ".join(str(h.get("msg", "")) for h in hits if h.get("msg"))
        if line:
            s = self.font_sm.render(line, True, COL_HP)
            x = max(STAGE_X + 16, CENTER_X - s.get_width() // 2)
            if x + s.get_width() > STAGE_X + STAGE_W - 16:
                self._blit_fit(line, self.font_sm, COL_HP, STAGE_X + 16, BATTLE_ROW_Y - 56, STAGE_W - 32)
            else:
                self.screen.blit(s, (x, BATTLE_ROW_Y - 56))

    def _word_hover_lines(self, spec: WordSpec) -> List[str]:
        return [
            f"{spec.word}（{spec.cn}）",
            f"T{spec.tier}  攻{spec.base_atk}  血{spec.base_hp}  {spec.cost}铜",
            f"技能：{spec.skill_cn}",
        ]

    def _food_hover_lines(self, food: FoodSpec) -> List[str]:
        return [
            f"{food.name}（{food.cn}）",
            f"{food.cost}铜",
            food.desc,
        ]

    def _unit_hover_lines(self, u: Unit, *, tag: str = "") -> List[str]:
        head = f"{u.word}（{u.cn}）"
        if tag:
            head = f"{tag} {head}"
        lines = [
            head,
            f"T{u.tier}  {'★' * u.star}  攻{u.total_atk}  血{max(0, u.hp)}/{u.max_hp}",
            f"技能：{u.skill_cn}",
        ]
        ba, bh = bonus_atk(u), bonus_hp(u)
        if ba or bh:
            bits = []
            if ba:
                bits.append(f"+{ba}攻")
            if bh:
                bits.append(f"+{bh}血")
            lines.append("局内 " + " ".join(bits))
        extras = []
        if u.honey:
            extras.append("蜂蜜：死亡召唤 1/1 蜜蜂")
        if u.garlic:
            extras.append("大蒜：受击减伤 1")
        if getattr(u, "_armor", 0):
            extras.append(f"护甲 {int(u._armor)}（受击减伤）")
        if u.cookie:
            extras.append("饼干：下次喂食效果×2")
        if extras:
            lines.extend(extras)
        return lines

    def _wrap_hover_line(self, text: str, font, max_w: int) -> List[str]:
        if not text:
            return []
        if font.size(text)[0] <= max_w:
            return [text]
        lines: List[str] = []
        cur = ""
        for ch in text:
            trial = cur + ch
            if font.size(trial)[0] <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines or [text]

    def _hover_lines_at(self, pos) -> Optional[List[str]]:
        for rect, lines in reversed(self._hover_zones):
            if rect.collidepoint(pos):
                return lines
        return None

    def _draw_hover_tooltip(self) -> None:
        if not self._hover_zones:
            return
        mx, my = pygame.mouse.get_pos()
        lines = self._hover_lines_at((mx, my))
        if not lines:
            return
        pad = 10
        max_w = 400
        wrapped: List[str] = []
        for s in lines:
            wrapped.extend(self._wrap_hover_line(s, self.font_sm, max_w - pad * 2))
        if not wrapped:
            return
        w = min(max_w, max(self.font_sm.size(s)[0] for s in wrapped) + pad * 2)
        line_h = 22
        h = pad * 2 + len(wrapped) * line_h
        x = mx + 16
        y = my + 18
        if x + w > W - 8:
            x = max(8, mx - w - 12)
        if y + h > H - 8:
            y = max(8, my - h - 8)
        pygame.draw.rect(self.screen, (24, 28, 44), (x, y, w, h), border_radius=8)
        pygame.draw.rect(self.screen, COL_ACCENT, (x, y, w, h), 1, border_radius=8)
        for i, s in enumerate(wrapped):
            color = COL_TEXT if i == 0 else COL_MUTED
            if s.startswith("技能"):
                color = COL_STAR
            self.screen.blit(self.font_sm.render(s, True, color), (x + pad, y + pad + i * line_h))

    def _register_click(self, rect: pygame.Rect, action: str, *args) -> None:
        self._click_zones.append((rect, action, args))

    def _register_hover(self, rect: pygame.Rect, lines: List[str]) -> None:
        if lines:
            self._hover_zones.append((rect, lines))

    def _draw_footer(self) -> None:
        pygame.draw.rect(self.screen, COL_PANEL, (0, H - 40, W, 40))
        msg = self.log or "ESC 退出并结算"
        self.screen.blit(self.font_sm.render(msg, True, COL_MUTED), (14, H - 30))

    def _draw_center(self, lines: List[str]) -> None:
        y = H // 2 - len(lines) * 30
        for ln in lines:
            s = self.font_lg.render(ln, True, COL_TEXT)
            self.screen.blit(s, (W // 2 - s.get_width() // 2, y))
            y += 60

    # ---------- dummy 无头模式 ----------
    def _dummy_step(self, dt: float) -> None:
        self.dummy_t += dt
        if self.phase == "title":
            if not self.entry_paid:
                self._pay_entry()
                return
            if self.entry_paid and self.dummy_t > 0.3:
                self._start_run()
                self.dummy_t = 0.0
            return
        if self.phase == "shop":
            if self.dummy_t < 1.0:
                # 有金币就买第一个可买的
                for i, slot in enumerate(self.shop):
                    if slot.spec is not None and self.copper >= slot.spec.cost:
                        self._click_buy_shop(i)
                        return
                # 都买不起：刷新（金币不足时会被拒绝）
                self._click_refresh()
            else:
                # 买窗结束即开战（含空队）；勿再空等到 2.2s，整局帧数会顶满测试上限
                self._start_battle()
                self.dummy_t = 0.0
            return
        if self.phase == "battle":
            # dummy 加速回放
            self.battle_event_cooldown = 0.0
            return
        if self.phase == "battle_res":
            if self.dummy_t > 1.0:
                self._next_round_or_end()
                self.dummy_t = 0.0
            return

    # ---------- 结算 ----------
    def _write_result(self) -> None:
        result = GameResult(
            session_id=self.session.session_id,
            gold_delta=self.gold - self.initial_gold,
            diamond_delta=self.diamond - self.initial_diamond,
            waves_cleared=self.round_no,
            letters=self.letters_awarded,
            word_lineups=self.new_lineups,
            message=f"词汇自走棋：{self.trophies} 奖杯 · {self.wins}胜{self.losses}负 · 最高回合 {self.round_no}",
        )
        result.write(self.session.result_path())


def run_session(session_path: str | Path) -> int:
    p = Path(session_path)
    if not p.exists():
        print(f"会话文件不存在: {p}")
        return 2
    try:
        s = GameSession.read(p)
        game = WordArenaGame(s)
        game.run()
        return 0
    except Exception as e:
        print(f"游戏运行错误: {e}")
        return 1


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m games.word_arena <session_in.json>")
        raise SystemExit(2)
    raise SystemExit(run_session(sys.argv[1]))


if __name__ == "__main__":
    main()
