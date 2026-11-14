"""Dependency evidence and packet-exact WAN idle classification for native FAST."""

import csv
from collections import defaultdict
from pathlib import Path


def rows(path):
    with Path(path).open(encoding='utf-8') as stream:
        return [{k:(int(v) if k!='event' else v) for k,v in row.items()}
                for row in csv.DictReader(stream)]


def read_native_runtime(schedule,path):
    modeled=schedule.metadata.get('execution_model')=='fast-nvidia-deps-modeled-scaleup'
    if not schedule.flows and not modeled:
        return dict(epoch_ns=0,end_ns=0,dependency_violations=0,flows=[],credit_wire_bytes=0,signal_tail_bytes=0)
    events=rows(path)
    by=defaultdict(list);by_command=defaultdict(list);redis={}
    for event in events:
        by[event['event'],event['flow_id']].append(event)
        by_command[event['event'],event['rank'],event['stage']].append(event['time_ns'])
        if event['event']=='redis_done':
            key=(event['rank'],event['stage'])
            if key in redis:raise ValueError('duplicate native redistribution')
            redis[key]=event['time_ns']
    def one(kind,id):
        records=by[kind,id]
        if len(records)!=1:raise ValueError(f'native {kind} flow{id}: expected exactly one event')
        return records[0]
    epoch=one('begin',-1)['time_ns'];end=one('finished',-1)['time_ns']
    measured=[];incoming={};outgoing=defaultdict(list)
    expected_ids={f.flow_id for f in schedule.flows}
    for kind in ('data_release','data_ready','ack_done','credit_send','credit_arrive'):
        observed={id for (name,id),entries in by.items() if name==kind and entries}
        if observed!=expected_ids:raise ValueError(f'native {kind} flow ID set mismatch')
    for flow in schedule.flows:
        id=flow.flow_id;k=flow.stage_id
        release=one('data_release',id);rx=one('data_ready',id);ack=one('ack_done',id)
        cs=one('credit_send',id);ca=one('credit_arrive',id)
        if (release['rank'],rx['rank'],ack['rank'])!=(flow.src,flow.dst,flow.src):
            raise ValueError('native event endpoint mismatch')
        if any(e['stage']!=k for e in (release,rx,ack,cs,ca)):
            raise ValueError('native event stage mismatch')
        if not(epoch<=cs['time_ns']<=ca['time_ns']<=release['time_ns']<=rx['time_ns']<=ack['time_ns']<=end):
            raise ValueError('native credit/visibility/ACK order violated')
        if redis.get((flow.dst,k),-1)<rx['time_ns']:
            raise ValueError('redistribution precedes native visibility')
        incoming[k,flow.dst]=id;outgoing[flow.src].append(flow)
        measured.append(dict(flow_id=id,release_ns=release['time_ns'],receive_ns=rx['time_ns'],
                             ack_ns=ack['time_ns'],credit_send_ns=cs['time_ns'],credit_arrive_ns=ca['time_ns']))
    for flow in schedule.flows:
        cs=one('credit_send',flow.flow_id)['time_ns'];k=flow.stage_id
        previous=incoming.get((k-1,flow.dst))
        if previous is not None and cs<one('data_ready',previous)['time_ns']:
            raise ValueError('credit issued before previous receive')
        if k>=2 and cs<redis.get((flow.dst,k-2),end+1):
            raise ValueError('credit issued before buffer reuse is safe')
    for rank,flows in outgoing.items():
        flows.sort(key=lambda f:f.stage_id)
        for first,second in zip(flows,flows[1:]):
            if one('data_release',second.flow_id)['time_ns']<one('ack_done',first.flow_id)['time_ns']:
                raise ValueError('native send stream advanced before quiet/ACK')
    count=schedule.metadata['native_rank_count']*schedule.metadata['n_stages']
    if len(redis)!=count:raise ValueError('native redistribution command coverage incomplete')
    for rank in range(schedule.metadata['native_rank_count']):
        times=[redis[rank,k] for k in range(schedule.metadata['n_stages'])]
        if times!=sorted(times) or any(t<epoch or t>end for t in times):
            raise ValueError('native receive stream order violated')
    local_stats={}
    if modeled:
        local=schedule.metadata['local_times_ns'];g=local['gpus'];tails=[]
        def local_one(kind,rank,k=-1):
            found=by_command[kind,rank,k]
            if len(found)!=1:raise ValueError(f'local {kind} coverage mismatch')
            return found[0]
        for s,(balance,intrinsic) in enumerate(zip(local['balance'],local['intrinsic'])):
            rank=s*g;begin=local_one('balance_begin',rank);balanced=local_one('balance_done',rank)
            ibegin=local_one('intrinsic_begin',rank);previous=local_one('intrinsic_done',rank)
            if begin!=epoch or balanced-begin!=balance or ibegin!=balanced or previous-ibegin!=intrinsic:
                raise ValueError('native local head duration mismatch')
            for k,durations in enumerate(local['dispatch']):
                start=local_one('redis_begin',rank,k)
                ready=max([previous]+[one('data_ready',incoming[k,r])['time_ns']
                    for r in range(rank,rank+g) if (k,r) in incoming])
                ends=[redis[r,k] for r in range(rank,rank+g)]
                if start!=ready or any(t-start!=durations[s] for t in ends):
                    raise ValueError('native local dispatch dependency/duration mismatch')
                previous=ends[0]
            tails.append(previous)
            for f in schedule.flows:
                if f.src//g==s and one('data_release',f.flow_id)['time_ns']<balanced:
                    raise ValueError('native send before balance complete')
                if f.dst//g==s and one('credit_send',f.flow_id)['time_ns']<balanced:
                    raise ValueError('native credit before balance complete')
        if end!=max(tails+[m['ack_ns'] for m in measured]):
            raise ValueError('native modeled operator completion mismatch')
        local_stats=dict(local_duration_verified=True,local_completion_ns=tails,
                         last_ack_ns=max([m['ack_ns'] for m in measured],default=epoch))
    return dict(epoch_ns=epoch,end_ns=end,dependency_violations=0,flows=measured,**local_stats,
                credit_wire_bytes=8*sum(e['event']=='credit_wire_send' for e in events),
                signal_tail_bytes=8*len(schedule.flows))


