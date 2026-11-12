#!/usr/bin/env python3
"""Offline residual diagnostics. Exhaustive actions/DP NEVER enter FAST online code."""
import argparse
import datetime
import functools
import hashlib
import itertools
import json
import math
import pathlib
import time

import baseline_control as b
import study

SCORES = ('stage_only_score_us', 'capacity_score_us', 'endpoint_score_us', 'wan_l2_score_us')


def consume(pending, permutation, capacity, n, g):
    if (len(pending) != n*n*g or sorted(permutation) != list(range(n)) or
            capacity <= 0 or any(not isinstance(v, int) or v < 0 for v in pending)):
        raise ValueError('invalid pending, permutation or capacity')
    result = pending[:]
    for s, d in enumerate(permutation):
        if s != d:
            for u in range(g):
                k = (s*n+d)*g+u
                result[k] -= min(capacity, result[k])
    return result


def initial_pending(raw):
    top = raw['topology_snapshot']; n, g = top['n'], top['g']
    result = [0]*(n*n*g)
    for p, payload in zip(raw['permutations'], raw['payload_units']):
        for s, d in enumerate(p):
            if s != d:
                for u in range(g):
                    result[(s*n+d)*g+u] += payload[s*g+u]
    return result


def remaining_features(raw, pending, unit):
    top = raw['topology_snapshot']; n, g = top['n'], top['g']
    if len(pending) != n*n*g or any(v < 0 for v in pending) or unit <= 0:
        raise ValueError('invalid true pending or byte unit')
    caps = dict(zip(top['resource_names'], top['bandwidth_gbps']))
    loads = {}; rows = [0.]*n; cols = [0.]*n
    for s in range(n):
        for d in range(n):
            amount = sum(pending[(s*n+d)*g:(s*n+d+1)*g])*unit
            if s == d or not amount:
                continue
            path = top['server_paths'][s*n+d]
            route = [top['resource_names'][e] for e in path['resource_ids']]
            if not route:
                raise ValueError('active real demand has no network route')
            for name in route:
                loads[name] = loads.get(name, 0)+amount
            # One propagation payment per still-active server pair is a lower
            # bound even if future decomposition splits that pair across stages.
            solo = path['propagation_us']+amount*.008/min(caps[e] for e in route)
            rows[s] += solo; cols[d] += solo
    work = {name: amount*.008/caps[name] for name, amount in loads.items()}
    capacity = max(work.values(), default=0.)
    endpoint = max(rows+cols, default=0.)
    wan = [v for name, v in work.items() if name.startswith('wan:')]
    return dict(capacity_lb_us=capacity, endpoint_lb_us=endpoint,
                combined_lb_us=max(capacity, endpoint),
                wan_l2_us=math.sqrt(sum(v*v for v in wan)), resource_work_us=work)


def action_features(raw, pending, permutation, capacity, unit, previous_us=0.):
    top = raw['topology_snapshot']; n, g = top['n'], top['g']
    after = consume(pending, permutation, capacity, n, g)
    flows = [[s*g+u, d*g+u, pending[(s*n+d)*g+u]-after[(s*n+d)*g+u]]
             for s, d in enumerate(permutation) if s != d for u in range(g)]
    _, delay, serial = b.resource_cost(flows, n, g, unit, top)
    current = max(previous_us, delay+serial)
    future = remaining_features(raw, after, unit)
    return dict(stage_us=delay+serial, current_pipeline_term_us=current,
                stage_only_score_us=current,
                capacity_score_us=current+future['capacity_lb_us'],
                endpoint_score_us=current+future['combined_lb_us'],
                wan_l2_score_us=current+future['wan_l2_us'], remaining=future)


