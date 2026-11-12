#include "all2all.h"
#include "matrix.h"
#include "define.h"
#include <iomanip>
#include <pthread.h>
#include "../topology/model.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <ctime>
#include <array>
using namespace std;
// ============================================================================
// FastAll2All 的实现：把 server 级流量矩阵拆成若干"置换 × 权重"。
//
// 算法流程（对应论文中的 Birkhoff-von Neumann 分解）：
//   1. 可选缩放：所有元素一起除以 factor，把最大行/列和压到较小范围，
//      从而减少分解出的阶段数；代价是量化误差。
//   2. 双随机化：往行/列和的"缺口"里补流量，使每行、每列的和都等于最大值。
//   3. 反复执行：
//        a. 在"有流量（>0）"的边构成的二分图上求最大匹配（匈牙利算法）；
//        b. 匹配上的每条边取最小残量作为这一阶段的频率 frequency；
//        c. 所有匹配边减去该频率，记录一个 PermutationSet；
//      直到所有流量都被消耗（累计频率 = 最大行/列和）。
//
// 乘积 "置换矩阵 × frequency" 按顺序叠加，正好等于双随机化后的矩阵。
// ============================================================================

// 构造函数：保存原始矩阵、初始化匈牙利算法状态、记录链路模型。
// 注意这里的 mat 是 server 级矩阵（维度 = server 数），每个元素代表
// "每块发送 GPU 发给该目的 server 的量"，而不是 server 间总流量；
// gpu_n 只用于估算 server 内链路的开销（scaling 判定函数会用到）。
FastAll2All::FastAll2All(Matrix * _mat, uint _gpu_n, INTER_LINK_TYPE _type, INTRA_LINK_TYPE _itype, double MBpu){
    is_scaled = false;
    mat.copy(_mat);
    uint dim = mat.get_dim();
    // 行、列顶点各 dim 个，所以匹配数组长度是 2*dim
    hungarian_info.matching.insert(hungarian_info.matching.end(), dim*2, -1);
    hungarian_info.visit.insert(hungarian_info.visit.end(), dim*2, false);
    unordered_set<uint> empty_vector;
    hungarian_info.row_to_col.insert(hungarian_info.row_to_col.end(), dim, empty_vector);
    MB_per_unit = MBpu;
    inter_type = _type;
    inter_link = get_inter_link_info(_type);
    intra_type = _itype;
    intra_link = get_intra_link_info(_itype);
    scaling_factor = 1;
    gpu_n = _gpu_n;
}

// 把矩阵补齐成"缩放双随机矩阵"：所有行和、列和都等于 max_row_col_sum。
//
// 做法很朴素：对每个"行和不足"的行，找"列和不足"的列，在交叉位置补上
//   diff = max_sum - max(当前行和, 当前列和)
// 这样每补一次，行和与列和中较大的那个会正好达到 max_sum，另一个只会增加；
// 跳过已经达到 max_sum 的列，行一旦补齐就换下一行。
// 由于每补一次至少让一行或一列达到目标，循环一定会结束；又因为行和与列和的
// 总和始终相等，最终所有行和、列和都会达到 max_sum。
//
// 这些补出来的元素只是为了让矩阵满足分解前提（保证每轮都存在完美匹配），
// 不一定对应真实业务流量。后续 server2server_dispatch() 只搬运真实待发数据，
// 补齐项本身不会进入 dispatch 矩阵；但计时时每个阶段仍按 pset 的 freq 计费，
// 所以补齐量会抬高模型中的阶段数和 server 间传输时间。
void FastAll2All::to_scaled_doubly_stochastic_matrix(){
    // 选择输入：如果做过 scaling 就用 S_mat，否则直接用原始矩阵 mat。
    if (!is_scaled){
        mat.get_sdsm_info();
        SDS_mat.copy(&mat);
    }else{
        S_mat.get_sdsm_info();
        // cout << "scaling factor " << scaling_factor  << ", max row/col sum: " << S_mat.sdsm_info.max_row_col_sum << endl;
        SDS_mat.copy(&S_mat);
    }

    // 由于 get_sdsm_info() 的计数偏差，这个条件对已平衡的矩阵通常也为真；
    // 此时 non_max_row/non_max_col 为空，下面的双层循环不补任何东西。
    if(!SDS_mat.sdsm_info.is_sdsm){
        uint dim = SDS_mat.get_dim();
        uint max_sum = SDS_mat.sdsm_info.max_row_col_sum;
        // original matrix is not SDSM, do the conversion
        // 遍历所有"行和不足"的行；每行的缺口往"列和不足"的列里填。
        for (vector<struct row_col_info_t>::iterator row = SDS_mat.sdsm_info.non_max_row.begin(); row != SDS_mat.sdsm_info.non_max_row.end(); row++){
            for (vector<struct row_col_info_t>::iterator col = SDS_mat.sdsm_info.non_max_col.begin(); col != SDS_mat.sdsm_info.non_max_col.end(); col++){
                if (col -> sum == max_sum)
                    continue;
                // 取行、列缺口的较大者，保证补完后行/列都不会超过 max_sum
                uint diff =  max_sum - MAX(row -> sum, col -> sum);
                SDS_mat.add(diff, row -> idx, col -> idx);
                row -> sum += diff;
                col -> sum += diff;
                if (row -> sum == max_sum) {
                    break;   // 当前行已补齐，处理下一行
                }
            }
        }
        // 补齐后矩阵已满足分解条件，更新标志即可（不必重新扫一遍矩阵）。
        SDS_mat.sdsm_info.is_sdsm = true;
        SDS_mat.sdsm_info.non_max_row.clear();
        SDS_mat.sdsm_info.non_max_col.clear();
        // SDS_mat.get_sdsm_info();
    }
}

// 旧的无参数入口：等价于 S0 原生 Kuhn 分解，不产生逐轮统计。
// 需要统计或选择 S1-S3 时使用下面的 decompose(options, stats)。
void FastAll2All::decompose(){
    decompose_with({});
}

