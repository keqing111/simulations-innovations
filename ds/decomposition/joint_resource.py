#!/usr/bin/env python3
"""Development prototype: joint coefficient/resource matching, never an oracle."""
import argparse
import hashlib
import json
import math
import pathlib
import subprocess
import time

import baseline_control as audit
import residual_opportunity as residual
import study

OBJECTIVES = ('stage', 'capacity', 'degree')


def assignment(costs):
    """O(n^3) primal-dual Hungarian; None denotes a forbidden edge."""
    n = len(costs)
    if any(len(row) != n for row in costs):
        raise ValueError('square assignment required')
    if any(v is not None and not math.isfinite(v) for row in costs for v in row):
        raise ValueError('finite assignment costs required')
    magnitude = max((abs(v) for row in costs for v in row if v is not None), default=0.)
    if magnitude > 1e100:
        scaled = [[None if v is None else v/magnitude for v in row] for row in costs]
        if any(v and scaled[i][j] == 0 for i, row in enumerate(costs)
               for j, v in enumerate(row) if v is not None):
            raise ValueError('assignment cost dynamic range is not representable')
        costs = scaled
    u, v, p, way = [0.]*(n+1), [0.]*(n+1), [0]*(n+1), [0]*(n+1)
    for i in range(1, n+1):
        p[0] = i; j0 = 0; low = [math.inf]*(n+1); used = [False]*(n+1)
        while True:
            used[j0] = True; i0 = p[j0]; delta = math.inf; j1 = 0
            for j in range(1, n+1):
                if used[j]:
                    continue
                c = costs[i0-1][j-1]
                if c is not None and c-u[i0]-v[j] < low[j]:
                    low[j] = c-u[i0]-v[j]; way[j] = j0
                if low[j] < delta:
                    delta, j1 = low[j], j
            if not math.isfinite(delta):
                return None
            for j in range(n+1):
                if used[j]:
                    u[p[j]] += delta; v[j] -= delta
                else:
                    low[j] -= delta
            j0 = j1
            if not p[j0]:
                break
        while j0:
            j1 = way[j0]; p[j0] = p[j1]; j0 = j1
    result = [0]*n
    for j in range(1, n+1):
        result[p[j]-1] = j-1
    return result


def active(values):
    peak = max(values, default=0.)
    ids = [i for i, value in enumerate(values)
           if math.isclose(value, peak, rel_tol=1e-12, abs_tol=1e-12)]
    return [1/len(ids) if i in ids else 0. for i in range(len(values))]


