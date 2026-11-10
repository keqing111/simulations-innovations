#!/usr/bin/env python3
"""Frozen same-process implementation benchmark and same-model SPM control."""
import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import statistics

# ============================================================================
# planning/study.py：planning 扫描的“套壳”实验协议。
#
# planning 目录不是一套新的网络仿真器，而是复用 scenario/study.py 的
# 输入生成、二进制调用、矩阵与审计框架，再叠加：
#   * 6 种策略 × 2 种实现（ref/opt）= 12 个方法标签；
#   * explore（8 配置）与 confirm（32 配置，两个新种子）两个 suite；
#   * -DFAST_PLANNING_SCAN 打开 planning 专用输出（决策/计时/静态路径）；
#   * 对每个 (case, strategy) 要求 ref/opt 的完整语义相等；
#   * summarize()/render() 生成几何平均与论文图；
#   * historical() 重放旧 ordering 归档，确认 A1 的历史结果未漂移。
#
# 关键复用方式：scenario/study.py 暴露的 base 模块（实际加载的是
# scan/study.py 的通用实验引擎）被本文件“猴子补丁”式替换 METHODS/FLAGS/
# files/identity/specs/command/reconstruct/checked/audit 等钩子；build/run
# 直接复用。这样冻结/重放/摘要逻辑只有一份，planning 只负责协议差异。
# ============================================================================

ROOT=Path(__file__).resolve().parent
SIM=ROOT.parent
# 载入 scenario 研究脚本；scene.base 是它内部的 engine 模块，
# scene.*/_prepare/_checked/_reconstruct 是可直接复用的实现。
_spec=importlib.util.spec_from_file_location('private_planning_scenario',SIM/'scenario/study.py')
scene=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(scene)
engine=scene.base
_files=scene.files
_prepare=scene._prepare
_checked=scene._checked
_reconstruct=scene.reconstruct
# default suite；main() 会根据 --suite 覆盖全局 SUITE。
SUITE='explore'
# 六个策略：原生 F000、精确 Max-Min、SpreadOut 参照、X10 交替环修复、
# SPM 静态路径最大权控制、A1（sliding 行序/阈值搜索）。每个都跑 ref/opt
# 两种实现，用语义桥接证明“实现优化没有改变调度含义”。
STRATEGIES=['F000','maxmin','spreadout','X10','SPM','A1']
METHODS=[s+'-'+i for s in STRATEGIES for i in ['ref','opt']]
# 在 scenario 的编译选项上增加 FAST_PLANNING_SCAN，打开 planning 输出。
FLAGS=[*scene.FLAGS,'-DFAST_PLANNING_SCAN']
sha=scene.sha
save=scene.save
command=scene.command
invoke=engine.invoke


def files():
    # 登记本目录的源码/Makefile/tests 以及 scenario 框架文件；
    # 这些文件哈希构成 planning 运行的身份，任一改变都应换新目录。
    return sorted(set(_files())|{p for p in ROOT.glob('*') if p.suffix in ['.cc','.cpp','.h','.py'] or p.name=='Makefile'}|set((ROOT/'tests').glob('*.*')))


def identity():
    # 身份包含：所有源文件哈希、编译选项、suite、编译器版本、libc 版本、
    # schema 版本；最后对整条记录再取 SHA-256 摘要作为运行目录名。
    record=dict(files={str(p.relative_to(SIM)):sha(p) for p in files()},flags=FLAGS,suite=SUITE,
                compiler=engine.subprocess.check_output(['g++','--version'],text=True).splitlines()[0],
                libc=list(platform.libc_ver()),schema=1)
    record['digest']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest();return record


