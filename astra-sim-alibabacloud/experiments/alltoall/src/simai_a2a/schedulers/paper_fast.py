"""Lightweight CPU-FAST decomposition policies and explicit paper-epoch costs.

No permutation enumeration or external solver. The adaptive guard is model-only.
"""

from dataclasses import replace
from math import ceil
from time import perf_counter_ns
import numpy as np

from ..delivery import validate_fast_delivery
from ..paper_model import PaperTopology
from . import _finalize_schedule
from . import fast_port as native
from .fast_wan import repair_matching


POLICIES={'fast-paper':'native','fast-paper-wan':'wan',
          'fast-paper-quantum':'quantum','fast-paper-progress':'progress',
          'fast-paper-amortized':'amortized'}
CPP_POLICIES={'fast-cpp-native':'native','fast-cpp-maxmin':'maxmin',
              'fast-cpp-partition':'partition','fast-cpp-adaptive':'adaptive'}


def supported_matching(support):
    """Original Kuhn augmenting paths over an explicitly ordered support."""
    n=len(support);inverse=[-1]*n;matching=[-1]*n
    def dfs(row,seen):
        for col in support[row]:
            if col not in seen:
                seen.add(col)
                if inverse[col]<0 or dfs(inverse[col],seen):
                    matching[row]=col;inverse[col]=row;return True
        return False
    return matching if all(dfs(row,set()) for row in range(n)) else None


def largest_quantum_matching(residual,support):
    """Maximum bottleneck matching via O(log E) feasibility calls, not permutations."""
    values=sorted(set(map(int,residual[residual>0])))
    lo,hi=0,len(values)-1;best=None;calls=0
    while lo<=hi:
        middle=(lo+hi)//2;threshold=values[middle]
        trial=supported_matching([[j for j in row if residual[i,j]>=threshold]
                                  for i,row in enumerate(support)])
        calls+=1
        if trial is None:hi=middle-1
        else:best=trial;lo=middle+1
    if best is None:raise ValueError('positive support has no perfect matching')
    return best,calls


def efficient_matching(residual,remaining,gpus,topology,choices):
    """Choose real-byte service per alpha+shared-capacity cost, ignoring padding."""
    def score(match):
        q=min(int(residual[i,j]) for i,j in enumerate(match))
        rows=np.asarray(remaining)
        if rows.ndim==2:
            if gpus!=1:raise ValueError('exact per-GPU residual required for multi-GPU matching')
            rows=rows[:,None,:]
        served=np.zeros((len(rows),len(rows)),dtype=np.int64)
        for i,j in enumerate(match):
            if i!=j:served[i,j]=sum(map(int,np.minimum(rows[i,:,j],q)))
        cost=topology.stage(served)
        return (cost['real_bytes']/cost['duration_ns'] if cost['duration_ns'] else 0.,q)
    return max(choices,key=score)


def decompose_policy(app,real,gpus,topology,policy,stats):
    n=app.dim;rows=np.array(real,dtype=np.int64,copy=True)
    if rows.ndim==2:
        if gpus!=1:raise ValueError('exact per-GPU residual required for multi-GPU matching')
        rows=rows[:,None,:]
    if rows.shape!=(n,gpus,n):raise ValueError('per-GPU residual shape mismatch')
    app.p_sets=[];stats.setdefault('matching_calls',0)
    stats['decision_stage_bytes']=[]
    cross=np.not_equal.outer(topology.dc_ids,topology.dc_ids)
    initial_edges=int(np.count_nonzero(app.SDS_mat))
    extracted=0
    while extracted<app.sds_info['max_sum']:
        remaining=rows.sum(axis=1)
        app.update_edges()
        support=[[j-n for j in row] for row in app.row_to_col]
        base=supported_matching(support);stats['matching_calls']+=1
        if base is None:raise RuntimeError('native positive support has no matching')
        if policy=='wan':
            matching,_=repair_matching(support,base,(remaining>0)&cross)
        elif policy in ('quantum','progress','amortized'):
            quantum,calls=largest_quantum_matching(app.SDS_mat,support)
            stats['matching_calls']+=calls
            if policy=='quantum':matching=quantum
            elif policy=='amortized':
                matching=efficient_matching(app.SDS_mat,rows,gpus,topology,[base,quantum])
            else:
                choices=[base,quantum]
                for wan_first in (False,True):
                    ordered=[sorted(row,key=lambda j:(
                        not (bool(cross[i,j])==wan_first and remaining[i,j]>0),
                        -int(remaining[i,j]))) for i,row in enumerate(support)]
                    choices.append(supported_matching(ordered));stats['matching_calls']+=1
                before=topology.lower_bound(remaining)
                def score(match):
                    q=min(int(app.SDS_mat[i,j]) for i,j in enumerate(match))
                    served=np.zeros_like(remaining)
                    for i,j in enumerate(match):
                        if i!=j:served[i,j]=sum(map(int,np.minimum(rows[i,:,j],q)))
                    cost=topology.stage(served)
                    if not cost['duration_ns']:return (0.,0.,q)
                    progress=max(0.,before-topology.lower_bound(remaining-served))
                    return (progress/cost['duration_ns'],cost['real_bytes']/cost['duration_ns'],q)
                matching=max(choices,key=score)
        else:raise ValueError(f'unknown paper policy {policy}')
        app.matching=[-1]*(2*n)
        for i,j in enumerate(matching):app.matching[i]=j+n;app.matching[j+n]=i
        q=app.update_permutation_sets();extracted+=q
        served_bytes=0
        for i,j in enumerate(matching):
            if i!=j:
                taken=np.minimum(rows[i,:,j],q);served_bytes+=sum(map(int,taken))
                rows[i,:,j]-=taken
        stats['decision_stage_bytes'].append(served_bytes)
        if q<=0 or len(app.p_sets)>initial_edges:
            raise RuntimeError('Birkhoff edge-zeroing progress violated')
    if np.any(rows):raise RuntimeError('paper decomposition leaves real server demand')
    return app.p_sets


