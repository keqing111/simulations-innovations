"""Alternating-cycle exchanges of a whole-message matching cover.

The cover labels possible GPU pairs, including absent pairs, but absent pairs
never produce messages or network bytes. No matrix coefficient is extracted.
Changing two colors along a closed alternating cycle preserves both matchings
and the set of covered pairs; payload sizes are unrestricted and never split.
"""
from collections import defaultdict

from .model import Plan


def initial_cover(rank_order):
    ranks=tuple(rank_order);n=len(ranks)
    if n<2 or any(type(x) is not int for x in ranks) or set(ranks)!=set(range(n)):
        raise ValueError('invalid GPU permutation')
    cover=[]
    for offset in range(1,n):
        row=[None]*n
        for k,src in enumerate(ranks):row[src]=ranks[(k+offset)%n]
        cover.append(tuple(row))
    return tuple(cover)


def cover_plan(messages,cover,rank_order):
    messages=tuple(messages);lookup={(f.src,f.dst):f.id for f in messages}
    return Plan.from_order(messages,[lookup[src,row[src]] for row in cover for src in rank_order
                                    if (src,row[src]) in lookup])


def queue_cover(plan,n):
    """ASAP partial matchings preserving BOTH endpoint predecessor queues exactly.

    None is an unused offline slot, never a dummy message. More layers do not
    add messages, launches, credits, or global barriers to the runtime.
    """
    by_id={f.id:f for f in plan.messages};levels={};rows=[]
    for ident in plan.order:
        f=by_id[ident]
        if not 0<=f.src<n or not 0<=f.dst<n:raise ValueError('endpoint outside cover')
        level=1+max((levels[p] for p in (plan.send_after[ident],plan.receive_after[ident])
                     if p is not None),default=-1)
        while len(rows)<=level:rows.append([None]*n)
        if rows[level][f.src] is not None or f.dst in rows[level]:
            raise ValueError('dependency layer is not a matching')
        rows[level][f.src]=f.dst;levels[ident]=level
    result=tuple(tuple(row) for row in rows) or ((None,)*n,)
    if cover_plan(plan.messages,result,range(n)).dependency_key()!=plan.dependency_key():
        raise ValueError('layer conversion changed endpoint queues')
    return result


def alternating_components(first,second):
    """Source masks of whole alternating paths/cycles in two partial matchings."""
    n=len(first)
    if len(second)!=n:raise ValueError('different matching dimensions')
    graph=defaultdict(set);edges=set()
    for row in (first,second):
        used=set()
        for src,dst in enumerate(row):
            if dst is None:continue
            if type(dst) is not int or not 0<=dst<n or dst in used:
                raise ValueError('invalid partial matching')
            if (src,dst) in edges:raise ValueError('pair appears in both groups')
            used.add(dst);edges.add((src,dst))
            graph[src].add(n+dst);graph[n+dst].add(src)
    remaining=set(graph);components=[]
    while remaining:
        todo=[min(remaining)];seen=set()
        while todo:
            v=todo.pop()
            if v in seen:continue
            seen.add(v);todo.extend(graph[v]-seen)
        remaining-=seen;components.append(tuple(sorted(v for v in seen if v<n)))
    return components


def alternating_cycles(first,second):
    n=len(first)
    if len(second)!=n or set(first)!=set(range(n)) or set(second)!=set(range(n)):
        raise ValueError('both colors must be perfect matchings')
    inverse={dst:src for src,dst in enumerate(first)};remaining=set(range(n));cycles=[]
    while remaining:
        src=min(remaining);cycle=[]
        while src in remaining:
            remaining.remove(src);cycle.append(src);src=inverse[second[src]]
        cycles.append(tuple(cycle))
    return cycles


def exchange(cover,a,b,cycle,*,partial=False):
    if a==b or not 0<=a<len(cover) or not 0<=b<len(cover):raise ValueError('invalid color pair')
    chosen=set(cycle)
    components=alternating_components if partial else alternating_cycles
    if len(chosen)!=len(cycle) or not chosen or not any(chosen==set(c) for c in components(cover[a],cover[b])):
        raise ValueError('exchange must use one complete alternating cycle')
    first=list(cover[a]);second=list(cover[b])
    for src in cycle:first[src],second[src]=second[src],first[src]
    result=list(cover);result[a]=tuple(first);result[b]=tuple(second)
    return tuple(result)


