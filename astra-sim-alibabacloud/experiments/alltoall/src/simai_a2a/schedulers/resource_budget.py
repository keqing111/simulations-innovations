"""Experimental resource-first BvN planner; time in us, payload in integer B.

Auxiliary capacity R and real channel inventory U are never conflated. H is a
planning surrogate, not a timer or stage barrier in the network executor.
"""
import math
from collections import deque
import time
import numpy as np
from scipy.optimize import linear_sum_assignment


def prepare_resources(topology):
    n,g=topology['n'],topology['g'];names=topology['resource_names'];rates=topology['bandwidth_gbps']
    if n<1 or g<1 or len(names)!=len(rates) or any(not math.isfinite(b) or b<=0 for b in rates):
        raise ValueError('invalid resource dimensions/capacity')
    paths=topology['server_paths']
    if len(paths)!=n*n:raise ValueError('invalid path dimensions')
    wans={i for i,name in enumerate(names) if name.startswith('wan:')};groups={};edges=[]
    for i,path in enumerate(paths):
        ids=tuple(path['resource_ids']);tau=path['propagation_us'];wan=[r for r in ids if r in wans]
        if (len(set(ids))!=len(ids) or any(not 0<=r<len(rates) for r in ids)
            or not math.isfinite(tau) or tau<0):raise ValueError('invalid resource path')
        if i//n!=i%n and not ids:raise ValueError('real server pair must have a resource path')
        edge=dict(src=i//n,dst=i%n,path=ids,tau=tau,wans=tuple(wan),
                  endpoints=() if not ids else (ids[0],ids[-1]))
        edges.append(edge)
        if i//n!=i%n:
            for r in wan or (None,):groups.setdefault(r,[]).append(i)
    return dict(n=n,g=g,edges=edges,rates=list(rates),names=list(names),wans=wans,groups=groups)


def pressure(U,res):
    work=[0.]*len(res['rates']);g=res['g']
    for i,e in enumerate(res['edges']):
        amount=sum(U[i*g:(i+1)*g])
        if e['src']==e['dst']:
            if amount:raise ValueError('intrinsic demand must not enter inter-server U')
            continue
        for r in e['path']:work[r]+=amount*.008/res['rates'][r]
    return work


def _support(R,q,pins=None):
    n=len(R);pins=pins or {}
    if len(set(pins.values()))!=len(pins):return None
    cost=np.full((n,n),np.inf)
    occupied=set(pins.values())
    for s in range(n):
        for d in range(n):
            if R[s][d]>=q and (pins.get(s,d)==d) and (d not in occupied or pins.get(s)==d):cost[s,d]=0.
    try:rows,cols=linear_sum_assignment(cost)
    except ValueError:return None
    p=[0]*n
    for s,d in zip(rows,cols):p[int(s)]=int(d)
    return p


def anchored_cap(R,anchor=None):
    pins={} if anchor is None else {anchor[0]:anchor[1]}
    values=sorted({v for row in R for v in row if v>0 and (anchor is None or v<=R[anchor[0]][anchor[1]])})
    lo,hi=0,len(values)
    while lo<hi:
        mid=(lo+hi)//2;p=_support(R,values[mid],pins)
        if p is None:hi=mid
        else:lo=mid+1
    return (values[lo-1],_support(R,values[lo-1],pins)) if lo else (0,None)


def _amounts(U,q,res):
    g=res['g'];return [sum(min(q,v) for v in U[i*g:(i+1)*g]) for i in range(res['n']**2)]


def _charge(load,delay,i,amount,res):
    load=load[:];delay=delay[:]
    if amount:
        edge=res['edges'][i]
        for r in edge['path']:load[r]+=amount;delay[r]=max(delay[r],edge['tau'])
    time=max((a+v*.008/b for a,v,b in zip(delay,load,res['rates'])),default=0.)
    return load,delay,time


