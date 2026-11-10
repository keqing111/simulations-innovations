# FAST 拓扑感知 BvN 实现审查报告

日期：2026-11-10  
审查对象：`8fad08e09da2bcf75e6a232f072eca1cd6132a2c`（`feat: add topology-aware partial BvN scheduling simulator`）及其工作区中的相关实现  
审查范围：`simulation/algorithm/bvn_schedule.cpp`、`simulation/algorithm/all2all.cpp`、`simulation/scheduler/{local,global}.cpp`、`simulation/topology/model.{h,cpp}`、相关测试  
审查方式：静态代码审计；对关键问题用 `/tmp` 下的临时程序直接编译复现。未修改仓库代码。

## 结论摘要

独立 `bvn_schedule.cpp` 的残余矩阵、真实字节恢复、阶段系数、support 上界和最终守恒逻辑，在本次审计范围内没有发现确定的破坏性错误。已有 `bvn-check` 的 5323 项检查通过。

但合并后的 topology 路径存在一个确定的自环重复计费错误；两个 `ScheduleMode` 的目标语义与真实阶段完成时间不一致；扫参方向存在大量 no-op 组合；另外还有确定性和数值边界问题。建议先修复 P0，再决定 P1 的语义，最后处理 P2 和测试缺口；在 P0 修复前，包含同机流量的 topology 结果不宜直接用于论文或算法对比。

| 编号 | 严重度 | 问题 | 判断 |
|---|---|---|---|
| P0-01 | 确定错误 | self-loop 消费同机 intrinsic，导致重复计费 | 已用最小场景复现 |
| P1-01 | 语义不一致 | `BandwidthAware` 只用 serial 接受候选，忽略 alpha | 代码确定；是否为错误取决于目标定义 |
| P1-02 | 语义不一致 | `CompletionTimeAware` 用单对 edge cost，不是真实 stage 完成时间 | 代码确定；与 mode 名称/目标不符 |
| P1-03 | 扫参有效性 | Global/Local 活跃但没有候选生成器时，plan 仍是 seed | 代码确定；扫参点会重复/误导 |
| P2-01 | 可复现性 | topology matching 的 tie-break 依赖 `unordered_set` 顺序 | 跨构建/平台不保证 |
| P2-02 | 数值边界 | 时间映射可能下溢，绝对容差下限可能接受过小流量 | 边界场景风险 |
| P2-03 | 测试缺口 | 无混合对角、alpha/serial 反向、共享瓶颈、no-op 测试 | 现有测试无法覆盖上述问题 |

---

## P0-01：同机 intrinsic 流量在 self-loop 阶段被重复计入

### 位置

- `simulation/scheduler/local.cpp:178-189`：`prepare_load_balance()` 的同机分支复制 `intrinsic_all2all`，但没有从 `balanced_data` 的对角块清除。
- `simulation/scheduler/global.cpp:143-148`：`collect_pending_units()` 把 `d == s` 的对角项也传给 `FastAll2All`。
- `simulation/algorithm/all2all.cpp:150-154`：`consume_pending()` 对所有源 server 扣减 `remaining_units_`，包括 `m[s] == s` 的 self-loop。
- `simulation/scheduler/global.cpp:576-592`：每个 stage 调用 `server2server_dispatch(dst_svr, ...)`；当 `dst_svr == src_svr` 时没有特殊处理。
- `simulation/scheduler/local.cpp:343-363`：`server2server_dispatch()` 没有跳过同机块；`i != j` 时仍累计 `r.dispatch[i * gpu_n + j]`。
- `simulation/scheduler/global.cpp:669-681`：`pipeline2` 又把 `intrinsic_all2all` 计入一次。
- `simulation/algorithm/all2all.cpp:134-136`：`decompose()` 的最终完整性检查只检查 `s != d`，不会发现对角线残留或被消费。
- `simulation/scheduler/global.cpp:149-159`：`topology_stage_cost()` 跳过 self-loop，因此网络代价项看不到这部分流量，但 server 内 dispatch 仍会计入。

