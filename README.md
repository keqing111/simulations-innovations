# simulations-innovations

面向异构/多数据中心网络的 FAST All-to-All 调度仿真研究工作区，基于
[MangoBoost/FAST](https://github.com/MangoBoost/FAST) 扩展。保留上游代码、历史、作者署名和许可；
新增方法与实验不代表原论文作者的结论。

目前主线仅使用 **FAST 原生解析仿真**，不是 ns-3 包级拥塞或 GPU 实机验证。
旁边独立的 SimAI 工作区不包含在本仓库首次同步中。

## 研究入口

- [最新 WAN 优势边界报告](simulation/boundary/REPORT.md)：90 条件、540 结果，含热图、重规划/冻结调度曲线和负例。
- [A1 规划实现优化与静态路径对照](simulation/planning/REPORT.md)：调度保持不变的实现优化，通信收益与 CPU 开销分别报告。
- [论文证据大纲](simulation/planning/PAPER_OUTLINE.md)。
- [文献与历轮研究索引](research/topology_aware/README.md)。
- [FAST 原论文复现及限制](simulation/reproduction/REPORT.md)：包括尚未完全对齐的 Fig17a，不声称所有论文曲线已复现。

A1 是本文工作区的共享资源感知扩展；Max-Min、SpreadOut、X10、SPM 的定义与比较边界见报告。
SpreadOut 不等于完整 NCCL 实现，SPM 不冒称外部论文算法的完整复现。

## 代码、可信数据与复现

源码、脚本、报告、图表和选定的已审计汇总表直接纳入 Git。
完整原始结果、矩阵、源码快照及审计依赖压缩分包放在
[verified_data/](verified_data/README.md)，校验、恢复命令和用途限制见该目录。
普通构建缓存、下载论文、未启动/作废实验不上传；不会删除本地原始文件。
旧论文复现数据包有明确标注的PDF依赖例外，不能把它当成无外部依赖的完整原论文复现包。

先恢复所需数据包，再从仓库根目录运行，例如：

```bash
cat verified_data/boundary-run-0d5b56247cfc.tar.gz.part-* | tar -xz -C .
python3 simulation/boundary/results/run-0d5b56247cfc/harness/study.py \
  --phase audit --replay --output simulation/boundary/results/run-0d5b56247cfc
```

运行当前源代码测试还需要恢复 `planning-final-96b18da3da36` 依赖包；完整历史回归的依赖范围见数据说明。
测试工具为 Python3、g++ 和 matplotlib；冻结 Linux 可执行文件仅作为原审计的版本依赖保存，
不保证任意平台二进制兼容。历史报告中“未做 Git 上传”描述的是当时实验状态，不是本仓库发布状态。

## 上游 FAST

This repository contains the reference code for the paper:
**"FAST: An Efficient Scheduler for All-to-All GPU Communication"**.

Authors:
Yiran Lei, Dongjoo Lee, Liangyu Zhao, Daniar Kurniawan, Chanmyeong Kim, Heetaek Jeong, Changsu Kim, Hyeonseong Choi, Liangcheng Yu, Arvind Krishnamurthy, Justine Sherry, Eriko Nurvitadhi.

## Repository Layout

- `simulation/`: Algorithm-level simulator and benchmarking code for FAST scheduling ideas (matrix decomposition, scheduling policy studies, and plotting scripts).
- `nvidia/`: NVIDIA/NVSHMEM runtime implementation, including CUDA kernels, runtime scheduling, Python bindings, and multi-node test scripts.
- `amd/`: AMD/RCCL-oriented implementation and test harnesses, including corresponding build scripts and legacy experiment artifacts.

## License (Academic & Non-Commercial)

This repository is intended and licensed for academic and non-commercial research use only.
You are free to use, modify, and distribute this software for non-commercial, academic, and research purposes only.

Commercial use, including integration into commercial products or services, requires prior written permission from the authors.
For commercial licensing inquiries, please contact us at contact@mangoboost.io.

## Third-Party Code and Notices

This repository includes some source files derived from external open-source
projects.

- `nvidia/src/include/registration.h` is derived from the vLLM project
  (`vllm-project/vllm`), licensed under Apache License 2.0.

See `THIRD_PARTY_NOTICES.md` for details.
