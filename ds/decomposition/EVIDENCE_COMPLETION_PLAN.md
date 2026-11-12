# Paper Evidence Completion Implementation Plan

> 实现方式：主 agent 在当前会话按测试驱动执行；不自动提交、推送或启动新的 agent。

**Goal:** 补齐强基线、最终资源感知算法与在线成本证据，暂缓论文正文。

**Architecture:** 保留原生 Local/Global/补齐/分解默认路径。优先扩展已有研究 CLI，而不是重写仿真器；先完成原生 scaling 对照，再对齐 GPU 轮转基线，最后依据差距改进匹配生成。

**Tech Stack:** 现有 C++11、Make、Python unittest 与 JSON 结果。

**Spec:** 用户要求优先补齐论文所需内容；边界见 `RESEARCH_STORY_AND_BASELINES.md`，写作暂存见 `../../paper/WRITING_HANDOFF.md`。

## 全局约束与重点检查

- 保留默认 scaling=false 与原生全部注释，不改算法数学规则。
- 缩放容量可能大于真实需求；只统计实际 payload，逐服务器对守恒。
- 不将分解 CPU 当作全规划成本；scaling 时间需保留在规划墙钟中。
- 同时报告强基线开/关模式，不按测试结果逐案例选最好策略。
- 不覆盖历史结果；新的可执行文件 hash 不冒充旧实验二进制。

## 1. 原生 scaling 实验开关（当前实施）

文件：`simulation/bvn_decompose_main.cpp`、`simulation/tests/test_bvn_scaling_cli.py`。

- [x] 先写 CLI 测试：显式关等于默认、开时逐对交付及流水线重算一致、非法参数拒绝；运行得到 unknown option 的预期失败。
- [x] 添加 `--scaling 0|1`，默认 0，仅 pipeline 模式允许开启；透传原生 GlobalScheduler 构造参数，不修改 scaling 判据。
- [x] JSON 增加 `scaling_enabled`，保留原有输出字段语义。
- [x] 运行 N8/16、G4、均匀/Zipf、S0/S1 的开关配对初测，核对真实交付、阶段与 CPU/规划时间。结果见 `SCALING_CONTROL_REPORT.md`。
- [x] `make -C simulation check bvn-check bvn-decompose-build` 通过：集成 C++、6个Python测试及独立BvN的5443项检查。

## 2. 完整 GPU 级 spreadout-like 对齐（实现与有限面板已完成）

复用 Topology::evaluate_gpu 的跨服务器共享资源计价，轮转使用原始 GPU 对数据，不经过 FAST Balance。机内采用明确、与原生参数一致的计价，并核验混合机内/机间轮次的执行语义；不直接把旧独占 alpha-beta 基线与新共享网络模型排名。

先以两 GPU、独立路径、共享 NIC、共享 WAN、混合轮次及零流量解析测试固定接口，再实现；输出逐轮流、资源字节、总时间与规划时间。此任务不能在缺少上述对账时标成完成。

2026-11-12：完成6项测试与60份开发输出，见 `BASELINE_CONTROL_REPORT.md`。逐流/逐资源/完整流水线对账通过；统一规划CPU、更多拓扑/独立负载仍待正式验证，不标成完整论文矩阵。

## 3. 最终拓扑匹配算法（基线明确后收敛）

针对等 q 原型跳过大量 Zipf 阶段的问题，在合法残余/粒度约束内让资源信息指导匹配生成。先确定一个有界规则并给出守恒和预算，再实现小例、C++ 成本与完整流程消融；不扩大删插枚举来强行提高胜率。

## 4. 冻结后验证

新需求/种子、容量与传播控制、端点放置和规模；独立报告退化、完整操作时间、规划成本与模型敏感性。真实 trace 未提供时不填真实负载结论。完成前三项前，不启动大网格或回填最终摘要收益。