### 问题机制

同机流量本应只由 `intrinsic_all2all` 表示一次。当前流程中：

1. `prepare_load_balance()` 把同机 `data` 复制到 `intrinsic_all2all`，但 `balanced_data` 的对角块仍保留原值。
2. `collect_pending_units()` 把对角块作为 `pending` 交给 `FastAll2All`。
3. 双随机补齐可能产生 `s -> s` 的 self-loop；`consume_pending()` 会把对角块从 `remaining_units_` 中扣掉。
4. `pipeline2` 在 dispatch 阶段对 self-loop 调用 `server2server_dispatch(s, ...)`，从 `balanced_data` 的对角块取数并生成 server 内 dispatch。
5. 同时 `intrinsic_all2all` 仍被计入 E2E 时间。

结果是同一份同机字节既进入 intrinsic 时间，又进入 self-loop 的 dispatch 时间。

此外，`row_sum` 的 self 槽位在 `prepare_load_balance()` 中没有被赋有效值（`memset` 后保持为 0）；`server2server_dispatch()` 对 self-loop 执行 `row_sum[to_server_id * gpu_n + i] -= transfer` 时是无符号 `uint` 减法，会发生下溢。`local.cpp:179-189, 353`。

### 复现

使用 `N=3, G=2`：

- server 0 与 server 1 之间有跨机流量；
- server 2 只有同机 `GPU0 -> GPU1` 100 MB；
- server 2 没有跨机流量，因此 server 级矩阵的第 2 行/列为 0，双随机补齐会加入 `(2,2)` 对角项；
- 调用 `GlobalScheduler::set_schedule(Native, &top)` 后执行 `pipeline2`。

分解结果出现：

```text
stage 0 freq=200000000
mp: 0->1 1->0 2->2(self)
payload: ... [4]=100000000
```

其中 `[4]` 是 `src_server=2, src_gpu=0` 的扁平索引 `2*G+0`。该 payload 正是 server 2 的同机 100 MB。

运行结果同时出现：

```text
intrinsic_us = 230.1
dispatch_us  = 230.1
```

同一份 100 MB 在 intrinsic 和自环 dispatch 中各计了一次。该复现程序位于 `/tmp/repro_global_diag.cpp`，未进入仓库。

### 影响

- 对包含同机流量的 workload，E2E 时间会被高估；高估量取决于 BvN 是否生成 self-loop stage。
- `row_sum` 下溢会污染 server 内调度元数据，即使当前主要读取路径尚未立即崩溃，也说明自环语义没有处理完整。
- `prepare_topology_stage()` 跳过 self-loop，使 self-loop 在 `BandwidthAware` 的候选排序中表现为零成本边，可能改变实际 cross-server 匹配选择。
- `decompose()` 最终检查跳过对角线，因此该错误不会在运行时被断言发现。
- 如果同机流量在不同 workload 中比例不同，算法排名可能被这一项系统性偏差影响。

### 测试缺口

- `simulation/tests/test_topology_schedule.cpp:65-66` 的 6-server 测试明确把对角设为 0。
- `global_tests()` 的 intra-only 场景没有“同机流量 + 跨机流量”的混合矩阵：`test_topology_schedule.cpp:125-131`。
- 目前没有测试断言 self-loop stage 不消费 intrinsic，或 intrinsic 只计一次。

### 建议

优先级最高。可选方案：

1. topology 路径的 `pending` 在交给 `FastAll2All` 前把 `(s,s)` 对角项置零；或者 `collect_pending_units()` 不返回对角项。
2. `consume_pending()`、`build_transfers()`、`server2server_dispatch()` 对 `dst == src` 统一跳过；self-loop 只作为 BvN 的虚拟容量，不消费真实 intrinsic。
3. `prepare_load_balance()` 复制 intrinsic 后明确清零 `balanced_data` 对角块，并初始化/维护 self 对应的 `row_sum` 槽位。
4. 增加混合场景回归测试，至少检查：
   - 不出现由 intrinsic 驱动的 self-loop payload；
   - `intrinsic_us` 只计一次；
   - `row_sum` 不会无符号下溢。

