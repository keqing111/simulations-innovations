"""NVIDIA host scheduler source adapter, with no CUDA runtime emulation."""

import hashlib
from pathlib import Path
import subprocess
import tempfile
import threading

import numpy as np

from . import _finalize_schedule


ROOT=Path(__file__).resolve().parents[3]
NATIVE=ROOT.parents[2].parent/'FAST/nvidia/src'
_BUILD_LOCK=threading.Lock()


def native_driver():
    sources=[NATIVE/'fast_alltoall'/name for name in
             ('alltoall_matrix.cc','alltoall_algorithm.cc','alltoall_local_scheduler.cc')]
    sources += [ROOT/'tools/native_host_dump.cc',ROOT/'tools/native_host_compat.h']
    headers=list((NATIVE/'include/fast_alltoall').glob('*.h'))
    digest=hashlib.sha256(b''.join(p.read_bytes() for p in sources+sorted(headers))).hexdigest()[:16]
    target=ROOT/'results/native-host-build'/digest/'native_host_dump'
    with _BUILD_LOCK:
        if not target.is_file():
            target.parent.mkdir(parents=True,exist_ok=True)
            built=subprocess.run(['g++','-std=c++14','-O2','-DNOLOG','-I',str(NATIVE/'include'),
                 '-include',str(ROOT/'tools/native_host_compat.h'),
                 *map(str,sources[:-1]),'-o',str(target)],capture_output=True,text=True,timeout=60)
            if built.returncode:raise RuntimeError(built.stderr)
    return target,digest


class NativeFastScheduler:
    def __init__(self,algorithm='fast-native-nvidia',fast_options=None):
        self.algorithm=algorithm
        self.options=fast_options

    def schedule(self,matrix,hardware,*,seed):
        hardware.validate_matrix(matrix)
        n,g=hardware.num_servers,hardware.gpus_per_server
        if n>10 or g>8:raise ValueError('original native source supports at most10 servers and8 GPUs/server')
        pair=[[0 if s==d else (sum(int(x) for x in matrix.values[s*g:(s+1)*g,d*g:(d+1)*g].flat)+g-1)//g
               for d in range(n)] for s in range(n)]
        bound=max(max(map(sum,pair)),max(sum(pair[s][d] for s in range(n)) for d in range(n)))
        if bound>4294967295:
            raise ValueError('original native source32-bit frequency range exceeded (conservative service-sum bound)')
        variant={'fast-native-nvidia':'native','fast-native-cost':'cost',
                 'fast-native-mixed':'mixed'}[self.algorithm]
        options=self.options or {}
        replay=options.get('replay_stages')
        if replay is not None:
            variant='replay'
        dc=options.get('server_dc_ids',[s//2 for s in range(hardware.num_servers)])
        if len(dc)!=hardware.num_servers:raise ValueError('native server_dc_ids length mismatch')
        permutation=options.get('manual_server_permutation',list(range(hardware.num_servers)))
        if sorted(permutation)!=list(range(hardware.num_servers)):raise ValueError('invalid native rank permutation')
        driver,digest=native_driver()
        with tempfile.TemporaryDirectory(prefix='fast-native-input-') as tmp:
            path=Path(tmp)/'matrix.txt'
            np.savetxt(path,matrix.values,fmt='%d')
            extra=[]
            if replay is not None:
                plan=Path(tmp)/'replay.txt'
                if any(not isinstance(r['freq'],int) or r['freq']<=0 or
                       sorted(r['mp'])!=list(range(n)) for r in replay):
                    raise ValueError('invalid replay permutation/coefficient')
                plan.write_text(str(len(replay))+'\n'+''.join(
                    ' '.join(map(str,[r['freq'],*r['mp']]))+'\n' for r in replay))
                extra=[str(plan)]
            done=subprocess.run([str(driver),str(hardware.num_servers),str(hardware.gpus_per_server),
                variant,str(path),*map(str,dc),str(options.get('intra_dc_bandwidth_gbps',400)),
                str(options.get('inter_dc_bandwidth_gbps',100)),*map(str,permutation),*extra],capture_output=True,text=True,timeout=30)
        if done.returncode:raise RuntimeError(f'native host exporter failed: {done.stderr}')
        raw,records=[],[]
        for line in done.stdout.splitlines():
            row=line.split()
            if row[0]=='F':
                raw.append((int(row[1]),int(row[2]),int(row[3]),0,row[4],int(row[5])))
            elif row[0]=='P':
                records.append(dict(stage_id=int(row[1]),freq=int(row[2]),mp=list(map(int,row[3:])),
                                    quota_per_gpu_bytes=[int(row[2])]*hardware.num_servers))
        return _finalize_schedule(self.algorithm,raw,hardware,None,dict(
            seed=seed,n_stages=len(records),stage_records=records,
            logical_bytes=matrix.logical_bytes,native_source_digest=digest,
            native_reference='FAST/nvidia flash_scheduler, nonchunked',
            server_dc_ids=list(dc),
            native_global_send_barrier=bool(options.get('native_global_send_barrier',False)),
            stage_order=('supplied replay order' if replay is not None else 'reverse stable ascending frequency'),
            native_variant=variant))
