# 论文写作配置（写作方式已确认）

日期：2026-11-11。使用 `academic-research-suite` 3.24.2 的 academic-paper / outline-only 流程，从配置阶段开始。本文件不是最终文章大纲，也不表示方法、贡献或实验结论已经定稿。

确认记录：用户回复“可以的”，确认上一轮提出的“中文大纲与说明、英文正文和摘要、Markdown 先行、暂不绑定会议模板”。会议、作者、经费、最终算法和性能结论仍未确定；该回复不被扩大解释为这些事项已经批准。

## 1. 已有共识与拟采用的写作配置

| 项目 | 当前值与来源 |
|---|---|
| 论文目标 | 用户目标：发表 EI 会议论文；具体会议尚未指定，不预判接收或索引 |
| 论文类型 | 工程算法与仿真实验的会议研究论文，不是文献综述 |
| 研究对象 | 异构多级固定路径网络中的单次 All-to-All(v) BvN 调度 |
| 工作研究问题 | 如何在有限规划成本下，兼顾分解碎片与共享资源组合，降低完整通信操作的预计完成时间？ |
| 核心动机 | 端点可行、阶段较少，不足以保证中间共享网络资源组合高效；传播成本与序列化并行收益还可能相互竞争 |
| 研究边界 | 纯仿真；静态已知路径/带宽/单向传播；不引入多路径、包级拥塞控制或实机部署；不新增人为全局 barrier |
| 主基线 | 完整原生 FAST；原生 scaling 开/关分列；使用共同拓扑评价器 |
| 补充基线 | 明确定义的 GPU 级 spreadout-like，不宣称测量真实 NCCL 库 |
| 当前证据 | 阶段数/资源组合的解析小例、完整 BvN 策略测试、规模实验、静态映射失败案例、88 份网络层组合评价 |
| 提议的说明语言 | 简体中文，用于大纲、证据表和作者讨论；不改变用户既有交流语言 |
| 提议的论文正文 | 英文；写作语言已确认，正文在大纲确认后起草 |
| 提议的摘要 | 英文工作草稿；附中文释义，不把尚未完成的实验写成已证实结果 |
| 提议的引用风格 | 工程论文数字引用；最终按目标会议要求调整，尚不锁定 IEEE 模板 |
| 输出格式 | 先 Markdown，确认会议后再建立 LaTeX 工程 |
| 字数/页数 | 未指定；不替目标会议虚构限制 |
| 作者、单位、经费与利益冲突 | 待作者提供；不推定无经费、无利益冲突或单作者 |
| 文献筛选证据标准 | 尚未作者选择；本轮不启动新筛选，不声称已有语料经过该技能筛选 |
| 引用与撤稿终结政策 | 默认仅标注待核验项，不代表所有既有参考文献已核验或未撤稿 |
| 会议适配状态 | `criteria_binding_unavailable`：未指定会议/track，不能宣称已对齐具体会议审稿要求 |
| 当前技能阶段 | 写作方式已确认，进入 Phase 2 大纲；不运行完整写作/审稿 pipeline |

这里“工作研究问题”整理自已有讨论，不替代最终贡献声明。题目暂不使用 optimal、near-optimal、throughput-optimal 等带保证的术语。

## 2. 什么现在能写，什么不能提前写死

### 可以先写

- 问题背景、A2A(v) 与端点匹配约束。
- FAST 已有的机内/机间层次，以及本工作关注的中间共享资源边界。
- 两种损失的动机、可手算小例、研究问题和评价目标。
- 论文结构、每节所需证据、实验图表占位。
- 明确标为工作草稿的摘要和 Introduction；摘要结果句先保留待完成标记，不虚构平均提升。

### 尚不能写成已经实现的结论

