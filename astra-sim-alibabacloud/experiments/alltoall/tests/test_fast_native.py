import unittest
import numpy as np

from simai_a2a.model import HardwareProfile, LinkModel, TrafficMatrix
from simai_a2a.delivery import validate_fast_delivery
from simai_a2a.schedulers import get_scheduler


class NativeFastTests(unittest.TestCase):
    def test_unsupported_native_uint_frequency_is_rejected_before_subprocess(self):
        hw=HardwareProfile(2,1,LinkModel(0,0),LinkModel(0,.02),'native',100,1)
        data=np.array([[0,4294967296],[4294967296,0]],dtype=np.int64)
        with self.assertRaisesRegex(ValueError,'32-bit'):
            get_scheduler('fast-native-nvidia').schedule(TrafficMatrix(data),hw,seed=1)

    def test_manual_witness_follows_rank_renumbering(self):
        data=np.zeros((6,6),dtype=np.int64)
        for i,(a,b) in enumerate(zip([1,0,3,2,5,4],[3,2,5,4,1,0])):
            data[i,a]=data[i,b]=1000
        permutation=[1,0,2,3,4,5]
        renamed=np.zeros_like(data);renamed[np.ix_(permutation,permutation)]=data
        hw=HardwareProfile(6,1,LinkModel(0,0),LinkModel(0,.02),'native',100,1)
        schedule=get_scheduler('fast-native-mixed',fast_options={
            'manual_server_permutation':permutation}).schedule(TrafficMatrix(renamed),hw,seed=1)
        validate_fast_delivery(TrafficMatrix(renamed),schedule,1)

    def test_native_equal_frequency_execution_is_reverse_decomposition_order(self):
        local = [1,0,3,2,5,4]
        remote = [3,2,5,4,1,0]
        data = np.zeros((6,6),dtype=np.int64)
        for i in range(6):
            data[i,local[i]] = data[i,remote[i]] = 1000
        hw = HardwareProfile(6,1,LinkModel(0,0),LinkModel(0,.02),'native',100,1)
        schedule = get_scheduler('fast-native-nvidia').schedule(TrafficMatrix(data),hw,seed=1)
        self.assertEqual([r['mp'] for r in schedule.metadata['stage_records']], [local,remote])
        self.assertEqual(schedule.physical_bytes,12000)
        validate_fast_delivery(TrafficMatrix(data),schedule,1)

    def test_original_host_code_balances_and_delivers_sparse_gpu_payload(self):
        rng=np.random.default_rng(1946)
        hw=HardwareProfile(3,2,LinkModel(0,0),LinkModel(0,.02),'native',100,1)
        for seed in range(10):
            data=rng.integers(0,10000,(6,6),dtype=np.int64)
            data[rng.random((6,6))<.5]=0
            np.fill_diagonal(data,0)
            matrix=TrafficMatrix(data)
            schedule=get_scheduler('fast-native-nvidia').schedule(matrix,hw,seed=seed)
            validate_fast_delivery(matrix,schedule,2)
            frequencies=[r['freq'] for r in schedule.metadata['stage_records']]
            self.assertEqual(frequencies,sorted(frequencies,reverse=True))

    def test_native_cost_neutral_point_matches_original_host_schedule(self):
        data=np.arange(36,dtype=np.int64).reshape(6,6)*1000
        np.fill_diagonal(data,0)
        hw=HardwareProfile(3,2,LinkModel(0,0),LinkModel(0,.02),'native',100,1)
        opts=dict(server_dc_ids=[0,0,1],intra_dc_rtt_ns=0,inter_dc_rtt_ns=0,
                  intra_dc_bandwidth_gbps=400,inter_dc_bandwidth_gbps=400)
        base=get_scheduler('fast-native-nvidia').schedule(TrafficMatrix(data),hw,seed=1)
        cost=get_scheduler('fast-native-cost',fast_options=opts).schedule(TrafficMatrix(data),hw,seed=1)
        self.assertEqual(base.flows,cost.flows)
        self.assertEqual(base.metadata['stage_records'],cost.metadata['stage_records'])


if __name__=='__main__':unittest.main()