---

## P1-01：`BandwidthAware` 的接受条件忽略 propagation

### 位置

- `simulation/algorithm/all2all.cpp:265-269`：`BandwidthAware` 只比较 `top.evaluate_gpu(...).serial_us`。
- `simulation/topology/model.cpp:146-155`：实际阶段时间 `total_us = alpha_us + serial_us`。

### 问题

候选只要降低 serialization 就可能被接受，即使它把流量换到更高传播延迟的路径，导致总阶段时间增加。

例如：

- seed 路径：alpha 5us，serial 10us，总时间 15us；
- candidate 路径：alpha 100us，serial 1us，总时间 101us。

`BandwidthAware` 会看到 serial 从 10 降到 1 并接受 candidate，但总阶段时间从 15 增到 101。若这个 mode 的真实目标是“降低带宽竞争/串行时间”，这是合理的专用目标；若它的含义是“改善阶段完成时间”，则 acceptance 条件不完整。

### 影响

- 不同路径的传播延迟差异较大时，`BandwidthAware` 可能产生负收益。
- 扫参中该 mode 的胜/负结果可能主要反映 serial 项，而不是 E2E 完成时间。
- 与 `CompletionTimeAware` 的对比可能被误读为“拓扑优化”的差异。

### 建议

- 如果目标确实是带宽/串行项：在文档、JSON 字段和实验说明中明确写成 bandwidth-only，不要用“完成时间改进”描述。
- 如果目标是完成时间：用 `serial_us + alpha_us` 或直接比较 `evaluate_gpu(...).total_us`。
- 增加低 serial / 高 alpha 与高 serial / 低 alpha 的对抗测试。

---

## P1-02：`CompletionTimeAware` 使用的是单对 edge cost，不是实际 stage 完成时间

### 位置

- `simulation/algorithm/all2all.cpp:157-183`：`prepare_topology_stage()` 计算每个 server pair 的 `edge_cost_us = alpha + pair-local serialization`。
- `simulation/algorithm/all2all.cpp:225-250`：`match_completion_time()` 用这个 per-pair cost 做阈值匹配。
- `simulation/algorithm/all2all.cpp:270-273`：`accept_candidate()` 只比较 seed/candidate 的 `max(edge_cost_us)`。
- `simulation/topology/model.cpp:139-155`：实际阶段代价是 `max(flow propagation)` 加上**所有 flow 共享资源字节聚合后**的最大 serialization。

### 问题

`edge_cost_us` 不包含不同通信对共享同一资源时的串行叠加。真实 stage 时间不是：

```text
max over pairs (pair_alpha + pair_local_serial)
```

而是：

```text
max over flows propagation
+ max over resources (aggregate bytes on resource / resource bandwidth)
```

因此可能出现：

- seed 的 per-pair 最大 edge cost 高于 candidate；
- 但 candidate 把多个 flow 放到同一共享瓶颈上，真实 `evaluate_gpu()` 阶段时间反而更高；
- `accept_candidate()` 仍接受 candidate，因为比较的是未考虑跨对共享的 edge cost。

### 影响

- `CompletionTimeAware` 不是实际的完成时间优化器，最多是“单对路径阈值 + 归一化匹配”的启发式。
- 在多级异构拓扑中（T1/T2/T3 这类网关/WAN 共享场景），共享瓶颈正是需要优化的部分，偏差会直接出现在实验结论中。
- 与真实评估器 `Topology::evaluate_gpu()` 的口径不一致，可能造成“规划时选择更优、仿真时更差”的候选。

### 建议