def proposals(messages,cover,network,*,positive_only=True,pairs=None,partial=False):
    """Return strict decreases of sum(group maximum resource work), largest first.

    That sum is a SYNCHRONOUS capacity-work proxy, not an asynchronous makespan
    bound. It ranks proposals only; ns-3 must validate actual communication time.
    Empty colors are omitted from comparisons: splitting one loaded color into
    an empty one cannot reduce the sum of infinity norms of nonnegative loads.
    """
    edge_work={(f.src,f.dst):tuple((e,f.bytes*.008/network.links[e].gbps)
                                 for e in network.path(f.src,f.dst)) for f in messages}
    work=[]
    for row in cover:
        load=defaultdict(float)
        for src,dst in enumerate(row):
            for edge,value in edge_work.get((src,dst),()):load[edge]+=value
        work.append(dict(load))
    costs=[max(w.values(),default=0.) for w in work];moves=[];checked=0
    selected=None if pairs is None else set(pairs)
    for a in range(len(cover)):
        if not work[a] and positive_only:continue
        for b in range(a+1,len(cover)):
            if selected is not None and (a,b) not in selected:continue
            if not work[b] and positive_only:continue
            cycles=(alternating_components if partial else alternating_cycles)(cover[a],cover[b])
            if len(cycles)<2 and positive_only:continue  # same capacity, possibly different DAG
            for cycle in cycles:
                if len(cycle)==1 and not partial:continue
                checked+=1;wa=defaultdict(float,work[a]);wb=defaultdict(float,work[b])
                for src in cycle:
                    for edge,value in edge_work.get((src,cover[a][src]),()):wa[edge]-=value;wb[edge]+=value
                    for edge,value in edge_work.get((src,cover[b][src]),()):wb[edge]-=value;wa[edge]+=value
                gain=costs[a]+costs[b]-max(wa.values(),default=0.)-max(wb.values(),default=0.)
                if gain>1e-9 or not positive_only:moves.append(dict(a=a,b=b,cycle=cycle,proxy_gain_us=gain))
    moves.sort(key=lambda m:(-round(m['proxy_gain_us'],9),m['a'],m['b'],m['cycle']))
    return moves,dict(proxy_us=sum(costs),cycles_checked=checked,
                     improving_proposals=sum(m['proxy_gain_us']>1e-9 for m in moves))


def isolated_costs(messages,network):
    return {(f.src,f.dst):(network.delay_us(f.src,f.dst),network.delay_us(f.dst,f.src),
             f.bytes*.008/min(network.links[e].gbps for e in network.path(f.src,f.dst))) for f in messages}


def dependency_bound(cover,costs,*,cutoff=float('inf')):
    """Queue DAG completion lower bound with isolated serialization and propagation.

    Receiver posts credit after its previous data, sender releases after ACK.
    Sharing, control serialization and packet overhead are omitted. If cutoff
    is reached, the partial lower bound suffices to reject a strict improvement.
    """
    n=len(cover[0]) if cover else 0;send=[0.]*n;recv=[0.]*n;maximum=0.
    for row in cover:
        for src,dst in enumerate(row):
            item=costs.get((src,dst))
            if item is None:continue
            forward,reverse,serial=item
            done=max(send[src],recv[dst]+reverse)+forward+serial
            recv[dst]=done;send[src]=done+reverse;maximum=max(maximum,done)
            if maximum>=cutoff:return maximum
    return maximum


def native_evaluator(cover,costs,library):
    """Prepare one cover for optional exact C++ candidate-bound evaluation."""
    import ctypes as c
    n=len(cover[0]);k=len(cover)
    if any(len(row)!=n or any(v is not None and (type(v) is not int or not 0<=v<n) for v in row) for row in cover):
        raise ValueError('invalid native cover')
    if any(v is None for row in cover for v in row):
        abi=getattr(library,'cover_partial_abi',None)
        if abi is None or abi()!=1:raise ValueError('native library does not support partial covers')
    rows=(c.c_int*(n*k))(*(-1 if v is None else v for row in cover for v in row))
    times=(c.c_double*(3*n*n))(*(v for s in range(n) for d in range(n)
                               for v in costs.get((s,d),(0.,0.,-1.))))
    fn=library.cover_dependency_bound
    fn.argtypes=[c.c_int,c.c_int,c.POINTER(c.c_int),c.POINTER(c.c_double),
                 c.c_int,c.c_int,c.POINTER(c.c_ubyte),c.c_double]
    fn.restype=c.c_double
    def evaluate(a,b,cycle,cutoff=float('inf')):
        if not 0<=a<k or not 0<=b<k or any(type(s) is not int or not 0<=s<n for s in cycle):
            raise ValueError('invalid native exchange')
        mask=(c.c_ubyte*n)()
        for s in cycle:mask[s]=1
        return fn(n,k,rows,times,a,b,mask,cutoff)
    return evaluate


