# 代码与实验数据更新（2026-11-16）

本次仅发布实验代码、数据、结果图与核验记录，不包含论文LaTeX、全文PDF、Overleaf包、写作草稿或引用核查材料。实验目录与脚本名称中的paper指实验批次，不表示论文正文。

## 最新数据

- [52条件的五方法配对结果](../../astra-sim-alibabacloud/experiments/alltoall/results/paper-supplement-20261115/paired.csv)：24个trace-derived MoE条件、22个合成/规模条件、6个机制条件；共260次回放。
- [逐方法CPU、PFC和rank错位指标](../../astra-sim-alibabacloud/experiments/alltoall/results/paper-supplement-20261115/method_metrics.csv)。
- [执行/精确交付核验](../../astra-sim-alibabacloud/experiments/alltoall/results/paper-supplement-20261115/supplement_verification.json)、[完整验证记录](../../astra-sim-alibabacloud/experiments/alltoall/results/paper-supplement-20261115/verification.json)、[同R/q*首次分叉](../../astra-sim-alibabacloud/experiments/alltoall/results/paper-supplement-20261115/first_differences.json)。
- [机制证据](../../astra-sim-alibabacloud/experiments/alltoall/results/paper-supplement-20261115/mechanism_evidence.json)、[紧凑证据归档](../../astra-sim-alibabacloud/experiments/alltoall/results/paper-supplement-20261115/paired-evidence.tar.gz)。
- [完整结果报告和图](../../astra-sim-alibabacloud/experiments/alltoall/results/paper-supplement-20261115/REPORT.md)、[冻结实验参数和入口](paper_supplement.py)。保留所有负结果；不上传原始专家选择记录、运行程序或全部包轨迹。

通信计时是完整算子返回（必要ACK及本地分发完成）；CPU规划、物化和校验独立计量，不混入通信时间。本次重新检查52×5条状态与配对资格均有效，没有重跑仿真。

## 相对上次远端版本的代码变化

- Resource-MaxQ核心`resource_budget.py`及整数预处理/物化`simai_plan.py`没有新的算法变动。
- 添加[Pairwise XOR对照](../../astra-sim-alibabacloud/experiments/alltoall/src/simai_a2a/schedulers/pairwise.py)，并在[机内计时入口](../../astra-sim-alibabacloud/experiments/alltoall/src/simai_a2a/execution.py)和[共用回放](../../astra-sim-alibabacloud/experiments/alltoall/tools/run_resource_budget_iterations.py)中接入。
- 添加[机制输入](paper_mechanism.py)、[固定路由实验](paper_experiment.py)、[五方法补充实验](paper_supplement.py)及结果汇总脚本、回归测试。
- 实验复用判断要求完整物化方案一致，不能仅凭相同输入或配额序列沿用通信时间；相关来源和证据保留在数据目录。

## 参数更正

BW Leaf的400/200/100/400 Gbps是四个机架分别采用的单链路速率，同一机架到四个Spine的速率相同，不是每个机架的四条Spine链路分别设置四档速率。以`experiment_matrix.json`及`experiment.py::fabric()`的实际配置为准。本次没有改变拓扑配置或结果数据，只更正说明。

## 发布核验

2026-11-16重新运行`prototype_tests`：84通过，退出码0；只对相关原型回归作此声明，不声称全项目所有历史测试通过。冻结数据保留的较早完整测试记录包含已知历史失败，不在本次同步中更改。

发布提交基于既有远端头，选择性纳入代码和数据，不携带本地11个待推送提交中的论文草稿历史。原工作区、论文材料及原本地提交保留不动；本次不进行force push。
