"""Pairwise exchange: rank XOR step, with one send and receive per round."""
from . import _finalize_schedule


class PairwiseScheduler:
    def schedule(self,matrix,hardware,*,seed):
        hardware.validate_matrix(matrix);n=matrix.num_devices
        if n&(n-1):raise ValueError('XOR pairwise requires a power of two ranks')
        raw=[];records=[]
        for step in range(1,n):
            records.append(dict(rank_mp=[rank^step for rank in range(n)]))
            for src in range(n):
                dst=src^step;amount=int(matrix.values[src,dst])
                if amount:raw.append((src,dst,amount,0,'direct',step-1))
        return _finalize_schedule('pairwise',raw,hardware,None,dict(
            seed=seed,logical_bytes=matrix.logical_bytes,physical_bytes=matrix.logical_bytes,
            n_stages=n-1,stage_records=records,native_round_protocol=True,
            baseline_scope='rank-level pairwise XOR exchange'))
