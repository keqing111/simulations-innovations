"""Actual event skew, byte-conserved original-source delivery and WAN bins."""
import csv
import json
from collections import defaultdict
from pathlib import Path
import numpy as np

def write_csv(path,fields,records):
    with Path(path).open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(records)

def collect(schedule,matrix,attempt):
    with (attempt/'native.csv.events.csv').open() as stream:events=list(csv.DictReader(stream))
    for row in events:
        for key in ('time_ns','flow_id','rank','stage'):row[key]=int(row[key])
    epoch=next(e['time_ns'] for e in events if e['event']=='begin')
    end=next(e['time_ns'] for e in events if e['event']=='finished')
    data={};ack={};release={};redis={};intrinsic={}
    for e in events:
        kind=e['event'];t=e['time_ns']
        if kind=='data_ready':data[e['flow_id']]=t
        elif kind=='data_release':release[e['flow_id']]=t
        elif kind=='ack_done':ack[e['flow_id']]=t
        elif kind=='redis_done':redis[e['rank'],e['stage']]=t
        elif kind=='intrinsic_done':intrinsic[e['rank']]=t
    stages=[];g=schedule.metadata['native_gpus_per_server'];p=matrix.num_devices
    for k in range(schedule.metadata['n_stages']):
        ids=[f.flow_id for f in schedule.flows if f.stage_id==k]
        times=[release[i] for i in ids];ends=[ack[i] for i in ids]
        stages.append(dict(stage=k,active_network_flows=len(ids),
            release_skew_ns=max(times)-min(times) if times else None,
            ack_skew_ns=max(ends)-min(ends) if ends else None,
            redistribution_skew_ns=max(redis[r,k] for r in range(p))-min(redis[r,k] for r in range(p))
                if all((r,k) in redis for r in range(p)) else None))
    write_csv(attempt/'stage_skew.csv',list(stages[0]) if stages else ['stage'],stages)
    # Send/receive frontiers count completed REAL messages, not invisible virtual slots.
    progress=[]
    for f in schedule.flows:
        progress.extend([dict(time_ns=ack[f.flow_id],rank=f.src,kind='real_send_ack',stage=f.stage_id),
                         dict(time_ns=data[f.flow_id],rank=f.dst,kind='real_receive',stage=f.stage_id)])
    progress.extend(dict(time_ns=t,rank=r,kind='redistribution',stage=k) for (r,k),t in redis.items())
    progress.sort(key=lambda e:(e['time_ns'],e['rank'],e['kind'],e['stage']))
    write_csv(attempt/'rank_progress.csv',['time_ns','rank','kind','stage'],progress)
    delivered=[]
    for f in schedule.flows:
        chunks=schedule.metadata['delivery_chunks'][str(f.flow_id)]
        if sum(c[2] for c in chunks)!=f.size_bytes:raise ValueError('network origin chunk size mismatch')
        for origin,dst,size in chunks:
            if dst==f.dst:delivered.append((data[f.flow_id],origin,dst,size))
    for record in schedule.metadata['local_delivery_records']:
        t=(intrinsic[record['src']//g*g] if record['phase']=='intrinsic' else redis[record['dst'],record['stage']])
        delivered.extend((t,origin,dst,size) for origin,dst,size in record['chunks'])
    actual=np.zeros_like(matrix.values)
    for t,src,dst,size in delivered:actual[src,dst]+=size
    if not np.array_equal(actual,matrix.values):raise ValueError('original source/final destination delivery mismatch')
    prefix=defaultdict(int);rows=[];complete={};required=matrix.values.sum(axis=1)
    for t,src,dst,size in sorted(delivered):
        prefix[src]+=size
        rows.append(dict(time_ns=t,original_source=src,final_destination=dst,bytes=size,source_delivered_bytes=prefix[src],
                         source_fraction=prefix[src]/int(required[src])))
        if prefix[src]==required[src]:complete[src]=t
    write_csv(attempt/'original_source_delivery.csv',['time_ns','original_source','final_destination','bytes','source_delivered_bytes','source_fraction'],rows)
    # Derive slot prefixes from the runtime's deterministic skip-empty rule.
    # Real send slots advance on ACK; empty send slots advance immediately after
    # balance/previous ACK. Receive is a visibility prefix, not dispatch/stream2.
    balanced={e['rank']//g:e['time_ns'] for e in events if e['event']=='balance_done'}
    outgoing={(f.src,f.stage_id):f.flow_id for f in schedule.flows}
    incoming={(f.dst,f.stage_id):f.flow_id for f in schedule.flows}
    changes=defaultdict(list);thresholds=[];K=schedule.metadata['n_stages']
    for rank in range(p):
        tx=rx=balanced.get(rank//g,epoch)
        for k in range(K):
            if (rank,k) in outgoing:tx=max(tx,ack[outgoing[rank,k]])
            if (rank,k) in incoming:rx=max(rx,data[incoming[rank,k]])
            changes[tx].append(('send_slot_prefix',rank,k+1))
            changes[rx].append(('receive_visibility_prefix',rank,k+1))
            if (rank,k) in redis:changes[redis[rank,k]].append(('redistribution_prefix',rank,k+1))
        if required[rank]:
            for fraction in (.1,.5,.9,1.):
                t=next(r['time_ns'] for r in rows if r['original_source']==rank and r['source_fraction']>=fraction)
                thresholds.append(dict(rank=rank,fraction=fraction,relative_time_ns=t-epoch))
    for t,src,dst,size in delivered:changes[t].append(('bytes',src,size))
    write_csv(attempt/'source_threshold_times.csv',['rank','fraction','relative_time_ns'],thresholds)
    frontier={kind:np.zeros(p,dtype=np.int64) for kind in ('send_slot_prefix','receive_visibility_prefix','redistribution_prefix')}
    byte_counts=np.zeros(p,dtype=np.int64);quantiles=[];points=sorted(changes);cursor=0
    for t in sorted(set([epoch,end]+list(range(epoch,end,100000)))):
        while cursor<len(points) and points[cursor]<=t:
            for kind,rank,value in changes[points[cursor]]:
                if kind=='bytes':byte_counts[rank]+=value
                else:frontier[kind][rank]=max(frontier[kind][rank],value)
            cursor+=1
        for kind,values in list(frontier.items())+[('original_source_fraction',byte_counts[required>0]/required[required>0])]:
            if not len(values):continue
            lo,median,hi=np.quantile(values,[.1,.5,.9])
            quantiles.append(dict(relative_time_ns=t-epoch,kind=kind,p10=float(lo),p50=float(median),p90=float(hi),
                minimum=float(min(values)),maximum=float(max(values)),gap=float(max(values)-min(values))))
    write_csv(attempt/'progress_quantiles.csv',['relative_time_ns','kind','p10','p50','p90','minimum','maximum','gap'],quantiles)
    # Throughput is wire occupancy unless backend supplies an explicit payload field.
    bins=defaultdict(lambda:[0.,0.,0.]);width=100000
    with (attempt/'native_wan.csv').open() as stream:
        for packet in csv.DictReader(stream):
            a=int(packet['start_ns']);b=a+int(packet['duration_ns']);kind=int(packet['kind'])
            if a>=end or b<=epoch or b<=a:continue
            bytes_=int(packet.get('payload_bytes',packet['wire_bytes']) if kind in (0,3) else packet['wire_bytes'])
            for index in range(max(0,(a-epoch)//width),(min(b,end)-1-epoch)//width+1):
                overlap=max(0,min(b,end,epoch+(index+1)*width)-max(a,epoch+index*width))
                bins[int(packet['node_id']),int(packet['port_id']),index][0 if kind==0 else 1 if kind==3 else 2]+=bytes_*overlap/(b-a)
    link_rows=[]
    ports={(node,port) for node,port,_ in bins}
    for node,port in sorted(ports):
        for index in range((end-epoch+width-1)//width):
            duration=min(width,end-epoch-index*width);fg,bg,control=bins[node,port,index]
            link_rows.append(dict(node=node,port=port,bin=index,relative_start_ns=index*width,duration_ns=duration,
                foreground_gbps=fg*8/duration,background_gbps=bg*8/duration,other_gbps=control*8/duration))
    write_csv(attempt/'wan_bins_100us.csv',['node','port','bin','relative_start_ns','duration_ns','foreground_gbps','background_gbps','other_gbps'],link_rows)
    result=dict(delivered_bytes=int(actual.sum()),original_source_completion_skew_ns=max(complete.values())-min(complete.values()) if complete else 0,
        source_q100_max_ns=max((r['relative_time_ns'] for r in thresholds if r['fraction']==1.),default=0),
        stage_release_skew_mean_ns=float(np.mean([s['release_skew_ns'] for s in stages if s['active_network_flows']]))
            if any(s['active_network_flows'] for s in stages) else None,
        background_horizon_valid=end<1000000000,
        frontiers='send slot prefix derived by ACK/skip-empty rule; receive visibility prefix; actual redistribution; no global barrier',
        wan_bin_unit='FG/BG logical payload excluding tail/header (retransmissions count); other=control wire bytes')
    (attempt/'v2_diagnostics.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
