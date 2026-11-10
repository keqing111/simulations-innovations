// ============================================================================
// bottleneck/ 实现细节。
//
// CachedScorer 把 Topology 的字符串资源名一次性映射成整数 id，score()
// 的核心循环与 Topology::evaluate 数值等价，但避免了每轮 map 查找：
//   delay = max(path alpha)，serial = max(load[id]/bw[id])，
//   返回 total=delay+serial（无真实流量时 0）。
//
// CachedPlanner 的分工：
//   - reference 或非 link-cost：完全委托 topology::Planner，保证基准不变；
//   - cached/bounded：自己实现 adjust，使用 CachedScorer。
// ============================================================================
#include "cached.h"
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <set>
#include <stdexcept>
namespace native_topology {
// 进程级默认变体；环境变量 FAST_BOTTLENECK_VARIANT 会在构造时覆盖它。
static std::string selected_variant="reference";
void set_variant(const std::string& v){
  if(v!="reference"&&v!="cached"&&v!="bounded")throw std::invalid_argument("invalid planner variant");
  selected_variant=v;
}
// 构造时给每个资源名分配稳定 id；路径只保存 id 序列。
// 注意 bandwidth 的定义顺序来自 map 字典序，但 ids 与之一致，故无歧义。
CachedScorer::CachedScorer(const Topology& t):n(t.n),g(t.g),routes(n*n),alpha(n*n) {
  std::map<std::string,unsigned> ids;
  for(const auto& item:t.bandwidths){ids[item.first]=bandwidth.size();bandwidth.push_back(item.second);}
  loads.resize(bandwidth.size());
  for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d){alpha[s*n+d]=t.path_alpha(s,d);
    for(const auto& key:t.path(s,d))routes[s*n+d].push_back(ids.at(key));}
}
// 对一个候选置换打分。只统计真实剩余（remaining）而非 server 级补齐容量；
// matching 必须双射，否则抛 invalid_argument。
CostSummary CachedScorer::score(const std::vector<int>& matching,unsigned w,const std::vector<uint64_t>& remaining){
  if(matching.size()!=n||remaining.size()!=n*n*g)throw std::invalid_argument("score dimensions");
  std::vector<bool> seen(n);for(int d:matching){if(d<0||d>=int(n)||seen[d])throw std::invalid_argument("score requires permutation");seen[d]=true;}
  std::fill(loads.begin(),loads.end(),0);uint64_t total=0;double delay=0,serial=0;
  for(unsigned s=0;s<n;++s){unsigned d=matching[s];if(s==d)continue;uint64_t bytes=0;
    // 本阶段每块 GPU 最多搬 w；逐 GPU 截断后汇总为真实字节。
    for(unsigned i=0;i<g;++i)bytes+=std::min<uint64_t>(w,remaining[(s*n+d)*g+i]);
    if(!bytes)continue;total+=bytes;delay=std::max(delay,alpha[s*n+d]);
    for(unsigned id:routes[s*n+d])loads[id]+=bytes;
  }
  // 瓶颈资源决定串行时间；0.008 把 bytes/Gbps 换算成 us。
  for(unsigned id=0;id<loads.size();++id)serial=std::max(serial,.008*double(loads[id])/bandwidth[id]);
  return {total?delay+serial:0,total};
}
// 变体选择优先级：环境变量 > set_variant 默认值 > "reference"。
CachedPlanner::CachedPlanner(const Topology& t,std::vector<uint64_t> rows,std::string mode):top(t),remaining(std::move(rows)),variant(std::getenv("FAST_BOTTLENECK_VARIANT")?std::getenv("FAST_BOTTLENECK_VARIANT"):selected_variant){
  if(variant!="reference"&&variant!="cached"&&variant!="bounded")throw std::invalid_argument("invalid planner variant");
  if(remaining.size()!=top.n*top.n*top.g)throw std::invalid_argument("planner row dimensions");
  // 只有 "cached"/"bounded" 且 mode 为 link-cost 时才走快速路径；
  // 其它组合没有实现等价版本，直接委托 reference。
  if(variant=="reference"||mode!="link-cost")delegate.reset(new Planner(top,remaining,mode));
  else scorer.reset(new CachedScorer(top));
}
uint64_t CachedPlanner::remaining_inter_bytes()const{
  if(delegate)return delegate->remaining_inter_bytes();
  uint64_t sum=0;for(unsigned s=0;s<top.n;++s)for(unsigned d=0;d<top.n;++d)if(s!=d)
    for(unsigned i=0;i<top.g;++i)sum+=remaining[(s*top.n+d)*top.g+i];return sum;
}
// cached/bounded 的每轮调整。与 topology::Planner::adjust 的 link-cost 分支
// 结构相同，差异只在打分器和候选预算：
//   cached : 预算 n，稳定排序所有候选边；
//   bounded: 预算 2，partial_sort，且拒绝系数变低的候选。
void CachedPlanner::adjust(Matrix& residual,hungarian_info_t& info){
  unsigned n=top.n,g=top.g;std::vector<unsigned> a(n*n);std::vector<int> original(n),m(n);
  for(unsigned s=0;s<n;++s){m[s]=original[s]=info.matching[s]-n;for(unsigned d=0;d<n;++d)a[s*n+d]=residual.get(s,d);}
  auto weight=[&](const std::vector<int>& candidate){unsigned w=UINT32_MAX;for(unsigned s=0;s<n;++s)w=std::min(w,a[s*n+candidate[s]]);return w;};
  auto score=[&](const std::vector<int>& candidate){auto v=scorer->score(candidate,weight(candidate),remaining);
    return v.bytes?v.us/double(v.bytes):std::numeric_limits<double>::infinity();};
  // 候选边：非匹配、非本地、且有真实剩余；按真实字节降序。
  using Edge=std::pair<uint64_t,std::pair<unsigned,unsigned>>;std::vector<Edge> edges;
  for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(a[s*n+d]&&s!=d&&m[s]!=int(d)){
    uint64_t real=0;for(unsigned i=0;i<g;++i)real+=remaining[(s*n+d)*g+i];if(real)edges.push_back({real,{s,d}});
  }
  auto before=[](const Edge& x,const Edge& y){return x.first>y.first;};
  unsigned budget=std::min<size_t>(variant=="bounded"?2:n,edges.size());
  if(variant=="bounded"){
    // Preserve stable row/column order on equal payload; no full edge sort needed.
    // 只排出前 budget 个；平局按 (src,dst) 字典序，保证结果可复现。
    auto stable=[](const Edge& x,const Edge& y){return x.first!=y.first?x.first>y.first:x.second<y.second;};
    std::partial_sort(edges.begin(),edges.begin()+budget,edges.end(),stable);
  }else std::stable_sort(edges.begin(),edges.end(),before);
  double best=score(m);unsigned original_weight=weight(m);
  for(unsigned k=0;k<budget;++k){auto trial=original;++cycle_trials;
    if(!force_edge(a,n,trial,edges[k].second.first,edges[k].second.second))continue;
    // bounded 的额外保护：换匹配后公共系数不能变小，否则会拉长阶段数。
    if(variant=="bounded"&&weight(trial)<original_weight)continue;
    double value=score(trial);if(value<best-1e-18){best=value;m=std::move(trial);}
  }
  // 与 topology::Planner 一样写回 info，并扣减逐 GPU 真实剩余。
  if(m!=original)++changed_matches;unsigned w=weight(m);if(!w)throw std::runtime_error("zero coefficient");
  for(unsigned s=0;s<n;++s){info.matching[s]=n+m[s];info.matching[n+m[s]]=s;
    for(unsigned i=0;i<g;++i){auto& v=remaining[(s*n+m[s])*g+i];v-=std::min<uint64_t>(w,v);}}
}
std::vector<PermutationSet> CachedPlanner::decompose(Matrix& service){
  // reference 路径：直接复用拓扑 Planner，并把审计计数同步出来。
  if(delegate){auto stages=delegate->decompose(service);cycle_trials=delegate->cycle_trials;changed_matches=delegate->changed_matches;repair_matches=delegate->repair_matches;return stages;}
  // cached/bounded 路径：自定义 adjust 插入原生分解循环。
  FastAll2All app(&service,top.g,ETHER400,H100,1e-6);app.to_scaled_doubly_stochastic_matrix();unsigned calls=0;
  app.decompose_with([&](Matrix& r,hungarian_info_t& info){if(++calls>top.n*top.n)throw std::runtime_error("edge zeroing bound");adjust(r,info);});
  if(remaining_inter_bytes())throw std::runtime_error("planner left real payload");return app.p_sets;
}
}