namespace {
// 四种策略共享的每轮工作区。为了避免在匹配递归里反复访问 Matrix，
// collect() 对当前残余做一次 O(n^2) 稠密扫描，并同时维护：
//   values[s*n+d]：当前残量的快照，供 S2 的 Hungarian 随机访问；
//   neighbors[s] ：残量 > 0 的列，供 S1/S3 的 Kuhn 只遍历正边；
//   row_degree/col_degree：正残量支持图上的行/列度，S3 和统计使用；
//   thresholds   ：所有正残量去重后的升序阈值表，供 S1 二分和 S2/S3 采样。
// 注意 S0 不需要阈值和邻接表；need_thresholds=false 时只做稠密 values/度数扫描。
// 空间是 O(n^2)，单轮时间也包含 O(n^2)；不要把“匹配只遍历正边”
// 误写成整个算法是 O(m) 或 O(nm)。
struct CompleteMatchingWorkspace {
    unsigned n;
    vector<uint> values, thresholds;
    vector<vector<unsigned>> neighbors;
    vector<unsigned> row_degree, col_degree;
    vector<int> inverse;
    vector<unsigned char> visited;
    unsigned maximum_degree = 0;
    explicit CompleteMatchingWorkspace(unsigned dim)
        : n(dim), values(size_t(dim) * dim), neighbors(dim), row_degree(dim), col_degree(dim),
          inverse(dim), visited(dim) {
    }
    void collect(Matrix &matrix, bool need_thresholds) {
        thresholds.clear();
        maximum_degree = 0;
        fill(row_degree.begin(), row_degree.end(), 0);
        fill(col_degree.begin(), col_degree.end(), 0);
        for (unsigned s = 0; s < n; ++s) {
            neighbors[s].clear();
            for (unsigned d = 0; d < n; ++d) {
                uint value = matrix.get(s, d);
                values[size_t(s) * n + d] = value;
                if (value) {
                    if (need_thresholds) {
                        neighbors[s].push_back(d);
                        thresholds.push_back(value);
                    }
                    ++row_degree[s];
                    ++col_degree[d];
                }
            }
        }
        for (unsigned s = 0; s < n; ++s)
            maximum_degree = max(maximum_degree, max(row_degree[s], col_degree[s]));
        sort(thresholds.begin(), thresholds.end());
        thresholds.erase(unique(thresholds.begin(), thresholds.end()), thresholds.end());
    }
};
// 在给定阈值 threshold 下判断是否存在完整（完美）匹配，并把结果写入 result。
// 可用的边必须满足 value >= threshold；当 restrict_degree=true（S3）时，
// 只要行或列已经是当前支持图的最大度端点，该边还必须恰好等于 threshold。
// 这里用 Kuhn 增广路，只遍历 collect() 缓存的邻居；最坏时间为 O(n*m)，
// 其中 m 是正残量边数，而不是整张 n*n 矩阵。result[s] 是源行 s 的目的列。
bool complete_threshold_matching(CompleteMatchingWorkspace &work, uint threshold,
                                 bool restrict_degree, vector<int> &result) {
    fill(work.inverse.begin(), work.inverse.end(), -1);
    function<bool(unsigned)> augment = [&](unsigned s) {
        for (unsigned d : work.neighbors[s]) {
            uint value = work.values[size_t(s) * work.n + d];
            if (value < threshold || work.visited[d])
                continue;
            if (restrict_degree &&
                (work.row_degree[s] == work.maximum_degree ||
                 work.col_degree[d] == work.maximum_degree) &&
                value != threshold)
                continue;
            work.visited[d] = 1;
            if (work.inverse[d] < 0 || augment(unsigned(work.inverse[d]))) {
                work.inverse[d] = s;
                return true;
            }
        }
        return false;
    };
    for (unsigned s = 0; s < work.n; ++s) {
        fill(work.visited.begin(), work.visited.end(), 0);
        if (!augment(s))
            return false;
    }
    result.assign(work.n, -1);
    for (unsigned d = 0; d < work.n; ++d)
        result[work.inverse[d]] = d;
    return true;
}
// S1 的核心：在升序阈值表上二分“最大可行瓶颈”。
// 对固定完美匹配 P，本轮瓶颈是 min{(s,P(s)) 的残量}；阈值越低，可用边越多，
// “存在完美匹配”这一性质单调，因此可以二分。最终 best 是最大可行阈值，
// result 取该阈值下的一次可行匹配。由于 best 是最大可行阈值，匹配中至少
// 有一条边的残量恰好等于 best；否则更高的阈值仍然可行，和 best 的最大性矛盾。
// 返回 0 表示正残量支持图没有完美匹配（调用方会抛出内部错误）。
uint maximum_bottleneck_matching(CompleteMatchingWorkspace &work, vector<int> &result,
                                 DecompositionStepStats &trace) {
    size_t lo = 0, hi = work.thresholds.size();
    uint best = 0;
    vector<int> trial;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        ++trace.threshold_probes;
        if (complete_threshold_matching(work, work.thresholds[mid], false, trial)) {
            best = work.thresholds[mid];
            result = trial;
            lo = mid + 1;
        } else
            hi = mid;
    }
    if (!best)
        throw runtime_error("positive residual lost perfect matching");
    return best;
}
// 从 <= bottleneck 的正残量中，按排序后的索引均匀取最多 budget 个阈值。
// 这里不是按数值等距，而是按“不同残量值”的序号取分位点：
//   - budget >= 2 时一定包含最小正残量和最大可行瓶颈 bottleneck；
//   - budget == 1 时只取 bottleneck；
//   - 候选数少于 budget 时全部取用。
// 该池子供 S2 做最大消边指派、S3 做受限消度匹配；它是有限预算的启发式，
// 不枚举所有可能阈值，也不保证全局最少阶段。
vector<uint> sampled_complete_thresholds(const vector<uint> &distinct, uint bottleneck,
                                         unsigned budget) {
    size_t available = upper_bound(distinct.begin(), distinct.end(), bottleneck) - distinct.begin();
    size_t count = min(size_t(budget), available);
    vector<uint> result;
    if (count == 1)
        result.push_back(bottleneck);
    else
        for (size_t i = 0; i < count; ++i)
            result.push_back(distinct[i * (available - 1) / (count - 1)]);
    return result;
}
// S2 的核心：给定阈值 t，求一个完整匹配，使所有选中边都满足 value >= t，
// 并让“恰好等于 t”的边数最多（这些边在扣减后会变成 0，因此称为消边）。
//
// 把等于 t 的边费用设为 0，把严格大于 t 的边费用设为 1，小于 t 的边完全不
// 进入松弛（禁止边不能被有限罚值“放宽”）。于是最小费用 = n - 等 t 边数，
// 最小化费用等价于最大化等 t 边数。这里使用标准 O(n^3) 最短增广路
// Hungarian（行列都按 1-based 编号）；若当前行到某列是禁边，该列不会从
// 当前行获得新的松弛，但树中其它行此前建立的松弛仍然有效。
// 前置条件：threshold <= maximum_bottleneck_matching 的结果，因此至少存在
// 一个可行完整匹配。函数最后重新检查每条选中边，并统计等于 t 的边数。
// 注意：这是“固定阈值下的最大消边指派”，不是枚举所有置换，也不是把
// 贪心挑出的 t 边再补全冒充最大权匹配。
bool maximum_elimination_assignment(const CompleteMatchingWorkspace &work, uint threshold,
                                    vector<int> &result, unsigned &eliminated) {
    const int infinity = numeric_limits<int>::max() / 4;
    unsigned n = work.n;
    // 标准最短增广路 Hungarian 的 1-based 状态：
    //   assigned[d]：当前与列 d 匹配的行；assigned[0] 是本轮新加入的行；
    //   row_potential/col_potential：行列对偶势，用来维护非负的 reduced cost；
    //   previous[d]：交替树中列 d 的前驱列，用于最后翻转增广路。
    vector<int> row_potential(n + 1, 0), col_potential(n + 1, 0), assigned(n + 1, 0),
        previous(n + 1, 0);
    for (unsigned row = 1; row <= n; ++row) {
        // 把新行挂到虚拟列 0，随后在等号图中找一条到空闲列的增广路。
        assigned[0] = row;
        unsigned col = 0;
        // slack[d] 是“当前交替树到列 d”的最小 reduced cost；只有合法边
        // (value >= threshold) 才会被松弛，禁边保持 infinity。
        vector<int> slack(n + 1, infinity);
        vector<unsigned char> used(n + 1, 0);
        do {
            used[col] = 1;
            unsigned active_row = assigned[col];
            int delta = infinity;
            unsigned next = 0;
            for (unsigned d = 1; d <= n; ++d)
                if (!used[d]) {
                    uint value = work.values[size_t(active_row - 1) * n + d - 1];
                    if (value >= threshold) {
                        // 费用 0 表示“本条边等于阈值”，费用 1 表示“严格大于阈值”。
                        int cost = (value == threshold ? 0 : 1) - row_potential[active_row] -
                                   col_potential[d];
                        if (cost < slack[d]) {
                            slack[d] = cost;
                            previous[d] = col;
                        }
                    }
                    // 其它树内行已建立的松弛仍有效，即使当前行到 d 是禁边。
                    if (slack[d] < delta) {
                        delta = slack[d];
                        next = d;
                    }
                }
            if (delta == infinity)
                return false;
            // 对偶更新：树内行势加 delta、树内列势减 delta，非树列 slack 同步减少。
            for (unsigned d = 0; d <= n; ++d) {
                if (used[d]) {
                    row_potential[assigned[d]] += delta;
                    col_potential[d] -= delta;
                } else if (slack[d] != infinity)
                    slack[d] -= delta;
            }
            col = next;
        } while (assigned[col] != 0);
        // 沿 previous 反向翻转增广路，把新行接入匹配。
        do {
            unsigned prior = previous[col];
            assigned[col] = assigned[prior];
            col = prior;
        } while (col != 0);
    }
    result.assign(n, -1);
    eliminated = 0;
    for (unsigned d = 1; d <= n; ++d) {
        // 标准 Hungarian 应给出每个行号恰好一次；这里显式检查，避免实现退化时
        // 用 assigned[d]-1 访问越界，也避免重复行被静默当成不同边。
        int row_index = assigned[d];
        if (row_index <= 0 || unsigned(row_index) > n || result[row_index - 1] != -1)
            throw runtime_error("weighted assignment is not a permutation");
        unsigned s = unsigned(row_index) - 1;
        uint value = work.values[size_t(s) * n + d - 1];
        if (value < threshold)
            throw runtime_error("weighted assignment selected forbidden edge");
        result[s] = d - 1;
        eliminated += value == threshold;
    }
    return true;
}
// 只测进程 CPU 时间（std::clock），不是墙钟，也不包含网络模型计算。
// 小步长可能因时钟精度读成 0；调用方通过 cpu_tick_us 了解标称精度。
// clock() 失败或回绕时显式报错，不把负时间静默当成有效样本。
double decomposition_cpu_elapsed(clock_t start) {
    clock_t finish = std::clock();
    if (start == clock_t(-1) || finish == clock_t(-1) || finish < start)
        throw runtime_error("process CPU clock unavailable or wrapped");
    return 1e6 * double(finish - start) / CLOCKS_PER_SEC;
}
// 所有完整分解入口共用同一组校验；GlobalScheduler::set_decomposition 为了
// 尽早失败会再调用一次同样的检查（见 scheduler/global.cpp）。
void validate_decomposition_options(const DecompositionOptions &options) {
    if((options.resource_cycle&&options.resource_cycle!=2&&options.resource_cycle!=3)||
       (options.resource_cycle&&options.strategy!=DecompositionStrategy::MaximumBottleneck)||
       (options.resource_cycle&&options.resource_skip_step!=UINT32_MAX))
        throw invalid_argument("resource cycle requires S1, length 2|3 and no skip intervention");
    if(options.resource_skip_step!=UINT32_MAX&&!options.resource_rounds)
        throw invalid_argument("resource skip requires resource pricing");
    if(options.resource_rounds>8 || (options.resource_rounds &&
       options.strategy!=DecompositionStrategy::MaximumBottleneck))
        throw invalid_argument("resource pricing requires S1 and at most 8 rounds");
    if (options.strategy != DecompositionStrategy::Kuhn &&
        options.strategy != DecompositionStrategy::MaximumBottleneck &&
        options.strategy != DecompositionStrategy::MaximumElimination &&
        options.strategy != DecompositionStrategy::ResidualDegree)
        throw invalid_argument("unsupported decomposition strategy");
    if (!options.threshold_budget)
        throw invalid_argument("threshold budget must be positive");
}
} // namespace

