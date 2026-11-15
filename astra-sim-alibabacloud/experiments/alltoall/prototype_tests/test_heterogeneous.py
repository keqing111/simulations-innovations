"""Generic physical shared resources and fixed topology/load experiment design."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from simai_a2a.schedulers.resource_budget import prepare_resources,plan
from test_resource_budget import topology


def driver():
    path=Path(__file__).resolve().parents[4]/'research/resource_a2av/experiment.py'
    spec=importlib.util.spec_from_file_location('heterogeneous_experiment_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class GenericResourceTests(unittest.TestCase):
    def test_explicit_shared_resources_need_no_wan_labels_and_can_overlap(self):
        top=topology(3);top['resource_names']=[s.replace('wan:','link:') for s in top['resource_names']]
        top['shared_resource_ids']=[6,7];top['server_paths'][1]['resource_ids'].insert(-1,7)
        res=prepare_resources(top)
        self.assertEqual(res['group_resources'],{6,7})
        self.assertEqual(res['edges'][1]['groups'],(6,7))
        self.assertIn(1,res['groups'][6]);self.assertIn(1,res['groups'][7])

    def test_explicit_grouping_rejects_unknown_or_duplicate_resources(self):
        for ids in ([999],[6,6],[-1],[True]):
            top=topology(3);top['shared_resource_ids']=ids
            with self.assertRaisesRegex(ValueError,'shared resource'):prepare_resources(top)

    def test_explicit_equivalent_groups_preserve_legacy_plans_after_resource_rename(self):
        legacy=topology(4,group=lambda s,d:(s//2,d//2))
        generic=dict(legacy,resource_names=[n.replace('wan:','shared:') for n in legacy['resource_names']],
                     shared_resource_ids=list(range(8,len(legacy['resource_names']))))
        R=[[6,8,0,0],[8,0,6,0],[0,6,0,8],[0,0,8,6]]
        prepared=dict(n=4,g=1,R=R,U=[0,8,0,0,8,0,0,0,0,6,0,2,0,0,8,0])
        a=plan(prepared,legacy,maximum_quantum=True);b=plan(prepared,generic,maximum_quantum=True)
        self.assertEqual(a['permutations'],b['permutations']);self.assertEqual(a['coefficients'],b['coefficients'])


class HeterogeneousExperimentTests(unittest.TestCase):
    def test_pilot_crosses_distribution_and_volume_without_balanced_seed_duplicates(self):
        module=driver();cases=module.case_specs('pilot')
        self.assertEqual(len(cases),18);self.assertEqual(len({c['name'] for c in cases}),18)
        self.assertEqual({c['family'] for c in cases},{'balanced','zipf08','zipf12'})
        self.assertEqual({c['mib'] for c in cases},{1,4})
        self.assertEqual({c['gpu_count'] for c in cases},{64})
        self.assertEqual(sum(c['family']=='balanced' for c in module.case_specs('topology')),8)

    def test_bandwidth_and_delay_match_the_physical_routes(self):
        module=driver()
        for kind,minimum,tau in [('leaf-uniform',400,3.),('leaf-bandwidth',100,3.),('leaf-delay',400,8.6)]:
            top,hw=module.fabric(kind,64);routes=module.fixed_routes(top)
            snap=module.server_snapshot(top,hw,routes)
            path=snap['server_paths'][4*hw.num_servers+8]
            self.assertEqual(min(snap['bandwidth_gbps'][r] for r in path['resource_ids']),minimum)
            self.assertAlmostEqual(path['propagation_us'],tau)
            self.assertFalse(top.gateway_ids)

    def test_multicut_path_has_all_three_cut_resources_and_pod_is_connected(self):
        module=driver()
        top,hw=module.fabric('multi-cut',64);snap=module.server_snapshot(top,hw,module.fixed_routes(top))
        rates=[snap['bandwidth_gbps'][i] for i in snap['server_paths'][12]['resource_ids']]
        self.assertEqual(rates.count(100),2);self.assertEqual(rates.count(200),1)
        top,hw=module.fabric('pod-core',128)
        routes=module.fixed_routes(top)
        self.assertEqual(len(routes),top.node_count*(top.node_count-1))
        with self.assertRaises(ValueError):module.fabric('pod-core',64)

    def test_background_rho_is_normalized_to_the_busiest_shared_resource(self):
        module=driver();top,hw=module.fabric('leaf-bandwidth',64);routes=module.fixed_routes(top)
        snap=module.server_snapshot(top,hw,routes)
        tasks,meta=module.background(top,routes,snap,.3)
        self.assertEqual(len(tasks.splitlines()),12)
        self.assertLessEqual(meta['max_configured_resource_rho'],.3)
        self.assertGreater(meta['max_configured_resource_rho'],.2999)
        self.assertGreater(meta['interval_ns'],0)
        self.assertEqual(module.background(top,routes,snap,0)[0],'')

    def test_prepared_generic_fabric_reuses_existing_replay_without_gateways(self):
        module=driver();case=module.case_specs('pilot')[0]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,top=module.prepare_case(root/'inputs',case)
            binary=root/'unused-binary';binary.write_bytes(b'not executed')
            result=module.iterations.run(root/'out',binary,[case['name']],['resource-maxq'],
                prepare_only=True,source_scenario=source,source_topology=top)
            self.assertEqual(result['runs'][0]['status'],'prepared')
            snapshot=json.loads((root/'out'/case['name']/'planner_topology.json').read_text())
            self.assertTrue(snapshot['shared_resource_ids'])
            self.assertEqual(prepare_resources(snapshot)['wans'],set())
