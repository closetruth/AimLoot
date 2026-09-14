"""词汇自走棋：鼠标可点、点击区不得每帧膨胀、启动不得堵住管道。"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.game_launcher import game_subprocess_stdio
from src.game_protocol import GameResult, GameSession

from games.word_arena import (
    CLASH_APPROACH,
    CLASH_LUNGE_OUT,
    FOODS,
    LIFE_GOLD_COST,
    MAX_LIVES,
    MAX_TEAM,
    ShopSlot,
    Unit,
    WordArenaGame,
    WORDS,
    WORD_BY_KEY,
    battle_draw_col,
    bonus_atk,
    bonus_hp,
    extra_clash_count,
    front_index,
    lineup_from_dict,
    lineup_to_dict,
    occupied_ahead,
    occupied_behind,
    printed_atk,
    printed_hp,
    unit_from_dict,
    unit_to_dict,
)


def _session(gold: float = 80.0) -> GameSession:
    return GameSession.create(gold=gold, diamond=2.0)


class WordArenaMouseTests(unittest.TestCase):
    def setUp(self):
        self.game = WordArenaGame(_session())

    def test_shop_click_zones_do_not_grow_each_frame(self):
        """每帧 _register_click 却不清空，几分钟后点击列表上万导致卡顿。"""
        self.game._click_start_game()
        self.game._draw()
        n1 = len(self.game._click_zones)
        self.assertGreater(n1, 0, "商店阶段应有可点区域")
        for _ in range(10):
            self.game._draw()
        self.assertEqual(
            len(self.game._click_zones),
            n1,
            "点击区必须每帧重建，不能累计",
        )

    def test_title_click_starts_game(self):
        self.game._draw()
        actions = [a for _, a, _ in self.game._click_zones]
        self.assertIn("start_game", actions)
        rect = next(r for r, a, _ in self.game._click_zones if a == "start_game")
        self.game._on_mouse_down(rect.center)
        self.assertEqual(self.game.phase, "shop")
        self.assertTrue(self.game.entry_paid)

    def test_click_shop_card_buys_without_crash(self):
        """曾把 (i,) 当成 idx 传入，点商店会 TypeError。"""
        g = self.game
        g._click_start_game()
        spec = WORDS[0]
        g.shop[0] = ShopSlot(kind="word", spec=spec)
        g.copper = 20
        g._draw()
        buy = next(r for r, a, args in g._click_zones if a == "buy_shop" and args == (0,))
        g._on_mouse_down((buy.x + 20, buy.y + 40))
        self.assertTrue(any(u is not None for u in g.team))
        self.assertIsNone(g.shop[0].spec)

    def test_freeze_button_not_swallowed_by_card(self):
        g = self.game
        g._click_start_game()
        g.shop[0] = ShopSlot(kind="word", spec=WORDS[0])
        g._draw()
        frz = next(r for r, a, args in g._click_zones if a == "freeze" and args == (0,))
        g._on_mouse_down(frz.center)
        self.assertTrue(g.frozen[0])
        self.assertIsNotNone(g.shop[0].spec)


class WordArenaShopBattleUxTests(unittest.TestCase):
    def setUp(self):
        self.game = WordArenaGame(_session())

    def test_shop_team_front_is_on_the_right(self):
        """买下来后队伍朝向与战斗一致：槽 0 前排在右侧。"""
        g = self.game
        g._click_start_game()
        g._draw()
        xs = {args[0]: r.x for r, a, args in g._click_zones if a == "team_slot"}
        self.assertEqual(set(xs), set(range(MAX_TEAM)))
        self.assertGreater(xs[0], xs[MAX_TEAM - 1])

    def test_sell_button_and_refund(self):
        g = self.game
        g._click_start_game()
        g.shop[0] = ShopSlot(kind="word", spec=WORDS[0])
        g.copper = 20
        g._click_buy_shop(0)
        idx = next(i for i, u in enumerate(g.team) if u is not None)
        copper = g.copper
        g._draw()
        sells = [z for z in g._click_zones if z[1] == "sell" and z[2] == (idx,)]
        self.assertTrue(sells, "应有出售按钮")
        g._click_sell(idx)
        self.assertIsNone(g.team[idx])
        self.assertGreater(g.copper, copper)

    def test_word_refresh_never_rolls_food(self):
        g = self.game
        g._click_start_game()
        for _ in range(12):
            g.copper = 10
            self.assertTrue(g._roll_word_shop(charge=True))
            for slot in g.shop:
                if slot.spec is not None:
                    self.assertEqual(slot.kind, "word")

    def test_food_refresh_independent(self):
        g = self.game
        g._click_start_game()
        g.frozen = [True] * 3
        words = [s.spec.word if s.spec else None for s in g.shop]
        g.copper = 10
        g._click_refresh_food()
        self.assertEqual([s.spec.word if s.spec else None for s in g.shop], words)
        self.assertTrue(any(s.spec is not None and s.kind == "food" for s in g.food_shop))
        g._draw()
        actions = {a for _, a, _ in g._click_zones}
        self.assertIn("refresh_food", actions)
        self.assertIn("buy_food", actions)

    def test_clash_moves_to_center_before_hit(self):
        g = self.game
        g._click_start_game()
        g.team[0] = Unit.from_spec(WORDS[0])
        g.team[0].atk, g.team[0].hp, g.team[0].max_hp = 1, 30, 30
        g.team[0].skill = "monitor"
        g.enemy_team = [Unit.from_spec(WORDS[1])]
        g.enemy_team[0].atk, g.enemy_team[0].hp, g.enemy_team[0].max_hp = 1, 30, 30
        g.enemy_team[0].skill = "monitor"
        g._start_battle()
        g.auto_dummy = False
        g.battle_event_cooldown = 0.0
        for _ in range(40):
            g._update_battle(1.0)
            g.battle_event_cooldown = 0.0
            if g._clash_phase == "approach":
                break
        self.assertEqual(g._clash_phase, "approach")
        g._update_battle(CLASH_APPROACH + 0.01)
        self.assertEqual(g._clash_phase, "lunge")
        g._update_battle(CLASH_LUNGE_OUT + 0.01)
        self.assertEqual(g._clash_phase, "hold")
        self.assertTrue(
            any(str(t.get("text", "")).startswith("-") for t in g.float_texts),
            "对撞命中后应飘出伤害数字",
        )
        clashes = [e for e in g.battle_events if e.get("type") == "clash"]
        self.assertTrue(clashes)
        self.assertTrue(
            any("-" in str(e.get("msg", "")) for e in clashes),
            "对撞日志应写出具体伤害",
        )

    def test_shop_team_hover_shows_skill(self):
        g = self.game
        g._click_start_game()
        spec = WORDS[0]
        g.shop[0] = ShopSlot(kind="word", spec=spec)
        g.copper = 20
        g._click_buy_shop(0)
        idx = next(i for i, u in enumerate(g.team) if u is not None)
        g._draw()
        team = next(r for r, a, args in g._click_zones if a == "team_slot" and args == (idx,))
        lines = g._hover_lines_at(team.center)
        self.assertIsNotNone(lines)
        blob = "\n".join(lines)
        self.assertIn(g.team[idx].skill_cn, blob)
        self.assertIn("技能", blob)
        g.shop[0] = ShopSlot(kind="word", spec=WORDS[1])
        g._draw()
        shop_card = next(r for r, a, args in g._click_zones if a == "buy_shop" and args == (0,))
        shop_lines = g._hover_lines_at(shop_card.center)
        self.assertIsNotNone(shop_lines)
        self.assertIn(WORDS[1].skill_cn, "\n".join(shop_lines))
        n1 = len(g._hover_zones)
        g._draw()
        self.assertEqual(len(g._hover_zones), n1)

    def test_battle_can_inspect_skill(self):
        g = self.game
        g._click_start_game()
        g.team[0] = Unit.from_spec(WORDS[0])
        g.enemy_team = [Unit.from_spec(WORDS[1])]
        g._start_battle()
        g.auto_dummy = False
        g._draw()
        inspect = [z for z in g._click_zones if z[1] == "inspect_unit"]
        self.assertTrue(inspect, "战斗中应能点击棋子看技能")
        r, _, args = inspect[0]
        u = g._visual_board(args[0])[args[1]]
        hover = g._hover_lines_at(r.center)
        self.assertIsNotNone(hover)
        self.assertIn(u.skill_cn, "\n".join(hover))
        g._on_mouse_down(r.center)
        self.assertEqual(g.battle_inspect, (args[0], args[1]))
        self.assertIn(u.skill_cn, g.log)


class WordArenaCopperAndLifeTests(unittest.TestCase):
    def setUp(self):
        self.game = WordArenaGame(_session(gold=80.0))

    def test_shop_spend_uses_copper_not_gold(self):
        g = self.game
        g._click_start_game()
        gold_before = g.gold
        copper_before = g.copper
        spec = WORDS[0]
        g.shop[0] = ShopSlot(kind="word", spec=spec)
        g._click_buy_shop(0)
        self.assertEqual(g.gold, gold_before)
        self.assertEqual(g.copper, copper_before - spec.cost)
        self.assertTrue(any(u is not None for u in g.team))

    def test_start_does_not_deduct_entry_gold_again(self):
        g = self.game
        g._click_start_game()
        self.assertEqual(g.gold, 80.0)
        self.assertEqual(g.copper, 10)

    def test_buy_life_costs_five_gold(self):
        g = self.game
        g._click_start_game()
        g.lives = 7
        g.gold = 20
        g._click_buy_life()
        self.assertEqual(g.lives, 8)
        self.assertEqual(g.gold, 20 - LIFE_GOLD_COST)

    def test_buy_life_blocked_when_full_or_broke(self):
        g = self.game
        g._click_start_game()
        g.lives = MAX_LIVES
        g.gold = 50
        g._click_buy_life()
        self.assertEqual(g.lives, MAX_LIVES)
        self.assertEqual(g.gold, 50)
        g.lives = 3
        g.gold = LIFE_GOLD_COST - 1
        g._click_buy_life()
        self.assertEqual(g.lives, 3)
        self.assertEqual(g.gold, LIFE_GOLD_COST - 1)


class WordArenaLineupAndBattleTests(unittest.TestCase):
    def test_history_lineup_becomes_enemy(self):
        snap = lineup_to_dict(
            4,
            [Unit.from_spec(WORDS[0], 2), None, Unit.from_spec(WORDS[1], 1), None, None],
        )
        session = GameSession.create(gold=80.0, diamond=2.0, word_lineups=[snap])
        g = WordArenaGame(session)
        g._click_start_game()
        words = [u.word for u in g.enemy_team]
        self.assertIn(WORDS[0].word, words)
        self.assertIn(WORDS[1].word, words)

    def test_battle_display_starts_full_hp(self):
        g = WordArenaGame(_session())
        g._click_start_game()
        g.team[0] = Unit.from_spec(WORDS[0])
        g.enemy_team = [Unit.from_spec(WORDS[1])]
        start_hp = g.team[0].hp
        g._start_battle()
        self.assertEqual(g.phase, "battle")
        alive = [u for u in g.battle_players if u is not None]
        self.assertTrue(alive)
        self.assertEqual(alive[0].hp, start_hp)
        self.assertEqual(alive[0].hp, alive[0].max_hp)

    def test_session_result_roundtrip_lineups(self):
        snap = lineup_to_dict(2, [Unit.from_spec(WORDS[0]), None, None, None, None])
        session = GameSession.create(gold=1.0, diamond=0.0, word_lineups=[snap])
        path = session.write()
        loaded = GameSession.read(path)
        restored = lineup_from_dict(loaded.word_lineups[0])
        self.assertEqual(restored[0].word, WORDS[0].word)
        result = GameResult(session_id=session.session_id, word_lineups=[snap])
        out = session.result_path()
        result.write(out)
        back = GameResult.read(out)
        self.assertEqual(back.word_lineups[0]["units"][0]["word"], WORDS[0].word)
        path.unlink(missing_ok=True)
        out.unlink(missing_ok=True)


class WordArenaRosterAndFxTests(unittest.TestCase):
    def test_word_pool_expanded(self):
        """词库扩充后：每层词汇 ≥ 10 个，总数 ≥ 50。"""
        self.assertGreaterEqual(len(WORDS), 50)
        per_tier = [sum(1 for w in WORDS if w.tier == t) for t in (1, 2, 3, 4, 5)]
        for n in per_tier:
            self.assertGreaterEqual(n, 10, f"层词汇不足: {n}")

    def test_all_skill_keys_valid(self):
        """所有词的 skill key 必须命中真实实现的技能分支，防止买到哑巴棋子。"""
        import inspect
        import re
        from games import word_arena
        src = (
            inspect.getsource(word_arena.WordArenaGame)
            + inspect.getsource(word_arena.extra_clash_count)
            + inspect.getsource(word_arena.is_snipe)
        )
        referenced = set(re.findall(
            r'(?:skill == |_has_skill\([^)]*,\s*|skill in \()"([a-z_]+)"', src
        ))
        for m in re.finditer(r'skill in \(([^)]*)\)', src):
            referenced.update(re.findall(r'"([a-z_]+)"', m.group(1)))
        referenced.update(word_arena.SNIPE_SKILLS)
        bad = [w for w in WORDS if w.skill not in referenced]
        self.assertEqual(bad, [], f"技能 key 未实现: {[(w.word, w.skill) for w in bad]}")

    def test_fx_state_no_crash(self):
        """战斗 fx 状态在无战斗时不崩、可清理。"""
        g = WordArenaGame(_session())
        g._click_start_game()
        g._update_battle_fx(0.1)
        self.assertEqual(g._lunges, [])
        self.assertIsNone(g._clash_phase)
        self.assertEqual(g._faint_fx, [])
        self.assertEqual(g._spawn_fx, [])
        self.assertEqual(g._block_fx, [])

    def test_cookie_roundtrip(self):
        """cookie 标记经 lineup 序列化往返不丢失。"""
        u = Unit.from_spec(WORDS[0])
        u.cookie = True
        back = unit_from_dict(unit_to_dict(u))
        self.assertTrue(back.cookie)

    def test_unit_from_dict_sets_cookie(self):
        u = Unit.from_spec(WORDS[0])
        u.cookie = True
        d = unit_to_dict(u)
        self.assertTrue(d["cookie"])
        back = unit_from_dict(d)
        self.assertTrue(back.cookie)


def _plain(spec, atk: int, hp: int) -> Unit:
    u = Unit.from_spec(spec)
    u.atk, u.hp, u.max_hp = atk, hp, hp
    u.skill = "monitor"
    return u


def _clashes(events):
    return [ev for ev in events if ev.get("type") == "clash"]


def _hits(events):
    out = []
    for ev in events:
        if ev.get("type") == "clash":
            out.extend(ev.get("hits") or [])
        elif ev.get("type") == "hit":
            out.append(ev)
    return out


class WordArenaSimultaneousBattleTests(unittest.TestCase):
    """前排对撞：同时互打、后排等待、clash 事件。"""

    def setUp(self):
        self.game = WordArenaGame(_session())

    def test_front_index_and_draw_col(self):
        self.assertEqual(battle_draw_col("p", 0), MAX_TEAM - 1)
        self.assertEqual(battle_draw_col("e", 0), 0)
        p = _plain(WORDS[0], 1, 1)
        board = [None, p, None]
        self.assertEqual(front_index(board), 1)
        self.assertEqual(extra_clash_count(p), 0)
        loop = Unit.from_spec(next(w for w in WORDS if w.skill == "loop"))
        self.assertEqual(extra_clash_count(loop), 1)

    def test_simultaneous_trade_both_hit_before_faint(self):
        """1v1 同攻同血：一条 clash 里两边 hit，after 里才 faint。"""
        g = self.game
        g._click_start_game()
        g.team[0] = _plain(WORDS[0], 2, 2)
        g.enemy_team = [_plain(WORDS[1], 2, 2)]
        g._start_battle()
        clashes = _clashes(g.battle_events)
        self.assertTrue(clashes, "应有对撞事件")
        first = clashes[0]
        sides = {h.get("target_side") for h in first.get("hits") or []}
        self.assertEqual(sides, {"p", "e"})
        self.assertTrue(any(a.get("type") == "faint" for a in first.get("after") or []))

    def test_backline_waits_until_front_falls(self):
        """默认对撞只有槽 0 出手；后排在前排倒下前不当攻击者。"""
        g = self.game
        g._click_start_game()
        g.team[0] = _plain(WORDS[0], 2, 8)
        g.team[1] = _plain(WORDS[1], 2, 8)
        g.team[2] = _plain(WORDS[2], 2, 8)
        g.enemy_team = [_plain(WORDS[3], 2, 8)]
        g._start_battle()
        first = _clashes(g.battle_events)[0]
        p_slots = {L["slot"] for L in first["lunges"] if L["side"] == "p"}
        self.assertEqual(p_slots, {0})
        p1_hit = [
            h for h in first.get("hits") or []
            if h.get("attacker_side") == "p" and h.get("attacker_slot") == 1
        ]
        self.assertFalse(p1_hit)

    def test_front_slot_targeted_first(self):
        """多单位对打：前排（槽 0）先被打。"""
        g = self.game
        g._click_start_game()
        g.team[0] = _plain(WORDS[0], 2, 8)
        g.team[1] = _plain(WORDS[1], 2, 2)
        g.enemy_team = [_plain(WORDS[2], 2, 8)]
        g._start_battle()
        first_e = next(h for h in _hits(g.battle_events) if h.get("attacker_side") == "e")
        self.assertEqual(first_e.get("target_slot"), 0)

    def test_queue_extra_clash_is_mutual(self):
        """queue：首次对撞后再与对方前排互打。"""
        g = self.game
        g._click_start_game()
        queue_spec = next(w for w in WORDS if w.skill == "queue")
        p = Unit.from_spec(queue_spec)
        p.atk, p.hp, p.max_hp = 1, 20, 20
        e = _plain(WORDS[1], 1, 20)
        g.team[0] = p
        g.enemy_team = [e]
        g._start_battle()
        clashes = _clashes(g.battle_events)
        self.assertGreaterEqual(len(clashes), 2, "queue 应多一次完整互打")
        p_hits = [h for h in _hits(g.battle_events) if h.get("attacker_side") == "p"]
        e_hits = [h for h in _hits(g.battle_events) if h.get("attacker_side") == "e"]
        self.assertGreaterEqual(len(p_hits), 2)
        self.assertGreaterEqual(len(e_hits), 2, "额外出手也是互打")

    def test_loop_extra_clash_is_mutual(self):
        g = self.game
        g._click_start_game()
        loop = Unit.from_spec(next(w for w in WORDS if w.skill == "loop"))
        loop.atk, loop.hp, loop.max_hp = 1, 20, 20
        g.team[0] = loop
        g.enemy_team = [_plain(WORDS[1], 1, 20)]
        g._start_battle()
        clashes = _clashes(g.battle_events)
        self.assertGreaterEqual(len(clashes), 2)
        for ev in clashes[:2]:
            sides = {h.get("target_side") for h in ev.get("hits") or []}
            self.assertEqual(sides, {"p", "e"})

    def test_mybatis_targets_lowest_hp(self):
        """mybatis：打血最少目标且伤害+2。"""
        g = self.game
        g._click_start_game()
        mb = Unit.from_spec(next(w for w in WORDS if w.skill == "mybatis"))
        mb.atk, mb.hp, mb.max_hp = 2, 20, 20
        e1, e2 = _plain(WORDS[1], 1, 10), _plain(WORDS[2], 1, 3)
        g.team[0] = mb
        g.enemy_team = [e1, e2]
        g._start_battle()
        first = _clashes(g.battle_events)[0]
        p_hits = [h for h in first.get("hits") or [] if h.get("attacker_side") == "p"]
        self.assertTrue(p_hits)
        self.assertEqual(p_hits[0].get("target_slot"), 1, "mybatis 应打血最少(槽1)")
        self.assertGreaterEqual(p_hits[0].get("damage", 0), 4, "伤害应含 +2")

    def test_sentinel_shield_blocks_two(self):
        """sentinel：开局 _shield=2 → 前两次受击是 block 而非 hit。"""
        g = self.game
        g._click_start_game()
        sn = Unit.from_spec(next(w for w in WORDS if w.skill == "sentinel"))
        sn.atk, sn.hp, sn.max_hp = 4, 80, 80
        e = _plain(WORDS[1], 3, 40)
        g.team[0] = sn
        g.enemy_team = [e]
        g._start_battle()
        incoming = []
        for ev in _clashes(g.battle_events):
            for b in ev.get("blocks") or []:
                if b.get("target_side") == "p" and b.get("target_slot") == 0:
                    incoming.append("block")
            for h in ev.get("hits") or []:
                if h.get("target_side") == "p" and h.get("target_slot") == 0:
                    incoming.append("hit")
        self.assertGreaterEqual(len(incoming), 2)
        self.assertEqual(incoming[:2], ["block", "block"])

    def test_new_java_words_present(self):
        """新 Java 八股词全部存在且归属正确。"""
        from collections import Counter
        by_word = {w.word: w for w in WORDS}
        expected = {
            "springboot": 3, "mybatis": 3, "redis": 3, "mq": 3, "alibaba": 3,
            "nginx": 4, "dubbo": 4, "sentinel": 4, "ruoyi": 4, "jvm": 4, "tomcat": 4,
            "threadpool": 5, "volatile": 5, "hashmap": 5,
        }
        for word, tier in expected.items():
            self.assertIn(word, by_word, f"缺少词汇 {word}")
            self.assertEqual(by_word[word].tier, tier, f"{word} 层归属")
        per_tier = Counter(w.tier for w in WORDS)
        self.assertGreaterEqual(len(WORDS), 70)

    def test_dummy_run_converges(self):
        """dummy 整局收敛：事件总数有上限，不重现虫海死循环。"""
        g = self.game
        g.auto_dummy = True
        g._click_start_game()
        g.dummy_t = 2.0  # 跳过商店买窗，直接开战
        import time
        t0 = time.time()
        guard = 0
        # 最多约 20 回合（9 胜 + 生命耗尽）；商店买窗 + 战斗回放可达数千帧
        while g.phase != "over" and guard < 25000 and time.time() - t0 < 30:
            guard += 1
            dt = 1 / 60
            if g.phase == "battle":
                g._update_battle(dt)
                g._update_battle_fx(dt)
                g.battle_event_cooldown = 0.0  # dummy 加速
            g._dummy_step(dt)
        self.assertEqual(g.phase, "over", "dummy 应能跑完整局")
        self.assertLess(len(g.battle_events), 2000, "事件数不应爆炸")


class WordArenaSynergyGrowthTests(unittest.TestCase):
    def setUp(self):
        self.game = WordArenaGame(_session())

    def _spec(self, skill: str):
        return next(w for w in WORDS if w.skill == skill)

    def test_printed_bonus_zero_then_apple(self):
        u = Unit.from_spec(WORD_BY_KEY["bug"])
        self.assertEqual(bonus_atk(u), 0)
        self.assertEqual(bonus_hp(u), 0)
        self.assertEqual(printed_atk(u), u.total_atk)
        g = self.game
        g._click_start_game()
        g.team[0] = u
        g.selected_slot = 0
        g.copper = 10
        apple = next(f for f in FOODS if f.effect == "apple")
        self.assertTrue(g._buy_food(apple))
        self.assertEqual(bonus_atk(g.team[0]), 1)
        self.assertEqual(bonus_hp(g.team[0]), 1)
        g._draw()
        rect = next(r for r, a, args in g._click_zones if a == "team_slot" and args == (0,))
        blob = "\n".join(g._hover_lines_at(rect.center) or [])
        self.assertIn("局内", blob)
        self.assertIn("+1攻", blob)

    def test_occupied_ahead_behind_skips_empty(self):
        a, b = Unit.from_spec(WORDS[0]), Unit.from_spec(WORDS[1])
        board = [a, None, b, None, None]
        self.assertIs(occupied_behind(board, 0), b)
        self.assertIs(occupied_ahead(board, 2), a)
        self.assertIsNone(occupied_behind(board, 2))

    def test_buffer_buffs_behind_hp(self):
        g = self.game
        g._click_start_game()
        carry = Unit.from_spec(WORD_BY_KEY["bug"])
        base = carry.max_hp
        g.team[0] = Unit.from_spec(self._spec("buffer"))
        g.team[1] = carry
        g.enemy_team = [_plain(WORDS[2], 1, 20)]
        g._start_battle()
        ev = next(e for e in g.battle_events if e.get("type") == "skill" and "缓冲" in e.get("msg", ""))
        slot1 = next(s for s in ev["stats"] if s["side"] == "p" and s["slot"] == 1)
        self.assertEqual(slot1["max_hp"], base + 2)
        self.assertEqual(g.team[1].max_hp, base)

    def test_callback_buffs_ahead_atk(self):
        g = self.game
        g._click_start_game()
        front = Unit.from_spec(WORD_BY_KEY["bug"])
        base = front.total_atk
        g.team[0] = front
        g.team[1] = Unit.from_spec(self._spec("callback"))
        g.enemy_team = [_plain(WORDS[2], 1, 20)]
        g._start_battle()
        ev = next(e for e in g.battle_events if e.get("type") == "skill" and "回调" in e.get("msg", ""))
        slot0 = next(s for s in ev["stats"] if s["side"] == "p" and s["slot"] == 0)
        self.assertEqual(slot0["atk"] + slot0["buffed_atk"], base + 2)

    def test_pointer_buffs_behind_atk(self):
        g = self.game
        g._click_start_game()
        carry = Unit.from_spec(WORD_BY_KEY["bug"])
        base = carry.total_atk
        g.team[0] = Unit.from_spec(self._spec("pointer"))
        g.team[1] = carry
        g.enemy_team = [_plain(WORDS[2], 1, 20)]
        g._start_battle()
        ev = next(e for e in g.battle_events if e.get("type") == "skill" and "指针" in e.get("msg", ""))
        slot1 = next(s for s in ev["stats"] if s["side"] == "p" and s["slot"] == 1)
        self.assertEqual(slot1["atk"] + slot1["buffed_atk"], base + 2)

    def test_memory_raises_max_hp_on_combat_copy(self):
        g = self.game
        g._click_start_game()
        m = Unit.from_spec(self._spec("memory"))
        shop_hp = m.max_hp
        g.team[0] = m
        g.enemy_team = [_plain(WORDS[1], 1, 20)]
        g._start_battle()
        ev = next(e for e in g.battle_events if e.get("type") == "skill" and "+2 血" in e.get("msg", ""))
        slot0 = next(s for s in ev["stats"] if s["side"] == "p" and s["slot"] == 0)
        self.assertEqual(slot0["max_hp"], shop_hp + 2)
        self.assertEqual(g.team[0].max_hp, shop_hp)

    def test_kernel_skill_stats_apply_to_visual(self):
        g = self.game
        g._click_start_game()
        k = Unit.from_spec(self._spec("kernel"))
        g.team[0] = k
        g.enemy_team = [_plain(WORDS[1], 1, 20)]
        g._start_battle()
        ev = next(e for e in g.battle_events if e.get("type") == "skill" and "核心" in e.get("msg", ""))
        self.assertTrue(ev.get("stats"))
        before = g.battle_players[0].total_atk
        g._apply_visual(ev)
        self.assertEqual(g.battle_players[0].total_atk, before + 4)

    def test_heap_hit_snapshot_includes_ally_buff(self):
        g = self.game
        g._click_start_game()
        heap = Unit.from_spec(self._spec("heap"))
        heap.atk, heap.hp, heap.max_hp = 1, 20, 20
        ally = _plain(WORDS[0], 2, 20)
        g.team[0] = heap
        g.team[1] = ally
        g.enemy_team = [_plain(WORDS[2], 1, 20)]
        g._start_battle()
        found = False
        for h in _hits(g.battle_events):
            if h.get("target_side") != "p" or h.get("target_slot") != 0:
                continue
            for s in h.get("stats") or []:
                if s.get("side") == "p" and s.get("slot") == 1 and int(s.get("buffed_atk", 0)) >= 1:
                    found = True
        self.assertTrue(found, "heap 受击后 hit.stats 应含队友 +1 攻")

    def test_mutex_locks_once(self):
        g = self.game
        g._click_start_game()
        mx = Unit.from_spec(self._spec("mutex"))
        mx.atk, mx.hp, mx.max_hp = 1, 2, 2
        g.team[0] = mx
        g.enemy_team = [_plain(WORDS[1], 5, 40)]
        g._start_battle()
        blocks = []
        for ev in g.battle_events:
            if ev.get("type") == "clash":
                blocks.extend(ev.get("blocks") or [])
        self.assertTrue(any("锁血" in (b.get("msg") or "") for b in blocks))

    def test_restore_revives_front(self):
        g = self.game
        g._click_start_game()
        tank = _plain(WORDS[0], 1, 1)
        res = Unit.from_spec(self._spec("restore"))
        res.atk, res.hp, res.max_hp = 1, 30, 30
        g.team[0] = tank
        g.team[1] = res
        g.enemy_team = [_plain(WORDS[1], 1, 30)]
        g._start_battle()
        after_msgs = []
        for ev in g.battle_events:
            if ev.get("type") == "clash":
                after_msgs.extend(a.get("msg", "") for a in ev.get("after") or [])
        self.assertTrue(any("复活" in m for m in after_msgs))

    def test_sigkill_executes_low_hp(self):
        g = self.game
        g._click_start_game()
        sk = Unit.from_spec(self._spec("sigkill"))
        sk.atk, sk.hp, sk.max_hp = 1, 20, 20
        g.team[0] = sk
        g.enemy_team = [_plain(WORDS[1], 1, 4)]
        g._start_battle()
        msgs = [h.get("msg", "") for h in _hits(g.battle_events)]
        self.assertTrue(any("强制终止" in m for m in msgs))

    def test_cron_grows_next_shop_round(self):
        g = self.game
        g._click_start_game()
        c = Unit.from_spec(self._spec("cron"))
        atk0, hp0 = c.atk, c.max_hp
        g.team[0] = c
        g.phase = "battle_res"
        g.trophies = 0
        g.lives = 5
        g._next_round_or_end()
        self.assertEqual(g.team[0].atk, atk0 + 1)
        self.assertEqual(g.team[0].max_hp, hp0 + 1)

    def test_watch_grows_on_paid_word_refresh(self):
        g = self.game
        g._click_start_game()
        w = Unit.from_spec(self._spec("watch"))
        hp0 = w.max_hp
        g.team[0] = w
        g.copper = 5
        self.assertTrue(g._roll_word_shop(charge=True))
        self.assertEqual(g.team[0].max_hp, hp0 + 1)

    def test_wrapper_armor_blocks_one_damage(self):
        g = self.game
        g._click_start_game()
        carry = Unit.from_spec(WORD_BY_KEY["bug"])
        g.team[0] = Unit.from_spec(self._spec("wrapper"))
        g.team[1] = carry
        g.enemy_team = [_plain(WORDS[1], 2, 20)]
        g._start_battle()
        ev = next(e for e in g.battle_events if e.get("type") == "skill" and "包装" in e.get("msg", ""))
        slot1 = next(s for s in ev["stats"] if s["side"] == "p" and s["slot"] == 1)
        self.assertEqual(int(slot1.get("armor", 0)), 1)

    def test_synergy_words_present(self):
        by_skill = {w.skill: w for w in WORDS}
        for key in (
            "buffer", "pointer", "wrapper", "shuffle", "callback",
            "cron", "restore", "mutex", "watch", "sigkill",
        ):
            self.assertIn(key, by_skill, key)


class GameLaunchStdioTests(unittest.TestCase):
    def test_stdout_not_piped(self):
        """capture_output 会把 pygame 的 stdout 堵死，游戏表现为卡顿。"""
        kw = game_subprocess_stdio()
        self.assertIs(kw["stdout"], subprocess.DEVNULL)
        self.assertIs(kw["stderr"], subprocess.PIPE)
