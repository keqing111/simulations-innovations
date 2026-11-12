#!/usr/bin/env python3
"""Small causal probes for fixed static mappings, without changing core code."""
import argparse
import copy
from fractions import Fraction
import hashlib
import json
import math
import pathlib
import subprocess

import static_study as static
import study


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=pathlib.Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    demand = [0]*16
    # A real permutation: fast LAN pair0<->1 and slow WAN pair2<->3.
    # Unlike an isolated two-flow input, native stuffing cannot simply fill the
    # original fast edges here; its virtual diagonal exposes time slicing.
    for pair in (1, 4, 11, 14):
        demand[pair] = 1024
    heterogeneous = static.fixed_topology(4, 'T1')
    zero_latency = copy.deepcopy(heterogeneous)
    for path in zero_latency['paths']:
        path['propagation_us'] = path['rtt_us'] = Fraction(0)
    cases = [('homogeneous', static.fixed_topology(4, 'T0')),
             ('bandwidth_only_zero_latency', zero_latency),
             ('bandwidth_and_propagation', heterogeneous)]
    rows = []
    for name, topology in cases:
        static.save_json(args.out/(name+'-input.json'), dict(demand=demand, topology=topology))
        for strategy in (0, 1):
            for space in ('bytes', 'time', 'delay'):
                auxiliary, weights = static.map_auxiliary(demand, topology, space)
                command = [str(static.BIN), '--mode', 'matrix', '--n', '4', '--strategy',
                           str(strategy), '--repeat', '1']
                process = subprocess.run(command, input=' '.join(map(str, auxiliary)),
                                         text=True, capture_output=True, check=True)
                raw = json.loads(process.stdout)
                study.audit_decomposition(auxiliary, 4, raw)
                output = static.project_and_evaluate(demand, auxiliary, topology, raw, True)
                static.save_json(args.out/f'{name}-s{strategy}-{space}.json',
                                 dict(auxiliary=auxiliary, fixed_weights=weights,
                                      raw=raw, projected=output))
                rows.append(dict(case=name, strategy=strategy, space=space,
                                 stages=output['K_real'], total_us=output['communication_us'],
                                 propagation_us=output['propagation_sum_us'],
                                 serialization_us=output['serialization_sum_us']))
    # A targeted reference: one legitimate stage sends both original flows.
    simultaneous = static.stage_cost([(s, d, Fraction(1024)) for s, d in ((0, 1), (1, 0), (2, 3), (3, 2))], heterogeneous)
    for strategy in (0, 1):
        selected = {(r['case'], r['space']): r for r in rows if r['strategy'] == strategy}
        zero_bytes = selected['bandwidth_only_zero_latency', 'bytes']
        zero_time = selected['bandwidth_only_zero_latency', 'time']
        assert zero_bytes['stages'] == 1 and zero_time['stages'] == 2
        assert math.isclose(zero_bytes['total_us'], zero_time['total_us'], rel_tol=1e-12)
        actual_bytes = selected['bandwidth_and_propagation', 'bytes']
        actual_time = selected['bandwidth_and_propagation', 'time']
        assert math.isclose(actual_time['total_us']-actual_bytes['total_us'], 52.8, abs_tol=1e-9)
    summary = dict(binary_sha256=hashlib.sha256(static.BIN.read_bytes()).hexdigest(),
                   source_sha256={str(p.relative_to(static.ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (pathlib.Path(__file__).resolve(),
                                            pathlib.Path(static.__file__).resolve(),
                                            pathlib.Path(study.__file__).resolve())},
                   rows=rows, simultaneous_original_flows=simultaneous)
    static.save_json(args.out/'summary.json', summary)
    for row in rows:
        print(row)


if __name__ == '__main__':
    main()