// 带策略的完整分解入口。四种策略共享同一份 SDS_mat（原生补齐后的整数残量）、
// 同一个“匹配 -> 取最小残量 -> 扣减”循环，以及同一个
// update_permutation_sets() 扣减入口；每轮只替换“选哪个完美匹配”：
//   S0 Kuhn              ：原生无权 Kuhn 增广，保留 unordered 访问顺序；
//   S1 MaximumBottleneck ：二分最大可行瓶颈 t*，在 t* 下取一个完美匹配；
//   S2 MaximumElimination：在有限阈值池上做 0/1 费用 Hungarian，优先最大化
//                           “等于阈值”的匹配边数，再按阈值高者打平；
//   S3 ResidualDegree    ：同一阈值池降序试，要求最大度行/列关联的匹配边恰为
//                           阈值，优先让支持图最大度下降；全部失败回退 S1。
//
// 统一不变量：
//   1. selected 必须是 n 个源到 n 个目的的一个完整置换；
//   2. actual = 选中边的最小残量，必须 > 0；
//   3. 只通过 update_permutation_sets() 扣减，保持与原生提取语义一致；
//   4. 每轮至少消掉一条正边，frequency_sum 单调增加，最终达到 maximum_sum。
//
// output==nullptr 时，S0 直接走旧 decompose()（不产生逐轮 trace）；其它策略
// 仍完整运行，只是不保存逐轮 stats。CPU 计时使用 std::clock()，是进程 CPU
// 时间，不是墙钟，也不包含 Local 工作或网络模型计算。
void FastAll2All::decompose(const DecompositionOptions &options, DecompositionStats *output,
                          const native_topology::Topology* topology,const vector<uint64_t>* pending) {
    validate_decomposition_options(options);
    const bool refine=options.resource_rounds||options.resource_cycle;
    if(refine && (!topology||!pending||topology->n!=SDS_mat.get_dim()||
       topology->g!=gpu_n||pending->size()!=size_t(topology->n)*topology->n*gpu_n))
        throw invalid_argument("resource pricing requires matching topology and pending units");
    if(refine&&(!isfinite(MB_per_unit)||MB_per_unit<=0))
        throw invalid_argument("resource pricing requires positive finite data unit");
    if (!SDS_mat.valid_sdsm())
        throw invalid_argument("decomposition requires native padded matrix");
    if (options.strategy == DecompositionStrategy::Kuhn && !output) {
        decompose();
        return;
    }
    const clock_t start = std::clock();
    DecompositionStats stats;
    if(refine)remaining_units_=*pending;
    stats.cpu_tick_us = 1e6 / CLOCKS_PER_SEC;
    p_sets.clear();
    unsigned n = SDS_mat.get_dim();
    CompleteMatchingWorkspace work(n);
    uint64_t frequency_sum = 0;
    uint maximum_sum = SDS_mat.sdsm_info.max_row_col_sum;
    while (frequency_sum < maximum_sum) {
        const clock_t step_start = std::clock();
        DecompositionStepStats step;
        // 每轮重新读取当前残量。S0 不需要阈值/邻接表；S1-S3 需要正边邻接
        // 和升序阈值表，所以 need_thresholds=true。
        work.collect(SDS_mat, options.strategy != DecompositionStrategy::Kuhn);
        step.degree_before = work.maximum_degree;
        vector<int> selected;
        if (options.strategy == DecompositionStrategy::Kuhn) {
            // S0：原生邻接表更新和 unordered 遍历逐字保留；这里只在外侧观测，
            // 把 matching 中 dim..2*dim-1 的列编号转换成 selected 的列下标。
            update_edges();
            if (hungarian() != n)
                throw runtime_error("native matching is not perfect");
            selected.resize(n);
            for (unsigned s = 0; s < n; ++s)
                selected[s] = hungarian_info.matching[s] - n;
        } else {
            // S1 是 S2/S3 的共同基准：先求最大可行瓶颈，并保留该阈值下的
            // 一个可行完美匹配。若 S3 的所有受限尝试都失败，会直接使用它。
            uint bottleneck = maximum_bottleneck_matching(work, selected, step);
            step.selected_threshold = bottleneck;
            if(refine)
                match_resource_priced(*topology,bottleneck,selected,
                    stats.decomposition_steps==options.resource_skip_step?0:options.resource_rounds,stats,step);
            if(options.resource_cycle)
                match_resource_cycles(*topology,bottleneck,selected,options.resource_cycle,stats,step);
            if (options.strategy == DecompositionStrategy::MaximumElimination) {
                // S2：候选阈值只取到 bottleneck 为止；每个阈值做一次真正的
                // 0/1 费用 Hungarian。目标先比等阈值边数（消边数），再比阈值；
                // 等阈值边数为 0 的候选只记录、不选。
                step.sampled_thresholds = sampled_complete_thresholds(work.thresholds, bottleneck,
                                                                      options.threshold_budget);
                unsigned best_eliminated = 0;
                uint best_q = 0;
                vector<int> best;
                for (uint threshold : step.sampled_thresholds) {
                    vector<int> trial;
                    unsigned eliminated = 0;
                    ++step.weighted_calls;
                    if (!maximum_elimination_assignment(work, threshold, trial, eliminated))
                        throw runtime_error("feasible threshold lost weighted assignment");
                    step.sampled_eliminations.push_back(eliminated);
                    if (eliminated && (eliminated > best_eliminated ||
                                       (eliminated == best_eliminated && threshold > best_q))) {
                        best_eliminated = eliminated;
                        best_q = threshold;
                        best = trial;
                    }
                }
                if (best.empty())
                    throw runtime_error("weighted candidates have no positive elimination");
                selected.swap(best);
                step.selected_threshold = best_q;
            } else if (options.strategy == DecompositionStrategy::ResidualDegree) {
                // S3：从高阈值向低阈值试。受限匹配要求“最大度行或列”关联的
                // 选中边恰好等于当前阈值；成功时这些端点各消掉一条边，最大度
                // 恰好下降 1。若整个池子都失败，标记 fallback，继续用 S1 匹配。
                step.sampled_thresholds = sampled_complete_thresholds(work.thresholds, bottleneck,
                                                                      options.threshold_budget);
                reverse(step.sampled_thresholds.begin(), step.sampled_thresholds.end());
                step.degree_fallback = true;
                for (uint threshold : step.sampled_thresholds) {
                    vector<int> trial;
                    ++step.threshold_probes;
                    if (complete_threshold_matching(work, threshold, true, trial)) {
                        selected.swap(trial);
                        step.selected_threshold = threshold;
                        step.degree_fallback = false;
                        break;
                    }
                }
            }
            // 把非原生策略的结果写回统一的 hungarian_info.matching，供下面的
            // update_permutation_sets() 使用；列顶点编号是 n + 目的列。
            for (unsigned s = 0; s < n; ++s) {
                hungarian_info.matching[s] = n + selected[s];
                hungarian_info.matching[n + selected[s]] = s;
            }
        }
        // 统一校验置换完整性，并从选中匹配重算实际最小残量 actual。
        // 注意 actual 可能小于 S2/S3 的候选阈值只在实现 bug 时发生；正常路径下
        // 所有选中边都 >= 阈值，且至少一条等于阈值。
        uint actual = UINT32_MAX;
        vector<unsigned char> used(n, 0);
        for (unsigned s = 0; s < n; ++s) {
            if (selected[s] < 0 || unsigned(selected[s]) >= n || used[selected[s]])
                throw runtime_error("decomposition produced incomplete matching");
            used[selected[s]] = 1;
            actual = min(actual, work.values[size_t(s) * n + selected[s]]);
        }
        if (!actual)
            throw runtime_error("decomposition made no positive progress");
        step.q_scaled = actual;
        step.q = original_units(actual);
        if (options.strategy == DecompositionStrategy::Kuhn)
            step.selected_threshold = actual;
        if(refine) {
            if(actual!=step.selected_threshold)throw runtime_error("resource pricing changed bottleneck");
            consume_pending(selected,actual);
        }
        // 只对值为 actual 的匹配边扣度；这些边在 update_permutation_sets() 后
        // 会变成 0，并从下一轮的正边支持图中消失。
        for (unsigned s = 0; s < n; ++s)
            if (work.values[size_t(s) * n + selected[s]] == actual) {
                ++step.eliminated;
                --work.row_degree[s];
                --work.col_degree[selected[s]];
            }
        for (unsigned s = 0; s < n; ++s)
            step.degree_after = max(step.degree_after, max(work.row_degree[s], work.col_degree[s]));
        // 与原生共用唯一扣减入口：update_permutation_sets() 会重新读取当前
        // matching、取匹配边最小残量、从 SDS_mat 扣除并写入 p_sets。
        // 这里不沿用上一候选 q，也不删除任何匹配端点；extracted 必须等于
        // 前面用 work.values 算出的 actual，否则说明匹配和矩阵已经不同步。
        uint extracted = update_permutation_sets();
        if (extracted != actual || !step.eliminated)
            throw runtime_error("decomposition extraction mismatch");
        frequency_sum += actual;
        if (frequency_sum > maximum_sum)
            throw runtime_error("decomposition exceeded padded row sum");
        step.cpu_us = decomposition_cpu_elapsed(step_start);
        ++stats.decomposition_steps;
        stats.threshold_probes += step.threshold_probes;
        stats.weighted_calls += step.weighted_calls;
        if (output)
            stats.steps.push_back(std::move(step));
    }
    stats.cpu_us = decomposition_cpu_elapsed(start);
    if(refine)
        for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d)
            for(unsigned u=0;u<gpu_n;++u)
                if(remaining_units_[(s*n+d)*gpu_n+u])throw runtime_error("resource pricing left real demand");
    if (output)
        *output = std::move(stats);
}

