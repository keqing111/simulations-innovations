#pragma once

// ============================================================================
// FastAll2All：FAST 的"server 级"核心算法。
//
// 在 FAST 流程中，输入是一个 server_n × server_n 的流量矩阵：
// 元素不是 server 间的总流量，
// 而是"server i 的每块发送 GPU 要发给 server j 的代表量"（通常是平衡后的
// 每 GPU 平均值，近似模式下取最大行和），由 LocalScheduler 汇总得到。
// 输出是一组置换集合 p_sets。
//
// 整个算法分三步，对应三个公开方法：
//   1. to_scaled_matrix(fn)                 （可选）把矩阵数值整体缩小，
//                                             降低最大行/列和，从而减少后面的阶段数；
//   2. to_scaled_doubly_stochastic_matrix() 把矩阵补齐成行和 = 列和的双随机矩阵；
//   3. decompose()                          用最大二分匹配 + 瓶颈流量，把矩阵拆成
//                                             "置换 × 权重" 的序列（Birkhoff 分解）。
//
// p_sets 中每一项是一个置换集合：它告诉每个源 server 当前阶段应该把数据
// 发往哪个目的 server，以及这一阶段每块发送 GPU 的发送量 freq。
// GlobalScheduler 会把 p_sets 展开成真正的 server 间传输顺序。
//
// 四种完整匹配策略（decompose(options, stats)）共享同一份补齐矩阵、同一个
// “匹配 -> 取最小残量 -> 扣减”循环和同一个 update_permutation_sets() 入口；
// 它们只改变每轮如何选完美匹配：
//   S0 Kuhn              ：原生无权 Kuhn 增广，保留 unordered 遍历顺序；
//   S1 MaximumBottleneck ：二分搜索最大可行瓶颈阈值，取该阈值下的完美匹配；
//   S2 MaximumElimination：在采样阈值上做 0/1 费用 Hungarian，最大化等于阈值的边数；
//   S3 ResidualDegree    ：要求最大度行/列关联的匹配边等于阈值，优先降低支持图最大度。
// 因此四种策略的守恒、终止和精确重构语义一致，差异只在“选哪一个完美匹配”。
// ============================================================================

#include <iostream>
#include <vector>
#include <unordered_set>
#include <map>
#include <functional>
#include <cstdint>
#include "matrix.h"
#include "config.h"
using namespace std;

namespace native_topology {class Topology; struct GpuTransfer;}
enum class ScheduleMode {Native, BandwidthAware, CompletionTimeAware};

// 完整 BvN 匹配策略：只读取同一份原生补齐整数残余，不使用拓扑奖励或部分匹配。
// 四种策略的差异只在本轮匹配选择；扣减、守恒和重构都由下方 decompose() 统一完成。
enum class DecompositionStrategy {
    Kuhn,               // S0：原生无权 Kuhn 增广，保留 unordered 的访问顺序。
    MaximumBottleneck,  // S1：最大可行瓶颈阈值 + 该阈值下的完美匹配。
    MaximumElimination, // S2：有限阈值池上的最大等阈值边数指派。
    ResidualDegree      // S3：最大度端点必须走等于阈值的边，优先降最大度。
};
struct DecompositionOptions {
    DecompositionStrategy strategy = DecompositionStrategy::Kuhn;
    // Opt-in S1 shared-resource pricing; zero preserves every legacy strategy.
    unsigned resource_rounds = 0; // bounded at 8; requires topology + real pending
    // Same-q strict stage-cost descent: 0 off, 2 swaps, 3 swaps + three-cycles.
    // Stops at a local minimum, not after a fixed number of attempts.
    unsigned resource_cycle = 0;
    // Research intervention only: skip pricing at one zero-based decomposition
    // step, then resume the same policy. UINT32_MAX means no intervention.
    uint32_t resource_skip_step = UINT32_MAX;
    // S2/S3 每条残量轮最多尝试的候选阈值数；S0/S1 忽略。必须 >=1。
    unsigned threshold_budget = 8;
};
struct DecompositionStepStats {
    vector<int> resource_seed; // empty when both pricing and cycles are disabled
    double resource_seed_us=0, resource_chosen_us=0;
    vector<double> cycle_costs_us; // initial cost, then each accepted strict decrease
    // eliminated/degree 均计补齐残余的正项（含虚拟容量），不是物理网络流完成数。
    // q/q_scaled：本轮实际最小残量（原始单位 / 缩放单位）。
    // selected_threshold：策略选中的候选阈值；S0 就是 q_scaled，S1 是最大瓶颈，
    // S2 是取得最大消边数的阈值，S3 是受限匹配阈值（回退时为 S1 的瓶颈）。
    uint q = 0, q_scaled = 0, selected_threshold = 0;
    // eliminated：本轮被扣到 0 的匹配边数；degree_*：正残量支持图的最大度。
    unsigned eliminated = 0, degree_before = 0, degree_after = 0;
    // threshold_probes：阈值可行性探测次数；weighted_calls：S2 Hungarian 调用次数。
    uint64_t threshold_probes = 0, weighted_calls = 0;
    // 本轮的进程 CPU 时间（不含 Local/网络模型）；S3 全部候选失败时为 true。
    double cpu_us = 0;
    bool degree_fallback = false;
    // S2/S3 使用的阈值池；S2 还记录每个池项的“等阈值边数”目标值。
    vector<uint> sampled_thresholds;
    vector<unsigned> sampled_eliminations;
};
struct DecompositionStats {
    uint64_t resource_proposals=0, resource_accepts=0;
    uint64_t cycle_evaluations=0, cycle_accepts=0, cycle_passes=0;
    // steps 是逐轮 trace；output 为 nullptr 时不保存，计数与 CPU 仍可累计。
    uint64_t decomposition_steps = 0, threshold_probes = 0, weighted_calls = 0;
    double cpu_us = 0, cpu_tick_us = 0;
    vector<DecompositionStepStats> steps;
};

