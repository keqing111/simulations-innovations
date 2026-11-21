"""Portable planning-only example; never runs ns-3 or reads historical results."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from resource_order.cover import initial_cover,cover_plan,minimize_joint
from resource_order.experiments import demand
from resource_order.model import Message
from resource_order.refinement import minimize_leximax
from resource_order.scheduler import interleaved_ranks
from resource_order.topology import build_topology

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--messages',type=Path,help='JSON list of {id,src,dst,bytes}; otherwise generate a small example')
    p.add_argument('--topology',choices=['tree','leaf-spine'],default='tree')
    p.add_argument('--racks',type=int,default=2)
    p.add_argument('--servers-per-rack',type=int,default=1)
    p.add_argument('--gpus-per-server',type=int,default=4)
    p.add_argument('--kib',type=int,default=64)
    p.add_argument('--seed',type=int,default=7)
    args=p.parse_args()
    if args.output.exists():p.error('output exists; choose a new file')
    network=build_topology(args.topology,racks=args.racks,servers_per_rack=args.servers_per_rack,
                           gpus_per_server=args.gpus_per_server)
    flows=(tuple(Message(**item) for item in json.loads(args.messages.read_text())) if args.messages
           else demand(network.gpu_count,args.kib*1024,'zipf',args.seed))
    if any(max(f.src,f.dst)>=network.gpu_count for f in flows):p.error('message endpoint outside topology')
    ranks=interleaved_ranks(network,across_racks=False)
    first_lib=ROOT/'build/dependency_partial.so';second_lib=ROOT/'build/dependency_prefix.so'
    first,h1=minimize_joint(flows,initial_cover(ranks),network,cache_pairs=True,
                           native_bound=first_lib if first_lib.exists() else None)
    final,h2=minimize_leximax(flows,first,network,native_bound=second_lib if second_lib.exists() else None,
                             critical_pruning=False)
    plan=cover_plan(flows,final,ranks)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(dict(messages=[vars(f) for f in flows],order=plan.order,cover=final,rank_order=ranks,
                       phase1_scans=len(h1),phase2_scans=len(h2),network_simulated=False),stream,indent=2)
        stream.write('\n')
    print(f'Plan written: {args.output}; messages={len(flows)}; scans={len(h1)}+{len(h2)}. No network simulation.')

if __name__=='__main__':main()
