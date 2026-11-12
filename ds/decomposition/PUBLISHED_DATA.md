# 当前发布数据、历史记录和复现边界

2026-11-13。当前主线入口：[算法与完整78条件附录](ALGORITHM_AND_CODE_GUIDE.md)、[最新审查重测](CORRECTED_RETEST_REPORT.md)。

## 本次发布包含什么

1. 当前C++源码、所有本层研究Python脚本及测试；原始NVIDIA/AMD源文件保持原有内容。
2. 本层开发报告/数学说明、`paper/`写作交接材料。除最新报告外，旧性能表均为历史开发证据，不在本轮重新认证。
3. [corrected-byte-host-20261113](corrected-byte-host-20261113/metadata.json)完整数据：26个输入、78条件、390个完整输出、候选计划、汇总CSV/JSON、验证日志；直接在GitHub浏览，不要求解压旧的500MB数据包。
4. [本轮冻结审计依赖](../../verified_data/bvn-current-audit-dependencies-20261113.json)：2个经本地核验的Linux CPU运行器，以及可选历史26例回归所需的study/raw/matrices。它们仅维持冻结数据的源码/二进制指纹审计与回归，不是最新主面板的新性能样本。
5. `causal-certified-20261112/balanced-n6-top2-baseline.json`作为两项旧解析反例回归的只读fixture。其“certified”目录名是历史名字，不构成外部真实性认证；不要把它计入新78条件。

两个token单位初测目录`corrected-host-20261113`和`corrected-host-reviewed-20261113`仍保留本地，未作为最新数据上传；其取整差异已在审查报告中披露。其他旧动态/静态/规模原始扫描也保留本地，不与本轮数据混合。仓库既有历史release和verified_data包保持不变，新的README/写作说明覆盖其“最新”措辞。

## 复核已经发布的数据

在新克隆或确认无自改结果的工作目录中执行；解压会恢复同名文件，不要覆盖自己的重新构建结果：

```bash
echo 'f45d9cedb886e9d272b1b73ad891a4c91c10e93119511a17c2ff28becf40fdc3  verified_data/bvn-current-audit-dependencies-20261113.tar.gz' | sha256sum -c -
tar -xzf verified_data/bvn-current-audit-dependencies-20261113.tar.gz -C .
python3 ds/decomposition/corrected_analysis.py \
  --input ds/decomposition/corrected-byte-host-20261113 \
  --report /tmp/fast-corrected-report.md
```

该核验重新生成输入并比较，核对上游0712d29源码、当前源码指纹、冻结运行器、真实GPU交付、辅助容量重构、每stage真实载荷与预测、资源字节和完整计时。不是对真实网络或CUDA语义的认证。

冻结运行器只依赖普通Linux C++运行库，不执行GPU代码；不保证跨体系结构或不同发行版二进制兼容。`metadata.json`记录仓库相对源码路径和原始二进制指纹，不将旧运行器当作新平台构建结果。SHA256只是来源/一致性检查，不抵抗同时恶意改写代码与manifest。

## 重新构建和重跑

需要Python3、g++；原始NVIDIA源码包含CUDA头文件，因此CPU参考构建仍需要CUDA include目录，默认`/usr/local/cuda/include`，可通过`CUDA_INCLUDE`覆盖；不需要GPU执行或链接GPU transport。算法研究运行本身不要求ns-3/SimAI。

```bash
make -C simulation runtime-host-build bvn-decompose-build
make -C simulation check bvn-check
python3 ds/decomposition/corrected_study.py --out /tmp/fast-new-byte-run
python3 ds/decomposition/corrected_analysis.py \
  --input /tmp/fast-new-byte-run --report /tmp/fast-new-byte-report.md
```

输出目录必须不存在，防止覆盖旧结果。新构建二进制可能与旧冻结指纹不同：对新运行使用新manifest，不能修改旧manifest来假装恢复了同一二进制。CPU样本会受机器/调度变化，不期待逐次相等；确定性的矩阵和通信计划才是重构比较对象。

当前原始NVIDIA固定数组最大10server、8GPU/server，适配器进一步保守限制矩阵总字节<=UINT_MAX；不是通用规模上限或FAST理论上限。更大负载/规模必须明确扩展实现及另测，不应绕过检查或把字节重新粗粒度量化来规避限制。

## 新旧报告的解释顺序

以`ALGORITHM_AND_CODE_GUIDE.md`、`CORRECTED_RETEST_REPORT.md`和`paper/READINESS_20261113.md`为当前结论。`JOINT_RESOURCE_REPORT.md`已经加入审查更正。其他旧报告的“完整FAST”“MoE”“全胜”“最新”等字样必须结合[负载与基线审查](WORKLOAD_AND_BASELINE_AUDIT_20261113.md)阅读，不能被引用为本轮已证实结论。
