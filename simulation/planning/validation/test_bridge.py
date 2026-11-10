# ============================================================================
# planning/validation/test_bridge.py：canonical() 剥离范围的边界测试。
#
# 桥接的核心风险是“为了通过比较而剥得太多”。这个测试构造一对 ref/opt：
#   * 计时、初始行、支持诊断、行序/压力等导出字段不同 → 允许；
#   * stage 的 payload/map/freq、policy.exact 等语义字段不同 → 必须失败。
# 这样把 canonical() 的白名单钉死在文档声明的字段上。
# ============================================================================

import copy
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('bridge',Path(__file__).with_name('bridge.py'))
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)


class BridgeTests(unittest.TestCase):
    def test_only_documented_export_and_implementation_fields_differ(self):
        # old 模拟 ref，new 模拟 opt：只在白名单字段上不同。
        old=dict(mode='A1',policy={'row_order':0,'exact':True},stages=[{'freq':10,'map':[1,0],'payload':[10,3]}],
                 decisions=[{'weight':10,'row_order':[0,1],'row_pressure':[]}],initial_rows=[10,3],support_diagnostics=[],wall_us=5,timing_samples=[])
        new=copy.deepcopy(old);new.update(mode='A1-opt',policy={'optimized':True,'static_path':False,'exact':True},wall_us=9)
        for key in ['initial_rows','support_diagnostics']:new.pop(key)
        new['decisions'][0].pop('row_order');new['decisions'][0].pop('row_pressure')
        # 只差导出/实现字段时，语义比较应通过。
        b.require_equal(old,new)
        # 篡改真正的调度语义：payload、map、系数、policy.exact。
        # 每一个都必须被 require_equal 拒绝。
        for key in ['payload','map','freq']:
            bad=copy.deepcopy(new);bad['stages'][0][key]=[0,0] if key!='freq' else 9
            with self.assertRaises(ValueError):b.require_equal(old,bad)
        bad=copy.deepcopy(new);bad['policy']['exact']=False
        with self.assertRaises(ValueError):b.require_equal(old,bad)


if __name__=='__main__':unittest.main()
