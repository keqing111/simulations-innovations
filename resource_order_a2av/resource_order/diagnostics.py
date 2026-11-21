"""Read-only diagnosis of complete message dependencies and ns-3 port events.

Waiting sums are over messages, NOT additive pieces of collective makespan.
Observed predecessor chains explain event dependencies, not counterfactuals.
Port inactivity is not classified as idle-with-demand without eligibility proof.
"""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path

from .model import Message,Plan
from .simai import validate_events,validate_fct


def demand_bounds(messages,network):
    """Necessary service bounds for this credit/ACK endpoint protocol, in us.

    Each receive posts its credit only after preceding data completion. A send
    slot waits for ACK, except that the final ACK is outside delivery makespan.
    Bounds are MAX-combined, never added across shared resources/endpoints.
    """
    messages=tuple(messages);send=defaultdict(float);recv=defaultdict(float)
    max_reverse=defaultdict(float);prop=defaultdict(float);serial=defaultdict(float)
    for f in messages:
        path=network.path(f.src,f.dst)
        service=f.bytes*.008/min(network.links[e].gbps for e in path)
        forward=network.delay_us(f.src,f.dst);reverse=network.delay_us(f.dst,f.src)
        send[f.src]+=service+forward+reverse;recv[f.dst]+=service+forward+reverse
        max_reverse[f.src]=max(max_reverse[f.src],reverse)
        prop[f.dst]+=forward+reverse;serial[f.dst]+=service
    resource=max(network.work_us(messages).values(),default=0.)
    sender=max((w-max_reverse[s] for s,w in send.items()),default=0.)
    receiver=max(recv.values(),default=0.)
    return dict(resource_us=resource,sender_us=sender,receiver_us=receiver,
                max_receive_propagation_us=max(prop.values(),default=0.),
                max_receive_isolated_serialization_us=max(serial.values(),default=0.),
                lower_bound_us=max(resource,sender,receiver))


def dependency_depth(plan):
    depth={}
    for ident in plan.order:
        depth[ident]=1+max(depth.get(plan.send_after[ident],0),depth.get(plan.receive_after[ident],0))
    return depth


def merge_intervals(intervals):
    result=[]
    for start,end in sorted(intervals):
        if result and start<=result[-1][1]:result[-1]=(result[-1][0],max(end,result[-1][1]))
        else:result.append((start,end))
    return result


def analyze(root):
    root=Path(root);saved=json.loads((root/'result.json').read_text())
    if saved['status']!='success':raise ValueError('only validated completed runs may be diagnosed')
    d=json.loads((root/'plan.json').read_text())
    plan=Plan.from_order([Message(**f) for f in d['messages']],d['order'])
    mapping={int(k):v for k,v in d['wire_to_message'].items()};by_id={f.id:f for f in plan.messages}
    with (root/'commands.csv.events.csv').open() as f:rows=list(csv.DictReader(f))
    metrics=validate_events(plan,mapping,rows)
    validate_fct(plan,mapping,(root/'fct.txt').read_text().splitlines())
    begin=next(int(row['time_ns']) for row in rows if row['event']=='begin')
    event={}
    for row in rows:
        if int(row['flow_id']) in mapping:event[mapping[int(row['flow_id'])],row['event']]=int(row['time_ns'])-begin
    depth=dependency_depth(plan);messages=[]
    for ident in plan.order:
        f=by_id[ident];prev=plan.send_after[ident]
        sender=event.get((prev,'ack_done'),0);credit=event[ident,'credit_arrive'];start=event[ident,'data_release']
        messages.append(dict(id=ident,src=f.src,dst=f.dst,bytes=f.bytes,depth=depth[ident],
            send_predecessor=prev,receive_predecessor=plan.receive_after[ident],
            sender_ready_ns=sender,credit_arrive_ns=credit,start_ns=start,data_ns=event[ident,'data_ready'],
            ack_ns=event[ident,'ack_done'],wait_for_sender_ns=max(0,sender-credit),
            wait_for_credit_ns=max(0,credit-sender),extra_release_wait_ns=start-max(sender,credit)))
    tail=max(messages,key=lambda r:r['data_ns']);chain=[];ident=tail['id']
    indexed={r['id']:r for r in messages}
    while ident is not None:
        row=indexed[ident];send=row['send_predecessor'];recv=row['receive_predecessor']
        chain.append(ident)
        ident=send if row['sender_ready_ns']>=row['credit_arrive_ns'] else recv
    ports={};indices=defaultdict(int);rates={}
    for line in (root/'topology.txt').read_text().splitlines()[2:]:
        u,v,rate,_,_=line.split();u=int(u);v=int(v)
        for a,b in ((u,v),(v,u)):
            indices[a]+=1;ports[a,indices[a]]=(a,b);rates[a,b]=float(rate.removesuffix('Gbps'))
    intervals=defaultdict(list);payload=defaultdict(int)
    with (root/'native_wan.csv').open() as f:
        for row in csv.DictReader(f):
            if int(row['kind'])!=0 or int(row['payload_bytes'])<=0:continue
            edge=ports[int(row['node_id']),int(row['port_id'])]
            start=int(row['start_ns'])-begin;end=start+int(row['duration_ns'])
            intervals[edge].append((start,end));payload[edge]+=int(row['payload_bytes'])
    resources=[]
    next_hop={(int(a),int(d)):int(v) for a,d,v in
              (line.split(',') for line in (root/'routes.csv').read_text().splitlines())}
    demand=defaultdict(int)
    for flow in plan.messages:
        node=flow.src;seen=set()
        while node!=flow.dst:
            if node in seen:raise ValueError('route loop in saved input')
            seen.add(node);neighbor=next_hop[node,flow.dst]
            demand[node,neighbor]+=flow.bytes;node=neighbor
    for edge,items in intervals.items():
        merged=merge_intervals(items);busy=sum(b-a for a,b in merged)
        resources.append(dict(src=edge[0],dst=edge[1],gbps=rates[edge],payload_bytes=payload[edge],
            required_bytes=demand[edge],
            busy_ns=busy,first_ns=merged[0][0],last_ns=merged[-1][1],
            internal_nonpayload_ns=merged[-1][1]-merged[0][0]-busy,
            payload_service_bound_ns=demand[edge]*8/rates[edge]))
    resources.sort(key=lambda r:-r['payload_service_bound_ns'])
    summary=dict(run=str(root),**metrics,depth=max(depth.values(),default=0),
                 tail_message=tail['id'],observed_predecessor_chain=chain,
                 wait_for_sender_sum_ns=sum(r['wait_for_sender_ns'] for r in messages),
                 wait_for_credit_sum_ns=sum(r['wait_for_credit_ns'] for r in messages),
                 max_extra_release_wait_ns=max(r['extra_release_wait_ns'] for r in messages))
    return summary,messages,resources


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('runs',nargs='+',type=Path)
    p.add_argument('--output',required=True,type=Path);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False);summaries=[]
    for root in args.runs:
        summary,messages,resources=analyze(root);summaries.append(summary)
        for name,rows in [('messages',messages),('resources',resources)]:
            with (args.output/f'{root.name}-{name}.csv').open('w') as f:
                w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    (args.output/'summary.json').write_text(json.dumps(summaries,indent=2)+'\n')
    print(json.dumps(summaries,indent=2))


if __name__=='__main__':main()
