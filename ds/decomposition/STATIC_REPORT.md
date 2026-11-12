# Static-only 辅助空间实验（2026-11-11）

## 结论与范围

完成固定 **S0/native Kuhn** 的81份静态 pilot 输出：N=16/32/64 × 3份合成需求 × 3份固定拓扑 × Bytes/Time/Delay。每个 native CLI repeat=1；没有选取最快样本，没有增加可选 S1 或额外参数网格。

本实验回答“只改变静态辅助矩阵 Z，是否改善投影后的阶段计划”。它不调用 Local balance/dispatch 或 FAST pipeline，不是完整 FAST 端到端实验，也不是实测网络或真实 MoE trace。匹配策略、原生补齐/分解程序、同一需求 D、同一固定路径与物理计价公式均保持不变。

结果不支持静态时间映射必然更好：同构 SerializationTime 如预期完全不变；异构共享 WAN 中只有 N16 receiver-Zipf 一份改善，其他多数更差；加入固定传播项的 DelayAdjustedTime 在若干例中产生接近满支持规模的细碎阶段，显著变差。这些结果仅适用于本 pilot 的固定 S0 与合成输入，不推广为所有匹配策略或真实负载的结论。

## 冻结输入与方法

复用用户拥有、未修改的 `study.fixed_load_matrix(n,1,kind,'matrix')`：

- `balanced`：每源9765KiB，所有非对角项严格均匀，行列总和相同。
- `perturbed`：均匀矩阵每行循环后继项增加1KiB，每源9766KiB；它仍行列和相同，但不再逐对均匀，不能把它误称为总负载不平衡。
- `zipf`：固定 seed101 的合成 receiver-Zipf，每源仍9765KiB，接收列偏斜；不是测得的专家路由或 MoE trace。

真实字节 D 始终是这些 KiB 整数乘1024。对不同 N 固定每源业务量，而非随 N 增加每源需求。9份真实需求在各拓扑和空间间复用。

本报告的拓扑名是 **STATIC 脚本内部标签**，不是早期 ds 文档中的同名 T1/T2：

| 本地标签 | 描述性名称 | 固定资源与单向传播 |
|---|---|---|
| T0 | static-homogeneous-endpoint400 | 每源出口/每目的入口各400Gbps；所有非自环路径1.4us |
| T1 | static-3DC-shared-WAN100 | `dc[s]=floor(3s/N)`；端点400、各DC出口/入口网关400、每个有向DC对WAN100Gbps；同DC1.4us，跨DC52.8us |
| T2 | static-3DC-long-01 | 资源与上述3DC共享拓扑相同；仅DC0↔DC1单向传播改为5002.8us，其他跨DC仍52.8us、同DC仍1.4us |

路径逐对显式保存资源ID；出口、入口、网关、WAN方向资源均可共享，反向 WAN 使用不同ID。N不能整分3时保留不等DC大小。RTT另外记为2×单向传播，但既不参与 Z 的传播项，也不参与本次计价。

### 精确整数辅助空间

令 x=D/1024（输入 KiB），b 为固定路径最小带宽（100或400Gbps），τ为明确的单向传播us：

```text
Bytes: Z = x * 128
Time:  Z = x * (400/b) * 128
Delay: Z = x * (400/b) * 128 + 6250 * τ       （alpha=1）
```

Time/Delay 的共同 tick 为1/6250us=0.00016us；1KiB/400Gbps=0.02048us=128ticks。τ=1.4/52.8/5002.8 通过 `Fraction(str(τ))` 的精确有理数构造，对应8750/330000/31267500整数ticks。Bytes 一个tick对应8真实字节。**没有对变换后的值向上取整**；若不整除固定ticks或越过原生单元/行列整数边界，脚本明确拒绝。

