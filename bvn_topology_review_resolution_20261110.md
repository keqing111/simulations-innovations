# BvN 拓扑审查问题处理报告

日期：2026-11-10。分支：`research/bvn-partial-20261109`。

已完整核对本地 `bvn_topology_error_report_20261110.md` 及用户补充意见。本次只修改三个现有实现文件及相关测试，不修改网络模型、Global 评分公式、原生匹配顺序或 Local balance 算法；不覆盖历史原始数据，不开展大规模扫描，不提交或推送 Git。

## 汇总

| 问题 | 已复现 | 是否真实 bug | 已修复 | 仍开放 |
|---|---|---|---|---|
| topology 自环重复消费 intrinsic | 是：intrinsic=230.1us、dispatch=230.1us、自环 payload=100MB | 是，重复计费并使 self row_sum 下溢 | 是，仅显式 topology pending/消费/dispatch 路径过滤 self-loop | 默认非 topology FAST/Local 行为按授权保留；不宣称其历史自环行为也已修复 |
| CompletionTimeAware 接受共享瓶颈更慢候选 | 是：pair proxy=0.5us 的候选实际阶段2us，优于 proxy=1us、实际1us 的种子而被旧代码接受 | 是，与本次明确的阶段总时间目标不符 | 是，保留阈值候选生成，接受条件改为 `evaluate_gpu().total_us` 严格改善 | 仍是有限启发式，不保证发现全部改进匹配或改善完整操作 |
| partial 物理目标打平却删除真实服务 | 是：独立等量双流从一阶段变两阶段 | 是，无收益拆分 | 是，Global/Local 两层均打平后，先最大化真实服务，再按目的向量打破平局 | 不解决严格局部降分造成的未来损失 |
| SquaredWork 删除严格降分但完整操作更慢 | 是：等量独立流从5.08us变10.16us | 局部目标与整体目标不一致，不是本次平局 bug | 未改评分、未伪称已解决 | 开放，作为既定局部启发式的反例保留 |
| BandwidthAware 忽略传播 | 是：serial 从4us降至0.5us，但 total 从4us升至100.5us | 已明确目标为 bandwidth/serial-only，不是代码偏离该目标 | 未改语义，新增对抗测试与说明 | 不应宣传为完成时间改善保证 |
| 启用 Global/Local 却没有候选来源 | 是：预算1或8都只有 seed | 是，配置会误导方案比较 | 是，明确拒绝启用目标但两个候选开关均关闭的配置 | 仍允许有限候选恰与 seed 重合；不能保证每个输入都产生不同计划 |
| max传播+max资源工作量模型偏差 | 是：独立路径例20us vs11us，并发生排名反转 | 是已声明解析近似的局限，不是实现偏离公式 | 未改模型，补独立/共享/多级小例 | 需要先明确参考网络执行规则，不能一键声称精确仿真 |
| propagation 与 RTT / Time 初始优先级 | 已核对并测 RTT-only 不改变阶段代价；Time 初始匹配不变 | 当前行为符合设计 | 不改，记录单位与系数语义 | 不将单向传播误称 RTT，也不将时间映射误称带权匹配 |
| Global 本轮更优但完整计划更差 | 是：18388.608us →18472.49408us | 全局启发式无长期最优保证，不是公式实现错误 | 保留现有公式与严格字典序 | epsilon 约束替代选择规则暂缓，未偷偷引入 |
| unordered_set 同分顺序 | 静态确认跨库/构建风险；本环境原生兼容与重复运行通过 | 可复现性边界，非本环境随机失败 | 未改变，保护默认/原生对照 | 跨平台固定顺序仍开放，需另立兼容版本 |
| 时间映射极端数值 / 容差 | 是：1e300:1e-300 Gbps 使某个输出下溢为0，受控异常 | 显式拒绝不可表示输出是设计边界；未发现静默丢量 | 不钳制、不补尾，新增正常异构及受控失败测试 | 极端案例不保证可运行；1e-6 byte 是守恒绝对容差，不是“一字节精度” |

