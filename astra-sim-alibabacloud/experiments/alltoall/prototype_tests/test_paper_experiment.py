"""Paper evidence preserves controls, full-cohort counts and honest idle semantics."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import json
import shutil
from unittest.mock import patch
import unittest


def load(name):
    folder=Path(__file__).resolve().parents[4]/'research/resource_a2av'
    sys.path.insert(0,str(folder))
    spec=importlib.util.spec_from_file_location(name+'_test',folder/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


class PaperExperimentTests(unittest.TestCase):
    def test_cases_do_not_duplicate_balanced_seeds_or_change_cross_topology_matrices(self):
        m=load('paper_experiment');cases=m.paper_cases()
        self.assertEqual(len(cases),24)
        self.assertEqual(sum(c['family']=='balanced' for c in cases),6)
        self.assertEqual({c['gpu_count'] for c in cases},{64})
        matrices={}
        for c in cases:
            a=m.e.synthetic(64,c['family'],c['seed'],c['mib'])
            matrices.setdefault(a.values.tobytes(),[]).append(c['topology'])
        self.assertEqual(len(matrices),8)
        self.assertTrue(all(len(v)==3 and len(set(v))==3 for v in matrices.values()))

    def test_hand_controls_same_q_two_rounds_reconstruct_exact_real_demand(self):
        m=load('paper_mechanism');matrix,top,hw,plans=m.controlled_case()
        from simai_a2a.schedulers.simai_plan import prepare,materialize
        from simai_a2a.schedulers.resource_budget import anchored_cap
        from simai_a2a.delivery import validate_fast_delivery
        prepared=prepare(matrix,hw)
        self.assertEqual(anchored_cap(prepared['R'])[0],262144)
        self.assertEqual(matrix.logical_bytes,16777216)
        for name,p in plans.items():
            self.assertEqual(p['coefficients'],[262144,262144])
            self.assertEqual(len(p['permutations']),2)
            full=materialize(prepared,p['permutations'],p['coefficients'],hw,name)
            validate_fast_delivery(matrix,full,4)

    def test_pending_idle_excludes_control_occupancy(self):
        m=load('paper_mechanism')
        packets=[dict(start_ns=2,duration_ns=2,payload_bytes=50,kind=0),
                 dict(start_ns=5,duration_ns=1,payload_bytes=0,kind=1),
                 dict(start_ns=8,duration_ns=1,payload_bytes=50,kind=0)]
        self.assertEqual(m.pending_idle(100,packets,0,12),[(0,2),(4,5),(6,8)])

    def test_shared_sender_control_is_same_q_and_legally_reconstructs(self):
        m=load('paper_mechanism');matrix,top,hw,plans=m.controlled_case('shared-senders')
        from simai_a2a.schedulers.simai_plan import prepare,materialize
        from simai_a2a.schedulers.resource_budget import anchored_cap
        from simai_a2a.delivery import validate_fast_delivery
        self.assertEqual(matrix.logical_bytes,12582912)
        prepared=prepare(matrix,hw);self.assertEqual(anchored_cap(prepared['R'])[0],262144)
        for name,p in plans.items():
            self.assertEqual(p['coefficients'],[262144,262144])
            validate_fast_delivery(matrix,materialize(prepared,p['permutations'],p['coefficients'],hw,name),4)

    def test_retransmission_can_only_reduce_reported_pending_idle(self):
        m=load('paper_mechanism')
        packets=[dict(start_ns=2,duration_ns=1,payload_bytes=50,kind=0),
                 dict(start_ns=4,duration_ns=1,payload_bytes=50,kind=0)]
        self.assertEqual(m.pending_idle(100,packets,0,12),[(0,2),(3,4)])
        self.assertEqual(m.pending_idle(0,packets,0,12),[])

    def test_cache_rejects_changed_planner_before_reusing_old_timings(self):
        m=load('paper_experiment')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);case=m.paper_cases()[0]
            (p/'study.json').write_text(json.dumps(dict(cases=[case],runs=[])))
            (p/'resource_budget.py').write_text('different planner')
            with self.assertRaisesRegex(ValueError,'planner identity'):m.reuse_case(case,p)

    def test_missing_cache_case_returns_none_not_a_fabricated_result(self):
        m=load('paper_experiment')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'study.json').write_text(json.dumps(dict(cases=[],runs=[])))
            self.assertIsNone(m.reuse_case(m.paper_cases()[0],p))

    def test_cache_rejects_changed_modeled_local_timing_even_when_pq_matches(self):
        m=load('paper_experiment');e=m.e
        from simai_a2a.artifacts import write_schedule_artifact
        from simai_a2a.scenario import canonical_sha256
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp);case=m.paper_cases()[0];source,top=e.prepare_case(old/'inputs',case)
            case['source']=str(source);matrix=e.synthetic(64,'balanced',3101,1);_,hw=e.fabric('leaf-uniform',64)
            pre=e.iterations.prepare(matrix,hw);snap=json.loads((source/'planner_topology.json').read_text())
            hardware=json.loads((source/'hardware.json').read_text());groups=list(range(16))
            groups=[s//4 for s in groups];rows=[]
            shutil.copyfile(e.EXPERIMENTS/'src/simai_a2a/schedulers/resource_budget.py',old/'resource_budget.py')
            for method in m.METHODS:
                if method=='spreadout':
                    full=e.iterations.SpreadOutScheduler().schedule(matrix,hw,seed=1)
                    full.metadata.update(delivery_chunks={str(f.flow_id):[[f.src,f.dst,f.size_bytes]] for f in full.flows},server_dc_ids=groups)
                else:
                    raw=(e.iterations.reference(pre) if method=='fast-simai' else e.iterations.volume_plan(pre,snap) if method=='volume-maxq' else e.iterations.plan(pre,snap,maximum_quantum=True))
                    qs=raw.get('coefficients',[r['alpha'] for r in raw.get('steps',[])])
                    full=e.iterations.materialize(pre,raw['permutations'],qs,hw,method,dict(server_dc_ids=groups))
                    e.dump(source/(method+'.plan.json'),raw)
                saved=e.iterations.modeled_scaleup_schedule(full,hw);attempt=source/'runs'/method/'attempt-0001'
                write_schedule_artifact(saved,attempt,matrix_sha256='fixture',hardware_sha256=canonical_sha256(hardware))
                rows.append(dict(case=case['name'],method=method,success=True,status='success',actual_ms=1,attempt_dir=str(attempt)))
            e.dump(old/'study.json',dict(cases=[case],runs=rows))
            original=e.iterations.modeled_scaleup_schedule
            def wrong_timing(full,hardware):
                result=original(full,hardware);result.metadata['local_times_ns']['balance'][0]+=1;return result
            # Only external backend authentication is stubbed; generation and
            # materialization stay real, and the changed timer is the bug trigger.
            def authenticate(row):row['input_contract']={'epoch':1}
            with patch.object(e.audit,'attach_contract',side_effect=authenticate),patch.object(e.iterations,'modeled_scaleup_schedule',side_effect=wrong_timing):
                with self.assertRaisesRegex(ValueError,'materialized schedule'):m.reuse_case(case,old)

    def test_seed_range_is_not_an_invented_balanced_replication(self):
        m=load('paper_results')
        self.assertEqual(m.sample_range([5,7,9]),(7,5,9,3))
        self.assertEqual(m.sample_range([4]),(4,4,4,1))

    def test_packet_overlap_uses_union_not_double_counted_envelopes(self):
        m=load('paper_results')
        self.assertEqual(m.overlap_ns([(0,5),(4,10)],[(3,6),(8,12)]),5)

    def test_volume_is_the_explicit_primary_speedup_denominator(self):
        m=load('paper_results')
        rows=[{'volume-maxq_ms':6,'fast-simai_ms':12,'spreadout_ms':3,'resource-maxq_ms':3}]
        self.assertEqual(m.ratios(rows,'volume-maxq'),dict(paired=1,unpaired=0,geomean=2.,wins=1,ties=0,losses=0))
        self.assertEqual(m.ratios(rows,'spreadout')['geomean'],1.)