- “整体算法只剩参数微调”：异 q 阶段的拓扑介入、完整流水线与在线开销仍可能需要结构调整。
- “均衡和 Zipf 都稳定优于强基线”：已有失败案例，且本轮单层网络 Zipf 中 spreadout-like 更快。
- “保持 FAST 在线优势”：当前 Python 拓扑调整开销大于若干案例节省的通信时间，尚无最终 C++ 证据。
- “阶段数越少整体一定越快”或“WAN 越连续越快”：均有解析/实验反例。
- “完整 FAST 接近两倍加速”：手工网络阶段控制不能替代完整 FAST 端到端结果。
- “首次拓扑感知 BvN”或“首次联合匹配与系数”：相关工作边界尚需对比，不能先写首创声明。

现阶段结论是：**有足够材料开始写作与组织动机，不等于已经有足够证据完成投稿。** 写作和算法/实验可并行，但摘要、贡献列表和结论必须跟随最终证据修订。

## 3. 已有材料入口

- [研究主线、基线与组合结果](../ds/decomposition/RESEARCH_STORY_AND_BASELINES.md)
- [阶段数与共享资源小例](../ds/decomposition/DYNAMIC_SMALL_REPORT.md)
- [均衡、Zipf 与规模实验](../ds/decomposition/SCALE_REPORT.md)
- [静态映射实验](../ds/decomposition/STATIC_REPORT.md)
- [静态映射失败的因果小例](../ds/decomposition/STATIC_SMALL_REPORT.md)
- [已有文献笔记](../research/topology_aware/READING_NOTES.md)

这些是已有项目资料，不冒充 ARS 正式 deep-research handoff，也没有人为补造预注册、独立审稿或引用完整性证明。

## 4. 写作工具与依赖建议

本轮只做只读检测：PATH 中未找到 `latexmk`、`pdflatex`、`xelatex`、`lualatex`、`bibtex`、`biber`、`tectonic` 或 `kpsewhich`。这不等于证明磁盘上没有任何 TeX 安装，但当前会话没有可直接使用的本地编译工具。

**当前不必安装 LaTeX，也不必安装额外 Python 求解器或文献 API 客户端。** Markdown 足够完成大纲、论证和初稿。后续两条路线均可：

1. 用目标会议的官方 Overleaf/LaTeX 模板，减少本地环境维护。
2. 本地安装 TeX Live、`latexmk` 和模板要求的文献工具；普通英文工程稿可从 pdfLaTeX + BibTeX 起步，具体仍以模板为准。

若目标会议确实使用 IEEE conference 模板，才采用 `IEEEtran`。正文通常需要的候选包是 `amsmath`、`amssymb`、`graphicx`、`booktabs`；引用与伪代码包跟随模板，只选一套，不预先堆叠 `algorithm2e` 与 `algorithmicx`。中文讨论稿不必为了编译中文而改变英文投稿工程。

EI 是检索目标，不是统一排版模板。目标会议、页数、匿名要求和模板未定前，不安装完整 TeX 套装，不修改系统配置，也不声称已经生成或验证了 PDF。

已检查的工具来源：

- [IEEE Author Center：工具与官方模板](https://conferences.ieeeauthorcenter.ieee.org/write-your-paper/authoring-tools-and-templates/)
- [CTAN：IEEEtran 文档类](https://ctan.org/pkg/ieeetran)
- [CTAN：latexmk 自动编译工具](https://ctan.org/pkg/latexmk)

## 5. 技能安装与暂停点

- 安装来源：[academic-research-skills-codex](https://github.com/Imbad0202/academic-research-skills-codex)。
- 安装位置：`/home/julius/.codex/skills/academic-research-suite`；版本 3.24.2。
- 仅安装技能文件，未启用额外 hooks、未改 Codex 模型设置、未向外部模型上传项目内容。
- 本轮已读取 router、academic-paper WORKFLOW 与 intake 说明；不宣称已运行其完整多阶段写作或独立评审。
- 暂停依据：academic-paper 的 Checkpoint Rules 与 intake 输出协议要求作者确认 Paper Configuration Record 后再继续。

该写作方式已获确认。下一检查点是文章大纲，而不是重复确认上述配置。作者/经费等与初始大纲无关的信息保留待补，不编造。
