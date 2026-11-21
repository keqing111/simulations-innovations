"""Local expert-choice traces -> explicitly assumed EP dispatch placement.

Only expert IDs are consumed. Source ranks/placement were NOT measured by the
trace; those are reproducible experiment assumptions. No text is exported.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path

from .model import Message


def load_dispatch(path,*,layer,ranks,tokens_per_rank,offset=0,experts=128,bytes_per_token=8192):
    for value in (layer,ranks,tokens_per_rank,offset,experts,bytes_per_token):
        if type(value) is not int:raise ValueError('trace dimensions must be integers')
    if layer<0 or offset<0 or min(ranks,tokens_per_rank,experts,bytes_per_token)<=0 or experts%ranks:
        raise ValueError('invalid trace mapping')
    path=Path(path);tokens=[];sources=[];count=ranks*tokens_per_rank
    # Directory input: deterministic numeric filename prefix, FIRST record only.
    # These are prompt/prefill records; do not mix later decode steps into one op.
    files=(sorted(path.glob('*.json'),key=lambda p:(0,int(p.stem)) if p.stem.isdigit() else (1,p.name))
           if path.is_dir() else [path])
    for source in files:
        raw=source.read_bytes();data=json.loads(raw)
        requests=data if isinstance(data,list) else [data]
        if path.is_dir():requests=requests[:1]
        for request in requests:
            if str(layer) not in request:raise ValueError('requested layer missing')
            for choice in request[str(layer)]:
                ids=[choice] if type(choice) is int else choice
                if not isinstance(ids,list) or not ids or any(type(e) is not int or not 0<=e<experts for e in ids):
                    raise ValueError('invalid expert selection')
                if len(ids)!=len(set(ids)):raise ValueError('duplicate expert per token')
                tokens.append(ids)
        sources.append(dict(path=str(source.resolve()),sha256=hashlib.sha256(raw).hexdigest()))
        if len(tokens)>=offset+count:break
    window=tokens[offset:offset+count]
    if len(window)!=count:raise ValueError('trace window is too short; no synthetic repetition')
    matrix=Counter();local=0;routes=0
    for index,choice in enumerate(window):
        src=index//tokens_per_rank
        for expert in choice:
            dst=expert//(experts//ranks);routes+=1
            if src==dst:local+=bytes_per_token
            else:matrix[src,dst]+=bytes_per_token
    flows=[Message(i,s,d,size) for i,((s,d),size) in enumerate(sorted(matrix.items()))]
    metadata=dict(source=str(path.resolve()),source_files=sources,record_scope='first per file' if path.is_dir() else 'all records',layer=layer,
        ranks=ranks,tokens_per_rank=tokens_per_rank,offset=offset,experts=experts,bytes_per_token=bytes_per_token,
        routing_choices=routes,network_bytes=sum(matrix.values()),same_gpu_bytes=local,messages=len(flows),
        mapping='consecutive token blocks to source ranks; contiguous equal expert placement; no inter-expert deduplication',
        provenance='measured expert choices; ASSUMED source partition, placement, activation bytes, invocation window')
    if metadata['network_bytes']+local!=routes*bytes_per_token:raise AssertionError('trace bytes not conserved')
    return flows,metadata
