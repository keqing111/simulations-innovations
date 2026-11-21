"""Published XOR partner order under our common whole-message executor.

The Pair policy is described in STAR-MPI, ICS 2006, section 3.1:
https://www.cs.fsu.edu/~xyuan/paper/06ics.pdf
This is a partner-order comparator, NOT the complete MPI library/runtime.
No global barriers, splitting, routing changes, or simulation-based selection.
Kept separate so the frozen main algorithm's sources are unchanged.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import time

from .benchmark import network_for
from .model import Message, Plan
from .scheduler import interleaved_ranks
from .simai import prepare, run


def pairwise_xor(messages, n, rank_order=None):
    if type(n) is not int or n < 2 or n & (n - 1):
        raise ValueError('XOR partner order requires a power-of-two endpoint count')
    ranks = tuple(range(n)) if rank_order is None else tuple(rank_order)
    if any(type(i) is not int for i in ranks) or len(ranks) != n or set(ranks) != set(range(n)):
        raise ValueError('invalid rank permutation')
    messages = tuple(messages)
    if any(max(f.src, f.dst) >= n for f in messages):
        raise ValueError('endpoint outside comparator topology')
    position = {rank: i for i, rank in enumerate(ranks)}
    ordered = sorted(messages, key=lambda f: (
        position[f.src] ^ position[f.dst], position[f.src], f.id))
    return Plan.from_order(messages, [f.id for f in ordered])


def normalized_config(path):
    # Only run-local filenames may differ. All numerical transport settings
    # must be identical; this is intentionally stricter than checking defaults.
    paths = {'FLOW_FILE', 'TRACE_FILE', 'TRACE_OUTPUT_FILE', 'FCT_OUTPUT_FILE',
             'PFC_OUTPUT_FILE', 'QLEN_MON_FILE', 'BW_MON_FILE', 'RATE_MON_FILE',
             'CNP_MON_FILE'}
    return tuple(line.split()[0] if line.split() and line.split()[0] in paths
                 else line.strip() for line in Path(path).read_text().splitlines())


def check_inputs(plan, network, reference):
    with tempfile.TemporaryDirectory(prefix='xor-input-check-') as folder:
        prepare(plan, network, folder)
        for name in ('topology.txt', 'routes.csv', 'workload.txt'):
            if (Path(folder) / name).read_bytes() != (reference / name).read_bytes():
                raise ValueError('paired input mismatch: ' + name)
        if normalized_config(Path(folder) / 'ns3.conf') != normalized_config(reference / 'ns3.conf'):
            raise ValueError('paired transport configuration mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-table', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binary', default='/home/julius/fast/SimAI/bin/SimAI_simulator')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('choose a fresh output directory; completed and failed runs are never overwritten')
    with args.source_table.open() as f:
        source_rows = list(csv.DictReader(f))
    cohort = [r for r in source_rows if r['cohort'] == 'held-out' and r['method'] == 'spreadout']
    if [int(r['seed']) for r in cohort] != [83, 97, 109, 131, 149]:
        parser.error('requires the complete frozen five-seed cohort in source order')
    args.output.mkdir(parents=True)
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    manifest = dict(type='simulation', created_unix=time.time(),
                    source_table=str(args.source_table.resolve()),
                    source_table_sha256=digest(args.source_table), seeds=[83,97,109,131,149],
                    methods=['pairwise-xor', 'pairwise-xor-server'],
                    topology='tree', profile='heterogeneous', gpus=128,
                    load='existing frozen 4 MiB/rank independently permuted Zipf 1.2',
                    failure_policy='retain failures without automatic retry; report all ten cells',
                    per_run_timeout_s=120, binary=str(Path(args.binary).resolve()),
                    sources={p.name:digest(p) for p in Path(__file__).parent.glob('*.py')})
    (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    network = network_for('tree', '128', 'heterogeneous')
    rows = []
    for source in cohort:
        reference = Path(source['path']).resolve(strict=True)
        raw = json.loads((reference/'plan.json').read_text())
        messages = tuple(Message(**f) for f in raw['messages'])
        old = json.loads((reference/'result.json').read_text())
        if old['status'] != 'success':
            raise ValueError('reference is not a completed valid run')
        for method in manifest['methods']:
            started = time.perf_counter()
            ranks = interleaved_ranks(network, across_racks=False) if method.endswith('server') else None
            plan = pairwise_xor(messages, network.gpu_count, ranks)
            planning_s = time.perf_counter() - started
            check_inputs(plan, network, reference)
            dest = args.output/f"s{source['seed']}-{method}"
            print(json.dumps(dict(event='start', seed=source['seed'], method=method)), flush=True)
            result = run(plan, network, dest, args.binary, timeout_s=120,
                         send_latency_ns=old['send_latency_ns'])
            comparable = result['backend_sha256'] == old['backend_sha256']
            row = dict(seed=int(source['seed']), method=method, status=result['status'],
                       backend_matches_reference=comparable,
                       delivery_ns=result.get('delivery_ns'), planning_s=planning_s,
                       wall_s=result['wall_s'], path=str(dest.resolve()), reference=str(reference))
            if not comparable:
                row['status'] = 'invalid_backend_mismatch'
            rows.append(row)
            (args.output/'results.json').write_text(json.dumps(rows, indent=2)+'\n')
            print(json.dumps(row), flush=True)
    return int(any(r['status'] != 'success' for r in rows))


if __name__ == '__main__':
    raise SystemExit(main())