// 匈牙利算法（求二分图最大匹配）的运行时状态。
// 行顶点编号 0..dim-1，列顶点编号 dim..2*dim-1。
// matching/visit 需要覆盖 2*dim 个顶点，row_to_col 只需每个行顶点一项。
struct hungarian_info_t {
    // 拓扑匹配工作区：同一次分解复用，不复制到PermutationSet。
    struct Candidate {
        uint dest, ordinal;
        double peak, square;
    };
    vector<int> seed_matching, candidate_matching, inverse_matching;
    vector<unsigned char> candidate_visit;
    vector<double> resource_load_bytes, edge_cost_us, edge_alpha_us, cost_thresholds_us;
    vector<vector<pair<uint,double>>> edge_loads;
    vector<vector<double>> reduced_loads;
    vector<vector<Candidate>> candidates;
    vector<int> matching;                       // 长度 2*dim。matching[row] = 列顶点编号（j+dim）；matching[col_id] = 匹配的行；-1 表示未匹配
    vector<bool> visit;                         // 长度 2*dim，DFS 过程中的访问标记，防止重复访问
    vector<unordered_set<uint> > row_to_col;    // 长度 dim。邻接表：每个行顶点可以连接到哪些列顶点（只保留矩阵中 >0 的边）
};

// 一个置换集合 = 一个置换矩阵 + 权重。
// mp 表示 "行 i -> 列 mp[i]" 的置换关系；frequency 是这个置换矩阵的流量权重。
// 多个 PermutationSet 按顺序叠加起来，能还原出双随机化后的矩阵 SDS_mat；
// 由于双随机化可能补过流量，它不一定等于最开始的原始矩阵。
class PermutationSet{
private:
    uint frequency;       // 在"缩放后"矩阵单位下的权重
    uint scaling_factor;  // 生成该置换时的缩放因子，用于把频率换算回原始单位
    uint dim;
public:
    map<uint, uint> mp;     // 行顶点 -> 列顶点，索引范围 0..dim-1
    PermutationSet(uint _freq = 1, uint _sf = 1, uint _dim = 0):frequency(_freq), scaling_factor(_sf), dim(_dim){}
    ~PermutationSet(){}
    void set_freq(uint freq){frequency = freq;}
    // 对外使用原始单位下的权重：缩放时矩阵被除以 factor，这里乘回来。
    uint get_freq() const {return frequency * scaling_factor;}
    void print_permutation_matrix();
};

// 某一缩放因子下的矩阵统计信息，供"是否接受这个缩放因子"的判定函数使用。
// max_sum 是缩放后矩阵的最大行/列和；factor 是缩放因子。
struct scale_info_t{
    uint max_sum;
    uint factor;
};

// 判定函数：给定一次缩放尝试的结果，返回这个缩放因子是否可接受。
// 参数依次为 server 数、每 server GPU 数、缩放信息、server 间/内链路模型、每个单位的 MB 数。
typedef bool (*scaling_success_condition)(uint, uint, struct scale_info_t, struct link_info_t, struct link_info_t, double);
bool limit_max_sum(struct scale_info_t s_info);

// 这个类中具体实现算法的分解
class FastAll2All{
private:
    Matrix mat;     // 原始输入矩阵（server 级流量需求）
    Matrix S_mat;   // 缩放后的矩阵（未做双随机化）
    Matrix SDS_mat; // 缩放 + 双随机化后的矩阵，分解直接作用在它上面，也就是分解时不断扣减的残余矩阵
    struct hungarian_info_t hungarian_info;  // 匈牙利算法运行时状态（匹配、邻接表）
    bool is_scaled;
    uint scaling_factor;
    double MB_per_unit;      // 每个矩阵单位对应多少 MB，用于把流量换算成传输时间
    INTER_LINK_TYPE inter_type;
    struct link_info_t inter_link;   // server 间链路模型（alpha/beta）
    INTRA_LINK_TYPE intra_type;
    struct link_info_t intra_link;   // server 内链路模型（alpha/beta）
    uint gpu_n;

