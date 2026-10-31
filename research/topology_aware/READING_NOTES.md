# 方法阅读笔记：哪些能借给FAST，哪些不能

日期：2026-10-29。书目及PDF见[PAPERS.csv](PAPERS.csv)。以下是针对本研究的选择性阅读，不是对各论文的完整复述。每项明确区分原论文内容与我们的迁移推断。代码链接只核验存在，未安装、运行或复现作者实现；空白表示本轮未核验到，不表示没有公开代码。

## P00 FAST：锚定原有能力

[会议页面](https://www.usenix.org/conference/nsdi26/presentation/lei-yiran)；[现有计时审计](../../simulation/reproduction/TIMING_AUDIT.md)。

原论文与当前项目：FAST已经区分机内scale-up和机间scale-out，并通过流量重整、服务器级分解和机内流水处理非均匀all-to-all。不能笼统写成“完全不考虑拓扑”。本项目沿用原生pipeline2与机内SpreadOut。

研究定位（我们的判断）：需补充的是**服务器端点之间的中间共享网络资源**，而非重新发明两级层次。原论文结果的剩余复现差异仍保留在原报告中，不能因开始优化就视为完全对齐。

## P01 TACCL：用通信草图约束搜索空间

Aashaka Shah等，NSDI 2023。[论文](https://www.usenix.org/system/files/nsdi23-shah.pdf)；[实现](https://github.com/microsoft/taccl)。重点：§3通信草图，尤其逻辑拓扑、switch hyperedge与对称约束。

论文方法：用户提供草图，将可选逻辑连接、交换机共享限制及对称性编码进合成过程。利用测量的通信参数和优化求解器生成调度，而非只依据端点间字节量。

对FAST的借鉴（推断）：静态拓扑可先编译为资源ID、冲突类别和少量合法模板，在线只处理残余需求。注意：删去物理交换机后，不能把所有逻辑边都当成拥有独立带宽。完整求解器不适合原样插入每个动态分解步骤。

## P02 TE-CCL：显式链路容量与时间演化

Xuting Liu等，SIGCOMM 2024。[作者页面](https://www.sivak.dev/projects/13-teccl)；[论文](papers/teccl-sigcomm24.pdf)；[实现](https://github.com/microsoft/TE-CCL)。重点：§3模型、§4.1不复制数据时的LP简化、§4.2时间分区。

论文方法：以流量工程建模链路容量、传播、缓存和数据需求。一般情况为MILP；AllToAll这类不需要数据复制的情况可去掉相关整数变量，化为LP。时间分区进一步折中规模与求解质量。

对FAST的借鉴（推断）：先把“哪条流使用哪些共享资源”描述正确；小规模LP可作为离线质量参照，不必成为在线算法。使用时仍需核验路由自由度、分块、交换机约束及all-to-allv需求是否与我们一致，不能把不同模型的解直接叫作FAST的全局最优。

## P03 SyCCL：利用拓扑与需求的对称性

Jiamin Cao等，SIGCOMM 2025。[论文](https://ennanzhai.github.io/pub/sigcomm25-syccl.pdf)；[实现](https://github.com/aliyun/syccl)。重点：§3–5，同构分组、草图探索和组合。

论文方法：用拓扑/通信的对称结构，生成较小子问题的调度并复制、组合，降低整体合成规模。支持多种collective；其公开实现对AlltoAll使用LP求解子问题。

对FAST的借鉴（推断）：我们的固定多DC结构可离线提取重复子结构，减少在线候选。**拓扑对称不意味着Zipf流量对称**：不能直接复制等量collective调度；也不能只对DC总矩阵求解而不证明其能还原到服务器合法匹配。

## P04 TACOS：时间展开网络与贪心资源占用

William Won等，MICRO 2024。[会议程序](https://microarch.org/micro57/program/index.php)；[作者稿v3](https://arxiv.org/html/2304.05301v3)；[实现](https://github.com/astra-sim/tacos)。重点：时间展开网络及贪心chunk匹配。

论文方法：将网络按时间展开，依据尚未满足的数据需求，在可用的链路—时间位置上贪心安排传输，处理异构链路代价。主要展示AllGather等collective，不等同于已经实现我们的动态all-to-allv。

对FAST的借鉴（推断）：候选应考虑真实数据是否就绪、资源是否被占用，而不是静态边权相加。可借一个短窗口的资源预约思想；WAN时延大、时间粒度细时，完整展开图可能巨大，不建议直接照搬。

## P05 Birkhoff+：稀疏分解与近似精度

Víctor Valls、George Iosifidis、Leandros Tassiulas，IEEE/ACM ToN 29(6)，2021。[正式版本书目](https://doi.org/10.1109/TNET.2021.3088327)；[本地公开预印本](papers/birkhoff-plus-author-preprint.pdf)。重点：稀疏性界、Frank–Wolfe与Birkhoff+。本地是2020年arXiv v1，不冒充最终期刊PDF。

论文方法：研究在少量排列下逼近目标矩阵，结合Birkhoff的可行残余扣除与Frank–Wolfe选择方向的思想，讨论近似误差和配置数量的关系。

对FAST的借鉴（推断）：大块需求优先、尾部单独精确处理值得研究；但本题要求所有真实字节交付，不能以近似误差为理由丢掉尾部。清尾的网络时间、阶段和计算开销都必须计入。也不能直接声称某个稀疏性界对我们的非均匀共享WAN模型成立。

## P06 Eclipse：匹配与持续时间联合选择

Shaileshh Bojja Venkatakrishnan、Mohammad Alizadeh、Pramod Viswanath，SIGMETRICS 2016。[正式论文](https://people.csail.mit.edu/alizadeh/papers/eclipse-sigmetrics16.pdf)。重点：§3、Algorithm 1。

论文方法：在有限窗口与电路重配置开销下，用某个持续时间能有效服务的需求作为匹配权重，再按“有效服务量／持续时间加重配置代价”选择配置。研究有特定假设的吞吐近似保证。

对FAST的借鉴（推断）：联合考虑匹配与阶段系数，而非匹配选完才被动接受极小系数。**电路重配置代价不是WAN RTT**：传播中的链路可能仍能持续发包，只有实际依赖造成的等待才能作为调度开销。不能把原论文保证搬到有限批次all-to-all的完成时间上。

版本提醒：2015年预印本标题为“Costly Circuits, Submodular Schedules: Hybrid Switch Scheduling for Data Centers”；与登记的2016年正式论文属于同一工作，不能算作两篇独立证据。

## P07 Solstice：阈值支持图、较大阶段与尾部

He Liu等，CoNEXT 2015。[作者页面](https://www.cs.cmu.edu/~dga/papers/solstice-conext15-abstract.html)；[论文](papers/solstice-conext15.pdf)。重点：§4.2 QuickStuff、BigSlice与Slicing。

论文方法：补齐矩阵时优先已有非零项；在阈值支持图中寻找perfect matching，以较大切片摊薄配置成本；阈值按二的幂递减。剩余需求可由另一套packet网络承载时结束电路调度。

对FAST的借鉴（推断）：这是“少量阈值候选＋匹配复用”的直接先例，比在线大规模枚举更符合约束；可在其可行图内加入拓扑组合选择。但**Max-Min和阈值分解本身不是我们的新贡献**。本场景没有额外独立packet网络，不能免费转移尾流；虚拟补齐也不能计作真实WAN服务。

## P08 HPDC All-to-All：按源聚合求解，再恢复需求

Prithwish Basu等，HPDC 2024。[论文](https://homes.cs.washington.edu/~arvind/papers/all2all-dc.pdf)。重点：§3.1.2 source-based master LP与child LP。

论文方法：针对direct-connect网络的all-to-all，把完整多商品流问题按源聚合求主问题，再通过子问题恢复各目的需求，降低求解规模。还讨论GPU转发与不同路由约束。

对FAST的借鉴（推断）：可先在粗粒度资源层形成目标，再恢复细粒度调度，但必须有可行性桥梁。该论文的按源聚合不等于任意DC矩阵聚合；其静态均匀A2A也不自动覆盖动态Zipf all-to-allv。这里的direct-connect是物理拓扑术语，**不是我们的Direct调度基线**。

## P09 ForestColl：从端点瓶颈扩展到网络割瓶颈

Liangyu Zhao等，NSDI 2026。[会议页面](https://www.usenix.org/conference/nsdi26/presentation/zhao-liangyu)；[论文](papers/forestcoll-nsdi26.pdf)；[实现](https://github.com/liangyuRain/ForestColl)。重点：瓶颈割、树打包、交换机处理及适用边界。

论文方法：通过网络流与广播/聚合树打包，在相应模型下构造吞吐最优的集合通信；不仅看端点，还考虑中间网络容量。

对FAST的借鉴（推断）：应给出中间链路/割容量下界，衡量距离瓶颈的差距。主要理论围绕AllGather及相关collective，数据复制与all-to-all独有数据不同；吞吐最优也不等于含传播和有限阶段开销的完成时间最优。不能直接拿其树打包结论证明我们的算法。

## P10 WAN通信依赖：不能只看字节与独立流

Yining Wang、Toon Craps、Baochun Li，ICDCS 2026。[实验室发表列表](https://iqua.ece.toronto.edu/publications/)；[论文](https://iqua.ece.toronto.edu/papers/ywang-icdcs26.pdf)。重点：flow group、finish-to-start依赖和并发分配。

论文方法：针对WAN上并发collective，在固定路由下建模流之间的完成后启动依赖。包含非并发MILP，以及基于流组的并发速率/权重分配；并非所有方案都不用求解器。

对FAST的借鉴（推断）：评价候选应识别真正的就绪前沿与关键依赖，不能预留给尚不可发送的流却当成有效利用。原文多Ring/多collective目标与我们的单次all-to-all不同；既不能照搬其barrier，也不能据此去掉FAST已有依赖。

## P11 Varys：目标应是coflow，不是孤立流公平

Mosharaf Chowdhury、Yuan Zhong、Ion Stoica，SIGCOMM 2014。[作者机构页面](https://istc-cc.cmu.edu/publications/papers/2014/varys-sigcomm14_abs.shtml)；[作者PDF](https://web.eecs.umich.edu/~mosharaf/Readings/Varys.pdf)。重点：effective bottleneck、SEBF与MADD；PDF下载失败，本轮保留网页阅读记录。

论文方法：围绕coflow完成时间进行调度和带宽分配，使用有效瓶颈及期望完成时间分配，而非单条流公平。

对FAST的借鉴（推断）：提示我们把整批A2A当成共同目标。但其网络抽象主要约束端口、假设内部充分连通，并非一般共享WAN拓扑算法；多coflow排序也不是单次A2A阶段分解的直接替代品。本轮只作目标建模背景。

## P12 NOTION：高相关，但目前只有书目核验

Hao Zhou等，NOMS 2026。[作者发表列表C111](https://musumeci.faculty.polimi.it/?page_id=198)。题为“NOTION: A Rolling-Horizon Algorithm for Collective Communication Libraries Over WAN”。

已核验：作者页面列出标题、作者及NOMS 2026。未取得：全文、可运行代码、具体窗口规则和保证。因此不使用聚合网站中的性能数字，也不把标题推断当成已读算法。下一轮若选择短前瞻方案，应优先取得全文检查重合；暂不填未一手核验的DOI。

## P13 动态分层BvN：相关预印本，不冒充已发表

Yen-Chieh Wu、Cheng-Shang Chang、Duan-Shin Lee、H. Jonathan Chao，arXiv:2602.22756v1，2026。[预印本页面](https://arxiv.org/abs/2602.22756)；[HTML](https://arxiv.org/html/2602.22756v1)。阅读范围：摘要、模型概览；不是完整复现。

作者主张：在两级GPU网络中进行局部平衡、服务器级到GPU级分层BvN，并结合动态frame sizing，分析可接纳Poisson到达下的队列稳定性。

对FAST的借鉴（推断）：提醒我们“分层BvN”和“动态阶段大小”已有直接相关工作。其队列稳定性不等于有限批次完成时间最优，也不自动解决共享WAN瓶颈。截至本次核验，页面只写提交IEEE审稿，未核验到正式接收信息。

## 如何减少我们的工作量

1. 概念与模型：从TE-CCL/TACCL借共享资源表示，从ForestColl借“先给出瓶颈下界”的论证结构，不再从零解释拓扑为何重要。
2. 轻量构造：先借Solstice的少量阈值支持图，在其中保留FAST合法匹配与残余扣除，再研究拓扑选择；不要先引入通用MILP。
3. 实现复用：上述公开实现可供核对建模细节；尚未运行，也未审查许可证，不承诺可直接复制或无成本集成。
4. 贡献边界：只做WAN优先、静态边权、Max-Min或缓存优化，都不足以自动形成有说服力的新调度贡献。需要证明具体联合规则为何有效、代价多大、何时退化，并与强基线比较。
