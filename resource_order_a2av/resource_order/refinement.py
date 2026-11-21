"""Development-only leximax refinement of a whole-message matching cover.

Keep the old max(Phi,D) objective primary; when it ties, reduce min(Phi,D).
The integer comparison grid is 1 ps (1e-6 us), not a tunable performance weight.
Only complete alternating cycles are exchanged. No simulation enters selection.
The frozen cover.py optimizer and all existing benchmark defaults are unchanged.
"""
import ctypes
import math

from .cover import proposals, isolated_costs, dependency_bound, native_evaluator, exchange

QUANTUM_US = 1e-6


def score_key(phi, dependency):
    if not all(math.isfinite(x) and x >= 0 for x in (phi, dependency)):
        raise ValueError('scores must be finite nonnegative service times')
    return tuple(sorted((round(phi / QUANTUM_US), round(dependency / QUANTUM_US)), reverse=True))


def resource_proxy(messages, cover, network):
    work = {(f.src, f.dst): tuple((e, f.bytes * .008 / network.links[e].gbps)
                                for e in network.path(f.src, f.dst)) for f in messages}
    return proxy_from_work(cover, work)


def proxy_from_work(cover, work):
    result = 0.
    for row in cover:
        loads = {}
        for src, dst in enumerate(row):
            for e, value in work.get((src, dst), ()):
                loads[e] = loads.get(e, 0.) + value
        result += max(loads.values(), default=0.)
    return result


def roundoff_floor(value, operations):
    """Conservative floor across two nonnegative floating-point recurrences."""
    product = operations * 2.**-53
    if product >= .5:
        return 0.
    gamma = product/(1-product)
    return max(0., math.nextafter(value*(1-gamma)/(1+gamma), -math.inf))


def capacity_floor(work, n, groups):
    """Bound all floating group sums, using exact sums of stored edge costs."""
    from fractions import Fraction
    totals = {}
    for values in work.values():
        for edge, value in values:
            totals[edge] = totals.get(edge, Fraction()) + Fraction.from_float(value)
    return roundoff_floor(float(max(totals.values(), default=Fraction())), n+groups+4)


def critical_path(cover, costs):
    """One longest isolated-time dependency path, represented by actual pairs.

    If no moved message belongs to this path, its order remains a subsequence
    of every affected endpoint queue. New intermediate messages only add
    nonnegative work, so the path cannot become shorter in exact arithmetic.
    """
    n = len(cover[0]) if cover else 0
    send = [0.]*n; recv = [0.]*n
    send_last = [None]*n; recv_last = [None]*n
    parent = {}; maximum = 0.; tail = None
    for row in cover:
        for src, dst in enumerate(row):
            item = costs.get((src,dst))
            if item is None:
                continue
            forward, reverse, serial = item
            pair = (src,dst)
            parent[pair] = send_last[src] if send[src] >= recv[dst]+reverse else recv_last[dst]
            done = max(send[src],recv[dst]+reverse)+forward+serial
            recv[dst] = done; send[src] = done+reverse
            send_last[src] = recv_last[dst] = pair
            if tail is None or done > maximum:
                maximum, tail = done, pair
    witness = set()
    while tail is not None:
        if tail in witness:
            raise ValueError('repeated pair in dependency path')
        witness.add(tail); tail = parent[tail]
    return maximum, witness


