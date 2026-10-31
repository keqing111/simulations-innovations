# 验证记录

2026-10-30，计划见PLAN.md，决策与迭代见PROGRESS.md。未提交Git；所有旧归档保留。

## 测试先行与回归

- 资源缓存API缺失→编译RED；stub42→42分配断言RED；实现后42→1 GREEN。覆盖平局同序、多次递归置换、失败分支、成功调用后的重复复用和30随机内核比较；未专测完整false返回后再次成功，见审查保留项。
- SPM API缺失→编译RED；80个3/4节点最大权目标与测试专用小规模枚举一致，非法支持图拒绝、虚拟/自环不计效用及路径计时核对GREEN。在线算法不是枚举。
- planning脚本缺失→RED；真实编译的24结果烟测、完整字段配对比较、源/策略篡改拒绝GREEN。
- V1隔离发现阈值快路径负优化；Max-Min实现一致性分配控制35/34 RED，撤回快路径后35/35 GREEN；资源缓存仍42→1。
- 版本桥接字段篡改、注册二进制覆盖、绘图拒绝混合CPU版本测试均经缺失API RED→GREEN。

最终执行：

```bash
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

41个Python测试通过：planning3、validation4、ordering5、scenario3、refine2、scan5、bottleneck2、topology8、reproduction7、timing-audit2；对应C++检查通过。最终日志`/tmp/fast-planning-completion-check.log`、`/tmp/fast-planning-validation-check.log`及`/tmp/fast-planning-final-regression-*.log`；临时日志不是永久交付依赖，归档原始数据和测试源才是复现依据。

## 实验与等价证据

1. V1两集在正式结果前冻结：explore8、confirm32；12标签、24轮转，480结果/11520样本。正式完整重放、路由、真实字节、原生pipeline递推、计数与240个ref/opt配对核验通过。
2. V1隔离8配置、两块反序、每块12次，96结果/2304样本；识别并撤回慢的阈值实现。原脚本两份源码在`isolated-600edb9c2a2c/source.tar.gz`，已逐份核验与最初注册源哈希一致。
3. 从V1冻结源码独立重建，3输入×12标签的36项全语义检查通过，见`results/archive-rebuild-600edb9c2a2c/checks.json`。
4. 最终源码`final-96b18da3da36`：逐项重建40×12=480结果，包含全部阶段、payload、资源、策略与计数；与V1完全一致。另对ordering两个归档的52+64=116输入重跑旧二进制与最终A1-opt，独立重建旧q*支持诊断后全语义相同，见`checks.json`。桥接时间不算性能样本。
5. 最终CPU只用该最终二进制、原定8个N48配置，96结果/2304样本。采集前本轮桥接、回归、剖面和绘图任务均已退出；无本轮CPU重任务并发。但未绑核、仍有系统背景活动，不能当生产截止保证。
6. 前后-pg仅诊断；正式图只读取桥接通信结果＋最终隔离CPU。绘图检查最终二进制SHA匹配；图像人工检查无裁切、使用线性纵轴。
7. 最终编译快照与当前源码身份一致；13份已归档的原有tracked代码逐份与V1基线SHA一致，见`protected_sources.json`。本轮未更改原生机内/网络计时。
8. 最终隔离额外核验192个单策略块的注册raw SHA、命令、样本数、与原冻结语义一致及独立重建，见`isolated-final-96b18da3da36/audit.json`。绘图对comparison派生关系的自动绑定仍留待加固，不把该元数据门禁当完整派生审计。

## 边界

- 32确认标签只有24个不同物理配置、6个不同rank需求矩阵；最终CPU8配置只有2个rank矩阵，不作独立随机样本显著性推断。
- 新CPU只覆盖8个N48配置；不拼接V1/V2的CPU，不将桥接重放当计时实验。
- 历史核验依赖列明的ordering归档；SPM是自建适配控制，不是外部合成器完整复现。
- 最终独立审查见REVIEW.md；本文件不代替该审查。
