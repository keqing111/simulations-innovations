"""Real native CLI tests: deleted policies, delay-blind matching and lost bytes fail."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

# ============================================================================
# independent/tests/test_native.py：通过真实 CLI 验证策略与原生引擎的接线。
#
# 这些测试不是纯单元测试：它们编译/调用 native_independent，读取 JSON，
# 检查机制输入的具体数值、payload 守恒、冻结基线逐项一致、非法输入拒绝，
# 以及“护栏拦截后第一阶段必须保持原生选择”。测试依赖冻结引擎归档，
# 而不是当前工作树里正在批注的源码。
#
# 注意：机制测试中的 35014/30012.6/81014 等是论文链路档案下的确定值，
# 一旦链路参数或计价公式改变，应显式更新断言而不是放宽容差。
# ============================================================================

ROOT = Path(__file__).resolve().parents[1]
# 冻结的参考运行器：用于对照 native/maxmin/spreadout 的 stage 与总时间。
FROZEN = ROOT.parent / 'planning/results/final-96b18da3da36/native_scan'


class NativeTests(unittest.TestCase):
    def binary(self):
        # 每个测试按需调用 build.py；构建身份相同会命中缓存，避免重复编译。
        result = subprocess.run([sys.executable, str(ROOT / 'build.py')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return ROOT / 'build/native_independent'

    def invoke(self, method, data, n=6, g=1, gateway=400, wan=100, latency=5000, dc=None):
        # 默认拓扑：6 server、3 DC 均分（0,0,1,1,2,2）、WAN 单向 5000us。
        # 矩阵通过 stdin 以空格分隔传入；REPEATS=1 使输出只含确定语义。
        dc = dc if dc is not None else [s * 3 // n for s in range(n)]
        command = [str(self.binary()), str(n), str(g), method, str(latency), '400',
                   str(gateway), str(wan), ','.join(map(str, dc)), '1']
        done = subprocess.run(command, input=' '.join(map(str, data)), capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_delay_only_mechanism_is_generated_not_hand_inserted(self):
        # 时延机制：非对角 100MB、每 DC 2 台、WAN 5000us、网关/WAN 800Gbps
        # 容量充足。原生 5 个跨 DC stage，暴露 5 次传播；time 策略应把纯 DC 内
        # 阶段也排进来，使 WAN stage 降为 4、总时间 30.0126ms。
        # 每个 stage serial 都是 2000us（100MB/400Gbps 的 server 资源瓶颈），
        # 因此改善只能来自传播重复暴露减少，而不是串行化变化。
        # bandwidth 策略只看 serial，容量充足时无可改善，应保持原生。
        data = [0 if i == j else 100000000 for i in range(6) for j in range(6)]
        native = self.invoke('native', data, gateway=800, wan=800)
        time = self.invoke('time', data, gateway=800, wan=800)
        bandwidth = self.invoke('bandwidth', data, gateway=800, wan=800)
        self.assertAlmostEqual(native['total_us'], 35014)
        self.assertAlmostEqual(time['total_us'], 30012.6)
        self.assertEqual(len(time['stages']), 5)
        self.assertEqual(sum(any(k.startswith('wan:') for k in s['links']) for s in time['stages']), 4)
        self.assertTrue(all(s['serial_us'] == 2000 for s in time['stages']))
        self.assertAlmostEqual(bandwidth['total_us'], native['total_us'])

    def test_bandwidth_mechanism_reduces_serialization(self):
        # 带宽机制：默认 400/400/100Gbps，WAN 是共享瓶颈。原生累计 serial
        # 56000us；bandwidth 策略应严格降低累计 serial 和总时间，且至少接受
        # 一个候选（accepted>0），否则说明策略没有真正执行。
        data = [0 if i == j else 100000000 for i in range(6) for j in range(6)]
        native = self.invoke('native', data)
        bandwidth = self.invoke('bandwidth', data)
        self.assertAlmostEqual(native['total_us'], 81014)
        self.assertEqual(sum(s['serial_us'] for s in native['stages']), 56000)
        self.assertLess(sum(s['serial_us'] for s in bandwidth['stages']), 56000)
        self.assertLess(bandwidth['total_us'], native['total_us'])
        self.assertGreater(bandwidth['stats']['accepted'], 0)

    def test_frozen_baselines_and_actual_server_pair_conservation(self):
        # 对七种方法统一检查：
        #   1. native 的 payload_verified 必须为真；
        #   2. stage 数不超过 N²（spreadout 为 r）；
        #   3. 从逐 stage payload 重建 server 对交付量，必须等于原始矩阵块和；
        #   4. native/maxmin/spreadout 的完整 stages 与冻结参考运行器逐项一致，
        #      保证新实现的“未改动路径”没有发生语义漂移。
        n, g = 6, 2
        r = n * g
        data = [0 if i == j else ((i * 47 + j * 31) % 17) * 10000 for i in range(r) for j in range(r)]
        for method in ['native', 'bandwidth', 'time', 'maxmin', 'maxmin-bandwidth', 'maxmin-time', 'spreadout']:
            with self.subTest(method=method):
                raw = self.invoke(method, data, n=n, g=g)
                self.assertTrue(raw['payload_verified'])
                self.assertLessEqual(len(raw['stages']), r if method == 'spreadout' else n * n)
                delivered = [[0] * n for _ in range(n)]
                for stage in raw['stages']:
                    size = r if method == 'spreadout' else n
                    self.assertEqual(sorted(stage['map']), list(range(size)))
                    for s, d in enumerate(stage['map']):
                        # spreadout 的 map/payload 是 GPU 级，需换算成 server 级；
                        # Birkhoff 方法则 payload 按 server 分块，每块 g 个 GPU。
                        src, dst = (s // g, d // g) if method == 'spreadout' else (s, d)
                        amount = stage['payload'][s] if method == 'spreadout' else sum(stage['payload'][s*g:(s+1)*g])
                        if src != dst:
                            delivered[src][dst] += amount
                for s in range(n):
                    for d in range(n):
                        if s != d:
                            self.assertEqual(delivered[s][d], sum(data[(s*g+i)*r+d*g+j] for i in range(g) for j in range(g)))
                if method in ['native', 'maxmin', 'spreadout']:
                    # 冻结运行器的历史 CLI 形状与当前独立二进制不同：
                    # 它接收旧方法名（native 对应 F000-opt）和旧参数顺序。
                    oldmethod = 'F000-opt' if method == 'native' else method + '-opt'
                    cmd = [str(FROZEN), str(n), str(g), oldmethod, '5000', '1', '0,0,1,1,2,2', '400', '400', '100', 'uniform']
                    old = json.loads(subprocess.check_output(cmd, input=' '.join(map(str, data)).encode()))
                    self.assertEqual(raw['stages'], old['stages'])
                    self.assertAlmostEqual(raw['total_us'], old['total_us'])

    def test_empty_intra_only_and_unimprovable_uniform_paths(self):
        # 边界输入：
        #   * 全零矩阵：无跨 server 需求，总时间必须为 0；
        #   * 只有一个同 server 块：只产生机内 0.1us alpha + 8000/3600us
        #     (1,000,000 bytes @ 3600Gbps)，不得被算成网络流；
        #   * 所有 server 在同一 DC：没有 WAN 路径差异，两种策略都应保持
        #     原生 stage 不变（接受候选没有意义）。
        for method in ['native', 'bandwidth', 'time', 'maxmin-bandwidth', 'maxmin-time']:
            self.assertEqual(self.invoke(method, [0] * 9, n=3)['total_us'], 0)
            data = [0] * 36
            data[1] = 1000000
            self.assertAlmostEqual(self.invoke(method, data, n=3, g=2)['total_us'], .1 + 8000/3600)
        data = [0 if i == j else 100000 for i in range(6) for j in range(6)]
        base = self.invoke('native', data, dc=[0]*6)
        for method in ['bandwidth', 'time']:
            self.assertEqual(self.invoke(method, data, dc=[0]*6)['stages'], base['stages'])

    def test_invalid_input_rejected_before_native_code(self):
        # 非法输入必须在进入原生代码前被 CLI 拒绝：
        # 未知方法、非有限/负/零参数、非零对角线、矩阵截断、超过 signed 上限。
        binary = self.binary()
        for method, latency, data in [('unknown', '5000', [0]*9), ('time', 'nan', [0]*9),
                                      ('time', '5000', [1]*9), ('time', '5000', [-1]*9),
                                      ('time', '5000', [0]*8), ('time', '5000', [2**31]*9)]:
            done = subprocess.run([str(binary), '3', '1', method, latency, '400', '800', '800', '0,1,2', '1'],
                                  input=' '.join(map(str, data)), text=True, capture_output=True)
            self.assertNotEqual(done.returncode, 0)

    def test_finite_parameters_cannot_emit_infinite_json(self):
        # 1e308 是有限浮点，但与字节数相乘后可溢出成 inf；程序应报错退出，
        # 而不是以成功状态输出非法 JSON（allow_nan=False 会拒绝）。
        data = [0 if i == j else 100000000 for i in range(6) for j in range(6)]
        done = subprocess.run([str(self.binary()), '6', '1', 'time', '1e308', '400', '800', '800',
                               '0,0,1,1,2,2', '1'], input=' '.join(map(str, data)), text=True, capture_output=True)
        self.assertNotEqual(done.returncode, 0, 'finite inputs overflowed CCT but produced invalid JSON with success status')

    def test_coefficient_and_volume_guards_keep_native_first_stage(self):
        # 两个手工构造的回归输入，分别触发系数护栏和真实服务量护栏。
        # 触发后策略必须回退种子匹配，因此第一阶段应与原生完全一致；
        # 同时对应计数器必须大于 0，证明“没有改善”不是因为没有尝试候选。
        fixtures = [
            ([0,1000000,2000000,1000000,1000000,0,1000000,2000000,
              2000000,1000000,0,1000000,1000000,2000000,1000000,0], [0,1,0,1], 'coefficient_rejections'),
            ([0,5000000,1000000,0,1000000,0,0,5000000,
              0,1000000,0,2000000,0,1000000,0,0], [0,0,1,1], 'volume_rejections')]
        for data, dc, counter in fixtures:
            with self.subTest(counter=counter):
                native = self.invoke('native', data, n=4, gateway=800, wan=800, dc=dc)
                time = self.invoke('time', data, n=4, gateway=800, wan=800, dc=dc)
                self.assertGreater(time['stats'][counter], 0)
                self.assertEqual(time['stages'][0], native['stages'][0])

    def test_bottleneck_assignment_matches_exhaustive_small_graphs(self):
        # 单独编译 tests/policy_test.cc 并运行；链接的是冻结引擎源码与
        # build.py 已生成的 config/profile 对象，不触碰当前工作树引擎。
        self.binary()
        engine = ROOT.parent/'planning/results/final-96b18da3da36/source'
        includes = ['-I'+str(ROOT), *['-I'+str(engine/p) for p in ['', 'algorithm', 'scheduler', 'topology', 'scan', 'reproduction']]]
        with tempfile.TemporaryDirectory(prefix='fast-independent-unit-') as tmp:
            binary = Path(tmp)/'policy_test'
            command = ['g++', '-std=c++11', '-O2', '-DNOLOG', '-include', 'cstring', *includes,
                       str(ROOT/'tests/policy_test.cc'), str(ROOT/'policy.cpp'),
                       *[str(engine/p) for p in ['algorithm/matrix.cpp', 'algorithm/all2all.cpp',
                         'topology/model.cpp', 'topology/matching.cpp', 'scan/planner.cpp']],
                       str(ROOT/'build/config.o'), str(ROOT/'build/profile.o'), '-lpthread', '-o', str(binary)]
            subprocess.run(command, check=True, capture_output=True, text=True)
            done = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stdout+done.stderr)


if __name__ == '__main__':
    unittest.main()
