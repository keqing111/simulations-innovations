#!/usr/bin/env python3
"""Corrected synthetic workload retest against unchanged FAST CPU volume planner.

No real MoE trace, GPU execution, packet simulation, or online-performance claim.
"""
import argparse
import bisect
import hashlib
import json
import math
import pathlib
import random
import statistics
import subprocess
import tempfile

import baseline_control as physical
import joint_resource
import study

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOST = ROOT/'simulation/build/runtime_host_reference'
UNIT = 8192
FAMILIES = ('balanced', 'edge_zipf', 'token_uniform', 'token_zipf', 'token_affinity')


def allocate(weights, total):
    scale = total/sum(weights)
    ideal = [w*scale for w in weights]
    result = [int(x) for x in ideal]
    for i in sorted(range(len(weights)), key=lambda i: (-(ideal[i]-result[i]), i))[:total-sum(result)]:
        result[i] += 1
    return result


def traffic(n, g, kind, batch, seed):
    """Return GPU matrix in token-vector units plus explicit sampling metadata."""
    r = n*g
    if r < 2 or batch <= 0 or kind not in FAMILIES:
        raise ValueError('invalid workload parameters')
    rng = random.Random(seed)
    meta = dict(kind=kind, seed=seed, batch_tokens_per_gpu=batch, top_k=2,
                experts_per_gpu=1, vector_bytes=UNIT, self_routes=[0]*r,
                duplicate_expert_routes=0, synthetic=True)
    data = [0]*(r*r)
    if kind == 'balanced':
        if 2*batch % r:
            raise ValueError('balanced control needs integral assignments per expert')
        q = 2*batch//r
        data = [q if i != j else 0 for i in range(r) for j in range(r)]
        meta['self_routes'] = [q]*r
    elif kind == 'edge_zipf':
        # Independent message SIZE distribution, not receiver popularity.
        cumulative = []
        total = 0.
        for v in range(1, 10001):
            total += v**-.8; cumulative.append(total)
        ids = [i*r+j for i in range(r) for j in range(r) if i != j]
        sampled = [1+bisect.bisect_left(cumulative, rng.random()*total) for _ in ids]
        target = (r-1)*batch*2
        for index, value in zip(ids, allocate(sampled, target)):
            data[index] = value
        meta.update(top_k=None, self_routes=[0]*r, size_exponent=.8,
                    size_support=[1, 10000], global_budget_units=target)
    else:
        # Expert placement is shuffled independently of network topology.
        def popularity():
            order = list(range(r)); rng.shuffle(order)
            weights = [0.]*r
            for rank, expert in enumerate(order, 1):
                weights[expert] = rank**-.9
            return [w/sum(weights) for w in weights], order
        shared, order = popularity()
        domains = [popularity() for _ in range(4)]
        meta.update(popularity_exponent=.9, expert_rank_order=order,
                    domain_rank_orders=[o for _, o in domains] if kind == 'token_affinity' else [],
                    domain_rule='(source_gpu + source_server) mod 4', affinity_mixture=.5)
        for src in range(r):
            weights = ([1/r]*r if kind == 'token_uniform' else shared)
            if kind == 'token_affinity':
                local = domains[(src+src//g) % 4][0]
                weights = [.5*a+.5*b for a, b in zip(shared, local)]
            cumulative = []; total = 0.
            for w in weights:
                total += w; cumulative.append(total)
            for _ in range(batch):
                first = bisect.bisect_left(cumulative, rng.random()*total)
                x = rng.random()*(total-weights[first])
                before = cumulative[first]-weights[first]
                if x >= before:
                    x += weights[first]
                second = bisect.bisect_left(cumulative, x)
                if first == second:
                    raise ArithmeticError('duplicate Top-2 expert')
                for dst in (first, second):
                    if src == dst:
                        meta['self_routes'][src] += 1
                    else:
                        data[src*r+dst] += 1
        assert all(sum(data[s*r:(s+1)*r])+meta['self_routes'][s] == batch*2 for s in range(r))
    assert all(data[i*r+i] == 0 for i in range(r))
    return data, meta


def input_features(data, n, g):
    r = n*g
    rows = [sum(data[s*r:(s+1)*r]) for s in range(r)]
    cols = [sum(data[s*r+d] for s in range(r)) for d in range(r)]
    cv = lambda a: statistics.pstdev(a)/statistics.mean(a) if sum(a) else 0
    unbalanced = 0
    for s in range(n):
        for d in range(n):
            if s == d:
                continue
            amounts = [sum(data[(s*g+u)*r+d*g:(s*g+u)*r+(d+1)*g]) for u in range(g)]
            unbalanced += len(set(amounts)) > 1
    return dict(unbalanced_tiles=unbalanced, total_remote_tiles=n*(n-1),
                local_units=sum(data[s*r+d] for s in range(r) for d in range(r) if s//g == d//g),
                total_units=sum(data), sender_cv=cv(rows), receiver_cv=cv(cols),
                identical_source_servers=sum(all(data[(s*g)*r:(s*g+1)*r] == data[(s*g+u)*r:(s*g+u+1)*r]
                                                 for u in range(1, g)) for s in range(n)),
                row_units=rows, col_units=cols)


def spread_time(flat, g, unit, alpha=.1, beta=2.3):
    return sum(alpha+peak*unit/1e6*beta for offset in range(1, g)
               if (peak := max(flat[i*g+(i+offset) % g] for i in range(g))))


def pipeline_time(balance, intrinsic, network, dispatch):
    if not network:
        return balance+intrinsic
    return balance+max(intrinsic, network[0])+sum(max(d, t) for d, t in zip(dispatch, network[1:]))+dispatch[-1]


def native_profile_us(raw):
    """Independent recurrence, same 380/48.85 GB/s and no startup as source profiler."""
    local = lambda matrices: max((spread_time(m, raw['g'], 1, 0, 1000/380) for m in matrices), default=0)
    return pipeline_time(local(raw['balance_matrices']), local(raw['intrinsic_matrices']),
                         [max(p, default=0)/48850 for p in raw['payload_units']],
                         [local(matrices) for matrices in raw['dispatch_matrices']])


def evaluate(raw, topology, unit=None):
    """Common analytical pricing of actual native balance/restore volume plans."""
    n, g = raw['n'], raw['g']
    unit = raw.get('payload_unit_bytes', UNIT) if unit is None else unit
    raw['topology_snapshot'] = topology
    local = lambda matrices: max((spread_time(m, g, unit) for m in matrices), default=0)
    raw['balance_us'] = local(raw['balance_matrices'])
    raw['intrinsic_us'] = local(raw['intrinsic_matrices'])
    raw['dispatch_us'] = [local(matrices) for matrices in raw['dispatch_matrices']]
    network = []; resources = {}; delays = []; serials = []
    for p, payload in zip(raw['permutations'], raw['payload_units']):
        flows = [[s*g+u, d*g+u, payload[s*g+u]] for s, d in enumerate(p) if s != d for u in range(g)]
        loads, delay, serial = physical.resource_cost(flows, n, g, unit, topology)
        for key, value in loads.items():
            resources[key] = resources.get(key, 0)+value
        network.append(delay+serial); delays.append(delay); serials.append(serial)
    raw['network_stages_us'] = network
    raw['network_sum_us'] = sum(network)
    raw['alpha_sum_us'] = sum(delays)
    raw['serial_sum_us'] = sum(serials)
    raw['resource_totals'] = resources
    raw['total_us'] = pipeline_time(raw['balance_us'], raw['intrinsic_us'], network, raw['dispatch_us'])
    raw['residual_intra_us'] = raw['total_us']-sum(network)
    return raw


def host(data, n, g, plan=None):
    command = [str(HOST), str(n), str(g)]+([str(plan)] if plan else [])
    done = subprocess.run(command, input=' '.join(map(str, data)), text=True,
                          capture_output=True, check=True, timeout=60)
    raw = json.loads(done.stdout)
    if raw['delivered_matrix'] != data:
        raise ArithmeticError('original GPU provenance failed')
    if plan is None:
        if not raw['native_replay_equal'] or not math.isclose(native_profile_us(raw), raw['native_profile_ms']*1000,
                                                           rel_tol=1e-12, abs_tol=1e-9):
            raise ArithmeticError('native profiler/volume parity failed')
    return raw


def host_vectors(data, n, g, plan=None):
    # Convert BEFORE native ceil(balance_sum/g), padding and coefficient
    # extraction. Rounding token-vector counts then scaling is not equivalent.
    raw = host([amount*UNIT for amount in data], n, g, plan)
    raw['payload_unit_bytes'] = 1
    return raw


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=pathlib.Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    sources = [pathlib.Path(__file__), ROOT/'ds/decomposition/runtime_host.cpp',
               ROOT/'ds/decomposition/joint_resource.py', ROOT/'ds/decomposition/residual_opportunity.py',
               ROOT/'ds/decomposition/baseline_control.py', ROOT/'simulation/bvn_decompose_main.cpp',
               ROOT/'simulation/topology/model.cpp']
    sources += list((ROOT/'nvidia/src/fast_alltoall').glob('alltoall_*.cc'))
    sources += list((ROOT/'nvidia/src/include/fast_alltoall').glob('*.h'))
    sources += [HOST, study.BIN]
    fingerprints = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    manifest = dict(complete=False, source_sha256=fingerprints, n=6, g=4, unit_bytes=UNIT,
                    planner_unit_bytes=1, native_input='original vector counts multiplied by8192 before native initialization',
                    batches=[480, 3840], seeds=[1101, 1102, 1103], profiles=[0, 1, 2],
                    scope='synthetic, native NVIDIA CPU volume plans, shared analytical pricing; not CUDA runtime',
                    algorithm_frozen=True, threshold_budget=4, conditions_planned=78, outputs_planned=390)
    save(args.out/'metadata.json', manifest)
    rows = []
    for batch in manifest['batches']:
        for kind in FAMILIES:
            for seed in ([1101] if kind == 'balanced' else manifest['seeds']):
                slug = f'{kind}-b{batch}-seed{seed}'
                data, info = traffic(6, 4, kind, batch, seed)
                features = input_features(data, 6, 4)
                save(args.out/(slug+'.input.json'), dict(data=data, metadata=info, features=features))
                native = host_vectors(data, 6, 4)
                for profile in manifest['profiles']:
                    case = slug+f'-p{profile}'
                    command = [str(study.BIN), '--mode', 'spreadout', '--n', '6', '--g', '4',
                               '--topology', '1', '--wan-profile', str(profile), '--repeat', '1',
                               '--unit-mb', str(UNIT/1e6)]
                    spread = json.loads(subprocess.run(command, input=' '.join(map(str, data)), text=True,
                                                       capture_output=True, check=True, timeout=60).stdout)
                    metrics = physical.audit(spread, data, 6, 4, UNIT)
                    top = spread['topology_snapshot']
                    reference = evaluate(dict(native), top)
                    expected = reference['resource_totals']
                    if expected != metrics['resource_totals']:
                        raise ArithmeticError('spreadout/native resource conservation mismatch')
                    def record(method, raw, cpu=0):
                        save(args.out/(case+'-'+method+'.json'), raw)
                        row = dict(case=case, kind=kind, batch=batch, seed=seed, profile=profile,
                                   method=method, K=raw.get('stage_count', raw.get('active_rounds')),
                                   total_us=raw['total_us'], balance_us=raw.get('balance_us', 0),
                                   intrinsic_us=raw.get('intrinsic_us', 0),
                                   network_us=raw.get('network_sum_us', metrics['network_sum_us']),
                                   alpha_us=raw.get('alpha_sum_us', metrics['alpha_sum_us']),
                                   serial_us=raw.get('serial_sum_us', metrics['serial_sum_us']),
                                   planner_us=cpu, features=features)
                        rows.append(row)
                        save(args.out/'summary.json', rows)
                        print(case, method, 'K', row['K'], 'total_us', round(row['total_us'], 3), flush=True)
                    record('native-host', reference, native['native_cpu_us'])
                    record('spreadout', dict(spread, **metrics))
                    for objective in ('stage', 'capacity'):
                        planned = joint_resource.plan(reference, 1, objective, budget=4)
                        save(args.out/(case+'-joint-'+objective+'.plan.json'), planned)
                        for descending in ([False, True] if objective == 'capacity' else [False]):
                            stages = list(zip(planned['permutations'], planned['trace']))
                            if descending:
                                # Match source runtime convention: ascending (q,index), then reverse.
                                stages = [stages[k] for k in sorted(range(len(stages)),
                                          key=lambda k: (stages[k][1]['capacity'], k), reverse=True)]
                            with tempfile.TemporaryDirectory() as temp:
                                path = pathlib.Path(temp)/'plan.txt'
                                path.write_text(str(len(stages))+'\n'+'\n'.join(' '.join(map(str, [q['capacity']]+p)) for p, q in stages)+'\n')
                                raw = evaluate(host_vectors(data, 6, 4, path), top)
                            if raw['resource_totals'] != expected:
                                raise ArithmeticError('candidate resource conservation failed')
                            if not descending and not math.isclose(raw['network_sum_us'], planned['network_us'], rel_tol=1e-12, abs_tol=1e-8):
                                raise ArithmeticError('joint/native actual payload timing mismatch')
                            label = 'joint-'+objective+('-descending' if descending else '')
                            record(label, raw, planned['cpu_us'])
    if any(hashlib.sha256((ROOT/p).read_bytes()).hexdigest() != digest for p, digest in fingerprints.items()):
        raise ArithmeticError('source changed during frozen experiment')
    manifest.update(complete=True, outputs=len(rows), conditions=len(rows)//5)
    save(args.out/'metadata.json', manifest)


if __name__ == '__main__':
    main()
