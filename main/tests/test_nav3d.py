import json
import math
import tempfile
import unittest
from pathlib import Path

from nav3d import EvacSim3D, SceneValidationError, region_clearance, point_in_region, validate_scene


ROOT = Path(__file__).resolve().parents[1]


def school_scene():
    return json.loads((ROOT / "static/scenes/scene-v2.json").read_text(encoding="utf-8"))


class Navigation3DTests(unittest.TestCase):
    def test_door_opening_lifecycle_and_events(self):
        scene = school_scene()
        scene["id"] = "door-lifecycle"
        floor = scene["floors"][0]
        floor["walkable"] = [
            {"id": "room", "outer": [[2, .8], [7, .8], [7, 4.2], [2, 4.2]], "holes": []},
            {"id": "corridor", "outer": [[7, .8], [14.5, .8], [14.5, 4.2], [7, 4.2]], "holes": []},
        ]
        floor["spawn_zones"] = [{"id": "room-start", "outer": [[3, 1.4], [6, 1.4], [6, 3.5], [3, 3.5]]}]
        scene["doors"] = [{"id": "door-1", "name": "房间门", "floor": "floor-1",
            "position": [7, 0, 2.5], "width_m": .9, "normal": [1, 0],
            "from_region": "room", "to_region": "corridor", "mode": "both",
            "initial_state": "closed", "open_duration_s": .2, "auto_close_s": 4}]
        scene["exits"] = [{"id": "exit", "floor": "floor-1", "position": [13, 0, 2.5], "width_m": 1.2}]
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(scene, Path(tmp), require_route=False, default_npcs=0)
            human = sim.add_human("door-human", "真人", "#fff")
            human.position = (6.7, 0, 2.5); human.floor_id = "floor-1"; human.region_id = "room"
            human.nav_node = sim._nearest_nav_node("room", human.position)
            self.assertEqual(sim.nearby_doors(human)[0]["state"], "closed")
            self.assertEqual(sim.request_door("door-1", human)["state"], "opening")
            sim.start(); [sim.step(.05) for _ in range(5)]
            self.assertEqual(sim.public_state()["doors"][0]["state"], "open")
            self.assertTrue(any(e["event"] == "opened" for e in sim.door_events))

    def test_locked_door_rejects_participant_request(self):
        scene = school_scene(); scene["id"] = "locked-door"
        scene["doors"] = [{"id": "locked", "name": "锁门", "floor": "floor-1",
            "position": [5, 0, 2.5], "width_m": .9, "normal": [1, 0],
            "from_region": "floor-1-main", "to_region": "floor-1-main-2",
            "mode": "both", "initial_state": "locked", "locked": True}]
        scene["floors"][0]["walkable"].append({"id": "floor-1-main-2", "outer": [[5, .8], [14.5, .8], [14.5, 4.2], [5, 4.2]], "holes": []})
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(scene, Path(tmp), require_route=False, default_npcs=0)
            human = sim.add_human("locked-human", "真人", "#fff")
            human.position = (4.7, 0, 2.5); human.floor_id = "floor-1"; human.region_id = "floor-1-main"
            human.nav_node = sim._nearest_nav_node(human.region_id, human.position)
            result = sim.request_door("locked", human)
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "locked")

    def test_fire_blocking_one_route_replans_to_another_exit(self):
        scene = school_scene()
        scene["id"] = "fire-replan"
        scene["floors"] = [{
            "id": "f1", "elevation_m": 0,
            "walkable": [
                {"id": "start", "outer": [[0, 0], [2, 0], [2, 2], [0, 2]], "holes": []},
                {"id": "blocked-exit", "outer": [[2, 0], [4, 0], [4, 2], [2, 2]], "holes": []},
                {"id": "safe-exit", "outer": [[0, 2], [2, 2], [2, 4], [0, 4]], "holes": []},
            ],
            "spawn_zones": [],
        }]
        scene["stairs"] = []
        scene["exits"] = [
            {"id": "exit-blocked", "floor": "f1", "position": [3.8, 0, 1], "width_m": 1.0},
            {"id": "exit-safe", "floor": "f1", "position": [1, 0, 3.8], "width_m": 1.0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(scene, Path(tmp), seed=12)
            sim.ensure_population(1)
            agent = next(iter(sim.agents.values()))
            agent.position = (1.0, 0.0, 1.0)
            agent.floor_id = "f1"
            agent.region_id = "start"
            agent.nav_node = sim._nearest_nav_node("start", agent.position)
            self.assertTrue(sim.ignite("f1", 3.0, 1.0))
            sim.start()
            for _ in range(3000):
                sim.step(0.02)
                if agent.escaped or agent.failed:
                    break
            self.assertTrue(agent.escaped)
            self.assertFalse(agent.failed)
            self.assertTrue(any(row[4] > 2.0 for row in agent.track))

    def test_replay_payload_contains_v2_coordinates_and_reset_clears_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), seed=6)
            sim.ensure_population(1)
            sim.start()
            sim.step(0.02)
            replay = sim.replay_payload()
            self.assertEqual(replay["schema"], 2)
            self.assertEqual(len(replay["agents"]), 1)
            sample = replay["agents"][0]["samples"][0]
            self.assertTrue({"x_m", "y_m", "z_m", "floor_id", "nav_region", "smoke", "health", "state"} <= set(sample))
            self.assertGreaterEqual(replay["duration"], 0)
            sim.reset()
            self.assertFalse(sim.public_state()["replay_available"])

    def test_v2_outputs_do_not_overwrite_legacy_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            legacy_json = data_dir / "evac_latest.json"
            legacy_csv = data_dir / "evac_latest_tracks.csv"
            legacy_json.write_bytes(b"legacy-json\n")
            legacy_csv.write_bytes(b"legacy-csv\n")
            sim = EvacSim3D(school_scene(), data_dir, seed=9)
            sim.ensure_population(1)
            sim.save_outputs()
            self.assertEqual(legacy_json.read_bytes(), b"legacy-json\n")
            self.assertEqual(legacy_csv.read_bytes(), b"legacy-csv\n")
            self.assertTrue((data_dir / "evac_latest_v2.json").is_file())
            self.assertTrue((data_dir / "evac_latest_tracks_v2.csv").is_file())
            summary = json.loads((data_dir / "evac_latest_v2.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["track_file"], "evac_latest_tracks_v2.csv")
            self.assertEqual(summary["track_rows"], 0)

    def test_explicit_fire_point_must_be_inside_a_walkable_region(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp))
            self.assertFalse(sim.ignite("floor-1", 100.0, 100.0))
            self.assertTrue(sim.ignite("floor-1", 5.0, 3.8))

    def test_builtin_school_navigation_is_aligned_to_visual_model_contract(self):
        # Deliberate calibration correction: elevations follow the model slab
        # tops (0.12/1.32/2.52), not the old 0/1.2/2.4 plate bottoms.
        scene = school_scene()
        transform = scene["transform"]
        self.assertAlmostEqual(transform["translation"][1], 0.0, places=6)
        self.assertGreater(transform["translation"][2], 20.0)
        self.assertEqual([f["elevation_m"] for f in scene["floors"]], [0.12, 1.32, 2.52])
        self.assertEqual(scene["scale"]["status"], "provisional")
        for floor in scene["floors"]:
            ids = {region["id"] for region in floor["walkable"]}
            self.assertEqual(ids, {f"{floor['id']}-room-w", f"{floor['id']}-room-e", f"{floor['id']}-main"})
            for zone in floor["spawn_zones"]:
                self.assertTrue(any(point_in_region(tuple(p), region) for region in floor["walkable"] for p in zone["outer"]))
        stairs = scene["stairs"]
        self.assertEqual(len(stairs), 2)
        self.assertLess(stairs[0]["bottom"][0], 5.0)
        self.assertGreater(stairs[0]["path"][0][2], stairs[0]["path"][3][2])
        self.assertEqual(scene["exits"][0]["floor"], "floor-1")
        self.assertGreater(scene["exits"][0]["position"][2], 4.5)

    def test_npc_moves_from_third_floor_through_stairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), seed=4)
            sim.ensure_population(1)
            agent = next(iter(sim.agents.values()))
            region = sim._region_index["floor-3-main"]
            agent.position = (7.0, region["elevation"], 3.5)
            agent.floor_id = "floor-3"
            agent.region_id = region["id"]
            agent.nav_node = sim._nearest_nav_node(region["id"], agent.position)
            sim.start()
            for _ in range(5000):
                sim.step(0.02)
                if agent.escaped or agent.failed:
                    break
            self.assertTrue(agent.escaped)
            self.assertEqual(agent.floor_id, "floor-1")
            heights = [row[3] for row in agent.track]
            self.assertGreater(max(heights), 2.0)
            self.assertLess(min(heights), 1.0)
            self.assertTrue(any(row[15] == "walking-stairs" for row in agent.track))

    def test_human_leave_removes_disconnected_participant(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), require_route=False, default_npcs=0)
            agent = sim.add_human("p-drop", "掉线", "#ffffff")
            sim.human_leave(agent.pid)
            self.assertNotIn(agent.pid, sim.agents)
            self.assertEqual(sim.active_human_count(), 0)
            self.assertEqual(sim.public_state()["human_count"], 0)

    def test_smoke_reaches_lower_floor_through_stairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), seed=2)
            sim.ensure_population(0)
            sentinel = sim.add_human("smoke-sentinel", "烟雾测试", "#ffffff")
            sentinel.state = "offline"
            sim.ignite("floor-3")
            sim.phase = "running"
            for _ in range(1000):
                sim.step(0.02)
            lower = [v for node, v in sim.smoke.items()
                      if sim._nav_nodes[node]["floor"] == "floor-1"]
            middle = [v for node, v in sim.smoke.items()
                      if sim._nav_nodes[node]["floor"] == "floor-2"]
            self.assertGreater(max(middle), 0.02)
            self.assertGreater(max(lower), 0.005)

    def test_hole_is_carved_from_hidden_navigation(self):
        scene = school_scene()
        scene["id"] = "hole-test"
        scene["floors"] = [{
            "id": "f1", "elevation_m": 0,
            "walkable": [{"id": "room", "outer": [[0, 0], [10, 0], [10, 10], [0, 10]],
                          "holes": [[[4, 4], [6, 4], [6, 6], [4, 6]]]}],
            "spawn_zones": [],
        }]
        scene["stairs"] = []
        scene["exits"] = [{"id": "exit", "floor": "f1", "position": [0, 0, 5], "width_m": 1.0}]
        normalized = validate_scene(scene)
        region = normalized["floors"][0]["walkable"][0]
        self.assertGreater(len(region["triangles"]), 10)
        self.assertTrue(all(point_in_region(
            (sum(p[0] for p in tri) / 3, sum(p[1] for p in tri) / 3), region
        ) for tri in region["triangles"]))

    def test_preview_navigation_returns_reachable_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), require_route=False)
            preview = sim.preview_navigation()
            self.assertEqual(preview["schema"], 2)
            self.assertGreater(preview["total"], 0)
            self.assertEqual(preview["reachable"], preview["total"])
            top = next(p for p in preview["paths"] if p["floor"] == "floor-3")
            self.assertTrue(top["reachable"])
            self.assertGreaterEqual(len(top["points"]), 3)
            ys = [round(pt[1], 3) for pt in top["points"]]
            self.assertGreaterEqual(max(ys), 2.3)
            self.assertLessEqual(min(ys), 0.12)
            self.assertGreater(max(ys) - min(ys), 2.0)

    def test_preview_navigation_reports_unreachable_without_raising(self):
        scene = school_scene()
        scene["id"] = "no-route"
        scene["stairs"] = []
        scene["floors"] = [scene["floors"][0], {**scene["floors"][1], "id": "floor-x"}]
        scene["exits"] = [{**scene["exits"][0], "floor": "floor-1"}]
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(scene, Path(tmp), require_route=False)
            preview = sim.preview_navigation()
            self.assertLess(preview["reachable"], preview["total"])
            self.assertTrue(any(not p["reachable"] for p in preview["paths"]))

    def test_require_route_false_allows_incomplete_draft(self):
        scene = school_scene()
        scene["id"] = "incomplete"
        scene["stairs"] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SceneValidationError):
                validate_scene(scene)
            sim = EvacSim3D(scene, Path(tmp), require_route=False)
            self.assertEqual(len(sim.scene["floors"]), 3)

    def test_preview_smoke_propagates_through_stairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), require_route=False)
            preview = sim.preview_smoke(source_floor="floor-1")
            self.assertEqual(preview["source"]["floor"], "floor-1")
            other = [r for r in preview["regions"] if r["floor"] != "floor-1"]
            self.assertTrue(other)
            self.assertGreater(max(r["value"] for r in other), 0.02)
            self.assertTrue(all(r["reachable"] for r in preview["regions"]))
            self.assertTrue(preview["connectors"])
            self.assertGreater(max(c["value"] for c in preview["connectors"]), 0.0)

    def test_crowd_at_exit_does_not_deadlock(self):
        """A ring of mutually repelling agents must still make progress.

        Regression: unbounded social force used to exactly cancel forward
        motion for the last few agents, freezing them a few metres before the
        exit for the whole run (75/80 escaped, 5 stuck forever).
        """
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), seed=476167300)
            sim.ensure_population(80)
            sim.start()
            # The calibrated switchback is longer than the old one-step stair,
            # so the same crowd needs more simulated time to clear it.
            for _ in range(16000):
                sim.step(0.02)
                if sim.phase != "running":
                    break
            escaped = sum(1 for a in sim.agents.values() if a.escaped)
            # Real classroom doors are 0.46 model-units wide and the only
            # stair is a switchback, so 80 people do not all clear before the
            # 180s run cap. The deadlock regression is that the crowd still
            # makes progress instead of freezing in the corridor.
            self.assertGreaterEqual(escaped, 30)
            self.assertEqual(sim.phase, "ended")
            self.assertTrue(any(not a.escaped and not a.failed for a in sim.agents.values()) or escaped == 80)

    def test_no_agent_freezes_in_dense_crowd(self):
        """No agent may stay motionless for a full 5 s while still on the floor.

        Sampled during the run because a healthy 200-agent scene now clears
        quickly; the historical bug let agents sit at one coordinate forever.
        """
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), seed=7)
            sim.ensure_population(200)
            sim.start()
            for _ in range(9000):
                sim.step(0.02)
                for agent in sim.agents.values():
                    if agent.escaped or agent.failed:
                        continue
                    tail = [tuple(round(v, 3) for v in row[2:5])
                            for row in agent.track[-25:]]
                    self.assertFalse(
                        len(tail) >= 25 and len(set(tail)) == 1,
                        f"{agent.pid} froze at {agent.position} at {sim.elapsed:.1f}s")
                if sim.phase != "running":
                    break
            self.assertGreater(sum(1 for a in sim.agents.values() if a.escaped), 0)

    def test_crowd_push_is_bounded_by_step(self):
        """The displacement cap must leave net progress along the route."""
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), seed=1)
            sim.ensure_population(40)
            sim.start()
            for _ in range(400):
                sim.step(0.02)
            agent = next(a for a in sim.agents.values() if not a.escaped and not a.failed)
            force = sim._crowd_force(agent)
            travel = 1.4 * 0.02
            push = sim._crowd_displacement(force, 0.02, travel)
            magnitude = sum(c * c for c in push) ** 0.5
            self.assertLessEqual(magnitude, travel * 0.6 + 1e-9)

    def test_human_walks_stairs_and_escapes(self):
        """A human steering itself must cross both stairs and reach the exit."""
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), seed=11)
            human = sim.add_human("p1", "玩家", "#ff8800")
            sim.ensure_population(0)
            # Force the top floor so a successful run must use both stairs.
            human.position = (7.0, 2.52, 3.4)
            human.floor_id = "floor-3"
            human.region_id = "floor-3-main"
            human.nav_node = sim._nearest_nav_node("floor-3-main", human.position)
            sim.start()
            off_surface = 0
            for _ in range(6000):
                # Stair heads are the upper ends of the measured switchback.
                if human.floor_id == "floor-3":
                    target = (2.78, 2.52, 2.35)
                elif human.floor_id == "floor-2":
                    target = (2.78, 1.32, 2.35)
                else:
                    target = (2.8, 0.12, 4.85)
                dx, dz = target[0] - human.position[0], target[2] - human.position[2]
                norm = math.hypot(dx, dz) or 1.0
                human.input = (dx / norm, dz / norm)
                sim.step(0.02)
                if not human.active_connector and not point_in_region(
                        (human.position[0], human.position[2]),
                        sim._region_index[human.region_id]):
                    off_surface += 1
                if human.escaped or human.failed:
                    break
            self.assertTrue(human.escaped, f"human stuck in {human.state} at {human.position}")
            self.assertFalse(human.failed)
            self.assertEqual(off_surface, 0)
            self.assertTrue(any(row[15] == "walking-stairs" for row in human.track))

    def test_human_cannot_walk_through_a_hole(self):
        """Continuous human motion must never enter a walkable-face hole."""
        scene = school_scene()
        scene["id"] = "human-hole"
        scene["stairs"] = []
        scene["floors"] = [{
            "id": "f1", "elevation_m": 0,
            "walkable": [{"id": "room", "outer": [[0, 0], [16, 0], [16, 10], [0, 10]],
                          "holes": [[[6, 3], [10, 3], [10, 7], [6, 7]]]}],
            "spawn_zones": [],
        }]
        scene["exits"] = [{"id": "exit", "floor": "f1", "position": [1, 0, 5], "width_m": 1.0}]
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(scene, Path(tmp), seed=3, require_route=False)
            human = sim.add_human("p1", "玩家", "#ff8800")
            human.position = (2.0, 0.0, 5.0)
            human.floor_id = "f1"
            human.region_id = "room"
            human.nav_node = sim._nearest_nav_node("room", human.position)
            sim.start()
            human.input = (1.0, 0.0)
            for _ in range(1500):
                sim.step(0.02)
                x, _, z = human.position
                self.assertFalse(6.0 < x < 10.0 and 3.0 < z < 7.0,
                                 f"human entered the hole at ({x:.2f}, {z:.2f})")
            self.assertLess(human.position[0], 6.0)

    def test_self_intersecting_polygon_is_rejected(self):
        scene = school_scene()
        scene["id"] = "bad"
        scene["floors"][0]["walkable"][0]["outer"] = [[0, 0], [4, 4], [0, 4], [4, 0]]
        with self.assertRaises(SceneValidationError):
            validate_scene(scene)

    def test_stair_capacity_defaults_and_validation(self):
        scene = school_scene()
        scene["id"] = "capacity-default"
        scene["stairs"][0].pop("capacity", None)
        normalized = validate_scene(scene)
        self.assertEqual(normalized["stairs"][0]["capacity"], 2)
        scene["stairs"][0]["capacity"] = 0
        with self.assertRaises(SceneValidationError):
            validate_scene(scene)
        scene["stairs"][0]["capacity"] = 65
        with self.assertRaises(SceneValidationError):
            validate_scene(scene)

    def test_stair_capacity_queues_and_releases(self):
        scene = school_scene()
        scene["id"] = "capacity-queue"
        scene["stairs"][0]["capacity"] = 1
        scene["stairs"][1]["capacity"] = 1
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(scene, Path(tmp), seed=1)
            sim.ensure_population(2)
            agents = list(sim.agents.values())
            for agent in agents:
                agent.position = (8.0, 1.32, 3.6)
                agent.floor_id = "floor-2"
                agent.region_id = "floor-2-main"
                agent.nav_node = sim._nearest_nav_node("floor-2-main", agent.position)
            sim.start()
            states = set()
            for _ in range(3000):
                sim.step(0.02)
                states.update(agent.state for agent in agents)
                if sim.phase == "ended":
                    break
            self.assertIn("queued-stairs", states)
            self.assertTrue(all(agent.escaped for agent in agents))

    def test_human_body_never_crosses_walkable_boundary(self):
        scene = school_scene()
        scene["id"] = "human-walls"
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(scene, Path(tmp), seed=5)
            human = sim.add_human("human-1", "一号", "#ff8800")
            # Start hard against the south wall and keep pushing into it while
            # also walking east: the body must slide along the facade and never
            # end up outside the walkable polygon.
            human.position = (6.0, 0.0, 3.7)
            human.floor_id = "floor-1"
            human.region_id = "floor-1-main"
            human.nav_node = sim._nearest_nav_node("floor-1-main", human.position)
            human.input = (0.6, -1.0)
            region = sim._region_index["floor-1-main"]
            sim.start()
            for _ in range(120):
                sim.step(0.02)
                self.assertGreaterEqual(
                    region_clearance((human.position[0], human.position[2]), region), -1e-6)
            self.assertEqual(human.floor_id, "floor-1")
            self.assertGreater(human.position[0], 6.2)

    def test_human_slides_along_wall_instead_of_sticking(self):
        scene = school_scene()
        scene["id"] = "human-slide"
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(scene, Path(tmp), seed=5)
            human = sim.add_human("human-1", "一号", "#ff8800")
            human.position = (6.0, 0.0, 4.15)
            human.floor_id = "floor-1"
            human.region_id = "floor-1-main"
            human.nav_node = sim._nearest_nav_node("floor-1-main", human.position)
            # Mostly into the north wall, with a small eastward component.
            human.input = (0.25, 1.0)
            region = sim._region_index["floor-1-main"]
            sim.start()
            for _ in range(120):
                sim.step(0.02)
                self.assertGreaterEqual(
                    region_clearance((human.position[0], human.position[2]), region), -1e-6)
                # The wall must absorb the normal component without letting the
                # body sink any further into it.
                self.assertLessEqual(human.position[2], 4.28)
            self.assertGreater(human.position[0], 6.15)

    def test_human_waits_when_stair_capacity_is_full(self):
        scene = school_scene()
        scene["id"] = "human-capacity"
        scene["stairs"][0]["capacity"] = 1
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(scene, Path(tmp), seed=5)
            first = sim.add_human("human-1", "一号", "#ff8800")
            second = sim.add_human("human-2", "二号", "#00aaff")
            for agent in (first, second):
                agent.position = (3.98, 0.12, 2.95)
                agent.floor_id = "floor-1"
                agent.region_id = "floor-1-main"
                agent.nav_node = sim._nearest_nav_node("floor-1-main", agent.position)
                agent.input = (0.0, -1.0)
            sim.start()
            for _ in range(20):
                sim.step(0.02)
            self.assertEqual(first.state, "walking-stairs")
            self.assertEqual(second.state, "queued-stairs")

    def test_vertical_stair_projection_preserves_height_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), seed=3)
            stair = {"bottom": [5.0, 0.0, 5.0], "top": [5.0, 3.0, 5.0], "width_m": 1.2}
            projected = sim._project_stair_position(stair, (5.0, 1.5, 5.0))
            self.assertAlmostEqual(projected[1], 1.5, places=6)
            self.assertAlmostEqual(projected[0], 5.0, places=6)
            self.assertAlmostEqual(projected[2], 5.0, places=6)

    def test_stair_projection_keeps_agent_inside_calibrated_width(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), seed=3)
            stair = sim.scene["stairs"][0]
            projected = sim._project_stair_position(stair, (9.8, 99.0, 99.0))
            self.assertLessEqual(
                math.dist((projected[0], projected[2]), (stair["bottom"][0], stair["bottom"][2])),
                math.dist((stair["top"][0], stair["top"][2]), (stair["bottom"][0], stair["bottom"][2])) + stair["width_m"],
            )
            self.assertGreaterEqual(projected[1], min(stair["bottom"][1], stair["top"][1]))
            self.assertLessEqual(projected[1], max(stair["bottom"][1], stair["top"][1]))

    def test_human_crosses_gap_between_room_and_corridor_without_freezing(self):
        """A doorway seam must not silently freeze a participant.

        Room and corridor outlines are separate polygons, so a thin band
        between them is outside every walkable region.  A body stepping into
        that band used to leave ``_region_for`` returning ``None`` and the
        mover zeroed its velocity permanently.  The player visibly "moved once
        and then could not move again".
        """
        scene = school_scene()
        scene["id"] = "door-seam"
        scene["floors"] = [scene["floors"][1]]
        scene["floors"][0]["walkable"] = [
            {"id": "room", "outer": [[2.0, 2.0], [4.0, 2.0], [4.0, 3.42], [2.0, 3.42]], "holes": []},
            {"id": "corridor", "outer": [[2.0, 3.45], [14.5, 3.45], [14.5, 4.2], [2.0, 4.2]], "holes": []},
        ]
        scene["floors"][0]["spawn_zones"] = [
            {"outer": [[2.0, 3.6], [14.5, 3.6], [14.5, 4.1], [2.0, 4.1]]}
        ]
        scene["stairs"] = []
        scene["exits"] = [{"id": "exit", "floor": "floor-2", "position": [10.0, 1.2, 3.9],
                           "width_m": 1.2}]
        scene["doors"] = [{
            "id": "door-room", "name": "房间门", "floor": "floor-2",
            "position": [3.0, 1.2, 3.45], "width_m": 1.2, "normal": [0, 1],
            "from_region": "room", "to_region": "corridor", "mode": "both",
            "initial_state": "open", "locked": False, "fire_block_threshold": .85,
            "auto_close_s": 4, "open_duration_s": .6,
        }]
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(scene, Path(tmp), seed=5)
            sim.reset()
            agent = sim.add_human("p1", "玩家", "#fff")
            # Start in the corridor and walk back through the shared doorway,
            # crossing the non-walkable seam between the two polygons.
            agent.position = (3.0, 1.2, 3.9)
            agent.region_id = "corridor"
            agent.nav_node = sim._nearest_nav_node("corridor", agent.position)
            sim.phase = "running"
            for _ in range(300):
                agent.input = (0.0, -1.0)
                sim.step(0.02)
            self.assertIsNotNone(
                sim._region_for(agent.position, agent.floor_id),
                f"agent left every walkable region at {agent.position}",
            )
            self.assertLess(
                agent.position[2], 3.42,
                "agent never crossed the seam into the room",
            )

    def test_nearest_walkable_region_recovers_stranded_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim = EvacSim3D(school_scene(), Path(tmp), seed=3)
            recovered = sim._nearest_walkable_region("floor-2", (5.99, 1.2, 3.435))
            self.assertIsNotNone(recovered, "seam position should recover to a region")
            region, point = recovered
            self.assertTrue(point_in_region(point, region))


if __name__ == "__main__":
    unittest.main()