void FastAll2All::match_resource_priced(const native_topology::Topology& top,uint q,
                                      vector<int>& selected,unsigned rounds,DecompositionStats& stats,
                                      DecompositionStepStats& trace) {
    // Linear assignment is a surrogate for the nonseparable shared-resource
    // maximum. Forbidden edges are never relaxed; no finite penalty permits them.
    unsigned n=SDS_mat.get_dim(),resources=top.resource_count();
    prepare_topology_stage(top,q);
    vector<native_topology::GpuTransfer> flows;
    build_transfers(selected,q,flows);
    auto current=top.evaluate_gpu(flows);
    double best=current.total_us;
    trace.resource_seed=selected;
    trace.resource_seed_us=best;
    vector<double> price(resources,0),cost(size_t(n)*n);
    const double infinity=numeric_limits<double>::infinity();
    for(unsigned attempt=0;attempt<rounds;++attempt) {
        double peak=0;
        for(unsigned e=0;e<resources;++e)
            peak=max(peak,.008*current.resource_bytes[e]/top.resource(e).bandwidth_gbps);
        if(peak==0)break;
        for(unsigned e=0;e<resources;++e)
            price[e]+=(.008*current.resource_bytes[e]/top.resource(e).bandwidth_gbps)/peak;
        fill(cost.begin(),cost.end(),0);
        for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)
            for(const auto& edge:hungarian_info.edge_loads[s*n+d])
                cost[s*n+d]+=price[edge.first]*.008*edge.second/top.resource(edge.first).bandwidth_gbps;
        vector<double> row_potential(n+1),col_potential(n+1);
        vector<unsigned> assigned(n+1),previous(n+1);
        for(unsigned row=1;row<=n;++row) {
            assigned[0]=row;unsigned col=0;
            vector<double> slack(n+1,infinity);vector<unsigned char> used(n+1,0);
            do {
                used[col]=1;unsigned active=assigned[col],next=0;double delta=infinity;
                for(unsigned d=1;d<=n;++d)if(!used[d]) {
                    if(SDS_mat.get(active-1,d-1)>=q) {
                        double reduced=cost[(active-1)*n+d-1]-row_potential[active]-col_potential[d];
                        if(reduced<slack[d]){slack[d]=reduced;previous[d]=col;}
                    }
                    if(slack[d]<delta){delta=slack[d];next=d;}
                }
                if(!isfinite(delta))throw runtime_error("resource assignment lost feasible matching");
                for(unsigned d=0;d<=n;++d) {
                    if(used[d]){row_potential[assigned[d]]+=delta;col_potential[d]-=delta;}
                    else if(isfinite(slack[d]))slack[d]-=delta;
                }
                col=next;
            } while(assigned[col]);
            do {unsigned prior=previous[col];assigned[col]=assigned[prior];col=prior;}while(col);
        }
        vector<int> trial(n,-1);
        for(unsigned d=1;d<=n;++d) {
            unsigned s=assigned[d]-1;
            if(s>=n||trial[s]!=-1||SDS_mat.get(s,d-1)<q)
                throw runtime_error("resource assignment selected illegal edge");
            trial[s]=d-1;
        }
        ++stats.resource_proposals;
        build_transfers(trial,q,flows);current=top.evaluate_gpu(flows);
        double tolerance=64*numeric_limits<double>::epsilon()*max(1.,max(best,current.total_us));
        if(current.total_us<best-tolerance) {
            best=current.total_us;selected=trial;++stats.resource_accepts;
        }
    }
    trace.resource_chosen_us=best;
}