def evaluate_round(R,U,p,q,res):
    n,g=res['n'],res['g']
    if (len(R)!=n or any(len(row)!=n for row in R) or len(U)!=n*n*g or
        sorted(p)!=list(range(n)) or type(q)is not int or q<=0):raise ValueError('invalid round dimensions/coefficient')
    if any(R[s][d]<q for s,d in enumerate(p)):raise ValueError('q exceeds selected auxiliary residual')
    before=pressure(U,res);next_R=[row[:] for row in R];next_U=U[:];load=[0]*len(before);delay=[0.]*len(before)
    zr=zu=size=0;time=0.;amounts=_amounts(U,q,res)
    for s,d in enumerate(p):
        i=s*n+d;zr+=int(R[s][d]==q);next_R[s][d]-=q
        for u in range(g):
            k=i*g+u;zu+=int(0<U[k]<=q);next_U[k]=max(0,U[k]-q)
        size+=amounts[i];load,delay,time=_charge(load,delay,i,amounts[i],res)
    after=pressure(next_U,res);phi=max(before,default=0.);next_phi=max(after,default=0.)
    excess=time+next_phi-phi
    if excess < -1e-9:raise ArithmeticError('capacity-potential lower bound violated')
    return dict(p=list(p),q=q,bytes=size,time_us=time,phi_before_us=phi,phi_after_us=next_phi,
        score=max(0.,excess)/size if size else math.inf,Z_R=zr,Z_U=zu,
        resource_bytes=load,R_next=next_R,U_next=next_U)


def _components(R,q,p,pins):
    """Allowed-edge oracle: a new edge needs a cycle in this alternating graph."""
    free=[s for s in range(len(R)) if s not in pins]
    graph={s:[t for t in free if R[s][p[t]]>=q] for s in free}
    index={};low={};stack=[];on=set();component={}
    def visit(s):
        index[s]=low[s]=len(index);stack.append(s);on.add(s)
        for t in graph[s]:
            if t not in index:visit(t);low[s]=min(low[s],low[t])
            elif t in on:low[s]=min(low[s],index[t])
        if low[s]==index[s]:
            while True:
                t=stack.pop();on.remove(t);component[t]=s
                if t==s:break
    for s in free:
        if s not in index:visit(s)
    return component,graph


def _force(p,s,d,graph):
    owner=p.index(d)
    if owner==s:return p[:]
    parent={owner:None};queue=deque([owner])
    while queue and s not in parent:
        v=queue.popleft()
        for t in graph[v]:
            if t not in parent:parent[t]=v;queue.append(t)
    if s not in parent:raise ValueError('edge does not belong to a legal alternating cycle')
    path=[s]
    while path[-1]!=owner:path.append(parent[path[-1]])
    path.reverse();new=p[:];new[s]=d
    for a,b in zip(path,path[1:]):new[a]=p[b]
    return new


def _solo(U,i,q,res):
    amount=sum(min(q,v) for v in U[i*res['g']:(i+1)*res['g']])
    edge=res['edges'][i]
    return edge['tau']+max((amount*.008/res['rates'][r] for r in edge['path']),default=0.) if amount else 0.


def propose_quanta(R,U,res,anchor):
    n,g=res['n'],res['g'];a=anchor[0]*n+anchor[1];cap,_=anchored_cap(R,anchor)
    whole=max(U[a*g:(a+1)*g],default=0);q=min(cap,whole)
    if not q:return []
    H=_solo(U,a,q,res)
    proposals=[dict(q=q,H_us=H,reason='anchor_full' if q==whole else 'anchor_capped',witness=None,next_edge=None)]
    work=pressure(U,res);opportunities=[];edge_a=res['edges'][a]
    for i,edge in enumerate(res['edges']):
        s,d=edge['src'],edge['dst']
        full=max(U[i*g:(i+1)*g],default=0)
        if (not full or s==anchor[0] or d==anchor[1] or not edge['wans'] or set(edge['wans']).intersection(edge_a['wans'])
            or not any(U[(s*n+anchor[1])*g:(s*n+anchor[1]+1)*g])):continue
        target=_solo(U,i,full,res)
        if target>=H-1e-10:continue
        # Use the FIRST integer anchor quantity whose window covers the event.
        # Rounding down and then setting H to the anchor time can exclude the
        # very witness that generated the candidate on unequal-bandwidth paths.
        lo,hi=full,q-1
        if lo>hi or _solo(U,a,hi,res)<target-1e-10:continue
        while lo<hi:
            mid=(lo+hi)//2
            if _solo(U,a,mid,res)>=target-1e-10:hi=mid
            else:lo=mid+1
        # Finish the witness's real transfer, not merely a fractional fragment.
        if lo<full or lo<=0 or R[s][d]<lo:continue
        p=_support(R,lo,{anchor[0]:anchor[1],s:d})
        if p is None:continue
        priority=max((work[r] for r in edge['path']),default=0.)
        opportunities.append((-priority,target,i,dict(q=lo,H_us=_solo(U,a,lo,res),reason='handoff',
            witness=(s,d),next_edge=(s,anchor[1]))))
    if opportunities:proposals.append(min(opportunities,key=lambda c:c[:3])[3])
    return proposals


