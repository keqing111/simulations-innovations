#include "planner.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <numeric>
#include <stdexcept>

// ============================================================================
// scan/planner.cpp：算法扫描的核心实现。
//
// 阅读顺序建议：
//   Options::parse               方法名 -> 策略开关
//   ResourceCache                拓扑资源预计算、评分、压力、构造匹配
//   support_summary              q* 支持图自由度诊断（Tarjan SCC）
//   Planner::choose              每个阶段如何选 matching
//   Planner::decompose_into      挂进 FastAll2All::decompose_with 的入口
//
// 关键约束：
//   - 所有策略都必须保持 perfect matching，并让 coefficient > 0；
//   - 排序/评分只用于“在有限候选里选一个”，不枚举排列、不做全局最优；
//   - 资源模型只统计真实剩余 payload，虚拟补齐容量不产生网络收益；
//   - 局部接受（L、构造、SPM）不保证全局阶段数或完成时间一定变好。
// ============================================================================

namespace native_scan {
// 方法名解析。Fxyz 中 x=G、y=L、z=R；其他名字是论文实验里的固定组合。
// 带 "-ref"/"-opt" 后缀时先递归解析主体，再记录实现版本（语义应相同）。
Options Options::parse(const std::string& method){
    if(method.size()>4&&(method.substr(method.size()-4)=="-ref"||method.substr(method.size()-4)=="-opt")){
        auto o=parse(method.substr(0,method.size()-4));o.optimized=method.substr(method.size()-4)=="-opt";return o;}
    Options o;
    // spreadout 由 main.cc 单独走基线循环，不会真的调用本 Planner；
    // 这里返回全 false 只是为了让 parse 有定义。
    if(method=="spreadout")return o;
    // SPM：静态路径最大权匹配。exact 只用来求 q*，真正的构造由 static_path 分支完成。
    if(method=="SPM"){o.exact=true;o.constructive_budget=1;o.static_path=true;return o;}
    // A1D/A1P/A1DP：精确 q* + 一次构造，行顺序分别是“度数升序 / 压力降序 / 度数后压力”。
    if(method=="A1D"||method=="A1P"||method=="A1DP"){
        o.exact=true;o.constructive_budget=1;o.row_order=method=="A1D"?1:method=="A1P"?2:3;return o;}
    // A1/A2：精确 q* + 一次/两次完整构造（A2 第二次按 DC 轮转行序）。
    if(method=="A1"||method=="A2"){o.exact=true;o.constructive_budget=method=="A1"?1:2;return o;}
    // Max-Min：只求精确 q*，按该阈值重新找一个可行匹配，不做 L/构造。
    if(method=="maxmin"){o.exact=true;return o;}
    // 第二轮组合：X10=Max-Min+L，X01=Max-Min+R，X11=L+R。
    // Q75/Q50 把阈值从 q* 放宽到 ceil(3q*/4)、ceil(q*/2)；
    // B11 用“本阶段时间 + 剩余需求下界”评分；-4 把 L 的预算从 2 提到 4。
    if(method=="X10"||method=="X01"||method=="X11"||method=="X11-4"||
       method=="Q75"||method=="Q50"||method=="B11"||method=="B11-4"){
        o.exact=true;o.link=method!="X01";o.residual=method!="X10";
        if(method=="Q75"){o.quota_numerator=3;o.quota_denominator=4;}
        if(method=="Q50"){o.quota_numerator=1;o.quota_denominator=2;}
        o.tail_score=method=="B11"||method=="B11-4";
        if(method=="X11-4"||method=="B11-4")o.cycle_budget=4;
        return o;
    }
    // Fxyz：三个二进制开关。
    if(method.size()!=4 || method[0]!='F' || method.find_first_not_of("01",1)!=std::string::npos)
        throw std::invalid_argument("unknown G/L/R method");
    o.granularity=method[1]=='1';o.link=method[2]=='1';o.residual=method[3]=='1';return o;
}

// 把 Topology 的字符串资源键映射成整数 id；routes 只保存 id 序列，
// 后续评分不再查 map。带宽/延迟对所有 (src,dst) 对预取一次。
ResourceCache::ResourceCache(const native_topology::Topology& t):n(t.n),g(t.g),dc(t.dc),routes(n*n),alpha(n*n){
    std::map<std::string,unsigned> ids;
    for(const auto& item:t.bandwidths){ids[item.first]=bandwidth.size();bandwidth.push_back(item.second);}
    loads.resize(bandwidth.size());
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d){alpha[s*n+d]=t.path_alpha(s,d);
        for(const auto& key:t.path(s,d))routes[s*n+d].push_back(ids.at(key));}
}
// 阶段代价 = max(路径 alpha) + max(每条共享资源的 bytes/带宽)。
// 只取 min(w, 真实剩余)：w 是 matching 的公共系数，可能大于该对真实剩余；
// 虚拟补齐的容量不会进入 rows，所以不会贡献 bytes。
CostSummary ResourceCache::score(const std::vector<int>& m,unsigned w,const std::vector<uint64_t>& rows){
    if(m.size()!=n || rows.size()!=n*n*g)throw std::invalid_argument("resource dimensions");
    std::fill(loads.begin(),loads.end(),0);CostSummary result;double delay=0,serial=0;
    for(unsigned s=0;s<n;++s){unsigned d=m[s];if(d>=n)throw std::invalid_argument("destination range");if(s==d)continue;
        uint64_t volume=0;for(unsigned u=0;u<g;++u)volume+=std::min<uint64_t>(w,rows[(s*n+d)*g+u]);
        if(!volume)continue;result.bytes+=volume;delay=std::max(delay,alpha[s*n+d]);
        for(unsigned e:routes[s*n+d])loads[e]+=volume;
    }
    // 0.008 = 8 bit/Byte / 1000（Gbps、us、MB 的单位换算）：bytes*8/(Gbps*1e9) 秒。
    for(unsigned e=0;e<loads.size();++e)serial=std::max(serial,.008*double(loads[e])/bandwidth[e]);
    result.us=result.bytes?delay+serial:0;return result;
}
// SPM 的边效用：本边单独跑时的真实字节 / (路径 alpha + 单流串行时间)。
// 这里只用该路径最小带宽，不考虑与其他流共享资源，是“静态路径”版本；
// q 限制每条边最多取 q 个单位，避免虚拟或超大残余把权重拉偏。
std::vector<double> ResourceCache::individual_utilities(unsigned q,const std::vector<uint64_t>& rows){
    if(!q||rows.size()!=n*n*g)throw std::invalid_argument("utility dimensions");
    if(path_min_bw.empty()){
        path_min_bw.assign(n*n,std::numeric_limits<double>::infinity());
        for(unsigned k=0;k<n*n;++k)for(unsigned e:routes[k])path_min_bw[k]=std::min(path_min_bw[k],bandwidth[e]);}
    std::vector<double> utility(n*n);
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d){uint64_t v=0;
        for(unsigned u=0;u<g;++u)v+=std::min<uint64_t>(q,rows[(s*n+d)*g+u]);
        if(v)utility[s*n+d]=double(v)/(alpha[s*n+d]+.008*double(v)/path_min_bw[s*n+d]);}
    return utility;
}

