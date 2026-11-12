#!/usr/bin/env python3
"""Bounded complete-BvN/topology ablation, not a full FAST pipeline benchmark."""
import argparse
from collections import Counter
import hashlib
import json
import math
import pathlib
import subprocess
import sys
import time
import unittest

import dynamic_small_cases as dynamic
import study


def summarize(rounds, delivered, topology):
    totals = [sum(item['resource_bytes'][r] for item in rounds)
              for r in range(len(topology['resources']))]
    expected = [0]*len(totals)
    for pair, amount in enumerate(delivered):
        for r in topology['paths'][pair]['resource_ids']:
            expected[r] += amount
    assert totals == expected
    return dict(K_real=sum(any(item['resource_bytes']) for item in rounds),
                network_sum_us=math.fsum(item['total_us'] for item in rounds),
                serialization_sum_us=math.fsum(item['serial_us'] for item in rounds),
                propagation_sum_us=math.fsum(item['alpha_us'] for item in rounds),
                delivered_bytes=delivered, resource_total_bytes=totals, rounds=rounds)


def evaluate(auxiliary, data, n, plan, topology, unit_bytes):
    if len(data) != n*n or any(x < 0 for x in data) or any(data[i*n+i] for i in range(n)):
        raise ValueError('nonnegative true demand with zero diagonal required')
    if any(a < d for a, d in zip(auxiliary, data)):
        raise ValueError('padded auxiliary matrix must dominate true demand')
    remaining_aux, remaining_true = auxiliary[:], [x*unit_bytes for x in data]
    rounds = []
    for item in plan:
        remaining_aux = dynamic.consume_aux(remaining_aux, n, item)
        remaining_true, flows = dynamic.project_true(remaining_true, n, item, unit_bytes)
        cost = dynamic.cost_stage(flows, topology)
        cost.update(q=item['q'], permutation=item['permutation'])
        rounds.append(cost)
    if any(remaining_aux) or any(remaining_true):
        raise ValueError('incomplete auxiliary or true reconstruction')
    result = summarize(rounds, [x*unit_bytes for x in data], topology)
    result.update(K_aux=len(plan), q_sequence=[item['q'] for item in plan])
    return result


def refine(auxiliary, data, n, original, topology, unit_bytes, budget):
    """Research-only bounded cycle guard; prefix states advance once, no full-plan restarts."""
    if budget < 0:
        raise ValueError('nonnegative proposal budget required')
    plan = [dynamic.stage(item['permutation'], item['q']) for item in original]
    auxiliary_before, true_before = auxiliary[:], [x*unit_bytes for x in data]
    attempts, accepted, reasons, trace = 0, 0, Counter(), []
    started = time.process_time_ns()
    for window in range(len(plan)-1):
        while attempts < budget:
            if plan[window]['q'] != plan[window+1]['q']:
                reasons['unequal_coefficients'] += 1
                break
            cycles = dynamic.cycles_between(plan[window]['permutation'], plan[window+1]['permutation'])
            if len(cycles) < 2:
                reasons['single_cycle_order_only'] += 1
                break
            changed = False
            for cycle in cycles:
                if attempts >= budget:
                    break
                attempts += 1
                result = dynamic.check_window(auxiliary_before, true_before, n, plan[window],
                                              plan[window+1], cycle, topology, unit_bytes)
                reasons[result['reason']] += 1
                record = dict(window=window, cycle=cycle,
                              result={k: v for k, v in result.items()
                                      if k in ('legal', 'reason', 'gain_us', 'before_us', 'after_us')})
                if result.get('improved'):
                    record['old_stages'] = plan[window:window+2]
                    record['new_stages'] = result['new_stages']
                    plan[window:window+2] = result['new_stages']
                    accepted += 1
                    changed = True
                trace.append(record)
                if changed:
                    break
            if not changed:
                break
        auxiliary_before = dynamic.consume_aux(auxiliary_before, n, plan[window])
        true_before, _ = dynamic.project_true(true_before, n, plan[window], unit_bytes)
    return dict(plan=plan, attempts=attempts, accepted=accepted, budget=budget,
                reason_counts=dict(reasons), traces=trace,
                cpu_us=(time.process_time_ns()-started)/1000)


def spreadout(data, n, topology, unit_bytes):
    """Endpoint cyclic rounds, variable pair sizes; explicitly NOT a real NCCL measurement."""
    rounds, delivered = [], [0]*(n*n)
    for offset in range(1, n):
        flows = []
        for src in range(n):
            dst = (src+offset) % n
            amount = data[src*n+dst]*unit_bytes
            if amount:
                flows.append(dict(src=src, dst=dst, bytes=amount))
                delivered[src*n+dst] += amount
        rounds.append(dynamic.cost_stage(flows, topology))
    assert delivered == [x*unit_bytes for x in data]
    return summarize(rounds, delivered, topology)


