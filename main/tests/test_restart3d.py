"""Restart a v2 trial without losing the connected player object / input channel."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from types import SimpleNamespace

import main
from nav3d import EvacSim3D, point_in_region


def trial_scene():
    return {
        "schema": 2, "id": "restart-test", "name": "Restart test",
        "model": {"file": "test.glb"},
        "scale": {"status": "provisional", "reference_length_m": None},
        "floors": [{"id": "f1", "elevation_m": 0,
                    "walkable": [{"id": "room", "outer": [[0, 0], [10, 0], [10, 10], [0, 10]], "holes": []}],
                    "spawn_zones": [{"outer": [[2, 2], [3, 2], [3, 3], [2, 3]]}]}],
        "stairs": [], "exits": [{"id": "out", "floor": "f1", "position": [9.5, 0, 5], "width_m": 1}],
    }


class Body:
    headers = {}

    def __init__(self, data):
        self.data = data

    async def stream(self):
        yield json.dumps(self.data).encode()


class Restart3DTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self.sim = EvacSim3D(trial_scene(), self.data_dir, default_npcs=0)
        self.hub = main.SceneHub()
        for name, value in [("SCENE_SIM", self.sim), ("SCENE_HUB", self.hub), ("DATA_DIR", self.data_dir)]:
            patcher = patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.hub.broadcast = AsyncMock()

    def join(self, pid="p-restart"):
        agent = self.sim.add_human(pid, "真人", "#fff")
        self.hub.players[pid] = object()
        return agent

    def restart(self, **data):
        return asyncio.run(main.api_v2_control("restart", Body(data)))

    def test_restart_running_resets_state_but_keeps_player_and_settings(self):
        a = self.join()
        self.sim.start()
        a.input = (.7, .2)
        self.sim.advance(.3)
        self.sim.time_scale = 2; self.sim.panic = .7; self.sim.wind = (1, 0, 2)
        self.sim.experiment = {"trial": "example"}
        a.pressure = 15; a.density = 3; a.health = .4; a.smoke_dose = .8
        a.active_connector = "old"; a.connector_progress = .5; a.connector_forward = False
        self.sim.fire["room"] = 1
        self.sim.smoke = dict.fromkeys(self.sim.smoke, .5)
        self.sim.door_events.append({"event": "old"})
        result = self.restart(demo=True)
        self.assertTrue(result["ok"])
        self.assertEqual((self.sim.phase, self.sim.elapsed, self.sim.real_elapsed), ("running", 0, 0))
        self.assertIs(self.sim.agents[a.pid], a)
        self.assertIn(a.pid, self.hub.players)
        self.assertEqual(a.input, (0, 0)); self.assertEqual(a.velocity, (0, 0, 0))
        self.assertEqual((a.health, a.smoke_dose, a.pressure, a.density), (1, 0, 0, 0))
        self.assertFalse(a.track); self.assertFalse(a.path)
        self.assertIsNone(a.active_connector); self.assertEqual(a.connector_progress, 0)
        self.assertTrue(point_in_region((a.position[0], a.position[2]), self.sim._region_index[a.region_id]))
        self.assertFalse(self.sim.fire); self.assertFalse(any(self.sim.smoke.values()))
        self.assertFalse(self.sim.door_events)
        self.assertEqual((self.sim.time_scale, self.sim.panic, self.sim.wind), (2, .7, (1, 0, 2)))
        self.assertEqual(self.sim.experiment, {"trial": "example"})
        self.assertEqual(result["round_revision"], 1)
        self.assertFalse(result["replay_available"])
        self.hub.broadcast.assert_awaited_once()
        # The WebSocket's old object reference must still control the new round.
        before = a.position
        a.input = (1, 0)
        self.sim.advance(.3)
        self.assertGreater(a.position[0], before[0])

    def test_ended_connected_escape_or_down_can_restart(self):
        a = self.join()
        for terminal in ("escaped", "down"):
            with self.subTest(terminal=terminal):
                self.sim.phase = "ended"
                a.escaped = terminal == "escaped"; a.failed = terminal == "down"; a.state = terminal
                self.assertEqual(self.sim.active_human_count(), 0)
                self.assertEqual(main._scene_sim_state()["connected_human_count"], 1)
                result = self.restart(demo=True)
                self.assertEqual(result["phase"], "running")
                self.assertFalse(a.escaped); self.assertFalse(a.failed)

    def test_no_connected_human_rejected_without_changing_trial(self):
        a = self.sim.add_human("p-disconnected", "离线", "#fff")
        a.escaped = True
        self.sim.phase = "ended"; self.sim.elapsed = 12
        before = copy.deepcopy(self.sim.public_state())
        with self.assertRaises(HTTPException) as error:
            self.restart(demo=True)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.sim.public_state(), before)
        self.hub.broadcast.assert_not_awaited()

    def test_provisional_restart_requires_explicit_demo_before_reset(self):
        self.join(); self.sim.start(); self.sim.advance(.2)
        before = copy.deepcopy(self.sim.public_state())
        for data in ({}, {"demo": False}, {"demo": "true"}):
            with self.subTest(data=data), self.assertRaises(HTTPException) as error:
                self.restart(**data)
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(self.sim.public_state(), before)

    def test_verified_paused_trial_restarts_without_demo(self):
        self.join(); self.sim.scene["scale"]["status"] = "verified"
        self.sim.start(); self.sim.advance(.2); self.sim.pause()
        self.assertEqual(self.restart()["elapsed"], 0)
        self.assertEqual(self.sim.phase, "running")

    def test_restart_npcs_without_humans_and_drops_disconnected_ghosts(self):
        self.sim.ensure_population(2)
        ghost = self.sim.add_human("p-ghost", "离线", "#fff"); ghost.escaped = True
        result = self.restart(demo=True)
        self.assertEqual(result["npc_count"], 2)
        self.assertEqual(result["human_count"], 0)
        self.assertNotIn(ghost.pid, self.sim.agents)

    def test_saved_v2_and_legacy_files_are_not_modified_by_restart(self):
        self.join(); self.sim.start(); self.sim.advance(.2); self.sim.save_outputs()
        for name in ("evac_latest.json", "evac_latest_tracks.csv"):
            (self.data_dir / name).write_bytes(b"historical bytes\r\n")
        before = {p.name: p.read_bytes() for p in self.data_dir.iterdir()}
        self.restart(demo=True)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.data_dir.iterdir()})

    def test_known_navigation_mismatch_blocks_formal_start_and_restart(self):
        self.join()
        self.sim.scene["scale"]["status"]="verified"
        self.sim.scene["navigation_review"]={"status":"needs-review"}
        for action in ("start","restart"):
            with self.subTest(action=action), self.assertRaises(HTTPException) as error:
                asyncio.run(main.api_v2_control(action,Body({})))
            self.assertEqual(error.exception.status_code,409)
            self.assertEqual(self.sim.phase,"idle")
        self.assertEqual(self.restart(demo=True)["phase"],"running")

    def test_restart_route_requires_admin(self):
        route = next(r for r in main.app.routes if getattr(r, "path", "") == "/api/v2/control/{action}")
        self.assertIn(main.require_admin, [d.call for d in route.dependant.dependencies])
        self.join()
        with patch.object(main, "ADMIN_TOKEN", "test-only-restart-token"):
            request = SimpleNamespace(headers={}, cookies={})
            with self.assertRaises(HTTPException) as error:
                asyncio.run(main.require_admin(request))
            self.assertEqual(error.exception.status_code, 401)
            self.assertEqual(self.sim.phase, "idle")
            request.headers["X-Maze-Admin-Token"] = "test-only-restart-token"
            asyncio.run(main.require_admin(request))
            self.assertEqual(self.restart(demo=True)["phase"], "running")


if __name__ == "__main__":
    unittest.main()