void FastAll2All::match_resource_cycles(const native_topology::Topology& top,uint q,
                                      vector<int>& selected,unsigned length,DecompositionStats& stats,
                                      DecompositionStepStats& trace) {
    // A cycle rotates existing destinations, preserving one send/receive per
    // endpoint. Only R>=q edges are allowed. Sparse deltas score the SAME real
    // stage objective as evaluate_gpu(), including removing the old maxima.
    const unsigned n=selected.size(),r=top.resource_count();auto& h=hungarian_info;
    vector<double> work(r),delta(r),added(r);vector<uint64_t> stamp(r,0);uint64_t generation=0;
    vector<unsigned> resource_order(r),source_order(n),touched;
    // Convert each sparse contribution once, preserving canonical operation
    // order: a reciprocal 0.008/B may overflow even when 0.008*bytes/B is finite.
    auto edge_work=h.edge_loads;
    for(auto& edge:edge_work)for(auto& v:edge)v.second=.008*v.second/top.resource(v.first).bandwidth_gbps;
    for(unsigned e=0;e<r;++e)resource_order[e]=e;
    for(unsigned s=0;s<n;++s)source_order[s]=s;
    unsigned base=1;while(base<n)base*=2;const size_t stride=2*size_t(base);
    vector<double> tree(size_t(r)*stride);
    vector<native_topology::GpuTransfer> flows;build_transfers(selected,q,flows);
    double current=top.evaluate_gpu(flows).total_us;
    trace.cycle_costs_us.push_back(current);
    const double eps=64*numeric_limits<double>::epsilon();
    for(;;) {
        ++stats.cycle_passes;
        if(current==0)break; // zero is already a certified minimum
        fill(tree.begin(),tree.end(),0);
        for(unsigned s=0;s<n;++s)for(const auto& v:edge_work[s*n+selected[s]])
            tree[v.first*stride+base+s]+=v.second;
        for(unsigned e=0;e<r;++e) {
            size_t offset=e*stride;
            for(unsigned i=base-1;i>0;--i)tree[offset+i]=tree[offset+2*i]+tree[offset+2*i+1];
            work[e]=tree[offset+1];
        }
        auto range_sum=[&](unsigned e,unsigned begin,unsigned end) {
            double sum=0;size_t offset=e*stride;
            for(unsigned a=begin+base,b=end+base;a<b;a/=2,b/=2) {
                if(a&1)sum+=tree[offset+a++];if(b&1)sum+=tree[offset+--b];
            }
            return sum;
        };
        sort(resource_order.begin(),resource_order.end(),[&](unsigned a,unsigned b){return work[a]>work[b];});
        sort(source_order.begin(),source_order.end(),[&](unsigned a,unsigned b){
            return h.edge_alpha_us[a*n+selected[a]]>h.edge_alpha_us[b*n+selected[b]];
        });
        array<unsigned,3> best_rows{{0,0,0}};array<int,3> best_dest{{0,0,0}};
        unsigned best_count=0;double best=current;
        auto inspect=[&](array<unsigned,3> rows,unsigned count,unsigned shift) {
            array<int,3> dest{{0,0,0}};
            for(unsigned i=0;i<count;++i) {
                dest[i]=selected[rows[(i+shift)%count]];
                if(SDS_mat.get(rows[i],dest[i])<q)return;
            }
            ++stats.cycle_evaluations;
            if(++generation==0){fill(stamp.begin(),stamp.end(),0);generation=1;}
            touched.clear();double alpha=0;
            auto add=[&](unsigned edge,double sign) {
                for(const auto& v:edge_work[edge]) {
                    unsigned e=v.first;
                    if(stamp[e]!=generation){stamp[e]=generation;delta[e]=added[e]=0;touched.push_back(e);}
                    delta[e]+=sign*v.second;
                    if(sign>0)added[e]+=v.second;
                }
            };
            for(unsigned i=0;i<count;++i) {
                unsigned s=rows[i];add(s*n+selected[s],-1);add(s*n+dest[i],1);
                alpha=max(alpha,h.edge_alpha_us[s*n+dest[i]]);
            }
            // At most three source maxima are removed: the first unaffected
            // source in descending order supplies the unchanged maximum.
            for(unsigned s:source_order) {
                bool changed=false;for(unsigned i=0;i<count;++i)changed|=s==rows[i];
                if(!changed){alpha=max(alpha,h.edge_alpha_us[s*n+selected[s]]);break;}
            }
            double serial=0;
            for(unsigned e:resource_order)if(stamp[e]!=generation){serial=work[e];break;}
            for(unsigned e:touched) {
                double next=work[e]+delta[e];
                if(next<.5*work[e]) {
                    // Avoid cancellation when a cycle removes most/all of a
                    // resource's work. Sum only positive unchanged intervals
                    // and newly added work; rows are already in sorted order.
                    next=added[e];unsigned first=0;
                    for(unsigned i=0;i<count;++i){next+=range_sum(e,first,rows[i]);first=rows[i]+1;}
                    next+=range_sum(e,first,n);
                }
                if(next < -eps*max(1.,work[e]))throw runtime_error("negative cycle resource work");
                serial=max(serial,max(0.,next));
            }
            double cost=alpha+serial;
            if(cost<best-eps*max(1.,max(best,cost))) {
                best=cost;best_rows=rows;best_dest=dest;best_count=count;
            }
        };
        for(unsigned a=0;a<n;++a)for(unsigned b=a+1;b<n;++b)inspect({{a,b,0}},2,1);
        if(length==3)for(unsigned a=0;a<n;++a)for(unsigned b=a+1;b<n;++b)for(unsigned c=b+1;c<n;++c) {
            inspect({{a,b,c}},3,1);inspect({{a,b,c}},3,2);
        }
        if(!best_count)break;
        vector<int> best_matching=selected;
        for(unsigned i=0;i<best_count;++i)best_matching[best_rows[i]]=best_dest[i];
        // Canonical evaluation is authoritative, not the incremental estimate.
        build_transfers(best_matching,q,flows);double exact=top.evaluate_gpu(flows).total_us;
        if(abs(exact-best)>max(8.,double(n)*gpu_n)*eps*max(1.,max(exact,best))||
           !(exact<current-eps*max(1.,max(exact,current))))
            throw runtime_error("cycle estimate or strict descent mismatch");
        selected.swap(best_matching);current=exact;++stats.cycle_accepts;
        trace.cycle_costs_us.push_back(current);
    }
    trace.resource_chosen_us=current;
}