def _one_plan(matrix,hardware,topology,algorithm,seed):
    started=perf_counter_ns();n=hardware.num_servers;g=hardware.gpus_per_server
    if len(topology.dc_ids)!=n:raise ValueError('paper dc_ids length must equal number of servers')
    hardware.validate_matrix(matrix)
    # Original CPU cells, tile row sums/averages and service sums are uint32.
    # Conservatively bound whole server sums before any native-style preprocessing.
    limit=4294967295
    if int(matrix.values.max(initial=0))>limit or any(
            sum(map(int,matrix.values[s*g:(s+1)*g,:].flat))+g-1>limit or
            sum(map(int,matrix.values[:,s*g:(s+1)*g].flat))+g-1>limit for s in range(n)):
        raise ValueError('CPU native32-bit input/intermediate range exceeded (conservative server-sum bound)')
    real=np.array([[0 if i==j else sum(map(int,matrix.values[i*g:(i+1)*g,j*g:(j+1)*g].flat))
                    for j in range(n)] for i in range(n)],dtype=np.int64)
    if not np.any(real):
        from .fast import FastScheduler
        schedule=FastScheduler().schedule(matrix,hardware,seed=seed)
        metadata={**schedule.metadata,'stage_records':[],'paper_stages':[],
                  'paper_model':topology.to_dict(),'paper_model_version':1,
                  'synthesis_ns':perf_counter_ns()-started,'matching_calls':0,'decomposition_ns':0,
                  'scaleout_lower_bound_ns':0.,'analytical_makespan_ns':schedule.predicted_makespan_ns}
        metadata['delivery_validation']=validate_fast_delivery(matrix,schedule,g)
        metadata['decision_stage_bytes']=[]
        if algorithm in CPP_POLICIES:
            metadata['cpp_epoch']=dict(kernel_ns=0,selected_policy='native',selected_cost_ns=schedule.predicted_makespan_ns,
                                       candidate_costs_ns={},matching_calls=0)
        return replace(schedule,algorithm=algorithm,metadata=metadata)
    stats={};cpp_mode=CPP_POLICIES.get(algorithm)
    policy=cpp_mode if cpp_mode is not None else POLICIES[algorithm]
    def decomposition(app):
        t0=perf_counter_ns()
        if app.sds_info['max_sum']>4294967295:
            raise ValueError('CPU native32-bit service-sum range exceeded')
        if cpp_mode is not None:
            from .cpp_epoch import synthesize
            result,compiled=synthesize(app,hardware,topology,cpp_mode);stats.update(compiled)
        elif policy=='native':
            result=app.decompose();stats['matching_calls']=len(result)
        else:result=decompose_policy(app,app.real_gpu_remaining,g,topology,policy,stats)
        stats['decomposition_ns']=perf_counter_ns()-t0
        return result
    r=native.run_fast_full(matrix.values,n,g,'ETHER400','H100',MBpu=1e-6,
            inter_override={'alpha':hardware.inter.alpha_ns/1000,
                            'beta':hardware.inter.beta_ns_per_byte*1000},
            intra_override={'alpha':hardware.intra.alpha_ns/1000,
                            'beta':hardware.intra.beta_ns_per_byte*1000},decomposition=decomposition)
    gs=r['gs'];intra=lambda a:native.spread_out(a,g,gs.intra_info,1e-6)*1000
    prep=max(sum(intra(gs.last_bs[s,d]) for d in range(n) if s!=d) for s in range(n))
    intrinsic=max(intra(local.intrinsic_all2all) for local in r['locals'])
    homogeneous_ns=r['t']*1000;clock=prep;records=[];stage_records=[]
    for k,pset in enumerate(r['p_sets']):
        volume=np.zeros((n,n),dtype=np.int64)
        for s,d in enumerate(pset['mp']):
            if s!=d:volume[s,d]=sum(gs.last_inter_rows[k,s])
        cost=topology.stage(volume);cost['duration_ns']=ceil(cost['duration_ns'])
        if 'decision_stage_bytes' in stats and stats['decision_stage_bytes'][k]!=cost['real_bytes']:
            raise RuntimeError('candidate real-progress accounting differs from native dispatch')
        local=intrinsic if k==0 else max(intra(gs.last_ds[k-1,s]) for s in range(n))
        slot=max(cost['duration_ns'],local)
        records.append(dict(stage_id=k,start_ns=clock,slot_ns=slot,scaleup_overlap_ns=local,
                            real_server_edges=[[int(s),int(d),int(volume[s,d])]
                                               for s,d in zip(*np.nonzero(volume))],**cost))
        clock+=slot
        pset['inter_duration_ns']=cost['duration_ns']
        stage_records.append(dict(stage_id=k,mp=list(pset['mp']),freq=int(pset['freq']),
                                  quota_per_gpu_bytes=[int(pset['freq'])]*n))
    tail=max(intra(gs.last_ds[len(records)-1,s]) for s in range(n))
    clock+=tail;r['t']=clock/1000
    if cpp_mode is not None and abs(stats['selected_cost_ns']+prep-clock)>1e-3:
        raise RuntimeError('compiled full-pipeline cost differs from independent CPU replay')
    flows,timeline=native.emit_schedule(r,MBpu=1e-6)
    raw=[(int(src),int(dst),int(size),round(start*1000),extra['phase'],extra['stage_id'])
         for (src,dst,size,start),extra in zip(flows,timeline['flow_metadata'])]
    metadata=dict(seed=seed,logical_bytes=matrix.logical_bytes,n_stages=len(records),
        stage_records=stage_records,paper_stages=records,paper_model=topology.to_dict(),paper_model_version=1,
        homogeneous_cpu_makespan_ns=homogeneous_ns,analytical_makespan_ns=clock,
        balance_ns=prep,final_redistribution_ns=tail,scaleout_lower_bound_ns=topology.lower_bound(real),
        matching_calls=stats['matching_calls'],decomposition_ns=stats['decomposition_ns'],decomposition_policy=policy,
        decision_stage_bytes=stats.get('decision_stage_bytes',[s['real_bytes'] for s in records]),
        baseline_reference='FAST/simulation pipeline2; native unordered_set matching order',
        scaleout_extension='max active path alpha + maximum shared-resource bytes/capacity')
    if cpp_mode is not None:
        metadata['cpp_epoch']={key:stats[key] for key in ['kernel_source_digest','kernel_binary_sha256','kernel_ns','matching_calls',
                              'selected_policy','selected_cost_ns','candidate_costs_ns','compiler']}
    schedule=_finalize_schedule(algorithm,raw,hardware,round(clock),metadata)
    metadata['delivery_validation']=validate_fast_delivery(matrix,schedule,g)
    metadata['synthesis_ns']=perf_counter_ns()-started
    return schedule


