import importlib.util
import unittest


class TopologyTests(unittest.TestCase):
    def build(self,kind,**kw):
        self.assertIsNotNone(importlib.util.find_spec('resource_order.topology'),'new topology missing')
        from resource_order.topology import build_topology
        return build_topology(kind,**kw)

    def test_128_gpus_share_physical_nic_without_capacity_replication(self):
        n=self.build('tree')
        self.assertEqual(n.gpu_count,128)
        p=n.path(0,64);q=n.path(1,72)
        shared=set(p)&set(q)
        self.assertIn((128,144),shared)
        self.assertEqual(n.links[128,144].gbps,400)
        self.assertEqual(n.path(0,1),((0,128),(128,1)))
        self.assertEqual(len(n.path(0,8)),4)
        self.assertEqual(len(n.path(0,32)),6)
        self.assertEqual(len(n.path(0,64)),8)

    def test_leaf_spine_oversubscription_is_two_to_one(self):
        n=self.build('leaf-spine')
        down=sum(n.links[s,144].gbps for s in range(128,132))
        up=sum(n.links[144,s].gbps for s in (148,149))
        self.assertEqual((down,up),(1600,800))
        self.assertIn((144,148),n.path(0,32))
        self.assertIn((144,149),n.path(0,40))

    def test_link_overrides_affect_both_directions_and_path_cost(self):
        n=self.build('tree',overrides={(144,148):(100,5.)})
        self.assertEqual(n.links[148,144].gbps,100)
        self.assertAlmostEqual(n.delay_us(0,32),7.3)
        with self.assertRaises(ValueError):self.build('tree',overrides={(0,127):(100,1.)})


if __name__=='__main__':unittest.main()
