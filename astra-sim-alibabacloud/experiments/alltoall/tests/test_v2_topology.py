import unittest
from simai_a2a.model import HardwareProfile,LinkModel


class V2TopologyTests(unittest.TestCase):
    def test_leafspine_counts_routes_and_one_wan_per_remote_pair(self):
        from simai_a2a.leafspine import build_leafspine,fixed_routes,server_snapshot
        for dcs in (2,3,4):
            hw=HardwareProfile(dcs*8,4,LinkModel(100,.0023),LinkModel(700,.02),'v2',400,1)
            top=build_leafspine(hw,dcs);routes=fixed_routes(top)
            self.assertIn((0,top.nvswitch_ids[0]),routes)
            self.assertEqual(len(top.gpu_ids),32*dcs);self.assertEqual(len(top.background_ids),dcs)
            self.assertEqual(len(top.tor_ids),2*dcs);self.assertEqual(len(top.spine_ids),2*dcs)
            self.assertEqual(top.node_count,32*dcs+dcs+8*dcs+5*dcs)
            ports={}
            for link in top.links:
                if link.src in top.tor_ids or link.dst in top.tor_ids:ports[link.src,link.dst]=link.bandwidth_gbps
            self.assertTrue(all(v in (400,1600) for v in ports.values()))
            snapshot=server_snapshot(top,hw,routes)
            for s in range(hw.num_servers):
                for d in range(hw.num_servers):
                    path=snapshot['server_paths'][s*hw.num_servers+d]
                    wans=[snapshot['resource_names'][e] for e in path['resource_ids'] if snapshot['resource_names'][e].startswith('wan:')]
                    self.assertEqual(len(wans),int(s//8!=d//8))
            self.assertAlmostEqual(snapshot['server_paths'][4]['propagation_us'],3.)
            self.assertAlmostEqual(snapshot['server_paths'][8]['propagation_us'],54.4)

