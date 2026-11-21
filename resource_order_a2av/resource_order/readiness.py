"""Whole-message list construction on predicted endpoint-release events.

This is a PLANNING model, not a replacement for SimAI measurements. Fluid
max-min rates approximate concurrent fixed-path transfers. Credit propagation,
data propagation and ACK propagation release the two endpoint roles separately.
Only the resulting queue order is exported; none of these times is enforced.
"""
from collections import defaultdict
import heapq
from bisect import bisect_right

from .model import Message, Plan


def fair_rates(paths, capacities):
    """Unweighted progressive filling, in bytes/us, on directed resources.

    Freeze flows on saturated resources, then increase the others. This avoids
    charging every path the slowest unrelated link's rate. No packet or CC model.
    """
    pending=set(paths); rates={i:0. for i in paths}; residual=dict(capacities)
    while pending:
        counts=defaultdict(int)
        for i in pending:
            for e in paths[i]:counts[e]+=1
        delta=min(residual[e]/count for e,count in counts.items())
        for i in pending:rates[i]+=max(0.,delta)
        for e,count in counts.items():residual[e]=max(0.,residual[e]-delta*count)
        saturated={e for e in counts if residual[e]<=1e-10*capacities[e]}
        frozen={i for i in pending if any(e in saturated for e in paths[i])}
        if not frozen:raise ArithmeticError('progressive filling failed to advance')
        pending-=frozen
    return rates


def sharing_finish_times(ids,remaining,paths,capacities,delay):
    """Per-flow pressure proxy; exact for simultaneous equal-share one-link jobs.

    For a link, sum_g min(bytes_i, bytes_g)/capacity. Unlike total link work,
    this accounts for small messages finishing before large ones. Taking the
    maximum over a multi-link path remains an approximation, not a fluid oracle.
    """
    return {i:delay[i]+max(sum(min(remaining[i],remaining[j]) for j in ids if e in paths[j])/capacities[e]
                         for e in paths[i]) for i in ids}


