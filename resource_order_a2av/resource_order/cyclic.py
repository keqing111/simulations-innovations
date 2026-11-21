"""Coordinated ordering moves on a cyclic whole-message cover.

Groups are an offline representation, NOT runtime rounds/barriers. Every nonzero
pair appears once. A group contains at most one send and receive per GPU, but
payload sizes remain independent. No BvN, padding or common quota is used.
"""
from .model import Plan


def groups(plan,rank_order):
    n=len(rank_order)
    if set(rank_order)!=set(range(n)):raise ValueError('invalid virtual rank permutation')
    position={rank:i for i,rank in enumerate(rank_order)}
    phase={f.id:(position[f.dst]-position[f.src])%n for f in plan.messages}
    order=[];buckets={}
    for ident in plan.order:
        k=phase[ident]
        if not order or order[-1]!=k:
            if k in buckets:raise ValueError('input is not a contiguous cyclic cover')
            order.append(k);buckets[k]=[]
        buckets[k].append(ident)
    return phase,order,buckets


def promote_group(plan,rank_order,before,moving):
    _,order,buckets=groups(plan,rank_order)
    if before not in buckets or moving not in buckets or order.index(moving)<=order.index(before):
        raise ValueError('group promotion must move a later group before an earlier one')
    order.remove(moving);order.insert(order.index(before),moving)
    return Plan.from_order(plan.messages,[i for k in order for i in buckets[k]])


def critical_group_moves(plan,rank_order,endpoint_moves):
    phase,_,_=groups(plan,rank_order);seen=set();result=[]
    for _,_,before,moving in endpoint_moves:
        pair=phase[before],phase[moving]
        if pair[0]!=pair[1] and pair not in seen:
            result.append(('group',len(rank_order),*pair));seen.add(pair)
    return result


def order_by_work(plan,rank_order,network,*,resource=True,descending=True):
    """Longest-work-first cyclic cover (cheap seed/control, not an optimizer).

    Resource score is max directed-link payload work plus the maximum path
    propagation among this group's users of that resource. Volume control uses
    max message bytes. Sorting does not enforce synchronized group execution.
    """
    _,order,buckets=groups(plan,rank_order);by_id={f.id:f for f in plan.messages};scores={}
    for k,ids in buckets.items():
        fs=[by_id[i] for i in ids]
        if not resource:scores[k]=max(f.bytes for f in fs);continue
        work=network.work_us(fs);delay={e:0. for e in work}
        for f in fs:
            tau=network.delay_us(f.src,f.dst)
            for e in network.path(f.src,f.dst):delay[e]=max(delay[e],tau)
        scores[k]=max(work[e]+delay[e] for e in work)
    ordered=sorted(order,key=lambda k:((-1 if descending else 1)*round(scores[k],9),order.index(k)))
    return Plan.from_order(plan.messages,[i for k in ordered for i in buckets[k]]),scores
