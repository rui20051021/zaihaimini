import copy
import math
import tempfile
import time
import unittest
from pathlib import Path
from nav3d import EvacSim3D, AGENT_RADIUS, point_in_region, region_clearance, slide_along_region


def fixture(gap=.04):
    return {"schema":2,"id":"movement-test","model":{"file":"test.glb"},
        "scale":{"status":"provisional","reference_length_m":None},
        "floors":[{"id":"f1","elevation_m":0,"walkable":[
            {"id":"room","outer":[[0,0],[10,0],[10,3],[0,3]],"holes":[]},
            {"id":"corridor","outer":[[0,3+gap],[10,3+gap],[10,5],[0,5]],"holes":[]}],
            "spawn_zones":[{"outer":[[1,1],[2,1],[2,2],[1,2]]}]}],
        "stairs":[],"exits":[{"id":"out","floor":"f1","position":[9.5,0,4.4],"width_m":1}],
        "doors":[{"id":"door","floor":"f1","from_region":"room","to_region":"corridor",
                   "position":[2,0,3+gap],"normal":[0,1],"width_m":.9,"mode":"manual","initial_state":"closed"}]}


class Movement3DTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.sim=EvacSim3D(fixture(),Path(self.tmp.name),default_npcs=0)
        self.a=self.sim.add_human('p-test','玩家','#fff')
        self.place(2,2)

    def place(self,x,z,region='room'):
        self.a.position=(x,0,z);self.a.region_id=region;self.a.floor_id='f1'
        self.a.nav_node=self.sim._nearest_nav_node(region,self.a.position)

    def test_rotated_wall_slides_along_normal_not_world_axes(self):
        region={'outer':[(0,0),(6,0),(0,6)],'holes':[]}
        p=(2.8,2.8)
        for _ in range(30):p=slide_along_region(p,(.06,.02),region,.22)
        self.assertGreater(p[0],3.1)
        self.assertLess(p[1],2.7)
        self.assertGreaterEqual(region_clearance(p,region),.22-1e-6)

    def test_sweep_does_not_tunnel_through_thin_hole(self):
        region={'outer':[(0,0),(10,0),(10,6),(0,6)],'holes':[[(4,1),(4.02,1),(4.02,5),(4,5)]]}
        p=slide_along_region((2,3),(5,0),region,.22)
        self.assertLessEqual(p[0],3.780001)
        self.assertGreaterEqual(region_clearance(p,region),.22-1e-6)

    def test_corners_cannot_push_body_outside(self):
        region={'outer':[(0,0),(3,0),(3,3),(0,3)],'holes':[]};p=(2,2)
        for _ in range(100):p=slide_along_region(p,(1,1),region,.22)
        self.assertLessEqual(p[0],2.780001);self.assertLessEqual(p[1],2.780001)
        self.assertGreaterEqual(region_clearance(p,region),.22-1e-6)

    def test_closed_and_opening_doors_remain_solid(self):
        for status in ['closed','opening','locked','fire-blocked','blocked']:
            with self.subTest(status=status):
                self.sim._door_state['door']=status;self.place(2,2.7)
                q=self.sim._move_human_on_floor(self.a,(0,1))
                self.assertLessEqual(q[1],3-AGENT_RADIUS+1e-6)

    def test_open_door_crosses_seam_continuously_and_both_directions(self):
        self.sim._door_state['door']='open';self.place(2,2.7)
        for direction in (1,-1):
            for _ in range(70):
                p=self.a.position;q=self.sim._move_human_on_floor(self.a,(0,.02*direction))
                self.assertLessEqual(math.dist((p[0],p[2]),q),.020001,'Door must not teleport the player')
                self.a.position=(q[0],0,q[1])
            self.assertEqual(self.a.region_id,'corridor' if direction>0 else 'room')

    def test_nearby_open_door_does_not_disable_whole_wall(self):
        self.sim._door_state['door']='open';self.place(2.4,2.7)
        q=self.sim._move_human_on_floor(self.a,(0,1))
        self.assertLess(q[1],3,'Body outside the finite aperture must hit the jamb')

    def test_touching_regions_with_closed_door_have_no_hidden_graph_bypass(self):
        self.sim=EvacSim3D(fixture(0),Path(self.tmp.name),default_npcs=0)
        node=self.sim._nearest_nav_node('room',(2,0,2))
        self.assertEqual(self.sim._nearest_exit_path(node)[0],[])
        self.sim._door_state['door']='open';self.sim._build_graph()
        self.assertTrue(self.sim._nearest_exit_path(node)[0])

    def test_adjacent_surfaces_without_door_are_walkable(self):
        scene=fixture(0);scene['doors']=[]
        self.sim=EvacSim3D(scene,Path(self.tmp.name),default_npcs=0)
        self.a=self.sim.add_human('p-test','玩家','#fff');self.place(2,2.8)
        q=self.sim._move_human_on_floor(self.a,(0,.5))
        self.assertAlmostEqual(q[1],3.3)
        self.assertEqual(self.a.region_id,'corridor')

    def test_stale_network_input_stops_authoritative_motion(self):
        self.sim.start();self.a.input=(1,0);self.a.input_received_at=time.monotonic()-1
        before=self.a.position;self.sim.step(.02)
        self.assertEqual(self.a.position,before);self.assertEqual(self.a.velocity,(0,0,0))

    def test_diagonal_input_is_normalized_and_acceleration_responsive(self):
        self.sim.start();self.a.input=(1,1)
        for _ in range(10):self.sim.step(.02)
        self.assertLessEqual(math.hypot(self.a.velocity[0],self.a.velocity[2]),1.250001)
        self.assertGreater(math.hypot(self.a.velocity[0],self.a.velocity[2]),.8)

    def test_host_cannot_close_door_on_crossing_person(self):
        self.sim._door_state['door']='open';self.place(2,3.02)
        result=self.sim.request_door('door',action='close',source='host')
        self.assertFalse(result['ok']);self.assertEqual(result['reason'],'occupied')
        self.assertEqual(self.sim._door_state['door'],'open')

    def test_motion_geometry_cache_has_bounded_size(self):
        for _ in range(20):
            for status in ['open','closed']:
                self.sim._door_state['door']=status;self.sim._motion_geometry('f1')
        self.assertEqual(len(self.sim._motion_cache),1)
