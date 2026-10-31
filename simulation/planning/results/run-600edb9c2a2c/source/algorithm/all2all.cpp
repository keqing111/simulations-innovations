#include "all2all.h"
#include "matrix.h"
#include "define.h"
#include <iomanip>
#include <pthread.h>
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

void FastAll2All::decompose(){
    decompose_with({});
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
bool FastAll2All::hungarian_dfs(uint u){
    for (unordered_set<uint>::iterator col_idx = hungarian_info.row_to_col[u].begin(); col_idx != hungarian_info.row_to_col[u].end(); col_idx ++){
        if (!hungarian_info.visit[*col_idx]){
            hungarian_info.visit[*col_idx] = true;
            if (hungarian_info.matching[*col_idx] == -1 || hungarian_dfs(hungarian_info.matching[*col_idx])){
                // 列顶点空闲，或者它的当前匹配行可以改配到别的列 -> 重新配对
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
void FastAll2All::update_edges(){
    uint dim = SDS_mat.get_dim();
    // row vertices id: 0 - dim-1, col vertices id: dim - 2*dim-1
    for (uint i = 0; i < dim; i++){
        for (uint j = 0; j < dim; j++){
            uint col_id = j + dim;
            if (SDS_mat.get(i, j) > 0 ){
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
uint FastAll2All::update_permutation_sets(){
    uint dim = SDS_mat.get_dim();
    // row vertices id: 0 - dim-1, col vertices id: 0 - dim-1
    PermutationSet r(1, is_scaled ? scaling_factor : 1, dim);
    uint min_freq = SDS_mat.get(0, hungarian_info.matching[0] - dim);
    for(uint i = 0; i < dim; i++){
        uint col_id = hungarian_info.matching[i] - dim;
        min_freq = MIN(SDS_mat.get(i, col_id), min_freq);
        r.mp.insert(make_pair(i, col_id));
    }

    for(uint i = 0; i < dim; i++){
        uint col_id = hungarian_info.matching[i] - dim;
        SDS_mat.subtract(min_freq, i, col_id);
    }

    r.set_freq(min_freq);
    p_sets.push_back(r);
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

