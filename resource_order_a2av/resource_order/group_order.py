"""Order-only refinement: fixed whole-message groups, coupled service/DAG cost.

This changes neither matching membership nor the sum of group resource maxima.
For simultaneous equal-share users of one link, service_i=sum_j min(x_i,x_j)/B.
The maximum along a path is propagated through send/receive dependencies. This
is a heuristic, not an ns-3 makespan bound: cross-group sharing and DCQCN are
not modeled. No observed network time participates in candidate selection.
"""
import ctypes

from .cover import cover_plan, dependency_bound, native_evaluator


def service_costs(messages, cover, network, *, shared=True):
    """Precompute per-pair times; valid only while group membership is fixed."""
    messages = tuple(messages)
    cover_plan(messages, cover, range(network.gpu_count))
    pairs = {(f.src, f.dst): f for f in messages}
    result = {}
    for row in cover:
        flows = [pairs[s, d] for s, d in enumerate(row) if (s, d) in pairs]
        resources = {}
        service = {f.id: 0. for f in flows}
        for f in flows:
            for edge in network.path(f.src, f.dst):
                resources.setdefault(edge, []).append(f)
        for edge, users in resources.items():
            users.sort(key=lambda f: (f.bytes, f.id))
            prefix = 0
            for index, f in enumerate(users):
                work = prefix + (len(users)-index)*f.bytes if shared else f.bytes
                prefix += f.bytes
                service[f.id] = max(service[f.id], work*.008/network.links[edge].gbps)
        for f in flows:
            result[f.src, f.dst] = (network.delay_us(f.src, f.dst),
                                    network.delay_us(f.dst, f.src), service[f.id])
    return result


def swap_groups(cover, a, b):
    result = list(cover)
    result[a], result[b] = result[b], result[a]
    return tuple(result)


def minimize_order(messages, cover, network, *, shared=True, on_step=None, native_bound=None):
    """Steepest descent over all two-group swaps, without a count budget.

    O(K^2) neighbors; each DAG evaluation O(KN), or O(K^3 N) per iteration.
    Per-pair service is prepared once because the membership of every group
    stays fixed. A group swap is legal even if its union has multiple cycles.
    """
    cover = tuple(tuple(row) for row in cover)
    if any(len(row) != network.gpu_count or set(row) != set(range(network.gpu_count)) for row in cover):
        raise ValueError('order refinement expects complete matchings')
    costs = service_costs(messages, cover, network, shared=shared)
    library = ctypes.CDLL(str(native_bound)) if native_bound else None
    history = []
    while True:
        current = best_value = dependency_bound(cover, costs)
        best = None
        fast = native_evaluator(cover, costs, library) if library and cover else None
        evaluations = 0
        for a in range(len(cover)):
            for b in range(a+1, len(cover)):
                # Swapping all sources exchanges two entire valid matchings.
                value = (fast(a, b, range(network.gpu_count), best_value-1e-9) if fast else
                         dependency_bound(swap_groups(cover, a, b), costs, cutoff=best_value-1e-9))
                evaluations += 1
                if value < best_value-1e-9:
                    best_value, best = value, (a, b)
        record = dict(iteration=len(history), coupled_us=current, move=best,
                      evaluations=evaluations, shared_service=shared)
        if history and current >= history[-1]['coupled_us']-1e-9:
            raise ArithmeticError('group swap did not decrease recomputed score')
        history.append(record)
        if on_step:
            on_step(cover, record)
        if best is None:
            return cover, history
        cover = swap_groups(cover, *best)


def main():
    """Development experiment: refine one frozen plan; no ns-3 candidate search."""
    import argparse
    import hashlib
    import json
    from pathlib import Path
    import tempfile
    import time
    from .benchmark import network_for
    from .model import Message, Plan
    from .refinement import resource_proxy
    from .simai import prepare, run

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-run', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scale', choices=['32', '128'], required=True)
    parser.add_argument('--native-bound', type=Path)
    parser.add_argument('--isolated', action='store_true', help='ablation: omit shared service cost')
    parser.add_argument('--binary', default='/home/julius/fast/SimAI/bin/SimAI_simulator')
    args = parser.parse_args()
    state = json.loads(args.checkpoint.read_text())
    if state['history'][-1]['move'] is not None:
        parser.error('base optimizer must be stationary')
    data = json.loads((args.base_run/'plan.json').read_text())
    flows = tuple(Message(**f) for f in data['messages'])
    cover = tuple(tuple(row) for row in state['cover'])
    ranks = state['rank_order']
    initial = cover_plan(flows, cover, ranks)
    if initial.dependency_key() != Plan.from_order(flows, data['order']).dependency_key():
        parser.error('checkpoint and base queues differ')
    network = network_for('tree', args.scale, 'heterogeneous')
    with tempfile.TemporaryDirectory(prefix='a2av-order-verify-') as temporary:
        prepare(initial, network, temporary)
        for name in ('topology.txt', 'routes.csv'):
            if (Path(temporary)/name).read_bytes() != (args.base_run/name).read_bytes():
                parser.error('base '+name+' differs from fixed scenario')
        settings = lambda p: [line for line in p.read_text().splitlines()
                              if line.strip() and not line.split()[0].endswith('_FILE')]
        if settings(Path(temporary)/'ns3.conf') != settings(args.base_run/'ns3.conf'):
            parser.error('base transport configuration differs')
    args.output.mkdir(parents=True, exist_ok=False)
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    manifest = dict(arguments={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                    base_planning_s=state['planning_s'], input_sha256=digest(args.base_run/'plan.json'),
                    checkpoint_sha256=digest(args.checkpoint),
                    source_sha256={p.name: digest(p) for p in Path(__file__).parent.iterdir()
                                   if p.suffix in ('.py', '.cpp')})
    if args.native_bound:
        args.native_bound = args.native_bound.resolve(strict=True)
        manifest['native_sha256'] = digest(args.native_bound)
    (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2))
    started = time.perf_counter()
    history = []
    phi = resource_proxy(flows, cover, network)

    def save(c, record):
        history.append(record)
        checkpoint = dict(cover=c, rank_order=ranks, history=history, base_planning_s=state['planning_s'],
                          refinement_s=time.perf_counter()-started, invariant_phi_us=phi,
                          status='planning' if record['move'] is not None else 'stationary')
        temp = args.output/'planning.json.tmp'
        temp.write_text(json.dumps(checkpoint))
        temp.replace(args.output/'planning.json')
        print(json.dumps(record), flush=True)

    final, _ = minimize_order(flows, cover, network, shared=not args.isolated,
                              on_step=save, native_bound=args.native_bound)
    if sorted(final) != sorted(cover):
        raise ValueError('group membership changed')
    for name, c in [('initial', cover), ('final', final)]:
        result = run(cover_plan(flows, c, ranks), network, args.output/name, args.binary)
        print(name, result['status'], result.get('delivery_ns'), flush=True)
        if result['status'] != 'success':
            return 1
        if name == 'initial':
            old = json.loads((args.base_run/'result.json').read_text())
            if result['backend_sha256'] != old['backend_sha256']:
                raise ValueError('backend changed')
            if (result['delivery_ns'], result['ack_drain_ns']) != (old['delivery_ns'], old['ack_drain_ns']):
                raise ValueError('frozen initial replay changed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
