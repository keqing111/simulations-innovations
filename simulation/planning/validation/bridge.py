#!/usr/bin/env python3
"""Rebuild final resource-only implementation; prove equality to frozen data.

Bridge timings are intentionally NOT performance measurements. All historical
diagnostic fields are independently audited before removing export-only fields.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile

# ============================================================================
# planning/validation/bridge.py：最终实现 ↔ 冻结规划数据的语义桥。
#
# 目标不是“再跑一遍得到相近数字”，而是证明当前 planning 源码构建出的
# 二进制，在冻结的 explore/confirm 输入上产生的**完整调度语义**与归档
# 逐字段相等。流程：
#   1. 用当前 study.py（SUITE=confirm）构建/校验收到的二进制；
#   2. 对两个冻结 planning 归档（explore 8 例 + confirm 32 例）重放，
#      每例 12 个方法共 480 项，逐项 canonical 比较；
#   3. 对两个旧 ordering 归档（116 例）比较 A1 历史调度，防止语义漂移；
#   4. 把当前实现输出、检查清单和验证源码一起写入 final-* 目录。
#
# canonical() 只剥离已声明的“导出/实现细节”字段（计时、行序诊断、
# optimized/static_path 标签等），保留 stage/matching/payload/费用/决策
# 等真正决定调度含义的内容。若剥离范围过大，测试
# validation/test_bridge.py 会用篡改用例发现。
#
# 重要：本脚本所有计时都来自语义重放，不能当作性能证据；性能验证在
# isolated.py 中另起进程、单独登记。
# ============================================================================

ROOT=Path(__file__).resolve().parents[1]
SIM=ROOT.parent


def load(path,name):
    # 按文件路径加载模块，避免依赖 sys.path；name 只用于调试/模块缓存。
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def canonical(raw):
    # 生成“只保留调度语义”的可比较字典：
    #   * 去掉墙钟、计时样本、原生分解/额外规划计时（随机器变化）；
    #   * 去掉以下划线开头的私有导出字段（如 _command）；
    #   * 统一 mode：A1-ref/A1-opt → A1；
    #   * 去掉仅用于诊断的行顺序/支持集/压力字段；
    #   * policy 内部去掉 optimized/static_path/row_order 标签。
    # 其余字段必须完全相等，包括 stage 顺序、matching、payload、费用和
    # decisions 的数值。任何“看起来差不多”的实现差异都会失败。
    value=copy.deepcopy({k:v for k,v in raw.items() if k not in ['wall_us','timing_samples','native_decompose_us','extra_planner_us'] and not k.startswith('_')})
    if value['mode'].endswith(('-ref','-opt')):value['mode']=value['mode'][:-4]
    for name in ['initial_rows','support_diagnostics']:value.pop(name,None)
    for name in ['optimized','static_path','row_order']:value['policy'].pop(name,None)
    for decision in value.get('decisions',[]):
        for name in ['row_order','row_pressure']:decision.pop(name,None)
    return value


def require_equal(expected,actual):
    # 语义相等断言；失败信息不打印巨大字典，只说明类别。
    if canonical(expected)!=canonical(actual):raise ValueError('full semantic bridge differs')


def run(output=None):
    # 当前 planning 协议固定用 confirm suite（32 配置）。最终二进制在
    # out 目录下构建；若已存在 build.json，则先用 isolated 的校验器确认
    # 二进制身份/依赖未变，避免复用被替换的产物。
    module=load(ROOT/'study.py','current_planning_bridge');module.SUITE='confirm'
    isolated=load(Path(__file__).with_name('isolated.py'),'bridge_binary_validator')
    ident=module.identity();out=(output or ROOT/'results'/('final-'+ident['digest'][:12])).resolve()
    # 已完成的桥接结果不可覆盖：保留旧证据，换新目录。
    if (out/'checks.json').exists():raise ValueError('completed bridge exists; preserve it and use a new output')
    if (out/'build.json').exists():
        if json.loads((out/'build.json').read_text())['identity']!=ident:raise ValueError('build source changed')
        binary=isolated.validate_binary(out)
    else:binary=module.build(out)
    # 归档验证脚本自身，使 final-* 目录可以独立复核“桥接逻辑”版本。
    with tarfile.open(out/'validation-source.tar.gz','w:gz') as stream:
        for path in sorted(Path(__file__).parent.glob('*.py')):stream.add(path,arcname=path.name)
    checks=[];sources={}
    # 第一部分：当前 planning 实现的冻结语义桥。
    # 两个归档分别覆盖 explore 与 confirm；每例重放一次 batch 得到 12 方法，
    # 先逐个重建、再 canonical 比较、最后检查每个策略的 ref/opt 成对相等。
    # 累计 40 例 × 12 = 480 项检查。
    for name,suite in [('run-45a8ce2df36d','explore'),('run-600edb9c2a2c','confirm')]:
        archive=ROOT/'results'/name;frozen=load(archive/'source/planning/study.py','frozen_'+suite);frozen.SUITE=suite
        record=frozen.checked(archive);sources[name]=dict(study_sha256=module.sha(archive/'study.json'),binary_sha256=module.sha(archive/'native_scan'))
        for case in record['cases']:
            data=json.loads((archive/'matrices'/f'{case["id"]}.json').read_text());actual=module.invoke(binary,case,None,data,1)
            for method in module.METHODS:
                key=case['id']+'-'+method;path=archive/'raw'/f'{key}.json'
                # 冻结原始结果本身必须先通过摘要检查；随后分别用冻结
                # study.py 和当前 study.py 重建，确保两边语义域都是合法的。
                if module.sha(path)!=record['records'][key]:raise ValueError('frozen raw changed')
                expected=json.loads(path.read_text());frozen.reconstruct(case,method,data,expected);module.reconstruct(case,method,data,actual[method])
                require_equal(expected,actual[method]);module.save(out/'raw'/f'{key}.json',actual[method]);checks.append(dict(archive=name,case=case['id'],method=method,equal=True))
            for strategy in module.STRATEGIES:module.check_pair(actual[strategy+'-ref'],actual[strategy+'-opt'])
            print('Bridge',case['id'],'12 policies equal',flush=True)
    historical=[]
    # 第二部分：历史 ordering 归档的 A1 回归。
    # 先用旧归档自己的二进制重放 A1，确认归档自洽；再用当前二进制跑
    # A1-opt，并用 canonical 比较（剥离历史导出字段）。这保证 A1 的
    # 行序/阈值决策没有在重构/优化中改变。
    for name,suite in [('run-5f741f907916','explore'),('run-fd58e408d88f','confirm')]:
        archive=SIM/'ordering/results'/name;old=load(archive/'source/ordering/study.py','old_order_'+suite);old.SUITE=suite
        record=old.checked(archive);sources[name]=dict(study_sha256=module.sha(archive/'study.json'),binary_sha256=module.sha(archive/'native_scan'))
        for case in record['cases']:
            data=json.loads((archive/'matrices'/f'{case["id"]}.json').read_text());previous=module.invoke(archive/'native_scan',case,'A1',data,1)
            path=archive/'raw'/f'{case["id"]}-A1.json'
            if module.sha(path)!=record['records'][case['id']+'-A1']:raise ValueError('historical raw changed')
            stored=json.loads(path.read_text())
            if module.engine.semantic(previous)!=module.engine.semantic(stored):raise ValueError('historical replay changed')
            old.reconstruct(case,'A1',data,previous)
            current=module.invoke(binary,case,'A1-opt',data,1);module.reconstruct(case,'A1-opt',data,current);require_equal(previous,current)
            historical.append(dict(archive=name,case=case['id'],equal=True,stages=len(current['stages'])))
        print('Historical bridge',name,len(record['cases']),'equal',flush=True)
    # checks.json 是后续 report_figures 的门票：它要求 checks 长度为 480、
    # historical 长度为 116 且全部 equal，才允许生成最终图。
    module.save(out/'checks.json',dict(source_identity=ident,binary_sha256=module.sha(binary),
                validation_source_sha256=module.sha(out/'validation-source.tar.gz'),sources=sources,checks=checks,historical=historical,
                timing_warning='all timings here are semantic replay only, NOT performance evidence; use separate isolated final run'))
    print('Final build:',out,'; full semantic checks',len(checks),'; historical',len(historical),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args();run(args.output)