## 1. 四项局部修复及前后证据

### 1.1 Self-loop 只表示 topology 补齐容量

最小例：N=3、G=2；server0↔server1 对齐 GPU 通道各200MB；server2 只有 GPU0→GPU1 的100MB intrinsic。server2 无跨机需求，但 native stuffing 产生2→2。

- 修复前：测试先运行并失败；输出 `intrinsic=230.1 dispatch=230.1 self-payload=100000000`。
- 修复后：Native/BandwidthAware/CompletionTimeAware 三个 topology 模式均为 `intrinsic=230.1 dispatch=0 self-payload=0`。
- `pending_units(2,0)` 仍为100MB，intrinsic 原值不变。通过原有 `server2server_approx(2)` 读取 row_sum 的效果，断言 self 槽仍为0，排除无符号下溢。
- 增加非零跨机 dispatch 变体：server0 GPU0 的远端流量改发目的 GPU1。修复后三个模式都保留真实 `dispatch=460.1us`，而 self-payload 仍为0，证明不是把所有 dispatch 清零。

实现：`collect_pending_units()` 仅在配置 topology 时令 self 槽为0；topology 的 `consume_pending()` 跳过 self；`pipeline2()` 只在 `topology_ && dst==src` 时跳过 Local dispatch。没有改 `LocalScheduler`、默认无 topology 的 balance/dispatch、原生补齐或原生匹配顺序。

测试：`mixed_intrinsic_self_loop_test()`，含两种跨机 dispatch 情形和三个模式。

### 1.2 完成时间候选按当前统一阶段模型接受

最小例：四个端点，需求支持图仅有两套完整匹配，每条边1000B。以原生第一套匹配作种子，其四条路径各自独立8Gbps、传播0，因此实际阶段1us；另一套匹配的每对独占 proxy 是0.5us，但四流共享16Gbps，实际阶段2us。

- 修复前：先加测试并运行，输出 `seed=1us selected=2us`，断言失败。
- 修复后：输出 `seed=1us selected=1us`，保留种子。

阈值匹配、相同 q 条件、真实服务量护栏均保留，只把 CompletionTimeAware 接受层的 per-pair 最大 proxy 换为 seed/trial 实际 flows 的 `evaluate_gpu().total_us`。BandwidthAware 仍比较 serial。

测试：`completion_shared_acceptance_test()`。这里“实际”仅指统一静态阶段计价器，不把它升级为包级网络真值。

### 1.3 partial 平局优先保留真实服务

最小例：0→1、2→3 各1000B，独立100Gbps，单向传播各5us，Local=StageTime、edits 开启、rho=0.5。一起发送或删除一路的单阶段代价同为5.08us。

- 修复前：目的向量字典序偏好带 `-1` 的匹配，最终两阶段10.16us；新测试先失败于 `stages.size()==1`。
- 修复后：一阶段双流，总计5.08us。
- 加一个精确服务量支配边界：Time 空间下1e15B/1B 两路具有相同序列化时间；若对真实服务量也套相对浮点分数容差，少发1B会被误当平局。该测试先失败，再改为对已展开真实服务量直接降序比较，一阶段通过。

顺序仍是：Global rate 降序 → Local cost 升序 → 真实服务量降序 → 固定目的向量。没有混合量纲相加，没有允许 Global 损失的 epsilon 层。原有 Global/Local 浮点比较容差未变。

测试：`objectives()` 中的独立等量及1e15:1服务量支配例。原 MaxPathTime 在单流/双流等成本时也改为保留双流，这是预期的平局规则修正。

### 1.4 无候选源配置拒绝

最小配置：Pressure+StageTime，pressure=false、edits=false。旧预算1和8都只展开 seed，目标评分不参与选择。

- 修复前：新增拒绝测试失败于 `rejects()`，证明旧配置被接受。
- 修复后：抛 `enabled objectives require a candidate generator`。旧 None/None 端点基线保持合法；开启候选源时原有至少两个候选预算规则继续生效。

