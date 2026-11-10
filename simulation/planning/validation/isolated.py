#!/usr/bin/env python3
"""Diagnostic only: warmed single-policy processes, reversed block order.

Uses an already frozen binary/input; does not change the formal study or tune
algorithms. Each independent planning instance still pays all cache/trace costs.
"""
import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import tarfile

# ============================================================================
# planning/validation/isolated.py：规划时间的“隔离诊断”，不是正式确认。
#
# 正式 planning 研究在同一个二进制进程里按固定顺序跑 12 个方法 × 24 次；
# 方法之间可能受分配器状态、缓存预热、前后顺序影响。本脚本用更保守的
# 协议单独测量规划开销：
#   * 只取冻结 confirm 归档中 N=48、layout_seed=760201 的 8 个配置；
#   * 每 (case, method) 在独立进程中运行，binary 内一次暖机 + 12 次采样；
#   * 第二块的 12 个方法顺序完全反转，抵消固定顺序/热身的偏差；
#   * 每次都重新构造规划器，预处理/候选预测/trace 全部计入规划。
#
# 重要边界：
#   1. 8 个配置是在观察到负对照的计时偏差之后选的，属于事后诊断，
#      不构成新算法确认，也不用于声称统计显著；
#   2. 计时仍受主机其他活动影响，不能当作生产尾延迟；
#   3. 重组样本时只把两块的 timing_samples 拼接，语义仍必须与冻结
#      原始结果逐字段一致（engine.semantic 比较）。
# ============================================================================


def orders(methods):
    # 两个 block：正常顺序 + 完全反转顺序。这样每个方法在“先跑”和
    # “后跑”各出现一次，减小固定位置带来的系统性偏差。
    return [list(methods),list(reversed(methods))]


def select(cases):
    # 只选 N=48 且 layout_seed=760201 的配置，共 8 个（2 负载 × 2 DC 数 × 2 WAN）。
    # 这是预先写死的诊断子集，不应随结果调整。
    return [c for c in cases if c['n']==48 and c['layout_seed']==760201]