def minimize_leximax(messages, cover, network, *, on_step=None, cache_pairs=True, native_bound=None,
                    critical_pruning=False):
    """Strict steepest descent of the quantized (max,min) pair to stationarity.

    No fixed iteration/candidate budget. Cached resource gains are invalidated
    for every pair involving a changed group; dependency times always re-evaluate.
    Full resource recomputation before accepting a best candidate prevents gain
    subtraction rounding from inventing an improvement at a quantization boundary.
    """
    messages = tuple(messages)
    costs = isolated_costs(messages, network)
    work = {(f.src, f.dst): tuple((e, f.bytes * .008 / network.links[e].gbps)
                                for e in network.path(f.src, f.dst)) for f in messages}
    library = ctypes.CDLL(str(native_bound)) if native_bound else None
    evaluator = native_evaluator
    if library is not None and hasattr(library, 'cover_prefix_create'):
        from .prefix_bound import prefix_evaluator
        evaluator = prefix_evaluator
    lower_phi = capacity_floor(work, network.gpu_count, len(cover)) if critical_pruning else 0.
    history, cache, affected = [], {}, None
    while True:
        moves, info = proposals(messages, cover, network, positive_only=False,
                                pairs=affected if cache_pairs else None)
        if cache_pairs:
            if affected is not None:
                for pair in affected:
                    cache.pop(pair, None)
            for move in moves:
                cache.setdefault((move['a'], move['b']), []).append(move)
            moves = [m for ms in cache.values() for m in ms]
            moves.sort(key=lambda m: (-round(m['proxy_gain_us'], 9), m['a'], m['b'], m['cycle']))
        phi = proxy_from_work(cover, work)
        dependency, witness = critical_path(cover, costs) if critical_pruning else (dependency_bound(cover, costs), set())
        lower_untouched = roundoff_floor(dependency, 4*len(cover)+4)
        current = best_key = score_key(phi, dependency)
        best, best_cover, evaluations, pruned = None, None, 0, 0
        fast = evaluator(cover, costs, library) if library else None
        for move in moves:
            predicted_phi = phi - move['proxy_gain_us']
            # Conservative by a full grid unit; exact recomputation below decides.
            if predicted_phi > (best_key[0] + 1) * QUANTUM_US:
                continue
            if (critical_pruning and score_key(lower_phi, lower_untouched) >= best_key
                    and not any((s,cover[move['a']][s]) in witness or (s,cover[move['b']][s]) in witness
                                for s in move['cycle'])):
                pruned += 1
                continue
            cutoff = (best_key[0] + 1) * QUANTUM_US
            changed = None
            if fast:
                value = fast(move['a'], move['b'], move['cycle'], cutoff)
            else:
                changed = exchange(cover, move['a'], move['b'], move['cycle'])
                value = dependency_bound(changed, costs, cutoff=cutoff)
            evaluations += 1
            if value >= cutoff:
                continue  # partial evaluation cannot be an improving exact score
            if score_key(max(0., predicted_phi), value) >= best_key:
                # Within numerical distance of a rounding boundary, still verify.
                near = abs(predicted_phi / QUANTUM_US - round(predicted_phi / QUANTUM_US) - .5)
                near = min(near, abs(predicted_phi / QUANTUM_US - round(predicted_phi / QUANTUM_US) + .5))
                if near > 1e-4:
                    continue
            changed = changed or exchange(cover, move['a'], move['b'], move['cycle'])
            exact_phi = proxy_from_work(changed, work)
            candidate = score_key(exact_phi, value)
            if candidate < best_key:
                best_key, best, best_cover = candidate, move, changed
        record = dict(iteration=len(history), proxy_us=phi, dependency_bound_us=dependency,
                      joint_us=max(phi, dependency), score_key=current,
                      dependency_evaluations=evaluations, move=best)
        if critical_pruning:
            record['critical_pruned'] = pruned
        if history and not current < tuple(history[-1]['score_key']):
            raise ArithmeticError('accepted exchange did not strictly decrease leximax score')
        history.append(record)
        if on_step:
            on_step(cover, record)
        if best is None:
            return cover, history
        cover = best_cover
        affected = {(min(a,b), max(a,b)) for a in (best['a'], best['b'])
                    for b in range(len(cover)) if a != b}


