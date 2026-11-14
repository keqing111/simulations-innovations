# 隔离的资源预算BvN原型

工作区：`/home/julius/fast/wan-budget-v1`；分支：`research/wan-budget-v1-20261115`。从当前SimAI工作内容复制必要源快照，不修改原目录，不合并、不push。

## 先看结果

[最小功能报告](astra-sim-alibabacloud/experiments/alltoall/results/wan-budget-smoke-fixed-20261115/REPORT.md)：13项原型测试、39项复用组件回归和9次ns3回放通过。两例持平，一例拆分慢4.315%；仅证明实现的合法性和机制，不是正向性能结论。

## 代码入口

- [核心模块](astra-sim-alibabacloud/experiments/alltoall/src/simai_a2a/schedulers/resource_budget.py)：静态资源、真实压力、锚点/q、预算匹配、评分及残差更新。没有新增类体系。
- [原型测试](astra-sim-alibabacloud/experiments/alltoall/prototype_tests/test_resource_budget.py)：数学/容量/合法性/有限进展；小规模穷举只在测试核验SCC，不用于规划。
- [交付与拆分测试](astra-sim-alibabacloud/experiments/alltoall/prototype_tests/test_smoke.py)：非零balance/dispatch、逐GPU交付、小q选择。
- [最小实验入口](astra-sim-alibabacloud/experiments/alltoall/tools/run_wan_budget_smoke.py)：三份固定输入×FAST-SimAI/full/split，复用已有SimAI/ns3事件回放，无新barrier。
- [批准边界](WAN_BUDGET_SPEC.md)、[实施计划](docs/superpowers/plans/2026-11-15-wan-budget-prototype.md)、[执行记录](WAN_BUDGET_PROGRESS.md)、SOURCE_SNAPSHOT.json。

直接使用：

```python
prepared = prepare(matrix, hardware)       # 现有独立SimAI整数预处理
result = plan(prepared, topology_snapshot, allow_split=True)
full = materialize(prepared, result['permutations'], result['coefficients'],
                   hardware, 'wan-budget-split')
projected = modeled_scaleup_schedule(full, hardware)
```

`plan`只在副本上更新R/U，原始带来源库存留给materialize真实取数。新计划按生成顺序执行；不得按q再次排序。未改旧调度器注册表，避免改变正在进行的旧实验默认行为。

局限详见报告：结构性交接不保证实际提前换目标，贪心支持图可补全不等于容量可补全，模型评分不是异步ns3完成时间。当前下一步是修正这些已观察到的机制缺口，不是扩大性能扫描。

原始固定回放产物在工作区保留展开版；Git中以`results/wan-budget-smoke-fixed-20261115.tar.gz`归档，另直接保留报告、图和汇总，避免数百个日志文件占据代码审查视图。归档中全部474个文件逐字节核对通过。若以后从Git检出，需要分析完整原始日志时可在实验目录运行：

```bash
tar -xzf results/wan-budget-smoke-fixed-20261115.tar.gz -C results
```
