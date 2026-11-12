#!/usr/bin/env python3
"""Full GPU cyclic/FAST comparison; bounded development panel, not an algorithm scan."""
import argparse
import hashlib
import json
import math
import pathlib
import subprocess

import study


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def resource_cost(flows, n, g, unit,topology=None):
    loads = {}
    delay = 0
    capacities=(dict(zip(topology['resource_names'],topology['bandwidth_gbps']))
                if topology else {})
    for src, dst, amount in flows:
        s, d = src//g, dst//g
        if s == d or not amount:
            continue
        if topology:
            path=topology['server_paths'][s*n+d]
            route=[topology['resource_names'][e] for e in path['resource_ids']]
            delay=max(delay,path['propagation_us'])
        else:
            route = [f'server-out:{s}', f'server-in:{d}']
            delay = max(delay, 1.4)
            a, b = s*3//n, d*3//n
            if a != b:
                route += [f'gateway-out:{a}', f'gateway-in:{b}', f'wan:{a}:{b}']
                delay = max(delay, 52.8)
        for name in route:
            loads[name] = loads.get(name, 0)+amount*unit
    work = {name: amount*.008/(capacities[name] if topology else (100 if name.startswith('wan:') else 400))
            for name, amount in loads.items()}
    return loads, delay, max(work.values(), default=0)


def audit(raw, data, n, g, unit):
    ranks = n*g
    is_spread = raw['mode'] == 'spreadout'
    if is_spread:
        stages = raw['rounds']
        delivered = [0]*len(data)
        for stage in stages:
            for src, dst, amount in stage['flows']:
                assert dst == (src+stage['offset']) % ranks
                delivered[src*ranks+dst] += amount
        assert delivered == data
    else:
        study.audit_pipeline(data, n, g, raw)
        stages = [dict(flows=[[s*g+u, p[s]*g+u, payload[s*g+u]]
                              for s in range(n) if s != p[s] for u in range(g)])
                  for p, payload in zip(raw['permutations'], raw['payload_units'])]
    resource_totals, alpha_sum, serial_sum = {}, 0, 0
    network = []
    for k, stage in enumerate(stages):
        loads, alpha, serial = resource_cost(stage['flows'], n, g, unit,raw.get('topology_snapshot'))
        for name, amount in loads.items():
            resource_totals[name] = resource_totals.get(name, 0)+amount
        alpha_sum += alpha; serial_sum += serial; network.append(alpha+serial)
        if is_spread:
            for field, expected in [('alpha_us', alpha), ('serial_us', serial), ('network_us', alpha+serial)]:
                assert math.isclose(stage[field], expected, abs_tol=1e-8, rel_tol=1e-12)
            actual = dict(zip(raw['resource_names'], stage['resource_bytes']))
            # Physical evaluator uses double MB conversion; demand/flow units
            # above remain exact integers. Never relax actual delivery checks.
            assert all(math.isclose(actual.get(key, 0), value, rel_tol=1e-12, abs_tol=1e-8)
                       for key, value in loads.items())
            assert math.isclose(sum(actual.values()), sum(loads.values()), rel_tol=1e-12, abs_tol=1e-8)
            intra = max((.1+amount*unit/1e6*2.3 for s, d, amount in stage['flows']
                         if s//g == d//g), default=0)
            assert math.isclose(stage['total_us'], max(intra, alpha+serial), abs_tol=1e-8)
        else:
            assert math.isclose(raw['network_stages_us'][k], alpha+serial, abs_tol=1e-8, rel_tol=1e-12)
    if is_spread:
        assert math.isclose(raw['total_us'], sum(s['total_us'] for s in stages), abs_tol=1e-8)
    else:
        total = raw['balance_us']+max(raw['intrinsic_us'], network[0] if network else 0)
        total += sum(max(d, t) for d, t in zip(raw['dispatch_us'], network[1:]))
        if raw['dispatch_us']:
            total += raw['dispatch_us'][-1]
        assert math.isclose(total, raw['total_us'], abs_tol=1e-8, rel_tol=1e-12)
    return dict(alpha_sum_us=alpha_sum, serial_sum_us=serial_sum,
                network_sum_us=sum(network), residual_intra_us=raw['total_us']-sum(network),
                resource_totals=resource_totals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=pathlib.Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    sources = ['simulation/bvn_decompose_main.cpp', 'simulation/topology/model.cpp',
               'simulation/scheduler/global.cpp', 'ds/decomposition/baseline_control.py',
               'ds/decomposition/study.py']
    metadata = dict(complete=False, scope='full FAST pipeline2 vs GPU cyclic rounds, fixed shared topology',
                    seed=101, repeats=3, g=4, unit_bytes=[16, 1024],
                    native_scaling_rule='unchanged balance_alpha_beta with original ETHER400/H100 inputs',
                    binary_sha256=hashlib.sha256(study.BIN.read_bytes()).hexdigest(),
                    sources={p: hashlib.sha256((study.ROOT/p).read_bytes()).hexdigest() for p in sources})
    save(args.out/'metadata.json', metadata)
    rows = []
    for n in (8, 16):
        for kind in ('balanced', 'perturbed', 'zipf'):
            data = study.fixed_load_matrix(n, 4, kind, 'pipeline')
            save(args.out/f'{kind}-n{n}.input.json', data)
            for unit in (16, 1024):
                expected_resources = None
                for method, strategy, scaling in [('spreadout', 0, 0), ('pipeline', 0, 0),
                                                    ('pipeline', 0, 1), ('pipeline', 1, 0), ('pipeline', 1, 1)]:
                    command = [str(study.BIN), '--mode', method, '--n', str(n), '--g', '4',
                               '--strategy', str(strategy), '--scaling', str(scaling),
                               '--topology', '1', '--repeat', '3', '--unit-mb', str(unit/1e6)]
                    done = subprocess.run(command, input=' '.join(map(str, data)), text=True,
                                          capture_output=True, check=True, timeout=90)
                    raw = json.loads(done.stdout)
                    metrics = audit(raw, data, n, 4, unit)
                    if expected_resources is None:
                        expected_resources = metrics['resource_totals']
                    assert metrics['resource_totals'] == expected_resources
                    name = f'{kind}-n{n}-unit{unit}-{method}-s{strategy}-scale{scaling}'
                    save(args.out/(name+'.json'), raw)
                    row = dict(case=f'{kind}-n{n}-unit{unit}', n=n, kind=kind, unit_bytes=unit,
                               method=method, strategy=strategy, scaling=scaling,
                               K=raw.get('active_rounds', raw.get('stage_count')),
                               total_us=raw['total_us'], **metrics)
                    rows.append(row)
                    print(name, 'K', row['K'], 'total_us', round(row['total_us'], 4), flush=True)
    assert hashlib.sha256(study.BIN.read_bytes()).hexdigest() == metadata['binary_sha256']
    assert all(hashlib.sha256((study.ROOT/p).read_bytes()).hexdigest() == digest
               for p, digest in metadata['sources'].items())
    save(args.out/'summary.json', rows)
    metadata.update(complete=True, outputs=len(rows), pairings=12,
                    timing='FAST decomposition CPU and full planning wall kept separately; spreadout CPU includes model evaluation, not planner-only')
    save(args.out/'metadata.json', metadata)


if __name__ == '__main__':
    main()