// 正式拓扑入口：只改变每轮匹配选择，矩阵补齐、扣除和结果仍由原生流程完成。
void FastAll2All::decompose(ScheduleMode mode,const native_topology::Topology& top,
                          const vector<uint64_t>& pending) {
    uint n=SDS_mat.get_dim();
    if((mode!=ScheduleMode::Native&&mode!=ScheduleMode::BandwidthAware&&mode!=ScheduleMode::CompletionTimeAware)
       ||top.n!=n||top.g!=gpu_n||pending.size()!=size_t(n)*n*gpu_n)
        throw invalid_argument("schedule dimensions or mode");
    if(!isfinite(MB_per_unit)||MB_per_unit<=0)throw invalid_argument("invalid data unit");
    // Validate active physical channels before entering native dispatch.
    for(uint s=0;s<n;++s)for(uint d=0;d<n;++d)for(uint u=0;u<gpu_n;++u) {
        auto v=pending[(s*n+d)*gpu_n+u];
        if(v>UINT32_MAX)throw invalid_argument("pending unit range");
        if(s!=d&&v) {
            uint src=s*gpu_n+u,dst=d*gpu_n+u;
            const auto& path=top.gpu_path(src,dst);
            if(path.resource_ids.empty())throw invalid_argument("missing active GPU route");
            double bytes=double(v)*MB_per_unit*1e6;
            double cost=path.propagation_us+.008*bytes/top.path_min_bandwidth_gbps(src,dst);
            if(!isfinite(bytes)||!isfinite(cost))throw overflow_error("flow cost overflow");
        }
    }
    remaining_units_=pending;
    uint stages=0;
    decompose_with([&](Matrix&,hungarian_info_t&) {
        if(++stages>n*n)throw runtime_error("finite support bound");
        adjust_matching(mode,top);
    });
    for(uint s=0;s<n;++s)for(uint d=0;d<n;++d)if(s!=d)
        for(uint u=0;u<gpu_n;++u)if(remaining_units_[(s*n+d)*gpu_n+u])
            throw runtime_error("undelivered real inter-server units");
}
uint FastAll2All::original_units(uint q)const {
    uint64_t v=uint64_t(q)*(is_scaled?scaling_factor:1);
    if(v>UINT32_MAX)throw overflow_error("stage coefficient overflow");
    return uint(v);
}
void FastAll2All::build_transfers(const vector<int>& m,uint q,vector<native_topology::GpuTransfer>& out)const {
    uint n=m.size(),units=original_units(q);out.clear();
    for(uint s=0;s<n;++s)if(s!=uint(m[s]))for(uint u=0;u<gpu_n;++u) {
        uint64_t x=min<uint64_t>(units,remaining_units_[(s*n+m[s])*gpu_n+u]);
        if(x)out.push_back({s*gpu_n+u,uint(m[s])*gpu_n+u,double(x)*MB_per_unit*1e6});
    }
}
void FastAll2All::consume_pending(const vector<int>& m,uint q) {
    uint n=m.size(),units=original_units(q);
    for(uint s=0;s<n;++s)if(s!=uint(m[s]))for(uint u=0;u<gpu_n;++u) {
        auto& v=remaining_units_[(s*n+m[s])*gpu_n+u];v-=min<uint64_t>(v,units);
    }
}
// Cache this stage's sparse resource contributions once per server pair.
void FastAll2All::prepare_topology_stage(const native_topology::Topology& top,uint q) {
    auto& h=hungarian_info;uint n=SDS_mat.get_dim(),units=original_units(q),r=top.resource_count();
    h.edge_loads.resize(n*n);h.edge_cost_us.assign(n*n,0);h.edge_alpha_us.assign(n*n,0);
    h.resource_load_bytes.assign(r,0);
    vector<uint> touched;
    for(uint s=0;s<n;++s)for(uint d=0;d<n;++d) {
        auto& edge=h.edge_loads[s*n+d];edge.clear();
        if(s==d||SDS_mat.get(s,d)<q)continue;
        double alpha=0;
        for(uint u=0;u<gpu_n;++u) {
            auto x=min<uint64_t>(units,remaining_units_[(s*n+d)*gpu_n+u]);if(!x)continue;
            double bytes=double(x)*MB_per_unit*1e6;
            const auto& p=top.gpu_path(s*gpu_n+u,d*gpu_n+u);
            alpha=max(alpha,p.propagation_us);
            for(uint e:p.resource_ids) {
                if(!h.resource_load_bytes[e])touched.push_back(e);
                h.resource_load_bytes[e]+=bytes;
            }
        }
        double serial=0;
        for(uint e:touched) {
            double bytes=h.resource_load_bytes[e];edge.push_back({e,bytes});
            serial=max(serial,.008*bytes/top.resource(e).bandwidth_gbps);h.resource_load_bytes[e]=0;
        }
        touched.clear();h.edge_cost_us[s*n+d]=alpha+serial;h.edge_alpha_us[s*n+d]=alpha;
        if(!isfinite(h.edge_cost_us[s*n+d]))throw overflow_error("edge cost overflow");
    }
}
bool FastAll2All::match_bandwidth(const native_topology::Topology& top,uint q,vector<int>& m) {
    // Incremental augmenting matching. Candidate ordering uses the partial
    // shared-resource peak, then its squared-load increment; ties keep native order.
    auto& h=hungarian_info;uint n=SDS_mat.get_dim(),r=top.resource_count();
    m.assign(n,-1);h.inverse_matching.assign(n,-1);h.candidate_visit.resize(n);
    h.reduced_loads.resize(n);h.candidates.resize(n);
    for(uint s=0;s<n;++s){h.reduced_loads[s].resize(r);h.candidates[s].reserve(n);}
    fill(h.resource_load_bytes.begin(),h.resource_load_bytes.end(),0);
    function<bool(uint)> dfs=[&](uint s) {
        auto& loads=h.reduced_loads[s];copy(h.resource_load_bytes.begin(),h.resource_load_bytes.end(),loads.begin());
        if(m[s]>=0)for(const auto& v:h.edge_loads[s*n+m[s]])loads[v.first]-=v.second;
        double base=0;for(uint e=0;e<r;++e)base=max(base,loads[e]/top.resource(e).bandwidth_gbps);
        auto& candidates=h.candidates[s];candidates.clear();
        for(uint col:h.row_to_col[s]) {
            uint d=col-n;if(h.candidate_visit[d]||SDS_mat.get(s,d)<q)continue;
            double peak=base,square=0;
            for(const auto& v:h.edge_loads[s*n+d]) {
                double bw=top.resource(v.first).bandwidth_gbps;
                double old=loads[v.first]/bw,next=(loads[v.first]+v.second)/bw;
                peak=max(peak,next);square+=(next-old)*(next+old);
            }
            if(!isfinite(peak)||!isfinite(square))throw overflow_error("resource score overflow");
            candidates.push_back({d,uint(candidates.size()),peak,square});
        }
        sort(candidates.begin(),candidates.end(),[](const hungarian_info_t::Candidate& a,const hungarian_info_t::Candidate& b) {
            return a.peak!=b.peak?a.peak<b.peak:a.square!=b.square?a.square<b.square:a.ordinal<b.ordinal;
        });
        for(const auto& c:candidates) {
            uint d=c.dest;if(h.candidate_visit[d])continue;h.candidate_visit[d]=true;
            if(h.inverse_matching[d]<0||dfs(h.inverse_matching[d])) {
                if(m[s]>=0)for(const auto& v:h.edge_loads[s*n+m[s]])h.resource_load_bytes[v.first]-=v.second;
                for(const auto& v:h.edge_loads[s*n+d])h.resource_load_bytes[v.first]+=v.second;
                m[s]=d;h.inverse_matching[d]=s;return true;
            }
        }
        return false;
    };
    for(uint s=0;s<n;++s){fill(h.candidate_visit.begin(),h.candidate_visit.end(),0);if(!dfs(s))return false;}
    return true;
}
bool FastAll2All::match_completion_time(const native_topology::Topology&,uint q,vector<int>& m) {
    // A threshold admits an edge only if its stand-alone pair cost fits.
    // Binary search the smallest threshold still permitting a perfect matching.
    auto& h=hungarian_info;uint n=SDS_mat.get_dim();auto& costs=h.cost_thresholds_us;costs.clear();
    for(uint s=0;s<n;++s)for(uint col:h.row_to_col[s])if(SDS_mat.get(s,col-n)>=q)costs.push_back(h.edge_cost_us[s*n+col-n]);
    sort(costs.begin(),costs.end());costs.erase(unique(costs.begin(),costs.end()),costs.end());
    auto feasible=[&](double bound,vector<int>& trial) {
        trial.assign(n,-1);h.inverse_matching.assign(n,-1);h.candidate_visit.resize(n);
        function<bool(uint)> dfs=[&](uint s) {
            for(uint col:h.row_to_col[s]) {
                uint d=col-n;
                if(SDS_mat.get(s,d)<q||h.edge_cost_us[s*n+d]>bound||h.candidate_visit[d])continue;
                h.candidate_visit[d]=true;
                if(h.inverse_matching[d]<0||dfs(h.inverse_matching[d])){trial[s]=d;h.inverse_matching[d]=s;return true;}
            }
            return false;
        };
        for(uint s=0;s<n;++s){fill(h.candidate_visit.begin(),h.candidate_visit.end(),0);if(!dfs(s))return false;}
        return true;
    };
    int lo=0,hi=int(costs.size())-1;vector<int> trial;
    while(lo<=hi) {
        int mid=(lo+hi)/2;
        if(feasible(costs[mid],trial)){m=trial;hi=mid-1;}else lo=mid+1;
    }
    return !m.empty();
}
bool FastAll2All::accept_candidate(ScheduleMode mode,const native_topology::Topology& top,uint q) {
    auto& h=hungarian_info;uint n=SDS_mat.get_dim();uint minimum=UINT32_MAX;
    for(uint s=0;s<n;++s)minimum=min(minimum,SDS_mat.get(s,h.candidate_matching[s]));
    if(minimum!=q)return false;
    // Exact volume guard stays in native integer units, never rounded bytes.
    auto volume=[&](const vector<int>& m) {
        uint64_t sum=0;uint units=original_units(q);
        for(uint s=0;s<n;++s)if(s!=uint(m[s]))for(uint u=0;u<gpu_n;++u)
            sum+=min<uint64_t>(units,remaining_units_[(s*n+m[s])*gpu_n+u]);
        return sum;
    };
    if(volume(h.candidate_matching)<volume(h.seed_matching))return false;
    vector<native_topology::GpuTransfer> seed,trial;
    build_transfers(h.seed_matching,q,seed);build_transfers(h.candidate_matching,q,trial);
    const auto seed_cost=top.evaluate_gpu(seed),trial_cost=top.evaluate_gpu(trial);
    // 带宽模式仍只优化serial；完成时间模式的阈值仅生成候选，接受时计入真实共享负载。
    double before=mode==ScheduleMode::BandwidthAware?seed_cost.serial_us:seed_cost.total_us;
    double after=mode==ScheduleMode::BandwidthAware?trial_cost.serial_us:trial_cost.total_us;
    return after<before-1e-12*max(1.,abs(before));
}
void FastAll2All::adjust_matching(ScheduleMode mode,const native_topology::Topology& top) {
    auto& h=hungarian_info;uint n=SDS_mat.get_dim(),q=UINT32_MAX;h.seed_matching.resize(n);
    for(uint s=0;s<n;++s){h.seed_matching[s]=h.matching[s]-n;q=min(q,SDS_mat.get(s,h.seed_matching[s]));}
    if(mode!=ScheduleMode::Native) {
        prepare_topology_stage(top,q);h.candidate_matching.clear();
        bool ok=mode==ScheduleMode::BandwidthAware?match_bandwidth(top,q,h.candidate_matching)
                                                 :match_completion_time(top,q,h.candidate_matching);
        if(!ok)throw runtime_error("topology matching lost feasibility");
        if(accept_candidate(mode,top,q))h.seed_matching.swap(h.candidate_matching);
        for(uint s=0;s<n;++s){h.matching[s]=n+h.seed_matching[s];h.matching[n+h.seed_matching[s]]=s;}
    }
    consume_pending(h.seed_matching,q);
}

