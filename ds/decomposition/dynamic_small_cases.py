#!/usr/bin/env python3
"""Bounded topology counterexamples and an offline guarded two-round micro-edit."""
import argparse
from collections import Counter
import hashlib
import itertools
import json
import math
import pathlib
import random
import subprocess
import sys
import time
import unittest

import study

ROOT = pathlib.Path(__file__).resolve().parents[2]
BIN = ROOT/'simulation/build/bvn_decompose_simulator'
UNIT_BYTES = 1 << 20


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def stage(permutation, q=1):
    return dict(permutation=list(permutation), q=q)


def matrix_from_plan(n, plan):
    result = [0]*(n*n)
    for item in plan:
        for src, dst in enumerate(item['permutation']):
            result[src*n+dst] += item['q']
    return result


def degree(data, n):
    return max([sum(x > 0 for x in data[s*n:s*n+n]) for s in range(n)] +
               [sum(data[s*n+d] > 0 for s in range(n)) for d in range(n)])


def consume_aux(data, n, item):
    permutation, q = item['permutation'], item['q']
    if sorted(permutation) != list(range(n)) or q <= 0:
        raise ValueError('nonpositive or incomplete stage')
    actual = min(data[s*n+d] for s, d in enumerate(permutation))
    if actual != q:
        raise ValueError(f'actual minimum {actual} differs from q={q}')
    result = data[:]
    for src, dst in enumerate(permutation):
        result[src*n+dst] -= q
    return result


def project_true(remaining, n, item, unit_bytes):
    result, flows = remaining[:], []
    for src, dst in enumerate(item['permutation']):
        pair = src*n+dst
        if src != dst and result[pair]:
            amount = min(result[pair], item['q']*unit_bytes)
            result[pair] -= amount
            flows.append(dict(src=src, dst=dst, bytes=amount))
    return result, flows


def physical_topology(dc, latency):
    n, groups = len(dc), max(dc)+1
    resources = []

    def resource(name, bandwidth):
        index = len(resources)
        resources.append(dict(id=index, name=name, bandwidth_gbps=bandwidth))
        return index

    outgoing = [resource(f'endpoint-out:{s}', 400) for s in range(n)]
    incoming = [resource(f'endpoint-in:{s}', 400) for s in range(n)]
    gateway_out = [resource(f'gateway-out:{d}', 400) for d in range(groups)]
    gateway_in = [resource(f'gateway-in:{d}', 400) for d in range(groups)]
    wan = {(s, d): resource(f'wan:{s}:{d}', 100)
           for s in range(groups) for d in range(groups) if s != d}
    paths = []
    for src in range(n):
        for dst in range(n):
            route, tau = [], 0.0
            if src != dst:
                route, tau = [outgoing[src], incoming[dst]], 1.4
                if dc[src] != dc[dst]:
                    route = [outgoing[src], gateway_out[dc[src]], wan[dc[src], dc[dst]],
                             gateway_in[dc[dst]], incoming[dst]]
                    tau = 1.4 if latency == 'equal' else 5002.8
            paths.append(dict(src=src, dst=dst, resource_ids=route, propagation_us=tau, rtt_us=2*tau))
    return dict(n=n, dc=dc, latency=latency, resources=resources, paths=paths,
                model='max one-way propagation + max resource aggregate serialization',
                full_duplex='each WAN direction is a distinct resource')


def cost_stage(flows, topology):
    loads = [0]*len(topology['resources'])
    alpha = 0.0
    for flow in flows:
        path = topology['paths'][flow['src']*topology['n']+flow['dst']]
        alpha = max(alpha, path['propagation_us'])
        for resource in path['resource_ids']:
            loads[resource] += flow['bytes']
    work = [.008*amount/resource['bandwidth_gbps']
            for amount, resource in zip(loads, topology['resources'])]
    serial = max(work, default=0.0)
    return dict(alpha_us=alpha, serial_us=serial, total_us=alpha+serial,
                resource_bytes=loads, resource_work_us=work,
                active_wan_resources=[resource['name'] for resource, amount in
                                      zip(topology['resources'], loads)
                                      if resource['name'].startswith('wan:') and amount])