// 带阈值掩码的最大权完美匹配（SPM 使用）。
// 只允许 a[i*n+j] >= q 的边进入增广；用最短增广路 Hungarian 最小化 -utility。
// 如果掩码后的图没有 perfect matching，抛异常而不是返回部分匹配。
std::vector<int> maximum_weight_matching(const std::vector<unsigned>& a,unsigned n,unsigned q,const std::vector<double>& weights){
    if(!n||!q||a.size()!=n*n||weights.size()!=n*n)throw std::invalid_argument("weighted matching dimensions");
    for(double w:weights)if(!std::isfinite(w))throw std::invalid_argument("nonfinite utility");
    // Masked shortest augmenting path Hungarian, minimizing -utility. An
    // absent edge is never relaxed; no finite penalty can leak forbidden edges.
    std::vector<double> u(n+1),v(n+1);std::vector<unsigned> p(n+1),way(n+1);
    for(unsigned i=1;i<=n;++i){p[0]=i;unsigned j0=0;
        std::vector<double> minv(n+1,std::numeric_limits<double>::infinity());std::vector<unsigned char> used(n+1);
        do{used[j0]=1;unsigned i0=p[j0],j1=0;double delta=std::numeric_limits<double>::infinity();
            for(unsigned j=1;j<=n;++j)if(!used[j]){
                if(a[(i0-1)*n+j-1]>=q){double cur=-weights[(i0-1)*n+j-1]-u[i0]-v[j];
                    if(cur<minv[j]){minv[j]=cur;way[j]=j0;}}
                if(minv[j]<delta){delta=minv[j];j1=j;}}
            if(!std::isfinite(delta))throw std::runtime_error("weighted support has no perfect matching");
            for(unsigned j=0;j<=n;++j)if(used[j]){u[p[j]]+=delta;v[j]-=delta;}else minv[j]-=delta;
            j0=j1;
        }while(p[j0]);
        do{unsigned j1=way[j0];p[j0]=p[j1];j0=j1;}while(j0);
    }
    std::vector<int> result(n,-1);for(unsigned j=1;j<=n;++j){
        if(!p[j]||a[(p[j]-1)*n+j-1]<q)throw std::runtime_error("weighted matching invalid");result[p[j]-1]=j-1;}
    return result;
}
// R 使用的“残余压力”：
//   1. 把当前所有真实剩余需求按固定路径摊到资源上，得到 load；
//   2. pressure[e] = load[e]/bandwidth[e]，再除以全局最大值归一化；
//   3. 一条 server 边的优先级 = 其真实量 × 沿途资源的归一化压力/带宽之和。
// 压力大的边/行会被优先处理，但这不是精确的拥塞模型。
std::vector<double> ResourceCache::priorities(const std::vector<uint64_t>& rows){
    if(rows.size()!=n*n*g)throw std::invalid_argument("residual dimensions");
    std::fill(loads.begin(),loads.end(),0);std::vector<uint64_t> volumes(n*n);
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d){
        auto& v=volumes[s*n+d];for(unsigned u=0;u<g;++u)v+=rows[(s*n+d)*g+u];
        for(unsigned e:routes[s*n+d])loads[e]+=v;
    }
    double maximum=0;std::vector<double> pressure(loads.size());
    for(unsigned e=0;e<loads.size();++e){pressure[e]=double(loads[e])/bandwidth[e];maximum=std::max(maximum,pressure[e]);}
    std::vector<double> priorities(n*n);
    if(!maximum)return priorities;
    for(unsigned k=0;k<n*n;++k)if(volumes[k])for(unsigned e:routes[k])
        priorities[k]+=double(volumes[k])*(pressure[e]/maximum)/bandwidth[e];
    return priorities;
}

