# ============================================================================
# planning/validation/test_isolated.py：隔离诊断协议的两个关键约束。
#
#   1. 第二块必须完全反转方法顺序，且 select() 只能取 N48/layout_seed760201
#      的 8 个配置；协议一旦改动，计时数据就不能与旧批次拼接。
#   2. 传入的构建目录必须带 build.json/native_scan 并通过摘要校验；
#      空目录必须报 'registered build'，防止误把任意二进制当最终实现。
# ============================================================================

import importlib.util
from pathlib import Path
import unittest
import tempfile

spec=importlib.util.spec_from_file_location('isolated',Path(__file__).with_name('isolated.py'))
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)


class IsolatedTests(unittest.TestCase):
    def test_protocol_is_fixed_and_balances_pair_block_order(self):
        # 12 个方法标签（6 策略 × ref/opt）；第二块是完整逆序，
        # 用于抵消固定位置偏差。
        methods=['F000-ref','F000-opt','maxmin-ref','maxmin-opt','spreadout-ref','spreadout-opt','X10-ref','X10-opt','SPM-ref','SPM-opt','A1-ref','A1-opt']
        self.assertEqual(v.orders(methods),[methods,list(reversed(methods))])
        # 构造完整的 confirm 网格（32 配置），select() 应只留下
        # N=48 且 layout_seed=760201 的 8 个。
        cases=[dict(id=f'{n}-{kind}-{seed}-{w}-{d}',n=n,kind=kind,layout_seed=seed,wan_us=w,dc_count=d)
               for n in [24,48] for kind in ['balanced','zipf'] for seed in [760201,760203] for w in [50,5000] for d in [3,6]]
        selected=v.select(cases);self.assertEqual(len(selected),8)
        self.assertTrue(all(c['n']==48 and c['layout_seed']==760201 for c in selected))

    def test_override_requires_registered_build(self):
        # 空临时目录没有 build.json/native_scan，必须被 validate_binary 拒绝。
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'registered build'):
                v.validate_binary(Path(tmp))


if __name__=='__main__':unittest.main()
