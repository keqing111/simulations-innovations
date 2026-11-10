# ============================================================================
# planning/tests/test_study.py：planning 协议/语义桥接的回归测试。
#
# 覆盖三件事：
#   1. 冻结网格形状：explore=8、confirm=32、12 个方法标签，ID 唯一、
#      全为 asymmetric 且 WAN 只取 50/5000；
#   2. check_pair 的语义粒度：stages/decisions/policy 任一字段变化都必须
#      被发现，而 wall_us/计时样本等“非语义字段”允许不同；
#   3. 真实 smoke：prepare+run 产出 cases×12 行，篡改 policy.optimized
#      或解压出的 scan/planner.cpp 都必须被拒绝。
# ============================================================================

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
# 直接加载 planning/study.py；它会再加载 scenario 框架。
spec=importlib.util.spec_from_file_location('planning_under_test',ROOT/'study.py')
study=importlib.util.module_from_spec(spec);spec.loader.exec_module(study)


class StudyTests(unittest.TestCase):
    def test_frozen_grid(self):
        # 网格是预先冻结的：8 个 explore 配置、32 个 confirm 配置；
        # confirm 通过两个种子把配置数从 16 扩到 32（n 也是 24/48）。
        self.assertEqual([len(study.specs(suite=s)) for s in ['explore','confirm']],[8,32])
        self.assertEqual(len(study.METHODS),12)
        for suite in ['explore','confirm']:
            cases=study.specs(suite=suite)
            # ID 唯一、全部为非对称链路档案、WAN 只覆盖两个水平。
            self.assertEqual(len({c['id'] for c in cases}),len(cases))
            self.assertEqual({c['profile'] for c in cases},{'asymmetric'})
            self.assertEqual({c['wan_us'] for c in cases},{50,5000})
        # 在 policy 元数据层面，SPM = A1 选项 + static_path；实现标签再叠加
        # optimized。真正构造时 static_path 会切换到静态最大权匹配。
        self.assertEqual(study.policy('SPM-opt'),{**study.scene.policy('A1'),'static_path':True,'optimized':True})

    def test_pair_guard_checks_every_semantic_field(self):
        # 构造一对最小 ref/opt：计时/墙钟字段故意不同，语义字段相同。
        a={'mode':'A1-ref','policy':study.policy('A1-ref'),'stages':[{'map':[1,0],'freq':3}],
           'decisions':[{'weight':3}],'timing_samples':[{'planning_us':100}],'wall_us':200}
        b=copy.deepcopy(a);b['mode']='A1-opt';b['policy']=study.policy('A1-opt');b['wall_us']=1;b['timing_samples']=[]
        # 非语义差异应被允许。
        study.check_pair(a,b)
        # 逐个篡改语义字段：stage 系数、决策权重、策略元数据。
        # 任一变化都必须让 check_pair 抛 ValueError。
        for field in ['stages','decisions','policy']:
            changed=copy.deepcopy(b)
            if field=='policy':changed[field]['static_path']=True
            else:changed[field][0]['freq' if field=='stages' else 'weight']=4
            with self.assertRaises(ValueError):study.check_pair(a,changed)

    def test_real_smoke_archive_and_tamper(self):
        # 真实 smoke：prepare 产生归档与登记，run 执行二进制并审计。
        # 结果行数 = 配置数 × 12（6 策略 × ref/opt），每行实现标签合法。
        with tempfile.TemporaryDirectory(prefix='fast-planning-test-') as tmp:
            out=Path(tmp);record=study.prepare(out,True)
            self.assertEqual(record['repeats'],3)
            rows=study.run(out,True)
            self.assertEqual(len(rows),len(record['cases'])*12)
            self.assertTrue(all(r['implementation'] in ['ref','opt'] for r in rows))
            # 篡改 1：把 A1-opt 原始结果里的 optimized 标志改成 False，
            # reconstruct 的策略/实现元数据检查应拒绝。
            raw=json.loads(next((out/'raw').glob('*A1-opt.json')).read_text())
            case=record['cases'][0];data=json.loads((out/'matrices'/f'{case["id"]}.json').read_text())
            raw['policy']['optimized']=False
            with self.assertRaises(ValueError):study.reconstruct(case,'A1-opt',data,raw)
            # 篡改 2：改动解压出的冻结源码，checked 的逐文件摘要应拒绝。
            path=out/'source'/'scan/planner.cpp';path.write_text(path.read_text()+'\n// tamper\n')
            with self.assertRaises(ValueError):study.checked(out,True)


if __name__=='__main__':unittest.main()