// 把边优先级按行汇总；只累计支持图 a[s,d]>=q 的边。
// A1P/A1DP 用它给行排序，因此压力大的行会先参与构造。
std::vector<double> ResourceCache::row_priorities(const std::vector<unsigned>& a,unsigned q,const std::vector<uint64_t>& rows){
    if(a.size()!=n*n||!q)throw std::invalid_argument("row priority dimensions");
    auto edges=priorities(rows);std::vector<double> result(n);
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(a[s*n+d]>=q)result[s]+=edges[s*n+d];
    return result;
}

// 构造式匹配：按 order 逐行增广。每行先看支持图 a>=q 的候选目的，
// 把“本行当前匹配边的负载”从共享资源中临时扣掉，再按加入候选后的
// 资源峰值（peak）和负载平方增量（square）排序，最后走标准 DFS 增广。
bool ResourceCache::balanced_matching(const std::vector<unsigned>& a,const std::vector<std::vector<unsigned>>& neighbors,
                                     unsigned q,const std::vector<uint64_t>& rows,const std::vector<unsigned>& order,std::vector<int>& m,bool optimized){
    if(a.size()!=n*n||neighbors.size()!=n||rows.size()!=n*n*g||order.size()!=n||!q)
        throw std::invalid_argument("constructive dimensions");
    if(optimized){
        // Lazy and per-planner: initialization is charged to planning, never
        // shared across independent inputs. A row cannot re-enter its active
        // DFS frame: its unique matched destination is already marked seen.
        work_volume.assign(n*n,0);work_used.assign(bandwidth.size(),0);
        work_inverse.assign(n,-1);work_seen.resize(n);work_ordered.assign(n,0);
        if(work_reduced.empty()){
            work_reduced.resize(n);work_candidates.resize(n);
            for(unsigned s=0;s<n;++s){work_reduced[s].resize(bandwidth.size());work_candidates[s].reserve(n);}}
        for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d)
            for(unsigned u=0;u<g;++u)work_volume[s*n+d]+=std::min<uint64_t>(q,rows[(s*n+d)*g+u]);
        m.assign(n,-1);
        std::function<bool(unsigned)> dfs=[&](unsigned s){
            // reduced 是本行视角下的资源负载副本；成功增广后才更新 work_used。
            auto& reduced=work_reduced[s];std::copy(work_used.begin(),work_used.end(),reduced.begin());
            if(m[s]>=0)for(unsigned e:routes[s*n+m[s]])reduced[e]-=work_volume[s*n+m[s]];
            double base=0;for(unsigned e=0;e<reduced.size();++e)base=std::max(base,double(reduced[e])/bandwidth[e]);
            auto& candidates=work_candidates[s];candidates.clear();bool finite=true;
            for(unsigned d:neighbors[s])if(a[s*n+d]>=q&&!work_seen[d]){
                double peak=base,square=0;auto v=work_volume[s*n+d];
                for(unsigned e:routes[s*n+d]){double old=double(reduced[e])/bandwidth[e],next=double(reduced[e]+v)/bandwidth[e];
                    peak=std::max(peak,next);square+=(next-old)*(next+old);}
                finite=finite&&std::isfinite(peak)&&std::isfinite(square);
                candidates.push_back({d,unsigned(candidates.size()),peak,square});}
            // 有序 sort 用 ordinal 做最终 tie-break；非有限值退回 stable_sort 保持原顺序。
            auto before=[](const Candidate& x,const Candidate& y){return x.peak!=y.peak?x.peak<y.peak:x.square<y.square;};
            if(finite)std::sort(candidates.begin(),candidates.end(),[&](const Candidate& x,const Candidate& y){
                return x.peak!=y.peak?x.peak<y.peak:x.square!=y.square?x.square<y.square:x.ordinal<y.ordinal;});
            else std::stable_sort(candidates.begin(),candidates.end(),before);
            for(const auto& candidate:candidates){unsigned d=candidate.d;if(work_seen[d])continue;work_seen[d]=true;
                if(work_inverse[d]<0||dfs(work_inverse[d])){
                    if(m[s]>=0)for(unsigned e:routes[s*n+m[s]])work_used[e]-=work_volume[s*n+m[s]];
                    for(unsigned e:routes[s*n+d])work_used[e]+=work_volume[s*n+d];m[s]=d;work_inverse[d]=s;return true;}}
            return false;
        };
        for(unsigned s:order){if(s>=n||work_ordered[s])throw std::invalid_argument("constructive row order");work_ordered[s]=true;
            std::fill(work_seen.begin(),work_seen.end(),0);if(!dfs(s))return false;}
        return true;
    }
    // 未优化版本：每次 DFS 都新建 reduced/candidates，作为 optimized 的语义参照。
    std::vector<uint64_t> volume(n*n),used(bandwidth.size());
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(s!=d)
        for(unsigned u=0;u<g;++u)volume[s*n+d]+=std::min<uint64_t>(q,rows[(s*n+d)*g+u]);
    m.assign(n,-1);std::vector<int> inverse(n,-1);std::vector<bool> seen(n);
    std::function<bool(unsigned)> dfs=[&](unsigned s){
        // Ranking is read-only. Remove this row's current assignment in a local
        // view; commit resource updates only on a successful augmenting path.
        auto reduced=used;
        if(m[s]>=0)for(unsigned e:routes[s*n+m[s]])reduced[e]-=volume[s*n+m[s]];
        double base=0;for(unsigned e=0;e<reduced.size();++e)base=std::max(base,double(reduced[e])/bandwidth[e]);
        struct Candidate{unsigned d;double peak,square;};std::vector<Candidate> candidates;
        for(unsigned d:neighbors[s])if(a[s*n+d]>=q&&!seen[d]){
            double peak=base,square=0;auto v=volume[s*n+d];
            for(unsigned e:routes[s*n+d]){double old=double(reduced[e])/bandwidth[e],next=double(reduced[e]+v)/bandwidth[e];
                peak=std::max(peak,next);square+=(next-old)*(next+old);}
            candidates.push_back({d,peak,square});
        }
        std::stable_sort(candidates.begin(),candidates.end(),[](const Candidate& x,const Candidate& y){
            return x.peak!=y.peak?x.peak<y.peak:x.square<y.square;});
        for(const auto& candidate:candidates){unsigned d=candidate.d;if(seen[d])continue;seen[d]=true;
            if(inverse[d]<0||dfs(inverse[d])){
                if(m[s]>=0)for(unsigned e:routes[s*n+m[s]])used[e]-=volume[s*n+m[s]];
                for(unsigned e:routes[s*n+d])used[e]+=volume[s*n+d];m[s]=d;inverse[d]=s;return true;}
        }
        return false;
    };
    std::vector<bool> ordered(n);
    for(unsigned s:order){if(s>=n||ordered[s])throw std::invalid_argument("constructive row order");ordered[s]=true;
        std::fill(seen.begin(),seen.end(),false);if(!dfs(s))return false;}
    return true;
}

