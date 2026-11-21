"""Offline endpoint-queue improvement, evaluated only by actual SimAI.

The neighborhood reverses a single adjacent send OR receive queue pair on an
observed dependency chain ending at the last delivery. Cycles are rejected.
Every other queue order is preserved. Each accepted pass strictly decreases
measured collective delivery time; neither convergence to a global optimum nor
performance on a different transport/seed is guaranteed. No splitting/barrier.
Gap mode proposes a later receive whose observed service duration fits the
receiver's sender-wait window after accounting for path propagation. This is a
proposal filter, NOT a guaranteed counterfactual: contention changes on replay.
"""
import argparse
from collections import defaultdict
import csv
import heapq
import json
from pathlib import Path
import time

from .model import Plan
from .simai import run


def queues(plan):
    by_id={f.id:f for f in plan.messages};result={'send':defaultdict(list),'receive':defaultdict(list)}
    for ident in plan.order:
        f=by_id[ident];result['send'][f.src].append(ident);result['receive'][f.dst].append(ident)
    return result


def swap_adjacent(plan,role,rank,a,b):
    q=queues(plan)
    if role not in q or rank not in q[role]:raise ValueError('unknown endpoint queue')
    target=q[role][rank]
    if a not in target or target.index(a)+1>=len(target) or target[target.index(a)+1]!=b:
        raise ValueError('exchange must be adjacent and in original order')
    index=target.index(a);target[index:index+2]=[b,a]
    return _from_queues(plan,q)


def insert_before(plan,role,rank,blocked,candidate):
    q=queues(plan)
    if role not in q or rank not in q[role]:raise ValueError('unknown endpoint queue')
    target=q[role][rank]
    if blocked not in target or candidate not in target or target.index(blocked)>=target.index(candidate):
        raise ValueError('insertion must promote a later message')
    target.remove(candidate);target.insert(target.index(blocked),candidate)
    return _from_queues(plan,q)


def promote_message(plan,role,rank,blocked,candidate):
    """Promote a whole message in total order, changing BOTH endpoint queues.

    Reuses a critical queue edge to select the pair, but unlike a one-port edit
    it permits the corresponding change at the other endpoint. All other
    messages retain relative order, so the resulting dependencies are acyclic.
    """
    if role!='global':
        q=queues(plan)
        if role not in q or rank not in q[role] or blocked not in q[role][rank] or candidate not in q[role][rank]:
            raise ValueError('promotion must refer to a real endpoint queue')
    order=list(plan.order)
    if order.index(candidate)<=order.index(blocked):raise ValueError('promotion must move a later message earlier')
    order.remove(candidate);order.insert(order.index(blocked),candidate)
    return Plan.from_order(plan.messages,order)


def _from_queues(plan,q):
    edges={i:set() for i in plan.order};degree={i:0 for i in plan.order}
    for ports in q.values():
        for queue in ports.values():
            for x,y in zip(queue,queue[1:]):
                if y not in edges[x]:edges[x].add(y);degree[y]+=1
    # Preserve old total order when the new dependencies leave a choice.
    position={ident:k for k,ident in enumerate(plan.order)}
    ready=[(position[i],i) for i,d in degree.items() if d==0];heapq.heapify(ready);order=[]
    while ready:
        _,ident=heapq.heappop(ready);order.append(ident)
        for child in sorted(edges[ident]):
            degree[child]-=1
            if degree[child]==0:heapq.heappush(ready,(position[child],child))
    if len(order)!=len(plan.order):return None
    return Plan.from_order(plan.messages,order)


def read_events(root):
    root=Path(root);d=json.loads((root/'plan.json').read_text())
    mapping={int(k):v for k,v in d['wire_to_message'].items()};events={}
    with (root/'commands.csv.events.csv').open() as f:
        for row in csv.DictReader(f):
            if int(row['flow_id']) in mapping:
                events[mapping[int(row['flow_id'])],row['event']]=int(row['time_ns'])
    return events


def critical_moves(plan,events):
    if not plan.messages:return []
    by_id={f.id:f for f in plan.messages};ident=max(plan.order,key=lambda i:(events[i,'data_ready'],-i))
    moves=[]
    while ident is not None:
        f=by_id[ident];send=plan.send_after[ident];recv=plan.receive_after[ident]
        if events.get((send,'ack_done'),0)>=events[ident,'credit_arrive']:
            if send is not None:moves.append(('send',f.src,send,ident))
            ident=send
        else:
            if recv is not None:moves.append(('receive',f.dst,recv,ident))
            ident=recv
    return moves


