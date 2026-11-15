"""Common baseline and supplemental study contracts, independent of timing gains."""
import importlib.util
from pathlib import Path
import sys
import unittest
import numpy as np

from simai_a2a.model import TrafficMatrix,HardwareProfile,LinkModel
from simai_a2a.execution import modeled_scaleup_schedule,write_native_commands
import tempfile


def load_driver():
    folder=Path(__file__).resolve().parents[4]/'research/resource_a2av'
    sys.path.insert(0,str(folder))
    spec=importlib.util.spec_from_file_location('supplement_test',folder/'paper_supplement.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def load_results():
    folder=Path(__file__).resolve().parents[4]/'research/resource_a2av'
    sys.path.insert(0,str(folder))
    spec=importlib.util.spec_from_file_location('supplement_results_test',folder/'supplement_results.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


class PairwiseTests(unittest.TestCase):
    def scheduler(self):
        from simai_a2a.schedulers.pairwise import PairwiseScheduler
        return PairwiseScheduler()

    def test_every_real_pair_once_and_inverse_peer_with_sparse_rounds(self):
        hw=HardwareProfile(2,2,LinkModel(100,.0023),LinkModel(700,.02),'test',400,1)
        values=np.array([[0,7,0,11],[13,0,17,0],[0,19,0,23],[29,0,31,0]],dtype=np.int64)
        matrix=TrafficMatrix(values)
        full=self.scheduler().schedule(matrix,hw,seed=1)
        self.assertEqual(sorted((f.src,f.dst,f.size_bytes) for f in full.flows),
            [(0,1,7),(0,3,11),(1,0,13),(1,2,17),(2,1,19),(2,3,23),(3,0,29),(3,2,31)])
        self.assertEqual(full.metadata['stage_records'],[
            {'rank_mp':[1,0,3,2]},{'rank_mp':[2,3,0,1]},{'rank_mp':[3,2,1,0]}])
        projected=modeled_scaleup_schedule(full,hw)
        self.assertEqual(projected.physical_bytes,76)
        self.assertGreater(sum(sum(row) for row in projected.metadata['local_times_ns']['dispatch']),0)
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'commands.csv';write_native_commands(projected,file)
            lines=file.read_text().splitlines()
            self.assertEqual(lines[0],'4,3,0,1')
            self.assertEqual([int(line.split(',')[-1]) for line in lines[1:5]],[1,0,3,2])

    def test_power_of_two_requirement_is_explicit(self):
        hw=HardwareProfile(3,1,LinkModel(100,.0023),LinkModel(700,.02),'test',400,1)
        with self.assertRaisesRegex(ValueError,'power of two'):
            self.scheduler().schedule(TrafficMatrix(np.zeros((3,3),dtype=np.int64)),hw,seed=1)


class SupplementTests(unittest.TestCase):
    def test_grid_has_all_methods_shared_inputs_and_no_duplicate_conditions(self):
        m=load_driver();cases=m.cases()
        self.assertEqual(len(cases),52)
        self.assertEqual([sum(c['block']==b for c in cases) for b in ['real','synthetic','mechanism']],[24,22,6])
        self.assertEqual(len({c['name'] for c in cases}),52)
        self.assertEqual(set(m.METHODS),{'fast-simai','spreadout','pairwise','volume-maxq','resource-maxq'})
        paired=[c for c in cases if c['block']=='synthetic' and c['family']=='zipf08' and c['mib']==4]
        self.assertEqual(len(paired),2)
        self.assertTrue(np.array_equal(m.demand(paired[0],None).values,m.demand(paired[1],None).values))

    def test_combine_is_exact_transpose_and_does_not_mutate_dispatch(self):
        m=load_driver();values=np.array([[0,1,3],[5,0,7],[11,13,0]],dtype=np.int64)
        dispatch=TrafficMatrix(values,dict(generator='literal'))
        combine=m.as_direction(dispatch,'combine')
        self.assertEqual(combine.values.tolist(),[[0,5,11],[1,0,13],[3,7,0]])
        self.assertEqual(combine.logical_bytes,40)
        self.assertEqual(dispatch.values.tolist(),[[0,1,3],[5,0,7],[11,13,0]])

    def test_relabeling_preserves_racks_and_two_round_auxiliary_feasibility(self):
        m=load_driver()
        from simai_a2a.schedulers.simai_plan import prepare,materialize
        from simai_a2a.schedulers.resource_budget import anchored_cap
        from simai_a2a.delivery import validate_fast_delivery
        for c in m.cases():
            if c['block']!='mechanism':continue
            matrix=m.demand(c,None);_,hw=m.e.fabric('multi-cut',64);pre=prepare(matrix,hw)
            self.assertEqual(matrix.logical_bytes,48*c['quantum_bytes'])
            self.assertEqual(anchored_cap(pre['R'])[0],c['quantum_bytes'])
            self.assertEqual(sorted(sum(row) for row in pre['R']),[2*c['quantum_bytes']]*16)
            plan=m.e.iterations.volume_plan(pre,m.e.server_snapshot(*m.e.fabric('multi-cut',64),m.e.fixed_routes(m.e.fabric('multi-cut',64)[0])))
            self.assertEqual(plan['coefficients'],[c['quantum_bytes']]*2)
            validate_fast_delivery(matrix,materialize(pre,plan['permutations'],plan['coefficients'],hw,'test'),4)

    def test_summary_keeps_failure_denominator_and_exact_win_tie_loss(self):
        m=load_results();cases=[dict(name=str(i),block='real') for i in range(4)];rows=[]
        for i,(base,ours) in enumerate([(8,4),(3,3),(5,10),(None,2)]):
            for method,value in [('volume-maxq',base),('resource-maxq',ours)]:
                rows.append(dict(case=str(i),method=method,success=value is not None,
                    actual_ms=value,pair_eligible=value is not None,status='success' if value is not None else 'timeout'))
        summary=m.summarize(dict(cases=cases,runs=rows,methods=['volume-maxq','resource-maxq']))
        result=summary['real']['volume-maxq']
        self.assertEqual((result['paired'],result['unpaired']),(3,1))
        self.assertEqual((result['wins'],result['ties'],result['losses']),(1,1,1))
        self.assertAlmostEqual(result['geomean_speedup'],1)
        self.assertEqual(summary['status_counts'],{'success':7,'timeout':1})

    def test_two_window_bar_does_not_silently_average_only_a_successful_window(self):
        m=load_results()
        self.assertEqual(m.complete_range([3,5]),(4,1,1))
        self.assertTrue(np.isnan(m.complete_range([3,None])[0]))
        self.assertTrue(np.isnan(m.complete_range([])[0]))

    def test_workload_accounting_distinguishes_intrinsic_and_real_balance(self):
        m=load_results();hw=HardwareProfile(2,2,LinkModel(100,.0023),LinkModel(700,.02),'test',400,1)
        matrix=TrafficMatrix(np.array([[0,3,0,5],[7,0,11,0],[0,13,0,17],[19,0,23,0]],dtype=np.int64))
        metrics=m.workload_metrics(matrix,hw)
        self.assertEqual(metrics['logical_bytes'],98)
        self.assertEqual(metrics['inter_server_bytes'],48)
        self.assertEqual(metrics['balance_bytes'],6)
        self.assertEqual(metrics['intrinsic_bytes'],50)