// B11 的乐观剩余下界：
//   LB = max_e(剩余负载/带宽)
//        + max(最大收发度 × 最小全网 alpha,
//              最大跨 DC 收发度 × 最小 WAN alpha)
// 先假设本阶段按当前 matching 和系数 w 扣掉需求，再算剩余。
// 这只是当前模型内的宽松下界，用来给 tail_score 一个方向，不是全局最优证明。
double ResourceCache::remaining_bound(const std::vector<int>& m,unsigned w,const std::vector<uint64_t>& rows){
    if(m.size()!=n||rows.size()!=n*n*g)throw std::invalid_argument("remaining bound dimensions");
    std::fill(loads.begin(),loads.end(),0);
    std::vector<unsigned> out(n),in(n),wan_out(n),wan_in(n);
    double min_all=std::numeric_limits<double>::infinity(),min_wan=min_all,serial=0;
    for(unsigned s=0;s<n;++s){if(m[s]<0||m[s]>=int(n))throw std::invalid_argument("bound destination range");
        for(unsigned d=0;d<n;++d)if(s!=d){uint64_t bytes=0;
            for(unsigned u=0;u<g;++u){auto v=rows[(s*n+d)*g+u];bytes+=v-(m[s]==int(d)?std::min<uint64_t>(v,w):0);}
            if(!bytes)continue;++out[s];++in[d];min_all=std::min(min_all,alpha[s*n+d]);
            if(dc[s]!=dc[d]){++wan_out[s];++wan_in[d];min_wan=std::min(min_wan,alpha[s*n+d]);}
            for(unsigned e:routes[s*n+d])loads[e]+=bytes;
        }
    }
    for(unsigned e=0;e<loads.size();++e)serial=std::max(serial,.008*double(loads[e])/bandwidth[e]);
    unsigned degree=0,wan_degree=0;
    for(unsigned s=0;s<n;++s){degree=std::max(degree,std::max(out[s],in[s]));wan_degree=std::max(wan_degree,std::max(wan_out[s],wan_in[s]));}
    return serial+std::max(degree?degree*min_all:0,wan_degree?wan_degree*min_wan:0);
}

