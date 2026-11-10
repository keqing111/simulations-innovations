#!/usr/bin/env python3
"""Independent bandwidth/time policies: fixed inputs, native runs and resource audit."""
import argparse
from collections import defaultdict
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import subprocess
import tarfile

# ============================================================================
# independent/study.py：独立策略的固定输入生成、运行、重放与独立审计。
#
# 这个脚本不实现策略，它是“实验协议 + 对账器”：
#   1. 预先固定 2 个机制输入 + 24 个扫描配置（规模/负载/容量/WAN 组合）；
#   2. 用同一二进制生成矩阵，并把矩阵摘要、源码身份写进 study.json；
#   3. 每个配置跑一次 batch（七种方法 × 一次暖机 + 三次轮转）；
#   4. audit() 从原始 JSON 独立重建每阶段资源字节、alpha/serial、pipeline
#      组合和总字节，并可选地重放原生二进制做逐字段比较。
#
# 审计只复用冻结引擎的机内 balance/intrinsic/dispatch 数值，不重写原生
# 机内调度器；跨服务器需求守恒、共享资源计价和 pipeline 组合是独立计算的。
# 计时样本只有三次，只作诊断，不用于尾延迟/生产可用性声明。
# ============================================================================

ROOT = Path(__file__).resolve().parent
# 通过 importlib 加载同目录 build.py，复用其中的冻结路径、哈希和构建逻辑；
# 这样 study.py 在任何工作目录下都能确定“以哪份源码/引擎为准”。
spec = importlib.util.spec_from_file_location('independent_build', ROOT/'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
# 七种方法的顺序是写出 JSON/审计的固定顺序，batch 输出以方法名为键。
METHODS = ['native', 'bandwidth', 'time', 'maxmin', 'maxmin-bandwidth', 'maxmin-time', 'spreadout']
sha = builder.sha


def save(path, value):
    # 原子写入：先写 .tmp 再 rename，避免中途崩溃留下半截 JSON。
    # allow_nan=False 让非有限数值在写盘时立刻失败，而不是产生非法 JSON。
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def own_files():
    # 登记本模块自身源码：排除 build/结果/缓存目录，只收集 C/C++/Python。
    # 这些文件会进入 source.tar.gz，审计时与 study.json 中的哈希对账。
    return sorted(p for p in ROOT.rglob('*') if p.is_file()
                  and p.suffix in ['.cpp', '.cc', '.h', '.py']
                  and not any(s in p.relative_to(ROOT).parts for s in ['build', 'results', '__pycache__']))


def identity():
    # 实验身份 = 自身源码哈希 + 冻结引擎摘要 + 编译选项 + 编译器版本。
    # 任一变化都意味着应使用新的输出目录，旧结果不能与新结果拼接。
    engine = json.loads((builder.FROZEN/'build.json').read_text())
    return dict(files={str(p.relative_to(ROOT)): sha(p) for p in own_files()},
                engine=engine['identity']['digest'], flags=builder.FLAGS,
                compiler=subprocess.check_output(['g++', '--version'], text=True).splitlines()[0])


def cases(smoke=False):
    # 两个机制输入：6 server、1 GPU、3 DC 各 2 台、非对角 100MB。
    # 带宽机制减少网关/WAN 容量，时延机制让容量充足，用于分别触发两种策略。
    result = [dict(id='mechanism-bandwidth', group='bandwidth-mechanism', n=6, g=1, kind='mechanism',
                   seed=0, mib=0, wan_us=5000, server_bw=400, gateway_bw=400, wan_bw=100, dc=[0,0,1,1,2,2]),
              dict(id='mechanism-delay', group='delay-mechanism', n=6, g=1, kind='mechanism',
                   seed=0, mib=0, wan_us=5000, server_bw=400, gateway_bw=800, wan_bw=800, dc=[0,0,1,1,2,2])]
    if not smoke:
        # 扫描网格：n=6/12、每 server 4 GPU；balanced + 两个 Zipf 种子；
        # shared（网关/WAN 100Gbps）与 capacity-sufficient（每 DC 服务器数×400）
        # 两种资源条件；WAN 单向 50us/5000us。共 2×3×2×2=24 个配置。
        for n in [6, 12]:
            for kind, seed in [('balanced', 0), ('zipf', 860201), ('zipf', 860203)]:
                for profile in ['shared', 'capacity-sufficient']:
                    for wan in [50, 5000]:
                        capacity = 400 if profile == 'shared' else n//3*400
                        c = dict(group='scan', n=n, g=4, kind=kind, seed=seed, mib=16,
                                 wan_us=wan, server_bw=400, gateway_bw=capacity,
                                 wan_bw=100 if profile == 'shared' else capacity, dc=[s*3//n for s in range(n)])
                        c['id'] = f'n{n}g4-{kind}-s{seed}-{profile}-w{wan}'
                        result.append(c)
    return result


def matrix_key(c):
    # 相同 (n,g,kind,seed,mib) 的配置共享同一矩阵；扫描里不同容量/WAN 的
    # 配置因此复用同一负载，不是 24 份独立随机输入。
    return f'n{c["n"]}g{c["g"]}-{c["kind"]}-s{c["seed"]}-m{c["mib"]}'


def matrix(c, binary):
    # 生成行优先 N*G × N*G 字节矩阵：
    #   mechanism：非对角全部 100,000,000，无对角线；
    #   balanced ：所有非对角单元相等，使每行/列总量相同；
    #   zipf     ：调用同一二进制内置的 Zipf(.9) 生成器，再把非对角权重
    #              归一化到目标总量（最大余数法补齐整数舍入）。
    # 目标总量按“平均每 rank 约 mib MiB”计算，并保持每行恰好 (r-1) 个非零。
    r = c['n']*c['g']
    if c['kind'] == 'mechanism':
        return [0 if i == j else 100000000 for i in range(r) for j in range(r)]
    target = r * ((c['mib']*1024**2)//(r-1)*(r-1))
    if c['kind'] == 'balanced':
        return [0 if i == j else target//(r*(r-1)) for i in range(r) for j in range(r)]
    weights = json.loads(subprocess.check_output([str(binary), '--zipf', str(c['seed']), str(r*r)], text=True))
    for i in range(r):
        weights[i*r+i] = 0
    total = sum(weights)
    values = [v*target//total for v in weights]
    # 按小数余数从大到小补 1，使总和精确等于 target；余数相同按索引稳定排序。
    order = sorted(range(r*r), key=lambda k: -(weights[k]*target % total))
    for k in order[:target-sum(values)]:
        values[k] += 1
    return values


def command(binary, c, method='batch', repeats=3):
    # 固定 CLI 形状：N G METHOD WAN_US SERVER_BW GATEWAY_BW WAN_BW DC_CSV REPEATS。
    return [str(binary), str(c['n']), str(c['g']), method, str(c['wan_us']), str(c['server_bw']),
            str(c['gateway_bw']), str(c['wan_bw']), ','.join(map(str, c['dc'])), str(repeats)]


def invoke(binary, c, data):
    # 通过 stdin 传入矩阵；任何非零退出码都视为失败，不解析部分输出。
    done = subprocess.run(command(binary, c), input=' '.join(map(str, data)), text=True, capture_output=True, timeout=180)
    if done.returncode:
        raise RuntimeError(done.stderr)
    return json.loads(done.stdout)


def close(a, b):
    # 模型对账容差：相对 1e-11、绝对 1e-7 微秒，足以覆盖浮点求和顺序差异，
    # 但远小于任何有意义的策略差异；非有限值一律判失败。
    if not math.isfinite(a) or not math.isclose(a, b, rel_tol=1e-11, abs_tol=1e-7):
        raise ValueError(f'model mismatch: {a} != {b}')


def reconstruct(c, method, data, raw):
    # 从单方法原始 JSON 独立重建网络模型，返回 (指标行, 逐资源总字节)。
    # 任何“模型应该成立但实际不成立”的关系都抛异常，而不是修正后继续。
    n, g, dc = c['n'], c['g'], c['dc']
    r = n*g
    # 元数据必须与登记配置一致；payload_verified 为 false 表示原生实际派发
    # 与策略影子不一致，这种结果不能进入审计。
    if raw['method'] != method or raw['payload_verified'] is not True or raw['topology']['dc'] != dc:
        raise ValueError('policy/topology metadata')
    # 机内时间由冻结引擎给出，本脚本只检查它们是有限非负数；不独立重算。
    local_times = [raw['balance_us'], raw['intrinsic_us'], *raw['dispatch_us']]
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in local_times):
        raise ValueError('invalid local time: expected finite nonnegative balance/intrinsic/dispatch')
    delivered = [[0]*n for _ in range(n)]
    totals = defaultdict(int)
    costs = []
    wan_stages = 0
    for k, stage in enumerate(raw['stages']):
        # 原生 Birkhoff 阶段：map 长度 n 且是 0..n-1 的排列；spreadout 阶段
        # 是 GPU 级循环映射，长度 r。payload 始终按 GPU 展开为 r 个值。
        size = r if method == 'spreadout' else n
        if sorted(stage['map']) != list(range(size)) or len(stage['payload']) != r:
            raise ValueError('not a perfect matching')
        # 原生阶段必须有正的公共系数；spreadout 没有 Birkhoff 系数。
        if method != 'spreadout' and stage['freq'] <= 0:
            raise ValueError('nonpositive coefficient')
        # links 记录本阶段每个共享资源承载的真实字节；rates 记录对应容量。
        # 同一阶段内同一资源可能被多条流共享，因此必须累加后再取最大值。
        links = defaultdict(int)
        rates = {}
        alpha = local = 0
        def add(key, amount, bw):
            links[key] += amount
            rates[key] = bw
        for s, d in enumerate(stage['map']):
            if method == 'spreadout':
                # spreadout：阶段 k 的 GPU s 必须发给 (s+k)%r，payload 必须
                # 等于原始需求；同 server 的字节按机内 alpha+beta 估算。
                amount = stage['payload'][s]
                if d != (s+k) % r or amount != data[s*r+d]:
                    raise ValueError('spreadout payload')
                src, dst = s//g, d//g
                if amount and src == dst:
                    local = max(local, .1+amount*(8000/3600)*1e-6)
            else:
                # Birkhoff：第 s 个 server 的 g 个 GPU payload 共享系数 freq；
                # 每个 GPU 的发送量必须在 [0,freq] 内，src/dst 是 server 级。
                payload = stage['payload'][s*g:(s+1)*g]
                if any(v < 0 or v > stage['freq'] for v in payload):
                    raise ValueError('GPU quota')
                amount = sum(payload)
                src, dst = s, d
            # 自环和零发送不产生网络事件，也不进入 delivered/links。
            if src == dst or not amount:
                continue
            delivered[src][dst] += amount
            # 每条真实 server 对流量同时占用源出口和目的入口的 server 资源。
            add(f'server-out:{src}', amount, c['server_bw'])
            add(f'server-in:{dst}', amount, c['server_bw'])
            if dc[src] == dc[dst]:
                # 同 DC：只有路径传播 1.4us，无网关/WAN 资源。
                alpha = max(alpha, 1.4)
            else:
                # 跨 DC：增加两端网关资源和有向 DC 对 WAN 资源；
                # 传播 = 一次性 WAN 时延 + 2.8us（两端网关处理）。
                add(f'gateway-out:{dc[src]}', amount, c['gateway_bw'])
                add(f'gateway-in:{dc[dst]}', amount, c['gateway_bw'])
                add(f'wan:{dc[src]}:{dc[dst]}', amount, c['wan_bw'])
                alpha = max(alpha, c['wan_us']+2.8)
        # 串行化 = 本阶段所有活跃资源中 max(字节 / 容量)；0.008 把
        # bytes/Gbps 转成微秒（8 bit/byte，1e-3 s/ms，1e-6 s/us）。
        serial = max((.008*v/rates[e] for e, v in links.items()), default=0)
        # 与二进制输出逐项对账：资源字节/容量、alpha、serial、总通信时间、
        # 机内本地时间，以及顶层声明的资源容量。
        if stage['links'] != dict(links) or stage['bandwidths'] != rates:
            raise ValueError('shared resource bytes/capacity')
        for e, bw in rates.items():
            if raw['topology']['bandwidths'].get(e) != bw:
                raise ValueError('declared topology bandwidth')
        close(stage['alpha_us'], alpha)
        close(stage['serial_us'], serial)
        close(stage['communication_us'], alpha+serial)
        close(stage['local_us'], local)
        # 原生 pipeline2 的阶段时间是 max(通信, 机内本地开销)；spreadout 也
        # 采用同样的保守 max，但其阶段序列/系数不同。
        costs.append(max(alpha+serial, local) if method == 'spreadout' else alpha+serial)
        wan_stages += int(any(e.startswith('wan:') for e in links))
        for e, amount in links.items():
            totals[e] += amount
    # 需求守恒：对每个 server 对，从原始矩阵按块求和，必须与逐阶段累计的
    # delivered 完全相等，确保没有丢字节、重复搬或把虚拟补齐算成真实流量。
    for s in range(n):
        for d in range(n):
            if s != d and delivered[s][d] != sum(data[(s*g+i)*r+d*g+j] for i in range(g) for j in range(g)):
                raise ValueError('lost or duplicated real demand')
    if method == 'spreadout':
        # spreadout 不经过 pipeline2，总时间就是各阶段时间之和。
        total = sum(costs)
    else:
        # 原生 pipeline2 的简化形式（与 hooks 暴露的字段一致）：
        #   balance + 首阶段 max(intrinsic, cost0)
        #   + Σ_{k>=1} max(cost_k, dispatch_{k-1})
        #   + 最后阶段 dispatch。
        # 阶段数受 N² 上界约束；constructions 不得超过阶段数（每阶段最多一次
        # 候选构造），用于发现“偷偷多次枚举”的实现。
        if len(costs) > n*n or len(raw['dispatch_us']) != len(costs):
            raise ValueError('stage/dispatch bound')
        total = raw['balance_us'] + (max(raw['intrinsic_us'], costs[0]) +
                 sum(max(costs[k], raw['dispatch_us'][k-1]) for k in range(1, len(costs))) +
                 raw['dispatch_us'][-1] if costs else raw['intrinsic_us'])
        if raw['stats']['stages'] != len(costs) or raw['stats']['constructions'] > len(costs):
            raise ValueError('single candidate budget')
    close(raw['total_us'], total)
    close(raw['inter_bytes'], sum(map(sum, delivered)))
    # 规划计时只有三个样本；拒绝缺失/非有限/负值，但 median/p95 仅作诊断。
    samples = raw['timing_samples_us']
    if len(samples) != 3 or any(not math.isfinite(v) or v < 0 for v in samples):
        raise ValueError('planning diagnostic samples')
    planning = statistics.median(samples)
    ordered = sorted(samples)
    # 指标行只包含可比较的确定量 + 规划诊断量；逐资源总字节单独返回，
    # 供 audit 验尸“不同方法是否搬运了相同总量”。
    return dict(**c, method=method, total_ms=total/1000, alpha_ms=sum(s['alpha_us'] for s in raw['stages'])/1000,
                serial_ms=sum(s['serial_us'] for s in raw['stages'])/1000, stages=len(costs), wan_stages=wan_stages,
                inter_bytes=sum(map(sum, delivered)), planning_median_us=planning, planning_p95_us=ordered[-1],
                diagnostic_plus_planning_ms=(total+planning)/1000, accepted=raw['stats']['accepted'],
                matching_calls=raw['stats']['matching_calls'], coefficient_rejections=raw['stats']['coefficient_rejections'],
                volume_rejections=raw['stats']['volume_rejections']), dict(totals)


def prepare(output, smoke):
    # 准备阶段是幂等的：已有 study.json 就直接走 checked()，不重建/覆盖。
    output = Path(output).resolve()
    if (output/'study.json').exists():
        return checked(output, smoke)
    # 二进制、矩阵、源码归档都放在本次运行目录下，使结果可独立重放。
    binary = builder.build(output/'build')
    output.mkdir(parents=True, exist_ok=True)
    inputs = {}
    for c in cases(smoke):
        # 相同 matrix_key 只生成一次；摘要和总字节写入 study.json，
        # 后续 audit 会重新生成并逐项比较，防止输入被替换。
        key = matrix_key(c)
        if key not in inputs:
            path = output/'matrices'/f'{key}.json'
            values = matrix(c, binary)
            save(path, values)
            inputs[key] = dict(sha256=sha(path), total_bytes=sum(values))
    # 源码归档：本模块源码 + 冻结引擎的 build.json/参考运行器/DESIGN 文档
    # + 冻结引擎全部源文件。归档后立即解压到 output/source，形成自包含副本。
    archive = output/'source.tar.gz'
    engine_build = json.loads((builder.FROZEN/'build.json').read_text())
    with tarfile.open(archive, 'w:gz') as stream:
        for p in own_files():
            stream.add(p, arcname='independent/'+str(p.relative_to(ROOT)))
        prefix = 'planning/results/final-96b18da3da36/'
        stream.add(builder.FROZEN/'build.json', arcname=prefix+'build.json')
        # 小型历史参考运行器是审计依赖，不代表当前平台重新编译的产物；
        # 必须与冻结清单中的二进制哈希一致才能打包。
        if sha(builder.FROZEN/'native_scan') != engine_build['binary_sha256']:
            raise ValueError('frozen reference runner changed')
        stream.add(builder.FROZEN/'native_scan', arcname=prefix+'native_scan')
        for name in ['DESIGN.md', 'PLAN.md']:
            stream.add(ROOT/name, arcname='independent/'+name)
        for name in engine_build['identity']['files']:
            stream.add(builder.FROZEN/'source'/name, arcname=prefix+'source/'+name)
    with tarfile.open(archive) as stream:
        stream.extractall(output/'source', filter='data')
    # records 初始为空；run() 每完成一个配置就追加并重写 study.json
    #（增量落盘），即使中途失败，已完成的原始结果仍有登记摘要可查。
    record = dict(identity=identity(), cases=cases(smoke), methods=METHODS, smoke=smoke, inputs=inputs,
                  source_archive_sha256=sha(archive), binary_sha256=sha(binary), records={}, repeats=3,
                  timing_warning='One warmup and three rotated runs: diagnostic only, not a fair production latency benchmark')
    save(output/'study.json', record)
    return record


def checked(output, smoke):
    # 对已存在的运行目录做完整性检查：身份、配置网格、归档/二进制、
    # 解压出的自身源码、冻结引擎身份/参考运行器/逐文件摘要。
    # 任一项不匹配都拒绝继续审计，避免“在错误目录上跑出好数字”。
    output = Path(output)
    record = json.loads((output/'study.json').read_text())
    if record['identity'] != identity() or record['cases'] != cases(smoke) or record['methods'] != METHODS or record['smoke'] != smoke:
        raise ValueError('registered source/grid changed; use frozen source or new output')
    if sha(output/'source.tar.gz') != record['source_archive_sha256'] or sha(output/'build/native_independent') != record['binary_sha256']:
        raise ValueError('source archive/binary changed')
    for name, digest in record['identity']['files'].items():
        if sha(output/'source/independent'/name) != digest:
            raise ValueError('frozen new policy source changed')
    frozen = output/'source/planning/results/final-96b18da3da36'
    engine = json.loads((frozen/'build.json').read_text())
    if engine['identity']['digest'] != record['identity']['engine']:
        raise ValueError('extracted frozen engine identity')
    if sha(frozen/'native_scan') != engine['binary_sha256']:
        raise ValueError('extracted frozen reference runner changed')
    for name, digest in engine['identity']['files'].items():
        if sha(frozen/'source'/name) != digest:
            raise ValueError('extracted frozen engine dependency changed: '+name)
    return record


def audit(output, smoke=False, replay=True):
    # 审计是整套证据的核心：不信任原始 JSON 里“自报”的聚合值，而是
    #   1. 检查登记完整、输入/原始结果摘要未变、输入可重新生成；
    #   2. 可选重放二进制，逐字段比较除计时样本外的全部输出；
    #   3. 对每个方法独立 reconstruct 阶段模型和总时间；
    #   4. 要求每个配置下七种方法的逐资源总字节完全相同；
    #   5. 计算相对 native/maxmin/spreadout 的成对加速比。
    # --no-replay 只跳过第 2 步，不跳过数据域和模型对账。
    output = Path(output)
    record = checked(output, smoke)
    if set(record['records']) != {c['id'] for c in record['cases']}:
        raise ValueError('incomplete registered raw results')
    rows = []
    for c in record['cases']:
        # 输入摘要 + 重新生成比较：防止用别的矩阵替换后仍沿用旧结果。
        path = output/'matrices'/f'{matrix_key(c)}.json'
        if sha(path) != record['inputs'][matrix_key(c)]['sha256']:
            raise ValueError('input digest changed')
        data = json.loads(path.read_text())
        if data != matrix(c, output/'build/native_independent'):
            raise ValueError('input does not match registered generator')
        # 原始执行结果摘要 + 方法集合检查。
        path = output/'raw'/f'{c["id"]}.json'
        if sha(path) != record['records'][c['id']]:
            raise ValueError('raw execution digest changed')
        batch = json.loads(path.read_text())
        if set(batch) != set(METHODS):
            raise ValueError('method set')
        if replay:
            # 完整重放：同一二进制、同一输入、同一 CLI；计时样本允许不同，
            # 其余字段必须逐项相等（包括 stages 内的所有网络明细）。
            fresh = invoke(output/'build/native_independent', c, data)
            for method in METHODS:
                old = {k: v for k, v in batch[method].items() if k != 'timing_samples_us'}
                new = {k: v for k, v in fresh[method].items() if k != 'timing_samples_us'}
                if old != new:
                    raise ValueError('full native replay mismatch')
        # 逐方法独立重建；同一配置下所有方法搬运的逐资源总字节必须一致，
        # 否则说明策略改变了流量本身（超出“只换 matching”的边界）。
        totals0 = None
        paired = []
        for method in METHODS:
            row, totals = reconstruct(c, method, data, batch[method])
            if totals0 is not None and totals != totals0:
                raise ValueError('policies changed per-link total bytes')
            totals0 = totals
            paired.append(row)
        by_method = {r['method']: r for r in paired}
        for row in paired:
            # 成对加速比：base 时间 / 当前方法时间，>1 表示更快；
            # 零时间保护为 1，避免除零。
            for base in ['native', 'maxmin', 'spreadout']:
                row['speedup_'+base] = by_method[base]['total_ms']/row['total_ms'] if row['total_ms'] else 1
            rows.append(row)
    # 审计通过后才写 comparison.json/csv；中途异常不会留下“已审计”文件。
    save(output/'comparison.json', rows)
    with (output/'comparison.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print('Audited native replay, generated inputs, per-link bytes and pipeline:', len(rows), flush=True)
    return rows


def run(output, smoke=False):
    # 运行阶段：prepare → 逐配置执行 batch → 每次落盘原始 JSON 和摘要 →
    # 最后立即 audit。已登记的配置不会重跑；目录里若有不属于登记表的
    # raw 文件则直接报错，避免新旧结果混在一起。
    output = Path(output)
    record = prepare(output, smoke)
    for c in record['cases']:
        path = output/'raw'/f'{c["id"]}.json'
        if c['id'] not in record['records']:
            if path.exists():
                raise ValueError('unregistered existing raw file')
            data = json.loads((output/'matrices'/f'{matrix_key(c)}.json').read_text())
            value = invoke(output/'build/native_independent', c, data)
            save(path, value)
            record['records'][c['id']] = sha(path)
            save(output/'study.json', record)
        print('Complete:', c['id'], flush=True)
    return audit(output, smoke)


if __name__ == '__main__':
    # 默认输出目录名包含当前身份摘要前 12 位；smoke 单独加后缀。
    # --output 可以指定已有运行目录以执行 audit。
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['run', 'audit'], required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--no-replay', action='store_true')
    args = parser.parse_args()
    digest = hashlib.sha256(json.dumps(identity(), sort_keys=True).encode()).hexdigest()[:12]
    out = args.output or ROOT/'results'/('run-'+digest+('-smoke' if args.smoke else ''))
    if args.phase == 'run':
        run(out, args.smoke)
    else:
        audit(out, args.smoke, not args.no_replay)
