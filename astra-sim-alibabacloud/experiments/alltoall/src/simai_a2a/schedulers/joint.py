"""Thin deployment of the frozen FAST research planner and native volume helpers.

The planner sees directed server-fabric resources. GPU access links remain in
ns-3, not aggregated into a fictitious single per-server GPU access resource.
"""
from collections import deque
import hashlib
import importlib
from pathlib import Path
import subprocess
import sys
import time

from ..model import Schedule
from .fast_native import NativeFastScheduler, ROOT

FAST = ROOT.parents[2].parent/'FAST'
RESEARCH = FAST/'ds/decomposition'


def research_modules():
    # Reuse the actual implementation; never maintain a second copy here.
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    subprocess.run(['make', '-s', '-C', str(FAST/'simulation'), 'runtime-host-build'],
                   check=True, capture_output=True, timeout=90)
    return importlib.import_module('corrected_study'), importlib.import_module('joint_resource')


def topology_snapshot(topology, hardware):
    """Unique min-hop fixed routes, matching the local ns-3 routing assumption.

    Reject equal-hop alternatives rather than silently pricing a different path.
    Propagation includes the two GPU/NVSwitch links; capacity resources begin at
    the server NVSwitch uplinks. No RTT-to-one-way conversion is performed.
    """
    adjacency = {node: [] for node in topology.nvswitch_ids+topology.regular_switch_ids}
    names, rates, edges = [], [], {}
    for link in topology.links:
        if link.src not in adjacency or link.dst not in adjacency:
            continue
        for src, dst in ((link.src, link.dst), (link.dst, link.src)):
            edges[src, dst] = len(names)
            names.append(f'link:{src}->{dst}'); rates.append(link.bandwidth_gbps)
            adjacency[src].append((dst, link.latency_ns))
    paths = []
    for source in topology.nvswitch_ids:
        distance, count, parent = {source: 0}, {source: 1}, {}
        queue = deque([source])
        while queue:
            u = queue.popleft()
            for v, delay in adjacency[u]:
                if v not in distance:
                    distance[v] = distance[u]+1; count[v] = count[u]
                    parent[v] = (u, delay); queue.append(v)
                elif distance[v] == distance[u]+1:
                    count[v] += count[u]
        for target in topology.nvswitch_ids:
            if source == target:
                paths.append(dict(resource_ids=[], propagation_us=0.)); continue
            if target not in distance or count[target] != 1:
                raise ValueError('joint planner requires connected, unique min-hop fixed routes')
            route, delay, v = [], 2*hardware.intra.alpha_ns, target
            while v != source:
                u, tau = parent[v]; route.append(edges[u, v]); delay += tau; v = u
            paths.append(dict(resource_ids=list(reversed(route)), propagation_us=delay/1000))
    return dict(n=hardware.num_servers, g=hardware.gpus_per_server,
                resource_names=names, bandwidth_gbps=rates, server_paths=paths)


class JointScheduler:
    def __init__(self, algorithm, options=None):
        self.algorithm, self.options = algorithm, options or {}

    def schedule(self, matrix, hardware, *, seed):
        hardware.validate_matrix(matrix)
        if 'topology_snapshot' not in self.options:
            raise ValueError('joint planner requires explicit fixed-path topology_snapshot')
        c, joint = research_modules()
        raw = c.host(list(map(int, matrix.values.flat)), hardware.num_servers, hardware.gpus_per_server)
        raw['topology_snapshot'] = self.options['topology_snapshot']
        objective, budget = self.algorithm.removeprefix('fast-joint-').rsplit('-b', 1)
        tick = time.process_time_ns()
        plan = joint.plan(raw, 1, objective, budget=int(budget))
        cpu = (time.process_time_ns()-tick)/1000
        records = [dict(mp=p, freq=t['capacity']) for p, t in zip(plan['permutations'], plan['trace'])]
        exported = NativeFastScheduler(fast_options={**self.options, 'replay_stages': records}).schedule(
            matrix, hardware, seed=seed)
        return Schedule(self.algorithm, exported.flows, None, {**exported.metadata,
            'planner_unit_bytes': 1, 'planner_cpu_us': cpu, 'joint_plan': plan,
            'planner_objective': objective, 'threshold_budget': int(budget),
            'planner_source_sha256': hashlib.sha256((RESEARCH/'joint_resource.py').read_bytes()).hexdigest(),
            'topology_snapshot': self.options['topology_snapshot']})
