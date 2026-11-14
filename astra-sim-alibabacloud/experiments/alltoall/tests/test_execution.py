"""Execution projection must not confuse ideal scale-up with extra payload."""

import tempfile
import unittest
from pathlib import Path
import numpy as np

from simai_a2a.model import HardwareProfile, LinkModel, TrafficMatrix
from simai_a2a.schedulers import get_scheduler


class ExecutionTests(unittest.TestCase):
    def test_spreadout_modeled_rounds_preserve_rank_pairings_and_local_work(self):
        from simai_a2a.execution import modeled_scaleup_schedule, write_native_commands
        hw=HardwareProfile(2,2,LinkModel(10,.5),LinkModel(700,.02),'test',100,1)
        data=np.ones((4,4),dtype=np.int64)*100;np.fill_diagonal(data,0)
        full=get_scheduler('spreadout').schedule(TrafficMatrix(data),hw,seed=1)
        modeled=modeled_scaleup_schedule(full,hw)
        self.assertEqual(modeled.physical_bytes,800)
        self.assertEqual(modeled.metadata['local_times_ns']['dispatch'],[[60,60],[0,0],[60,60]])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'native.csv';write_native_commands(modeled,path)
            rows=path.read_text().splitlines()
            self.assertEqual(rows[0],'4,3,0,1')
            # At offset=1, rank0 receives from rank3, not server-aligned rank2.
            self.assertEqual(rows[1].split(',')[-1],'3')

    def test_modeled_scaleup_prices_real_local_volume_and_keeps_network_eligible(self):
        from simai_a2a.execution import modeled_scaleup_schedule, write_local_times
        from simai_a2a.schedulers import _finalize_schedule
        hw=HardwareProfile(2,2,LinkModel(10,.5),LinkModel(700,.02),'test',100,1)
        full=_finalize_schedule('fast-native-nvidia',[
            (0,1,100,0,'balance',1),
            (1,0,50,0,'balance',1),
            (2,3,20,0,'intrinsic',0),
            (0,2,200,0,'inter',0),
            (2,3,40,0,'dispatch',0)],hw,None,
            {'n_stages':1,'stage_records':[{'mp':[1,0]}]})
        projected=modeled_scaleup_schedule(full,hw)
        self.assertEqual([(f.src,f.dst,f.size_bytes) for f in projected.flows],[(0,2,200)])
        self.assertEqual(projected.flows[0].start_ns,0)
        self.assertEqual(projected.metadata['local_times_ns'],
                         {'gpus':2,'balance':[60,0],'intrinsic':[0,20],'dispatch':[[0,30]]})
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'local.csv';write_local_times(projected,path)
            self.assertEqual(path.read_text().splitlines(),['2 2 1','60 0','0 20','0 30'])

    def test_modeled_runtime_uses_local_timers_without_global_barriers(self):
        import subprocess
        root=Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as tmp:
            executable=Path(tmp)/'local-test'
            built=subprocess.run(['g++','-std=c++14','-I',str(root/'astra-sim/network_frontend/ns3'),
                str(Path(__file__).parent/'cpp/native_scaleup_test.cc'),'-o',str(executable)],
                capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stderr)
            ran=subprocess.run([str(executable)],capture_output=True,text=True)
            self.assertEqual(ran.returncode,0,ran.stderr)

    def test_empty_network_keeps_nonzero_modeled_local_completion(self):
        from simai_a2a.execution import modeled_scaleup_schedule, write_local_only_events
        from simai_a2a.native_analysis import read_native_runtime
        hw=HardwareProfile(2,2,LinkModel(10,.5),LinkModel(700,.02),'test',100,1)
        data=np.zeros((4,4),dtype=np.int64);data[0,1]=100
        full=get_scheduler('fast-native-nvidia').schedule(TrafficMatrix(data),hw,seed=1)
        modeled=modeled_scaleup_schedule(full,hw)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'events.csv';write_local_only_events(modeled,path)
            result=read_native_runtime(modeled,path)
            self.assertEqual(result['end_ns'],60)
            self.assertTrue(result['local_duration_verified'])

    def test_local_only_spreadout_preserves_virtual_network_round_slots(self):
        from simai_a2a.execution import modeled_scaleup_schedule,write_local_only_events
        from simai_a2a.native_analysis import read_native_runtime
        hw=HardwareProfile(2,2,LinkModel(10,.5),LinkModel(700,.02),'test',100,1)
        data=np.zeros((4,4),dtype=np.int64);data[0,1]=100
        full=get_scheduler('spreadout').schedule(TrafficMatrix(data),hw,seed=1)
        modeled=modeled_scaleup_schedule(full,hw)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'events.csv';write_local_only_events(modeled,path)
            result=read_native_runtime(modeled,path)
            self.assertEqual(result['end_ns'],60)
            self.assertTrue(result['local_duration_verified'])

    def test_native_commands_preserve_rank_streams_and_virtual_stage_slots(self):
        from simai_a2a.execution import native_dependency_schedule,write_native_commands
        from simai_a2a.model import Schedule,ScheduledFlow
        hw=HardwareProfile(2,1,LinkModel(0,0),LinkModel(0,.02),'test',100,1)
        plan=Schedule('fast-native-nvidia',[ScheduledFlow(0,0,1,100,0,'inter',0),
                      ScheduledFlow(1,1,0,200,0,'inter',2)],None,
                      {'n_stages':3,'stage_records':[{'mp':[1,0]}]*3})
        native=native_dependency_schedule(plan,hw)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'native.csv';write_native_commands(native,path)
            self.assertEqual(path.read_text().splitlines(),[
                '2,3','0,0,0,-1,1','1,0,-1,0,0','0,1,-1,-1,1',
                '1,1,-1,-1,0','0,2,-1,1,1','1,2,1,-1,0'])

    def test_observed_stage_overlap_is_rejected(self):
        from simai_a2a.execution import stage_barrier_statistics
        from simai_a2a.model import Schedule, ScheduledFlow
        plan = Schedule('fast', [ScheduledFlow(0, 0, 1, 10, 0, 'inter', 0),
                                 ScheduledFlow(1, 0, 1, 10, 0, 'inter', 3)], None)
        with self.assertRaisesRegex(ValueError, 'barrier violated'):
            stage_barrier_statistics(plan, {0: {'actual_start_ns': 10, 'fct_ns': 100},
                                           1: {'actual_start_ns': 50, 'fct_ns': 10}})
        result = stage_barrier_statistics(plan, {0: {'actual_start_ns': 10, 'fct_ns': 100},
                                                1: {'actual_start_ns': 120, 'fct_ns': 10}})
        self.assertEqual(result['active_stages'], 2)
        self.assertEqual(result['interstage_release_gap_ns'], 10)

    def test_projection_retains_only_real_inter_bytes_and_zero_release_offsets(self):
        from simai_a2a.execution import ideal_scaleup_schedule, write_stage_file
        data = np.arange(16, dtype=np.int64).reshape(4, 4) * 10000
        np.fill_diagonal(data, 0)
        hw = HardwareProfile(2, 2, LinkModel(10, .001), LinkModel(100, .02), 'test', 400, 1)
        full = get_scheduler('fast').schedule(TrafficMatrix(data), hw, seed=1)
        projected = ideal_scaleup_schedule(full, hw)
        self.assertEqual(projected.physical_bytes,
                         int(data[:2, 2:].sum() + data[2:, :2].sum()))
        self.assertTrue(all(f.phase == 'inter' and f.start_ns == 0 for f in projected.flows))
        self.assertEqual([(f.src, f.dst, f.size_bytes, f.stage_id) for f in projected.flows],
                         [(f.src, f.dst, f.size_bytes, f.stage_id) for f in full.flows if f.phase == 'inter'])
        self.assertIsNone(projected.predicted_makespan_ns)
        self.assertEqual(projected.metadata['execution_model'], 'stage-barrier-ideal-scaleup')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'stages.csv'
            write_stage_file(projected, path)
            self.assertEqual(path.read_text().splitlines(),
                             [f'{f.flow_id},{f.stage_id}' for f in projected.flows])

    def test_local_only_projection_has_zero_network_work(self):
        from simai_a2a.execution import ideal_scaleup_schedule
        data = np.zeros((4, 4), dtype=np.int64)
        data[0, 1] = 12345
        hw = HardwareProfile(2, 2, LinkModel(10, .001), LinkModel(100, .02), 'test', 400, 1)
        full = get_scheduler('fast').schedule(TrafficMatrix(data), hw, seed=1)
        projected = ideal_scaleup_schedule(full, hw)
        self.assertEqual(projected.flows, [])
        self.assertEqual(projected.physical_bytes, 0)


if __name__ == '__main__':
    unittest.main()