def construct_round(R,U,res,anchor,q,H,required_edge=None,diagnostics=None):
    """Resource-directed greedy construction with SCC-supported legal completion.

    One full support witness is maintained, but its unused edges do not select
    the traffic: only resource/endpoint pressure and budget decide pinned edges.
    """
    def reject(code,**details):
        if diagnostics is not None:diagnostics.update(code=code,**details)
        return None
    if type(q)is not int or q<=0 or not math.isfinite(H) or H<0:return reject('invalid_coefficient_or_window')
    n=res['n'];pins={anchor[0]:anchor[1]}
    if required_edge is not None:
        s,d=required_edge
        if (s in pins and pins[s]!=d) or (d in pins.values() and pins.get(s)!=d):return reject('endpoint_conflict')
        pins[s]=d
    witness=_support(R,q,pins)
    if witness is None:return reject('no_support_completion')
    amount=_amounts(U,q,res);remaining=pressure(U,res);load=[0]*len(remaining);delay=[0.]*len(remaining);covered=set()
    for s,d in pins.items():
        i=s*n+d;load,delay,T=_charge(load,delay,i,amount[i],res)
        if amount[i]:covered.update(res['edges'][i]['wans'])
        if T>H+1e-9:return reject('pinned_budget_exceeded',time_us=T,H_us=H)
    while len(pins)<n:
        comp,graph=_components(R,q,witness,pins);inverse={d:s for s,d in enumerate(witness)};best=None
        occupied=set(pins.values())
        for s in range(n):
            if s in pins:continue
            for d in range(n):
                if d in occupied or R[s][d]<q or comp[s]!=comp[inverse[d]]:continue
                i=s*n+d;e=res['edges'][i];x=amount[i]
                new_load,new_delay,T=_charge(load,delay,i,x,res)
                if T>H+1e-9:continue
                new_group=x>0 and any(r not in covered for r in e['wans'])
                residual=max((max(0.,remaining[r]-load[r]*.008/res['rates'][r]) for r in e['path']),default=0.)
                endpoint=max((max(0.,remaining[r]-load[r]*.008/res['rates'][r]) for r in e['endpoints']),default=0.)
                key=(-int(new_group),-int(x>0),-residual,-endpoint,T,-x,s,d)
                if best is None or key<best[0]:best=(key,s,d,new_load,new_delay)
        if best is None:return reject('budget_completion_dead_end',pinned=dict(pins),H_us=H)
        _,s,d,load,delay=best;witness=_force(witness,s,d,graph);pins[s]=d
        i=s*n+d
        if amount[i]:covered.update(res['edges'][i]['wans'])
    result=evaluate_round(R,U,witness,q,res)
    if result['time_us']>H+1e-9:return reject('completion_budget_exceeded',time_us=result['time_us'],H_us=H)
    if result['Z_R']+result['Z_U']<=0:return reject('no_discrete_progress')
    return result


