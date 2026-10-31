# 验证与独立审查记录

2026-10-29；代码冻结后只改报告/计划/本记录，无算法调参或模型改动。

## 测试与数据审计

- `make -C simulation/scenario check`：C++6服务器确定性例子、32次稀疏矩阵分解；3个Python测试通过，含21个smoke结果及篡改拒绝。
- `make -C simulation/refine check`：C++系数/阶段/真实流量测试、2个Python测试通过。
- `make -C simulation/scan check`：入口与规划C++检查、5个Python测试通过。
- `make -C simulation/bottleneck check`：模型与缓存C++检查、2个Python测试通过。
- `make -C simulation/topology check`：模型与匹配C++检查、8个Python测试通过。
- `python3 -m unittest discover -s simulation/reproduction/tests -v`：7个测试通过，包括原始工厂手算及prechange oracle。
- 共27个Python测试通过；历史五种共享策略的阶段、真实字节和完成时间与上一轮冻结二进制一致；Zipf生成向量一致。
- 两组预登记后顺序运行，无其他编译/测试干扰正式计时。探索742/742结果、3710计时样本；确认336/336结果、7056样本；分别5/7个不同需求矩阵，不把复用配置当独立随机样本。
- 两组均执行完整回放和独立路径容量/字节/pipeline重建两次（run及render）。1078结果通过；真实网络字节总量方法间一致。这里只验证算法次数预算与总字节，不声称Zipf逐rank封顶预算通过。
- 从探索归档的`source/scenario/study.py`独立重编译，源码identity一致，3配置×7方法共21结果语义一致，见归档`archive_rebuild_checks.json`。
- topology/model.h/.cpp、scheduler/global.h/.cpp、scheduler/local.h/.cpp、algorithm/all2all.h/.cpp与上一轮归档逐字节一致。新方法没有修改执行公式或增加barrier。
- 图已实际打开检查；报告链接全部存在。确认热图中空列是未登记配置，不是0倍收益。

## 唯一一次独立只读审查

审查者：`review_scenario_constructive`，干净上下文，未修改任何文件、未派生其他审查者。与上一轮归档比较真实改动，不将旧dirty changes纳入本轮成果。

发现/裁决：

1. **Important，协议/报告**：设计写每GPU≤64MiB，但Zipf只控制全局rQ。独立计算确认，520201最大rank发送86,484,256字节。接受发现，保留全部冻结数据，REPORT §3.1披露原预登记偏差与全部三个种子最大值，限定Zipf结论为平均预算；不把已完成审计称作每rank流量封顶审计。先运行“缺少平均量/上限偏差披露”的断言观察RED，文档修正后再验证GREEN。没有修改执行代码或偷偷重生成输入。
2. **Minor，覆盖率**：已有确定性最终匹配及随机分解，但缺少指定多跳增广/失败分支的逐步资源占用单测。未发现当前算法错误；记为后续重构前测试增强，不为此改已冻结算法/重跑实验。
3. **Minor，图形**：部分图标签较密，确认热图保留了未登记配置的空列。数值可读且报告解释空列；投稿图可进一步精简，不影响结果。

审查确认：完整DFS只调整邻居顺序、成功增广后提交占用，未发现匹配合法性或资源扣减错误；q*与1/2构造预算符合设计；block行列控制及input_key拓扑依赖正确；可变拓扑独立重建未退回固定3DC。

审查者未独立判断：EI发表准备度、真实网络泛化（超出只读代码审查）；仅凭时间戳证明预登记先后（报告是执行记录）；未重复运行全部重实验/host计时（由主执行者测试与审计证据支持）。这些均不据审查结论宣称已解决。

所有Important发现均通过明确披露解决；无Critical算法问题。未执行Git提交、推送、清理或覆盖旧实验。