    vector<uint64_t> remaining_units_;
    void adjust_matching(ScheduleMode,const native_topology::Topology&);
    void prepare_topology_stage(const native_topology::Topology&,uint q_scaled);
    bool match_bandwidth(const native_topology::Topology&,uint q_scaled,vector<int>&);
    bool match_completion_time(const native_topology::Topology&,uint q_scaled,vector<int>&);
    bool accept_candidate(ScheduleMode,const native_topology::Topology&,uint q_scaled);
    void build_transfers(const vector<int>&,uint q_scaled,vector<native_topology::GpuTransfer>&) const;
    void consume_pending(const vector<int>&,uint q_scaled);
    uint original_units(uint q_scaled) const;
    void match_resource_priced(const native_topology::Topology&,uint,vector<int>&,
                               unsigned,DecompositionStats&,DecompositionStepStats&);
    void match_resource_cycles(const native_topology::Topology&,uint,vector<int>&,
                               unsigned,DecompositionStats&,DecompositionStepStats&);

public:
    vector<PermutationSet> p_sets; // 分解结果：每个元素是一个"置换 + 权重"阶段
    FastAll2All(Matrix * _mat, uint _gpu_n, INTER_LINK_TYPE _type = INFB, INTRA_LINK_TYPE _itype = DGX2, double MBpu = 1.0);
    ~FastAll2All(){}
    void print(){is_scaled ? S_mat.print() : mat.print();SDS_mat.print();}
    // 用指定 factor 缩放矩阵，并返回缩放后的最大行/列和信息（只写 buffer，不修改成员）。
    struct scale_info_t scale_matrix(uint factor, uint* buffer);
    // 在 [l, r] 中按 step 查找满足条件的最小缩放因子；返回 -1 表示找不到。
    int scaling_binary_search(scaling_success_condition fn, int l, int r, uint step, uint* buffer);
    // 选择一个缩放因子并把 mat 缩放保存到 S_mat；找不到则保持不缩放。
    void to_scaled_matrix(scaling_success_condition fn);
    // 把（缩放后的）矩阵补齐成所有行和、列和都相等的双随机矩阵。
    void to_scaled_doubly_stochastic_matrix();
    // 根据 SDS_mat 中 >0 的元素刷新匈牙利算法的邻接表。
    void update_edges();
    // 沿当前匹配取瓶颈流量作为一个置换集合，更新残量矩阵并返回该阶段的频率。
    uint update_permutation_sets();
    // Birkhoff 分解主循环：反复"找完美匹配 -> 取瓶颈 -> 减去"直到流量耗尽。
    void decompose();
    // 带策略的完整分解：S0-S3 共享原生补齐、提取和终止条件，只改变每轮匹配选择。
    // q 是原始计数单位，阈值/q_scaled 是缩放后整数；stats 为空时仍计算但不保存
    // 逐轮 trace。CPU 计时使用 std::clock，与外层墙钟分开；resource_rounds>0
    // 时包含资源缓存、价格更新、带权指派与拓扑阶段评分，不只计匹配内核。
    void decompose(const DecompositionOptions&, DecompositionStats* stats=nullptr,
                   const native_topology::Topology* topology=nullptr,
                   const vector<uint64_t>* pending=nullptr);
    // pending按(src_server,dst_server,src_gpu)展平，单位与原始矩阵相同。
    void decompose(ScheduleMode,const native_topology::Topology&,const vector<uint64_t>& pending_units);
    // 与 decompose 相同，但允许在每次匹配后回调 adjust 来修改矩阵/匹配（研究用扩展点）。
    void decompose_with(const std::function<void(Matrix&, hungarian_info_t&)>& adjust);
    // 用 Kuhn 增广路求二分图最大匹配，返回匹配边数。
    uint hungarian();
    bool hungarian_dfs(uint u);
    // 用 p_sets 重建矩阵，验证分解结果与 SDSM 是否一致。
    bool verify_decomposition();
    void print_decomposition();
};

bool limit_max_sum(uint server_n, uint gpu_n, struct scale_info_t s_info, struct link_info_t inter_link, struct link_info_t intra_link, double MBpu);
bool balance_alpha_beta(uint server_n, uint gpu_n, struct scale_info_t s_info, struct link_info_t inter_link, struct link_info_t intra_link, double MBpu);
bool always_scale(uint server_n, uint gpu_n, struct scale_info_t s_info, struct link_info_t inter_link, struct link_info_t intra_link, double MBpu);
