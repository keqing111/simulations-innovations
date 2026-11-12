#!/usr/bin/env python3
"""Exact static-space pilot using the unchanged complete native BvN CLI."""
import argparse
from fractions import Fraction
import hashlib
import json
import math
import pathlib
import statistics
import subprocess
import sys
import tempfile
import time
import unittest

import study

ROOT = pathlib.Path(__file__).resolve().parents[2]
BIN = ROOT / 'simulation/build/bvn_decompose_simulator'
NATIVE_BIN = ROOT / 'simulation/build/bvn_simulator'
SPACES = ('bytes', 'time', 'delay')
TOPOLOGIES = ('T0', 'T1', 'T2')
KINDS = ('balanced', 'perturbed', 'zipf')


def json_default(value):
    if isinstance(value, Fraction):
        return {'numerator': value.numerator, 'denominator': value.denominator}
    raise TypeError(type(value).__name__)


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=json_default)+'\n')


def fixed_topology(n, kind):
    if kind not in TOPOLOGIES:
        raise ValueError('unknown topology')
    dc = [s*3//n for s in range(n)]
    resources = []

    def resource(name, bandwidth):
        index = len(resources)
        resources.append(dict(id=index, name=name, bandwidth_gbps=bandwidth))
        return index

    outgoing = [resource(f'endpoint-out:{s}', 400) for s in range(n)]
    incoming = [resource(f'endpoint-in:{s}', 400) for s in range(n)]
    gateways_out, gateways_in, wan = [], [], {}
    if kind != 'T0':
        gateways_out = [resource(f'gateway-out:{d}', 400) for d in range(3)]
        gateways_in = [resource(f'gateway-in:{d}', 400) for d in range(3)]
        wan = {(src, dst): resource(f'wan:{src}:{dst}', 100)
               for src in range(3) for dst in range(3) if src != dst}
    paths = []
    for src in range(n):
        for dst in range(n):
            ids, delay = [], Fraction(0)
            if src != dst:
                ids = [outgoing[src], incoming[dst]]
                delay = Fraction('1.4')
                if kind != 'T0' and dc[src] != dc[dst]:
                    ids += [gateways_out[dc[src]], wan[dc[src], dc[dst]], gateways_in[dc[dst]]]
                    delay = Fraction('5002.8' if kind == 'T2' and {dc[src], dc[dst]} == {0, 1}
                                     else '52.8')
            paths.append(dict(src=src, dst=dst, propagation_us=delay, rtt_us=2*delay,
                              resource_ids=ids))
    return dict(n=n, kind=kind, dc=dc, resources=resources, paths=paths,
                model='max active one-way propagation + max aggregate resource work',
                path_selection='fixed; identical across all spaces')


def map_auxiliary(demand, topology, space, delay_alpha=1):
    """Exact integer ticks only; w=Z/D is fixed for this complete decomposition."""
    if space not in SPACES:
        raise ValueError('unknown space')
    n = topology['n']
    if len(demand) != n*n or any(not isinstance(x, int) or x < 0 for x in demand):
        raise ValueError('demand must be a nonnegative integer N squared byte matrix')
    auxiliary, weights = [], []
    for pair, amount in enumerate(demand):
        if pair//n == pair % n and amount:
            raise ValueError('nonzero endpoint diagonal')
        if not amount:
            auxiliary.append(0); weights.append(Fraction(0)); continue
        path = topology['paths'][pair]
        bandwidth = min(topology['resources'][r]['bandwidth_gbps'] for r in path['resource_ids'])
        z = Fraction(amount, 1024)*128
        if space != 'bytes':
            z *= Fraction(400, bandwidth)
        if space == 'delay':
            z += Fraction(delay_alpha)*path['propagation_us']*6250
        if z.denominator != 1:
            raise ValueError(f'pair {pair//n}->{pair%n} is not exactly representable in fixed ticks')
        if not 0 < z <= 2**31-1:
            raise OverflowError('native INT32_MAX cell boundary')
        auxiliary.append(int(z)); weights.append(z/amount)
    limit = 2**32-1-n
    if any(sum(auxiliary[s*n:s*n+n]) > limit for s in range(n)) or any(
            sum(auxiliary[s*n+d] for s in range(n)) > limit for d in range(n)):
        raise OverflowError('native UINT32_MAX aggregate boundary')
    return auxiliary, weights


def run_decomposition(auxiliary, n, repeat):
    command = [str(BIN), '--mode', 'matrix', '--n', str(n), '--g', '1',
               '--strategy', '0', '--budget', '8', '--repeat', str(repeat),
               '--unit-mb', '0.000001', '--topology', '0']
    payload = ' '.join(map(str, auxiliary))
    started = time.perf_counter_ns()
    process = subprocess.run(command, input=payload, text=True, capture_output=True,
                             check=True, timeout=180)
    wall_us = (time.perf_counter_ns()-started)/1000
    raw = json.loads(process.stdout)
    if raw['strategy'] != 0 or raw['mode'] != 'matrix':
        raise ValueError('static experiment must keep complete native strategy0')
    if any(step['alpha'] != step['alpha_scaled'] for step in raw['steps']):
        raise ValueError('static experiment requires native scaling off')
    return raw, wall_us


def stage_cost(flows, topology):
    loads = [Fraction(0) for _ in topology['resources']]
    alpha = Fraction(0)
    for src, dst, amount in flows:
        if amount <= 0 or src == dst:
            raise ValueError('invalid real flow')
        path = topology['paths'][src*topology['n']+dst]
        alpha = max(alpha, path['propagation_us'])
        for resource in path['resource_ids']:
            loads[resource] += amount
    serial = max((.008*float(load)/resource['bandwidth_gbps']
                  for load, resource in zip(loads, topology['resources'])), default=0.0)
    result = dict(alpha_us=float(alpha), serial_us=serial, total_us=float(alpha)+serial,
                  resource_bytes=loads)
    if not math.isfinite(result['total_us']):
        raise OverflowError('nonfinite static physical stage cost')
    return result


def demand_resource_totals(demand, topology):
    totals = [Fraction(0) for _ in topology['resources']]
    for pair, amount in enumerate(demand):
        for resource in topology['paths'][pair]['resource_ids']:
            totals[resource] += amount
    return totals


def project_and_evaluate(demand, auxiliary, topology, raw, capture_flows=False):
    n = topology['n']
    remaining = list(map(Fraction, demand))
    delivered = [Fraction(0)]*(n*n)
    weights = [Fraction(z, amount) if amount else Fraction(0)
               for z, amount in zip(auxiliary, demand)]
    stages, empty_indices = [], []
    resource_float_samples = [[] for _ in topology['resources']]
    for index, (permutation, step) in enumerate(zip(raw['permutations'], raw['steps'])):
        if sorted(permutation) != list(range(n)) or step['alpha_scaled'] <= 0:
            raise ValueError('invalid complete auxiliary stage')
        flows = []
        for src, dst in enumerate(permutation):
            pair = src*n+dst
            if not remaining[pair]:
                continue
            actual = min(remaining[pair], Fraction(step['alpha_scaled'])/weights[pair])
            if actual <= 0:
                raise ValueError('nonpositive projection progress')
            remaining[pair] -= actual
            delivered[pair] += actual
            flows.append((src, dst, actual))
        if not flows:
            empty_indices.append(index)
            continue
        cost = stage_cost(flows, topology)
        for resource, amount in enumerate(cost.pop('resource_bytes')):
            resource_float_samples[resource].append(float(amount))
        cost.update(auxiliary_index=index, coefficient_ticks=step['alpha_scaled'],
                    flow_count=len(flows), real_bytes=sum((f[2] for f in flows), Fraction(0)))
        if capture_flows:
            cost['flows'] = [dict(src=src, dst=dst, bytes=amount) for src, dst, amount in flows]
        stages.append(cost)
    if any(remaining) or delivered != list(map(Fraction, demand)):
        raise ValueError('exact per-pair conservation failed')
    # Regroup exact emitted bytes by pair and then route. Each pair has a fixed
    # denominator; this avoids a gigantic transient LCM across thousands of pairs.
    resource_totals = demand_resource_totals(delivered, topology)
    expected = demand_resource_totals(demand, topology)
    if resource_totals != expected:
        raise ValueError('exact fixed-route resource conservation failed')
    for samples, exact in zip(resource_float_samples, resource_totals):
        if not math.isclose(math.fsum(samples), float(exact), rel_tol=1e-12, abs_tol=1e-5):
            raise ValueError('stage/resource floating-point cross-check failed')
    return dict(K_aux=raw['stage_count'], K_real=len(stages), zero_real_stages=len(empty_indices),
                zero_real_auxiliary_indices=empty_indices, max_pair_error_bytes=0,
                communication_us=math.fsum(s['total_us'] for s in stages),
                serialization_sum_us=math.fsum(s['serial_us'] for s in stages),
                propagation_sum_us=math.fsum(s['alpha_us'] for s in stages),
                delivered_bytes=sum(delivered, Fraction(0)), resource_total_bytes=resource_totals,
                exact_resource_grouping='actual emitted bytes by pair, then fixed resource path',
                stages=stages)


def native_adapter_parity(demand, topology, projected, directory):
    """Bytes ticks are 8 true bytes, hence the existing adapter uses unit_bytes=8."""
    n = topology['n']
    tokens = ['BVN_CASE_V1', 'N', str(n), 'DEMAND']+list(map(str, demand))
    tokens += ['RESOURCES', str(len(topology['resources']))]
    for resource in topology['resources']:
        tokens += [str(resource['id']), resource['name'], str(resource['bandwidth_gbps'])]
    tokens += ['PATHS', str(n*(n-1))]
    for path in topology['paths']:
        if path['src'] == path['dst']:
            continue
        tokens += [str(path['src']), str(path['dst']), str(float(path['propagation_us'])),
                   str(float(path['rtt_us'])), str(len(path['resource_ids']))]
        tokens += list(map(str, path['resource_ids']))
    input_path = directory/'native-adapter-parity.case'
    input_path.write_text(' '.join(tokens)+'\n')
    result = subprocess.run([str(NATIVE_BIN), '--input', str(input_path), '--method', 'fast-native',
                             '--unit-bytes', '8'], capture_output=True, text=True, check=True, timeout=180)
    native = json.loads(result.stdout)
    save_json(directory/'native-adapter-parity.json', native)
    if native['decomposition_steps'] != projected['K_aux'] or len(native['stages']) != projected['K_real']:
        raise ValueError('native adapter stage parity failed')
    for actual, expected in zip(native['stages'], projected['stages']):
        if Fraction(str(actual['coefficient'])) != 8*expected['coefficient_ticks']:
            raise ValueError('native adapter coefficient parity failed')
        native_flows = [(f['src'], f['dst'], Fraction(str(f['bytes']))) for f in actual['flows']]
        expected_flows = [(f['src'], f['dst'], f['bytes']) for f in expected['flows']]
        if native_flows != expected_flows:
            raise ValueError('native adapter real-byte projection parity failed')
    if not math.isclose(native['communication_us'], projected['communication_us'], rel_tol=1e-12):
        raise ValueError('native adapter evaluator parity failed')
    return dict(ok=True, method='fast-native', native_unit_bytes=8,
                coefficient_unit='true bytes', stages=projected['K_real'])


def source_hashes():
    files = ['simulation/algorithm/all2all.cpp', 'simulation/algorithm/all2all.h',
             'simulation/algorithm/matrix.cpp', 'simulation/algorithm/bvn_schedule.cpp',
             'simulation/bvn_decompose_main.cpp', 'simulation/topology/model.cpp',
             'ds/decomposition/study.py', 'ds/decomposition/static_study.py']
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=pathlib.Path,
                        default=ROOT/'ds/decomposition/static-results-20261111')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--sizes', type=int, nargs='+', choices=(16, 32, 64), default=[16, 32, 64])
    args = parser.parse_args()
    if not 1 <= args.repeat <= 100 or len(set(args.sizes)) != len(args.sizes):
        parser.error('repeat in [1,100] and unique sizes required')
    args.out.mkdir(parents=True, exist_ok=False)
    for name in ('cases', 'topologies', 'auxiliary', 'raw', 'projected'):
        (args.out/name).mkdir()
    metadata = dict(schema_version=1, complete=False, strategy=0, native_scaling='off',
                    repeat=args.repeat, sizes=args.sizes, seed=101, workload_kinds=KINDS,
                    spaces=SPACES, topologies=TOPOLOGIES, generated_moe_trace=False,
                    payload='true input integer bytes; fluid fractional-byte stage projection',
                    evaluator='static stages, no Local balance/dispatch or FAST pipeline',
                    source_sha256=source_hashes(),
                    revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    git_status=subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
                    compiler=subprocess.check_output(['g++', '--version'], text=True).splitlines()[0],
                    binary_sha256=hashlib.sha256(BIN.read_bytes()).hexdigest(),
                    native_adapter_binary_sha256=hashlib.sha256(NATIVE_BIN.read_bytes()).hexdigest(),
                    mapping=dict(rounding='none; exact rational ticks, refuse nonintegral values',
                                 bytes_ticks_per_kib=128, bytes_per_tick=8,
                                 time_tick_us=Fraction(1, 6250),
                                 reference_serialization_us_per_kib=Fraction('0.02048'),
                                 delay_alpha=1, weight='fixed original Z_ij / original true D_ij bytes'),
                    timing=dict(mapping_cpu='Python process_time; exact Z and weights only',
                                cli_cpu='native std::clock decomposition samples, excluding padding',
                                subprocess_wall='launch/CLI/stdio; not native CPU',
                                projection_evaluation_cpu='Python process_time; restoration, costs, exact audits',
                                decomposition_audit_cpu='Python process_time; study.audit_decomposition only'))
    save_json(args.out/'run_metadata.json', metadata)
    rows = []
    parity_done = False
    for n in args.sizes:
        topologies = {name: fixed_topology(n, name) for name in TOPOLOGIES}
        for name, topology in topologies.items():
            save_json(args.out/'topologies'/f'n{n}-{name}.json', topology)
        for kind in KINDS:
            kib = study.fixed_load_matrix(n, 1, kind, 'matrix')
            demand = [x*1024 for x in kib]
            case = dict(n=n, kind=kind, seed=101, units='bytes', demand_bytes=demand,
                        input_kib=kib, per_source_kib=[sum(kib[s*n:s*n+n]) for s in range(n)],
                        measured_trace=False)
            save_json(args.out/'cases'/f'n{n}-{kind}.json', case)
            for topology_name, topology in topologies.items():
                expected_resources = demand_resource_totals(demand, topology)
                paired = {}
                for space in SPACES:
                    key = f'n{n}-{kind}-{topology_name}-{space}'
                    started = time.process_time_ns()
                    auxiliary, weights = map_auxiliary(demand, topology, space)
                    mapping_cpu_us = (time.process_time_ns()-started)/1000
                    (args.out/'auxiliary'/(key+'.input')).write_text(' '.join(map(str, auxiliary))+'\n')
                    save_json(args.out/'auxiliary'/(key+'.weights.json'),
                              dict(units='auxiliary ticks / true byte', fixed=True, weights=weights))
                    raw, wall_us = run_decomposition(auxiliary, n, args.repeat)
                    save_json(args.out/'raw'/(key+'.json'), raw)
                    started = time.process_time_ns()
                    study.audit_decomposition(auxiliary, n, raw)
                    audit_cpu_us = (time.process_time_ns()-started)/1000
                    capture = not parity_done and topology_name == 'T0' and space == 'bytes'
                    started = time.process_time_ns()
                    projected = project_and_evaluate(demand, auxiliary, topology, raw, capture)
                    projection_cpu_us = (time.process_time_ns()-started)/1000
                    if projected['resource_total_bytes'] != expected_resources:
                        raise ValueError('variant resource totals differ from original true demand')
                    if capture:
                        metadata['native_adapter_parity'] = native_adapter_parity(demand, topology, projected, args.out)
                        parity_done = True
                    save_json(args.out/'projected'/(key+'.json'), projected)
                    paired[space] = (auxiliary, projected)
                    row = dict(case=key, n=n, kind=kind, topology=topology_name, space=space,
                               strategy=0, delay_alpha=1 if space == 'delay' else 0,
                               K_aux=projected['K_aux'], K_real=projected['K_real'],
                               zero_real_stages=projected['zero_real_stages'],
                               communication_us=projected['communication_us'],
                               serialization_sum_us=projected['serialization_sum_us'],
                               propagation_sum_us=projected['propagation_sum_us'],
                               mapping_cpu_us=mapping_cpu_us,
                               native_decomposition_cpu_samples_us=raw['cpu_samples_us'],
                               native_decomposition_cpu_median_us=statistics.median(raw['cpu_samples_us']),
                               native_padding_wall_samples_us=raw['padding_wall_samples_us'],
                               subprocess_wall_us=wall_us, decomposition_audit_cpu_us=audit_cpu_us,
                               projection_evaluation_cpu_us=projection_cpu_us,
                               max_pair_error_bytes=0, exact_resource_totals_verified=True)
                    rows.append(row)
                    print(key, 'Kaux', row['K_aux'], 'Kreal', row['K_real'],
                          'static_us', round(row['communication_us'], 6),
                          'native_cpu_us', row['native_decomposition_cpu_median_us'], flush=True)
                if topology_name == 'T0':
                    if paired['bytes'][0] != paired['time'][0]:
                        raise ValueError('homogeneous serialization constant-weight invariance failed')
                    for field in ('K_aux', 'K_real', 'communication_us', 'resource_total_bytes'):
                        if paired['bytes'][1][field] != paired['time'][1][field]:
                            raise ValueError('homogeneous Bytes/Time output invariance failed')
    for row in rows:
        baseline = next(r for r in rows if r['n'] == row['n'] and r['kind'] == row['kind'] and
                        r['topology'] == row['topology'] and r['space'] == 'bytes')
        row['static_speedup_vs_bytes'] = baseline['communication_us']/row['communication_us']
    save_json(args.out/'summary.json', rows)
    metadata['source_sha256_after'] = source_hashes()
    metadata['binary_sha256_after'] = hashlib.sha256(BIN.read_bytes()).hexdigest()
    if metadata['source_sha256_after'] != metadata['source_sha256'] or metadata['binary_sha256_after'] != metadata['binary_sha256']:
        raise RuntimeError('source/binary changed during paired experiment; do not treat as frozen results')
    metadata.update(complete=True, outputs=len(rows), homogeneous_constant_weight_cases=len(args.sizes)*len(KINDS),
                    exact_conservation_cases=len(rows), strategy_held_fixed=True)
    save_json(args.out/'run_metadata.json', metadata)