def evaluate_plan(data, n, plan, topology, unit_bytes, raw_steps=None):
    auxiliary, true = data[:], [x*unit_bytes for x in data]
    delivered = [0]*(n*n)
    rounds, resource_totals = [], [0]*len(topology['resources'])
    for index, item in enumerate(plan):
        before_degree = degree(auxiliary, n)
        eliminated = sum(auxiliary[s*n+d] == item['q'] for s, d in enumerate(item['permutation']))
        auxiliary = consume_aux(auxiliary, n, item)
        true, flows = project_true(true, n, item, unit_bytes)
        for flow in flows:
            delivered[flow['src']*n+flow['dst']] += flow['bytes']
        cost = cost_stage(flows, topology)
        resource_totals = [a+b for a, b in zip(resource_totals, cost['resource_bytes'])]
        cost.update(index=index, q=item['q'], eliminated=eliminated,
                    degree_before=before_degree, degree_after=degree(auxiliary, n),
                    permutation=item['permutation'], flows=flows)
        if raw_steps is not None:
            expected = raw_steps[index]
            assert item['q'] == expected['alpha_scaled']
            for field in ('eliminated', 'degree_before', 'degree_after'):
                assert cost[field] == expected[field]
        rounds.append(cost)
    if any(auxiliary) or any(true) or delivered != [x*unit_bytes for x in data]:
        raise ValueError('complete plan does not reconstruct auxiliary and true demand')
    expected_resources = [0]*len(resource_totals)
    for pair, amount in enumerate(delivered):
        for resource in topology['paths'][pair]['resource_ids']:
            expected_resources[resource] += amount
    assert resource_totals == expected_resources
    return dict(K=len(plan), q_sequence=[item['q'] for item in plan],
                network_sum_us=math.fsum(r['total_us'] for r in rounds),
                serialization_sum_us=math.fsum(r['serial_us'] for r in rounds),
                propagation_sum_us=math.fsum(r['alpha_us'] for r in rounds),
                wan_active_stages=sum(bool(r['active_wan_resources']) for r in rounds),
                resource_total_bytes=resource_totals, pair_error_units=0, rounds=rounds)


def cycles_between(p, q):
    n = len(p)
    if sorted(p) != list(range(n)) or sorted(q) != list(range(n)):
        raise ValueError('cycles require perfect permutations')
    inverse = {dst: src for src, dst in enumerate(q)}
    visited, cycles = set(), []
    for start in range(n):
        if start in visited:
            continue
        cycle, current = [], start
        while current not in visited:
            visited.add(current); cycle.append(current)
            current = inverse[p[current]]
        if len(cycle) > 1:
            cycles.append(cycle)
    return cycles


def check_window(auxiliary_before, true_before, n, left, right, cycle, topology, unit_bytes):
    if left['q'] != right['q']:
        return dict(legal=False, reason='unequal_coefficients')
    p, q = left['permutation'], right['permutation']
    cycles = cycles_between(p, q)
    if len(cycles) < 2:
        return dict(legal=False, reason='single_cycle_order_only')
    if set(cycle) not in [set(candidate) for candidate in cycles]:
        return dict(legal=False, reason='not_a_whole_proper_cycle')
    new_p, new_q = p[:], q[:]
    for row in cycle:
        new_p[row], new_q[row] = q[row], p[row]
    new_stages = [stage(new_p, left['q']), stage(new_q, right['q'])]
    assert matrix_from_plan(n, [left, right]) == matrix_from_plan(n, new_stages)
    original_auxiliary_after = consume_aux(consume_aux(auxiliary_before, n, left), n, right)
    candidate = auxiliary_before[:]
    for index, item in enumerate(new_stages):
        actual = min(candidate[s*n+d] for s, d in enumerate(item['permutation']))
        if actual != item['q']:
            return dict(legal=False, reason=('first' if index == 0 else 'second')+'_actual_min_changed',
                        required_q=item['q'], actual_min=actual)
        candidate = consume_aux(candidate, n, item)
    assert candidate == original_auxiliary_after
    true_original, true_candidate = true_before[:], true_before[:]
    old_cost, new_cost = [], []
    for original, changed in zip((left, right), new_stages):
        true_original, flows = project_true(true_original, n, original, unit_bytes)
        old_cost.append(cost_stage(flows, topology))
        true_candidate, flows = project_true(true_candidate, n, changed, unit_bytes)
        new_cost.append(cost_stage(flows, topology))
    assert true_original == true_candidate
    before = math.fsum(c['total_us'] for c in old_cost)
    after = math.fsum(c['total_us'] for c in new_cost)
    tolerance = 64*sys.float_info.epsilon*max(1, abs(before), abs(after))
    improved = after < before-tolerance
    return dict(legal=True, improved=improved, reason='accepted' if improved else 'no_strict_network_gain',
                before_us=before, after_us=after, gain_us=before-after,
                new_stages=new_stages, auxiliary_after=candidate, true_after=true_candidate,
                old_cost=old_cost, new_cost=new_cost)


