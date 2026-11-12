#!/usr/bin/env python3
"""Independently audit frozen retest artifacts and generate a bounded result report."""
import argparse
import csv
import hashlib
import json
import math
import pathlib
import statistics
import subprocess

import baseline_control
import corrected_study as c


def gm(values):
    return math.exp(statistics.mean(math.log(x) for x in values))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=pathlib.Path)
    parser.add_argument('--report', required=True, type=pathlib.Path)
    parser.add_argument('--guide', type=pathlib.Path, help='refresh only the marked latest-data appendix')
    args = parser.parse_args()
    folder = args.input
    meta = json.loads((folder/'metadata.json').read_text())
    assert meta['complete'] and meta['conditions'] == 78 and meta['outputs'] == 390 and meta['planner_unit_bytes'] == 1
    for path, digest in meta['source_sha256'].items():
        assert hashlib.sha256((c.ROOT/path).read_bytes()).hexdigest() == digest, path
        if path.startswith('nvidia/'):
            original = subprocess.check_output(['git', 'show', '0712d29:'+path], cwd=c.ROOT)
            assert hashlib.sha256(original).hexdigest() == digest, path
    inputs = {}
    for path in folder.glob('*.input.json'):
        saved = json.loads(path.read_text()); info = saved['metadata']
        data, regen = c.traffic(meta['n'], meta['g'], info['kind'], info['batch_tokens_per_gpu'], info['seed'])
        assert saved == dict(data=data, metadata=regen, features=c.input_features(data, meta['n'], meta['g']))
        inputs[path.name[:-len('.input.json')]] = saved
    assert len(inputs) == 26
    rows = json.loads((folder/'summary.json').read_text())
    assert len(rows) == 390
    index = {(row['case'], row['method']): row for row in rows}
    assert len(index) == len(rows)
    native = {row['case']: row for row in rows if row['method'] == 'native-host'}
    totals = {}
    for row in rows:
        data = inputs[row['case'].rsplit('-p', 1)[0]]['data']
        raw = json.loads((folder/(row['case']+'-'+row['method']+'.json')).read_text())
        if row['method'] == 'spreadout':
            metrics = baseline_control.audit(raw, data, 6, 4, c.UNIT)
            resources = metrics['resource_totals']
        else:
            assert raw['delivered_matrix'] == [amount*c.UNIT for amount in data]
            assert raw['payload_unit_bytes'] == 1
            if row['method'] == 'native-host':
                assert raw['native_replay_equal']
                assert math.isclose(c.native_profile_us(raw), raw['native_profile_ms']*1000, abs_tol=1e-9, rel_tol=1e-12)
            fresh = c.evaluate(dict(raw), raw['topology_snapshot'])
            for name in ('balance_us', 'intrinsic_us', 'dispatch_us', 'network_stages_us', 'total_us'):
                assert fresh[name] == raw[name], (row['case'], row['method'], name)
            # Any execution order is allowed, but capacity reconstruction is exact.
            residual = raw['padded_matrix'][:]
            for p, step in zip(raw['permutations'], raw['steps']):
                assert sorted(p) == list(range(6)) and step['alpha'] > 0
                if row['method'] in ('joint-stage', 'joint-capacity'):
                    assert step['alpha'] == min(residual[s*6+d] for s, d in enumerate(p))
                for s, d in enumerate(p):
                    residual[s*6+d] -= step['alpha']
                    assert residual[s*6+d] >= 0
            assert not any(residual)
            if row['method'] != 'native-host':
                baseline = json.loads((folder/(row['case']+'-native-host.json')).read_text())
                pending = c.joint_resource.residual.initial_pending(baseline)
                for p, step, payload in zip(raw['permutations'], raw['steps'], raw['payload_units']):
                    predicted = [0]*24
                    for s, d in enumerate(p):
                        if s == d:
                            continue
                        for u in range(4):
                            position = (s*6+d)*4+u
                            predicted[s*4+u] = min(step['alpha'], pending[position])
                            pending[position] -= predicted[s*4+u]
                    assert predicted == payload
                assert not any(pending)
                if row['method'] in ('joint-stage', 'joint-capacity'):
                    planned = json.loads((folder/(row['case']+'-'+row['method']+'.plan.json')).read_text())
                    assert planned['permutations'] == raw['permutations']
                    assert len(planned['trace']) == raw['stage_count']
                    for trace, step, network in zip(planned['trace'], raw['steps'], raw['network_stages_us']):
                        assert trace['capacity'] == step['alpha']
                        assert math.isclose(trace['time_us'], network, abs_tol=1e-8, rel_tol=1e-12)
            resources = raw['resource_totals']
        assert raw['total_us'] == row['total_us']
        if row['case'] in totals:
            assert resources == totals[row['case']]
        totals[row['case']] = resources
    assert len(totals) == 78

    methods = ('joint-stage', 'joint-capacity', 'joint-capacity-descending', 'spreadout')
    labels = {'joint-stage': '当前阶段/服务量', 'joint-capacity': '容量势函数/服务量',
              'joint-capacity-descending': '容量势函数后再降序', 'spreadout': 'GPU cyclic spreadout-like'}
    def selected(method, kind=None):
        return [r for r in rows if r['method'] == method and (kind is None or r['kind'] == kind)]
    def ratios(rs, reference='native-host'):
        return [index[r['case'], reference]['total_us']/r['total_us'] for r in rs]
    def counts(values):
        return (sum(x > 1+1e-9 for x in values), sum(abs(x-1) <= 1e-9 for x in values),
                sum(x < 1-1e-9 for x in values))
    lines = [
        '# 修正负载和原生FAST基线后的重测', '',
        '2026-11-13。以下结论仅适用于本次冻结的合成输入、原生CPU数据量计划和统一解析计时模型。没有GPU实测，也没有证明在线端到端加速。', '',
        '## 1. 审查结论与修复边界', '',
        '| 原问题 | 本次处理 | 核验 |', '|---|---|---|',
        '| 旧矩阵复制源GPU行，所有balance为0，排除了机内需求 | 重新逐源采样，保留同服务器非对角需求，自身expert路由另存 | 26个输入可精确重生成；所有随机输入30/30个远端tile存在源GPU负载不均 |',
        '| 仿真pipeline2被称为完整原生FAST | 直接编译未经修改的NVIDIA CPU源码，baseline调用原始run_scheduler | 源码/头文件逐个等于0712d29；78次原生计划与适配器重放的完整scheduling_result_t内存相等 |',
        '| 重放要求每轮q等于当前min，拒绝合法重排 | 执行核验改成0<q<=min，且必须最终完整重构 | 新增降序重排回归；生成算法仍严格取实际min |',
        '| uint64输入可能越过原生uint系数更新安全域 | 适配器在原生调用前保守限制总单位数<=UINT_MAX，不改上游 | 2服务器×8GPU、跨机每格1e9的输入由SIGSEGV变为明确拒绝；正式输入未触及此范围 |',
        '| 先按token向量数做整数balance再乘8192B，不等价于按字节规划 | 在原生初始化之前转换为字节矩阵，原生/候选分解、restore与物理计价均使用字节 | 新增直接字节输入的原始计划等价回归；最终目录不混入此前token单位初测 |', '',
        '基线名称为 **FAST-Host**：项目原始CPU数据量规划，包含balance、server间补齐/BvN、系数降序执行、restore/dispatch与intrinsic需求。它不是“仅Kuhn”，也不是CUDA真实运行：rank0的CPU元数据、token偏移、GPU kernel、chunk/credit/signal及拥塞不等同于实测验证。另有8GPU/服务器非零负载的交付与profiler回归，不把它计入主面板。', '',
        '旧结果保留作开发记录；此前“66组都赢”等结论不能继续作为真实MoE或完整FAST运行的证据。本轮数据独立，不与旧面板汇总。', '',
        '## 2. 输入、拓扑与统一计时', '',
        '主面板固定3个DC，每DC 2台服务器，每服务器4GPU。26个GPU级矩阵×3种拓扑参数=78组条件；每组5种方案，共390份输出。两档每源GPU token数为480/3840；随机种子1101/1102/1103，严格均衡只取一个种子。每token向2个不同expert发送8192B向量（4096维×2B），每GPU放1个expert；不含模型计算、expert容量限制、丢弃token或MoE真实trace。', '',
        '| 输入族 | 明确定义 | 每档输入数 |', '|---|---|---:|',
        '| balanced | 每源到每expert含自身均为40/320次；自身从通信矩阵去除但另存 | 1 |',
        '| edge_zipf | 每个非自身GPU对独立抽取大小，值1..10000、概率正比于size^-0.8；仅全局归一到均衡通信总量 | 3 |',
        '| token_uniform | 每源逐token独立抽取两个不同expert，均匀概率；有限样本不等于严格均衡 | 3 |',
        '| token_zipf | expert热度正比于rank^-0.9，所有源共享热点但独立采样，不复制目的计数 | 3 |',
        '| token_affinity | 0.5全局Zipf+0.5源域Zipf，4个源域；独立expert排列和逐token采样 | 3 |', '',
        'edge_zipf是消息大小模型，不宣称满足逐源Top-2 token语义；480/3840只是它匹配通信预算的规模标记。expert排列不依据拓扑优化。token族严格满足“每源通信量+自身路由=2×token数”，自身路由不被重分配为远端流量。输入文件保存向量计数；在调用原生规划前乘8192，所有原生/候选载荷和系数都以字节记录，避免整数取整造成隐藏的规划量化差异。', '',
        '| profile | server有向资源 | gateway有向资源 | DC间有向资源 | 路径单向传播 |', '|---|---|---|---|---|',
        '| p0 | 400Gbps | 400Gbps | 全部100Gbps | DC内1.4us；跨DC 52.8us |',
        '| p1 | 400Gbps | 100Gbps | 全部100Gbps | 同p0 |',
        '| p2 | 400Gbps | 400Gbps | DC0↔1为50、0↔2为200、1↔2为100Gbps | 对应202.8、7.8、52.8us；DC内1.4us |', '',
        '所有方法使用同一静态单路径模型：stage网络时间=活动路径最大单向传播+最大资源累计字节/容量。这是容量近似，不是逐流完成时间或包级精确仿真；最大传播和最大工作量可能来自不同流。RTT不另加一次。', '',
        '机内统一使用已有H100解析参数：每个非空cyclic round为0.1us+2.3us/MB×最大消息大小；balance按原生代码将各目标服务器的搬运合并，intrinsic与dispatch均计入。先独立使用原生profiler的380GB/s机内、48.85GB/s端点、无启动开销模型进行等价核验，再用上述共同WAN模型评价所有计划；两套计时不能混为一套。', '',
        '完整计时（非空K）为：balance + max(intrinsic,network[0]) + Σmax(dispatch[k−1],network[k]) + dispatch[K−1]。因此没有忽略balance、机内需求或末尾dispatch，也不把通信阶段简单串行总和冒充完整操作时间。', '',
        '## 3. 冻结算法与总体结果', '',
        '未为本轮数据调参、改匹配公式或筛种子。已有两个候选：当前阶段版本最小化“本轮网络时间/真实服务字节”；容量势函数版本最小化“本轮网络时间+下轮最大剩余资源工作量−本轮最大剩余资源工作量”，再除以真实服务字节。均使用原有最多4个阈值、带权指派及下降方向交替环搜索，非全排列枚举，不声称全局最优。共同保留原生补齐/balance/restore；候选按构造顺序执行；额外降序版本只作执行顺序消融。', '',
        '加速比定义为FAST-Host完整预计通信时间/候选完整预计通信时间；>1为更快。“GM”是逐条件加速比的几何平均，不是时间下降百分比。', '',
        '| 方法 | 优/平/劣（78组） | GM加速比 | 最差 | 最好 |', '|---|---:|---:|---:|---:|',
    ]
    for method in methods:
        rs = ratios(selected(method))
        lines.append(f'| {labels[method]} | {"/".join(map(str, counts(rs)))} | {gm(rs):.3f}× | {min(rs):.3f}× | {max(rs):.3f}× |')
    lines += ['', '| 输入族 | 条件数 | 当前阶段GM | 容量势函数GM | 容量势函数降序GM |', '|---|---:|---:|---:|---:|']
    for kind in c.FAMILIES:
        rs = selected('joint-capacity', kind)
        values = [gm(ratios(selected(method, kind))) for method in methods[:3]]
        lines.append(f'| {kind} | {len(rs)} | '+ ' | '.join(f'{v:.3f}×' for v in values)+' |')
    lines += ['', '下面给出时间数值，避免将阶段数与时间混淆。取各族条件的算术平均，单位ms；它与上表GM不是同一个统计量。', '',
              '| 输入族 | FAST-Host | 当前阶段 | 容量势函数 | spreadout-like | FAST K范围 | 容量势函数K范围 |', '|---|---:|---:|---:|---:|---:|---:|']
    for kind in c.FAMILIES:
        base = [r for r in native.values() if r['kind'] == kind]; cap = selected('joint-capacity', kind)
        times = [statistics.mean(r['total_us'] for r in group)/1000 for group in
                 (base, selected('joint-stage', kind), cap, selected('spreadout', kind))]
        lines.append(f'| {kind} | '+' | '.join(f'{t:.3f}' for t in times)+f' | {min(r["K"] for r in base)}–{max(r["K"] for r in base)} | {min(r["K"] for r in cap)}–{max(r["K"] for r in cap)} |')
    lines += ['', '## 4. balance和机内需求是否真的恢复', '',
              '| 输入族 | 不均衡远端tile数（总30） | balance范围us | intrinsic范围us |', '|---|---:|---:|---:|']
    for kind in c.FAMILIES:
        rs = [r for r in native.values() if r['kind'] == kind]
        lines.append(f'| {kind} | {min(r["features"]["unbalanced_tiles"] for r in rs)}–{max(r["features"]["unbalanced_tiles"] for r in rs)} | '+
                     f'{min(r["balance_us"] for r in rs):.3f}–{max(r["balance_us"] for r in rs):.3f} | '+
                     f'{min(r["intrinsic_us"] for r in rs):.3f}–{max(r["intrinsic_us"] for r in rs):.3f} |')
    lines += ['', '严格均衡控制组balance为0是预期，不再用它代表随机MoE。token_uniform虽然期望均匀，有限采样后也触发balance。共享Zipf热点下，固定每源token数使全行总量相近，但不同目标服务器tile仍有卡间差异；balance相对WAN很小并非遗漏。这些是有明确假设的合成路由，非零balance本身不构成真实性证明。', '',
              '## 5. 收益来源及仍然存在的退化', '']
    cap = selected('joint-capacity')
    smaller = sum(r['K'] < native[r['case']]['K'] for r in cap)
    equal = sum(r['K'] == native[r['case']]['K'] for r in cap)
    lines += [f'容量势函数版本在{smaller}组减少stage，在{equal}组stage不变，在{78-smaller-equal}组增加stage；算法没有普遍不增加stage的保证。严格均衡一直是5个stage仍有收益；token_zipf许多条件仍为23个stage也有收益。因此收益不能简单归因为少启动几轮。共同固定路径上累计资源字节完全相同，差异来自阶段之间的瓶颈工作重叠、传播付费次数及真实数据/虚拟容量切分。', '',
              '| profile | 容量势函数GM |', '|---|---:|']
    for profile in range(3):
        lines.append(f'| p{profile} | {gm(ratios([r for r in cap if r["profile"] == profile])):.3f}× |')
    lines += ['', 'p0本身已有共享WAN瓶颈，并非无拓扑影响的均匀网络。本次p0收益大于更异构的p2，不能据此宣称“异构越强收益越大”；热点、共享工作重叠和分解切分共同决定可优化空间。', '',
              '当前阶段版本的较明显退化：token_affinity-b480-seed1101-p2。以下单位us。', '',
              '| 方案 | K | 传播和 | 序列化和 | 完整时间 |', '|---|---:|---:|---:|---:|']
    loss_case = 'token_affinity-b480-seed1101-p2'
    for method in ('native-host',)+methods[:3]:
        r = index[loss_case, method]
        lines.append(f'| {method} | {r["K"]} | {r["alpha_us"]:.3f} | {r["serial_us"]:.3f} | {r["total_us"]:.3f} |')
    lines += ['', '当前阶段版本在这里没有减少K，却把全程传播和序列化和都提高，完整时间变差；这证明只优化本轮服务效率不能保证剩余调度更优。容量势函数保留对后续关键资源工作的考量，本例同时降低传播和序列化和，并减少1轮；这不构成普遍改善保证。', '',
              '独立的重排退化：token_zipf-b480-seed1102-p2。单位us。', '',
              '| 方案 | K | 传播和 | 序列化和 | 完整时间 |', '|---|---:|---:|---:|---:|']
    for method in ('native-host', 'joint-capacity', 'joint-capacity-descending'):
        r = index['token_zipf-b480-seed1102-p2', method]
        lines.append(f'| {method} | {r["K"]} | {r["alpha_us"]:.3f} | {r["serial_us"]:.3f} | {r["total_us"]:.3f} |')
    lines += ['', '把同一容量势函数分解再按系数降序执行，会在这个Zipf例中丢失部分收益，甚至略差于基线。原因不只是dispatch次序：虚拟补齐量存在时，原生restore按min(本轮容量,真实剩余量)发送；相同P/q集合被重排后，真实载荷在阶段间的切分也会改变，因而序列化总和可变。', '',
              'spreadout-like仅为GPU cyclic轮次基线，不声称复现NCCL实现。容量势函数对它的胜场为'+str(counts(ratios(cap, 'spreadout'))[0])+'/78，GM为'+f'{gm(ratios(cap, "spreadout")):.3f}×'+f'，最差{min(ratios(cap, "spreadout")):.3f}×。因此“比FAST-Host都快”不等于“所有其他调度器都被超过”。', '',
              '## 6. 在线开销：当前尚未达标', '',
              '| 记录对象 | 计时含义 | 中位数ms |', '|---|---|---:|']
    for method, scope in [('native-host', 'C++原始run_scheduler进程CPU，含CPU元数据与profiling；不含初始化/核验'),
                          ('joint-stage', 'Python plan循环进程CPU；不含初始State构造/原生balance-restore重放'),
                          ('joint-capacity', '同上')]:
        lines.append(f'| {method} | {scope} | {statistics.median(r["planner_us"] for r in selected(method)) / 1000:.3f} |')
    lines += ['', '这两类是不同语言、不同工作范围的CPU成本，不是严格可比的端到端规划延迟；spreadout的planner_us=0仅为未测占位，不代表零开销。通信表没有加上这些CPU成本。', '',
              '| 每GPU token数 | 容量势函数通信节省中位数ms | Python规划中位数ms | 通信节省超过Python成本的条件数 |', '|---|---:|---:|---:|']
    for batch in (480, 3840):
        rs = [r for r in cap if r['batch'] == batch]
        saved = [native[r['case']]['total_us']-r['total_us'] for r in rs]
        lines.append(f'| {batch} | {statistics.median(saved)/1000:.3f} | {statistics.median(r["planner_us"] for r in rs)/1000:.3f} | {sum(x > r["planner_us"] for x, r in zip(saved, rs))}/{len(rs)} |')
    lines += ['', '以上只是保守的成本量级诊断，不是假定CPU重叠或摊销后的在线实测。当前Python原型不支持“保留FAST在线优势且已经实现净加速”的结论；需要C++落地和统一规划计时。', '',
              '## 7. 复现、核验与结论边界', '',
              '```bash', 'make -C simulation runtime-host-build bvn-decompose-build',
              'python3 ds/decomposition/corrected_study.py --out /一个尚不存在的结果目录',
              'python3 ds/decomposition/corrected_analysis.py --input ds/decomposition/'+folder.name+' --report ds/decomposition/CORRECTED_RETEST_REPORT.md',
              'make -C simulation check bvn-check runtime-host-build bvn-decompose-build', '```', '',
              f'正式档案：[{folder.name}]({folder.name}/metadata.json)；[完整390行汇总]({folder.name}/summary.json)；输入、原生/候选计划和完整时序逐条件保存。分析脚本重新生成全部26个输入，核验源码指纹、上游源码一致性、78个原生profiler、390份输出的逐原GPU需求、辅助矩阵重构和资源字节守恒。独立只读审查覆盖初测档案和适配器；整数安全/字节单位修复后另行全量重跑，由新增回归与重算脚本核验，未混入旧输出，不冒称最终字节面板已经第二次独立人工审查。', '',
              '本轮能支持：在明确合成模型与固定3DC面板中，已有容量势函数方法的通信优化仍存在，且不依赖旧数据的零balance。不能支持：真实MoE、全体拓扑、所有随机样本、完整CUDA运行或在线净加速。78组含同矩阵的三种拓扑参数，不能当78个独立随机重复进行显著性推断。', '',
              '下一步优先级：①先将这一冻结候选迁入精简C++路径并测统一规划+通信总成本；②追加未用种子、8GPU服务器及源负载/路由强度扫描，保持真实交付审计；③保留上述退化例与spreadout不足区间，分析并发资源错配，而不是继续以旧面板的全胜作为普遍保证。', '']
    verification = folder/'verification.log'
    if verification.exists():
        log = verification.read_text()
        assert 'Ran 60 tests' in log and '\nOK\n' in log
        assert 'Topology schedule tests passed' in log and '5443 checks passed' in log
        lines += [f'本次完整测试：60项Python测试、Topology schedule tests、5443项独立BvN检查全部通过；[原始测试日志]({folder.name}/verification.log)。', '']
    args.report.write_text('\n'.join(lines))
    if args.guide:
        marker = '<!-- LATEST_DATA_APPENDIX -->'
        current = args.guide.read_text()
        if current.count(marker) != 1:
            raise ValueError('guide must contain exactly one latest-data marker')
        appendix = ['## 附录A：最新总体及逐条件数据', '',
                    '以下直接读取`corrected-byte-host-20261113/summary.json`生成。计时单位为ms；加速比=FAST-Host/候选。GM为逐条件加速比几何平均，不是算术平均时间之比。', '',
                    '| 方法 | 优/平/劣 | GM | 最差 | 最好 |', '|---|---:|---:|---:|---:|']
        for method in methods:
            rr = ratios(selected(method))
            appendix.append(f'| {method} | {"/".join(map(str, counts(rr)))} | {gm(rr):.4f}× | {min(rr):.4f}× | {max(rr):.4f}× |')
        appendix += ['', '### A.1 五类负载汇总', '',
                     '| 负载 | 条件数 | stage GM | capacity GM | capacity-descending GM | capacity对spreadout GM |',
                     '|---|---:|---:|---:|---:|---:|']
        for kind in c.FAMILIES:
            rr = selected('joint-capacity', kind)
            values = [gm(ratios(selected(method, kind))) for method in methods[:3]]
            values.append(gm(ratios(rr, 'spreadout')))
            appendix.append(f'| {kind} | {len(rr)} | '+' | '.join(f'{x:.4f}×' for x in values)+' |')
        appendix += ['', '### A.2 全部78条件（不筛胜场）', '',
                     '每个方法单元格为“完整预计通信时间ms / K”；K对BvN为stage数，对spreadout为活跃round数。CPU列仅为capacity的Python计划循环进程CPU，不含初始State和原生回放，不是总墙钟。batch对token族为每GPU token数，对edge_zipf仅为通信预算规模。', '']
        for profile in range(3):
            appendix += [f'#### p{profile}', '',
                         '| 负载 | batch | seed | FAST ms/K | stage ms/K | capacity ms/K | cap-desc ms/K | spread ms/K | capacity加速 | capacity CPU ms |',
                         '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
            for base in (r for r in rows if r['method'] == 'native-host' and r['profile'] == profile):
                cells = [f'{index[base["case"], method]["total_us"]/1000:.6f} / {index[base["case"], method]["K"]}'
                         for method in ('native-host', 'joint-stage', 'joint-capacity', 'joint-capacity-descending', 'spreadout')]
                caprow = index[base['case'], 'joint-capacity']
                appendix.append(f'| {base["kind"]} | {base["batch"]} | {base["seed"]} | '+
                                ' | '.join(cells)+f' | {base["total_us"]/caprow["total_us"]:.6f}× | {caprow["planner_us"]/1000:.3f} |')
            appendix.append('')
        appendix += ['### A.3 补充字段与反例入口', '',
                     '各stage的q、真实payload、dispatch矩阵、累计传播和资源序列化，以及每个输入的卡间不均衡和自身路由计数，均在完整JSON/CSV及逐条件文件中。两组详细退化见[修正报告§5](CORRECTED_RETEST_REPORT.md#5-收益来源及仍然存在的退化)。', '',
                     '本附录仅反映这78条件，不声称所有未来输入都优；最新方法仍未达到在线净加速目标。', '']
        args.guide.write_text(current.split(marker)[0]+marker+'\n\n'+'\n'.join(appendix))
    with (folder/'summary.csv').open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=[key for key in rows[0] if key != 'features'], lineterminator='\n')
        writer.writeheader(); writer.writerows({k: v for k, v in row.items() if k != 'features'} for row in rows)
    print('VERIFIED: 26 regenerated inputs, 78 conditions, 390 outputs, original host source parity and native profiles')
    print(args.report)


if __name__ == '__main__':
    main()
