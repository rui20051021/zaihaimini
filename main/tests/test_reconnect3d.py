import tempfile
import unittest
from pathlib import Path
from nav3d import EvacSim3D
from test_movement3d import fixture


class ReconnectTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.sim=EvacSim3D(fixture(),Path(tmp.name),default_npcs=0)
        self.agent=self.sim.add_human('p-reconnect','Player','#fff')

    def test_disconnect_stops_movement_preserves_tracks_and_last_player_can_rejoin(self):
        sim=self.sim;a=self.agent;sim.start();a.input=(1,0);sim.advance(.1)
        position=a.position;tracks=list(a.track);sim.human_disconnect(a.pid)
        sim.advance(.2)
        self.assertEqual(sim.phase,'running');self.assertEqual(a.position,position)
        self.assertEqual(a.track,tracks);self.assertEqual(a.input,(0,0));self.assertEqual(a.velocity,(0,0,0))
        self.assertEqual(sim.active_human_count(),0)
        returned=sim.add_human(a.pid,'Player','#fff')
        self.assertIs(returned,a);self.assertEqual(a.position,position);self.assertEqual(a.track,tracks)
        self.assertEqual(sim.active_human_count(),1)

    def test_reset_does_not_resurrect_offline_participant(self):
        self.sim.human_disconnect(self.agent.pid);self.sim.reset()
        self.assertEqual(self.agent.state,'offline');self.assertEqual(self.sim.active_human_count(),0)
        self.sim.add_human(self.agent.pid,'Player','#fff');self.assertEqual(self.agent.state,'waiting')

    def test_stair_reconnect_keeps_connector_progress(self):
        a=self.agent;a.active_connector='stairs';a.connector_progress=.4
        self.sim.human_disconnect(a.pid);self.sim.add_human(a.pid,'Player','#fff')
        self.assertEqual(a.active_connector,'stairs');self.assertEqual(a.connector_progress,.4)
        self.assertEqual(a.state,'walking-stairs')

    def test_offline_body_does_not_push_other_participants_or_occupy_stairs(self):
        a=self.agent;b=self.sim.add_human('p-other','Other','#fff')
        a.position=(2,0,2);b.position=(2.1,0,2);a.active_connector='stairs'
        self.sim.human_disconnect(a.pid)
        self.sim._spatial_bins={(1,0,1):[a,b]}
        self.assertEqual(self.sim._crowd_force(b),(0,0,0))
        self.assertEqual(self.sim._stair_occupancy('stairs',True),0)

    def test_reconnected_stair_actor_waits_for_capacity_without_losing_progress(self):
        sim=self.sim;a=self.agent;a.active_connector='stairs';a.connector_progress=.4
        sim.human_disconnect(a.pid);sim.add_human(a.pid,'Player','#fff')
        b=sim.add_human('p-resident','Other','#fff');b.active_connector='stairs'
        sim._stairs=[{'id':'stairs','capacity':1,'width_m':.6}]
        self.assertTrue(sim._advance_human_stairs(a,.02))
        self.assertEqual(a.state,'queued-stairs');self.assertEqual(a.connector_progress,.4)
        self.assertEqual(sim._stair_occupancy('stairs',True),1)