def gap_moves(plan,network,events):
    """Receiver gap candidates; times in ns. Keep complete messages intact.

    For blocked b, receiver available time r is its old credit_send. For later
    f, estimated new data finish is max(sender_ready[f], r+reverse_delay[f])
    + observed(data_ready[f]-data_release[f]). Require room to deliver b's new
    credit before b's old start. All changing congestion is validated in ns-3.
    """
    by_id={f.id:f for f in plan.messages};candidates=[]
    for dst,queue in queues(plan)['receive'].items():
        for index,blocked in enumerate(queue):
            b=by_id[blocked];ready=events.get((plan.send_after[blocked],'ack_done'),0)
            if ready<=events[blocked,'credit_arrive']:continue
            window=events[blocked,'credit_send'];old_start=events[blocked,'data_release']
            for ident in queue[index+1:]:
                f=by_id[ident];source=events.get((plan.send_after[ident],'ack_done'),0)
                finish=max(source,window+network.delay_us(dst,f.src)*1000)
                finish+=events[ident,'data_ready']-events[ident,'data_release']
                if finish+network.delay_us(dst,b.src)*1000<=old_start:
                    advance=events[ident,'data_ready']-finish
                    candidates.append((-advance,('receive',dst,blocked,ident)))
    return [move for _,move in sorted(candidates)]


def send_gap_moves(plan,events):
    """Fill a sender's wait for a receiver credit with a later WHOLE message.

    Symmetric to receive-gap insertion, but the sender must be released by ACK,
    not by data receipt. This filter uses the previous trace; it is no guarantee
    under changed contention. Only actual full-operator improvement is accepted.
    """
    candidates=[]
    for src,queue in queues(plan)['send'].items():
        for index,blocked in enumerate(queue):
            available=events.get((plan.send_after[blocked],'ack_done'),0)
            old_start=events[blocked,'data_release']
            if available>=events[blocked,'credit_arrive']:continue
            for ident in queue[index+1:]:
                start=max(available,events[ident,'credit_arrive'])
                finish_ack=start+events[ident,'ack_done']-events[ident,'data_release']
                if finish_ack<=old_start:
                    candidates.append((-(events[ident,'ack_done']-finish_ack),('send',src,blocked,ident)))
    return [move for _,move in sorted(candidates)]


def duplex_gap_moves(plan,network,events):
    # Interleave the two roles; a development evaluation cap must not always
    # exhaust the receiver list before seeing any sender candidate.
    receive=gap_moves(plan,network,events);send=send_gap_moves(plan,events);moves=[]
    for index in range(max(len(receive),len(send))):
        if index<len(receive):moves.append(receive[index])
        if index<len(send):moves.append(send[index])
    return moves


def resource_promotions(plan,network,events):
    """Endpoint-critical moves plus potentially contending path neighbors.

    Data execution intervals overlapping on a directed resource are a candidate
    filter, NOT proof of queue causality. Promote a critical message before an
    earlier potentially interfering message; full ns-3 evaluation decides.
    """
    endpoint=critical_moves(plan,events)
    if not plan.messages:return []
    tail=max(plan.order,key=lambda i:(events[i,'data_ready'],-i))
    critical=set([tail]+[i for move in endpoint for i in move[2:]])
    by_id={f.id:f for f in plan.messages};position={i:k for k,i in enumerate(plan.order)}
    index=network.resources(plan.messages);candidates={}
    for i in critical:
        f=by_id[i]
        neighbors=set(j for edge in network.path(f.src,f.dst) for j in index[edge])
        for j in neighbors:
            if position[j]>=position[i]:continue
            overlap=min(events[i,'data_ready'],events[j,'data_ready'])-max(events[i,'data_release'],events[j,'data_release'])
            if overlap<=0:continue
            move=('global',-1,j,i);candidates[move]=-overlap
    resource=[move for move in sorted(candidates,key=lambda m:(candidates[m],m))]
    moves=[];seen=set()
    for k in range(max(len(endpoint),len(resource))):
        for group in (endpoint,resource):
            if k<len(group) and group[k] not in seen:moves.append(group[k]);seen.add(group[k])
    return moves


