"""Mixed heterogeneity must be present in physical paths, not only planner costs."""
from collections import Counter
import unittest
from test_heterogeneous import driver


class ExpandedFabricTests(unittest.TestCase):
    def test_expanded_blocks_have_41_conditions_and_three_primary_methods(self):
        module=driver();cases=module.case_specs('expanded')
        self.assertEqual(len(cases),41)
        self.assertEqual(Counter(c['block'] for c in cases),dict(primary=15,volume=8,pressure=8,scale=4,real=6))
        self.assertEqual(len({c['name'] for c in cases}),41)
        self.assertEqual(module.CONFIG['phases']['expanded']['methods'],['fast-simai','spreadout','resource-maxq'])
        self.assertEqual({c['gpu_count'] for c in cases},{64,128,256})
        self.assertEqual({c['rho'] for c in cases},{0,.3,.6})
        self.assertEqual({c['mib'] for c in cases if not c['model']},{1,4,16})

    def test_forward_and_reverse_asymmetry_follows_actual_links(self):
        module=driver();top,hw=module.fabric('leaf-route-asymmetric',128)
        routes=module.fixed_routes(top);snapshot=module.server_snapshot(top,hw,routes)
        a=snapshot['server_paths'][5];b=snapshot['server_paths'][5*hw.num_servers]
        self.assertEqual(min(snapshot['bandwidth_gbps'][r] for r in a['resource_ids']),100)
        self.assertEqual(min(snapshot['bandwidth_gbps'][r] for r in b['resource_ids']),400)
        self.assertAlmostEqual(a['propagation_us'],7.3)
        self.assertAlmostEqual(b['propagation_us'],3.)
        edges={(u,v):(e.bandwidth_gbps,e.latency_ns) for e in top.links for u,v in [(e.src,e.dst),(e.dst,e.src)]}
        for s,d,reference in [(0,5,a),(5,0,b)]:
            for offset in range(4):
                node=top.nvswitch_ids[s];end=top.nvswitch_ids[d];rates=[];delay=200
                while node!=end:
                    nxt=routes[node,d*4+offset];bw,tau=edges[node,nxt];rates.append(bw);delay+=tau;node=nxt
                self.assertEqual(min(rates),min(snapshot['bandwidth_gbps'][r] for r in reference['resource_ids']))
                self.assertAlmostEqual(delay/1000,reference['propagation_us'])

    def test_both_bandwidth_and_delay_vary_and_larger_pods_are_connected(self):
        module=driver()
        for kind,p in [('leaf-mixed',128),('pod-mixed',128),('pod-mixed',256),('multi-cut-mixed',128),('leaf-route-asymmetric',256)]:
            top,hw=module.fabric(kind,p);routes=module.fixed_routes(top)
            self.assertEqual(len(routes),top.node_count*(top.node_count-1))
            snapshot=module.server_snapshot(top,hw,routes)
            active=[x for i,x in enumerate(snapshot['server_paths']) if i//hw.num_servers!=i%hw.num_servers]
            bottlenecks={min(snapshot['bandwidth_gbps'][r] for r in x['resource_ids']) for x in active}
            # Both Pod exits exceed/equal the independent400G server NIC.
            if kind=='pod-mixed':self.assertEqual(bottlenecks,{400})
            else:self.assertGreater(len(bottlenecks),1)
            self.assertGreater(len({snapshot['bandwidth_gbps'][r] for r in module.shared_ids(top,snapshot)}),1)
            self.assertGreater(len({x['propagation_us'] for x in active}),1)

    def test_background_route_classes_spread_spines_without_changing_gpu_paths(self):
        module=driver();top,_=module.fabric('leaf-uniform',128)
        ordinary=module.fixed_routes(top)
        routes=module.fixed_routes(top,route_classes={h:i for i,h in enumerate(top.background_ids)})
        self.assertEqual({k:v for k,v in ordinary.items() if k[1] in top.gpu_ids},
                         {k:v for k,v in routes.items() if k[1] in top.gpu_ids})
        used={routes[top.tor_ids[0],h] for h in top.background_ids[1:]}
        self.assertEqual(used,set(top.spine_ids))

    def test_invalid_zero_or_nonserver_size_is_rejected(self):
        module=driver()
        for p in [0,3,63]:
            with self.assertRaises(ValueError):module.fabric('leaf-route-asymmetric',p)
