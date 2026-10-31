# 已审计数据发布包

本目录发布经记录校验的研究数据，不把“性能退化”当作无效实验删除。
首次同步包含17份已完成研究/诊断归档，压缩数据合计约516.5MiB；没有将约9GB原始树直接塞入Git。
每份包的用途、原目录、文件清单、内容 SHA256、分块 SHA256 和限制列在
[DATASETS.json](DATASETS.json)；[SHA256SUMS](SHA256SUMS)用于传输后核对。
源码/脚本和报告位于常规目录，选定汇总表与图可直接浏览。

## 恢复

从仓库根目录执行：

```bash
sha256sum -c verified_data/SHA256SUMS

# 恢复最新90条件/540结果，内部自带审计所需冻结引擎和脚本。
cat verified_data/boundary-run-0d5b56247cfc.tar.gz.part-* | tar -xz -C .

# 当前源码烟测使用的最终引擎依赖。
cat verified_data/planning-final-96b18da3da36.tar.gz.part-* | tar -xz -C .

make -C simulation/boundary check
python3 simulation/boundary/results/run-0d5b56247cfc/harness/study.py \
  --phase audit --replay --output simulation/boundary/results/run-0d5b56247cfc
```

每块最多40MiB，按三位编号连接；单块的包也使用`.part-000`后缀。
tar成员均从`simulation/`开始，不含绝对路径、Git认证或个人凭据。
已有自行修改的数据时，应先备份或在另一个空目录解压，不要覆盖自己的新结果。

历史完整回归还需要相应的旧数据依赖；可按清单逐包恢复，或在确认目标目录没有自行修改文件后：

```bash
for first in verified_data/*.tar.gz.part-000; do
  prefix="${first%.part-000}"
  cat "${prefix}".part-* | tar -xz -C .
done
```

包内保留必要的小型**冻结审计运行器**及源码快照，以维持旧登记的二进制摘要。
它们不是新平台构建结果，不保证跨平台兼容；普通`build/`、`.o`、缓存和性能剖面运行器不发布。

## 数据边界

- boundary：最新固定算法的WAN边界扫描；只有3个rank需求矩阵，结果不是实网或CPU性能。
- planning：最终实现/隔离CPU与旧V1分别保留；最终桥接重放时间不是性能样本。
- ordering/scenario/refine/scan/bottleneck/topology：历史已完成、已审计批次；负例和已披露模型限制保留。
- 原论文复现数据仅说明固定原生代码的结果，不表示Fig17a已完全对齐。论文PDF及包含PDF的旧适配器源码归档不发布；该历史包是明确标注的data-only例外，完整原始源归档仍只保留在本机。
- 机内timing-audit仅诊断：pipeline1未初始化变量的输出不可当有效基线，保留警告和上下文，不混入主性能结论。
- 下载论文、未启动/作废批次、临时重建目录和ad-hoc benchmark不作为可信数据包发布。必要的作废说明文件仍可在报告链接处阅读。

以上是本地来源/一致性审计，不是抵抗同时重写所有manifest和数据的认证机制；
源码、报告与数据仍受根目录LICENSE约束。原始本机结果未被删除或重写。