def optimize(seed,network,output,binary,*,max_evaluations=64,timeout_s=120,neighborhood='critical',rank_order=None):
    if neighborhood not in ('critical','critical-promotion','resource-promotion','cyclic-group','gap','duplex-gap'):raise ValueError('unknown neighborhood')
    if type(max_evaluations) is not int or max_evaluations<1:raise ValueError('invalid evaluation cap')
    if neighborhood=='cyclic-group':
        from .cyclic import groups,critical_group_moves,promote_group
        rank_order=tuple(range(network.gpu_count)) if rank_order is None else tuple(rank_order)
        groups(seed,rank_order)  # fail before creating any run if representation mismatches
    root=Path(output).resolve();root.mkdir(parents=True,exist_ok=False)
    cache={};evaluations=[];accepted=[];passes=[];started=time.monotonic()
    state=dict(status='running',neighborhood=neighborhood,
               max_evaluations=max_evaluations,search_rule='steepest strict improvement; best evaluated prefix at cap',
               evaluations=evaluations,accepted=accepted,passes=passes)
    if neighborhood=='cyclic-group':state['virtual_rank_order']=rank_order
    def save():
        state['wall_s']=time.monotonic()-started
        (root/'search.json').write_text(json.dumps(state,indent=2)+'\n')
    def evaluate(plan,move):
        key=plan.dependency_key()
        if key in cache:return cache[key]
        run_dir=root/f'eval-{len(evaluations):04d}'
        result=run(plan,network,run_dir,binary,timeout_s=timeout_s)
        record=dict(index=len(evaluations),path=str(run_dir.relative_to(root)),move=move,
                    status=result['status'],delivery_ns=result.get('delivery_ns'))
        evaluations.append(record);cache[key]=record;save()
        print(json.dumps(record),flush=True)
        return record
    current=seed;current_eval=evaluate(current,None)
    if current_eval['status']!='success':
        state.update(status='failed',stop_reason='seed simulation failed');save();return state
    state['initial_delivery_ns']=current_eval['delivery_ns']
    while True:
        events=read_events(root/current_eval['path'])
        moves=(critical_moves(current,events) if neighborhood in ('critical','critical-promotion') else
               critical_group_moves(current,rank_order,critical_moves(current,events)) if neighborhood=='cyclic-group' else
               resource_promotions(current,network,events) if neighborhood=='resource-promotion' else
               duplex_gap_moves(current,network,events) if neighborhood=='duplex-gap' else gap_moves(current,network,events))
        best=current;best_eval=current_eval;tested=0;cycles=0;exhausted=True;failures=0
        for move in moves:
            edit=(swap_adjacent if neighborhood=='critical' else
                  promote_message if neighborhood in ('critical-promotion','resource-promotion') else insert_before)
            candidate=(promote_group(current,rank_order,*move[2:]) if neighborhood=='cyclic-group' else edit(current,*move))
            if candidate is None:cycles+=1;continue
            if candidate.dependency_key() not in cache and len(evaluations)>=max_evaluations:
                exhausted=False;break
            result=evaluate(candidate,move);tested+=1
            if result['status']!='success':failures+=1;continue
            if result['delivery_ns']<best_eval['delivery_ns']:best=candidate;best_eval=result
        passes.append(dict(base_eval=current_eval['index'],moves=len(moves),tested=tested,
                           cycles_rejected=cycles,failed=failures,fully_evaluated=exhausted,
                           selected_eval=best_eval['index']))
        if best is not current:
            accepted.append(dict(from_eval=current_eval['index'],to_eval=best_eval['index'],
                                 before_ns=current_eval['delivery_ns'],after_ns=best_eval['delivery_ns']))
            current=best;current_eval=best_eval
        else:
            reason=('evaluation_cap' if not exhausted else
                    'incomplete_neighborhood_due_to_failures' if failures else f'{neighborhood}_neighborhood_stationary')
            break
        if not exhausted:reason='evaluation_cap';break
        save()
    state.update(status='completed',stop_reason=reason,best_eval=current_eval['index'],
                 best_path=current_eval['path'],best_delivery_ns=current_eval['delivery_ns'])
    (root/'best-plan.json').write_text(json.dumps(dict(messages=[vars(f) for f in current.messages],
                                                     order=current.order),indent=2)+'\n')
    save();return state


def main():
    from .experiments import demand,pilot_network
    from .scheduler import construct,baseline
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--topology',choices=('tree','leaf-spine'),required=True)
    p.add_argument('--load',choices=('balanced','zipf'),required=True)
    p.add_argument('--seed',type=int,default=7);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--initial',choices=('resource','no-resource','spreadout'),default='resource')
    p.add_argument('--max-evaluations',type=int,default=64)
    p.add_argument('--neighborhood',choices=('critical','critical-promotion','resource-promotion','gap','duplex-gap'),default='critical')
    p.add_argument('--binary',default='/home/julius/fast/SimAI/bin/SimAI_simulator');args=p.parse_args()
    n=pilot_network(args.topology);flows=demand(16,1048576,args.load,args.seed)
    initial=baseline(flows,'spreadout',16) if args.initial=='spreadout' else construct(flows,n,shared=args.initial!='no-resource')
    result=optimize(initial,n,args.output,args.binary,max_evaluations=args.max_evaluations,neighborhood=args.neighborhood)
    print(json.dumps({k:v for k,v in result.items() if k not in ('evaluations','accepted','passes')},indent=2))
    return 0 if result['status']=='completed' else 1


if __name__=='__main__':raise SystemExit(main())