- 保留 per-pair threshold 作为候选生成启发式，但把 `accept_candidate()` 的比较改为对 seed/candidate 的真实 stage flows 调用 `top.evaluate_gpu()`，比较 `total_us`。
- 如果继续使用 edge cost，应在 mode 名称、输出 JSON 和文档中明确它是 proxy，而不是完成时间。
- 增加“两流共享同一瓶颈”的测试，验证接受准则与实际阶段时间同序。
- 若要真正优化阶段完成时间，候选生成也应考虑资源聚合负载，而不只是 per-pair cost；至少 acceptance 必须使用实际评估器。

---

## P1-03：扫参组合存在 no-op 点

### 位置

- `simulation/algorithm/bvn_schedule.cpp:514-516`：只有 Global 和 Local 都为 `None` 时，`validate_options()` 才限制候选生成器。
- `simulation/algorithm/bvn_schedule.cpp:420-471`：`plan_partial()` 的额外候选只来自 `pressure_candidate` 和 `local_edits`。
- `simulation/algorithm/bvn_schedule.cpp:392-483`：没有候选时，每个 stage 直接使用 seed matching。

### 问题

以下配置会被接受，但不会产生任何新计划：

```cpp
Options o;
o.global = GlobalRule::Pressure;
o.local = LocalRule::StageTime;
o.candidate_budget = 1;
o.pressure_candidate = false;
o.local_edits = false;
```

此时：

- `candidate_budget` 从 1 开始计数，seed 已占 1；
- `pressure_candidate == false`，不调用 pressure matching；
- `local_edits == false`，不执行删除/插入/交换；
- `plan_partial()` 只 materialize seed，然后 consume；
- `Pressure`/`StageTime` 只被计算用于 seed 评分，从未用于比较第二个候选。

即使把 `candidate_budget` 设为 8，只要两个开关都关闭，仍然只有 seed。

### 影响

- 参数扫描中不同 Global/Local 组合可能输出完全相同的 plan，但被当成不同算法样本。
- 统计胜/平/负、几何平均加速比时，重复点会改变权重，尤其是组合数量远多于有效候选生成器时。
- 后续“某个目标更好”的结论可能只是重复采样了 baseline。

### 建议

二选一：

1. **拒绝**：在 `validate_options()` 中要求活跃的 Global/Local 目标至少有一个候选生成器，或 `candidate_budget >= 2` 且存在有效候选来源。
2. **显式标记**：在 `Plan`/JSON 中输出 `candidate_generators` 或 `noop=true`，扫参脚本自动去重，报告中明确“只评分不改调度”的对照。

同时补测试：活跃目标 + 两个开关关闭时，plan 是否与 baseline 相同；如果相同，输出应标记 no-op。

---

## P2-01：topology matching 的 tie-break 依赖 `unordered_set` 迭代顺序

### 位置

- `simulation/algorithm/all2all.h:52`：`row_to_col` 类型是 `vector<unordered_set<uint>>`。
- `simulation/algorithm/all2all.cpp:198-210`：`match_bandwidth()` 按 `row_to_col[s]` 的迭代顺序给候选分配 `ordinal`；当 `peak`、`square` 相等时按 `ordinal` 排序。
- `simulation/algorithm/all2all.cpp:229-243`：`match_completion_time()` 也直接迭代 `row_to_col`，同阈值下可能有多个完美匹配。
- 代码注释称“ties keep native order”，但 `unordered_set` 的迭代顺序不是插入顺序，也不是可移植的固定顺序。

### 问题

同一输入、同一进程内的重复运行通常稳定；但跨 libstdc++ 版本、跨编译选项或不同插入历史不保证相同。对于均匀拓扑或大量等代价边，`peak`/`square` 大量相等，tie-break 会直接决定匹配。

### 影响

- 跨构建复现实验时，`BandwidthAware`/`CompletionTimeAware` 可能产生不同 plan。
- 现有测试主要验证同一二进制内的重复运行，无法发现跨平台差异。
- 如果论文声称确定性调度，需要在实现上真正固定 tie-break。

### 建议

