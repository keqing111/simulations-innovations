#pragma once
#include "matching.h"
#include <memory>

// ============================================================================
// scan/ 是核心仿真器之上的“算法扫描”扩展。
//
// 它不替换 FAST 的 server 内 balance/dispatch，也不改 alpha-beta 模型；
// 只在 Birkhoff 分解的每一轮（每次 hungarian() 之后、扣减 residual 之前）
// 插入一个 choose() 决策：重新挑选这一阶段的 perfect matching 和系数，
// 从而比较 G/L/R、精确 Max-Min、构造式匹配等策略。
//
// 与核心代码的连接点是 pipeline_hooks_t::prepare：
//   GlobalScheduler 完成 server 内 balance 后，把每个 (源 server, 目的 server,
//   源 GPU) 的真实剩余 payload 交给本目录的 Planner；Planner 随后调用
//   FastAll2All::decompose_with，把自己的回调挂在每一轮分解里。
//
// 术语：
//   a[s*n+d]      server 级残余容量矩阵（FastAll2All 当前的 SDS_mat）
//   remaining     GPU 级真实剩余需求，索引 [(s*n+d)*g+u]；补齐产生的虚拟容量
//                 不在这里，所以优化不能靠“多搬虚拟流量”得分
//   coefficient   一个 perfect matching 上所有边的 min(a[s][m[s]])，即本轮
//                 能同时抽走的最大公共系数，也就是该阶段真正的频次上界
// ============================================================================