测试：`noop_configuration_test()`；全部现有目标组合测试仍运行，未靠自动启用开关来“修正”用户参数。

## 2. 确认但不擅自改变的语义

### 2.1 Bandwidth-only 不是总完成时间目标

`bandwidth_only_semantics_test()`：原生匹配四流共享8Gbps，传播0，总时间4us；替代匹配四条独立16Gbps，传播100us，serial=0.5us，总时间100.5us。BandwidthAware 如其定义接受 serial 改善，CompletionTimeAware 的统一接受层不应据此宣称总时间改善。此例修复前后语义不变，测试把边界写明。

### 2.2 传播与 RTT；时间映射不是初始带权匹配

`propagation_us` 是明确输入的单向传播，`rtt_us` 独立保存。当前 stage alpha 及 Delay 权重都读取 propagation，RTT 不参与这两式。`model_boundary_tests()` 把 RTT 单独放大至200000/700000us，阶段代价仍为20us，未改模型。

`review_characterization_tests()`：两条各1000B 的独立路径，带宽1000Gbps/1Gbps、传播0。Bytes 初始 q=1000B；Time 初始 q=0.008us，初始两条匹配的 src/dst 相同，真实发送量不同，最终守恒。该差别是固定权重影响系数，不是“忘了按时间排序”的 bug。

### 2.3 阶段解析近似的定量边界

所有数字单位为us；流量每条1000B。参考规则在表中明确，不把不同参考规则混成同一个真值。

| 固定小例 | 容量工作量下界 | 当前 max传播+max工作量 | 明确参考 | 差异 |
|---|---:|---:|---|---:|
| 独立A：两路8/0.8Gbps，传播10/1 | 10 | 20 | 独立路径完成最大值=max(10+1,1+10)=11 | 当前多9 |
| 独立B：两路1Gbps，传播均5 | 8 | 13 | 独立路径完成最大值=13 | 0 |
| 共享：两流共享1Gbps，传播均5 | 16 | 21 | 单共享容量、相同传播的流体并发参考=21 | 0 |
| 多级：两流均经8Gbps网关再0.8Gbps WAN，传播均5 | 20 | 25 | cut-through 流体参考25；若整条消息 store-forward，首网关1+两次WAN服务20+传播5=26 | 取决于执行规则，不能宣称精确 |

当前模型把独立B的13排在A的20之前，但明确的独立路径参考把A的11排在B的13之前，发生评分排名反转。`model_boundary_tests()` 固定这些当前公式输出，防止把限制悄悄“修掉”。多级例没有在本任务中引入队列/包/拥塞模型；用户拥有的 `topology/model.h` 修改原样保留。

## 3. 局部/全局贪心尚未解决的最小反例

### SquaredWork：严格降分，不属于平局修复

两条独立100Gbps、5us、各1000B：一起发送时局部代价为 `0.08²+0.08²=0.0128us²`；删除一路后为0.0064us²，严格更小。因此即使新增真实服务量平局层，SquaredWork 仍选择拆分，完整时间从5.08变10.16us。`review_characterization_tests()` 明确断言当前两阶段结果，作为开放启发式限制，而非声称已经优化完毕。

### Pressure：首阶段 Global rate 更大但全计划更差

N=4；0→1=1MiB、2→3=100MiB；独立100Gbps，传播各5000us；edits=1，预算8/32，rho=0.5。

| 选择 | 第一阶段 q | 第一阶段 Pressure rate | 阶段数 | 完整时间 |
|---|---:|---:|---:|---:|
| endpoint seed：两路先各1MiB，再发99MiB | 1MiB | 84.7249408/5083.88608≈0.0166653893 | 2 | 18388.608 |
| Pressure：先100MiB，再发1MiB | 100MiB | 8388.608/13388.608≈0.6265481819 | 2 | 18472.49408 |

Global 本轮分数严格改善，完整时间反而增加83.88608us。资源总字节并未增加，损失来自独立资源序列化重叠减少。`review_characterization_tests()` 实际调用 plan/evaluate 并断言两份完整时间，不是扫参猜测。本次保留 Global 公式与严格字典序；epsilon-constrained 选择规则是暂缓的独立研究变体。

