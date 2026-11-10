#!/usr/bin/env python3
"""Small paired study of complete integer BvN policies; no policy search."""
import argparse
import json
import hashlib
import math
import pathlib
import random
import statistics
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
BIN = ROOT / 'simulation/build/bvn_decompose_simulator'


def matrix(n, kind, seed=101):
    if kind == 'example':
        return [1, 1, 2, 1, 2, 1, 2, 1, 1]
    if kind in ('balanced', 'perturbed'):
        return [0 if s == d else 256 + int(kind == 'perturbed' and d == (s+1) % n)
                for s in range(n) for d in range(n)]
    rng = random.Random(seed)
    if kind == 'random-regular':
        a = [0]*(n*n)
        for _ in range(2*n):
            p = list(range(n)); rng.shuffle(p)
            weight = rng.randint(1, 512)
            for s, d in enumerate(p):
                a[s*n+d] += weight
        return a
    # Synthetic receiver-skew, not a measured MoE trace.
    order = list(range(n)); rng.shuffle(order)
    rank = {d: i+1 for i, d in enumerate(order)}
    a = []
    for s in range(n):
        weights = [0 if s == d else rank[d]**(-.9) for d in range(n)]
        values = [(n-1)*256*w/sum(weights) for w in weights]
        row = list(map(math.floor, values))
        remainder = (n-1)*256-sum(row)
        for d in sorted(range(n), key=lambda d: (-(values[d]-row[d]), d))[:remainder]:
            row[d] += 1
        a += row
    return a


def audit_decomposition(data, n, raw):
    stages = raw['permutations']; steps = raw['steps']
    assert len(stages) == len(steps) == raw['stage_count']
    r = [0]*(n*n)
    for p, st in zip(stages, steps):
        assert sorted(p) == list(range(n))
        for s, d in enumerate(p):
            r[s*n+d] += st['alpha_scaled']
    bound = max([sum(data[s*n:s*n+n]) for s in range(n)] +
                [sum(data[s*n+d] for s in range(n)) for d in range(n)])
    assert all(x >= y for x, y in zip(r, data))
    assert all(sum(r[s*n:s*n+n]) == bound for s in range(n))
    assert all(sum(r[s*n+d] for s in range(n)) == bound for d in range(n))

    def degree():
        return max([sum(x > 0 for x in r[s*n:s*n+n]) for s in range(n)] +
                   [sum(r[s*n+d] > 0 for s in range(n)) for d in range(n)])
    for p, st in zip(stages, steps):
        q = min(r[s*n+d] for s, d in enumerate(p))
        assert q > 0 and q == st['alpha_scaled']
        assert st['degree_before'] == degree()
        eliminated = sum(r[s*n+d] == q for s, d in enumerate(p))
        assert eliminated == st['eliminated']
        for s, d in enumerate(p):
            r[s*n+d] -= q
        assert st['degree_after'] == degree()
        assert len(st['sampled_thresholds']) <= raw['budget']
    assert not any(r)


def audit_pipeline(data, n, g, raw):
    ranks = n*g; delivered = [[0]*n for _ in range(n)]
    for p, payload in zip(raw['permutations'], raw['payload_units']):
        assert sorted(p) == list(range(n)) and len(payload) == ranks
        for s, d in enumerate(p):
            if s != d:
                delivered[s][d] += sum(payload[s*g:(s+1)*g])
    for s in range(n):
        for d in range(n):
            if s != d:
                expected = sum(data[(s*g+u)*ranks+d*g+v] for u in range(g) for v in range(g))
                assert delivered[s][d] == expected
    assert math.isfinite(raw['total_us']) and raw['total_us'] >= 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    sources = ['simulation/algorithm/all2all.cpp', 'simulation/algorithm/all2all.h',
               'simulation/scheduler/global.cpp', 'simulation/scheduler/global.h',
               'simulation/bvn_decompose_main.cpp', 'simulation/topology/model.cpp',
               'simulation/scheduler/local.cpp', 'ds/decomposition/study.py']
    metadata = dict(repeat=args.repeat, budget=8, complete=False,
                    revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    compiler=subprocess.check_output(['g++', '--version'], text=True).splitlines()[0],
                    source_sha256={s: hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources},
                    binary_sha256=hashlib.sha256(BIN.read_bytes()).hexdigest())
    (out/'run_metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')
    rows = []
    jobs = [('matrix', 3, 1, 'example', 0)]
    jobs += [('matrix', n, 1, kind, 0) for n in (8, 16, 32)
             for kind in ('balanced', 'perturbed', 'zipf', 'random-regular')]
    jobs += [('pipeline', n, 4, kind, topo) for n in (8, 16)
             for kind in ('balanced', 'zipf') for topo in (0, 1)]
    for mode, n, g, kind, topo in jobs:
        data = matrix(n if mode == 'matrix' else n*g, kind)
        if mode == 'pipeline':
            # Avoid the preserved legacy self-loop/intrinsic ambiguity: this study
            # measures inter-server A2A with balance/dispatch, not same-server data.
            ranks = n*g
            data = [0 if s//g == d//g else data[s*ranks+d]
                    for s in range(ranks) for d in range(ranks)]
        key = f'{mode}-n{n}g{g}-{kind}-top{topo}'
        (out/(key+'.input')).write_text(' '.join(map(str, data))+'\n')
        for strategy in range(4):
            cmd = [str(BIN), '--mode', mode, '--n', str(n), '--g', str(g),
                   '--strategy', str(strategy), '--budget', '8', '--repeat', str(args.repeat),
                   '--topology', str(topo)]
            started = time.time()
            run = subprocess.run(cmd, input=' '.join(map(str, data)), text=True,
                                 capture_output=True, timeout=120, check=True)
            raw = json.loads(run.stdout)
            if mode == 'matrix':
                audit_decomposition(data, n, raw)
            else:
                audit_pipeline(data, n, g, raw)
            (out/(key+f'-s{strategy}.json')).write_text(json.dumps(raw, indent=2)+'\n')
            rows.append(dict(case=key, mode=mode, n=n, kind=kind, topology=topo,
                             strategy=strategy, stages=raw['stage_count'],
                             cpu_median_us=statistics.median(raw['cpu_samples_us']),
                             total_us=raw.get('total_us'),
                             dispatch_us=raw.get('dispatch_us'),
                             threshold_probes=raw['threshold_probes'], weighted_calls=raw['weighted_calls']))
            print(key, strategy, 'K', raw['stage_count'], 'cpu_us', rows[-1]['cpu_median_us'],
                  'elapsed_s', round(time.time()-started, 3), flush=True)
    (out/'summary.json').write_text(json.dumps(rows, indent=2)+'\n')
    metadata['complete'] = True
    metadata['outputs'] = len(rows)
    (out/'run_metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')


if __name__ == '__main__':
    main()