对每个正需求对固定 `w_ij=Z_ij/D_ij_bytes`。零需求对始终Z=0，不加传播项，不做权重除法。调用同一 `bvn_decompose_simulator --mode matrix --strategy 0 --budget 8 --topology 0`，不开启缩放，直接由原生程序补齐/分解 Z；CLI 的 `unit-mb` 不参与这里的物理字节恢复与计价。

每个辅助完整置换及其整数q按 `actual=min(real_remaining,q/w)` 投影，R、w、实际发送量均用 Fraction；虚拟边不产生真实流量。先记录 K_aux，再独立删除纯零真实量的阶段得到 K_real。本次81份结果中纯零真实阶段数均为0，故 K_aux=K_real；不能把过滤虚拟边宣传为观测到阶段数下降。

计价使用同一固定拓扑、同一真实流量的显式公式：

```text
stage_alpha = max(活动真实路径的单向传播)
stage_serial = max_resource(0.008 * 本阶段聚合真实字节 / Gbps)
static_communication_us = sum(stage_alpha + stage_serial)
```

每阶段资源负载用 Fraction 聚合后转float计价。逐对已发送量严格累加，最终逐对等于原始整数字节；精确资源总量由实际已发送量按“先按pair汇总，再沿固定路径汇总”重组，避免跨几千不同分母生成不必要的巨大临时LCM。另以各阶段资源负载的浮点总和交叉核对。每个同D/同拓扑的三空间资源总量完全相同。

## Pilot 结果

速度比定义为 Bytes 静态时间 / 该空间静态时间；大于1才表示模型中的改善。每行9组，包含三个N和三种需求；胜/平/负阈值为速度比距1的1e-10。几何均值只是固定小面板的描述，不是总体统计推断。

| 固定拓扑 | 空间 | 胜 / 平 / 负 | 速度比范围 | 几何均值 |
|---|---|---:|---:|---:|
| 同构 endpoint400 | Time | 0 / 9 / 0 | 1.0000–1.0000 | 1.0000 |
| 同构 endpoint400 | Delay | 0 / 3 / 6 | 0.7989–1.0000 | 0.9578 |
| 3DC shared WAN100 | Time | 1 / 0 / 8 | 0.6665–1.0475 | 0.8068 |
| 3DC shared WAN100 | Delay | 1 / 0 / 8 | 0.06494–1.0519 | 0.4616 |
| 3DC long-01 | Time | 0 / 0 / 9 | 0.3265–0.9377 | 0.5863 |
| 3DC long-01 | Delay | 0 / 0 / 9 | 0.01965–0.8490 | 0.2826 |

代表性完整计划，时间均为本报告的静态阶段模型us：

| N / 需求 / 固定拓扑 | Bytes K / us | Time K / us | Delay K / us |
|---|---:|---:|---:|
| 16 / receiver-Zipf / 3DC shared | 222 / 15948.881600 | 216 / 15225.364480 | 217 / 15161.291800 |
| 64 / 均匀 / 同构 | 63 / 288.187200 | 63 / 288.187200 | 63 / 288.187200 |
| 64 / 均匀 / 3DC shared | 63 / 12989.273600 | 192 / 18606.899200 | 3636 / 200012.351487 |
| 64 / 均匀 / 3DC long-01 | 63 / 324839.273600 | 192 / 969006.899200 | 3302 / 16533416.851390 |
| 64 / receiver-Zipf / 同构 | 3393 / 6975.392960 | 3393 / 6975.392960 | 3948 / 7752.392960 |
| 64 / receiver-Zipf / 3DC shared | 3393 / 192688.577280 | 3715 / 208267.374080 | 3927 / 219608.924164 |

最强负例为64端点均匀需求、3DC long-01：Delay 速度比仅0.0196474，即约50.9倍更慢，而真实业务和资源总字节未增加。不能用“辅助系数包含传播”推断每次真实阶段就会更短。

