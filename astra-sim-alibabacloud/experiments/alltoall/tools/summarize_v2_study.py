#!/usr/bin/env python3
"""Descriptive snapshot of completed paired cases, never impute pending results."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import statistics

METHODS=('fast-simai','spreadout','fast-joint-stage-b1','fast-joint-stage-b4','fast-joint-capacity-b1','fast-joint-capacity-b4')
LABELS=('FAST-SimAI','Spreadout','Stage-q*','Stage-4q','Capacity-q*','Capacity-4q')

def paired(rows):
    index={(r['case'],r['method']):r for r in rows if r.get('success') and r.get('communication_ns',0)>0}
    cases=sorted(c for c in {r['case'] for r in rows} if all((c,m) in index for m in METHODS))
    table={}
    for method in METHODS:
        values=[index[c,'fast-simai']['communication_ns']/index[c,method]['communication_ns'] for c in cases]
        spread=[index[c,'spreadout']['communication_ns']/index[c,method]['communication_ns'] for c in cases]
        table[method]=dict(paired_cases=len(cases),speedup_fast_geomean=statistics.geometric_mean(values) if values else None,
            speedup_spreadout_geomean=statistics.geometric_mean(spread) if spread else None,
            minimum=min(values) if values else None,maximum=max(values) if values else None,
            slower_than_fast=sum(v<1-1e-6 for v in values))
    return cases,table

def generate(root):
    study=json.loads((root/'study.json').read_text());rows=study['runs'];cases,aggregate=paired(rows)
    index={(r['case'],r['method']):r for r in rows};status=Counter(r['status'] for r in rows)
    assets=root/'assets';assets.mkdir(exist_ok=True)
    text=['# SimAI 多 DC A2Av 实验结果（持续更新快照）','', '## Material Passport','',
        '- Origin Skill: academic-research-suite / experiment-agent','- Origin Mode: run',
        '- Origin Date: 2026-11-15','- Verification Status: 部分运行已完成，完整矩阵仍以 study.json 状态为准',
        '- Version Label: leafspine-v2-ns3','', '## 1. 当前覆盖与口径','',
        f'快照时间（UTC）：{datetime.now(timezone.utc).isoformat(timespec="seconds")}。',
        f'计划 {study["planned_runs"]} 次；已登记 {len(rows)} 次：{dict(status)}。六方法全部完成的配对条件 {len(cases)} 个。',
        f'输入准备错误 {len(study.get("preparation_errors",[]))} 个。未完成不按零计；失败不从表中隐去；只在完整六方法配对条件内计算汇总。',
        '', '固定每 DC 2 ToR、每 ToR 4 server、每 server 4 GPU；2/3/4 DC 对应64/96/128 GPU。',
        '服务器共享上联400 Gbps；Leaf–Spine/网关上联1.6 Tbps，单向700ns；DC对直连WAN100 Gbps、单向50us。每通信对唯一固定路径，不启用随机ECMP。',
        '主指标为事件回放所得完整算子时间：包含真实数据、credit/ACK、排队、DCQCN/PFC和机内alpha/beta计时；不计规划CPU，不预定各流实际起始时刻，不加全局stage barrier。',
        'QP没有可直接复用的消息接口，因此按用户要求保留fresh-QP且不做QP敏感性；不计对象创建/建连开销，但每条消息的拥塞状态重置是局限。',
        '', 'FAST-SimAI是FAST无权Kuhn规则在本实验入口的对照，并非原生FAST二进制复现。FAST及四改进方法共用整数balance、NW补齐、机内计时和依赖回放。Spreadout直接发送原GPU矩阵，使用自身循环轮次协议，不做FAST的balance/dispatch；六者并非只有匹配规则不同。',
        'Stage-q*/Capacity-q*只查最大可行阈值；Stage-4q/Capacity-4q最多查4个阈值，仍保留冻结的候选构造及交替环选择。Stage只看本轮代价；Capacity还用剩余容量势函数。这里没有实现新的资源预算算法。',
        '', '合成输入保持每rank发送总量固定，严格均衡及条件目标热点Zipf(alpha0.8/1.2)，seed1101–1103。4MiB/rank为主档，4DC另测1/16MiB；整数缩放导致实际略小，精确字节见features.json。',
        '真实输入只使用采集的专家选择；源GPU重新组批、每副本8192B、seed1101等分专家放置是建模假设。固定首/中/末层、三个不重叠文件窗口、64token/GPU。96GPU仅Kimi满足本轮等分专家放置，其余不是失败而是N/A。',
        '', '## 2. 运行配置异常及处理','',
        '首轮5us PFC暂停计时下，四个拓扑计划在网关出现Headroom full并超时。逐跳追踪确认某266240B流在GPU/NV/ToR/Spine已完整发送，WAN只输出99000B，不能把它当作正常算法完成时间。',
        '仅增大输出队列并未解决；原后端PFC暂停租期会自动到期，但MMU仍标记paused，未持续刷新。统一改用configs/ns3-leafspine.conf的10ms保守暂停租期，保留显式提前resume、32MiB缓冲和原ECN/DCQCN，再对所有方法重跑。该值是仿真配置，不声称校准了物理硬件，也不作为算法贡献。',
        '旧超时和诊断保留在attempt-0001及diagnostics；本表只用主入口当前配置登记的结果。不可将旧超时视作无限加速收益。',
        '', '## 3. 完整配对条件的描述性汇总','',
        '倍率定义：基线完整算子时间 / 对应方法完整算子时间；大于1表示更快。几何均值不代表总体置信区间，不把deterministic重复运行当独立样本。',
        '', '| 方法 | 完整配对数 | 对FAST几何均值 | 对Spreadout几何均值 | 对FAST最小–最大 | 慢于FAST条件数 |',
        '|---|---:|---:|---:|---:|---:|']
    def fmt(value):return '—' if value is None else f'{value:.3f}'
    for method,label in zip(METHODS,LABELS):
        a=aggregate[method];text.append(f'| {label} | {a["paired_cases"]} | {fmt(a["speedup_fast_geomean"])}× | {fmt(a["speedup_spreadout_geomean"])}× | {fmt(a["minimum"])}–{fmt(a["maximum"])} | {a["slower_than_fast"]} |')
    text+=['','该汇总混合了当前已完成条件，仅作执行快照；正式结论应分开DC数、分布、规模、真实模型和背景压力，避免先完成条件造成选择偏差。', '', '## 4. 各条件完整算子时间','',
        '以下均为ms；“未完成”包括仍排队/尚未登记，失败显示状态。表中每格不是stage数。精确阶段数、规划CPU、WAN占用等见summary.csv及每方法v2_result.json。',
        '', '| 条件 | FAST | Spreadout | Stage-q* | Stage-4q | Capacity-q* | Capacity-4q |','|---|---:|---:|---:|---:|---:|---:|']
    for c in study['cases']:
        values=[]
        for m in METHODS:
            row=index.get((c['name'],m))
            values.append(f'{row["communication_ns"]/1e6:.3f}' if row and row.get('success') else row['status'] if row else '未完成')
        text.append('| '+c['name']+' | '+' | '.join(values)+' |')
    if cases:
        import matplotlib;matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        chosen=[c['name'] for c in study['cases'] if c['name'] in cases][:12]
        fig,ax=plt.subplots(figsize=(max(9,len(chosen)*.8),5));x=np.arange(len(chosen));width=.12
        for i,(m,label) in enumerate(zip(METHODS,LABELS)):
            ax.bar(x+(i-2.5)*width,[index[c,m]['communication_ns']/1e6 for c in chosen],width,label=label)
        ax.set_xticks(x,chosen,rotation=60,ha='right',fontsize=7);ax.set_ylabel('Operator completion time (ms)')
        ax.set_title('First completed paired cases in fixed campaign order (preview)');ax.legend(fontsize=7,ncol=3)
        fig.tight_layout();fig.savefig(assets/'paired_completion_preview.png',dpi=160);plt.close(fig)
        text+=['','## 5. 图：已完成条件预览','','按预定矩阵顺序取前12个完整配对条件，并非挑选收益最大的条件；纵轴线性。','','![完整算子时间](assets/paired_completion_preview.png)']
    text+=['','## 6. 数据文件与诊断边界','',
        '- study.json：计划、成功/失败和原始attempt目录；summary.csv：每次已登记结果。',
        '- matrix/matrix.npy及manifest：完整字节矩阵与来源；features.json：真实倾斜度/DC字节；planner_topology.json及routes.csv：规划/后端共同路径。',
        '- schedules/*：真实字节、补齐/分解、所选系数、原始来源交付映射及stage_records；本轮冻结规划器源哈希见study.json。',
        '- 每attempt的fct.txt、native.csv.events.csv：流级ACK及依赖事件；background_events.csv：独立背景消息释放/接收/ACK。',
        '- native_wan.csv：包级WAN占用及有效数据字节；native_wan_queue.csv和qlen.txt：WAN事件/所有交换机5us队列采样；pfc.txt/cnp.txt：PFC与CNP。',
        '- stage_skew.csv：仅真实活跃流的启动/ACK错位、实际dispatch错位；空网络stage的启动/ACK错位为null，不计入活跃均值。',
        '- original_source_delivery.csv：按原始源GPU和最终目的GPU逐字节验守恒；source_threshold_times.csv：10/50/90/100%交付时间。',
        '- rank_progress.csv：实际流事件；progress_quantiles.csv：发送slot前缀（ACK加skip-empty规则推导）、接收可见性前缀及真实redistribution前缀，三者不混用。',
        '- wan_bins_100us.csv：前景/背景有效负载吞吐；other使用控制包wire字节，不是有效数据吞吐。最后不足窗口按实际时长归一化。',
        '', '当前未做：新资源预算算法、QP复用敏感性、真实机内包级/计算模型、对所有拓扑或真实流量分布的普适证明。单rank阶段差距更小或WAN更忙本身都不等于算子更快。',
        '', '运行/恢复命令（同一输入和代码未改变时）：', '', '```bash',
        f'python3 tools/run_v2_study.py --out {root} --workers 3 --timeout 120',
        f'python3 tools/summarize_v2_study.py --out {root}', '```',
        '', '未来改变流量生成或字节适配器实现应另建输出目录；当前缓存尚未强制绑定这部分源码哈希。']
    (root/'RESULTS.md').write_text('\n'.join(text)+'\n')
    (root/'paired_aggregate.json').write_text(json.dumps(aggregate,indent=2)+'\n')
    print(dict(planned=study['planned_runs'],recorded=len(rows),paired=len(cases),status=dict(status)))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    generate(parser.parse_args().out.resolve())
