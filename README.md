# Resource-Order — code-only snapshot

本分支保存当前 Resource-Order 算法及必要接口代码，属于原 FAST 研究项目的独立代码快照。
它是一个无父提交的独立分支，**不包含也不继承旧分支的论文、实验数据、结果图表或构建产物**。
旧 FAST 分支及原工作目录保持不变。

## 主要内容

- `resource_order_a2av/resource_order/cover.py`：完整匹配覆盖、交替环交换、资源/依赖联合目标的第一阶段优化。
- `resource_order_a2av/resource_order/refinement.py`：第二阶段细化与确定性停止条件。
- `resource_order_a2av/resource_order/model.py`、`topology.py`：完整消息、固定有向资源路径、多级树与 Leaf–Spine。
- `resource_order_a2av/resource_order/dependency_*.cpp`：与Python依赖递推对应的可选C++加速内核。
- `resource_order_a2av/resource_order/simai.py`：现有定制SimAI/ns-3的单槽事件执行适配器。
- `resource_order_a2av/integration/ns3/`：原单槽执行器、隔离的多槽FIFO接口及事件合同测试。
- `resource_order_a2av/tests/`：不依赖历史实验数据的算法单元测试。

这不是 FAST/BvN 分解版本：算法不使用统一字节配额，不将消息主动分片。
离线逻辑组导出为端点顺序；执行层没有全局轮次barrier。
当前规划目标仍按单发送/单接收槽依赖建模。多槽执行器支持并发敏感性验证，
但不能将其描述成已经针对多并发重新设计或验证完毕的调度器。

## 本地构建与测试

需要Python 3.10+、GNU Make和支持C++17的编译器。算法和选定单元测试仅依赖Python标准库。

```bash
cd resource_order_a2av
make test
python3 tools/plan.py --output build/example-plan.json
```

`make test`构建可选加速库与事件合同测试，并运行选定的Python测试。
它不运行网络性能实验，不需要下载需求数据，也不需要GPU。
`tools/plan.py`只生成计划；示例中的合成输入是功能演示，不是已发表实验结果。

## 网络仿真依赖

完整SimAI/ns-3源码及其构建树不包含在本次上传中。
现有`simai.py`要求带外部流表、固定路由、credit/data/ACK事件及监控接口的定制后端，
不能直接将未修改的上游SimAI二进制视为可替代实现。
历史CLI中保留的本机默认二进制路径必须用`--binary`覆盖。
多槽接口的具体语义、部署前提见 [接口说明](resource_order_a2av/integration/ns3/README.md)。

主算法文件与本地源文件逐字节一致；本次整理没有修改算法、运行新性能实验或上传测量结果。
`search.py`等辅助模块因历史CLI的导入依赖保留；当前主线是`cover.py`与`refinement.py`，
不是基于多次ns-3回放筛选候选的旧搜索器。并发回放接口仅将本机硬编码构建清单路径改为显式参数。
保留原FAST项目的LICENSE，不改变外部依赖各自的授权条款。