def improve_windows(data, n, original, topology, unit_bytes, budget=64):
    plan = [stage(item['permutation'], item['q']) for item in original]
    attempts, skipped, traces = 0, Counter(), []
    started = time.process_time_ns()
    for window in range(len(plan)-1):
        changed = True
        while changed and attempts < budget:
            changed = False
            if plan[window]['q'] != plan[window+1]['q']:
                skipped['unequal_coefficients'] += 1; break
            auxiliary, true = data[:], [x*unit_bytes for x in data]
            for item in plan[:window]:
                auxiliary = consume_aux(auxiliary, n, item)
                true, _ = project_true(true, n, item, unit_bytes)
            cycles = cycles_between(plan[window]['permutation'], plan[window+1]['permutation'])
            if len(cycles) < 2:
                skipped['single_cycle_order_only'] += 1; break
            for cycle in cycles:
                if attempts >= budget:
                    break
                attempts += 1
                result = check_window(auxiliary, true, n, plan[window], plan[window+1],
                                      cycle, topology, unit_bytes)
                record = dict(window=window, cycle=cycle, result=result)
                if result.get('improved'):
                    record['prefix_auxiliary'] = auxiliary
                    record['prefix_true_bytes'] = true
                    record['old_stages'] = plan[window:window+2]
                    plan[window:window+2] = result['new_stages']
                    changed = True
                traces.append(record)
                if changed:
                    break
    elapsed = (time.process_time_ns()-started)/1000
    return dict(plan=plan, attempts=attempts, budget=budget,
                accepted=sum(t['result'].get('improved', False) for t in traces),
                window_skips=dict(skipped), reason_counts=dict(Counter(t['result']['reason'] for t in traces)),
                cpu_us=elapsed, traces=traces)


def native_plan(data, n, strategy):
    command = [str(BIN), '--mode', 'matrix', '--n', str(n), '--g', '1',
               '--strategy', str(strategy), '--budget', '8', '--repeat', '1',
               '--unit-mb', str(UNIT_BYTES/1e6), '--topology', '0']
    start = time.perf_counter_ns()
    done = subprocess.run(command, input=' '.join(map(str, data)), text=True,
                          capture_output=True, check=True, timeout=30)
    wall = (time.perf_counter_ns()-start)/1000
    raw = json.loads(done.stdout)
    study.audit_decomposition(data, n, raw)
    assert all(step['alpha'] == step['alpha_scaled'] for step in raw['steps'])
    plan = [stage(p, step['alpha_scaled']) for p, step in zip(raw['permutations'], raw['steps'])]
    assert matrix_from_plan(n, plan) == data  # Every case is already exactly balanced.
    return plan, raw, wall


