# 两条独立 FAST 优化策略

先看 [REPORT.md](REPORT.md)：共享带宽策略与单路径完成时间策略分别实现，暂不联合。

```bash
make -C simulation/independent check
python3 simulation/independent/study.py --phase run
```

构建只读取 `simulation/planning/results/final-96b18da3da36/source` 中摘要匹配的冻结引擎，不编译当前其他目录里正在批注的源码。缺少该归档时按仓库 `verified_data/README.md` 恢复最终planning包。

直接调用（从 FAST 根目录）：

```text
simulation/independent/build/native_independent N G METHOD WAN_US SERVER_BW GATEWAY_BW WAN_BW DC_CSV REPEATS
```

标准输入为 `N*G` 阶的行优先字节矩阵，输出JSON。METHOD可选：

- `native`：原生匹配。
- `bandwidth`：原生种子+一次共享带宽引导构造，按串行化费用严格接受。
- `time`：原生种子+辅助完成时间矩阵的最小瓶颈匹配。
- `maxmin`：已有精确阶段粒度对照。
- `maxmin-bandwidth` / `maxmin-time`：分别使用相同q*种子，不是带宽与时间的联合策略。
- `spreadout`：循环配对参照，不是完整NCCL。
- `batch`：一次暖机后轮转运行全部七种策略，逐次从原始需求重建调度器。

WAN_US为单向传播微秒，带宽为Gbps；源文件 [policy.cpp](policy.cpp) 实现匹配策略，[main.cc](main.cc) 接入原生钩子，[study.py](study.py) 登记输入并独立重建网络计价/资源总量/pipeline组合。

运行目录自带源码与冻结依赖，可使用其中 `source/independent/study.py --phase audit --output <运行目录>` 重放。审计阶段的 `--no-replay` 仅关闭重新执行原生二进制，仍检查数据域、登记摘要与模型组合；它不独立重建原生机内balance/dispatch的内部算法。默认启用完整重放。

这里是解析网络实验，不生成包级队列/PFC/DCQCN结果。三次规划计时只作诊断；输出中 `planning_p95_us` 是三样本nearest-rank p95（即最大值），不应作生产尾延迟证据。