class UniformOracle:
    """Exact *network* cost-to-go for uniform positive edges, n<=6 only.

    Every positive auxiliary edge occurs once, has the same coefficient and
    carries the same true payload on every GPU channel. Entirely virtual edges,
    diagonal support and unequal payload are rejected. Scaling may round a
    capacity upward, but cannot change that fixed real payload.
    """
    def __init__(self, raw, unit):
        self.raw, self.unit = raw, unit
        top = raw['topology_snapshot']; self.n, self.g = top['n'], top['g']
        n, g = self.n, self.g
        if not 1 < n <= 6 or unit <= 0:
            raise ValueError('offline oracle requires 2<=n<=6 and positive units')
        ps, steps, payloads = raw['permutations'], raw['steps'], raw['payload_units']
        if not steps or not len(ps) == len(steps) == len(payloads):
            raise ValueError('incomplete uniform trace')
        capacity, coefficient = steps[0]['alpha'], steps[0]['alpha_scaled']
        if coefficient <= 0 or capacity < coefficient or capacity % coefficient:
            raise ValueError('invalid uniform scaling')
        auxiliary = [0]*(n*n)
        for p, step, payload in zip(ps, steps, payloads):
            if (sorted(p) != list(range(n)) or len(payload) != n*g or
                    (step['alpha'], step['alpha_scaled']) != (capacity, coefficient)):
                raise ValueError('nonuniform stage or malformed permutation')
            for s, d in enumerate(p):
                auxiliary[s*n+d] += coefficient
        pending = initial_pending(raw)
        positives = {v for v in pending if v}
        if len(positives) != 1 or not 0 < next(iter(positives)) <= capacity:
            raise ValueError('nonuniform true GPU payload')
        self.payload = next(iter(positives)); self.pending = pending
        for s in range(n):
            for d in range(n):
                a = auxiliary[s*n+d]; block = pending[(s*n+d)*g:(s*n+d+1)*g]
                if a and (s == d or a != coefficient or block != [self.payload]*g):
                    raise ValueError('dummy, repeated or nonuniform support edge')
                if not a and any(block):
                    raise ValueError('real demand absent from auxiliary support')
        degrees = [sum(auxiliary[s*n+d] > 0 for d in range(n)) for s in range(n)]
        degrees += [sum(auxiliary[s*n+d] > 0 for s in range(n)) for d in range(n)]
        if len(set(degrees)) != 1 or degrees[0] != len(steps):
            raise ValueError('support is not regular')
        self.full_mask = sum(1 << i for i, value in enumerate(auxiliary) if value)
        self.options = []
        for p in itertools.permutations(range(n)):
            mask = self.mask_of(p)
            if mask & self.full_mask != mask:
                continue
            flows = [[s*g+u, d*g+u, self.payload] for s, d in enumerate(p) for u in range(g)]
            _, a, serial = b.resource_cost(flows, n, g, unit, top)
            self.options.append((mask, a+serial, list(p)))
        self.costs = {mask: cost for mask, cost, _ in self.options}
        self.by_edge = [[v for v in self.options if v[0] & (1 << i)] for i in range(n*n)]
        self.value = functools.lru_cache(None)(self._value)

    def mask_of(self, p):
        if sorted(p) != list(range(self.n)):
            raise ValueError('not a perfect permutation')
        return sum(1 << (s*self.n+d) for s, d in enumerate(p))

    def candidates(self, mask):
        return [v for v in self.options if mask & v[0] == v[0]]

    def stage_cost(self, matching_mask):
        return self.costs[matching_mask]

    def _value(self, mask):
        if not mask:
            return 0.
        edge = (mask & -mask).bit_length()-1
        # Any full factorization contains this edge in one of its matchings.
        # With additive fixed edge costs we can put that matching first.
        values = [cost+self.value(mask ^ m) for m, cost, _ in self.by_edge[edge]
                  if mask & m == m]
        if not values:
            raise ValueError('residual lacks complete factorization')
        return min(values)

    def plan(self, mask):
        result = []
        while mask:
            best = min(self.candidates(mask), key=lambda v: (v[1]+self.value(mask ^ v[0]), v[2]))
            result.append(best[2]); mask ^= best[0]
        return result

    def pending_at(self, mask):
        return [self.payload if mask & (1 << (s*self.n+d)) else 0
                for s in range(self.n) for d in range(self.n) for _ in range(self.g)]