// Birkhoff 分解主循环。
// 目标：把 SDS_mat 表示成 ∑ frequency_k × P_k，其中 P_k 是置换矩阵。
// 每一轮：
//   - update_edges() 按当前残量矩阵刷新二分图的边；
//   - hungarian() 求一个完美匹配（每个行顶点配一个仍有流量的列顶点）；
//   - update_permutation_sets() 取匹配边上的最小残量作为本阶段频率，
//     从这些边上扣掉这个量，并把该置换记入 p_sets。
// 累计频率达到 max_sum 时，矩阵中的所有流量都被"抽取"完，分解结束。
// 因为每一轮至少消耗 1 个单位，所以阶段数不超过 max_sum。
// adjust 是给研究用的扩展点：可以在每轮匹配后修改矩阵或匹配关系。
void FastAll2All::decompose_with(const std::function<void(Matrix&, hungarian_info_t&)>& adjust){
    if (!SDS_mat.valid_sdsm()){
        LOG("error when doing decomposition, must convert matrix to sdsm first!");
        return;
    }
    p_sets.clear(); // store results
    uint freq_sum = 0, max_sum = SDS_mat.sdsm_info.max_row_col_sum;
    while(freq_sum < max_sum){
        update_edges();       // 根据残量矩阵重建"有流量的边"
        hungarian();          // 求一个最大匹配（双随机矩阵保证存在完美匹配）
        if (adjust) adjust(SDS_mat, hungarian_info);
        freq_sum += update_permutation_sets();  // 记录一个置换阶段
    }
}


// Kuhn 算法求二分图最大匹配。
// 依次尝试为每个尚未匹配的行顶点寻找增广路；找到就匹配数 +1。
// 对于双随机矩阵，每一轮都能得到完美匹配（匹配数 = dim）。
uint FastAll2All::hungarian(){
    uint match_num = 0;
    uint dim = SDS_mat.get_dim();
    std::fill(hungarian_info.matching.begin(), hungarian_info.matching.end(), -1);
    for (uint u = 0; u < dim; u++){
        if (hungarian_info.matching[u] == -1){
            std::fill(hungarian_info.visit.begin(), hungarian_info.visit.end(), false);
            if(hungarian_dfs(u))
                match_num ++;
        }
    }
    return match_num;
}

// 从行顶点 u 出发找一条增广路。
// 列顶点的编号是 dim..2*dim-1，matching 在两个方向都存了配对关系，
// 所以可以用 matching[col] 跳到与它匹配的行，再继续递归。
// 所以 hungarian 算法仅实现了不同服务器之间的匹配，而没有决定它们之间到底传输多少数据？更新数据应该作用在 SDS 矩阵上
bool FastAll2All::hungarian_dfs(uint u){
    // 这个 for 循环应该也可以换成 int 这么写是为了方便直接传 hash set
    for (unordered_set<uint>::iterator col_idx = hungarian_info.row_to_col[u].begin(); col_idx != hungarian_info.row_to_col[u].end(); col_idx ++){
        if (!hungarian_info.visit[*col_idx]){       // 那么到这里的 *col_idx 代表什么？这是什么语法了呢？其实应该还是看这个位置的 visit 是否为 true
            hungarian_info.visit[*col_idx] = true;
            if (hungarian_info.matching[*col_idx] == -1 || hungarian_dfs(hungarian_info.matching[*col_idx])){       // ==-1 代表尚未匹配，
                // 列顶点空闲，或者它的当前匹配行可以改配到别的列 -> 重新配对
                // 当前列的匹配行可以改配到其它列则将它改掉，把这个列分配给当前行，但这么写总觉得会浪费很多额外的时间？
                hungarian_info.matching[*col_idx] = u;
                hungarian_info.matching[u] = *col_idx;
                return true;
            }
        }
    }
    return false;
}

// 根据当前 SDS_mat 的残量刷新邻接表：>0 才有边，==0 的边要删掉。
// 行顶点 i 只连到非零元素所在的列，这样匈牙利算法只会沿"还有流量"的边匹配。
// 保存的结构是一个有向无环图而不是矩阵，所以是将无关的边移出去，而不是行或列
void FastAll2All::update_edges(){
    uint dim = SDS_mat.get_dim();
    // row vertices id: 0 - dim-1, col vertices id: dim - 2*dim-1
    for (uint i = 0; i < dim; i++){
        for (uint j = 0; j < dim; j++){
            uint col_id = j + dim;          // 这一步是为什么？ 为了让行和列的顶点不冲突，因为 hungarian.matching 是一个一维数组，而 hungarian 是一个二向图
            if (SDS_mat.get(i, j) > 0 ){        // get 函数 return mat[i][j]
                hungarian_info.row_to_col[i].insert(col_id);
            }else if (SDS_mat.get(i, j) == 0){
                hungarian_info.row_to_col[i].erase(col_id);
            }
        }
    }
}

// 把当前匹配转换成一个 PermutationSet，并从矩阵中扣掉该阶段的流量。
// 阶段频率 = 匹配边上的最小残量（瓶颈）；所有匹配边减去它之后，
// 至少有一条边变成 0，下一轮邻接表就会少一条边，循环单调推进。
//
// 调用前提：decompose_with() 刚刚调用过 hungarian()，得到的是完美匹配，
// 所以每个行顶点 i 的 matching[i] 都是有效列顶点编号（dim..2*dim-1），不会为 -1。
//
// 函数分三步：
//   1. 第一遍扫描匹配边 (i, matching[i]-dim)：取所有边残量的最小值作为本阶段
//      频率 min_freq，同时把置换关系 i -> col_id 记入 r.mp；
//   2. 第二遍再扫描一遍匹配边，把每条边的残量减去 min_freq，
//      表示这些流量已经在本阶段发送完毕；
//   3. 把 r（置换 + 频率）追加到分解结果 p_sets，并返回 min_freq，
//      供 decompose_with() 累加，判断总流量是否已分解完。
uint FastAll2All::update_permutation_sets(){
    uint dim = SDS_mat.get_dim();
    // matching 中列顶点的编号是 j+dim，减掉 dim 才是矩阵的列下标 j（0..dim-1）。
    // 构造一个占位置换：frequency 先给 1（下面 set_freq 会覆盖），
    // scaling_factor 用于 get_freq() 把"缩放后单位"的频率换算回原始单位
    // （未缩放时就是 1），dim 决定打印置换矩阵时的阶数。
    PermutationSet r(1, is_scaled ? scaling_factor : 1, dim);
    // 用第一条匹配边的残量初始化瓶颈频率
    uint min_freq = SDS_mat.get(0, hungarian_info.matching[0] - dim);
    // 第一遍：遍历所有匹配边，求瓶颈流量 min_freq，并记录置换关系。
    // r.mp 是 map，按行号有序，后面 verify_decomposition() 直接用 mp[i] 取值。
    for(uint i = 0; i < dim; i++){
        uint col_id = hungarian_info.matching[i] - dim;
        min_freq = MIN(SDS_mat.get(i, col_id), min_freq);
        r.mp.insert(make_pair(i, col_id));
    }

    // 第二遍：每条匹配边都扣掉 min_freq（即本阶段每块发送 GPU 的发送量）。
    // 因为 min_freq 是所有匹配边残量的最小值，扣减后不会出现负数；
    // 且至少有一条边恰好变成 0，下一轮 update_edges() 会把它从邻接表删掉，
    // 保证分解循环严格向前推进（阶段数不超过 max_row_col_sum）。
    // 由于是完美匹配，每行每列各被扣一次，残量矩阵的双随机性得以保持。
    for(uint i = 0; i < dim; i++){
        uint col_id = hungarian_info.matching[i] - dim;
        SDS_mat.subtract(min_freq, i, col_id);
    }

    // 写入本阶段频率（缩放后单位；对外通过 get_freq() 乘回 scaling_factor）。
    // p_sets 的先后顺序就是各阶段的执行顺序，后续 GlobalScheduler 会按此展开传输。
    r.set_freq(min_freq);
    p_sets.push_back(r);
    // 返回本阶段消耗的流量，decompose_with() 把它累加进 freq_sum
    return min_freq;
}