def minimize_joint(messages,cover,network,*,on_step=None,cache_pairs=False,native_bound=None,dependency_only=False,partial=False):
    """Strict descent of max(capacity group proxy, endpoint DAG lower bound).

    Includes capacity-increasing exchanges when dependency improvement can
    lower the joint score. Pruning uses an exact component bound, not a count
    budget. No ns-3 objective is consulted. This remains a planning surrogate.
    dependency_only is a named ablation: no capacity pruning or capacity tie
    ordering is used; path-isolated times still enter the dependency bound.
    """
    costs=isolated_costs(messages,network);history=[];cache={};affected=None
    library=None
    if native_bound:
        import ctypes
        library=ctypes.CDLL(str(native_bound))
    while True:
        moves,info=proposals(messages,cover,network,positive_only=False,pairs=affected if cache_pairs else None,partial=partial)
        if cache_pairs:
            if affected is not None:
                for pair in affected:cache.pop(pair,None)
            for m in moves:cache.setdefault((m['a'],m['b']),[]).append(m)
            moves=[m for items in cache.values() for m in items]
            moves.sort(key=lambda m:(-round(m['proxy_gain_us'],9),m['a'],m['b'],m['cycle']))
            info.update(improving_proposals=sum(m['proxy_gain_us']>1e-9 for m in moves),pair_cache=True)
        if dependency_only:moves.sort(key=lambda m:(m['a'],m['b'],m['cycle']))
        dep=dependency_bound(cover,costs);score=dep if dependency_only else max(info['proxy_us'],dep)
        fast=native_evaluator(cover,costs,library) if library is not None else None
        best_score=score;best=None;best_cover=None;evaluations=0
        for m in moves:
            phi=info['proxy_us']-m['proxy_gain_us']
            if not dependency_only and phi>=best_score-1e-9:continue
            changed=None
            if fast is not None:d=fast(m['a'],m['b'],m['cycle'],best_score-1e-9)
            else:
                changed=exchange(cover,m['a'],m['b'],m['cycle'],partial=partial)
                d=dependency_bound(changed,costs,cutoff=best_score-1e-9)
            evaluations+=1
            candidate=d if dependency_only else max(phi,d)
            if candidate<best_score-1e-9:
                best_score=candidate;best=m
                best_cover=changed if changed is not None else exchange(cover,m['a'],m['b'],m['cycle'],partial=partial)
        record=dict(iteration=len(history),**info,dependency_bound_us=dep,joint_us=max(info['proxy_us'],dep),objective_us=score,
                    dependency_evaluations=evaluations,move=best)
        if history and score>=history[-1]['objective_us']-1e-9:
            raise ArithmeticError('accepted cycle did not decrease recomputed joint score')
        history.append(record)
        if on_step is not None:on_step(cover,record)
        if best is None:return cover,history
        cover=best_cover
        affected={(min(a,b),max(a,b)) for a in (best['a'],best['b']) for b in range(len(cover)) if a!=b}


def minimize_proxy(messages,cover,network,*,on_step=None):
    """Steepest strict resource-work descent until no improving cycle remains.

    There is no fixed iteration count and no simulation used for selection.
    Termination is local to this cover-exchange neighborhood and this proxy.
    Actual asynchronous completion may improve OR regress; measure separately.
    """
    history=[];previous=None
    while True:
        moves,info=proposals(messages,cover,network)
        if previous is not None and info['proxy_us']>=previous-1e-9:
            raise ArithmeticError('accepted cycle did not strictly decrease recomputed proxy')
        record=dict(iteration=len(history),**info,move=moves[0] if moves else None)
        history.append(record)
        if on_step is not None:on_step(cover,record)
        if not moves:return cover,history
        m=moves[0];previous=info['proxy_us'];cover=exchange(cover,m['a'],m['b'],m['cycle'])