def exchange_neighborhood(oracle, mask, permutation):
    """Inspect 2/3-row alternating cycles, offline; never change a saved plan."""
    current = oracle.stage_cost(oracle.mask_of(permutation)); result = {}
    for size in (2, 3):
        legal = []
        for rows in itertools.combinations(range(oracle.n), size):
            for shift in range(1, size):
                trial = permutation[:]
                for i, s in enumerate(rows):
                    trial[s] = permutation[rows[(i+shift) % size]]
                m = oracle.mask_of(trial)
                if mask & m == m:
                    legal.append((oracle.stage_cost(m), trial))
        best = min(legal, default=(None, None))
        result[str(size)] = dict(legal=len(legal),
            improving=sum(cost < current-1e-8 for cost, _ in legal),
            best_us=best[0], best_matching=best[1])
    return result


def inspect_interventions(root, read):
    rows = []
    for event in read(root/'causal-certified-20261112/summary.json'):
        name, k = event['case'], event['step_zero_based']
        raw = read(root/'causal-certified-20261112'/f'{name}-baseline.json')['raw']
        top = raw['topology_snapshot']
        data = read(root/'causal-certified-20261112'/f'{name}.input.json')
        b.audit(raw, data, top['n'], top['g'], 1024)
        assert raw['permutations'][k] == event['chosen']
        assert raw['steps'][k]['resource_seed'] == event['seed']
        assert raw['steps'][k]['alpha'] == event['q']
        pending = initial_pending(raw)
        for p, step in zip(raw['permutations'][:k], raw['steps'][:k]):
            pending = consume(pending, p, step['alpha'], top['n'], top['g'])
        previous = raw['dispatch_us'][k-1] if k else raw['intrinsic_us']
        seed = action_features(raw, pending, event['seed'], event['q'], 1024, previous)
        chosen = action_features(raw, pending, event['chosen'], event['q'], 1024, previous)
        delta = {score: chosen[score]-seed[score] for score in SCORES}
        # Ground truth here is the *same resumed heuristic*, not an optimal tail.
        rows.append(dict(event=event, seed=seed, chosen=chosen, chosen_minus_seed=delta,
                         measured_chosen_minus_seed_us=-event['counter_minus_base_us']))
    return rows


def inspect_uniform(raw, unit, case, label):
    oracle = UniformOracle(raw, unit); mask = oracle.full_mask
    rows = []
    for k, p in enumerate(raw['permutations']):
        options = oracle.candidates(mask); pending = oracle.pending_at(mask)
        values = []
        optimum = oracle.value(mask)
        for m, cost, permutation in options:
            scores = action_features(raw, pending, permutation, raw['steps'][k]['alpha'], unit)
            tail = oracle.value(mask ^ m); q = cost+tail
            if scores['remaining']['combined_lb_us'] > tail+1e-8:
                raise AssertionError('a claimed residual lower bound exceeds the exact tail')
            values.append(dict(permutation=permutation, mask=m, exact_q_us=q, **scores))
            if not math.isclose(cost, scores['stage_us'], abs_tol=1e-8):
                raise AssertionError('fixed-payload evaluator disagrees')
        actual = oracle.mask_of(p)
        picked = next(v for v in values if v['mask'] == actual)
        rankings = {}
        for score in SCORES:
            minimum = min(v[score] for v in values)
            ties = [v for v in values if math.isclose(v[score], minimum, abs_tol=1e-8, rel_tol=1e-12)]
            rankings[score] = dict(ties=len(ties),
                best_irreversible_regret_us=min(v['exact_q_us'] for v in ties)-optimum,
                worst_irreversible_regret_us=max(v['exact_q_us'] for v in ties)-optimum)
        rows.append(dict(case=case, label=label, index=k, matching=p,
            stage_us=oracle.stage_cost(actual), exact_value_before_us=optimum,
            actual_action_q_us=picked['exact_q_us'],
            irreversible_regret_us=max(0., picked['exact_q_us']-optimum),
            policy_gap_us=sum(raw['network_stages_us'][k:])-optimum,
            legal_actions=len(options), score_rankings=rankings,
            minimum_stage_us=min(v['stage_us'] for v in values),
            exchanges=exchange_neighborhood(oracle, mask, p)))
        mask ^= actual
    assert not mask
    record = dict(case=case, label=label, unit=unit,
        actual_network_us=sum(raw['network_stages_us']), optimal_network_us=oracle.value(oracle.full_mask),
        optimal_plan=oracle.plan(oracle.full_mask),
        dp_states=oracle.value.cache_info().currsize,
        pipeline_extra_observed_us=raw['total_us']-sum(raw['network_stages_us']),
        equal_observed_dispatch=len(set(raw['dispatch_us'])) == 1)
    return rows, record, oracle


