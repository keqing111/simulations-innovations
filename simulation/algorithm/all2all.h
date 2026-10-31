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
// ============================================================================

#include <iostream>
#include <vector>
#include <unordered_set>
#include <map>
#include <functional>
#include "matrix.h"
#include "config.h"
using namespace std;


// 匈牙利算法（求二分图最大匹配）的运行时状态。
// 行顶点编号 0..dim-1，列顶点编号 dim..2*dim-1。
// matching/visit 需要覆盖 2*dim 个顶点，row_to_col 只需每个行顶点一项。
struct hungarian_info_t{
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

class FastAll2All{
private:
    Matrix mat;     // 原始输入矩阵（server 级流量需求）
    Matrix S_mat;   // 缩放后的矩阵（未做双随机化）
    Matrix SDS_mat; // 缩放 + 双随机化后的矩阵，分解直接作用在它上面
    struct hungarian_info_t hungarian_info;  // 匈牙利算法运行时状态（匹配、邻接表）
    bool is_scaled;
    uint scaling_factor;
    double MB_per_unit;      // 每个矩阵单位对应多少 MB，用于把流量换算成传输时间
    INTER_LINK_TYPE inter_type;
    struct link_info_t inter_link;   // server 间链路模型（alpha/beta）
    INTRA_LINK_TYPE intra_type;
    struct link_info_t intra_link;   // server 内链路模型（alpha/beta）
    uint gpu_n;

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