def minimize_proxy_cached(messages,cover,network,*,on_step=None):
    """Same steepest capacity descent, refreshing only affected color pairs.

    An exchange changes exactly two groups. Other pairs' gains and cycles
    remain identical; the current absolute proxy is still recomputed fully.
    Cache does not prune the search neighborhood or change its tie-breaking.
    """
    cache={};history=[];affected=None;previous=None
    while True:
        moves,info=proposals(messages,cover,network,pairs=affected)
        if affected is not None:
            for pair in affected:cache.pop(pair,None)
        for m in moves:cache.setdefault((m['a'],m['b']),[]).append(m)
        choices=[m for items in cache.values() for m in items]
        choices.sort(key=lambda m:(-round(m['proxy_gain_us'],9),m['a'],m['b'],m['cycle']))
        if previous is not None and info['proxy_us']>=previous-1e-9:
            raise ArithmeticError('cached step did not decrease recomputed proxy')
        info['improving_proposals']=len(choices)
        info['pair_cache']=True
        record=dict(iteration=len(history),**info,move=choices[0] if choices else None)
        history.append(record)
        if on_step is not None:on_step(cover,record)
        if not choices:return cover,history
        m=choices[0];previous=info['proxy_us'];cover=exchange(cover,m['a'],m['b'],m['cycle'])
        affected={(min(a,b),max(a,b)) for a in (m['a'],m['b']) for b in range(len(cover)) if a!=b}


def main():
    """Reproducible offline cover experiment; simulation is outside selection."""
    import argparse
    import hashlib
    import json
    import time
    from pathlib import Path
    from .benchmark import network_for
    from .model import Message
    from .scheduler import interleaved_ranks
    from .simai import run

    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-plan',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--topology',choices=['tree','leaf-spine'],required=True)
    p.add_argument('--scale',choices=['32','128'],required=True)
    p.add_argument('--profile',choices=['uniform','heterogeneous'],default='heterogeneous')
    p.add_argument('--rank-order',choices=['ordinary','server','rack'],default='server')
    p.add_argument('--objective',choices=['capacity','joint','dependency'],default='capacity')
    p.add_argument('--cache-pairs',action='store_true',help='exact color-pair proposal cache; dependency scores are recomputed')
    p.add_argument('--native-bound',type=Path,help='optional exact compiled dependency-bound library')
    p.add_argument('--binary',default='/home/julius/fast/SimAI/bin/SimAI_simulator')
    args=p.parse_args()
    if args.native_bound:
        if args.objective=='capacity':p.error('native bound applies only to joint/dependency objectives')
        args.native_bound=args.native_bound.resolve(strict=True)
    data=json.loads(args.input_plan.read_text())
    messages=tuple(Message(**f) for f in data['messages'])
    net=network_for(args.topology,args.scale,args.profile)
    ranks=(tuple(range(int(args.scale))) if args.rank_order=='ordinary' else
           interleaved_ranks(net,across_racks=args.rank_order=='rack'))
    initial=initial_cover(ranks)
    # Validate before launching or creating results. The source queues are not
    # used: only its unchanged message matrix is imported.
    cover_plan(messages,initial,ranks)
    args.output.mkdir(parents=True,exist_ok=False)
    manifest={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}
    manifest.update(input_sha256=hashlib.sha256(args.input_plan.read_bytes()).hexdigest(),
        rank_order_values=ranks,source_sha256={f.name:hashlib.sha256(f.read_bytes()).hexdigest()
                          for f in Path(__file__).parent.iterdir() if f.suffix in ('.py','.cpp')})
    if args.native_bound:manifest['native_bound_sha256']=hashlib.sha256(args.native_bound.read_bytes()).hexdigest()
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    started=time.perf_counter();history=[]
    def save(c,record):
        history.append(record)
        state=dict(status='planning' if record['move'] else 'proxy_stationary',
                   planning_s=time.perf_counter()-started,rank_order=ranks,cover=c,history=history)
        (args.output/'planning.json').write_text(json.dumps(state,indent=2)+'\n')
        print(record['iteration'],record['proxy_us'],record['improving_proposals'],flush=True)
    optimizer=minimize_proxy if args.objective=='capacity' else minimize_joint
    options=dict(on_step=save)
    if args.native_bound:options['native_bound']=args.native_bound
    if args.objective=='dependency':options['dependency_only']=True
    if args.cache_pairs:
        if args.objective=='capacity':optimizer=minimize_proxy_cached
        else:options['cache_pairs']=True
    final,_=optimizer(messages,initial,net,**options)
    # Run both plans under the exact same topology/configuration. Neither
    # simulation result feeds back into the optimizer.
    for name,c in [('initial',initial),('final',final)]:
        result=run(cover_plan(messages,c,ranks),net,args.output/name,args.binary)
        print(name,result['status'],result.get('delivery_ns'),flush=True)


if __name__=='__main__':main()
