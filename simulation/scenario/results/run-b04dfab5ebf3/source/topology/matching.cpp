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
  std::vector<int> inverse(n,-1);
  for(unsigned i=0;i<n;++i) {
    if(m[i]<0||m[i]>=int(n)||inverse[m[i]]>=0||!a[i*n+m[i]])throw std::invalid_argument("cycle requires perfect supported matching");
    inverse[m[i]]=i;
  }
  int start=inverse[col];std::vector<int> parent(n,-1);std::queue<int> pending;
  pending.push(start);parent[start]=start;
  while(!pending.empty() && parent[row]<0) {
    int current=pending.front();pending.pop();
    for(unsigned d=0;d<n;++d)if(a[current*n+d]) {
      int next=inverse[d];if(parent[next]>=0)continue;
      parent[next]=current;pending.push(next);
    }
  }
  if(parent[row]<0)return false;
  auto old=m;int at=row;
  while(at!=start){int prev=parent[at];m[prev]=old[at];at=prev;}
  m[row]=col;return true;
}

Planner::Planner(const Topology& t,std::vector<uint64_t> rows,std::string m):top(t),remaining(std::move(rows)),mode(std::move(m)) {
  if(remaining.size()!=top.n*top.n*top.g)throw std::invalid_argument("planner row dimensions");
  if(mode!="wan-repair"&&mode!="link-cost"&&mode!="link-cost-wan"&&mode!="maxmin")throw std::invalid_argument("planner mode");
}
uint64_t Planner::remaining_inter_bytes()const {
  uint64_t total=0;for(unsigned s=0;s<top.n;++s)for(unsigned d=0;d<top.n;++d)if(s!=d)
    for(unsigned i=0;i<top.g;++i)total+=remaining[(s*top.n+d)*top.g+i];return total;
}

static bool find_matching(const std::vector<std::vector<int>>& support,std::vector<int>& m) {
  int n=support.size();m.assign(n,-1);std::vector<int> inverse(n,-1),visited(n);
  std::function<bool(int)> dfs=[&](int s){for(int d:support[s])if(!visited[d]) {
    visited[d]=1;if(inverse[d]<0||dfs(inverse[d])){m[s]=d;inverse[d]=s;return true;}
  }return false;};
  for(int s=0;s<n;++s){std::fill(visited.begin(),visited.end(),0);if(!dfs(s))return false;}return true;
}

void Planner::adjust(Matrix& residual,hungarian_info_t& info) {
  unsigned n=top.n,g=top.g;std::vector<unsigned> a(n*n);std::vector<int> original(n),m(n);
  for(unsigned s=0;s<n;++s){m[s]=original[s]=info.matching[s]-n;for(unsigned d=0;d<n;++d)a[s*n+d]=residual.get(s,d);}
  auto weight=[&](const std::vector<int>& candidate){unsigned w=UINT32_MAX;for(unsigned s=0;s<n;++s)w=std::min(w,a[s*n+candidate[s]]);return w;};
  auto payload=[&](const std::vector<int>& candidate,unsigned w) {
    std::vector<Transfer> transfers;
    for(unsigned s=0;s<n;++s){uint64_t bytes=0;for(unsigned i=0;i<g;++i)bytes+=std::min<uint64_t>(w,remaining[(s*n+candidate[s])*g+i]);transfers.push_back({s,unsigned(candidate[s]),bytes});}
    return transfers;
  };
  auto score=[&](const std::vector<int>& candidate){auto flows=payload(candidate,weight(candidate));uint64_t real=0;
    for(const auto& f:flows)if(f.src!=f.dst)real+=f.bytes;
    return real?top.evaluate(flows).total_us/double(real):std::numeric_limits<double>::infinity();};
  if(mode=="maxmin") {
    std::vector<unsigned> values=a;values.erase(std::remove(values.begin(),values.end(),0),values.end());
    std::sort(values.begin(),values.end());values.erase(std::unique(values.begin(),values.end()),values.end());
    int lo=0,hi=int(values.size())-1;
    while(lo<=hi){int mid=(lo+hi)/2;std::vector<std::vector<int>> support(n);
      for(unsigned s=0;s<n;++s)for(unsigned d:info.row_to_col[s])if(a[s*n+d-n]>=values[mid])support[s].push_back(d-n);
      std::vector<int> trial;if(find_matching(support,trial)){m=trial;lo=mid+1;}else hi=mid-1;
    }
  }
  if(mode=="link-cost"||mode=="link-cost-wan") {
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
    auto real_wan=[&](unsigned s,unsigned d){if(top.dc[s]==top.dc[d])return false;
      for(unsigned i=0;i<g;++i)if(remaining[(s*n+d)*g+i])return true;return false;};
    bool covered=false;for(unsigned s=0;s<n;++s)covered=covered||real_wan(s,m[s]);
    if(!covered)for(unsigned s=0;s<n&&!covered;++s)for(unsigned d=0;d<n&&!covered;++d)if(a[s*n+d]&&real_wan(s,d)) {
      auto trial=m;++cycle_trials;if(force_edge(a,n,trial,s,d)){m=trial;covered=true;++repair_matches;}
    }
  }
  if(m!=original)++changed_matches;
  unsigned w=weight(m);if(!w)throw std::runtime_error("zero coefficient");
  for(unsigned s=0;s<n;++s){info.matching[s]=n+m[s];info.matching[n+m[s]]=s;
    for(unsigned i=0;i<g;++i){auto& v=remaining[(s*n+m[s])*g+i];v-=std::min<uint64_t>(w,v);}}
}

std::vector<PermutationSet> Planner::decompose(Matrix& service) {
  FastAll2All app(&service,top.g,ETHER400,H100,1e-6);app.to_scaled_doubly_stochastic_matrix();
  unsigned calls=0;app.decompose_with([&](Matrix& r,hungarian_info_t& info){if(++calls>top.n*top.n)throw std::runtime_error("edge zeroing bound");adjust(r,info);});
  if(remaining_inter_bytes())throw std::runtime_error("planner left real payload");return app.p_sets;
}
}