class StaticStudyTests(unittest.TestCase):
    def test_exact_ticks_and_zero_demand(self):
        demand = [0, 2048, 0, 0, 0, 3072, 1024, 0, 0]
        topology = fixed_topology(3, 'T1')
        mapped = {space: map_auxiliary(demand, topology, space)[0] for space in SPACES}
        self.assertEqual(mapped['bytes'], [0, 256, 0, 0, 0, 384, 128, 0, 0])
        self.assertEqual(mapped['time'][1], 1024)
        self.assertEqual(mapped['delay'][1], 331024)
        self.assertEqual(mapped['delay'][5], 331536)
        self.assertEqual(map_auxiliary(demand, topology, 'delay', delay_alpha=0)[0], mapped['time'])
        topology['paths'][1]['propagation_us'] = Fraction(1, 7)
        with self.assertRaises(ValueError):
            map_auxiliary(demand, topology, 'delay')

    def test_shared_stage_cost(self):
        topology = fixed_topology(4, 'T0')
        topology['resources'] = [{'id': 0, 'name': 'shared', 'bandwidth_gbps': 100}]
        for src in range(4):
            for dst in range(4):
                if src != dst:
                    topology['paths'][src*4+dst]['resource_ids'] = [0]
                    topology['paths'][src*4+dst]['propagation_us'] = Fraction(5)
        result = stage_cost([(0, 1, Fraction(1000)), (2, 3, Fraction(1000))], topology)
        self.assertAlmostEqual(result['total_us'], 5.16)
        self.assertEqual(result['resource_bytes'], [2000])

    def test_homogeneous_constant_weight_and_delay_alpha_zero(self):
        n = 4
        demand = [x*1024 for x in study.fixed_load_matrix(n, 1, 'zipf', 'matrix')]
        topology = fixed_topology(n, 'T0')
        values, weights = map_auxiliary(demand, topology, 'bytes')
        time_values, time_weights = map_auxiliary(demand, topology, 'time')
        self.assertEqual(values, time_values)
        self.assertEqual(weights, time_weights)
        delay_zero, _ = map_auxiliary(demand, topology, 'delay', delay_alpha=0)
        self.assertEqual(delay_zero, time_values)
        raw, _ = run_decomposition(values, n, 1)
        study.audit_decomposition(values, n, raw)
        projected = project_and_evaluate(demand, values, topology, raw, capture_flows=True)
        self.assertEqual(projected['max_pair_error_bytes'], 0)
        self.assertEqual(projected['resource_total_bytes'], demand_resource_totals(demand, topology))
        self.assertTrue(projected['stages'])

    def test_heterogeneous_fixed_weights_exact_conservation(self):
        n = 4
        demand = [x*1024 for x in study.fixed_load_matrix(n, 1, 'zipf', 'matrix')]
        topology = fixed_topology(n, 'T2')
        expected = demand_resource_totals(demand, topology)
        for space in SPACES:
            values, _ = map_auxiliary(demand, topology, space)
            raw, _ = run_decomposition(values, n, 1)
            study.audit_decomposition(values, n, raw)
            projected = project_and_evaluate(demand, values, topology, raw)
            self.assertEqual(projected['resource_total_bytes'], expected)
            self.assertEqual(projected['max_pair_error_bytes'], 0)
            self.assertTrue(math.isfinite(projected['communication_us']))
            self.assertEqual(projected['K_aux'], projected['K_real']+projected['zero_real_stages'])


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    else:
        main()