// Planner 只保存真实逐 GPU 残余；ResourceCache 仅在策略需要拓扑评分/
// 压力/构造时创建，F000 这类纯原生策略不会付这份预处理成本。
Planner::Planner(const native_topology::Topology& t,std::vector<uint64_t> rows,const std::string& method)
    :top(t),remaining(std::move(rows)),options(Options::parse(method)){
    if(remaining.size()!=top.n*top.n*top.g)throw std::invalid_argument("planner dimensions");
    auto start=std::chrono::steady_clock::now();
    if(options.link||options.residual||options.constructive_budget)resources.reset(new ResourceCache(top));
    preprocess_us=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-start).count();
}
// 一个 perfect matching 的公共系数 = 所有匹配边 a[s][m[s]] 的最小值；
// 同时校验它是合法置换且每条边都 >0。
static unsigned coefficient(const std::vector<unsigned>& a,unsigned n,const std::vector<int>& m){
    unsigned w=UINT32_MAX;std::vector<bool> seen(n);
    if(m.size()!=n)throw std::invalid_argument("matching dimension");
    for(unsigned s=0;s<n;++s){int d=m[s];
        if(d<0||d>=int(n)||seen[d]||!a[s*n+d])throw std::invalid_argument("matching not supported perfect");
        seen[d]=true;w=std::min(w,a[s*n+d]);}
    return w;
}

// q* 支持图自由度诊断（只读，不参与调度决策）。
// 把每个匹配边 s->m[s] 收缩成一个行顶点；非匹配支持边 s->d 变成
// s -> inverse[d] 的有向弧。于是“二分图中的交替环”等价于“行图中的环”。
// Tarjan SCC 找出大小>1 的分量；分量为>1 的行才可能换目的。
// real_movable_rows 进一步要求变化的边上有真实 payload，避免把纯虚拟容量
// 的移动误认为可优化空间。
SupportInfo support_summary(const std::vector<unsigned>& a,unsigned n,unsigned q,const std::vector<int>& m,
                            const std::vector<uint64_t>& real,unsigned g){
    if(!n||!g||!q||a.size()!=n*n||real.size()!=n*n*g||coefficient(a,n,m)<q)
        throw std::invalid_argument("support diagnostic input");
    std::vector<unsigned> inverse(n);for(unsigned s=0;s<n;++s)inverse[m[s]]=s;
    SupportInfo result;result.degrees.resize(n);std::vector<std::vector<unsigned>> graph(n);
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(a[s*n+d]>=q){
        ++result.edges;++result.degrees[s];if(d!=unsigned(m[s]))graph[s].push_back(inverse[d]);}
    // Directed cycles in the contracted row graph are alternating cycles of
    // the bipartite graph. Tarjan is O(N+E); no matching enumeration.
    // 注意：这里统计的是非匹配支持边，不含当前匹配边本身。
    std::vector<int> index(n,-1),low(n),component(n,-1);std::vector<unsigned> stack,sizes;
    std::vector<bool> on_stack(n);int next=0;
    std::function<void(unsigned)> visit=[&](unsigned s){index[s]=low[s]=next++;stack.push_back(s);on_stack[s]=true;
        for(unsigned v:graph[s]){if(index[v]<0){visit(v);low[s]=std::min(low[s],low[v]);}
            else if(on_stack[v])low[s]=std::min(low[s],index[v]);}
        if(low[s]==index[s]){unsigned count=0,v;do{v=stack.back();stack.pop_back();on_stack[v]=false;component[v]=sizes.size();++count;}while(v!=s);sizes.push_back(count);}
    };
    for(unsigned s=0;s<n;++s)if(index[s]<0)visit(s);
    auto has_real=[&](unsigned s,unsigned d){if(s==d)return false;for(unsigned u=0;u<g;++u)if(real[(s*n+d)*g+u])return true;return false;};
    for(unsigned s=0;s<n;++s)if(sizes[component[s]]>1){
        ++result.movable_rows;bool change=has_real(s,m[s]);
        // flexible_edges 统计同一 SCC 内所有非匹配支持边；
        // real_movable 还要求当前匹配边或这些替代边至少有一条承载真实数据。
        for(unsigned v:graph[s])if(component[v]==component[s]){++result.flexible_edges;change=change||has_real(s,m[v]);}
        if(change)++result.real_movable_rows;
    }
    return result;
}