void FastAll2All::print_decomposition(){
    for (vector<PermutationSet>::iterator ps = p_sets.begin(); ps != p_sets.end(); ps++){
        ps -> print_permutation_matrix();
    }
}

// 正确性检查：把 p_sets 里的置换按频率重新累加成一个矩阵，
// 再和当前 SDS_mat 比较。因为 PermutationSet 保存的是原始单位，
// 累加时要先除以 scaling_factor 换回缩放后的单位。
bool FastAll2All::verify_decomposition(){
    uint dim = SDS_mat.get_dim();
    Matrix r(dim);
    for (vector<PermutationSet>::iterator ps = p_sets.begin(); ps != p_sets.end(); ps++){
        for (uint i = 0; i < dim; i++){
            uint non_empty_col_id = ps -> mp[i];
            r.add(ps->get_freq()/scaling_factor, i, non_empty_col_id);
        }
    }
    r.get_sdsm_info();
    to_scaled_doubly_stochastic_matrix();
    return SDS_mat.equal_to(&r);
}

void PermutationSet::print_permutation_matrix(){
    cout << "Permutation Matrix:" << endl;
    for(uint i = 0; i < dim; i ++){
        uint non_empty_col_id = mp[i];
        for (uint j = 0; j < dim; j++){
            cout << setw(10);
            if (non_empty_col_id == j){
                cout << frequency * scaling_factor;
            }else{
                cout << "0";
            }
        }
        cout << endl;
     }
}
// 试算"把 mat 整体除以 factor"的结果：只写入外部 buffer，不改成员。
// 返回缩放后的最大行/列和，供判定函数决定这个 factor 是否合适。
// 注意每个元素是向上取整（ceil），所以缩放只会多算流量、不会少算。
struct scale_info_t FastAll2All::scale_matrix(uint factor, uint* buffer){
    uint dim = mat.get_dim();
    for (uint i = 0; i < dim; i ++){
        for (uint j = 0; j < dim; j++){
            uint idx = i * dim + j;
            buffer[idx] = (mat.get(i, j) + factor - 1) / factor;    // ceiling division 
        }
    }
    // collect information after scaling
    uint max_sum = 0;
    for (uint i = 0; i < dim; i++){
        uint row_sum = 0, col_sum = 0;
        for (uint j = 0; j < dim; j++){
            row_sum += buffer[i * dim + j];
            col_sum += buffer[j * dim + i];
        }
        max_sum = MAX(max_sum, MAX(row_sum, col_sum));
    }
    struct scale_info_t r = {.max_sum = max_sum, .factor=factor};
    return r;
}

// 判定策略 1：只要缩放后的最大行/列和不超过固定阈值（MAX_SUM_LIMIT）就接受。
// 这能硬性限制置换阶段数，但完全不考虑链路快慢。
bool limit_max_sum(uint server_n, uint gpu_n, struct scale_info_t s_info, struct link_info_t link, struct link_info_t intra_link, double MBpu){
    return (s_info.max_sum <= MAX_SUM_LIMIT);
}

// 判定策略 2：只接受能把最大行/列和压到 server 数以内的因子。
// 由于每个置换阶段至少消耗 1 个单位，这保证整个分解最多只有 server_n 个阶段。
bool always_scale(uint server_n, uint gpu_n, struct scale_info_t s_info, struct link_info_t inter_link, struct link_info_t intra_link, double MBpu){
    if (s_info.max_sum <= server_n){
        return true;
    }
    return false;
}

// 判定策略 3（默认）：在"精度"和"阶段开销"之间折中。
//   alpha = 固定开销的估计（按 n*n 次 server 间传输 + n*n*m 次 server 内传输计）：
//           inter.alpha * n*n + intra.alpha * n*n*m。
//   beta  = 数据传输时间的估计：
//           inter.beta * (缩放后 max_sum * factor) * MBpu，
//           max_sum*factor 近似等于缩放前的最大行/列和，所以 beta 近似与 factor 无关。
// 判定条件（对每个候选 factor）：
//   1. 若缩放后 max_sum <= server_n，直接接受；
//   2. 否则只在 alpha/beta < 0.1 时接受——固定开销不到传输时间的 10% 时，
//      再增加阶段数也无所谓，于是二分搜索会落到最小因子（通常是 1，精度最高）。
// 反过来，若固定开销不可忽略，就要求 max_sum 足够小以减少阶段数。
bool balance_alpha_beta(uint server_n, uint gpu_n, struct scale_info_t s_info, struct link_info_t inter_link, struct link_info_t intra_link, double MBpu){
    if (s_info.max_sum <= server_n){
        return true;
    }
    double alpha = inter_link.alpha * server_n * server_n + intra_link.alpha * server_n * server_n * gpu_n;
    double beta = inter_link.beta * s_info.max_sum * s_info.factor * MBpu;
    // cout << "factor: " << s_info.factor << ", alpha: " << alpha << ", beta: " << beta << " , MBpu: " << MBpu << endl;
    return alpha/beta < 0.1;
    // cost of spreadout: m*n-1 inter-transfer
    // cost of proposed algorithm: n*n inter-transfer, n*n*m intra-transfer
    // cost of proposed algorithm after making each element to one: n inter-transfer, n*m intra transfer
}

// 在 [l, r] 内按 step 搜索"满足条件的最小 factor"。
// 这里假设条件对 factor 单调：factor 越大，缩放后的 max_sum 越小、越容易满足。
// （limit_max_sum / always_scale 严格满足；balance_alpha_beta 也按此设计。）
// 因此可以二分：mid 可行就继续往左找更小的，否则往右找更大的。
// 返回 -1 表示整个区间都找不到可行 factor。
//return scaling factor that satisfies the condition, -1 means not finding a valid scaling factor
int FastAll2All::scaling_binary_search(scaling_success_condition fn, int l, int r, uint step, uint* buffer){
    if (l > r){
        return -1;
    }
    uint ele_num = (r - l) / step + 1;
    uint mid = l + ele_num / 2 * step;
    struct scale_info_t scale_result = scale_matrix(mid, buffer);
    uint dim = mat.get_dim();

    if ((*fn)(dim, gpu_n, scale_result, inter_link, intra_link, MB_per_unit)) {
        // mid satisfies the condition, try to find a smaller factor
        int ret = scaling_binary_search(fn, l, mid - step, step, buffer);
        return (ret == -1) ? mid : ret;
    }else{
        // mid does not work, try a bigger factor
        return scaling_binary_search(fn, mid + step, r, step, buffer);
    }
}


// 对外入口：选一个满足条件的缩放因子，把原矩阵复制到 S_mat 并缩放。
// 注意：二分搜索从 l=1、step=2 开始，因此只检查奇数因子；
// 如果找不到（返回 -1），保持 is_scaled=false，后续会使用未缩放的矩阵。
void FastAll2All::to_scaled_matrix(scaling_success_condition fn){
    is_scaled = false;
    uint dim = mat.get_dim();
    uint * sm = new uint[dim * dim];
    int ret = scaling_binary_search(fn, SCALING_FACTOR_MIN, SCALING_FACTOR_MAX, SCALING_FACTOR_STEP, sm);
    // cout<<"factor: "<<ret << endl;
    delete[] sm;
    if (ret == -1){
        cout << "no scaling factor can satisfy the condition, change search range!" <<endl;
    }else{
        S_mat.copy(&mat);
        S_mat.scale(ret);
        is_scaled = true;
        scaling_factor = ret;
    }
}