def run_native(data, n, strategy):
    command = [str(study.BIN), '--mode', 'matrix', '--n', str(n), '--g', '1',
               '--strategy', str(strategy), '--budget', '8', '--repeat', '3',
               '--unit-mb', '.001024', '--topology', '0']
    raw = json.loads(subprocess.run(command, input=' '.join(map(str, data)), text=True,
                                    capture_output=True, check=True, timeout=60).stdout)
    study.audit_decomposition(data, n, raw)
    plan = [dynamic.stage(p, step['alpha_scaled'])
            for p, step in zip(raw['permutations'], raw['steps'])]
    return plan, raw


def cases():
    for n in (8, 16):
        for kind in ('balanced', 'perturbed', 'zipf'):
            for seed in ((101, 102, 103) if kind == 'zipf' else (101,)):
                data = study.fixed_load_matrix(n, 1, kind, 'matrix')
                # Existing generator fixes seed101. Relabel every endpoint together
                # to vary placement without changing skew or endpoint load multiset.
                if seed != 101:
                    import random
                    order = list(range(n))
                    random.Random(seed).shuffle(order)
                    data = [data[order[s]*n+order[d]] for s in range(n) for d in range(n)]
                profiles = {}
                for label in ('equal-propagation', 'wan52us-extra'):
                    top = dynamic.physical_topology([s*3//n for s in range(n)], 'equal')
                    if label == 'wan52us-extra':
                        for path in top['paths']:
                            if top['dc'][path['src']] != top['dc'][path['dst']]:
                                path['propagation_us'], path['rtt_us'] = 52.8, 105.6
                    top['latency'] = label
                    profiles[label] = top
                yield dict(name=f'{kind}-n{n}-placement{seed}', n=n, kind=kind,
                           placement_seed=seed, generator_seed=101, data=data, unit_bytes=1024,
                           profiles=profiles, scope='small pilot, not independent traffic seeds')
    for case in dynamic.hand_cases():
        if case['name'] == 'independent-wan-pairs-n8':
            yield dict(name='independent-wan-control', n=8, kind='mechanism-control',
                       data=case['data'], unit_bytes=1 << 20,
                       profiles={key: value for key, value in case['profiles'].items()
                                 if 'misaligned' in key})


def source_hashes():
    names = ['ds/decomposition/combination_pilot.py', 'ds/decomposition/dynamic_small_cases.py',
             'ds/decomposition/study.py', 'simulation/algorithm/all2all.cpp',
             'simulation/scheduler/global.cpp', 'simulation/topology/model.cpp']
    return {name: hashlib.sha256((study.ROOT/name).read_bytes()).hexdigest() for name in names}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=pathlib.Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    for folder in ('cases', 'raw', 'evaluated', 'spreadout'):
        (args.out/folder).mkdir()
    metadata = dict(complete=False, scope='network-stage-sum only; G1 server endpoints; no Local/pipeline',
                    reference='FAST original padded BvN strategy0, scaling disabled',
                    spreadout='cyclic endpoint-round structural reference, not NCCL library',
                    edit_budget=64, threshold_budget=8, native_repeats=3,
                    source_sha256=source_hashes(),
                    binary_sha256=hashlib.sha256(study.BIN.read_bytes()).hexdigest(),
                    revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                                     cwd=study.ROOT, text=True).strip())
    dynamic.save(args.out/'run_metadata.json', metadata)
    rows = []
    for case in cases():
        n, data, unit = case['n'], case['data'], case['unit_bytes']
        dynamic.save(args.out/'cases'/(case['name']+'.json'), case)
        references = {}
        for label, topology in case['profiles'].items():
            reference = spreadout(data, n, topology, unit)
            references[label] = reference
            dynamic.save(args.out/'spreadout'/(case['name']+'-'+label+'.json'), reference)
        shared_auxiliary = None
        baselines = {}
        for strategy in range(4):
            plan, raw = run_native(data, n, strategy)
            dynamic.save(args.out/'raw'/(case['name']+f'-s{strategy}.json'), raw)
            auxiliary = dynamic.matrix_from_plan(n, plan)
            if shared_auxiliary is None:
                shared_auxiliary = auxiliary
            assert auxiliary == shared_auxiliary  # All strategies really see the same padding.
            for label, topology in case['profiles'].items():
                before = evaluate(auxiliary, data, n, plan, topology, unit)
                adjustment = refine(auxiliary, data, n, plan, topology, unit, 64)
                after = evaluate(auxiliary, data, n, adjustment['plan'], topology, unit)
                assert before['K_aux'] == after['K_aux'] and before['q_sequence'] == after['q_sequence']
                assert before['delivered_bytes'] == after['delivered_bytes']
                assert before['resource_total_bytes'] == after['resource_total_bytes']
                assert after['network_sum_us'] <= before['network_sum_us']+1e-9
                if strategy == 0:
                    baselines[label] = before['network_sum_us']
                row = dict(case=case['name'], kind=case['kind'], n=n, profile=label, strategy=strategy,
                           K=before['K_aux'], K_real=before['K_real'], before_us=before['network_sum_us'],
                           after_us=after['network_sum_us'], native_us=baselines[label],
                           spreadout_us=references[label]['network_sum_us'],
                           native_speedup=baselines[label]/after['network_sum_us'],
                           topology_speedup=before['network_sum_us']/after['network_sum_us'],
                           serial_before_us=before['serialization_sum_us'],
                           serial_after_us=after['serialization_sum_us'],
                           prop_before_us=before['propagation_sum_us'],
                           prop_after_us=after['propagation_sum_us'],
                           equal_q_windows=sum(a['q'] == b['q'] for a, b in zip(plan, plan[1:])),
                           accepted=adjustment['accepted'], attempts=adjustment['attempts'],
                           native_cpu_samples_us=raw['cpu_samples_us'],
                           research_refine_cpu_us=adjustment['cpu_us'],
                           reason_counts=adjustment['reason_counts'])
                rows.append(row)
                dynamic.save(args.out/'evaluated'/(case['name']+'-'+label+f'-s{strategy}.json'),
                             dict(row=row, before=before, after=after, adjustment=adjustment))
                print(case['name'], label, strategy, 'K', row['K'],
                      'network', round(row['before_us'], 3), '->', round(row['after_us'], 3),
                      'accept', row['accepted'], flush=True)
    dynamic.save(args.out/'summary.json', rows)
    assert source_hashes() == metadata['source_sha256']
    assert hashlib.sha256(study.BIN.read_bytes()).hexdigest() == metadata['binary_sha256']
    metadata.update(complete=True, paired_evaluations=len(rows), true_cases=len({r['case'] for r in rows}),
                    nonregression_scope='refinement versus its own strategy, not versus native FAST')
    dynamic.save(args.out/'run_metadata.json', metadata)


class CombinationTests(unittest.TestCase):
    def test_padding_is_not_real_traffic_or_a_real_stage(self):
        # Charging the diagonal virtual round or sending its q as real bytes breaks this.
        plan = [dynamic.stage([0, 1], 3), dynamic.stage([1, 0], 2)]
        result = evaluate([3, 2, 2, 3], [0, 1, 1, 0], 2, plan,
                          dynamic.physical_topology([0, 1], 'equal'), 1024)
        self.assertEqual(result['K_aux'], 2)
        self.assertEqual(result['K_real'], 1)
        self.assertEqual(result['delivered_bytes'], [0, 1024, 1024, 0])
        self.assertAlmostEqual(result['network_sum_us'], 1.48192)

    def test_actual_min_rule_is_still_required(self):
        # Accepting a merely feasible coefficient rather than the actual minimum is a bug.
        with self.assertRaises(ValueError):
            evaluate([0, 2, 2, 0], [0, 1, 1, 0], 2,
                     [dynamic.stage([1, 0], 1), dynamic.stage([1, 0], 1)],
                     dynamic.physical_topology([0, 1], 'equal'), 1024)

    def test_guard_reorganizes_independent_resources_without_changing_work(self):
        # Losing a cycle edge, changing q, or rejecting this strict gain breaks this.
        plan = [dynamic.stage([2, 3, 0, 1, 6, 7, 4, 5]),
                dynamic.stage([1, 0, 3, 2, 5, 4, 7, 6])]
        auxiliary = dynamic.matrix_from_plan(8, plan)
        topology = dynamic.physical_topology([0, 0, 1, 1, 2, 3, 2, 3], 'equal')
        result = refine(auxiliary, auxiliary, 8, plan, topology, 1 << 20, 64)
        actual = evaluate(auxiliary, auxiliary, 8, result['plan'], topology, 1 << 20)
        self.assertAlmostEqual(actual['network_sum_us'], 191.54368)
        self.assertEqual(actual['q_sequence'], [1, 1])
        self.assertEqual(dynamic.matrix_from_plan(8, result['plan']), auxiliary)
        self.assertGreater(result['accepted'], 0)
        self.assertLessEqual(result['attempts'], 64)

    def test_zero_budget_leaves_padded_plan_unchanged(self):
        # Confusing auxiliary units and true units would send the extra padded unit.
        plan = [dynamic.stage([1, 0], 2)]
        topology = dynamic.physical_topology([0, 1], 'equal')
        result = refine([0, 2, 2, 0], [0, 1, 1, 0], 2, plan, topology, 1024, 0)
        self.assertEqual(result['plan'], plan)
        self.assertEqual(result['attempts'], 0)
        actual = evaluate([0, 2, 2, 0], [0, 1, 1, 0], 2, result['plan'], topology, 1024)
        self.assertEqual(actual['delivered_bytes'], [0, 1024, 1024, 0])

    def test_spreadout_sends_different_pair_sizes_in_same_round(self):
        # Using a common BvN coefficient here would silently truncate the 7-unit flow.
        result = spreadout([0, 3, 7, 0], 2,
                           dynamic.physical_topology([0, 1], 'equal'), 1024)
        self.assertEqual(result['K_real'], 1)
        self.assertEqual(result['delivered_bytes'], [0, 3072, 7168, 0])
        self.assertAlmostEqual(result['network_sum_us'], 1.97344)


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    else:
        main()
