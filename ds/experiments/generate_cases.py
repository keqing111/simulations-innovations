#!/usr/bin/env python3
"""Generate deterministic BVN_CASE_V1 inputs for the partial-BvN study.

The first-round matrix follows bvn_topology_test_matrix.md:
  T0/T1 with N=16, T2/T3-B/T3-L with N=32,
  W0, W1(alpha=.9), W2(p=.8), S=8MiB/128MiB, seeds 11/23/47.

All generated demand values are integer multiples of 1024 bytes.  The generated
manifest records the actual matrix hash and deduplicates identical matrices per
(topology, workload, size), because W2's seeded DC-role permutation can coincide.

W2 interpretation used here:
  Each source sends one hot flow to the hot destination DC and one cold flow to
  the cold destination DC.  Within each source DC, slots map to destination
  slots through an independent complete permutation, so each destination slot
  receives exactly one hot flow from the hot source group and one cold flow from
  the cold source group.  Thus every row and column is exactly S, while the
  traffic remains two flows per source (the sparse "complete permutation"
  reading of the matrix document, not a dense block fill).  The seed permutes
  DC roles; hot role -> role xor 1, cold role -> (role+2) mod 4, plus slot
  permutations.  The per-source group budget is rounded down to a whole 1KiB
  unit, and p_eff is recorded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
UNIT_BYTES = 1024
SIZES_MIB = (8, 128)
SEEDS = (11, 23, 47)
FIRST_ROUND_TOPOLOGIES_16 = ("T0", "T1")
FIRST_ROUND_TOPOLOGIES_32 = ("T2", "T3-B", "T3-L")


def digest_matrix(values):
    raw = b"".join(int(x).to_bytes(8, "little", signed=False) for x in values)
    return hashlib.sha256(raw).hexdigest()


class TopologyBuilder:
    def __init__(self):
        self.resources = {}
        self.paths = {}

    def resource(self, name, bandwidth):
        if name in self.resources:
            if self.resources[name] != bandwidth:
                raise ValueError(f"resource {name} bandwidth conflict")
            return
        self.resources[name] = bandwidth

    def path(self, src, dst, names, propagation, rtt=None):
        if src == dst:
            return
        if len(set(names)) != len(names):
            raise ValueError(f"repeated resource in path {src}->{dst}: {names}")
        if propagation < 0:
            raise ValueError("negative propagation")
        self.paths[(src, dst)] = (list(names), float(propagation),
                                  float(2 * propagation if rtt is None else rtt))

    def build(self, n):
        expected = {(s, d) for s in range(n) for d in range(n) if s != d}
        if set(self.paths) != expected:
            missing = expected - set(self.paths)
            extra = set(self.paths) - expected
            raise ValueError(f"path coverage mismatch: missing={sorted(missing)[:3]} "
                             f"extra={sorted(extra)[:3]}")
        names = list(self.resources)
        ids = {name: i for i, name in enumerate(names)}
        resources = [(name, self.resources[name]) for name in names]
        paths = []
        for s in range(n):
            for d in range(n):
                if s == d:
                    continue
                route, prop, rtt = self.paths[(s, d)]
                paths.append((s, d, prop, rtt, [ids[x] for x in route]))
        return resources, paths


def topology_t0(n=16):
    b = TopologyBuilder()
    for s in range(n):
        b.resource(f"srv-out:{s}", 400)
        b.resource(f"srv-in:{s}", 400)
    for s in range(n):
        for d in range(n):
            if s != d:
                b.path(s, d, [f"srv-out:{s}", f"srv-in:{d}"], 1.4)
    return b.build(n)


def topology_t1(n=16, wan_us=50.0):
    b = TopologyBuilder()
    dc = [s // 8 for s in range(n)]
    for s in range(n):
        b.resource(f"srv-out:{s}", 400)
        b.resource(f"srv-in:{s}", 400)
    for x in sorted(set(dc)):
        b.resource(f"gateway-out:{x}", 400)
        b.resource(f"gateway-in:{x}", 400)
    for a in sorted(set(dc)):
        for c in sorted(set(dc)):
            if a != c:
                b.resource(f"wan:{a}:{c}", 100)
    for s in range(n):
        for d in range(n):
            if s == d:
                continue
            route = [f"srv-out:{s}", f"srv-in:{d}"]
            prop = 1.4
            if dc[s] != dc[d]:
                route = [f"srv-out:{s}", f"gateway-out:{dc[s]}",
                         f"wan:{dc[s]}:{dc[d]}", f"gateway-in:{dc[d]}",
                         f"srv-in:{d}"]
                prop = wan_us + 2.8
            b.path(s, d, route, prop)
    return b.build(n)


def topology_t2(variant="T2"):
    n = 32
    b = TopologyBuilder()
    dc = [s // 8 for s in range(n)]
    tor = [x * 2 + (s % 8) // 4 for s, x in enumerate(dc)]
    wan_us = 50.0

    def dc_out_capacity(x):
        return 100.0 if variant in ("T3-B", "T3-BL") and x == 0 else 400.0

    def dc_in_capacity(x):
        return 100.0 if variant in ("T3-B", "T3-BL") and x == 1 else 400.0

    def wan_capacity(a, c):
        if variant in ("T3-B", "T3-BL") and {a, c} == {0, 1}:
            return 50.0
        return 200.0

    def one_way(a, c):
        if variant in ("T3-L", "T3-BL") and {a, c} == {0, 1}:
            return 5000.0
        return wan_us

    for s in range(n):
        b.resource(f"srv-out:{s}", 400)
        b.resource(f"srv-in:{s}", 400)
    for x in range(8):
        b.resource(f"tor-up:{x}", 800)
        b.resource(f"tor-down:{x}", 800)
    for x in range(4):
        b.resource(f"dc-out:{x}", dc_out_capacity(x))
        b.resource(f"dc-in:{x}", dc_in_capacity(x))
    for a in range(4):
        for c in range(4):
            if a != c:
                b.resource(f"wan:{a}:{c}", wan_capacity(a, c))

    for s in range(n):
        for d in range(n):
            if s == d:
                continue
            if tor[s] == tor[d]:
                route = [f"srv-out:{s}", f"srv-in:{d}"]
                prop = 1.4
            elif dc[s] == dc[d]:
                route = [f"srv-out:{s}", f"tor-up:{tor[s]}",
                         f"tor-down:{tor[d]}", f"srv-in:{d}"]
                prop = 2.8
            else:
                route = [f"srv-out:{s}", f"tor-up:{tor[s]}",
                         f"dc-out:{dc[s]}", f"wan:{dc[s]}:{dc[d]}",
                         f"dc-in:{dc[d]}", f"tor-down:{tor[d]}",
                         f"srv-in:{d}"]
                prop = one_way(dc[s], dc[d]) + 4.2
            b.path(s, d, route, prop)
    return b.build(n)


def apportion(weights, total):
    """Largest-remainder rounding; ties break by index."""
    weights = [float(x) for x in weights]
    denominator = sum(weights)
    if denominator <= 0:
        raise ValueError("nonpositive weight sum")
    exact = [total * x / denominator for x in weights]
    base = [int(math.floor(x)) for x in exact]
    left = total - sum(base)
    order = sorted(range(len(weights)),
                   key=lambda k: (-(exact[k] - base[k]), k))
    for k in order[:left]:
        base[k] += 1
    if sum(base) != total:
        raise AssertionError("apportion failed")
    return base


def workload_w0(n, s_units):
    q = n * s_units
    per_pair = q // (n * (n - 1))
    left = q - per_pair * n * (n - 1)
    if left % n:
        raise ValueError("W0 remainder must be a whole number of permutations")
    permutations = left // n
    if permutations >= n:
        raise ValueError("W0 needs more cyclic offsets than available")
    values = [0] * (n * n)
    for s in range(n):
        for d in range(n):
            if s != d:
                values[s * n + d] = per_pair
    for r in range(permutations):
        offset = r + 1
        for s in range(n):
            values[s * n + (s + offset) % n] += 1
    return values


def workload_w1(n, s_units, seed, alpha=0.9):
    rng = random.Random(seed)
    order = list(range(n))
    rng.shuffle(order)
    rank = [0] * n
    for r, d in enumerate(order):
        rank[d] = r
    values = [0] * (n * n)
    for s in range(n):
        destinations = [d for d in range(n) if d != s]
        weights = [(rank[d] + 1) ** (-alpha) for d in destinations]
        row = apportion(weights, s_units)
        for d, x in zip(destinations, row):
            values[s * n + d] = x
    return values


def workload_w2(n, s_units, seed, p=0.8):
    if n != 32:
        raise ValueError("W2 is defined for N=32")
    dc_count, m = 4, 8
    rng = random.Random(seed)
    roles = list(range(dc_count))
    rng.shuffle(roles)  # roles[dc] = role
    by_role = {role: dc for dc, role in enumerate(roles)}
    hot_per = int(math.floor(p * s_units))
    cold_per = s_units - hot_per
    if hot_per <= 0 or cold_per <= 0:
        raise ValueError("W2 budget rounded out of range")
    hot_perm = [list(range(m)) for _ in range(dc_count)]
    cold_perm = [list(range(m)) for _ in range(dc_count)]
    for x in range(dc_count):
        rng.shuffle(hot_perm[x])
        rng.shuffle(cold_perm[x])
    dc = [s // 8 for s in range(n)]
    values = [0] * (n * n)
    for s in range(n):
        src_dc = dc[s]
        slot = s % m
        role = roles[src_dc]
        hot_dc = by_role[role ^ 1]
        cold_dc = by_role[(role + 2) % dc_count]
        hot_dst = hot_dc * m + hot_perm[src_dc][slot]
        cold_dst = cold_dc * m + cold_perm[src_dc][slot]
        if hot_dst == s or cold_dst == s or hot_dst == cold_dst:
            raise AssertionError("W2 target collision")
        values[s * n + hot_dst] += hot_per
        values[s * n + cold_dst] += cold_per
        if sum(values[s * n + d] for d in range(n)) != s_units:
            raise AssertionError("W2 row sum")
    for d in range(n):
        if sum(values[s * n + d] for s in range(n)) != s_units:
            raise AssertionError("W2 column sum")
    return values, hot_per / s_units


def workload_w3(n, s_units, seed, alpha=0.9):
    """Bilateral Zipf: source and destination ranks are independent."""
    rng = random.Random(seed)
    src_order = list(range(n))
    dst_order = list(range(n))
    rng.shuffle(src_order)
    rng.shuffle(dst_order)
    src_rank = [0] * n
    dst_rank = [0] * n
    for r, x in enumerate(src_order):
        src_rank[x] = r
    for r, x in enumerate(dst_order):
        dst_rank[x] = r
    weights = []
    for s in range(n):
        for d in range(n):
            if s == d:
                weights.append(0.0)
            else:
                weights.append(((src_rank[s] + 1) * (dst_rank[d] + 1)) ** (-alpha))
    values = apportion(weights, n * s_units)
    for s in range(n):
        values[s * n + s] = 0
    return values


def workload_w4(n, s_units, seed, shifts_count, zipf=False):
    """Mixture of complete cyclic permutations, fixed total Q=N*S."""
    if not 1 <= shifts_count < n:
        raise ValueError("shifts_count must be in [1,N-1]")
    rng = random.Random(seed)
    shifts = rng.sample(range(1, n), shifts_count)
    edge_weights = []
    for r, shift in enumerate(shifts):
        weight = (r + 1) ** (-0.9) if zipf else 1.0
        for s in range(n):
            edge_weights.append((s, (s + shift) % n, weight))
    values = [0] * (n * n)
    apportioned = apportion([x[2] for x in edge_weights], n * s_units)
    for (s, d, _), x in zip(edge_weights, apportioned):
        values[s * n + d] += x
    return values


def write_case(path, n, demand_units, resources, paths, unit_bytes):
    demand_bytes = [int(x) * unit_bytes for x in demand_units]
    if len(demand_bytes) != n * n:
        raise ValueError("demand dimensions")
    if any(demand_bytes[s * n + s] for s in range(n)):
        raise ValueError("nonzero diagonal")
    if any(x < 0 for x in demand_bytes):
        raise ValueError("negative demand")
    if sum(1 for s in range(n) for d in range(n) if s != d and demand_bytes[s * n + d]) == 0:
        pass
    lines = ["BVN_CASE_V1", f"N {n}", "DEMAND"]
    lines.extend(str(x) for x in demand_bytes)
    lines.append(f"RESOURCES {len(resources)}")
    for rid, (name, bandwidth) in enumerate(resources):
        lines.append(f"{rid} {name} {bandwidth:.17g}")
    lines.append(f"PATHS {n * (n - 1)}")
    for s, d, prop, rtt, ids in paths:
        lines.append(f"{s} {d} {prop:.17g} {rtt:.17g} {len(ids)} " +
                     " ".join(str(x) for x in ids))
    text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return {
        "path": str(path.relative_to(ROOT)),
        "n": n,
        "unit_bytes": unit_bytes,
        "demand_bytes": sum(demand_bytes),
        "demand_sha256": hashlib.sha256(
            b"".join(int(x).to_bytes(8, "little", signed=False)
                     for x in demand_bytes)).hexdigest(),
        "matrix_sha256": digest_matrix(demand_units),
        "resource_count": len(resources),
        "path_count": len(paths),
        "case_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def add_case(cases, seen, case_id, topology, workload, size_mib, seed, n,
             matrix, builder, unit_bytes, dedupe_key=None):
    resources, paths = builder
    path = HERE / "cases" / f"{case_id}.txt"
    meta = write_case(path, n, matrix, resources, paths, unit_bytes)
    meta.update(id=case_id, topology=topology, workload=workload,
                size_mib=size_mib, seed=seed)
    key = dedupe_key or (topology, workload, size_mib, meta["matrix_sha256"])
    if key in seen:
        return False
    seen.add(key)
    cases.append(meta)
    return True


def build_first_round():
    cases, seen = [], set()
    topologies = {
        "T0": topology_t0(16),
        "T1": topology_t1(16),
        "T2": topology_t2("T2"),
        "T3-B": topology_t2("T3-B"),
        "T3-L": topology_t2("T3-L"),
    }
    for topo in FIRST_ROUND_TOPOLOGIES_16:
        n = 16
        for size in SIZES_MIB:
            s_units = size * 1024
            matrix = workload_w0(n, s_units)
            add_case(cases, seen, f"{topo}-W0-{size}MiB", topo, "W0", size, 0,
                     n, matrix, topologies[topo], UNIT_BYTES)
            for seed in SEEDS:
                matrix = workload_w1(n, s_units, seed)
                add_case(cases, seen, f"{topo}-W1-{size}MiB-s{seed}", topo, "W1",
                         size, seed, n, matrix, topologies[topo], UNIT_BYTES)
    for topo in FIRST_ROUND_TOPOLOGIES_32:
        n = 32
        for size in SIZES_MIB:
            s_units = size * 1024
            matrix = workload_w0(n, s_units)
            add_case(cases, seen, f"{topo}-W0-{size}MiB", topo, "W0", size, 0,
                     n, matrix, topologies[topo], UNIT_BYTES)
            for seed in SEEDS:
                matrix = workload_w1(n, s_units, seed)
                add_case(cases, seen, f"{topo}-W1-{size}MiB-s{seed}", topo, "W1",
                         size, seed, n, matrix, topologies[topo], UNIT_BYTES)
                matrix, p_eff = workload_w2(n, s_units, seed)
                ok = add_case(cases, seen, f"{topo}-W2-{size}MiB-s{seed}", topo,
                              "W2", size, seed, n, matrix, topologies[topo],
                              UNIT_BYTES)
                if ok:
                    cases[-1]["p_eff"] = p_eff
    return cases


def build_extensions():
    """W3/W4 extension cases, off by default."""
    cases, seen = [], set()
    topologies = {
        "T0": topology_t0(16),
        "T1": topology_t1(16),
        "T2": topology_t2("T2"),
        "T3-B": topology_t2("T3-B"),
        "T3-L": topology_t2("T3-L"),
    }
    for topo in ("T0", "T1", "T2", "T3-B", "T3-L"):
        n = 16 if topo in ("T0", "T1") else 32
        for size in SIZES_MIB:
            s_units = size * 1024
            for seed in SEEDS:
                matrix = workload_w3(n, s_units, seed)
                add_case(cases, seen, f"{topo}-W3-{size}MiB-s{seed}", topo, "W3",
                         size, seed, n, matrix, topologies[topo], UNIT_BYTES)
            for shifts in (2, 4):
                matrix = workload_w4(n, s_units, 11, shifts)
                add_case(cases, seen, f"{topo}-W4-d{shifts}-{size}MiB", topo, "W4",
                         size, 11, n, matrix, topologies[topo], UNIT_BYTES)
    return cases


def hand_cases():
    cases = []

    def simple(topology_name, n, demand, resources, paths):
        b = TopologyBuilder()
        for name, bw in resources:
            b.resource(name, bw)
        for s, d, names, prop in paths:
            b.path(s, d, names, prop)
        built = b.build(n)
        case_id = f"HAND-{topology_name}"
        path = HERE / "cases" / f"{case_id}.txt"
        meta = write_case(path, n, demand, built[0], built[1], 1)
        meta.update(id=case_id, topology=topology_name, workload="hand",
                    size_mib=0, seed=0)
        cases.append(meta)

    # C0: empty N=1.
    simple("C0", 1, [0], [], [])
    # C1: one 1024-byte flow on a 100 Gbps, 5us path.
    simple("C1", 2, [0, 1024, 0, 0], [("r", 100)],
           [(0, 1, ["r"], 5.0), (1, 0, ["r"], 5.0)])
    # C2: two disjoint flows sharing one directed resource.
    d2 = [0, 1000, 0, 0,
          0, 0, 0, 0,
          0, 0, 0, 1000,
          0, 0, 0, 0]
    simple("C2", 4, d2, [("shared", 100)],
           [(s, d, ["shared"], 5.0) for s in range(4) for d in range(4) if s != d])
    # C3: same demand, independent resources per flow.
    simple("C3", 4, d2, [("r01", 100), ("r23", 100)],
           [(s, d, ["r01"] if (s, d) == (0, 1) else ["r23"], 5.0)
            for s in range(4) for d in range(4) if s != d])
    # C4: sparse 3x3 demand; padding can introduce a self edge.
    d4 = [0, 0, 1,
          2, 0, 0,
          0, 0, 0]
    simple("C4", 3, d4, [("r", 100)],
           [(s, d, ["r"], 0.0) for s in range(3) for d in range(3) if s != d])
    # C5: same demand, one uniform shared resource.
    simple("C5", 3, d4, [("r", 100)],
           [(s, d, ["r"], 0.0) for s in range(3) for d in range(3) if s != d])
    # C6: nonuniform paths and nonuniform bandwidths.
    d6 = [0, 1000, 1,
          1, 0, 1000,
          1000, 1, 0]
    simple("C6", 3, d6,
           [("r01", 100), ("r02", 1), ("r10", 10),
            ("r12", 1000), ("r20", 100), ("r21", 5)],
           [(0, 1, ["r01"], 0.0), (0, 2, ["r02"], 5.0),
            (1, 0, ["r10"], 1.0), (1, 2, ["r12"], 0.0),
            (2, 0, ["r20"], 2.0), (2, 1, ["r21"], 10.0)])
    # C7: support graph has no perfect matching, but partial demand remains.
    d7 = [0, 1, 1,
          0, 0, 1,
          0, 0, 0]
    simple("C7", 3, d7, [("r01", 100), ("r02", 100), ("r12", 100)],
           [(0, 1, ["r01"], 0.0), (0, 2, ["r02"], 0.0),
            (1, 2, ["r12"], 0.0),
            (1, 0, ["r01"], 0.0), (2, 0, ["r02"], 0.0),
            (2, 1, ["r12"], 0.0)])
    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "manifest.json"))
    parser.add_argument("--include-hand", action="store_true")
    parser.add_argument("--extensions", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    if args.smoke:
        cases = hand_cases()
        # Add one representative full-size case from each topology family.
        first = build_first_round()
        picks = {("T0", "W0", 8), ("T1", "W1", 8), ("T2", "W0", 8),
                 ("T3-B", "W2", 8), ("T3-L", "W1", 8)}
        cases.extend([c for c in first
                      if (c["topology"], c["workload"], c["size_mib"]) in picks])
    else:
        cases = build_first_round()
        if args.include_hand:
            cases = hand_cases() + cases
        if args.extensions:
            cases += build_extensions()

    methods = ["fast-native", "partial-endpoint", "partial-topology",
               "partial-time", "partial-delay"]
    manifest = {
        "schema": 1,
        "generated_by": "ds/experiments/generate_cases.py",
        "unit_bytes": UNIT_BYTES,
        "sizes_mib": list(SIZES_MIB),
        "seeds": list(SEEDS),
        "methods": methods,
        "repeat": 1 if args.smoke else 3,
        "cases": cases,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(cases)} cases to {out}")
    print("by topology/workload:")
    counts = {}
    for c in cases:
        key = (c["topology"], c["workload"])
        counts[key] = counts.get(key, 0) + 1
    for key in sorted(counts):
        print(f"  {key[0]:5s} {key[1]:2s}: {counts[key]}")


if __name__ == "__main__":
    main()