## 4. 数值与可复现性

- `review_characterization_tests()` 验证1Gbps/1Tbps 的正常异构 Time 映射守恒；1e300:1e-300 Gbps 的有限极端输入导致非限制边输出约1e-597B，转 double 为0，明确抛 `nonpositive or nonfinite output flow`。不钳制为最小正数、不静默丢弃，也不虚构已支持该极端配置。
- 初始需求仍是非负整数字节，因此“初始小于1字节需求”不在接口中；时间空间允许的是输出分数字节。每对守恒阈值仍是 max(1e-6B,64·double epsilon·原需求)，不是一字节舍入策略。原有零需求对正流量拒绝测试继续通过。
- 原生及 merged topology 中 unordered_set 的平局顺序没有改。现有 native 逐阶段一致性和本环境重复运行通过，但不能据此保证跨 libstdc++/编译构建一致。固定可移植顺序将改变旧基线，本次按授权保留并记录风险；独立 partial 的编号遍历仍确定。

## 5. 回归与历史数据处理

修复前先运行：`make -C simulation bvn-check && make -C simulation check`。基线通过：5323项新工具断言、现有 C++ topology 测试、3个 Python 用例及原78份历史输出比较。

修复后首次旧合同回归出现 **48个历史 subtest 失败**，没有忽略：旧 Zipf GPU 矩阵含同机块，旧档把 intrinsic 从 self-loop 再提取，污染 payload/部分dispatch；另有 time 接受准则本次被明确修正。不能把这些已知错误字段继续充当正确 golden，也不能覆盖旧文件。

修改后的历史合同不跳过任何案例、不覆盖任何归档：

1. 全26案例、52份 native/bandwidth 输出：严格保留旧档 map/freq、真实跨机 payload、alpha/serial/communication、balance/intrinsic。所有 self 槽新 payload 必须为0。
2. 未受自环 intrinsic 污染的 dispatch 槽仍与旧档精确相等。受污染的508个阶段 dispatch 槽，使用测试中独立编译的小程序，只调用**未修改的 Local helper**，从原需求重放 balance 与非 self dispatch，全部精确对账；并断言新 dispatch 不超过旧污染值。
3. 26份 time 输出按修正后行为检查，不再强迫旧 per-edge-proxy 计划相等；其中6份匹配计划改变。全部模式都独立重放每对跨机总量、Local payload/dispatch、路径资源聚合网络公式，并按原 pipeline recurrence 重算完整时间。此处不把26份行为验证冒称旧 golden 精确匹配。
4. 新的混合对角与非零 cross-dispatch C++ 回归额外约束修复，防止仅放松旧档断言。

最终实际命令与结果：

```text
make -C simulation bvn-check
  bvn schedule: 5443 checks passed
make -C simulation check
  Topology schedule tests passed
  Ran 3 tests in 7.097s — OK
  52 strict native/bandwidth plan+network comparisons
  26 corrected time replays (6 changed plans)
  508 intrinsic-contaminated dispatch slots independently replayed
git diff --check -- <本次三个实现及三个测试文件>
  exit 0，无空白错误
g++ -std=c++11 -Wall -Wextra -Wpedantic -Werror ... -fsyntax-only
  simulation/algorithm/bvn_schedule.cpp simulation/tests/test_bvn_schedule.cpp
  exit 0，无诊断
```

本次自身代码修改仅为 `algorithm/{all2all,bvn_schedule}.cpp`、`scheduler/global.cpp`、`tests/{test_bvn_schedule.cpp,test_topology_schedule.cpp,test_schedule_cli.py}`，外加本报告。已有其它 dirty 文件包括 `topology/model.h` 没有被本任务编辑。未新增求解器、未改模型公式、未改 Local、未改历史 raw 文件、未提交或推送。

本报告中的模型/启发式限制均已保留，不能据通过回归测试声称得到全局最优或精确物理网络完成时间。
