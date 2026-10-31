# 原生FAST机内计时、流水线与拓扑核查

日期：2026-10-28。只检查固定原生提交0712d29，不修改作者算法或原有981输入实验。结果目录：[run-230f4af1e86a](timing_audit/results/run-230f4af1e86a/)。

## 结论与此前表述纠正

1. 原始项目有**4种机内通信计时函数**，不是4种新的端到端调度器。设备带宽枚举不是拓扑选择器。
2. 正式论文§4.4（PDF第9页）明确说明：balance与redistribution采用MPI SpreadOut。因此FAST使用机内SpreadOut并不与对比端到端SpreadOut基线矛盾；此前说论文未明确机内算法不准确。§5的Workloads也明确random为均匀分布；未知的是精确生成区间、种子与矩阵，而不是分布类别。
3. 论文H200的硬件拓扑类别已知，是NVLink/NVSwitch两层集群；公开项目也有机内拓扑相关解析函数。**未知的不是有没有拓扑，而是图17完整作图实现和各资源参数的精确口径。** 仿真代码没有交换机队列、逐跳路由或任意拓扑图求解。
4. 与论文§4.3最一致的是pipeline2的“先balance，随后机外阶段与上一阶段的redistribution重叠”结构；不能为贴图而换用full_mesh或旧pipeline。
5. 旧pipeline()确有未初始化读取，不能视为可靠基线。原始topology_benchmark的SpreadOut基线也不会随机内回调更换拓扑模型；旧拓扑实验入口不能自动保证公平的硬件资源对比。

