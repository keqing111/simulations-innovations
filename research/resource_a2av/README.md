# Resource-aware A2Av research entry

这是当前研究的集中入口，不复制核心算法、不重排历史目录。

- [简版报告：依据、流程、代码路径和已知局限](REPORT.md)
- [拓扑×负载实验矩阵](experiment_matrix.json)
- [异构实验入口](experiment.py)：构建拓扑、分布与字节规模，复用现有四方法回放
- [完整输入迁移验证](../../HETEROGENEOUS_TOPOLOGY_VALIDATION.md)
- [前期机制和消融报告](../../WAN_BUDGET_ITERATIONS_REPORT.md)
- [27条件完整配对表](../../astra-sim-alibabacloud/experiments/alltoall/results/resource-heldout-20261115/paired.csv)
- [结果与审计记录目录](../../astra-sim-alibabacloud/experiments/alltoall/results/resource-heldout-20261115/)

目录边界：

```text
research/resource_a2av/
  README.md                  唯一阅读入口
  REPORT.md                  简洁算法说明与研究边界
  experiment_matrix.json     预先固定的拓扑、负载和分组实验
  experiment.py              异构实验入口，不复制调度算法
  runtime/ns3-local.patch    当前本地ns-3运行时增量，不含凭据/二进制

astra-sim-alibabacloud/experiments/alltoall/
  src/simai_a2a/schedulers/resource_budget.py   核心实现（唯一真源）
  src/simai_a2a/schedulers/simai_plan.py        整数预处理与真实交付物化
  tools/run_resource_budget_iterations.py     四方法回放复用入口
  tools/run_resource_budget_validation.py     层/窗口/种子验证与只读审计
  prototype_tests/                            新算法及实验入口回归
  results/resource-budget-iterations-20261115/ 机制数据、图和负结果
  results/resource-heldout-20261115/           27条件数据与图
```

命令在仓库根目录执行。新检出不需要先运行仿真即可阅读所有已提交汇总。旧验证的`--analyze-only`仍引用本机完整产物路径；只检出CSV/图不等于拥有全部原始轨迹。专家选择原数据不随公开分支发布，完整包轨迹与可执行文件也不放Git。

```bash
PYTHONPATH=astra-sim-alibabacloud/experiments/alltoall/src \
python3 -m pytest -q astra-sim-alibabacloud/experiments/alltoall/prototype_tests
```

```bash
# 仅生成18个pilot条件的输入，不启动后端；输出目录必须不存在
python3 research/resource_a2av/experiment.py --phase pilot --prepare-only \
  --out astra-sim-alibabacloud/experiments/alltoall/results/heterogeneous-inputs

# 真实回放，按本机安装设置--binary；失败只登记，不自动重试
python3 research/resource_a2av/experiment.py --phase pilot --workers 3 \
  --binary /path/to/SimAI_simulator \
  --out astra-sim-alibabacloud/experiments/alltoall/results/heterogeneous-pilot
```

运行实际回放需要与结果指纹一致的SimAI/ns-3后端。仓库的ns-3子模块固定在`3e0c7c1bfbbe9f77890ddcf5e5b9c79fc6dd7437`，另加本目录导出的[本地运行时补丁](runtime/ns3-local.patch)。在干净子模块中先`git apply --check ../../research/resource_a2av/runtime/ns3-local.patch`，核对后才应用；已有修改的子模块不要盲目重复应用。补丁是源码快照，不是“已在新克隆中重新构建通过”的声明。

前端运行时源码随本仓库保存于`astra-sim-alibabacloud/astra-sim/network_frontend/ns3/NativeFastRuntime.h`、`NativeFastTransport.h`和`AstraSimNetwork.cc`。初始化其他子模块与编译步骤参考原项目README。