def link_idle_statistics(epoch,end,flow_bytes,releases,injections,busy_intervals,queues):
    """Partition idle using source-unsent backlog, not unacknowledged bytes.

    This diagnoses eligibility, not proves that every blocked interval can be
    removed. A legal counterfactual schedule is required for a causal claim.
    """
    changes=defaultdict(list)
    changes[epoch];changes[end]
    for id,size in flow_bytes.items():changes[releases[id]].append(('release',size))
    injected=defaultdict(int)
    for time,id,prefix in injections:
        if id not in flow_bytes:continue
        if prefix<injected[id] or prefix>flow_bytes[id]:raise ValueError('invalid native source injection prefix')
        changes[time].append(('inject',prefix-injected[id]));injected[id]=prefix
    for start,stop in busy_intervals:
        if stop>epoch and start<end:
            changes[max(epoch,start)].append(('busy',1));changes[min(end,stop)].append(('busy',-1))
    initial_queue=0
    for time,size in queues:
        if time<epoch:initial_queue=size
        if epoch<=time<=end:changes[time].append(('queue',size))
    result=dict(busy_ns=0,idle_ns=0,dependency_only_idle_ns=0,
                transport_or_upstream_idle_ns=0,no_source_backlog_idle_ns=0,
                queued_idle_ns=0,max_idle_gap_ns=0,peak_queue_bytes=0)
    gated=sum(flow_bytes.values());active=busy=0;queue=initial_queue;idle_run=0
    result['peak_queue_bytes']=initial_queue
    times=sorted(t for t in changes if epoch<=t<=end)
    for time,next_time in zip(times,times[1:]):
        for kind,value in changes[time]:
            if kind=='release':gated-=value;active+=value
            elif kind=='inject':active-=value
            elif kind=='busy':busy+=value
            else:queue=value;result['peak_queue_bytes']=max(result['peak_queue_bytes'],value)
        if active<0 or gated<0 or busy<0:raise ValueError('native trace accounting underflow')
        dt=next_time-time
        if busy:result['busy_ns']+=dt;idle_run=0
        else:
            result['idle_ns']+=dt;idle_run+=dt
            result['max_idle_gap_ns']=max(result['max_idle_gap_ns'],idle_run)
            if queue:result['queued_idle_ns']+=dt
            elif active:result['transport_or_upstream_idle_ns']+=dt
            elif gated:result['dependency_only_idle_ns']+=dt
            else:result['no_source_backlog_idle_ns']+=dt
    result['eligible_payload_bytes']=sum(flow_bytes.values())
    result['busy_fraction']=result['busy_ns']/(end-epoch) if end>epoch else None
    result['idle_classification']='source unsent payload; dependency-only is a candidate loss, not a proof'
    return result


def analyze_native_links(schedule,attempt,topology):
    attempt=Path(attempt);gateways=topology.get('gateway_ids',[])
    if not gateways:return {'ports':[]}
    runtime=read_native_runtime(schedule,attempt/'native.csv.events.csv')
    dc=schedule.metadata['server_dc_ids'];g=schedule.metadata['native_gpus_per_server']
    ports={};next_port=defaultdict(lambda:1)
    for line in (attempt/'topology.txt').read_text().splitlines()[2:]:
        fields=line.split();a,b=int(fields[0]),int(fields[1])
        pa,pb=next_port[a],next_port[b];next_port[a]+=1;next_port[b]+=1
        if a in gateways and b in gateways:
            ports[a,pa]=(gateways.index(a),gateways.index(b))
            ports[b,pb]=(gateways.index(b),gateways.index(a))
    packets=rows(attempt/'native_wan.csv');injection=rows(attempt/'native_injection.csv')
    queue_rows=rows(attempt/'native_wan_queue.csv')
    injected=[(r['time_ns'],r['flow_id'],r['injected_bytes']) for r in injection]
    released={r['flow_id']:r['release_ns'] for r in runtime['flows']}
    summaries=[]
    for (node,port),(src_dc,dst_dc) in ports.items():
        selected={f.flow_id:f.size_bytes for f in schedule.flows
                  if (dc[f.src//g],dc[f.dst//g])==(src_dc,dst_dc)}
        transmissions=[(r['start_ns'],r['start_ns']+r['duration_ns']) for r in packets
                       if (r['node_id'],r['port_id'])==(node,port)]
        queues=[(r['time_ns'],r['queue_bytes']) for r in queue_rows
                if (r['node_id'],r['port_id'])==(node,port)]
        stats=link_idle_statistics(runtime['epoch_ns'],runtime['end_ns'],selected,released,
                                   injected,transmissions,queues)
        stats.update(node_id=node,port_id=port,src_dc=src_dc,dst_dc=dst_dc)
        summaries.append(stats)
    return dict(ports=summaries,dependency_only_idle_ns=sum(p['dependency_only_idle_ns'] for p in summaries),
                busy_fraction_mean=sum(p['busy_fraction'] for p in summaries)/len(summaries) if summaries else None)
