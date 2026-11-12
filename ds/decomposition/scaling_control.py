#!/usr/bin/env python3
"""Paired scaling control through the unchanged native FAST pipeline."""
import argparse
import hashlib
import json
import pathlib
import statistics
import subprocess

import study


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=pathlib.Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    metadata = dict(complete=False, binary_sha256=hashlib.sha256(study.BIN.read_bytes()).hexdigest(),
                    n=[8, 16], g=4, generator_seed=101, unit_bytes=1024,
                    topology='native 3DC server400 gateway400 WAN100/50us',
                    note='development control; full pipeline, no topology refinement or spreadout baseline',
                    sources={name: hashlib.sha256((study.ROOT/name).read_bytes()).hexdigest()
                             for name in ('simulation/bvn_decompose_main.cpp',
                                          'ds/decomposition/scaling_control.py', 'ds/decomposition/study.py')})
    save(args.out/'metadata.json', metadata)
    rows = []
    for n in (8, 16):
        for kind in ('balanced', 'zipf'):
            data = study.fixed_load_matrix(n, 4, kind, 'pipeline')
            save(args.out/f'{kind}-n{n}.input.json', data)
            for strategy in (0, 1):
                for scaling in (0, 1):
                    command = [str(study.BIN), '--mode', 'pipeline', '--n', str(n), '--g', '4',
                               '--strategy', str(strategy), '--repeat', '3', '--topology', '1',
                               '--scaling', str(scaling)]
                    result = subprocess.run(command, input=' '.join(map(str, data)), text=True,
                                            capture_output=True, check=True, timeout=90)
                    raw = json.loads(result.stdout)
                    study.audit_pipeline(data, n, 4, raw)
                    assert raw['scaling_enabled'] == bool(scaling)
                    network, dispatch = raw['network_stages_us'], raw['dispatch_us']
                    total = raw['balance_us'] + max(raw['intrinsic_us'], network[0] if network else 0)
                    total += sum(max(t, d) for t, d in zip(network[1:], dispatch))
                    if dispatch:
                        total += dispatch[-1]
                    assert abs(total-raw['total_us']) < 1e-6
                    factors = sorted({step['alpha']//step['alpha_scaled'] for step in raw['steps']})
                    row = dict(n=n, kind=kind, strategy=strategy, scaling=scaling,
                               K=raw['stage_count'], total_ms=raw['total_us']/1000,
                               decomposition_cpu_ms=statistics.median(raw['cpu_samples_us'])/1000,
                               planning_wall_ms=statistics.median(raw['planning_wall_samples_us'])/1000,
                               frequency_scaling_factors=factors)
                    rows.append(row)
                    save(args.out/f'{kind}-n{n}-s{strategy}-scale{scaling}.json', raw)
                    print(row, flush=True)
    assert hashlib.sha256(study.BIN.read_bytes()).hexdigest() == metadata['binary_sha256']
    for name, digest in metadata['sources'].items():
        assert hashlib.sha256((study.ROOT/name).read_bytes()).hexdigest() == digest
    save(args.out/'summary.json', rows)
    metadata.update(complete=True, outputs=len(rows),
                    timing='communication excludes CPU; decomposition CPU excludes scaling, planning wall includes it')
    save(args.out/'metadata.json', metadata)


if __name__ == '__main__':
    main()
