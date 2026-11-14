"""Paper CPU epoch semantics, shared cuts and bounded matching policies."""

import unittest
import numpy as np


class PaperModelTests(unittest.TestCase):
    def topology(self, **kwargs):
        from simai_a2a.paper_model import PaperTopology
        return PaperTopology(**kwargs)

    def test_shared_wan_counts_all_simultaneous_server_flows(self):
        p=self.topology(dc_ids=(0,0,1,1),server_gbps=400,wan_gbps=100,
                        lan_alpha_ns=2,wan_alpha_ns=10)
        d=np.zeros((4,4),dtype=np.int64);d[0,2]=100;d[1,3]=100
        r=p.stage(d)
        self.assertEqual(r['serialization_ns'],16)
        self.assertEqual(r['duration_ns'],26)
        self.assertEqual(p.lower_bound(d),16)

    def test_gateway_capacity_shared_across_different_wan_destinations(self):
        p=self.topology(dc_ids=(0,0,1,2),server_gbps=1000,wan_gbps=80,
                        lan_alpha_ns=0,wan_alpha_ns=0,gateway_gbps=8)
        d=np.zeros((4,4),dtype=np.int64);d[0,2]=10;d[1,3]=10
        self.assertEqual(p.stage(d)['duration_ns'],20)
        self.assertIn('gateway_out:0',p.stage(d)['bottlenecks'])

    def test_two_stage_fixed_overhead_crossover_is_not_wan_coverage(self):
        p=self.topology(dc_ids=(0,0,1,1),server_gbps=8,wan_gbps=4,
                        lan_alpha_ns=2,wan_alpha_ns=202)
        def cost(mapping,b):
            d=np.zeros((4,4),dtype=np.int64)
            for i,j in enumerate(mapping):d[i,j]=b
            return p.stage(d)['duration_ns']
        for b,native,mixed in [(100,704,804),(200,1204,1204),(400,2204,2004)]:
            with self.subTest(bytes=b):
                self.assertEqual(cost([1,0,3,2],b)+cost([2,3,0,1],b),native)
                self.assertEqual(cost([2,0,3,1],b)+cost([1,3,0,2],b),mixed)

    def test_zero_and_virtual_demand_has_no_startup_or_wan_work(self):
        p=self.topology(dc_ids=(0,1),server_gbps=400,wan_gbps=100,
                        lan_alpha_ns=2,wan_alpha_ns=10)
        self.assertEqual(p.stage(np.zeros((2,2),dtype=np.int64))['duration_ns'],0)

    def test_invalid_profile_or_matrix_is_rejected(self):
        for extra in [dict(wan_gbps=0),dict(server_gbps=float('nan')),
                      dict(lan_alpha_ns=-1),dict(dc_ids=(0,-1))]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                self.topology(**{**dict(dc_ids=(0,1),server_gbps=400,wan_gbps=100,
                             lan_alpha_ns=2,wan_alpha_ns=10),**extra})
        p=self.topology(dc_ids=(0,1),server_gbps=400,wan_gbps=100,
                        lan_alpha_ns=2,wan_alpha_ns=10)
        with self.assertRaises(ValueError):p.stage(np.array([[0,-1],[0,0]],dtype=np.int64))

    def test_opt_in_cpu_decomposition_keeps_default_result_identical(self):
        from simai_a2a.schedulers.fast_port import run_fast_full
        d=np.array([[0,2,4],[4,0,1],[2,1,0]],dtype=np.int64)
        a=run_fast_full(d,3,1,'ETHER100','H100',MBpu=1e-6)
        def decompose(app):return app.decompose()
        b=run_fast_full(d,3,1,'ETHER100','H100',MBpu=1e-6,decomposition=decompose)
        self.assertEqual(a['p_sets'],b['p_sets'])
        self.assertEqual(a['t'],b['t'])


