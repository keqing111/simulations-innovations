"""Content-addressed compiled CPU-FAST/Max-Min/partition core, not a GPU runtime."""

import ctypes as ct
import fcntl
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import numpy as np

ROOT=Path(__file__).resolve().parents[3]
CPU=ROOT.parents[2].parent/'FAST/simulation'
MODES={'native':0,'maxmin':1,'partition':2,'adaptive':3}
CACHE_ROOT=ROOT/'results/cpp-epoch-build'
BUILD_FLAGS=('-std=c++17','-O3','-DNOLOG','-shared','-fPIC')
_loaded={};_lock=threading.Lock()


@lru_cache(maxsize=1)
def compiler_identity():
    return subprocess.check_output(['g++','--version'],timeout=10).decode().splitlines()[0]


@lru_cache(maxsize=1)
def compiler_signature():
    executable=Path(shutil.which('g++')).resolve()
    return dict(path=str(executable),sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                version=compiler_identity())


def compile_kernel():
    sources=[ROOT/'tools/paper_epoch_kernel.cc',CPU/'algorithm/all2all.cpp',
             CPU/'algorithm/matrix.cpp',CPU/'scheduler/config.cpp',CPU/'topology/model.cpp']
    headers=[CPU/'define.h',*sorted((CPU/'algorithm').glob('*.h')),
             *sorted((CPU/'scheduler').glob('*.h')),*sorted((CPU/'topology').glob('*.h'))]
    recipe=[compiler_signature()['path'],*BUILD_FLAGS,
            '-I',str(CPU),'-I',str(CPU/'algorithm'),'-I',str(CPU/'scheduler'),*map(str,sources)]
    identity=json.dumps(dict(compiler=compiler_signature(),recipe=recipe),sort_keys=True).encode()
    digest=hashlib.sha256(identity+b''.join(p.read_bytes() for p in sources+headers)).hexdigest()
    directory=CACHE_ROOT/digest;target=directory/'kernel.so';manifest=directory/'build.json'
    with _lock:
        directory.mkdir(parents=True,exist_ok=True)
        with (directory/'build.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if not target.is_file():
                with tempfile.TemporaryDirectory(prefix='compile-',dir=directory) as tmp:
                    built=Path(tmp)/'kernel.so'
                    command=[*recipe,'-o',str(built)]
                    done=subprocess.run(command,capture_output=True,text=True,timeout=90)
                    if done.returncode:raise RuntimeError(f'compiled epoch build failed: {done.stderr}')
                    info=dict(source_digest=digest,recipe=recipe,compiler=compiler_signature(),
                              binary_sha256=hashlib.sha256(built.read_bytes()).hexdigest())
                    record=Path(tmp)/'build.json';record.write_text(json.dumps(info,sort_keys=True)+'\n')
                    built.replace(target)
                    record.replace(manifest)
            if not manifest.is_file():raise RuntimeError('compiled library digest mismatch: missing build manifest')
            info=json.loads(manifest.read_text());binary_sha=hashlib.sha256(target.read_bytes()).hexdigest()
            if info.get('source_digest')!=digest or info.get('recipe')!=recipe or info.get('binary_sha256')!=binary_sha:
                raise RuntimeError('compiled library digest mismatch: cache changed')
        key=str(target)
        if key in _loaded:
            library=_loaded[key]
            if library.kernel_binary_sha256!=binary_sha:
                raise RuntimeError('compiled library digest mismatch: in-process loaded bytes differ')
            return library,digest
        library=ct.CDLL(str(target))
        library.kernel_binary_sha256=binary_sha
        u32=ct.POINTER(ct.c_uint32);u64=ct.POINTER(ct.c_uint64);i32=ct.POINTER(ct.c_int32)
        library.fast_epoch.argtypes=[ct.c_uint32,ct.c_uint32,ct.c_uint32,u32,u64,i32,
            ct.POINTER(ct.c_double),u32,i32,ct.c_uint32,u64,ct.POINTER(ct.c_double),ct.POINTER(ct.c_char),ct.c_uint32]
        library.fast_epoch.restype=ct.c_int
        _loaded[key]=library
        return library,digest


def synthesize(app,hardware,topology,mode):
    if mode not in MODES:raise ValueError('unknown compiled epoch mode')
    n,g=hardware.num_servers,hardware.gpus_per_server
    if not 1<=n<=64 or not 1<=g<=16 or app.dim!=n or len(topology.dc_ids)!=n:
        raise ValueError('compiled epoch supports1..64servers and1..16GPUs/server with matching labels')
    service=np.asarray(app.mat);contents=np.asarray(app.real_gpu_contents)
    limit=4294967295
    if (service.shape!=(n,n) or contents.shape!=(n*g,n*g) or
            not np.issubdtype(service.dtype,np.integer) or not np.issubdtype(contents.dtype,np.integer) or
            np.any(service<0) or np.any(contents<0) or np.any(np.diag(service)) or
            int(service.max(initial=0))>limit or int(contents.max(initial=0))>limit or
            any(sum(map(int,service[s,:]))>limit or sum(map(int,service[:,s]))>limit for s in range(n))):
        raise ValueError('compiled kernel matrix/contents shape or native32-bit range invalid')
    for s in range(n):
        for d in range(n):
            if s!=d and any(sum(map(int,contents[s*g+i,d*g:(d+1)*g]))>int(service[s,d]) for i in range(g)):
                raise ValueError('real GPU row exceeds native server service quota')
    labels={v:i for i,v in enumerate(sorted(set(topology.dc_ids)))}
    dc=np.ascontiguousarray([labels[v] for v in topology.dc_ids],dtype=np.int32)
    data=np.ascontiguousarray(contents,dtype=np.uint64);server=np.ascontiguousarray(service,dtype=np.uint32)
    params=np.array([topology.server_gbps,topology.wan_gbps,topology.gateway_gbps,
                     topology.lan_alpha_ns,topology.wan_alpha_ns,hardware.intra.alpha_ns,
                     hardware.intra.beta_ns_per_byte],dtype=np.float64)
    if np.any(~np.isfinite(params)) or np.any(params[:3]<=0) or np.any(params[3:]<0):
        raise ValueError('invalid compiled model parameters')
    capacity=2*n*n;weights=np.zeros(capacity,dtype=np.uint32);maps=np.zeros((capacity,n),dtype=np.int32)
    stats=np.zeros(4,dtype=np.uint64);costs=np.zeros(3,dtype=np.float64);error=ct.create_string_buffer(1024)
    library,digest=compile_kernel()
    ptr=lambda a,kind:a.ctypes.data_as(ct.POINTER(kind))
    code=library.fast_epoch(n,g,MODES[mode],ptr(server,ct.c_uint32),ptr(data,ct.c_uint64),
        ptr(dc,ct.c_int32),ptr(params,ct.c_double),ptr(weights,ct.c_uint32),ptr(maps,ct.c_int32),capacity,
        ptr(stats,ct.c_uint64),ptr(costs,ct.c_double),error,len(error))
    if code:raise RuntimeError(error.value.decode())
    names=['native','maxmin','partition'];count=int(stats[0]);chosen=int(stats[3])
    stages=[dict(freq=int(weights[k]),mp=list(map(int,maps[k]))) for k in range(count)]
    return stages,dict(kernel_source_digest=digest,kernel_ns=int(stats[2]),matching_calls=int(stats[1]),
        kernel_binary_sha256=library.kernel_binary_sha256,
        selected_policy=names[chosen],selected_cost_ns=float(costs[chosen]),
        candidate_costs_ns={name:(float(costs[i]) if costs[i]>=0 else None) for i,name in enumerate(names)},
        compiler=compiler_identity())
