"""Approved v2 inputs: fixed-row synthetic demand and disjoint real windows."""
import hashlib
import json
from pathlib import Path
import numpy as np
from .model import TrafficMatrix

MODELS={
 'deepseek':('cognitivecomputations/DeepSeek-R1-AWQ',256),
 'kimi':('moonshotai/Kimi-K2-Thinking',384),
 'qwen':('Qwen/Qwen3-235B-A22B-FP8',128),
 'llama':('meta-llama/Llama-4-Maverick-17B-128E-Instruct',128)}

def synthetic(p,family,seed,mib):
    M=32*(p-1);rng=np.random.default_rng(seed);C=np.zeros((p,p),dtype=np.int64)
    if family=='balanced':C[:]=32;np.fill_diagonal(C,0);popularity=None
    else:
        alpha={'zipf08':.8,'zipf12':1.2}[family]
        order=rng.permutation(p);weight=np.zeros(p);weight[order]=np.arange(1,p+1,dtype=float)**(-alpha)
        for i in range(p):
            w=weight.copy();w[i]=0;C[i]=rng.multinomial(M,w/w.sum())
        popularity=order.tolist()
    h=4*((int(mib)*1024**2)//(4*M))
    if h<=0:raise ValueError('size below integer block granularity')
    return TrafficMatrix(C*h,dict(generator=family,seed=seed,blocks_per_rank=M,block_bytes=h,
        requested_mib_per_rank=mib,actual_bytes_per_rank=h*M,popularity_order=popularity,
        meaning='conditional destination-hotspot multinomial; no claim of fitting real MoE'))

def catalog(model,root):
    folder,experts=MODELS[model];files=sorted((Path(root)/folder).rglob('*.json'))
    if len(files)<8:raise ValueError(f'not enough trace files: {model}')
    records=json.loads(files[0].read_bytes());records=[records] if isinstance(records,dict) else records
    layers=sorted({int(k) for record in records for k,v in record.items() if k.isdigit() and isinstance(v,list) and v})
    if not layers:raise ValueError(f'no layers: {model}')
    return dict(files=[str(p.resolve()) for p in files],layers=sorted({layers[0],layers[len(layers)//2],layers[-1]}),
                experts=experts,file_count=len(files),selection='sorted paths; fixed first/middle/last layer IDs')

def trace_input(info,window,layer,p,batch):
    if info['experts']%p:raise ValueError('equal expert placement unavailable for this GPU count')
    length=len(info['files']);start=length*(1+2*window)//8;end=length*(3+2*window)//8
    placement=np.random.default_rng(1101).permutation(info['experts'])
    owner=np.zeros(info['experts'],dtype=np.int64);owner[placement]=np.arange(info['experts'])//(info['experts']//p)
    values=np.zeros((p,p),dtype=np.int64);origins=[];sources=[];assignments=self_bytes=0;required=p*batch
    for index in range(start,end):
        path=Path(info['files'][index]);blob=path.read_bytes();sources.append(dict(path=str(path),sha256=hashlib.sha256(blob).hexdigest()))
        records=json.loads(blob);records=[records] if isinstance(records,dict) else records
        for ri,record in enumerate(records):
            for ti,choice in enumerate(record.get(str(layer),[])):
                experts=[choice] if type(choice) is int else choice
                if not isinstance(experts,list) or not experts or any(type(e)is not int or not 0<=e<info['experts'] for e in experts) or len(set(experts))!=len(experts):
                    raise ValueError('invalid trace expert choices')
                src=len(origins)%p
                for expert in experts:
                    dst=int(owner[expert]);assignments+=1
                    if src==dst:self_bytes+=8192
                    else:values[src,dst]+=8192
                origins.append([index,ri,ti])
                if len(origins)==required:break
            if len(origins)==required:break
        if len(origins)==required:break
    if len(origins)!=required:raise ValueError(f'trace window lacks tokens: {len(origins)}<{required}')
    return TrafficMatrix(values,dict(generator='real-expert-trace-rebatched-v2',window=window,window_files=[start,end],
        layer=layer,tokens=required,tokens_per_gpu=batch,token_origins=origins,sources=sources,
        expert_count=info['experts'],vector_bytes=8192,routed_assignments=assignments,self_route_bytes=self_bytes,
        source_placement='canonical token index modulo GPU count',expert_placement='seed1101 shuffled equal groups',
        expert_placement_seed=1101,expert_to_gpu=owner.tolist(),
        measurement_scope='expert choices measured; sources, placement and bytes modeled',synthetic_rebatch=True,
        bytes_conserved=int(values.sum())+self_bytes==assignments*8192))
