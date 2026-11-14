import unittest
from unittest.mock import patch
import numpy as np
from simai_a2a.model import HardwareProfile,LinkModel,TrafficMatrix
from simai_a2a.delivery import validate_fast_delivery


class V2PlanTests(unittest.TestCase):
    def test_independent_preprocessing_and_replay_deliver_unaligned_bytes(self):
        from simai_a2a.schedulers.simai_plan import prepare,reference,materialize
        data=np.arange(144,dtype=np.int64).reshape(12,12);np.fill_diagonal(data,0)
        matrix=TrafficMatrix(data);hw=HardwareProfile(3,4,LinkModel(100,.0023),LinkModel(700,.02),'v2',400,1)
        with patch('subprocess.run',side_effect=AssertionError('native host must not run')):
            prepared=prepare(matrix,hw);raw=reference(prepared)
            full=materialize(prepared,raw['permutations'],[s['alpha'] for s in raw['steps']],hw,'fast-simai')
        validate_fast_delivery(matrix,full,4)
        R=prepared['R'];self.assertEqual(len({sum(row) for row in R}),1)
        self.assertEqual([sum(row) for row in R],[sum(col) for col in zip(*R)])
        self.assertTrue(any(f.phase=='balance' for f in full.flows))
        self.assertTrue(full.metadata['delivery_chunks'])
        from simai_a2a.execution import modeled_scaleup_schedule
        projected=modeled_scaleup_schedule(full,hw)
        for flow in projected.flows:
            self.assertEqual(sum(c[2] for c in projected.metadata['delivery_chunks'][str(flow.flow_id)]),flow.size_bytes)
        self.assertTrue(projected.metadata['local_delivery_records'])

    def test_four_frozen_policies_use_standalone_state_and_deliver(self):
        from simai_a2a.schedulers.simai_plan import schedule
        hw=HardwareProfile(3,4,LinkModel(100,.0023),LinkModel(700,.02),'v2',400,1)
        data=np.arange(144,dtype=np.int64).reshape(12,12)*4096;np.fill_diagonal(data,0);matrix=TrafficMatrix(data)
        top=dict(n=3,g=4,resource_names=[str(i) for i in range(6)],bandwidth_gbps=[400]*6,
            server_paths=[dict(resource_ids=[s,3+d] if s!=d else [],propagation_us=1.4 if s!=d else 0.)
                          for s in range(3) for d in range(3)])
        with patch('subprocess.run',side_effect=AssertionError('native host must not run')):
            for objective in ('stage','capacity'):
                for budget in (1,4):
                    full=schedule(matrix,hw,f'fast-joint-{objective}-b{budget}',top,[0,0,1])
                    validate_fast_delivery(matrix,full,4)

    def test_more_than_ten_servers_and_local_only_do_not_use_native_buffers(self):
        from simai_a2a.schedulers.simai_plan import prepare,reference,materialize
        hw=HardwareProfile(16,4,LinkModel(100,.0023),LinkModel(700,.02),'v2',400,1)
        for local in (False,True):
            data=np.ones((64,64),dtype=np.int64)*64;np.fill_diagonal(data,0)
            if local:data[np.arange(64)[:,None]//4!=np.arange(64)[None,:]//4]=0
            matrix=TrafficMatrix(data);p=prepare(matrix,hw);raw=reference(p)
            full=materialize(p,raw['permutations'],[s['alpha'] for s in raw['steps']],hw,'fast-simai')
            validate_fast_delivery(matrix,full,4)
            self.assertEqual(len(raw['steps']),0 if local else 15)