// 构造式匹配的行顺序：
//   kind=0 自然顺序；
//   kind=1 支持度（可选目的数）升序，越“稀缺”的行越先处理；
//   kind=2 残余压力降序；
//   kind=3 先按支持度升序，再按压力降序（A1DP）。
// 相同键时 stable_sort 保留自然顺序，保证结果可复现。
std::vector<unsigned> constructive_order(const std::vector<unsigned>& a,unsigned n,unsigned q,
                                        const std::vector<double>& pressure,unsigned kind){
    if(!n||a.size()!=n*n||!q||kind>3||(kind>=2&&pressure.size()!=n))throw std::invalid_argument("row order input");
    std::vector<unsigned> order(n),degree(n);std::iota(order.begin(),order.end(),0);
    if(!kind)return order;
    if(kind>=2)for(double v:pressure)if(!std::isfinite(v)||v<0)throw std::invalid_argument("row pressure input");
    if(kind!=2)for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)degree[s]+=a[s*n+d]>=q;
    std::stable_sort(order.begin(),order.end(),[&](unsigned x,unsigned y){
        if(kind!=2&&degree[x]!=degree[y])return degree[x]<degree[y];
        return kind>=2?pressure[x]>pressure[y]:false;});
    return order;
}
// 普通 Kuhn 最大匹配，只看 a>=q 的边；用于阈值可行性判断。
static bool find_matching(const std::vector<unsigned>& a,const std::vector<std::vector<unsigned>>& neighbors,
                          unsigned q,std::vector<int>& m){
    unsigned n=neighbors.size();m.assign(n,-1);std::vector<int> inverse(n,-1);std::vector<bool> seen(n);
    std::function<bool(unsigned)> dfs=[&](unsigned s){
        for(unsigned d:neighbors[s])if(a[s*n+d]>=q && !seen[d]){
            seen[d]=true;if(inverse[d]<0 || dfs(inverse[d])){m[s]=d;inverse[d]=s;return true;}}
        return false;
    };
    for(unsigned s=0;s<n;++s){std::fill(seen.begin(),seen.end(),false);if(!dfs(s))return false;}
    return true;
}

