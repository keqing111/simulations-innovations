"""Exercise the real merged executable; optional historical data are test-only."""
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'build/all2all_simulator'


def invoke(binary, data, n=6, g=1, mode='native', wan=100, gateway=400,
           latency=5000, mbpu=1e-6, dc=None):
    dc = dc if dc is not None else [i * 3 // n for i in range(n)]
    return subprocess.run([str(binary), '--topology', str(n), str(g), mode,
                           str(latency), '400', str(gateway), str(wan), str(mbpu),
                           ','.join(map(str, dc))], input=' '.join(map(str, data)),
                          capture_output=True, text=True, timeout=30)


class ScheduleCLI(unittest.TestCase):
    def assert_current_model(self, raw, data, case, reference):
        """Replay Local independently and reconstruct the unchanged resource/pipeline model."""
        n, g, dc = case['n'], case['g'], case['dc']
        sent = defaultdict(int)
        network = []
        for index, stage in enumerate(raw['stages']):
            self.assertEqual(sorted(stage['map']), list(range(n)))
            self.assertEqual(stage['payload'], reference['payload'][index])
            loads, bandwidths = defaultdict(int), {}
            alpha = 0
            for src, dst in enumerate(stage['map']):
                for u in range(g):
                    amount = stage['payload'][src*g+u]
                    self.assertGreaterEqual(amount, 0)
                    self.assertLessEqual(amount, stage['freq'])
                    if src == dst:
                        self.assertEqual(amount, 0)
                        continue
                    sent[src, dst] += amount
                    if not amount:
                        continue
                    route = [(('out', src), 400), (('in', dst), 400)]
                    delay = 1.4
                    if dc[src] != dc[dst]:
                        route += [(('gateway-out', dc[src]), case['gateway_bw']),
                                  (('gateway-in', dc[dst]), case['gateway_bw']),
                                  (('wan', dc[src], dc[dst]), case['wan_bw'])]
                        delay = case['wan_us'] + 2.8
                    alpha = max(alpha, delay)
                    for resource, bandwidth in route:
                        loads[resource] += amount
                        bandwidths[resource] = bandwidth
            serial = max((.008*amount/bandwidths[r] for r, amount in loads.items()), default=0)
            for key, want in [('alpha_us', alpha), ('serial_us', serial),
                              ('communication_us', alpha+serial)]:
                self.assertTrue(math.isclose(stage[key], want, rel_tol=1e-12, abs_tol=1e-9),
                                (key, stage[key], want))
            network.append(alpha+serial)
        ranks = n*g
        for src in range(n):
            for dst in range(n):
                if src != dst:
                    want = sum(data[(src*g+u)*ranks+dst*g+v] for u in range(g) for v in range(g))
                    self.assertEqual(sent[src, dst], want)
        self.assertEqual(raw['dispatch_us'], reference['dispatch_us'])
        self.assertEqual(raw['balance_us'], reference['balance_us'])
        self.assertEqual(raw['intrinsic_us'], reference['intrinsic_us'])
        total = reference['balance_us'] + max(network[0] if network else 0,
                                               reference['intrinsic_us'])
        total += sum(max(reference['dispatch_us'][i-1], network[i]) for i in range(1, len(network)))
        if network:
            total += reference['dispatch_us'][-1]
        self.assertTrue(math.isclose(raw['total_us'], total, rel_tol=1e-12, abs_tol=1e-9))

    def test_mechanisms_and_actual_payload(self):
        data = [0 if i == j else 100000000 for i in range(6) for j in range(6)]
        for mode, gateway, wan, want in [('native', 400, 100, 81014),
                                       ('bandwidth', 400, 100, 54012.6),
                                       ('time', 800, 800, 30012.6)]:
            with self.subTest(mode=mode):
                done = invoke(BINARY, data, mode=mode, gateway=gateway, wan=wan)
                self.assertEqual(done.returncode, 0, done.stderr)
                raw = json.loads(done.stdout)
                self.assertAlmostEqual(raw['total_us'], want)
                self.assertEqual(sum(sum(s['payload']) for s in raw['stages']), 3000000000)

    def test_invalid_cli_rejected(self):
        data = [0] * 36
        for opts in [dict(mode='unknown'), dict(latency='nan'), dict(wan=0),
                     dict(mbpu=0), dict(dc=[0]), dict(n=0), dict(n=65)]:
            self.assertNotEqual(invoke(BINARY, data, **opts).returncode, 0)
        self.assertNotEqual(invoke(BINARY, data[:-1]).returncode, 0)
        data[0] = 1
        self.assertNotEqual(invoke(BINARY, data).returncode, 0)

    def test_historical_26_cases_corrected_contract(self):
        archive = ROOT / 'independent/results/run-2c90c8145c7d-serial'
        if not (archive / 'study.json').exists():
            self.skipTest('optional historical data not installed; core tests do not require it')
        study = json.loads((archive / 'study.json').read_text())
        # Match the archived paper link profile only in this test binary.
        # No archived scheduler or Planner is compiled into it.
        with tempfile.TemporaryDirectory(prefix='fast-merge-paper-') as tmp:
            tmp = Path(tmp)
            flags = ['g++', '-std=c++11', '-O2', '-DNOLOG', '-include', 'cstring',
                     '-I'+str(ROOT), '-I'+str(ROOT/'algorithm'), '-I'+str(ROOT/'scheduler')]
            subprocess.run(flags + ['-Dget_inter_link_info=upstream_get_inter_link_info',
                                    '-Dget_intra_link_info=upstream_get_intra_link_info',
                                    '-c', str(ROOT/'scheduler/config.cpp'), '-o', str(tmp/'config.o')],
                           check=True, capture_output=True)
            shim = '#include "config.h"\nlink_info_t get_inter_link_info(INTER_LINK_TYPE){link_info_t r={};r.alpha=.7;r.beta=20;return r;}\nlink_info_t get_intra_link_info(INTRA_LINK_TYPE){link_info_t r={};r.alpha=.1;r.beta=8000./3600;return r;}\n'
            subprocess.run(flags + ['-c', '-x', 'c++', '-', '-o', str(tmp/'profile.o')],
                           input=shim, text=True, check=True, capture_output=True)
            sources = ['main.cpp', 'test.cpp', 'algorithm/all2all.cpp', 'algorithm/matrix.cpp',
                       'scheduler/local.cpp', 'scheduler/global.cpp', 'topology/model.cpp']
            binary = tmp/'native'
            subprocess.run(flags + [str(ROOT/p) for p in sources] +
                           [str(tmp/'config.o'), str(tmp/'profile.o'), '-lpthread', '-o', str(binary)],
                           check=True, capture_output=True)
            # Only unchanged Local helpers are reused here: no GlobalScheduler or
            # corrected dispatch function is called by this independent replay.
            replay_source = tmp/'local_replay.cpp'
            replay_source.write_text(r'''
#include "local.h"
#include <algorithm>
#include <iomanip>
#include <iostream>
#include <memory>
#include <vector>
int main() {
    unsigned n,g,k;std::cin>>n>>g>>k;
    std::vector<unsigned> data(n*n*g*g);for(auto& x:data)std::cin>>x;
    std::vector<std::unique_ptr<LocalScheduler>> locals;
    std::vector<bool> approx(n*n);
    std::vector<std::vector<unsigned>> balance(n*n,std::vector<unsigned>(g*g,0));
    for(unsigned s=0;s<n;++s) {
        locals.emplace_back(new LocalScheduler(data.data()+s*g*n*g,g,n,s,H100));
        locals[s]->prepare_load_balance();
        for(unsigned d=0;d<n;++d) {
            approx[s*n+d]=locals[s]->check_balance_dispatch(d,1e-6);
            if(approx[s*n+d])locals[s]->server2server_approx(d);
        }
    }
    double balance_us=0,intrinsic_us=0;
    for(unsigned s=0;s<n;++s) {
        double cost=0;
        for(unsigned d=0;d<n;++d)if(s!=d) {
            load_balance_result result={balance[s*n+d].data(),nullptr};
            if(!approx[s*n+d])locals[s]->server2server_balance(d,result);
            cost+=spread_out(result.balance,g,locals[s]->get_link_info(),1e-6);
        }
        balance_us=std::max(balance_us,cost);
        intrinsic_us=std::max(intrinsic_us,spread_out(locals[s]->get_intrinsic_all2all(),g,locals[s]->get_link_info(),1e-6));
    }
    std::cout<<std::setprecision(17)<<"{\"balance_us\":"<<balance_us
             <<",\"intrinsic_us\":"<<intrinsic_us<<",\"payload\":[";
    std::vector<double> dispatch;
    for(unsigned stage=0;stage<k;++stage) {
        unsigned freq;std::cin>>freq;std::vector<unsigned> dst(n);for(auto& x:dst)std::cin>>x;
        if(stage)std::cout<<',';std::cout<<'[';double maximum=0;
        for(unsigned s=0;s<n;++s) {
            std::vector<unsigned> moves(g*g,0);std::vector<uint64_t> before(g,0),payload(g,0);
            if(s!=dst[s]) {
                for(unsigned u=0;u<g;++u)before[u]=locals[s]->pending_units(dst[s],u);
                load_balance_result result={nullptr,moves.data()};
                locals[s]->server2server_dispatch(dst[s],result,freq);
                for(unsigned u=0;u<g;++u)payload[u]=before[u]-locals[s]->pending_units(dst[s],u);
            }
            maximum=std::max(maximum,spread_out(moves.data(),g,locals[s]->get_link_info(),1e-6));
            for(unsigned u=0;u<g;++u){if(s||u)std::cout<<',';std::cout<<payload[u];}
        }
        std::cout<<']';dispatch.push_back(maximum);
    }
    std::cout<<"],\"dispatch_us\":[";
    for(unsigned i=0;i<dispatch.size();++i){if(i)std::cout<<',';std::cout<<dispatch[i];}
    std::cout<<"]}\n";
}
''')
            replay = tmp/'local_replay'
            subprocess.run(flags+[str(replay_source), str(ROOT/'scheduler/local.cpp'),
                                   str(tmp/'config.o'), str(tmp/'profile.o'), '-o', str(replay)],
                           check=True, capture_output=True)
            contaminated, changed_time = 0, 0
            for case in study['cases']:
                expected = json.loads((archive/'raw'/f"{case['id']}.json").read_text())
                key = f"n{case['n']}g{case['g']}-{case['kind']}-s{case['seed']}-m{case['mib']}"
                matrix_path = archive / 'matrices' / (key + '.json')
                data = json.loads(matrix_path.read_text())
                for mode in ['native', 'bandwidth', 'time']:
                    with self.subTest(case=case['id'], mode=mode):
                        done = invoke(binary, data, case['n'], case['g'], mode,
                                      case['wan_bw'], case['gateway_bw'], case['wan_us'], dc=case['dc'])
                        self.assertEqual(done.returncode, 0, done.stderr)
                        raw = json.loads(done.stdout)
                        old = expected[mode]
                        replay_input = [case['n'], case['g'], len(raw['stages'])] + data
                        for stage in raw['stages']:
                            replay_input += [stage['freq']] + stage['map']
                        result = subprocess.run([str(replay)], input=' '.join(map(str, replay_input)),
                                                capture_output=True, text=True, check=True)
                        reference = json.loads(result.stdout)
                        self.assert_current_model(raw, data, case, reference)
                        for key in ['balance_us', 'intrinsic_us']:
                            self.assertTrue(math.isclose(raw[key], old[key], rel_tol=1e-12, abs_tol=1e-9), (key, raw[key], old[key]))
                        if mode == 'time':
                            # Changed acceptance is tested against physical replay, not
                            # obsolete per-edge-proxy plans. No archived file is rewritten.
                            changed_time += [(s['freq'], s['map']) for s in raw['stages']] != [(s['freq'], s['map']) for s in old['stages']]
                            continue
                        self.assertEqual(len(raw['stages']), len(old['stages']))
                        for index, (actual, prior) in enumerate(zip(raw['stages'], old['stages'])):
                            for key in ['freq', 'map']:
                                self.assertEqual(actual[key], prior[key])
                            cross_payload = prior['payload'][:]
                            dirty = False
                            for src, dst in enumerate(prior['map']):
                                if src == dst:
                                    for u in range(case['g']):
                                        dirty |= cross_payload[src*case['g']+u] > 0
                                        cross_payload[src*case['g']+u] = 0
                            self.assertEqual(actual['payload'], cross_payload)
                            if dirty:
                                contaminated += 1
                                self.assertLessEqual(raw['dispatch_us'][index], old['dispatch_us'][index])
                            else:
                                self.assertEqual(raw['dispatch_us'][index], old['dispatch_us'][index])
                            for key in ['alpha_us', 'serial_us', 'communication_us']:
                                self.assertTrue(math.isclose(actual[key], prior[key], rel_tol=1e-12, abs_tol=1e-9), (key, actual[key], prior[key]))
            print(f'archive contract: 52 strict native/bandwidth plan+network comparisons; '
                  f'26 corrected time replays ({changed_time} changed plans); '
                  f'{contaminated} intrinsic-contaminated dispatch slots independently replayed')


if __name__ == '__main__':
    unittest.main()