- 将 candidate 排序的 tie-break 改为目标 destination 编号，或先在 `row_to_col[s]` 中按列编号排序后再分配到 `ordinal`。
- `match_completion_time()` 的 `dfs` 迭代同样按目标编号排序。
- 增加一个包含大量等代价候选的确定性测试，最好在不同 `unordered_set` 插入顺序下构造等价输入。

---

## P2-02：时间映射的数值边界与容差下限

### 位置

- `simulation/algorithm/bvn_schedule.cpp:13-15`：`tolerance()` 有 `1e-6` 绝对下限。
- `simulation/algorithm/bvn_schedule.cpp:275-296`：`materialize()` 使用 `actual = coefficient / weight`，要求 `x > 0` 且误差不超过 `tolerance`。
- `simulation/algorithm/bvn_schedule.cpp:367-391`：`consume()` 对 limiting 边设置零，并要求非 limiting 边不超扣。

### 问题

在极端带宽、传播延迟或极小需求下：

- `coefficient / weight` 可能小于 double 的最小可表示正数，`x` 变为 0，`require(x > 0)` 直接抛异常；
- `1e-6` 的绝对容差会让非常小的真实流量在守恒检查中以 1e-6 的误差被接受；
- 对 `Space::SerializationTime`/`DelayAdjustedTime`，这不是“字节整数”场景，误差语义需要单独定义。

正常规模的 1 KiB 单位场景不太会触发，但作为扫参工具，应明确这是“快速失败”还是“应钳制后继续”。

### 建议

- 增加极端带宽比（例如 1 Gbps 与 1 Tbps 混合）和极小需求的测试。
- 在 `materialize()` 捕获下溢时给出包含 pair、weight、coefficient 的错误信息，或定义最小正流量策略。
- 对时间空间使用与字节空间不同的误差说明，不要把 `1e-6` 直接解释为“1 字节精度”。

---

## P2-03：测试缺口

现有测试做得好的部分：

- `test_topology_schedule.cpp` 覆盖了 topology API、legacy compatibility、零需求、intra-only、direct decompose；
- `test_bvn_schedule.cpp` 覆盖了 validation、守恒、目标函数、deterministic plan、CLI；
- `bvn-check` 当前报告 5323 项通过。

但以下场景没有覆盖：

1. **混合对角**：同机流量 + 跨机流量 + 双随机补齐产生 self-loop（P0-01）。
2. **alpha/serial 反向**：`BandwidthAware` 是否会接受总时间更差的候选（P1-01）。
3. **共享瓶颈**：多个 flow 共享同一资源时，`CompletionTimeAware` 的接受准则与 `evaluate_gpu()` 是否一致（P1-02）。
4. **no-op**：Global/Local 活跃但候选生成器关闭时，plan 与 baseline 相同，是否应拒绝或标记（P1-03）。
5. **确定性**：等代价候选、不同 `unordered_set` 插入顺序、不同构建。
6. **极端数值**：时间映射下溢、极小需求、绝对容差下限（P2-02）。

---

## 独立 `bvn` 核心的判断

在本次审计范围内，未发现独立 `bvn_schedule.cpp` 中会破坏守恒或终止性的确定错误：

- `matching()` 是标准 Kuhn 最大基数匹配：`bvn_schedule.cpp:182-242`。
- `materialize()` 用匹配边最小残余作为 coefficient，并按 `Z = weight × remaining` 恢复真实字节：`bvn_schedule.cpp:275-340`。
- `consume()` 维护 real remaining、auxiliary residual 和 resource work，并要求至少耗尽一条边：`bvn_schedule.cpp:367-391`。
- `plan_partial()` 有 support 上界、候选预算、编辑预算和最终逐对守恒检查：`bvn_schedule.cpp:392-483`。
- `plan_native()` 只投影真实需求，过滤 native stuffing 的虚拟流量：`bvn_schedule.cpp:80-114`。

需要区分“正确性”和“最优性”：