namespace native_scan {
// 一个方法名（如 F010、X11-4、A1DP）解析后的策略开关。
// Fxyz 的三个字母分别对应 granularity/link/residual；其余命名是预置组合。
struct Options {
    bool granularity=false,link=false,residual=false,exact=false;
    bool tail_score=false;      // B11：评分时加上剩余需求的乐观下界
    bool optimized=false,static_path=false; // -opt 实现优化；SPM 静态路径最大权匹配
    unsigned quota_numerator=1,quota_denominator=1,cycle_budget=2; // Q75/Q50；L 每阶段尝试次数
    unsigned constructive_budget=0; // A1=1, A2=2：每阶段重新构造完整匹配的次数
    unsigned row_order=0; // 0 natural,1 scarce-degree,2 residual-pressure,3 degree then pressure.
    static Options parse(const std::string& method);
};

// 一个候选阶段（matching × coefficient）的资源模型结果：
// bytes 是实际取走的跨机真实字节，us 是拓扑模型估出的该阶段耗时。
struct CostSummary {double us=0;uint64_t bytes=0;};
// 把拓扑路径预先展开成“资源 id”列表，避免每个候选阶段都重新构造字符串路径。
// score/priorities/remaining_bound 都是只读查询；临时数组（loads 等）复用，
// 所以同一个 ResourceCache 不能并发调用。
class ResourceCache {
public:
    explicit ResourceCache(const native_topology::Topology&);
    // 给定 matching、公共系数 w 和真实逐 GPU 残余，算本阶段拓扑代价。
    // 只统计 s!=dst 且真实 bytes>0 的流；虚拟补齐不贡献。
    CostSummary score(const std::vector<int>&,unsigned,const std::vector<uint64_t>&);
    // 每条 (src server, dst server) 边的残余压力：按当前所有真实剩余需求
    // 在各链路上形成的 load/带宽，加权累计到经过该边的流上。
    std::vector<double> priorities(const std::vector<uint64_t>&);
    // 把边压力按行汇总；只统计支持图 a[s,d]>=q 的边，供构造排序使用。
    std::vector<double> row_priorities(const std::vector<unsigned>&,unsigned,const std::vector<uint64_t>&);
    // 当前阶段结束后剩余需求的一个乐观完成时间下界（B11 的 tail score）。
    double remaining_bound(const std::vector<int>&,unsigned,const std::vector<uint64_t>&);
    // SPM 的静态边效用：真实字节 / (路径延迟 + 单流串行时间)。
    std::vector<double> individual_utilities(unsigned,const std::vector<uint64_t>&);
    // 在支持图 a>=q 上构造 perfect matching；候选目的地按“加入后共享资源
    // 峰值 + 负载平方增量”排序，而不是按行号/原始顺序。optimized 复用工作区。
    bool balanced_matching(const std::vector<unsigned>&,const std::vector<std::vector<unsigned>>&,
                          unsigned,const std::vector<uint64_t>&,const std::vector<unsigned>&,std::vector<int>&,bool optimized=false);
private:
    unsigned n,g;
    std::vector<int> dc;
    std::vector<std::vector<unsigned>> routes; // 每条 server 对的资源 id 序列
    std::vector<double> bandwidth,alpha;       // 资源带宽（Gbps）、路径固定延迟（us）
    std::vector<uint64_t> loads;               // score/priorities 的可复用临时数组
    struct Candidate {unsigned d,ordinal;double peak,square;};
    // balanced_matching(optimized=true) 的复用工作区：
    // 行临时负载、候选数组、visited 标记等，避免每轮 DFS 反复分配。
    std::vector<uint64_t> work_volume,work_used;
    std::vector<int> work_inverse;
    std::vector<unsigned char> work_seen,work_ordered;
    std::vector<std::vector<uint64_t>> work_reduced;
    std::vector<std::vector<Candidate>> work_candidates;
    std::vector<double> path_min_bw;           // 每条路径上最小带宽（静态缓存）
};

// choose() 对当前阶段的完整决定，同时也是审计/JSON 输出的记录。
struct Decision {
    std::vector<int> matching;      // 行 server -> 目的 server
    // threshold 本阶段最小支持容量(q)；seed_weight 进入修补/构造前的原生
    // 系数；weight 最终采用系数；probes 为 G 的阈值探测次数(<=6)。
    unsigned threshold=1,seed_weight=0,weight=0,probes=0;
    // native_weight 原始 hungarian 匹配系数；exact_weight 精确 Max-Min 的 q*。
    unsigned native_weight=0,exact_weight=0;
    unsigned constructions=0;       // 本阶段实际执行的构造式匹配次数
    std::vector<unsigned> row_order; // A1D/A1P/A1DP 的构造行顺序（诊断输出）
    std::vector<double> row_pressure; // 对应的行压力
};

// q* 支持图的自由度诊断：有多少行能通过交替环换到别的目的。
struct SupportInfo {
    // edges 支持边数；flexible_edges 属于非平凡 SCC 的非匹配边数；
    // movable_rows 位于非平凡 SCC、可参与交替环的行数；
    // real_movable_rows 其中涉及真实残余（不是纯虚拟容量）的行数。
    unsigned edges=0,flexible_edges=0,movable_rows=0,real_movable_rows=0;
    std::vector<unsigned> degrees; // 每个行顶点的支持度
};
SupportInfo support_summary(const std::vector<unsigned>&,unsigned,unsigned,const std::vector<int>&,
                            const std::vector<uint64_t>&,unsigned);
std::vector<unsigned> constructive_order(const std::vector<unsigned>&,unsigned,unsigned,const std::vector<double>&,unsigned);
std::vector<int> maximum_weight_matching(const std::vector<unsigned>&,unsigned,unsigned,const std::vector<double>&);

// 一个 Planner 的累计运行统计；decisions 按阶段保存，便于回放/比较。
struct Stats {
    uint64_t stages=0,decomposition_runs=0,threshold_probes=0,matching_calls=0;
    uint64_t cycle_trials=0,changed_matches=0,lower_coefficient_rejections=0;
    uint64_t infeasible_cycles=0,nonimproving_rejections=0;
    uint64_t construction_trials=0,construction_accepted=0;
    std::vector<Decision> decisions;
};

// 单次分解的规划器。内部保存逐 GPU 真实剩余，每个阶段只扣减一次，
// 因此不可重复调用 decompose_into（会抛 logic_error）。
class Planner {
public:
    Planner(const native_topology::Topology&,std::vector<uint64_t>,const std::string&);
    // 对当前残余矩阵 a 和原生 hungarian 结果，决定本阶段 matching。
    Decision choose(const std::vector<unsigned>&,const hungarian_info_t&);
    void decompose_into(FastAll2All&);
    uint64_t remaining_inter_bytes()const;
    const Stats& stats()const{return counts;}
    double preprocess_us=0; // 构造 ResourceCache 的 CPU 时间（计入规划开销）
private:
    const native_topology::Topology& top;
    std::vector<uint64_t> remaining; // 真实逐 GPU 剩余需求
    Options options;
    Stats counts;
    std::unique_ptr<ResourceCache> resources;
    void consume(const std::vector<int>&,unsigned);
};
}
