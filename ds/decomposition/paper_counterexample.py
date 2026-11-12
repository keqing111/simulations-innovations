#!/usr/bin/env python3
"""A fixed six-server witness: native FAST replay and an analytic resource bound."""
import argparse
import hashlib
import json
import math
import pathlib
import subprocess

import dynamic_small_cases as d
import study

DEMAND = [0,1,0,0,1,0,
          1,0,0,1,0,0,
          0,1,0,0,1,0,
          1,0,0,0,0,1,
          0,0,1,0,0,1,
          0,0,1,1,0,0]
NATIVE = [d.stage([1,0,4,5,2,3]), d.stage([4,3,1,0,5,2])]
ALTERNATIVE = [d.stage([4,0,1,5,2,3]), d.stage([1,3,4,0,5,2])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=pathlib.Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    sources = ['simulation/algorithm/all2all.cpp', 'simulation/scheduler/global.cpp',
               'simulation/topology/model.cpp', 'simulation/bvn_decompose_main.cpp',
               'ds/decomposition/paper_counterexample.py', 'ds/decomposition/dynamic_small_cases.py']
    hashes = {s: hashlib.sha256((study.ROOT/s).read_bytes()).hexdigest() for s in sources}
    meta = dict(complete=False, sources=hashes,
                binary_sha256=hashlib.sha256(study.BIN.read_bytes()).hexdigest(),
                scope='fixed witness, not a random-performance sample or online optimizer',
                discovery='bounded n6 degree2 support search, RNG seed121, first qualifying witness at attempt18 (zero-based)')
    d.save(args.out/'metadata.json', meta)
    assert d.matrix_from_plan(6, NATIVE) == DEMAND == d.matrix_from_plan(6, ALTERNATIVE)
    assert all(sum(DEMAND[s*6:s*6+6]) == 2 for s in range(6))
    assert all(sum(DEMAND[s*6+t] for s in range(6)) == 2 for t in range(6))
    topology = d.physical_topology([0,0,1,1,2,2], 'equal')
    for path in topology['paths']:
        if topology['dc'][path['src']] != topology['dc'][path['dst']]:
            path['propagation_us'], path['rtt_us'] = 52.8, 105.6
    d.save(args.out/'fixture.json', dict(demand=DEMAND, native=NATIVE, alternative=ALTERNATIVE,
                                       topology=topology, unit_bytes=1000000))
    results = []
    for g, native_total, optimal_total in [(1,425.6,345.6), (4,1385.6,1065.6)]:
        old = d.evaluate_plan(DEMAND, 6, NATIVE, topology, 1000000*g)
        new = d.evaluate_plan(DEMAND, 6, ALTERNATIVE, topology, 1000000*g)
        assert old['resource_total_bytes'] == new['resource_total_bytes']
        assert old['propagation_sum_us'] == new['propagation_sum_us'] == 105.6
        assert math.isclose(old['network_sum_us'], native_total)
        assert math.isclose(new['network_sum_us'], optimal_total)
        wan21 = next(r['id'] for r in topology['resources'] if r['name']=='wan:2:1')
        assert new['resource_total_bytes'][wan21] == 3000000*g
        # Every row has two unit edges; row2 has only WAN destinations.
        # Thus every complete integer BvN decomposition has exactly 2 WAN stages.
        lower_bound = 2*52.8 + .008*3000000*g/100
        assert math.isclose(new['network_sum_us'], lower_bound)
        d.save(args.out/f'resource-replay-g{g}.json', dict(native=old, alternative=new,
                                                        model_lower_bound_us=lower_bound))
        ranks=6*g
        data=[DEMAND[(s//g)*6+t//g] if s%g==t%g else 0
              for s in range(ranks) for t in range(ranks)]
        d.save(args.out/f'gpu-input-g{g}.json', data)
        for scaling in (0,1):
            command=[str(study.BIN), '--mode','pipeline','--n','6','--g',str(g),
                     '--strategy','0','--scaling',str(scaling),'--topology','1',
                     '--unit-mb','1','--repeat','3']
            raw=json.loads(subprocess.run(command,input=' '.join(map(str,data)),
                                          text=True,capture_output=True,check=True,timeout=30).stdout)
            study.audit_pipeline(data,6,g,raw)
            assert raw['permutations']==[x['permutation'] for x in NATIVE]
            assert [s['alpha'] for s in raw['steps']]==[1,1]
            assert math.isclose(raw['total_us'],native_total)
            assert raw['balance_us']==raw['intrinsic_us']==0
            assert raw['dispatch_us']==[0,0]
            d.save(args.out/f'native-pipeline-g{g}-scaling{scaling}.json',raw)
            results.append(dict(g=g,scaling=scaling,native_us=native_total,
                                alternative_us=optimal_total,lower_bound_us=lower_bound))
    assert all(hashlib.sha256((study.ROOT/s).read_bytes()).hexdigest()==digest for s,digest in hashes.items())
    assert hashlib.sha256(study.BIN.read_bytes()).hexdigest()==meta['binary_sha256']
    d.save(args.out/'summary.json',results)
    meta.update(complete=True, checks='perfect matchings, actual min, exact reconstruction, resource conservation, native pipeline parity, analytic bound')
    d.save(args.out/'metadata.json',meta)
    print(json.dumps(results,indent=2))


if __name__=='__main__':
    main()
