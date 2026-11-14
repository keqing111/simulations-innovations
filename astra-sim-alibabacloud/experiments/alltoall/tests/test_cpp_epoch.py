"""Compiled epoch policies compared against independent CPU replay and payload."""

import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
import hashlib
import numpy as np

from simai_a2a.model import HardwareProfile,LinkModel,TrafficMatrix
from simai_a2a.paper_model import PaperTopology
from simai_a2a.schedulers import fast_port as native


class CompiledEpochTests(unittest.TestCase):
    def test_loaded_kernel_bytes_are_recorded_and_modified_cache_is_rejected(self):
        from simai_a2a.schedulers import cpp_epoch
        with tempfile.TemporaryDirectory() as tmp,patch.object(cpp_epoch,'CACHE_ROOT',Path(tmp),create=True):
            library,digest=cpp_epoch.compile_kernel()
            binary=Path(tmp)/digest/'kernel.so'
            self.assertEqual(library.kernel_binary_sha256,hashlib.sha256(binary.read_bytes()).hexdigest())
            # Atomic cache replacement preserves the inode already mapped by dlopen.
            # Truncating a live ELF in place can itself corrupt runtime .bss pages.
            replacement=binary.with_name('changed.so')
            replacement.write_bytes(binary.read_bytes()+b'cache-mutation');replacement.replace(binary)
            with self.assertRaisesRegex(RuntimeError,'digest mismatch'):cpp_epoch.compile_kernel()

    def test_build_flags_are_part_of_kernel_cache_identity(self):
        from simai_a2a.schedulers import cpp_epoch
        before=cpp_epoch.compile_kernel()[1]
        flags=getattr(cpp_epoch,'BUILD_FLAGS',('-std=c++17','-O3','-DNOLOG','-shared','-fPIC'))
        with tempfile.TemporaryDirectory() as tmp,patch.object(cpp_epoch,'CACHE_ROOT',Path(tmp),create=True),\
                patch.object(cpp_epoch,'BUILD_FLAGS',(*flags,'-DFAST_CACHE_ID_TEST=1'),create=True):
            after=cpp_epoch.compile_kernel()[1]
        self.assertNotEqual(before,after)

    def fixture(self,seed=8,n=4,g=2,small=False):
        d=np.random.default_rng(seed).integers(0 if small else 100000,20 if small else 300000,
                                              (n*g,n*g),dtype=np.int64)
        np.fill_diagonal(d,0)
        h=HardwareProfile(n,g,LinkModel(100,.0023),LinkModel(700,.02),'compiled-test',100,1)
        p=PaperTopology(tuple(i//2 for i in range(n)),400,100,1600,5003000)
        return TrafficMatrix(d),h,p

    def run_compiled(self,matrix,h,p,mode):
        from simai_a2a.schedulers.cpp_epoch import synthesize
        stats={}
        def callback(app):
            sets,result=synthesize(app,h,p,mode);stats.update(result);return sets
        result=native.run_fast_full(matrix.values,h.num_servers,h.gpus_per_server,'ETHER400','H100',
            MBpu=1e-6,decomposition=callback,
            inter_override={'alpha':h.inter.alpha_ns/1000,'beta':h.inter.beta_ns_per_byte*1000},
            intra_override={'alpha':h.intra.alpha_ns/1000,'beta':h.intra.beta_ns_per_byte*1000})
        return result,stats

    def independent_core_cost(self,result,h,p):
        g,n=h.gpus_per_server,h.num_servers;gs=result['gs']
        local=lambda a:native.spread_out(a,g,gs.intra_info,1e-6)*1000
        total=0.
        for k,ps in enumerate(result['p_sets']):
            volume=np.zeros((n,n),dtype=np.int64)
            for s,d in enumerate(ps['mp']):
                if s!=d:volume[s,d]=sum(gs.last_inter_rows[k,s])
            prior=max(local(x.intrinsic_all2all) for x in result['locals']) if k==0 else max(
                local(gs.last_ds[k-1,s]) for s in range(n))
            total+=max(np.ceil(p.stage(volume)['duration_ns']),prior)
        if result['p_sets']:total+=max(local(gs.last_ds[len(result['p_sets'])-1,s]) for s in range(n))
        return total

    def test_native_calls_original_cpu_source_and_keeps_matching_order(self):
        for seed in range(3):
            matrix,h,p=self.fixture(seed)
            old=native.run_fast_full(matrix.values,4,2,'ETHER400','H100',MBpu=1e-6,
                inter_override={'alpha':.7,'beta':20},intra_override={'alpha':.1,'beta':2.3})
            new,stats=self.run_compiled(matrix,h,p,'native')
            self.assertEqual(new['p_sets'],old['p_sets'])
            self.assertGreater(stats['kernel_ns'],0)

    def test_compiled_maxmin_matches_existing_python_reference(self):
        from simai_a2a.schedulers.paper_fast import decompose_policy
        matrix,h,p=self.fixture()
        def callback(app):return decompose_policy(app,app.real_gpu_remaining,2,p,'quantum',{})
        old=native.run_fast_full(matrix.values,4,2,'ETHER400','H100',MBpu=1e-6,
            decomposition=callback,inter_override={'alpha':.7,'beta':20},intra_override={'alpha':.1,'beta':2.3})
        new,_=self.run_compiled(matrix,h,p,'maxmin')
        self.assertEqual(old['p_sets'],new['p_sets'])

    def test_partition_coefficients_cover_real_service_without_claiming_same_padding(self):
        matrix,h,p=self.fixture(seed=13)
        result,_=self.run_compiled(matrix,h,p,'partition')
        service=np.zeros((4,4),dtype=np.int64)
        for stage in result['p_sets']:
            self.assertEqual(sorted(stage['mp']),[0,1,2,3])
            for s,d in enumerate(stage['mp']):service[s,d]+=stage['freq']
        self.assertTrue(np.all(service>=result['server_mat']))
        self.assertTrue(all(not np.any(l.balanced_data[:,:l.server_id*2]) and
            not np.any(l.balanced_data[:,(l.server_id+1)*2:]) for l in result['locals']))

    def test_lan_virtual_completion_cannot_release_real_wan_traffic(self):
        _,h,p=self.fixture(n=6,g=1)
        d=np.zeros((6,6),dtype=np.int64)
        for s,(local,remote,amount) in enumerate(zip([1,0,3,2,5,4],[3,2,5,4,1,0],[5,1,3,2,100,50])):
            d[s,local]=amount;d[s,remote]=200
        result,_=self.run_compiled(TrafficMatrix(d),h,p,'partition')
        wan_started=False
        for k,ps in enumerate(result['p_sets']):
            lan=wan=0
            for s,dst in enumerate(ps['mp']):
                if s==dst:continue
                amount=sum(result['gs'].last_inter_rows[k,s])
                if p.dc_ids[s]==p.dc_ids[dst]:lan+=amount
                else:wan+=amount
            self.assertFalse(lan and wan,'LAN-class padding caused a real cross-class transmission')
            self.assertFalse(wan_started and lan,'LAN demand survived into WAN class')
            wan_started|=wan>0

    def test_compiled_pipeline_cost_matches_independent_actual_gpu_dispatch(self):
        for small in [False,True]:
            matrix,h,p=self.fixture(seed=0,small=small)
            costs=[]
            for mode in ['native','maxmin','partition','adaptive']:
                result,stats=self.run_compiled(matrix,h,p,mode)
                measured=self.independent_core_cost(result,h,p)
                self.assertAlmostEqual(stats['selected_cost_ns'],measured,delta=1e-3)
                if mode!='adaptive':costs.append(measured)
                else:self.assertLessEqual(measured,min(costs)+1e-3)

    def test_empty_classes_and_invalid_modes_are_handled_before_native_calls(self):
        from simai_a2a.schedulers.cpp_epoch import synthesize
        matrix,h,p=self.fixture()
        app=native.FastAll2All(np.zeros((4,4),dtype=np.int64),2,'ETHER400','H100')
        app.real_gpu_contents=np.zeros((8,8),dtype=np.int64)
        stages,stats=synthesize(app,h,p,'adaptive')
        self.assertEqual(stages,[]);self.assertEqual(stats['selected_policy'],'native')
        with self.assertRaises(ValueError):synthesize(app,h,p,'invalid')


class CompiledPublicTests(unittest.TestCase):
    def test_registry_plans_preserve_payload_and_compiled_costs(self):
        from simai_a2a.schedulers import get_scheduler
        from simai_a2a.delivery import validate_fast_delivery
        fixture=CompiledEpochTests();matrix,h,p=fixture.fixture(n=6,g=4)
        costs=[]
        for name in ['fast-cpp-native','fast-cpp-maxmin','fast-cpp-partition','fast-cpp-adaptive']:
            plan=get_scheduler(name,fast_options=p.to_dict()).schedule(matrix,h,seed=101)
            self.assertEqual(validate_fast_delivery(matrix,plan,h.gpus_per_server)['logical_bytes_delivered'],matrix.logical_bytes)
            meta=plan.metadata
            self.assertAlmostEqual(meta['cpp_epoch']['selected_cost_ns']+meta['balance_ns'],meta['analytical_makespan_ns'],delta=1e-3)
            if name!='fast-cpp-adaptive':costs.append(plan.predicted_makespan_ns)
            else:self.assertLessEqual(plan.predicted_makespan_ns,min(costs)+1)

    def test_known_baselines_keep_python_reference_plans_exact(self):
        from simai_a2a.schedulers import get_scheduler
        matrix,h,p=CompiledEpochTests().fixture()
        for old,new in [('fast-paper','fast-cpp-native'),('fast-paper-quantum','fast-cpp-maxmin')]:
            a=get_scheduler(old,fast_options=p.to_dict()).schedule(matrix,h,seed=101)
            b=get_scheduler(new,fast_options=p.to_dict()).schedule(matrix,h,seed=101)
            self.assertEqual(a.predicted_makespan_ns,b.predicted_makespan_ns)
            self.assertEqual(a.flows,b.flows)


class LatencyRegionTests(unittest.TestCase):
    def plan(self,name,stages):
        from simai_a2a.model import Schedule
        return Schedule(name,[],None,dict(balance_ns=0,final_redistribution_ns=0,
             paper_model={'lan_alpha_ns':0,'wan_alpha_ns':100},
             paper_stages=[dict(wan_bytes=1,serialization_ns=tau,scaleup_overlap_ns=prior,
                                 duration_ns=int(np.ceil(100+tau))) for tau,prior in stages]))

    def test_exact_model_boundary_and_native_tie(self):
        from simai_a2a.latency_regions import phase_regions
        a=self.plan('native',[(10,0)]);b=self.plan('partition',[(1,0),(1,0)])
        result=phase_regions([a,b],0,20)
        self.assertEqual([r['winner'] for r in result['regions']],['partition','native'])
        crossing=next(r for r in result['boundaries'] if abs(r['alpha_ns']-8)<1e-8)
        self.assertEqual(crossing['winner'],'native')

    def test_scaleup_hinge_changes_slope_and_preserves_correct_winner(self):
        from simai_a2a.latency_regions import phase_regions,curve_time
        a=self.plan('native',[(10,100)]);b=self.plan('partition',[(30,0)])
        result=phase_regions([a,b],0,200)
        self.assertEqual([r['winner'] for r in result['regions']],['partition','native','native'])
        self.assertEqual([r['alpha_ns'] for r in result['boundaries']],[0,70,90,200])
        self.assertEqual(curve_time(a,50),100)
        self.assertEqual(curve_time(a,100),110)

    def test_integer_alpha_extension_accounts_for_stage_rounding(self):
        from simai_a2a.latency_regions import phase_regions,curve_time
        a=self.plan('native',[(10.2,0)]);b=self.plan('partition',[(1.2,0),(1.2,0)])
        result=phase_regions([a,b],0,20)
        self.assertIn(7,[r['alpha_ns'] for r in result['boundaries']])
        self.assertEqual(curve_time(a,8),19)

    def test_invalid_latency_domain_is_rejected(self):
        from simai_a2a.latency_regions import phase_regions
        p=self.plan('native',[(10,0)])
        for lower,upper in [(-1,10),(10,10),(0,float('nan'))]:
            with self.assertRaises(ValueError):phase_regions([p],lower,upper)

    def test_actual_compiled_selection_matches_fixed_plan_curves(self):
        from simai_a2a.schedulers import get_scheduler
        from simai_a2a.latency_regions import curve_time
        matrix,h,p=CompiledEpochTests().fixture()
        names=['fast-cpp-native','fast-cpp-maxmin','fast-cpp-partition']
        plans=[get_scheduler(name,fast_options=p.to_dict()).schedule(matrix,h,seed=101) for name in names]
        for alpha in [1600,53000,5003000]:
            options={**p.to_dict(),'wan_alpha_ns':alpha}
            chosen=get_scheduler('fast-cpp-adaptive',fast_options=options).schedule(matrix,h,seed=101)
            costs=[curve_time(plan,alpha) for plan in plans]
            self.assertAlmostEqual(chosen.predicted_makespan_ns,min(costs),delta=1)
            self.assertEqual(chosen.metadata['cpp_epoch']['selected_policy'],['native','maxmin','partition'][int(np.argmin(costs))])

    def test_no_scaleout_curve_keeps_local_only_cost_constant(self):
        from simai_a2a.schedulers import get_scheduler
        from simai_a2a.latency_regions import curve_time
        _,h,p=CompiledEpochTests().fixture()
        d=np.zeros((8,8),dtype=np.int64);d[0,1]=100
        plan=get_scheduler('fast-cpp-adaptive',fast_options=p.to_dict()).schedule(TrafficMatrix(d),h,seed=101)
        self.assertEqual(curve_time(plan,5003000),plan.predicted_makespan_ns)
