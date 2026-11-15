import importlib.util
from pathlib import Path
import unittest
import numpy as np

def smoke_module(tool='run_wan_budget_smoke.py'):
    import sys
    path=Path(__file__).resolve().parents[1]/'tools'/tool
    if str(path.parent) not in sys.path:sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('wan_smoke',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

class SmokePreparationTests(unittest.TestCase):
    def test_fixtures_materialize_exact_bytes_with_balance_and_dispatch(self):
        from simai_a2a.schedulers.simai_plan import prepare,materialize
        from simai_a2a.schedulers.resource_budget import plan
        from simai_a2a.delivery import validate_fast_delivery
        from simai_a2a.leafspine import fixed_routes,server_snapshot
        for name in ('independent-wan','shared-wan','handoff'):
            matrix,hw,top=smoke_module().fixture(name)
            snapshot=server_snapshot(top,hw,fixed_routes(top));prepared=prepare(matrix,hw)
            result=plan(prepared,snapshot)
            full=materialize(prepared,result['permutations'],result['coefficients'],hw,'wan-budget')
            validate_fast_delivery(matrix,full,hw.gpus_per_server)
            self.assertTrue(any(f.phase=='balance' for f in full.flows))
            self.assertTrue(any(f.phase=='dispatch' for f in full.flows))
            self.assertTrue(any(f.phase=='intrinsic' for f in full.flows))
            self.assertEqual(result['stage_bound'],sum(v>0 for row in prepared['R'] for v in row)+sum(v>0 for v in prepared['U']))

    def test_handoff_plan_can_select_q_below_all_selected_auxiliary_edges(self):
        from simai_a2a.schedulers.simai_plan import prepare
        from simai_a2a.schedulers.resource_budget import plan
        from simai_a2a.leafspine import fixed_routes,server_snapshot
        matrix,hw,top=smoke_module().fixture('handoff');prepared=prepare(matrix,hw)
        snapshot=server_snapshot(top,hw,fixed_routes(top));result=plan(prepared,snapshot,split_guard=False)
        first=result['trace'][0];self.assertEqual(first['reason'],'handoff')
        self.assertEqual(first['q'],2*262144);self.assertEqual(first['Z_R'],0);self.assertGreater(first['Z_U'],0)
        self.assertEqual(first['witness'],(2,3))

    def test_split_is_followed_by_its_real_handoff_not_an_identical_matching(self):
        # Break caught: selecting a split for a successor which is then forgotten.
        from simai_a2a.schedulers.simai_plan import prepare
        from simai_a2a.schedulers.resource_budget import plan
        from simai_a2a.leafspine import fixed_routes,server_snapshot
        matrix,hw,top=smoke_module().fixture('handoff')
        result=plan(prepare(matrix,hw),server_snapshot(top,hw,fixed_routes(top)),split_guard=False)
        for first,second in zip(result['trace'],result['trace'][1:]):
            if first['reason']=='handoff':
                s,d=first['next_edge']
                self.assertEqual(second['p'][s],d)
                self.assertGreater(second['bytes'],0)
                self.assertNotEqual(first['p'],second['p'])

    def test_guard_rejects_split_without_nominal_same_work_improvement(self):
        from simai_a2a.schedulers.simai_plan import prepare
        from simai_a2a.schedulers.resource_budget import plan
        from simai_a2a.leafspine import fixed_routes,server_snapshot
        matrix,hw,top=smoke_module().fixture('handoff')
        result=plan(prepare(matrix,hw),server_snapshot(top,hw,fixed_routes(top)))
        self.assertFalse(any(step['reason']=='handoff' for step in result['trace']))

    def test_equal_finish_estimates_do_not_prefer_a_longer_stage_just_for_bytes(self):
        from simai_a2a.schedulers.simai_plan import prepare
        from simai_a2a.schedulers.resource_budget import plan
        from simai_a2a.leafspine import fixed_routes,server_snapshot
        matrix,hw,top=smoke_module('run_resource_budget_iterations.py').fixture('chain-near')
        result=plan(prepare(matrix,hw),server_snapshot(top,hw,fixed_routes(top)))
        self.assertAlmostEqual(result['trace'][0]['time_us'],136.88608)

    def test_real_work_does_not_lose_its_topology_anchor_to_budget_fallback(self):
        from simai_a2a.schedulers.simai_plan import prepare
        from simai_a2a.schedulers.resource_budget import plan
        from simai_a2a.leafspine import fixed_routes,server_snapshot
        matrix,hw,top=smoke_module().fixture('shared-wan')
        result=plan(prepare(matrix,hw),server_snapshot(top,hw,fixed_routes(top)))
        self.assertFalse(any(step['fallback'] and step['bytes'] for step in result['trace']))
