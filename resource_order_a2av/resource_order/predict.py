"""Fixed-queue fluid predictor, used to audit planning errors, NOT performance.

Unlike greedy admission, this follows exported receive queues exactly: credit
can travel while a sender is busy. Payload shares every directed path resource;
ACK and credit consume propagation time but no link capacity in this surrogate.
"""
import heapq

from .readiness import fair_rates


def evaluate_cohorts(plan,network):
    """Diagnostic coupled service/DAG estimate, NOT a makespan bound.

    Canonical ASAP layers depend on endpoint queues, not arbitrary cover labels.
    On one link, simultaneous equal-share messages of sizes x_j give message i
    service time sum_j min(x_i,x_j)/B: short messages release their share early.
    Take the maximum along i's path and propagate that time through its endpoint
    dependencies. Different layers can overlap in reality; this approximation
    does not model their cross-layer contention, queueing or congestion control.
    """
    from .cover import queue_cover
    cover=queue_cover(plan,network.gpu_count)
    pairs={(f.src,f.dst):f for f in plan.messages};send=[0.]*network.gpu_count;recv=send.copy();finish=0.
    for row in cover:
        flows=[pairs[s,d] for s,d in enumerate(row) if d is not None];resources={};service={f.id:0. for f in flows}
        for f in flows:
            for e in network.path(f.src,f.dst):resources.setdefault(e,[]).append(f)
        for edge,users in resources.items():
            users.sort(key=lambda f:(f.bytes,f.id));prefix=0
            for index,f in enumerate(users):
                work=prefix+(len(users)-index)*f.bytes;prefix+=f.bytes
                service[f.id]=max(service[f.id],work*.008/network.links[edge].gbps)
        for f in flows:
            forward=network.delay_us(f.src,f.dst);reverse=network.delay_us(f.dst,f.src)
            done=max(send[f.src],recv[f.dst]+reverse)+forward+service[f.id]
            recv[f.dst]=done;send[f.src]=done+reverse;finish=max(finish,done)
    return dict(predicted_ns=finish*1000,cohorts=len(cover),
                model='ASAP equal-share cohort service propagated through endpoint DAG; diagnostic surrogate, NOT ns-3')


def evaluate(plan,network,*,shared=True):
    by_id={f.id:f for f in plan.messages}
    paths={i:network.path(f.src,f.dst) for i,f in by_id.items()}
    delay={i:network.delay_us(f.src,f.dst) for i,f in by_id.items()}
    reverse={i:network.delay_us(f.dst,f.src) for i,f in by_id.items()}
    capacity={e:link.gbps/.008 for e,link in network.links.items()}
    send_next={prev:i for i,prev in plan.send_after.items() if prev is not None}
    receive_next={prev:i for i,prev in plan.receive_after.items() if prev is not None}
    remaining={i:float(f.bytes) for i,f in by_id.items()}
    credits=set();acked=set();started=set();active=set();events=[];trace={};now=0.

    def grant(i):
        trace[i,'credit_send']=now*1000
        heapq.heappush(events,(now+reverse[i],'credit_arrive',i))

    def start(i):
        if i not in started and i in credits and (plan.send_after[i] is None or plan.send_after[i] in acked):
            started.add(i);active.add(i);trace[i,'data_release']=now*1000

    for i in plan.order:
        if plan.receive_after[i] is None:grant(i)
    while events or active:
        while events and events[0][0]<=now+1e-9:
            _,kind,i=heapq.heappop(events);trace[i,kind]=now*1000
            if kind=='credit_arrive':credits.add(i);start(i)
            elif kind=='data_ready':
                if i in receive_next:grant(receive_next[i])
            elif kind=='ack_done':
                acked.add(i)
                if i in send_next:start(send_next[i])
        if not active:
            if events:now=events[0][0]
            continue
        rates=(fair_rates({i:paths[i] for i in active},capacity) if shared else
               {i:min(capacity[e] for e in paths[i]) for i in active})
        dt=min(remaining[i]/rates[i] for i in active)
        if events:dt=min(dt,max(0.,events[0][0]-now))
        for i in active:remaining[i]=max(0.,remaining[i]-rates[i]*dt)
        now+=dt
        finished=[i for i in active if remaining[i]<=max(1e-7,by_id[i].bytes*1e-12)]
        for i in finished:
            active.remove(i);remaining[i]=0.
            heapq.heappush(events,(now+delay[i],'data_ready',i))
            heapq.heappush(events,(now+delay[i]+reverse[i],'ack_done',i))
    if len(acked)!=len(by_id):raise ValueError('queue dependency deadlock or incomplete plan')
    return dict(predicted_ns=max((v for (i,k),v in trace.items() if k=='data_ready'),default=0.),
                events=trace,model='fixed-queue fluid surrogate; NOT ns-3 performance')
