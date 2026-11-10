// ============================================================================
// 局部匹配修补与拓扑规划。
//
// 核心操作 force_edge：在保持“完美匹配”的前提下，把一条指定边塞进去。
// 算法：设 col 当前由 start 行占用。在“行 -> 其匹配列”的交替图上从 start
// 做 BFS，边为所有 residual>0 的支持边；找到通向 row 的路径后沿路径旋转
// 匹配，最后令 row->col。若 col 本来就空闲则直接改。
//
// adjust() 每轮被调用一次，流程固定为：
//   weight(candidate)  = 该置换在当前残余矩阵上的公共系数；
//   payload(candidate) = 用逐 GPU 真实剩余截断后的 server 对流量；
//   score              = 拓扑总时间 / 真实字节（越小越好）。
// 不同 mode 只会改变候选匹配的来源，最终都由 Planner 统一扣减 remaining。
// ============================================================================
#include "matching.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <queue>
#include <stdexcept>

namespace native_topology {
bool force_edge(const std::vector<unsigned>& a,unsigned n,std::vector<int>& m,unsigned row,unsigned col) {
  if(a.size()!=n*n||m.size()!=n||row>=n||col>=n)throw std::invalid_argument("cycle dimensions");
  if(!a[row*n+col])return false;
  if(m[row]==int(col))return true;
  // 当前匹配必须是一个合法的完美匹配；inverse[col] 给出占用该列的行。
  std::vector<int> inverse(n,-1);
  for(unsigned i=0;i<n;++i) {
    if(m[i]<0||m[i]>=int(n)||inverse[m[i]]>=0||!a[i*n+m[i]])throw std::invalid_argument("cycle requires perfect supported matching");
    inverse[m[i]]=i;
  }
  // 交替图 BFS：从“被 col 占用的行”出发，只走残余容量 >0 的边。
  // parent 记录每个行顶点由哪一行扩展而来，用于回溯旋转。
  int start=inverse[col];std::vector<int> parent(n,-1);std::queue<int> pending;
  pending.push(start);parent[start]=start;
  while(!pending.empty() && parent[row]<0) {
    int current=pending.front();pending.pop();
    for(unsigned d=0;d<n;++d)if(a[current*n+d]) {
      int next=inverse[d];if(parent[next]>=0)continue;
      parent[next]=current;pending.push(next);
    }
  }
  if(parent[row]<0)return false; // row 与 start 不在同一交替分量，无法交换
  auto old=m;int at=row;
  // 沿 BFS 树从 row 回到 start：每行继承路径上下一行的旧列，形成旋转。
  while(at!=start){int prev=parent[at];m[prev]=old[at];at=prev;}
  m[row]=col;return true;
}

// 构造时保存逐 GPU 真实剩余和模式；remaining 的索引是 [(s*n+d)*g+u]。
Planner::Planner(const Topology& t,std::vector<uint64_t> rows,std::string m):top(t),remaining(std::move(rows)),mode(std::move(m)) {
  if(remaining.size()!=top.n*top.n*top.g)throw std::invalid_argument("planner row dimensions");
  if(mode!="wan-repair"&&mode!="link-cost"&&mode!="link-cost-wan"&&mode!="maxmin")throw std::invalid_argument("planner mode");
}
// 只统计 s!=d 的真实剩余；本地流不属于 server 间传输，不计入。
uint64_t Planner::remaining_inter_bytes()const {
  uint64_t total=0;for(unsigned s=0;s<top.n;++s)for(unsigned d=0;d<top.n;++d)if(s!=d)
    for(unsigned i=0;i<top.g;++i)total+=remaining[(s*top.n+d)*top.g+i];return total;
}

// 在给定支持图（每行允许的列列表）上找完美匹配；失败返回 false。
// 这是 maxmin 模式内部用的 Kuhn 匹配，不修改残余矩阵。
static bool find_matching(const std::vector<std::vector<int>>& support,std::vector<int>& m) {
  int n=support.size();m.assign(n,-1);std::vector<int> inverse(n,-1),visited(n);
  std::function<bool(int)> dfs=[&](int s){for(int d:support[s])if(!visited[d]) {
    visited[d]=1;if(inverse[d]<0||dfs(inverse[d])){m[s]=d;inverse[d]=s;return true;}
  }return false;};
  for(int s=0;s<n;++s){std::fill(visited.begin(),visited.end(),0);if(!dfs(s))return false;}return true;
}

void Planner::adjust(Matrix& residual,hungarian_info_t& info) {
  unsigned n=top.n,g=top.g;std::vector<unsigned> a(n*n);std::vector<int> original(n),m(n);
  // 复制本轮残余矩阵与原生匹配；m 是本函数唯一会修改的候选置换。
  for(unsigned s=0;s<n;++s){m[s]=original[s]=info.matching[s]-n;for(unsigned d=0;d<n;++d)a[s*n+d]=residual.get(s,d);}
  // 候选置换的公共系数 = 所有行上残余容量的最小值。
  auto weight=[&](const std::vector<int>& candidate){unsigned w=UINT32_MAX;for(unsigned s=0;s<n;++s)w=std::min(w,a[s*n+candidate[s]]);return w;};
  // 候选置换在本系数下实际能搬的真实字节：逐 GPU 用 w 截断 remaining。
  // 虚拟补齐只存在于 a 中，不会在这里被计入，因此评分不会“搬空气”。
  auto payload=[&](const std::vector<int>& candidate,unsigned w) {
    std::vector<Transfer> transfers;
    for(unsigned s=0;s<n;++s){uint64_t bytes=0;for(unsigned i=0;i<g;++i)bytes+=std::min<uint64_t>(w,remaining[(s*n+candidate[s])*g+i]);transfers.push_back({s,unsigned(candidate[s]),bytes});}
    return transfers;
  };
  // 目标函数：拓扑总时间 / 真实字节。没有真实流量时返回无穷大，
  // 这样纯虚拟阶段不会被误判为“更便宜”。
  auto score=[&](const std::vector<int>& candidate){auto flows=payload(candidate,weight(candidate));uint64_t real=0;
    for(const auto& f:flows)if(f.src!=f.dst)real+=f.bytes;
    return real?top.evaluate(flows).total_us/double(real):std::numeric_limits<double>::infinity();};
  if(mode=="maxmin") {
    // 精确 Max-Min：候选阈值只取当前残余矩阵中出现过的非零值。
    // 因为“是否存在系数 >= q 的完美匹配”关于 q 单调，可以二分；
    // 每次二分用支持图 a>=q 重建一次 Kuhn 匹配。最终 m 是最大可行 q 的匹配。
    std::vector<unsigned> values=a;values.erase(std::remove(values.begin(),values.end(),0),values.end());
    std::sort(values.begin(),values.end());values.erase(std::unique(values.begin(),values.end()),values.end());
    int lo=0,hi=int(values.size())-1;
    while(lo<=hi){int mid=(lo+hi)/2;std::vector<std::vector<int>> support(n);
      // row_to_col 存的是列全局编号 d+n，这里转换回 0..n-1。
      for(unsigned s=0;s<n;++s)for(unsigned d:info.row_to_col[s])if(a[s*n+d-n]>=values[mid])support[s].push_back(d-n);
      std::vector<int> trial;if(find_matching(support,trial)){m=trial;lo=mid+1;}else hi=mid-1;
    }
  }
  if(mode=="link-cost"||mode=="link-cost-wan") {
    // link-cost：把“当前真实剩余最多”的 n 条非匹配边依次强制塞入，
    // 每次用完整拓扑模型打分；只有严格变好才接受。base 固定为调整前匹配，
    // 避免多个候选互相影响导致结果依赖顺序。
    std::vector<std::pair<uint64_t,std::pair<unsigned,unsigned>>> edges;
    for(unsigned s=0;s<n;++s)for(unsigned d=0;d<n;++d)if(a[s*n+d]&&s!=d&&m[s]!=int(d)){
      uint64_t real=0;for(unsigned i=0;i<g;++i)real+=remaining[(s*n+d)*g+i];
      if(real)edges.push_back({real,{s,d}});
    }
    using Edge=std::pair<uint64_t,std::pair<unsigned,unsigned>>;
    std::stable_sort(edges.begin(),edges.end(),[](const Edge& x,const Edge& y){return x.first>y.first;});
    double best=score(m);auto base=m;
    for(unsigned k=0;k<std::min<size_t>(n,edges.size());++k){auto trial=base;++cycle_trials;
      if(force_edge(a,n,trial,edges[k].second.first,edges[k].second.second)){
        double cost=score(trial);if(cost<best-1e-18){best=cost;m=trial;}
      }
    }
  }
  if(mode=="wan-repair"||mode=="link-cost-wan") {
    // WAN 修复：当前匹配如果完全没有覆盖任何“有真实需求的跨 DC 流”，
    // 就强行找一条这样的边塞进去。它不追求全局最优，只保证跨 DC 流量
    // 至少能与本地流量出现在不同阶段，避免被本地流掩盖。
    auto real_wan=[&](unsigned s,unsigned d){if(top.dc[s]==top.dc[d])return false;
      for(unsigned i=0;i<g;++i)if(remaining[(s*n+d)*g+i])return true;return false;};
    bool covered=false;for(unsigned s=0;s<n;++s)covered=covered||real_wan(s,m[s]);
    if(!covered)for(unsigned s=0;s<n&&!covered;++s)for(unsigned d=0;d<n&&!covered;++d)if(a[s*n+d]&&real_wan(s,d)) {
      auto trial=m;++cycle_trials;if(force_edge(a,n,trial,s,d)){m=trial;covered=true;++repair_matches;}
    }
  }
  // 把最终匹配写回 hungarian_info，并按公共系数扣减逐 GPU 真实剩余。
  // FastAll2All 之后还会用同一个系数扣减 server 级残余矩阵 a。
  if(m!=original)++changed_matches;
  unsigned w=weight(m);if(!w)throw std::runtime_error("zero coefficient");
  for(unsigned s=0;s<n;++s){info.matching[s]=n+m[s];info.matching[n+m[s]]=s;
    for(unsigned i=0;i<g;++i){auto& v=remaining[(s*n+m[s])*g+i];v-=std::min<uint64_t>(w,v);}}
}

// 与 scan 的入口一致：先双随机化，再用 decompose_with 在每轮插入 adjust。
// calls 上限 n*n 是防御性断言：每个阶段至少消耗一个矩阵单元，Birkhoff 的
// 阶段数不会超过非零边数；超过说明残余矩阵没有正确扣减（死循环保护）。
std::vector<PermutationSet> Planner::decompose(Matrix& service) {
  FastAll2All app(&service,top.g,ETHER400,H100,1e-6);app.to_scaled_doubly_stochastic_matrix();
  unsigned calls=0;app.decompose_with([&](Matrix& r,hungarian_info_t& info){if(++calls>top.n*top.n)throw std::runtime_error("edge zeroing bound");adjust(r,info);});
  if(remaining_inter_bytes())throw std::runtime_error("planner left real payload");return app.p_sets;
}
}