class PaperPolicyTests(unittest.TestCase):
    def fixture(self,n=4,g=2):
        from simai_a2a.model import TrafficMatrix,HardwareProfile,LinkModel
        from simai_a2a.paper_model import PaperTopology
        data=np.random.default_rng(8).integers(100000,300000,(n*g,n*g),dtype=np.int64)
        np.fill_diagonal(data,0)
        return (TrafficMatrix(data),HardwareProfile(n,g,LinkModel(100,.0023),
                LinkModel(700,.08),'paper-test',100,1),
                PaperTopology(tuple(i//2 for i in range(n)),400,100,1600,5003000))

    def test_large_quantum_finds_maximum_feasible_minimum_without_enumeration(self):
        from simai_a2a.schedulers.paper_fast import largest_quantum_matching
        match,calls=largest_quantum_matching(np.array([[5,1],[1,5]]),[[1,0],[0,1]])
        self.assertEqual(match,[0,1]);self.assertLessEqual(calls,3)

    def test_all_candidate_plans_deliver_same_original_gpu_payload(self):
        from simai_a2a.schedulers.paper_fast import paper_schedules
        from simai_a2a.delivery import validate_fast_delivery
        matrix,hw,p=self.fixture()
        plans=paper_schedules(matrix,hw,p,seed=11)
        self.assertEqual(len(plans),6)
        for name,plan in plans.items():
            with self.subTest(name=name):
                proof=validate_fast_delivery(matrix,plan,hw.gpus_per_server)
                self.assertEqual(proof['logical_bytes_delivered'],matrix.logical_bytes)
                self.assertLessEqual(plan.metadata['n_stages'],hw.num_servers**2)
                self.assertTrue(all(sorted(r['mp'])==list(range(hw.num_servers))
                                    for r in plan.metadata['stage_records']))

    def test_homogeneous_native_pipeline_matches_existing_cpu_simulator(self):
        from simai_a2a.schedulers.paper_fast import paper_schedules
        from simai_a2a.schedulers.fast import FastScheduler
        from simai_a2a.paper_model import PaperTopology
        matrix,hw,_=self.fixture()
        p=PaperTopology((0,0,0,0),200,100,700,700)
        old=FastScheduler().schedule(matrix,hw,seed=11)
        new=paper_schedules(matrix,hw,p,seed=11)['fast-paper']
        self.assertAlmostEqual(new.predicted_makespan_ns,old.predicted_makespan_ns,delta=20)
        self.assertEqual([r['mp'] for r in new.metadata['stage_records']],
                         [r['mp'] for r in old.metadata['stage_records']])

    def test_adaptive_selects_minimum_full_pipeline_including_native(self):
        from simai_a2a.schedulers.paper_fast import paper_schedules
        matrix,hw,p=self.fixture()
        plans=paper_schedules(matrix,hw,p,seed=11)
        selected=plans['fast-paper-adaptive']
        self.assertEqual(selected.predicted_makespan_ns,min(
            plan.predicted_makespan_ns for key,plan in plans.items() if key!='fast-paper-adaptive'))
        self.assertGreaterEqual(selected.metadata['synthesis_ns'],sum(
            plan.metadata['synthesis_ns'] for key,plan in plans.items() if key!='fast-paper-adaptive'))
        self.assertEqual(selected.metadata['matching_calls'],sum(
            plan.metadata['matching_calls'] for key,plan in plans.items() if key!='fast-paper-adaptive'))

    def test_amortization_prefers_more_real_payload_per_stage_cost(self):
        from simai_a2a.schedulers.paper_fast import efficient_matching
        from simai_a2a.paper_model import PaperTopology
        residual=np.array([[0,100,400,0],[100,0,0,400],[400,0,0,100],[0,400,100,0]])
        p=PaperTopology((0,0,1,1),8,4,2,1000)
        self.assertEqual(efficient_matching(residual,residual,1,p,
                         [[1,3,0,2],[2,3,0,1]]),[2,3,0,1])

    def test_amortization_does_not_count_large_virtual_quantum_as_progress(self):
        from simai_a2a.schedulers.paper_fast import efficient_matching
        from simai_a2a.paper_model import PaperTopology
        residual=np.array([[100,1],[1,100]]);real=np.array([[0,1],[0,0]])
        p=PaperTopology((0,1),8,4,2,1000)
        self.assertEqual(efficient_matching(residual,real,1,p,[[0,1],[1,0]]),[1,0])

    def test_zero_input_native_tie_and_registry_adapter(self):
        from simai_a2a.model import TrafficMatrix
        from simai_a2a.schedulers import get_scheduler
        _,hw,p=self.fixture()
        plan=get_scheduler('fast-paper-adaptive',fast_options=p.to_dict()).schedule(
            TrafficMatrix(np.zeros((8,8),dtype=np.int64)),hw,seed=11)
        self.assertEqual(plan.predicted_makespan_ns,0)
        self.assertEqual(plan.metadata['selected_policy'],'fast-paper')
        self.assertEqual(plan.flows,[])

    def test_padding_never_counted_as_wan_payload(self):
        from simai_a2a.model import TrafficMatrix
        from simai_a2a.schedulers.paper_fast import paper_schedules
        _,hw,p=self.fixture(n=4,g=1)
        d=np.zeros((4,4),dtype=np.int64);d[0,1]=100000
        for plan in paper_schedules(TrafficMatrix(d),hw,p).values():
            self.assertEqual(sum(r['wan_bytes'] for r in plan.metadata['paper_stages']),0)
            self.assertTrue(all(f.src//2==f.dst//2 for f in plan.flows))

    def test_unequal_gpu_rows_score_exactly_the_bytes_actually_dispatched(self):
        from simai_a2a.model import TrafficMatrix,HardwareProfile,LinkModel
        from simai_a2a.paper_model import PaperTopology
        from simai_a2a.schedulers.paper_fast import paper_schedules
        d=np.zeros((6,6),dtype=np.int64);d[1,2]=100;d[4,0]=50
        h=HardwareProfile(3,2,LinkModel(100,.0023),LinkModel(700,.08),'uneven',100,1)
        plans=paper_schedules(TrafficMatrix(d),h,PaperTopology((0,1,2),400,100,1600,5003000))
        for name,plan in plans.items():
            if name=='fast-paper':continue
            actual=[sum(f.size_bytes for f in plan.flows if f.phase=='inter' and f.stage_id==k)
                    for k in range(plan.metadata['n_stages'])]
            self.assertEqual(plan.metadata['decision_stage_bytes'],actual,name)
            self.assertEqual(sum(actual),150)

    def test_original_uint32_cell_boundary_checked_before_local_or_averaging_paths(self):
        from simai_a2a.model import TrafficMatrix
        from simai_a2a.schedulers.paper_fast import PaperFastScheduler
        _,hw,p=self.fixture(n=2,g=2)
        for destination in [1,2]:
            d=np.zeros((4,4),dtype=np.int64);d[0,destination]=2**32
            with self.subTest(destination=destination),self.assertRaisesRegex(ValueError,'32-bit'):
                PaperFastScheduler('fast-paper',p.to_dict()).schedule(TrafficMatrix(d),hw,seed=11)

    def test_zero_and_local_only_plans_have_delivery_proof(self):
        from simai_a2a.model import TrafficMatrix
        from simai_a2a.schedulers.paper_fast import paper_schedules
        _,hw,p=self.fixture(n=2,g=2)
        for size in [0,100]:
            d=np.zeros((4,4),dtype=np.int64);d[0,1]=size
            for plan in paper_schedules(TrafficMatrix(d),hw,p).values():
                self.assertEqual(plan.metadata['delivery_validation']['logical_bytes_delivered'],size)

    def test_policy_decomposition_reconstructs_completed_support(self):
        from simai_a2a.schedulers.paper_fast import decompose_policy
        from simai_a2a.schedulers.fast_port import FastAll2All
        _,_,p=self.fixture(n=4,g=1)
        for policy in ['wan','quantum','progress']:
            for seed in range(4):
                data=np.random.default_rng(seed).integers(0,10,(4,4),dtype=np.int64)
                np.fill_diagonal(data,0)
                app=FastAll2All(data,1,'ETHER400','H100');app.to_scaled_doubly_stochastic_matrix()
                before=app.SDS_mat.copy();stats={}
                sets=decompose_policy(app,data,1,p,policy,stats)
                actual=np.zeros((4,4),dtype=np.int64)
                for ps in sets:
                    for i,j in enumerate(ps['mp']):actual[i,j]+=ps['freq']
                np.testing.assert_array_equal(actual,before)
                self.assertFalse(np.any(app.SDS_mat))


class StudyDriverTests(unittest.TestCase):
    def module(self):
        import importlib.util
        from pathlib import Path
        path=Path(__file__).resolve().parents[1]/'scripts/fast_paper_study.py'
        spec=importlib.util.spec_from_file_location('paper_study_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_fingerprint_includes_matching_repair_and_schedule_construction_dependencies(self):
        import shutil,tempfile
        from pathlib import Path
        from unittest.mock import patch
        module=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(module.ROOT/'src',Path(tmp)/'src')
            with patch.object(module,'ROOT',Path(tmp)):
                for name in ['fast_wan.py','__init__.py']:
                    path=Path(tmp)/'src/simai_a2a/schedulers'/name
                    before=module.fingerprint();path.write_text(path.read_text()+'\n# dependency change\n')
                    self.assertNotEqual(before,module.fingerprint(),name)

    def test_old_packet_scenarios_cannot_be_combined_with_new_analytical_study(self):
        import json,tempfile
        from pathlib import Path
        module=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scenario=root/'packet/proof';scenario.mkdir(parents=True)
            (root/'packet.json').write_text(json.dumps({'proof':{'fast-paper':{'success':True,'attempt':'unused'}}}))
            (scenario/'scenario_manifest.json').write_text(json.dumps({'source_fingerprint':'old'}))
            with self.assertRaisesRegex(ValueError,'source|version'):
                module.audit_packet_sources(root,{'source_fingerprint':'new','cases':[{'name':'proof'}]})

    def test_small_study_persists_all_policies_and_valid_selected_plan(self):
        import csv,json,os,subprocess,sys,tempfile
        from pathlib import Path
        from simai_a2a.artifacts import read_matrix_artifact,read_schedule_artifact
        from simai_a2a.delivery import validate_fast_delivery
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='fast-paper-smoke-') as tmp:
            run=subprocess.run([sys.executable,str(root/'scripts/fast_paper_study.py'),
                '--phase','analytical','--smoke','--workers','1','--output',tmp],
                env={**os.environ,'PYTHONPATH':str(root/'src')},capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stderr)
            with (Path(tmp)/'analytical.csv').open() as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual(len(rows),12)
            manifest=json.loads((Path(tmp)/'study.json').read_text())
            module=self.module()
            module.audit_analytical(Path(tmp))
            first=Path(tmp)/'cases'/manifest['cases'][0]['name']/'schedules/fast-paper/schedule_manifest.json'
            original=first.read_text();bad=json.loads(original)
            bad['metadata']['study_source_fingerprint']='old'
            first.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError,'source|version'):
                module.audit_analytical(Path(tmp))
            first.write_text(original)
            for case in manifest['cases']:
                directory=Path(tmp)/'cases'/case['name']
                matrix,_=read_matrix_artifact(directory/'matrix')
                native,_=read_schedule_artifact(directory/'schedules/fast-paper')
                selected,_=read_schedule_artifact(directory/'schedules/fast-paper-adaptive')
                self.assertLessEqual(selected.predicted_makespan_ns,native.predicted_makespan_ns)
                self.assertEqual(validate_fast_delivery(matrix,selected,case['gpus'])['logical_bytes_delivered'],matrix.logical_bytes)
