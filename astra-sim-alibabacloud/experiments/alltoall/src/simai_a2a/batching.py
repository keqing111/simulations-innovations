"""Explicit NEW ideal-scaleup executor: adjacent batches with peer/buffer bounds.

This does not replace original CPU or GPU FAST semantics. All source-local
balance is complete first; destination redistribution is instantaneous.
"""
from collections import defaultdict
import hashlib
import json
from numbers import Integral
from time import perf_counter_ns

from .execution import ideal_scaleup_schedule,STAGE_BARRIER_MODEL
from .model import Schedule,ScheduledFlow


def _limits(max_stages,max_peers,max_receive_bytes):
    for value in [max_stages,max_peers,max_receive_bytes]:
        if value is not None and (isinstance(value,bool) or not isinstance(value,Integral) or value<=0):
            raise ValueError('batch limits must be positive integers or None')


def _signature(projected):
    return hashlib.sha256(json.dumps([[f.src,f.dst,f.size_bytes,f.stage_id] for f in projected.flows],
                                    separators=(',',':')).encode()).hexdigest()


def _resources(flows):
    outgoing=defaultdict(set);incoming=defaultdict(set);received=defaultdict(int)
    for f in flows:
        outgoing[f.src].add(f.dst);incoming[f.dst].add(f.src);received[f.dst]+=f.size_bytes
    return dict(peak_send_peers_per_rank=max(map(len,outgoing.values()),default=0),
                peak_receive_peers_per_rank=max(map(len,incoming.values()),default=0),
                peak_receive_bytes_per_rank=max(received.values(),default=0))


def _fits(flows,stages,window,peers,buffer):
    if window is not None and stages[-1]-stages[0]+1>window:return False
    r=_resources(flows)
    return ((peers is None or max(r['peak_send_peers_per_rank'],r['peak_receive_peers_per_rank'])<=peers) and
            (buffer is None or r['peak_receive_bytes_per_rank']<=buffer))


def batch_schedule(full,hardware,name,*,max_stages=None,max_peers=None,max_receive_bytes=None,coalesce=True):
    _limits(max_stages,max_peers,max_receive_bytes)
    if not isinstance(coalesce,bool):raise ValueError('coalesce must be boolean')
    started=perf_counter_ns();projected=ideal_scaleup_schedule(full,hardware)
    if any(f.phase!='inter' for f in projected.flows):raise ValueError('batching requires FAST inter payload')
    bystage=defaultdict(list)
    for f in projected.flows:bystage[f.stage_id].append(f)
    groups=[];current=[];stages=[]
    for stage,flows in sorted(bystage.items()):
        if not _fits(flows,[stage],max_stages,max_peers,max_receive_bytes):
            raise ValueError('single original stage exceeds requested batch limit')
        if current and not _fits(current+flows,stages+[stage],max_stages,max_peers,max_receive_bytes):
            groups.append((stages,current));current=[];stages=[]
        stages.append(stage);current.extend(flows)
    if current:groups.append((stages,current))
    records=[]
    for batch,(_,flows) in enumerate(groups):
        endpoints=defaultdict(list)
        for f in flows:endpoints[f.src,f.dst].append(f)
        if coalesce:
            for (src,dst),members in endpoints.items():
                records.append((batch,src,dst,sum(f.size_bytes for f in members),[f.flow_id for f in members]))
        else:
            records.extend((batch,f.src,f.dst,f.size_bytes,[f.flow_id]) for f in flows)
    records.sort(key=lambda r:r[:4])
    output=[ScheduledFlow(i,src,dst,size,0,'inter',batch) for i,(batch,src,dst,size,_) in enumerate(records)]
    spec=dict(max_stages=max_stages,max_peers=max_peers,max_receive_bytes=max_receive_bytes,coalesce=coalesce,
        source_network_signature=_signature(projected),members=[r[4] for r in records],
        original_stage_groups=[s for s,_ in groups],resource_bounds=[_resources(f) for _,f in groups],
        executor='adjacent ACK-gated batches; not original FAST; no rolling window',
        scaleup_precondition='all ideal source balance complete before launch; local redistribution instant')
    result=Schedule(name,output,None,{**projected.metadata,'batching':spec,
        'active_network_stages':len(groups),'batch_construction_ns':perf_counter_ns()-started})
    audit_batch(full,result,hardware)
    return result


def audit_batch(full,result,hardware):
    """Reconstruct every original fragment and resource bound, not just totals."""
    source=ideal_scaleup_schedule(full,hardware);meta=result.metadata
    b=meta['batching'];window,peers,buffer=(b[k] for k in ['max_stages','max_peers','max_receive_bytes'])
    _limits(window,peers,buffer)
    if meta.get('execution_model')!=STAGE_BARRIER_MODEL or b['source_network_signature']!=_signature(source):
        raise ValueError('batch source or executor mismatch')
    if len(b['members'])!=len(result.flows):raise ValueError('batch member row count mismatch')
    covered=set();stage_batches={};batch_sources=defaultdict(list);endpoint_counts=defaultdict(int)
    for flow,ids in zip(result.flows,b['members']):
        flow.validate(hardware)
        if not ids or ids!=sorted(ids) or flow.start_ns or flow.phase!='inter':raise ValueError('invalid batch membership/order')
        total=0
        for member in ids:
            if isinstance(member,bool) or not isinstance(member,int) or not 0<=member<len(source.flows) or member in covered:
                raise ValueError('unknown or duplicate batch member')
            original=source.flows[member];covered.add(member)
            if (original.src,original.dst)!=(flow.src,flow.dst):raise ValueError('batch endpoint changed')
            if original.stage_id in stage_batches and stage_batches[original.stage_id]!=flow.stage_id:
                raise ValueError('original stage split between batches')
            stage_batches[original.stage_id]=flow.stage_id;total+=original.size_bytes
            batch_sources[flow.stage_id].append(original)
        if total!=flow.size_bytes:raise ValueError('batch member byte mismatch')
        if not b['coalesce'] and len(ids)!=1:raise ValueError('raw batch unexpectedly coalesced')
        endpoint_counts[flow.stage_id,flow.src,flow.dst]+=1
    if covered!=set(range(len(source.flows))):raise ValueError('missing original batch member')
    if b['coalesce'] and any(v!=1 for v in endpoint_counts.values()):raise ValueError('coalesced endpoints repeated')
    if sorted(batch_sources)!=list(range(len(batch_sources))):raise ValueError('noncontiguous batch IDs')
    mapping=[stage_batches[s] for s in sorted(stage_batches)]
    if mapping!=sorted(mapping):raise ValueError('batch precedence reversed')
    groups=[];resources=[]
    for batch,flows in sorted(batch_sources.items()):
        stages=sorted({f.stage_id for f in flows});groups.append(stages)
        if not _fits(flows,stages,window,peers,buffer):raise ValueError('batch limit violated')
        resources.append(_resources(flows))
    if groups!=b['original_stage_groups'] or resources!=b['resource_bounds']:
        raise ValueError('batch resource/group evidence mismatch')
    if result.physical_bytes!=source.physical_bytes:raise ValueError('batch physical bytes changed')
    return dict(source_flows_covered=len(covered),physical_bytes=result.physical_bytes,batch_count=len(groups),
        **{key:max((r[key] for r in resources),default=0) for key in
           ['peak_send_peers_per_rank','peak_receive_peers_per_rank','peak_receive_bytes_per_rank']})