def specs(smoke=False,suite=None):
    # 配置网格：
    #   smoke   : n=6, g=1, 1MiB, 1 seed, 单 WAN  → 机制测试
    #   explore : n=12, g=8, 64MiB, 3/6 DC, 2 负载, 2 WAN, 1 seed → 8 配置
    #   confirm : n=24/48, g=8, 64MiB, 3/6 DC, 2 负载, 2 WAN, 2 seed → 32 配置
    # DC 标签用 floor(s*d/n) 均匀切分；layout_seed 与 zipf seed 分开，
    # 使“负载随机性”和“拓扑/布局随机性”不混为一个变量。
    suite=suite or SUITE;cases=[]
    for n in ([6] if smoke else [24,48] if suite=='confirm' else [12]):
        for d in ([3] if smoke else [3,6]):
            for kind in ['balanced','zipf']:
                for wan in ([50] if smoke else [50,5000]):
                    for seed in ([760201,760203] if suite=='confirm' and not smoke else [760101]):
                        c=dict(n=n,g=1 if smoke else 8,mib=1 if smoke else 64,dc_count=d,
                               dc=[s*d//n for s in range(n)],profile='asymmetric',kind=kind,
                               seed=seed if kind=='zipf' else 0,layout_seed=seed,wan_us=wan,split=suite)
                        c['id']=f'n{n}g{c["g"]}-d{d}-asymmetric-{kind}-s{c["seed"]}-l{seed}-w{wan}'
                        cases.append(c)
    return cases


def policy(method):
    # 从方法标签拆出 (strategy, implementation)。SPM 在内部表示为
    # exact q* + 一次构造 + static_path：阈值搜索与 A1 相同，但构造阶段
    # 改用静态边效用的最大权匹配，而不是 A1 的资源感知构造。
    if method not in METHODS:raise ValueError('unknown implementation label')
    strategy,implementation=method.rsplit('-',1)
    return {**scene.policy('A1' if strategy=='SPM' else strategy),
            'optimized':implementation=='opt','static_path':strategy=='SPM'}


def normalized(raw):
    # 比较 ref/opt 时去掉“实现细节”字段：模式后缀、optimized 标志、
    # 计时/导出字段由 engine.semantic() 先行剥离；这里再统一模式名。
    # 保留的是完整调度语义：stage、matching、payload、费用、决策等。
    value=copy.deepcopy(engine.semantic(raw));value['mode']=value['mode'].rsplit('-',1)[0]
    value['policy'].pop('optimized')
    return value


def check_pair(reference,optimized):
    # 每个 (case,strategy) 的 ref/opt 必须满足：
    #   1. 标签确实是 ref 与 opt；
    #   2. policy.optimized 元数据与标签一致；
    #   3. 归一化后的完整语义完全相等。
    # 这不是“输出差不多”，而是逐字段相等；实现优化不得改变调度。
    if reference['mode'].rsplit('-',1)[-1]!='ref' or optimized['mode'].rsplit('-',1)[-1]!='opt':raise ValueError('pair labels')
    if reference['policy']['optimized'] or not optimized['policy']['optimized']:raise ValueError('pair implementation metadata')
    if normalized(reference)!=normalized(optimized):raise ValueError('implementation changed full schedule/counters/payload/cost')


def reconstruct(case,method,data,raw):
    # 复用 scenario 的重建器，但先把 SPM 适配成它认识的 A1：
    # 二进制内部 SPM 就是 A1+static_path，重建时用 A1 的语义即可；
    # 之后再补回对外的 method/strategy/implementation 标签。
    if raw['mode']!=method or raw['policy']!=policy(method):raise ValueError('strategy/implementation policy')
    strategy,implementation=method.rsplit('-',1);mapped='A1' if strategy=='SPM' else strategy
    adapted=dict(raw,mode=mapped,policy=scene.policy(mapped))
    row,totals=_reconstruct(case,mapped,data,adapted)
    row.update(method=method,strategy=strategy,implementation=implementation)
    return row,totals


def prepare(output,smoke=False):
    # 复用 scenario 的 prepare（冻结源码、构建、矩阵、归档），再登记
    # planning 特有的 suite 与重复次数（smoke 3 次，正式 24 次）。
    output=Path(output)
    if (output/'study.json').exists():return checked(output,smoke)
    record=_prepare(output,smoke);record.update(suite=SUITE,repeats=3 if smoke else 24)
    save(output/'study.json',record);return record


def checked(output,smoke=False):
    # 在 scenario 完整性检查之上，确认 suite/重复次数确实是本次协议，
    # 防止用 explore 的结果冒充 confirm 或反之。
    record=_checked(output,smoke)
    if record.get('suite')!=SUITE or record['repeats']!=(3 if smoke else 24):raise ValueError('suite/repeat registration')
    return record


def audit(output,smoke=False,replay=True):
    # 审计分三层：
    #   1. 登记完整性：raw 文件集合必须恰好等于 cases×methods，摘要匹配，
    #      _command/timing 重复次数符合登记；
    #   2. 语义重放：可选重跑原生二进制，与存档逐语义字段相等；
    #   3. 独立重建 + 成对检查：每个方法从 stage 重建网络费用，同 case 的
    #      逐资源总字节必须一致，每个 strategy 的 ref/opt 必须完整相等。
    # 最后计算 planning_speedup 和相对各策略的通信/含规划加速比。
    output=Path(output);study=checked(output,smoke);rows=[]
    keys={c['id']+'-'+m for c in study['cases'] for m in METHODS}
    if set(study['records'])!=keys or {p.stem for p in (output/'raw').glob('*.json')}!=keys:raise ValueError('incomplete/unregistered raw')
    for case in study['cases']:
        data=json.loads((output/'matrices'/f'{case["id"]}.json').read_text());totals_reference=None;batch={}
        # 重放一次 batch（12 方法），计时只跑 1 次；语义比较不看计时字段。
        if replay:replayed=invoke(output/'native_scan',case,None,data,1)
        for method in METHODS:
            key=case['id']+'-'+method;path=output/'raw'/f'{key}.json'
            if sha(path)!=study['records'][key]:raise ValueError('raw timing/semantic digest changed')
            raw=json.loads(path.read_text());batch[method]=raw
            # 命令必须与登记的执行命令完全一致（含 repeats），防止用不同
            # 参数跑出的结果冒充本协议结果。
            if raw['_command']!=command(Path(study['execution_root'])/'native_scan',case,None,study['repeats']):raise ValueError('wrong execution command')
            if len(raw['timing_samples'])!=study['repeats']:raise ValueError('wrong timing repetition count')
            if replay and engine.semantic(raw)!=engine.semantic(replayed[method]):raise ValueError('native replay mismatch')
            row,totals=reconstruct(case,method,data,raw)
            # 同一 case 下所有策略/实现必须搬运完全相同的逐资源字节；
            # 只允许改变“怎么搬”，不允许改变“搬多少”。
            if totals_reference is not None and totals!=totals_reference:raise ValueError('methods changed routed byte totals')
            totals_reference=totals;rows.append(row)
        # 每个策略的 ref/opt 语义桥接检查。
        for strategy in STRATEGIES:check_pair(batch[strategy+'-ref'],batch[strategy+'-opt'])
    index={(r['id'],r['method']):r for r in rows}
    for row in rows:
        # planning_speedup 只在同一实现内比较：ref 的规划时间 / opt 的规划
        # 时间。跨实现比较没有意义，因为 ref 本身就是“未优化”的对照。
        impl=row['implementation'];reference=index[row['id'],row['strategy']+'-ref']
        row['planning_speedup']=reference['planning_us']/row['planning_us']
        for baseline in STRATEGIES:
            # 通信加速比和“通信+串行规划”加速比分别列出，避免把规划开销
            # 藏进通信结论；baseline 用同一实现（ref 对 ref，opt 对 opt）。
            base=index[row['id'],baseline+'-'+impl]
            row['speedup_'+baseline]=base['total_ms']/row['total_ms']
            row['with_planning_'+baseline]=base['modeled_plus_planning_ms']/row['modeled_plus_planning_ms']
    save(output/'comparison.json',rows)
    with (output/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print('Audited full ref/opt equality, replay, routes, payload and native pipeline:',len(rows),flush=True)
    return rows


# 把 planning 的协议钩子注入 scenario 的 engine 模块；build/run 直接复用，
# 因此冻结/构建/运行/重放代码不需要在 planning 再写一遍。
engine.METHODS=METHODS;engine.FLAGS=FLAGS;engine.files=files;engine.identity=identity;engine.specs=specs
engine.matrix=scene.matrix;engine.input_key=scene.input_key;engine.command=command
engine.reconstruct=reconstruct;engine.checked=checked;engine.audit=audit
build=engine.build;run=engine.run


def summarize(rows):
    # 按“全部 / 负载类型 / 服务器数”分组，对每方法计算：
    #   * 规划时间中位数与 p95 中位数（跨配置）；
    #   * 相对每个基线的加速比几何平均（GM）、最小/最大、胜/负计数；
    #   * 含规划时间的加速比 GM。
    # GM 适合跨配置聚合比值；胜/负用 1±1e-9 容差，不代表统计显著性。
    gm=lambda vs:math.exp(statistics.mean(math.log(v) for v in vs));result=[]
    groups=[('all',lambda r:True)]
    groups += [('load:'+k,lambda r,k=k:r['kind']==k) for k in sorted({r['kind'] for r in rows})]
    groups += [('N:'+str(n),lambda r,n=n:r['n']==n) for n in sorted({r['n'] for r in rows})]
    for group,include in groups:
        for method in METHODS:
            ss=[r for r in rows if r['method']==method and include(r)]
            record=dict(group=group,method=method,configs=len(ss),planning_speedup=gm([r['planning_speedup'] for r in ss]),
                        planning_median_ms=statistics.median(r['planning_us']/1000 for r in ss),
                        planning_p95_median_ms=statistics.median(r['planning_p95_us']/1000 for r in ss))
            for base in STRATEGIES:
                ratios=[r['speedup_'+base] for r in ss]
                record[base]=dict(gm=gm(ratios),minimum=min(ratios),maximum=max(ratios),
                                  wins=sum(v>1+1e-9 for v in ratios),losses=sum(v<1-1e-9 for v in ratios),
                                  with_planning_gm=gm([r['with_planning_'+base] for r in ss]))
            result.append(record)
    return result


def render(output,smoke=False):
    # 先审计再出图；图只用于描述已审计数据，不引入新的统计口径。
    # 左图：各策略 ref/opt 的完整规划时间随 N 的中位数；
    # 右图：各策略 ref→opt 的成对规划加速比 GM。
    # 第二张图：相对优化版 Max-Min 的通信加速比，以及加入串行规划后的加速比。
    output=Path(output);rows=audit(output,smoke);save(output/'summary.json',summarize(rows))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    gm=lambda vs:math.exp(statistics.mean(math.log(v) for v in vs));ns=sorted({r['n'] for r in rows})
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    for strategy in ['F000','maxmin','SPM','A1']:
        for impl,style in [('ref','--'),('opt','-')]:
            ss=[r for r in rows if r['strategy']==strategy and r['implementation']==impl]
            axes[0].plot(ns,[statistics.median(r['planning_us']/1000 for r in ss if r['n']==n) for n in ns],style,marker='o',label=strategy+'-'+impl)
    ss=[r for r in rows if r['implementation']=='opt']
    axes[1].bar(STRATEGIES,[gm([r['planning_speedup'] for r in ss if r['strategy']==s]) for s in STRATEGIES]);axes[1].axhline(1,color='gray',ls='--')
    axes[0].set_xlabel('Servers');axes[0].set_ylabel('Full planning median (ms)');axes[0].legend(fontsize=7,ncol=2)
    axes[1].set_ylabel('Paired planning speedup ref / opt (GM)')
    for ax in axes:ax.grid(axis='y',alpha=.2)
    fig.savefig(output/'planning.png',dpi=170);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    for ax,key,title in zip(axes,['speedup_maxmin','with_planning_maxmin'],['Communication only','Communication + serial host planning']):
        for offset,kind in enumerate(['balanced','zipf']):
            values=[gm([r[key] for r in ss if r['strategy']==s and r['kind']==kind]) for s in STRATEGIES]
            ax.bar([i+(offset-.5)*.35 for i in range(6)],values,.35,label=kind)
        ax.set_xticks(range(6),STRATEGIES);ax.axhline(1,color='gray',ls='--');ax.set_ylabel('Speedup / optimized Max-Min');ax.set_title(title);ax.legend();ax.grid(axis='y',alpha=.2)
    fig.savefig(output/'comparison.png',dpi=170);plt.close(fig)


def historical(output):
    # 历史回归：从两个旧 ordering 运行目录重放其原生二进制，先确认旧
    # 归档自身重放一致，再用当前 native_scan 跑同一输入的 A1-opt，比较
    # 完整调度。比较前只剥离两类已声明为“导出/实现细节”的字段：
    # initial_rows、support_diagnostics、row_order、row_pressure、
    # static_path，以及模式后缀。任何实际调度差异都视为 A1 语义漂移。
    output=Path(output);study=checked(output);checks=[]
    for name in ['run-5f741f907916','run-fd58e408d88f']:
        old=SIM/'ordering/results'/name;registered=json.loads((old/'study.json').read_text())
        for case in registered['cases']:
            data=json.loads((old/'matrices'/f'{case["id"]}.json').read_text())
            previous=invoke(old/'native_scan',case,'A1',data,1)
            stored=json.loads((old/'raw'/f'{case["id"]}-A1.json').read_text())
            if engine.semantic(previous)!=engine.semantic(stored):raise ValueError('old archive replay changed')
            current=invoke(output/'native_scan',case,'A1-opt',data,1)
            prior=copy.deepcopy(engine.semantic(previous));prior.pop('initial_rows');prior.pop('support_diagnostics');prior['policy'].pop('row_order')
            for d in prior['decisions']:d.pop('row_order');d.pop('row_pressure')
            now=normalized(current);now['policy'].pop('static_path')
            if prior!=now:raise ValueError('historical A1 schedule changed: '+case['id'])
            checks.append(dict(archive=name,case=case['id'],stages=len(current['stages']),equal=True))
    save(output/'historical_checks.json',dict(checks=checks,binaries={name:sha(SIM/'ordering/results'/name/'native_scan') for name in ['run-5f741f907916','run-fd58e408d88f']}))
    print('Historical archived binary/input/full schedule equality:',len(checks),flush=True)


def main():
    # phase 分派：prepare/run/audit/render 交给 engine 的通用流程；
    # historical 只做旧归档回归。默认输出目录名包含身份摘要前 12 位。
    global SUITE
    p=argparse.ArgumentParser();p.add_argument('--phase',required=True,choices=['prepare','run','audit','render','historical'])
    p.add_argument('--suite',choices=['explore','confirm'],default='explore');p.add_argument('--output',type=Path);p.add_argument('--smoke',action='store_true');args=p.parse_args();SUITE=args.suite
    output=(args.output or ROOT/'results'/('run-'+identity()['digest'][:12]+('-smoke' if args.smoke else ''))).resolve()
    if args.phase=='historical':historical(output)
    else:globals()[args.phase](output,args.smoke)


if __name__=='__main__':main()
