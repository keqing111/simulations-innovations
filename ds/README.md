# 实验工作区说明

本目录保存审计、复现和实验脚本，与生产实现分开；不修改生产文件，也不覆盖历史归档。

当前范围：只研究仓库内独立的部分分解模拟器。不包含与原系统合并后的端到端实验，也不接入真实流量记录。用作对比的原有完整分解方法只调用原实现的补齐和分解，不调用流量均衡与任务分发，因此它是分解层基线，不是完整原系统基线。

版本库同步只包含脚本、清单、生成用例和汇总结果；原始运行输出与独立审计文件体积较大，保留在本地，不纳入 Git。

内容包括：

- `EXPERIMENT_PLAN.md`：冻结的实验范围、正确性门槛、第一轮场景、指标和流程。
- `RESULTS_FIRST_ROUND.md`：第一轮采集的数据、分组表、负例和遗留问题。
- `experiments/generate_cases.py`：生成五种拓扑、三种正式流量和八个人工小例。
- `experiments/run.py`：运行五种算法组合，保存原始输出、日志和校验值。
- `experiments/audit.py`：独立重放守恒、阶段结构、资源总量和阶段公式。
- `experiments/summarize.py`：配对指标、分组几何平均和胜负平统计。
- `repro_global_diag.cpp`、`repro_all2all_diag.cpp`：早期的自环诊断程序，保留作参考。
- `experiments/raw/smoke/`：19 个场景乘 5 个预设的正确性与冒烟数据，包含八个人工小例和代表性正式场景。
- `experiments/raw/full/`：第一轮 58 个场景乘 5 个预设的数据，每个运行重复三次。
- `experiments/audit/`：独立审计输出。
- `experiments/summary/`：配对结果表、分组结果表和汇总文件。

构建与运行：

```bash
cd /home/julius/fast/FAST
make -C simulation bvn-build
python3 ds/experiments/generate_cases.py --smoke \
  --out ds/experiments/smoke_manifest.json
python3 ds/experiments/run.py --manifest ds/experiments/smoke_manifest.json \
  --raw ds/experiments/raw/smoke
python3 ds/experiments/audit.py --raw ds/experiments/raw/smoke \
  --out ds/experiments/audit/smoke_audit.json

python3 ds/experiments/generate_cases.py --out ds/experiments/manifest.json
python3 ds/experiments/run.py --manifest ds/experiments/manifest.json \
  --raw ds/experiments/raw/full
python3 ds/experiments/audit.py --raw ds/experiments/raw/full \
  --out ds/experiments/audit/full_audit.json
python3 ds/experiments/summarize.py --raw ds/experiments/raw/full \
  --out ds/experiments/summary/full
```

说明：原有完整分解方法接收 1024 字节单位；部分分解预设要求 1 字节单位。运行器按方法自动区分这两种单位，但两种方式收到的真实需求字节相同。人工小例对两者都使用 1 字节单位。

热点数据中心成组流量采用测试矩阵文档的两组完整置换写法：每个源发送一条热点流和一条冷点流，源数据中心内部通过槽位置换映射到目标槽位。它是刻意稀疏的两阶段负对照，不能在不说明的情况下换成稠密块填充并仍使用同一个名称。

早期诊断程序的构建方式记录在此前版本中；生产目标文件位于 `simulation/build/`。
