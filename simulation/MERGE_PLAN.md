# FAST 原生代码归并 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Inline implementation, test first, one final independent review.

**Goal:** 将两条拓扑策略归入原生A2A，保留Local和默认调用，正式构建不依赖历史实验源码。

**Architecture:** Topology提供只读GPU路径和整数资源索引；FastAll2All内实现策略与复用工作区；Global仅配置、传递实际数据及统一计价。沿用原生结果类型和pipeline。

**Tech Stack:** C++11、现有Makefile、C++断言测试、Python只作迁移对照。

**Spec:** [INTERFACE_BOUNDARY.md](INTERFACE_BOUNDARY.md)

## Global Constraints

- 不改Local、Matrix实现和原生匹配默认语义，不覆盖用户批注。
- 不引入新Planner、新算法模块、逐流动态或其他调度方向。
- 默认旧入口可用；启用拓扑时三种策略使用同一计价器。
- 通用GPU路径与旧服务器路径接口明确区分；浮点字节不截断到旧Cost。
- 不删历史目录，不提交/推送，不创建worktree；用户明确要求在当前代码中归并，已有dirty改动保留。

## Review Focus

- GPU路径不同、共享资源及零流量：独立核对资源负载和阶段费用。
- 原生单位/缩放与非整数字节：禁止把矩阵系数直接当字节。
- 空/仅机内输入、虚拟自环：不生成跨机网络费用，不改Local历史语义。
- 借用拓扑生命周期、GPU-only旧接口、冲突hooks：拒绝非法配置。
- 正式构建与迁移对照：原生和新策略均不引用results/scan/independent源码。

### Task 1: Topology缓存及接口

**Files:** Modify topology/model.h, topology/model.cpp; Create tests/test_topology_schedule.cpp.

**Interfaces:** from_fixed_routes, gpu_path, resource, resource_count, path_min_bandwidth_gbps, evaluate_gpu。

- [x] 写测试：共享资源、GPU路径、浮点字节、旧接口兼容与拒绝边界；观察缺接口RED。
- [x] 加入缓存和查询实现，保留旧结构；检查测试GREEN。

### Task 2: 原生A2A策略

**Files:** Modify algorithm/all2all.h, algorithm/all2all.cpp; extend tests/test_topology_schedule.cpp.

**Interfaces:** ScheduleMode、decompose重载、hungarian_info_t工作区；旧decompose不变。

- [x] 写测试：原生输出一致、两机制输入、单位/缩放、输入不变、真实需求守恒；观察RED。
- [x] 迁移最少必要增广构造和阈值匹配，移除正式算法对Planner/ResourceCache的依赖；测试GREEN。

### Task 3: Global接线及主构建

**Files:** Modify scheduler/global.h, scheduler/global.cpp, main.cpp, Makefile; extend tests/test_topology_schedule.cpp.

**Interfaces:** set_schedule、collect_pending_units、topology_stage_cost；pipeline2旧签名不变。

- [x] 写测试：旧默认、三策略同拓扑计价、空/机内、冲突hooks与不支持pipeline；观察RED。
- [x] 加入小范围接线及正式CLI，Local不改；make build/check通过。
- [x] 通过现有实际输入和冻结输出逐项对照匹配、系数、payload、pipeline费用；运行相关既有套件。

### Task 4: 精简接入、文档与审查

**Files:** Modify simulation/README.md, INTERFACE_BOUNDARY.md as needed; test utilities only if necessary.

- [x] 记录正式入口和历史目录地位；不删除归档、用户改动。
- [x] 检查核心依赖、默认兼容、Local摘要未变。
- [x] 一次只读独立审查；修复重要问题并运行完整相关套件。
- [x] 记录测试结果、未解决限制、实际新增接口和代码规模，不声称删除了未清理的历史目录。