同构 Time 不变是严格常数权重校验。**同构 Delay 不一定不变**：即使所有τ相同，固定 `w=serialization_weight+τ/D` 仍随每对D变化；receiver-Zipf和逐对扰动会改变辅助分解轨迹。均匀D时才重新成为常数权重；本次三份均匀同构 Delay 正好全部持平。另有 self-test 明确验证 alpha=0 时 Delay 的辅助矩阵等于 Time。没有额外加入“时间权重初始匹配优先级”；改变 Z 后由同一原生补齐和 unordered Kuhn 自然产生计划。

## 计时口径

原始结果分别保存：

- `mapping_cpu_us`：Python `process_time_ns()`，仅构造精确Z和固定w。
- `native_decomposition_cpu_samples_us`：C++ CLI 的 `std::clock()` 真实进程CPU分解时间，含现有统计观测，不含padding。
- `native_padding_wall_samples_us`：CLI 明确单列的padding墙钟。
- `subprocess_wall_us`：进程启动、CLI及stdio墙钟，不当成C++分解CPU。
- `decomposition_audit_cpu_us`：Python调用原有 `study.audit_decomposition` 的验证CPU。
- `projection_evaluation_cpu_us`：Python Fraction 恢复、物理计价及守恒验证合并CPU。

Fraction mapping/恢复/验证是研究工具实现成本，**不是测得的C++在线规划器开销**，不滚入FAST CPU性能或端到端加速比。

例如 N64均匀3DC shared，Bytes/Time/Delay 的原生分解CPU分别为6792/21186/321665us；Python映射约16394/22535/39996us，恢复与评价约84951/190199/3166160us。这显示工具验证也有明显成本，不能用subprocess墙钟冒充分解CPU，或把 Python Fraction 原型与FAST的C++规划开销直接比较。每个单元只有1次CPU样本，不提供噪声置信度或微小CPU差异结论。

## 验证、复现与产物

先写4个脚本内 self-test，首次运行因 `fixed_topology` 等接口尚不存在而RED；实现后全部GREEN：

```text
python3 ds/decomposition/static_study.py --self-test
  Ran 4 tests — OK
python3 ds/decomposition/static_study.py \
  --out ds/decomposition/static-results-20261111 --repeat 1
  PASS: 81 outputs, complete=true, source/binary hashes unchanged across the run
```

验证覆盖精确ticks、零需求、无法精确表示的传播拒绝、共享资源5.16us手算、同构常数权重/Delay alpha0、异构非均匀需求、原生完整辅助分解重构、每对真实字节守恒及每资源总量不变。

9个同构 Bytes/Time 面板的辅助整数矩阵、真实阶段数及静态时间严格一致。81份输出逐对真实守恒误差均为0，27个同D同拓扑的三空间资源总量均一致。首个 N16均匀同构 Bytes 额外与现有 `bvn_simulator --method fast-native --unit-bytes 8` 对账：15个阶段、系数、真实流及物理静态评价均一致。只宣称这一个实际运行的native adapter parity，不冒充81份均重复跑了adapter。

结果目录：[static-results-20261111](static-results-20261111/)：

- `cases/`：真实字节矩阵、原KiB矩阵、固定seed及每源业务量。
- `topologies/`：逐资源容量、逐对固定路径、单向传播/RTT的精确分数。
- `auxiliary/`：每空间精确整数输入和逐对固定有理数权重。
- `raw/`：未经改写语义的完整native CLI输出（含置换、q、CPU计数）。
- `projected/`：K_aux/K_real、逐阶段真实量/静态代价、精确资源总量与守恒诊断；第一份parity例额外保留真实flows。
- `native-adapter-parity.case/.json`：独立adapter对账输入/输出。
- `summary.json`、`run_metadata.json`：81行摘要、配对速度比、选项、编译器/commit/dirty状态、所列源码SHA256、两个可执行文件SHA256及计时口径。

首尾源码及分解二进制hash完全相同；结果文件不可覆盖式创建。没有修改用户的generator、core、pipeline、模型或既有实验结果。原生同分顺序仍继承当前构建的 unordered traversal；跨标准库可复现性及单样本计时噪声仍是既有边界。
