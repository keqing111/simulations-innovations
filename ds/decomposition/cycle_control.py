#!/usr/bin/env python3
"""Paired FAST cycle-refinement checks; fixed rules, no best-of-run selection."""
import argparse
import datetime
import hashlib
import itertools
import json
import math
import pathlib
import random
import statistics
import subprocess
import time

import baseline_control as b
import residual_opportunity as residual
import study
import typical_topology as typical

METHODS = [('native', 'pipeline', 0, 0, 0), ('bottleneck', 'pipeline', 1, 0, 0),
           ('pricing', 'pipeline', 1, 3, 0), ('cycle', 'pipeline', 1, 0, 3),
           ('pricing_cycle', 'pipeline', 1, 3, 3), ('spreadout', 'spreadout', 0, 0, 0)]


def zipf_matrix(n, g, seed):
    """Same exact receiver-rank generator as study, with an explicit seed."""
    ranks = n*g; order = list(range(ranks)); random.Random(seed).shuffle(order)
    rank = {d: i+1 for i, d in enumerate(order)}; result = []
    for s in range(ranks):
        weights = [0 if s//g == d//g else rank[d]**(-.9) for d in range(ranks)]
        exact = [39060*w/sum(weights) for w in weights]; row = list(map(math.floor, exact))
        for d in sorted(range(ranks), key=lambda d: (-(exact[d]-row[d]), d))[:39060-sum(row)]:
            row[d] += 1
        result += row
    return result


def cases(panels):
    if 'development' in panels:
        for kind, placement, data in typical.cases():
            for profile in range(3):
                yield dict(name=f'development-{kind}-p{placement}-top{profile}', panel='development',
                    kind=kind, n=6, profile=profile, seed=101, placement=placement,
                    units=(16, 1024), data=data,
                    archive=f'multidc-certified-20261112/{kind}-p{placement}-top{profile}')
    if 'new-seed' in panels:
        for seed, profile in itertools.product((202, 303), range(3)):
            yield dict(name=f'new-seed{seed}-zipf-n6-top{profile}', panel='new-seed', kind='zipf',
                n=6, profile=profile, seed=seed, placement=101, units=(16, 1024),
                data=zipf_matrix(6, 4, seed))
    if 'legacy' in panels:
        for n, kind in itertools.product((8, 16), ('balanced', 'perturbed', 'zipf')):
            yield dict(name=f'legacy-{kind}-n{n}-top0', panel='legacy', kind=kind, n=n,
                profile=0, seed=101, placement=101, units=(16, 1024),
                data=study.fixed_load_matrix(n, 4, kind, 'pipeline'),
                archive=f'resource-certified-20261112/{kind}-n{n}')
    if 'scale' in panels:
        for n in (32, 64):
            yield dict(name=f'scale-balanced-n{n}-top0', panel='scale', kind='balanced',
                n=n, profile=0, seed=101, placement=101, units=(1024,),
                data=study.fixed_load_matrix(n, 4, 'balanced', 'pipeline'))


def audit_cycles(raw, unit):
    accepts = 0
    for cost, step in zip(raw['network_stages_us'], raw['steps']):
        trace = step['cycle_costs_us']
        assert trace and math.isclose(trace[-1], cost, abs_tol=1e-8, rel_tol=1e-12)
        assert all(a > b for a, b in zip(trace, trace[1:])); accepts += len(trace)-1
    assert accepts == raw['cycle_accepts']
    assert raw['cycle_passes'] == accepts+raw['stage_count']
    # Independently certify terminal neighborhoods on small states. Large-size
    # verification relies on exact delivery, trace and canonical C++ acceptance.
    n, g = raw['n'], raw['g']
    if n > 6:
        return
    remaining = [0]*(n*n)
    for p, step in zip(raw['permutations'], raw['steps']):
        for s, d in enumerate(p):
            remaining[s*n+d] += step['alpha_scaled']
    pending = residual.initial_pending(raw)
    for k, (p, step) in enumerate(zip(raw['permutations'], raw['steps'])):
        q, capacity = step['alpha_scaled'], step['alpha']
        for count in (2, 3):
            for rows in itertools.combinations(range(n), count):
                for shift in range(1, count):
                    trial = p[:]
                    for i, s in enumerate(rows):
                        trial[s] = p[rows[(i+shift) % count]]
                    if any(remaining[s*n+d] < q for s, d in enumerate(trial)):
                        continue
                    cost = residual.action_features(raw, pending, trial, capacity, unit)['stage_us']
                    assert cost >= raw['network_stages_us'][k]-1e-8
        for s, d in enumerate(p):
            remaining[s*n+d] -= q
        pending = residual.consume(pending, p, capacity, n, g)
    assert not any(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=pathlib.Path)
    parser.add_argument('--panels', nargs='+', choices=('development', 'new-seed', 'legacy', 'scale'),
                        default=['development', 'new-seed', 'legacy'])
    parser.add_argument('--repeat', type=int, choices=range(1, 11), default=3)
    args = parser.parse_args(); args.out.mkdir(parents=True, exist_ok=False)
    names = ['simulation/algorithm/all2all.cpp', 'simulation/algorithm/all2all.h',
             'simulation/scheduler/global.cpp', 'simulation/bvn_decompose_main.cpp',
             'simulation/scheduler/global.h', 'simulation/scheduler/local.cpp',
             'simulation/scheduler/local.h', 'simulation/scheduler/config.cpp',
             'simulation/scheduler/config.h', 'simulation/algorithm/matrix.cpp',
             'simulation/algorithm/matrix.h', 'simulation/topology/model.h',
             'simulation/define.h', 'simulation/Makefile',
             'simulation/topology/model.cpp', 'ds/decomposition/cycle_control.py',
             'ds/decomposition/typical_topology.py', 'ds/decomposition/study.py',
             'ds/decomposition/baseline_control.py', 'ds/decomposition/residual_opportunity.py',
             'simulation/tests/test_topology_schedule.cpp', 'simulation/tests/test_resource_cycles_cli.py',
             'simulation/tests/test_cycle_control.py']
    meta = dict(complete=False, panels=args.panels, repeats=args.repeat, g=4, scaling=True,
        source_sha256={s: hashlib.sha256((study.ROOT/s).read_bytes()).hexdigest() for s in names},
        binary_sha256=hashlib.sha256(study.BIN.read_bytes()).hexdigest(),
        frozen_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        scope='fixed-rule paired simulation; new seeds are checks, not population guarantees',
        cycle_objective='canonical stage time; steepest legal 2/3-cycle descent to local minimum')
    b.save(args.out/'metadata.json', meta); rows = []; parity = 0; started = time.monotonic()
    for case in cases(args.panels):
        data = case['data']; n = case['n']; profile = case['profile']
        b.save(args.out/(case['name']+'.input.json'), data)
        for unit in case['units']:
            expected = None
            for label, mode, strategy, rounds, cycle in METHODS:
                command = [str(study.BIN), '--mode', mode, '--n', str(n), '--g', '4',
                    '--strategy', str(strategy), '--topology', '1', '--scaling', str(int(mode == 'pipeline')),
                    '--wan-profile', str(profile), '--resource-rounds', str(rounds),
                    '--resource-cycle', str(cycle), '--unit-mb', str(unit/1e6), '--repeat', str(args.repeat)]
                raw = json.loads(subprocess.run(command, input=' '.join(map(str, data)), text=True,
                    capture_output=True, check=True, timeout=90).stdout)
                metrics = b.audit(raw, data, n, 4, unit)
                if expected is None:
                    expected = metrics['resource_totals']
                assert metrics['resource_totals'] == expected
                if cycle:
                    audit_cycles(raw, unit)
                if 'archive' in case and label in ('native', 'bottleneck', 'pricing', 'spreadout'):
                    old_label = {'pricing': 'resource', 'spreadout': 'spreadout'}.get(label, label)
                    if case['panel'] == 'legacy':
                        old_label = {'bottleneck': 's1', 'pricing': 'resource3'}.get(label, label)
                    path = study.ROOT/'ds/decomposition'/f"{case['archive']}-unit{unit}-{old_label}.json"
                    archived = json.loads(path.read_text()); old = archived.get('raw', archived)
                    keys = ('rounds', 'total_us') if mode == 'spreadout' else (
                        'permutations', 'payload_units', 'network_stages_us', 'dispatch_us',
                        'balance_us', 'intrinsic_us', 'total_us')
                    for key in keys:
                        assert raw[key] == old[key], (case['name'], label, key)
                    parity += 1
                name = f"{case['name']}-unit{unit}"
                b.save(args.out/(name+'-'+label+'.json'), dict(raw=raw))
                row = {k: case[k] for k in ('panel', 'kind', 'n', 'profile', 'seed', 'placement')}
                row.update(case=name, label=label, unit=unit, total_us=raw['total_us'],
                    K=raw.get('stage_count', raw.get('active_rounds')), **metrics,
                    cycle_evaluations=raw.get('cycle_evaluations', 0),
                    cycle_accepts=raw.get('cycle_accepts', 0), cycle_passes=raw.get('cycle_passes', 0))
                if mode == 'pipeline':
                    cpu = statistics.median(raw['cpu_samples_us'])
                    row.update(decomposition_cpu_us=cpu,
                        planning_wall_us=statistics.median(raw['planning_wall_samples_us']),
                        serial_cpu_plus_communication_us=raw['total_us']+cpu)
                rows.append(row)
                print(name, label, 'K', row['K'], 'us', round(row['total_us'], 6),
                      'cpu', row.get('decomposition_cpu_us'), 'moves', row['cycle_accepts'], flush=True)
    b.save(args.out/'summary.json', rows)
    assert hashlib.sha256(study.BIN.read_bytes()).hexdigest() == meta['binary_sha256']
    for s, h in meta['source_sha256'].items():
        assert hashlib.sha256((study.ROOT/s).read_bytes()).hexdigest() == h, s
    meta.update(complete=True, outputs=len(rows), conditions=len(rows)//len(METHODS),
                legacy_plan_parity=parity, elapsed_s=time.monotonic()-started)
    b.save(args.out/'metadata.json', meta)


if __name__ == '__main__':
    main()