def paper_schedules(matrix,hardware,topology,*,seed=11):
    started=perf_counter_ns()
    plans={name:_one_plan(matrix,hardware,topology,name,seed) for name in POLICIES}
    best=min(plans,key=lambda name:plans[name].predicted_makespan_ns)
    selected=plans[best]
    plans['fast-paper-adaptive']=replace(selected,algorithm='fast-paper-adaptive',metadata={
        **selected.metadata,'selected_policy':best,'synthesis_ns':perf_counter_ns()-started,
        'matching_calls':sum(p.metadata['matching_calls'] for p in plans.values()),
        'decomposition_ns':sum(p.metadata['decomposition_ns'] for p in plans.values()),
        'candidate_costs_ns':{name:plan.predicted_makespan_ns for name,plan in plans.items()},
        'selection_guarantee':'non-regression in this analytical model only; synthesis excluded'})
    return plans


class PaperFastScheduler:
    def __init__(self,algorithm,fast_options):
        self.algorithm=algorithm
        self.topology=PaperTopology(**(fast_options or {}))

    def schedule(self,matrix,hardware,*,seed):
        if self.algorithm=='fast-paper-adaptive':
            return paper_schedules(matrix,hardware,self.topology,seed=seed)[self.algorithm]
        return _one_plan(matrix,hardware,self.topology,self.algorithm,seed)
