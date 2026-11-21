"""Stage-free plan construction. Estimates guide ordering, never report speedup.

Temporary compatible groups are a construction device only; the returned Plan
contains just whole-message endpoint dependencies, not execution rounds.
"""
from .model import Plan


def completion_estimates(messages,network,*,shared=True):
    messages=tuple(messages)
    work=network.work_us(messages) if shared else None
    return {f.id:network.delay_us(f.src,f.dst)+max(
        (work[e] if shared else f.bytes*.008/network.links[e].gbps
         for e in network.path(f.src,f.dst)),default=0.) for f in messages}


def baseline(messages,kind,n,*,rank_order=None):
    messages=tuple(messages)
    if type(n) is not int or n<=0 or any(max(f.src,f.dst)>=n for f in messages):
        raise ValueError('invalid baseline endpoint count')
    if rank_order is None:rank_order=tuple(range(n))
    if len(rank_order)!=n or set(rank_order)!=set(range(n)):raise ValueError('invalid rank permutation')
    position={rank:i for i,rank in enumerate(rank_order)}
    if kind=='spreadout':key=lambda f:((position[f.dst]-position[f.src])%n,position[f.src],f.id)
    elif kind=='small-first':key=lambda f:(f.bytes,f.id)
    elif kind=='large-first':key=lambda f:(-f.bytes,f.id)
    else:raise ValueError('unknown baseline')
    return Plan.from_order(messages,[f.id for f in sorted(messages,key=key)])


def interleaved_ranks(network,*,across_racks=True):
    """Static cyclic-order sensitivity control; not a new collective primitive.

    Derive server/ToR groups from physical adjacency. Interleave local GPU index,
    then server index within a rack, then racks; all payload pairs stay intact.
    """
    n=network.gpu_count;groups={}
    for gpu in range(n):
        neighbors=network.adjacency[gpu]
        if len(neighbors)!=1:raise ValueError('requires factory server-local topology')
        groups.setdefault(neighbors[0],[]).append(gpu)
    racks={}
    for local in groups:
        uplinks=[node for node in network.adjacency[local] if node>=n]
        if len(uplinks)!=1:raise ValueError('requires one physical server uplink')
        racks.setdefault(uplinks[0],[]).append(local)
    servers={local:(r,s) for r,tor in enumerate(sorted(racks)) for s,local in enumerate(sorted(racks[tor]))}
    keys={}
    for local,gpus in groups.items():
        r,s=servers[local]
        for k,gpu in enumerate(sorted(gpus)):keys[gpu]=(k,s,r) if across_racks else (k,r,s)
    return tuple(sorted(range(n),key=keys.__getitem__))


def construct(messages,network,*,alignment=True,shared=True,anchor_shared=None,fill_shared=None):
    # Orthogonal ablation switches; old shared flag retains exactly its behavior.
    anchor_shared=shared if anchor_shared is None else anchor_shared
    fill_shared=shared if fill_shared is None else fill_shared
    messages=tuple(messages)
    # Validate before a set/dict can silently deduplicate input.
    Plan.from_order(messages,[f.id for f in messages])
    solo=completion_estimates(messages,network,shared=False)
    pending={f.id:f for f in messages};order=[]
    while pending:
        work=network.work_us(pending.values()) if anchor_shared or fill_shared else {}
        if anchor_shared:
            heavy=min(work,key=lambda e:(-work[e],e))
            anchors=[f for f in pending.values() if heavy in network.path(f.src,f.dst)]
        else:anchors=list(pending.values())
        anchor=min(anchors,key=lambda f:(-solo[f.id],f.id))
        group=[anchor];send={anchor.src};receive={anchor.dst};chosen={anchor.id}
        span=solo[anchor.id];minimum_solo=span
        while True:
            candidates=[f for f in pending.values() if f.id not in chosen and f.src not in send and f.dst not in receive]
            if not candidates:break
            def rank(f):
                if not fill_shared:
                    # Isolated costs are independent of the group. This is
                    # algebraically the same ranking as re-evaluating every
                    # existing message for every candidate, without O(|group|)
                    # repeated path scans. It does not change the heuristic.
                    new_span=max(span,solo[f.id])
                    spread=new_span-min(minimum_solo,solo[f.id]) if alignment else 0.
                    return (new_span-span,spread,0.,f.id)
                estimates=completion_estimates(group+[f],network,shared=fill_shared)
                times=list(estimates.values());new_span=max(times)
                spread=new_span-min(times) if alignment else 0.
                # Secondary progress score, all terms in microseconds. Shared
                # groups may overlap; this is a heuristic, not a makespan bound.
                pressure=sum(work[e] for e in network.path(f.src,f.dst)) if fill_shared else 0.
                return (new_span-span,spread,-pressure,f.id)
            selected=min(candidates,key=rank)
            group.append(selected);chosen.add(selected.id);send.add(selected.src);receive.add(selected.dst)
            minimum_solo=min(minimum_solo,solo[selected.id])
            span=(max(completion_estimates(group,network,shared=True).values()) if fill_shared
                  else max(span,solo[selected.id]))
        order.extend(f.id for f in group)
        for f in group:del pending[f.id]
    return Plan.from_order(messages,order)