- seed 使用的是**最大基数匹配**，不是最大瓶颈匹配；它可能选择更多边但更小的 coefficient，增加阶段数。这作为 partial endpoint baseline 是合理的，但不能声称产生最优 partial BvN。
- `GlobalRule::Pressure` 的候选只是按压力排序的 Kuhn 增广，不是最大权匹配；代码注释明确说“不声称求解最大权匹配”：`bvn_schedule.cpp:183`。
- Global/Local 规则主要是候选评分器；没有对应的候选生成器时，目标本身不会改变 plan（见 P1-03）。
- `plan_partial()` 的局部编辑只包含删除、插入未匹配行、交换两个已匹配目标；没有任意“已匹配行换到一个未使用目标”的单步邻域。这是启发式覆盖范围问题，不是守恒错误。

---

## 独立 `bvn` 与合并 FAST 路径不一致

需要特别提醒：独立的 `bvn_simulator` 和合并的 `all2all_simulator --topology` **不是同一个算法**：

- 独立 `bvn` 的 `Engine::Partial` 不做 native stuffing，直接做 partial matching；
- 合并 `FastAll2All` 在 topology 模式下仍使用 native SDSM、stuffing、balance/dispatch 和 `pipeline2` overlap；
- 独立 `bvn` 的 S/G/L/F 轴与合并 `ScheduleMode::{Native,BandwidthAware,CompletionTimeAware}` 不是一一映射；
- 因此两边相同名称的“拓扑候选”结果不可直接互相验证，也不能用合并路径的结果替代独立扫参结论。

如果论文/实验同时使用两套入口，需要在方法部分明确两者对应的算法层级，否则容易被理解为同一个算法的两种实现。

---

## 建议修复顺序

1. **P0-01**：先统一 self-loop 语义，过滤/清零对角 pending，避免 intrinsic 被 topology 分解和 dispatch 重复消费；补混合场景回归测试。
2. **P1-02**：决定 `CompletionTimeAware` 是“真实阶段完成时间”还是“per-pair threshold proxy”；若是前者，用 `evaluate_gpu()` 的真实 `total_us` 做 acceptance，并考虑候选生成。
3. **P1-01**：明确 `BandwidthAware` 的带宽-only 语义，或改为比较 `alpha + serial`。
4. **P1-03**：拒绝或标记 no-op 组合，扫参脚本去重。
5. **P2-01/P2-02**：固定 tie-break，补极端数值测试。
6. **重新跑受影响实验**：包含同机流量的 E2E 结果、`BandwidthAware`/`CompletionTimeAware` 对比、以及所有 no-op 组合的统计。

---

## 建议新增的最小测试

1. **P0 混合对角**：`N=3, G=2`，0↔1 跨机 + server2 同机 100 MB；断言 self-loop 不消费 intrinsic、`intrinsic_us` 只出现一次、`row_sum` 无下溢。
2. **P1 alpha/serial**：两条路径分别低 alpha/高 serial 与高 alpha/低 serial；断言 `BandwidthAware` 的行为符合其定义（若为完成时间目标，不应接受总时间更差的候选）。
3. **P1 共享瓶颈**：两个 flow 共享同一资源，比较 `max(edge_cost)` 与 `evaluate_gpu().total_us` 的排序是否一致；不一致时测试应能捕获。
4. **P1 no-op**：`global=Pressure, local=StageTime, pressure_candidate=false, local_edits=false`；断言被拒绝或输出标记 `noop`。
5. **P2 确定性**：等代价候选，改变 `row_to_col` 插入顺序，断言 plan 相同。
6. **P2 数值**：极端带宽/极小需求，断言下溢时是显式失败并带上下文，或按定义钳制。

---

## 审查限制

- 本报告是静态审计加最小复现，不是形式化证明；未运行完整的 290 份扫描实验。
- 行号基于当前工作区版本，后续修改可能导致偏移。
- P1-01/P1-02/P1-03 是否算“错误”取决于模式定义和实验解释；本报告按“名字/目标与实现行为是否一致”判断，并明确标注了不确定性。
- 未修改任何仓库代码；P0 复现程序在 `/tmp`，未纳入版本控制。