def construct_ready(messages,network,*,shared=True,alignment=True,objective='span'):
    """Return (Plan, diagnostic) without network simulation or oracle search.

    At a predicted event, admit only messages with BOTH endpoint roles free.
    An anchor advances the heaviest remaining resource; subsequent choices
    minimize extension of the active group, then its finish-time spread. Unlike
    scheduler.construct, a finished endpoint can immediately change partners.
    shared=False removes intermediate-resource sharing from selection and rates
    (an explicit ablation, not a feasible network capacity prediction).
    """
    if objective not in ('span','finish','tail'):raise ValueError('unknown readiness objective')
    messages=tuple(messages); Plan.from_order(messages,[f.id for f in messages])
    by_id={f.id:f for f in messages}
    paths={f.id:network.path(f.src,f.dst) for f in messages}
    delay={f.id:network.delay_us(f.src,f.dst) for f in messages}
    reverse={f.id:network.delay_us(f.dst,f.src) for f in messages}
    capacities={e:link.gbps/.008 for e,link in network.links.items()}
    solo={f.id:f.bytes/min(capacities[e] for e in paths[f.id]) for f in messages}
    remaining={f.id:float(f.bytes) for f in messages}
    work=defaultdict(float)
    for f in messages:
        for e in paths[f.id]:work[e]+=f.bytes/capacities[e]
    pending=set(by_id); busy_send=set(); busy_receive=set(); active=set()
    pending_send=defaultdict(float);pending_receive=defaultdict(float)
    for f in messages:
        service=solo[f.id]+delay[f.id]+reverse[f.id]
        pending_send[f.src]+=service;pending_receive[f.dst]+=service
    # credit phase is counted in candidate pressure, not in link service.
    credit=set(); events=[]; order=[]; records={}; now=0.; last_data=0.
    credit_start={};send_release=defaultdict(float);receive_release=defaultdict(float)

    def estimates(ids,extra=None):
        ids=set(ids)
        if extra is not None:ids.add(extra)
        loads=defaultdict(float)
        if shared:
            for i in ids:
                for e in paths[i]:loads[e]+=remaining[i]/capacities[e]
        return {i:delay[i]+max((loads[e] if shared else remaining[i]/capacities[e])
                              for e in paths[i]) for i in ids}

    while pending or events or active:
        # Drain simultaneous events before selecting any new partners.
        while events and events[0][0]<=now+1e-9:
            _,kind,i=heapq.heappop(events);f=by_id[i]
            if kind=='start':
                credit.remove(i);active.add(i);records[i]['start_us']=now
            elif kind=='data':
                busy_receive.remove(f.dst);records[i]['data_us']=now;last_data=max(last_data,now)
            else:
                busy_send.remove(f.src);records[i]['ack_us']=now
        while True:
            eligible=[i for i in pending if by_id[i].src not in busy_send and by_id[i].dst not in busy_receive]
            if not eligible:break
            group=active|credit
            if objective in ('finish','tail') and (group or objective=='tail'):
                # Cache current per-resource short-message work once per decision.
                sizes=defaultdict(list)
                for i in group:
                    for e in paths[i]:sizes[e].append(remaining[i])
                prefix={}
                for e,values in sizes.items():
                    values.sort();sums=[0.]
                    for value in values:sums.append(sums[-1]+value)
                    prefix[e]=sums
                def min_work(e,size):
                    values=sizes.get(e,());cut=bisect_right(values,size)
                    return prefix[e][cut]+(len(values)-cut)*size if values else 0.
                parts={i:{e:(min_work(e,remaining[i]) if shared else remaining[i])/capacities[e]
                          for e in paths[i]} for i in group}
                # Unreserved work gives a constant endpoint floor. For already
                # serialized data, include the known propagation/ACK release.
                floor=max([now]+[max(now,send_release[s])+w for s,w in pending_send.items()]
                               +[max(now,receive_release[d])+w for d,w in pending_receive.items()])
                def tail_key(i):
                    f=by_id[i];fpath=set(paths[i]);size=remaining[i]
                    finish={j:delay[j]+max(v+(min(size,remaining[j])/capacities[e] if shared and e in fpath else 0.)
                                          for e,v in parts[j].items())
                              +max(0.,credit_start.get(j,now)-now) for j in group}
                    finish[i]=reverse[i]+delay[i]+max(((min_work(e,size) if shared else 0.)+size)/capacities[e] for e in paths[i])
                    tail=floor
                    for j,t in finish.items():
                        g=by_id[j];service=solo[j]+delay[j]+reverse[j] if j==i else 0.
                        tail=max(tail,now+t+reverse[j]+pending_send[g.src]-service,
                                      now+t+pending_receive[g.dst]-service)
                    times=list(finish.values())
                    # 1e-9 us is far below the ns-3 timestamp resolution; prevent
                    # floating summation noise from replacing documented tie rules.
                    return ((round(tail,9),) if objective=='tail' else ())+(round(max(times),9),
                            round(max(times)-min(times),9) if alignment else 0.,
                            -round(max(work[e] for e in paths[i]),9) if shared else 0.,i)
                chosen=min(eligible,key=tail_key)
            elif not group:
                def anchor_key(i):
                    return (-max(work[e] for e in paths[i]) if shared else 0.,
                            -solo[i]-delay[i],i)
                chosen=min(eligible,key=anchor_key)
            else:
                span=max(estimates(group).values())
                def fill_key(i):
                    times=list(estimates(group,i).values());end=max(times)
                    return (end-span,end-min(times) if alignment else 0.,
                            -max(work[e] for e in paths[i]) if shared else 0.,i)
                chosen=min(eligible,key=fill_key)
            f=by_id[chosen];pending.remove(chosen);order.append(chosen)
            busy_send.add(f.src);busy_receive.add(f.dst);credit.add(chosen)
            records[chosen]={'reserve_us':now}
            service=solo[chosen]+delay[chosen]+reverse[chosen]
            pending_send[f.src]=max(0.,pending_send[f.src]-service)
            pending_receive[f.dst]=max(0.,pending_receive[f.dst]-service)
            credit_start[chosen]=now+reverse[chosen]
            heapq.heappush(events,(credit_start[chosen],'start',chosen))
        if events and events[0][0]<=now+1e-9:continue
        if not active:
            if not events:
                if pending:raise RuntimeError('no runnable message or future release')
                break
            now=events[0][0];continue
        rates=(fair_rates({i:paths[i] for i in active},capacities) if shared else
               {i:min(capacities[e] for e in paths[i]) for i in active})
        dt=min(remaining[i]/rates[i] for i in active)
        if events:dt=min(dt,max(0.,events[0][0]-now))
        for i in active:
            served=min(remaining[i],rates[i]*dt);remaining[i]-=served
            for e in paths[i]:work[e]=max(0.,work[e]-served/capacities[e])
        now+=dt
        finished=[i for i in active if remaining[i]<=max(1e-7,by_id[i].bytes*1e-12)]
        for i in finished:
            active.remove(i);remaining[i]=0.;records[i]['serialize_us']=now
            receive_release[by_id[i].dst]=now+delay[i]
            send_release[by_id[i].src]=now+delay[i]+reverse[i]
            heapq.heappush(events,(now+delay[i],'data',i))
            heapq.heappush(events,(now+delay[i]+reverse[i],'ack',i))
    return Plan.from_order(messages,order),dict(predicted_delivery_us=last_data,
        events=records,model='fluid planning approximation; NOT simulated performance',
        shared=shared,alignment=alignment,objective=objective)
