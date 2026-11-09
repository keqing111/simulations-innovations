# FAST Simulator

This folder contains the simulator for **FAST**, an efficient scheduler for all-to-all communication.

## Folder structure

```text
simulation/
├── algorithm/
│   ├── all2all.h/.cpp      # FastAll2All decomposition (server-level permutation sets)
│   └── matrix.h/.cpp       # Matrix utilities
├── scheduler/
│   ├── local.h/.cpp        # Intra-server scheduling
│   ├── global.h/.cpp       # End-to-end FAST pipeline and runtime model
│   └── config.h/.cpp       # Link model + baselines
├── topology/model.h/.cpp   # Static GPU routes, shared resources and stage cost
├── tests/                  # Native API, CLI and migration regression tests
├── plot/                   # Plot scripts and generated figures
├── main.cpp                # Entry point: choose which benchmark/test to run
├── test.h/.cpp             # Workload generation + benchmark drivers
├── define.h                # Global constants (for example BENCHMARK_DIR)
└── Makefile                # Build and run targets
```

## Build and run

From the `simulation/` directory:

```bash
make clean
make build
make check
make run
```

Binary path:

```text
simulation/build/all2all_simulator
```

## 拓扑策略的正式入口

两条改进已归入 `algorithm/all2all.h/.cpp`，没有新增Planner或调度器类。
`LocalScheduler` 不变；`GlobalScheduler` 只负责策略配置、实际数据传递和网络计价。

```cpp
native_topology::Topology topology(server_n, gpu_n, dc_ids,
                                  400, 400, 100, 5000);
GlobalScheduler scheduler(server_n, gpu_n, locals,
                          ETHER400, H100, false, MBpu);
scheduler.set_schedule(ScheduleMode::BandwidthAware, &topology);
auto result = scheduler.pipeline2(MBpu);
```

|模式|每轮匹配做什么|
|---|---|
|`Native`|直接使用原生Hungarian匹配|
|`BandwidthAware`|在原生本轮系数允许的边上，用增广路构造候选；优先减小共享资源负载/带宽的峰值|
|`CompletionTimeAware`|为服务器对计算实际量对应的传播加串行时间；二分时间阈值，寻找最大边代价最小的完美匹配|

两条策略都不改变原始需求矩阵。候选必须保持本轮系数、真实发送量不减少，且对应目标严格改善，否则保留原生匹配；这不代表全局最优或每个负载必然加速。虚拟补齐容量不计入网络字节。

启用拓扑时，三种模式统一以实际派发量计价：每个共享有向资源先累加字节，再取资源传输时间最大值，加上活跃路径最大单向传播时间。没有逐流动态带宽、额外barrier或新流水线。传播时间和RTT均可存入路径，但当前计价使用单向传播时间，不重复加入RTT。

不调用 `set_schedule()` 时，原生默认行为及alpha-beta计价保持不变。
配置的拓扑必须在整个调用期间存活且不再修改。每次比较策略应重新创建Local/Global，不能重复消耗同一份Local数据。

### 单一构建产物的命令行入口

```text
build/all2all_simulator --topology N G MODE WAN_US SERVER_GBPS GATEWAY_GBPS WAN_GBPS MBpu DC_CSV
```

- `MODE` 为 `native`、`bandwidth` 或 `time`。
- stdin输入 `(N*G)²` 个非负整数，按源GPU行、目的GPU列展开，对角线为0；值是原生数据单位。
- `MBpu` 是每单位的十进制MB数，例如 `1e-6` 表示一个单位一个字节；带宽Gbps，传播时延微秒。
- `DC_CSV` 如 `0,0,1,1,2,2`；此便捷入口使用现有多DC拓扑与H100机内模型，关闭缩放。
- 输出JSON含总时间、机内时间、阶段匹配、系数、实际payload及网络时间分解。无参数时仍运行原先benchmark入口。

任意固定单路径拓扑通过C++的 `Topology::from_fixed_routes()` 输入GPU对路径和共享资源；不受CLI的多DC模板限制。
详见 [INTERFACE_BOUNDARY.md](INTERFACE_BOUNDARY.md) 的接口、所有权和兼容约定。

### 历史代码与验证

`independent/`、`scan/`、`planning/`、`bottleneck/` 等目录保留为历史实验和核对工具，不是上述正式入口的依赖；本次不删除归档或用户批注。正式算法只需阅读A2A、Topology和Global的相关函数。

`make check` 运行C++接口测试和Python端到端测试；存在历史26组归档时还逐阶段比对三种策略的78份结果。没有归档时该项明确跳过，其余测试和正式构建不受影响。测试中的论文链路参数只用于复现历史输入，不修改正式默认链路参数。

## Workload and testbed setup (`test.cpp`)

- **Workload (traffic matrix)** is generated in `test.cpp` (random matrix per trial, diagonal forced to 0).
- **Testbed scale** is controlled by `server_n` and `gpu_n` in `FastAll2AllTester`.

Main configuration entry is in `main.cpp`:

```cpp
FastAll2AllTester simulator(2, 16, 20, false, ETHER400, H100);
simulator.server_gpu_number_benchmark(H100, ETHER100);
```

Constructor arguments are:

```text
FastAll2AllTester(server_n, gpu_n, test_times, enable_scaling, inter_link_type, intra_link_type)
```

`test.cpp` provides benchmark methods such as:
- `run()`
- `server_gpu_number_benchmark(...)`
- `fabric_speed_benchmark()`
- `topology_benchmark(...)`
- `skewness_benchmark(...)`
- `transfer_size_benchmark(...)`

## Where FAST scheduling results are stored

Core result container is in `algorithm/all2all.h`:

- `class FastAll2All`
- `vector<PermutationSet> p_sets;`

After calling:

1. `to_scaled_doubly_stochastic_matrix()` (and optional `to_scaled_matrix(...)`)
2. `decompose()`

`p_sets` stores the Birkhoff decomposition result:

- each `PermutationSet` holds `mp` (row server -> destination server mapping)
- `get_freq()` gives the transfer frequency/weight for that permutation

`GlobalScheduler::pipeline2(...)` consumes `all2all.p_sets` to build inter-server transfer order and intra-server dispatch schedule.

## Benchmark outputs

Benchmark functions in `test.cpp` write text outputs under `benchmark/` (for example speedup/time data).
Create this directory before running benchmarks:

```bash
mkdir -p benchmark
```
