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
    for(uint s=0;s<n;++s)for(uint u=0;u<gpu_n;++u) {
        auto& v=remaining_units_[(s*n+m[s])*gpu_n+u];v-=min<uint64_t>(v,units);
    }
}
// Cache this stage's sparse resource contributions once per server pair.
void FastAll2All::prepare_topology_stage(const native_topology::Topology& top,uint q) {
    auto& h=hungarian_info;uint n=SDS_mat.get_dim(),units=original_units(q),r=top.resource_count();
    h.edge_loads.resize(n*n);h.edge_cost_us.assign(n*n,0);
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
        touched.clear();h.edge_cost_us[s*n+d]=alpha+serial;
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
    double before=0,after=0;
    if(mode==ScheduleMode::BandwidthAware) {
        vector<native_topology::GpuTransfer> seed,trial;
        build_transfers(h.seed_matching,q,seed);build_transfers(h.candidate_matching,q,trial);
        before=top.evaluate_gpu(seed).serial_us;after=top.evaluate_gpu(trial).serial_us;
    }
    else {
        before=after=0;
        for(uint s=0;s<n;++s){before=max(before,h.edge_cost_us[s*n+h.seed_matching[s]]);after=max(after,h.edge_cost_us[s*n+h.candidate_matching[s]]);}
    }
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