def rollouts(oracle):
    result = []
    for score in SCORES:
        mask = oracle.full_mask; network = 0.; plan = []
        while mask:
            pending = oracle.pending_at(mask)
            values = [(action_features(oracle.raw, pending, p, oracle.raw['steps'][0]['alpha'], oracle.unit)[score],
                       tuple(p), m, cost) for m, cost, p in oracle.candidates(mask)]
            minimum = min(v[0] for v in values)
            # Fixed deterministic lexicographic tie rule, never choose by exact tail.
            _, p, m, cost = min((v for v in values if math.isclose(v[0], minimum, abs_tol=1e-8, rel_tol=1e-12)),
                                key=lambda v: v[1])
            network += cost; plan.append(list(p)); mask ^= m
        result.append(dict(score=score, network_us=network, plan=plan))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=pathlib.Path, required=True)
    args = parser.parse_args(); args.out.mkdir(parents=True, exist_ok=False)
    root = study.ROOT/'ds/decomposition'; hashes = {}
    def read(path):
        data = path.read_bytes(); hashes[str(path.relative_to(study.ROOT))] = hashlib.sha256(data).hexdigest()
        return json.loads(data)
    sources = ['ds/decomposition/residual_opportunity.py', 'ds/decomposition/baseline_control.py',
               'ds/decomposition/study.py',
               'simulation/tests/test_residual_opportunity.py']
    metadata = dict(complete=False, scope='offline development diagnostics; no online solver or holdout claims',
        started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        source_sha256={s: hashlib.sha256((study.ROOT/s).read_bytes()).hexdigest() for s in sources},
        binary_sha256=hashlib.sha256(study.BIN.read_bytes()).hexdigest())
    b.save(args.out/'metadata.json', metadata); started = time.monotonic()
    interventions = inspect_interventions(root, read)
    b.save(args.out/'interventions.json', interventions)
    allrows = []; cases = []; greedy = []
    for profile, unit, label in itertools.product(range(3), (16, 1024), ('native', 'bottleneck', 'resource')):
        name = f'balanced-p101-top{profile}-unit{unit}'
        raw = read(root/'multidc-certified-20261112'/f'{name}-{label}.json')['raw']
        data = read(root/'multidc-certified-20261112'/f'balanced-p101-top{profile}.input.json')
        b.audit(raw, data, 6, 4, unit)
        rows, case, oracle = inspect_uniform(raw, unit, name, label)
        allrows += rows; cases.append(case)
        if label == 'resource':
            greedy += [dict(case=name, **v) for v in rollouts(oracle)]
        print(name, label, 'actual/optimal network', round(case['actual_network_us'], 6),
              round(case['optimal_network_us'], 6), flush=True)
    b.save(args.out/'uniform_prefixes.json', allrows)
    b.save(args.out/'uniform_cases.json', cases)
    b.save(args.out/'score_rollouts.json', greedy)
    for s, h in {**hashes, **metadata['source_sha256']}.items():
        assert hashlib.sha256((study.ROOT/s).read_bytes()).hexdigest() == h, s
    assert hashlib.sha256(study.BIN.read_bytes()).hexdigest() == metadata['binary_sha256']
    metadata.update(complete=True, input_sha256=hashes, interventions=len(interventions),
                    uniform_traces=len(cases), uniform_prefixes=len(allrows),
                    elapsed_s=time.monotonic()-started)
    b.save(args.out/'metadata.json', metadata)


if __name__ == '__main__':
    main()
