#include "policy.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <stdexcept>

// ============================================================================
// independent/policy.cpp：两条单目标策略的实现与每阶段验收护栏。
//
// 阅读顺序：
//   coefficient()          —— 校验 matching 并求本轮公共系数；
//   minimum_bottleneck()   —— time 策略的“最小化最大边代价”匹配；
//   Planner::Planner()     —— 解析方法名、准备 q* 种子/资源缓存/路径带宽；
//   Planner::decompose_into() —— 逐阶段构造候选、验收、扣减真实剩余。
//
// 所有候选仍走原生 Birkhoff 的“一个 perfect matching × 一个公共系数”
// 形式；本文件只改变“选哪个 matching”，不改变真实需求、balance/dispatch、
// 扣减规则或 pipeline2 组合。虚拟补齐不进入 remaining，因此不会被计价。
// ============================================================================

namespace independent_fast {
// 校验 m 是 a 上的 perfect matching，并返回所有匹配边的最小容量。
// 这里同时检查：维度、目的端范围、目的端不重复、边容量非零。
// 返回的 q 是本轮能同时抽走的公共系数；后续候选必须保持这个 q 不变。
static unsigned coefficient(const std::vector<unsigned>& a, const std::vector<int>& m) {
    unsigned n=m.size(), result=UINT32_MAX;
    std::vector<bool> seen(n);
    if(a.size()!=n*n || !n) throw std::invalid_argument("coefficient dimensions");
    for(unsigned s=0;s<n;++s) {
        int d=m[s];
        if(d<0 || d>=int(n) || seen[d] || !a[s*n+d]) throw std::invalid_argument("unsupported matching");
        seen[d]=true; result=std::min(result,a[s*n+d]);
    }
    return result;
}
// 精确的 bottleneck assignment：
//   minimize max_{s} costs[s][m(s)]
//   subject to m 是 perfect matching，且所有边满足 a[s][m(s)] >= q。
// 做法不是在边上贪心，而是：
//   1. 收集所有支持边的代价，排序去重；
//   2. 二分一个上界 bound；
//   3. 在“a>=q 且 cost<=bound”的子图上跑增广路，判断是否存在完美匹配；
//   4. 取最小的可行 bound 对应的匹配。
// 因而它精确最小化当前阶段的“最大单路径时间”，但仍然看不到同阶段其他流
// 对共享资源的并发竞争——这正是 time 策略与 bandwidth 策略的分工。
std::vector<int> minimum_bottleneck(const std::vector<unsigned>& a,
    const std::vector<std::vector<unsigned>>& neighbors, unsigned q,
    const std::vector<double>& costs, uint64_t& calls) {
    unsigned n=neighbors.size();
    if(!n || !q || a.size()!=n*n || costs.size()!=n*n) throw std::invalid_argument("bottleneck dimensions");
    // 只把真正可用的支持边代价放入候选阈值；被 a>=q 排除的边永远不能使用。
    std::vector<double> values;
    for(unsigned s=0;s<n;++s) for(unsigned d:neighbors[s]) {
        if(d>=n) throw std::invalid_argument("neighbor range");
        double v=costs[s*n+d];
        if(!std::isfinite(v) || v<0) throw std::invalid_argument("nonfinite time cost");
        if(a[s*n+d]>=q) values.push_back(v);
    }
    std::sort(values.begin(),values.end()); values.erase(std::unique(values.begin(),values.end()),values.end());
    // 可行性检查：在 cost<=bound 的支持子图上找完美匹配。
    // 标准 Kuhn 增广；每次外层行重新清空 seen，避免跨行复用访问标记。
    auto feasible=[&](double bound,std::vector<int>& m) {
        ++calls; m.assign(n,-1); std::vector<int> inverse(n,-1); std::vector<bool> seen(n);
        std::function<bool(unsigned)> dfs=[&](unsigned s) {
            for(unsigned d:neighbors[s]) if(a[s*n+d]>=q && costs[s*n+d]<=bound && !seen[d]) {
                seen[d]=true;
                if(inverse[d]<0 || dfs(inverse[d])) { m[s]=d;inverse[d]=s;return true; }
            }
            return false;
        };
        for(unsigned s=0;s<n;++s) { std::fill(seen.begin(),seen.end(),false);if(!dfs(s)) return false; }
        return true;
    };
    // 二分最小可行上界；best 保存最后一次成功（也就是最小可行 bound）的匹配。
    // 注意 values 已经去重，所以循环结束时的 best 一定落在某个真实边代价上。
    std::vector<int> best; int lo=0,hi=int(values.size())-1;
    while(lo<=hi) {
        int mid=(lo+hi)/2; std::vector<int> trial;
        if(feasible(values[mid],trial)) { best=std::move(trial);hi=mid-1; } else lo=mid+1;
    }
    if(best.empty()) throw std::invalid_argument("time support has no perfect matching");
    return best;
}

// 解析方法名并准备所需组件：
//   maxmin            -> exact 种子 + 不再做拓扑构造；
//   maxmin-bandwidth  -> exact 种子 + bandwidth 构造；
//   maxmin-time       -> exact 种子 + time 构造；
//   native/bandwidth/time -> 使用原生 hungarian 的匹配作为种子。
// exact 种子由 scan::Planner 的 maxmin 实现给出；ResourceCache 只给
// bandwidth 策略使用；time 策略只需预先计算每条 server 对路径上的最小带宽。
Planner::Planner(const native_topology::Topology& t,std::vector<uint64_t> rows,std::string method)
    :top(t),remaining(std::move(rows)),objective(std::move(method)) {
    if(remaining.size()!=top.n*top.n*top.g) throw std::invalid_argument("pending dimensions");
    // maxmin 自身只改变粒度；maxmin-X 在粒度之外再执行 X 的单目标构造。
    if(objective=="maxmin") { exact=true;objective="native"; }
    else if(objective.find("maxmin-")==0) { exact=true;objective=objective.substr(7); }
    if(objective!="native" && objective!="bandwidth" && objective!="time") throw std::invalid_argument("unknown strategy");
    // 精确 q* 种子：只创建一次，内部保存自己的逐 GPU 剩余影子；它不会与
    // 下面的 remaining 混用。threshold_planner->choose() 只负责选种子匹配。
    if(exact) threshold_planner.reset(new native_scan::Planner(top,remaining,"maxmin"));
    // bandwidth 策略的路径/资源缓存；编译成本计入规划，不作为免费预处理。
    if(objective=="bandwidth") resources.reset(new native_scan::ResourceCache(top));
    if(objective=="time") {
        // time 策略的辅助代价用“路径上最小带宽”近似该条流的单路径服务能力。
        // path() 返回的是资源 key 序列；同 DC 路径只有 server 资源，跨 DC 还会
        // 经过 gateway/WAN 资源。这里取最小值，避免把瓶颈资源平均掉。
        path_bandwidth.assign(top.n*top.n,std::numeric_limits<double>::infinity());
        for(unsigned s=0;s<top.n;++s) for(unsigned d=0;d<top.n;++d)
            for(const auto& key:top.path(s,d)) path_bandwidth[s*top.n+d]=std::min(path_bandwidth[s*top.n+d],top.bandwidths.at(key));
    }
}
// 把 matching 和公共系数 q 变成每对 server 的实际流。
// 真实字节 = sum_u min(q, remaining[(s,d,u)])；只有 src!=dst 才产生网络流。
// 自环/零真实量不进入 Transfer，避免把虚拟补齐或同机数据算成 WAN 负载。
std::vector<native_topology::Transfer> Planner::flows(const std::vector<int>& m,unsigned q) const {
    std::vector<native_topology::Transfer> result;
    for(unsigned s=0;s<top.n;++s) {
        unsigned d=m[s];uint64_t bytes=0;
        if(s!=d) for(unsigned u=0;u<top.g;++u) bytes+=std::min<uint64_t>(q,remaining[(s*top.n+d)*top.g+u]);
        result.push_back({s,d,bytes});
    }
    return result;
}
void Planner::consume(const std::vector<int>& m,unsigned q) {
    // 与原生派发保持同一个“逐 GPU 待发量”影子。注意：原生允许自匹配块继续
    // 参与扣减；这里不把自环当网络服务，但仍按同一历史语义扣减 remaining，
    // 以保证后续阶段和原生 balance/dispatch 的剩余量一致。
    // 真实跨服务器字节由 flows()/decompose_into() 末尾的检查单独保证。
    for(unsigned s=0;s<top.n;++s) for(unsigned u=0;u<top.g;++u) {
        auto& v=remaining[(s*top.n+m[s])*top.g+u];v-=std::min<uint64_t>(q,v);
    }
}
void Planner::decompose_into(FastAll2All& app) {
    // 先执行原生 server 内 balance/缩放，得到与 FAST 一致的 server 级残余矩阵。
    // 下面无论是 native 直接分解，还是自定义 decompose_with，都在这份残余上工作。
    app.to_scaled_doubly_stochastic_matrix();
    if(objective=="native" && !exact) {
        // 纯 native：完全调用冻结引擎的原始分解，只同步扣减自己的真实剩余影子。
        // 这条路径用于基线和“策略关闭”对照，不能再做任何匹配改动。
        app.decompose();
        for(const auto& p:app.p_sets) {
            std::vector<int> m(top.n);for(unsigned s=0;s<top.n;++s)m[s]=p.mp.at(s);
            consume(m,p.get_freq());
        }
        counts.stages=app.p_sets.size();
    } else app.decompose_with([&](Matrix& residual,hungarian_info_t& info) {
        // 每个 Birkhoff 阶段的回调：info 已包含原生 hungarian 的种子匹配和
        // 当前残余矩阵。先把矩阵/支持图复制成紧凑形式，避免反复访问 Matrix。
        if(++counts.stages>top.n*top.n) throw std::runtime_error("finite support bound");
        unsigned n=top.n;std::vector<unsigned> a(n*n);std::vector<int> chosen(n);
        std::vector<std::vector<unsigned>> neighbors(n);
        for(unsigned s=0;s<n;++s) {
            chosen[s]=info.matching[s]-n;
            for(unsigned d:info.row_to_col[s])neighbors[s].push_back(d-n);
            for(unsigned d=0;d<n;++d)a[s*n+d]=residual.get(s,d);
        }
        // exact 种子：用 scan 的精确 Max-Min 重新选本轮匹配；否则保留原生种子。
        // 注意 exact 只改变“选哪个匹配/粒度”，下面的 q 仍由最终 chosen 计算。
        if(exact) chosen=threshold_planner->choose(a,info).matching;
        unsigned q=coefficient(a,chosen);
        if(objective!="native") {
            // 候选构造：两种策略都只构造一次，且都限制在 a>=q 的支持图上。
            ++counts.constructions;std::vector<int> trial;
            std::vector<double> costs(n*n);
            if(objective=="bandwidth") {
                // bandwidth：复用 ResourceCache 的完整增广构造；自然行序，
                // 以“共享资源峰值 + 负载平方增量”选择候选边。
                std::vector<unsigned> order(n);std::iota(order.begin(),order.end(),0);++counts.matching_calls;
                if(!resources->balanced_matching(a,neighbors,q,remaining,order,trial,true)) throw std::runtime_error("resource matching lost feasibility");
            } else {
                // time：为每条支持边构造辅助单路径时间
                //   alpha(s,d) + 0.008 * x / B_path_min
                // x 是本轮真实可搬字节（min(q, 逐GPU剩余)）；0.008 把
                // bytes/Gbps 转为微秒。自环和零真实量保持 0，不产生服务。
                for(unsigned s=0;s<n;++s) for(unsigned d=0;d<n;++d) if(s!=d) {
                    uint64_t x=0;for(unsigned u=0;u<top.g;++u)x+=std::min<uint64_t>(q,remaining[(s*n+d)*top.g+u]);
                    if(x) costs[s*n+d]=top.path_alpha(s,d)+.008*double(x)/path_bandwidth[s*n+d];
                }
                trial=minimum_bottleneck(a,neighbors,q,costs,counts.matching_calls);
            }
            // 三条护栏，任何一条不满足都回退种子：
            //   1. 候选系数必须恰好等于 q（不允许用更小系数换覆盖率）；
            //   2. 候选真实服务字节不得少于种子（不允许靠虚拟补齐得分）；
            //   3. 当前阶段目标必须严格改善（带相对浮点容差）。
            auto real_bytes=[&](const std::vector<int>& m) { uint64_t total=0;for(const auto& f:flows(m,q))total+=f.bytes;return total; };
            auto score=[&](const std::vector<int>& m) {
                if(objective=="bandwidth")return top.evaluate(flows(m,q)).serial_us;
                double value=0;for(unsigned s=0;s<n;++s)value=std::max(value,costs[s*n+m[s]]);return value;
            };
            if(coefficient(a,trial)!=q) ++counts.coefficient_rejections;
            else if(real_bytes(trial)<real_bytes(chosen)) ++counts.volume_rejections;
            else {
                double old=score(chosen),value=score(trial);
                if(value<old-1e-12*std::max(1.,std::abs(old))) { chosen=std::move(trial);++counts.accepted; }
            }
        }
        // 把选中的 matching 写回原生 info，并扣减真实剩余。下一轮回调会看到
        // 由这个选择产生的残余；因此本阶段的局部改善可能让后续阶段更差。
        for(unsigned s=0;s<n;++s) { info.matching[s]=n+chosen[s];info.matching[n+chosen[s]]=s; }
        consume(chosen,q);
    });
    // 守恒检查：所有真实跨 server 需求必须被搬完；自环/虚拟补齐不计入这里。
    // 若剩余非零，说明匹配/扣减逻辑丢了字节，直接失败而不是输出好看的数字。
    for(unsigned s=0;s<top.n;++s) for(unsigned d=0;d<top.n;++d) if(s!=d)
        for(unsigned u=0;u<top.g;++u) if(remaining[(s*top.n+d)*top.g+u]) throw std::runtime_error("planner left real inter-server bytes");
    // exact 种子的阈值搜索次数也计入统一统计，方便解释规划开销。
    if(threshold_planner) counts.matching_calls+=threshold_planner->stats().matching_calls;
}
}
