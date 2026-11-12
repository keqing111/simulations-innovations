# 完整 FAST 原生 scaling 配对控制

2026-11-11。写作已暂存，本轮开始补齐论文证据。只扩展现有研究 CLI 的 `--scaling 0|1`（默认 0），透传原生 GlobalScheduler 开关；未修改 scaling 判据、补齐、匹配、Local 或流水线。

## 范围与结果

N=8/16、G=4、均匀/合成 receiver-Zipf（指数0.9、seed101）、S0原生/S1最大瓶颈、scaling关/开，共16份输出，每份规划3次。输入复用 `study.fixed_load_matrix`，每GPU跨server需求39060KiB，同server需求为0；沿用3DC、端口/网关400Gbps、有向WAN100Gbps、WAN参数单向50us（完整路径传播52.8us）。

以下每行 scaling 开/关结果相同。时间为完整 pipeline2 的预计通信时间，**不含规划时间**。

| N / 需求 | S0 阶段数 / ms | S1 阶段数 / ms | 开启 scaling 后 S0/S1 的频率缩放因子 |
|---|---:|---:|---:|
| 8 / 均匀 | 7 / 22.321209 | 7 / 22.321209 | 1 / 1 |
| 8 / Zipf | 48 / 35.350300 | 40 / 28.876436 | 1 / 1 |
| 16 / 均匀 | 15 / 46.020672 | 15 / 44.314114 | 2605 / 2605 |
| 16 / Zipf | 223 / 71.163455 | 91 / 65.004511 | 1 / 1 |

因此，本面板中原有 S1 收益不能由“仅关闭原生 scaling”解释；但不能推断 scaling 对其他需求大小、规模和倾斜无效。16均匀的单位确实发生缩放，但匹配、阶段数和通信时间保持不变；不能将所有开关实验误称为 scaling 完全未生效。

## 正确性与计时边界

- 新增3个 CLI 回归测试，先出现 `unknown option` 失败后通过：显式关闭与默认一致、开启时交付守恒及流水线重算、非法参数拒绝。
- 16份输出均核对真实服务器对字节与原始GPU需求总和一致，并从balance/intrinsic/dispatch/network重新计算完整pipeline时间。
- 输出包含 `scaling_enabled`；因子从原始/缩放系数比得到，不将辅助容量当真实字节。
- `decomposition_cpu_ms` 不含 scaling；`planning_wall_ms` 含 scaling、补齐、分解及机内计划，仍是墙钟而非进程CPU。两者分别保存，3次计时不用于微小差异排名。
- 这不是拓扑组合算法的完整流水线验证，也没有完成GPU级spreadout基线。
- 完整回归 `make -C simulation check bvn-check bvn-decompose-build` 通过：集成C++、6个Python测试和独立BvN的5443项检查。后者不是本次新增检查数量。

结果：[scaling-control-20261111](scaling-control-20261111/)，含输入、每次CLI输出、摘要与本轮源码/二进制hash。旧结果不改写；研究CLI重新编译后的hash与历史运行不同是正常版本边界，不能将新二进制冒充旧二进制。

复现（输出目录必须不存在）：

```bash
make -C simulation bvn-decompose-build
python3 -m unittest discover -s simulation/tests -p test_bvn_scaling_cli.py -v
python3 ds/decomposition/scaling_control.py --out /tmp/bvn-scaling-control-new
```

后续优先顺序见 [证据补齐计划](EVIDENCE_COMPLETION_PLAN.md)：GPU级轮转强基线 → 资源信息指导匹配生成 → 冻结算法后的独立验证与规划成本。暂不扩大正式网格。