// 每个阶段的核心决策。输入是当前残余矩阵 a（含虚拟补齐容量）和原生
// hungarian 给出的 matching；输出该阶段最终采用的 matching。
//
// 处理顺序：读取原生匹配 -> R 重排邻居 -> 求阈值 q -> 构造式候选 ->
//           L 交替环修补 -> 统计。各步骤都是“可选开关”，关闭时保持原语义。
Decision Planner::choose(const std::vector<unsigned>& a,const hungarian_info_t& info){
    unsigned n=top.n,g=top.g;if(a.size()!=n*n||info.matching.size()!=2*n||info.row_to_col.size()!=n)
        throw std::invalid_argument("matching metadata dimensions");
    Decision result;result.matching.resize(n);std::vector<std::vector<unsigned>> neighbors(n);
    for(unsigned s=0;s<n;++s){result.matching[s]=info.matching[s]-n;
        for(unsigned d:info.row_to_col[s]){if(d<n||d>=2*n)throw std::invalid_argument("neighbor range");neighbors[s].push_back(d-n);}}
    auto original=result.matching;unsigned original_weight=coefficient(a,n,original);
    result.native_weight=original_weight;
    // R：按残余资源压力从大到小重排每行的候选目的。后续 all-feasible
    // 搜索（R-only、阈值检查）会优先走压力大的边；关闭时保留原邻居顺序。
    std::vector<double> priorities;
    if(options.residual){priorities=resources->priorities(remaining);
        for(unsigned s=0;s<n;++s)std::stable_sort(neighbors[s].begin(),neighbors[s].end(),[&](unsigned x,unsigned y){return priorities[s*n+x]>priorities[s*n+y];});}
    auto feasible=[&](unsigned q,std::vector<int>& trial){++counts.matching_calls;return find_matching(a,neighbors,q,trial);};
    // 精确 Max-Min：在全部非零 a 值上二分，找仍存在 perfect matching 的最大 q*。
    // 这是“当前残余图上的精确最大公共系数”，不是全局最优调度。
    if(options.exact){
        std::vector<unsigned> values=a;values.erase(std::remove(values.begin(),values.end(),0),values.end());
        std::sort(values.begin(),values.end());values.erase(std::unique(values.begin(),values.end()),values.end());
        int lo=0,hi=int(values.size())-1;
        while(lo<=hi){int mid=(lo+hi)/2;std::vector<int> trial;
            if(feasible(values[mid],trial)){result.matching=std::move(trial);result.threshold=values[mid];lo=mid+1;}else hi=mid-1;}
        result.exact_weight=result.threshold;
        // Q75/Q50：把阈值放宽到 ceil(q*·分子/分母)，但不低于原生系数；
        // 放宽后必须重新验证仍存在 perfect matching。
        if(options.quota_numerator<options.quota_denominator){
            result.threshold=std::max(original_weight,unsigned((uint64_t(result.exact_weight)*options.quota_numerator+options.quota_denominator-1)/options.quota_denominator));
            if(!feasible(result.threshold,result.matching))throw std::runtime_error("relaxed exact threshold lost feasibility");
        }
    }else if(options.granularity){
        // G：只在 2 的幂阈值上二分，减少搜索次数（最多 6 次探测）；
        // 最后取可行幂次与原生系数的较大者，避免比原生更差。
        unsigned maximum=*std::max_element(a.begin(),a.end());std::vector<unsigned> powers;
        for(uint64_t q=1;q<=maximum;q*=2)powers.push_back(unsigned(q));
        int lo=0,hi=int(powers.size())-1;unsigned q2=1;
        while(lo<=hi){int mid=(lo+hi)/2;std::vector<int> trial;++result.probes;++counts.threshold_probes;
            if(feasible(powers[mid],trial)){q2=powers[mid];lo=mid+1;}else hi=mid-1;}
        result.threshold=std::max(q2,original_weight);
        if(!feasible(result.threshold,result.matching))throw std::runtime_error("known feasible coarse threshold lost");
        if(result.probes>6)throw std::runtime_error("G probe budget");
    }else if(options.residual){
        // R-only（F001）：不抬阈值，只按压力顺序重新找一个 q=1 的匹配。
        if(!feasible(1,result.matching))throw std::runtime_error("positive residual lost perfect matching");
    }
    result.seed_weight=coefficient(a,n,result.matching);
    // 构造式匹配（A1/A2/SPM）：不沿用原生匹配，而是按行顺序重新构造一个
    // 支持 q 阈值的 perfect matching，再用“阶段时间/真实字节”评分；
    // 只有严格优于当前候选才接受。A2 的第二次构造把行按 DC 轮转重排。
    if(options.constructive_budget){
        auto score=[&](const std::vector<int>& m){auto value=resources->score(m,coefficient(a,n,m),remaining);
            return value.bytes?value.us/double(value.bytes):std::numeric_limits<double>::infinity();};
        std::vector<double> row_pressure;
        if(options.row_order>=2)row_pressure=resources->row_priorities(a,result.threshold,remaining);
        double best=score(result.matching);auto order=constructive_order(a,n,result.threshold,row_pressure,options.row_order);
#ifdef FAST_ORDER_SCAN
        result.row_pressure=row_pressure;
#endif
        for(unsigned k=0;k<options.constructive_budget;++k){
            // 第二次及以后：按 DC 分组后轮转取行，改变资源竞争的组合；
            // 这不是随机扰动，顺序确定且可复现。
            if(k){std::map<int,std::vector<unsigned>> groups;for(unsigned s=0;s<n;++s)groups[top.dc[s]].push_back(s);
                order.clear();for(unsigned pos=0;order.size()<n;++pos)for(const auto& item:groups)if(pos<item.second.size())order.push_back(item.second[pos]);}
#ifdef FAST_ORDER_SCAN
            result.row_order=order;
#endif
            std::vector<int> trial;++counts.construction_trials;++result.constructions;++counts.matching_calls;
            // SPM 用静态边效用做最大权匹配；A1/A2 用资源感知的增广构造。
            if(options.static_path)trial=maximum_weight_matching(a,n,result.threshold,resources->individual_utilities(result.threshold,remaining));
            else if(!resources->balanced_matching(a,neighbors,result.threshold,remaining,order,trial,options.optimized))
                throw std::runtime_error("constructive matching lost feasible threshold");
            // 构造不能以降低本阶段系数为代价；否则“省资源”只是把阶段切得更碎。
            if(coefficient(a,n,trial)<result.seed_weight)throw std::runtime_error("constructive coefficient regression");
            double value=score(trial);if(value<best-1e-18){best=value;result.matching=std::move(trial);++counts.construction_accepted;}
        }
    }
    // L：在不低于当前阈值/种子的前提下，尝试把一条未匹配的真实边强制
    // 加入 matching；force_edge 会用交替环保持完美匹配。候选按真实字节
    // （或有 R 时的残余压力）排序，预算默认 2（-4 为 4）。
    if(options.link){
        std::vector<unsigned> mask=a;for(auto& v:mask)if(v<result.threshold)v=0;
        struct Edge {unsigned s,d;uint64_t bytes;double priority;};std::vector<Edge> edges;
        for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(mask[s*n+d]&&s!=d&&result.matching[s]!=int(d)){
            uint64_t real=0;for(unsigned u=0;u<g;++u)real+=remaining[(s*n+d)*g+u];
            if(real)edges.push_back({s,d,real,options.residual?priorities[s*n+d]:double(real)});
        }
        auto before=[](const Edge& x,const Edge& y){return x.priority!=y.priority?x.priority>y.priority:
            x.s!=y.s?x.s<y.s:x.d<y.d;};
        unsigned budget=std::min<size_t>(options.cycle_budget,edges.size());std::partial_sort(edges.begin(),edges.begin()+budget,edges.end(),before);
        auto score=[&](const std::vector<int>& m){unsigned w=coefficient(a,n,m);auto value=resources->score(m,w,remaining);
            if(!value.bytes)return std::numeric_limits<double>::infinity();
            return options.tail_score?value.us+resources->remaining_bound(m,w,remaining):value.us/double(value.bytes);};
        auto seed=result.matching;double best=score(seed);
        // 每个候选都从同一个 seed 出发，最多只做一次强制边修改；
        // 这样“预算 k”表示检查 k 个候选，而不是连续叠加 k 次修补。
        for(unsigned k=0;k<budget;++k){auto trial=seed;++counts.cycle_trials;
            if(!native_topology::force_edge(mask,n,trial,edges[k].s,edges[k].d)){++counts.infeasible_cycles;continue;}
            if(coefficient(a,n,trial)<result.seed_weight){++counts.lower_coefficient_rejections;continue;}
            double value=score(trial);if(value<best-1e-18){best=value;result.matching=std::move(trial);}
            else ++counts.nonimproving_rejections;
        }
    }
    result.weight=coefficient(a,n,result.matching);
    if(result.matching!=original)++counts.changed_matches;
    return result;
}
// 按本阶段采用 matching 和系数 w 扣减真实剩余；每条边最多扣到 0。
// 这里不检查 a 中虚拟容量，只保证真实需求不会被多扣。
void Planner::consume(const std::vector<int>& m,unsigned w){
    for(unsigned s=0;s<top.n;++s)for(unsigned u=0;u<top.g;++u){auto& v=remaining[(s*top.n+m[s])*top.g+u];v-=std::min<uint64_t>(v,w);}
}
// 统计仍未搬运的真实跨机需求；分解结束时必须为 0。
uint64_t Planner::remaining_inter_bytes()const{
    uint64_t sum=0;for(unsigned s=0;s<top.n;++s)for(unsigned d=0;d<top.n;++d)if(s!=d)
        for(unsigned u=0;u<top.g;++u)sum+=remaining[(s*top.n+d)*top.g+u];return sum;
}
// 把 Planner 挂到 FastAll2All 的分解循环上。
// 注意：这是“一次分解”的规划器，consume 会修改 remaining，重复调用会抛异常。
void Planner::decompose_into(FastAll2All& app){
    if(counts.decomposition_runs)throw std::logic_error("planner is single-use");++counts.decomposition_runs;
    app.to_scaled_doubly_stochastic_matrix();
    // F000（所有策略开关关闭）：完全走原生 decompose，只是同步扣减真实剩余，
    // 以便后面用 remaining_inter_bytes 检查守恒。
    if(!options.granularity&&!options.link&&!options.residual&&!options.exact){
        app.decompose();for(const auto& p:app.p_sets){std::vector<int> m(top.n);for(unsigned s=0;s<top.n;++s)m[s]=p.mp.at(s);consume(m,p.get_freq());}
        counts.stages=app.p_sets.size();
    }else app.decompose_with([&](Matrix& residual,hungarian_info_t& info){
        // 回调发生在 hungarian() 之后、update_permutation_sets() 之前。
        // 我们改写 info.matching，FastAll2All 随后按新匹配扣减 SDS_mat 并记录 p_sets。
        // 真实剩余的扣减由 consume 同步完成；两边必须使用同一个系数。
        if(++counts.stages>top.n*top.n)throw std::runtime_error("finite support bound");
        std::vector<unsigned> a(top.n*top.n);for(unsigned s=0;s<top.n;++s)for(unsigned d=0;d<top.n;++d)a[s*top.n+d]=residual.get(s,d);
        auto decision=choose(a,info);for(unsigned s=0;s<top.n;++s){int d=decision.matching[s];info.matching[s]=top.n+d;info.matching[top.n+d]=s;}
        consume(decision.matching,decision.weight);counts.decisions.push_back(std::move(decision));
    });
    // 若仍有真实需求，说明策略/扣减不一致或遗漏；宁可报错也不输出不完整结果。
    if(remaining_inter_bytes())throw std::runtime_error("decomposition left true demand");
}
}