def two_dc_references(n):
    half = n//2
    cyclic = [stage([(src+offset) % n for src in range(n)]) for offset in range(1, n)]
    packed = []
    for offset in range(1, half):
        packed.append(stage([(src//half)*half+(src % half+offset) % half for src in range(n)]))
    for offset in range(half):
        packed.append(stage([((src//half+1) % 2)*half+(src % half+offset) % half for src in range(n)]))
    return dict(cyclic_spread=cyclic, dc_packed=packed)


def unit_factorizations(data, n):
    """Offline test reference ONLY; n4 dense or n8 degree2, never an online solver."""
    if n > 8 or any(value not in (0, 1) for value in data):
        raise ValueError('bounded unit oracle only')
    eligible = [permutation for permutation in itertools.permutations(range(n))
                if all(data[src*n+dst] for src, dst in enumerate(permutation))]
    result = []

    def search(remaining, plan):
        if not any(remaining):
            result.append(plan); return
        pair = next(i for i, value in enumerate(remaining) if value)
        src, dst = divmod(pair, n)
        for permutation in eligible:
            if permutation[src] == dst and all(remaining[s*n+d] for s, d in enumerate(permutation)):
                current = stage(permutation)
                search(consume_aux(remaining, n, current), plan+[current])
    search(data, [])
    return result


def plan_signature(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()


def hand_cases():
    cases = []
    for n in (4, 6, 8):
        data = [int(src != dst) for src in range(n) for dst in range(n)]
        profiles = {f'2dc-{latency}': physical_topology([src//(n//2) for src in range(n)], latency)
                    for latency in ('equal', 'long')}
        references = two_dc_references(n)
        if n == 4:
            references.update({f'exact-{i}': plan for i, plan in enumerate(unit_factorizations(data, n))})
        cases.append(dict(name=f'uniform-n{n}', n=n, data=data, profiles=profiles,
                          references=references, reference_exact=True, reference_proof='two-DC L formula'))
    n = 8
    data = matrix_from_plan(n, [stage([s ^ 1 for s in range(n)]), stage([s ^ 2 for s in range(n)])])
    profiles = {f'4dc-{alignment}-{latency}': physical_topology(dc, latency)
                for alignment, dc in [('aligned', [0, 0, 1, 1, 2, 2, 3, 3]),
                                      ('misaligned', [0, 0, 1, 1, 2, 3, 2, 3])]
                for latency in ('equal', 'long')}
    cases.append(dict(name='independent-wan-pairs-n8', n=n, data=data, profiles=profiles,
                      references={f'exact-{i}': plan for i, plan in enumerate(unit_factorizations(data, n))},
                      reference_exact=True, reference_proof='all unit degree2 factorizations, test-only oracle'))
    plan = [stage([1, 0, 3, 2]), stage([2, 3, 0, 1]), stage([3, 2, 1, 0], 2)]
    cases.append(dict(name='weighted-tail-n4', n=4, data=matrix_from_plan(4, plan),
                      profiles={f'2dc-{latency}': physical_topology([0, 0, 1, 1], latency)
                                for latency in ('equal', 'long')},
                      references=dict(valid_q1_q1_q2=plan), reference_exact=False,
                      reference_proof='valid reference only, not global optimum'))
    return cases


def weighted_balanced(n, seed):
    random_source = random.Random(seed)
    result = [0]*(n*n)
    for _ in range(2*n):
        permutation = list(range(n))
        while True:
            random_source.shuffle(permutation)
            if all(src != dst for src, dst in enumerate(permutation)):
                break
        weight = random_source.randint(1, 11)
        for src, dst in enumerate(permutation):
            result[src*n+dst] += weight
    return result


def stage_count_witnesses(directory, limit=80):
    witnesses, comparisons = {}, []
    jobs = [(n, seed) for n in (4, 6) for seed in range(101, 141)][:limit]
    for index, (n, seed) in enumerate(jobs):
        if len(witnesses) == 3:
            break
        data = weighted_balanced(n, seed)
        key = f'weighted-n{n}-seed{seed}'
        save(directory/(key+'.input.json'), dict(n=n, seed=seed, data=data,
                                               construction='sum of 2N weighted derangements'))
        native, native_raw, _ = native_plan(data, n, 0)
        save(directory/(key+'-s0.json'), native_raw)
        for strategy in (1, 2, 3):
            if strategy in witnesses:
                continue
            plan, raw, _ = native_plan(data, n, strategy)
            save(directory/(key+f'-s{strategy}.json'), raw)
            row = dict(case=key, n=n, seed=seed, strategy=strategy,
                       K_native=len(native), K_strategy=len(plan),
                       native_cpu_us=native_raw['cpu_us'], strategy_cpu_us=raw['cpu_us'])
            comparisons.append(row)
            if len(plan) > len(native):
                witnesses[strategy] = dict(**row, data=data, native_plan=native, strategy_plan=plan)
                print('K witness', strategy, key, len(native), '->', len(plan), flush=True)
    result = dict(matrix_budget=limit, generated_matrices=len({row['case'] for row in comparisons}),
                  comparisons=comparisons, witnesses=witnesses,
                  not_found_in_budget=[s for s in (1, 2, 3) if s not in witnesses],
                  sampling='bounded counterexample search; stop per policy at first loss, not a rate estimate')
    save(directory/'summary.json', result)
    return result


def hashes():
    names = ['ds/decomposition/dynamic_small_cases.py', 'ds/decomposition/study.py',
             'simulation/algorithm/all2all.cpp', 'simulation/algorithm/all2all.h',
             'simulation/bvn_decompose_main.cpp']
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=pathlib.Path, default=ROOT/'ds/decomposition/dynamic-small-results-20261111')
    parser.add_argument('--edit-budget', type=int, default=64)
    parser.add_argument('--matrix-budget', type=int, default=80)
    args = parser.parse_args()
    if not 1 <= args.edit_budget <= 64 or not 1 <= args.matrix_budget <= 80:
        parser.error('bounded edit<=64, weighted matrix<=80 required')
    args.out.mkdir(parents=True, exist_ok=False)
    for subdirectory in ('cases', 'raw', 'evaluated', 'stagecount'):
        (args.out/subdirectory).mkdir()
    metadata = dict(complete=False, strategies=[0, 1, 2, 3], repeat=1, native_scaling='off',
                    unit_bytes=UNIT_BYTES, edit_budget=args.edit_budget, matrix_budget=args.matrix_budget,
                    source_sha256=hashes(), binary_sha256=hashlib.sha256(BIN.read_bytes()).hexdigest(),
                    revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    compiler=subprocess.check_output(['g++', '--version'], text=True).splitlines()[0],
                    scope='research-only network-stage sum, not FAST pipeline or GPU dispatch',
                    latency_profiles=dict(equal_us=1.4, long_wan_us=5002.8),
                    runtime='CLI process CPU separate from subprocess wall and Python guarded-search CPU')
    save(args.out/'run_metadata.json', metadata)
    rows, blindness, crossover = [], [], []
    for case in hand_cases():
        name, n, data = case['name'], case['n'], case['data']
        save(args.out/'cases'/(name+'.json'), case)
        reference_results = {}
        for profile, topology in case['profiles'].items():
            evaluated = {label: evaluate_plan(data, n, plan, topology, UNIT_BYTES)
                         for label, plan in case['references'].items()}
            label = min(evaluated, key=lambda item: evaluated[item]['network_sum_us'])
            reference_results[profile] = dict(name=label, plan=case['references'][label],
                                              result=evaluated[label], exact=case['reference_exact'],
                                              proof=case['reference_proof'])
        for strategy in range(4):
            plan, raw, wall = native_plan(data, n, strategy)
            save(args.out/'raw'/(name+f'-s{strategy}.json'), raw)
            if name == 'uniform-n4':
                # Two predeclared payload regimes on the SAME routes, no new CLI search.
                control = physical_topology([0, 0, 1, 1], 'long')
                for path in control['paths']:
                    if control['dc'][path['src']] != control['dc'][path['dst']]:
                        path['propagation_us'], path['rtt_us'] = 52.8, 105.6
                for payload in (1 << 20, 16 << 20):
                    actual = evaluate_plan(data, n, plan, control, payload)
                    candidates = {label: evaluate_plan(data, n, alternative, control, payload)
                                  for label, alternative in two_dc_references(n).items()}
                    crossover.append(dict(strategy=strategy, unit_bytes=payload,
                                          plan=plan, topology=control, result=actual,
                                          references=candidates,
                                          scope='fixed matrix and routes; only physical payload scale changes'))
            signatures = set()
            for profile, topology in case['profiles'].items():
                baseline = evaluate_plan(data, n, plan, topology, UNIT_BYTES, raw['steps'])
                micro = improve_windows(data, n, plan, topology, UNIT_BYTES, args.edit_budget)
                improved = evaluate_plan(data, n, micro['plan'], topology, UNIT_BYTES)
                assert baseline['K'] == improved['K'] and baseline['q_sequence'] == improved['q_sequence']
                assert baseline['resource_total_bytes'] == improved['resource_total_bytes']
                assert improved['network_sum_us'] <= baseline['network_sum_us']+1e-9
                signatures.add(plan_signature(plan))
                record = dict(case=name, profile=profile, strategy=strategy,
                              native_plan_signature=plan_signature(plan), native_plan=plan,
                              baseline=baseline, guarded_search=micro, improved=improved,
                              reference=reference_results[profile], native_cpu_us=raw['cpu_us'],
                              subprocess_wall_us=wall)
                save(args.out/'evaluated'/(name+'-'+profile+f'-s{strategy}.json'), record)
                row = dict(case=name, n=n, profile=profile, strategy=strategy, K=baseline['K'],
                           before_us=baseline['network_sum_us'], after_us=improved['network_sum_us'],
                           gain_us=baseline['network_sum_us']-improved['network_sum_us'],
                           accepted=micro['accepted'], attempts=micro['attempts'],
                           reference_us=reference_results[profile]['result']['network_sum_us'],
                           reference_exact=case['reference_exact'], native_cpu_us=raw['cpu_us'],
                           research_micro_cpu_us=micro['cpu_us'])
                rows.append(row)
                print(name, profile, strategy, 'K', row['K'], 'network', round(row['before_us'], 6),
                      '->', round(row['after_us'], 6), 'accepted', row['accepted'], flush=True)
            assert len(signatures) == 1
            blindness.append(dict(case=name, strategy=strategy, physical_profiles=len(case['profiles']),
                                  schedule_signature=next(iter(signatures)),
                                  reason='same native matrix plan reused unchanged under every fixed physical profile'))
    save(args.out/'summary.json', rows)
    save(args.out/'topology_blindness.json', blindness)
    save(args.out/'payload_crossover.json', crossover)
    witnesses = stage_count_witnesses(args.out/'stagecount', args.matrix_budget)
    metadata['source_sha256_after'] = hashes()
    metadata['binary_sha256_after'] = hashlib.sha256(BIN.read_bytes()).hexdigest()
    assert metadata['source_sha256_after'] == metadata['source_sha256']
    assert metadata['binary_sha256_after'] == metadata['binary_sha256']
    metadata.update(complete=True, physical_evaluations=len(rows), crossover_evaluations=len(crossover), native_hand_cases=5,
                    accepted_edits=sum(row['accepted'] for row in rows),
                    stagecount_matrices=witnesses['generated_matrices'],
                    stagecount_witness_policies=list(witnesses['witnesses']))
    save(args.out/'run_metadata.json', metadata)


class GuardTests(unittest.TestCase):
    def test_equal_q_window_preserves_nonempty_tail_and_real_delivery(self):
        p, q, tail = [1, 0, 3, 2], [2, 3, 0, 1], [3, 2, 1, 0]
        data = matrix_from_plan(4, [stage(p), stage(q), stage(tail, 2)])
        topology = physical_topology([0, 0, 1, 1], 'equal')
        result = check_window(data, [x*UNIT_BYTES for x in data], 4, stage(p), stage(q),
                              [0, 3], topology, UNIT_BYTES)
        self.assertTrue(result['legal'])
        self.assertTrue(result['improved'])
        self.assertEqual(result['auxiliary_after'], matrix_from_plan(4, [stage(tail, 2)]))
        self.assertEqual(result['true_after'], [x*UNIT_BYTES for x in result['auxiliary_after']])
        before = evaluate_plan(data, 4, [stage(p), stage(q), stage(tail, 2)], topology, UNIT_BYTES)
        after = evaluate_plan(data, 4, result['new_stages']+[stage(tail, 2)], topology, UNIT_BYTES)
        self.assertEqual(before['K'], after['K'])
        self.assertLess(after['network_sum_us'], before['network_sum_us'])

    def test_actual_min_increase_in_either_new_round_is_rejected(self):
        data = [0, 1, 2, 2, 3, 0, 1, 1, 2, 1, 0, 2, 0, 3, 2, 0]
        p, q = [1, 0, 3, 2], [2, 3, 0, 1]
        topology = physical_topology([0, 0, 1, 1], 'equal')
        for cycle, reason in [([0, 3], 'first_actual_min_changed'),
                              ([1, 2], 'second_actual_min_changed')]:
            result = check_window(data, [x*UNIT_BYTES for x in data], 4, stage(p), stage(q),
                                  cycle, topology, UNIT_BYTES)
            self.assertFalse(result['legal'])
            self.assertEqual(result['reason'], reason)

    def test_single_alternating_cycle_only_swaps_order(self):
        p, q = [1, 2, 0], [2, 0, 1]
        data = matrix_from_plan(3, [stage(p), stage(q)])
        result = check_window(data, [x*UNIT_BYTES for x in data], 3, stage(p), stage(q),
                              [0, 1, 2], physical_topology([0, 0, 1], 'equal'), UNIT_BYTES)
        self.assertFalse(result['legal'])
        self.assertEqual(result['reason'], 'single_cycle_order_only')

    def test_long_wan_can_reject_same_structural_exchange(self):
        p, q = [1, 0, 3, 2], [2, 3, 0, 1]
        data = matrix_from_plan(4, [stage(p), stage(q)])
        result = check_window(data, [x*UNIT_BYTES for x in data], 4, stage(p), stage(q),
                              [0, 3], physical_topology([0, 0, 1, 1], 'long'), UNIT_BYTES)
        self.assertTrue(result['legal'])
        self.assertFalse(result['improved'])
        self.assertGreater(result['after_us'], result['before_us'])


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    else:
        main()
