#!/usr/bin/env python3
"""Report-only derived figures; refuse mixed-version CPU provenance."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

# ============================================================================
# planning/validation/report_figures.py：从已审计数据生成最终论文图/指标。
#
# 这是最下游的脚本，刻意做“门票检查”：
#   1. 通信数据来自正式 planning confirm 运行；
#   2. CPU 数据来自 isolated.py 的独立诊断；
#   3. CPU 诊断登记里的二进制必须与 bridge 的最终二进制相同；
#   4. bridge 必须已有 480 项当前语义检查 + 116 项历史检查，且全部 equal。
# 任一项不满足就拒绝出图，避免把不同版本/不同二进制的数字拼在同一张图上。
# provenance.json 记录每个输入文件的哈希和“无置信区间”的声明。
# ============================================================================

METHODS=['F000','maxmin','spreadout','X10','SPM','A1']


def gm(values):
    # 几何平均：比值型指标（加速比、规划加速比）跨配置聚合的常用口径；
    # 传入 0/负值会失败，调用方应只传正比值。
    return math.exp(statistics.mean(math.log(v) for v in values))


def require_final_binary(bridge,registration):
    # CPU 诊断与语义桥必须来自同一个最终二进制；否则通信图和规划图
    # 描述的不是同一个实现。
    if bridge['binary_sha256']!=registration['identity']['binary_sha256']:raise ValueError('CPU evidence is not from final binary')


def render(formal,isolated,final,out):
    # formal：正式 confirm 运行（communication）；isolated：隔离 CPU 诊断；
    # final：bridge 输出（含 checks.json 与最终二进制）；out：图/指标目录。
    read=lambda path:json.loads(path.read_text())
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    save=lambda path,value:path.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n')
    bridge=read(final/'checks.json');registration=read(isolated/'registration.json');require_final_binary(bridge,registration)
    # 完整性门票：任何一项语义检查缺失/失败都不允许生成最终图。
    if len(bridge['checks'])!=480 or len(bridge['historical'])!=116 or not all(c['equal'] for c in bridge['checks']+bridge['historical']):raise ValueError('incomplete final semantic proof')
    communication=read(formal/'comparison.json');cpu=read(isolated/'comparison.json');out.mkdir(parents=True,exist_ok=True)
    opt=[r for r in cpu if r['implementation']=='opt'];index={(r['id'],r['method']):r for r in cpu}
    # 指标分组：all / balanced / zipf。通信加速比取正式 confirm 的 opt 行；
    # 规划指标取隔离诊断的 opt 行，并用同配置 A1-ref 作为对照。
    # communication 中的每个 baseline 加速比都是几何平均。
    metrics={}
    for kind in ['all','balanced','zipf']:
        metrics[kind]={}
        for method in METHODS:
            cc=[r for r in communication if r['method']==method+'-opt' and (kind=='all' or r['kind']==kind)]
            ss=[r for r in opt if r['strategy']==method and (kind=='all' or r['kind']==kind)]
            refs=[index[r['id'],method+'-ref'] for r in ss]
            gains=[r['planning_speedup'] for r in ss]
            metrics[kind][method]=dict(communication={b:gm([r['speedup_'+b] for r in cc]) for b in METHODS},
                planning_gm=gm(gains),planning_min=min(gains),planning_max=max(gains),
                ref_median_ms=statistics.median(r['planning_us']/1000 for r in refs),
                opt_median_ms=statistics.median(r['planning_us']/1000 for r in ss),
                p95_median_ms=statistics.median(r['planning_p95_us']/1000 for r in ss),
                with_planning={b:gm([r['with_planning_'+b] for r in ss]) for b in METHODS})
    save(out/'metrics.json',metrics)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    # 图 1：相对 maxmin 与 SPM 的通信加速比，按 balanced/zipf 分柱；
    # 数值来自正式 confirm，不含规划时间。
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    for ax,baseline in zip(axes,['maxmin','SPM']):
        for offset,kind in enumerate(['balanced','zipf']):
            values=[metrics[kind][method]['communication'][baseline] for method in METHODS]
            ax.bar([i+(offset-.5)*.35 for i in range(6)],values,.35,label=kind)
        ax.set_xticks(range(6),METHODS);ax.axhline(1,color='gray',ls='--');ax.set_ylabel('Communication speedup / '+baseline)
        ax.set_title('N24/48, confirm; equal load-stratum weights');ax.legend();ax.grid(axis='y',alpha=.2)
    fig.savefig(out/'communication.png',dpi=170);plt.close(fig)
    # 图 2：左图 A1 的 ref/opt 完整规划时间（N48、每配置 24 样本）；
    # 右图各策略 ref→opt 的规划加速比 GM；灰柱是不改内核的对照，
    # 蓝柱是 A1，避免把“所有策略都变快”误读成 A1 独有收益。
    fig,axes=plt.subplots(1,2,figsize=(13,4.5),layout='constrained')
    selected=[r for r in opt if r['strategy']=='A1'];xs=list(range(len(selected)))
    for offset,impl in enumerate(['ref','opt']):
        values=[index[r['id'],'A1-'+impl]['planning_us']/1000 for r in selected]
        axes[0].bar([i+(offset-.5)*.36 for i in xs],values,.36,label=impl)
    axes[0].set_xticks(xs,[f'D{r["dc_count"]} {r["kind"]}\n{r["wan_us"]}us' for r in selected],rotation=35,ha='right',fontsize=8)
    axes[0].set_ylabel('A1 full planning median (ms)');axes[0].set_title('N48: 24 samples/config; final binary');axes[0].legend()
    values=[metrics['all'][method]['planning_gm'] for method in METHODS]
    bars=axes[1].bar(METHODS,values,color=['#999999']*5+['#1f77b4'])
    for bar,value in zip(bars,values):axes[1].text(bar.get_x()+bar.get_width()/2,value+.015,f'{value:.3f}',ha='center',fontsize=9)
    axes[1].axhline(1,color='gray',ls='--');axes[1].set_ylim(0,max(values)*1.15);axes[1].set_ylabel('Paired planning speedup ref / opt (GM)')
    axes[1].set_title('Gray: unchanged-kernel controls; blue: pooled A1')
    for ax in axes:ax.grid(axis='y',alpha=.2)
    fig.savefig(out/'planning_final.png',dpi=170);plt.close(fig)
    # provenance 固定输入版本；warning 明确这些图不包含 V1 计时、也不代表
    # 置信区间。任何输入文件变化都会在哈希里体现。
    save(out/'provenance.json',dict(script_sha256=digest(Path(__file__)),test_sha256=digest(Path(__file__).with_name('test_figures.py')),
                communication_sha256=digest(formal/'comparison.json'),cpu_sha256=digest(isolated/'comparison.json'),
                final_checks_sha256=digest(final/'checks.json'),binary_sha256=bridge['binary_sha256'],
                cpu_warning='Only isolated final-binary CPU; no V1 timing is used in these figures. No confidence intervals implied.'))


if __name__=='__main__':
    # 四个输入都必须显式给出：正式 confirm 运行、隔离 CPU 诊断、
    # bridge 最终目录、出图目录；不提供默认路径，避免误用旧批次。
    parser=argparse.ArgumentParser()
    for name in ['formal','isolated','final','output']:parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();render(args.formal.resolve(),args.isolated.resolve(),args.final.resolve(),args.output.resolve())
