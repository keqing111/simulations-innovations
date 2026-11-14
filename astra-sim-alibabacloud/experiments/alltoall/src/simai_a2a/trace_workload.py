"""Trace-driven routing, with modeled placement/rebatching clearly disclosed."""
import hashlib
import json
from pathlib import Path

import numpy as np

from .model import TrafficMatrix


def trace_matrix(path, *, layer, num_devices, expert_count, token_limit, vector_bytes):
    """First token_limit recorded tokens; one copy per expert assignment.

    Token source = flattened position modulo GPU count. Expert destination =
    contiguous equal-size expert groups. These are experiment assumptions, not
    fields measured by the source trace. Same-GPU routes remain in metadata.
    """
    if (min(num_devices, expert_count, token_limit, vector_bytes) <= 0 or
            expert_count % num_devices):
        raise ValueError('positive dimensions and evenly divisible expert placement required')
    path=Path(path);blob=path.read_bytes();records=json.loads(blob)
    if isinstance(records,dict):records=[records]
    values=np.zeros((num_devices,num_devices),dtype=np.int64)
    tokens=assignments=self_bytes=0;origins=[]
    for record_id,record in enumerate(records):
        entries=record.get(str(layer))
        if not isinstance(entries,list):continue
        for position,choice in enumerate(entries):
            experts=[choice] if type(choice) is int else choice
            if not isinstance(experts,list) or not experts:
                raise ValueError('invalid token expert choices')
            if (any(type(e) is not int or not 0<=e<expert_count for e in experts) or
                    len(set(experts))!=len(experts)):
                raise ValueError('invalid or duplicate expert id')
            src=tokens%num_devices
            for expert in experts:
                dst=expert//(expert_count//num_devices)
                if src==dst:self_bytes+=vector_bytes
                else:values[src,dst]+=vector_bytes
                assignments+=1
            origins.append([record_id,position]);tokens+=1
            if tokens==token_limit:break
        if tokens==token_limit:break
    if not tokens:raise ValueError('layer missing or empty')
    return TrafficMatrix(values,dict(generator='real-expert-trace-rebatched',
        source_file=str(path.resolve()),source_sha256=hashlib.sha256(blob).hexdigest(),
        layer=layer,tokens=tokens,requested_tokens=token_limit,routed_assignments=assignments,
        token_origins=origins,expert_count=expert_count,vector_bytes=vector_bytes,
        self_route_bytes=self_bytes,source_placement='token_position modulo GPU count',
        expert_placement='contiguous equal-size groups',dispatch_pack='one copy per expert assignment',
        measurement_scope='expert choices only',synthetic_rebatch=True,
        bytes_conserved=int(values.sum())+self_bytes==assignments*vector_bytes))