class State:
    """Auxiliary units R and true post-balance GPU units U stay separate."""
    def __init__(self, raw, unit):
        self.n, self.g, self.unit = raw['n'], raw['g'], unit
        self.top = raw['topology_snapshot']; n = self.n
        if (unit <= 0 or not math.isfinite(unit) or self.top['n'] != n or
                self.top['g'] != self.g):
            raise ValueError('inconsistent topology or data units')
        self.R = [0]*(n*n); factors = set()
        if not len(raw['permutations']) == len(raw['steps']) == len(raw['payload_units']):
            raise ValueError('incomplete reference trace')
        for p, step in zip(raw['permutations'], raw['steps']):
            q, cap = step['alpha_scaled'], step['alpha']
            if sorted(p) != list(range(n)) or q <= 0 or cap % q or cap < q:
                raise ValueError('invalid reference permutation/coefficient')
            factors.add(cap//q)
            for s, d in enumerate(p):
                self.R[s*n+d] += q
        if len(factors) > 1:
            raise ValueError('scaling changed within an operation')
        self.kappa = next(iter(factors), 1)
        self.U = residual.initial_pending(raw)
        if (any(v < 0 or not isinstance(v, int) for v in self.U) or
                any(v > self.kappa*self.R[i//self.g] for i, v in enumerate(self.U))):
            raise ValueError('real data not covered by auxiliary capacity')
        sums = [sum(self.R[s*n:(s+1)*n]) for s in range(n)]
        sums += [sum(self.R[s*n+d] for s in range(n)) for d in range(n)]
        if len(set(sums)) != 1:
            raise ValueError('auxiliary row/column sums differ')
        self.counts = dict(assignments=0, evaluations=0, refinements=0)
        self._refresh()

    def _refresh(self):
        n, g, top = self.n, self.g, self.top
        self.work = [0.]*len(top['resource_names']); self.degrees = [0.]*(2*n)
        for s in range(n):
            for d in range(n):
                amount = sum(self.U[(s*n+d)*g:(s*n+d+1)*g])*self.unit
                if s == d or not amount:
                    continue
                path = top['server_paths'][s*n+d]
                if not path['resource_ids']:
                    raise ValueError('active real demand lacks a route')
                self.degrees[s] += path['propagation_us']
                self.degrees[n+d] += path['propagation_us']
                for e in path['resource_ids']:
                    self.work[e] += amount*.008/top['bandwidth_gbps'][e]
        self.L = max(self.work, default=0.); self.Q = max(self.degrees, default=0.)
        self._edges = {}

    def edge_data(self, q):
        if q not in self._edges:
            rows = []
            for s in range(self.n):
                for d in range(self.n):
                    block = self.U[(s*self.n+d)*self.g:(s*self.n+d+1)*self.g]
                    amount = sum(min(self.kappa*q, v) for v in block)*self.unit if s != d else 0
                    path = self.top['server_paths'][s*self.n+d]
                    work = [0.]*len(self.work)
                    if amount:
                        for e in path['resource_ids']:
                            work[e] = amount*.008/self.top['bandwidth_gbps'][e]
                    tau = path['propagation_us'] if amount else 0.
                    drop = tau if amount and max(block) <= self.kappa*q else 0.
                    rows.append((work, tau, amount, drop))
            self._edges[q] = rows
        return self._edges[q]

    def potential(self, objective):
        return 0. if objective == 'stage' else self.L+(self.Q if objective == 'degree' else 0.)

    def action(self, p):
        n = self.n
        if sorted(p) != list(range(n)):
            raise ValueError('not a perfect matching')
        q = min(self.R[s*n+d] for s, d in enumerate(p))
        if not q:
            raise ValueError('matching uses zero residual')
        self.counts['evaluations'] += 1
        child = object.__new__(State); child.__dict__ = self.__dict__.copy()
        child.R = self.R[:]
        for s, d in enumerate(p):
            child.R[s*n+d] -= q
        child.U = residual.consume(self.U, p, self.kappa*q, n, self.g); child._refresh()
        edges = self.edge_data(q); work = [0.]*len(self.work); tau = 0.; size = 0.
        for s, d in enumerate(p):
            w, a, amount, _ = edges[s*n+d]; tau = max(tau, a); size += amount
            work = [a+b for a, b in zip(work, w)]
        return dict(p=list(p), q=q, capacity=self.kappa*q, bytes=size, propagation_us=tau,
                    work=work, time_us=tau+max(work, default=0.), next=child)

    def after(self, action):
        return action['next']


def score(state, action, objective):
    excess = action['time_us']+action['next'].potential(objective)-state.potential(objective)
    if excess < -1e-8*max(1., action['time_us'], state.potential(objective)):
        raise ArithmeticError('potential consistency violated')
    return max(0., excess)/action['bytes'] if action['bytes'] else 0.


def key(state, action, objective):
    return (score(state, action, objective), -action['bytes'], action['time_us'],
            -action['q'], tuple(action['p']))


def cycle_trials(current, target):
    """Only cycles supplied by a weighted direction, not all row subsets."""
    inverse = {d: s for s, d in enumerate(current)}; seen = set()
    yield target
    for start in range(len(current)):
        if start in seen or current[start] == target[start]:
            continue
        rows = []; s = start
        while s not in seen:
            seen.add(s); rows.append(s); s = inverse[target[s]]
        p = current[:]
        for s in rows:
            p[s] = target[s]
        if p != target:
            yield p


def direction(state, action, objective, threshold):
    n = state.n; edges = state.edge_data(action['q']); child = action['next']
    stage_prices = active(action['work']); future_prices = active(child.work)
    vertex_prices = active(child.degrees)
    critical = [s*n+d for s, d in enumerate(action['p'])
                if edges[s*n+d][1] == action['propagation_us']]
    eta = score(state, action, objective); costs = []
    for s in range(n):
        row = []
        for d in range(n):
            i = s*n+d; work, tau, amount, drop = edges[i]
            c = sum(w*a for w, a in zip(work, stage_prices))
            if i in critical:
                c += tau/len(critical)
            if objective != 'stage':
                c -= sum(w*a for w, a in zip(work, future_prices))
            if objective == 'degree':
                c -= drop*(vertex_prices[s]+vertex_prices[n+d])
            row.append(c-eta*amount if state.R[i] >= threshold else None)
        costs.append(row)
    state.counts['assignments'] += 1
    return assignment(costs)


def propose(state, objective, budget=4):
    if objective not in OBJECTIVES or not 1 <= budget <= 4:
        raise ValueError('objective or threshold budget')
    if not any(state.R):
        return []
    n = state.n; values = sorted({v for v in state.R if v})
    def feasible(t):
        state.counts['assignments'] += 1
        return assignment([[0. if state.R[s*n+d] >= t else None for d in range(n)]
                           for s in range(n)])
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo+hi)//2
        if feasible(values[mid]) is None:
            hi = mid
        else:
            lo = mid+1
    if not lo:
        raise ValueError('residual has no perfect matching')
    values = values[:lo]
    thresholds = ([values[-1]] if budget == 1 else
                  sorted({values[round(i*(len(values)-1)/(budget-1))] for i in range(budget)}))
    actions = {}; prices = active(state.work)
    for threshold in thresholds:
        edges = state.edge_data(threshold)
        costs = [[(sum(w*p for w, p in zip(edges[s*n+d][0], prices))+
                   edges[s*n+d][1]/n) if state.R[s*n+d] >= threshold else None
                  for d in range(n)] for s in range(n)]
        state.counts['assignments'] += 1
        current = state.action(assignment(costs)); seen = set()
        while tuple(current['p']) not in seen:
            seen.add(tuple(current['p']))
            target = direction(state, current, objective, threshold)
            trials = [state.action(p) for p in cycle_trials(current['p'], target)]
            best = min(trials+[current], key=lambda a: key(state, a, objective))
            if key(state, best, objective) >= key(state, current, objective):
                break
            state.counts['refinements'] += 1; current = best
        actions[tuple(current['p'])] = current
    return list(actions.values())


def plan(raw, unit, objective, lookahead=False, budget=4):
    state = State(raw, unit); initial = state; trace = []; permutations = []; total = 0.
    started = time.process_time(); edge_bound = sum(v > 0 for v in state.R)
    while any(state.R):
        actions = propose(state, objective, budget)
        choice_cache = {}
        def choose(a):
            identity = tuple(a['p'])
            if identity in choice_cache:
                return choice_cache[identity]
            if not lookahead:
                return key(state, a, objective)
            future = propose(a['next'], objective, budget)
            continuation = min((b['time_us']+b['next'].potential(objective) for b in future), default=0.)
            numerator = a['time_us']+continuation-state.potential(objective)
            density = max(0., numerator)/a['bytes'] if a['bytes'] else 0.
            choice_cache[identity] = (density,)+key(state, a, objective)[1:]
            return choice_cache[identity]
        selected = min(actions, key=choose); value = choose(selected)
        trace.append(dict(q=selected['q'], capacity=selected['capacity'], bytes=selected['bytes'],
                          time_us=selected['time_us'], propagation_us=selected['propagation_us'],
                          excess_us=selected['time_us']+selected['next'].potential(objective)-state.potential(objective),
                          decision_score=value[0], candidate_count=len(actions)))
        permutations.append(selected['p']); total += selected['time_us']; state = selected['next']
        if len(trace) > edge_bound:
            raise ArithmeticError('decomposition exceeded positive-edge bound')
    if any(state.U):
        raise ArithmeticError('real payload was not completely delivered')
    return dict(permutations=permutations, trace=trace, network_us=total,
                delivered_bytes=sum(initial.U)*unit, kappa=initial.kappa,
                cpu_us=(time.process_time()-started)*1e6, counts=state.counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=pathlib.Path, required=True)
    parser.add_argument('--out', type=pathlib.Path, required=True)
    parser.add_argument('--lookahead', action='store_true')
    parser.add_argument('--panels', nargs='+', choices=('development', 'new-seed', 'legacy'),
                        default=['development'])
    args = parser.parse_args(); args.out.mkdir(parents=True, exist_ok=False)
    sources = [pathlib.Path(__file__)] + [study.ROOT/p for p in (
        'simulation/bvn_decompose_main.cpp', 'simulation/algorithm/all2all.cpp',
        'simulation/algorithm/all2all.h', 'simulation/scheduler/global.cpp',
        'simulation/scheduler/local.cpp', 'simulation/topology/model.cpp',
        'ds/decomposition/baseline_control.py', 'ds/decomposition/residual_opportunity.py',
        'ds/decomposition/study.py', 'simulation/tests/test_joint_resource.py')]
    metadata = dict(complete=False, scope='development prototype; not holdout or online benchmark',
        source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        binary_sha256=hashlib.sha256(study.BIN.read_bytes()).hexdigest(), threshold_budget=4,
        input_sha256={}, matching='weighted assignment plus directed alternating-cycle line search',
        tie_rule='score, negative real bytes, stage time, negative actual q, permutation',
        panels=args.panels, lookahead_max_n=6)
    audit.save(args.out/'metadata.json', metadata); rows = []
    for row in json.loads((args.input/'summary.json').read_text()):
        if row['label'] != 'native' or row['panel'] not in args.panels:
            continue
        raw = json.loads((args.input/(row['case']+'-native.json')).read_text())['raw']
        # Existing archived input is a controlled development input, not regenerated traffic.
        data = json.loads((args.input/(row['case'].rsplit('-unit', 1)[0]+'.input.json')).read_text())
        for path in (args.input/(row['case']+'-native.json'),
                     args.input/(row['case'].rsplit('-unit', 1)[0]+'.input.json')):
            metadata['input_sha256'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        command = [str(study.BIN), '--mode', 'pipeline', '--n', str(row['n']), '--g', str(raw['g']),
                   '--topology', '1', '--scaling', '1', '--repeat', '1', '--wan-profile', str(row['profile']),
                   '--unit-mb', str(row['unit']/1e6)]
        fresh = json.loads(subprocess.run(command, input=' '.join(map(str, data)), text=True,
                                         capture_output=True, check=True, timeout=60).stdout)
        for field in ('permutations', 'payload_units', 'network_stages_us', 'dispatch_us',
                      'balance_us', 'intrinsic_us', 'total_us'):
            if raw[field] != fresh[field]:
                raise ArithmeticError('archived native parity failed: '+field)
        methods = [(s, False, 4) for s in OBJECTIVES]
        methods += [(s, False, 1) for s in ('stage', 'capacity')]
        if args.lookahead and row['n'] <= 6:
            methods.append(('capacity', True, 4))
        for objective, lookahead, budget in methods:
            label = objective+('_lookahead' if lookahead else '_maxq' if budget == 1 else '')
            result = plan(raw, row['unit'], objective, lookahead, budget)
            name = row['case']+'-'+label; plan_path = args.out/(name+'.plan.txt')
            lines = [str(len(result['trace']))]
            lines += [' '.join(map(str, [step['capacity']]+p))
                      for step, p in zip(result['trace'], result['permutations'])]
            plan_path.write_text('\n'.join(lines)+'\n')
            command = [str(study.BIN), '--mode', 'pipeline', '--n', str(row['n']), '--g', str(raw['g']),
                       '--topology', '1', '--scaling', '1', '--repeat', '1', '--wan-profile', str(row['profile']),
                       '--unit-mb', str(row['unit']/1e6), '--replay-plan', str(plan_path)]
            replay = json.loads(subprocess.run(command, input=' '.join(map(str, data)), text=True,
                                              capture_output=True, check=True, timeout=60).stdout)
            metrics = audit.audit(replay, data, row['n'], raw['g'], row['unit'])
            if not math.isclose(metrics['network_sum_us'], result['network_us'], abs_tol=1e-8, rel_tol=1e-12):
                raise ArithmeticError('Python/C++ network models disagree')
            if metrics['resource_totals'] != row['resource_totals']:
                raise ArithmeticError('resource byte conservation violated')
            audit.save(args.out/(name+'.json'), dict(prototype=result, replay=replay))
            output = dict(case=row['case'], label=label, kind=row['kind'], profile=row['profile'],
                          panel=row['panel'], n=row['n'], unit=row['unit'], K=len(result['trace']), total_us=replay['total_us'],
                          prototype_cpu_us=result['cpu_us'], **metrics, **result['counts'])
            rows.append(output)
            print(name, 'K', output['K'], 'full_us', round(output['total_us'], 3), flush=True)
    audit.save(args.out/'summary.json', rows)
    assert metadata['binary_sha256'] == hashlib.sha256(study.BIN.read_bytes()).hexdigest()
    assert all(hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() == h
               for p, h in metadata['source_sha256'].items())
    assert all(hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() == h
               for p, h in metadata['input_sha256'].items())
    metadata.update(complete=True, outputs=len(rows)); audit.save(args.out/'metadata.json', metadata)


if __name__ == '__main__':
    main()