def load(archive):
    # 从归档内部加载它自己的 frozen study.py，这样重建/语义比较使用的是
    # 归档时代的协议，而不是当前工作树的协议。
    path=archive/'source/planning/study.py'
    spec=importlib.util.spec_from_file_location('frozen_planning',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.SUITE='confirm'
    return module


def validate_binary(directory):
    # 校验要测量的二进制确实由登记的构建产生：
    #   1. native_scan 的哈希 == build.json.binary_sha256；
    #   2. source.tar.gz 的哈希 == build.json.source_archive_sha256；
    #   3. 解压出的每个源文件哈希 == build.identity.files。
    # 任一项不符都拒绝，避免测到另一个实现。
    def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    if not (directory/'build.json').is_file() or not (directory/'native_scan').is_file():raise ValueError('missing registered build')
    build=json.loads((directory/'build.json').read_text())
    if digest(directory/'native_scan')!=build['binary_sha256'] or digest(directory/'source.tar.gz')!=build['source_archive_sha256']:raise ValueError('registered build digest changed')
    for name,sha in build['identity']['files'].items():
        if digest(directory/'source'/name)!=sha:raise ValueError('registered build source changed')
    return directory/'native_scan'


def run(archive,out,binary_run=None):
    # archive：冻结的 planning 正式运行目录（提供输入/语义基线）；
    # binary_run：实际要测量的构建目录（默认同 archive）。
    # 允许二者分离，是为了在“新构建的二进制”上测规划时间，同时仍与
    # 冻结语义基线比较。
    module=load(archive);formal=module.checked(archive);cases=select(formal['cases']);repeats=12
    target=binary_run or archive;binary=validate_binary(target)
    # 两个构建必须使用相同编译选项，否则计时不可比。
    if json.loads((target/'build.json').read_text())['identity']['flags']!=formal['identity']['flags']:raise ValueError('different compiler flags')
    if len(cases)!=8:raise ValueError('wrong diagnostic subset')
    # 登记完整的诊断身份：脚本/测试/二进制/归档摘要、选中的 case、
    # 顺序、每块重复数、协议说明，以及“事后选择”的披露。
    ident=dict(source_sha256=module.sha(Path(__file__)),test_sha256=module.sha(Path(__file__).with_name('test_isolated.py')),
               binary_sha256=module.sha(binary),archive_sha256=module.sha(archive/'source.tar.gz'),
               binary_source_archive_sha256=module.sha(target/'source.tar.gz'),build_sha256=module.sha(target/'build.json'),binary_run=str(target),
               cases=cases,orders=orders(module.METHODS),repeats_per_block=repeats,blocks=2,
               protocol='new process per policy block, one warm-up; all preprocessing charged per instance; reversed second block',
               selection='N48, layout_seed760201, both loads/DC counts/WAN latencies; chosen after noticing negative-control timing bias; diagnostic, not new independent algorithm confirmation')
    out.mkdir(parents=True,exist_ok=True);registration=out/'registration.json'
    # 已有登记必须完全一致才能续跑；不允许在旧目录里换协议/换二进制。
    if registration.exists():
        record=json.loads(registration.read_text())
        if record['identity']!=ident:raise ValueError('diagnostic source/grid changed')
    else:
        # 归档本次诊断脚本；之后 raw 增量写入并随时可续跑。
        with tarfile.open(out/'source.tar.gz','w:gz') as stream:
            for name in ['isolated.py','test_isolated.py']:stream.add(Path(__file__).with_name(name),arcname=name)
        record=dict(identity=ident,records={},script_archive_sha256=module.sha(out/'source.tar.gz'));module.save(registration,record)
    if module.sha(out/'source.tar.gz')!=record['script_archive_sha256']:raise ValueError('diagnostic script archive changed')
    for case in cases:
        data=json.loads((archive/'matrices'/f'{case["id"]}.json').read_text())
        for block,methods in enumerate(ident['orders']):
            for method in methods:
                # 每个方法调用都是一次独立进程；raw 已存在则校验摘要后跳过，
                # 支持中断续跑。
                key=f'{case["id"]}-block{block}-{method}';path=out/'raw'/f'{key}.json'
                if key in record['records']:
                    if module.sha(path)!=record['records'][key]:raise ValueError('diagnostic raw changed')
                    continue
                raw=module.invoke(binary,case,method,data,repeats)
                # 先重建数据域，再与冻结语义逐字段比较；隔离测量不允许
                # 改变调度含义，只允许改变计时样本。
                module.reconstruct(case,method,data,raw)
                expected=json.loads((archive/'raw'/f'{case["id"]}-{method}.json').read_text())
                if module.engine.semantic(raw)!=module.engine.semantic(expected):raise ValueError('isolated semantics changed')
                raw['_command']=module.command(binary,case,method,repeats)
                module.save(path,raw);record['records'][key]=module.sha(path);module.save(registration,record)
            print(case['id'],'isolated block',block,'complete',flush=True)
    rows=[]
    # 合并两块样本：每方法得到 2×12=24 个规划计时；语义字段仍取自 raw
    # （两块必须一致），只有 timing_samples 被拼接后交给 reconstruct。
    for case in cases:
        data=json.loads((archive/'matrices'/f'{case["id"]}.json').read_text())
        for method in module.METHODS:
            samples=[]
            for block in range(2):
                key=f'{case["id"]}-block{block}-{method}';path=out/'raw'/f'{key}.json'
                if module.sha(path)!=record['records'][key]:raise ValueError('diagnostic digest changed')
                raw=json.loads(path.read_text());module.reconstruct(case,method,data,raw)
                if len(raw['timing_samples'])!=repeats:raise ValueError('diagnostic repeat count')
                samples.extend(raw['timing_samples'])
            row,_=module.reconstruct(case,method,data,dict(raw,timing_samples=samples));rows.append(row)
    index={(r['id'],r['method']):r for r in rows}
    for row in rows:
        # 与正式 study 相同的口径：规划加速比只比同一策略的 ref/opt；
        # 含规划加速比按同一实现与各基线成对比较。
        reference=index[row['id'],row['strategy']+'-ref']
        row['planning_speedup']=reference['planning_us']/row['planning_us']
        for baseline in module.STRATEGIES:
            ref=index[row['id'],baseline+'-'+row['implementation']]
            row['with_planning_'+baseline]=ref['modeled_plus_planning_ms']/row['modeled_plus_planning_ms']
    module.save(out/'comparison.json',rows)
    with (out/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print('Isolated diagnostic:',len(rows),'outcomes,',len(rows)*24,'measured samples',flush=True)


if __name__=='__main__':
    # --archive 指定冻结的正式运行目录；--binary-run 可指定另一份已登记
    # 构建来测规划时间；--output 必须是一个新的诊断目录（协议/身份会登记）。
    parser=argparse.ArgumentParser();parser.add_argument('--archive',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--binary-run',type=Path)
    args=parser.parse_args();run(args.archive.resolve(),args.output.resolve(),args.binary_run.resolve() if args.binary_run else None)
