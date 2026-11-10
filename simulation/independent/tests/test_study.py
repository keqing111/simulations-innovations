"""Exercise full native study/audit; wrong routed bytes and fake CCT must fail."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

# ============================================================================
# independent/tests/test_study.py：study.py 的防篡改/数据域测试。
#
# 两个测试都走真实 smoke 流程（两个机制输入 × 七种方法 = 14 行）：
#   1. 即使 --no-replay（不重跑原生二进制），也必须拒绝不可能的机内时间
#      （负数、非有限），因为 reconstruct 会检查本地时间的数据域；
#   2. 同时篡改数值和 study.json 中的外层摘要，仍必须被独立重建发现；
#      links 里的资源字节 +1 也必须被发现；解压出的冻结依赖被改也必须被发现。
# 这些测试验证“审计不依赖攻击者是否会正确更新摘要”。
# ============================================================================

ROOT = Path(__file__).resolve().parents[1]


class StudyTests(unittest.TestCase):
    def test_impossible_local_costs_rejected_without_native_replay(self):
        # 先跑一次 smoke（包含真实二进制、审计与 comparison 输出）。
        with tempfile.TemporaryDirectory(prefix='fast-independent-domain-') as tmp:
            out = Path(tmp)/'run'
            def call(phase):
                return subprocess.run([sys.executable, str(ROOT/'study.py'), '--phase', phase,
                                       '--output', str(out), '--smoke', '--no-replay'], text=True, capture_output=True)
            done = call('run')
            self.assertEqual(done.returncode, 0, done.stdout+done.stderr)
            index = out/'study.json'
            record = json.loads(index.read_text())
            cid = record['cases'][0]['id']
            path = out/'raw'/f'{cid}.json'
            original = json.loads(path.read_text())
            import copy
            # raw 是 batch 输出，顶层以方法名为键；raw['time'] 是“time 策略”
            # 的结果字典（不是嵌套的时间字段），下面直接篡改该方法的机内
            # balance/intrinsic/dispatch 和总时间。
            for field, value, delta in [('balance_us', -1000, -1000), ('intrinsic_us', -1, 0),
                                         ('dispatch_us', -1, 0), ('dispatch_us', float('nan'), 0)]:
                with self.subTest(field=field, value=value):
                    raw = copy.deepcopy(original)
                    if field == 'dispatch_us':
                        raw['time'][field][0] = value  # Hidden by next inter-stage time, not caught by CCT alone.
                    else:
                        raw['time'][field] = value
                    raw['time']['total_us'] += delta
                    path.write_text(json.dumps(raw))
                    # 攻击者同步更新外层摘要，模拟“自洽伪造”；审计仍应
                    # 通过 reconstruct 的数据域检查发现异常。
                    record['records'][cid] = hashlib.sha256(path.read_bytes()).hexdigest()
                    index.write_text(json.dumps(record))
                    result = call('audit')
                    self.assertNotEqual(result.returncode, 0, 'impossible local cost accepted by no-replay audit')
                    self.assertIn('local time', result.stderr)

    def test_real_smoke_and_self_consistent_raw_forgery_rejected(self):
        # 真实 smoke：14 行结果，并抽查时延机制的确定总时间 30.0126ms。
        with tempfile.TemporaryDirectory(prefix='fast-independent-test-') as tmp:
            out = Path(tmp) / 'run'
            def call(phase, *extra):
                return subprocess.run([sys.executable, str(ROOT/'study.py'), '--phase', phase,
                                       '--output', str(out), '--smoke', *extra], text=True, capture_output=True)
            done = call('run')
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            rows = json.loads((out/'comparison.json').read_text())
            self.assertEqual(len(rows), 14)
            time = next(r for r in rows if r['group'] == 'delay-mechanism' and r['method'] == 'time')
            self.assertAlmostEqual(time['total_ms'], 30.0126)
            self.assertEqual(call('audit').returncode, 0)
            index = out/'study.json'
            original_index = index.read_bytes()
            record = json.loads(original_index)
            case_id = record['cases'][0]['id']
            path = out/'raw'/f'{case_id}.json'
            original_raw = path.read_bytes()
            # 伪造 1：改总时间并同步更新 raw 摘要，不重放；reconstruct 应发现
            # 总时间与各阶段/pipeline 组合不一致。
            raw = json.loads(original_raw)
            raw['time']['total_us'] += 100
            path.write_text(json.dumps(raw))
            record['records'][case_id] = hashlib.sha256(path.read_bytes()).hexdigest()
            index.write_text(json.dumps(record))
            done = call('audit', '--no-replay')
            self.assertNotEqual(done.returncode, 0, 'forged model time accepted after updating outer digest')
            path.write_bytes(original_raw)
            index.write_bytes(original_index)
            # 伪造 2：只给某阶段某个共享资源的字节 +1；即使总时间没改，
            # 重建的资源字节也应与 links 不一致。
            raw = json.loads(original_raw)
            frame = next(s for s in raw['bandwidth']['stages'] if s['links'])
            frame['links'][next(iter(frame['links']))] += 1
            path.write_text(json.dumps(raw))
            record = json.loads(original_index)
            record['records'][case_id] = hashlib.sha256(path.read_bytes()).hexdigest()
            index.write_text(json.dumps(record))
            self.assertNotEqual(call('audit', '--no-replay').returncode, 0)
            path.write_bytes(original_raw)
            index.write_bytes(original_index)
            # 伪造 3：解压出的冻结引擎源文件被追加一行；checked() 的逐文件
            # 摘要必须拒绝，防止“用当前源码替换冻结基线”。
            frozen = out/'source/planning/results/final-96b18da3da36/source/algorithm/all2all.cpp'
            frozen.write_bytes(frozen.read_bytes()+b'\n// changed dependency\n')
            self.assertNotEqual(call('audit', '--no-replay').returncode, 0,
                                'changed extracted native source accepted')


if __name__ == '__main__':
    unittest.main()
