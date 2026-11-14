"""Linear-time matching repair and nonuniform FAST service quotas.

No permutations are enumerated. Weighted rounds re-complete an auxiliary
matrix; the real demand is never rounded, scaled or replaced by padding.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from math import lcm
from numbers import Real
from time import perf_counter_ns

import numpy as np

from . import fast_port as native


@dataclass(frozen=True)
class PairProfile:
    server_dc_ids: tuple[int, ...]
    intra_dc_rtt_ns: int
    inter_dc_rtt_ns: int
    intra_dc_bandwidth_gbps: float
    inter_dc_bandwidth_gbps: float

    def __post_init__(self):
        if not self.server_dc_ids or any(
            isinstance(x, bool) or not isinstance(x, int) or x < 0
            for x in self.server_dc_ids
        ):
            raise ValueError("server_dc_ids must be nonnegative integers")
        for value in (self.intra_dc_rtt_ns, self.inter_dc_rtt_ns):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("pair RTT must be nonnegative integer nanoseconds")
        for value in (self.intra_dc_bandwidth_gbps, self.inter_dc_bandwidth_gbps):
            if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value) or value <= 0:
                raise ValueError("pair bandwidth must be finite and positive")
        object.__setattr__(self, '_service_cache', {})

    @classmethod
    def from_dict(cls, value):
        keys = set(cls.__dataclass_fields__)
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError("fast_options must provide exactly the five pair-profile fields")
        if not isinstance(value['server_dc_ids'], (list, tuple)):
            raise ValueError('server_dc_ids must be a sequence of nonnegative integers')
        return cls(**{**value, "server_dc_ids": tuple(value["server_dc_ids"])})

    def service(self, src, dst, gpus):
        wan = self.server_dc_ids[src] != self.server_dc_ids[dst]
        # Gbps is bits/ns. Fraction prevents a one-byte rounding deficit.
        if gpus not in self._service_cache:
            self._service_cache[gpus] = (
                (self.intra_dc_rtt_ns, Fraction(str(self.intra_dc_bandwidth_gbps))/(8*gpus)),
                (self.inter_dc_rtt_ns, Fraction(str(self.inter_dc_bandwidth_gbps))/(8*gpus)))
        return self._service_cache[gpus][wan]


def update_support(app, previous):
    """Update only changed edges, preserving native row/column iteration order."""
    current = app.SDS_mat > 0
    for src, dst in zip(*np.nonzero(current != previous)):
        col = int(dst) + app.dim
        if current[src, dst]:
            app.row_to_col[src].insert(col)
        else:
            app.row_to_col[src].erase(col)
    return current


def repair_matching(support, matching, targets):
    """Force the first real target edge by flipping one alternating cycle.

    In the contracted graph, i -> inverse[j] denotes a supported edge i-j.
    Force u-v and search from inverse[v] back to u. One BFS is O(n+E).
    Return a copy, leaving the caller's original matching available on failure.
    """
    result = list(matching)
    if any(targets[i, j] for i, j in enumerate(matching)):
        return result, False
    edge = next(((i, j) for i, row in enumerate(support)
                 for j in row if targets[i, j]), None)
    if edge is None:
        return result, False
    u, v = edge
    inverse = [0] * len(matching)
    for i, j in enumerate(matching):
        inverse[j] = i
    first = inverse[v]
    parents = {first: None}
    pending = deque([first])
    while pending and u not in parents:
        node = pending.popleft()
        for col in support[node]:
            nxt = inverse[col]
            if nxt not in parents:
                parents[nxt] = node
                pending.append(nxt)
    if u not in parents:
        return result, False
    node = u
    while parents[node] is not None:
        prev = parents[node]
        result[prev] = matching[node]
        node = prev
    result[u] = v
    return result, True


def weighted_quotas(remaining, matching, profile):
    """Return per-GPU integer quotas and a progress-guaranteeing horizon (ns)."""
    gpus = remaining.shape[1]
    completions, wan_completions = [], []
    for src, dst in enumerate(matching):
        amount = int(remaining[src, :, dst].max())
        if not amount:
            continue
        rtt, rate = profile.service(src, dst, gpus)
        finish = rtt + (amount * rate.denominator + rate.numerator - 1) // rate.numerator
        completions.append(finish)
        if profile.server_dc_ids[src] != profile.server_dc_ids[dst]:
            wan_completions.append(finish)
    if not completions:
        raise ValueError("weighted matching must include a real transfer")
    duration = min(wan_completions or completions)
    quotas = []
    for src, dst in enumerate(matching):
        rtt, rate = profile.service(src, dst, gpus)
        budget = rate.numerator * max(0, duration - rtt) // rate.denominator
        quotas.append(min(int(remaining[src, :, dst].max()), budget))
    return quotas, duration


def cost_ordered_support(support, remaining, profile):
    """Stable native-order ties, with priority for real excess service cost.

    w=Cref/Cij; priority=D_real*(w-1). Equal weights therefore leave *every*
    adjacency order unchanged, rather than switching to volume-greedy matching.
    Padding is present only to complete a permutation and receives zero cost.
    """
    reference = Fraction(str(max(profile.intra_dc_bandwidth_gbps,
                                 profile.inter_dc_bandwidth_gbps)))
    factors = [reference / Fraction(str(bw)) - 1 for bw in
               (profile.intra_dc_bandwidth_gbps, profile.inter_dc_bandwidth_gbps)]
    # A common positive scale preserves exact ordering while avoiding Fraction
    # allocation/division for every supported edge in every Birkhoff round.
    denominator = lcm(*(factor.denominator for factor in factors))
    factors = [factor.numerator * (denominator // factor.denominator) for factor in factors]
    demand = remaining.max(axis=1)
    def score(src, dst):
        cross = profile.server_dc_ids[src] != profile.server_dc_ids[dst]
        return int(demand[src, dst]) * factors[cross]
    return [sorted(row, key=lambda dst: -score(src, dst))
            for src, row in enumerate(support)]


def run_wan_full(mat, server_n, gpu_n, inter_info, intra_info, profile,
                 *, wan_priority, weighted, cost_priority=False):
    """Reuse native preprocessing, matching, balancing and dispatch bookkeeping."""
    if len(profile.server_dc_ids) != server_n:
        raise ValueError("server_dc_ids length must equal num_servers")
    locals_ = [native.LocalScheduler(mat[s*gpu_n:(s+1)*gpu_n], gpu_n,
                                    server_n, s, intra_info)
               for s in range(server_n)]
    gs = native.GlobalScheduler(server_n, gpu_n, locals_, "ETHER400", "H100",
                                False, 1e-6, inter_info, intra_info)
    bs = {}
    for src, local in enumerate(locals_):
        for dst in range(server_n):
            balance = np.zeros((gpu_n, gpu_n), dtype=np.int64)
            if src != dst and not gs.if_approx[src * server_n + dst]:
                local.server2server_balance(dst, balance)
            bs[src, dst] = balance
    remaining = np.stack([
        local.balanced_data.reshape(gpu_n, server_n, gpu_n).sum(axis=2)
        for local in locals_
    ])
    for src in range(server_n):
        remaining[src, :, src] = 0
    expected = remaining.sum(axis=1).copy()
    initial_pairs = int(np.count_nonzero(expected))
    dc = np.asarray(profile.server_dc_ids)
    cross = dc[:, None] != dc[None, :]
    app = native.FastAll2All(gs.mat, gpu_n, "ETHER400", "H100", 1e-6)
    app.to_scaled_doubly_stochastic_matrix()
    total = int(app.sds_info['max_sum'])
    extracted = 0
    p_sets, ds, rows, records = [], {}, {}, []
    timers = dict(matching_wall_ns=0, repair_wall_ns=0,
                  completion_wall_ns=0, quota_wall_ns=0)
    repairs = attempts = fallbacks = 0
    previous_support = np.zeros((server_n,server_n), dtype=bool)
    while np.any(remaining) if weighted else extracted < total:
        started = perf_counter_ns()
        if weighted:
            app.mat = remaining.max(axis=1).copy()
            app.to_scaled_doubly_stochastic_matrix()
        previous_support = update_support(app, previous_support)
        timers['completion_wall_ns'] += perf_counter_ns() - started
        started = perf_counter_ns()
        original_support = app.row_to_col
        if cost_priority:
            ordered = cost_ordered_support(
                [[j-server_n for j in row] for row in original_support],
                remaining, profile)
            app.row_to_col = [[j+server_n for j in row] for row in ordered]
        try:
            matched = app.hungarian()
        finally:
            app.row_to_col = original_support
        if matched != server_n:
            raise RuntimeError("native matching failed on completed residual")
        matching = [app.matching[i] - server_n for i in range(server_n)]
        timers['matching_wall_ns'] += perf_counter_ns() - started
        real = remaining.max(axis=1) > 0
        wan_pending = bool(np.any(real & cross))
        targets = real & cross if wan_priority and wan_pending else real
        # Uniform native behavior is changed only for WAN priority. Weighted
        # service also requires one real edge, to exclude virtual-only rounds.
        repair_needed = (wan_priority and wan_pending or weighted) and not any(
            targets[i, j] for i, j in enumerate(matching))
        if repair_needed:
            attempts += 1
            started = perf_counter_ns()
            support = [[j-server_n for j in adj] for adj in app.row_to_col]
            matching, changed = repair_matching(support, matching, targets)
            repairs += changed
            fallbacks += not changed
            timers['repair_wall_ns'] += perf_counter_ns() - started
        if weighted and not any(real[i, j] for i, j in enumerate(matching)):
            raise RuntimeError("no real edge in weighted matching after repair")
        started = perf_counter_ns()
        if weighted:
            quotas, duration_ns = weighted_quotas(remaining, matching, profile)
            pset = dict(mp=matching, freq=max(quotas), quota_per_gpu_bytes=quotas,
                        inter_duration_ns=duration_ns)
        else:
            app.matching = [-1] * (2 * server_n)
            for src, dst in enumerate(matching):
                app.matching[src] = dst + server_n
                app.matching[dst + server_n] = src
            extracted += app.update_permutation_sets()
            pset = app.p_sets[-1]
            quotas = [int(pset['freq'])] * server_n
            duration_ns = round((inter_info['alpha'] +
                                 pset['freq'] * inter_info['beta'] * 1e-6) * 1000)
        timers['quota_wall_ns'] += perf_counter_ns() - started
        stage = len(p_sets)
        wan_bytes = 0
        drained = 0
        real_bytes = 0
        for src, dst in enumerate(matching):
            dispatch = np.zeros((gpu_n, gpu_n), dtype=np.int64)
            before = int(remaining[src, :, dst].sum())
            # Preserve the native model's self-match bookkeeping for uniform
            # timing, but never emit those intrinsic bytes again as payload.
            transferred = locals_[src].server2server_dispatch(dst, dispatch, quotas[src])
            if src == dst:
                transferred = [0] * gpu_n
            rows[stage, src] = transferred
            ds[stage, src] = dispatch
            remaining[src, :, dst] -= transferred
            amount = sum(transferred)
            real_bytes += amount
            if cross[src, dst]:
                wan_bytes += amount
            drained += before > 0 and not np.any(remaining[src, :, dst])
        if weighted and not drained:
            raise RuntimeError("weighted round made no server-pair progress")
        records.append(dict(stage_id=stage, mp=list(matching),
                            quota_per_gpu_bytes=quotas, inter_duration_ns=duration_ns,
                            wan_pending_before=wan_pending, real_wan_bytes=int(wan_bytes),
                            real_inter_bytes=int(real_bytes), pairs_drained=int(drained)))
        p_sets.append(pset)
    if np.any(remaining):
        raise RuntimeError("decomposition left real bytes undelivered")
    if weighted and len(p_sets) > initial_pairs:
        raise RuntimeError("weighted stage count exceeds initial real pair count")
    gs.last_bs, gs.last_ds, gs.last_inter_rows = bs, ds, rows
    gs.last_intra_mode, gs.last_intra_fn = 'spread_out', None
    intra_cost = lambda x: native.spread_out(x, gpu_n, intra_info, 1e-6)
    balance_time = max(sum(intra_cost(bs[s, d]) for d in range(server_n) if s != d)
                       for s in range(server_n))
    time_us = balance_time
    bound_us = balance_time
    for k, pset in enumerate(p_sets):
        inter_us = (pset['inter_duration_ns'] / 1000 if weighted else
                    inter_info['alpha'] + pset['freq'] * inter_info['beta'] * 1e-6)
        local_us = max(intra_cost(l.intrinsic_all2all) for l in locals_) if k == 0 else max(
            intra_cost(ds[k-1, s]) for s in range(server_n))
        time_us += max(inter_us, local_us)
        bound_us += inter_us
    if p_sets:
        time_us += max(intra_cost(ds[len(p_sets)-1, s]) for s in range(server_n))
    baseline = native.spread_out_baseline(mat, server_n, gpu_n,
                                         inter_info, intra_info, 1e-6)
    return dict(gs=gs, locals=locals_, p_sets=p_sets, mat=mat, server_mat=gs.mat,
                t=time_us, baseline=baseline, bound=bound_us,
                speedup=baseline/time_us if time_us else 1.0,
                stage_records=records, real_server_demand=expected.tolist(),
                synthesis={**timers, 'repair_count': repairs,
                           'repair_attempt_count': attempts,
                           'repair_fallback_count': fallbacks,
                           'initial_real_pairs': initial_pairs})