参考：[NSDI2026正式论文](https://www.usenix.org/system/files/nsdi26-lei-yiran.pdf)、[公开原生源码](https://github.com/MangoBoost/FAST/tree/0712d29fb13a09962425c8a57b951b91d8fc33ba/simulation)。

## 4个函数分别计算什么

令D为单台服务器8GPU之间需要通信的矩阵，alpha单位µs，beta单位µs/MB。下表描述源码实现，不宣称完整建模真实硬件。

| 原生函数 | 源码中的计算方式 | 所表达的假设/限制 |
|---|---|---|
| spread_out | 按循环移位组成若干轮，每轮取最大消息的alpha+size×beta，再对轮求和 | 同轮每GPU至多一发一收；适合端口受限、近似无阻塞的交换式scale-up，未求最优本地调度 |
| intra_transfer_full_mesh | alpha+整个矩阵最大消息×beta | 全部GPU对同时走独立直连链路；beta是每对链路代价，不是GPU聚合出口代价 |
| intra_transfer_2ring | 正向距离1…3、反向距离1…4分别累计“每轮最大消息代价×距离”，最后取两个方向最大值 | 8GPU双向ring的固定距离/跳数近似；beta应按相应方向链路速率设置 |
| intra_transfer_hybrid_cude_mesh | 32个直连方向与8组经中间节点的固定公式，先后两部分分别取最大再相加 | 8GPU、固定编号的hybrid cube mesh；部分边有两倍带宽，非任意维度/拓扑，代码函数名保留cude拼写 |

源码位置：[config.cpp](results/run-9247cfc8536a/upstream/simulation/scheduler/config.cpp:61)。其中full_mesh的公式甚至不会在输入全零时跳过alpha；hybrid_cube的两部分也会保留alpha。因此同一beta下的数字不能直接解释成同一GPU聚合带宽下的算法优劣。

## 手算与逐边核查

设置：8GPU；alpha=.1µs；beta=1/450000s/MB=2.222222µs/MB；单元100MB。除“仅自拷贝”探针外对角线全零。

| 输入 | SpreadOut (µs) | Full mesh (µs) | 2-ring (µs) | Hybrid cube (µs) |
|---|---:|---:|---:|---:|
| 全零 | 0 | 0.1 | 0 | 0.3 |
| 每个非对角GPU对100MB | 1556.256 | 222.322 | 2223.222 | 1111.411 |
| 仅GPU0向其余7GPU各发100MB | 1556.256 | 222.322 | 2223.222 | 555.856 |
| 仅每GPU自拷贝100MB | 222.322 | 222.322 | 0 | 0.3 |

密集与单源扇出两个输入中，最忙GPU都需发送700MB。**若450GB/s是该GPU的总出口带宽，忽略alpha也至少需要1555.556µs。** 因此Full mesh的222.322µs不能在这个聚合带宽假设下成立；它隐含7条各450GB/s的并行链路。Hybrid cube也存在同一个带宽解释问题，因为若干边采用beta/2、相当于2倍单边速率。

这不是证明full_mesh函数必然有错，而是证明它不能不经换算就代替H200每GPU450GB/s的模型。若全互连每GPU聚合总带宽固定450GB/s，并且均匀分给7条直连边，则每边约64.286GB/s；该密集例的full_mesh时间才约1555.656µs。NVSwitch动态共享端口与固定分配的7条独立链路仍不是同一个模型。

“仅自拷贝”是接口诊断，不属于本轮合法GPU对网络负载。它说明各函数对本地复制的约定不一致，不能把它们当成可无条件替换的通用网络计时器；原生balance/dispatch生成器排除了需要redistribution的自拷贝，真实输入GPU对矩阵也强制对角为零。

另外对56个有向非对角GPU对逐个放置100MB：4函数均给出正耗时；每个输入字节量翻倍时耗时不下降。单向100MB的耗时范围如下：

| 模型 | 最小/最大单向时间 (µs) | 方向对称性 |
|---|---|---|
| SpreadOut | 222.322 / 222.322 | 28对均对称 |
| Full mesh | 222.322 / 222.322 | 28对均对称 |
| 2-ring | 222.322 / 889.289 | 28对均对称，差异来自环距离 |
| Hybrid cube | 111.411 / 444.744 | 8对正反方向时间不同 |

Hybrid cube的方向差异可能来自固定非对称路由/计算公式，不足以单独证明bug；必须对照具体V100链路和路由才能判定。它不是H200模型，本轮不据其性能判断FAST优劣。

测试先在beta=1µs/MB下用独立手算值验证：密集矩阵4函数依次700.7/100.1/1001.0/500.3µs，再使用论文带宽计算上表。测试经历缺失实现RED→2项GREEN。60种输入×4模型×原量/翻倍共480次机内原生调用，保存为[local.json](timing_audit/results/run-230f4af1e86a/local.json)的240行。

## FAST与SpreadOut为什么可以同时出现

```text
FAST端到端方案
  机内balance：SpreadOut执行本地重分配
  机外scale-out：Birkhoff生成服务器间匹配与阶段
  机内redistribution：SpreadOut将代理GPU数据送到真正目标

SpreadOut端到端基线
  不做上述FAST重塑，直接对全部GPU轮转配对
```

二者竞争的是端到端方案，而不是“FAST内的每一个操作都必须排斥SpreadOut”。源码中的本地spread_out面对8×8矩阵；spread_out_baseline面对(N×8)×(N×8)的原始矩阵，每轮根据是否跨服务器选择机内/机外链路参数。这两个函数名字接近，但层级和输入不同。

## pipeline、pipeline2、pipeline3的差别

源码：[global.cpp](results/run-9247cfc8536a/upstream/simulation/scheduler/global.cpp:72)。这些是原生C++中的调度生成加解析耗时估算入口，不是分别启动多轮ns-3或多个GPU进程。

| 入口 | 本地调度策略与重叠 | 机内计时 |
|---|---|---|
| pipeline()，本文称pipeline1 | 按每个Birkhoff阶段的频率做balance；尝试把上一阶段dispatch、下一阶段balance与当前机外传输重叠；原始机内A2A放到最后阶段附近 | 固定spread_out；有未初始化读取 |
| pipeline2() | 先对各目标服务器tile做一次balance；首阶段机外通信与原始机内A2A重叠，后续机外阶段与前阶段dispatch重叠，最后收尾dispatch | 固定spread_out |
| pipeline3(fn) | 一次balance及后续流水线结构与pipeline2相同，但接收可替换的机内计时回调 | 可以选4个函数；函数本身没有pipeline2中的可选scaling预处理 |

pipeline1有一个具体实现缺陷：首次循环cur_pid=0时不进入给intra1赋值的分支，随即执行intra_time=intra1+intra2。[原始代码第482/492行](results/run-9247cfc8536a/upstream/simulation/scheduler/global.cpp:482)和[编译警告](timing_audit/results/run-230f4af1e86a/compiler-warnings.txt)均可核验。即使某次输出稳定，也不能把未定义行为产生的数值当作可信结果；本轮未修复它，保持原生证据。

论文§4.3明确描述先balance、首阶段同时处理原始机内A2A、后续dispatch与下一次scale-out重叠，与pipeline2/3的结构对应，不是pipeline1的逐阶段balance版本。

## 全部机内模型的端到端计算

同一批已保存的随机矩阵，均为400Gbps机外、450GB/s机内、每GPU对平均50MB。6个规模×20个种子×6个入口=720次原生调用。所有模型使用完全相同的输入；pipeline3回调spread_out与原有pipeline2在120个输入上逐项数值一致。

下表是FAST端到端算法带宽GB/s，不含调度耗时；**不是不同算法在等价物理拓扑上的公平排名**，因为4个回调的beta资源含义不同。pipeline1的数字仅为本次编译的原始输出，全部标记不可靠。

| GPU数 | pipeline1† | pipeline2 | pipeline3 SpreadOut | pipeline3 Full mesh | pipeline3 2-ring | pipeline3 Hybrid cube | 论文FAST raw |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 16 | 73.060 | 72.868 | 72.868 | 85.143 | 66.894 | 78.569 | 72.511 |
| 32 | 52.450 | 50.412 | 50.412 | 58.639 | 44.744 | 54.590 | 53.903 |
| 64 | 43.745 | 43.785 | 43.785 | 51.941 | 37.634 | 47.507 | 51.138 |
| 128 | 37.626 | 39.461 | 39.461 | 49.034 | 32.520 | 43.110 | 49.087 |
| 256 | 32.921 | 36.976 | 36.976 | 48.109 | 30.196 | 40.875 | 48.489 |
| 320 | 31.812 | 36.471 | 36.471 | 47.913 | 29.907 | 40.494 | 48.381 |

†未初始化读取，不参与有效性或排名判断。没有一个回调在保持其资源含义和论文描述一致的同时，解释了所有规模的差异。Full mesh大规模接近不是选择它的充分理由；Hybrid cube在某个小规模贴图也不是H200采用V100模型的证据。

原始记录：[global.json](timing_audit/results/run-230f4af1e86a/global.json)、[summary.json](timing_audit/results/run-230f4af1e86a/summary.json)、[registration.json](timing_audit/results/run-230f4af1e86a/registration.json)。原始调用、矩阵摘要、构建命令与结果摘要均保留，新增实验没有重写主复现。

## 原始项目提供哪些拓扑能力

原生main.cpp注释提供硬件模型的选择示例：A100→spread_out；MI300→full_mesh；MI250→2ring；V100→hybrid cube。topology_benchmark(fn, inter_Gbps, intra_Gbps)接收机内函数与两类速率。这说明项目不是完全没有拓扑环节，而是把机内拓扑约束压缩为特定耗时公式。

机外部分则通过统一alpha/beta和服务器级匹配估算时间，没有TOR/网关节点、共享上行、路由矩阵、队列、ECMP或DC间链路对象。NVIDIA/AMD目录是实际GPU通信实现，不是另一套逐包拓扑模拟器。

另一个公平性边界：原生topology_benchmark更换fn只会改变FAST侧pipeline3机内计时，基线仍调用spread_out_baseline，并不会同步加入环/立方体的逐跳代价。因此这组原生入口本身并不足以自动形成“所有算法使用同一物理拓扑”的对照；特别是不能把ring/cube结果直接用于新的拓扑优化结论。

## 后续建议

1. 保持论文指定的机内SpreadOut及pipeline2作为主复现方向，继续分解balance、机外阶段、未隐藏dispatch和收尾耗时，定位图17a差距。
2. 先核实各带宽参数的端口/单边/聚合含义；跨拓扑对比时让FAST与基线共享同一套资源约束，不能仅共享一个速率数值。
3. 将pipeline1和未初始化link_info构造入口列为原生已知问题；如需修复，单独保留“原样”与“修正”两组，不覆盖原始复现结果。

复查命令（从FAST目录）：

```bash
python3 -m unittest discover -s simulation/reproduction/timing_audit -p 'test_*.py' -v
python3 simulation/reproduction/timing_audit/audit.py simulation/reproduction/results/run-9247cfc8536a
```