def select_anchors(U,res):
    work=pressure(U,res);g=res['g'];active=[i for i in range(res['n']**2) if any(U[i*g:(i+1)*g])]
    if not active:return []
    endpoint=lambda i:max((work[r] for r in res['edges'][i]['endpoints']),default=0.)
    wan=lambda i:max((work[r] for r in res['edges'][i]['wans']),default=0.)
    candidates=[]
    # Keep representatives of the critical SET, including shared non-WAN
    # resources. One path covering several tied links is still one anchor.
    phi=max(work,default=0.)
    for r,w in enumerate(work):
        if w>0 and math.isclose(w,phi,rel_tol=1e-12,abs_tol=1e-12):
            incident=[i for i in active if r in res['edges'][i]['path']]
            candidates.append(min(incident,key=lambda i:(-endpoint(i),-wan(i),i)))
    candidates.append(min(active,key=lambda i:(-endpoint(i),-wan(i),i)))
    return [(i//res['n'],i%res['n']) for i in dict.fromkeys(candidates)]


def plan(prepared,topology,allow_split=True):
    started=time.process_time_ns();res=prepare_resources(topology);n,g=res['n'],res['g']
    R=[row[:] for row in prepared['R']];U=list(prepared['U'])
    if (prepared['n']!=n or prepared['g']!=g or len(R)!=n or any(len(row)!=n for row in R)
        or len(U)!=n*n*g or any(type(v)is not int or v<0 for row in R for v in row)
        or any(type(v)is not int or v<0 or v>R[i//g//n][i//g%n] for i,v in enumerate(U))):
        raise ValueError('invalid auxiliary/real state')
    sums=[sum(row) for row in R]+[sum(col) for col in zip(*R)]
    if len(set(sums))!=1:raise ValueError('auxiliary rows/columns must have equal sums')
    pressure(U,res) # validates zero diagonal real traffic
    bound=sum(v>0 for row in R for v in row)+sum(v>0 for v in U)
    trace=[];permutations=[];coefficients=[];pending=None
    while any(v for row in R for v in row):
        candidates=[];attempts=[]
        for anchor in ([] if pending is not None else select_anchors(U,res)):
            for proposal in propose_quanta(R,U,res,anchor):
                if not allow_split and proposal['reason']=='handoff':continue
                failure={}
                action=construct_round(R,U,res,anchor,proposal['q'],proposal['H_us'],proposal['witness'],diagnostics=failure)
                # A split and its handoff are ONE decision. Prevalidate the
                # successor against this exact residual and retain it, rather
                # than letting the next greedy round forget why we split.
                if action is not None and proposal['next_edge'] is not None:
                    edge=proposal['next_edge'];nr,nu=action['R_next'],action['U_next']
                    follow_proposals=propose_quanta(nr,nu,res,edge)
                    f=follow_proposals[0] if follow_proposals else None
                    follow=construct_round(nr,nu,res,edge,f['q'],f['H_us']) if f else None
                    if follow is None:
                        action=None;failure.update(code='no_progressing_handoff_completion')
                    else:
                        follow.update(anchor=edge,H_us=f['H_us'],reason='handoff_followup',
                            witness=None,next_edge=None,accepted=True,failure=None)
                        action['continuation']=follow
                info=dict(anchor=anchor,**proposal,accepted=action is not None,failure=failure or None)
                attempts.append(info)
                if action is not None and action['bytes']:
                    action.update(info);candidates.append(action)
        fallback=not candidates and pending is None
        if pending is not None:
            action=pending;pending=None
        elif candidates:
            action=min(candidates,key=lambda a:(a['score'],-a['bytes'],a['time_us'],-a['q'],tuple(a['p'])))
        else:
            q,p=anchored_cap(R)
            if p is None:raise ValueError('no legal fallback matching')
            action=evaluate_round(R,U,p,q,res);action.update(anchor=None,H_us=None,reason='fallback',witness=None,next_edge=None)
        if action['Z_R']+action['Z_U']<=0:raise ArithmeticError('round made no discrete progress')
        pending=action.pop('continuation',None)
        permutations.append(action['p']);coefficients.append(action['q'])
        R=action.pop('R_next');U=action.pop('U_next')
        trace.append(dict(**action,fallback=fallback,attempts=attempts))
        if len(trace)>bound:raise ArithmeticError('positive-entry stage bound exceeded')
    if any(U):raise ArithmeticError('auxiliary drain left real data behind')
    return dict(permutations=permutations,coefficients=coefficients,trace=trace,remaining_U=U,
                stage_bound=bound,planner_cpu_us=(time.process_time_ns()-started)/1000,
                time_model='per-resource max propagation + summed serialization; proxy only',allow_split=allow_split)
