# WAN边界扫描验证

2026-10-31。冻结运行`results/run-0d5b56247cfc`。本轮仅新增Python实验/分析，不改C++、调度算法或解析网络计时。

## 测试与冻结

- runner/API缺失RED→真实48结果烟测GREEN；检查CLI实际WAN100/400、两服务器100字节WAN串行化0.008/0.002µs、独立带宽/传播重建、篡改raw/二进制/派生表拒绝。
- analysis缺失RED→GREEN：手算原生max流水线24/31.88/31.82µs、零WAN不收传播、SpreadOut本地round、签名忽略代价但不忽略payload、多根与采样平局/非有限函数拒绝。
- 二次render会把旧图provenance当输入表来源的问题经RED复现，过滤figures/输入后GREEN；未改任何算法或网格。
- 新7Python测试与旧41Python测试全通过，总48；相关C++检查通过。

```bash
make -C simulation/boundary check
make -C simulation/planning check
python3 -m unittest discover -s simulation/planning/validation -p 'test_*.py' -v
make -C simulation/ordering check
make -C simulation/scenario check
make -C simulation/refine check
make -C simulation/scan check
make -C simulation/bottleneck check
make -C simulation/topology check
python3 -m unittest discover -s simulation/reproduction/tests -v
python3 -m unittest discover -s simulation/reproduction/timing_audit -p 'test_*.py' -v
```

本轮日志`/tmp/fast-boundary-*.log`仅临时诊断；测试源、归档manifest/raw和结果表才是永久依据。

## 原生结果、机理与来源

1. 在首个正式调度结果前冻结90条件、6策略和3矩阵；540结果完整采集、独立重建及另一次全部native重放通过，48旧锚点一致。
2. 540冻结重定价/重规划对、36组冻结锚点比较。F000/Max-Min/SpreadOut在所有网络参数下签名不变、冻结重定价与实际CCT一致；所有锚点的原生max递推一致。
3. 已知6DC/Zipf760203/100Gbps的冻结交点2729.429957µs与上一轮相符。实际重新规划只确认粗网格符号变化区间；不承诺唯一根、区间单调性或任意负载阈值。
4. 注册引擎SHA、源码包和逐文件快照检查，复制冻结二进制而非重编译；记录每次命令、矩阵生成器与总字节；各策略真实路由总字节相同。
5. 派生表注册内容SHA并绑定完整raw记录指纹；绘图先核对source/grid、raw集合与每个raw摘要、派生表和raw指纹，防止仅凭二进制元数据标签出图。二次render和损坏表拒绝已测试。
6. 额外在临时目录创建48结果烟测、移走原目录，再用归档自身harness执行audit --replay、analyze、render，全部通过。见运行中的portable_checks.json；只证明兼容本机运行时的路径移动，不保证跨平台二进制。
7. 图像人工查看：热图完整呈现90条件及两种强基线，颜色以1为中心分段线性、无对数并声明截断范围，数值不截断；折线仅连接采样点，实线/虚线定义分别说明。
8. 当前33份C++/头文件与冻结编译源快照SHA一致；登记源/矩阵/raw/派生/图校验通过，已知根及无CPU性能字段检查见handoff_checks.json。

## 没有证明的内容

- 本轮48测试包含旧测试，不是48个独立工作负载；90条件也只有3个rank矩阵。
- 一份新Zipf不是整个偏斜负载族的保证；未扩展N48、重编号、新DC内部拓扑或真实数据集。
- CPU时间未采样成性能实验，不用于含规划或deadline结论；并行回归任务只影响执行耗时，不影响确定性模型CCT。
- 瓶颈/利用率为解析资源工作代理，不是包级队列/拥塞控制或实机测量。
- SPM非外部算法完整复现，不保证普遍优势、论文新颖性或EI接收。

最终独立审查见[REVIEW.md](REVIEW.md)：无Critical/Important/Minor；重新运行新7测试，逐条核对540重建和540冻结计价、48旧锚点，另9次native抽跑。未把审查抽检描述为独立重跑整轮native/旧回归/搬移活动。
