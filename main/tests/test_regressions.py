import asyncio
import csv
import copy
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evac import Agent, DataPersistenceError, EvacSim, MAX_DURATION
import main


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        maze = SimpleNamespace(mw=4, mh=4, cell=32, world_w=128, world_h=128,
                               walls=[[0] * 4 for _ in range(4)], exit=(3, 3), complexity=1)
        self.sim = EvacSim(maze, Path(self.tmp.name))
        self.sim.agents['ptest'] = Agent('ptest', '测试', '#fff', 16, 16, delay=100)

    def test_pause_and_end_freeze_time_and_health(self):
        sim = self.sim
        sim.start()
        sim.step(.02, sim.now + 100)
        self.assertAlmostEqual(sim.elapsed, .02, places=5)
        sim.pause()
        before = sim.now
        sim.step(.05, before + 1000)
        self.assertEqual(sim.now, before)
        sim.start()  # A start request while paused resumes, without resetting time.
        sim.step(.02, before + 1001)
        self.assertAlmostEqual(sim.elapsed, .04, places=5)
        sim._finish()
        agent = sim.agents['ptest']
        state = (sim.now, agent.x, agent.y, agent.health, agent.path_len)
        sim.burning.add((0, 0))
        for _ in range(20):
            sim.step(.05, before + 2000)
        self.assertEqual((sim.now, agent.x, agent.y, agent.health, agent.path_len), state)

    def test_duplicate_start_does_not_reset_run(self):
        sim = self.sim
        sim.start()
        sim.step(.02, sim.now)
        before = (sim.t0, sim.now, list(sim.flow))
        sim.start()
        self.assertEqual((sim.t0, sim.now, sim.flow), before)

    def test_timeout_and_restart(self):
        sim = self.sim
        sim.start()
        sim.now = sim.t0 + MAX_DURATION - .01
        sim.step(.05, sim.now + 1)
        self.assertEqual(sim.phase, 'ended')
        self.assertAlmostEqual(sim.elapsed, MAX_DURATION, places=5)
        sim.start()
        self.assertEqual(sim.phase, 'running')
        self.assertEqual(sim.elapsed, 0)
        self.assertTrue(all(not a.failed and not a.escaped for a in sim.agents.values()))

    def test_smoke_cells_are_published_for_display(self):
        sim = self.sim
        self.assertEqual(sim.public_state()['smoke'], [])
        self.assertTrue(sim.ignite(0, 0))
        seeded = sim.smoke_cells()
        self.assertTrue(any(c[0] == 0 and c[1] == 0 and c[2] >= 0.5 for c in seeded))
        self.assertEqual(sim.public_state()['smoke'], seeded)
        sim.start()
        for _ in range(30):
            sim.step(.05, sim.now)
        later = sim.smoke_cells()
        self.assertGreater(sim.smoke[0][0], 0.2)
        self.assertGreaterEqual(len(later), len(seeded))
        self.assertTrue(all(c[2] >= 0.03 for c in later))

    def test_idle_fire_does_not_damage_people(self):
        sim = self.sim
        sim.burning.add((0, 0))
        for _ in range(20):
            sim.step(.05, sim.now + 1)
        self.assertEqual(sim.agents['ptest'].health, 1)

    def test_human_without_input_stays_still_after_delay(self):
        sim = self.sim
        agent = sim.agents['ptest']
        agent.is_human = True
        sim.start()
        agent.delay = 0
        for _ in range(100):
            sim.step(.02, sim.now)
        self.assertEqual((agent.x, agent.y, agent.vx, agent.vy), (16, 16, 0, 0))
        self.assertEqual(agent.started_at, 0)

    def test_human_manual_motion_and_release_deceleration(self):
        sim = self.sim
        agent = sim.agents['ptest']
        agent.is_human = True
        sim.start()
        agent.delay = 0
        agent.ix = 1
        for _ in range(20):
            sim.step(.02, sim.now)
        self.assertGreater(agent.x, 16)
        self.assertGreater(agent.vx, 0)
        self.assertEqual(agent.y, 16)
        agent.ix = 0
        for _ in range(50):
            previous_speed = agent.vx
            sim.step(.02, sim.now)
            self.assertGreaterEqual(agent.vx, 0)
            self.assertLess(agent.vx, previous_speed)
            self.assertEqual(agent.vy, 0)

    def test_npc_still_moves_toward_exit(self):
        sim = self.sim
        agent = sim.agents['ptest']
        sim.start()
        agent.delay = 0
        for _ in range(20):
            sim.step(.02, sim.now)
        self.assertGreater(agent.path_len, 0)
        self.assertGreater(agent.x + agent.y, 32)

    def test_speed_scales_time_physics_smoke_and_damage_together(self):
        sim = self.sim
        sim.start()
        sim.agents['ptest'].delay = 0
        sim.ignite(0, 0)
        fast = copy.deepcopy(sim)
        fast.set_time_scale(4)
        sim.advance(.2)
        fast.advance(.05)
        self.assertAlmostEqual(fast.elapsed, .2, places=5)
        self.assertEqual(fast.snapshot_agents(), sim.snapshot_agents())
        self.assertEqual(fast.smoke, sim.smoke)
        self.assertEqual(fast.agents['ptest'].health, sim.agents['ptest'].health)
        # 除真实时间轴(real_t)外，仿真量必须逐一相等：倍速只改变仿真时间流速。
        strip = lambda t: [row[:13] for row in t]
        self.assertEqual(strip(fast.agents['ptest'].track),
                         strip(sim.agents['ptest'].track))
        # 真实耗时不同(4x 用 .05s 真实时间推进 .2s 仿真)，这正是需要记录的信息。
        self.assertNotEqual(fast.agents['ptest'].track[0][13],
                            sim.agents['ptest'].track[0][13])
        self.assertAlmostEqual(sim.agents['ptest'].track[0][13], .2, places=5)
        self.assertAlmostEqual(fast.agents['ptest'].track[0][13], .05, places=5)

    def test_slow_speed_pause_and_switch_history(self):
        sim = self.sim
        sim.set_time_scale(.25)
        sim.start()
        sim.advance(.2)
        self.assertAlmostEqual(sim.elapsed, .05, places=5)
        sim.pause()
        sim.set_time_scale(2)
        sim.advance(10)
        self.assertAlmostEqual(sim.elapsed, .05, places=5)
        sim.pause(False)
        sim.advance(.1)
        self.assertAlmostEqual(sim.elapsed, .25, places=5)
        self.assertEqual(sim.build_summary()['speed_changes'], [[0, .25], [.05, 2]])
        self.assertEqual(sim.public_state()['time_scale'], 2)

    def test_high_speed_stops_at_simulation_limit(self):
        sim = self.sim
        sim.start()
        sim.now = sim.t0 + MAX_DURATION - .01
        sim.set_time_scale(4)
        sim.advance(.25)
        self.assertEqual(sim.phase, 'ended')
        self.assertAlmostEqual(sim.elapsed, MAX_DURATION, places=5)
        sim.advance(.25)
        self.assertAlmostEqual(sim.elapsed, MAX_DURATION, places=5)
        sim.reset_crowd()
        self.assertEqual(sim.time_scale, 4)
        self.assertEqual(sim.speed_changes, [])

    def test_invalid_speed_is_rejected_without_changing_state(self):
        for speed in (0, -1, .1, 5, float('nan'), float('inf')):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                self.sim.set_time_scale(speed)
            self.assertEqual(self.sim.time_scale, 1)

    def test_wind_is_bounded_and_recorded(self):
        sim = self.sim
        sim.set_wind(1.5, -0.5)
        self.assertAlmostEqual(sim.public_state()["wind"]["speed"], (1.5 ** 2 + .5 ** 2) ** .5, places=2)
        self.assertEqual(sim.build_summary()["wind"]["x"], 1.5)
        for value in ((8.1, 0), (0, -8.1), (float("nan"), 0)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sim.set_wind(*value)

    def test_npc_count_can_shrink_grow_and_reach_zero(self):
        sim = self.sim
        sim.ensure_population(7)
        self.assertEqual(sum(not a.is_human for a in sim.agents.values()), 7)
        sim.set_npc_count(13)
        self.assertEqual(sum(not a.is_human for a in sim.agents.values()), 13)
        sim.set_npc_count(0)
        self.assertEqual(sum(not a.is_human for a in sim.agents.values()), 0)
        self.assertEqual(sim.public_state()['n'], 0)

    def test_humans_are_independent_from_npc_target(self):
        sim = self.sim
        sim.ensure_population(5)
        human = sim.add_human('p-human', '玩家', '#fff')
        self.assertTrue(human.is_human)
        self.assertEqual(sum(not a.is_human for a in sim.agents.values()), 5)
        self.assertEqual(sim.public_state()['human_count'], 1)
        sim.set_npc_count(2)
        self.assertEqual(sum(not a.is_human for a in sim.agents.values()), 2)
        self.assertIn('p-human', sim.agents)

    def test_npc_count_is_bounded(self):
        for value in (-1, 301, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.sim.set_npc_count(value)

    def test_multiple_exits_use_multi_source_field_and_any_exit_escapes(self):
        self.sim.maze.exits = [(0, 0), (3, 3)]
        self.sim.maze.exit = (0, 0)
        self.sim._rebuild_field()
        self.assertEqual(self.sim.dist[0][0], 0)
        self.assertEqual(self.sim.dist[3][3], 0)
        self.sim.start()
        agent = self.sim.agents['ptest']
        agent.is_human = True
        agent.x = 3 * self.sim.maze.cell + 16
        agent.y = 3 * self.sim.maze.cell + 16
        agent.ix = agent.iy = 0
        self.sim.step(.02, self.sim.now)
        self.assertTrue(agent.escaped)
        self.assertFalse(self.sim.ignite(0, 0))
        self.assertFalse(self.sim.ignite(3, 3))

    def test_reconnect_preserves_outcome(self):
        agent = self.sim.agents['ptest']
        agent.escaped = True
        self.sim.add_human('ptest', '重连', '#fff')
        self.assertTrue(agent.escaped)

    def test_csv_quotes_fields_and_neutralizes_formulas(self):
        agent = self.sim.agents['ptest']
        agent.name = '张,\"三\"\n测试'
        agent.track = [[0, 16, 16, 0, 0, 0, 0, 0, 1]]
        rows = list(csv.reader(io.StringIO(self.sim.export_csv())))
        self.assertEqual(rows[1][1], agent.name)
        self.assertEqual(len(rows[1]), len(rows[0]))
        agent.name = '=1+1'
        rows = list(csv.reader(io.StringIO(self.sim.export_csv())))
        self.assertEqual(rows[1][1], "'=1+1")

    def test_npc_does_not_enter_burning_cell(self):
        sim = self.sim
        agent = sim.agents['ptest']
        agent.delay = 0
        self.assertTrue(sim.ignite(1, 0))
        sim.start()
        for _ in range(80):
            sim.step(.02, sim.now)
        gx = int(agent.x // sim.maze.cell)
        gy = int(agent.y // sim.maze.cell)
        self.assertNotEqual((gx, gy), (1, 0))
        self.assertFalse(agent.failed)

    def test_human_can_walk_into_fire(self):
        sim = self.sim
        agent = sim.agents['ptest']
        agent.is_human = True
        agent.delay = 0
        agent.ix = 1
        self.assertTrue(sim.ignite(1, 0))
        sim.start()
        for _ in range(120):
            sim.step(.02, sim.now)
        self.assertGreaterEqual(int(agent.x // sim.maze.cell), 1)

    def test_trapped_npc_stops_instead_of_entering_fire(self):
        sim = self.sim
        agent = sim.agents['ptest']
        agent.delay = 0
        for x in range(4):
            for y in range(4):
                if (x, y) not in {(0, 0), (3, 3)}:
                    sim.burning.add((x, y))
        sim._rebuild_field()
        self.assertEqual(sim.dist[0][0], -1)
        sim.start()
        for _ in range(40):
            sim.step(.02, sim.now)
        self.assertEqual((int(agent.x // 32), int(agent.y // 32)), (0, 0))
        self.assertFalse(agent.failed)

    def test_fail_cause_and_time_are_recorded(self):
        sim = self.sim
        agent = sim.agents['ptest']
        agent.is_human = True
        sim.maze.walls[0][0] = 15
        sim.burning.add((0, 0))
        sim.start()
        for _ in range(250):
            sim.step(.02, sim.now)
        self.assertTrue(agent.failed)
        self.assertEqual(agent.fail_cause, 'fire')
        summary = sim.build_summary()
        row = next(item for item in summary['agents'] if item['pid'] == 'ptest')
        self.assertEqual(row['fail_cause'], 'fire')
        self.assertGreater(row['t_fail'], 0)

    def test_smoke_failure_is_labeled(self):
        sim = self.sim
        agent = sim.agents['ptest']
        agent.is_human = True
        sim.start()
        for _ in range(600):
            sim.smoke[0][0] = 1.0
            sim.step(.02, sim.now)
        self.assertTrue(agent.failed)
        self.assertEqual(agent.fail_cause, 'smoke')

    def test_finish_writes_tracks_csv(self):
        sim = self.sim
        sim.start()
        sim._finish()
        self.assertTrue((Path(self.tmp.name) / 'evac_latest_tracks.csv').exists())
        self.assertTrue(any(Path(self.tmp.name).glob('evac_*_tracks.csv')))

    def test_same_seed_reproduces_maze(self):
        main.rebuild_maze(3, 3, seed=42)
        first = [row[:] for row in main.maze.walls]
        main.rebuild_maze(3, 3, seed=42)
        self.assertEqual(main.maze.walls, first)
        main.rebuild_maze(3, 3, seed=43)
        self.assertNotEqual(main.maze.walls, first)

    def test_scenario_roundtrip_keeps_fire_and_population(self):
        sim = self.sim
        sim.ensure_population(4)
        self.assertTrue(sim.ignite(0, 0))
        data = sim.export_scenario()
        self.assertEqual(data['version'], 1)
        self.assertEqual(data['npc_target'], 4)
        self.assertIn([0, 0], data['fire'])
        sim.burning.clear()
        sim.npc_target = 1
        sim.apply_scenario_state(data)
        self.assertIn((0, 0), sim.burning)
        self.assertEqual(sim.npc_target, 4)
        self.assertEqual(sum(not a.is_human for a in sim.agents.values()), 4)

    def test_unknown_scenario_version_is_rejected_before_archive(self):
        sim = self.sim
        original = (sim.npc_target, sim.panic, sim.time_scale, sim.trial_seq,
                    sim.maze.mw, sim.maze.mh, copy.deepcopy(sim.maze.walls))
        with patch.object(sim, 'archive_trial') as archive:
            with self.assertRaises(ValueError):
                sim.apply_scenario_state({'version': 2, 'npc_target': 7})
        archive.assert_not_called()
        self.assertEqual(
            (sim.npc_target, sim.panic, sim.time_scale, sim.trial_seq,
             sim.maze.mw, sim.maze.mh, sim.maze.walls),
            original,
        )

    def test_invalid_scenario_is_rejected_without_partial_mutation(self):
        sim = self.sim
        sim.ensure_population(0)
        sim.add_human('p1', '甲', '#fff')
        sim.start()
        sim.agents['p1'].track = [[0.0, 16, 16, 0, 0, 0, 0, 0, 1]]
        original = (sim.npc_target, sim.panic, sim.time_scale, sim.wind_x,
                    sim.wind_y, sim.spawn_zone, set(sim.burning), sim.trial_seq,
                    copy.deepcopy(sim.agents['p1'].track))
        bad_scenario = {"npc_target": 5, "panic": .9, "time_scale": 2,
                        "wind": {"x": 2, "y": 0}, "spawn_zone": [0, 0, 1, 1],
                        "fire": [[99, 99]]}
        with self.assertRaises(ValueError):
            sim.apply_scenario_state(bad_scenario)
        after = (sim.npc_target, sim.panic, sim.time_scale, sim.wind_x,
                 sim.wind_y, sim.spawn_zone, set(sim.burning), sim.trial_seq,
                 sim.agents['p1'].track)
        self.assertEqual(after, original)


class DataDictionaryTests(unittest.TestCase):
    """轨迹 CSV 的列语义必须逐行正确，否则分析会得出错误结论。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        maze = SimpleNamespace(mw=4, mh=4, cell=32, world_w=128, world_h=128,
                               walls=[[0] * 4 for _ in range(4)], exit=(3, 3),
                               exits=[(3, 3)], complexity=1)
        self.sim = EvacSim(maze, Path(self.tmp.name))

    def test_escaped_flag_is_per_row_not_final_state(self):
        sim = self.sim
        agent = sim.add_human('p1', '甲', '#fff')
        sim.ensure_population(0)
        sim.start()
        # 手动造:先在中途,再逃生。
        agent.track = [[0.0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 5.0, 5.0, 0.0],
                       [9.0, 96, 96, 0, 0, 0, 0, 0, 1, 0, 0, 5.0, 5.0, 4.0]]
        agent.escaped = True
        agent.escaped_at = sim.t0 + 9.0
        rows = list(csv.DictReader(io.StringIO(sim.export_csv())))
        self.assertEqual([r['state'] for r in rows], ['active', 'escaped'])
        # 整场结局是常量列,与逐行状态分离。
        self.assertEqual({r['escaped_total'] for r in rows}, {'1'})

    def test_failed_flag_is_final_column_only(self):
        sim = self.sim
        agent = sim.add_human('p1', '甲', '#fff')
        sim.ensure_population(0)
        sim.start()
        agent.track = [[0.0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1.0, 1.0, 0.0],
                       [3.0, 10, 10, 0, 0, 0, 0, 0, 1, 0, 0, 1.0, 1.0, 2.0]]
        agent.failed = True
        agent.failed_at = sim.t0 + 3.0
        agent.fail_cause = 'smoke'
        rows = list(csv.DictReader(io.StringIO(sim.export_csv())))
        self.assertEqual([r['state'] for r in rows], ['active', 'failed'])
        self.assertEqual({r['failed_total'] for r in rows}, {'1'})
        self.assertEqual({r['fail_cause'] for r in rows}, {'smoke'})

    def test_instant_and_min_fire_distance_are_separate_columns(self):
        sim = self.sim
        agent = sim.add_human('p1', '甲', '#fff')
        sim.ensure_population(0)
        sim.start()
        # min 单调不增;instant 可以回升。两者必须是不同的列。
        agent.track = [[0.0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 12.0, 12.0, 0.0],
                       [1.0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 12.0, 30.0, 1.0]]
        rows = list(csv.DictReader(io.StringIO(sim.export_csv())))
        self.assertEqual(rows[1]['min_fire_distance_m'], '12.0')
        self.assertEqual(rows[1]['fire_distance_m'], '30.0')

    def test_real_time_is_recorded_alongside_sim_time(self):
        sim = self.sim
        sim.ensure_population(0)
        agent = sim.add_human('p1', '甲', '#fff')
        # 固定在远离出口的格子里且不移动,避免提前逃生导致本场结束。
        agent.x, agent.y = 16, 16
        sim.set_time_scale(4)
        sim.start()
        for _ in range(10):
            sim.advance(.05)
        self.assertEqual(sim.phase, 'running')
        # 仿真时间被放大 4 倍,真实时间保持原始值。
        self.assertAlmostEqual(sim.real_t, .5, places=5)
        self.assertAlmostEqual(sim.elapsed, 2.0, places=5)
        rows = list(csv.DictReader(io.StringIO(sim.export_csv())))
        self.assertTrue(rows)
        # 真实时间轴单调递增,末行等于累计真实耗时。
        real = [float(r['real_t']) for r in rows]
        self.assertEqual(real, sorted(real))
        self.assertAlmostEqual(real[-1], .5, places=2)
        self.assertAlmostEqual(float(rows[-1]['t']), 2.0, places=2)

    def test_real_time_reaction_is_comparable_across_speeds(self):
        # 同样 1 秒真实反应时间,在不同倍速下 real 值一致而 t 值不同。
        observed = []
        for scale in (1.0, 4.0):
            sim = EvacSim(self.sim.maze, Path(self.tmp.name))
            sim.ensure_population(0)
            agent = sim.add_human('p1', '甲', '#fff')
            agent.x, agent.y = 16, 16
            sim.set_time_scale(scale)
            sim.start()
            sim.advance(1.0)          # 1 秒真实时间,但人未操作
            sim.agents['p1'].ix = 1
            sim.advance(0.1)
            used = sim.agents['p1'].real_started_at
            observed.append((round(used, 2), round(sim.elapsed, 2)))
        self.assertEqual(observed[0][0], observed[1][0])
        self.assertNotEqual(observed[0][1], observed[1][1])

    def test_summary_separates_sim_and_real_durations(self):
        sim = self.sim
        sim.ensure_population(0)
        sim.add_human('p1', '甲', '#fff')
        sim.set_time_scale(2)
        sim.start()
        sim.advance(1.0)
        summary = sim.build_summary()
        self.assertAlmostEqual(summary['duration'], 2.0, places=2)
        self.assertAlmostEqual(summary['timebase']['real_duration'], 1.0, places=2)
        self.assertEqual(summary['timebase']['sim_seconds_per_real_second'], 2)

    def test_escape_rate_denominator_includes_all_participants(self):
        sim = self.sim
        sim.ensure_population(0)
        sim.start()
        for i in range(4):
            a = sim.add_human(f'p{i}', f'人{i}', '#fff')
            a.track = [[0.0, 16, 16, 0, 0, 0, 0, 0, 1, 0, 0, None, None, 0.0]]
        # 两人逃生,两人未逃;逃出率必须按 4 人算,而不是只按在场者。
        first = sim.agents['p0']
        first.escaped = True
        first.track.append([1.0, 96, 96, 0, 0, 0, 0, 0, 1, 0, 0, None, None, 1.0])
        summary = sim.build_summary()
        self.assertEqual(summary['n'], 4)
        self.assertEqual(summary['escaped'], 1)
        self.assertAlmostEqual(summary['escape_rate'], 0.25, places=3)

    def test_retired_participant_is_archived_not_dropped(self):
        sim = self.sim
        sim.ensure_population(0)
        sim.start()
        agent = sim.add_human('p1', '甲', '#fff')
        agent.track = [[0.0, 16, 16, 0, 0, 0, 0, 0, 1, 0, 0, None, None, 0.0]]
        sim.human_leave('p1')
        # 掉线者被重置清理,但其数据必须先落盘,不能凭空消失。
        sim.reset_crowd(keep_humans=True)
        self.assertNotIn('p1', sim.agents)
        saved = sorted(Path(self.tmp.name).glob('evac_*_tracks.csv'))
        self.assertTrue(saved)
        self.assertIn('p1', saved[-1].read_text(encoding='utf-8'))

    def test_archive_is_idempotent_per_trial(self):
        sim = self.sim
        sim.ensure_population(0)
        sim.start()
        agent = sim.add_human('p1', '甲', '#fff')
        agent.track = [[0.0, 16, 16, 0, 0, 0, 0, 0, 1, 0, 0, None, None, 0.0]]
        sim.archive_trial()
        before = len(list(Path(self.tmp.name).glob('evac_*_tracks.csv')))
        sim.archive_trial()
        after = len(list(Path(self.tmp.name).glob('evac_*_tracks.csv')))
        self.assertEqual(before, after)

    def test_failed_archive_preserves_trial_before_reset(self):
        sim = self.sim
        sim.ensure_population(0)
        sim.add_human('p1', '甲', '#fff')
        sim.start()
        sim.agents['p1'].track = [[0.0, 16, 16, 0, 0, 0, 0, 0, 1]]
        before_track = copy.deepcopy(sim.agents['p1'].track)
        before_trial = sim.trial_seq
        with patch.object(sim, 'save_outputs', side_effect=DataPersistenceError('disk full')):
            with self.assertRaises(DataPersistenceError):
                sim.reset_crowd(keep_humans=False)
        self.assertIn('p1', sim.agents)
        self.assertEqual(sim.agents['p1'].track, before_track)
        self.assertEqual(sim.trial_seq, before_trial)
        self.assertNotEqual(sim._archived_trial, before_trial)

    def test_failed_archive_is_not_marked_complete(self):
        sim = self.sim
        sim.ensure_population(0)
        sim.add_human('p1', '甲', '#fff')
        sim.start()
        sim.agents['p1'].track = [[0.0, 16, 16, 0, 0, 0, 0, 0, 1]]
        with patch.object(sim, 'save_outputs', side_effect=DataPersistenceError('read only')):
            sim._finish()
        self.assertNotEqual(sim._archived_trial, sim.trial_seq)
        self.assertTrue(sim.agents['p1'].track)

    def test_failed_archive_does_not_apply_population_change(self):
        sim = self.sim
        sim.ensure_population(0)
        sim.add_human('p1', '甲', '#fff')
        sim.start()
        sim.agents['p1'].track = [[0.0, 16, 16, 0, 0, 0, 0, 0, 1]]
        before_track = copy.deepcopy(sim.agents['p1'].track)
        with patch.object(sim, 'save_outputs', side_effect=DataPersistenceError('disk full')):
            with self.assertRaises(DataPersistenceError):
                sim.set_npc_count(3)
        self.assertEqual(sim.npc_target, 0)
        self.assertEqual(sim.agents['p1'].track, before_track)

    def test_archive_filenames_never_overwrite_existing_trial(self):
        sim = self.sim
        sim.ensure_population(0)
        agent = sim.add_human('p1', '甲', '#fff')
        sim.start()
        agent.track = [[0.0, 16, 16, 0, 0, 0, 0, 0, 1]]
        with patch('evac.datetime') as clock:
            clock.now.return_value.strftime.return_value = '20260929_120000'
            first = sim.archive_trial()
            sim._archived_trial = -1
            second = sim.archive_trial()
        self.assertNotEqual(first['json'], second['json'])
        self.assertTrue((Path(self.tmp.name) / first['json']).is_file())
        self.assertTrue((Path(self.tmp.name) / second['json']).is_file())

    def test_manual_snapshot_does_not_finalize_a_running_trial(self):
        sim = self.sim
        sim.ensure_population(0)
        agent = sim.add_human('p1', '甲', '#fff')
        sim.start()
        agent.track = [[0.0, 16, 16, 0, 0, 0, 0, 0, 1]]
        first = sim.archive_trial(reason='manual', finalize=False)
        self.assertIsNone(sim.summary)
        agent.track.append([0.2, 18, 16, 0, 0, 0, 0, 0, 1])
        second = sim.archive_trial(reason='reset')
        self.assertNotEqual(first['csv'], second['csv'])
        with (Path(self.tmp.name) / second['csv']).open(encoding='utf-8', newline='') as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 2)


class ExperimentMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        maze = SimpleNamespace(mw=4, mh=4, cell=32, world_w=128, world_h=128,
                               walls=[[0] * 4 for _ in range(4)], exit=(3, 3),
                               exits=[(3, 3)], complexity=1)
        self.sim = EvacSim(maze, Path(self.tmp.name))

    def test_experiment_metadata_is_exported(self):
        sim = self.sim
        sim.experiment_id = 'EXP-01'
        sim.condition = 'low_visibility'
        sim.group = 'B'
        sim.operator = 'rui'
        sim.ensure_population(0)
        sim.start()
        summary = sim.build_summary()
        self.assertEqual(summary['schema_version'], 1)
        exp = summary['experiment']
        self.assertEqual(exp['experiment_id'], 'EXP-01')
        self.assertEqual(exp['condition'], 'low_visibility')
        self.assertEqual(exp['group'], 'B')
        self.assertEqual(exp['trial_seq'], 1)
        self.assertTrue(exp['t0_iso'])
        self.assertEqual(exp['t0_iso'][4], '-')

    def test_consent_and_participant_recorded_per_human(self):
        sim = self.sim
        sim.ensure_population(0)
        sim.add_human('p1', '甲', '#fff', participant_id='S001', consent=True)
        sim.add_human('p2', '乙', '#fff', participant_id='S002', consent=False)
        sim.start()
        summary = sim.build_summary()
        by_pid = {a['pid']: a for a in summary['agents']}
        self.assertEqual(by_pid['p1']['participant_id'], 'S001')
        self.assertIs(by_pid['p1']['consent'], True)
        self.assertIs(by_pid['p2']['consent'], False)
        for a in sim.agents.values():
            a.track = [[0.0, 16, 16, 0, 0, 0, 0, 0, 1, 0, 0, None, None, 0.0]]
        rows = list(csv.DictReader(io.StringIO(sim.export_csv())))
        consent = {r['pid']: r['consent'] for r in rows}
        self.assertEqual(consent['p1'], '1')
        self.assertEqual(consent['p2'], '0')

    def test_reconnect_can_fill_in_participant_metadata(self):
        sim = self.sim
        sim.ensure_population(0)
        sim.add_human('p1', '甲', '#fff')
        self.assertIsNone(sim.agents['p1'].consent)
        sim.add_human('p1', '甲', '#fff', participant_id='S009', consent=True)
        self.assertEqual(sim.agents['p1'].participant_id, 'S009')
        self.assertIs(sim.agents['p1'].consent, True)

    def test_reconnect_does_not_erase_existing_metadata(self):
        sim = self.sim
        sim.ensure_population(0)
        sim.add_human('p1', '甲', '#fff', participant_id='S001', consent=True)
        sim.add_human('p1', '甲', '#fff')        # 无参重连
        self.assertEqual(sim.agents['p1'].participant_id, 'S001')
        self.assertIs(sim.agents['p1'].consent, True)

    def test_missing_consent_stays_empty_not_false(self):
        # 未收集 ≠ 拒绝:必须区分 None 与 False。
        sim = self.sim
        sim.ensure_population(0)
        sim.add_human('p1', '甲', '#fff')
        sim.start()
        sim.agents['p1'].track = [[0.0, 16, 16, 0, 0, 0, 0, 0, 1, 0, 0, None, None, 0.0]]
        self.assertIsNone(sim.agents['p1'].consent)
        row = next(csv.DictReader(io.StringIO(sim.export_csv())))
        self.assertEqual(row['consent'], '')
        self.assertIsNone(sim.build_summary()['agents'][0]['consent'])


class FakeSocket:
    def __init__(self, messages=(), delay=0):
        self.messages = iter(messages)
        self.sent = []
        self.delay = delay
        self.closed = None

    async def accept(self):
        pass

    async def send_json(self, data):
        self.sent.append(data)

    async def send_text(self, data):
        await asyncio.sleep(self.delay)
        self.sent.append(json.loads(data))

    async def receive_text(self):
        try:
            return next(self.messages)
        except StopIteration:
            raise main.WebSocketDisconnect()

    async def close(self, code=1000, reason=''):
        self.closed = code


class SpawnControlTests(unittest.TestCase):
    """起始位置控制：初始到出口距离是逃生时间的关键协变量，必须可控可记录。"""

    def _sim(self, **kw):
        params = dict(mw=16, mh=16, cell=32, world_w=512, world_h=512,
                      walls=[[0] * 16 for _ in range(16)], exit=(0, 0),
                      exits=[(0, 0)], complexity=1, seed=11, view_level=3)
        params.update(kw)
        maze = SimpleNamespace(**params)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return EvacSim(maze, Path(tmp.name))

    def test_spawn_zone_confines_positions(self):
        sim = self._sim()
        sim.set_spawn_zone((10, 10, 15, 15))
        sim.set_npc_count(40)
        cell = 32
        for a in sim.agents.values():
            if a.is_human:
                continue
            gx = int(a.x // cell)
            gy = int(a.y // cell)
            self.assertTrue(10 <= gx <= 15, f"gx={gx} 越界")
            self.assertTrue(10 <= gy <= 15, f"gy={gy} 越界")

    def test_spawn_zone_tightens_exit_distance_distribution(self):
        wide = self._sim()
        wide.set_npc_count(60)
        wide_ds = [a.initial_exit_distance for a in wide.agents.values()
                   if not a.is_human and a.initial_exit_distance is not None]
        spread_wide = max(wide_ds) - min(wide_ds)

        zoned = self._sim()
        zoned.set_spawn_zone((12, 12, 15, 15))
        zoned.set_npc_count(60)
        zoned_ds = [a.initial_exit_distance for a in zoned.agents.values()
                    if not a.is_human and a.initial_exit_distance is not None]
        spread_zoned = max(zoned_ds) - min(zoned_ds)
        self.assertLess(spread_zoned, spread_wide)

    def test_initial_exit_distance_recorded_per_agent(self):
        sim = self._sim()
        sim.set_npc_count(10)
        for a in sim.agents.values():
            if not a.is_human:
                self.assertIsNotNone(a.initial_exit_distance)
                self.assertGreaterEqual(a.initial_exit_distance, 0)

    def test_initial_exit_distance_in_csv_and_summary(self):
        sim = self._sim()
        sim.set_spawn_zone((8, 8, 11, 11))
        sim.set_npc_count(6)
        sim.start()
        sim.advance(0.05)
        rows = list(csv.DictReader(io.StringIO(sim.export_csv())))
        self.assertTrue(rows)
        self.assertTrue(all(r['initial_exit_distance'] for r in rows))
        summary = sim.build_summary()
        self.assertEqual(summary['spawn']['zone'], [8, 8, 11, 11])
        self.assertIsNotNone(summary['spawn']['initial_exit_distance_cells'])
        self.assertEqual(
            summary['spawn']['initial_exit_distance_cells']['n'],
            len({r['pid'] for r in rows}),
        )

    def test_spawn_zone_none_restores_full_map(self):
        sim = self._sim()
        sim.set_spawn_zone((5, 5, 6, 6))
        self.assertEqual(sim.spawn_zone, (5, 5, 6, 6))
        sim.set_spawn_zone(None)
        self.assertIsNone(sim.spawn_zone)
        sim.set_npc_count(50)
        gxs = {int(a.x // 32) for a in sim.agents.values() if not a.is_human}
        self.assertGreater(max(gxs) - min(gxs), 3)

    def test_invalid_spawn_zone_rejected(self):
        sim = self._sim()
        with self.assertRaises(ValueError):
            sim.set_spawn_zone((1, 2, 3))
        with self.assertRaises(ValueError):
            sim.set_spawn_zone("nope")
        for zone in ((-1, 0, 2, 2), (0, 0, sim.maze.mw, 2),
                     (sim.maze.mw, 0, sim.maze.mw + 1, 2)):
            with self.subTest(zone=zone), self.assertRaises(ValueError):
                sim.set_spawn_zone(zone)
        with self.assertRaises(ValueError):
            sim.set_spawn_zone((0.5, 0, 2, 2))
        self.assertIsNone(sim.spawn_zone)

    def test_spawn_zone_round_trips_through_scenario(self):
        sim = self._sim()
        sim.set_spawn_zone((3, 4, 9, 9))
        data = sim.export_scenario()
        self.assertEqual(data['spawn_zone'], [3, 4, 9, 9])
        other = self._sim()
        other.apply_scenario_state(data)
        self.assertEqual(other.spawn_zone, (3, 4, 9, 9))


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sim = EvacSim(main.maze, Path(self.tmp.name))
        # 既有网络测试走演示模式;参与者校验由专门用例覆盖。
        self.sim.require_participant_consent = False
        self.sim.ensure_population()
        self.hub = main.Hub()
        self.patches = [patch.object(main, 'sim', self.sim), patch.object(main, 'hub', self.hub)]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        tasks = list(self.hub.senders.values())
        for ws in list(self.hub.outboxes):
            self.hub.remove(ws)
        await asyncio.gather(*tasks, return_exceptions=True)
        for p in self.patches:
            p.stop()

    async def test_idle_socket_is_not_closed_just_for_waiting(self):
        ws = FakeSocket()
        self.hub.hosts.add(ws)
        self.hub.register(ws)
        await asyncio.sleep(1.1)
        self.assertIsNone(ws.closed)

    async def test_slow_socket_does_not_block_fast_socket(self):
        slow, fast = FakeSocket(delay=.3), FakeSocket()
        for ws in (slow, fast):
            self.hub.hosts.add(ws)
            self.hub.register(ws)
        await asyncio.wait_for(self.hub.broadcast({'type': 'state'}), .1)
        await asyncio.sleep(.02)
        self.assertEqual(len(fast.sent), 1)
        self.assertEqual(slow.sent, [])

    async def test_old_connection_cleanup_keeps_new_connection(self):
        old, new = FakeSocket(), FakeSocket()
        self.sim.add_human('ptest', '玩家', '#fff')
        self.hub.players['ptest'] = new
        self.hub.remove(old)
        self.assertIs(self.hub.players['ptest'], new)
        self.assertTrue(self.sim.agents['ptest'].online)

    async def test_invalid_messages_do_not_disconnect_before_rename(self):
        ws = FakeSocket(['[]', 'null', '{', '{"type":"input","x":null}',
                         '{"type":"input","x":"NaN"}', '{"type":"rename","name":"成功"}'])
        observed = []
        leave = self.sim.human_leave
        def record_leave(pid):
            observed.append(self.sim.agents[pid].name)
            leave(pid)
        with patch.object(self.sim, 'human_leave', side_effect=record_leave):
            await main.ws_player(ws, 'ptest', '原名')
        self.assertEqual(observed, ['成功'])
        self.assertTrue(ws.sent)
        self.assertNotIn('ptest', self.hub.players)

    async def test_duplicate_participant_reuses_single_identity(self):
        self.sim.require_participant_consent = True
        first = FakeSocket()
        await self.sim_wrap(first, 'pAAAA1111', '甲', participant='S001', consent=True)
        self.assertTrue(self.sim.agent_by_participant('S001'))
        # 同一参与者换设备再来,必须复用原 pid,不能新增第二条样本。
        second = FakeSocket()
        await self.sim_wrap(second, 'pBBBB2222', '甲', participant='S001', consent=True)
        humans = [a for a in self.sim.agents.values() if a.is_human]
        self.assertEqual(len(humans), 1)
        self.assertEqual(humans[0].pid, 'pAAAA1111')

    async def test_missing_consent_is_rejected(self):
        self.sim.require_participant_consent = True
        ws = FakeSocket()
        await self.sim_wrap(ws, 'pAAAA1111', '甲', participant='S001', consent=None)
        self.assertEqual(ws.closed, 4005)
        self.assertFalse(any(a.is_human for a in self.sim.agents.values()))

    async def test_missing_participant_is_rejected(self):
        self.sim.require_participant_consent = True
        ws = FakeSocket()
        await self.sim_wrap(ws, 'pAAAA1111', '甲', participant='', consent=True)
        self.assertEqual(ws.closed, 4004)
        self.assertFalse(any(a.is_human for a in self.sim.agents.values()))

    async def test_declined_consent_is_rejected(self):
        self.sim.require_participant_consent = True
        ws = FakeSocket()
        await self.sim_wrap(ws, 'pAAAA1111', '甲', participant='S001', consent=False)
        self.assertEqual(ws.closed, 4005)

    async def test_new_participant_cannot_join_finished_trial(self):
        self.sim.require_participant_consent = True
        self.sim.phase = 'ended'
        ws = FakeSocket()
        await self.sim_wrap(ws, 'pAAAA1111', '甲', participant='S001', consent=True)
        self.assertEqual(ws.closed, 4006)
        self.assertFalse(any(a.is_human for a in self.sim.agents.values()))

    async def test_demo_mode_allows_anonymous_join(self):
        self.sim.require_participant_consent = False
        ws = FakeSocket()
        await self.sim_wrap(ws, 'pAAAA1111', '甲')
        self.assertTrue(self.sim.agents['pAAAA1111'].is_human)

    async def test_participant_identifier_is_trimmed_before_identity_matching(self):
        self.sim.require_participant_consent = True
        first = FakeSocket()
        await self.sim_wrap(first, 'pAAAA1111', '甲', participant='  S001  ', consent=True)
        second = FakeSocket()
        await self.sim_wrap(second, 'pBBBB2222', '甲', participant='S001', consent=True)
        humans = [a for a in self.sim.agents.values() if a.is_human]
        self.assertEqual(len(humans), 1)
        self.assertEqual(humans[0].participant_id, 'S001')

    async def test_player_name_is_limited_for_public_state(self):
        ws = FakeSocket()
        name = '这是一个特别特别长的玩家名称需要截断'
        await self.sim_wrap(ws, 'pAAAA1111', name)
        self.assertEqual(self.sim.agents['pAAAA1111'].name, name[:main.MAX_PLAYER_NAME_LENGTH])

    async def sim_wrap(self, ws, pid, name, participant='', consent=None):
        """调用 ws_player 并在第一条消息后结束,模拟玩家接入。"""
        original = self.sim.human_leave
        self.sim.human_leave = lambda _pid: None
        try:
            await main.ws_player(ws, pid, name, participant=participant, consent=consent)
        finally:
            self.sim.human_leave = original

    async def test_reset_people_preserves_fire(self):
        self.sim.ignite(0, 0)
        await main.api_reset(pos=1, fire=0)
        self.assertIn((0, 0), self.sim.burning)
        await main.api_reset(pos=1, fire=1)
        self.assertFalse(self.sim.burning)


if __name__ == '__main__':
    unittest.main()


class MainRobustnessTests(unittest.TestCase):
    class Headers(dict):
        def get(self, key, default=None):
            key = key.lower()
            return next((value for name, value in self.items() if name.lower() == key), default)

    def _admin_request(self, *, method="GET", path="/api/tracks", origin=None, cookie=None,
                       header_token=None):
        headers = {"Host": "maze.test"}
        if origin is not None:
            headers["Origin"] = origin
        if header_token is not None:
            headers["X-Maze-Admin-Token"] = header_token
        return SimpleNamespace(
            headers=self.Headers(headers),
            cookies={main.ADMIN_SESSION_COOKIE: cookie} if cookie else {},
            method=method,
            url=SimpleNamespace(scheme="http", netloc="maze.test", path=path),
        )

    def test_admin_header_token_and_session_authentication(self):
        async def check():
            with patch.object(main, "ADMIN_TOKEN", "secret-test-token"), \
                 patch.dict(main.ADMIN_SESSIONS, {"valid-session": time.monotonic() + 60}, clear=True):
                await main.require_admin(self._admin_request(header_token="secret-test-token"))
                self.assertFalse(main.admin_token_matches("🔒"))
                await main.require_admin(self._admin_request(cookie="valid-session"))
                with self.assertRaises(main.HTTPException) as raised:
                    await main.require_admin(self._admin_request())
                self.assertEqual(raised.exception.status_code, 401)
        asyncio.run(check())

    def test_cookie_authenticated_mutations_require_same_origin(self):
        async def check():
            with patch.object(main, "ADMIN_TOKEN", "secret-test-token"), \
                 patch.dict(main.ADMIN_SESSIONS, {"valid-session": time.monotonic() + 60}, clear=True):
                await main.require_admin(self._admin_request(
                    method="POST", cookie="valid-session", origin="http://maze.test"))
                with self.assertRaises(main.HTTPException) as raised:
                    await main.require_admin(self._admin_request(
                        method="POST", cookie="valid-session", origin="https://attacker.test"))
                self.assertEqual(raised.exception.status_code, 403)
        asyncio.run(check())

    def test_trusted_proxy_headers_are_used_only_when_enabled(self):
        request = self._admin_request(method="POST", origin="https://public.example")
        request.url = SimpleNamespace(scheme="http", netloc="internal:8000", path="/api/tracks")
        request.headers["X-Forwarded-Proto"] = "https"
        request.headers["X-Forwarded-Host"] = "public.example"

        with patch.object(main, "TRUST_PROXY_HEADERS", False):
            with self.assertRaises(main.HTTPException) as raised:
                main.require_same_origin(request)
            self.assertEqual(raised.exception.status_code, 403)

        with patch.object(main, "TRUST_PROXY_HEADERS", True):
            main.require_same_origin(request)

    def test_trusted_proxy_headers_work_for_admin_websocket(self):
        with patch.object(main, "ADMIN_TOKEN", "secret-test-token"), \
             patch.object(main, "TRUST_PROXY_HEADERS", True), \
             patch.dict(main.ADMIN_SESSIONS, {"valid-session": time.monotonic() + 60}, clear=True):
            ws = SimpleNamespace(
                cookies={main.ADMIN_SESSION_COOKIE: "valid-session"},
                headers={
                    "origin": "https://public.example",
                    "host": "internal:8000",
                    "x-forwarded-proto": "https",
                    "x-forwarded-host": "public.example",
                },
                url=SimpleNamespace(scheme="ws", netloc="internal:8000"),
            )
            self.assertTrue(main.websocket_admin_token(ws))

    def test_admin_sessions_expire_and_are_removed(self):
        with patch.dict(main.ADMIN_SESSIONS, {"expired": 0.0}, clear=True):
            self.assertFalse(main.valid_admin_session("expired"))
            self.assertNotIn("expired", main.ADMIN_SESSIONS)

    def test_json_body_reader_rejects_declared_and_actual_oversize_payloads(self):
        class StreamingRequest:
            def __init__(self, body, content_length=None):
                headers = {}
                if content_length is not None:
                    headers["Content-Length"] = str(content_length)
                self.headers = MainRobustnessTests.Headers(headers)
                self.body = body

            async def stream(self):
                yield self.body

        async def check():
            declared = StreamingRequest(b"{}", main.MAX_EXPERIMENT_JSON_BYTES + 1)
            with self.assertRaises(main.HTTPException) as raised:
                await main.read_json_body(
                    declared, main.MAX_EXPERIMENT_JSON_BYTES, "实验元数据"
                )
            self.assertEqual(raised.exception.status_code, 413)

            actual = StreamingRequest(b"x" * (main.MAX_EXPERIMENT_JSON_BYTES + 1))
            with self.assertRaises(main.HTTPException) as raised:
                await main.read_json_body(
                    actual, main.MAX_EXPERIMENT_JSON_BYTES, "实验元数据"
                )
            self.assertEqual(raised.exception.status_code, 413)

            valid = StreamingRequest(b'{"condition":"baseline"}')
            self.assertEqual(
                await main.read_json_body(valid, main.MAX_EXPERIMENT_JSON_BYTES, "实验元数据"),
                {"condition": "baseline"},
            )

        asyncio.run(check())

    def test_readiness_checks_actual_data_volume(self):
        # Source checkouts omit the large GLB assets; model presence is tested
        # separately, while this test isolates the readiness data volume.
        with patch.object(main, "SCENE_DIR", Path("missing-scene-assets")):
            payload = asyncio.run(main.readyz())
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["checks"]["maze"])
        self.assertTrue(payload["checks"]["simulation"])
        self.assertTrue(payload["checks"]["data_dir"])

        with patch.object(main, "data_dir_is_writable", return_value=False):
            response = asyncio.run(main.readyz())
        self.assertEqual(response.status_code, 503)
        body = json.loads(response.body.decode("utf-8"))
        self.assertEqual(body["status"], "not_ready")
        self.assertFalse(body["checks"]["data_dir"])

    def test_persistence_error_returns_storage_status_without_exposing_path(self):
        request = SimpleNamespace()
        response = asyncio.run(main.data_persistence_error_handler(
            request, DataPersistenceError("/private/research/data is read-only")))
        self.assertEqual(response.status_code, 507)
        payload = json.loads(response.body.decode("utf-8"))
        self.assertIn("无法写入磁盘", payload["detail"])
        self.assertNotIn("/private/research/data", response.body.decode("utf-8"))

    def test_experiment_metadata_update_is_atomic(self):
        class JsonRequest:
            async def json(self):
                return {"experiment_id": "SHOULD-NOT-APPLY", "operator": []}

        before = main.sim.experiment_id
        with self.assertRaises(main.HTTPException):
            asyncio.run(main.api_experiment_set(JsonRequest(), None))
        self.assertEqual(main.sim.experiment_id, before)

    def test_experiment_metadata_cannot_change_mid_trial(self):
        class JsonRequest:
            async def json(self):
                return {"condition": "new-condition"}

        sim = EvacSim(main.maze, Path(tempfile.mkdtemp()))
        sim.phase = "running"
        with patch.object(main, "sim", sim):
            with self.assertRaises(main.HTTPException) as raised:
                asyncio.run(main.api_experiment_set(JsonRequest(), None))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(sim.condition, "")

    def test_admin_login_attempts_are_rate_limited_per_client(self):
        with patch.dict(main.ADMIN_LOGIN_ATTEMPTS, {}, clear=True), \
             patch.object(main.time, "monotonic", return_value=100.0):
            for _ in range(main.ADMIN_LOGIN_LIMIT):
                self.assertTrue(main.allow_admin_login_attempt("127.0.0.1"))
            self.assertFalse(main.allow_admin_login_attempt("127.0.0.1"))
        with patch.dict(main.ADMIN_LOGIN_ATTEMPTS, {}, clear=True), \
             patch.object(main.time, "monotonic", return_value=500.0):
            self.assertTrue(main.allow_admin_login_attempt("127.0.0.1"))

    def test_websocket_admin_requires_valid_cookie_and_same_origin(self):
        with patch.object(main, "ADMIN_TOKEN", "secret-test-token"), \
             patch.dict(main.ADMIN_SESSIONS, {"valid-session": time.monotonic() + 60}, clear=True):
            ws = SimpleNamespace(
                cookies={main.ADMIN_SESSION_COOKIE: "valid-session"},
                headers={"origin": "http://maze.test", "host": "maze.test"},
                url=SimpleNamespace(scheme="ws", netloc="maze.test"),
            )
            self.assertTrue(main.websocket_admin_token(ws))
            ws.headers["origin"] = "http://attacker.test"
            self.assertFalse(main.websocket_admin_token(ws))
            ws.headers["origin"] = "http://maze.test"
            ws.cookies[main.ADMIN_SESSION_COOKIE] = "expired"
            self.assertFalse(main.websocket_admin_token(ws))

    def test_manual_end_archives_running_trial_without_resetting_agents(self):
        sim = EvacSim(main.maze, Path(tempfile.mkdtemp()))
        sim.require_participant_consent = False
        sim.ensure_population(1)
        sim.start()
        sim.advance(0.2)
        before = {pid: (a.x, a.y) for pid, a in sim.agents.items()}
        with patch.object(main, "sim", sim):
            result = asyncio.run(main.api_evac_end(None))
        self.assertTrue(result["changed"])
        self.assertEqual(sim.phase, "ended")
        self.assertEqual(before, {pid: (a.x, a.y) for pid, a in sim.agents.items()})
        self.assertIsNotNone(result["saved"])

    def test_manual_end_storage_failure_returns_without_claiming_archive(self):
        sim = EvacSim(main.maze, Path(tempfile.mkdtemp()))
        sim.ensure_population(1)
        sim.start()
        sim.advance(0.2)
        with patch.object(main, "sim", sim), \
             patch.object(sim, "archive_trial", side_effect=DataPersistenceError("read only")):
            with self.assertRaises(DataPersistenceError):
                asyncio.run(main.api_evac_end(None))
        self.assertEqual(sim.phase, "ended")
        self.assertNotEqual(sim._archived_trial, sim.trial_seq)

    def test_manual_end_is_idempotent_when_idle(self):
        sim = EvacSim(main.maze, Path(tempfile.mkdtemp()))
        with patch.object(main, "sim", sim):
            result = asyncio.run(main.api_evac_end(None))
        self.assertFalse(result["changed"])
        self.assertIsNone(result.get("saved"))

    def test_manual_end_retries_archive_after_storage_failure(self):
        sim = EvacSim(main.maze, Path(tempfile.mkdtemp()))
        sim.ensure_population(1)
        sim.start()
        sim.advance(0.2)
        with patch.object(sim, "archive_trial", side_effect=[DataPersistenceError("read only"), {"json": "ok.json", "csv": "ok.csv"}]) as archive:
            with self.assertRaises(DataPersistenceError):
                sim.end()
            self.assertEqual(sim.phase, "ended")
            self.assertEqual(sim.end(), {"json": "ok.json", "csv": "ok.csv"})
            self.assertEqual(archive.call_count, 2)

    def test_invalid_scenario_does_not_archive_or_mutate_live_state(self):
        before = {
            "gen": main.maze.gen,
            "mw": main.maze.mw,
            "mh": main.maze.mh,
            "trial_seq": main.sim.trial_seq,
            "walls": copy.deepcopy(main.maze.walls),
        }
        bad = {
            "mw": 4,
            "mh": 4,
            "cell": 32,
            "walls": [[0, 0, 0, 0]],  # row count mismatch
        }
        with self.assertRaises(main.HTTPException):
            main.apply_loaded_scenario(bad)
        self.assertEqual(main.maze.gen, before["gen"])
        self.assertEqual((main.maze.mw, main.maze.mh), (before["mw"], before["mh"]))
        self.assertEqual(main.sim.trial_seq, before["trial_seq"])
        self.assertEqual(main.maze.walls, before["walls"])

    def test_scenario_apply_failure_rolls_back_map_and_simulation(self):
        before_maze = copy.deepcopy(main.maze.__dict__)
        before_rng_state = main.sim.rng.getstate()
        before_sim = {
            key: copy.deepcopy(value)
            for key, value in main.sim.__dict__.items()
            if key not in {"maze", "rng"}
        }
        scenario = {
            "mw": main.maze.mw,
            "mh": main.maze.mh,
            "cell": main.maze.cell,
            "walls": copy.deepcopy(main.maze.walls),
            "exits": [list(main.maze.exit)],
        }
        with patch.object(main.sim, "archive_trial"), \
             patch.object(main.sim, "apply_scenario_state", side_effect=ValueError("应用失败")):
            with self.assertRaises(main.HTTPException) as raised:
                main.apply_loaded_scenario(scenario)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(main.maze.__dict__, before_maze)
        self.assertEqual(
            {key: value for key, value in main.sim.__dict__.items()
             if key not in {"maze", "rng"}},
            before_sim,
        )
        self.assertEqual(main.sim.rng.getstate(), before_rng_state)
        self.assertIs(main.sim.maze, main.maze)

    def test_invalid_scenario_controls_are_rejected_before_archive(self):
        before_trial = main.sim.trial_seq
        bad = {
            "mw": main.maze.mw,
            "mh": main.maze.mh,
            "cell": main.maze.cell,
            "walls": copy.deepcopy(main.maze.walls),
            "exits": [list(main.maze.exit)],
            "npc_target": 12.5,
        }
        with self.assertRaises(main.HTTPException):
            main.apply_loaded_scenario(bad)
        self.assertEqual(main.sim.trial_seq, before_trial)

    def test_unknown_scenario_version_is_rejected_before_archive_or_map_mutation(self):
        before = (main.maze.gen, main.maze.mw, main.maze.mh,
                  copy.deepcopy(main.maze.walls))
        bad = {
            "version": 2,
            "mw": 4,
            "mh": 4,
            "cell": 32,
            "walls": [[15, 15, 15, 15] for _ in range(4)],
        }
        with patch.object(main.sim, "archive_trial") as archive:
            with self.assertRaises(main.HTTPException) as raised:
                main.apply_loaded_scenario(bad)
        self.assertEqual(raised.exception.status_code, 400)
        archive.assert_not_called()
        self.assertEqual(
            (main.maze.gen, main.maze.mw, main.maze.mh, main.maze.walls), before
        )

    def test_invalid_scenario_heating_is_rejected_before_archive(self):
        before = (main.maze.gen, main.maze.mw, main.maze.mh, main.sim.trial_seq,
                  copy.deepcopy(main.maze.walls))
        bad = {
            "mw": main.maze.mw,
            "mh": main.maze.mh,
            "cell": main.maze.cell,
            "walls": copy.deepcopy(main.maze.walls),
            "heating": [[main.maze.mw, 0, .5]],
        }
        with self.assertRaises(main.HTTPException):
            main.apply_loaded_scenario(bad)
        after = (main.maze.gen, main.maze.mw, main.maze.mh, main.sim.trial_seq,
                 main.maze.walls)
        self.assertEqual(after, before)

    def test_invalid_scenario_fractional_fire_is_rejected_before_archive(self):
        before = (main.maze.gen, main.maze.mw, main.maze.mh, main.sim.trial_seq,
                  copy.deepcopy(main.maze.walls))
        bad = {
            "mw": main.maze.mw,
            "mh": main.maze.mh,
            "cell": main.maze.cell,
            "walls": copy.deepcopy(main.maze.walls),
            "fire": [[1.5, 0]],
        }
        with self.assertRaises(main.HTTPException):
            main.apply_loaded_scenario(bad)
        after = (main.maze.gen, main.maze.mw, main.maze.mh, main.sim.trial_seq,
                 main.maze.walls)
        self.assertEqual(after, before)

    def test_invalid_scenario_wall_topology_is_rejected_before_archive(self):
        walls = copy.deepcopy(main.maze.walls)
        walls[0][0] ^= 2
        before = (main.maze.gen, main.maze.mw, main.maze.mh, main.sim.trial_seq,
                  copy.deepcopy(main.maze.walls))
        bad = {"mw": main.maze.mw, "mh": main.maze.mh, "cell": main.maze.cell,
               "walls": walls}
        with self.assertRaises(main.HTTPException):
            main.apply_loaded_scenario(bad)
        after = (main.maze.gen, main.maze.mw, main.maze.mh, main.sim.trial_seq,
                 main.maze.walls)
        self.assertEqual(after, before)

    def test_audit_event_redacts_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = SimpleNamespace(
                client=SimpleNamespace(host="127.0.0.1"),
                method="POST",
                url=SimpleNamespace(path="/api/experiment"),
                state=SimpleNamespace(request_id="req-123"),
            )
            path = Path(tmp) / "audit.jsonl"
            with patch.object(main, "AUDIT_LOG_PATH", path):
                main.audit_event(
                    "http_request", request, status=200,
                    token="do-not-write", participant_id="S001", safe="ok",
                )
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["request_id"], "req-123")
            self.assertEqual(record["status"], 200)
            self.assertEqual(record["safe"], "ok")
            self.assertNotIn("token", record)
            self.assertNotIn("participant_id", record)
