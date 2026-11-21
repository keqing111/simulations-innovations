"""Explicit multi-GPU physical fabrics; no dependencies on historical planners."""
from .model import Link, Network


def build_topology(kind, racks=4, servers_per_rack=4, gpus_per_server=8, overrides=None):
    if kind not in ('tree','leaf-spine'):raise ValueError('unknown topology')
    if any(type(v) is not int or v<=0 for v in (racks,servers_per_rack,gpus_per_server)):
        raise ValueError('invalid fabric dimensions')
    if kind=='tree' and racks%2:raise ValueError('tree pairs two racks per aggregation switch')
    servers=racks*servers_per_rack;p=servers*gpus_per_server
    local=tuple(range(p,p+servers));tor=tuple(range(p+servers,p+servers+racks))
    links=[Link(g,local[g//gpus_per_server],8/.0023,.1) for g in range(p)]
    links += [Link(node,tor[s//servers_per_rack],400,.7) for s,node in enumerate(local)]
    begin=p+servers+racks
    if kind=='tree':
        upper=tuple(range(begin,begin+racks//2));core=begin+racks//2
        links += [Link(node,upper[r//2],800,.7) for r,node in enumerate(tor)]
        links += [Link(node,core,800,.7) for node in upper]
    else:
        upper=(begin,begin+1)
        links += [Link(node,spine,400,.7) for node in tor for spine in upper]
    changes={}
    for (a,b),value in (overrides or {}).items():
        key=tuple(sorted((a,b)))
        if key in changes:raise ValueError('duplicate undirected override')
        changes[key]=value
    known={tuple(sorted((e.src,e.dst))) for e in links}
    if changes.keys()-known:raise ValueError('override targets nonexistent link')
    links=[Link(e.src,e.dst,*changes.get(tuple(sorted((e.src,e.dst))),(e.gbps,e.delay_us))) for e in links]
    return Network(p,links,gpus_per_server)
