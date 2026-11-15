"""Explicit ideal scale-up projection and actual stage-barrier validation."""

from pathlib import Path

from .model import HardwareProfile, Schedule
from .schedulers import _finalize_schedule


STAGE_BARRIER_MODEL = 'stage-barrier-ideal-scaleup'
NATIVE_MODEL = 'fast-nvidia-deps-ideal-scaleup'
MODELED_MODEL = 'fast-nvidia-deps-modeled-scaleup'
NATIVE_MODELS = (NATIVE_MODEL, MODELED_MODEL)


def modeled_scaleup_schedule(schedule: Schedule, hardware: HardwareProfile) -> Schedule:
    """Hybrid local alpha/beta collective timers and ns-3 inter-server transport.

    A serialized local engine per server executes balance, intrinsic, then
    receiver dispatch in order. Intrinsic/dispatch may overlap network sends.
    No cross-server global barrier is inserted.
    """
    import math
    g,n=hardware.gpus_per_server,hardware.num_servers
    count=len(schedule.metadata['stage_records'])
    volume={}
    for flow in schedule.flows:
        if flow.src//g!=flow.dst//g:
            continue
        phase=('dispatch' if (schedule.algorithm=='spreadout' or schedule.metadata.get('native_round_protocol'))
               and flow.phase=='direct' else flow.phase)
        if phase not in ('balance','intrinsic','dispatch'):
            raise ValueError('unrecognized local operation for native timing')
        stage=flow.stage_id if phase=='dispatch' else -1
        key=(phase,stage,flow.src//g)
        block=volume.setdefault(key,[0]*(g*g))
        block[(flow.src%g)*g+flow.dst%g]+=flow.size_bytes
    def duration(phase,k,s):
        block=volume.get((phase,k,s),[0]*(g*g))
        # Spreadout pricing matches the existing full-operator reference, with
        # absent rounds free and ceiling avoiding an early-completion timestamp.
        return math.ceil(sum(hardware.intra.alpha_ns+hardware.intra.beta_ns_per_byte*peak
            for offset in range(1,g)
            if (peak:=max(block[u*g+(u+offset)%g] for u in range(g)))))
    local=dict(gpus=g,balance=[duration('balance',-1,s) for s in range(n)],
               intrinsic=[duration('intrinsic',-1,s) for s in range(n)],
               dispatch=[[duration('dispatch',k,s) for s in range(n)] for k in range(count)])
    projected=native_dependency_schedule(schedule,hardware)
    return Schedule(projected.algorithm,projected.flows,None,{**projected.metadata,
        'execution_model':MODELED_MODEL,'local_times_ns':local,
        'scaleup_assumption':'per-server serialized alpha/beta spreadout engine; no local packets',
        'operator_completion':'all network ACKs and local balance/intrinsic/dispatch complete'})


def write_local_times(schedule: Schedule,path: Path) -> None:
    if schedule.metadata.get('execution_model')!=MODELED_MODEL:
        raise ValueError('local timing sidecar requires modeled scaleup')
    local=schedule.metadata['local_times_ns'];g=local['gpus']
    rows=[f'{schedule.metadata["native_rank_count"]//g} {g} {len(local["dispatch"])}']
    rows+=[' '.join(map(str,row)) for row in [local['balance'],local['intrinsic'],*local['dispatch']]]
    Path(path).write_text('\n'.join(rows)+'\n')


def write_local_only_events(schedule: Schedule,path: Path) -> None:
    """No payload packets: drain local engines, including virtual network slots."""
    if schedule.flows:
        raise ValueError('local-only shortcut requires no network payload')
    local=schedule.metadata['local_times_ns'];g=local['gpus']
    rows=[(0,'begin',-1,-1,-1)];end=0
    for s,(b,i) in enumerate(zip(local['balance'],local['intrinsic'])):
        rows.extend([(0,'balance_begin',-1,s*g,-1),(b,'balance_done',-1,s*g,-1),
                     (b,'intrinsic_begin',-1,s*g,-1),(b+i,'intrinsic_done',-1,s*g,-1)])
        tail=b+i
        for k,durations in enumerate(local['dispatch']):
            rows.append((tail,'redis_begin',-1,s*g,k));tail+=durations[s]
            rows.extend((tail,'redis_done',-1,s*g+u,k) for u in range(g))
        end=max(end,tail)
    rows.append((end,'finished',-1,-1,-1));rows.sort(key=lambda r:r[0])
    Path(path).write_text('time_ns,event,flow_id,rank,stage\n'+''.join(
        ','.join(map(str,row))+'\n' for row in rows))


def native_dependency_schedule(schedule: Schedule, hardware: HardwareProfile) -> Schedule:
    projected=ideal_scaleup_schedule(schedule,hardware)
    return Schedule(projected.algorithm,projected.flows,None,{
        **projected.metadata,'execution_model':NATIVE_MODEL,'barrier_completion':None,
        'native_rank_count':hardware.num_devices,
        'native_gpus_per_server':hardware.gpus_per_server,
        'native_protocol':'nonchunked stream/visibility/credit dependency model',
        'signal_tail_bytes':8,'credit_payload_bytes':8,'launch_latency_ns':0})


def write_native_commands(schedule: Schedule,path: Path) -> None:
    if schedule.metadata.get('execution_model') not in NATIVE_MODELS:
        raise ValueError('native commands require native execution model')
    n=schedule.metadata['native_rank_count'];g=schedule.metadata['native_gpus_per_server']
    records=schedule.metadata['stage_records']
    outgoing={(f.stage_id,f.src):f.flow_id for f in schedule.flows}
    incoming={(f.stage_id,f.dst):f.flow_id for f in schedule.flows}
    rows=[f'{n},{len(records)}\n']
    if schedule.metadata.get('native_global_send_barrier'):
        rows=[f'{n},{len(records)},1\n']
    if schedule.metadata.get('native_round_protocol'):
        rows=[f'{n},{len(records)},{int(bool(schedule.metadata.get("native_global_send_barrier")))},1\n']
    for k,record in enumerate(records):
        rank_map='rank_mp' in record
        inverse={d:s for s,d in enumerate(record['rank_mp'] if rank_map else record['mp'])}
        for rank in range(n):
            peer=inverse[rank] if rank_map else inverse[rank//g]*g+rank%g
            rows.append(f'{rank},{k},{outgoing.get((k,rank),-1)},{incoming.get((k,rank),-1)},{peer}\n')
    Path(path).write_text(''.join(rows),encoding='utf-8')


def ideal_scaleup_schedule(schedule: Schedule, hardware: HardwareProfile) -> Schedule:
    """Project an already validated plan; logical placement operations cost zero.

    start_ns is now an eligibility offset (zero), NOT a stage time prediction.
    The sidecar stage ids and actual QP completions determine execution order.
    """
    raw = [(f.src, f.dst, f.size_bytes, 0, f.phase, f.stage_id)
           for f in schedule.flows
           if f.src // hardware.gpus_per_server != f.dst // hardware.gpus_per_server]
    projected = _finalize_schedule(schedule.algorithm, raw, hardware, None, {
        **schedule.metadata,
        'execution_model': STAGE_BARRIER_MODEL,
        'scaleup_assumption': 'all server-local operations instantaneous, including head/tail',
        'barrier_completion': 'all real stage flows sender ACK complete; zero coordinator overhead',
        'full_plan_physical_bytes': schedule.physical_bytes,
        'full_plan_predicted_makespan_ns': schedule.predicted_makespan_ns,
        'physical_bytes': sum(row[2] for row in raw),
        'active_network_stages': len({row[5] for row in raw}),
    })
    if 'delivery_chunks' in schedule.metadata:
        # Projection renumbers network flows; preserve original source identity.
        tags = schedule.metadata['delivery_chunks']
        network = [f for f in schedule.flows
                   if f.src // hardware.gpus_per_server != f.dst // hardware.gpus_per_server]
        projected.metadata['delivery_chunks'] = {
            str(new.flow_id): tags[str(old.flow_id)] for old, new in zip(network, projected.flows)}
        projected.metadata['local_delivery_records'] = [
            dict(src=f.src, dst=f.dst, stage=f.stage_id, phase=f.phase,
                 chunks=tags[str(f.flow_id)]) for f in schedule.flows
            if f.src // hardware.gpus_per_server == f.dst // hardware.gpus_per_server
            and f.phase != 'balance']
    return projected


def write_stage_file(schedule: Schedule, path: Path) -> None:
    if schedule.metadata.get('execution_model') != STAGE_BARRIER_MODEL:
        raise ValueError('stage sidecar requires the stage-barrier execution model')
    if any(f.start_ns != 0 for f in schedule.flows):
        raise ValueError('stage-barrier schedules require zero eligibility offsets')
    Path(path).write_text(''.join(f'{f.flow_id},{f.stage_id}\n' for f in schedule.flows),
                          encoding='utf-8')


def stage_barrier_statistics(schedule: Schedule, observations: dict) -> dict:
    stages = {}
    for flow in schedule.flows:
        obs = observations[flow.flow_id]
        stage = stages.setdefault(flow.stage_id, {'stage_id': flow.stage_id,
                                  'start_ns': obs['actual_start_ns'], 'end_ns': 0,
                                  'real_bytes': 0, 'flow_count': 0})
        stage['start_ns'] = min(stage['start_ns'], obs['actual_start_ns'])
        stage['end_ns'] = max(stage['end_ns'], obs['actual_start_ns'] + obs['fct_ns'])
        stage['real_bytes'] += flow.size_bytes
        stage['flow_count'] += 1
    ordered = [stages[k] for k in sorted(stages)]
    gaps = []
    for previous, current in zip(ordered, ordered[1:]):
        gap = current['start_ns'] - previous['end_ns']
        if gap < 0:
            raise ValueError(f"stage barrier violated between {previous['stage_id']} and {current['stage_id']}")
        gaps.append(gap)
    return {'active_stages': len(ordered), 'violation_count': 0,
            'interstage_release_gap_ns': sum(gaps), 'stages': ordered}