def main():
    """Refine a frozen final cover, recording base and incremental planning time."""
    import argparse
    import hashlib
    import json
    from pathlib import Path
    import time
    from .benchmark import network_for
    from .cover import cover_plan
    from .model import Message, Plan
    from .simai import run

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-run', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scale', choices=['32', '128'], required=True)
    parser.add_argument('--topology', choices=['tree', 'leaf-spine'], default='tree')
    parser.add_argument('--native-bound', type=Path)
    parser.add_argument('--critical-pruning', action='store_true', help='exact unchanged-path lower-bound pruning')
    parser.add_argument('--binary', default='/home/julius/fast/SimAI/bin/SimAI_simulator')
    args = parser.parse_args()
    checkpoint = json.loads(args.checkpoint.read_text())
    if checkpoint['history'][-1]['move'] is not None:
        parser.error('base optimizer is not stationary; refusing unfinished checkpoint')
    data = json.loads((args.base_run/'plan.json').read_text())
    flows = tuple(Message(**f) for f in data['messages'])
    cover = tuple(tuple(row) for row in checkpoint['cover'])
    ranks = checkpoint['rank_order']
    initial = cover_plan(flows, cover, ranks)
    if initial.dependency_key() != Plan.from_order(flows, data['order']).dependency_key():
        parser.error('checkpoint and base plan queues differ')
    network = network_for(args.topology, args.scale, 'heterogeneous')
    # Verify actual path inputs rather than silently reusing a different topology.
    from .simai import prepare
    import tempfile
    with tempfile.TemporaryDirectory(prefix='a2av-refine-verify-') as temporary:
        prepare(initial, network, temporary)
        for name in ('topology.txt', 'routes.csv'):
            if (Path(temporary)/name).read_bytes() != (args.base_run/name).read_bytes():
                parser.error('base '+name+' differs from requested fixed scenario')
        def settings(path):
            # Only file destinations differ between otherwise identical runs.
            return [line for line in path.read_text().splitlines()
                    if line.strip() and not line.split()[0].endswith('_FILE')]
        if settings(Path(temporary)/'ns3.conf') != settings(args.base_run/'ns3.conf'):
            parser.error('base transport settings differ')
    args.output.mkdir(parents=True, exist_ok=False)
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    manifest = dict(arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
                    input_sha256=digest(args.base_run/'plan.json'), checkpoint_sha256=digest(args.checkpoint),
                    base_planning_s=checkpoint['planning_s'], quantum_us=QUANTUM_US,
                    source_sha256={p.name:digest(p) for p in Path(__file__).parent.iterdir()
                                   if p.suffix in ('.py','.cpp')})
    if args.native_bound:
        args.native_bound=args.native_bound.resolve(strict=True)
        manifest['native_sha256']=digest(args.native_bound)
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    started=time.perf_counter();history=[]
    def save(c,record):
        history.append(record)
        state=dict(cover=c, rank_order=ranks, history=history, refinement_s=time.perf_counter()-started,
                   base_planning_s=checkpoint['planning_s'], status='planning' if record['move'] else 'stationary')
        temp=args.output/'planning.json.tmp'
        temp.write_text(json.dumps(state));temp.replace(args.output/'planning.json')
        print(json.dumps({k:v for k,v in record.items() if k!='move'}),flush=True)
    final,_=minimize_leximax(flows,cover,network,on_step=save,native_bound=args.native_bound,
                            critical_pruning=args.critical_pruning)
    # Replayed initial is a transport-version control, not an optimization candidate.
    for name,c in [('initial',cover),('final',final)]:
        result=run(cover_plan(flows,c,ranks),network,args.output/name,args.binary)
        print(name,result['status'],result.get('delivery_ns'),flush=True)
        if result['status']!='success':return 1
        if name=='initial':
            old=json.loads((args.base_run/'result.json').read_text())
            if result['backend_sha256'] != old['backend_sha256']:
                raise ValueError('backend changed; stop comparison')
            if (result['delivery_ns'],result['ack_drain_ns']) != (old['delivery_ns'],old['ack_drain_ns']):
                raise ValueError('frozen initial replay changed; stop comparison')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
